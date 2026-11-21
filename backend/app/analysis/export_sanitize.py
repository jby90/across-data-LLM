"""Metadata sanitizer for exported Word (.docx) and PDF (.pdf) documents.

Strips underlying rendering engine identifiers (LibreOffice Writer, Microsoft Word,
WeasyPrint, template author/company fields) from generated deliverables while
preserving document titles, visual layout, page numbering, and PDF xref byte offsets.
Static official form files on disk are never modified.
"""

from __future__ import annotations

import io
import re
import zipfile
from xml.sax.saxutils import escape as _xml_escape

_XML_BAD_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\ufffe\uffff]")

_PDF_STRIP_KEYS = (b"Creator", b"Producer", b"Author", b"Subject", b"Keywords")
_PDF_INFO_REF_RE = re.compile(rb"/Info\s+(\d+)\s+(\d+)\s+R\b")
_XMP_BLOCK_RE = re.compile(rb"<x:xmpmeta\b[^>]*>.*?</x:xmpmeta>", re.S)
_XMP_TAG_PATTERNS = (
    re.compile(rb"<pdf:Producer>.*?</pdf:Producer>", re.S),
    re.compile(rb"<xmp:CreatorTool>.*?</xmp:CreatorTool>", re.S),
    re.compile(rb"<dc:creator>.*?</dc:creator>", re.S),
    re.compile(rb"<pdf:Keywords>.*?</pdf:Keywords>", re.S),
    re.compile(rb"<dc:description>.*?</dc:description>", re.S),
)


def _clean_xml_str(text: str) -> str:
    return _XML_BAD_RE.sub("", text or "")


def clean_core_xml(title: str = "") -> bytes:
    safe = _xml_escape(_clean_xml_str(title or "").strip())
    cp_ns = "http://schemas.openxmlformats.org/package/2006/metadata/core-properties"
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<cp:coreProperties xmlns:cp="{cp_ns}" '
        'xmlns:dc="http://purl.org/dc/elements/1.1/" '
        'xmlns:dcterms="http://purl.org/dc/terms/" '
        'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
        f"<dc:title>{safe}</dc:title>"
        "<dc:creator></dc:creator>"
        "<cp:lastModifiedBy></cp:lastModifiedBy>"
        "</cp:coreProperties>"
    ).encode()


def clean_app_xml() -> bytes:
    return (
        b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        b'<Properties xmlns="http://schemas.openxmlformats.org/'
        b'officeDocument/2006/extended-properties">'
        b"<Application></Application>"
        b"<Company></Company>"
        b"</Properties>"
    )


def _extract_existing_docx_title(zin: zipfile.ZipFile) -> str:
    if "docProps/core.xml" not in zin.namelist():
        return ""
    raw = zin.read("docProps/core.xml").decode("utf-8", errors="ignore")
    m = re.search(r"<dc:title[^>]*>(.*?)</dc:title>", raw, re.S)
    return (m.group(1) if m else "").strip()


def sanitize_docx_metadata(docx_bytes: bytes, title: str = "") -> bytes:
    """Replace docProps/core.xml and docProps/app.xml inside a .docx archive with clean metadata."""
    try:
        zin = zipfile.ZipFile(io.BytesIO(docx_bytes), "r")
    except zipfile.BadZipFile:
        return docx_bytes
    with zin:
        final_title = title.strip() or _extract_existing_docx_title(zin) or "数据合规正式文件"
        buf = io.BytesIO()
        has_core = False
        has_app = False
        with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zout:
            for item in zin.infolist():
                if item.filename == "docProps/core.xml":
                    has_core = True
                    zout.writestr(item, clean_core_xml(final_title))
                elif item.filename == "docProps/app.xml":
                    has_app = True
                    zout.writestr(item, clean_app_xml())
                else:
                    zout.writestr(item, zin.read(item.filename))
            if not has_core:
                zout.writestr("docProps/core.xml", clean_core_xml(final_title))
            if not has_app:
                zout.writestr("docProps/app.xml", clean_app_xml())
        return buf.getvalue()


def _scan_pdf_string_end(data: bytes, pos: int) -> int | None:
    """Return index just past the closing delimiter of a PDF literal or hex string."""
    n = len(data)
    if pos >= n:
        return None
    ch = data[pos : pos + 1]
    if ch == b"<":
        if pos + 1 < n and data[pos + 1 : pos + 2] == b"<":
            return None  # dictionary, not hex string
        end = data.find(b">", pos + 1)
        return end + 1 if end != -1 else None
    if ch == b"(":
        depth = 1
        i = pos + 1
        while i < n and depth > 0:
            c = data[i]
            if c == 0x5C:  # backslash escape
                i += 2
                continue
            if c == 0x28:  # '('
                depth += 1
            elif c == 0x29:  # ')'
                depth -= 1
            i += 1
        return i if depth == 0 else None
    return None


def _scrub_info_object(obj_bytes: bytes) -> bytes:
    """Blank out engine metadata in an uncompressed PDF /Info object (fixed byte length)."""
    buf = bytearray(obj_bytes)
    n = len(buf)
    for key in _PDF_STRIP_KEYS:
        token = b"/" + key
        start = 0
        while True:
            idx = bytes(buf).find(token, start)
            if idx == -1:
                break
            after_key = idx + len(token)
            # Ensure exact key boundary (not prefix of a longer name)
            if after_key < n and buf[after_key : after_key + 1] not in (
                b" ", b"\t", b"\r", b"\n", b"(", b"<", b"/",
            ):
                start = after_key
                continue
            val_pos = after_key
            while val_pos < n and buf[val_pos] in (0x20, 0x09, 0x0D, 0x0A):
                val_pos += 1
            val_end = _scan_pdf_string_end(bytes(buf), val_pos)
            if val_end is None:
                start = after_key
                continue
            span = val_end - idx
            replacement = token + b"()"
            if len(replacement) <= span:
                padded = replacement + b" " * (span - len(replacement))
                buf[idx:val_end] = padded
            start = val_end
    return bytes(buf)


def _scrub_xmp_block(xmp_bytes: bytes) -> bytes:
    """Blank out engine tags inside an uncompressed XMP block without changing its byte length."""
    orig_len = len(xmp_bytes)
    out = xmp_bytes
    for pat in _XMP_TAG_PATTERNS:
        out = pat.sub(b"", out)
    if len(out) == orig_len:
        return xmp_bytes
    pad = orig_len - len(out)
    close_tag = b"</x:xmpmeta>"
    idx = out.rfind(close_tag)
    if idx == -1 or pad < 0:
        return xmp_bytes
    return out[:idx] + (b" " * pad) + out[idx:]


def sanitize_pdf_metadata(pdf_bytes: bytes) -> bytes:
    """Strip /Creator, /Producer, /Author, /Subject, /Keywords from PDF Info & XMP while
    preserving exact byte offsets so all xref tables and stream /Length values stay 100% valid."""
    if not pdf_bytes or not pdf_bytes.startswith(b"%PDF"):
        return pdf_bytes
    out = bytearray(pdf_bytes)

    # 1. Scrub every referenced uncompressed /Info indirect object in-place
    refs = _PDF_INFO_REF_RE.findall(pdf_bytes)
    seen_objs: set[tuple[bytes, bytes]] = set()
    for obj_num, gen_num in refs:
        pair = (obj_num, gen_num)
        if pair in seen_objs:
            continue
        seen_objs.add(pair)
        pat = re.compile(
            rb"(?:^|[\r\n])("
            + re.escape(obj_num)
            + rb"\s+"
            + re.escape(gen_num)
            + rb"\s+obj\b.*?endobj)",
            re.S,
        )
        for m in pat.finditer(bytes(out)):
            s, e = m.start(1), m.end(1)
            scrubbed = _scrub_info_object(bytes(out[s:e]))
            if len(scrubbed) == e - s:
                out[s:e] = scrubbed

    # 2. Scrub uncompressed XMP metadata packets (e.g., MS Word PDF exports) in-place
    for m in _XMP_BLOCK_RE.finditer(bytes(out)):
        s, e = m.start(), m.end()
        scrubbed = _scrub_xmp_block(bytes(out[s:e]))
        if len(scrubbed) == e - s:
            out[s:e] = scrubbed

    return bytes(out)
