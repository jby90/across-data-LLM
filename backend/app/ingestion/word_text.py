"""Plain text out of Word files, without Word: ``.docx`` (zipped XML) and ``.doc`` (binary).

Only text is read (paragraph by paragraph); layout, images and tracked changes are not needed
for the clause splitter. ``.doc`` uses the document's own piece table, so text stored as
compressed 8-bit or as UTF-16 comes out right (Arabic and French laws alike).
"""

from __future__ import annotations

import io
import re
import struct
import zipfile
from xml.etree import ElementTree

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


class WordError(ValueError):
    pass


def is_docx(content: bytes) -> bool:
    if not content.startswith(b"PK"):
        return False
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as z:
            return "word/document.xml" in z.namelist()
    except zipfile.BadZipFile:
        return False


def is_doc(content: bytes) -> bool:
    return content.startswith(OLE_MAGIC)


def docx_paragraphs(content: bytes) -> list[str]:
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as z:
            root = ElementTree.fromstring(z.read("word/document.xml"))
    except (zipfile.BadZipFile, KeyError, ElementTree.ParseError) as exc:
        raise WordError("无法读取 Word 文件") from exc
    out: list[str] = []
    for p in root.iter(f"{_W}p"):
        parts: list[str] = []
        for node in p.iter():
            if node.tag == f"{_W}t" and node.text:
                parts.append(node.text)
            elif node.tag == f"{_W}tab":
                parts.append("\t")
            elif node.tag in (f"{_W}br", f"{_W}cr"):
                parts.append("\n")
        text = "".join(parts).strip()
        if text:
            out.append(text)
    return out


def _doc_text(content: bytes) -> str:
    try:
        import olefile
    except ImportError as exc:  # pragma: no cover - dependency is declared
        raise WordError("缺少读取 .doc 的组件") from exc
    try:
        ole = olefile.OleFileIO(io.BytesIO(content))
    except OSError as exc:
        raise WordError("无法读取 Word 文件") from exc
    with ole:
        word = ole.openstream("WordDocument").read()
        flags = struct.unpack_from("<H", word, 0x0A)[0]
        table_name = "1Table" if flags & 0x0200 else "0Table"
        table = ole.openstream(table_name).read()
        fc_clx = struct.unpack_from("<I", word, 0x01A2)[0]
        lcb_clx = struct.unpack_from("<I", word, 0x01A6)[0]
    clx = table[fc_clx : fc_clx + lcb_clx]
    pos = 0
    while pos < len(clx) and clx[pos] == 1:  # skip property modifiers
        pos += 3 + struct.unpack_from("<H", clx, pos + 1)[0]
    if pos >= len(clx) or clx[pos] != 2:
        raise WordError("Word 文件的文字结构无法识别")
    size = struct.unpack_from("<I", clx, pos + 1)[0]
    plc = clx[pos + 5 : pos + 5 + size]
    pieces = (size - 4) // 12
    cps = struct.unpack_from(f"<{pieces + 1}I", plc, 0)
    chunks: list[str] = []
    for i in range(pieces):
        fc_raw = struct.unpack_from("<I", plc, 4 * (pieces + 1) + 8 * i + 2)[0]
        count = cps[i + 1] - cps[i]
        if fc_raw & 0x40000000:  # compressed: one byte per character (Windows-1252)
            start = (fc_raw & 0x3FFFFFFF) // 2
            chunks.append(word[start : start + count].decode("cp1252", "replace"))
        else:
            start = fc_raw & 0x3FFFFFFF
            chunks.append(word[start : start + 2 * count].decode("utf-16-le", "replace"))
    return "".join(chunks)


def doc_paragraphs(content: bytes) -> list[str]:
    text = _doc_text(content)
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x13\x14\x15]", " ", text.replace("\r", "\n"))
    return [line.strip() for line in text.split("\n") if line.strip()]


def word_paragraphs(content: bytes) -> list[str]:
    return docx_paragraphs(content) if is_docx(content) else doc_paragraphs(content)
