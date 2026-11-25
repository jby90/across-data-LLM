"""End-to-end regression tests for 2026-11-25 remaining meeting closures:
1. Stop-editing auto version snapshots + date-based inspection & atomic rollback (with append-only audit & CAC Word/PDF sync).
2. Multi-scenario multi-select compliance assessment + overseas admission union + mixed-material auto-splitting + dynamic 8-doc applicability preservation.
3. Opinion monitoring scheduled/manual idempotent extraction + deterministic statutory grading + real loopback SMTP multi-recipient group send & pure-Chinese error handling.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import email.policy
import io
import zipfile
from email import message_from_bytes

import pytest
from sqlalchemy import func, select

from app.admission.models import AdmissionEntry, AdmissionStep
from app.analysis import doc_suggest
from app.analysis.document_instances import DocumentEdit, DocumentInstanceVersion
from app.analysis.workflow_engine import BUSINESS_SCENES
from app.iam import service as iam_service
from app.iam.models import Role
from app.ingestion.models import Clause, Document, DocumentVersion
from app.knowledge.models import ClauseTranslation
from app.opinion.collect import (
    ensure_sources,
    grade_ungraded_deterministic,
    run_due_opinion_collect_if_needed,
)
from app.opinion.models import OpinionItem, OpinionSource
from app.sources.models import SourceSite
from tests.conftest import PASSWORD, bearer, login


class _LoopbackSmtpServer:
    """Minimal real TCP SMTP server on 127.0.0.1:0 to verify multi-recipient MIME delivery end-to-end."""

    def __init__(self, *, reject_auth: bool = False) -> None:
        self.reject_auth = reject_auth
        self.deliveries: list[dict] = []
        self._server: asyncio.AbstractServer | None = None
        self.port: int = 0

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        sock = self._server.sockets[0]
        self.port = int(sock.getsockname()[1])

    async def close(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        writer.write(b"220 127.0.0.1 ESMTP CyshuheLoopback\r\n")
        await writer.drain()
        mail_from = ""
        rcpt_tos: list[str] = []
        in_data = False
        data_lines: list[bytes] = []
        try:
            while not reader.at_eof():
                line = await reader.readline()
                if not line:
                    break
                if in_data:
                    if line in (b".\r\n", b".\n"):
                        raw_bytes = b"\r\n".join(data_lines)
                        parsed = message_from_bytes(raw_bytes, policy=email.policy.default)
                        html_part = parsed.get_body(preferencelist=("html", "plain"))
                        decoded_body = html_part.get_content() if html_part is not None else ""
                        self.deliveries.append(
                            {
                                "mail_from": mail_from,
                                "rcpt_tos": list(rcpt_tos),
                                "subject": str(parsed.get("Subject") or ""),
                                "to_header": str(parsed.get("To") or ""),
                                "raw_size": len(raw_bytes),
                                "decoded_body": decoded_body,
                            }
                        )
                        in_data = False
                        data_lines.clear()
                        writer.write(b"250 2.0.0 OK queued\r\n")
                        await writer.drain()
                    else:
                        clean_ln = line.rstrip(b"\r\n")
                        if clean_ln.startswith(b".."):
                            clean_ln = clean_ln[1:]
                        data_lines.append(clean_ln)
                    continue

                cmd = line.decode("utf-8", errors="ignore").strip()
                upper = cmd.upper()
                if upper.startswith(("EHLO", "HELO")):
                    writer.write(b"250-127.0.0.1\r\n250 AUTH PLAIN LOGIN\r\n")
                elif upper.startswith("AUTH"):
                    if self.reject_auth:
                        writer.write(b"535 5.7.8 Authentication credentials invalid\r\n")
                    else:
                        writer.write(b"235 2.7.0 Authentication successful\r\n")
                elif upper.startswith("MAIL FROM:"):
                    mail_from = cmd.split(":", 1)[1].strip().strip("<>")
                    rcpt_tos = []
                    writer.write(b"250 2.1.0 OK\r\n")
                elif upper.startswith("RCPT TO:"):
                    addr = cmd.split(":", 1)[1].strip().strip("<>")
                    rcpt_tos.append(addr)
                    writer.write(b"250 2.1.5 OK\r\n")
                elif upper == "DATA":
                    in_data = True
                    writer.write(b"354 End data with <CR><LF>.<CR><LF>\r\n")
                elif upper == "QUIT":
                    writer.write(b"221 2.0.0 Bye\r\n")
                    await writer.drain()
                    break
                else:
                    writer.write(b"250 OK\r\n")
                await writer.drain()
        finally:
            writer.close()


@pytest.mark.asyncio
async def test_document_stop_edit_versions_rollback_and_rbac(client, session_factory, seed):
    # Create a viewer in org_a to verify 4-role RBAC linkage
    async with session_factory() as s:
        await iam_service.create_user(
            s,
            email="a-viewer@example.com",
            display_name="甲车企查看员",
            password=PASSWORD,
            role=Role.VIEWER,
            org_id=seed["org_a"].id,
        )

    tok_member = await login(client, "user", "a-member@example.com")
    tok_viewer = await login(client, "user", "a-viewer@example.com")
    tok_other_org = await login(client, "user", "b-admin@example.com")

    # 1. Create a CN -> DE compliance project so risk_report uses official CAC Word template
    r_proj = await client.post(
        "/api/analysis/doc-projects",
        headers=bearer(tok_member),
        json={
            "source_country": "中国",
            "recipient_country": "德国",
            "function": "ota_diagnostics",
            "vehicle_model": "QH-E9",
            "company_name": "甲车企智能汽车有限公司",
        },
    )
    assert r_proj.status_code == 201, r_proj.text
    pid = r_proj.json()["id"]

    # 2. Save baseline document -> creates v1
    r_save = await client.post(
        f"/api/analysis/tasks/{pid}/documents/risk_report/saved?draft=true",
        headers=bearer(tok_member),
        json={},
    )
    assert r_save.status_code == 200, r_save.text
    saved_data = r_save.json()
    assert len(saved_data["versions"]) == 1
    assert saved_data["versions"][0]["version_no"] == 1
    assert saved_data["versions"][0]["is_current"] is True

    # Find first editable paragraph block
    blocks_v1 = saved_data["blocks"]
    para_idx = next(
        i
        for i, b in enumerate(blocks_v1)
        if b.get("type") == "paragraph" and str(b.get("text") or "").strip()
    )
    orig_text = blocks_v1[para_idx]["text"]
    modified_text = f"{orig_text}（第二版修订留痕验证）"

    # 3. Edit paragraph and trigger stop-edit auto-seal -> creates v2
    r_patch = await client.patch(
        f"/api/analysis/tasks/{pid}/documents/risk_report/saved",
        headers=bearer(tok_member),
        json={"block": para_idx, "text": modified_text},
    )
    assert r_patch.status_code == 200, r_patch.text

    r_seal = await client.post(
        f"/api/analysis/tasks/{pid}/documents/risk_report/versions",
        headers=bearer(tok_member),
        json={"note": "停止编辑自动存版"},
    )
    assert r_seal.status_code == 200, r_seal.text
    seal_body = r_seal.json()
    assert seal_body["created"] is True
    assert seal_body["version_no"] == 2
    assert [v["version_no"] for v in seal_body["versions"]] == [2, 1]

    # Idempotency check: sealing again without new edits does NOT create duplicate version
    r_seal_dup = await client.post(
        f"/api/analysis/tasks/{pid}/documents/risk_report/versions",
        headers=bearer(tok_member),
        json={"note": "停止编辑自动存版"},
    )
    assert r_seal_dup.status_code == 200
    assert r_seal_dup.json()["created"] is False
    assert len(r_seal_dup.json()["versions"]) == 2

    # 4. Viewer in same org can inspect version list & snapshot v1, but gets 403 on write/rollback
    r_v_list = await client.get(
        f"/api/analysis/tasks/{pid}/documents/risk_report/versions",
        headers=bearer(tok_viewer),
    )
    assert r_v_list.status_code == 200
    assert len(r_v_list.json()["versions"]) == 2

    r_v1_snap = await client.get(
        f"/api/analysis/tasks/{pid}/documents/risk_report/versions/1",
        headers=bearer(tok_viewer),
    )
    assert r_v1_snap.status_code == 200
    assert r_v1_snap.json()["blocks"][para_idx]["text"] == orig_text

    r_viewer_rb = await client.post(
        f"/api/analysis/tasks/{pid}/documents/risk_report/versions/1/rollback",
        headers=bearer(tok_viewer),
        json={},
    )
    assert r_viewer_rb.status_code == 403
    assert "查看" in r_viewer_rb.json()["detail"]

    # Cross-tenant org B gets 404
    r_other = await client.get(
        f"/api/analysis/tasks/{pid}/documents/risk_report/versions",
        headers=bearer(tok_other_org),
    )
    assert r_other.status_code == 404

    # 5. Rollback to v1 -> restores orig_text, appends compensating DocumentEdit (append-only!), seals v3, and syncs DOCX export
    async with session_factory() as s:
        edits_before = await s.scalar(select(func.count()).select_from(DocumentEdit))
        vers_before = await s.scalar(select(func.count()).select_from(DocumentInstanceVersion))

    r_rb = await client.post(
        f"/api/analysis/tasks/{pid}/documents/risk_report/versions/1/rollback",
        headers=bearer(tok_member),
        json={},
    )
    assert r_rb.status_code == 200, r_rb.text
    rb_data = r_rb.json()
    assert rb_data["blocks"][para_idx]["text"] == orig_text
    assert rb_data["versions"][0]["version_no"] == 3
    assert "回退至第 1 版" in rb_data["versions"][0]["note"]

    async with session_factory() as s:
        edits_after = await s.scalar(select(func.count()).select_from(DocumentEdit))
        vers_after = await s.scalar(select(func.count()).select_from(DocumentInstanceVersion))
    assert edits_after > edits_before
    assert vers_after == vers_before + 1

    # Verify exported Word file reflects rolled-back v1 content (no residual v2 marker)
    r_docx = await client.get(
        f"/api/analysis/tasks/{pid}/documents/risk_report/export.docx?draft=true",
        headers=bearer(tok_member),
    )
    assert r_docx.status_code == 200
    with zipfile.ZipFile(io.BytesIO(r_docx.content)) as zf:
        doc_xml = zf.read("word/document.xml").decode("utf-8", errors="ignore")
    assert "第二版修订留痕验证" not in doc_xml


@pytest.mark.asyncio
async def test_multi_scenario_multiselect_admission_union_and_mixed_text_split(
    client, session_factory, seed, monkeypatch
):
    tok_member = await login(client, "user", "a-member@example.com")

    # Seed a citable DE law + clause + translation and two distinct overseas admission entries
    async with session_factory() as s:
        site_de = SourceSite(
            country_code="DE",
            publisher="德国联邦法律门户",
            language="de",
            document_type="LAW",
            entry_url="https://www.gesetze-im-internet.de",
        )
        s.add(site_de)
        await s.flush()
        doc_de = Document(
            site_id=site_de.id,
            country_code="DE",
            language="de",
            title="Straßenverkehrsgesetz & Funkanlagengesetz",
            title_zh="德国道路交通法与无线电设备法",
            document_type="LAW",
            instrument_kind="PRIMARY",
            relevance="relevant",
            source_url="https://www.gesetze-im-internet.de/stvg/index.html",
        )
        s.add(doc_de)
        await s.flush()
        ver_de = DocumentVersion(
            document_id=doc_de.id,
            version_no=1,
            state="parsed",
            legal_status="in_force",
            review_status="approved",
            content_hash="a" * 64,
            archive_path="test/de_stvg.html",
            size_bytes=1024,
            mime_type="text/html",
            final_url="https://www.gesetze-im-internet.de/stvg/index.html",
        )
        s.add(ver_de)
        await s.flush()
        cl_de = Clause(
            version_id=ver_de.id,
            ordinal=1,
            clause_number="§ 1f",
            original_text="Der Betrieb eines Kraftfahrzeugs mit autonomer Fahrfunktion bedarf einer Betriebserlaubnis des Kraftfahrt-Bundesamtes.",
            text_hash="b" * 64,
            language="de",
        )
        s.add(cl_de)
        await s.flush()
        s.add(
            ClauseTranslation(
                clause_id=cl_de.id,
                engine="llm",
                translated_text="具有自动驾驶功能的机动车的运行须取得德国联邦机动车运输管理局（KBA）颁发的运行许可。",
                model_name="test",
                prompt_version="v1",
                is_current=True,
                quality_check_passed=True,
                review_status="approved",
            )
        )
        adm_telecom = AdmissionEntry(
            country_code="DE",
            name_zh="德国无线电与车载网联终端合规声明",
            category="telecom_approval",
            authority_zh="德国联邦网络管理局（BNetzA）",
            summary_zh="车载T-Box与OTA无线通信模块合规准入",
            evidence_clause_id=cl_de.id,
            evidence_quote=cl_de.original_text,
            review_status="approved",
            functions=["ota_diagnostics", "connected_vehicle_cloud"],
            profile={
                "applies_to": "车载通信终端",
                "portal": {"site": "https://www.bundesnetzagentur.de"},
            },
        )
        adm_av = AdmissionEntry(
            country_code="DE",
            name_zh="德国自动驾驶道路测试特别运行许可",
            category="av_testing",
            authority_zh="德国联邦机动车运输管理局（KBA）",
            summary_zh="L3/L4自动驾驶测试车辆在德国公共道路运行许可",
            evidence_clause_id=cl_de.id,
            evidence_quote=cl_de.original_text,
            review_status="approved",
            functions=["autonomous_trial"],
            profile={
                "applies_to": "自动驾驶测试车辆",
                "portal": {"site": "https://www.kba.de"},
            },
        )
        s.add_all([adm_telecom, adm_av])
        await s.flush()
        s.add_all(
            [
                AdmissionStep(
                    entry_id=adm_telecom.id,
                    seq=1,
                    title_zh="提交车载通信模块符合性声明",
                    detail_zh="向德国联邦网络管理局提交无线电设备技术文档与符合性声明。",
                    evidence_clause_id=cl_de.id,
                    evidence_quote=cl_de.original_text,
                ),
                AdmissionStep(
                    entry_id=adm_av.id,
                    seq=1,
                    title_zh="递交自动驾驶道路测试许可申请",
                    detail_zh="向德国联邦机动车运输管理局提交安全概念书与测试路线方案。",
                    evidence_clause_id=cl_de.id,
                    evidence_quote=cl_de.original_text,
                ),
            ]
        )
        await s.commit()

    # 1. Single-scenario project (ota_diagnostics only) -> only needs telecom entry, NOT autonomous road test
    r_single = await client.post(
        "/api/analysis/doc-projects",
        headers=bearer(tok_member),
        json={
            "source_country": "中国",
            "recipient_country": "德国",
            "function": "ota_diagnostics",
            "vehicle_model": "QH-S7",
        },
    )
    assert r_single.status_code == 201
    single_pid = r_single.json()["id"]

    # 2. Multi-scenario project (ota_diagnostics + autonomous_trial + sales_service) -> unions BOTH admission entries!
    two_scenes = list(BUSINESS_SCENES.keys())[:2]
    r_multi = await client.post(
        "/api/analysis/doc-projects",
        headers=bearer(tok_member),
        json={
            "source_country": "中国",
            "recipient_country": "德国",
            "functions": ["ota_diagnostics", "autonomous_trial", "sales_service"],
            "business_scenes": two_scenes,
            "vehicle_model": "QH-S9",
        },
    )
    assert r_multi.status_code == 201, r_multi.text
    multi = r_multi.json()
    multi_pid = multi["id"]
    assert multi["route"]["functions"] == ["ota_diagnostics", "autonomous_trial", "sales_service"]
    assert multi["route"]["function"] == "ota_diagnostics"
    assert multi["route"]["business_scenes"] == two_scenes
    assert "远程诊断与OTA、自动驾驶测试等3项" in multi["title"]

    # Verify Admission Plan API unions required items across all selected scenarios
    r_plan_single = await client.get(
        f"/api/admission/plan?task_id={single_pid}", headers=bearer(tok_member)
    )
    r_plan_multi = await client.get(
        f"/api/admission/plan?task_id={multi_pid}", headers=bearer(tok_member)
    )
    assert r_plan_single.status_code == 200
    assert r_plan_multi.status_code == 200
    single_needed_names = {x["name_zh"] for x in r_plan_single.json()["needed"]}
    multi_needed_names = {x["name_zh"] for x in r_plan_multi.json()["needed"]}
    assert "德国自动驾驶道路测试特别运行许可" not in single_needed_names
    assert {
        "德国无线电与车载网联终端合规声明",
        "德国自动驾驶道路测试特别运行许可",
    }.issubset(multi_needed_names)

    # 3. Dynamic 8-doc applicability preservation:
    # Setting lawful_basis to contract (not legitimate interests) -> LIA becomes 'na', while DPIA/TIA stay 'need'
    r_ans = await client.put(
        f"/api/analysis/doc-projects/{multi_pid}/answers",
        headers=bearer(tok_member),
        json={
            "answers": {
                "data_volume": "约 5 万人",
                "sensitive_pi": "否",
                "important_data": "否",
                "is_cii": "否",
                "lawful_basis": "履行合同所必需及取得个人单独同意，不以合法利益为依据",
                "issues": "无",
            }
        },
    )
    assert r_ans.status_code == 200
    app_states = {k: v["state"] for k, v in r_ans.json()["applicability"].items()}
    assert app_states["lia"] == "na"
    assert app_states["remediation"] == "na"
    assert app_states["declaration"] == "na"  # CN standard contract route does not need security assessment declaration
    assert app_states["contract"] == "need"
    assert app_states["dpia"] == "need"
    assert app_states["tia"] == "need"
    assert app_states["admission"] == "need"

    # 4. Unified comprehensive material mixed-text auto-splitting & multi-span quote verification
    mixed_text = (
        "本公司在德国同步开展远程故障诊断OTA升级、自动驾驶道路测试及智能座舱语音服务。"
        "远程诊断采集电控故障码与软件版本号；自动驾驶路测采集车外脱敏感知图像（车端已隐去人脸车牌）；"
        "智能座舱处理驾驶员唤醒词语音指令。上述数据经双向鉴权加密通道传输至法兰克福受控机房保存 2 年。"
    )
    detected = doc_suggest.detect_functions_from_text(mixed_text)
    detected_keys = [k for k, _ in detected]
    assert "ota_diagnostics" in detected_keys
    assert "autonomous_trial" in detected_keys
    assert "cockpit_biometrics" in detected_keys

    class _FakeLlmRes:
        prompt_tokens = 100
        completion_tokens = 80
        data = {
            "answers": [
                {
                    "key": "data_types",
                    "value": "（1）远程诊断与OTA：电控故障码与软件版本号；（2）自动驾驶路测：车外脱敏感知图像；（3）智能座舱：唤醒词语音指令",
                    "source": "text",
                    "quote": "远程诊断采集电控故障码与软件版本号……自动驾驶路测采集车外脱敏感知图像……智能座舱处理驾驶员唤醒词语音指令",
                },
                {
                    "key": "retention_and_blackbox",
                    "value": "存放于法兰克福受控机房，保存期限为 2 年",
                    "source": "text",
                    "quote": "传输至法兰克福受控机房保存 2 年",
                },
            ]
        }

    async def _fake_llm(*_a, **_kw):
        return _FakeLlmRes()

    monkeypatch.setattr(doc_suggest, "call_llm_json", _fake_llm)
    drafted, _ = await doc_suggest.draft(
        [
            {"key": "data_types", "label": "数据类型", "question": "传输哪些数据", "allow_general": False},
            {"key": "retention_and_blackbox", "label": "保存期限", "question": "保存多久", "allow_general": False},
        ],
        {"业务": "远程诊断与OTA"},
        mixed_text,
    )
    assert "（1）远程诊断与OTA" in drafted["data_types"]["text"]
    assert "（3）智能座舱" in drafted["data_types"]["text"]
    assert "2 年" in drafted["retention_and_blackbox"]["text"]


@pytest.mark.asyncio
async def test_opinion_scheduled_extract_and_real_loopback_multi_recipient_smtp(
    client, session_factory, seed
):
    tok_admin = await login(client, "admin", "admin@example.com")

    # 1. Seed ungraded automotive data compliance opinion items and verify deterministic statutory grading
    today = dt.date.today()
    now_ts = dt.datetime.now(dt.UTC)
    async with session_factory() as s:
        await ensure_sources(s)
        for existing_src in (await s.scalars(select(OpinionSource))).all():
            existing_src.last_run_at = now_ts
            existing_src.last_status = "success"
            existing_src.last_note = "已巡检"
        src = await s.scalar(select(OpinionSource).limit(1))
        await s.flush()
        s.add_all(
            [
                OpinionItem(
                    source_id=src.id,
                    publisher="国家互联网信息办公室",
                    kind="regulator",
                    url="https://www.cac.gov.cn/2026-11/24/c_1001.htm",
                    title="关于加强智能网联汽车数据出境与车外人脸车牌视频安全管理的通知",
                    published_on=today,
                    body="网信部门发布汽车数据安全管理通知，明确智能网联汽车向境外提供数据及处理车外影像人脸车牌须严格履行安全评估与车端脱敏要求。",
                    content_hash="h1001",
                    matched_keywords=["汽车数据", "数据出境", "人脸"],
                ),
                OpinionItem(
                    source_id=src.id,
                    publisher="工业和信息化部",
                    kind="regulator",
                    url="https://www.miit.gov.cn/2026-11/24/c_1002.htm",
                    title="智能网联汽车软件升级（OTA）与车联网网络安全标准发布",
                    published_on=today,
                    body="工业和信息化部今日正式发布智能网联汽车软件升级（OTA）与车联网信息安全强制性国家标准。",
                    content_hash="h1002",
                    matched_keywords=["智能网联", "OTA", "网络安全"],
                ),
            ]
        )
        await s.commit()

    graded_n = await grade_ungraded_deterministic(session_factory)
    assert graded_n == 2

    # Scheduled idempotency check: source ran just now, so force=False must skip network fetch cleanly
    sched_res = await run_due_opinion_collect_if_needed(
        session_factory, lambda: None, force=False, interval_hours=24
    )
    assert sched_res["ran"] is False

    # 2. Start real loopback TCP SMTP server and test multi-recipient group email delivery
    smtp_srv = _LoopbackSmtpServer()
    await smtp_srv.start()
    try:
        rcpts = [
            "compliance@oem-test.com",
            "legal@oem-test.com",
            "dpo@oem-test.com",
        ]
        secret_pwd = "SuperSecretMailToken_9981!"

        # Save weekly digest config with loopback host/port and 3 recipients
        r_cfg = await client.put(
            "/api/admin/opinion/digest-config",
            headers=bearer(tok_admin),
            json={
                "enabled": True,
                "weekday": 1,
                "hour_utc": 1,
                "lookback_days": 7,
                "min_level": "low",
                "recipients": rcpts,
                "smtp_host": "127.0.0.1",
                "smtp_port": smtp_srv.port,
                "smtp_user": "mailer@cyshuhe.local",
                "smtp_from": "digest@cyshuhe.local",
            },
        )
        assert r_cfg.status_code == 200, r_cfg.text

        # Execute internal verification email test -> must deliver to ALL 3 recipients & never echo password
        r_test = await client.post(
            "/api/admin/opinion/digest-test",
            headers=bearer(tok_admin),
            json={
                "recipients": rcpts,
                "smtp_host": "127.0.0.1",
                "smtp_port": smtp_srv.port,
                "smtp_user": "mailer@cyshuhe.local",
                "smtp_from": "digest@cyshuhe.local",
                "smtp_password": secret_pwd,
            },
        )
        assert r_test.status_code == 200, r_test.text
        test_body = r_test.json()
        assert test_body["sent"] is True
        assert test_body["recipient_count"] == 3
        assert secret_pwd not in r_test.text
        assert "SMTP" not in test_body["message"]
        assert len(smtp_srv.deliveries) == 1
        assert smtp_srv.deliveries[0]["rcpt_tos"] == rcpts
        assert "c_1001.htm" in smtp_srv.deliveries[0]["decoded_body"]

        # Execute formal weekly digest send -> delivers over SMTP + syncs portal run
        r_send = await client.post(
            "/api/admin/opinion/digest-send",
            headers=bearer(tok_admin),
            json={"recipients": rcpts},
        )
        assert r_send.status_code == 200, r_send.text
        send_body = r_send.json()
        assert send_body["delivery_mode"] == "smtp"
        assert send_body["status"] == "sent"
        assert "SMTP" not in (send_body["note"] or "")
        assert len(smtp_srv.deliveries) == 2
        assert smtp_srv.deliveries[1]["rcpt_tos"] == rcpts
    finally:
        await smtp_srv.close()

    # 3. After closing SMTP server, verify failure messages are 100% pure Chinese (zero engineering jargon)
    r_fail = await client.post(
        "/api/admin/opinion/digest-test",
        headers=bearer(tok_admin),
        json={
            "recipients": ["compliance@oem-test.com"],
            "smtp_host": "127.0.0.1",
            "smtp_port": smtp_srv.port,
        },
    )
    assert r_fail.status_code == 502
    err_detail = r_fail.json()["detail"]
    assert "发信邮件服务器" in err_detail
    for forbidden in ("SMTP", "Traceback", "ConnectionRefused", "Errno", "Exception"):
        assert forbidden not in err_detail
