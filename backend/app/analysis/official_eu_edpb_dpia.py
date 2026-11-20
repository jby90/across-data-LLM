# ruff: noqa: E501
"""Fill the official European Data Protection Board (EDPB) 2026 DPIA Template (v1.0, 10 March 2026) in English.

For outbound routes where the source jurisdiction has no dedicated domestic DPIA Word form (or follows
EU-aligned data protection regimes such as Morocco Law 09-08, Tunisia Organic Law 2004-63, Egypt Law 151/2020,
or EU GDPR), the platform provides dual-track output for ``dpia``:
1. Chinese Review Draft (``中文核对底稿``): structured 7-part Chinese legal argument for internal OEM management,
   R&D, security, and legal sign-off;
2. Official English EDPB 2026 Form (``英文官方原表``): ``EU_EDPB_DPIA_template_2026_v1_en.docx`` populated in
   authentic European data protection legal English with zero CJK residue, preserving all official headings,
   tables, footnotes, and Wingdings checkboxes, ready for overseas supervisory authorities or EU partners.
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import io
import json
import re
import uuid
import zipfile
from datetime import date
from typing import Any
from xml.etree import ElementTree as ET

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.analysis import clause_gate, risk_items
from app.analysis.document_narrative import DocumentNarrative
from app.analysis.llm import LlmUnavailableError, call_llm_json
from app.analysis.official_docx import (
    CHECKED,
    OFFICIAL_DIR,
    W,
    _register_namespaces,
    _serialise,
    _text,
)

EDPB_TEMPLATE_PATH = OFFICIAL_DIR / "EU_EDPB_DPIA_template_2026_v1_en.docx"
EDPB_NARRATIVE_CODE = "dpia_edpb_en"
_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\u3000-\u303f\uff01-\uff60]")
_XML_BAD_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\ufffe\uffff]")

_LEVEL_EN = {"低": "Low", "中": "Medium", "高": "High", "低风险": "Low", "中风险": "Medium", "高风险": "High"}

SYSTEM_EDPB_EN = (
    "You are Senior European Data Protection Counsel drafting an official Data Protection Impact Assessment (DPIA) "
    "in the European Data Protection Board (EDPB) 2026 v1.0 Template for an intelligent connected vehicle (ICV) or autonomous driving operator. "
    "Write strictly in crisp, varied, authoritative European regulatory English (GDPR / EDPB / CNIL / BfDI counsel style).\n"
    "Hard rules:\n"
    "1. NEVER output any Chinese characters or CJK punctuation anywhere in the JSON values. Translate all proper nouns, "
    "company names, authority names, vehicle models, and statutory titles into accurate, standard English.\n"
    "2. Rely strictly on the facts, risk items, and statutory citations provided in the user payload. Do NOT invent "
    "unfounded statistics, headcounts, dates, or certificate numbers. Commercial automotive R&D, autonomous perception validation, "
    "and warranty diagnostics are commercial engineering operations—NEVER misclassify them under GDPR Art. 89 / statutory academic 'scientific research' exemptions unless academic research is explicitly stated in the facts. "
    "Where draft=true and a specific company fact is genuinely absent, write '[To be supplemented by Controller]'. Where draft=false and facts are present, write complete, substantive prose without placeholders.\n"
    "3. Eliminate robotic repetition: NEVER copy-paste the full list of sensors/fields into every cell, NEVER repeat identical boilerplate sentences across the eight Art. 5 principles or Art. 12-22 rights rows—tailor each row specifically to the concrete technical/contractual control for that principle or right. Tailor legal_basis_rows strictly to the actual project archetype (e.g. autonomous road-test safety drivers + edge-masked public-road perception + authorised R&D transfer vs. consumer warranty diagnostics + user-consented telematics).\n"
    "4. Avoid obscure engineering slang; use standard legal-technical terminology (data minimisation, real-time edge anonymisation, "
    "pseudonymisation, mutual TLS encrypted tunnel, role-based access control, retention limitation, onward transfer prohibition).\n"
    "5. Preserve any redaction token matching '__CYSH_SECRET_...__' character-for-character wherever the Data Controller's "
    "entity name appears.\n"
    "6. Keep internal reasoning brief and output ONLY valid JSON matching the requested schema."
)


def _sanitise_edpb_tree(node: Any, cjk_aliases: list[str]) -> Any:
    ordered_aliases = sorted((a for a in cjk_aliases if a), key=len, reverse=True)
    if isinstance(node, str):
        out = node
        for alias in ordered_aliases:
            if alias and alias in out:
                out = out.replace(alias, "[Official English Legal Name of Data Controller]")
        return _strip_cjk(out)
    if isinstance(node, list):
        return [_sanitise_edpb_tree(item, ordered_aliases) for item in node]
    if isinstance(node, dict):
        return {k: _sanitise_edpb_tree(v, ordered_aliases) for k, v in node.items()}
    return node


def _strip_cjk(text: Any, fallback: str = "") -> str:
    if not text:
        return fallback
    cleaned = _XML_BAD_RE.sub("", str(text))
    replacements = (
        ("，", ", "),
        ("。", ". "),
        ("；", "; "),
        ("：", ": "),
        ("（", " ("),
        ("）", ") "),
        ("、", ", "),
        ("“", '"'),
        ("”", '"'),
        ("‘", "'"),
        ("’", "'"),
        ("《", ""),
        ("》", ""),
        ("【待补充】", "[To be supplemented by Controller]"),
    )
    for k, v in replacements:
        cleaned = cleaned.replace(k, v)
    cleaned = _CJK_RE.sub("", cleaned)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return cleaned or fallback


def _edpb_inputs_hash(values: dict[str, str], citations: list[dict], draft: bool) -> str:
    payload = {
        "v": 3,
        "draft": draft,
        "values": {k: str(values.get(k) or "").strip() for k in sorted(values)},
        "citations": sorted(f"{c.get('document_title', '')}:{c.get('clause_number', '')}" for c in citations[:20]),
    }
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def _schema_part1() -> dict[str, Any]:
    return {
        "controller": {
            "name_and_address": "Full English name and address/country of Data Controller",
            "units": "Internal departments responsible for automotive data governance, R&D and security",
            "contact_or_rep": "Main establishment, local representative or point of contact",
            "dpo_info": "Data Protection Officer / Chief Compliance Officer contact & role",
        },
        "processors": [
            {
                "name": "Recipient / Processor legal entity and jurisdiction",
                "obligations": "Specific processing scope, cloud hosting, contractual safeguards and audit obligations (2-3 sentences)",
            }
        ],
        "processing_name": "Formal English title of the connected vehicle processing activity",
        "version_history": "Version 1.0 - Initial DPIA prior to cross-border production/trial operation",
        "planning": {
            "launch_date": "Planned launch window or pre-authorisation milestone",
            "expiration": "Retention & operational review cycle / expiration conditions",
        },
        "tech_sheet": {
            "dpia_team": "Cross-functional DPIA team (Privacy Legal, Vehicle Cybersecurity, Cloud Ops, Business Owner)",
            "reference_materials": "EDPB DPIA Template 2026 v1.0, EDPB Recommendations 01/2020, applicable national data protection statutes, UN R155/R156",
            "scope": "In-scope vehicle sensors, ECUs, edge gateway, cross-border encrypted link and recipient cloud; out-of-scope items",
        },
        "data_items": [
            {
                "item": "Data item name (3 to 4 core items)",
                "explanation": "Source sensor/ECU, data subject category, format & edge masking (1-2 sentences)",
                "is_special": False,
                "need_justification": "Why strictly necessary for the automotive purpose (1-2 sentences)",
                "recipient": "Designated recipient entity & restricted engineering role",
                "recipient_justification": "Why recipient requires access (1 sentence)",
                "retention": "Exact retention period & storage location",
                "retention_justification": "Why this retention duration is the minimum necessary (1 sentence)",
                "quality_metric": "Integrity checksums, timestamp sync & edge de-identification verification",
                "quality_justification": "How data accuracy and de-identification quality are maintained (1 sentence)",
            }
        ],
        "purposes": [
            {
                "purpose": "Specific, explicit automotive purpose",
                "justification": "Personal data involved and why processing is necessary (2 sentences)",
            }
        ],
        "secondary_uses": "Statement that secondary or commercial profiling uses are strictly prohibited without separate lawful basis",
        "nature_scope_context": {
            "nature": "End-to-end operations from vehicle sensor capture and edge masking to encrypted transfer, cloud diagnostics and erasure",
            "scope": "Data subject categories, vehicle fleet/model scope, geographic origin and destination, transfer frequency",
            "context_cross_border": "Detailed cross-border transfer route, legal transfer mechanism and relationship with data subjects",
        },
        "phases": [
            {
                "phase": "Stage name (exactly 4 lifecycle stages: In-Vehicle Capture & Edge Masking; Encrypted Cross-Border Transfer; Cloud Storage & Restricted Analysis; Scheduled Erasure)",
                "ops": ["Collection", "Use", "Storage", "Sharing and Transfer", "Deletion and Destruction"],
                "explanation": "Operations performed at this stage (1-2 sentences)",
                "assets": "Supporting hardware/software assets (ECU/T-Box, TLS tunnel, KMS, cloud enclave)",
                "architecture_note": "Security architecture and isolation controls at this stage (1-2 sentences)",
            }
        ],
        "codes_of_conduct": {
            "code": "Applicable national data protection law, automotive data security rules & UN R155 cybersecurity standards",
            "explanation": "Mandatory statutory obligations and technical conformity benefits (2 sentences)",
        },
        "special_category_determination": "Formal 2-3 sentence legal determination note placed directly below Table 6 (1.1.a) explaining why the processed connected-vehicle data does or does not constitute GDPR Art. 9(1) / statutory special-category biometric or health data (e.g. real-time edge face/plate masking without extracting biometric templates for unique identification, or explicit statutory exception where triggered)",
        "legal_basis_rows": [
            {
                "purpose_group": "Distinct scenario-specific sub-processing activity tailored strictly to the actual project facts (3 rows; e.g. for autonomous road-testing: 1. Employed safety-driver duty & takeover logging, 2. Public-road incidental perception & real-time edge face/plate erasure, 3. Supervisory-authorised cross-border validation transfer; for connected passenger vehicles: 1. Core warranty & safety diagnostics, 2. User-consented connected features, 3. Cybersecurity & cross-batch defect analysis)",
                "basis_choice": "contract | consent | legal_obligation | legitimate_interest",
                "justification": "Substantive, non-repetitive legal analysis under GDPR Art. 6(1) and applicable sending-jurisdiction statute (2-3 crisp sentences)",
            }
        ],
        "lia_balancing_note": "Formal 3-step Legitimate Interest Balancing Test summary placed directly below Table 13 (2.1.a): (1) Legitimate Purpose Test, (2) Necessity Test, and (3) Balancing Test & on-vehicle edge safeguards confirming no override of data subjects' fundamental rights and freedoms",
        "legal_basis": {
            "purpose": "Primary cross-border automotive processing purpose",
            "basis_choice": "consent | contract | legal_obligation | legitimate_interest",
            "justification": "Substantive legal analysis under applicable data protection statute and GDPR Art. 6(1) benchmark (3-4 sentences)",
            "special_data_item": "Not applicable (or specific biometric/sensitive item if any)",
            "special_justification": "Explanation that raw facial/licence-plate imagery is excluded/masked at vehicle edge or covered by explicit consent",
        },
        "principles": {
            "fairness": {"measures": "1-2 concise sentences", "discussion": "1-2 concise sentences"},
            "transparency": {"measures": "1-2 concise sentences", "discussion": "1-2 concise sentences"},
            "purpose_limitation": {"measures": "1-2 concise sentences", "discussion": "1-2 concise sentences"},
            "data_minimisation": {"measures": "1-2 concise sentences", "discussion": "1-2 concise sentences"},
            "accuracy": {"measures": "1-2 concise sentences", "discussion": "1-2 concise sentences"},
            "storage_limitation": {"measures": "1-2 concise sentences", "discussion": "1-2 concise sentences"},
            "integrity_confidentiality": {"measures": "1-2 concise sentences", "discussion": "1-2 concise sentences"},
            "accountability": {"measures": "1-2 concise sentences", "discussion": "1-2 concise sentences"},
        },
    }


def _schema_part2() -> dict[str, Any]:
    return {
        "rights": {
            "information": {"measures": "1-2 concise sentences", "discussion": "1-2 concise sentences"},
            "access_portability": {"measures": "1-2 concise sentences", "discussion": "1-2 concise sentences"},
            "rectification_erasure": {"measures": "1-2 concise sentences", "discussion": "1-2 concise sentences"},
            "objection_restriction": {"measures": "1-2 concise sentences", "discussion": "1-2 concise sentences"},
            "automated_decisions": {"measures": "1-2 concise sentences", "discussion": "1-2 concise sentences"},
        },
        "other_gdpr": {
            "consent": {"measures": "1-2 concise sentences", "discussion": "1-2 concise sentences"},
            "processors": {"measures": "1-2 concise sentences", "discussion": "1-2 concise sentences"},
            "transfers": {"measures": "1-2 concise sentences", "discussion": "1-2 concise sentences"},
        },
        "by_design_default": [
            {"aspect": "Data protection by design (Vehicle Edge)", "measures": "1-2 sentences", "discussion": "1-2 sentences"},
            {"aspect": "Data protection by default (Cloud Access & Retention)", "measures": "1-2 sentences", "discussion": "1-2 sentences"},
        ],
        "security_measures": [
            {"domain": "Cryptographic Transport, Storage & Key Custody", "measures": "1-2 sentences", "discussion": "1-2 sentences"},
            {"domain": "Role-Based Access Control, Audit Logging & Incident Response", "measures": "1-2 sentences", "discussion": "1-2 sentences"},
        ],
        "necessity_assessment": "Structured 4-option counterfactual comparison (160-220 words): Option 1 Pure On-Vehicle Offline Processing (rejected: cannot achieve cross-fleet diagnostics/safety objectives); Option 2 Unfiltered Raw Full-Stream Cloud Upload (rejected: excessive privacy intrusion); Option 3 On-Vehicle Edge Masking + Event-Triggered Minimal Whitelist [Adopted: achieves target safety/service objectives with minimum intrusion]; Option 4 Full Localised Regional Infrastructure (long-term evolutionary reference)",
        "proportionality_assessment": "Two focused paragraphs (150-200 words) weighing tangible vehicle safety and user service benefits against residual rights impact, explicitly stating mandatory pre-conditions for proportionality: privacy-by-default off-switch for non-essential collection, continuous edge de-identification, layered notice with one-click withdrawal/objection, and automated expiry erasure including all backups",
        "abnormal_threats": [
            {
                "threat": "Specific threat scenario (3 to 4 scenarios: Unauthorised interception, Cloud privilege escalation, Disproportionate government access)",
                "materialisation": "How the threat could materialise in connected vehicle operations (1-2 sentences)",
                "sources": "Threat sources / potential vulnerabilities",
                "impact": "Concrete impact on data subjects' rights and freedoms (1-2 sentences)",
            }
        ],
        "risk_method": "Formal explanation (4-5 sentences) of the 5x5 quantitative risk matrix (Likelihood L1=1, L3=3, L5=5 x Severity S1=1, S3=3, S5=5 yielding 1-25 score: 1-4 Low, 5-12 Medium, 15-25 High), inherent-to-residual evaluation, and strict risk treatment priority rule: Eliminate > Reduce > Transfer > Accept",
        "risk_rows": [
            {
                "risk": "English risk description mapped 1:1 from each graded_risks item",
                "init_likelihood": "Qualitative + quantitative band from graded_risks (e.g. Medium (L3))",
                "init_severity": "Qualitative + quantitative band from graded_risks (e.g. High (S5))",
                "init_level": "Qualitative + quantitative score from graded_risks (e.g. High (15/25))",
                "modulating_factors": "Contextual factors influencing inherent likelihood and severity (1-2 sentences)",
                "inherent_acceptability": "Why inherent risk requires mitigating controls under the Eliminate > Reduce > Transfer > Accept rule (1 sentence)",
                "measures": "Concrete technical, contractual and organisational safeguards applied (1-2 sentences)",
                "measure_effectiveness": "Appropriateness and effectiveness analysis showing how controls sever causal risk propagation under Eliminate > Reduce > Transfer > Accept (1-2 sentences)",
                "res_likelihood": "Qualitative + quantitative band from graded_risks (e.g. Low (L1))",
                "res_severity": "Qualitative + quantitative band from graded_risks (e.g. Medium (S3))",
                "res_level": "Qualitative + quantitative score from graded_risks (e.g. Low (3/25))",
                "residual_acceptability": "Acceptable - residual risk reduced to controlled range under implemented safeguards",
            }
        ],
        "residual_risk_conclusion": "Formal 2-3 sentence synthesis placed directly below Table 29 (4.2.b) confirming that under the Eliminate > Reduce > Transfer > Accept hierarchy, all inherent high/medium risks are reduced to acceptable residual scores (<= 12/25, zero unmitigated high risks) and stating whether GDPR Art. 36 / national prior supervisory consultation is triggered",
        "action_plan": "Ongoing compliance governance plan, responsible teams, pre-launch gates and re-evaluation triggers (3-4 sentences)",
        "dpo_advice": "Formal DPO / Chief Compliance Counsel review opinion, recommendations and sign-off confirmation (3-4 sentences)",
        "data_subject_views": "Summary of layered in-vehicle / mobile privacy notice transparency and user opt-out / rights feedback mechanisms (2-3 sentences)",
        "decision": {
            "outcome": "APPROVED | CONDITIONALLY_APPROVED | CONSULTATION | REJECTED",
            "condition_1": "Completion of statutory prior filing / standard contractual clauses / supervisory transfer authorisation prior to production activation, with strict whitelist field locking",
            "condition_2": "Continuous enforcement of on-vehicle edge masking, mTLS encryption, backup-inclusive expiry erasure, and a single-path circuit-breaker (immediate isolated suspension and local fallback of any non-compliant or anomalous sub-path)",
            "justification": "Formal synthesis (3-4 sentences) justifying conditional approval based on acceptable residual risk, mandatory pre-launch conditions, single-path suspension isolation, and annual/change-triggered DPIA re-evaluation",
        },
    }


def _set_cell_text(cell, text: str, *, append_below: bool = False) -> None:
    clean = _strip_cjk(text)
    if not clean:
        return
    paras = cell.findall(W + "p")
    template_rpr = None
    for r in cell.iter(W + "r"):
        rpr = r.find(W + "rPr")
        if rpr is not None:
            template_rpr = copy.deepcopy(rpr)
            for tag in ("i", "iCs", "b", "bCs", "color", "highlight", "rStyle"):
                for el in template_rpr.findall(W + tag):
                    template_rpr.remove(el)
            break
    if template_rpr is None:
        template_rpr = ET.Element(W + "rPr")
        sz = ET.SubElement(template_rpr, W + "sz")
        sz.set(W + "val", "18")
        sz_cs = ET.SubElement(template_rpr, W + "szCs")
        sz_cs.set(W + "val", "18")

    lines = [ln.strip() for ln in clean.splitlines() if ln.strip()]
    if not lines:
        return

    if not append_below:
        p0 = paras[0] if paras else ET.SubElement(cell, W + "p")
        ppr0 = copy.deepcopy(p0.find(W + "pPr")) if p0.find(W + "pPr") is not None else None
        for p in list(paras):
            cell.remove(p)
        for ln in lines:
            np = ET.SubElement(cell, W + "p")
            if ppr0 is not None:
                np.append(copy.deepcopy(ppr0))
            nr = ET.SubElement(np, W + "r")
            nr.append(copy.deepcopy(template_rpr))
            nt = ET.SubElement(nr, W + "t")
            nt.text = ln
    else:
        for idx, ln in enumerate(lines):
            np = ET.SubElement(cell, W + "p")
            ppr = ET.SubElement(np, W + "pPr")
            sp = ET.SubElement(ppr, W + "spacing")
            sp.set(W + "before", "80" if idx == 0 else "40")
            sp.set(W + "after", "40")
            nr = ET.SubElement(np, W + "r")
            nr.append(copy.deepcopy(template_rpr))
            nt = ET.SubElement(nr, W + "t")
            nt.text = ln


def _check_boxes_in_cell(cell, match_substrings: list[str]) -> None:
    """Tick Wingdings checkboxes on paragraphs inside ``cell`` whose text matches ``match_substrings``.
    A pattern starting with ``^`` anchors to the start of the stripped paragraph text so mutually
    exclusive options (e.g. ``Implemented`` vs ``Partially implemented``, ``APPROVED:`` vs
    ``CONDITIONALLY APPROVED:``) never collide."""
    lowered = [s.lower() for s in match_substrings if s]
    if not lowered:
        return
    for p in cell.findall(W + "p"):
        ptxt = re.sub(r"^[\s\uf000-\uf0ff☐☑☒✓✔•\-]+", "", _text(p)).strip().lower()
        matched = False
        for m in lowered:
            if m.startswith("^"):
                if ptxt.startswith(m[1:]):
                    matched = True
                    break
            elif m == "implemented":
                if ptxt == "implemented" or (ptxt.startswith("implemented") and "partially" not in ptxt):
                    matched = True
                    break
            elif m.startswith("approved:"):
                if ptxt.startswith("approved:") and not ptxt.startswith("conditionally"):
                    matched = True
                    break
            elif m in ptxt:
                matched = True
                break
        if matched:
            for sym in p.iter(W + "sym"):
                sym.set(W + "font", "Wingdings")
                sym.set(W + "char", CHECKED)


def _replace_underscore_in_cell(cell, needle_prefix: str, replacement_value: str) -> None:
    val = _strip_cjk(replacement_value)
    if not val:
        return
    for p in cell.findall(W + "p"):
        ptxt = _text(p)
        if needle_prefix.lower() in ptxt.lower():
            ts = list(p.iter(W + "t"))
            if ts:
                joined = "".join(t.text or "" for t in ts)
                updated = re.sub(r"_{3,}", " " + val, joined, count=1)
                if updated == joined:
                    updated = joined.rstrip() + " " + val
                ts[0].text = updated
                for extra in ts[1:]:
                    extra.text = ""


def _populate_dynamic_rows(tbl, rows_data: list[dict], builder) -> None:
    trs = tbl.findall(W + "tr")
    if len(trs) < 2 or not rows_data:
        return
    proto = copy.deepcopy(trs[1])
    for tr in trs[1:]:
        tbl.remove(tr)
    for idx, item in enumerate(rows_data, 1):
        new_tr = copy.deepcopy(proto)
        tcs = new_tr.findall(W + "tc")
        builder(idx, tcs, item)
        tbl.append(new_tr)


def _insert_note_below_table(body: ET.Element, tbl: ET.Element, label_prefix: str, note_text: str) -> None:
    """Insert a formal regulatory determination / conclusion paragraph immediately below ``tbl``."""
    clean_note = _strip_cjk(note_text)
    clean_label = _strip_cjk(label_prefix)
    if not clean_note:
        return
    children = list(body)
    if tbl not in children:
        return
    pos = children.index(tbl)
    p = ET.Element(W + "p")
    ppr = ET.SubElement(p, W + "pPr")
    sp = ET.SubElement(ppr, W + "spacing")
    sp.set(W + "before", "100")
    sp.set(W + "after", "140")
    sp.set(W + "line", "276")
    sp.set(W + "lineRule", "auto")

    def _make_rpr(*, bold: bool) -> ET.Element:
        rpr = ET.Element(W + "rPr")
        rf = ET.SubElement(rpr, W + "rFonts")
        rf.set(W + "ascii", "Calibri")
        rf.set(W + "hAnsi", "Calibri")
        rf.set(W + "cs", "Calibri")
        if bold:
            ET.SubElement(rpr, W + "b")
            ET.SubElement(rpr, W + "bCs")
        sz = ET.SubElement(rpr, W + "sz")
        sz.set(W + "val", "19")
        sz_cs = ET.SubElement(rpr, W + "szCs")
        sz_cs.set(W + "val", "19")
        return rpr

    if clean_label:
        r_lbl = ET.SubElement(p, W + "r")
        r_lbl.append(_make_rpr(bold=True))
        t_lbl = ET.SubElement(r_lbl, W + "t")
        t_lbl.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
        t_lbl.text = clean_label.rstrip() + " "

    r_txt = ET.SubElement(p, W + "r")
    r_txt.append(_make_rpr(bold=False))
    t_txt = ET.SubElement(r_txt, W + "t")
    t_txt.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
    t_txt.text = clean_note
    body.insert(pos + 1, p)


def render_edpb_2026_docx(data: dict[str, Any], assessment_date: str | None = None) -> bytes:
    """Populate ``EU_EDPB_DPIA_template_2026_v1_en.docx`` from structured English DPIA JSON."""
    raw_bytes = EDPB_TEMPLATE_PATH.read_bytes()
    zin = zipfile.ZipFile(io.BytesIO(raw_bytes))
    original_xml = zin.read("word/document.xml")
    _register_namespaces(original_xml)
    root = ET.fromstring(original_xml)
    body = root.find(W + "body")
    tables = body.findall(W + "tbl")
    today_str = assessment_date or date.today().isoformat()

    ctrl = data.get("controller") or {}
    t1 = tables[1].findall(W + "tr")
    _set_cell_text(t1[0].findall(W + "tc")[1], str(ctrl.get("name_and_address") or ""))
    _set_cell_text(t1[1].findall(W + "tc")[1], str(ctrl.get("units") or ""))
    _set_cell_text(t1[2].findall(W + "tc")[1], str(ctrl.get("contact_or_rep") or ""))
    _set_cell_text(t1[3].findall(W + "tc")[1], str(ctrl.get("dpo_info") or ""))

    procs = (data.get("processors") or [])[:3]
    t2_trs = tables[2].findall(W + "tr")
    for ri in range(1, min(len(t2_trs), 4)):
        tcs = t2_trs[ri].findall(W + "tc")
        if ri - 1 < len(procs):
            p = procs[ri - 1]
            _set_cell_text(tcs[0], str(ri))
            _set_cell_text(tcs[1], str(p.get("name") or ""))
            _set_cell_text(tcs[2], str(p.get("obligations") or ""))
        else:
            _set_cell_text(tcs[0], "—" if ri == 1 else "")
            _set_cell_text(tcs[1], "Not applicable (no additional sub-processor engaged)" if ri > len(procs) and ri == 2 else "")
            _set_cell_text(tcs[2], "Onward transfer or sub-processing without prior written consent is strictly prohibited by contract." if ri == 2 and len(procs) == 1 else "")

    t3 = tables[3].findall(W + "tr")
    _set_cell_text(t3[0].findall(W + "tc")[1], str(data.get("processing_name") or "Connected Vehicle Cross-Border Data Processing"))
    _set_cell_text(t3[1].findall(W + "tc")[1], str(data.get("version_history") or f"Version 1.0 ({today_str}) — Initial pre-deployment assessment"))

    plan = data.get("planning") or {}
    t4 = tables[4].findall(W + "tr")
    _set_cell_text(t4[0].findall(W + "tc")[1], str(plan.get("launch_date") or f"Upon completion of statutory filing / authorisation ({today_str})"))
    _set_cell_text(t4[1].findall(W + "tc")[1], str(plan.get("expiration") or "Continuous operation subject to strict retention limits and annual / change-triggered DPIA review"))

    items = (data.get("data_items") or [])[:6]
    any_special = any(
        it.get("is_special") or str(it.get("special_type") or "none").lower() not in ("", "none", "no", "false")
        for it in items
    )

    ts = data.get("tech_sheet") or {}
    t5 = tables[5].findall(W + "tr")
    _set_cell_text(t5[0].findall(W + "tc")[1], f"Version 1.0 ({today_str}) — Prepared in accordance with EDPB DPIA Template 2026 v1.0")
    _set_cell_text(t5[1].findall(W + "tc")[1], str(ts.get("dpia_team") or ""))
    _set_cell_text(t5[2].findall(W + "tc")[1], str(ts.get("reference_materials") or ""))
    t5_criteria = ["^systematic monitoring:", "^data processed on a large scale", "^innovative use"]
    if any_special:
        t5_criteria.insert(0, "^processing on a large scale of special categories")
    _check_boxes_in_cell(t5[3].findall(W + "tc")[1], t5_criteria)
    _set_cell_text(t5[4].findall(W + "tc")[1], str(ts.get("scope") or ""))
    _set_cell_text(t5[5].findall(W + "tc")[1], today_str)
    _set_cell_text(t5[6].findall(W + "tc")[1], today_str)
    _check_boxes_in_cell(t5[7].findall(W + "tc")[1], ["shared externally"])
    _replace_underscore_in_cell(t5[7].findall(W + "tc")[1], "shared externally", "Submitted confidentially to competent supervisory authority upon statutory filing/request; executive summary available to data subjects.")

    if items:
        def _fill_t6(idx: int, tcs: list, it: dict) -> None:
            _set_cell_text(tcs[0], str(idx))
            _set_cell_text(tcs[1], str(it.get("item") or ""))
            _set_cell_text(tcs[2], str(it.get("explanation") or it.get("need_justification") or ""))
            spec_type_raw = str(it.get("special_type") or "none").strip().lower()
            if it.get("is_special") or spec_type_raw not in ("", "none", "no", "false"):
                spec_blob = f"{spec_type_raw} {it.get('item') or ''} {it.get('explanation') or ''}".lower()
                picks = ["^yes:"]
                if re.search(
                    r"health|medical|heart|vital|fatigue|drows|physiolog|illness|clinical|patient",
                    spec_blob,
                ):
                    picks.append("data concerning health")
                if re.search(r"genetic|dna|genome|chromosom", spec_blob):
                    picks.append("genetic data")
                if re.search(r"racial|ethnic|political|religi|philosoph|trade\s+union", spec_blob):
                    picks.append("data revealing racial")
                if re.search(r"\bsex|sexual\s+orientation", spec_blob):
                    picks.append("sex life or sexual orientation")
                if (
                    re.search(
                        r"biometric|face|facial|voice|voiceprint|iris|fingerprint|gait|retina",
                        spec_blob,
                    )
                    or len(picks) == 1
                ):
                    picks.append("biometric data")
                _check_boxes_in_cell(tcs[3], picks)
            else:
                _check_boxes_in_cell(tcs[3], ["^no"])

        _populate_dynamic_rows(tables[6], items, _fill_t6)

    lb_meta = data.get("legal_basis") or {}
    spec_det = str(
        data.get("special_category_determination")
        or lb_meta.get("special_justification")
        or (
            "Special-category data scope is strictly restricted to the explicitly declared items and isolated under explicit consent and edge security enclaves."
            if any_special
            else "All connected-vehicle telemetry and optical/location inputs undergo on-vehicle edge de-identification without extracting biometric templates for unique natural-person identification; no GDPR Article 9(1) special-category processing is triggered."
        )
    )
    _insert_note_below_table(body, tables[6], "Special-Category Data Determination Note:", spec_det)

    purposes = (data.get("purposes") or [])[:3]
    t7_trs = tables[7].findall(W + "tr")
    for ri in range(1, min(len(t7_trs), 4)):
        tcs = t7_trs[ri].findall(W + "tc")
        if ri - 1 < len(purposes):
            pu = purposes[ri - 1]
            _set_cell_text(tcs[0], str(ri))
            _set_cell_text(tcs[1], str(pu.get("purpose") or ""))
            _set_cell_text(tcs[2], str(pu.get("justification") or ""))
        else:
            _set_cell_text(tcs[0], "")
            _set_cell_text(tcs[1], "—")
            _set_cell_text(tcs[2], "No additional processing purpose.")

    t8_trs = tables[8].findall(W + "tr")
    _set_cell_text(t8_trs[1].findall(W + "tc")[0], "1")
    _set_cell_text(t8_trs[1].findall(W + "tc")[1], "No secondary or commercial profiling uses")
    _set_cell_text(t8_trs[1].findall(W + "tc")[2], str(data.get("secondary_uses") or "Personal data is strictly confined to the specified primary automotive purposes; any secondary use is contractually and technically prohibited."))
    for ri in range(2, len(t8_trs)):
        for ci in range(3):
            _set_cell_text(t8_trs[ri].findall(W + "tc")[ci], "")

    nsc = data.get("nature_scope_context") or {}
    t9 = tables[9].findall(W + "tr")
    _set_cell_text(t9[0].findall(W + "tc")[1], str(nsc.get("nature") or ""))
    _set_cell_text(t9[1].findall(W + "tc")[1], str(nsc.get("scope") or ""))
    c_ctx = t9[2].findall(W + "tc")[1]
    ctx_ps = c_ctx.findall(W + "p")
    ctx_main = _strip_cjk(
        str(
            nsc.get("context")
            or "Connected-vehicle telematics, safety diagnostics and cross-regional engineering collaboration operated under privacy-by-default controls, transparent layered notices and strict contractual purpose locks."
        )
    )
    if ctx_ps and not _text(ctx_ps[0]).strip():
        r_ctx0 = ET.SubElement(ctx_ps[0], W + "r")
        t_ctx0 = ET.SubElement(r_ctx0, W + "t")
        t_ctx0.text = ctx_main
        for p_blank in ctx_ps[1:]:
            if not _text(p_blank).strip() and not list(p_blank.iter(W + "sym")):
                c_ctx.remove(p_blank)
    _check_boxes_in_cell(c_ctx, ["yes (justification", "yes ("])
    yes_ps = [p for p in c_ctx.findall(W + "p") if "yes" in _text(p).lower()]
    cb_scope = _strip_cjk(
        "Cross-jurisdictional coordination between the local data controller / market operator and designated technical support units under unified data protection governance."
    )
    cb_transfer = _strip_cjk(
        str(
            nsc.get("context_cross_border")
            or "Cross-border transfer to the designated recipient is governed by enforceable transfer safeguards, edge de-identification, end-to-end encryption and strict purpose limitation."
        )
    )
    for idx_y, p_y in enumerate(yes_ps):
        val_y = cb_scope if idx_y == 0 and len(yes_ps) > 1 else cb_transfer
        ts_y = list(p_y.iter(W + "t"))
        if ts_y:
            joined_y = "".join(t.text or "" for t in ts_y)
            updated_y = re.sub(r"_{3,}", " " + val_y, joined_y, count=1)
            ts_y[0].text = updated_y if updated_y != joined_y else (joined_y.rstrip() + " " + val_y)
            for extra_y in ts_y[1:]:
                extra_y.text = ""

    phases = (data.get("phases") or [])[:5]
    if phases:
        def _fill_t10(idx: int, tcs: list, ph: dict) -> None:
            _set_cell_text(tcs[0], str(idx))
            _set_cell_text(tcs[1], str(ph.get("phase") or ""))
            ops = [str(x) for x in (ph.get("ops") or ["Collection", "Use"])]
            _check_boxes_in_cell(tcs[2], ops)
            _set_cell_text(tcs[3], str(ph.get("explanation") or ""))

        _populate_dynamic_rows(tables[10], phases, _fill_t10)

        def _fill_t11(_idx: int, tcs: list, ph: dict) -> None:
            _set_cell_text(tcs[0], str(ph.get("phase") or ""))
            _set_cell_text(tcs[1], str(ph.get("assets") or ""))
            _set_cell_text(tcs[2], str(ph.get("architecture_note") or ""))

        _populate_dynamic_rows(tables[11], phases, _fill_t11)

    coc = data.get("codes_of_conduct") or {}
    t12 = tables[12].findall(W + "tr")[1].findall(W + "tc")
    _set_cell_text(t12[1], str(coc.get("code") or ""))
    _check_boxes_in_cell(t12[2], ["necessary or beneficial"])
    _replace_underscore_in_cell(t12[2], "necessary or beneficial. why?:", str(coc.get("explanation") or ""))

    lb = data.get("legal_basis") or {}
    choice_map = {
        "consent": "(a) the data subject has given consent",
        "contract": "(b) the processing is necessary for the performance of a contract",
        "legal_obligation": "(c) the processing is necessary for compliance with a legal obligation",
        "legitimate_interest": "(f) the processing is necessary for the purposes of the legitimate interests",
        "legitimate_interests": "(f) the processing is necessary for the purposes of the legitimate interests",
    }
    lb_rows = [r for r in (data.get("legal_basis_rows") or []) if isinstance(r, dict) and (r.get("purpose_group") or r.get("purpose"))][:4]
    if lb_rows:
        def _fill_t13(_idx: int, tcs: list, row_item: dict) -> None:
            _set_cell_text(tcs[0], str(row_item.get("purpose_group") or row_item.get("purpose") or ""))
            raw_choice = str(row_item.get("basis_choice") or "contract").lower().strip()
            _check_boxes_in_cell(tcs[1], [choice_map.get(raw_choice, "(b) the processing is necessary")])
            _set_cell_text(tcs[2], str(row_item.get("justification") or ""))

        _populate_dynamic_rows(tables[13], lb_rows, _fill_t13)
    else:
        t13 = tables[13].findall(W + "tr")[1].findall(W + "tc")
        _set_cell_text(t13[0], str(lb.get("purpose") or data.get("processing_name") or "Connected vehicle cross-border diagnostics and safety operations"))
        sel = lb.get("selected") if isinstance(lb.get("selected"), list) else [lb.get("basis_choice") or "contract"]
        picks = [choice_map.get(str(s or "").lower().strip(), "(b) the processing is necessary") for s in sel]
        _check_boxes_in_cell(t13[1], picks or ["(b) the processing is necessary"])
        _set_cell_text(t13[2], str(lb.get("justification") or ""))

    lia_note = str(
        data.get("lia_balancing_note")
        or (
            "(1) Legitimate Purpose: Ensuring connected-vehicle operational safety, cybersecurity integrity and defect root-cause diagnostics constitutes a concrete, lawful interest; "
            "(2) Necessity: Event-triggered pseudonymised telemetry and real-time on-vehicle edge masking represent the least intrusive means capable of achieving this safety objective; "
            "(3) Balancing: Privacy-by-default off-switches, strict retention caps and immediate opt-out/objection channels ensure data subjects' fundamental rights and reasonable expectations are not overridden."
        )
    )
    _insert_note_below_table(body, tables[13], "Legitimate Interest Balancing Test Summary:", lia_note)

    t14 = tables[14].findall(W + "tr")[1].findall(W + "tc")
    _set_cell_text(t14[0], str(lb.get("special_data_item") or "Not applicable — raw biometric features masked on vehicle edge"))
    if any(it.get("is_special") for it in items):
        _check_boxes_in_cell(t14[1], ["(a) the data subject has given explicit consent"])
    _set_cell_text(t14[2], str(lb.get("special_justification") or ""))

    if items:
        def _fill_t15(_idx: int, tcs: list, it: dict) -> None:
            _set_cell_text(tcs[0], str(it.get("item") or ""))
            _set_cell_text(tcs[1], str(it.get("need_justification") or ""))
            _set_cell_text(tcs[2], str(it.get("recipient") or ""))
            _set_cell_text(tcs[3], str(it.get("recipient_justification") or ""))
            _set_cell_text(tcs[4], str(it.get("retention") or ""))
            _set_cell_text(tcs[5], str(it.get("retention_justification") or ""))

        _populate_dynamic_rows(tables[15], items, _fill_t15)

        def _fill_t16(_idx: int, tcs: list, it: dict) -> None:
            _set_cell_text(tcs[0], str(it.get("item") or ""))
            _set_cell_text(tcs[1], str(it.get("quality_metric") or ""))
            _set_cell_text(tcs[2], str(it.get("quality_justification") or ""))

        _populate_dynamic_rows(tables[16], items, _fill_t16)

    princ = data.get("principles") or {}
    princ_keys = [
        "fairness", "transparency", "purpose_limitation", "data_minimisation",
        "accuracy", "storage_limitation", "integrity_confidentiality", "accountability",
    ]
    t17_trs = tables[17].findall(W + "tr")
    for idx, pk in enumerate(princ_keys, 1):
        if idx < len(t17_trs):
            tcs = t17_trs[idx].findall(W + "tc")
            pv = princ.get(pk) or {}
            _set_cell_text(tcs[1], str(pv.get("measures") or ""))
            _set_cell_text(tcs[2], str(pv.get("discussion") or ""))
            _check_boxes_in_cell(tcs[3], ["implemented"])

    rights = data.get("rights") or {}
    r_keys = ["information", "access_portability", "rectification_erasure", "objection_restriction", "automated_decisions"]
    t18_trs = tables[18].findall(W + "tr")
    for idx, rk in enumerate(r_keys, 1):
        if idx < len(t18_trs):
            tcs = t18_trs[idx].findall(W + "tc")
            rv = rights.get(rk) or {}
            _set_cell_text(tcs[1], str(rv.get("measures") or ""))
            _set_cell_text(tcs[2], str(rv.get("discussion") or ""))
            _check_boxes_in_cell(tcs[3], ["implemented"])

    og = data.get("other_gdpr") or {}
    t19_trs = tables[19].findall(W + "tr")
    for idx, ok in enumerate(["consent", "processors", "transfers"], 1):
        if idx < len(t19_trs):
            tcs = t19_trs[idx].findall(W + "tc")
            ov = og.get(ok) or {}
            _set_cell_text(tcs[1], str(ov.get("measures") or ""))
            _set_cell_text(tcs[2], str(ov.get("discussion") or ""))
            _check_boxes_in_cell(tcs[3], ["implemented"])

    bdd = (data.get("by_design_default") or [])[:2]
    t20_trs = tables[20].findall(W + "tr")
    for idx, bv in enumerate(bdd, 1):
        if idx < len(t20_trs):
            tcs = t20_trs[idx].findall(W + "tc")
            _set_cell_text(tcs[0], str(bv.get("aspect") or ""))
            _set_cell_text(tcs[1], str(bv.get("measures") or ""))
            _set_cell_text(tcs[2], str(bv.get("discussion") or ""))
            _check_boxes_in_cell(tcs[3], ["implemented"])

    sec = (data.get("security_measures") or [])[:2]
    t21_trs = tables[21].findall(W + "tr")
    for idx, sv in enumerate(sec, 1):
        if idx < len(t21_trs):
            tcs = t21_trs[idx].findall(W + "tc")
            _set_cell_text(tcs[0], str(sv.get("domain") or ""))
            _set_cell_text(tcs[1], str(sv.get("measures") or ""))
            _set_cell_text(tcs[2], str(sv.get("discussion") or ""))
            _check_boxes_in_cell(tcs[3], ["implemented"])

    threats = (data.get("abnormal_threats") or [])[:4]
    intended_threats = (data.get("intended_threats") or [])[:4]
    if not intended_threats and threats:
        intended_threats = [
            {
                "threat": "Over-collection beyond strictly necessary diagnostic telemetry",
                "materialisation": "Continuous background vehicle telemetry could capture granular behavioural or location patterns beyond episodic fault diagnostics if whitelist filters drift.",
                "sources": "Broad sensor configuration or unreviewed firmware telemetry schema expansion",
                "impact": "Intrusive behavioural inference or erosion of driver and passenger expectations of informational autonomy.",
            },
            {
                "threat": "Residual re-identification linkage across pseudonymised VIN tokens",
                "materialisation": "Cross-referencing pseudonymised vehicle event logs with service-centre warranty records could indirectly single out the registered vehicle owner.",
                "sources": "Insufficient separation between local identity mapping vaults and cross-border engineering datasets",
                "impact": "Loss of effective pseudonymity and unintended attribution of vehicle operating events to identifiable individuals.",
            },
            {
                "threat": "Extended retention or secondary repurposing in recipient environment",
                "materialisation": "Retaining diagnostic snapshots past incident resolution could expose historical records to secondary analytics outside the declared safety purpose.",
                "sources": "Manual archive retention or unsynchronised backup purge schedules",
                "impact": "Prolonged exposure window and loss of data-subject control over personal data lifecycle.",
            },
        ]
    if intended_threats or threats:
        def _fill_threat(idx: int, tcs: list, th: dict) -> None:
            _set_cell_text(tcs[0], str(idx))
            _set_cell_text(tcs[1], str(th.get("threat") or ""))
            _set_cell_text(tcs[2], str(th.get("materialisation") or ""))
            _set_cell_text(tcs[3], str(th.get("sources") or ""))
            _set_cell_text(tcs[4], str(th.get("impact") or ""))

        if intended_threats:
            _populate_dynamic_rows(tables[22], intended_threats, _fill_threat)
        if threats:
            _populate_dynamic_rows(tables[25], threats, _fill_threat)

    _set_cell_text(tables[23].findall(W + "tr")[0].findall(W + "tc")[0], str(data.get("necessity_assessment") or ""), append_below=True)
    _set_cell_text(tables[24].findall(W + "tr")[0].findall(W + "tc")[0], str(data.get("proportionality_assessment") or ""), append_below=True)
    _set_cell_text(tables[26].findall(W + "tr")[0].findall(W + "tc")[0], str(data.get("risk_method") or ""), append_below=True)

    rrows = (data.get("risk_rows") or [])[:6]
    if rrows:
        def _fill_t27(idx: int, tcs: list, rr: dict) -> None:
            _set_cell_text(tcs[0], str(idx))
            _set_cell_text(tcs[1], str(rr.get("risk") or ""))
            _set_cell_text(tcs[2], str(rr.get("init_likelihood") or "Medium"))
            _set_cell_text(tcs[3], str(rr.get("init_severity") or "Medium"))
            _set_cell_text(tcs[4], str(rr.get("modulating_factors") or ""))
            _set_cell_text(tcs[5], str(rr.get("init_level") or "Medium"))
            _set_cell_text(tcs[6], str(rr.get("inherent_acceptability") or ""))

        _populate_dynamic_rows(tables[27], rrows, _fill_t27)

        def _fill_t28(idx: int, tcs: list, rr: dict) -> None:
            _set_cell_text(tcs[0], str(idx))
            _set_cell_text(tcs[1], str(rr.get("measures") or ""))
            _set_cell_text(tcs[2], f"Risk {idx}: {rr.get('risk', '')}")
            _set_cell_text(tcs[3], str(rr.get("measure_effectiveness") or rr.get("residual_acceptability") or ""))
            _check_boxes_in_cell(tcs[4], ["implemented"])

        _populate_dynamic_rows(tables[28], rrows, _fill_t28)

        def _fill_t29(idx: int, tcs: list, rr: dict) -> None:
            _set_cell_text(tcs[0], str(idx))
            _set_cell_text(tcs[1], str(rr.get("risk") or ""))
            _set_cell_text(tcs[2], str(rr.get("measures") or ""))
            _set_cell_text(tcs[3], str(rr.get("res_likelihood") or "Low"))
            _set_cell_text(tcs[4], str(rr.get("res_severity") or "Low"))
            _set_cell_text(tcs[5], str(rr.get("res_level") or "Low"))
            _set_cell_text(tcs[6], str(rr.get("residual_acceptability") or "Acceptable"))

        _populate_dynamic_rows(tables[29], rrows, _fill_t29)

    res_concl = str(
        data.get("residual_risk_conclusion")
        or (
            "Following the Eliminate > Reduce > Transfer > Accept risk treatment hierarchy, all identified inherent risks are mitigated to acceptable Low/Medium residual levels through on-vehicle edge de-identification, mTLS encryption, RBAC isolation and enforceable transfer clauses; no unmitigated high residual risk remains that would trigger mandatory prior consultation under Article 36(1) GDPR."
        )
    )
    _insert_note_below_table(body, tables[29], "Residual Risk Conclusion:", res_concl)

    _set_cell_text(tables[30].findall(W + "tr")[0].findall(W + "tc")[0], str(data.get("action_plan") or ""), append_below=True)
    _set_cell_text(tables[31].findall(W + "tr")[0].findall(W + "tc")[0], str(data.get("dpo_advice") or ""), append_below=True)
    _set_cell_text(tables[32].findall(W + "tr")[0].findall(W + "tc")[0], str(data.get("data_subject_views") or ""), append_below=True)

    dec = data.get("decision") or {}
    c33 = tables[33].findall(W + "tr")[0].findall(W + "tc")[0]
    outcome_norm = re.sub(r"[\s\-]+", "_", str(dec.get("outcome") or "CONDITIONALLY_APPROVED").strip().upper())
    if any(x in outcome_norm for x in ("REJECT", "NOT_APPROVED", "DISAPPROVED", "PROHIBITED", "ABANDON")):
        _check_boxes_in_cell(c33, ["^rejected:"])
    elif "CONSULT" in outcome_norm:
        _check_boxes_in_cell(c33, ["^consultation:"])
    elif "CONDITIONALLY" in outcome_norm or "DEFER" in outcome_norm:
        _check_boxes_in_cell(c33, ["^conditionally approved:"])
        _replace_underscore_in_cell(c33, "condition 1:", str(dec.get("condition_1") or ""))
        _replace_underscore_in_cell(c33, "condition 2:", str(dec.get("condition_2") or ""))
    else:
        _check_boxes_in_cell(c33, ["^approved:"])
    _set_cell_text(tables[34].findall(W + "tr")[0].findall(W + "tc")[0], str(dec.get("justification") or ""))

    _unlink_broken_ref_fields(root)
    for idx, sect in enumerate(body.iter(W + "sectPr")):
        pg_num = sect.find(W + "pgNumType")
        if idx == 0:
            if pg_num is None:
                pg_num = ET.SubElement(sect, W + "pgNumType")
            pg_num.set(W + "fmt", "decimal")
            pg_num.set(W + "start", "1")
        elif pg_num is not None:
            pg_num.attrib.pop(W + "start", None)
    xml_bytes = _serialise(root, original_xml)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zout:
        for info in zin.infolist():
            if info.filename == "word/document.xml":
                zout.writestr(info, xml_bytes)
            elif info.filename == "word/footer1.xml":
                ftr_txt = zin.read(info.filename).decode("utf-8")
                ftr_txt = ftr_txt.replace(
                    '<w:fldChar w:fldCharType="begin"/>',
                    '<w:fldChar w:fldCharType="begin" w:dirty="true"/>',
                )
                zout.writestr(info, ftr_txt.encode())
            elif info.filename == "word/settings.xml":
                st_raw = zin.read(info.filename)
                if b"updateFields" not in st_raw:
                    st_txt = st_raw.decode("utf-8", errors="ignore").replace(
                        "</w:settings>", '<w:updateFields w:val="true"/></w:settings>', 1
                    )
                    st_raw = st_txt.encode()
                zout.writestr(info, st_raw)
            else:
                zout.writestr(info, zin.read(info.filename))
    return buf.getvalue()


def _unlink_broken_ref_fields(root: ET.Element) -> None:
    """Unlink Word REF cross-reference field codes into their cached static text (e.g. '6.2')
    so localized MS Word never overwrites them with localized error strings during PDF export."""
    for p in root.iter(W + "p"):
        children = list(p)
        idx = 0
        while idx < len(children):
            el = children[idx]
            fc = el.find(W + "fldChar") if el.tag == W + "r" else None
            if fc is not None and fc.get(W + "fldCharType") == "begin":
                sep_idx = None
                end_idx = None
                instr_parts: list[str] = []
                for j in range(idx + 1, len(children)):
                    c = children[j]
                    if c.tag == W + "r":
                        cfc = c.find(W + "fldChar")
                        if cfc is not None:
                            ftype = cfc.get(W + "fldCharType")
                            if ftype == "separate" and sep_idx is None:
                                sep_idx = j
                            elif ftype == "end":
                                end_idx = j
                                break
                        if sep_idx is None:
                            for it in c.findall(W + "instrText"):
                                instr_parts.append("".join(it.itertext()))
                instr = "".join(instr_parts).strip().upper()
                if end_idx is not None and instr.startswith("REF "):
                    drop_indices = set(range(idx, (sep_idx if sep_idx is not None else end_idx) + 1)) | {end_idx}
                    for k in sorted(drop_indices, reverse=True):
                        p.remove(children[k])
                    children = list(p)
                    continue
            idx += 1


async def build_edpb_2026_en(
    session: AsyncSession,
    task,
    values: dict[str, str],
    citations: list[dict],
    *,
    draft: bool = False,
    actor_user_id: uuid.UUID | None = None,
    read_only_viewer: bool = False,
) -> tuple[bytes, str]:
    """Generate or fetch cached English EDPB 2026 official DPIA .docx for a non-China outbound project."""
    from fastapi import HTTPException

    from app.core.redaction import bind_egress, egress_context, llm_scope
    from app.iam.models import Organization

    ihash = _edpb_inputs_hash(values, citations, draft)
    cached = await session.scalar(
        select(DocumentNarrative).where(
            DocumentNarrative.task_id == task.id,
            DocumentNarrative.code == EDPB_NARRATIVE_CODE,
            DocumentNarrative.inputs_hash == ihash,
        )
    )
    assessment_dt = str(values.get("self_assessed_on") or "").strip() or date.today().isoformat()
    if cached and isinstance(cached.report, dict) and isinstance(cached.report.get("edpb_data"), dict):
        try:
            return render_edpb_2026_docx(cached.report["edpb_data"], assessment_dt), "EDPB_2026_DPIA_Official_EN"
        except (AttributeError, TypeError, KeyError, ValueError):
            if not read_only_viewer:
                await session.delete(cached)
                await session.flush()

    if read_only_viewer:
        raise HTTPException(403, "查看账号只能查阅或导出已生成的欧盟标准英文范本，不能触发新建生成")

    parsed_risks = [r for r in risk_items.parse(values.get("risk_rating", "")) if r.get("mitigated") != "1"]
    structured_risks = []
    for r in parsed_risks:
        raw_il = str(r.get("init_likelihood") or r.get("likelihood") or "中")
        raw_isv = str(r.get("init_severity") or r.get("severity") or "中")
        raw_rl = str(r.get("likelihood") or "低")
        raw_rsv = str(r.get("severity") or "低")
        il = _LEVEL_EN.get(raw_il, "Medium")
        isv = _LEVEL_EN.get(raw_isv, "Medium")
        rl = _LEVEL_EN.get(raw_rl, "Low")
        rsv = _LEVEL_EN.get(raw_rsv, "Low")
        iq = risk_items.quant_triplet(raw_il, raw_isv) or (3, 3, 9)
        rq = risk_items.quant_triplet(raw_rl, raw_rsv) or (1, 1, 1)
        ilv_en = _LEVEL_EN.get(risk_items.init_level(r), "Medium")
        rlv_en = _LEVEL_EN.get(risk_items.level(r), "Low")
        structured_risks.append(
            {
                "risk_zh": r.get("risk", ""),
                "measures_zh": r.get("measures", ""),
                "owner_zh": r.get("owner", ""),
                "init_likelihood": f"{il} (L{iq[0]})",
                "init_severity": f"{isv} (S{iq[1]})",
                "init_level": f"{ilv_en} ({iq[2]}/25)",
                "res_likelihood": f"{rl} (L{rq[0]})",
                "res_severity": f"{rsv} (S{rq[1]})",
                "res_level": f"{rlv_en} ({rq[2]}/25)",
            }
        )

    offered_laws = [
        {
            "title_zh": str(c.get("document_title") or ""),
            "original_title": str(c.get("original_title") or ""),
            "clause": str(c.get("clause_number") or ""),
            "excerpt": clause_gate.cut(str(c.get("quote_original") or c.get("quote_zh") or ""), 320),
        }
        for c in citations[:14]
    ]
    clean_facts = {
        k: str(v).strip()
        for k, v in values.items()
        if str(v or "").strip() and not str(v).strip().startswith("暂不确定") and k != "risk_rating"
    }
    base_payload = {
        "draft": draft,
        "project_title": getattr(task, "title", ""),
        "facts": clean_facts,
        "graded_risks": structured_risks,
        "statutory_citations": offered_laws,
    }

    org = await session.get(Organization, task.org_id) if getattr(task, "org_id", None) else None
    cjk_aliases: list[str] = []
    try:
        with llm_scope():
            bind_egress(session, org)
            cjk_aliases = [a for a in egress_context.get().aliases if a and _CJK_RE.search(a)]
            res1, res2 = await asyncio.gather(
                call_llm_json(
                    [
                        {"role": "system", "content": SYSTEM_EDPB_EN},
                        {"role": "user", "content": json.dumps({**base_payload, "schema": _schema_part1()}, ensure_ascii=False)},
                    ],
                    max_tokens=14000,
                    temperature=0.1,
                    timeout=240.0,
                ),
                call_llm_json(
                    [
                        {"role": "system", "content": SYSTEM_EDPB_EN},
                        {"role": "user", "content": json.dumps({**base_payload, "schema": _schema_part2()}, ensure_ascii=False)},
                    ],
                    max_tokens=14000,
                    temperature=0.1,
                    timeout=240.0,
                ),
            )
    except LlmUnavailableError as exc:
        raise RuntimeError(f"英文 EDPB 官方表生成服务暂时不可用：{exc}") from exc

    part1 = res1.data if isinstance(res1.data, dict) else {}
    part2 = res2.data if isinstance(res2.data, dict) else {}
    raw_edpb = {**part1, **part2}
    if not isinstance(raw_edpb.get("controller"), dict) or not raw_edpb["controller"]:
        raise RuntimeError("英文 EDPB 官方表结构化输出未通过核验，请重试")
    edpb_data = _sanitise_edpb_tree(raw_edpb, cjk_aliases)
    try:
        rendered_docx = render_edpb_2026_docx(edpb_data, assessment_dt)
    except (AttributeError, TypeError, KeyError, ValueError) as exc:
        raise RuntimeError("英文 EDPB 官方表结构化输出未通过核验，请重试") from exc

    tokens = (
        int(getattr(res1, "prompt_tokens", 0) or 0)
        + int(getattr(res1, "completion_tokens", 0) or 0)
        + int(getattr(res2, "prompt_tokens", 0) or 0)
        + int(getattr(res2, "completion_tokens", 0) or 0)
    )
    row = DocumentNarrative(
        org_id=task.org_id,
        task_id=task.id,
        code=EDPB_NARRATIVE_CODE,
        inputs_hash=ihash,
        blocks=[],
        report={"edpb_data": edpb_data, "draft": draft},
        tokens=tokens,
        created_by=actor_user_id or getattr(task, "created_by", None),
    )
    from sqlalchemy.exc import IntegrityError

    session.add(row)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
    return rendered_docx, "EDPB_2026_DPIA_Official_EN"
