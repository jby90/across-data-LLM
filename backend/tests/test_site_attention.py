"""站点提醒：自动重试仍无效才提醒管理员；已明确不采集或主动停用的不打扰。"""

from datetime import UTC, datetime, timedelta

from app.sources.attention import FAILURES_BEFORE_REMINDER, attention_of

NOW = datetime(2026, 10, 31, 12, tzinfo=UTC)


def _site(**kw):
    base = dict(
        enabled=True,
        terms_status="approved",
        consecutive_failures=0,
        last_run_at=NOW - timedelta(hours=1),
        interval_hours=72,
        now=NOW,
    )
    return attention_of(**{**base, **kw})


def test_a_healthy_site_raises_nothing():
    assert _site() is None


def test_a_registered_site_waits_for_the_terms_review_before_monitoring():
    assert "审核" in _site(enabled=False, terms_status="pending", last_run_at=None)


def test_a_blocked_site_is_retried_first_and_reminded_only_after_repeated_failure():
    assert _site(consecutive_failures=FAILURES_BEFORE_REMINDER - 1) is None
    assert "自动重试" in _site(consecutive_failures=FAILURES_BEFORE_REMINDER)


def test_a_site_that_stopped_updating_is_flagged_after_two_cycles():
    assert _site(last_run_at=NOW - timedelta(hours=2 * 72 - 1)) is None
    assert "两个监测周期" in _site(last_run_at=NOW - timedelta(hours=2 * 72 + 1))


def test_rejected_or_deliberately_disabled_sites_are_left_alone():
    assert _site(terms_status="rejected", enabled=False) is None
    assert _site(enabled=False, consecutive_failures=9) is None


async def test_the_sites_list_separates_monitored_sites_from_one_off_import_records(
    client, seed, session_factory
):
    from app.sources.models import SourceSite

    from .conftest import bearer, login

    async with session_factory() as s:
        s.add_all(
            [
                SourceSite(
                    country_code="SG",
                    publisher="入口页",
                    language="en",
                    document_type="OTHER",
                    entry_url="https://www.csa.gov.sg/legislation/",
                    strategy="INDEX_PAGE",
                ),
                SourceSite(
                    country_code="SG",
                    publisher="单份文件",
                    language="en",
                    document_type="LAW",
                    entry_url="https://assets.egazette.gov.sg/2024/a.pdf",
                    strategy="DIRECT",
                ),
            ]
        )
        await s.commit()
    admin = bearer(await login(client, "admin", "admin@example.com"))

    async def urls(scope):
        r = await client.get(f"/api/admin/sites?scope={scope}", headers=admin)
        assert r.status_code == 200, r.text
        return {x["entry_url"] for x in r.json()}

    monitored, imports, everything = await urls("monitor"), await urls("imports"), await urls("all")
    assert monitored == {"https://www.csa.gov.sg/legislation/"}
    assert imports == {"https://assets.egazette.gov.sg/2024/a.pdf"}
    assert everything == monitored | imports
    default = await client.get("/api/admin/sites", headers=admin)
    assert {x["entry_url"] for x in default.json()} == monitored  # 默认只给要定期看的
