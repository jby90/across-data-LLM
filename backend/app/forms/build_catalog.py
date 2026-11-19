# ruff: noqa: E501
"""Builds ``official/forms.json`` from the official files: what each control means, in Chinese for
the user and in the form's own language, where the value comes from, and what to ask when the
assessment does not hold it. The file is data; run this script when an official form changes.

    python -m app.forms.build_catalog

A form's control names are the publisher's and can be misleading (Morocco's F118 names the second
identity block "Contact Principal" although it is the block "Personne communiquant les données"),
so every mapping below was checked against the rendered page, not against the names."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent / "official"

PERSON = [  # (suffix in key, label_zh, label_fr, kind, optional)
    ("nom", "姓", "Nom", "text"),
    ("prenom", "名", "Prénom", "text"),
    ("qualite", "职务", "Qualité", "text"),
    ("id_type", "证件类型", "Pièce d'identité", "radio"),
    ("id_autre", "其他证件名称", "Autre", "text"),
    ("id_num", "证件号码", "N° Pièce d'Identité", "text"),
    ("nationalite", "国籍", "Nationalité", "text"),
    ("email", "电子邮箱", "E-mail", "text"),
    ("tel", "固定电话", "Tél (Fixe)", "text"),
    ("gsm", "手机", "GSM", "text"),
    ("fax", "传真", "Fax", "text"),
]


def item(item_id, label_zh, label_fr, pdf_key, kind="text", fact=None, question=None, help_=None,
         required=True, options=None, max_chars=None, option_labels=None):
    return {
        "option_labels_zh": option_labels,
        "id": item_id, "label_zh": label_zh, "label_fr": label_fr, "pdf_key": pdf_key, "kind": kind,
        "fact": fact, "question_zh": question or f"请填写：{label_zh}", "help_zh": help_ or "",
        "required": required, "options": options, "max_chars": max_chars,
    }


def person_block(prefix, who_zh, who_fr, keys, id_radio, id_options):
    out = []
    for (suffix, zh, fr, kind), pdf_key in zip(PERSON, keys, strict=False):
        if not pdf_key:  # 这一块在官方表里没有这个栏目
            continue
        if kind == "radio":
            out.append(item(f"{prefix}_{suffix}", f"{who_zh}：{zh}", f"{who_fr} — {fr}", id_radio, "radio",
                            options=id_options, required=False,
                            question=f"{who_zh}的证件类型是？", help_="三选一：身份证（C.N.I）、护照、其他"))
        else:
            required = suffix in ("nom", "prenom", "qualite", "email")
            out.append(item(f"{prefix}_{suffix}", f"{who_zh}：{zh}", f"{who_fr} — {fr}", pdf_key, kind,
                            required=required,
                            help_="据实填写，与证件一致" if suffix in ("nom", "prenom", "id_num") else ""))
    return out


def f118() -> dict:
    org = lambda p, who_zh, who_fr, keys: [  # noqa: E731
        item(f"{p}_nom", f"{who_zh}：名称", f"{who_fr} — Nom / Raison sociale", keys[0],
             fact="company_name" if p == "ctrl" else "recipient",
             question=f"{who_zh}的名称（法人全称）是？", help_="法人填公司全称；自然人另勾选“自然人”"),
        item(f"{p}_sigle", f"{who_zh}：简称", f"{who_fr} — Prénom / Sigle", keys[1], required=False),
        item(f"{p}_adresse", f"{who_zh}：地址", f"{who_fr} — Adresse", keys[2]),
        item(f"{p}_commune", f"{who_zh}：区/镇", f"{who_fr} — Commune", keys[3], required=False),
        item(f"{p}_ville", f"{who_zh}：城市", f"{who_fr} — Ville", keys[4]),
        item(f"{p}_pays", f"{who_zh}：国家", f"{who_fr} — Pays", keys[5]),
        item(f"{p}_cp", f"{who_zh}：邮编", f"{who_fr} — Code Postal", keys[6], required=False),
        item(f"{p}_activite", f"{who_zh}：经营活动", f"{who_fr} — Activité", keys[7]),
        item(f"{p}_code_activite", f"{who_zh}：活动代码", f"{who_fr} — Code Activité", keys[8], required=False),
    ]
    sections = [
        {
            "id": "I", "title_zh": "一、责任人（数据输出方，在摩洛哥）", "title_fr": "I. Identification du responsable de traitement",
            "items": [
                item("ctrl_type", "责任人类型", "Personne Physique / Personne Morale", "Gr Ident Respo Trait", "radio",
                     options={"physique": "Gr Ident Respo Trait", "morale": "Gr Ident Respo Trait#1"},
                     question="责任人是自然人还是法人？", help_="企业一般选“法人”（Personne Morale）"),
                *org("ctrl", "责任人", "Responsable", ["Nom  Raison sociale1", "Prénom  Sigle1", "Adresse1", "Commune1", "Ville1", "Pays1", "Code Postal1", "Activité1", "Code Activité1"]),
                item("ctrl_nrc", "责任人：商事登记号（RC）", "Responsable — N°RC", "NRC1", help_="摩洛哥商事登记号"),
                item("ctrl_juridiction", "责任人：登记法院", "Responsable — Juridiction", "Juridiction1"),
                item("ctrl_email", "责任人：电子邮箱", "Responsable — E-mail", "Email1"),
                item("ctrl_tel", "责任人：固定电话", "Responsable — Tél (Fixe)", "Tel1", required=False),
                item("ctrl_gsm", "责任人：手机", "Responsable — GSM", "GSM1", required=False),
                item("ctrl_fax", "责任人：传真", "Responsable — Fax", "Fax2", required=False),
                *person_block("ctrl_rep", "法定代表人", "Représentant légal",
                              ["Nom1", "Prénom1", "Qualité1", "Gr Id tatata", "Autre1", "N Pièce dIdentité1", "Nationalité1", "Email_21", "Tél Fixe1", "GSM_11", "Fax_11"],
                              "Gr Id tatata", {"cni": "Gr Id tatata", "passeport": "Gr Id tatata#1", "autre": "Gr Id tatata#2"}),
                *person_block("ctrl_comm", "提供数据的人", "Personne communiquant les données",
                              ["Nom_21", "Prénom_21", "Qualité_21", "Gr Id Contact Principal", "", "N Pièce dIdentité_21", "Nationalité_21", "Email_31", "Tél Fixe_21", "GSM_2", "Fax_2"],
                              "Gr Id Contact Principal", {"cni": "Gr Id Contact Principal", "passeport": "Gr Id Contact Principal#1", "autre": "Gr Id Contact Principal#2"}),
                *person_block("ctrl_contact", "主要联系人", "Contact principal",
                              ["Nom_22", "Prénom_22", "Qualité_22", "Gr Id personne comm donnée", "", "N Pièce dIdentité_22", "Nationalité_22", "Email_32", "Tél Fixe_22", "GSM_22", "Fax_22"],
                              "Gr Id personne comm donnée", {"cni": "Gr Id personne comm donnée", "passeport": "Gr Id personne comm donnée#1", "autre": "Gr Id personne comm donnée#2"}),
            ],
        },
        {
            "id": "II", "title_zh": "二、数据接收方（境外）", "title_fr": "II. Identification du destinataire des données",
            "items": [
                item("dest_type", "接收方类型", "Personne Physique / Personne Morale", "Gr Ident dest Trait", "radio",
                     options={"physique": "Gr Ident dest Trait", "morale": "Gr Ident dest Trait#1"}, question="接收方是自然人还是法人？"),
                *org("dest", "接收方", "Destinataire", ["Nom  Raison sociale_244", "Prénom  Sigle_244", "Adresse_24", "Commune_244", "Ville_24", "Pays_2", "Code Postal_244", "Activité_2", "Code Activité_244"]),
                item("dest_idtype", "接收方：企业识别号类型", "Destinataire — Type identifiant", "Gr II ident dest type entr", "radio",
                     options={"rc": "Gr II ident dest type entr", "siren": "Gr II ident dest type entr#1", "autre": "Gr II ident dest type entr#2"},
                     question="接收方的企业识别号属于哪一种？", help_="商事登记（RC）、SIREN 或其他（中国企业一般选“其他”，填统一社会信用代码）"),
                item("dest_idautre", "接收方：其他识别号类型", "Destinataire — Autre", "Autre_444", required=False),
                item("dest_idnum", "接收方：识别号", "Destinataire — N° d'identifiant", "N didentifiant44", help_="中国企业填统一社会信用代码"),
                item("dest_juridiction", "接收方：登记机关", "Destinataire — Juridiction", "Juridiction_244"),
                item("dest_email", "接收方：电子邮箱", "Destinataire — E-mail", "Email_544"),
                item("dest_tel", "接收方：固定电话", "Destinataire — Tél", "Tél Fixe_44", required=False),
                item("dest_gsm", "接收方：手机", "Destinataire — GSM", "GSM1144", required=False),
                item("dest_fax", "接收方：传真", "Destinataire — Fax", "Fax114", required=False),
                item("dest_rep_nom", "接收方法定代表人：姓", "Destinataire, représentant légal — Nom", "Nom_4"),
                item("dest_rep_prenom", "接收方法定代表人：名", "Destinataire, représentant légal — Prénom", "Prénom_4"),
                item("dest_rep_qualite", "接收方法定代表人：职务", "Destinataire, représentant légal — Qualité", "Qualité_4"),
                item("dest_rep_idnum", "接收方法定代表人：证件号码", "Destinataire, représentant légal — N° Pièce d'Identité", "N Pièce dIdentité_4", required=False),
                item("dest_rep_nationalite", "接收方法定代表人：国籍", "Destinataire, représentant légal — Nationalité", "Nationalité_4", required=False),
                item("dest_rep_email", "接收方法定代表人：电子邮箱", "Destinataire, représentant légal — E-mail", "Email_6", required=False),
                # 证件类型三个框从左到右：C.N.I、护照、其他（按页面位置核对）
                item("dest_rep_id_type", "接收方法定代表人：证件类型", "Destinataire, représentant légal — Pièce d'identité", "Gr II rep leg type entr", "radio",
                     required=False, options={"cni": "Gr II rep leg type entr", "passeport": "Gr II rep leg type entr#1", "autre": "Gr II rep leg type entr#2"},
                     option_labels={"cni": "身份证（C.N.I）", "passeport": "护照", "autre": "其他"}, help_="境外人员一般选“护照”"),
                item("dest_rep_id_autre", "接收方法定代表人：其他证件名称", "Destinataire, représentant légal — Autre", "undefined_300", required=False),
                item("dest_rep_tel", "接收方法定代表人：固定电话", "Destinataire, représentant légal — Tél", "Tél Fixe_5", required=False),
                item("dest_rep_gsm", "接收方法定代表人：手机", "Destinataire, représentant légal — GSM", "GSM111", required=False),
                item("dest_rep_fax", "接收方法定代表人：传真", "Destinataire, représentant légal — Fax", "Fax111", required=False),
                item("dest_contact_nom", "接收方主要联系人：姓", "Destinataire, contact principal — Nom", "Nom_5"),
                item("dest_contact_prenom", "接收方主要联系人：名", "Destinataire, contact principal — Prénom", "Prénom_5"),
                item("dest_contact_qualite", "接收方主要联系人：职务", "Destinataire, contact principal — Qualité", "Qualité_5"),
                item("dest_contact_email", "接收方主要联系人：电子邮箱", "Destinataire, contact principal — E-mail", "Email_7"),
                item("dest_contact_id_type", "接收方主要联系人：证件类型", "Destinataire, contact principal — Pièce d'identité", "Gr II cont princ type entr", "radio",
                     required=False, options={"cni": "Gr II cont princ type entr", "passeport": "Gr II cont princ type entr#1", "autre": "Gr II cont princ type entr#2"},
                     option_labels={"cni": "身份证（C.N.I）", "passeport": "护照", "autre": "其他"}, help_="境外人员一般选“护照”"),
                item("dest_contact_id_autre", "接收方主要联系人：其他证件名称", "Destinataire, contact principal — Autre", "undefined_301", required=False),
                item("dest_contact_idnum", "接收方主要联系人：证件号码", "Destinataire, contact principal — N° Pièce d'Identité", "N Pièce dIdentité_5", required=False),
                item("dest_contact_nationalite", "接收方主要联系人：国籍", "Destinataire, contact principal — Nationalité", "Nationalité_5", required=False),
                item("dest_contact_tel", "接收方主要联系人：固定电话", "Destinataire, contact principal — Tél", "Tél Fixe_6", required=False),
                item("dest_contact_gsm", "接收方主要联系人：手机", "Destinataire, contact principal — GSM", "GSM112", required=False),
                item("dest_contact_fax", "接收方主要联系人：传真", "Destinataire, contact principal — Fax", "Fax", required=False),
                item("dest_kind", "接收方类型", "Type de destinataire", "Succursales", "check",
                     options={"succursales": "Succursales", "siege": "siege", "clients": "Clients1", "sous_traitant": "ss traitant", "fournisseurs": "Fournisseurs1", "autres": "undefined_25"},
                     question="接收方与责任人是什么关系？（可多选）", help_="分支机构 / 总部 / 客户 / 分包商 / 供应商 / 其他"),
                item("dest_kind_autre", "接收方类型：其他说明", "Type de destinataire — Autres", "Nautres", required=False),
                item("dest_authority", "接收国是否设有数据保护监管机构", "Le pays destinataire dispose-t-il d'une autorité de contrôle ?", "Gr pays dest aut", "radio",
                     options={"oui": "Gr pays dest aut", "non": "Gr pays dest aut#1"}, question="接收国是否设有个人数据保护监管机构？",
                     help_="摩洛哥 236-2015 号决议列出了认定保护足够的国家，其余国家须按第 44 条办理"),
                item("dest_decl_kind", "接收国的申报/授权类别", "Déclaration ou Autorisation dans le pays de destination", "Gr Dec Aut", "radio",
                     options={"declaration": "Gr Dec Aut", "autorisation": "Gr Dec Aut#1"}, required=False, question="接收方在其所在国是做了申报还是取得了授权？"),
                item("dest_decl_num", "接收国的申报/授权编号", "Numéro de déclaration / autorisation", "N", required=False),
            ],
        },
        {
            "id": "III", "title_zh": "三、传输的数据文件", "title_fr": "III. Description du fichier transféré",
            "items": [
                item("file_name", "文件名称", "1- Nom du fichier", "1Nom du fichier", question="这批传输数据的名称是什么？（如“车辆运行数据回传”）", max_chars=120),
                item("file_description", "文件描述", "2- Description du fichier", "2Description du fichier", fact="data_types", question="这批数据包含哪些内容？", max_chars=200),
                item("file_people", "涉及人数（大约）", "3- Nombre approximatif de personnes concernées", "3Nombre approximatif de personnes concernées", fact="data_volume", question="大约涉及多少个自然人？"),
                item("file_mode", "传输方式", "4- Le mode de transfert", "Texte111", question="数据以什么方式传输？（如：加密专线、API 对接、云存储同步）"),
                item("file_purpose", "传输目的", "5- Finalité du transfert", "5Finalité du transfert", fact="purpose", question="传输的目的是什么？"),
                item("file_frequency", "传输频率", "6- Fréquence de transfert", "6Fréquence de transfert", question="多久传输一次？（如：实时、每日、每月）"),
                item("file_first_date", "首次传输日期", "7- Date du premier transfert", "7Date du premier transfert", question="首次传输的日期（或计划日期）？", help_="格式：日/月/年"),
                *[item(f"proc_{n}_{k}", f"摩洛哥已有处理{n}：{lz}", f"Traitement au Maroc {n} — {lf}", f"8_caract_trait_{key}{n}", required=False,
                       help_="须已向 CNDP 申报或获授权的底层处理；没有则整行留空（CNDP 只在与底层处理同时提交时受理转移申请）" if n == 1 and k == "denom" else "")
                  for n in (1, 2, 3, 4) for k, key, lz, lf in (("denom", "denom", "处理名称", "Dénomination"), ("rec", "recepisse", "回执号", "N° Récépissé"), ("aut", "n_aut", "授权号", "N° Autorisation"))],
            ],
        },
        {
            "id": "IV", "title_zh": "四、当事人同意", "title_fr": "IV. Consentement des personnes concernées",
            "items": [
                item("consent", "当事人是否同意传输", "Les personnes concernées ont-elles consenti ?", "Gr cons", "radio",
                     options={"oui": "Gr cons", "non": "Gr cons#1"}, question="相关个人是否已同意把其数据传输出境？"),
                {**item("consent_method", "同意的方式", "Si oui, méthode de consentement", "undefined_66", required=False,
                        question="如果已同意，是以什么方式取得的？（如：车机端弹窗勾选、注册协议单独条款）", max_chars=200),
                 "pdf_key2": "Si oui indiquez la méthode de consentement", "line_chars": 45},  # 印了两行：短的一行 + 下面整行
            ],
        },
        {
            "id": "VI", "title_zh": "六、传输到未确保充分保护的国家：援引的例外", "title_fr": "VI. Transfert vers un pays n'assurant pas une protection suffisante",
            "items": [
                item("derog_cases", "传输所援引的例外情形", "Le transfert est nécessaire", "derog", "check", required=False,
                     options={"vie": "à la sauvegarde de la vie de la personne concernée", "interet": "à la prévention de lintérêt public", "justice": "au respect dobligations permettant dassurer la constatation lexercice ou la défense dun droit en", "contrat": "à lexécution dun contrat entre le responsable du trainement et lintéressé ou de mesures", "contrat_tiers": "à la conclusion ou à lexécution dun contrat conclu ou à conclure entre le responsable du traitement et", "entraide": "à lexécution dune mesure dentraide judiciaire internationale", "medical": "à la prévention le diagnostic ou le traitement daffections médicales", "accord": "Le transfert  seffectue en application dun accord bilatéral ou multilatéral auquel le Maroc est partie", "garanties": "Le traitement garantit un niveau de protection suffisant de la vie privée ainsi que des libertés et droits"},
                     option_labels={"vie": "为保护当事人生命所必需", "interet": "为预防危害公共利益所必需",
                                    "justice": "为确认、行使或抗辩司法权利所必需", "contrat": "为履行处理负责人与当事人之间的合同（或合同前措施）所必需",
                                    "contrat_tiers": "为订立或履行处理负责人与第三方之间的合同所必需", "entraide": "为执行国际司法协助措施所必需",
                                    "medical": "为预防、诊断或治疗疾病所必需", "accord": "依据摩洛哥参加的双边或多边协定传输",
                                    "garanties": "接收方通过合同条款或内部规则保证充分保护水平（需附条款文本）"},
                     question="接收国不在摩洛哥承认的充分保护国家名单内时，本次传输援引哪项例外？（可多选）",
                     help_="摩洛哥 CNDP 第 236-2015 号决议列有承认充分保护的国家名单；不在名单内的国家（含中国）须援引一项例外并取得 CNDP 同意。合同条款或内部规则方式需要把条款文本作为附件一并提交。"),
            ],
        },
        {
            "id": "VII", "title_zh": "七、传输的安全措施", "title_fr": "VII. Sécurité des transferts",
            "items": [
                item("sec_1", "接收方身份认证", "Authentification des destinataires", "Gr VII sec 1", "radio", options={"oui": "Gr VII sec 1", "non": "Gr VII sec 1#1"}, required=True,
                     question="是否对接收方做身份验证（身份控制、VPN 等）？"),
                item("sec_1_note", "接收方身份认证：说明", "Authentification des destinataires — Comment", "undefined_3000", required=False, max_chars=120,
                     question="简要说明采取的做法（选填）。"),
                item("sec_2", "数据完整性", "Intégrité des données", "Gr VII sec 2", "radio", options={"oui": "Gr VII sec 2", "non": "Gr VII sec 2#1"}, required=True,
                     question="传输通道是否有保证数据完整性的安全措施？"),
                item("sec_2_note", "数据完整性：说明", "Intégrité des données — Comment", "undefined_21001", required=False, max_chars=120,
                     question="简要说明采取的做法（选填）。"),
                item("sec_3", "合同条款", "Clauses contractuelles", "Gr VII sec 3", "radio", options={"oui": "Gr VII sec 3", "non": "Gr VII sec 3#1"}, required=True,
                     question="合同里是否约定了数据安全、访问权、审计等条款？"),
                item("sec_3_note", "合同条款：说明", "Clauses contractuelles — Comment", "undefined_21002", required=False, max_chars=120,
                     question="简要说明采取的做法（选填）。"),
                item("sec_4", "销毁不再使用的数据", "Destruction des données non utilisées", "Gr VII sec 4", "radio", options={"oui": "Gr VII sec 4", "non": "Gr VII sec 4#1"}, required=True,
                     question="接收方是否承诺销毁不再使用的数据，并可现场核查？"),
                item("sec_4_note", "销毁不再使用的数据：说明", "Destruction des données non utilisées — Comment", "undefined_24003", required=False, max_chars=120,
                     question="简要说明采取的做法（选填）。"),
                item("sec_5", "数据保密", "Confidentialité des données", "Gr VII sec 5", "radio", options={"oui": "Gr VII sec 5", "non": "Gr VII sec 5#1"}, required=True,
                     question="是否有访问权限与账号口令的管理（保密）？"),
                item("sec_5_note", "数据保密：说明", "Confidentialité des données — Comment", "undefined_21004", required=False, max_chars=120,
                     question="简要说明采取的做法（选填）。"),
                item("sec_6", "访问控制", "Contrôle d'accès", "Gr VII sec 6", "radio", options={"oui": "Gr VII sec 6", "non": "Gr VII sec 6#1"}, required=True,
                     question="场所与设备是否有物理访问保护？"),
                item("sec_6_note", "访问控制：说明", "Contrôle d'accès — Comment", "undefined_24005", required=False, max_chars=120,
                     question="简要说明采取的做法（选填）。"),
                item("sec_7", "通信加密", "Chiffrement de la communication", "Gr VII sec 7", "radio", options={"oui": "Gr VII sec 7", "non": "Gr VII sec 7#1"}, required=True,
                     question="传输通信是否加密（VPN、电子证书等）？"),
                item("sec_7_note", "通信加密：说明", "Chiffrement de la communication — Comment", "undefined_21006", required=False, max_chars=120,
                     question="简要说明采取的做法（选填）。"),
                item("sec_8", "数据加密或编码", "Cryptage ou codage des données", "Gr VII sec 8", "radio", options={"oui": "Gr VII sec 8", "non": "Gr VII sec 8#1"}, required=True,
                     question="数据本身是否加密或编码（算法、加密软件、匿名化）？"),
                item("sec_8_note", "数据加密或编码：说明", "Cryptage ou codage des données — Comment", "undefined_24007", required=False, max_chars=120,
                     question="简要说明采取的做法（选填）。"),
            ],
        },
        {
            "id": "VIII", "title_zh": "八、法定代表人声明与签署", "title_fr": "VIII. Signature du représentant légal",
            "items": [
                item("sign_name", "签署人姓名", "Je soussigné(e)", "text000", fact=None, max_chars=60, question="声明签署人的姓名（法定代表人）是？"),
                item("sign_role", "签署人职务", "en ma qualité de", "text001", max_chars=60, question="签署人的职务是？"),
                item("sign_place", "签署地点", "Fait à", "text002", required=False, max_chars=40, help_="可留空，打印后手写"),
                item("sign_date", "签署日期", "Le", "text003", required=False, max_chars=20, help_="可留空，打印后手写"),
            ],
        },
    ]
    return {
        "code": "MA_F118", "country": "MA", "direction": "export",
        "name_zh": "摩洛哥：转移数据到国外申请（CNDP F118）", "authority_zh": "摩洛哥国家数据保护委员会（CNDP）",
        "language": "fr", "kind": "pdf", "file": "MA_CNDP_F118_2025-09.pdf", "version": "2025-09",
        "source_url": "https://www.cndp.ma/wp-content/uploads/2025/09/CNDP-Transfert-Etranger-F118.pdf",
        "retrieved": "2026-11-04", "legal_basis_zh": "第 09-08 号法第 43、44 条",
        "prerequisite_zh": "转移申请须与底层数据处理的申报或授权同时提交；接收国不在 236-2015 号决议名单内的，须经 CNDP 明确同意（回执）后才能传输",
        "timing_zh": "CNDP 在 2 个月内答复，可延长一次；材料不全时期限从补齐之日起算",
        "sections": sections,
    }


def _person(prefix: str, who_zh: str, who_fr: str, keys: dict[str, str], radio: str, required: bool = True,
            id_options: dict[str, str] | None = None) -> list[dict]:
    """A person block, ``keys`` mapping each field to the control at that position on the page.
    ``id_options``: the controls of the three identity-document boxes when they are not in the usual
    order (C.N.I, passport, other)."""
    def req(name):
        return required and name in ("nom", "prenom", "qualite", "email")

    out = [
        item(f"{prefix}_nom", f"{who_zh}：姓", f"{who_fr} — Nom", keys["nom"], required=req("nom"), help_="据实填写，与证件一致"),
        item(f"{prefix}_prenom", f"{who_zh}：名", f"{who_fr} — Prénom", keys["prenom"], required=req("prenom"), help_="据实填写，与证件一致"),
        item(f"{prefix}_qualite", f"{who_zh}：职务", f"{who_fr} — Qualité", keys["qualite"], required=req("qualite")),
        item(f"{prefix}_id_type", f"{who_zh}：证件类型", f"{who_fr} — Pièce d'identité", radio, "radio", required=False,
             options=id_options or {"cni": radio, "passeport": radio + "#1", "autre": radio + "#2"},
             option_labels={"cni": "身份证（C.N.I）", "passeport": "护照", "autre": "其他"},
             question=f"{who_zh}的证件类型是？", help_="三选一：身份证（C.N.I）、护照、其他"),
        item(f"{prefix}_id_autre", f"{who_zh}：其他证件名称", f"{who_fr} — Autre", keys["autre"], required=False),
        item(f"{prefix}_id_num", f"{who_zh}：证件号码", f"{who_fr} — N° Pièce d'Identité", keys["idnum"], required=False, help_="据实填写，与证件一致"),
        item(f"{prefix}_nationalite", f"{who_zh}：国籍", f"{who_fr} — Nationalité", keys["nationalite"], required=False),
        item(f"{prefix}_email", f"{who_zh}：电子邮箱", f"{who_fr} — E-mail", keys["email"], required=req("email")),
        item(f"{prefix}_tel", f"{who_zh}：固定电话", f"{who_fr} — Tél (Fixe)", keys["tel"], required=False),
        item(f"{prefix}_gsm", f"{who_zh}：手机", f"{who_fr} — GSM", keys["gsm"], required=False),
        item(f"{prefix}_fax", f"{who_zh}：传真", f"{who_fr} — Fax", keys["fax"], required=False),
    ]
    return [i for i in out if i["pdf_key"]]


def _org(prefix: str, who_zh: str, who_fr: str, keys: dict[str, str], fact: str | None, required: bool = True) -> list[dict]:
    fields = [
        ("nom", "名称", "Nom / Raison sociale", True), ("sigle", "简称", "Prénom / Sigle", False), ("adresse", "地址", "Adresse", True),
        ("commune", "区/镇", "Commune", False), ("ville", "城市", "Ville", True), ("pays", "国家", "Pays", False),
        ("cp", "邮编", "Code Postal", False), ("activite", "经营活动", "Activité", True), ("code_activite", "活动代码", "Code Activité", False),
        ("nrc", "商事登记号（RC）", "N°RC", False), ("juridiction", "登记法院", "Juridiction", False), ("email", "电子邮箱", "E-mail", True),
        ("tel", "固定电话", "Tél (Fixe)", False), ("gsm", "手机", "GSM", False), ("fax", "传真", "Fax", False),
    ]
    out = []
    for name, zh, fr, must in fields:
        if not keys.get(name):
            continue
        out.append(item(f"{prefix}_{name}", f"{who_zh}：{zh}", f"{who_fr} — {fr}", keys[name], fact=fact if name == "nom" else None,
                        required=required and must,
                        question=f"{who_zh}的名称（法人全称）是？" if name == "nom" else None))
    return out


def f211() -> dict:
    """Morocco CNDP F211 — declaration of a processing of personal data (the regime for a processing
    carried out in Morocco). Filed by whoever processes the data in Morocco, so it belongs to the
    receiving end of a flow into Morocco."""
    sec_rows = [
        ("locaux", "场所物理访问", "Accès physique aux locaux"), ("equipements", "设备物理访问", "Accès physique aux équipements"),
        ("supports", "存储介质访问", "Accès aux supports de données"), ("transmission", "数据传输", "La transmission des données"),
        ("postes", "终端电脑", "Postes clients"), ("lan", "有线局域网", "Réseau LAN"), ("wlan", "无线局域网", "Réseau WLAN"),
        ("droits", "访问权限管理", "Gestion des droits d'accès"), ("journal", "日志记录", "Journalisation"),
        ("continuite", "业务连续性管理", "Gestion de la continuité"), ("contrat", "合同条款（员工、分包商、第三方）", "Clauses contractuelles"),
        ("autres", "其他", "Autres"),
    ]
    security = [
        item("sec_all", "是否采取了保障数据安全与保密的措施", "Mesures adéquates pour la sécurité et la confidentialité ?", "Gr Mesure Securite 0", "radio",
             options={"oui": "Gr Mesure Securite 0", "non": "Gr Mesure Securite 0#1"}, question="是否对数据的安全和保密采取了相应措施？"),
        item("sec_autres_label", "其他安全措施的名称", "Autres (type de sécurité)", "autre__001", required=False, max_chars=40),
    ]
    for n, (rid, zh, fr) in enumerate(sec_rows, 1):
        security.append(item(f"sec_{rid}", zh, fr, f"Gr Mesure Securite {n}", "radio", required=n <= 11,
                             options={"oui": f"Gr Mesure Securite {n}", "non": f"Gr Mesure Securite {n}#1"},
                             question=f"“{zh}”方面是否采取了措施？"))
        security.append(item(f"sec_{rid}_note", f"{zh}：说明", f"{fr} — Comment", f"comment{n - 1}", required=False, max_chars=120,
                             question="简要说明采取的做法（选填）。"))
    interconnect = []
    # 每行的“用途/负责机构/理由”各有两行格子；第二行的控件名与所在列对不上（如“organisme_resp21”在理由列），按位置对应
    for n, (name, fin, fin2, org, org2, why, why2, num) in enumerate([
        ("nom_fich1", "finalité_fich1", "finalité_fich11", "organisme_resp1", "organisme_resp11", "raison1", "organisme_resp11#1", "n_decl"),
        ("Nom_fich2", "finalité_fich2", "finalité_fich12", "organisme_resp2", "organisme_resp22", "raison2", "organisme_resp21", "n_decl1"),
        ("Nom_fich3", "finalité_fich3", "finalité_fich13", "organisme_resp3", "organisme_resp33", "raison3", "organisme_resp31", "n_decl2"),
        ("Nom_fich4", "finalité_fich4", "finalité_fich14", "organisme_resp4", "organisme_resp44", "raison4", "organisme_resp41", "n_decl3"),
        ("Nom_fich5", "finalité_fich5", "finalité_fich15", "organisme_resp5", "organisme_resp55", "raison5", "organisme_resp51", "n_decl4"),
    ], 1):
        for k, key, zh, fr in (("nom", name, "文件名称", "Nom du fichier"), ("fin", fin, "文件用途", "Finalités du fichier"),
                               ("fin2", fin2, "文件用途（续）", "Finalités du fichier (suite)"),
                               ("org", org, "负责机构", "Organisme responsable"), ("org2", org2, "负责机构（续）", "Organisme responsable (suite)"),
                               ("raison", why, "互联理由（法律依据）", "Raisons d'interconnexion"), ("raison2", why2, "互联理由（续）", "Raisons d'interconnexion (suite)"),
                               ("num", num, "回执号或授权号", "N° de récépissé ou d'autorisation")):
            interconnect.append(item(f"ic_{n}_{k}", f"互联文件{n}：{zh}", f"Fichier {n} — {fr}", key, required=False, max_chars=40))
    sections = [
        {
            "id": "A", "title_zh": "A 部分：申报摘要", "title_fr": "Partie A — Synthèse de la déclaration",
            "items": [
                *_org("ctrl", "处理责任人（在摩洛哥处理数据的一方）", "Responsable du traitement",
                      {"nom": "Nom  Raison sociale1", "sigle": "Prénom  Sigle1", "adresse": "Adresse1", "commune": "Commune1", "ville": "Ville1",
                       "pays": "Pays1", "cp": "Code Postal1", "activite": "Activité1", "code_activite": "Code Activité1", "nrc": "NRC1",
                       "juridiction": "Juridiction1", "email": "Email1", "tel": "Tel1", "fax": "Fax1"}, fact="ma_processor"),
                item("treat_name", "处理活动名称", "Dénomination du traitement", "Dénomination du traitement1", max_chars=90,
                     question="这项数据处理活动叫什么？（如“车辆运行数据的售后远程诊断”）"),
                item("treat_purpose", "处理目的", "Finalité du traitement", "Finalité de traitement1", fact="purpose", max_chars=90),
            ],
        },
        {
            "id": "I", "title_zh": "一、处理责任人的代表与联系人", "title_fr": "I. Identification du responsable de traitement",
            "items": [
                *_person("ctrl_rep", "法定代表人", "Représentant légal",
                         {"nom": "Nom", "prenom": "Prénom", "qualite": "Qualité", "autre": "Autre", "idnum": "N Pièce dIdentité",
                          "nationalite": "Nationalité", "email": "Email_2", "tel": "Tél Fixe", "gsm": "GSM_1", "fax": "Fax_1"}, "Gr Id Repres. Legal"),
                *_person("ctrl_contact", "主要联系人", "Contact principal",
                         {"nom": "Nom_2", "prenom": "Prénom_2", "qualite": "Qualité_2", "autre": "Autre_2", "idnum": "N Pièce dIdentité_2",
                          "nationalite": "Nationalité_2", "email": "Email_3", "tel": "Tél Fixe_2", "gsm": "GSM_2", "fax": "Fax_2"}, "Gr Id Contact Principal"),
            ],
        },
        {
            "id": "II", "title_zh": "二、在摩洛哥的代表（责任人不在摩洛哥时填）", "title_fr": "II. Identification du représentant au Maroc",
            "items": [
                item("in_morocco", "处理责任人是否设立在摩洛哥", "Le responsable est-il installé au Maroc ?", "Gr Responsable localisation", "radio",
                     options={"oui": "Gr Responsable localisation", "non": "Gr Responsable localisation#1"},
                     question="处理责任人是否设立在摩洛哥？", help_="设立在摩洛哥的，本部分其余栏目不用填"),
                item("rep_type", "代表类型", "Personne Physique / Personne Morale", "Gr Ident Repres Maroc", "radio", required=False,
                     options={"physique": "Gr Ident Repres Maroc", "morale": "Gr Ident Repres Maroc#1"}),
                *_org("rep", "在摩洛哥的代表", "Représentant au Maroc",
                      {"nom": "Nom  Raison sociale_2", "sigle": "Prénom  Sigle_2", "adresse": "Adresse_2", "commune": "Commune_2", "ville": "Ville_2",
                       "cp": "Code Postal_2", "activite": "Activité_2", "code_activite": "Code Activité_2", "nrc": "NRC_2",
                       "juridiction": "Juridiction_2", "email": "Email_4", "tel": "Tél Fixe_3", "gsm": "GSM_3", "fax": "Fax_3"}, fact=None, required=False),
                *_person("rep_legal", "代表的法定代表人", "Représentant légal du représentant",
                         {"nom": "Nom_3", "prenom": "Prénom_3", "qualite": "Qualité_3", "autre": "Autre_3", "idnum": "N Pièce dIdentité_3",
                          "nationalite": "Nationalité_3", "email": "Email_5", "tel": "Tél Fixe_4", "gsm": "GSM_31", "fax": "Fax_31"}, "Gr Id Repre Legal 2", required=False),
                *_person("rep_contact", "代表的主要联系人", "Contact principal du représentant",
                         {"nom": "Nom_4", "prenom": "Prénom_4", "qualite": "Qualité_4", "autre": "Autre_4", "idnum": "N Pièce dIdentité_4",
                          "nationalite": "Nationalité_4", "email": "Email_6", "tel": "Tél Fixe_5", "gsm": "GSM_4", "fax": "Fax_4"}, "Gr Id Contact Princ 2", required=False),
            ],
        },
        {
            "id": "III", "title_zh": "三、分包商或第三方", "title_fr": "III. Identification du sous-traitant ou du tiers",
            "items": [
                item("self_processing", "处理是否由责任人自己进行", "Le responsable assure-t-il lui-même le traitement ?", "Gr Traitement assure O", "radio",
                     options={"oui": "Gr Traitement assure O", "non": "Gr Traitement assure N"},
                     question="数据处理是由责任人自己完成，还是交给分包商或第三方？", help_="自己完成选“是”并填负责的部门；交给他人选“否”并填对方信息"),
                item("self_service", "负责处理的部门", "Partie A — Service en charge du traitement", "undefined_5", required=False, max_chars=80),
                item("third_kind", "对方是分包商还是第三方", "Sous-traitant / Tiers", "Gr soustrait/tiers", "radio", required=False,
                     options={"soustraitant": "Gr soustrait/tiers", "tiers": "Gr soustrait/tiers#1"}),
                item("third_note", "分包商或第三方：说明", "Sous-traitant / Tiers — précision", "undefined_6", required=False, max_chars=80),
                item("third_type", "对方类型", "Personne Physique / Personne Morale", "Gr identification soustrait/tiers", "radio", required=False,
                     options={"physique": "Gr identification soustrait/tiers", "morale": "Gr identification soustrait/tiers#1"}),
                *_org("third", "分包商或第三方", "Sous-traitant ou tiers",
                      {"nom": "Nom  Raison sociale_3", "sigle": "Prénom  Sigle_3", "adresse": "Adresse_3", "commune": "Commune_3", "ville": "Ville_3",
                       "pays": "Pays_3", "cp": "Code Postal_3", "activite": "Activité_3", "code_activite": "Code Activité_3", "nrc": "NRC_3",
                       "juridiction": "Juridiction_3", "email": "Email_7", "tel": "Tél Fixe_6", "gsm": "GSM1", "fax": "Fax_21"}, fact=None, required=False),
            ],
        },
        {
            "id": "IV", "title_zh": "四、处理的特征", "title_fr": "IV. Caractéristiques du traitement",
            "items": [
                item("treat_mode", "处理方式", "Traitement manuel / automatisé", "Gr caracter du traitement M", "check",
                     options={"manuel": "Gr caracter du traitement M", "auto": "Gr caracter du traitement A"},
                     option_labels={"manuel": "人工处理", "auto": "自动化处理"}, question="数据是人工处理还是自动化处理？（可多选）"),
                item("treat_desc", "处理描述", "1- Description du traitement", "2Description du traitement 1", fact="data_flow", max_chars=90,
                     question="简要描述这项处理（采集什么、怎么处理、用于什么）。"),
                item("treat_desc2", "处理描述（续）", "1- Description du traitement (suite)", "2Description du traitement 2", required=False, max_chars=400),
                item("treat_people", "涉及人数（大约）", "2- Nombre approximatif de personnes concernées", "3Nombre approximatif de personnes concernées 1",
                     fact="data_volume", max_chars=60),
                item("treat_people2", "涉及人数（说明）", "2- Nombre de personnes (suite)", "3Nombre approximatif de personnes concernées 2", required=False, max_chars=120),
                item("anonymised", "数据是否匿名化", "3- Données anonymisées", "Gr donnees anonymisees", "radio",
                     options={"oui": "Gr donnees anonymisees", "non": "Gr donnees anonymisees#1"}, question="处理的数据是否做了匿名化？"),
                item("tools", "数据采集工具", "4- Outils utilisés pour la collecte des données", "Interview direct", "check",
                     options={"interview": "Interview direct", "video": "Vidéosurveillance", "puces": "Puces électroniques",
                              "papier": "Formulaires papiers", "biometrie": "Matériel biométrique", "autres": "Autres1"},
                     option_labels={"interview": "直接询问", "video": "视频监控", "puces": "电子芯片/传感器", "papier": "纸质表格",
                                    "biometrie": "生物识别设备", "autres": "其他"},
                     question="数据用什么工具采集？（可多选）", help_="车载摄像头一般选“视频监控”，车载终端、传感器选“电子芯片/传感器”"),
                item("tools_autres", "其他采集工具", "Autres (outils)", "undefined_7", required=False, max_chars=80),
            ],
        },
        {
            "id": "VI", "title_zh": "六、当事人同意", "title_fr": "VI. Consentement des personnes concernées",
            "items": [
                item("consent", "当事人是否同意", "Les personnes concernées ont-elles consenti ?", "Gr Information consentement", "radio",
                     options={"oui": "Gr Information consentement", "non": "Gr Information consentement#1"}, question="相关个人是否同意登记和处理其数据？"),
                item("consent_method", "同意的方式", "Méthode de consentement", "undefined_65", fact="consent_and_notice", required=False, max_chars=80),
                item("consent_method2", "同意的方式（续）", "Méthode de consentement (suite)", "Si oui indiquez la méthode de consentement", required=False, max_chars=150),
            ],
        },
        {
            "id": "VII", "title_zh": "七、当事人的权利", "title_fr": "VII. Droits des personnes concernées",
            "items": [
                item("rights_info", "是否告知了处理特征和查阅、更正、反对等权利", "Information des personnes (article 5)", "Gr Droits personnes 1", "radio",
                     options={"oui": "Gr Droits personnes 1", "non": "Gr Droits personnes 1#1"}, question="是否已告知当事人处理的情况及其查阅、更正、反对等权利？"),
                item("rights_info_how", "告知方式", "Si oui, comment", "undefined_666", required=False, max_chars=80),
                item("rights_info_how2", "告知方式（续）", "Si oui, comment (suite)", "Si oui comment", required=False, max_chars=150),
                item("rights_access", "是否有便于查阅信息的措施", "Mesures facilitant l'accès aux informations", "Gr Droits personnes 2", "radio",
                     options={"oui": "Gr Droits personnes 2", "non": "Gr Droits personnes 2#1"}),
                item("rights_access_how", "便于查阅的措施", "Si oui, comment", "undefined_67", required=False, max_chars=80),
                item("rights_access_how2", "便于查阅的措施（续）", "Si oui, comment (suite)", "Si oui comment_2", required=False, max_chars=150),
                item("rights_contact", "是否清楚写明了查询联系方式", "Contact clairement indiqué", "Gr Doits personnes 3", "radio",
                     options={"oui": "Gr Doits personnes 3", "non": "Gr Doits personnes 3#1"}),
                item("rights_contact_how", "联系方式的写明方式", "Si oui, comment", "undefined_68", required=False, max_chars=80),
                item("rights_contact_how2", "联系方式的写明方式（续）", "Si oui, comment (suite)", "Si oui comment_3", required=False, max_chars=150),
                item("rights_service", "当事人行使权利的受理部门", "Service auquel s'adresser", "undefined_681", max_chars=120),
                # 第 9 页这一块的控件名与位置不一致，下面按位置对应（例如名为“Email_9”的控件在“国籍”一格）
                item("svc_nom", "受理部门联系人：姓", "Contact du service — Nom", "Nom_5", required=False),
                item("svc_prenom", "受理部门联系人：名", "Contact du service — Prénom", "Prénom_5", required=False),
                item("svc_qualite", "受理部门联系人：职务", "Contact du service — Qualité", "N Pièce dIdentité_5", required=False),
                item("svc_id_type", "受理部门联系人：证件类型", "Contact du service — Pièce d'identité", "Gr ID Contact Service", "radio", required=False,
                     options={"cni": "Gr ID Contact Service", "passeport": "Gr ID Contact Service#1", "autre": "Gr ID Contact Service#2"}),
                item("svc_id_autre", "受理部门联系人：其他证件名称", "Contact du service — Autre", "Qualité_5", required=False),
                item("svc_id_num", "受理部门联系人：证件号码", "Contact du service — N° Pièce d'Identité", "undefined_69", required=False),
                item("svc_nationalite", "受理部门联系人：国籍", "Contact du service — Nationalité", "Email_9", required=False),
                item("svc_email", "受理部门联系人：电子邮箱", "Contact du service — E-mail", "Autre_13", required=False),
                item("svc_tel", "受理部门联系人：固定电话", "Contact du service — Tél (Fixe)", "Nationalité_5", required=False),
                item("svc_gsm", "受理部门联系人：手机", "Contact du service — GSM", "GSM_5", required=False),
                item("svc_fax", "受理部门联系人：传真", "Contact du service — Fax", "Fax_5", required=False),
            ],
        },
        {
            "id": "VIII", "title_zh": "八、与其他文件的互联和交叉比对", "title_fr": "VIII. Interconnexion et recoupement avec d'autres fichiers",
            "items": [
                item("ic", "是否与其他数据文件交叉比对", "Des recoupements de données sont-ils effectués ?", "Gr Interconnexion et recoupement", "radio",
                     options={"oui": "Gr Interconnexion et recoupement", "non": "Gr Interconnexion et recoupement#1"}, question="这些数据是否会与其他数据文件互联或交叉比对？"),
                item("ic_with", "与谁的文件互联", "Avec d'autres services / des organismes extérieurs", "Gr Interconnexion O/N", "radio", required=False,
                     options={"interne": "Gr Interconnexion O/N", "externe": "Gr Interconnexion O/N#1"},
                     option_labels={"interne": "本机构内的其他部门", "externe": "外部机构"}),
                *interconnect,
            ],
        },
        {"id": "IX", "title_zh": "九、安全措施", "title_fr": "IX. Mesures de sécurité", "items": security},
        {
            "id": "X", "title_zh": "十、法定代表人签署", "title_fr": "X. Signature du représentant légal",
            "items": [
                item("sign_name", "签署人姓名", "Je soussigné(e)", "text000", max_chars=60, question="声明签署人的姓名（法定代表人）是？"),
                item("sign_role", "签署人职务", "en ma qualité de", "text001", max_chars=60, question="签署人的职务是？"),
                item("sign_place", "签署地点", "Fait à", "text002", required=False, max_chars=40, help_="可留空，打印后手写"),
                item("sign_date", "签署日期", "Le", "text003", required=False, max_chars=20, help_="可留空，打印后手写"),
            ],
        },
    ]
    return {
        "code": "MA_F211", "country": "MA", "direction": "processing", "applies_if": "not_sensitive",
        "name_zh": "摩洛哥：个人数据处理申报（CNDP F211）", "authority_zh": "摩洛哥国家数据保护委员会（CNDP）",
        "language": "fr", "kind": "pdf", "file": "MA_CNDP_F211_2025-09.pdf", "version": "2025-09-10",
        "source_url": "https://www.cndp.ma/wp-content/uploads/2025/09/CNDP-Declaration-Normale_F211_20250910_Fr.pdf",
        "retrieved": "2026-11-04", "legal_basis_zh": "第 09-08 号法第 12、15–19 条",
        "prerequisite_zh": "在摩洛哥处理个人数据须先向 CNDP 申报（敏感数据等情形须改走事先授权 F112）；处理的数据转往国外的，另需同时提交转移申请（F118）",
        "timing_zh": "材料齐全时 CNDP 在 24 小时内出具回执（第 09-08 号法第 19 条）",
        "matrix_page": 5,
        "sections": sections,
    }


def f112() -> dict:
    """Morocco CNDP F112 — request for prior authorisation of a processing (sensitive data, data used
    for another purpose, offences, national ID numbers, interconnection of files with different
    purposes: law 09-08 art. 12). Same blocks as F211 plus the qualification of the request (I), the
    legal frame for sensitive data (II), the purpose (VII) and the transfer question (VIII); the
    controls were matched to the printed labels on each page, not by name — several names repeat
    F211's at other positions (e.g. the identity boxes run Passport/C.N.I/Other on page 3)."""
    yes_no = lambda key_yes, key_no: {"oui": key_yes, "non": key_no}  # noqa: E731
    sec_rows = [
        ("locaux", "场所物理访问", "Accès physique aux locaux"), ("equipements", "设备物理访问", "Accès physique aux équipements"),
        ("supports", "存储介质访问", "Accès aux supports de données"), ("transmission", "数据传输", "La transmission des données"),
        ("postes", "终端电脑", "Postes clients"), ("lan", "有线局域网", "Réseau LAN"), ("wlan", "无线局域网", "Réseau WLAN"),
        ("droits", "访问权限管理", "Gestion des droits d'accès"), ("journal", "日志记录", "Journalisation"),
        ("continuite", "业务连续性管理", "Gestion de la continuité"), ("contrat", "合同条款（员工、分包商、第三方）", "Clauses contractuelles"),
        ("autres", "其他", "Autres"),
    ]
    security = [
        item("sec_all", "是否采取了保障数据安全与保密的措施", "Mesures adéquates pour la sécurité et la confidentialité ?", "Gr  mesures securite 1", "radio",
             options=yes_no("Gr  mesures securite 1", "Gr  mesures securite 1#1"), question="是否对数据的安全和保密采取了相应措施？"),
        item("sec_autres_label", "其他安全措施的名称", "Autres (type de sécurité)", "autre__001", required=False, max_chars=40),
    ]
    for n, (rid, zh, fr) in enumerate(sec_rows, 1):
        key = f"Gr  mesures securite {n + 1}"
        security.append(item(f"sec_{rid}", zh, fr, key, "radio", required=n <= 11, options=yes_no(key, key + "#1"),
                             question=f"“{zh}”方面是否采取了措施？"))
        security.append(item(f"sec_{rid}_note", f"{zh}：说明", f"{fr} — Comment", f"comment{n - 1}", required=False, max_chars=120,
                             question="简要说明采取的做法（选填）。"))
    interconnect = []
    # 第一行“负责机构（续）”和“互联理由（续）”两个格子的控件名与 F211 相反（按页面位置核对）
    for n, (name, fin, fin2, org, org2, why, why2, num) in enumerate([
        ("nom_fich1", "finalité_fich1", "finalité_fich11", "organisme_resp1", "organisme_resp11#1", "raison1", "organisme_resp11", "n_decl"),
        ("Nom_fich2", "finalité_fich2", "finalité_fich12", "organisme_resp2", "organisme_resp22", "raison2", "organisme_resp21", "n_decl1"),
        ("Nom_fich3", "finalité_fich3", "finalité_fich13", "organisme_resp3", "organisme_resp33", "raison3", "organisme_resp31", "n_decl2"),
        ("Nom_fich4", "finalité_fich4", "finalité_fich14", "organisme_resp4", "organisme_resp44", "raison4", "organisme_resp41", "n_decl3"),
        ("Nom_fich5", "finalité_fich5", "finalité_fich15", "organisme_resp5", "organisme_resp55", "raison5", "organisme_resp51", "n_decl4"),
    ], 1):
        for k, key, zh, fr in (("nom", name, "文件名称", "Nom du fichier"), ("fin", fin, "文件用途", "Finalités du fichier"),
                               ("fin2", fin2, "文件用途（续）", "Finalités du fichier (suite)"),
                               ("org", org, "负责机构", "Organisme responsable"), ("org2", org2, "负责机构（续）", "Organisme responsable (suite)"),
                               ("raison", why, "互联理由（法律依据）", "Raisons d'interconnexion"), ("raison2", why2, "互联理由（续）", "Raisons d'interconnexion (suite)"),
                               ("num", num, "回执号或授权号", "N° de récépissé ou d'autorisation")):
            interconnect.append(item(f"ic_{n}_{k}", f"互联文件{n}：{zh}", f"Fichier {n} — {fr}", key, required=False, max_chars=40))
    frame = [  # 第二部分：处理敏感数据的法律前提（每问一对“是/否”勾选框）
        ("a", "当事人是否明确同意处理其数据", "Consentement exprès des personnes concernées"),
        ("b", "处理是否为责任人履行法定职责所必需", "Traitement indispensable à l'exercice des fonctions légales ou statutaires"),
        ("c", "处理是否为保护当事人或他人重大利益所必需（当事人无法表示同意时）", "Défense d'intérêts vitaux de la personne concernée ou d'une autre personne"),
        ("d", "数据是否由当事人本人公开", "Données manifestement rendues publiques par la personne concernée"),
        ("e", "处理是否为确认、行使或维护法律权利所必需", "Reconnaissance, exercice ou défense d'un droit en justice"),
    ]
    sections = [
        {
            "id": "I", "title_zh": "一、申请事由（勾选适用的情形）", "title_fr": "I. Qualification de la demande",
            "items": [
                item("qualif", "需要事先授权的情形", "Cochez les cases qui s'appliquent à votre traitement",
                     "Traitement de données sensibles origine raciale ou ethnique opinions politiques", "check",
                     options={"sensibles": "Traitement de données sensibles origine raciale ou ethnique opinions politiques",
                              "autres_fins": "Utilisation des données pour des fins autres que celles pour lesquelles elles ont été",
                              "infractions": "Traitement de données portant sur les infractions condamnations ou mesures de sûreté",
                              "cin": "Traitement de données comportant le numéro de la CIN",
                              "interconnexion": "Interconnexion de fichiers dont les finalités sont différentes"},
                     option_labels={"sensibles": "处理敏感数据（种族或民族出身、政治观点、宗教或哲学信仰、工会、健康与基因数据等）",
                                    "autres_fins": "将数据用于收集目的以外的用途",
                                    "infractions": "处理违法、定罪或保安措施相关数据",
                                    "cin": "处理含身份证（CIN）号码的数据",
                                    "interconnexion": "将用途不同的数据文件互联"},
                     question="这项处理属于哪种需要事先授权的情形？（可多选）",
                     help_="车内摄像头采集人脸用于识别、驾驶员健康监测等属于“处理敏感数据”；用车辆数据做原定用途以外的事属于“用于其他用途”"),
                item("qualif_autres", "其他情形", "Autres", "AUTRES____10", required=False, max_chars=120),
            ],
        },
        {
            "id": "II", "title_zh": "二、处理敏感数据的前提", "title_fr": "II. Cadre de traitement des données sensibles",
            "items": [
                item(f"frame_{k}", zh, fr, f"Gr traitement de donnees {k} oui", "radio", required=False,
                     options=yes_no(f"Gr traitement de donnees {k} oui", f"Gr traitement de donnees {k} non"),
                     question=f"{zh}？", help_="只在处理敏感数据时回答；至少有一项为“是”，敏感数据的处理才有法律前提")
                for k, zh, fr in frame
            ],
        },
        {
            "id": "III", "title_zh": "三、处理责任人", "title_fr": "III. Identification du responsable de traitement",
            "items": [
                item("ctrl_type", "处理责任人类型", "Personne Physique / Personne Morale", "Gr identi respo traitement", "radio",
                     options={"physique": "Gr identi respo traitement#1", "morale": "Gr identi respo traitement"},
                     option_labels={"physique": "自然人", "morale": "法人"}, question="处理责任人是自然人还是法人？"),
                *_org("ctrl", "处理责任人（在摩洛哥处理数据的一方）", "Responsable du traitement",
                      {"nom": "Nom  Raison sociale", "sigle": "Prénom  Sigle", "adresse": "Adresse", "commune": "Commune", "ville": "Ville",
                       "pays": "Pays", "cp": "Code Postal", "activite": "Activité", "code_activite": "Code Activité", "nrc": "NRC",
                       "juridiction": "Juridiction", "email": "Email", "tel": "Tel", "gsm": "GSM", "fax": "Fax"}, fact="ma_processor"),
                *_person("ctrl_rep", "法定代表人", "Représentant légal",
                         {"nom": "Nom", "prenom": "Prénom", "qualite": "Qualité", "autre": "Autre", "idnum": "N Pièce dIdentité",
                          "nationalite": "Nationalité", "email": "Email_2", "tel": "Tél Fixe", "gsm": "GSM_1", "fax": "Fax_1"},
                         "Gr ID identi respo traitement",
                         id_options={"cni": "Gr ID identi respo traitement#2", "passeport": "Gr ID identi respo traitement#1",
                                     "autre": "Gr ID identi respo traitement"}),
                *_person("ctrl_contact", "主要联系人", "Contact principal",
                         {"nom": "Nom_2", "prenom": "Prénom_2", "qualite": "Qualité_2", "autre": "Autre_2", "idnum": "N Pièce dIdentité_2",
                          "nationalite": "Nationalité_2", "email": "Email_3", "tel": "Tél Fixe_2", "gsm": "GSM_2", "fax": "Fax_2"},
                         "Gr ID Contact Principal 1"),
            ],
        },
        {
            "id": "IV", "title_zh": "四、在摩洛哥的代表（责任人不在摩洛哥时填）", "title_fr": "IV. Identification du représentant au Maroc",
            "items": [
                item("in_morocco", "处理责任人是否设立在摩洛哥", "Le responsable est-il installé au Maroc ?", "Gr respon Mar/Etran", "radio",
                     options={"oui": "Gr respon Mar/Etran", "non": "Gr respon Mar/Etran#1"},
                     question="处理责任人是否设立在摩洛哥？", help_="设立在摩洛哥的，本部分其余栏目不用填"),
                item("rep_type", "代表类型", "Personne Physique / Personne Morale", "Gr identif respo au maroc", "radio", required=False,
                     options={"physique": "Gr identif respo au maroc#1", "morale": "Gr identif respo au maroc"},
                     option_labels={"physique": "自然人", "morale": "法人"}),
                *_org("rep", "在摩洛哥的代表", "Représentant au Maroc",
                      {"nom": "Nom  Raison sociale_2", "sigle": "Prénom  Sigle_2", "adresse": "Adresse_2", "commune": "Commune_2", "ville": "Ville_2",
                       "cp": "Code Postal_2", "activite": "Activité_2", "code_activite": "Code Activité_2", "nrc": "NRC_2",
                       "juridiction": "Juridiction_2", "email": "Email_4", "tel": "Tél Fixe_3", "gsm": "GSM_3", "fax": "Fax_3"}, fact=None, required=False),
                *_person("rep_legal", "代表的法定代表人", "Représentant légal du représentant",
                         {"nom": "Nom_3", "prenom": "Prénom_3", "qualite": "Qualité_3", "autre": "Autre_3", "idnum": "N Pièce dIdentité_3",
                          "nationalite": "Nationalité_3", "email": "Email_5", "tel": "Tél Fixe_4", "gsm": "GSM_31", "fax": "Fax_31"},
                         "Gr ID representant legal 2", required=False,
                         id_options={"cni": "Gr ID representant legal 2#2", "passeport": "Gr ID representant legal 2#1",
                                     "autre": "Gr ID representant legal 2"}),
                *_person("rep_contact", "代表的主要联系人", "Contact principal du représentant",
                         {"nom": "Nom_4", "prenom": "Prénom_4", "qualite": "Qualité_4", "autre": "Autre_4", "idnum": "N Pièce dIdentité_4",
                          "nationalite": "Nationalité_4", "email": "Email_6", "tel": "Tél Fixe_5", "gsm": "GSM_4", "fax": "Fax_4"},
                         "Gr ID contact principal 2", required=False),
            ],
        },
        {
            "id": "V", "title_zh": "五、分包商或第三方", "title_fr": "V. Identification du sous-traitant ou du tiers",
            "items": [
                item("self_processing", "处理是否由责任人自己进行", "Le responsable assure-t-il lui-même le traitement ?", "Gr Traitement assure O", "radio",
                     options={"oui": "Gr Traitement assure O", "non": "Gr Traitement assure N"},
                     question="数据处理是由责任人自己完成，还是交给分包商或第三方？", help_="自己完成选“是”并填负责的部门；交给他人选“否”并填对方信息"),
                item("self_service", "负责处理的部门", "Partie A — Service en charge du traitement", "undefined_5", required=False, max_chars=80),
                item("third_kind", "对方是分包商还是第三方", "Sous-traitant / Tiers", "Gr soustrait/tiers", "radio", required=False,
                     options={"soustraitant": "Gr soustrait/tiers", "tiers": "Gr soustrait/tiers#1"},
                     option_labels={"soustraitant": "分包商", "tiers": "第三方"}),
                item("third_note", "分包商或第三方：说明", "Sous-traitant / Tiers — précision", "undefined_6", required=False, max_chars=80),
                item("third_type", "对方类型", "Personne Physique / Personne Morale", "Gr identification soustrait/tiers", "radio", required=False,
                     options={"physique": "Gr identification soustrait/tiers", "morale": "Gr identification soustrait/tiers#1"},
                     option_labels={"physique": "自然人", "morale": "法人"}),
                *_org("third", "分包商或第三方", "Sous-traitant ou tiers",
                      {"nom": "Nom  Raison sociale_3", "sigle": "Prénom  Sigle_3", "adresse": "Adresse_3", "commune": "Commune_3", "ville": "Ville_3",
                       "pays": "Pays_3", "cp": "Code Postal_3", "activite": "Activité_3", "code_activite": "Code Activité_3", "nrc": "NRC_3",
                       "juridiction": "Juridiction_3", "email": "Email_7", "tel": "Tél Fixe_6", "gsm": "GSM#1", "fax": "Fax_201"}, fact=None, required=False),
            ],
        },
        {
            "id": "VI", "title_zh": "六、处理的特征", "title_fr": "VI. Caractéristiques du traitement",
            "items": [
                item("treat_mode", "处理方式", "Traitement manuel / automatisé", "Gr caracter du traitement M", "check",
                     options={"manuel": "Gr caracter du traitement M", "auto": "Gr caracter du traitement A"},
                     option_labels={"manuel": "人工处理", "auto": "自动化处理"}, question="数据是人工处理还是自动化处理？（可多选）"),
                item("treat_name", "处理活动名称", "1- Dénomination du traitement", "1Dénomination du traitement 1", max_chars=90,
                     question="这项数据处理活动叫什么？（如“驾驶员疲劳监测”）"),
                item("treat_name2", "处理活动名称（续）", "1- Dénomination du traitement (suite)", "1Dénomination du traitement 2", required=False, max_chars=400),
                item("treat_desc", "处理描述", "2- Description du traitement", "2Description du traitement 1", fact="data_flow", max_chars=90,
                     question="简要描述这项处理（采集什么、怎么处理、用于什么）。"),
                item("treat_desc2", "处理描述（续）", "2- Description du traitement (suite)", "2Description du traitement 2", required=False, max_chars=400),
                item("treat_people", "涉及人数（大约）", "3- Nombre approximatif de personnes concernées", "3Nombre approximatif de personnes concernées 1",
                     fact="data_volume", max_chars=60),
                item("treat_people2", "涉及人数（说明）", "3- Nombre de personnes (suite)", "3Nombre approximatif de personnes concernées 2", required=False, max_chars=120),
                item("anonymised", "数据是否匿名化", "4- Données anonymisées", "Gr donnees anonymisees", "radio",
                     options={"oui": "Gr donnees anonymisees", "non": "Gr donnees anonymisees#1"}, question="处理的数据是否做了匿名化？"),
                item("tools", "数据采集工具", "5- Outils utilisés pour la collecte des données", "Interview direct", "check",
                     options={"interview": "Interview direct", "video": "Vidéosurveillance", "puces": "Puces électroniques",
                              "papier": "Formulaires papiers", "biometrie": "Matériel biométrique", "autres": "Autres"},
                     option_labels={"interview": "直接询问", "video": "视频监控", "puces": "电子芯片/传感器", "papier": "纸质表格",
                                    "biometrie": "生物识别设备", "autres": "其他"},
                     question="数据用什么工具采集？（可多选）", help_="车载摄像头一般选“视频监控”，车载终端、传感器选“电子芯片/传感器”，人脸或指纹识别选“生物识别设备”"),
                item("tools_autres", "其他采集工具", "Autres (outils)", "undefined_7", required=False, max_chars=80),
            ],
        },
        {
            "id": "VII", "title_zh": "七、处理目的", "title_fr": "VII. Finalité du traitement",
            "items": [
                item("finalite", "处理目的类别", "Finalité du traitement", "Gr Finalite du traitement", "radio",
                     options={"personnel": "Gr Finalite du traitement#2", "recherche": "Gr Finalite du traitement#1",
                              "statistique": "Gr Finalite du traitement", "clients": "Gr Finalite du traitement#3",
                              "marketing": "Gr Finalite du traitement#4", "marche": "Gr Finalite du traitement#5",
                              "fournisseurs": "Gr Finalite du traitement#6", "securite": "Gr Finalite du traitement#7",
                              "etudiants": "Gr Finalite du traitement#8", "comptabilite": "Gr Finalite du traitement#9",
                              "patients": "Gr Finalite du traitement#10", "services_publics": "Gr Finalite du traitement#11",
                              "autres": "Gr Finalite du traitement#12"},
                     option_labels={"personnel": "人事管理", "recherche": "科学研究", "statistique": "统计调查", "clients": "客户管理",
                                    "marketing": "直接营销", "marche": "市场分析", "fournisseurs": "供应商管理", "securite": "安全与门禁",
                                    "etudiants": "学生管理", "comptabilite": "财务会计", "patients": "患者随访", "services_publics": "公共服务",
                                    "autres": "其他"},
                     question="处理目的属于哪一类？", help_="车辆研发、测试、远程诊断等不在列出的类别里，选“其他”并在下面写明"),
                item("finalite_autres", "其他目的", "Autres (finalité)", "undefined_701", fact="purpose", required=False, max_chars=120),
            ],
        },
        {
            "id": "VIII", "title_zh": "八、数据是否转往国外", "title_fr": "VIII. Transfert de données à l'étranger",
            "items": [
                item("transfer", "处理的数据是否转往国外", "Les données traitées sont-elles transférées vers un pays étranger ?",
                     "Gr transfert etranger O/N", "radio", options={"oui": "Gr transfert etranger O/N#1", "non": "Gr transfert etranger O/N"},
                     question="处理的数据是否会转往国外？", help_="转往国外的，还要另外提交数据转移申请（F118），取得 CNDP 回执后才能传输"),
            ],
        },
        {
            "id": "X", "title_zh": "十、当事人同意", "title_fr": "X. Consentement des personnes concernées",
            "items": [
                item("consent", "当事人是否同意", "Les personnes concernées ont-elles consenti ?", "Gr Info/Consentement O/N", "radio",
                     options={"oui": "Gr Info/Consentement O/N#1", "non": "Gr Info/Consentement O/N"}, question="相关个人是否同意登记和处理其数据？"),
                item("consent_method", "同意的方式", "Méthode de consentement", "undefined_65", fact="consent_and_notice", required=False, max_chars=80),
                item("consent_method2", "同意的方式（续）", "Méthode de consentement (suite)", "Si oui indiquez la méthode de consentement", required=False, max_chars=150),
            ],
        },
        {
            "id": "XI", "title_zh": "十一、当事人的权利", "title_fr": "XI. Droits des personnes concernées",
            "items": [
                item("rights_info", "是否告知了处理特征和查阅、更正、反对等权利", "Information des personnes (article 5)", "Gr droits des personnes 1", "radio",
                     options={"oui": "Gr droits des personnes 1#1", "non": "Gr droits des personnes 1"}, question="是否已告知当事人处理的情况及其查阅、更正、反对等权利？"),
                item("rights_info_how", "告知方式", "Si oui, comment", "undefined_66", required=False, max_chars=80),
                item("rights_info_how2", "告知方式（续）", "Si oui, comment (suite)", "Si oui comment", required=False, max_chars=150),
                item("rights_access", "是否有便于查阅信息的措施", "Mesures facilitant l'accès aux informations", "Gr droits des personnes 2", "radio",
                     options={"oui": "Gr droits des personnes 2", "non": "Gr droits des personnes 2#1"}),
                item("rights_access_how", "便于查阅的措施", "Si oui, comment", "undefined_67", required=False, max_chars=80),
                item("rights_access_how2", "便于查阅的措施（续）", "Si oui, comment (suite)", "Si oui comment_2", required=False, max_chars=150),
                item("rights_contact", "是否清楚写明了查询联系方式", "Contact clairement indiqué", "Gr droits des personnes 3", "radio",
                     options={"oui": "Gr droits des personnes 3", "non": "Gr droits des personnes 3#1"}),
                item("rights_contact_how", "联系方式的写明方式", "Si oui, comment", "undefined_68", required=False, max_chars=80),
                item("rights_contact_how2", "联系方式的写明方式（续）", "Si oui, comment (suite)", "Si oui comment_3", required=False, max_chars=150),
                item("rights_service", "当事人行使权利的受理部门", "Service auquel s'adresser", "undefined_681", max_chars=120),
                # 这一块的控件名与位置不一致（与 F211 相同），按位置对应
                item("svc_nom", "受理部门联系人：姓", "Contact du service — Nom", "Nom_5", required=False),
                item("svc_prenom", "受理部门联系人：名", "Contact du service — Prénom", "Prénom_5", required=False),
                item("svc_qualite", "受理部门联系人：职务", "Contact du service — Qualité", "N Pièce dIdentité_5", required=False),
                item("svc_id_type", "受理部门联系人：证件类型", "Contact du service — Pièce d'identité", "Gr id contact au sein du service", "radio", required=False,
                     options={"cni": "Gr id contact au sein du service", "passeport": "Gr id contact au sein du service#1", "autre": "Gr id contact au sein du service#2"},
                     option_labels={"cni": "身份证（C.N.I）", "passeport": "护照", "autre": "其他"}),
                item("svc_id_autre", "受理部门联系人：其他证件名称", "Contact du service — Autre", "Qualité_5", required=False),
                item("svc_id_num", "受理部门联系人：证件号码", "Contact du service — N° Pièce d'Identité", "undefined_69", required=False),
                item("svc_nationalite", "受理部门联系人：国籍", "Contact du service — Nationalité", "Email_9", required=False),
                item("svc_email", "受理部门联系人：电子邮箱", "Contact du service — E-mail", "Autre_13", required=False),
                item("svc_tel", "受理部门联系人：固定电话", "Contact du service — Tél (Fixe)", "Nationalité_5", required=False),
                item("svc_gsm", "受理部门联系人：手机", "Contact du service — GSM", "GSM_5", required=False),
                item("svc_fax", "受理部门联系人：传真", "Contact du service — Fax", "Fax_5", required=False),
            ],
        },
        {
            "id": "XII", "title_zh": "十二、与其他文件的互联和交叉比对", "title_fr": "XII. Interconnexion et recoupement avec d'autres fichiers",
            "items": [
                item("ic", "是否与其他数据文件交叉比对", "Des recoupements de données sont-ils effectués ?", "Gr recoupement O/N", "radio",
                     options={"oui": "Gr recoupement O/N", "non": "Gr recoupement O/N#1"}, question="这些数据是否会与其他数据文件互联或交叉比对？"),
                item("ic_with", "与谁的文件互联", "Avec d'autres services / des organismes extérieurs", "Gr  Si recoupement O", "check", required=False,
                     options={"interne": "Gr  Si recoupement O", "externe": "Gr  Si recoupement N"},
                     option_labels={"interne": "本机构内的其他部门", "externe": "外部机构"}),
                *interconnect,
            ],
        },
        {"id": "XIII", "title_zh": "十三、安全措施", "title_fr": "XIII. Mesures de sécurité", "items": security},
        {
            "id": "XIV", "title_zh": "十四、法定代表人签署", "title_fr": "XIV. Signature du représentant légal",
            "items": [
                item("sign_name", "签署人姓名", "Je soussigné(e)", "text000", max_chars=60, question="声明签署人的姓名（法定代表人）是？"),
                item("sign_role", "签署人职务", "en ma qualité de", "text001", max_chars=60, question="签署人的职务是？"),
                item("sign_place", "签署地点", "Fait à", "text002", required=False, max_chars=40, help_="可留空，打印后手写"),
                item("sign_date", "签署日期", "Le", "text003", required=False, max_chars=20, help_="可留空，打印后手写"),
            ],
        },
    ]
    return {
        "code": "MA_F112", "country": "MA", "direction": "processing", "applies_if": "sensitive",
        "name_zh": "摩洛哥：个人数据处理事先授权申请（CNDP F112）", "authority_zh": "摩洛哥国家数据保护委员会（CNDP）",
        "language": "fr", "kind": "pdf", "file": "MA_CNDP_F112_2025-09.pdf", "version": "2025-09-10",
        "source_url": "https://www.cndp.ma/wp-content/uploads/2025/09/CNDP-Autorisation-Prealable-F112-20250910.pdf",
        "retrieved": "2026-10-30", "legal_basis_zh": "第 09-08 号法第 12 条",
        "prerequisite_zh": "处理敏感数据（含人脸等生物识别、健康数据）、将数据用于其他用途、处理身份证号码或互联用途不同的文件时，须先取得 CNDP 事先授权（不适用普通申报 F211）；数据转往国外的，另需提交转移申请（F118）",
        "timing_zh": "CNDP 审查后作出授权决定；材料不全时期限从补齐之日起算",
        "matrix_page": 6, "matrix_section": ("IX", "九、数据类别", "IX. Catégorie des données", "X"),
        "sections": sections,
    }


def main() -> None:
    from app.forms.build_tn import build as build_tn

    forms = [f118(), build_tn()[0], f211(), f112()]
    from app.forms.pdf_fill import list_fields

    for form in forms:
        data = (HERE / form["file"]).read_bytes()
        if form.get("matrix_page") is not None:
            from app.forms import f118_matrix

            matrix, _used = f118_matrix.build_declaration(list_fields(data), form["matrix_page"])
            mid, mzh, mfr, before = form.pop("matrix_section", ("V", "五、数据类别", "V. Catégorie des données", "VI"))
            section_v = {
                "id": mid, "title_zh": mzh, "title_fr": mfr,
                "items": [{
                    "id": "matrix_v", "label_zh": "处理的数据类别", "label_fr": "Catégories d'informations", "pdf_key": "", "kind": "matrix",
                    "fact": None, "required": True, "options": None, "option_labels_zh": None, "max_chars": None, "matrix": matrix,
                    "question_zh": "这项处理涉及哪些类别的数据？每一类说明涉及的人群、来源、接收方、内部可以访问的部门和保存期限。",
                    "help_zh": "车联网场景常见的类别是：照片、影像，地理位置、行车轨迹，姓名，电话号码；车内摄像头涉及人脸时还要勾选“生物识别数据”。",
                }],
            }
            at = next(i for i, sec in enumerate(form["sections"]) if sec["id"] == before)
            form["sections"].insert(at, section_v)
        if form["code"] == "MA_F118":
            from app.forms import f118_matrix

            matrix, _used = f118_matrix.build(list_fields(data))
            section_v = {
                "id": "V", "title_zh": "五、接收方处理的特征（按数据类别）", "title_fr": "V. Caractéristiques du traitement effectué par le destinataire",
                "items": [{
                    "id": "matrix_v", "label_zh": "传输的数据类别及其处理特征", "label_fr": "Catégories d'informations", "pdf_key": "", "kind": "matrix",
                    "fact": None, "required": True, "options": None, "option_labels_zh": None, "max_chars": None, "matrix": matrix,
                    "question_zh": "本次传输涉及哪些类别的数据？每一类说明涉及的人群、来源、接收方、处理目的和保存期限。",
                    "help_zh": "官方表格第五部分是一张按数据类别填的大表。车联网场景常见的类别是：照片、影像，地理位置、行车轨迹，姓名，电话号码；摄像头拍到路人的，涉及人群选“其他人群”。生物识别、健康等属于敏感数据，请据实勾选。",
                }],
            }
            at = next(i for i, sec in enumerate(form["sections"]) if sec["id"] == "VI")
            form["sections"].insert(at, section_v)
        # 没有控件的表（kind=pdf_fixed）：栏位就是目录里的 boxes
        keys = set(form["boxes"]) if form["kind"] == "pdf_fixed" else {f.key for f in list_fields(data)}
        used: set[str] = set()
        for section in form["sections"]:
            for it in section["items"]:
                if it["kind"] == "matrix":
                    from app.forms.f118_matrix import _row_keys

                    targets = [k for r in it["matrix"]["rows"] for k in _row_keys(r)]
                else:
                    targets = list((it["options"] or {}).values()) if it["kind"] in ("radio", "check") else [it["pdf_key"], *([it["pdf_key2"]] if it.get("pdf_key2") else [])]
                for t in targets:
                    assert t in keys, (it["id"], t)
                    assert t not in used, ("同一控件被两个栏目使用", it["id"], t)
                    used.add(t)
        form["controls_total"], form["controls_mapped"] = len(keys), len(used)
        form["sha256"] = hashlib.sha256(data).hexdigest()
        form["bytes"] = len(data)
    (HERE / "forms.json").write_text(json.dumps({"forms": forms}, ensure_ascii=False, indent=1), encoding="utf-8")
    print("forms", [f["code"] for f in forms], "items", sum(len(s["items"]) for f in forms for s in f["sections"]))


if __name__ == "__main__":
    main()
