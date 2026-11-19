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

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base

MAX_EDIT_CHARS = 4000


class DocumentInstance(Base):
    __tablename__ = "document_instances"

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

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    instance_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("document_instances.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    user_name: Mapped[str] = mapped_column(String(128), default="")
    place: Mapped[str] = mapped_column(String(128))
    before: Mapped[str] = mapped_column(Text)
    after: Mapped[str] = mapped_column(Text)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


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


def build_xlsx(blocks: list[dict], title: str) -> bytes:
    """Workbook with one sheet: clean typography, no raw Markdown markers, semantic 8-column widths, bordered tables, and merged prose spans."""
    import math

    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter

    from app.analysis.rich_text import strip_bold

    wb = Workbook()
    ws = wb.active
    clean_sheet = strip_bold(title or "合规文件").translate(str.maketrans("", "", r"[]:*?/\\"))[:28] or "合规文件"
    ws.title = clean_sheet

    max_cols = max([len(b.get("head") or []) for b in blocks if b.get("type") == "table"] + [3])
    max_cols = min(max(max_cols, 3), 8)

    wrap = Alignment(wrap_text=True, vertical="top")
    head_align = Alignment(wrap_text=True, vertical="center")
    title_font = Font(name="Microsoft YaHei", bold=True, size=14, color="000000")
    h1_font = Font(name="Microsoft YaHei", bold=True, size=12, color="000000")
    h2_font = Font(name="Microsoft YaHei", bold=True, size=11, color="000000")
    body_font = Font(name="Microsoft YaHei", bold=False, size=10, color="000000")
    th_font = Font(name="Microsoft YaHei", bold=True, size=10, color="000000")
    th_fill = PatternFill("solid", fgColor="F5F5F5")
    thin = Side(style="thin", color="000000")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)

    col_widths = [24, 34, 38, 34, 24, 24, 22, 22]
    for idx in range(1, max_cols + 1):
        ws.column_dimensions[get_column_letter(idx)].width = col_widths[idx - 1]
    total_char_width = sum(col_widths[:max_cols])

    def _merge_prose_row(row_idx: int, txt: str, font_obj: Font, min_h: int = 22) -> None:
        val = strip_bold(txt or "")
        c = ws.cell(row_idx, 1, val)
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
                _merge_prose_row(r, f"· {strip_bold(str(it or ''))}", body_font, min_h=20)
                r += 1
        elif kind == "table":
            if not b.get("no_head"):
                heads = [strip_bold(str(h or "")) for h in (b.get("head") or [])]
                for j, h in enumerate(heads, 1):
                    cell = ws.cell(r, j, h)
                    cell.font = th_font
                    cell.fill = th_fill
                    cell.alignment = head_align
                    cell.border = border
                ws.row_dimensions[r].height = 24
                r += 1
            for row in b.get("rows") or []:
                max_lines = 1
                for j, v in enumerate(row, 1):
                    val = strip_bold(str(v or ""))
                    cell = ws.cell(r, j, val)
                    cell.font = body_font
                    cell.alignment = wrap
                    cell.border = border
                    cw = col_widths[min(j - 1, len(col_widths) - 1)]
                    ln_cnt = sum(max(1, math.ceil(len(ln) / max(10, cw * 0.45))) for ln in (val.splitlines() or [""]))
                    max_lines = max(max_lines, ln_cnt)
                ws.row_dimensions[r].height = max(20, max_lines * 15 + 6)
                r += 1
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
