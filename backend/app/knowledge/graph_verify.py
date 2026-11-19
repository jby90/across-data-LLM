# ruff: noqa: E501
"""Automatic verification of knowledge graph nodes and edges.

Machine-extracted nodes and edges keep ``review_status == 'pending_review'`` so that
``review_status == 'approved'`` remains reserved exclusively for human approval (which protects
human-reviewed items from being overwritten on re-extraction).

Separately from ``review_status``, every active node and edge is evaluated against the platform's
automatic verification gates:
- verbatim evidence quote present in the current parsed clause text;
- relation endpoint type constraints (``ALLOWED_RELATION_ENDPOINT_TYPES``);
- independent semantic verification verdict (``judge_verdict == 'yes'`` and ``confidence >= 0.75``
  for edges; ``party_checked``, ``node_class != 'generic'``, and dual-pass ``node_check`` agreement
  for obligation/penalty nodes).

Items that pass all automatic gates receive ``verification_status = 'auto_verified'`` (已核验);
items with split verdicts, title-inferred amendments, generic principle statements, or low-confidence
interpretations receive ``verification_status = 'needs_confirmation'`` (待确认) with a specific
human-readable ``verification_reason`` so reviewers only need to inspect the small set of ambiguous
items.
"""

from __future__ import annotations

import re

from app.knowledge.extractor import ALLOWED_RELATION_ENDPOINT_TYPES
from app.knowledge.models import KgEdge, KgNode, NodeType

_DEBUG_TOKEN_RE = re.compile(
    r"[a-z_]+\s*(?:为|=|==|:|：)\s*(?:null|none|true|false|\"\"|'')"
    r"|条文另有未写入的内容\s*[：:]\s*无[。；，,\s]*"
    r"|（[^）]*(?:null|obligated_party|applicable_condition|penalty_ceiling|retention_limit|omissions)[^）]*）"
    r"|\([^)]*(?:null|obligated_party|applicable_condition|penalty_ceiling|retention_limit|omissions)[^)]*\)",
    re.I,
)


def _enum_pat(token: str) -> re.Pattern:
    return re.compile(rf"(?<![A-Za-z_]){re.escape(token)}(?![A-Za-z_])", re.I)


_RELATION_ZH_MAP: tuple[tuple[re.Pattern, str], ...] = (
    (_enum_pat("RESTRICTS_CROSS_BORDER"), "「限制跨境传输」"),
    (_enum_pat("RESTRICTS_TRANSFER"), "「限制出境传输」"),
    (_enum_pat("REQUIRES_LOCALIZATION"), "「要求本地存储」"),
    (_enum_pat("REQUIRES_ASSESSMENT"), "「要求开展评估」"),
    (_enum_pat("REQUIRES_ADMISSION"), "「要求属地准入」"),
    (_enum_pat("REQUIRES_CONSENT"), "「要求取得同意」"),
    (_enum_pat("APPLIES_TO_SCENARIO"), "「适用业务场景」"),
    (_enum_pat("IMPOSES_PENALTY"), "「规定违规罚则」"),
    (_enum_pat("INVOLVES_DATA"), "「涉及数据类型」"),
    (_enum_pat("TRANSFERS_TO"), "「跨境传输至」"),
    (_enum_pat("DATA_CATEGORY"), "「数据类型」"),
    (_enum_pat("APPLIES_TO"), "「适用于」"),
    (_enum_pat("INSTRUMENT"), "「法规文件」"),
    (_enum_pat("OBLIGATION"), "「合规义务」"),
    (_enum_pat("REGULATOR"), "「监管机构」"),
    (_enum_pat("SUPERVISES"), "「监管执法」"),
    (_enum_pat("PUBLISHES"), "「发布法规」"),
    (_enum_pat("PENALIZES"), "「设定罚则」"),
    (_enum_pat("PROHIBITS"), "「禁止行为」"),
    (_enum_pat("REGULATES"), "「规范管理」"),
    (_enum_pat("REQUIRES"), "「规定合规义务」"),
    (_enum_pat("SCENARIO"), "「业务场景」"),
    (_enum_pat("GOVERNS"), "「管辖与适用」"),
    (_enum_pat("EXEMPTS"), "「规定豁免」"),
    (_enum_pat("PENALTY"), "「违规罚则」"),
    (_enum_pat("COUNTRY"), "「国家/地区」"),
    (_enum_pat("AMENDS"), "「修订」"),
    (_enum_pat("REPEALS"), "「废止」"),
)


def clean_verify_note(note: str | None) -> str:
    """Strip internal model debug parentheticals/field names and translate relation/node enums before exposing to UI."""
    if not note:
        return ""
    cleaned = _DEBUG_TOKEN_RE.sub("", str(note))
    for pat, zh_label in _RELATION_ZH_MAP:
        cleaned = pat.sub(zh_label, cleaned)
    cleaned = re.sub(r"「「([^」]+)」」", r"「\1」", cleaned)
    cleaned = re.sub(r"「([^」]+)」\s*关系", r"「\1」关系", cleaned)
    cleaned = re.sub(r"[；;，,\s]{2,}", "；", cleaned).strip(" ；;，,。")
    if not cleaned:
        return ""
    return cleaned

STRUCTURAL_NODE_TYPES = {
    NodeType.COUNTRY.value,
    NodeType.INSTRUMENT.value,
    NodeType.REGULATOR.value,
    NodeType.DATA_CATEGORY.value,
}


def verify_node(
    node: KgNode, *, has_current_clause: bool = True, has_verbatim_edge: bool = True
) -> tuple[str, str]:
    """Return ``(verification_status, verification_reason)`` for a knowledge graph node."""
    if node.review_status == "approved":
        return "approved", "已由法务人员人工确认"
    if node.review_status in ("stale", "rejected", "superseded"):
        return "retired", "已停用"

    # 第二层：模型对照条款原文的逐项核验（graph_auto_verify）判为“需人工确认”的，不因规则闸门全过而放行
    if getattr(node, "verify_state", None) == "human_needed":
        return (
            "needs_confirmation",
            clean_verify_note(getattr(node, "verify_note", None)) or "对照条文核验未通过，需人工确认",
        )

    attrs = node.attributes or {}
    if attrs.get("old_version_evidence"):
        return "needs_confirmation", "所引条款版本已更新，需对照现行条文确认"

    if node.node_type in STRUCTURAL_NODE_TYPES:
        return "auto_verified", "已对照法规登记信息与条款原文自动核验"

    if node.evidence_clause_id and not has_current_clause:
        return "needs_confirmation", "所引条款版本已更新，需对照现行条文确认"

    if node.node_type == NodeType.OBLIGATION.value and attrs.get("node_class") == "generic":
        return "needs_confirmation", "条文表述为原则性守法要求，需确认是否列为具体合规义务"

    node_check = attrs.get("node_check")
    if isinstance(node_check, dict):
        v1 = (node_check.get("first") or {}).get("verdict")
        v2 = (node_check.get("second") or {}).get("verdict")
        if v1 == "unsupported" and v2 == "unsupported":
            return "needs_confirmation", "独立核对认为条文未直接规定该项，需人工确认"
        if v1 == "unsupported" or v2 == "unsupported":
            return "needs_confirmation", "两次独立核对结论不一致，需人工确认"

    if attrs.get("party_checked") is False:
        return "needs_confirmation", "适用主体范围需对照条文人工确认"

    if not has_verbatim_edge and not has_current_clause:
        return "needs_confirmation", "缺少现行条款原文引证，需人工确认"

    return "auto_verified", "已通过原文逐字比对、适用主体核查与独立语义核验"


def verify_edge(
    edge: KgEdge,
    *,
    src_node: KgNode,
    dst_node: KgNode,
    clause_text: str,
    src_status: str = "auto_verified",
    dst_status: str = "auto_verified",
) -> tuple[str, str]:
    """Return ``(verification_status, verification_reason)`` for a knowledge graph edge."""
    if edge.review_status == "approved":
        return "approved", "已由法务人员人工确认"
    if edge.review_status in ("stale", "rejected", "superseded"):
        return "retired", "已停用"

    if getattr(edge, "verify_state", None) == "human_needed":
        return (
            "needs_confirmation",
            clean_verify_note(getattr(edge, "verify_note", None)) or "对照条文核验未通过，需人工确认",
        )

    verdict = str(getattr(edge, "judge_verdict", "yes"))
    if verdict == "title_inferred":
        return "needs_confirmation", "由修正案标题与年份关系推断，需对照条文确认"

    if bool(getattr(edge, "is_structural", False)):
        return "auto_verified", "已对照法规官方发布信息自动核验"

    quote = (edge.evidence_quote or "").strip()
    if not quote or (clause_text and quote not in clause_text):
        return "needs_confirmation", "摘录原文与现行条款文字不完全一致，需人工确认"

    allowed = ALLOWED_RELATION_ENDPOINT_TYPES.get(edge.relation_type, set())
    if allowed and (src_node.node_type, dst_node.node_type) not in allowed:
        return "needs_confirmation", "要素类型搭配需人工确认"

    if verdict != "yes":
        return "needs_confirmation", "独立语义核对未完全通过，需人工确认"

    conf = float(getattr(edge, "confidence", 0.85))
    if conf < 0.75:
        return "needs_confirmation", "条款语义存在多种理解可能，需人工确认"

    if src_status == "needs_confirmation" or dst_status == "needs_confirmation":
        return "needs_confirmation", "关联的义务或主体节点待人工确认"

    return "auto_verified", "已通过条款原文逐字比对、关系约束校验与独立语义核验"
