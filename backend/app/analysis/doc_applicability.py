# ruff: noqa: E501
"""Whether each of the eight compliance documents is needed for this project, and why — for any
sending and receiving country.

The rule is the same for every country (a threshold step before the documents, as assessment
products do it): the legal grounds come from the law of the country the data leaves — its verified
requirements in the knowledge graph, by aspect, and for a country with its own outbound route
module (China, see ``cn_route``) that module — and the facts come from what the enterprise
answered. Three outcomes per document:

- ``need``: needed. Also the outcome whenever the grounds are not enough to say otherwise: a
  country whose regulations are not in the library is “按各国通行的要求判断，需要”, and a requirement the
  library does not show is never read as an exemption.
- ``na``: “按目前情况不需要” — only when the enterprise's own answers clearly meet the condition
  under which the document is not needed (no personal data at all, a lawful basis other than
  legitimate interests, no compliance issue found…). The document stays downloadable.
- ``unknown``: “还不能判断” — the answer that decides it is not given yet; ``missing_keys`` names
  the questions, so the page can jump to them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

NEED, NA, UNKNOWN = "need", "na", "unknown"
STATE_ZH = {NEED: "需要", NA: "按目前情况不需要", UNKNOWN: "还不能判断"}
GENERAL = "按各国通行的要求判断"

# 发出国法规里“哪类要求”支撑哪份文件：只看跨境、内部管理、告知同意几类已核验的要求，按名称和跨境条件里的词认
_APPROVAL = re.compile(r"许可|授权|批准|核准|通报|事先通知|事前通知|申报|备案")
_CONTRACT = re.compile(r"合同|协议|法律文件|约束性|可强制执行|担保")
_ADEQUACY = re.compile(r"充分|同等|相当|保护水平|适当保护|适当保障")
_DPIA = re.compile(r"数据保护影响评估|隐私影响评估|个人(数据|信息)(保护)?影响评估")
_LEGIT = re.compile(r"合法利益|正当利益")
_PERSONAL = re.compile(r"个人(数据|信息)")
_CATEGORY_FIRST = ("vehicle_approval", "telecom_approval", "data_filing")

_NONE_ANSWER = re.compile(r"^\s*(无|没有|暂无|未发现|不存在|不涉及|否)\s*[。.；;，,]?\s*$|^\s*(无|没有|暂无|未发现|不涉及)(任何)?(合规)?问题")
_NO_PERSONAL = re.compile(r"不(涉及|含|包含|处理|传输)(任何)?个人(数据|信息)|无个人(数据|信息)|没有个人(数据|信息)|^\s*(0|零)\s*(人|个)?\s*$|^\s*(无|不涉及)\s*$")
_OTHER_BASES = re.compile(r"同意|合同|法定义务|法律义务|法定职责|公共利益|重大利益|生命|紧急")


@dataclass
class Ground:
    """One requirement of the sending country's law that bears on a document."""

    name: str
    cite: str  # 《法规》第N条
    personal: bool  # 只针对个人数据


def _ground_text(g: Ground | None) -> str:
    return f"“{g.name}”" if g else ""


_SOURCE_GROUNDS_CACHE: dict[tuple, dict[str, Ground | None]] = {}


async def source_grounds(session: AsyncSession, cc: str) -> dict[str, Ground | None]:
    """The sending country's requirements that decide the documents, each the best-ranked verified
    one with a citable article (the same pools and ranking as the country briefing)."""
    from sqlalchemy import text as sa_text

    from app.knowledge import whitepaper_graph as W
    from app.knowledge.cache_sync import rebuilding

    stamp_row = (
        await session.execute(
            sa_text(
                "select "
                "(select md5(coalesce(string_agg(id::text || review_status || coalesce(verify_state, '') || name_zh, ',' order by id), '')) "
                " from kg_nodes where country_code = :cc), "
                "(select md5(coalesce(string_agg(d.id::text || d.relevance || coalesce(d.business_weight, '') || coalesce(d.title_zh, '') "
                " || coalesce(v.legal_status, '') || coalesce(v.review_status, ''), ',' order by d.id, v.id), '')) "
                " from documents d left join document_versions v on v.document_id = d.id where d.country_code = :cc), "
                "(select md5(coalesce(string_agg(p.id::text || p.status, ',' order by p.id), '')) "
                " from knowledge_change_proposals p where p.status in ('pending_review', 'approved'))"
            ),
            {"cc": cc},
        )
    ).one()
    cache_key = (cc, tuple(str(x) for x in stamp_row))
    if not rebuilding.get() and cache_key in _SOURCE_GROUNDS_CACHE:
        return _SOURCE_GROUNDS_CACHE[cache_key]

    pools, _, _ = await W.requirement_pools(session, cc)
    transfer = pools.get("transfer") or []
    inner = (pools.get("governance") or []) + (pools.get("consent") or [])

    # 通用出境主依据优先选上位基本法律（law/act），排在视频监控、医疗、银行等窄域专用决议/决定之前
    narrow_sector = re.compile(
        r"视频监控|摄像头|图像采集|门禁|考勤|生物识别门禁|医疗|健康|医院|病历|银行|信贷|征信|金融|保险|"
        r"vidéosurveillance|caméra|télésurveillance|biométr|santé|médical|bancaire",
        re.I,
    )
    primary_law_words = re.compile(
        r"个人数据保护法|个人信息保护法|数据安全法|网络安全法|保护自然人.*法|"
        r"\bAct\b|\bLoi\b|قانون",
        re.I,
    )
    doc_ids = {n.document_id for n in (*transfer, *inner) if getattr(n, "document_id", None)}
    clause_ids = {
        n.evidence_clause_id for n in (*transfer, *inner) if getattr(n, "evidence_clause_id", None)
    }
    doc_meta: dict = {}
    clause_doc_map: dict = {}
    if clause_ids:
        from app.ingestion.models import Clause, Document, DocumentVersion

        c_rows = (
            await session.execute(
                select(Clause.id, Document.id, Document.document_type, Document.instrument_kind, Document.title, Document.title_zh)
                .join(DocumentVersion, DocumentVersion.id == Clause.version_id)
                .join(Document, Document.id == DocumentVersion.document_id)
                .where(Clause.id.in_(clause_ids))
            )
        ).all()
        for cid, did, dtype, ikind, dtitle, dtitle_zh in c_rows:
            clause_doc_map[cid] = did
            doc_meta[did] = (dtype or "", ikind or "", dtitle or "", dtitle_zh or "")
    missing_doc_ids = doc_ids - set(doc_meta)
    if missing_doc_ids:
        from app.ingestion.models import Document

        d_rows = (
            await session.execute(
                select(Document.id, Document.document_type, Document.instrument_kind, Document.title, Document.title_zh)
                .where(Document.id.in_(missing_doc_ids))
            )
        ).all()
        for did, dtype, ikind, dtitle, dtitle_zh in d_rows:
            doc_meta[did] = (dtype or "", ikind or "", dtitle or "", dtitle_zh or "")

    def _statute_rank(n) -> tuple[int, int]:
        did = getattr(n, "document_id", None) or clause_doc_map.get(getattr(n, "evidence_clause_id", None))
        dtype, ikind, dtitle, dtitle_zh = doc_meta.get(did, ("", "", "", ""))
        titles = f"{dtitle} {dtitle_zh} {n.name_zh or ''}"
        is_narrow = 1 if narrow_sector.search(titles) else 0
        kind_low = f"{dtype} {ikind}".lower()
        is_primary = 0 if (any(w in kind_low for w in ("law", "act", "statute", "primary")) or primary_law_words.search(f"{dtitle} {dtitle_zh}")) else 1
        return (is_narrow, is_primary)

    def text(n) -> str:
        return " ".join((n.name_zh or "", W._attr(n, "cross_border_condition")))

    async def first(nodes, pattern: re.Pattern | None) -> Ground | None:
        # 优先上位通用基本法律且非窄域专用规范，再取写得出条号的那条；都没有条号时取排名第一的
        ordered = sorted(nodes, key=_statute_rank)
        fallback = None
        for n in ordered:
            if pattern is not None and not pattern.search(text(n)):
                continue
            cite = await W.citation_of(session, n)
            if not cite:
                continue
            if narrow_sector.search(cite["law"]):
                # 窄域专用决议仅在没有任何通用上位法候选时兜底
                ground = Ground(n.name_zh.strip(), f"《{cite['law']}》{cite['article']}", bool(_PERSONAL.search(text(n))))
                fallback = fallback or ground
                continue
            ground = Ground(n.name_zh.strip(), f"《{cite['law']}》{cite['article']}", bool(_PERSONAL.search(text(n))))
            if cite["article"]:
                return ground
            fallback = fallback or ground
        return fallback

    approvals = [n for n in transfer if _APPROVAL.search(text(n))]
    out = {
        "approval": await first(approvals, None),
        "contract": await first(transfer, _CONTRACT),
        "adequacy": await first(transfer, _ADEQUACY),
        "transfer": await first(transfer, None),
        "dpia": await first(inner, _DPIA),
        "legit": await first(inner, _LEGIT),
    }
    # 发出国要求报批的跨境传输是否都只针对个人数据：都是时，“不涉及个人信息”才免得了申报
    out["approval_personal_only"] = bool(approvals) and all(_PERSONAL.search(text(n)) for n in approvals)  # type: ignore[assignment]
    # 库里有这国的法规、但还没整理出任何已核验的要求：与未收录同样按通用要求
    out["organized"] = any(pools.values())  # type: ignore[assignment]
    if len(_SOURCE_GROUNDS_CACHE) > 64:
        _SOURCE_GROUNDS_CACHE.clear()
    _SOURCE_GROUNDS_CACHE[cache_key] = out
    return out


async def admission_needed(session: AsyncSession, cc: str, function: str, inbound: bool = False) -> tuple[bool, list[str]]:
    """Whether the library holds admission items for ``cc`` at all, and the names of those this
    business must handle (the same rule the admission plan uses)."""
    from app.admission import plan
    from app.admission.models import AdmissionEntry
    from app.admission.router import SHOWN

    entries = (
        await session.scalars(
            select(AdmissionEntry)
            .where(AdmissionEntry.country_code == cc, AdmissionEntry.review_status.in_(SHOWN), AdmissionEntry.merged_into.is_(None))
            .order_by(AdmissionEntry.category, AdmissionEntry.name_zh)
        )
    ).all()
    from app.admission.router import entry_listed
    from app.analysis.doc_rules import admission_gate

    entries = [e for e in entries if await entry_listed(session, e)]  # 与准入页同一口径
    needed = [e for e in entries if plan.applies(e.category, function, e.functions, e.name_zh) == "needed" and not admission_gate(e)]
    if inbound:  # 海外一端是接收国：它管数据传出的许可不列（接收方再向第三国转移时才要）
        from app.analysis.doc_rules import _ONWARD

        needed = [e for e in needed if not _ONWARD.search(f"{e.name_zh} {e.summary_zh or ''}"[:80])]
    # 举例时车辆、通信设备、数据备案类在前（最常见、最先要办），其余按原顺序
    # 举例时和这项业务直接相关的在前：数据类（每项业务都传车辆数据）、业务本身对应的类别，再到车辆、通信设备
    own = [c for c, fns in plan.ONLY_FOR.items() if function in fns]  # 这项业务专有的类别（如自动驾驶测试）
    first = ("data_filing", *own, "vehicle_approval", "telecom_approval")
    needed.sort(key=lambda e: first.index(e.category) if e.category in first else len(first))
    return bool(entries), [e.name_zh for e in needed]


def _quote(label: str, value: str, n: int = 24) -> str:
    v = re.sub(r"\s+", " ", value or "").strip()
    return f"“{label}：{v[:n]}{'……' if len(v) > n else ''}”"


def _out(state: str, why: str, basis: str = "", missing_keys: list[str] | None = None, labels: dict[str, str] | None = None) -> dict:
    keys = missing_keys or []
    return {
        "state": state, "label": STATE_ZH[state], "why": why, "basis": basis,
        "missing_keys": keys, "missing": [(labels or {}).get(k, k) for k in keys],
    }


def judge(
    *,
    source: str,
    recipient: str,
    source_in_library: bool,
    values: dict[str, str],
    labels: dict[str, str],
    grounds: dict | None,
    cn: dict | None,
    overseas: str,
    overseas_known: bool,
    admission: list[str],
    function_zh: str,
) -> dict[str, dict]:
    """Per document code: {state, label, why, basis, missing_keys, missing}. ``grounds`` is
    ``source_grounds`` of the sending country (None when it is not in the graph); ``cn`` the result
    of the sending country's own route module (``cn_route``), if it has one."""
    from app.analysis.cn_route import _yes as _cn_yes
    from app.analysis.cn_route import has_no_personal_data

    src, rcp = source or "数据发出国", recipient or "数据接收国"
    g = grounds or {}
    volume = values.get("data_volume", "")
    sensitive = values.get("sensitive_pi", "").strip()
    dtypes = values.get("data_types", "").strip()
    sensitive_is_yes = _cn_yes(sensitive, "sensitive_pi") is True
    no_personal = has_no_personal_data(volume, sensitive, dtypes)
    no_personal_src = (
        _quote(labels.get("data_volume", "涉及自然人数量"), volume)
        if volume.strip()
        else _quote(labels.get("data_types", "传输的数据类型"), dtypes)
    )
    no_personal_why = f"你填了{no_personal_src}，不向境外提供个人信息"
    general = f"{src}的法规还没有收录，按各国通行的要求判断，需要"

    def from_cn(code: str) -> dict | None:
        doc = (cn or {}).get("docs", {}).get(code)
        if not doc:
            return None
        because = "、".join((cn or {}).get("because") or [])
        if doc["applies"] is None:
            return _out(UNKNOWN, f"{doc['why']}，回答“{'”“'.join(cn['missing'])}”后自动判断", doc["basis"], cn.get("missing_keys"), labels)
        why = (f"你填了{because}，" if because else "") + doc["why"]
        return _out(NEED if doc["applies"] else NA, why, doc["basis"])

    def facts_said() -> str:
        """The answers that make personal data part of the transfer, quoted."""
        said = []
        if sensitive_is_yes:
            said.append(_quote(labels.get("sensitive_pi", "是否含敏感个人信息"), sensitive, 16))
        if volume and not no_personal:
            said.append(_quote(labels.get("data_volume", "涉及自然人数量"), volume))
        if not said and values.get("data_types"):
            said.append(_quote(labels.get("data_types", "传输的数据类型"), values["data_types"]))
        return "你填了" + "、".join(said) if said else ""

    def personal_need(lead: str) -> str:
        said = facts_said()
        if said:
            return f"{said}，{lead}"
        return f"{lead}；完全不涉及个人信息的，“{labels.get('data_volume', '涉及自然人数量')}”写“0”"

    out: dict[str, dict] = {}

    # 1 申报表：发出国对数据出境的申报 / 批准
    if cn is not None:
        out["declaration"] = from_cn("declaration")
    elif not source_in_library:
        out["declaration"] = _out(NEED, general)
    elif g.get("approval"):
        if no_personal and g.get("approval_personal_only"):
            out["declaration"] = _out(NA, f"{no_personal_why}；{src}要求报批的是个人数据出境", g["approval"].cite)
        else:
            out["declaration"] = _out(NEED, f"数据从{src}发出，{src}法规要求{_ground_text(g['approval'])}", g["approval"].cite)
    else:
        out["declaration"] = _out(NEED, f"数据从{src}发出；已收录的{src}法规里没有出境须报批的条文，按各国通行的要求先备一份申报材料，可备而不交")

    # 2 风险评估报告：出境前评估是否满足发出国的出境条件
    if cn is not None:
        out["risk_report"] = from_cn("risk_report")
    elif not source_in_library:
        out["risk_report"] = _out(NEED, general)
    elif ground := (g.get("approval") or g.get("adequacy") or g.get("contract") or g.get("transfer")):
        out["risk_report"] = _out(NEED, f"出境前须评估是否满足{src}的出境条件，如{_ground_text(ground)}", ground.cite)
    else:
        out["risk_report"] = _out(NEED, f"已收录的{src}法规没有单列出境条件，按各国通行的要求，出境前须评估风险")

    # 3 合同：与境外接收方约定数据保护责任
    if cn is not None:
        out["contract"] = from_cn("contract")
    elif not source_in_library:
        out["contract"] = _out(NEED, general)
    elif g.get("contract"):
        out["contract"] = _out(NEED, f"{src}法规要求{_ground_text(g['contract'])}", g["contract"].cite)
    else:
        out["contract"] = _out(NEED, f"已收录的{src}法规没有单列合同要求，按各国通行的要求，须与{rcp}的接收方约定数据保护责任")

    # 4 整改跟踪：有没有发现合规问题
    issues = values.get("issues", "").strip()
    issues_label = labels.get("issues", "发现的合规问题")
    if not issues:
        out["remediation"] = _out(UNKNOWN, f"填写“{issues_label}”后判断；没有问题写“无”", "", ["issues"], labels)
    elif _NONE_ANSWER.search(issues):
        out["remediation"] = _out(NA, f"你填了{_quote(issues_label, issues)}，没有要跟踪整改的问题")
    else:
        count = len([s for s in re.split(r"[\n；;]+|(?:^|\s)\d+[.、．)]", issues) if s.strip()]) or 1
        out["remediation"] = _out(NEED, f"你填了 {count} 条{issues_label}，须逐条跟踪整改")

    # 5 准入材料清单：海外一端要办的准入事项
    entry = values.get("entry_type", "").strip()
    entry_label = labels.get("entry_type", "准入事项")
    where = f"“{function_zh}”" if function_zh else "这项业务"
    if admission:
        names = "、".join(f"“{n}”" for n in admission[:2])
        out["admission"] = _out(NEED, f"{overseas}对{where}要办 {len(admission)} 项准入事项，如{names}")
    elif entry and _NONE_ANSWER.search(entry):
        out["admission"] = _out(NA, f"你填了{_quote(entry_label, entry)}，法规库里{overseas or '海外一端'}也没有{where}要办的准入事项")
    elif entry:
        out["admission"] = _out(NEED, f"你填了{_quote(entry_label, entry)}")
    elif not overseas_known:
        out["admission"] = _out(NEED, f"{overseas or '海外一端'}的准入事项还没有收录，按各国通行的要求判断，需要")
    else:
        out["admission"] = _out(UNKNOWN, f"法规库里{overseas}没有{where}要办的准入事项；填写“{entry_label}”后判断，不办理写“无”", "", ["entry_type"], labels)

    # 6 DPIA：处理个人信息的影响评估
    if no_personal:
        out["dpia"] = _out(NA, no_personal_why)
    elif cn is not None:
        doc = from_cn("dpia")  # 走安全评估时并入风险自评估报告，不单独准备
        out["dpia"] = doc if doc["state"] != NEED else _out(NEED, personal_need(cn["docs"]["dpia"]["why"]), doc["basis"])
    elif not source_in_library:
        out["dpia"] = _out(NEED, personal_need(general))
    elif g.get("dpia"):
        out["dpia"] = _out(NEED, personal_need(f"{src}法规要求{_ground_text(g['dpia'])}"), g["dpia"].cite)
    else:
        out["dpia"] = _out(NEED, personal_need("处理车辆数据中的个人信息，按各国通行的要求须评估对个人的影响"))

    # 7 LIA：只在以合法利益为处理依据时需要（按分句逐一核验，支持“正当利益/合法利益”双向否定与多场景分句）
    basis = values.get("lawful_basis", "").strip()
    basis_label = labels.get("lawful_basis", "处理的合法性基础")
    legit_term_pat = r"(?:正当)?(?:合法|正当)利益|合法权益（?追求|\blegitimate\s+interests?\b|intérêt\s+légitime"
    neg_legit_re = re.compile(
        rf"(?:不以|非以|不适用|不采用|不援引|并非|不含|不涉及|排除|无需|不用)[^，。；;\n]{{0,12}}(?:{legit_term_pat})|"
        rf"(?:{legit_term_pat})[^，。；;\n]{{0,8}}(?:不适用|不采用|不援引|除外|排除|不涉及|无|否)|"
        r"\bnot\s+(?:based\s+on\s+|relying\s+on\s+|rely\s+on\s+)?legitimate\s+interests?\b|"
        r"\bno\s+legitimate\s+interests?\b",
        re.I,
    )
    pos_legit_re = re.compile(rf"{legit_term_pat}|\blegitimate\b", re.I)
    has_neg_legit = False
    has_pos_legit = False
    if basis:
        def _strip_neg_paren(m: re.Match) -> str:
            nonlocal has_neg_legit
            if neg_legit_re.search(m.group(1)):
                has_neg_legit = True
                return " "
            return m.group(0)

        cleaned_basis = re.sub(r"[（(]([^）)]*)[）)]", _strip_neg_paren, basis)
        clauses_b = [
            c.strip()
            for c in re.split(
                r"[；;\n。]+|[，,]\s*(?=(?:不以|非以|不适用|不采用|不援引|并非|不含|不涉及|排除|但|不过|然而|而|同时|另外|车|远程|自动驾驶))",
                cleaned_basis,
            )
            if c.strip()
        ]
        for cl_b in clauses_b:
            if neg_legit_re.search(cl_b):
                has_neg_legit = True
            elif pos_legit_re.search(cl_b):
                has_pos_legit = True
    if no_personal:
        out["lia"] = _out(NA, no_personal_why)
    elif not basis:
        out["lia"] = _out(UNKNOWN, f"填写“{basis_label}”后判断：以合法利益为依据时才需要", "", ["lawful_basis"], labels)
    elif has_pos_legit:
        cite = g["legit"].cite if g.get("legit") else ""
        out["lia"] = _out(NEED, f"你填了{_quote(basis_label, basis)}，以合法利益为依据须评估利益是否超过对个人的影响", cite)
    elif has_neg_legit or _OTHER_BASES.search(basis):
        out["lia"] = _out(NA, f"你填了{_quote(basis_label, basis)}，不以合法利益为依据")
    else:
        out["lia"] = _out(NEED, f"没能从你填的{_quote(basis_label, basis)}认出合法性基础，先按需要准备；写明是否以合法利益为依据后再判断")

    # 8 TIA：接收国的法律环境能否提供发出国要求的保护
    lead = f"数据传往{rcp}，须评估{rcp}的法律环境能否提供{src}要求的保护"
    if no_personal:
        out["tia"] = _out(NA, no_personal_why)
    elif cn is not None and from_cn("tia"):
        out["tia"] = _out(NEED, personal_need(cn["docs"]["tia"]["why"]), cn["docs"]["tia"]["basis"])
    elif cn is None and not source_in_library:
        out["tia"] = _out(NEED, personal_need(general))
    elif g.get("adequacy") or g.get("contract"):
        ground = g.get("adequacy") or g.get("contract")
        out["tia"] = _out(NEED, personal_need(f"{lead}，{src}法规要求{_ground_text(ground)}"), ground.cite)
    else:
        out["tia"] = _out(NEED, personal_need(lead))
    return out


async def for_project(
    session: AsyncSession, *, source: str, recipient: str, values: dict[str, str], labels: dict[str, str],
    function: str, library: frozenset[str], cn: dict | None,
) -> dict[str, dict]:
    """``judge`` for one project: reads the sending country's grounds and the overseas end's
    admission items from the library."""
    from app.admission import plan
    from app.analysis.document_basis import NAMES, country_of

    src_cc = country_of(source)
    grounds = None
    if cn is None and src_cc and src_cc in library:
        grounds = await source_grounds(session, src_cc)
    # 海外一端（与准入页同一规则）：两端里中国以外、认得出的那一端
    ends = [c for c in (src_cc, country_of(recipient)) if c and c != "CN"]
    overseas_cc = ends[0] if ends else ""
    inbound = bool(overseas_cc) and overseas_cc == country_of(recipient) and not str(values.get("onward_provision", "")).startswith("有")
    known, needed = (await admission_needed(session, overseas_cc, function, inbound)) if overseas_cc else (False, [])
    overseas = NAMES.get(overseas_cc, "") or (recipient if overseas_cc else "")
    return judge(
        source=source, recipient=recipient,
        source_in_library=cn is not None or bool(grounds and grounds.get("organized")),
        values=values, labels=labels, grounds=grounds, cn=cn,
        overseas=overseas, overseas_known=known, admission=needed,
        function_zh=plan.FUNCTIONS.get(function, ""),
    )
