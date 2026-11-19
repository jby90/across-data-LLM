# ruff: noqa: E501
"""Builds ``official/forms_auto.json``: the catalogue entries of official admission forms read from
the publisher's own file, without per-form hand mapping.

    python -m app.cli forms-auto [--only CODE] [--redo]

What each form's spaces are comes from the file itself:
- a fillable PDF: its form controls (text fields, check boxes);
- a print-only PDF: the dotted or underlined blanks and the printed check boxes of its text layer.
For each space the printed words around it (left, above, right) are read off the page; the model
then names the space in Chinese and in the form's language, puts it under the form's own section,
says which company fact fills it (so a value given once — the address, the contact person — is
not asked again on the next form), and groups the check boxes of one question. The model never
invents a space: only spaces found in the file are kept, and spaces for the authority's own use
are left out.

Which admission items use a form is the form's ``matches_zh`` (an expression over the item name),
recorded with the form — not a rule per country."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from pathlib import Path
from typing import Any

import pypdfium2 as pdfium

from app.forms.pdf_fill import FormField, list_fields

HERE = Path(__file__).resolve().parent / "official"
OUT = HERE / "forms_auto.json"
SRC = Path("D:/汽研任务一/来源下载/官方模板")

# 跨表格共用的企业信息：在一份表里填过，别的表自动带入
PROFILE = {
    "company_name": "企业名称（全称）",
    "company_address": "企业地址",
    "company_city": "企业所在城市",
    "company_postcode": "邮政编码",
    "company_country": "企业所在国家",
    "company_reg_no": "商业登记号/统一社会信用代码",
    "company_tax_no": "税务登记号",
    "company_phone": "企业电话",
    "company_fax": "企业传真",
    "company_email": "企业电子邮箱",
    "company_website": "企业网站",
    "legal_rep_name": "法定代表人姓名",
    "legal_rep_title": "法定代表人职务",
    "contact_name": "联系人姓名",
    "contact_title": "联系人职务",
    "contact_phone": "联系人电话",
    "contact_email": "联系人电子邮箱",
    "local_rep_name": "当地代表/代理机构名称",
    "local_rep_address": "当地代表地址",
    "local_rep_phone": "当地代表电话",
    "local_rep_email": "当地代表电子邮箱",
    "manufacturer_name": "制造商名称",
    "manufacturer_address": "制造商地址",
    "manufacturer_country": "制造商所在国家",
    "vehicle_model": "车型",
    "vehicle_brand": "车辆品牌",
    "equipment_name": "设备名称（车载通信模块/终端）",
    "equipment_model": "设备型号",
    "equipment_brand": "设备品牌/商标",
    "purpose": "数据处理目的",
    "data_types": "处理的数据类别",
    "recipient": "数据接收方",
    "recipient_country": "数据接收国",
}

SPECS: list[dict[str, Any]] = [
    {
        "code": "EG_NTRA_TA", "country": "EG", "src": "EG/NTRA_Type-Approval-Application-Form_2025-09_AR-EN.pdf",
        "file": "EG_NTRA_Type-Approval-Application-Form_2025-09.pdf", "direction": "equipment", "language": "en",
        "name_zh": "埃及：电信设备型式批准申请表（NTRA）", "authority_zh": "埃及国家电信管理局（NTRA）", "version": "2025-09",
        "source_url": "https://www.tra.gov.eg/wp-content/uploads/2025/09/Type-Approval-Application-Form.pdf",
        "matches_zh": r"型式|设备.{0,6}(批准|认证|认可)|终端",
    },
    {
        "code": "EG_GOEIC_IMPORTER", "country": "EG", "src": "EG/GOEIC_importer-register-application-form-SO-CR-IA-QR-7-01_AR.pdf",
        "file": "EG_GOEIC_importer-register-application-form_SO-CR-IA-QR-7-01.pdf", "direction": "vehicle", "language": "ar",
        "name_zh": "埃及：进口商登记簿注册申请表及声明（GOEIC）", "authority_zh": "埃及进出口监管总局（GOEIC）", "version": "第五版 2024-01",
        "source_url": "https://www.goeic.gov.eg/upload/online/2024/02/documents/files/ar/1037.pdf",
        "matches_zh": r"进口商.{0,6}(登记|注册)",
    },
    {
        "code": "LB_TRA_TA", "country": "LB", "src": "LB/TRA_type-approval-application-form_EN.pdf",
        "file": "LB_TRA_type-approval-application-form_EN.pdf", "direction": "equipment", "language": "en",
        "name_zh": "黎巴嫩：电信设备型式批准申请表（TRA）", "authority_zh": "黎巴嫩电信管理局（TRA）", "version": "现行版",
        "source_url": "https://www.tra.gov.lb/Library/Files/Uploaded%20files/TAForms/Application%20Forms%20-%20TA%20-%20EN.pdf",
        "matches_zh": r"型式|设备.{0,6}(批准|认证|认可)",
    },
    {
        "code": "LB_TRA_DOC", "country": "LB", "src": "LB/TRA_declaration-of-conformity-RTTE_AR-EN.pdf",
        "file": "LB_TRA_declaration-of-conformity-RTTE_AR-EN.pdf", "direction": "equipment", "language": "en",
        "name_zh": "黎巴嫩：无线电与电信终端设备符合性声明（TRA）", "authority_zh": "黎巴嫩电信管理局（TRA）", "version": "现行版",
        "source_url": "https://www.tra.gov.lb/Library/Files/Uploaded%20files/TAForms/Application%20Forms%20-%20DoC%20-%20RTTE%20-%20AR%20and%20EN.pdf",
        "matches_zh": r"型式|设备.{0,6}(批准|认证|认可)",
    },
    {
        "code": "MA_F214", "country": "MA", "src": "MA/CNDP_F214_Declaration-Conforme-Decision_2025-07-24.pdf",
        "file": "MA_CNDP_F214_2025-07.pdf", "direction": "processing", "applies_if": "not_sensitive", "language": "fr",
        "name_zh": "摩洛哥：按 CNDP 决定简化的个人数据处理申报（CNDP F214）", "authority_zh": "摩洛哥国家数据保护委员会（CNDP）", "version": "2025-07-24",
        "source_url": "https://www.cndp.ma/wp-content/uploads/2025/07/CNDP-Declaration-Normale-Conformement-Decision_F214_20250724_Fr.pdf",
        "matches_zh": r"(申报|备案).{0,4}$|事先申报|处理申报",
        "applies_when_zh": "处理活动属于 CNDP 已发布决定（标准规范）覆盖的类型时，用本表代替普通申报 F211",
    },
    {
        "code": "MA_F113", "country": "MA", "src": "MA/CNDP_F113_Autorisation-Conforme-Decision_2025-09-10.pdf",
        "file": "MA_CNDP_F113_2025-09.pdf", "direction": "processing", "applies_if": "sensitive", "language": "fr",
        "name_zh": "摩洛哥：按 CNDP 决定简化的事先授权申请（CNDP F113）", "authority_zh": "摩洛哥国家数据保护委员会（CNDP）", "version": "2025-09-10",
        "source_url": "https://www.cndp.ma/wp-content/uploads/2025/09/CNDP-Autorisation-Prealable-Conformement-Decision-F113-20250910.pdf",
        "matches_zh": r"处理.{0,6}(授权|许可)|事先授权",
        "applies_when_zh": "须事先授权的处理（敏感数据、生物识别等）属于 CNDP 已发布决定覆盖的类型时，用本表代替 F112",
    },
    {
        "code": "TN_INPDP_DECL", "spaces": ["glyph", "images"], "country": "TN", "src": "TN/INPDP_declaration-traitement_2016-11-18.pdf",
        "file": "TN_INPDP_declaration-traitement_2016-11.pdf", "direction": "processing", "applies_if": "not_sensitive", "language": "fr",
        "name_zh": "突尼斯：个人数据处理事先申报表（INPDP）", "authority_zh": "突尼斯国家个人数据保护局（INPDP）", "version": "2016-11-18",
        "source_url": "http://www.inpdp.nat.tn/declaration.pdf",
        "matches_zh": r"处理.{0,4}(事先)?申报|申报.{0,4}处理",
    },
    {
        "code": "TN_INPDP_BIO", "spaces": ["glyph", "images"], "country": "TN", "src": "TN/INPDP_autorisation-donnees-biometriques_2016-11-18.pdf",
        "file": "TN_INPDP_autorisation-biometrie_2016-11.pdf", "direction": "processing", "applies_if": "sensitive", "language": "fr",
        "name_zh": "突尼斯：生物识别数据处理授权申请表（INPDP）", "authority_zh": "突尼斯国家个人数据保护局（INPDP）", "version": "2016-11-18",
        "source_url": "http://www.inpdp.nat.tn/biometrie.pdf",
        "matches_zh": r"生物|授权许可证|处理授权",
        "applies_when_zh": "处理指纹、人脸等生物识别数据（如座舱人脸识别、驾驶员状态识别）时",
    },
    {
        "code": "TN_INPDP_COMM", "spaces": ["glyph", "images"], "country": "TN", "src": "TN/INPDP_autorisation-donnees-communication_2016-11-18.pdf",
        "file": "TN_INPDP_autorisation-communication_2016-11.pdf", "direction": "processing", "applies_if": "sensitive", "language": "fr",
        "name_zh": "突尼斯：向第三方提供个人数据授权申请表（INPDP）", "authority_zh": "突尼斯国家个人数据保护局（INPDP）", "version": "2016-11-18",
        "source_url": "http://www.inpdp.nat.tn/communication.pdf",
        "matches_zh": r"第三方|提供.{0,6}授权|传输.{0,4}授权(?!.*境)",
    },
    {
        "code": "TN_ATTT_RECEPTION", "country": "TN", "src": "TN/ATTT_demande-reception-vehicule-par-type-ou-titre-isole_AR_via-SICAD.pdf",
        "file": "TN_ATTT_demande-reception-vehicule_AR.pdf", "direction": "vehicle", "language": "ar",
        "name_zh": "突尼斯：车辆型式认证（按型/单车）申请表（ATTT）", "authority_zh": "突尼斯陆路运输技术局（ATTT）", "version": "SICAD 现行版",
        "source_url": "http://www.sicad.gov.tn/Fr/upload/documents/imprimes/ar/pdf/15161013701.pdf",
        "matches_zh": r"车辆.{0,6}(型式|认证|接收|homologation)|型式认证申请",
    },
    {
        "code": "TN_CERT_HOMOLOGATION", "country": "TN", "src": "TN/CERT_demande-homologation-terminaux-telecom-radio_REV4_via-SICAD.pdf",
        "file": "TN_CERT_demande-homologation-terminaux_REV4.pdf", "direction": "equipment", "language": "fr",
        "name_zh": "突尼斯：电信终端及无线电设备型式认证申请表（CERT）", "authority_zh": "突尼斯电信研究中心（CERT）", "version": "REV.4",
        "source_url": "http://www.sicad.gov.tn/Fr/upload/documents/imprimes/fr/pdf/15158010101.pdf",
        "matches_zh": r"电信终端|无线电设备|设备.{0,6}型式",
    },
    {
        "code": "MA_ANRT_AGREMENT", "country": "MA", "row_by_bottom": True, "inline_after": {"p0_k419_320": "q_s4"}, "src": "MA/SGG_BO7376_p290-291_ANRT-Decision-16-24_Annexe1-formulaire-demande.pdf",
        "file": "MA_ANRT_formulaire-demande-agrement_BO7376.pdf", "direction": "equipment", "language": "fr",
        "name_zh": "摩洛哥：电信终端与无线电设备认可（agrément）申请表（ANRT）", "authority_zh": "摩洛哥国家电信管理局（ANRT）",
        "version": "ANRT/DG/N°16/24 号决定附件 1（BO 7376）",
        "source_url": "https://www.sgg.gov.ma/BO/FR/2873/2025/BO_7376_fr.pdf", "spaces": ["leader", "glyph", "colon", "square"],
        "matches_zh": r"无线电设施|终端设备|设备.{0,6}(批准|认可|认证)",
    },
    {
        "code": "TN_ANCE_CRYPTO", "country": "TN", "src": "TN/ANCE_demande-homologation-dispositif-cryptage_ancien-regime_via-SICAD.pdf",
        "file": "TN_ANCE_demande-homologation-cryptage.pdf", "direction": "equipment", "language": "fr",
        "name_zh": "突尼斯：加密手段技术核准申请表（ANCE）", "authority_zh": "突尼斯国家电子认证局（ANCE）", "version": "SICAD 现行版",
        "source_url": "http://www.sicad.gov.tn/Fr/upload/documents/imprimes/fr/pdf/15155010301.pdf", "applies_when_zh": "现行做法经 CERT 统一窗口申报，窗口另有表格时以窗口为准", "spaces": ["leader", "glyph", "colon", "square"],
        "matches_zh": r"加密.{0,6}(技术|认证|核准|认可)",
    },
    {
        "code": "SG_LTA_R01", "country": "SG", "src": "SG/LTA_OneMotoring_R01_Registration_Licensing_Motor_Vehicle.pdf",
        "file": "SG_LTA_R01_Registration_Licensing_Motor_Vehicle.pdf", "direction": "vehicle", "language": "en",
        "name_zh": "新加坡：机动车注册与牌照申请表（LTA R01）", "authority_zh": "新加坡陆路交通管理局（LTA）", "version": "现行版",
        "source_url": "https://onemotoring.lta.gov.sg/content/dam/onemotoring/Forms/R01.pdf",
        "matches_zh": r"车辆注册|注册批准|车辆登记",
    },
    {
        "code": "SG_LTA_AV", "country": "SG", "src": "SG/LTA_AV_Application_Form_PublicRoads_AV01-AV06_2025-01-14.pdf",
        "file": "SG_LTA_AV_Application_Form_PublicRoads_2025-01-14.pdf", "direction": "vehicle", "language": "en",
        "name_zh": "新加坡：自动驾驶车辆公共道路测试与使用申请表（LTA AV01–AV06）", "authority_zh": "新加坡陆路交通管理局（LTA）", "version": "2025-01-14",
        "source_url": "https://www.lta.gov.sg/content/dam/ltagov/industry_innovations/Technologies/PDF/av_application_20250114.pdf",
        "matches_zh": r"自动驾驶",
    },
]

OFFICIAL_ONLY = re.compile(r"r[ée]serv[ée]|official use|for office use|internal use|内部使用|خاص بال(إدارة|هيئة)|机关(内部)?填写|局内填|主管机关填|仅供.{0,6}填", re.IGNORECASE)
LEADER = re.compile(r"[._…‥⋯\u2026]{5,}|_{3,}")
BOX_GLYPH = set("□☐❑❒■▢◻⬜\uf06f\uf0a8\uf071\uf072\uf0fe")


def _bounded(tp, left: float, bottom: float, right: float, top: float) -> str:
    try:
        return " ".join(tp.get_text_bounded(left=left, bottom=bottom, right=right, top=top).split())
    except Exception:  # noqa: BLE001
        return ""


def _context(tp, f: FormField, page_w: float) -> dict[str, str]:
    h = max(f.height, 8.0)
    near = _bounded(tp, max(0, f.left - 110), f.bottom - 1, f.left - 0.5, f.top + 1)[-60:]
    # 左边一行里常印着好几个标签（“Code postal …… Ville ……”）：紧挨着格子的那个才是这一格的标签；
    # 左边没字的长格子（问题印在上面一两行）往上多看一些
    return {
        "left_near": near,
        "left": _bounded(tp, max(0, f.left - 260), f.bottom - 1, f.left - 0.5, f.top + 1)[-120:],
        "above": _bounded(tp, f.left - 10, f.top, min(page_w, f.right + 10), f.top + (h + 6 if near else 45))[:160],
        "right": _bounded(tp, f.right + 0.5, f.bottom - 1, min(page_w, f.right + 220), f.top + 1)[:90],
    }


def printed_spaces(pdf_bytes: bytes, modes: tuple[str, ...] = ("leader", "glyph")) -> list[FormField]:
    """The blanks of a print-only form: runs of dots/underscores (a line to write on) and printed
    check boxes, as boxes in PDF points. Dotted runs that follow each other on one line are one
    blank each; a run's box spans the run, with room above the dots for the writing.
    Forms laid out without dots say so in their spec (``spaces``): ``colon`` — the blank after a
    label ending in a colon, up to the next printed word on the line; ``square`` — check boxes
    drawn as small squares rather than printed as a character."""
    pdf = pdfium.PdfDocument(pdf_bytes)
    out: list[FormField] = []
    for pi in range(len(pdf)):
        tp = pdf[pi].get_textpage()
        n = tp.count_chars()
        text = tp.get_text_range(0, n) if n else ""
        if len(text) != n:  # 文字层与字符序号对不上时只按字符逐个读
            text = "".join(tp.get_text_range(i, 1) or " " for i in range(n))
        for m in LEADER.finditer(text):
            boxes = [tp.get_charbox(i) for i in range(m.start(), m.end())]
            boxes = [b for b in boxes if b[2] > b[0]]
            if not boxes:
                continue
            # 跨行的点线按行拆开（下一行的 x 回到左边）
            line: list[tuple] = []
            for b in boxes + [None]:
                if b is not None and (not line or (abs(b[1] - line[-1][1]) < 4 and b[0] >= line[-1][0] - 1)):
                    line.append(b)
                    continue
                if line:
                    left, right = min(x[0] for x in line), max(x[2] for x in line)
                    base = min(x[1] for x in line)
                    if right - left >= 18:
                        k = f"p{pi}_{int(left)}_{int(base)}"
                        out.append(FormField(key=k, name=k, kind="text", page=pi, left=left, bottom=base - 1, right=right, top=base + 11))
                line = [b] if b is not None else []
        if "colon" in modes:
            out.extend(_colon_blanks(tp, text, pi, pdf[pi].get_width()))
        if "square" in modes:
            out.extend(_drawn_squares(pdf[pi], pi))
        if "lines" in modes:
            out.extend(_drawn_lines(pdf[pi], pi))
        if "images" in modes:
            out.extend(_image_blanks(pdf[pi], pi, pdf_bytes, tp))
        for i, ch in enumerate(text if "glyph" in modes else ""):
            if ch in BOX_GLYPH:
                b = tp.get_charbox(i)
                if b[2] > b[0]:
                    k = f"p{pi}_c{int(b[0])}_{int(b[1])}"
                    out.append(FormField(key=k, name=k, kind="check", page=pi, left=b[0], bottom=b[1], right=b[2], top=b[3]))
    if "colon" in modes:
        # 后来补认的空白放在最后：已有位置的编号不变（模型结果缓存、已存答案都按编号对应）
        for pi in range(len(pdf)):
            tp = pdf[pi].get_textpage()
            n = tp.count_chars()
            text = tp.get_text_range(0, n) if n else ""
            if len(text) != n:
                text = "".join(tp.get_text_range(i, 1) or " " for i in range(n))
            for f in _open_line_blanks(tp, text, pi, pdf[pi].get_width()):
                if not any(g.page == f.page and g.left < f.right and f.left < g.right and g.bottom < f.top and f.bottom < g.top for g in out):
                    out.append(f)
    return out


FILL_MARK = set("-–—_.…·")
OPEN_END = re.compile(r"(?:^|\s)(?:de|du|des|of|à|au|par|by)\s*$", re.IGNORECASE)


def _open_line_blanks(tp, text: str, pi: int, page_w: float) -> list[FormField]:
    """Blanks the colon rule misses: a label ending in a colon followed only by printed dashes
    (“Je soussigné(e) :- - -”), and a short line ending in “de / of …” with nothing after it
    (“En qualité de”). The blank runs from the end of the label to the right margin."""
    lines: dict[int, list[tuple[float, float, float, float, str]]] = {}
    for i, ch in enumerate(text):
        if ch in "\r\n":
            continue
        b = tp.get_charbox(i)
        if b[2] <= b[0]:
            continue
        lines.setdefault(round(b[1] / 3), []).append((b[0], b[1], b[2], b[3], ch))
    out = []
    end = page_w - 40
    for chars in lines.values():
        chars.sort()

        def spaced(cs):  # 文字层里常没有空格字符：按字间距补上
            s, prev = "", None
            for c in cs:
                if prev is not None and c[0] - prev > 1.5:
                    s += " "
                s, prev = s + c[4], c[2]
            return s

        words = spaced(chars)
        label_chars = [c for c in chars if c[4] not in FILL_MARK and c[4].strip()]
        if not label_chars:
            continue
        label = spaced(label_chars).strip()
        last = max(c[2] for c in label_chars if c[4].strip())
        bottom = min(c[1] for c in label_chars if c[4].strip())
        short = len(label.split()) <= 6
        if not short or end - last < 120:
            continue
        colon_then_fill = label.endswith((":", "：")) and any(c[4] in FILL_MARK for c in chars if c[0] > last - 1)
        if colon_then_fill or OPEN_END.search(words.rstrip()):
            start = max(c[2] for c in chars if c[4].strip()) if colon_then_fill else last  # 印着的短横之后
            k = f"p{pi}_x{int(last)}_{int(bottom)}"
            out.append(FormField(key=k, name=k, kind="text", page=pi, left=start + 4, bottom=bottom - 2, right=end, top=bottom + 10))
    return out


def lead_in_colons(pdf_bytes: bytes, fields: list[FormField]) -> set[str]:
    """Colon “blanks” that end a sentence introducing a list (“… je m'engage sur l'honneur à :” followed by
    1., 2., …): not a place to write. Told apart by the length of the text before the colon."""
    pdf = pdfium.PdfDocument(pdf_bytes)
    out = set()
    for f in fields:
        if "_k" not in f.key or f.kind != "text":
            continue
        page = pdf[f.page]
        # 只看紧挨冒号的那段字：同一行左边还有别的格子时，从那个格子右边算起
        x0 = max((g.right for g in fields if g.page == f.page and g.right <= f.left and g.bottom < f.top - 2 and f.bottom + 2 < g.top), default=0)
        before = page.get_textpage().get_text_bounded(x0, f.bottom + 2, f.left - 1, f.top - 2) or ""
        if len(before.split()) > 8:
            out.add(f.key)
    return out


def _colon_blanks(tp, text: str, pi: int, page_w: float) -> list[FormField]:
    lines: dict[int, list[tuple[float, float, float, float, str]]] = {}
    for i, ch in enumerate(text):
        if ch in "\r\n":
            continue
        b = tp.get_charbox(i)
        if b[2] <= b[0]:
            continue
        lines.setdefault(round(b[1] / 3), []).append((b[0], b[1], b[2], b[3], ch))
    out = []
    for chars in lines.values():
        chars.sort()
        for j, (_left, bottom, right, _top, ch) in enumerate(chars):
            if ch not in ":：":
                continue
            nxt = next((c for c in chars[j + 1 :] if c[4].strip()), None)
            end = (nxt[0] - 6) if nxt else page_w - 40
            if end - right >= 40:
                k = f"p{pi}_k{int(right)}_{int(bottom)}"
                out.append(FormField(key=k, name=k, kind="text", page=pi, left=right + 3, bottom=bottom - 2, right=end, top=bottom + 10))
    return out


def _drawn_squares(page, pi: int) -> list[FormField]:
    out, seen = [], set()
    for obj in page.get_objects(max_depth=3):
        if obj.type != pdfium.raw.FPDF_PAGEOBJ_PATH:
            continue
        left, bottom, right, top = obj.get_bounds()
        w, h = right - left, top - bottom
        if 5 <= w <= 14 and 5 <= h <= 14 and abs(w - h) < 2.5:
            k = f"p{pi}_s{int(left)}_{int(bottom)}"
            if k not in seen:
                seen.add(k)
                out.append(FormField(key=k, name=k, kind="check", page=pi, left=left, bottom=bottom, right=right, top=top))
    return out


def _drawn_lines(page, pi: int) -> list[FormField]:
    """Blanks drawn as a line to write on (a long thin horizontal path, or the base line of a row
    of character cells); the box is the space just above the line."""
    out, seen = [], set()
    for obj in page.get_objects(max_depth=3):
        if obj.type != pdfium.raw.FPDF_PAGEOBJ_PATH:
            continue
        left, bottom, right, top = obj.get_bounds()
        if right - left >= 30 and top - bottom <= 2.0:
            k = f"p{pi}_l{int(left)}_{int(bottom)}"
            if k not in seen:
                seen.add(k)
                out.append(FormField(key=k, name=k, kind="text", page=pi, left=left + 2, bottom=bottom + 1, right=right - 2, top=bottom + 12))
    return out


SHAPE_SCALE = 4.0
EMAIL_HELP = "完整邮箱，如 name@example.com；原表 @ 前后各一段，平台自动拆开填写"


def _image_blanks(page, pi: int, pdf_bytes: bytes | None = None, tp=None) -> list[FormField]:
    """Blanks pasted into the form as pictures of a line or a row of cells (Tunisia's INPDP forms):
    flat images, wide and at most 25 points high. The picture is often wider than what is drawn in it
    (it runs under the printed label next to it): the box is narrowed to the drawn line, a row of
    evenly spaced ticks is a row of cells (one character each), and a printed “@” inside it splits it in two."""
    out = []
    boxes = []
    for obj in page.get_objects(max_depth=4):
        if obj.type != pdfium.raw.FPDF_PAGEOBJ_IMAGE:
            continue
        left, bottom, right, top = obj.get_bounds()
        if right - left >= 40 and top - bottom <= 25:
            boxes.append((left, bottom, right, top))
    shapes = _drawn_shapes(pdf_bytes, pi, boxes) if pdf_bytes is not None else {}
    for left, bottom, right, top in boxes:
        k = f"p{pi}_i{int(left)}_{int(bottom)}"
        ticks = shapes.get((left, bottom))
        l2, r2, cells, segments = left + 3, right - 3, 0, ()
        if ticks and len(ticks) >= 2 and ticks[-1] - ticks[0] >= 20:
            l2, r2 = ticks[0] + 1.5, ticks[-1] - 1.5
            gaps = [b - a for a, b in zip(ticks, ticks[1:], strict=False)]
            if len(ticks) >= 3 and max(gaps) - min(gaps) <= 0.25 * (sum(gaps) / len(gaps)):
                l2, r2, cells = ticks[0], ticks[-1], len(ticks) - 1
            elif len(ticks) == 2 and tp is not None:
                at = _printed_at(tp, left, bottom, right, top)
                if at and l2 + 10 < at[0] and at[1] + 10 < r2 and _shows(pdf_bytes, pi, at, bottom, top):
                    segments = ((round(l2, 2), round(at[0] - 1, 2)), (round(at[1] + 1, 2), round(r2, 2)))
        out.append(FormField(key=k, name=k, kind="text", page=pi, left=l2, bottom=bottom + 2, right=r2, top=top - 2,
                             cells=cells, segments=segments))
    return out


def _drawn_shapes(pdf_bytes: bytes, pi: int, boxes: list[tuple]) -> dict[tuple, list[float]]:
    """For each picture box: x positions of the vertical strokes drawn in it, the printed text taken off
    the page first (labels printed over a picture are not part of the blank). Outer strokes are its ends."""
    doc = pdfium.PdfDocument(pdf_bytes)
    page = doc[pi]
    for obj in list(page.get_objects(max_depth=1)):
        if obj.type == pdfium.raw.FPDF_PAGEOBJ_TEXT:
            page.remove_obj(obj)
    page.gen_content()
    s, h = SHAPE_SCALE, page.get_height()
    img = page.render(scale=s).to_pil().convert("L")
    px = img.load()
    out: dict[tuple, list[float]] = {}
    for left, bottom, right, top in boxes:
        x0, x1 = max(0, int(left * s)), min(img.width, int(right * s))
        y0, y1 = max(0, int((h - top) * s)), min(img.height, int((h - bottom) * s))
        tall = (y1 - y0) * 0.35
        runs: list[list[int]] = []
        for x in range(x0, x1):
            if sum(1 for y in range(y0, y1) if px[x, y] < 215) >= tall:
                if runs and x - runs[-1][1] <= 2:
                    runs[-1][1] = x
                else:
                    runs.append([x, x])
        out[(left, bottom)] = [round((a + b) / 2 / s, 2) for a, b in runs]
    return out


_RENDERED: dict[tuple[int, int], object] = {}


def _shows(pdf_bytes: bytes, pi: int, at: tuple[float, float], bottom: float, top: float) -> bool:
    """Whether the printed “@” can be seen on the page (some forms keep it in the text layer under the picture)."""
    key = (id(pdf_bytes), pi)
    if key not in _RENDERED:
        _RENDERED.clear()
        page = pdfium.PdfDocument(pdf_bytes)[pi]
        _RENDERED[key] = (page.render(scale=SHAPE_SCALE).to_pil().convert("L"), page.get_height())
    img, h = _RENDERED[key]
    s = SHAPE_SCALE
    px = img.load()
    x0, x1 = int(at[0] * s), int(at[1] * s)
    y0, y1 = int((h - top) * s), int((h - bottom) * s)
    return sum(1 for x in range(x0, x1) for y in range(y0, y1) if px[x, y] < 128) >= 6


def _printed_at(tp, left: float, bottom: float, right: float, top: float) -> tuple[float, float] | None:
    """The left and right edge of a printed “@” inside the box (an e-mail blank in two parts)."""
    n = tp.count_chars()
    for i in range(n):
        if tp.get_text_range(i, 1) == "@":
            b = tp.get_charbox(i)
            if left <= (b[0] + b[2]) / 2 <= right and bottom - 2 <= (b[1] + b[3]) / 2 <= top + 2:
                return b[0], b[2]
    return None


SYSTEM = """你在为中国车企整理外国官方申报表格。给你一份官方表格的全文和表格里每个可填位置（编号、类型、位置周围印着的字），请逐个说明。
输出 JSON：{"legal_basis_zh": "表格上写明的法律依据（没写就空）", "timing_zh": "表格上写明的期限或办理时间（没写就空）",
 "fields": [{"i": 编号, "skip": true/false, "section_zh": "所属栏目（用表格自己的栏目，译成中文，如“一、申请人信息”）", "section_orig": "栏目原文",
  "label_zh": "这一栏填什么（中文，简短）", "label_orig": "这一栏的原文标签", "fact": "下列企业信息键之一或 null", "required": true/false,
  "question_zh": "问企业的一句话", "help_zh": "填写提示（可空）", "group": "勾选框所属问题的编号（同一问题的勾选框写同一个字符串；文本栏写 null）",
  "group_label_zh": "勾选框所属问题（中文）", "option_zh": "这个勾选框代表的选项（中文）", "single": true/false（同一问题只能选一个时为 true）}]}
规则：
- 每一格的标签以 left_near（紧挨着格子左边的字）为准；left_near 为空时看 above（格子上方印的问题）；不要用同一行更左边的其他标签。同一张表有多行相同的栏目时，按 y 从大到小（从上到下）写明第几行。
- 只说明给你的位置，不要编造；表格写明“仅供主管机关填写/For official use/réservé à l'administration/خاص بالإدارة”的位置、签名、盖章、手写日期位置 skip=true。
- 页眉页脚里的点线、目录引导点、正文里的省略号等不是填写位置，skip=true。
- fact 只能取这些键：{facts}；与企业信息无关（如设备频段、数据保存期限）写 null。同一表里同一方的多个栏目各自对应（如申请人地址→company_address，制造商地址→manufacturer_address）。
- 表格是阿拉伯文或法文时 label_orig 写原文；label_zh 用中国企业看得懂的中文。"""


def _pages_text(pdf_bytes: bytes, limit: int = 9000) -> str:
    pdf = pdfium.PdfDocument(pdf_bytes)
    parts = [f"[第{i + 1}页]\n" + pdf[i].get_textpage().get_text_range() for i in range(len(pdf))]
    return "\n".join(parts)[:limit]


async def _describe(spec: dict, data: bytes, fields: list[FormField], only: set[int] | None = None) -> tuple[dict, list[dict], int]:
    """``only``: describe just these positions (newly found ones); the numbering stays that of ``fields``."""
    from app.analysis.llm import call_llm_json

    pdf = pdfium.PdfDocument(data)
    tps = {}
    listing = []
    for i, f in enumerate(fields):
        if only is not None and i not in only:
            continue
        if f.page not in tps:
            tps[f.page] = pdf[f.page].get_textpage()
        ctx = _context(tps[f.page], f, pdf[f.page].get_width())
        listing.append({"i": i, "kind": "勾选框" if f.kind in ("check", "radio") else "文本", "page": f.page + 1,
                        "y": round(f.top), "x": round(f.left), **ctx,
                        **({"name": f.name} if not f.name.startswith("p") or "_" not in f.name else {})})
    # 按页面位置排（从上到下、从左到右）再分批：几轮找出来的位置编号是乱序的，同一行、同一张表的格子要在同一批里
    listing.sort(key=lambda x: (x["page"], -x["y"], x["x"]))
    text = _pages_text(data)
    system = SYSTEM.replace("{facts}", "、".join(f"{k}（{v}）" for k, v in PROFILE.items()))
    meta: dict = {}
    out: list[dict] = []
    tokens = 0
    from app.analysis.llm import LlmUnavailableError

    queue = [listing[k : k + 22] for k in range(0, len(listing), 22)]
    while queue:
        part = queue.pop(0)
        try:
            res = await call_llm_json(
                [{"role": "system", "content": system},
                 {"role": "user", "content": f"表格：{spec['name_zh']}（{spec['authority_zh']}）\n全文：\n{text}\n\n可填位置：\n{json.dumps(part, ensure_ascii=False)}"}],
                max_tokens=12000, temperature=0.0,
            )
        except LlmUnavailableError:
            if len(part) > 4:  # 输出被截断时拆成两半再问
                queue[:0] = [part[: len(part) // 2], part[len(part) // 2 :]]
                continue
            raise
        tokens += int(res.prompt_tokens or 0) + int(res.completion_tokens or 0)
        data_ = res.data if isinstance(res.data, dict) else {}
        if not meta:
            meta = {k2: data_.get(k2, "") for k2 in ("legal_basis_zh", "timing_zh")}
        out.extend(x for x in data_.get("fields", []) if isinstance(x, dict))
    return meta, out, tokens


HEADING = re.compile(r"^\s*(\d+|[IVX]+)[.)]\s+\S|\S\s+\d+\.\s*$")  # 右到左排版的文字层里，编号在行尾


def official_zones(pdf_bytes: bytes) -> list[tuple[int, float, float]]:
    """Parts of the form the authority fills itself: from a printed heading that says so
    (“Réservé à l'INPDP”, “For official use”) down to the next numbered heading. (page, top, bottom)."""
    pdf = pdfium.PdfDocument(pdf_bytes)
    zones = []
    for pi in range(len(pdf)):
        tp = pdf[pi].get_textpage()
        n = tp.count_chars()
        text = tp.get_text_range(0, n) if n else ""
        if len(text) != n:
            continue
        lines, start = [], 0
        for k, ch in enumerate(text + "\n"):
            if ch == "\n":
                seg = text[start:k].strip("\r")
                box = next((tp.get_charbox(j) for j in range(start, k) if text[j].strip()), None)
                if seg.strip() and box:
                    lines.append((box[3], seg))
                start = k + 1
        for top, seg in lines:
            if OFFICIAL_ONLY.search(seg):
                below = [t for t, sg in lines if t < top - 2 and HEADING.search(sg)]
                zones.append((pi, top + 2, max(below) + 1 if below else 0.0))
    return zones


DATE_LABEL = re.compile(r"\bdate\b|(^|\s)le\s*[:,]|التاريخ|日期", re.IGNORECASE)
SIGN_OR_STAMP = re.compile(r"signature|sign[ée]|cachet|stamp|NRIC|FIN\b|签名|签字|盖章|印章|التوقيع|الختم", re.IGNORECASE)
SIGN_SECTION = re.compile(r"声明|签名|签字|签署|承诺|declaration|engagement|signature", re.IGNORECASE)


CN_NUM = {c: n for n, c in enumerate("一二三四五六七八九", 1)}
SEC_CODE = re.compile(r"(?<![\d.])(\d+(?:\.\d+)*)(?=[\s.、:：）)]|$)")
SEC_TAIL = re.compile(r"\s*(——.*|（[^（）]*）|\([^()]*\))\s*$")


def _cn_number(text: str) -> int | None:
    """一 … 九十九 at the start of a heading (“十一、”)."""
    m = re.match(r"\s*([一二三四五六七八九十]{1,3})[、.．]", text)
    if not m:
        return None
    t = m[1]
    if "十" not in t:
        return CN_NUM.get(t) if len(t) == 1 else None
    tens, _, ones = t.partition("十")
    return (CN_NUM.get(tens, 1) if tens else 1) * 10 + (CN_NUM.get(ones, 0) if ones else 0)


def _section_key(title: str) -> str:
    """The form's own number of a section (the deepest one written, “3.5.1”), else its name without the number."""
    codes = SEC_CODE.findall(title)
    if codes:
        return "#" + codes[-1]
    cn = _cn_number(title)
    if cn is not None:
        return f"#{cn}"
    return re.sub(r"\s+", "", SEC_TAIL.sub("", title))


def _merged_title(titles: list[str], key: str) -> str:
    """One heading for sections merged under the same number: one that starts with that number if there is one,
    without the part that only named the piece (“——身份”“（3.5.1 …）”)."""
    num = key[1:] if key.startswith("#") else ""
    starts = [t for t in titles if num and (t.strip().startswith(num) or (_cn_number(t) is not None and str(_cn_number(t)) == num))]
    head = (starts or titles)[0]
    stripped = SEC_TAIL.sub("", head).strip()
    if num and _section_key(stripped) != key:  # 去掉后缀会把编号也去掉（“六、……——6.3 ……”）：保留原标题
        return head
    return stripped or head


def _sign_date(d: dict) -> bool:
    """A blank the model skipped as a handwritten date, next to the signature: kept as an optional item."""
    orig, zh, sec = str(d.get("label_orig") or ""), str(d.get("label_zh") or ""), f"{d.get('section_zh') or ''} {d.get('section_orig') or ''}"
    if not (DATE_LABEL.search(orig) or "日期" in zh) or SIGN_OR_STAMP.search(f"{orig} {zh}") or OFFICIAL_ONLY.search(f"{sec} {orig} {zh}"):
        return False
    return "签署日期" in zh or bool(SIGN_SECTION.search(sec))


def _max_chars(f: FormField) -> int:
    from app.forms.pdf_fill import max_chars_for

    return max_chars_for(f)


def _name_table_cells(fields: list[FormField], sections: list[dict], by_i: dict, uid) -> None:
    """A table of cells (rows of at least 3 boxes with the same column widths, one under the other): the
    model names the first row by the printed column headers but often calls the rows below “blank cell”
    and skips some of them. When a lower row has repeated names or skipped cells, every cell is named
    “第 N 行—<column>” from the first row, skipped cells of a row that has others are added (optional),
    and a lone box of the same name just above the table (a stray copy of a header) is dropped."""
    item_of = {it["pdf_key"]: (it, s) for s in sections for it in s["items"] if it.get("pdf_key")}
    texts = sorted(((i, f) for i, f in enumerate(fields) if f.kind == "text"), key=lambda x: (x[1].page, -x[1].top, x[1].left))
    rows: list[list[tuple[int, FormField]]] = []
    for i, f in texts:
        for r in rows:
            g = r[0][1]
            if g.page == f.page and abs(g.top - f.top) <= 2 and abs(g.bottom - f.bottom) <= 2:
                r.append((i, f))
                break
        else:
            rows.append([(i, f)])
    rows = [sorted(r, key=lambda x: x[1].left) for r in rows if len(r) >= 3]

    def same(a: FormField, b: FormField) -> bool:
        return abs(a.left - b.left) <= 2 and abs(a.right - b.right) <= 2

    grids: list[list[list[tuple[int, FormField]]]] = []
    for r in rows:
        for g in grids:
            last = g[-1]
            lf, rf = last[0][1], r[0][1]
            shared = sum(1 for _, f in r if any(same(f, h) for _, h in last))
            if lf.page == rf.page and shared >= 3 and -2 <= lf.bottom - rf.top <= 40:
                g.append(r)
                break
        else:
            grids.append([r])
    strip = re.compile(r"^第\s*\d+\s*行\s*[—\-－:：]\s*")
    for g in grids:
        if len(g) < 2:
            continue
        head = {}
        for _, f in g[0]:
            if f.key in item_of:
                head[f.key] = item_of[f.key][0]
        if len(head) < 3:
            continue
        cols = [(f, head[f.key]) for _, f in g[0] if f.key in head]
        trouble = False
        for r in g[1:]:
            names = [item_of[f.key][0]["label_zh"] for _, f in r if f.key in item_of]
            if not names:
                continue
            if len(set(names)) < len(names) or any(f.key not in item_of and any(same(f, h) for h, _ in cols) for _, f in r):
                trouble = True
        if not trouble:
            continue
        sec = item_of[cols[0][0].key][1]
        for n, r in enumerate(g, 1):
            if n > 1 and not any(f.key in item_of for _, f in r):
                continue  # 整行都没收：多半是说明行，不补
            for i, f in r:
                col = next((it for h, it in cols if same(f, h)), None)
                if col is None:
                    continue
                name = strip.sub("", col["label_zh"])
                d = by_i.get(i) or {}
                if OFFICIAL_ONLY.search(f"{d.get('section_zh') or ''} {d.get('label_orig') or ''}"):
                    continue
                if f.key in item_of:
                    it = item_of[f.key][0]
                else:
                    it = {"id": uid(f"t{i}"), "label_fr": col.get("label_fr", ""), "pdf_key": f.key, "kind": "text", "fact": None,
                          "help_zh": "", "options": None, "option_labels_zh": None,
                          "max_chars": _max_chars(f)}
                    sec["items"].append(it)
                    item_of[f.key] = (it, sec)
                it["label_zh"] = f"第{n}行—{name}"
                q = strip.sub("", str(col.get("question_zh") or f"{name}是？"))
                it["question_zh"] = f"（第 {n} 行）{q}"
                it["required"] = bool(col.get("required")) if n == 1 else False
        top = g[0][0][1]
        left, right = min(f.left for _, f in g[0]), max(f.right for _, f in g[0])
        names = {strip.sub("", it["label_zh"]) for _, it in cols}
        inside = {f.key for r in g for _, f in r}
        for key, (it, s) in list(item_of.items()):
            f = next((x for x in fields if x.key == key), None)
            if (f is None or key in inside or f.page != top.page or not (0 <= f.bottom - top.top <= 60)
                    or f.left < left - 2 or f.right > right + 2 or it["label_zh"] not in names):
                continue
            s["items"].remove(it)
            del item_of[key]


def _assemble(spec: dict, fields: list[FormField], meta: dict, described: list[dict], zones: list | None = None,
              skip: set[str] | None = None) -> dict:
    by_i = {}
    for d in described:
        try:
            by_i[int(d.get("i"))] = d
        except (TypeError, ValueError):
            continue
    sections: list[dict] = []
    sec_of: dict[str, dict] = {}
    groups: dict[str, dict] = {}
    moved: dict[str, str] = {}
    used_ids: set[str] = set()

    def section(title_zh: str, title_orig: str) -> dict:
        title_zh = title_zh or "表格内容"
        if title_zh not in sec_of:
            sec_of[title_zh] = {"id": f"S{len(sections) + 1}", "title_zh": title_zh, "title_fr": title_orig or "", "items": []}
            sections.append(sec_of[title_zh])
        return sec_of[title_zh]

    def uid(base: str) -> str:
        base = re.sub(r"[^a-z0-9_]", "", base.lower())[:40] or "f"
        n, out = 1, base
        while out in used_ids:
            n += 1
            out = f"{base}_{n}"
        used_ids.add(out)
        return out

    for i, f in enumerate(fields):
        if any(f.page == pg and bottom <= f.bottom <= top for pg, top, bottom in zones or []):
            continue  # 主管机关自己填的部分（印着“仅供主管机关填写”的栏目）
        if f.key in (skip or ()):
            continue  # 引出下面 1. 2. … 各条的句子末尾的冒号，不是填写位置
        if f.kind == "text" and any(g.kind == "text" and g.page == f.page and abs(g.left - f.left) <= 2 and abs(g.right - f.right) <= 2
                                    and abs(g.bottom - f.bottom) <= 2 and abs(g.top - f.top) <= 2 for g in fields[:i]):
            continue  # 原件在同一位置叠放了两个控件：只收一个（否则同一格问两遍、导出叠写两遍）
        d = by_i.get(i)
        if d and d.get("skip") and f.kind not in ("check", "radio") and _sign_date(d):
            # 签署日期：和手工核过的表一致，收成选填题（可留空，打印后手写）；签名、盖章本身仍留给企业
            d = {**d, "skip": False, "label_zh": "签署日期", "fact": None, "required": False, "sign_date": True,
                 "question_zh": "签署日期是？（可留空，打印后手写）", "help_zh": "可留空，打印后手写"}
        if not d or d.get("skip"):
            continue
        if OFFICIAL_ONLY.search(f"{d.get('section_zh') or ''} {d.get('section_orig') or ''} {d.get('label_orig') or ''}"):
            continue  # 主管机关自己填的栏目
        sec = section(str(d.get("section_zh") or ""), str(d.get("section_orig") or ""))
        fact = d.get("fact") if d.get("fact") in PROFILE else None
        if f.kind in ("check", "radio"):
            # 多选题按“栏目 + 问题”归并（模型有时给每个勾选框单独编号）；单选题按模型给的题号
            g = str(d.get("group") or f"g{i}") if d.get("single") else f"{sec['id']}|{d.get('group_label_zh') or d.get('group') or i}"
            opt = str(d.get("option_zh") or d.get("label_zh") or "是")
            g = moved.get(g, g)  # 这一组已经另起过：后面的勾选框都跟着新的一组
            if g not in groups:
                item = {
                    "id": uid(f"q_{g}"), "label_zh": str(d.get("group_label_zh") or d.get("label_zh") or opt),
                    "label_fr": str(d.get("group_label_orig") if "group_label_orig" in d else d.get("label_orig") or ""),  # 这一道题本身印的原文，不是第一个选项的
                    "pdf_key": None, "kind": "radio" if d.get("single") else "check", "fact": None,
                    "question_zh": str(d.get("question_zh") or d.get("group_label_zh") or opt), "help_zh": str(d.get("help_zh") or ""),
                    "required": bool(d.get("required")) and bool(d.get("single")), "options": {}, "option_labels_zh": {}, "max_chars": None,
                }
                groups[g] = item
                sec["items"].append(item)
            item = groups[g]
            if opt in item["option_labels_zh"].values() and len(set(item["option_labels_zh"].values())) > 1:
                # 同一组里又出现一样的选项（“1个月 / 3个月 …”印了两排）：是另一道题，另起一组，稍后归到它所在那一行的栏目
                base = g.split("#")[0]
                n = 2
                while f"{base}#{n}" in groups:
                    n += 1
                moved[base] = g = f"{base}#{n}"
                if g not in groups:
                    groups[g] = {**{k: v for k, v in item.items() if k not in ("options", "option_labels_zh", "_field_labels")},
                                 "id": uid(item["id"]), "options": {}, "option_labels_zh": {}, "_split": True}
                    sec["items"].append(groups[g])
                item = groups[g]
            ok = f"o{len(item['options']) + 1}"
            item["options"][ok] = f.key
            item["option_labels_zh"][ok] = opt
            item.setdefault("_field_labels", {})[ok] = str(d.get("label_zh") or opt)
            continue
        sec["items"].append({
            "id": uid("sign_date" if d.get("sign_date") else fact or f"t{i}"), "label_zh": str(d.get("label_zh") or "")[:60], "label_fr": str(d.get("label_orig") or "")[:120],
            "pdf_key": f.key, "kind": "text", "fact": fact, "question_zh": str(d.get("question_zh") or f"请填写：{d.get('label_zh', '')}"),
            "help_zh": EMAIL_HELP if f.segments else str(d.get("help_zh") or ""), "required": bool(d.get("required")), "options": None, "option_labels_zh": None,
            "max_chars": _max_chars(f),
        })
    _name_table_cells(fields, sections, by_i, uid)
    # 只有一个选项的“问题”是单独一个勾选框：是否勾选
    for item in groups.values():
        if item["kind"] == "radio" and len(item["options"]) == 1:
            item["kind"] = "check"
        # 每个勾选框的选项都叫同一个词（“已提交”）：选项改用各勾选框自己的栏目名（提交的是哪份文件）
        labels = list(item["option_labels_zh"].values())
        fl = item.get("_field_labels") or {}
        if len(labels) > 1 and len(set(labels)) == 1 and len(set(fl.values())) == len(fl):
            item["option_labels_zh"] = {k: fl[k].split("：")[-1] for k in item["options"]}
            # 题目本身也别再写“是否提交该文件”（原文标签只是第一份文件的）：问的是勾了哪几份
            item["label_zh"] = "随申请提交的" + item['label_zh'].removeprefix('是否').removeprefix('提交').replace("该", "") + "（勾选适用的）"
            item["label_fr"] = ""
            item["question_zh"] = "下面哪几项适用？请逐项勾选"
    sections = [s for s in sections if s["items"]]
    for key, lead in (spec.get("inline_after") or {}).items():  # 个别栏排到某道题的右边（选“其他”时的说明）
        for sec_ in sections:
            for it in sec_["items"]:
                if it.get("pdf_key") == key:
                    it["inline_after"] = lead
    # 题目和栏目按纸面顺序排（页、从上到下、从左到右）：位置编号是几轮找出来的，顺序是乱的。
    # 只排顺序，题目编号和选项编号不变（已存的答案照旧对得上）
    # 个别表（spec 里写 row_by_bottom）按底边排：同一行里文字格和勾选框高矮不同，按顶边排会让右边的文字格排到同一行的勾选题前面
    where = {f.key: (f.page, -round(f.bottom if spec.get("row_by_bottom") else f.top), f.left) for f in fields}

    def pos(item: dict) -> tuple:
        keys = [item["pdf_key"]] if item.get("pdf_key") else list((item.get("options") or {}).values())
        return min((where[k] for k in keys if k in where), default=(10**6, 0, 0))

    # 另起的那组勾选框：归到同一页、同一高度（相差 25pt 以内）那道文字题所在的栏目
    for s in sections:
        for item in [i for i in s["items"] if i.get("_split")]:
            pg, ny, _ = pos(item)
            near = [(abs(-pos(t)[1] + ny), s2) for s2 in sections for t in s2["items"]
                    if t.get("pdf_key") and pos(t)[0] == pg and abs(pos(t)[1] - ny) <= 25]
            if near:
                target = min(near, key=lambda x: x[0])[1]
                if target is not s:
                    s["items"].remove(item)
                    target["items"].append(item)
                    # 题目名跟着换成所在栏目（原名是模型照第一排起的，如“3.5.1 ……——保存期限”）
                    what = item["label_zh"].split("——")[-1]
                    inner = re.search(r"[（(]\s*(\d[\d.]*\s*[^（）()]+)[）)]\s*$", target["title_zh"])  # “3.5 ……（3.5.7 其他数据）”取括号里那一小节
                    where_ = inner[1].strip() if inner else SEC_TAIL.sub("", target["title_zh"]).strip() or target["title_zh"]
                    item["label_zh"] = f"{where_}——{what}"
                    item["question_zh"] = f"{where_}：{what}是？"
    for s in sections:
        for item in s["items"]:
            item.pop("_split", None)
            item.pop("_field_labels", None)
    sections = [s for s in sections if s["items"]]
    for s in sections:
        s["items"].sort(key=pos)
    sections.sort(key=lambda s: pos(s["items"][0]))  # 栏目编号也不改
    # 模型分批命名栏目，同一栏会出现“十、数据安全与保密 / 10. 数据安全与保密”“七、系统安装方 / 七、系统安装商”：
    # 按表格自己的栏目编号（没有编号就按去掉编号后的名称）合并成一栏，题目再按纸面排
    merged: list[dict] = []
    by_key: dict[str, dict] = {}
    for s in sections:
        key = _section_key(s["title_zh"])
        orig = "fr:" + re.sub(r"\s+", " ", s.get("title_fr") or "").strip().lower() if (s.get("title_fr") or "").strip() else ""
        into = (by_key.get(key) if key else None) or (by_key.get(orig) if orig else None)  # 中文译名不同、原文标题相同的也是同一栏
        if into is None:
            merged.append(s)
            for k in (key, orig):
                if k:
                    by_key[k] = s
            continue
        into["items"].extend(s["items"])
        into.setdefault("_titles", [into["title_zh"]]).append(s["title_zh"])
    for s in merged:
        titles = s.pop("_titles", None)
        if titles:
            s["title_zh"] = _merged_title(titles, _section_key(titles[0]))
            # 原文副标题同样去掉只属于其中一小块的部分（“2. Demandeur - Nature” → “2. Demandeur”）
            s["title_fr"] = re.sub(r"\s+[-–—]\s+[^-–—]+$", "", s.get("title_fr") or "").strip() or s.get("title_fr", "")
            s["items"].sort(key=pos)
    sections = merged
    return {**{k: v for k, v in meta.items() if v}, "sections": sections}


def _ti(item_id, zh, en, key, fact=None, question=None, help_="", required=True, max_chars=None):
    return {"id": item_id, "label_zh": zh, "label_fr": en, "pdf_key": key, "kind": "text", "fact": fact,
            "question_zh": question or f"请填写：{zh}", "help_zh": help_, "required": required, "options": None,
            "option_labels_zh": None, "max_chars": max_chars}


def imda_sdoc() -> dict:
    """IMDA 供应商符合性声明（Word 原件）。值写在说明行上方那一格，或接在“I/We”这类印刷字后面；
    位置对着原件第一张表逐行核过（表 0 第 2、4、6、10–12、14、18、20、22 行，表 1 第 1 行）。签名留给企业手签。"""
    src = SRC / "SG/IMDA_Suppliers_Declaration_of_Conformity_SDoC_2026-03.docx"
    data = src.read_bytes()
    file = "SG_IMDA_Suppliers_Declaration_of_Conformity_SDoC_2026-03.docx"
    shutil.copyfile(src, HERE / file)
    ts = []
    for n, row in enumerate((10, 11, 12), 1):
        ts += [
            _ti(f"ts{n}_ref", f"适用技术规范 {n}：IMDA 编号", "IMDA Reference", f"t0/r{row}/c0", required=n == 1, max_chars=30,
                question=f"适用的第 {n} 项 IMDA 技术规范编号是？", help_="见本表附表 D.1/D.2，车载蜂窝模块一般为 IMDA TS CMT，车载短距通信为 IMDA TS SRD" if n == 1 else ""),
            _ti(f"ts{n}_date", f"适用技术规范 {n}：发布日期", "Issue Date", f"t0/r{row}/c1", required=n == 1, max_chars=12),
            _ti(f"ts{n}_title", f"适用技术规范 {n}：名称", "Title of IMDA Technical Specification", f"t0/r{row}/c2", required=n == 1, max_chars=80),
        ]
    sections = [
        {"id": "S1", "title_zh": "一、声明人（新加坡本地供应商）", "title_fr": "Supplier’s Declaration of Conformity", "items": [
            _ti("local_rep_name", "本地供应商名称", "Name of Local Supplier", "t0/r2/c0/a", fact="local_rep_name",
                help_="须是持有 IMDA 电信经销商执照的新加坡本地供应商（车企在新加坡的子公司或经销商）"),
            _ti("local_rep_address", "本地供应商地址", "Address of Local Supplier", "t0/r4/c0/a", fact="local_rep_address"),
        ]},
        {"id": "S2", "title_zh": "二、设备与适用的技术规范", "title_fr": "Equipment and IMDA Technical Specifications", "items": [
            _ti("equipment_model", "设备商品名与型号", "Trade name and Model name", "t0/r6/c0/a", fact="equipment_model",
                help_="车载通信模块（T-Box）或其他无线终端的商品名与型号"),
            *ts,
            _ti("test_lab", "测试实验室或认证机构名称及地址", "Name & Address of Test Laboratory or Certification Body", "t0/r14/c0/a", max_chars=160),
        ]},
        {"id": "S3", "title_zh": "三、签署人", "title_fr": "Authorised Officer", "items": [
            _ti("sign_date", "声明日期", "Date of Declaration", "t0/r18/c2", required=False, max_chars=20),
            _ti("officer_name", "授权签署人姓名", "Name of Authorised Officer", "t0/r20/c0", fact="legal_rep_name"),
            _ti("officer_title", "授权签署人职务", "Position held in Organisation", "t0/r22/c0", fact="legal_rep_title"),
            _ti("dealer_licence", "电信经销商执照号", "Telecom Dealer’s Licence No.", "t0/r22/c2", max_chars=30),
        ]},
        {"id": "S4", "title_zh": "四、补充信息", "title_fr": "Additional Information", "items": [
            _ti("manufacturer_name", "制造商名称及地址", "Name & Address of Manufacturer", "t1/r1/c0", fact="manufacturer_name", required=False),
        ]},
    ]
    return {
        "code": "SG_IMDA_SDOC", "country": "SG", "direction": "equipment", "language": "en", "kind": "docx", "file": file,
        "name_zh": "新加坡：电信设备供应商符合性声明（IMDA SDoC）", "authority_zh": "新加坡资讯通信媒体发展局（IMDA）", "version": "2026-03",
        "source_url": "https://www.imda.gov.sg/regulations-and-licensing-listing/dealer-and-equipment-registration",
        "retrieved": "2026-11-08", "legal_basis_zh": "《电信法》（Telecommunications Act 1999）设备注册框架", "timing_zh": "",
        "prerequisite_zh": "随 IMDA 设备注册（简易注册类设备）在线提交", "matches_zh": r"^(IMDA)?电信设备注册$",
        "sections": sections, "controls_total": 16, "controls_mapped": 16,
        "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data),
    }


STATIC = [imda_sdoc]


async def build(only: str | None = None, redo: bool = False, reassemble: bool = False) -> dict:
    """``redo``: ask the model again; ``reassemble``: rebuild from the kept model output (no tokens)."""
    import app.models_registry  # noqa: F401  模型调用要写出站审计
    from app.core.rls import system_scope

    have = {f["code"]: f for f in json.loads(OUT.read_text(encoding="utf-8"))["forms"]} if OUT.exists() else {}
    stats: dict[str, Any] = {"built": [], "skipped": [], "tokens": 0}
    for make in STATIC:  # 按原件逐格核过的表（Word 表格），不调用模型
        form = make()
        if not only or only == form["code"]:
            have[form["code"]] = form
            stats["built"].append(f"{form['code']}：按原件栏位登记")
    for spec in SPECS:
        if only and spec["code"] != only:
            continue
        if spec["code"] in have and not redo and not reassemble:
            stats["skipped"].append(spec["code"])
            continue
        src = SRC / spec["src"]
        data = src.read_bytes()
        shutil.copyfile(src, HERE / spec["file"])
        fields = list_fields(data)
        kind = "pdf"
        if not fields:
            fields, kind = printed_spaces(data, tuple(spec.get("spaces") or ("leader", "glyph"))), "pdf_fixed"
        if not fields:
            stats["skipped"].append(spec["code"] + "（没有可填位置）")
            continue
        cache = HERE / "_auto_cache" / f"{spec['code']}.json"
        if cache.exists() and not redo:
            got = json.loads(cache.read_text(encoding="utf-8"))
            meta, described, tokens = got["meta"], got["described"], 0
            # 规则改进后补认出的空白（编号排在最后）还没有说明：只把这几处交给模型，已有的说明照旧用
            told = set()
            for d in described:
                try:
                    told.add(int(d.get("i")))
                except (TypeError, ValueError):
                    pass
            new = {i for i, f in enumerate(fields) if "_x" in f.key and i not in told}
            if new:
                with system_scope("forms-auto"):
                    _, more, tokens = await _describe(spec, data, fields, only=new)
                described = described + more
                cache.write_bytes(json.dumps({"meta": meta, "described": described}, ensure_ascii=False).encode("utf-8"))
        else:
            with system_scope("forms-auto"):
                meta, described, tokens = await _describe(spec, data, fields)
            cache.parent.mkdir(exist_ok=True)
            cache.write_bytes(json.dumps({"meta": meta, "described": described}, ensure_ascii=False).encode("utf-8"))
        stats["tokens"] += tokens
        body = _assemble(spec, fields, meta, described, official_zones(data), skip=lead_in_colons(data, fields))
        form = {
            **{k: v for k, v in spec.items() if k != "src"}, "kind": kind, "retrieved": "2026-11-08",
            "legal_basis_zh": body.pop("legal_basis_zh", "") or "", "timing_zh": body.pop("timing_zh", "") or "",
            "prerequisite_zh": spec.get("applies_when_zh", ""), **body,
            "controls_total": len(fields), "controls_mapped": sum(len(i["options"] or {}) or 1 for s in body["sections"] for i in s["items"]),
            "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data),
        }
        if kind == "pdf_fixed":
            keep = {i["pdf_key"] for s in body["sections"] for i in s["items"] if i["pdf_key"]} | {
                v for s in body["sections"] for i in s["items"] for v in (i["options"] or {}).values()}
            form["boxes"] = {f.key: {"kind": "check" if f.kind != "text" else "text", "page": f.page, "left": round(f.left, 2),
                                     "bottom": round(f.bottom, 2), "right": round(f.right, 2), "top": round(f.top, 2),
                                     **({"cells": f.cells} if f.cells else {}),
                                     **({"segments": [list(s) for s in f.segments]} if f.segments else {})}
                             for f in fields if f.key in keep}
        have[spec["code"]] = form
        stats["built"].append(f"{spec['code']}：{len(fields)} 个位置，收录 {form['controls_mapped']}，{tokens} tokens")
        OUT.write_bytes(json.dumps({"forms": list(have.values())}, ensure_ascii=False, indent=1).encode("utf-8"))  # 每份表做完即落盘
    return stats
