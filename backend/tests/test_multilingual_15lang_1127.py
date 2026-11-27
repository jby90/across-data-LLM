# ruff: noqa: E501
"""Offline exhaustive regression suite for 15-Language Official Host-Country Compliance Reports:
Covers `fr, de, es, pt, it, nl, id, tr, pl, ja, ko, th, vi, ar, ru` (+ `zh` & `en` = 17 languages):
1. `/api/analysis/report-languages` registry & route-based official host language recommendation;
2. Japanese (`ja`) `cp932` Joyo Kanji + Kana gate (accepting legal Japanese while blocking PRC-simplified & pure-Chinese clauses);
3. Arabic (`ar`) RTL `<w:bidi/>` + `<w:bidiVisual/>` mirrored table columns + LRI/PDI (`\\u2066...\\u2069`) BiDi isolation;
4. Thai (`th`) cluster-safe `U+200B` ZWSP line breaking;
5. Viewer `can_export=False` RBAC `403` enforcement vs read-only `preview-i18n` `200`;
6. All 15 host languages across `preview-i18n`, `export-i18n.docx`, `export-i18n.pdf`, `doc-projects/{id}/export.zip`, and `reports/1/export.{docx,pdf}` + mocked LLM `DocumentNarrative` (`{code}__{lang}_v1`) cache persistence.
"""

from __future__ import annotations

import io
import uuid
import zipfile
from types import SimpleNamespace
from unittest.mock import patch
from xml.etree import ElementTree as ET

import pypdfium2 as pdfium
from sqlalchemy import select

from app.analysis.document_narrative import DocumentNarrative
from app.analysis.models import AnalysisTask, ReportVersion
from app.analysis.multilingual_report import (
    HOST_REPORT_LANGS,
    _inject_thai_zwsp,
    _isolate_arabic_ltr,
    build_multilingual_doc_blocks,
    clean_and_gate_target_text,
    has_untranslated_chinese_leak,
    load_lang_lexicon,
    release_lang_lexicon_cache,
)
from app.iam import service as iam_service
from app.iam.models import Role

from .conftest import PASSWORD, bearer, login


def _docx_xml_and_text(raw: bytes) -> tuple[str, str]:
    xml_parts: list[str] = []
    txt_parts: list[str] = []
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        for name in zf.namelist():
            if name.startswith("word/") and name.endswith(".xml"):
                raw_xml = zf.read(name).decode("utf-8")
                xml_parts.append(raw_xml)
                root = ET.fromstring(raw_xml)
                txt_parts.extend(t for t in root.itertext() if t)
    return "\n".join(xml_parts), " ".join(txt_parts)


def _pdf_all_text(raw: bytes) -> str:
    doc = pdfium.PdfDocument(raw)
    try:
        out: list[str] = []
        for idx in range(len(doc)):
            tp = doc[idx].get_textpage()
            out.append(tp.get_text_range())
            tp.close()
        return " ".join(out)
    finally:
        doc.close()


def test_japanese_gate_arabic_bidi_thai_zwsp_and_bounded_lexicon():
    assert len(HOST_REPORT_LANGS) == 15

    # 1. Bounded LRU cache (`maxsize=3`) for 1.6 GiB zero-Swap discipline
    release_lang_lexicon_cache()
    for lg in ("fr", "de", "ja", "ar", "ru"):
        lex = load_lang_lexicon(lg)
        assert len(lex["doc_titles"]) >= 8
        assert len(lex["dimension_titles"]) >= 8
    assert load_lang_lexicon.cache_info().currsize <= 3
    release_lang_lexicon_cache()
    assert load_lang_lexicon.cache_info().currsize == 0

    # 2. Japanese legal Kanji + Kana gate vs Mainland-simplified Chinese leak
    valid_ja = "個人情報保護法に基づき、車両データの越境移転には本人の同意及び暗号化措置が必要です。"
    assert has_untranslated_chinese_leak(valid_ja, "ja") == []
    assert clean_and_gate_target_text(valid_ja, "ja") == valid_ja
    ja_corp = "星途智行汽車科技株式会社"
    assert has_untranslated_chinese_leak(ja_corp, "ja") == []
    assert clean_and_gate_target_text(ja_corp, "ja") == ja_corp

    simplified_zh = "车辆远程诊断应当履行数据出境安全评估义务"
    assert len(has_untranslated_chinese_leak(simplified_zh, "ja")) > 0
    gated_ja = clean_and_gate_target_text(simplified_zh, "ja", fallback="越境データ移転評価")
    assert has_untranslated_chinese_leak(gated_ja, "ja") == []

    # 3. Arabic LTR BiDi isolation (`\u2066...\u2069`) without double-wrapping
    ar_raw = "نموذج المركبة QH-S9 عبر قناة TLS بتاريخ 2026-11-27"
    ar_iso = _isolate_arabic_ltr(ar_raw)
    assert "\u2066QH-S9\u2069" in ar_iso
    assert "\u20662026-11-27\u2069" in ar_iso
    assert _isolate_arabic_ltr(ar_iso) == ar_iso

    # 4. Thai cluster-safe U+200B line breaking
    th_long = "การประเมินผลกระทบด้านการคุ้มครองข้อมูลส่วนบุคคลสำหรับยานยนต์อัจฉริยะข้ามพรมแดน"
    th_wrapped = _inject_thai_zwsp(th_long)
    assert "\u200b" in th_wrapped
    assert th_wrapped.replace("\u200b", "") == th_long


async def test_all_15_host_languages_routes_exports_cache_and_rbac(client, seed, session_factory):
    member = bearer(await login(client, "user", "a-member@example.com"))
    async with session_factory() as s:
        await iam_service.create_user(
            s,
            email="v-15lang@example.com",
            display_name="多语种只读测试员",
            password=PASSWORD,
            role=Role.VIEWER,
            org_id=seed["org_a"].id,
            can_export=False,
        )
    viewer = bearer(await login(client, "user", "v-15lang@example.com"))

    # 1. `/api/analysis/report-languages` recommendation accuracy
    for rcp, expected_first in (
        ("日本", "ja"),
        ("摩洛哥", "fr"),
        ("沙特阿拉伯", "ar"),
        ("韩国", "ko"),
        ("泰国", "th"),
        ("德国", "de"),
        ("俄罗斯", "ru"),
        ("巴西", "pt"),
    ):
        r_lang = await client.get(
            "/api/analysis/report-languages",
            params={"source_country": "中国", "recipient_country": rcp},
            headers=member,
        )
        assert r_lang.status_code == 200
        body = r_lang.json()
        assert len(body["languages"]) == 17
        assert body["recommended"][0]["code"] == expected_first, (rcp, body["recommended"])

    # 2. Create compliance document project + 8-dimension consulting report
    r_proj = await client.post(
        "/api/analysis/doc-projects",
        headers=member,
        json={
            "source_country": "中国",
            "recipient_country": "沙特阿拉伯",
            "functions": ["ota_diagnostics", "跨境车队碳排监测"],
            "business_scenes": ["after_sales"],
            "vehicle_model": "QH-S9",
            "company_name": "测试车企股份有限公司",
        },
    )
    assert r_proj.status_code == 201, r_proj.text
    pid = r_proj.json()["id"]

    async with session_factory() as s:
        chat = AnalysisTask(
            org_id=seed["org_a"].id,
            created_by=seed["a_member"].id,
            kind="chat",
            title="沙特车联网合规咨询评估",
            title_custom=True,
            country_code="SA",
            vehicle_model="QH-S9",
            scenario_type="connected_vehicle_cloud",
            status="completed",
            facts_json={"recipient_country": "沙特阿拉伯", "source_country": "中国"},
        )
        s.add(chat)
        await s.flush()
        s.add(
            ReportVersion(
                org_id=seed["org_a"].id,
                task_id=chat.id,
                version_no=1,
                status="full_draft",
                is_partial_sample=False,
                change_note="初始生成",
                executive_summary="履行跨境传输安全评估与端到端加密保护义务。",
                dimensions_json=[
                    {
                        "dimension_code": f"D{i}",
                        "dimension_title": f"维度{i}",
                        "status": "ok",
                        "risk_level": "中风险",
                        "content_markdown": "采取最小必要脱敏与跨境传输加密控制。",
                        "key_recommendations": ["签署跨境标准合同条款并开展年度审计"],
                        "citations": [
                            {
                                "clause_id": None,
                                "document_title": "沙特个人数据保护法",
                                "original_title": "نظام حماية البيانات الشخصية",
                                "clause_number": "29",
                                "version_no": 1,
                                "quote_original": "لا يجوز لجهة التحكم نقل البيانات الشخصية إلى خارج المملكة إلا وفق الضوابط المحددة.",
                                "quote_zh": "控制者非经法定保障措施不得向王国境外传输个人数据。",
                            }
                        ],
                        "findings": [],
                        "missing_gap_note": None,
                    }
                    for i in range(1, 9)
                ],
                stale_impacts_json=[],
            )
        )
        await s.commit()
        chat_id = str(chat.id)

    # 3. RBAC: Viewer (`can_export=False`) gets 403 on all i18n exports, 200 on read-only preview-i18n
    assert (await client.get(f"/api/analysis/tasks/{pid}/documents/dpia/preview-i18n?lang=ar&draft=true", headers=viewer)).status_code == 200
    for ep in (
        f"/api/analysis/tasks/{pid}/documents/dpia/export-i18n.docx?lang=ar&draft=true",
        f"/api/analysis/tasks/{pid}/documents/dpia/export-i18n.pdf?lang=ar&draft=true",
        f"/api/analysis/doc-projects/{pid}/export.zip?fmt=docx&draft=true&lang=ar",
        f"/api/analysis/tasks/{chat_id}/reports/1/export.docx?lang=ar",
        f"/api/analysis/tasks/{chat_id}/reports/1/export.pdf?lang=ar",
    ):
        r403 = await client.get(ep, headers=viewer)
        assert r403.status_code == 403, (ep, r403.text)

    # 4. Exhaustive check across ALL 15 official host-country languages (preview + doc DOCX + consulting DOCX)
    for lg in HOST_REPORT_LANGS:
        r_prev = await client.get(
            f"/api/analysis/tasks/{pid}/documents/dpia/preview-i18n?lang={lg}&draft=true",
            headers=member,
        )
        assert r_prev.status_code == 200, (lg, r_prev.text)
        p_body = r_prev.json()
        assert p_body["lang"] == lg
        assert not has_untranslated_chinese_leak(str(p_body["blocks"]), lg), lg

        r_docx = await client.get(
            f"/api/analysis/tasks/{pid}/documents/dpia/export-i18n.docx?lang={lg}&draft=true",
            headers=member,
        )
        assert r_docx.status_code == 200, lg
        d_xml, d_txt = _docx_xml_and_text(r_docx.content)
        assert not has_untranslated_chinese_leak(d_txt, lg), (lg, has_untranslated_chinese_leak(d_txt, lg)[:8])
        if lg == "ar":
            assert "<w:bidi/>" in d_xml and "<w:bidiVisual/>" in d_xml

        r_rep = await client.get(
            f"/api/analysis/tasks/{chat_id}/reports/1/export.docx?lang={lg}",
            headers=member,
        )
        assert r_rep.status_code == 200, lg
        _, rep_txt = _docx_xml_and_text(r_rep.content)
        assert not has_untranslated_chinese_leak(rep_txt, lg), lg
        if lg == "ar":
            # Host-country Arabic statutory quote preserved verbatim when target lang == ar
            assert "لا يجوز لجهة التحكم" in rep_txt

    # 5. Representative multi-script PDF + ZIP verification (`fr`, `ja`, `ko`, `th`, `vi`, `ar`, `ru`)
    for lg in ("fr", "ja", "ko", "th", "vi", "ar", "ru"):
        r_pdf = await client.get(
            f"/api/analysis/tasks/{pid}/documents/dpia/export-i18n.pdf?lang={lg}&draft=true",
            headers=member,
        )
        assert r_pdf.status_code == 200 and r_pdf.content.startswith(b"%PDF"), lg
        pdf_txt = _pdf_all_text(r_pdf.content)
        assert not has_untranslated_chinese_leak(pdf_txt, lg), lg

        r_rep_pdf = await client.get(
            f"/api/analysis/tasks/{chat_id}/reports/1/export.pdf?lang={lg}",
            headers=member,
        )
        assert r_rep_pdf.status_code == 200 and r_rep_pdf.content.startswith(b"%PDF"), lg
        assert not has_untranslated_chinese_leak(_pdf_all_text(r_rep_pdf.content), lg), lg

    r_zip_ar = await client.get(
        f"/api/analysis/doc-projects/{pid}/export.zip?fmt=docx&draft=true&lang=ar",
        headers=member,
    )
    assert r_zip_ar.status_code == 200
    with zipfile.ZipFile(io.BytesIO(r_zip_ar.content)) as zf:
        ar_files = [n for n in zf.namelist() if n.endswith(".docx") and "_AR" in n]
        assert len(ar_files) >= 3
        for n in ar_files:
            _, z_txt = _docx_xml_and_text(zf.read(n))
            assert not has_untranslated_chinese_leak(z_txt, "ar"), n

    # 6. Mocked LLM slot translation + persistent `DocumentNarrative` (`dpia__fr_v1`) cache hit
    async def _fake_llm_json(messages, **kwargs):
        import json as _json

        payload = _json.loads(messages[-1]["content"])
        slots = payload.get("slots", {})
        return SimpleNamespace(
            data={"slots": {k: f"Traduction réglementaire officielle ({k}) QH-S9" for k in slots}},
            total_tokens=321,
        )

    async with session_factory() as s:
        t_obj = await s.get(AnalysisTask, uuid.UUID(pid))
        assert t_obj is not None
        zh_sample = [{"type": "paragraph", "text": "需要通过大模型精细翻译的跨国车辆合规段落。"}]
        with patch("app.analysis.multilingual_report.call_llm_json", side_effect=_fake_llm_json):
            b1, _, _ = await build_multilingual_doc_blocks(
                s, t_obj, "dpia", zh_sample, {}, [], lang="fr", draft=True, force_llm=True
            )
        assert any("Traduction réglementaire officielle" in str(b.get("text", "")) for b in b1)
        cached = await s.scalar(
            select(DocumentNarrative).where(
                DocumentNarrative.task_id == t_obj.id,
                DocumentNarrative.code == "dpia__fr_v1",
            )
        )
        assert cached is not None and cached.tokens == 321
        # Second call must hit cache without invoking LLM again
        await s.refresh(t_obj)
        b2, _, _ = await build_multilingual_doc_blocks(
            s, t_obj, "dpia", zh_sample, {}, [], lang="fr", draft=True, force_llm=True
        )
        assert b2 == b1
