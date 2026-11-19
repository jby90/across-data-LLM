<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import AppIcon from '../components/AppIcon.vue'
import { api } from '../api'
import { canEdit, userName, localTime, statusClass, COUNTRY_NAMES } from '@cyshuhe/shared'
import { useAuth } from '../auth'
const auth = useAuth()
// 最近项目与进度同步：展示最近更新的合规评估与海外属地准入项目，状态文案与各自业务页面实时一致（已就绪项目显示绿色就绪标签，不再隐藏或滞留旧缓存）
interface Progress { ready: number; total: number; missing: number; issues: number }
interface DocItem { id: string; title: string; conversation_code: string; created_at: string | null; updated_at: string | null }
interface Filing { state: string; state_zh: string; submitted_on: string; correction_on: string; result_on: string }
interface AdmItem { id: string; entry_name: string; country_code: string; created_at: string; updated_at: string; summary: string; ready: boolean; approval: unknown; filing?: Filing }
interface Row { key: string; module: string; title: string; code: string; status: string; short: string; ok: boolean; at: string; to: { path: string; query: Record<string, string> } }
interface OverviewTodo { id: string; kind: string; level: 'danger' | 'warning' | 'info'; title: string; detail: string; action_label: string; to: { path: string; query?: Record<string, string> } }
interface ComplianceOverview {
  summary: {
    projects_total: number; ready_projects: number; docs_ready_total: number; docs_required_total: number
    high_risk_active_total: number; high_risk_cleared_total: number; admission_total: number; admission_ready_or_approved: number
  }
  route_distribution: { route: string; count: number; ready: number; docs_ready: number; docs_total: number }[]
  risk_summary: { init_high: number; init_medium: number; init_low: number; current_high: number; current_medium: number; current_low: number; mitigated: number; downgraded: number; high_cleared: number }
  todos: OverviewTodo[]
}
const COUNTRIES: Record<string, string> = COUNTRY_NAMES
const FOLD_LIMIT = 5
const PROGRESS_KEY = 'home-doc-progress-v2'
const recentExpanded = ref(false)
const todosExpanded = ref(false)
const overview = ref<ComplianceOverview | null>(null)
const docItems = ref<DocItem[]>([])
const progress = ref<Record<string, Progress>>({})
const admItems = ref<AdmItem[]>([])
const docsLoaded = ref(false)
const admsLoaded = ref(false)
const hasAdmission = ref(false)
// 准入：递交以前与准入页一致显示备齐或差项，递交以后显示申报进度
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
// 两个项目同名时（各自独立建的），建立时间写到分钟，和更新通知里的写法一样
const sameName = (title: string) => docItems.value.filter((d) => d.title === title).length > 1
const madeAt = (title: string, at: string | null) => (sameName(title) ? localTime(at) : localTime(at, 'date'))
const rows = computed<Row[]>(() => {
  return [
    ...docItems.value.map((d): Row => {
      const p = progress.value[d.id]
      const st = docShort(p)
      return {
        key: `d-${d.id}`, module: '合规评估', title: d.title, code: `${madeAt(d.title, d.created_at)} 创建`,
        status: docStatus(p), short: st.short, ok: st.ok,
        at: d.updated_at || d.created_at || '', to: { path: '/documents', query: { project: d.id } },
      }
    }),
    ...admItems.value.map((a): Row => {
      const st = admShort(a)
      return {
        key: `a-${a.id}`, module: '属地准入', title: `${COUNTRIES[a.country_code] ?? a.country_code} · ${a.entry_name}`,
        code: `${localTime(a.created_at, 'date')} 创建`, status: admStatus(a), short: st.short, ok: st.ok, at: a.updated_at,
        to: { path: '/admission', query: { project: a.id } },
      }
    }),
  ].sort((x, y) => y.at.localeCompare(x.at))
})
const visibleRows = computed(() => (recentExpanded.value ? rows.value : rows.value.slice(0, FOLD_LIMIT)))
const visibleTodos = computed(() => {
  const list = overview.value?.todos ?? []
  return todosExpanded.value ? list : list.slice(0, FOLD_LIMIT)
})
async function fetchDocsWithProgress(): Promise<{ docs: DocItem[]; prog: Record<string, Progress> }> {
  let docs: DocItem[] = []
  try {
    const res = await api.get<DocItem[]>('/api/analysis/doc-projects')
    docs = Array.isArray(res) ? res : []
  } catch {
    docs = []
  }
  const ids = docs.map((d) => d.id)
  if (!ids.length) return { docs, prog: {} }
  let cached: Record<string, Progress> = {}
  try {
    cached = JSON.parse(localStorage.getItem(PROGRESS_KEY) || '{}') || {}
  } catch {
    cached = {}
  }
  const fresh: Record<string, Progress> = {}
  const batches: string[][] = []
  for (let i = 0; i < ids.length; i += 20) batches.push(ids.slice(i, i + 20))
  await Promise.all(
    batches.map(async (batch) => {
      try {
        const chunk = await api.get<Record<string, Progress>>(`/api/analysis/doc-projects/progress?ids=${batch.join(',')}`)
        Object.assign(fresh, chunk)
      } catch {
        /* 批次查询异常时回退上次缓存 */
      }
    }),
  )
  if (Object.keys(fresh).length) {
    try {
      localStorage.setItem(PROGRESS_KEY, JSON.stringify(fresh))
    } catch {
      /* 忽略本地存储容量限制 */
    }
  }
  return { docs, prog: { ...cached, ...fresh } }
}
async function fetchAdmissionList(): Promise<AdmItem[]> {
  try {
    const all = await api.get<AdmItem[]>('/api/admission/projects')
    return Array.isArray(all) ? [...all].sort((a, b) => b.updated_at.localeCompare(a.updated_at)) : []
  } catch {
    return []
  }
}
async function loadRecentAll() {
  const [{ docs, prog }, adms] = await Promise.all([fetchDocsWithProgress(), fetchAdmissionList()])
  docItems.value = docs
  progress.value = prog
  admItems.value = adms
  hasAdmission.value = adms.length > 0
  docsLoaded.value = true
  admsLoaded.value = true
}
async function loadOverview() {
  try {
    overview.value = await api.get<ComplianceOverview>('/api/analysis/doc-projects/compliance-overview')
  } catch {
    overview.value = null
  }
}
const TODO_TAG: Record<string, string> = {
  high_risk: '高风险待处置',
  risk_stale: '待重新评估',
  law_update: '更新通知',
  admission_pending: '属地准入',
  missing_info: '待补信息',
}
onMounted(() => { void loadRecentAll(); void loadOverview() })
const loaded = computed(() => docsLoaded.value && admsLoaded.value)
const visibleCards = [
  { path: '/knowledge', icon: 'book', title: '法规知识库' },
  { path: '/documents', icon: 'shield', title: '合规评估' },
  { path: '/admission', icon: 'flow', title: '海外属地准入' },
]
</script>
<template>
  <div>
    <section class="welcome-hero">
      <div class="hero-head">
        <h1>欢迎，{{ userName(auth.user) }}</h1>
        <div class="hero-actions">
          <RouterLink v-if="canEdit(auth.user) && loaded && rows.length" to="/documents"><el-button type="primary" data-testid="home-new">新建合规评估</el-button></RouterLink>
        </div>
      </div>

      <div v-if="!loaded" v-loading="true" class="recent recent-loading" data-testid="home-recent-loading" />
      <div v-else-if="rows.length" class="recent" data-testid="home-recent">
        <div class="recent-head"><h2>最近项目与办理进度<small v-if="rows.length > FOLD_LIMIT" class="recent-count">共 {{ rows.length }} 项</small></h2>
          <span class="recent-links"><RouterLink to="/documents" data-testid="home-recent-all">查看评估 →</RouterLink><RouterLink v-if="hasAdmission" to="/admission" data-testid="home-recent-all-admission">查看准入 →</RouterLink><RouterLink to="/analysis" data-testid="home-go-consulting">咨询助手 →</RouterLink></span></div>
        <RouterLink v-for="r in visibleRows" :key="r.key" class="recent-row" :to="r.to" data-testid="home-recent-row">
          <span class="recent-module">{{ r.module }}</span>
          <span class="recent-main"><strong>{{ r.title }}</strong><small>{{ r.code }}<template v-if="r.at && !r.code.startsWith(localTime(r.at, 'date'))"> · {{ localTime(r.at, 'date') }} 更新</template><template v-if="r.status && r.status !== r.short"> · {{ r.status }}</template></small></span>
          <span class="recent-status" data-testid="home-recent-status"><span v-if="r.short" class="cy-status" :class="r.ok ? 'cy-status--done' : statusClass(r.short)">{{ r.short }}</span><span v-else class="cy-status cy-status--muted">进行中</span></span>
          <span class="recent-go">{{ (!canEdit(auth.user) || r.ok) ? '查看 →' : '继续 →' }}</span>
        </RouterLink>
        <button
          v-if="rows.length > FOLD_LIMIT"
          type="button"
          class="cy-fold-toggle"
          data-testid="home-recent-fold-toggle"
          @click="recentExpanded = !recentExpanded"
        >
          <span>{{ recentExpanded ? '收起' : `展开其余 ${rows.length - FOLD_LIMIT} 项` }}</span>
          <AppIcon :name="recentExpanded ? 'chevron-up' : 'chevron-down'" />
        </button>
      </div>
      <div v-else-if="loaded" class="recent" data-testid="home-recent-empty">
        <RouterLink v-if="canEdit(auth.user)" to="/documents"><el-button type="primary">新建合规评估项目</el-button></RouterLink>
        <p v-else class="viewer-empty">当前暂无本单位共享项目，您可以先查阅法规知识库或使用咨询助手提问查法。</p>
      </div>

      <div v-if="overview?.todos?.length" class="risk-todos" data-testid="home-risk-todos">
        <div class="recent-head">
          <h2>待处理事项与更新通知<small v-if="overview.todos.length > FOLD_LIMIT" class="recent-count">共 {{ overview.todos.length }} 项</small></h2>
        </div>
        <RouterLink v-for="t in visibleTodos" :key="t.id" class="todo-row" :to="t.to" data-testid="home-todo-row">
          <span class="todo-tag" :class="t.level">{{ TODO_TAG[t.kind] || '待办提醒' }}</span>
          <span class="recent-main"><strong>{{ t.title }}</strong><small>{{ t.detail }}</small></span>
          <span class="recent-go">{{ canEdit(auth.user) ? `${t.action_label} →` : '查看详情 →' }}</span>
        </RouterLink>
        <button
          v-if="overview.todos.length > FOLD_LIMIT"
          type="button"
          class="cy-fold-toggle"
          data-testid="home-todos-fold-toggle"
          @click="todosExpanded = !todosExpanded"
        >
          <span>{{ todosExpanded ? '收起' : `展开其余 ${overview.todos.length - FOLD_LIMIT} 项` }}</span>
          <AppIcon :name="todosExpanded ? 'chevron-up' : 'chevron-down'" />
        </button>
      </div>
    </section>
    <nav class="quick-links" aria-label="常用功能" data-testid="home-quick">
      <RouterLink v-for="card in visibleCards" :key="card.path" :to="card.path" class="quick-link"><span class="feature-icon"><AppIcon :name="card.icon" /></span><strong>{{ card.title }}</strong><AppIcon name="arrow" /></RouterLink>
    </nav>
  </div>
</template>

<style scoped>
.hero-head { display: flex; align-items: center; justify-content: space-between; gap: 16px; flex-wrap: wrap; }
.hero-head h1 { margin: 0; }
.hero-actions { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
.risk-todos { margin-top: 16px; display: flex; flex-direction: column; gap: 8px; }
.todo-row { display: grid; grid-template-columns: 96px minmax(0, 1fr) 116px; align-items: center; gap: 14px; padding: 10px 16px; background: #fff; border: 1px solid #e2e8f0; border-radius: 8px; color: #0f172a; text-decoration: none; font-size: 14px; }
.todo-row:hover { border-color: #3b82f6; }
.todo-tag { display: inline-block; font-size: 13px; font-weight: 600; border-radius: 999px; padding: 2px 8px; text-align: center; white-space: nowrap; }
.todo-tag.danger { background: #fef2f2; color: #b91c1c; border: 1px solid #fecaca; }
.todo-tag.warning { background: #fffbeb; color: #b45309; border: 1px solid #fde68a; }
.todo-tag.info { background: #eff6ff; color: #1d4ed8; border: 1px solid #bfdbfe; }
.recent { margin-top: 14px; display: flex; flex-direction: column; gap: 8px; }
.recent-loading { min-height: 180px; border-radius: 10px; background: rgba(255, 255, 255, 0.72); border: 1px solid #e2e8f0; }
.recent-head { display: flex; justify-content: space-between; align-items: baseline; }
.recent-head h2 { font-size: 16px; font-weight: 600; color: var(--cy-text-1); margin: 0 0 4px; display: inline-flex; align-items: baseline; gap: 8px; }
.recent-count { font-size: 13px; font-weight: 400; color: var(--cy-text-3); }
.recent-head a { font-size: 13px; color: #2563eb; text-decoration: none; }
.recent-row { display: grid; grid-template-columns: 76px minmax(0, 1fr) 136px 64px; align-items: center; gap: 16px; padding: 10px 16px; background: #fff; border: 1px solid #e2e8f0; border-radius: 8px; color: #0f172a; text-decoration: none; font-size: 14px; }
.recent-row:hover, .recent-row:focus-visible { border-color: #3b82f6; outline: none; }
.recent-module { font-size: 13px; color: #475569; background: #f1f5f9; border-radius: 4px; padding: 2px 6px; text-align: center; white-space: nowrap; }
.recent-main { display: flex; flex-direction: column; min-width: 0; }
.recent-main small { color: var(--cy-text-2); font-size: 13px; }
.recent-status { color: var(--cy-tone-warn-fg); font-size: 14px; text-align: left; }
.recent-links { display: flex; gap: 16px; }
.recent-go { font-size: 14px; color: var(--el-color-primary); text-align: right; }
.quick-links { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 16px; margin-top: 8px; }
.quick-link { display: flex; align-items: center; gap: 12px; padding: 14px 18px; background: #fff; border: 1px solid var(--cy-line); border-radius: 10px; color: var(--cy-text-1); }
.quick-link strong { flex: 1; font-size: 16px; }
.quick-link:hover { border-color: var(--el-color-primary-light-5); color: var(--el-color-primary); }
.quick-link .feature-icon { width: 36px; height: 36px; margin: 0; }
.viewer-empty { margin: 0; color: #64748b; font-size: 14px; }
@media (max-width: 800px) {
  .recent-head { flex-wrap: wrap; gap: 6px 12px; }
  .recent-links { gap: 12px; flex-wrap: wrap; }
  .recent-row { grid-template-columns: auto minmax(0, 1fr) auto; gap: 8px 10px; padding: 12px 14px; align-items: center; }
  .recent-row .recent-module { order: 1; }
  .recent-row .recent-status { order: 2; font-size: 13px; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .recent-row .recent-go { order: 3; white-space: nowrap; }
  .recent-row .recent-main { grid-column: 1 / -1; order: 4; width: 100%; }
  .todo-row { grid-template-columns: auto minmax(0, 1fr); gap: 8px 10px; padding: 12px 14px; }
  .todo-row .todo-tag { order: 1; justify-self: start; }
  .todo-row .recent-go { order: 2; justify-self: end; white-space: nowrap; }
  .todo-row .recent-main { grid-column: 1 / -1; order: 3; width: 100%; }
  .quick-links { grid-template-columns: 1fr; gap: 10px; }
}
</style>
