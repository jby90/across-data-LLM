# ruff: noqa: E501
"""Put values onto an official PDF form without changing the form.

The official file is the thing an organisation signs and stamps, so its layout, wording, numbering
and headers must come out exactly as published. The values are written onto the page at the
position of each form field (text boxes, check boxes, radio buttons); nothing else on the page is
touched. A value that does not fit its box is made smaller, then wrapped when the box is tall
enough; when it still does not fit it is NOT written (it would run into the next printed label)
and is reported, so the caller can ask for a shorter text. Nothing is ever cut silently.

Only the PDF library the platform already uses (pypdfium2) and the CJK font it already ships are
needed, so the form can carry Chinese company names as well as the form's own language."""

from __future__ import annotations

import ctypes
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import pypdfium2 as pdfium
import pypdfium2.raw as raw

from app.ingestion.parsing import _PDFIUM_LOCK

_FONTS_DIR = Path(__file__).resolve().parents[1] / "analysis" / "fonts"


def _resolve_regular_font_path() -> Path:
    for cand in (
        Path("/usr/local/share/fonts/cyshuhe/NotoSansSC-Regular.ttf"),
        _FONTS_DIR / "SourceHanSansCN-Medium.ttf",
    ):
        if cand.exists():
            return cand
    var_ttf = _FONTS_DIR / "notosanssc" / "NotoSansSC[wght].ttf"
    cached = Path(tempfile.gettempdir()) / "cyshuhe-fonts-cache" / "NotoSansSC-Regular-400.ttf"
    if cached.exists() and cached.stat().st_size > 1_000_000:
        return cached
    try:
        from app.analysis.fonts.build_static_faces import build_static_faces

        out_dir = cached.parent
        build_static_faces(var_ttf.parent, out_dir)
        reg = out_dir / "NotoSansSC-Regular.ttf"
        if reg.exists():
            reg.replace(cached)
            return cached
    except Exception:
        pass
    return var_ttf


FONT_PATH = _resolve_regular_font_path()
START_SIZE = 9.0
MIN_SIZE = 5.5
PAD = 2.0
TEXT, CHECK, RADIO = "text", "check", "radio"
_TYPES = {6: TEXT, 2: CHECK, 3: RADIO}


@dataclass(frozen=True)
class FormField:
    """One control of the form. ``key`` is unique within the form: the PDF name, plus ``#n`` for
    the n-th control sharing that name (every option of a radio group has the same name)."""

    key: str
    name: str
    kind: str
    page: int  # 0-based
    left: float
    bottom: float
    right: float
    top: float
    # 没有表单控件、只有印刷栏位的官方表（如突尼斯 INPDP 表）：栏位由目录给出坐标，以下三项说明栏位形状
    cells: int = 0  # 逐格填写（电话号码等）：每个字符居中放在一格里
    rules: tuple[float, ...] = ()  # 多条横线：每条线上写一行，值为各行的基线位置
    segments: tuple[tuple[float, float], ...] = ()  # 被印刷符号隔开的两段（邮箱的 @ 前后）
    erase: bool = False  # 境外接收方邮编/电话等不等长栏位：用白底遮去框内印刷细竖线，保留底边与两端竖线

    @property
    def width(self) -> float:
        return self.right - self.left

    @property
    def height(self) -> float:
        return self.top - self.bottom


@dataclass
class FillReport:
    placed: list[str] = field(default_factory=list)
    shrunk: list[str] = field(default_factory=list)
    wrapped: list[str] = field(default_factory=list)
    overflow: list[str] = field(default_factory=list)  # still too long at the smallest size
    unknown: list[str] = field(default_factory=list)  # asked for, but not a control of this form
    unwritable: list[str] = field(default_factory=list)  # holds characters the font cannot draw (Arabic, …): not written


def _name(fn, *args) -> str:
    n = fn(*args, None, 0)
    if n <= 2:
        return ""
    buf = (ctypes.c_ushort * (n // 2 + 1))()
    fn(*args, buf, n)
    return ctypes.string_at(buf, n - 2).decode("utf-16-le")


def list_fields(pdf_bytes: bytes) -> list[FormField]:
    with _PDFIUM_LOCK:
        pdf = pdfium.PdfDocument(pdf_bytes)
        try:
            pdf.init_forms()
            if pdf.formenv is None:  # 文件里没有表单（只有印刷栏位）
                return []
            handle = pdf.formenv.raw
            seen: dict[str, int] = {}
            out: list[FormField] = []
            for index in range(len(pdf)):
                page = pdf[index]
                try:
                    for j in range(raw.FPDFPage_GetAnnotCount(page.raw)):
                        annot = raw.FPDFPage_GetAnnot(page.raw, j)
                        try:
                            if raw.FPDFAnnot_GetSubtype(annot) != raw.FPDF_ANNOT_WIDGET:
                                continue
                            kind = _TYPES.get(raw.FPDFAnnot_GetFormFieldType(handle, annot))
                            if kind is None:
                                continue
                            name = _name(raw.FPDFAnnot_GetFormFieldName, handle, annot)
                            rect = raw.FS_RECTF()
                            raw.FPDFAnnot_GetRect(annot, rect)
                            n = seen.get(name, 0)
                            seen[name] = n + 1
                            out.append(
                                FormField(
                                    key=name if n == 0 else f"{name}#{n}",
                                    name=name,
                                    kind=kind,
                                    page=index,
                                    left=rect.left,
                                    bottom=rect.bottom,
                                    right=rect.right,
                                    top=rect.top,
                                )
                            )
                        finally:
                            raw.FPDFPage_CloseAnnot(annot)
                finally:
                    page.close()
            return out
        finally:
            pdf.close()


def _utf16(text: str):
    data = (text + "\0").encode("utf-16-le")
    return (ctypes.c_ushort * (len(data) // 2)).from_buffer_copy(data)


def _text_object(pdf, font, size: float, text: str):
    obj = raw.FPDFPageObj_CreateTextObj(pdf.raw, font, size)
    raw.FPDFText_SetText(obj, _utf16(text))
    return obj


def _width(obj) -> float:
    left, bottom, right, top = (ctypes.c_float() for _ in range(4))
    raw.FPDFPageObj_GetBounds(obj, left, bottom, right, top)
    return right.value - left.value


def _wrap(pdf, font, size: float, text: str, avail: float) -> list[str]:
    lines: list[str] = []
    for paragraph in text.splitlines() or [""]:
        words, current = paragraph.split(" "), ""
        for word in words:
            trial = f"{current} {word}".strip()
            probe = _text_object(pdf, font, size, trial)
            fits = _width(probe) <= avail
            raw.FPDFPageObj_Destroy(probe)
            if fits or not current:
                current = trial
            else:
                lines.append(current)
                current = word
        lines.append(current)
    return lines


_COMMON = set("0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ .,;:-/()@+'’\"àâäçéèêëîïôöùûüÿœæÀÂÇÉÈÊÎÔÙÛ")


_COVERED: dict[str, frozenset[int]] = {}


def _covers(path: Path, text: str) -> bool:
    """Whether the font draws every character of ``text`` (spaces aside). Arabic needs joining and
    right-to-left shaping that a single text object does not do either, so it is never written here."""
    if str(path) not in _COVERED:
        try:
            from fontTools.ttLib import TTFont

            _COVERED[str(path)] = frozenset(TTFont(str(path), lazy=True).getBestCmap())
        except Exception:  # noqa: BLE001 - 读不出字表就不拦
            _COVERED[str(path)] = frozenset()
    have = _COVERED[str(path)]
    if not have:
        return True
    return all(ord(ch) in have and not ("\u0590" <= ch <= "\u08ff") for ch in text if not ch.isspace())


def _subset_font(path: Path, chars: set[str]) -> bytes:
    """The font with only the characters this form needs. The full CJK font is 8 MB and would be
    embedded whole in every exported form; the subset keeps what is written (and the common Latin
    range) so the file stays a few hundred KB. On any failure the whole font is used."""
    try:
        import io

        from fontTools import subset

        options = subset.Options()
        options.layout_features = ["*"]
        options.name_IDs = ["*"]
        options.notdef_outline = True
        font = subset.load_font(str(path), options)
        sub = subset.Subsetter(options)
        sub.populate(text="".join(sorted(chars)))
        sub.subset(font)
        buf = io.BytesIO()
        subset.save_font(font, buf, options)
        return buf.getvalue()
    except Exception:  # noqa: BLE001 - 子集失败就用完整字体，宁可文件大也不能写不出字
        return path.read_bytes()


def max_chars_for(f: FormField) -> int:
    """At most how many characters a text box can take: ``fill_pdf`` shrinks a value down to 5.5 pt on one
    line, or wraps it at 6.5 pt when the box is tall enough for two lines; measured for Latin text and digits
    (about 3.3 pt a character at 5.5 pt). A wider script that still does not fit is reported at export,
    never cut silently. Never below the 9 pt estimate the forms used before."""
    if f.segments:  # 印着 @ 分成两段：两段各自写得下的字数，加上 @
        return sum(max(1, int((right - left - 2 * PAD) / 3.3)) for left, right in f.segments) + 1
    w = f.right - f.left - 2 * PAD
    normal = max(4, int((f.right - f.left) / 4.6)) * (2 if f.height > 20 else 1)
    one_line = int(w / 3.6)
    two_lines = int(w / 4.2) * 2 if f.height >= 18 else 0  # 只有真正两行高的格子；单行写字线不折行
    return max(4, normal, one_line, two_lines)


RULE_CHARS = set(" \t\r\n.…·_-–—‒‑:")


def _printed_inside(tp, f: FormField) -> bool:
    """Whether the form prints words inside the field's box (a placeholder such as “(Not Applicable)”),
    as opposed to only the dotted / dashed line one writes on."""
    return _letters_in(tp, f.left, f.bottom, f.right, f.top) >= 2


def _letters_in(tp, left: float, bottom: float, right: float, top: float) -> int:
    """Printed letters / digits whose centre lies in the box (punctuation and rule marks do not count)."""
    n = 0
    for i in range(tp.count_chars()):
        ch = tp.get_text_range(i, 1)
        if not ch or not ch.isalnum():
            continue
        b = tp.get_charbox(i)
        if left < (b[0] + b[2]) / 2 < right and bottom < (b[1] + b[3]) / 2 < top:
            n += 1
    return n


def _place_shaped(pdf, font, page, f: FormField, value: str, put, tp=None) -> bool:
    """Write into a printed field that is not one box: comb cells, ruled lines, or two segments.
    False when the value does not fit (nothing is written then)."""
    text = " ".join(value.split())
    if f.cells:
        digits = text.replace(" ", "").replace("-", "")
        for prefix in ("+216", "00216"):
            if digits.startswith(prefix) and len(digits) > f.cells:
                digits = digits[len(prefix) :]
        if not digits:
            return False
        pitch = f.width / f.cells
        extra = len(digits) - f.cells
        if (
            f.cells == 3
            and 0 < extra <= 2
            and digits.isdigit()
            and tp is not None
            and _letters_in(tp, f.right, f.bottom, f.right + pitch * extra, f.top) == 0
        ):
            # 比格数多一两位（部分旧版原表邮编只印了 3 格、实际邮编为 4 位数字）：按同格距写在右侧空白处
            for n, ch in enumerate(digits):
                obj = _text_object(pdf, font, START_SIZE, ch)
                put(page, obj, f.left + pitch * n + (pitch - _width(obj)) / 2, f.bottom + 2.0)
            return True
        if len(digits) > f.cells:
            return False
        for n, ch in enumerate(digits):
            obj = _text_object(pdf, font, START_SIZE, ch)
            put(page, obj, f.left + pitch * n + (pitch - _width(obj)) / 2, f.bottom + 2.0)
        return True
    if f.segments:
        parts = text.split("@", 1)
        if len(parts) != 2 or not parts[0] or not parts[1]:
            return False
        placed = []
        for part, (left, right) in zip(parts, f.segments, strict=True):
            size = START_SIZE
            while True:
                probe = _text_object(pdf, font, size, part)
                fits = _width(probe) <= right - left - 2 * PAD
                raw.FPDFPageObj_Destroy(probe)
                if fits or size <= MIN_SIZE:
                    break
                size -= 0.5
            if not fits:
                return False
            placed.append((part, size, left))
        for part, size, left in placed:
            put(page, _text_object(pdf, font, size, part), left + PAD, f.bottom + 1.5)
        return True
    size = START_SIZE - 1.0
    lines = _wrap(pdf, font, size, text, f.width - 2 * PAD)
    if len(lines) > len(f.rules):
        return False
    for line, base in zip(lines, f.rules, strict=False):
        put(page, _text_object(pdf, font, size, line), f.left + PAD, base + 1.5)
    return True


def _drop_widgets(pdf, keys: set[str], page_of) -> None:
    """Remove the text-field widgets whose key (name, ``#n`` for the n-th of that name) was filled."""

    handle = pdf.formenv.raw if pdf.formenv is not None else None
    if handle is None:
        return
    seen: dict[str, int] = {}
    for index in range(len(pdf)):
        page = page_of(index)
        drop = []
        for j in range(raw.FPDFPage_GetAnnotCount(page.raw)):
            annot = raw.FPDFPage_GetAnnot(page.raw, j)
            try:
                if raw.FPDFAnnot_GetSubtype(annot) != raw.FPDF_ANNOT_WIDGET or _TYPES.get(raw.FPDFAnnot_GetFormFieldType(handle, annot)) is None:
                    continue
                name = _name(raw.FPDFAnnot_GetFormFieldName, handle, annot)
                n = seen.get(name, 0)
                seen[name] = n + 1
                if (name if n == 0 else f"{name}#{n}") in keys and _TYPES.get(raw.FPDFAnnot_GetFormFieldType(handle, annot)) == TEXT:
                    drop.append(j)
            finally:
                raw.FPDFPage_CloseAnnot(annot)
        for j in reversed(drop):
            raw.FPDFPage_RemoveAnnot(page.raw, j)


def fill_pdf(
    pdf_bytes: bytes,
    texts: dict[str, str],
    checked: set[str] | None = None,
    font_path: Path = FONT_PATH,
    fields: list[FormField] | None = None,
) -> tuple[bytes, FillReport]:
    """``texts``: field key -> value. ``checked``: keys of check boxes / radio options to mark.
    ``fields``: the form's controls when the file has none of its own (see ``FormField``)."""
    with _PDFIUM_LOCK:
        return _fill_pdf_locked(pdf_bytes, texts, checked, font_path, fields)


def _fill_pdf_locked(
    pdf_bytes: bytes,
    texts: dict[str, str],
    checked: set[str] | None = None,
    font_path: Path = FONT_PATH,
    fields: list[FormField] | None = None,
) -> tuple[bytes, FillReport]:
    checked = checked or set()
    fixed = fields  # 目录给出坐标的表（文件本身没有控件）
    fields = {f.key: f for f in (list_fields(pdf_bytes) if fields is None else fields)}
    report = FillReport()
    pdf = pdfium.PdfDocument(pdf_bytes)
    if fixed is None:
        pdf.init_forms()  # 要在取页面之前；用来找出写了值的控件
    font_bytes = _subset_font(Path(font_path), set("".join(texts.values())) | {"✓", "X"} | _COMMON)
    font_buf = (ctypes.c_ubyte * len(font_bytes)).from_buffer_copy(font_bytes)
    font = raw.FPDFText_LoadFont(pdf.raw, font_buf, len(font_bytes), raw.FPDF_FONT_TRUETYPE, True)

    def put(page, obj, x: float, y: float) -> None:
        raw.FPDFPageObj_Transform(obj, 1, 0, 0, 1, x, y)
        raw.FPDFPage_InsertObject(page.raw, obj)

    # 同一页必须始终用同一个页面对象：每次 pdf[i] 取到的是新对象，插入的内容不会汇到一起
    pages: dict[int, object] = {}

    def page_of(index: int):
        if index not in pages:
            pages[index] = pdf[index]
        return pages[index]

    # 原表印的字：在写入任何值之前取（之后取会把写进去的值也算进来）
    printed: dict[int, str] = {}

    def text_of(index: int):
        if index not in printed:
            printed[index] = page_of(index).get_textpage()
        return printed[index]

    pages_touched: set[int] = set()

    for key, value in texts.items():
        f = fields.get(key)
        value = (value or "").strip()
        if f is None or f.kind != TEXT:
            report.unknown.append(key)
            continue
        if not value:
            continue
        if not _covers(Path(font_path), value):
            report.unwritable.append(key)
            continue
        page = page_of(f.page)
        if f.erase:
            # 境外接收方邮编/电话等变长字段：白底遮除框内印刷细竖分隔线，完整保留外侧两端竖线与底部横线
            cover = raw.FPDFPageObj_CreateNewRect(
                f.left + 1.4, f.bottom + 1.2, max(0.0, f.width - 2.8), max(0.0, f.height - 1.4)
            )
            raw.FPDFPageObj_SetFillColor(cover, 255, 255, 255, 255)
            raw.FPDFPath_SetDrawMode(cover, raw.FPDF_FILLMODE_WINDING, False)
            raw.FPDFPage_InsertObject(page.raw, cover)
        elif fixed is None and _printed_inside(text_of(f.page), f):
            # 官方表在栏位里印了占位字（如“(Not Applicable)”）：先用白底盖住栏位内部，再写值。
            # 栏位里只有横线、虚线、点线（写字用的线）时不盖：那是原表的一部分
            cover = raw.FPDFPageObj_CreateNewRect(f.left + 1, f.bottom + 1, max(0.0, f.width - 2), max(0.0, f.height - 2))
            raw.FPDFPageObj_SetFillColor(cover, 255, 255, 255, 255)
            raw.FPDFPath_SetDrawMode(cover, raw.FPDF_FILLMODE_WINDING, False)
            raw.FPDFPage_InsertObject(page.raw, cover)
        if f.cells or f.rules or f.segments:
            if _place_shaped(pdf, font, page, f, value, put, text_of(f.page)):
                report.placed.append(key)
                pages_touched.add(f.page)
            else:
                report.overflow.append(key)
            continue
        avail = f.width - 2 * PAD
        size, shrunk = START_SIZE, False
        while True:
            probe = _text_object(pdf, font, size, " ".join(value.split()))
            fits = _width(probe) <= avail
            raw.FPDFPageObj_Destroy(probe)
            if fits or size <= MIN_SIZE:
                break
            size, shrunk = max(MIN_SIZE, size - 0.5), True
        if fits:
            obj = _text_object(pdf, font, size, " ".join(value.split()))
            put(page, obj, f.left + PAD, f.bottom + max(1.5, (f.height - size) / 2))
            (report.shrunk if shrunk else report.placed).append(key)
        else:
            lines = _wrap(pdf, font, MIN_SIZE + 1.0, value, avail)
            lead = MIN_SIZE + 1.0 + 1.0
            room = int(f.height // lead) if f.height >= 18 else 1  # 单行写字线不折行（第二行会写到线上方）
            if len(lines) <= room and len(lines) > 1:
                for n, line in enumerate(lines):
                    obj = _text_object(pdf, font, MIN_SIZE + 1.0, line)
                    put(page, obj, f.left + PAD, f.top - lead * (n + 1) + 1.5)
                report.wrapped.append(key)
            else:
                # 放不下就不写：压在旁边的栏目上，企业没法盖章。由调用方在导出前提示缩短
                report.overflow.append(key)
                continue
        pages_touched.add(f.page)
    for key in checked:
        f = fields.get(key)
        if f is None or f.kind not in (CHECK, RADIO):
            report.unknown.append(key)
            continue
        page = page_of(f.page)
        size = max(6.0, min(f.height, f.width) + 1.0)
        obj = _text_object(pdf, font, size, "✓")
        put(page, obj, f.left + 0.5, f.bottom + 0.5)
        report.placed.append(key)
        pages_touched.add(f.page)
    for index in pages_touched:
        raw.FPDFPage_GenerateContent(page_of(index).raw)
    # 写了值的文本栏：去掉原表控件本身（有的官方表给控件预置了“(Not Applicable)”这类默认值，不去掉会和写入的内容叠在一起）
    filled = {k for k in report.placed + report.shrunk + report.wrapped if k in texts}
    if filled and fixed is None:
        _drop_widgets(pdf, filled, page_of)

    # 若原表末尾带有无任何字符且无任何填报项的纯空白页（如 TN_ANCE_CRYPTO 第 3 页），干净裁除
    field_pages = {f.page for f in fields.values()}
    while len(pdf) > 1:
        last_idx = len(pdf) - 1
        if last_idx in pages_touched or last_idx in field_pages:
            break
        tp_last = pdf[last_idx].get_textpage() if last_idx not in printed else printed[last_idx]
        n_chars = tp_last.count_chars()
        if last_idx not in printed:
            tp_last.close()
        if n_chars > 0:
            break
        if last_idx in printed:
            printed.pop(last_idx).close()
        if last_idx in pages:
            pages.pop(last_idx).close()
        raw.FPDFPage_Delete(pdf.raw, last_idx)

    import io

    buf = io.BytesIO()
    pdf.save(buf)
    return buf.getvalue(), report


def strip_trailing_blank_pdf_pages(pdf_bytes: bytes) -> bytes:
    """Remove trailing 0-character blank pages from an archived PDF (e.g. TN_ANCE_CRYPTO page 3)."""
    import io

    with _PDFIUM_LOCK:
        pdf = pdfium.PdfDocument(pdf_bytes)
        try:
            changed = False
            while len(pdf) > 1:
                last_idx = len(pdf) - 1
                page = pdf[last_idx]
                tp = page.get_textpage()
                n_chars = tp.count_chars()
                tp.close()
                page.close()
                if n_chars > 0:
                    break
                raw.FPDFPage_Delete(pdf.raw, last_idx)
                changed = True
            if not changed:
                return pdf_bytes
            buf = io.BytesIO()
            pdf.save(buf)
            return buf.getvalue()
        finally:
            pdf.close()


def fit_summary_for_box(
    pdf_bytes: bytes,
    f: FormField,
    value: str,
    ref_tag: str,
    font_path: Path = FONT_PATH,
) -> str:
    """Build the longest prefix of ``value`` plus ``ref_tag`` (e.g. ``(Voir Annexe #1)``) that
    is guaranteed to fit inside ``f`` under ``fill_pdf``'s sizing/wrapping rules."""
    clean = " ".join((value or "").split())
    if not clean or f.cells or f.segments:
        return clean
    with _PDFIUM_LOCK:
        pdf = pdfium.PdfDocument(pdf_bytes)
        try:
            sample_chars = set(clean) | set(ref_tag) | {"…", " "} | _COMMON
            font_bytes = _subset_font(Path(font_path), sample_chars)
            font_buf = (ctypes.c_ubyte * len(font_bytes)).from_buffer_copy(font_bytes)
            font = raw.FPDFText_LoadFont(pdf.raw, font_buf, len(font_bytes), raw.FPDF_FONT_TRUETYPE, True)
            avail = max(8.0, f.width - 2 * PAD)

            def _fits_candidate(cand: str) -> bool:
                if f.rules:
                    lines = _wrap(pdf, font, START_SIZE - 1.0, cand, avail)
                    return len(lines) <= len(f.rules)
                probe = _text_object(pdf, font, MIN_SIZE, cand)
                w_ok = _width(probe) <= avail
                raw.FPDFPageObj_Destroy(probe)
                if w_ok:
                    return True
                lead = MIN_SIZE + 2.0
                room = int(f.height // lead) if f.height >= 18 else 1
                if room > 1:
                    lines = _wrap(pdf, font, MIN_SIZE + 1.0, cand, avail)
                    return 1 < len(lines) <= room
                return False

            # Binary search the longest prefix that fits together with "… " + ref_tag
            lo, hi = 0, len(clean)
            best = ""
            while lo <= hi:
                mid = (lo + hi) // 2
                prefix = clean[:mid].rstrip(" ,;:.，；：。")
                cand = f"{prefix}… {ref_tag}" if prefix else ref_tag
                if _fits_candidate(cand):
                    best = cand
                    lo = mid + 1
                else:
                    hi = mid - 1
            if best:
                return best
            short_tag = ref_tag.strip("()（） ")
            if _fits_candidate(short_tag):
                return short_tag
            m_idx = next((tok for tok in short_tag.split() if tok.startswith("#")), "#1")
            return f"Ann. {m_idx}" if _fits_candidate(f"Ann. {m_idx}") else m_idx
        finally:
            pdf.close()


def append_pdf_pages(main_pdf_bytes: bytes, annex_pdf_bytes: bytes) -> bytes:
    """Append all pages of ``annex_pdf_bytes`` onto the end of ``main_pdf_bytes`` without touching
    the official form pages' layout, widgets, or coordinates."""
    import io

    with _PDFIUM_LOCK:
        dst = pdfium.PdfDocument(main_pdf_bytes)
        src = pdfium.PdfDocument(annex_pdf_bytes)
        try:
            raw.FPDF_ImportPages(dst.raw, src.raw, None, len(dst))
            buf = io.BytesIO()
            dst.save(buf)
            return buf.getvalue()
        finally:
            src.close()
            dst.close()
