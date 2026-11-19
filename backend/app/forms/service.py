# ruff: noqa: E501
"""Official forms: the catalogue, where each value comes from, and the filled file.

The catalogue (``official/forms.json``) says, for every control a form has, what it means, whether
the assessment already holds the value (``fact``) and what to ask when it does not. A value comes
from, in this order: what the user answered for this form, then the facts of the linked
assessment. The filled file is the publisher's own file with the values written at the position of
each control; see ``pdf_fill``."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.forms import derive, f118_matrix
from app.forms.pdf_fill import FillReport, FormField, fill_pdf, list_fields

OFFICIAL = Path(__file__).resolve().parent / "official"


class CatalogError(RuntimeError):
    """The catalogue and the stored official file disagree (the file changed or is missing)."""


@lru_cache(maxsize=1)
def catalog() -> dict[str, dict[str, Any]]:
    forms = json.loads((OFFICIAL / "forms.json").read_text(encoding="utf-8"))["forms"]
    if (OFFICIAL / "forms_auto.json").exists():  # 由原件自动读出的准入官方表格（build_auto）
        forms += json.loads((OFFICIAL / "forms_auto.json").read_text(encoding="utf-8"))["forms"]
    out: dict[str, dict[str, Any]] = {}
    for form in forms:
        path = OFFICIAL / form["file"]
        if not path.exists():
            raise CatalogError(f"官方文件缺失：{form['file']}")
        if hashlib.sha256(path.read_bytes()).hexdigest() != form["sha256"]:
            raise CatalogError(f"官方文件与登记的指纹不一致：{form['file']}（文件被改动或已换版）")
        _fill_limits(form, path)
        from app.admission.names import normalize_body

        form["authority_zh"] = normalize_body(form.get("authority_zh", ""), form.get("country", ""))  # 机构名全站一种写法
        out[form["code"]] = form
    return out


def _fill_limits(form: dict[str, Any], path) -> None:
    """Every text item's character limit is at most what its box takes (``pdf_fill.max_chars_for``), and items
    without one get it: the enterprise sees the limit while typing instead of learning at export that the
    value did not fit."""
    from app.forms.pdf_fill import max_chars_for

    todo = [i for s in form["sections"] for i in s["items"] if i["kind"] == "text" and i.get("pdf_key")]
    grids = [i for s in form["sections"] for i in s["items"] if i["kind"] == "matrix"]
    if not (todo or grids) or form.get("kind") == "docx":
        return
    fields = {f.key: f for f in (fixed_fields(form) or list_fields(path.read_bytes()))}
    for item in grids:  # 大表每行的小格：同一条规则
        for row in item["matrix"]["rows"]:
            row["limits"] = f118_matrix.row_limits(row, fields)
    is_cndp_manual = form.get("code") in ("MA_F118", "MA_F211", "MA_F112")
    for item in todo:
        f = fields.get(item["pdf_key"])
        if f is None:
            continue
        if f.cells and not f.rules:
            item.setdefault("store_chars", 4000)
            continue
        if f.rules:
            item.setdefault("store_chars", 4000)
            continue
        # 登记时写的上限也不能超过格子放得下的字数（手工登记的表有写成 40、格子只放得下 20 的）
        room = max_chars_for(f)
        # 页面输入框按格子大小限字；保存时留足完整内容以便导出触发 409 提示或自动生成附页续表
        default_store = (item.get("max_chars") or 400) if is_cndp_manual else 4000
        item.setdefault("store_chars", default_store)
        item["max_chars"] = min(item["max_chars"], room) if item.get("max_chars") else room


def fixed_fields(form: dict[str, Any]) -> list[FormField] | None:
    """The controls of a form whose file has none of its own: the boxes the catalogue records."""
    if form.get("kind") != "pdf_fixed":
        return None
    return [
        FormField(
            key=key, name=key, kind=b["kind"], page=b["page"], left=b["left"], bottom=b["bottom"],
            right=b["right"], top=b["top"], cells=b.get("cells", 0), rules=tuple(b.get("rules", ())),
            segments=tuple((a, c) for a, c in b.get("segments", ())), erase=bool(b.get("erase", False)),
        )
        for key, b in form["boxes"].items()
    ]


def profile_values(forms_answers: list[tuple[str, dict[str, Any]]], origins: dict[str, int] | None = None) -> dict[str, str]:
    """The company facts an enterprise has already written in any of its official forms, by fact
    key (address, contact person, manufacturer...), newest first: a value given once is offered
    on every other form instead of being asked again. ``forms_answers``: (form code, answers),
    newest first. ``origins``, when given, receives for each fact the index of the form it came from."""
    out: dict[str, str] = {}
    cat = catalog()
    for n, (code, answers) in enumerate(forms_answers):
        form = cat.get(code)
        if form is None:
            continue
        for section in form["sections"]:
            for item in section["items"]:
                key, value = item.get("fact"), (answers or {}).get(item["id"])
                if key and item["kind"] == "text" and isinstance(value, str) and value.strip() and key not in out:
                    out[key] = value.strip()
                    if origins is not None:
                        origins[key] = n
    return out


def get_form(code: str) -> dict[str, Any] | None:
    return catalog().get(code)


def summary(form: dict[str, Any]) -> dict[str, Any]:
    items = [i for s in form["sections"] for i in s["items"]]
    return {
        "code": form["code"],
        "country": form["country"],
        "direction": form["direction"],
        "name_zh": form["name_zh"],
        "authority_zh": form["authority_zh"],
        "version": form["version"],
        "legal_basis_zh": form["legal_basis_zh"],
        "prerequisite_zh": form["prerequisite_zh"],
        "timing_zh": form["timing_zh"],
        "source_url": form["source_url"],
        "language": form.get("language", ""),
        "kind": "docx" if form.get("kind") == "docx" else "pdf",
        "items": len(items),
        "required": sum(1 for i in items if i["required"]),
    }


@dataclass
class ItemState:
    item: dict[str, Any]
    value: Any  # text, or option id(s) for radio / check
    source: str  # "answer" | "assessment" | "missing"


def _from_assessment(item: dict[str, Any], facts: dict[str, str]) -> str:
    key = item.get("fact")
    return str(facts.get(key, "") or "").strip() if key else ""


def resolve(form: dict[str, Any], answers: dict[str, Any], facts: dict[str, str]) -> list[tuple[dict, list[ItemState]]]:
    """Per section, the state of every item."""
    out = []
    for section in form["sections"]:
        states = []
        for item in section["items"]:
            given = answers.get(item["id"])
            if item["kind"] == "matrix":
                rows_on = f118_matrix.clean(item["matrix"], given) if given else {}
                if not rows_on:  # 平台推得出的行（如车联网项目的位置数据）
                    derived = f118_matrix.clean(item["matrix"], derive.value(item, facts, form) or {})
                    if derived:
                        states.append(ItemState(item, derived, "assessment"))
                        continue
                states.append(ItemState(item, rows_on, "answer" if rows_on else "missing"))
                continue
            if given not in (None, "", []):
                states.append(ItemState(item, given, "answer"))
                continue
            known = _from_assessment(item, facts)
            if known and item["kind"] == "text":
                states.append(ItemState(item, known, "assessment"))
            elif (derived := derive.value(item, facts, form)) not in (None, "", []):
                states.append(ItemState(item, derived, "assessment"))  # 平台推得出的（国家名、法人、例外情形）
            else:
                states.append(ItemState(item, "" if item["kind"] == "text" else [], "missing"))
        out.append((section, states))
    return out


def missing_questions(form: dict[str, Any], answers: dict[str, Any], facts: dict[str, str]) -> list[dict[str, str]]:
    """The required items nobody has filled yet, as questions."""
    out = []
    for section, states in resolve(form, answers, facts):
        for st in states:
            if st.item["kind"] == "matrix" and st.source == "answer":
                labels = {r["id"]: r["label_zh"] for r in st.item["matrix"]["rows"]}
                for rid, v in st.value.items():
                    for field, zh in (("purpose", "处理目的"), ("persons", "涉及的人群")):
                        row = next((r for r in st.item["matrix"]["rows"] if r["id"] == rid), {})
                        if field == "purpose" and not row.get("purpose"):
                            continue  # F211/F112 的大表没有“处理目的”一列
                        if not v.get(field):
                            out.append({"id": f"{st.item['id']}/{rid}/{field}", "section": section["id"],
                                        "label": f"{labels[rid]}：{zh}", "question": f"“{labels[rid]}”这一类数据的{zh}是？", "help": ""})
                continue
            if st.item["required"] and st.source == "missing":
                out.append(
                    {
                        "id": st.item["id"],
                        "section": section["id"],
                        "label": st.item["label_zh"],
                        "question": st.item["question_zh"],
                        "help": st.item["help_zh"],
                    }
                )
    return out


def clean_answers(form: dict[str, Any], raw: dict[str, Any]) -> dict[str, Any]:
    """Keep only answers to items the form has; a choice must be one of its options."""
    items = {i["id"]: i for s in form["sections"] for i in s["items"]}
    out: dict[str, Any] = {}
    for key, value in raw.items():
        item = items.get(key)
        if item is None:
            continue
        if item["kind"] == "matrix":
            out[key] = f118_matrix.clean(item["matrix"], value)  # 空也保留：把所有类别取消勾选就是清空
            continue
        if item["kind"] == "text":
            text = " ".join(str(value or "").split())
            limit = item.get("store_chars") or 4000
            if text:
                out[key] = text[:limit]
        elif item["kind"] == "radio":
            if value in (item["options"] or {}):
                out[key] = value
        elif item["kind"] == "check":
            picked = [v for v in (value if isinstance(value, list) else [value]) if v in (item["options"] or {})]
            if picked:
                out[key] = picked
    return out


def _render_docx(form: dict[str, Any], answers: dict[str, Any], facts: dict[str, str]) -> tuple[bytes, FillReport, list[dict]]:
    """A Word form: each item's ``pdf_key`` is the cell it goes in, ``t{table}/r{row}/c{col}`` with
    ``/a`` when the value follows the printed words of that cell."""
    import re

    from app.analysis.official_docx import fill_cells

    cells = []
    for _, states in resolve(form, answers, facts):
        for st in states:
            m = re.fullmatch(r"t(\d+)/r(\d+)/c(\d+)(/a)?", st.item.get("pdf_key") or "")
            if st.source != "missing" and m and st.item["kind"] == "text":
                cells.append((int(m[1]), int(m[2]), int(m[3]), str(st.value), bool(m[4])))
    res = fill_cells((OFFICIAL / form["file"]).read_bytes(), cells)
    return res.data, FillReport(placed=res.filled, unknown=res.missing_labels), []


def _build_overflow_annex_pdf(form: dict[str, Any], annex_rows: list[list[str]]) -> bytes:
    """Render a clean A4 continuation sheet PDF for text fields whose full content exceeded the
    fixed official form box capacity."""
    from app.analysis.document_templates import Rendered, _render_pdf

    lang = form.get("language", "fr")
    title = (
        f"Annexe complémentaire — {form['name_zh']}"
        if lang == "fr"
        else f"Continuation Annex — {form['name_zh']}"
    )
    subtitle = (
        f"主管机关：{form.get('authority_zh', '')}　｜　原表版本：{form.get('version', '')}　｜　"
        "说明：本附页系官方申报原表固定栏位容量受限时的正式续表，原表对应栏位已填写摘要及附页索引号，本附页载明完整申报内容，与原表一并签章递交。"
    )
    blocks: list[dict[str, Any]] = [
        {"type": "title", "text": f"官方申报表附页续表（{'Annexe complémentaire' if lang == 'fr' else 'Continuation Sheet'}）"},
        {"type": "paragraph", "text": f"对应官方原表：{form['name_zh']}"},
        {"type": "paragraph", "text": subtitle},
        {"type": "heading", "text": "超长栏目完整申报内容对照表"},
        {
            "type": "table",
            "head": ["附页索引", "原表栏目名称", "完整申报内容"],
            "widths": [12, 28, 60],
            "rows": annex_rows,
        },
    ]
    return _render_pdf(Rendered(blocks=blocks), title)


def render(
    form: dict[str, Any],
    answers: dict[str, Any],
    facts: dict[str, str],
    *,
    allow_annex: bool = False,
) -> tuple[bytes, FillReport, list[dict]]:
    """(filled official file, fill report, items that did not fit their box)."""
    if form.get("kind") == "docx":
        return _render_docx(form, answers, facts)
    texts: dict[str, str] = {}
    checked: set[str] = set()
    labels: dict[str, dict] = {}
    for _, states in resolve(form, answers, facts):
        for st in states:
            item = st.item
            if st.source == "missing":
                continue
            if item["kind"] == "matrix":
                m_texts, m_checked, m_desc = f118_matrix.apply(item["matrix"], st.value)
                texts.update(m_texts)
                checked.update(m_checked)
                for key, d in m_desc.items():
                    labels[key] = d
                continue
            if item["kind"] == "text":
                value = str(st.value)
                if item.get("pdf_key2"):  # 栏目印了两行：第一行写不下的接着写第二行
                    value, rest = f118_matrix.split_line(value, item.get("line_chars") or 40)
                    if rest:
                        texts[item["pdf_key2"]] = rest
                        labels[item["pdf_key2"]] = item
                texts[item["pdf_key"]] = value
                labels[item["pdf_key"]] = item
            elif item["kind"] == "radio":
                rv = st.value[0] if isinstance(st.value, list) and st.value else st.value
                if isinstance(rv, str) and rv in (item["options"] or {}):
                    checked.add(item["options"][rv])
            elif item["kind"] == "check":
                vals = st.value if isinstance(st.value, list) else [st.value]
                checked.update(item["options"][v] for v in vals if isinstance(v, str) and v in (item["options"] or {}))
    # 汉字名称等放在法语表格里：填值本身不翻译，企业按官方语言填写；这里只负责放进格子
    data = (OFFICIAL / form["file"]).read_bytes()
    fixed = fixed_fields(form)
    filled, report = fill_pdf(data, texts, checked, fields=fixed)
    if allow_annex and report.overflow:
        from app.forms.pdf_fill import append_pdf_pages, fit_summary_for_box

        field_map = {f.key: f for f in (fixed or list_fields(data))}
        annex_rows: list[list[str]] = []
        lang = form.get("language", "fr")
        patched_texts = dict(texts)
        for k in list(report.overflow):
            f_obj = field_map.get(k)
            meta = labels.get(k)
            if f_obj is None or meta is None or f_obj.cells or f_obj.segments:
                continue
            full_text = str(texts.get(k) or "").strip()
            if not full_text:
                continue
            idx = len(annex_rows) + 1
            ref_tag = f"(Voir Annexe #{idx})" if lang == "fr" else f"(See Annex #{idx})"
            patched_texts[k] = fit_summary_for_box(data, f_obj, full_text, ref_tag)
            lbl_zh = meta.get("label_zh") or k
            lbl_orig = str(meta.get("label_original") or meta.get("label_fr") or "").strip()
            col_label = f"{lbl_zh} / {lbl_orig}" if lbl_orig and lbl_orig not in lbl_zh else lbl_zh
            annex_rows.append([f"#{idx}", col_label, full_text])
        if annex_rows:
            filled, report = fill_pdf(data, patched_texts, checked, fields=fixed)
            annex_pdf = _build_overflow_annex_pdf(form, annex_rows)
            filled = append_pdf_pages(filled, annex_pdf)
    field_map_all = {f.key: f for f in (fixed or list_fields(data))}
    too_long = []
    for k in report.overflow:
        if k not in labels:
            continue
        f_obj = field_map_all.get(k)
        raw_val = str(texts.get(k) or "").strip()
        if f_obj is not None and f_obj.segments and ("@" not in raw_val or raw_val.startswith("@") or raw_val.endswith("@")):
            too_long.append(
                {
                    "id": labels[k]["id"],
                    "label": labels[k]["label_zh"] + "（原表印有固定 @ 分隔符，请填写包含 @ 的完整电子邮箱地址）",
                    "max_chars": None,
                }
            )
        elif f_obj is not None and f_obj.cells:
            too_long.append(
                {
                    "id": labels[k]["id"],
                    "label": labels[k]["label_zh"] + f"（原表为 {f_obj.cells} 格定长栏位，当前填写内容超出格数上限）",
                    "max_chars": f_obj.cells,
                }
            )
        else:
            too_long.append(
                {
                    "id": labels[k]["id"],
                    "label": labels[k]["label_zh"],
                    "max_chars": labels[k].get("max_chars"),
                }
            )
    too_long.extend(
        {"id": labels[k]["id"], "label": labels[k]["label_zh"] + "（含阿拉伯文等无法直接写入的文字，请用法文或英文填写）", "max_chars": None}
        for k in report.unwritable
        if k in labels
    )
    return filled, report, too_long
