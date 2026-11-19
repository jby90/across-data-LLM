"""Graph data checks for the admin page: what a reviewer must look at before trusting the graph."""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

_LATEST = (
    "(select max(version_no) from document_versions x "
    "where x.document_id=v.document_id and x.state='parsed')"
)

CHECKS = {
    "no_evidence": (
        "义务、罚则类节点没有依据条款",
        """select n.id::text, n.node_type, n.name_zh, null as detail from kg_nodes n
        where n.country_code=:c and n.review_status not in ('superseded','stale')
          and n.evidence_clause_id is null
          and n.node_type not in ('COUNTRY','INSTRUMENT','DATA_CATEGORY','REGULATOR','SCENARIO')""",
    ),
    "old_version_evidence": (
        "节点的依据条款属于文件的旧版本（新版已重新切分，条文有变化）",
        f"""select n.id::text, n.node_type, n.name_zh, c.clause_number as detail from kg_nodes n
        join clauses c on c.id=n.evidence_clause_id
        join document_versions v on v.id=c.version_id
        where n.country_code=:c and n.review_status not in ('superseded','stale')
          and v.version_no < {_LATEST}""",
    ),
    "edge_to_superseded_node": (
        "关系的一端是已作废的节点",
        """select e.id::text, e.relation_type, s.name_zh, t.name_zh from kg_edges e
        join kg_nodes s on s.id=e.source_node_id join kg_nodes t on t.id=e.target_node_id
        where e.country_code=:c and e.review_status not in ('superseded','rejected','stale')
          and (s.review_status='superseded' or t.review_status='superseded')""",
    ),
    "stale_nodes": (
        "依据找不到对应新版条文、已停用的节点（需重新抽取）",
        """select n.id::text, n.node_type, n.name_zh, n.attributes->>'stale_reason' as detail
        from kg_nodes n where n.country_code=:c and n.review_status='stale'
          and coalesce(n.attributes->>'stale_reason','') not like '两次独立核对%'""",
    ),
    "duplicate_nodes": (
        "同类型同名称的节点重复",
        """select min(n.id::text), n.node_type, n.name_zh, count(*)::text from kg_nodes n
        where n.country_code=:c and n.review_status not in ('superseded','stale')
        group by n.node_type, n.name_zh having count(*)>1""",
    ),
}


_VALIDATION_TYPE_ZH = {
    "COUNTRY": "国家/地区",
    "REGULATOR": "监管机构",
    "INSTRUMENT": "法规文件",
    "OBLIGATION": "合规义务",
    "DATA_CATEGORY": "数据类型",
    "PENALTY": "违规罚则",
    "SCENARIO": "业务场景",
    "PUBLISHES": "发布法规",
    "AMENDS": "修订",
    "REPEALS": "废止",
    "GOVERNS": "管辖与适用",
    "PROHIBITS": "禁止行为",
    "REQUIRES": "规定合规义务",
    "EXEMPTS": "规定豁免",
    "SUPERVISES": "监管执法",
    "PENALIZES": "设定罚则",
    "APPLIES_TO_SCENARIO": "适用业务场景",
    "RESTRICTS_CROSS_BORDER": "限制跨境传输",
    "RESTRICTS_TRANSFER": "限制出境传输",
    "REQUIRES_ADMISSION": "要求属地准入",
    "TRANSFERS_TO": "跨境传输至",
    "INVOLVES_DATA": "涉及数据类型",
    "IMPOSES_PENALTY": "规定违规罚则",
}


async def validate_graph(session: AsyncSession, country: str, limit: int = 50) -> list[dict]:
    from app.knowledge.graph_verify import clean_verify_note

    out = []
    for key, (label, sql) in CHECKS.items():
        rows = (await session.execute(text(sql), {"c": country})).all()
        out.append(
            {
                "key": key,
                "label": label,
                "count": len(rows),
                "items": [
                    {
                        "id": r[0],
                        "type": _VALIDATION_TYPE_ZH.get(str(r[1] or "").upper(), r[1]),
                        "name": r[2],
                        "detail": clean_verify_note(r[3]) if isinstance(r[3], str) else r[3],
                    }
                    for r in rows[:limit]
                ],
            }
        )
    return out


async def retire_dangling_edges(session: AsyncSession, country: str | None = None) -> int:
    """An edge whose end node was retired cannot be shown or reviewed; retire it too (with a
    history snapshot). Edges already marked stale/superseded/rejected are left alone."""
    from app.knowledge.history import snapshot
    from app.knowledge.models import KgEdge

    ids = (
        (
            await session.execute(
                text(
                    """select e.id from kg_edges e
                join kg_nodes s on s.id=e.source_node_id join kg_nodes t on t.id=e.target_node_id
                where e.review_status not in ('superseded','rejected','stale')
                  and (s.review_status in ('superseded','stale')
                       or t.review_status in ('superseded','stale'))
                  and (cast(:c as text) is null or e.country_code=cast(:c as text))"""
                ),
                {"c": country},
            )
        )
        .scalars()
        .all()
    )
    for edge_id in ids:
        edge = await session.get(KgEdge, edge_id)
        snapshot(session, edge, "before_dangling_edge_retirement")
        edge.review_status = "stale"  # 一端已停用：关系同样停用，重抽后重建
    await session.flush()
    return len(ids)


async def _latest_counterpart(session: AsyncSession, old_id) -> object | None:
    """The clause of the document's latest parsed version that says the same as ``old_id``:
    identical text first, then the same clause number if unique, then a clearly best fuzzy match
    (>= 0.8 and 0.05 ahead of the runner-up). None when there is no safe answer."""
    import difflib

    old = (
        await session.execute(
            text(
                """select c.version_id, c.clause_number, c.text_hash, c.original_text,
                  (select v2.id from document_versions v2 where v2.document_id=v.document_id
                   and v2.state='parsed' order by v2.version_no desc limit 1)
                from clauses c join document_versions v on v.id=c.version_id where c.id=:i"""
            ),
            {"i": old_id},
        )
    ).first()
    if old is None:
        return None
    _, number, text_hash, body, latest = old
    cands = (
        await session.execute(
            text(
                "select id, clause_number, text_hash, original_text "
                "from clauses where version_id=:v"
            ),
            {"v": latest},
        )
    ).all()
    same = [c for c in cands if c[2] == text_hash]
    if same:
        return same[0][0]
    by_number = [c for c in cands if number and c[1] == number]
    if len(by_number) == 1:
        return by_number[0][0]
    scored = sorted(
        (
            (difflib.SequenceMatcher(None, body[:1500], c[3][:1500], autojunk=False).ratio(), c[0])
            for c in cands
        ),
        reverse=True,
    )
    if scored and scored[0][0] >= 0.8 and (len(scored) < 2 or scored[0][0] - scored[1][0] >= 0.05):
        return scored[0][1]
    needle = _plain((body or "")[:120])
    if len(needle) >= 20:
        sub = [c for c in cands if needle in _plain(c[3] or "")]
        if len(sub) == 1:
            return sub[0][0]
    return None


def _plain(value: str) -> str:
    """Lower-case, accents and punctuation removed, whitespace collapsed: a phrase copied from an
    OCR text still matches after the engine changed an apostrophe, hyphen or accent."""
    import re
    import unicodedata

    value = unicodedata.normalize("NFKD", value or "").lower()
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    return " ".join(re.sub(r"[^\w\s]|_", " ", value).split())


async def _clause_quoting(
    session: AsyncSession, node, cache: dict, preferred_docs: set | None = None
) -> object | None:
    """A node keeps the source phrase it was extracted from in ``name_orig``. If exactly one clause
    of the country's latest parsed versions contains that phrase (accents, punctuation and
    whitespace ignored), it is the evidence; zero or several hits give no answer."""
    quote = _plain(node.name_orig)
    if len(quote) < 12:
        return None
    if node.country_code not in cache:
        rows = (
            await session.execute(
                text(
                    """select c.id, c.original_text, v.document_id from clauses c
                    join document_versions v on v.id=c.version_id
                    join documents d on d.id=v.document_id
                    where d.country_code=:c and v.version_no=(select max(version_no)
                      from document_versions x where x.document_id=v.document_id
                      and x.state='parsed')"""
                ),
                {"c": node.country_code},
            )
        ).all()
        cache[node.country_code] = [(r[0], _plain(r[1]), r[2]) for r in rows]
    hits = [(cid, doc) for cid, body, doc in cache[node.country_code] if quote in body]
    if len(hits) > 1 and preferred_docs:
        # 同一条旧依据下的其他节点已确定在哪份文件里：只在那份文件内找
        hits = [h for h in hits if h[1] in preferred_docs]
    return hits[0][0] if len(hits) == 1 else None


async def restore_evidence_from_attributes(
    session: AsyncSession, country: str | None = None
) -> dict:
    """Nodes without the evidence column but with ``attributes.evidence_clause_ids``: attach the
    latest-version counterpart of the first listed clause that has a safe one. Otherwise the node
    is left for review, not guessed."""
    import uuid

    from app.knowledge.history import snapshot
    from app.knowledge.models import KgNode

    rows = (
        await session.execute(
            text(
                """select n.id, n.attributes->'evidence_clause_ids' from kg_nodes n
                where n.evidence_clause_id is null and n.review_status not in ('superseded','stale')
                  and jsonb_typeof(n.attributes->'evidence_clause_ids')='array'
                  and (cast(:c as text) is null or n.country_code=cast(:c as text))"""
            ),
            {"c": country},
        )
    ).all()
    fixed = skipped = 0
    cache: dict = {}
    sibling_docs: dict[str, set] = {}  # 同一条旧依据 -> 已确定的所在文件
    pending = []
    for node_id, ids in rows:
        chosen = None
        for raw in ids or []:
            try:
                chosen = await _latest_counterpart(session, uuid.UUID(str(raw)))
            except ValueError:
                continue
            if chosen:
                break
        node = await session.get(KgNode, node_id)
        if chosen is None:
            chosen = await _clause_quoting(session, node, cache)
        if chosen is None:
            pending.append((node, ids))
            continue
        doc = (
            await session.execute(
                text(
                    "select v.document_id from clauses c join document_versions v "
                    "on v.id=c.version_id where c.id=:i"
                ),
                {"i": chosen},
            )
        ).scalar_one()
        for raw in ids or []:
            sibling_docs.setdefault(str(raw), set()).add(doc)
        snapshot(session, node, "before_evidence_restore")
        node.evidence_clause_id = chosen
        fixed += 1
    for node, ids in pending:  # 第二遍：借同条旧依据下兄弟节点的所在文件消歧
        docs = set().union(*(sibling_docs.get(str(r), set()) for r in ids or []))
        chosen = await _clause_quoting(session, node, cache, docs) if docs else None
        if chosen is None:
            skipped += 1
            continue
        snapshot(session, node, "before_evidence_restore")
        node.evidence_clause_id = chosen
        fixed += 1
    await session.flush()
    return {"restored": fixed, "skipped": skipped}


async def stale_unanchored_nodes(session: AsyncSession, country: str | None = None) -> int:
    """Obligation/penalty nodes whose evidence is missing or still in an older parse, after every
    safe re-pointing has been tried: switch them off (stale) with the reason, so no reviewer or
    user sees a statement that cannot be traced to the current text. Snapshotted; reversible."""
    from app.knowledge.history import snapshot
    from app.knowledge.models import KgNode

    ids = (
        (
            await session.execute(
                text(
                    f"""select n.id from kg_nodes n
                    left join clauses c on c.id=n.evidence_clause_id
                    left join document_versions v on v.id=c.version_id
                    where n.review_status not in ('superseded','stale','rejected')
                      and (cast(:c as text) is null or n.country_code=cast(:c as text))
                      and (
                        (n.evidence_clause_id is null and n.node_type in ('OBLIGATION','PENALTY'))
                        or v.version_no < {_LATEST}
                      )"""
                ),
                {"c": country},
            )
        )
        .scalars()
        .all()
    )
    structural = ("COUNTRY", "INSTRUMENT", "REGULATOR", "DATA_CATEGORY", "SCENARIO")
    retired = 0
    for node_id in ids:
        node = await session.get(KgNode, node_id)
        if node.node_type in structural:
            # 国家、法规文件、监管机构、数据类型本来就不靠某一条条款成立：旧版条款指针去掉即可，
            # 不能因为它指着旧版就把整个节点停用（会连带停用它的所有关系）
            snapshot(session, node, "before_structural_evidence_clear")
            node.evidence_clause_id = None
            continue
        snapshot(session, node, "before_unanchored_stale")
        node.review_status = "stale"
        node.attributes = {
            **(node.attributes or {}),
            "stale_reason": "依据条款在新版文本中找不到对应条文，待重新抽取",
        }
        retired += 1
    await session.flush()
    return retired


async def relocate_edges_by_quote(session: AsyncSession, country: str | None = None) -> dict:
    """Edges marked stale because their clause was re-read: bring them back when the sentence
    they cite is still in a clause of the document's latest version.

    The evidence of an edge is its quote. If the same sentence (whitespace-insensitive) is found
    in exactly one clause of the latest parsed version of the same document, the edge is the same
    statement on a new clause row: it moves there and goes back to review. Nothing is guessed:
    no hit, or more than one, leaves the edge stale."""
    from app.knowledge.history import snapshot
    from app.knowledge.models import KgEdge

    rows = (
        await session.execute(
            text(
                f"""select e.id, e.evidence_quote, v.document_id from kg_edges e
                join clauses c on c.id=e.evidence_clause_id
                join document_versions v on v.id=c.version_id
                where e.review_status='stale' and v.version_no < {_LATEST}
                  and (cast(:c as text) is null or e.country_code=cast(:c as text))"""
            ),
            {"c": country},
        )
    ).all()
    cache: dict = {}
    moved = ambiguous = 0
    for edge_id, quote, document_id in rows:
        needle = " ".join((quote or "").split())
        if len(needle) < 12:
            continue
        if document_id not in cache:
            cache[document_id] = [
                (r[0], " ".join((r[1] or "").split()))
                for r in (
                    await session.execute(
                        text(
                            """select c.id, c.original_text from clauses c
                            join document_versions v on v.id=c.version_id
                            where v.document_id=:d and v.state='parsed'
                              and v.version_no=(select max(version_no) from document_versions x
                                                where x.document_id=:d and x.state='parsed')"""
                        ),
                        {"d": document_id},
                    )
                ).all()
            ]
        hits = [cid for cid, body in cache[document_id] if needle in body]
        if len(hits) != 1:
            ambiguous += bool(hits)
            continue
        edge = await session.get(KgEdge, edge_id)
        snapshot(session, edge, "before_edge_relocate")
        edge.evidence_clause_id = hits[0]
        edge.review_status = "pending_review"
        edge.reviewed_by = None
        edge.reviewed_at = None
        moved += 1
    await session.flush()
    return {"edges_relocated": moved, "ambiguous": ambiguous, "still_stale": len(rows) - moved}
