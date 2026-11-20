# ruff: noqa: E501
"""白皮书（国家合规洞察简报）的“图谱模板拼装版”：不调用任何大模型，不花 token。

与“模型概括版”（whitepaper.py）并列：同样 8 个固定维度，但每个维度由固定版式把知识图谱里已核验的内容排出来——
法规与监管机构、数据类别、跨境条件、同意告知要求、存储留存要求、处罚、准入事项（来自海外准入模块）、
整改待办（由前面各维度的义务转成）。每一行都带条文出处，文末的条文表（原文摘录 + 中文）由程序按编号列出。

国家页（country_page）用的是这里的结构化内容：每个维度一组要点，每个要点分栏给出“适用对象 / 条件 / 要点”，
依据指向一条完整的条文（被切开存放的长条文按整条给出），法规用中文名在前、原名在后。
法规份数、条数与法规浏览同一口径（library_view）。

特点：结果每次相同；法规或图谱变化后重新拼装就会同步对应章节；没有模型概括出来的“总体情况”段落，
开头只有按图谱数据计数的固定句式。
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import String, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.analysis.document_basis import NAMES as _ALL_COUNTRY_NAMES
from app.analysis.workflow_engine import COUNTRY_NAMES_ZH, EIGHT_DIMENSIONS_SPEC, country_name_zh
from app.ingestion.display_text import for_reading
from app.ingestion.layout_safety import clause_layout_available
from app.ingestion.models import NOT_CITABLE_STATUSES, Clause, Document, DocumentVersion
from app.ingestion.safety import document_available
from app.knowledge import business_topic as bt
from app.knowledge import claim_tags as ct
from app.knowledge import data_classes as dc
from app.knowledge import whitepaper as wp
from app.knowledge.graph_evidence import active_edge_filters
from app.knowledge.library_view import article_of, library, main_law, relation_counts
from app.knowledge.models import KgEdge, KgNode
from app.knowledge.reading import article_label

NUMERALS = "一二三四五六七八九十"
PER_DIMENSION = 12
DEAD = ("stale", "rejected", "superseded")
REF_MAX = 4000  # 条文窗口里显示的原文/中文上限（整条通常远短于此）

# 各维度取哪一方面的要求（claim_tags 给每条义务/处罚标的方面）；还没有标签的节点（刚抽出、
# 尚未打标）退回按名称关键词分流
ASPECT_OF = {"D3": "transfer", "D4": "consent", "D5": "security", "D7": "admission"}
WEIGHT_RANK = {"core": 0, "supporting": 1, "": 2, "peripheral": 3}
TOPIC_RANK = {k: i for i, (k, _) in enumerate(bt.TOPICS)}
LEGISLATURE = ("议会", "国会", "众议院", "参议院", "立法会", "法规库", "公报", "网站", "官方托管", "Statutes", "Gazette")
KEYWORDS = {
    "D3": ("跨境", "境外", "出境", "国外", "外国", "向国外", "充分保护", "接收国"),
    "D4": ("同意", "告知", "通知", "撤回", "权利", "知情", "访问", "更正", "反对", "授权"),
    "D5": ("保存", "留存", "销毁", "删除", "存储", "备份", "加密", "安全", "保密", "期限"),
    "D7": ("申报", "许可", "准入", "注册", "登记", "备案", "认证", "批准", "审批", "声明"),
}
STORAGE_WORDS = ("保存", "留存", "保留", "销毁", "删除", "存储", "备份", "期限", "境内", "本地")
INTRO = {
    "D1": "{name}与汽车数据跨境相关的法规共 {n_inst} 份（其中核心法规 {n_core} 份），涉及主要监管与主管机构 {n_reg} 个。",
    "D2": "{name}法规涉及的车辆业务数据共 {n_groups} 种。",
    "D3": "{name}法规中与数据跨境传输相关的要求共 {n_total} 项。",
    "D4": "{name}法规中与授权、告知、同意和数据主体权利相关的要求共 {n_total} 项。",
    "D5": "{name}法规中与数据存储、保存期限、销毁和安全保护相关的要求共 {n_total} 项。",
    "D6": "{name}法规中的违规追责与处罚规定共 {n_total} 项。",
    "D7": "{name}的市场准入与申报事项共 {n_total} 个；办理环节和材料见“海外属地准入”。",
    "D8": "开展业务时最先要落地的几件事，按办理先后排列。",
}
NONE_FOUND = "已收录的条文未涉及这一方面的具体要求。"
HEADINGS = {
    "D1": ["法规名称", "类别与条数", "官方原名"],
    "D2": ["数据种类", "涉及的数据类别", "依据"],
    "D3": ["要求", "适用对象与条件", "依据"],
    "D4": ["要求", "适用对象与条件", "依据"],
    "D5": ["要求", "适用对象与条件", "依据"],
    "D6": ["处罚", "适用对象与条件", "依据"],
    "D7": ["准入事项", "受理机构与说明", "依据"],
    "D8": ["落地事项", "适用对象与条件", "依据"],
}
WIDTHS = {
    "D1": [32, 14, 54],
    "D2": [24, 46, 30],
    "D3": [30, 40, 30],
    "D4": [30, 40, 30],
    "D5": [30, 40, 30],
    "D6": [32, 38, 30],
    "D7": [30, 40, 30],
    "D8": [30, 40, 30],
}
KIND_RANK = {
    "LAW": 0,
    "REGULATION": 1,
    "GUIDANCE": 2,
    "STANDARD": 3,
    "ADMISSION": 3,
    "FORM": 4,
    "OTHER": 5,
}
KIND_ZH = {
    "LAW": "法律",
    "REGULATION": "法规与实施规则",
    "GUIDANCE": "监管指南",
    "STANDARD": "标准",
    "ADMISSION": "准入申报材料",
    "FORM": "申报表",
    "OTHER": "其他文件",
}


@dataclass
class Ref:
    n: int
    doc_id: str
    clause_id: str
    title: str  # 法规中文名（无中文名时用原名）
    original_title: str
    label: str  # 第43条
    original: str
    zh: str


@dataclass
class Item:
    title: str
    facts: list[tuple[str, str]] = field(default_factory=list)  # [(适用对象, …), (条件, …)]
    ref: int | None = None
    sub: str = ""  # 第二行：原名、类型等
    doc_id: str = ""  # 法规本身（D1）：可在法规浏览中打开
    node_id: str = ""  # 对应的知识图谱节点 ID（供合规主题卡片直接对齐）

    def row(self, refs: _Refs) -> list[str]:
        """The three columns of the exported table."""
        return [
            self.title,
            "；".join(f"{k}：{v}" for k, v in self.facts) or self.sub or "—",
            refs.cite(self.ref),
        ]

    def out(self) -> dict:
        return {
            "title": self.title,
            "sub": self.sub,
            "facts": [{"label": k, "text": v} for k, v in self.facts],
            "ref": self.ref,
            "doc_id": self.doc_id,
        }


def _clip(text: str, n: int) -> str:
    text = re.sub(r"\s+", " ", (text or "")).strip()
    return text if len(text) <= n else text[: n - 1] + "…"


_BILINGUAL = re.compile(r"^[\u0600-\u06FFA-Za-zÀ-ÿ][\u0600-\u06FFA-Za-zÀ-ÿ\s\-’'/]*[（(]([^）)]+)[）)]$")


def _plain(value: str) -> str:
    """“organisation（组织）” / “الأشخاص الاعتبارية（法人）” → “组织” / “法人”."""
    value = re.sub(r"\s+", " ", value or "").strip()
    m = _BILINGUAL.match(value)
    out = m.group(1).strip() if m else value
    out = re.sub(r"[（(][XYPZT][）)]", "", out).strip()
    if out in ("X", "Y", "Z"):
        return "相关组织"
    if out in ("P", "T"):
        return "相关个人"
    if re.match(r"^[XYZ](?:\s*(?:与|和|或|及|/)\s*[XYZ])+(?:（视情况而定）)?$", out):
        return "交易相关各方组织"
    return out


def _attr(node: KgNode, key: str) -> str:
    val = ((node.attributes or {}).get("ontology") or {}).get(key) or {}
    return str(val.get("value") or "").strip() if isinstance(val, dict) else ""


def _score(node: KgNode) -> tuple:
    onto = (node.attributes or {}).get("ontology") or {}
    return (
        (node.attributes or {}).get("node_class") == "concrete",
        bool(_attr(node, "obligated_party")),
        len(onto),
        -len(node.name_zh or ""),
    )


_TITLE_TAIL = re.compile(
    r"s+(?:relative|relatif|relatifs|relatives|portant|pris|prise|fixant|concernant|on|for|of|to|regarding)|[（(，,:：]",
    re.I,
)


def _short_law(name: str) -> str:
    """The law's short name (“Loi n° 09-08”) when the full title is long; empty if no short form."""
    head = _TITLE_TAIL.split((name or "").strip(), maxsplit=1)[0].strip()
    return head if 4 <= len(head) <= 30 else ""


_DIGIT_ZH = {"1": "一", "2": "二", "3": "三", "4": "四", "5": "五", "6": "六", "7": "七", "8": "八", "9": "九", "10": "十"}


def _key(name: str) -> str:
    norm = _plain(name or "")
    norm = norm.replace("保留", "保存").replace("不得超过", "不超过").replace("不得多于", "不超过")
    norm = re.sub(r"(?<!\d)(10|[1-9])(?=(?:个)?(?:月|年|日|天))", lambda m: _DIGIT_ZH[m.group(1)], norm)
    return re.sub(r"[\W_]+", "", norm)[:18]


def _doc_title(doc: Document) -> str:
    return (doc.title_zh or "").strip() or doc.title


class _Refs:
    """Numbering of the articles cited in the briefing; one article keeps one number, whichever of
    its stored pieces a graph item points at."""

    def __init__(self, session: AsyncSession) -> None:
        self.session, self.by_clause, self.items = session, {}, []
        self.units: dict = {}
        self.by_head: dict = {}
        self._ver_doc: dict[Any, tuple[DocumentVersion, Document]] = {}
        self._clause_ver: dict[Any, Any] = {}
        self._tr_by_clause: dict[Any, list[str]] = {}
        self._loaded_tr_ids: set[Any] = set()

    async def preload(self, clause_ids: set[Any] | list[Any]) -> None:
        from app.knowledge.library_view import USABLE_TRANSLATION
        from app.knowledge.models import ClauseTranslation
        from app.knowledge.reading import reading_units

        wanted = {cid for cid in clause_ids if cid is not None and cid not in self.by_clause and cid not in self._ver_doc}
        if not wanted:
            return
        rows = (
            await self.session.execute(
                select(Clause.id, Clause.version_id, DocumentVersion, Document)
                .join(DocumentVersion, Clause.version_id == DocumentVersion.id)
                .join(Document, Document.id == DocumentVersion.document_id)
                .where(
                    Clause.id.in_(wanted),
                    document_available(),
                    clause_layout_available(),
                )
            )
        ).all()
        seen_cids = set()
        new_ver_ids = set()
        for cid, vid, ver, doc in rows:
            seen_cids.add(cid)
            if (
                ver.legal_status in NOT_CITABLE_STATUSES
                or ver.review_status == "rejected"
                or getattr(doc, "relevance", "") in ("irrelevant", "out_of_scope")
            ):
                self.by_clause[cid] = None
                continue
            self._ver_doc[cid] = (ver, doc)
            self._clause_ver[cid] = vid
            if vid not in self.units:
                new_ver_ids.add(vid)
        for missing_cid in wanted - seen_cids:
            self.by_clause[missing_cid] = None
        if new_ver_ids:
            all_clauses = (
                await self.session.scalars(select(Clause).where(Clause.version_id.in_(new_ver_ids)))
            ).all()
            by_ver: dict[Any, list[Clause]] = {}
            for cl in all_clauses:
                by_ver.setdefault(cl.version_id, []).append(cl)
            for vid, c_list in by_ver.items():
                self.units[vid] = reading_units(c_list)
        unit_clause_ids: set[Any] = set()
        for cl_id in wanted:
            vid = self._clause_ver.get(cl_id)
            u_list = self.units.get(vid) if vid is not None else None
            if u_list is not None:
                hit = next((u for u in u_list if cl_id in u.ids), None)
                if hit is not None:
                    unit_clause_ids.update(rid for rid in hit.ids if rid not in self._loaded_tr_ids)
        if unit_clause_ids:
            self._loaded_tr_ids.update(unit_clause_ids)
            trs = (
                await self.session.scalars(
                    select(ClauseTranslation).where(
                        ClauseTranslation.clause_id.in_(unit_clause_ids),
                        ClauseTranslation.is_current.is_(True),
                        ClauseTranslation.quality_check_passed.is_(True),
                        ClauseTranslation.review_status.in_(USABLE_TRANSLATION),
                    )
                )
            ).all()
            for tr in trs:
                self._tr_by_clause.setdefault(tr.clause_id, []).append(tr.translated_text)

    async def number(self, clause_id: Any) -> int | None:
        if clause_id is None:
            return None
        if clause_id in self.by_clause:
            return self.by_clause[clause_id]
        row = self._ver_doc.get(clause_id)
        if row is None:
            row = (
                await self.session.execute(
                    select(DocumentVersion, Document)
                    .join(Clause, Clause.version_id == DocumentVersion.id)
                    .join(Document, Document.id == DocumentVersion.document_id)
                    .where(
                        Clause.id == clause_id,
                        document_available(),
                        clause_layout_available(),
                    )
                )
            ).first()
        if (
            row is None
            or row[0].legal_status in NOT_CITABLE_STATUSES
            or row[0].review_status == "rejected"
            or getattr(row[1], "relevance", "") in ("irrelevant", "out_of_scope")
        ):
            self.by_clause[clause_id] = None
            return None
        vid = self._clause_ver.get(clause_id)
        if vid is not None and vid in self.units:
            from app.knowledge.reading import joined

            unit = next((u for u in self.units[vid] if clause_id in u.ids), None)
            if unit is None:
                self.by_clause[clause_id] = None
                return None
            parts = [
                self._tr_by_clause[r.id][0] if len(self._tr_by_clause.get(r.id, [])) == 1 else None
                for r in unit.rows
            ]
            zh = joined([p or "〔此段暂无中文〕" for p in parts]) if any(parts) else ""
            found = (unit.head, unit.rows, zh)
        else:
            found = await article_of(self.session, clause_id, self.units)
        if found is None:
            self.by_clause[clause_id] = None
            return None
        head, rows, zh = found
        if head.id in self.by_head:
            self.by_clause[clause_id] = self.by_head[head.id]
            return self.by_head[head.id]
        doc = row[1]
        number = len(self.items) + 1
        original = "\n".join(r.original_text.strip("\n") for r in rows)
        label = article_label(head.clause_number) or (head.heading or "")
        self.items.append(
            Ref(
                number,
                str(doc.id),
                str(head.id),
                _doc_title(doc),
                doc.title,
                label,
                for_reading(original)[:REF_MAX],
                for_reading(zh)[:REF_MAX],
            )
        )
        self.by_clause[clause_id] = self.by_head[head.id] = number
        return number

    def cite(self, n: int | None) -> str:
        if n is None:
            return "—"
        ref = self.items[n - 1]
        return f"《{_clip(ref.title, 40)}》{ref.label} [{n}]"


async def _live_claims(session: AsyncSession, cc: str, skip: set[str]) -> list[KgNode]:
    rows = (
        await session.scalars(
            select(KgNode).where(
                KgNode.country_code == cc,
                KgNode.node_type.in_(("OBLIGATION", "PENALTY")),
                KgNode.review_status.notin_(DEAD),
                func.coalesce(KgNode.verify_state, "") != "retired",
                (KgNode.verify_state == "auto") | (KgNode.review_status == "approved"),
            )
        )
    ).all()
    out = []
    for n in rows:
        if n.document_id and str(n.document_id) in skip:
            continue
        if (n.attributes or {}).get("node_class") in ("generic", "regulator"):
            continue
        out.append(n)
    return out


def _rank(nodes: list[KgNode], docs: dict[str, tuple[str, str]]) -> list[KgNode]:
    """Requirements of the data-protection laws first (the business is data crossing borders),
    then cybersecurity, vehicle and telecom rules; within a category the core laws before the
    supporting ones; then vehicle-specific before general; then the better-described node."""
    by_detail = sorted(nodes, key=_score, reverse=True)

    def key(n: KgNode) -> tuple:
        weight, topic = _doc_of(n, docs)
        return (
            TOPIC_RANK.get(topic, 9),
            WEIGHT_RANK.get(weight, 2),
            ct.CLOSE_RANK.get(ct.closeness_of(n) or "data", 1),
        )

    return sorted(by_detail, key=key)


def _doc_of(node: KgNode, docs: dict[str, tuple[str, str]]) -> tuple[str, str]:
    """(business weight, business topic) of the law the node comes from: by its document, else by
    the document of its evidence clause (most graph nodes carry only the clause)."""
    return docs.get(str(node.document_id)) or docs.get(str(node.evidence_clause_id)) or ("", "")


def _law_core(title: str) -> str:
    """The law's name without the edition note and the number prefix: “第81号法：电子交易与个人数据法
    （人权委员会转载版）” and “电子交易与个人数据法（议会版）” are one law to a reader."""
    t = re.sub(r"[（(][^）)]*[）)]", "", title or "")
    t = re.sub(r"^[^：:]{0,20}[：:]", "", t)
    return re.sub(r"\s+", "", t)


def _aspect(node: KgNode) -> str:
    """The node's aspect; an untagged node by the old keyword rules."""
    if aspect := ct.aspect_of(node):
        return aspect
    if node.node_type == "PENALTY":
        return "penalty"
    if _attr(node, "cross_border_condition") or _matches(node, KEYWORDS["D3"]):
        return "transfer"
    if _attr(node, "retention_period") or _matches(node, KEYWORDS["D5"]):
        return "security"
    if _matches(node, KEYWORDS["D4"]):
        return "consent"
    if _matches(node, KEYWORDS["D7"]):
        return "admission"
    return "other"


def _distinct(nodes: list[KgNode]) -> list[KgNode]:
    """Keep the first of nodes with the same name (the same requirement extracted from two texts)."""
    seen, out = set(), []
    for n in nodes:
        k = _key(n.name_zh)
        if k not in seen:
            seen.add(k)
            out.append(n)
    return out


def _pick(nodes: list[KgNode], limit: int, docs: dict[str, tuple[str, str]] | None = None) -> list[KgNode]:
    seen, out = set(), []
    if limit <= 0:
        return out
    for n in _rank(nodes, docs or {}):
        k = _key(n.name_zh)
        if k in seen:
            continue
        seen.add(k)
        out.append(n)
        if len(out) >= limit:
            break
    return out


def _matches(node: KgNode, words: tuple[str, ...]) -> bool:
    text = " ".join(
        [node.name_zh or "", _attr(node, "applicable_condition"), _attr(node, "data_scope")]
    )
    return any(w in node.name_zh for w in words) or (sum(1 for w in words if w in text) >= 3)


async def _claim_items(refs: _Refs, nodes: list[KgNode], *, penalty: bool = False) -> list[Item]:
    await refs.preload([n.evidence_clause_id for n in nodes if n.evidence_clause_id])
    items = []
    for n in nodes:
        facts = []
        if who := _plain(_attr(n, "obligated_party")):
            facts.append(("适用对象", who))
        if cond := _plain(_attr(n, "applicable_condition")):
            facts.append(("适用条件", cond))
        if penalty:
            if amount := _attr(n, "penalty_amount"):
                facts.append(("处罚", amount))
            if level := _attr(n, "violation_level"):
                facts.append(("违法情形", level))
        else:
            if period := _attr(n, "retention_period"):
                facts.append(("保存期限", period))
            if cross := _attr(n, "cross_border_condition"):
                facts.append(("跨境条件", cross))
        title = _plain(n.name_zh)
        if (
            penalty
            and n.node_type == "OBLIGATION"
            and not re.search(r"罚|监禁|拘役|吊销|没收|责任|制裁|处以|追责", title)
        ):
            core = re.sub(r"(?:的)?(?:合规)?(?:义务|要求)$", "", title).strip() or title
            title = f"违反“{core}”的法律责任"
        ref_no = await refs.number(n.evidence_clause_id)
        if n.evidence_clause_id is not None and ref_no is None:
            continue
        items.append(Item(title, facts, ref_no, node_id=str(n.id)))
    return items


async def _unrelated_names(session: AsyncSession, cc: str) -> list[str]:
    nodes = (
        await session.scalars(
            select(KgNode).where(
                KgNode.country_code == cc,
                KgNode.node_type == "DATA_CATEGORY",
                KgNode.review_status.notin_(DEAD),
            )
        )
    ).all()
    related = {_plain(n.name_zh) for n in nodes if dc.class_of(n) not in (None, "unrelated")}
    return [
        name
        for n in nodes
        if dc.class_of(n) == "unrelated"
        and len(name := _plain(n.name_zh)) >= 4
        and name not in related
    ]


def _about(node: KgNode, names: list[str]) -> bool:
    text = " ".join(
        (node.name_zh or "", _attr(node, "applicable_condition"), _attr(node, "data_scope"))
    )
    return any(name in text for name in names)


# 法条里没有点名的角色（“主管机关”“执法机构”“注册官或授权人员”）、司法咨询机关或多人并列职务，不是单独的行政监管机构
UNNAMED_ROLE = re.compile(
    r"^(主管|执法|调查|有关|相关|主管调查)?(机关|机构|部门|当局)$|主管机关|主管部门|授权人员|^注册官"
    r"|^(管理局|委员会|中心|监管局|监管机构|理事会|署|局|主管部长)$"
    r"|部长[和与及]|国家协商委员会|法院$|法庭$|副警察总监|网络安全专员|^警察总监$"
)


def _display_regulator_name(raw: str) -> str:
    clean = _plain(raw)
    return re.sub(r"部长(?!会议)", "部", clean)
HAN = re.compile(r"[一-鿿]")
_SMALL_WORDS = {"of", "the", "and", "for", "de", "la", "le", "les", "des", "du", "à", "et", "d", "l"}
_COUNTRY_WORDS = tuple(
    sorted(
        {w for w in (*COUNTRY_NAMES_ZH.values(), *_ALL_COUNTRY_NAMES.values()) if len(w) >= 2 and w != "中国"},
        key=len,
        reverse=True,
    )
)


def _zh_key(name: str) -> str:
    """“摩洛哥国家个人数据保护监督委员会 CNDP”“个人资料保护委员会（Commission）”→“数据保护委员会”."""
    t = re.sub(r"[（(][^）)]*[）)]", "", name or "")
    t = re.sub(r"[A-Za-z\u0600-\u06FF'’ /.-]+", "", t)
    for w in _COUNTRY_WORDS:
        t = t.replace(w, "")
    return t.replace("资料", "数据").replace("国家", "").replace("个人", "").replace("监督", "").strip()


def _acronyms(*names: str) -> set[str]:
    """Acronyms written in the name (“(CNDP)”“NTRA”) and the initials of a full Latin name
    (“Land Transport Authority” → LTA)."""
    out = set()
    for n in names:
        out |= set(re.findall(r"\b[A-Z]{2,6}\b", n or ""))
        words = [w for w in re.findall(r"[A-Za-zÀ-ÿ]+", n or "") if w.lower() not in _SMALL_WORDS]
        if len(words) >= 2 and all(w[0].isupper() for w in words):
            out.add("".join(w[0] for w in words))
    return out


def _orig_key(name: str) -> str:
    """The original name without brackets and the article: “the Commission”/“Commission”,
    “الجهاز (NTRA)”/“الجهاز”."""
    t = re.sub(r"[（(][^）)]*[）)]", "", name or "").strip().lower()
    t = re.sub(r"^(the |l['’]|la |le |les )", "", t)
    return re.sub(r"^ال", "", t).strip()


def _keys_of(zh: str, orig: str = "") -> set[str]:
    """What identifies a body: its normalised Chinese name, its original name without the article,
    and its acronyms (written or the initials of a Latin full name)."""
    keys = {"orig:" + k for k in (_orig_key(orig), _orig_key(zh)) if len(k) >= 3}
    if len(z := _zh_key(zh)) >= 3:
        keys.add("zh:" + z)
    return keys | {"acr:" + a for a in _acronyms(zh, orig)}


async def authority_resolver(session: AsyncSession, cc: str):
    """A function giving the proper name of the body an authority text refers to (“委员会（Commission）”
    → “新加坡个人数据保护委员会 PDPC”); a text naming several bodies or no known one is kept."""
    await _regulators(session, cc)
    known = _KEYS.get(cc, {})

    def resolve(text: str) -> str:
        if not text or re.search(r"[/／；;、]|与|和|或", text):
            return text
        parts = [re.sub(r"[（(][^）)]*[）)]", "", text).strip(), *re.findall(r"[（(]([^）)]*)[）)]", text)]
        for p in parts:
            for k in _keys_of(p, p):
                if k in known:
                    return known[k]
        # 认不出的：外文简称在前、中文在括号里的，把中文放前面（“الهيئة（电信监管机构）”→“电信监管机构（الهيئة）”）
        if len(parts) == 2 and not HAN.search(parts[0]) and HAN.search(parts[1]):
            return f"{parts[1]}（{parts[0]}）"
        return text

    return resolve


async def _regulators(session: AsyncSession, cc: str) -> list[KgNode]:
    """Supervisory bodies named in the laws themselves (a node backed by a clause), one per body:
    the full name, the short form a law uses for it (“the Commission”, “المركز”), its acronym and an
    untranslated original name are the same body. The publishing websites a document was fetched
    from, the legislature and roles not naming a body (“主管机关”“执法机构”) are not regulators."""
    rows = (
        await session.scalars(
            select(KgNode).where(
                KgNode.country_code == cc,
                KgNode.node_type == "REGULATOR",
                KgNode.review_status.notin_(DEAD),
            )
        )
    ).all()
    from sqlalchemy import or_ as sa_or

    all_candidate_cids: set[uuid.UUID] = set()
    for r in rows:
        for raw_cid in (r.attributes or {}).get("evidence_clause_ids") or []:
            try:
                all_candidate_cids.add(uuid.UUID(str(raw_cid)))
            except (ValueError, TypeError):
                continue
    disallowed_reg_cids = (
        {
            str(cid)
            for cid in (
                await session.scalars(
                    select(Clause.id)
                    .join(DocumentVersion, Clause.version_id == DocumentVersion.id)
                    .join(Document, Document.id == DocumentVersion.document_id)
                    .where(
                        Clause.id.in_(all_candidate_cids),
                        sa_or(
                            ~document_available(),
                            ~clause_layout_available(),
                            Document.relevance.in_(("irrelevant", "out_of_scope")),
                            DocumentVersion.legal_status.in_(NOT_CITABLE_STATUSES),
                            DocumentVersion.review_status == "rejected",
                        ),
                    )
                )
            ).all()
        }
        if all_candidate_cids
        else set()
    )
    valid_by_node: dict[uuid.UUID, list[str]] = {}
    nodes = []
    for r in rows:
        raw_ev_cids = (r.attributes or {}).get("evidence_clause_ids") or []
        usable_cids = [str(c) for c in raw_ev_cids if c and str(c) not in disallowed_reg_cids]
        if not usable_cids or (r.attributes or {}).get("merged_into"):
            continue  # 没有可用条文依据的、在图谱里已并入别的节点的不列
        if (r.attributes or {}).get("is_role"):
            continue  # 按条文原句判断只是泛指一种角色（主管机关、执法机构），不是具体机构
        name = (r.name_zh or "").strip()
        if len(name) < 3 or name in ("监管机构", "主管机构", "主管部门"):
            continue
        if any(w in name for w in LEGISLATURE):  # 制定法律的机关、刊登法规的网站不是监管机构
            continue
        valid_by_node[r.id] = usable_cids
        nodes.append(r)
    # 同一机构的不同叫法归为一组（并查集：中文名归一、缩写、原名任一相同即同组）
    parent = list(range(len(nodes)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    owner: dict[str, int] = {}
    _KEYS[cc] = {}
    node_keys = [_keys_of(r.name_zh, r.name_orig or "") for r in nodes]
    for i, keys in enumerate(node_keys):
        for k in keys:
            if k in owner:
                parent[find(i)] = find(owner[k])
            else:
                owner[k] = i
    groups: dict[int, list[KgNode]] = {}
    for i, r in enumerate(nodes):
        groups.setdefault(find(i), []).append(r)
    out = []
    for members in groups.values():
        named = [m for m in members if not UNNAMED_ROLE.search(re.sub(r"[（(][^）)]*[）)]", "", m.name_zh).strip())]
        if not named:
            continue
        # 显示用：有中文名的、带缩写的、带国名的在前，同等时取最简洁的叫法
        named.sort(
            key=lambda m: (
                not re.search(r"[一-鿿]", m.name_zh or ""),
                not _acronyms(m.name_zh),
                not any(w in (m.name_zh or "") for w in _COUNTRY_WORDS),
                len(m.name_zh or ""),
            )
        )
        out.append(named[0])
        merged_cids: list[str] = []
        for m in (named[0], *[x for x in members if x.id != named[0].id]):
            for c in valid_by_node.get(m.id, []):
                if c not in merged_cids:
                    merged_cids.append(c)
        _CLAUSES[named[0].id] = set(merged_cids)
        _PRIMARY_CLAUSE[named[0].id] = merged_cids[0] if merged_cids else ""
        for m in members:
            for k in node_keys[nodes.index(m)]:
                _KEYS.setdefault(cc, {})[k] = named[0].name_zh
        _MENTIONS[named[0].id] = len(_CLAUSES[named[0].id])
    return out


# 每个监管机构（合并各种叫法后）被多少条条文提到：排序用，提到得多的是主要监管机构
_MENTIONS: dict[uuid.UUID, int] = {}
_CLAUSES: dict[uuid.UUID, set[str]] = {}  # 每个监管机构（合并后）被提到的条文
_PRIMARY_CLAUSE: dict[uuid.UUID, str] = {}  # 每个监管机构用于展示的首选可用依据条文
_KEYS: dict[str, dict[str, str]] = {}  # 国家 → 机构的各种叫法 → 正式名称


async def regulator_of_law(session: AsyncSession, cc: str, document_id, *, at_least: int = 1) -> tuple[str, str]:
    """(body, how): the supervisory body the law names most often (all its names counted as one),
    how = "law"; if the law names none, the body most named by the country's laws of the same
    business category (vehicle permits → vehicle laws), how = "category"; else ("", "")."""

    async def best_in(doc_ids) -> str:
        ids = {
            str(c)
            for c in (
                await session.scalars(
                    select(Clause.id).join(DocumentVersion, Clause.version_id == DocumentVersion.id).where(DocumentVersion.document_id.in_(doc_ids))
                )
            ).all()
        }
        best, hits = "", at_least - 1
        for r in await _regulators(session, cc):
            n = len(_CLAUSES.get(r.id, set()) & ids)
            if n > hits:
                best, hits = r.name_zh, n
        return best

    if name := await best_in([document_id]):
        return name, "law"
    doc = await session.get(Document, document_id)
    if doc is not None and doc.business_topic:
        same = (
            await session.scalars(
                select(Document.id).where(
                    Document.country_code == cc, Document.business_topic == doc.business_topic, Document.business_weight != "peripheral"
                )
            )
        ).all()
        if name := await best_in(list(same)):
            return name, "category"
    return "", ""


async def _data_classes(session: AsyncSession, refs: _Refs, cc: str) -> tuple[int, list[Item]]:
    """Data categories with live, verified requirements, grouped by the kind of vehicle data."""
    rows = (
        await session.execute(
            select(KgNode, func.count(KgEdge.id), func.min(cast(KgEdge.evidence_clause_id, String)))
            .join(KgEdge, KgEdge.target_node_id == KgNode.id)
            .where(
                KgNode.country_code == cc,
                KgNode.node_type == "DATA_CATEGORY",
                KgNode.review_status.notin_(DEAD),
                KgEdge.is_structural.is_(False),
                *active_edge_filters(),
            )
            .group_by(KgNode.id)
            .order_by(func.count(KgEdge.id).desc(), KgNode.name_zh)
        )
    ).all()
    groups: dict[str, list] = {}
    for node, cnt, clause_id in rows:
        code = dc.class_of(node) or "other"
        if code == "unrelated":
            continue
        groups.setdefault(code, []).append((node, cnt, clause_id))
    items, total = [], 0
    for code in dc.SHOWN:
        members = groups.get(code) or []
        if not members:
            continue
        names, seen = [], set()
        for node, _, _ in members:
            k = _key(node.name_zh)
            if k not in seen:
                seen.add(k)
                names.append(_plain(node.name_zh))
        ref = None
        for _, _, clause_id in members:  # 第一条依据不可引用（已废止版本等）时取下一条
            if clause_id and (ref := await refs.number(uuid.UUID(clause_id))):
                break
        if ref is None:
            continue
        total += len(names)
        requirements = sum(cnt for _, cnt, _ in members)
        facts = [
            ("包括", "、".join(names[:8]) + (f" 等 {len(names)} 类" if len(names) > 8 else "")),
            ("相关要求", f"{requirements} 项"),
        ]
        items.append(Item(dc.LABEL[code], facts, ref))
    return total, items


async def _add_clauses(session: AsyncSession, docs: dict[str, tuple[str, str]], ids: set) -> None:
    """Map each evidence clause to the (weight, topic) of its document, in ``docs`` itself."""
    ids = {i for i in ids if i and str(i) not in docs}
    if not ids:
        return
    for cid, doc_id in (
        await session.execute(
            select(Clause.id, DocumentVersion.document_id)
            .join(DocumentVersion, Clause.version_id == DocumentVersion.id)
            .where(Clause.id.in_(ids))
        )
    ).all():
        docs[str(cid)] = docs.get(str(doc_id), ("", ""))


async def requirement_pools(
    session: AsyncSession, cc: str
) -> tuple[dict[str, list[KgNode]], dict[str, tuple[str, str]], list[KgNode]]:
    """The country's verified requirements by aspect (跨境、告知同意、存储安全、申报许可、内部管理、处罚),
    each list ranked and with same-named duplicates dropped. Requirements unrelated to a vehicle-data
    business, of peripheral documents or of out-of-scope documents are left out. The country page
    and the compliance topics both read these lists, so their numbers agree."""
    skip = await wp.out_of_scope_documents(session, cc)
    claims = await _live_claims(session, cc, skip)
    # 只关乎与车辆业务无关的数据（如营销电话的谢绝来电登记）的要求不放进汽车数据简报
    off_topic = await _unrelated_names(session, cc)
    claims = [n for n in claims if not _about(n, off_topic)]
    docs = {
        str(i): (w or "", t or "")
        for i, w, t in (
            await session.execute(
                select(Document.id, Document.business_weight, Document.business_topic).where(
                    Document.country_code == cc
                )
            )
        ).all()
    }
    await _add_clauses(session, docs, {n.evidence_clause_id for n in claims if not n.document_id})
    # 与数据处理和车辆业务无关的要求（交通违章、驾驶资格等）、外围文件里的要求不进国家页
    claims = [
        n
        for n in claims
        if ct.closeness_of(n) != "unrelated" and _doc_of(n, docs)[0] != "peripheral"
    ]
    # 依据条文已不能引用（所在版本已废止、草案等）的要求不列：国家页、合规主题都只列能点开依据的
    ids = {n.evidence_clause_id for n in claims if n.evidence_clause_id}
    citable = {
        cid
        for cid, status, rev_status, rel in (
            await session.execute(
                select(
                    Clause.id,
                    DocumentVersion.legal_status,
                    DocumentVersion.review_status,
                    Document.relevance,
                )
                .join(DocumentVersion, Clause.version_id == DocumentVersion.id)
                .join(Document, Document.id == DocumentVersion.document_id)
                .where(
                    Clause.id.in_(ids),
                    document_available(),
                    clause_layout_available(),
                )
            )
        ).all()
        if status not in NOT_CITABLE_STATUSES
        and rev_status != "rejected"
        and (rel or "") not in ("irrelevant", "out_of_scope")
    } if ids else set()
    claims = [n for n in claims if n.evidence_clause_id in citable]
    obligations = [n for n in claims if n.node_type == "OBLIGATION"]
    penalties = [n for n in claims if n.node_type == "PENALTY"]
    penalties += [
        n
        for n in obligations
        if (_attr(n, "penalty_amount") or _aspect(n) == "penalty") and n not in penalties
    ]
    with_amount = [n for n in penalties if _attr(n, "penalty_amount")]
    pools = {
        aspect: _distinct(_rank([n for n in obligations if _aspect(n) == aspect], docs))
        for aspect in ("transfer", "consent", "security", "admission", "governance")
    }
    # 处罚：写明了罚则幅度的在前
    # 处罚：处罚条款本身（写明罚则幅度的在前）排在前面，带罚则的义务其次
    real = [n for n in with_amount if n.node_type == "PENALTY"]
    pools["penalty"] = _distinct(
        _rank(real, docs)
        + _rank([n for n in with_amount if n not in real], docs)
        + _rank([n for n in penalties if n not in with_amount], docs)
    )
    return pools, docs, obligations


async def citation_of(session: AsyncSession, node: KgNode) -> dict | None:
    """Where a requirement of the graph is written — the law's title and the article, read the same
    way the briefing cites it — or None when that article can no longer be cited."""
    refs = _Refs(session)
    n = await refs.number(node.evidence_clause_id)
    if n is None:
        return None
    ref = refs.items[n - 1]
    return {"law": ref.title, "article": ref.label, "document_id": ref.doc_id, "clause_id": ref.clause_id}


async def build(session: AsyncSession, country_code: str) -> tuple[list[dict], dict]:
    from app.knowledge.router import public_doc_type

    cc = country_code.upper()
    name = country_name_zh(cc)
    refs = _Refs(session)
    pools, docs, obligations = await requirement_pools(session, cc)
    entries = await library(session, cc, in_force_only=True)
    docs_total, clauses_total = len(entries), sum(e.articles for e in entries)
    counts: dict[str, int] = {}
    sections: dict[str, tuple[str, list[Item]]] = {}

    # D1 法规与监管机构：法规取自与“法规浏览”同一个范围
    def kind(e):
        return public_doc_type(e.document.document_type, e.document.instrument_kind)

    relations = await relation_counts(session, [e.version.id for e in entries])
    main = main_law(entries, relations)
    weight_rank = {"core": 0, "supporting": 1, "peripheral": 3}
    topic_rank = {k: i for i, (k, _) in enumerate(bt.TOPICS)}
    ranked = sorted(
        [e for e in entries if e.document.business_weight != "peripheral"],
        key=lambda e: (
            e is not main,
            weight_rank.get(e.document.business_weight or "", 2),
            topic_rank.get(e.document.business_topic or "", 9),
            e.document.relevance != "relevant",
            KIND_RANK.get(kind(e), 6),
            -relations.get(e.version.id, 0),
            -e.articles,
        ),
    )
    regulators = await _regulators(session, cc)
    first_clause = {
        r.id: uuid.UUID(_PRIMARY_CLAUSE[r.id])
        for r in regulators
        if _PRIMARY_CLAUSE.get(r.id)
    }
    await _add_clauses(session, docs, set(first_clause.values()))
    # 专门个人数据保护主管机关优先排第一，网络安全/电信/车辆准入机构其次，综合统计/民航等非汽车专项机构排后
    def _reg_authority_tier(name_s: str) -> int:
        if re.search(r"个人数据|个人信息|数据保护|PDPC|INPDP|CNDP", name_s, re.I):
            return 0
        if re.search(r"统计局|统计所|\bINS\b|民航|航空", name_s):
            return 2
        return 1

    regulators.sort(
        key=lambda r: (
            _reg_authority_tier(r.name_zh or ""),
            TOPIC_RANK.get(docs.get(str(first_clause.get(r.id)), ("", ""))[1], 9),
            WEIGHT_RANK.get(docs.get(str(first_clause.get(r.id)), ("", ""))[0], 2),
            not re.search(r"[一-鿿]", r.name_zh or ""),
            -_MENTIONS.get(r.id, 0),
        )
    )
    # 同名文件（不同语种、修订版或转载源）在目录里各算一份，全部完整列出并标明版本或语种序号，确保份数与列表严格一致
    title_totals: dict[str, int] = {}
    for e in ranked:
        t_lbl = _doc_title(e.document)
        title_totals[t_lbl] = title_totals.get(t_lbl, 0) + 1
    title_seen: dict[str, int] = {}
    items = []
    for e in ranked:
        t_lbl = _doc_title(e.document)
        title_seen[t_lbl] = title_seen.get(t_lbl, 0) + 1
        dup_note = (
            f"第 {title_seen[t_lbl]}/{title_totals[t_lbl]} 份文本"
            if title_totals[t_lbl] > 1
            else ""
        )
        items.append(
            Item(
                t_lbl,
                [("原名", e.document.title)] if e.document.title_zh and e.document.title != t_lbl else [],
                None,
                sub=" · ".join(
                    x
                    for x in (
                        "核心法规" if e.document.business_weight == "core" else "",
                        bt.TOPIC_ZH.get(e.document.business_topic or "", ""),
                        KIND_ZH.get(kind(e), "其他文件"),
                        e.document.title if e.document.title_zh else "",
                        f"{e.articles} 条",
                        dup_note,
                    )
                    if x
                ),
                doc_id=str(e.document.id),
            )
        )
    reg_clause_ids = [first_clause.get(r.id) for r in regulators]
    await refs.preload([cid for cid in reg_clause_ids if cid])
    reg_items: list[Item] = []
    for r in regulators:
        primary_cid = first_clause.get(r.id)
        reg_items.append(
            Item(
                _display_regulator_name(r.name_zh),
                [("职能说明", _clip(r.summary or f"{name}法定监管与主管机构", 160))],
                await refs.number(primary_cid) if primary_cid else None,
                sub="监管与主管机构",
            )
        )
    counts["D1"] = docs_total
    sections["D1"] = (
        INTRO["D1"].format(
            name=name,
            n_inst=docs_total,
            n_reg=len(reg_items),
            n_core=sum(1 for e in entries if e.document.business_weight == "core"),
        ),
        items,
    )

    # D2 数据种类
    n_cat, items = await _data_classes(session, refs, cc)
    counts["D2"] = len(items)
    sections["D2"] = (INTRO["D2"].format(name=name, n_cat=n_cat, n_groups=len(items)), items)

    # D3/D4/D5 义务分流（全量展示，卡片数字与抽屉条目数严格一致）
    for code in ("D3", "D4", "D5"):
        pool = pools[ASPECT_OF[code]]
        if code == "D5":  # 这一方面讲“存储、留存、销毁时效”：保存期限、销毁、存储地点的要求排在一般安全措施前面
            pool = sorted(pool, key=lambda n: 0 if _attr(n, "retention_period") or _matches(n, STORAGE_WORDS) else 1)
        claim_list = await _claim_items(refs, pool)
        counts[code] = len(claim_list)
        sections[code] = (
            INTRO[code].format(name=name, n_total=len(claim_list), shown=len(claim_list)),
            claim_list,
        )

    # D6 处罚（全量展示，处罚节点本身排在前面，带罚则的义务其次）
    pen_list = await _claim_items(refs, pools["penalty"], penalty=True)
    counts["D6"] = len(pen_list)
    sections["D6"] = (
        INTRO["D6"].format(name=name, n_total=counts["D6"], shown=counts["D6"]),
        pen_list,
    )

    # D7 准入事项（与“海外属地准入”模块严格同一口径）
    from sqlalchemy import text as _sql_text

    from app.admission.models import AdmissionEntry
    from app.admission.router import (
        SHOWN as ADM_SHOWN,
    )
    from app.admission.router import (
        _basis as _adm_basis,
    )
    from app.admission.router import (
        _entry_out as _adm_entry_out,
    )
    from app.admission.router import (
        _named as _adm_named,
    )
    from app.admission.router import (
        display_authority as _adm_display_authority,
    )
    from app.admission.router import (
        legal_basis as _adm_legal_basis,
    )
    from app.admission.router import (
        user_shown as _adm_user_shown,
    )

    raw_admissions = list(
        (
            await session.scalars(
                select(AdmissionEntry)
                .where(
                    AdmissionEntry.country_code == cc,
                    AdmissionEntry.review_status.in_(ADM_SHOWN),
                    AdmissionEntry.merged_into.is_(None),
                )
                .order_by(AdmissionEntry.category, AdmissionEntry.name_zh)
            )
        ).all()
    )
    step_counts = (
        dict(
            (
                await session.execute(
                    _sql_text(
                        "select entry_id, count(*) from admission_steps where entry_id = any(:ids) group by entry_id"
                    ),
                    {"ids": [e.id for e in raw_admissions]},
                )
            ).all()
        )
        if raw_admissions
        else {}
    )
    adm_outs = await _adm_named(
        session, [_adm_entry_out(e, step_counts.get(e.id, 0)) for e in raw_admissions]
    )
    shown_pairs: list[tuple[AdmissionEntry, dict]] = []
    for e, o in zip(raw_admissions, adm_outs, strict=True):
        b = await _adm_basis(session, e.evidence_clause_id)
        o["laws"], o["guide"] = await _adm_legal_basis(session, e, b)
        if _adm_user_shown(o):
            o["authority_zh"] = _adm_display_authority(o.get("authority_zh") or "", e.country_code)
            shown_pairs.append((e, o))

    basis = dict(
        (
            (cid, (WEIGHT_RANK.get(w or "", 2), topic_rank.get(t or "", 9)))
            for cid, w, t in (
                await session.execute(
                    select(Clause.id, Document.business_weight, Document.business_topic)
                    .join(DocumentVersion, Clause.version_id == DocumentVersion.id)
                    .join(Document, DocumentVersion.document_id == Document.id)
                    .where(Clause.id.in_([e.evidence_clause_id for e, _ in shown_pairs if e.evidence_clause_id]))
                )
            ).all()
        )
    ) if shown_pairs else {}
    shown_pairs.sort(key=lambda pair: (*basis.get(pair[0].evidence_clause_id, (2, 9)), pair[0].name_zh))
    await refs.preload([e.evidence_clause_id for e, _ in shown_pairs if e.evidence_clause_id])
    items = []
    for e, o in shown_pairs:
        steps = int(o.get("step_count") or 0)
        facts = [
            (k, v)
            for k, v in (
                ("受理机构", o.get("authority_zh") or e.authority_zh or ""),
                ("说明", (e.summary_zh or "").strip()),
                ("办理环节", f"{steps} 个" if steps else ""),
            )
            if v
        ]
        items.append(Item(e.name_zh, facts, await refs.number(e.evidence_clause_id)))
    counts["D7"] = len(shown_pairs)
    sections["D7"] = (
        INTRO["D7"].format(name=name, n_total=len(shown_pairs), shown=len(items)),
        items,
    )

    # D8 待办：由 D3/D4/D5/D7 的前几项转成，注明来自哪一方面
    todo: list[Item] = []
    seen_titles: set[str] = set()
    governance = await _claim_items(
        refs, pools["governance"][:3]
    )
    areas = (
        ("市场准入", sections["D7"][1][:3]),
        ("跨境传输", sections["D3"][1][:3]),
        ("告知与同意", sections["D4"][1][:2]),
        ("存储与安全", sections["D5"][1][:2]),
        ("内部管理", governance[:2]),
    )
    # 各方面轮流取：先每个方面最要紧的一件，再各取第二件……，读者一眼看到全貌
    for round_no in range(3):
        for area, picked in areas:
            if round_no >= len(picked):
                continue
            it = picked[round_no]
            if _key(it.title) in seen_titles:
                continue
            seen_titles.add(_key(it.title))
            todo.append(Item(it.title, [("方面", area), *it.facts], it.ref))
    sections["D8"] = (INTRO["D8"], todo)
    counts["D8"] = len(todo)

    # 国家页用的结构化内容：概述三条 + 每个维度的结论句、要点
    law_name = (
        f"《{_clip(re.sub(r'[（(][^）)]*[）)]', '', _doc_title(main.document)), 24)}》"
        if main
        else ""
    )
    reg_name = _clip(_display_regulator_name(regulators[0].name_zh), 32) if regulators else ""
    # 风险警示写出具体后果：“违反‘某义务’可处……”；没有金额的只写处罚名称
    top = sections["D6"][1][0] if sections["D6"][1] else None
    amount = dict(top.facts).get("处罚", "") if top else ""
    top_penalty = (
        (
            f"“{_clip(top.title, 24)}”：{_clip(amount, 34)}"
            if amount and not re.search(r"罚|监禁|拘役|吊销", top.title)
            else _clip(top.title, 34)
        )
        if top
        else ""
    )
    overview = [
        {
            "label": "法律体系",
            "text": (
                f"{name}相关法规 {docs_total} 份"
                + (f"，以{law_name}等为主。" if law_name else "。")
            )
            if docs_total
            else f"暂未收录{name}与数据合规直接相关的法规。",
        },
        {
            "label": "监管核心",
            "text": f"涉及 {len(reg_items)} 个监管机构，如{reg_name}。"
            if reg_name
            else "暂未识别出明确的监管机构。",
        },
        {
            "label": "风险警示",
            "text": f"处罚规定 {counts['D6']} 项，如{top_penalty}。"
            if top_penalty
            else "暂未收录明确的处罚规定。",
        },
    ]
    dimensions = []
    for code, title in EIGHT_DIMENSIONS_SPEC:
        intro, items = sections[code]
        first = items[0].title if items else ""
        if code == "D2":
            first = "、".join(it.title for it in items[:3])
        unit = {"D1": "份法规", "D2": "种数据", "D7": "个事项", "D8": "件事"}.get(code, "项要求")
        dim_payload: dict[str, Any] = {
            "code": code,
            "title": wp.display_title(code, title),
            "total": counts.get(code, 0),
            "unit": unit,
            "headline": (
                (f"先办：{_clip(first, 34)}" if code == "D8" else f"例如：{_clip(first, 34)}")
                if items
                else "暂无直接相关的条文"
            ),
            "intro": intro if items else NONE_FOUND,
            "columns": HEADINGS[code],
            "items": [it.out() for it in items],
        }
        if code == "D1":
            dim_payload["regulators"] = [it.out() for it in reg_items]
        dimensions.append(dim_payload)
    refs_out = [
        {
            "n": r.n,
            "doc_id": r.doc_id,
            "clause_id": r.clause_id,
            "title": r.title,
            "original_title": r.original_title,
            "label": r.label,
            "source": f"《{r.title}》{r.label}",
            "original": r.original,
            "zh": r.zh,
        }
        for r in refs.items
    ]

    # 组装（导出用的版式）
    stamp = wp.datetime.now(wp.UTC).astimezone().strftime("%Y-%m-%d")
    blocks: list[dict] = [
        {"type": "title", "text": f"{name}汽车数据合规洞察简报（图谱整理版）"},
        {
            "type": "paragraph",
            "text": (
                f"依据范围：本平台收录的{name}官方法规{docs_total}份、条文{clauses_total}条；编写日期 {stamp}。"
                "本版由知识图谱中已核验的内容按固定版式排出，不经大模型改写：每一行都给出条文出处，"
                "文末的条文表列出原文摘录和中文。" + wp.LEVEL_NOTE
            ),
        },
    ]
    for index, (code, title) in enumerate(EIGHT_DIMENSIONS_SPEC):
        intro, items = sections[code]
        blocks.append(
            {"type": "heading", "text": f"{NUMERALS[index]}、{wp.display_title(code, title)}"}
        )
        blocks.append({"type": "paragraph", "text": intro if items else NONE_FOUND})
        if items:
            if code == "D1":
                blocks.append(
                    {
                        "type": "table",
                        "head": HEADINGS["D1"],
                        "widths": WIDTHS["D1"],
                        "rows": [
                            [
                                it.title,
                                it.sub or "—",
                                it.facts[0][1] if it.facts else "—",
                            ]
                            for it in items
                        ],
                    }
                )
                if reg_items:
                    blocks.append({"type": "paragraph", "text": "**主要监管与主管机构**"})
                    blocks.append(
                        {
                            "type": "table",
                            "head": ["监管与主管机构", "职能说明", "条文依据"],
                            "widths": [34, 36, 30],
                            "rows": [
                                [
                                    r.title,
                                    r.facts[0][1] if r.facts else "—",
                                    refs.cite(r.ref),
                                ]
                                for r in reg_items
                            ],
                        }
                    )
            else:
                blocks.append(
                    {
                        "type": "table",
                        "head": HEADINGS[code],
                        "widths": WIDTHS[code],
                        "rows": [it.row(refs) for it in items],
                    }
                )
    if refs.items:
        blocks.append({"type": "heading", "text": "参考条文"})
        blocks.append(
            {
                "type": "table",
                "head": ["编号", "法规与条款", "条文摘录（原文）", "中文"],
                "widths": [7, 22, 36, 35],
                "rows": [
                    [
                        f"[{r.n}]",
                        f"《{_clip(r.title, 40)}》{r.label}",
                        _clip(r.original, 200),
                        _clip(r.zh, 200) or "（暂无中文）",
                    ]
                    for r in refs.items
                ],
            }
        )
    return blocks, {
        "counts": counts,
        "documents": docs_total,
        "clauses": clauses_total,
        "clause_refs": len(refs.items),
        "overview": overview,
        "dimensions": dimensions,
        "refs": refs_out,
    }
