/** UI classification from existing metadata only; never infer legal status from a title. */
export interface DirectoryDocument {
  id: string
  country_code: string
  title: string
  title_zh?: string | null
  document_type: string
}
export interface DirectoryCountry { code: string; name: string; count: number }

export const COUNTRY_NAMES: Record<string, string> = {
  SG: '新加坡', EG: '埃及', TN: '突尼斯', MA: '摩洛哥', LB: '黎巴嫩',
  ZZ: '国际',
  CN: '中国', EU: '欧盟', DE: '德国', FR: '法国', IT: '意大利', ES: '西班牙', NL: '荷兰', BE: '比利时',
  SE: '瑞典', PL: '波兰', AT: '奥地利', IE: '爱尔兰', PT: '葡萄牙', CZ: '捷克', HU: '匈牙利',
  DK: '丹麦', FI: '芬兰', GR: '希腊', RO: '罗马尼亚', BG: '保加利亚', SK: '斯洛伐克', SI: '斯洛文尼亚',
  HR: '克罗地亚', LT: '立陶宛', LV: '拉脱维亚', EE: '爱沙尼亚', LU: '卢森堡', CY: '塞浦路斯',
  GB: '英国', CH: '瑞士', NO: '挪威', RU: '俄罗斯', TR: '土耳其', UA: '乌克兰', BY: '白俄罗斯', RS: '塞尔维亚',
  JP: '日本', KR: '韩国', TH: '泰国', MY: '马来西亚', ID: '印度尼西亚', VN: '越南', PH: '菲律宾',
  BN: '文莱', KH: '柬埔寨', LA: '老挝', MM: '缅甸', MN: '蒙古',
  IN: '印度', PK: '巴基斯坦', BD: '孟加拉国', LK: '斯里兰卡', NP: '尼泊尔',
  KZ: '哈萨克斯坦', UZ: '乌兹别克斯坦', KG: '吉尔吉斯斯坦', TJ: '塔吉克斯坦', TM: '土库曼斯坦',
  AE: '阿联酋', SA: '沙特阿拉伯', QA: '卡塔尔', KW: '科威特', OM: '阿曼', BH: '巴林',
  IL: '以色列', JO: '约旦', IQ: '伊拉克', IR: '伊朗', DZ: '阿尔及利亚', LY: '利比亚',
  ZA: '南非', NG: '尼日利亚', KE: '肯尼亚', ET: '埃塞俄比亚',
  US: '美国', CA: '加拿大', MX: '墨西哥', BR: '巴西', AR: '阿根廷', CL: '智利', PE: '秘鲁', CO: '哥伦比亚',
  AU: '澳大利亚', NZ: '新西兰', HK: '中国香港', MO: '中国澳门',
}
const TYPE_ALIASES: Record<string, string> = {
  ACT: 'LAW',
  SUBSIDIARY: 'REGULATION',
  DECREE: 'REGULATION',
  RULE: 'REGULATION',
  ORDER: 'REGULATION',
  GUIDELINE: 'GUIDANCE',
  NOTICE: 'GUIDANCE',
  CIRCULAR: 'GUIDANCE',
}
const LEVELS: Record<string, { name: string; rank: number }> = {
  LAW: { name: '法律', rank: 0 },
  REGULATION: { name: '法规与实施规则', rank: 1 },
  GUIDANCE: { name: '监管指南与框架', rank: 2 },
  STANDARD: { name: '标准', rank: 3 },
  ADMISSION: { name: '准入申报材料', rank: 3.5 },
  FORM: { name: '申报表与材料', rank: 4 },
  OTHER: { name: '其他文件', rank: 5 },
}

export function directoryLevels<T extends DirectoryDocument>(documents: T[], country: string) {
  const groups = new Map<string, { key: string; name: string; rank: number; items: T[] }>()
  for (const item of documents) {
    if (item.country_code !== country) continue
    const raw = (item.document_type || '').trim()
    const upper = raw ? raw.toUpperCase() : '__unclassified__'
    const key = TYPE_ALIASES[upper] || upper
    const meta = LEVELS[key] || { name: key === '__unclassified__' ? '未分类' : `其他类型（${key}）`, rank: 6 }
    if (!groups.has(key)) groups.set(key, { key, ...meta, items: [] })
    groups.get(key)!.items.push(item)
  }
  for (const group of groups.values()) group.items.sort((a, b) =>
    (a.title_zh || a.title).localeCompare(b.title_zh || b.title, 'zh-CN', { numeric: true }) || a.id.localeCompare(b.id),
  )
  return [...groups.values()].sort((a, b) => a.rank - b.rank || a.key.localeCompare(b.key))
}

export function directoryCountries(documents: DirectoryDocument[], names: Record<string, string> = {}): DirectoryCountry[] {
  const counts = new Map<string, number>()
  for (const d of documents) counts.set(d.country_code, (counts.get(d.country_code) || 0) + 1)
  return [...counts].sort(([a], [b]) => a.localeCompare(b)).map(([code, count]) => ({
    code, name: (names[code] && names[code] !== code ? names[code] : COUNTRY_NAMES[code]) || code, count,
  }))
}

/** Keep the selected document reachable even in a large category. */
export function documentPage<T>(items: T[], page: number, size = 8) {
  const pageSize = Number.isFinite(size) ? Math.max(1, Math.floor(size)) : 8
  const pages = Math.max(1, Math.ceil(items.length / pageSize))
  const current = Math.max(1, Math.min(pages, Math.floor(page) || 1))
  return { page: current, pages, items: items.slice((current - 1) * pageSize, current * pageSize) }
}

/**
 * User-side directory: regulations grouped by the part of the automotive cross-border data business
 * they serve (personal data and transfer, cybersecurity, vehicle approval and autonomous driving,
 * telecoms and radio), the core law and its main rules first. Documents kept only for completeness
 * (do-not-call register, lists of statutory bodies, commencement notices) are gathered in one group
 * at the end. Documents not classified yet fall back to their document type.
 */
export interface TopicDocument extends DirectoryDocument {
  business_topic?: string | null
  business_weight?: string | null
}
const TOPICS: Record<string, { name: string; rank: number }> = {
  personal_data: { name: '个人数据保护与跨境传输', rank: 0 },
  cybersecurity: { name: '网络安全与关键信息基础设施', rank: 1 },
  vehicle: { name: '车辆准入、自动驾驶与道路交通', rank: 2 },
  telecom: { name: '车联网通信与无线电设备', rank: 3 },
  other: { name: '其他相关法规', rank: 4 },
}
const WEIGHT_RANK: Record<string, number> = { core: 0, supporting: 1 }
export const PERIPHERAL_GROUP = '一般关联与配套文件'

export function directoryTopics<T extends TopicDocument>(documents: T[], country: string) {
  const inCountry = documents.filter((d) => d.country_code === country)
  if (!inCountry.some((d) => d.business_topic)) return directoryLevels(inCountry, country)
  const groups = new Map<string, { key: string; name: string; rank: number; items: T[] }>()
  for (const item of inCountry) {
    const peripheral = item.business_weight === 'peripheral'
    const key = peripheral ? '__peripheral__' : item.business_topic || 'other'
    const meta = peripheral ? { name: PERIPHERAL_GROUP, rank: 9 } : TOPICS[key] || TOPICS.other
    if (!groups.has(key)) groups.set(key, { key, ...meta, items: [] })
    groups.get(key)!.items.push(item)
  }
  for (const group of groups.values()) group.items.sort((a, b) =>
    (WEIGHT_RANK[a.business_weight || ''] ?? 2) - (WEIGHT_RANK[b.business_weight || ''] ?? 2) ||
    (a.title_zh || a.title).localeCompare(b.title_zh || b.title, 'zh-CN', { numeric: true }) || a.id.localeCompare(b.id),
  )
  return [...groups.values()].sort((a, b) => a.rank - b.rank)
}
