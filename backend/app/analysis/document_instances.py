# ruff: noqa: E501
"""A generated compliance document kept as its own record, so it can be edited by hand.

The template engine renders a document from the assessment every time it is asked for. Once a user
wants to adjust the wording, the rendered blocks are saved as an instance; every later change is a
row in the edit log (who, when, where, the text before and after), and exports come from the saved
blocks. The template version the instance was made from is kept, so a later template edit does not
touch documents already made.
"""

from __future__ import annotations

import io
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base

MAX_EDIT_CHARS = 4000


class DocumentInstance(Base):
    __tablename__ = "document_instances"
    __table_args__ = (UniqueConstraint("task_id", "code", name="uq_document_instance_task_code"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    task_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("analysis_tasks.id", ondelete="CASCADE"), index=True
    )
    org_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    code: Mapped[str] = mapped_column(String(64))
    name_zh: Mapped[str] = mapped_column(String(128))
    template_version: Mapped[int] = mapped_column(Integer)
    blocks: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    edit_count: Mapped[int] = mapped_column(Integer, default=0)
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class DocumentEdit(Base):
    __tablename__ = "document_edits"
    __table_args__ = (Index("ix_document_edits_task_code", "task_id", "doc_code"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    instance_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)
    org_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)
    task_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    doc_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    user_name: Mapped[str] = mapped_column(String(128), default="")
    place: Mapped[str] = mapped_column(String(128))
    before: Mapped[str] = mapped_column(Text)
    after: Mapped[str] = mapped_column(Text)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DocumentInstanceVersion(Base):
    """Immutable point-in-time version snapshot sealed when the user starts or stops editing."""

    __tablename__ = "document_instance_versions"
    __table_args__ = (
        UniqueConstraint("instance_id", "version_no", name="uq_doc_instance_version_no"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    instance_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("document_instances.id", ondelete="CASCADE"), index=True
    )
    version_no: Mapped[int] = mapped_column(Integer)
    blocks: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    edit_count: Mapped[int] = mapped_column(Integer, default=0)
    note: Mapped[str] = mapped_column(String(200), default="")
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    user_name: Mapped[str] = mapped_column(String(128), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


def _block_summary_text(b: dict) -> str:
    kind = b.get("type")
    if kind in ("title", "heading", "subheading", "paragraph"):
        return str(b.get("text") or "")
    if kind == "list":
        return "；".join(str(x or "") for x in (b.get("items") or []))
    if kind == "table":
        return " | ".join(
            " / ".join(str(c or "") for c in (r or []))
            for r in (b.get("rows") or [])
        )
    return ""


def diff_blocks(cur_blocks: list[dict], target_blocks: list[dict]) -> list[tuple[str, str, str]]:
    """Compute (place, before_text, after_text) differences from cur_blocks to target_blocks."""
    diffs: list[tuple[str, str, str]] = []
    limit = min(len(cur_blocks), len(target_blocks))
    for b_idx in range(limit):
        cb, tb = cur_blocks[b_idx], target_blocks[b_idx]
        kind = cb.get("type")
        if kind != tb.get("type"):
            c_sum, t_sum = _block_summary_text(cb), _block_summary_text(tb)
            if c_sum != t_sum:
                diffs.append((f"第 {b_idx + 1} 段结构调整", c_sum, t_sum))
            continue
        if kind in ("title", "heading", "subheading", "paragraph"):
            c_txt = str(cb.get("text") or "")
            t_txt = str(tb.get("text") or "")
            if c_txt != t_txt:
                diffs.append((f"第 {b_idx + 1} 段", c_txt, t_txt))
        elif kind == "list":
            c_items = cb.get("items") or []
            t_items = tb.get("items") or []
            max_i = max(len(c_items), len(t_items))
            for i_idx in range(max_i):
                c_txt = str(c_items[i_idx] or "") if i_idx < len(c_items) else ""
                t_txt = str(t_items[i_idx] or "") if i_idx < len(t_items) else ""
                if c_txt != t_txt:
                    diffs.append((f"第 {b_idx + 1} 段第 {i_idx + 1} 条", c_txt, t_txt))
        elif kind == "table":
            c_rows = cb.get("rows") or []
            t_rows = tb.get("rows") or []
            max_r = max(len(c_rows), len(t_rows))
            for r_idx in range(max_r):
                cr = (c_rows[r_idx] or []) if r_idx < len(c_rows) else []
                tr = (t_rows[r_idx] or []) if r_idx < len(t_rows) else []
                max_c = max(len(cr), len(tr))
                for col_idx in range(max_c):
                    c_txt = str(cr[col_idx] or "") if col_idx < len(cr) else ""
                    t_txt = str(tr[col_idx] or "") if col_idx < len(tr) else ""
                    if c_txt != t_txt:
                        diffs.append(
                            (f"第 {b_idx + 1} 段表格第 {r_idx + 1} 行第 {col_idx + 1} 列", c_txt, t_txt)
                        )
    if len(cur_blocks) > limit:
        for b_idx in range(limit, len(cur_blocks)):
            c_sum = _block_summary_text(cur_blocks[b_idx])
            if c_sum:
                diffs.append((f"第 {b_idx + 1} 段移除", c_sum, ""))
    elif len(target_blocks) > limit:
        for b_idx in range(limit, len(target_blocks)):
            t_sum = _block_summary_text(target_blocks[b_idx])
            if t_sum:
                diffs.append((f"第 {b_idx + 1} 段恢复", "", t_sum))
    return diffs


class EditError(ValueError):
    pass


def _where(blocks: list[dict], block: int, item: int | None, row: int | None, col: int | None):
    """The list that holds the text and its index, plus a readable place name."""
    if not 0 <= block < len(blocks):
        raise EditError("没有这一处内容")
    b = blocks[block]
    kind = b.get("type")
    if kind in ("title", "heading", "subheading", "paragraph"):
        if item is not None or row is not None or col is not None:
            raise EditError("这一处不是列表或表格")
        return b, "text", f"第 {block + 1} 段"
    if kind == "list":
        if item is None or not 0 <= item < len(b["items"]):
            raise EditError("没有这一条")
        return b["items"], item, f"第 {block + 1} 段第 {item + 1} 条"
    if kind == "table":
        if row is None or col is None or not 0 <= row < len(b["rows"]):
            raise EditError("没有这个单元格")
        if not 0 <= col < len(b["rows"][row]):
            raise EditError("没有这个单元格")
        return b["rows"][row], col, f"第 {block + 1} 段表格第 {row + 1} 行第 {col + 1} 列"
    raise EditError("这一处不能修改")


def apply_edit(
    blocks: list[dict],
    text: str,
    *,
    block: int,
    item: int | None = None,
    row: int | None = None,
    col: int | None = None,
) -> tuple[str, str, str]:
    """Change one piece of text in place; returns (place, before, after)."""
    text = (text or "").strip()
    if not text:
        raise EditError("内容不能为空")
    if len(text) > MAX_EDIT_CHARS:
        raise EditError(f"内容请控制在 {MAX_EDIT_CHARS} 字以内")
    holder, key, place = _where(blocks, block, item, row, col)
    before = str(holder[key])
    holder[key] = text
    return place, before, text


def build_xlsx(
    blocks: list[dict],
    title: str,
    *,
    foreign: bool = False,
    rtl: bool = False,
) -> bytes:
    """Workbook with one sheet: clean typography, no raw Markdown markers, semantic column widths, bordered tables, and merged prose/grid spans."""
    import math
    import re

    from openpyxl import Workbook
    from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter

    from app.analysis.rich_text import strip_bold
    from app.core.spreadsheet import write_safe_xlsx_cell

    def _safe_xlsx_str(raw: object) -> str:
        return ILLEGAL_CHARACTERS_RE.sub("", strip_bold(str(raw or "")))

    wb = Workbook()
    ws = wb.active
    fallback_sheet = "Sheet1" if (foreign or rtl) else "合规文件"
    raw_sheet = _safe_xlsx_str(title or fallback_sheet).translate(str.maketrans("", "", r"[]:*?/\\")).strip()
    if (foreign or rtl) and re.search(r"[\u3400-\u9fff]", raw_sheet):
        raw_sheet = re.sub(r"[\u3400-\u9fff\u3000-\u303f\uff00-\uffef]+", " ", raw_sheet).strip(" -_·")
    clean_sheet = raw_sheet[:28].strip() or fallback_sheet
    ws.title = clean_sheet
    if rtl and ws.views.sheetView:
        ws.views.sheetView[0].rightToLeft = True

    table_col_counts = [
        max([len(b.get("head") or []), *(len(row) for row in (b.get("rows") or [])), 0])
        for b in blocks
        if b.get("type") == "table"
    ]
    max_cols = min(max([*table_col_counts, 3]), 12)

    horiz = "right" if rtl else "left"
    wrap = Alignment(horizontal=horiz, wrap_text=True, vertical="top", readingOrder=2 if rtl else 0)
    head_align = Alignment(horizontal=horiz, wrap_text=True, vertical="center", readingOrder=2 if rtl else 0)
    font_family = "Arial" if (foreign or rtl) else "Microsoft YaHei"
    title_font = Font(name=font_family, bold=True, size=14, color="000000")
    h1_font = Font(name=font_family, bold=True, size=12, color="000000")
    h2_font = Font(name=font_family, bold=True, size=11, color="000000")
    body_font = Font(name=font_family, bold=False, size=10, color="000000")
    th_font = Font(name=font_family, bold=True, size=10, color="000000")
    th_fill = PatternFill("solid", fgColor="FFFFFF")
    thin = Side(style="thin", color="000000")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)

    col_widths = [24, 34, 38, 34, 24, 24, 22, 22, 20, 20, 18, 18]
    for idx in range(1, max_cols + 1):
        ws.column_dimensions[get_column_letter(idx)].width = col_widths[idx - 1]
    total_char_width = sum(col_widths[:max_cols])

    def _merge_prose_row(row_idx: int, txt: str, font_obj: Font, min_h: int = 22) -> None:
        val = _safe_xlsx_str(txt or "")
        c = write_safe_xlsx_cell(ws, row_idx, 1, val)
        c.font = font_obj
        c.alignment = wrap
        if max_cols > 1:
            ws.merge_cells(start_row=row_idx, start_column=1, end_row=row_idx, end_column=max_cols)
        lines = sum(max(1, math.ceil(len(ln) / max(24, total_char_width * 0.45))) for ln in (val.splitlines() or [""]))
        ws.row_dimensions[row_idx].height = max(min_h, lines * 16 + 6)

    r = 1
    for b in blocks:
        kind = b.get("type")
        if kind == "title":
            _merge_prose_row(r, str(b.get("text") or ""), title_font, min_h=28)
            r += 2
        elif kind == "heading":
            r += 1
            _merge_prose_row(r, str(b.get("text") or ""), h1_font, min_h=24)
            r += 1
        elif kind == "subheading":
            _merge_prose_row(r, str(b.get("text") or ""), h2_font, min_h=22)
            r += 1
        elif kind == "paragraph":
            _merge_prose_row(r, str(b.get("text") or ""), body_font, min_h=20)
            r += 1
        elif kind == "list":
            for it in b.get("items") or []:
                _merge_prose_row(r, f"· {_safe_xlsx_str(it)}", body_font, min_h=20)
                r += 1
        elif kind == "table":
            if not b.get("no_head"):
                heads = [_safe_xlsx_str(h) for h in (b.get("head") or [])]
                for j, h in enumerate(heads, 1):
                    cell = write_safe_xlsx_cell(ws, r, j, h)
                    cell.font = th_font
                    cell.fill = th_fill
                    cell.alignment = head_align
                    cell.border = border
                ws.row_dimensions[r].height = 24
                r += 1
            data_start_row = r
            rows_list = b.get("rows") or []
            for row in rows_list:
                max_lines = 1
                for j, v in enumerate(row, 1):
                    val = _safe_xlsx_str(v)
                    cell = write_safe_xlsx_cell(ws, r, j, val)
                    cell.font = body_font
                    cell.alignment = wrap
                    cell.border = border
                    cw = col_widths[min(j - 1, len(col_widths) - 1)]
                    ln_cnt = sum(max(1, math.ceil(len(ln) / max(10, cw * 0.45))) for ln in (val.splitlines() or [""]))
                    max_lines = max(max_lines, ln_cnt)
                ws.row_dimensions[r].height = max(20, max_lines * 15 + 6)
                r += 1
            raw_spans = b.get("spans") or []
            if raw_spans and isinstance(raw_spans[0], list):
                for r_off, sp_row in enumerate(raw_spans):
                    if not isinstance(sp_row, list):
                        continue
                    for c_off, pair in enumerate(sp_row):
                        if not isinstance(pair, list) or len(pair) < 2:
                            continue
                        try:
                            rs, cs = int(pair[0]), int(pair[1])
                            sr = data_start_row + r_off
                            sc = c_off + 1
                            er = sr + rs - 1
                            ec = sc + cs - 1
                            if (rs > 1 or cs > 1) and sr >= data_start_row and er < r and sc >= 1 and ec <= max_cols:
                                ws.merge_cells(start_row=sr, start_column=sc, end_row=er, end_column=ec)
                        except Exception:  # noqa: BLE001
                            continue
            else:
                for sp in raw_spans:
                    try:
                        sr = data_start_row + int(sp.get("r", 0))
                        sc = int(sp.get("c", 0)) + 1
                        er = sr + int(sp.get("rs", 1)) - 1
                        ec = sc + int(sp.get("cs", 1)) - 1
                        if (er > sr or ec > sc) and sr >= data_start_row and er < r and sc >= 1 and ec <= max_cols:
                            ws.merge_cells(start_row=sr, start_column=sc, end_row=er, end_column=ec)
                    except Exception:  # noqa: BLE001
                        continue
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
