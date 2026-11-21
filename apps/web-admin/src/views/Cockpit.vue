<script setup lang="ts">
import { MAP_VIEWBOX, WORLD_COUNTRY_PATHS, buildRouteArcPath, getCountryHub } from '@cyshuhe/shared'
import { ElMessage } from 'element-plus'
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter, type RouteLocationRaw } from 'vue-router'
import { api } from '../api'

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

type ViewMode = 'map' | 'charts' | 'list'

const route = useRoute()
const router = useRouter()
const boardRef = ref<HTMLElement | null>(null)
const loading = ref(false)
const viewMode = ref<ViewMode>('map')
const selectedGeoCode = ref<string>('ALL')
const hoveredBarHint = ref<string>('')
const isFullscreen = ref(route.query.fullscreen === '1')
const data = ref<CockpitOverview | null>(null)
const showAllCountries = ref(false)
const fallbackWpOutdated = ref<number | null>(null)
const fallbackStdSplit = ref<{ cn: number; intl: number } | null>(null)

async function load() {
  loading.value = true
  try {
    const [overviewRes, wpRes, stdRes] = await Promise.allSettled([
      api.get<CockpitOverview>('/admin/cockpit/overview'),
      api.get<Array<{ outdated?: boolean | null }>>('/admin/knowledge/whitepapers'),
      api.get<{ by_country?: Record<string, Record<string, number>> }>('/admin/standards/summary'),
    ])
    if (overviewRes.status === 'rejected') {
      throw overviewRes.reason
    }
    data.value = overviewRes.value
    if (wpRes.status === 'fulfilled' && Array.isArray(wpRes.value)) {
      fallbackWpOutdated.value = wpRes.value.filter((w) => w.outdated).length
    }
    if (stdRes.status === 'fulfilled' && stdRes.value?.by_country) {
      const countActive = (m?: Record<string, number>) =>
        Object.entries(m || {})
          .filter(([st]) => st !== 'withdrawn' && st !== 'draft')
          .reduce((acc, [, n]) => acc + Number(n || 0), 0)
      fallbackStdSplit.value = {
        cn: countActive(stdRes.value.by_country.CN),
        intl: countActive(stdRes.value.by_country.ZZ),
      }
    }
  } catch (e) {
    ElMessage.error((e as Error).message)
  } finally {
    loading.value = false
  }
}

function syncFullscreenState() {
  isFullscreen.value = Boolean(document.fullscreenElement)
}

function onFullscreenKeydown(e: KeyboardEvent) {
  if (e.key === 'Escape' && isFullscreen.value && !document.fullscreenElement) {
    isFullscreen.value = false
  }
}

async function toggleFullscreen() {
  const next = !isFullscreen.value
  isFullscreen.value = next
  try {
    if (next && !document.fullscreenElement && boardRef.value?.requestFullscreen) {
      await boardRef.value.requestFullscreen()
    } else if (!next && document.fullscreenElement) {
      await document.exitFullscreen()
    }
  } catch {
    /* 嵌入容器不支持原生全屏时保持视口全屏展示 */
  }
}

const sovereignCountries = computed(() =>
  (data.value?.countries || []).filter((c) => c.country_code !== 'ZZ' && c.country_code !== 'INTL'),
)

const coveredCodeSet = computed(() => new Set(sovereignCountries.value.map((c) => c.country_code)))

const backgroundLandEntries = computed(() =>
  Object.entries(WORLD_COUNTRY_PATHS).filter(([cc]) => !coveredCodeSet.value.has(cc)),
)

const coveredLandEntries = computed(() =>
  sovereignCountries.value
    .map((c) => ({
      code: c.country_code,
      d: WORLD_COUNTRY_PATHS[c.country_code] || '',
      country: c,
    }))
    .filter((x) => Boolean(x.d)),
)

const mapArcs = computed(() => {
  const list = data.value?.routes || []
  const out: Array<{ key: string; d: string; fromCn: boolean }> = []
  list.forEach((rt, idx) => {
    const sc = rt.source_code || ''
    const dc = rt.recipient_code || ''
    const d = buildRouteArcPath(sc, dc, idx)
    if (d) {
      out.push({ key: `${sc}-${dc}-${idx}`, d, fromCn: sc === 'CN' })
    }
  })
  return out
})

function labelOffset(cc: string, hub: [number, number]): { x: number; y: number; anchor: 'start' | 'middle' | 'end' } {
  if (cc === 'CN') return { x: hub[0] - 10, y: hub[1] - 10, anchor: 'end' }
  if (cc === 'SG') return { x: hub[0] + 9, y: hub[1] + 5, anchor: 'start' }
  if (cc === 'MA') return { x: hub[0] - 6, y: hub[1] + 21, anchor: 'start' }
  if (cc === 'TN') return { x: hub[0] - 8, y: hub[1] - 10, anchor: 'middle' }
  if (cc === 'EG') return { x: hub[0] - 4, y: hub[1] + 21, anchor: 'middle' }
  if (cc === 'LB') return { x: hub[0] + 9, y: hub[1] - 3, anchor: 'start' }
  return { x: hub[0] + 8, y: hub[1] - 6, anchor: 'start' }
}

const mapHubMarkers = computed(() =>
  sovereignCountries.value
    .map((c) => {
      const hub = getCountryHub(c.country_code)
      if (!hub) return null
      const lbl = labelOffset(c.country_code, hub)
      return {
        code: c.country_code,
        name: c.country_name_zh,
        laws: c.laws_count,
        cx: hub[0],
        cy: hub[1],
        lx: lbl.x,
        ly: lbl.y,
        anchor: lbl.anchor,
      }
    })
    .filter((x): x is NonNullable<typeof x> => x !== null),
)

const selectedCountry = computed(() => {
  if (selectedGeoCode.value === 'ALL') return null
  return sovereignCountries.value.find((c) => c.country_code === selectedGeoCode.value) || null
})

const sortedKgBreakdown = computed(() =>
  [...(data.value?.kg_node_breakdown || [])].sort((a, b) => b.count - a.count),
)

const visibleCountries = computed(() => {
  const list = data.value?.countries || []
  return showAllCountries.value ? list : list.slice(0, 6)
})

const chartCountries = computed(() => sovereignCountries.value.slice(0, 6))

const chartBarMax = computed(() => {
  const vals = chartCountries.value.flatMap((c) => [c.clauses_count, c.kg_nodes_count])
  return Math.max(100, ...vals)
})

const defaultBarHint = computed(() => {
  if (hoveredBarHint.value) return hoveredBarHint.value
  const first = chartCountries.value[0]
  if (!first) return '暂无国家法规数据'
  return `${first.country_name_zh} · 原始条款 ${first.clauses_count} 条 · 知识图谱要素 ${first.kg_nodes_count} 个`
})

const donutSegments = computed(() => {
  const total = Math.max(1, data.value?.headline.kg_nodes_total || 0)
  const circum = 2 * Math.PI * 56
  const palette = ['#2563eb', '#0ea5e9', '#10b981', '#f59e0b', '#6366f1', '#64748b']
  let offset = 0
  return sortedKgBreakdown.value.map((item, idx) => {
    const len = (item.count / total) * circum
    const seg = {
      label: cleanKgLabel(item.label),
      count: item.count,
      pct: data.value?.headline.kg_nodes_total ? ((item.count / total) * 100).toFixed(1) : '0.0',
      color: palette[idx % palette.length],
      dasharray: `${len} ${circum}`,
      dashoffset: -offset,
    }
    offset += len
    return seg
  })
})

const overseasAdmissionCountries = computed(() =>
  sovereignCountries.value.filter((c) => !isOriginCountry(c) && (c.admission_entries_count > 0 || c.official_forms_count > 0)),
)

const whitepapersOutdated = computed(() => {
  const opVal = data.value?.operations.whitepapers_outdated
  if (typeof opVal === 'number') return opVal
  const hlVal = data.value?.headline.whitepapers_outdated
  if (typeof hlVal === 'number') return hlVal
  return fallbackWpOutdated.value ?? 0
})

const intlStandardsCount = computed(() => {
  const hl = data.value?.headline
  return hl?.intl_standards_count ?? hl?.standards_intl ?? hl?.standards_international ?? fallbackStdSplit.value?.intl ?? 0
})

const standardsSubtitle = computed(() => {
  const hl = data.value?.headline
  const cn = hl?.national_standards_count ?? hl?.standards_cn ?? fallbackStdSplit.value?.cn
  const intl = hl?.intl_standards_count ?? hl?.standards_intl ?? hl?.standards_international ?? fallbackStdSplit.value?.intl
  if (typeof cn === 'number' && typeof intl === 'number' && (cn > 0 || intl > 0)) {
    return `中国标准 ${cn} 项 · 国际通用 ${intl} 项`
  }
  return '涵盖整车信息安全、智能网联与数据合规技术标准'
})

const cleanKgLabel = (label: string) => label.replace(/节点/g, '要素')
const isOriginCountry = (c: CountryPosture) => Boolean(c.is_origin_country || c.country_code === 'CN')
const displayCountryName = (c: CountryPosture) =>
  c.country_code === 'ZZ' ? '国际通用标准' : c.country_name_zh

const maxClauses = computed(() => {
  const vals = (data.value?.countries || []).map((c) => c.clauses_count)
  return Math.max(1, ...vals)
})

const maxKgCount = computed(() => {
  const vals = (data.value?.kg_node_breakdown || []).map((x) => x.count)
  return Math.max(1, ...vals)
})

const maxRouteCount = computed(() => {
  const vals = (data.value?.routes || []).map((x) => x.project_count)
  return Math.max(1, ...vals)
})

const ringOffset = (pct: number | null | undefined, radius = 34) => {
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
  },
  { immediate: true },
)

onMounted(() => {
  document.addEventListener('fullscreenchange', syncFullscreenState)
  window.addEventListener('keydown', onFullscreenKeydown)
  void load()
})

onUnmounted(() => {
  document.documentElement.classList.remove('cy-cockpit-fullscreen')
  document.removeEventListener('fullscreenchange', syncFullscreenState)
  window.removeEventListener('keydown', onFullscreenKeydown)
})
</script>

<template>
  <div
    ref="boardRef"
    class="cockpit-board"
    :class="[`is-mode-${viewMode}`, { 'is-fullscreen': isFullscreen }]"
    v-loading="loading"
    data-testid="super-admin-cockpit"
  >
    <header class="cockpit-header">
      <div>
        <h1>平台总览</h1>
        <p class="cockpit-subtitle">
          汇总现行有效法规、外文译文、知识图谱、海外属地准入与入驻企业项目实时状态
        </p>
      </div>
      <div class="cockpit-actions">
        <div class="view-seg" :class="{ dark: viewMode === 'map' }" role="tablist" aria-label="总览视图切换">
          <button
            type="button"
            class="seg-btn"
            :class="{ active: viewMode === 'map' }"
            data-testid="cockpit-view-map"
            @click="viewMode = 'map'"
          >
            大屏地图视图
          </button>
          <button
            type="button"
            class="seg-btn"
            :class="{ active: viewMode === 'charts' }"
            data-testid="cockpit-view-charts"
            @click="viewMode = 'charts'"
          >
            图表分析视图
          </button>
          <button
            type="button"
            class="seg-btn"
            :class="{ active: viewMode === 'list' }"
            data-testid="cockpit-view-list"
            @click="viewMode = 'list'"
          >
            标准列表视图
          </button>
        </div>
        <el-button type="primary" data-testid="cockpit-fullscreen-toggle" @click="toggleFullscreen">
          {{ isFullscreen ? '退出全屏' : '全屏展示' }}
        </el-button>
      </div>
    </header>

    <!-- ===================================================================
         视图 1（默认）：大屏地图视图（深海夜蓝 #0B1120 + Natural Earth 地理边界）
         =================================================================== -->
    <template v-if="data && viewMode === 'map'">
      <section class="dk-kpi6" aria-label="平台核心指标">
        <button type="button" class="dk-card dk-btn-card" @click="go({ path: '/documents', query: { status: 'in_force', folder: 'all' } })">
          <span class="dk-kpi-lbl">全库在册现行法规</span>
          <strong class="dk-kpi-val">{{ data.headline.laws_in_force }}<small> 部</small></strong>
          <span class="dk-kpi-foot">覆盖 {{ data.headline.covered_jurisdictions }} 国 · {{ data.headline.clauses_total }} 条原始条款</span>
        </button>

        <button type="button" class="dk-card dk-btn-card" @click="go({ path: '/standards', query: { status: 'active' } })">
          <span class="dk-kpi-lbl">现行与即将实施标准</span>
          <strong class="dk-kpi-val">{{ data.headline.standards_active }}<small> 项</small></strong>
          <span class="dk-kpi-foot">{{ standardsSubtitle }}</span>
        </button>

        <button type="button" class="dk-card dk-btn-card" @click="go('/translations')">
          <span class="dk-kpi-lbl">外文条文中文译文覆盖</span>
          <strong class="dk-kpi-val dk-val-emerald">
            {{ data.headline.translation_coverage_pct !== null ? data.headline.translation_coverage_pct : 100 }}<small>%</small>
          </strong>
          <span class="dk-kpi-foot">{{ data.headline.translated_clauses_total }} / {{ data.headline.foreign_clauses_total }} 条已译</span>
        </button>

        <button type="button" class="dk-card dk-btn-card" @click="go('/knowledge-graph')">
          <span class="dk-kpi-lbl">知识图谱关联要素</span>
          <strong class="dk-kpi-val">{{ data.headline.kg_nodes_total }}<small> 个</small></strong>
          <span class="dk-kpi-foot">已梳理 {{ data.headline.kg_edges_total }} 条条文关联关系</span>
        </button>

        <button type="button" class="dk-card dk-btn-card" @click="go('/whitepapers')">
          <span class="dk-kpi-lbl">国家合规简报与准入</span>
          <strong class="dk-kpi-val">{{ data.headline.briefings_ready }}<small> 国简报</small></strong>
          <span class="dk-kpi-foot">{{ data.headline.admission_entries_total }} 项准入 · {{ data.headline.official_forms_total }} 套法定申报表</span>
        </button>

        <button type="button" class="dk-card dk-btn-card" @click="go('/agent-studio')">
          <span class="dk-kpi-lbl">企业评估与准入项目</span>
          <strong class="dk-kpi-val">{{ data.headline.doc_projects_total + data.headline.admission_projects_total }}<small> 个项目</small></strong>
          <span class="dk-kpi-foot">合规评估 {{ data.headline.doc_projects_total }} · 属地准入 {{ data.headline.admission_projects_total }}</span>
        </button>
      </section>

      <section class="adm-dark-body">
        <!-- 左栏 -->
        <div class="dk-stack dk-col-left">
          <div class="dk-card">
            <div class="dk-sec-head">
              <h2>译文覆盖与采集站点状态</h2>
              <span class="dk-link" @click="go('/updates')">更新与时效</span>
            </div>
            <div class="dk-ring-pair">
              <div class="dk-ring-col" @click="go('/translations')">
                <svg viewBox="0 0 64 64" width="56" height="56" aria-hidden="true">
                  <circle cx="32" cy="32" r="25" fill="none" stroke="#1e293b" stroke-width="6" />
                  <circle
                    v-if="data.headline.translation_coverage_pct !== null"
                    cx="32"
                    cy="32"
                    r="25"
                    fill="none"
                    stroke="#38bdf8"
                    stroke-width="6"
                    :stroke-dasharray="2 * Math.PI * 25"
                    :stroke-dashoffset="ringOffset(data.headline.translation_coverage_pct, 25)"
                    transform="rotate(-90 32 32)"
                  />
                  <text x="32" y="37" text-anchor="middle" fill="#f8fafc" font-size="13" font-weight="600">
                    {{ data.headline.translation_coverage_pct !== null ? `${Math.round(data.headline.translation_coverage_pct)}%` : '—' }}
                  </text>
                </svg>
                <div>
                  <strong class="dk-ring-title">译文覆盖</strong>
                  <span class="dk-ring-sub">{{ data.headline.translated_clauses_total }} 条</span>
                </div>
              </div>

              <div class="dk-ring-col" @click="go(data.operations.attention_sites_total > 0 ? '/sites?attention=1' : '/sites')">
                <svg viewBox="0 0 64 64" width="56" height="56" aria-hidden="true">
                  <circle cx="32" cy="32" r="25" fill="none" stroke="#1e293b" stroke-width="6" />
                  <circle
                    v-if="siteHealthyPct !== null"
                    cx="32"
                    cy="32"
                    r="25"
                    fill="none"
                    :stroke="data.operations.attention_sites_total > 0 ? '#f59e0b' : '#10b981'"
                    stroke-width="6"
                    :stroke-dasharray="2 * Math.PI * 25"
                    :stroke-dashoffset="ringOffset(siteHealthyPct, 25)"
                    transform="rotate(-90 32 32)"
                  />
                  <text x="32" y="37" text-anchor="middle" fill="#f8fafc" font-size="13" font-weight="600">
                    {{ siteHealthyPct !== null ? `${Math.round(siteHealthyPct)}%` : '—' }}
                  </text>
                </svg>
                <div>
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
              <h2>知识图谱要素结构分布</h2>
              <span class="dk-link" @click="go('/knowledge-graph')">打开知识图谱</span>
            </div>
            <div
              v-for="item in sortedKgBreakdown"
              :key="item.label"
              class="dk-bar-item"
              @click="go('/knowledge-graph')"
            >
              <div class="dk-bar-top">
                <span>{{ cleanKgLabel(item.label) }}</span>
                <strong>{{ item.count }} 个</strong>
              </div>
              <div class="dk-track">
                <div
                  class="dk-fill"
                  :class="{ emerald: item.label.includes('罚则') }"
                  :style="{ width: `${Math.max(4, Math.round((item.count / maxKgCount) * 100))}%` }"
                />
              </div>
            </div>
          </div>
        </div>

        <!-- 中栏：世界地理边界与动态跨境流向 -->
        <div class="map-frame dk-col-center">
          <div class="dk-sec-head map-top-head">
            <h2>重点法域法规覆盖与跨境业务流向分布</h2>
            <span class="map-hint">点击高亮国家或右侧列表切换明细</span>
          </div>

          <svg class="geo-svg" :viewBox="MAP_VIEWBOX" role="img" aria-label="全球重点法域法规覆盖与跨境流向地图">
            <g stroke="#131e33" stroke-width="1">
              <line x1="0" y1="100" x2="840" y2="100" />
              <line x1="0" y1="200" x2="840" y2="200" />
              <line x1="0" y1="300" x2="840" y2="300" />
              <line x1="210" y1="0" x2="210" y2="400" />
              <line x1="420" y1="0" x2="420" y2="400" />
              <line x1="630" y1="0" x2="630" y2="400" />
            </g>

            <path
              v-for="[cc, d] in backgroundLandEntries"
              :key="`bg-${cc}`"
              class="geo-land"
              :d="d"
            />

            <path
              v-for="item in coveredLandEntries"
              :key="`cov-${item.code}`"
              class="geo-land geo-covered"
              :class="{ active: selectedGeoCode === item.code }"
              :d="item.d"
              @click="selectedGeoCode = item.code"
            />

            <path
              v-for="arc in mapArcs"
              :key="arc.key"
              class="arc-line"
              :class="arc.fromCn ? 'arc-cn' : 'arc-intl'"
              :d="arc.d"
            />

            <g
              v-for="m in mapHubMarkers"
              :key="`hub-${m.code}`"
              class="hub-group"
              @click="selectedGeoCode = m.code"
            >
              <circle
                :cx="m.cx"
                :cy="m.cy"
                :r="selectedGeoCode === m.code ? 6 : 4.8"
                :fill="selectedGeoCode === m.code ? '#10b981' : '#38bdf8'"
                stroke="#ffffff"
                stroke-width="1.5"
              />
              <text
                class="map-lbl"
                :x="m.lx"
                :y="m.ly"
                :text-anchor="m.anchor"
                fill="#f8fafc"
                font-size="13"
                font-weight="600"
              >
                {{ m.name }} ({{ m.laws }}部)
              </text>
            </g>
          </svg>

          <div class="map-detail-bar" data-testid="cockpit-map-detail">
            <template v-if="!selectedCountry">
              <div class="map-detail-title">
                <span class="map-muted">当前范围：</span>
                <strong>全库已覆盖 {{ sovereignCountries.length }} 个国家与地区</strong>
              </div>
              <div class="map-detail-metrics">
                <span>在册现行法规 <strong>{{ data.headline.laws_in_force }} 部 / {{ data.headline.clauses_total }} 条</strong></span>
                <span>中文译文覆盖 <strong>{{ data.headline.translation_coverage_pct !== null ? `${data.headline.translation_coverage_pct}%` : '100%' }}</strong></span>
                <span>知识图谱要素 <strong>{{ data.headline.kg_nodes_total }} 个</strong></span>
                <span>准入与申报表 <strong>{{ data.headline.admission_entries_total }} 项 / {{ data.headline.official_forms_total }} 套</strong></span>
                <span>官方采集站点 <strong>{{ data.operations.monitor_sites_total - data.operations.attention_sites_total }} 个运行正常</strong></span>
              </div>
            </template>
            <template v-else>
              <div class="map-detail-title">
                <span class="map-muted">当前查看：</span>
                <strong>{{ selectedCountry.country_name_zh }}</strong>
                <button type="button" class="dk-inline-reset" @click="selectedGeoCode = 'ALL'">返回全部</button>
              </div>
              <div class="map-detail-metrics">
                <span>在册现行法规 <strong>{{ selectedCountry.laws_count }} 部 / {{ selectedCountry.clauses_count }} 条</strong></span>
                <span>译文状态 <strong>{{ selectedCountry.is_zh_native ? '中文原文直接可读' : `${selectedCountry.translation_pct ?? 100}%` }}</strong></span>
                <span>知识图谱要素 <strong>{{ selectedCountry.kg_nodes_count }} 个</strong></span>
                <span>准入与申报表 <strong>{{ selectedCountry.admission_entries_count }} 项 / {{ selectedCountry.official_forms_count }} 套</strong></span>
                <button type="button" class="dk-inline-reset" @click="go({ path: '/documents', query: { country: selectedCountry.country_code } })">
                  进入该国法规 →
                </button>
              </div>
            </template>
          </div>
        </div>

        <!-- 右栏：国家列表（支持内部滚动）+ 账号与审计概况 -->
        <div class="dk-stack dk-col-right">
          <div class="dk-card">
            <div class="dk-sec-head">
              <h2>各国现行法规与条文收录</h2>
              <span class="dk-link" @click="go('/documents')">全部法规 →</span>
            </div>
            <div class="dk-country-scroll">
              <div
                v-for="c in sovereignCountries"
                :key="`dk-c-${c.country_code}`"
                class="dk-bar-item dk-country-row"
                :class="{ selected: selectedGeoCode === c.country_code }"
                @click="selectedGeoCode = c.country_code"
              >
                <div class="dk-bar-top">
                  <span>{{ c.country_name_zh }}</span>
                  <span class="dk-country-right">
                    <strong>{{ c.laws_count }} 部 · {{ c.clauses_count }} 条</strong>
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
                    :class="{ emerald: selectedGeoCode === c.country_code }"
                    :style="{ width: `${c.clauses_count > 0 ? Math.max(8, Math.round((c.clauses_count / maxClauses) * 100)) : 4}%` }"
                  />
                </div>
              </div>
              <p v-if="!sovereignCountries.length" class="dk-empty">当前暂无已收录国家法规数据</p>
            </div>
          </div>

          <div class="dk-card dk-flex1">
            <div class="dk-sec-head">
              <h2>平台账号与运行审计概况</h2>
              <span class="dk-link" @click="go('/audit-logs')">审计记录 →</span>
            </div>
            <div class="dk-row dk-row-click" @click="go('/orgs')">
              <span>入驻企业单位</span>
              <strong>{{ data.headline.active_organizations }} 家单位</strong>
            </div>
            <div class="dk-row dk-row-click" @click="go('/orgs')">
              <span>管理端与企业账号</span>
              <strong>管理 {{ data.account_summary.super_admins + data.account_summary.ops_admins }} · 企业 {{ data.account_summary.enterprise_members + data.account_summary.ordinary_viewers }}</strong>
            </div>
            <div v-if="data.account_summary.trial_visitors" class="dk-row dk-row-click" @click="go('/orgs')">
              <span>独立体验空间账号</span>
              <strong>{{ data.account_summary.trial_visitors }} 个账号</strong>
            </div>
            <div class="dk-row dk-row-click" @click="go('/agent-studio')">
              <span>已覆盖跨境业务流向</span>
              <strong>{{ data.routes.length }} 条路线</strong>
            </div>
            <div class="dk-row dk-row-click" @click="go('/opinion')">
              <span>舆情监测累计收录</span>
              <strong>{{ data.headline.opinion_items_total }} 条动态</strong>
            </div>
            <div class="dk-row dk-row-click" @click="go('/audit-logs')">
              <span>近 7 日操作审计记录</span>
              <strong>{{ data.operations.audit_events_7d }} 条记录</strong>
            </div>
          </div>
        </div>
      </section>
    </template>

    <!-- ===================================================================
         视图 2：图表分析视图（白底单层卡片多维对比图）
         =================================================================== -->
    <template v-else-if="data && viewMode === 'charts'">
      <section class="charts-kpi4">
        <button type="button" class="kpi-tile" @click="go({ path: '/documents', query: { status: 'in_force', folder: 'all' } })">
          <span class="kpi-label">在册现行法规与条款</span>
          <strong class="kpi-num">{{ data.headline.laws_in_force }}<small> 部 / {{ data.headline.clauses_total }} 条</small></strong>
          <span class="kpi-meta">外文条文中文译文覆盖 {{ data.headline.translation_coverage_pct ?? 100 }}%（{{ data.headline.translated_clauses_total }} 条）</span>
        </button>

        <button type="button" class="kpi-tile" @click="go({ path: '/standards', query: { status: 'active' } })">
          <span class="kpi-label">现行与即将实施标准</span>
          <strong class="kpi-num">{{ data.headline.standards_active }}<small> 项技术标准</small></strong>
          <span class="kpi-meta">{{ standardsSubtitle }}</span>
        </button>

        <button type="button" class="kpi-tile" @click="go('/knowledge-graph')">
          <span class="kpi-label">法规知识图谱规模</span>
          <strong class="kpi-num">{{ data.headline.kg_nodes_total }}<small> 个关联要素</small></strong>
          <span class="kpi-meta">已梳理 {{ data.headline.kg_edges_total }} 条法规条文关联关系</span>
        </button>

        <button type="button" class="kpi-tile" @click="go('/admission')">
          <span class="kpi-label">国家简报、准入与申报表</span>
          <strong class="kpi-num">{{ data.headline.briefings_ready }}<small> 国简报 / {{ data.headline.admission_entries_total }} 项准入</small></strong>
          <span class="kpi-meta">{{ data.headline.official_forms_total }} 套法定申报表 · {{ data.operations.monitor_sites_total }} 个站点监测中</span>
        </button>
      </section>

      <section class="charts-row-7-5">
        <div class="posture-panel">
          <div class="panel-head">
            <h2>重点国家法规条文与知识图谱要素对比</h2>
            <div class="chart-legend">
              <span><i class="lg-dot" style="background:#2563eb;" />原始条款数</span>
              <span><i class="lg-dot" style="background:#10b981;" />知识图谱要素数</span>
            </div>
          </div>

          <svg v-if="chartCountries.length" viewBox="0 0 680 225" class="bar-chart-svg" role="img" aria-label="各国条文与图谱要素柱状对比">
            <g stroke="#e2e8f0" stroke-width="1">
              <line x1="58" y1="20" x2="660" y2="20" />
              <line x1="58" y1="64" x2="660" y2="64" />
              <line x1="58" y1="108" x2="660" y2="108" />
              <line x1="58" y1="152" x2="660" y2="152" />
              <line x1="58" y1="192" x2="660" y2="192" stroke="#cbd5e1" />
            </g>
            <g fill="#64748b" font-size="13" text-anchor="end">
              <text x="50" y="25">{{ chartBarMax }}</text>
              <text x="50" y="69">{{ Math.round(chartBarMax * 0.75) }}</text>
              <text x="50" y="113">{{ Math.round(chartBarMax * 0.5) }}</text>
              <text x="50" y="157">{{ Math.round(chartBarMax * 0.25) }}</text>
              <text x="50" y="196">0</text>
            </g>
            <g
              v-for="(c, idx) in chartCountries"
              :key="`bar-${c.country_code}`"
              class="bar-pair-group"
              @mouseenter="hoveredBarHint = `${c.country_name_zh} · 原始条款 ${c.clauses_count} 条 · 知识图谱要素 ${c.kg_nodes_count} 个`"
              @click="go({ path: '/documents', query: { country: c.country_code } })"
            >
              <rect
                :x="84 + idx * 98"
                :y="192 - Math.max(4, Math.round((c.clauses_count / chartBarMax) * 170))"
                width="22"
                :height="Math.max(4, Math.round((c.clauses_count / chartBarMax) * 170))"
                rx="3"
                fill="#2563eb"
              />
              <rect
                :x="110 + idx * 98"
                :y="192 - Math.max(4, Math.round((c.kg_nodes_count / chartBarMax) * 170))"
                width="22"
                :height="Math.max(4, Math.round((c.kg_nodes_count / chartBarMax) * 170))"
                rx="3"
                fill="#10b981"
              />
              <text :x="108 + idx * 98" y="215" text-anchor="middle" fill="#20323f" font-size="13" font-weight="600">
                {{ c.country_name_zh }} ({{ c.laws_count }}部)
              </text>
            </g>
          </svg>
          <div class="chart-foot-bar">
            <span><strong>当前聚焦：</strong>{{ defaultBarHint }}</span>
            <span>点击柱状图查看对应国家法规</span>
          </div>
        </div>

        <div class="posture-panel">
          <div class="panel-head">
            <h2>知识图谱要素构成占比</h2>
            <el-button link type="primary" @click="go('/knowledge-graph')">打开知识图谱</el-button>
          </div>
          <div class="donut-wrap">
            <svg viewBox="0 0 150 150" width="154" height="154" aria-hidden="true">
              <circle cx="75" cy="75" r="56" fill="none" stroke="#f1f5f9" stroke-width="18" />
              <circle
                v-for="seg in donutSegments"
                :key="seg.label"
                cx="75"
                cy="75"
                r="56"
                fill="none"
                :stroke="seg.color"
                stroke-width="18"
                :stroke-dasharray="seg.dasharray"
                :stroke-dashoffset="seg.dashoffset"
                transform="rotate(-90 75 75)"
              />
              <text x="75" y="72" text-anchor="middle" fill="#20323f" font-size="18" font-weight="600">
                {{ data.headline.kg_nodes_total }}
              </text>
              <text x="75" y="92" text-anchor="middle" fill="#4b5e6c" font-size="13">要素总数</text>
            </svg>
            <div class="donut-legend">
              <div v-for="seg in donutSegments" :key="`lg-${seg.label}`" class="donut-row">
                <span><i class="lg-dot" :style="{ background: seg.color }" />{{ seg.label }}</span>
                <strong>{{ seg.count }} ({{ seg.pct }}%)</strong>
              </div>
            </div>
          </div>
        </div>
      </section>

      <section class="charts-row-5-7">
        <div class="posture-panel">
          <div class="panel-head">
            <h2>海外属地准入事项与法定申报表</h2>
            <el-button link type="primary" @click="go('/admission')">海外准入事项</el-button>
          </div>
          <table v-if="overseasAdmissionCountries.length" class="clean-adm-table">
            <thead>
              <tr>
                <th>目标国家</th>
                <th>准入事项</th>
                <th>法定申报表</th>
                <th>国家合规简报</th>
              </tr>
            </thead>
            <tbody>
              <tr
                v-for="c in overseasAdmissionCountries"
                :key="`adm-row-${c.country_code}`"
                @click="go({ path: '/admission', query: { country: c.country_code } })"
              >
                <td><strong>{{ c.country_name_zh }}</strong></td>
                <td>{{ c.admission_entries_count }} 项</td>
                <td>{{ c.official_forms_count }} 套</td>
                <td>
                  <span :class="c.briefing_ready ? 'cy-status cy-status--done' : 'cy-status cy-status--muted'">
                    {{ c.briefing_ready ? '已就绪' : '待生成' }}
                  </span>
                </td>
              </tr>
            </tbody>
          </table>
          <p v-else class="empty-inline">暂无海外属地准入事项记录</p>
        </div>

        <div class="posture-panel">
          <div class="panel-head">
            <h2>质量核验与法规时效监控状态</h2>
            <el-button link type="primary" @click="go('/updates')">更新与时效</el-button>
          </div>
          <div class="ops-clean-list">
            <div class="ops-clean-row" @click="go('/intake-quality')">
              <div>
                <strong class="ops-clean-title">入库质量检查</strong>
                <span class="ops-clean-sub">{{ data.headline.laws_in_force }} 部在册现行有效法规格式、条号与完整性核验</span>
              </div>
              <span :class="data.operations.quality_unpassed > 0 ? 'cy-status cy-status--warn' : 'cy-status cy-status--done'">
                {{ data.operations.quality_unpassed > 0 ? `${data.operations.quality_unpassed} 部待复查` : '全部通过检查' }}
              </span>
              <span class="ops-clean-act">查看明细 →</span>
            </div>
            <div class="ops-clean-row" @click="go(data.operations.attention_sites_total > 0 ? '/sites?attention=1' : '/sites')">
              <div>
                <strong class="ops-clean-title">官方采集站点巡检</strong>
                <span class="ops-clean-sub">覆盖各国立法公报、数据保护与交通通信主管机构共 {{ data.operations.monitor_sites_total }} 个站点</span>
              </div>
              <span :class="data.operations.attention_sites_total > 0 ? 'cy-status cy-status--warn' : 'cy-status cy-status--done'">
                {{ data.operations.attention_sites_total > 0 ? `${data.operations.attention_sites_total} 站待处理` : `${data.operations.monitor_sites_total} 站运行正常` }}
              </span>
              <span class="ops-clean-act">采集站点 →</span>
            </div>
            <div class="ops-clean-row" @click="go('/updates')">
              <div>
                <strong class="ops-clean-title">法规条文修订与简报联动</strong>
                <span class="ops-clean-sub">监测到条文变更后 72 小时内联动更新关联国家简报与项目提醒</span>
              </div>
              <span :class="data.operations.pending_updates > 0 ? 'cy-status cy-status--warn' : 'cy-status cy-status--done'">
                {{ data.operations.pending_updates > 0 ? `${data.operations.pending_updates} 项待确认` : '已全部确认' }}
              </span>
              <span class="ops-clean-act">更新记录 →</span>
            </div>
          </div>
        </div>
      </section>
    </template>

    <!-- ===================================================================
         视图 3：标准列表视图（逐国明细表格与路由分布）
         =================================================================== -->
    <template v-else-if="data">
      <section class="kpi-strip" aria-label="核心指标">
        <button
          type="button"
          class="kpi-tile"
          @click="go({ path: '/documents', query: { status: 'in_force', folder: 'all' } })"
        >
          <span class="kpi-label">全库在册现行法规</span>
          <strong class="kpi-num">{{ data.headline.laws_in_force }}<small> 部</small></strong>
          <span class="kpi-meta">
            覆盖 {{ data.headline.covered_jurisdictions }} 国 · {{ data.headline.clauses_total }} 条原始条款
          </span>
        </button>

        <button
          type="button"
          class="kpi-tile"
          @click="go({ path: '/standards', query: { status: 'active' } })"
        >
          <span class="kpi-label">现行与即将实施标准</span>
          <strong class="kpi-num">{{ data.headline.standards_active }}<small> 项</small></strong>
          <span class="kpi-meta">{{ standardsSubtitle }}</span>
        </button>

        <button type="button" class="kpi-tile" @click="go('/knowledge-graph')">
          <span class="kpi-label">知识图谱关联要素</span>
          <strong class="kpi-num">{{ data.headline.kg_nodes_total }}<small> 个</small></strong>
          <span class="kpi-meta">已梳理 {{ data.headline.kg_edges_total }} 条法规条文关联关系</span>
        </button>

        <div class="kpi-tile kpi-tile--split">
          <button type="button" class="kpi-hotspot" @click="go('/whitepapers')">
            <span class="kpi-label">国家合规简报与准入事项</span>
            <strong class="kpi-num">{{ data.headline.briefings_ready }}<small> 国简报</small></strong>
          </button>
          <button type="button" class="kpi-hotspot kpi-hotspot--sub" @click="go('/admission')">
            <span class="kpi-meta kpi-meta--link">
              {{ data.headline.admission_entries_total }} 项属地准入 · {{ data.headline.official_forms_total }} 套法定申报表
            </span>
          </button>
        </div>

        <button type="button" class="kpi-tile" @click="go('/orgs')">
          <span class="kpi-label">入驻企业与平台账号</span>
          <strong class="kpi-num">{{ data.headline.active_organizations }}<small> 家单位</small></strong>
          <span class="kpi-meta">企业用户 {{ data.account_summary.enterprise_members }} · 普通查看者 {{ data.account_summary.ordinary_viewers }}<template v-if="data.account_summary.trial_visitors"> · 独立体验 {{ data.account_summary.trial_visitors }}</template></span>
        </button>

        <button type="button" class="kpi-tile" @click="go('/agent-studio')">
          <span class="kpi-label">企业评估与准入项目</span>
          <strong class="kpi-num">{{ data.headline.doc_projects_total + data.headline.admission_projects_total }}<small> 个项目</small></strong>
          <span class="kpi-meta">合规评估 {{ data.headline.doc_projects_total }} · 属地准入 {{ data.headline.admission_projects_total }} · 咨询对话 {{ data.headline.consulting_chats_total }}</span>
        </button>
      </section>

      <section class="posture-grid">
        <div class="posture-panel">
          <div class="panel-head">
            <h2>外文译文与采集站点状态</h2>
            <el-button link type="primary" @click="go('/updates')">更新与时效</el-button>
          </div>
          <div class="ring-row">
            <div class="ring-item" @click="go('/translations')">
              <svg viewBox="0 0 84 84" class="ring-svg" aria-hidden="true">
                <circle cx="42" cy="42" r="34" class="ring-track" />
                <circle
                  v-if="data.headline.translation_coverage_pct !== null"
                  cx="42"
                  cy="42"
                  r="34"
                  class="ring-fill ring-fill--blue"
                  :stroke-dasharray="2 * Math.PI * 34"
                  :stroke-dashoffset="ringOffset(data.headline.translation_coverage_pct)"
                />
              </svg>
              <div class="ring-info">
                <span class="ring-title">外文条文中文译文覆盖</span>
                <strong class="ring-val">
                  {{ data.headline.translation_coverage_pct !== null ? `${data.headline.translation_coverage_pct}%` : '无外文项' }}
                </strong>
                <span class="ring-sub">{{ data.headline.translated_clauses_total }} / {{ data.headline.foreign_clauses_total }} 条已译</span>
              </div>
            </div>

            <div class="ring-item" @click="go(data.operations.attention_sites_total > 0 ? '/sites?attention=1' : '/sites')">
              <svg viewBox="0 0 84 84" class="ring-svg" aria-hidden="true">
                <circle cx="42" cy="42" r="34" class="ring-track" />
                <circle
                  v-if="siteHealthyPct !== null"
                  cx="42"
                  cy="42"
                  r="34"
                  class="ring-fill"
                  :class="data.operations.attention_sites_total > 0 ? 'ring-fill--amber' : 'ring-fill--green'"
                  :stroke-dasharray="2 * Math.PI * 34"
                  :stroke-dashoffset="ringOffset(siteHealthyPct)"
                />
              </svg>
              <div class="ring-info">
                <span class="ring-title">官方采集站点正常率</span>
                <strong class="ring-val">{{ siteHealthyPct !== null ? `${siteHealthyPct}%` : '未登记' }}</strong>
                <span class="ring-sub">共 {{ data.operations.monitor_sites_total }} 个站点 · {{ data.operations.attention_sites_total }} 个待处理</span>
              </div>
            </div>
          </div>

          <div class="ops-list">
            <div class="ops-row" @click="go('/intake-quality')">
              <span>入库质量待复查法规</span>
              <span :class="data.operations.quality_unpassed > 0 ? 'cy-status cy-status--warn' : 'cy-status cy-status--done'">
                {{ data.operations.quality_unpassed > 0 ? `${data.operations.quality_unpassed} 部待复查` : '全部通过检查' }}
              </span>
            </div>
            <div class="ops-row" @click="go('/updates')">
              <span>待确认的法规条文修订</span>
              <span :class="data.operations.pending_updates > 0 ? 'cy-status cy-status--warn' : 'cy-status cy-status--done'">
                {{ data.operations.pending_updates > 0 ? `${data.operations.pending_updates} 项待确认` : '已全部确认' }}
              </span>
            </div>
            <div class="ops-row" @click="go('/whitepapers')">
              <span>国家合规简报待更新</span>
              <span :class="whitepapersOutdated > 0 ? 'cy-status cy-status--warn' : 'cy-status cy-status--done'">
                {{ whitepapersOutdated > 0 ? `${whitepapersOutdated} 国待更新` : '均为最新' }}
              </span>
            </div>
            <div class="ops-row" @click="go('/audit-logs')">
              <span>近 7 日操作审计记录</span>
              <span class="ops-plain">{{ data.operations.audit_events_7d }} 条记录</span>
            </div>
          </div>
        </div>

        <div class="posture-panel">
          <div class="panel-head">
            <h2>知识图谱要素结构分布</h2>
            <el-button link type="primary" @click="go('/knowledge-graph')">打开知识图谱</el-button>
          </div>
          <div class="bar-stack">
            <div v-for="item in sortedKgBreakdown" :key="item.label" class="bar-row" @click="go('/knowledge-graph')">
              <div class="bar-row__top">
                <span>{{ cleanKgLabel(item.label) }}</span>
                <strong class="nowrap">{{ item.count }} 个</strong>
              </div>
              <div class="bar-track">
                <div class="bar-fill" :style="{ width: `${Math.max(4, Math.round((item.count / maxKgCount) * 100))}%` }" />
              </div>
            </div>
          </div>
        </div>

        <div class="posture-panel">
          <div class="panel-head">
            <div class="panel-title-inline">
              <h2 class="nowrap-title">跨境业务流向与四级账号分布</h2>
              <span v-if="data.routes.length" class="route-count-note">共 {{ data.routes.length }} 条路线</span>
            </div>
            <el-button link type="primary" @click="go('/orgs')">企业与账号</el-button>
          </div>
          <div
            v-if="data.routes.length"
            class="bar-stack"
            :class="{ 'bar-stack--routes-grid': data.routes.length > 4 }"
            data-testid="cockpit-routes-list"
          >
            <div v-for="rt in data.routes" :key="rt.route_label" class="bar-row" @click="go('/agent-studio')">
              <div class="bar-row__top">
                <span class="route-label-text">{{ rt.route_label }}</span>
                <strong class="nowrap">{{ rt.project_count }} 个项目</strong>
              </div>
              <div class="bar-track">
                <div class="bar-fill bar-fill--teal" :style="{ width: `${Math.max(8, Math.round((rt.project_count / maxRouteCount) * 100))}%` }" />
              </div>
            </div>
          </div>
          <p v-else class="empty-inline">暂无跨境合规评估项目记录</p>

          <div class="role-mini-grid">
            <div class="role-mini" @click="go('/orgs')">
              <span>超级管理员</span>
              <strong>{{ data.account_summary.super_admins }}</strong>
            </div>
            <div class="role-mini" @click="go('/orgs')">
              <span>运维管理员</span>
              <strong>{{ data.account_summary.ops_admins }}</strong>
            </div>
            <div class="role-mini" @click="go('/orgs')">
              <span>企业用户</span>
              <strong>{{ data.account_summary.enterprise_members }}</strong>
            </div>
            <div class="role-mini" @click="go('/orgs')">
              <span>普通查看者</span>
              <strong>{{ data.account_summary.ordinary_viewers }}</strong>
            </div>
          </div>
        </div>
      </section>

      <section class="country-panel">
        <div class="panel-head">
          <div class="panel-title-inline">
            <h2>各国与地区法规收录及项目分布</h2>
          </div>
          <el-button
            v-if="data.countries.length > 6"
            size="small"
            @click="showAllCountries = !showAllCountries"
          >
            {{ showAllCountries ? '只看核心法域' : `展开全部 ${data.countries.length} 个国家与地区` }}
          </el-button>
        </div>

        <div v-if="visibleCountries.length" class="country-matrix" data-testid="cockpit-country-matrix">
          <div class="matrix-header">
            <span>国家与地区</span>
            <span>在册现行法规与原始条款</span>
            <span>中文可读与译文进度</span>
            <span>知识图谱与技术标准</span>
            <span>简报与属地准入配套</span>
            <span>采集站点与企业项目</span>
          </div>

          <div v-for="c in visibleCountries" :key="c.country_code" class="matrix-row">
            <div class="c-name">
              <strong>{{ displayCountryName(c) }}</strong>
            </div>

            <button
              type="button"
              class="c-cell"
              @click="go({ path: '/documents', query: { country: c.country_code } })"
            >
              <div class="c-cell__top">
                <strong class="nowrap">{{ c.laws_count }} 部法规</strong>
                <span class="nowrap">{{ c.clauses_count }} 条</span>
              </div>
              <div class="bar-track bar-track--sm">
                <div
                  class="bar-fill"
                  :style="{ width: `${c.clauses_count > 0 ? Math.max(5, Math.round((c.clauses_count / maxClauses) * 100)) : 0}%` }"
                />
              </div>
            </button>

            <button
              type="button"
              class="c-cell"
              @click="go({ path: '/translations', query: { country: c.country_code } })"
            >
              <template v-if="c.is_zh_native">
                <div class="c-cell__top">
                  <span class="cy-status cy-status--done">中文原文</span>
                  <span class="nowrap">{{ c.clauses_count }} 条直接可读</span>
                </div>
              </template>
              <template v-else-if="c.translation_pct !== null">
                <div class="c-cell__top">
                  <strong class="nowrap">{{ c.translation_pct }}%</strong>
                  <span class="nowrap">已译 {{ c.translated_clauses_count }} / {{ c.foreign_clauses_count }}</span>
                </div>
                <div class="bar-track bar-track--sm">
                  <div class="bar-fill bar-fill--green" :style="{ width: `${Math.max(4, c.translation_pct)}%` }" />
                </div>
              </template>
              <template v-else>
                <span class="c-muted">暂无外文条文</span>
              </template>
            </button>

            <div class="c-cell c-cell--split">
              <button
                type="button"
                class="c-subbtn"
                @click="go({ path: '/knowledge-graph', query: { country: c.country_code } })"
              >
                <strong class="nowrap">图谱要素 {{ c.kg_nodes_count }} 个</strong>
                <span class="nowrap">关联 {{ c.kg_edges_count }} 条</span>
              </button>
              <button
                type="button"
                class="c-subbtn c-subbtn--sub"
                @click="go({ path: '/standards', query: { country: c.country_code } })"
              >
                <span class="c-muted nowrap">{{ c.standards_count > 0 ? `技术标准 ${c.standards_count} 项` : `国际标准 ${intlStandardsCount} 项` }}</span>
              </button>
            </div>

            <button
              v-if="isOriginCountry(c)"
              type="button"
              class="c-cell"
              @click="go('/document-templates')"
            >
              <div class="c-cell__top">
                <span class="cy-status cy-status--done">国内出境监管</span>
              </div>
              <span class="c-muted nowrap">网信办法定申报表 {{ c.official_forms_count || 4 }} 套</span>
            </button>
            <div v-else class="c-cell c-cell--split">
              <button
                type="button"
                class="c-subbtn"
                @click="go({ path: '/whitepapers', query: { country: c.country_code } })"
              >
                <span :class="c.briefing_ready ? 'cy-status cy-status--done' : 'cy-status cy-status--muted'">
                  {{ c.briefing_ready ? '合规简报已就绪' : '简报待生成' }}
                </span>
              </button>
              <button
                type="button"
                class="c-subbtn c-subbtn--sub"
                @click="go({ path: '/admission', query: { country: c.country_code } })"
              >
                <span class="c-muted">
                  <span class="nowrap">准入 {{ c.admission_entries_count }} 项</span>
                  ·
                  <span class="nowrap">申报表 {{ c.official_forms_count }} 套</span>
                </span>
              </button>
            </div>

            <button
              type="button"
              class="c-cell"
              @click="
                go({
                  path: '/sites',
                  query:
                    c.attention_sites_count > 0
                      ? { country: c.country_code, attention: '1' }
                      : { country: c.country_code },
                })
              "
            >
              <div class="c-cell__top">
                <span class="nowrap">站点 {{ c.monitor_sites_count }} 个</span>
                <span v-if="c.attention_sites_count > 0" class="cy-status cy-status--warn">
                  {{ c.attention_sites_count }} 个待处理
                </span>
                <span v-else-if="c.monitor_sites_count > 0" class="cy-status cy-status--done">正常</span>
              </div>
              <span class="c-muted nowrap">关联项目 {{ c.enterprise_projects_count }} 个</span>
            </button>
          </div>
        </div>
      </section>
    </template>
  </div>
</template>

<style scoped>
@keyframes routeFlow {
  to { stroke-dashoffset: -36; }
}

.cockpit-board {
  display: flex;
  flex-direction: column;
  gap: 20px;
  color: #20323f;
  font-variant-numeric: tabular-nums;
}

.cockpit-board.is-mode-map {
  background: #0b1120;
  border: 1px solid #1e293b;
  border-radius: 10px;
  padding: 20px 24px 24px;
  color: #f1f5f9;
}

.cockpit-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 16px;
  flex-wrap: wrap;
}
.cockpit-header h1 {
  margin: 0;
  font-size: 24px;
  font-weight: 600;
  line-height: 1.35;
}
.cockpit-board.is-mode-map .cockpit-header h1 {
  color: #f8fafc;
}
.cockpit-subtitle {
  margin: 4px 0 0;
  font-size: 13px;
  color: #4b5e6c;
}
.cockpit-board.is-mode-map .cockpit-subtitle {
  color: #94a3b8;
}
.cockpit-actions {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}

.view-seg {
  display: inline-flex;
  background: #e2e8f0;
  padding: 4px;
  border-radius: 6px;
  gap: 4px;
}
.view-seg.dark {
  background: #1e293b;
  border: 1px solid #334155;
}
.seg-btn {
  height: 28px;
  padding: 0 12px;
  font-size: 13px;
  font-weight: 600;
  border: none;
  border-radius: 4px;
  background: transparent;
  color: #4b5e6c;
  cursor: pointer;
  white-space: nowrap;
}
.view-seg.dark .seg-btn {
  color: #94a3b8;
}
.seg-btn.active {
  background: #ffffff;
  color: #20323f;
}
.view-seg.dark .seg-btn.active {
  background: #2563eb;
  color: #ffffff;
}

/* =========================================================================
   深色大屏地图视图样式
   ========================================================================= */
.dk-kpi6 {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 14px;
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
  background: #111827;
  border: 1px solid #1f2937;
  border-radius: 8px;
  padding: 16px;
}
.dk-btn-card {
  text-align: left;
  cursor: pointer;
  color: inherit;
  font: inherit;
  transition: border-color 0.15s;
}
.dk-btn-card:hover {
  border-color: #38bdf8;
}
.dk-kpi-lbl {
  font-size: 13px;
  color: #94a3b8;
  display: block;
}
.dk-kpi-val {
  font-size: 24px;
  font-weight: 600;
  color: #f8fafc;
  display: block;
  margin: 4px 0;
}
.dk-kpi-val small {
  font-size: 13px;
  font-weight: 400;
  color: #94a3b8;
}
.dk-val-emerald {
  color: #34d399;
}
.dk-kpi-foot {
  font-size: 13px;
  color: #64748b;
  display: block;
}

/* 宽屏或全屏时左中右三栏；1280/1440 带左侧菜单栏时自动变为上地图、下双栏，零挤压 */
.adm-dark-body {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 16px;
  align-items: stretch;
}
.dk-col-center {
  grid-column: 1 / -1;
  order: -1;
}
@media (min-width: 1580px) {
  .adm-dark-body {
    grid-template-columns: 300px minmax(0, 1fr) 316px;
  }
  .dk-col-center {
    grid-column: auto;
    order: 0;
  }
}
.cockpit-board.is-fullscreen .adm-dark-body {
  grid-template-columns: 308px minmax(0, 1fr) 324px;
}
.cockpit-board.is-fullscreen .dk-col-center {
  grid-column: auto;
  order: 0;
}
@media (max-width: 860px) {
  .adm-dark-body {
    grid-template-columns: 1fr;
  }
}

.dk-stack {
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.dk-flex1 {
  flex: 1;
}
.dk-sec-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 8px;
  margin-bottom: 12px;
}
.dk-sec-head h2 {
  margin: 0;
  font-size: 16px;
  font-weight: 600;
  color: #f1f5f9;
  white-space: nowrap;
}
.dk-link {
  font-size: 13px;
  color: #38bdf8;
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
  margin-bottom: 12px;
  border-bottom: 1px solid #1f2937;
}
.dk-ring-col {
  display: flex;
  align-items: center;
  gap: 10px;
  cursor: pointer;
}
.dk-ring-title {
  font-size: 14px;
  font-weight: 600;
  color: #f8fafc;
  display: block;
}
.dk-ring-sub {
  font-size: 13px;
  color: #94a3b8;
  display: block;
}

.dk-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  padding: 8px 0;
  border-bottom: 1px solid #1f2937;
  font-size: 13px;
  color: #cbd5e1;
}
.dk-row > span:first-child {
  white-space: nowrap;
  flex-shrink: 0;
}
.dk-row > strong,
.dk-row > span:last-child {
  text-align: right;
  white-space: nowrap;
}
.dk-row:last-child {
  border-bottom: none;
  padding-bottom: 0;
}
.dk-row-click {
  cursor: pointer;
}
.dk-row-click:hover {
  color: #ffffff;
}
.dk-done {
  color: #34d399;
  font-weight: 600;
}
.dk-warn {
  color: #fbbf24;
  font-weight: 600;
}

.dk-bar-item {
  margin-bottom: 10px;
  font-size: 13px;
  cursor: pointer;
}
.dk-bar-item:last-child {
  margin-bottom: 0;
}
.dk-bar-top {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 8px;
  margin-bottom: 4px;
  color: #cbd5e1;
}
.dk-bar-top span,
.dk-bar-top strong {
  white-space: nowrap;
}
.dk-track {
  height: 6px;
  background: #1e293b;
  border-radius: 3px;
  overflow: hidden;
}
.dk-fill {
  height: 100%;
  background: #38bdf8;
  border-radius: 3px;
}
.dk-fill.emerald {
  background: #10b981;
}

.dk-country-scroll {
  max-height: 236px;
  overflow-y: auto;
  padding-right: 2px;
}
.dk-country-row.selected .dk-bar-top {
  color: #ffffff;
}
.dk-country-right {
  display: inline-flex;
  align-items: center;
  gap: 8px;
}
.dk-jump-btn {
  border: none;
  background: none;
  color: #38bdf8;
  font-size: 13px;
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
  color: #94a3b8;
}

.map-frame {
  background: #0d1526;
  border: 1px solid #1f2937;
  border-radius: 8px;
  padding: 16px;
  display: flex;
  flex-direction: column;
  justify-content: space-between;
}
.map-top-head {
  margin-bottom: 8px;
}
.map-hint {
  font-size: 13px;
  color: #94a3b8;
}
.geo-svg {
  width: 100%;
  height: auto;
  display: block;
  background: #09101d;
  border-radius: 6px;
}
.geo-land {
  fill: #162238;
  stroke: #263859;
  stroke-width: 0.8;
  transition: fill 0.15s;
}
.geo-land.geo-covered {
  fill: #1d4ed8;
  fill-opacity: 0.45;
  stroke: #60a5fa;
  stroke-width: 1.1;
  cursor: pointer;
}
.geo-land.geo-covered:hover,
.geo-land.geo-covered.active {
  fill: #2563eb;
  fill-opacity: 0.82;
  stroke: #93c5fd;
  stroke-width: 1.4;
}
.hub-group {
  cursor: pointer;
}
.map-lbl {
  paint-order: stroke;
  stroke: #09101d;
  stroke-width: 3.6px;
  stroke-linecap: round;
  stroke-linejoin: round;
}
.arc-line {
  fill: none;
  stroke-width: 1.8;
  stroke-dasharray: 5 5;
  animation: routeFlow 2.4s linear infinite;
}
.arc-cn {
  stroke: #38bdf8;
}
.arc-intl {
  stroke: #34d399;
}
.map-detail-bar {
  margin-top: 12px;
  padding-top: 12px;
  border-top: 1px solid #1f2937;
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 14px;
  flex-wrap: wrap;
  font-size: 13px;
}
.map-detail-title {
  display: flex;
  align-items: center;
  gap: 6px;
  color: #ffffff;
}
.map-muted {
  color: #94a3b8;
}
.dk-inline-reset {
  border: none;
  background: none;
  color: #38bdf8;
  font-size: 13px;
  cursor: pointer;
  padding: 0;
  margin-left: 6px;
}
.dk-inline-reset:hover {
  text-decoration: underline;
}
.map-detail-metrics {
  display: flex;
  gap: 18px;
  flex-wrap: wrap;
  align-items: center;
  color: #94a3b8;
}
.map-detail-metrics strong {
  color: #f8fafc;
  margin-left: 4px;
}

/* =========================================================================
   图表分析视图样式
   ========================================================================= */
.charts-kpi4 {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 16px;
}
@media (min-width: 1200px) {
  .charts-kpi4 {
    grid-template-columns: repeat(4, minmax(0, 1fr));
  }
}
@media (max-width: 680px) {
  .charts-kpi4 {
    grid-template-columns: 1fr;
  }
}
.charts-row-7-5 {
  display: grid;
  grid-template-columns: 7fr 5fr;
  gap: 16px;
}
.charts-row-5-7 {
  display: grid;
  grid-template-columns: 5fr 7fr;
  gap: 16px;
}
@media (max-width: 1180px) {
  .charts-row-7-5,
  .charts-row-5-7 {
    grid-template-columns: 1fr;
  }
}
.chart-legend {
  display: flex;
  gap: 14px;
  font-size: 13px;
  color: #4b5e6c;
  flex-wrap: wrap;
}
.lg-dot {
  display: inline-block;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  margin-right: 6px;
}
.bar-chart-svg {
  width: 100%;
  height: 220px;
  display: block;
}
.bar-pair-group {
  cursor: pointer;
}
.bar-pair-group:hover rect {
  opacity: 0.85;
}
.chart-foot-bar {
  padding-top: 8px;
  border-top: 1px solid #f1f5f9;
  display: flex;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
  font-size: 13px;
  color: #4b5e6c;
}
.chart-foot-bar strong {
  color: #20323f;
}
.donut-wrap {
  display: grid;
  grid-template-columns: 158px 1fr;
  gap: 18px;
  align-items: center;
  margin-top: 8px;
}
@media (max-width: 560px) {
  .donut-wrap {
    grid-template-columns: 1fr;
    justify-items: center;
  }
}
.donut-legend {
  font-size: 13px;
  width: 100%;
}
.donut-row {
  display: flex;
  justify-content: space-between;
  padding: 6px 0;
  border-bottom: 1px solid #f1f5f9;
  color: #20323f;
}
.donut-row:last-child {
  border-bottom: none;
}

.clean-adm-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 14px;
}
.clean-adm-table th {
  text-align: left;
  font-size: 13px;
  color: #4b5e6c;
  padding: 10px 10px;
  border-bottom: 1px solid #e2e8f0;
  white-space: nowrap;
}
.clean-adm-table td {
  padding: 11px 10px;
  border-bottom: 1px solid #f1f5f9;
  color: #20323f;
  cursor: pointer;
}
.clean-adm-table tr:last-child td {
  border-bottom: none;
}
.clean-adm-table tbody tr:hover td {
  background: #f8fafc;
}

.ops-clean-list {
  display: flex;
  flex-direction: column;
}
.ops-clean-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 132px 96px;
  align-items: center;
  gap: 14px;
  padding: 12px 0;
  border-bottom: 1px solid #f1f5f9;
  cursor: pointer;
}
.ops-clean-row:last-child {
  border-bottom: none;
  padding-bottom: 0;
}
.ops-clean-title {
  display: block;
  font-size: 15px;
  color: #20323f;
}
.ops-clean-sub {
  display: block;
  font-size: 13px;
  color: #4b5e6c;
  margin-top: 2px;
}
.ops-clean-act {
  text-align: right;
  font-size: 13px;
  color: #2563eb;
  white-space: nowrap;
}

/* =========================================================================
   标准列表视图（保持既有布局兼容）
   ========================================================================= */
.kpi-strip {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 14px;
}
@media (min-width: 1480px) {
  .kpi-strip {
    grid-template-columns: repeat(6, minmax(0, 1fr));
  }
}
@media (max-width: 760px) {
  .kpi-strip {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
.kpi-tile {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  justify-content: space-between;
  gap: 6px;
  padding: 16px;
  background: #ffffff;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  text-align: left;
  cursor: pointer;
  color: #20323f;
  font: inherit;
  transition: border-color 0.15s;
}
.kpi-tile:hover {
  border-color: #93c5fd;
}
.kpi-tile--split {
  padding: 0;
  cursor: default;
}
.kpi-hotspot {
  width: 100%;
  padding: 16px 16px 6px;
  border: none;
  background: none;
  text-align: left;
  cursor: pointer;
  font: inherit;
  color: inherit;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.kpi-hotspot--sub {
  padding: 0 16px 16px;
}
.kpi-label {
  font-size: 13px;
  color: #4b5e6c;
}
.kpi-num {
  font-size: 24px;
  font-weight: 600;
  color: #20323f;
  line-height: 1.2;
}
.kpi-num small {
  font-size: 13px;
  font-weight: 400;
  color: #4b5e6c;
}
.kpi-meta {
  font-size: 13px;
  color: #64748b;
  line-height: 1.45;
}
.kpi-meta--link:hover {
  color: #2563eb;
}

.posture-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 16px;
}
@media (max-width: 1220px) {
  .posture-grid {
    grid-template-columns: 1fr;
  }
}
.posture-panel,
.country-panel {
  background: #ffffff;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  padding: 20px;
}
.panel-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  margin-bottom: 16px;
  flex-wrap: wrap;
}
.panel-head h2 {
  margin: 0;
  font-size: 18px;
  font-weight: 600;
  color: #20323f;
}
.panel-title-inline {
  display: flex;
  align-items: baseline;
  gap: 10px;
  flex-wrap: wrap;
}
.route-count-note {
  font-size: 13px;
  color: #64748b;
}

.ring-row {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
  padding-bottom: 14px;
  margin-bottom: 12px;
  border-bottom: 1px solid #f1f5f9;
}
.ring-item {
  display: flex;
  align-items: center;
  gap: 12px;
  cursor: pointer;
}
.ring-svg {
  width: 68px;
  height: 68px;
  flex-shrink: 0;
  transform: rotate(-90deg);
}
.ring-track {
  fill: none;
  stroke: #e2e8f0;
  stroke-width: 7;
}
.ring-fill {
  fill: none;
  stroke-width: 7;
  stroke-linecap: round;
}
.ring-fill--blue { stroke: #2563eb; }
.ring-fill--green { stroke: #16a34a; }
.ring-fill--amber { stroke: #d97706; }
.ring-info {
  display: flex;
  flex-direction: column;
  min-width: 0;
}
.ring-title { font-size: 13px; color: #4b5e6c; }
.ring-val { font-size: 20px; font-weight: 600; color: #20323f; }
.ring-sub { font-size: 13px; color: #64748b; }

.ops-list { display: flex; flex-direction: column; }
.ops-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 8px 0;
  border-bottom: 1px solid #f1f5f9;
  font-size: 14px;
  cursor: pointer;
}
.ops-row:last-child { border-bottom: none; padding-bottom: 0; }
.ops-plain { font-size: 13px; color: #4b5e6c; font-weight: 600; }

.bar-stack { display: flex; flex-direction: column; gap: 12px; }
.bar-stack--routes-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px 14px;
}
.bar-row { cursor: pointer; }
.bar-row__top {
  display: flex;
  justify-content: space-between;
  font-size: 13px;
  margin-bottom: 4px;
  color: #20323f;
}
.bar-track {
  height: 6px;
  background: #f1f5f9;
  border-radius: 3px;
  overflow: hidden;
}
.bar-track--sm { height: 5px; margin-top: 6px; }
.bar-fill { height: 100%; background: #2563eb; border-radius: 3px; }
.bar-fill--green { background: #16a34a; }
.bar-fill--teal { background: #0d9488; }
.nowrap { white-space: nowrap; }

.role-mini-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
  margin-top: 14px;
  padding-top: 14px;
  border-top: 1px solid #f1f5f9;
}
.role-mini {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 8px 10px;
  border: 1px solid #e2e8f0;
  border-radius: 6px;
  font-size: 13px;
  cursor: pointer;
}

.country-matrix { display: flex; flex-direction: column; }
.matrix-header {
  display: grid;
  grid-template-columns: 104px repeat(5, minmax(0, 1fr));
  gap: 12px;
  padding: 10px 12px;
  font-size: 13px;
  font-weight: 600;
  color: #4b5e6c;
  border-bottom: 1px solid #e2e8f0;
}
.matrix-row {
  display: grid;
  grid-template-columns: 104px repeat(5, minmax(0, 1fr));
  gap: 12px;
  padding: 12px;
  align-items: center;
  border-bottom: 1px solid #f1f5f9;
}
.matrix-row:last-child { border-bottom: none; }
@media (max-width: 1180px) {
  .matrix-header { display: none; }
  .matrix-row { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
.c-name strong { font-size: 15px; color: #20323f; }
.c-cell {
  border: none;
  background: none;
  text-align: left;
  padding: 4px;
  cursor: pointer;
  font: inherit;
  color: inherit;
}
.c-cell--split { display: flex; flex-direction: column; gap: 4px; cursor: default; }
.c-subbtn {
  border: none;
  background: none;
  text-align: left;
  padding: 0;
  cursor: pointer;
  font: inherit;
  color: inherit;
  display: flex;
  justify-content: space-between;
  gap: 6px;
  font-size: 13px;
}
.c-cell__top {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 6px;
  font-size: 13px;
}
.c-muted { font-size: 13px; color: #64748b; }
.empty-inline { margin: 0; font-size: 13px; color: #64748b; }

/* =========================================================================
   全屏展示模式（隐藏外层导航与顶栏，铺满整个屏幕）
   ========================================================================= */
.cockpit-board.is-fullscreen {
  position: fixed;
  inset: 0;
  z-index: 2000;
  overflow-y: auto;
  border-radius: 0;
  padding: 20px 28px;
}
.cockpit-board.is-fullscreen:not(.is-mode-map) {
  background: #f8fafc;
}
</style>
