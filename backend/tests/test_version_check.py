"""扫描件原文核对：管理员点“识别无误 / 识别有误”后直接记下，不再经过审批。"""

from app.ingestion.models import DocumentVersion

from .conftest import bearer, login
from .review_s1_s2_contracts import make_document


async def test_the_administrator_checks_a_scanned_law_directly(client, seed, session_factory):
    async with session_factory() as s:
        _, version, _ = await make_document(s)
        version.text_source = "ocr"
        await s.commit()
        version_id = version.id
    admin = bearer(await login(client, "admin", "admin@example.com"))
    r = await client.post(f"/api/admin/versions/{version_id}/check", headers=admin, json={"approve": True})
    assert r.status_code == 200
    async with session_factory() as s:
        assert (await s.get(DocumentVersion, version_id)).review_status == "approved"
    # 审核员修改的审批页接口已撤
    assert (await client.get("/api/admin/knowledge/reviewer-changes", headers=admin)).status_code == 404



async def test_a_scan_that_reads_well_is_recorded_as_checked(session_factory):
    from app.ingestion.ocr_check import apply_recognition_check

    async with session_factory() as s:
        _, version, _ = await make_document(s)
        version.text_source = "ocr"
        await s.flush()
        assert await apply_recognition_check(s, version) is True
        assert version.review_status == "approved" and version.check_note is None  # 通过就是“已核对”，不另写结论
        assert await apply_recognition_check(s, version) is None  # 已定过的不再改


async def test_a_scan_that_does_not_read_says_which_measure_failed(session_factory):
    from app.ingestion.ocr_check import apply_recognition_check

    async with session_factory() as s:
        _, version, _ = await make_document(s, texts=["ن خات ..... رن ~ 2_ /4 ∴. >. -.ii. ~ ~ +.* Δ_ xq zzk wq"])
        version.text_source = "ocr"
        await s.flush()
        assert await apply_recognition_check(s, version) is False
        assert version.review_status == "pending_review" and version.check_note == "有 1 段文字可能识别错了"
        assert [i["state"] for i in version.check_items] == ["pending"]
        # 逐段处理：“改一下”后这一段存成校读文本，全部处理完自动记为已核对
        from app.ingestion.models import ClauseReading
        from app.ingestion.ocr_check import settle_item

        cid = version.check_items[0]["clause_id"]
        await settle_item(s, version, __import__("uuid").UUID(cid), "改正后的文字")
        assert version.review_status == "approved" and version.check_note is None
        reading = await s.scalar(__import__("sqlalchemy").select(ClauseReading))
        assert reading.reading_text == "改正后的文字" and reading.method == "manual"
