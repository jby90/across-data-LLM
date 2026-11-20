<script setup lang="ts">
import { localTime } from '@cyshuhe/shared'
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
  routes: Array<{ route_label: string; project_count: number }>
  countries: CountryPosture[]
}

const route = useRoute()
const router = useRouter()
const boardRef = ref<HTMLElement | null>(null)
const loading = ref(false)
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
  if (document.fullscreenElement) {
    isFullscreen.value = true
  } else {
    isFullscreen.value = false
  }
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

const visibleCountries = computed(() => {
  const list = data.value?.countries || []
  return showAllCountries.value ? list : list.slice(0, 6)
})

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
    return `中国标准 ${cn} 项 · 国际通用标准 ${intl} 项`
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

const ringOffset = (pct: number | null | undefined) => {
  const c = 2 * Math.PI * 34
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
        <h1>合规态势看板</h1>
        <p class="cockpit-subtitle">
          实时汇总现行法规、外文译文、知识图谱、海外属地准入与企业项目概况
          <span v-if="data?.generated_at" class="cockpit-time">· 数据刷新于 {{ localTime(data.generated_at) }}</span>
        </p>
      </div>
      <div class="cockpit-actions">
        <el-button @click="load" data-testid="cockpit-refresh">刷新数据</el-button>
        <el-button type="primary" data-testid="cockpit-fullscreen-toggle" @click="toggleFullscreen">
          {{ isFullscreen ? '退出全屏汇报' : '全屏汇报模式' }}
        </el-button>
      </div>
    </header>

    <template v-if="data">
      <!-- 第一排：六大核心指标（点击直达对应管理模块） -->
      <section class="kpi-strip" aria-label="核心指标">
        <button
          type="button"
          class="kpi-tile"
          @click="go({ path: '/documents', query: { status: 'in_force', folder: 'all' } })"
        >
          <span class="kpi-label">全库在册现行法规</span>
          <strong class="kpi-num">{{ data.headline.laws_in_force }}<small> 部</small></strong>
          <span class="kpi-meta">
            <template v-if="data.headline.laws_archived_total || data.headline.laws_total">归档 {{ data.headline.laws_archived_total ?? data.headline.laws_total }} 部（含外围配套）· </template>覆盖 {{ data.headline.covered_jurisdictions }} 国 · {{ data.headline.clauses_total }} 条原始条款
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
              {{ data.headline.admission_entries_total }} 项属地准入 · {{ data.headline.official_forms_total }} 套官方原表<template v-if="data.headline.official_forms_eu">（含欧盟通用 {{ data.headline.official_forms_eu }} 套）</template>
            </span>
          </button>
        </div>

        <button type="button" class="kpi-tile" @click="go('/orgs')">
          <span class="kpi-label">入驻企业与平台账号</span>
          <strong class="kpi-num">{{ data.headline.active_organizations }}<small> 家单位</small></strong>
          <span class="kpi-meta">企业用户 {{ data.account_summary.enterprise_members }} · 普通查看者 {{ data.account_summary.ordinary_viewers }}<template v-if="data.account_summary.portal_onboarded">（含门户单点开通 {{ data.account_summary.portal_onboarded }}）</template><template v-if="data.account_summary.trial_visitors"> · 独立体验账号 {{ data.account_summary.trial_visitors }}</template></span>
        </button>

        <button type="button" class="kpi-tile" @click="go('/agent-studio')">
          <span class="kpi-label">企业评估与准入项目</span>
          <strong class="kpi-num">{{ data.headline.doc_projects_total + data.headline.admission_projects_total }}<small> 个项目</small></strong>
          <span class="kpi-meta">合规评估 {{ data.headline.doc_projects_total }} · 属地准入 {{ data.headline.admission_projects_total }} · 咨询对话 {{ data.headline.consulting_chats_total }}</span>
        </button>
      </section>

      <!-- 第二排：三栏视觉态势 -->
      <section class="posture-grid">
        <div class="posture-panel">
          <div class="panel-head">
            <h2>外文译文与采集站点状态</h2>
            <el-button link type="primary" @click="go('/updates')">查看更新与时效</el-button>
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
            <div class="ops-row" @click="go('/opinion')">
              <span>舆情监测累计收录</span>
              <span class="ops-plain">{{ data.headline.opinion_items_total }} 条动态</span>
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
            <div v-for="item in data.kg_node_breakdown" :key="item.label" class="bar-row" @click="go('/knowledge-graph')">
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
              <span v-if="data.routes.length" class="route-count-note">共 {{ data.routes.length }} 条跨境流向</span>
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
                <strong class="nowrap">{{ data.routes.length > 4 ? `${rt.project_count} 个项目` : `${rt.project_count} 个评估项目` }}</strong>
              </div>
              <div class="bar-track">
                <div class="bar-fill bar-fill--teal" :style="{ width: `${Math.max(8, Math.round((rt.project_count / maxRouteCount) * 100))}%` }" />
              </div>
            </div>
          </div>
          <p v-else class="empty-inline">还没有跨境合规评估项目记录</p>

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

      <!-- 第三排：各国与地区法规收录及项目分布 -->
      <section class="country-panel">
        <div class="panel-head">
          <div class="panel-title-inline">
            <h2>各国与地区法规收录及项目分布</h2>
            <span class="panel-scope-note">统计口径：全库在册现行法规（含外围配套）· 原始条款；用户端默认精选核心常用法规 · 阅读条文</span>
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

          <div
            v-for="c in visibleCountries"
            :key="c.country_code"
            class="matrix-row"
          >
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
                  <span class="cy-status cy-status--done">中文母语原文</span>
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
                <span class="cy-status cy-status--done">国内出境监管母国</span>
              </div>
              <span class="c-muted nowrap">母国出境合规要求 · 网信办原表 {{ c.official_forms_count || 4 }} 套</span>
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
                  <span class="nowrap">准入事项 {{ c.admission_entries_count }} 项</span>
                  ·
                  <span class="nowrap">官方表 {{ c.official_forms_count }} 套</span>
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
                <span
                  v-if="c.attention_sites_count > 0"
                  class="cy-status cy-status--warn"
                >{{ c.attention_sites_count }} 个待处理</span>
                <span v-else-if="c.monitor_sites_count > 0" class="cy-status cy-status--done">运行正常</span>
              </div>
              <span class="c-muted nowrap">关联企业项目 {{ c.enterprise_projects_count }} 个</span>
            </button>
          </div>
        </div>
        <p v-else class="empty-inline" data-testid="cockpit-country-empty">当前尚未收录各国现行法规记录</p>
      </section>
    </template>
  </div>
</template>

<style scoped>
.cockpit-board {
  display: flex;
  flex-direction: column;
  gap: 20px;
}
.nowrap {
  white-space: nowrap;
}
.nowrap-title {
  white-space: nowrap;
}
.cockpit-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  flex-wrap: wrap;
}
.cockpit-header h1 {
  margin: 0 0 6px;
  font-size: 24px;
  font-weight: 600;
  color: #20323f;
  line-height: 1.4;
}
.cockpit-subtitle {
  margin: 0;
  font-size: 14px;
  color: #4b5e6c;
  line-height: 1.6;
}
.cockpit-time {
  font-size: 13px;
  color: #64748b;
}
.cockpit-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

.kpi-strip {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 12px;
}
@media (min-width: 1360px) {
  .kpi-strip {
    grid-template-columns: repeat(6, minmax(0, 1fr));
  }
}
@media (max-width: 900px) {
  .kpi-strip {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
.kpi-tile {
  text-align: left;
  background: #ffffff;
  border: 1px solid var(--cy-line);
  border-radius: 8px;
  padding: 14px 16px;
  display: flex;
  flex-direction: column;
  gap: 6px;
  cursor: pointer;
  transition: border-color 0.16s ease, box-shadow 0.16s ease;
  font: inherit;
  min-width: 0;
}
.kpi-tile:hover {
  border-color: var(--el-color-primary-light-5);
  box-shadow: 0 4px 12px rgba(32, 50, 63, 0.06);
}
.kpi-tile--split {
  padding: 10px 12px;
  gap: 4px;
  cursor: default;
}
.kpi-hotspot {
  text-align: left;
  background: transparent;
  border: 0;
  border-radius: 6px;
  padding: 4px;
  display: flex;
  flex-direction: column;
  gap: 6px;
  cursor: pointer;
  font: inherit;
  width: 100%;
  transition: background-color 0.15s ease;
}
.kpi-hotspot:hover {
  background: #f5f8ff;
}
.kpi-hotspot--sub {
  border-top: 1px dashed var(--cy-line);
  border-radius: 0 0 6px 6px;
  padding-top: 6px;
}
.kpi-meta--link:hover {
  color: var(--el-color-primary);
}
.kpi-label {
  font-size: 13px;
  font-weight: 600;
  color: #4b5e6c;
}
.kpi-num {
  font-size: 24px;
  font-weight: 600;
  color: #20323f;
  line-height: 1.3;
}
.kpi-num small {
  font-size: 13px;
  font-weight: 400;
  color: #4b5e6c;
}
.kpi-meta {
  font-size: 13px;
  line-height: 1.5;
  color: #64748b;
}

.posture-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 16px;
}
@media (max-width: 1180px) {
  .posture-grid {
    grid-template-columns: 1fr;
  }
}
.posture-panel,
.country-panel {
  background: #ffffff;
  border: 1px solid var(--cy-line);
  border-radius: 8px;
  padding: 20px;
  display: flex;
  flex-direction: column;
  gap: 14px;
  min-width: 0;
}
.panel-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px 12px;
  flex-wrap: wrap;
}
.panel-title-inline {
  display: flex;
  align-items: baseline;
  gap: 4px 10px;
  flex-wrap: wrap;
  min-width: 0;
}
.route-count-note {
  font-size: 13px;
  color: #64748b;
  white-space: nowrap;
}
.panel-scope-note {
  font-size: 13px;
  color: #64748b;
  line-height: 1.4;
}
.panel-head h2 {
  margin: 0;
  font-size: 18px;
  font-weight: 600;
  color: #20323f;
  line-height: 1.4;
}
@media (max-width: 1560px) and (min-width: 1181px) {
  .posture-panel {
    padding: 16px;
  }
  .panel-head h2 {
    font-size: 16px;
  }
}

.ring-row {
  display: grid;
  grid-template-columns: 1fr;
  gap: 10px;
}
.ring-item {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 10px 12px;
  border: 1px solid var(--cy-line);
  border-radius: 8px;
  cursor: pointer;
  min-width: 0;
}
.ring-item:hover {
  border-color: var(--el-color-primary-light-5);
}
.ring-svg {
  width: 64px;
  height: 64px;
  flex: none;
  transform: rotate(-90deg);
}
.ring-track {
  fill: none;
  stroke: #edf2f7;
  stroke-width: 8;
}
.ring-fill {
  fill: none;
  stroke-width: 8;
  stroke-linecap: round;
}
.ring-fill--blue {
  stroke: #2563eb;
}
.ring-fill--green {
  stroke: #16a34a;
}
.ring-fill--amber {
  stroke: #d97706;
}
.ring-info {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}
.ring-title {
  font-size: 13px;
  color: #4b5e6c;
}
.ring-val {
  font-size: 18px;
  font-weight: 600;
  color: #20323f;
}
.ring-sub {
  font-size: 13px;
  color: #64748b;
}

.ops-list {
  display: flex;
  flex-direction: column;
  border-top: 1px solid var(--cy-line);
  padding-top: 6px;
}
.ops-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 10px;
  padding: 7px 0;
  font-size: 14px;
  color: #20323f;
  cursor: pointer;
}
.ops-row:hover {
  color: var(--el-color-primary);
}
.ops-plain {
  font-size: 13px;
  color: #4b5e6c;
  font-weight: 600;
  white-space: nowrap;
}

.bar-stack {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
@media (min-width: 1560px) {
  .bar-stack--routes-grid {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 8px 14px;
  }
}
.route-label-text {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.bar-row {
  display: flex;
  flex-direction: column;
  gap: 5px;
  cursor: pointer;
}
.bar-row__top {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 8px;
  font-size: 14px;
  color: #20323f;
}
.bar-row__top strong {
  font-weight: 600;
  font-size: 13px;
  color: #4b5e6c;
}
.bar-track {
  height: 8px;
  background: #edf2f7;
  border-radius: 4px;
  overflow: hidden;
}
.bar-track--sm {
  height: 6px;
}
.bar-fill {
  height: 100%;
  background: #2563eb;
  border-radius: 4px;
}
.bar-fill--teal {
  background: #0d9488;
}
.bar-fill--green {
  background: #16a34a;
}

.role-mini-grid {
  margin-top: auto;
  padding-top: 10px;
  border-top: 1px solid var(--cy-line);
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 8px;
}
.role-mini {
  padding: 8px 10px;
  border: 1px solid var(--cy-line);
  border-radius: 8px;
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  color: #4b5e6c;
  cursor: pointer;
}
.role-mini strong {
  font-size: 15px;
  font-weight: 600;
  color: #20323f;
}
.empty-inline {
  margin: 0;
  font-size: 14px;
  color: #4b5e6c;
}

.country-matrix {
  display: flex;
  flex-direction: column;
  border-top: 1px solid var(--cy-line);
}
.matrix-header,
.matrix-row {
  display: grid;
  grid-template-columns: 104px repeat(5, minmax(0, 1fr));
  gap: 10px;
  align-items: center;
  padding: 10px 0;
  border-bottom: 1px solid var(--cy-line);
}
.matrix-header {
  font-size: 13px;
  font-weight: 600;
  color: #4b5e6c;
}
@media (max-width: 1180px) {
  .matrix-header {
    display: none;
  }
  .matrix-row {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
.c-name strong {
  font-size: 15px;
  font-weight: 600;
  color: #20323f;
}
.c-cell {
  text-align: left;
  background: transparent;
  border: 1px solid transparent;
  border-radius: 8px;
  padding: 6px 8px;
  display: flex;
  flex-direction: column;
  gap: 5px;
  cursor: pointer;
  font: inherit;
  min-width: 0;
}
.c-cell:hover {
  background: #f8fafc;
  border-color: var(--cy-line);
}
.c-cell--split {
  padding: 2px 4px;
  gap: 2px;
  cursor: default;
}
.c-cell--split:hover {
  background: transparent;
  border-color: transparent;
}
.c-subbtn {
  text-align: left;
  background: transparent;
  border: 1px solid transparent;
  border-radius: 6px;
  padding: 3px 6px;
  display: flex;
  justify-content: space-between;
  align-items: center;
  flex-wrap: wrap;
  gap: 4px 8px;
  cursor: pointer;
  font: inherit;
  width: 100%;
  font-size: 13px;
  color: #20323f;
}
.c-subbtn:hover {
  background: #f5f8ff;
  border-color: var(--cy-line);
}
.c-subbtn strong {
  font-size: 14px;
  font-weight: 600;
}
.c-cell__top {
  display: flex;
  justify-content: space-between;
  align-items: center;
  flex-wrap: wrap;
  gap: 4px 8px;
  font-size: 13px;
  color: #20323f;
}
.c-cell__top strong {
  font-size: 14px;
  font-weight: 600;
}
.c-muted {
  font-size: 13px;
  color: #64748b;
}

/* 1920×1080 全屏汇报模式：紧凑自适应一屏完整落入 1080px 高度，零纵向截断；小高宽视口开启纵向顺滑滚动 */
.cockpit-board.is-fullscreen {
  position: fixed;
  inset: 0;
  z-index: 2000;
  width: 100vw;
  height: 100vh;
  background: #f7f9fc;
  padding: 10px 22px;
  gap: 8px;
  overflow-x: hidden;
  overflow-y: hidden;
  box-sizing: border-box;
}
.cockpit-board.is-fullscreen > * {
  flex-shrink: 0;
}
@media (min-width: 1181px) {
  .cockpit-board.is-fullscreen .bar-stack--routes-grid {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 4px 12px;
  }
}
@media (max-height: 1039px), (max-width: 1599px) {
  .cockpit-board.is-fullscreen {
    overflow-y: auto;
    overflow-x: hidden;
    padding: 12px 22px 24px;
    scrollbar-gutter: stable;
  }
  .cockpit-board.is-fullscreen::-webkit-scrollbar {
    width: 8px;
  }
  .cockpit-board.is-fullscreen::-webkit-scrollbar-thumb {
    background: #cbd5e1;
    border-radius: 4px;
  }
  .cockpit-board.is-fullscreen::-webkit-scrollbar-track {
    background: rgba(226, 232, 240, 0.45);
  }
}
.cockpit-board.is-fullscreen .cockpit-header h1 {
  margin: 0;
  font-size: 20px;
  line-height: 1.25;
}
.cockpit-board.is-fullscreen .cockpit-subtitle {
  margin: 0;
  font-size: 13px;
  line-height: 1.3;
}
.cockpit-board.is-fullscreen .kpi-strip {
  gap: 8px;
}
.cockpit-board.is-fullscreen .kpi-tile {
  padding: 6px 10px;
  gap: 2px;
}
.cockpit-board.is-fullscreen .kpi-tile--split {
  padding: 4px 8px;
  gap: 1px;
}
.cockpit-board.is-fullscreen .kpi-hotspot {
  padding: 2px 4px;
  gap: 1px;
}
.cockpit-board.is-fullscreen .kpi-hotspot--sub {
  padding-top: 2px;
}
.cockpit-board.is-fullscreen .kpi-num {
  font-size: 20px;
  line-height: 1.15;
}
.cockpit-board.is-fullscreen .kpi-meta {
  line-height: 1.25;
}
.cockpit-board.is-fullscreen .posture-grid {
  gap: 8px;
}
.cockpit-board.is-fullscreen .posture-panel,
.cockpit-board.is-fullscreen .country-panel {
  padding: 8px 14px;
  gap: 6px;
}
.cockpit-board.is-fullscreen .panel-head h2 {
  font-size: 15px;
  line-height: 1.25;
  margin: 0;
}
.cockpit-board.is-fullscreen .ring-row {
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 6px;
}
.cockpit-board.is-fullscreen .ring-item {
  padding: 4px 8px;
  gap: 8px;
}
.cockpit-board.is-fullscreen .ring-svg {
  width: 42px;
  height: 42px;
}
.cockpit-board.is-fullscreen .ops-list {
  padding-top: 2px;
}
.cockpit-board.is-fullscreen .ops-row {
  padding: 3px 0;
  line-height: 1.25;
}
.cockpit-board.is-fullscreen .bar-stack {
  gap: 4px;
}
.cockpit-board.is-fullscreen .bar-row {
  gap: 2px;
}
.cockpit-board.is-fullscreen .bar-track {
  height: 5px;
}
.cockpit-board.is-fullscreen .role-mini-grid {
  padding-top: 4px;
  gap: 5px;
}
.cockpit-board.is-fullscreen .role-mini {
  padding: 3px 8px;
}
.cockpit-board.is-fullscreen .matrix-header {
  padding: 3px 0;
}
.cockpit-board.is-fullscreen .matrix-row {
  padding: 2px 0;
}
.cockpit-board.is-fullscreen .c-cell {
  padding: 2px 5px;
  gap: 2px;
}
.cockpit-board.is-fullscreen .c-cell--split {
  padding: 1px 2px;
  gap: 1px;
}
.cockpit-board.is-fullscreen .c-subbtn {
  padding: 1px 4px;
}
</style>
