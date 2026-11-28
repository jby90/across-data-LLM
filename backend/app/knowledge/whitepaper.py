# ruff: noqa: E501
"""Country white paper: the eight dimensions as a short briefing, one per country, read without
entering any business information (requirement 1.5 / 3.1).

It is a briefing of what the country's own regulations in the library say about each of the eight
dimensions, written from the clauses the same search the assessments use finds for that dimension.
Nothing of a company's own situation goes in: that belongs to the evidence documents and the
assessment, which the 11.04 minutes keep apart from the white paper.

The model writes the paragraphs, under the same rules as the argumentative documents: only the
numbered clauses it is given, every legal statement carrying the numbers of the clauses it rests on
(the program prints the clause table itself), a figure that is in no cited clause refused, internal
numbering never in the text. A white paper is stored per country and version; a new one is a new
version, and ``source_hash`` (the clauses it was written from) tells when the library has moved on.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import DateTime, Integer, String, UniqueConstraint, func, select
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from app.analysis import clause_gate
from app.analysis import document_narrative as nar
from app.analysis.llm import LlmUnavailableError, call_llm_json
from app.analysis.workflow_engine import EIGHT_DIMENSIONS_SPEC, country_name_zh
from app.core.db import Base

PER_DIMENSION = 10
POOL = 60  # candidates per dimension before the model picks the ones that matter
NUMERALS = "一二三四五六七八九十"
# 第八个维度与需求、评估报告同名：“企业合规整改建议与落地执行方案”。此前一度显示为“落地建议”，旧版本读出时改回
DISPLAY_TITLE: dict[str, str] = {}
OLD_TITLES = {"、落地建议": "、企业合规整改建议与落地执行方案"}


def display_title(code: str, title: str) -> str:
    return DISPLAY_TITLE.get(code, title)


_PRIVATE = re.compile("[-]")  # 原文 PDF 里的私用区符号（多是项目符号），字体里没有，显示成方框
_AR_DUAL_PAREN = re.compile(
    r"([\u0600-\u06ff][^（）\n]{2,120}?)\s*\([A-Za-z0-9 ,./'\"\-–—:;]*\b(?:Law|Decree|Act|Decision|Resolution|Regulation)\b[A-Za-z0-9 ,./'\"\-–—:;]*\)"
)
_ELLIPSIS_TAIL = re.compile(r"(\s*(?:……|…|\.{3,}))\s*$")
_CONFIDENTIAL_FOOTER_NOISE = re.compile(
    r"(?:\n|(?<=[。；;！!？?]))\s*(?:私密及机密|机密文件|STRICTLY\s+CONFIDENTIAL|PRIVATE\s+AND\s+CONFIDENTIAL)(?:\s+[A-Z0-9\-]{2,20})?[^\n]*",
    re.IGNORECASE,
)
_DANGLING_BEFORE_ELLIPSIS = re.compile(
    r"(?:[:：—–\-]+|"
    r"[，,、；;：:\s”\"）)]*(?:并且|而且|或者|以及|具体涉及|包括|涵盖)|"
    r"[，,、；;：:\s”\"）)]*(?<![埃涉普提及触危顾波遍企力合吞兼归并参给赐赋授予相交总求饱温柔调附唱应缓中平失让赠尚姑苟暂权施祥谐亲违契])[及和或且并与]|"
    r"[，,、；;：:\s”\"）)]*(?<![A-Za-zÀ-ž])(?:and|or|ou|et)(?![A-Za-zÀ-ž])|"
    r"(?<![\u0600-\u06ff])(?:أو|و)(?![\u0600-\u06ff])|"
    r"(?:^|(?<=[\s（(；;。：:，,、\u4e00-\u9fff]))(?:\d+\.|[a-zA-Z]\.|[一二三四五六七八九十]+、))\s*$",
    re.IGNORECASE,
)


def _tidy_brackets(s: str) -> str:
    s = _PRIVATE.sub("•", s)
    s = _CONFIDENTIAL_FOOTER_NOISE.sub("", s)
    if "Law))" in s or "Law)）" in s or _AR_DUAL_PAREN.search(s):
        s = _AR_DUAL_PAREN.sub(r"\1", s)
        s = re.sub(r"(\b(?:Law|Decree|Act|Decision)\b[^()（）]{0,60})\)\)", r"\1)", s)
    m = _ELLIPSIS_TAIL.search(s)
    if m:
        head = s[: m.start()].rstrip(" 、，,；;：:—–-\n\r\t")
        had_dash_or_colon = bool(re.search(r"(?:——+|—+|[:：])\s*$", s[: m.start()].rstrip()))
        while True:
            trimmed = _DANGLING_BEFORE_ELLIPSIS.sub("", head).rstrip(" 、，,；;：:—–-\n\r\t")
            if trimmed == head or not trimmed:
                break
            head = trimmed
        if had_dash_or_colon:
            last_p = max(head.rfind("。"), head.rfind("！"), head.rfind("？"))
            if last_p >= len(head) // 3:
                stub = head[last_p + 1 :].strip()
                if stub and not re.search(r"[。；;！!？?]$", stub):
                    head = head[: last_p + 1]
        tail = m.group(1).lstrip("\n\r")
        for op, cl in (("《", "》"), ("（", "）"), ("(", ")"), ("“", "”")):
            diff = head.count(op) - head.count(cl)
            if diff > 0:
                head = head + (cl * diff)
        s = head + (tail if tail else "……")
    return s


def _clean(v):
    if isinstance(v, str):
        return _tidy_brackets(v)
    if isinstance(v, list):
        return [_clean(x) for x in v]
    if isinstance(v, dict):
        return {k: _clean(x) for k, x in v.items()}
    return v


def _clean_orig_title(raw_orig: str) -> str:
    orig = re.sub(r"\s*[（(]议会版[）)]", "", str(raw_orig or "").strip()).strip()
    orig = _AR_DUAL_PAREN.sub(r"\1", orig).strip()
    return orig


_LAW_SUMMARY_HEADS = (["法规（中文名）", "原名"], ["中文名称", "原名"], ["中文名", "原名"])
_REF_TABLE_HEADS = (
    ["编号", "法规与条款", "条文摘录"],
    ["编号", "依据", "条款", "条文内容"],
    ["依据", "条款", "条文内容"],
)


def merge_law_origins_into_refs(blocks: list[dict], laws: list[list[str]] | None = None) -> list[dict]:
    """Merge foreign original law names into first occurrence of each law in 参考条文 and drop redundant 引用法规一览."""
    orig_map: dict[str, str] = {}
    for pair in laws or []:
        if len(pair) >= 2:
            zh = str(pair[0] or "").strip().strip("《》")
            orig = _clean_orig_title(pair[1])
            if zh and orig and orig != zh:
                orig_map.setdefault(zh, orig)
    for b in blocks:
        if b.get("type") == "table" and b.get("head") in _LAW_SUMMARY_HEADS:
            for r in b.get("rows") or []:
                if len(r) >= 2:
                    zh = str(r[0] or "").strip().strip("《》")
                    orig = _clean_orig_title(r[1])
                    if zh and orig and orig != zh:
                        orig_map.setdefault(zh, orig)
    sorted_zhs = sorted(orig_map.keys(), key=len, reverse=True)
    annotated: set[str] = set()
    cleaned: list[dict] = []
    for b in blocks:
        if b.get("type") == "heading" and str(b.get("text") or "").strip() == "引用法规一览":
            continue
        if b.get("type") == "table" and b.get("head") in _LAW_SUMMARY_HEADS:
            continue
        if orig_map and b.get("type") == "table" and b.get("head") in _REF_TABLE_HEADS:
            head = b.get("head") or []
            law_col = 1 if (head and head[0] == "编号") else 0
            new_rows: list[list[str]] = []
            for r in b.get("rows") or []:
                r2 = list(r)
                if len(r2) > law_col and isinstance(r2[law_col], str):
                    cell = r2[law_col].strip()
                    for zh in sorted_zhs:
                        orig = orig_map[zh]
                        if cell.startswith(f"《{zh}》"):
                            if zh not in annotated and orig not in cell:
                                r2[law_col] = f"《{zh}》（{orig}）" + cell[len(f"《{zh}》"):]
                            annotated.add(zh)
                            break
                        if cell.startswith(zh):
                            if zh not in annotated and orig not in cell:
                                r2[law_col] = f"{zh}（{orig}）" + cell[len(zh):]
                            annotated.add(zh)
                            break
                new_rows.append(r2)
            b = {**b, "rows": new_rows}
            if head == ["编号", "法规与条款", "条文摘录"]:
                b["widths"] = [8, 32, 60]
        cleaned.append(b)
    return cleaned


def retitle(blocks: list[dict]) -> list[dict]:
    """Stored versions written before the rename show the new name too; private-use symbols from
    the source PDFs are shown as a bullet; redundant 引用法规一览 is merged into 参考条文."""
    out = []
    for b in _clean(blocks):
        if b.get("type") == "heading":
            text = str(b.get("text", ""))
            for old, new in OLD_TITLES.items():
                text = text.replace(old, new)
            text = re.sub(r"^所依据的条文(?:（[^）]*对应此表）)?$", "参考条文", text)
            b = {**b, "text": text}
        elif b.get("type") == "paragraph":
            text = str(b.get("text", ""))
            if text.startswith("**属地准入核心事项一览"):
                b = {**b, "text": "**属地准入事项一览**"}
        out.append(b)
    return merge_law_origins_into_refs(out)
LEVEL_NOTE = (
    "本简报依据本平台收录的该国官方法规编写，每个判断都标注参考条文；"
    "具体业务适用请结合主管机构现行规定核定。"
)

DIMENSION_BRIEF = {
    "D0": (
        "版式要求：写 1 段连贯的学术研究摘要（约 230 至 290 字，points 留空 []）。"
        "参考合规研究论文摘要体例，依次凝练：（1）该国数据治理与汽车相关监管的立法核心及主管机构；"
        "（2）受规制的数据范围及对智能网联运行、远程诊断与OTA、自动驾驶研发测试、整车销售售后与汽车金融出行等全链条汽车业务的覆盖关系；"
        "（3）个人数据向境外接收方跨法域传输的法定路径与属地车辆认证、电信核准、数据处理申报的衔接机制（跨法域传输须中性表述，不预设单一接收国）；"
        "（4）核心法律责任边界。所有论断与数字必须严格依据 clauses 并标注 cites。"
    ),
    "D1": (
        "版式要求：写 2 段连贯的学术综述段落（每段约 230 至 290 字，points 留空 []），按“核心命题—法条机理—汽车业务映射”层次行文，避免逐条机械罗列。"
        "第一段综述该国个人数据与数据治理主干立法体系、适用效力范围及法定主管机构权责配置（机构名称用 clauses 里每条的 authority 规范名）；"
        "第二段阐明配套实施条例及与智能网联汽车、自动驾驶测试、车载通信、道路交通及经销售后相关的网络安全、电信、电子交易法规协同框架。"
    ),
    "D2": (
        "版式要求：写 1 段简明法理总述（不超过 130 字，提炼该国法规的数据分级分类逻辑与规制梯度，不提前罗列各类具体定义）＋ points 列 4 至 5 条分类界定与差异化合规要求"
        "（用 **个人数据**：、**敏感个人数据**：、**车载运行与定位数据**：、**经销售后与客户业务数据**： 等贴合条文的加粗短语领起，严禁与总述重复）。"
        "紧扣条文界定的数据概念及其在智能网联、座舱交互、销售金融与售后出行中的对应形态，只写数据的定义和分类，不写车辆铭牌、标识、号牌这类实物要求。"
    ),
    "D3": (
        "版式要求：写 2 段连贯的法理论证段落（每段约 230 至 290 字，points 留空 []），避免简单条文堆砌。"
        "第一段论证个人数据向境外跨法域传输的基本法定原则、充分保护水平认定或主管机构事先审批与登记备案机制；"
        "第二段剖析法定例外豁免情形、具有法律约束力的合同或集团规则保障及数据主体同意条件，映射车联网云端协同、远程诊断研发及跨国售后客户管理等跨境流转场景。"
        "只写向境外传输的通用规定，境内处理的一般规定不写在这里，也不预设或限定单一接收国。"
    ),
    "D4": (
        "版式要求：写 1 段法理原则概述（不超过 125 字，提炼数据处理合法性基础、透明原则及车载终端、移动应用与经销售后触点的告知同意逻辑）＋ points 列 4 至 6 条具体规则"
        "（分别写明事先或明确同意前提与形式、法定告知事项、同意撤回机制，以及查阅、更正、删除、反对等数据主体权利与法定响应时限，每条用 **加粗短语**： 领起，不与概述重复）。"
        "扣押、调查、取证等程序性条文不属于这一方面。"
    ),
    "D5": (
        "版式要求：写 1 段总体规制论述（不超过 130 字，阐明是否设境内存储要求、存储期限最小化及全生命周期安全保护原则）＋ points 列 4 至 5 条具体规范"
        "（汇总条文中的保存期限与起算日、加密与访问控制等技术组织措施、超期销毁或匿名化要求、数据安全事件通报对象与法定窗口期，用 **加粗短语**： 领起，不与首段重复）。"
        "条文写“自某日起 X 年/个月”的，照条文写明起算日和期限。"
    ),
    "D6": (
        "版式要求：写 1 段追责体系概述（不超过 125 字，阐明行政监管执法与刑事追责双轨框架及主管机构调查检查权，不列具体罚款数额）＋ points 按违法情形逐项列出处罚标准"
        "（先写违反跨境传输、告知同意、留存与安全义务的罚则，再写阻碍执法或未履行申报等违法情形；修订法修改了罚款上限的只写修订后现行上限；固定金额与按年营业额比例的算法都要写全，特定适用范围写明，每条用 **加粗短语**： 领起）。"
    ),
    "D7": (
        "版式要求：写 2 段连贯的制度综述（每段约 220 至 275 字，points 留空 []，程序会在段后自动附上准入事项一览表）。"
        "第一段结合 context 与条文，体系化归纳整车进口与型式批准、车载无线电与电信终端设备认证（含自动驾驶测试许可，如有）的监管分工及前置技术核准逻辑；"
        "第二段归纳个人数据处理事先申报或许可、跨境传输审批与属地法人代表资格、官方递交语言等制度衔接要点，覆盖整车投放、车联网上线及属地营销售后业务，不再逐行照抄表格。"
    ),
    "D8": (
        "版式要求：写 1 句统领总纲段落（不超过 95 字，阐明车企围绕整车准入、智能网联运行及营销售后全业务链统筹属地合规与跨法域数据流转治理）＋ points 列 6 条可执行落地举措，"
        "依次采用自然加粗标题领起（如 **全业务数据盘点与适用核定**：、**属地申报与专员指定**：、**跨法域传输合规保障**：、**多触点告知与权利响应**：、**留存销毁与安全事件通报**：、**整车与通信准入协同**：，严禁使用 ①②③ 序号）。"
        "只能从 context 里维度一到七已经写出的要求和条文推出，刑事罚则条文不能当成准入或申报要求引用，不得引入前面没写的主题；"
        "跨法域传输举措须面向境外接收方（如集团研发中心、区域运营平台或境外云端节点）中性表述，不得默认或限定为向中国传输；条文规定了明确义务或时限的照条文写成要求，未写到的写成建议做法（不写“未规定”）。"
    ),
}

_WHITEPAPER_EXEMPLARS: dict[str, str] = {
    "synthesis": (
        "标杆行文正例示范（参照比较法白皮书与头部律所国别意见书“核心制度命题领起→法条规范要件拆解→汽车全业务链映射”结构，严禁照抄非本节条文，所有机构、法条与数值须完全取自 clauses）："
        "“该国个人数据跨法域流动采取原则限制与法定路径放行相结合的审慎监管模式，专门数据保护法律明确规定数据处理组织在将个人数据移转至境外司法辖区前，必须履行法定前置审查或确保境外接收方承担具有法律强制执行力的同等保护义务。"
        "在汽车产业适用上，上述跨法域合规要求全面覆盖智能网联汽车车载遥测运行、远程故障诊断与软件升级协同、自动驾驶研发测试以及整车出口经销、售后服务和汽车金融业务中的客户档案跨国集中管理场景，要求车企在业务架构设计阶段即将跨法域流转纳入统一问责体系。”"
    ),
    "lead_and_points": (
        "标杆行文正例示范（总述段凝练制度框架，points 逐项展开具体法定要件或可执行举措，严禁总述与分点重复同一规则或数字）："
        "总述示例：“围绕整车准入、智能网联运行及属地营销售后全链条业务，车企须统筹建立本地数据处理合规与跨法域流转内控机制。”"
        "分点示例：“**全业务数据盘点与分类核定**：梳理车载终端、移动应用及经销售后触点采集的个人数据与敏感数据台账，明确区分车端本地闭环数据与确需向境外接收方流转的业务字段。”"
    ),
}

SYSTEM = (
    "你是汽车行业数据合规与比较法领域的资深研究员，参照权威监管指南与头部律所国别法律意见书白皮书体例，为汽车企业合规与法务团队撰写某国法规洞察简报。"
    "行文要求逻辑连贯、层次递进、法理扎实：严格按“核心制度判断领起—法条构成要件与程序期限拆解—汽车全链条业务场景映射（涵盖智能网联运行、远程诊断与OTA、自动驾驶测试、高精定位，以及整车出口经销、售后服务、汽车金融保险、充电出行中的车主与客户数据处理）”展开，彻底杜绝“第X条规定……第Y条规定……”的流水账式条文罗列。"
    "只能使用 clauses 里编号的条文：不得补充条文以外的法律规定、数字、日期或机构名称，也不得凭对该国的印象发挥。"
    "每个涉及法律要求的论断，必须在 cites 里写出参考条文的编号：clauses 里的 n（从 1 起的序号），不是法规的条号（如“第 17 条”的 17）；没有条文依据的不要写成法律要求。"
    "条文没有写到的内容，每个方面最多用一句话带过，并写成建议的做法，不要写“该国未规定”“目前收录的法规未涉及”，也不要猜；建议向某机构确认时，该机构必须是所引条文点名的机构，条文没有点名机构的，不写向谁确认。"
    "clauses 里标了 required 的条文必须引用，并把其中的义务照条文写成要求（谁、做什么、什么期限）。"
    "罚则条文（处监禁、罚金、罚款）只在讲违规处罚的方面引用（摘要除外），其他方面不得把它当作义务条文来写。"
    "同一段落内同一机构第一次出现写中文全称（含英文或法文缩写），同段再次提及时可直接用简称或缩写。"
    "不要写“所给条文”“表述为”“写入方案”“读者”“本简报”“本节”“下文”“下表”“见表”“需结合义务条款确认”这类写作过程的用语。"
    "条文对义务写了明确要求的（报告对象、告知事项、检查权、期限），照条文写成要求，不写成“建议书面确认”。"
    "严格遵循 must_cover 指定的章节版式（paragraphs 段数与 points 条数；指定 points 留空 [] 的章节不得输出 points）。"
    "paragraphs 与 points 严禁重复同一条规则、同一组数字或同一句话：总述段写整体原则与制度框架，points 写具体展开项（可用 **关键词**： 领起，每条不超过 115 字），段落每段不超过 300 字。"
    "正文里不得出现“条文6”“clauses”“cites”这类内部编号或字段名，条文编号只写在 cites 里；"
    "提到某条法律要求时，用法规简称加条号，条号照 clauses 里的 clause 写（写“第19.6段”的不改成“条”），如“《个人数据保护法》第 43 条规定……”，不要写法规的外文全名。"
    "只输出 JSON：{\"paragraphs\":[{\"text\":\"\",\"cites\":[1]}],\"points\":[{\"text\":\"\",\"cites\":[1]}]}。"
)


class CountryWhitepaper(Base):
    __tablename__ = "country_whitepapers"
    __table_args__ = (UniqueConstraint("country_code", "version_no", name="uq_whitepaper_version"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    country_code: Mapped[str] = mapped_column(String(8), index=True)
    version_no: Mapped[int] = mapped_column(Integer)
    blocks: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    source_hash: Mapped[str] = mapped_column(String(64))
    tokens: Mapped[int] = mapped_column(Integer, default=0)
    report: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class WhitepaperError(RuntimeError):
    """A dimension could not be written so that it passes the checks."""


def source_hash(per_dimension: dict[str, list[dict]]) -> str:
    ids = {code: sorted(str(c.get("clause_id")) for c in items) for code, items in per_dimension.items()}
    return hashlib.sha256(json.dumps(ids, sort_keys=True).encode()).hexdigest()


def _number_of(c: dict) -> str:
    """“第(98)条”, “98”, “٩٨” → “98”: one clause number however a document writes it."""
    raw = str(c.get("clause_number") or "").translate(str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789"))
    return re.sub(r"[^0-9A-Za-z]+", "", raw).lower()


def _text_of(c: dict) -> str:
    return re.sub(r"[\s\W_]+", "", str(c.get("quote_zh") or c.get("quote_original") or ""))[:300]


def _dedupe(items: list[dict]) -> list[dict]:
    """The same law is often in the library more than once (an original, translations, re-cuts);
    two clauses with the same number whose texts read mostly alike are one clause to a reader."""
    from difflib import SequenceMatcher

    out: list[dict] = []
    for c in items:
        number, text = _number_of(c), _text_of(c)
        if number and any(
            _number_of(k) == number and SequenceMatcher(None, _text_of(k), text).quick_ratio() > 0.8
            and SequenceMatcher(None, _text_of(k), text).ratio() > 0.55
            for k in out
        ):
            continue
        out.append(c)
    return out


# 各维度条文里值得让挑选环节看到的词（条文开头往往只是一个标题，要点在后面的款项里）
FOCUS = {
    "D1": ("适用", "监管", "委员会", "机构", "scope"),
    "D2": ("定义", "界定", "敏感", "分类", "指"),
    "D3": ("境外", "国外", "外国", "另一国家", "传输", "转移", "跨境", "出境"),
    "D4": ("同意", "告知", "通知", "撤回", "权利", "访问", "更正", "反对"),
    "D5": ("保存", "期限", "销毁", "删除", "存储", "安全措施", "加密"),
    "D6": ("罚", "监禁", "处罚", "制裁", "吊销", "责任"),
    "D7": ("申报", "授权", "许可", "批准", "认证", "登记", "备案", "准入"),
    "D8": ("应当", "必须", "义务", "措施", "审计", "评估"),
}


def _glimpse(code: str, text: str) -> str:
    """The start of the clause plus the passages around the dimension's focus words, so a clause
    whose relevant item is the twelfth of fourteen is still recognised when choosing."""
    text = re.sub(r"\s+", " ", text)
    parts = [text[:110]]
    taken = 0
    for word in FOCUS.get(code, ()):
        i = text.find(word, 110)
        if i >= 0 and taken < 2:
            parts.append("…" + text[max(110, i - 40) : i + 70])
            taken += 1
    return " ".join(parts)


async def out_of_scope_documents(session: AsyncSession, country_code: str) -> set[str]:
    """Documents the library itself marks as not about this subject (its scope check on every
    document, admin relevance override, or source safety hold): kept out of the briefing so an
    out-of-scope or quarantined file is never offered as the country's data-compliance regime."""
    from sqlalchemy import or_

    from app.ingestion.models import Document
    from app.ingestion.safety import document_available
    from app.knowledge.models import KgNode, NodeType

    rows = await session.execute(
        select(KgNode.document_id).where(
            KgNode.country_code == country_code,
            KgNode.node_type == NodeType.INSTRUMENT.value,
            KgNode.attributes["scope"].astext == "out_of_scope",
            KgNode.review_status.notin_(("stale", "rejected", "superseded")),
        )
    )
    excluded = {str(r[0]) for r in rows if r[0]}
    doc_rows = await session.scalars(
        select(Document.id).where(
            Document.country_code == country_code,
            or_(
                ~document_available(),
                Document.relevance.in_(("irrelevant", "out_of_scope")),
            ),
        )
    )
    excluded.update(str(did) for did in doc_rows if did)
    return excluded


async def gather(session: AsyncSession, country_code: str) -> dict[str, list[dict]]:
    """The candidate clauses the library finds for each dimension of this country (no user facts):
    a pool of ``POOL`` per dimension, ranked; ``select_clauses`` then picks the ones that matter."""
    from app.analysis.doc_rules import (
        _NOT_FOR_MAKERS,
        _OTHER_SUBJECT,
        _OUTBOUND,
        _SCENE_ONLY,
        _SCENE_TITLE,
        not_a_basis,
        real_label,
    )
    from app.analysis.documents_router import _resolve_citation_to_current
    from app.analysis.tools import ToolBox
    from app.ingestion.display_text import for_reading
    from app.ingestion.models import Document

    toolbox = ToolBox(session, country_code, cache_scope=None)
    skip = await out_of_scope_documents(session, country_code)
    # 正文和条文表只写法规中文名，外文原名收进文末“引用法规一览”
    zh_titles = {str(i): t for i, t in (await session.execute(select(Document.id, Document.title_zh).where(Document.country_code == country_code, Document.title_zh.is_not(None)))).all()}
    out: dict[str, list[dict]] = {}
    for code, _ in EIGHT_DIMENSIONS_SPEC:
        found: list[dict] = []
        for c in await toolbox.search_effective_clauses(code, top_k=POOL):
            if not c.text.strip() or str(c.document_id) in skip:
                continue
            resolved = await _resolve_citation_to_current(
                session,
                {
                    "clause_id": c.clause_id,
                    "document_id": c.document_id,
                    "document_title": c.document_title,
                    "clause_number": c.label,
                    "quote_original": c.text,
                    "quote_zh": c.translated_text,
                },
            )
            for k in ("quote_original", "quote_zh"):
                resolved[k] = for_reading(str(resolved.get(k) or ""))
            title = zh_titles.get(str(resolved.get("document_id") or "")) or str(resolved.get("document_title") or "")
            label = real_label(
                str(resolved.get("clause_number") or ""),
                str(resolved.get("quote_original") or ""),
                str(resolved.get("quote_zh") or ""),
                title,
                section_path=c.section_path or c.heading or "",
            )
            if not label:
                continue
            resolved["quote_zh"] = clause_gate.chinese_text(resolved.get("quote_zh"), resolved.get("quote_original"))
            if not clause_gate.usable({**resolved, "document_title": title}):
                continue
            resolved["clause_number"] = label
            resolved["document_title"] = clause_gate.law_name(title, resolved.get("document_id"), country_code=country_code)
            if not_a_basis(resolved):  # 立法目的、施行日期、过渡、主管部门自身工作安排：不作依据
                continue
            if code != "D7" and (_SCENE_ONLY.search(str(resolved.get("quote_zh") or "")[:300]) or _SCENE_TITLE.search(title) or _OTHER_SUBJECT.search(str(resolved.get("quote_zh") or "")[:120]) or _NOT_FOR_MAKERS.search(str(resolved.get("quote_zh") or ""))):
                continue  # 视频监控、健康研究、电信运营商义务、营销等与车企无关的：不选
            if code == "D3" and not _OUTBOUND.search(str(resolved.get("quote_zh") or "") + str(resolved.get("quote_original") or "")):
                continue  # 跨境维度只用向境外传输的条文，境内处理的一般规定不进来
            found.append({**resolved, "dimension_code": code})
        out[code] = _dedupe(found)
    await nar.load_official_numbers(session, {c.get("document_id") for items in out.values() for c in items})
    return out


SELECT_SYSTEM = (
    "你为一份汽车数据合规研究简报挑选条文。给你某国法规库里与某个方面相关的候选条文（带编号 n、法规名称、条号、开头一段）。"
    "请挑出能说明该方面“法律要求”的条文，候选足够时选满 6–10 条，按重要程度从高到低排序：先放直接规定该要求的条文，再放相邻的、有助于理解制度衔接的条文（如申报内容或行业规章中涉及该方面的条文）。"
    "优先：个人数据保护、网络与数据安全、跨境传输，以及覆盖智能网联运行、远程诊断与OTA、自动驾驶测试、车载无线电通信、整车经销售后与汽车金融出行等汽车全业务链的主干法律和实施细则；"
    "排除：与数据和汽车合规无关的领域（如一般交通违章、驾校、消费信贷纠纷等），也排除只有目录、标题、页眉页脚的残片。"
    "同一法规的同一问题只留最能说明问题的 1–2 条。只输出 JSON：{\"keep\":[n,...]}。"
)


async def select_clauses(code: str, title: str, pool: list[dict]) -> tuple[list[dict], int]:
    """Up to ``PER_DIMENSION`` clauses from the pool that the model judges to state the requirement
    the aspect is about. The clauses stay real clauses (each one is still cited and checked); only
    which ones are shown is the model's choice. On any failure the ranking's top clauses are used."""
    fallback = pool[:PER_DIMENSION]
    if len(pool) <= PER_DIMENSION:
        return fallback, 0
    menu = [
        {
            "n": i + 1,
            "document": str(c.get("document_title", ""))[:80],
            "clause": str(c.get("clause_number", "")),
            "start": _glimpse(code, str(c.get("quote_zh") or c.get("quote_original") or "")),
        }
        for i, c in enumerate(pool)
    ]
    messages = [
        {"role": "system", "content": SELECT_SYSTEM},
        {"role": "user", "content": json.dumps({"aspect": title, "means": DIMENSION_BRIEF[code], "candidates": menu}, ensure_ascii=False)},
    ]
    try:
        res = await call_llm_json(messages, max_tokens=800, temperature=0.0)
    except LlmUnavailableError:
        return fallback, 0
    tokens = int(getattr(res, "prompt_tokens", 0) or 0) + int(getattr(res, "completion_tokens", 0) or 0)
    keep = res.data.get("keep") if isinstance(res.data, dict) else None
    picked: list[dict] = []
    for n in keep or []:
        if isinstance(n, int) and 1 <= n <= len(pool) and pool[n - 1] not in picked:
            picked.append(pool[n - 1])
    return (picked[:PER_DIMENSION] or fallback), tokens


async def _write_dimension(
    code: str, title: str, country: str, clauses: list[dict], refs: dict, context: str = ""
) -> tuple[list[dict], int, list[str]]:
    limit = 14 if code == "D8" else (12 if code == "D0" else PER_DIMENSION)
    offered_list: list[dict] = []
    for c in clauses[:limit]:
        txt = str(c.get("quote_zh") or "").strip()
        if txt in ("", "(未提供译文)", "（未提供译文）"):
            txt = str(c.get("quote_original") or "").strip()
        offered_list.append(
            {
                "n": len(offered_list) + 1,
                "document": str(c.get("document_title", "")),
                "clause": str(c.get("clause_number", "")),
                "text": txt[: nar.OFFER_CHARS],
            }
        )
    for item, c in zip(offered_list, clauses, strict=False):
        if c.get("authority"):
            item["authority"] = c["authority"]
        if c.get("pinned") and code != "D0":
            item["required"] = True
    offered = {c["n"]: c for c in offered_list}
    if not offered_list:
        text = f"本库目前没有收录该国与“{title}”直接相关的条文，这一方面暂无法编写简报；待相关法规入库后更新。"
        return [{"type": "paragraph", "text": text}], 0, []
    messages = [
        {"role": "system", "content": SYSTEM},
        {
            "role": "user",
            "content": json.dumps(
                {
                    "country": country,
                    "aspect": title,
                    "must_cover": DIMENSION_BRIEF[code],
                    "structural_exemplar": _WHITEPAPER_EXEMPLARS["synthesis" if code in ("D0", "D1", "D3", "D7") else "lead_and_points"],
                    "clauses": offered_list,
                    **({"context": context} if context else {}),
                    **({"note": "有 clauses 规定了按营业额比例计算的罚款上限，必须写出这种算法（比例和适用门槛）"} if code == "D6" and any(re.search(r"营业额|turnover", str(x.get("text"))) for x in offered_list) else {}),
                },
                ensure_ascii=False,
            ),
        },
    ]
    allowed: set[str] = set()
    allowed |= nar.offered_numbers(clauses[:limit], offered_list)
    tokens, dropped = 0, []
    best: list[dict] = []
    best_refs: dict = dict(refs)
    min_chars = 160 if code == "D0" else MIN_CHARS
    max_attempts = 3 if code == "D0" else 2
    for attempt in range(max_attempts):
        try:
            res = await call_llm_json(messages, max_tokens=9000, temperature=0.0)
        except LlmUnavailableError as exc:
            # 输出被截断：条文较多的维度偶有，只重试一次，不让整份简报因此中断
            if attempt == 0 and (
                "truncated" in str(exc)
                or "截断" in str(exc)
                or any(f.get("reason") == "output_truncated" for f in getattr(exc, "failures", []))
            ):
                messages = messages[:2] + [{"role": "user", "content": "上一次输出被截断。请把 paragraphs 控制在 2 段以内、points 控制在 5 条以内，再输出 JSON。"}]
                continue
            raise WhitepaperError(str(exc)) from exc
        data = res.data if isinstance(res.data, dict) else {}
        tokens += int(getattr(res, "prompt_tokens", 0) or 0) + int(getattr(res, "completion_tokens", 0) or 0)
        data = advice_checked(code, data, offered, bool(context and code in ("D0", "D7", "D8")))
        local_refs = dict(refs)
        blocks, why_dropped = _checked_blocks(code, data, offered, allowed, local_refs)
        for d in why_dropped:
            d["title"] = display_title(code, title)
        if _chars(blocks) > _chars(best):
            best, best_refs, dropped = blocks, local_refs, why_dropped
        if _chars(best) >= min_chars or attempt == max_attempts - 1 or not why_dropped:
            break
        # 被拒的段落较多、留下的太少：把被拒原因告诉模型，重写一次
        messages = messages[:2] + [
            {"role": "assistant", "content": json.dumps(data, ensure_ascii=False)[:3000]},
            {
                "role": "user",
                "content": "上一版里有些内容没有通过核对，原因是：" + "；".join(sorted({d["why"] for d in why_dropped})[:6]) + "。请改正后重写："
                f"cites 里只能写 clauses 的编号 n（1 到 {len(offered_list)}），不是法规的条号；数字只能用 clauses 里出现的，不要自己换算；"
                "条文没有写到的内容写成建议的做法，不写“未规定”“未涉及”。仍只输出 JSON。",
            },
        ]
    if not best:
        raise WhitepaperError(f"“{title}”没有得到通过核对的内容（{'; '.join(d['why'] for d in dropped) or '模型无输出'}）")
    refs.update(best_refs)
    return best, tokens, dropped


MIN_CHARS = 450


def _chars(blocks: list[dict]) -> int:
    return sum(len(b.get("text", "")) + sum(len(i) for i in b.get("items", [])) for b in blocks)


def _dropped(code: str, item, why: str, offered: dict) -> dict:
    """A sentence left out of the briefing: what it said, why, and the clauses it cited."""
    text = str(item.get("text") or "").strip() if isinstance(item, dict) else str(item or "")
    cites = [c for c in (item.get("cites") or []) if isinstance(c, int)] if isinstance(item, dict) else []
    return {
        "dimension": code,
        "text": text,
        "why": why,
        "cites": [
            {"document": offered[n]["document"], "clause": offered[n]["clause"], "text": offered[n]["text"][:600]}
            for n in cites if n in offered
        ],
    }


_ADVICE = re.compile(r"[^。；]*建议[^。；]{0,14}(?:就|向)([^。；]{0,70}?)(?:书面)?(?:确认|咨询|询问|核实)[^。；]*[。；]?")
_ADMISSION_ASK = re.compile(r"[；，]?[^；。，]{0,12}其他(准入|申报|许可)?(手续|事项)[^；。]{0,24}(确认|核实|咨询)[^；。]*|[；，]?[^；。，]{0,24}(?:准入|申报|许可|审批)(?:手续|事项|要求|路径)[^；。]{0,30}建议[^；。]{0,6}(?:向|就)[^；。]{0,24}(?:确认|咨询|核实)[^；。]*|[；，]?[^；。，]{0,16}受理机构[^；。]{0,20}建议[^；。]{0,24}(?:确认|核实)[^；。]*")


def _named_in(body: str, offered: dict, cites: list[int]) -> bool:
    """Whether a cited clause names the body: its own proper name for it, its short form or a
    distinctive part of its Chinese name in the clause's text."""
    stems = {m for m in re.findall(r"[A-Z]{3,6}", body or "")}
    stems |= {m for m in re.findall(r"[一-鿿]{4,}", re.sub(r"(委员会|管理局|监管局|保护局|中心|机构|当局|部门)$", "", body or ""))}
    for n in cites:
        c = offered.get(n) or {}
        hay = str(c.get("text") or "") + str(c.get("authority") or "")
        if any(st in hay for st in stems if st):
            return True
    return False


def advice_checked(code: str, data: dict, offered: dict, admission_known: bool) -> dict:
    """“建议向××书面确认”：第一、二方面不写这类话；别的方面，被建议去问的机构必须是所引条文点名的机构；
    准入事项已由准入事项库列明受理机构的（第七、八方面），不再写“准入手续向主管机构确认”。"""
    def fix(item):
        if not isinstance(item, dict):
            return item
        text = str(item.get("text") or "")
        cites = [c for c in (item.get("cites") or []) if isinstance(c, int)]

        def sub(m):
            if code in ("D0", "D1", "D2"):
                return ""
            body = m.group(1)
            return m.group(0) if _named_in(body, offered, cites) else ""

        new = _ADVICE.sub(sub, text)
        if admission_known:
            new = _ADMISSION_ASK.sub("", new)
        new = re.sub(r"[，；]\s*(?=[。；]|$)", "", new)
        new = re.sub(r"([。；])\1+", r"\1", new).strip()
        return {**item, "text": new} if new else None

    out = dict(data)
    out["paragraphs"] = [x for x in (fix(i) for i in data.get("paragraphs") or []) if x]
    out["points"] = [x for x in (fix(i) for i in data.get("points") or []) if x]
    return out


_PENALTY_HEAD = re.compile(r"处[^。；]{0,30}(监禁|罚金|罚款)|(监禁|罚金|罚款)[^。；]{0,30}(不超过|不少于|不得超过)")


def _penalty_clause(c: dict) -> bool:
    head = str(c.get("quote_zh") or "")[:160]
    return bool(_PENALTY_HEAD.search(head)) and not re.search(r"应当|必须|不得[^。；]{0,10}(传输|转移|处理)", head[:60])


def _wrong_body(text: str, cites: list[int], offered: dict) -> str:
    """A body named in the sentence that none of the cited clauses names ("" if all match)."""
    named = {offered[n].get("authority") for n in cites if n in offered and offered[n].get("authority")}
    every = {c.get("authority") for c in offered.values() if c.get("authority")}
    for body in every - named:
        short = re.findall(r"[A-Z]{3,6}", body or "")
        if body and (body in text or any(s in text for s in short)) and not any(s in (n or "") for n in named for s in short or [body]):
            return body
    return ""


_EXCLUSIVE_LAW_SIGNS = (
    ("EG", re.compile(r"2020\s*年第\s*151\s*号")),
    ("MA", re.compile(r"第?\s*09[-—–]08\s*号")),
    ("TN", re.compile(r"2004\s*年第\s*63\s*号")),
    ("LB", re.compile(r"2018\s*年第\s*81\s*号")),
    ("SG", re.compile(r"2012\s*年个人数据保护法")),
    ("EU", re.compile(r"2016/679|\bGDPR\b")),
    ("BR", re.compile(r"13[.,]709\s*号|\bLGPD\b")),
    ("TR", re.compile(r"6698\s*号|\bKVKK\b")),
    ("RU", re.compile(r"152-FZ|152-ФЗ", re.I)),
    ("ZA", re.compile(r"\bPOPIA\b")),
    ("CA", re.compile(r"\bPIPEDA\b")),
    ("MY", re.compile(r"709\s*号法")),
    ("TH", re.compile(r"佛历\s*2562|B\.?E\.?\s*2562", re.I)),
)
_CHINA_BIAS = re.compile(r"向中国|回传中国|传回中国|发往中国|传至中国|中国总部")
_BOLD_LEAD = re.compile(r"^\s*\*\*[^*]{1,24}\*\*\s*[：:]\s*")


def _grams4(s: str) -> set[str]:
    plain = re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]", "", _BOLD_LEAD.sub("", s or ""))
    return {plain[i : i + 4] for i in range(len(plain) - 3)} if len(plain) >= 14 else set()


def _fifth_gate(text: str, offered_blob: str) -> str:
    for _, pat in _EXCLUSIVE_LAW_SIGNS:
        if pat.search(text) and not pat.search(offered_blob):
            return "混入了其他法域的法规编号或名称"
    if _CHINA_BIAS.search(text) and "中国" not in offered_blob:
        return "预设了单一传输接收国（应面向境外接收方中性表述）"
    return ""


def _checked_blocks(code: str, data: dict, offered: dict, allowed: set[str], refs: dict) -> tuple[list[dict], list[dict]]:
    blocks: list[dict] = []
    dropped: list[dict] = []
    offered_blob = " ".join(f"{c.get('document', '')} {c.get('text', '')}" for c in offered.values())
    para_chars = 0
    seen_grams: set[str] = set()
    for item in data.get("paragraphs") or []:
        ok, why = nar._check_paragraph(item, offered, allowed)
        if ok is not None and (body := _wrong_body(ok["text"], ok["cites"], offered)):
            ok, why = None, f"写的受理机构“{body}”不是所引条文点名的机构"
        if ok is not None and (gate_err := _fifth_gate(ok["text"], offered_blob)):
            ok, why = None, gate_err
        if ok is None:
            dropped.append(_dropped(code, item, why, offered))
            continue
        para_chars += len(ok["text"])
        seen_grams |= _grams4(ok["text"])
        blocks.append({"type": "paragraph", "text": ok["text"] + nar._cite_line(ok["cites"], offered, refs)})
    points = []
    if not (code in ("D0", "D1", "D3", "D7") and para_chars >= 80):
        for item in data.get("points") or []:
            ok, why = nar._check_paragraph(item, offered, allowed)
            if ok is not None and (body := _wrong_body(ok["text"], ok["cites"], offered)):
                ok, why = None, f"写的受理机构“{body}”不是所引条文点名的机构"
            if ok is not None and (gate_err := _fifth_gate(ok["text"], offered_blob)):
                ok, why = None, gate_err
            if ok is None:
                dropped.append(_dropped(code, item, why, offered))
                continue
            pt_text = ok["text"]
            if code == "D8":
                pt_text = re.sub(r"^[①②③④⑤⑥⑦⑧⑨⑩]\s*", "", pt_text).strip()
                if not pt_text.startswith("**") and (m_lead := re.match(r"^([\u4e00-\u9fff]{3,10})([：:])", pt_text)):
                    pt_text = f"**{m_lead.group(1)}**{m_lead.group(2)}{pt_text[m_lead.end():]}"
            pt_grams = _grams4(pt_text)
            if pt_grams and seen_grams and len(pt_grams & seen_grams) / len(pt_grams) >= 0.5:
                continue
            seen_grams |= pt_grams
            points.append(pt_text + nar._cite_line(ok["cites"], offered, refs))
    if points:
        blocks.append({"type": "list", "items": points})
    return blocks, dropped


async def library_stats(session: AsyncSession, country_code: str) -> tuple[int, int]:
    """(documents, articles) of the country's library as users see it — the same set and the same
    counting as the regulation browser (library_view)."""
    from app.knowledge.library_view import library

    entries = await library(session, country_code)
    return len(entries), sum(e.articles for e in entries)


def renumber_citations(
    body: list[dict],
    refs: dict[tuple[str, str], tuple[int, str]],
    country_code: str,
) -> tuple[list[dict], list[list[str]], set[str]]:
    """Renumber [n] citations in top-to-bottom first-appearance order so body and 参考条文 read [1]..[k]."""
    old_by_n: dict[int, tuple[str, str, str]] = {}
    for (doc, clause), (n, text) in refs.items():
        uni_doc = clause_gate.unify_law_names(doc, country_code=country_code)
        old_by_n[n] = (uni_doc, clause, text)
    if not old_by_n:
        return body, [], set()

    canon_n: dict[tuple[str, str], int] = {}
    alias_old: dict[int, int] = {}
    for n in sorted(old_by_n):
        uni_doc, clause, _ = old_by_n[n]
        ckey = (uni_doc, nar._clause_label(clause))
        primary = canon_n.setdefault(ckey, n)
        alias_old[n] = primary

    remap: dict[int, int] = {}
    cite_re = re.compile(r"\[(\d+)\]")
    cluster_re = re.compile(r"(?:\[\d+\])+")

    def _scan_text(s: str) -> None:
        for m in cite_re.finditer(s or ""):
            val = int(m.group(1))
            if val in alias_old:
                prim = alias_old[val]
                if prim not in remap:
                    remap[prim] = len(remap) + 1

    for b in body:
        if "text" in b and isinstance(b["text"], str):
            _scan_text(b["text"])
        for it in b.get("items") or []:
            if isinstance(it, str):
                _scan_text(it)

    if not remap:
        for n in sorted(set(alias_old.values())):
            remap[n] = len(remap) + 1

    def _rewrite_cluster(m: re.Match) -> str:
        nums = []
        for sub in cite_re.finditer(m.group(0)):
            val = int(sub.group(1))
            if val in alias_old and alias_old[val] in remap:
                nums.append(remap[alias_old[val]])
            else:
                nums.append(val)
        dedup_sorted = sorted(dict.fromkeys(nums))
        return "".join(f"[{x}]" for x in dedup_sorted)

    def _rewrite_text(s: str) -> str:
        return cluster_re.sub(_rewrite_cluster, s) if s else s

    new_body: list[dict] = []
    for b in body:
        nb = dict(b)
        if "text" in nb and isinstance(nb["text"], str):
            nb["text"] = _rewrite_text(nb["text"])
        if "items" in nb and isinstance(nb["items"], list):
            nb["items"] = [_rewrite_text(x) if isinstance(x, str) else x for x in nb["items"]]
        new_body.append(nb)

    rows: list[list[str]] = []
    cited_docs: set[str] = set()
    for prim_old, new_idx in sorted(remap.items(), key=lambda kv: kv[1]):
        uni_doc, clause, text = old_by_n[prim_old]
        cited_docs.add(uni_doc)
        rows.append([f"[{new_idx}]", f"{uni_doc} {nar._clause_label(clause)}".strip(), clause_gate.cut(text)])
    return new_body, rows, cited_docs


def _merge_local_refs(blocks: list[dict], local_refs: dict, global_refs: dict) -> list[dict]:
    if not local_refs:
        return blocks
    idx_map: dict[int, int] = {}
    for key, (local_idx, txt) in sorted(local_refs.items(), key=lambda kv: kv[1][0]):
        global_refs.setdefault(key, (len(global_refs) + 1, txt))
        idx_map[local_idx] = global_refs[key][0]
    cite_re = re.compile(r"\[(\d+)\]")
    cluster_re = re.compile(r"(?:\[\d+\])+")

    def _rw_cluster(m: re.Match) -> str:
        nums = [idx_map.get(int(s.group(1)), int(s.group(1))) for s in cite_re.finditer(m.group(0))]
        return "".join(f"[{x}]" for x in sorted(dict.fromkeys(nums)))

    def _rw(s: str) -> str:
        return cluster_re.sub(_rw_cluster, s) if s else s

    out: list[dict] = []
    for b in blocks:
        nb = dict(b)
        if "text" in nb and isinstance(nb["text"], str):
            nb["text"] = _rw(nb["text"])
        if "items" in nb and isinstance(nb["items"], list):
            nb["items"] = [_rw(x) if isinstance(x, str) else x for x in nb["items"]]
        out.append(nb)
    return out


async def write_whitepaper(session: AsyncSession, country_code: str) -> tuple[list[dict], dict]:
    """Blocks of the whole briefing and the data to store with it."""
    name = country_name_zh(country_code)
    await clause_gate.load_title_names(session)
    per_dim = await gather(session, country_code)
    docs, clauses = await library_stats(session, country_code)
    refs: dict[tuple[str, str], tuple[int, str]] = {}
    body: list[dict] = []
    tokens, dropped = 0, []
    chosen_of: dict[str, list[dict]] = {}
    written: dict[str, str] = {}
    pins = await pinned_topics(session, country_code)
    admission_list = await admission_context(session, country_code)
    await name_authorities(session, country_code, per_dim)

    first_seven = [(idx, code, title) for idx, (code, title) in enumerate(EIGHT_DIMENSIONS_SPEC) if code != "D8"]
    picked_list = await asyncio.gather(*(select_clauses(code, title, per_dim[code]) for _, code, title in first_seven))

    prep: dict[str, tuple[int, str, list[dict], int, dict | None]] = {}
    for (_, code, title), (chosen, picked_tokens) in zip(first_seven, picked_list, strict=True):
        turnover_clause = None
        extra = {
            "D1": pins.get("dpo", []),
            "D5": pins.get("breach", []),
            "D6": pins.get("financial_penalty", []) + pins.get("transfer_penalty", []),
            "D4": pins.get("consent", []) + pins.get("notice", []),
            "D3": pins.get("transfer_basis", []) + pins.get("transfer_exception", []),
            "D7": pins.get("declaration", []),
        }.get(code, [])
        chosen = [c for c in extra if c not in chosen][: 4 if code == "D3" else (3 if code == "D4" else 2)] + chosen
        if code == "D4":
            chosen = [c for c in chosen if re.search(r"同意|告知|通知|撤回|权利|查阅|更正|删除|反对|知情", str(c.get("quote_zh") or "")[:400])]
        if code != "D6":
            chosen = [c for c in chosen if not _penalty_clause(c)]
        chosen = chosen[:PER_DIMENSION]
        if code == "D6":
            caps = await turnover_caps(session, country_code, chosen)
            turnover_clause = caps[0] if caps else None
            chosen = superseded_dropped(chosen)
        idx = next(i for i, c, _ in first_seven if c == code)
        prep[code] = (idx, title, chosen, picked_tokens, turnover_clause)

    async def _run_dim(code: str, title: str, chosen: list[dict], ctx: str) -> tuple[list[dict], int, list[str], dict]:
        lrefs: dict = {}
        blks, used_t, dr_list = await _write_dimension(code, title, name, chosen, lrefs, ctx)
        return blks, used_t, dr_list, lrefs

    dim_results = await asyncio.gather(
        *(
            _run_dim(code, prep[code][1], prep[code][2], admission_list if code == "D7" else "")
            for _, code, _ in first_seven
        )
    )
    d3_tail = await to_china(session, country_code)
    d7_table = await admission_table(session, country_code)

    for (_, code, _), (blocks, used, dr, lrefs) in zip(first_seven, dim_results, strict=True):
        idx, title, chosen, picked_tokens, turnover_clause = prep[code]
        blocks = _merge_local_refs(blocks, lrefs, refs)
        if code == "D6" and turnover_clause:
            base48 = next((c for c in chosen if re.search(r"48J|财务处罚", str(c.get("clause_number", "")) + str(c.get("quote_zh") or "")[:80])), None)
            blocks = turnover_point(blocks, [turnover_clause], refs, base48, country_code=country_code)
        if code == "D7":
            blocks = blocks + d7_table
        chosen_of[code] = chosen
        written[code] = " ".join(b.get("text", "") + " ".join(b.get("items", [])) for b in blocks)
        tokens += used + picked_tokens
        dropped += dr
        body.append({"type": "heading", "text": f"{NUMERALS[idx]}、{display_title(code, title)}"})
        body.extend(blocks)
        if code == "D3":
            body.extend(d3_tail)

    d8_idx, (_, d8_title) = 7, EIGHT_DIMENSIONS_SPEC[7]
    d8_chosen: list[dict] = []
    for k in ("D1", "D3", "D4", "D5", "D6", "D7"):
        d8_chosen += chosen_of.get(k, [])[: 3 if k != "D1" else 2]
    d8_chosen += [
        c
        for c in pins.get("dpo", []) + pins.get("breach", []) + pins.get("declaration", []) + pins.get("notice", []) + pins.get("consent", [])
        if c not in d8_chosen
    ]
    d8_chosen = [c for c in d8_chosen if (str(c.get("document_title", "")), str(c.get("clause_number", ""))) in refs]
    d8_ctx = "\n".join(f"【{k}】{written.get(k, '')}" for k in ("D1", "D2", "D3", "D4", "D5", "D6", "D7"))[:8000] + ("\n" + admission_list if admission_list else "")
    chosen_of["D8"] = d8_chosen

    want_abstract = sum(1 for v in chosen_of.values() if v) == 8 and docs >= 5
    abs_clauses: list[dict] = []
    if want_abstract:
        for k in ("D1", "D2", "D3", "D5", "D6", "D7"):
            for c in chosen_of.get(k, [])[:2]:
                if (str(c.get("document_title", "")), str(c.get("clause_number", ""))) in refs and c not in abs_clauses:
                    abs_clauses.append(c)

    if abs_clauses:
        abs_ctx = "\n".join(f"【{k}】{written.get(k, '')[:500]}" for k in ("D1", "D2", "D3", "D5", "D6", "D7"))
        (d8_blocks, d8_used, d8_dr, d8_lrefs), (abs_blocks, abs_used, abs_dr, abs_lrefs) = await asyncio.gather(
            _run_dim("D8", d8_title, d8_chosen, d8_ctx),
            _run_dim("D0", "摘要", abs_clauses[:12], abs_ctx),
        )
        d8_blocks = _merge_local_refs(d8_blocks, d8_lrefs, refs)
        abs_blocks = _merge_local_refs(abs_blocks, abs_lrefs, refs)
        tokens += d8_used + abs_used
        dropped += d8_dr + abs_dr
        body.append({"type": "heading", "text": f"{NUMERALS[d8_idx]}、{display_title('D8', d8_title)}"})
        body.extend(d8_blocks)
        body = [{"type": "heading", "text": "摘要"}, *abs_blocks, *body]
    else:
        d8_blocks, d8_used, d8_dr, d8_lrefs = await _run_dim("D8", d8_title, d8_chosen, d8_ctx)
        d8_blocks = _merge_local_refs(d8_blocks, d8_lrefs, refs)
        tokens += d8_used
        dropped += d8_dr
        body.append({"type": "heading", "text": f"{NUMERALS[d8_idx]}、{display_title('D8', d8_title)}"})
        body.extend(d8_blocks)
    stamp = datetime.now(UTC).astimezone().strftime("%Y-%m-%d")
    law_date = await _law_updated_date(session, country_code)
    date_part = f"法规最后更新 {law_date}，简报汇编日期 {stamp}" if law_date else f"简报汇编日期 {stamp}"
    head = [
        {"type": "title", "text": f"{name}汽车数据合规洞察简报"},
        {
            "type": "paragraph",
            "text": f"依据范围：本平台收录的{name}官方法规{docs}份、条款{clauses}条（{date_part}）。{LEVEL_NOTE}",
        },
    ]
    body = await one_name_per_body(session, country_code, body)
    body, ref_rows, cited_docs = renumber_citations(body, refs, country_code)
    tail: list[dict] = []
    if ref_rows:
        laws = await law_names(session, country_code, cited_docs | {doc for doc, _ in refs})
        tail = merge_law_origins_into_refs(
            [
                {"type": "heading", "text": "参考条文"},
                {
                    "type": "table",
                    "head": ["编号", "法规与条款", "条文摘录"],
                    "widths": [8, 32, 60],
                    "rows": ref_rows,
                },
            ],
            laws,
        )
    from app.knowledge.whitepaper_router import country_lib_stamp

    return head + body + tail, {
        "source_hash": source_hash(per_dim),
        "tokens": tokens,
        "report": {
            "dropped": dropped,
            "documents": docs,
            "clauses": clauses,
            "lib_stamp": await country_lib_stamp(session, country_code),
        },
    }


async def turnover_caps(session: AsyncSession, country_code: str, chosen: list[dict]) -> list[dict]:
    """Up to two current clauses of the country's personal-data laws that cap a fine by a share of
    turnover, when none of the chosen clauses does."""
    from sqlalchemy import text as sql

    from app.analysis.doc_rules import _SQL_ELIGIBLE_DOC_VER_CLAUSE, real_label
    from app.ingestion.display_text import for_reading

    have = {str(c.get("clause_id")) for c in chosen}
    rows = (
        await session.execute(
            sql(
                f"""select k.id::text, d.id::text, coalesce(d.title_zh, d.title), k.clause_number, k.original_text, t.translated_text
                   from clauses k join document_versions v on v.id=k.version_id and v.state='parsed' join documents d on d.id=v.document_id
                   join clause_translations t on t.clause_id=k.id and t.is_current
                     and t.quality_check_passed and t.review_status not in ('translation_failed','not_translated','rejected','stale','superseded')
                     and not exists (
                       select 1 from clause_translations t2
                       where t2.clause_id=k.id and t2.id<>t.id and t2.is_current
                         and t2.quality_check_passed and t2.review_status not in ('translation_failed','not_translated','rejected','stale','superseded')
                     )
                   where d.country_code=:c and d.relevance in ('relevant', 'related')
                     and coalesce(d.title_zh, d.title) ~ '个人数据|个人信息|数据保护' and coalesce(d.title_zh, d.title) !~ '指南|指引|咨询'
                     and t.translated_text ~ '(罚款|罚金)' and t.translated_text ~ '营业额' and t.translated_text ~ '([0-9]+ ?%|百分之)'
                     {_SQL_ELIGIBLE_DOC_VER_CLAUSE}
                   order by length(t.translated_text) limit 2"""
            ),
            {"c": country_code},
        )
    ).all()
    out = []
    gone = clause_gate.superseded_titles(sorted({r[2] for r in rows if r[2]}))
    for cid, did, title, number, original, zh in rows:
        label = real_label(number, original, zh)
        if label and cid not in have and title not in gone:  # 罚款上限按营业额算的，只有修订法里有的，也要取（修订条文本身就是现行上限）
            out.append({"clause_id": cid, "document_id": did, "document_title": clause_gate.law_name(title, did, country_code=country_code), "clause_number": label,
                        "quote_original": for_reading(original or ""), "quote_zh": for_reading(zh or ""), "dimension_code": "D6"})
    return out


_TOPICS = {
    "dpo": r"(数据保护官|数据保护负责人|个人数据保护(责任人|负责人)|数据保护专员)[^。]{0,40}(任命|指定|登记)|(任命|指定)[^。]{0,30}(数据保护官|数据保护负责人|个人数据保护(责任人|负责人))",
    "breach": r"(泄露|违规|侵害|数据安全事件)[^。]{0,90}(通知|报告|通报)[^。]{0,60}(小时|日|天|立即)|不迟于[^。]{0,40}(日历日|工作日|小时)[^。]{0,40}(通知|报告|通报)|(通知|报告|通报)[^。]{0,60}不迟于[^。]{0,40}(日历日|工作日|小时)",
    "transfer_penalty": r"(罚款|罚金|监禁)",
    "transfer_basis": r"(法律上可强制执行|具有法律强制执行力|可强制执行)[^。]{0,60}义务|约束性公司规则|特定认证|标准合同条款",
    "financial_penalty": r"要求[^。]{0,40}缴付财务处罚",
    "transfer_exception": r"作为[^。]{0,24}例外[^。]{0,60}同意",
    "declaration": r"个人数据处理[^。]{0,90}操作[^。]{0,20}(均须|应|须|必须)[^。]{0,20}(事先)?(申报|声明)|履行以下程序[：:][\s\S]{0,60}事先授权|(须|应|必须)[^。]{0,20}(事先)?(申报|声明)[^。]{0,40}(应当|须|必须)[^。]{0,30}(递交|提交|提出)",
    "notice": r"(必须|应当|须)[^。]{0,40}(告知|通知)[^。]{0,40}下列事项",
    "consent": r"(明确|事先|书面)?同意[^。]{0,40}(处理|收集)|(处理|收集)[^。]{0,40}(须|应|必须)[^。]{0,20}同意",
}

_PINNED_TOPICS_CACHE: dict[str, dict[str, list[dict]]] = {}


async def pinned_topics(session: AsyncSession, country_code: str) -> dict[str, list[dict]]:
    """For obligations a law states outright, the clause that states them (the principal data
    protection laws first, shortest matching clause): so the briefing writes the obligation, not
    “建议书面确认”."""
    from sqlalchemy import text as sql

    from app.analysis.doc_rules import (
        _AMENDING_TITLE,
        _OUTBOUND,
        _SQL_ELIGIBLE_DOC_VER_CLAUSE,
        not_a_basis,
        real_label,
    )
    from app.ingestion.display_text import for_reading
    from app.knowledge.cache_sync import keeper_active, rebuilding, use_cached

    s_cache = session.info.setdefault("_pinned_topics", {})
    if country_code in s_cache and not rebuilding.get():
        return {k: [dict(x) for x in v] for k, v in s_cache[country_code].items()}
    if use_cached(country_code in _PINNED_TOPICS_CACHE):
        return {k: [dict(x) for x in v] for k, v in _PINNED_TOPICS_CACHE[country_code].items()}

    rows = (
        await session.execute(
            sql(
                f"""select k.id::text, d.id::text, coalesce(d.title_zh, d.title), k.clause_number, k.original_text, t.translated_text
                   from clauses k join document_versions v on v.id=k.version_id and v.state='parsed' join documents d on d.id=v.document_id
                   join clause_translations t on t.clause_id=k.id and t.is_current
                     and t.quality_check_passed and t.review_status not in ('translation_failed','not_translated','rejected','stale','superseded')
                     and not exists (
                       select 1 from clause_translations t2
                       where t2.clause_id=k.id and t2.id<>t.id and t2.is_current
                         and t2.quality_check_passed and t2.review_status not in ('translation_failed','not_translated','rejected','stale','superseded')
                     )
                   where d.country_code=:c and d.relevance='relevant' and coalesce(d.title_zh, d.title) ~ '个人数据|个人信息|数据保护'
                     {_SQL_ELIGIBLE_DOC_VER_CLAUSE}"""
            ),
            {"c": country_code},
        )
    ).all()
    out: dict[str, list[dict]] = {}
    gone = clause_gate.superseded_titles(sorted({r[2] for r in rows if r[2]}))
    rows = [r for r in rows if r[2] not in gone]
    for topic, pat in _TOPICS.items():
        hits = []
        for cid, did, title, number, original, zh in rows:
            zh = clause_gate.chinese_text(zh, original)
            if not re.search(pat, zh or "") or (topic in ("transfer_penalty", "transfer_exception") and not _OUTBOUND.search(zh)) or not_a_basis({"quote_zh": zh}) or _AMENDING_TITLE.search(title or "") or re.search(r"^\s*(第[^条]{1,8}条\s*)?[：:]?\s*(在?本(部分|法|条例|章)[^。]{0,6}中，|本部分定义)|定义|释义", (zh or "")[:30]) or (topic != "transfer_penalty" and _penalty_clause({"quote_zh": zh})):
                continue
            label = real_label(number, original, zh, title)
            if not label or clause_gate.why_unusable(zh, title, original):
                continue
            main = 0 if not re.search(r"条例|法令|决定|规章|指南|regulation", title or "") else (2 if re.search(r"指南|指引", title or "") else 1)
            explicit_deadline = (
                0
                if (
                    topic != "breach"
                    or re.search(r"[0-9一二三四五六七八九十]+\s*个?(?:日历|工作)?(?:日|天|小时)", zh or "")
                )
                else 1
            )
            hits.append((main, explicit_deadline, re.search(r"原文|旧版", title or "") is not None, len(zh), {
                "clause_id": cid, "document_id": did, "document_title": clause_gate.law_name(title, did, country_code=country_code), "clause_number": label,
                "quote_original": for_reading(original or ""), "quote_zh": for_reading(zh or ""), "dimension_code": "D8"}))
        hits.sort(key=lambda h: h[:4])
        out[topic] = [{**h[4], "pinned": True} for h in hits[: 3 if topic == "transfer_basis" else 1]]
    s_cache[country_code] = out
    if keeper_active or rebuilding.get():
        while len(_PINNED_TOPICS_CACHE) >= 24:
            _PINNED_TOPICS_CACHE.pop(next(iter(_PINNED_TOPICS_CACHE)), None)
        _PINNED_TOPICS_CACHE[country_code] = out
    return {k: [dict(x) for x in v] for k, v in out.items()}


async def name_authorities(session: AsyncSession, country_code: str, per_dim: dict[str, list[dict]]) -> None:
    """Each candidate clause gets the proper name of the body it names (one name per body)."""
    from app.knowledge import whitepaper_graph as W

    nodes = await W._regulators(session, country_code)
    by_clause: dict[str, str] = {}
    for n in nodes:
        for cid in W._CLAUSES.get(n.id, set()):
            by_clause.setdefault(cid, n.name_zh)
    for items in per_dim.values():
        for c in items:
            if by_clause.get(str(c.get("clause_id"))):
                c["authority"] = by_clause[str(c["clause_id"])]


def _collapse_repeated_authorities(text: str, forms: list[tuple[re.Pattern, str, str]], cname: str = "") -> str:
    """Within a single paragraph or bullet item, keep the first full canonical mention of an
    authority ('中文全称（ABBR）') and shorten 2nd+ repetitions to its abbreviation or short name."""
    if not text:
        return text
    out = text
    seen_proper: set[str] = set()
    for _, proper, _ in forms:
        if not proper or proper in seen_proper or out.count(proper) < 2:
            continue
        seen_proper.add(proper)
        m_abbr = re.search(r"（([A-Z][A-Za-z0-9\-]{1,8})）$", proper)
        short = ""
        if m_abbr and len(proper) >= 9:
            short = m_abbr.group(1)
        elif cname and proper.startswith(cname) and len(proper) - len(cname) >= 5:
            short = proper[len(cname) :]
        if not short or short == proper:
            continue
        first_idx = out.find(proper)
        if first_idx >= 0:
            head_end = first_idx + len(proper)
            out = out[:head_end] + out[head_end:].replace(proper, short)
    return out


async def one_name_per_body(
    session: AsyncSession,
    country_code: str,
    blocks: list[dict],
    law_countries: str | set[str] | list[str] | tuple[str, ...] | None = None,
) -> list[dict]:
    """Every way the briefing names a body becomes the body's one proper name (with 2nd+ mentions
    within the same paragraph shortened cleanly)."""
    from app.knowledge import whitepaper_graph as W

    await W._regulators(session, country_code)
    variants = {}
    from app.knowledge.models import KgNode

    rows = (await session.execute(select(KgNode.name_zh).where(KgNode.country_code == country_code, KgNode.node_type == "REGULATOR"))).scalars().all()
    resolve = await W.authority_resolver(session, country_code)
    for name in rows:
        proper = resolve(name or "")
        if name and proper and proper != name and len(name) >= 4:
            variants[name] = canonical_authority(proper)
    def _alt(v: str) -> str:  # 纯外文缩写（“CNDP”）：括号里的、已经跟在中文全称后面的不再展开
        return rf"(?<![（(A-Za-z]){re.escape(v)}(?![A-Za-z）)])" if re.fullmatch(r"[A-Za-z\- ]+", v) else re.escape(v) + r"(?:\s*[（(][A-Za-z]{2,9}[）)])?"

    pat = re.compile("|".join(_alt(v) for v in sorted(variants, key=len, reverse=True))) if variants else None
    from app.admission.names import CANON  # 准入页的受理机构规范名（7.1 的机构字典）也一并按它统一
    from app.analysis.document_basis import NAMES

    cname = NAMES.get(country_code, "")
    propers = {n.name_zh for n in await W._regulators(session, country_code) if n.name_zh} | {f for f in CANON if cname and f.startswith(cname)}
    forms = authority_forms(sorted(propers), cname)
    if isinstance(law_countries, (set, list, tuple)) and len(law_countries) > 1:
        forms = [(p, pr, "") for p, pr, _ in forms]  # 双国跨境文书中裸词“委员会”不单边归属某一国
    scoped_cc = law_countries or country_code

    def fix(t: str) -> str:
        t = clause_gate.unify_law_names(t, country_code=scoped_cc)
        if pat:
            t = pat.sub(lambda m: next(variants[k] for k in sorted(variants, key=len, reverse=True) if m.group(0).startswith(k)), t)
        t = unify_authorities(t, forms, country_name=cname)
        t = _collapse_repeated_authorities(t, forms, cname)
        t = re.sub(r"(?<=[\u4e00-\u9fff])[ \t]+(?=[\u4e00-\u9fff])", "", t)
        t = re.sub(r"(?<=[）》】\)])[ \t]+(?=[\u4e00-\u9fff，。；：、！？])", "", t)
        t = re.sub(r"(?<=[\u4e00-\u9fff])[ \t]+(?=[（《【\(])", "", t)
        return t

    out = []
    for b in blocks:
        b = dict(b)
        if b.get("original"):
            out.append(b)  # 官方外文条文原句逐字保留，不展开中文机构全称
            continue
        if b.get("verbatim_quote") and "text" in b:
            m_src = re.match(r"^(《[^》]+》[^：:]*[：:])(.*)$", str(b["text"]), re.S)
            if m_src:
                b["text"] = (
                    clause_gate.unify_law_names(m_src.group(1), country_code=scoped_cc)
                    + m_src.group(2)
                )
            out.append(b)
            continue
        if "text" in b:
            b["text"] = fix(b["text"])
        if "items" in b:
            b["items"] = [fix(x) for x in b["items"]]
        if b.get("type") == "table" and b.get("head") in (["法规（中文名）", "原名"],):
            out.append(b)  # 引用法规一览：原名栏照原样
            continue
        if b.get("type") == "table" and b.get("head") in (
            ["依据", "条款", "条文内容"],
            ["编号", "依据", "条款", "条文内容"],
        ):
            law_col = 1 if b.get("head")[0] == "编号" else 0
            rows = []
            for r in b.get("rows") or []:
                r2 = list(r)
                if len(r2) > law_col and isinstance(r2[law_col], str):
                    m_orig = re.match(r"^(《[^》]+》)(\s*（.+）)$", r2[law_col].strip())
                    if m_orig:
                        r2[law_col] = (
                            clause_gate.unify_law_names(m_orig.group(1), country_code=scoped_cc)
                            + m_orig.group(2)
                        )
                    else:
                        r2[law_col] = clause_gate.unify_law_names(r2[law_col], country_code=scoped_cc)
                rows.append(r2)
            b["rows"] = rows
            out.append(b)
            continue
        if b.get("type") == "table" and b.get("head") != ["编号", "法规与条款", "条文摘录"]:  # 条文摘录是法规原话，不动
            col = (b.get("head") or []).index("受理机构") if "受理机构" in (b.get("head") or []) else -1
            rows = []
            for r in b.get("rows") or []:
                r = [fix(c) if isinstance(c, str) else c for c in r]
                if 0 <= col < len(r) and isinstance(r[col], str):  # 受理机构一栏只写中文名（外文原名属于条文原文）
                    r[col] = foreign_name_dropped(canonical_authority(resolve(r[col])))
                rows.append(r)
            b["rows"] = rows
        out.append(b)
    return out


_FOREIGN_PAREN = re.compile(r"\s*[（(][^）)]*[؀-ۿA-Za-z’'][^）)]*[）)]")


def foreign_name_dropped(name: str) -> str:
    """“电信监管机构（الجهاز）”→“电信监管机构”: a body is written in Chinese; its original name belongs to the clause."""
    kept = re.sub(r"[（(]([A-Z][A-Za-z]{1,9})[）)]", lambda m: "<<" + m.group(1) + ">>", name or "")  # 英文缩写保留
    kept = _FOREIGN_PAREN.sub("", kept)
    return re.sub(r"<<([A-Z][A-Za-z]{1,9})>>", r"（\1）", kept).strip() or name


_ALT_SUFFIX = "局|机构|当局|中心|委员会"
_ABBR_PAREN = r"(?:\s*[（(][A-Za-z؀-ۿ][^）)]{1,80}[）)]|\s+[A-Z]{2,9}(?![A-Za-z]))?"


def canonical_authority(proper: str) -> str:
    """The one way a body is written: 中文全称（英文缩写）."""
    return re.sub(r"\s+([A-Z][A-Za-z]{1,9})$", r"（\1）", (proper or "").strip())


def authority_forms(propers: list[str], country_name: str) -> list[tuple[re.Pattern, str, str]]:
    """For each proper body name: a pattern for the ways a text shortens it (without the country,
    with another word for “局”, with a foreign-language name in brackets) → its one proper name; and the bare word (“委员会”) that stands for it."""
    out = []
    for proper in propers:
        if not proper or not re.search(r"[一-鿿]", proper):
            continue
        base = re.sub(r"[（(][^）)]*[）)]|\s+[A-Z]{2,9}$", "", proper).strip()
        if country_name and base.startswith(country_name):
            base = base[len(country_name):]
        m = re.match(rf"^(.{{4,}}?)({_ALT_SUFFIX})$", base)
        if not m:
            continue
        stem, suffix = m.group(1), m.group(2)
        stem_re = re.escape(stem).replace("监管", "(?:监管|管理)").replace("管理", "(?:监管|管理)") if re.search(r"监管|管理", stem) else re.escape(stem)
        stem_re = stem_re.replace("(?:监管|(?:监管|管理))", "(?:监管|管理)")
        lead = f"(?:{re.escape(country_name)})?" if country_name else ""
        pat = re.compile(rf"{lead}{stem_re}(?:{_ALT_SUFFIX}){_ABBR_PAREN}")
        bare = suffix if suffix == "委员会" and re.search(r"数据|信息", proper) else ""
        out.append((pat, canonical_authority(proper), bare))
    return out


def unify_authorities(text: str, forms: list[tuple[re.Pattern, str, str]], country_name: str = "") -> str:
    """Every short way of naming a body becomes its proper name; “委员会” standing alone
    becomes the data protection body's name when that body is a commission."""
    if not text:
        return text
    from app.analysis.document_basis import NAMES

    other_countries = sorted({c for c in NAMES.values() if c and c != country_name}, key=len, reverse=True)
    for pat, proper, bare in forms:
        keep = re.search(r"[（(]([A-Za-z]{2,9})[）)]$", proper)
        if keep:
            text = re.sub(rf"(?<![A-Za-z]){keep.group(1)}\s*[（(]\s*(?:{pat.pattern})\s*[）)]", proper, text)
        base_no_cc = re.sub(r"[（(][^）)]*[）)]$", "", proper).strip()
        if country_name and base_no_cc.startswith(country_name):
            base_no_cc = base_no_cc[len(country_name):]
        has_guojia = base_no_cc.startswith("国家")

        def sub(m, proper=proper, keep=keep, has_guojia=has_guojia):
            prefix = m.string[max(0, m.start() - 8) : m.start()]
            if any(prefix.endswith(oc) or prefix.endswith(oc + "国家") for oc in other_countries):
                return m.group(0)
            if not has_guojia and prefix.endswith("国家") and any(oc in prefix for oc in other_countries):
                return m.group(0)
            if keep:
                tail = m.string[m.end() : m.end() + 20]
                if re.match(rf"^[\u4e00-\u9fff]{{1,12}}[（(]\s*{re.escape(keep.group(1))}\s*[）)]", tail):
                    return m.group(0)
                m_acr = re.search(r"[（(]([A-Z]{2,8})[）)]|\s([A-Z]{2,8})(?![A-Za-z])$", m.group(0).strip())
                if m_acr and (m_acr.group(1) or m_acr.group(2)) != keep.group(1).upper():
                    return m.group(0)
                return proper
            ab = re.search(r"[（(]([A-Z][A-Za-z]{1,9})[）)]|\s([A-Z]{2,9})(?![A-Za-z])", re.sub(r"[（(](?:以下)?简称[^）)]*[）)]$", "", m.group(0))[-14:])
            return proper + (f"（{ab.group(1) or ab.group(2)}）" if ab else "")

        text = pat.sub(sub, text)
        ab = re.search(r"[（(]([A-Z]{3,6})[）)]$", proper)
        if ab:
            cc_lead = f"(?:{re.escape(country_name)}\\s*)?" if country_name and proper.startswith(country_name) else ""
            text = re.sub(rf"(?<![（(A-Za-z-]){cc_lead}{ab.group(1)}(?![A-Za-z0-9）)\-]|\s?(?:第|F\d|决议|-))", proper, text)
            if country_name and proper.startswith(country_name):
                text = text.replace(country_name + proper, proper)
        text = re.sub(rf"{re.escape(proper)}\s*[（(]\s*{re.escape(proper)}\s*[）)]", proper, text)
        if keep:
            text = re.sub(re.escape(proper) + r"\s*[（(][^）)]*[A-Za-zÀ-ÿ\u0600-\u06FF][^）)]*[）)]", proper, text)
        else:
            text = re.sub(
                re.escape(proper) + r"\s*[（(](?![A-Z][A-Za-z]{1,9}[）)])[^）)]*[A-Za-zÀ-ÿ\u0600-\u06FF][^）)]*[）)]",
                proper,
                text,
            )
        if bare:
            text = re.sub(rf"[（(](?:以下)?简称{bare}[）)]", "", text)  # 全称之后不再留“简称委员会”
            text = re.sub(rf"(?:(?<=[向按由经报附至给称被对让])|(?<=规定的)|(?<![一-鿿（(])){bare}(?:（Commission）)?(?!{bare[-1]})(?![一-鿿]{{0,2}}(?:法|指南))", lambda _m, proper=proper: proper, text)
    return text


_AMENDS = re.compile(r"第\s*([0-9]+[A-Z]*)\s*条[^。\n]{0,30}(现予|予以|兹)(修订|修改)")


def superseded_dropped(chosen: list[dict]) -> list[dict]:
    """When an amending act among the clauses rewrites article X, the principal act's article X
    (the text before the amendment) is not offered: only the amended rule is written."""
    amended = {m.group(1) for c in chosen for m in _AMENDS.finditer(str(c.get("quote_zh") or ""))}
    if not amended:
        return chosen
    out = []
    for c in chosen:
        num = re.sub(r"^第|条$", "", str(c.get("clause_number") or ""))
        if num in amended and not _AMENDS.search(str(c.get("quote_zh") or "")):
            continue
        out.append(c)
    return out


_SUBSECTION = re.compile(r"(?:^|\n)[“\"]?\((\d+[A-Z]?)\)\s*")


_DOLLAR_UNIT_ZH: dict[str, str] = {
    "SG": "新加坡元",
    "AU": "澳大利亚元",
    "CA": "加拿大元",
    "US": "美元",
    "NZ": "新西兰元",
    "HK": "港元",
}


def _money(p: str, country_code: str = "") -> str:
    cc = (country_code or "").upper()
    if "新加坡元" in p or "S$" in p or "SGD" in p:
        unit = "新加坡元"
    elif "AUD" in p or "A$" in p:
        unit = "澳大利亚元"
    elif "CAD" in p or "C$" in p:
        unit = "加拿大元"
    elif "USD" in p or "US$" in p:
        unit = "美元"
    else:
        unit = _DOLLAR_UNIT_ZH.get(cc, "元（当地法定货币）")
    p = re.sub(
        r"(?:[A-Z]{1,3})?\$\s*(\d+(?:,\d{3})*)\s*百万",
        lambda m: f"{int(m.group(1).replace(',', '')) * 100} 万{unit}",
        p,
    )
    p = re.sub(
        r"(?:[A-Z]{1,3})?\$\s*(\d+)\s*million",
        lambda m: f"{int(m.group(1)) * 100} 万{unit}",
        p,
    )
    p = re.sub(
        r"(?:[A-Z]{1,3})?\$\s*(\d{1,3}(?:,\d{3})+)",
        lambda m: f"{int(m.group(1).replace(',', '')) // 10000} 万{unit}",
        p,
    )
    return re.sub(r"——\s*(\d+ ?%)\s*of\s*(.+)$", r"——上限为\2的\1", p)


def turnover_point(
    blocks: list[dict],
    chosen: list[dict],
    refs: dict,
    base: dict | None = None,
    *,
    country_code: str = "",
) -> list[dict]:
    """Fines the law caps by a share of turnover: written by the program from the clause that sets
    them, one point per kind of person (organisation / individual or other person) with its own
    threshold, so the caps are never mixed. ``base``: the principal law's clause the amendment
    belongs to (cited together with it). Only when the dimension does not already say so."""
    text = " ".join(b.get("text", "") + " ".join(b.get("items", [])) for b in blocks)
    if re.search(r"营业额", text):
        return blocks
    for c in chosen:
        if re.search(r"指南|指引|咨询", str(c.get("document_title") or "")):
            continue  # 罚款上限以法律条文为准，不取指南的说明
        zh = str(c.get("quote_zh") or "")
        marks = list(_SUBSECTION.finditer(zh))
        points = []
        for k, m in enumerate(marks):
            seg = zh[m.end(): marks[k + 1].start() if k + 1 < len(marks) else len(zh)]
            if "营业额" not in seg or not re.search(r"\d+ ?%", seg):
                continue
            head, _, rest = seg.partition("：")
            subject = re.sub(r"^根据", "", head.split("，")[0]).strip("“” ")
            items = [re.sub(r"^\(?[a-z0-9]\)\s*", "", x.strip(" ；;。”“\"")) for x in rest.split("\n") if re.match(r"^\(?[a-z]\)", x.strip()) and ("——" in x or "$" in x)]
            items = [_money(re.sub(r"在《[^》]*》\s*\d*的?第\d+条的生效日期的当日或之后实施违反行为的", "", re.sub(r"[”“；;。]*以及$", "", x).strip(" ”“；;。")), country_code) for x in items if x]
            subject = re.sub(r"^第\(\d+\)款第\([a-z]\)项(第\([ivx]+\)目)?", "", subject)
            if subject and items:
                shown = [i.replace("对于由", "由").replace("对于个人的，", "个人：").replace("情形——", "：").replace("的情形——", "：").replace("在任何其他情况下——", "其他情形：") for i in items]
                scope = ""
                base_zh = str((base or {}).get("quote_zh") or "")
                if "(a)项" in head and (sm := re.search(r"\(a\)[^\n]*?违反(第[^\n；]*?部)的任何条文", base_zh)):
                    scope = f"（违反{sm.group(1)}的任何条文）"
                elif "(ii)目" in head and (sm := re.search(r"\(ii\)\s*(第[^\n，]*?条)", base_zh)):
                    if "第9部" in base_zh:
                        scope = f"（只适用于违反{sm.group(1)}的人员；违反第9部的人员适用第(4)款）"
                    else:
                        scope = f"（适用于违反{sm.group(1)}的情形）"
                points.append(f"第({m.group(1)})款，{subject}{scope}，上限：" + "；".join(shown))
        if not points:
            continue
        keys = []
        for cl in ([base] if base else []) + [c]:
            key = (str(cl.get("document_title", "")), str(cl.get("clause_number", "")))
            refs.setdefault(key, (len(refs) + 1, str(cl.get("quote_zh") or "")[: nar.OFFER_CHARS]))
            keys.append(key)
        cites = "".join(f"[{idx}]" for idx in sorted({refs[k][0] for k in keys}))
        law = str((base or c).get("document_title") or "")
        art = nar._clause_label(str((base or c).get("clause_number", "")))
        if base and base is not c:
            c_law = str(c.get("document_title") or "")
            c_art = nar._clause_label(str(c.get("clause_number", "")))
            lead = f"《{c_law}》{c_art}修改了《{law}》{art}，按营业额计算的罚款上限："
        else:
            lead = f"《{law}》{art}（经修订法修订）按营业额计算的罚款上限："
        items_out = [lead + points[0] + f"。{cites}"] + [pt + f"。{cites}" for pt in points[1:]]
        out = [dict(b) for b in blocks]
        lists = [b for b in out if b.get("type") == "list"]
        if lists:
            lists[0]["items"] = [*items_out, *lists[0]["items"]]
        else:
            out.append({"type": "list", "items": items_out})
        return out
    return blocks


async def _law_updated_date(session: AsyncSession, country_code: str) -> str:
    from datetime import timedelta, timezone

    from app.knowledge.library_view import library

    entries = [e for e in await library(session, country_code) if e.document.business_weight != "peripheral"]
    stamps = [e.version.fetched_at or e.version.parsed_at or e.document.discovered_at for e in entries]
    stamps = [t for t in stamps if t is not None]
    if not stamps:
        return ""
    return max(stamps).astimezone(timezone(timedelta(hours=8))).date().isoformat()


async def briefing_entries(session: AsyncSession, country_code: str) -> list[tuple[Any, str, str]]:
    """All listed admission items of the country (same set and count as D7 and ``/admission``),
    with (entry, “办理” or “视情况”, scenario/condition note)."""
    from app.admission import plan
    from app.admission.models import AdmissionEntry
    from app.admission.router import SHOWN, entry_listed
    from app.analysis.doc_rules import _ONWARD, admission_gate

    rows = (await session.scalars(select(AdmissionEntry).where(AdmissionEntry.country_code == country_code, AdmissionEntry.review_status.in_(SHOWN), AdmissionEntry.merged_into.is_(None)).order_by(AdmissionEntry.category, AdmissionEntry.name_zh))).all()
    cat_gate_fallback = {
        "vehicle_approval": "开展整车或零部件进口投放业务时办理",
        "telecom_approval": "投放含无线电或车载联网通信终端产品时办理",
        "data_filing": "涉及属地个人数据处理或跨境传输时核定办理",
        "av_testing": "开展自动驾驶道路测试或试点时办理",
    }
    out = []
    for r in rows:
        name = r.name_zh or ""
        if not await entry_listed(session, r):
            continue
        funcs = getattr(r, "functions", None)
        need = plan.applies(getattr(r, "category", ""), "connected_vehicle_cloud", funcs, name)
        fn_gate = ""
        if not need and r.category == "av_testing":
            need = "maybe"
            fn_gate = "开展自动驾驶道路测试或试点时办理"
        elif not need and funcs:
            matched_fns = [plan.FUNCTIONS[f] for f in funcs if f in plan.FUNCTIONS]
            if matched_fns:
                need = "maybe"
                fn_gate = f"开展{'、'.join(matched_fns[:3])}业务时办理"
        if not need:
            need = "maybe"
            fn_gate = cat_gate_fallback.get(getattr(r, "category", "") or "", "结合具体车型配置与属地业务形态核定办理")
        gate = admission_gate(r) or fn_gate
        if not gate and need == "maybe":
            gate = cat_gate_fallback.get(getattr(r, "category", "") or "", "结合具体车型功能与业务场景核定")
        if not gate and _ONWARD.search(name):
            gate = "数据从该国向境外传出时办理"
        out.append((r, "视情况" if gate else "办理", gate))
    return out


async def admission_table(session: AsyncSession, country_code: str) -> list[dict]:
    """The country's full listed admission items matching D7 count (one table)."""
    from app.admission.models import AdmissionStep

    out = []
    for r, state, gate in await briefing_entries(session, country_code):
        steps = (await session.scalars(select(AdmissionStep.title_zh).where(AdmissionStep.entry_id == r.id).order_by(AdmissionStep.seq))).all()
        out.append([r.name_zh, r.authority_zh or "条文未写明", "；".join(steps[:5]) or "条文未写明", state + (f"（{gate}）" if gate else "")])
    if not out:
        return []
    return [
        {"type": "paragraph", "text": f"**属地准入事项一览（共 {len(out)} 项）**"},
        {"type": "table", "head": ["准入事项", "受理机构", "主要环节", "办理要求"], "widths": [24, 22, 34, 20], "rows": out},
    ]


async def admission_context(session: AsyncSession, country_code: str) -> str:
    """The admission items a vehicle maker's cloud business has to do (and those only sometimes),
    as the admission library holds them: what D7 summarises."""
    from app.admission.models import AdmissionStep

    names = {"vehicle_approval": "车辆型式批准", "telecom_approval": "电信设备认证", "data_filing": "数据处理申报与许可", "av_testing": "自动驾驶测试", "other": "其他"}
    out = []
    for r, state, gate in (await briefing_entries(session, country_code))[:24]:
        steps = (await session.scalars(select(AdmissionStep.title_zh).where(AdmissionStep.entry_id == r.id).order_by(AdmissionStep.seq))).all()
        mats = [p.get("name") for p in (r.materials_plan or []) if p.get("name")][:4]
        out.append(f"{names.get(r.category, r.category)}：{r.name_zh}（{state}{'：' + gate if gate else ''}；受理：{r.authority_zh or '条文未写明'}；环节：{'、'.join(steps[:4]) or '条文未写明'}；材料：{'、'.join(mats) or '条文未写明'}）")
    return "该国准入事项库（第七维结合下列事项写两段综述，程序会在段后自动附上完整表格；综述归纳监管分工、核心材料与递交衔接要点，不写“建议书面确认”，也不引入库外事项）：" + "；".join(out)


async def law_names(session: AsyncSession, country_code: str, names: set[str]) -> list[list[str]]:
    """[中文名, 原名] of the laws a briefing cites, as the library registers them."""
    from app.ingestion.models import Document

    rows = (
        await session.execute(
            select(Document.id, Document.title_zh, Document.title).where(Document.country_code == country_code)
        )
    ).all()
    seen: dict[str, str] = {}
    for did, zh, orig in rows:
        raw_zh = (zh or "").strip()
        raw_orig = (orig or "").strip()
        disp = clause_gate.law_name(raw_zh or raw_orig, str(did), country_code=country_code)
        uni = clause_gate.unify_law_names(disp, country_code=country_code)
        matched = uni if uni in names else (disp if disp in names else (raw_zh if raw_zh in names else ""))
        if matched and matched not in seen and raw_orig and raw_orig != matched:
            seen[matched] = raw_orig
    return [[f"《{zh}》", orig] for zh, orig in seen.items()]


async def to_china(session: AsyncSession, country_code: str) -> list[dict]:
    """维度三末尾固定一段：跨法域传输路径核定要点（按该国法规客观归纳名单机制与前置保障，不预设单一接收国）。"""
    from app.analysis import doc_rules

    if country_code == "CN":
        return []
    ad = await doc_rules.adequacy(session, country_code, "CN")
    basis = await doc_rules.transfer_clause(session, country_code)
    options = doc_rules._options(basis, not_listed=bool(ad))
    if ad:
        lead = (
            f"**跨法域传输路径核定要点**：《{ad['law']}》{ad['article']}设有充分保护国家（或地区）名单机制。"
            "向列入名单的接收法域传输个人数据，可适用充分保护路径（仍须履行法定申报与告知义务）；"
            "向未列入名单的接收法域（如境外集团总部、区域研发中心或境外云平台所在法域）传输，须履行法定前置保障。"
        )
        return [{"type": "paragraph", "text": lead + (f" {options}" if options else "")}]
    if options:
        return [
            {
                "type": "paragraph",
                "text": "**跨法域传输路径核定要点**：该国现行法规未单列充分保护国家白名单，向任何境外接收法域传输个人数据均须满足法定前置保障。"
                + options,
            }
        ]
    if basis:
        return [
            {
                "type": "paragraph",
                "text": f"**跨法域传输路径核定要点**：向境外接收法域传输个人数据，须依《{basis['law']}》{basis['article']}履行法定合规义务。",
            }
        ]
    return []


async def latest(session: AsyncSession, country_code: str) -> CountryWhitepaper | None:
    return await session.scalar(
        select(CountryWhitepaper)
        .where(CountryWhitepaper.country_code == country_code)
        .order_by(CountryWhitepaper.version_no.desc())
        .limit(1)
    )


async def save(session: AsyncSession, country_code: str, blocks: list[dict], meta: dict) -> CountryWhitepaper:
    from sqlalchemy import text as sa_text

    await session.execute(
        sa_text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"),
        {"k": f"whitepaper:{country_code}"},
    )
    last = await session.scalar(
        select(func.max(CountryWhitepaper.version_no)).where(CountryWhitepaper.country_code == country_code)
    )
    row = CountryWhitepaper(
        country_code=country_code,
        version_no=(last or 0) + 1,
        blocks=blocks,
        source_hash=meta["source_hash"],
        tokens=meta["tokens"],
        report=meta["report"],
    )
    session.add(row)
    await session.flush()
    return row


def page_text_length(blocks: list[dict]) -> int:
    n = 0
    for b in blocks:
        n += len(str(b.get("text", ""))) + sum(len(i) for i in b.get("items", []))
    return n


def _extract_prev_sections_and_refs(
    blocks: list[dict], country_code: str
) -> tuple[dict[str, list[dict]], dict[tuple[str, str], tuple[int, str]]]:
    """Split an existing briefing's blocks into D0..D8 sections and recover its local citation map."""
    sections: dict[str, list[dict]] = {}
    prev_refs: dict[tuple[str, str], tuple[int, str]] = {}
    cur_dim: str | None = None

    num_to_dim = {NUMERALS[idx]: code for idx, (code, _) in enumerate(EIGHT_DIMENSIONS_SPEC)}
    for b in retitle(blocks):
        btype = b.get("type")
        txt = str(b.get("text") or "").strip()
        if btype == "title" or (btype == "paragraph" and txt.startswith("依据范围：") and cur_dim is None):
            continue
        if btype == "heading":
            if txt == "摘要":
                cur_dim = "D0"
                sections[cur_dim] = []
                continue
            if txt == "参考条文" or txt.startswith("所依据的条文"):
                cur_dim = "_REFS"
                continue
            m_num = re.match(r"^([一二三四五六七八])、", txt)
            if m_num and m_num.group(1) in num_to_dim:
                cur_dim = num_to_dim[m_num.group(1)]
                sections[cur_dim] = []
                continue
        if cur_dim == "_REFS" and btype == "table":
            for r in b.get("rows") or []:
                if len(r) < 3:
                    continue
                m_idx = re.search(r"\d+", str(r[0] or ""))
                if not m_idx:
                    continue
                idx_val = int(m_idx.group(0))
                if len(r) >= 4:
                    raw_doc = str(r[1] or "").strip()
                    clause_lbl = str(r[2] or "").strip()
                    excerpt = str(r[3] or "").strip()
                else:
                    cell = str(r[1] or "").strip()
                    excerpt = str(r[2] or "").strip()
                    m_split = re.match(r"^(.*?)\s+(第\s*[^\s]+|(?:Art|Sec|Section|Article)[^\s]*|\S+)$", cell)
                    if m_split:
                        raw_doc, clause_lbl = m_split.group(1).strip(), m_split.group(2).strip()
                    else:
                        raw_doc, clause_lbl = cell, ""
                clean_doc = re.sub(
                    r"（(?![A-Z0-9/\-]{2,12}）$)(?:[^（）]|（[^（）]*）|\([^()]*\))*[A-Za-z\u0600-\u06FF](?:[^（）]|（[^（）]*）|\([^()]*\))*）$",
                    "",
                    raw_doc,
                ).strip().strip("《》")
                clean_doc = clause_gate.unify_law_names(clean_doc, country_code=country_code)
                key = (clean_doc, clause_lbl)
                while key in prev_refs and prev_refs[key][0] != idx_val:
                    key = (clean_doc, f"{clause_lbl}#{idx_val}")
                prev_refs[key] = (idx_val, excerpt)
            continue
        if cur_dim and cur_dim != "_REFS":
            sections.setdefault(cur_dim, []).append(dict(b))
    return sections, prev_refs


async def sync_dimensions(
    session: AsyncSession, country_code: str, dimensions: list[str]
) -> tuple[list[dict], dict]:
    """Regenerate only the specified affected dimensions (D1..D8) after a law revision while preserving
    unaffected dimensions and renumbering citations cleanly."""
    prev = await latest(session, country_code)
    valid_dims = {code for code, _ in EIGHT_DIMENSIONS_SPEC}
    targets = {d.strip().upper() for d in (dimensions or []) if d and d.strip().upper() in valid_dims}
    if not targets:
        targets = {"D1", "D3"}
    if prev is None or targets >= valid_dims:
        return await write_whitepaper(session, country_code)

    prev_sections, prev_refs = _extract_prev_sections_and_refs(prev.blocks or [], country_code)
    if not any(k in prev_sections for k in valid_dims):
        return await write_whitepaper(session, country_code)

    name = country_name_zh(country_code)
    await clause_gate.load_title_names(session)
    per_dim = await gather(session, country_code)
    docs, clauses = await library_stats(session, country_code)
    pins = await pinned_topics(session, country_code)
    admission_list = await admission_context(session, country_code)
    await name_authorities(session, country_code, per_dim)

    refs: dict[tuple[str, str], tuple[int, str]] = {}
    body: list[dict] = []
    tokens = 0
    dropped: list[dict] = []
    chosen_of: dict[str, list[dict]] = {}
    written: dict[str, str] = {}

    first_seven = [(idx, code, title) for idx, (code, title) in enumerate(EIGHT_DIMENSIONS_SPEC) if code != "D8"]
    regen_seven = [(idx, code, title) for idx, code, title in first_seven if code in targets or code not in prev_sections]

    picked_map: dict[str, tuple[list[dict], int]] = {}
    if regen_seven:
        picked_res = await asyncio.gather(*(select_clauses(code, title, per_dim[code]) for _, code, title in regen_seven))
        for (_, code, _), res in zip(regen_seven, picked_res, strict=True):
            picked_map[code] = res

    async def _run_one(code: str, title: str) -> tuple[list[dict], int, list, dict, list[dict], dict | None]:
        chosen, picked_tokens = picked_map[code]
        turnover_clause = None
        extra = {
            "D1": pins.get("dpo", []),
            "D5": pins.get("breach", []),
            "D6": pins.get("financial_penalty", []) + pins.get("transfer_penalty", []),
            "D4": pins.get("consent", []) + pins.get("notice", []),
            "D3": pins.get("transfer_basis", []) + pins.get("transfer_exception", []),
            "D7": pins.get("declaration", []),
        }.get(code, [])
        chosen = [c for c in extra if c not in chosen][: 4 if code == "D3" else (3 if code == "D4" else 2)] + chosen
        if code == "D4":
            chosen = [c for c in chosen if re.search(r"同意|告知|通知|撤回|权利|查阅|更正|删除|反对|知情", str(c.get("quote_zh") or "")[:400])]
        if code != "D6":
            chosen = [c for c in chosen if not _penalty_clause(c)]
        chosen = chosen[:PER_DIMENSION]
        if code == "D6":
            caps = await turnover_caps(session, country_code, chosen)
            turnover_clause = caps[0] if caps else None
            chosen = superseded_dropped(chosen)
        lrefs: dict = {}
        blks, used_t, dr_list = await _write_dimension(code, title, name, chosen, lrefs, admission_list if code == "D7" else "")
        return blks, used_t + picked_tokens, dr_list, lrefs, chosen, turnover_clause

    regen_outputs: dict[str, tuple[list[dict], int, list, dict, list[dict], dict | None]] = {}
    if regen_seven:
        outs = await asyncio.gather(*(_run_one(code, title) for _, code, title in regen_seven))
        for (_, code, _), out_item in zip(regen_seven, outs, strict=True):
            regen_outputs[code] = out_item

    d3_tail = await to_china(session, country_code) if "D3" in regen_outputs else []
    d7_table = await admission_table(session, country_code) if "D7" in regen_outputs else []

    for idx, code, title in first_seven:
        body.append({"type": "heading", "text": f"{NUMERALS[idx]}、{display_title(code, title)}"})
        if code in regen_outputs:
            blks, used_tok, dr, lrefs, chosen, turnover_clause = regen_outputs[code]
            blks = _merge_local_refs(blks, lrefs, refs)
            if code == "D6" and turnover_clause:
                base48 = next((c for c in chosen if re.search(r"48J|财务处罚", str(c.get("clause_number", "")) + str(c.get("quote_zh") or "")[:80])), None)
                blks = turnover_point(blks, [turnover_clause], refs, base48, country_code=country_code)
            if code == "D7":
                blks = blks + d7_table
            chosen_of[code] = chosen
            tokens += used_tok
            dropped += dr
            body.extend(blks)
            if code == "D3":
                body.extend(d3_tail)
            written[code] = " ".join(b.get("text", "") + " ".join(b.get("items", [])) for b in blks)
        else:
            kept_blks = _merge_local_refs(prev_sections.get(code, []), prev_refs, refs)
            body.extend(kept_blks)
            written[code] = " ".join(b.get("text", "") + " ".join(b.get("items", [])) for b in kept_blks)

    d8_idx, (_, d8_title) = 7, EIGHT_DIMENSIONS_SPEC[7]
    body.append({"type": "heading", "text": f"{NUMERALS[d8_idx]}、{display_title('D8', d8_title)}"})
    if "D8" in targets or "D8" not in prev_sections:
        d8_pool: list[dict] = []
        for k in ("D1", "D3", "D4", "D5", "D6", "D7"):
            d8_pool += (chosen_of.get(k) or per_dim.get(k, [])[:2])[:2]
        d8_pool += [
            c
            for c in pins.get("dpo", []) + pins.get("breach", []) + pins.get("declaration", []) + pins.get("notice", []) + pins.get("consent", [])
            if c not in d8_pool
        ]
        d8_pool = d8_pool[:PER_DIMENSION]
        d8_ctx = "\n".join(f"【{k}】{written.get(k, '')}" for k in ("D1", "D2", "D3", "D4", "D5", "D6", "D7"))[:8000] + ("\n" + admission_list if admission_list else "")
        d8_lrefs: dict = {}
        d8_blks, d8_used, d8_dr = await _write_dimension("D8", d8_title, name, d8_pool, d8_lrefs, d8_ctx)
        d8_blks = _merge_local_refs(d8_blks, d8_lrefs, refs)
        tokens += d8_used
        dropped += d8_dr
        body.extend(d8_blks)
    else:
        kept_d8 = _merge_local_refs(prev_sections["D8"], prev_refs, refs)
        body.extend(kept_d8)

    prev_rep = prev.report if (prev is not None and isinstance(prev.report, dict)) else {}
    preserve_locked_abstract = bool(prev_rep.get("review_status") == "approved" and "D0" in prev_sections)
    abs_clauses: list[dict] = []
    if not preserve_locked_abstract and ("D0" in prev_sections or docs >= 5):
        for k in ("D1", "D2", "D3", "D5", "D6", "D7"):
            pool_k = chosen_of.get(k) or per_dim.get(k, [])
            picked_k = 0
            for c in pool_k:
                if (str(c.get("document_title", "")), str(c.get("clause_number", ""))) in refs and c not in abs_clauses:
                    abs_clauses.append(c)
                    picked_k += 1
                    if picked_k >= 2:
                        break
        if not abs_clauses:
            for k in ("D1", "D2", "D3", "D5", "D6", "D7"):
                for c in (per_dim.get(k) or [])[:2]:
                    if c not in abs_clauses:
                        abs_clauses.append(c)
    if abs_clauses:
        abs_ctx = "\n".join(f"【{k}】{written.get(k, '')[:500]}" for k in ("D1", "D2", "D3", "D5", "D6", "D7"))
        abs_lrefs: dict = {}
        abs_blocks, abs_used, abs_dr = await _write_dimension("D0", "摘要", name, abs_clauses[:12], abs_lrefs, abs_ctx)
        if abs_blocks:
            abs_blocks = _merge_local_refs(abs_blocks, abs_lrefs, refs)
            tokens += abs_used
            dropped += abs_dr
            body = [{"type": "heading", "text": "摘要"}, *abs_blocks, *body]
        elif "D0" in prev_sections:
            abs_merged = _merge_local_refs(prev_sections["D0"], prev_refs, refs)
            body = [{"type": "heading", "text": "摘要"}, *abs_merged, *body]
    elif "D0" in prev_sections:
        abs_merged = _merge_local_refs(prev_sections["D0"], prev_refs, refs)
        body = [{"type": "heading", "text": "摘要"}, *abs_merged, *body]

    stamp = datetime.now(UTC).astimezone().strftime("%Y-%m-%d")
    law_date = await _law_updated_date(session, country_code)
    date_part = f"法规最后更新 {law_date}，简报汇编日期 {stamp}" if law_date else f"简报汇编日期 {stamp}"
    head = [
        {"type": "title", "text": f"{name}汽车数据合规洞察简报"},
        {
            "type": "paragraph",
            "text": f"依据范围：本平台收录的{name}官方法规{docs}份、条款{clauses}条（{date_part}）。{LEVEL_NOTE}",
        },
    ]
    body = await one_name_per_body(session, country_code, body)
    body, ref_rows, cited_docs = renumber_citations(body, refs, country_code)
    tail: list[dict] = []
    if ref_rows:
        laws = await law_names(session, country_code, cited_docs | {doc for doc, _ in refs})
        tail = merge_law_origins_into_refs(
            [
                {"type": "heading", "text": "参考条文"},
                {
                    "type": "table",
                    "head": ["编号", "法规与条款", "条文摘录"],
                    "widths": [8, 32, 60],
                    "rows": ref_rows,
                },
            ],
            laws,
        )
    from app.knowledge.whitepaper_router import country_lib_stamp

    prev_rep = prev.report if (prev is not None and isinstance(prev.report, dict)) else {}
    merged_rep: dict[str, Any] = {
        "dropped": dropped,
        "documents": docs,
        "clauses": clauses,
        "synced_dimensions": sorted(targets),
        "lib_stamp": await country_lib_stamp(session, country_code),
    }
    for meta_k in (
        "review_status",
        "reviewed_at",
        "reviewed_by",
        "review_note",
        "manual_edited",
        "edited_at",
        "edited_by",
        "edit_note",
    ):
        if prev_rep.get(meta_k) is not None:
            merged_rep[meta_k] = prev_rep[meta_k]
    return head + body + tail, {
        "source_hash": source_hash(per_dim),
        "tokens": tokens,
        "report": merged_rep,
    }


