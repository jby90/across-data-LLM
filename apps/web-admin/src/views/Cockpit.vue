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

const route = useRoute()
const router = useRouter()
const boardRef = ref<HTMLElement | null>(null)
const loading = ref(false)
const selectedGeoCode = ref<string>('ALL')
const isFullscreen = ref(route.query.fullscreen === '1')
const data = ref<CockpitOverview | null>(null)

async function load() {
  loading.value = true
  try {
    data.value = await api.get<CockpitOverview>('/admin/cockpit/overview')
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

const whitepapersOutdated = computed(() => {
  const opVal = data.value?.operations.whitepapers_outdated
  if (typeof opVal === 'number') return opVal
  const hlVal = data.value?.headline.whitepapers_outdated
  if (typeof hlVal === 'number') return hlVal
  return 0
})

const standardsSubtitle = computed(() => {
  const hl = data.value?.headline
  const cn = hl?.national_standards_count ?? hl?.standards_cn
  const intl = hl?.intl_standards_count ?? hl?.standards_intl ?? hl?.standards_international
  if (typeof cn === 'number' && typeof intl === 'number' && (cn > 0 || intl > 0)) {
    return `中国标准 ${cn} 项 · 国际通用 ${intl} 项`
  }
  return '涵盖整车信息安全、智能网联与数据合规技术标准'
})

const cleanKgLabel = (label: string) => label.replace(/节点/g, '要素')

const maxClauses = computed(() => {
  const vals = (data.value?.countries || []).map((c) => c.clauses_count)
  return Math.max(1, ...vals)
})

const maxKgCount = computed(() => {
  const vals = (data.value?.kg_node_breakdown || []).map((x) => x.count)
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
    :class="{ 'is-fullscreen': isFullscreen }"
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
        <el-button type="primary" data-testid="cockpit-fullscreen-toggle" @click="toggleFullscreen">
          {{ isFullscreen ? '退出全屏' : '全屏展示' }}
        </el-button>
      </div>
    </header>

    <template v-if="data">
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
              @click="selectedGeoCode = selectedGeoCode === item.code ? 'ALL' : item.code"
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
              @click="selectedGeoCode = selectedGeoCode === m.code ? 'ALL' : m.code"
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

        <!-- 右栏：国家列表 + 账号与审计概况 -->
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
                @click="selectedGeoCode = selectedGeoCode === c.country_code ? 'ALL' : c.country_code"
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
  </div>
</template>

<style scoped>
@keyframes routeFlow {
  to { stroke-dashoffset: -36; }
}

.cockpit-board {
  display: flex;
  flex-direction: column;
  gap: 18px;
  background: #0b1120;
  border: 1px solid #1e293b;
  border-radius: 10px;
  padding: 20px 24px 24px;
  color: #f1f5f9;
  font-variant-numeric: tabular-nums;
  min-height: 480px;
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
  font-size: 22px;
  font-weight: 600;
  line-height: 1.35;
  color: #f8fafc;
}
.cockpit-subtitle {
  margin: 4px 0 0;
  font-size: 13px;
  color: #94a3b8;
}
.cockpit-actions {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}

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
  color: #94a3b8;
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

.cockpit-board.is-fullscreen {
  position: fixed;
  inset: 0;
  z-index: 2000;
  overflow-y: auto;
  border-radius: 0;
  padding: 20px 28px;
}
</style>
