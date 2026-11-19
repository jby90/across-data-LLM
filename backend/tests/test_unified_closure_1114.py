"""End-to-end verification of Track 1 (exemplar-driven prose & typography/official template layout),
Track 2 (0->0.5 policy upload with outbound redaction summary + incremental briefing dimension sync),
and Track 3 (weekly opinion digest, 72h SLA Excel report, KB status/fuzzy search, executive overview &
project clone, and KG validated batch import)."""

from __future__ import annotations

import datetime as dt
import io
import zipfile
from xml.etree import ElementTree as ET

from openpyxl import load_workbook

from app.analysis import document_instances as di
from app.analysis import document_narrative as dn
from app.analysis import document_templates as tpl
from app.analysis import official_docx
from app.core.redaction import redact, summarize_redaction
from app.iam import service as iam_service
from app.iam.models import Role
from app.ingestion.models import Clause, Document, DocumentVersion
from app.knowledge.whitepaper import CountryWhitepaper
from app.opinion.models import OpinionItem
from app.sources.models import SourceSite

from .conftest import PASSWORD, bearer, login

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def test_track1_narrative_guards_and_typography_exports() -> None:
    # 1) Single-verdict guard strips premature verdicts on non-final chapters while final chapter skips it
    early = (
        "车端已完成脱敏隔离并在上海境内网关实施双向证书加密校验与最小权限白名单管控。"
        "综合核查，本次出境目的合法、正当、必要，整体风险可控，在申报获批后可开展跨境传输。"
    )
    stripped = dn._strip_premature_verdict(early)
    assert "车端已完成脱敏隔离" in stripped
    assert "综合核查" not in stripped and "可开展跨境传输" not in stripped

    # 2) Prose cleanup normalizes 冷备快照 -> 备份数据 and removes leaked spaces
    cleaned = dn.polish_legal_prose("境外备份服务器保存 30 天冷备快照 ， 到期自动安全删除。")
    assert "冷备快照" not in cleaned
    assert "备份数据" in cleaned
    assert " ， " not in cleaned

    # 3) Word export applies 2-char Chinese first-line indent (firstLine=440) + justified alignment + clean core.xml
    blocks = [
        {"type": "title", "text": "数据出境风险自评估报告"},
        {"type": "heading", "text": "一、基本情况"},
        {"type": "paragraph", "text": "本章节围绕车载远程诊断业务开展事实梳理与合规边界核验，明确境内脱敏边界与境外只读访问控制。"},
        {"type": "table", "head": ["字段", "说明"], "rows": [["**故障码**", "用于云端故障预警"]]},
        {"type": "list", "items": ["第一项控制措施"]},
    ]
    rendered = tpl.Rendered(blocks=blocks)
    docx_bytes = tpl.build_docx(rendered, "数据出境风险自评估报告")
    with zipfile.ZipFile(io.BytesIO(docx_bytes)) as zf:
        if "docProps/core.xml" in zf.namelist():
            core_xml = zf.read("docProps/core.xml").decode("utf-8")
            assert "python-docx" not in core_xml
        doc_root = ET.fromstring(zf.read("word/document.xml"))
        indents = [el.attrib.get(W + "firstLine") for el in doc_root.iter(W + "ind")]
        assert "440" in indents
        jcs = [el.attrib.get(W + "val") for el in doc_root.iter(W + "jc")]
        assert "both" in jcs

    # 4) Excel export strips **bold** markers and preserves · list bullets + all chapters
    xlsx_bytes = di.build_xlsx(blocks, "数据出境风险自评估报告")
    wb = load_workbook(io.BytesIO(xlsx_bytes))
    cells = [c.value for row in wb.active.iter_rows() for c in row if c.value is not None]
    assert "故障码" in cells
    assert not any("**" in str(v) for v in cells)
    assert "· 第一项控制措施" in cells

    # 5) 1:1 CAC official standard contract (.docx) separates Annex I (八) retention vs (九) overseas location & cleans signature line
    res = official_docx.cn_standard_contract(
        {
            "company_name": "启衡智行汽车科技（上海）有限公司",
            "recipient": "Qiheng Mobility Singapore Pte. Ltd.",
            "purpose": "海外售后远程电池热失控预警",
            "retention_and_blackbox": "境外节点存储于新加坡裕廊东独立云服务器集群，保存期限严格限定为180天到期自动安全删除",
            "recipient_country": "新加坡",
            "storage_chain": "双向 TLS 1.3 加密专用通道传输",
        }
    )
    with zipfile.ZipFile(io.BytesIO(res.data)) as zf:
        root = ET.fromstring(zf.read("word/document.xml"))
        full_text = "".join(root.itertext())
        assert "（八）出境后保存期限" in full_text
        assert "（九）出境后保存地点" in full_text
        assert "180天" in full_text
        assert "新加坡裕廊东独立云服务器集群" in full_text
        assert "签字：____" not in full_text


def test_track2_redaction_summary_zero_plaintext_leak() -> None:
    raw = (
        "联系人邮箱 zhangsan@qiheng-auto.com，联系电话 13812345678，"
        "测试车架号 LSVAA4182N2123456，内部代号 鲲鹏智驾平台。"
    )
    redacted, _ = redact(raw, aliases=("鲲鹏智驾平台",))
    summary = summarize_redaction(raw, aliases=("鲲鹏智驾平台",))
    assert "zhangsan@qiheng-auto.com" not in redacted
    assert "13812345678" not in redacted
    assert "LSVAA4182N2123456" not in redacted
    assert "鲲鹏智驾平台" not in redacted
    assert summary["total"] >= 4
    assert any("企业主体与自定义敏感词" in lbl for lbl in summary["labels"])
    summary_str = str(summary)
    assert "zhangsan" not in summary_str and "鲲鹏智驾平台" not in summary_str


async def test_track2_and_track3_end_to_end_apis(client, seed, session_factory, monkeypatch) -> None:
    async with session_factory() as s:
        await iam_service.create_user(
            s,
            email="ops-closure@example.com",
            display_name="日常运营员",
            password=PASSWORD,
            role=Role.OPS_ADMIN,
            org_id=None,
        )
        await iam_service.create_user(
            s,
            email="viewer-closure@example.com",
            display_name="只读查看者",
            password=PASSWORD,
            role=Role.VIEWER,
            org_id=seed["org_a"].id,
        )
        site = SourceSite(
            country_code="SG",
            publisher="PDPC Singapore",
            entry_url="https://www.pdpc.gov.sg",
            language="en",
            document_type="LAW",
            enabled=True,
            terms_status="approved",
            last_status="success",
        )
        s.add(site)
        await s.flush()

        doc_force = Document(
            site_id=site.id,
            country_code="SG",
            title="Personal Data Protection Act 2012",
            title_zh="新加坡个人数据保护法",
            document_type="act",
            language="en",
            relevance="relevant",
            source_url="https://sso.agc.gov.sg/Act/PDPA2012",
        )
        doc_draft = Document(
            site_id=site.id,
            country_code="SG",
            title="Connected Vehicle Data Guidelines Draft",
            title_zh="智能网联汽车数据指南（征求意见草案）",
            document_type="guidance",
            language="en",
            relevance="relevant",
            source_url="https://www.pdpc.gov.sg/draft-cv",
        )
        s.add_all([doc_force, doc_draft])
        await s.flush()

        ver_force = DocumentVersion(
            document_id=doc_force.id,
            version_no=1,
            state="parsed",
            legal_status="in_force",
            content_hash="a" * 64,
            archive_path="raw/sg_pdpa.pdf",
            size_bytes=2048,
            mime_type="application/pdf",
            final_url="https://sso.agc.gov.sg/Act/PDPA2012",
            fetched_at=dt.datetime.now(dt.UTC),
        )
        ver_draft = DocumentVersion(
            document_id=doc_draft.id,
            version_no=1,
            state="parsed",
            legal_status="draft",
            content_hash="b" * 64,
            archive_path="raw/sg_draft.pdf",
            size_bytes=1024,
            mime_type="application/pdf",
            final_url="https://www.pdpc.gov.sg/draft-cv",
            fetched_at=dt.datetime.now(dt.UTC),
        )
        s.add_all([ver_force, ver_draft])
        await s.flush()

        clause1 = Clause(
            version_id=ver_force.id,
            ordinal=1,
            clause_number="Section 26",
            heading="Transfer of personal data outside Singapore",
            section_path="Part 6",
            original_text="An organisation must not transfer any personal data to a country or territory outside Singapore except in accordance with requirements prescribed under this Act.",
            text_hash="c" * 64,
            language="en",
        )
        clause_draft = Clause(
            version_id=ver_draft.id,
            ordinal=1,
            clause_number="Para 1",
            heading="Scope",
            section_path="Part 1",
            original_text="Draft guidance for connected vehicle telemetry.",
            text_hash="d" * 64,
            language="en",
        )
        s.add_all([clause1, clause_draft])
        await s.flush()
        clause1_id = str(clause1.id)

        s.add(
            CountryWhitepaper(
                country_code="SG",
                version_no=1,
                source_hash="init_hash",
                tokens=500,
                blocks=[
                    {"type": "title", "text": "新加坡汽车数据合规简报"},
                    {"type": "heading", "text": "一、法规体系全景"},
                    {"type": "paragraph", "text": "新加坡以《个人数据保护法》为核心。"},
                ],
                report={"documents": 1, "clauses": 1, "sections_meta": {"D1": {"paragraphs": [{"text": "新加坡以《个人数据保护法》为核心。"}]}}},
            )
        )
        s.add(
            OpinionItem(
                publisher="新加坡个人数据保护委员会",
                kind="regulator",
                title="新加坡发布车载智能终端跨境数据传输安全指引通知",
                url="https://www.pdpc.gov.sg/news/cv-transfer-2026",
                published_on=dt.date.today(),
                body="明确要求车载遥测数据跨境传输前完成保护水平核验与加密隔离。",
                content_hash="e" * 64,
                level="high",
                bases=["涉及跨境数据传输法定要求"],
            )
        )
        await s.commit()

    admin_hdr = bearer(await login(client, "admin", seed["admin"].email))
    ops_hdr = bearer(await login(client, "admin", "ops-closure@example.com"))
    member_hdr = bearer(await login(client, "user", seed["a_member"].email))
    viewer_hdr = bearer(await login(client, "user", "viewer-closure@example.com"))

    # 1) Opinion weekly digest config + preview + send + user portal weekly-digests linkage
    cfg_r = await client.put(
        "/api/admin/opinion/digest-config",
        headers=ops_hdr,
        json={
            "enabled": True,
            "weekday": 1,
            "hour_utc": 1,
            "lookback_days": 14,
            "min_level": "low",
            "recipients": ["compliance@oem.example.com"],
            "smtp_host": "",
            "smtp_port": 587,
            "smtp_user": "",
            "smtp_from": "",
        },
    )
    assert cfg_r.status_code == 200 and cfg_r.json()["recipients"] == ["compliance@oem.example.com"]

    prev_r = await client.get("/api/admin/opinion/digest-preview?days=14", headers=ops_hdr)
    assert prev_r.status_code == 200 and prev_r.json()["item_count"] >= 1
    assert "车载智能终端" in prev_r.json()["html_body"]

    send_r = await client.post("/api/admin/opinion/digest-send", headers=ops_hdr, json={})
    assert send_r.status_code == 200 and send_r.json()["item_count"] >= 1

    user_digests = await client.get("/api/notifications/weekly-digests", headers=member_hdr)
    assert user_digests.status_code == 200 and len(user_digests.json()) >= 1
    assert user_digests.json()[0]["items"][0]["country_code"] == "SG"

    # 2) Updates 72h SLA & patrol timeliness report + Excel export
    rep_r = await client.get("/api/admin/updates/report", headers=ops_hdr)
    assert rep_r.status_code == 200
    assert "response_72h" in rep_r.json() and "patrol_timeliness" in rep_r.json()

    xlsx_r = await client.get("/api/admin/updates/report/export.xlsx", headers=ops_hdr)
    assert xlsx_r.status_code == 200
    wb = load_workbook(io.BytesIO(xlsx_r.content))
    assert "时效与联动总览" in wb.sheetnames and "分国别入库与联动明细" in wb.sheetnames

    # 3) Incremental briefing dimension sync accessible to ops_admin (while full update stays super_admin only)
    async def _noop_sync_job(*_args, **_kwargs):
        from app.knowledge import whitepaper_router as wpr

        wpr._running.discard("SG")

    monkeypatch.setattr("app.knowledge.whitepaper_router._sync_dims_job", _noop_sync_job)
    assert (await client.post("/api/admin/knowledge/whitepapers/SG/update", headers=ops_hdr)).status_code == 403
    sync_r = await client.post(
        "/api/admin/knowledge/whitepapers/SG/sync-dimensions",
        headers=ops_hdr,
        json={"dimensions": ["D3", "D6"]},
    )
    assert sync_r.status_code == 202 and sync_r.json()["synced_dimensions"] == ["D3", "D6"]

    # 4) User KB legal_status filter + multi-keyword fuzzy search
    draft_docs = await client.get(
        "/api/knowledge/regulations?country_code=SG&legal_status=draft",
        headers=member_hdr,
    )
    assert draft_docs.status_code == 200
    assert len(draft_docs.json()) == 1 and draft_docs.json()[0]["legal_status"] == "draft"

    fuzzy_docs = await client.get(
        "/api/knowledge/regulations?country_code=SG&keyword=新加坡+保护法",
        headers=member_hdr,
    )
    assert fuzzy_docs.status_code == 200 and len(fuzzy_docs.json()) == 1

    # 5) Create compliance project -> clone project -> executive compliance-overview
    proj_r = await client.post(
        "/api/analysis/doc-projects",
        headers=member_hdr,
        json={
            "source_country": "中国",
            "recipient_country": "新加坡",
            "function": "remote_diag",
            "scenario_text": "车载电池热管理诊断日志跨境回传新加坡技术中心",
            "vehicle_model": "QH-E9",
            "company_name": "启衡智行汽车科技有限公司",
        },
    )
    assert proj_r.status_code == 201
    pid = proj_r.json()["id"]

    # Viewer cannot clone; member can clone
    assert (await client.post(f"/api/analysis/doc-projects/{pid}/clone", headers=viewer_hdr)).status_code == 403
    clone_r = await client.post(f"/api/analysis/doc-projects/{pid}/clone", headers=member_hdr)
    assert clone_r.status_code == 201 and "（副本）" in clone_r.json()["title"]

    ov_r = await client.get("/api/analysis/doc-projects/compliance-overview", headers=viewer_hdr)
    assert ov_r.status_code == 200
    assert ov_r.json()["summary"]["projects_total"] >= 2
    assert any("中国 → 新加坡" in r["route"] for r in ov_r.json()["route_distribution"])

    # 6) Knowledge graph validated batch import (creates valid node + edge, rejects invalid obligation without clause)
    tpl_r = await client.get("/api/admin/knowledge/batch-template", headers=ops_hdr)
    assert tpl_r.status_code == 200 and "DATA_CATEGORY" in tpl_r.json()["node_types"]

    batch_r = await client.post(
        "/api/admin/knowledge/batch-import",
        headers=admin_hdr,
        json={
            "nodes": [
                {
                    "country_code": "SG",
                    "node_type": "DATA_CATEGORY",
                    "name_zh": "车载动力电池故障遥测数据",
                    "name_orig": "Traction Battery Telemetry",
                },
                {
                    "country_code": "SG",
                    "node_type": "OBLIGATION",
                    "name_zh": "缺少法条依据的无效义务节点",
                },
            ],
            "edges": [],
        },
    )
    assert batch_r.status_code == 200
    bdata = batch_r.json()
    assert bdata["created_nodes_count"] == 1
    assert len(bdata["errors"]) == 1

    # Create a second node + validated edge with exact quote from clause1
    node1_id = bdata["created_nodes"][0]["id"]
    batch_edge_r = await client.post(
        "/api/admin/knowledge/batch-import",
        headers=admin_hdr,
        json={
            "nodes": [
                {
                    "country_code": "SG",
                    "node_type": "OBLIGATION",
                    "name_zh": "个人数据跨境转移须满足法定保护要求",
                    "evidence_clause_id": clause1_id,
                }
            ],
            "edges": [],
        },
    )
    assert batch_edge_r.status_code == 200 and batch_edge_r.json()["created_nodes_count"] == 1
    node2_id = batch_edge_r.json()["created_nodes"][0]["id"]

    edge_import_r = await client.post(
        "/api/admin/knowledge/batch-import",
        headers=admin_hdr,
        json={
            "nodes": [],
            "edges": [
                {
                    "source_node_id": node2_id,
                    "target_node_id": node1_id,
                    "relation_type": "APPLIES_TO_SCENARIO",
                    "evidence_clause_id": clause1_id,
                    "evidence_quote": "An organisation must not transfer any personal data to a country or territory outside Singapore",
                },
                {
                    "source_node_id": node2_id,
                    "target_node_id": node1_id,
                    "relation_type": "REQUIRES",
                    "evidence_clause_id": clause1_id,
                    "evidence_quote": "This quote does not exist anywhere in the clause text",
                },
            ],
        },
    )
    assert edge_import_r.status_code == 200
    assert edge_import_r.json()["created_edges_count"] == 1
    assert len(edge_import_r.json()["errors"]) == 1

    # 7) Incremental Stage-4 linkage & calibration contracts (KG synonyms, clauses search, translations X-Total-Count,
    #    cockpit standards/origin flags, source-changes affected_dims, admission X-Status-Counts/counts, audit active_only)
    tpl_ma = await client.get("/api/admin/knowledge/batch-template?country_code=MA", headers=ops_hdr)
    assert tpl_ma.status_code == 200
    assert tpl_ma.json()["json_example"]["nodes"][0]["country_code"] == "摩洛哥"

    zh_syn_edge = await client.post(
        "/api/admin/knowledge/batch-import",
        headers=admin_hdr,
        json={
            "nodes": [],
            "edges": [
                {
                    "country_code": "新加坡",
                    "source_node_id": node2_id,
                    "target_node_id": node1_id,
                    "relation_type": "涵盖数据",
                    "evidence_clause_id": clause1_id,
                    "evidence_quote": "transfer any personal data to a country or territory outside Singapore",
                }
            ],
        },
    )
    assert zh_syn_edge.status_code == 200 and zh_syn_edge.json()["created_edges_count"] == 1

    clause_search = await client.get(
        "/api/admin/knowledge/clauses?country_code=SG&q=transfer", headers=ops_hdr
    )
    assert clause_search.status_code == 200
    assert any(c["id"] == clause1_id for c in clause_search.json())

    tr_short = await client.get(
        "/api/admin/knowledge/translations?country_code=SG&include_short=false",
        headers=ops_hdr,
    )
    assert tr_short.status_code == 200
    assert int(tr_short.headers["X-Total-Count"]) == len(tr_short.json())

    std_all = await client.get("/api/knowledge/standards?limit=1000", headers=member_hdr)
    assert std_all.status_code == 200

    cockpit_r = await client.get("/api/admin/cockpit/overview", headers=admin_hdr)
    assert cockpit_r.status_code == 200
    cdata = cockpit_r.json()
    assert "intl_standards_count" in cdata["headline"]
    assert "national_standards_count" in cdata["headline"]
    assert "whitepapers_outdated" in cdata["operations"]
    assert not any(c["country_code"] in ("ZZ", "INTL") for c in cdata["countries"])

    from app.updates.models import VersionChange

    async with session_factory() as s:
        s.add(
            VersionChange(
                origin_key="test-sg-pdpa-s26",
                document_id=doc_force.id,
                old_version_id=ver_force.id,
                new_version_id=ver_force.id,
                detected_at=dt.datetime.now(dt.UTC),
                change_kind="modified",
                summary="Section 26 cross-border transfer restriction updated",
                changes_json=[],
                graph_edge_ids=[],
                content_hashes={},
            )
        )
        await s.commit()

    src_changes_r = await client.get("/api/admin/updates/source-changes", headers=ops_hdr)
    assert src_changes_r.status_code == 200
    sc_body = src_changes_r.json()
    assert isinstance(sc_body["items"], list) and len(sc_body["items"]) >= 1
    assert sc_body["items"][0]["affected_dims"] and isinstance(
        sc_body["items"][0]["affected_dims"], list
    )

    adm_entries_r = await client.get("/api/admin/admission/entries", headers=ops_hdr)
    assert adm_entries_r.status_code == 200 and isinstance(adm_entries_r.json(), list)
    assert "X-Status-Counts" in adm_entries_r.headers
    adm_counts_r = await client.get("/api/admin/admission/counts", headers=ops_hdr)
    assert adm_counts_r.status_code == 200 and "status_counts" in adm_counts_r.json()

    assert (
        await client.get("/api/admin/agent-studio/tasks?include_archived=false", headers=ops_hdr)
    ).status_code == 403
    studio_tasks_r = await client.get(
        "/api/admin/agent-studio/tasks?include_archived=false", headers=admin_hdr
    )
    assert studio_tasks_r.status_code == 200

    audit_active_r = await client.get("/api/admin/audit-logs?active_only=true", headers=admin_hdr)
    assert audit_active_r.status_code == 200

    # Standalone consultation list excludes project-side copilot chat while default list keeps it
    proj_r = await client.post(
        "/api/analysis/doc-projects",
        headers=member_hdr,
        json={"source_country": "中国", "recipient_country": "新加坡", "function": "ota_diagnostics"},
    )
    assert proj_r.status_code == 201
    side_chat_id = (
        await client.post(f"/api/analysis/doc-projects/{proj_r.json()['id']}/chat", headers=member_hdr)
    ).json()["chat_id"]
    all_chats = {t["id"] for t in (await client.get("/api/analysis/tasks", headers=member_hdr)).json()}
    standalone_chats = {
        t["id"]
        for t in (await client.get("/api/analysis/tasks?standalone_only=true", headers=member_hdr)).json()
    }
    assert side_chat_id in all_chats
    assert side_chat_id not in standalone_chats


def test_stage4_pure_unit_calibration_and_wording_guards() -> None:
    from types import SimpleNamespace

    from app.admission.router import fact_example
    from app.analysis import doc_field_guide, document_templates_default, documents_router
    from app.audit import describe as audit_describe
    from app.knowledge import graph_edit, legal_terms, reading
    from app.knowledge import router as kg_router
    from app.opinion import weekly_digest
    from app.updates import notices

    # 1) All 12 standard Chinese relation labels + business synonyms map cleanly
    for code, zh_label in graph_edit.RELATIONS.items():
        assert kg_router._RELATION_ZH_MAP.get(zh_label) == code
    assert kg_router._RELATION_ZH_MAP["涵盖数据"] == "INVOLVES_DATA"
    assert kg_router._RELATION_ZH_MAP["豁免于"] == "EXEMPTS"
    assert kg_router._RELATION_ZH_MAP["限制出境"] == "RESTRICTS_TRANSFER"
    assert kg_router._resolve_cc_input("中国") == "CN"
    assert kg_router._resolve_cc_input("摩洛哥") == "MA"
    assert kg_router._resolve_cc_input("国际通用标准") == "ZZ"

    # 2) Glossary stopword filter & 2020 Ed. year guard
    assert legal_terms.is_stopword_term("OF", "of") is True
    assert legal_terms.is_stopword_term("the", "the") is True
    assert legal_terms.is_stopword_term("personal data", "个人数据") is False
    assert legal_terms.is_stopword_term("routing party", "routing party") is False
    assert legal_terms.is_stopword_term("controller", "controller") is False
    assert kg_router._clean_glossary_hits(
        [
            {"source_term": "OF", "target_zh": "of"},
            {"source_term": "organisations", "target_zh": "organisation"},
            {"source_term": "personal data", "target_zh": "个人数据"},
        ]
    ) == [{"source_term": "personal data", "target_zh": "个人数据"}]
    assert reading.article_label("2020") == "2020"
    assert reading.article_label("26") == "第26条"

    # 3) Field guide & template brief natural Chinese guards + _user_label fallback
    guide_dump = str(doc_field_guide.DOC_SHORT)
    assert "数据保护影响评估" in guide_dump
    assert "合法利益评估" in guide_dump
    assert "传输影响评估" in guide_dump
    for _, _, spec in document_templates_default.DEFAULT_TEMPLATES:
        for sec in spec.get("narrative", []):
            brief = str(sec.get("brief") or "")
            assert "context.legit_basis" not in brief
            assert "context.project_facts" not in brief
            assert "context.recipient_law" not in brief
            assert "待核实" not in brief

    assert (
        documents_router._user_label(
            SimpleNamespace(display_name="林合规", email="lin@oem.example.com")
        )
        == "林合规"
    )
    assert (
        documents_router._user_label(
            SimpleNamespace(display_name="企业用户", email="lin@oem.example.com")
        )
        == "lin"
    )

    # 4) Notices diff_around slices around diff_idx; weekly digest strips breadcrumbs; admission neutral hint
    prefix = "本国个人数据跨境传输应当事先履行保护水平评估程序并报主管机关备案，" * 3
    old_snip, new_snip = notices._diff_around(
        prefix + "且保存期限不得超过一百八十日。",
        prefix + "且保存期限不得超过九十日并加密归档。",
    )
    assert old_snip != new_snip
    assert "一百八十日" in old_snip and "九十日" in new_snip

    cleaned_body = weekly_digest.strip_portal_noise(
        "首页 > 监管动态 > 正文\n摩洛哥发布车载数据出境合规指引。"
    )
    assert "首页 >" not in cleaned_body and "摩洛哥发布车载数据出境合规指引" in cleaned_body

    de_phone = fact_example("联系电话", "DE")
    de_addr = fact_example("办公地址", "DE")
    assert "+20" not in de_phone and "开罗" not in de_addr and "德国" in de_addr

    # 5) Audit failure reason in natural Chinese, password hash redacted change, structured JSON summarized
    fail_fb = audit_describe.fallback_summary(
        "change", "新建账号", "POST", "/api/admin/users", 403
    )
    assert "未通过（无操作权限）" in fail_fb
    assert "未通过（无操作权限）" in (audit_describe.humanize("新建账号未通过（状态码 403）") or "")

    dumped = audit_describe.readable(
        {
            "password_hash": "已修改",
            "blocks": [{"type": "title", "text": "标题"}, {"type": "paragraph", "text": "新段落"}],
            "risks": [{"title": "出境风险"}],
        }
    )
    assert "登录密码：已修改" in dumped
    assert '[{"type"' not in dumped
    assert "正文共" in dumped
    assert "风险评估记录" in dumped

