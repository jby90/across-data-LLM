<script setup lang="ts">
import { ElMessage } from 'element-plus'
import { computed, defineAsyncComponent, nextTick, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '../api'
import { localTime, officialTextLabel } from '@cyshuhe/shared'
import { articleLabel } from '@cyshuhe/shared/src/articleLabel'
import RegulationDirectory from '../components/RegulationDirectory.vue'
import { alignBilingualParagraphs, reflow } from '@cyshuhe/shared/src/reflow'
import { splitLegalText, type LegalTerm } from '@cyshuhe/shared/src/legalText'
import {
  cleanTermReference,
  closeTermPopover,
  dedupeParagraphLines,
  handleTermDelegation,
  normTermText,
  registerDocTitle,
  registerTerm,
} from '../termPopover'
const RegulationRelations = defineAsyncComponent(
  () => import('../components/RegulationRelations.vue'),
)
const VersionCompare = defineAsyncComponent(
  () => import('../components/VersionCompare.vue'),
)
const CountryWhitepaper = defineAsyncComponent(() => import('../components/CountryWhitepaper.vue'))
const readingView = ref('clauses')
// 第 7.5 项 规则 18、21：法规目录可收起，给条文阅读让宽；收起与否记在本机
const DIR_KEY = 'cyshuhe.kb.dir-collapsed'
const dirCollapsed = ref(localStorage.getItem(DIR_KEY) === '1')
watch(dirCollapsed, (v) => localStorage.setItem(DIR_KEY, v ? '1' : '0'))
async function readRelationBasis(clauseId: string) {
  if (!detail.value) return
  readingView.value = 'clauses'
  await openDocument(detail.value.regulation.id, clauseId)
}
interface Country {
  country_code: string
  country_name_zh: string
  documents_count: number
  last_updated_at?: string | null
}
interface Regulation {
  id: string
  country_code: string
  title: string
  title_zh?: string | null
  business_topic?: string | null
  business_weight?: string | null
  document_type: string
  document_type_zh: string
  publisher: string
  language: string
  source_url: string
  version_no: number
  legal_status: string
  clauses_count: number
  relations_count?: number
  primary?: boolean
}
interface Clause {
  clause_id: string
  part_ids?: string[]
  preamble?: boolean
  position?: number
  ordinal: number
  clause_number: string | null
  heading: string | null
  section_path?: string | null
  original_text: string
  translated_text: string | null
  translation_status?: string
  scope_note?: string | null
}
interface Detail {
  version_id: string | null
  clauses_total: number
  clause_offset: number
  regulation: Regulation
  clauses: Clause[]
  versions: Array<{
    version_no: number
    legal_status: string
    version_label?: string | null
    published_date?: string | null
  }>
}
interface Topic {
  id: string
  topic: string
  topic_label: string
  title: string
  document_id: string
  document_title: string
  clause_id: string
  clause_number: string
  version_no: number
  quote: string
  source_url: string
  facts?: { label: string; text: string }[]
  source?: string
}
interface StandardItem {
  id: string
  country_code: string
  country_name_zh: string
  number: string
  title: string
  kind_code: string
  kind: string
  status_code: string
  status: string
  published_on: string | null
  effective_on: string | null
  topics: string[]
  detail_url: string | null
  publisher: string | null
}
const countries = ref<Country[]>([]),
  regulations = ref<Regulation[]>([]),
  detail = ref<Detail | null>(null)
const country = ref(''),
  tab = ref('countries'),
  search = ref(''),
  statusFilter = ref(''),
  clauseSearch = ref(''),
  topic = ref('')
const terms = ref<LegalTerm[]>([]),
  docTerms = ref<LegalTerm[]>([]),
  topics = ref<Topic[]>([]),
  standards = ref<StandardItem[]>([])
const standardScope = ref<'local' | 'domestic' | 'intl' | 'all'>('all'),
  standardKind = ref(''),
  standardPage = ref(1)
const busy = ref(false),
  detailBusy = ref(false),
  page = ref(1),
  topicPage = ref(1),
  termPage = ref(1),
  selectedClause = ref(''),
  detailError = ref(''),
  expanded = ref(new Set<string>())
const LONG = 2400 // 超过这个长度的条文先收起，点展开全文再看
function toggleClause(id: string) {
  const next = new Set(expanded.value)
  if (next.has(id)) next.delete(id)
  else next.add(id)
  expanded.value = next
}
const SEARCH_HINT: Record<string, string> = {
  library: '按法规名称或多关键词筛选（空格分隔）',
  topics: '搜索合规主题与条文（支持多词模糊匹配）',
  terms: '搜索法律术语（支持多词模糊匹配）',
  standards: '按标准编号、标准名称或发布机构搜索',
}
const searchTokens = computed(() =>
  search.value
    .trim()
    .toLowerCase()
    .split(/\s+/)
    .filter(Boolean),
)
function matchesSearch(text: string): boolean {
  if (!searchTokens.value.length) return true
  const lower = text.toLowerCase()
  return searchTokens.value.every((tok) => lower.includes(tok))
}
function highlightSegments(text: string): { text: string; hit: boolean }[] {
  const raw = text || ''
  if (!searchTokens.value.length || !raw) return [{ text: raw, hit: false }]
  const escaped = searchTokens.value
    .map((t) => t.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'))
    .filter(Boolean)
  if (!escaped.length) return [{ text: raw, hit: false }]
  const rx = new RegExp(`(${escaped.join('|')})`, 'gi')
  const parts = raw.split(rx).filter((p) => p.length > 0)
  return parts.map((p) => ({
    text: p,
    hit: searchTokens.value.some((tok) => p.toLowerCase() === tok),
  }))
}
let requestNo = 0,
  docRequest = 0
const showPeripheral = ref(false)
const allCountryRegs = reactive<Record<string, Regulation[]>>({})
const activeStandard = ref<StandardItem | null>(null)
const stdDrawerOpen = ref(false)

function openStandardDetail(s: StandardItem) {
  activeStandard.value = s
  stdDrawerOpen.value = true
}

function stdScopeSummary(s: StandardItem): string {
  const topicsText = s.topics?.length ? s.topics.join('、') : ''
  const num = (s.number || '').toUpperCase()
  let domain = '汽车数据处理、车联网信息安全及跨境业务技术合规管理'
  if (/21434|CYBER|信息安全|网络安全/.test(`${num} ${s.title}`)) {
    domain = '道路车辆全生命周期网络安全工程、威胁分析与风险评估及车端安全防护'
  } else if (/24089|OTA|软件升级/.test(`${num} ${s.title}`)) {
    domain = '汽车软件在线升级（远程升级）管理体系、升级包安全校验与备案合规'
  } else if (/UN\s*R155|R155/.test(num)) {
    domain = '联合国车辆网络安全管理体系（CSMS）及海外属地车辆型式批准认证'
  } else if (/UN\s*R156|R156/.test(num)) {
    domain = '联合国车辆软件升级管理体系（SUMS）及海外属地车辆型式批准认证'
  } else if (/数据|个人信息|隐私|27701|27001/.test(s.title)) {
    domain = '车内外个人数据与重要数据识别、车端匿名化脱敏、隐私保护及跨境安全传输'
  } else if (/自动驾驶|智能网联|辅助驾驶|3450|21448/.test(`${num} ${s.title}`)) {
    domain = '智能网联汽车自动驾驶功能安全、预期功能安全及道路测试准入评估'
  }
  return topicsText ? `归属领域：${topicsText}。主要适用于${domain}。` : `主要适用于${domain}。`
}

function stdStatusExplain(s: StandardItem): string {
  const status = s.status || '现行'
  if (s.status_code === 'withdrawn' || status.includes('废止')) {
    return '该标准已废止或被新版替代，仅供历史车型技术对标参考，新立项研发与申报请采用现行替代标准。'
  }
  if (s.status_code === 'upcoming' || status.includes('即将')) {
    return '该标准已正式发布但尚未到正式实施日期，建议车企在新车型立项与合规评估中提前对照预研。'
  }
  if (s.status_code === 'draft' || status.includes('制定')) {
    return '该标准尚处于起草或征求意见阶段，可作为前瞻技术合规参考。'
  }
  if (s.kind_code === 'mandatory' || s.kind.includes('强制')) {
    return '当前现行有效的强制性标准，相关车型研发制造、数据出境安全评估及市场准入认证须严格满足其技术要求。'
  }
  if (INTL_STD_CODES.has(s.country_code)) {
    return '当前现行有效的国际通行标准，广泛用于海外目标市场准入型式认证及跨国车企安全体系建设对标。'
  }
  return '当前现行有效的推荐性/指导性技术标准，可作为企业落实法定数据安全保护义务、开展技术核验的行业通行依据。'
}

function filterRegList(list: Regulation[]): Regulation[] {
  return list.filter(
    (d) =>
      (search.value.trim() || showPeripheral.value || d.business_weight !== 'peripheral') &&
      (!statusFilter.value || (d.legal_status || 'in_force') === statusFilter.value) &&
      matchesSearch(`${d.title_zh || ''} ${d.title}`),
  )
}

watch(country, () => { showPeripheral.value = false })
const isFilteringRegs = computed(() => Boolean(statusFilter.value || search.value.trim() || showPeripheral.value))
const directoryCountries = computed(() =>
  countries.value.map((c) => {
    if (c.country_code === country.value && !busy.value) {
      return {
        code: c.country_code,
        name: c.country_name_zh,
        count: isFilteringRegs.value ? filteredDocs.value.length : c.documents_count,
      }
    }
    const cached = allCountryRegs[c.country_code]
    return {
      code: c.country_code,
      name: c.country_name_zh,
      count: isFilteringRegs.value && cached ? filterRegList(cached).length : c.documents_count,
    }
  }),
)
// 与车辆数据业务关系较远的配套文件（营销电话登记、机构名单公告等）默认收起，按名称搜索或点击“展开全部收录”时展示
const filteredDocs = computed(() => filterRegList(regulations.value))

watch([statusFilter, search], () => {
  closeTermPopover()
  if (busy.value || !detail.value) return
  const currentId = detail.value.regulation.id
  if (!filteredDocs.value.some((d) => d.id === currentId)) {
    ++docRequest
    detailBusy.value = false
    detailError.value = ''
    detail.value = null
  }
})
const peripheralCount = computed(() =>
  search.value.trim()
    ? 0
    : regulations.value.filter(
        (d) =>
          d.business_weight === 'peripheral' &&
          (!statusFilter.value || (d.legal_status || 'in_force') === statusFilter.value),
      ).length,
)
// 同一个术语在几份法规里都有定义时合成一张卡：先看第一份的定义，其余列在其他法规中的定义里
const filteredTerms = computed(() => {
  const groups = new Map<string, LegalTerm[]>()
  for (const t of terms.value) {
    const text = `${t.source_term} ${t.target_zh} ${t.definition_zh || ''} ${t.definition_original || ''}`
    if (!matchesSearch(text)) continue
    const key = t.source_term.trim().toLowerCase()
    groups.set(key, [...(groups.get(key) || []), t])
  }
  return [...groups.values()].map(([first, ...others]) => ({ ...first, others }))
})
const topicOptions = computed(() => [
  ...new Map(topics.value.map((t) => [t.topic, t.topic_label])).entries(),
])
const topicGroups = computed(() =>
  topicOptions.value.map(([key, label]) => {
    const rows = topics.value.filter((t) => t.topic === key)
    return {
      key,
      label,
      count: rows.length,
      documents: new Set(rows.map((t) => t.document_id)).size,
    }
  }),
)
function resetSearchPages() {
  topicPage.value = 1
  termPage.value = 1
  standardPage.value = 1
}
const INTL_STD_CODES = new Set(['INTL', 'ISO', 'UN', 'IEC', 'EU', 'ZZ'])
const STD_COUNTRY_FALLBACK: Record<string, string> = {
  ZZ: '国际通用标准',
  INTL: '国际通用标准',
  ISO: '国际通用标准',
  UN: '联合国法规标准',
  IEC: '国际电工标准',
  EU: '欧盟标准',
  CN: '中国',
}
function stdCountryLabel(s: StandardItem): string {
  if (s.country_code === 'ZZ' || s.country_name_zh === 'ZZ' || INTL_STD_CODES.has(s.country_code)) {
    const num = (s.number || '').trim().toUpperCase()
    if (num.startsWith('ISO/SAE')) return '国际标准（ISO/SAE）'
    if (num.startsWith('ISO/IEC')) return '国际标准（ISO/IEC）'
    if (num.startsWith('ISO')) return '国际标准（ISO）'
    if (num.startsWith('IEC')) return '国际电工标准（IEC）'
    if (num.startsWith('ITU')) return '国际电信标准（ITU）'
    if (num.startsWith('UN') || num.startsWith('ECE')) return '联合国车辆法规（UN/WP.29）'
    if (num.startsWith('SAE')) return '国际汽车工程标准（SAE）'
    if (num.startsWith('ETSI')) return '欧洲电信标准（ETSI）'
    return '国际通用标准'
  }
  return s.country_name_zh || STD_COUNTRY_FALLBACK[s.country_code] || s.country_code
}
const domesticStandardsCount = computed(() =>
  country.value ? standards.value.filter((s) => s.country_code === country.value).length : 0,
)
const intlStandardsCount = computed(() =>
  standards.value.filter((s) => INTL_STD_CODES.has(s.country_code)).length,
)
const cnStandardsCount = computed(() =>
  standards.value.filter((s) => s.country_code === 'CN').length,
)
const localStandardsCount = computed(() =>
  standards.value.filter(
    (s) => (country.value && s.country_code === country.value) || INTL_STD_CODES.has(s.country_code),
  ).length,
)
const hasGuidanceStandards = computed(() =>
  standards.value.some((s) => s.kind_code === 'guidance'),
)
function exitToOverview() {
  ++requestNo
  ++docRequest
  closeTermPopover()
  cameFromTab.value = ''
  country.value = ''
  detail.value = null
  tab.value = 'countries'
  search.value = ''
  statusFilter.value = ''
  topic.value = ''
  standardScope.value = 'all'
  standardKind.value = ''
  standardPage.value = 1
  if (route.query.country || route.query.doc || route.query.clause) {
    void router.replace({ path: '/knowledge' })
  }
}
const filteredStandards = computed(() => {
  const inScope = (s: StandardItem): boolean => {
    if (!country.value) {
      if (standardScope.value === 'intl') return INTL_STD_CODES.has(s.country_code)
      if (standardScope.value === 'domestic') return s.country_code === 'CN'
      return true
    }
    if (standardScope.value === 'all') return true
    if (standardScope.value === 'domestic') return s.country_code === country.value
    if (standardScope.value === 'intl') return INTL_STD_CODES.has(s.country_code)
    if (localStandardsCount.value === 0) return true
    return s.country_code === country.value || INTL_STD_CODES.has(s.country_code)
  }
  return standards.value.filter(
    (s) =>
      inScope(s) &&
      (!standardKind.value || s.kind_code === standardKind.value) &&
      matchesSearch(
        `${s.number} ${s.title} ${stdCountryLabel(s)} ${s.publisher || ''} ${(s.topics || []).join(' ')}`,
      ),
  )
})
function chooseTopic(key: string) {
  topic.value = key
  topicPage.value = 1
}
// 译文长度和原文差得太多（译文比原文还长一大截，或短得多）：说明两边覆盖的范围不一样，不硬对齐，改成整条各一栏、各自滚动（目录里的连续点线不计入原文有效字数）
function columnsOnly(c: Clause): boolean {
  if (originalIsZh.value || !c.translated_text) return false
  const strippedOrig = bodyOf(c).replace(/[.…·\-_]{4,}/g, ' ')
  const a = strippedOrig.length, b = c.translated_text.replace(/[.…·\-_]{4,}/g, ' ').length
  if (a < 400) return false
  const r = b / a
  return r > 1.25 || r < 0.15
}
function rowsOf(c: Clause): [string, string][] {
  const body = bodyOf(c)
  if (originalIsZh.value) return [[body, '']]
  const stripped = body.length < c.original_text.length ? (c.heading || '').trim() : ''
  return alignBilingualParagraphs(body, c.translated_text || '', stripped)
}
// 屏幕不够宽（目录展开后正文区不到约 860px）时，进入阅读默认收起目录
if (window.innerWidth < 1600 && localStorage.getItem('cyshuhe.kb.dir-collapsed') === null) dirCollapsed.value = true
function bodyOf(c: Clause) {
  const heading = (c.heading || '').trim()
  const text = c.original_text
  return heading && text.startsWith(heading + '\n') ? text.slice(heading.length + 1) : text
}
// 条的标题：印在条文上方的才算（正文以它开头，或正文里没有它）；切分时把正文第一句当成的标题不显示
function headingOf(c: Clause) {
  const heading = (c.heading || '').replace(/[（(]自动识别[)）]/g, '').trim()
  if (!heading) return ''
  const text = c.original_text.trimStart()
  return text.startsWith(heading) || !text.includes(heading) ? heading : ''
}
// 条标题：条号 · 标题；没有条号的段落用它自己的标题，第一条之前的叫前言，其余按在全文中的位置叫第 N 段
function clauseTitle(c: Clause) {
  const heading = headingOf(c)
  const number = articleLabel(clauseLabel(c.clause_number))
  if (number) return heading ? `${number} · ${heading}` : number
  return heading || (c.preamble ? '前言' : `第 ${c.position || c.ordinal} 段`)
}
function clauseLabel(value: string | null | undefined) {
  if (!value) return ''
  return value.length > 70
    ? value.match(/^(?:[A-Za-zÀ-ÿ.]+\s*)?\d+(?:\.\d+)*(?:\([a-z0-9]+\))*/)?.[0] ||
        value.slice(0, 65) + '…'
    : value
}
const filteredTopics = computed(() =>
  topics.value.filter(
    (t) =>
      (!topic.value || t.topic === topic.value) &&
      matchesSearch(
        `${t.title} ${t.document_title} ${t.quote} ${(t.facts || []).map((f) => f.text).join(' ')}`,
      ),
  ),
)
const pageClauses = computed(() => detail.value?.clauses || [])
// 原文就是中文的法规（中国法规）不需要中文对照栏
const originalIsZh = computed(() => (detail.value?.regulation.language || '').toLowerCase().startsWith('zh'))

interface Seg {
  text: string
  termKey: string
}
interface RenderedClauseView {
  colsOnly: boolean
  origSegs: Seg[]
  zhSegs: Seg[]
  rows: Array<{ orig: Seg[]; zh: Seg[]; hasZh: boolean }>
}

function scopedTermsForClause(all: LegalTerm[], clauseId: string, sectionPath?: string | null): LegalTerm[] {
  if (!all.length) return []
  return all.filter(
    (t) =>
      !t.source_scope ||
      t.source_scope === 'document' ||
      (t.source_scope === 'clause' && t.clause_id === clauseId) ||
      (t.source_scope === 'chapter' && !!t.scope_path && sectionPath?.includes(t.scope_path)),
  )
}

function toSegs(raw: string, scopedTerms: LegalTerm[]): Seg[] {
  const cleaned = reflow(raw)
  if (!cleaned) return []
  return splitLegalText(cleaned, scopedTerms).map((p) =>
    p.term ? { text: p.text, termKey: registerTerm(p.term) } : { text: p.text, termKey: '' },
  )
}

const renderedClauseMap = computed(() => {
  const map = new Map<string, RenderedClauseView>()
  const allTerms = docTerms.value
  for (const c of pageClauses.value) {
    const scoped = scopedTermsForClause(allTerms, c.clause_id, c.section_path)
    const colsOnly = columnsOnly(c)
    if (colsOnly) {
      map.set(c.clause_id, {
        colsOnly: true,
        origSegs: toSegs(bodyOf(c), scoped),
        zhSegs: toSegs(c.translated_text || '', scoped),
        rows: [],
      })
    } else {
      const pairs = rowsOf(c)
      map.set(c.clause_id, {
        colsOnly: false,
        origSegs: [],
        zhSegs: [],
        rows: pairs.map(([orig, zh]) => ({
          orig: toSegs(orig, scoped),
          zh: zh ? toSegs(zh, scoped) : [],
          hasZh: Boolean(zh),
        })),
      })
    }
  }
  return map
})

function termHasDistinctOriginal(t: LegalTerm): boolean {
  const orig = dedupeParagraphLines(t.definition_original)
  if (!orig) return false
  const zh = dedupeParagraphLines(t.definition_zh)
  if (!zh) return true
  if (normTermText(orig) === normTermText(zh)) return false
  if (!/[A-Za-zÀ-ÿ\u0600-\u06FF]/.test(orig) && (normTermText(zh).includes(normTermText(orig)) || normTermText(orig).includes(normTermText(zh)))) {
    return false
  }
  return true
}

const knownStatus = (s: string) => !!s && s !== 'unknown' && s in legalStatus
const legalStatus: Record<string, string> = {
  in_force: '现行',
  amended: '已修订',
  repealed: '已废止',
  unknown: '有效状态未标注',
  draft: '草案，尚未生效',
}
async function chooseCountry(code: string, open?: { doc: string; clause: string }) {
  const id = ++requestNo
  ++docRequest
  closeTermPopover()
  cameFromTab.value = ''
  country.value = code
  if (tab.value === 'countries' || (code === 'CN' && tab.value === 'whitepaper')) {
    tab.value = code === 'CN' ? 'library' : 'whitepaper'
  }
  busy.value = true
  detail.value = null
  search.value = ''
  statusFilter.value = ''
  topic.value = ''
  topicPage.value = 1
  termPage.value = 1
  standardPage.value = 1
  standardScope.value = 'local'
  standardKind.value = ''
  try {
    const [docs, ts, tp] = await Promise.all([
      api.get<Regulation[]>(`/api/knowledge/regulations?country_code=${code}`),
      api.get<{ items: LegalTerm[] }>(`/api/knowledge/terminology?country_code=${code}`),
      api.get<{ items: Topic[] }>(`/api/knowledge/topics?country_code=${code}`),
    ])
    if (id !== requestNo) return
    allCountryRegs[code] = docs
    docs.forEach((d) => registerDocTitle(d.id, d.title_zh || d.title))
    regulations.value = docs
    terms.value = ts.items
    topics.value = tp.items
    // 打开页面先看到该国数据合规的核心法律（由后端按图谱关系数和条文数选出），没有时看第一份法律
    const first =
      docs.find((d) => d.primary) ??
      docs.find((d) => d.document_type?.toUpperCase() === 'LAW') ??
      docs[0]
    if (open) await openDocument(open.doc, open.clause)  // 从别的页面链过来：直接打开那部法规的那一条
    else if (first) await openDocument(first.id)
  } catch (e) {
    if (id === requestNo) {
      regulations.value = []
      terms.value = []
      topics.value = []
      ElMessage.error((e as Error).message)
    }
  } finally {
    if (id === requestNo) busy.value = false
  }
}
async function openDocument(id: string, clauseId = '') {
  clearTimeout(clauseSearchTimer)
  closeTermPopover()
  if (id !== detail.value?.regulation.id) readingView.value = 'clauses'
  const req = ++docRequest
  detailBusy.value = true
  clauseSearch.value = ''
  page.value = 1
  selectedClause.value = clauseId
  detailError.value = ''
  try {
    const d = await api.get<Detail>(
      `/api/knowledge/regulations/${id}${clauseId ? `?focus_clause_id=${clauseId}` : ''}`,
    )
    if (req !== docRequest) return
    const t = await api.get<{ items: LegalTerm[] }>(
      `/api/knowledge/terminology?country_code=${country.value}&document_id=${id}${d.version_id ? `&version_id=${d.version_id}` : ''}`,
    )
    if (req !== docRequest) return
    registerDocTitle(d.regulation.id, d.regulation.title_zh || d.regulation.title)
    detail.value = d
    docTerms.value = t.items
    void loadCitedBy(id, req)
    page.value = Math.floor(d.clause_offset / 12) + 1
    if (clauseId) {
      const hit = d.clauses.find((c) => c.clause_id === clauseId || c.part_ids?.includes(clauseId))
      if (hit) selectedClause.value = clauseId = hit.clause_id
      await nextTick()
      if (req === docRequest)
        document.getElementById(`clause-${clauseId}`)?.scrollIntoView({
          block: 'start',
          behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches
            ? 'instant'
            : 'smooth',
        })
    }
  } catch (e) {
    if (req === docRequest) {
      detail.value = null
      detailError.value = (e as Error).message || '这份法规暂时无法打开'
    }
  } finally {
    if (req === docRequest) detailBusy.value = false
  }
}
// 条文被本企业哪些合规文件引用：题旁依据、正文依据表、论述引用（按条文 id 比，后端算），点了回到那一题或那份文件
interface CiteRef {
  project_id: string; project_title: string; doc_code: string; doc_name: string
  where: 'question' | 'body' | 'paper'; question_key?: string; question_label?: string; section?: string; link: string
}
const citedBy = ref<Record<string, CiteRef[]>>({})
async function loadCitedBy(id: string, req: number) {
  citedBy.value = {}
  try {
    const r = await api.get<{ clauses: Record<string, CiteRef[]> }>(`/api/analysis/doc-projects/cited-by?document_id=${id}`)
    if (req === docRequest) citedBy.value = r.clauses
  } catch {
    // 拿不到引用不影响阅读条文
  }
}
// 按项目分组：每个项目一行标题，下面是这个项目的引用；每组默认显示前 3 条，其余点展开
const CITED_SHOWN = 3
interface CitedGroup { key: string; title: string; lines: Array<{ text: string; link: string }> }
function citedGroups(c: Clause): CitedGroup[] {
  const refs = [c.clause_id, ...(c.part_ids || [])].flatMap((i) => citedBy.value[i] || [])
  const groups = new Map<string, { title: string; lines: Map<string, { text: string; link: string }> }>()
  for (const r of refs) {
    const g = groups.get(r.project_id) ?? { title: r.project_title, lines: new Map() }
    groups.set(r.project_id, g)
    if (r.where === 'question') {
      // 一题用在几份文件里：合成一行，写明用在哪几份
      const key = `q:${r.question_key}`
      const docs = refs.filter((x) => x.where === 'question' && x.project_id === r.project_id && x.question_key === r.question_key).map((x) => x.doc_name)
      g.lines.set(key, { text: `填写依据：${r.question_label}（用在${[...new Set(docs)].join('、')}）`, link: r.link })
    } else {
      const key = `${r.where}:${r.doc_code}`
      const where = r.where === 'paper' ? '论述引用' : `正文引用${r.section ? `（${r.section}）` : ''}`
      if (!g.lines.has(key)) g.lines.set(key, { text: `${r.doc_name} ${where}`, link: r.link })
    }
  }
  return [...groups.entries()].map(([pid, g]) => ({ key: `${c.clause_id}:${pid}`, title: g.title, lines: [...g.lines.values()] }))
}
const citedCount = (c: Clause) => citedGroups(c).reduce((n, g) => n + g.lines.length, 0)
const citedOpen = ref(new Set<string>())
function toggleCited(key: string) {
  const next = new Set(citedOpen.value)
  if (next.has(key)) next.delete(key)
  else next.add(key)
  citedOpen.value = next
}
function turnClausePage(value: number) {
  if (value === page.value) return
  page.value = value
  void loadClausePage()
}
async function loadClausePage() {
  const doc = detail.value
  if (!doc) return
  const req = ++docRequest
  detailBusy.value = true
  try {
    const q = new URLSearchParams({
      clause_offset: String((page.value - 1) * 12),
      clause_limit: '12',
      keyword: clauseSearch.value,
    })
    if (doc.version_id) q.set('version_id', doc.version_id)
    const d = await api.get<Detail>(`/api/knowledge/regulations/${doc.regulation.id}?${q}`)
    if (req === docRequest) detail.value = d
  } catch (e) {
    if (req === docRequest) ElMessage.error((e as Error).message)
  } finally {
    if (req === docRequest) detailBusy.value = false
  }
}
let clauseSearchTimer: ReturnType<typeof setTimeout> | undefined
function searchClauses() {
  page.value = 1
  clearTimeout(clauseSearchTimer)
  clauseSearchTimer = setTimeout(loadClausePage, 250)
}
const cameFromTab = ref<'whitepaper' | 'topics' | ''>('')
async function seeBasis(t: Topic) {
  cameFromTab.value = 'topics'
  tab.value = 'library'
  await openDocument(t.document_id, t.clause_id)
}
async function openFromBriefing(documentId: string, clauseId = '') {
  cameFromTab.value = 'whitepaper'
  tab.value = 'library'
  await openDocument(documentId, clauseId)
}
onUnmounted(() => {
  clearTimeout(clauseSearchTimer)
  ++docRequest
  ++requestNo
})
// 链接直达某部法规的某一条：/knowledge?country=CN&doc=法规编号&clause=条文编号（如合规文件题目旁的依据）
const route = useRoute()
const router = useRouter()
const fromPath = computed(() => {
  const f = typeof route.query.from === 'string' ? route.query.from.trim() : ''
  return f.startsWith('/') && !f.startsWith('//') ? f : ''
})
const backLabel = computed(() => {
  if (cameFromTab.value === 'whitepaper') {
    return country.value && country.value !== 'CN' ? '← 返回国家合规简报' : ''
  }
  if (cameFromTab.value === 'topics') return '← 返回合规主题'
  if (!fromPath.value) return ''
  if (fromPath.value.startsWith('/admission')) return '← 返回海外属地准入'
  if (fromPath.value.startsWith('/documents') || fromPath.value.startsWith('/analysis')) return '← 返回合规评估'
  if (fromPath.value.startsWith('/notifications')) return '← 返回更新通知'
  return '← 返回上一页'
})
function goBack() {
  if (cameFromTab.value === 'whitepaper') {
    cameFromTab.value = ''
    if (country.value && country.value !== 'CN') tab.value = 'whitepaper'
    return
  }
  if (cameFromTab.value === 'topics') {
    cameFromTab.value = ''
    tab.value = 'topics'
    return
  }
  if (fromPath.value) router.push(fromPath.value)
}
watch(tab, (t) => {
  closeTermPopover()
  if (t !== 'library') cameFromTab.value = ''
  if (t === 'standards' && !country.value && standardScope.value === 'local') {
    standardScope.value = 'all'
  }
})
async function openFromLink() {
  const q = route.query
  const code = typeof q.country === 'string' ? q.country.toUpperCase() : ''
  const doc = typeof q.doc === 'string' ? q.doc : ''
  if (!code) return
  if (!doc) {  // 只带国家（如更新通知查看简报）：打开这个国家的合规简报；中国无海外简报则打开法规浏览
    tab.value = code === 'CN' ? 'library' : 'whitepaper'
    await chooseCountry(code)
    return
  }
  tab.value = 'library'
  await chooseCountry(code, { doc, clause: typeof q.clause === 'string' ? q.clause : '' })
}
watch(() => [route.query.country, route.query.doc, route.query.clause], openFromLink)
onMounted(async () => {
  try {
    const [ov, stdRes] = await Promise.all([
      api.get<Country[]>('/api/knowledge/overview'),
      api.get<{ items: StandardItem[] }>('/api/knowledge/standards?limit=500').catch(() => ({ items: [] })),
    ])
    countries.value = ov
    standards.value = stdRes.items || []
    // 后台静默预取各国法规摘要，用于筛选效力状态或搜索时在左侧折叠栏实时展示各国匹配份数
    for (const c of ov) {
      void api
        .get<Regulation[]>(`/api/knowledge/regulations?country_code=${c.country_code}`)
        .then((list) => {
          allCountryRegs[c.country_code] = list
          list.forEach((d) => registerDocTitle(d.id, d.title_zh || d.title))
        })
        .catch(() => {})
    }
    // 不替用户选国家：每次进来由用户自己选（标签共用这一个国家）；带了链接参数的直接打开那一条
  } catch (e) {
    ElMessage.error((e as Error).message)
  }
  await openFromLink()
})
</script>
<template>
  <div class="knowledge-page">
    <header class="page-heading" :class="{ compact: tab === 'library' && !!detail }">
      <div>
        <h1>法规知识库</h1>
        <p v-if="!(tab === 'library' && detail)">查阅各国数据合规简报、官方条文中文对照、法律术语及技术标准目录。</p>
      </div>
      <div v-if="country" class="kb-head-controls">
        <el-button data-testid="kb-back-countries" @click="exitToOverview">← 返回全部国家</el-button>
        <el-select
          v-model="country"
          filterable
          aria-label="选择国家"
          style="width: 180px"
          placeholder="切换国家"
          @change="chooseCountry"
          ><el-option
            v-for="c in countries"
            :key="c.country_code"
            :value="c.country_code"
            :label="c.country_name_zh"
        /></el-select>
      </div>
    </header>
    <el-tabs v-model="tab" :key="country ? (country === 'CN' ? 'kb-cn' : 'kb-country') : 'kb-overview'" class="kb-tabs">
      <el-tab-pane v-if="!country" :label="`国别法规（${countries.length}）`" name="countries">
        <section class="kb-pick" data-testid="kb-pick">
          <p class="kb-scope-note" data-testid="kb-scope-note">
            统计口径：默认聚焦企业日常检索的核心与常用现行法规（进入国别目录后可展开全部外围配套文件），条文按合并后的阅读条文展示。
          </p>
          <el-empty
            v-if="!countries.length"
            description="当前法规库尚未收录国家与法规条文"
            data-testid="kb-empty-countries"
          />
          <template v-else>
            <el-alert
              v-if="countries.every((c) => (c.documents_count ?? 0) === 0)"
              type="info"
              :closable="false"
              show-icon
              title="当前法规库尚未收录现行法规条文，待平台管理员在后台完成官方法规采集或上传后，即可在此检索条文与查阅国家合规简报。"
              style="margin-bottom: 12px"
              data-testid="kb-coldboot-notice"
            />
            <div class="country-grid" data-testid="kb-country-cards">
              <button v-for="c in countries" :key="c.country_code" type="button" class="country-tile" :data-testid="`kb-country-${c.country_code}`" @click="chooseCountry(c.country_code)">
                <div class="tile-head">
                  <strong>{{ c.country_name_zh }}</strong>
                  <span class="tile-badge">{{ c.country_code === 'CN' ? '出境源头法规' : '法规与合规简报' }}</span>
                </div>
                <span class="tile-count">{{ c.documents_count ? `${c.documents_count} 份核心常用法规 · 阅读条文` : '暂未收录现行法规条文' }}</span>
                <span class="tile-updated">法规最后更新 {{ localTime(c.last_updated_at, 'date') || '—' }}</span>
              </button>
            </div>
          </template>
        </section>
      </el-tab-pane>
      <el-tab-pane v-if="country && country !== 'CN'" label="国家合规简报" name="whitepaper" lazy
        ><CountryWhitepaper
          :country="country"
          :countries="countries.filter((c) => c.country_code !== 'CN')"
          @choose="chooseCountry"
          @open="openFromBriefing"
        />
      </el-tab-pane>
      <el-tab-pane v-if="country" label="法规浏览" name="library"
        ><div class="library-toolbar">
          <div class="knowledge-search in-tab"><el-input v-model="search" clearable :placeholder="SEARCH_HINT[tab]" :aria-label="SEARCH_HINT[tab]" @input="resetSearchPages" /></div>
          <el-radio-group v-model="statusFilter" size="small" class="cy-pill-group" aria-label="效力状态筛选" data-testid="kb-status-filter">
            <el-radio-button value="">全部效力</el-radio-button>
            <el-radio-button value="in_force">现行有效</el-radio-button>
            <el-radio-button value="amended">已修订</el-radio-button>
            <el-radio-button value="draft">草案</el-radio-button>
            <el-radio-button value="repealed">已废止</el-radio-button>
          </el-radio-group>
        </div><div class="library-layout" :class="{ 'dir-collapsed': dirCollapsed && !!detail, 'has-detail': !!detail }" v-loading="busy">
          <button v-if="dirCollapsed && detail" type="button" class="dir-rail" title="展开法规目录" aria-label="展开法规目录" data-testid="kb-dir-open" @click="dirCollapsed = false">
            <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M6 3l5 5-5 5" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" /></svg>
            <span>法规目录</span>
          </button>
          <div v-else class="dir-wrap">
          <button v-if="detail" type="button" class="cy-rail-toggle dir-close" title="收起法规目录" aria-label="收起法规目录" data-testid="kb-dir-close" @click="dirCollapsed = true">
            <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><path d="M10 3L5 8l5 5" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" /></svg>
          </button>
          <RegulationDirectory
            :countries="directoryCountries"
            :documents="filteredDocs"
            :hidden="peripheralCount"
            :show-peripheral="showPeripheral"
            :country="country"
            :selected-id="detail?.regulation.id"
            :loading="busy"
            @country="chooseCountry"
            @document="openDocument"
            @toggle-peripheral="showPeripheral = !showPeripheral"
          />
          </div>
          <div class="reading-panel" v-loading="detailBusy" @click="handleTermDelegation" @keydown="handleTermDelegation">
            <template v-if="detail">
              <div v-if="backLabel && !(country === 'CN' && cameFromTab === 'whitepaper')" class="kb-back-row">
                <el-button link type="primary" data-testid="kb-back" @click="goBack">{{ backLabel }}</el-button>
              </div>
              <header class="document-heading">
                <div>
                  <div class="eyebrow">
                    {{ detail.regulation.document_type_zh }} ·
                    {{ detail.regulation.publisher }}
                  </div>
                  <h2>{{ detail.regulation.title_zh || detail.regulation.title }}</h2>
                  <p v-if="detail.regulation.title_zh && detail.regulation.title_zh !== detail.regulation.title" class="original-title" dir="auto">
                    {{ detail.regulation.title }}
                  </p>
                  <p>
                    <template v-if="knownStatus(detail.regulation.legal_status)"
                      >{{ legalStatus[detail.regulation.legal_status] }} · </template
                    >{{ detail.regulation.clauses_count }} 条阅读条文
                  </p>
                  <p v-if="detail.versions.length > 1" class="version-line" data-testid="kb-versions">
                    修订沿革：<span v-for="v in detail.versions" :key="v.version_no">{{ officialTextLabel(v) }}<template v-if="knownStatus(v.legal_status)">（{{ legalStatus[v.legal_status] }}）</template></span>
                  </p>
                </div>
                <a
                  :href="detail.regulation.source_url"
                  target="_blank"
                  rel="noopener noreferrer"
                  class="source-link"
                  >官方原文 ↗</a
                >
              </header>
              <el-alert
                v-if="detail.regulation.legal_status === 'draft' || detail.regulation.legal_status === 'repealed'"
                type="warning"
                :closable="false"
                show-icon
                class="nonforce-banner"
                data-testid="kb-nonforce-banner"
                :title="detail.regulation.legal_status === 'draft' ? '立法草案提示：本文件尚处于征求意见或立法审议阶段，仅供合规前瞻研判参考，不作为现行强制合规依据。' : '已废止文本提示：本文件已被后续立法替代或废止，仅供历史沿革对照查阅，不作为现行合规依据。'"
              />
              <el-radio-group v-model="readingView" class="reading-view cy-pill-group" aria-label="法规阅读方式">
                <el-radio-button value="clauses">条款阅读</el-radio-button>
                <el-radio-button
                  value="relations"
                  :disabled="!detail.regulation.relations_count"
                  :title="!detail.regulation.relations_count ? '该法规暂未梳理出条文关联关系，请直接阅读条文' : undefined"
                  >关系解读<template v-if="detail.regulation.relations_count">
                    {{ detail.regulation.relations_count }}</template
                  ></el-radio-button
                >
                <el-radio-button
                  v-if="detail.versions.length > 1"
                  value="compare"
                  >版本对照（{{ detail.versions.length }} 版）</el-radio-button
                >
              </el-radio-group>
              <RegulationRelations
                v-if="readingView === 'relations'"
                :key="`${detail.regulation.id}:${detail.version_id}`"
                :document-id="detail.regulation.id"
                :version-id="detail.version_id"
                @basis="readRelationBasis"
              />
              <VersionCompare
                v-else-if="readingView === 'compare' && detail.versions.length > 1"
                :key="`cmp:${detail.regulation.id}`"
                :document-id="detail.regulation.id"
              />
              <template v-else>
                <p v-if="!detail.regulation.relations_count" class="muted relation-none">
                  这份法规还没有整理出可展示的关系，可直接阅读条文。
                </p>
                <div class="reading-tools">
                  <el-input
                    v-model="clauseSearch"
                    clearable
                    placeholder="查找条号或正文"
                    aria-label="查找条款"
                    @input="searchClauses"
                  />
                </div>
                <div v-if="!originalIsZh" class="cols-head" data-testid="kb-cols-head"><span>原文</span><span>中文对照（参考译文，以官方原文为准）</span></div>
                <article
                  v-for="c in pageClauses"
                  :key="c.clause_id"
                  :id="`clause-${c.clause_id}`"
                  class="clause"
                  :class="{ selected: selectedClause === c.clause_id }"
                >
                  <h3>{{ clauseTitle(c) }}<el-tag v-if="c.scope_note" size="small" type="info" effect="plain" class="scope-tag" data-testid="clause-scope-note">{{ c.scope_note }}</el-tag></h3>
                  <div v-if="renderedClauseMap.get(c.clause_id)?.colsOnly" class="parallel-scroll" :class="{ open: expanded.has(c.clause_id) }" data-testid="kb-columns">
                    <div class="ps-col" dir="auto"><span class="legal-text"><template v-for="(seg, si) in renderedClauseMap.get(c.clause_id)?.origSegs || []" :key="si"><span v-if="seg.termKey" class="term term-mark" role="button" tabindex="0" :data-term-key="seg.termKey">{{ seg.text }}</span><template v-else>{{ seg.text }}</template></template></span></div>
                    <div class="ps-col ps-zh"><span class="legal-text"><template v-for="(seg, si) in renderedClauseMap.get(c.clause_id)?.zhSegs || []" :key="si"><span v-if="seg.termKey" class="term term-mark" role="button" tabindex="0" :data-term-key="seg.termKey">{{ seg.text }}</span><template v-else>{{ seg.text }}</template></template></span></div>
                  </div>
                  <div
                    v-else
                    class="parallel-rows"
                    :class="{ folded: c.original_text.length > LONG && !expanded.has(c.clause_id), single: originalIsZh }"
                    data-testid="kb-aligned"
                  >
                    <template v-for="(row, k) in renderedClauseMap.get(c.clause_id)?.rows || []" :key="k">
                      <div class="p-orig" dir="auto"><span class="legal-text"><template v-for="(seg, si) in row.orig" :key="si"><span v-if="seg.termKey" class="term term-mark" role="button" tabindex="0" :data-term-key="seg.termKey">{{ seg.text }}</span><template v-else>{{ seg.text }}</template></template></span></div>
                      <div v-if="!originalIsZh" class="p-zh"><span v-if="row.hasZh" class="legal-text"><template v-for="(seg, si) in row.zh" :key="si"><span v-if="seg.termKey" class="term term-mark" role="button" tabindex="0" :data-term-key="seg.termKey">{{ seg.text }}</span><template v-else>{{ seg.text }}</template></template></span><span v-else class="muted">{{ c.scope_note ? '未翻译' : '暂无中文对照' }}</span></div>
                    </template>
                  </div>
                  <el-button
                    v-if="c.original_text.length > LONG || renderedClauseMap.get(c.clause_id)?.colsOnly"
                    link
                    type="primary"
                    class="fold-toggle"
                    @click="toggleClause(c.clause_id)"
                    >{{ expanded.has(c.clause_id) ? '收起' : '展开全文' }}</el-button
                  >
                  <div v-if="citedCount(c)" class="cited-by" data-testid="clause-cited-by">
                    <h4>被本企业合规文件引用（{{ citedCount(c) }} 处）</h4>
                    <section v-for="g in citedGroups(c)" :key="g.key" class="cited-group" data-testid="cited-group">
                      <h5>{{ g.title }}<small>{{ g.lines.length }} 处</small></h5>
                      <ul>
                        <li v-for="line in citedOpen.has(g.key) ? g.lines : g.lines.slice(0, CITED_SHOWN)" :key="line.link + line.text">
                          <router-link :to="line.link">{{ line.text }}</router-link>
                        </li>
                      </ul>
                      <el-button v-if="g.lines.length > CITED_SHOWN" link type="primary" size="small" data-testid="cited-more" @click="toggleCited(g.key)">
                        {{ citedOpen.has(g.key) ? '收起' : `展开其余 ${g.lines.length - CITED_SHOWN} 处` }}</el-button>
                    </section>
                  </div>
                </article>
                <el-empty v-if="!pageClauses.length" description="没有匹配的条款" /><el-pagination
                  v-if="detail.clauses_total > 12"
                  :current-page="page"
                  @update:current-page="turnClausePage"
                  :page-size="12"
                  :pager-count="5"
                  :total="detail.clauses_total"
                  layout="prev, pager, next, total"
                />
              </template> </template
            ><el-result
              v-else-if="detailError && !detailBusy"
              icon="warning"
              :title="detailError"
              sub-title="可以从左侧目录选择其他法规"
            /><el-empty
              v-else-if="!detailBusy && isFilteringRegs && !filteredDocs.length"
              description="当前筛选条件下暂无对应法规条文"
              data-testid="kb-filter-empty"
            >
              <el-button size="small" @click="statusFilter = ''; search = ''">恢复全部法规</el-button>
            </el-empty><el-empty
              v-else-if="!detailBusy"
              :description="isFilteringRegs ? `当前筛选条件下共有 ${filteredDocs.length} 份法规，请从左侧目录选择阅读` : '从左侧目录选择一份法规开始阅读'"
            />
          </div></div
      ></el-tab-pane>

      <el-tab-pane v-if="country" label="合规主题" name="topics"
        ><div class="knowledge-search in-tab"><el-input v-model="search" clearable :placeholder="SEARCH_HINT[tab]" :aria-label="SEARCH_HINT[tab]" @input="resetSearchPages" /></div><div class="topic-filters">
          <el-radio-group v-model="topic" class="cy-pill-group" @change="topicPage = 1"
            ><el-radio-button value="">主题总览</el-radio-button
            ><el-radio-button v-for="[key, label] in topicOptions" :key="key" :value="key">{{
              label
            }}</el-radio-button></el-radio-group
          ><span class="muted">{{ filteredTopics.length }} 项要求</span>
        </div>
        <div v-if="!topic && !search" v-loading="busy" class="topic-overview">
          <button
            v-for="g in topicGroups"
            :key="g.key"
            class="topic-overview-card"
            @click="chooseTopic(g.key)"
          >
            <h2>{{ g.label }}</h2>
            <p>{{ g.count }} 项要求 · 涉及 {{ g.documents }} 份法规</p>
            <span>查看各项要求</span>
          </button>
        </div>
        <div v-else class="topic-grid" v-loading="busy">
          <article
            v-for="t in filteredTopics.slice((topicPage - 1) * 18, topicPage * 18)"
            :key="t.id"
            class="topic-card"
          >
            <div class="eyebrow">{{ t.topic_label }}</div>
            <h2><template v-for="(seg, si) in highlightSegments(t.title)" :key="si"><mark v-if="seg.hit" class="kb-hit">{{ seg.text }}</mark><template v-else>{{ seg.text }}</template></template></h2>
            <dl v-if="t.facts?.length" class="topic-facts">
              <template v-for="f in t.facts" :key="f.label"><dt>{{ f.label }}</dt><dd>{{ f.text }}</dd></template>
            </dl>
            <blockquote v-else-if="t.quote" dir="auto">{{ t.quote }}</blockquote>
            <p class="topic-document">
              依据：{{ t.source || `${t.document_title} · ${articleLabel(clauseLabel(t.clause_number))}` }}
            </p>
            <el-button link type="primary" @click="seeBasis(t)">阅读条款与中文对照</el-button>
          </article>
        </div>
        <el-empty
          v-if="!busy && !filteredTopics.length"
          description="没有找到匹配的要求，可以换个说法，或在法规浏览里查阅原文"
        /><el-pagination
          v-if="(topic || search) && filteredTopics.length > 18"
          v-model:current-page="topicPage"
          :page-size="18"
          :pager-count="5"
          :total="filteredTopics.length"
          layout="prev, pager, next, total"
        />
      </el-tab-pane>
      <el-tab-pane v-if="country" label="法律术语" name="terms"
        ><div class="knowledge-search in-tab"><el-input v-model="search" clearable :placeholder="SEARCH_HINT[tab]" :aria-label="SEARCH_HINT[tab]" @input="resetSearchPages" /></div><div class="term-grid" v-loading="busy">
          <article
            v-for="t in filteredTerms.slice((termPage - 1) * 24, termPage * 24)"
            :key="t.id"
            class="term-card"
          >
            <div class="eyebrow">{{ t.source_label }}</div>
            <h2><template v-for="(seg, si) in highlightSegments(t.target_zh)" :key="si"><mark v-if="seg.hit" class="kb-hit">{{ seg.text }}</mark><template v-else>{{ seg.text }}</template></template></h2>
            <p class="original-term" dir="auto">{{ t.source_term }}</p>
            <p v-if="t.definition_zh">{{ dedupeParagraphLines(t.definition_zh) }}</p>
            <details v-if="termHasDistinctOriginal(t)">
              <summary>查看原文定义</summary>
              <p dir="auto">{{ dedupeParagraphLines(t.definition_original) }}</p>
            </details>
            <div class="term-source">
              {{ cleanTermReference(t.source_reference, '', t.document_id)
              }}<a
                v-if="t.source_url"
                :href="t.source_url"
                target="_blank"
                rel="noopener noreferrer"
              >
                官方来源 ↗</a
              >
            </div>
            <details v-if="t.others.length" class="term-others">
              <summary>其他 {{ t.others.length }} 份法规中的定义</summary>
              <section v-for="o in t.others" :key="o.id">
                <p v-if="o.definition_zh">{{ dedupeParagraphLines(o.definition_zh) }}</p>
                <p v-else dir="auto">{{ dedupeParagraphLines(o.definition_original) }}</p>
                <div class="term-source">{{ cleanTermReference(o.source_reference, '', o.document_id) }}</div>
              </section>
            </details>
          </article>
        </div>
        <el-empty
          v-if="!busy && !filteredTerms.length"
          description="没有匹配的术语" /><el-pagination
          v-if="filteredTerms.length > 24"
          v-model:current-page="termPage"
          :page-size="24"
          :pager-count="5"
          :total="filteredTerms.length"
          layout="prev, pager, next, total"
      /></el-tab-pane>
      <el-tab-pane name="standards">
        <template #label><span data-testid="kb-direct-standards">{{ country ? '标准目录' : `标准目录（${standards.length}）` }}</span></template>
        <div class="library-toolbar">
          <div class="knowledge-search in-tab">
            <el-input
              v-model="search"
              clearable
              :placeholder="SEARCH_HINT.standards"
              :aria-label="SEARCH_HINT.standards"
              @input="resetSearchPages"
            />
          </div>
          <div class="standard-filter-bar">
            <el-radio-group
              v-if="!country && standards.length > 0"
              v-model="standardScope"
              size="small"
              class="cy-pill-group"
              aria-label="标准范围筛选"
              @change="standardPage = 1"
            >
              <el-radio-button value="all">全部已收录标准（{{ standards.length }}）</el-radio-button>
              <el-radio-button value="intl">国际通用标准（{{ intlStandardsCount }}）</el-radio-button>
              <el-radio-button value="domestic">中国标准（{{ cnStandardsCount }}）</el-radio-button>
            </el-radio-group>
            <el-radio-group
              v-else-if="country && standards.length > 0"
              v-model="standardScope"
              size="small"
              class="cy-pill-group"
              aria-label="标准范围筛选"
              @change="standardPage = 1"
            >
              <el-radio-button value="local">本国及国际通用（{{ localStandardsCount }}）</el-radio-button>
              <el-radio-button v-if="domesticStandardsCount > 0 && domesticStandardsCount < localStandardsCount" value="domestic">仅本国标准（{{ domesticStandardsCount }}）</el-radio-button>
              <el-radio-button v-if="domesticStandardsCount > 0 && intlStandardsCount > 0" value="intl">国际通用标准（{{ intlStandardsCount }}）</el-radio-button>
              <el-radio-button v-if="localStandardsCount < standards.length" value="all">全部已收录标准（{{ standards.length }}）</el-radio-button>
            </el-radio-group>
            <el-radio-group
              v-model="standardKind"
              size="small"
              class="cy-pill-group"
              aria-label="标准性质筛选"
              @change="standardPage = 1"
            >
              <el-radio-button value="">全部性质</el-radio-button>
              <el-radio-button value="mandatory">强制性</el-radio-button>
              <el-radio-button value="recommended">推荐性</el-radio-button>
              <el-radio-button v-if="hasGuidanceStandards" value="guidance">指导性</el-radio-button>
            </el-radio-group>
            <span class="muted">共 {{ filteredStandards.length }} 项标准</span>
          </div>
        </div>
        <p v-if="country && domesticStandardsCount === 0 && standards.length > 0" class="muted" style="margin: 0 0 14px">
          当前所选目的国暂无独立本国标准收录，默认展示可跨国适用的 {{ intlStandardsCount }} 项国际通用标准（ISO、UN、IEC 等）；如需对照车辆研发出境源头配套技术要求，可切换至全部已收录标准一并查阅中国标准（共 {{ standards.length }} 项）。
        </p>
        <div class="term-grid" data-testid="kb-standards-grid">
          <article
            v-for="s in filteredStandards.slice((standardPage - 1) * 24, standardPage * 24)"
            :key="s.id"
            class="term-card std-card"
            role="button"
            tabindex="0"
            data-testid="kb-standard-card"
            @click="openStandardDetail(s)"
            @keydown.enter="openStandardDetail(s)"
          >
            <div class="eyebrow">
              {{ stdCountryLabel(s) }} · {{ s.kind }} · {{ s.status }}
            </div>
            <h2>
              <template v-for="(seg, si) in highlightSegments(s.title)" :key="si">
                <mark v-if="seg.hit" class="kb-hit">{{ seg.text }}</mark>
                <template v-else>{{ seg.text }}</template>
              </template>
            </h2>
            <p class="original-term">
              <template v-for="(seg, si) in highlightSegments(s.number)" :key="si">
                <mark v-if="seg.hit" class="kb-hit">{{ seg.text }}</mark>
                <template v-else>{{ seg.text }}</template>
              </template>
            </p>
            <dl class="topic-facts">
              <template v-if="s.publisher">
                <dt>发布机构</dt>
                <dd>{{ s.publisher }}</dd>
              </template>
              <template v-if="s.published_on">
                <dt>发布日期</dt>
                <dd>{{ s.published_on }}</dd>
              </template>
              <template v-if="s.effective_on">
                <dt>实施日期</dt>
                <dd>{{ s.effective_on }}</dd>
              </template>
            </dl>
            <div class="term-source std-card-foot">
              <span v-if="s.topics?.length" class="std-topics">
                <span v-for="tp in s.topics" :key="tp" class="std-topic-tag">{{ tp }}</span>
              </span>
              <span v-else class="std-view-hint">查看标准详情 →</span>
              <a
                v-if="s.detail_url"
                :href="s.detail_url"
                target="_blank"
                rel="noopener noreferrer"
                class="std-source-link"
                @click.stop
              >官方来源 ↗</a>
            </div>
          </article>
        </div>
        <el-empty v-if="!filteredStandards.length" description="没有匹配的标准" />
        <el-pagination
          v-if="filteredStandards.length > 24"
          v-model:current-page="standardPage"
          :page-size="24"
          :pager-count="5"
          :total="filteredStandards.length"
          layout="prev, pager, next, total"
        />
      </el-tab-pane>
    </el-tabs>

    <el-drawer
      v-model="stdDrawerOpen"
      :title="activeStandard?.number || '技术标准详情'"
      size="min(560px, 92vw)"
      :close-on-press-escape="true"
      append-to-body
      data-testid="kb-standard-detail"
    >
      <div v-if="activeStandard" class="std-detail">
        <div class="std-detail-badges">
          <el-tag size="small" type="info" effect="plain">{{ stdCountryLabel(activeStandard) }}</el-tag>
          <el-tag size="small" :type="activeStandard.kind_code === 'mandatory' ? 'warning' : 'info'" effect="plain">{{ activeStandard.kind }}标准</el-tag>
          <el-tag size="small" :type="activeStandard.status_code === 'withdrawn' ? 'danger' : 'success'">{{ activeStandard.status }}</el-tag>
        </div>
        <h3 class="std-detail-title">{{ activeStandard.title }}</h3>
        <dl class="std-detail-dl">
          <dt>标准编号</dt>
          <dd><strong>{{ activeStandard.number }}</strong></dd>
          <dt>标准名称</dt>
          <dd>{{ activeStandard.title }}</dd>
          <dt>归口类别</dt>
          <dd>{{ stdCountryLabel(activeStandard) }} · {{ activeStandard.kind }}标准<template v-if="activeStandard.publisher">（发布/归口：{{ activeStandard.publisher }}）</template></dd>
          <template v-if="activeStandard.published_on">
            <dt>发布日期</dt>
            <dd>{{ activeStandard.published_on }}</dd>
          </template>
          <template v-if="activeStandard.effective_on">
            <dt>实施日期</dt>
            <dd>{{ activeStandard.effective_on }}</dd>
          </template>
          <dt>适用范围</dt>
          <dd>
            <div v-if="activeStandard.topics?.length" class="std-topics" style="margin-bottom: 6px">
              <span v-for="tp in activeStandard.topics" :key="tp" class="std-topic-tag">{{ tp }}</span>
            </div>
            <div>{{ stdScopeSummary(activeStandard) }}</div>
          </dd>
          <dt>状态说明</dt>
          <dd>{{ stdStatusExplain(activeStandard) }}</dd>
        </dl>
        <div v-if="activeStandard.detail_url" class="std-detail-foot">
          <a :href="activeStandard.detail_url" target="_blank" rel="noopener noreferrer" class="std-detail-link">前往官方目录查阅标准著录原文 ↗</a>
        </div>
      </div>
    </el-drawer>
  </div>
</template>
<style scoped>
.knowledge-page {
  max-width: 1700px;
  margin: auto;
}
.knowledge-search {
  max-width: 650px;
  margin: 0 0 20px;
}
.library-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
  margin-bottom: 16px;
}
.library-toolbar .knowledge-search {
  flex: 1 1 320px;
  margin: 0;
}
.nonforce-banner {
  margin-bottom: 14px;
}
.kb-hit {
  background: #fef08a;
  color: inherit;
  padding: 0 2px;
  border-radius: 3px;
}
.knowledge-search.in-tab {
  margin: 0 0 16px;
}
.library-layout {
  display: grid;
  grid-template-columns: 280px minmax(0, 1fr);
  gap: 24px;
  align-items: start;
}
.library-layout.dir-collapsed {
  grid-template-columns: 48px minmax(0, 1fr);
}
/* 二轮：目录固定在左侧，内部自己滚动，层级高于右侧搜索条和画布 */
.dir-wrap {
  position: sticky;
  top: 80px;
  z-index: 12;
  min-width: 0;
  max-height: calc(100vh - 104px);
  overflow-y: auto;
  border-radius: 12px;
  background: #ffffff;
}
.page-heading.compact {
  margin-bottom: 12px;
}
.page-heading.compact h1 {
  font-size: 18px;
  margin: 0;
}
.kb-head-controls {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}
.dir-close {
  position: absolute;
  top: 14px;
  right: 14px;
  z-index: 2;
  width: 28px;
  height: 28px;
}
.dir-rail {
  position: sticky;
  top: 80px;
  z-index: 12;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 10px;
  width: 48px;
  padding: 14px 0;
  border: 1px solid #e3eaf1;
  border-radius: 12px;
  background: white;
  color: var(--cy-text-2);
  cursor: pointer;
}
.dir-rail svg {
  width: 16px;
  height: 16px;
}
.dir-rail span {
  writing-mode: vertical-rl;
  letter-spacing: 0.2em;
  font-size: 14px;
}
.dir-rail:hover {
  color: var(--el-color-primary);
}
.reading-panel {
  background: white;
  border: 1px solid #e3eaf1;
  border-radius: 14px;
  padding: 28px;
  min-height: 480px;
  min-width: 0;
}
.kb-back-row {
  margin: -8px 0 8px;
}
.document-heading {
  display: flex;
  gap: 20px;
  align-items: start;
  justify-content: space-between;
  border-bottom: 1px solid #e8edf3;
  padding-bottom: 20px;
}
.eyebrow {
  font-size: 13px;
  letter-spacing: 0.02em;
  color: var(--cy-text-2);
}
.document-heading h2 {
  font-size: 18px;
  line-height: 1.5;
  margin: 8px 0 12px;
}
.document-heading p,
.muted {
  font-size: 13px;
  color: var(--cy-text-2);
}
.original-title {
  margin: -6px 0 10px;
  color: #64748b;
  font-size: 13px;
}
.relation-none {
  margin: 12px 0 0;
}
.parallel-rows {
  display: grid;
  grid-template-columns: 1fr 1fr;
  column-gap: 28px;
  font-size: 14px;
}
.cols-head {
  position: sticky;
  top: 64px;
  z-index: 4;
  display: grid;
  grid-template-columns: 1fr 1fr;
  column-gap: 28px;
  padding: 10px 0;
  margin-bottom: 4px;
  background: #fff;
  border-bottom: 1px solid #e8edf3;
  font-size: 13px;
  color: var(--cy-text-2);
}
.cols-head span + span {
  padding-left: 28px;
}
.parallel-rows.single {
  grid-template-columns: 1fr;
}
.parallel-rows > h4 {
  font-size: 13px;
  font-weight: 600;
  color: var(--cy-text-2);
  margin: 0 0 10px;
}
.parallel-rows > .p-orig,
.parallel-rows > .p-zh {
  min-width: 0;
  padding: 8px 0;
  border-top: 1px solid #eef2f6;
  line-height: 1.8;
}
.parallel-rows > .p-zh {
  border-left: 1px solid #e8edf3;
  padding-left: 28px;
}
.parallel-rows > .p-orig:dir(rtl) {
  text-align: right;
}
.parallel-rows.folded,
.parallel.folded {
  max-height: 520px;
  overflow: hidden;
  -webkit-mask-image: linear-gradient(#000 80%, transparent);
  mask-image: linear-gradient(#000 80%, transparent);
}
.fold-toggle {
  display: flex;
  width: fit-content;
  margin: 8px auto 0;
}
.source-link {
  flex-shrink: 0;
  font-size: 14px;
  color: #245c9a;
}
.reading-tools {
  display: flex;
  gap: 12px;
  margin: 22px 0;
}
.reading-view {
  margin-top: 22px;
}
.reading-tools .el-input {
  max-width: 410px;
}
.kb-pick { margin: 12px 0 24px; }
.country-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 16px; }
.country-tile { display: flex; flex-direction: column; gap: 8px; align-items: flex-start; padding: 20px 24px; border: 1px solid var(--cy-line); border-radius: 12px; background: #fff; font: inherit; text-align: left; cursor: pointer; transition: border-color 0.16s, box-shadow 0.16s; }
.tile-head { display: flex; align-items: center; justify-content: space-between; gap: 10px; width: 100%; }
.country-tile strong { font-size: 18px; color: var(--cy-text-1); }
.tile-badge { font-size: 13px; color: #475569; background: #f1f5f9; border-radius: 6px; padding: 2px 8px; white-space: nowrap; }
.country-tile .tile-count { font-size: 14px; color: var(--cy-text-2); }
.country-tile .tile-updated { font-size: 13px; color: var(--cy-text-3, #64748b); }
.country-tile:hover { border-color: var(--el-color-primary-light-5); box-shadow: 0 6px 18px rgba(15, 23, 42, 0.04); }
@media (max-width: 1100px) { .country-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
@media (max-width: 700px) { .country-grid { grid-template-columns: 1fr; } }
.kb-pick h2 { font-size: 18px; margin: 0 0 16px; color: #0f172a; }
.kb-pick-select { width: 100%; }
.kb-pick p { color: #64748b; font-size: 13px; margin: 14px 0 0; }
.kb-pick p.kb-scope-note { margin: 0 0 12px; color: var(--cy-text-2); font-size: 13px; line-height: 1.6; }
.version-line { font-size: 13px; color: #64748b; }
.version-line span + span::before { content: '；'; }
.versions {
  padding: 14px 18px;
  background: #f5f8fc;
  font-size: 13px;
  line-height: 2;
}
.clause {
  scroll-margin-top: 88px;
  padding: 24px 0;
  border-top: 1px solid #ecf0f4;
}
.clause.selected {
  background: #f1f7ff;
  box-shadow: 0 0 0 12px #f1f7ff;
}
.clause h3 {
  scroll-margin-top: 24px;
  font-size: 16px;
  line-height: 1.6;
  margin: 0 0 18px;
}
.legal-text {
  white-space: pre-wrap;
  line-height: 1.9;
  overflow-wrap: anywhere;
}
.term-mark {
  font: inherit;
  color: #215c9b;
  background: transparent;
  border: 0;
  border-bottom: 1px dotted #88a9c8;
  padding: 0;
  cursor: pointer;
  text-align: inherit;
}
.term-mark:hover {
  color: #1d4ed8;
  border-bottom-color: #2563eb;
  background: #eff6ff;
}
.term-mark:focus-visible {
  outline: 2px solid #2563eb;
  outline-offset: 2px;
}
.parallel {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 28px;
  font-size: 14px;
}
.parallel.single {
  grid-template-columns: 1fr;
}
.parallel section {
  min-width: 0;
}
.parallel section + section {
  border-left: 1px solid #e8edf3;
  padding-left: 28px;
}
.parallel h4 {
  font-size: 13px;
  font-weight: 600;
  color: var(--cy-text-2);
  margin: 0 0 10px;
}
.standard-filter-bar {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}
.topic-overview {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(235px, 1fr));
  gap: 20px;
  margin: 16px 0 30px;
}
.topic-overview-card {
  font: inherit;
  text-align: left;
  border: 1px solid #e3eaf1;
  border-radius: 14px;
  background: white;
  padding: 28px;
  cursor: pointer;
  transition: box-shadow 0.2s;
}
.topic-overview-card:hover {
  box-shadow: 0 8px 24px #263c4b0d;
}
.topic-overview-card h2 {
  font-size: 18px;
  margin: 0 0 16px;
  color: #263c4b;
}
.topic-overview-card p {
  font-size: 13px;
  color: #77869a;
}
.topic-overview-card span {
  display: block;
  color: #245c9a;
  font-size: 14px;
  margin-top: 24px;
}
.topic-filters {
  display: flex;
  justify-content: space-between;
  gap: 16px;
  align-items: center;
  flex-wrap: wrap;
  margin: 8px 0 24px;
}
.topic-grid,
.term-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 20px;
  margin-bottom: 24px;
}
.topic-card,
.term-card {
  background: white;
  border: 1px solid #e3eaf1;
  border-radius: 14px;
  padding: 24px;
  min-width: 0;
  transition:
    box-shadow 0.2s,
    transform 0.2s;
}
.topic-facts { display: grid; grid-template-columns: 5em 1fr; gap: 3px 8px; margin: 6px 0; font-size: 13px; line-height: 1.65; }
.topic-facts dt { color: #64748b; }
.topic-facts dd { margin: 0; color: #1e293b; overflow-wrap: anywhere; }
.topic-card:hover {
  transform: translateY(-2px);
  box-shadow: 0 8px 24px #263c4b0a;
}
.topic-card h2,
.term-card h2 {
  font-size: 16px;
  line-height: 1.6;
  margin: 10px 0;
}
.topic-document {
  font-size: 13px;
  color: #74869a;
  line-height: 1.6;
}
.topic-card blockquote {
  margin: 18px 0;
  padding-left: 14px;
  border-left: 2px solid #d8e5f2;
  font-size: 14px;
  line-height: 1.8;
  max-height: 145px;
  overflow: auto;
}
.term-card p,
.term-card details {
  font-size: 14px;
  line-height: 1.8;
  overflow-wrap: anywhere;
}
.original-term {
  color: #617990;
}
.term-source {
  margin-top: 18px;
  font-size: 13px;
  color: #78899b;
  line-height: 1.8;
}
.term-others {
  margin-top: 14px;
  border-top: 1px dashed #e3eaf1;
  padding-top: 10px;
}
.term-others section {
  margin-top: 10px;
}
.term-source a {
  color: #245c9a;
}
.std-card-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  flex-wrap: wrap;
}
.std-topics {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
}
.std-topic-tag {
  font-size: 13px;
  color: #475569;
  background: #f1f5f9;
  border-radius: 4px;
  padding: 1px 8px;
  line-height: 1.6;
}
.std-source-link {
  margin-left: auto;
  white-space: nowrap;
}
.knowledge-page small {
  font-size: 13px;
}
summary {
  cursor: pointer;
}
.el-pagination {
  margin-top: 24px;
  justify-content: center;
  flex-wrap: wrap;
  gap: 6px 2px;
  max-width: 100%;
}
@media (max-width: 768px) {
  .knowledge-page :deep(.el-pagination) {
    flex-wrap: wrap;
    justify-content: center;
    gap: 6px 2px;
    max-width: 100%;
  }
  .knowledge-page :deep(.el-pagination button),
  .knowledge-page :deep(.el-pagination .el-pager li) {
    min-width: 26px !important;
    height: 28px !important;
    line-height: 28px !important;
    padding: 0 4px !important;
    margin: 0 1px !important;
    font-size: 13px !important;
  }
  .knowledge-page :deep(.el-pagination__total) {
    font-size: 13px !important;
    margin-left: 4px !important;
  }
}
@media (max-width: 1100px) {
  .topic-grid,
  .term-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
  .library-layout {
    grid-template-columns: 220px minmax(0, 1fr);
    gap: 16px;
  }
  .library-layout.dir-collapsed {
    grid-template-columns: 48px minmax(0, 1fr);
  }
  .parallel {
    grid-template-columns: 1fr;
  }
  .parallel section + section {
    border: 0;
    padding: 16px 0 0;
  }
}
.kb-tabs :deep(.el-tabs__active-bar) {
  display: none !important;
}
.kb-tabs :deep(.el-tabs__item) {
  position: relative;
  padding: 0 2px !important;
  margin-right: 28px;
}
.kb-tabs :deep(.el-tabs__item:last-child) {
  margin-right: 0;
}
.kb-tabs :deep(.el-tabs__item.is-active::after) {
  content: '';
  position: absolute;
  left: 0;
  right: 0;
  bottom: 0;
  height: 3px;
  border-radius: 3px;
  background: var(--el-color-primary);
}
@media (max-width: 768px) {
  .kb-tabs :deep(.el-tabs__header) {
    margin-bottom: 16px;
  }
  .kb-tabs :deep(.el-tabs__nav-prev),
  .kb-tabs :deep(.el-tabs__nav-next) {
    display: none !important;
  }
  .kb-tabs :deep(.el-tabs__nav-wrap),
  .kb-tabs :deep(.el-tabs__nav-wrap.is-scrollable) {
    padding: 0 !important;
  }
  .kb-tabs :deep(.el-tabs__nav-scroll) {
    overflow: visible !important;
  }
  .kb-tabs :deep(.el-tabs__nav) {
    display: flex !important;
    flex-wrap: wrap !important;
    justify-content: space-between;
    gap: 4px 10px;
    width: 100% !important;
    float: none !important;
    transform: none !important;
  }
  .kb-tabs :deep(.el-tabs__item) {
    margin-right: 0 !important;
    padding: 0 2px !important;
    height: 38px;
    line-height: 38px;
    font-size: 13px;
    flex: 0 0 auto;
  }
}
.std-card {
  cursor: pointer;
}
.std-card:hover,
.std-card:focus-visible {
  border-color: var(--el-color-primary-light-5);
  box-shadow: 0 8px 24px rgba(15, 23, 42, 0.06);
  outline: none;
}
.std-view-hint {
  font-size: 13px;
  color: var(--el-color-primary);
}
.std-detail {
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.std-detail-badges {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.std-detail-title {
  margin: 0;
  font-size: 18px;
  line-height: 1.5;
  color: #0f172a;
}
.std-detail-dl {
  display: grid;
  grid-template-columns: 5.5em minmax(0, 1fr);
  gap: 10px 14px;
  margin: 4px 0 0;
  padding: 14px 16px;
  background: #f8fafc;
  border: 1px solid #e2e8f0;
  border-radius: 10px;
  font-size: 14px;
  line-height: 1.7;
}
.std-detail-dl dt {
  color: #64748b;
  font-weight: 600;
}
.std-detail-dl dd {
  margin: 0;
  color: #1e293b;
  overflow-wrap: anywhere;
}
.std-detail-foot {
  margin-top: 4px;
}
.std-detail-link {
  font-size: 14px;
  color: var(--el-color-primary);
  font-weight: 600;
}
.std-detail-link:hover {
  text-decoration: underline;
}
@media (max-width: 900px) {
  .library-layout,
  .library-layout.has-detail,
  .library-layout.dir-collapsed {
    grid-template-columns: 1fr;
  }
  .dir-wrap {
    position: static;
    max-height: 320px;
  }
  .dir-rail {
    position: static;
    flex-direction: row;
    justify-content: center;
    width: 100%;
    padding: 10px 14px;
  }
  .dir-rail span {
    writing-mode: horizontal-tb;
    letter-spacing: 0.05em;
  }
}
@media (max-width: 700px) {
  .topic-grid,
  .term-grid {
    grid-template-columns: 1fr;
  }
  .reading-panel {
    padding: 20px;
  }
  .document-heading {
    flex-direction: column;
  }
  .parallel-rows,
  .parallel-scroll,
  .cols-head {
    grid-template-columns: 1fr;
  }
  .parallel-rows > .p-zh,
  .parallel-scroll .ps-zh {
    border-left: 0;
    padding-left: 0;
  }
  .cols-head span + span {
    display: none;
  }
}
@media (prefers-reduced-motion: reduce) {
  .topic-card,
  .term-card {
    transition: none;
  }
  .topic-card:hover {
    transform: none;
  }
}
.cited-by { margin-top: 10px; padding: 8px 12px; border-left: 3px solid var(--el-color-primary-light-5); background: var(--el-fill-color-lighter); }
.cited-by h4 { margin: 0 0 4px; font-size: 13px; font-weight: 600; }
.cited-by ul { margin: 0; padding-left: 18px; font-size: 13px; line-height: 1.8; }
.cited-group + .cited-group { margin-top: 6px; }
.cited-group h5 { margin: 4px 0 0; font-size: 13px; font-weight: 600; color: var(--el-text-color-regular); }
.cited-group h5 small { margin-left: 6px; font-size: 13px; font-weight: 400; color: var(--el-text-color-secondary); }
.scope-tag { margin-left: 8px; vertical-align: middle; font-weight: normal; }
:deep(.el-tabs__content) { overflow: visible; }
@media (max-width: 1599px) and (min-width: 1101px) {
  .library-layout.has-detail { grid-template-columns: 248px minmax(0, 1fr); gap: 18px; }
  .library-layout.has-detail.dir-collapsed { grid-template-columns: 48px minmax(0, 1fr); gap: 16px; }
  .library-layout.has-detail .dir-wrap { top: 80px; max-height: calc(100vh - 104px); width: 100%; }
}
.parallel-scroll { display: grid; grid-template-columns: 1fr 1fr; column-gap: 28px; }
.parallel-scroll .ps-col { max-height: 360px; overflow: hidden; padding: 8px 12px 8px 0; font-size: 14px; line-height: 1.8; -webkit-mask-image: linear-gradient(#000 78%, transparent); mask-image: linear-gradient(#000 78%, transparent); }
.parallel-scroll.open .ps-col { max-height: none; overflow: visible; -webkit-mask-image: none; mask-image: none; }
.parallel-scroll .ps-zh { padding-left: 28px; padding-right: 0; border-left: 1px solid #e8edf3; }
:deep(.cy-pill-group .el-radio-button__inner) { font-size: 13px; }
</style>
