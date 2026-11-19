# ruff: noqa: E501
"""Fill an official Word template as published: the official .docx is the document, values are put
into its own table cells and its own check boxes; its wording, layout, fonts and notes are untouched.

Used for the Chinese data-export declaration form (annex 3 of the CAC guide, 3rd edition): the annex
is cut out of the guide (from its title to the next annex) into a document of its own, keeping the
guide's styles and page settings. A cell is found by the label printed next to it, not by position,
so a re-issued template with rows moved still fills correctly; a label that is not found is reported.

The XML is edited with the standard library: the original root start tag (with every namespace the
file declares) is put back after serialisation so Word sees exactly the declarations it wrote.
"""

from __future__ import annotations

import copy
import io
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from xml.etree import ElementTree as ET

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
W = "{" + W_NS + "}"
CHECKED = "F0FE"  # Wingdings 里打了勾的方框
OFFICIAL_DIR = Path(__file__).resolve().parents[1] / "forms" / "official"
CN_GUIDE = OFFICIAL_DIR / "CN_CAC_security_assessment_guide_v3_2025-06-27.docx"
# 《个人信息出境标准合同备案指南（第二版）》（国家网信办，2024-03-22），附件 5 为个人信息保护影响评估报告模板
CN_SC_GUIDE = OFFICIAL_DIR / "CN_CAC_standard_contract_filing_guide_v2_2024-03-22.docx"


@dataclass
class FillResult:
    data: bytes
    filled: list[str] = field(default_factory=list)
    missing_labels: list[str] = field(default_factory=list)  # 模板里没找到的栏目（模板改版时会出现）


def _text(el) -> str:
    return "".join(t.text or "" for t in el.iter(W + "t"))


def _flat(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def _register_namespaces(xml: bytes) -> None:
    for prefix, uri in re.findall(rb'xmlns:([A-Za-z0-9]+)="([^"]+)"', xml[:5000]):
        ET.register_namespace(prefix.decode(), uri.decode())


def _serialise(root, original: bytes) -> bytes:
    out = ET.tostring(root, encoding="utf-8", xml_declaration=False)
    start = re.search(rb"<w:document\b[^>]*>", original).group(0)
    out = re.sub(rb"^<w:document\b[^>]*>", start, out, count=1)
    return b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\r\n' + out


def _clean_core_xml(title: str) -> bytes:
    from xml.sax.saxutils import escape as _esc

    safe = _esc((title or "数据合规正式文件").strip())
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
        'xmlns:dc="http://purl.org/dc/elements/1.1/" '
        'xmlns:dcterms="http://purl.org/dc/terms/" '
        'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
        f"<dc:title>{safe}</dc:title>"
        "<dc:creator></dc:creator>"
        "<cp:lastModifiedBy></cp:lastModifiedBy>"
        "</cp:coreProperties>"
    ).encode()


def _reset_sect_page_start(body) -> None:
    if body is None:
        return
    for idx, sect in enumerate(body.iter(W + "sectPr")):
        pg_num = sect.find(W + "pgNumType")
        if idx == 0:
            if pg_num is None:
                pg_num = ET.SubElement(sect, W + "pgNumType")
            pg_num.set(W + "fmt", "decimal")
            pg_num.set(W + "start", "1")
        elif pg_num is not None:
            pg_num.set(W + "fmt", "decimal")
            pg_num.attrib.pop(W + "start", None)


def _clean_cac_footer_xml() -> bytes:
    rpr = (
        "<w:rPr>"
        '<w:rFonts w:ascii="Times New Roman" w:hAnsi="Times New Roman" w:eastAsia="仿宋" w:cs="Times New Roman"/>'
        '<w:sz w:val="21"/><w:szCs w:val="21"/>'
        "</w:rPr>"
    )
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<w:ftr xmlns:w="{W_NS}">'
        '<w:p><w:pPr><w:jc w:val="center"/></w:pPr>'
        f'<w:r>{rpr}<w:fldChar w:fldCharType="begin" w:dirty="true"/></w:r>'
        f'<w:r>{rpr}<w:instrText xml:space="preserve"> PAGE </w:instrText></w:r>'
        f'<w:r>{rpr}<w:fldChar w:fldCharType="separate"/></w:r>'
        f"<w:r>{rpr}<w:t>1</w:t></w:r>"
        f'<w:r>{rpr}<w:fldChar w:fldCharType="end"/></w:r>'
        "</w:p></w:ftr>"
    ).encode()


def _ensure_update_fields_xml(raw_settings: bytes) -> bytes:
    if b"updateFields" in raw_settings:
        return raw_settings
    txt = raw_settings.decode("utf-8", errors="ignore")
    if "</w:settings>" in txt:
        return txt.replace("</w:settings>", '<w:updateFields w:val="true"/></w:settings>', 1).encode()
    return raw_settings


def _needs_clean_cac_footer(raw_ftr: bytes) -> bool:
    if b"txbx" in raw_ftr or b"textbox" in raw_ftr or b"AlternateContent" in raw_ftr:
        return True
    try:
        return not _text(ET.fromstring(raw_ftr)).strip()
    except ET.ParseError:
        return False


def _write_clean_zip(zin: zipfile.ZipFile, doc_xml: bytes, title: str) -> bytes:
    buf = io.BytesIO()
    has_core = False
    clean_ftr = _clean_cac_footer_xml()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            if item.filename == "word/document.xml":
                zout.writestr(item, doc_xml)
            elif item.filename == "docProps/core.xml":
                has_core = True
                zout.writestr(item, _clean_core_xml(title))
            elif item.filename == "word/settings.xml":
                zout.writestr(item, _ensure_update_fields_xml(zin.read(item.filename)))
            elif re.fullmatch(r"word/footer\d+\.xml", item.filename):
                raw_ftr = zin.read(item.filename)
                zout.writestr(item, clean_ftr if _needs_clean_cac_footer(raw_ftr) else raw_ftr)
            else:
                zout.writestr(item, zin.read(item.filename))
        if not has_core:
            zout.writestr("docProps/core.xml", _clean_core_xml(title))
    return buf.getvalue()


def _cut_annex(body, start_title: str, end_title: str) -> None:
    """Keep only the body elements from the paragraph ``start_title`` up to (not including)
    ``end_title``; the section properties at the end are kept."""
    children = list(body)
    sect = body.find(W + "sectPr")
    start = next(i for i, el in enumerate(children) if el.tag == W + "p" and _text(el).strip() == start_title)
    end = next((i for i, el in enumerate(children) if i > start and el.tag == W + "p" and _text(el).strip() == end_title), None)
    if end is None:
        # 下一个附件的标号和上一段落在同一段里（中间是分页符）：保留这一段，把分页符及其后的内容去掉
        end = next((i for i, el in enumerate(children) if i > start and el.tag == W + "p" and _text(el).strip().endswith(end_title)), len(children))
        if end < len(children):
            para = children[end]
            runs = para.findall(W + "r")
            cut = next((k for k, r in enumerate(runs) if any(b.get(W + "type") == "page" for b in r.iter(W + "br"))), None)
            if cut is not None:
                for r in runs[cut:]:
                    para.remove(r)
                end += 1
    keep = children[start + 1 : end]  # “附件3”这一行标号不要，从表名开始
    for el in children:
        body.remove(el)
    for el in keep:
        if el.tag != W + "sectPr":
            body.append(el)
    if sect is not None:
        body.append(sect)


def _strip_template_word(body) -> None:
    """用户下载的是按本项目填好的申报件或评估报告，只去掉大标题括号里的“（模板）”，保留官方原表其余原文。"""
    for p in body.iter(W + "p"):
        ts = list(p.iter(W + "t"))
        if not ts:
            continue
        joined = "".join(t.text or "" for t in ts)
        if "（模板）" not in joined and "(模板)" not in joined:
            continue
        cleaned = joined.replace("（模板）", "").replace("(模板)", "")
        ts[0].text = cleaned
        for extra in ts[1:]:
            extra.text = ""


def _run_props(cell):
    r = cell.find(f".//{W}r/{W}rPr")
    return copy.deepcopy(r) if r is not None else None


def _plain(props):
    """The run style of an instruction text without what marks it as instruction (italic, colour)."""
    if props is None:
        return None
    for tag in ("i", "iCs", "color", "highlight"):
        for el in props.findall(W + tag):
            props.remove(el)
    return props


def _put_text(cell, value: str, props, after_print: bool = False) -> None:
    """Write ``value`` into the cell's first paragraph (lines become line breaks).
    ``after_print``: the value follows printed words (“I/We ____”), so it goes at the end of the
    paragraph holding them — the cell may start with empty spacing paragraphs."""
    paras = cell.findall(W + "p")
    worded = [q for q in paras if "".join(t.text or "" for t in q.iter(W + "t")).strip()]
    p = worded[-1] if after_print and worded else (paras[0] if paras else None)
    if p is None:
        p = ET.SubElement(cell, W + "p")
    run = ET.SubElement(p, W + "r")
    if props is not None:
        run.append(copy.deepcopy(props))
    for i, line in enumerate(value.split("\n")):
        if i:
            ET.SubElement(run, W + "br")
        t = ET.SubElement(run, W + "t")
        t.text = line
        t.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")


def _clear(cell) -> None:
    for p in cell.findall(W + "p"):
        for r in list(p):
            if r.tag != W + "pPr":
                p.remove(r)
    for extra in cell.findall(W + "p")[1:]:
        cell.remove(extra)


def _options(cell) -> list[tuple[object, str]]:
    """(box symbol, the option text after it) in reading order."""
    out: list[list] = []
    for el in cell.iter():
        if el.tag == W + "sym":
            out.append([el, ""])
        elif el.tag == W + "t" and out:
            out[-1][1] += el.text or ""
    return [(sym, _flat(label)) for sym, label in out]


def _tick(cell, wanted: list[str], other_text: str = "") -> bool:
    """Tick the boxes whose option text starts with one of ``wanted``; the “其他” box gets the free
    text written after it. True when something was ticked."""
    hit = False
    options = _options(cell)
    for sym, label in options:
        if any(w and label.startswith(_flat(w)) for w in wanted):
            sym.set(W + "char", CHECKED)
            hit = True
    if other_text and not hit:
        for sym, label in options:
            if label.startswith("其他"):
                sym.set(W + "char", CHECKED)
                _put_text(cell, other_text, _run_props(cell))
                hit = True
                break
    return hit


@dataclass
class Slot:
    label: str  # 印在表格里的栏目名
    value: str = ""
    choose: list[str] = field(default_factory=list)  # 勾选项（选项文字的开头）
    other: str = ""  # 勾“其他”时写在后面的文字
    group: str = ""  # 同名栏目所在的大项（第一列，如“2法定代表人信息”）
    in_cell: bool = False  # 勾选框就在这个格子里（如“□个人信息”），不是在右边的格子
    prefix: bool = False  # 栏目名只比开头（如很长的承诺句）


def _merge_broken_note_paragraphs(body) -> None:
    """Merge hard-wrapped continuation lines inside CAC trailing instructions ('注：1. ... 2. ... 3. ... 4. ...')
    so font-width differences never leave single-character orphan lines such as '景' / '表' / '“仿' / '下'."""
    children = list(body)
    in_notes = False
    active_p = None
    item_head_re = re.compile(r"^\s*(?:注[：:]\s*)?\d+[\.．、]")
    for el in children:
        if el.tag != W + "p":
            if in_notes and el.tag == W + "tbl":
                in_notes = False
                active_p = None
            continue
        txt = _text(el).strip()
        if not txt:
            continue
        if txt.startswith(("注：", "注:")):
            in_notes = True
            active_p = el
            continue
        if not in_notes:
            continue
        if item_head_re.match(txt):
            active_p = el
            continue
        if active_p is not None:
            for child in list(el):
                if child.tag != W + "pPr":
                    el.remove(child)
                    active_p.append(child)
            body.remove(el)


def fill_table_form(template: bytes, start_title: str, end_title: str, slots: list[Slot], hints: dict[str, str] | None = None) -> FillResult:
    """Cut the annex out of ``template`` and fill ``slots``. ``hints``: grey instruction text printed
    in a value cell (e.g. “按自然人（去重）统计数量”) that is replaced when a value is given."""
    hints = hints or {}
    zin = zipfile.ZipFile(io.BytesIO(template))
    original = zin.read("word/document.xml")
    _register_namespaces(original)
    root = ET.fromstring(original)
    body = root.find(W + "body")
    _cut_annex(body, start_title, end_title)
    _strip_template_word(body)
    _merge_broken_note_paragraphs(body)
    result = FillResult(b"")
    for table in body.iter(W + "tbl"):
        group = ""
        for tr in table.findall(W + "tr"):
            cells = tr.findall(W + "tc")
            if cells and _flat(_text(cells[0])):
                head = _flat(_text(cells[0]))
                if re.match(r"^\d", head):
                    group = head
            for i, cell in enumerate(cells):
                label = _flat(_text(cell))
                for slot in slots:
                    if slot.label.startswith("DONE:"):
                        continue
                    same = label.startswith(_flat(slot.label)) if slot.prefix else label == _flat(slot.label)
                    if not same or (slot.group and not group.startswith(_flat(slot.group))):
                        continue
                    target = cell if slot.in_cell else (cells[i + 1] if i + 1 < len(cells) else None)
                    if target is None:
                        continue
                    if slot.choose or slot.other:
                        if _tick(target, slot.choose, slot.other):
                            result.filled.append(slot.label)
                    elif slot.value:
                        current = _flat(_text(target))
                        if current and current in {_flat(h) for h in hints.values()} | {_flat(h) for h in hints}:
                            props = _plain(_run_props(target))  # 说明文字是斜体：填的内容用正体
                            _clear(target)
                            _put_text(target, slot.value, props)
                        elif not current:
                            _put_text(target, slot.value, _run_props(cell))
                        else:
                            _put_text(target, slot.value, _run_props(target))
                        result.filled.append(slot.label)
                    slot.label = "DONE:" + slot.label  # 同名栏目只填第一处（按大项区分的除外）
    result.missing_labels = [s.label for s in slots if not s.label.startswith("DONE:") and (s.value or s.choose or s.other)]
    for sym in body.iter(W + "sym"):
        if (sym.get(W + "font") or "").lower() == "wingdings" and (sym.get(W + "char") or "").upper() == "00A8":
            sym.set(W + "char", "F06F")
    _reset_sect_page_start(body)
    xml = _serialise(root, original)
    result.data = _write_clean_zip(zin, xml, "数据出境安全评估申报表")
    return result


def _first(value: str, choices: list[str]) -> list[str]:
    return [c for c in choices if c in (value or "")]


def _yes_no(value: str) -> list[str]:
    v = (value or "").strip()
    if not v or v.startswith("暂不确定"):
        return []
    return ["否"] if re.match(r"^(否|不|无|没有|不含|不涉及)", v) else ["是"]


def _parse_person(raw: str) -> dict[str, str]:
    """Parse structured or semi-structured person info ('姓名：X；职务/国籍：Y；联系电话：Z；电子邮箱：E；证件类型：居民身份证；证件号码：N')
    while remaining 100% backwards-compatible with a plain name string."""
    text = (raw or "").strip()
    if not text:
        return {}
    out = {"name": "", "title": "", "phone": "", "email": "", "id_type": "", "id_no": ""}
    email_m = re.search(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}", text)
    if email_m:
        out["email"] = email_m.group(0)
    id_types = ["居民身份证", "护照", "台湾居民来往大陆通行证", "港澳居民来往内地通行证", "外国人永久居留身份证"]
    for it in id_types:
        if it in text:
            out["id_type"] = it
            break
    parts = [p.strip() for p in re.split(r"[；;\n]+", text) if p.strip()]
    if len(parts) == 1 and "：" not in text and ":" not in text:
        sub = [p.strip() for p in re.split(r"[，,]+", text) if p.strip()]
        if len(sub) == 1:
            out["name"] = text
            return out
        parts = sub
    unlabeled: list[str] = []
    for p in parts:
        kv = re.split(r"[：:]", p, maxsplit=1)
        if len(kv) == 2:
            k, val = kv[0].strip(), kv[1].strip()
            if any(w in k for w in ("姓名", "负责人", "代表人", "经办人")) and not out["name"]:
                out["name"] = val
            elif any(w in k for w in ("职务", "国籍", "岗位", "头衔")):
                out["title"] = f"{out['title']} / {val}".strip(" /") if out["title"] else val
            elif any(w in k for w in ("电话", "手机", "座机", "联系")):
                if "@" not in val:
                    out["phone"] = val
            elif "邮箱" in k or "邮件" in k:
                out["email"] = val
            elif "证件类型" in k or "证件种类" in k:
                out["id_type"] = val
            elif any(w in k for w in ("证件号", "身份证号", "护照号", "号码")):
                out["id_no"] = val
        else:
            unlabeled.append(p)
    if not out["name"] and unlabeled:
        out["name"] = re.sub(r"[（(][^）)]*[）)]", "", unlabeled[0]).strip() or unlabeled[0]
        if not out["title"]:
            m_paren = re.search(r"[（(]([^）)]+)[）)]", unlabeled[0])
            if m_paren:
                out["title"] = m_paren.group(1).strip()
            elif len(unlabeled) >= 2 and "@" not in unlabeled[1] and not re.search(r"\d{7,}", unlabeled[1]):
                title_parts = [unlabeled[1]]
                if len(unlabeled) >= 3 and unlabeled[2] not in id_types and "@" not in unlabeled[2] and not re.search(r"\d{5,}", unlabeled[2]):
                    title_parts.append(unlabeled[2])
                out["title"] = " / ".join(title_parts)
    if not out["phone"]:
        for seg in parts:
            if any(w in seg for w in ("证件", "身份证", "护照", "信用代码")) or re.search(r"\b\d{17}[\dXx]\b", seg):
                continue
            pm = re.search(r"(?:\+?\d{2,4}[-\s])?\d{3,4}[-\s]?\d{4,8}|\b1[3-9]\d{9}\b", seg)
            if pm:
                out["phone"] = pm.group(0).strip()
                break
    if not out["id_no"]:
        idm = re.search(r"\b\d{17}[\dXx]\b|\b[EGDSP]\d{7,8}\b", text)
        if idm:
            out["id_no"] = idm.group(0)
    if not out["name"]:
        out["name"] = text
    return out


def _parse_security_org(raw: str) -> tuple[str, str]:
    text = (raw or "").strip()
    if not text:
        return "", ""
    cnt = ""
    m = re.search(r"(?:人数|专兼职|共计|共|成员|编制)[：:\s]*(\d+\s*人)", text) or re.search(r"(\d+\s*人)", text)
    if m:
        cnt = m.group(1).replace(" ", "")
    name = text
    for sep in ("；", ";"):
        if sep in name:
            first = name.split(sep, 1)[0].strip()
            name = re.sub(r"^(?:机构名称|名称)[：:]\s*", "", first)
            break
    name = re.sub(r"[（(][^）)]*\d+\s*人[^）)]*[）)]", "", name).strip("，, ")
    return name or text, cnt


def _parse_recipient_info(rec_raw: str, contact_raw: str) -> dict[str, str]:
    rec = (rec_raw or "").strip()
    con = (contact_raw or "").strip()
    out = {"name": "", "address": "", "biz": "", "head_name": "", "head_title": "", "contact": ""}
    if rec:
        parts = [p.strip() for p in re.split(r"[；;\n]+", rec) if p.strip()]
        if len(parts) == 1 and "：" not in rec and ":" not in rec:
            out["name"] = rec
        else:
            unlabeled: list[str] = []
            for p in parts:
                kv = re.split(r"[：:]", p, maxsplit=1)
                if len(kv) == 2:
                    k, val = kv[0].strip(), kv[1].strip()
                    if "名称" in k and not out["name"]:
                        out["name"] = val
                    elif "地址" in k or "注册地" in k or "办公地" in k:
                        out["address"] = val
                    elif "业务" in k or "经营" in k or "职能" in k:
                        out["biz"] = val
                else:
                    unlabeled.append(p)
            if not out["name"]:
                out["name"] = unlabeled[0] if unlabeled else rec
            if not out["address"] and len(unlabeled) >= 2:
                out["address"] = unlabeled[1]
            if not out["biz"] and len(unlabeled) >= 3:
                out["biz"] = unlabeled[2]
    if con:
        person = _parse_person(con)
        if ("：" in con or ":" in con or "；" in con or ";" in con) and person.get("name") and person["name"] != con:
            out["head_name"] = person["name"]
            out["head_title"] = person.get("title", "")
            c_parts = []
            if person.get("phone"):
                c_parts.append(f"电话：{person['phone']}")
            if person.get("email"):
                c_parts.append(f"邮箱：{person['email']}")
            out["contact"] = "；".join(c_parts) if c_parts else con
        else:
            out["contact"] = con
    return out


def cn_declaration(values: dict[str, str]) -> FillResult:
    """附件 3 数据出境安全评估申报表：在官方模板上填写。"""
    v = {k: str(x or "").strip() for k, x in values.items()}
    v = {k: ("" if x.startswith("暂不确定") else x) for k, x in v.items()}
    industries = ["工业", "电信", "交通", "金融", "自然资源", "卫生健康", "教育", "科技", "能源", "国防科工", "文旅", "跨境电商", "零售", "互联网"]
    id_choices = ["居民身份证", "护照", "台湾居民来往大陆通行证", "港澳居民来往内地通行证", "外国人永久居留身份证"]
    # 平台只服务车企：没写行业的按交通勾，没写单位性质的按企业勾
    picked = _first(v.get("industry_field", ""), industries) or (["交通"] if not v.get("industry_field") or any(w in v.get("industry_field", "") for w in ("汽车", "车联网", "智能网联")) else [])
    v.setdefault("entity_nature", "企业") if v.get("entity_nature") else v.__setitem__("entity_nature", "企业")
    # 含敏感个人信息必然含个人信息；写了涉及多少自然人（不是“0/无”）的也含个人信息
    sensitive_yes = _yes_no(v.get("sensitive_pi", "")) == ["是"]
    volume = v.get("data_volume", "")
    has_pi = sensitive_yes or (bool(volume) and not re.match(r"^\s*(0|零|无|不涉及|没有)", volume)) or (
        bool(v.get("data_types")) and not re.match(r"^(无|不含|没有)", v.get("sensitive_pi", "") or "x")
    )
    rep = _parse_person(v.get("legal_rep", ""))
    sec = _parse_person(v.get("security_officer", ""))
    agt = _parse_person(v.get("agent_info", ""))
    org_name, org_cnt = _parse_security_org(v.get("security_org", ""))
    rcp = _parse_recipient_info(v.get("recipient", ""), v.get("recipient_contact", ""))

    def _person_slots(p: dict[str, str], grp: str) -> list[Slot]:
        if not p:
            return []
        picked_id = _first(p.get("id_type", ""), id_choices)
        other_id = "" if picked_id else p.get("id_type", "")
        return [
            Slot("姓名", p.get("name", ""), group=grp),
            Slot("职务/国籍", p.get("title", ""), group=grp),
            Slot("联系电话", p.get("phone", ""), group=grp),
            Slot("电子邮箱", p.get("email", ""), group=grp),
            Slot("证件类型", choose=picked_id, other=other_id, group=grp),
            Slot("证件号码", p.get("id_no", ""), group=grp),
        ]

    slots = [
        Slot("单位名称", v.get("company_name", "")),
        Slot("单位性质", choose=_first(v.get("entity_nature", ""), ["政府部门", "事业单位", "企业", "社会组织"]), other="" if _first(v.get("entity_nature", ""), ["政府部门", "事业单位", "企业", "社会组织"]) else v.get("entity_nature", "")),
        Slot("单位类型", choose=_first(v.get("entity_type", ""), ["中外合资", "港澳台资", "内资", "外资"]), other="" if _first(v.get("entity_type", ""), ["中外合资", "港澳台资", "内资", "外资"]) else v.get("entity_type", "")),
        Slot("单位注册地", v.get("registered_place", "")),
        Slot("办公所在地", v.get("office_place", "")),
        Slot("员工数量", v.get("staff_count", "")),
        Slot("统一社会信用代码", v.get("credit_code", "")),
        Slot("是否为关键信息基础设施运营者", choose=_yes_no(v.get("is_cii", ""))),
        Slot("处理个人信息规模", v.get("pi_scale", "")),
        *_person_slots(rep, "2"),
        *_person_slots(sec, "3"),
        Slot("管理机构名称", org_name, group="3"),
        Slot("管理机构人数", org_cnt, group="3"),
        *_person_slots(agt, "4"),
        Slot("5数据处理者遵守中国法律、行政法规、部门规章情况", v.get("law_compliance", "")),
        Slot("出境场景简述", v.get("scenario_summary", "") or v.get("scenario", ""), group="6数据出境场景1"),
        Slot("重要数据", choose=["重要数据"] if _yes_no(v.get("important_data", "")) == ["是"] else [], in_cell=True, group="6数据出境场景1"),
        Slot("个人信息", choose=["个人信息"] if has_pi else [], in_cell=True, group="6数据出境场景1"),
        Slot("是否包含敏感个人信息", choose=_yes_no(v.get("sensitive_pi", "")), group="6数据出境场景1"),
        Slot("涉及行业/领域", choose=picked, other="" if picked else v.get("industry_field", ""), group="6数据出境场景1"),
        Slot("涉及自然人（去重）数量", v.get("data_volume", ""), group="6数据出境场景1"),
        Slot("境外接收方名称", rcp.get("name", ""), group="6数据出境场景1"),
        Slot("所在国家或者地区", v.get("recipient_country", ""), group="6数据出境场景1"),
        Slot("所在地址", rcp.get("address", ""), group="6数据出境场景1"),
        Slot("主营业务", rcp.get("biz", ""), group="6数据出境场景1"),
        Slot("负责人姓名", rcp.get("head_name", ""), group="6数据出境场景1"),
        Slot("负责人职务", rcp.get("head_title", ""), group="6数据出境场景1"),
        Slot("联系方式", rcp.get("contact", ""), group="6数据出境场景1"),
        # 承诺栏不预先勾选：由企业核对后自己勾、盖章
    ]
    hints = {
        "处理个人信息规模": "按自然人（去重）统计数量",
        "5数据处理者遵守中国法律、行政法规、部门规章情况": "简述近2年在业务经营活动中受到行政处罚和有关主管监管部门调查及整改情况，重点说明数据和网络安全方面相关情况",
        "出境场景简述": "据实填写此次申报的数据出境场景业务、数据出境目的、数据出境方式等，应与法律文件中涉及业务名称一致，不超过100字（数据出境方式参考：公共互联网传输、跨境专线传输、公共互联网远程访问、跨境专线远程访问等）",
        "涉及自然人（去重）数量": "包括当年已出境数量、未来三年出境数量以及两者的关系",
        "联系方式": "电话：邮箱：",
    }
    return fill_table_form(CN_GUIDE.read_bytes(), "附件3", "附件4", slots, hints)


_FONT_MAP = {
    "仿宋_GB2312": "仿宋",
    "楷体_GB2312": "楷体",
    "方正小标宋简体": "华文中宋",
}


def _normalize_doc_fonts(body) -> None:
    """Map legacy GB2312 / missing font names to standard Windows/Office Chinese official-document fonts."""
    for rf in body.iter(W + "rFonts"):
        for attr in (W + "eastAsia", W + "ascii", W + "hAnsi", W + "cs"):
            cur = rf.get(attr)
            if cur in _FONT_MAP:
                rf.set(attr, _FONT_MAP[cur])


def _set_font_family(rpr, east_asia: str, latin: str = "Times New Roman") -> None:
    if rpr is None:
        return
    rf = rpr.find(W + "rFonts")
    if rf is None:
        rf = ET.Element(W + "rFonts")
        rpr.insert(0, rf)
    rf.set(W + "eastAsia", east_asia)
    rf.set(W + "ascii", latin)
    rf.set(W + "hAnsi", latin)
    rf.set(W + "cs", latin)


def _set_bold(rpr, on: bool) -> None:
    if rpr is None:
        return
    for tag in ("b", "bCs"):
        for el in rpr.findall(W + tag):
            rpr.remove(el)
        if on:
            ET.SubElement(rpr, W + tag)


def _ensure_rpr(run):
    rpr = run.find(W + "rPr")
    if rpr is None:
        rpr = ET.Element(W + "rPr")
        run.insert(0, rpr)
    return rpr


def _style_official_para(para, *, east_asia: str, sz: str = "28", bold: bool = False, keep_next: bool = False, center: bool = False) -> None:
    ppr = para.find(W + "pPr")
    if ppr is None:
        ppr = ET.Element(W + "pPr")
        para.insert(0, ppr)
    if keep_next and ppr.find(W + "keepNext") is None:
        ppr.insert(1 if ppr.find(W + "pStyle") is not None else 0, ET.Element(W + "keepNext"))
    if center:
        for old in ppr.findall(W + "ind") + ppr.findall(W + "jc"):
            ppr.remove(old)
        ind = ET.SubElement(ppr, W + "ind")
        ind.set(W + "left", "0")
        ind.set(W + "right", "0")
        ind.set(W + "firstLine", "0")
        ind.set(W + "firstLineChars", "0")
        jc = ET.SubElement(ppr, W + "jc")
        jc.set(W + "val", "center")
    else:
        sp = ppr.find(W + "spacing")
        if sp is None:
            sp = ET.SubElement(ppr, W + "spacing")
        sp.set(W + "line", "520")
        sp.set(W + "lineRule", "exact")
        ind = ppr.find(W + "ind")
        if ind is None:
            ind = ET.SubElement(ppr, W + "ind")
        ind.set(W + "firstLine", "560")
        ind.set(W + "firstLineChars", "200")
    for run in para.findall(W + "r"):
        rpr = _ensure_rpr(run)
        _plain(rpr)
        _set_font_family(rpr, east_asia)
        _set_font_size(rpr, sz)
        _set_bold(rpr, bold)


def _strip_trailing_semicolon(para) -> None:
    ts = [t for t in para.iter(W + "t") if (t.text or "")]
    if not ts:
        return
    last = ts[-1].text or ""
    stripped = re.sub(r"[；;]\s*$", "", last)
    if stripped != last:
        ts[-1].text = stripped


def _para_like(guide, text: str):
    """An answer paragraph formatted per CAC official report specification: 四号 仿宋 + Times New Roman,
    regular upright black, 2-char first-line indent, 26pt line spacing."""
    p = ET.Element(W + "p")
    ppr = guide.find(W + "pPr")
    if ppr is not None:
        p.append(copy.deepcopy(ppr))
    else:
        ppr = ET.SubElement(p, W + "pPr")
    ppr = p.find(W + "pPr")
    for kn in ppr.findall(W + "keepNext"):
        ppr.remove(kn)
    sp = ppr.find(W + "spacing")
    if sp is None:
        sp = ET.SubElement(ppr, W + "spacing")
    sp.set(W + "line", "520")
    sp.set(W + "lineRule", "exact")
    ind = ppr.find(W + "ind")
    if ind is None:
        ind = ET.SubElement(ppr, W + "ind")
    ind.set(W + "firstLine", "560")
    ind.set(W + "firstLineChars", "200")
    jc = ppr.find(W + "jc")
    if jc is None:
        jc = ET.SubElement(ppr, W + "jc")
    jc.set(W + "val", "both")
    plain_props = _plain(_run_props(guide))
    props = plain_props if plain_props is not None else ET.Element(W + "rPr")
    _set_font_family(props, "仿宋")
    _set_font_size(props, "28")
    _set_bold(props, False)
    for i, line in enumerate(text.split("\n")):
        run = ET.SubElement(p, W + "r")
        run.append(copy.deepcopy(props))
        if i:
            ET.SubElement(run, W + "br")
        t = ET.SubElement(run, W + "t")
        t.text = line
        t.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
    return p


def _cac_cell_runs(text: str, *, bold: bool = False) -> str:
    """Render cell text in CAC official template style (Times New Roman + 仿宋, black 000000, 10.5pt)."""
    from xml.sax.saxutils import escape as _esc

    from app.analysis.report_exporter import xml_safe
    from app.analysis.rich_text import parse_bold

    cite_re = re.compile(r"((?:\[\d+\])+)")
    rfonts = "<w:rFonts w:ascii='Times New Roman' w:hAnsi='Times New Roman' w:eastAsia='仿宋' w:cs='Times New Roman'/>"

    def _one_run(seg_txt: str, is_b: bool, *, sup: bool = False) -> str:
        b_tag = "<w:b/><w:bCs/>" if is_b else ""
        sz_val = "17" if sup else "21"
        va_tag = "<w:vertAlign w:val='superscript'/>" if sup else ""
        return (
            f"<w:r><w:rPr>{rfonts}{b_tag}<w:color w:val='000000'/>"
            f"<w:sz w:val='{sz_val}'/><w:szCs w:val='{sz_val}'/>{va_tag}</w:rPr>"
            f"<w:t xml:space='preserve'>{_esc(xml_safe(seg_txt))}</w:t></w:r>"
        )

    out_runs: list[str] = []
    prev_ch = ""
    for seg, seg_bold in parse_bold(text or ""):
        if not seg:
            continue
        pos = 0
        for m in cite_re.finditer(seg):
            if m.start() > pos:
                chunk = seg[pos : m.start()]
                out_runs.append(_one_run(chunk, bold or seg_bold))
                prev_ch = chunk[-1]
            ct = m.group(1)
            is_sup = bool(prev_ch and not prev_ch.isspace() and prev_ch != "[")
            out_runs.append(_one_run(ct, False if is_sup else (bold or seg_bold), sup=is_sup))
            prev_ch = ct[-1]
            pos = m.end()
        if pos < len(seg):
            tail = seg[pos:]
            out_runs.append(_one_run(tail, bold or seg_bold))
            prev_ch = tail[-1]
    return "".join(out_runs) or _one_run("", bold)


def _cac_table_xml(head: list[str], rows: list[list[str]], widths: list[int] | None = None) -> str:
    """Build a table strictly matching CAC official Word template tables (black 0.5pt borders,
    white header background, bold 仿宋 header row, left-aligned compact 仿宋 body rows)."""
    from app.analysis.document_templates import _infer_widths
    from app.analysis.report_exporter import PAGE_TEXT_WIDTH

    pct = widths or _infer_widths(head, rows)
    cols = [PAGE_TEXT_WIDTH * p // sum(pct) for p in pct]
    grid = "<w:tblGrid>" + "".join(f"<w:gridCol w:w='{c}'/>" for c in cols) + "</w:tblGrid>"
    b_single = "<w:{0} w:val='single' w:sz='4' w:space='0' w:color='000000'/>"
    tbdr = "".join(b_single.format(n) for n in ("top", "bottom", "left", "right", "insideH", "insideV"))

    def _cac_tc(cell_txt: str, col_w: int, *, is_head: bool = False) -> str:
        kn = "<w:keepNext/>" if is_head else ""
        lines = (cell_txt or "").split("\n")
        paras = "".join(
            f"<w:p><w:pPr>{kn}<w:spacing w:after='40' w:line='280' w:lineRule='auto'/>"
            "<w:ind w:firstLine='0' w:firstLineChars='0'/><w:jc w:val='left'/></w:pPr>"
            f"{_cac_cell_runs(ln, bold=is_head)}</w:p>"
            for ln in lines
        )
        return f"<w:tc><w:tcPr><w:tcW w:w='{col_w}' w:type='dxa'/></w:tcPr>{paras}</w:tc>"

    trs = [
        "<w:tr><w:trPr><w:cantSplit/><w:tblHeader/></w:trPr>"
        + "".join(_cac_tc(str(h), w, is_head=True) for h, w in zip(head, cols, strict=True))
        + "</w:tr>"
    ]
    trs.extend(
        "<w:tr><w:trPr><w:cantSplit/></w:trPr>"
        + "".join(_cac_tc(str(c), w, is_head=False) for c, w in zip(row, cols, strict=True))
        + "</w:tr>"
        for row in rows
    )
    return (
        f"<w:tbl><w:tblPr><w:tblW w:w='{sum(cols)}' w:type='dxa'/><w:tblLayout w:type='fixed'/>"
        f"<w:tblBorders>{tbdr}</w:tblBorders>"
        "<w:tblCellMar><w:top w:w='60' w:type='dxa'/><w:left w:w='108' w:type='dxa'/>"
        "<w:bottom w:w='60' w:type='dxa'/><w:right w:w='108' w:type='dxa'/></w:tblCellMar>"
        "</w:tblPr>" + grid + "".join(trs) + "</w:tbl>"
    )


def _blocks_xml(guide, blocks: list[dict]) -> list:
    """Document blocks (paragraphs and tables, such as the risk register and matrix) as elements to
    put into an official CAC template: both paragraphs and tables strictly match the CAC template's
    native black-bordered 仿宋 layout."""
    out = []
    for i, b in enumerate(blocks):
        if b["type"] == "paragraph":
            p = _para_like(guide, b["text"])
            if i + 1 < len(blocks) and blocks[i + 1]["type"] == "table":  # 说明句与下面的表同页
                ppr = p.find(W + "pPr")
                if ppr is None:
                    ppr = ET.Element(W + "pPr")
                    p.insert(0, ppr)
                if ppr.find(W + "keepNext") is None:  # 元素顺序按 Word 规定：pStyle 之后
                    ppr.insert(1 if ppr.find(W + "pStyle") is not None else 0, ET.Element(W + "keepNext"))
            out.append(p)
        elif b["type"] == "table":
            xml = f'<w:wrap xmlns:w="{W_NS}">{_cac_table_xml(b["head"], b["rows"], b.get("widths"))}</w:wrap>'
            out.extend(list(ET.fromstring(xml)))
    return out


def _set_font_size(rpr, half_pts: str) -> None:
    if rpr is None:
        return
    for tag in ("sz", "szCs"):
        els = rpr.findall(W + tag)
        if els:
            for el in els:
                el.set(W + "val", half_pts)
        else:
            el = ET.SubElement(rpr, W + tag)
            el.set(W + "val", half_pts)


def _cover_font_size(total_chars: int) -> str:
    """Keep cover '数据处理者名称：XXXX（盖章）' on a single centered A4 line without ever splitting '（盖章）'."""
    if total_chars <= 23:
        return "28"
    if total_chars <= 29:
        return "25"
    if total_chars <= 34:
        return "22"
    return "20"


def _format_cn_cover_date(raw_date: str) -> str:
    m = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})$", (raw_date or "").strip())
    if m:
        return f"{int(m.group(1))} 年 {int(m.group(2))} 月 {int(m.group(3))} 日"
    return (raw_date or "").strip()


def _paras_like(guide, text: str) -> list:
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    if not lines:
        return [_para_like(guide, text)]
    return [_para_like(guide, ln) for ln in lines]


def fill_narrative_form(template: bytes, start_title: str, end_title: str, answers: list[tuple[str, str]],
                        cover: dict[str, str] | None = None,
                        cover_date: str = "",
                        table_items: list[str | list[str] | tuple[str, ...]] | None = None,
                        after: dict[str, list[dict]] | None = None) -> FillResult:
    """Cut a report template out of ``template`` and write each answer as paragraphs right after the
    template paragraph that starts with its prompt; format headings & body strictly per CAC official
    typography specification (一级黑体四号加粗、二级楷体四号加粗、三级仿宋四号加粗、正文仿宋四号)."""
    zin = zipfile.ZipFile(io.BytesIO(template))
    original = zin.read("word/document.xml")
    _register_namespaces(original)
    root = ET.fromstring(original)
    body = root.find(W + "body")
    _cut_annex(body, start_title, end_title)
    _strip_template_word(body)
    _normalize_doc_fonts(body)
    result = FillResult(b"")
    children = list(body)
    list_table = body.find(W + "tbl")  # 模板自带的清单表（在插入别的表之前定位）

    seen_major = False
    for el in children:
        if el.tag != W + "p":
            continue
        txt = _text(el).strip()
        if not txt:
            continue
        if re.match(r"^[一二三四五六七八九十]+、", txt):
            seen_major = True
            _style_official_para(el, east_asia="黑体", sz="28", bold=True, keep_next=True)
        elif seen_major and re.match(r"^（[一二三四五六七八九十]+）", txt) and len(txt) <= 40:
            _style_official_para(el, east_asia="楷体", sz="28", bold=True, keep_next=True)
        elif seen_major:
            if re.match(r"^(?:\d+\.|（\d+）)", txt):
                _strip_trailing_semicolon(el)
                _style_official_para(el, east_asia="仿宋", sz="28", bold=True, keep_next=True)
            else:
                _style_official_para(el, east_asia="仿宋", sz="28", bold=False, keep_next=False)

    for prompt, text in answers:
        if not text:
            continue
        target = next((el for el in children if el.tag == W + "p" and _flat(_text(el)).startswith(_flat(prompt))), None)
        if target is None:
            result.missing_labels.append(prompt)
            continue
        _strip_trailing_semicolon(target)
        _style_official_para(target, east_asia="仿宋", sz="28", bold=True, keep_next=True)
        at = list(body).index(target) + 1
        paras = _paras_like(target, text)
        for idx, p_el in enumerate(paras):
            body.insert(at + idx, p_el)
        offset = at + len(paras)
        for n, el in enumerate(_blocks_xml(target, (after or {}).get(prompt, []))):
            body.insert(offset + n, el)
        result.filled.append(prompt)

    cover_sz = "26"
    for label, value in (cover or {}).items():
        para = next((el for el in children if el.tag == W + "p" and _flat(_text(el)).startswith(_flat(label))), None)
        if para is None or not value:
            continue
        orig_flat = _flat(_text(para))
        has_seal = "（盖章）" in orig_flat or "(盖章)" in orig_flat
        found_rpr = _run_props(para)
        base_rpr = found_rpr if found_rpr is not None else ET.Element(W + "rPr")
        clean_val = value.strip()
        seal_text = "（盖章）" if has_seal else ""
        total_len = len(label.strip()) + len(clean_val) + (4 if has_seal else 0)
        cover_sz = _cover_font_size(total_len)
        _set_font_family(base_rpr, "仿宋")
        _set_font_size(base_rpr, cover_sz)
        _set_bold(base_rpr, True)
        for child in list(para):
            if child.tag != W + "pPr":
                para.remove(child)
        r = ET.SubElement(para, W + "r")
        r.append(copy.deepcopy(base_rpr))
        t = ET.SubElement(r, W + "t")
        t.text = f"{label.strip()}{clean_val}{seal_text}"
        t.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
        _style_official_para(para, east_asia="仿宋", sz=cover_sz, bold=True, center=True)
        result.filled.append(label)

    if cover_date:
        date_para = next((el for el in children if el.tag == W + "p" and _flat(_text(el)) == "年月日"), None)
        if date_para is not None:
            _dr = _run_props(date_para)
            d_rpr = _dr if _dr is not None else ET.Element(W + "rPr")
            for child in list(date_para):
                if child.tag != W + "pPr":
                    date_para.remove(child)
            r = ET.SubElement(date_para, W + "r")
            r.append(copy.deepcopy(d_rpr))
            t = ET.SubElement(r, W + "t")
            t.text = _format_cn_cover_date(cover_date)
            t.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
            _style_official_para(date_para, east_asia="仿宋", sz=cover_sz, bold=True, center=True)
            result.filled.append("年月日")

    if table_items:
        table = list_table
        rows = table.findall(W + "tr") if table is not None else []
        if len(rows) >= 2:
            template_row = rows[1]
            _nr = _run_props(template_row)
            num_props = _nr if _nr is not None else ET.Element(W + "rPr")
            _tr = _plain(_run_props(template_row))
            txt_props = _tr if _tr is not None else ET.Element(W + "rPr")
            _set_font_family(num_props, "仿宋")
            _set_font_size(num_props, "21")
            _set_font_family(txt_props, "仿宋")
            _set_font_size(txt_props, "21")
            for row in rows[1:]:
                table.remove(row)
            for n, item in enumerate(table_items, 1):
                row = copy.deepcopy(template_row)
                cells = row.findall(W + "tc")
                for c in cells:
                    _clear(c)
                if len(cells) >= 2:
                    _put_text(cells[0], str(n), num_props)
                    if isinstance(item, (list, tuple)):
                        for ci, col_val in enumerate(item, 1):
                            if ci < len(cells) and str(col_val or "").strip():
                                _put_text(cells[ci], str(col_val).strip(), txt_props)
                    else:
                        _put_text(cells[1], str(item), txt_props)
                table.append(row)
            result.filled.append("数据项清单")
    _reset_sect_page_start(body)
    xml = _serialise(root, original)
    doc_title = "个人信息保护影响评估报告" if start_title == "附件5" else "数据出境风险自评估报告"
    result.data = _write_clean_zip(zin, xml, doc_title)
    return result


def _risk_part(v: dict[str, str], raw: str, blank: str, *keys: str) -> tuple[str, list[dict]]:
    """The answer under the template's risk heading and the risk register and matrix after it: the
    enterprise's other answers for that heading as they are, then the risks as rows (the tables), or
    as text when they were not given in rows."""
    from app.analysis import risk_items

    rows = risk_items.parse(raw)
    if not rows:
        text = "\n".join(x for x in (v.get(k, "") for k in keys) if x)
        return text or blank, []
    lead = "\n".join(x for x in (v.get(k, "") for k in keys if k != "risk_rating") if x)
    head = "主要风险、风险等级与应对措施见下表。"
    if not lead:
        return head, risk_items.blocks(raw, blank)
    return lead, [{"type": "paragraph", "text": head}, *risk_items.blocks(raw, blank)]


def _strip_cites(text: str) -> str:
    return re.sub(r"(?:\[\d+\])+", "", text or "").strip()


def _ensure_period(text: str, blank: str) -> str:
    t = (text or "").strip()
    if not t or t == blank:
        return t
    lines = [ln.strip() for ln in t.splitlines() if ln.strip()]
    fixed = [ln if ln[-1] in "。！？；：”》）)" else ln + "。" for ln in lines]
    return "\n".join(fixed)


def _narrative_parts(narrative_blocks: list[dict] | None) -> tuple[list[list[str]], list[list[str]]]:
    """Extract per-chapter clean paragraph lists and any 5-col field_items rows from DocumentNarrative blocks."""
    if not narrative_blocks:
        return [], []
    chapters: list[list[str]] = []
    field_rows: list[list[str]] = []
    for b in narrative_blocks:
        btype = b.get("type")
        if btype == "heading":
            txt = str(b.get("text") or "").strip()
            if txt == "参考条文":
                break
            chapters.append([])
        elif btype == "paragraph" and chapters:
            raw = str(b.get("text") or "").strip()
            if (
                raw.startswith("数据处理流程图（")
                or raw.startswith("主要风险评估与应对清单：")
                or raw.startswith("风险等级判定与分布表")
                or raw.startswith("防控措施适当性与有效性论证")
                or raw.startswith("剩余风险可接受性结论：")
                or raw.startswith("风险整改前后变化说明：")
                or (" → " in raw and not raw.endswith("。"))
                or (raw.endswith("：") and len(raw) <= 68)
            ):
                continue
            for ln in raw.splitlines():
                cleaned = _strip_cites(ln.strip())
                if not cleaned:
                    continue
                if re.match(r"^[一二三四五六七八九十]+、", cleaned) and len(cleaned) <= 45 and "。" not in cleaned:
                    continue
                cleaned = re.sub(r"^[一二三四五六七八九十]+、[^。；：:\n]{2,38}[。；：:]\s*", "", cleaned).strip()
                if cleaned:
                    chapters[-1].append(cleaned)
        elif btype == "table":
            head = b.get("head") or []
            if len(head) >= 6 and head[:2] == ["序号", "数据项名称"]:
                for r in b.get("rows") or []:
                    if len(r) >= 6 and str(r[1] or "").strip():
                        field_rows.append([_strip_cites(str(c or "")) for c in r[1:6]])
    return chapters, field_rows


def _merge_prose(fact_text: str, nar_paras: list[str], blank: str) -> str:
    nar = "\n".join(p for p in nar_paras if p)
    if nar:
        return _ensure_period(nar, blank)
    return _ensure_period(fact_text, blank)


def cn_self_assessment(
    values: dict[str, str],
    blank: str = "",
    narrative_blocks: list[dict] | None = None,
    extra_after: dict[str, list[dict]] | None = None,
) -> FillResult:
    """附件 4 数据出境风险自评估报告：在官方模板上逐节填写（模板自带的标题和说明原样保留）。
    ``blank``：没有回答的节写什么（初稿写“【待补充】”，成稿不允许缺）；
    ``narrative_blocks``：已生成的合规自证论述块，按章节拆解并注入官方模板各节与 6 列数据项清单表。"""
    from datetime import date as _date

    v = {k: ("" if str(x or "").strip().startswith("暂不确定") else str(x or "").strip()) for k, x in values.items()}

    def a(key: str, *more: str) -> str:
        text = "\n".join(x for x in (v.get(key, ""), *(v.get(m, "") for m in more)) if x)
        return _ensure_period(text, blank) or blank

    chapters, field_rows = _narrative_parts(narrative_blocks)
    c0 = chapters[0] if len(chapters) > 0 else []
    c1 = chapters[1] if len(chapters) > 1 else []
    c2 = chapters[2] if len(chapters) > 2 else []
    c3 = chapters[3] if len(chapters) > 3 else []
    c4 = chapters[4] if len(chapters) > 4 else []

    risk_text, risk_tables = _risk_part(v, v.get("risk_rating", ""), blank, "risk_findings", "risk_rating")
    after_has_head = bool(risk_tables and risk_tables[0].get("type") == "paragraph" and "见下表" in str(risk_tables[0].get("text", "")))
    if c4:
        risk_prose = c4[:-1] if len(c4) > 1 else c4
        if risk_tables and risk_prose:
            parts = list(risk_prose)
            if not after_has_head:
                parts.append("主要风险、风险等级与应对措施见下表。")
            risk_text = _ensure_period("\n".join(parts), blank)
        else:
            risk_text = _merge_prose(risk_text, risk_prose, blank)
    concl_prose = [c4[-1]] if len(c4) > 1 else []

    asset_base = a("company_profile") if v.get("company_profile") else blank
    onward_val = v.get("onward_provision", "")
    if onward_val in ("无", "否", "不涉及"):
        onward_full = "本次汽车数据出境后仅在境外接收方受控云端环境内封闭处理，不向任何境外其他组织或个人再转移；如后续确因业务变更需向境外第三方提供的，须事先重走法定评估与单独同意程序。"
    elif onward_val:
        onward_full = _ensure_period(onward_val, blank)
    else:
        onward_full = blank

    has_contract_facts = bool(v.get("legal_doc_terms"))
    purp_clean = v.get("purpose", "").rstrip("。；; ")
    dtypes_clean = v.get("data_types", "").rstrip("。；; ")
    ret_clean = v.get("retention_and_blackbox", "").rstrip("。；; ")
    c5_1 = (
        f"双方在跨境数据传输协议及标准合同附录中明确约定出境目的为：{purp_clean}；出境数据范围严格限定于：{dtypes_clean}；传输与处理方式限定为车端脱敏预处理、跨境加密专用通道或双向证书认证通道传输及境外受控云端封闭运维处理，严禁超范围采集或用于商业画像。"
        if has_contract_facts and purp_clean and dtypes_clean
        else ""
    )
    c5_2 = (
        f"法律文件明确约定境外保存地点、保存期限及到期删除机制：{ret_clean}。达到约定保存期限、完成约定目的或合同终止后，境外接收方须立即停止处理并执行安全彻底删除（含备份数据），同时向数据处理者出具书面删除证明。"
        if has_contract_facts and ret_clean
        else ""
    )
    c5_3 = (
        "法律文件设置严格的再转移限制与连带合同约束条款：未经数据处理者事先书面同意、依法完成法定前置评估程序并取得个人信息主体单独同意，境外接收方不得将出境数据再转移给任何境外第三方组织或个人。"
        if has_contract_facts
        else ""
    )
    c5_4 = (
        "法律文件约定，若境外接收方实际控制权、经营范围发生实质性变化，或接收方所在国家、地区数据安全政策法规与网络安全环境发生变化导致难以履行约定保护义务的，境外接收方须立即书面通知数据处理者，数据处理者有权立即暂停数据传输、要求采取补充加密控制或单方解除合同并责令销毁存量数据。"
        if has_contract_facts
        else ""
    )
    c5_5 = (
        f"法律文件明确约定违约补救、全额追偿与争议管辖机制：任一方违约侵害个人合法权益或造成损失的，依法承担民事赔偿及连带追偿责任；{v.get('governing_law') or '合同适用中华人民共和国法律并接受境内有管辖权的人民法院或约定仲裁机构管辖。'}"
        if has_contract_facts
        else ""
    )
    c5_6 = (
        "法律文件专门设立安全事件应急协同与个人信息主体跨境维权畅通机制：一旦出境数据发生篡改、破坏、泄露、丢失或被非法获取、非法利用风险，境外接收方须立即启动隔离封堵预案并在约定紧急时限内书面通知数据处理者，协同采取补救措施并依法配合向网信等主管部门报告及告知受影响个人；同时，数据处理者在车载终端与移动端公布境内统一隐私权利受理渠道，境外接收方通过合同绑定协同响应接口，保障个人依法便捷行使查阅、复制、更正、补充、删除及撤回同意等权利。"
        if has_contract_facts
        else ""
    )

    c0_work = [p for p in c0 if "工作组" in p or p.startswith("本次自评估")] or c0[:1]
    c0_prof = [p for p in c0 if p not in c0_work] or c0[1:2]
    cover_dt = v.get("self_assessed_on") or (_date.today().isoformat() if not blank else "")
    answers = [
        ("简要概述自评估工作开展情况", _merge_prose(a("assessment_work"), c0_work[:1], blank)),
        ("1.基本情况简介", _merge_prose(a("company_profile"), c0_prof[:1], blank)),
        ("2.组织架构和数据安全管理机构信息", a("security_org")),
        ("3.整体业务与数据资产情况", _merge_prose(asset_base, c0_prof[1:] or c0[2:], blank)),
        ("1.数据出境涉及业务", _merge_prose(a("scenario_summary") if v.get("scenario_summary") else a("scenario"), c1[:1], blank)),
        ("2.数据出境及境外接收方处理数据的目的", _merge_prose(a("purpose"), c1[1:], blank)),
        ("4.拟出境数据在境内存储的系统平台", a("storage_chain")),
        ("5.数据出境后向境外其他接收方提供的情况", onward_full),
        ("6.涉及个人信息的，按照自然人", a("data_volume")),
        ("1.数据安全管理能力", _merge_prose(a("security_mgmt"), c2[:1], blank)),
        ("（涉及个人信息出境的，需提供履行", a("consent_and_notice") if v.get("consent_and_notice") else (a("lawful_basis") if v.get("lawful_basis") else blank)),
        ("2.数据安全技术能力", _merge_prose(a("security_tech"), c2[1:2], blank)),
        ("3.数据安全保障措施有效性证明", _merge_prose(a("effectiveness_proof"), c2[2:], blank)),
        ("4.遵守数据和网络安全相关法律法规的情况", a("law_compliance")),
        ("1.境外接收方基本情况", _merge_prose(a("recipient"), c3[:1], blank)),
        ("2.境外接收方处理数据的用途", a("purpose")),
        ("3.境外接收方履行责任义务的管理和技术措施", _merge_prose(a("recipient_measures"), c3[1:2], blank)),
        ("1.数据出境的目的、方式和数据范围", c5_1),
        ("2.数据在境外保存地点、期限", c5_2),
        ("3.对于境外接收方将出境数据再转移给其他组织", c5_3),
        ("4.境外接收方在实际控制权或者经营范围发生实质性变化", c5_4),
        ("5.违反法律文件约定的数据安全保护义务的补救措施", c5_5),
        ("6.出境数据遭到篡改、破坏", _merge_prose(c5_6 or a("legal_doc_terms"), c3[2:], blank)),
        ("（六）数据处理者认为需要说明的其他情况", "本次申报所涉车端预处理脱敏策略、跨境加密网关配置、访问审计日志及标准合同文本均已归档留存，随时备供网信及行业主管部门现场核查。" if not blank and has_contract_facts else ""),
        ("对照《数据出境安全评估办法》第五条规定事项", risk_text),
        ("综合风险自评估情况和相应整改情况", _merge_prose(a("risk_conclusion"), concl_prose, blank)),
    ]
    dtypes_no_paren = re.sub(r"[（(]不含[^）)]*[）)]", "", v.get("data_types", ""))
    items = [x.strip("。 ") for x in re.split(r"[、，,；;]", dtypes_no_paren) if x.strip("。 ")]
    table_items: list[str | list[str]] | None = list(field_rows) if field_rows else (items or None)
    merged_after = {"对照《数据出境安全评估办法》第五条规定事项": risk_tables}
    if extra_after:
        for k, blks in extra_after.items():
            if blks:
                merged_after[k] = [*merged_after.get(k, []), *blks]
    return fill_narrative_form(CN_GUIDE.read_bytes(), "附件4", "附件5", answers,
                               cover={"数据处理者名称：": v.get("company_name", "")},
                               cover_date=cover_dt,
                               table_items=table_items,
                               after=merged_after)


def cn_pia(
    values: dict[str, str],
    blank: str = "",
    narrative_blocks: list[dict] | None = None,
    extra_after: dict[str, list[dict]] | None = None,
) -> FillResult:
    """标准合同备案指南附件 5 个人信息保护影响评估报告（出境版）：在官方模板上逐节填写，模板的标题、
    说明原样保留。DPIA 自己问到的信息之外，同一项目里为其他文件回答过的（处理者情况、境外接收方等）一并用上。"""
    from datetime import date as _date

    v = {k: ("" if str(x or "").strip().startswith("暂不确定") else str(x or "").strip()) for k, x in values.items()}

    def a(*keys: str) -> str:
        parts: list[str] = []
        for k in keys:
            x = v.get(k, "")
            if k == "lawful_basis" and x:
                x = "处理的合法性基础：" + x
            if x and not any(x in y for y in parts):  # 同一事实别的栏目已写过（如人群规模与人数），不重复
                parts.append(x)
        return _ensure_period("\n".join(parts), blank) or blank

    chapters, _ = _narrative_parts(narrative_blocks)
    d0 = chapters[0] if len(chapters) > 0 else []
    d1 = chapters[1] if len(chapters) > 1 else []
    d2 = chapters[2] if len(chapters) > 2 else []
    d3 = chapters[3] if len(chapters) > 3 else []
    d4 = chapters[4] if len(chapters) > 4 else []
    d5 = chapters[5] if len(chapters) > 5 else []
    d6 = chapters[6] if len(chapters) > 6 else []

    risk_text, risk_tables = _risk_part(v, v.get("risk_rating", ""), blank, "risk_rating", "risk_findings")
    after_has_head = bool(risk_tables and risk_tables[0].get("type") == "paragraph" and "见下表" in str(risk_tables[0].get("text", "")))
    if d4:
        if risk_tables:
            parts = list(d4)
            if not after_has_head:
                parts.append("主要风险、风险等级与应对措施见下表。")
            risk_text = _ensure_period("\n".join(parts), blank)
        else:
            risk_text = _merge_prose(risk_text, d4, blank)
    recipient = v.get("recipient") or (f"境外接收方位于{v['recipient_country']}。" if v.get("recipient_country") else "")
    cover_dt = v.get("self_assessed_on") or (_date.today().isoformat() if not blank else "")
    answers = [
        ("1.基本情况简介", _merge_prose(a("company_profile") if v.get("company_profile") else (v.get("company_name") or blank), d0[:1], blank)),
        ("2.整体业务与处理个人信息情况", _merge_prose(a("scenario_summary", "scenario"), d0[1:], blank)),
        ("（1）个人信息出境涉及业务", _merge_prose(a("scenario", "data_flow"), d1[:1], blank)),
        ("（2）个人信息处理者和境外接收方处理个人信息的目的", _merge_prose(a("purpose", "lawful_basis"), [*d2, *d3], blank)),
        ("（3）出境个人信息的规模、范围、种类、敏感程度", a("exterior_video_and_gnss", "data_subjects", "data_volume")),
        ("（4）拟出境个人信息在境内存储的系统平台", _merge_prose(a("storage_chain", "retention_and_blackbox"), d1[1:], blank)),
        ("（5）个人信息出境后向境外其他接收方提供的情况", a("onward_provision")),
        ("4.遵守个人信息保护相关法律法规的情况", _merge_prose(a("consent_and_notice", "law_compliance"), d5[:1], blank)),
        ("1.境外接收方基本情况", _ensure_period(recipient, blank) or blank),
        ("2.境外接收方处理个人信息的用途", a("purpose")),
        ("3.境外接收方履行责任义务的管理和技术措施", a("recipient_measures") if v.get("recipient_measures") else a("risk_measures")),
        ("（三）个人信息处理者认为需要说明的其他情况", _merge_prose(a("dpo_opinion"), d5[1:], blank)),
        ("对照《个人信息出境标准合同办法》第五条规定事项", risk_text),
        ("综合影响评估情况和相应整改情况", _merge_prose(a("risk_conclusion") if v.get("risk_conclusion") else a("risk_measures"), d6, blank)),
    ]
    merged_after = {"对照《个人信息出境标准合同办法》第五条规定事项": risk_tables}
    if extra_after:
        for k, blks in extra_after.items():
            if blks:
                merged_after[k] = [*merged_after.get(k, []), *blks]
    return fill_narrative_form(CN_SC_GUIDE.read_bytes(), "附件5", "（本模板到此为止）", answers,
                               cover={"个人信息处理者名称：": v.get("company_name", "")},
                               cover_date=cover_dt,
                               after=merged_after)


def _split_retention_and_location(raw: str, recipient_country: str, blank: str) -> tuple[str, str]:
    txt = (raw or "").strip()
    if not txt:
        return blank, blank
    loc_m = re.search(
        r"(?:存储于|存放于|存放在|保存于|位于|在)([^，,；;。：（(]{2,42}?(?:数据中心|云端[^，,；;。：（(]{0,14}|云平台|节点|服务器|机房))",
        txt,
    )
    clauses = [c.strip() for c in re.split(r"[；;。]+", txt) if c.strip()]
    pure_loc = [
        c for c in clauses
        if re.search(r"(存储地点|保存地点|存放地点|存放位置|位于|数据中心|节点|机房|服务器)", c)
        and not re.search(r"(\d+\s*(?:天|日|个月|年)|期满|届满|到期)", c)
    ]
    dur_clauses = [c for c in clauses if c not in pure_loc]
    if pure_loc and dur_clauses:
        return _ensure_period("；".join(dur_clauses), blank), _ensure_period("；".join(pure_loc), blank)
    if loc_m:
        loc_val = loc_m.group(1).strip()
        if recipient_country and recipient_country not in loc_val:
            loc_val = f"{recipient_country}（{loc_val}）"
        return _ensure_period(txt, blank), loc_val
    fallback_loc = f"{recipient_country}境内受控云端存储节点" if recipient_country else txt
    return _ensure_period(txt, blank), fallback_loc


def cn_standard_contract(values: dict[str, str], blank: str = "") -> FillResult:
    """国家网信办《个人信息出境标准合同》正式公文排版 Word 原件（含正文九条、双栏签署区、附录一及附录二标准边框表）。"""
    from xml.sax.saxutils import escape as _esc

    from app.analysis import official_cn_standard_contract as _cn_sc
    from app.analysis.document_narrative import polish_legal_prose
    from app.analysis.report_exporter import _clean_styles_for_official_contract

    v = {k: ("" if str(x or "").strip().startswith("暂不确定") else str(x or "").strip()) for k, x in values.items()}
    fallback = blank or "________________"

    def val(k: str, default: str = "") -> str:
        raw_v = polish_legal_prose(v.get(k, "").strip())
        return raw_v or default or fallback

    rcp_parsed = _parse_recipient_info(v.get("recipient", ""), v.get("recipient_contact", ""))
    rcp_name = polish_legal_prose(rcp_parsed.get("name") or v.get("recipient", "").strip()) or fallback
    rcp_addr = polish_legal_prose(rcp_parsed.get("address") or v.get("recipient_country", "").strip()) or fallback
    rcp_contact = polish_legal_prose(v.get("recipient_contact", "").strip() or rcp_parsed.get("contact", "")) or fallback

    def _p_xml(
        text: str,
        *,
        font: str = "仿宋",
        sz: int = 28,
        bold: bool = False,
        center: bool = False,
        indent: bool = True,
        keep_next: bool = False,
        before: int = 0,
        after: int = 60,
        line: int = 520,
    ) -> str:
        b_xml = "<w:b/><w:bCs/>" if bold else ""
        kn_xml = "<w:keepNext/>" if keep_next else ""
        jc_val = "center" if center else "both"
        ind_xml = (
            "<w:ind w:firstLine='560' w:firstLineChars='200'/>"
            if (indent and not center)
            else "<w:ind w:firstLine='0' w:firstLineChars='0'/>"
        )
        rfonts = f"<w:rFonts w:ascii='Times New Roman' w:hAnsi='Times New Roman' w:eastAsia='{font}' w:cs='Times New Roman'/>"
        safe = _esc(text)
        return (
            f"<w:p><w:pPr>{kn_xml}<w:spacing w:before='{before}' w:after='{after}' w:line='{line}' w:lineRule='exact'/>"
            f"{ind_xml}<w:jc w:val='{jc_val}'/></w:pPr>"
            f"<w:r><w:rPr>{rfonts}{b_xml}<w:color w:val='000000'/><w:sz w:val='{sz}'/><w:szCs w:val='{sz}'/></w:rPr>"
            f"<w:t xml:space='preserve'>{safe}</w:t></w:r></w:p>"
        )

    def _cell_xml(lines: list[str], width: int, *, bold: bool = False, shade: str = "", borders: bool = True) -> str:
        shd = f"<w:shd w:val='clear' w:color='auto' w:fill='{shade}'/>" if shade else ""
        cell_sz = 24 if borders else 21
        paras = "".join(
            _p_xml(ln, font="仿宋", sz=cell_sz, bold=bold, center=False, indent=False, after=40, line=400)
            for ln in (lines or [""])
        )
        bdr = ""
        if not borders:
            bdr = (
                "<w:tcBorders>"
                + "".join(f"<w:{s} w:val='none' w:sz='0' w:space='0' w:color='auto'/>" for s in ("top", "bottom", "left", "right"))
                + "</w:tcBorders>"
            )
        return f"<w:tc><w:tcPr><w:tcW w:w='{width}' w:type='dxa'/>{bdr}{shd}</w:tcPr>{paras}</w:tc>"

    def _table_xml(rows_spec: list[tuple[str, str]], col_widths: tuple[int, int] = (2800, 6226), *, bordered: bool = True, header_row: tuple[str, str] | None = None) -> str:
        if bordered:
            b_single = "<w:{0} w:val='single' w:sz='4' w:space='0' w:color='000000'/>"
            tbdr = "".join(b_single.format(s) for s in ("top", "bottom", "left", "right", "insideH", "insideV"))
        else:
            b_none = "<w:{0} w:val='none' w:sz='0' w:space='0' w:color='auto'/>"
            tbdr = "".join(b_none.format(s) for s in ("top", "bottom", "left", "right", "insideH", "insideV"))
        grid = f"<w:tblGrid><w:gridCol w:w='{col_widths[0]}'/><w:gridCol w:w='{col_widths[1]}'/></w:tblGrid>"
        trs: list[str] = []
        if header_row is not None:
            trs.append(
                "<w:tr><w:trPr><w:cantSplit/><w:tblHeader/></w:trPr>"
                + _cell_xml([header_row[0]], col_widths[0], bold=True, shade="", borders=bordered)
                + _cell_xml([header_row[1]], col_widths[1], bold=True, shade="", borders=bordered)
                + "</w:tr>"
            )
        for c1, c2 in rows_spec:
            trs.append(
                "<w:tr><w:trPr><w:cantSplit/></w:trPr>"
                + _cell_xml(c1.split("\n"), col_widths[0], bold=bordered, borders=bordered)
                + _cell_xml(c2.split("\n"), col_widths[1], bold=False, borders=bordered)
                + "</w:tr>"
            )
        return (
            f"<w:tbl><w:tblPr><w:tblW w:w='{sum(col_widths)}' w:type='dxa'/><w:tblLayout w:type='fixed'/>"
            f"<w:tblBorders>{tbdr}</w:tblBorders>"
            "<w:tblCellMar><w:top w:w='80' w:type='dxa'/><w:left w:w='120' w:type='dxa'/>"
            "<w:bottom w:w='80' w:type='dxa'/><w:right w:w='120' w:type='dxa'/></w:tblCellMar></w:tblPr>"
            + grid
            + "".join(trs)
            + "</w:tbl>"
        )

    parts: list[str] = [
        _p_xml("个人信息出境标准合同", font="华文中宋", sz=44, bold=True, center=True, indent=False, before=120, after=120, line=600),
        _p_xml("国家互联网信息办公室 制定", font="楷体", sz=28, bold=False, center=True, indent=False, after=200, line=480),
        _p_xml(
            "为了确保境外接收方处理个人信息的活动达到中华人民共和国相关法律法规规定的个人信息保护标准，明确个人信息处理者和境外接收方个人信息保护的权利和义务，经双方协商一致，订立本合同。"
        ),
        _table_xml(
            [
                ("个人信息处理者", val("company_name")),
                ("地址", val("registered_place", v.get("office_place", ""))),
                ("联系人及联系方式", val("security_officer", v.get("agent_info", ""))),
                ("境外接收方", rcp_name),
                ("地址及所在国家或者地区", f"{val('recipient_country')} · {rcp_addr}" if rcp_addr != val("recipient_country") else val("recipient_country")),
                ("负责人及联系方式", rcp_contact),
            ],
            col_widths=(2600, 6426),
            bordered=True,
        ),
        _p_xml(
            "个人信息处理者与境外接收方依据本合同约定开展个人信息出境活动，与此活动相关的商业行为，双方已结合主业务协议及服务订单予以确认。",
            before=120,
        ),
        _p_xml(
            "本合同正文根据《个人信息出境标准合同办法》的要求拟定，在不与本合同正文内容相冲突的前提下，双方如有其他约定可在附录二中详述，附录构成本合同的组成部分。"
        ),
    ]

    def _fill_line(raw_line: str) -> str:
        out_line = raw_line
        repl = {
            "{{company_name}}": val("company_name"),
            "{{registered_place}}": val("registered_place"),
            "{{security_officer}}": val("security_officer"),
            "{{recipient}}": rcp_name,
            "{{recipient_country}}": val("recipient_country"),
            "{{recipient_contact}}": rcp_contact,
            "{{risk_measures}}": val("risk_measures"),
        }
        for k_ph, v_ph in repl.items():
            out_line = out_line.replace(k_ph, v_ph)
        return polish_legal_prose(out_line)

    gov_law = v.get("governing_law", "")
    is_arb = "仲裁" in gov_law
    is_lit = "诉讼" in gov_law or "法院" in gov_law
    disp_choice = "1" if is_arb and not is_lit else "2"
    reg_addr_clean = (v.get("registered_place", "") or v.get("office_place", "")).strip()
    notice_addr_phrase = (
        f"发往本合同首部载明的双方地址（境内处理者地址：{reg_addr_clean}）"
        if reg_addr_clean and not reg_addr_clean.startswith("【")
        else "发往本合同首部载明的双方地址"
    )
    arb_city_m = re.search(
        r"([\u4e00-\u9fa5]{2,8}市|北京|上海|深圳|广州|杭州|重庆|天津|武汉|新加坡|香港)",
        gov_law,
    )
    arb_venue_phrase = (
        f"在{arb_city_m.group(1)}进行仲裁"
        if (is_arb and not is_lit and arb_city_m)
        else "在选定仲裁机构所在地进行仲裁"
    )

    for sec in _cn_sc.SECTIONS[1:10]:
        parts.append(_p_xml(sec["heading"], font="黑体", sz=28, bold=True, center=False, indent=False, keep_next=True, before=180, after=80))
        for raw_p in sec["paras"]:
            if raw_p.startswith(("本合同在（", "个人信息处理者（盖章）", "境外接收方（盖章）")):
                continue
            filled_p = _fill_line(raw_p)
            filled_p = filled_p.replace("发往（具体地址）", notice_addr_phrase)
            if notice_addr_phrase != "发往本合同首部载明的双方地址":
                filled_p = filled_p.replace("发往本合同首部载明的双方地址", notice_addr_phrase)
            filled_p = re.sub(r"在邮戳日期后的\s*天应当视为收讫", "在邮戳日期后的 5 天应当视为收讫", filled_p)
            filled_p = re.sub(r"在发出以后的\s*个工作日应当视为收讫", "在发出以后的 3 个工作日应当视为收讫", filled_p)
            filled_p = re.sub(r"在\s*（仲裁地点）\s*进行仲裁", arb_venue_phrase, filled_p)
            if arb_venue_phrase != "在选定仲裁机构所在地进行仲裁":
                filled_p = filled_p.replace("在选定仲裁机构所在地进行仲裁", arb_venue_phrase)
            filled_p = filled_p.replace("本合同正本一式____份，双方各执____份", "本合同正本一式 贰 份，双方各执 壹 份")
            if "可以采取下列第 种方式加以解决" in filled_p:
                filled_p = filled_p.replace("下列第 种方式", f"下列第 {disp_choice} 种方式")
            if is_arb and "仲裁委员会" in filled_p:
                for arb_name in ("中国国际经济贸易仲裁委员会", "中国海事仲裁委员会", "北京仲裁委员会", "上海国际仲裁中心"):
                    if arb_name in gov_law:
                        filled_p = filled_p.replace(f"□{arb_name}", f"☑{arb_name}")
            parts.append(_p_xml(filled_p))

    sign_place = (
        val("registered_place", "中华人民共和国境内").split("市")[0] + "市"
        if "市" in v.get("registered_place", "")
        else val("registered_place", "中华人民共和国境内")
    )
    parts.append(_p_xml(f"本合同在 {sign_place} 签订。", before=120, after=120))
    parts.append(
        _table_xml(
            [
                (
                    f"个人信息处理者：{val('company_name')}（盖章）\n法定代表人或授权代表（签字）：________\n签署日期：      年    月    日",
                    f"境外接收方：{rcp_name}（盖章）\n授权代表（签字）：________\n签署日期：      年    月    日",
                )
            ],
            col_widths=(4513, 4513),
            bordered=False,
        )
    )

    ret_period, ret_loc = _split_retention_and_location(
        polish_legal_prose(v.get("retention_and_blackbox", "")), v.get("recipient_country", ""), fallback
    )
    onward_raw = v.get("onward_provision", "").strip()
    onward_txt = (
        "不涉及（未经个人信息处理者事先书面同意并依法履行单独同意及前置评估备案程序，不向任何境外第三方提供）"
        if onward_raw in ("无", "否", "不涉及")
        else (polish_legal_prose(onward_raw) or fallback)
    )
    chain_txt = val("storage_chain", "通过双向证书认证的跨境加密专用通道传输")

    parts.append("<w:p><w:r><w:br w:type='page'/></w:r></w:p>")
    parts.append(_p_xml("附录一  个人信息出境说明", font="黑体", sz=30, bold=True, center=True, indent=False, keep_next=True, before=80, after=120))
    parts.append(_p_xml("根据本合同向境外提供个人信息的详情约定如下：", indent=True, keep_next=True))
    parts.append(
        _table_xml(
            [
                ("（一）处理目的", val("purpose")),
                ("（二）处理方式", f"车端脱敏与去标识化预处理、跨境加密通道传输、境外受控云端存储分析及到期安全彻底删除（{chain_txt}）"),
                ("（三）出境个人信息的规模", val("data_volume")),
                ("（四）出境个人信息种类", val("data_types")),
                ("（五）出境敏感个人信息种类", val("sensitive_pi_kinds", "不涉及" if re.match(r"^(否|无|不含)", v.get("sensitive_pi", "")) else fallback)),
                ("（六）境外接收方只向以下境外第三方提供个人信息", onward_txt),
                ("（七）传输方式", chain_txt),
                ("（八）出境后保存期限", ret_period),
                ("（九）出境后保存地点", ret_loc),
                ("（十）其他事项（适用法律与争议管辖补充说明）", val("governing_law", "按本合同第九条约定执行")),
            ],
            col_widths=(3100, 5926),
            bordered=True,
            header_row=("约定事项", "具体内容"),
        )
    )

    parts.append(_p_xml("附录二  双方约定的其他条款", font="黑体", sz=30, bold=True, center=True, indent=False, keep_next=True, before=240, after=120))
    risk_m = val("risk_measures").rstrip("。；;，, ")
    parts.append(
        _table_xml(
            [
                (
                    "补充安全与履约约定",
                    f"在不与本合同正文相冲突的前提下，双方确认严格落实以下技术与管理保障措施：{risk_m}。除上述约定外，无其他与本合同正文相抵触之附加条款。",
                )
            ],
            col_widths=(2600, 6426),
            bordered=True,
        )
    )

    raw_docx = _clean_styles_for_official_contract(parts, "个人信息出境标准合同")
    zin = zipfile.ZipFile(io.BytesIO(raw_docx))
    doc_xml = zin.read("word/document.xml")
    _register_namespaces(doc_xml)
    root = ET.fromstring(doc_xml)
    _reset_sect_page_start(root.find(W + "body"))
    final_bytes = _write_clean_zip(zin, _serialise(root, doc_xml), "个人信息出境标准合同")
    return FillResult(
        data=final_bytes,
        filled=["company_name", "recipient", "purpose", "data_types", "data_volume", "retention_and_blackbox", "附录一", "附录二"],
    )


def docx_preview_blocks(data: bytes) -> list[dict]:
    """Convert a filled official .docx into BlocksView blocks (title, heading, paragraph, table) for online preview."""
    zin = zipfile.ZipFile(io.BytesIO(data))
    root = ET.fromstring(zin.read("word/document.xml"))
    body = root.find(W + "body")
    if body is None:
        return []
    blocks: list[dict] = []
    seen_title = False
    seen_major = False
    major_re = re.compile(r"^(?:[一二三四五六七八九十]+、|第[一二三四五六七八九十百]+条\s|附录[一二三四五六七八九十])")
    sub_re = re.compile(r"^(?:（[一二三四五六七八九十]+）|\d+\.)")
    def _preview_text(elem) -> str:
        parts: list[str] = []
        for node in elem.iter():
            if node.tag == W + "t" and node.text:
                parts.append(node.text)
            elif node.tag == W + "sym":
                ch = (node.get(W + "char") or "").strip().upper()
                parts.append("☑" if ch in ("F0FE", "00FE", "F052", "0052") else "☐")
        return "".join(parts)

    for el in body:
        if el.tag == W + "p":
            txt = _preview_text(el).strip()
            if not txt:
                continue
            if not seen_title:
                blocks.append({"type": "title", "text": txt})
                seen_title = True
            elif major_re.match(txt) and len(txt) <= 48:
                seen_major = True
                blocks.append({"type": "heading", "text": txt})
            elif seen_major and sub_re.match(txt) and len(txt) <= 60:
                blocks.append({"type": "subheading", "text": txt})
            else:
                blocks.append({"type": "paragraph", "text": txt})
        elif el.tag == W + "tbl":
            rows_xml = el.findall(W + "tr")
            raw_rows: list[list[tuple[str, int, str]]] = []
            for tr in rows_xml:
                row_items: list[tuple[str, int, str]] = []
                for tc in tr.findall(W + "tc"):
                    tc_pr = tc.find(W + "tcPr")
                    gs_el = tc_pr.find(W + "gridSpan") if tc_pr is not None else None
                    try:
                        cspan = max(1, int(gs_el.get(W + "val", "1"))) if gs_el is not None else 1
                    except ValueError:
                        cspan = 1
                    vm_el = tc_pr.find(W + "vMerge") if tc_pr is not None else None
                    if vm_el is not None:
                        vm_val = (vm_el.get(W + "val") or "").strip().lower()
                        vm = "start" if vm_val == "restart" else "cont"
                    else:
                        vm = "none"
                    row_items.append((_preview_text(tc).strip(), cspan, vm))
                if any(t for t, _, _ in row_items) or any(vm == "cont" for _, _, vm in row_items):
                    raw_rows.append(row_items)
            if raw_rows:
                col_n = max(sum(cs for _, cs, _ in r) for r in raw_rows)
                grid_cells: list[list[str]] = []
                grid_spans: list[list[list[int]]] = []
                grid_vm: list[list[str]] = []
                for r in raw_rows:
                    rc = [""] * col_n
                    rs = [[1, 1] for _ in range(col_n)]
                    rv = ["none"] * col_n
                    pos = 0
                    last_start = 0
                    for txt, cs, vm in r:
                        if pos >= col_n:
                            break
                        eff_cs = min(cs, col_n - pos)
                        last_start = pos
                        rc[pos] = txt
                        rs[pos] = [1, eff_cs]
                        rv[pos] = vm
                        for k in range(1, eff_cs):
                            rs[pos + k] = [0, 0]
                            rv[pos + k] = "covered"
                        pos += eff_cs
                    if pos < col_n and r:
                        extra = col_n - pos
                        rs[last_start][1] += extra
                        for j in range(pos, col_n):
                            rs[j] = [0, 0]
                            rv[j] = "covered"
                    grid_cells.append(rc)
                    grid_spans.append(rs)
                    grid_vm.append(rv)
                n_rows = len(grid_cells)
                for c_idx in range(col_n):
                    r_idx = 0
                    while r_idx < n_rows:
                        if (
                            grid_vm[r_idx][c_idx] in ("start", "none")
                            and grid_spans[r_idx][c_idx][0] > 0
                            and r_idx + 1 < n_rows
                            and grid_vm[r_idx + 1][c_idx] == "cont"
                            and grid_spans[r_idx + 1][c_idx][1] == grid_spans[r_idx][c_idx][1]
                        ):
                            r2 = r_idx + 1
                            while (
                                r2 < n_rows
                                and grid_vm[r2][c_idx] == "cont"
                                and grid_spans[r2][c_idx][1] == grid_spans[r_idx][c_idx][1]
                            ):
                                grid_spans[r_idx][c_idx][0] += 1
                                for k in range(grid_spans[r2][c_idx][1]):
                                    if c_idx + k < col_n:
                                        grid_spans[r2][c_idx + k] = [0, 0]
                                r2 += 1
                            r_idx = r2
                        else:
                            r_idx += 1
                is_header_table = (
                    n_rows > 1
                    and col_n <= 8
                    and all(s == [1, 1] for s in grid_spans[0])
                    and all(0 < len(c) <= 32 for c in grid_cells[0])
                    and len(set(grid_cells[0])) == col_n
                )
                if is_header_table:
                    body_spans = grid_spans[1:]
                    tbl_block: dict = {"type": "table", "head": grid_cells[0], "rows": grid_cells[1:]}
                    if any(s != [1, 1] for row_s in body_spans for s in row_s):
                        tbl_block["spans"] = body_spans
                    blocks.append(tbl_block)
                else:
                    tbl_block = {
                        "type": "table",
                        "no_head": True,
                        "head": ["" for _ in range(col_n)],
                        "rows": grid_cells,
                    }
                    if any(s != [1, 1] for row_s in grid_spans for s in row_s):
                        tbl_block["spans"] = grid_spans
                    blocks.append(tbl_block)
    return blocks


def _replace_para_text(para, new_text: str) -> None:
    props = _run_props(para)
    for child in list(para):
        if child.tag != W + "pPr":
            para.remove(child)
    run = ET.SubElement(para, W + "r")
    if props is not None:
        run.append(copy.deepcopy(props))
    for idx, line in enumerate(new_text.split("\n")):
        if idx:
            ET.SubElement(run, W + "br")
        t = ET.SubElement(run, W + "t")
        t.text = line
        t.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")


def apply_edits_to_official_docx(
    base_docx: bytes, edits: list[tuple[str, str]], title: str = ""
) -> bytes:
    """Apply chronological online user text/cell edits in-place onto a filled official Word .docx
    while preserving 100% of CAC official typography, borders, checkboxes, and seal formatting."""
    if not edits:
        return base_docx
    zin = zipfile.ZipFile(io.BytesIO(base_docx))
    original = zin.read("word/document.xml")
    _register_namespaces(original)
    root = ET.fromstring(original)
    body = root.find(W + "body")
    if body is None:
        return base_docx
    doc_title = title.strip()
    if not doc_title and "docProps/core.xml" in zin.namelist():
        m_title = re.search(rb"<dc:title>(.*?)</dc:title>", zin.read("docProps/core.xml"), re.S)
        if m_title:
            doc_title = m_title.group(1).decode("utf-8", errors="ignore").strip()

    for before, after in edits:
        b_strip = (before or "").strip()
        a_val = after or ""
        if not b_strip or b_strip == a_val.strip():
            continue
        matched = False
        for exact in (True, False):
            target_cmp = b_strip if exact else _flat(b_strip)
            if not target_cmp:
                continue
            for el in body:
                if el.tag == W + "p":
                    cur = _text(el).strip() if exact else _flat(_text(el))
                    if cur == target_cmp and el.find(f".//{W}sym") is None:
                        _replace_para_text(el, a_val)
                        matched = True
                        break
            if matched:
                break
            for tc in body.iter(W + "tc"):
                if tc.find(f".//{W}sym") is not None:
                    continue
                cur = _text(tc).strip() if exact else _flat(_text(tc))
                if cur == target_cmp:
                    props = _run_props(tc)
                    _clear(tc)
                    _put_text(tc, a_val, props)
                    matched = True
                    break
            if matched:
                break
            for tc in body.iter(W + "tc"):
                for p in tc.findall(W + "p"):
                    if p.find(f".//{W}sym") is not None:
                        continue
                    cur = _text(p).strip() if exact else _flat(_text(p))
                    if cur == target_cmp:
                        _replace_para_text(p, a_val)
                        matched = True
                        break
                if matched:
                    break
            if matched:
                break

    xml = _serialise(root, original)
    return _write_clean_zip(zin, xml, doc_title or "数据合规正式文件")


def fill_cells(template: bytes, cells: list[tuple[int, int, int, str, bool]]) -> FillResult:
    """Write values into given cells of the official file as published (nothing cut out): each
    ``(table, row, column, value, append)``; ``append`` adds the value after the printed words of
    the cell (“I/We ____”), otherwise it goes into the blank cell. Positions are those of the
    catalogued file (its fingerprint is checked before use)."""
    zin = zipfile.ZipFile(io.BytesIO(template))
    original = zin.read("word/document.xml")
    _register_namespaces(original)
    root = ET.fromstring(original)
    tables = list(root.find(W + "body").iter(W + "tbl"))
    result = FillResult(b"")
    for ti, ri, ci, value, append in cells:
        if not value.strip():
            continue
        try:
            tr = tables[ti].findall(W + "tr")[ri]
            cell = tr.findall(W + "tc")[ci]
        except IndexError:
            result.missing_labels.append(f"{ti}/{ri}/{ci}")
            continue
        printed = "".join(t.text or "" for t in cell.iter(W + "t"))
        if not append and not printed.strip():
            # 签字/日期栏（同行其他单元格留有多行空白撑高行距、本单元格在底部横线上方）：底端对齐避免文字悬空
            own_paras = len(cell.findall(W + "p"))
            if any(len(other_tc.findall(W + "p")) > own_paras for other_tc in tr.findall(W + "tc")):
                tc_pr = cell.find(W + "tcPr")
                if tc_pr is None:
                    tc_pr = ET.Element(W + "tcPr")
                    cell.insert(0, tc_pr)
                va = tc_pr.find(W + "vAlign")
                if va is None:
                    va = ET.SubElement(tc_pr, W + "vAlign")
                va.set(W + "val", "bottom")
        gap = " " if append and printed and not printed.endswith(" ") else ""
        _put_text(cell, gap + value, _run_props(cell), after_print=append)
        result.filled.append(f"{ti}/{ri}/{ci}")
    xml = _serialise(root, original)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            zout.writestr(item, xml if item.filename == "word/document.xml" else zin.read(item.filename))
    result.data = buf.getvalue()
    return result
