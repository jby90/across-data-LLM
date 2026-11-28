# ruff: noqa: E501  规则是长句和长正则
"""The rules that decide what a compliance document says about the law, the same for every country
and every direction of the data flow.

1. Direction: the main legal basis is the law of the country the data leaves (its outbound rules);
   the receiving country's law is cited only where it binds the receiver (processing licence or
   registration, breach notification, access by public authorities) — never its own outbound rules,
   which govern data leaving that country, not data arriving.
2. The route: before the documents, the sending country's law decides the route — for China the
   thresholds of the 2024 provisions (``cn_route``); for any other country whether the receiving
   country is on the sending country's adequacy list (read from the list clause in the library),
   and so whether the transfer rests on adequacy or on the exceptions / an authorisation.
3. Only clauses that apply: not a clause that exempts transfers without personal information when
   personal information is transferred; not marketing, natural-person-applicant, CCTV / access-gate
   clauses for a vehicle manufacturer; not the purpose, commencement, transitional or the
   authority's own time-limit clauses; each clause once per document.
4. The sector's core regulation of the sending country (the vehicle-data rules) is always among the
   basis when the library holds it.
"""

from __future__ import annotations

import copy
import re

from sqlalchemy import text as sql
from sqlalchemy.ext.asyncio import AsyncSession

from app.analysis.clause_gate import law_name
from app.analysis.document_basis import CN, NAMES, country_of

_LATEST_CLAUSES_CACHE: dict[tuple[str, bool], list[tuple]] = {}
_TRANSFER_CLAUSE_CACHE: dict[str, dict | None] = {}
_PINNED_CACHE: dict[tuple, list[dict]] = {}

# 接收国条文只引约束接收方的：处理许可/登记、泄露通报、公权力调取
_BINDS_RECEIVER = re.compile(
    r"许可证|执照|(取得|获得|申请|颁发|持有)[^。；]{0,8}(许可|授权|批准)|登记册|注册|备案|泄露|泄漏|违规事件|安全事件|通报|报告.{0,6}(中心|委员会|主管)|调取|调阅|公共当局|主管(机关|部门).{0,10}(要求|提供|查阅|访问)|国家安全(机关|机构)|执法|"
    r"licen[cs]e|authori[sz]ation|registration|breach|notif|government|public authorit|law enforcement",
    re.IGNORECASE,
)
# 出境规定（向境外提供、跨境传输）：只在数据从这个国家发出时才适用
_OUTBOUND = re.compile(
    r"向境外|出境|境外接收方|跨境(传输|提供|流动)|(转移|传输|提供|发送|披露)[^。；]{0,14}(境外|国外|外国|第三国|其他国家)|(境外|国外|外国|第三国)[^。；]{0,14}(转移|传输|提供|发送)|向[^。；]{0,14}(该国|境外|外国|国外|他国|第三国|另一国家?)[^。；]{0,10}(转移|传输|提供)|(传输|转移)至[^。；]{0,30}国家|(传输|转移|共享|交换|处理)[^。；]{0,12}至[^。；]{0,30}(国家|地区)|位于[^。；]{0,20}(境外|外国|国外)|境外的另一|"
    r"transfer[^.;]{0,30}(outside|abroad|foreign|third countr)|à l.étranger|vers un pays|hors (du territoire|de)|국외|外国にある第三者|трансгранич",
    re.IGNORECASE,
)
# 不作依据：立法目的、施行日期、过渡、主管部门自己的工作安排
_PURPOSE = re.compile(r"^\s*(第[^条]{1,8}条\s*)?(为了|为)[^。；]{2,200}(制定本|特制定|特制订|制定本办法|制定本规定|制定本法)")
_COMMENCE = re.compile(
    r"(本(法|法律|条例|规定|办法|决定|法令|规章|细则|指令|命令)|this (act|law|regulation|order|decree)|la présente (loi|décret|décision)|le présent (décret|arrêté))[^。；.]{0,40}"
    r"(施行|生效|起实施|公布之日|公布次日|在官方公报上公布|entre en vigueur|come into (force|operation)|takes? effect|sera publié)",
    re.IGNORECASE,
)
_TRANSITION = re.compile(r"过渡|本(法|条例|规定)(公布|施行|生效)前|自[^。；]{0,20}设立之日起[^。；]{0,20}期限[^。；]{0,30}(符合|合规|调整)|最长[^。；]{0,6}年的期限|废止|予以撤销")
_AUTHORITY_OWN = re.compile(r"(安全评估|评估|审查|审核)过程中，?发现|^\s*(第[^条]{1,8}条\s*)?(国家网信部门|各地网信部门|省级网信部门|网信部门|国务院有关部门|中心|委员会|国家委员会|该局|主管(机关|部门))(应当|应|负责|可以)?(加强|组织|制定|在|自|会同|定期|对)")
# 车企用不上的条文
_NOT_FOR_MAKERS = re.compile(r"政务数据|电子政务|国家机关(委托|应当|不得|收集)|协会、工会|俱乐部|数字证据|电子证据|解散或合并|解散、合并|主席和成员|(成员|工作人员)[^。；]{0,12}(保守|保密)|视频记录|视频监控|公共场所[^。；]{0,12}监控|(电信|网络)?运营商[^。；]{0,20}(日志|通信记录)|服务提供者[^。；]{0,12}(保存|留存)[^。；]{0,10}(日志|流量数据|通信数据)|直接营销|电子营销|营销活动|自然人(取得|申请|获得)[^。；]{0,6}(许可|批准|证书)|公共场所[^。；]{0,8}(视觉)?监控|视频监控|门禁|个人或家庭事务|为个人或家庭|驾驶学校|驾校|驾驶教练|教练车|计价器|出租汽车计价|行人违法|畜力车")
# 涉及个人信息时不引“不含个人信息即免”的条文
_RECEIVER_LAW = re.compile(r"个人(数据|信息)|数据主体|权利|投诉|申诉|诉讼|赔偿|监管|主管部门|委员会|中心|调查|检查")
_ONWARD = re.compile(r"跨境|向境外|出境|境外传输|转移到?国外|向外国|传输至境外|transfer", re.IGNORECASE)


def inbound_only(entries: list, source: str | None, recipient: str | None, target: str | None, values: dict[str, str]) -> list:
    """The receiving country's own outbound items (its transfer-abroad licence) govern data leaving
    it, not data arriving: listed only when the receiver says it passes the data on to a third country."""
    if not target or target != recipient or source == recipient:
        return entries
    if str(values.get("onward_provision", "")).strip().startswith("有"):
        return entries
    return [e for e in entries if not _ONWARD.search(f"{getattr(e, 'name_zh', '')} {getattr(e, 'summary_zh', '') or ''}"[:80])]


# 准入事项三道门槛（二审第 20 条）：适用前提成立、申请主体与企业角色匹配、与项目场景相关（依据不是序言）
_LATER_STAGE = re.compile(r"超(过)?许可|超出许可|修改许可|变更|续期|延期|过户|所有权转移|转让|继承|赠与|撤销|注销|补发|遗失|换发")
_ROLE_MISMATCH = re.compile(r"51\s*%|%\s*51|国籍|籍(的)?(自然人|股东|合伙人)|自然人(申请|取得)|网站(负责人|控制者)|个人机构|境内提供[^，。；]{0,12}服务的(公司|机构)|提供物联网服务")
_PREAMBLE_BASIS = re.compile(r"^\s*(鉴于|经查阅|经审阅|兹|根据宪法|以人民名义)")


def _first_clause(text: str, limit: int = 80) -> str:
    """The first whole clause of a condition (up to “；” or “。”), never cut inside a number."""
    first = re.split(r"[；;。]", text or "", maxsplit=1)[0].strip()
    if len(first) <= limit:
        return first
    cut = max(first.rfind(m, 0, limit) for m in ("，", "、", ","))
    return first[:cut] if cut > limit // 2 else first


def admission_gate(entry) -> str:
    """Why an admission item is only “视情况” for a vehicle maker's project ("" when it is needed)."""
    name = f"{getattr(entry, 'name_zh', '')}"
    profile = getattr(entry, "profile", None) or {}
    need = str(profile.get("eligibility") or "")
    m = _LATER_STAGE.search(name)
    if m:
        return f"取得许可或登记后发生“{m.group(0)}”时才办"
    if _ROLE_MISMATCH.search(need) or _ROLE_MISMATCH.search(name):
        return f"申请人须为：{_first_clause(need)}；车企不一定符合" if need else "申请人条件与车企的角色不一定相符"
    if _PREAMBLE_BASIS.search(str(getattr(entry, "evidence_quote", "") or "")):
        return "参考条文是法规序言，没有规定须办理"
    if _OPTIONAL_ACT.search(name):
        return f"只有需要{_OPTIONAL_ACT.search(name).group(0)}时才办"
    if _TO_THIRD_PARTY.search(name):
        return "只有把数据提供给第三方时才办；数据只由车企自己的云平台处理的，不需要办"
    return ""


# 只在做了某件事（向第三方提供数据）时才要办的事项
_TO_THIRD_PARTY = re.compile(r"向第三方(提供|通信|披露)")
_OPTIONAL_ACT = re.compile(r"豁免|免除|减免|延期|续期|复议|申诉")


def applicant_type(entry) -> str:
    """Who files, as a kind of entity (the conditions go in a column of their own)."""
    need = str((getattr(entry, "profile", None) or {}).get("eligibility") or "")
    if re.search(r"当地|本地|境内|在[^，。]{1,6}(登记|注册|设立)|[一-鿿]{2,4}公司|商业登记", need):
        return "当地法人或其授权代表"
    return "数据控制者" if getattr(entry, "category", "") == "data_filing" else "车辆或设备的进口商、制造商"


# 条文开头就点名的义务主体不是车企（电信运营商、互联网接入商、认证服务商、银行……）：不作车企的依据
_OTHER_SUBJECT = re.compile(r"^\s*(第[^条]{1,8}条\s*[/／]?\s*)?((公共)?电信(网络)?运营商|互联网接入提供商|网络运营商|认证服务提供者|电子认证服务|信任服务提供者|银行|金融机构|保险公司|律师|医疗机构|公证)")
_CN_PLACES = re.compile(r"上海|北京|深圳|广州|杭州|苏州|天津|重庆|南京|武汉|成都|西安|合肥|长春|宁波|无锡|厦门|青岛|郑州|长沙|佛山|东莞|（中国）|\(中国\)|中国")


def entity_country(name: str | None) -> str | None:
    """The country an entity's name places it in (“（埃及）”“上海”), None when the name does not say."""
    n = name or ""
    for code, zh in NAMES.items():
        if code != CN and zh and (f"（{zh}）" in n or f"({zh})" in n or n.startswith(zh)):
            return code
    return CN if _CN_PLACES.search(n) else None


def entity_for(name: str | None, country: str | None) -> str:
    """The name if it can be the party in ``country`` (same country, or the name does not say);
    "" when it is an entity of another country (a Shanghai company cannot be the Moroccan sender)."""
    if not name or not country:
        return name or ""
    where = entity_country(name)
    return "" if where and where != country else name


_SCENE_TITLE = re.compile(r"运营一般条件|电信运营商|接入网运营|公共电信网络|视频监控|视觉监控|门禁|生物识别|健康|医疗|科研|研究|统计|雇员|工作场所|直接营销|儿童")
_FTZ = re.compile(r"自由贸易试验区|自贸区|自贸试验区")
_SCENE_ONLY = re.compile(r"视频记录|扣押|数字证据|协会、工会|健康数据|医疗|遗传|视频监控|视觉监控|门禁|生物识别|工作场所|雇员|劳动关系|儿童|未成年人|直接营销|历史、统计|统计或科学|科学目的|科学研究")
_NO_PI_EXEMPT = re.compile(r"不(含|包含|涉及)个人信息|未包含个人信息|不包含[^。；]{0,8}个人信息[^。；]{0,10}(免|无需|不需)")
_NO_PERSONAL = re.compile(r"不(涉及|含|包含|处理|传输)(任何)?个人(数据|信息)|无个人(数据|信息)|没有个人(数据|信息)|^\s*(0|零)\s*(人|个)?\s*$")

# 各国名称在条文里的写法（中文、法文、英文、阿拉伯文及主要当地语种）：判断接收国是否在充分保护名单里
_NAME_FORMS = {
    "CN": ("中国", "中华人民共和国", "Chine", "China", "الصين"),
    "EG": ("埃及", "Égypte", "Egypt", "مصر"),
    "MA": ("摩洛哥", "Maroc", "Morocco", "المغرب"),
    "TN": ("突尼斯", "Tunisie", "Tunisia", "تونس"),
    "LB": ("黎巴嫩", "Liban", "Lebanon", "لبنان"),
    "SG": ("新加坡", "Singapour", "Singapore", "سنغافورة"),
    "EU": ("欧盟", "欧洲联盟", "Union européenne", "European Union", "الاتحاد الأوروبي"),
    "DE": ("德国", "Allemagne", "Germany", "Deutschland", "ألمانيا"),
    "FR": ("法国", "France", "فرنسا"),
    "IT": ("意大利", "Italie", "Italy", "Italia", "إيطاليا"),
    "ES": ("西班牙", "Espagne", "Spain", "España", "إسبانيا"),
    "NL": ("荷兰", "Pays-Bas", "Netherlands", "Nederland", "هولندا"),
    "BE": ("比利时", "Belgique", "Belgium", "België", "بلجيكا"),
    "SE": ("瑞典", "Suède", "Sweden", "Sverige", "السويد"),
    "PL": ("波兰", "Pologne", "Poland", "Polska", "بولندا"),
    "AT": ("奥地利", "Autriche", "Austria", "Österreich"),
    "IE": ("爱尔兰", "Irlande", "Ireland"),
    "GB": ("英国", "Royaume-Uni", "United Kingdom", "Britain", "المملكة المتحدة"),
    "CH": ("瑞士", "Suisse", "Switzerland", "Schweiz", "سويسرا"),
    "NO": ("挪威", "Norvège", "Norway"),
    "RU": ("俄罗斯", "Russie", "Russia", "Российская Федерация", "روسيا"),
    "TR": ("土耳其", "Turquie", "Turkey", "Türkiye", "تركيا"),
    "JP": ("日本", "Japon", "Japan", "اليابان"),
    "KR": ("韩国", "大韩民国", "Corée du Sud", "Republic of Korea", "South Korea", "대한민국", "كوريا"),
    "TH": ("泰国", "Thaïlande", "Thailand", "ประเทศไทย"),
    "MY": ("马来西亚", "Malaisie", "Malaysia"),
    "ID": ("印度尼西亚", "印尼", "Indonésie", "Indonesia"),
    "VN": ("越南", "Viêt Nam", "Vietnam", "Việt Nam"),
    "PH": ("菲律宾", "Philippines"),
    "IN": ("印度", "Inde", "India", "الهند"),
    "PK": ("巴基斯坦", "Pakistan", "باكستان"),
    "BD": ("孟加拉国", "Bangladesh"),
    "KZ": ("哈萨克斯坦", "Kazakhstan"),
    "KG": ("吉尔吉斯斯坦", "Kirghizistan", "Kyrgyzstan"),
    "AE": ("阿联酋", "阿拉伯联合酋长国", "Émirats arabes unis", "United Arab Emirates", "الإمارات"),
    "SA": ("沙特阿拉伯", "沙特", "Arabie saoudite", "Saudi Arabia", "المملكة العربية السعودية", "السعودية"),
    "QA": ("卡塔尔", "Qatar", "قطر"),
    "KW": ("科威特", "Koweït", "Kuwait", "الكويت"),
    "OM": ("阿曼", "Oman", "عمان"),
    "IL": ("以色列", "Israël", "Israel"),
    "DZ": ("阿尔及利亚", "Algérie", "Algeria", "الجزائر"),
    "ZA": ("南非", "Afrique du Sud", "South Africa"),
    "NG": ("尼日利亚", "Nigéria", "Nigeria"),
    "US": ("美国", "États-Unis", "United States", "USA", "الولايات المتحدة"),
    "CA": ("加拿大", "Canada", "كندا"),
    "MX": ("墨西哥", "Mexique", "Mexico", "México"),
    "BR": ("巴西", "Brésil", "Brazil", "Brasil"),
    "AR": ("阿根廷", "Argentine", "Argentina"),
    "CL": ("智利", "Chili", "Chile"),
    "CO": ("哥伦比亚", "Colombie", "Colombia"),
    "PE": ("秘鲁", "Pérou", "Peru", "Perú"),
    "AU": ("澳大利亚", "Australie", "Australia"),
    "NZ": ("新西兰", "Nouvelle-Zélande", "New Zealand"),
}
_LIST_CLAUSE = re.compile(r"(下列|以下)(国家|国)[^。]{0,60}(充分|适当)[^。]{0,40}保护|充分保护[^。]{0,10}(国家)?名单")


def ends(values: dict[str, str], assessed: str | None = None) -> tuple[str | None, str | None]:
    """(sending country, receiving country) as codes; the assessed country stands for the receiving
    end only when the user named neither end."""
    source = country_of(values.get("source_country"))
    recipient = country_of(values.get("recipient_country")) or (None if source else assessed)
    return source, recipient


def overseas(source: str | None, recipient: str | None) -> str | None:
    """Where the business is carried on for market admission: the end that is not China (the market
    a Chinese maker enters, or the country a foreign subsidiary sends data from); neither being
    China, the sending country, where the vehicles run and the data is generated."""
    for c in (source, recipient):
        if c and c != CN:
            return c
    return source or recipient


def personal_involved(values: dict[str, str]) -> bool:
    """Whether the transfer carries personal information, unless the enterprise said it does not."""
    from app.analysis.cn_route import has_no_personal_data

    return not has_no_personal_data(
        str(values.get("data_volume", "")),
        str(values.get("sensitive_pi", "")),
        str(values.get("data_types", "")),
    )


def _text(c: dict) -> str:
    zh = str(c.get("quote_zh") or "").strip()
    return zh if zh not in ("", "(未提供译文)", "（未提供译文）") else str(c.get("quote_original") or "")


# 修订别的法的条文（“第48J条的修订…现予修订——删除第(3)款，并替换为”）：规则以合订本为准，不引修订条文本身
_AMENDING = re.compile(r"现予(修订|修改|废除|删除|替换)|予以(修订|修改)[^。]{0,10}(如下|：|:)|(删除|替换)第[^。]{1,14}(款|项|条)|条的修订")


def not_a_basis(c: dict) -> bool:
    """Purpose, commencement, transitional and the authority's own work clauses: never a basis."""
    t = _text(c)
    head = t[:240]
    if _PURPOSE.search(head) or _AUTHORITY_OWN.search(head) or _AMENDING.search(t[:160]):
        return True
    if len(t) < 400 and (_COMMENCE.search(t) or _TRANSITION.search(t)):
        return True
    return bool(_TRANSITION.search(head) and re.search(r"期限|调整|合规|废止", head))


_ALL_DIGITS_MAP = str.maketrans(
    "٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹০১২৩৪৫৬৭৮৯๐๑๒๓๔๕๖๗๘๙",
    "0123456789012345678901234567890123456789",
)


def label_ok(raw: str | None, text: str | None) -> bool:
    """Whether a clause's number is a real article number: a “第N条” the label reads as, or a
    section-style number (“26.1”, “3.15.1”) that the clause text itself starts with. A number the
    splitter gave a chunk (“113.1” on a paragraph that starts “(b) …”), a page (“第1页”) or nothing
    is not, and such a clause is not cited by number."""
    from app.knowledge.reading import article_label

    raw = (raw or "").strip()
    if not raw or re.fullmatch(r"第?\s*\d+\s*页", raw):
        return False
    label = article_label(raw)
    if re.fullmatch(r"第[0-9一二三四五六七八九十百零]+[A-Za-z]?条(?:之\d+)?|单一条款", label):
        return True
    head = re.sub(r"\s+", " ", (text or "").translate(_ALL_DIGITS_MAP).strip())[:40]
    raw_norm = raw.translate(_ALL_DIGITS_MAP)
    return bool(re.match(r"^(art(icle)?\.?\s*|section\s*|第\s*)?" + re.escape(raw_norm) + r"(?![0-9])", head, re.IGNORECASE))


_SECTION_HEAD = re.compile(r"^\s*(第\s*[0-9一二三四五六七八九十]+\s*(节|章|编|部分)|section\s+\d|chapitre|titre|القسم|الفصل|الباب)", re.IGNORECASE)
_FIRST_ARTICLE = re.compile(r"第\s*([0-9]+)\s*条|\bArt(?:icle|\.)\s*([0-9]+)|المادة\s*\(?([0-9]+)", re.IGNORECASE)
_COMMON_LAW_SECTION_HEAD = re.compile(r"^\s*Section\s+(\d{1,3}[A-Za-z]?)\b", re.IGNORECASE)
_INLINE_ORIG_ARTICLE_RE = re.compile(
    r"(?:^|\n)\s*(?:"
    r"(?i:(?:Article|Art\.|Section|Sec\.|Regulation)\s+(\d{1,3}[A-Za-z]?)\b(?![.\d]))"
    r"|(?:المادة|مادة)\s*\(?\s*(\d{1,3})\s*\)?(?=\s|$|[:：.\-–—])"
    r"|มาตรา\s*(\d{1,3})(?!\d)"
    r"|ม\s*า\s*ต\s*ร\s*า\s*(\d{1,3})(?!\d)"
    r"|(?:ধারা\s*)?(\d{1,3})\s*[৷|]\s+\S"
    r"|(\d{1,3}[A-Z]?)\s+(?!(?:Working|Calendar|Business|Days|Months|Years|Hours|Percent)\b)[A-Z“\"][A-Za-z\s“”\"',\-]{3,75}\n"
    r")"
)
_INLINE_ZH_ARTICLE_RE = re.compile(
    r"^\s*(?:第[一二三四五六七八九十百\d]+[章节编部分][^\n]{0,40}\n\s*)?第\s*(\d{1,3}[A-Za-z]?)\s*条(?=\s|[—–\-:：，。]|[\u4e00-\u9fff])"
)
_GUIDE_PRINCIPLE_RE = re.compile(r"^\s*Principle\s*\(([a-g])\)\s*[:：]", re.IGNORECASE)


_GUIDE = re.compile(r"指南|指引|框架|标准|基准|规范要求|表格|申请表|清单|附件|守则|计划|咨询文件|guide|guideline|framework|standard|form|code of practice", re.IGNORECASE)


def _extract_inline_label(
    text: str | None,
    zh: str | None,
    title: str | None,
    section_path: str | None = "",
) -> str | None:
    """When ``clause.clause_number`` was omitted at parse time, extract an explicit statutory
    article/section identifier only if the clause text or its parsed section_path declares one."""
    norm_orig = (text or "").translate(_ALL_DIGITS_MAP)
    norm_zh = (zh or "").translate(_ALL_DIGITS_MAP)
    if "....." in norm_orig[:240]:
        return None
    if section_path:
        sp_norm = section_path.translate(_ALL_DIGITS_MAP)
        sp_hits = re.findall(
            r"\b(?:Article|Art\.|Section|Sec\.|Regulation)\s+(\d{1,3}[A-Za-z]?)\b(?![.\d])"
            r"|(?:المادة|مادة)\s*\(?\s*(\d{1,3})\s*\)?",
            sp_norm,
            flags=re.IGNORECASE,
        )
        if sp_hits:
            last_pair = sp_hits[-1]
            sp_num = next((g for g in last_pair if g), None)
            if sp_num and not re.fullmatch(r"(?:19|20)\d{2}", sp_num):
                return f"第{sp_num}项" if (title and _GUIDE.search(title)) else f"第{sp_num}条"
    m_orig = _INLINE_ORIG_ARTICLE_RE.search(norm_orig[:260])
    if m_orig:
        num = next((g for g in m_orig.groups() if g), None)
        if num and not re.fullmatch(r"(?:19|20)\d{2}", num):
            return f"第{num}项" if (title and _GUIDE.search(title)) else f"第{num}条"
    m_zh = _INLINE_ZH_ARTICLE_RE.match(norm_zh[:180])
    if m_zh:
        num = m_zh.group(1)
        plain_digits = re.sub(r"\D+", "", num)
        orig_nums = set(re.findall(r"\d+", norm_orig[:320]))
        if plain_digits and plain_digits in orig_nums and not re.fullmatch(r"(?:19|20)\d{2}", num):
            return f"第{num}项" if (title and _GUIDE.search(title)) else f"第{num}条"
    if title and _GUIDE.search(title):
        if m_pr := _GUIDE_PRINCIPLE_RE.match(norm_orig.strip()):
            return f"原则({m_pr.group(1).lower()})"
    return None


def real_label(
    raw: str | None,
    text: str | None,
    zh: str | None = "",
    title: str | None = "",
    section_path: str | None = "",
) -> str | None:
    """The article a clause really is, read from its own text: a chunk that starts with a section
    heading (“第2节 … 第31条——……”) is article 31, not “第2条”; Common-Law “Section 26” is “第26条”;
    None when no real article number can be given (the clause is then not cited by number)."""
    from app.knowledge.reading import article_label

    orig_head = re.sub(r"\s+", " ", (text or "").translate(_ALL_DIGITS_MAP).strip())[:160]
    m_cl_sec = _COMMON_LAW_SECTION_HEAD.match(orig_head)
    if m_cl_sec and not re.search(r"\bArt(?:icle|\.)\s*\d", orig_head, re.IGNORECASE):
        sec_num = m_cl_sec.group(1)
        return f"第{sec_num}项" if (title and _GUIDE.search(title)) else f"第{sec_num}条"

    for body in (zh or "", text or ""):
        head = re.sub(r"\s+", " ", body.translate(_ALL_DIGITS_MAP).strip())[:160]
        if head and _SECTION_HEAD.match(head):
            m = _FIRST_ARTICLE.search(head)
            if m:
                return f"第{next(g for g in m.groups() if g)}条"
            if not label_ok(raw, text):
                return None
    if not label_ok(raw, text):
        return _extract_inline_label(text, zh, title, section_path)
    label = article_label(raw) or raw
    # 指南、标准按段落编号（“19.6”“3.15.1”）：写成“第19.6段”；指南、框架、表格里的整数编号写“第N项”，不写“条”
    if re.fullmatch(r"\d+(\.\d+)+", label):
        return f"第{label}段"
    if re.fullmatch(r"\d+(-\d+)+", label):
        return f"第{label}项"
    if title and _GUIDE.search(title) and (m := re.fullmatch(r"第(\d+)条", label)):
        return f"第{m.group(1)}项"
    return label


def applicable(citations: list[dict], values: dict[str, str], code: str, assessed: str | None = None) -> list[dict]:
    """The clauses this document may cite, by direction and by the facts (see the module doc)."""
    source, recipient = ends(values, assessed)
    pi = personal_involved(values)
    out, seen = [], set()
    for c in citations:
        key = str(c.get("clause_id") or "") or (str(c.get("document_title")) + str(c.get("clause_number")))
        if key in seen:
            continue
        t = _text(c)
        side = c.get("side") or ""
        if c.get("core"):  # 出境路径条文、主依据、车企核心法规：已按规则选定
            seen.add(key)
            out.append(c)
            continue
        if code == "tia" and side == "recipient" and source != recipient:
            # TIA 第三节评估接收国：它的数据保护法、监管机构、公权力调取、救济途径都要；它自己的出境规定不要
            title = str(c.get("document_title") or "")
            if (not _OUTBOUND.search(t) and not re.search(r"出境|跨境|向境外", title) and not re.search(r"报送|年度报告|违反本(办法|规定)", t)
                    and (_BINDS_RECEIVER.search(t) or _RECEIVER_LAW.search(t)) and not not_a_basis(c)):
                seen.add(key)
                out.append(c)
            continue
        if code != "admission" and source and recipient and source != recipient and side == "recipient":
            # 接收国一端：只引约束接收方的，绝不引它自己的出境规定
            if _OUTBOUND.search(t) or not _BINDS_RECEIVER.search(t) or re.search(r"报送|年度报告|年报", t):
                continue
        if not_a_basis(c) or _NOT_FOR_MAKERS.search(t) or _OTHER_SUBJECT.search(t[:120]) or (pi and _NO_PI_EXEMPT.search(t)):
            continue
        if _FTZ.search(t) and not _FTZ.search(" ".join(str(v) for v in values.values())):
            continue  # 自贸区负面清单条款：企业确认在自贸区时才引
        if _SCENE_TITLE.search(str(c.get("document_title") or "")) and not _SCENE_ONLY.search(" ".join(str(v) for v in values.values())):
            continue  # 视频监控、门禁、健康研究类法规：场景对不上整份不引
        if code != "tia" and _SCENE_ONLY.search(t[:300]) and not _SCENE_ONLY.search(" ".join(str(v) for v in values.values())):
            continue  # 健康、门禁、视频监控等特定场景的条文：场景对得上才引
        seen.add(key)
        out.append(c)
    # 发出国的条文在前（主依据），接收国约束接收方的条文在后
    out.sort(key=lambda c: 0 if c.get("core") else (1 if c.get("side") != "recipient" or source == recipient else 2))
    return out


_SQL_ELIGIBLE_DOC_VER_CLAUSE = """
  and not exists (
    select 1 from knowledge_change_proposals p
    where p.target_type = 'documents' and p.target_id = d.id
      and p.status in ('pending_review', 'approved')
      and coalesce((p.payload->>'safety_hold')::boolean, false)
  )
  and not exists (
    select 1 from knowledge_change_proposals p
    where p.target_type = 'clauses'
      and p.status in ('pending_review', 'approved')
      and p.payload->'source_parse_revision'->>'scope' = 'whole_source_page'
      and (
        p.target_id = k.id
        or (
          p.payload->'source_parse_revision'->>'version_id' = k.version_id::text
          and (p.payload->'source_parse_revision'->>'page_number')::int >= k.page_start
          and (p.payload->'source_parse_revision'->>'page_number')::int <= k.page_end
        )
      )
  )
  and v.legal_status not in ('repealed', 'draft')
  and v.review_status <> 'rejected'
  and v.version_no = (
    select max(x.version_no) from document_versions x
    where x.document_id = d.id and x.state = 'parsed'
      and x.legal_status <> 'draft'
      and x.review_status <> 'rejected'
  )
"""


_SQL_SINGLE_CURRENT_TR = (
    "(select t.translated_text from clause_translations t "
    "where t.clause_id=k.id and t.is_current and t.quality_check_passed "
    "and t.review_status not in ('translation_failed','not_translated','rejected','stale','superseded') "
    "and not exists (select 1 from clause_translations t2 where t2.clause_id=k.id and t2.id<>t.id "
    "and t2.is_current and t2.quality_check_passed "
    "and t2.review_status not in ('translation_failed','not_translated','rejected','stale','superseded')) "
    "limit 1)"
)


async def core_clauses(session: AsyncSession, country: str | None, side: str) -> list[dict]:
    """The sending country's vehicle-data regulation (its articles on important data and transfers
    abroad), always part of the basis when the library holds it."""
    if not country:
        return []
    rows = (
        await session.execute(
            sql(
                f"""select k.id::text, d.id::text, coalesce(d.title_zh, d.title), d.title, k.clause_number, k.original_text,
                   {_SQL_SINGLE_CURRENT_TR}, k.language
                   from clauses k join document_versions v on v.id=k.version_id and v.state='parsed' join documents d on d.id=v.document_id
                   where d.country_code=:c and d.relevance in ('relevant','related')
                     and (coalesce(d.title_zh, d.title) ~ '汽车数据' or d.title ~* '(vehicle|automobile|véhicule).{{0,20}}(data|données)')
                     {_SQL_ELIGIBLE_DOC_VER_CLAUSE}
                   order by k.ordinal"""
            ),
            {"c": country},
        )
    ).all()
    from app.ingestion.display_text import for_reading
    from app.knowledge.reading import article_label

    out = []
    for cid, did, title_zh, title, number, original, zh, clang in rows:
        is_zh = str(clang or "").lower().startswith("zh")
        if not zh and not is_zh:
            continue
        body = zh or original or ""
        if not re.search(r"重要数据|向境外|出境|境外|transfer|abroad", body, re.IGNORECASE) or not number:
            continue
        out.append({
            "clause_id": cid, "document_id": did, "document_title": law_name(title_zh, did), "original_title": title if title != title_zh else "",
            "clause_number": article_label(number) or number, "quote_original": for_reading(original or ""), "quote_zh": for_reading(zh or (original if is_zh else "")),
            "dimension_code": "D3", "side": side, "core": True,
        })
    # 讲出境的在前，最多三条：核心法规要在每张依据表里出现，但不能挤掉其他依据
    out.sort(key=lambda c: 0 if re.search(r"向境外|出境|境外", c["quote_zh"] or "") else 1)
    return out[:3]


ADEQUACY_SEEN: dict[tuple[str | None, str | None], dict | None] = {}  # 官方表格预填同步读取（forms.router.facts_for_task）


async def warm_adequacy(session: AsyncSession, task) -> None:
    """Look the project's route up once so the official forms can fill what follows from it."""
    facts = {k.removeprefix("doc_"): str(v) for k, v in (task.facts_json or {}).items() if isinstance(v, str)}
    await adequacy(session, country_of(facts.get("source_country")), country_of(facts.get("recipient_country")))


async def adequacy(session: AsyncSession, source: str | None, recipient: str | None) -> dict | None:
    """Whether the receiving country is on the sending country's adequacy list: {listed, law,
    article} from the list clause in the library; None when the library holds no such list."""
    if not source or not recipient or source == CN:
        return None
    if (source, recipient) in ADEQUACY_SEEN:
        return ADEQUACY_SEEN[(source, recipient)]
    found = await _adequacy(session, source, recipient)
    ADEQUACY_SEEN[(source, recipient)] = found
    return found

async def _latest_clauses(session: AsyncSession, country: str, any_relevance: bool = False) -> list[tuple]:
    """(law title, clause number, original, translation) of the country's current versions."""
    from app.knowledge.cache_sync import keeper_active, rebuilding, use_cached

    key = (country, bool(any_relevance))
    s_cache = session.info.setdefault("_latest_clauses", {})
    if key in s_cache and not rebuilding.get():
        return s_cache[key]
    if use_cached(key in _LATEST_CLAUSES_CACHE):
        return _LATEST_CLAUSES_CACHE[key]
    rows = list(
        (
            await session.execute(
                sql(
                    f"""select coalesce(d.title_zh, d.title), k.clause_number, k.original_text, {_SQL_SINGLE_CURRENT_TR}
                       from clauses k join document_versions v on v.id=k.version_id and v.state='parsed' join documents d on d.id=v.document_id
                       where d.country_code=:c and (:any or d.relevance in ('relevant','related'))
                         {_SQL_ELIGIBLE_DOC_VER_CLAUSE}
                       order by k.ordinal"""
                ),
                {"c": country, "any": any_relevance},
            )
        ).all()
    )
    from app.analysis.clause_gate import superseded_titles

    gone = superseded_titles(sorted({r[0] for r in rows if r[0]}))
    out = [r for r in rows if r[0] not in gone]
    s_cache[key] = out
    if keeper_active or rebuilding.get():
        while len(_LATEST_CLAUSES_CACHE) >= 10 and key not in _LATEST_CLAUSES_CACHE:
            _LATEST_CLAUSES_CACHE.pop(next(iter(_LATEST_CLAUSES_CACHE)), None)
        _LATEST_CLAUSES_CACHE[key] = out
    return out


async def _adequacy(session: AsyncSession, source: str, recipient: str) -> dict | None:
    from app.knowledge.reading import article_label

    forms = _NAME_FORMS.get(recipient, (NAMES.get(recipient, recipient),))
    for title, number, original, zh in await _latest_clauses(session, source):
        body = (zh or "") + "\n" + (original or "")
        # 名单条款：带条号、正文列出了国家（不是只提到“名单”的序言）
        if not number or not _LIST_CLAUSE.search(zh or "") or len(body) < 200:
            continue
        listed = any(f in body for f in forms)
        return {"listed": listed, "law": law_name(title, country_code=source), "article": article_label(number)}
    return None


_DATA_LAW = re.compile(
    r"个人数据|个人信息|数据保护|数据隐私|隐私保护|隐私法|信息隐私|"
    r"personal data|personal information|data protection|data privacy|privacy act|protection of privacy|"
    r"données à caractère personnel|protección de datos|proteção de dados|البيانات الشخصية",
    re.IGNORECASE,
)
_SUBSIDIARY = re.compile(r"条例|法令|决定|决议|指南|指引|通函|regulation|décret|guideline|circular", re.IGNORECASE)
_CONDITIONS = [
    (re.compile(r"明确同意|同意"), "取得数据主体明确同意"),
    (re.compile(r"许可|授权|批准|核准"), "取得数据保护主管机构的许可或授权"),
    (re.compile(r"合同|约束性|可强制执行|保障措施|保证"), "以合同条款等保障措施确保接收方提供同等保护"),
    (re.compile(r"充分|同等|相当|不低于|可比|适当(的)?保护"), "接收国或接收方的保护水平不低于本国"),
    (re.compile(r"约束性公司规则|有约束力的公司规则|具有约束力的公司规则", re.IGNORECASE), "接收方受约束性公司规则约束"),
    (re.compile(r"特定认证|跨境隐私规则|CBPR|隐私认可|数据保护认证", re.IGNORECASE), "接收方持有特定认证（如亚太经合组织跨境隐私规则认证）"),
]


_AMENDING_TITLE = re.compile(r"[（(](?:第\d+号)?修订[）)]|修订(?:条例|法|规则|令|通知)")


async def transfer_clause(session: AsyncSession, country: str) -> dict | None:
    """The clause of the sending country's law that sets the conditions for sending personal data
    abroad: {law, article, conditions}. Ranked by what it says (abroad + exceptions / licence /
    consent / contract) and by being the principal data protection law rather than a regulation."""
    from app.knowledge.cache_sync import keeper_active, rebuilding, use_cached
    from app.knowledge.reading import article_label

    s_cache = session.info.setdefault("_transfer_clause", {})
    if country in s_cache and not rebuilding.get():
        return copy.deepcopy(s_cache[country])
    if use_cached(country in _TRANSFER_CLAUSE_CACHE):
        return copy.deepcopy(_TRANSFER_CLAUSE_CACHE[country])

    best, score_best = None, 0
    ranked: list[tuple[int, dict]] = []
    for title, number, original, zh in await _latest_clauses(session, country):
        text = zh or ""
        if _AMENDING_TITLE.search(title or ""):  # 修订别的法规的文件不是规则本身：以被修订的现行法规为准
            continue
        if not number or not label_ok(number, original) or not _OUTBOUND.search(text[:200]) or not_a_basis({"quote_zh": text}) or _wrong_scene(text) or re.search(r"示例|例如[：:]", text[:300]):
            continue
        conds = [label for pat, label in _CONDITIONS if pat.search(text)]
        if not conds:  # 跨境条款总要写出条件（同意、许可、合同、同等保护）；只提到“境外”的定义条、罚则不算
            continue
        # 这一条讲的就是向境外传输（开头就说），出自主干数据保护法，不是长篇的综合条文
        score = (6 if _OUTBOUND.search(text[:150]) else 0) + len(conds) + (4 if _DATA_LAW.search(title or "") else 0) + (0 if _SUBSIDIARY.search(title or "") else 2)
        score += 2 if re.search(r"例外|除外|除非|不得", text) else 0
        score -= 3 if len(text) > 1500 else 0
        score += 1 if re.search(r"现行|修订|合订", title or "") else (-1 if re.search(r"原文|旧版", title or "") else 0)
        either = connective(text)
        if not _DATA_LAW.search(title or ""):
            score -= 6  # 不是数据保护法的条文（电子认证、电信）只在没有更好的时候用
        ranked.append((score, {"law": law_name(title, country_code=country), "article": real_label(number, original, zh, title) or article_label(number), "conditions": conds, "either": either, "data_law": bool(_DATA_LAW.search(title or "")),
                               "mandatory": bool(re.search(r"任何情况下|在所有情况下|一律|均须|均应|均为强制|强制性", text)), "_title": title,
                               "narrow": bool(re.search(r"限于下列情形|仅限于下列情形|仅在下列情形", text[:400]))}))
        if score > score_best:
            best, score_best = ranked[-1][1], score
    ranked.sort(key=lambda x: -x[0])
    if best is not None:  # 排在后面的几条也是这条路的依据（同意、许可、保护水平分在不同条里的）
        seen = {(best["law"], best["article"])}
        # 只限几种特定情形的例外条文（“经明确同意，限于：保护生命、司法程序……”）不是一般的传输途径：主条已要求许可的，不列为可选方式
        main_needs_permit = "取得数据保护主管机构的许可或授权" in best["conditions"]
        best["others"] = [r for _, r in ranked if r["data_law"] and (r["law"], r["article"]) not in seen and not (main_needs_permit and r.get("narrow")) and not re.search(r"指南|指引", r["law"]) and not seen.add((r["law"], r["article"]))][:2]  # 咨询指南只是解释，不当作法律依据
        # 同一部法里说明“第N条”所列条件怎样满足的配套条文（“就第 10 条第(1)款而言，具有法律强制执行力的义务包括合同、约束性公司规则”“持有特定认证的接收方”）
        seen_c = {c for o in [best, *best["others"]] for c in o["conditions"]}
        rows = await _latest_clauses(session, country)
        for base in [best, *best["others"]]:
            n = re.search(r"\d+", base.get("article") or "")
            if not n or not base.get("data_law"):
                continue
            for title, number, original, zh in rows:
                if title != base.get("_title") or not number or not zh or len(zh) > 1800:
                    continue
                label = real_label(number, original, zh, title) or article_label(number)
                if main_needs_permit and re.search(r"限于下列情形|仅限于下列情形|仅在下列情形", zh[:400]):
                    continue
                if label == base["article"] or not re.search(rf"(第\s*{n.group(0)}\s*条|条例\s*{n.group(0)}\b|规例\s*{n.group(0)}\b)", zh[:260]):
                    continue
                conds = [lab for pat, lab in _CONDITIONS if pat.search(zh) and lab not in seen_c]
                if conds:
                    seen_c.update(conds)
                    best["others"].append({"law": base["law"], "article": label, "conditions": conds, "either": connective(zh), "data_law": True, "mandatory": False})
    s_cache[country] = best
    if keeper_active or rebuilding.get():
        _TRANSFER_CLAUSE_CACHE[country] = best
    return copy.deepcopy(best)


_ALL = re.compile(r"(须|应当?|必须)(同时)?(满足|符合|具备)(下列|以下)(全部)?条件(?!之一)|但须(满足|符合)|并且|且须|同时满足|以及(须|应)")
_ANY = re.compile(r"(下列|以下)?(条件|情形)之一|任一|任何一项|例外|除外|除非|或者|或经|或在|或为|或依据|或通过|也可以|亦可")


def connective(text: str) -> bool:
    """Whether a clause lists alternatives (True, “任选其一”) or conditions that must all be met
    (False, “须同时满足”), read from the clause's own wording; conjunctive wording wins when the
    clause opens its list with it (“但须满足下列条件：……”)."""
    t = re.sub(r"\s+", "", text or "")
    if re.search(r"除非[^。；]{0,80}(并|且|同时)(获得|取得|经|具备|满足)", t):
        return False  # “除非具备 A 并获得 B”：A、B 都要
    m_all, m_any = _ALL.search(t), _ANY.search(t)
    if m_all and (not m_any or m_all.start() < m_any.start()):
        return False
    return bool(m_any)


def _wrong_scene(text: str) -> bool:
    return bool(re.search(r"电子(认证|签名)|认证服务|数字证书", text[:200]))


def _options(basis: dict | None, not_listed: bool = False) -> str:
    """The ways the law allows, each with its clause: “可选路径（满足其中一项即可）：①…（《法》第N条）”
    when the clause lists alternatives, “须同时满足：…” when it lists conditions."""
    if not basis:
        return ""
    level = "接收国或接收方的保护水平不低于本国"
    others = basis.get("others", [])
    if basis["conditions"] == [level] and others and not not_listed:
        # 主条只要求同等保护，配套条文列的是达到同等保护的几种机制：任选其一
        ways, used, also = [], {level}, []
        for b in others:
            if b.get("mandatory"):  # “在任何情况下均须取得授权”：不是可选方式，是另外必须满足的条件
                also += [f"{c}（《{b['law']}》{b['article']}）" for c in b["conditions"] if c not in used]
                used.update(b["conditions"])
                continue
            for c in b["conditions"]:
                if c not in used:
                    used.add(c)
                    ways.append(f"{c}（《{b['law']}》{b['article']}）")
        if ways or also:
            head = f"依《{basis['law']}》{basis['article']}，须确保接收方提供与本国法律相当的保护"
            body = ("；可通过下列任一方式满足：" + "；".join(f"{'①②③④⑤⑥'[i]}{w}" for i, w in enumerate(ways[:6]))) if ways else ""
            tail = ("；并且须同时满足：" + "、".join(also)) if also else ""
            return head + body + tail + "。"
    lines, used = [], set()
    for b in [basis, *others]:
        conds = [c for c in b["conditions"] if c not in used and not (not_listed and c.startswith("接收国或接收方的保护水平"))]
        if "取得数据保护主管机构的许可或授权" in conds and "以合同条款等保障措施确保接收方提供同等保护" in conds:
            conds = [c for c in conds if c != "以合同条款等保障措施确保接收方提供同等保护"]
            conds[conds.index("取得数据保护主管机构的许可或授权")] = "以合同条款或内部规则等保障措施为依据，向数据保护主管机构申请授权"
        used.update(conds)
        if not conds:
            continue
        cite = f"《{b['law']}》{b['article']}"
        if len(conds) == 1:
            lines.append(f"依{cite}：{conds[0]}")
        elif b.get("either"):
            lines.append(f"依{cite}，任选其一：" + "；".join(f"{'①②③④⑤⑥'[i]}{c}" for i, c in enumerate(conds)))
        else:
            lines.append(f"依{cite}，须同时满足：" + "、".join(conds))
    return "。".join(lines) + "。" if lines else ""


async def route(session: AsyncSession, values: dict[str, str], assessed: str | None = None) -> dict:
    """The transfer route under the sending country's law, in one paragraph the enterprise reads:
    {text, route}. ``route`` is cn_route's code for China, else "adequate" / "exception" / ""."""
    source, recipient = ends(values, assessed)
    src, rcp = NAMES.get(source or "", "数据发出国"), NAMES.get(recipient or "", "接收国")
    if source == CN:
        from app.analysis import cn_route

        r = cn_route.judge(values)
        if r["route"] == "unknown":
            return {"route": "unknown", "text": f"数据从中国发出，走哪条出境路径按{cn_route.BASIS}判断：还要写明“{'”“'.join(r['missing'])}”。"}
        because = "、".join(r["because"])
        return {"route": r["route"], "text": f"数据从中国发出：{because}，按{cn_route.BASIS}，{r['text']}。"}
    if not source:
        return {"route": "", "text": ""}
    ad = await adequacy(session, source, recipient)
    basis = await transfer_clause(session, source)
    cite = f"《{basis['law']}》{basis['article']}" if basis else ""
    conds = [c for c in (basis or {}).get("conditions", []) if not c.startswith("接收国或接收方")]
    ways = "、".join(conds) or "满足发出国法律规定的条件"
    options = _options(basis, not_listed=bool(ad) and not ad.get("listed"))
    if ad and ad["listed"]:
        return {"route": "adequate", "text": f"{rcp}在{src}《{ad['law']}》{ad['article']}所列充分保护国家名单内，可以依据充分保护向{rcp}传输；仍须履行{src}法律规定的申报和告知义务。"}
    if ad:
        lead = f"{rcp}不在{src}《{ad['law']}》{ad['article']}所列充分保护国家名单内"
        return {"route": "exception", "cite": cite, "text": f"{lead}，不能仅凭充分保护向{rcp}传输。{options}" if options else f"{lead}，向{rcp}传输须依据{cite or src + '法律规定的例外情形'}：{ways}。",
                "options_text": f"结论：{lead}，不能仅凭充分保护向{rcp}传输。{options}" if options else ""}
    if basis:
        return {"route": "exception", "cite": cite, "text": options or f"向{rcp}传输须满足{cite}规定的条件：{'、'.join(basis['conditions']) or ways}。",
                "options_text": f"结论：{src}法律对向境外传输设有前置条件，向{rcp}传输须满足下列要求。{options}" if options else ""}
    law = await main_data_law(session, source)
    by = f"按《{law}》" if law else f"按{src}数据保护法律"
    return {"route": "general", "text": f"{src}已收录的法规里没有专门规定向境外传输条件的条文，向{rcp}传输{by}对处理活动的申报、告知和安全要求办理，并以合同约定{rcp}接收方的保护义务。",
            "options_text": f"结论：{src}已收录的法规里没有专门规定向境外传输条件的条文。做法：{by}对处理活动的申报、告知和安全要求办理，并以合同约定{rcp}接收方的保护义务。"}


async def main_data_law(session: AsyncSession, country: str | None) -> str:
    """The country's principal data protection law in the library (its one Chinese name)."""
    if not country:
        return ""
    rows = (
        await session.execute(
            sql(
                f"""select coalesce(d.title_zh, d.title), count(k.id), d.document_type,
                           coalesce(d.title, ''),
                           sum(case when coalesce(k.original_text, '') ~* 'personal data|datos personales|dados pessoais|données (à caractère )?personnelles|персональн\\w+ данн|البيانات الشخصية' then 1 else 0 end)
                   from documents d join document_versions v on v.document_id=d.id and v.state='parsed'
                   join clauses k on k.version_id=v.id
                   where d.country_code=:c and d.relevance='relevant'
                     {_SQL_ELIGIBLE_DOC_VER_CLAUSE}
                   group by 1, 3, 4"""
            ),
            {"c": country},
        )
    ).all()
    if not rows:
        return ""

    _non_statute_re = re.compile(
        r"指南|指引|规章|办法|公告|通知|通函|勘误|豁免令|命令|申请表|声明表|核准|认证程序|实施细则|实施条例|调查规则|若干条款|出境|跨境|"
        r"guideline|notification|circular|implementing rules|form\b",
        re.I,
    )
    _subnational_or_narrow_re = re.compile(
        r"宪法修正案|现代化的法案|消费者数据权利|反垃圾邮件|无线电|机动车|电信法|电信（重组）|金融中心|公平审判|"
        r"阿尔伯塔|BC省|首都领地|塔斯马尼亚|昆士兰|北领地|2014年隐私与数据保护法|1998年隐私与个人信息保护法",
        re.I,
    )
    _decree_or_reg_re = re.compile(r"条例|法令|决定|决议|regulation|order|decree", re.I)
    _cyber_fallback_re = re.compile(r"网络安全|网络信息安全|电子犯罪|信息犯罪|数字法典|信息和通信技术|电子交易", re.I)

    candidates: list[tuple[tuple, str]] = []
    for t, n, dt, orig_t, pd_hits in rows:
        combined = f"{t or ''} {orig_t or ''}"
        cleaned_t = re.sub(r"修订版|现行合订本", "", t or "")
        if _non_statute_re.search(cleaned_t):
            continue
        is_data_title = bool(_DATA_LAW.search(combined))
        pd_ratio = (int(pd_hits or 0) / max(1, int(n or 1)))
        is_data_body = dt == "LAW" and int(pd_hits or 0) >= 8 and pd_ratio >= 0.25
        is_cyber_law = dt == "LAW" and bool(_cyber_fallback_re.search(cleaned_t))
        if not (is_data_title or is_data_body or is_cyber_law):
            continue

        is_narrow = bool(_subnational_or_narrow_re.search(cleaned_t))
        is_decree = bool(_decree_or_reg_re.search(cleaned_t))
        if is_data_title and dt == "LAW" and not is_decree and not is_narrow:
            tier = 0
        elif is_data_title and not is_narrow:
            tier = 1
        elif is_data_body and not is_narrow:
            tier = 2
        elif is_data_title:
            tier = 3
        else:
            tier = 4
        sort_key = (
            tier,
            dt != "LAW",
            is_narrow,
            bool(re.fullmatch(r"隐私法", (t or "").strip())),
            -int(n or 0),
            not bool(re.search(r"第\s*\d+\s*号|\d{4}年", t or "")),
        )
        candidates.append((sort_key, t))

    if not candidates:
        return ""
    candidates.sort(key=lambda x: x[0])
    return law_name(candidates[0][1], country_code=country)


# 中国出境的主依据（用户 11.11 定）：个人信息保护法第三十八、三十九条，加上所走路径的办法
CN_OUTBOUND_CORE = ["《个人信息保护法》第三十八条、第三十九条"]
CN_ROUTE_CORE = {
    "security_assessment": ["《促进和规范数据跨境流动规定》第七条", "《数据出境安全评估办法》第四条、第五条、第六条、第九条"],
    "standard_contract": ["《促进和规范数据跨境流动规定》第八条", "《个人信息出境标准合同办法》第四条、第五条、第六条、第七条"],
    "exempt": ["《促进和规范数据跨境流动规定》第五条"],
    "unknown": ["《促进和规范数据跨境流动规定》第五条、第七条、第八条"],
}
_ART_TOKEN_PAT = r"第[0-9一二三四五六七八九十百零]+(?:\.[0-9]+)*[A-Za-z]?(?:条(?:之[0-9一二三四五六七八九十]+)?|段|项)"
_CITE = re.compile(rf"《([^》]{{2,60}})》((?:{_ART_TOKEN_PAT}[、和及，,\s]*)+)")


def cites_in(text: str) -> list[tuple[str, str]]:
    """(law, “第X条”) pairs a sentence names (“《A》第五条、第七条” → two pairs)."""
    out = []
    for m in _CITE.finditer(text or ""):
        for art in re.findall(_ART_TOKEN_PAT, m.group(2)):
            out.append((m.group(1).strip(), art))
    return out


async def named_clauses(session: AsyncSession, country: str | None, refs: list[tuple[str, str]], side: str) -> list[dict]:
    """The library clauses for (law, article) pairs of one country, in the order named; a pair the
    library does not hold is skipped (it appears once the law is in the library)."""
    if not country or not refs:
        return []
    from app.ingestion.display_text import for_reading
    from app.knowledge.reading import article_label

    laws = sorted({law for law, _ in refs})
    rows = (
        await session.execute(
            sql(
                f"""select k.id::text, d.id::text, coalesce(d.title_zh, d.title), d.title, k.clause_number, k.original_text,
                   {_SQL_SINGLE_CURRENT_TR}, k.language
                   from clauses k join document_versions v on v.id=k.version_id and v.state='parsed' join documents d on d.id=v.document_id
                   where d.country_code=:c and d.relevance in ('relevant', 'related')
                     and coalesce(d.title_zh, d.title) = any(:laws)
                     {_SQL_ELIGIBLE_DOC_VER_CLAUSE}"""
            ),
            {"c": country, "laws": laws},
        )
    ).all()
    found = {}
    for cid, did, title_zh, title, number, original, zh, clang in rows:
        is_zh = str(clang or "").lower().startswith("zh")
        if not zh and not is_zh:
            continue
        label = article_label(number or "")
        if label:
            found.setdefault((title_zh, label), (cid, did, title_zh, title, label, original, zh or (original if is_zh else "")))
    out = []
    for key in dict.fromkeys(refs):
        r = found.get(key)
        if r:
            cid, did, title_zh, title, label, original, zh = r
            out.append({"clause_id": cid, "document_id": did, "document_title": law_name(title_zh, did), "original_title": title if title != title_zh else "",
                        "clause_number": label, "quote_original": for_reading(original or ""), "quote_zh": for_reading(zh or ""),
                        "dimension_code": "D3", "side": side, "core": True})
    return out


_BASES_CLAUSE = re.compile(r"同意[^。]{0,200}(合同|法定义务|法律义务)|(合同|法定义务)[^。]{0,200}同意")
_LEGIT_WORD = re.compile(r"合法利益|正当利益|合法权益（?追求|legitimate interest|intérêt légitime", re.IGNORECASE)


async def legit_basis(session: AsyncSession, country: str | None) -> dict:
    """Whether the sending country's law lists legitimate interests among the lawful bases of
    processing, read only from the clause that lists the bases: {state: 有/无/待核实, cite, clause}."""
    if not country:
        return {"state": "待核实", "cite": ""}
    from app.knowledge.reading import article_label

    for title, number, original, zh in await _latest_clauses(session, country):
        text = zh or (original if re.search(r"[一-鿿]", (original or "")[:60]) else "")  # 中文原文的法没有译文，直接用原文
        if not number or not _DATA_LAW.search(title or "") or not _BASES_CLAUSE.search(text) or len(text) < 60:
            continue
        kinds = sum(bool(re.search(p, text)) for p in (r"同意", r"合同", r"法定义务|法律义务|法律规定的义务", r"重大利益|生命", r"公共利益|公共任务|公共权力"))
        if kinds < 3 or _OUTBOUND.search(text[:200]) or re.search(r"至[^。]{0,30}国家|条规定的例外", text[:200]):  # 列举处理依据的条款：至少列了三种依据，且不是跨境例外条款
            continue
        if re.search(r"条例|法令|决定|决议|指南|清单|修订", title or ""):
            continue
        cite = f"《{law_name(title, country_code=country)}》{article_label(number)}"
        m = _LEGIT_WORD.search(text + " " + (original or ""))
        if m:
            item = re.search(r"([a-z一二三四五六七八九十]|\d)[)）]\s*[^。；]{0,40}" + re.escape(m.group(0)[:4]), text)
            return {"state": "有", "cite": cite + (f"第{item.group(1)})项" if item else "")}
        return {"state": "无", "cite": cite}
    return {"state": "待核实", "cite": ""}


async def pinned(session: AsyncSession, values: dict[str, str], assessed: str | None, code: str = "") -> list[dict]:
    """The clauses every basis table of the document starts with: the route's own clauses (those
    the route paragraph names), China's outbound core when data leaves China, then the sending
    country's vehicle-data rules."""
    from app.knowledge.cache_sync import keeper_active, rebuilding, use_cached

    source, recipient = ends(values, assessed)
    if not source:
        return []
    r = await route(session, values, assessed)
    cache_key = (
        source,
        recipient,
        r.get("route", ""),
        r.get("cite", ""),
        tuple(cites_in(r.get("text", ""))),
        "lia" if code == "lia" else "",
    )
    s_cache = session.info.setdefault("_pinned", {})
    if cache_key in s_cache and not rebuilding.get():
        return [dict(c) for c in s_cache[cache_key]]
    if use_cached(cache_key in _PINNED_CACHE):
        return [dict(c) for c in _PINNED_CACHE[cache_key]]

    refs = cites_in(r.get("text", "")) + cites_in(r.get("cite", ""))
    if source == CN:
        refs = [p for s in CN_OUTBOUND_CORE + CN_ROUTE_CORE.get(r.get("route", "unknown"), []) for p in cites_in(s)] + refs
    if code == "lia":
        lb = await legit_basis(session, source)
        refs = cites_in(re.sub(r"第[^条]*\)项$", "", lb.get("cite") or "")) + refs
    if source != CN:  # 发出国的出境主条文：要求充分保护的那一条、违法跨境的罚则
        from app.analysis.doc_applicability import source_grounds
        from app.knowledge.whitepaper import pinned_topics

        adq = (await source_grounds(session, source)).get("adequacy")
        refs = refs + cites_in(getattr(adq, "cite", "") or "")
        penalty = (await pinned_topics(session, source)).get("transfer_penalty") or []
    else:
        penalty = []
    named = await named_clauses(session, source, refs, "source")
    named += [{**p, "side": "source", "core": True, "dimension_code": "D6"} for p in penalty if p["clause_id"] not in {c["clause_id"] for c in named}]
    if code == "lia" and named:
        named[0]["dimension_code"] = "D4"  # 目的测试一节就从处理依据条款讲起
    if source != CN and r.get("route") in ("exception", "adequate"):
        ad = await adequacy(session, source, recipient)
        if ad and ad.get("article"):
            named += await named_clauses(session, source, [(ad["law"], ad["article"])], "source")
    seen = {c["clause_id"] for c in named}
    out = named + [c for c in await core_clauses(session, source, "source") if c["clause_id"] not in seen]
    s_cache[cache_key] = out
    if keeper_active or rebuilding.get():
        _PINNED_CACHE[cache_key] = out
    return [dict(c) for c in out]
