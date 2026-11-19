# ruff: noqa: E501
"""The articles each question of a documents project rests on, for the card beside the question —
for any sending and receiving country, by one rule.

As for the documents themselves (``doc_applicability``), the law is that of the country the data
leaves. Every question names the kinds of requirement it answers to; the article shown is:

- data leaving China: the article of the four provisions in the library (《数据出境安全评估办法》
  《个人信息出境标准合同办法》《促进和规范数据跨境流动规定》《汽车数据安全管理若干规定（试行）》)
  that asks for this fact, written here by article and read against the stored text: an article
  the library does not hold (or no longer parses) is not shown;
- data leaving another country: the best-ranked verified requirement of that country's knowledge
  graph of the kind the question answers to (and, where a kind is broad, whose name or condition
  speaks of what the question asks), with a citable article;
- the admission questions: the admission requirements of the overseas end, as the admission page.

A question with nothing found shows “暂无对应条文”; nothing is guessed. Each article carries the
library's document and clause ids so the card links to the article in the regulations library.
"""

from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

# 中国四部规定（法规库收录的原文核对过，条号与要点一一对应）
SA, SC, CP, AUTO = "数据出境安全评估办法", "个人信息出境标准合同办法", "促进和规范数据跨境流动规定", "汽车数据安全管理若干规定（试行）"
CN: dict[str, list[tuple[str, str, str]]] = {
    "is_cii": [(CP, "七", "关键信息基础设施运营者向境外提供个人信息或者重要数据，须申报数据出境安全评估")],
    "pi_scale": [(SC, "四", "以标准合同方式出境的，处理个人信息须不满 100 万人")],
    "security_officer": [(AUTO, "十三", "开展重要数据处理活动的，年度报告须写明汽车数据安全管理负责人的姓名和联系方式")],
    "law_compliance": [(SA, "八", "安全评估重点评估遵守中国法律、行政法规、部门规章的情况")],
    "scenario_summary": [(SA, "五", "自评估重点评估数据出境的目的、范围、方式等的合法性、正当性、必要性")],
    "purpose": [
        (SA, "五", "自评估重点评估数据出境的目的、范围、方式等的合法性、正当性、必要性"),
        (AUTO, "四", "处理汽车数据应当与汽车的设计、生产、销售、使用、运维等直接相关"),
    ],
    "data_types": [(SA, "五", "自评估重点评估出境数据的规模、范围、种类、敏感程度")],
    "important_data": [
        (CP, "二", "未被相关部门、地区告知或者公开发布为重要数据的，不需要作为重要数据申报"),
        (AUTO, "三", "汽车重要数据包括含人脸、车牌信息的车外视频图像，涉及超过 10 万人的个人信息等"),
    ],
    "sensitive_pi": [
        (AUTO, "三", "车辆行踪轨迹、音频、视频、图像和生物识别特征等属于敏感个人信息"),
        (CP, "七", "累计向境外提供 1 万人以上敏感个人信息的，须申报数据出境安全评估"),
    ],
    "data_volume": [
        (CP, "七", "自当年 1 月 1 日起累计向境外提供 100 万人以上个人信息的，须申报数据出境安全评估"),
        (CP, "八", "10 万人以上、不满 100 万人的，订立个人信息出境标准合同或者通过个人信息保护认证"),
    ],
    "recipient": [(AUTO, "十四", "向境外提供重要数据的，须报告接收者的基本情况")],
    "retention_and_blackbox": [(SA, "九", "法律文件须约定数据在境外的保存地点、期限，以及到期后的处理措施")],
    "risk_measures": [(CP, "十一", "向境外提供数据，须采取技术措施和其他必要措施，保障数据出境安全")],
    "risk_rating": [(SA, "五", "自评估重点评估数据出境中和出境后遭到篡改、破坏、泄露、丢失等的风险")],
    "risk_conclusion": [(SA, "五", "申报安全评估前须开展数据出境风险自评估")],
    "assessment_work": [(SA, "五", "申报安全评估前须开展数据出境风险自评估")],
    "onward_provision": [(SA, "九", "法律文件须约定对境外接收方将数据再转移给其他组织、个人的约束性要求")],
    "security_mgmt": [(CP, "十一", "向境外提供数据，须履行数据安全保护义务")],
    "security_tech": [
        (CP, "十一", "向境外提供数据，须采取技术措施和其他必要措施"),
        (AUTO, "六", "倡导车内处理、默认不收集、精度范围适用、脱敏处理"),
    ],
    "effectiveness_proof": [(AUTO, "五", "利用信息网络处理汽车数据，须落实网络安全等级保护等制度")],
    "recipient_measures": [(SA, "五", "自评估重点评估境外接收方履行责任义务的管理和技术措施、能力")],
    "legal_doc_terms": [(SA, "九", "法律文件至少约定目的范围、境外保存地点和期限、再转移约束、情况变化时的措施、违约责任、应急处置等")],
    "governing_law": [(SA, "九", "法律文件须约定违约责任和争议解决方式")],
    "exterior_video_and_gnss": [
        (AUTO, "八", "无法征得同意采集车外个人信息并向车外提供的，须匿名化处理，如删除人脸画面或局部轮廓化"),
        (AUTO, "六", "倡导尽可能进行匿名化、去标识化处理"),
    ],
    "data_subjects": [(AUTO, "三", "个人信息是与车主、驾驶人、乘车人、车外人员等有关的信息")],
    "data_flow": [(AUTO, "六", "倡导车内处理，除非确有必要不向车外提供")],
    "lawful_basis": [(AUTO, "八", "处理个人信息须取得个人同意，或者符合法律、行政法规规定的其他情形")],
    "consent_and_notice": [
        (AUTO, "七", "须通过用户手册、车载显示面板、语音、App 等显著方式告知处理个人信息的事项"),
        (CP, "十", "向境外提供个人信息，须履行告知、取得个人单独同意等义务"),
    ],
    "necessity": [(AUTO, "四", "处理汽车数据应当合法、正当、具体、明确")],
    "cross_border_mechanism": [
        (CP, "八", "10 万人以上、不满 100 万人的，订立个人信息出境标准合同或者通过个人信息保护认证"),
        (CP, "七", "达到门槛的，须申报数据出境安全评估"),
    ],
    "supplementary_measures": [
        (AUTO, "六", "倡导尽可能进行匿名化、去标识化处理"),
        (CP, "十一", "须采取技术措施和其他必要措施，保障数据出境安全"),
    ],
    "reevaluation": [
        (SC, "八", "出境目的、范围、保存期限变化，或接收方所在国法规变化的，须重新开展个人信息保护影响评估"),
        (SA, "十四", "出境情况或接收方情况发生变化的，须重新申报评估"),
    ],
}

# 其他发出国：每题答的是哪类要求（按顺序取第一条有条号的）；给了词的，要求的名称或跨境条件里要有这些词
_APPROVAL = re.compile(r"许可|授权|批准|核准|通报|事先通知|事前通知|申报|备案")
_CONTRACT = re.compile(r"合同|协议|法律文件|约束性|可强制执行|担保")
_ADEQUACY = re.compile(r"充分|同等|相当|保护水平|适当保护|适当保障")
# 影响评估：只认针对个人数据的评估（云服务商自身的“风险评估”这类不算）
_DPIA = re.compile(r"(个人(数据|信息)|数据保护|隐私)[^，。；]{0,10}(影响|风险)评估|(影响|风险)评估[^，。；]{0,10}(个人(数据|信息)|数据保护|隐私)")
_LEGIT = re.compile(r"合法利益|正当利益")
KINDS: dict[str, tuple[str, re.Pattern | None]] = {
    "approval": ("transfer", _APPROVAL),
    "contract": ("transfer", _CONTRACT),
    "adequacy": ("transfer", _ADEQUACY),
    "transfer": ("transfer", None),
    "dpia": ("inner", _DPIA),
    "legit": ("inner", _LEGIT),
    "consent": ("consent", None),
    "security": ("security", None),
    "governance": ("governance", None),
}
# 每题：按顺序在哪类要求里找、要求里要有哪些词（词要具体，宁可“暂无对应条文”也不挂不相干的条文）
ASPECTS: dict[str, list[tuple[str, str | None]]] = {
    "is_cii": [("approval", r"关键信息基础设施|关键基础设施|重要信息基础设施"), ("governance", r"关键信息基础设施|关键基础设施")],
    "important_data": [("approval", r"重要数据|国家安全"), ("security", r"重要数据")],
    "pi_scale": [("approval", r"(?<!个)人数|数量|规模|门槛|万人")],
    "data_volume": [("approval", r"(?<!个)人数|数量|规模|门槛|万人")],
    "scenario_summary": [("approval", None)],
    "purpose": [("transfer", r"目的|用途"), ("consent", r"处理目的|目的明确|目的限定|特定目的|目的相关")],
    "data_types": [("approval", r"种类|类别|类型"), ("transfer", r"种类|类别|类型")],
    "sensitive_pi": [(kind, r"敏感(及[^，。；]{0,4})?个人|敏感个人信息|敏感及健康|特殊类别") for kind in ("consent", "security", "transfer")],
    "lawful_basis": [("consent", r"合法(性)?(基础|依据)|合法处理|处理[^，。；]{0,6}同意|同意[^，。；]{0,4}处理"), ("legit", None)],
    "consent_and_notice": [("consent", r"收集前告知|告知[^，。；]{0,4}(义务|目的|事项)|通知[^，。；]{0,4}(目的|义务)|知情")],
    "exterior_video_and_gnss": [("security", r"匿名|假名|脱敏|去标识|视频|图像|地理位置|定位"), ("consent", r"匿名|假名|脱敏|去标识|视频|图像|地理位置|定位")],
    "recipient": [("adequacy", None), ("contract", None), ("approval", None)],
    "cross_border_mechanism": [("adequacy", None), ("contract", None), ("approval", None)],
    "onward_provision": [("transfer", r"再转移|再次转移|转移给第三方|继续转移|向第三方")],
    "legal_doc_terms": [("contract", None)],
    "governing_law": [("contract", r"争议|管辖|适用法律|仲裁|责任")],
    "retention_and_blackbox": [(k, r"保存期限|保留期限|存储期限|保存[^，。；]{0,4}期|停止保留|不再需要[^，。；]{0,8}(保留|删除|销毁)|超期[^，。；]{0,4}(删除|销毁)") for k in ("security", "consent")],
    "risk_measures": [("security", r"安全措施|保护措施|技术措施|组织措施|技术和组织|加密")],
    "security_tech": [("security", r"技术措施|技术和组织|加密|安全措施")],
    "security_mgmt": [("governance", r"制度|政策|程序|内部规则|管理体系"), ("security", r"组织措施|管理措施")],
    "effectiveness_proof": [("governance", r"审计|测评|合规评估|合规审查"), ("security", r"审计|测评")],
    "supplementary_measures": [("security", r"加密|假名|匿名|去标识")],
    "recipient_measures": [("adequacy", None), ("contract", None)],
    "security_officer": [("governance", r"负责人|保护官|专员|DPO")],
    "security_org": [("governance", r"负责人|保护官|管理机构|DPO")],
    "dpo_opinion": [("governance", r"数据保护官|保护官|DPO")],
    "risk_rating": [("dpia", None)],
    "risk_conclusion": [("dpia", None)],
    "assessment_work": [("dpia", None)],
    "data_flow": [("dpia", None)],
    "necessity": [("legit", None), ("dpia", None)],
    "reevaluation": [("dpia", None)],
}
# 要求名称里要说到数据（个人数据、隐私、数据主体……）才挂到数据题上
_ABOUT_DATA = re.compile(r"个人(数据|信息)|数据|PII|隐私|信息主体")
_PERSONAL_DATA = re.compile(r"个人(数据|信息)|PII|隐私|数据主体|信息主体")
# 准入题已移归“海外属地准入”模块，合规文件侧不再挂准入题依据
ADMISSION_KEYS: tuple[str, ...] = ()
MAX_PER_QUESTION = 2
_CN_ARTICLES_CACHE: dict[str, dict[tuple[str, str], dict]] = {}


async def _cn_articles(session: AsyncSession) -> dict[tuple[str, str], dict]:
    """(law, clause number) → where the library holds it, for the four provisions; read from the
    latest parsed version of each, only articles whose stored text starts with that article."""
    from app.ingestion.models import Clause, Document, DocumentVersion
    from app.knowledge.cache_sync import keeper_active, rebuilding, use_cached

    s_cache = session.info.get("_cn_articles")
    if s_cache is not None and not rebuilding.get():
        return s_cache
    if use_cached("CN" in _CN_ARTICLES_CACHE):
        return _CN_ARTICLES_CACHE["CN"]

    laws = {SA, SC, CP, AUTO}
    docs = (await session.scalars(select(Document).where(Document.country_code == "CN"))).all()
    matched_docs: list[tuple[Document, str]] = []
    for doc in docs:
        title = next((t for t in (doc.title_zh, doc.title) if t and t.strip() in laws), None)
        if title is not None:
            matched_docs.append((doc, title.strip()))
    out: dict[tuple[str, str], dict] = {}
    if matched_docs:
        doc_ids = [doc.id for doc, _ in matched_docs]
        versions = (
            await session.scalars(
                select(DocumentVersion)
                .where(DocumentVersion.document_id.in_(doc_ids), DocumentVersion.state == "parsed")
                .order_by(DocumentVersion.document_id, DocumentVersion.version_no.desc())
            )
        ).all()
        latest_ver_by_doc = {}
        for v in versions:
            latest_ver_by_doc.setdefault(v.document_id, v.id)
        ver_ids = list(latest_ver_by_doc.values())
        clauses_by_ver: dict = {}
        if ver_ids:
            for c in (
                await session.scalars(
                    select(Clause).where(Clause.version_id.in_(ver_ids), Clause.clause_number.is_not(None))
                )
            ).all():
                clauses_by_ver.setdefault(c.version_id, []).append(c)
        for doc, title in matched_docs:
            vid = latest_ver_by_doc.get(doc.id)
            if vid is None:
                continue
            for c in clauses_by_ver.get(vid, ()):
                number = (c.clause_number or "").strip()
                if (c.original_text or "").lstrip().startswith(f"第{number}条"):
                    out.setdefault((title, number), {"document_id": str(doc.id), "clause_id": str(c.id)})
    session.info["_cn_articles"] = out
    if keeper_active or rebuilding.get():
        _CN_ARTICLES_CACHE["CN"] = out
    return out


async def cn_link(session: AsyncSession, law: str, number: str) -> str:
    """The regulations-library address of one article of the four provisions (“” when not held)."""
    at = (await _cn_articles(session)).get((law.strip("《》"), number))
    return link({"country": "CN", **at}) if at else ""


def _cn_card(key: str, held: dict[tuple[str, str], dict]) -> list[dict]:
    out = []
    for law, number, point in CN.get(key, []):
        at = held.get((law, number))
        if at is None:
            continue  # 库里没有这一条：不显示，不凭记忆写
        out.append({"law": f"《{law}》", "article": f"第{number}条", "point": point, "country": "CN", **at})
    return out[:MAX_PER_QUESTION]


async def _admission_cards(session: AsyncSession, cc: str, function: str) -> list[dict]:
    """The legal basis of the admission items the overseas end requires for this business (the
    first ones, in the order the admission page and the documents list show them)."""
    from app.admission import plan
    from app.admission.models import AdmissionEntry
    from app.admission.router import SHOWN, _basis
    from app.analysis.doc_applicability import _CATEGORY_FIRST

    if not cc:
        return []
    entries = (
        await session.scalars(
            select(AdmissionEntry)
            .where(AdmissionEntry.country_code == cc, AdmissionEntry.review_status.in_(SHOWN), AdmissionEntry.merged_into.is_(None))
            .order_by(AdmissionEntry.category, AdmissionEntry.name_zh)
        )
    ).all()
    needed = [e for e in entries if plan.applies(e.category, function, e.functions, e.name_zh) == "needed"]
    needed.sort(key=lambda e: _CATEGORY_FIRST.index(e.category) if e.category in _CATEGORY_FIRST else len(_CATEGORY_FIRST))
    out: list[dict] = []
    for e in needed:
        b = await _basis(session, e.evidence_clause_id)
        if not b or not b.get("clause_number") or any(x["clause_id"] == b["clause_id"] for x in out):
            continue
        out.append({"law": f"《{b['document']}》", "article": b["clause_number"], "point": f"须办理“{e.name_zh}”", "country": cc,
                    "document_id": b["document_id"], "clause_id": b["clause_id"]})
        if len(out) >= MAX_PER_QUESTION:
            break
    return out


async def _graph_cards(session: AsyncSession, cc: str, keys: list[str]) -> dict[str, list[dict]]:
    from app.knowledge import whitepaper_graph as W

    pools_by_cc: dict[str, dict] = {}

    async def pools(code: str) -> dict:
        if code not in pools_by_cc:
            got, docs, _ = await W.requirement_pools(session, code) if code else ({}, {}, None)
            # 数据题只挂个人数据保护类法规里的要求：网络安全、云服务、物联网、电信等技术文件里的不挂
            got = {k: [n for n in v if W._doc_of(n, docs or {})[1] == "personal_data"] for k, v in (got or {}).items()}
            got = dict(got or {})
            got["inner"] = (got.get("governance") or []) + (got.get("consent") or [])
            pools_by_cc[code] = got
        return pools_by_cc[code]

    def text(n) -> str:
        return " ".join((n.name_zh or "", W._attr(n, "cross_border_condition")))

    cites: dict = {}

    async def cite_of(n) -> dict | None:
        if n.id not in cites:
            cites[n.id] = await W.citation_of(session, n)
        return cites[n.id]

    out: dict[str, list[dict]] = {}
    for key in keys:
        cards: list[dict] = []
        for kind, words in ASPECTS.get(key, []):
            pool_name, kind_pattern = KINDS[kind]
            code = cc
            if not code:
                continue
            nodes = (await pools(code)).get(pool_name) or []
            wanted = re.compile(words) if words else None
            fits = []
            for n in nodes:
                name = (n.name_zh or "").strip()
                if (kind_pattern and not kind_pattern.search(text(n))) or (wanted and not wanted.search(name)):
                    continue  # 类别看名称和跨境条件；题目要的词只看要求名称
                if pool_name != "transfer" and not _ABOUT_DATA.search(name):
                    continue  # 网络安全、物联网这类与数据处理无关的要求不挂到数据题上
                fits.append(n)
            # 先取说到个人数据的（数据保护法里的要求），没有再取其他说到数据的；其余各类按原排名
            if pool_name in ("security", "governance"):  # 这两类混有网络安全、云服务的要求
                fits.sort(key=lambda n: not _PERSONAL_DATA.search(n.name_zh or ""))
            for n in fits:
                c = await cite_of(n)
                if not c or not c.get("article") or not c.get("clause_id"):
                    continue
                card = {"law": f"《{c['law']}》", "article": c["article"], "point": (n.name_zh or "").strip(), "country": code,
                        "document_id": str(c["document_id"]), "clause_id": str(c["clause_id"])}
                if all(x["clause_id"] != card["clause_id"] for x in cards):
                    cards.append(card)
                break
            if len(cards) >= MAX_PER_QUESTION:
                break
        out[key] = cards
    return out


async def for_project(
    session: AsyncSession, *, source: str, recipient: str, keys: list[str], library: frozenset[str], function: str = ""
) -> dict[str, list[dict]]:
    """Per question key, the articles for its card (possibly none)."""
    from app.analysis.document_basis import country_of

    src = country_of(source) or ""
    ends = [c for c in (src, country_of(recipient)) if c and c != "CN"]
    overseas = ends[0] if ends else ""  # 海外一端（与准入页同一规则）
    data_keys = [k for k in keys if k not in ADMISSION_KEYS]
    if src == "CN":
        held = await _cn_articles(session)
        out = {k: _cn_card(k, held) for k in data_keys}
    else:
        out = await _graph_cards(session, src if src in library else "", data_keys)
    admission = [k for k in keys if k in ADMISSION_KEYS]
    if admission:
        cards = await _admission_cards(session, overseas, function)
        out.update({k: cards for k in admission})
    return out


def link(card: dict) -> str:
    """The regulations-library address of the article (the page opens it directly)."""
    return f"/knowledge?country={card['country']}&doc={card['document_id']}&clause={card['clause_id']}"
