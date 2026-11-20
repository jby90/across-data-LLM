"""Safe Excel (.xlsx) and CSV cell writers preventing CWE-1236 formula injection
and openpyxl XML <f> formula corruption on strings starting with '='."""

from __future__ import annotations

import re

from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE

_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")
_ACTIVE_FORMULA_RE = re.compile(r"^\s*[=+@-]\s*[A-Za-z_][A-Za-z0-9_.]*\s*[\(|!]")


def sanitize_csv_cell(value: object) -> str:
    """Return a CSV-safe string neutralizing CWE-1236 spreadsheet formula execution."""
    if value is None:
        return ""
    s = ILLEGAL_CHARACTERS_RE.sub("", str(value))
    if s and (s.startswith(_FORMULA_PREFIXES) or s.lstrip(" ").startswith(_FORMULA_PREFIXES)):
        return "'" + s
    return s


def write_safe_xlsx_cell(
    ws,
    row: int,
    col: int,
    value: object,
    *,
    neutralize_formula_prefix: bool = False,
):
    """Write a cell to an openpyxl worksheet without ever emitting an XML <f> formula node."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return ws.cell(row, col, value)
    s = ILLEGAL_CHARACTERS_RE.sub("", str(value)) if value is not None else ""
    has_prefix = bool(
        s and (s.startswith(_FORMULA_PREFIXES) or s.lstrip(" ").startswith(_FORMULA_PREFIXES))
    )
    if (neutralize_formula_prefix and has_prefix) or _ACTIVE_FORMULA_RE.match(s):
        s = "'" + s
    cell = ws.cell(row, col)
    cell.value = s
    cell.data_type = "s"
    if has_prefix:
        cell.quotePrefix = True
    return cell
