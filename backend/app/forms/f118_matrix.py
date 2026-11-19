# ruff: noqa: E501
"""摩洛哥 CNDP 表格里按数据类别填的大表：F118 第五部分“接收方处理的特征”（第 5–7 页，32 行），以及
F211（普通申报）、F112（事先授权）第五部分“数据类别”（同样 32 行；没有“处理目的”列，多一列“内部可访问的部门”）。

每一行是一类数据（姓名、定位、生物识别……），行里依次是：该类数据涉及的人群（8 个勾选）、数据来源（2 个勾选）、
接收方类别（6 个勾选）、处理目的（一个文本框）、保存期限（一个文本框），另有三处“其他”自由文本。官方 PDF 里这些控件
的名字不可靠（例如“Service de santé”实际放在“分支机构”的位置），所以这里一律**按版面位置**给每个控件定位，并逐行断言
每行的控件数与结构一致；位置对不上就报错，不静默放过。

页 7 有三行的“勾选框”在原 PDF 里被做成了文本框（官方文件本身的瑕疵），填写时在框里写“X”。
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

PERSONS = ["clients", "prospects", "fournisseurs", "patients", "etudiants", "abonnes", "employes", "autres"]
PERSON_LABELS = {
    "clients": "客户", "prospects": "潜在客户", "fournisseurs": "供应商", "patients": "患者",
    "etudiants": "学生", "abonnes": "订阅用户", "employes": "员工", "autres": "其他人群",
}
ORIGINS = ["personne", "autre"]
ORIGIN_LABELS = {"personne": "数据主体本人提供", "autre": "其他来源"}
RECIPIENTS = ["soustraitant", "administration", "succursales", "financiers", "sante", "autres"]
ACCESS = ["it", "commercial", "daf", "achat", "rh", "autre"]
ACCESS_LABELS = {"it": "信息技术部门", "commercial": "商务部门", "daf": "行政财务部门", "achat": "采购部门", "rh": "人力资源部门", "autre": "其他部门"}
RECIPIENT_LABELS = {
    "soustraitant": "分包商", "administration": "行政机关", "succursales": "分支机构",
    "financiers": "金融机构", "sante": "医疗服务机构", "autres": "其他接收方",
}

# (页, 行 id, 中文名, 法文名, 组, 该行是否有“其他”自由类别名)
ROWS: list[tuple[int, str, str, str, str, bool]] = [
    (4, "nom", "姓名", "Nom et prénom", "个人身份识别", False),
    (4, "adresse", "地址", "Adresse", "个人身份识别", False),
    (4, "naissance", "出生日期与地点", "Date et lieu de naissance", "个人身份识别", False),
    (4, "email", "电子邮箱", "E-mail", "个人身份识别", False),
    (4, "tel", "电话号码", "N° Tél", "个人身份识别", False),
    (4, "cni", "身份证号码", "N° CNI", "个人身份识别", False),
    (4, "photo", "照片、影像", "Photo", "个人身份识别", False),
    (4, "id_autres", "身份识别：其他", "Autres (identification)", "个人身份识别", True),
    (4, "consommation", "消费习惯", "Habitudes de consommation", "行为", False),
    (4, "localisation", "地理位置、行车轨迹", "Localisation géographique", "行为", False),
    (4, "mode_vie", "生活方式", "Mode de vie", "行为", False),
    (4, "loisirs", "休闲爱好", "Loisirs", "行为", False),
    (5, "comp_autres", "行为：其他", "Autres (comportement)", "行为", True),
    (5, "cv", "简历", "CV", "职业信息", False),
    (5, "formation", "教育与学历", "Formation - Diplômes", "职业信息", False),
    (5, "experience", "工作经历", "Expérience", "职业信息", False),
    (5, "pro_autres", "职业信息：其他", "Autres (données professionnelles)", "职业信息", True),
    (5, "bancaire", "银行信息", "Informations bancaires", "财务状况", False),
    (5, "revenus", "收入", "Revenus", "财务状况", False),
    (5, "dettes", "债务", "Dettes", "财务状况", False),
    (5, "carte", "银行卡号", "N° Carte bancaire", "财务状况", False),
    (5, "fin_autres", "财务状况：其他", "Autres (situation financière)", "财务状况", True),
    (6, "origine", "种族或民族出身", "Origine raciale ou ethnique", "敏感数据", False),
    (6, "politique", "政治观点", "Opinions politiques", "敏感数据", False),
    (6, "religion", "宗教或哲学信仰", "Convictions religieuses ou philosophiques", "敏感数据", False),
    (6, "syndical", "工会归属", "Appartenance syndicale", "敏感数据", False),
    (6, "sante", "健康数据", "Données de santé", "敏感数据", False),
    (6, "genetique", "基因数据", "Données génétiques", "敏感数据", False),
    (6, "infractions", "违法与定罪记录", "Données relatives aux infractions et condamnations", "敏感数据", False),
    (6, "surete", "安全措施记录", "Données relatives aux mesures de sûreté", "敏感数据", False),
    (6, "biometrie", "生物识别数据", "Données biométriques", "敏感数据", False),
    (6, "sens_autres", "敏感数据：其他", "Autres (données sensibles)", "敏感数据", True),
]


def _lines(items: list, tol: float = 4.0) -> list[list]:
    """Group controls into visual lines (top to bottom), each line left to right."""
    out: list[list] = []
    for f in sorted(items, key=lambda f: -((f.top + f.bottom) / 2)):
        centre = (f.top + f.bottom) / 2
        if out and abs(out[-1][0] - centre) <= tol:
            out[-1][1].append(f)
        else:
            out.append([centre, [f]])
    return [sorted(line, key=lambda f: f.left) for _, line in out]


def build(fields: list) -> tuple[dict[str, Any], set[str]]:
    """The matrix item (rows with the key of every control) and the set of keys it uses."""
    used: set[str] = set()
    pseudo: set[str] = set()
    rows_out: list[dict[str, Any]] = []
    by_page: dict[int, list] = defaultdict(list)
    for r in ROWS:
        by_page[r[0]].append(r)
    for page, defs in by_page.items():
        fs = [f for f in fields if f.page == page]
        cats = sorted([f for f in fs if f.left < 80 and f.right < 90], key=lambda f: -f.top)
        if len(cats) != len(defs):
            raise AssertionError(f"第 {page + 1} 页的类别控件 {len(cats)} 个，与预期的 {len(defs)} 行不符")
        mids = [(c.top + c.bottom) / 2 for c in cats]
        bounds = [1e9] + [(mids[i] + mids[i + 1]) / 2 for i in range(len(mids) - 1)] + [-1e9]
        for i, (_, rid, zh, fr, group, free) in enumerate(defs):
            band = [
                f for f in fs
                if bounds[i + 1] <= (f.top + f.bottom) / 2 < bounds[i] and f is not cats[i] and f.left >= 80
            ]
            # 官方 PDF 里有些勾选框被做成了只有 7–9 磅宽的文本框：当作勾选框，填写时写“X”
            checks = [f for f in band if f.kind == "check" or (f.kind == "text" and f.width < 12)]
            texts = [f for f in band if f.kind == "text" and f.width >= 12]
            pseudo.update(f.key for f in checks if f.kind == "text")
            persons = [f for f in checks if 140 < f.left < 260]
            origin = [f for f in checks if 290 < f.left < 312]
            recip = [f for f in checks if 385 < f.left < 470]
            if (len(persons), len(origin), len(recip)) != (8, 2, 6):
                raise AssertionError(f"第 {page + 1} 页“{fr}”行勾选框数 {len(persons)}/{len(origin)}/{len(recip)}，应为 8/2/6")
            p_lines = _lines(persons)
            if [len(line) for line in p_lines] != [3, 3, 2]:
                raise AssertionError(f"“{fr}”行的人群勾选框排布不是 3+3+2：{[len(x) for x in p_lines]}")
            r_lines = _lines(recip)
            if [len(line) for line in r_lines] != [2, 2, 2]:
                raise AssertionError(f"“{fr}”行的接收方勾选框排布不是 2+2+2：{[len(x) for x in r_lines]}")
            person_keys = {pid: f.key for pid, f in zip(PERSONS, [f for line in p_lines for f in line], strict=True)}
            recip_keys = {rid_: f.key for rid_, f in zip(RECIPIENTS, [f for line in r_lines for f in line], strict=True)}
            origin_sorted = sorted(origin, key=lambda f: -f.top)
            origin_keys = {"personne": origin_sorted[0].key, "autre": origin_sorted[1].key}
            persons_text = [f for f in texts if 225 < f.left < 295]
            origin_text = sorted([f for f in texts if 300 < f.left < 330], key=lambda f: -f.top)
            recip_text = sorted([f for f in texts if 380 < f.left < 420], key=lambda f: f.top)
            recip_text2 = [f for f in texts if 470 < f.left < 540]  # “Autres……”后面那一小段
            purpose = [f for f in texts if 540 < f.left < 600]
            duration = [f for f in texts if f.left > 690]
            label_text = [f for f in texts if f.left < 120]
            if free and len(label_text) != 1:
                raise AssertionError(f"“{fr}”行应有 1 个“其他类别名称”文本框，实际 {len(label_text)}")
            if not (len(persons_text) == 1 and len(origin_text) >= 1 and len(recip_text) >= 1 and len(purpose) == 1 and len(duration) == 1):
                raise AssertionError(
                    f"第 {page + 1} 页“{fr}”行文本框数不符：人群其他{len(persons_text)} 来源其他{len(origin_text)} 接收方其他{len(recip_text)} 目的{len(purpose)} 期限{len(duration)}"
                )
            row = {
                "id": rid, "label_zh": zh, "label_fr": fr, "group_zh": group,
                "cat": cats[i].key,
                "persons": person_keys, "persons_other": persons_text[0].key,
                "origin": origin_keys, "origin_other": origin_text[0].key,
                "origin_other2": origin_text[1].key if len(origin_text) > 1 else None,
                # 接收方“Autres……”后面是一小段，下面还有一整行：小段写开头，其余写在下面整行
                "recipients": recip_keys, "recipients_other": (recip_text2 or recip_text)[0].key,
                "recipients_other2": recip_text[0].key if recip_text2 else None,
                "purpose": purpose[0].key, "duration": duration[0].key,
                "label_other": label_text[0].key if free else None,
            }
            for k in ("cat", "persons_other", "origin_other", "origin_other2", "recipients_other", "recipients_other2", "purpose", "duration", "label_other"):
                if row[k]:
                    used.add(row[k])
            for group_keys in (person_keys, origin_keys, recip_keys):
                used.update(group_keys.values())
            rows_out.append(row)
    keys = [k for r in rows_out for k in _row_keys(r)]
    if len(keys) != len(set(keys)):
        raise AssertionError("同一个控件被两处使用")
    pseudo.update(c.key for c in [f for f in fields if f.page in (4, 5, 6) and f.left < 80 and f.right < 90 and f.kind == "text"])
    return {
        "rows": rows_out,
        "text_marks": sorted(pseudo),
        "person_labels": PERSON_LABELS, "origin_labels": ORIGIN_LABELS, "recipient_labels": RECIPIENT_LABELS,
    }, used


def _row_keys(row: dict) -> list[str]:
    keys = [row["cat"], row["duration"]]
    for name in ("persons_other", "origin_other", "origin_other2", "recipients_other", "recipients_other2", "access_other", "purpose", "label_other"):
        if row.get(name):
            keys.append(row[name])
    for group in ("persons", "origin", "recipients", "access"):
        keys.extend((row.get(group) or {}).values())
    return keys


def build_declaration(fields: list, first_page: int) -> tuple[dict[str, Any], set[str]]:
    """F211 / F112: the data-category table on pages ``first_page``…+2 (0-based). Every control is
    located by its position on the page and every row is checked against the expected layout; a
    layout that does not match raises instead of filling the wrong boxes."""
    used: set[str] = set()
    pseudo: set[str] = set()
    rows_out: list[dict[str, Any]] = []
    by_page: dict[int, list] = defaultdict(list)
    for r in ROWS:
        by_page[r[0] - 4 + first_page].append(r)  # ROWS 记的是 F118 的页码（第 4–6 页，从 0 起）
    for page, defs in by_page.items():
        fs = [f for f in fields if f.page == page]
        cats = sorted([f for f in fs if f.left < 120 and f.right < 130 and (f.kind == "check" or (f.kind == "text" and f.width < 12))], key=lambda f: -f.top)
        pseudo.update(c.key for c in cats if c.kind == "text")  # 被做成文本框的勾选框：写“X”
        if len(cats) != len(defs):
            raise AssertionError(f"第 {page + 1} 页的类别控件 {len(cats)} 个，与预期的 {len(defs)} 行不符")
        mids = [(c.top + c.bottom) / 2 for c in cats]
        bounds = [1e9] + [(mids[i] + mids[i + 1]) / 2 for i in range(len(mids) - 1)] + [-1e9]
        for i, (_, rid, zh, fr, group, free) in enumerate(defs):
            band = [f for f in fs if bounds[i + 1] <= (f.top + f.bottom) / 2 < bounds[i] and f is not cats[i]]
            checks = [f for f in band if f.kind == "check" or (f.kind == "text" and f.width < 12)]
            texts = [f for f in band if f.kind == "text" and f.width >= 12]
            pseudo.update(f.key for f in checks if f.kind == "text")
            persons = [f for f in checks if 180 < f.left < 300]
            origin = [f for f in checks if 320 < f.left < 350]
            recip = [f for f in checks if 440 < f.left < 520]
            access = [f for f in checks if 595 < f.left < 650]
            if (len(persons), len(origin), len(recip), len(access)) != (8, 2, 6, 6):
                raise AssertionError(f"第 {page + 1} 页“{fr}”行勾选框数 {len(persons)}/{len(origin)}/{len(recip)}/{len(access)}，应为 8/2/6/6")
            p_lines, r_lines, a_lines = _lines(persons), _lines(recip), _lines(access)
            if [len(x) for x in p_lines] != [3, 3, 2] or [len(x) for x in r_lines] != [2, 2, 2] or [len(x) for x in a_lines] != [2, 2, 2]:
                raise AssertionError(f"“{fr}”行的勾选框排布不符：{[len(x) for x in p_lines]} {[len(x) for x in r_lines]} {[len(x) for x in a_lines]}")
            persons_text = [f for f in texts if 260 < f.left < 330]
            origin_text = sorted([f for f in texts if 330 <= f.left < 420], key=lambda f: -f.top)
            recip_text = sorted([f for f in texts if 445 < f.left < 600], key=lambda f: -f.top)
            access_text = [f for f in texts if 600 <= f.left < 685]
            duration = [f for f in texts if f.left >= 685]
            label_text = [f for f in texts if 110 < f.left < 185]
            if free and len(label_text) != 1:
                raise AssertionError(f"“{fr}”行应有 1 个“其他类别名称”文本框，实际 {len(label_text)}")
            if not (len(persons_text) == 1 and 1 <= len(origin_text) <= 2 and 1 <= len(recip_text) <= 2 and len(access_text) == 1 and len(duration) == 1):
                raise AssertionError(
                    f"第 {page + 1} 页“{fr}”行文本框数不符：人群其他{len(persons_text)} 来源其他{len(origin_text)} 接收方其他{len(recip_text)} 部门其他{len(access_text)} 期限{len(duration)}"
                )
            row = {
                "id": rid, "label_zh": zh, "label_fr": fr, "group_zh": group, "cat": cats[i].key,
                "persons": {k: f.key for k, f in zip(PERSONS, [f for line in p_lines for f in line], strict=True)},
                "persons_other": persons_text[0].key,
                "origin": {"personne": sorted(origin, key=lambda f: -f.top)[0].key, "autre": sorted(origin, key=lambda f: -f.top)[1].key},
                "origin_other": origin_text[0].key, "origin_other2": origin_text[1].key if len(origin_text) > 1 else None,
                "recipients": {k: f.key for k, f in zip(RECIPIENTS, [f for line in r_lines for f in line], strict=True)},
                "recipients_other": recip_text[0].key, "recipients_other2": recip_text[1].key if len(recip_text) > 1 else None,
                "access": {k: f.key for k, f in zip(ACCESS, [f for line in a_lines for f in line], strict=True)},
                "access_other": access_text[0].key,
                "purpose": None, "duration": duration[0].key,
                "label_other": label_text[0].key if free else None,
            }
            used.update(_row_keys(row))
            rows_out.append(row)
    keys = [k for r in rows_out for k in _row_keys(r)]
    if len(keys) != len(set(keys)):
        raise AssertionError("同一个控件被两处使用")
    return {
        "rows": rows_out, "text_marks": sorted(pseudo), "columns": ["persons", "origin", "recipients", "access", "duration"],
        "person_labels": PERSON_LABELS, "origin_labels": ORIGIN_LABELS, "recipient_labels": RECIPIENT_LABELS, "access_labels": ACCESS_LABELS,
    }, used


def clean(matrix: dict, raw: Any) -> dict[str, Any]:
    """Keep only rows the form has and choices it offers; texts are shortened to the box."""
    if not isinstance(raw, dict):
        return {}
    known = {r["id"]: r for r in matrix["rows"]}
    out: dict[str, Any] = {}
    for rid, value in raw.items():
        row = known.get(rid)
        if row is None or not isinstance(value, dict) or not value.get("on"):
            continue

        # 每格的格子上限（row["limits"]）只用于页面输入框；保存按原来的上限留存，放不下的导出时报出来
        def pick(group: str, options: dict, value=value) -> list[str]:
            given = value.get(group) or []
            return [o for o in options if o in given] if isinstance(given, list) else []

        def text(name: str, limit: int, value=value) -> str:
            return " ".join(str(value.get(name) or "").split())[:limit]

        out[rid] = {
            "on": True,
            "persons": pick("persons", row["persons"]), "persons_other": text("persons_other", 40),
            "origin": pick("origin", row["origin"]), "origin_other": text("origin_other", 40),
            "recipients": pick("recipients", row["recipients"]), "recipients_other": text("recipients_other", 80),
            "access": pick("access", row.get("access") or {}), "access_other": text("access_other", 40) if row.get("access_other") else "",
            "purpose": text("purpose", 220) if row.get("purpose") else "", "duration": text("duration", 30),
            "label_other": text("label_other", 40) if row.get("label_other") else "",
        }
    return out


def apply(matrix: dict, answers: dict[str, Any]) -> tuple[dict[str, str], set[str], dict[str, dict]]:
    """(texts, checked keys, key → description for the too-long report) for the rows turned on."""
    texts: dict[str, str] = {}
    checked: set[str] = set()
    described: dict[str, dict] = {}
    text_marks = set(matrix.get("text_marks", []))
    for row in matrix["rows"]:
        v = answers.get(row["id"])
        if not v or not v.get("on"):
            continue
        marks = [row["cat"]]
        marks += [row["persons"][p] for p in v["persons"]]
        marks += [row["origin"][o] for o in v["origin"]]
        marks += [row["recipients"][r] for r in v["recipients"]]
        marks += [row["access"][a] for a in v.get("access") or [] if row.get("access")]
        for key in marks:
            if key in text_marks:  # 原 PDF 把这个勾选框做成了文本框：在框里写“X”
                texts[key] = "X"
            else:
                checked.add(key)
        for name, key in (("persons_other", "persons_other"), ("origin_other", "origin_other"), ("recipients_other", "recipients_other"),
                          ("access_other", "access_other"), ("purpose", "purpose"), ("duration", "duration"), ("label_other", "label_other")):
            if v.get(name) and row.get(key):
                # “其他来源”“其他接收方”印了两行：第一行写不下的接着写到续行
                first, rest = split_line(v[name], LINE_CHARS) if row.get(key + "2") else (v[name], "")
                texts[row[key]] = first
                described[row[key]] = {"id": f"matrix/{row['id']}", "label_zh": f"{row['label_zh']}：{_FIELD_ZH[name]}", "max_chars": None}
                if rest:
                    texts[row[key + "2"]] = rest
                    described[row[key + "2"]] = described[row[key]]
    return texts, checked, described


LINE_CHARS = 16  # “Autre……”后面那一小段大约能写下的字数
DEFAULT_LIMITS = {"persons_other": 40, "origin_other": 40, "recipients_other": 80, "access_other": 40, "purpose": 220, "duration": 30, "label_other": 40}


def row_limits(row: dict, fields: dict) -> dict[str, int]:
    """Per row, the characters each free-text box takes (with its continuation line), at most the defaults."""
    from app.forms.pdf_fill import max_chars_for

    out = {}
    for name, cap in DEFAULT_LIMITS.items():
        f = fields.get(row.get(name) or "")
        if f is None:
            continue
        room = max_chars_for(f)
        f2 = fields.get(row.get(name + "2") or "")
        if f2 is not None:
            room = min(room, LINE_CHARS) + max_chars_for(f2)  # 第一段只写到 LINE_CHARS 就换到续行
        out[name] = min(cap, room)
    return out


def split_line(text: str, n: int) -> tuple[str, str]:
    """Split ``text`` into a short first line and a continuation line, at a space where possible."""
    text = " ".join(str(text).split())
    if len(text) <= n:
        return text, ""
    cut = text.rfind(" ", 0, n + 1)
    cut = cut if cut > n // 2 else n
    return text[:cut].rstrip(), text[cut:].lstrip()


_FIELD_ZH = {
    "persons_other": "其他人群", "origin_other": "其他来源", "recipients_other": "其他接收方", "access_other": "其他部门",
    "purpose": "处理目的", "duration": "保存期限", "label_other": "其他类别名称",
}


def public(matrix: dict) -> dict[str, Any]:
    """What the page needs: row labels and option labels, never the form's control keys."""
    return {
        "rows": [
            {"id": r["id"], "label_zh": r["label_zh"], "label_fr": r["label_fr"], "group_zh": r["group_zh"], "free_label": bool(r.get("label_other")),
             "limits": {**DEFAULT_LIMITS, **{k: min(v, DEFAULT_LIMITS.get(k, v)) for k, v in (r.get("limits") or {}).items()}}}
            for r in matrix["rows"]
        ],
        "persons": [{"id": k, "label": PERSON_LABELS[k]} for k in PERSONS],
        "origin": [{"id": k, "label": ORIGIN_LABELS[k]} for k in ORIGINS],
        "recipients": [{"id": k, "label": RECIPIENT_LABELS[k]} for k in RECIPIENTS],
        "access": [{"id": k, "label": ACCESS_LABELS[k]} for k in ACCESS] if matrix.get("columns") and "access" in matrix["columns"] else [],
        "purpose": not matrix.get("columns") or "purpose" in matrix["columns"],
    }
