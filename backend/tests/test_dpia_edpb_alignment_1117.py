from __future__ import annotations

import io
import json
import zipfile
from types import SimpleNamespace
from xml.etree import ElementTree as ET

from app.analysis import clause_gate, doc_sections, documents_router, risk_items
from app.analysis.document_templates import render
from app.analysis.document_templates_default import DPIA, LIA, TIA
from app.analysis.official_eu_edpb_dpia import _CJK_RE, render_edpb_2026_docx

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def test_clean_margin_numbers_preserves_chinese_measures_and_strips_gazette_margins() -> None:
    assert clause_gate.clean_margin_numbers("保存不超过 5 年。") == "保存不超过 5 年。"
    assert clause_gate.clean_margin_numbers("共识别出 5 项，须在 10 个工作日内完成，留存 30 天。") == (
        "共识别出 5 项，须在 10 个工作日内完成，留存 30 天。"
    )
    assert clause_gate.clean_margin_numbers("组织应当保护数据。 15 控制者须履行通知义务。") == (
        "组织应当保护数据。控制者须履行通知义务。"
    )


def test_dpia_0_to_6_table_first_and_below_table_prose_layout() -> None:
    assert [s["heading"] for s in DPIA["narrative"]] == [
        "0. 处理活动概览与评估治理档案",
        "1. 数据处理活动与跨境链路系统性描述",
        "2. 处理活动合法性、基本原则与保障措施评估",
        "3. 必要性与相称性专项评估",
        "4. 自然人权利与自由风险评估及量化管理",
        "5. 利益相关方参与及数据保护官审查意见",
        "6. 评估结论、放行决定与持续复评机制",
    ]

    risk_json = json.dumps(
        [
            {
                "id": "r1",
                "risk": "跨境诊断报文遭中间人截获",
                "init_likelihood": "中",
                "init_severity": "高",
                "likelihood": "低",
                "severity": "中",
                "measures": "车端去标识化与双向mTLS加密专用通道",
                "owner": "车云安全组",
                "due": "2026-12-01",
            }
        ],
        ensure_ascii=False,
    )
    values = {
        "country_name": "突尼斯",
        "source_country": "突尼斯",
        "recipient_country": "法国",
        "vehicle_model": "QH-A8",
        "scenario": "自动驾驶道路测试",
        "company_name": "启衡智行科技有限公司",
        "exterior_video_and_gnss": "车外环视视频（车端边缘实时模糊人脸与车牌）及去标识化定位坐标",
        "data_subjects": "测试安全员及道路偶发行人",
        "purpose": "验证自动驾驶感知算法在复杂路况下的安全性",
        "data_flow": "车载域控制器采集并脱敏后经加密通道传至法国研发节点",
        "lawful_basis": "安全员签署协议及车外瞬时脱敏正当利益",
        "consent_and_notice": "车机端与测试车外部标识分层告知，设数据保护官邮箱",
        "retention_and_blackbox": "保存于法国受控云端90天，期满含备份彻底安全删除",
        "risk_rating": risk_json,
        "risk_measures": "车端边缘脱敏、双向证书加密传输、最小权限访问控制与全量审计日志",
        "dpo_opinion": "数据保护官审查确认边缘脱敏有效，同意在办结突尼斯事先授权后启动。",
    }
    raw_paper = [
        {"type": "heading", "text": "0. 处理活动概览与评估治理档案"},
        {"type": "paragraph", "text": "控制者已建立跨部门数据合规内控机制。[1]"},
        {"type": "heading", "text": "1. 数据处理活动与跨境链路系统性描述"},
        {"type": "paragraph", "text": "端侧控制器先行完成去标识化再经加密链路传输。[1]"},
        {"type": "paragraph", "text": "数据处理流程图（按数据流动顺序，箭头表示流向）："},
        {"type": "paragraph", "text": "车端采集脱敏 → 跨境加密传输 → 法国节点存储销毁"},
        {"type": "table", "head": ["环节", "处理的数据", "地点与主体", "保护措施"], "rows": [["车端采集脱敏", "环视帧", "突尼斯", "人脸车牌模糊"]]},
        {"type": "table", "head": ["序号", "数据项名称", "内容描述", "出境必要性", "示例", "备注"], "rows": [["1", "脱敏感知帧", "车外路况", "算法安全验证", "模糊图像", "加密传输"]]},
        {"type": "heading", "text": "2. 处理活动合法性、基本原则与保障措施评估"},
        {"type": "paragraph", "text": "各子场景均严格对应法定合法性事由并落实分层告知。[1]"},
        {"type": "heading", "text": "3. 必要性与相称性专项评估"},
        {"type": "paragraph", "text": "纯本地算力无法完成跨车队边角工况安全回归验证。[1]"},
        {"type": "heading", "text": "4. 自然人权利与自由风险评估及量化管理"},
        {"type": "paragraph", "text": "加密传输与源端脱敏实质切断了跨网泄露向自然人损害传导的链条。[1]"},
        *risk_items.blocks(risk_json, ""),
        {"type": "heading", "text": "5. 利益相关方参与及数据保护官审查意见"},
        {"type": "paragraph", "text": "数据保护官意见已全额采纳并建立跨境权利协同接口。[1]"},
        {"type": "heading", "text": "6. 评估结论、放行决定与持续复评机制"},
        {"type": "paragraph", "text": "在办结法定事先授权并维持单路径暂停熔断机制的前提下核准放行。[1]"},
        {"type": "heading", "text": "参考条文"},
        {"type": "table", "head": ["编号", "法规与条款", "条文摘录"], "rows": [["[1]", "突尼斯个人数据保护组织法 第50条", "跨境传输须经授权。"]]},
    ]

    fake_task = SimpleNamespace(country_code="FR", scenario_type="autonomous_trial", scenario_label="自动驾驶道路测试", facts_json={})
    route = {"route": "exception", "text": "向突尼斯国家个人数据保护局（INPDP）申请事先授权后传输"}
    slots = {
        "dpia_ch0_overview": [
            {"type": "paragraph", "text": "境外数据接收主体、履约约束与运行周期档案表（对应标准范本 0.2、0.4）："},
            {"type": "table", "head": ["核查维度", "本项目核实内容"], "rows": [["境外接收主体", "法国研发节点"]]},
            {"type": "paragraph", "text": "处理活动边界界定说明：仅限受评测试车队。"},
        ],
        "dpia_special_data_note": [
            {"type": "paragraph", "text": "特殊类别与敏感个人数据边界判定说明：车端实时模糊且不提取生物识别模板。"}
        ],
        "dpia_legal_basis": [
            {"type": "paragraph", "text": "分业务场景处理目的与合法性基础映射表（对应标准范本 2.1.a、2.1.b）："},
            {"type": "table", "head": ["细分处理场景与用途", "适用的法定合法性基础", "要件适格性与边界核验说明"], "rows": [["测试履约", "合同与同意", "适格"]]},
            {"type": "paragraph", "text": "合法利益三步平衡测试说明：目的正当、手段必要、车端实时遮蔽保障权益平衡。"},
        ],
        "safeguard_effectiveness": doc_sections.safeguard_adequacy_blocks(fake_task, "dpia", values, route),
        "necessity_comparison": doc_sections.necessity_alternatives_blocks(fake_task, "dpia", values),
        "dpia_stakeholders": doc_sections.dpia_stakeholders_blocks(fake_task, values),
        "verdict_decision": doc_sections.verdict_decision_blocks(fake_task, "dpia", values, route, missing_count=0),
    }

    paper = documents_router._promote_flow_and_field_tables(raw_paper, ("1.", "二、"), slots["dpia_special_data_note"])
    paper = documents_router._insert_at_section_top(
        paper, ("2.", "三、"), [*slots["dpia_legal_basis"], *slots["safeguard_effectiveness"]], "分业务场景处理目的与合法性基础映射表"
    )
    paper = documents_router._insert_at_section_top(paper, ("3.", "四、"), slots["necessity_comparison"], "必要性与最小侵害替代方案排他比选表")
    paper = documents_router._refresh_and_promote_dpia_risk_blocks(paper, values, draft=False)
    paper = documents_router._insert_at_section_top(paper, ("5.", "六、"), slots["dpia_stakeholders"], "利益相关方参与、数据保护官审查意见及权利保障核验表")
    paper = documents_router._insert_at_section_top(paper, ("6.", "七、"), slots["verdict_decision"], "结论性研判结果与放行条件核定表")

    rendered = render(DPIA, values, [], None, paper, draft=False, slots=slots, note="项目：突尼斯 → 法国")
    headings = [b["text"] for b in rendered.blocks if b["type"] == "heading"]
    assert headings == [
        "0. 处理活动概览与评估治理档案",
        "1. 数据处理活动与跨境链路系统性描述",
        "2. 处理活动合法性、基本原则与保障措施评估",
        "3. 必要性与相称性专项评估",
        "4. 自然人权利与自由风险评估及量化管理",
        "5. 利益相关方参与及数据保护官审查意见",
        "6. 评估结论、放行决定与持续复评机制",
        "参考条文",
    ]
    full_dump = "\n".join(
        b.get("text", "")
        or ("|".join(b.get("head", [])) + "\n" + "\n".join("|".join(r) for r in b.get("rows", [])))
        for b in rendered.blocks
    )
    # In Chapter 1: Data Items table -> Special-Category Determination Note -> Flow table -> Prose
    assert (
        full_dump.index("序号|数据项名称")
        < full_dump.index("特殊类别与敏感个人数据边界判定说明")
        < full_dump.index("环节|处理的数据")
        < full_dump.index("端侧控制器先行完成去标识化再经加密链路传输")
    )
    # In Chapter 2: Legal basis table -> LIA 3-step balancing note -> Safeguard adequacy table -> Prose
    assert (
        full_dump.index("细分处理场景与用途")
        < full_dump.index("合法利益三步平衡测试说明")
        < full_dump.index("合规控制维度|本项目已部署或拟部署措施")
        < full_dump.index("各子场景均严格对应法定合法性事由并落实分层告知")
    )
    # In Chapter 4: Quantitative 15/25 -> 3/25 scores + Residual Risk Conclusion BEFORE Chapter 4 prose
    assert "15/25分" in full_dump and "3/25分" in full_dump
    assert full_dump.index("剩余风险可接受性结论") < full_dump.index("加密传输与源端脱敏实质切断了")
    # In Chapter 6: Conclusive 4-tier decision table + rationale BEFORE Chapter 6 prose
    assert full_dump.index("裁断决定类别|核定结果") < full_dump.index("在办结法定事先授权并维持单路径暂停熔断机制")
    assert LIA["revision"] >= 14 and TIA["revision"] >= 18


def test_edpb_official_en_has_multirow_legal_basis_quantitative_scores_and_three_below_table_notes() -> None:
    data = {
        "controller": {
            "name_and_address": "Qihang Smart Mobility Ltd., Tunis, Tunisia",
            "units": "Autonomous Driving R&D & Group Privacy Office",
            "contact_or_rep": "EU Representative Office, Paris, France",
            "dpo_info": "Data Protection Officer, dpo@example.com",
        },
        "processors": [{"name": "Qihang Europe R&D SAS (France)", "obligations": "Cloud validation under SCCs and RBAC."}],
        "processing_name": "Autonomous Driving Edge-Masked Scene Validation",
        "data_items": [
            {
                "item": "Edge-Blurred Exterior Video Snippet",
                "explanation": "Faces and licence plates irreversibly blurred on vehicle edge.",
                "is_special": False,
                "need_justification": "Required for corner-case obstacle perception verification.",
                "recipient": "Paris Perception Engineering Team",
                "recipient_justification": "Restricted Tier-2 perception engineers.",
                "retention": "90 days",
                "retention_justification": "Minimum validation cycle.",
                "quality_metric": "SHA-256 frame integrity & blur verification",
                "quality_justification": "Automated pre-upload blur audit.",
            }
        ],
        "special_category_determination": "On-vehicle edge AI blurs faces and plates in real time without extracting biometric templates for unique identification; Article 9(1) GDPR is not triggered.",
        "legal_basis_rows": [
            {
                "purpose_group": "Safety driver trial operations and telemetry logging",
                "basis_choice": "contract",
                "justification": "Necessary for performance of the safety-driver operational contract under Art. 6(1)(b) GDPR.",
            },
            {
                "purpose_group": "Optional cabin feedback recording",
                "basis_choice": "consent",
                "justification": "Subject to explicit granular opt-in consent under Art. 6(1)(a) GDPR.",
            },
            {
                "purpose_group": "Incidental exterior road-user transient edge blurring",
                "basis_choice": "legitimate_interest",
                "justification": "Necessary for road-safety validation under Art. 6(1)(f) GDPR with immediate edge anonymisation.",
            },
        ],
        "lia_balancing_note": "(1) Purpose: Road safety validation is lawful and concrete; (2) Necessity: Real-time edge blurring avoids raw PII upload; (3) Balancing: Zero raw facial/plate retention protects passers-by.",
        "risk_rows": [
            {
                "risk": "Interception of cross-border perception packets",
                "init_likelihood": "Medium (L3)",
                "init_severity": "High (S5)",
                "modulating_factors": "Cross-Mediterranean public carrier transit.",
                "init_level": "High (15/25)",
                "inherent_acceptability": "Unacceptable without cryptographic transport controls.",
                "measures": "Mutual-TLS 1.3 tunnel and HSM key isolation.",
                "measure_effectiveness": "Eliminates cleartext exposure and reduces interception likelihood under Eliminate > Reduce > Transfer > Accept.",
                "res_likelihood": "Low (L1)",
                "res_severity": "Medium (S3)",
                "res_level": "Low (3/25)",
                "residual_acceptability": "Acceptable residual risk.",
            }
        ],
        "residual_risk_conclusion": "All inherent risks are reduced to 3/25 (Low) under Eliminate > Reduce > Transfer > Accept; Article 36 GDPR prior consultation is not required.",
        "decision": {
            "outcome": "CONDITIONALLY_APPROVED",
            "condition_1": "Obtain prior INPDP transfer authorisation and execute SCCs prior to launch.",
            "condition_2": "Enforce edge blurring and single-path circuit-breaker suspension on any anomaly.",
            "justification": "Conditional approval granted with acceptable 3/25 residual risk and strict circuit-breaker isolation.",
        },
    }
    docx_bytes = render_edpb_2026_docx(data, "2026-11-17")
    with zipfile.ZipFile(io.BytesIO(docx_bytes)) as zf:
        root = ET.fromstring(zf.read("word/document.xml"))
    body = root.find(W + "body")
    full_text = "".join(root.itertext())
    assert _CJK_RE.search(full_text) is None
    assert "Special-Category Data Determination Note:" in full_text
    assert "Legitimate Interest Balancing Test Summary:" in full_text
    assert "Residual Risk Conclusion:" in full_text
    assert "High (15/25)" in full_text and "Low (3/25)" in full_text
    assert "single-path circuit-breaker" in full_text

    # Verify Table 13 actually has 1 header + 3 dynamic legal-basis rows = 4 rows
    tables = body.findall(W + "tbl")
    assert len(tables[13].findall(W + "tr")) == 4
    # Verify each inserted note sits immediately after its target table in <w:body>
    children = list(body)
    assert "Special-Category Data Determination Note:" in "".join(children[children.index(tables[6]) + 1].itertext())
    assert "Legitimate Interest Balancing Test Summary:" in "".join(children[children.index(tables[13]) + 1].itertext())
    assert "Residual Risk Conclusion:" in "".join(children[children.index(tables[29]) + 1].itertext())
