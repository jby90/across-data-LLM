# ruff: noqa: E501
"""Replace the reading of a document with a second reading of the same archived file.

A text layer can keep the words but scramble the reading order, so articles run together and one
article's text sits inside another. Reading the page images gives the right order. The archived
file is not touched: a new version (same bytes, next ``parse_revision``) is added with the clauses
cut from the second reading, then the earlier reading is folded into it (``version_merge``): a
version means an official text, and the same bytes read twice are one text. What pointed at the
earlier clauses (reports, graph, admission items) is moved to the matching new clauses. Translations and graph evidence of clauses
whose text is unchanged are reused, and the others are marked for re-check by the same version
linkage every other new version goes through.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ingestion.archive import ArchiveStore
from app.ingestion.clauses import article_number, split_clauses
from app.ingestion.excerpt import cut_excerpt
from app.ingestion.heading_sequence import (
    fix_reversed_headings,
    repair_heading_sequence,
    repair_headings,
)
from app.ingestion.models import Clause, DocumentVersion, PipelineEvent, VersionState
from app.ingestion.noise import clean_text  # 网页导航、地址电话、下一章标题不进库
from app.ingestion.parsing import strip_margin_line_numbers, strip_running_headers


def _cleaned(old: DocumentVersion, pages: list[str], language: str) -> list[str]:
    """The steps every re-cut applies to the pages before clauses are split."""
    if language.split("-")[0].lower() in ("ar", "fa", "ur"):
        pages, _ = fix_reversed_headings(pages)
        pages, _ = repair_heading_sequence(pages)
        pages, _ = repair_headings(pages)
    pages, _ = strip_running_headers(pages)
    pages, _ = strip_margin_line_numbers(pages)
    if old.excerpt:
        pages = cut_excerpt(
            pages,
            official_number=old.excerpt.get("number", ""),
            start=old.excerpt.get("start", ""),
            end=old.excerpt.get("end", ""),
        )
    return pages


MIN_CHAR_SHARE = 0.8  # 新版本的文字量不得低于现有版本的这个比例
MIN_PAGE_SHARE = 0.9  # 有文字的页数不得少于现有版本页数的这个比例（防止只读了一部分页就整份重切）
MIN_REAL_NUMBERING = 3  # 现有版本至少有这么多个条号，才把“条号不得减少”当底线
GARBAGE_CHARS = 500  # 现有版本字数少于它（几乎没读出内容）时，不按文字量与页数比


def regression_reason(
    *,
    old_numbers: set[int],
    old_chars: int,
    old_pages: int,
    new_numbers: set[int],
    new_chars: int,
    new_pages: int,
    allow_shrink: bool = False,
) -> str | None:
    """Why a re-cut must not replace the current version, or None when it may.

    A second reading is adopted only when it is no worse on what a user relies on: every article
    number found before is still found (a number may only be dropped when the old reading was
    itself broken), no large part of the text is lost, and the whole document was read. A reading
    that covers only some of the pages cuts a document into a few clauses; that must never become
    the latest version."""
    if old_pages and new_pages < MIN_PAGE_SHARE * old_pages:
        return f"只读了 {new_pages}/{old_pages} 页"
    # allow_shrink：操作者明确要把文件裁成其中一部法令（摘录），
    # 文字量与条号变少是预期的（页数仍要读全）
    if not allow_shrink and old_chars >= GARBAGE_CHARS and new_chars < MIN_CHAR_SHARE * old_chars:
        return f"文字量由 {old_chars} 降到 {new_chars}"
    # 现有版本的条号少于 3 个时（零星读出的数字、乱码里的“1”），它不是真的编号，不拿它当底线
    if (
        not allow_shrink
        and len(old_numbers) >= MIN_REAL_NUMBERING
        and len(new_numbers) < len(old_numbers)
    ):
        return f"条号由 {len(old_numbers)} 个减到 {len(new_numbers)} 个"
    return None


async def reparse_from_pages(
    session: AsyncSession,
    version_id,
    pages: list[str],
    language: str,
    *,
    note: str,
    text_source: str = "ocr",
    allow_shrink: bool = False,
    preserve_page_count: int | None = None,
) -> dict:
    old = await session.get(DocumentVersion, version_id)
    if old is None or old.state != VersionState.PARSED.value:
        raise ValueError("only a parsed version can be re-parsed")
    last = await session.scalar(
        select(func.max(DocumentVersion.version_no)).where(
            DocumentVersion.document_id == old.document_id
        )
    )
    last_revision = await session.scalar(
        select(func.max(DocumentVersion.parse_revision)).where(
            DocumentVersion.document_id == old.document_id,
            DocumentVersion.content_hash == old.content_hash,
        )
    )
    pages = _cleaned(old, pages, language)
    drafts, strategy = split_clauses(pages)
    if not drafts:
        raise ValueError("the second reading produced no clauses")
    old_rows = (
        await session.execute(
            select(Clause.clause_number, func.length(Clause.original_text)).where(
                Clause.version_id == old.id
            )
        )
    ).all()
    new_nonempty_pages = sum(1 for p in pages if p.strip())
    problem = regression_reason(
        old_numbers={n for c, _ in old_rows if (n := article_number(c)) is not None},
        old_chars=sum(length for _, length in old_rows),
        old_pages=new_nonempty_pages if preserve_page_count is not None else (old.page_count or 0),
        new_numbers={n for d in drafts if (n := article_number(d.clause_number)) is not None},
        new_chars=sum(len(d.text) for d in drafts),
        new_pages=new_nonempty_pages,
        allow_shrink=allow_shrink,
    )
    if problem:
        raise ValueError(f"新读数不优于现有版本，不新增版本：{problem}")

    new = DocumentVersion(
        document_id=old.document_id,
        version_no=(last or 0) + 1,
        content_hash=old.content_hash,
        excerpt=old.excerpt,
        archive_path=old.archive_path,
        size_bytes=old.size_bytes,
        mime_type=old.mime_type,
        final_url=old.final_url,
        fetched_at=old.fetched_at,
        state=VersionState.PARSED.value,
        page_count=preserve_page_count or len(pages),
        char_count=sum(len(p.strip()) for p in pages),
        parse_strategy=strategy,
        parsed_at=datetime.now(UTC),
        text_source=text_source,
        ocr_required=text_source == "ocr",
        official_number=old.official_number,
        version_label=old.version_label,
        published_date=old.published_date,
        effective_date=old.effective_date,
        repealed_date=old.repealed_date,
        legal_status=old.legal_status,
        metadata_source=old.metadata_source,
        parse_revision=(last_revision or old.parse_revision or 1) + 1,
    )
    session.add(new)
    await session.flush()
    for d in drafts:
        session.add(
            Clause(
                version_id=new.id,
                ordinal=d.ordinal,
                clause_number=d.clause_number,
                heading=d.heading,
                section_path=d.section_path,
                original_text=clean_text(d.text),
                page_start=d.page_start,
                page_end=d.page_end,
                text_hash=ArchiveStore.sha256(clean_text(d.text).encode("utf-8")),
                language=language,
                ocr_required=text_source == "ocr",
                needs_review=True,
            )
        )
    session.add(
        PipelineEvent(
            version_id=new.id,
            step="reparse",
            status="ok",
            message=f"{note}；{len(drafts)} 条，策略 {strategy}",
        )
    )
    await session.flush()
    from app.knowledge.extractor import handle_new_version_linkage

    linkage = await handle_new_version_linkage(session, new)
    old_clauses = await session.scalar(select(func.count()).select_from(Clause).where(Clause.version_id == old.id))
    # 同一份原文再读一遍不是新版本：识别质量检查后，把之前的读法并进这一份（引用一并改指向）
    from app.ingestion.ocr_check import apply_recognition_check
    from app.ingestion.version_merge import fold_into

    await apply_recognition_check(session, new)
    folded = await fold_into(session, new.id)
    await session.flush()
    return {
        "new_version_id": str(new.id),
        "version_no": new.version_no,
        "clauses": len(drafts),
        "old_clauses": old_clauses,
        "linkage": linkage,
        "folded": folded,
    }


def _continuity(drafts) -> float:
    """Share of article numbers 1..max that are present once (0 when nothing is numbered)."""
    numbers = [n for d in drafts if (n := article_number(d.clause_number)) is not None]
    return len(set(numbers)) / max(numbers) if len(numbers) >= 5 else 0.0


async def reparse_from_archive(
    session: AsyncSession, archive: ArchiveStore, version_id, language: str
) -> dict:
    """Read the archived file again with today's parser and keep the result only when it numbers
    the clauses better. Used when the parser has learned something (column order) since a file was
    imported; a file that does not improve is left exactly as it was."""
    from app.ingestion.parsing import parse_document

    old = await session.get(DocumentVersion, version_id)
    if old is None or old.state != VersionState.PARSED.value:
        raise ValueError("only a parsed version can be re-parsed")
    parsed = parse_document(archive.read(old.archive_path), old.mime_type)
    pages = parsed.pages
    if language.split("-")[0].lower() in ("ar", "fa", "ur"):
        pages, _ = repair_headings(pages)
    pages, _ = strip_running_headers(pages)
    pages, _ = strip_margin_line_numbers(pages)
    if old.excerpt:
        pages = cut_excerpt(
            pages,
            official_number=old.excerpt.get("number", ""),
            start=old.excerpt.get("start", ""),
            end=old.excerpt.get("end", ""),
        )
    drafts, _ = split_clauses(pages)
    old_numbers = (
        await session.scalars(
            select(Clause.clause_number).where(Clause.version_id == old.id).order_by(Clause.ordinal)
        )
    ).all()
    before = _continuity([type("D", (), {"clause_number": n}) for n in old_numbers])
    after = _continuity(drafts)
    if after <= before:
        return {"status": "kept_old", "before": round(before, 3), "after": round(after, 3)}
    result = await reparse_from_pages(
        session,
        version_id,
        parsed.pages,
        language,
        note=f"按栏顺序重新读取文字层，条号连续率 {before:.0%}→{after:.0%}",
        text_source="text_layer",
    )
    return {"status": "reparsed", "before": round(before, 3), "after": round(after, 3), **result}


async def resplit_from_stored(session: AsyncSession, version_id, language: str) -> dict:
    """Cut a version again from the text it already holds (clause texts in order, put back on
    their pages), for files whose text came from image recognition and so cannot be read from the
    archived bytes. The text is unchanged; only where one clause ends and the next begins."""
    old = await session.get(DocumentVersion, version_id)
    if old is None or old.state != VersionState.PARSED.value:
        raise ValueError("only a parsed version can be re-split")
    rows = (
        await session.execute(
            select(Clause.clause_number, Clause.original_text, Clause.page_start)
            .where(Clause.version_id == old.id)
            .order_by(Clause.ordinal)
        )
    ).all()
    by_page: dict[int, list[str]] = {}
    for _, text, page in rows:
        by_page.setdefault(page or 1, []).append(text)
    pages = [chr(10).join(by_page.get(n, [])) for n in range(1, max(by_page, default=1) + 1)]
    drafts, _ = split_clauses(pages)
    before = _continuity([type("D", (), {"clause_number": n}) for n, _, _ in rows])
    after = _continuity(drafts)
    if after <= before or not drafts:
        return {"status": "kept_old", "before": round(before, 3), "after": round(after, 3)}
    result = await reparse_from_pages(
        session,
        version_id,
        pages,
        language,
        note=f"按已识别文本重新切分，条号连续率 {before:.0%}→{after:.0%}",
        text_source=old.text_source or "text_layer",
        preserve_page_count=old.page_count,
    )
    return {"status": "reparsed", "before": round(before, 3), "after": round(after, 3), **result}


async def apply_page_replacements(
    session: AsyncSession,
    version_id,
    replacements: dict[int, str],
    language: str,
    archive: ArchiveStore | None = None,
    corroborated: frozenset[int] | set[int] = frozenset(),
) -> dict:
    """Put better readings of some pages into a version and cut it again.

    Each page is judged first (``page_replace.judge``); the version is replaced only when at
    least one page was accepted. The pages that were not accepted keep their old text.

    For a text-layer file the pages are the file's own pages (read again from the archive): the
    page numbers stored with its clauses follow the text, not the sheet, so they cannot say which
    text a page holds. An image-recognised file has no other source, so its stored pages are used.
    Either way the result is kept only if the clause numbering is no worse than before."""
    from app.ingestion.page_replace import merge_pages
    from app.ingestion.parsing import parse_document

    current = await session.get(DocumentVersion, version_id)
    rows = (
        await session.execute(
            select(Clause.clause_number, Clause.page_start, Clause.original_text)
            .where(Clause.version_id == version_id)
            .order_by(Clause.ordinal)
        )
    ).all()
    if archive is not None and current.text_source == "text_layer":
        parsed_pages = parse_document(archive.read(current.archive_path), current.mime_type).pages
        old = {i + 1: t for i, t in enumerate(parsed_pages)}
    else:
        by_page: dict[int, list[str]] = {}
        for _, page, text in rows:
            by_page.setdefault(page or 1, []).append(text)
        old = {p: chr(10).join(t) for p, t in by_page.items()}
    for page in replacements:
        old.setdefault(page, "")
    merged, verdicts = merge_pages(old, replacements, language, corroborated)
    accepted = sorted(p for p, v in verdicts.items() if v.accept)
    if not accepted:
        return {
            "status": "kept_old",
            "accepted": [],
            "rejected": {p: v.reason for p, v in verdicts.items()},
        }
    before = _continuity([type("D", (), {"clause_number": n}) for n, _, _ in rows])
    last = max(merged)

    def shape(candidate: dict[int, str]) -> tuple[int, int]:
        drafts, _ = split_clauses(
            _cleaned(current, [candidate.get(n, "") for n in range(1, last + 1)], language)
        )
        numbers = {n for d in drafts if (n := article_number(d.clause_number)) is not None}
        return len(numbers), len(drafts)

    # 逐页试：把这一页换上后真的切分一遍，条号或条款数少了就不要这一页。有的文件文字层和页面
    # 图像对不上（后半部分文字层是别的页的内容），换一页会让别处的条号丢失，只有试了才知道
    base: dict[int, str] = {**old}
    best = shape(base)
    kept: list[int] = []
    refused: dict[int, str] = {p: v.reason for p, v in verdicts.items() if not v.accept}
    for page in accepted:
        trial = {**base, page: merged[page]}
        got = shape(trial)
        # 有条号的文件：条号数和条款数都不能少。没有条号的文件（按长度切块）：块数只取决于文字
        # 总量，换掉乱码页后块数少一块不是丢内容，这时只要求条号数不少
        no_numbering = best[0] == 0
        if got[0] >= best[0] and (no_numbering or got[1] >= best[1]):
            base, best = trial, got
            kept.append(page)
        else:
            refused[page] = "替换后别处条号或条款丢失，保留原读数"
    if not kept:
        return {"status": "kept_old", "accepted": [], "rejected": refused}
    accepted = kept
    merged = base
    pages = [merged.get(n, "") for n in range(1, last + 1)]
    after = _continuity(split_clauses(_cleaned(current, pages, language))[0])
    if after < before:
        return {
            "status": "kept_old",
            "accepted": [],
            "rejected": {p: "替换后条号连续率变差，整份不采用" for p in accepted},
        }
    result = await reparse_from_pages(
        session,
        version_id,
        pages,
        language,
        note=f"第 {','.join(map(str, accepted))} 页改用图像识别的新读数（现有读数不可用）",
    )
    return {
        "status": "replaced",
        "accepted": accepted,
        "rejected": refused,
        **result,
    }
