# ruff: noqa: E501
"""Source catalogue: the complete list of official texts per country, kept as data.

One JSON file per country lists every text found for the ten categories the requirement asks us to
collect, with where it came from and what happened to it. Importing is one generic command over
that file: a new country is a new catalogue, never new code.

Statuses:
  ingest       official source, downloaded, ready to import
  ingested     already in the library
  listed       registered in the directory only (number, title, scope, publisher, where to buy);
               no full text, e.g. paywalled standards whose copyright forbids copying
  hold         downloaded but needs a decision first
  manual       the official text exists but cannot be fetched from here; a person must get it
  unavailable  checked, no official text exists

Every entry that is imported must carry the exact URL it was downloaded from, a SHA-256 that
matches the file, and a publisher on the source lock's list.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.ingestion.archive import ArchiveStore
from app.ingestion.manual_import import ManualImportResult, ManualSource, import_manual_file
from app.ingestion.ocr import OcrEngine
from app.ingestion.source_lock import publisher_of

CATEGORIES = (
    "法律",
    "行政法规",
    "部门规章",
    "行业规范",
    "汽车数据专项",
    "智驾标准",
    "个人信息保护",
    "跨境传输",
    "市场准入",
    "处罚",
)
STATUSES = ("ingest", "ingested", "listed", "hold", "manual", "unavailable")
DOCUMENT_TYPES = ("LAW", "REGULATION", "GUIDANCE", "STANDARD", "OTHER")
LEGAL_STATUSES = ("unknown", "in_force", "amended", "repealed", "draft")
_NEEDS_FILE = ("ingest",)  # ingested 只是登记现状，文件已在库里


class CatalogError(ValueError):
    """The catalogue is malformed or an entry cannot be imported as declared."""


@dataclass(frozen=True)
class CatalogItem:
    category: str
    title: str
    status: str
    language: str = ""
    document_type: str = ""
    instrument_kind: str = ""
    legal_status: str = ""
    publisher: str = ""
    official_number: str = ""
    published_date: str = ""
    url: str = ""
    file: str = ""
    sha256: str = ""
    note: str = ""
    title_zh: str = ""
    excerpt: str = ""  # "1"：公报摘录页，只取官方编号对应的那部法令
    excerpt_start: str = ""
    excerpt_end: str = ""

    def problems(self) -> list[str]:
        out = []
        if self.category not in CATEGORIES:
            out.append(f"类别不在十类之内：{self.category!r}")
        if self.status not in STATUSES:
            out.append(f"状态不合法：{self.status!r}")
        if not self.title.strip():
            out.append("缺标题")
        if self.status in _NEEDS_FILE:
            for name in ("language", "document_type", "publisher", "url", "file", "sha256"):
                if not getattr(self, name):
                    out.append(f"{self.status} 条目缺 {name}")
            if self.legal_status and self.legal_status not in LEGAL_STATUSES:
                out.append(f"legal_status 不合法：{self.legal_status!r}")
            if self.document_type and self.document_type not in DOCUMENT_TYPES:
                out.append(f"document_type 不合法：{self.document_type!r}")
            if self.sha256 and len(self.sha256) != 64:
                out.append("sha256 必须是 64 位完整值")
            if self.url and publisher_of(self.url) is None:
                out.append(f"网址不在发布机构名单内：{self.url}")
            if self.published_date:
                try:
                    date.fromisoformat(self.published_date)
                except ValueError:
                    out.append(f"发布日期格式应为 YYYY-MM-DD：{self.published_date!r}")
        elif self.status != "ingested" and not self.note.strip():
            out.append(f"{self.status} 条目必须写明原因")
        return out


@dataclass(frozen=True)
class Catalog:
    country: str
    items: tuple[CatalogItem, ...]
    entry_pages: tuple[dict, ...]
    base_dir: Path


def load_catalog(path: str | Path) -> Catalog:
    path = Path(path)
    raw = json.loads(path.read_text(encoding="utf-8"))
    country = str(raw.get("country", "")).upper()
    if len(country) != 2:
        raise CatalogError("清单缺国家码")
    fields = CatalogItem.__dataclass_fields__
    items, errors = [], []
    for index, entry in enumerate(raw.get("items", []), 1):
        unknown = set(entry) - set(fields)
        if unknown:
            errors.append(f"第 {index} 条有未知字段：{sorted(unknown)}")
            continue
        item = CatalogItem(**{k: str(entry.get(k, "") or "") for k in fields if k in entry})
        errors += [f"第 {index} 条（{item.title[:40]}）：{p}" for p in item.problems()]
        items.append(item)
    if errors:
        raise CatalogError("；".join(errors))
    return Catalog(country, tuple(items), tuple(raw.get("entry_pages", [])), path.parent)


def summarize(catalog: Catalog) -> dict[str, dict[str, int]]:
    """Category x status counts, so a gap in any of the ten categories is visible at a glance."""
    out = {c: dict.fromkeys(STATUSES, 0) for c in CATEGORIES}
    for item in catalog.items:
        out[item.category][item.status] += 1
    return out


def _digest(file: Path) -> str:
    return hashlib.sha256(file.read_bytes()).hexdigest()


async def import_catalog(
    catalog: Catalog,
    sf: async_sessionmaker[AsyncSession],
    archive: ArchiveStore,
    ocr: OcrEngine | None = None,
    *,
    dry_run: bool = False,
    concurrency: int = 1,
) -> list[dict]:
    """Import every ``ingest`` entry. Every file is checked against its declared SHA-256 first,
    so a replaced file stops the run before anything is written. Scanned files are slow to
    recognise; ``concurrency`` lets several be processed at once. One failing file does not stop
    the others: its row carries the error."""
    todo = [item for item in catalog.items if item.status == "ingest"]
    for item in todo:
        file = catalog.base_dir / item.file
        if not file.is_file():
            raise CatalogError(f"文件不存在：{item.file}")
        if _digest(file) != item.sha256:
            raise CatalogError(f"文件内容与清单登记的 sha256 不一致，未导入：{item.file}")
    if dry_run:
        return [{"title": i.title, "file": i.file, "result": "dry_run"} for i in todo]

    gate = asyncio.Semaphore(max(1, concurrency))

    async def one(item: CatalogItem) -> dict:
        row = {"title": item.title, "file": item.file}
        async with gate:
            try:
                res: ManualImportResult = await import_manual_file(
                    ManualSource(
                        country_code=catalog.country,
                        file=catalog.base_dir / item.file,
                        source_url=item.url,
                        title=item.title,
                        language=item.language,
                        document_type=item.document_type,
                        publisher=item.publisher,
                        official_number=item.official_number or None,
                        published_date=date.fromisoformat(item.published_date)
                        if item.published_date
                        else None,
                        instrument_kind=item.instrument_kind or None,
                        legal_status=item.legal_status or None,
                        excerpt=_excerpt_of(item),
                    ),
                    sf,
                    archive,
                    ocr,
                )
            except Exception as exc:  # noqa: BLE001 - one file must not stop the batch
                return {**row, "result": "error", "error": f"{type(exc).__name__}: {exc}"[:300]}
        return {**row, "result": "already_present" if res.already_present else res.state}

    rows = list(await asyncio.gather(*(one(i) for i in todo)))
    await apply_title_zh(catalog, sf)
    return rows


async def apply_title_zh(catalog: Catalog, sf: async_sessionmaker[AsyncSession]) -> int:
    """Record the catalogue's Chinese title on the imported documents of that country — matched by
    the official address, or by the original title written the same way (quotes, spaces and case
    normalised: “l'égard” and “l’égard” are the same title). An existing Chinese title is kept."""
    from sqlalchemy import select

    from app.core.rls import system_scope
    from app.ingestion.models import Document

    def norm(text: str) -> str:
        # 同一部法规在清单和库里的写法常差一点：括注（“(texte français)”“(arabe)”“(Law No. …)”）、破折号后的英文名、
        # 标点和撇号。比较时去掉这些，只看名称主体
        text = (text or "").replace("’", "'").replace("‘", "'").replace("`", "'")
        text = text.split(" — ")[0]  # 只认长破折号：英文标题里 " - " 后面常是附属条例名，不是同一部法
        text = re.sub(r"[(（][^()（）]*[)）]", " ", text)
        text = re.sub(r"[^\w']+", " ", text)
        return re.sub(r"\s+", " ", text).strip().casefold()

    count = 0
    with system_scope("catalog-title-zh"):
        async with sf() as session:
            docs = (await session.scalars(select(Document).where(Document.country_code == catalog.country, Document.title_zh.is_(None)))).all()
            by_url: dict[str, list] = {}
            by_title: dict[str, list] = {}
            for d in docs:
                by_url.setdefault(d.source_url, []).append(d)
                by_title.setdefault(norm(d.title), []).append(d)
            for item in catalog.items:
                zh = item.title_zh.strip()[:500]
                if not zh:
                    continue
                for d in by_url.get(item.url, []) + by_title.get(norm(item.title), []):
                    if d.title_zh is None:
                        d.title_zh = zh
                        count += 1
            await session.commit()
    return count


def _excerpt_of(item: CatalogItem) -> dict | None:
    if not (item.excerpt or item.excerpt_start):
        return None
    return {
        "number": item.official_number,
        "start": item.excerpt_start,
        "end": item.excerpt_end,
    }


MONITOR_INTERVAL_HOURS = 72  # 需求 2.3：新增与修订在 72 小时内发现


async def register_entry_pages(
    catalog: Catalog, sf: async_sessionmaker[AsyncSession], *, dry_run: bool = False
) -> list[dict]:
    """Register the catalogue's stable entry pages as crawl sites for scheduled monitoring.

    The sites are created disabled with unconfirmed terms: the platform administrator reviews
    the site's terms and switches monitoring on, so nothing here starts crawling by itself.
    Pages whose host is not an official publisher are reported, not registered. Repeatable.
    """
    from sqlalchemy import select

    from app.ingestion.source_lock import require_official
    from app.sources.models import SourceSite, Strategy

    languages: dict[str, dict[str, int]] = {}
    for item in catalog.items:
        counts = languages.setdefault(item.publisher, {})
        counts[item.language] = counts.get(item.language, 0) + 1
    fallback = max(
        (lang for counts in languages.values() for lang in counts),
        key=lambda lang: sum(c.get(lang, 0) for c in languages.values()),
        default="en",
    )

    rows: list[dict] = []
    async with sf() as s:
        for page in catalog.entry_pages:
            url = str(page.get("url", "")).strip()
            publisher = str(page.get("publisher", "")).strip()
            row = {"url": url, "publisher": publisher}
            try:
                if not url or not publisher:
                    raise ValueError("入口页缺网址或发布机构")
                require_official(url, None)
            except Exception as exc:  # noqa: BLE001 - report and move on
                rows.append({**row, "result": "rejected", "error": str(exc)[:200]})
                continue
            exists = (
                await s.execute(
                    select(SourceSite.id).where(
                        SourceSite.country_code == catalog.country,
                        SourceSite.entry_url == url,
                    )
                )
            ).first()
            if exists:
                rows.append({**row, "result": "already_present"})
                continue
            counts = languages.get(publisher, {})
            language = max(counts, key=counts.get) if counts else fallback
            if not dry_run:
                s.add(
                    SourceSite(
                        country_code=catalog.country,
                        publisher=publisher,
                        language=language,
                        document_type="OTHER",
                        entry_url=url,
                        strategy=Strategy.INDEX_PAGE.value,
                        config={},
                        enabled=False,
                        interval_hours=MONITOR_INTERVAL_HOURS,
                        terms_note=str(page.get("note", ""))[:500] or None,
                    )
                )
            rows.append({**row, "result": "dry_run" if dry_run else "registered"})
        await s.commit()
    return rows
