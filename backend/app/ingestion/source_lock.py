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
