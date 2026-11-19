# ruff: noqa: E501
"""Documents a user puts into an assessment conversation, and the review of what they lack.

A company that already has some compliance papers (a privacy policy, a data-handling procedure)
should not have to re-type them: it uploads them into the window, the system reads them, sets what
they say against what the target country's rules require, and says what is missing and how to
fill it (the “0 to 0.5” need of the 11.04 meeting).

Reading is local and takes no extra library: PDF text through the PDF library the platform already
uses, Word through its XML, plain text as is. A scan (a PDF with no text layer) is not guessed at;
the user is told it could not be read and why. The file itself is not kept, only the extracted text
and its hash, for the conversation it belongs to.

The review compares the document with the country's own clauses, dimension by dimension. For every
requirement it finds the model must quote the uploaded text word for word to say the document
covers it; the program checks the quote is really in the document. A requirement with no such quote
is reported as not covered, never as covered on the model's word.
"""

from __future__ import annotations

import hashlib
import io
import re
import uuid
import zipfile
from datetime import datetime
from xml.etree import ElementTree

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base

MAX_BYTES = 10 * 1024 * 1024
MAX_PAGES = 200
MAX_CHARS = 200_000
MAX_FILES = 5
ALLOWED = {".pdf": "application/pdf", ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document", ".txt": "text/plain", ".md": "text/plain"}


class TaskAttachment(Base):
    __tablename__ = "task_attachments"
    __table_args__ = (UniqueConstraint("task_id", "sha256", name="uq_task_attachment_hash"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    task_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("analysis_tasks.id", ondelete="CASCADE"), index=True
    )
    filename: Mapped[str] = mapped_column(String(255))
    mime: Mapped[str] = mapped_column(String(100), default="")
    size_bytes: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    pages: Mapped[int] = mapped_column(Integer, default=0)
    text: Mapped[str] = mapped_column(Text)
    readable: Mapped[bool] = mapped_column(Boolean, default=True)
    note: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    message_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)  # 随哪条消息发出；空 = 还在输入框里
    review_state: Mapped[str | None] = mapped_column(String(16), nullable=True)  # running / done / failed；空 = 还没开始对照
    review_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    review_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AttachmentReview(Base):
    __tablename__ = "attachment_reviews"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    attachment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("task_attachments.id", ondelete="CASCADE"), index=True
    )
    country_code: Mapped[str] = mapped_column(String(8))
    blocks: Mapped[list[dict]] = mapped_column(JSONB)
    summary: Mapped[dict] = mapped_column(JSONB, default=dict)
    tokens: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class UploadError(ValueError):
    """The file cannot be accepted; the message is shown to the user."""


def kind_of(filename: str) -> str:
    ext = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in ALLOWED:
        raise UploadError("目前支持 PDF、Word（.docx）和纯文本文件；其他格式请先另存为这几种之一")
    return ext


def _pdf_text(data: bytes) -> tuple[str, int]:
    import pypdfium2 as pdfium

    from app.ingestion.parsing import _PDFIUM_LOCK

    with _PDFIUM_LOCK:
        try:
            pdf = pdfium.PdfDocument(data)
        except Exception as exc:  # noqa: BLE001 - 损坏或加密的文件
            raise UploadError("这个 PDF 打不开（可能已损坏或设置了密码）") from exc
        try:
            pages = len(pdf)
            parts = []
            for i in range(min(pages, MAX_PAGES)):
                page = pdf[i]
                tp = page.get_textpage()
                try:
                    parts.append((tp.get_text_bounded() or "").replace("\r\n", "\n"))
                finally:
                    tp.close()
                    page.close()
            return "\n\n".join(parts), pages
        finally:
            pdf.close()


def _docx_text(data: bytes) -> tuple[str, int]:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            xml = zf.read("word/document.xml")
    except (zipfile.BadZipFile, KeyError) as exc:
        raise UploadError("这个 Word 文件打不开（请确认是 .docx，不是旧版 .doc）") from exc
    ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    paragraphs = []
    for p in ElementTree.fromstring(xml).iter(ns + "p"):
        paragraphs.append("".join(t.text or "" for t in p.iter(ns + "t")))
    return "\n".join(paragraphs), 0


def extract(filename: str, data: bytes) -> tuple[str, int, bool, str]:
    """(text, pages, readable, note). ``readable`` is False when there is no usable text."""
    ext = kind_of(filename)
    if ext == ".pdf":
        text, pages = _pdf_text(data)
    elif ext == ".docx":
        text, pages = _docx_text(data)
    else:
        for enc in ("utf-8-sig", "gb18030"):
            try:
                text, pages = data.decode(enc), 0
                break
            except UnicodeDecodeError:
                continue
        else:
            raise UploadError("文本文件的编码无法识别，请另存为 UTF-8 后再上传")
    text = re.sub(r"[ \t ]+", " ", text.replace("\r\n", "\n")).strip()
    note = ""
    if ext == ".pdf" and pages > MAX_PAGES:
        note = f"文件共 {pages} 页，只读取了前 {MAX_PAGES} 页。"
    if len(text) > MAX_CHARS:
        text, note = text[:MAX_CHARS], (note + f" 文字超过 {MAX_CHARS // 1000} 千字，只读取了前面部分。").strip()
    if len(text) < 50:
        why = "没有读到文字，可能是扫描件（图片）。请上传带文字层的 PDF 或 Word，或把内容另存为文本。" if ext == ".pdf" else "几乎没有读到文字。"
        return text, pages, False, why
    return text, pages, True, note


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
