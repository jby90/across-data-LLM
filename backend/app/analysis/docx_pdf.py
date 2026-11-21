# ruff: noqa: E501
"""PDF = the Word file, converted. Every document the platform offers as both Word and PDF is
written once, as Word; its PDF is that same .docx printed to PDF by an office suite, so the two can
never say different things or look different.

Engines, in order: LibreOffice (``soffice --headless --convert-to pdf``; what the Linux server
uses), then Microsoft Word through PowerShell (Windows desktops where Word is installed). The engine
can be forced with ``CYSH_PDF_ENGINE`` = ``libreoffice`` | ``word`` | ``builtin``; ``builtin`` means
no office suite (the test suite), and the caller falls back to its own renderer.

Conversions are cached by the hash of the Word file and run one at a time (an office suite is one
process); callers in request handlers run them in a worker thread.
"""

from __future__ import annotations

import gc
import hashlib
import io
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import zipfile
from collections import OrderedDict
from pathlib import Path
from xml.etree import ElementTree as ET

from app.analysis.export_sanitize import sanitize_pdf_metadata

_W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_W = "{" + _W_NS + "}"
_CHECKED_SYMS = {"F0FE", "00FE", "F052", "0052"}
_TICK_SYMS = {"F0FC", "00FC", "F076", "0076"}
_BULLET_SYMS = {"F0B7", "00B7"}
_SQUARE_SYMS = {"F0A7", "00A7"}
_PUA_CHAR_MAP = {
    "\uf0b7": "\u2022",
    "\uf0a7": "\u25aa",
    "\uf0fc": "\u2713",
    "\uf076": "\u2713",
    "\uf0fe": "\u2611",
    "\uf052": "\u2611",
    "\uf0a8": "\u2610",
    "\uf06f": "\u2610",
    "\uf02a": "\u2610",
}
_PUA_RE = re.compile(r"[\ue000-\uf8ff]")
_BOX_SPLIT_RE = re.compile(r"([\u2610\u2611\u2612\u2713\u25a0\u25a1\u25aa\u2022]+)")
_XML_SPACE = "{http://www.w3.org/XML/1998/namespace}space"

_LOCK = threading.Lock()
_CACHE: OrderedDict[str, bytes] = OrderedDict()
CACHE_SIZE = 32
TIMEOUT = 180

WORD_SCRIPT = r"""
param([string]$Dir)
$w = New-Object -ComObject Word.Application
$w.Visible = $false
$w.DisplayAlerts = 0
try {
  Get-ChildItem -LiteralPath $Dir -Filter *.docx | ForEach-Object {
    $d = $w.Documents.Open($_.FullName, $false, $false)
    $d.Repaginate()
    foreach ($toc in $d.TablesOfContents) {
      try { $toc.UpdatePageNumbers() } catch {}
    }
    foreach ($s in $d.Sections) {
      foreach ($hf in $s.Headers) {
        try { $hf.Range.Fields.Update() } catch {}
        try { foreach ($sh in $hf.Shapes) { if ($sh.TextFrame.HasText) { $sh.TextFrame.TextRange.Fields.Update() } } } catch {}
      }
      foreach ($hf in $s.Footers) {
        try { $hf.Range.Fields.Update() } catch {}
        try { foreach ($sh in $hf.Shapes) { if ($sh.TextFrame.HasText) { $sh.TextFrame.TextRange.Fields.Update() } } } catch {}
      }
    }
    $d.Repaginate()
    $d.ExportAsFixedFormat(($_.FullName -replace '\.docx$', '.pdf'), 17)
    $d.Close(0)
  }
} finally { $w.Quit() }
"""


class PdfEngineUnavailable(RuntimeError):
    """No office suite to print Word files to PDF on this machine."""


def _soffice() -> str | None:
    return shutil.which("soffice") or shutil.which("libreoffice")


def _word() -> bool:
    if sys.platform != "win32" or not shutil.which("powershell.exe"):
        return False
    roots = [os.environ.get("ProgramFiles", r"C:\Program Files"), os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")]
    return any(Path(r, "Microsoft Office").exists() for r in roots if r)


def engine() -> str:
    """The engine that will be used: libreoffice, word or builtin (none)."""
    forced = (os.environ.get("CYSH_PDF_ENGINE") or "").strip().lower()
    if forced in ("libreoffice", "word", "builtin"):
        return forced
    if _soffice():
        return "libreoffice"
    if _word():
        return "word"
    return "builtin"


def _map_pua_text(text: str) -> str:
    return _PUA_RE.sub(lambda m: _PUA_CHAR_MAP.get(m.group(0), "\u2022"), text)


def _set_rfonts(rpr: ET.Element, font_name: str) -> None:
    rf = rpr.find(_W + "rFonts")
    if rf is None:
        rf = ET.Element(_W + "rFonts")
        rpr.insert(0, rf)
    for attr in ("ascii", "hAnsi", "eastAsia", "cs"):
        rf.set(_W + attr, font_name)
    rf.attrib.pop(_W + "hint", None)


def _rewrite_ooxml_symbols(original_xml: bytes, font_name: str, *, for_libreoffice: bool = False) -> bytes | None:
    xml_text = original_xml.decode("utf-8", errors="ignore")
    has_sym = ":sym" in xml_text or bool(_PUA_RE.search(xml_text)) or bool(_BOX_SPLIT_RE.search(xml_text))
    has_lo_fix = for_libreoffice and (":docGrid" in xml_text or ":u " in xml_text or ":u>" in xml_text)
    if not has_sym and not has_lo_fix:
        return None
    import copy

    for prefix, uri in re.findall(rb'xmlns:([A-Za-z0-9]+)="([^"]+)"', original_xml[:8000]):
        ET.register_namespace(prefix.decode(), uri.decode())
    root = ET.fromstring(original_xml)
    changed = False

    for r in root.iter(_W + "r"):
        children = list(r)
        syms = [c for c in children if c.tag == _W + "sym"]
        pua_ts = [c for c in children if c.tag == _W + "t" and c.text and _PUA_RE.search(c.text)]
        if not syms and not pua_ts:
            continue
        rpr = r.find(_W + "rPr")
        if rpr is None:
            rpr = ET.Element(_W + "rPr")
            r.insert(0, rpr)
        _set_rfonts(rpr, font_name)
        for sym in syms:
            pos = list(r).index(sym)
            code = (sym.get(_W + "char") or "").strip().upper()
            t_el = ET.Element(_W + "t")
            if code in _CHECKED_SYMS:
                t_el.text = "\u2611"
            elif code in _TICK_SYMS:
                t_el.text = "\u2713"
            elif code in _BULLET_SYMS:
                t_el.text = "\u2022"
            elif code in _SQUARE_SYMS:
                t_el.text = "\u25aa"
            else:
                t_el.text = "\u2610"
            r.remove(sym)
            r.insert(pos, t_el)
            changed = True
        for t_el in pua_ts:
            t_el.text = _map_pua_text(t_el.text or "")
            changed = True

    # Ensure literal Unicode ballot/geometric box glyphs inside <w:t> also bind to DejaVu Sans / Segoe UI Symbol
    # without altering the font/weight of adjacent CJK or Latin label text in mixed runs.
    for parent in root.iter():
        runs = [c for c in parent if c.tag == _W + "r"]
        if not runs:
            continue
        idx = 0
        children = list(parent)
        while idx < len(children):
            el = children[idx]
            if el.tag != _W + "r":
                idx += 1
                continue
            r_children = list(el)
            t_nodes = [c for c in r_children if c.tag == _W + "t"]
            if len(t_nodes) != 1 or not t_nodes[0].text or not _BOX_SPLIT_RE.search(t_nodes[0].text):
                idx += 1
                continue
            rpr = el.find(_W + "rPr")
            rf = rpr.find(_W + "rFonts") if rpr is not None else None
            if rf is not None and rf.get(_W + "ascii") == font_name and rf.get(_W + "eastAsia") == font_name:
                idx += 1
                continue
            raw_txt = t_nodes[0].text
            other_non_rpr = [c for c in r_children if c.tag not in (_W + "rPr", _W + "t")]
            parts = [p for p in _BOX_SPLIT_RE.split(raw_txt) if p]
            if len(parts) == 1 and _BOX_SPLIT_RE.fullmatch(parts[0]) and not other_non_rpr:
                if rpr is None:
                    rpr = ET.Element(_W + "rPr")
                    el.insert(0, rpr)
                _set_rfonts(rpr, font_name)
                changed = True
                idx += 1
                continue
            if other_non_rpr:
                idx += 1
                continue
            new_runs: list[ET.Element] = []
            for seg in parts:
                nr = ET.Element(_W + "r")
                nr_rpr = copy.deepcopy(rpr) if rpr is not None else ET.Element(_W + "rPr")
                if _BOX_SPLIT_RE.fullmatch(seg):
                    _set_rfonts(nr_rpr, font_name)
                if len(nr_rpr) or nr_rpr.attrib:
                    nr.append(nr_rpr)
                nt = ET.SubElement(nr, _W + "t")
                nt.text = seg
                if seg[:1].isspace() or seg[-1:].isspace():
                    nt.set(_XML_SPACE, "preserve")
                new_runs.append(nr)
            parent.remove(el)
            for offset_i, nr in enumerate(new_runs):
                parent.insert(idx + offset_i, nr)
            children = list(parent)
            changed = True
            idx += len(new_runs)

    for lvl in root.iter(_W + "lvl"):
        lvl_text = lvl.find(_W + "lvlText")
        if lvl_text is None:
            continue
        val = lvl_text.get(_W + "val") or ""
        if not _PUA_RE.search(val) and not _BOX_SPLIT_RE.search(val):
            continue
        if _PUA_RE.search(val):
            lvl_text.set(_W + "val", _map_pua_text(val))
        rpr = lvl.find(_W + "rPr")
        if rpr is None:
            rpr = ET.Element(_W + "rPr")
            lvl.append(rpr)
        _set_rfonts(rpr, font_name)
        changed = True

    if for_libreoffice:
        # LibreOffice Writer snaps table paragraphs with exact line=320 onto 2x docGrid(linePitch=317)=634 twips
        # when w:docGrid w:type="lines" is active; relaxing type to "default" preserves exact 4-page CAC table layout.
        for dg in root.iter(_W + "docGrid"):
            if dg.get(_W + "type") in ("lines", "linesAndChars"):
                dg.set(_W + "type", "default")
                changed = True
        # LibreOffice suppresses underline rendering on trailing ASCII space-only runs (e.g. "其他________").
        for r in root.iter(_W + "r"):
            rpr = r.find(_W + "rPr")
            u_el = rpr.find(_W + "u") if rpr is not None else None
            if u_el is None or (u_el.get(_W + "val") or "none") == "none":
                continue
            for t_el in r.findall(_W + "t"):
                txt = t_el.text or ""
                if txt and " " in txt and txt.strip(" ") == "":
                    t_el.text = txt.replace(" ", "\u00a0") + "\u200c"
                    changed = True

    if not changed:
        return None
    out_xml = ET.tostring(root, encoding="utf-8", xml_declaration=False)
    m_start = re.search(rb"<([A-Za-z0-9:]+)\b[^>]*>", original_xml)
    if m_start:
        tag_pat = rb"^<" + re.escape(m_start.group(1)) + rb"\b[^>]*>"
        out_xml = re.sub(tag_pat, m_start.group(0), out_xml, count=1)
    return b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\r\n' + out_xml


def _materialize_sym_for_libreoffice(
    docx_bytes: bytes, *, font_name: str = "DejaVu Sans", for_libreoffice: bool = False
) -> bytes:
    """Replace proprietary Wingdings/Symbol ``<w:sym>`` checkboxes and PUA list bullets with standard
    Unicode glyphs (``☑`` U+2611, ``☐`` U+2610, ``•`` U+2022, ``▪`` U+25AA, ``✓`` U+2713) across
    document, numbering, styles, and header/footer XML parts so converted PDFs contain zero PUA codepoints."""
    try:
        zin = zipfile.ZipFile(io.BytesIO(docx_bytes))
    except zipfile.BadZipFile:
        return docx_bytes
    names = set(zin.namelist())
    if "word/document.xml" not in names:
        return docx_bytes

    replacements: dict[str, bytes] = {}
    for name in zin.namelist():
        if not name.startswith("word/") or not name.endswith(".xml"):
            continue
        base = name.rsplit("/", 1)[-1]
        if base in ("document.xml", "numbering.xml", "styles.xml", "footnotes.xml", "endnotes.xml") or base.startswith(("header", "footer")):
            rewritten = _rewrite_ooxml_symbols(zin.read(name), font_name, for_libreoffice=for_libreoffice)
            if rewritten is not None:
                replacements[name] = rewritten

    if not replacements:
        return docx_bytes
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            zout.writestr(item, replacements.get(item.filename) or zin.read(item.filename))
    return buf.getvalue()


def _convert_dir(folder: Path, kind: str) -> None:
    try:
        if kind == "libreoffice":
            files = sorted(str(p) for p in folder.glob("*.docx"))
            soffice_bin = _soffice() or "soffice"
            for idx in range(0, len(files), 2):
                batch = files[idx : idx + 2]
                with tempfile.TemporaryDirectory(prefix="cysh-lo-prof-") as prof:
                    user_install = f"-env:UserInstallation={Path(prof).resolve().as_uri()}"
                    subprocess.run(
                        [soffice_bin, user_install, "--headless", "--norestore", "--convert-to", "pdf", "--outdir", str(folder), *batch],
                        check=True, capture_output=True, timeout=TIMEOUT,
                    )
            return
        script = folder / "convert.ps1"
        script.write_text(WORD_SCRIPT, encoding="utf-8")
        subprocess.run(
            ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script), "-Dir", str(folder)],
            check=True, capture_output=True, timeout=TIMEOUT,
        )
    except (subprocess.SubprocessError, OSError) as exc:
        raise PdfEngineUnavailable("Word 转 PDF 失败") from exc


def docx_to_pdf_many(files: list[bytes]) -> list[bytes]:
    """The PDF of each Word file, in order (batched office-suite run for all that are not cached)."""
    if not files:
        return []
    kind = engine()
    if kind == "builtin":
        raise PdfEngineUnavailable("本机没有可把 Word 转成 PDF 的办公软件")
    keys = [hashlib.sha256(f).hexdigest() for f in files]
    sym_font = "DejaVu Sans" if kind == "libreoffice" else "Segoe UI Symbol"
    with _LOCK:
        out: dict[str, bytes] = {}
        todo: list[tuple[str, bytes]] = []
        seen_todo: set[str] = set()
        for k, f in zip(keys, files, strict=True):
            if k in _CACHE:
                _CACHE.move_to_end(k)
                out[k] = _CACHE[k]
            elif k not in seen_todo:
                seen_todo.add(k)
                todo.append((k, f))
        if todo:
            with tempfile.TemporaryDirectory(prefix="cysh-pdf-") as tmp:
                folder = Path(tmp)
                for i, (_, data) in enumerate(todo):
                    payload = _materialize_sym_for_libreoffice(
                        data, font_name=sym_font, for_libreoffice=(kind == "libreoffice")
                    )
                    (folder / f"{i:03d}.docx").write_bytes(payload)
                _convert_dir(folder, kind)
                for i, (key, _) in enumerate(todo):
                    pdf = folder / f"{i:03d}.pdf"
                    if not pdf.exists():
                        raise PdfEngineUnavailable("Word 转 PDF 失败")
                    pdf_bytes = sanitize_pdf_metadata(pdf.read_bytes())
                    out[key] = pdf_bytes
                    _CACHE[key] = pdf_bytes
                    while len(_CACHE) > CACHE_SIZE:
                        _CACHE.popitem(last=False)
            gc.collect()
        for k in keys:
            if k in _CACHE:
                _CACHE.move_to_end(k)
        return [out[k] for k in keys]


def docx_to_pdf(data: bytes) -> bytes:
    return docx_to_pdf_many([data])[0]
