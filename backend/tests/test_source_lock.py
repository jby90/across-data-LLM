import pytest

from app.ingestion.source_lock import (
    OFFICIAL_PUBLISHERS,
    SourceNotOfficial,
    is_official_host,
    publisher_of,
    require_official,
)


@pytest.mark.parametrize(
    ("url", "country"),
    [
        ("https://pdpc.gov.eg/portal/cms/fs/media/x.pdf", "EG"),
        ("https://assets.egazette.gov.sg/2021/a.pdf", "SG"),
        ("https://www.bdl.gov.lb/laws/Law_81.pdf", "LB"),
        ("https://www.justice.gov.tn/x.pdf", "TN"),
        ("https://www.cndp.ma/wp-content/uploads/Loi.pdf", "MA"),
        ("https://www.dgssi.gov.ma/sites/default/files/x.pdf", "MA"),
        ("https://www.ins.tn/files/Loi.pdf", "TN"),
    ],
)
def test_listed_publishers_are_accepted_and_the_basis_names_them(url, country):
    assert is_official_host(url)
    assert publisher_of(url).country == country
    assert require_official(url).startswith("官方站点（")


@pytest.mark.parametrize(
    "url",
    [
        "https://nhrclb.org/archives/8228",
        "https://www.scribd.com/document/1/Loi-53-05",
        "https://www.cliffordchance.com/briefings/x.pdf",
        "https://gov.example.com.evil.net/x.pdf",
        "https://evilgov.eg/x.pdf",
        "https://pdpc.gov.eg.evil.net/x.pdf",  # 名单内域名只能作后缀，不能被当前缀借用
        "https://www.service-public.gouv.fr/x.pdf",  # 别国政府站：有 gouv 标签也不是这些国家的发布机构
        "https://www.moj.gov.xx/x.pdf",  # 任何未登记的 gov 站
        "not a url",
        "",
    ],
)
def test_unlisted_hosts_are_refused_until_someone_confirms(url):
    assert not is_official_host(url)
    with pytest.raises(SourceNotOfficial):
        require_official(url)


def test_confirmation_must_name_who_confirmed_and_is_recorded():
    url = "https://nhrclb.org/archives/8228"
    assert "法务" in require_official(url, "法务张三")
    with pytest.raises(SourceNotOfficial):
        require_official(url, "   ")


def test_every_publisher_entry_is_complete_and_unique():
    hosts = [p.host for p in OFFICIAL_PUBLISHERS]
    assert len(hosts) == len(set(hosts))
    assert all(p.name_zh and p.country and "." in p.host for p in OFFICIAL_PUBLISHERS)


def test_chinese_official_sources_for_opinion_are_listed_but_not_every_gov_cn_site():
    from app.ingestion.source_lock import publisher_of

    for url in (
        "https://www.cac.gov.cn/2026-10/01/c_1.htm",
        "https://www.miit.gov.cn/xwfb/index.html",
        "https://www.samr.gov.cn/",
        "http://www.caam.org.cn/",
        "https://www.news.cn/auto/",
        "https://www.gov.cn/zhengce/",
    ):
        assert publisher_of(url) is not None, url
    assert publisher_of("https://some-county.gov.cn/news/1.html") is None
    assert publisher_of("https://www.example-news.com.cn/") is None


def test_the_official_law_portals_of_germany_japan_and_korea_are_recognised_and_lookalikes_are_not():
    from app.ingestion.source_lock import publisher_of

    assert publisher_of("https://www.gesetze-im-internet.de/bdsg_2018/").country == "DE"
    assert publisher_of("https://laws.e-gov.go.jp/api/2/laws").country == "JP"
    assert publisher_of("https://www.law.go.kr/LSW/lsInfoP.do?lsId=011357").country == "KR"
    assert publisher_of("https://gesetze-im-internet.de.evil.example/") is None
    assert publisher_of("https://notlaw.go.kr.example.com/") is None


def test_international_standards_bodies_are_recognised_and_lookalikes_are_not():
    from app.ingestion.source_lock import publisher_of

    assert publisher_of("https://www.iso.org/ics/43.040.15/x/").country == "*"
    assert publisher_of("https://unece.org/transport/vehicle-regulations").country == "*"
    assert publisher_of("https://iso.org.evil.example/") is None
    assert publisher_of("https://notunece.org/") is None
