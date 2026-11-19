# ruff: noqa: E501
"""A version of a law is an official text. Reading the same archived file again (a better OCR, a
re-split) is not a new version: once the new reading is in place, the earlier readings of the same
bytes are folded into it and removed.

Folding: each clause of an earlier reading is matched to the clause of the kept reading with the
same text (same text fingerprint; otherwise the same article number and similar text; otherwise the
most similar text). What pointed at the earlier clause or version is moved to the match — graph
evidence, admission items and steps, terms, law definitions, open proposals, version history, and
clause or version ids inside JSON (document narratives, briefings, reports, drafts); audit tables
are left as they were. A translation the kept clause lacks, a hand-revised or confirmed
translation, and a confirmed check of the scan are carried over. Graph records that cannot be
matched and were never approved (superseded, stale, rejected or still pending) go with the earlier
reading; anything else that cannot be matched keeps the earlier reading in place and is reported.
"""

from __future__ import annotations

import re
import unicodedata
import uuid
from difflib import SequenceMatcher

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
SKIP_JSON = {"audit_logs", "knowledge_audit_records", "llm_call_audit"}
LIVE_FK = [
    ("kg_nodes", "evidence_clause_id"),
    ("kg_edges", "evidence_clause_id"),
    ("admission_entries", "evidence_clause_id"),
    ("admission_steps", "evidence_clause_id"),
    ("glossary_terms", "evidence_clause_id"),
    ("review_points", "clause_id"),
]
NOT_APPROVED = "('superseded','stale','rejected','pending_review')"


_NORM_TAG = re.compile(r"\[\s*(?:act\s+\d+\s+of\s+(?:19|20)\d{2}[^\]]*|s\s*\d{1,4}\s*/\s*(?:19|20)\d{2}[^\]]*|\d{1,3}\s*/\s*(?:19|20)\d{2}[^\]]*)\s*\]")


def _norm(t: str) -> str:
    s = unicodedata.normalize("NFKC", t or "").lower()
    s = _NORM_TAG.sub(" ", s)
    return re.sub(r"\s+", " ", s).strip()[:30000]


def _num(n: str | None) -> str:
    m = re.search(r"\d+", n or "")
    return m.group(0) if m else (n or "").strip()


def _match(old: dict, keep: list[dict], by_hash: dict, by_num: dict) -> str | None:
    if old["text_hash"] in by_hash:
        return by_hash[old["text_hash"]]
    a = _norm(old["original_text"])
    if not a:
        return None
    for c in keep:
        if c["_n"] == a:
            return c["id"]
    probes = [p for p in (a[:60], a[len(a) // 5 : len(a) // 5 + 60], a[len(a) // 2 : len(a) // 2 + 60]) if len(p) >= 35]
    for pool, floor, cov_floor in (
        (by_num.get(_num(old["clause_number"]), []) if old["clause_number"] else [], 0.5, 0.65),
        (keep, 0.6, 0.70),
    ):
        best, score = None, 0.0
        for c in pool:
            cn = c["_n"]
            sub_hit = any(p in cn for p in probes)
            if pool is keep and len(cn) > 2 * max(len(a), 1) and not sub_hit:
                continue
            sm = SequenceMatcher(None, a, cn, autojunk=False)
            if not sub_hit and pool is keep and sm.real_quick_ratio() < floor:
                continue
            if not sub_hit and pool is not keep and sm.real_quick_ratio() < floor and len(cn) <= 2 * max(len(a), 1):
                continue
            r = sm.ratio() if len(cn) <= 3 * max(len(a), 1) else 0.0
            if len(a) >= 40 and len(cn) > len(a) and (pool is not keep or sub_hit):
                cover = sum(x.size for x in sm.get_matching_blocks()) / len(a)
                if cover >= cov_floor:
                    r = max(r, cover * 0.85)
            if r > score:
                best, score = c["id"], r
        if best is not None and score >= floor:
            return best
    return None


async def fold_into(
    session: AsyncSession,
    keep_id: uuid.UUID,
    *,
    include_all_versions: bool = False,
) -> dict:
    """Fold every other reading of the same file (or document if include_all_versions) into ``keep_id``."""
    q = lambda sql, p=None: session.execute(text(sql), p or {})  # noqa: E731
    keep = (await q("select id, document_id, content_hash, review_status from document_versions where id=:k", {"k": keep_id})).mappings().one()
    drop_sql = (
        "select id, review_status from document_versions where document_id=:d and id<>:k"
        if include_all_versions
        else "select id, review_status from document_versions where document_id=:d and content_hash=:h and id<>:k"
    )
    drops = (
        await q(
            drop_sql,
            {"d": keep["document_id"], "h": keep["content_hash"], "k": keep_id},
        )
    ).mappings().all()
    if not drops:
        return {"folded": 0}
    drop_ids = [d["id"] for d in drops]
    kc = [dict(r._mapping) for r in (await q("select id, clause_number, original_text, text_hash from clauses where version_id=:v", {"v": keep_id})).all()]
    by_hash: dict = {}
    by_num: dict = {}
    kc_by_id: dict = {}
    for c in kc:
        c["_n"] = _norm(c["original_text"])
        kc_by_id[c["id"]] = c
        by_hash.setdefault(c["text_hash"], c["id"])
        if c["clause_number"]:
            by_num.setdefault(_num(c["clause_number"]), []).append(c)
    old = [dict(r._mapping) for r in (await q("select id, clause_number, original_text, text_hash from clauses where version_id = any(:v)", {"v": drop_ids})).all()]
    old_by_id = {o["id"]: o for o in old}
    referenced: set = set()
    for t, c in LIVE_FK + [("legal_term_definitions", "clause_id")]:
        referenced |= {r[0] for r in (await q(f"select distinct x.{c} from {t} x join clauses c on c.id=x.{c} where c.version_id = any(:v)", {"v": drop_ids})).all()}
    cmap: dict = {}
    unmapped: list = []
    for o in old:
        m = _match(o, kc, by_hash, by_num)
        if m is not None:
            cmap[o["id"]] = m
        elif o["id"] in referenced:
            unmapped.append(o["id"])
    blocking = []
    for t, c in LIVE_FK:
        extra = f" and review_status not in {NOT_APPROVED}" if t.startswith("kg_") else ""
        blocking += [f"{t}:{r[0]}" for r in (await q(f"select id from {t} where {c} = any(:u){extra}", {"u": unmapped})).all()]
    if blocking:
        return {"folded": 0, "kept_back": blocking}
    for t, c in LIVE_FK + [("legal_term_definitions", "clause_id")]:
        for o, n in cmap.items():
            await q(f"update {t} set {c}=:n where {c}=:o", {"n": n, "o": o})
    for t in ("kg_edges", "kg_nodes"):
        await q(f"delete from {t} where evidence_clause_id = any(:u) and review_status in {NOT_APPROVED}", {"u": unmapped})
    for t in ("kg_nodes", "admission_entries", "admission_steps", "glossary_terms"):
        await q(f"update {t} set evidence_clause_id=null where evidence_clause_id = any(:u)", {"u": unmapped})
    await q("delete from legal_term_definitions where version_id = any(:v) and (clause_id is null or clause_id not in (select id from clauses where version_id=:k))", {"v": drop_ids, "k": keep_id})
    await q("update legal_term_definitions set version_id=:k where version_id = any(:v)", {"k": keep_id, "v": drop_ids})
    await q("delete from legal_term_definitions where clause_id in (select id from clauses where version_id = any(:v))", {"v": drop_ids})
    for o, n in cmap.items():
        oc = old_by_id.get(o)
        nc = kc_by_id.get(n)
        same = bool(
            oc
            and nc
            and (oc["text_hash"] == nc["text_hash"] or _norm(oc["original_text"]) == nc["_n"])
        )
        if not same:
            continue
        human = (await q("select id from clause_translations where clause_id=:o and (engine='human_revision' or review_status='approved') order by updated_at desc nulls last limit 1", {"o": o})).scalar()
        keep_ok = (await q("select count(*) from clause_translations where clause_id=:n and is_current and quality_check_passed", {"n": n})).scalar()
        if human:
            await q("update clause_translations set is_current=false where clause_id=:n", {"n": n})
            await q("update clause_translations set clause_id=:n, is_current=(id=:h) where clause_id=:o", {"n": n, "o": o, "h": human})
        elif not keep_ok:
            good = (await q("select id from clause_translations where clause_id=:o and is_current and quality_check_passed", {"o": o})).scalar()
            if good:
                await q("update clause_translations set is_current=false where clause_id=:n", {"n": n})
                await q("update clause_translations set clause_id=:n where id=:g", {"n": n, "g": good})
    if any(d["review_status"] == "approved" for d in drops) and keep["review_status"] != "approved":
        await q("update document_versions set review_status='approved' where id=:k", {"k": keep_id})
    ids = {str(k): str(v) for k, v in cmap.items()} | {str(v): str(keep_id) for v in drop_ids}
    for o, n in ids.items():
        await q("update knowledge_change_proposals set target_id=:n where target_id=:o", {"n": n, "o": o})
    await q("update pipeline_events set version_id=:k where version_id = any(:v)", {"k": keep_id, "v": drop_ids})
    await q("update source_fetch_observations set version_id=:k where version_id = any(:v)", {"k": keep_id, "v": drop_ids})
    await q("update source_fetch_observations set previous_version_id=:k where previous_version_id = any(:v)", {"k": keep_id, "v": drop_ids})
    for cid, ov, nv in (await q("select id, old_version_id, new_version_id from version_changes where old_version_id = any(:a) or new_version_id = any(:a)", {"a": drop_ids + [keep_id]})).all():
        o2 = ids.get(str(ov), str(ov)) if ov else None
        n2 = ids.get(str(nv), str(nv)) if nv else None
        if o2 == n2:  # 同一份原文之间的“变化”不是变化：连同影响与提醒删掉
            await q("delete from user_notifications where change_id=:c", {"c": cid})
            await q("delete from update_impacts where change_id=:c", {"c": cid})
            await q("delete from version_changes where id=:c", {"c": cid})
        else:
            await q("update version_changes set old_version_id=:o, new_version_id=:n where id=:c", {"o": o2, "n": n2, "c": cid})
    json_rows = 0
    for t, c in (await q("select table_name, column_name from information_schema.columns where table_schema='public' and data_type='jsonb'")).all():
        if t in SKIP_JSON or not (await q("select count(*) from information_schema.columns where table_name=:t and column_name='id'", {"t": t})).scalar():
            continue
        for rid, js in (await q(f"select id::text, {c}::text from {t} where {c} is not null and {c}::text ~ '[0-9a-f]{{8}}-[0-9a-f]{{4}}-'")).all():
            if set(UUID_RE.findall(js)) & ids.keys():
                await q(f"update {t} set {c}=cast(:j as jsonb) where id::text=:i", {"j": UUID_RE.sub(lambda m: ids.get(m.group(0), m.group(0)), js), "i": rid})
                json_rows += 1
    await q("delete from clause_readings where clause_id in (select id from clauses where version_id = any(:v))", {"v": drop_ids})
    await q("delete from clause_translations where clause_id in (select id from clauses where version_id = any(:v))", {"v": drop_ids})
    await q("delete from clauses where version_id = any(:v)", {"v": drop_ids})
    await q("delete from document_versions where id = any(:v)", {"v": drop_ids})
    # 并完以后扫一遍全库：旧条文被其他地方引用、又没对上的，按摘录再找一次
    from app.ingestion.ref_check import repair

    refs = await repair(session)
    return {"folded": len(drop_ids), "clauses_matched": len(cmap), "json_rows": json_rows, "references": refs}
