# ruff: noqa: E501
"""Subscriptions to regulation changes (requirement 2.3).

A user picks countries, kinds of regulation and business areas (an empty list = any). From then on,
every change the platform collects for a matching law becomes one notice in “更新通知”:

- a collected law was revised (``version_changes`` with changed clauses): subject ``change:<id>``;
- a law was newly collected: subject ``doc:<id>``.

Each subject gets one ``change_advice`` row shared by every notice about it: the changed clauses
before and after (with the platform's Chinese translation), what the change means for an
enterprise and what to do — written once by the model in the background. The notice also lists the
enterprise's projects the change touches (computed when the list is read). One notice per user per
subject (``dedupe_key``); a report-impact notice about the same change is folded into it.
"""

from __future__ import annotations

import asyncio
import logging
import re
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import and_, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.ingestion.models import Clause, Document, DocumentVersion
from app.knowledge.business_topic import TOPIC_ZH, TOPICS
from app.updates.models import ChangeAdvice, UserNotification, UserSubscription, VersionChange

log = logging.getLogger(__name__)

# 法规类型：与用户端知识库“法规目录”的分级同一口径
DOC_TYPES: list[tuple[str, str]] = [
    ("LAW", "法律"),
    ("REGULATION", "法规与实施规则"),
    ("GUIDANCE", "监管指南与框架"),
    ("STANDARD", "标准"),
]
COUNTRY_ORDER = ("SG", "EG", "TN", "MA", "LB", "CN")  # 与用户端各页的国家顺序一致
DOC_TYPE_ZH = dict(DOC_TYPES)
ADMISSION_KIND = "ADMISSION_GUIDANCE"
SHOWN_RELEVANCE = ("relevant", "related")
CHANGED_KIND = "text_change_pending_review"  # 条文确有增删改（只是字节变化、待解析的不推）
MAX_PAIRS = 6
CLIP = 1200


def doc_type(doc: Document) -> str:
    return "GUIDANCE" if doc.instrument_kind == ADMISSION_KIND else (doc.document_type or "OTHER").upper()


def country_zh(code: str) -> str:
    from app.analysis.document_basis import NAMES

    return NAMES.get((code or "").upper(), code)


def matches(sub: UserSubscription, doc: Document) -> bool:
    if not (sub.countries or sub.doc_types or sub.topics):
        return False
    return (
        (not sub.countries or doc.country_code in sub.countries)
        and (not sub.doc_types or doc_type(doc) in sub.doc_types)
        and (not sub.topics or (doc.business_topic or "") in sub.topics)
    )


def describe(sub: UserSubscription | None) -> str:
    """一句话说清订了什么，如“新加坡、埃及 · 法律 · 个人数据保护与跨境传输”。"""
    if sub is None or not (sub.countries or sub.doc_types or sub.topics):
        return ""
    parts = [
        "、".join(country_zh(c) for c in sub.countries) or "所有国家",
        "、".join(DOC_TYPE_ZH.get(t, t) for t in sub.doc_types) or "所有类型",
        "、".join(TOPIC_ZH.get(t, t) for t in sub.topics) or "所有业务领域",
    ]
    return " · ".join(parts)


async def options(session: AsyncSession) -> dict[str, list[dict[str, str]]]:
    shown = select(Document).where(Document.relevance.in_(SHOWN_RELEVANCE))
    codes = set((await session.scalars(shown.with_only_columns(Document.country_code).distinct())).all())
    kinds = {(k or "").upper() for k in (await session.scalars(shown.with_only_columns(Document.document_type).distinct())).all()}
    topics = set((await session.scalars(shown.with_only_columns(Document.business_topic).distinct())).all())
    codes_sorted = sorted((c for c in codes if c), key=lambda c: (COUNTRY_ORDER.index(c) if c in COUNTRY_ORDER else 99, c))
    return {
        "countries": [{"value": c, "label": country_zh(c)} for c in codes_sorted],
        "doc_types": [{"value": v, "label": z} for v, z in DOC_TYPES if v in kinds],
        "topics": [{"value": v, "label": z} for v, z in TOPICS if v in topics],
    }


def _title(doc: Document) -> str:
    return doc.title_zh or doc.title


def _cn_date(d) -> str:
    return f"{d.year} 年 {d.month} 月 {d.day} 日" if d else ""


async def _translations(session: AsyncSession, ids: list[uuid.UUID]) -> dict[uuid.UUID, str]:
    from app.knowledge.models import ClauseTranslation

    if not ids:
        return {}
    rows = (
        await session.execute(
            select(ClauseTranslation.clause_id, ClauseTranslation.translated_text).where(
                ClauseTranslation.clause_id.in_(ids), ClauseTranslation.is_current.is_(True)
            )
        )
    ).all()
    return {cid: txt for cid, txt in rows if txt}


def _clip(text: str) -> str:
    text = (text or "").strip()
    return text if len(text) <= CLIP else text[:CLIP].rstrip() + "……"


CONTEXT = 160  # 改动处前后各留多少字


def around_change(before: str, after: str) -> tuple[str, str]:
    """Both sides cut to the part that changed plus some context, so a one-word amendment in a long
    clause is not lost below the fold (or past the length limit)."""
    before, after = (before or "").strip(), (after or "").strip()
    if not before or not after:
        return _clip(before), _clip(after)
    head = 0
    while head < min(len(before), len(after)) and before[head] == after[head]:
        head += 1
    tail = 0
    while tail < min(len(before), len(after)) - head and before[-1 - tail] == after[-1 - tail]:
        tail += 1
    start = max(0, head - CONTEXT)

    def cut(t: str) -> str:
        end = min(len(t), len(t) - tail + CONTEXT)
        piece = t[start:end]
        more = end < len(t) or len(piece) > CLIP
        return ("……" if start else "") + piece[:CLIP].rstrip() + ("……" if more else "")

    return cut(before), cut(after)


async def change_pairs(session: AsyncSession, change: VersionChange) -> list[dict[str, Any]]:
    """The changed clauses, before and after, original wording and the platform's translation."""
    items = [c for c in (change.changes_json or []) if c.get("old") or c.get("new")][:MAX_PAIRS]
    ids = [uuid.UUID(x["clause_id"]) for c in items for x in [*c.get("old", []), *c.get("new", [])]]
    clauses = {c.id: c for c in (await session.scalars(select(Clause).where(Clause.id.in_(ids)))).all()} if ids else {}
    zh = await _translations(session, ids)

    def side(refs: list[dict]) -> dict[str, str]:
        cs = [clauses.get(uuid.UUID(r["clause_id"])) for r in refs]
        cs = [c for c in cs if c is not None]
        return {
            "text": "\n".join(c.original_text for c in cs),
            "zh": "\n".join(zh.get(c.id, "") for c in cs if zh.get(c.id)),
            "clause_id": str(cs[0].id) if cs else "",
        }

    out = []
    for c in items:
        label = c.get("clause_number") or c.get("section_path") or ""
        before, after = side(c.get("old", [])), side(c.get("new", []))
        for key in ("text", "zh"):
            before[key], after[key] = around_change(before[key], after[key])
        out.append({
            "kind": {"added": "新增", "removed": "删除"}.get(c.get("kind", ""), "修改"),
            "label": f"第 {label} 条" if label and str(label)[:1].isdigit() else label,
            "before": before,
            "after": after,
        })
    return out


async def _first_clauses(session: AsyncSession, doc: Document, limit: int = 12) -> str:
    version = await session.scalar(
        select(DocumentVersion)
        .where(DocumentVersion.document_id == doc.id, DocumentVersion.state == "parsed")
        .order_by(DocumentVersion.version_no.desc())
        .limit(1)
    )
    if version is None:
        return ""
    clauses = (
        await session.scalars(select(Clause).where(Clause.version_id == version.id).order_by(Clause.ordinal).limit(limit))
    ).all()
    zh = await _translations(session, [c.id for c in clauses])
    return "\n\n".join(_clip(zh.get(c.id) or c.original_text)[:600] for c in clauses)


async def _events_since(session: AsyncSession, since: datetime) -> list[tuple[str, Document, datetime]]:
    """Changes collected since ``since``: (subject, law, when)."""
    out: list[tuple[str, Document, datetime]] = []
    rows = (
        await session.execute(
            select(VersionChange, Document)
            .join(Document, Document.id == VersionChange.document_id)
            .where(VersionChange.detected_at >= since, VersionChange.change_kind == CHANGED_KIND, Document.relevance.in_(SHOWN_RELEVANCE))
        )
    ).all()
    for ch, doc in rows:
        if await official_revision(session, ch):
            out.append((f"change:{ch.id}", doc, ch.detected_at))
    parsed = select(DocumentVersion.id).where(
        DocumentVersion.document_id == Document.id, DocumentVersion.state == "parsed"
    ).exists()
    dated = select(DocumentVersion.id).where(
        DocumentVersion.document_id == Document.id,
        (DocumentVersion.effective_date.is_not(None)) | (DocumentVersion.published_date.is_not(None)),
    ).exists()  # 没有公布、施行日期的（存量法规首次入库）在库里就筛掉；管理员标记过的除外
    docs = (
        await session.scalars(
            select(Document).where(
                Document.relevance.in_(SHOWN_RELEVANCE), parsed,
                or_(and_(Document.discovered_at >= since, dated), Document.news_marked_at >= since),
            )
        )
    ).all()
    for d in docs:
        day = await law_day(session, d)
        if day is None or (d.news_marked_at is None and await same_as_existing(session, d)):
            continue
        out.append((f"doc:{d.id}", d, day))
    return out


async def law_day(session: AsyncSession, doc: Document) -> datetime | None:
    """When the law itself came out (effective, else published date). A law first collected without
    either is taken for one already in force before (not news), so it is not pushed."""
    from datetime import UTC, time

    if doc.news_marked_at is not None:
        return doc.news_marked_at  # 管理员确认是新发布的：按标记的时间推送
    ver = await session.scalar(
        select(DocumentVersion).where(DocumentVersion.document_id == doc.id).order_by(DocumentVersion.version_no.desc()).limit(1)
    )
    day = ver and (ver.effective_date or ver.published_date)
    return datetime.combine(day, time(), tzinfo=UTC) if day else None


async def same_as_existing(session: AsyncSession, doc: Document) -> bool:
    """Collected again with exactly the content of another law in the library: nothing changed."""
    mine = select(DocumentVersion.content_hash).where(DocumentVersion.document_id == doc.id)
    return bool(
        await session.scalar(
            select(DocumentVersion.id).where(DocumentVersion.document_id != doc.id, DocumentVersion.content_hash.in_(mine)).limit(1)
        )
    )


def _stem(title: str) -> str:
    return re.sub(r"[（(].*$", "", title or "").strip()


AMENDS = re.compile(r"修改|修正|修订")


async def amended_base(session: AsyncSession, doc: Document) -> Document | None:
    """The law an amending law amends (“关于修改进口商登记法的 2026 年第 4 号法律” → 《进口商登记法》)."""
    title = doc.title_zh or ""
    if not AMENDS.search(title):
        return None
    cands = (
        await session.scalars(
            select(Document).where(Document.country_code == doc.country_code, Document.id != doc.id, Document.relevance.in_(SHOWN_RELEVANCE))
        )
    ).all()
    hits = [c for c in cands if len(_stem(c.title_zh or "")) >= 3 and _stem(c.title_zh or "") in title and not AMENDS.search(c.title_zh or "")]
    return min(hits, key=lambda c: len(c.title_zh or "")) if hits else None


async def _entries_of(session: AsyncSession, doc: Document) -> list[str]:
    from app.admission.models import AdmissionEntry

    rows = (
        await session.scalars(
            select(AdmissionEntry.id).where(
                AdmissionEntry.country_code == doc.country_code, AdmissionEntry.merged_into.is_(None),
                AdmissionEntry.review_status != "out_of_scope", AdmissionEntry.profile["law_ids"].astext.contains(str(doc.id)),
            )
        )
    ).all()
    return sorted(str(r) for r in rows)


def _local_day(value: datetime | None) -> str:
    from datetime import timedelta, timezone

    return value.astimezone(timezone(timedelta(hours=8))).date().isoformat() if value else ""


async def batch_of(session: AsyncSession, lead: Document) -> list[Document]:
    """The laws collected the same day for the same admission item (or amending the same law) as
    ``lead``: one notice for them all, not one per law."""
    mine = set(await _entries_of(session, lead))
    base = await amended_base(session, lead)
    from datetime import timedelta, timezone

    day = _local_day(lead.discovered_at)
    cn = timezone(timedelta(hours=8))
    start = datetime.fromisoformat(day).replace(tzinfo=cn) if day else lead.discovered_at
    others = (
        await session.scalars(
            select(Document).where(
                Document.country_code == lead.country_code, Document.id != lead.id, Document.relevance.in_(SHOWN_RELEVANCE),
                Document.discovered_at >= start, Document.discovered_at < start + timedelta(days=1),  # 同一天收录的才算同一批
            )
        )
    ).all()
    out = [lead]
    for d in sorted(others, key=lambda x: x.discovered_at):
        if _local_day(d.discovered_at) != day or await law_day(session, d) is None or await same_as_existing(session, d):
            continue
        b = await amended_base(session, d)
        if (mine and mine & set(await _entries_of(session, d))) or (base is not None and b is not None and b.id == base.id):
            out.append(d)
    return out


async def official_revision(session: AsyncSession, change: VersionChange) -> dict | None:
    """The amendment a revision notice names: the new official version's order number or label and its
    effective (or publication) date. A text change without them is not pushed as a revision."""
    new = await session.get(DocumentVersion, change.new_version_id)
    if new is None:
        return None
    ref = (new.official_number or new.version_label or "").strip()
    day = new.effective_date or new.published_date
    if not ref or not day:
        return None
    return {"ref": ref, "effective": day.isoformat() if hasattr(day, "isoformat") else str(day), "published": str(new.published_date or "")}


async def fresh_in_batch(session: AsyncSession, whole: list[Document], subs: list[UserSubscription] | None = None) -> list[Document]:
    """The laws of a batch that are news: came out after a matching subscription began (with no
    subscriptions given: on or after the earliest-dated law that is). Earliest-dated first — that
    one names the notice."""
    dated = [(await law_day(session, d), d) for d in whole]
    dated = [(day, d) for day, d in dated if day is not None]
    if subs is not None:
        dated = [(day, d) for day, d in dated if any(s.created_at <= day and matches(s, d) for s in subs)]
    return [d for _, d in sorted(dated, key=lambda x: x[0])]


async def batch_members(session: AsyncSession, lead: Document) -> list[Document]:
    """The laws one notice (subject ``doc:<lead>``) covers: the lead and the batch's laws dated on or
    after it."""
    start = await law_day(session, lead)
    return [d for d in await fresh_in_batch(session, await batch_of(session, lead)) if start is None or (await law_day(session, d)) >= start]


async def push(session: AsyncSession, org_id: uuid.UUID) -> bool:
    """Notices for this enterprise's subscribers; True when some advice still has to be written."""
    subs = (
        await session.scalars(select(UserSubscription).where(UserSubscription.org_id == org_id))
    ).all()
    subs = [s for s in subs if s.countries or s.doc_types or s.topics]
    if not subs:
        return False
    events = await _events_since(session, min(s.created_at for s in subs))
    from app.admission import names
    from app.iam.models import User

    everyone = list((await session.scalars(select(User.id).where(User.org_id == org_id, User.is_active.is_(True)))).all())
    pending = False
    done: set[str] = set()
    for subject, doc, when in sorted(events, key=lambda e: e[1].discovered_at or e[2]):
        if subject in done or not any(s.created_at <= when and matches(s, doc) for s in subs):
            continue  # 不在订阅范围内、或订阅开始前就已公布的，先跳过（同一批里新的那部会再把整批带出来）
        revised = subject.startswith("change:")
        if revised:
            if not any(s.created_at <= when and matches(s, doc) for s in subs):
                continue
            batch = [doc]
        else:
            # 同一天为同一事项（或修改同一部法律）收录的几部合成一条；其中订阅之前就已公布的存量法规不算
            whole = await batch_of(session, doc)
            done |= {f"doc:{d.id}" for d in whole}
            batch = await fresh_in_batch(session, whole, subs)
            if not batch:
                continue
            doc, subject = batch[0], f"doc:{batch[0].id}"
        # 订阅是企业的：企业里每个账号都收到同一条
        users = everyone
        pending |= await ensure_advice(session, subject, doc)
        name = country_zh(doc.country_code)
        body = ""
        if revised:
            title = f"{name}《{_title(doc)}》修订"
            off = await official_revision(session, await session.get(VersionChange, uuid.UUID(subject.split(":", 1)[1])))
            if off:
                body = f"修订依据：{off['ref']}，{off['effective']} 起施行。"
        else:
            bases = [b for b in [await amended_base(session, d) for d in batch] if b is not None]
            base = bases[0] if bases else None
            if base is not None:
                title = f"{name}《{names.law_name(str(base.id), _title(base))}》修订"
            else:
                title = f"{name}《{names.law_name(str(doc.id), _title(doc))}》新发布"
            parts = []
            for d in batch:
                ver = await session.scalar(select(DocumentVersion).where(DocumentVersion.document_id == d.id).order_by(DocumentVersion.version_no.desc()).limit(1))
                if ver is not None and ver.effective_date:
                    when_ = f"，{_cn_date(ver.effective_date)}起施行"
                elif ver is not None and ver.published_date:
                    when_ = f"，{_cn_date(ver.published_date)}公布"
                else:
                    when_ = ""
                parts.append(f"《{names.law_name(str(d.id), _title(d))}》{when_}")
            body = ("修订依据：" if base is not None else "") + "；".join(parts) + "。"
        for uid in users:
            await session.execute(
                insert(UserNotification)
                .values(
                    id=uuid.uuid4(), org_id=org_id, user_id=uid, kind="subscription",
                    change_id=uuid.UUID(subject.split(":", 1)[1]) if revised else None,
                    dedupe_key=f"sub:{subject}", title=title, body=body,
                    link=f"/knowledge?country={doc.country_code}&doc={doc.id}",
                )
                .on_conflict_do_nothing(index_elements=["user_id", "dedupe_key"])
            )
    return pending


async def ensure_advice(session: AsyncSession, subject: str, doc: Document) -> bool:
    """Create the shared advice row (still to be written) once; True when it is not written yet."""
    row = await session.get(ChangeAdvice, subject)
    if row is None:
        pairs: list[dict] = []
        if subject.startswith("change:"):
            change = await session.get(VersionChange, uuid.UUID(subject.split(":", 1)[1]))
            pairs = await change_pairs(session, change) if change else []
        await session.execute(
            insert(ChangeAdvice)
            .values(subject=subject, document_id=doc.id, pairs=pairs, state="pending")
            .on_conflict_do_nothing(index_elements=["subject"])
        )
        return True
    if row.state == "failed" and row.updated_at and (datetime.now(row.updated_at.tzinfo) - row.updated_at).total_seconds() > 1800:
        row.state = "pending"  # 上次没写成（模型不可用）：半小时后打开通知时再写
    return row.state == "pending"


SYSTEM = """你是汽车出海合规顾问。读者是中国车企负责出海合规的人，管两件事：一是车辆数据和个人信息的跨境合规（在当地收集、处理、向中国传回）；二是海外属地准入（进口商登记、车辆型式认证、电信设备认证、数据处理许可等要向当地机构申请的事项）。
根据给出的法规变化（条文修改前后对照），或新收录法规的条文，写两样东西：
1. impact：2 到 4 句。说清这次变化改了什么义务（谁要做什么、期限、门槛、罚则），对上面这类车企的数据合规或准入申请有什么具体影响（如申请资格、要交的材料、有效期、罚则）。只是文字、编号、格式的改动，或者与车辆数据、个人信息和准入申请都无关，才写“对车企没有实质影响”并说明原因。
2. actions：1 到 5 条企业能直接照做的事。每条以动词开头，写清做什么、改哪份文件或哪个流程（文件名只用给出的 8 类合规文件、准入申请材料或递交时带的证明原件；流程如事件通报流程）、期限（条文里有才写）。没有实质影响时只写一条：核对现有文件里引用的这一条，按新条文更新引用。
新发布的法规（没有修改前后对照的），不写“本次修改”“修改后”这类说法，按它现在的规定写。
有期限的（如“施行后一年内”“颁布后三个月”），按给出的公布、施行日期算出具体日期写进这条（如“2026 年 11 月 9 日前”）；没给日期就照原话写期限，不编日期。
不写“建议关注”“密切跟踪”“加强管理”“及时了解”这类空话；条文里没有的义务、期限、条号不要编；不提平台、不提模型。
只返回 JSON：{"impact": "...", "actions": ["...", "..."]}"""


def _prompt(doc: Document, pairs: list[dict], body: str, dates: str = "", context: str = "") -> str:
    head = f"国家：{country_zh(doc.country_code)}\n法规：{doc.title}" + (f"（{doc.title_zh}）" if doc.title_zh else "") + (f"\n{dates}" if dates else "") + (f"\n{context}" if context else "")
    out = head
    if pairs:
        parts = []
        for p in pairs:
            before = p["before"]["zh"] or p["before"]["text"] or "（无，新增）"
            after = p["after"]["zh"] or p["after"]["text"] or "（无，删除）"
            parts.append(f"【{p['kind']} {p['label']}】\n修改前：{before}\n修改后：{after}")
        out += "\n\n这次修订改动的条文：\n\n" + "\n\n".join(parts)
    if body:
        out += ("\n\n同批新发布的法规，以下是它的开头条文：\n\n" if pairs else "\n\n这是新发布的法规，以下是它的开头条文：\n\n") + body
    return out


PAIRS_SYSTEM = """下面是一部修改法律的条文（中文译文），以及被修改法律现有的条文目录。逐处列出修改法律对被修改法律做了哪些改动：
- target：改的是被修改法律的哪一条哪一款，如“第 2 条最后一款”“第 4 条之二”；修改法律没写明是哪一条的，写它的内容，如“继承人重新登记的一款”；
- kind：新增 / 修改 / 删除；
- base_clause：被修改法律里对应的条号（只写数字，如“2”）；新增的条（如“第 4 条之二”）或看不出的写 ""；
- after：修改后的条文，照抄修改法律译文里的原话（一字不改，不加说明）；删除的写 ""。
只列对被修改法律的改动；修改法律自己的施行、授权制定条例、公布之类的条款不列。
只返回 JSON：{"changes": [{"target": "...", "kind": "...", "base_clause": "...", "after": "..."}]}"""


def _flat(t: str) -> str:
    return re.sub(r"[\s，,。.、；;：:（）()“”\"'·]", "", t or "")


async def _clauses_with_zh(session: AsyncSession, doc: Document) -> list[tuple[Clause, str]]:
    version = await session.scalar(
        select(DocumentVersion).where(DocumentVersion.document_id == doc.id, DocumentVersion.state == "parsed").order_by(DocumentVersion.version_no.desc()).limit(1)
    )
    if version is None:
        return []
    clauses = (await session.scalars(select(Clause).where(Clause.version_id == version.id).order_by(Clause.ordinal))).all()
    zh = await _translations(session, [c.id for c in clauses])
    return [(c, zh.get(c.id, "")) for c in clauses]


_AR_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")


async def amendment_pairs(session: AsyncSession, amending: Document, base: Document) -> tuple[list[dict], int]:
    """What an amending law changes in the law it amends, as before/after pairs: the new wording is
    copied from the amending law (checked to be its own words), the old one is the amended law's
    clause of that number; a new article or paragraph has no “before”."""
    from app.admission import names
    from app.analysis.llm import LlmUnavailableError, call_llm_json

    mine = await _clauses_with_zh(session, amending)
    theirs = await _clauses_with_zh(session, base)
    if not mine:
        return [], 0
    text_ = "\n\n".join(z or c.original_text for c, z in mine)[:12000]
    toc = "\n".join(f"第 {(c.clause_number or '').translate(_AR_DIGITS).strip('()（） ')} 条：{(z or c.original_text)[:60]}" for c, z in theirs if c.clause_number)[:6000]
    prompt = f"被修改法律：{names.law_name(str(base.id), _title(base))}\n修改法律：{names.law_name(str(amending.id), _title(amending))}\n\n修改法律的条文：\n{text_}\n\n被修改法律的条文目录：\n{toc}"
    try:
        res = await call_llm_json([{"role": "system", "content": PAIRS_SYSTEM}, {"role": "user", "content": prompt}], max_tokens=6000, temperature=0)
    except LlmUnavailableError:
        return [], 0
    data = res.data if isinstance(res.data, dict) else {}
    by_no = {(c.clause_number or "").translate(_AR_DIGITS).strip("()（） "): (c, z) for c, z in theirs if c.clause_number}
    out = []
    for ch in (data.get("changes") or [])[:MAX_PAIRS]:
        after = str(ch.get("after") or "").strip()
        kind = str(ch.get("kind") or "修改").strip()
        src = next(((c, z) for c, z in mine if after and _flat(after) in _flat(z)), None)
        if kind != "删除" and src is None:
            continue  # 不是修改法律自己的原话：不放
        old = by_no.get(str(ch.get("base_clause") or "").strip()) if kind != "新增" else None
        label = str(ch.get("target") or "").strip()  # 被修改的法律已在标题里，这里只写哪一条哪一款
        out.append({
            "kind": kind if kind in ("新增", "修改", "删除") else "修改",
            "label": label,
            "before": {"text": old[0].original_text if old else "", "zh": old[1] if old else "", "clause_id": str(old[0].id) if old else ""},
            "after": {"text": src[0].original_text if src else "", "zh": after, "clause_id": str(src[0].id) if src else ""},
        })
    return out, res.total_tokens


async def law_context(session: AsyncSession, doc: Document, also: list | None = None) -> str:
    """What the model must know to describe the law as it stands: the later laws that amend it (in
    the library), and the receiving body the admission items resting on it name."""
    import re as _re

    from app.admission import names

    out = []
    m = _re.search(r"(\d{4})\s*年\s*第\s*(\d+)\s*号|No\.?\s*\(?(\d+)\)?\s*of\s*(\d{4})", f"{doc.title_zh or ''} {doc.title}")
    if m:
        year, num = (m.group(1), m.group(2)) if m.group(1) else (m.group(4), m.group(3))
        rows = (await session.execute(select(Document.id, Document.title, Document.title_zh).where(
            Document.country_code == doc.country_code, Document.id != doc.id, Document.relevance.in_(SHOWN_RELEVANCE)))).all()
        amend = [(i, names.law_name(str(i), z or t)) for i, t, z in rows
                 if _re.search(rf"(?<!\d){num}(?!\d)", f"{z or ''} {t}") and year in f"{z or ''} {t}" and _re.search(r"修改|修正|amend|تعديل", f"{z or ''} {t}", _re.I)]
        if amend:
            texts = []
            for i, name in amend:
                body = await _first_clauses(session, await session.get(Document, i), limit=30)
                texts.append(f"【{name}】\n{body[:2500]}")
            out.append(
                "这部法律已被以下法律修改：" + "、".join(n for _, n in amend)
                + "。影响说明和要做的事按修改后的现行规定写（门槛、金额、罚则以修改后的为准）；提到被修改的旧规定时，写明“已被某某法律修改”。修改法律的条文：\n"
                + "\n\n".join(texts)
            )
    from sqlalchemy import or_

    from app.admission.material_kinds import plan_of
    from app.admission.models import AdmissionEntry, AdmissionStep
    from app.admission.router import display_authority

    ids = [str(doc.id), *[str(x) for x in also or []]]
    entries = (await session.scalars(select(AdmissionEntry).where(
        AdmissionEntry.country_code == doc.country_code, AdmissionEntry.merged_into.is_(None), AdmissionEntry.review_status != "out_of_scope",
        or_(*[AdmissionEntry.profile["law_ids"].astext.contains(i) for i in ids])))).all()
    if entries:
        out.append("本国依据这部法规的准入事项及受理机构（照这个写，不另起名称）：" + "；".join(f"{e.name_zh}：{display_authority(e.authority_zh or '', e.country_code) or '未写明'}" for e in entries))
        for e in entries:
            conds = [c.get("condition", "") for c in (e.profile or {}).get("conditions") or [] if c.get("condition")]
            steps = (await session.scalars(select(AdmissionStep).where(AdmissionStep.entry_id == e.id))).all()
            bring = [x["name"] for x in plan_of(e, [m for st in steps for m in st.materials or []]) if x.get("kind") in ("original", "form")]
            if conds or bring:
                out.append(
                    f"“{e.name_zh}”现行要求（申请主体：{(e.profile or {}).get('applicant') or '未写明'}）。影响说明和要做的事提到门槛、金额时，只照这里写，不另写数字；"
                    "提到要带的证明材料时，只用下面递交清单里的名称，清单里没有的不写：\n资格门槛：" + "；".join(conds) + "\n递交清单：" + "、".join(bring)
                )
    from app.analysis.document_templates_default import DEFAULT_TEMPLATES

    out.append("企业的合规文件只有这 8 类，提到要改的文件时只用这些名称：" + "、".join(t for _, t, _ in DEFAULT_TEMPLATES) + "；准入申请材料、递交时带的证明原件也可以提。")
    return "\n".join(out)


_running = False


async def write_pending(factory=None) -> int:
    """Write the advice still pending (background; one at a time). Returns tokens used."""
    global _running
    if _running:
        return 0
    _running = True
    from app.analysis.llm import LlmUnavailableError, call_llm_json
    from app.core.db import get_sessionmaker
    from app.core.rls import system_scope

    factory = factory or get_sessionmaker()
    used = 0
    try:
        with system_scope("订阅推送：写法规变化的影响说明和整改建议"):
            async with factory() as session:
                rows = (await session.scalars(select(ChangeAdvice).where(ChangeAdvice.state == "pending"))).all()
                for row in rows:
                    doc = await session.get(Document, row.document_id)
                    if doc is None:
                        continue
                    from app.admission import names

                    revision = row.subject.startswith("change:")
                    members = [doc] if revision else (await batch_members(session, doc) or [doc])
                    pairs = list(row.pairs or [])
                    plain: list[Document] = []
                    base = None
                    if not revision:
                        for m in members:
                            b = await amended_base(session, m)
                            if b is None:
                                plain.append(m)
                                continue
                            base = base or b
                            if not row.pairs:
                                got, spent = await amendment_pairs(session, m, b)
                                pairs += got
                                used += spent
                        row.pairs = pairs
                    body = "\n\n".join(
                        [f"【{names.law_name(str(m.id), _title(m))}】\n" + await _first_clauses(session, m) for m in plain]
                    )
                    dates = []
                    for m in members:
                        ver = await session.scalar(select(DocumentVersion).where(DocumentVersion.document_id == m.id).order_by(DocumentVersion.version_no.desc()).limit(1))
                        d = "；".join(x for x in (f"公布日期 {ver.published_date}" if ver and ver.published_date else "", f"施行日期 {ver.effective_date}" if ver and ver.effective_date else "") if x)
                        if d:
                            dates.append(f"《{names.law_name(str(m.id), _title(m))}》{d}")
                    context = await law_context(session, base or doc, [m.id for m in members])
                    try:
                        res = await call_llm_json(
                            [{"role": "system", "content": SYSTEM}, {"role": "user", "content": _prompt(base or doc, pairs, body, "\n".join(dates), context)}],
                            max_tokens=8000, temperature=0.1,
                        )
                    except LlmUnavailableError as exc:
                        log.warning("advice for %s not written: %s", row.subject, exc)
                        row.state = "failed"
                        await session.commit()
                        continue
                    data = res.data if isinstance(res.data, dict) else {}
                    actions = [str(a).strip() for a in data.get("actions") or [] if str(a).strip()][:5]
                    row.impact_zh = str(data.get("impact") or "").strip()
                    row.actions = actions
                    row.state = "ready" if row.impact_zh else "failed"
                    row.model, row.tokens = res.model, res.total_tokens
                    used += res.total_tokens
                    await session.commit()
    finally:
        _running = False
    return used


def schedule_writing() -> None:
    """Start writing pending advice without holding up the page (not under tests)."""
    import os

    if "PYTEST_CURRENT_TEST" in os.environ or _running:
        return
    try:
        asyncio.get_running_loop().create_task(write_pending())
    except RuntimeError:
        pass


async def advice_out(session: AsyncSession, subject: str) -> dict[str, Any] | None:
    row = await session.get(ChangeAdvice, subject)
    if row is None:
        return None
    return {"pairs": row.pairs or [], "impact": row.impact_zh, "actions": row.actions or [], "state": row.state}
