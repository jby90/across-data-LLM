from __future__ import annotations

import io
import zipfile
from xml.etree import ElementTree as ET

from app.analysis import document_templates as tpl
from app.analysis import risk_items
from app.analysis.doc_projects import RISK_FACTS_SIG_KEY, _build_risk_sync, _risk_facts_signature
from app.analysis.document_narrative import polish_legal_prose
from app.analysis.official_docx import CHECKED
from app.analysis.official_eu_edpb_dpia import (
    _CJK_RE,
    _sanitise_edpb_tree,
    render_edpb_2026_docx,
)
from app.knowledge.whitepaper import merge_law_origins_into_refs


def test_polish_legal_prose_eliminates_engineering_jargon() -> None:
    raw = (
        "经车端边缘脱敏裁剪、字段裁剪与500米围栏裁剪，日常坐标完成地理围栏泛化与精度降维；"
        "通过单向哈希消息认证码假名化、互联网协议安全隧道及穿透约束构建风险缓释体系，"
        "缓释闭环后结合风险机理执行180天到期不可逆硬擦除与覆写擦除。"
    )
    cleaned = polish_legal_prose(raw)
    for banned in (
        "裁剪",
        "泛化",
        "降维",
        "缓释",
        "硬擦除",
        "覆写擦除",
        "穿透约束",
        "风险机理",
        "单向哈希消息认证码假名化",
        "互联网协议安全隧道",
    ):
        assert banned not in cleaned, f"banned jargon remained: {banned} in {cleaned}"
    assert "180" in cleaned
    assert "500" in cleaned


def test_document_templates_merges_foreign_law_origin_once_and_drops_law_summary() -> None:
    definition = {
        "title": "数据跨境传输影响评估（TIA）",
        "facts": [
            {"key": "security_tech", "label": "技术防护", "question": "采取了哪些技术措施？"},
        ],
        "sections": [
            {
                "heading": "一、技术保护说明",
                "blocks": [
                    {"type": "facts_table", "keys": ["security_tech"]},
                    {"type": "citations", "dimensions": ["D3"], "limit": 4},
                ],
            }
        ],
    }
    citations = [
        {
            "clause_id": "c1",
            "document_id": "d1",
            "document_title": "新加坡个人数据保护法",
            "original_title": "Personal Data Protection Act 2012",
            "clause_number": "第26条",
            "quote_zh": "组织不得将个人数据转移至新加坡境外，除非符合法定保护要求。",
            "quote_original": "An organisation must not transfer any personal data...",
            "dimension_code": "D3",
        },
        {
            "clause_id": "c2",
            "document_id": "d1",
            "document_title": "新加坡个人数据保护法",
            "original_title": "Personal Data Protection Act 2012",
            "clause_number": "第24条",
            "quote_zh": "组织必须作出合理安全安排以保护其持有的个人数据。",
            "quote_original": "An organisation must protect personal data...",
            "dimension_code": "D3",
        },
    ]
    res = tpl.render(
        definition,
        {"security_tech": "采集端执行精度裁剪，销毁端配置到期不可逆硬擦除。"},
        citations,
    )
    headings = [b.get("text") for b in res.blocks if b.get("type") == "heading"]
    assert "引用法规一览" not in headings
    tables = [b for b in res.blocks if b.get("type") == "table"]
    assert len(tables) == 2
    # Facts table normalized jargon automatically
    assert "裁剪" not in tables[0]["rows"][0][1]
    assert "硬擦除" not in tables[0]["rows"][0][1]
    # Citations table annotated foreign law original title ONLY on first occurrence
    cite_rows = tables[1]["rows"]
    assert cite_rows[0][0] == "《新加坡个人数据保护法》（Personal Data Protection Act 2012）"
    assert cite_rows[1][0] == "《新加坡个人数据保护法》"


def test_whitepaper_merges_law_origins_into_refs_and_removes_law_summary_table() -> None:
    blocks = [
        {"type": "title", "text": "新加坡汽车数据合规简报"},
        {"type": "heading", "text": "参考条文"},
        {
            "type": "table",
            "head": ["编号", "依据", "条款", "条文内容"],
            "rows": [
                ["[1]", "《新加坡个人数据保护法》", "第24条", "安全保护义务。"],
                ["[2]", "《新加坡个人数据保护法》", "第26条", "跨境传输限制。"],
            ],
        },
        {"type": "heading", "text": "引用法规一览"},
        {
            "type": "table",
            "head": ["中文名称", "原名"],
            "rows": [["《新加坡个人数据保护法》", "Personal Data Protection Act 2012"]],
        },
    ]
    merged = merge_law_origins_into_refs(blocks)
    headings = [b.get("text") for b in merged if b.get("type") == "heading"]
    assert "引用法规一览" not in headings
    ref_table = next(b for b in merged if b.get("type") == "table")
    assert ref_table["rows"][0][1] == "《新加坡个人数据保护法》（Personal Data Protection Act 2012）"
    assert ref_table["rows"][1][1] == "《新加坡个人数据保护法》"


def test_render_edpb_2026_docx_zero_cjk_and_valid_xml() -> None:
    sample = {
        "controller": {
            "name_and_address": "启衡智行汽车科技（上海）有限公司, Casablanca, Morocco",
            "units": "Connected Vehicle R&D Division and Group Privacy Office",
            "contact_or_rep": "Casablanca Operations Office, Morocco",
            "dpo_info": "Data Protection Officer, dpo@example.com",
        },
        "processors": [
            {
                "name": "Qihang Smart Mobility Singapore Pte. Ltd. (Singapore)",
                "obligations": "Remote diagnostics hosting under EU SCC (2021/914) and strict RBAC.",
            }
        ],
        "processing_name": "Connected Vehicle Remote Diagnostics and Safety Telemetry",
        "version_history": "Version 1.0 - Initial Pre-Launch DPIA",
        "planning": {"launch_date": "2026-12-01", "expiration": "180-day rolling retention"},
        "tech_sheet": {
            "dpia_team": "DPO, Vehicle Cybersecurity Lead, Cloud Architect",
            "reference_materials": "EDPB DPIA Template 2026 v1.0; Law 09-08; PDPA 2012",
            "scope": "T-Box diagnostic snapshots, encrypted tunnel, Singapore private cloud",
        },
        "context": {
            "overview": "Cross-border transmission of pseudonymised battery and ECU fault telemetry.",
            "purposes": "Vehicle warranty diagnostics and thermal runaway safety alerts.",
            "data_subjects": "Vehicle owners and authorised drivers (adult consumers).",
            "scale_frequency": "Event-driven transmission upon diagnostic fault code trigger.",
            "retention_deletion": "180 days maximum in Singapore cloud followed by secure deletion.",
            "functional_desc": "In-vehicle T-Box filters fields and pseudonymises VIN prior to mTLS transmission.",
            "assets": "In-vehicle T-Box, Casablanca edge gateway, mTLS 1.3 tunnel, Singapore cloud.",
        },
        "data_items": [
            {
                "item": "Pseudonymised VIN Token",
                "category": "pseudonymised",
                "special_type": "none",
                "source": "Vehicle ECU",
                "need_justification": "Required to correlate fault codes with vehicle configuration.",
                "recipient": "Singapore Diagnostics Team",
                "recipient_justification": "12 whitelisted Tier-2 engineers only.",
                "retention": "180 days",
                "retention_justification": "Minimum period for warranty root-cause verification.",
                "quality_metric": " Cryptographic HMAC integrity check",
                "quality_justification": "Prevents diagnostic packet spoofing.",
            }
        ],
        "legal_basis": {
            "selected": ["contract", "legitimate_interests"],
            "justification": "Necessary for performance of vehicle warranty and safety maintenance contract.",
            "special_justification": "No special-category biometric or health data is collected.",
        },
        "principles": {
            k: {"measures": "Implemented technical and organisational controls.", "discussion": "Verified proportionate."}
            for k in (
                "fairness", "transparency", "purpose_limitation", "data_minimisation",
                "accuracy", "storage_limitation", "integrity_confidentiality", "accountability",
            )
        },
        "rights": {
            k: {"measures": "In-car privacy centre and dedicated DPO email channel.", "discussion": "SLA within statutory period."}
            for k in ("information", "access_portability", "rectification_erasure", "objection_restriction", "automated_decisions")
        },
        "other_gdpr": {
            k: {"measures": "Executed EU SCC 2021/914 and processor audit clauses.", "discussion": "Enforceable commitments."}
            for k in ("consent", "processors", "transfers")
        },
        "by_design_default": [
            {"aspect": "In-vehicle edge filtering", "measures": "Cabin audio/video disabled by default.", "discussion": "Minimises collection."}
        ],
        "security_measures": [
            {"domain": "Encryption & Key Sovereignty", "measures": "mTLS 1.3 in transit, AES-256 at rest, HSM key isolation.", "discussion": "Mitigates interception."}
        ],
        "abnormal_threats": [
            {
                "threat": "Unauthorised access to diagnostic cloud repository",
                "materialisation": "Compromised engineer credentials",
                "sources": "External attacker or insider misuse",
                "impact": "Potential linkage of vehicle diagnostic status",
            }
        ],
        "necessity_assessment": "Purely local processing cannot provide 24/7 regional Tier-2 battery safety engineering analysis.",
        "proportionality_assessment": "Strict field filtering, pseudonymisation and 180-day retention ensure proportionality.",
        "risk_method": "3x3 Likelihood x Severity matrix aligned with EDPB guidelines.",
        "risk_rows": [
            {
                "risk": "Potential re-identification of vehicle movement patterns",
                "init_likelihood": "Medium",
                "init_severity": "High",
                "modulating_factors": "Continuous GNSS disabled; single-frame coarse location only on safety alert.",
                "init_level": "High",
                "inherent_acceptability": "Requires technical safeguards prior to launch.",
                "measures": "Edge coarse location filtering and domestic HSM pseudonymisation key custody.",
                "res_likelihood": "Low",
                "res_severity": "Medium",
                "res_level": "Low",
                "residual_acceptability": "Acceptable residual risk.",
            }
        ],
        "action_plan": "Complete supervisory filing prior to enabling production cross-border sync.",
        "dpo_advice": "DPO concurs that residual risks are low subject to supervisory authorisation.",
        "data_subject_views": "Clear layered notice provided via in-vehicle screen and owner manual.",
        "decision": {
            "outcome": "CONDITIONALLY_APPROVED",
            "condition_1": "Obtain prior transfer authorisation from competent authority",
            "condition_2": "Verify 180-day automated deletion job in pre-production",
            "justification": "Safeguards effectively reduce all inherent high risks to acceptable low levels.",
        },
    }
    sanitised = _sanitise_edpb_tree(sample, ["启衡智行汽车科技（上海）有限公司"])
    docx_bytes = render_edpb_2026_docx(sanitised, "2026-11-14")
    assert len(docx_bytes) > 40_000
    with zipfile.ZipFile(io.BytesIO(docx_bytes)) as zf:
        assert "word/footnotes.xml" in zf.namelist()
        ET.fromstring(zf.read("word/footnotes.xml"))
        doc_root = ET.fromstring(zf.read("word/document.xml"))
        full_text = "".join(doc_root.itertext())
        assert _CJK_RE.search(full_text) is None
        assert "Connected Vehicle Remote Diagnostics and Safety Telemetry" in full_text
        assert "[Official English Legal Name of Data Controller]" in full_text
        # Checked Wingdings boxes exist
        raw_xml = zf.read("word/document.xml").decode("utf-8")
        assert CHECKED in raw_xml


def test_risk_sync_detects_upstream_fact_changes() -> None:
    risk_json = risk_items.dump(
        [
            {
                "id": "r1",
                "risk": "跨境传输链路被截获风险",
                "init_likelihood": "中",
                "init_severity": "高",
                "likelihood": "低",
                "severity": "中",
                "measures": "启用双向mTLS 1.3加密专用通道与HSM密钥隔离。",
                "owner": "车云安全部",
                "due": "2026-11-20",
            }
        ]
    )
    facts = {
        "doc_data_types": "车辆故障码与电池包温度告警帧",
        "doc_security_tech": "双向mTLS 1.3加密传输与AES-256静态加密",
        "doc_risk_rating": risk_json,
    }
    facts[RISK_FACTS_SIG_KEY] = _risk_facts_signature(facts, "QH-S9")
    sync0 = _build_risk_sync(facts, "QH-S9", risk_json)
    assert sync0 == {"stale": False, "can_diagnose": True}

    # Changing downstream remediation field does NOT mark risk grading stale
    facts["doc_issues"] = "待提交备案"
    assert _build_risk_sync(facts, "QH-S9", risk_json)["stale"] is False

    # Changing upstream business/data/security fact DOES mark risk grading stale
    facts["doc_data_types"] = "车辆故障码、电池包温度告警帧及紧急碰撞单帧位置坐标"
    sync1 = _build_risk_sync(facts, "QH-S9", risk_json)
    assert sync1 == {"stale": True, "can_diagnose": True}
