# ruff: noqa: E501  编制依据是长句数据
"""Where each standard document's structure comes from, stated on the document itself.

A document is either laid out on an official template (the Chinese outbound-data forms published
by the Cyberspace Administration) or, where no official template exists, on the most demanding
public standard (the EU's). Saying which is part of the document, so nobody mistakes a document
built on the EU standard for an official form. The lines are fixed text per document code; the one
thing that depends on the country is whether the catalogue of official forms holds a form for it.
"""

from __future__ import annotations

import logging
import re

from app.analysis.workflow_engine import COUNTRY_NAMES_ZH

logger = logging.getLogger(__name__)

_CN_GUIDE = "国家网信办《数据出境安全评估申报指南（第三版）》（2025-06-27 发布）"

_FIXED: dict[str, list[str]] = {
    "declaration": [],
    "risk_report": [],
    "contract": [
        "条款结构参照欧盟委员会标准合同条款（SCC，实施决定（EU）2021/914）编制：传输详情、保障义务、技术与组织措施、接收国法律与政府调取、终止；不是任何国家的官方范本。",
    ],
    "dpia": [
        "按欧盟 GDPR 第 35 条第 7 款评估要素及欧洲数据保护委员会（EDPB）《数据保护影响评估（DPIA）标准范本》（2026 年第 1.0 版）编制（评估概览、处理活动系统性描述、合法性基础与基本原则、必要性与最小侵害替代方案、风险评估与安全防控措施、数据保护官及数据主体意见、审查决定），同步支持中文核对底稿与英文官方原表导出；目的国没有统一官方原表时采用这一标准。",
    ],
    "lia": [
        "参照欧盟 GDPR 第 6 条第 1 款（f）项合法利益的三步测试（目的、必要性、平衡）编制；目的国没有统一官方原表时采用这一标准。",
    ],
    "tia": [
        "按欧洲数据保护委员会（EDPB）《关于补充措施的建议 01/2020》的六个步骤编制；目的国没有统一官方原表时采用这一标准。",
    ],
    "admission": [
        "办理事项、环节与材料依据目的国法规条文整理，每项附条文原文，见下文列出的条文。",
    ],
}

_ASEAN = {"SG": "数据进入新加坡的，可另行使用东盟示范合同条款（ASEAN MCC）作为传输工具。"}


CN = "CN"
# 出入境国家不限定：用户说出的任何国家都要认得出来（法规库里有没有它的条文是另一回事，由库里的数据决定）。
# 需求文档列出的国家和地区 + 常见目的地；新增国家时在这里补一行名称即可
WORLD = {
    "EU": "欧盟", "DE": "德国", "FR": "法国", "IT": "意大利", "ES": "西班牙", "NL": "荷兰", "BE": "比利时",
    "SE": "瑞典", "PL": "波兰", "AT": "奥地利", "IE": "爱尔兰", "PT": "葡萄牙", "CZ": "捷克", "HU": "匈牙利",
    "DK": "丹麦", "FI": "芬兰", "GR": "希腊", "RO": "罗马尼亚", "BG": "保加利亚", "SK": "斯洛伐克", "SI": "斯洛文尼亚",
    "HR": "克罗地亚", "LT": "立陶宛", "LV": "拉脱维亚", "EE": "爱沙尼亚", "LU": "卢森堡", "CY": "塞浦路斯",
    "GB": "英国", "CH": "瑞士", "NO": "挪威", "RU": "俄罗斯", "TR": "土耳其", "UA": "乌克兰", "BY": "白俄罗斯", "RS": "塞尔维亚",
    "JP": "日本", "KR": "韩国", "TH": "泰国", "MY": "马来西亚", "ID": "印度尼西亚", "VN": "越南", "PH": "菲律宾",
    "BN": "文莱", "KH": "柬埔寨", "LA": "老挝", "MM": "缅甸", "MN": "蒙古",
    "IN": "印度", "PK": "巴基斯坦", "BD": "孟加拉国", "LK": "斯里兰卡", "NP": "尼泊尔",
    "KZ": "哈萨克斯坦", "UZ": "乌兹别克斯坦", "KG": "吉尔吉斯斯坦", "TJ": "塔吉克斯坦", "TM": "土库曼斯坦",
    "AE": "阿联酋", "SA": "沙特阿拉伯", "QA": "卡塔尔", "KW": "科威特", "OM": "阿曼", "BH": "巴林",
    "IL": "以色列", "JO": "约旦", "IQ": "伊拉克", "IR": "伊朗", "DZ": "阿尔及利亚", "LY": "利比亚",
    "ZA": "南非", "NG": "尼日利亚", "KE": "肯尼亚", "ET": "埃塞俄比亚",
    "US": "美国", "CA": "加拿大", "MX": "墨西哥", "BR": "巴西", "AR": "阿根廷", "CL": "智利", "PE": "秘鲁", "CO": "哥伦比亚",
    "AU": "澳大利亚", "NZ": "新西兰", "HK": "中国香港", "MO": "中国澳门",
}
ALIASES = {
    "印尼": "ID", "沙特": "SA", "阿拉伯联合酋长国": "AE", "大韩民国": "KR", "香港": "HK", "澳门": "MO",
    "中华人民共和国": "CN", "新加坡共和国": "SG", "摩洛哥王国": "MA", "阿拉伯埃及共和国": "EG",
    "突尼斯共和国": "TN", "黎巴嫩共和国": "LB", "德意志联邦共和国": "DE", "法兰西共和国": "FR",
    "意大利共和国": "IT", "西班牙王国": "ES", "荷兰王国": "NL", "比利时王国": "BE", "瑞典王国": "SE",
    "波兰共和国": "PL", "奥地利共和国": "AT", "爱尔兰共和国": "IE", "葡萄牙共和国": "PT",
    "大不列颠及北爱尔兰联合王国": "GB", "瑞士联邦": "CH", "挪威王国": "NO", "俄罗斯联邦": "RU",
    "土耳其共和国": "TR", "日本国": "JP", "泰王国": "TH", "印度尼西亚共和国": "ID",
    "越南社会主义共和国": "VN", "菲律宾共和国": "PH", "印度共和国": "IN", "孟加拉人民共和国": "BD",
    "吉尔吉斯共和国": "KG", "哈萨克斯坦共和国": "KZ", "卡塔尔国": "QA", "科威特国": "KW", "阿曼苏丹国": "OM",
    "以色列国": "IL", "阿尔及利亚民主人民共和国": "DZ", "尼日利亚联邦共和国": "NG", "南非共和国": "ZA",
    "美利坚合众国": "US", "墨西哥合众国": "MX", "巴西联邦共和国": "BR", "阿根廷共和国": "AR",
    "哥伦比亚共和国": "CO", "秘鲁共和国": "PE", "澳大利亚联邦": "AU", "欧洲联盟": "EU",
    "香港特别行政区": "HK", "澳门特别行政区": "MO",
}
NAMES = {**COUNTRY_NAMES_ZH, CN: "中国", **WORLD}


def country_of(text: str | None) -> str | None:
    """The country a user named (“中国”, “摩洛哥”, “德国”, or its code); None if not named or ambiguous.
    A name inside a longer one (“中国” in “中国香港”, “印度” in “印度尼西亚”) counts as the longer one."""
    text = str(text or "").strip()
    found = {c: n for c, n in NAMES.items() if n in text}
    found.update({c: a for a, c in ALIASES.items() if a in text and c not in found})
    found = {c: n for c, n in found.items() if not any(n != o and n in o for o in found.values())}
    if not found:
        # 国家代码：只认整句就是代码，或“摩洛哥 (MA)”这种括注；不在长句里找两个字母，免得误认
        bare = text.upper().strip("()（） ")
        if bare in NAMES:
            return bare
        boxed = re.search(r"[(（]\s*([A-Z]{2})\s*[)）]", text.upper())
        return boxed.group(1) if boxed and boxed.group(1) in NAMES else None
    return next(iter(found)) if len(found) == 1 else None


def official_forms(country: str | None, role: str, sensitive: str = "") -> list[dict]:
    """The official forms the catalogue holds for one end of the flow. Both ends declare the
    processing they carry out in their own country (the sender collected the data there); the
    sending end also files the transfer form. Where the country has a separate prior-authorisation
    form for sensitive data (Morocco F112 against the ordinary declaration F211), ``sensitive``
    (“是”/“否”, from the project) picks one; not yet answered, both are offered."""
    if not country:
        return []
    from app.analysis.cn_route import _yes as _cn_yes
    from app.forms.service import CatalogError, catalog

    # 申报表只对应发出国的出境申报；处理申报/事先授权是属地准入事项（见 processing_forms）
    wanted = ("export",) if role == "source" else ()
    answer = (sensitive or "").strip()
    yes_val = _cn_yes(answer, "sensitive_pi")
    flag = "sensitive" if yes_val is True else "not_sensitive" if yes_val is False else ""
    try:
        forms = [f for f in catalog().values() if f["country"] == country and f["direction"] in wanted]
    except CatalogError:
        logger.warning("官方表格目录与存档文件不一致，略去官方表格一行", exc_info=True)
        return []
    forms = [f for f in forms if not flag or f.get("applies_if", flag) == flag]
    return sorted(forms, key=lambda f: f["direction"] != "export")  # 转移申请在前


def processing_forms(country: str | None) -> list[dict]:
    """The official forms for processing data in ``country`` (declaration, prior authorisation):
    local admission items, not the export declaration."""
    if not country:
        return []
    from app.forms.service import CatalogError, catalog

    try:
        return [f for f in catalog().values() if f["country"] == country and f["direction"] == "processing"]
    except CatalogError:
        logger.warning("官方表格目录与存档文件不一致", exc_info=True)
        return []


def _end_lines(code: str, country: str | None, role: str) -> list[str]:
    """What applies at one end of the flow. ``role``: "source" (data leaves) or "recipient"."""
    if not country:
        return []
    name = NAMES.get(country, country)
    side = "发出端" if role == "source" else "接收端"
    if code in ("declaration", "risk_report") and country == CN and role == "source":
        template = "附件 3《数据出境安全评估申报表》" if code == "declaration" else "附件 4《数据出境风险自评估报告》"
        return [f"{side}（{name}）：适用法定申报表（{_CN_GUIDE}{template}），本文件直接按该官方制式版式填写并导出。"]
    if code == "declaration":
        forms = official_forms(country, role)
        if forms:
            return [
                f"{side}（{name}）：适用法定申报表《{f['name_zh']}》（{f['authority_zh']}，{f['version']}），在项目的申报表一栏填写并导出。"
                for f in forms
            ]
    if code in ("declaration", "risk_report") and role == "recipient":
        # 出境申报和出境风险评估由数据发出方向发出国办理；接收国这一端要办的处理申报、许可属于属地准入
        if code == "declaration" and processing_forms(country):
            return [f"{side}（{name}）：在该国处理这些数据要办的申报或授权（有官方表格）见“海外属地准入”。"]
        return []
    if code in ("declaration", "risk_report"):
        return [f"{side}（{name}）：该国没有可用的官方申报表格，本文件参照欧盟标准编制；该国要求见文末所列条文，以主管机构实际要求为准。"]
    return []


def basis_lines(code: str, country_code: str, values: dict | None = None) -> list[str]:
    """The “编制依据” lines: the fixed standard of the document, then, for the documents that have
    an official form or template at some end of the data flow, what applies at each end.
    The ends are what the user named (source and recipient); not named, the assessed country."""
    values = values or {}
    source = country_of(values.get("source_country"))
    recipient = country_of(values.get("recipient_country")) or (None if source else country_code)
    lines = list(_FIXED.get(code, []))
    if code in ("declaration", "risk_report"):
        lines = [x for x in lines if not x.startswith(("栏目参照", "章节参照"))]
        lines += _end_lines(code, source, "source") + _end_lines(code, recipient, "recipient")
        if not source and not values.get("recipient_country"):
            lines.append("数据流向的两端（发出国、接收国）尚未填写，填写后这里会按两端各自适用的官方原表或标准说明依据。")
    if code == "dpia" and source == CN:
        lines = [
            "数据从中国发出：本报告直接在国家网信办《个人信息出境标准合同备案指南（第二版）》（2024-03-22）附件 5《个人信息保护影响评估报告（出境版）》原件上填写（版式、栏目、说明原样），"
            "用于标准合同备案；走数据出境安全评估路径的，对应的是风险评估报告（申报指南附件 4）。"
        ]
    if code == "contract":
        if source == CN:
            lines = [
                "数据从中国发出：合同采用国家网信办《个人信息出境标准合同》范本全文（《个人信息出境标准合同办法》附件，2023 年 2 月，官方原文），"
                "办法要求“应当严格按照附件订立”；本文件只在附录一等已知信息处填入评估事实，其余空白栏留给双方补填。"
                "该范本适用于通过标准合同路径出境个人信息的情形（个人信息出境的数量与敏感程度是否落入标准合同路径，以《促进和规范数据跨境流动规定》为准；"
                "涉及重要数据或达到规定规模的，需走数据出境安全评估，见申报表）。生效后 10 个工作日内向所在地省级网信部门备案，并附个人信息保护影响评估报告。"
            ]
        if country_code in _ASEAN or (recipient in _ASEAN):
            lines.append(_ASEAN[country_code if country_code in _ASEAN else recipient])
    return lines


def basis_kind(code: str, values: dict | None = None) -> tuple[str, str]:
    """(kind, label) of what a document stands on at this route: "official" (an official template
    or form is used), "eu" (no official template: the EU standard), or "law" (laid out from the
    destination country's clauses). Shown next to each document so nobody mistakes the EU-standard
    documents for official forms."""
    values = values or {}
    source = country_of(values.get("source_country"))
    if code == "dpia" and source == CN:
        return "official", "中国官方原表"
    if code in ("dpia", "lia", "tia"):
        return "eu", "欧盟标准方法"
    if code == "admission":
        return "law", "按目的国法规条文整理"
    if code == "remediation":
        return "eu", "通用结构"
    if source == CN and code in ("declaration", "risk_report", "contract", "dpia"):
        return "official", "中国官方原表"
    if code == "declaration":
        recipient = country_of(values.get("recipient_country"))
        ends = [c for c, role in ((source, "source"), (recipient, "recipient")) if official_forms(c, role)]
        if ends:
            return "official", "、".join(NAMES.get(c, c) for c in ends) + "官方表格"
    return "eu", "参照欧盟标准"
