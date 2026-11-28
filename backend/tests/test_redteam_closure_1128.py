# ruff: noqa: E501
"""Permanent offline regression suite for Wave-1 7-Perspective Red-Team Closure (2026-11-28).

Covers all P0/P1/P2/P3 root causes across:
- Perspective 1 & 6: 17-language fault injection (0 MissingGreenlet 500), Redactor CYSH_SECRET gate,
  zero-Chinese export.zip (docx/pdf/xlsx + aids/txt), grid table spans/no_head, explicit RTL boundary,
  Japanese/Thai linguistic gates, advice chat clone + attachment copy, custom scenario preservation.
- Perspective 2 & 3: Whitepaper table geometry validation, ops_admin RLS scope='ops' isolation,
  viewer private chat cross-router isolation, viewer 403-before-422 priority, document_edits append-only
  immutability across DELETE /saved, CWE-1236 Unicode/newline formula neutralization.
- Perspective 4 & 9: 119-jurisdiction ISO-3166 multi-language coverage (BD/DZ/KG/PE/UY/MT),
  empty-DB active_overseas_countries, unified PDF render semaphore & bounded caches.
"""

from __future__ import annotations

import io
import re
import uuid
import zipfile

import httpx
import openpyxl
import pytest
from sqlalchemy import func, select

from app.admission.models import AdmissionEntry
from app.analysis.attachments import AttachmentReview, TaskAttachment
from app.analysis.document_basis import WORLD, country_of
from app.analysis.document_instances import DocumentEdit
from app.analysis.document_templates import Rendered, build_docx
from app.analysis.english_report import COUNTRY_EN, strip_and_translate_cjk
from app.analysis.multilingual_report import (
    _inject_thai_zwsp,
    clean_and_gate_target_text,
    has_untranslated_chinese_leak,
    load_lang_lexicon,
)
from app.analysis.workflow_engine import active_overseas_countries, is_overseas_country
from app.core.jargon import scrub_engineering_jargon
from app.core.redaction import Redactor
from app.core.spreadsheet import sanitize_csv_cell, write_safe_xlsx_cell
from app.iam import service
from app.iam.models import Role
from app.ingestion.models import Clause, Document, DocumentVersion
from app.knowledge.models import ClauseTranslation
from app.knowledge.whitepaper import CountryWhitepaper
from app.sources.models import SourceSite

from .conftest import PASSWORD, bearer, login


def test_redactor_jargon_and_spreadsheet_hardening():
    # 1. Redactor.restore rejects mutated or echoed CYSH_SECRET placeholders
    red = Redactor(["华晨星途智行汽车制造有限公司", "昆仑X9绝密架构"])
    masked = red.redact("主体：华晨星途智行汽车制造有限公司，平台：昆仑X9绝密架构")
    assert "__CYSH_SECRET_" in masked
    assert "华晨星途智行汽车制造有限公司" in red.restore(masked)
    with pytest.raises(ValueError):
        red.restore("Translated for __CYSH_SECRET_DEADBEEF00112233_A__")
    with pytest.raises(ValueError):
        red.restore("Keep token __CYSH_SECRET_...__ intact")

    # 2. Engineering jargon scrubber catches newly identified tokens
    cleaned = scrub_engineering_jargon("DeepSeek SQLAlchemy Traceback in 125ms (ReadTimeout)")
    for bad in ("DeepSeek", "SQLAlchemy", "Traceback", "125ms", "ReadTimeout"):
        assert bad not in cleaned

    # 3. CWE-1236 formula injection neutralization across leading newline / NBSP / tab
    for payload in (
        "\n=HYPERLINK(\"https://evil.example\",\"x\")",
        "\u00a0=1+1",
        "=1+1",
        "\t=1+1",
        "+1+1",
        "-1+1",
    ):
        assert sanitize_csv_cell(payload).startswith("'")
        wb = openpyxl.Workbook()
        ws = wb.active
        cell = write_safe_xlsx_cell(ws, 1, 1, payload)
        assert str(cell.value).startswith("'")
        assert cell.quotePrefix is True

    # Legitimate space-separated phone numbers keep unquoted text with quotePrefix in XLSX; +86-21 arithmetic-like is quoted; em-dash stays clean
    wb2 = openpyxl.Workbook()
    c_phone = write_safe_xlsx_cell(wb2.active, 1, 1, "+86 21 50001234")
    assert c_phone.value == "+86 21 50001234" and c_phone.quotePrefix is True
    assert write_safe_xlsx_cell(wb2.active, 1, 2, "+86-21-50001234").value == "'+86-21-50001234"
    assert sanitize_csv_cell("+86-21-50001234") == "'+86-21-50001234"
    assert sanitize_csv_cell("—") == "—"


def test_global_119_iso_lexicon_and_ja_th_gates():
    # 1. Previously missing DB & expansion countries resolve in WORLD, COUNTRY_EN, and all 15 lexicons
    for cc in ("BD", "DZ", "KG", "PE", "UY", "MT", "LI", "CR", "PA", "EC", "UZ"):
        assert country_of(cc) == cc
        assert is_overseas_country(cc) is True
        assert cc in WORLD and WORLD[cc]
        assert cc in COUNTRY_EN and len(COUNTRY_EN[cc]) >= 4
        for lg in ("fr", "de", "es", "ru", "ar", "ja", "ko", "th", "vi"):
            cmap = load_lang_lexicon(lg)["countries"]
            assert cc in cmap and cmap[cc]

    # 2. Pure Chinese company name does not collapse to bare "Ltd."
    comp_en = strip_and_translate_cjk("华晨星途智行汽车制造有限公司")
    assert comp_en.lower() not in {"ltd.", "co., ltd."} and len(comp_en) >= 8

    # 3. Japanese gate preserves statutory Kanji compounds, fixes word subs, and catches Chinese prose
    ja_fixed = clean_and_gate_target_text("自動車の销售および契約の签署に関する規定", "ja")
    assert "販売" in ja_fixed and "署名" in ja_fixed
    assert "消售" not in ja_fixed and "署署" not in ja_fixed
    ja_law = clean_and_gate_target_text("中華人民共和国個人情報保護法", "ja")
    assert "個人情報保護法" in ja_law
    assert has_untranslated_chinese_leak("他的公司不在那里", "ja")
    assert has_untranslated_chinese_leak("ア：本公司的个人数据管理制度限定不得向外国提供", "ja")

    # 4. Thai ZWSP splitter never breaks upper vowel + consonant clusters
    th_wrapped = _inject_thai_zwsp("เครื่องมือคุ้มครองข้อมูลส่วนบุคคล")
    assert "มื\u200bอ" not in th_wrapped

    # 5. build_docx handles no_head empty-head grid tables without ZeroDivisionError/ValueError
    docx_bytes = build_docx(
        Rendered(blocks=[{"type": "table", "no_head": True, "head": [], "rows": [["Field A", "Value B"]]}]),
        "Grid Table Check",
        foreign=True,
        rtl=False,
    )
    assert len(docx_bytes) > 500
    with zipfile.ZipFile(io.BytesIO(docx_bytes)) as zf:
        xml = zf.read("word/document.xml").decode("utf-8")
        assert "<w:bidi/>" not in xml


async def test_rbac_rls_immutable_edits_and_whitepaper_guards(client, seed, session_factory):
    async with session_factory() as s:
        assert await active_overseas_countries(s) == []
        await service.create_user(
            s, email="rt-ops@example.com", display_name="运维员", password=PASSWORD, role=Role.OPS_ADMIN, org_id=None
        )
        await service.create_user(
            s,
            email="rt-view@example.com",
            display_name="查看者",
            password=PASSWORD,
            role=Role.VIEWER,
            org_id=seed["org_a"].id,
            can_export=True,
        )
        site = SourceSite(
            country_code="SG",
            publisher="PDPC Singapore",
            entry_url="https://www.pdpc.gov.sg/redteam-sg",
            language="en",
            document_type="LAW",
            terms_status="approved",
            enabled=True,
        )
        s.add(site)
        await s.flush()
        doc = Document(
            site_id=site.id,
            country_code="SG",
            title="Personal Data Protection Act 2012",
            source_url="https://www.pdpc.gov.sg/pdpa-2012",
            document_type="LAW",
            language="en",
            relevance="relevant",
        )
        s.add(doc)
        await s.flush()
        ver = DocumentVersion(
            document_id=doc.id,
            version_no=1,
            content_hash="a" * 64,
            archive_path="SG/pdpa.pdf",
            size_bytes=1024,
            mime_type="application/pdf",
            final_url="https://www.pdpc.gov.sg/pdpa-2012",
            state="parsed",
            legal_status="in_force",
            review_status="approved",
        )
        s.add(ver)
        await s.flush()
        cl = Clause(
            version_id=ver.id,
            ordinal=1,
            clause_number="26",
            original_text="An organisation shall not transfer any personal data to a country outside Singapore except in accordance with requirements prescribed under this Act.",
            text_hash="c" * 64,
            language="en",
            needs_review=False,
        )
        s.add(cl)
        await s.flush()
        s.add(
            ClauseTranslation(
                clause_id=cl.id,
                translated_text="任何组织不得将个人数据转移至新加坡境外，但按照本法规定的要求进行的除外。",
                engine="llm",
                model_name="seed",
                prompt_version="v1",
                quality_check_passed=True,
                review_status="approved",
                is_current=True,
            )
        )
        s.add(
            AdmissionEntry(
                country_code="SG",
                category="自动驾驶路测",
                name_zh="新加坡自动驾驶道路测试许可",
                authority_zh="陆路交通管理局（LTA）",
                summary_zh="公共道路测试须事先取得批准。",
                evidence_clause_id=cl.id,
                evidence_quote="An organisation shall not transfer any personal data",
                review_status="approved",
                functions=["autonomous_driving"],
                profile={"applies_to": "av_testing"},
            )
        )
        s.add_all(
            [
                CountryWhitepaper(
                    country_code="SG",
                    version_no=1,
                    source_hash="old_hash_v1",
                    blocks=[
                        {"type": "title", "text": "新加坡汽车数据合规简报"},
                        {"type": "heading", "text": "一、属地数据法规体系概述"},
                        {"type": "paragraph", "text": "第一版历史正文。"},
                    ],
                    report={"review_status": "auto_verified", "note": "初始版本"},
                ),
                CountryWhitepaper(
                    country_code="SG",
                    version_no=2,
                    source_hash="old_hash_v2",
                    blocks=[
                        {"type": "title", "text": "新加坡汽车数据合规简报"},
                        {"type": "heading", "text": "一、属地数据法规体系概述"},
                        {"type": "paragraph", "text": "第二版待更新正文。"},
                    ],
                    report={"review_status": "approved", "note": "已审定"},
                ),
            ]
        )
        await s.commit()
        assert [c["code"] for c in await active_overseas_countries(s)] == ["SG"]

    h_super = bearer(await login(client, "admin", "admin@example.com"))
    h_ops = bearer(await login(client, "admin", "rt-ops@example.com"))
    h_mem = bearer(await login(client, "user", "a-member@example.com"))
    h_view = bearer(await login(client, "user", "rt-view@example.com"))

    # 1. ops_admin cannot read Organization redaction audit history
    r_redact = await client.patch(
        f"/api/admin/orgs/{seed['org_a'].id}/redaction", headers=h_super, json={"terms": ["绝密代号Kunlun"]}
    )
    assert r_redact.status_code == 200
    r_ops_hist = await client.get(f"/api/admin/knowledge/history/{seed['org_a'].id}", headers=h_ops)
    assert r_ops_hist.status_code == 403
    r_ops_rep = await client.get("/api/admin/updates/report", headers=h_ops)
    assert r_ops_rep.status_code == 200
    assert r_ops_rep.json()["response_72h"]["total_impacts"] == 0
    assert r_ops_rep.json()["linkage"]["enterprise_notices_pushed"] == 0

    # 1b. ops_admin & super_admin posture endpoints + viewer/member public posture summary
    assert (await client.get("/api/admin/cockpit/overview", headers=h_ops)).status_code == 403
    r_sup_cockpit = await client.get("/api/admin/cockpit/overview", headers=h_super)
    assert r_sup_cockpit.status_code == 200
    r_ops_posture = await client.get("/api/admin/knowledge/posture", headers=h_ops)
    assert r_ops_posture.status_code == 200
    ops_body = r_ops_posture.json()
    assert ops_body["headline"]["covered_jurisdictions"] >= 1
    assert ops_body["headline"]["active_organizations"] == 0
    assert ops_body["operations"]["audit_events_7d"] == 0
    assert ops_body["operations"]["pending_updates"] == 0
    for h_u in (h_mem, h_view):
        r_upos = await client.get("/api/knowledge/posture-summary", headers=h_u)
        assert r_upos.status_code == 200
        assert r_upos.json()["covered_jurisdictions"] >= 1

    # 2. Whitepaper bad table geometry rejected with 422, and rollback preserves outdated source_hash
    for bad_tbl in (
        {"type": "table", "head": [], "rows": []},
        {"type": "table", "head": ["A", "B"], "widths": [0, 0], "rows": [["1", "2"]]},
        {"type": "table", "head": ["A", "B"], "widths": [10, -10], "rows": [["1", "2"]]},
        {"type": "table", "head": ["A", "B"], "rows": [["only_one"]]},
    ):
        resp_bad = await client.patch(
            "/api/admin/knowledge/whitepapers/SG/blocks",
            headers=h_ops,
            json={"blocks": [{"type": "title", "text": "新加坡简报"}, bad_tbl]},
        )
        assert resp_bad.status_code == 422

    r_rb = await client.post("/api/admin/knowledge/whitepapers/SG/versions/1/rollback", headers=h_ops, json={})
    assert r_rb.status_code == 200
    r_wps = await client.get("/api/admin/knowledge/whitepapers", headers=h_ops)
    sg_wp = next(x for x in r_wps.json() if x["country_code"] == "SG")
    assert sg_wp["outdated"] is True

    # 3. Viewer 403-before-422 on shared chat write endpoints + viewer private isolation
    r_shared = await client.post("/api/analysis/chats?advice=true", headers=h_mem, json={})
    assert r_shared.status_code in (200, 201)
    shared_id = r_shared.json()["id"]

    assert (await client.post(f"/api/analysis/tasks/{shared_id}/messages", headers=h_view, json={})).status_code == 403
    assert (
        await client.patch(f"/api/analysis/tasks/{shared_id}", headers=h_view, json={"title": "x" * 120})
    ).status_code == 403
    assert (await client.post("/api/analysis/chats", headers=h_view, json={"country_code": 12345})).status_code == 403

    r_priv = await client.post("/api/analysis/chats?advice=true", headers=h_view, json={})
    assert r_priv.status_code in (200, 201)
    priv_id = r_priv.json()["id"]
    assert (
        await client.post(f"/api/analysis/tasks/{priv_id}/facts", headers=h_mem, json={"facts": {"a": "b"}})
    ).status_code == 404
    assert (await client.get(f"/api/analysis/tasks/{priv_id}/documents/dpia", headers=h_mem)).status_code == 404
    assert (await client.get(f"/api/admission/by-task/{priv_id}", headers=h_mem)).status_code == 404

    # 4. Advice chat clone copies TaskAttachment & AttachmentReview
    async with session_factory() as s:
        att = TaskAttachment(
            org_id=seed["org_a"].id,
            task_id=uuid.UUID(shared_id),
            filename="policy.txt",
            mime="text/plain",
            size_bytes=20,
            sha256="b" * 64,
            text="跨境数据安全管理制度",
            readable=True,
            created_by=seed["a_member"].id,
            review_state="done",
        )
        s.add(att)
        await s.flush()
        s.add(
            AttachmentReview(
                org_id=seed["org_a"].id,
                attachment_id=att.id,
                country_code="SG",
                blocks=[{"type": "paragraph", "text": "已核对"}],
                summary={"missing": 1},
                tokens=10,
            )
        )
        await s.commit()

    r_clone = await client.post(f"/api/analysis/tasks/{shared_id}/clone", headers=h_mem, json={})
    assert r_clone.status_code == 200
    cloned_id = r_clone.json()["id"]
    r_atts = await client.get(f"/api/analysis/tasks/{cloned_id}/attachments", headers=h_mem)
    assert r_atts.status_code == 200 and len(r_atts.json()) == 1

    # 5. Custom 36-char scenario + immutable document_edits across DELETE /saved
    custom_scn = "车载毫米波雷达诊断参数与电池热管理特征跨境同步至区域研发中心分析"
    r_proj = await client.post(
        "/api/analysis/doc-projects",
        headers=h_mem,
        json={
            "source_country": "中国",
            "recipient_country": "新加坡",
            "functions": [custom_scn],
            "vehicle_model": "QH-S9",
            "company_name": "华晨星途智行汽车制造有限公司",
        },
    )
    assert r_proj.status_code in (200, 201)
    pid = r_proj.json()["id"]
    assert r_proj.json()["route"]["function_zh"] == custom_scn

    r_plan = await client.get(f"/api/admission/plan?task_id={pid}", headers=h_mem)
    assert r_plan.status_code == 200
    needed_names = [x["name_zh"] for x in r_plan.json().get("needed", [])]
    assert "新加坡自动驾驶道路测试许可" not in needed_names

    assert (await client.post(f"/api/analysis/tasks/{pid}/documents/tia/saved?draft=true", headers=h_mem)).status_code == 200
    r_saved = (await client.get(f"/api/analysis/tasks/{pid}/documents/tia/saved", headers=h_mem)).json()
    p_idx = next(i for i, b in enumerate(r_saved["blocks"]) if b.get("type") == "paragraph")
    assert (
        await client.patch(
            f"/api/analysis/tasks/{pid}/documents/tia/saved",
            headers=h_mem,
            json={"block": p_idx, "text": "经法务审定的新加坡跨境传输影响评估专章结论。"},
        )
    ).status_code == 200

    async with session_factory() as s:
        edits_before = await s.scalar(
            select(func.count()).select_from(DocumentEdit).where(DocumentEdit.task_id == uuid.UUID(pid))
        )
    assert edits_before >= 1
    r_del = await client.delete(f"/api/analysis/tasks/{pid}/documents/tia/saved", headers=h_mem)
    assert r_del.status_code == 200 and r_del.json() == {"exists": False}
    async with session_factory() as s:
        edits_after = await s.scalar(
            select(func.count()).select_from(DocumentEdit).where(DocumentEdit.task_id == uuid.UUID(pid))
        )
    assert edits_after >= edits_before + 1


async def test_foreign_zip_fault_injection_and_zero_chinese_xlsx(client, seed, monkeypatch):
    h_mem = bearer(await login(client, "user", "a-member@example.com"))

    r_proj = await client.post(
        "/api/analysis/doc-projects",
        headers=h_mem,
        json={
            "source_country": "中国",
            "recipient_country": "吉尔吉斯斯坦",
            "functions": ["ota_diagnostics"],
            "vehicle_model": "QH-K1",
            "company_name": "华晨星途智行汽车制造有限公司",
        },
    )
    assert r_proj.status_code in (200, 201)
    pid = r_proj.json()["id"]

    # Force live LLM branch and inject ReadTimeout to verify 0 MissingGreenlet 500
    monkeypatch.setenv("CYSH_LLM_API_KEY", "sk-fault-injection-probe")
    monkeypatch.setenv("CYSH_MULTILINGUAL_REPORT_LLM", "1")
    monkeypatch.setenv("CYSH_ENGLISH_REPORT_LLM", "1")

    async def _boom_llm(*_args, **_kwargs):
        raise httpx.ReadTimeout("simulated upstream timeout")

    monkeypatch.setattr("app.analysis.multilingual_report.call_llm_json", _boom_llm)
    monkeypatch.setattr("app.analysis.english_report.call_llm_json", _boom_llm)

    r_zip_ru = await client.get(
        f"/api/analysis/doc-projects/{pid}/export.zip?fmt=docx&draft=true&lang=ru", headers=h_mem
    )
    assert r_zip_ru.status_code == 200

    r_zip_xlsx = await client.get(
        f"/api/analysis/doc-projects/{pid}/export.zip?fmt=xlsx&draft=true&lang=fr", headers=h_mem
    )
    assert r_zip_xlsx.status_code == 200
    cjk_re = re.compile(r"[\u4e00-\u9fff]")
    with zipfile.ZipFile(io.BytesIO(r_zip_xlsx.content)) as zf:
        names = zf.namelist()
        assert any(n.endswith(".xlsx") for n in names)
        for name in names:
            assert not cjk_re.search(name), f"Chinese character leaked in ZIP entry name: {name}"
            if name.endswith(".xlsx"):
                wb = openpyxl.load_workbook(io.BytesIO(zf.read(name)))
                for ws in wb.worksheets:
                    for row in ws.iter_rows(values_only=True):
                        for val in row:
                            if val is not None:
                                assert not cjk_re.search(str(val)), f"Chinese leaked in {name}: {val}"
            elif name.endswith(".txt"):
                txt = zf.read(name).decode("utf-8")
                assert not cjk_re.search(txt), f"Chinese leaked in {name}: {txt}"


def test_wave2_multilingual_spreadsheet_redactor_and_multiscript_gates():
    from app.analysis.doc_rules import real_label
    from app.analysis.document_instances import build_xlsx
    from app.analysis.multilingual_report import _isolate_arabic_ltr
    from app.analysis.tools import is_usable_clause_text
    from app.ingestion.noise import clean_text

    # 1. Japanese narrow sub-clause gate catches short Chinese leaks & non-CP932 fragments, preserves valid Japanese
    assert has_untranslated_chinese_leak("ア：正式申告交前人工核清単", "ja")
    assert has_untranslated_chinese_leak("他的数据由公司保管", "ja")
    ja_ok = clean_and_gate_target_text("個人情報保護法に基づき自動車データの販売および契約署名を行う。", "ja")
    assert "個人情報保護法" in ja_ok and not has_untranslated_chinese_leak(ja_ok, "ja")

    # 2. Bare punctuation never short-circuits fallback prose
    assert clean_and_gate_target_text(".", "fr", fallback="Rapport de conformité") == "Rapport de conformité"
    assert strip_and_translate_cjk(".", fallback="Compliance Statement") == "Compliance Statement"

    # 3. Arabic multi-word Latin & space-separated international phone stay in single LRI..PDI run
    ar_iso = _isolate_arabic_ltr("وفقاً لقانون Personal Data Protection Act وهاتف +216 71 800 123.")
    assert "\u2066Personal Data Protection Act\u2069" in ar_iso
    assert "\u2066+216 71 800 123\u2069" in ar_iso

    # 4. build_xlsx foreign Sheet1 fallback, RTL sheetView, empty-head max_cols, and spans merges
    xlsx_bytes = build_xlsx(
        [
            {
                "type": "table",
                "no_head": True,
                "head": [],
                "rows": [["خلية مدمجة", "", "قيمة"], ["أ", "ب", "ج"]],
                "spans": [{"r": 0, "c": 0, "rs": 1, "cs": 2}],
            }
        ],
        "中文工作表标题",
        foreign=True,
        rtl=True,
    )
    wb = openpyxl.load_workbook(io.BytesIO(xlsx_bytes))
    ws = wb.active
    assert not re.search(r"[\u4e00-\u9fff]", ws.title)
    assert ws.views.sheetView[0].rightToLeft is True
    assert ws.max_column >= 3
    assert any(str(rng) == "A1:B1" for rng in ws.merged_cells.ranges)

    # 5. CWE-1236 Unicode BOM / ZWSP / LRM prefix neutralization + Redactor dict keys + ms jargon unit
    for tricky in ("\ufeff=1+1", " \u200b=cmd|' /C calc'!A0", "\u200e+1+1", "\u202a-1+1"):
        assert sanitize_csv_cell(tricky).startswith("'")
        wb_t = openpyxl.Workbook()
        c_t = write_safe_xlsx_cell(wb_t.active, 1, 1, tricky)
        assert str(c_t.value).startswith("'") and c_t.quotePrefix is True

    red = Redactor(["星途绝密代号X9"])
    masked_tree = red.tree({"星途绝密代号X9": "参数属于星途绝密代号X9"})
    assert "星途绝密代号X9" not in str(masked_tree)
    restored_tree = red.restore_tree(masked_tree) if hasattr(red, "restore_tree") else {
        red.restore(k): red.restore(v) for k, v in masked_tree.items()
    }
    assert restored_tree == {"星途绝密代号X9": "参数属于星途绝密代号X9"}
    assert "125 毫秒" in scrub_engineering_jargon("接口耗时 125ms 完成")

    # 6. Multi-script clause usability, Cleaner-Verifier Symmetry, & Common-Law/Korean/Thai/Bengali labels
    for sample in (
        "Настоящий Федеральный закон регулирует отношения, связанные с обработкой персональных данных операторами.",
        "개인정보처리자는 정보주체의 동의를 받은 경우 개인정보를 제3자에게 제공할 수 있으며 개인정보의 분실·도난·유출을 방지하기 위하여 안전성 확보에 필요한 기술적·관리적 조치를 하여야 한다.",
        "มาตรา ๒๑ ผู้ควบคุมข้อมูลส่วนบุคคลต้องทำการเก็บรวบรวม ใช้ หรือเปิดเผยข้อมูลส่วนบุคคลตามวัตถุประสงค์ที่ได้แจ้งไว้เท่านั้น",
        "৬৷ আপাততঃ বলবৎ অন্য কোন আইনে যাহা কিছুই থাকুক না কেন, ইলেক্ট্রনিক রেকর্ড আইনানুগভাবে বৈধ ও কার্যকর বলিয়া গণ্য হইবে।",
    ):
        assert is_usable_clause_text(sample) is True
    assert "HOOFDSTUK IX" not in clean_text("De verwerkingsverantwoordelijke neemt passende maatregelen.\nHOOFDSTUK IX. - Toezichthoudende autoriteit")
    assert "Abschnitt 2" not in clean_text("Die Stelle trifft geeignete Sicherheitsmaßnahmen.\nAbschnitt 2 Pflichten der Verantwortlichen\nNichtamtliches Inhaltsverzeichnis")
    assert real_label("26", "SECTION 26. Organizational Security Measures. Every controller shall...", "第26节 组织安全措施。", "Republic Act 10173") == "第26条"
    assert real_label("58의2", "제58조의2(적용제외) 가명정보에 대하여는...", "第58条之2", "개인정보 보호법") == "第58条之2"
    assert real_label("第2页", "มาตรา ๒๑ ผู้ควบคุมข้อมูลส่วนบุคคลต้อง...", "个人数据控制者必须...", "PDPA") == "第21条"
    assert real_label(None, "ইলেক্ট্রনিক রেকর্ড\n৬৷ আপাততঃ বলবৎ অন্য কোন আইনে...", "第6条 尽管其他现行法律...", "ICT Act") == "第6条"
    assert (
        real_label(
            "第19页",
            "(1) Any Processing of Personal Data which involves the transfer...",
            "（1）任何涉及传输的个人数据处理...",
            "QFC Data Protection Regulations 2021",
            section_path="PART 4 TRANSFERS OF DATA OUTSIDE THE QFC ARTICLE 23 – TRANSFERS",
        )
        == "第23条"
    )
    from app.admission.router import display_authority
    from app.knowledge.legal_terms import TranslationTerm
    from app.knowledge.numeric_integrity import dates
    from app.knowledge.translator import match_clause_glossary

    assert dates("decreto legislativo 30 giugno 2003, n. 196") == {(2003, 6, 30)}
    assert dates("Real Decreto 15 de julio de 2018") == {(2018, 7, 15)}
    t_sens = TranslationTerm(
        uuid.uuid4(), "sensitive_data", None, "en", "sensitive data", "敏感数据", 4, None, None,
        aliases=["sensitive personal data", "sensitive data"],
    )
    t_pd = TranslationTerm(
        uuid.uuid4(), "personal_data", None, "en", "personal data", "个人数据", 4, None, None,
        aliases=["personal data"],
    )
    hits_sp, _ = match_clause_glossary("Disclosure of sensitive personal data requires consent.", [t_sens, t_pd])
    assert [h["concept_code"] for h in hits_sp] == ["sensitive_data"]
    assert "负责部（" not in display_authority("信息专员（出具证明书）；负责部长（批准）", "AU")
    assert display_authority("Competent Authority", "SA") == "沙特数据与人工智能管理局（SDAIA）"


async def test_wave2_ops_self_update_opinion_isolation_and_archive_clone_guards(client, seed, session_factory):
    from app.analysis.models import AnalysisMessage

    async with session_factory() as s:
        await service.create_user(
            s, email="w2-ops@example.com", display_name="运维专员甲", password=PASSWORD, role=Role.OPS_ADMIN, org_id=None
        )
        await s.commit()

    h_super = bearer(await login(client, "admin", "admin@example.com"))
    h_ops = bearer(await login(client, "admin", "w2-ops@example.com"))
    h_mem = bearer(await login(client, "user", "a-member@example.com"))

    # 1. ops_admin can update own display_name via /api/auth/account under scope='ops' RLS
    r_me = await client.patch("/api/auth/account", headers=h_ops, json={"display_name": "运维专员已改名"})
    assert r_me.status_code == 200 and r_me.json()["display_name"] == "运维专员已改名"

    # 2. Opinion digest SMTP & recipient list strictly isolated from ops_admin
    assert (
        await client.put(
            "/api/admin/opinion/digest-config",
            headers=h_super,
            json={"enabled": True, "recipients": ["vip-client@example.com"], "smtp_host": "smtp.internal.example"},
        )
    ).status_code == 200
    r_ops_cfg = await client.get("/api/admin/opinion/digest-config", headers=h_ops)
    assert r_ops_cfg.status_code == 200
    ops_cfg_str = str(r_ops_cfg.json())
    assert "vip-client@example.com" not in ops_cfg_str and "smtp.internal.example" not in ops_cfg_str
    assert (await client.post("/api/admin/opinion/digest-test", headers=h_ops, json={})).status_code == 403
    assert (
        await client.post(
            "/api/admin/opinion/digest-send", headers=h_ops, json={"recipients": ["leak@example.com"]}
        )
    ).status_code == 403

    # 3. Advice chat clone remaps message_id & summary["attachment_ids"], and archive blocks rename with 409
    r_chat = await client.post("/api/analysis/chats?advice=true", headers=h_mem, json={})
    assert r_chat.status_code in (200, 201)
    cid = r_chat.json()["id"]
    async with session_factory() as s:
        msg = AnalysisMessage(task_id=uuid.UUID(cid), seq_no=99, role="user", content="请核查本制度")
        s.add(msg)
        await s.flush()
        att = TaskAttachment(
            org_id=seed["org_a"].id,
            task_id=uuid.UUID(cid),
            message_id=msg.id,
            filename="manual.txt",
            mime="text/plain",
            size_bytes=24,
            sha256="d" * 64,
            text="数据出境审批制度正文",
            readable=True,
            created_by=seed["a_member"].id,
            review_state="done",
        )
        s.add(att)
        await s.flush()
        s.add(
            AttachmentReview(
                org_id=seed["org_a"].id,
                attachment_id=att.id,
                country_code="SG",
                blocks=[{"type": "paragraph", "text": "核查完成"}],
                summary={"missing": 0, "attachment_ids": [str(att.id)]},
                tokens=8,
            )
        )
        await s.commit()
        orig_att_id = str(att.id)
        orig_msg_id = msg.id

    r_cl = await client.post(f"/api/analysis/tasks/{cid}/clone", headers=h_mem, json={})
    assert r_cl.status_code == 200
    new_cid = uuid.UUID(r_cl.json()["id"])
    async with session_factory() as s:
        new_att = await s.scalar(select(TaskAttachment).where(TaskAttachment.task_id == new_cid))
        assert new_att is not None and new_att.message_id is not None and new_att.message_id != orig_msg_id
        new_rev = await s.scalar(select(AttachmentReview).where(AttachmentReview.attachment_id == new_att.id))
        assert new_rev is not None and new_rev.summary.get("attachment_ids") == [str(new_att.id)]
        assert orig_att_id not in new_rev.summary.get("attachment_ids", [])

    assert (await client.delete(f"/api/analysis/tasks/{cid}", headers=h_mem)).status_code == 200
    r_ren = await client.patch(f"/api/analysis/tasks/{cid}", headers=h_mem, json={"title": "试图改名已归档会话"})
    assert r_ren.status_code == 409


async def test_wave3_cleaner_numeric_consulting_and_admission_correcting_closure(
    client, seed, session_factory, monkeypatch
):
    from types import SimpleNamespace

    from app.analysis.clause_gate import cut
    from app.analysis.doc_rules import cites_in, real_label
    from app.analysis.document_templates import _render_pdf
    from app.analysis.english_report import _is_clean_latin_quote
    from app.analysis.llm import LlmResult
    from app.analysis.multilingual_report import (
        _truncate_at_sentence_or_word,
        build_multilingual_consulting_report_blocks_async,
    )
    from app.ingestion.noise import clean_text, clean_translation
    from app.knowledge.legal_terms import TranslationTerm
    from app.knowledge.models import TranslationChunkCache
    from app.knowledge.numeric_integrity import amounts, dates, missing_amounts, tokens
    from app.knowledge.translator import (
        PROMPT_VERSION,
        chunk_key,
        match_clause_glossary,
        select_effective_terms,
        translate_clause,
        verify_translation,
    )
    from app.knowledge.whitepaper import _extract_prev_sections_and_refs, _tidy_brackets

    # 1. [P0-1] Cleaner-Verifier symmetry: AV EDR clause, CNDP inline URL, Chinese link intro, amendment tags
    zh_edr = "第9条 车辆安全要求\n(a) 车载事件数据记录器，能够在碰撞前 30 秒内捕获传感器数据；\n(b) 人工接管机制。"
    assert "(a) 车载事件数据记录器" in clean_translation(zh_edr) and "30" in clean_translation(zh_edr)
    fr_cndp = "Les formulaires de déclaration préalable sont téléchargeables sur le site officiel www.cndp.ma conformément aux modalités fixées par la loi."
    assert "www.cndp.ma" in clean_text(fr_cndp)
    assert "https://www.lta.gov.sg/av-2017" in clean_translation("申请人可亲自办理或\nhttps://www.lta.gov.sg/av-2017")
    assert "notify-24h@pdpc.gov.sg" in clean_translation("可通过邮寄或\nnotify-24h@pdpc.gov.sg")
    assert "2016" not in clean_text("[Act 22 of 2016, s. 5] An organisation shall protect personal data under its control.")
    assert "2016" not in clean_translation("[2016年第22号法，第5条] 任何组织应当保护其控制的个人数据。")

    # 2. [P0-2] Multi-locale thousands separators, comma-decimal percentages, Gazette dates, & Polish zł boundary
    assert amounts("20 000 000 EUR") == {("EUR", "20000000")}
    assert missing_amounts("20 000 000 EUR", "最高处以 20,000,000 欧元的行政罚款。") == []
    assert amounts("50.000 Euro") == {("EUR", "50000")}
    assert amounts("R$ 50.000.000") == {("BRL", "50000000")}
    assert missing_amounts("50.000 Euro", "处 50 欧元罚款") == ["EUR 50000"]
    for pct_src, expected in (
        ("2,5 pour cent", ("2.5", True)),
        ("yüzde 2,5", ("2.5", True)),
        ("ร้อยละ 4", ("4", True)),
        ("4 بالمائة", ("4", True)),
        ("4 パーセント", ("4", True)),
        ("4 퍼센트", ("4", True)),
    ):
        assert expected in tokens(pct_src), f"Failed percentage token for {pct_src}: {tokens(pct_src)}"
    assert dates("10 تشرين الأول 2018") == {(2018, 10, 10)}
    assert dates("15 يوليو سنة 2020") == {(2020, 7, 15)}
    assert dates("15 EKİM 2020") == {(2020, 10, 15)}
    assert dates("2020년 8월 5일") == {(2020, 8, 5)}
    assert dates("ngày 17 tháng 4 năm 2023") == {(2023, 4, 17)}
    assert amounts("Pojazd posiada 2 złącza diagnostyczne.") == set()
    assert amounts("50 000 zł") == {("PLN", "50000")}

    # 3. [P1-1] Glossary actual span length & hardware controller/processor compound guard + region subtag
    t_dc = TranslationTerm(
        uuid.uuid4(), "data_controller", None, "en", "data controller", "数据控制者", 2, None, None,
        aliases=["data controller", "controller"],
    )
    t_bc = TranslationTerm(
        uuid.uuid4(), "bus_controller", "SG", "en", "bus controller", "总线控制器", 1, None, None,
        aliases=["bus controller"],
    )
    assert len(select_effective_terms([t_dc, t_bc], "SG", "en-SG")) == 2
    hits_bc, _ = match_clause_glossary("The bus controller shall log personal data access events.", [t_dc, t_bc])
    assert [h["concept_code"] for h in hits_bc] == ["bus_controller"]
    ok_both, iss_both, _ = verify_translation(
        "The data controller shall ensure the bus controller and车载 processor log events.",
        "数据控制者应当确保总线控制器与车载处理器记录事件。",
        [{"concept_code": "data_controller", "source_term": "data controller", "target_zh": "数据控制者"}],
    )
    assert ok_both, iss_both

    # 4. [P1-2, P1-4, P2-1, P2-2] Sentence boundary truncation, non-Latin acronym guard, cites_in/real_label, whitepaper refs
    fr_statute = (
        "La demande d'autorisation comporte notamment les mentions suivantes :\n"
        "1) le nom, le prénom et le domicile du responsable du traitement ;\n"
        "2) la finalité du traitement et ses caractéristiques techniques ;\n"
        "3) les catégories de traitement, le lieu et la date de sa mise en oeuvre ;\n"
        "5. les données à caractère personnel dont le traitement est envisagé ainsi que leur origine et la durée de conservation prévue par le responsable du traitement conformément à la loi."
    )
    tr_fr = _truncate_at_sentence_or_word(fr_statute, 420, lang="fr")
    assert not tr_fr.endswith("5.") and not tr_fr.endswith("5....")
    assert _is_clean_latin_quote("البيانات الشخصية 5G PDPA") is False
    assert _is_clean_latin_quote("An organisation shall protect personal data.") is True
    assert cites_in("依《2012年个人数据保护法》第26A条、《个人数据保护法》第12条之二、第58条之2及《条例》第10.1段") == [
        ("2012年个人数据保护法", "第26A条"),
        ("个人数据保护法", "第12条之二"),
        ("个人数据保护法", "第58条之2"),
        ("条例", "第10.1段"),
    ]
    assert real_label(None, "5 working days after making the assessment\nThe organisation must notify.", "评估后5个工作日内\n该组织必须通知。", "PDPA") is None
    assert "并且" not in _tidy_brackets("……提供至少可与本法所规定的保护相比拟的保护标准；并且……")
    assert "——" not in _tidy_brackets("……已依照本法提供其同意。（2）组织（organisation）不得——……")
    assert "EG-CERT" not in _tidy_brackets("……可能对个人造成有限损害的数据。私密及机密 EG-CERT、NTRA、埃及……")
    long_def = "第六条 本法所称“个人数据处理”，是指无论采用何种工艺或程序，由自然人或法人以自动化或非自动化方式实施的，与个人数据的收集、记录、保存、改编、修改、提取、查阅、使用、传递、传播、销毁、擦除、利用或互连（interconnexion）有关的一切操作。"
    assert cut(long_def, 220).endswith("有关的一切操作。")
    _secs, prev_refs = _extract_prev_sections_and_refs(
        [
            {"type": "heading", "text": "参考条文"},
            {
                "type": "table",
                "head": ["编号", "法规与条款", "条文摘录"],
                "rows": [
                    ["[1]", "欧盟通用数据保护条例（GDPR）（Regulation (EU) 2016/679） 第45条", "摘录一。"],
                    ["[2]", "欧盟通用数据保护条例（GDPR） 第48条", "摘录二。"],
                ],
            },
        ],
        "FR",
    )
    assert list(prev_refs.keys()) == [("欧盟通用数据保护条例（GDPR）", "第45条"), ("欧盟通用数据保护条例（GDPR）", "第48条")]

    # 5. [P1-3] Builtin PDF grid table with missing head & dict spans + consulting LlmResult unpacking + rejected chunk_cache purge + correcting foreign draft
    pdf_grid = _render_pdf(
        Rendered(
            blocks=[
                {
                    "type": "table",
                    "no_head": True,
                    "rows": [["Merged Title", "", "Val"], ["A", "B", "C"]],
                    "spans": [{"r": 0, "c": 0, "rs": 1, "cs": 2}],
                }
            ]
        ),
        "Grid Dict Spans",
    )
    assert pdf_grid.startswith(b"%PDF-")

    monkeypatch.setenv("CYSH_LLM_API_KEY", "sk-wave3-test")
    monkeypatch.setenv("CYSH_MULTILINGUAL_REPORT_LLM", "1")

    async def _fake_consulting_llm(messages, **_kwargs):
        user_payload = messages[-1]["content"]
        if '"slots"' in user_payload:
            return LlmResult(data={"slots": {"0": "Synthèse exécutive vérifiée."}}, prompt_tokens=12, completion_tokens=8, duration_ms=50, model="test")
        return LlmResult(data={"strings": ["Verified executive summary."]}, prompt_tokens=12, completion_tokens=8, duration_ms=50, model="test")

    monkeypatch.setattr("app.analysis.multilingual_report.call_llm_json", _fake_consulting_llm)
    async with session_factory() as s:
        from app.analysis.models import AnalysisTask

        t_cons = AnalysisTask(
            org_id=seed["org_a"].id,
            title="法国自动驾驶合规咨询",
            country_code="FR",
            scenario_type="autonomous_driving",
            vehicle_model="QH-FR1",
            status="completed",
            created_by=seed["a_member"].id,
        )
        s.add(t_cons)
        await s.commit()
        await s.refresh(t_cons)
        rv_mock = SimpleNamespace(
            version_no=1,
            status="completed",
            executive_summary="本报告核验自动驾驶数据跨境合规要求。",
            dimensions_json=[],
            consulting_json=None,
            citation_policy="strict",
            regulatory_updates=[],
        )
        cons_blocks = await build_multilingual_consulting_report_blocks_async(s, t_cons, rv_mock, None, lang="fr")
        assert any("Synthèse" in str(b) for b in cons_blocks)

        # Verify rejected translation purges TranslationChunkCache on re-translate
        site = SourceSite(
            country_code="FR", publisher="CNIL", entry_url="https://www.cnil.fr/w3", language="fr",
            document_type="LAW", terms_status="approved", enabled=True,
        )
        s.add(site)
        await s.flush()
        doc = Document(
            site_id=site.id, country_code="FR", title="Loi Informatique", source_url="https://www.cnil.fr/loi-w3",
            document_type="LAW", language="fr", relevance="relevant",
        )
        s.add(doc)
        await s.flush()
        ver = DocumentVersion(
            document_id=doc.id, version_no=1, content_hash="e" * 64, archive_path="FR/w3.pdf",
            size_bytes=512, mime_type="application/pdf", final_url="https://www.cnil.fr/loi-w3",
            state="parsed", legal_status="in_force", review_status="approved",
        )
        s.add(ver)
        await s.flush()
        src_fr = "Le responsable du traitement prend toutes précautions utiles pour préserver la sécurité des données."
        cl_fr = Clause(
            version_id=ver.id, ordinal=1, clause_number="34", original_text=src_fr,
            text_hash="f" * 64, language="fr", needs_review=False,
        )
        s.add(cl_fr)
        await s.flush()
        stale_zh = "旧的被人工驳回的错误译文内容，应当被彻底清除而不得从缓存复活。"
        s.add(
            ClauseTranslation(
                clause_id=cl_fr.id, translated_text=stale_zh, engine="llm", model_name="seed",
                prompt_version=PROMPT_VERSION, quality_check_passed=False, review_status="rejected", is_current=True,
            )
        )
        ckey = chunk_key(src_fr, "fr")
        s.add(TranslationChunkCache(chunk_hash=ckey, prompt_version=PROMPT_VERSION, language="fr", translated_text=stale_zh))
        adm_entry = AdmissionEntry(
            country_code="FR", category="data_filing", name_zh="法国自动驾驶数据授权申请",
            authority_zh="法国国家信息与自由委员会（CNIL）", summary_zh="须以法语提交申请。",
            evidence_clause_id=cl_fr.id, evidence_quote=src_fr, review_status="approved",
            functions=["autonomous_driving"], profile={"language": "法语", "applies_to": "av_testing"},
        )
        s.add(adm_entry)
        await s.commit()
        adm_entry_id = str(adm_entry.id)

        fresh_zh = "数据控制者应当采取一切有用的预防措施，以保障数据的安全性。"

        async def _fake_retranslate(*_a, **_kw):
            return (fresh_zh, "deepseek-chat", 20, 18, 40)

        monkeypatch.setattr("app.knowledge.translator._invoke_llm_translate", _fake_retranslate)
        async def _no_sem(*_a, **_kw):
            return []

        monkeypatch.setattr("app.knowledge.translator._semantic_issues", _no_sem)
        tr_row = await translate_clause(s, cl_fr, "FR", [])
        await s.commit()
        assert tr_row.translated_text == fresh_zh and tr_row.translated_text != stale_zh

    # Admission state machine: submitted locks answers/drafts/foreign (409), correcting unlocks answers/drafts/foreign!
    h_mem = bearer(await login(client, "user", "a-member@example.com"))
    r_ap = await client.post("/api/admission/projects", headers=h_mem, json={"entry_id": adm_entry_id, "title": "FR-AV"})
    assert r_ap.status_code == 201
    apid = r_ap.json()["id"]
    d1 = (await client.post(f"/api/admission/projects/{apid}/drafts", headers=h_mem)).json()
    assert (await client.put(f"/api/admission/projects/{apid}/filing", headers=h_mem, json={"state": "submitted"})).status_code == 200
    assert (await client.post(f"/api/admission/projects/{apid}/drafts", headers=h_mem)).status_code == 409
    assert (await client.post(f"/api/admission/drafts/{d1['id']}/foreign", headers=h_mem)).status_code == 409

    assert (
        await client.put(
            f"/api/admission/projects/{apid}/filing",
            headers=h_mem,
            json={"state": "correcting", "questions": ["请补充法文正式申请函签章页"]},
        )
    ).status_code == 200
    r_d2 = await client.post(f"/api/admission/projects/{apid}/drafts", headers=h_mem)
    assert r_d2.status_code == 201
    d2_id = r_d2.json()["id"]

    async def _fake_foreign_letter(_session, _entry, _draft, lang_info, **_kw):
        return {
            "lang_code": lang_info[0],
            "lang_zh": lang_info[1],
            "lang_en": lang_info[2],
            "title": "Demande d'autorisation CNIL",
            "name_zh": "法国自动驾驶数据授权申请书（法文正式稿）",
            "blocks": [
                {"type": "title", "text": "Demande d'autorisation CNIL"},
                {"type": "paragraph", "text": "Référence autorité arabe secondaire : الهيئة الوطنية لحماية المعطيات."},
            ],
            "verified": True,
            "notes": [],
            "generated_at": "2026-11-28T00:00:00+00:00",
        }

    monkeypatch.setattr("app.admission.foreign_letter.generate_foreign_letter", _fake_foreign_letter)
    r_fg = await client.post(f"/api/admission/drafts/{d2_id}/foreign", headers=h_mem)
    assert r_fg.status_code == 200
    r_fg_docx = await client.get(f"/api/admission/drafts/{d2_id}/foreign/export.docx", headers=h_mem)
    assert r_fg_docx.status_code == 200
    with zipfile.ZipFile(io.BytesIO(r_fg_docx.content)) as zf:
        doc_xml = zf.read("word/document.xml").decode("utf-8")
        # French formal letter containing an Arabic name must NOT flip whole document to RTL!
        assert "<w:bidi/>" not in doc_xml

    # Wave-4 closure regressions: noise, dot-thousands, clause cut, spans, portal sandbox, & filing locks
    import openpyxl

    from app.analysis import clause_gate
    from app.analysis.document_instances import build_xlsx
    from app.analysis.document_templates import build_docx
    from app.core.security import create_portal_ticket
    from app.ingestion.noise import clean_text, clean_translation
    from app.knowledge.numeric_integrity import dates as ni_dates
    from app.knowledge.numeric_integrity import normalize as ni_norm
    from app.knowledge.translator import verify_translation

    src_email = (
        "(1) Every data controller shall maintain safeguards for personal data.\n"
        "(2) Where a personal data breach occurs, assessment shall be prompt.\n"
        "(3) Written breach notifications shall be sent to notification@pdpc.gov.sg within 3 calendar days."
    )
    assert "notification@pdpc.gov.sg" in clean_text(src_email)
    zh_anchor = "第3条 本法的目的在于加强个人数据保护（Personal Data Protection），并平衡组织合法需求。"
    assert "个人数据保护（Personal Data Protection）" in clean_translation(zh_anchor)
    zh_margin = "委员会可以发布指南：\n5   具有一般行政性质的指南；\n10  适用于所有持牌人的指南。"
    assert "5   " not in clean_translation(zh_margin)

    ok_eur, iss_eur, _ = verify_translation(
        "Eine Geldbuße bis zu 5.000 EUR wird verhängt.", "处最高 5.000 欧元的罚款。", []
    )
    assert ok_eur, iss_eur
    assert "4.5%" in ni_norm("百分之4,5")
    assert ni_dates("15 MART 2020") == {(2020, 3, 15)}
    assert ni_dates("15 MAYIS 2020") == {(2020, 5, 15)}
    ok_ecu, iss_ecu, _ = verify_translation(
        "The data controller shall document gateway controller telemetry.",
        "数据处理者/控制者（data controller）应当记录网关控制器遥测数据。",
        [],
    )
    assert ok_ecu, iss_ecu

    cut_sample = (
        "本条规定适用于下列情形：1. 向境外传输数据的组织应当事先经过安全核验"
        "并且提交完整技术说明文档；5. 涉及关键基础设施的数据应当本地留存。"
    )
    cut_res = clause_gate.cut(cut_sample, 52)
    assert not cut_res.endswith("5. ……") and not cut_res.endswith("；……")

    xlsx_bytes = build_xlsx(
        [
            {
                "type": "table",
                "no_head": True,
                "rows": [["A", "", "B"], ["", "", "C"]],
                "spans": [[[2, 2], [0, 0], [1, 1]], [[0, 0], [0, 0], [1, 1]]],
            }
        ],
        "合并表测试",
    )
    wb_check = openpyxl.load_workbook(io.BytesIO(xlsx_bytes))
    assert len(wb_check.active.merged_cells.ranges) >= 1
    docx_sp = build_docx(
        Rendered(
            blocks=[
                {
                    "type": "table",
                    "head": ["大类", "细项", "要求"],
                    "rows": [["安全", "传输", "加密"], ["", "存储", "脱敏"]],
                    "spans": [[[2, 1], [1, 1], [1, 1]], [[0, 0], [1, 1], [1, 1]]],
                }
            ]
        ),
        "合并表测试",
    )
    with zipfile.ZipFile(io.BytesIO(docx_sp)) as zf_sp:
        sp_xml = zf_sp.read("word/document.xml").decode("utf-8")
        assert "<w:vMerge w:val='restart'/>" in sp_xml

    t_std = create_portal_ticket(
        email="std-visitor-1128@example.com",
        display_name="李访客",
        org_name=seed["org_a"].name,
        tier="standard",
    )
    r_ex = await client.post("/api/auth/portal/exchange", json={"ticket": t_std})
    assert r_ex.status_code == 200
    std_tok = bearer(r_ex.json()["access_token"])
    assert (await client.get("/api/analysis/doc-projects", headers=std_tok)).json() == []
    prof_std = (await client.get("/api/auth/account", headers=std_tok)).json()
    from app.iam.service import TRIAL_ORG_MARKER as _TRIAL_MARK

    assert _TRIAL_MARK
    assert prof_std["is_trial"] is True and prof_std["org_id"] != str(seed["org_a"].id)

    open_issues = (await client.get(f"/api/admission/projects/{apid}", headers=h_mem)).json()["issues"]
    auth_issue = next(x for x in open_issues if x["kind"] == "authority")
    assert (await client.delete(f"/api/admission/issues/{auth_issue['id']}", headers=h_mem)).status_code == 409
    r_blk_app = await client.put(
        f"/api/admission/projects/{apid}/approval", headers=h_mem, json={"valid_until": "2028-12-31"}
    )
    assert r_blk_app.status_code == 409

    # Wave-5 closure regressions:
    # (a) Project delete lock on correcting/submitted/approved & correcting->rejected->preparing open authority lock
    assert (await client.delete(f"/api/admission/projects/{apid}", headers=h_mem)).status_code == 409
    assert (
        await client.put(
            f"/api/admission/projects/{apid}/filing",
            headers=h_mem,
            json={"state": "rejected", "reason": "材料待补齐"},
        )
    ).status_code == 200
    assert (
        await client.put(f"/api/admission/projects/{apid}/filing", headers=h_mem, json={"state": "preparing"})
    ).status_code == 200
    # Even after rejected -> preparing, unclosed authority issue still blocks project delete, issue delete, resubmit, & approval!
    assert (await client.delete(f"/api/admission/projects/{apid}", headers=h_mem)).status_code == 409
    assert (await client.delete(f"/api/admission/issues/{auth_issue['id']}", headers=h_mem)).status_code == 409
    assert (
        await client.put(f"/api/admission/projects/{apid}/filing", headers=h_mem, json={"state": "submitted"})
    ).status_code == 422
    assert (
        await client.put(
            f"/api/admission/projects/{apid}/approval",
            headers=h_mem,
            json={"number": "CNIL-99", "issued_on": "2026-11-01", "expires_on": "2028-11-01"},
        )
    ).status_code == 409

    # (b) Uppercase Latin margin line numbers symmetry & French statutory breach email preservation
    orig_up_margin = (
        "The Commission may issue guidelines:\n"
        "5   Where an organisation processes personal data;\n"
        "10  Subject to section 24, the organisation shall comply."
    )
    zh_up_margin = (
        "委员会可以发布指南：\n"
        "5   当组织处理个人数据时；\n"
        "10  在符合第24条规定的前提下，该组织应当遵守。"
    )
    ok_mg, iss_mg, _ = verify_translation(
        clean_text(orig_up_margin), clean_translation(zh_up_margin), []
    )
    assert ok_mg, iss_mg
    fr_breach_email = (
        "Article 14. Le responsable du traitement met en oeuvre les mesures de sécurité appropriées "
        "pour protéger les données personnelles contre toute destruction accidentelle ou illicite.\n"
        "Toute violation est déclarée par courriel : alerte@cndp.ma dans les trois jours."
    )
    assert "alerte@cndp.ma" in clean_text(fr_breach_email)

    # (c) Symmetric dot-thousands for MAD / TRY (< 10.000) & multi-char ECU compounds
    ok_mad, iss_mad, _ = verify_translation(
        "Est puni d'une amende de 5.000 MAD.", "处 5.000 摩洛哥迪拉姆罚款。", []
    )
    assert ok_mad, iss_mad
    bad_mad, _, _ = verify_translation(
        "Est puni d'une amende de 5.000 MAD.", "处 5 摩洛哥迪拉姆罚款。", []
    )
    assert not bad_mad
    ok_try, iss_try, _ = verify_translation(
        "5.000 Türk Lirası idari para cezası verilir.", "处以 5.000 土耳其里拉行政罚款。", []
    )
    assert ok_try, iss_try
    ok_airbag, iss_airbag, _ = verify_translation(
        "Airbag controller logs shall be retained.", "安全气囊控制器日志应当予以保存。", []
    )
    assert ok_airbag, iss_airbag
    bad_primary_ctrl, _, _ = verify_translation(
        "The primary controller shall notify the authority.", "主控制器应当通知主管机关。", []
    )
    assert not bad_primary_ctrl

    # (d) Clause gate word boundaries & lookbehind
    assert clause_gate._strip_dangling_tail("The organisation or data processor") == "The organisation or data processor"
    assert clause_gate._strip_dangling_tail("Conformément au présent décret") == "Conformément au présent décret"
    assert clause_gate._strip_dangling_tail("根据本法第1款规定，包括一、") == "根据本法第1款规定，包括"
    assert clause_gate.find_clean_sentence_cut("第一款 数据传输涉及埃及。第二款 其他规定........................", 24) > 0
    enum_cut = clause_gate.cut("本条适用于下列国家：爱尔兰、冰岛、意大利、拉脱维亚、列支敦士登、立陶宛、\n卢森堡、匈牙利、马耳他、荷兰、挪威、奥地利。", 40)
    assert not enum_cut.endswith("、……")

    # (e) Portal trial spoofing block (incl. half-width parens) & audit ROUTE_ACTIONS + SUPER_ADMIN_ONLY
    from fastapi import HTTPException

    from app.audit.describe import ROUTE_ACTIONS
    from app.iam.deps import Role as _Role
    from app.iam.deps import check_role_scope

    assert ("POST", "/api/admin/users/{user_id}/promote") in ROUTE_ACTIONS
    with pytest.raises(HTTPException):
        check_role_scope(_Role.OPS_ADMIN, "GET", "/api/admin/trial-users", True)
    r_spoof_reg = await client.post(
        "/api/auth/user/register",
        json={
            "email": "spoof-reg-1128@example.com",
            "display_name": "测试员",
            "org_name": "某车企(体验空间-1234abcd)",
            "password": "StrongPassword!2026",
        },
    )
    assert r_spoof_reg.status_code == 422
    t_spoof_hw = create_portal_ticket(
        email="spoof-hw-1128@example.com",
        display_name="测试员",
        org_name="某车企(体验空间-1a2b3c4d)",
        tier="enterprise",
    )
    assert (await client.post("/api/auth/portal/exchange", json={"ticket": t_spoof_hw})).status_code == 422

    # Wave-6 cross-module closure regressions:
    # (f) Sentence-final period dot-thousands ('Rp 5.000.', 'EUR 5.000.') & CHF/EGP/JPY/KRW/THB symmetry
    assert ni_norm("Rp 5.000.") == "Rp 5000."
    assert ni_norm("5.000 CHF") == "5000 CHF" and ni_norm("5.000 瑞士法郎") == "5000 瑞士法郎"
    assert ni_norm("5.000 Egyptian pounds") == "5000 Egyptian pounds"
    assert ni_norm("5.000 円") == "5000 円" and ni_norm("5.000 원") == "5000 원" and ni_norm("5.000 บาท") == "5000 บาท"
    bad_idr_dot, _, _ = verify_translation("Denda sebesar Rp 5.000.", "罚款 5 印尼盾。", [])
    assert not bad_idr_dot
    ok_idr_dot, iss_idr_dot, _ = verify_translation("Denda sebesar Rp 5.000.", "罚款 5.000 印尼盾。", [])
    assert ok_idr_dot, iss_idr_dot

    # (g) clause_gate.clean_margin_numbers preserves foreign wrapped statutory deadlines
    assert "30 days" in clause_gate.clean_margin_numbers("notify within\n30 days of becoming aware.")
    assert "15 jours" in clause_gate.clean_margin_numbers("dans un délai de\n15 jours suivant la découverte.")
    assert "10 Tagen" in clause_gate.clean_margin_numbers("innerhalb von\n10 Tagen zu melden.")
    assert "5 días" in clause_gate.clean_margin_numbers("en un plazo de\n5 días hábiles.")

    # (h) whitepaper._tidy_brackets preserves 'processor'/'décret'/'涉及' and strips '、' before '……'
    assert _tidy_brackets("The data processor ……") == "The data processor ……"
    assert _tidy_brackets("Le présent décret ……") == "Le présent décret ……"
    assert _tidy_brackets("跨境传输活动涉及……") == "跨境传输活动涉及……"
    assert _tidy_brackets("列支敦士登、立陶宛、\n……") == "列支敦士登、立陶宛……"
    assert _tidy_brackets("采取加密措施使风险得以缓和……") == "采取加密措施使风险得以缓和……"
    assert _tidy_brackets("法定人格权利不得让与……") == "法定人格权利不得让与……"

    # Wave-7 closure regressions:
    # (i) Multilingual margin line-number symmetry & inline colon penalty range protection ([P0-1])
    zh_wrap_dl = (
        "数据控制者应当遵守下列时限要求：\n"
        "5 个工作日内完成初步评估并记录在案；\n"
        "15 个日历日内向个人数据保护委员会提交书面报告。"
    )
    assert "5 个工作日" in clean_translation(zh_wrap_dl) and "15 个日历日" in clean_translation(zh_wrap_dl)
    ru_wrap_dl = (
        "Оператор обязан уведомить орган в течение\n"
        "10 дней с даты выявления и устранить нарушения в течение\n"
        "30 дней с даты предписания."
    )
    assert "10 дней" in clean_text(ru_wrap_dl) and "30 дней" in clean_text(ru_wrap_dl)
    ar_wrap_dl = (
        "يجب على المتحكم إبلاغ المركز خلال\n"
        "15 يوماً من تاريخ علمه والرد خلال\n"
        "30 يوماً من تاريخ الطلب."
    )
    assert "15 يوماً" in clean_text(ar_wrap_dl) and "30 يوماً" in clean_text(ar_wrap_dl)
    assert "10 至 20 万" in clause_gate.clean_margin_numbers("违反本条规定的，处以罚款： 10 至 20 万摩洛哥迪拉姆。")

    # (j) Labeled statutory notification emails/URLs & clause_gate usability ([P1-1])
    zh_stat_email = (
        "第26D条 当组织评估确认发生须通报的数据泄露事件时，应当在三个日历日内通过下列官方渠道提交书面通知：\n"
        "电子邮件：breach@pdpc.gov.sg"
    )
    assert "breach@pdpc.gov.sg" in clean_translation(zh_stat_email)
    assert clause_gate.why_unusable(clean_translation(zh_stat_email), "2012年个人数据保护法", "") == ""
    fr_stat_email = "La notification de violation de données est transmise sans délai à la Commission :\nCourriel : alerte@cndp.ma"
    assert "alerte@cndp.ma" in clean_text(fr_stat_email)
    de_stat_url = "Die Meldung über eine Verletzung des Schutzes personenbezogener Daten erfolgt elektronisch unter\nhttps://www.bfdi.bund.de"
    assert "https://www.bfdi.bund.de" in clean_text(de_stat_url)

    # (k) Comma-decimal + dot-thousands amounts ([P1-2]) & T-Box / bare controller gates ([P1-3])
    assert amounts("5.000,00 EUR") == {("EUR", "5000")}
    assert amounts("Rp 5.000,00") == {("IDR", "5000")}
    assert amounts("5.000,50 EUR") == {("EUR", "5000.5")}
    ok_eur_cents, iss_eur_cents, _ = verify_translation(
        "Eine Geldbuße bis zu 5.000,00 EUR wird verhängt.", "处以最高 5,000 欧元的罚款。", []
    )
    assert ok_eur_cents, iss_eur_cents
    ok_tbox, iss_tbox, _ = verify_translation(
        "The data controller shall protect T-Box controller logs.", "数据控制者应当保护 T-Box 控制器日志。", []
    )
    assert ok_tbox, iss_tbox
    assert not verify_translation(
        "The controller shall notify the supervisory authority within 72 hours.",
        "控制器应当在 72 小时内通知监管机构。",
        [],
    )[0]
    assert verify_translation(
        "The controller checks the vehicle processor and brakes.",
        "控制器检查车辆处理器与制动系统。",
        [],
    )[0]
    assert not verify_translation(
        "Le responsable de traitement notifie la violation.", "数据控制器通报违规行为。", []
    )[0]
    assert not verify_translation(
        "يلتزم المتحكم بإخطار المركز خلال 72 ساعة.", "控制器应当在 72 小时内通知中心。", []
    )[0]

    # (l) PUA normalization across DOCX & builtin PDF ([P1-6]) + generalized year & script guards ([P2-1, P2-2])
    import pypdfium2 as pdfium

    pua_docx = build_docx(Rendered(blocks=[{"type": "paragraph", "text": "\uf0b7 加密存储 \uf0fe 已核验 \ue001"}]), "PUA检查")
    with zipfile.ZipFile(io.BytesIO(pua_docx)) as zf_pua:
        pua_xml = zf_pua.read("word/document.xml").decode("utf-8")
        assert "\u2022" in pua_xml and "\u2611" in pua_xml
        assert not re.search(r"[\ue000-\uf8ff]", pua_xml)
    pua_pdf = _render_pdf(Rendered(blocks=[{"type": "paragraph", "text": "\uf0b7 加密存储 \uf0fe 已核验 \ue001"}]), "PUA检查")
    pdf_doc = pdfium.PdfDocument(pua_pdf)
    pdf_txt = "".join(pdf_doc[i].get_textpage().get_text_range() for i in range(len(pdf_doc)))
    pdf_doc.close()
    assert not re.search(r"[\ue000-\uf8ff]", pdf_txt)
    assert clause_gate.superseded_titles(["德国 2017年联邦数据保护法", "德国 2021年联邦数据保护法"]) == {"德国 2017年联邦数据保护法"}
    assert _is_clean_latin_quote("धारा 16 व्यक्तिगत डेटा (DPDP Act)") is False
    assert _is_clean_latin_quote("Νόμος 4624/2019 (GDPR)") is False

    # (m) Authority issue empty-answer close block & post-rejection approval lock ([P1-7])
    assert (
        await client.patch(f"/api/admission/issues/{auth_issue['id']}", headers=h_mem, json={"state": "closed"})
    ).status_code == 422
    assert (
        await client.patch(
            f"/api/admission/issues/{auth_issue['id']}",
            headers=h_mem,
            json={"answer": "已补交法文正式签章授权函原件", "state": "closed"},
        )
    ).status_code == 200
    # Still in preparing after prior rejected/correcting -> must re-submit before registering approval!
    assert (
        await client.put(
            f"/api/admission/projects/{apid}/approval",
            headers=h_mem,
            json={"number": "CNIL-99", "issued_on": "2026-11-01", "expires_on": "2028-11-01"},
        )
    ).status_code == 409
    assert (await client.put(f"/api/admission/projects/{apid}/filing", headers=h_mem, json={"state": "submitted"})).status_code == 200
    assert (
        await client.put(
            f"/api/admission/projects/{apid}/approval",
            headers=h_mem,
            json={"number": "CNIL-99", "issued_on": "2026-11-01", "expires_on": "2028-11-01"},
        )
    ).status_code == 200
    # Fresh preparing project with zero prior rejection/correction still supports direct legacy approval registration
    r_ap_fresh = await client.post("/api/admission/projects", headers=h_mem, json={"entry_id": adm_entry_id, "title": "FR-FRESH"})
    assert r_ap_fresh.status_code == 201
    assert (
        await client.put(
            f"/api/admission/projects/{r_ap_fresh.json()['id']}/approval",
            headers=h_mem,
            json={"number": "CNIL-LEGACY-01", "issued_on": "2026-11-01", "expires_on": "2028-11-01"},
        )
    ).status_code == 200

    # (n) doc_projects official foreign form export with overflow uses zero-Chinese foreign annex ([P0-2])
    from app.forms import service as form_svc
    from app.forms.models import FormInstance

    r_ma_proj = await client.post(
        "/api/analysis/doc-projects",
        headers=h_mem,
        json={
            "source_country": "摩洛哥",
            "recipient_country": "法国",
            "functions": ["ota_diagnostics"],
            "vehicle_model": "QH-MA1",
            "company_name": "Qiheng Mobility Maroc SARL",
        },
    )
    assert r_ma_proj.status_code in (200, 201)
    ma_pid = r_ma_proj.json()["id"]
    ma_form = form_svc.get_form("MA_F118")
    assert ma_form is not None
    fixed_map = {f.key: f for f in (form_svc.fixed_fields(ma_form) or [])}
    text_keys = [
        it["id"]
        for sec in ma_form.get("sections", [])
        for it in sec.get("items", [])
        if it.get("kind") == "text"
        and it.get("pdf_key")
        and not (fixed_map.get(it["pdf_key"]) and (fixed_map[it["pdf_key"]].cells or fixed_map[it["pdf_key"]].segments))
    ]
    assert text_keys
    long_zh_val = "远程诊断与空中升级日志跨境加密传输（车型：QH-MA1；年度规模：5000辆；保存期限：180天；TLS1.3双向认证）" * 5
    async with session_factory() as s:
        s.add(
            FormInstance(
                org_id=seed["org_a"].id,
                task_id=uuid.UUID(ma_pid),
                form_code="MA_F118",
                form_version=str(ma_form.get("version") or "2025-09"),
                answers={text_keys[0]: long_zh_val},
                created_by=seed["a_member"].id,
            )
        )
        await s.commit()
    r_ma_pdf = await client.get(f"/api/analysis/doc-projects/{ma_pid}/official-forms/MA_F118/export.pdf", headers=h_mem)
    assert r_ma_pdf.status_code == 200 and r_ma_pdf.content.startswith(b"%PDF-")
    ma_doc = pdfium.PdfDocument(r_ma_pdf.content)
    assert len(ma_doc) > 2
    annex_last_txt = ma_doc[len(ma_doc) - 1].get_textpage().get_text_range()
    ma_doc.close()
    assert "Annexe complémentaire" in annex_last_txt
    assert not re.search(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\uff01-\uff60]", annex_last_txt)

    # Wave-8 closure regressions:
    # (1) Arabic official PDF forms (TN_ATTT_RECEPTION / EG_GOEIC_IMPORTER) translate CJK to FR/EN (0 unwritable Arabic)
    for fcode in ("TN_ATTT_RECEPTION", "EG_GOEIC_IMPORTER"):
        ar_form = form_svc.get_form(fcode)
        assert ar_form is not None
        ar_text_ids = [
            it["id"]
            for sec in ar_form.get("sections", [])
            for it in sec.get("items", [])
            if it.get("kind") == "text"
        ]
        assert ar_text_ids
        _pdf_b, ar_rep, ar_too_long = form_svc.render(
            ar_form,
            {ar_text_ids[0]: "启衡汽车有限公司（100％／5G）"},
            {},
            allow_annex=True,
            foreign_only=True,
            foreign_annex=True,
        )
        assert not ar_rep.unwritable, (fcode, ar_rep.unwritable)
        assert not any("无法直接写入" in str(t.get("label", "")) for t in ar_too_long), (fcode, ar_too_long)

    # (2) Footer letterhead Courriel after full-stop clause + stripped Tél is dropped, while colon-introduced email stays
    fr_footer_lh = (
        "Article 12. Le responsable du traitement assure la sécurité des données.\n"
        "Tél : 05 37 57 11 24\n"
        "Courriel : contact@cndp.ma"
    )
    assert "contact@cndp.ma" not in clean_text(fr_footer_lh)

    # (3) North African official Arabic 'المسؤول عن المعالجة' caught by bare controller gate
    assert not verify_translation(
        "يلتزم المسؤول عن المعالجة بإخطار اللجنة.", "控制器应当通知委员会。", []
    )[0]





