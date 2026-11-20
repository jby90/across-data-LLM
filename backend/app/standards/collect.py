"""Collect the directory of intelligent-driving, connected-vehicle and in-vehicle-data standards.

Source: the national standards full-text disclosure platform of the State Administration for Market
Regulation, which lists every national standard with its number, title, kind (mandatory or
recommended), status and dates. Only that directory information is stored, never the text.
What is searched, and how a title is tagged with the data classes of the requirement (perception,
decision, control, in-vehicle interaction, roadside coordination, data security), are tables below,
so another standards body or country is a new table entry, not new code.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from bs4 import BeautifulSoup

PUBLISHER = "国家标准化管理委员会（国家标准全文公开系统）"
LIST_URL = "https://openstd.samr.gov.cn/bzgk/gb/std_list"
DETAIL_URL = "https://openstd.samr.gov.cn/bzgk/gb/newGbInfo?hcno={hcno}"
PAGE_SIZE = 50
MAX_PAGES = 20

# What to search for: each phrase is one query of the platform.
SEARCH_PHRASES = (
    "智能网联汽车",
    "自动驾驶",
    "车联网",
    "驾驶辅助",
    "汽车 数据",
    "汽车 信息安全",
    "汽车 网络安全",
    "汽车 软件升级",
    "车载",
    "道路车辆 网络安全",
    "电动汽车 远程",
    "汽车 事件数据记录",
    "汽车 驾驶员监测",
    "车辆 定位",
    "车用 通信",
    "智能交通",
    "高精度地图",
    "车路协同",
    "智能座舱",
    "汽车 诊断",
    "车载 以太网",
    "汽车 雷达",
    "自动紧急制动",
    "车道 保持",
    "汽车 功能安全",
    "汽车 电子 通信",
    "整车 网络",
    "电动汽车 通信协议",
    "汽车 隐私",
    "无人驾驶",
    "自动泊车",
    "汽车 远程 升级",
    "车辆 数据 接口",
)

# A title is in scope when it names intelligent driving or connected vehicles outright, or names a
# vehicle together with a data, safety or communication subject. Charging equipment, batteries and
# rail or ship vehicles are outside the requirement's three themes (in-vehicle data, driving safety,
# cross-border transfer) and are excluded by name.
STRONG_WORDS = (
    "智能网联",
    "自动驾驶",
    "驾驶辅助",
    "辅助驾驶",
    "驾驶自动化",
    "智能驾驶",
    "车联网",
    "车路协同",
    "网联汽车",
)
VEHICLE_WORDS = ("汽车", "车辆", "道路车辆", "乘用车", "商用车", "车载")
SUBJECT_WORDS = (
    "数据",
    "信息安全",
    "网络安全",
    "软件升级",
    "通信",
    "定位",
    "记录",
    "监测",
    "终端",
    "操作系统",
    "地图",
    "隐私",
    "个人信息",
    "身份",
    "认证",
    "远程服务",
    "事件数据",
)
EXCLUDED_WORDS = (
    "轨道",
    "铁路",
    "机车",
    "船舶",
    "充电",
    "蓄电池",
    "电池",
    "非车载",
    "卫星",
    "测绘",
    "适配器",
    "直流电源",
)
DOMAIN_WORDS = STRONG_WORDS + VEHICLE_WORDS
# Data classes of the requirement (2.4), each with the words in a title that indicate it.
TOPIC_RULES = (
    ("感知数据", ("感知", "雷达", "摄像", "传感", "视觉", "探测", "环境", "目标", "夜视", "盲区")),
    (
        "决策数据",
        ("决策", "规划", "自动驾驶系统", "设计运行条件", "功能安全", "预期功能安全", "运行安全"),
    ),
    (
        "控制数据",
        (
            "控制",
            "转向",
            "制动",
            "紧急",
            "驾驶辅助",
            "组合驾驶",
            "车道",
            "巡航",
            "泊车",
            "车控",
            "换道",
        ),
    ),
    (
        "车载交互数据",
        (
            "人机",
            "交互",
            "驾驶员",
            "监测",
            "座舱",
            "信息娱乐",
            "语音",
            "显示",
            "指示",
            "操作系统",
            "车载终端",
        ),
    ),
    (
        "路侧协同数据",
        ("路侧", "车路", "协同", "V2X", "通信", "车联网", "智能交通", "高精度地图", "定位", "网联"),
    ),
    (
        "数据与信息安全",
        (
            "数据",
            "信息安全",
            "网络安全",
            "软件升级",
            "隐私",
            "个人信息",
            "记录",
            "安全要求",
            "数字身份",
            "认证",
            "密码",
        ),
    ),
    ("综合与测试", ("术语", "测试", "试验", "通用", "评价", "分级", "场地", "道路试验")),
)
KIND = {"强标": "mandatory", "推标": "recommended", "指标": "guidance"}
STATUS = {"现行": "in_force", "即将实施": "upcoming", "废止": "withdrawn"}


@dataclass(frozen=True)
class Entry:
    number: str
    title: str
    kind: str
    status: str
    published_on: date | None
    effective_on: date | None
    detail_url: str | None
    topics: tuple[str, ...]


def _date(text: str) -> date | None:
    m = re.match(r"\s*(\d{4})-(\d{2})-(\d{2})", text or "")
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def topics_of(title: str) -> tuple[str, ...]:
    return tuple(name for name, words in TOPIC_RULES if any(w in title for w in words))


def in_domain(title: str) -> bool:
    if any(w in title for w in EXCLUDED_WORDS):
        return False
    if any(w in title for w in STRONG_WORDS):
        return True
    return any(w in title for w in VEHICLE_WORDS) and any(w in title for w in SUBJECT_WORDS)


def parse_results(html: bytes | str) -> list[Entry]:
    """The rows of one page of search results."""
    soup = BeautifulSoup(html, "html.parser")
    out: list[Entry] = []
    for tr in soup.find_all("tr"):
        cells = [re.sub(r"\s+", " ", td.get_text(" ")).strip() for td in tr.find_all("td")]
        if len(cells) < 9 or not re.match(r"^(GB|GB/T|GB/Z)\s*\d", cells[1]):
            continue
        button = tr.find("button", onclick=True)
        hcno = re.search(r"showInfo\('([0-9A-Fa-f]+)'", button["onclick"]) if button else None
        title = cells[4]
        out.append(
            Entry(
                number=re.sub(r"\s+", " ", cells[1]),
                title=title,
                kind=KIND.get(cells[5], "recommended"),
                status=STATUS.get(cells[6], "in_force"),
                published_on=_date(cells[7]),
                effective_on=_date(cells[8]),
                detail_url=DETAIL_URL.format(hcno=hcno.group(1)) if hcno else None,
                topics=topics_of(title),
            )
        )
    return out


def search_url(phrase: str, page: int) -> str:
    from urllib.parse import urlencode

    query = {
        "p.p1": "0",
        "p.p90": "circulation_date",
        "p.p91": "desc",
        "p.p2": phrase,
        "page": str(page),
        "pageSize": str(PAGE_SIZE),
    }
    return f"{LIST_URL}?{urlencode(query)}"


async def collect(sf, fetcher, *, phrases=SEARCH_PHRASES) -> dict[str, int]:
    """Search every phrase, keep the entries in the vehicle/driving domain, store new ones."""
    from sqlalchemy import select

    from app.standards.models import StandardEntry

    stats = {
        "phrases": 0,
        "rows": 0,
        "kept": 0,
        "new": 0,
        "updated": 0,
        "off_domain": 0,
        "errors": 0,
    }
    for phrase in phrases:
        stats["phrases"] += 1
        seen_here: set[str] = set()
        for page in range(1, MAX_PAGES + 1):
            url = search_url(phrase, page)
            try:
                res = await fetcher.get(url)
            except Exception:  # noqa: BLE001 - one failed page ends this phrase, not the run
                stats["errors"] += 1
                break
            entries = parse_results(res.content or b"")
            fresh = [e for e in entries if e.number not in seen_here]
            if not fresh:
                break
            seen_here |= {e.number for e in fresh}
            stats["rows"] += len(fresh)
            async with sf() as session:
                for e in fresh:
                    if not in_domain(e.title):
                        stats["off_domain"] += 1
                        continue
                    from sqlalchemy import text as sa_text

                    stats["kept"] += 1
                    await session.execute(
                        sa_text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"),
                        {"k": f"std_num:{e.number}"},
                    )
                    row = await session.scalar(
                        select(StandardEntry).where(StandardEntry.number == e.number)
                    )
                    if row is None:
                        session.add(
                            StandardEntry(
                                country_code="CN",
                                number=e.number,
                                title=e.title,
                                kind=e.kind,
                                status=e.status,
                                published_on=e.published_on,
                                effective_on=e.effective_on,
                                publisher=PUBLISHER,
                                source_url=LIST_URL,
                                detail_url=e.detail_url,
                                topics=list(e.topics),
                                matched_keywords=[phrase],
                            )
                        )
                        stats["new"] += 1
                    else:
                        row.status, row.kind = e.status, e.kind
                        row.topics = list(e.topics)  # 分类规则是表，改了表重采即可重新打标签
                        row.effective_on = e.effective_on or row.effective_on
                        row.detail_url = e.detail_url or row.detail_url
                        if phrase not in (row.matched_keywords or []):
                            row.matched_keywords = [*(row.matched_keywords or []), phrase]
                        stats["updated"] += 1
                await session.commit()
            if len(entries) < PAGE_SIZE:
                break
    return stats


async def prune_off_domain(sf, *, dry_run: bool = False) -> dict[str, int]:
    """Remove stored entries that the current scope rule would not admit."""
    from sqlalchemy import delete, select

    from app.standards.models import StandardEntry

    async with sf() as session:
        rows = (await session.execute(select(StandardEntry.id, StandardEntry.title))).all()
        drop = [r.id for r in rows if not in_domain(r.title)]
        if drop and not dry_run:
            await session.execute(delete(StandardEntry).where(StandardEntry.id.in_(drop)))
            await session.commit()
    return {"kept": len(rows) - len(drop), "removed": len(drop), "dry_run": int(dry_run)}


# ---- 行业标准（工信部、交通部、公安部等备案的 QC/T、JT/T、YD/T、GA/T 等） ----
INDUSTRY_PUBLISHER = "行业标准信息服务平台（国家标准化管理委员会）"
INDUSTRY_LIST_URL = "https://hbba.sacinfo.org.cn/stdList"
INDUSTRY_QUERY_URL = "https://hbba.sacinfo.org.cn/stdQueryList"
INDUSTRY_DETAIL_URL = "https://hbba.sacinfo.org.cn/stdDetail/{pk}"
INDUSTRY_PAGE_SIZE = 50
INDUSTRY_STATUS = {"现行": "in_force", "废止": "withdrawn", "即将实施": "upcoming"}


CHINA = timezone(timedelta(hours=8))  # 平台的日期是北京时间零点


def _epoch_date(value) -> date | None:
    try:
        return datetime.fromtimestamp(int(value) / 1000, CHINA).date()
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def parse_industry(body: bytes | str) -> list[Entry]:
    """The records of one page of the industry-standard query (JSON)."""
    try:
        data = json.loads(body)
    except ValueError:
        return []
    out: list[Entry] = []
    for r in (data or {}).get("records") or []:
        number = re.sub(
            r"\s+", " ", str(r.get("code") or "").replace("\u2013", "-").replace("\u2014", "-")
        )
        title = re.sub(r"\s+", " ", str(r.get("chName") or "")).strip()
        if not number or not title:
            continue
        out.append(
            Entry(
                number=number,
                title=title,
                kind="recommended" if "/T" in number else "mandatory",
                status=INDUSTRY_STATUS.get(str(r.get("status") or ""), "in_force"),
                published_on=_epoch_date(r.get("issueDate")),
                effective_on=_epoch_date(r.get("actDate")),
                detail_url=INDUSTRY_DETAIL_URL.format(pk=r["pk"]) if r.get("pk") else None,
                topics=topics_of(title),
            )
        )
    return out


async def collect_industry(sf, fetcher, *, phrases=SEARCH_PHRASES) -> dict[str, int]:
    """Same phrases and scope rule as the national standards, against the industry platform."""
    from sqlalchemy import select

    from app.standards.models import StandardEntry

    stats = {
        "phrases": 0,
        "rows": 0,
        "kept": 0,
        "new": 0,
        "updated": 0,
        "off_domain": 0,
        "errors": 0,
    }
    for phrase in phrases:
        stats["phrases"] += 1
        seen_here: set[str] = set()
        for page in range(1, MAX_PAGES + 1):
            form = {
                "current": str(page),
                "size": str(INDUSTRY_PAGE_SIZE),
                "key": phrase,
                "ministry": "",
                "industry": "",
                "pubdate": "",
                "date": "",
                "status": "",
            }
            try:
                res = await fetcher.post_form(INDUSTRY_QUERY_URL, form)
            except Exception:  # noqa: BLE001 - one failed page ends this phrase, not the run
                stats["errors"] += 1
                break
            entries = parse_industry(res.content or b"")
            fresh = [e for e in entries if e.number not in seen_here]
            if not fresh:
                break
            seen_here |= {e.number for e in fresh}
            stats["rows"] += len(fresh)
            async with sf() as session:
                for e in fresh:
                    if not in_domain(e.title):
                        stats["off_domain"] += 1
                        continue
                    from sqlalchemy import text as sa_text

                    stats["kept"] += 1
                    await session.execute(
                        sa_text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"),
                        {"k": f"std_num:{e.number}"},
                    )
                    row = await session.scalar(
                        select(StandardEntry).where(StandardEntry.number == e.number)
                    )
                    if row is None:
                        session.add(
                            StandardEntry(
                                country_code="CN",
                                number=e.number,
                                title=e.title,
                                kind=e.kind,
                                status=e.status,
                                published_on=e.published_on,
                                effective_on=e.effective_on,
                                publisher=INDUSTRY_PUBLISHER,
                                source_url=INDUSTRY_LIST_URL,
                                detail_url=e.detail_url,
                                topics=list(e.topics),
                                matched_keywords=[phrase],
                            )
                        )
                        stats["new"] += 1
                    else:
                        row.status, row.kind = e.status, e.kind
                        row.topics = list(e.topics)
                        row.effective_on = e.effective_on or row.effective_on
                        row.detail_url = e.detail_url or row.detail_url
                        if phrase not in (row.matched_keywords or []):
                            row.matched_keywords = [*(row.matched_keywords or []), phrase]
                        stats["updated"] += 1
                await session.commit()
            if len(entries) < INDUSTRY_PAGE_SIZE:
                break
    return stats
