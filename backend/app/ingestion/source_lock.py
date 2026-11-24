"""Source lock: only documents published by the issuing authority's own site may enter the library.

Every host is an explicit entry in ``OFFICIAL_PUBLISHERS``: the publishing body, its country and
the host it publishes on. A ``gov`` / ``gouv`` label in a hostname proves nothing about who
published a text (another country's ministry can republish it), so labels are no longer enough.
Anything not listed is refused unless the operator names who confirmed it as official; that
confirmation is stored with the document's import event. Adding a country or a publisher is
adding an entry here, not changing the rule.
"""

from dataclasses import dataclass
from urllib.parse import urlsplit


@dataclass(frozen=True)
class Publisher:
    host: str  # the publisher's own domain; sub-domains are included
    name_zh: str
    country: str  # country whose law the body publishes ("*" for supranational bodies)


OFFICIAL_PUBLISHERS: tuple[Publisher, ...] = (
    # 新加坡
    Publisher("sso.agc.gov.sg", "新加坡总检察署（法规库）", "SG"),
    Publisher("egazette.gov.sg", "新加坡政府电子公报", "SG"),
    Publisher("pdpc.gov.sg", "新加坡个人数据保护委员会", "SG"),
    Publisher("lta.gov.sg", "新加坡陆路交通管理局", "SG"),
    Publisher("csa.gov.sg", "新加坡网络安全局", "SG"),
    Publisher("imda.gov.sg", "新加坡资讯通信媒体发展局", "SG"),
    # 新加坡政府网站的内容托管域名：CSA 等机构页面链出的文件存放于此；文件本身的发布机构
    # 由清单条目的 publisher 与来源页面记录（域名只证明是新加坡政府托管，不证明是哪家机构）
    Publisher("isomer-user-content.by.gov.sg", "新加坡政府网站内容托管", "SG"),
    Publisher("mot.gov.sg", "新加坡交通部", "SG"),
    Publisher("ccs.gov.sg", "新加坡竞争与消费者委员会", "SG"),
    Publisher(
        "singaporestandardseshop.sg", "新加坡企业发展局（标准商店，付费标准仅登记目录）", "SG"
    ),
    # 国际组织：标准与车辆法规目录（只登记编号、名称、状态，不存全文）
    Publisher("iso.org", "国际标准化组织（ISO）", "*"),
    Publisher("unece.org", "联合国欧洲经济委员会（UNECE，世界车辆法规协调论坛 WP.29）", "*"),
    # 德国、日本、韩国：各国政府的官方法律检索门户（发布机构自己的站点）
    Publisher("gesetze-im-internet.de", "德国联邦司法部与联邦司法局（联邦法律门户）", "DE"),
    Publisher("laws.e-gov.go.jp", "日本数字厅（e-Gov 法令检索）", "JP"),
    Publisher("law.go.kr", "韩国法制处（国家法令信息中心）", "KR"),
    # 埃及
    Publisher("pdpc.gov.eg", "埃及个人数据保护中心", "EG"),
    Publisher("mcit.gov.eg", "埃及通信与信息技术部", "EG"),
    Publisher("tra.gov.eg", "埃及国家电信监管局", "EG"),
    Publisher("itida.gov.eg", "埃及信息技术产业发展局", "EG"),
    Publisher("idsc.gov.eg", "埃及内阁信息与决策支持中心", "EG"),
    Publisher("escc.gov.eg", "埃及最高网络安全委员会（内阁下属）", "EG"),
    Publisher("cpa.gov.eg", "埃及消费者保护局", "EG"),
    Publisher("cbe.org.eg", "埃及中央银行", "EG"),
    Publisher("customs.gov.eg", "埃及海关总署", "EG"),
    Publisher("goeic.gov.eg", "埃及进出口监管总局（GOEIC，隶属贸易与工业部）", "EG"),
    Publisher("fra.gov.eg", "埃及金融监管局", "EG"),
    Publisher("eos.org.eg", "埃及标准与质量组织（国家标准机构）", "EG"),
    Publisher("traffic.moi.gov.eg", "埃及内政部交通总局", "EG"),
    # EG-CERT 是国家电信监管局下属的国家 CERT：自己发布网络安全框架，也托管少量他机构发布的文本。
    # 域名只证明“经官方机构发布”，文件的真实发布机构以清单条目的 publisher 为准
    Publisher("egcert.eg", "埃及国家 CERT（隶属国家电信监管局）", "EG"),
    Publisher("digital.tra.gov.eg", "埃及国家电信监管局数字服务门户", "EG"),
    # 突尼斯
    Publisher("justice.gov.tn", "突尼斯司法部", "TN"),
    Publisher("ins.tn", "突尼斯国家统计局（托管第 2004-63 号法法语文本）", "TN"),
    Publisher("inpdp.nat.tn", "突尼斯国家个人数据保护机构", "TN"),
    Publisher("iort.gov.tn", "突尼斯官方公报印刷厂", "TN"),
    Publisher("mtc.gov.tn", "突尼斯通信技术部（法规汇编）", "TN"),
    Publisher("ancs.tn", "突尼斯国家网络安全局（ANCS）", "TN"),
    Publisher("carthage.tn", "突尼斯共和国总统府（官方公报所载法令）", "TN"),
    Publisher("intt.tn", "突尼斯国家电信管理局", "TN"),
    Publisher("arp.tn", "突尼斯人民代表大会", "TN"),
    Publisher("commerce.gov.tn", "突尼斯商务部", "TN"),
    Publisher("transport.tn", "突尼斯交通部（道路法典及其实施文本，官方印刷厂版本）", "TN"),
    Publisher("innorpi.tn", "突尼斯国家标准化与工业产权局", "TN"),
    # 摩洛哥
    Publisher("cndp.ma", "摩洛哥国家个人数据保护监督委员会", "MA"),
    Publisher("dgssi.gov.ma", "摩洛哥信息系统安全总局", "MA"),
    Publisher("sgg.gov.ma", "摩洛哥政府总秘书处（官方公报）", "MA"),
    Publisher("mcinet.gov.ma", "摩洛哥工业与贸易部", "MA"),
    Publisher("adala.justice.gov.ma", "摩洛哥司法部法规门户（Adala）", "MA"),
    Publisher("narsa.ma", "摩洛哥国家道路安全局（NARSA）", "MA"),
    Publisher("anrt.ma", "摩洛哥国家电信监管局", "MA"),
    Publisher("imanor.gov.ma", "摩洛哥标准化研究院", "MA"),
    Publisher("douane.gov.ma", "摩洛哥海关", "MA"),
    Publisher("bkam.ma", "摩洛哥央行", "MA"),
    # 黎巴嫩
    Publisher("bdl.gov.lb", "黎巴嫩央行", "LB"),
    Publisher("lp.gov.lb", "黎巴嫩议会", "LB"),
    Publisher("pcm.gov.lb", "黎巴嫩总理府", "LB"),
    Publisher("economy.gov.lb", "黎巴嫩经济与贸易部", "LB"),
    Publisher("tra.gov.lb", "黎巴嫩电信管理局", "LB"),
    Publisher("mpt.gov.lb", "黎巴嫩电信部", "LB"),
    Publisher("customs.gov.lb", "黎巴嫩海关", "LB"),
    Publisher("libnor.gov.lb", "黎巴嫩标准协会（LIBNOR）", "LB"),
    Publisher("isf.gov.lb", "黎巴嫩内部安全部队", "LB"),
    # 特别调查委员会设在黎巴嫩央行内，托管央行基本通函汇编；
    # 文件的发布机构仍是央行（以清单 publisher 为准）
    Publisher("sic.gov.lb", "黎巴嫩特别调查委员会（设于央行）", "LB"),
    # 中国与欧盟（出境、入境文件的官方模板）
    Publisher("cac.gov.cn", "国家互联网信息办公室", "CN"),
    Publisher("flk.npc.gov.cn", "国家法律法规数据库（全国人民代表大会常务委员会）", "CN"),
    # 舆情来源（用户 10.31 定：网信办、汽车行业官方通报及允许的官媒）。均已在大陆出口下实测可访问
    Publisher("miit.gov.cn", "工业和信息化部", "CN"),
    Publisher("openstd.samr.gov.cn", "国家标准全文公开系统（国家标准化管理委员会）", "CN"),
    Publisher("std.samr.gov.cn", "全国标准信息公共服务平台", "CN"),
    Publisher("samr.gov.cn", "国家市场监督管理总局", "CN"),
    Publisher("sac.gov.cn", "国家标准化管理委员会", "CN"),
    Publisher("hbba.sacinfo.org.cn", "行业标准信息服务平台（国家标准化管理委员会）", "CN"),
    Publisher("mot.gov.cn", "交通运输部", "CN"),
    Publisher("www.gov.cn", "中国政府网（国务院）", "CN"),
    Publisher("caam.org.cn", "中国汽车工业协会", "CN"),
    Publisher("news.cn", "新华社（新华网）", "CN"),
    Publisher("people.com.cn", "人民日报社（人民网）", "CN"),
    Publisher("eur-lex.europa.eu", "欧盟官方法律数据库", "*"),
    Publisher("publications.europa.eu", "欧盟出版局官方文献库（CELLAR）", "*"),
    Publisher("edpb.europa.eu", "欧洲数据保护委员会（EDPB）", "*"),
    # 欧洲国家官方立法门户与数据/电信/交通主管机关
    Publisher("bfdi.bund.de", "德国联邦数据保护与信息自由专员（BfDI）", "DE"),
    Publisher("kba.de", "德国联邦机动车运输管理局（KBA）", "DE"),
    Publisher("bundesnetzagentur.de", "德国联邦网络管理局（BNetzA）", "DE"),
    Publisher("legifrance.gouv.fr", "法国政府官方法律门户（Légifrance）", "FR"),
    Publisher("cnil.fr", "法国国家信息与自由委员会（CNIL）", "FR"),
    Publisher("arcep.fr", "法国电子通信、邮政与印刷媒体分发监管局（ARCEP）", "FR"),
    Publisher("legislation.gov.uk", "英国国家档案馆官方法律门户", "GB"),
    Publisher("ico.org.uk", "英国信息专员办公室（ICO）", "GB"),
    Publisher("gov.uk", "英国政府官方门户", "GB"),
    Publisher("ofcom.org.uk", "英国通信管理局（Ofcom）", "GB"),
    Publisher("boe.es", "西班牙国家官方公报署（BOE）", "ES"),
    Publisher("aepd.es", "西班牙数据保护局（AEPD）", "ES"),
    Publisher("normattiva.it", "意大利共和国现行法律官方门户（Normattiva）", "IT"),
    Publisher("gazzettaufficiale.it", "意大利共和国官方公报", "IT"),
    Publisher("garanteprivacy.it", "意大利个人数据保护局（Garante）", "IT"),
    Publisher("wetten.overheid.nl", "荷兰政府官方法律库", "NL"),
    Publisher("autoriteitpersoonsgegevens.nl", "荷兰数据保护局（AP）", "NL"),
    Publisher("rdw.nl", "荷兰车辆管理局（RDW）", "NL"),
    Publisher("ejustice.just.fgov.be", "比利时联邦司法部官方公报门户", "BE"),
    Publisher("autoriteprotectiondonnees.be", "比利时数据保护局（APD）", "BE"),
    Publisher("riksdagen.se", "瑞典议会官方法律库", "SE"),
    Publisher("imy.se", "瑞典隐私保护局（IMY）", "SE"),
    Publisher("transportstyrelsen.se", "瑞典交通管理局", "SE"),
    Publisher("isap.sejm.gov.pl", "波兰众议院官方法律信息系统（ISAP）", "PL"),
    Publisher("dziennikustaw.gov.pl", "波兰法律公报署", "PL"),
    Publisher("uodo.gov.pl", "波兰个人数据保护局（UODO）", "PL"),
    # 亚太国家官方立法与监管门户
    Publisher("ppc.go.jp", "日本个人信息保护委员会（PPC）", "JP"),
    Publisher("soumu.go.jp", "日本总务省（MIC/电波利用门户）", "JP"),
    Publisher("mlit.go.jp", "日本国土交通省（MLIT）", "JP"),
    Publisher("pipc.go.kr", "韩国个人信息保护委员会（PIPC）", "KR"),
    Publisher("rra.go.kr", "韩国国立电波研究院（RRA）", "KR"),
    Publisher("molit.go.kr", "韩国国土交通部（MOLIT）", "KR"),
    Publisher("ratchakitcha.soc.go.th", "泰王国政府皇家公报", "TH"),
    Publisher("pdpc.or.th", "泰国个人数据保护委员会办公室（PDPC）", "TH"),
    Publisher("nbtc.go.th", "泰国国家广播和电信委员会（NBTC）", "TH"),
    Publisher("lom.agc.gov.my", "马来西亚总检察署联邦法规门户", "MY"),
    Publisher("pdp.gov.my", "马来西亚个人数据保护局（JPDP）", "MY"),
    Publisher("mcmc.gov.my", "马来西亚通信与多媒体委员会（MCMC）", "MY"),
    Publisher("peraturan.bpk.go.id", "印度尼西亚审计署国家法规数据库（JDIH BPK）", "ID"),
    Publisher("jdih.komdigi.go.id", "印度尼西亚通信与数字部法规门户（Komdigi）", "ID"),
    Publisher("komdigi.go.id", "印度尼西亚通信与数字部（Komdigi）", "ID"),
    Publisher("vbpl.vn", "越南司法部国家法律法规数据库", "VN"),
    Publisher("chinhphu.vn", "越南社会主义共和国政府门户", "VN"),
    Publisher("mic.gov.vn", "越南信息与通信部（MIC）", "VN"),
    Publisher("officialgazette.gov.ph", "菲律宾共和国官方公报", "PH"),
    Publisher("privacy.gov.ph", "菲律宾国家隐私委员会（NPC）", "PH"),
    Publisher("ntc.gov.ph", "菲律宾国家电信委员会（NTC）", "PH"),
    Publisher("indiacode.nic.in", "印度联邦法典官方数据库（India Code）", "IN"),
    Publisher("egazette.gov.in", "印度政府电子公报", "IN"),
    Publisher("meity.gov.in", "印度电子与信息技术部（MeitY）", "IN"),
    Publisher("tec.gov.in", "印度电信工程中心（TEC）", "IN"),
    Publisher("morth.nic.in", "印度道路运输与公路部（MoRTH）", "IN"),
    Publisher("pakistancode.gov.pk", "巴基斯坦司法部联邦法典门户", "PK"),
    Publisher("moitt.gov.pk", "巴基斯坦信息技术与电信部（MoITT）", "PK"),
    Publisher("pta.gov.pk", "巴基斯坦电信管理局（PTA）", "PK"),
    Publisher("bdlaws.minlaw.gov.bd", "孟加拉国司法部官方法律库", "BD"),
    Publisher("btrc.gov.bd", "孟加拉国电信监管委员会（BTRC）", "BD"),
    Publisher("legislation.gov.au", "澳大利亚联邦立法登记处", "AU"),
    Publisher("oaic.gov.au", "澳大利亚信息专员办公室（OAIC）", "AU"),
    Publisher("acma.gov.au", "澳大利亚通信与媒体管理局（ACMA）", "AU"),
    Publisher("infrastructure.gov.au", "澳大利亚基础设施、交通、区域发展与通信部", "AU"),
    # 欧亚、中东与非洲官方立法与监管门户
    Publisher("pravo.gov.ru", "俄罗斯联邦官方国家法律信息门户", "RU"),
    Publisher("rkn.gov.ru", "俄罗斯联邦通信、信息技术和大众传媒监督局（Roskomnadzor）", "RU"),
    Publisher("adilet.zan.kz", "哈萨克斯坦司法部官方法律信息系统（Adilet）", "KZ"),
    Publisher("cbd.minjust.gov.kg", "吉尔吉斯斯坦司法部集中法律数据库", "KG"),
    Publisher("resmigazete.gov.tr", "土耳其共和国官方公报", "TR"),
    Publisher("mevzuat.gov.tr", "土耳其总统府国家法规信息系统", "TR"),
    Publisher("kvkk.gov.tr", "土耳其个人数据保护局（KVKK）", "TR"),
    Publisher("btk.gov.tr", "土耳其信息与通信技术管理局（BTK）", "TR"),
    Publisher("uaelegislation.gov.ae", "阿联酋内阁联邦立法官方门户", "AE"),
    Publisher("tdra.gov.ae", "阿联酋电信与数字政府监管局（TDRA）", "AE"),
    Publisher("dlp.dubai.gov.ae", "迪拜政府最高立法委员会法律门户", "AE"),
    Publisher("laws.boe.gov.sa", "沙特阿拉伯内阁专家局官方法律库", "SA"),
    Publisher("sdaia.gov.sa", "沙特数据与人工智能管理局（SDAIA）", "SA"),
    Publisher("cst.gov.sa", "沙特通信、空间与技术委员会（CST）", "SA"),
    Publisher("saso.gov.sa", "沙特标准、计量和质量组织（SASO）", "SA"),
    Publisher("almeezan.qa", "卡塔尔司法部官方法律门户（Al Meezan）", "QA"),
    Publisher("cra.gov.qa", "卡塔尔通信监管局（CRA）", "QA"),
    Publisher("citra.gov.kw", "科威特通信与信息技术监管局（CITRA）", "KW"),
    Publisher("qanoon.om", "阿曼司法与法律事务部官方法律门户", "OM"),
    Publisher("tra.gov.om", "阿曼电信监管局（TRA）", "OM"),
    Publisher("gov.il", "以色列政府与隐私保护局官方门户", "IL"),
    Publisher("joradp.dz", "阿尔及利亚政府总秘书处官方公报", "DZ"),
    Publisher("anpdp.dz", "阿尔及利亚国家个人数据保护局（ANPDP）", "DZ"),
    Publisher("arpce.dz", "阿尔及利亚邮政与电子通信监管局（ARPCE）", "DZ"),
    Publisher("gov.za", "南非共和国政府官方公报门户", "ZA"),
    Publisher("inforegulator.org.za", "南非信息监管局（Information Regulator）", "ZA"),
    Publisher("icasa.org.za", "南非独立通信管理局（ICASA）", "ZA"),
    Publisher("ndpc.gov.ng", "尼日利亚数据保护委员会（NDPC）", "NG"),
    Publisher("ncc.gov.ng", "尼日利亚通信委员会（NCC）", "NG"),
    # 美洲国家官方立法与监管门户
    Publisher("laws-lois.justice.gc.ca", "加拿大联邦司法部官方法律门户", "CA"),
    Publisher("priv.gc.ca", "加拿大隐私专员办公室（OPC）", "CA"),
    Publisher("ised-isde.canada.ca", "加拿大创新、科学与经济发展部（ISED）", "CA"),
    Publisher("canada.ca", "加拿大联邦政府官方门户", "CA"),
    Publisher("diputados.gob.mx", "墨西哥众议院联邦法律官方门户", "MX"),
    Publisher("dof.gob.mx", "墨西哥联邦官方公报（DOF）", "MX"),
    Publisher("ift.org.mx", "墨西哥联邦电信研究所（IFT）", "MX"),
    Publisher("planalto.gov.br", "巴西总统府联邦立法门户（Planalto）", "BR"),
    Publisher("gov.br", "巴西联邦政府与国家数据保护局（ANPD）官方门户", "BR"),
    Publisher("anatel.gov.br", "巴西国家电信管理局（ANATEL）", "BR"),
    Publisher("argentina.gob.ar", "阿根廷共和国政府与公共信息获取局（AAIP）门户", "AR"),
    Publisher("boletinoficial.gob.ar", "阿根廷共和国官方公报", "AR"),
    Publisher("servicios.infoleg.gob.ar", "阿根廷司法部国家立法信息系统（InfoLEG）", "AR"),
    Publisher("enacom.gob.ar", "阿根廷国家通信管理局（ENACOM）", "AR"),
    Publisher("bcn.cl", "智利国会图书馆国家法规库（Ley Chile）", "CL"),
    Publisher("leychile.cl", "智利国会图书馆国家法规库（Ley Chile）", "CL"),
    Publisher("subtel.gob.cl", "智利电信副部（SUBTEL）", "CL"),
    Publisher("funcionpublica.gov.co", "哥伦比亚行政管理部官方法规库", "CO"),
    Publisher("sic.gov.co", "哥伦比亚工商监管局（SIC）", "CO"),
    Publisher("crc.gov.co", "哥伦比亚通信监管委员会（CRC）", "CO"),
    Publisher("gob.pe", "秘鲁共和国政府与国家个人数据保护局门户", "PE"),
    Publisher("spij.minjus.gob.pe", "秘鲁司法部国家法律信息系统（SPIJ）", "PE"),
)


class SourceNotOfficial(ValueError):
    """The URL does not belong to a listed publisher and nobody confirmed it."""


def _host(url: str) -> str:
    return (urlsplit(url).hostname or "").lower().rstrip(".")


def publisher_of(url: str) -> Publisher | None:
    host = _host(url)
    if not host:
        return None
    for p in OFFICIAL_PUBLISHERS:
        if host == p.host or host.endswith("." + p.host):
            return p
    return None


def is_official_host(url: str) -> bool:
    return publisher_of(url) is not None


def require_official(url: str, confirmed_by: str | None = None) -> str:
    """Returns the basis recorded for the import; raises when the source is not locked in."""
    publisher = publisher_of(url)
    if publisher is not None:
        return f"官方站点（{publisher.name_zh}）"
    if confirmed_by and confirmed_by.strip():
        return f"非名单内站点，经 {confirmed_by.strip()} 确认为官方来源"
    raise SourceNotOfficial(f"来源不在已登记的发布机构名单内，未确认前不得入库：{url}")
