# ruff: noqa: E501
"""Offline, HarfBuzz-shaped report export. No model, network or data mutation."""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import unicodedata
from functools import lru_cache
from html import escape
from pathlib import Path

from app.analysis.citation_policy import policy_label, review_detail, review_label, review_overview
from app.analysis.rich_text import parse_bold, strip_bold

FONT_DIR = Path(__file__).with_name("fonts")
_RENDER_LOCK = threading.Lock()  # Bound native rendering memory on small prototype hosts.


@lru_cache(maxsize=1)
def verified_fonts():
    manifest = json.loads((FONT_DIR / "open-fonts-manifest.json").read_text(encoding="utf-8"))
    fonts = {}
    for item in manifest["files"]:
        path = (FONT_DIR / item["file"]).resolve()
        if not path.is_relative_to(FONT_DIR.resolve()):
            raise ValueError("字体路径越界")
        if hashlib.sha256(path.read_bytes()).hexdigest() != item["sha256"]:
            raise ValueError("随包字体校验失败")
        if path.suffix == ".ttf":
            fonts[item["family"]] = path.as_uri()
    return fonts


RTL = "\u0590-\u08ff\ufb1d-\ufdff\ufe70-\ufefc"
_RTL_RUN = re.compile(
    rf"[{RTL}](?:[{RTL}\s0-9\u0660-\u0669.,\u060c\u061b:/\-]*[{RTL}\u0660-\u0669]"
    rf"|[{RTL}\s0-9\u0660-\u0669.,\u060c\u061b:/\-]*[0-9](?![A-Za-z]))?"
)
_LTR_TOKEN_IN_RTL = re.compile(
    r"(?:[A-Za-z\u00C0-\u024F]|\d+[A-Za-z\u00C0-\u024F])"
    r"(?:[A-Za-z0-9\u00C0-\u024F\s./_+\-]*[A-Za-z0-9\u00C0-\u024F])?"
)


def _text(value, *, base_dir: str = ""):
    raw = str(value or "")
    # 双向文字混排隔离：LTR 段落中隔离阿语片段（U+2068…U+2069），
    # RTL 阿语段落中隔离含数字前缀的拉丁代号如 4G/5G、QH-S9（U+2066…U+2069）
    bidi = {unicodedata.bidirectional(c) for c in raw}
    if not (bidi & {"R", "AL"} and "L" in bidi):
        return escape(raw, quote=True)
    first_strong = next(
        (
            unicodedata.bidirectional(c)
            for c in raw
            if unicodedata.bidirectional(c) in {"R", "AL", "L"}
        ),
        "L",
    )
    eff_dir = base_dir or ("rtl" if first_strong in {"R", "AL"} else "ltr")
    if eff_dir == "rtl":
        raw = _LTR_TOKEN_IN_RTL.sub(lambda m: "\u2066" + m.group(0) + "\u2069", raw)
    else:
        raw = _RTL_RUN.sub(lambda m: "\u2068" + m.group(0) + "\u2069", raw)
    return escape(raw, quote=True)


def _p(value, cls=""):
    # HTML dir=auto is not implemented consistently by paged-media engines.
    # Unicode's first-strong rule works for every language without country rules.
    strong = next(
        (
            unicodedata.bidirectional(c)
            for c in str(value or "")
            if unicodedata.bidirectional(c) in {"R", "AL", "L"}
        ),
        "L",
    )
    direction = "rtl" if strong in {"R", "AL"} else "ltr"
    return f'<p class="{cls} {direction}" dir="{direction}">{_text(value)}</p>'


def _rich(value):
    """先转义每个片段，再只为加粗片段包上 <b>；模型文字里的 HTML 不会被执行。"""
    return "".join(
        f"<b>{_text(seg)}</b>" if bold else _text(seg) for seg, bold in parse_bold(str(value or ""))
    )


def _rp(value, cls=""):
    """与 _p 相同的段落（含文字方向判断），内容支持 **加粗**。"""
    plain = strip_bold(str(value or ""))
    strong = next(
        (
            unicodedata.bidirectional(c)
            for c in plain
            if unicodedata.bidirectional(c) in {"R", "AL", "L"}
        ),
        "L",
    )
    direction = "rtl" if strong in {"R", "AL"} else "ltr"
    return f'<p class="{cls} {direction}" dir="{direction}">{_rich(value)}</p>'


def _dir_attr(value: object) -> str:
    strong = next(
        (
            unicodedata.bidirectional(c)
            for c in str(value or "")
            if unicodedata.bidirectional(c) in {"R", "AL", "L"}
        ),
        "L",
    )
    return ' class="rtl" dir="rtl"' if strong in {"R", "AL"} else ""


def _blocks_html(blocks):
    """咨询版内容块 → HTML；文字一律转义，不执行任何内容。"""
    out = []
    for blk in blocks:
        kind = blk["t"]
        if kind == "h1":
            out.append(f"<h1{_dir_attr(blk['text'])}>{_text(blk['text'])}</h1>")
        elif kind == "meta":
            out.append(_p(blk["text"], "meta"))
        elif kind == "notice":
            out.append(_p(blk["text"], "notice"))
        elif kind == "verdict":
            head = strip_bold(blk["headline"])
            out.append(f'<p class="verdict">{_text(blk["label"])}：{_text(head)}</p>')
        elif kind == "h2":
            out.append(f"<h2{_dir_attr(blk['text'])}>{_text(blk['text'])}</h2>")
        elif kind == "h3":
            out.append(f"<h3{_dir_attr(blk['text'])}>{_text(blk['text'])}</h3>")
        elif kind == "p":
            s_plain = strip_bold(str(blk["text"] or "")).strip()
            is_prose = (
                not blk.get("center")
                and len(re.findall(r"[\u4e00-\u9fff]", s_plain)) >= 6
                and not s_plain.startswith(("•", "·", "[", "【部分样稿】", "任务:", "任务："))
                and not (len(s_plain) <= 45 and s_plain.endswith(("：", ":")))
                and not (" → " in s_plain and not s_plain.endswith("。"))
            )
            p_cls = "center" if blk.get("center") else ("prose" if is_prose else "")
            out.append(_rp(blk["text"], p_cls))
        elif kind == "item":
            out.append(
                f'<div class="item">{_rp(str(blk["n"]) + ". " + blk["text"], "itemhead")}</div>'
            )
        elif kind == "finding":
            head = f"{_rich(blk['title'])}{_text(blk['refs'])}（{_text(blk['chips'])}）"
            rows = "".join(
                f'<p class="kv"><b>{_text(lab)}：</b>{_rich(text)}</p>' for lab, text in blk["rows"]
            )
            out.append(f'<div class="finding"><p class="ftitle"><b>{head}</b></p>{rows}</div>')
        elif kind == "table":
            head = "".join(f"<th>{_p(h)}</th>" for h in blk["head"])
            rows = "".join(
                "<tr>" + "".join(f"<td>{_rp(c)}</td>" for c in row) + "</tr>" for row in blk["rows"]
            )
            # 列宽写死：自动列宽会把“时限”这类窄列挤成一字一行
            widths = blk.get("widths") or []
            cols = "".join(f'<col style="width:{w}%">' for w in widths)
            out.append(
                f"<table style='table-layout:fixed'><colgroup>{cols}</colgroup>"
                f"<thead><tr>{head}</tr></thead><tbody>{rows}</tbody></table>"
            )
        elif kind == "grid":
            # 官方表格原样：合并单元格照原表合并（横向 colspan、纵向 rowspan）
            cols = "".join(f'<col style="width:{w}%">' for w in blk.get("widths") or [])
            body = "".join(
                "<tr>"
                + "".join(
                    f'<td colspan="{c["span"]}" rowspan="{c["rows"]}"'
                    + (' class="blank"' if c.get("blank") else "")
                    + f">{_rp(c['text'])}</td>"
                    for c in row
                )
                + "</tr>"
                for row in blk["rows"]
            )
            out.append(f"<table class='grid' style='table-layout:fixed'><colgroup>{cols}</colgroup><tbody>{body}</tbody></table>")
        elif kind == "ref":
            subs = "".join(_p(s, "meta") for s in blk["sub"])
            head = f"[{blk['n']}] {_text(blk['text'])}"
            out.append(f'<div class="ref"><p class="refhead">{head}</p>{subs}</div>')
    return "".join(out)


_RISK_ZH = {
    "CRITICAL": "极高",
    "HIGH": "高",
    "MEDIUM": "中",
    "LOW": "低",
    "INFO": "提示",
    "UNKNOWN": "待核实",
    "PENDING_VERIFICATION": "待核实",
    "PENDING": "待核实",
    "NONE": "无显著风险",
}
_INLINE_RISK_RE = re.compile(
    r"(风险\s*[:：]\s*)(CRITICAL|HIGH|MEDIUM|LOW|INFO|PENDING_VERIFICATION|UNKNOWN)\b",
    re.IGNORECASE,
)


def _risk_zh(value: object) -> str:
    s = str(value or "").strip()
    return _RISK_ZH.get(s.upper(), s)


def _localize_inline_enums(value: object) -> str:
    s = str(value or "")
    return _INLINE_RISK_RE.sub(
        lambda m: f"风险：{_RISK_ZH.get(m.group(2).upper(), m.group(2))}", s
    )


def report_html(
    task,
    rv,
    *,
    dimension,
    context,
    country,
    notices,
    blocks=None,
    table_style: str = "formal_black",
    rtl: bool = False,
):
    fonts = verified_fonts()
    css_fonts = "\n".join(
        f"@font-face{{font-family:'{name}';src:url('{uri}');font-weight:100 900}}"
        for name, uri in fonts.items()
    )
    bdr_col = "#0a105d" if table_style == "edpb" else "#000000"
    head_col = "#0a105d" if table_style == "edpb" else "#000000"
    outer_w = "0.5pt" if table_style == "edpb" else "0.75pt"
    html_lang = "ar" if rtl else "zh-CN"
    body_attr = " dir='rtl' class='rtl-doc'" if rtl else ""
    top_slot = "@top-right" if rtl else "@top-left"
    parts = [
        f"<!doctype html><html lang='{html_lang}'><meta charset='utf-8'><style>",
        css_fonts,
        f"""
        @page {{size:A4; margin:28mm 18mm 22mm;
          {top_slot} {{content:element(reportHeader)}}
          @bottom-center {{content:counter(page) ' / ' counter(pages);font-size:9pt}}}}
        body{{font-family:inter,notosanssc,notonaskharabic,sans-serif;
          font-size:10.5pt;color:#000000;line-height:1.5;hyphens:none}}
        body.rtl-doc{{direction:rtl;text-align:right}}
        body.rtl-doc table{{direction:rtl}}
        body.rtl-doc th{{text-align:right}}
        header{{position:running(reportHeader);font-size:8pt;color:#64748b}}
        h1{{font-size:20pt;line-height:1.4;color:{head_col}}} h2{{font-size:14pt;color:{head_col}}}
        h3{{font-size:12pt;color:{head_col}}} h1,h2,h3{{break-after:avoid}}
        p{{margin:5pt 0;white-space:pre-wrap;overflow-wrap:anywhere}}
        .rtl{{direction:rtl;text-align:right;font-size:12pt}}
        .notice{{padding:7pt;background:#fff7e8;color:#855800}}
        .meta,.citation{{font-size:9pt;color:#43546a}}
        .finding{{border-left:2pt solid #334155;padding-left:8pt;margin:10pt 0}}
        .citation{{border-top:0.5pt solid #cbd5e1;padding-top:5pt}}
        .citation-block{{break-inside:avoid}}
        table{{border-collapse:collapse;width:100%;font-size:9.5pt;margin:6pt 0 10pt;border:{outer_w} solid {bdr_col}}}
        th,td{{border:0.5pt solid {bdr_col};padding:3.5pt 5pt;vertical-align:top;color:#000000}}
        th{{background:#ffffff;border-bottom:{outer_w} solid {bdr_col};text-align:left;font-weight:700;color:#000000}}
        td p,th p{{margin:0}}
        tr{{break-inside:avoid}}
        .verdict{{font-size:16pt;font-weight:700;color:#000000;margin:14pt 0 6pt}}
        .item{{margin:6pt 0;break-inside:avoid}} .itemhead{{font-weight:700;margin-bottom:1pt}}
        .finding{{break-inside:avoid;margin:9pt 0;padding-left:8pt;border-left:2pt solid #334155}}
        .finding .ftitle{{font-size:11pt;margin:0 0 2pt}} .finding .kv{{margin:1.5pt 0}}
        .ref{{break-inside:avoid;margin:5pt 0}}
        p.center{{text-align:center}}
        p.prose.ltr{{text-indent:2em;text-align:justify}}
        table.grid td{{font-size:9.5pt}} table.grid td.blank{{border:0}}
        .ref .refhead{{font-weight:700;font-size:9.5pt}}
        </style><body{body_attr}>""",
        f"<header>{_text(context)}" + (f"<br>{_text(notices[0])}" if notices else "") + "</header>",
    ]
    if blocks is not None:
        return "".join(parts) + _blocks_html(blocks) + "</body></html>"
    parts += [
        f"<h1>{_text(country)} 汽车数据跨境合规评估报告 {_text(dimension or '八维度')}</h1>",
        _p(f"任务：{task.title} | v{rv.version_no} | 车型：{task.vehicle_model}", "meta"),
        *[_p(n, "notice") for n in notices],
        _p(review_overview(rv.dimensions_json)),
        _p(f"生成时引用策略：{policy_label(rv.citation_policy)}", "meta"),
        "<h2>一、执行摘要</h2>",
        _rp(rv.executive_summary),
        "<h2>二、合规分析判定矩阵</h2>",
    ]
    for idx, finding in enumerate(task.findings_json or [], 1):
        parts.extend(
            [
                '<section class="finding">',
                _rp(f"[{idx}] {finding.get('business_fact', '')}"),
                _p(
                    f"{finding.get('document_title', '')} {finding.get('clause_number', '')} "
                    f"(v{finding.get('version_no', 1)}) | 风险：{_risk_zh(finding.get('risk_level', ''))}"
                ),
                _rp(f"义务：{finding.get('compliance_obligation', '')}"),
                _p(finding.get("basis_quote", ""), "citation"),
                "</section>",
            ]
        )
    if not task.findings_json:
        parts.append(_p("该版本未保存判定矩阵快照，请查看本章引用；未借用当前任务数据。"))
    parts.append("<h2>三、评估章节</h2>")
    for dim in rv.dimensions_json:
        parts.append(
            f"<h3>{_text(dim.get('dimension_code'))} "
            f"{_text(dim.get('dimension_title'))} / {_text(_risk_zh(dim.get('risk_level')))}</h3>"
        )
        if dim.get("missing_gap_note"):
            parts.append(_p("核查缺口：" + dim["missing_gap_note"], "notice"))
        parts.append(_rp(_localize_inline_enums(dim.get("content_markdown"))))
        for cite in dim.get("citations", []) or []:
            parts.extend(
                [
                    '<aside class="citation-block">',
                    _p(
                        f"引用 {cite.get('citation_tag', '')}：{cite.get('document_title', '')} "
                        f"{cite.get('clause_number', '')} (v{cite.get('version_no', 1)})",
                        "citation",
                    ),
                    _p(cite.get("source_notice"), "meta") if cite.get("source_notice") else "",
                    _p(cite.get("quote_original")),
                    _p(cite.get("quote_zh")) if cite.get("quote_zh") else "",
                    _p(f"来源：{cite.get('source_url') or '未提供'}", "meta"),
                    _p(f"人工复核：{review_label(cite)}；{review_detail(cite)}", "meta"),
                    "</aside>",
                ]
            )
    return "".join(parts) + "</body></html>"


def render_pdf(task, rv, **kwargs):
    # Lazy import: installation/runtime failures must not degrade to unshaped text.
    os.environ.setdefault("FONTCONFIG_FILE", str(FONT_DIR / "fontconfig.xml"))
    from weasyprint import HTML
    from weasyprint.text.fonts import FontConfiguration
    from weasyprint.urls import URLFetcher

    allowed = frozenset(verified_fonts().values())

    class OfflineFonts(URLFetcher):
        def fetch(self, url, headers=None):
            if url not in allowed:
                raise ValueError("报告渲染禁止网络及非字体文件访问")
            return super().fetch(url, headers)

    with _RENDER_LOCK:
        return HTML(
            string=report_html(task, rv, **kwargs),
            url_fetcher=OfflineFonts(
                allowed_protocols={"file"},
                fail_on_errors=True,
            ),
        ).write_pdf(
            font_config=FontConfiguration(),
            full_fonts=False,
        )
