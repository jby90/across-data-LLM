"""舆情采集：只读已确认的官方来源；标题与正文都要在主题内；事实必须有原句；等级由规则表定。"""

from dataclasses import dataclass

import pytest
from sqlalchemy import select

from app.opinion.collect import (
    collect_source,
    ensure_sources,
    extract_links,
    grade_items,
    looks_relevant,
    parse_article,
)
from app.opinion.extract import facts_from_model
from app.opinion.grading import HIGH, LOW, grade
from app.opinion.models import OpinionItem, OpinionSource

BODY = (
    "为规范汽车数据处理活动，保护个人信息权益，国家网信部门会同有关部门发布通知，要求汽车数据处理者"
    "对车外摄像头采集的人脸和车牌信息在车内完成脱敏处理，未经评估不得向境外提供重要数据。"
) * 2
PAGE = f"""<html><head><title>x</title><meta name="ArticleTitle" content="汽车数据出境新规发布">
<meta name="PubDate" content="2026-10-20 09:30"></head><body><nav>导航</nav>
<div class="TRS_Editor"><p>{BODY}</p></div><footer>版权</footer></body></html>"""
SECTION = """<html><body>
<a href="/2026-10/20/c_1001.htm">汽车数据出境新规发布并开始实施征求意见</a>
<a href="https://www.cac.gov.cn/2026-10/19/c_1002.htm">习近平会见外国客人并举行会谈活动</a>
<a href="https://evil.example.com/2026/a.html">汽车数据出境新规发布并开始实施征求意见</a>
<a href="/about.html">关于我们</a>
<a href="/2026-10/20/c_1001.htm">汽车数据出境新规发布并开始实施征求意见</a>
</body></html>"""


def test_only_article_links_on_the_same_listed_publisher_are_taken_once():
    links = extract_links(SECTION, "https://www.cac.gov.cn/")
    assert [link.url for link in links] == [
        "https://www.cac.gov.cn/2026-10/20/c_1001.htm",
        "https://www.cac.gov.cn/2026-10/19/c_1002.htm",
    ]
    assert [looks_relevant(link.title) for link in links] == [True, False]


def test_an_article_on_topic_is_read_with_title_date_and_clean_text():
    art = parse_article(PAGE, "https://www.cac.gov.cn/2026-10/20/c_1001.htm")
    assert art is not None and art.title == "汽车数据出境新规发布"
    assert str(art.published_on) == "2026-10-20"
    assert "导航" not in art.body and "版权" not in art.body
    assert {"汽车", "出境"} <= set(art.matched)


@pytest.mark.parametrize(
    "text",
    [
        "东风汽车与宁德时代签署战略合作协议，共同推进新能源汽车产业发展，双方将在电池研发、整车制造等方面深度合作。"
        * 3,
        "国家主席会见外国领导人并就双边关系交换意见，双方一致同意加强各领域合作。" * 6,
        "短文",
    ],
)
def test_a_pure_business_item_a_foreign_affairs_item_or_a_stub_is_not_taken(text):
    page = f'<html><head><meta name="ArticleTitle" content="一则消息"></head><body><p>{text}</p></body></html>'
    assert parse_article(page, "https://www.news.cn/auto/20261001/x/c.html") is None


def test_a_claim_is_kept_only_when_its_sentence_is_in_the_article():
    body = "某车企承认约 12 万名车主的人脸和车牌图像被传至境外服务器。监管部门已约谈该企业。"
    data = {
        "facts": {
            "outside_video_or_plates": {
                "value": True,
                "quote": "约 12 万名车主的人脸和车牌图像被传至境外服务器",
            },
            "crosses_border": {"value": True, "quote": "传至境外服务器"},
            "harm_happened": {"value": True, "quote": "监管部门已约谈该企业"},
            "policy_signal": {"value": True, "quote": "文章里根本没有的一句话"},
            "people": {"value": 120000, "quote": "约 12 万名车主的人脸和车牌图像被传至境外服务器"},
        }
    }
    facts = facts_from_model(data, body)
    assert facts.outside_video_or_plates and facts.harm_happened
    assert not facts.policy_signal  # 引用的句子不在文中，不算
    assert facts.people == 120000  # “12 万”在句子里，换算后一致
    assert grade(facts).level == HIGH


def test_a_head_count_the_article_does_not_state_is_dropped():
    body = "某企业在境外存放了大量车辆数据，涉及大量用户。监管部门表示将加强监督检查工作。"
    data = {"facts": {"people": {"value": 5000000, "quote": "涉及大量用户"}}}
    facts = facts_from_model(data, body)
    assert facts.people is None and grade(facts).level == LOW


def test_the_model_cannot_set_the_grade_by_saying_so():
    data = {"level": "high", "facts": {}}
    assert (
        grade(facts_from_model(data, "一篇普通的汽车技术讨论文章，介绍座椅舒适性调校方法。")).level
        == LOW
    )


@dataclass
class _Res:
    url: str
    content: bytes


class _Fetcher:
    def __init__(self, pages):
        self.pages, self.calls = pages, []

    async def get(self, url):
        self.calls.append(url)
        if url not in self.pages:
            raise RuntimeError("blocked")
        return _Res(url, self.pages[url].encode())


async def test_a_run_stores_new_on_topic_articles_once_and_reports_what_it_did(session_factory):
    async with session_factory() as s:
        added = await ensure_sources(s)
        assert added >= 8 and await ensure_sources(s) == 0  # 重复运行不重复建
        source = await s.scalar(
            select(OpinionSource).where(OpinionSource.entry_url == "https://www.cac.gov.cn/")
        )
    fetcher = _Fetcher(
        {
            "https://www.cac.gov.cn/": SECTION,
            "https://www.cac.gov.cn/2026-10/20/c_1001.htm": PAGE,
        }
    )
    first = await collect_source(session_factory, fetcher, source)
    assert (first.kept, first.already, first.rejected) == (1, 0, 0)
    again = await collect_source(session_factory, fetcher, source)
    assert (again.kept, again.already) == (0, 1)
    async with session_factory() as s:
        item = (await s.scalars(select(OpinionItem))).one()
        assert item.publisher == "国家互联网信息办公室" and item.level is None
        refreshed = await s.get(OpinionSource, source.id)
        assert refreshed.last_status == "success" and "新增" in refreshed.last_note


async def test_a_source_that_cannot_be_read_is_reported_as_failed(session_factory):
    async with session_factory() as s:
        await ensure_sources(s)
        source = await s.scalar(select(OpinionSource).limit(1))
    stats = await collect_source(session_factory, _Fetcher({}), source)
    assert stats.pages == 0 and stats.errors == 1
    async with session_factory() as s:
        assert (await s.get(OpinionSource, source.id)).last_status == "failed"


async def test_grading_stores_the_level_the_rules_and_the_sentences(session_factory, monkeypatch):
    from app.opinion import collect as C

    async def fake_read(title, body):
        return facts_from_model(
            {
                "facts": {
                    "outside_video_or_plates": {
                        "value": True,
                        "quote": "对车外摄像头采集的人脸和车牌信息在车内完成脱敏处理",
                    }
                }
            },
            f"{title}\n{body}",
        )

    monkeypatch.setattr("app.opinion.extract.read_facts", fake_read)
    async with session_factory() as s:
        s.add(
            OpinionItem(
                publisher="国家互联网信息办公室",
                kind="regulator",
                url="https://www.cac.gov.cn/2026-10/20/c_1001.htm",
                title="汽车数据出境新规发布",
                body=BODY,
                content_hash="x" * 64,
            )
        )
        await s.commit()
    stats = await grade_items(session_factory)
    assert stats == {"graded": 1, "unavailable": 0}
    async with session_factory() as s:
        item = (await s.scalars(select(OpinionItem))).one()
    assert item.level == "high" and "outside-video" in item.rules and item.bases
    assert item.facts["evidence"]["outside_video_or_plates"].startswith("对车外摄像头")
    assert C.grade_items  # 公共入口存在


def test_a_sales_or_member_count_is_not_a_head_count_of_people_whose_data_is_handled():
    body = "截至目前，该系列已收获超20万高净值用户认可，实现了销量、口碑、质量的全方位领先。"
    data = {"facts": {"people": {"value": 200000, "quote": body}}}
    facts = facts_from_model(data, body)
    assert facts.people is None and grade(facts).level == LOW


def test_a_head_count_in_a_data_handling_sentence_counts():
    body = "调查显示，该企业未经同意向境外传输了约 30 万名车主的个人信息，涉及行驶轨迹。"
    quote = "该企业未经同意向境外传输了约 30 万名车主的个人信息"
    facts = facts_from_model({"facts": {"people": {"value": 300000, "quote": quote}}}, body)
    assert facts.people == 300000 and grade(facts).level == HIGH


def test_a_regulatory_move_unrelated_to_data_or_driving_does_not_raise_the_grade():
    body = "今年7月正式实施的《电动汽车用动力蓄电池安全要求》，将对动力电池要求从被动防护转向主动安全。"
    facts = facts_from_model(
        {
            "facts": {
                "policy_signal": {
                    "value": True,
                    "quote": "今年7月正式实施的《电动汽车用动力蓄电池安全要求》",
                }
            }
        },
        body,
    )
    assert not facts.policy_signal and grade(facts).level == LOW


def test_a_data_security_rule_is_still_a_medium_signal():
    body = "工业和信息化部发布汽车数据安全管理的征求意见稿，要求企业落实数据分类分级保护措施。"
    quote = "工业和信息化部发布汽车数据安全管理的征求意见稿"
    facts = facts_from_model({"facts": {"policy_signal": {"value": True, "quote": quote}}}, body)
    assert facts.policy_signal and grade(facts).level == "medium"


def test_sections_are_found_from_short_navigation_labels_on_the_same_publisher():
    from app.opinion.collect import extract_sections

    home = """<html><body>
    <a href="/zcfg/index.html">政策法规</a><a href="/wlaq/index.html">网络安全</a>
    <a href="https://www.cac.gov.cn/tzgg/index.html"> 通知 公告 </a>
    <a href="https://evil.example.com/zc/index.html">政策法规</a>
    <a href="/2026-10/20/c_1001.htm">这是一篇很长的文章标题包含政策法规字样的文章</a>
    <a href="/about/index.html">关于我们</a><a href="/">首页新闻</a>
    </body></html>"""
    found = extract_sections(home, "https://www.cac.gov.cn/")
    assert [s.url for s in found] == [
        "https://www.cac.gov.cn/zcfg/index.html",
        "https://www.cac.gov.cn/wlaq/index.html",
        "https://www.cac.gov.cn/tzgg/index.html",
    ]


async def test_a_run_also_reads_the_sections_the_home_page_points_to(session_factory):
    async with session_factory() as s:
        await ensure_sources(s)
        source = await s.scalar(
            select(OpinionSource).where(OpinionSource.entry_url == "https://www.cac.gov.cn/")
        )
    home = '<html><body><a href="/wlaq/index.html">网络安全</a></body></html>'
    section = '<html><body><a href="/2026-10/20/c_1001.htm">汽车数据出境新规发布并开始实施征求意见</a></body></html>'
    fetcher = _Fetcher(
        {
            "https://www.cac.gov.cn/": home,
            "https://www.cac.gov.cn/wlaq/index.html": section,
            "https://www.cac.gov.cn/2026-10/20/c_1001.htm": PAGE,
        }
    )
    stats = await collect_source(session_factory, fetcher, source)
    assert stats.pages == 2 and stats.kept == 1


async def test_the_admin_api_lists_items_high_first_with_reasons_and_hides_nothing_else(
    client, seed, session_factory
):
    from .conftest import bearer, login

    async with session_factory() as s:
        for n, (level, bases) in enumerate(
            [("low", []), ("high", ["《数据安全法》…"]), ("medium", ["监管动向"]), (None, [])]
        ):
            s.add(
                OpinionItem(
                    publisher="工业和信息化部",
                    kind="regulator",
                    url=f"https://www.miit.gov.cn/xwfb/art/{n}.html",
                    title=f"汽车数据资讯 {n}",
                    body="正文" * 80,
                    content_hash=str(n) * 64,
                    level=level,
                    bases=bases,
                    facts={"evidence": {"harm_happened": "已被监管部门约谈"}}
                    if level == "high"
                    else {},
                )
            )
        await s.commit()
    admin = bearer(await login(client, "admin", "admin@example.com"))
    rows = (await client.get("/api/admin/opinion/items", headers=admin)).json()
    assert [r["level_label"] for r in rows] == ["高", "中", "低", "待分级"]
    assert rows[0]["reasons"] == ["《数据安全法》…"]
    only_high = (await client.get("/api/admin/opinion/items?level=high", headers=admin)).json()
    assert len(only_high) == 1
    detail = (await client.get(f"/api/admin/opinion/items/{rows[0]['id']}", headers=admin)).json()
    assert detail["evidence"] == ["已被监管部门约谈"] and detail["excerpt"]
    summary = (await client.get("/api/admin/opinion/summary", headers=admin)).json()
    assert summary["total"] == 4
    member = bearer(await login(client, "user", "a-member@example.com"))
    assert (await client.get("/api/admin/opinion/items", headers=member)).status_code in (401, 403)


def test_a_high_or_medium_label_without_a_real_quotation_is_not_accepted():
    from app.opinion.labeling import _valid

    body = "某企业被监管部门约谈，要求整改数据出境问题。"
    ok = {"level": "high", "rule": "H4", "quote": "某企业被监管部门约谈，要求整改", "reason": "x"}
    assert _valid(ok, body)["level"] == "high"
    invented = {**ok, "quote": "文章里根本没有的一句话内容"}
    assert _valid(invented, body) is None
    assert _valid({**ok, "quote": ""}, body) is None
    assert _valid({"level": "low", "rule": "L", "quote": ""}, body)["level"] == "low"
    assert _valid({"level": "critical", "quote": "某企业被监管部门约谈"}, body) is None


async def test_labelling_resumes_and_agreement_decides_whether_a_ruling_is_needed(
    session_factory, tmp_path, monkeypatch
):
    import json

    from app.opinion import labeling
    from app.opinion.evaluate import label_all

    answers = {"model-a": "high", "model-b": "high", "judge": "medium"}
    calls = {"rule": 0}

    async def fake_label(model, title, body, **_):
        level = answers[model]
        if "分歧" in title:
            level = "high" if model == "model-a" else "low"
        return {"level": level, "rule": "H4", "quote": "某句话原文", "reason": ""}

    async def fake_rule(model, title, body, a, b):
        calls["rule"] += 1
        return {"level": "medium", "rule": "M3", "quote": "某句话原文", "reason": ""}

    monkeypatch.setattr(labeling, "label_with", fake_label)
    monkeypatch.setattr(labeling, "rule_between", fake_rule)
    async with session_factory() as s:
        for n, title in enumerate(["一致的资讯", "有分歧的资讯"]):
            s.add(
                OpinionItem(
                    publisher="人民网",
                    kind="media",
                    url=f"https://www.people.com.cn/n/{n}.html",
                    title=title,
                    body="正文" * 80,
                    content_hash=str(n) * 64,
                )
            )
        await s.commit()
    out = tmp_path / "labels.jsonl"
    stats = await label_all(
        session_factory, out, model_a="model-a", model_b="model-b", model_judge="judge"
    )
    assert stats == {"labelled": 2, "agreed": 1, "ruled": 1, "failed": 0}
    rows = {r["title"]: r for r in map(json.loads, out.read_text("utf-8").splitlines())}
    assert rows["一致的资讯"]["final"] == "high" and rows["一致的资讯"]["agreed"]
    assert rows["有分歧的资讯"]["final"] == "medium" and not rows["有分歧的资讯"]["agreed"]
    assert calls["rule"] == 1
    again = await label_all(
        session_factory, out, model_a="model-a", model_b="model-b", model_judge="judge"
    )
    assert again["labelled"] == 0 and calls["rule"] == 1  # 续跑不重复标注、不重复裁决


SHELL = (
    '<html><body><div><script id="u1" src="/cms_files/default/script/AuthorizedRead/unitbuild.js"'
    ' webId="w1" parseType="buildstatic" url="/api-gateway/jpaas-publish-server/front/page/build/unit"'
    " queryData=\"{'parseType':'buildstatic','webId':'w1','pageId':'p1'}\"></script></div></body></html>"
)
FRAGMENT = (
    '{"success":true,"data":{"html":"<ul><li><a href=\\"/zw/zh/art/2026/art_1.html\\">'
    '广汽本田汽车有限公司召回部分进口和国产汽车</a></li></ul>"}}'
)


def test_a_list_page_that_is_an_empty_shell_names_the_request_that_fills_it():
    from app.opinion.collect import fragment_of, unit_requests

    assert extract_links(SHELL, "https://www.samr.gov.cn/zw/zh/index.html") == []
    reqs = unit_requests(SHELL, "https://www.samr.gov.cn/zw/zh/index.html")
    assert reqs == [
        (
            "https://www.samr.gov.cn/api-gateway/jpaas-publish-server/front/page/build/unit",
            {"parseType": "buildstatic", "webId": "w1", "pageId": "p1"},
        )
    ]
    links = extract_links(fragment_of(FRAGMENT), "https://www.samr.gov.cn/zw/zh/index.html")
    assert [link.url for link in links] == ["https://www.samr.gov.cn/zw/zh/art/2026/art_1.html"]
    assert fragment_of("<p>plain html</p>") == "<p>plain html</p>"
    assert fragment_of('{"data": null}') == ""


async def test_the_run_fills_an_empty_list_page_through_its_own_request(session_factory):
    from urllib.parse import urlencode

    async with session_factory() as s:
        await ensure_sources(s)
        source = await s.scalar(
            select(OpinionSource).where(OpinionSource.entry_url == "https://www.samr.gov.cn/")
        )
    unit = "https://www.samr.gov.cn/api-gateway/jpaas-publish-server/front/page/build/unit"
    params = urlencode({"parseType": "buildstatic", "webId": "w1", "pageId": "p1"})
    fragment = (
        '{"data":{"html":"<a href=\\"/zw/zh/art/2026/art_1.html\\">'
        '汽车数据出境新规发布并开始实施征求意见</a>"}}'
    )
    fetcher = _Fetcher(
        {
            "https://www.samr.gov.cn/": '<html><body><a href="/zw/zh/index.html">召回</a></body></html>',
            "https://www.samr.gov.cn/zw/zh/index.html": SHELL,
            f"{unit}?{params}": fragment,
            "https://www.samr.gov.cn/zw/zh/art/2026/art_1.html": PAGE,
        }
    )
    stats = await collect_source(session_factory, fetcher, source)
    assert stats.kept == 1 and stats.relevant_links == 1


async def test_the_extraction_gives_the_model_room_for_a_long_article(monkeypatch):
    from app.opinion import extract

    seen = {}

    class _R:
        data = {"facts": {}}

    async def fake(messages, *, max_tokens, **_):
        seen["max_tokens"] = max_tokens
        return _R()

    monkeypatch.setattr(extract, "call_llm_json", fake)
    facts = await extract.read_facts("标题", "很长的正文" * 2000)
    assert facts is not None and seen["max_tokens"] >= 4000


def test_a_standards_system_or_software_update_rule_counts_as_a_regulatory_move():
    body = "工信部发布2021年汽车标准化工作要点，加快推进整车信息安全、软件升级强制性国家标准的立项和制定工作。"
    quote = "加快推进整车信息安全、软件升级强制性国家标准的立项和制定工作"
    facts = facts_from_model({"facts": {"policy_signal": {"value": True, "quote": quote}}}, body)
    assert facts.policy_signal and grade(facts).level == "medium"


def test_a_penalty_or_a_navigation_chip_that_has_nothing_to_do_with_data_does_not_count():
    body = (
        "交通运输委员会对钱某作出行政处罚，钱某不服申请行政复议。意法半导体推出汽车卫星导航芯片。"
    )
    facts = facts_from_model(
        {
            "facts": {
                "harm_happened": {"value": True, "quote": "交通运输委员会对钱某作出行政处罚"},
                "precise_location_or_biometrics": {
                    "value": True,
                    "quote": "意法半导体推出汽车卫星导航芯片",
                },
            }
        },
        body,
    )
    assert not facts.harm_happened and not facts.precise_location_or_biometrics
    assert grade(facts).level == "low"


def test_a_notice_title_is_fetched_but_a_notice_about_something_else_is_not_kept():
    assert looks_relevant("关于侵害用户权益行为的APP（SDK）通报")
    page = (
        '<html><head><meta name="ArticleTitle" content="关于侵害用户权益行为的APP通报"></head><body><p>'
        + "工业和信息化部组织检测发现一批手机应用存在违规收集个人信息的问题，要求限期整改。" * 4
        + "</p></body></html>"
    )
    assert (
        parse_article(page, "https://www.miit.gov.cn/jgsj/art/2026/art_1.html") is None
    )  # 与车无关
    car = page.replace("手机应用", "车载应用和车联网平台").replace("通报", "汽车数据通报")
    assert parse_article(car, "https://www.miit.gov.cn/jgsj/art/2026/art_2.html") is not None


def test_the_cac_enforcement_columns_that_forbid_robots_are_not_among_the_sources():
    from app.opinion.sources_default import DEFAULT_SOURCES

    urls = [u for _, _, u in DEFAULT_SOURCES]
    assert not any("/zfz/" in u or "/wxzf/" in u or "/wxb_zfz/" in u for u in urls)
    assert any("sjcjaqpg" in u for u in urls)  # 数据出境安全评估栏目在其中


def test_an_item_that_only_mentions_vehicles_in_passing_is_not_about_vehicles():
    from app.opinion.collect import on_topic

    road_show = (
        "数据安全和个人信息保护法规政策系列宣讲会举办，汽车、金融、医疗等行业企业代表参加。" * 3
    )
    assert not on_topic("数安中国行宣讲会在郑州举办", road_show.replace("汽车", "制造"))
    assert not on_topic("数安中国行宣讲会在郑州举办", "数据安全宣讲会，涉及汽车行业一家企业。" * 3)
    assert on_topic("汽车数据出境新规发布", "汽车数据处理者应当依法保护个人信息，数据出境须评估。")
    many = "智能网联汽车企业的汽车数据与车载终端的个人信息保护要求，汽车数据出境须经评估。"
    assert on_topic("数据安全要求发布", many * 2)


def test_list_pages_following_the_numbered_convention_are_addressed_and_others_are_not():
    from app.opinion.collect import next_page_url

    assert (
        next_page_url("http://www.caam.org.cn/chn/9/cate_97/list_1.html", 3)
        == "http://www.caam.org.cn/chn/9/cate_97/list_3.html"
    )
    assert (
        next_page_url("https://www.miit.gov.cn/xwfb/index_1.html", 2)
        == "https://www.miit.gov.cn/xwfb/index_2.html"
    )
    assert next_page_url("https://www.miit.gov.cn/xwfb/gxdt/index.html", 2) is None
    # 地址不按这个约定写页码的栏目不翻页；按约定写了但没有下一页的，由“读不到就停”收尾
    assert next_page_url("https://www.cac.gov.cn/wxzw/", 2) is None
    assert next_page_url("https://www.cac.gov.cn/wxzw/wlaq/A093705index_1.htm", 2) is not None


def test_a_traffic_article_that_only_says_vehicles_is_not_about_cars():
    from app.opinion.collect import on_topic

    body = (
        "交通运输数据共享。"
        + "车辆调度数据、车辆轨迹数据的汇聚与跨境数据流动。" * 20
        + "汽车一次。"
    )
    assert not on_topic("交通强国建设试点典型案例", body)
    assert on_topic("汽车数据出境安全", "汽车数据出境。" * 10 + "智能网联汽车的数据跨境传输。")
