"""Plain text out of Rich Text Format (``.rtf``) files without external Office binaries.

Handles ``\\ansicpg`` single-byte escapes (``\\'xx``), signed 16-bit Unicode escapes (``\\uN`` with
``\\ucK`` fallback byte skipping), paragraph/line/table breaks, and nested header groups
(``\\fonttbl``, ``\\colortbl``, ``\\stylesheet``, ``\\info``, ``\\*``).
"""

from __future__ import annotations

import codecs
import re


class RtfError(ValueError):
    pass


_SKIP_DESTINATIONS = frozenset(
    {
        "fonttbl",
        "colortbl",
        "stylesheet",
        "info",
        "pict",
        "object",
        "datafield",
        "fldinst",
        "header",
        "footer",
        "headerl",
        "headerr",
        "headerf",
        "footerl",
        "footerr",
        "footerf",
        "footnote",
        "xmlopen",
        "revtbl",
        "listtable",
        "listoverridetable",
        "rsidtbl",
        "generator",
    }
)

_ANSICPG_RE = re.compile(rb"\\ansicpg(\d{3,5})\b")
_CTRL_TOKEN_RE = re.compile(
    r"\\(?:"
    r"'([0-9a-fA-F]{2})"
    r"|([a-zA-Z]+)(-?\d+)?[ ]?"
    r"|([^a-zA-Z0-9\r\n])"
    r")"
    r"|([{}])"
    r"|([\r\n]+)"
    r"|([^\\{}\r\n]+)"
)


def is_rtf(content: bytes) -> bool:
    head = content[:64].lstrip()
    return head.startswith(b"{\\rtf")


def _resolve_encoding(content: bytes) -> str:
    m = _ANSICPG_RE.search(content[:2048])
    if m:
        cp = f"cp{m.group(1).decode('ascii')}"
        try:
            codecs.lookup(cp)
            return cp
        except LookupError:
            pass
    return "cp1252"


def rtf_text(content: bytes) -> str:
    if not is_rtf(content):
        raise RtfError("不是有效的 RTF 文档")
    if len(content) > 64 * 1024 * 1024:
        raise RtfError("RTF 文件体积超出安全上限")

    enc = _resolve_encoding(content)
    raw = content.decode("latin1")

    stack: list[tuple[int, bool]] = []
    uc_skip = 1
    skip_group = False
    pending_star = False
    skip_chars = 0
    hex_buf = bytearray()
    out: list[str] = []

    def flush_hex() -> None:
        if hex_buf:
            out.append(bytes(hex_buf).decode(enc, errors="replace"))
            hex_buf.clear()

    for m in _CTRL_TOKEN_RE.finditer(raw):
        hex_byte, word, arg_str, sym, brace, newline, literal = m.groups()

        if brace == "{":
            flush_hex()
            stack.append((uc_skip, skip_group))
            pending_star = False
            continue
        if brace == "}":
            flush_hex()
            if stack:
                uc_skip, skip_group = stack.pop()
            pending_star = False
            skip_chars = 0
            continue
        if newline is not None:
            continue

        if sym is not None:
            if sym == "*":
                pending_star = True
                continue
            pending_star = False
            if skip_group:
                continue
            if skip_chars > 0:
                skip_chars -= 1
                continue
            flush_hex()
            if sym in ("\\", "{", "}"):
                out.append(sym)
            elif sym == "~":
                out.append("\u00a0")
            elif sym == "_":
                out.append("-")
            elif sym in ("\r", "\n"):
                out.append("\n")
            continue

        if word is not None:
            w = word.lower()
            if pending_star or w in _SKIP_DESTINATIONS:
                flush_hex()
                skip_group = True
                pending_star = False
                continue
            pending_star = False
            if skip_group:
                continue
            if w == "uc" and arg_str is not None:
                flush_hex()
                uc_skip = max(0, min(16, int(arg_str)))
                continue
            if w == "u" and arg_str is not None:
                flush_hex()
                code = int(arg_str)
                if code < 0:
                    code += 65536
                if 0 <= code <= 0x10FFFF and not (0xD800 <= code <= 0xDFFF):
                    out.append(chr(code))
                skip_chars = uc_skip
                continue
            if skip_chars > 0:
                # An ANSI escape used as a \u fallback counts as 1 skipped representation unit
                skip_chars = 0
            flush_hex()
            if w in ("par", "line", "sect", "page", "row"):
                out.append("\n")
            elif w in ("tab", "cell"):
                out.append("\t")
            elif w == "emdash":
                out.append("—")
            elif w == "endash":
                out.append("–")
            elif w in ("lquote", "rquote"):
                out.append("’" if w == "rquote" else "‘")
            elif w in ("ldblquote", "rdblquote"):
                out.append("”" if w == "rdblquote" else "“")
            elif w == "bullet":
                out.append("•")
            continue

        pending_star = False
        if skip_group:
            continue

        if hex_byte is not None:
            if skip_chars > 0:
                skip_chars -= 1
                continue
            hex_buf.append(int(hex_byte, 16))
            continue

        if literal is not None:
            flush_hex()
            if skip_chars > 0:
                if len(literal) <= skip_chars:
                    skip_chars -= len(literal)
                    continue
                literal = literal[skip_chars:]
                skip_chars = 0
            out.append(literal)

    flush_hex()
    text = "".join(out)
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", " ", text)
    return text


def rtf_paragraphs(content: bytes) -> list[str]:
    text = rtf_text(content)
    return [" ".join(line.split()) for line in text.splitlines() if line.strip()]
