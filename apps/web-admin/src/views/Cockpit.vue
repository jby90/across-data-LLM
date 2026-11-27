<script setup lang="ts">
import { MAP_VIEWBOX, WORLD_COUNTRY_PATHS, buildRouteArcPath, getCountryHub, getCountryLabelOffset } from '@cyshuhe/shared'
import { ElMessage } from 'element-plus'
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter, type RouteLocationRaw } from 'vue-router'
import { api } from '../api'

const props = withDefaults(defineProps<{ opsMode?: boolean }>(), {
  opsMode: false,
})

interface CountryPosture {
  country_code: string
  country_name_zh: string
  is_origin_country?: boolean
  laws_count: number
  clauses_count: number
  is_zh_native: boolean
  foreign_clauses_count: number
  translated_clauses_count: number
  approved_clauses_count: number
  translation_pct: number | null
  standards_count: number
  kg_nodes_count: number
  kg_edges_count: number
  briefing_ready: boolean
  briefing_updated_at: string | null
  admission_entries_count: number
  official_forms_count: number
  monitor_sites_count: number
  attention_sites_count: number
  enterprise_projects_count: number
}

interface CockpitRouteItem {
  route_label: string
  project_count: number
  source_code?: string
  recipient_code?: string
}

interface CockpitOverview {
  generated_at: string
  headline: {
    covered_jurisdictions: number
    laws_total?: number
    laws_archived_total?: number
    laws_in_force: number
    clauses_total: number
    foreign_clauses_total: number
    translated_clauses_total: number
    translation_coverage_pct: number | null
    standards_active: number
    standards_cn?: number
    standards_intl?: number
    standards_international?: number
    national_standards_count?: number
    intl_standards_count?: number
    kg_nodes_total: number
    kg_edges_total: number
    briefings_ready: number
    whitepapers_outdated?: number
    admission_entries_total: number
    official_forms_total: number
    official_forms_eu?: number
    active_organizations: number
    doc_projects_total: number
    admission_projects_total: number
    consulting_chats_total: number
    opinion_items_total: number
  }
  kg_node_breakdown: Array<{ label: string; count: number }>
  account_summary: {
    super_admins: number
    ops_admins: number
    enterprise_members: number
    ordinary_viewers: number
    portal_onboarded: number
    trial_visitors?: number
  }
  operations: {
    monitor_sites_total: number
    attention_sites_total: number
    pending_updates: number
    quality_unpassed: number
    whitepapers_outdated?: number
    audit_events_7d: number
  }
  routes: CockpitRouteItem[]
  countries: CountryPosture[]
}

type CameraKey = 'WORLD' | 'EUROPE' | 'ASIA' | 'MENA' | 'AMER'
type DeckTheme = 'command' | 'ministry'
type DeckFocus = 'foundation' | 'governance'

const CAMERA_PRESETS: Record<CameraKey, { label: string; s: number; cx: number; cy: number }> = {
  WORLD: { label: '全球全景', s: 1, cx: 480, cy: 220 },
  EUROPE: { label: '欧洲法域', s: 3.25, cx: 502, cy: 92 },
  ASIA: { label: '亚太与东盟', s: 2.32, cx: 754, cy: 172 },
  MENA: { label: '中东与非洲', s: 2.42, cx: 528, cy: 186 },
  AMER: { label: '美洲与澳新', s: 1.24, cx: 245, cy: 222 },
}

const REGION_CODES: Record<Exclude<CameraKey, 'WORLD'>, Set<string>> = {
  EUROPE: new Set([
    'EU', 'DE', 'FR', 'IT', 'ES', 'NL', 'SE', 'PL', 'PT', 'BE', 'AT', 'IE', 'DK', 'FI',
    'NO', 'CH', 'GB', 'CZ', 'HU', 'RO', 'GR', 'SK', 'SI', 'BG', 'HR', 'LT', 'LV', 'EE',
    'LU', 'RU', 'TR', 'UA', 'BY', 'RS',
  ]),
  ASIA: new Set([
    'CN', 'SG', 'JP', 'KR', 'TH', 'MY', 'ID', 'VN', 'PH', 'IN', 'PK', 'KZ', 'UZ', 'BD', 'LK', 'KH', 'BN',
  ]),
  MENA: new Set([
    'MA', 'TN', 'EG', 'LB', 'AE', 'SA', 'QA', 'KW', 'OM', 'BH', 'IL', 'JO', 'DZ', 'NG', 'ZA', 'KE', 'ET',
  ]),
  AMER: new Set(['US', 'CA', 'MX', 'BR', 'AR', 'CL', 'CO', 'PE', 'UY', 'AU', 'NZ']),
}

const ANCHOR_LABEL_CODES = new Set([
  'CN', 'SG', 'EU', 'DE', 'FR', 'MA', 'TN', 'EG', 'SA', 'AE', 'JP', 'US', 'CA', 'MX', 'BR', 'AR', 'AU', 'ZA', 'RU', 'IN', 'TH',
])

const THEME_STORAGE_KEY = 'cyshuhe.admin.cockpit.theme'

const route = useRoute()
const router = useRouter()
const boardRef = ref<HTMLElement | null>(null)
const loading = ref(false)
const selectedGeoCode = ref<string>('ALL')
const activeCamera = ref<CameraKey>('WORLD')
const deckFocus = ref<DeckFocus>('foundation')
const deckTheme = ref<DeckTheme>('command')
const isFullscreen = ref(!props.opsMode && route.query.fullscreen === '1')
const data = ref<CockpitOverview | null>(null)

const camScale = ref(1)
const camTx = ref(0)
const camTy = ref(0)
let camAnimId: number | null = null

function readInitialTheme(): DeckTheme {
  try {
    const saved = localStorage.getItem(THEME_STORAGE_KEY)
    if (saved === 'ministry' || saved === 'command') return saved
  } catch {
    /* 忽略浏览器存储限制 */
  }
  return 'command'
}

function setTheme(next: DeckTheme) {
  deckTheme.value = next
  try {
    localStorage.setItem(THEME_STORAGE_KEY, next)
  } catch {
    /* 忽略浏览器存储限制 */
  }
}

function setCamera(cam: CameraKey) {
  activeCamera.value = cam
  const preset = CAMERA_PRESETS[cam]
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
  const duration = 320

  const step = (now: number) => {
    const p = Math.min(1, (now - t0) / duration)
    const ease = 1 - Math.pow(1 - p, 3)
    camScale.value = startS + (targetS - startS) * ease
    camTx.value = startTx + (targetTx - startTx) * ease
    camTy.value = startTy + (targetTy - startTy) * ease
    if (p < 1) {
      camAnimId = requestAnimationFrame(step)
    } else {
      camAnimId = null
    }
  }
  camAnimId = requestAnimationFrame(step)
}

const cameraTransform = computed(
  () => `translate(${camTx.value.toFixed(2)} ${camTy.value.toFixed(2)}) scale(${camScale.value.toFixed(4)})`,
)

async function load() {
  loading.value = true
  try {
    const endpoint = props.opsMode ? '/admin/knowledge/posture' : '/admin/cockpit/overview'
    data.value = await api.get<CockpitOverview>(endpoint)
  } catch (e) {
    ElMessage.error((e as Error).message)
  } finally {
    loading.value = false
  }
}

function onFullscreenKeydown(e: KeyboardEvent) {
  if (e.key === 'Escape' && isFullscreen.value) {
    isFullscreen.value = false
  }
}

function toggleFullscreen() {
  isFullscreen.value = !isFullscreen.value
}

const sovereignCountries = computed(() =>
  (data.value?.countries || []).filter((c) => c.country_code !== 'ZZ' && c.country_code !== 'INTL'),
)

const coveredCodeSet = computed(() => new Set(sovereignCountries.value.map((c) => c.country_code)))

const backgroundLandEntries = computed(() =>
  Object.entries(WORLD_COUNTRY_PATHS).filter(([cc]) => !coveredCodeSet.value.has(cc)),
)

function countryTierClass(c: CountryPosture): string {
  if (c.country_code === 'CN') return 'tier-cn'
  if (c.laws_count >= 25) return 'tier-high'
  if (c.laws_count >= 8) return 'tier-mid'
  return 'tier-base'
}

const coveredLandEntries = computed(() =>
  sovereignCountries.value
    .map((c) => ({
      code: c.country_code,
      d: WORLD_COUNTRY_PATHS[c.country_code] || '',
      country: c,
      tierClass: countryTierClass(c),
    }))
    .filter((x) => Boolean(x.d)),
)

const mapArcs = computed(() => {
  const list = data.value?.routes || []
  const out: Array<{ key: string; d: string; fromCn: boolean; active: boolean }> = []
  list.forEach((rt, idx) => {
    const sc = rt.source_code || ''
    const dc = rt.recipient_code || ''
    const d = buildRouteArcPath(sc, dc, idx)
    if (d) {
      const active = selectedGeoCode.value === 'ALL' || selectedGeoCode.value === sc || selectedGeoCode.value === dc
      out.push({ key: `${sc}-${dc}-${idx}`, d, fromCn: sc === 'CN', active })
    }
  })
  return out
})

const mapHubMarkers = computed(() => {
  const s = camScale.value
  const tx = camTx.value
  const ty = camTy.value
  const zoomed = activeCamera.value !== 'WORLD'
  const dense = sovereignCountries.value.length > 12
  const routeCodes = new Set<string>()
  for (const rt of data.value?.routes || []) {
    if (rt.source_code) routeCodes.add(rt.source_code)
    if (rt.recipient_code) routeCodes.add(rt.recipient_code)
  }

  const placedLabels: Array<{ x: number; y: number }> = []
  return sovereignCountries.value
    .map((c) => {
      const hub = getCountryHub(c.country_code)
      if (!hub) return null
      const sx = hub[0] * s + tx
      const sy = hub[1] * s + ty
      if (sx < 14 || sx > 946 || sy < 14 || sy > 426) return null

      const baseOffset = getCountryLabelOffset(c.country_code, hub)
      const dx = baseOffset.x - hub[0]
      const dy = baseOffset.y - hub[1]
      const lx = Math.max(24, Math.min(936, sx + dx))
      const ly = Math.max(20, Math.min(424, sy + dy))

      const isSelected = selectedGeoCode.value === c.country_code
      const isCn = c.country_code === 'CN'
      const inRegion = activeCamera.value === 'WORLD' || REGION_CODES[activeCamera.value].has(c.country_code)
      let showLabel =
        isSelected ||
        isCn ||
        (zoomed && inRegion) ||
        !dense ||
        ANCHOR_LABEL_CODES.has(c.country_code) ||
        routeCodes.has(c.country_code)

      if (showLabel && !isSelected && !isCn) {
        const collides = placedLabels.some((p) => Math.abs(p.x - lx) < 82 && Math.abs(p.y - ly) < 17)
        if (collides) showLabel = false
      }
      if (showLabel) placedLabels.push({ x: lx, y: ly })

      const labelText = isCn
        ? c.standards_count > 0
          ? `★中国枢纽 (${c.laws_count}部+${c.standards_count}国标)`
          : `★中国枢纽 (${c.laws_count}部)`
        : `${c.country_name_zh} (${c.laws_count}部)`

      return {
        code: c.country_code,
        name: c.country_name_zh,
        labelText,
        laws: c.laws_count,
        isCn,
        isSelected,
        isMajor: isCn || isSelected || c.laws_count >= 25,
        sx,
        sy,
        lx,
        ly,
        anchor: baseOffset.anchor,
        showLabel,
      }
    })
    .filter((x): x is NonNullable<typeof x> => x !== null)
})

const selectedCountry = computed(() => {
  if (selectedGeoCode.value === 'ALL') return null
  return sovereignCountries.value.find((c) => c.country_code === selectedGeoCode.value) || null
})

const filteredRankingCountries = computed(() => {
  const cam = activeCamera.value
  const pool =
    cam === 'WORLD'
      ? sovereignCountries.value
      : sovereignCountries.value.filter((c) => REGION_CODES[cam].has(c.country_code))
  return [...pool].sort(
    (a, b) => b.laws_count * 100000 + b.clauses_count - (a.laws_count * 100000 + a.clauses_count),
  )
})

const sortedKgBreakdown = computed(() =>
  [...(data.value?.kg_node_breakdown || [])].sort((a, b) => b.count - a.count),
)

const radarGeometry = computed(() => {
  const raw = data.value?.kg_node_breakdown || []
  const axes = raw.length === 6 ? raw : sortedKgBreakdown.value.slice(0, 6)
  if (axes.length < 3) return null
  const cx = 112
  const cy = 94
  const R = 62
  const n = axes.length
  const maxLog = Math.log10(Math.max(10, ...axes.map((x) => x.count + 1)))

  const pt = (i: number, ratio: number): [number, number] => {
    const ang = -Math.PI / 2 + (i * 2 * Math.PI) / n
    return [cx + R * ratio * Math.cos(ang), cy + R * ratio * Math.sin(ang)]
  }

  const rings = [0.34, 0.67, 1].map((r) =>
    axes
      .map((_, i) => pt(i, r).map((v) => v.toFixed(1)).join(','))
      .join(' '),
  )
  const spokes = axes.map((_, i) => {
    const [x, y] = pt(i, 1)
    return { x1: cx, y1: cy, x2: x.toFixed(1), y2: y.toFixed(1) }
  })
  const points = axes.map((a, i) => {
    const ratio = a.count > 0 ? Math.max(0.22, Math.log10(a.count + 1) / maxLog) : 0.12
    const [x, y] = pt(i, ratio)
    const [lx, ly] = pt(i, 1.27)
    const anchor = i === 0 || (n % 2 === 0 && i === n / 2) ? 'middle' : i < n / 2 ? 'start' : 'end'
    const shortLabel = a.label.replace(/节点/g, '').replace('国家与地区', '覆盖法域')
    return {
      label: a.label,
      shortLabel,
      count: a.count,
      x: x.toFixed(1),
      y: y.toFixed(1),
      lx: lx.toFixed(1),
      ly: (ly + 4).toFixed(1),
      anchor,
      isPenalty: a.label.includes('罚则'),
    }
  })

  return {
    rings,
    spokes,
    polygon: points.map((p) => `${p.x},${p.y}`).join(' '),
    points,
  }
})

const whitepapersOutdated = computed(() => {
  const opVal = data.value?.operations.whitepapers_outdated
  if (typeof opVal === 'number') return opVal
  const hlVal = data.value?.headline.whitepapers_outdated
  if (typeof hlVal === 'number') return hlVal
  return 0
})

const cnStandardsCount = computed(() => {
  const hl = data.value?.headline
  return hl?.national_standards_count ?? hl?.standards_cn ?? 0
})

const intlStandardsCount = computed(() => {
  const hl = data.value?.headline
  return hl?.intl_standards_count ?? hl?.standards_intl ?? hl?.standards_international ?? 0
})

const standardsSubtitle = computed(() => {
  if (cnStandardsCount.value > 0 || intlStandardsCount.value > 0) {
    return `中国标准 ${cnStandardsCount.value} 项 · 国际通用 ${intlStandardsCount.value} 项`
  }
  return '涵盖整车信息安全、智能网联与数据合规技术标准'
})

const executiveTakeaway = computed(() => {
  const d = data.value
  if (!d) return ''
  const hl = d.headline
  const trPct = hl.translation_coverage_pct !== null ? hl.translation_coverage_pct : 100
  if (deckFocus.value === 'governance' && !props.opsMode) {
    const totalProj = hl.doc_projects_total + hl.admission_projects_total
    return `平台治理实绩：当前服务 ${hl.active_organizations} 家活跃车企单位，在途跨境合规评估与海外准入共 ${totalProj} 个项目、打通 ${d.routes.length} 条跨洲数据流向；近 7 日累计安全留痕 ${d.operations.audit_events_7d} 条，入库质控与国家简报待更新保持零积压。`
  }
  return `全球法规底座实况：已覆盖五大洲 ${hl.covered_jurisdictions} 个汽车出海法域，收录现行有效法规 ${hl.laws_in_force} 部（${hl.clauses_total.toLocaleString()} 条可用条文）与中外标准 ${hl.standards_active} 项；外文条文中文精译率 ${trPct}%，全面支撑 ${hl.briefings_ready} 国八维合规简报与 ${hl.official_forms_total} 套东道国法定原表套打。`
})

const cleanKgLabel = (label: string) => label.replace(/节点/g, '要素')

const maxClauses = computed(() => {
  const vals = filteredRankingCountries.value.map((c) => c.clauses_count)
  return Math.max(1, ...vals)
})

const ringOffset = (pct: number | null | undefined, radius = 25) => {
  const c = 2 * Math.PI * radius
  const v = Math.max(0, Math.min(100, Number(pct || 0)))
  return c - (c * v) / 100
}

const siteHealthyPct = computed(() => {
  const total = data.value?.operations.monitor_sites_total || 0
  const att = data.value?.operations.attention_sites_total || 0
  if (total <= 0) return null
  return Math.round(((total - att) * 1000) / total) / 10
})

function go(target: RouteLocationRaw) {
  isFullscreen.value = false
  if (document.fullscreenElement) {
    void document.exitFullscreen().finally(() => router.push(target))
    return
  }
  router.push(target)
}

watch(
  isFullscreen,
  (val) => {
    document.documentElement.classList.toggle('cy-cockpit-fullscreen', val)
    document.body.style.overflow = val ? 'hidden' : ''
  },
  { immediate: true },
)

onMounted(() => {
  deckTheme.value = readInitialTheme()
  window.addEventListener('keydown', onFullscreenKeydown)
  void load()
})

onUnmounted(() => {
  if (camAnimId !== null) cancelAnimationFrame(camAnimId)
  document.documentElement.classList.remove('cy-cockpit-fullscreen')
  document.body.style.overflow = ''
  window.removeEventListener('keydown', onFullscreenKeydown)
})
</script>

<template>
  <div
    ref="boardRef"
    class="cockpit-board"
    :class="[`theme-${deckTheme}`, { 'is-fullscreen': isFullscreen }]"
    v-loading="loading"
    :data-testid="props.opsMode ? 'ops-admin-cockpit' : 'super-admin-cockpit'"
  >
    <!-- 1. 领导汇报冠冕顶栏：实时在线脉冲 + 视角与主题秒切 -->
    <header class="cockpit-header">
      <div class="crown-left">
        <div class="crown-mark" aria-hidden="true">数合</div>
        <div>
          <div v-if="data" class="crown-pulse-row">
            <span class="pulse-dot" :class="{ warn: data.operations.attention_sites_total > 0 }" />
            <span class="pulse-text">
              全球 {{ data.operations.monitor_sites_total - data.operations.attention_sites_total }}/{{ data.operations.monitor_sites_total }} 个官方源站错峰巡检正常 ·
              {{ whitepapersOutdated === 0 ? `${data.headline.briefings_ready} 国简报全部最新` : `${whitepapersOutdated} 国简报待同步` }}
            </span>
          </div>
          <h1>{{ props.opsMode ? '全球法规知识底座与采集巡检态势台' : '全球汽车数据跨境与属地准入战略态势总控台' }}</h1>
        </div>
      </div>

      <div class="cockpit-actions">
        <div v-if="!props.opsMode" class="seg-group" role="group" aria-label="汇报视角切换">
          <button
            type="button"
            class="seg-btn"
            :class="{ active: deckFocus === 'foundation' }"
            @click="deckFocus = 'foundation'"
          >
            全局法规底座
          </button>
          <button
            type="button"
            class="seg-btn"
            :class="{ active: deckFocus === 'governance' }"
            @click="deckFocus = 'governance'"
          >
            车企治理实绩
          </button>
        </div>

        <div class="seg-group" role="group" aria-label="展示底色切换">
          <button
            type="button"
            class="seg-btn"
            :class="{ active: deckTheme === 'command' }"
            @click="setTheme('command')"
          >
            深海指挥大屏
          </button>
          <button
            type="button"
            class="seg-btn"
            :class="{ active: deckTheme === 'ministry' }"
            @click="setTheme('ministry')"
          >
            政务白底评审
          </button>
        </div>

        <el-button
          type="primary"
          :data-testid="props.opsMode ? 'ops-cockpit-fullscreen-toggle' : 'cockpit-fullscreen-toggle'"
          @click="toggleFullscreen"
        >
          {{ isFullscreen ? '退出全屏' : '全屏展示' }}
        </el-button>
      </div>
    </header>

    <template v-if="data">
      <!-- 2. 一页纸核心建设成果结论带 -->
      <section class="takeaway-ribbon" aria-label="核心成效摘要">
        <p class="takeaway-text">{{ executiveTakeaway }}</p>
        <span class="takeaway-badge">中英双语文书直出 · 25 套官方原表套打</span>
      </section>

      <!-- 3. 顶部 6 大旗舰精密指标舱 -->
      <section class="dk-kpi6" aria-label="平台核心指标">
        <button
          type="button"
          class="dk-card dk-btn-card"
          @click="go({ path: '/documents', query: { status: 'in_force', folder: 'all' } })"
        >
          <div class="dk-kpi-top">
            <span class="dk-kpi-lbl">全库在册现行法规</span>
            <span class="dk-kpi-tag">{{ data.headline.covered_jurisdictions }} 国覆盖</span>
          </div>
          <strong class="dk-kpi-val">{{ data.headline.laws_in_force }}<small> 部</small></strong>
          <div class="dk-mini-track"><div class="dk-mini-fill" style="width: 100%" /></div>
          <span class="dk-kpi-foot">{{ data.headline.clauses_total.toLocaleString() }} 条现行可用条文</span>
        </button>

        <button
          type="button"
          class="dk-card dk-btn-card"
          @click="go({ path: '/standards', query: { status: 'active' } })"
        >
          <div class="dk-kpi-top">
            <span class="dk-kpi-lbl">现行与即将实施标准</span>
            <span class="dk-kpi-tag gold">中外双轨</span>
          </div>
          <strong class="dk-kpi-val">{{ data.headline.standards_active }}<small> 项</small></strong>
          <div class="dk-mini-track">
            <div
              class="dk-mini-fill gold"
              :style="{ width: `${data.headline.standards_active ? Math.max(18, Math.round((cnStandardsCount * 100) / data.headline.standards_active)) : 50}%` }"
            />
          </div>
          <span class="dk-kpi-foot">{{ standardsSubtitle }}</span>
        </button>

        <button type="button" class="dk-card dk-btn-card" @click="go('/translations')">
          <div class="dk-kpi-top">
            <span class="dk-kpi-lbl">外文条文中文译文覆盖</span>
            <span class="dk-kpi-tag emerald">六道门禁</span>
          </div>
          <strong class="dk-kpi-val dk-val-emerald">
            {{ data.headline.translation_coverage_pct !== null ? data.headline.translation_coverage_pct : 100 }}<small>%</small>
          </strong>
          <div class="dk-mini-track">
            <div
              class="dk-mini-fill emerald"
              :style="{ width: `${data.headline.translation_coverage_pct !== null ? data.headline.translation_coverage_pct : 100}%` }"
            />
          </div>
          <span class="dk-kpi-foot">{{ data.headline.translated_clauses_total.toLocaleString() }} / {{ data.headline.foreign_clauses_total.toLocaleString() }} 条已核验</span>
        </button>

        <button type="button" class="dk-card dk-btn-card" @click="go('/knowledge-graph')">
          <div class="dk-kpi-top">
            <span class="dk-kpi-lbl">知识图谱关联要素</span>
            <span class="dk-kpi-tag">逐字锚定</span>
          </div>
          <strong class="dk-kpi-val">{{ data.headline.kg_nodes_total.toLocaleString() }}<small> 个</small></strong>
          <div class="dk-mini-track"><div class="dk-mini-fill" style="width: 94%" /></div>
          <span class="dk-kpi-foot">已梳理 {{ data.headline.kg_edges_total.toLocaleString() }} 条义务与罚则关系</span>
        </button>

        <button type="button" class="dk-card dk-btn-card" @click="go('/whitepapers')">
          <div class="dk-kpi-top">
            <span class="dk-kpi-lbl">国家合规简报与准入</span>
            <span class="dk-kpi-tag emerald">{{ whitepapersOutdated === 0 ? '零待更新' : `${whitepapersOutdated} 待同步` }}</span>
          </div>
          <strong class="dk-kpi-val">{{ data.headline.briefings_ready }}<small> 国简报</small></strong>
          <div class="dk-mini-track"><div class="dk-mini-fill emerald" style="width: 100%" /></div>
          <span class="dk-kpi-foot">{{ data.headline.admission_entries_total }} 项准入 · {{ data.headline.official_forms_total }} 套法定申报表</span>
        </button>

        <button
          v-if="!props.opsMode"
          type="button"
          class="dk-card dk-btn-card"
          @click="go('/agent-studio')"
        >
          <div class="dk-kpi-top">
            <span class="dk-kpi-lbl">企业评估与准入项目</span>
            <span class="dk-kpi-tag gold">{{ data.headline.active_organizations }} 家单位</span>
          </div>
          <strong class="dk-kpi-val">{{ data.headline.doc_projects_total + data.headline.admission_projects_total }}<small> 个项目</small></strong>
          <div class="dk-mini-track"><div class="dk-mini-fill gold" style="width: 92%" /></div>
          <span class="dk-kpi-foot">合规评估 {{ data.headline.doc_projects_total }} · 属地准入 {{ data.headline.admission_projects_total }}</span>
        </button>

        <button
          v-else
          type="button"
          class="dk-card dk-btn-card"
          @click="go('/sites')"
        >
          <div class="dk-kpi-top">
            <span class="dk-kpi-lbl">官方源站与舆情监测</span>
            <span class="dk-kpi-tag emerald">周错峰巡检</span>
          </div>
          <strong class="dk-kpi-val">{{ data.operations.monitor_sites_total }}<small> 个源站</small></strong>
          <div class="dk-mini-track"><div class="dk-mini-fill emerald" :style="{ width: `${siteHealthyPct ?? 100}%` }" /></div>
          <span class="dk-kpi-foot">正常 {{ data.operations.monitor_sites_total - data.operations.attention_sites_total }} 站 · 舆情动态 {{ data.headline.opinion_items_total }} 条</span>
        </button>
      </section>

      <!-- 4. 主体三栏战略大屏 -->
      <section class="adm-dark-body">
        <!-- 左栏：权威质量双环 + 六维知识图谱雷达 -->
        <div class="dk-stack dk-col-left">
          <div class="dk-card">
            <div class="dk-sec-head">
              <h2>底座质量门禁与巡检状态</h2>
              <span class="dk-link" @click="go('/updates')">更新与时效 →</span>
            </div>
            <div class="dk-ring-pair">
              <div class="dk-ring-col" @click="go('/translations')">
                <svg viewBox="0 0 64 64" width="48" height="48" class="dk-ring-svg" aria-hidden="true">
                  <circle cx="32" cy="32" r="25" fill="none" class="ring-track-svg" stroke-width="6" />
                  <circle
                    v-if="data.headline.translation_coverage_pct !== null"
                    cx="32"
                    cy="32"
                    r="25"
                    fill="none"
                    class="ring-cyan-svg"
                    stroke-width="6"
                    stroke-linecap="round"
                    :stroke-dasharray="2 * Math.PI * 25"
                    :stroke-dashoffset="ringOffset(data.headline.translation_coverage_pct, 25)"
                    transform="rotate(-90 32 32)"
                  />
                  <text x="32" y="37" text-anchor="middle" class="ring-text-svg" font-size="13" font-weight="600">
                    {{ data.headline.translation_coverage_pct !== null ? `${Math.round(data.headline.translation_coverage_pct)}%` : '100%' }}
                  </text>
                </svg>
                <div class="dk-ring-meta">
                  <strong class="dk-ring-title">译文覆盖</strong>
                  <span class="dk-ring-sub">{{ data.headline.translated_clauses_total.toLocaleString() }} 条</span>
                </div>
              </div>

              <div class="dk-ring-col" @click="go(data.operations.attention_sites_total > 0 ? '/sites?attention=1' : '/sites')">
                <svg viewBox="0 0 64 64" width="48" height="48" class="dk-ring-svg" aria-hidden="true">
                  <circle cx="32" cy="32" r="25" fill="none" class="ring-track-svg" stroke-width="6" />
                  <circle
                    v-if="siteHealthyPct !== null"
                    cx="32"
                    cy="32"
                    r="25"
                    fill="none"
                    :class="data.operations.attention_sites_total > 0 ? 'ring-gold-svg' : 'ring-emerald-svg'"
                    stroke-width="6"
                    stroke-linecap="round"
                    :stroke-dasharray="2 * Math.PI * 25"
                    :stroke-dashoffset="ringOffset(siteHealthyPct, 25)"
                    transform="rotate(-90 32 32)"
                  />
                  <text x="32" y="37" text-anchor="middle" class="ring-text-svg" font-size="13" font-weight="600">
                    {{ siteHealthyPct !== null ? `${Math.round(siteHealthyPct)}%` : '—' }}
                  </text>
                </svg>
                <div class="dk-ring-meta">
                  <strong class="dk-ring-title">采集站点</strong>
                  <span class="dk-ring-sub">{{ data.operations.monitor_sites_total - data.operations.attention_sites_total }} 站正常</span>
                </div>
              </div>
            </div>

            <div class="dk-row dk-row-click" @click="go('/intake-quality')">
              <span>入库质量待复查法规</span>
              <span :class="data.operations.quality_unpassed > 0 ? 'dk-warn' : 'dk-done'">
                {{ data.operations.quality_unpassed > 0 ? `${data.operations.quality_unpassed} 部待复查` : '全部通过检查' }}
              </span>
            </div>
            <div class="dk-row dk-row-click" @click="go('/updates')">
              <span>待确认的法规条文修订</span>
              <span :class="data.operations.pending_updates > 0 ? 'dk-warn' : 'dk-done'">
                {{ data.operations.pending_updates > 0 ? `${data.operations.pending_updates} 项待确认` : '已全部确认' }}
              </span>
            </div>
            <div class="dk-row dk-row-click" @click="go('/whitepapers')">
              <span>国家合规简报待更新</span>
              <span :class="whitepapersOutdated > 0 ? 'dk-warn' : 'dk-done'">
                {{ whitepapersOutdated > 0 ? `${whitepapersOutdated} 国待更新` : '均为最新' }}
              </span>
            </div>
          </div>

          <div class="dk-card dk-flex1">
            <div class="dk-sec-head">
              <h2>知识图谱六要素立体雷达</h2>
              <span class="dk-link" @click="go('/knowledge-graph')">打开图谱 →</span>
            </div>

            <div v-if="radarGeometry" class="radar-wrap" @click="go('/knowledge-graph')">
              <svg viewBox="0 0 224 188" class="radar-svg" aria-label="知识图谱六要素分布雷达图">
                <polygon
                  v-for="(pts, rIdx) in radarGeometry.rings"
                  :key="`ring-${rIdx}`"
                  :points="pts"
                  fill="none"
                  class="radar-grid-line"
                  stroke-width="1"
                />
                <line
                  v-for="(sp, sIdx) in radarGeometry.spokes"
                  :key="`spoke-${sIdx}`"
                  :x1="sp.x1"
                  :y1="sp.y1"
                  :x2="sp.x2"
                  :y2="sp.y2"
                  class="radar-grid-line"
                  stroke-width="1"
                />
                <polygon :points="radarGeometry.polygon" class="radar-area" stroke-width="2" />
                <circle
                  v-for="pt in radarGeometry.points"
                  :key="`dot-${pt.label}`"
                  :cx="pt.x"
                  :cy="pt.y"
                  r="3.6"
                  :class="pt.isPenalty ? 'radar-dot-gold' : 'radar-dot-cyan'"
                />
                <text
                  v-for="pt in radarGeometry.points"
                  :key="`lbl-${pt.label}`"
                  :x="pt.lx"
                  :y="pt.ly"
                  :text-anchor="pt.anchor"
                  class="radar-axis-lbl"
                  font-size="13"
                >
                  {{ pt.shortLabel }}
                </text>
              </svg>
            </div>

            <div class="kg-legend-list">
              <div
                v-for="item in sortedKgBreakdown"
                :key="item.label"
                class="dk-row dk-row-click kg-compact-row"
                @click="go('/knowledge-graph')"
              >
                <span class="kg-dot-label">
                  <i class="kg-dot" :class="{ gold: item.label.includes('罚则') }" />
                  {{ cleanKgLabel(item.label) }}
                </span>
                <strong>{{ item.count.toLocaleString() }} 个</strong>
              </div>
            </div>
          </div>
        </div>

        <!-- 中栏：180国真实矢量边界 + 四阶密度热力 + 五大洲平滑推拉镜头 + 穿透档案舱 -->
        <div class="map-frame dk-col-center">
          <div class="map-header-row">
            <div>
              <h2>全球法域法规密度热力与跨境业务流向沙盘</h2>
              <span class="map-hint">支持按洲推近镜头 · 点击高亮国家或右侧排行联动查阅单国穿透档案</span>
            </div>
            <div class="cam-bar" role="group" aria-label="区域镜头切换">
              <button
                v-for="(preset, key) in CAMERA_PRESETS"
                :key="key"
                type="button"
                class="cam-pill"
                :class="{ active: activeCamera === key }"
                @click="setCamera(key)"
              >
                {{ preset.label }}
              </button>
            </div>
          </div>

          <div class="geo-stage-box">
            <svg class="geo-svg" :viewBox="MAP_VIEWBOX" role="img" aria-label="全球重点法域法规覆盖与跨境流向地图">
              <g class="geo-grid-lines" stroke-width="0.9">
                <line x1="0" y1="110" x2="960" y2="110" />
                <line x1="0" y1="220" x2="960" y2="220" />
                <line x1="0" y1="330" x2="960" y2="330" />
                <line x1="240" y1="0" x2="240" y2="440" />
                <line x1="480" y1="0" x2="480" y2="440" />
                <line x1="720" y1="0" x2="720" y2="440" />
              </g>

              <!-- 地理几何与航线层：缩放时保持 1px 锐利边线 -->
              <g :transform="cameraTransform">
                <path
                  v-for="[cc, d] in backgroundLandEntries"
                  :key="`bg-${cc}`"
                  class="geo-land"
                  vector-effect="non-scaling-stroke"
                  :d="d"
                />

                <path
                  v-for="item in coveredLandEntries"
                  :key="`cov-${item.code}`"
                  class="geo-land geo-covered"
                  :class="[
                    item.tierClass,
                    {
                      active: selectedGeoCode === item.code,
                      dimmed: selectedGeoCode !== 'ALL' && selectedGeoCode !== item.code,
                    },
                  ]"
                  vector-effect="non-scaling-stroke"
                  :d="item.d"
                  @click="selectedGeoCode = selectedGeoCode === item.code ? 'ALL' : item.code"
                >
                  <title>{{ item.country.country_name_zh }}（{{ item.country.laws_count }} 部现行法规 · {{ item.country.clauses_count }} 条条文）</title>
                </path>

                <g v-for="arc in mapArcs" :key="arc.key" :opacity="arc.active ? 1 : 0.2">
                  <path
                    class="arc-glow"
                    :class="arc.fromCn ? 'arc-cn' : 'arc-intl'"
                    vector-effect="non-scaling-stroke"
                    :d="arc.d"
                  />
                  <path
                    class="arc-line"
                    :class="arc.fromCn ? 'arc-cn' : 'arc-intl'"
                    vector-effect="non-scaling-stroke"
                    :d="arc.d"
                  />
                </g>
              </g>

              <!-- 屏幕空间锚点与 13px 恒定清晰标签层 -->
              <g
                v-for="m in mapHubMarkers"
                :key="`hub-${m.code}`"
                class="hub-group"
                @click="selectedGeoCode = selectedGeoCode === m.code ? 'ALL' : m.code"
              >
                <title>{{ m.name }}（{{ m.laws }} 部现行法规）</title>
                <circle
                  v-if="m.isMajor"
                  :cx="m.sx"
                  :cy="m.sy"
                  :r="m.isCn ? 10 : 8.5"
                  class="hub-halo"
                  :class="{ gold: m.isCn, emerald: m.isSelected && !m.isCn }"
                />
                <circle
                  :cx="m.sx"
                  :cy="m.sy"
                  :r="m.isCn || m.isSelected ? 5.6 : 4.2"
                  class="hub-core"
                  :class="{ gold: m.isCn, emerald: m.isSelected && !m.isCn }"
                />
                <text
                  v-if="m.showLabel"
                  class="map-lbl"
                  :class="{ 'map-lbl-cn': m.isCn }"
                  :x="m.lx"
                  :y="m.ly"
                  :text-anchor="m.anchor"
                  font-size="13"
                  font-weight="600"
                >
                  {{ m.labelText }}
                </text>
              </g>
            </svg>

            <div class="map-legend-strip">
              <div class="legend-group">
                <span class="legend-caption">法规密度阶梯：</span>
                <span class="legend-item"><i class="lg-swatch cn" />中国发出枢纽</span>
                <span class="legend-item"><i class="lg-swatch high" />高密度 (≥25部)</span>
                <span class="legend-item"><i class="lg-swatch mid" />重点国 (8–24部)</span>
                <span class="legend-item"><i class="lg-swatch base" />基础覆盖 (1–7部)</span>
              </div>
              <div v-if="mapArcs.length" class="legend-group">
                <span class="legend-item"><i class="lg-line gold" />中国出境走廊</span>
                <span class="legend-item"><i class="lg-line emerald" />海外互传/回传</span>
              </div>
            </div>
          </div>

          <!-- 单国/全局五维穿透档案底舱 -->
          <div class="map-detail-bar" :data-testid="props.opsMode ? 'ops-cockpit-map-detail' : 'cockpit-map-detail'">
            <template v-if="!selectedCountry">
              <div class="dossier-head">
                <div class="map-detail-title">
                  <span class="dossier-tag">全球底座汇总</span>
                  <strong>全库已覆盖 {{ sovereignCountries.length }} 个主权国家与地区</strong>
                </div>
                <span class="map-muted">点击地图高亮法域可展开单国五维穿透指标</span>
              </div>
              <div class="dossier-grid5">
                <div class="dossier-cell">
                  <span>在册现行法规</span>
                  <strong>{{ data.headline.laws_in_force }} 部 / {{ data.headline.clauses_total.toLocaleString() }} 条</strong>
                </div>
                <div class="dossier-cell">
                  <span>现行技术标准</span>
                  <strong>{{ data.headline.standards_active }} 项中外标准</strong>
                </div>
                <div class="dossier-cell">
                  <span>中文译文覆盖</span>
                  <strong class="dk-done">{{ data.headline.translation_coverage_pct !== null ? `${data.headline.translation_coverage_pct}%` : '100%' }}</strong>
                </div>
                <div class="dossier-cell">
                  <span>知识图谱要素</span>
                  <strong>{{ data.headline.kg_nodes_total.toLocaleString() }} 个 / {{ data.headline.kg_edges_total.toLocaleString() }} 边</strong>
                </div>
                <div class="dossier-cell">
                  <span>准入与申报原表</span>
                  <strong>{{ data.headline.admission_entries_total }} 项 / {{ data.headline.official_forms_total }} 套</strong>
                </div>
              </div>
            </template>

            <template v-else>
              <div class="dossier-head">
                <div class="map-detail-title">
                  <span class="dossier-tag active">单国穿透档案</span>
                  <strong>{{ selectedCountry.country_name_zh }}（{{ selectedCountry.country_code }}）</strong>
                  <span class="dossier-sub">
                    · {{ selectedCountry.country_code === 'CN' ? '数据发出母港与国标体系基准' : (selectedCountry.briefing_ready ? '八维合规简报已就绪' : '法规档案持续收录中') }}
                  </span>
                </div>
                <div class="dossier-actions">
                  <button type="button" class="dk-inline-reset" @click="selectedGeoCode = 'ALL'">返回全部</button>
                  <button
                    v-if="selectedCountry.country_code !== 'CN' && selectedCountry.briefing_ready"
                    type="button"
                    class="dk-inline-reset"
                    @click="go('/whitepapers')"
                  >
                    国别简报 →
                  </button>
                  <button
                    type="button"
                    class="dk-cta-btn"
                    @click="go({ path: '/documents', query: { country: selectedCountry.country_code } })"
                  >
                    进入该国法规 →
                  </button>
                </div>
              </div>
              <div class="dossier-grid5">
                <div class="dossier-cell">
                  <span>在册现行法规</span>
                  <strong>{{ selectedCountry.laws_count }} 部 / {{ selectedCountry.clauses_count.toLocaleString() }} 条</strong>
                </div>
                <div class="dossier-cell">
                  <span>配套技术标准</span>
                  <strong>{{ selectedCountry.standards_count }} 项关联标准</strong>
                </div>
                <div class="dossier-cell">
                  <span>译文状态</span>
                  <strong class="dk-done">
                    {{
                      selectedCountry.is_zh_native
                        ? '中文原文直接可读'
                        : selectedCountry.translated_clauses_count === 0 && selectedCountry.foreign_clauses_count > 0
                          ? '原文已归档（待精译）'
                          : `${selectedCountry.translation_pct ?? 100}% 已核验`
                    }}
                  </strong>
                </div>
                <div class="dossier-cell">
                  <span>知识图谱要素</span>
                  <strong>{{ selectedCountry.kg_nodes_count.toLocaleString() }} 个 / {{ selectedCountry.kg_edges_count.toLocaleString() }} 边</strong>
                </div>
                <div class="dossier-cell">
                  <span>准入与法定原表</span>
                  <strong>{{ selectedCountry.admission_entries_count }} 项 / {{ selectedCountry.official_forms_count }} 套</strong>
                </div>
              </div>
            </template>
          </div>
        </div>

        <!-- 右栏：重点出海法域密度排行 + 平台治理/运维巡检概况 -->
        <div class="dk-stack dk-col-right">
          <div class="dk-card">
            <div class="dk-sec-head">
              <h2>{{ activeCamera === 'WORLD' ? '重点法域法规与条文储备榜' : `${CAMERA_PRESETS[activeCamera].label}法规储备榜` }}</h2>
              <span class="dk-link" @click="go('/documents')">全部法规 →</span>
            </div>
            <div class="dk-country-scroll">
              <div
                v-for="(c, idx) in filteredRankingCountries"
                :key="`dk-c-${c.country_code}`"
                class="dk-bar-item dk-country-row"
                :class="{ selected: selectedGeoCode === c.country_code }"
                @click="selectedGeoCode = selectedGeoCode === c.country_code ? 'ALL' : c.country_code"
              >
                <div class="dk-bar-top">
                  <span class="rank-country-name">
                    <i class="rank-num" :class="{ top3: idx < 3 }">{{ idx + 1 }}</i>
                    {{ c.country_name_zh }}
                  </span>
                  <span class="dk-country-right">
                    <strong>
                      {{ c.laws_count }} 部 ·
                      {{ c.country_code === 'CN' && c.standards_count > 0 ? `${c.standards_count} 项国标` : `${c.clauses_count.toLocaleString()} 条` }}
                    </strong>
                    <button
                      type="button"
                      class="dk-jump-btn"
                      @click.stop="go({ path: '/documents', query: { country: c.country_code } })"
                    >
                      查看 →
                    </button>
                  </span>
                </div>
                <div class="dk-track">
                  <div
                    class="dk-fill"
                    :class="{ emerald: selectedGeoCode === c.country_code, gold: c.country_code === 'CN' && selectedGeoCode !== c.country_code }"
                    :style="{ width: `${c.clauses_count > 0 ? Math.max(10, Math.round((c.clauses_count / maxClauses) * 100)) : 8}%` }"
                  />
                </div>
              </div>
              <p v-if="!filteredRankingCountries.length" class="dk-empty">当前区域暂无已收录国家法规数据</p>
            </div>
          </div>

          <!-- 超级管理员：企业治理、跨洲走廊与安全审计 -->
          <div v-if="!props.opsMode" class="dk-card dk-flex1">
            <div class="dk-sec-head">
              <h2>平台服务治理与运行审计</h2>
              <span class="dk-link" @click="go('/audit-logs')">审计记录 →</span>
            </div>
            <div class="dk-row dk-row-click" @click="go('/orgs')">
              <span>活跃入驻车企单位</span>
              <strong>{{ data.headline.active_organizations }} 家单位</strong>
            </div>
            <div class="dk-row dk-row-click" @click="go('/orgs')">
              <span>管理端与企业端角色账号</span>
              <strong>管理 {{ data.account_summary.super_admins + data.account_summary.ops_admins }} · 企业 {{ data.account_summary.enterprise_members + data.account_summary.ordinary_viewers }}</strong>
            </div>
            <div v-if="data.account_summary.trial_visitors" class="dk-row dk-row-click" @click="go('/orgs')">
              <span>门户独立体验空间账号</span>
              <strong>{{ data.account_summary.trial_visitors }} 个账号</strong>
            </div>
            <div class="dk-row dk-row-click" @click="go('/agent-studio')">
              <span>在途跨洲数据合规走廊</span>
              <strong class="dk-done">{{ data.routes.length }} 条活跃路线</strong>
            </div>
            <div class="dk-row dk-row-click" @click="go('/opinion')">
              <span>海外监管与行业舆情收录</span>
              <strong>{{ data.headline.opinion_items_total }} 条动态</strong>
            </div>
            <div class="dk-row dk-row-click" @click="go('/audit-logs')">
              <span>近 7 日不可变操作审计</span>
              <strong>{{ data.operations.audit_events_7d }} 条记录</strong>
            </div>
          </div>

          <!-- 运维管理员：法规巡检、文书模板与准入资料底座（100% 匹配运维权限边界） -->
          <div v-else class="dk-card dk-flex1">
            <div class="dk-sec-head">
              <h2>采集巡检与文书模板底座</h2>
              <span class="dk-link" @click="go('/sites')">巡检管理 →</span>
            </div>
            <div class="dk-row dk-row-click" @click="go('/sites')">
              <span>在用官方源站周错峰巡检</span>
              <strong :class="data.operations.attention_sites_total > 0 ? 'dk-warn' : 'dk-done'">
                {{ data.operations.monitor_sites_total - data.operations.attention_sites_total }} / {{ data.operations.monitor_sites_total }} 站正常
              </strong>
            </div>
            <div class="dk-row dk-row-click" @click="go('/admission')">
              <span>东道国属地准入事项库</span>
              <strong>{{ data.headline.admission_entries_total }} 项现行事项</strong>
            </div>
            <div class="dk-row dk-row-click" @click="go('/admission')">
              <span>已锁定指纹官方申报原表</span>
              <strong>{{ data.headline.official_forms_total }} 套法定原表</strong>
            </div>
            <div class="dk-row dk-row-click" @click="go('/document-templates')">
              <span>合规评估标准文书模板库</span>
              <strong class="dk-done">8 类全套法定模板</strong>
            </div>
            <div class="dk-row dk-row-click" @click="go('/opinion')">
              <span>海外执法与行业舆情动态</span>
              <strong>{{ data.headline.opinion_items_total }} 条已归档</strong>
            </div>
          </div>
        </div>
      </section>
    </template>
  </div>
</template>

<style scoped>
@keyframes routeFlow {
  to {
    stroke-dashoffset: -36;
  }
}

/* 主题变量：默认深海指挥大屏，支持一键切换政务白底评审态（无阴影、无花哨渐变、全站 >=13px、字重仅 400/600） */
.cockpit-board.theme-command {
  --ck-bg: #060d1b;
  --ck-header: #091428;
  --ck-card: #0b1629;
  --ck-inner: #10203a;
  --ck-border: #1d3354;
  --ck-accent: #38bdf8;
  --ck-emerald: #10b981;
  --ck-emerald-tx: #34d399;
  --ck-gold: #f59e0b;
  --ck-gold-tx: #fbbf24;
  --ck-tx-1: #f8fafc;
  --ck-tx-2: #cbd5e1;
  --ck-tx-muted: #94a3b8;
  --ck-ocean: #050b17;
  --ck-grid: #102038;
  --ck-land: #132238;
  --ck-land-stroke: #233858;
  --ck-tier-base: #1e4278;
  --ck-tier-mid: #0284c7;
  --ck-tier-high: #0ea5e9;
  --ck-tier-cn: #d97706;
  --ck-lbl-stroke: #050b17;
}

.cockpit-board.theme-ministry {
  --ck-bg: #f1f5f9;
  --ck-header: #ffffff;
  --ck-card: #ffffff;
  --ck-inner: #f8fafc;
  --ck-border: #cbd5e1;
  --ck-accent: #1d4ed8;
  --ck-emerald: #059669;
  --ck-emerald-tx: #047857;
  --ck-gold: #b45309;
  --ck-gold-tx: #b45309;
  --ck-tx-1: #0f172a;
  --ck-tx-2: #334155;
  --ck-tx-muted: #64748b;
  --ck-ocean: #e8eff8;
  --ck-grid: #d5e1ef;
  --ck-land: #dce4ee;
  --ck-land-stroke: #b8c6d8;
  --ck-tier-base: #93c5fd;
  --ck-tier-mid: #3b82f6;
  --ck-tier-high: #1d4ed8;
  --ck-tier-cn: #d97706;
  --ck-lbl-stroke: #ffffff;
}

.cockpit-board {
  display: flex;
  flex-direction: column;
  gap: 14px;
  background: var(--ck-bg);
  border: 1px solid var(--ck-border);
  border-radius: 10px;
  padding: 18px 20px 20px;
  color: var(--ck-tx-1);
  font-size: 13px;
  font-weight: 400;
  font-variant-numeric: tabular-nums;
  min-height: 480px;
}

.cockpit-board strong,
.cockpit-board b,
.cockpit-board h1,
.cockpit-board h2,
.cockpit-board h3 {
  font-weight: 600;
}

.cockpit-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 14px;
  flex-wrap: wrap;
  background: var(--ck-header);
  border: 1px solid var(--ck-border);
  border-top: 2px solid var(--ck-accent);
  border-radius: 8px;
  padding: 12px 16px;
}

.crown-left {
  display: flex;
  align-items: center;
  gap: 12px;
  min-width: 0;
}

.crown-mark {
  width: 38px;
  height: 38px;
  border-radius: 8px;
  background: var(--ck-accent);
  color: #ffffff;
  font-size: 14px;
  font-weight: 600;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}

.crown-pulse-row {
  display: flex;
  align-items: center;
  gap: 7px;
  margin-bottom: 2px;
}

.pulse-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--ck-emerald);
  flex-shrink: 0;
}
.pulse-dot.warn {
  background: var(--ck-gold);
}

.pulse-text {
  font-size: 13px;
  font-weight: 600;
  color: var(--ck-emerald-tx);
}

.cockpit-header h1 {
  margin: 0;
  font-size: 20px;
  line-height: 1.35;
  color: var(--ck-tx-1);
}

.cockpit-actions {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

.seg-group {
  display: inline-flex;
  background: var(--ck-inner);
  border: 1px solid var(--ck-border);
  border-radius: 6px;
  padding: 2px;
  gap: 2px;
}

.seg-btn {
  border: none;
  background: transparent;
  color: var(--ck-tx-2);
  font-size: 13px;
  font-weight: 400;
  padding: 5px 10px;
  border-radius: 4px;
  cursor: pointer;
  white-space: nowrap;
}
.seg-btn:hover {
  color: var(--ck-tx-1);
}
.seg-btn.active {
  background: var(--ck-accent);
  color: #ffffff;
  font-weight: 600;
}

.takeaway-ribbon {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
  background: var(--ck-card);
  border: 1px solid var(--ck-border);
  border-left: 4px solid var(--ck-gold);
  border-radius: 8px;
  padding: 10px 14px;
}

.takeaway-text {
  margin: 0;
  font-size: 13px;
  line-height: 1.6;
  color: var(--ck-tx-1);
  flex: 1 1 520px;
}

.takeaway-badge {
  font-size: 13px;
  font-weight: 600;
  color: var(--ck-emerald-tx);
  background: var(--ck-inner);
  border: 1px solid var(--ck-border);
  border-radius: 6px;
  padding: 4px 10px;
  white-space: nowrap;
}

.dk-kpi6 {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 12px;
}
@media (min-width: 1480px) {
  .dk-kpi6 {
    grid-template-columns: repeat(6, minmax(0, 1fr));
  }
}
@media (max-width: 760px) {
  .dk-kpi6 {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

.dk-card {
  background: var(--ck-card);
  border: 1px solid var(--ck-border);
  border-radius: 8px;
  padding: 14px 15px;
}

.dk-btn-card {
  text-align: left;
  cursor: pointer;
  color: inherit;
  font: inherit;
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  gap: 6px;
  transition: border-color 0.15s;
}
.dk-btn-card:hover {
  border-color: var(--ck-accent);
}

.dk-kpi-top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 6px;
}

.dk-kpi-lbl {
  font-size: 13px;
  color: var(--ck-tx-muted);
}

.dk-kpi-tag {
  font-size: 13px;
  font-weight: 600;
  color: var(--ck-accent);
  background: var(--ck-inner);
  border: 1px solid var(--ck-border);
  border-radius: 4px;
  padding: 1px 6px;
  white-space: nowrap;
}
.dk-kpi-tag.emerald {
  color: var(--ck-emerald-tx);
}
.dk-kpi-tag.gold {
  color: var(--ck-gold-tx);
}

.dk-kpi-val {
  font-size: 24px;
  font-weight: 600;
  color: var(--ck-tx-1);
  display: block;
  line-height: 1.2;
}
.dk-kpi-val small {
  font-size: 13px;
  font-weight: 400;
  color: var(--ck-tx-muted);
}
.dk-val-emerald {
  color: var(--ck-emerald-tx);
}

.dk-mini-track {
  height: 5px;
  background: var(--ck-inner);
  border-radius: 999px;
  overflow: hidden;
}
.dk-mini-fill {
  height: 100%;
  background: var(--ck-accent);
  border-radius: 999px;
}
.dk-mini-fill.emerald {
  background: var(--ck-emerald);
}
.dk-mini-fill.gold {
  background: var(--ck-gold);
}

.dk-kpi-foot {
  font-size: 13px;
  color: var(--ck-tx-2);
  display: block;
}

/* 宽屏或全屏时左中右三栏；1280/1440 带左侧菜单栏时自动变为上地图、下双栏，零挤压 */
.adm-dark-body {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 14px;
  align-items: stretch;
}
.dk-col-center {
  grid-column: 1 / -1;
  order: -1;
}
@media (min-width: 1580px) {
  .adm-dark-body {
    grid-template-columns: 304px minmax(0, 1fr) 320px;
  }
  .dk-col-center {
    grid-column: auto;
    order: 0;
  }
}
.cockpit-board.is-fullscreen .adm-dark-body {
  grid-template-columns: 312px minmax(0, 1fr) 328px;
}
.cockpit-board.is-fullscreen .dk-col-center {
  grid-column: auto;
  order: 0;
}
@media (max-width: 860px) {
  .adm-dark-body,
  .cockpit-board.is-fullscreen .adm-dark-body {
    grid-template-columns: 1fr;
  }
}

.dk-stack {
  display: flex;
  flex-direction: column;
  gap: 14px;
  min-width: 0;
}
.dk-flex1 {
  flex: 1;
  display: flex;
  flex-direction: column;
  justify-content: space-between;
}
.dk-sec-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 8px;
  margin-bottom: 10px;
}
.dk-sec-head h2 {
  margin: 0;
  font-size: 15px;
  color: var(--ck-tx-1);
  white-space: nowrap;
}
.dk-link {
  font-size: 13px;
  color: var(--ck-accent);
  cursor: pointer;
  white-space: nowrap;
}
.dk-link:hover {
  text-decoration: underline;
}

.dk-ring-pair {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 10px;
  padding-bottom: 12px;
  margin-bottom: 10px;
  border-bottom: 1px solid var(--ck-border);
}
.dk-ring-col {
  display: flex;
  align-items: center;
  gap: 7px;
  cursor: pointer;
  background: var(--ck-inner);
  border: 1px solid var(--ck-border);
  border-radius: 8px;
  padding: 7px 8px;
  min-width: 0;
}
.dk-ring-svg {
  flex-shrink: 0;
}
.dk-ring-meta {
  min-width: 0;
}
.ring-track-svg {
  stroke: var(--ck-border);
}
.ring-cyan-svg {
  stroke: var(--ck-accent);
}
.ring-emerald-svg {
  stroke: var(--ck-emerald);
}
.ring-gold-svg {
  stroke: var(--ck-gold);
}
.ring-text-svg {
  fill: var(--ck-tx-1);
}
.dk-ring-title {
  font-size: 13px;
  color: var(--ck-tx-1);
  display: block;
  white-space: nowrap;
}
.dk-ring-sub {
  font-size: 13px;
  color: var(--ck-tx-muted);
  display: block;
  white-space: nowrap;
}

.dk-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 10px;
  padding: 7px 0;
  border-bottom: 1px solid var(--ck-border);
  font-size: 13px;
  color: var(--ck-tx-2);
}
.dk-row > span:first-child {
  white-space: nowrap;
  flex-shrink: 0;
}
.dk-row > strong,
.dk-row > span:last-child {
  text-align: right;
  white-space: nowrap;
  color: var(--ck-tx-1);
}
.dk-row:last-child {
  border-bottom: none;
  padding-bottom: 0;
}
.dk-row-click {
  cursor: pointer;
}
.dk-row-click:hover {
  color: var(--ck-tx-1);
}
.dk-done {
  color: var(--ck-emerald-tx) !important;
  font-weight: 600;
}
.dk-warn {
  color: var(--ck-gold-tx) !important;
  font-weight: 600;
}

/* 六维知识图谱雷达 */
.radar-wrap {
  display: flex;
  justify-content: center;
  cursor: pointer;
  margin-bottom: 6px;
}
.radar-svg {
  width: 224px;
  height: 184px;
  overflow: visible;
}
.radar-grid-line {
  stroke: var(--ck-border);
}
.radar-area {
  fill: var(--ck-accent);
  fill-opacity: 0.2;
  stroke: var(--ck-accent);
}
.radar-dot-cyan {
  fill: var(--ck-accent);
  stroke: #ffffff;
  stroke-width: 1;
}
.radar-dot-gold {
  fill: var(--ck-gold);
  stroke: #ffffff;
  stroke-width: 1;
}
.radar-axis-lbl {
  fill: var(--ck-tx-2);
}
.kg-compact-row {
  padding: 5px 0;
}
.kg-dot-label {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}
.kg-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--ck-accent);
  display: inline-block;
}
.kg-dot.gold {
  background: var(--ck-gold);
}

/* 排行列表 */
.dk-bar-item {
  margin-bottom: 9px;
  font-size: 13px;
  cursor: pointer;
  padding: 6px 8px;
  border-radius: 6px;
  background: var(--ck-inner);
  border: 1px solid transparent;
}
.dk-bar-item:last-child {
  margin-bottom: 0;
}
.dk-bar-item:hover,
.dk-country-row.selected {
  border-color: var(--ck-accent);
}
.dk-bar-top {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 8px;
  margin-bottom: 5px;
  color: var(--ck-tx-2);
}
.rank-country-name {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-weight: 600;
  color: var(--ck-tx-1);
  white-space: nowrap;
}
.rank-num {
  font-style: normal;
  display: inline-block;
  min-width: 20px;
  text-align: center;
  border-radius: 4px;
  background: var(--ck-card);
  color: var(--ck-tx-muted);
  font-size: 13px;
  font-weight: 600;
}
.rank-num.top3 {
  color: var(--ck-gold-tx);
}
.dk-track {
  height: 5px;
  background: var(--ck-card);
  border-radius: 999px;
  overflow: hidden;
}
.dk-fill {
  height: 100%;
  background: var(--ck-accent);
  border-radius: 999px;
}
.dk-fill.emerald {
  background: var(--ck-emerald);
}
.dk-fill.gold {
  background: var(--ck-gold);
}

.dk-country-scroll {
  max-height: 256px;
  overflow-y: auto;
  padding-right: 2px;
}
.dk-country-right {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  white-space: nowrap;
}
.dk-jump-btn {
  border: none;
  background: none;
  color: var(--ck-accent);
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
  padding: 0;
  white-space: nowrap;
}
.dk-jump-btn:hover {
  text-decoration: underline;
}
.dk-empty {
  margin: 0;
  font-size: 13px;
  color: var(--ck-tx-muted);
}

/* 中央世界地图舱 */
.map-frame {
  background: var(--ck-card);
  border: 1px solid var(--ck-border);
  border-radius: 8px;
  padding: 14px 16px;
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  gap: 10px;
}
.map-header-row {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 10px;
  flex-wrap: wrap;
}
.map-header-row h2 {
  margin: 0 0 2px;
  font-size: 16px;
  color: var(--ck-tx-1);
}
.map-hint {
  font-size: 13px;
  color: var(--ck-tx-muted);
}
.cam-bar {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.cam-pill {
  border: 1px solid var(--ck-border);
  background: var(--ck-inner);
  color: var(--ck-tx-2);
  font-size: 13px;
  padding: 4px 10px;
  border-radius: 6px;
  cursor: pointer;
  white-space: nowrap;
}
.cam-pill:hover {
  border-color: var(--ck-accent);
  color: var(--ck-tx-1);
}
.cam-pill.active {
  background: var(--ck-accent);
  border-color: var(--ck-accent);
  color: #ffffff;
  font-weight: 600;
}

.geo-stage-box {
  border: 1px solid var(--ck-border);
  border-radius: 8px;
  overflow: hidden;
  background: var(--ck-ocean);
}
.geo-svg {
  width: 100%;
  height: auto;
  display: block;
  background: var(--ck-ocean);
}
.geo-grid-lines {
  stroke: var(--ck-grid);
}
.geo-land {
  fill: var(--ck-land);
  stroke: var(--ck-land-stroke);
  stroke-width: 0.75;
  transition: fill 0.15s, fill-opacity 0.15s;
}
.geo-land.geo-covered {
  fill-opacity: 0.78;
  stroke: var(--ck-accent);
  stroke-width: 1;
  cursor: pointer;
}
.geo-land.geo-covered.tier-base {
  fill: var(--ck-tier-base);
}
.geo-land.geo-covered.tier-mid {
  fill: var(--ck-tier-mid);
}
.geo-land.geo-covered.tier-high {
  fill: var(--ck-tier-high);
}
.geo-land.geo-covered.tier-cn {
  fill: var(--ck-tier-cn);
  fill-opacity: 0.88;
}
.geo-land.geo-covered.dimmed {
  fill-opacity: 0.3;
}
.geo-land.geo-covered:hover,
.geo-land.geo-covered.active {
  fill: var(--ck-gold);
  fill-opacity: 0.92;
  stroke: #ffffff;
  stroke-width: 1.5;
}

.hub-group {
  cursor: pointer;
}
.hub-halo {
  fill: var(--ck-accent);
  fill-opacity: 0.24;
}
.hub-halo.gold {
  fill: var(--ck-gold);
  fill-opacity: 0.32;
}
.hub-halo.emerald {
  fill: var(--ck-emerald);
  fill-opacity: 0.32;
}
.hub-core {
  fill: var(--ck-accent);
  stroke: #ffffff;
  stroke-width: 1.3;
}
.hub-core.gold {
  fill: var(--ck-gold);
}
.hub-core.emerald {
  fill: var(--ck-emerald);
}

.map-lbl {
  fill: var(--ck-tx-1);
  paint-order: stroke;
  stroke: var(--ck-lbl-stroke);
  stroke-width: 3.4px;
  stroke-linecap: round;
  stroke-linejoin: round;
  pointer-events: none;
}
.map-lbl-cn {
  fill: var(--ck-gold-tx);
}

.arc-glow {
  fill: none;
  stroke-width: 3.4;
  stroke-opacity: 0.24;
}
.arc-line {
  fill: none;
  stroke-width: 1.8;
  stroke-dasharray: 6 5;
  animation: routeFlow 2.2s linear infinite;
}
@media (prefers-reduced-motion: reduce) {
  .arc-line {
    animation: none;
  }
}
.arc-cn {
  stroke: var(--ck-gold);
}
.arc-intl {
  stroke: var(--ck-emerald);
}

.map-legend-strip {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  padding: 8px 12px;
  background: var(--ck-inner);
  border-top: 1px solid var(--ck-border);
  font-size: 13px;
  color: var(--ck-tx-2);
}
.legend-group {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}
.legend-caption {
  color: var(--ck-tx-muted);
  font-weight: 600;
}
.legend-item {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  white-space: nowrap;
}
.lg-swatch {
  width: 11px;
  height: 11px;
  border-radius: 2px;
  display: inline-block;
}
.lg-swatch.cn {
  background: var(--ck-tier-cn);
}
.lg-swatch.high {
  background: var(--ck-tier-high);
}
.lg-swatch.mid {
  background: var(--ck-tier-mid);
}
.lg-swatch.base {
  background: var(--ck-tier-base);
}
.lg-line {
  width: 16px;
  height: 3px;
  border-radius: 2px;
  display: inline-block;
}
.lg-line.gold {
  background: var(--ck-gold);
}
.lg-line.emerald {
  background: var(--ck-emerald);
}

/* 穿透档案底舱 */
.map-detail-bar {
  background: var(--ck-inner);
  border: 1px solid var(--ck-border);
  border-radius: 8px;
  padding: 11px 14px;
  display: flex;
  flex-direction: column;
  gap: 10px;
  font-size: 13px;
}
.dossier-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  padding-bottom: 8px;
  border-bottom: 1px solid var(--ck-border);
}
.map-detail-title {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  color: var(--ck-tx-1);
}
.dossier-tag {
  font-size: 13px;
  font-weight: 600;
  color: var(--ck-accent);
  background: var(--ck-card);
  border: 1px solid var(--ck-border);
  border-radius: 4px;
  padding: 1px 8px;
}
.dossier-tag.active {
  background: var(--ck-accent);
  border-color: var(--ck-accent);
  color: #ffffff;
}
.dossier-sub {
  color: var(--ck-emerald-tx);
  font-weight: 600;
}
.dossier-actions {
  display: inline-flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}
.map-muted {
  color: var(--ck-tx-muted);
}
.dk-inline-reset {
  border: none;
  background: none;
  color: var(--ck-accent);
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
  padding: 0;
}
.dk-inline-reset:hover {
  text-decoration: underline;
}
.dk-cta-btn {
  border: none;
  background: var(--ck-accent);
  color: #ffffff;
  font-size: 13px;
  font-weight: 600;
  padding: 4px 12px;
  border-radius: 6px;
  cursor: pointer;
  white-space: nowrap;
}

.dossier-grid5 {
  display: grid;
  grid-template-columns: repeat(5, minmax(0, 1fr));
  gap: 10px;
}
@media (max-width: 1180px) {
  .dossier-grid5 {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }
}
@media (max-width: 680px) {
  .dossier-grid5 {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
.dossier-cell {
  display: flex;
  flex-direction: column;
  gap: 3px;
}
.dossier-cell span {
  color: var(--ck-tx-muted);
  font-size: 13px;
}
.dossier-cell strong {
  color: var(--ck-tx-1);
  font-size: 13px;
}

.cockpit-board.is-fullscreen {
  position: fixed;
  inset: 0;
  z-index: 2000;
  overflow-y: auto;
  border-radius: 0;
  padding: 12px 22px 14px;
  gap: 10px;
}
.cockpit-board.is-fullscreen .cockpit-header {
  padding: 9px 14px;
}
.cockpit-board.is-fullscreen .takeaway-ribbon {
  padding: 7px 14px;
}
.cockpit-board.is-fullscreen .dk-card {
  padding: 11px 14px;
}
.cockpit-board.is-fullscreen .dk-stack,
.cockpit-board.is-fullscreen .adm-dark-body {
  gap: 10px;
}
.cockpit-board.is-fullscreen .radar-svg {
  height: 156px;
}
.cockpit-board.is-fullscreen .geo-svg {
  max-height: 448px;
}
</style>
