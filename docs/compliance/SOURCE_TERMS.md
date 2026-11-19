# 法规来源使用条款登记

每个官方来源站点，在**抓取前**登记其使用条款，并经管理端「采集站点」使用条款确认后方可启用定时巡检。
系统严格保留官方原文归档、来源 URL、版本号、抓取时间与 SHA-256 内容校验值。

| 国家 | 官方站点 / 发布机构 | 官方入口 | 授权 / 条款依据 | 允许礼貌巡检 | 允许企业内部合规使用 | 允许展示官方法规条文 | robots.txt 与限速策略 | 访问限制说明 | 确认状态（基准日） | 状态 |
|---|---|---|---|---|---|---|---|---|---|---|
| 中国 | 国家互联网信息办公室 / 工信部 / 国家标准全文公开系统 | `https://www.cac.gov.cn`、`https://openstd.samr.gov.cn` | 中华人民共和国政府信息公开条例（官方规范性文件公开发布） | 是（低频限速） | 是 | 是（保留官方出处与文号） | 遵守 `robots.txt`，单域名间隔 ≥ 5 秒 | 标准全文仅登记题录与官方跳转链接 | 系统管理员已核验（2026-11） | 已确认 |
| 新加坡 | Singapore Statutes Online (AGC) / PDPC / CSA / LTA / IMDA | `https://sso.agc.gov.sg`、`https://www.pdpc.gov.sg` | Singapore Open Data Licence / AGC Terms of Use（允许注明来源引用与法规合规查阅） | 是（遵守 `robots.txt`） | 是 | 是（标明官方来源与版本日期） | 严格遵守 `robots.txt`，礼貌限速抓取 | 禁止高频并发请求 | 系统管理员已核验（2026-11） | 已确认 |
| 埃及 | 埃及官方公报 / 通信与信息技术部（MCIT）/ NTRA / GOEIC | `https://mcit.gov.eg`、`https://www.ntra.gov.eg`、`https://www.goeic.gov.eg` | 埃及第 151 号法律及官方公报公开立法文件条款（公开官方立法文本） | 仅针对 `robots.txt` 正常响应站点启用 | 是 | 是（附官方公报号与原文对照） | 若 `robots.txt` 返回 5xx 自动暂停抓取并改走人工归档校验 | 部分政府门户偶发区域网络抖动 | 系统管理员已核验（2026-11） | 已确认 |
| 摩洛哥 | 摩洛哥政府秘书处官方公报（SGG）/ CNDP / ANRT | `https://www.sgg.gov.ma`、`https://www.cndp.ma`、`https://www.anrt.ma` | SGG Bulletin Officiel 摩洛哥官方公报公开法律公文与 CNDP 官方公开审议决议 | 是（遵守 `robots.txt`） | 是 | 是（标明公报期号与审议文号） | 遵守 `robots.txt`，低频定时巡检 | 扫描版公报入库后执行双路版面核验 | 系统管理员已核验（2026-11） | 已确认 |
| 突尼斯 | 突尼斯官方印刷局公报（IORT）/ INPDP / CERT / ANCE / ATTT | `http://www.iort.gov.tn`、`https://www.inpdp.nat.tn`、`https://www.ance.tn` | JORT 突尼斯共和国官方公报公开法令及监管机构公开办事指南 | 是（遵守 `robots.txt`） | 是 | 是（标明 JORT 法令编号与出处） | 遵守 `robots.txt`，单站点低频轮询 | 部分历史公报采用官方公报原件归档 | 系统管理员已核验（2026-11） | 已确认 |
| 黎巴嫩 | 黎巴嫩官方公报（PCM）/ 议会立法库 / TRA / 黎巴嫩央行法规库 | `http://pcm.gov.lb`、`https://www.tra.gov.lb`、`https://www.bdl.gov.lb` | 黎巴嫩共和国公开立法与官方公报公共领域文件规则 | 是（遵守 `robots.txt`） | 是 | 是（标明法律编号与发布年份） | 遵守 `robots.txt`，超时自动退避 | 低频周期巡检（≥ 24 小时） | 系统管理员已核验（2026-11） | 已确认 |

## 规则

1. 抓取程序必须读取并遵守 `robots.txt`，执行单站点限速，并携带明确的 `User-Agent`。
2. 保留官方原文文件、来源 URL、版本号、获取时间和 SHA-256 内容哈希，入库后不可篡改。
3. 国际与国家技术标准（如 ISO / UNECE / GB）仅收录公开题录元数据、标准号、实施状态及官方标准门户跳转链接，不违规镜像受版权保护的标准商业正文。
4. 官方站点使用条款发生变化或 `robots.txt` 限制调整时，管理端自动置为“待复核”并暂停定时抓取，重新确认后方可恢复。
