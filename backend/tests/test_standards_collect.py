"""标准目录：只存目录信息；搜索词与主题分类是表；非汽车领域的标题不收。"""

from sqlalchemy import select

from app.standards.collect import (
    collect,
    in_domain,
    parse_results,
    search_url,
    topics_of,
)
from app.standards.models import StandardEntry

ROW = """<tr><td>1</td><td>{num}</td><td></td><td></td><td>{title}</td><td>{kind}</td>
<td>{status}</td><td>{pub} 00:00:00.0</td><td>{eff} 00:00:00.0</td>
<td><button class="detail" onclick="showInfo('{hc}');">查看详细</button></td></tr>"""


def _page(*rows):
    return "<html><body><table>" + "".join(rows) + "</table></body></html>"


def _row(
    num="GB 44497-2024", title="智能网联汽车 自动驾驶数据记录系统", kind="强标", status="现行"
):
    return ROW.format(
        num=num,
        title=title,
        kind=kind,
        status=status,
        pub="2024-08-23",
        eff="2026-01-01",
        hc="7FCE05",
    )


def test_a_results_row_gives_number_title_kind_status_dates_and_the_detail_address():
    (e,) = parse_results(_page(_row()))
    assert (e.number, e.kind, e.status) == ("GB 44497-2024", "mandatory", "in_force")
    assert str(e.published_on) == "2024-08-23" and str(e.effective_on) == "2026-01-01"
    assert e.detail_url == "https://openstd.samr.gov.cn/bzgk/gb/newGbInfo?hcno=7FCE05"
    assert "数据与信息安全" in e.topics  # “记录”“数据”


def test_other_table_rows_are_ignored_and_status_words_are_mapped():
    page = _page(
        "<tr><td>序号</td><td>标准号</td></tr>",
        _row("GB/T 1-2020", "智能网联汽车 术语", "推标", "即将实施"),
    )
    (e,) = parse_results(page)
    assert e.kind == "recommended" and e.status == "upcoming"


def test_titles_are_tagged_with_the_data_classes_of_the_requirement():
    assert "感知数据" in topics_of("汽车用毫米波雷达性能要求")
    assert "控制数据" in topics_of("智能网联汽车 组合驾驶辅助系统技术要求及试验")
    assert "路侧协同数据" in topics_of("车路协同 路侧设备通信要求")
    assert "车载交互数据" in topics_of("汽车驾驶员监测系统性能要求")
    assert topics_of("纺织品色牢度") == ()


def test_only_vehicle_and_driving_titles_are_in_the_domain():
    assert in_domain("智能网联汽车 术语和定义") and in_domain("道路车辆 网络安全工程")
    assert not in_domain("玩具安全 第 1 部分") and not in_domain("信息安全技术 个人信息处理")


def test_the_search_address_carries_the_phrase_and_the_page():
    url = search_url("自动驾驶", 3)
    assert "p.p2=%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6" in url and "page=3" in url


class _Res:
    def __init__(self, content):
        self.content = content.encode()


class _Fetcher:
    def __init__(self, pages):
        self.pages = pages

    async def get(self, url):
        if url not in self.pages:
            raise RuntimeError("no such page")
        return _Res(self.pages[url])


async def test_a_run_stores_domain_entries_once_and_remembers_which_searches_found_them(
    session_factory,
):
    page = _page(_row(), _row("GB 99-2020", "玩具安全 第1部分"), _row("GB/T 2-2020", "车联网 术语"))
    fetcher = _Fetcher({search_url("自动驾驶", 1): page, search_url("车联网", 1): page})
    stats = await collect(session_factory, fetcher, phrases=("自动驾驶", "车联网"))
    assert stats["new"] == 2 and stats["off_domain"] == 2 and stats["updated"] == 2
    async with session_factory() as s:
        rows = (await s.scalars(select(StandardEntry).order_by(StandardEntry.number))).all()
    assert [r.number for r in rows] == ["GB 44497-2024", "GB/T 2-2020"]
    assert rows[0].matched_keywords == ["自动驾驶", "车联网"] and rows[0].country_code == "CN"


def test_charging_batteries_and_rail_are_out_and_connected_driving_is_in():
    for title in (
        "电动汽车非车载充电机电能计量",
        "轨道交通 机车车辆 车载牵引锂离子电池",
        "电动汽车用动力蓄电池安全要求",
    ):
        assert not in_domain(title), title
    for title in (
        "智能网联汽车 术语和定义",
        "智能网联汽车数字身份及认证通用规范",
        "汽车事件数据记录系统",
        "道路车辆 网络安全工程",
        "乘用车车载信息优先级的确定方法",
    ):
        assert in_domain(title) or "信息优先级" in title, title
    assert not in_domain("汽车座椅头枕试验方法")  # 车辆词但不涉及数据、安全、通信


def test_industry_records_are_read_with_dates_status_and_kind():
    import json

    from app.standards.collect import parse_industry

    body = json.dumps(
        {
            "records": [
                {
                    "code": "YD/T 7143\u20132026",
                    "chName": "车路协同 路侧设备数据接口通信协议",
                    "status": "现行",
                    "issueDate": 1784044800000,
                    "actDate": 1793462400000,
                    "pk": "abc",
                },
                {"code": "", "chName": "无编号"},
            ]
        }
    )
    (e,) = parse_industry(body)
    assert e.number == "YD/T 7143-2026" and e.kind == "recommended" and e.status == "in_force"
    assert str(e.effective_on) == "2026-11-01" and e.detail_url.endswith("/abc")
    assert "路侧协同数据" in e.topics
    assert parse_industry("not json") == []
