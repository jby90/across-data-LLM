<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import AppIcon from '../components/AppIcon.vue'
import { api } from '../api'
import {
  MAP_VIEWBOX,
  WORLD_COUNTRY_PATHS,
  buildRouteArcPath,
  canEdit,
  COUNTRY_NAMES,
  getCountryHub,
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

const COUNTRIES: Record<string, string> = COUNTRY_NAMES
const FOLD_LIMIT = 5

const isFullscreen = ref(false)
const boardEl = ref<HTMLElement | null>(null)
const selectedRouteKey = ref<string>('ALL')

const recentExpanded = ref(false)
const todosExpanded = ref(false)
const overview = ref<ComplianceOverview | null>(null)
const docItems = ref<DocItem[]>([])
const progress = ref<Record<string, Progress>>({})
const admItems = ref<AdmItem[]>([])
const loaded = ref(false)
const hasAdmission = computed(() => admItems.value.length > 0)

const editable = computed(() => canEdit(auth.user))
const isPortalTrial = computed(() => auth.user?.signup_source === 'portal')

function syncFullscreenState() {
  isFullscreen.value = Boolean(document.fullscreenElement)
}

function onKeydown(e: KeyboardEvent) {
  if (e.key === 'Escape' && isFullscreen.value && !document.fullscreenElement) {
    isFullscreen.value = false
  }
}

async function toggleFullscreen() {
  const next = !isFullscreen.value
  isFullscreen.value = next
  try {
    if (next && !document.fullscreenElement && boardEl.value?.requestFullscreen) {
      await boardEl.value.requestFullscreen()
    } else if (!next && document.fullscreenElement) {
      await document.exitFullscreen()
    }
  } catch {
    /* 容器限制原生全屏时保持视口铺满模式 */
  }
}

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
    return `本次应出 ${p.total} 份，已全部就绪，可下载成稿`
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
      return {
        key: `d-${d.id}`,
        projectId: d.id,
        module: '合规评估',
        title: d.title,
        code: `${madeAt(d.title, d.created_at)} 创建`,
        status: docStatus(p),
        short: st.short,
        ok: st.ok,
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
        at: a.updated_at,
        routeKey: a.country_code ? `ADM:${a.country_code}` : '',
        to: { path: '/admission', query: { project: a.id } },
      }
    }),
  ]
  return combined.sort((x, y) => Number(x.ok) - Number(y.ok) || y.at.localeCompare(x.at))
})

const visibleRows = computed(() => (recentExpanded.value ? rows.value : rows.value.slice(0, FOLD_LIMIT)))
const visibleTodos = computed(() => {
  const list = overview.value?.todos ?? []
  return todosExpanded.value ? list : list.slice(0, FOLD_LIMIT)
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
  for (const r of overview.value?.map_routes || []) {
    const sHub = getCountryHub(r.source_code)
    const tHub = getCountryHub(r.recipient_code)
    if (!sHub || !tHub || r.source_code === r.recipient_code) continue
    const key = `${r.source_code}->${r.recipient_code}`
    let g = groups.get(key)
    if (!g) {
      const d = buildRouteArcPath(r.source_code, r.recipient_code)
      if (!d) continue
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
  }
  return [...groups.values()].sort((a, b) => b.count - a.count)
})

const activeCountryCodes = computed(() => {
  const codes = new Set<string>()
  for (const r of overview.value?.map_routes || []) {
    if (r.source_code && WORLD_COUNTRY_PATHS[r.source_code]) codes.add(r.source_code)
    if (r.recipient_code && WORLD_COUNTRY_PATHS[r.recipient_code]) codes.add(r.recipient_code)
  }
  for (const a of admItems.value) {
    if (a.country_code && WORLD_COUNTRY_PATHS[a.country_code]) codes.add(a.country_code)
  }
  return codes
})

const backgroundMapEntries = computed(() =>
  Object.entries(WORLD_COUNTRY_PATHS).filter(([cc]) => !activeCountryCodes.value.has(cc)),
)

const activeMapEntries = computed(() =>
  [...activeCountryCodes.value].map((cc) => ({
    code: cc,
    d: WORLD_COUNTRY_PATHS[cc] || '',
    hub: getCountryHub(cc),
    name: COUNTRIES[cc] || cc,
  })),
)

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
  return { projTotal, projReady, docsReady, docsReq, curHigh, clearedHigh, admTotal, admReady }
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

async function loadHomeData() {
  try {
    localStorage.removeItem('home-doc-progress-v2')
  } catch {
    /* 忽略浏览器存储限制 */
  }
  if (isPortalTrial.value) {
    loaded.value = true
    return
  }
  try {
    const res = await api.get<ComplianceOverview>('/api/analysis/doc-projects/compliance-overview')
    overview.value = res
    docItems.value = Array.isArray(res.recent_docs) ? res.recent_docs : []
    progress.value = res.recent_progress || {}
    admItems.value = Array.isArray(res.recent_admissions) ? res.recent_admissions : []
  } catch {
    overview.value = null
  } finally {
    loaded.value = true
  }
}

const TODO_TAG: Record<string, string> = {
  high_risk: '高风险待处置',
  risk_stale: '待重新评估',
  law_update: '更新通知',
  admission_pending: '属地准入',
  missing_info: '待补信息',
}

onMounted(() => {
  document.addEventListener('fullscreenchange', syncFullscreenState)
  window.addEventListener('keydown', onKeydown)
  void loadHomeData()
})

onUnmounted(() => {
  document.removeEventListener('fullscreenchange', syncFullscreenState)
  window.removeEventListener('keydown', onKeydown)
})

const visibleCards = [
  { path: '/knowledge', icon: 'book', title: '法规知识库' },
  { path: '/documents', icon: 'shield', title: '合规评估' },
  { path: '/admission', icon: 'flow', title: '海外属地准入' },
]

const portalTrialCards = [
  { path: '/knowledge', icon: 'book', title: '法规知识库', desc: '查阅目标国现行法规双语对照、国家合规简报与汽车数据标准目录' },
  { path: '/analysis', icon: 'shield', title: '咨询助手', desc: '针对跨境流向与合规问题在线提问，回答逐条附现行有效条文依据' },
  { path: '/admission?tab=library', icon: 'flow', title: '海外属地准入事项库', desc: '按目标国家与车载产品类别检索法定准入事项、受理机关与法定申报表' },
]
</script>

<template>
  <div class="home-wrap">
    <!-- 门户体验访客：独立纯净引导视图，不混入企业空间空态 -->
    <section v-if="isPortalTrial" class="portal-welcome" data-testid="home-portal-welcome">
      <div class="portal-head">
        <h1>欢迎体验车研数合汽车跨境数据合规服务平台</h1>
        <p>当前为门户访客体验模式。您可以直接查阅各国汽车数据现行法规与国家合规简报、检索海外属地市场准入事项，或使用咨询助手开展法规问答（提问内容仅本人可见）。</p>
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
      <div ref="boardEl" class="user-board" :class="{ 'is-fullscreen': isFullscreen, 'has-projects': loaded && rows.length > 0 }">
        <header class="hero-head">
          <div class="hero-title-group">
            <h1>欢迎，{{ userName(auth.user) }}</h1>
            <span v-if="loaded && rows.length" class="hero-sub">
              {{ editable ? '本单位跨境数据流向与项目推进实时状态' : '只读查看模式 · 本单位共享项目实时同步' }}
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

        <!-- 空企业状态：无项目时不展示空地图，给出清晰新建引导或只读说明 -->
        <div v-else-if="!rows.length" class="empty-org-card" data-testid="home-recent-empty">
          <template v-if="editable">
            <h2>开始本单位首个汽车数据跨境合规评估</h2>
            <p>选定数据发出国、数据接收国与车载业务场景后，平台将自动研判法定出境路径、筛选必备合规文件清单并同步呈现跨境流向状态。</p>
            <RouterLink to="/documents"><el-button type="primary" data-testid="home-new">新建合规评估项目</el-button></RouterLink>
          </template>
          <p v-else class="viewer-empty">当前暂无本单位共享项目，您可以先查阅法规知识库或使用咨询助手提问查法。</p>
        </div>

        <!-- 工作概览态势主视图 -->
        <section v-else class="posture-screen" data-testid="user-posture-screen">
          <div class="kpi-strip">
            <div class="kpi-tile">
              <span class="kpi-label">在办评估项目</span>
              <div class="kpi-val-row">
                <strong>{{ summaryMetrics.projTotal }}</strong>
                <span class="kpi-unit">个</span>
                <span class="kpi-sub">已齐套 {{ summaryMetrics.projReady }} 个</span>
              </div>
            </div>
            <div class="kpi-tile">
              <span class="kpi-label">必备文件就绪</span>
              <div class="kpi-val-row">
                <strong>{{ summaryMetrics.docsReady }} / {{ summaryMetrics.docsReq }}</strong>
                <span class="kpi-unit">份</span>
                <span class="kpi-sub">{{ summaryMetrics.docsReq ? `完成率 ${Math.round((summaryMetrics.docsReady / summaryMetrics.docsReq) * 100)}%` : '按流向核定' }}</span>
              </div>
            </div>
            <div class="kpi-tile">
              <span class="kpi-label">风险压降闭环</span>
              <div class="kpi-val-row">
                <strong :class="{ 'is-danger': summaryMetrics.curHigh > 0, 'is-safe': summaryMetrics.curHigh === 0 }">{{ summaryMetrics.curHigh }}</strong>
                <span class="kpi-unit">项当前高风险</span>
                <span class="kpi-sub">已化解高风险 {{ summaryMetrics.clearedHigh }} 项</span>
              </div>
            </div>
            <div class="kpi-tile">
              <span class="kpi-label">海外属地准入</span>
              <div class="kpi-val-row">
                <strong>{{ summaryMetrics.admTotal }}</strong>
                <span class="kpi-unit">项</span>
                <span class="kpi-sub">已备齐或获批 {{ summaryMetrics.admReady }} 项</span>
              </div>
            </div>
          </div>

          <div class="posture-grid">
            <!-- 左栏：法定路径与风险前后演变 -->
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
                      <div class="bar-fill" :style="{ width: `${Math.max(10, Math.round((item.count / maxLegalCount) * 100))}%` }" />
                    </div>
                  </div>
                </div>
                <p v-else class="p-empty">暂无评估项目路径分布</p>
              </div>

              <div class="p-panel p-panel-flex">
                <div class="p-panel-head">
                  <h2>风险初评与当前对比</h2>
                  <span>动态复评同步</span>
                </div>
                <div v-if="overview?.risk_summary" class="risk-compare">
                  <div class="rc-row">
                    <span class="rc-grade danger">高风险</span>
                    <span class="rc-pair">初评 {{ overview.risk_summary.init_high }} → <b>当前 {{ overview.risk_summary.current_high }}</b></span>
                  </div>
                  <div class="rc-row">
                    <span class="rc-grade warn">中风险</span>
                    <span class="rc-pair">初评 {{ overview.risk_summary.init_medium }} → <b>当前 {{ overview.risk_summary.current_medium }}</b></span>
                  </div>
                  <div class="rc-row">
                    <span class="rc-grade safe">低风险</span>
                    <span class="rc-pair">初评 {{ overview.risk_summary.init_low }} → <b>当前 {{ overview.risk_summary.current_low }}</b></span>
                  </div>
                  <div class="rc-foot">
                    <span>已降级 {{ overview.risk_summary.downgraded }} 项</span>
                    <span>已完成整改 {{ overview.risk_summary.mitigated }} 项</span>
                  </div>
                </div>
                <p v-else class="p-empty">项目完成风险定级后自动汇总</p>
              </div>
            </div>

            <!-- 中栏：本单位真实跨境流向矢量地图（默认展示本单位全部跨境流向） -->
            <div class="posture-col posture-center">
              <div class="map-card">
                <div class="map-topbar">
                  <div>
                    <h2>本单位跨境数据流向与准入节点</h2>
                    <span class="map-sub">仅点亮本单位在办项目涉及的发出国、接收国与属地准入目标国</span>
                  </div>
                  <div class="map-pills">
                    <button
                      type="button"
                      class="map-pill"
                      :class="{ active: selectedRouteKey === 'ALL' }"
                      data-testid="user-map-reset-all"
                      @click="selectedRouteKey = 'ALL'"
                    >
                      全部流向（{{ arcGroups.length }}）
                    </button>
                  </div>
                </div>

                <div class="map-stage">
                  <svg :viewBox="MAP_VIEWBOX" class="world-svg" role="img" aria-label="本单位跨境数据流向图">
                    <g class="land-bg">
                      <path v-for="[cc, d] in backgroundMapEntries" :key="`bg-${cc}`" :d="d" />
                    </g>

                    <g class="land-active">
                      <path
                        v-for="item in activeMapEntries"
                        :key="`act-${item.code}`"
                        :d="item.d"
                        class="active-country"
                        :class="{ dimmed: !isCountryHighlighted(item.code) }"
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
                          :stroke="arc.tone === 'danger' ? '#f87171' : arc.tone === 'warn' ? '#fbbf24' : '#38bdf8'"
                        />
                        <path :d="arc.path" class="arc-dash" />
                      </g>
                    </g>

                    <g class="country-hubs">
                      <g
                        v-for="item in activeMapEntries"
                        :key="`hub-${item.code}`"
                        class="hub-node"
                        :class="{ dimmed: !isCountryHighlighted(item.code) }"
                        @click="selectCountryOnMap(item.code)"
                      >
                        <template v-if="item.hub">
                          <circle :cx="item.hub[0]" :cy="item.hub[1]" r="7" class="hub-ring" />
                          <circle :cx="item.hub[0]" :cy="item.hub[1]" r="3.4" class="hub-dot" />
                          <text :x="item.hub[0] + 8" :y="item.hub[1] + 4" class="hub-label">{{ item.name }}</text>
                        </template>
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
                      <RouterLink to="/admission" class="mf-cta">{{ editable ? '前往办理 →' : '查看准入 →' }}</RouterLink>
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

            <!-- 右栏：最近项目与办理进度（上） + 待处理事项与期限提醒（下） -->
            <div class="posture-col posture-right">
              <div class="p-panel p-panel-flex" data-testid="home-recent">
                <div class="p-panel-head">
                  <h2>最近项目与办理进度</h2>
                  <span class="p-head-links">
                    <RouterLink to="/documents" data-testid="home-recent-all">评估列表</RouterLink>
                    <RouterLink v-if="hasAdmission" to="/admission" data-testid="home-recent-all-admission">准入列表</RouterLink>
                    <RouterLink to="/analysis" data-testid="home-go-consulting">咨询助手</RouterLink>
                  </span>
                </div>
                <div class="p-scroll-list">
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
                        {{ (!editable || r.ok) ? '查看 →' : '继续 →' }}
                      </RouterLink>
                    </div>
                    <div class="p-proj-meta">
                      <span class="p-status-chip" :class="r.ok ? 'is-ok' : 'is-pending'" data-testid="home-recent-status">
                        {{ r.short || '进行中' }}
                      </span>
                      <span class="p-proj-sub">{{ r.status || r.code }}</span>
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
                  <h2>待处理事项与期限提醒</h2>
                  <span>{{ (overview?.deadlines?.length || 0) + (overview?.todos?.length || 0) }} 项</span>
                </div>
                <div v-if="overview?.deadlines?.length || overview?.todos?.length" class="p-scroll-list">
                  <RouterLink
                    v-for="dl in overview?.deadlines || []"
                    :key="dl.id"
                    :to="dl.to"
                    class="p-todo-row"
                  >
                    <div class="p-todo-top">
                      <span class="p-todo-badge" :class="dl.tone === 'danger' ? 'danger' : dl.tone === 'warn' ? 'warning' : 'info'">法定效期</span>
                      <strong class="p-todo-status">{{ dl.status }}</strong>
                      <span class="p-proj-go">查看 →</span>
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
                      <span class="p-todo-badge" :class="t.level">{{ TODO_TAG[t.kind] || '待办提醒' }}</span>
                      <span class="p-proj-go">{{ editable ? `${t.action_label} →` : '查看详情 →' }}</span>
                    </div>
                    <div class="p-todo-title">{{ t.title }}</div>
                    <div class="p-todo-detail">{{ t.detail }}</div>
                  </RouterLink>
                </div>
                <p v-else class="p-empty p-empty-ok">当前无待处置高风险、超期申报窗口或未读法规变更提醒。</p>
                <button
                  v-if="(overview?.todos?.length || 0) > FOLD_LIMIT"
                  type="button"
                  class="p-fold-btn"
                  data-testid="home-todos-fold-toggle"
                  @click="todosExpanded = !todosExpanded"
                >
                  {{ todosExpanded ? '收起待办' : `展开其余 ${(overview?.todos?.length || 0) - FOLD_LIMIT} 项` }}
                </button>
              </div>
            </div>
          </div>
        </section>
      </div>

      <nav v-if="!isFullscreen" class="quick-links" aria-label="常用功能" data-testid="home-quick">
        <RouterLink v-for="card in visibleCards" :key="card.path" :to="card.path" class="quick-link">
          <span class="feature-icon"><AppIcon :name="card.icon" /></span>
          <strong>{{ card.title }}</strong>
          <AppIcon name="arrow" />
        </RouterLink>
      </nav>
    </template>
  </div>
</template>

<style scoped>
.home-wrap { display: flex; flex-direction: column; gap: 14px; }
.home-wrap strong, .home-wrap b, .home-wrap h1, .home-wrap h2 { font-weight: 600; }

/* 门户体验访客引导页 */
.portal-welcome { background: #fff; border: 1px solid #e2e8f0; border-radius: 12px; padding: 24px 28px; display: flex; flex-direction: column; gap: 18px; }
.portal-head h1 { margin: 0 0 8px; font-size: 20px; color: #0f172a; }
.portal-head p { margin: 0; font-size: 14px; line-height: 1.75; color: #475569; max-width: 880px; }
.portal-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 16px; }
.portal-card { display: flex; align-items: flex-start; gap: 14px; padding: 18px 20px; border: 1px solid #e2e8f0; border-radius: 10px; background: #f8fafc; color: #0f172a; text-decoration: none; }
.portal-card:hover { border-color: #2563eb; background: #eff6ff; }
.portal-card-body { flex: 1; display: flex; flex-direction: column; gap: 6px; min-width: 0; }
.portal-card-body strong { font-size: 16px; color: #0f172a; }
.portal-card-body span { font-size: 13px; line-height: 1.6; color: #475569; }

/* 顶部标题与全屏栏 */
.user-board { display: flex; flex-direction: column; gap: 12px; }
.user-board.is-fullscreen { position: fixed; inset: 0; z-index: 2000; padding: 18px 24px; overflow-y: auto; background: #071120; }
.user-board.is-fullscreen .hero-title-group h1 { color: #f8fafc; }
.user-board.is-fullscreen .hero-sub { color: #94a3b8; }

.hero-head { display: flex; align-items: center; justify-content: space-between; gap: 12px 16px; flex-wrap: wrap; }
.hero-title-group { display: flex; align-items: baseline; gap: 12px; flex-wrap: wrap; }
.hero-title-group h1 { margin: 0; font-size: 20px; color: #0f172a; }
.hero-sub { font-size: 13px; color: #64748b; }
.hero-actions { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }

.fs-btn { border: 1px solid #cbd5e1; background: #fff; color: #1e293b; font-size: 13px; font-weight: 600; padding: 6px 14px; border-radius: 6px; cursor: pointer; white-space: nowrap; }
.fs-btn:hover { border-color: #2563eb; color: #1d4ed8; }

.recent-loading { min-height: 220px; border-radius: 10px; background: #ffffff; border: 1px solid #e2e8f0; }

/* 空企业引导卡 */
.empty-org-card { background: #fff; border: 1px solid #e2e8f0; border-radius: 12px; padding: 28px 30px; display: flex; flex-direction: column; align-items: flex-start; gap: 12px; }
.empty-org-card h2 { margin: 0; font-size: 18px; color: #0f172a; }
.empty-org-card p { margin: 0; font-size: 14px; line-height: 1.7; color: #475569; max-width: 760px; }
.viewer-empty { margin: 0; color: #64748b; font-size: 14px; }

/* 工作概览主看板 */
.posture-screen { background: #081426; border: 1px solid #1e3a5f; border-radius: 10px; padding: 14px 16px; color: #e2e8f0; display: flex; flex-direction: column; gap: 12px; font-variant-numeric: tabular-nums; }
.kpi-strip { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 10px; }
.kpi-tile { background: #0f213a; border: 1px solid #1e3a5f; border-radius: 8px; padding: 10px 14px; display: flex; flex-direction: column; gap: 4px; }
.kpi-label { font-size: 13px; color: #94a3b8; }
.kpi-val-row { display: flex; align-items: baseline; gap: 6px; flex-wrap: wrap; }
.kpi-val-row strong { font-size: 22px; color: #f8fafc; }
.kpi-val-row strong.is-danger { color: #f87171; }
.kpi-val-row strong.is-safe { color: #34d399; }
.kpi-unit { font-size: 13px; color: #cbd5e1; }
.kpi-sub { margin-left: auto; font-size: 13px; color: #94a3b8; }

/* 宽屏或全屏时左中右三栏；1280/1440 带左侧菜单栏时自动变为上地图、下双栏，零挤压零截断 */
.posture-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; align-items: stretch; }
.posture-center { grid-column: 1 / -1; order: -1; }
@media (min-width: 1560px) {
  .posture-grid { grid-template-columns: 256px minmax(0, 1fr) 348px; }
  .posture-center { grid-column: auto; order: 0; }
}
.user-board.is-fullscreen .posture-grid { grid-template-columns: 264px minmax(0, 1fr) 360px; }
.user-board.is-fullscreen .posture-center { grid-column: auto; order: 0; }

.posture-col { display: flex; flex-direction: column; gap: 12px; min-width: 0; }
.p-panel { background: #0f213a; border: 1px solid #1e3a5f; border-radius: 10px; padding: 12px 14px; display: flex; flex-direction: column; gap: 10px; }
.p-panel-flex { flex: 1; min-height: 0; }
.p-panel-head { display: flex; justify-content: space-between; align-items: baseline; gap: 8px; flex-wrap: wrap; }
.p-panel-head h2 { margin: 0; font-size: 14px; color: #f8fafc; }
.p-panel-head span { font-size: 13px; color: #94a3b8; }
.p-head-links { display: inline-flex; gap: 10px; flex-wrap: wrap; }
.p-head-links a { color: #60a5fa; text-decoration: none; font-size: 13px; }
.p-head-links a:hover { text-decoration: underline; }
.p-empty { margin: 0; font-size: 13px; color: #94a3b8; line-height: 1.6; }
.p-empty-ok { color: #6ee7b7; }

.bar-list { display: flex; flex-direction: column; gap: 10px; }
.bar-row { display: flex; flex-direction: column; gap: 4px; }
.bar-top { display: flex; justify-content: space-between; gap: 8px; font-size: 13px; }
.bar-name { color: #e2e8f0; }
.bar-num { color: #94a3b8; white-space: nowrap; }
.bar-track { height: 6px; background: #172e4d; border-radius: 999px; overflow: hidden; }
.bar-fill { height: 100%; background: #38bdf8; border-radius: 999px; }

.risk-compare { display: flex; flex-direction: column; gap: 8px; font-size: 13px; }
.rc-row { display: flex; justify-content: space-between; align-items: center; gap: 8px; padding: 6px 8px; background: #0a182c; border-radius: 6px; }
.rc-grade { font-weight: 600; }
.rc-grade.danger { color: #fca5a5; }
.rc-grade.warn { color: #fde68a; }
.rc-grade.safe { color: #6ee7b7; }
.rc-pair { color: #cbd5e1; }
.rc-pair b { color: #f8fafc; }
.rc-foot { display: flex; justify-content: space-between; gap: 8px; color: #94a3b8; font-size: 13px; padding-top: 2px; }

.map-card { flex: 1; background: #0a182c; border: 1px solid #1e3a5f; border-radius: 10px; padding: 12px 14px; display: flex; flex-direction: column; gap: 10px; min-height: 400px; }
.map-topbar { display: flex; justify-content: space-between; align-items: flex-start; gap: 10px; flex-wrap: wrap; }
.map-topbar h2 { margin: 0 0 2px; font-size: 15px; color: #f8fafc; }
.map-sub { font-size: 13px; color: #94a3b8; }
.map-pills { display: inline-flex; gap: 6px; }
.map-pill { border: 1px solid #294d78; background: #102542; color: #cbd5e1; font-size: 13px; padding: 4px 10px; border-radius: 999px; cursor: pointer; }
.map-pill.active { background: #1d4ed8; border-color: #3b82f6; color: #fff; font-weight: 600; }

.map-stage { flex: 1; display: flex; align-items: center; justify-content: center; background: #071222; border-radius: 8px; overflow: hidden; min-height: 270px; }
.world-svg { width: 100%; height: auto; max-height: 360px; display: block; }
.land-bg path { fill: #12243d; stroke: #1c3557; stroke-width: 0.55; }
.active-country { fill: #1e4b7a; stroke: #60a5fa; stroke-width: 0.9; cursor: pointer; transition: opacity 0.2s, fill 0.2s; }
.active-country:hover { fill: #2563eb; }
.active-country.dimmed { opacity: 0.35; }
.arc-item { cursor: pointer; transition: opacity 0.2s; }
.arc-item.dimmed { opacity: 0.22; }
.arc-base { fill: none; stroke-width: 2.2; stroke-linecap: round; }
.arc-item.selected .arc-base { stroke-width: 3.4; }
.arc-dash { fill: none; stroke: #f8fafc; stroke-width: 1.2; stroke-dasharray: 4 7; opacity: 0.7; }
.hub-node { cursor: pointer; transition: opacity 0.2s; }
.hub-node.dimmed { opacity: 0.32; }
.hub-ring { fill: rgba(56, 189, 248, 0.22); stroke: #38bdf8; stroke-width: 1; }
.hub-dot { fill: #f8fafc; }
.hub-label { fill: #f8fafc; font-size: 13px; font-weight: 600; paint-order: stroke; stroke: #071222; stroke-width: 3px; }

.map-focus-bar { background: #0f213a; border: 1px solid #1e3a5f; border-radius: 8px; padding: 9px 12px; display: flex; justify-content: space-between; align-items: center; gap: 10px 14px; flex-wrap: wrap; font-size: 13px; }
.mf-main { display: flex; flex-direction: column; gap: 2px; min-width: 0; flex: 1 1 280px; }
.mf-main strong { color: #f8fafc; font-size: 13px; }
.mf-main span { color: #cbd5e1; font-size: 13px; line-height: 1.5; }
.mf-actions { display: inline-flex; align-items: center; gap: 10px; flex-shrink: 0; }
.mf-link { border: 0; background: none; color: #93c5fd; font-size: 13px; cursor: pointer; padding: 0; }
.mf-link:hover { text-decoration: underline; }
.mf-cta { background: #2563eb; color: #fff; text-decoration: none; padding: 4px 12px; border-radius: 6px; font-size: 13px; font-weight: 600; white-space: nowrap; }
.mf-cta:hover { background: #1d4ed8; }
.mf-legend { display: inline-flex; gap: 12px; flex-wrap: wrap; color: #cbd5e1; font-size: 13px; }
.mf-legend .lg::before { content: ''; display: inline-block; width: 8px; height: 8px; border-radius: 50%; margin-right: 5px; vertical-align: 1px; }
.mf-legend .lg.safe::before { background: #34d399; }
.mf-legend .lg.warn::before { background: #fbbf24; }
.mf-legend .lg.danger::before { background: #f87171; }

.p-scroll-list { display: flex; flex-direction: column; gap: 8px; max-height: 240px; overflow-y: auto; padding-right: 2px; }
.p-proj-row { background: #0a182c; border: 1px solid #1b3556; border-radius: 8px; padding: 8px 10px; display: flex; flex-direction: column; gap: 5px; cursor: pointer; }
.p-proj-row:hover, .p-proj-row.active { border-color: #38bdf8; background: #102542; }
.p-proj-top { display: flex; align-items: flex-start; gap: 6px; }
.p-mod-tag { font-size: 13px; color: #93c5fd; background: #172e4d; border-radius: 4px; padding: 1px 6px; white-space: nowrap; flex-shrink: 0; }
.p-proj-title { flex: 1; min-width: 0; color: #f8fafc; font-weight: 600; font-size: 13px; line-height: 1.45; text-decoration: none; word-break: break-word; }
.p-proj-title:hover { color: #60a5fa; text-decoration: underline; }
.p-proj-go { color: #60a5fa; font-size: 13px; text-decoration: none; white-space: nowrap; flex-shrink: 0; }
.p-proj-go:hover { text-decoration: underline; }
.p-proj-meta { display: flex; align-items: flex-start; gap: 8px; font-size: 13px; min-width: 0; }
.p-status-chip { font-size: 13px; font-weight: 600; border-radius: 999px; padding: 1px 8px; white-space: nowrap; flex-shrink: 0; }
.p-status-chip.is-ok { background: rgba(16, 185, 129, 0.18); color: #6ee7b7; }
.p-status-chip.is-pending { background: rgba(245, 158, 11, 0.2); color: #fde68a; }
.p-proj-sub { color: #94a3b8; line-height: 1.45; min-width: 0; word-break: break-word; }

.p-todo-row { background: #0a182c; border: 1px solid #1b3556; border-radius: 8px; padding: 8px 10px; display: flex; flex-direction: column; gap: 4px; text-decoration: none; color: #e2e8f0; }
.p-todo-row:hover { border-color: #38bdf8; }
.p-todo-top { display: flex; justify-content: space-between; align-items: center; gap: 8px; }
.p-todo-badge { font-size: 13px; font-weight: 600; border-radius: 999px; padding: 1px 8px; }
.p-todo-badge.danger { background: rgba(239, 68, 68, 0.22); color: #fca5a5; }
.p-todo-badge.warning { background: rgba(245, 158, 11, 0.22); color: #fde68a; }
.p-todo-badge.info { background: rgba(59, 130, 246, 0.22); color: #93c5fd; }
.p-todo-status { color: #fde68a; font-size: 13px; margin-right: auto; }
.p-todo-title { font-size: 13px; font-weight: 600; color: #f8fafc; line-height: 1.45; }
.p-todo-detail { font-size: 13px; color: #94a3b8; line-height: 1.45; }
.p-fold-btn { border: 1px solid #23436b; background: #0a182c; color: #93c5fd; border-radius: 6px; padding: 5px 10px; font-size: 13px; cursor: pointer; }
.p-fold-btn:hover { border-color: #38bdf8; }

.quick-links { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 16px; }
.quick-link { display: flex; align-items: center; gap: 12px; padding: 14px 18px; background: #fff; border: 1px solid var(--cy-line); border-radius: 10px; color: var(--cy-text-1); text-decoration: none; }
.quick-link strong { flex: 1; font-size: 16px; }
.quick-link:hover { border-color: var(--el-color-primary-light-5); color: var(--el-color-primary); }
.quick-link .feature-icon { width: 36px; height: 36px; margin: 0; }

@media (max-width: 1240px) {
  .kpi-strip { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}

@media (max-width: 860px) {
  .posture-grid, .portal-grid, .kpi-strip, .quick-links { grid-template-columns: 1fr; }
}
</style>
