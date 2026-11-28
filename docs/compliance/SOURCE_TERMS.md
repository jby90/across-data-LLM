# 法规来源使用条款登记（46 个国家与地区及国际标准组织）

每个官方来源站点，在**抓取或归档入库前**均完成公开立法/政府公开许可条款核验，并经管理端「采集站点」使用条款确认后方可启用 168 小时每周错峰巡检。
系统严格保留官方原文归档、来源 URL、版本号、抓取时间与 SHA-256 内容校验值。

## 一、重点及扩充法域官方法规门户登记表

| 法域范围 | 官方站点 / 发布机构 | 官方入口示例 | 授权 / 条款依据 | 允许礼貌巡检 | 允许企业内部合规使用 | 允许展示官方法规条文 | robots.txt 与限速策略 | 访问限制说明 | 确认状态（基准日） | 状态 |
|---|---|---|---|---|---|---|---|---|---|---|
| 中国（CN） | 国家互联网信息办公室 / 工信部 / 全国人大国家法律法规数据库 / 国家标准全文公开系统 | `https://www.cac.gov.cn`、`https://flk.npc.gov.cn`、`https://openstd.samr.gov.cn` | 《政府信息公开条例》及官方公开规范性文件规定 | 是（低频限速） | 是 | 是（保留官方出处与文号） | 遵守 `robots.txt`，单域名间隔 ≥ 5 秒 | 标准全文仅登记题录与官方跳转链接 | 系统管理员已核验（2026-11） | 已确认 |
| 新加坡（SG） | Singapore Statutes Online (AGC) / PDPC / CSA / LTA / IMDA | `https://sso.agc.gov.sg`、`https://www.pdpc.gov.sg` | Singapore Open Data Licence / AGC Terms of Use | 是（遵守 `robots.txt`） | 是 | 是（标明官方来源与版本日期） | 严格遵守 `robots.txt`，礼貌限速抓取 | 禁止高频并发请求 | 系统管理员已核验（2026-11） | 已确认 |
| 埃及（EG） | 埃及官方公报 / 通信与信息技术部（MCIT）/ NTRA / GOEIC | `https://mcit.gov.eg`、`https://www.ntra.gov.eg`、`https://www.goeic.gov.eg` | 埃及第 151 号法律及官方公报公开立法文本规则 | 仅针对 `robots.txt` 正常响应站点启用 | 是 | 是（附官方公报号与原文对照） | 若 `robots.txt` 异常自动暂停抓取走人工归档校验 | 部分政府门户偶发区域网络抖动 | 系统管理员已核验（2026-11） | 已确认 |
| 摩洛哥（MA） | 摩洛哥政府秘书处官方公报（SGG）/ CNDP / ANRT | `https://www.sgg.gov.ma`、`https://www.cndp.ma`、`https://www.anrt.ma` | SGG Bulletin Officiel 公开法律公文与 CNDP 公开审议决议 | 是（遵守 `robots.txt`） | 是 | 是（标明公报期号与审议文号） | 遵守 `robots.txt`，每周错峰巡检 | 扫描版公报入库后执行双路版面核验 | 系统管理员已核验（2026-11） | 已确认 |
| 突尼斯（TN） | 突尼斯官方印刷局公报（IORT）/ INPDP / CERT / ANCE / ATTT | `http://www.iort.gov.tn`、`https://www.inpdp.nat.tn`、`https://www.ance.tn` | JORT 突尼斯共和国官方公报公开法令及监管指南 | 是（遵守 `robots.txt`） | 是 | 是（标明 JORT 法令编号与出处） | 遵守 `robots.txt`，单站点低频轮询 | 部分历史公报采用官方原件归档 | 系统管理员已核验（2026-11） | 已确认 |
| 黎巴嫩（LB） | 黎巴嫩官方公报（PCM）/ 议会立法库 / TRA / 黎巴嫩央行法规库 | `http://pcm.gov.lb`、`https://www.tra.gov.lb`、`https://www.bdl.gov.lb` | 黎巴嫩共和国公开立法与官方公报公共领域文件规则 | 是（遵守 `robots.txt`） | 是 | 是（标明法律编号与发布年份） | 遵守 `robots.txt`，超时自动退避 | 低频周期巡检（168 小时） | 系统管理员已核验（2026-11） | 已确认 |
| 欧盟及欧洲经济区（EU / DE / FR / IT / ES / NL / PL / SE / NO / AT / BE / CZ / HU / RO / CH / GB） | EUR-Lex / EDPB / Gesetze im Internet（德）/ Légifrance（法）/ Normattiva（意）/ BOE（西）/ Overheid.nl（荷）/ ISAP（波）/ legislation.gov.uk（英）/ Fedlex（瑞）等 | `https://eur-lex.europa.eu`、`https://www.edpb.europa.eu`、`https://www.gesetze-im-internet.de`、`https://www.legifrance.gouv.fr`、`https://www.legislation.gov.uk` | 欧盟委员会重用决定（2011/833/EU）、Open Government Licence v3.0 及各国官方公报法定公开免版权/署名重用条款 | 是（遵守 `robots.txt`） | 是 | 是（保留官方文号与原始 URI） | 遵守 `robots.txt`，每周错峰巡检 | 仅抓取公开现行立法文本与监管指南 | 系统管理员已核验（2026-11） | 已确认 |
| 亚太与东盟扩充法域（JP / KR / TH / ID / MY / VN / PH / IN / BD / AU / NZ / HK） | 日本 e-Gov 法令检索 / 韩国国家法令情报中心 / 泰国皇家公报与 PDPC / 印尼 JDIH / 越南国家法律库 / 澳大利亚联邦立法登记册 / 香港电子版法例等 | `https://laws.e-gov.go.jp`、`https://www.law.go.kr`、`https://www.legislation.gov.au`、`https://www.elegislation.gov.hk` | 各国电子政务公开立法数据条款（Creative Commons Attribution / 政府法令公共领域公开原则） | 是（遵守 `robots.txt`） | 是 | 是（标明法令号与公布施行日期） | 遵守 `robots.txt`，礼貌限速访问 | 非拉丁文字法令入库强制校验字符层完整性 | 系统管理员已核验（2026-11） | 已确认 |
| 中东、中亚与欧亚法域（AE / SA / QA / KW / TR / IL / RU / KZ / UZ / KG） | 阿联酋立法门户（UAE Legislation）/ 沙特 SDAIA 与官方公报 / 土耳其 Resmi Gazete / 以色列司法部公开法规库 / 俄罗斯官方现行法律门户及欧亚各国公开立法库 | `https://uaelegislation.gov.ae`、`https://sdaia.gov.sa`、`https://www.resmigazete.gov.tr`、`http://pravo.gov.ru` | 官方立法文本公共领域公开或注明官方出处非排他合规查阅许可 | 是（遵守 `robots.txt`） | 是 | 是（保留原语种文本与官方编号） | 遵守 `robots.txt`，超时自动回退人工校验 | 部分政府门户对境外访问限流时采用已核验公报归档 | 系统管理员已核验（2026-11） | 已确认 |
| 美洲与非洲扩充法域（US / CA / MX / BR / AR / CL / CO / ZA / NG / KE / DZ） | 美国联邦法规汇编（eCFR / Congress.gov）/ 加拿大 Justice Laws / 巴西 Planalto 总统府立法门户 / 墨西哥 DOF / 南非政府公报 / 阿尔及利亚 SGG 公报等 | `https://www.ecfr.gov`、`https://laws-lois.justice.gc.ca`、`https://www.planalto.gov.br`、`https://www.joradp.dz` | 美国联邦政府作品公共领域规则（17 U.S.C. § 105）、Canada Open Government Licence 及各国官方公报公开立法条款 | 是（遵守 `robots.txt`） | 是 | 是（标明法典卷号与生效状态） | 遵守 `robots.txt`，168 小时错峰巡检 | 严格按现行有效（in_force / amended）过滤草案 | 系统管理员已核验（2026-11） | 已确认 |
| 国际汽车与信息安全标准组织（UNECE / ISO / IEC / SAE / ITU） | 联合国欧洲经济委员会车辆法规（WP.29）/ ISO、IEC、SAE、ITU 公开标准目录门户 | `https://unece.org/transport/vehicle-regulations`、`https://www.iso.org` | UNECE 公开政府间技术法规规则；商业国际标准仅登记公开题录元数据 | 仅限公开目录页 | 是（题录检索与合规对标） | UNECE 公开法规可引；ISO/IEC/SAE 仅展示公开题录与官方链接 | 遵守 `robots.txt` | **严禁镜像受版权保护的 ISO/SAE 商业标准正文** | 系统管理员已核验（2026-11） | 已确认 |

## 二、强制合规规则

1. 抓取程序 `PoliteFetcher` 全方法（`GET` / `POST`）必须读取并遵守 `robots.txt`，执行单站点限速与内网地址（SSRF）阻断，并携带明确的 `User-Agent`。
2. 完整保留官方原文文件、来源 URL、版本号、获取时间和 SHA-256 内容哈希，入库后受不可变审计约束，严禁篡改。
3. 国际与国家技术标准（共 311 项现行与即将实施标准：中国标准 148 项 + 国际通用标准 163 项）中，受商业版权保护的标准仅收录公开题录元数据、标准号、实施状态及官方门户跳转链接，不镜像受保护正文。
4. 官方站点使用条款发生变化或 `robots.txt` 限制调整时，管理端自动置为“待复核”并暂停定时抓取，经重新核验确认后方可恢复。
