<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import AppIcon from '../components/AppIcon.vue'
import { api } from '../api'
import {
  MAP_VIEWBOX,
  WORLD_COUNTRY_PATHS,
  buildRouteArcPath,
  canEdit,
  canExport,
  COUNTRY_NAMES,
  getCountryHub,
  getCountryLabelOffset,
  localTime,
  userName,
} from '@cyshuhe/shared'
import { useAuth } from '../auth'

const auth = useAuth()
const router = useRouter()

interface Progress {
  ready: number
  total: number
  missing: number
  issues: number
}
interface DocItem {
  id: string
  title: string
  conversation_code: string
  source_country?: string
  recipient_country?: string
  created_at: string | null
  updated_at: string | null
}
interface Filing {
  state: string
  state_zh: string
  submitted_on: string
  correction_on: string
  result_on: string
}
interface AdmItem {
  id: string
  entry_name: string
  country_code: string
  created_at: string
  updated_at: string
  summary: string
  ready: boolean
  approval: unknown
  filing?: Filing
}
interface Row {
  key: string
  projectId: string
  module: '合规评估' | '属地准入'
  title: string
  code: string
  status: string
  short: string
  ok: boolean
  pct: number
  at: string
  routeKey: string
  to: { path: string; query: Record<string, string> }
}
interface OverviewTodo {
  id: string
  kind: string
  level: 'danger' | 'warning' | 'info'
  title: string
  detail: string
  action_label: string
  to: { path: string; query?: Record<string, string> }
}
interface MapRouteItem {
  project_id: string
  title: string
  source_code: string
  recipient_code: string
  source_name: string
  recipient_name: string
  legal_route: string
  ready: number
  total: number
  missing: number
  high_risk: number
  med_risk: number
  low_risk: number
  verdict_label: string
  verdict_tone: string
}
interface DeadlineItem {
  id: string
  title: string
  status: string
  tone: string
  to: { path: string; query?: Record<string, string> }
}
interface RouteBucket {
  route?: string
  label?: string
  count: number
  ready: number
  docs_ready: number
  docs_total: number
}
interface ComplianceOverview {
  summary: {
    projects_total: number
    ready_projects: number
    docs_ready_total: number
    docs_required_total: number
    high_risk_active_total: number
    high_risk_cleared_total: number
    admission_total: number
    admission_ready_or_approved: number
  }
  route_distribution: RouteBucket[]
  legal_route_distribution?: RouteBucket[]
  map_routes?: MapRouteItem[]
  recent_docs?: DocItem[]
  recent_progress?: Record<string, Progress>
  recent_admissions?: AdmItem[]
  deadlines?: DeadlineItem[]
  risk_summary: {
    init_high: number
    init_medium: number
    init_low: number
    current_high: number
    current_medium: number
    current_low: number
    mitigated: number
    downgraded: number
    high_cleared: number
  }
  todos: OverviewTodo[]
}

interface GlobalKbCountry {
  country_code: string
  country_name_zh: string
  laws_count: number
  clauses_count: number
  standards_count: number
  briefing_ready: boolean
  admission_entries_count: number
  official_forms_count: number
}
interface GlobalKbSummary {
  covered_jurisdictions: number
  laws_in_force: number
  clauses_total: number
  translation_coverage_pct: number
  standards_active: number
  standards_cn: number
  standards_intl: number
  kg_nodes_total: number
  kg_edges_total: number
  briefings_ready: number
  admission_entries_total: number
  official_forms_total: number
  countries: GlobalKbCountry[]
}

type UserMapCam = 'WORLD' | 'EUR_MENA' | 'APAC'

const USER_CAMERAS: Record<UserMapCam, { label: string; s: number; cx: number; cy: number }> = {
  WORLD: { label: '全球视图', s: 1, cx: 480, cy: 220 },
  EUR_MENA: { label: '欧非中东走廊', s: 2.25, cx: 520, cy: 145 },
  APAC: { label: '亚太东盟走廊', s: 2.3, cx: 752, cy: 175 },
}

const COUNTRIES: Record<string, string> = COUNTRY_NAMES
const FOLD_LIMIT = 2

const isFullscreen = ref(false)
const boardEl = ref<HTMLElement | null>(null)
const selectedRouteKey = ref<string>('ALL')
const showGlobalOverlay = ref(false)
const activeCam = ref<UserMapCam>('WORLD')

const camScale = ref(1)
const camTx = ref(0)
const camTy = ref(0)
let camAnimId: number | null = null

const recentExpanded = ref(false)
const todosExpanded = ref(false)
const overview = ref<ComplianceOverview | null>(null)
const kbSummary = ref<GlobalKbSummary | null>(null)
const docItems = ref<DocItem[]>([])
const progress = ref<Record<string, Progress>>({})
const admItems = ref<AdmItem[]>([])
const loaded = ref(false)
const hasAdmission = computed(() => admItems.value.length > 0)

const editable = computed(() => canEdit(auth.user))
const exportable = computed(() => canExport(auth.user))
const isPortalTrial = computed(() => auth.user?.signup_source === 'portal' && auth.user?.role === 'viewer')
const worldSvgRef = ref<SVGSVGElement | null>(null)
const worldSvgWidth = ref(960)
let worldResizeObs: ResizeObserver | null = null
const worldLabelFontSize = computed(() =>
  Math.max(13, Math.ceil((13.3 * 960) / Math.max(240, worldSvgWidth.value))),
)

function setUserCamera(cam: UserMapCam) {
  activeCam.value = cam
  const preset = USER_CAMERAS[cam]
  const targetS = preset.s
  const targetTx = 480 - preset.cx * targetS
  const targetTy = 220 - preset.cy * targetS
  if (camAnimId !== null) {
    cancelAnimationFrame(camAnimId)
    camAnimId = null
  }
  const startS = camScale.value
  const startTx = camTx.value
  const startTy = camTy.value
  const t0 = performance.now()
  const dur = 300
  const tick = (now: number) => {
    const p = Math.min(1, (now - t0) / dur)
    const ease = 1 - Math.pow(1 - p, 3)
    camScale.value = startS + (targetS - startS) * ease
    camTx.value = startTx + (targetTx - startTx) * ease
    camTy.value = startTy + (targetTy - startTy) * ease
    if (p < 1) camAnimId = requestAnimationFrame(tick)
    else camAnimId = null
  }
  camAnimId = requestAnimationFrame(tick)
}

const mapCameraTransform = computed(
  () => `translate(${camTx.value.toFixed(2)} ${camTy.value.toFixed(2)}) scale(${camScale.value.toFixed(4)})`,
)

function onKeydown(e: KeyboardEvent) {
  if (e.key === 'Escape' && isFullscreen.value) {
    isFullscreen.value = false
  }
}

function toggleFullscreen() {
  isFullscreen.value = !isFullscreen.value
}

watch(isFullscreen, (val) => {
  document.body.style.overflow = val ? 'hidden' : ''
})

function admStatus(a: AdmItem): string {
  const f = a.filing
  if (!f || f.state === 'preparing') return a.summary.replace(/还没有/g, '尚未')
  const day = f.state === 'submitted' ? f.submitted_on : f.state === 'correcting' ? f.correction_on : f.result_on
  return `${f.state_zh}${day ? ` · ${day}` : ''}`
}

function admShort(a: AdmItem): { short: string; ok: boolean } {
  const f = a.filing
  if (!f || f.state === 'preparing') {
    return a.ready ? { short: '材料已备齐', ok: true } : { short: '待补齐', ok: false }
  }
  return { short: f.state_zh, ok: f.state === 'approved' }
}

function docStatus(p: Progress | undefined): string {
  if (!p) return ''
  if (p.total > 0 && p.ready >= p.total && !p.missing && !p.issues) {
    return editable.value || exportable.value
      ? `本次应出 ${p.total} 份，已全部就绪，可下载成稿`
      : `本次应出 ${p.total} 份，已全部就绪，可在线审阅`
  }
  const parts = [`已就绪 ${p.ready}/${p.total} 份`]
  if (p.issues) parts.push(`发现 ${p.issues} 处填写不一致`)
  if (p.missing) parts.push(`还缺 ${p.missing} 项`)
  else if (p.ready < p.total) parts.push(`待生成论述 ${p.total - p.ready} 份`)
  return parts.join(' · ')
}

function docShort(p: Progress | undefined): { short: string; ok: boolean } {
  if (!p) return { short: '', ok: false }
  if (p.total > 0 && p.ready >= p.total && !p.missing && !p.issues) {
    return { short: `已就绪 ${p.ready}/${p.total} 份`, ok: true }
  }
  if (p.missing) return { short: `还缺 ${p.missing} 项`, ok: false }
  if (p.issues) return { short: `待核查 ${p.issues} 处`, ok: false }
  if (p.ready < p.total) return { short: `待生成论述 ${p.total - p.ready} 份`, ok: false }
  return { short: `已就绪 ${p.ready}/${p.total} 份`, ok: true }
}

const sameName = (title: string) => docItems.value.filter((d) => d.title === title).length > 1
const madeAt = (title: string, at: string | null) => (sameName(title) ? localTime(at) : localTime(at, 'date'))

const mapRouteByProject = computed(() => {
  const m = new Map<string, MapRouteItem>()
  for (const r of overview.value?.map_routes || []) {
    m.set(r.project_id, r)
  }
  return m
})

const rows = computed<Row[]>(() => {
  const combined: Row[] = [
    ...docItems.value.map((d): Row => {
      const p = progress.value[d.id]
      const st = docShort(p)
      const mr = mapRouteByProject.value.get(d.id)
      const rKey = mr && mr.source_code && mr.recipient_code ? `${mr.source_code}->${mr.recipient_code}` : ''
      const pct = p && p.total > 0 ? Math.min(100, Math.round((p.ready / p.total) * 100)) : st.ok ? 100 : 35
      return {
        key: `d-${d.id}`,
        projectId: d.id,
        module: '合规评估',
        title: d.title,
        code: `${madeAt(d.title, d.created_at)} 创建`,
        status: docStatus(p),
        short: st.short,
        ok: st.ok,
        pct,
        at: d.updated_at || d.created_at || '',
        routeKey: rKey,
        to: { path: '/documents', query: { project: d.id } },
      }
    }),
    ...admItems.value.map((a): Row => {
      const st = admShort(a)
      return {
        key: `a-${a.id}`,
        projectId: a.id,
        module: '属地准入',
        title: `${COUNTRIES[a.country_code] ?? a.country_code} · ${a.entry_name}`,
        code: `${localTime(a.created_at, 'date')} 创建`,
        status: admStatus(a),
        short: st.short,
        ok: st.ok,
        pct: st.ok || a.ready ? 100 : 55,
        at: a.updated_at,
        routeKey: a.country_code ? `ADM:${a.country_code}` : '',
        to: { path: '/admission', query: { project: a.id } },
      }
    }),
  ]
  return combined.sort((x, y) => Number(x.ok) - Number(y.ok) || y.at.localeCompare(x.at))
})

const visibleRows = computed(() => (recentExpanded.value ? rows.value : rows.value.slice(0, FOLD_LIMIT)))
const visibleDeadlines = computed(() => {
  const dls = overview.value?.deadlines ?? []
  return todosExpanded.value ? dls : dls.slice(0, FOLD_LIMIT)
})
const visibleTodos = computed(() => {
  const list = overview.value?.todos ?? []
  if (todosExpanded.value) return list
  const rem = Math.max(0, FOLD_LIMIT - visibleDeadlines.value.length)
  return list.slice(0, rem)
})
const hiddenTodoCount = computed(() => {
  const total = (overview.value?.deadlines?.length ?? 0) + (overview.value?.todos?.length ?? 0)
  return Math.max(0, total - FOLD_LIMIT)
})

interface ArcGroup {
  key: string
  sourceCode: string
  recipientCode: string
  sourceName: string
  recipientName: string
  path: string
  count: number
  readyDocs: number
  totalDocs: number
  highRisk: number
  legalRoutes: string[]
  tone: 'done' | 'warn' | 'danger'
  firstProjectId: string
}

const arcGroups = computed<ArcGroup[]>(() => {
  const groups = new Map<string, ArcGroup>()
  ;(overview.value?.map_routes || []).forEach((r, idx) => {
    const sHub = getCountryHub(r.source_code)
    const tHub = getCountryHub(r.recipient_code)
    if (!sHub || !tHub || r.source_code === r.recipient_code) return
    const key = `${r.source_code}->${r.recipient_code}`
    let g = groups.get(key)
    if (!g) {
      const d = buildRouteArcPath(r.source_code, r.recipient_code, idx)
      if (!d) return
      g = {
        key,
        sourceCode: r.source_code,
        recipientCode: r.recipient_code,
        sourceName: r.source_name || COUNTRIES[r.source_code] || r.source_code,
        recipientName: r.recipient_name || COUNTRIES[r.recipient_code] || r.recipient_code,
        path: d,
        count: 0,
        readyDocs: 0,
        totalDocs: 0,
        highRisk: 0,
        legalRoutes: [],
        tone: 'done',
        firstProjectId: r.project_id,
      }
      groups.set(key, g)
    }
    g.count += 1
    g.readyDocs += r.ready
    g.totalDocs += r.total
    g.highRisk += r.high_risk
    if (r.legal_route && !g.legalRoutes.includes(r.legal_route)) {
      g.legalRoutes.push(r.legal_route)
    }
    if (r.high_risk > 0 || r.verdict_tone === 'danger') {
      g.tone = 'danger'
    } else if (g.tone !== 'danger' && (r.missing > 0 || r.ready < r.total || r.verdict_tone === 'warn')) {
      g.tone = 'warn'
    }
  })
  return [...groups.values()].sort((a, b) => b.count - a.count)
})

const activeCountryCodes = computed(() => {
  const codes = new Set<string>()
  for (const r of overview.value?.map_routes || []) {
    if (r.source_code && (WORLD_COUNTRY_PATHS[r.source_code] || getCountryHub(r.source_code))) codes.add(r.source_code)
    if (r.recipient_code && (WORLD_COUNTRY_PATHS[r.recipient_code] || getCountryHub(r.recipient_code))) codes.add(r.recipient_code)
  }
  for (const a of admItems.value) {
    if (a.country_code && (WORLD_COUNTRY_PATHS[a.country_code] || getCountryHub(a.country_code))) codes.add(a.country_code)
  }
  return codes
})

const globalLibraryCodes = computed(() => {
  const s = new Set<string>()
  for (const c of kbSummary.value?.countries || []) {
    if ((WORLD_COUNTRY_PATHS[c.country_code] || getCountryHub(c.country_code)) && !activeCountryCodes.value.has(c.country_code)) {
      s.add(c.country_code)
    }
  }
  return s
})

const backgroundMapEntries = computed(() =>
  Object.entries(WORLD_COUNTRY_PATHS).filter(
    ([cc]) => !activeCountryCodes.value.has(cc) && !((showGlobalOverlay.value || isPortalTrial.value) && globalLibraryCodes.value.has(cc)),
  ),
)

const globalOverlayEntries = computed(() => {
  if (!isPortalTrial.value && !showGlobalOverlay.value) return []
  return (kbSummary.value?.countries || [])
    .filter((c) => WORLD_COUNTRY_PATHS[c.country_code] && !activeCountryCodes.value.has(c.country_code))
    .map((c) => ({
      code: c.country_code,
      d: WORLD_COUNTRY_PATHS[c.country_code] || '',
      name: c.country_name_zh,
      laws: c.laws_count,
    }))
})

const activeMapEntries = computed(() =>
  [...activeCountryCodes.value]
    .filter((cc) => Boolean(WORLD_COUNTRY_PATHS[cc]))
    .map((cc) => ({
      code: cc,
      d: WORLD_COUNTRY_PATHS[cc] || '',
      name: COUNTRIES[cc] || cc,
      isCn: cc === 'CN',
    })),
)

const screenHubNodes = computed(() => {
  const s = camScale.value
  const tx = camTx.value
  const ty = camTy.value
  const fScale = worldLabelFontSize.value / 13
  const minX = 74 * fScale
  const minY = 16 * fScale
  const raw = [...activeCountryCodes.value]
    .map((cc) => {
      const hub = getCountryHub(cc)
      if (!hub) return null
      const sx = hub[0] * s + tx
      const sy = hub[1] * s + ty
      if (sx < 14 || sx > 946 || sy < 14 || sy > 426) return null
      const baseLbl = getCountryLabelOffset(cc, hub)
      const lx = Math.max(24, Math.min(936, sx + (baseLbl.x - hub[0])))
      const ly = Math.max(18, Math.min(424, sy + (baseLbl.y - hub[1])))
      const highlighted = isCountryHighlighted(cc)
      const pinned = cc === 'CN' || (selectedRouteKey.value !== 'ALL' && highlighted)
      return {
        code: cc,
        name: COUNTRIES[cc] || cc,
        isCn: cc === 'CN',
        sx,
        sy,
        lx,
        ly,
        anchor: baseLbl.anchor,
        highlighted,
        pinned,
      }
    })
    .filter((x): x is NonNullable<typeof x> => x !== null)

  const placed: Array<{ x: number; y: number }> = []
  const ordered = [...raw].sort((a, b) => Number(b.pinned) - Number(a.pinned) || Number(b.isCn) - Number(a.isCn))
  const showByCode = new Map<string, boolean>()
  for (const n of ordered) {
    const collides = placed.some((p) => Math.abs(p.x - n.lx) < minX && Math.abs(p.y - n.ly) < minY)
    if (collides) {
      showByCode.set(n.code, false)
    } else {
      showByCode.set(n.code, true)
      placed.push({ x: n.lx, y: n.ly })
    }
  }
  return raw.map((n) => ({
    code: n.code,
    name: n.name,
    isCn: n.isCn,
    sx: n.sx,
    sy: n.sy,
    lx: n.lx,
    ly: n.ly,
    anchor: n.anchor,
    highlighted: n.highlighted,
    showLabel: showByCode.get(n.code) ?? true,
  }))
})

const selectedArc = computed(() => arcGroups.value.find((g) => g.key === selectedRouteKey.value) || null)
const selectedAdmCountry = computed(() => {
  if (!selectedRouteKey.value.startsWith('ADM:')) return ''
  return selectedRouteKey.value.slice(4)
})

function isCountryHighlighted(cc: string): boolean {
  if (selectedRouteKey.value === 'ALL') return true
  if (selectedArc.value) {
    return selectedArc.value.sourceCode === cc || selectedArc.value.recipientCode === cc
  }
  if (selectedAdmCountry.value) {
    return selectedAdmCountry.value === cc
  }
  return false
}

function selectCountryOnMap(cc: string) {
  const hitArc = arcGroups.value.find((g) => g.recipientCode === cc || g.sourceCode === cc)
  if (hitArc) {
    selectedRouteKey.value = selectedRouteKey.value === hitArc.key ? 'ALL' : hitArc.key
    return
  }
  if (admItems.value.some((a) => a.country_code === cc)) {
    const key = `ADM:${cc}`
    selectedRouteKey.value = selectedRouteKey.value === key ? 'ALL' : key
  }
}

function focusRowOnMap(r: Row) {
  if (!r.routeKey) {
    void router.push(r.to)
    return
  }
  selectedRouteKey.value = selectedRouteKey.value === r.routeKey ? 'ALL' : r.routeKey
}

const summaryMetrics = computed(() => {
  const s = overview.value?.summary
  const rs = overview.value?.risk_summary
  const projTotal = s?.projects_total ?? docItems.value.length
  const projReady = s?.ready_projects ?? 0
  const docsReady = s?.docs_ready_total ?? 0
  const docsReq = s?.docs_required_total ?? 0
  const curHigh = rs?.current_high ?? s?.high_risk_active_total ?? 0
  const clearedHigh = rs?.high_cleared ?? s?.high_risk_cleared_total ?? 0
  const admTotal = s?.admission_total ?? admItems.value.length
  const admReady = s?.admission_ready_or_approved ?? admItems.value.filter((x) => x.ready).length
  const projPct = projTotal > 0 ? Math.round((projReady / projTotal) * 100) : 0
  const docsPct = docsReq > 0 ? Math.round((docsReady / docsReq) * 100) : 100
  const admPct = admTotal > 0 ? Math.round((admReady / admTotal) * 100) : 100
  return { projTotal, projReady, docsReady, docsReq, curHigh, clearedHigh, admTotal, admReady, projPct, docsPct, admPct }
})

const postureGrade = computed(() => {
  if (summaryMetrics.value.curHigh > 0) {
    return {
      tone: 'warn',
      badge: `风险重点管控 · ${summaryMetrics.value.curHigh} 项高风险待闭环`,
    }
  }
  const rs = overview.value?.risk_summary
  const rsCount = rs
    ? rs.init_high + rs.init_medium + rs.init_low + rs.current_high + rs.current_medium + rs.current_low + rs.mitigated
    : 0
  const hasRiskAssessed = rsCount > 0 || summaryMetrics.value.clearedHigh > 0
  if (!hasRiskAssessed) {
    return {
      tone: 'info',
      badge: '风险待识别定级 · 法定文书齐套推进中',
    }
  }
  if (summaryMetrics.value.docsPct >= 85 && summaryMetrics.value.admPct >= 85) {
    return {
      tone: 'safe',
      badge: '高风险动态清零 · 合规受控放行态',
    }
  }
  return {
    tone: 'info',
    badge: '高风险已清零 · 法定文书齐套推进中',
  }
})

const legalDistribution = computed<RouteBucket[]>(() => {
  const list = overview.value?.legal_route_distribution
  if (list && list.length) return list
  return (overview.value?.route_distribution || []).map((r) => ({
    label: r.route || '跨境项目',
    count: r.count,
    ready: r.ready,
    docs_ready: r.docs_ready,
    docs_total: r.docs_total,
  }))
})

const maxLegalCount = computed(() => Math.max(1, ...legalDistribution.value.map((x) => x.count)))

const riskMaxScale = computed(() => {
  const rs = overview.value?.risk_summary
  if (!rs) return 1
  return Math.max(1, rs.init_high, rs.current_high, rs.init_medium, rs.current_medium, rs.init_low, rs.current_low)
})

async function loadHomeData() {
  try {
    localStorage.removeItem('home-doc-progress-v2')
  } catch {
    /* 忽略浏览器存储限制 */
  }

  const kbPromise = api.get<GlobalKbSummary>('/api/knowledge/posture-summary').catch(() => null)

  if (isPortalTrial.value) {
    kbSummary.value = await kbPromise
    loaded.value = true
    return
  }
  try {
    const [res, kbRes] = await Promise.all([
      api.get<ComplianceOverview>('/api/analysis/doc-projects/compliance-overview'),
      kbPromise,
    ])
    overview.value = res
    kbSummary.value = kbRes
    docItems.value = Array.isArray(res.recent_docs) ? res.recent_docs : []
    progress.value = res.recent_progress || {}
    admItems.value = Array.isArray(res.recent_admissions) ? res.recent_admissions : []
  } catch {
    overview.value = null
    kbSummary.value = await kbPromise
  } finally {
    loaded.value = true
  }
}

function resetAllRoutes() {
  selectedRouteKey.value = 'ALL'
  if (activeCam.value !== 'WORLD') setUserCamera('WORLD')
}

const TODO_TAG: Record<string, string> = {
  high_risk: '高风险待处置',
  risk_stale: '待重新评估',
  law_update: '更新通知',
  admission_pending: '属地准入',
  missing_info: '待补信息',
}

const TODO_TAG_VIEWER: Record<string, string> = {
  high_risk: '高风险（经办整改中）',
  risk_stale: '待经办复评',
  law_update: '法规更新查阅',
  admission_pending: '准入推进中',
  missing_info: '待经办补齐',
}

watch(
  worldSvgRef,
  (el) => {
    worldResizeObs?.disconnect()
    if (!el || typeof ResizeObserver === 'undefined') return
    const w0 = el.getBoundingClientRect().width
    if (w0 > 0) worldSvgWidth.value = w0
    worldResizeObs = new ResizeObserver((entries) => {
      const w = entries[0]?.contentRect?.width || el.getBoundingClientRect().width
      if (w > 0) worldSvgWidth.value = w
    })
    worldResizeObs.observe(el)
  },
  { flush: 'post' },
)

onMounted(() => {
  window.addEventListener('keydown', onKeydown)
  void loadHomeData()
})

onUnmounted(() => {
  worldResizeObs?.disconnect()
  worldResizeObs = null
  if (camAnimId !== null) cancelAnimationFrame(camAnimId)
  document.body.style.overflow = ''
  window.removeEventListener('keydown', onKeydown)
})

const visibleCards = [
  { path: '/knowledge', icon: 'book', title: '法规知识库', sub: '现行法规双语对照与八维简报' },
  { path: '/documents', icon: 'shield', title: '合规评估', sub: '法定路径研判与 17 语种文书' },
  { path: '/admission', icon: 'flow', title: '海外属地准入', sub: '目标国准入事项与官方原表套打' },
]

const portalTrialCards = [
  { path: '/knowledge', icon: 'book', title: '法规知识库', desc: '查阅目标国现行法规双语对照、国家合规简报与汽车数据标准目录' },
  { path: '/analysis', icon: 'shield', title: '咨询助手', desc: '针对跨境流向与合规问题在线提问，回答逐条附现行有效条文依据' },
  { path: '/admission?tab=library', icon: 'flow', title: '海外属地准入事项库', desc: '按目标国家与车载产品类别检索法定准入事项、受理机关与法定申报表' },
]
</script>

<template>
  <div class="home-wrap">
    <!-- 门户体验访客：同款旗舰视觉底座，但严格限定为“公开法规覆盖与个人体验权限”（零企业项目/零流向/零风险/零导出） -->
    <section v-if="isPortalTrial" class="portal-welcome user-board" data-testid="home-portal-welcome">
      <div class="portal-head">
        <div class="portal-badge-row">
          <span class="perm-role-chip portal">门户体验访客权限 · 仅限查阅公开法规库与个人问答（企业项目与导出已隔离）</span>
        </div>
        <h1>欢迎体验车研数合汽车跨境数据合规服务平台</h1>
        <p>当前为门户公开注册体验账号：您可免费查阅全球在册汽车数据法规、国家八维合规简报与海外属地准入事项库，或使用咨询助手开展法规问答（提问记录仅本人可见，不含企业评估项目创建与正式文书导出权限）。</p>
      </div>

      <div v-if="kbSummary" class="kpi-strip portal-kpi5" aria-label="平台公开法规底座能力">
        <div class="kpi-tile">
          <div class="kpi-head-row"><span class="kpi-label">公开法域覆盖</span><span class="kpi-chip">可查阅</span></div>
          <div class="kpi-val-row"><strong>{{ kbSummary.covered_jurisdictions }}</strong><span class="kpi-unit">个国家与地区</span></div>
        </div>
        <div class="kpi-tile">
          <div class="kpi-head-row"><span class="kpi-label">现行有效法规</span><span class="kpi-chip safe">双语对照</span></div>
          <div class="kpi-val-row"><strong>{{ kbSummary.laws_in_force }}</strong><span class="kpi-unit">部</span></div>
        </div>
        <div class="kpi-tile">
          <div class="kpi-head-row"><span class="kpi-label">汽车技术标准</span><span class="kpi-chip gold">中外双轨</span></div>
          <div class="kpi-val-row"><strong>{{ kbSummary.standards_active }}</strong><span class="kpi-unit">项</span></div>
        </div>
        <div class="kpi-tile">
          <div class="kpi-head-row"><span class="kpi-label">国家合规简报</span><span class="kpi-chip safe">在线可读</span></div>
          <div class="kpi-val-row"><strong>{{ kbSummary.briefings_ready }}</strong><span class="kpi-unit">国八维简报</span></div>
        </div>
        <div class="kpi-tile">
          <div class="kpi-head-row"><span class="kpi-label">属地准入事项</span><span class="kpi-chip">公开名录</span></div>
          <div class="kpi-val-row"><strong>{{ kbSummary.admission_entries_total }}</strong><span class="kpi-unit">项法定事项</span></div>
        </div>
      </div>

      <div v-if="kbSummary" class="portal-map-panel">
        <div class="map-topbar">
          <div>
            <h2>平台公开法规与准入覆盖分布预览（访客体验视图）</h2>
            <span class="map-sub">仅展示平台公开收录的 {{ kbSummary.covered_jurisdictions }} 个国家与地区法规底座，不包含任何企业项目或跨境传输路线</span>
          </div>
          <RouterLink to="/knowledge" class="mf-cta">进入法规知识库 →</RouterLink>
        </div>
        <div class="map-stage portal-map-stage">
          <svg :viewBox="MAP_VIEWBOX" class="world-svg" role="img" aria-label="公开法规法域覆盖分布图">
            <g class="u-grid-lines" stroke-width="0.9">
              <line x1="0" y1="110" x2="960" y2="110" />
              <line x1="0" y1="220" x2="960" y2="220" />
              <line x1="0" y1="330" x2="960" y2="330" />
              <line x1="240" y1="0" x2="240" y2="440" />
              <line x1="480" y1="0" x2="480" y2="440" />
              <line x1="720" y1="0" x2="720" y2="440" />
            </g>
            <g class="land-bg">
              <path v-for="[cc, d] in backgroundMapEntries" :key="`pbg-${cc}`" :d="d" />
            </g>
            <g class="land-global-overlay">
              <path
                v-for="gItem in globalOverlayEntries"
                :key="`pgl-${gItem.code}`"
                :d="gItem.d"
                class="active-country"
                :class="{ 'origin-cn': gItem.code === 'CN' }"
              >
                <title>{{ gItem.name }}（已收录 {{ gItem.laws }} 部现行法规）</title>
              </path>
            </g>
          </svg>
        </div>
      </div>

      <div class="portal-grid">
        <RouterLink v-for="c in portalTrialCards" :key="c.path" :to="c.path" class="portal-card">
          <span class="feature-icon"><AppIcon :name="c.icon" /></span>
          <div class="portal-card-body">
            <strong>{{ c.title }}</strong>
            <span>{{ c.desc }}</span>
          </div>
          <AppIcon name="arrow" />
        </RouterLink>
      </div>
    </section>

    <!-- 企业账号（企业经办人 member / 本单位普通查看者 viewer） -->
    <template v-else>
      <div
        ref="boardEl"
        class="user-board"
        :class="{ 'is-fullscreen': isFullscreen, 'has-projects': loaded && rows.length > 0 }"
      >
        <header class="hero-head">
          <div class="hero-title-group">
            <h1>欢迎，{{ userName(auth.user) }}</h1>
            <span class="perm-role-chip" :class="editable ? 'member' : 'viewer'">
              {{
                editable
                  ? '企业编辑权限 · 可新建、填报、修订与整包导出'
                  : `企业只读权限 · 禁新建修改 · ${exportable ? '已授权导出成稿' : '禁止导出文件'}`
              }}
            </span>
            <span v-if="loaded && rows.length" class="posture-grade-pill" :class="postureGrade.tone">
              {{ postureGrade.badge }}
            </span>
          </div>

          <div class="hero-actions">
            <button
              v-if="loaded && rows.length"
              type="button"
              class="fs-btn"
              data-testid="user-fullscreen-btn"
              @click="toggleFullscreen"
            >
              {{ isFullscreen ? '退出全屏' : '全屏展示' }}
            </button>
            <RouterLink v-if="editable && loaded && rows.length" to="/documents">
              <el-button type="primary" data-testid="home-new">新建合规评估</el-button>
            </RouterLink>
          </div>
        </header>

        <div v-if="!loaded" v-loading="true" class="recent-loading" data-testid="home-recent-loading" />

        <!-- 空企业状态：保留新建引导与只读说明，同时直观展示平台全球底座能力 -->
        <div v-else-if="!rows.length" class="empty-org-card" data-testid="home-recent-empty">
          <template v-if="editable">
            <span class="capability-kicker">自助合规评估 · 17 语种属地报告 · 东道国法定原表一键齐套</span>
            <h2>开始本单位首个汽车数据跨境合规评估</h2>
            <p>选定数据发出国、数据接收国与车载业务场景后，平台将自动研判法定出境路径、筛选必备合规文件清单并同步呈现跨境流向状态。</p>
            <div v-if="kbSummary" class="capability-strip empty-cap-strip">
              <div class="cap-item">
                <strong>{{ kbSummary.covered_jurisdictions }} 国</strong>
                <span>目标法域覆盖</span>
              </div>
              <div class="cap-item">
                <strong>{{ kbSummary.laws_in_force }} 部</strong>
                <span>现行有效法规</span>
              </div>
              <div class="cap-item">
                <strong>{{ kbSummary.standards_active }} 项</strong>
                <span>汽车技术标准</span>
              </div>
              <div class="cap-item">
                <strong>{{ kbSummary.official_forms_total }} 套</strong>
                <span>法定申报原表</span>
              </div>
            </div>
            <RouterLink to="/documents"><el-button type="primary" data-testid="home-new">新建合规评估项目</el-button></RouterLink>
          </template>
          <template v-else>
            <p class="viewer-empty">当前暂无本单位共享项目，您可以先查阅法规知识库或使用咨询助手提问查法。</p>
            <div v-if="kbSummary" class="capability-strip empty-cap-strip">
              <div class="cap-item">
                <strong>{{ kbSummary.covered_jurisdictions }} 国</strong>
                <span>目标法域覆盖</span>
              </div>
              <div class="cap-item">
                <strong>{{ kbSummary.laws_in_force }} 部</strong>
                <span>现行有效法规</span>
              </div>
              <div class="cap-item">
                <strong>{{ kbSummary.briefings_ready }} 国</strong>
                <span>八维合规简报</span>
              </div>
              <div class="cap-item">
                <strong>{{ kbSummary.official_forms_total }} 套</strong>
                <span>法定申报原表</span>
              </div>
            </div>
          </template>
        </div>

        <!-- 企业旗舰态势主看板 -->
        <section v-else class="posture-screen" data-testid="user-posture-screen">
          <!-- 商业护航能力价值带（按编辑/只读与导出开关精准区分提示） -->
          <div v-if="kbSummary" class="assurance-ribbon">
            <span class="assure-main">
              <strong>全球法规底座实时护航：</strong>已联动 {{ kbSummary.covered_jurisdictions }} 国 {{ kbSummary.laws_in_force }} 部现行法规（{{ kbSummary.clauses_total.toLocaleString() }} 条可用条文）、{{ kbSummary.standards_active }} 项汽车标准与 {{ kbSummary.official_forms_total }} 套法定申报原表
            </span>
            <span class="assure-pills">
              <RouterLink to="/knowledge" class="assure-link">查阅国别简报 →</RouterLink>
              <span class="assure-chip" :class="{ warn: !editable && !exportable }">
                {{
                  editable
                    ? '支持新建评估与 17 语种整包导出'
                    : exportable
                      ? '只读审阅权限 · 已授权导出成稿'
                      : '受控只读权限 · 仅限在线审阅（禁止导出）'
                }}
              </span>
            </span>
          </div>

          <!-- 4 大核心进度舱（按编辑实操 vs 只读审阅权限精确映射） -->
          <div class="kpi-strip">
            <div class="kpi-tile">
              <div class="kpi-head-row">
                <span class="kpi-label">{{ editable ? '在办评估项目' : '本单位项目审阅' }}</span>
                <span class="kpi-chip">齐套 {{ summaryMetrics.projReady }}/{{ summaryMetrics.projTotal }}</span>
              </div>
              <div class="kpi-val-row">
                <strong>{{ summaryMetrics.projTotal }}</strong>
                <span class="kpi-unit">个项目</span>
                <span class="kpi-sub">齐套率 {{ summaryMetrics.projPct }}%</span>
              </div>
              <div class="kpi-bar"><div class="kpi-bar-fill" :style="{ width: `${Math.max(8, summaryMetrics.projPct)}%` }" /></div>
            </div>

            <div class="kpi-tile">
              <div class="kpi-head-row">
                <span class="kpi-label">{{ editable ? '必备文件编撰进度' : '本单位文书齐套审阅' }}</span>
                <span class="kpi-chip" :class="editable || exportable ? 'safe' : 'gold'">
                  {{ editable ? '17 语种可导' : (exportable ? '允许导出成稿' : '仅限在线审阅') }}
                </span>
              </div>
              <div class="kpi-val-row">
                <strong>{{ summaryMetrics.docsReady }} / {{ summaryMetrics.docsReq }}</strong>
                <span class="kpi-unit">份</span>
                <span class="kpi-sub">{{ summaryMetrics.docsReq ? `完成率 ${summaryMetrics.docsPct}%` : '按流向核定' }}</span>
              </div>
              <div class="kpi-bar"><div class="kpi-bar-fill safe" :style="{ width: `${Math.max(8, summaryMetrics.docsPct)}%` }" /></div>
            </div>

            <div class="kpi-tile">
              <div class="kpi-head-row">
                <span class="kpi-label">{{ editable ? '风险压降闭环' : '本单位风险压降监督' }}</span>
                <span class="kpi-chip" :class="summaryMetrics.curHigh === 0 ? 'safe' : 'danger'">
                  {{ summaryMetrics.curHigh === 0 ? '高风险清零' : (editable ? '待落实措施' : '经办整改中') }}
                </span>
              </div>
              <div class="kpi-val-row">
                <strong :class="{ 'is-danger': summaryMetrics.curHigh > 0, 'is-safe': summaryMetrics.curHigh === 0 }">{{ summaryMetrics.curHigh }}</strong>
                <span class="kpi-unit">项当前高风险</span>
                <span class="kpi-sub">已化解高风险 {{ summaryMetrics.clearedHigh }} 项</span>
              </div>
              <div class="kpi-bar">
                <div
                  class="kpi-bar-fill"
                  :class="summaryMetrics.curHigh === 0 ? 'safe' : 'danger'"
                  :style="{ width: summaryMetrics.curHigh === 0 ? '100%' : '45%' }"
                />
              </div>
            </div>

            <div class="kpi-tile">
              <div class="kpi-head-row">
                <span class="kpi-label">{{ editable ? '海外属地准入' : '海外准入办理审阅' }}</span>
                <span class="kpi-chip gold">{{ editable ? '官方原表套打' : '准入进度查阅' }}</span>
              </div>
              <div class="kpi-val-row">
                <strong>{{ summaryMetrics.admTotal }}</strong>
                <span class="kpi-unit">项</span>
                <span class="kpi-sub">已备齐或获批 {{ summaryMetrics.admReady }} 项</span>
              </div>
              <div class="kpi-bar"><div class="kpi-bar-fill gold" :style="{ width: `${Math.max(8, summaryMetrics.admPct)}%` }" /></div>
            </div>
          </div>

          <div class="posture-grid">
            <!-- 左栏：法定路径与风险前后演变可视化对比 -->
            <div class="posture-col posture-left">
              <div class="p-panel">
                <div class="p-panel-head">
                  <h2>出境法定路径分布</h2>
                  <span>共 {{ summaryMetrics.projTotal }} 项</span>
                </div>
                <div v-if="legalDistribution.length" class="bar-list">
                  <div v-for="item in legalDistribution" :key="item.label || item.route" class="bar-row">
                    <div class="bar-top">
                      <span class="bar-name">{{ item.label || item.route }}</span>
                      <span class="bar-num">{{ item.count }} 项 · 文件 {{ item.docs_ready }}/{{ item.docs_total }}</span>
                    </div>
                    <div class="bar-track">
                      <div class="bar-fill" :style="{ width: `${Math.max(12, Math.round((item.count / maxLegalCount) * 100))}%` }" />
                    </div>
                  </div>
                </div>
                <p v-else class="p-empty">暂无评估项目路径分布</p>
              </div>

              <div class="p-panel p-panel-flex">
                <div class="p-panel-head">
                  <h2>风险初评与当前压降对比</h2>
                  <span>动态复评同步</span>
                </div>
                <div v-if="overview?.risk_summary" class="risk-compare">
                  <div class="rc-card">
                    <div class="rc-row">
                      <span class="rc-grade danger">高风险</span>
                      <span class="rc-pair">初评 {{ overview.risk_summary.init_high }} → <b>当前 {{ overview.risk_summary.current_high }}</b></span>
                    </div>
                    <div class="rc-dual-bars">
                      <div class="rc-bar init-danger" :style="{ width: `${Math.max(6, Math.round((overview.risk_summary.init_high / riskMaxScale) * 100))}%` }" />
                      <div class="rc-bar cur-safe" :style="{ width: `${overview.risk_summary.current_high > 0 ? Math.max(8, Math.round((overview.risk_summary.current_high / riskMaxScale) * 100)) : 0}%` }" />
                    </div>
                  </div>

                  <div class="rc-card">
                    <div class="rc-row">
                      <span class="rc-grade warn">中风险</span>
                      <span class="rc-pair">初评 {{ overview.risk_summary.init_medium }} → <b>当前 {{ overview.risk_summary.current_medium }}</b></span>
                    </div>
                    <div class="rc-dual-bars">
                      <div class="rc-bar init-warn" :style="{ width: `${Math.max(6, Math.round((overview.risk_summary.init_medium / riskMaxScale) * 100))}%` }" />
                      <div class="rc-bar cur-cyan" :style="{ width: `${overview.risk_summary.current_medium > 0 ? Math.max(8, Math.round((overview.risk_summary.current_medium / riskMaxScale) * 100)) : 0}%` }" />
                    </div>
                  </div>

                  <div class="rc-card">
                    <div class="rc-row">
                      <span class="rc-grade safe">低风险</span>
                      <span class="rc-pair">初评 {{ overview.risk_summary.init_low }} → <b>当前 {{ overview.risk_summary.current_low }}</b></span>
                    </div>
                    <div class="rc-dual-bars">
                      <div class="rc-bar cur-safe" :style="{ width: `${overview.risk_summary.current_low > 0 ? Math.max(8, Math.round((overview.risk_summary.current_low / riskMaxScale) * 100)) : 6}%` }" />
                    </div>
                  </div>

                  <div class="rc-foot-boxes">
                    <div class="rc-stat-box">
                      <span>已降级风险</span>
                      <strong>{{ overview.risk_summary.downgraded }} 项</strong>
                    </div>
                    <div class="rc-stat-box">
                      <span>已完成整改</span>
                      <strong>{{ overview.risk_summary.mitigated }} 项</strong>
                    </div>
                  </div>
                </div>
                <p v-else class="p-empty">项目完成风险定级后自动汇总</p>
              </div>
            </div>

            <!-- 中栏：本单位真实跨境流向矢量地图 + 区域变焦 + 46国法规底座叠加 -->
            <div class="posture-col posture-center">
              <div class="map-card">
                <div class="map-topbar">
                  <div>
                    <h2>本单位跨境数据流向与准入节点沙盘</h2>
                    <span class="map-sub">点亮本单位在办发出国、接收国与准入目标国；支持叠加查看平台全球法规覆盖网</span>
                  </div>
                  <div class="map-pills">
                    <button
                      type="button"
                      class="map-pill"
                      :class="{ active: selectedRouteKey === 'ALL' && activeCam === 'WORLD' }"
                      data-testid="user-map-reset-all"
                      @click="resetAllRoutes"
                    >
                      全部流向（{{ arcGroups.length }}）
                    </button>
                    <button
                      type="button"
                      class="map-pill"
                      :class="{ active: activeCam === 'EUR_MENA' }"
                      @click="setUserCamera(activeCam === 'EUR_MENA' ? 'WORLD' : 'EUR_MENA')"
                    >
                      欧非走廊
                    </button>
                    <button
                      type="button"
                      class="map-pill"
                      :class="{ active: activeCam === 'APAC' }"
                      @click="setUserCamera(activeCam === 'APAC' ? 'WORLD' : 'APAC')"
                    >
                      亚太走廊
                    </button>
                    <button
                      v-if="kbSummary && kbSummary.covered_jurisdictions > 0"
                      type="button"
                      class="map-pill overlay-pill"
                      :class="{ active: showGlobalOverlay }"
                      @click="showGlobalOverlay = !showGlobalOverlay"
                    >
                      {{ showGlobalOverlay ? `已叠 ${kbSummary.covered_jurisdictions} 国底座` : `叠加 ${kbSummary.covered_jurisdictions} 国法规网` }}
                    </button>
                  </div>
                </div>

                <div class="map-stage">
                  <svg ref="worldSvgRef" :viewBox="MAP_VIEWBOX" class="world-svg" role="img" aria-label="本单位跨境数据流向图">
                    <g class="u-grid-lines" stroke-width="0.9">
                      <line x1="0" y1="110" x2="960" y2="110" />
                      <line x1="0" y1="220" x2="960" y2="220" />
                      <line x1="0" y1="330" x2="960" y2="330" />
                      <line x1="240" y1="0" x2="240" y2="440" />
                      <line x1="480" y1="0" x2="480" y2="440" />
                      <line x1="720" y1="0" x2="720" y2="440" />
                    </g>

                    <g :transform="mapCameraTransform">
                      <g class="land-bg">
                        <path
                          v-for="[cc, d] in backgroundMapEntries"
                          :key="`bg-${cc}`"
                          :d="d"
                          vector-effect="non-scaling-stroke"
                        />
                      </g>

                      <g v-if="showGlobalOverlay" class="land-global-overlay">
                        <path
                          v-for="gItem in globalOverlayEntries"
                          :key="`gl-${gItem.code}`"
                          :d="gItem.d"
                          class="overlay-country"
                          vector-effect="non-scaling-stroke"
                        >
                          <title>{{ gItem.name }}（平台已收录 {{ gItem.laws }} 部现行法规，随时可建项目）</title>
                        </path>
                      </g>

                      <g class="land-active">
                        <path
                          v-for="item in activeMapEntries"
                          :key="`act-${item.code}`"
                          :d="item.d"
                          class="active-country"
                          :class="{ 'origin-cn': item.isCn, dimmed: !isCountryHighlighted(item.code) }"
                          vector-effect="non-scaling-stroke"
                          @click="selectCountryOnMap(item.code)"
                        >
                          <title>{{ item.name }}</title>
                        </path>
                      </g>

                      <g class="route-arcs">
                        <g
                          v-for="arc in arcGroups"
                          :key="arc.key"
                          class="arc-item"
                          :class="{
                            selected: selectedRouteKey === arc.key,
                            dimmed: selectedRouteKey !== 'ALL' && selectedRouteKey !== arc.key,
                          }"
                          @click="selectedRouteKey = selectedRouteKey === arc.key ? 'ALL' : arc.key"
                        >
                          <path
                            :d="arc.path"
                            class="arc-base"
                            vector-effect="non-scaling-stroke"
                            :stroke="arc.tone === 'danger' ? '#f87171' : arc.tone === 'warn' ? '#fbbf24' : '#34d399'"
                          />
                          <path :d="arc.path" class="arc-dash" vector-effect="non-scaling-stroke" />
                        </g>
                      </g>
                    </g>

                    <!-- 屏幕空间恒定 13px 清晰节点标签 -->
                    <g class="country-hubs">
                      <g
                        v-for="node in screenHubNodes"
                        :key="`hub-${node.code}`"
                        class="hub-node"
                        :class="{ dimmed: !node.highlighted }"
                        @click="selectCountryOnMap(node.code)"
                      >
                        <circle :cx="node.sx" :cy="node.sy" :r="node.isCn ? 8.5 : 7" class="hub-ring" :class="{ gold: node.isCn }" />
                        <circle :cx="node.sx" :cy="node.sy" :r="node.isCn ? 4.4 : 3.6" class="hub-dot" :class="{ gold: node.isCn }" />
                        <text
                          v-if="node.showLabel"
                          :x="node.lx"
                          :y="node.ly"
                          :text-anchor="node.anchor"
                          class="hub-label"
                          :class="{ gold: node.isCn }"
                          :font-size="worldLabelFontSize"
                          font-weight="600"
                        >
                          {{ node.isCn ? `★${node.name}` : node.name }}
                        </text>
                      </g>
                    </g>
                  </svg>
                </div>

                <!-- 地图底部实时聚焦摘要栏 -->
                <div class="map-focus-bar" data-testid="user-map-focus-bar">
                  <template v-if="selectedArc">
                    <div class="mf-main">
                      <strong>当前聚焦流向：{{ selectedArc.sourceName }} → {{ selectedArc.recipientName }}</strong>
                      <span>关联项目 {{ selectedArc.count }} 个 · 应出文件已就绪 {{ selectedArc.readyDocs }}/{{ selectedArc.totalDocs }} 份 · 当前高风险 {{ selectedArc.highRisk }} 项 · 路径：{{ selectedArc.legalRoutes.join('、') || '待核定' }}</span>
                    </div>
                    <div class="mf-actions">
                      <button type="button" class="mf-link" @click="selectedRouteKey = 'ALL'">返回全部流向</button>
                      <RouterLink :to="{ path: '/documents', query: { project: selectedArc.firstProjectId } }" class="mf-cta">
                        {{ editable ? '继续办理 →' : '查看项目 →' }}
                      </RouterLink>
                    </div>
                  </template>
                  <template v-else-if="selectedAdmCountry">
                    <div class="mf-main">
                      <strong>当前聚焦属地准入：{{ COUNTRIES[selectedAdmCountry] || selectedAdmCountry }}</strong>
                      <span>在办属地准入项目 {{ admItems.filter((a) => a.country_code === selectedAdmCountry).length }} 项</span>
                    </div>
                    <div class="mf-actions">
                      <button type="button" class="mf-link" @click="selectedRouteKey = 'ALL'">返回全部流向</button>
                      <RouterLink :to="{ path: '/admission', query: { country: selectedAdmCountry } }" class="mf-cta">{{ editable ? '前往办理 →' : '查看准入 →' }}</RouterLink>
                    </div>
                  </template>
                  <template v-else>
                    <div class="mf-main">
                      <strong>本单位全部跨境流向总览</strong>
                      <span>已建立 {{ arcGroups.length }} 条跨国传输流向，覆盖 {{ activeMapEntries.length }} 个国家或地区；点击地图节点或右侧项目行可定位对应流向。</span>
                    </div>
                    <div class="mf-legend">
                      <span class="lg safe">低风险或已就绪</span>
                      <span class="lg warn">填报或复评推进中</span>
                      <span class="lg danger">含未化解高风险</span>
                    </div>
                  </template>
                </div>
              </div>
            </div>

            <!-- 右栏：按企业编辑权限 vs 只读审阅权限精准区分标题与动作 -->
            <div class="posture-col posture-right">
              <div class="p-panel p-panel-flex" data-testid="home-recent">
                <div class="p-panel-head">
                  <h2>{{ editable ? '最近项目与办理进度' : '本单位共享项目审阅' }}</h2>
                  <span class="p-head-links">
                    <RouterLink to="/documents" data-testid="home-recent-all">评估列表</RouterLink>
                    <RouterLink v-if="hasAdmission" to="/admission" data-testid="home-recent-all-admission">准入列表</RouterLink>
                    <RouterLink to="/analysis" data-testid="home-go-consulting">咨询助手</RouterLink>
                  </span>
                </div>
                <div class="p-scroll-list" :class="{ 'is-expanded': recentExpanded }">
                  <div
                    v-for="r in visibleRows"
                    :key="r.key"
                    class="p-proj-row"
                    :class="{ active: r.routeKey && selectedRouteKey === r.routeKey }"
                    data-testid="home-recent-row"
                    @click="focusRowOnMap(r)"
                  >
                    <div class="p-proj-top">
                      <span class="p-mod-tag">{{ r.module }}</span>
                      <RouterLink :to="r.to" class="p-proj-title" @click.stop>{{ r.title }}</RouterLink>
                      <RouterLink :to="r.to" class="p-proj-go" @click.stop>
                        {{ !editable ? '审阅 →' : (r.ok ? '查看 →' : '继续 →') }}
                      </RouterLink>
                    </div>
                    <div class="p-proj-meta">
                      <span class="p-status-chip" :class="r.ok ? 'is-ok' : 'is-pending'" data-testid="home-recent-status">
                        {{ r.short || '进行中' }}
                      </span>
                      <span class="p-proj-sub">{{ r.status || r.code }}</span>
                    </div>
                    <div class="p-proj-bar">
                      <div class="p-proj-bar-fill" :class="{ ok: r.ok }" :style="{ width: `${r.pct}%` }" />
                    </div>
                  </div>
                </div>
                <button
                  v-if="rows.length > FOLD_LIMIT"
                  type="button"
                  class="p-fold-btn"
                  data-testid="home-recent-fold-toggle"
                  @click="recentExpanded = !recentExpanded"
                >
                  {{ recentExpanded ? '收起列表' : `展开其余 ${rows.length - FOLD_LIMIT} 项` }}
                </button>
              </div>

              <div class="p-panel p-panel-flex" data-testid="home-risk-todos">
                <div class="p-panel-head">
                  <h2>{{ editable ? '待处理事项与期限提醒' : '审阅关注点与效期动态（只读）' }}</h2>
                  <span>{{ (overview?.deadlines?.length || 0) + (overview?.todos?.length || 0) }} 项</span>
                </div>
                <div v-if="overview?.deadlines?.length || overview?.todos?.length" class="p-scroll-list" :class="{ 'is-expanded': todosExpanded }">
                  <RouterLink
                    v-for="dl in visibleDeadlines"
                    :key="dl.id"
                    :to="dl.to"
                    class="p-todo-row"
                  >
                    <div class="p-todo-top">
                      <span class="p-todo-badge" :class="dl.tone === 'danger' ? 'danger' : dl.tone === 'warn' ? 'warning' : 'info'">法定效期</span>
                      <strong class="p-todo-status">{{ dl.status }}</strong>
                      <span class="p-proj-go">{{ editable ? '查看 →' : '在线审阅 →' }}</span>
                    </div>
                    <div class="p-todo-title">{{ dl.title }}</div>
                  </RouterLink>

                  <RouterLink
                    v-for="t in visibleTodos"
                    :key="t.id"
                    :to="t.to"
                    class="p-todo-row"
                    data-testid="home-todo-row"
                  >
                    <div class="p-todo-top">
                      <span class="p-todo-badge" :class="t.level">
                        {{ editable ? (TODO_TAG[t.kind] || '待办提醒') : (TODO_TAG_VIEWER[t.kind] || '进展动态') }}
                      </span>
                      <span class="p-proj-go">{{ editable ? `${t.action_label} →` : '在线审阅 →' }}</span>
                    </div>
                    <div class="p-todo-title">{{ t.title }}</div>
                    <div class="p-todo-detail">{{ t.detail }}</div>
                  </RouterLink>
                </div>
                <p v-else class="p-empty p-empty-ok">当前无待处置高风险、超期申报窗口或未读法规变更提醒。</p>
                <button
                  v-if="hiddenTodoCount > 0"
                  type="button"
                  class="p-fold-btn"
                  data-testid="home-todos-fold-toggle"
                  @click="todosExpanded = !todosExpanded"
                >
                  {{ todosExpanded ? '收起待办' : `展开其余 ${hiddenTodoCount} 项` }}
                </button>
              </div>
            </div>
          </div>
        </section>
      </div>

      <nav v-if="!isFullscreen" class="quick-links" aria-label="常用功能" data-testid="home-quick">
        <RouterLink v-for="card in visibleCards" :key="card.path" :to="card.path" class="quick-link">
          <span class="feature-icon"><AppIcon :name="card.icon" /></span>
          <div class="quick-link-text">
            <strong>{{ card.title }}</strong>
            <span>{{ card.sub }}</span>
          </div>
          <AppIcon name="arrow" />
        </RouterLink>
      </nav>
    </template>
  </div>
</template>

<style scoped>
@keyframes routeFlow {
  to {
    stroke-dashoffset: -36;
  }
}

.home-wrap {
  display: flex;
  flex-direction: column;
  gap: 14px;
  font-weight: 400;
}
.home-wrap strong,
.home-wrap b,
.home-wrap h1,
.home-wrap h2 {
  font-weight: 600;
}

/* 唯一旗舰深海态势配色规范（无阴影、无花哨渐变、>=13px、字重仅 400/600） */
.user-board {
  --ub-screen: #071324;
  --ub-panel: #0d1e36;
  --ub-inner: #09172b;
  --ub-border: #1d385c;
  --ub-accent: #38bdf8;
  --ub-link: #60a5fa;
  --ub-emerald: #10b981;
  --ub-emerald-tx: #6ee7b7;
  --ub-gold: #f59e0b;
  --ub-gold-tx: #fde68a;
  --ub-danger: #ef4444;
  --ub-danger-tx: #fca5a5;
  --ub-tx-1: #f8fafc;
  --ub-tx-2: #cbd5e1;
  --ub-tx-muted: #94a3b8;
  --ub-ocean: #060e1c;
  --ub-grid: #102038;
  --ub-land: #15243b;
  --ub-land-stroke: #253b5e;
  --ub-overlay: #1a3b63;
  --ub-active-land: #1d5ca8;
  --ub-lbl-stroke: #060e1c;
}

/* 门户体验访客与空态能力矩阵带 */
.capability-kicker {
  display: inline-block;
  font-size: 13px;
  font-weight: 600;
  color: #1d4ed8;
  background: #eff6ff;
  border: 1px solid #bfdbfe;
  border-radius: 6px;
  padding: 3px 10px;
  margin-bottom: 6px;
}

.capability-strip {
  display: grid;
  grid-template-columns: repeat(5, minmax(0, 1fr));
  gap: 12px;
  width: 100%;
  background: #f8fafc;
  border: 1px solid #e2e8f0;
  border-radius: 10px;
  padding: 14px 16px;
}
.empty-cap-strip {
  grid-template-columns: repeat(4, minmax(0, 1fr));
  max-width: 780px;
}
.cap-item {
  display: flex;
  flex-direction: column;
  gap: 3px;
}
.cap-item strong {
  font-size: 20px;
  color: #0f172a;
}
.cap-item span {
  font-size: 13px;
  color: #475569;
}

/* 角色权限范围显式标签 */
.perm-role-chip {
  font-size: 13px;
  font-weight: 600;
  padding: 3px 10px;
  border-radius: 6px;
  white-space: nowrap;
}
.perm-role-chip.member {
  background: #eff6ff;
  color: #1d4ed8;
  border: 1px solid #bfdbfe;
}
.perm-role-chip.viewer {
  background: #fffbeb;
  color: #b45309;
  border: 1px solid #fde68a;
}
.perm-role-chip.portal {
  background: #09172b;
  color: #fde68a;
  border: 1px solid rgba(245, 158, 11, 0.45);
}

/* 门户体验访客：同款旗舰深海受控看板（仅公开法规覆盖，零企业数据） */
.portal-welcome {
  background: var(--ub-screen);
  border: 1px solid var(--ub-border);
  border-radius: 12px;
  padding: 20px 22px;
  color: var(--ub-tx-1);
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.portal-badge-row {
  margin-bottom: 6px;
}
.portal-head h1 {
  margin: 0 0 6px;
  font-size: 20px;
  color: var(--ub-tx-1);
}
.portal-head p {
  margin: 0;
  font-size: 13px;
  line-height: 1.7;
  color: var(--ub-tx-2);
  max-width: 980px;
}
.kpi-strip.portal-kpi5 {
  grid-template-columns: repeat(5, minmax(0, 1fr));
}
@media (max-width: 1180px) {
  .kpi-strip.portal-kpi5 {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
.portal-map-panel {
  background: var(--ub-panel);
  border: 1px solid var(--ub-border);
  border-radius: 10px;
  padding: 12px 14px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.portal-map-stage {
  min-height: 220px;
}
.portal-map-stage .world-svg {
  max-height: 310px;
}
.portal-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 12px;
}
.portal-card {
  display: flex;
  align-items: flex-start;
  gap: 14px;
  padding: 15px 18px;
  border: 1px solid var(--ub-border);
  border-radius: 10px;
  background: var(--ub-panel);
  color: var(--ub-tx-1);
  text-decoration: none;
}
.portal-card:hover {
  border-color: var(--ub-accent);
}
.portal-card-body {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 5px;
  min-width: 0;
}
.portal-card-body strong {
  font-size: 15px;
  color: var(--ub-tx-1);
}
.portal-card-body span {
  font-size: 13px;
  line-height: 1.55;
  color: var(--ub-tx-2);
}

/* 顶部标题与全屏栏 */
.user-board {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.user-board.is-fullscreen {
  position: fixed;
  inset: 0;
  z-index: 2000;
  padding: 18px 24px;
  overflow-y: auto;
  background: var(--ub-screen);
}
.user-board.is-fullscreen .hero-title-group h1 {
  color: var(--ub-tx-1);
}
.user-board.is-fullscreen .hero-sub {
  color: var(--ub-tx-muted);
}

.hero-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px 16px;
  flex-wrap: wrap;
}
.hero-title-group {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}
.hero-title-group h1 {
  margin: 0;
  font-size: 20px;
  color: #0f172a;
}
.posture-grade-pill {
  font-size: 13px;
  font-weight: 600;
  padding: 3px 10px;
  border-radius: 999px;
  border: 1px solid #a7f3d0;
  background: #ecfdf5;
  color: #047857;
}
.posture-grade-pill.warn {
  border-color: #fde68a;
  background: #fffbeb;
  color: #b45309;
}
.posture-grade-pill.info {
  border-color: #bfdbfe;
  background: #eff6ff;
  color: #1d4ed8;
}
.hero-sub {
  font-size: 13px;
  color: #64748b;
}
.hero-actions {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

.fs-btn {
  border: 1px solid #cbd5e1;
  background: #ffffff;
  color: #1e293b;
  font-size: 13px;
  font-weight: 600;
  padding: 6px 14px;
  border-radius: 6px;
  cursor: pointer;
  white-space: nowrap;
}
.fs-btn:hover {
  border-color: #2563eb;
  color: #1d4ed8;
}
.user-board.is-fullscreen .fs-btn {
  border-color: #334155;
  background: rgba(15, 23, 42, 0.78);
  color: #f8fafc;
}
.user-board.is-fullscreen .fs-btn:hover {
  border-color: #38bdf8;
  color: #38bdf8;
}

.recent-loading {
  min-height: 220px;
  border-radius: 10px;
  background: #ffffff;
  border: 1px solid #e2e8f0;
}

/* 空企业引导卡 */
.empty-org-card {
  background: #ffffff;
  border: 1px solid #e2e8f0;
  border-radius: 12px;
  padding: 28px 30px;
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 12px;
}
.empty-org-card h2 {
  margin: 0;
  font-size: 18px;
  color: #0f172a;
}
.empty-org-card p {
  margin: 0;
  font-size: 14px;
  line-height: 1.7;
  color: #475569;
  max-width: 780px;
}
.viewer-empty {
  margin: 0;
  color: #64748b;
  font-size: 14px;
}

/* 工作概览主看板 */
.posture-screen {
  background: var(--ub-screen);
  border: 1px solid var(--ub-border);
  border-radius: 10px;
  padding: 14px 16px;
  color: var(--ub-tx-1);
  display: flex;
  flex-direction: column;
  gap: 12px;
  font-variant-numeric: tabular-nums;
}

.assurance-ribbon {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 10px 14px;
  flex-wrap: wrap;
  background: var(--ub-panel);
  border: 1px solid var(--ub-border);
  border-left: 4px solid var(--ub-gold);
  border-radius: 8px;
  padding: 9px 14px;
  font-size: 13px;
}
.assure-main {
  color: var(--ub-tx-2);
  line-height: 1.5;
  flex: 1 1 480px;
}
.assure-main strong {
  color: var(--ub-tx-1);
}
.assure-pills {
  display: inline-flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}
.assure-link {
  color: var(--ub-link);
  font-size: 13px;
  font-weight: 600;
  text-decoration: none;
  white-space: nowrap;
}
.assure-link:hover {
  text-decoration: underline;
}
.assure-chip {
  font-size: 13px;
  font-weight: 600;
  color: var(--ub-emerald-tx);
  background: var(--ub-inner);
  border: 1px solid var(--ub-border);
  border-radius: 6px;
  padding: 2px 8px;
  white-space: nowrap;
}
.assure-chip.warn {
  color: var(--ub-gold-tx);
}

.kpi-strip {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 10px;
}
.kpi-tile {
  background: var(--ub-panel);
  border: 1px solid var(--ub-border);
  border-radius: 8px;
  padding: 11px 14px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.kpi-head-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 6px;
}
.kpi-label {
  font-size: 13px;
  color: var(--ub-tx-muted);
}
.kpi-chip {
  font-size: 13px;
  font-weight: 600;
  color: var(--ub-link);
  background: var(--ub-inner);
  border: 1px solid var(--ub-border);
  border-radius: 4px;
  padding: 1px 6px;
  white-space: nowrap;
}
.kpi-chip.safe {
  color: var(--ub-emerald-tx);
}
.kpi-chip.gold {
  color: var(--ub-gold-tx);
}
.kpi-chip.danger {
  color: var(--ub-danger-tx);
}

.kpi-val-row {
  display: flex;
  align-items: baseline;
  gap: 6px;
  flex-wrap: wrap;
}
.kpi-val-row strong {
  font-size: 22px;
  color: var(--ub-tx-1);
}
.kpi-val-row strong.is-danger {
  color: var(--ub-danger-tx);
}
.kpi-val-row strong.is-safe {
  color: var(--ub-emerald-tx);
}
.kpi-unit {
  font-size: 13px;
  color: var(--ub-tx-2);
}
.kpi-sub {
  margin-left: auto;
  font-size: 13px;
  color: var(--ub-tx-muted);
}
.kpi-bar {
  height: 5px;
  background: var(--ub-inner);
  border-radius: 999px;
  overflow: hidden;
}
.kpi-bar-fill {
  height: 100%;
  background: var(--ub-accent);
  border-radius: 999px;
}
.kpi-bar-fill.safe {
  background: var(--ub-emerald);
}
.kpi-bar-fill.gold {
  background: var(--ub-gold);
}
.kpi-bar-fill.danger {
  background: var(--ub-danger);
}

/* 宽屏或全屏时左中右三栏；1280/1440 带左侧菜单栏时自动变为上地图、下双栏，零挤压零截断 */
.posture-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
  align-items: stretch;
}
.posture-center {
  grid-column: 1 / -1;
  order: -1;
}
@media (min-width: 1560px) {
  .posture-grid {
    grid-template-columns: 264px minmax(0, 1fr) 352px;
  }
  .posture-center {
    grid-column: auto;
    order: 0;
  }
}
.user-board.is-fullscreen .posture-grid {
  grid-template-columns: 272px minmax(0, 1fr) 364px;
}
.user-board.is-fullscreen .posture-center {
  grid-column: auto;
  order: 0;
}

.posture-col {
  display: flex;
  flex-direction: column;
  gap: 12px;
  min-width: 0;
}
.p-panel {
  background: var(--ub-panel);
  border: 1px solid var(--ub-border);
  border-radius: 10px;
  padding: 12px 14px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.p-panel-flex {
  flex: 1;
  min-height: 0;
}
.p-panel-head {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  gap: 8px;
  flex-wrap: wrap;
}
.p-panel-head h2 {
  margin: 0;
  font-size: 14px;
  color: var(--ub-tx-1);
}
.p-panel-head span {
  font-size: 13px;
  color: var(--ub-tx-muted);
}
.p-head-links {
  display: inline-flex;
  gap: 10px;
  flex-wrap: wrap;
}
.p-head-links a {
  color: var(--ub-link);
  text-decoration: none;
  font-size: 13px;
  font-weight: 600;
}
.p-head-links a:hover {
  text-decoration: underline;
}
.p-empty {
  margin: 0;
  font-size: 13px;
  color: var(--ub-tx-muted);
  line-height: 1.6;
}
.p-empty-ok {
  color: var(--ub-emerald-tx);
}

.bar-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.bar-row {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.bar-top {
  display: flex;
  justify-content: space-between;
  gap: 8px;
  font-size: 13px;
}
.bar-name {
  color: var(--ub-tx-1);
}
.bar-num {
  color: var(--ub-tx-muted);
  white-space: nowrap;
}
.bar-track {
  height: 6px;
  background: var(--ub-inner);
  border-radius: 999px;
  overflow: hidden;
}
.bar-fill {
  height: 100%;
  background: var(--ub-accent);
  border-radius: 999px;
}

.risk-compare {
  display: flex;
  flex-direction: column;
  gap: 8px;
  font-size: 13px;
}
.rc-card {
  background: var(--ub-inner);
  border: 1px solid var(--ub-border);
  border-radius: 6px;
  padding: 7px 9px;
  display: flex;
  flex-direction: column;
  gap: 5px;
}
.rc-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 8px;
}
.rc-grade {
  font-weight: 600;
}
.rc-grade.danger {
  color: var(--ub-danger-tx);
}
.rc-grade.warn {
  color: var(--ub-gold-tx);
}
.rc-grade.safe {
  color: var(--ub-emerald-tx);
}
.rc-pair {
  color: var(--ub-tx-2);
}
.rc-pair b {
  color: var(--ub-tx-1);
}
.rc-dual-bars {
  display: flex;
  flex-direction: column;
  gap: 3px;
}
.rc-bar {
  height: 4px;
  border-radius: 999px;
}
.rc-bar.init-danger {
  background: var(--ub-danger);
  opacity: 0.55;
}
.rc-bar.init-warn {
  background: var(--ub-gold);
  opacity: 0.55;
}
.rc-bar.cur-safe {
  background: var(--ub-emerald);
}
.rc-bar.cur-cyan {
  background: var(--ub-accent);
}
.rc-foot-boxes {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 8px;
  padding-top: 2px;
}
.rc-stat-box {
  background: var(--ub-inner);
  border: 1px solid var(--ub-border);
  border-radius: 6px;
  padding: 6px 8px;
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.rc-stat-box span {
  font-size: 13px;
  color: var(--ub-tx-muted);
}
.rc-stat-box strong {
  font-size: 14px;
  color: var(--ub-emerald-tx);
}

.map-card {
  flex: 1;
  background: var(--ub-panel);
  border: 1px solid var(--ub-border);
  border-radius: 10px;
  padding: 10px 14px;
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  gap: 8px;
  min-height: 350px;
}
.map-topbar {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 10px;
  flex-wrap: wrap;
}
.map-topbar h2 {
  margin: 0 0 2px;
  font-size: 15px;
  color: var(--ub-tx-1);
}
.map-sub {
  font-size: 13px;
  color: var(--ub-tx-muted);
}
.map-pills {
  display: inline-flex;
  gap: 6px;
  flex-wrap: wrap;
}
.map-pill {
  border: 1px solid var(--ub-border);
  background: var(--ub-inner);
  color: var(--ub-tx-2);
  font-size: 13px;
  padding: 4px 10px;
  border-radius: 999px;
  cursor: pointer;
  white-space: nowrap;
}
.map-pill:hover {
  border-color: var(--ub-accent);
  color: var(--ub-tx-1);
}
.map-pill.active {
  background: var(--ub-accent);
  border-color: var(--ub-accent);
  color: #ffffff;
  font-weight: 600;
}
.map-pill.overlay-pill.active {
  background: var(--ub-emerald);
  border-color: var(--ub-emerald);
}

.map-stage {
  flex: 0 0 auto;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--ub-ocean);
  border: 1px solid var(--ub-border);
  border-radius: 8px;
  overflow: hidden;
  min-height: 236px;
}
.world-svg {
  width: 100%;
  height: auto;
  max-height: 332px;
  display: block;
}
.u-grid-lines {
  stroke: var(--ub-grid);
}
.land-bg path {
  fill: var(--ub-land);
  stroke: var(--ub-land-stroke);
  stroke-width: 0.7;
}
.overlay-country {
  fill: var(--ub-overlay);
  fill-opacity: 0.52;
  stroke: var(--ub-accent);
  stroke-width: 0.65;
}
.active-country {
  fill: var(--ub-active-land);
  fill-opacity: 0.82;
  stroke: var(--ub-accent);
  stroke-width: 1.05;
  cursor: pointer;
  transition: opacity 0.2s, fill 0.2s;
}
.active-country.origin-cn {
  fill: #d97706;
  fill-opacity: 0.86;
  stroke: #fbbf24;
}
.active-country:hover {
  fill-opacity: 0.96;
}
.active-country.dimmed {
  opacity: 0.35;
}
.arc-item {
  cursor: pointer;
  transition: opacity 0.2s;
}
.arc-item.dimmed {
  opacity: 0.22;
}
.arc-base {
  fill: none;
  stroke-width: 2.4;
  stroke-linecap: round;
  opacity: 0.42;
}
.arc-item.selected .arc-base {
  stroke-width: 3.6;
  opacity: 0.86;
}
.arc-dash {
  fill: none;
  stroke: #e0f2fe;
  stroke-width: 1.6;
  stroke-dasharray: 5 5;
  opacity: 0.92;
  animation: routeFlow 2.4s linear infinite;
}
@media (prefers-reduced-motion: reduce) {
  .arc-dash {
    animation: none;
  }
}
.hub-node {
  cursor: pointer;
  transition: opacity 0.2s;
}
.hub-node.dimmed {
  opacity: 0.32;
}
.hub-ring {
  fill: var(--ub-accent);
  fill-opacity: 0.24;
  stroke: var(--ub-accent);
  stroke-width: 1;
}
.hub-ring.gold {
  fill: var(--ub-gold);
  fill-opacity: 0.3;
  stroke: var(--ub-gold);
}
.hub-dot {
  fill: #ffffff;
  stroke: var(--ub-accent);
  stroke-width: 1.2;
}
.hub-dot.gold {
  fill: var(--ub-gold);
  stroke: #ffffff;
}
.hub-label {
  fill: var(--ub-tx-1);
  paint-order: stroke;
  stroke: var(--ub-lbl-stroke);
  stroke-width: 3.2px;
  stroke-linecap: round;
  stroke-linejoin: round;
  pointer-events: none;
}
.hub-label.gold {
  fill: var(--ub-gold-tx);
}

.map-focus-bar {
  background: var(--ub-inner);
  border: 1px solid var(--ub-border);
  border-radius: 8px;
  padding: 9px 12px;
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 10px 14px;
  flex-wrap: wrap;
  font-size: 13px;
}
.mf-main {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
  flex: 1 1 280px;
}
.mf-main strong {
  color: var(--ub-tx-1);
  font-size: 13px;
}
.mf-main span {
  color: var(--ub-tx-2);
  font-size: 13px;
  line-height: 1.5;
}
.mf-actions {
  display: inline-flex;
  align-items: center;
  gap: 10px;
  flex-shrink: 0;
}
.mf-link {
  border: 0;
  background: none;
  color: var(--ub-link);
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
  padding: 0;
}
.mf-link:hover {
  text-decoration: underline;
}
.mf-cta {
  background: #2563eb;
  color: #ffffff;
  text-decoration: none;
  padding: 4px 12px;
  border-radius: 6px;
  font-size: 13px;
  font-weight: 600;
  white-space: nowrap;
}
.mf-cta:hover {
  background: #1d4ed8;
}
.mf-legend {
  display: inline-flex;
  gap: 12px;
  flex-wrap: wrap;
  color: var(--ub-tx-2);
  font-size: 13px;
}
.mf-legend .lg::before {
  content: '';
  display: inline-block;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  margin-right: 5px;
  vertical-align: 1px;
}
.mf-legend .lg.safe::before {
  background: #34d399;
}
.mf-legend .lg.warn::before {
  background: #fbbf24;
}
.mf-legend .lg.danger::before {
  background: #f87171;
}

.p-scroll-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding-right: 2px;
}
.p-scroll-list.is-expanded {
  max-height: 360px;
  overflow-y: auto;
}
.p-proj-row {
  background: var(--ub-inner);
  border: 1px solid var(--ub-border);
  border-radius: 8px;
  padding: 8px 10px;
  display: flex;
  flex-direction: column;
  gap: 6px;
  cursor: pointer;
}
.p-proj-row:hover,
.p-proj-row.active {
  border-color: var(--ub-accent);
}
.p-proj-top {
  display: flex;
  align-items: flex-start;
  gap: 6px;
}
.p-mod-tag {
  font-size: 13px;
  color: var(--ub-link);
  background: var(--ub-panel);
  border: 1px solid var(--ub-border);
  border-radius: 4px;
  padding: 1px 6px;
  white-space: nowrap;
  flex-shrink: 0;
}
.p-proj-title {
  flex: 1;
  min-width: 0;
  color: var(--ub-tx-1);
  font-weight: 600;
  font-size: 13px;
  line-height: 1.45;
  text-decoration: none;
  word-break: break-word;
}
.p-proj-title:hover {
  color: var(--ub-link);
  text-decoration: underline;
}
.p-proj-go {
  color: var(--ub-link);
  font-size: 13px;
  font-weight: 600;
  text-decoration: none;
  white-space: nowrap;
  flex-shrink: 0;
}
.p-proj-go:hover {
  text-decoration: underline;
}
.p-proj-meta {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  font-size: 13px;
  min-width: 0;
}
.p-status-chip {
  font-size: 13px;
  font-weight: 600;
  border-radius: 999px;
  padding: 1px 8px;
  white-space: nowrap;
  flex-shrink: 0;
}
.p-status-chip.is-ok {
  background: rgba(16, 185, 129, 0.16);
  color: var(--ub-emerald-tx);
}
.p-status-chip.is-pending {
  background: rgba(245, 158, 11, 0.16);
  color: var(--ub-gold-tx);
}
.p-proj-sub {
  color: var(--ub-tx-muted);
  line-height: 1.45;
  min-width: 0;
  word-break: break-word;
}
.p-proj-bar {
  height: 4px;
  background: var(--ub-panel);
  border-radius: 999px;
  overflow: hidden;
}
.p-proj-bar-fill {
  height: 100%;
  background: var(--ub-accent);
  border-radius: 999px;
}
.p-proj-bar-fill.ok {
  background: var(--ub-emerald);
}

.p-todo-row {
  background: var(--ub-inner);
  border: 1px solid var(--ub-border);
  border-radius: 8px;
  padding: 8px 10px;
  display: flex;
  flex-direction: column;
  gap: 4px;
  text-decoration: none;
  color: var(--ub-tx-2);
}
.p-todo-row:hover {
  border-color: var(--ub-accent);
}
.p-todo-top {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 8px;
}
.p-todo-badge {
  font-size: 13px;
  font-weight: 600;
  border-radius: 999px;
  padding: 1px 8px;
}
.p-todo-badge.danger {
  background: rgba(239, 68, 68, 0.18);
  color: var(--ub-danger-tx);
}
.p-todo-badge.warning {
  background: rgba(245, 158, 11, 0.18);
  color: var(--ub-gold-tx);
}
.p-todo-badge.info {
  background: rgba(59, 130, 246, 0.18);
  color: var(--ub-link);
}
.p-todo-status {
  color: var(--ub-gold-tx);
  font-size: 13px;
  margin-right: auto;
}
.p-todo-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--ub-tx-1);
  line-height: 1.45;
}
.p-todo-detail {
  font-size: 13px;
  color: var(--ub-tx-muted);
  line-height: 1.45;
}
.p-fold-btn {
  border: 1px solid var(--ub-border);
  background: var(--ub-inner);
  color: var(--ub-link);
  border-radius: 6px;
  padding: 5px 10px;
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
}
.p-fold-btn:hover {
  border-color: var(--ub-accent);
}

.quick-links {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 16px;
}
.quick-link {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 14px 18px;
  background: #ffffff;
  border: 1px solid var(--cy-line);
  border-radius: 10px;
  color: var(--cy-text-1);
  text-decoration: none;
}
.quick-link-text {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}
.quick-link-text strong {
  font-size: 16px;
}
.quick-link-text span {
  font-size: 13px;
  color: #64748b;
}
.quick-link:hover {
  border-color: var(--el-color-primary-light-5);
  color: var(--el-color-primary);
}
.quick-link .feature-icon {
  width: 36px;
  height: 36px;
  margin: 0;
}

@media (max-width: 1240px) {
  .kpi-strip {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
  .capability-strip {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }
}

@media (max-width: 860px) {
  .posture-grid,
  .user-board.is-fullscreen .posture-grid,
  .portal-grid,
  .kpi-strip,
  .quick-links,
  .capability-strip,
  .empty-cap-strip {
    grid-template-columns: 1fr;
  }
}
</style>
