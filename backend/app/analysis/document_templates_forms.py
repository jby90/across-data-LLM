# ruff: noqa: E501  模板正文是长句数据，不按代码行宽折行
"""Starting templates for the other five standard documents of requirement 3.2 (items 1–5).

Same as the first three: data, not logic; nothing depends on a country (the destination, legal basis
and evidence come from the assessment the document is made for). Structure follows what each
document is for: the outbound-data declaration lists who sends what to whom, why, with which
safeguards; the risk report identifies, grades and judges; the contract template carries the
clauses a transfer contract needs; the remediation tracker is a list of problems each with its
basis, measure and verification; the market-access list says which materials the target market asks
for and how each is declared.
"""

from app.analysis import official_cn_standard_contract as _OFFICIAL_CN  # noqa: E402

_COMMON = [
    {
        "key": "country_name",
        "label": "数据发出国（适用其法律）",
        "question": "请问这份文件针对哪个国家？",
        "why": "文件需要写明适用的国家。",
    },
    {
        "key": "source_country",
        "label": "数据发出国",
        "question": "数据从哪个国家（或地区）发出？（写国家名，如“中国”“摩洛哥”）",
        "why": "文件按数据流向的两端各自适用的规定编写，不假定任何一方是中国。",
        "short": True,
    },
    {
        "key": "recipient_country",
        "label": "数据接收国",
        "question": "数据最终发往哪个国家（或地区）？（写国家名）",
        "why": "文件按数据流向的两端各自适用的规定编写，不假定任何一方是中国。",
        "short": True,
    },
    {
        "key": "vehicle_model",
        "label": "车型或车队",
        "question": "请问涉及的车型或车队名称是什么？",
        "why": "文件需要写明对象。",
    },
    {
        "key": "scenario",
        "label": "业务场景",
        "question": "请问涉及的业务场景是什么（例如道路测试、远程诊断）？",
        "why": "文件需要写明数据出境发生的场景。",
    },
    {
        "key": "company_name",
        "label": "数据发出方（数据控制者）",
        "question": "请问承担本次数据处理责任的公司全称是什么？",
        "why": "文件需要写明责任主体。",
        "short": True,
    },
]

def _fact(
    key: str,
    label: str,
    question: str,
    why: str,
    *,
    short: bool = False,
    optional: bool = False,
    source: str | None = None,
    choices: list[str] | None = None,
    detail_on: str | None = None,
) -> dict:
    """The evidence documents collect the business facts from the user in the conversation, the same
    way for every document; ``source`` names the assessment fact that already holds the value.
    ``optional`` is kept for a fact a template author wants shown but not required."""
    fact = {"key": key, "label": label, "question": question, "why": why, "short": short, "optional": optional}
    if source:
        fact["from"] = source
    if choices:  # 是/否这类问题：页面上给按钮点选，不用手打
        fact["choices"] = choices
    if detail_on:  # 选了这一项还要写一句（如“是”之后写明涉及多少人）：页面在选中后展开输入框
        fact["detail_on"] = detail_on
    return fact


# 申报表栏目：通用申报要素 + 参照国家网信办《数据出境安全评估申报指南（第三版）》（2025-06-27）附件 3 的单位与出境场景栏目。
_DECLARATION_FACTS = [
    _fact("entity_nature", "单位性质", "单位性质是什么（政府部门、事业单位、企业、社会组织或其他）？", "单位情况。", short=True),
    _fact("entity_type", "单位类型", "单位类型是什么（内资、外资、中外合资、港澳台资、境外注册企业或其他）？", "单位情况。", short=True),
    _fact("registered_place", "单位注册地", "单位注册地在哪里？", "单位情况。", short=True),
    _fact("office_place", "办公所在地", "办公所在地在哪里？", "单位情况。", short=True),
    _fact("staff_count", "员工数量", "员工数量大约是多少？", "单位情况。", short=True),
    _fact("credit_code", "单位注册号", "单位注册号是多少？（中国企业填统一社会信用代码）", "单位情况。", short=True),
    _fact("is_cii", "是否关键信息基础设施运营者", "是否为关键信息基础设施运营者？", "影响出境路径。", short=True, choices=["是", "否"]),
    _fact("pi_scale", "处理个人信息规模", "按自然人（去重）统计，处理的个人信息规模是多少？", "规模决定出境路径。", short=True),
    _fact("legal_rep", "法定代表人信息", "法定代表人的姓名、职务/国籍、联系电话、电子邮箱、证件类型和号码是什么？", "申报表要求。"),
    _fact("security_officer", "数据安全负责人信息", "数据安全负责人的姓名、职务/国籍、联系电话、电子邮箱、证件类型和号码是什么？", "申报表要求。"),
    _fact("security_org", "数据安全管理机构", "数据安全管理机构的名称和人数是什么？", "申报表要求。", short=True),
    _fact("agent_info", "经办人信息", "经办人的姓名、职务/国籍、联系电话、电子邮箱、证件类型和号码是什么？", "申报表要求。"),
    _fact("law_compliance", "遵守法律法规情况", "近两年在业务经营中是否受到行政处罚、监管调查或整改？重点说明数据和网络安全方面，没有请如实写“无”。", "申报表要求。"),
    _fact("scenario_summary", "跨境传输场景简述", "请用不超过 300 字说明本次跨境传输的业务、目的和方式。", "申报表要求。", source="scenario"),
    _fact("purpose", "传输目的", "数据跨境传输的具体目的是什么？", "目的决定传输是否必要。"),
    _fact("data_types", "传输的数据类型", "传输的数据有哪些类型（车外视频、定位轨迹、座舱数据、车辆运行数据等）？", "申报与评估都要写明数据范围。", source="exterior_video_and_gnss"),
    _fact("important_data", "是否含重要数据", "是否含重要数据？含的话可补充认定依据和主管部门。", "影响出境路径。", short=True, choices=["是", "否"]),
    _fact("sensitive_pi", "是否含敏感个人信息", "是否含敏感个人信息（如精确位置、人脸等）？含的话写明涉及多少人（去重）。", "敏感信息适用更严格要求，人数决定出境路径。", short=True, choices=["是", "否"], detail_on="是"),
    _fact("industry_field", "涉及行业领域", "涉及的行业或领域是什么（如交通、互联网、工业等）？", "申报表要求。", short=True),
    _fact("data_volume", "涉及自然人数量", "涉及自然人（去重）数量是多少？包括当年已传输数量和未来三年预估。", "规模决定出境路径。"),
    _fact("recipient", "境外接收方", "境外接收方的名称、所在国家或地区、地址、主营业务是什么？", "申报必须写明接收方。"),
    _fact("recipient_contact", "接收方负责人与联系方式", "接收方负责人的姓名、职务、电话和邮箱是什么？", "申报表要求。"),
    _fact("retention_and_blackbox", "保存期限与存放位置", "数据在接收方保存多久、存放在哪里、到期如何销毁？", "评估存储限制与安全措施。"),
    _fact("risk_measures", "已采取的安全保护措施", "目前已采取哪些技术和管理措施（加密、脱敏、访问控制、审计等）？", "说明保护能力。"),
]

DECLARATION = {
    "revision": 11,  # 11：场景简述放宽至 300 字并补充境外注册企业选项；10：统一流向表头为数据发出国与数据接收国
    "title": "汽车数据出境评估申报表",
    "facts": [*_COMMON, *_DECLARATION_FACTS],
    "sections": [
        {
            "heading": "一、申报方基本情况",
            "blocks": [
                {
                    "type": "facts_table",
                    "keys": [
                        "company_name", "entity_nature", "entity_type", "registered_place", "office_place",
                        "staff_count", "credit_code", "is_cii", "pi_scale",
                    ],
                }
            ],
        },
        {"heading": "二、法定代表人信息", "blocks": [{"type": "facts_table", "keys": ["legal_rep"]}]},
        {
            "heading": "三、数据安全负责人和管理机构信息",
            "blocks": [{"type": "facts_table", "keys": ["security_officer", "security_org"]}],
        },
        {"heading": "四、经办人信息", "blocks": [{"type": "facts_table", "keys": ["agent_info"]}]},
        {
            "heading": "五、遵守法律法规情况",
            "blocks": [{"type": "text", "text": "{{law_compliance}}"}],
        },
        {
            "heading": "六、数据跨境传输情况",
            "blocks": [
                {
                    "type": "facts_table",
                    "keys": [
                        "scenario_summary", "vehicle_model", "source_country", "recipient_country", "purpose", "data_types", "important_data",
                        "sensitive_pi", "industry_field", "data_volume", "recipient", "recipient_contact",
                        "retention_and_blackbox",
                    ],
                },
            ],
        },
        {"heading": "七、数据安全保护措施", "blocks": [{"type": "text", "text": "{{risk_measures}}"}]},
        {
            "heading": "八、适用的法规依据",
            "blocks": [{"type": "citations", "dimensions": ["D3", "D5", "D1"], "limit": 8}],
        },
        {
            "heading": "九、承诺",
            "blocks": [
                {
                    "type": "text",
                    "text": "{{company_name}} 承诺申报材料所有内容真实、完整、准确和有效，为主管部门组织实施的评估工作提供必要的配合和支持，并在传输情况发生变化时及时更新申报。",
                }
            ],
        },
    ],
}

# 风险评估报告：需求 3.2 要求含风险识别、风险分级、合规判定、整改建议；附录参照网信办自评估报告模板（附件 4）的章节。
_RISK_FACTS = [
    _fact("risk_rating", "主要风险及其可能性、严重程度与应对措施", "本次传输的主要风险有哪些？每项风险写一行：风险内容、你判断的可能性和严重程度（低/中/高），以及至少一项应对措施；可以写责任人和完成时限。", "风险分级需要企业对自身情况的判断；每个风险至少一项措施。"),
    _fact("risk_findings", "已发现的问题和整改情况", "评估中发现了哪些问题，整改情况如何？没有发现问题请写明。", "整改建议的依据。"),
    _fact("risk_conclusion", "评估结论", "综合评估和整改情况，本次传输的风险结论是什么，理由是什么？", "报告必须得出结论。"),
    _fact("assessment_work", "自评估工作情况", "评估是何时、由谁、怎样开展的（起止时间、组织情况、实施过程与方式）？", "附录：自评估报告第一部分。"),
    _fact("company_profile", "数据处理者基本情况", "请简述公司股权结构、实际控制人、组织架构、数据安全管理机构，以及整体业务与数据资产情况。", "附录：自评估报告第二部分（一）。"),
    _fact("storage_chain", "境内存储与传输链路", "数据在境内存放在哪些系统平台、数据中心（含云服务），经什么链路、以什么方式传输出去？", "附录：自评估报告第二部分（二）。"),
    _fact("onward_provision", "向其他接收方提供", "数据传输后是否还会向其他接收方提供？", "附录：自评估报告第二部分（二）。", short=True, choices=["有", "无"]),
    _fact("security_mgmt", "数据安全管理能力", "数据安全管理组织、制度建设，分类分级、应急处置、风险评估、个人信息权益保护等制度及落实情况如何？", "附录：自评估报告第二部分（三）。"),
    _fact("security_tech", "数据安全技术能力", "数据收集、存储、使用、传输、提供、删除等环节采取了哪些安全技术措施？", "附录：自评估报告第二部分（三）。"),
    _fact("effectiveness_proof", "保障措施有效性证明", "已开展过哪些数据安全风险评估、认证、检查测评、合规审计、等级保护测评？", "附录：自评估报告第二部分（三）。"),
    _fact("recipient_measures", "接收方的管理与技术措施", "接收方为履行责任义务采取了哪些管理和技术措施，具备怎样的能力？", "附录：自评估报告第二部分（四）。"),
    _fact("legal_doc_terms", "法律文件约定的责任义务", "与接收方的法律文件约定了哪些内容：保存地点和期限、期满后的处理、再转移的约束、情况变化时的处理、违约责任与争议解决、应急处置和个人权益保障？", "附录：自评估报告第二部分（五）。"),
]

RISK_REPORT = {
    "revision": 20,  # 20：精细隔离各章法理职责，杜绝跨章复读字段枚举与口头禅套话
    "title": "汽车数据跨境风险评估报告",
    "facts": [
        *_COMMON,
        *[f for f in _DECLARATION_FACTS if f["key"] in ("purpose", "data_types", "data_volume", "recipient", "important_data", "sensitive_pi", "retention_and_blackbox", "risk_measures", "law_compliance")],
        *_RISK_FACTS,
    ],
    "narrative": [
        {
            "key": "overview_scope",
            "role": "governance_scope",
            "side": "source",
            "dimensions": ["D1", "D3"],
            "heading": "二、自评估工作背景与数据处理者合规管理体系",
            "brief": "撰写两段精练治理自查陈述：结合本次跨境数据自评估组织实施情况、公司业务布局与专职数据安全管理机构配置，核验近两年遵守数据与网络安全法律法规情况，确立内控评估事实基准；严禁在本章罗列具体出境字段清单或展开加密参数，不提前给出全篇风险结论。",
            "facts": ["company_name", "country_name", "vehicle_model", "scenario", "assessment_work", "company_profile", "law_compliance"],
        },
        {
            "key": "business_necessity",
            "role": "necessity_proportionality",
            "side": "source",
            "dimensions": ["D2", "D3", "D4"],
            "heading": "三、出境业务场景、数据范围与最小必要性论证",
            "brief": "将拟出境数据拆解为结构化数据项清单后，撰写两段反事实排他必要性论证：阐明为何纯境内本地处理或完全剥离车辆关联标识无法达成同等行车安全运维与履约目标，核定重要数据与敏感个人信息边界、自然人规模、境内系统链路及无擅自再转移安排；不重复铺陈安全管理制度，不提前下全篇综合结论。",
            "facts": ["scenario", "purpose", "data_types", "important_data", "sensitive_pi", "data_volume", "storage_chain", "onward_provision"],
            "field_table": True,
        },
        {
            "key": "security_capability",
            "role": "safeguards_lifecycle",
            "side": "source",
            "dimensions": ["D5", "D8", "D4"],
            "heading": "四、境内全链路技术防护与数据安全管理能力评估",
            "brief": "撰写两段全链路安全保障实效推演：一段核验数据分类分级、应急响应及权益保障制度与等级保护测评或安全认证等有效性证明；另一段推演源端去标识化、双向鉴权加密通道、访问控制审计与期满连同备份彻底删除如何阻断未授权访问与超期滞留；严禁再次逐项背诵第三章数据项名称，不提前下全篇综合结论。",
            "facts": ["security_mgmt", "security_tech", "risk_measures", "effectiveness_proof", "retention_and_blackbox"],
        },
        {
            "key": "recipient_and_contract",
            "role": "recipient_and_remedy",
            "side": "source",
            "dimensions": ["D3", "D5"],
            "heading": "五、境外接收方保护能力与法律文件约束力论证",
            "brief": "撰写两段接收方履约与契约阻断分析：评估境外接收方部署的隔离环境、最小权限制度与安全技术能力，并剖析跨境法律文件关于唯一目的限制、到期删除、严禁擅自再转移、外部调取审查通报、连带违约追责及数据主体跨境救济条款的可执行性；不提前下全篇综合结论。",
            "facts": ["recipient", "recipient_country", "purpose", "recipient_measures", "legal_doc_terms", "onward_provision"],
        },
        {
            "key": "risk_and_conclusion",
            "role": "risk_and_verdict",
            "side": "source",
            "dimensions": ["D3", "D5", "D6"],
            "heading": "六、跨境安全风险研判与综合评估结论论证",
            "brief": "对照发出国出境安全评估法定审查要件撰写两段终局研判：一段结合自查问题与阶段性整改完成情况剖析数据遭篡改、泄露、越权利用或违规再转移的风险压降实效；另一段给出全篇唯一综合风险评估结论，明确法定申报/备案前置程序、单路径异常熔断及变更重评条件；正文中不得提及任何表格字样。",
            "facts": ["risk_rating", "risk_findings", "risk_conclusion", "important_data", "sensitive_pi"],
        },
    ],
    "sections": [
        {
            "heading": "一、评估对象与跨境流转要素概览",
            "blocks": [
                {
                    "type": "text",
                    "text": "本报告针对 {{company_name}} 在{{country_name}}开展【{{scenario}}】业务过程中涉及的汽车数据跨境传输活动（适用车型或车队：{{vehicle_model}}）开展系统性合规风险评估。本次数据传输自{{source_country}}发往{{recipient_country}}，核心业务目的在于：{{purpose}}。报告结合智能网联汽车数据流转全生命周期特征、数据发出国出境监管规范与接收国属地保护制度，采用法条要件比对与风险发生可能性及影响严重程度分级判定相结合的方法开展合规风险评估。",
                },
                {"type": "facts_table", "keys": ["source_country", "recipient_country", "data_types", "data_volume", "important_data", "sensitive_pi", "recipient"]},
            ],
        },
        {"narrative": True},
        {
            "heading": "七、风险等级评估与安全防护措施台账",
            "blocks": [
                {
                    "type": "text",
                    "text": "依据个人信息及数据安全风险评估通行规范，从风险发生的可能性与一旦发生对个人合法权益及公共安全造成的严重程度两个维度（各设低、中、高三档）综合判定风险等级，逐项列明风险评估结果与安全防控措施。",
                },
                {"type": "risk_matrix", "key": "risk_rating"},
                {
                    "type": "text",
                    "text": "现有安全防护与风险防控体系综述：{{company_name}} 当前已建立并实施的技术与组织管理措施包括：{{risk_measures}}。",
                },
                {"type": "slot", "name": "safeguard_effectiveness"},
            ],
        },
        {
            "heading": "八、法定路径判定与持续合规治理方案",
            "blocks": [
                {
                    "type": "text",
                    "text": "对照数据发出国现行有效的数据跨境流动监管法源及目标市场属地合规准入要求，结合本项目已核实的业务事实与自查整改记录，对法定传输路径、持续治理措施及法规依据汇总如下：",
                },
                {"type": "slot", "name": "route"},
                {"type": "text", "text": "自查发现问题与阶段性整改核验情况：{{risk_findings}}"},
                {"type": "text", "text": "综合评估判定结论：{{risk_conclusion}}"},
                {"type": "slot", "name": "verdict_decision"},
                {"type": "slot", "name": "advice"},
                {"type": "citations", "dimensions": ["D1", "D2", "D3", "D4", "D5"], "limit": 12},
            ],
        },
        {
            "when": {"source_country": "CN"},
            "heading": "附录：自评估报告章节底稿（参照国家网信办《数据出境安全评估申报指南（第三版）》附件 4）",
            "blocks": [
                {"type": "text", "text": "（一）自评估工作情况：{{assessment_work}}"},
                {"type": "text", "text": "（二）数据处理者基本情况：{{company_profile}}"},
                {"type": "text", "text": "（三）境内存储与传输链路：{{storage_chain}}；向其他接收方提供：{{onward_provision}}"},
                {"type": "text", "text": "（四）数据安全保障能力。管理能力：{{security_mgmt}}技术能力：{{security_tech}}有效性证明：{{effectiveness_proof}}遵守法律法规情况：{{law_compliance}}"},
                {"type": "text", "text": "（五）接收方的管理与技术措施：{{recipient_measures}}"},
                {"type": "text", "text": "（六）法律文件约定的责任义务：{{legal_doc_terms}}"},
            ],
        },
    ],
}

# 合同：需求 3.2 要求含权责条款、免责条款、合规约束；条款结构参照欧盟委员会标准合同条款（SCC，实施决定 2021/914）。
# 数据从中国发出：合同采用国家网信办《个人信息出境标准合同》范本全文（办法要求“应当严格按照附件订立”），
# 只在已知信息的位置填入评估事实；非中国发出时用上面的欧盟标准合同条款（SCC）结构。
_OFFICIAL_CN_CONTRACT_SECTIONS = [
    {
        "when": {"source_country": "CN"},
        "heading": sec["heading"],
        "blocks": [{"type": "text", "text": para} for para in sec["paras"]],
    }
    for sec in _OFFICIAL_CN.SECTIONS
]

CONTRACT = {
    "revision": 18,  # 18：明确约定目的内处理边界，避免与智驾算法训练场景冲突；17：统一流向表头为数据发出国与数据接收国
    "title": "汽车数据跨境传输合规合同",
    "facts": [
        *_COMMON,
        *[f for f in _DECLARATION_FACTS if f["key"] in ("data_types", "data_volume", "purpose", "recipient", "retention_and_blackbox", "risk_measures", "sensitive_pi")],
        _fact("governing_law", "适用法律与争议解决", "合同适用哪国法律，争议如何解决（法院或仲裁）？", "合同必须约定适用法律与争议解决。"),
        _fact("sensitive_pi_kinds", "出境敏感个人信息种类", "出境的敏感个人信息有哪些种类（如精确位置、人脸图像、生物识别）？不含敏感个人信息写“不涉及”。", "标准合同附录一要写明敏感个人信息的种类。", short=True),
        *[{**f, "optional": True} for f in [*_DECLARATION_FACTS, *_RISK_FACTS] if f["key"] in ("registered_place", "security_officer", "recipient_contact", "storage_chain", "onward_provision")],
    ],
    "sections": [
        {
            "unless": {"source_country": "CN"},
            "heading": "一、缔约主体、定义与合同标的（参照欧盟 SCC 2021/914 第一部分）",
            "blocks": [
                {
                    "type": "text",
                    "text": "本《汽车数据跨境传输标准合规合同》由数据发出方（数据控制者/数据输出方）：{{company_name}} 与境外数据接收方（数据输入方）：{{recipient}} 依法订立。本合同适用于双方围绕【{{scenario}}】业务（适用车型或车队：{{vehicle_model}}）将汽车及关联个人数据自{{source_country}}跨境传输至{{recipient_country}}之全部处理活动。本合同旨在通过具有强制法律约束力与第三方受益人效力的合同条款，确保跨境流转数据获得与发出国法律实质同等的保护水平。",
                },
            ],
        },
        {
            "unless": {"source_country": "CN"},
            "heading": "二、跨境传输详情与边界锁定（附录一）",
            "blocks": [
                {"type": "facts_table", "keys": ["data_types", "sensitive_pi", "data_volume", "purpose", "retention_and_blackbox"]},
                {
                    "type": "text",
                    "text": "其中涉及出境的敏感个人信息种类限定为：{{sensitive_pi_kinds}}。境外接收方确认并承诺，仅在实现本节所列既定汽车业务目的（{{purpose}}）所严格必需的最小范围内处理数据；未经数据发出方事先书面授权，严禁超出约定目的变更处理用途、扩大字段采集范围，或将数据挪用于商业营销画像及与约定业务无关的模型训练。",
                },
            ],
        },
        {
            "unless": {"source_country": "CN"},
            "heading": "三、双方权利与义务（权责条款）",
            "blocks": [
                {
                    "type": "checklist",
                    "items": [
                        "目的限制原则：接收方仅为本合同附录一明确载明的特定汽车业务目的处理跨境传输数据，不得将车辆运行轨迹、座舱交互或诊断日志挪作任何兼容范围之外的次生用途。",
                        "透明告知与副本提供：发出方负责在车机端及移动端完成分层隐私告知；数据主体提出请求时，双方应免费提供本合同条款副本（涉及商业秘密或核心安全架构配置的部分可依法脱敏遮蔽并附理由说明）。",
                        "数据准确性与最小必要原则：双方应在车端采集与网关预处理阶段落实字段级脱敏与降低定位精度要求，仅传输实现业务目的所必需的数据字段；接收方发现数据不准确或超出必要范围时，应立即通知发出方并予以更正或安全删除。",
                        "存储期限限定与到期安全删除：接收方仅可在附录一约定的留存周期内存储数据；保存期届满或本合同终止后，接收方须按发出方书面指令限期返还全部数据或执行安全彻底删除（含备份数据），并出具书面删除证明。",
                        "全链路安全防护与泄露应急通报：接收方须持续落实不低于附录二标准的技术与组织安全措施；一旦发生或合理怀疑发生数据篡改、破坏、泄露或未授权访问事件，接收方须第一时间书面通报发出方，全力配合溯源止损并依法协助向主管监管机关及受影响数据主体履行通知义务。",
                        "敏感个人数据强化管控：针对精确地理轨迹、车外高清人脸车牌影像或座舱生物识别等敏感数据，接收方须实施独立加密存储、细粒度双人审批访问控制及全量操作留痕。",
                        "后续再转移限制与连带合同约束：未经发出方事先逐案书面授权，接收方不得将任何跨境数据向第三国或境外第三方再转移；经书面同意再转移的，接收方须确保第三方签署保护标准不低于本合同的书面协议，并对第三方的履约行为向发出方承担连带责任。",
                        "数据主体法定权利协同响应：接收方应建立专门技术接口与内控流程，协助发出方在法定期限内响应车主、乘员等数据主体提出的查阅、复制、可携、更正、删除、限制处理及撤回同意请求。",
                        "处理记录留存与独立合规审计：接收方应完整留存跨境数据处理与访问日志，定期向发出方提交合规履约报告，并无条件配合发出方或其委派的独立第三方审计机构开展现场或远程安全合规审计。",
                    ],
                }
            ],
        },
        {
            "unless": {"source_country": "CN"},
            "heading": "四、责任与免责条款",
            "blocks": [
                {
                    "type": "checklist",
                    "items": [
                        "第三方受益人权利与直接索赔：数据主体作为本合同项下明确受保护的第三方受益人，就任一方违反本合同核心保障条款对其合法权益造成的物质或精神损害，有权依法直接向发出方或接收方主张损害赔偿。",
                        "违约赔偿与全额追偿机制：任一方违反本合同义务给对方造成损失的，应承担全部违约赔偿责任；因接收方或其受托分处理者违约导致发出方遭受监管行政处罚、诉讼索赔或商誉损失的，发出方有权向接收方全额追偿（含罚款、赔偿金、审计费及合理律师费用）。",
                        "法定不可抗力免责边界：因自然灾害、战争等不能预见、不能避免且不能克服的客观不可抗力事件导致部分义务暂时无法履行的，受影响方须在事件发生后立即书面通知对方并采取一切合理减损补救措施，方可在不可抗力直接影响范围内主张部分免责。",
                        "核心安全义务不可免除：任何不可抗力或外部商业情势变更均不免除接收方对已接收数据的持续加密保密义务、安全事件即时通报义务以及数据销毁返还义务。",
                        "重大过错禁止免责与限责无效：因任一方故意、欺诈或重大过失导致汽车数据大规模泄露、非法交易或违规再转移的，违约方不得援引任何免责事由，亦不得通过任何补充商业协议限制或免除其对数据主体依法承担的法定赔偿责任。",
                    ],
                }
            ],
        },
        {
            "unless": {"source_country": "CN"},
            "heading": "五、属地法律冲突审查与公权力调取合规约束（参照欧盟 SCC 第 14–15 条）",
            "blocks": [
                {
                    "type": "checklist",
                    "items": [
                        "双重合规与高标准优先：双方均须严格遵守各自所在法域的数据保护、网络安全及智能网联汽车行业监管法规；两地保护要求存在差异时，在不违背属地强制性禁止规范的前提下按保护标准更严格者执行。",
                        "前置行政程序协同配合：本次跨境传输在发出国或接收国涉及出境安全评估、标准合同备案、事先授权或属地登记的，发出方应在传输启动前依法办结，接收方须如实提供主体资信、技术架构及安全认证证明文件予以配合。",
                        "接收国法域变更即时预警：接收方确认在签署本合同时无理由相信接收国现行法律与执法实践会妨碍其履行本合同义务；合同履行期间若接收国法律修订或监管实践变化可能导致合同保护落空，接收方须立即书面通知发出方，发出方有权立即暂停数据流转直至增补有效技术措施或单方解除本合同。",
                        "公权力机关调取合法性抗辩与最小化披露：接收方收到接收国行政执法、司法或国家安全机关要求调取本合同项下跨境数据的指令时，须在法律允许的最大范围内立即通知发出方；同时严格审查该调取指令是否具备明确法定权限与正当程序，对超越权限或违反国际法义务的宽泛无针对性的调取要求依法提出法律异议与上诉，并在确须依法配合时仅提供指令所严格必需的最小范围数据并全程留档备查。",
                    ],
                }
            ],
        },
        {
            "unless": {"source_country": "CN"},
            "heading": "六、技术与组织安全保护措施清单（附录二）",
            "blocks": [
                {
                    "type": "text",
                    "text": "双方围绕车载采集脱敏、跨境信道加密、云端存储隔离、密钥主权管控、访问权限审计及安全事件应急响应部署的具体技术与组织措施如下：{{risk_measures}}",
                },
            ],
        },
        {
            "unless": {"source_country": "CN"},
            "heading": "七、传输暂停、合同终止与数据清退",
            "blocks": [
                {
                    "type": "text",
                    "text": "若接收方实质性或持续性违反本合同义务，或因接收国法律变更导致实质同等保护水平无法维持且补充措施无法消除风险的，发出方有权立即单方切断跨境传输链路并书面通知解除本合同。合同解除或期满终止后，接收方须立即停止一切数据处理活动，按发出方选择将全部存量及备份数据安全返还或彻底不可逆销毁，在销毁完成前仍须继续履行本合同全部安全保护义务。",
                },
            ],
        },
        {
            "unless": {"source_country": "CN"},
            "heading": "八、适用法律与争议解决管辖",
            "blocks": [{"type": "text", "text": "本合同的订立、效力、解释、履行及争议解决约定如下：{{governing_law}}"}],
        },
        *_OFFICIAL_CN_CONTRACT_SECTIONS,
        {
            "unless": {"source_country": "CN"},  # 官方范本保持原样：平台补的依据不放进合同正文
            "heading": "九、适用的法规依据",
            "blocks": [{"type": "citations", "dimensions": ["D3", "D4", "D5"], "limit": 10}],
        },
    ],
}

REMEDIATION = {
    "revision": 9,  # 9：统一流向表头为数据发出国与数据接收国；8：统一平实严谨律所文风
    "title": "合规问题整改跟踪报告",
    "facts": [
        *_COMMON,
        {
            "key": "issues",
            "label": "发现的合规问题",
            "question": "除平台按规则列出的事项外，企业自查还发现了哪些合规问题（每条一句话，例如“原始摄像头图像未脱敏直接上传”）？没有写“无”。",
            "why": "企业自查发现的问题一并列入问题清单。",
        },
        {
            "key": "measures",
            "label": "整改措施与责任人",
            "question": "针对这些问题，已计划或已采取哪些整改措施，由谁负责，何时完成？",
            "why": "整改跟踪需要措施、责任人和时限。",
        },
        {
            "key": "verification",
            "label": "完成验证方式",
            "question": "整改完成后如何验证（复测、审计、第三方检测）？",
            "why": "整改需要有完成验证。",
        },
    ],
    "sections": [
        {
            "heading": "一、整改治理范围、法定合规要求与内控目标",
            "blocks": [
                {
                    "type": "text",
                    "text": "本报告围绕 {{company_name}} 开展自{{source_country}}至{{recipient_country}}的【{{scenario}}】跨境数据流转业务（适用车型或车队：{{vehicle_model}}）所识别的合规差距展开持续跟踪管理。报告将发出国数据跨境流动监管前置程序义务、海外属地市场准入行政许可要求、风险评估识别出的待治理高风险事项以及企业研发法务内控自查发现项统一纳入整改台账，建立从问题定位、法规依据核对、技术与制度整改分派到客观证据验收的全流程责任机制。",
                },
                {"type": "slot", "name": "route"},
            ],
        },
        {
            "heading": "二、合规差距识别、成因剖析与问题台账",
            "blocks": [
                {
                    "type": "text",
                    "text": "结合法定出境路径研判结论、目标市场属地监管准入清单及企业车端与云端架构自查记录，梳理形成本项目当前尚待完成整改闭环的合规差距事项台账，并同步记录跨境安全风险在整改前后的等级变化与已可控闭环核销情况：",
                },
                {"type": "slot", "name": "issues"},
                {"type": "slot", "name": "risk_evolution"},
            ],
        },
        {
            "heading": "三、参考条文与监管法源依据",
            "blocks": [
                {
                    "type": "text",
                    "text": "上述各项合规整改任务直接对标数据发出国与接收国关于个人数据及汽车数据采集处理、车机告知同意、跨境传输保障及全生命周期安全防护的强制性法律规范，核心法源条款汇编如下：",
                },
                {"type": "citations", "dimensions": ["D1", "D2", "D4", "D5"], "limit": 10},
            ],
        },
        {
            "heading": "四、车云技术改造、制度完善措施与权责配置",
            "blocks": [
                {
                    "type": "text",
                    "text": "针对问题台账所列各项合规缺口，企业已统筹智能网联研发部门、信息安全团队、法务合规部及海外属地运营主体明确具体技术改造路径、合同补充签署安排、责任部门与完成时限，具体整改落实方案如下：{{measures}}",
                }
            ],
        },
        {
            "heading": "五、分阶段整改实施路线图与上线门禁约束",
            "blocks": [
                {
                    "type": "text",
                    "text": "遵循法定前置行政程序优先办结、车端边缘脱敏与跨境加密网关同步加固、合同与内控台账全面归档的治理原则，确立以下分阶段实施路线图，并将关键整改项完成情况设为实车OTA推送与跨境链路放通的刚性合规门禁：",
                },
                {"type": "slot", "name": "fix_route"},
            ],
        },
        {
            "heading": "六、闭环验收核验标准与长效合规审计机制",
            "blocks": [
                {
                    "type": "text",
                    "text": "各项整改措施执行完毕后，须通过抓包流量核验、车机交互走查、权限配置复测或专项合规审计形成可追溯的客观验收留痕，严防整改流于形式或版本迭代导致风险回潮。本项目确定的闭环验收与长效监督机制如下：{{verification}}",
                }
            ],
        },
    ],
}

ADMISSION = {
    "revision": 8,  # 8：准入说明法域标签改为准入适用法域，消除海外双端路由歧义；7：统一流向表头为数据发出国与数据接收国
    "title": "海外属地市场准入材料清单及申报说明报告",
    # 准入按事项类型取申请主体（见 doc_sections._applicant），这里的法域是准入办理及业务关联地
    "facts": [
        {**f, "label": {"country_name": "准入适用法域", "company_name": "企业名称"}.get(f["key"], f["label"])} for f in _COMMON
    ],
    "sections": [
        {
            "heading": "一、海外属地监管体系与准入事项适用性论证",
            "blocks": [
                {
                    "type": "text",
                    "text": "本报告面向{{company_name}}围绕{{country_name}}开展【{{scenario}}】业务（适用车型或车队：{{vehicle_model}}，跨境数据链路关联{{source_country}}至{{recipient_country}}），系统梳理业务开展法域在整车型式认证、车载无线电与通信终端设备核准、智能网联及自动驾驶道路测试授权、个人数据处理登记与跨境传输事先许可等领域的属地行政监管体系，逐项界定法定主管机关、适格当地申请主体资格及前置触发条件。",
                },
                {"type": "facts_table", "keys": ["country_name", "scenario", "vehicle_model", "company_name"]},
                {"type": "slot", "name": "admission_items"},
            ],
        },
        {
            "heading": "二、法定申报材料体系与编制实务规范",
            "blocks": [
                {
                    "type": "text",
                    "text": "依据属地现行生效法规条文及主管机关发布的官方制式表格规范，将各项属地准入所需的正式申请书、官方登记/授权表格、技术检测报告及主体资信证明材料分类汇编如下，明确每份材料的法定编制口径与准备分工：",
                },
                {"type": "slot", "name": "admission_materials"},
            ],
        },
        {
            "heading": "三、受理机构管辖、法定语种与卷宗递交规程",
            "blocks": [
                {
                    "type": "text",
                    "text": "企业在向属地主管机关（适用法域：{{country_name}}）正式递交申报卷宗前，须严格核验各受理机构的管辖权限、法定官方申报语种、外文证明文件公证认证及官方译文要求，以及线上政务门户或线下窗口递交规程：",
                },
                {"type": "slot", "name": "admission_filing"},
            ],
        },
        {
            "heading": "四、属地行政审批全流程与前后端业务衔接节点",
            "blocks": [
                {
                    "type": "text",
                    "text": "结合目标国法定行政审查环节与整车出口清关、车联网实网激活用车的先后依赖关系，将各项属地准入事项拆解为可执行的阶段化办理节点与批文留档要求：",
                },
                {"type": "slot", "name": "admission_route"},
            ],
        },
        {
            "heading": "五、参考条文与官方原文溯源核验",
            "blocks": [
                {
                    "type": "text",
                    "text": "本报告所列准入事项、材料要求及审查程序均直接溯源至目标国官方法规条文，以下逐条列出中文译文与官方原文供企业法务、认证工程师及当地授权代表逐字核验：",
                },
                {"type": "slot", "name": "admission_clauses"},
            ],
        },
    ],
}

FORM_TEMPLATES = [
    ("declaration", "汽车数据出境评估申报表", DECLARATION),
    ("risk_report", "汽车数据跨境风险评估报告", RISK_REPORT),
    ("contract", "汽车数据跨境传输合规合同", CONTRACT),
    ("remediation", "合规问题整改跟踪报告", REMEDIATION),
    ("admission", "海外属地市场准入材料清单及申报说明报告", ADMISSION),
]
