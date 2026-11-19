# ruff: noqa: E501  词表是数据，不按代码行宽折行
"""Business-relevant sections of a long law: which parts of a document are in the intake scope.

A long code (a road code with its decrees, a telecoms code…) is mostly about things the platform
does not serve — speed limits, driving licences, penalties. Such a document may be marked
“按业务相关章节入库”: it is cut into its parts (books, titles, chapters, and the decrees and orders
printed with it), and a part is in scope when it is about the platform's business — cross-border
vehicle data and the approvals needed to bring vehicles to a market:

* its title, or a fair share of its clauses, speaks of type approval, registration and plates,
  technical inspection, on-board equipment and conversions, automated or trial vehicles, or data
  and personal information (``TOPICS``: one row per subject, the words of each language side by
  side — never a list per country);
* or one of its clauses is cited by an admission item, the knowledge graph or a country briefing;
* or it cannot be told (no title found): what is unsure stays in.

Out-of-scope clauses keep their original text and any translation already made; they are not
translated further, not extracted into the graph and not counted by the intake quality card. The
reading page says the part is unrelated to vehicle data and market access.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

RULE = "business-sections-v1"
MODE = "business_sections"

# 结构标题：一部法规里的编、章，以及随法规一起印的法令、部长令（各语言一行，按需再加）
# 结构标题分两层：上层是编以及随法规一起印的法令、部长令；下层是章（各语言一行，按需再加）
_TOP = (
    r"(?:LIVRE|Livre|TITRE|Titre)\s+(?:[IVXLC]+|PREMIER|premier|\d+)\b"
    r"|D[ée]cret\s+n[°o]\s*\S+|DECRET\s+N[°O]\s*\S+|Arr[êe]t[ée]\s+(?:du|des|\d)|ARRETE\s+DU|Loi\s+n[°o]\s*\S+"
    r"|(?:PART|Part)\s+(?:[IVXLC]+|\d+)\b"
    r"|(?:الباب|الكتاب|مرسوم|قرار|قانون)\s+(?:رقم|\S+)"
)
_SUB = r"(?:CHAPITRE|Chapitre|CHAPTER|Chapter)\s+(?:[IVXLC]+|PREMIER|premier|\d+)\b|الفصل\s+\S+"
_HEADING = re.compile(rf"^\s*(?:(?P<top>{_TOP})|(?P<sub>{_SUB}))", re.M)
_LEADER = re.compile(r"\.{8,}|…{3,}")
_CONTENTS_LINE = re.compile(r"(?:\.{8,}|…{3,})[.…\s]*\d{1,4}\b")

# 业务主题：每一行一个主题，各语言的说法并列
_VEHICLE = r"(?:v[ée]hicul|vehicle|automobil|motocycl|remorqu|trailer|مركب|سيار|عرب)"
TOPICS: list[tuple[str, str, bool]] = [
    # (主题, 词, 是否须与“车辆”同现)
    ("型式认证 / 认可", r"r[ée]ception|homologat|type[- ]approv|approbation de type|certificat de conformit|conformity|اعتماد النوع|المصادقة|الموافقة على الطراز", False),
    ("登记与牌照", r"immatricul|enregistrement des v|registration|register of vehicles|plaque|number plate|licen[cs]e plate|carte grise|mise en circulation|placing on the market|ترقيم|تسجيل المركبات|لوحة|لوحات", False),
    ("技术检验", r"visite technique|contr[ôo]le technique|technical inspection|roadworthiness|الفحص الفني|المراقبة الفنية", False),
    ("车载设备与改装", r"[ée]quipement|am[ée]nagement|transformation|modification|dispositif|equipment|conversion|retrofit|تجهيز|تعديل", True),
    ("自动驾驶与试验车辆", r"autonome|automatis[ée]|exp[ée]riment|autonomous|automated|self-driving|trial|ذاتية القيادة|تجريب", False),
    ("数据与个人信息", r"donn[ée]es|fichier|traitement automatis|personal data|personal information|\bdata\b|البيانات|المعطيات|الشخصية", False),
]
_TOPICS = [(name, re.compile(words, re.I), needs_vehicle) for name, words, needs_vehicle in TOPICS]
_VEHICLE_RX = re.compile(_VEHICLE, re.I)
SHARE = 0.3  # 标题看不出时，这一部分里至少三成条文讲到业务主题，才算相关


def topics_in(text: str) -> list[str]:
    flat = " ".join((text or "").split())
    return [name for name, rx, needs_vehicle in _TOPICS if rx.search(flat) and (not needs_vehicle or _VEHICLE_RX.search(flat))]


@dataclass
class Part:
    title: str
    first: int  # 起止条文序号（含）
    last: int
    clause_ids: list[uuid.UUID] = field(default_factory=list)
    parent: str = ""  # 章所属的上层（编、法令）标题；上层自己为空
    in_scope: bool = True
    why: str = ""

    def as_dict(self) -> dict:
        return {"title": self.title, "parent": self.parent, "from": self.first, "to": self.last, "clauses": len(self.clause_ids), "in_scope": self.in_scope, "why": self.why}


def _is_contents(text: str) -> bool:
    """A table of contents (many titles behind dot leaders) or a list of amending texts: not where a
    part begins."""
    # 点线后面跟页码才是目录；表单里留空让人填写的点线后面没有页码
    return len(_CONTENTS_LINE.findall(text or "")) >= 2


def cut_parts(clauses: list) -> list[Part]:
    """Cut a version's clauses (with ``id``, ``ordinal``, ``original_text``) into its parts: a top
    part (book, title, decree, order) runs until the next top part, a chapter until the next
    heading, and a chapter belongs to the top part above it. A title near the start of a clause
    opens the part at that clause; a title further down (after the end of the previous text) opens
    it at the next clause."""
    parts: list[Part] = []
    top = ""
    pending: tuple[str, str] | None = None  # (title, top part) opening at the next clause
    for c in sorted(clauses, key=lambda x: x.ordinal):
        text = c.original_text or ""
        start, pending = pending, None
        if not _is_contents(text):
            for m in _HEADING.finditer(text):
                title = " ".join(text[m.start():m.start() + 220].split())
                is_top = bool(m.group("top"))
                if is_top:
                    top = title
                found = (title, "" if is_top else top)
                if m.start() <= max(200, len(text) / 2):
                    start = start or found
                else:
                    pending = found
        if start is not None:
            parts.append(Part(title=start[0], first=c.ordinal, last=c.ordinal, parent=start[1]))
        elif not parts:
            parts.append(Part(title="", first=c.ordinal, last=c.ordinal))
        parts[-1].last = c.ordinal
        parts[-1].clause_ids.append(c.id)
    return parts


def decide(parts: list[Part], clauses: list, cited: set) -> list[Part]:
    """In scope: the part (or the top part it belongs to) is titled after a business subject; or
    one of its clauses is cited; or no title is found; or, failing a telling title, a fair share of
    its clauses speak of a business subject."""
    by_id = {c.id: c for c in clauses}
    for p in parts:
        hit = [i for i in p.clause_ids if i in cited]
        own = topics_in(p.title + " " + p.parent) if p.parent and _VEHICLE_RX.search(p.parent) else topics_in(p.title)
        parent_topics = topics_in(p.parent) if p.parent else []
        if hit:
            p.in_scope, p.why = True, f"有 {len(hit)} 条被准入事项、知识图谱或国家简报引用"
        elif parent_topics:
            p.in_scope, p.why = True, "所属部分的标题讲" + "、".join(parent_topics)
        elif own:
            p.in_scope, p.why = True, "标题讲" + "、".join(own)
        elif not p.title:
            p.in_scope, p.why = True, "看不出章节标题，按相关处理"
        else:
            talk = [i for i in p.clause_ids if topics_in(by_id[i].original_text)]
            share = len(talk) / max(len(p.clause_ids), 1)
            p.in_scope = share >= SHARE
            p.why = f"{len(talk)}/{len(p.clause_ids)} 条讲到业务主题" + ("" if p.in_scope else "，与汽车数据和准入无关")
    return parts


async def cited_clause_ids(session, clause_ids: list) -> set:
    """Clauses of this version cited by an admission item or step, the knowledge graph or a country
    briefing (by clause id)."""
    from sqlalchemy import text

    if not clause_ids:
        return set()
    ids = list(clause_ids)
    found = set()
    for sql in (
        "select evidence_clause_id from admission_entries where evidence_clause_id = any(:ids)",
        "select evidence_clause_id from admission_steps where evidence_clause_id = any(:ids)",
        "select evidence_clause_id from kg_nodes where evidence_clause_id = any(:ids)",
        "select evidence_clause_id from kg_edges where evidence_clause_id = any(:ids)",
    ):
        found |= {r[0] for r in (await session.execute(text(sql), {"ids": ids})).all()}
    papers = " ".join(r[0] or "" for r in (await session.execute(text(
        "select distinct on (country_code) blocks::text || report::text from country_whitepapers order by country_code, version_no desc"))).all())
    found |= {i for i in ids if str(i) in papers}
    return found


async def plan(session, document_id: uuid.UUID) -> dict:
    """The scope of a document's latest parsed version, worked out but not saved."""
    from sqlalchemy import func, select

    from app.ingestion.models import Clause, DocumentVersion

    version = (await session.execute(
        select(DocumentVersion).where(DocumentVersion.document_id == document_id, DocumentVersion.state == "parsed")
        .order_by(DocumentVersion.version_no.desc()).limit(1))).scalar_one_or_none()
    if version is None:
        raise ValueError("这份文件还没有解析完成的版本")
    clauses = list((await session.scalars(select(Clause).where(Clause.version_id == version.id).order_by(Clause.ordinal))).all())
    parts = decide(cut_parts(clauses), clauses, await cited_clause_ids(session, [c.id for c in clauses]))
    inside = [i for p in parts if p.in_scope for i in p.clause_ids]
    del func
    return {
        "mode": MODE, "rule": RULE, "version_id": str(version.id), "decided_at": datetime.now(UTC).isoformat(),
        "parts": [p.as_dict() for p in parts],
        "in_scope_ordinals": sorted(by_ordinal for p in parts if p.in_scope for by_ordinal in range(p.first, p.last + 1)),
        "in_scope": len(inside), "total": len(clauses),
    }


def scoped(scope: dict | None, version_id) -> set[int] | None:
    """The in-scope clause ordinals of this version, or None when the whole document is in scope
    (not marked, or marked on an older version: a new version is judged again)."""
    if not scope or scope.get("mode") != MODE or str(scope.get("version_id")) != str(version_id):
        return None
    return set(scope.get("in_scope_ordinals") or [])


def part_of(scope: dict | None, ordinal: int) -> dict | None:
    for p in (scope or {}).get("parts", []):
        if p["from"] <= ordinal <= p["to"]:
            return p
    return None


async def save(session, doc, plan: dict) -> None:
    """Record the document's intake scope (or clear it with ``{}``); the decision is kept in the
    knowledge audit trail with what it was before."""
    import hashlib
    import json

    from app.knowledge.models import KnowledgeAuditRecord

    payload = {"before": doc.intake_scope or {}, "after": plan}
    session.add(KnowledgeAuditRecord(
        target_type="documents", target_id=doc.id, action="intake_scope_set", payload=payload,
        content_hash=hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest(),
    ))
    doc.intake_scope = plan


async def scopes_for(session, document_ids) -> dict:
    """{document id: intake scope} for the documents that are taken in by sections."""
    from sqlalchemy import select

    from app.ingestion.models import Document

    ids = list(document_ids)
    if not ids:
        return {}
    rows = (await session.execute(select(Document.id, Document.intake_scope).where(Document.id.in_(ids)))).all()
    return {i: sc for i, sc in rows if sc and sc.get("mode") == MODE}


def out_of_scope(scope: dict | None, version_id, ordinal: int) -> bool:
    inside = scoped(scope, version_id)
    return inside is not None and ordinal not in inside


def note_for(scope: dict | None, version_id, ordinal: int) -> str | None:
    """What the reading page says on a clause outside the scope."""
    if not out_of_scope(scope, version_id, ordinal):
        return None
    return "与汽车数据和准入无关"
