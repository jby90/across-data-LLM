from __future__ import annotations

import json
from types import SimpleNamespace

from app.analysis import doc_sections, document_narrative, risk_items


def test_risk_adequacy_notes_are_mechanism_differentiated_not_rubber_stamped() -> None:
    rows = [
        {
            "id": "r1",
            "risk": "强逆光下车端可能漏检个别行人面部",
            "init_likelihood": "中",
            "init_severity": "高",
            "likelihood": "低",
            "severity": "低",
            "measures": "在本地安全站增设离线高精度二次脱敏质检闸机，疑似漏检帧直接整段丢弃。",
        },
        {
            "id": "r2",
            "risk": "未获批文前研发脚本误触发自动云同步",
            "init_likelihood": "中",
            "init_severity": "高",
            "likelihood": "低",
            "severity": "中",
            "measures": "在出境防火墙实施法务审批数字签名门禁，未录入有效授权批文号前物理阻断对外连接。",
        },
        {
            "id": "r3",
            "risk": "激光雷达点云可能反映敏感区域地形",
            "init_likelihood": "中",
            "init_severity": "中",
            "likelihood": "低",
            "severity": "低",
            "measures": "车载采集端内置敏感区域500米电子围栏自动停录，并剥离绝对经纬度坐标。",
        },
    ]
    blks = risk_items.blocks(json.dumps(rows, ensure_ascii=False), "")
    closure_tbl = next(b for b in blks if b.get("type") == "table" and "措施适当性与有效性论证" in (b.get("head") or []))
    notes = [r[2] for r in closure_tbl["rows"]]
    assert len(set(notes)) == 3
    assert all(not n.startswith("依托既有控制（") for n in notes)
    assert document_narrative.audit_document_prose_quality(blks, "dpia") == []


def test_normalize_statute_mentions_wraps_book_marks_and_uses_idiomatic_anaphora() -> None:
    offered = [
        "个人数据保护基本法（2004年第63号）",
        "实施第09-08号法律的第2-09-165号法令",
        "个人数据保护法（第09-08号法律）",
    ]
    raw = (
        "不准确数据的更正删除依个人数据保护基本法第55条办理，"
        "通信以2004年第63号法第47条书面同意为基础，"
        "并依个人数据保护基本法（2004年第63号）第17条禁止捆绑。"
    )
    norm = document_narrative._normalize_statute_mentions(raw, offered)
    assert "依《个人数据保护基本法（2004年第63号）》第55条" in norm
    assert "以该法第47条" in norm
    assert "依该法第17条" in norm
    assert "《《" not in norm

    # Intervening second statute resets anaphora cleanly without ambiguity
    mixed = (
        "依据个人数据保护法第2条确定适用范围，"
        "对照第2-09-165号法令第27条核对申请要素，"
        "再依个人数据保护法第28条申请传输授权并按个人数据保护法第25条约束处理者。"
    )
    norm_mixed = document_narrative._normalize_statute_mentions(mixed, offered)
    assert "《个人数据保护法（第09-08号法律）》第2条" in norm_mixed
    assert "《实施第09-08号法律的第2-09-165号法令》第27条" in norm_mixed
    assert "《个人数据保护法（第09-08号法律）》第28条" in norm_mixed
    assert "按该法第25条" in norm_mixed


def test_narrow_research_clause_filtered_for_commercial_automotive_but_general_rule_kept() -> None:
    cits = [
        {
            "clause_id": "tn49",
            "document_title": "个人数据保护基本法（2004年第63号）",
            "clause_number": "第66条",
            "quote_zh": "为科学研究目的处理的个人数据不得包含可揭示数据主体身份的内容。",
            "dimension_code": "D2",
            "side": "source",
        },
        {
            "clause_id": "lb87",
            "document_title": "第81号电子交易与个人数据法",
            "clause_number": "第87条",
            "quote_zh": "个人数据应诚信收集，并为合法、具体和明确的目的收集；不得为不相符目的处理，除非涉及为统计、历史或科学研究目的处理数据。",
            "dimension_code": "D2",
            "side": "source",
        },
        {
            "clause_id": "tn11",
            "document_title": "个人数据保护基本法（2004年第63号）",
            "clause_number": "第11条",
            "quote_zh": "个人数据必须公平处理，且在收集目的所必需的限度内处理。",
            "dimension_code": "D2",
            "side": "source",
        },
    ]
    offered = document_narrative._offered(
        cits,
        dimensions=["D2"],
        side="source",
        context_blob="L4级自动驾驶公共道路测试与多传感器感知算法迭代验证",
    )
    clauses = [o["clause"] for o in offered]
    assert "第66条" not in clauses
    assert "第87条" in clauses
    assert "第11条" in clauses


async def test_negated_camera_collection_never_triggers_optical_blur_claim(monkeypatch) -> None:
    async def _fake_prof(_s, _cc, _lbl):
        return ("《个人数据保护法（第09-08号法律）》", "摩洛哥CNDP")

    monkeypatch.setattr(doc_sections, "_source_jurisdiction_profile", _fake_prof)
    task = SimpleNamespace(country_code="EG", scenario_type="connected_vehicle_cloud", facts_json={})
    values = {
        "source_country": "摩洛哥",
        "recipient_country": "埃及",
        "exterior_video_and_gnss": (
            "车载终端不采集车内音视频与车外道路人脸车牌影像；"
            "日常连续行驶轨迹不在跨境同步范围内，仅在热失控告警瞬间附带经本地网关模糊处理的区域级位置信息。"
        ),
    }
    blks = await doc_sections.dpia_special_data_note_blocks(None, task, values)
    txt = blks[0]["text"]
    assert "不采集车内外音视频" in txt
    assert "已完成不可逆人脸车牌遮蔽" not in txt


def test_editorial_quality_gate_catches_mechanical_defects() -> None:
    bad_blocks = [
        {"type": "heading", "text": "0. 概览"},
        {"type": "table", "head": ["项", "值"], "rows": [["范围", "严格限定于【测试算法验证。】所需最小边界"]]},
        {"type": "paragraph", "text": "依个人数据保护基本法第6条承担责任，采集点云、航迹、图像片段、网格编码。[1]"},
        {"type": "heading", "text": "1. 描述"},
        {"type": "paragraph", "text": "采集点云、航迹、图像片段、网格编码，符合科学研究场景下不得揭示身份的要求。[2]"},
        {"type": "heading", "text": "2. 合法性"},
        {"type": "paragraph", "text": "采集点云、航迹、图像片段、网格编码，依《个人数据保护基本法》第11条、《个人数据保护基本法》第17条、《个人数据保护基本法》第47条办理。[3]"},
    ]
    issues = document_narrative.audit_document_prose_quality(bad_blocks, "dpia", "自动驾驶道路测试")
    joined = "\n".join(issues)
    assert "方括号吞入完整句号" in joined
    assert "缺失中文书名号" in joined
    assert "科学研究" in joined
    assert "单段内机械重复" in joined
    assert "跨章报菜名复读" in joined
