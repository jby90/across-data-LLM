# ruff: noqa: E501
from __future__ import annotations

import copy
import io
import re
import zipfile
from datetime import UTC, datetime, timedelta, timezone
from types import SimpleNamespace
from xml.sax.saxutils import escape

from app.analysis.citation_policy import policy_label, review_detail, review_label, review_overview
from app.analysis.consulting import (
    ROADMAP_DIMENSION,
    build_ledger,
    filter_dimension,
    upgrade_legacy,
)
from app.analysis.consulting_export import consulting_blocks
from app.analysis.consulting_export import usable as consulting_usable
from app.analysis.models import AnalysisTask, ReportVersion
from app.analysis.rich_text import parse_bold, strip_bold
from app.analysis.schemas import format_conversation_code
from app.core.config import get_settings

DIMENSIONS = tuple(f"D{i}" for i in range(1, 9))
EXPORT_NOTICE = "合规分析初稿；条文译文及复核状态见引用标记，核验结果供业务评估参考。"
DISCLAIMER = "本报告供业务研究参考，不构成法律意见；业务事实及适用法律须由负责人和专业人员核对。"


CST = timezone(timedelta(hours=8))
BUSINESS_NAME = "汽车数据跨境合规评估"


def _safe_part(text: str) -> str:
    """文件名里的一段：去掉 Windows/macOS 不允许的字符和空白。"""
    return re.sub(r'[\\/:*?"<>|\s]+', "", text or "")


def export_filename(task, rv, ext, dimension=None):
    """导出文件名：日期_国家_业务_内容_版本。

    例如 20261028_新加坡_汽车数据跨境合规评估_完整报告_v1.docx。

    日期取报告生成日（北京时间），同一份报告多次下载文件名不变，不同日期的报告一眼能分开。"""
    from app.analysis.consulting import TOPIC_NAMES

    country = _safe_part(COUNTRY_LABEL.get(task.country_code, task.country_code).split(" ")[0])
    created = getattr(rv, "created_at", None) or getattr(task, "created_at", None)
    day = (created or datetime.now(UTC)).astimezone(CST).strftime("%Y%m%d")
    if ext == "zip":
        what = "全部文件"
    elif dimension:
        what = TOPIC_NAMES.get(dimension, dimension)
    else:
        what = "完整报告"
    return f"{day}_{country}_{BUSINESS_NAME}_{what}_v{rv.version_no}.{ext}"


def file_label(title: str) -> str:
    """A project or conversation title as the front of a file name: “中国 → 埃及 · 车联网上云” →
    “中国-埃及_车联网上云”; characters a file system refuses are dropped."""
    t = (title or "").replace("→", "-").replace("·", "_")
    t = re.sub(r"\s*([-_])\s*", r"\1", t)
    t = re.sub(r'[\\/:*?"<>|\r\n\t]+', "", t).strip(" ._-")
    return t[:80]


def named(*parts: str, ext: str) -> str:
    """〔项目或国家〕_〔文件名〕.ext — every download in the platform is named this way."""
    return "_".join(p for p in (file_label(x) for x in parts) if p) + f".{ext}"


def content_disposition(filename: str) -> str:
    """附件头：给旧浏览器一个纯 ASCII 兜底名，同时用 RFC 5987 的 filename* 带上中文名。"""
    from pathlib import Path
    from urllib.parse import quote

    ext = Path(filename).suffix
    stem = filename[: len(filename) - len(ext)] if ext else filename
    ascii_stem = re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("._-")
    ascii_stem = re.sub(r"_+", "_", ascii_stem)
    if not ascii_stem:
        ascii_stem = "export"
    ascii_ext = re.sub(r"[^A-Za-z0-9.]+", "", ext)
    ascii_name = f"{ascii_stem}{ascii_ext}"
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(filename)}"


def _view(task, rv, dimension):
    """Never borrow the current task's findings when exporting a historical version."""
    if dimension is not None and dimension not in DIMENSIONS:
        raise ValueError("未知报告维度")
    dims = copy.deepcopy(rv.dimensions_json or [])
    if dimension:
        dims = [d for d in dims if d.get("dimension_code") == dimension]
        if not dims:
            raise ValueError("该报告版本没有此维度")
    findings = [f for d in dims for f in d.get("findings", [])]
    consulting = upgrade_legacy(getattr(rv, "consulting_json", None))
    if consulting_usable(consulting):
        ledgers = {}
        for d in rv.dimensions_json or []:
            ledgers.update(build_ledger(d))
        consulting = (
            filter_dimension(consulting, dimension, ledgers)
            if dimension
            else copy.deepcopy(consulting)
        )
    else:
        consulting = None
    task_view = SimpleNamespace(
        id=task.id,
        created_at=task.created_at,
        title=task.title,
        country_code=task.country_code,
        vehicle_model=task.vehicle_model,
        findings_json=findings,
    )
    rv_view = SimpleNamespace(
        regulatory_updates=[
            u
            for u in getattr(rv, "regulatory_updates", [])
            if not dimension or u.get("dimension_code") == dimension
        ],
        citation_policy=getattr(rv, "citation_policy", None),
        version_no=rv.version_no,
        is_partial_sample=rv.is_partial_sample,
        dimensions_json=dims,
        consulting=consulting,
        executive_summary=(
            f"本文件仅导出 {dimension} {dims[0]['dimension_title']}。"
            if dimension
            else rv.executive_summary
        ),
    )
    return task_view, rv_view


def _context(task, rv, dimension):
    code = format_conversation_code(task.id, task.created_at)
    label = dimension or "完整报告"
    if dimension and getattr(rv, "consulting", None):
        # 咨询版给业务人员看：页眉写方面名称，不写内部编号
        label = next(
            (d["title"] for d in rv.consulting["dimensions"] if d["code"] == dimension), label
        )
    return f"{code} | 报告 v{rv.version_no} | {label}"


def update_notices(rv):
    groups = {}
    for update in rv.regulatory_updates:
        key = update["change_id"]
        groups.setdefault(key, {"title": update["document_title"], "dimensions": set()})
        groups[key]["dimensions"].add(update["dimension_code"])
    return [
        f"来源变更待核对：{g['title']}；涉及 {'、'.join(sorted(g['dimensions']))}。"
        "本历史版本正文保留，重新评估后生成新版本；此提醒不是法规修订或人工审批结论。"
        for g in groups.values()
    ]


def exportable_dimensions(rv) -> list[str]:
    """Dimensions that can be exported on their own: the ones the report actually wrote about.
    A dimension with no evidence (or whose advice failed) has nothing to hand over, so it is left out
    of the package and refused on its own; the roadmap dimension is always a plan, never evidence."""
    consulting = upgrade_legacy(getattr(rv, "consulting_json", None))
    if consulting_usable(consulting):
        no_evidence = {
            d["code"]
            for d in consulting.get("dimensions", [])
            if d.get("status") in ("no_evidence", "failed") and d["code"] != ROADMAP_DIMENSION
        }
        return [c for c in DIMENSIONS if c not in no_evidence]
    has = {d.get("dimension_code") for d in rv.dimensions_json or [] if d.get("findings")}
    return [c for c in DIMENSIONS if c in has]


def build_report_zip(task, rv):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(export_filename(task, rv, "docx"), build_report_docx(task, rv))
        for code in exportable_dimensions(rv):
            zf.writestr(export_filename(task, rv, "docx", code), build_report_docx(task, rv, code))
    return buf.getvalue()


COUNTRY_LABEL = {
    "SG": "新加坡 (Singapore)",
    "EG": "埃及 (Egypt)",
    "TN": "突尼斯 (Tunisia)",
    "MA": "摩洛哥 (Morocco)",
    "LB": "黎巴嫩 (Lebanon)",
}

PARTIAL_NOTICE = (
    "【部分样稿】部分维度缺少可通过原文核验的依据,已逐维标注缺口;"
    "引用的中文为参考译文,以官方原文为准。"
)


FONTS = "<w:rFonts w:ascii='Calibri' w:hAnsi='Calibri' w:eastAsia='Microsoft YaHei' w:cs='Arial'/>"
PAGE_TEXT_WIDTH = 9026  # A4 去掉左右各 2.54cm 页边距后的可用宽度（twip）


# Word 不接受的字符（控制符、U+FFFE/U+FFFF 等）：原文提取里偶尔混入，写进去整份文件就打不开
_XML_BAD = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff]")
_CITE_CHAIN = re.compile(r"((?:\[\d+\])+)")
_ARABIC_RE = re.compile(r"[\u0600-\u06ff\u0750-\u077f\u08a0-\u08ff\ufb50-\ufdff\ufe70-\ufeff]")
_RTL_TOKEN_SPLIT = re.compile(
    r"(\([A-Za-z0-9@._:/+\-\s,&']+\)|\+\d[\d\s\-]{4,}\d|[A-Za-z0-9@._:/+-]+(?:\s+[A-Za-z0-9@._:/+-]+)*)"
)


def xml_safe(text: str) -> str:
    return _XML_BAD.sub("", text or "")


def _w_one_run(text: str, bold: bool, size: int, color: str, *, superscript: bool = False, is_rtl: bool = False) -> str:
    b_tag = "<w:b/><w:bCs/>" if bold else ""
    va_tag = "<w:vertAlign w:val='superscript'/>" if superscript else ""
    rtl_tag = "<w:lang w:bidi='ar-SA'/><w:rtl/>" if is_rtl else ""
    return (
        f"<w:r><w:rPr>{b_tag}{FONTS}<w:color w:val='{color}'/>"
        f"<w:sz w:val='{size}'/><w:szCs w:val='{size}'/>{va_tag}{rtl_tag}</w:rPr>"
        f"<w:t xml:space='preserve'>{escape(xml_safe(text))}</w:t></w:r>"
    )


def _w_run(
    text: str,
    bold: bool,
    size: int,
    color: str,
    *,
    superscript: bool = False,
    rtl: bool = False,
) -> str:
    if not rtl or not text:
        return _w_one_run(text, bold, size, color, superscript=superscript, is_rtl=False)
    # 在阿拉伯语 RTL 段落中：阿语与中性括号标点走 <w:rtl/>（保证 (الختم)/(قرار ... 2025) 正常镜像），
    # 英文括注 (Qiheng ... LLC)、国际电话 +20 2 2736 4810 与纯拉丁词段包裹 LRM 保持原子 LTR 顺序
    out: list[str] = []
    for part in _RTL_TOKEN_SPLIT.split(text):
        if not part:
            continue
        is_ltr_span = (
            not _ARABIC_RE.search(part)
            and bool(re.search(r"[A-Za-z]", part) or re.match(r"^\+\d", part))
        )
        if is_ltr_span:
            wrapped = f"\u200e{part}\u200e" if (part.startswith(("(", "+")) or " " in part) else part
            out.append(_w_one_run(wrapped, bold, size, color, superscript=superscript, is_rtl=False))
        else:
            out.append(_w_one_run(part, bold, size, color, superscript=superscript, is_rtl=True))
    return "".join(out)


def _rich_runs(
    text: str,
    bold: bool,
    size: int,
    color: str,
    *,
    has_prefix: bool = False,
    rtl: bool = False,
) -> str:
    out: list[str] = []
    prev_char = "x" if has_prefix else ""
    for seg, is_bold in parse_bold(text):
        if not seg:
            continue
        pos = 0
        for m in _CITE_CHAIN.finditer(seg):
            if m.start() > pos:
                chunk = seg[pos : m.start()]
                out.append(_w_run(chunk, bold or is_bold, size, color, rtl=rtl))
                prev_char = chunk[-1]
            cite_txt = m.group(1)
            if prev_char and not prev_char.isspace() and prev_char != "[":
                out.append(_w_run(cite_txt, False, max(16, size - 4), color, superscript=True, rtl=False))
            else:
                out.append(_w_run(cite_txt, bold or is_bold, size, color, rtl=rtl))
            prev_char = cite_txt[-1]
            pos = m.end()
        if pos < len(seg):
            tail = seg[pos:]
            out.append(_w_run(tail, bold or is_bold, size, color, rtl=rtl))
            prev_char = tail[-1]
    return "".join(out)


_NO_INDENT_PREFIXES = (
    "•",
    "·",
    "[",
    "【部分样稿】",
    "任务:",
    "任务：",
    "生成时引用策略",
    "来源：",
    "人工复核：",
    "个人信息处理者：",
    "地址：",
    "联系人及联系方式：",
    "境外接收方：",
    "所在国家或者地区：",
    "负责人及联系方式：",
    "国家互联网信息办公室",
    "本合同在",
)


def should_indent_prose(text: str) -> bool:
    s = strip_bold(text or "").strip()
    if not s or len(re.findall(r"[\u4e00-\u9fff]", s)) < 6:
        return False
    if s.startswith(_NO_INDENT_PREFIXES):
        return False
    if len(s) <= 45 and s.endswith(("：", ":")):
        return False
    if " → " in s and not s.endswith("。"):
        return False
    return True


def _w_p(
    text: str,
    bold: bool = False,
    size: int = 22,
    color: str = "000000",
    *,
    keep_next: bool = False,
    keep_lines: bool = False,
    rich: bool = False,
    label: str | None = None,
    style: str | None = None,
    indent: int = 0,
    bar: str | None = None,
    after: int = 120,
    prose_indent: bool = False,
    rtl: bool = False,
    align: str | None = None,
) -> str:
    keep = (
        "<w:keepNext/>"
        if (keep_next or (bold and size >= 24) or text.startswith("依据与合规义务"))
        else ""
    )
    keep += "<w:keepLines/>" if keep_lines else ""
    pstyle = f"<w:pStyle w:val='{style}'/>" if style else ""
    bidi = "<w:bidi/>" if rtl else ""
    # 左侧色条用来把“一条发现”的几行文字连成一组（取代原来的缩进+重复标题）
    border = (
        f"<w:pBdr><w:{'right' if rtl else 'left'} w:val='single' w:sz='18' w:space='8' w:color='{bar}'/></w:pBdr>"
        if bar
        else ""
    )
    use_prose = prose_indent and not indent and not bar and not label and not rtl
    ind = (
        "<w:ind w:firstLine='440' w:firstLineChars='200'/>"
        if use_prose
        else (f"<w:ind w:left='{indent}'/>" if indent else "")
    )
    if align:
        jc = f"<w:jc w:val='{align}'/>"
    elif use_prose:
        jc = "<w:jc w:val='both'/>"
    else:
        jc = ""
    # rich：文字里的 **重点** 变成加粗片段，句中/句末引用号 [1][3] 排成上标；其他情况原样输出
    runs = (
        _rich_runs(text, bold, size, color, has_prefix=bool(label), rtl=rtl)
        if rich
        else _w_run(text, bold, size, color, rtl=rtl)
    )
    if label:
        runs = _w_run(label, True, size, color, rtl=rtl) + runs
    if not runs:
        runs = _w_run("", bold, size, color, rtl=rtl)
    return (
        f"<w:p><w:pPr>{pstyle}{keep}{border}{bidi}"
        f"<w:spacing w:after='{after}' w:line='300' w:lineRule='auto'/>{ind}{jc}</w:pPr>"
        f"{runs}</w:p>"
    )


def _w_tc(
    text: str,
    bold: bool = False,
    shade: str | None = None,
    rich: bool = False,
    width: int | None = None,
    *,
    size: int = 19,
    keep_header: bool = False,
    tc_borders: str = "",
    rtl: bool = False,
) -> str:
    shd = f"<w:shd w:val='clear' w:color='auto' w:fill='{shade}'/>" if shade else ""
    tcw = f"<w:tcW w:w='{width}' w:type='dxa'/>" if width else ""
    # 单元格内的换行（如“部分达到\n风险高”）要拆成多段，否则 Word 会把换行符当空格吞掉
    # 表头与下一行绑在一起，避免表头孤零零留在页尾
    paras = "".join(
        _w_p(
            line,
            bold=bold,
            size=size,
            color="000000",
            rich=rich,
            after=40,
            keep_next=bool(keep_header or shade),
            rtl=rtl,
        )
        for line in (text or "").split("\n")
    )
    return f"<w:tc><w:tcPr>{tcw}{tc_borders}{shd}</w:tcPr>{paras}</w:tc>"


def _clause_ref(f: dict) -> str:
    notice = f"；{f['source_notice']}" if f.get("source_notice") else ""
    return (
        f"{f.get('document_title', '')} {f.get('clause_number', '')} "
        f"(v{f.get('version_no', 1)}){notice}"
    )


def report_notices(rv) -> list[str]:
    """咨询版正文里的提示：引用复核概览、来源变更、引用策略与部分样稿。

    现阶段只关注流程与报告质量，默认不显示（report_review_notices 关闭）。"""
    if not get_settings().report_review_notices:
        return []
    return [
        review_overview(rv.dimensions_json),
        *update_notices(rv),
        f"生成时引用策略：{policy_label(rv.citation_policy)}",
        *([PARTIAL_NOTICE] if rv.is_partial_sample else []),
    ]


def _w_table(
    head: list[str],
    rows: list[list[str]],
    widths: list[int] | None = None,
    *,
    table_style: str = "formal_black",
    rtl: bool = False,
) -> str:
    if table_style == "edpb":
        # 严格对齐欧盟 EDPB 2026 官方 DPIA 模板 (Table2-EDPB)：0.5pt #0A105D 细实线边框、白底无填充、表头加粗黑字
        border = "<w:{0} w:val='single' w:sz='4' w:space='0' w:color='0A105D'/>"
        borders = "".join(
            border.format(n) for n in ("top", "bottom", "left", "right", "insideH", "insideV")
        )
        head_tcb = ""
        head_sz = 20
    else:
        # 无外部模板的自生成正式合规表格：统一纯黑实线边框（外框及表头下沿 0.75pt、内框 0.5pt）、白底无蓝色底纹、表头加粗黑字
        outer = "<w:{0} w:val='single' w:sz='6' w:space='0' w:color='000000'/>"
        inner = "<w:{0} w:val='single' w:sz='4' w:space='0' w:color='000000'/>"
        borders = "".join(outer.format(n) for n in ("top", "bottom", "left", "right")) + "".join(
            inner.format(n) for n in ("insideH", "insideV")
        )
        head_tcb = "<w:tcBorders><w:bottom w:val='single' w:sz='6' w:space='0' w:color='000000'/></w:tcBorders>"
        head_sz = 19
    # 列宽：百分比换算成 twip；不给就均分。固定布局 + tblGrid，Word 才不会按内容挤列
    pct = widths or [100 // len(head)] * len(head)
    cols = [PAGE_TEXT_WIDTH * p // sum(pct) for p in pct]
    grid = "<w:tblGrid>" + "".join(f"<w:gridCol w:w='{c}'/>" for c in cols) + "</w:tblGrid>"
    # 表头行跨页重复；行内不拆页，避免一行被劈成两半
    row_pr = "<w:trPr><w:cantSplit/>{0}</w:trPr>"
    body = [
        "<w:tr>"
        + row_pr.format("<w:tblHeader/>")
        + "".join(
            _w_tc(h, True, None, width=w, size=head_sz, keep_header=True, tc_borders=head_tcb, rtl=rtl)
            for h, w in zip(head, cols, strict=True)
        )
        + "</w:tr>"
    ]
    body += [
        "<w:tr>"
        + row_pr.format("")
        + "".join(_w_tc(str(c), rich=True, width=w, rtl=rtl) for c, w in zip(row, cols, strict=True))
        + "</w:tr>"
        for row in rows
    ]
    bidi_vis = "<w:bidiVisual/>" if rtl else ""
    return (
        f"<w:tbl><w:tblPr>{bidi_vis}<w:tblW w:w='{sum(cols)}' w:type='dxa'/><w:tblLayout w:type='fixed'/>"
        f"<w:tblBorders>{borders}</w:tblBorders>"
        "<w:tblCellMar><w:top w:w='60' w:type='dxa'/><w:left w:w='100' w:type='dxa'/>"
        "<w:bottom w:w='60' w:type='dxa'/><w:right w:w='100' w:type='dxa'/></w:tblCellMar>"
        "</w:tblPr>" + grid + "".join(body) + "</w:tbl>"
    )


def _w_blocks(blocks: list[dict]) -> list[str]:
    parts: list[str] = []
    for blk in blocks:
        kind = blk["t"]
        if kind == "h1":
            parts.append(_w_p(blk["text"], bold=True, size=32, color="000000", style="Title"))
        elif kind == "meta":
            parts.append(_w_p(blk["text"], size=19, color="475569"))
        elif kind == "notice":
            parts.append(_w_p(blk["text"], size=18, color="92400E"))
        elif kind == "verdict":
            parts.append(
                _w_p(
                    f"{blk['label']}：{strip_bold(blk['headline'])}",
                    bold=True,
                    size=30,
                    color="000000",
                )
            )
        elif kind == "h2":
            parts.append(
                _w_p(
                    blk["text"],
                    bold=True,
                    size=26,
                    color="000000",
                    keep_next=True,
                    style="Heading1",
                )
            )
        elif kind == "h3":
            parts.append(
                _w_p(
                    blk["text"],
                    bold=True,
                    size=22,
                    color="000000",
                    keep_next=True,
                    style="Heading2",
                )
            )
        elif kind == "p":
            lines = [ln for ln in (blk["text"] or "").split("\n") if ln.strip()] or [blk["text"]]
            for ln in lines:
                parts.append(_w_p(ln, rich=True, prose_indent=not blk.get("center") and should_indent_prose(ln)))
        elif kind == "item":
            parts.append(_w_p(f"{blk['n']}. {blk['text']}", rich=True, indent=240))
        elif kind == "finding":
            bar = "334155"
            parts.append(
                _w_p(
                    f"{blk['title']}{blk['refs']}（{blk['chips']}）",
                    bold=True,
                    size=22,
                    color="000000",
                    keep_next=True,
                    rich=True,
                    indent=200,
                    bar=bar,
                    after=40,
                )
            )
            parts.extend(
                _w_p(
                    text,
                    size=21,
                    rich=True,
                    label=f"{lab}：",
                    keep_lines=True,
                    indent=200,
                    bar=bar,
                    after=40 if i < len(blk["rows"]) - 1 else 200,
                )
                for i, (lab, text) in enumerate(blk["rows"])
            )
        elif kind == "table":
            parts.append(_w_table(blk["head"], blk["rows"], blk.get("widths")))
            parts.append(_w_p("", size=10))
        elif kind == "ref":
            parts.append(_w_p(f"[{blk['n']}] {blk['text']}", bold=True, size=19, keep_next=True))
            parts.extend(_w_p(s, size=18, color="475569", keep_lines=True) for s in blk["sub"])
    return parts


def build_report_docx(task: AnalysisTask, rv: ReportVersion, dimension: str | None = None) -> bytes:
    """生成八维度国别安全评估报告 Word (.docx)。"""
    task, rv = _view(task, rv, dimension)
    country = COUNTRY_LABEL.get(task.country_code, task.country_code)
    now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    if rv.consulting:
        parts = _w_blocks(
            consulting_blocks(
                country=country,
                task=task,
                rv=rv,
                consulting=rv.consulting,
                dimension=dimension,
                notices=report_notices(rv),
            )
        )
        return _docx_package(parts, _context(task, rv, dimension), clean=True)
    parts: list[str] = [
        _w_p(
            f"{country} 汽车数据跨境合规评估报告 {dimension or '八维度'}",
            bold=True,
            size=32,
            color="000000",
        ),
        _w_p(
            f"任务:{task.title} | 版本:v{rv.version_no} | 车型:{task.vehicle_model} | {now}",
            size=19,
            color="475569",
        ),
    ]
    parts.extend([_w_p(EXPORT_NOTICE, size=19), _w_p(DISCLAIMER, size=19)])
    parts.append(_w_p(review_overview(rv.dimensions_json), size=19))
    parts.extend(_w_p(notice, True, 19, "B45309") for notice in update_notices(rv))
    parts.append(_w_p(f"生成时引用策略：{policy_label(rv.citation_policy)}", size=18))
    if rv.is_partial_sample:
        parts.append(_w_p(PARTIAL_NOTICE, bold=True, size=20, color="B45309"))
    parts.append(_w_p("一、执行摘要", bold=True, size=26, color="000000"))
    parts.extend(_w_p(ln.strip()) for ln in (rv.executive_summary or "").splitlines() if ln.strip())

    parts.append(_w_p("二、合规分析判定矩阵", bold=True, size=26, color="000000"))
    heads = ["业务事实", "适用条款", "合规义务", "风险等级", "判断依据(原文子串)"]
    if not task.findings_json:
        parts.append(
            _w_p("该版本未保存判定矩阵快照；请查阅下列章节及其引用，未使用任务最新结果替代。")
        )
    _RISK_ZH = {
        "critical": "极高风险",
        "high": "高风险",
        "medium": "中风险",
        "low": "低风险",
        "info": "提示",
        "unknown": "待核实",
        "pending_verification": "待核实",
        "pending": "待核实",
        "none": "无显著风险",
    }
    _STATUS_ZH = {
        "complete": "已完成",
        "completed": "已完成",
        "partial_gap": "部分缺口待核实",
        "missing_evidence": "待补充依据",
        "pending": "待评估",
        "pending_verification": "待核实",
        "running": "评估中",
        "analysis_error": "需复核",
        "analysis_failed": "需复核",
        "failed": "需复核",
    }
    _inline_risk_re = re.compile(
        r"(风险\s*[:：]\s*)(CRITICAL|HIGH|MEDIUM|LOW|INFO|PENDING_VERIFICATION|UNKNOWN)\b",
        re.IGNORECASE,
    )

    def _clean_dim_line(ln: str) -> str:
        return _inline_risk_re.sub(
            lambda m: f"风险：{_RISK_ZH.get(m.group(2).lower(), m.group(2))}", ln
        )
    matrix_rows = [
        [
            str(f.get("business_fact", "")),
            _clause_ref(f),
            str(f.get("compliance_obligation", "")),
            _RISK_ZH.get(str(f.get("risk_level", "") or "").strip().lower(), str(f.get("risk_level", ""))),
            f"{f.get('basis_quote', '')}\n【参考译文】{f.get('basis_zh', '')}",
        ]
        for f in (task.findings_json or [])
    ]
    parts.append(_w_table(heads, matrix_rows, [20, 18, 22, 10, 30]))

    parts.append(_w_p("三、评估章节", bold=True, size=26, color="000000"))
    for dim in rv.dimensions_json or []:
        rl_raw = str(dim.get("risk_level", "") or "").strip()
        st_raw = str(dim.get("status", "") or "").strip()
        rl_disp = _RISK_ZH.get(rl_raw.lower(), rl_raw)
        st_disp = _STATUS_ZH.get(st_raw.lower(), st_raw)
        title = (
            f"【{dim.get('dimension_code', '')}】{dim.get('dimension_title', '')} "
            f"（风险：{rl_disp}｜状态：{st_disp}）"
        )
        parts.append(_w_p(title, bold=True, size=24, color="0F172A"))
        if dim.get("missing_gap_note"):
            parts.append(_w_p(f"核查缺口:{dim['missing_gap_note']}", True, 20, "B45309"))
        for line in str(dim.get("content_markdown", "")).splitlines():
            if line.strip():
                parts.append(_w_p(_clean_dim_line(line.strip().lstrip("#").strip())))
        for cite in dim.get("citations", []) or []:
            parts.append(
                _w_p(
                    f"  引用 {cite.get('citation_tag', '')}:{_clause_ref(cite)} — "
                    f'原文:"{cite.get("quote_original", "")}" | 译文:{cite.get("quote_zh", "")}',
                    size=18,
                    color="334155",
                    keep_next=True,
                )
            )
            parts.append(
                _w_p(
                    f"来源：{cite.get('source_url') or '未提供'}；"
                    f"人工复核：{review_label(cite)}；"
                    f"{review_detail(cite)}",
                    size=18,
                    keep_lines=True,
                )
            )

    return _docx_package(parts, _context(task, rv, dimension))


def _styles_xml(ns: str, *, heading_color: str = "000000") -> str:
    """默认字体与标题样式：标题用真正的 Heading 样式，Word 里才有导航窗格和目录。"""

    def heading(sid: str, name: str, level: int, size: int, color: str, before: int) -> str:
        return (
            f"<w:style w:type='paragraph' w:styleId='{sid}'><w:name w:val='{name}'/>"
            "<w:basedOn w:val='Normal'/><w:next w:val='Normal'/><w:qFormat/>"
            f"<w:pPr><w:keepNext/><w:spacing w:before='{before}' w:after='120'/>"
            f"<w:outlineLvl w:val='{level}'/></w:pPr>"
            f"<w:rPr><w:b/><w:bCs/><w:color w:val='{color}'/>"
            f"<w:sz w:val='{size}'/><w:szCs w:val='{size}'/></w:rPr></w:style>"
        )

    return (
        "<?xml version='1.0' encoding='UTF-8' standalone='yes'?>"
        f"<w:styles xmlns:w='{ns}'><w:docDefaults><w:rPrDefault><w:rPr>{FONTS}"
        "<w:sz w:val='22'/><w:szCs w:val='22'/><w:lang w:val='en-US' w:eastAsia='zh-CN'/>"
        "</w:rPr></w:rPrDefault></w:docDefaults>"
        "<w:style w:type='paragraph' w:default='1' w:styleId='Normal'><w:name w:val='Normal'/>"
        "<w:qFormat/></w:style>"
        "<w:style w:type='paragraph' w:styleId='Title'><w:name w:val='Title'/>"
        "<w:basedOn w:val='Normal'/><w:next w:val='Normal'/><w:qFormat/>"
        "<w:pPr><w:spacing w:after='120'/></w:pPr></w:style>"
        + heading("Heading1", "heading 1", 0, 26, heading_color, 360)
        + heading("Heading2", "heading 2", 1, 22, heading_color, 200)
        + "</w:styles>"
    )


def _footer_xml(ns: str, *, foreign: bool = False, rtl: bool = False) -> str:
    """页脚：居中的“第 N 页 / 共 M 页”（外文正式稿用通用“N / M”页码，不夹中文）。"""

    def fld(code: str) -> str:
        rpr = f"<w:rPr>{FONTS}<w:color w:val='64748B'/><w:sz w:val='16'/></w:rPr>"
        return (
            f"<w:r>{rpr}<w:fldChar w:fldCharType='begin' w:dirty='true'/></w:r>"
            f"<w:r>{rpr}<w:instrText xml:space='preserve'> {code} </w:instrText></w:r>"
            f"<w:r>{rpr}<w:fldChar w:fldCharType='separate'/></w:r>"
            f"<w:r>{rpr}<w:t>1</w:t></w:r>"
            f"<w:r>{rpr}<w:fldChar w:fldCharType='end'/></w:r>"
        )

    if foreign:
        return (
            f"<w:ftr xmlns:w='{ns}'><w:p><w:pPr><w:bidi w:val='0'/><w:jc w:val='center'/></w:pPr>"
            + _w_run("\u200e", False, 16, "64748B")
            + fld("PAGE")
            + _w_run(" / ", False, 16, "64748B")
            + fld("NUMPAGES")
            + _w_run("\u200e", False, 16, "64748B")
            + "</w:p></w:ftr>"
        )
    return (
        f"<w:ftr xmlns:w='{ns}'><w:p><w:pPr><w:jc w:val='center'/></w:pPr>"
        + _w_run("第 ", False, 16, "64748B")
        + fld("PAGE")
        + _w_run(" 页 / 共 ", False, 16, "64748B")
        + fld("NUMPAGES")
        + _w_run(" 页", False, 16, "64748B")
        + "</w:p></w:ftr>"
    )


def _docx_package(
    parts: list[str],
    context: str,
    *,
    clean: bool = False,
    foreign: bool = False,
    heading_color: str = "000000",
    rtl: bool = False,
) -> bytes:
    ns = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    sect_bidi = "<w:bidi/>" if rtl else ""
    document_xml = (
        "<?xml version='1.0' encoding='UTF-8' standalone='yes'?>"
        f"<w:document xmlns:w='{ns}' "
        "xmlns:r='http://schemas.openxmlformats.org/officeDocument/2006/relationships'><w:body>"
        + "".join(parts)
        + f"<w:sectPr>{sect_bidi}<w:headerReference w:type='default' r:id='rIdHeader'/>"
        "<w:footerReference w:type='default' r:id='rIdFooter'/>"
        "<w:pgSz w:w='11906' w:h='16838'/>"
        "<w:pgMar w:top='1800' w:right='1440' w:bottom='1440' w:left='1440' "
        "w:header='360' w:footer='500'/>"
        "<w:pgNumType w:fmt='decimal' w:start='1'/></w:sectPr>"
        "</w:body></w:document>"
    )
    rels_ct = "application/vnd.openxmlformats-package.relationships+xml"
    main_ct = "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"
    content_types = (
        "<?xml version='1.0' encoding='UTF-8' standalone='yes'?>"
        "<Types xmlns='http://schemas.openxmlformats.org/package/2006/content-types'>"
        f"<Default Extension='rels' ContentType='{rels_ct}'/>"
        "<Default Extension='xml' ContentType='application/xml'/>"
        f"<Override PartName='/word/document.xml' ContentType='{main_ct}'/></Types>"
    )
    wml = "application/vnd.openxmlformats-officedocument.wordprocessingml"
    content_types = content_types.replace(
        "</Types>",
        f"<Override PartName='/word/header1.xml' ContentType='{wml}.header+xml'/>"
        f"<Override PartName='/word/footer1.xml' ContentType='{wml}.footer+xml'/>"
        f"<Override PartName='/word/styles.xml' ContentType='{wml}.styles+xml'/>"
        f"<Override PartName='/word/settings.xml' ContentType='{wml}.settings+xml'/></Types>",
    )
    rel_type = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument"
    rels = (
        "<?xml version='1.0' encoding='UTF-8' standalone='yes'?>"
        "<Relationships xmlns='http://schemas.openxmlformats.org/package/2006/relationships'>"
        f"<Relationship Id='rId1' Type='{rel_type}' Target='word/document.xml'/></Relationships>"
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", content_types)
        zf.writestr("_rels/.rels", rels)
        zf.writestr("word/document.xml", document_xml)
        zf.writestr(
            "word/_rels/document.xml.rels",
            "<Relationships xmlns='http://schemas.openxmlformats.org/package/2006/relationships'>"
            "<Relationship Id='rIdHeader' "
            "Type='http://schemas.openxmlformats.org/officeDocument/2006/relationships/header' "
            "Target='header1.xml'/>"
            "<Relationship Id='rIdFooter' "
            "Type='http://schemas.openxmlformats.org/officeDocument/2006/relationships/footer' "
            "Target='footer1.xml'/>"
            "<Relationship Id='rIdStyles' "
            "Type='http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles' "
            "Target='styles.xml'/>"
            "<Relationship Id='rIdSettings' "
            "Type='http://schemas.openxmlformats.org/officeDocument/2006/relationships/settings' "
            "Target='settings.xml'/>"
            "</Relationships>",
        )
        zf.writestr("word/styles.xml", _styles_xml(ns, heading_color=heading_color))
        zf.writestr(
            "word/settings.xml",
            "<?xml version='1.0' encoding='UTF-8' standalone='yes'?>"
            f"<w:settings xmlns:w='{ns}'><w:updateFields w:val='true'/></w:settings>",
        )
        zf.writestr("word/footer1.xml", _footer_xml(ns, foreign=foreign, rtl=rtl))
        zf.writestr(
            "word/header1.xml",
            f"<w:hdr xmlns:w='{ns}'>"
            + _w_p(context, size=16, rtl=rtl)
            + (
                ""
                if foreign or (clean and not get_settings().report_review_notices)
                else _w_p(EXPORT_NOTICE + DISCLAIMER, size=16)
            )
            + "</w:hdr>",
        )
    return buf.getvalue()


def _clean_styles_for_official_contract(parts: list[str], context: str) -> bytes:
    return _docx_package(parts, context, clean=True)


def build_report_pdf(task: AnalysisTask, rv: ReportVersion, dimension: str | None = None) -> bytes:
    """Selectable multilingual PDF; originals and frozen report snapshots remain unchanged."""
    from app.analysis.pdf_renderer import render_pdf

    task, rv = _view(task, rv, dimension)
    country = COUNTRY_LABEL.get(task.country_code, task.country_code)
    if rv.consulting:
        blocks = consulting_blocks(
            country=country,
            task=task,
            rv=rv,
            consulting=rv.consulting,
            dimension=dimension,
            notices=report_notices(rv),
        )
        show = get_settings().report_review_notices
        return render_pdf(
            task,
            rv,
            dimension=dimension,
            context=_context(task, rv, dimension),
            country=country,
            notices=[EXPORT_NOTICE, DISCLAIMER] if show else [],
            blocks=blocks,
        )
    return render_pdf(
        task,
        rv,
        dimension=dimension,
        context=_context(task, rv, dimension),
        country=country,
        notices=[EXPORT_NOTICE, DISCLAIMER, *update_notices(rv)]
        + ([PARTIAL_NOTICE] if rv.is_partial_sample else []),
    )
