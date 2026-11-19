# ruff: noqa: E501
"""Collect automotive-data news and notices from the listed official sources.

Only publishers on the source lock are read, through the same polite fetcher the legal library
uses (robots.txt, per-host spacing). A section page is searched for article links; links whose
title looks relevant are fetched; an article is kept only when its own text matches the topic
keywords too, so a headline alone never admits an item.
Everything source-specific is data (the section addresses); the parsing is the same everywhere.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date
from urllib.parse import urlencode, urljoin, urlsplit

from bs4 import BeautifulSoup

from app.ingestion.source_lock import publisher_of

# Topic words. The scope is automotive data, driving-safety and cross-border transfer, so an item
# must be about vehicles (AUTO_WORDS) and about data, network security, intelligent driving, export
# of data, or a safety incident (SCOPE_WORDS). A title needs one word from either group to be
# fetched at all; the article text then has to show both groups, with at least two different words.
AUTO_WORDS = (
    "汽车",
    "车联网",
    "智能网联",
    "智能汽车",
    "自动驾驶",
    "智驾",
    "辅助驾驶",
    "车载",
    "新能源车",
    "整车",
    "车辆",
    "OTA",
)
SCOPE_WORDS = (
    # 车载数据与个人信息
    "汽车数据",
    "车载数据",
    "车辆数据",
    "用户数据",
    "重要数据",
    "数据安全",
    "数据处理",
    "数据保护",
    "数据合规",
    "数据治理",
    "数据出境",
    "数据跨境",
    "个人信息",
    "隐私",
    "人脸",
    "车牌",
    "座舱数据",
    "地理信息",
    "测绘",
    # 网络与信息安全、数据出境
    "网络安全",
    "信息安全",
    "出境",
    "跨境",
    "安全评估",
    "标准合同",
    "泄露",
    # 智能驾驶安全
    "车联网",
    "智能网联",
    "自动驾驶",
    "智驾",
    "辅助驾驶",
    "驾驶辅助",
    "OTA",
)
# 执法、通报、典型案例类标题：标题不带“汽车”也读正文，是否在主题内仍由正文决定（正文必须同时
# 讲到车辆与数据/安全）
NOTICE_WORDS = ("执法", "通报", "典型案例", "违法违规", "侵害用户权益", "整改")
HEADLINE_WORDS = tuple(dict.fromkeys(AUTO_WORDS + SCOPE_WORDS + NOTICE_WORDS))
BODY_WORDS = HEADLINE_WORDS
# 标题里带这些字的条目最可能是“法规标准发布/征求意见”类，口径变化时先重读它们
POLICY_CANDIDATE_WORDS = ("标准", "指南", "征求意见", "规定", "办法", "条例", "指引", "规范")
MIN_BODY_MATCHES = 2
MIN_VEHICLE_MENTIONS = 4
# “车辆”“整车”在交通、物流文章里随处可见，不能证明文章讲的是汽车；数篇幅时只数这些专指词
SPECIFIC_AUTO_WORDS = tuple(w for w in AUTO_WORDS if w not in ("车辆", "整车"))
MIN_BODY_CHARS = 120
_ARTICLE_URL = re.compile(
    r"\.(?:s?html?)(?:$|\?)|/c\.html|/content_\d+|/art/|/c_\d+", re.IGNORECASE
)
_SKIP_HOST_PARTS = ("beian.", "weibo.", "weixin.", "qq.com")
_DATE_IN_TEXT = re.compile(r"(20\d{2})[-年./](\d{1,2})[-月./](\d{1,2})")
_DATE_IN_URL = re.compile(
    r"/(20\d{2})[-/]?(\d{2})[-/]?(\d{2})/|/art/(20\d{2})/|(20\d{2})(\d{2})(\d{2})"
)


@dataclass(frozen=True)
class Link:
    title: str
    url: str


@dataclass(frozen=True)
class Article:
    title: str
    body: str
    published_on: date | None
    matched: tuple[str, ...]


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").replace("　", " ")).strip()


def extract_links(html: bytes | str, base_url: str) -> list[Link]:
    """Article-looking links on a section page, on the same publisher, without duplicates."""
    soup = BeautifulSoup(html, "html.parser")
    base_publisher = publisher_of(base_url)
    seen: set[str] = set()
    out: list[Link] = []
    for a in soup.find_all("a", href=True):
        title = _clean(a.get_text())
        if len(title) < 8:
            continue
        url = urljoin(base_url, a["href"].strip())
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https") or any(
            s in (parts.hostname or "") for s in _SKIP_HOST_PARTS
        ):
            continue
        publisher = publisher_of(url)
        if publisher is None or (base_publisher and publisher.host != base_publisher.host):
            continue
        if not _ARTICLE_URL.search(url):
            continue
        url = url.split("#", 1)[0]
        if url in seen:
            continue
        seen.add(url)
        out.append(Link(title, url))
    return out


# 栏目导航的文字：政策法规、通知公告、执法通报、召回、数据安全、汽车……（短文字，不是文章标题）
SECTION_WORDS = (
    "政策",
    "法规",
    "法律",
    "通知",
    "公告",
    "公示",
    "通报",
    "执法",
    "召回",
    "数据安全",
    "网络安全",
    "个人信息",
    "汽车",
    "智能网联",
    "标准",
    "动态",
    "要闻",
    "解读",
    "新闻",
)
MAX_SECTIONS = 14


def extract_sections(html: bytes | str, base_url: str) -> list[Link]:
    """Navigation entries that lead to a section of the same publisher (short label, no article)."""
    soup = BeautifulSoup(html, "html.parser")
    base_publisher = publisher_of(base_url)
    seen: set[str] = {base_url.rstrip("/")}
    out: list[Link] = []
    for a in soup.find_all("a", href=True):
        label = re.sub(r"\s+", "", a.get_text())
        if not 2 <= len(label) <= 8 or not any(w in label for w in SECTION_WORDS):
            continue
        url = urljoin(base_url, a["href"].strip()).split("#", 1)[0]
        publisher = publisher_of(url)
        if publisher is None or (base_publisher and publisher.host != base_publisher.host):
            continue
        if url.rstrip("/") in seen or not url.startswith(("http://", "https://")):
            continue
        seen.add(url.rstrip("/"))
        out.append(Link(label, url))
    return out[:MAX_SECTIONS]


_UNIT_SCRIPT = re.compile(r'<script[^>]*\burl="([^"]*build/unit)"[^>]*\bqueryData="([^"]*)"')


def unit_requests(html: bytes | str, base_url: str) -> list[tuple[str, dict]]:
    """List requests a page makes for itself.

    Many government sites are built on one publishing platform whose list pages are empty shells:
    the page carries the address and parameters of the request that fills the list. Making that
    same request (same site, same parameters, no login) returns the list as an HTML fragment.
    """
    text = html.decode("utf-8", "ignore") if isinstance(html, bytes) else html
    out: list[tuple[str, dict]] = []
    for match in _UNIT_SCRIPT.finditer(text):
        raw = match.group(2).replace("&quot;", '"').replace("&#39;", "'")
        try:
            params = json.loads(raw.replace("'", '"'))
        except ValueError:
            continue
        if isinstance(params, dict):
            out.append((urljoin(base_url, match.group(1)), {k: str(v) for k, v in params.items()}))
    return out


def fragment_of(body: bytes | str) -> str:
    """The HTML inside a unit response (JSON with data.html), or the body itself when it is HTML."""
    text = body.decode("utf-8", "ignore") if isinstance(body, bytes) else body
    try:
        data = json.loads(text)
    except ValueError:
        return text
    html = ((data or {}).get("data") or {}).get("html") if isinstance(data, dict) else None
    return html or ""


_PAGE_NUMBER = re.compile(
    r"(?P<head>.*?(?:list_|index_))(?P<n>\d+)(?P<tail>\.html?)$", re.IGNORECASE
)
MAX_EXTRA_PAGES = 12


def next_page_url(url: str, page: int) -> str | None:
    """The address of list page ``page`` when the section follows the ``list_N.html`` /
    ``index_N.html`` convention; None for addresses that do not paginate that way."""
    match = _PAGE_NUMBER.match(url.split("?", 1)[0])
    if not match:
        return None
    return f"{match.group('head')}{page}{match.group('tail')}"


def looks_relevant(title: str) -> bool:
    return any(w in title for w in HEADLINE_WORDS)


def _date_from(text: str) -> date | None:
    m = _DATE_IN_TEXT.search(text or "")
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def _date_from_url(url: str) -> date | None:
    m = re.search(r"/(20\d{2})[-/]?(\d{2})[-/]?(\d{2})(?:/|$|_)", url)
    if not m:
        m = re.search(r"/(20\d{2})(\d{2})(\d{2})/", url)
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


_PORTAL_HEADER_NOISE = re.compile(
    r"(?:\[?\s*邮箱登录\s*\]?|旧版网站|设为首页|加入收藏|协会概况\s+协会工作|\[\s*字号[：:]|字体[：:]\s*小\s*中\s*大|分享到[：:]|新华网\s*>\s*>\s*正文|当前位置[：:])"
)


def strip_article_chrome(text: str) -> str:
    if not text:
        return ""
    kept: list[str] = []
    for raw_line in text.splitlines():
        line = _clean(raw_line)
        if not line:
            continue
        if _PORTAL_HEADER_NOISE.search(line) and len(line) < 160:
            # Strip leading breadcrumb/meta prefix if actual sentence follows on the same line
            cleaned = re.sub(
                r"^.*?(?:\[\s*打印\s*\]|分享到[：:]\S*|来源[：:]\s*\S+\s*)", "", line
            ).strip()
            if len(cleaned) >= 24 and not _PORTAL_HEADER_NOISE.search(cleaned):
                kept.append(cleaned)
            continue
        if re.fullmatch(r"(?:首页|首\s*页|时政要闻|网信政务|互动服务|热点专题|English|\s|\|)+", line):
            continue
        kept.append(line)
    return "\n".join(kept).strip()


def parse_article(html: bytes | str, url: str) -> Article | None:
    """Title, publication date and main text of an article page; None when it is not an article
    about the topic (too short, or fewer than ``MIN_BODY_MATCHES`` different topic words)."""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "header", "footer", "aside", "form", "noscript"]):
        tag.decompose()
    title = ""
    for selector in ({"property": "og:title"}, {"name": "ArticleTitle"}, {"name": "title"}):
        meta = soup.find("meta", attrs=selector)
        if meta and meta.get("content"):
            title = _clean(meta["content"])
            break
    if not title:
        h1 = soup.find("h1")
        title = _clean(h1.get_text()) if h1 else _clean(soup.title.get_text() if soup.title else "")
    published: date | None = None
    for selector in (
        {"name": "PubDate"},
        {"name": "publishdate"},
        {"property": "article:published_time"},
        {"name": "pubdate"},
    ):
        meta = soup.find("meta", attrs=selector)
        if meta and meta.get("content"):
            published = _date_from(meta["content"])
            if published:
                break
    container = None
    for selector in (
        "#detailContent",
        "#UCAP-CONTENT",
        ".TRS_Editor",
        "#zoom",
        ".article",
        "#article",
        ".content",
        "article",
    ):
        container = soup.select_one(selector)
        if container and len(_clean(container.get_text())) >= MIN_BODY_CHARS:
            break
        container = None
    node = container or soup.body or soup
    paragraphs = [_clean(p.get_text()) for p in node.find_all(["p", "li", "div"])]
    paragraphs = [p for p in paragraphs if len(p) >= 12]
    # nested blocks repeat their children's text: keep a paragraph only if it adds new text
    body_lines: list[str] = []
    for p in paragraphs:
        if not any(p in kept or kept in p for kept in body_lines[-3:]):
            body_lines.append(p)
    raw_body = "\n".join(body_lines) if body_lines else _clean(node.get_text())
    body = strip_article_chrome(raw_body) or raw_body
    if published is None:
        published = _date_from(soup.get_text()[:1500]) or _date_from_url(url)
    if len(body) < MIN_BODY_CHARS or not title:
        return None
    matched = tuple(w for w in dict.fromkeys(BODY_WORDS) if w in title or w in body)
    if (
        len(matched) < MIN_BODY_MATCHES
        or not any(w in AUTO_WORDS for w in matched)
        or not any(w in SCOPE_WORDS for w in matched)
    ):
        return None
    # the item has to be about vehicles, not mention them in passing: a vehicle word in the
    # title, or vehicle words several times in the text
    mentions = sum(body.count(w) for w in SPECIFIC_AUTO_WORDS)
    if not any(w in title for w in AUTO_WORDS) and mentions < MIN_VEHICLE_MENTIONS:
        return None
    return Article(title[:500], body[:20000], published, matched)


def content_hash(title: str, body: str) -> str:
    return hashlib.sha256(f"{title}\n{body}".encode()).hexdigest()


async def ensure_sources(session) -> int:
    """Insert the starting sources that are not there yet. A source whose host is not on the source
    lock is refused, so the list can only ever name confirmed publishers."""
    from sqlalchemy import select

    from app.opinion.models import OpinionSource
    from app.opinion.sources_default import DEFAULT_SOURCES

    added = 0
    for publisher, kind, url in DEFAULT_SOURCES:
        if publisher_of(url) is None:
            raise ValueError(f"来源不在已确认的发布机构名单内：{url}")
        exists = await session.scalar(
            select(OpinionSource.id).where(OpinionSource.entry_url == url)
        )
        if exists is None:
            session.add(OpinionSource(publisher=publisher, kind=kind, entry_url=url))
            added += 1
    await session.commit()
    return added


@dataclass
class RunStats:
    pages: int = 0
    links: int = 0
    relevant_links: int = 0
    fetched: int = 0
    kept: int = 0
    already: int = 0
    rejected: int = 0
    errors: int = 0


async def _page_links(fetcher, page) -> list[Link]:
    """Article links of a page, including the ones its own list request fills in."""
    html = page.content or b""
    links = extract_links(html, page.url)
    for url, params in unit_requests(html, page.url):
        try:
            res = await fetcher.get(f"{url}?{urlencode(params)}")
        except Exception:  # noqa: BLE001 - a list that cannot be read is skipped
            continue
        links += extract_links(fragment_of(res.content or b""), page.url)
    return links


async def collect_source(sf, fetcher, source, *, per_page: int = 40) -> RunStats:
    """Read one section page, fetch the relevant new articles and store the ones on topic."""
    from datetime import UTC, datetime

    from sqlalchemy import select

    from app.opinion.models import OpinionItem, OpinionSource

    stats = RunStats()
    note = None
    status = "success"
    try:
        page = await fetcher.get(source.entry_url)
        stats.pages += 1
        links = await _page_links(fetcher, page)
        # the home page is only a door: read the sections it points to as well
        for section in extract_sections(page.content or b"", page.url):
            try:
                res = await fetcher.get(section.url)
            except Exception:  # noqa: BLE001 - a section that cannot be read is skipped
                continue
            stats.pages += 1
            links += await _page_links(fetcher, res)
            # older notices sit on the following pages of the same section
            seen_urls = {link.url for link in links}
            for number in range(2, MAX_EXTRA_PAGES + 2):
                nxt = next_page_url(section.url, number)
                if nxt is None:
                    break
                try:
                    more = await fetcher.get(nxt)
                except Exception:  # noqa: BLE001 - the last page ends the section
                    break
                stats.pages += 1
                fresh = [x for x in await _page_links(fetcher, more) if x.url not in seen_urls]
                if not fresh:
                    break
                seen_urls |= {x.url for x in fresh}
                links += fresh
        unique: dict[str, Link] = {}
        for link in links:
            unique.setdefault(link.url, link)
        stats.links = len(unique)
        wanted = [link for link in unique.values() if looks_relevant(link.title)][:per_page]
        stats.relevant_links = len(wanted)
        for link in wanted:
            async with sf() as session:
                if await session.scalar(select(OpinionItem.id).where(OpinionItem.url == link.url)):
                    stats.already += 1
                    continue
            try:
                res = await fetcher.get(link.url)
            except Exception:  # noqa: BLE001 - one unreachable article must not stop the run
                stats.errors += 1
                continue
            stats.fetched += 1
            article = parse_article(res.content or b"", res.url)
            if article is None:
                stats.rejected += 1
                continue
            async with sf() as session:
                try:
                    session.add(
                        OpinionItem(
                            source_id=source.id,
                            publisher=source.publisher,
                            kind=source.kind,
                            url=link.url,
                            title=article.title,
                            published_on=article.published_on,
                            body=article.body,
                            content_hash=content_hash(article.title, article.body),
                            matched_keywords=list(article.matched),
                        )
                    )
                    await session.commit()
                except Exception:  # noqa: BLE001 - the same article reached by two sources
                    await session.rollback()
                    stats.already += 1
                    continue
            stats.kept += 1
    except Exception:  # noqa: BLE001
        status = "failed"
        note = "官方站点连接超时或访问受限，稍后自动重试"
        stats.errors += 1
    async with sf() as session:
        row = await session.get(OpinionSource, source.id)
        row.last_run_at = datetime.now(UTC)
        row.last_status = status
        row.last_note = note or (
            f"读取 {stats.relevant_links} 篇相关标题，新增 {stats.kept}，已有 {stats.already}，"
            f"不在主题内 {stats.rejected}"
        )
        await session.commit()
    return stats


async def collect_urls(sf, fetcher, urls: list[str]) -> RunStats:
    """Store specific articles (for example a notice a regulator or state media reported on) by
    address. The publisher must be on the source lock and has to have a registered source."""
    from sqlalchemy import select

    from app.opinion.models import OpinionItem, OpinionSource

    stats = RunStats()
    for url in urls:
        publisher = publisher_of(url)
        if publisher is None:
            stats.rejected += 1
            continue
        async with sf() as session:
            if await session.scalar(select(OpinionItem.id).where(OpinionItem.url == url)):
                stats.already += 1
                continue
            source = await session.scalar(
                select(OpinionSource).where(OpinionSource.entry_url.contains(publisher.host))
            )
        if source is None:
            stats.rejected += 1
            continue
        try:
            res = await fetcher.get(url)
        except Exception:  # noqa: BLE001 - one unreachable article must not stop the run
            stats.errors += 1
            continue
        stats.fetched += 1
        article = parse_article(res.content or b"", res.url)
        if article is None:
            stats.rejected += 1
            continue
        async with sf() as session:
            session.add(
                OpinionItem(
                    source_id=source.id,
                    publisher=source.publisher,
                    kind=source.kind,
                    url=url,
                    title=article.title,
                    published_on=article.published_on,
                    body=article.body,
                    content_hash=content_hash(article.title, article.body),
                    matched_keywords=list(article.matched),
                )
            )
            try:
                await session.commit()
            except Exception:  # noqa: BLE001 - the same article reached by two sources
                stats.already += 1
                continue
        stats.kept += 1
    return stats


async def regrade_stored(sf) -> dict[str, int]:
    """Grade again from the facts already stored, under today's guards and rule table, without
    asking the model: a rule that got stricter changes grades, not the cost."""
    from sqlalchemy import select

    from app.opinion.extract import POLICY_TOPIC
    from app.opinion.grading import Facts, grade
    from app.opinion.models import OpinionItem

    changed = total = 0
    async with sf() as session:
        for item in (
            await session.scalars(select(OpinionItem).where(OpinionItem.facts.is_not(None)))
        ).all():
            f = dict(item.facts or {})
            ev = dict(f.get("evidence") or {})
            policy = ev.get("policy_signal", "")
            if f.get("policy_signal") and not any(w in policy for w in POLICY_TOPIC):
                f["policy_signal"] = False
                ev.pop("policy_signal", None)
            facts = Facts(
                important_data=tuple(f.get("important_data") or ()),
                outside_video_or_plates=bool(f.get("outside_video_or_plates")),
                precise_location_or_biometrics=bool(f.get("precise_location_or_biometrics")),
                sensitive_area=bool(f.get("sensitive_area")),
                people=f.get("people"),
                sensitive_people=f.get("sensitive_people"),
                crosses_border=bool(f.get("crosses_border")),
                harm_happened=bool(f.get("harm_happened")),
                policy_signal=bool(f.get("policy_signal")),
                evidence=ev,
            )
            g = grade(facts)
            total += 1
            if g.level != item.level:
                changed += 1
            item.level, item.rules, item.bases = g.level, list(g.rules), list(g.bases)
            item.scale_unknown = g.scale_unknown
            f["evidence"] = ev
            item.facts = f
        await session.commit()
    return {"items": total, "changed": changed}


async def grade_items(
    sf,
    *,
    limit: int | None = None,
    regrade: bool = False,
    concurrency: int = 4,
    policy_candidates: bool = False,
):
    """Grade stored items that have no grade yet (or all of them with ``regrade``)."""
    import asyncio
    from datetime import UTC, datetime

    from sqlalchemy import select

    from app.opinion.extract import GRADING_VERSION, read_facts
    from app.opinion.grading import grade
    from app.opinion.models import OpinionItem

    async with sf() as session:
        q = select(OpinionItem.id).order_by(OpinionItem.fetched_at.desc())
        if not regrade:
            q = q.where(OpinionItem.level.is_(None))
        if policy_candidates:
            # 只重读可能受“政策主题”口径变化影响的：已判中高档的，和标题带规章、标准字样的
            from sqlalchemy import or_

            q = q.where(
                or_(
                    OpinionItem.level.in_(("medium", "high")),
                    *[OpinionItem.title.contains(w) for w in POLICY_CANDIDATE_WORDS],
                )
            )
        if limit:
            q = q.limit(limit)
        ids = list((await session.scalars(q)).all())
    gate = asyncio.Semaphore(max(1, concurrency))
    stats = {"graded": 0, "unavailable": 0}

    async def one(item_id):
        async with gate:
            async with sf() as session:
                item = await session.get(OpinionItem, item_id)
                facts = await read_facts(item.title, item.body)
                if facts is None:
                    stats["unavailable"] += 1
                    return
                g = grade(facts)
                item.level = g.level
                item.rules = list(g.rules)
                item.bases = list(g.bases)
                item.scale_unknown = g.scale_unknown
                item.facts = {
                    "important_data": list(facts.important_data),
                    "outside_video_or_plates": facts.outside_video_or_plates,
                    "precise_location_or_biometrics": facts.precise_location_or_biometrics,
                    "sensitive_area": facts.sensitive_area,
                    "people": facts.people,
                    "sensitive_people": facts.sensitive_people,
                    "crosses_border": facts.crosses_border,
                    "harm_happened": facts.harm_happened,
                    "policy_signal": facts.policy_signal,
                    "evidence": facts.evidence,
                }
                item.graded_at = datetime.now(UTC)
                item.grading_version = GRADING_VERSION
                await session.commit()
                stats["graded"] += 1

    await asyncio.gather(*(one(i) for i in ids))
    return stats


def on_topic(title: str, body: str) -> bool:
    """The same topic test as ``parse_article``, applied to text that is already stored."""
    matched = tuple(w for w in dict.fromkeys(BODY_WORDS) if w in title or w in body)
    if (
        len(matched) < MIN_BODY_MATCHES
        or not any(w in AUTO_WORDS for w in matched)
        or not any(w in SCOPE_WORDS for w in matched)
    ):
        return False
    mentions = sum(body.count(w) for w in SPECIFIC_AUTO_WORDS)
    return any(w in title for w in AUTO_WORDS) or mentions >= MIN_VEHICLE_MENTIONS


async def prune_off_topic(sf, *, dry_run: bool = False) -> dict[str, int]:
    """Remove stored items that the current topic rule would not admit (and their labels follow)."""
    from sqlalchemy import delete, select

    from app.opinion.models import OpinionItem

    async with sf() as session:
        rows = (
            await session.execute(select(OpinionItem.id, OpinionItem.title, OpinionItem.body))
        ).all()
        drop = [r.id for r in rows if not on_topic(r.title, r.body)]
        if drop and not dry_run:
            await session.execute(delete(OpinionItem).where(OpinionItem.id.in_(drop)))
            await session.commit()
    return {"kept": len(rows) - len(drop), "removed": len(drop), "dry_run": int(dry_run)}
