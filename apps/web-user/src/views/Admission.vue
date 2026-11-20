<script setup lang="ts">
// 海外属地准入：企业选好国家和业务，平台列出要办的准入事项；每项把申报材料准备好（需要的信息、官方表格、
// 申请材料），平台检查填得对不对、全不全，用户改好后下载，到受理机构递交。平台只帮准备材料：企业自己的证照、
// 检测报告等原件不收，只列成递交时还要带的材料；批文只登记编号和日期（到期提醒），不上传。
// 不对接官方系统：递交以后的进度（已递交、补正中、已取得批文、未通过）由企业自己在申报进度里记。
// 普通查看者只能看，不显示新建、填写、生成、删除；下载看账号有没有下载权限。
import { canEdit, canExport, serverFilename, localTime, statusTone, missingWording, COUNTRY_NAMES } from '@cyshuhe/shared'
import { ElMessage, ElMessageBox } from 'element-plus'
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '../api'
import { useAuth } from '../auth'
import BlocksView, { type Block } from '../components/BlocksView.vue'
import CopilotDrawer from '../components/CopilotDrawer.vue'
import type { CopilotProposal } from '../components/CopilotFillCard.vue'

interface Portal { authority_zh: string; site: string; links: { label: string; url: string }[]; verified: string }
interface Profile { eligibility?: string; local_test: string; local_rep: string; language: string; validity: string }
interface LawRef { source: string; link: string | null }
// 依据法规（法律、法令、部令）和参考的官方办事说明，事项卡、项目页、详情同一写法
interface Basis { laws?: LawRef[]; guide?: LawRef | null; authority_why?: string }
interface Approval { number: string; issued_on: string; expires_on: string; long_term: boolean; days_left: number | null; state: string; status?: string }
interface Readiness { ready: boolean; started: boolean; blocking: number; warnings: number; checked_at: string | null; summary: string; items?: Array<{ target: string; text: string }>; stale?: boolean }
interface PlanItem extends Basis {
  id: string; country_code: string; name_zh: string; category: string; authority_zh: string; summary_zh: string
  step_count: number; portal?: Portal | null; forms: string[]; profile?: Profile
  products_zh?: string; basis?: { source: string; link: string } | null; gate?: string
  project: ({ id: string; filing?: Filing; approval?: Approval | null } & Readiness) | null
}
interface Plan {
  country_code: string; country_zh: string; function: string; function_zh: string
  task: { id: string; title: string } | null; needed: PlanItem[]; maybe: PlanItem[]; other?: { id: string; name_zh: string; summary_zh: string }[]
  functions: { key: string; label: string }[]
}
interface Step { id: string; seq: number; title_zh: string; detail_zh: string; materials: string[]; evidence_quote: string }
interface OfficialForm { code: string; name_zh: string; authority_zh: string; version: string; version_note?: string }
interface EntryDetail extends Basis {
  evidence_zh?: string
  id: string; country_code: string; name_zh: string; category: string; authority_zh: string; summary_zh: string; step_count: number
  evidence_quote: string; authority_hint?: string; authority_unnamed?: boolean; steps_generic?: boolean; authority_note?: string; authority_short?: string
  forms?: OfficialForm[]; portal?: Portal | null; profile?: Profile; authority_original?: string; authority_roles?: string
  basis: { clause_id: string; clause_number: string | null; document: string; document_id?: string; source?: string } | null
  steps: Step[]
}
interface FormState { code: string; name: string; authority_zh: string; version: string; version_note?: string; applies_when?: string; instance_id: string | null; filled: boolean; missing: string[]; carried?: string }
interface Issue { id: string; kind: string; target: string; question: string; answer: string; state: 'open' | 'closed'; updated_at: string | null }
type FilingState = 'preparing' | 'submitted' | 'correcting' | 'approved' | 'rejected'
interface Filing { submitted_draft?: string; renewal?: boolean; state: FilingState; state_zh: string; submitted_on: string; correction_on: string; result_on: string; reason: string; rounds: number; open_questions: number; next: FilingState[]; history: Array<{ state: FilingState; on?: string; at: string; reason?: string }> }
interface Question { key: string; label: string; question: string; why: string }
interface ProjectRow { id: string; entry_id?: string; title: string; country_code: string; entry_name: string; function_zh: string; created_at: string; source: { id: string; title: string; kind: string } | null; summary: string; ready: boolean; approval: Approval | null; filing: Filing }
interface DraftSummary {
  id: string; name_zh: string; created_at: string; missing: string[]
  forms: Array<{ code: string; name: string }>
  has_foreign?: boolean; foreign_lang_code?: string; foreign_lang_zh?: string; foreign_name_zh?: string; foreign_generated_at?: string | null
}
interface ProjectDetail {
  id: string; title: string; applicant: string; vehicle_model: string; function_zh: string
  task: { id: string; title: string; kind: string } | null
  entry: EntryDetail; bring: Array<{ name: string; what: string }>; checklist: string[][]; forms: FormState[]; issues: Issue[]; readiness: Readiness
  drafts: DraftSummary[]
  foreign_lang_code?: string; foreign_lang_zh?: string
  approval: Approval | null
  filing: Filing
  filled: Array<{ key: string; label: string; value: string; editable: boolean; changed: boolean; invalid?: string }>
}
interface Draft { id: string; name_zh: string; blocks: Array<Record<string, unknown>>; missing: string[]; notes?: string[] }
interface LibEntry extends Basis { id: string; country_code: string; name_zh: string; category: string; authority_zh: string; summary_zh: string; step_count: number; products_zh?: string; basis?: { source: string; link: string } | null }

const countryList = ref<Array<{ code: string; name: string }>>([])
const COUNTRIES = computed<Record<string, string>>(() => ({
  ...COUNTRY_NAMES,
  ...Object.fromEntries(countryList.value.map((c) => [c.code, c.name])),
}))
async function loadCountries() {
  try {
    countryList.value = await api.get<Array<{ code: string; name: string }>>('/admission/countries')
  } catch { /* 接口暂不可用时下拉框保持为空 */ }
}
const CATEGORY: Record<string, string> = {
  vehicle_approval: '车辆与零部件型式批准', telecom_approval: '通信设备型号认证',
  data_filing: '数据处理许可与授权', av_testing: '自动驾驶测试与运营授权', other: '其他准入事项',
}

// 产品类别：车型（乘用车、商用车）与车载通信模块（设备类事项）；不选 = 全部
const PRODUCTS: Record<string, string> = { passenger: '乘用车', commercial: '商用车', module: '车载通信模块' }

const route = useRoute()
const router = useRouter()
const auth = useAuth()
const editable = computed(() => canEdit(auth.user))
// 递交以后，信息、表格、申请材料都按递交时那一版只读显示（续期时回到准备中再改）
const sent = computed(() => !!project.value && project.value.filing.state !== 'preparing')
const canChange = computed(() => editable.value && !sent.value)
const submittedDraft = computed(() => project.value?.drafts.find((d) => d.id === project.value?.filing.submitted_draft) ?? project.value?.drafts[0] ?? null)
const shownDrafts = computed(() => (sent.value ? (submittedDraft.value ? [submittedDraft.value] : []) : project.value?.drafts ?? []))
const activeDraft = computed(() => shownDrafts.value[0] ?? null)
const itemShortName = computed(() => (project.value?.entry.name_zh ?? '').replace(/(申请办理|申请|办理)$/, '') || project.value?.entry.name_zh || '')
function lawLink(link: string | null | undefined) {
  if (!link) return ''
  const sep = link.includes('?') ? '&' : '?'
  return `${link}${sep}from=${encodeURIComponent(route.fullPath)}`
}
// 填写项的示例（占位文字）：后端按这一项自己的含义给；对不上的不给，不放灰字
const example = (q: unknown) => (q as { example?: string }).example || ''  // 7.1：只用后端给的示例，没有就不放灰字
// 这个事项已有项目的：事项库里打开它就去项目，不再重复新建准备材料
const projectOfEntry = (entryId: string) => projects.value.find((p) => p.entry_id === entryId) ?? null
interface CatalogForm { code: string; country: string; name_zh: string; authority_zh: string; version: string; language?: string; kind?: string; direction?: string; applies_when?: string }
const downloadable = computed(() => canExport(auth.user))
const tab = ref<'prepare' | 'library' | 'forms'>('prepare')
const formCatalog = ref<CatalogForm[]>([])
const formCountry = ref('')
const formsBusy = ref(false)
async function loadFormCatalog() {
  formsBusy.value = true
  try {
    formCatalog.value = await api.get<CatalogForm[]>('/forms/catalog')
  } catch { /* 目录未加载时保持空表 */ } finally {
    formsBusy.value = false
  }
}
const filteredCatalogForms = computed(() =>
  formCountry.value ? formCatalog.value.filter((f) => f.country === formCountry.value) : formCatalog.value,
)
const groupedCatalogForms = computed(() => {
  const map = new Map<string, CatalogForm[]>()
  for (const f of filteredCatalogForms.value) {
    const arr = map.get(f.country) || []
    arr.push(f)
    map.set(f.country, arr)
  }
  return Array.from(map.entries())
})

// ---------- 准备申报材料：选国家和业务 → 要办的事项 ----------
const planCountry = ref('')
const planFunction = ref('')
const planProduct = ref('')
const planTask = ref<string | null>(null)
const taskOriginCountry = ref('')
const plan = ref<Plan | null>(null)
const planBusy = ref(false)
// 业务与合规评估新建项目时的车辆数据主要用途是同一张表（服务端给出）
const FUNCTIONS = ref<{ key: string; label: string }[]>([])
async function loadFunctions() {
  try {
    FUNCTIONS.value = await api.get<{ key: string; label: string }[]>('/admission/functions')
  } catch { /* 下拉框为空时仍可只选国家 */ }
}
async function loadPlan() {
  if (!planTask.value && !planCountry.value) { plan.value = null; return }
  planBusy.value = true
  try {
    const q = new URLSearchParams()
    if (planTask.value) q.set('task_id', planTask.value)
    if (planCountry.value) q.set('country', planCountry.value)
    if (planFunction.value) q.set('function', planFunction.value)
    if (planProduct.value) q.set('product', planProduct.value)
    plan.value = await api.get<Plan>(`/admission/plan?${q}`)
    if (plan.value.task && planTask.value && !taskOriginCountry.value) {
      taskOriginCountry.value = plan.value.country_code
    }
    planCountry.value = plan.value.country_code
    planFunction.value = plan.value.function
  } catch (e) { ElMessage.error((e as Error).message) } finally { planBusy.value = false }
}
function changeRoute(changedField: 'country' | 'function' = 'function') {
  if (
    changedField === 'country' &&
    planTask.value &&
    taskOriginCountry.value &&
    planCountry.value !== taskOriginCountry.value
  ) {
    planTask.value = null
    taskOriginCountry.value = ''
    if (route.query.task) {
      const { task: _drop, ...rest } = route.query
      router.replace({ query: rest })
    }
  }
  void loadPlan()
}
const opening = ref('')
async function prepare(item: { id: string; project?: { id: string } | null }) {
  if (item.project) return openProject(item.project.id)
  opening.value = item.id
  try {
    const made = await api.post<{ id: string }>('/admission/projects', { entry_id: item.id, task_id: planTask.value, function: planFunction.value })
    await openProject(made.id)
  } catch (e) { ElMessage.error((e as Error).message) } finally { opening.value = '' }
}

// ---------- 准备过的材料 ----------
const projects = ref<ProjectRow[]>([])
const projectsLoading = ref(true)  // 第一次加载项目列表：显示全站统一的加载遮罩，不留空白页
async function loadProjects() {
  try { projects.value = await api.get<ProjectRow[]>('/admission/projects') } catch (e) { ElMessage.error((e as Error).message) } finally { projectsLoading.value = false }
}
async function removeProject(p: { id: string; title: string }) {
  try {
    await ElMessageBox.confirm(`已填信息与生成的申请材料将一并删除且无法恢复。\n\n${p.title}`, '确认删除准入项目', { confirmButtonText: '删除', cancelButtonText: '取消', type: 'warning' })
  } catch { return }
  try {
    await api.delete(`/admission/projects/${p.id}`)
    if (project.value?.id === p.id) closeProject()
    await Promise.all([loadProjects(), loadPlan()])
    ElMessage.success('已删除')
  } catch (e) { ElMessage.error((e as Error).message) }
}

// ---------- 一个事项的申报材料 ----------
const project = ref<ProjectDetail | null>(null)
const draft = ref<Draft | null>(null)
const questions = ref<Question[]>([])
const answers = ref<Record<string, string>>({})
const saving = ref(false)
const checking = ref(false)
const lastFindings = ref<Array<{ target: string; text: string; blocking: boolean }> | null>(null)
async function openProject(id: string) {
  try {
    project.value = await api.get<ProjectDetail>(`/admission/projects/${id}`)
    draft.value = null; lastFindings.value = null
    tab.value = 'prepare'
    if (route.query.project !== id) router.replace({ query: { ...route.query, project: id } })  // 刷新、返回时停在这一项
    await loadQuestions()
    window.scrollTo({ top: 0 })
  } catch (e) { ElMessage.error((e as Error).message) }
}
async function reloadProject() { if (project.value) project.value = await api.get<ProjectDetail>(`/admission/projects/${project.value.id}`) }
function closeProject() {
  project.value = null; draft.value = null
  const { project: _drop, ...rest } = route.query
  router.replace({ query: rest })
  loadProjects(); loadPlan()
}
async function loadQuestions() {
  if (!project.value) return
  const res = await api.get<{ questions: Question[] }>(`/admission/projects/${project.value.id}/questions`)
  questions.value = res.questions
  answers.value = Object.fromEntries(res.questions.map((q) => [q.key, answers.value[q.key] ?? '']))
}
async function saveAnswers() {
  if (!project.value) return
  const filled = Object.fromEntries(Object.entries(answers.value).filter(([, v]) => v.trim()))
  if (!Object.keys(filled).length) return ElMessage.warning('请至少填写一项')
  saving.value = true
  try {
    const res = await api.post<{ questions: Question[] }>(`/admission/projects/${project.value.id}/answers`, { answers: filled })
    questions.value = res.questions
    answers.value = Object.fromEntries(res.questions.map((q) => [q.key, '']))
    await reloadProject()
    ElMessage.success(res.questions.length ? `已保存，还有 ${res.questions.length} 项` : '信息已齐')
  } catch (e) { ElMessage.error((e as Error).message) } finally { saving.value = false }
}
const copilotRef = ref<InstanceType<typeof CopilotDrawer> | null>(null)
const admissionCopilotWelcome = computed(() => {
  if (!project.value) return ''
  return `您好，我是《${project.value.title}》的项目助手。您可以直接输入当地申请主体、商业登记号、数据保护负责人、联系电话或电子邮箱，系统将自动提取填报项供您核对填入；也可随时咨询该事项的材料要求。`
})
async function applyAdmissionProposals(items: CopilotProposal[]) {
  if (!project.value || !items.length) return
  const patch: Record<string, string> = {}
  for (const it of items) patch[it.key] = Array.isArray(it.value) ? it.value.join('、') : String(it.value ?? '')
  const res = await api.post<{ questions: Question[] }>(`/admission/projects/${project.value.id}/answers`, { answers: patch })
  questions.value = res.questions
  answers.value = Object.fromEntries(res.questions.map((q) => [q.key, answers.value[q.key] ?? '']))
  await reloadProject()
  window.setTimeout(() => {
    const sec = document.getElementById('sec-info')
    sec?.scrollIntoView({ behavior: 'smooth', block: 'center' })
    sec?.classList.add('flash')
    window.setTimeout(() => sec?.classList.remove('flash'), 2200)
  }, 150)
  ElMessage.success(`已联动填入 ${items.length} 项申报信息`)
}
async function undoAdmissionProposals(items: CopilotProposal[]) {
  if (!project.value || !items.length) return
  const patch: Record<string, string> = {}
  for (const it of items) patch[it.key] = Array.isArray(it.previous_value) ? it.previous_value.join('、') : String(it.previous_value ?? '')
  const res = await api.post<{ questions: Question[] }>(`/admission/projects/${project.value.id}/answers`, { answers: patch })
  questions.value = res.questions
  answers.value = Object.fromEntries(res.questions.map((q) => [q.key, answers.value[q.key] ?? '']))
  await reloadProject()
  ElMessage.info(`已撤回刚才填入的 ${items.length} 项申报信息`)
}
const formBusy = ref('')
async function fillForm(f: FormState) {
  if (!project.value) return
  const query: Record<string, string> = {
    project: project.value.id,
    back: `/admission?project=${project.value.id}`,
  }
  if (project.value.task?.id) query.task_id = project.value.task.id
  if (f.instance_id) {
    await router.push({ name: 'form', params: { id: f.instance_id }, query })
    return
  }
  await router.push({ name: 'form', params: { id: `preview:${f.code}` }, query })
}
async function fetchFile(url: string, fallback: string) {
  const res = await fetch(url, { headers: { Authorization: `Bearer ${api.token()}` } })
  if (!res.ok) {
    const err = await res.json().catch(() => null)
    throw new Error(typeof err?.detail === 'string' ? err.detail : '下载失败，可重试')
  }
  const href = URL.createObjectURL(await res.blob())
  const a = document.createElement('a'); a.href = href; a.download = serverFilename(res, fallback)
  document.body.appendChild(a); a.click(); a.remove()
  window.setTimeout(() => URL.revokeObjectURL(href), 30000)
}
async function downloadForm(f: FormState) {
  if (!downloadable.value || !f.instance_id) return
  try { await fetchFile(`/api/forms/instances/${f.instance_id}/export.pdf`, `${f.name}.pdf`) } catch (e) { ElMessage.error((e as Error).message) }
}
async function makeDraft() {
  if (!project.value) return
  try {
    const made = await api.post<Draft>(`/admission/projects/${project.value.id}/drafts`)
    await reloadProject()
    draft.value = made
    ElMessage.success(made.missing.length ? `申请材料已生成，还有 ${made.missing.length} 项待补充` : '申请材料已生成')
  } catch (e) { ElMessage.error((e as Error).message) }
}
async function showDraft(id: string) {
  try { draft.value = await api.get<Draft>(`/admission/drafts/${id}`) } catch (e) { ElMessage.error((e as Error).message) }
}
async function downloadDraft(id: string, ext: 'docx' | 'pdf') {
  if (!downloadable.value) return
  try { await fetchFile(`/api/admission/drafts/${id}/export.${ext}`, `申请材料.${ext}`) } catch (e) { ElMessage.error((e as Error).message) }
}
// 每一版生成时填好的官方表格随这一版留存，按当时的内容再下载
async function downloadDraftForm(id: string, f: { code: string; name: string }) {
  if (!downloadable.value) return
  try { await fetchFile(`/api/admission/drafts/${id}/forms/${f.code}`, `${f.name}.pdf`) } catch (e) { ElMessage.error((e as Error).message) }
}
const foreignBusy = ref('')
async function makeForeignDraft(id: string) {
  if (!project.value) return
  foreignBusy.value = id
  try {
    const made = await api.post<Draft>(`/admission/drafts/${id}/foreign`)
    await reloadProject()
    draft.value = made
    ElMessage.success(`${project.value.foreign_lang_zh || '外文'}正式稿已生成并完成核对`)
  } catch (e) { ElMessage.error((e as Error).message) } finally { foreignBusy.value = '' }
}
async function showForeignDraft(id: string) {
  try { draft.value = await api.get<Draft>(`/admission/drafts/${id}/foreign`) } catch (e) { ElMessage.error((e as Error).message) }
}
async function downloadForeignDraft(id: string, ext: 'docx' | 'pdf') {
  if (!downloadable.value) return
  const label = project.value?.foreign_lang_zh ? `申请书（${project.value.foreign_lang_zh}正式稿）` : '申请书外文正式稿'
  try { await fetchFile(`/api/admission/drafts/${id}/foreign/export.${ext}`, `${label}.${ext}`) } catch (e) { ElMessage.error((e as Error).message) }
}
const allVersions = ref(false)
async function runCheck() {
  if (!project.value) return
  checking.value = true
  try {
    const res = await api.post<{ findings: Array<{ target: string; text: string; blocking: boolean }> } & Readiness>(`/admission/projects/${project.value.id}/check`)
    lastFindings.value = res.findings
    await reloadProject()
    ElMessage[res.ready ? 'success' : 'warning'](res.summary)
  } catch (e) { ElMessage.error((e as Error).message) } finally { checking.value = false }
}
// 以前针对原件上传的检查结果（指向 file:）不再显示：原件只列清单，不收
const shownIssue = (i: Issue) => i.kind === 'check' && !i.target.startsWith('file:')
const openIssues = computed(() => project.value?.issues.filter((i) => shownIssue(i) && i.state === 'open') ?? [])
const fixedIssues = computed(() => project.value?.issues.filter((i) => shownIssue(i) && i.state === 'closed') ?? [])
function goTo(target: string) {
  const sec = target.startsWith('info') ? 'sec-info' : target.startsWith('form') ? 'sec-forms' : 'sec-draft'
  document.getElementById(sec)?.scrollIntoView({ behavior: 'smooth', block: 'start' })
}
function backToReport() {
  const t = project.value?.task
  if (!t) return
  router.push(t.kind === 'documents' ? { path: '/documents', query: { project: t.id } } : { path: '/analysis', query: { task: t.id } })
}
const UNNAMED_CLAUSE = '条文未明确（递交前向受理机构确认）'
const isAuthorityUnnamed = (v?: string | null) => !v || !v.trim() || v.includes('未写明') || v === '条文未明确' || v === UNNAMED_CLAUSE
const authorityText = (e: EntryDetail) => (!isAuthorityUnnamed(e.authority_short) ? e.authority_short! : !isAuthorityUnnamed(e.authority_zh) ? e.authority_zh! : UNNAMED_CLAUSE)
// 条文没写受理机构时，写明按什么确定（平台给出，详情里显示）
const authorityWhy = (e: Basis) => e.authority_why || ''

// 事项卡的固定填写说明项：条文未明确时统一提示递交前向受理机构确认
const FACTS: Array<[keyof Profile, string]> = [['eligibility', '申请人资格'], ['local_test', '本地检测'], ['local_rep', '本地代表'], ['language', '申请语言'], ['validity', '有效期']]
const unknown = (v?: string) => !v || v === '待核实' || v === '条文未明确'
// 不适用的项（如申请人本身须为当地主体时的本地代表）不显示，也不列进待确认提示
const shownFact = (v?: string) => !unknown(v) && v !== '不适用'
const noRule = (p?: Profile) => FACTS.filter(([k]) => k !== 'eligibility' && unknown(p?.[k])).map(([, l]) => l)
const askLine = (p?: Profile) => `递交前向受理机构确认：${noRule(p).join('、')}`
// 每份材料的状态：未填写 / 未生成 / 填写中 / 待补正 / 通过（还没检查过时显示已填好或已生成）
function matState(present: boolean, target: string, pending = false) {
  if (!present) return { text: target.startsWith('form:') ? '未填写' : '未生成', cls: 'none' }
  if (pending && target.startsWith('form:')) return { text: '填写中', cls: 'doing' }  // 表还没填完（检查查出的不一致另算待补正）
  if (pending && target === 'draft') return { text: '需重新生成', cls: 'fix' }
  if (pending || openIssues.value.some((i) => i.target === target)) return { text: '待补正', cls: 'fix' }
  return { text: project.value?.readiness.checked_at ? '通过' : target === 'draft' ? '已生成' : '已填好', cls: 'ok' }
}
const draftStale = computed(() => !!project.value?.readiness.stale)

// ---------- 取得批文后登记（证照台账、到期提醒） ----------
const approvalForm = ref({ number: '', issued_on: '', expires_on: '', long_term: false })
const editingApproval = ref(false)
const savingApproval = ref(false)
watch(project, (p) => {
  const a = p?.approval
  approvalForm.value = { number: a?.number ?? '', issued_on: a?.issued_on ?? '', expires_on: a?.expires_on ?? '', long_term: a?.long_term ?? false }
})
async function saveApproval() {
  if (!project.value) return
  const f = approvalForm.value
  if (!f.number.trim()) return ElMessage.warning('请填写批文或证书编号')
  if (!f.long_term && !f.expires_on) return ElMessage.warning('请填写有效期至，或勾选长期有效')
  if (!f.long_term && f.issued_on && f.expires_on && f.expires_on < f.issued_on) {
    return ElMessage.warning('批文有效期届满日期不能早于签发日期')
  }
  savingApproval.value = true
  try {
    await api.put(`/admission/projects/${project.value.id}/approval`, f)
    await reloadProject()
    editingApproval.value = false
    filingForm.value = { to: '', on: '', reason: '', questions: '' }
    ElMessage.success('已登记批文，到期前 90 天会提醒')
  } catch (e) { ElMessage.error((e as Error).message) } finally { savingApproval.value = false }
}
// ---------- 申报进度：企业递交后自己记（已递交 → 补正中 → 已取得批文 / 未通过） ----------
const today = () => new Date().toLocaleDateString('sv-SE')
const filingForm = ref<{ to: '' | 'submitted' | 'correcting' | 'rejected' | 'approved'; on: string; reason: string; questions: string }>({ to: '', on: '', reason: '', questions: '' })
const filingBusy = ref(false)
async function renew() {
  if (!project.value) return
  filingBusy.value = true
  try {
    await api.put(`/admission/projects/${project.value.id}/filing`, { state: 'renewing' })
    await reloadProject(); await loadQuestions()
    ElMessage.success('已回到准备中：按最新要求准备续期材料，原批文留在记录里')
  } catch (e) { ElMessage.error((e as Error).message) } finally { filingBusy.value = false }
}
function startFiling(to: 'submitted' | 'correcting' | 'rejected' | 'approved') {
  filingForm.value = { to, on: today(), reason: '', questions: '' }
  if (to === 'approved') editingApproval.value = true
}
async function saveFiling(to?: 'submitted' | 'correcting' | 'rejected' | 'preparing') {
  if (!project.value) return
  const f = filingForm.value
  const state = to ?? f.to
  if (!state || state === 'approved') return
  const questions = f.questions.split(String.fromCharCode(10)).map((q) => q.trim()).filter(Boolean)
  if (state === 'correcting' && !questions.length) return ElMessage.warning('请填写受理机构反馈的补正意见（每行一条）')
  if (state === 'rejected' && !f.reason.trim()) return ElMessage.warning('请填写未获批原因说明')
  filingBusy.value = true
  try {
    await api.put(`/admission/projects/${project.value.id}/filing`, { state, on: to ? '' : f.on, reason: f.reason, questions })
    filingForm.value = { to: '', on: '', reason: '', questions: '' }
    await reloadProject()
    ElMessage.success(`已改为：${FILING_ZH[state]}`)
  } catch (e) { ElMessage.error((e as Error).message) } finally { filingBusy.value = false }
}
const FILING_ZH: Record<FilingState, string> = { preparing: '准备中', submitted: '已递交', correcting: '补正中', approved: '已取得批文', rejected: '未通过' }
// 受理机构提的问题（补正中时记下），处理好一条勾一条
const authorityIssues = computed(() => project.value?.issues.filter((i) => i.kind === 'authority') ?? [])
const answerDraft = ref<Record<string, string>>({})
async function closeAuthority(i: Issue) {
  try {
    await api.patch(`/admission/issues/${i.id}`, { state: i.state === 'open' ? 'closed' : 'open', answer: answerDraft.value[i.id] ?? i.answer })
    await reloadProject()
  } catch (e) { ElMessage.error((e as Error).message) }
}
const newAuthority = ref('')
async function addAuthority() {
  if (!project.value || newAuthority.value.trim().length < 2) return
  try {
    await api.post(`/admission/projects/${project.value.id}/issues`, { question: newAuthority.value.trim(), kind: 'authority' })
    newAuthority.value = ''
    await reloadProject()
  } catch (e) { ElMessage.error((e as Error).message) }
}
// 项目列表、首页上的一句话：递交以后显示申报进度，递交之前显示材料准备情况
function stateLine(p: { summary: string; ready: boolean; filing?: Filing; approval?: Approval | null }) {
  const f = p.filing
  if (p.approval?.status) return { text: p.approval.status === '已取得' ? `已取得批文 · ${approvalText(p.approval)}` : `批文${p.approval.status} · ${approvalText(p.approval)}`, ok: p.approval.status === '已取得' }
  if (f?.renewal && f.state === 'preparing') return { text: `准备续期 · ${p.summary}`, ok: false }
  if (!f || f.state === 'preparing') return { text: p.summary, ok: p.ready }
  const day = f.state === 'submitted' ? f.submitted_on : f.state === 'correcting' ? f.correction_on : f.result_on
  return { text: `${f.state_zh}${day ? ` · ${day}` : ''}`, ok: f.state === 'approved' }
}
// 第 7.5 项三轮 规则 12：状态列只放短状态词；长句（还差什么）放进名称下面的说明行
function shortState(p: { approval?: Approval | null; filing?: Filing; ready?: boolean }): string {
  if (p.approval?.status) return p.approval.status === '已取得' ? '已取得批文' : `批文${p.approval.status}`
  return p.filing?.state_zh || '准备中'
}
// 规则 15：列表里的状态按全站一套色调上色（只定颜色，文字照旧）
const toneOf = (s: { text: string; ok: boolean }) => (s.ok ? 'done' : statusTone(s.text))
// 待补提示框只写一次缺口汇总，下面只列字段名；其余缺口（如申请材料未生成）各占一个标签
const INFO_PREFIX = /^还缺信息[:：]\s*/
const tagsOpen = ref(false)
const gapTags = computed(() => (project.value?.readiness.items ?? []).map((it) => ({ target: it.target, text: it.text.replace(INFO_PREFIX, '').replace(/还没有/g, '尚未'), info: INFO_PREFIX.test(it.text) })))
const infoTags = computed(() => gapTags.value.filter((t) => t.info))
const readyTitle = computed(() => {
  // 数字只取后端状态句里的（项目页、列表、概览三处同一个来源），不再自己重数
  const r = project.value?.readiness
  return r ? missingWording(r.summary).replace(/还没有/g, '尚未') : ''
})
const approvals = computed(() => projects.value.filter((p) => p.approval))
const expiring = computed(() => approvals.value.filter((p) => p.approval && ['即将到期', '已过期'].includes(p.approval.state)))
const expiringText = computed(() => {
  const soon = expiring.value.filter((p) => p.approval!.state === '即将到期'), gone = expiring.value.filter((p) => p.approval!.state === '已过期')
  const line = (ps: ProjectRow[]) => ps.map((p) => `${p.entry_name}（${approvalText(p.approval!)}）`).join('；')
  return [gone.length ? `${gone.length} 项批文已过期，需要续期：${line(gone)}` : '', soon.length ? `${soon.length} 项批文即将到期：${line(soon)}` : ''].filter(Boolean).join('。')
})
// 选了国家，项目和批文也只列这个国家的；超过 5 个时默认折叠其余项
const ADM_FOLD_LIMIT = 5
const admProjectsExpanded = ref(false)
const shownProjects = computed(() => projects.value.filter((p) => !planCountry.value || p.country_code === planCountry.value))
const visibleShownProjects = computed(() => (admProjectsExpanded.value ? shownProjects.value : shownProjects.value.slice(0, ADM_FOLD_LIMIT)))
const scopeName = computed(() => (planCountry.value ? COUNTRIES.value[planCountry.value] ?? planCountry.value : '全部国家'))
// 已填信息：点修改在原处改
const editingFact = ref('')
const factDraft = ref('')
async function saveFact(key: string) {
  if (!project.value || !factDraft.value.trim()) return
  try {
    await api.post(`/admission/projects/${project.value.id}/answers`, { answers: { [key]: factDraft.value.trim() } })
    editingFact.value = ''
    await reloadProject(); await loadQuestions()
    ElMessage.success('已保存')
  } catch (e) { ElMessage.error((e as Error).message) }
}
// 提示条的主按钮：直接去第一处要补的地方
const firstGap = computed(() => project.value?.readiness.items?.[0] ?? null)
const gapLabel = (t: string) => (t.startsWith('info') ? '补充信息' : t.startsWith('form') ? '填写官方表格' : t === 'draft' ? (draftStale.value ? '更新申请材料' : '生成申请材料') : '前往修改')
const approvalText = (a: Approval) =>
  a.state === '长期有效' ? '长期有效' : a.days_left === null ? '有效期待填' : a.days_left < 0 ? `已过期 ${-a.days_left} 天` : `${a.expires_on} 到期，剩 ${a.days_left} 天`

// ---------- 关联合规评估项目：评估里答过的企业、数据、接收方信息带入表格和申请材料 ----------
const docProjects = ref<Array<{ id: string; title: string }>>([])
const linking = ref(false)
async function loadDocProjects() {
  try { docProjects.value = await api.get<Array<{ id: string; title: string }>>('/analysis/doc-projects') } catch { /* 不关联也能准备 */ }
}
async function linkTask(id: string | null) {
  if (!project.value) return
  linking.value = true
  try {
    await api.put(`/admission/projects/${project.value.id}/link`, { task_id: id })
    await reloadProject()
    ElMessage.success(id ? '已关联，评估里的信息会带入表格和申请材料' : '已取消关联')
  } catch (e) { ElMessage.error((e as Error).message) } finally { linking.value = false }
}

// ---------- 准入事项库（按国家浏览） ----------
const libCountry = ref('')
const libProduct = ref('')
const libEntries = ref<LibEntry[]>([])
const libBusy = ref(false)
// 要办的事项按类别分组（型式批准、通信设备、数据处理……），与事项库同一种分法
const neededGrouped = computed(() => {
  const map = new Map<string, PlanItem[]>()
  for (const e of plan.value?.needed ?? []) map.set(e.category, [...(map.get(e.category) ?? []), e])
  return [...map.entries()]
})
const grouped = computed(() => {
  const map = new Map<string, LibEntry[]>()
  for (const e of libEntries.value) map.set(e.category, [...(map.get(e.category) ?? []), e])
  return [...map.entries()]
})
async function loadLibrary() {
  libBusy.value = true
  const q = new URLSearchParams()
  if (libCountry.value) q.set('country', libCountry.value)
  if (libProduct.value) q.set('product', libProduct.value)
  try { libEntries.value = await api.get<LibEntry[]>(`/admission/entries${q.size ? `?${q}` : ''}`) }
  catch (e) { ElMessage.error((e as Error).message) } finally { libBusy.value = false }
}
const entryOpen = ref(false)
const entryLoading = ref(false)
const opened = ref<EntryDetail | null>(null)
async function openEntry(id: string) {
  entryOpen.value = true; entryLoading.value = true; opened.value = null
  try { opened.value = await api.get<EntryDetail>(`/admission/entries/${id}`) }
  catch (e) { entryOpen.value = false; ElMessage.error((e as Error).message) } finally { entryLoading.value = false }
}
async function prepareFromLibrary() {
  if (!opened.value) return
  const e = opened.value
  entryOpen.value = false
  planTask.value = null
  taskOriginCountry.value = ''
  planCountry.value = e.country_code
  await loadPlan()
  await prepare({ id: e.id, project: [...(plan.value?.needed ?? []), ...(plan.value?.maybe ?? [])].find((x) => x.id === e.id)?.project ?? null })
}

const copilotOpen = ref(false)
const copilotWidth = ref(390)
const copilotStyle = computed(() => (copilotOpen.value ? { '--copilot-pad': `${Math.max(24, copilotWidth.value - 20)}px` } : { '--copilot-pad': '24px' }))

async function fromRoute() {
  const pid = route.query.project
  const tid = route.query.task
  const qTab = route.query.tab
  if (qTab === 'forms' || qTab === 'library' || qTab === 'prepare') {
    tab.value = qTab
  }
  if (typeof tid === 'string' && planTask.value !== tid) { planTask.value = tid; taskOriginCountry.value = ''; planCountry.value = ''; planFunction.value = ''; await loadPlan() }
  if (typeof pid === 'string' && project.value?.id !== pid) await openProject(pid)
}
watch(() => [route.query.project, route.query.task, route.query.tab], fromRoute)
onMounted(async () => { loadCountries(); loadFunctions(); loadProjects(); loadLibrary(); loadFormCatalog(); loadDocProjects(); await fromRoute() })
</script>

<template>
  <div class="management-page admission-page adm-page">
    <header v-if="!project" class="page-heading">
      <div><h1>海外属地准入</h1><p>{{ editable ? '按目标国家与业务场景梳理准入事项，在线编制申报材料并跟踪批文进度。' : '查阅本企业各准入事项的申报材料与批文状态。' }}</p></div>
    </header>

    <article v-if="project" class="project-detail" :style="copilotStyle" data-testid="admission-project">
      <div class="back-row">
        <el-button link @click="closeProject">← 海外属地准入</el-button>
        <el-button v-if="project.task" link type="primary" data-testid="back-to-report" @click="backToReport">返回关联评估项目：{{ project.task.title }}</el-button>
        <el-button link type="primary" data-testid="admission-open-copilot" @click="copilotRef?.openPanel()">项目助手</el-button>
      </div>
      <header class="project-head">
        <div>
          <h1 class="project-title">{{ project.entry.name_zh }}<span class="filing-chip" :class="project.approval?.status === '需续期' ? 'rejected' : project.approval?.status === '即将到期' ? 'correcting' : project.filing.state" data-testid="admission-filing-chip">{{ project.approval?.status && project.approval.status !== '已取得' ? `批文${project.approval.status}` : project.filing.state_zh }}</span></h1>
          <p class="meta">{{ COUNTRIES[project.entry.country_code] }} · {{ project.function_zh || '通用业务场景' }} · {{ CATEGORY[project.entry.category] }}</p>
        </div>
        <el-button v-if="editable" link class="cy-danger-quiet" @click="removeProject(project)">删除</el-button>
      </header>
        <ol class="filing-steps top" data-testid="admission-filing-steps">
          <li :class="{ done: project.filing.state !== 'preparing', now: project.filing.state === 'preparing' }"><strong>准备材料</strong></li>
          <li :class="{ done: ['correcting', 'approved', 'rejected'].includes(project.filing.state), now: project.filing.state === 'submitted' }"><strong>已递交</strong><small v-if="project.filing.submitted_on">{{ project.filing.submitted_on }}</small></li>
          <li v-if="project.filing.state === 'correcting' || project.filing.rounds" :class="{ now: project.filing.state === 'correcting', done: project.filing.state !== 'correcting' }"><strong>补正</strong><small v-if="project.filing.rounds">{{ project.filing.rounds > 1 ? `共 ${project.filing.rounds} 次` : project.filing.correction_on }}</small></li>
          <li :class="{ now: ['approved', 'rejected'].includes(project.filing.state), bad: project.filing.state === 'rejected' }"><strong>{{ project.filing.state === 'rejected' ? '未通过' : '取得批文' }}</strong><small v-if="project.filing.result_on">{{ project.filing.result_on }}</small></li>
        </ol>
      <dl class="where-box">
        <dt>受理机构</dt><dd>{{ authorityText(project.entry) }}<small v-if="authorityWhy(project.entry)" class="auth-note" data-testid="admission-authority-note">{{ authorityWhy(project.entry) }}</small>
          <small v-if="project.entry.authority_roles" class="auth-note">职责分工：{{ project.entry.authority_roles }}</small>
          <small v-if="project.entry.authority_original" class="auth-note">条文原文写作：<bdi dir="rtl" class="rtl">{{ project.entry.authority_original }}</bdi></small></dd>
        <template v-if="project.entry.portal || project.entry.guide"><dt>递交渠道</dt><dd class="links"><template v-if="project.entry.portal"><a :href="project.entry.portal.site" target="_blank" rel="noopener noreferrer">官网</a>
          <a v-for="l in project.entry.portal.links" :key="l.url" :href="l.url" target="_blank" rel="noopener noreferrer">{{ l.label }}</a></template>
          <router-link v-if="project.entry.guide?.link" :to="lawLink(project.entry.guide.link)">官方办事说明</router-link></dd></template>
        <dt>依据法规</dt><dd data-testid="admission-laws"><template v-if="project.entry.laws?.length"><template v-for="(l, i) in project.entry.laws" :key="i"><template v-if="i">、</template><router-link v-if="l.link" :to="lawLink(l.link)">{{ l.source }}</router-link><span v-else>{{ l.source }}</span></template></template><span v-else class="muted">{{ UNNAMED_CLAUSE }}</span>
</dd>
        <template v-for="[k, label] in FACTS.filter(([k]) => shownFact(project!.entry.profile?.[k]))" :key="k"><dt>{{ label }}</dt><dd>{{ project.entry.profile?.[k] }}</dd></template>
        <template v-if="noRule(project.entry.profile).length"><dt>条文未明确</dt><dd class="muted" data-testid="admission-no-rule">{{ askLine(project.entry.profile) }}</dd></template>
      </dl>
      <!-- 第 7.5 项 规则 37：参考类内容紧跟概要，可展开，不压在页底 -->
      <details class="law">
        <summary>办理环节与条文依据（{{ project.entry.steps.length }} 项）</summary>
        <p v-if="project.entry.steps_generic" class="muted">法规明确须履行本项准入义务但未列明具体流程，以下为通用办理环节。</p>
        <ol class="step-list"><li v-for="s in project.entry.steps" :key="s.id"><strong>{{ s.title_zh }}</strong><span>{{ s.detail_zh }}</span>
          <small v-if="s.materials.length">材料：{{ s.materials.join('；') }}</small>
          <details v-if="s.evidence_quote" class="orig"><summary>条文原文</summary><blockquote dir="auto">{{ s.evidence_quote }}</blockquote></details></li></ol>
      </details>

      <!-- 还差什么只在准备阶段说；递交以后看申报进度 -->
      <div v-if="project.filing.state === 'preparing'" class="ready-bar" :class="{ ok: project.readiness.ready }" data-testid="admission-readiness">
        <div class="ready-main"><strong>{{ readyTitle }}</strong>
          <span v-if="infoTags.length" class="ready-items"><button v-for="(it, n) in (tagsOpen ? infoTags : infoTags.slice(0, 8))" :key="n" type="button" class="ready-tag" @click="goTo(it.target)">{{ it.text }}</button><button v-if="infoTags.length > 8" type="button" class="ready-more" @click="tagsOpen = !tagsOpen">{{ tagsOpen ? '收起' : `还有 ${infoTags.length - 8} 项` }}</button></span></div>
        <span v-if="project.readiness.checked_at" class="muted">最近核验 {{ localTime(project.readiness.checked_at) }}</span>
        <el-button v-if="firstGap && editable" type="primary" data-testid="admission-first-gap" @click="goTo(firstGap.target)">{{ gapLabel(firstGap.target) }}</el-button>
        <el-button v-if="project.readiness.started && editable" :type="project.readiness.ready ? 'success' : 'default'" :loading="checking" data-testid="admission-check" @click="runCheck">核验材料</el-button>
      </div>
      <section v-if="openIssues.length || fixedIssues.length" class="issues" data-testid="admission-issues">
        <h3>核验结果<small v-if="openIssues.length">待修正 {{ openIssues.length }} 项</small></h3>
        <button v-for="i in openIssues" :key="i.id" type="button" class="issue-row" @click="goTo(i.target)"><span>{{ i.question }}</span><small>前往修改</small></button>
        <details v-if="fixedIssues.length"><summary>已修正 {{ fixedIssues.length }} 项</summary>
          <p v-for="i in fixedIssues" :key="i.id" class="fixed">{{ i.question }}<small>{{ localTime(i.updated_at) }}</small></p></details>
      </section>

      <section id="sec-info" class="block">
        <h3 class="sec-h"><span>1. 申报基础信息<small v-if="sent">递交存档内容</small><small v-else>{{ questions.length || project.filled.some((f) => f.invalid) ? `还缺 ${questions.length + project.filled.filter((f) => f.invalid).length} 项` : '已齐' }}</small></span><el-button v-if="canChange" size="small" data-testid="admission-open-copilot" @click="copilotRef?.openPanel()">辅助填写</el-button></h3>
        <dl v-if="project.filled.length" class="filled" data-testid="admission-filled">
          <template v-for="f in project.filled" :key="f.key"><dt>{{ f.label }}</dt>
            <dd v-if="editingFact === f.key" class="fact-edit"><el-input v-model="factDraft" type="textarea" :rows="2" maxlength="1000" /><span><el-button size="small" type="primary" @click="saveFact(f.key)">保存</el-button><el-button size="small" @click="editingFact = ''">取消</el-button></span></dd>
            <dd v-else><span class="fact-v">{{ f.value }}</span><span v-if="f.invalid" class="mat-state miss" data-testid="admission-fact-invalid">{{ f.invalid }}</span><span v-else-if="f.changed && !sent" class="mat-state fix" data-testid="admission-fact-changed">已变更</span>
              <el-button v-if="f.editable && canChange" link type="primary" size="small" @click="editingFact = f.key; factDraft = f.value">修改</el-button></dd></template>
        </dl>
        <div v-if="!project.task && docProjects.length && canChange" class="link-task">
          <span>从合规评估项目带入已填的信息：</span>
          <el-select :model-value="''" placeholder="选择合规评估项目" style="width: 360px" :loading="linking" data-testid="admission-link-task" @change="(v: string) => v && linkTask(v)">
            <el-option v-for="d in docProjects" :key="d.id" :label="d.title" :value="d.id" />
          </el-select>
        </div>
        <el-form v-if="questions.length && canChange" label-position="top" class="qa-form" data-testid="admission-questions" @submit.prevent="saveAnswers">
          <el-form-item v-for="q in questions" :key="q.key" :label="q.label || q.question" :title="q.question">
            <el-input v-model="answers[q.key]" type="textarea" :autosize="{ minRows: 1, maxRows: 6 }" maxlength="1000" :placeholder="example(q)" />
          </el-form-item>
          <el-button :type="firstGap ? 'default' : 'primary'" :loading="saving" @click="saveAnswers">保存</el-button>
        </el-form>
      </section>

      <section v-if="project.forms.length" id="sec-forms" class="block" data-testid="admission-forms">
        <h3>2. 官方表格<small>{{ editable && downloadable ? '平台代填官方原表，下载后签字盖章' : '平台代填的官方原表' }}</small></h3>
        <div v-for="f in project.forms" :key="f.code" class="row">
          <span class="main"><span class="name-line"><strong>{{ f.name }}</strong></span><small>{{ f.authority_zh }} · {{ /^\d/.test(f.version) ? f.version + ' 版' : f.version }}<template v-if="f.filled"> · {{ f.missing.length ? `还缺 ${f.missing.length} 项必填` : '必填已填完' }}</template></small><small v-if="f.applies_when" class="applies">适用情形：{{ f.applies_when }}</small><small v-if="f.version_note" class="applies" data-testid="form-version-note">{{ f.version_note }}</small><small v-if="f.carried" class="carried" data-testid="admission-form-carried">{{ f.carried }}</small></span>
          <span class="row-state"><span v-if="!sent" class="mat-state" :class="matState(f.filled, `form:${f.code}`, f.missing.length > 0).cls">{{ matState(f.filled, `form:${f.code}`, f.missing.length > 0).text }}</span></span>
          <span class="row-actions"><el-button v-if="canChange" size="small" :type="f.filled && !f.missing.length ? 'default' : 'primary'" :loading="formBusy === f.code" :data-testid="`admission-form-${f.code}`" @click="fillForm(f)">{{ f.filled ? '继续填写' : '填写' }}</el-button>
            <el-button v-else-if="!editable && f.instance_id" size="small" :data-testid="`admission-form-${f.code}`" @click="fillForm(f)">查看表格</el-button>
            <el-tooltip v-if="f.filled" :disabled="downloadable" content="当前只读账号未开通导出权限" placement="top">
              <span><el-button size="small" :disabled="!downloadable" @click="downloadForm(f)">下载官方原表</el-button></span>
            </el-tooltip></span>
        </div>
      </section>

      <section id="sec-draft" class="block">
        <h3>{{ project.forms.length ? 3 : 2 }}. 申请材料<small>平台生成</small></h3>
        <div class="row">
          <span class="main"><span class="name-line"><strong>{{ project.entry.name_zh }}申请材料</strong></span><small>申请书 + 办理指引</small></span>
          <span class="row-state"><span v-if="!sent" class="mat-state" :class="matState(project.drafts.length > 0, 'draft', draftStale).cls">{{ matState(project.drafts.length > 0, 'draft', draftStale).text }}</span><span v-else class="mat-state ok">递交存档版</span></span>
          <span class="row-actions"><el-button v-if="canChange" :type="!firstGap && (!project.drafts.length || draftStale) ? 'primary' : 'default'" size="small" data-testid="admission-make-draft" @click="makeDraft">{{ project.drafts.length ? '重新生成' : '生成' }}</el-button></span>
        </div>
        <ul v-if="shownDrafts.length" class="draft-list">
          <li v-for="(d, i) in (allVersions ? shownDrafts : shownDrafts.slice(0, 3))" :key="d.id" data-testid="admission-draft-version"><span>{{ localTime(d.created_at) }}<small v-if="sent"> · 递交存档版</small><small v-else-if="i === 0 && !draftStale"> · 最新版</small><small v-if="d.missing.length"> · 生成时缺 {{ d.missing.length }} 项</small></span>
            <span class="cy-links"><el-button link type="primary" @click="showDraft(d.id)">查看</el-button><el-tooltip :disabled="downloadable" content="当前只读账号未开通导出权限" placement="top"><span><el-button link type="primary" :disabled="!downloadable" @click="downloadDraft(d.id, 'docx')">Word</el-button></span></el-tooltip><el-tooltip :disabled="downloadable" content="当前只读账号未开通导出权限" placement="top"><span><el-button link type="primary" :disabled="!downloadable" @click="downloadDraft(d.id, 'pdf')">PDF</el-button></span></el-tooltip>
              <el-button v-if="d.has_foreign && d.id !== activeDraft?.id" link type="primary" @click="showForeignDraft(d.id)">{{ d.foreign_lang_zh || project.foreign_lang_zh || '外文' }}正式稿</el-button>
              <el-tooltip v-for="f in d.forms" :key="f.code" :disabled="downloadable" content="当前只读账号未开通导出权限" placement="top"><span><el-button link type="primary" :disabled="!downloadable" :data-testid="`admission-draft-form-${f.code}`" @click="downloadDraftForm(d.id, f)">{{ d.forms.length > 1 ? f.name : '对应版本官方表格' }}</el-button></span></el-tooltip></span></li>
        </ul>
        <el-button v-if="!sent && project.drafts.length > 3" link type="primary" data-testid="admission-all-versions" @click="allVersions = !allVersions">{{ allVersions ? '只看最近 3 版' : `查看全部 ${project.drafts.length} 版` }}</el-button>
        <div v-if="project.foreign_lang_zh && activeDraft" class="row" data-testid="admission-foreign-row">
          <span class="main"><span class="name-line"><strong>{{ itemShortName }}申请书（{{ project.foreign_lang_zh }}正式稿）</strong></span><small>按{{ sent ? '递交版本' : '最新一版' }}申请书译成{{ project.foreign_lang_zh }}并经核对，打印签章后递交</small></span>
          <span class="row-state"><span class="mat-state" :class="activeDraft.has_foreign ? (draftStale && !sent ? 'fix' : 'ok') : 'none'">{{ activeDraft.has_foreign ? (draftStale && !sent ? '需随新稿更新' : '已生成') : '未生成' }}</span></span>
          <span class="row-actions"><el-button v-if="canChange" size="small" :type="activeDraft.has_foreign ? 'default' : 'primary'" :loading="foreignBusy === activeDraft.id" data-testid="admission-make-foreign" @click="makeForeignDraft(activeDraft.id)">{{ activeDraft.has_foreign ? '重新生成' : `生成${project.foreign_lang_zh}正式稿` }}</el-button></span>
        </div>
        <ul v-if="project.foreign_lang_zh && activeDraft?.has_foreign" class="draft-list">
          <li data-testid="admission-foreign-item"><span>{{ localTime(activeDraft.foreign_generated_at || activeDraft.created_at) }}<small> · {{ activeDraft.foreign_lang_zh || project.foreign_lang_zh }}正式稿</small></span>
            <span class="cy-links"><el-button link type="primary" data-testid="admission-view-foreign" @click="showForeignDraft(activeDraft.id)">查看</el-button><el-tooltip :disabled="downloadable" content="当前只读账号未开通导出权限" placement="top"><span><el-button link type="primary" :disabled="!downloadable" data-testid="admission-dl-foreign-docx" @click="downloadForeignDraft(activeDraft.id, 'docx')">Word</el-button></span></el-tooltip><el-tooltip :disabled="downloadable" content="当前只读账号未开通导出权限" placement="top"><span><el-button link type="primary" :disabled="!downloadable" data-testid="admission-dl-foreign-pdf" @click="downloadForeignDraft(activeDraft.id, 'pdf')">PDF</el-button></span></el-tooltip></span></li>
        </ul>
        <el-drawer :model-value="!!draft" size="min(960px, 92vw)" :title="draft?.name_zh" @close="draft = null">
          <section v-if="draft" class="draft-view" data-testid="admission-draft">
            <el-alert v-if="draft.missing.length" type="warning" :closable="false" show-icon :title="`有 ${draft.missing.length} 项需要补充后才能递交`" />
            <el-alert v-for="(note, ni) in (draft.notes ?? [])" :key="`n-${ni}`" type="info" :closable="false" show-icon :title="note" style="margin-top: 8px" />
            <BlocksView v-if="draft.blocks?.length" :blocks="(draft.blocks as unknown as Block[])" outline hide-title />
          </section>
        </el-drawer>
      </section>

      <section id="sec-bring" class="block" data-testid="admission-bring">
        <h3>{{ project.forms.length ? 4 : 3 }}. 递交清单</h3>
        <table class="d-table checklist">
          <colgroup><col class="c-name" /><col /></colgroup>
          <thead><tr><th>材料名称</th><th>准备方式与要求</th></tr></thead>
          <tbody><tr v-for="r in project.checklist" :key="r[0]"><td>{{ r[0] }}</td><td>{{ r[1] }}</td></tr></tbody>
        </table>
      </section>

      <section id="sec-filing" class="block" data-testid="admission-filing">
        <h3>{{ project.forms.length ? 5 : 4 }}. 申报进度</h3>

        <!-- 补正中：受理机构提的问题，处理好一条勾一条 -->
        <div v-if="authorityIssues.length" class="authority" data-testid="admission-authority">
          <p class="authority-h">受理机构补正要求<small v-if="project.filing.open_questions">待处理 {{ project.filing.open_questions }} 项</small><small v-else>已全部处理</small></p>
          <div v-for="i in authorityIssues" :key="i.id" class="authority-row" :class="{ closed: i.state === 'closed' }">
            <el-checkbox v-if="editable" :model-value="i.state === 'closed'" @change="closeAuthority(i)" />
            <span v-else class="mat-state" :class="i.state === 'closed' ? 'ok' : 'fix'">{{ i.state === 'closed' ? '已处理' : '未处理' }}</span>
            <div class="authority-main"><span>{{ i.question }}</span>
              <el-input v-if="editable && i.state === 'open'" v-model="answerDraft[i.id]" size="small" placeholder="填写补正处理说明（选填）" maxlength="2000" />
              <small v-else-if="i.answer">{{ i.answer }}</small></div>
          </div>
          <div v-if="editable && project.filing.state === 'correcting'" class="authority-add">
            <el-input v-model="newAuthority" size="small" placeholder="新增一条受理机构补正要求" maxlength="4000" @keyup.enter="addAuthority" />
            <el-button size="small" @click="addAuthority">添加</el-button>
          </div>
        </div>

        <p v-if="project.filing.state === 'rejected' && project.filing.reason" class="reject-reason" data-testid="admission-reject-reason">未获批原因：{{ project.filing.reason }}</p>

        <!-- 已取得批文：编号、日期（到期提醒） -->
        <dl v-if="project.approval && !editingApproval" class="approval-show" data-testid="admission-approval-show">
          <dt>批文/证书编号</dt><dd>{{ project.approval.number }}</dd>
          <dt>签发日期</dt><dd>{{ project.approval.issued_on || '—' }}</dd>
          <dt>有效期至</dt><dd>{{ project.approval.long_term ? '长期有效' : (project.approval.expires_on || '—') }}<small class="ap-left">{{ project.approval.long_term ? '' : project.approval.days_left === null ? '' : project.approval.days_left < 0 ? `已过期 ${-project.approval.days_left} 天，需要续期` : `剩 ${project.approval.days_left} 天` }}</small></dd>
          <template v-if="editable"><dt></dt><dd><el-button v-if="project.approval.status && project.approval.status !== '已取得'" type="primary" size="small" :loading="filingBusy" data-testid="admission-renew" @click="renew">准备续期</el-button><el-button size="small" data-testid="admission-approval-edit" @click="editingApproval = true">修改登记</el-button></dd></template>
        </dl>
        <div v-else-if="editingApproval && editable" class="approval-form" data-testid="admission-approval">
          <label class="ap-field"><span>批文/证书编号</span><el-input v-model="approvalForm.number" placeholder="如：GOEIC-2026-0001" style="width: 220px" maxlength="120" /></label>
          <label class="ap-field"><span>签发日期</span><el-date-picker v-model="approvalForm.issued_on" type="date" value-format="YYYY-MM-DD" placeholder="选择日期" style="width: 150px" /></label>
          <label class="ap-field"><span>有效期至</span><el-date-picker v-model="approvalForm.expires_on" type="date" value-format="YYYY-MM-DD" placeholder="选择日期" style="width: 150px" :disabled="approvalForm.long_term" /></label>
          <el-checkbox v-model="approvalForm.long_term" class="ap-check">长期有效</el-checkbox>
          <el-button type="primary" size="small" class="ap-check" :loading="savingApproval" data-testid="admission-approval-save" @click="saveApproval">{{ project.approval ? '保存修改' : '登记批文' }}</el-button>
          <el-button size="small" class="ap-check" @click="editingApproval = false; filingForm.to = ''">取消</el-button>
        </div>

        <!-- 下一步：按当前状态给出能做的几件事 -->
        <div v-if="editable && filingForm.to && filingForm.to !== 'approved'" class="filing-form" data-testid="admission-filing-form">
          <label class="ap-field"><span>{{ filingForm.to === 'submitted' ? '递交日期' : filingForm.to === 'correcting' ? '收到补正通知日期' : '收到结果日期' }}</span>
            <el-date-picker v-model="filingForm.on" type="date" value-format="YYYY-MM-DD" :clearable="false" style="width: 150px" /></label>
          <label v-if="filingForm.to === 'correcting'" class="ap-field wide"><span>受理机构反馈的补正意见（每行一条）</span>
            <el-input v-model="filingForm.questions" type="textarea" :rows="3" maxlength="8000" placeholder="如：请补充当地代表的授权委托书" /></label>
          <label v-if="filingForm.to === 'rejected'" class="ap-field wide"><span>未获批原因</span>
            <el-input v-model="filingForm.reason" maxlength="500" placeholder="请记录受理机构出具的不予批准原因或整改意见" /></label>
          <el-button type="primary" size="small" class="ap-check" :loading="filingBusy" data-testid="admission-filing-save" @click="saveFiling()">确定</el-button>
          <el-button size="small" class="ap-check" @click="filingForm.to = ''">取消</el-button>
        </div>
        <div v-else-if="editable && !editingApproval && project.filing.next.length" class="filing-next" data-testid="admission-filing-next">
          <template v-if="project.filing.state === 'preparing'">
            <el-button :type="project.readiness.ready ? 'primary' : 'default'" size="small" data-testid="admission-filing-submitted" @click="startFiling('submitted')">登记已递交</el-button>
            <el-button size="small" @click="startFiling('approved')">登记获批批文</el-button>
          </template>
          <template v-else-if="project.filing.state === 'submitted'">
            <el-button size="small" data-testid="admission-filing-correcting" @click="startFiling('correcting')">收到补正通知</el-button>
            <el-button type="primary" size="small" data-testid="admission-filing-approved" @click="startFiling('approved')">登记获批批文</el-button>
            <el-button size="small" @click="startFiling('rejected')">未通过</el-button>
            <el-button link size="small" :loading="filingBusy" @click="saveFiling('preparing')">撤回至准备阶段</el-button>
          </template>
          <template v-else-if="project.filing.state === 'correcting'">
            <el-tooltip :disabled="!project.filing.open_questions" :content="`尚有 ${project.filing.open_questions} 项补正要求待处理，勾选完成后再递交`" placement="top">
              <span><el-button type="primary" size="small" :disabled="!!project.filing.open_questions" data-testid="admission-filing-resubmit" @click="startFiling('submitted')">补正后再次递交</el-button></span>
            </el-tooltip>
            <el-button size="small" @click="startFiling('approved')">登记获批批文</el-button>
            <el-button size="small" @click="startFiling('rejected')">未通过</el-button>
          </template>
          <template v-else-if="project.filing.state === 'rejected'">
            <el-button type="primary" size="small" :loading="filingBusy" data-testid="admission-filing-again" @click="saveFiling('preparing')">重新准备</el-button>
          </template>
        </div>
      </section>

      <CopilotDrawer
        ref="copilotRef"
        :storage-key="`admission.${project.id}`"
        :endpoint="`/api/admission/projects/${project.id}/assist`"
        title="项目助手"
        tab-label="项目助手"
        :welcome="admissionCopilotWelcome"
        placeholder="输入申请主体、商业登记号、联系人等信息自动提取，或咨询该事项的材料要求"
        :disabled="!canChange"
        :readonly="!editable"
        :apply-fn="applyAdmissionProposals"
        :undo-fn="undoAdmissionProposals"
        @layout="(o: boolean, w: number) => { copilotOpen = o; copilotWidth = w }"
      />
    </article>

    <template v-else>
      <el-radio-group v-model="tab" class="tabs cy-page-tabs">
        <el-radio-button value="prepare">准备材料</el-radio-button>
        <el-radio-button value="library">准入事项库</el-radio-button>
        <el-radio-button value="forms">官方申报原表<template v-if="formCatalog.length">（{{ formCatalog.length }}）</template></el-radio-button>
      </el-radio-group>

      <section v-if="tab === 'prepare'" v-loading="planBusy || projectsLoading" class="prepare-section">
        <el-alert v-if="expiring.length" type="warning" :closable="false" show-icon class="expiring" data-testid="admission-expiring" :title="expiringText" />
        <template v-if="projects.length">
          <h2 class="sub-h first">在办准入项目<small>{{ scopeName }} · {{ shownProjects.length }} 个</small></h2>
          <div v-for="p in visibleShownProjects" :key="p.id" class="item-row">
            <button type="button" class="main link" data-testid="admission-project-row" @click="openProject(p.id)"><span class="name-line"><strong>{{ p.entry_name }}</strong></span>
              <small>{{ COUNTRIES[p.country_code] }} · {{ p.function_zh || '通用业务场景' }}<template v-if="p.approval?.number"> · 批文编号 {{ p.approval.number }}</template><template v-if="p.source"> · 关联项目：{{ p.source.title }}</template> · {{ localTime(p.created_at, 'date') }} 创建</small><small v-if="stateLine(p).text !== shortState(p)" class="state-detail">{{ missingWording(stateLine(p).text).replace(/还没有/g, '尚未') }}</small></button>
            <span class="state-col"><span class="state-tag" :class="`tone-${toneOf({ text: shortState(p), ok: stateLine(p).ok })}`" :title="missingWording(stateLine(p).text).replace(/还没有/g, '尚未')" data-testid="admission-project-state">{{ shortState(p) }}</span></span>
            <span class="row-actions"><el-button data-testid="admission-project-continue" @click="openProject(p.id)">{{ editable ? '继续办理' : '查看' }}</el-button>
            <el-button v-if="editable" link class="cy-danger-quiet" @click="removeProject(p)">删除</el-button></span>
          </div>
          <button
            v-if="shownProjects.length > ADM_FOLD_LIMIT"
            type="button"
            class="cy-fold-toggle"
            data-testid="admission-projects-fold-toggle"
            @click="admProjectsExpanded = !admProjectsExpanded"
          >
            <span>{{ admProjectsExpanded ? '收起' : `展开其余 ${shownProjects.length - ADM_FOLD_LIMIT} 个项目` }}</span>
          </button>
          <p v-if="!shownProjects.length" class="muted">{{ scopeName }}暂无在办准入项目。</p>
        </template>
        <p v-if="!projectsLoading && !projects.length && !editable" class="cy-empty" data-testid="admission-empty">暂无在办准入项目</p>
        <template v-if="editable">
        <h2 v-if="projects.length" class="sub-h">查询并新建准入事项</h2>
        <div class="pick-bar" data-testid="admission-pick">
          <el-select v-model="planCountry" placeholder="选择目标国家" style="width: 160px" data-testid="admission-country" @change="changeRoute('country')">
            <el-option v-for="c in countryList" :key="c.code" :label="c.name" :value="c.code" />
          </el-select>
          <el-select v-model="planFunction" placeholder="选择业务场景" clearable style="width: 200px" data-testid="admission-function" @change="changeRoute('function')">
            <el-option v-for="f in FUNCTIONS" :key="f.key" :label="f.label" :value="f.key" />
          </el-select>
          <el-select v-model="planProduct" placeholder="全部产品类别" clearable style="width: 160px" data-testid="admission-product" @change="loadPlan">
            <el-option v-for="(label, key) in PRODUCTS" :key="key" :label="label" :value="key" />
          </el-select>
          <span v-if="plan?.task" class="muted">关联评估项目：{{ plan.task.title }}</span>
        </div>
        <template v-if="plan">
          <h2 class="sub-h">{{ plan.country_zh }}<template v-if="plan.function_zh"> · {{ plan.function_zh }}</template><template v-if="planProduct"> · {{ PRODUCTS[planProduct] }}</template>：应办事项 {{ plan.needed.length }} 项</h2>
          <p v-if="!plan.needed.length" class="cy-empty cy-empty--inline">该国家暂无已收录的对应准入事项</p>
          <template v-for="[cat, items] in neededGrouped" :key="cat">
          <h3 class="cat-h" data-testid="admission-category">{{ CATEGORY[cat] ?? cat }}<small>{{ items.length }} 项</small></h3>
          <div v-for="e in items" :key="e.id" class="item-row" data-testid="admission-item">
            <span class="main"><span class="name-line"><strong>{{ e.name_zh }}</strong></span>
              <small>适用：{{ e.products_zh }} · 受理：{{ !isAuthorityUnnamed(e.authority_zh) ? e.authority_zh : UNNAMED_CLAUSE }}<template v-if="e.forms.length"> · 官方表格：{{ e.forms.join('、') }}</template></small>
              <small data-testid="admission-item-basis">依据法规：<template v-if="e.laws?.length"><template v-for="(l, i) in e.laws" :key="i"><template v-if="i">、</template><router-link v-if="l.link" :to="lawLink(l.link)">{{ l.source }}</router-link><span v-else>{{ l.source }}</span></template></template><span v-else class="muted">{{ UNNAMED_CLAUSE }}</span></small>
              <small class="facts"><span v-for="[k, label] in FACTS.filter(([k]) => shownFact(e.profile?.[k]))" :key="k">{{ label }}：{{ e.profile?.[k] }}</span></small></span>
            <span class="state-col"><span class="state-tag" :class="e.project ? 'tone-active' : 'tone-muted'">{{ e.project ? '已在准备' : '未开始' }}</span></span>
              <span class="row-actions"><el-button v-if="e.project || editable" :loading="opening === e.id" data-testid="admission-prepare" @click="prepare(e)">{{ e.project ? (editable ? '继续办理' : '查看') : '准备材料' }}</el-button></span>
          </div>
          </template>
          <details v-if="plan.maybe.length" class="maybe">
            <summary>其他可能涉及事项（{{ plan.maybe.length }} 项，视具体业务而定）</summary>
            <div v-for="e in plan.maybe" :key="e.id" class="item-row">
              <span class="main"><span class="name-line"><strong>{{ e.name_zh }}</strong></span><small v-if="e.gate" class="applies" data-testid="admission-gate">{{ e.gate }}</small><small>{{ e.summary_zh }}</small><small>受理：{{ !isAuthorityUnnamed(e.authority_zh) ? e.authority_zh : UNNAMED_CLAUSE }}</small></span>
              <span class="state-col"><span v-if="e.project" class="state-tag tone-active">已在准备</span></span>
              <span class="row-actions"><el-button v-if="e.project || editable" :loading="opening === e.id" @click="prepare(e)">{{ e.project ? (editable ? '继续办理' : '查看') : '准备材料' }}</el-button></span>
            </div>
          </details>
          <details v-if="plan.other?.length" class="maybe" data-testid="admission-other">
            <summary>当前所选业务暂不涉及事项（{{ plan.other.length }} 项）</summary>
            <div v-for="e in plan.other" :key="e.id" class="item-row">
              <span class="main"><span class="name-line"><strong>{{ e.name_zh }}</strong></span><small>{{ e.summary_zh }}</small></span>
              <span class="state-col" /><span class="row-actions" />
            </div>
          </details>
        </template>
        <p v-else class="cy-empty cy-empty--inline">请先选择目标国家与业务场景</p>
        </template>

      </section>

      <section v-else-if="tab === 'library'" v-loading="libBusy">
        <div class="country-bar" role="group" aria-label="按国家筛选">
          <el-check-tag :checked="!libCountry" @change="libCountry = ''; loadLibrary()">全部国家</el-check-tag>
          <el-check-tag v-for="c in countryList" :key="c.code" :checked="libCountry === c.code" @change="libCountry = c.code; loadLibrary()">{{ c.name }}</el-check-tag>
        </div>
        <div class="country-bar" role="group" aria-label="按产品类别筛选" data-testid="admission-lib-product">
          <el-check-tag :checked="!libProduct" @change="libProduct = ''; loadLibrary()">全部产品类别</el-check-tag>
          <el-check-tag v-for="(label, key) in PRODUCTS" :key="key" :checked="libProduct === key" @change="libProduct = key; loadLibrary()">{{ label }}</el-check-tag>
        </div>
        <div v-for="[cat, items] in grouped" :key="cat" class="category">
          <h2>{{ CATEGORY[cat] ?? cat }}<small>{{ items.length }} 项</small></h2>
          <button v-for="e in items" :key="e.id" class="entry-row" data-testid="admission-entry" @click="openEntry(e.id)">
            <span v-if="!libCountry" class="entry-country">{{ COUNTRIES[e.country_code] ?? e.country_code }}</span>
            <span class="entry-main"><strong>{{ e.name_zh }}</strong><span>{{ e.summary_zh }}</span><span class="muted">适用：{{ e.products_zh }} · 依据法规：{{ e.laws?.length ? e.laws.map((l) => l.source).join('、') : UNNAMED_CLAUSE }}</span></span>
            <span class="entry-meta">{{ e.step_count ? `${e.step_count} 个办理环节` : '暂无办理步骤资料' }}</span>
            <span class="entry-view">查看 →</span>
          </button>
        </div>
      </section>

      <section v-else v-loading="formsBusy" data-testid="admission-forms-catalog">
        <div class="country-bar" role="group" aria-label="按国家筛选官方原表">
          <el-check-tag :checked="!formCountry" @change="formCountry = ''">全部国家（{{ formCatalog.length }}）</el-check-tag>
          <el-check-tag
            v-for="c in countryList.filter((x) => formCatalog.some((f) => f.country === x.code))"
            :key="c.code"
            :checked="formCountry === c.code"
            @change="formCountry = c.code"
          >{{ c.name }}</el-check-tag>
        </div>
        <div v-for="[cc, fList] in groupedCatalogForms" :key="cc" class="category">
          <h2>{{ COUNTRIES[cc] ?? cc }}官方申报原表<small>{{ fList.length }} 套</small></h2>
          <button
            v-for="f in fList"
            :key="f.code"
            type="button"
            class="entry-row"
            :data-testid="`official-form-${f.code}`"
            @click="router.push({ path: `/forms/preview:${f.code}`, query: { back: '/admission?tab=forms' } })"
          >
            <span class="entry-country">{{ COUNTRIES[f.country] ?? f.country }}</span>
            <span class="entry-main">
              <strong>{{ f.name_zh }}</strong>
              <span class="muted">主管机关：{{ f.authority_zh }} · 版本：{{ f.version }}<template v-if="f.applies_when"> · 适用情形：{{ f.applies_when }}</template></span>
            </span>
            <span class="entry-meta">{{ f.kind === 'docx' ? 'Word 原表' : 'PDF 原表' }}</span>
            <span class="entry-view">{{ editable ? '预览 / 填报 →' : '查看原表 →' }}</span>
          </button>
        </div>
      </section>
    </template>

    <el-drawer v-model="entryOpen" size="min(560px, 94vw)" :with-header="false" @closed="opened = null">
      <div v-if="entryLoading" v-loading="true" class="entry-loading" />
      <article v-if="opened" class="entry-detail" data-testid="admission-entry-detail">
        <p class="crumb">{{ COUNTRIES[opened.country_code] }} · {{ CATEGORY[opened.category] }}</p>
        <div class="entry-head"><h2>{{ opened.name_zh }}</h2><el-button v-if="projectOfEntry(opened.id)" type="primary" data-testid="admission-open-existing" @click="entryOpen = false; openProject(projectOfEntry(opened.id)!.id)">进入在办项目</el-button><el-button v-else-if="editable" type="primary" data-testid="admission-start" @click="prepareFromLibrary">准备材料</el-button></div>
        <p>{{ opened.summary_zh }}</p>
        <dl><dt>受理机构</dt><dd>{{ authorityText(opened) }}<small v-if="authorityWhy(opened)" class="auth-note">{{ authorityWhy(opened) }}</small>
            <small v-if="opened.authority_roles" class="auth-note">职责分工：{{ opened.authority_roles }}</small>
            <small v-if="opened.authority_original" class="auth-note">条文原文写作：<bdi dir="rtl" class="rtl">{{ opened.authority_original }}</bdi></small></dd>
          <template v-for="[k, label] in FACTS.filter(([k]) => shownFact(opened!.profile?.[k]))" :key="k"><dt>{{ label }}</dt><dd>{{ opened.profile?.[k] }}</dd></template>
          <template v-if="noRule(opened.profile).length"><dt>条文未明确</dt><dd class="muted">{{ askLine(opened.profile) }}</dd></template>
          <dt>依据法规</dt><dd><template v-if="opened.laws?.length"><template v-for="(l, i) in opened.laws" :key="i"><template v-if="i">、</template><router-link v-if="l.link" :to="lawLink(l.link)">{{ l.source }}</router-link><span v-else>{{ l.source }}</span></template></template><span v-else class="muted">{{ UNNAMED_CLAUSE }}</span>
</dd></dl>
        <template v-if="opened.evidence_quote"><h3>参考条文</h3><p v-if="opened.evidence_zh" class="hint quote-zh">{{ opened.evidence_zh }}</p>
          <details class="orig"><summary>条文原文</summary><blockquote dir="auto">{{ opened.evidence_quote }}</blockquote></details></template>
        <template v-if="opened.portal">
          <h3>递交渠道</h3>
          <ul class="form-list portal-list"><li><a :href="opened.portal.site" target="_blank" rel="noopener noreferrer">{{ opened.portal.authority_zh }} 官网</a></li>
            <li v-for="l in opened.portal.links" :key="l.url"><a :href="l.url" target="_blank" rel="noopener noreferrer">{{ l.label }}</a></li>
            <li v-if="opened.guide?.link"><router-link :to="lawLink(opened.guide.link)">官方办事说明</router-link></li></ul>
        </template>
        <template v-if="opened.forms?.length">
          <h3>官方表格</h3>
          <ul class="form-list"><li v-for="f in opened.forms" :key="f.code"><strong>{{ f.name_zh.split('：').pop() }}</strong><span>{{ f.authority_zh }} · {{ /^\d/.test(f.version) ? f.version + ' 版' : f.version }}</span><small v-if="f.version_note" class="applies">{{ f.version_note }}</small></li></ul>
        </template>
        <h3>办理环节</h3>
        <p v-if="opened.steps_generic" class="hint">法规明确须履行本项准入义务但未列明具体流程，以下为通用办理环节。</p>
        <ol class="step-list"><li v-for="s in opened.steps" :key="s.id">
          <strong>{{ s.title_zh }}</strong><span>{{ s.detail_zh }}</span>
          <small v-if="s.materials.length">材料：{{ s.materials.join('；') }}</small>
          <details v-if="s.evidence_quote" class="orig"><summary>条文原文</summary><blockquote dir="auto">{{ s.evidence_quote }}</blockquote></details></li></ol>
      </article>
    </el-drawer>
  </div>
</template>

<style scoped>
.admission-page { max-width: 1200px; }
.admission-page small, .adm-page small, .entry-detail small, .draft-view small { font-size: 13px; }
.tabs { margin: 4px 0 20px; }
.pick-bar { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; margin-bottom: 18px; }
.sub-h { font-size: 18px; margin: 28px 0 12px; }
/* 第 7.5 项 规则 12：带状态的列表统一行——名称说明 | 状态（固定宽、左对齐）| 操作（固定宽、右对齐） */
.item-row { display: grid; grid-template-columns: minmax(0, 1fr) 112px 156px; column-gap: 20px; align-items: center; padding: 16px 20px; margin-bottom: 8px; background: #fff; border: 1px solid var(--cy-line); border-radius: 10px; }
.item-row .main { min-width: 0; display: flex; flex-direction: column; gap: 4px; text-align: left; }
.item-row .main strong { font-size: 15px; }
.item-row .main.link { background: none; border: 0; padding: 0; cursor: pointer; font: inherit; }
.item-row .main.link:hover strong { color: var(--el-color-primary); }
.item-row small, .row small { color: var(--cy-text-2); font-size: 13px; line-height: 1.6; }
.row-actions { display: flex; align-items: center; justify-content: flex-end; gap: 8px; min-width: 96px; align-self: center; }
/* 二轮 规则 12：第一行名称紧跟状态标签，第二行起说明，操作固定在右侧、与第一行顶部对齐 */
.name-line { display: flex; flex-wrap: wrap; align-items: center; gap: 6px 10px; }
.state-tag { display: inline-block; white-space: nowrap; max-width: 100%; overflow: hidden; text-overflow: ellipsis; font-size: 13px; line-height: 20px; padding: 2px 12px; border-radius: 999px; color: var(--cy-tone-muted-fg); background: var(--cy-tone-muted-bg); font-weight: 400; }
.state-tag.tone-active { color: var(--cy-tone-active-fg); background: var(--cy-tone-active-bg); }
.state-tag.tone-warn { color: var(--cy-tone-warn-fg); background: var(--cy-tone-warn-bg); }
.state-tag.tone-danger { color: var(--cy-tone-danger-fg); background: var(--cy-tone-danger-bg); }
.state-tag.tone-done { color: var(--cy-tone-done-fg); background: #edf7f4; }
.row-actions .el-button + .el-button { margin-left: 0; }
/* 规则 15：状态色全站一套 */
.state { justify-self: start; font-size: 14px; line-height: 1.6; color: var(--cy-tone-muted-fg); }
.state.tone-active { color: var(--cy-tone-active-fg); }
.state.tone-warn { color: var(--cy-tone-warn-fg); }
.state.tone-danger { color: var(--cy-tone-danger-fg); }
.state.tone-done { color: var(--cy-tone-done-fg); font-weight: 600; }
.maybe { margin: 14px 0; } .maybe summary { cursor: pointer; color: var(--el-color-primary); margin-bottom: 10px; }
.muted { color: var(--cy-text-2); font-size: 13px; }
.country-bar { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 18px; }
.category { margin-bottom: 26px; }
.cat-h { font-size: 16px; margin: 24px 0 8px; }
.cat-h small { margin-left: 8px; font-weight: 400; font-size: 13px; color: var(--cy-text-3); }
.category h2 { font-size: 16px; margin: 0 0 10px; }
.category h2 small { margin-left: 10px; font-weight: 400; color: var(--el-text-color-secondary); }
.entry-row { display: flex; align-items: center; gap: 20px; width: 100%; min-height: 96px; text-align: left; padding: 16px 20px; margin-bottom: 8px; background: white; border: 1px solid var(--cy-line); border-radius: 10px; cursor: pointer; font: inherit; }
.entry-row:hover { border-color: var(--el-color-primary-light-5); }
.entry-row .entry-main > strong { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.entry-row .entry-main > span { display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
.entry-country { flex: none; width: 56px; font-size: 14px; font-weight: 600; color: var(--cy-text-2); }
.entry-main > strong { font-size: 15px; }
.entry-main { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 4px; }
.entry-main span { color: var(--cy-text-2); font-size: 13px; line-height: 1.6; }
.entry-meta { flex: none; width: 128px; font-size: 13px; color: var(--cy-text-2); }
.entry-view { flex: none; font-size: 14px; color: var(--el-color-primary); }
/* 规则 26、35：只一层卡片；返回单独一行；删除收成灰字 */
.project-detail { background: white; border: 1px solid var(--cy-line); border-radius: 12px; padding: 24px 28px 28px; }
.back-row { display: flex; flex-wrap: wrap; gap: 4px 16px; align-items: center; margin: -6px 0 8px; }
.back-row .el-button + .el-button { margin-left: 0; }
.project-head { display: flex; justify-content: space-between; gap: 20px; align-items: flex-start; }
.project-head h2 { margin: 0 0 6px; font-size: 18px; } .meta { margin: 0; color: var(--cy-text-2); }
.where-box { display: grid; grid-template-columns: 80px 1fr; gap: 6px 12px; margin: 14px 0; font-size: 14px; }
.where-box dt { color: var(--el-text-color-secondary); } .where-box dd { margin: 0; line-height: 1.7; }
.links a { margin-right: 14px; color: #1d4ed8; }
.ready-bar { display: flex; align-items: center; gap: 14px; margin-top: 16px; padding: 14px 18px; border-radius: 10px; background: #fff7ed; border: 1px solid #fed7aa; }
.ready-bar.ok { background: #f0fdf4; border-color: #bbf7d0; }
.ready-bar .ready-main { flex: 1; display: flex; flex-direction: column; gap: 4px; }
.ready-items { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 4px; }
.ready-tag { border: 1px solid #fed7aa; background: #fff; color: var(--cy-text-1); border-radius: 4px; padding: 1px 8px; font: inherit; font-size: 13px; line-height: 22px; cursor: pointer; }
.ready-tag:hover { border-color: var(--el-color-primary-light-5); color: var(--el-color-primary); }
.ready-more { border: 0; background: none; color: var(--el-color-primary); font: inherit; font-size: 13px; cursor: pointer; }
.project-title { margin: 0 0 6px; font-size: 24px; line-height: 1.4; }
.filing-steps.top { margin: 16px 0 4px; max-width: 720px; }
.ready-item { border: 0; background: none; padding: 0; color: var(--el-color-primary); cursor: pointer; font: inherit; font-size: 13px; text-align: left; }
.issues { margin: 12px 0 4px; } .issues h3 { font-size: 14px; margin: 0 0 6px; } .issues h3 small { margin-left: 10px; font-weight: 400; color: var(--el-text-color-secondary); }
.issue-row { display: flex; justify-content: space-between; width: 100%; text-align: left; padding: 8px 12px; margin-bottom: 6px; background: #fffbeb; border: 1px solid #fde68a; border-radius: 8px; cursor: pointer; font: inherit; font-size: 13px; }
.issue-row small { color: var(--el-color-primary); }
.fixed { font-size: 13px; color: #15803d; margin: 4px 0; } .fixed small { margin-left: 10px; color: var(--el-text-color-secondary); }
.block { border-top: 1px solid var(--cy-line); padding: 20px 0 8px; margin-top: 16px; }
.block.flash { outline: 2px solid var(--el-color-primary); outline-offset: 6px; border-radius: 8px; background: #f8fbff; transition: outline-color .4s, background .4s; }
.block h3 { font-size: 16px; margin: 0 0 12px; } .block h3 small { margin-left: 10px; font-weight: 400; font-size: 13px; color: var(--cy-text-2); }
.block h3.sec-h { display: flex; justify-content: space-between; align-items: center; gap: 12px; flex-wrap: wrap; }
/* 材料行：名称 | 状态 | 操作，同一区块上下对齐 */
.row { display: grid; grid-template-columns: minmax(0, 1fr) 112px 156px; column-gap: 20px; align-items: center; padding: 12px 0; border-bottom: 1px solid var(--cy-line); }
.row .main { display: flex; flex-direction: column; gap: 2px; min-width: 0; }
.row .main strong { font-size: 15px; }
.row-state, .state-col { justify-self: start; }
.state-detail { display: block; color: var(--cy-tone-warn-fg) !important; }
.row a { color: #1d4ed8; } .row a.danger { color: var(--el-color-danger); margin-left: 6px; }
.todo-text { color: #b45309 !important; }
.law { margin-top: 12px; } .law summary { cursor: pointer; color: var(--el-color-primary); font-size: 14px; }
.draft-list { list-style: none; padding: 0; margin: 8px 0; } .draft-list li { display: flex; justify-content: space-between; align-items: center; gap: 12px; padding: 6px 0; font-size: 14px; color: var(--cy-text-2); }
/* 第 7.5 项二轮：申请材料预览不再是抽屉里再套一张卡片，各节之间用分隔线 */
.draft-view { margin-top: 4px; }
.draft-view h5 { padding-top: 16px; border-top: 1px solid var(--cy-line); }
.draft-view .d-title { margin-top: 0; }
.d-title { font-size: 18px; margin: 16px 0 8px; } .draft-view h5 { font-size: 15px; margin: 18px 0 8px; } .d-p { white-space: pre-line; line-height: 1.8; margin: 6px 0; }
.draft-view ul { line-height: 1.8; padding-left: 20px; }
/* 规则 27、33：短列表头不竖排，首列不被挤窄；阿拉伯文单元格按自身方向排 */
.d-table { width: 100%; border-collapse: collapse; font-size: 14px; line-height: 1.6; } .d-table th, .d-table td { border: 1px solid var(--cy-line); padding: 8px 12px; text-align: left; vertical-align: top; }
.d-table th { white-space: nowrap; background: var(--cy-surface-sunken); font-weight: 600; }
.d-table td:first-child { min-width: 7em; }
.d-table td[dir='auto'], .d-table th[dir='auto'] { unicode-bidi: plaintext; }
.d-table td:dir(rtl), .d-table th:dir(rtl), .draft-view :is(h4, h5, p, li):dir(rtl) { font-family: 'Noto Naskh Arabic', var(--cy-font); text-align: right; }
.d-table td:dir(rtl) { font-size: 13px; color: var(--cy-text-2); }
.d-table td.todo { background: #fff6e5; color: #9a5b00; }
.entry-loading { height: 240px; }
.entry-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; }
.entry-head h2 { margin: 0 0 8px; }
.entry-detail { padding: 8px 6px; } .entry-detail h2 { font-size: 18px; margin: 6px 0 10px; line-height: 1.5; }
.crumb { color: var(--el-color-primary); font-size: 13px; margin: 0; }
.entry-detail dl { display: grid; grid-template-columns: 80px 1fr; gap: 8px 12px; margin: 16px 0; } .entry-detail dt { color: var(--el-text-color-secondary); } .entry-detail dd { margin: 0; line-height: 1.7; }
.form-list { list-style: none; padding: 0; margin: 0 0 12px; }
.form-list li { display: flex; flex-direction: column; padding: 6px 0; border-bottom: 1px solid #f1f5f9; }
.form-list span { color: var(--cy-text-2); font-size: 13px; }
.portal-list a { color: #1d4ed8; font-size: 13px; }
.hint { color: var(--el-text-color-secondary); font-size: 13px; line-height: 1.7; }
blockquote { margin: 8px 0; padding: 8px 12px; border-left: 3px solid var(--el-border-color); color: var(--el-text-color-secondary); font-size: 13px; line-height: 1.7; white-space: pre-line; background: #f7f9fb; }
.step-list { padding-left: 20px; } .step-list li { margin-bottom: 14px; } .step-list li > * { display: block; } .step-list li span { color: var(--el-text-color-regular); line-height: 1.7; margin-top: 2px; } .step-list small { color: var(--el-text-color-secondary); margin-top: 4px; }
.orig summary { cursor: pointer; color: var(--cy-text-2); font-size: 13px; margin-top: 4px; }
@media (max-width: 800px) {
  .item-row, .row { grid-template-columns: minmax(0, 1fr) auto; }
  .item-row .row-actions, .row .row-actions { grid-column: 1 / -1; justify-content: flex-start; margin-top: 8px; }
  .entry-row { flex-wrap: wrap; align-items: flex-start; gap: 8px 12px; min-height: 0; padding: 14px 16px; }
  .entry-country { width: auto; }
  .entry-meta { width: auto; font-size: 13px; }
  .entry-view { margin-left: auto; font-size: 13px; white-space: nowrap; }
  .entry-main { flex: 1 1 100%; order: 2; }
  .entry-row .entry-main > strong { white-space: normal; overflow: visible; text-overflow: clip; line-height: 1.45; }
  .project-detail { padding: 18px 16px 24px !important; }
  .where-box, .filled, .approval-show { grid-template-columns: 1fr; gap: 2px 0; }
  .where-box dd, .filled dd, .approval-show dd { margin-bottom: 8px; }
  .ready-bar { flex-wrap: wrap; }
}
.facts { display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
.facts span + span::before { content: ' · '; }
.facts .muted, dd.muted { color: var(--cy-text-2); }
.applies { display: block; color: var(--el-text-color-secondary); }
.carried { display: block; color: #047857; }
.mat-state { display: inline-block; font-size: 13px; line-height: 20px; padding: 2px 12px; border-radius: 999px; white-space: nowrap; align-self: center; color: var(--cy-tone-muted-fg); background: var(--cy-tone-muted-bg); }
.mat-state.miss { color: var(--cy-tone-danger-fg); background: var(--cy-tone-danger-bg); }
.mat-state.doing { color: var(--cy-tone-active-fg); background: var(--cy-tone-active-bg); }
.mat-state.fix { color: var(--el-color-warning-dark-2); background: var(--el-color-warning-light-9); }
.mat-state.ok { color: var(--cy-tone-done-fg); background: #edf7f4; }
.approval-form { display: flex; flex-wrap: wrap; gap: 8px 12px; align-items: flex-end; }
.ap-field { display: flex; flex-direction: column; gap: 4px; font-size: 14px; color: var(--cy-text-1); }
.ap-check { margin-bottom: 4px; }
.approval-show { display: grid; grid-template-columns: 110px 1fr; gap: 6px 12px; font-size: 14px; margin: 6px 0 0; } .approval-show dt { color: var(--el-text-color-secondary); } .approval-show dd { margin: 0; }
.link-task { display: flex; align-items: center; gap: 8px; margin: 6px 0 10px; font-size: 13px; color: var(--el-text-color-secondary); flex-wrap: wrap; }
.stale { color: var(--el-color-warning-dark-2); }
.auth-note { display: block; color: var(--cy-text-2); font-size: 13px; line-height: 1.6; }
.checklist { margin-top: 6px; }
.checklist col.c-name { width: 34%; }
/* 规则 22：信息项多为短字段，宽屏两列排，输入框随内容长高 */
.qa-form { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); column-gap: 24px; align-items: start; }
.qa-form > .el-button { grid-column: 1 / -1; justify-self: start; }
.qa-form :deep(.el-textarea__inner) { resize: none; }
.qa-form :deep(.el-form-item) { display: flex; flex-direction: column; justify-content: flex-start; }
.qa-form :deep(.el-form-item__label) { font-weight: 600; }
@media (max-width: 1100px) { .qa-form { grid-template-columns: minmax(0, 1fr); } }
.d-orig { margin: 0 0 10px; font-size: 13px; color: var(--el-text-color-secondary); } .d-orig summary { cursor: pointer; }
.bring-list { margin: 4px 0 0; padding-left: 20px; line-height: 1.8; font-size: 14px; }
.bring-list li span { display: block; color: var(--el-text-color-secondary); font-size: 13px; }
.state.warn { color: var(--el-color-warning-dark-2); }
.expiring { margin: 14px 0 4px; }
.sub-h.first { margin-top: 4px; } .sub-h small { margin-left: 10px; font-weight: 400; font-size: 13px; color: var(--el-text-color-secondary); }
.rtl { unicode-bidi: isolate; direction: rtl; }
.quote-zh { white-space: pre-line; }
.where-box { grid-template-columns: 96px 1fr; }
.filled { display: grid; grid-template-columns: 160px 1fr; gap: 6px 12px; margin: 4px 0 12px; font-size: 14px; }
.filled dt { color: var(--el-text-color-secondary); } .filled dd { margin: 0; display: flex; gap: 8px; align-items: baseline; flex-wrap: wrap; line-height: 1.6; }
.fact-v { white-space: pre-line; } .fact-edit { flex-direction: column; align-items: stretch !important; }
.filing-chip { margin-left: 10px; font-size: 13px; font-weight: 400; padding: 2px 12px; border-radius: 999px; vertical-align: middle; color: var(--el-text-color-secondary); background: #f1f5f9; }
.filing-chip.preparing, .filing-chip.submitted { color: var(--cy-tone-active-fg); background: var(--cy-tone-active-bg); }
.filing-chip.correcting { color: var(--cy-tone-warn-fg); background: var(--cy-tone-warn-bg); }
.filing-chip.approved { color: var(--el-color-success-dark-2); background: var(--el-color-success-light-9); }
.filing-chip.rejected { color: var(--el-color-danger); background: var(--el-color-danger-light-9); }
.filing-steps { display: flex; list-style: none; padding: 0; margin: 4px 0 14px; gap: 0; }
.filing-steps li { flex: 1; position: relative; padding: 8px 0 0; border-top: 3px solid #e2e8f0; display: flex; flex-direction: column; gap: 2px; font-size: 13px; color: var(--el-text-color-secondary); }
.filing-steps li + li { margin-left: 6px; }
.filing-steps li.done { border-top-color: #93c5fd; color: var(--el-text-color-regular); }
.filing-steps li.now { border-top-color: var(--el-color-primary); color: var(--el-text-color-primary); }
.filing-steps li.now strong { color: var(--el-color-primary); }
.filing-steps li.bad { border-top-color: var(--el-color-danger); } .filing-steps li.bad strong { color: var(--el-color-danger); }
.filing-steps small { font-size: 13px; color: var(--el-text-color-secondary); }
.filing-next, .filing-form { display: flex; flex-wrap: wrap; gap: 8px 10px; align-items: flex-end; margin-top: 10px; }
.ap-field.wide { flex: 1 1 360px; }
.authority { margin: 4px 0 10px; padding: 10px 14px; background: #fffbeb; border: 1px solid #fde68a; border-radius: 10px; }
.authority-h { margin: 0 0 6px; font-size: 14px; font-weight: 600; } .authority-h small { margin-left: 10px; font-weight: 400; color: var(--el-text-color-secondary); }
.authority-row { display: flex; gap: 10px; align-items: flex-start; padding: 6px 0; font-size: 14px; }
.authority-row.closed .authority-main > span { color: var(--el-text-color-secondary); text-decoration: line-through; }
.authority-main { flex: 1; display: flex; flex-direction: column; gap: 4px; line-height: 1.6; } .authority-main small { color: #15803d; font-size: 13px; }
.authority-add { display: flex; gap: 8px; margin-top: 6px; }
.reject-reason { margin: 4px 0 8px; padding: 8px 12px; border-radius: 8px; font-size: 14px; color: var(--el-color-danger); background: var(--el-color-danger-light-9); }
.ap-left { margin-left: 10px; color: var(--cy-text-2); font-size: 13px; }
.project-detail { padding-right: var(--copilot-pad, 24px); transition: padding-right 0.18s ease; }
@media (max-width: 1100px) { .project-detail { padding-right: 24px; } }
.prepare-section { min-height: 160px; }
</style>
