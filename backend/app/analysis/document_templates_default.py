# ruff: noqa: E501  模板正文是长句数据，不按代码行宽折行
"""Starting templates for the standard compliance documents (requirement 3.2, items 6–8).

They are data, loaded into the template table on first use and edited there afterwards (each edit
is a new version). Section structure follows the public methodology for each assessment: the DPIA
elements of GDPR Article 35(7); the three-part legitimate interests test (purpose, necessity,
balancing); the transfer-impact steps of the EDPB recommendations on supplementary measures.
Nothing here depends on a country: the destination, the evidence and the legal basis come from the
assessment the document is made for.
"""

_COMMON_FACTS = [
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
        "question": "请问受评的车型或车队名称是什么？",
        "why": "文件需要写明评估对象。",
    },
    {
        "key": "scenario",
        "label": "业务场景",
        "question": "请问涉及的业务场景是什么（例如道路测试、远程诊断）？",
        "why": "文件需要写明处理发生的场景。",
    },
    {
        "key": "company_name",
        "label": "数据发出方（数据控制者）",
        "question": "请问承担本次数据处理责任的公司全称是什么？",
        "why": "文件需要写明责任主体。",
    },
]

def _q(key, label, question, why, *, short=False, optional=False, source=None):
    fact = {"key": key, "label": label, "question": question, "why": why, "short": short, "optional": optional}
    if source:
        fact["from"] = source
    return fact


# DPIA：对标欧盟数据保护委员会（EDPB）《数据保护影响评估（DPIA）标准范本》（2026 年第 1.0 版）0–6 七章“表主文辅”结构，
# 同时兼顾网信办《个人信息保护影响评估报告（出境版）》全章节论证要素，按真实项目事实与适用法域动态生成。
DPIA = {
    "revision": 15,  # 15：按 EDPB 2026 0–6 章精细隔离章节法理职责（杜绝跨章复读数据项清单与重复风险链条）
    "title": "数据保护影响评估（DPIA）",
    "facts": [
        *_COMMON_FACTS,
        _q("exterior_video_and_gnss", "处理的数据类型与匿名化措施", "处理哪些数据（例如车外视频、定位轨迹、座舱数据、车辆运行数据）？是否做了脱敏或匿名化？", "说明处理的性质与范围。", source="exterior_video_and_gnss"),
        _q("data_subjects", "涉及的人群与规模", "处理涉及哪些人群（车主、乘客、路人、员工等），大致有多少人？", "说明受影响的人群。"),
        _q("purpose", "处理目的", "采集和处理这些数据的具体目的是什么（例如道路测试、远程诊断、产品研发）？", "目的决定处理是否必要、是否相称。"),
        _q("data_flow", "数据流转过程", "数据从车端采集开始，经过哪些环节、哪些主体、哪些地点，最后存放在哪里？", "用来画数据处理流图。"),
        _q("lawful_basis", "处理的合法性基础", "处理依据哪项合法性基础（取得同意、合同所必需、合法利益、履行法定义务等）？", "必要性与合理性论证的起点。"),
        _q("consent_and_notice", "告知与同意", "如何向车主、乘客和路人告知并取得同意？是否任命数据保护官？", "评估对数据主体权利的保障。", source="consent_and_notice"),
        _q("retention_and_blackbox", "保存期限与存放位置", "数据保存多久、存放在哪里、到期如何销毁？", "评估存储限制与安全措施。", source="retention_and_blackbox"),
        _q("risk_rating", "主要风险及其可能性、严重程度与应对措施", "本次传输的主要风险有哪些？每项风险写一行：风险内容、你判断的可能性和严重程度（低/中/高），以及至少一项应对措施；可以写责任人和完成时限。", "风险分级需要企业自己的判断；每个风险至少一项措施。"),
        _q("risk_measures", "已采取的保护措施与技术手段", "目前已采取哪些技术和管理措施来降低风险（加密、访问控制、审计、脱敏等）？", "论证保护措施。"),
        _q("dpo_opinion", "咨询意见", "是否征求了数据保护官（或法务、合规负责人）的意见？意见是什么？", "评估要求记录咨询意见。", optional=True),
    ],
    "narrative": [
        {
            "key": "overview",
            "role": "governance_scope",
            "side": "source",
            "dimensions": ["D1", "D2"],
            "heading": "0. 处理活动概览与评估治理档案",
            "brief": "紧承概览档案开展两段精炼的法理定性与治理收束：明确本单位作为数据控制者的法定主体地位与境外接收方的角色边界，阐明针对受评车型或车队设立的跨部门前置审查关口与申报留痕机制；严禁罗列具体传感器字段清单，严禁展开加密算法细节，不提前下达全篇评估结论。",
            "facts": ["company_name", "country_name", "vehicle_model", "scenario", "data_subjects", "purpose"],
        },
        {
            "key": "systematic_flow",
            "role": "systematic_description",
            "side": "source",
            "dimensions": ["D2", "D3", "D5"],
            "heading": "1. 数据处理活动与跨境链路系统性描述",
            "brief": "将拟处理及跨境流转的数据逐项拆解为结构化数据项清单并梳理端到端流转环节后，用两段精练文字总括车载事件触发条件、境内前置去标识化关口、跨境受控传输与境外隔离存储销毁的时空边界，并对数据是否落入法定敏感或特殊类别作出定性；严禁在本章提前展开四类替代方案比选，不提前下达全篇评估结论。",
            "facts": ["exterior_video_and_gnss", "data_flow", "retention_and_blackbox"],
            "flow": True,
            "field_table": True,
        },
        {
            "key": "legal_basis",
            "role": "lawfulness_and_principles",
            "side": "source",
            "dimensions": ["D4", "D1", "D3", "D5"],
            "heading": "2. 处理活动合法性、基本原则与保障措施评估",
            "brief": "紧扣数据发出国现行法规条款撰写两段法理涵摄分析：一段剖析本次车载业务及跨境提供所依据的合法性事由适格性、分层告知与独立授权（禁止捆绑强迫）的可核验性；另一段论证源端默认隐私保护、目的限定与期限控制如何落实法定基本原则；严禁逐项背诵第一章数据字段名称，不提前下达全篇评估结论。",
            "facts": ["lawful_basis", "consent_and_notice", "purpose", "risk_measures", "retention_and_blackbox"],
        },
        {
            "key": "necessity",
            "role": "necessity_proportionality",
            "side": "source",
            "dimensions": ["D2", "D4", "D5"],
            "heading": "3. 必要性与相称性专项评估",
            "brief": "运用反事实排他推理撰写两段深度相称性论证：一段对比阐明为何纯车端离线运算或完全剥离车辆关联标识无法达成同等行车安全验证与定向故障溯源目标、为何事件触发式白名单已将处理压至最小必要边界；另一段权衡业务安全收益与自然人隐私预期，明确维持相称性的刚性生效前提；严禁整串照抄字段清单，不提前下达全篇评估结论。",
            "facts": ["purpose", "exterior_video_and_gnss", "data_subjects"],
        },
        {
            "key": "risk",
            "role": "risk_mitigation",
            "side": "source",
            "dimensions": ["D5", "D4", "D6"],
            "heading": "4. 自然人权利与自由风险评估及量化管理",
            "brief": "围绕企业逐项识别的具体跨境风险撰写两段针对性阻断实效分析：逐一对应每项风险的触发节点与对自然人权益的潜在危害，剖析已部署的二次质检、审批门禁、电子围栏或最小权限审计如何从源头消除或实质压降发生概率与危害面；企业尚未填写风险的写“风险清单【待补充】”，不重述前文背景，不提前下达全篇综合放行结论。",
            "facts": ["risk_rating", "risk_measures", "data_subjects"],
            "risks": True,
        },
        {
            "key": "stakeholders",
            "role": "stakeholders_and_dpo",
            "side": "source",
            "dimensions": ["D4", "D5"],
            "heading": "5. 利益相关方参与及数据保护官审查意见",
            "brief": "聚焦利益相关方权利救济与内控独立监督撰写两段论述：一段阐明车主、乘员或受影响自然人跨法域行使查阅、更正、删除、撤回同意及向监管机构申诉的境内受理与境外合同协同机制；另一段阐明数据保护官或合规负责人的独立审查意见如何转化为传输启动前置条件与偏离叫停约束；不展开外国公权力调取抗辩，不提前下达全篇综合结论。",
            "facts": ["consent_and_notice", "dpo_opinion"],
        },
        {
            "key": "conclusion",
            "role": "conclusive_ruling",
            "side": "source",
            "dimensions": ["D3", "D4", "D5"],
            "heading": "6. 评估结论、放行决定与持续复评机制",
            "brief": "撰写一至两段凝练权威的终局审批裁断（严禁重新铺陈第四章风险成因或罗列传感器清单）：依据发出国法定跨境传输与安全条款，明确核定剩余风险可接受性及‘有条件通过’（或存在缺失项时‘暂缓放行’）的正式决定，逐一锁定法定前置许可/备案完结、单路径异常熔断转本地隔离运行、期满含备份彻底销毁三项硬性执行门禁，以及架构变更、规模跃升或法规修订时强制重评的闭环规则。",
            "facts": ["purpose", "risk_rating", "dpo_opinion"],
        },
    ],
    "sections": [
        {
            "heading": "0. 处理活动概览与评估治理档案",
            "blocks": [
                {"type": "facts_table", "keys": ["company_name", "source_country", "recipient_country", "vehicle_model", "scenario"]},
                {"type": "slot", "name": "dpia_ch0_overview"},
            ],
        },
        {"narrative": True},
    ],
}

# LIA：合法利益的三步测试写成论证——目的测试 → 必要性测试 → 平衡测试 → 结论。
LIA = {
    "revision": 15,  # 15：精细隔离三步测试与终局裁断职责，消除段落机械复读
    "title": "合法利益评估（LIA）",
    "facts": [
        *_COMMON_FACTS,
        _q("purpose", "合法利益（目的）", "你主张的合法利益是什么（例如保障道路测试安全、改进自动驾驶功能）？", "目的测试：利益本身必须合法、具体。"),
        _q("exterior_video_and_gnss", "处理的数据", "为实现这一利益处理哪些个人数据？", "说明数据范围。", source="exterior_video_and_gnss"),
        _q("necessity", "必要性", "不处理这些个人数据，能否达到同样目的？有没有侵害更小的替代办法？", "必要性测试。"),
        _q("consent_and_notice", "告知与反对权", "是否向数据主体告知了处理，并提供了反对或退出的办法？", "平衡测试：合理预期与可控性。", source="consent_and_notice"),
        _q("risk_measures", "对个人影响的缓解措施", "为降低对个人的影响，已采取哪些措施（脱敏、限期保存、访问控制等）？", "平衡测试：缓解措施。"),
        _q("retention_and_blackbox", "保存期限", "数据保存多久？", "平衡测试：影响的持续时间。", source="retention_and_blackbox"),
    ],
    "narrative": [
        {
            "key": "purpose",
            "role": "lawfulness_and_principles",
            "side": "source",
            "dimensions": ["D4", "D2"],
            "heading": "一、目的正当性与合法利益识别测试",
            "brief": "对标合法利益评估第一步（目的测试）撰写两段精炼法理论证：结合车载业务场景剖析企业主张的具体运行安全或质量履约利益之真实性、合法性与现实紧迫性，并严格对照数据发出国现行法规界定合法利益事由的法定适用地位；不提前展开技术细节与最终结论。",
            "facts": ["company_name", "country_name", "vehicle_model", "scenario", "purpose"],
        },
        {
            "key": "necessity",
            "role": "necessity_proportionality",
            "side": "source",
            "dimensions": ["D2", "D4"],
            "heading": "二、处理手段必要性与最小影响替代测试",
            "brief": "对标合法利益评估第二步（必要性测试）撰写两段反事实排他论证：阐明为何纯离线运算、合成数据或完全不处理该范围数据无法达成同等安全运维目标，以及事件触发与字段白名单如何将侵入性压至最小必要边界；不提前输出最终结论。",
            "facts": ["purpose", "exterior_video_and_gnss", "necessity"],
        },
        {
            "key": "balance",
            "role": "stakeholders_and_dpo",
            "side": "source",
            "dimensions": ["D4", "D5"],
            "heading": "三、企业利益与数据主体基本权利平衡测试",
            "brief": "对标合法利益评估第三步（平衡测试）撰写两段权衡分析：综合购车用车或道路通行场景下自然人的合理预期、分层告知与便捷反对/退出通道的可操作性、源端脱敏及最短留存窗口的对冲作用，论证企业正当利益为何未凌驾于个人基本权利之上；不提前输出第四章综合结论。",
            "facts": ["consent_and_notice", "risk_measures", "retention_and_blackbox"],
        },
        {
            "key": "conclusion",
            "role": "conclusive_ruling",
            "side": "source",
            "dimensions": ["D4"],
            "heading": "四、三步测试综合判定结论与附加保障条件",
            "brief": "撰写一至两段凝练终局判定（不重复铺陈前三章细节）：明确合法利益在发出国法域下能否单独或辅助作为合法性依据，并锁定维持权利平衡必须持续满足的源端脱敏、显著告知、便捷异议响应及变更重评刚性条件。",
            "facts": ["purpose", "necessity", "consent_and_notice"],
        },
    ],
    "sections": [
        {
            "heading": "评估对象与编制基准",
            "blocks": [{"type": "facts_table", "keys": ["company_name", "source_country", "recipient_country", "vehicle_model", "scenario"]}],
        },
        {"narrative": True},
    ],
}

# TIA：传输影响评估的六步（对标欧盟 EDPB 第 01/2020 号补充措施建议书六步法）。
TIA = {
    "revision": 19,  # 19：精细隔离 EDPB 六步法各章法理职责，消除首尾章节重复叙述
    "title": "数据跨境传输影响评估（TIA）",
    "facts": [
        *_COMMON_FACTS,
        _q("cross_border_mechanism", "接收方与传输保障机制", "数据传输到哪个国家或地区的哪家接收方？采用什么保障机制（标准合同、认证、属地许可等）？", "确认传输的去向和所依据的传输工具。", source="cross_border_mechanism"),
        _q("exterior_video_and_gnss", "传输的数据类型", "跨境传输哪些数据（视频、轨迹、车辆状态等），是否已脱敏？", "确认传输的数据范围。", source="exterior_video_and_gnss"),
        _q("retention_and_blackbox", "接收方的保存与访问安排", "数据在接收方保存多久、哪些人员可以访问？", "评估公权力调取的可能与影响。", source="retention_and_blackbox"),
        _q("supplementary_measures", "补充保护措施", "除传输工具外，还采取了哪些补充措施（端到端加密、密钥由境内持有、假名化等）？", "补充措施是否足以弥补保护水平的差距。"),
        _q("reevaluation", "复评安排", "多久复评一次？哪些情形会触发复评？", "定期复评。", optional=True),
    ],
    "narrative": [
        {
            "key": "transfer",
            "role": "systematic_description",
            "dimensions": ["D3"],
            "heading": "一、跨境传输全貌与车云流转链路梳理",
            "brief": "对标传输影响评估第一步撰写两段精练跨境链路界定：厘清控制者向境外哪家接收主体传输何种敏感等级的车载数据、发出国前置去标识化边界、境外存储节点留存周期与受控访问人数范围，明确不含无关再转移；不提前展开法域调取对抗与最终结论。",
            "side": "source",
            "facts": ["company_name", "country_name", "vehicle_model", "scenario", "recipient_country", "exterior_video_and_gnss", "retention_and_blackbox"],
        },
        {
            "key": "tool",
            "role": "lawfulness_and_principles",
            "dimensions": ["D3", "D1"],
            "heading": "二、所依托跨境传输法定机制及其适格性论证",
            "brief": "对标传输影响评估第二步撰写两段法定工具适格性论证：结合跨境流转路线与企业选定的跨境传输机制，紧扣发出国现行法规条款剖析所依托的事先授权、标准合同备案或安全评估工具之法定门槛、生效要件及双方核心义务边界；不提前输出最终结论。",
            "side": "source",
            "facts": ["cross_border_mechanism", "recipient_country", "country_name"],
        },
        {
            "key": "law",
            "role": "recipient_and_remedy",
            "dimensions": ["D3", "D6", "D1"],
            "heading": "三、接收国法律与实践评估",
            "brief": "对标传输影响评估第三步撰写两段接收方法域深度剖析：围绕接收国专门数据保护立法与独立监管机构、当地公权力机关调取数据的法定程序与比例原则边界、双边管辖冲突阻断及数据主体行政司法救济通道，评估当地法制环境是否会实质削弱传输保障工具效力；注意区分接收国向第三国再转移规定与本次传入条件，不提前输出最终结论。",
            "side": "recipient",
            "facts": ["recipient_country", "cross_border_mechanism"],
        },
        {
            "key": "measures",
            "role": "safeguards_lifecycle",
            "dimensions": ["D3", "D5"],
            "heading": "四、技术、合同与组织补充保护措施有效性论证",
            "brief": "对标传输影响评估第四步撰写两段补充措施实效推演：论证端侧去标识化、双向鉴权加密通道、发出国境内独立持有还原密钥、外部调取合法性抗辩通报条款及最小权限审计如何协同阻却境外未授权明文访问与越权调取；不提前输出最终结论。",
            "side": "source",
            "facts": ["supplementary_measures", "exterior_video_and_gnss"],
        },
        {
            "key": "review",
            "role": "governance_scope",
            "dimensions": ["D3", "D7"],
            "heading": "五、法定程序性前置步骤与定期复评机制",
            "brief": "对标传输影响评估第五步及动态治理要求撰写两段程序闭环说明：列明正式启动传输前须向发出国主管机关办结的申报审批、备案或许可步骤，以及定期合规审计与遇接收国修法或调取实践变化立即暂停重评的治理机制；不提前输出最终结论。",
            "side": "source",
            "facts": ["reevaluation", "country_name", "cross_border_mechanism"],
        },
        {
            "key": "conclusion",
            "role": "conclusive_ruling",
            "dimensions": ["D3"],
            "heading": "六、实质同等保护水平综合判定结论",
            "brief": "撰写一至两段凝练终局裁断（不重述前文链路流水账）：综合法定传输工具与境内密钥受控、源端去标识化等补充措施的叠加效力，明确判定跨境数据在接收国能否持续获得与发出国法律要求实质相当的保护水平，并锁定前置行政程序完结、单路径异常熔断与触发重评的刚性条件。",
            "side": "source",
            "facts": ["cross_border_mechanism", "supplementary_measures", "reevaluation"],
        },
    ],
    "sections": [
        {
            "heading": "评估对象与编制基准",
            "blocks": [{"type": "facts_table", "keys": ["company_name", "source_country", "recipient_country", "vehicle_model", "scenario"]}],
        },
        {"narrative": True},
    ],
}

from app.analysis.document_templates_forms import FORM_TEMPLATES  # noqa: E402

# 需求 3.2 的八类文件：前五类是申报、报告、合同、整改、准入，后三类是 DPIA、LIA、TIA
DEFAULT_TEMPLATES = [
    *FORM_TEMPLATES,
    ("dpia", "数据保护影响评估（DPIA）", DPIA),
    ("lia", "合法利益评估（LIA）", LIA),
    ("tia", "数据跨境传输影响评估（TIA）", TIA),
]
