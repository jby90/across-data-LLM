<script setup lang="ts">
import { ElMessage } from 'element-plus'
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { api } from '../api'
import { useAuth } from '../auth'
import { localTime, COUNTRY_NAMES } from '@cyshuhe/shared'

interface Entry {
  id: string; country_code: string; name_zh: string; category: string
  authority_zh: string; summary_zh: string; review_status: string; step_count: number; user_shown?: boolean; amount_conflicts?: string[]; authority_why?: string; merged_into?: string | null; merged_into_name?: string; out_reason?: string
}
interface Step { id: string; seq: number; title_zh: string; detail_zh: string; materials: string[]; evidence_quote: string; edited?: boolean }
interface PlanRow { name: string; kind: 'info' | 'form' | 'application' | 'original' | 'none'; fact?: string; what: string; added?: boolean; proves?: boolean }
interface LawRef { source: string; link: string | null }
interface Detail extends Entry {
  evidence_quote: string
  basis: { clause_number: string | null; document: string } | null
  steps: Step[]
  profile?: Record<string, string>
  forms?: Array<{ code: string; name_zh: string; authority_zh: string; version: string }>
  materials_plan?: PlanRow[]
  edited?: string[]
  route?: string[]; checklist?: string[][]; authority_short?: string; steps_generic?: boolean
  authority_note?: string; authority_unnamed?: boolean; authority_hint?: string
  authority_why?: string; authority_roles?: string; authority_original?: string
  laws?: LawRef[]; guide?: LawRef | null; cited_laws?: string[]; form_codes?: string[] | null
  portal?: { site: string; links: Array<{ label: string; url: string }>; verified: string } | null
  conditions?: Array<{ condition: string; proof: string; what: string }>
  evidence_zh?: string
}
// 法规列的每项材料怎么准备：写进申请材料的说明 / 官方表格 / 申请材料本身 / 企业递交时自带 / 不列入
const KIND: Record<PlanRow['kind'], string> = { info: '写进申请材料', form: '官方表格', application: '申请材料本身', original: '企业递交时自带', none: '不列入' }
const FACTS: Array<[string, string]> = [['eligibility', '申请人资格'], ['local_test', '本地检测'], ['local_rep', '本地代表'], ['language', '申请语言'], ['validity', '有效期']]
const LEGACY_UNSPECIFIED = new Set(['', '条文未明确', '\u5f85\u6838\u5b9e', '\u6761\u6587\u672a\u5199\u660e'])
const UNSPECIFIED_AUTHORITY_TEXT = '条文未明确（递交前向受理机构确认）'
const unknown = (v?: string) => !v || LEGACY_UNSPECIFIED.has(v.trim())
const shownFact = (v?: string) => !unknown(v) && v !== '不适用'
function formatAuthority(v?: string | null): string {
  const clean = (v || '').trim()
  if (!clean || LEGACY_UNSPECIFIED.has(clean)) return UNSPECIFIED_AUTHORITY_TEXT
  return clean
}
// 修改资料里能改的固定栏目（与用户端事项卡一致）
const PROFILE_EDIT: Array<[string, string]> = [...FACTS, ['authority_roles', '职责分工（涉及多个机构时说明各自职责）']]

const route = useRoute()
const countryList = ref<Array<{ code: string; name: string }>>([])
const COUNTRIES = computed<Record<string, string>>(() => ({
  ...COUNTRY_NAMES,
  ...Object.fromEntries(countryList.value.map((c) => [c.code, c.name])),
}))
async function loadCountries() {
  try {
    const rows = await api.get<Array<{ code: string; name: string }>>('/admin/admission/countries')
    if (rows.length) countryList.value = rows
  } catch { /* 由事项列表回填国家项 */ }
}
const STATUS: Record<string, string> = { pending_review: '展示中', approved: '已确认', out_of_scope: '范围外（不展示）' }
// 筛选：默认只看用户端在展示的
const FILTERS: Record<string, string> = { shown: '用户端展示中', incomplete: '资料未齐（用户端不展示）', merged: '与另一项重复、已合并', out_of_scope: '范围外（不展示）', all: '全部' }

const allEntries = ref<Entry[]>([])
const serverCounts = ref<{ shown: number; incomplete: number; merged: number; out_of_scope: number; all?: number } | null>(null)
const detail = ref<Detail | null>(null)
const loading = ref(false)
const country = ref(String(route.query.country || route.query.country_code || '').trim())
const status = ref('shown')

watch(
  () => [route.query.country, route.query.country_code],
  ([c1, c2]) => {
    const next = String(c1 || c2 || '').trim()
    if (next !== country.value) {
      country.value = next
      void load()
    }
  },
)

const counts = computed(() => {
  if (serverCounts.value) {
    const sc = serverCounts.value
    return {
      shown: sc.shown ?? 0,
      noSteps: sc.incomplete ?? 0,
      merged: sc.merged ?? 0,
      hidden: sc.out_of_scope ?? 0,
      all: sc.all ?? (sc.shown ?? 0) + (sc.incomplete ?? 0) + (sc.merged ?? 0) + (sc.out_of_scope ?? 0),
    }
  }
  const shown = allEntries.value.filter((e) => e.review_status !== 'out_of_scope' && !e.merged_into && !!e.user_shown).length
  const merged = allEntries.value.filter((e) => !!e.merged_into).length
  const hidden = allEntries.value.filter((e) => !e.merged_into && e.review_status === 'out_of_scope').length
  const noSteps = allEntries.value.filter((e) => e.review_status !== 'out_of_scope' && !e.merged_into && !e.user_shown).length
  return { shown, merged, hidden, noSteps, all: allEntries.value.length }
})

const filterOptions = computed(() => [
  { value: 'shown', label: `${FILTERS.shown}（${counts.value.shown}）` },
  { value: 'incomplete', label: `${FILTERS.incomplete}（${counts.value.noSteps}）` },
  ...(counts.value.merged > 0 ? [{ value: 'merged', label: `${FILTERS.merged}（${counts.value.merged}）` }] : []),
  { value: 'out_of_scope', label: `${FILTERS.out_of_scope}（${counts.value.hidden}）` },
  { value: 'all', label: `${FILTERS.all}（${counts.value.all}）` },
])

const entries = computed(() => {
  const s = status.value || 'all'
  if (s === 'all') return allEntries.value
  if (s === 'shown') {
    return allEntries.value.filter((e) => e.review_status !== 'out_of_scope' && !e.merged_into && !!e.user_shown)
  }
  if (s === 'incomplete') {
    return allEntries.value.filter((e) => e.review_status !== 'out_of_scope' && !e.merged_into && !e.user_shown)
  }
  if (s === 'merged') {
    return allEntries.value.filter((e) => !!e.merged_into)
  }
  if (s === 'out_of_scope') {
    return allEntries.value.filter((e) => !e.merged_into && e.review_status === 'out_of_scope')
  }
  return allEntries.value.filter((e) => e.review_status === s)
})

async function load() {
  loading.value = true
  try {
    const q = [country.value && `country=${encodeURIComponent(country.value)}`, 'status=all'].filter(Boolean).join('&')
    const raw = await api.get<Entry[] | { items: Entry[]; counts?: typeof serverCounts.value }>(`/admin/admission/entries?${q}`)
    if (Array.isArray(raw)) {
      allEntries.value = raw
      serverCounts.value = null
    } else {
      allEntries.value = Array.isArray(raw?.items) ? raw.items : []
      serverCounts.value = raw?.counts ?? null
    }
    if (!countryList.value.length && allEntries.value.length) {
      const codes = [...new Set(allEntries.value.map((e) => e.country_code).filter(Boolean))]
      countryList.value = codes.map((code) => ({ code, name: COUNTRY_NAMES[code] || code }))
    }
  } catch (e) { ElMessage.error((e as Error).message) } finally { loading.value = false }
}
async function open(id: string) {
  try { detail.value = await api.get<Detail>(`/admin/admission/entries/${id}`) }
  catch (e) { ElMessage.error((e as Error).message) }
}
async function setStatus(value: 'approved' | 'pending_review' | 'out_of_scope') {
  if (!detail.value) return
  try {
    await api.patch(`/admin/admission/entries/${detail.value.id}`, { review_status: value })
    detail.value.review_status = value
    const row = allEntries.value.find((e) => e.id === detail.value!.id)
    if (row) row.review_status = value
    ElMessage.success(value === 'out_of_scope' ? '已设为范围外，用户端不再展示' : value === 'approved' ? '已确认' : '已恢复展示')
  } catch (e) { ElMessage.error((e as Error).message) }
}
// ---------- 修改事项资料：直接修改保存后立即生效；人工校订的栏目后续重新识别也不覆盖 ----------
const editing = ref(false)
const saving = ref(false)
interface FormStep { id: string | null; key: string; title_zh: string; materials: string }
const form = ref<{
  name_zh: string; authority_zh: string; authority_note: string; summary_zh: string; steps: FormStep[]; plan: PlanRow[]
  profile: Record<string, string>; laws: string; site: string; links: string; forms_auto: boolean; form_codes: string[]
  applicant: string; conditions: Array<{ condition: string; proof: string; what: string }>
}>({ name_zh: '', authority_zh: '', authority_note: '', summary_zh: '', steps: [], plan: [], profile: {}, laws: '', site: '', links: '', forms_auto: true, form_codes: [], applicant: '', conditions: [] })
const countryForms = ref<Array<{ code: string; name_zh: string }>>([])
let newKey = 0
function move<T>(list: T[], i: number, d: number) {
  const j = i + d
  if (j < 0 || j >= list.length) return
  ;[list[i], list[j]] = [list[j], list[i]]
}
function addStep() { form.value.steps.push({ id: null, key: `new-${++newKey}`, title_zh: '', materials: '' }) }
const newPlanName = ref('')
function addPlanRow() {
  const n = newPlanName.value.trim()
  if (!n || form.value.plan.some((p) => p.name === n)) return
  form.value.plan.push({ name: n, kind: 'original', what: '', added: true })
  newPlanName.value = ''
}
// 递交清单里删一行：管理员加的直接删；环节里列的材料改成“不列入”（要从环节里去掉，就改环节的材料）
function dropPlanRow(i: number) {
  const row = form.value.plan[i]
  if (row.added) form.value.plan.splice(i, 1)
  else row.kind = 'none'  // 环节里的材料、证明资格的原件：记为“不列入”，以后不再自动补回
}
function startEdit() {
  const d = detail.value
  if (!d) return
  const own = d.portal?.verified === '管理员填写' ? d.portal : null
  form.value = {
    name_zh: d.name_zh, authority_zh: d.authority_unnamed ? '' : d.authority_zh || '', authority_note: d.authority_note || '', summary_zh: d.summary_zh || '',
    steps: d.steps.filter((s) => !String(s.id).startsWith('generic-')).map((s) => ({ id: s.id, key: s.id, title_zh: s.title_zh, materials: s.materials.join('\n') })),
    plan: (d.materials_plan || []).map((p) => ({ ...p })),
    profile: Object.fromEntries(
      PROFILE_EDIT.map(([k]) => {
        const rawVal = d.profile?.[k]
        if (k === 'authority_roles') return [k, rawVal && !unknown(rawVal) ? rawVal : d.authority_roles ?? '']
        return [k, rawVal && !unknown(rawVal) ? rawVal : '条文未明确']
      }),
    ),
    laws: (d.cited_laws || []).join('\n'),
    site: own?.site ?? '', links: (own?.links ?? []).map((l) => `${l.label} | ${l.url}`).join('\n'),
    forms_auto: d.form_codes == null, form_codes: d.form_codes ?? (d.forms || []).map((f) => f.code),
    applicant: d.profile?.applicant && !unknown(d.profile.applicant) ? d.profile.applicant : '',
    conditions: (d.conditions || []).map((c) => ({ ...c })),
  }
  api.get<Array<{ code: string; name_zh: string }>>(`/admin/admission/forms?country=${d.country_code}`).then((x) => (countryForms.value = x)).catch(() => (countryForms.value = []))
  editing.value = true
}
async function saveEdit() {
  if (!detail.value) return
  const f = form.value
  if (f.name_zh.trim().length < 2) return ElMessage.warning('事项名称至少 2 个字')
  saving.value = true
  try {
    const d = await api.put<Detail>(`/admin/admission/entries/${detail.value.id}/content`, {
      name_zh: f.name_zh, authority_zh: f.authority_zh, authority_note: f.authority_note, summary_zh: f.summary_zh,
      steps: f.steps.filter((s) => s.title_zh.trim()).map((s) => ({ id: s.id, title_zh: s.title_zh, materials: s.materials.split('\n').map((m) => m.trim()).filter(Boolean) })),
      steps_full: true,
      materials_plan: f.plan.map((p) => ({ name: p.name, kind: p.kind, what: p.what || '' })),
      profile: f.profile,
      cited_laws: f.laws.split('\n').map((x) => x.trim()).filter(Boolean),
      portal: { site: f.site.trim(), links: f.links.split('\n').map((x) => x.split('|').map((y) => y.trim())).filter((x) => x.length === 2 && x[0] && x[1]).map(([label, url]) => ({ label, url })) },
      forms_auto: f.forms_auto, form_codes: f.forms_auto ? null : f.form_codes,
      applicant: f.applicant, conditions: f.conditions.filter((c) => c.condition.trim() || c.proof.trim()),
    })
    detail.value = d
    const row = allEntries.value.find((e) => e.id === d.id)
    if (row) Object.assign(row, { name_zh: d.name_zh, authority_zh: d.authority_zh })
    editing.value = false
    ElMessage.success('已保存，用户端立即生效；正在准备这一事项的企业会收到提醒')
  } catch (e) { ElMessage.error((e as Error).message) } finally { saving.value = false }
}
// 递交清单跟着各环节的材料走：改了材料名称或加了材料，清单里随之出现（新材料默认“企业递交时自带”）
watch(() => form.value.steps.map((x) => x.materials).join('\n'), () => {
  if (!editing.value) return
  const names = [...new Set(form.value.steps.flatMap((x) => x.materials.split('\n').map((m) => m.trim()).filter(Boolean)))]
  const old = new Map(form.value.plan.map((p) => [p.name, p]))
  // 环节里的材料按环节顺序；管理员加的行、证明申请人资格的原件留在原处
  const fromSteps = names.map((n) => old.get(n) ?? { name: n, kind: 'original' as const, what: '' })
  const extra = form.value.plan.filter((p) => (p.added || p.proves) && !names.includes(p.name))
  form.value.plan = [...fromSteps, ...extra]
})
const hand = (field: string) => detail.value?.edited?.includes(field)
async function unmerge() {
  if (!detail.value) return
  try {
    await api.patch(`/admin/admission/entries/${detail.value.id}`, { unmerge: true })
    detail.value.merged_into = null
    const row = allEntries.value.find((e) => e.id === detail.value!.id)
    if (row) row.merged_into = null
    ElMessage.success('已拆开，用户端单独列出这一项')
  } catch (e) { ElMessage.error((e as Error).message) }
}
// 企业准备情况：平台了解各事项的使用情况（哪些事项用得多、常卡在哪、补正几次），用来改进事项资料；
// 申报进度是企业自己记的，平台不跟进办理。只给超级管理员看（运维管理员只维护事项资料）
interface Usage {
  approval_dates?: { issued_on: string; expires_on: string; long_term: boolean }
  id: string; org: string; by: string; country_zh: string; entry: string; function_zh: string; from_compliance: boolean
  missing: string[]; last_draft: string | null; updated_at: string | null; ready: boolean; summary: string
  filing: { state: string; state_zh: string; submitted_on: string; result_on: string; rounds: number }
  approval_status: string
}
const isSuper = useAuth().user?.role === 'platform_admin'
const view = ref<'entries' | 'usage'>('entries')
const usage = ref<Usage[]>([])
// 第 7.5 项 规则 33：同一家企业的几行合并成一格；整列都没有内容的列不占位
function orgSpan({ rowIndex, columnIndex }: { rowIndex: number; columnIndex: number }) {
  if (columnIndex !== 0) return { rowspan: 1, colspan: 1 }
  const rows = usage.value
  if (rowIndex > 0 && rows[rowIndex - 1].org === rows[rowIndex].org) return { rowspan: 0, colspan: 0 }
  let n = 1
  while (rowIndex + n < rows.length && rows[rowIndex + n].org === rows[rowIndex].org) n++
  return { rowspan: n, colspan: 1 }
}
const anyDraft = computed(() => usage.value.some((r) => !!r.last_draft))
async function loadUsage() {
  if (!isSuper) return
  try { usage.value = await api.get<Usage[]>('/admin/admission/projects') } catch (e) { ElMessage.error((e as Error).message) }
}
const usageStats = computed(() => ({
  orgs: new Set(usage.value.map((u) => u.org)).size, total: usage.value.length, ready: usage.value.filter((u) => u.ready).length,
  submitted: usage.value.filter((u) => u.filing && u.filing.state !== 'preparing').length,
  approved: usage.value.filter((u) => u.filing?.state === 'approved').length,
  correcting: usage.value.filter((u) => (u.filing?.rounds ?? 0) > 0).length,
}))
const filingDay = (f: Usage['filing'], a?: Usage['approval_dates']) =>
  f.state === 'approved' && a ? (a.long_term ? `${a.issued_on} 签发，长期有效` : `${a.issued_on} 签发，${a.expires_on} 到期`) : f.state === 'submitted' || f.state === 'correcting' ? `${f.submitted_on} 递交` : f.result_on

onMounted(() => { loadCountries(); load(); loadUsage() })
</script>

<template>
  <div class="management-page" v-loading="loading">
    <header class="page-heading">
      <div><h1>海外准入事项</h1><p>{{ isSuper ? '维护各国准入事项资料；掌握企业各准入事项办理进展与关键办理要点。' : '维护各国准入事项资料。' }}</p></div>
    </header>
    <el-radio-group v-if="isSuper" v-model="view" class="view-tabs cy-page-tabs">
      <el-radio-button value="entries">准入事项</el-radio-button>
      <el-radio-button value="usage" data-testid="admission-usage-tab">企业准备情况（{{ usage.length }}）</el-radio-button>
    </el-radio-group>
    <section v-if="view === 'usage'" data-testid="admission-usage">
      <p class="stat">{{ usageStats.orgs }} 家企业 · {{ usageStats.total }} 个准入项目 · 材料已备齐 {{ usageStats.ready }} 个 · 已递交 {{ usageStats.submitted }} 个，其中被要求补正过 {{ usageStats.correcting }} 个、已取得批文 {{ usageStats.approved }} 个</p>
      <el-table :data="usage" :span-method="orgSpan" class="usage-table">
        <el-table-column prop="org" label="企业" min-width="180" class-name="org-cell" />
        <el-table-column label="国家 / 事项" min-width="240"><template #default="{ row }"><strong>{{ row.country_zh }}</strong> · {{ row.entry }}<div class="sub">{{ row.function_zh || '未选业务' }}<template v-if="row.from_compliance"> · 来自合规评估</template> · {{ row.by }}</div></template></el-table-column>
        <el-table-column label="材料" width="100"><template #default="{ row }"><el-tag v-if="row.filing.state === 'preparing'" :type="row.ready ? 'success' : 'warning'" size="small">{{ row.ready ? '已备齐' : '未备齐' }}</el-tag><span v-else class="sub">已递交</span></template></el-table-column>
        <el-table-column label="申报进度（企业自记）" width="190"><template #default="{ row }"><span data-testid="usage-filing" :title="row.approval_dates?.issued_on ? `签发 ${row.approval_dates.issued_on}` : ''">{{ row.approval_status ? (row.approval_status === '已取得' ? '已取得批文' : `批文${row.approval_status}`) : row.filing.state_zh }}</span><div v-if="filingDay(row.filing, row.approval_dates) || row.filing.rounds" class="sub"><template v-if="filingDay(row.filing, row.approval_dates)">{{ filingDay(row.filing, row.approval_dates) }}</template><template v-if="row.filing.rounds"> · 补正 {{ row.filing.rounds }} 次</template></div></template></el-table-column>
        <el-table-column label="待补事项" min-width="260"><template #default="{ row }"><span v-if="!row.missing.length || row.filing.state !== 'preparing'" class="sub">—</span><span v-else class="miss-text">{{ row.missing.join('、').replace(/还没有/g, '尚未') }}</span></template></el-table-column>
        <el-table-column v-if="anyDraft" label="最近生成申请材料" width="160"><template #default="{ row }">{{ localTime(row.last_draft) || '—' }}</template></el-table-column>
      </el-table>
    </section>
    <template v-else>
    <div class="filters">
      <el-select v-model="country" clearable placeholder="全部国家" style="width:160px" @change="load">
        <el-option v-for="c in countryList" :key="c.code" :label="c.name" :value="c.code" />
      </el-select>
      <el-select v-model="status" clearable placeholder="全部状态" style="width:240px">
        <el-option v-for="opt in filterOptions" :key="opt.value" :label="opt.label" :value="opt.value" />
      </el-select>
    </div>
    <p class="cy-filter-summary">
      用户端展示中 {{ counts.shown }} 项 · 资料未齐 {{ counts.noSteps }} 项<template v-if="counts.merged > 0"> · 已合并 {{ counts.merged }} 项</template> · 范围外 {{ counts.hidden }} 项
    </p>
    <el-table :data="entries" stripe row-key="id" @row-click="(r: Entry) => open(r.id)" style="cursor:pointer">
      <el-table-column v-if="!country" label="国家" width="90"><template #default="{ row }">{{ COUNTRIES[row.country_code] ?? row.country_code }}</template></el-table-column>
      <el-table-column prop="name_zh" label="准入事项" min-width="260" />
      <el-table-column label="受理机构" min-width="280"><template #default="{ row }"><span v-if="!unknown(row.authority_zh)">{{ row.authority_zh }}</span><span v-else class="meta">{{ UNSPECIFIED_AUTHORITY_TEXT }}</span><div v-if="row.authority_why" class="sub">{{ row.authority_why }}</div></template></el-table-column>
      <el-table-column label="环节" width="80"><template #default="{ row }">{{ row.step_count }}</template></el-table-column>
      <el-table-column label="状态" min-width="180" class-name="cy-col-status-wrap"><template #default="{ row }"><span v-if="row.merged_into" class="sub">已合并到（{{ row.merged_into_name }}，同一事项用户端只显示那一项）</span><span v-else-if="row.review_status === 'out_of_scope'" class="sub">范围外：{{ row.out_reason || '与汽车数据和准入业务无关' }}</span><span v-else-if="row.amount_conflicts?.length" class="sub cy-danger-text" data-testid="entry-amount-conflict">人工校订的栏目与条文原文不符，改正前用户端不展示：{{ row.amount_conflicts.join('；') }}</span><span v-else-if="!row.user_shown" class="sub">资料未齐，用户端不展示（{{ !row.step_count ? '缺办理环节' : '缺依据法规' }}）</span><span v-else class="cy-status" :class="row.review_status === 'approved' ? 'cy-status--done' : 'cy-status--muted'">{{ STATUS[row.review_status] }}</span></template></el-table-column>
    </el-table>
    </template>

    <el-drawer :model-value="!!detail" size="min(640px, 94vw)" :with-header="false" @close="detail = null; editing = false">
      <article v-if="detail && editing" class="detail" data-testid="admission-entry-edit">
        <p class="crumb">{{ COUNTRIES[detail.country_code] }} · 修改事项资料</p>
        <el-form label-position="top">
          <el-form-item label="事项名称"><el-input v-model="form.name_zh" maxlength="256" data-testid="edit-name" /></el-form-item>
          <el-form-item label="受理机构"><el-input v-model="form.authority_zh" maxlength="256" data-testid="edit-authority" /></el-form-item>
          <el-form-item label="受理机构依据（条文未直书受理机构、由平台补注时填写）"><el-input v-model="form.authority_note" type="textarea" :autosize="{ minRows: 1, maxRows: 4 }" maxlength="300" placeholder="如：按电信法实施条例第 12 条，频率许可由电信监管局发放" data-testid="edit-authority-note" /></el-form-item>
          <el-form-item label="简介"><el-input v-model="form.summary_zh" type="textarea" :autosize="{ minRows: 2, maxRows: 6 }" maxlength="4000" /></el-form-item>
          <h3>事项卡栏目</h3>
          <p class="meta">条文未明确规定的统一填条文未明确，不适用的填不适用。</p>
          <el-form-item label="申请人"><el-input v-model="form.applicant" maxlength="200" placeholder="如：在埃及商业登记的进口企业" /></el-form-item>
          <el-form-item label="资格条件与证明原件（每条条件均须关联证明原件，自动同步至递交清单）">
            <div class="conds">
              <div v-for="(c, i) in form.conditions" :key="i" class="cond-row" data-testid="edit-condition">
                <el-input v-model="c.condition" size="small" placeholder="条件，如：商业登记满 1 年" />
                <el-input v-model="c.proof" size="small" placeholder="证明原件，如：商业登记摘录" />
                <el-input v-model="c.what" size="small" placeholder="核验要点说明（选填）" />
                <el-button link size="small" type="danger" @click="form.conditions.splice(i, 1)">删除</el-button>
              </div>
              <el-button size="small" data-testid="edit-add-condition" @click="form.conditions.push({ condition: '', proof: '', what: '' })">新增条件</el-button>
            </div>
          </el-form-item>
          <el-form-item v-for="[k, label] in PROFILE_EDIT.filter(([k]) => k !== 'eligibility')" :key="k" :label="label"><el-input v-model="form.profile[k]" type="textarea" :autosize="{ minRows: 1, maxRows: 5 }" maxlength="300" :data-testid="`edit-profile-${k}`" /></el-form-item>
          <el-form-item label="依据法规（法律、法令、部令，每行一部，如 1982 年第 121 号法律；库里有的自动链到原文）"><el-input v-model="form.laws" type="textarea" :autosize="{ minRows: 1, maxRows: 5 }" data-testid="edit-laws" /></el-form-item>
          <el-form-item label="递交渠道：官网（留空默认采用平台已核实入口）"><el-input v-model="form.site" placeholder="https://" data-testid="edit-site" /></el-form-item>
          <el-form-item label="递交渠道：办理页面（每行填写 名称 | 网址）"><el-input v-model="form.links" type="textarea" :autosize="{ minRows: 1, maxRows: 4 }" placeholder="在线申请 | https://..." /></el-form-item>
          <el-form-item label="官方表格">
            <div class="forms-pick"><el-checkbox v-model="form.forms_auto">按事项自动匹配</el-checkbox>
              <el-select v-if="!form.forms_auto" v-model="form.form_codes" multiple placeholder="选择该事项适用的官方表格" style="width: 100%" data-testid="edit-forms">
                <el-option v-for="f in countryForms" :key="f.code" :label="f.name_zh" :value="f.code" />
              </el-select></div>
          </el-form-item>
          <h3>办理环节<el-button size="small" class="hand" data-testid="edit-add-step" @click="addStep">新增环节</el-button></h3>
          <p v-if="!form.steps.length" class="meta">暂无办理步骤资料，用户端显示通用环节。</p>
          <div v-for="(s, i) in form.steps" :key="s.key" class="edit-step">
            <div class="row-tools"><strong>环节 {{ i + 1 }}</strong><span><el-button link size="small" :disabled="i === 0" @click="move(form.steps, i, -1)">上移</el-button><el-button link size="small" :disabled="i === form.steps.length - 1" @click="move(form.steps, i, 1)">下移</el-button><el-button link size="small" type="danger" @click="form.steps.splice(i, 1)">删除</el-button></span></div>
            <el-form-item label="名称"><el-input v-model="s.title_zh" maxlength="256" /></el-form-item>
            <el-form-item label="材料（每行一项）"><el-input v-model="s.materials" type="textarea" :autosize="{ minRows: 1, maxRows: 8 }" /></el-form-item>
          </div>
          <h3>递交清单</h3>
          <p v-if="!form.plan.length" class="meta">条文未列明材料清单。</p>
          <div v-for="(p, i) in form.plan" :key="p.name" class="edit-plan" data-testid="edit-plan-row">
            <span class="plan-name">{{ p.name }}<small v-if="p.proves" class="meta">（证明申请人资格）</small></span>
            <el-select v-model="p.kind" size="small" style="width: 150px">
              <el-option v-for="(label, k) in KIND" :key="k" :label="label" :value="k" :disabled="k === 'info' && !p.fact" />
            </el-select>
            <el-input v-if="p.kind === 'original'" v-model="p.what" size="small" maxlength="120" placeholder="规格或认证要求（选填）" />
            <span class="plan-tools"><el-button link size="small" :disabled="i === 0" @click="move(form.plan, i, -1)">上移</el-button><el-button link size="small" :disabled="i === form.plan.length - 1" @click="move(form.plan, i, 1)">下移</el-button><el-button link size="small" type="danger" @click="dropPlanRow(i)">删除</el-button></span>
          </div>
          <div class="edit-plan"><el-input v-model="newPlanName" size="small" placeholder="新增一项企业递交时自带的原件，如：商业登记摘录" maxlength="400" data-testid="edit-add-plan" @keyup.enter="addPlanRow" /><el-button size="small" @click="addPlanRow">加入清单</el-button></div>
        </el-form>
        <div class="actions">
          <el-button type="primary" :loading="saving" data-testid="edit-save" @click="saveEdit">保存</el-button>
          <el-button @click="editing = false">取消</el-button>
        </div>
      </article>
      <article v-else-if="detail" class="detail">
        <p class="crumb">{{ COUNTRIES[detail.country_code] }} · {{ STATUS[detail.review_status] }}</p>
        <h2>{{ detail.name_zh }}<el-tag v-if="hand('name_zh')" size="small" type="info" class="hand">人工校订</el-tag></h2>
        <p>{{ detail.summary_zh }}<el-tag v-if="hand('summary_zh')" size="small" type="info" class="hand">人工校订</el-tag></p>
        <dl class="facts"><dt>受理机构</dt><dd>{{ formatAuthority(detail.authority_short || detail.authority_zh) }}<el-tag v-if="hand('authority_zh')" size="small" type="info" class="hand">人工校订</el-tag>
          <small v-if="detail.authority_why" class="meta note">{{ detail.authority_why }}</small>
          <small v-if="detail.authority_roles" class="meta note">职责分工：{{ detail.authority_roles }}</small>
          <small v-if="detail.authority_original" class="meta note">条文原文写作：<bdi dir="rtl" class="rtl">{{ detail.authority_original }}</bdi></small></dd>
          <dt>依据法规</dt><dd><template v-if="detail.laws?.length"><div v-for="(l, i) in detail.laws" :key="i">{{ l.source }}</div></template><span v-else class="meta">{{ UNSPECIFIED_AUTHORITY_TEXT }}</span><el-tag v-if="hand('profile:cited_laws')" size="small" type="info" class="hand">人工校订</el-tag>
            <small v-if="detail.guide" class="meta note">参考：官方办事说明 {{ detail.guide.source }}</small></dd>
          <template v-if="detail.portal"><dt>递交渠道</dt><dd><a :href="detail.portal.site" target="_blank" rel="noopener noreferrer">官网</a><a v-for="l in detail.portal.links" :key="l.url" :href="l.url" target="_blank" rel="noopener noreferrer" class="plink">{{ l.label }}</a><el-tag v-if="hand('profile:portal')" size="small" type="info" class="hand">人工校订</el-tag></dd></template>
          <template v-for="[k, label] in FACTS.filter(([k]) => shownFact(detail!.profile?.[k]))" :key="k"><dt>{{ label }}</dt><dd>{{ detail.profile?.[k] }}<el-tag v-if="hand(`profile:${k}`)" size="small" type="info" class="hand">人工校订</el-tag></dd></template>
          <template v-if="FACTS.some(([k]) => k !== 'eligibility' && unknown(detail!.profile?.[k]))"><dt>条文未明确</dt><dd class="meta">{{ FACTS.filter(([k]) => k !== 'eligibility' && unknown(detail!.profile?.[k])).map(([, l]) => l).join('、') }}（递交前向受理机构确认）</dd></template>
          <dt>官方表格</dt><dd><template v-if="detail.forms?.length"><div v-for="f in detail.forms" :key="f.code">{{ f.name_zh.split('：').pop() }}<span class="meta"> · {{ f.authority_zh }} · {{ /^\d/.test(f.version) ? f.version + ' 版' : f.version }}</span></div></template><span v-else class="meta">暂无</span></dd></dl>
        <p class="meta">参考条文：{{ detail.basis?.document }}<template v-if="detail.basis?.clause_number"> {{ detail.basis.clause_number }}</template></p>
        <template v-if="detail.evidence_quote"><p v-if="detail.evidence_zh" class="quote-zh">{{ detail.evidence_zh }}</p>
          <details class="law-steps"><summary>条文原文</summary><blockquote dir="auto">{{ detail.evidence_quote }}</blockquote></details></template>
        <h3>办理环节</h3>
        <details class="law-steps" open><summary>环节与条文原文</summary>
        <p v-if="detail.steps_generic" class="meta">条文明确须履行本事项，未单列具体环节。</p>
        <ol><li v-for="s in detail.steps" :key="s.id"><strong>{{ s.title_zh }}<el-tag v-if="s.edited" size="small" type="info" class="hand">人工校订</el-tag></strong><span>{{ s.detail_zh }}</span>
          <small v-if="s.materials.length">材料：{{ s.materials.join('；') }}</small><blockquote dir="auto">{{ s.evidence_quote }}</blockquote></li></ol></details>
        <template v-if="detail.checklist?.length">
          <h3>递交清单<el-tag v-if="hand('materials_plan')" size="small" type="info" class="hand">人工校订</el-tag></h3>
          <table class="plan" data-testid="admission-entry-plan"><tbody>
            <tr v-for="r in detail.checklist" :key="r[0]"><td>{{ r[0] }}</td><td class="k">{{ r[1] }}</td></tr>
          </tbody></table>
        </template>
        <div class="actions">
          <el-button data-testid="admission-entry-edit-btn" @click="startEdit">修改资料</el-button>
          <el-button v-if="detail.review_status !== 'approved'" type="primary" @click="setStatus('approved')">确认无误</el-button>
          <el-button v-if="detail.review_status === 'out_of_scope'" @click="setStatus('pending_review')">恢复展示</el-button>
          <el-button v-if="detail.merged_into" @click="unmerge">解除合并并独立展示</el-button>
          <el-button v-else type="danger" plain @click="setStatus('out_of_scope')">设为范围外</el-button>
        </div>
      </article>
    </el-drawer>
  </div>
</template>

<style scoped>
.filters { display: flex; gap: 12px; align-items: center; margin-bottom: 0; flex-wrap: wrap; }
.usage-table :deep(td.org-cell) { vertical-align: top; font-weight: 600; background: #fff !important; }
.miss-text { color: var(--cy-text-2); font-size: 13px; line-height: 1.6; }
.stat { color: var(--el-text-color-secondary); font-size: 13px; }
.detail h2 { font-size: 18px; margin: 6px 0 10px; line-height: 1.5; }
small { font-size: 13px; }
.facts { display: grid; grid-template-columns: 84px 1fr; gap: 6px 12px; margin: 10px 0 14px; font-size: 14px; } .facts dt { color: var(--el-text-color-secondary); } .facts dd { margin: 0; }
.crumb { color: var(--el-color-primary); font-size: 13px; margin: 0; }
.meta { color: var(--el-text-color-secondary); font-size: 13px; }
/* 规则 27：外文原文单独成块、比中文弱一档；阿拉伯文按自身方向右对齐 */
blockquote { margin: 8px 0; padding: 8px 12px; border-left: 3px solid var(--el-border-color); background: #f7f9fb; color: var(--cy-text-2); font-size: 13px; line-height: 1.8; white-space: pre-line; unicode-bidi: plaintext; }
blockquote:dir(rtl) { border-left: 0; border-right: 3px solid var(--el-border-color); font-family: 'Noto Naskh Arabic', var(--cy-font); }
ol { padding-left: 20px; } li { margin-bottom: 14px; } li > * { display: block; } li span { line-height: 1.7; margin-top: 2px; } li small { color: var(--el-text-color-secondary); margin-top: 4px; }
/* 规则 28：长抽屉的按钮固定在底部 */
.actions { position: sticky; bottom: -24px; z-index: 3; background: white; padding: 14px 0 18px; margin-top: 16px; border-top: 1px solid var(--cy-line); display: flex; flex-wrap: wrap; gap: 10px; }
.actions .el-button + .el-button { margin-left: 0; }
.view-tabs { margin-bottom: 14px; }
.hand { margin-left: 8px; vertical-align: middle; }
.route { padding-left: 20px; } .route li { margin-bottom: 6px; line-height: 1.7; }
.law-steps { margin: 8px 0 12px; }
.rtl { unicode-bidi: isolate; direction: rtl; }
.plink { margin-left: 12px; }
.forms-pick { display: flex; flex-direction: column; gap: 6px; width: 100%; }
.quote-zh { white-space: pre-line; font-size: 13px; line-height: 1.7; color: var(--el-text-color-regular); }
.conds { display: flex; flex-direction: column; gap: 6px; width: 100%; }
.cond-row { display: grid; grid-template-columns: 1.4fr 1fr 1fr auto; gap: 6px; align-items: center; }
.row-tools { display: flex; justify-content: space-between; align-items: center; font-size: 13px; margin-bottom: 4px; }
.plan-tools { flex: none; white-space: nowrap; } .law-steps summary { cursor: pointer; color: var(--el-text-color-secondary); font-size: 13px; }
.note { display: block; line-height: 1.6; margin-top: 2px; }
.edit-step { border-left: 2px solid var(--el-border-color); padding-left: 10px; margin-bottom: 6px; }
.edit-plan { display: grid; grid-template-columns: minmax(0, 1fr) 150px auto; gap: 6px 12px; align-items: center; padding: 8px 0; border-bottom: 1px solid var(--el-border-color-lighter); font-size: 14px; }
.edit-plan .el-input { grid-column: 1 / -1; }
.plan { width: 100%; border-collapse: collapse; font-size: 14px; margin-bottom: 16px; } .plan td { padding: 8px 6px; border-bottom: 1px solid var(--el-border-color-lighter); vertical-align: top; line-height: 1.6; }
.plan td small { display: block; color: var(--el-text-color-secondary); } .plan td.k { width: 55%; color: var(--cy-text-2); } .sub { color: var(--cy-text-2); font-size: 13px; line-height: 1.6; }
</style>
