# ruff: noqa: E501
"""突尼斯国家个人数据保护局（INPDP）《境外传输事先授权申请表》的目录。

这份表没有表单控件，只有印刷的栏位；各栏位的坐标是对着渲染页逐项量出来的（单位：PDF 点，
原点在页面左下角），量法见 dev-tools/tn_form_crop.py。第 1 项“申请登记（局内填）”是 INPDP 自己填的，
不收录；“日期与地点”要手写签署，也不收录。"""

from __future__ import annotations

from typing import Any

PAGE1, PAGE2 = 0, 1
SQ = 3.75  # 方框复选框的半边长


def _box(page, left, bottom, right, top, **extra) -> dict[str, Any]:
    return {"kind": "text", "page": page, "left": left, "bottom": bottom, "right": right, "top": top, **extra}


def _check(page, cx, cy) -> dict[str, Any]:
    return {"kind": "check", "page": page, "left": cx - SQ, "bottom": cy - SQ, "right": cx + SQ, "top": cy + SQ}


def _item(item_id, zh, fr, pdf_key, kind="text", fact=None, question=None, help_="", required=True,
          options=None, option_labels=None, max_chars=None):
    return {
        "id": item_id, "label_zh": zh, "label_fr": fr, "pdf_key": pdf_key, "kind": kind, "fact": fact,
        "question_zh": question or f"请填写：{zh}", "help_zh": help_, "required": required,
        "options": options, "option_labels_zh": option_labels, "max_chars": max_chars,
    }


def _party(prefix, who_zh, who_fr):
    """申请人与受益人共有的一组栏目：身份、地址、邮编、城市、电话、邮箱。"""
    items = [
        _item(f"{prefix}_identite", f"{who_zh}：名称", f"{who_fr} — Identité", f"{prefix}_identite",
              fact="company_name" if prefix == "s2" else None, max_chars=90,
              question=f"{who_zh}的名称（公司全称）是什么？"),
        _item(f"{prefix}_adresse", f"{who_zh}：地址", f"{who_fr} — Adresse", f"{prefix}_adresse", max_chars=90,
              question=f"{who_zh}的地址是什么？"),
        _item(f"{prefix}_cp", f"{who_zh}：邮编", f"{who_fr} — Code postal", f"{prefix}_cp", required=False, max_chars=8,
              question=f"{who_zh}的邮政编码是？"),
        _item(f"{prefix}_ville", f"{who_zh}：城市", f"{who_fr} — Ville", f"{prefix}_ville", max_chars=40,
              question=f"{who_zh}所在城市是？"),
        _item(f"{prefix}_tel_fixe", f"{who_zh}：固定电话", f"{who_fr} — Tél. fixe", f"{prefix}_tel_fixe", required=False,
              max_chars=16, help_="表格是 8 格，请填 8 位号码（突尼斯号码），区号 +216 可省略"),
        _item(f"{prefix}_tel_port", f"{who_zh}：手机", f"{who_fr} — Tél. portable", f"{prefix}_tel_port", required=False,
              max_chars=16, help_="表格是 8 格，请填 8 位号码，区号 +216 可省略"),
        _item(f"{prefix}_email", f"{who_zh}：电子邮箱", f"{who_fr} — Adresse électronique", f"{prefix}_email", max_chars=80,
              help_="完整邮箱，如 name@example.com；表格里 @ 前后各一段，系统自动拆开"),
    ]
    return items


def build() -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    boxes: dict[str, dict[str, Any]] = {}

    # ---- 2 申请人（在突尼斯处理数据、要把数据传出去的一方）
    for key, cx in zip(("physique", "pers_pub", "societe", "association", "parti"),
                       (137.3, 225.4, 332.4, 426.5, 496.0), strict=True):
        boxes[f"s2_nature_{key}"] = _check(PAGE1, cx, 447.3)
    boxes["s2_identite"] = _box(PAGE1, 99, 424, 508, 435)
    boxes["s2_adresse"] = _box(PAGE1, 99, 406.5, 508, 417.5)
    boxes["s2_cp"] = _box(PAGE1, 126, 387, 177, 397)
    boxes["s2_ville"] = _box(PAGE1, 357, 389.5, 507, 399.5)
    boxes["s2_tel_fixe"] = _box(PAGE1, 84.3, 370.3, 224.3, 380.5, cells=8)
    boxes["s2_tel_port"] = _box(PAGE1, 364.9, 372.5, 504.9, 382.5, cells=8)
    boxes["s2_email"] = _box(PAGE1, 163, 351, 464, 361, segments=[[163, 395], [408, 464]])
    boxes["s2_contact"] = _box(PAGE1, 160, 334.6, 467, 345)
    boxes["s2_gsm"] = _box(PAGE1, 75.1, 316.8, 215.1, 327, cells=8)
    boxes["s2_email2"] = _box(PAGE1, 303, 313.3, 476, 323.5, segments=[[303, 402], [415, 476]])
    # ---- 3 传输目的（三条横线）
    boxes["s3_finalite"] = _box(PAGE1, 57, 222, 532, 268, rules=[257.3, 240.0, 222.1])
    # ---- 4 受益人（境外接收方）
    for key, cx in zip(("maison_mere", "filiale", "sous_traitant", "partenaire"), (164.3, 265.7, 379.7, 480.0), strict=True):
        boxes[f"s4_nature_{key}"] = _check(PAGE1, cx, 175.1)
    boxes["s4_identite"] = _box(PAGE1, 97, 150.3, 506, 161)
    boxes["s4_adresse"] = _box(PAGE1, 97, 132.3, 506, 143)
    boxes["s4_cp"] = _box(PAGE1, 124, 112.5, 175, 123)
    boxes["s4_ville"] = _box(PAGE1, 356, 115.2, 507, 125.5)
    boxes["s4_tel_fixe"] = _box(PAGE1, 82, 95.7, 221.6, 106, cells=8)
    boxes["s4_tel_port"] = _box(PAGE1, 362.7, 98.0, 502.7, 108, cells=8)
    boxes["s4_email"] = _box(PAGE1, 162, 77.3, 465, 87.5, segments=[[162, 395], [408, 465]])
    boxes["s4_pays"] = _box(PAGE1, 157, 60.6, 465, 71)
    # ---- 5 处理性质（两条横线）
    boxes["s5_nature"] = _box(PAGE2, 64, 738, 536, 771, rules=[760.8, 738.4])
    # ---- 6 传输的数据类别（左右两列）
    left_rows = (("identification", 683.2), ("etat_civil", 667.0), ("numeros_id", 651.1), ("genetiques", 634.9),
                 ("difficultes_sociales", 615.7), ("prelevements_biologiques", 596.5), ("origines", 577.6))
    right_rows = (("opinions", 689.5), ("vie_personnelle", 670.3), ("situation_eco", 654.3), ("localisation", 634.9),
                  ("biometriques", 615.7), ("judiciaires", 596.8), ("sante", 577.6))
    for key, cy in left_rows:
        boxes[f"s6_{key}"] = _check(PAGE2, 193.8, cy)
    for key, cy in right_rows:
        boxes[f"s6_{key}"] = _check(PAGE2, 464.3, cy)
    # ---- 7 保障措施
    for key, cy in (("contrat_resp", 519.2), ("contrat_st", 500.3), ("regles", 481.1), ("autres", 462.2)):
        boxes[f"s7_{key}"] = _check(PAGE2, 315.4, cy)
    boxes["s7_autres_texte"] = _box(PAGE2, 64, 445, 536, 456)
    # ---- 8 承诺：签署人姓名与职务（日期与地点留给手写）
    boxes["s8_nom"] = _box(PAGE2, 196, 183.5, 442, 194)

    categories = {
        "identification": "身份信息", "etat_civil": "民事状况", "numeros_id": "社保、医保、身份证号码", "genetiques": "基因数据",
        "difficultes_sociales": "社会困难情况", "prelevements_biologiques": "生物样本", "origines": "社会出身或种族出身",
        "opinions": "观点、信仰、性取向", "vie_personnelle": "私生活", "situation_eco": "经济状况", "localisation": "位置数据",
        "biometriques": "生物识别数据", "judiciaires": "犯罪记录", "sante": "健康数据",
    }
    cat_items = {k: f"s6_{k}" for k in categories}
    sections = [
        {"id": "II", "title_zh": "二、申请人（在突尼斯处理数据并要传出的一方）", "title_fr": "2. Demandeur", "items": [
            _item("s2_nature", "申请人：性质", "Nature", "s2_nature", "radio", required=True,
                  options={k: f"s2_nature_{k}" for k in ("physique", "pers_pub", "societe", "association", "parti")},
                  option_labels={"physique": "自然人", "pers_pub": "公共机构", "societe": "公司", "association": "协会", "parti": "政党"},
                  question="申请人是自然人、公共机构、公司、协会还是政党？"),
            *_party("s2", "申请人", "Demandeur"),
            _item("s2_contact", "联系人", "Personne contact", "s2_contact", max_chars=80, question="联系人姓名是？"),
            _item("s2_gsm", "联系人手机", "G.S.M.", "s2_gsm", required=False, max_chars=16, help_="8 位号码，区号 +216 可省略"),
            _item("s2_email2", "联系人邮箱", "E-mail", "s2_email2", required=False, max_chars=80, help_="完整邮箱，如 name@example.com"),
        ]},
        {"id": "III", "title_zh": "三、传输数据的目的", "title_fr": "3. Finalité du transfert de données", "items": [
            _item("s3_finalite", "传输目的", "Finalité du transfert", "s3_finalite", fact="purpose", max_chars=300,
                  question="传输数据出境的目的是什么？（用法语或阿拉伯语，三行内写完）",
                  help_="表格只有三条横线，请写得简明"),
        ]},
        {"id": "IV", "title_zh": "四、受益人（境外接收方）", "title_fr": "4. Bénéficiaire", "items": [
            _item("s4_nature", "受益人：与申请人的关系", "Nature", "s4_nature", "radio", required=True,
                  options={k: f"s4_nature_{k}" for k in ("maison_mere", "filiale", "sous_traitant", "partenaire")},
                  option_labels={"maison_mere": "母公司", "filiale": "分支机构", "sous_traitant": "分包商", "partenaire": "合作伙伴"},
                  question="境外接收方是申请人的母公司、分支机构、分包商还是合作伙伴？"),
            *_party("s4", "受益人", "Bénéficiaire"),
            _item("s4_pays", "受益人所在国家（目的国）", "Pays destinataire", "s4_pays", max_chars=60,
                  question="数据传到哪个国家？（用法语或阿拉伯语写国家名）"),
        ]},
        {"id": "V", "title_zh": "五、处理的性质", "title_fr": "5. Nature du traitement opéré", "items": [
            _item("s5_nature", "处理的性质", "Nature du traitement opéré", "s5_nature", max_chars=200,
                  question="对数据做哪些处理？（如采集、存储、分析、传输；两行内写完）"),
        ]},
        {"id": "VI", "title_zh": "六、传输的数据类别", "title_fr": "6. Catégorie de données transférées", "items": [
            _item("s6_categories", "传输的数据类别", "Catégories", "s6_categories", "check", required=True,
                  options=cat_items, option_labels=categories,
                  question="传输的数据包含哪些类别？（可多选）",
                  help_="位置数据、生物识别数据、健康数据等属于敏感类别，请据实勾选"),
        ]},
        {"id": "VII", "title_zh": "七、已采取的保障措施", "title_fr": "7. Garanties mises en œuvre", "items": [
            _item("s7_garanties", "已采取的保障措施", "Garanties", "s7_garanties", "check", required=True,
                  options={"contrat_resp": "s7_contrat_resp", "contrat_st": "s7_contrat_st", "regles": "s7_regles", "autres": "s7_autres"},
                  option_labels={"contrat_resp": "与数据处理责任人签有合同", "contrat_st": "与分包商签有合同", "regles": "内部规则", "autres": "其他"},
                  question="采取了哪些保障措施？（可多选）"),
            _item("s7_autres_texte", "其他保障措施说明", "Autres", "s7_autres_texte", required=False, max_chars=120,
                  question="如选了“其他”，请说明是什么措施。"),
        ]},
        {"id": "VIII", "title_zh": "八、承诺与声明", "title_fr": "8. Engagement et déclaration sur l'honneur", "items": [
            _item("s8_nom", "签署人姓名与职务", "Nom & prénom & qualité", "s8_nom", max_chars=80,
                  question="签署声明的人的姓名与职务是？",
                  help_="日期与地点留空，打印后手写；声明要求处理责任人具突尼斯国籍、无犯罪记录，请先确认签署人符合"),
        ]},
    ]
    form = {
        "code": "TN_INPDP_TRANSFERT", "country": "TN", "direction": "export",
        "name_zh": "突尼斯：境外传输个人数据事先授权申请（INPDP）",
        "authority_zh": "突尼斯国家个人数据保护局（INPDP）", "language": "fr", "kind": "pdf_fixed",
        "file": "TN_INPDP_demande-autorisation-transfert_MJS.pdf", "version": "司法部网站存档版",
        "source_url": "https://www.mjs.tn/images/pdf/archive/3.pdf", "retrieved": "2026-11-04",
        "legal_basis_zh": "2004 年第 63 号组织法第 51、52 条；2007 年第 3004 号法令第 11 条",
        "prerequisite_zh": "把个人数据传到境外前须取得 INPDP 事先授权；表格第 8 项声明要求处理责任人具突尼斯国籍且无犯罪记录，非突尼斯籍主体需先与 INPDP 确认由谁申请",
        "timing_zh": "事先授权，取得前不得传输；INPDP 答复期限以其通知为准（官方表格未写明）",
        "sections": sections, "boxes": boxes,
    }
    return form, boxes
