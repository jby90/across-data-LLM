# ruff: noqa: E501
"""Standard compliance documents generated from stored, versioned templates.

A template is data: which facts the document needs, which sections it has, and where the legal
basis is drawn from. The engine below never mentions a country, a regulation or a document type;
adding a document type or a country is adding a template row, and editing a template creates a new
version so a document can always be traced to the template version it was made from.

Facts come from three places and are resolved in this order: the assessment itself (country,
vehicle, scenario), the answers already given in the conversation, and answers the user gives for
this document. A fact that is still missing is returned as a question; the engine never invents a
value and never fills one in silently.
"""

from __future__ import annotations

import copy
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, Integer, String, UniqueConstraint, func, select
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from app.analysis import risk_items
from app.core.db import Base

PLACEHOLDER = re.compile(r"\{\{\s*([a-z0-9_]+)\s*\}\}")
DOC_FACT_PREFIX = "doc_"  # 为文件单独回答的事实存放在评估的事实里，加前缀避免和评估必填事实混淆
MAX_ANSWER_CHARS = 1000


class DocumentTemplate(Base):
    __tablename__ = "document_templates"
    __table_args__ = (
        UniqueConstraint("code", "version_no", name="uq_document_template_version"),
        CheckConstraint("status IN ('published','retired')", name="ck_document_template_status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    code: Mapped[str] = mapped_column(String(64), index=True)
    version_no: Mapped[int] = mapped_column(Integer)
    name_zh: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(16), default="published")
    definition: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)


BLANK_FOR_COMPANY = "（待企业填写）"
DRAFT_BLANK = "【待补充】"  # 初稿里还没回答的栏目


class TemplateError(ValueError):
    """The template definition is malformed."""


@dataclass
class Question:
    key: str
    label: str
    question: str
    why: str
    short: bool = False  # 一句话能答完的事实，界面用单行输入


@dataclass
class Rendered:
    blocks: list[dict] = field(default_factory=list)
    missing: list[Question] = field(default_factory=list)
    used_facts: dict[str, str] = field(default_factory=dict)
    optional_open: list[Question] = field(default_factory=list)  # 可补充但不拦截的栏目
    needs_paper: bool = False  # 含论述部分、尚未生成（见 document_narrative）
    cited: list[dict] = field(default_factory=list)  # 正文“依据”表里放进的条文（条文 id、所在章节）


def validate_definition(definition: dict) -> None:
    """Reject a template that refers to facts it does not declare, so mistakes surface on save."""
    if not isinstance(definition, dict):
        raise TemplateError("模板定义格式不正确")
    raw_facts = definition.get("facts", [])
    if not isinstance(raw_facts, list):
        raise TemplateError("事实项列表格式不正确")
    fact_keys: list[str] = []
    for f in raw_facts:
        if not isinstance(f, dict) or not isinstance(f.get("key"), str) or not f["key"].strip():
            raise TemplateError("事实项定义缺少有效的键名")
        fact_keys.append(f["key"])
    facts = set(fact_keys)
    if len(facts) != len(fact_keys):
        raise TemplateError("事实项的键重复")
    raw_sections = definition.get("sections")
    if not isinstance(raw_sections, list) or not raw_sections:
        raise TemplateError("模板没有章节")
    for section in raw_sections:
        if not isinstance(section, dict):
            raise TemplateError("模板章节结构格式不正确")
        for cond in ("when", "unless"):
            cond_val = section.get(cond, {})
            if cond_val is None:
                cond_val = {}
            if not isinstance(cond_val, dict):
                raise TemplateError("章节生效条件格式不正确")
            unknown_keys = set(cond_val) - facts
            if unknown_keys:
                raise TemplateError("章节条件引用了未声明的事实项")
        if section.get("narrative"):
            if not definition.get("narrative"):
                raise TemplateError("论述位置没有对应的论述部分定义")
            continue
        if not isinstance(section.get("heading"), str) or not section.get("heading", "").strip():
            raise TemplateError("章节缺标题")
        raw_blocks = section.get("blocks", [])
        if not isinstance(raw_blocks, list):
            raise TemplateError("章节内容块列表格式不正确")
        for block in raw_blocks:
            if not isinstance(block, dict):
                raise TemplateError("内容块结构格式不正确")
            kind = block.get("type")
            if kind not in ("text", "facts_table", "citations", "checklist", "static_table", "risk_matrix", "slot"):
                raise TemplateError("未知内容块类型")
            b_keys = block.get("keys", [])
            if b_keys is not None and (not isinstance(b_keys, list) or any(not isinstance(x, str) for x in b_keys)):
                raise TemplateError("内容块字段引用列表格式不正确")
            b_key = block.get("key")
            if b_key is not None and not isinstance(b_key, str):
                raise TemplateError("内容块字段键名格式不正确")
            refs: set[str] = set(b_keys or []) | ({b_key} if b_key else set())
            b_text = block.get("text", "")
            b_items = block.get("items", [])
            if not isinstance(b_text, str) or not isinstance(b_items, list) or any(not isinstance(x, str) for x in b_items):
                raise TemplateError("内容块文本或条目格式不正确")
            for piece in [b_text, *b_items]:
                refs |= set(PLACEHOLDER.findall(piece))
            unknown = refs - facts
            if unknown:
                raise TemplateError("内容块引用了未声明的事实项")


def resolve_facts(definition: dict, task_values: dict[str, str], answers: dict[str, str]) -> dict:
    """Each declared fact: what the assessment already holds, else this document's own answer. A fact
    that only borrows another fact as its default (``from`` naming a different key, such as the
    transfer summary defaulting to the business scene) keeps the user's own answer when there is one:
    the default must not overwrite what the user wrote."""
    values: dict[str, str] = {}
    for fact in definition.get("facts", []):
        key = fact["key"]
        known = str(task_values.get(fact.get("from") or key, "") or "").strip()
        given = str(answers.get(DOC_FACT_PREFIX + key, "") or "").strip()
        borrowed = bool(fact.get("from")) and fact["from"] != key
        limit = risk_items.MAX_CHARS if key in risk_items.LONG_KEYS else MAX_ANSWER_CHARS
        values[key] = ((given or known) if borrowed else (known or given))[:limit]
    return values


def _applies(section: dict, values: dict[str, str]) -> bool:
    """A section may apply only to some routes: ``when`` / ``unless`` name facts that hold a
    country, compared as the country the user named (the document's route, not the assessed one)."""
    from app.analysis.document_basis import country_of

    for key, wanted in section.get("when", {}).items():
        if country_of(values.get(key)) != wanted:
            return False
    for key, banned in section.get("unless", {}).items():
        if country_of(values.get(key)) == banned:
            return False
    return True


def render(
    definition: dict,
    values: dict[str, str],
    citations: list[dict],
    basis: list[str] | None = None,
    paper: list[dict] | None = None,
    draft: bool = False,
    slots: dict[str, list[dict]] | None = None,
    note: str = "",
) -> Rendered:
    """Fill the template and list every fact still missing; the export waits until none are.
    ``basis`` (where the document's structure comes from) is no longer printed in the document;
    ``slots`` are the parts the platform writes itself (``doc_sections``); ``note`` is the line
    under the title (project and date), so two projects' files are never mistaken for each other."""
    declared = {f["key"]: f for f in definition.get("facts", [])}
    out = Rendered()
    needed: set[str] = set()

    def given(key: str) -> bool:
        """Answered — and, for a question answered in rows (the main risks), every row complete."""
        return bool(values.get(key)) and not (key in risk_items.STRUCTURED and risk_items.lacking(values[key]))

    def shown(key: str) -> str:
        """The value; an optional fact nobody has given reads “待企业填写” instead of blocking."""
        if values.get(key):
            return risk_items.shown(key, values[key], DRAFT_BLANK if draft else "")
        if declared.get(key, {}).get("optional"):
            return BLANK_FOR_COMPANY
        return DRAFT_BLANK if draft else ""

    def _split_retention_and_location(raw: str) -> tuple[str, str]:
        """Split a combined retention_and_blackbox sentence into (retention_period_part, storage_location_part)
        for Standard Contract Appendix I (八) vs (九) when both are present; otherwise return (raw, raw)."""
        txt = (raw or "").strip()
        if not txt or "；" not in txt and "，" not in txt:
            return txt, txt
        clauses = [c.strip() for c in re.split(r"[；;]", txt) if c.strip()]
        loc_parts: list[str] = []
        dur_parts: list[str] = []
        for cl in clauses:
            sub_clauses = [sc.strip() for sc in cl.split("，") if sc.strip()]
            has_loc = any(k in cl for k in ("存储于", "存放于", "保存在", "集群", "服务器", "数据中心", "专区", "云系统", "阵列"))
            has_dur = any(k in cl for k in ("期限", "天", "日", "个月", "年", "期满", "删除", "销毁", "擦除", "清除"))
            if has_loc and has_dur and len(sub_clauses) >= 2:
                for sc in sub_clauses:
                    if any(k in sc for k in ("期限", "天", "日", "个月", "年", "期满", "删除", "销毁", "擦除", "清除")):
                        dur_parts.append(sc)
                    else:
                        loc_parts.append(sc)
            elif has_dur and not has_loc:
                dur_parts.append(cl)
            elif has_loc and not has_dur:
                loc_parts.append(cl)
            else:
                dur_parts.append(cl)
                loc_parts.append(cl)
        dur_str = "；".join(dur_parts).strip() if dur_parts else txt
        loc_str = "；".join(loc_parts).strip() if loc_parts else txt
        return dur_str, loc_str

    def fill(text: str) -> str:
        def one(match: re.Match) -> str:
            key = match.group(1)
            needed.add(key)
            val = shown(key)
            if key == "recipient" and ("境外接收方" in text or "境外数据接收方" in text) and val:
                from app.analysis.official_docx import _clean_recipient_entity_name

                return _clean_recipient_entity_name(val)
            if key == "retention_and_blackbox" and val:
                if text.startswith("（八）出境后保存期限："):
                    return _split_retention_and_location(val)[0]
                if text.startswith("（九）出境后保存地点："):
                    return _split_retention_and_location(val)[1]
            return val

        # 回答自带句号或分号时，模板里的标点不再重复碰撞
        merged = PLACEHOLDER.sub(one, text)
        merged = re.sub(r"。；", "；", merged)
        merged = re.sub(r"[；，]。", "。", merged)
        return re.sub(r"([。；！？，])\1+", r"\1", merged)

    out.blocks.append({"type": "title", "text": definition["title"]})
    if note:
        out.blocks.append({"type": "paragraph", "text": note})
    shown_clauses: set[str] = set()  # 同一条文在一份文件里只出现一次
    seen_laws: set[str] = set()  # 同一部法规首次出现时附注外文原名，不再单独重复一张《引用法规一览》表
    for section in definition["sections"]:
        if not _applies(section, values):
            continue
        if section.get("narrative"):
            # 论述部分用到的事实也要问到：没问就写论述，只会得到一篇“待企业补充”
            for part in definition.get("narrative", []):
                needed.update(k for k in part.get("facts", []) if k in declared)
            existing_headings = {b["text"] for b in out.blocks if b.get("type") == "heading"}
            if paper is None:
                out.needs_paper = True
                if draft:
                    # 初稿：论述还没写，不假装有内容；按成稿的章节给出提纲，每节列出要用到的信息（已有的照填、缺的标待补充），
                    # 读者拿到初稿就知道这份文件会写什么、还差什么
                    parts = definition.get("narrative", [])
                    for part in parts:
                        ph = part.get("heading") or "论述部分"
                        if ph not in existing_headings:
                            out.blocks.append({"type": "heading", "text": ph})
                            existing_headings.add(ph)
                        keys = [k for k in part.get("facts", []) if k in declared]
                        if keys:
                            out.blocks.append({"type": "table", "head": ["本节用到的信息", "内容"], "rows": [[declared[k]["label"], shown(k)] for k in keys]})
                    if not parts:
                        out.blocks.append({"type": "heading", "text": section.get("heading") or "论述部分"})
                        out.blocks.append({"type": "paragraph", "text": "【待生成】这一部分在信息补齐后由系统依据目标国法规条文写成。"})
            else:
                copied_paper = copy.deepcopy(paper)
                if copied_paper and copied_paper[0].get("type") == "heading" and copied_paper[0].get("text") in existing_headings:
                    copied_paper = copied_paper[1:]
                out.blocks.extend(copied_paper)
            continue
        out.blocks.append({"type": "heading", "text": section["heading"]})
        for block in section.get("blocks", []):
            kind = block["type"]
            if kind == "text":
                out.blocks.append({"type": "paragraph", "text": fill(block["text"])})
            elif kind == "checklist":
                out.blocks.append({"type": "list", "items": [fill(i) for i in block["items"]]})
            elif kind == "facts_table":
                needed.update(block["keys"])
                rows = [[declared[k]["label"], shown(k)] for k in block["keys"]]
                out.blocks.append({"type": "table", "head": ["事项", "内容"], "rows": rows})
            elif kind == "static_table":
                out.blocks.append(
                    {"type": "table", "head": block["head"], "rows": [list(r) for r in block["rows"]]}
                )
            elif kind == "risk_matrix":
                # 风险清单与“可能性 × 严重程度”矩阵：由企业逐行填的风险得出，等级按矩阵算，不由平台判断
                needed.add(block["key"])
                out.blocks.extend(risk_items.blocks(values.get(block["key"], ""), DRAFT_BLANK if draft else ""))
            elif kind == "slot":
                out.blocks.extend(copy.deepcopy((slots or {}).get(block["name"], [])))
            elif kind == "citations":
                _cite_heads = (
                    ["编号", "法规与条款", "条文摘录"],
                    ["法规与条款", "条文摘录"],
                    ["编号", "依据", "条款", "条文内容"],
                    ["依据", "条款", "条文内容"],
                )
                has_paper_refs = bool(
                    paper
                    and any(
                        b.get("type") == "table" and b.get("head") in _cite_heads
                        for b in paper
                    )
                )
                picked = [] if has_paper_refs else [c for c in _pick([c for c in citations if _clause_key(c) not in shown_clauses], block)]
                shown_clauses.update(_clause_key(c) for c in picked)
                cite_rows: list[list[str]] = []
                for c in picked:
                    if c.get("clause_id"):
                        out.cited.append({"clause_id": str(c["clause_id"]), "document_id": str(c.get("document_id") or ""), "section": section["heading"]})
                    zh = str(c.get("document_title") or "").strip()
                    orig = str(c.get("original_title") or "").strip()
                    if zh and orig and orig != zh and zh not in seen_laws and orig not in zh:
                        seen_laws.add(zh)
                        law_cell = f"《{zh}》（{orig}）"
                    else:
                        if zh:
                            seen_laws.add(zh)
                        law_cell = f"《{zh}》"
                    cite_rows.append([law_cell, str(c.get("clause_number", "")), _quote(c)])
                if cite_rows and not has_paper_refs:
                    out.blocks.append(
                        {
                            "type": "table",
                            "head": ["依据", "条款", "条文内容"],
                            "rows": cite_rows,
                        }
                    )
    for key in sorted(needed, key=list(declared).index):
        if not given(key) and declared[key].get("optional"):
            f = declared[key]
            out.optional_open.append(
                Question(key, f["label"], f["question"], f.get("why", ""), bool(f.get("short")))
            )
        if not given(key) and not declared[key].get("optional"):
            f = declared[key]
            out.missing.append(
                Question(key, f["label"], f["question"], f.get("why", ""), bool(f.get("short")))
            )
    from app.analysis.document_narrative import polish_legal_prose

    for blk in out.blocks:
        if "text" in blk and isinstance(blk["text"], str):
            txt = polish_legal_prose(_sort_cite_chains(blk["text"]))
            if blk.get("type") == "heading" and (
                txt == "所依据的条文" or (txt.startswith("所依据的条文（") and txt.endswith("对应此表）"))
            ):
                txt = "参考条文"
            blk["text"] = txt
        if "items" in blk and isinstance(blk["items"], list):
            blk["items"] = [
                polish_legal_prose(_sort_cite_chains(it)) if isinstance(it, str) else it
                for it in blk["items"]
            ]
        if (
            blk.get("type") == "table"
            and isinstance(blk["rows"], list)
            and blk.get("head")
            not in (
                ["编号", "法规与条款", "条文摘录"],
                ["法规与条款", "条文摘录"],
                ["依据", "条款", "条文内容"],
                ["编号", "依据", "条款", "条文内容"],
            )
        ):
            blk["rows"] = [
                [polish_legal_prose(cell) if isinstance(cell, str) else cell for cell in row]
                for row in blk["rows"]
            ]
    out.used_facts = {k: risk_items.shown(k, values[k]) for k in needed if values.get(k)}
    return out


_MULTI_CITE = re.compile(r"(?:\[\d+\]){2,}")


def _sort_cite_chains(text: str) -> str:
    if "[" not in text:
        return text
    return _MULTI_CITE.sub(
        lambda m: "".join(f"[{n}]" for n in sorted({int(x) for x in re.findall(r"\d+", m.group(0))})),
        text,
    )


_NO_TRANSLATION = ("", "(未提供译文)", "（未提供译文）")


def _quote(citation: dict, limit: int = 300) -> str:
    """The Chinese rendering when there is one; otherwise the clause in its own language. A long
    clause is cut at the end of a sentence (never in the middle of one) and marked as cut."""
    from app.analysis.clause_gate import (
        _close_delims_before_ellipsis,
        clean_margin_numbers,
        strip_own_label,
    )

    zh = strip_own_label(str(citation.get("clause_number") or ""), str(citation.get("quote_zh") or "")).strip()
    text = zh if zh not in _NO_TRANSLATION else clean_margin_numbers(str(citation.get("quote_original") or "")).strip()
    if len(text) <= limit:
        return text
    cut = max(text.rfind(mark, 0, limit) for mark in ("。", "；", ";", "！", "？", "\n", ". "))
    if cut >= limit // 3:
        return _close_delims_before_ellipsis(text[: cut + 1].rstrip()) + " ……"
    end = limit
    if end < len(text) and text[end - 1].isascii() and text[end].isascii():
        while end > limit - 24 and text[end - 1].isalnum():
            end -= 1
    return _close_delims_before_ellipsis(text[:end].rstrip()) + "……"


def _clause_key(c: dict) -> str:
    return str(c.get("clause_id") or "") or f"{c.get('document_title', '')}|{c.get('clause_number', '')}"


def _pick(citations: list[dict], block: dict) -> list[dict]:
    """The block's clauses in the order of the dimensions it names (what matters most first)."""
    dimensions = block.get("dimensions")
    wanted = [c for c in citations if _wanted(c, block)]
    if dimensions:
        wanted.sort(key=lambda c: -1 if c.get("core") else dimensions.index(c.get("dimension_code")) if c.get("dimension_code") in dimensions else len(dimensions))  # 稳定排序：同一方面内保持检索顺序
    return wanted[: int(block.get("limit", 8))]


def _wanted(citation: dict, block: dict) -> bool:
    dimensions = block.get("dimensions")
    # 车企核心法规（发出国的汽车数据规定）属于每一张依据表
    return not dimensions or citation.get("dimension_code") in dimensions or bool(citation.get("core"))


def task_values(task: Any) -> dict[str, str]:
    """The values the assessment itself already holds, under fixed keys any template may use."""
    from app.analysis.document_basis import NAMES as COUNTRY_NAMES_ZH

    base = {
        "country_name": COUNTRY_NAMES_ZH.get(task.country_code, "" if task.country_code in ("--", "") else task.country_code),
        "vehicle_model": task.vehicle_model or "",
        "scenario": getattr(task, "scenario_label", "") or task.scenario_type or "",
        "company_name": "",
    }
    out = {**{k: str(v) for k, v in (task.facts_json or {}).items()}, **base}
    explicit_kinds = str(out.get("doc_sensitive_pi_kinds") or "").strip()
    out["sensitive_pi_kinds"] = explicit_kinds or sensitive_kinds(out.get("doc_sensitive_pi") or out.get("sensitive_pi") or "")
    return out


def sensitive_kinds(answer: str) -> str:
    """What the contract's “敏感个人信息种类” line says: the kinds the enterprise wrote when
    sensitive PI is involved, “不涉及” when negated, otherwise left to fill."""
    from app.analysis import cn_route

    a = (answer or "").strip()
    if not a or a.startswith("暂不确定"):
        return ""
    ans = cn_route._yes(a, "sensitive_pi")
    if ans is False:
        return "不涉及"
    if ans is not True:
        return ""
    if cn_route._ADVERSATIVE_SPLIT_RE.search(a):
        parts = cn_route._ADVERSATIVE_SPLIT_RE.split(a)
        rest = parts[-1].strip() if cn_route._eval_non_adversative(parts[-1], "sensitive_pi") is True else a
    elif m_exc := cn_route._EXCEPT_POS_RE.search(a):
        rest = m_exc.group(1).strip()
    else:
        pos_clauses = [
            c.strip()
            for c in re.split(r"[，,；;]+", a)
            if c.strip() and not cn_route._HEAD_NEG_RE.match(c.strip())
        ]
        rest = "、".join(pos_clauses) if pos_clauses else a
    rest = re.sub(r"^是[，,。:：；;、\s]*", "", rest)
    rest = re.sub(r"^(?:仅|只)?(?:涉及|包含|含|有|限于|为|属于)[：:\s]*", "", rest)
    rest = re.sub(
        r"[，,；;、（(]?\s*(?:预计|约|共|累计|涉及)?\s*(?:不满|不足|少于|低于)?\s*[\d,.]+\s*(?:亿|万|千|百)?\s*"
        r"(?:名|位|个)?(?:自然人|车主|用户|驾驶员|乘客|员工|客户|人).*$",
        "",
        rest,
    ).strip()
    rest = re.sub(r"^[（(]\s*(?:涉及)?", "", rest).strip()
    if "(" not in rest and "（" not in rest:
        rest = re.sub(r"\s*[）)]$", "", rest).strip()
    elif (rest.startswith("（") and rest.endswith("）")) or (rest.startswith("(") and rest.endswith(")")):
        rest = rest[1:-1].strip()
    rest = rest.strip("，,。；;、 ")
    if rest in ("", "是", "有", "涉及", "含", "包含", "敏感个人信息", "敏感个人数据"):
        return ""
    return rest


_DEFAULT_TEMPLATES_ENSURED = False


async def latest_template(session: AsyncSession, code: str) -> DocumentTemplate | None:
    memo = session.info.setdefault("latest_templates", {})
    if code in memo:
        return memo[code]
    row = await session.scalar(
        select(DocumentTemplate)
        .where(DocumentTemplate.code == code, DocumentTemplate.status == "published")
        .order_by(DocumentTemplate.version_no.desc())
        .limit(1)
    )
    memo[code] = row
    return row


async def save_template(
    session: AsyncSession, code: str, name_zh: str, definition: dict, user_id: uuid.UUID | None
) -> DocumentTemplate:
    """Editing never overwrites: it adds the next version and retires nothing a document used."""
    from sqlalchemy import text as sa_text

    validate_definition(definition)
    await session.execute(
        sa_text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"),
        {"k": f"doc_tpl:{code}"},
    )
    last = await session.scalar(
        select(func.max(DocumentTemplate.version_no)).where(DocumentTemplate.code == code)
    )
    row = DocumentTemplate(
        code=code,
        version_no=(last or 0) + 1,
        name_zh=name_zh,
        definition=definition,
        created_by=user_id,
    )
    session.add(row)
    await session.flush()
    session.info.get("latest_templates", {}).pop(code, None)
    return row


async def ensure_default_templates(session: AsyncSession) -> None:
    """Load the starting templates; a code already there is only moved to a newer starting revision
    when its latest version is still the system's own (a version a person saved is never replaced)."""
    global _DEFAULT_TEMPLATES_ENSURED
    from app.analysis.document_templates_default import DEFAULT_TEMPLATES
    from app.knowledge.cache_sync import keeper_active, use_cached

    # 一次请求里只核一遍；后台常驻进程已完成首次核对后也不再每次读库提交
    if session.info.get("default_templates_ensured") or use_cached(_DEFAULT_TEMPLATES_ENSURED):
        return
    changed = False
    for code, name, definition in DEFAULT_TEMPLATES:
        last = await session.scalar(
            select(DocumentTemplate)
            .where(DocumentTemplate.code == code)
            .order_by(DocumentTemplate.version_no.desc())
            .limit(1)
        )
        if last is None:
            await save_template(session, code, name, definition, None)
            changed = True
        elif last.created_by is None and int(definition.get("revision", 1)) > int(
            last.definition.get("revision", 1)
        ):
            await save_template(session, code, name, definition, None)
            changed = True
    if changed:
        await session.commit()
    session.info["default_templates_ensured"] = True
    if keeper_active:
        _DEFAULT_TEMPLATES_ENSURED = True


def _infer_widths(head: list[str], rows: list[list[str]]) -> list[int]:
    """Semantic percentage widths for tables without explicit ``widths`` so narrow index/code
    columns never steal half the page from prose descriptions."""
    n = len(head)
    if n <= 1:
        return [100]
    h0 = (head[0] or "").strip()
    if n == 2:
        if h0 in ("编号", "序号", "步骤"):
            return [10, 90]
        if h0 in ("事项", "本节用到的信息"):
            return [26, 74]
        if head == ["法规（中文名）", "原名"]:
            return [42, 58]
        if h0 == "准入事项":
            return [32, 68]
        return [34, 66]
    if n == 3:
        if head == ["依据", "条款", "条文内容"]:
            return [26, 12, 62]
        if head == ["编号", "法规与条款", "条文摘录"]:
            return [8, 30, 62]
        if h0 in ("材料名称", "材料"):
            return [32, 28, 40]
        if h0 in ("步骤", "序号", "编号"):
            return [9, 28, 63]
        return [28, 34, 38]
    if n == 4:
        if h0 in ("序号", "编号", "步骤"):
            return [8, 28, 22, 42]
        if h0 == "环节":
            return [16, 26, 26, 32]
        if h0 == "准入事项":
            return [24, 22, 34, 20]
        if "可能性" in h0:
            return [22, 26, 26, 26]
        return [20, 26, 26, 28]
    if n == 5:
        if "可能性" in head:
            return [8, 44, 15, 15, 18]
        if h0 in ("步骤", "序号", "编号"):
            return [8, 36, 22, 18, 16]
        return [20, 20, 20, 20, 20]
    if n == 6:
        if "可能性" in head:
            return [7, 28, 10, 11, 12, 32]
        if h0 == "序号":
            return [7, 21, 19, 15, 23, 15]
    base = [max(8, round(100 / n))] * n
    base[-1] = 100 - sum(base[:-1])
    return base


def _blocks_have_arabic(blocks: list[dict], context: str) -> bool:
    arabic_re = re.compile(r"[\u0600-\u06ff]")
    if arabic_re.search(context or ""):
        return True
    for blk in blocks:
        if arabic_re.search(str(blk.get("text") or "")):
            return True
        for it in blk.get("items") or []:
            if arabic_re.search(str(it)):
                return True
        for h in blk.get("head") or []:
            if arabic_re.search(str(h)):
                return True
        for row in blk.get("rows") or []:
            if any(arabic_re.search(str(c)) for c in row):
                return True
    return False


def build_docx(
    rendered: Rendered,
    context: str,
    *,
    foreign: bool = False,
    table_style: str = "formal_black",
) -> bytes:
    """Word output through the same writer the reports use: ``table_style='edpb'`` strictly matches
    the official EU EDPB 2026 DPIA template's table borders/headings, while ``'formal_black'`` uses
    uniform solid black borders and black headers for all template-less documents."""
    from app.analysis.report_exporter import _docx_package, _w_p, _w_table, should_indent_prose

    head_color = "0A105D" if table_style == "edpb" else "000000"
    parts: list[str] = []
    blocks = rendered.blocks
    rtl = bool(foreign and _blocks_have_arabic(blocks, context))
    for i, block in enumerate(blocks):
        kind = block["type"]
        if kind == "title":
            parts.append(
                _w_p(
                    block["text"],
                    bold=True,
                    size=36,
                    color=head_color,
                    style="Title",
                    after=240,
                    rtl=rtl,
                    align="center" if rtl else None,
                )
            )
        elif kind == "heading":
            parts.append(_w_p(block["text"], bold=True, size=26, color=head_color, style="Heading1", keep_next=True, rtl=rtl))
        elif kind == "subheading":
            parts.append(_w_p(block["text"], bold=True, size=22, color=head_color, style="Heading2", keep_next=True, rtl=rtl))
        elif kind == "paragraph":
            # 表格前的说明句（如“风险矩阵（……）：”）与表格同页，不单独留在页底
            before_table = i + 1 < len(blocks) and blocks[i + 1]["type"] == "table"
            lines = [ln for ln in (block["text"] or "").split("\n") if ln.strip()] or [block["text"]]
            for li, ln in enumerate(lines):
                is_last = li == len(lines) - 1
                parts.append(
                    _w_p(
                        ln,
                        keep_next=(before_table and is_last),
                        rich=True,
                        prose_indent=(not foreign and should_indent_prose(ln)),
                        rtl=rtl,
                        align="both" if rtl else None,
                    )
                )
        elif kind == "list":
            parts.extend(_w_p("• " + item, indent=0 if rtl else 360, after=60, rich=True, rtl=rtl) for item in block["items"])
        elif kind == "pagebreak":  # 一份下载件里的两部分（如申请书和办理指引）各自从新页开始
            parts.append("<w:p><w:r><w:br w:type='page'/></w:r></w:p>")
        elif kind == "table":
            widths = block.get("widths") or _infer_widths(block["head"], block["rows"])
            parts.append(_w_table(block["head"], block["rows"], widths, table_style=table_style, rtl=rtl))
            if i == len(blocks) - 1:
                parts.append(
                    "<w:p><w:pPr><w:spacing w:before='0' w:after='0' w:line='20' w:lineRule='exact'/></w:pPr>"
                    "<w:r><w:rPr><w:sz w:val='2'/><w:szCs w:val='2'/></w:rPr></w:r></w:p>"
                )
            else:
                parts.append(_w_p("", after=80, rtl=rtl))
    return _docx_package(parts, context, clean=True, foreign=foreign, heading_color=head_color, rtl=rtl)


def build_pdf(
    rendered: Rendered,
    context: str,
    *,
    foreign: bool = False,
    table_style: str = "formal_black",
) -> bytes:
    """The PDF of a document is its Word file printed to PDF (app.analysis.docx_pdf), so the two
    are the same document. Only where no office suite is installed (the test suite) is the PDF
    drawn by the offline renderer below."""
    from app.analysis.docx_pdf import PdfEngineUnavailable, docx_to_pdf, engine

    if engine() != "builtin":
        try:
            return docx_to_pdf(build_docx(rendered, context, foreign=foreign, table_style=table_style))
        except PdfEngineUnavailable:
            pass
    return _render_pdf(rendered, context, table_style=table_style, foreign=foreign)


def _render_pdf(
    rendered: Rendered,
    context: str,
    *,
    table_style: str = "formal_black",
    foreign: bool = False,
) -> bytes:
    """PDF through the offline renderer (embedded fonts, Arabic shaping): used only without an
    office suite."""
    from types import SimpleNamespace

    from app.analysis.pdf_renderer import render_pdf

    rtl = bool(foreign and _blocks_have_arabic(rendered.blocks, context))
    blocks: list[dict] = []
    number = 0
    for block in rendered.blocks:
        kind = block["type"]
        if kind == "title":
            blocks.append({"t": "h1", "text": block["text"]})
        elif kind == "heading":
            blocks.append({"t": "h2", "text": block["text"]})
        elif kind == "subheading":
            blocks.append({"t": "p", "text": f"**{block['text']}**"})
        elif kind == "paragraph":
            blocks.append({"t": "p", "text": block["text"]})
        elif kind == "list":
            for item in block["items"]:
                number += 1
                blocks.append({"t": "p", "text": "• " + item})
        elif kind == "table":
            width = block.get("widths") or _infer_widths(block["head"], block["rows"])
            if block.get("no_head"):
                spans = block.get("spans")
                grid_rows: list[list[dict]] = []
                for r_idx, row in enumerate(block["rows"]):
                    g_row: list[dict] = []
                    for c_idx, cell_text in enumerate(row):
                        rs, cs = (
                            spans[r_idx][c_idx]
                            if spans and r_idx < len(spans) and c_idx < len(spans[r_idx])
                            else [1, 1]
                        )
                        if rs == 0 or cs == 0:
                            continue
                        g_row.append({"text": cell_text, "rows": rs, "span": cs})
                    if g_row:
                        grid_rows.append(g_row)
                blocks.append({"t": "grid", "rows": grid_rows, "widths": width})
            else:
                blocks.append(
                    {"t": "table", "head": block["head"], "rows": block["rows"], "widths": width}
                )
    task = SimpleNamespace(country_code="", vehicle_model="")
    rv = SimpleNamespace(consulting=None)
    return render_pdf(
        task,
        rv,
        dimension=None,
        context=context,
        country="",
        notices=[],
        blocks=blocks,
        table_style=table_style,
        rtl=rtl,
    )
