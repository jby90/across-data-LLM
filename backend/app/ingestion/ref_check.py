# ruff: noqa: E501
"""References to clauses that no longer exist (after versions are folded together or a law is read
again), wherever they sit inside stored JSON — reports, findings, document narratives, proposals.

``scan`` counts them. ``repair`` points each one at the clause of the same law that holds the same
text: the excerpt the reference carries (quote / snippet), found in the law's current text by its
longest runs, else the most similar clause (also with the word order of a reversed old reading put
back); without an excerpt, the same article number. Audit tables are never rewritten.
"""

from __future__ import annotations

import json
import re
import time
import unicodedata
from difflib import SequenceMatcher

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

_U = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_SKIP = {
    "audit_logs",
    "knowledge_audit_records",
    "llm_call_audit",
    "agent_workflow_configs",
    "source_fetch_observations",
    "version_changes",
}
_KEYS = ("clause_id", "evidence_clause_id")
_CACHE: dict[str, tuple[float, int]] = {}


def _norm(t: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", t or "")).strip()


def _runs(t: str) -> list[str]:
    parts = [p.strip() for p in re.split(r"[\n\r|/]+|(?<=[.。;；:：])\s", _norm(t)) if len(p.strip()) >= 12]
    return sorted(set(parts), key=len, reverse=True)[:6]


async def _columns(session: AsyncSession) -> list[tuple[str, str]]:
    rows = (await session.execute(text("select c.table_name, c.column_name from information_schema.columns c where c.table_schema='public' and c.data_type='jsonb' and exists (select 1 from information_schema.columns i where i.table_name=c.table_name and i.column_name='id')"))).all()
    return [(t, c) for t, c in rows if t not in _SKIP]


def _refs(obj, out: list):
    if isinstance(obj, dict):
        for k in _KEYS:
            v = obj.get(k)
            if isinstance(v, str) and _U.match(v):
                out.append((v, obj))
        for v in obj.values():
            _refs(v, out)
    elif isinstance(obj, list):
        for v in obj:
            _refs(v, out)


async def scan(session: AsyncSession, *, cached: bool = False) -> int:
    """How many clause references in stored JSON point at a clause that is gone."""
    if cached and (hit := _CACHE.get("n")) and time.monotonic() - hit[0] < 600:
        return hit[1]
    alive = set((await session.execute(text("select id::text from clauses"))).scalars().all())
    n = 0
    for t, c in await _columns(session):
        for (js,) in (await session.execute(text(f"select {c} from {t} where {c}::text like '%clause_id%'"))).all():
            refs: list = []
            _refs(js, refs)
            n += sum(1 for cid, _ in refs if cid not in alive)
    _CACHE["n"] = (time.monotonic(), n)
    return n


async def repair(session: AsyncSession) -> dict:
    """Point every dangling reference at its clause in the current text; returns what was done."""
    q = lambda sql, p=None: session.execute(text(sql), p or {})  # noqa: E731
    alive = set((await q("select id::text from clauses")).scalars().all())
    latest = dict((await q("select document_id::text, id::text from document_versions")).all())
    by_title = dict((await q("select title, id::text from documents")).all())
    by_url = dict((await q("select source_url, id::text from documents where source_url is not null")).all())
    cols = await _columns(session)
    known: dict[str, dict] = {}
    for t, c in cols:
        for (js,) in (await q(f"select {c} from {t} where {c}::text like '%clause_id%'")).all():
            refs: list = []
            _refs(js, refs)
            for cid, obj in refs:
                if cid not in alive:
                    k = known.setdefault(cid, {})
                    for f in ("quote_original", "quote", "snippet", "evidence_quote", "document_title", "document_id", "clause_number", "source_url"):
                        if obj.get(f) and not k.get(f):
                            k[f] = obj.get(f)
    mapping: dict[str, str | None] = {}
    for cid, info in known.items():
        doc = str(info.get("document_id") or "")
        doc = doc if doc in latest else by_title.get(info.get("document_title")) or by_url.get(info.get("source_url"))
        vid = latest.get(doc) if doc else None
        quote = info.get("quote_original") or info.get("quote") or info.get("snippet") or info.get("evidence_quote") or ""
        found = None
        for run in _runs(quote):
            rows = (await q("select c.id::text, c.original_text from clauses c where position(:r in regexp_replace(c.original_text, '\\s+', ' ', 'g')) > 0" + (" and c.version_id = cast(:v as uuid)" if vid else "") + " limit 5", {"r": run, "v": vid})).all()
            if rows:
                found = max(rows, key=lambda r: SequenceMatcher(None, _norm(quote)[:2000], _norm(r[1])[:4000], autojunk=False).ratio())[0]
                break
        if not found and vid:
            clauses = (await q("select id::text, clause_number, original_text from clauses where version_id=cast(:v as uuid)", {"v": vid})).all()
            num = re.sub(r"\D", "", str(info.get("clause_number") or ""))
            if quote:
                a = _norm(quote)[:600]
                flipped = _norm(" ".join(" ".join(reversed(line.split())) for line in quote.splitlines()))[:600]
                best, score = None, 0.0
                for rid, _, body in clauses:
                    b = _norm(body)[:6000]
                    cover = max(
                        sum(x.size for x in SequenceMatcher(None, a, b, autojunk=False).get_matching_blocks()) / max(len(a), 1),
                        sum(x.size for x in SequenceMatcher(None, flipped, b, autojunk=False).get_matching_blocks()) / max(len(flipped), 1),
                    )
                    if cover > score:
                        best, score = rid, cover
                found = best if score >= 0.55 else None
            elif num:
                same = [rid for rid, n, _ in clauses if re.sub(r"\D", "", n or "") == num]
                found = same[0] if len(same) == 1 else None
        mapping[cid] = found
    rows_changed = 0
    for t, c in cols:
        for rid, js in (await q(f"select id::text, {c} from {t} where {c}::text like '%clause_id%'")).all():
            refs: list = []
            _refs(js, refs)
            hit = False
            for cid, obj in refs:
                if cid not in alive and mapping.get(cid):
                    for k in _KEYS:
                        if obj.get(k) == cid:
                            obj[k] = mapping[cid]
                    hit = True
            if hit:
                await q(f"update {t} set {c}=cast(:j as jsonb) where id::text=:i", {"j": json.dumps(js, ensure_ascii=False), "i": rid})
                rows_changed += 1
    _CACHE.pop("n", None)
    return {"found": sum(1 for v in mapping.values() if v), "left": sum(1 for v in mapping.values() if not v), "rows": rows_changed}
