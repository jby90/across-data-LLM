<script setup lang="ts">
import {
  HOST_REPORT_LANGUAGES,
  canEdit,
  canExport,
  inferHostLanguage,
  localTime,
  type HostReportLang,
  type ReportOutputLang,
} from '@cyshuhe/shared'
import { useAuth } from '../auth'
import { ElMessage, ElMessageBox } from 'element-plus'
import { computed, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '../api'
import AssessmentSwitch from '../components/AssessmentSwitch.vue'
import AdmissionLaunch from '../components/AdmissionLaunch.vue'
import QuestionAssistant from '../components/QuestionAssistant.vue'
import QuestionBasis, { type BasisCard } from '../components/QuestionBasis.vue'
import RiskTable from '../components/RiskTable.vue'
import ProjectChat from '../components/ProjectChat.vue'
import BlocksView, { type Block as PreviewBlock } from '../components/BlocksView.vue'
import type { CopilotProposal } from '../components/CopilotFillCard.vue'

interface DocVerdict { tier: string; label: string; tone: string; text: string }
interface ProjectVerdict { label: string; tone: string; tier: string; text: string; route_text: string }
interface DocView {
  code: string; name: string; basis_kind: 'official' | 'eu' | 'law'; basis_label: string; basis: string[]
  // 四态：未开始 / 填写中 / 待生成论述 / 已就绪（没有论述环节的文件只有三态）
  state: 'not_started' | 'filling' | 'needs_paper' | 'ready'; missing: { key: string; label: string }[]; optional: { key: string; label: string }[]
  official_only: boolean; official_missing: number; has_edpb_en?: boolean
  narrative: boolean; paper: { state: string; seconds?: number; error?: string } | null; basis_updated?: boolean
  official_forms: { code: string; kind?: string; name_zh: string; authority_zh: string; side_label: string; short: string; title: string; note: string; missing: number; instance_id: string | null }[]; edited: boolean
  verdict?: DocVerdict | null
}
// 能不能下载成稿（后端算，打包接口按同一结果放行或拒绝）：要的文件都已就绪才行；按目前情况不需要、没就绪的不放进成稿包
interface FinalStatus {
  ok: boolean; missing: { key: string; label: string; tier: 'business' | 'company' | 'system' | 'project'; docs: string[] }[]
  papers: { code: string; name: string }[]; forms: { code: string; title: string; short: string; missing: number }[]; skipped: { code: string; name: string }[]
  issues: Issue[]  // 平台检查出的前后不一致（一对题算一处）：只提示，不挡下载
}
// 平台检查结果：挂在题目旁；must 必须 / advise 建议（与准入材料检查同一套分档）；点了跳到对不上的另一处
type IssueTo = { kind: 'question'; key: string } | { kind: 'route' } | { kind: 'form'; instance_id: string; item: string; name: string }
interface Issue { id?: string; key: string; text: string; level: 'must' | 'advise'; to: IssueTo }
interface Suggestion { text: string; source: 'text' | 'general'; quote: string; keys?: string[] }
interface DensityHint { missing_facets: string[]; message: string; draft_template: string }
interface ThirdCountryChecklist { country: string; country_code: string; role: string; in_eu_eea: boolean; framework_note: string; import_guide: string; items: { seq: string; topic: string; detail: string }[] }
interface SignoffSummary { authority: string; deadline_note: string; required_docs_count: number; unconfirmed_count: number; check_items_count: number }
// 带入来源：project 另一个项目（点了打开那个项目的这一题）/ route 本项目的流向与业务 / said 你提供的业务情况（附原话）
interface Carried { kind: 'project' | 'route' | 'said' | 'risk'; text: string; project_id?: string; quote?: string }
// kind 为 risks：主要风险按行填（lacking 每行还缺什么，remediate 还有几项没生成整改项）；max 这题最多几字
interface Question { kind?: 'risks'; lacking?: string[]; remediate?: { pending: number; done: number }; max?: number; key: string; label: string; question: string; why: string; hint: string; used_for: string; example: string; basis: BasisCard[]; carried: Carried | null; issues: Issue[]; density_hint?: DensityHint | null; other?: boolean; detail_on?: string; short: boolean; optional: boolean; choices: string[]; value: string; tier: 'company' | 'business' | 'system'; suggestion: Suggestion | null; first_doc: string; used_in: string[] }
interface SuggestState { state: 'none' | 'running' | 'done' | 'failed'; seconds?: number; error?: string; count: number }
interface Route { source_country: string; recipient_country: string; function: string; functions: string[]; business_scene: string; business_scenes: string[]; scenario_text: string; vehicle_model: string; company_name: string; company_name_carried: Carried | null; library_country: string }
// 走安全评估时的两个期限：申报截止日（自评估完成日起 3 个月）、评估结果到期日（出具日起 3 年）
interface Period { date: string; days_left: number; state: string }
interface Validity { assessed_on: string; issued_on: string; filing: Period | null; result: Period | null; extend: string; basis: BasisCard[] }
interface CnRoute { route: 'security_assessment' | 'standard_contract' | 'exempt' | 'unknown'; text: string; because: string[]; missing: string[]; missing_keys?: string[]; basis: string; validity: Validity | null }
// 每份文件是否需要：need 需要 / na 按目前情况不需要 / unknown 还不能判断（缺的题在 missing_keys，可跳过去回答）
interface Applies { state: 'need' | 'na' | 'unknown'; label: string; why: string; basis: string; missing: string[]; missing_keys: string[]; manual_include?: boolean; auto_why?: string }
interface RedactionBrief { total: number; labels: string[]; custom_terms_configured: number }
interface SuggestDiagnosis { doc_kind?: string; summary: string; gaps: string[]; followups: string[]; redaction?: RedactionBrief | null }
interface RiskEvolution { total: number; active_count: number; excluded_count: number; downgraded_count: number; upgraded_count: number; has_change: boolean; init_counts: Record<string, number>; current_counts: Record<string, number> }
interface RiskSync { stale?: boolean; can_diagnose?: boolean }
interface Project { id: string; title: string; conversation_code: string; archived: boolean; route: Route; documents: DocView[]; questions: Question[]; thin_count?: number; third_country_checklists?: ThirdCountryChecklist[]; signoff_summary?: SignoffSummary | null; ready_count: number; required_count?: number; final: FinalStatus; route_issues: Issue[]; suggest: SuggestState; suggest_diagnosis?: SuggestDiagnosis | null; risk_evolution?: RiskEvolution | null; risk_sync?: RiskSync | null; project_verdict?: ProjectVerdict | null; route_suggestions: Record<string, Suggestion>; cn_route: CnRoute | null; applicability: Record<string, Applies> }
interface Item { id: string; title: string; conversation_code: string; source_country: string; recipient_country: string; updated_at: string | null }
interface Options { countries: string[]; library_countries: string[]; functions: { key: string; label: string }[]; scenes: { key: string; label: string }[] }

const route = useRoute()
const auth = useAuth()
const editable = computed(() => canEdit(auth.user))  // 普通查看者只能看
const downloadable = computed(() => canExport(auth.user))
const router = useRouter()
const items = ref<Item[]>([])
const archivedItems = ref<Item[]>([])
const listLoaded = ref(false)
const showArchive = ref(false)
// 第 7.5 项 规则 18：项目列表可收起成窄条，给中间填写区让宽；收起与否记在本机
const RAIL_KEY = 'cyshuhe.docs.rail-collapsed'
const railCollapsed = ref(localStorage.getItem(RAIL_KEY) === '1')
watch(railCollapsed, (v) => localStorage.setItem(RAIL_KEY, v ? '1' : '0'))
const project = ref<Project | null>(null)
// 只能查看：项目已归档，或这是只读账号
const locked = computed(() => !!project.value?.archived || !editable.value)
const options = ref<Options>({ countries: [], library_countries: [], functions: [], scenes: [] })
const loading = ref(false)
const busy = ref('')
const edits = reactive<Record<string, string>>({})
const startForm = reactive({ source: '', recipient: '', functions: [] as string[], scenes: [] as string[], text: '', vehicle: '', company: '' })
const editingRoute = ref(false)

const openId = computed(() => (route.query.project as string) || '')
const isNew = computed(() => !openId.value)

async function loadList() {
  try {
    items.value = await api.get<Item[]>('/api/analysis/doc-projects')
    archivedItems.value = await api.get<Item[]>('/api/analysis/doc-projects?archived=true')
  } finally {
    listLoaded.value = true
  }
}
async function open(id: string, refresh = false) {
  // refresh：后台生成时的定时刷新。用户正在改、还没保存的输入不能被服务端的旧值盖掉
  const before = refresh && project.value?.id === id ? Object.fromEntries(project.value.questions.map((q) => [q.key, q.value])) : null
  if (!before) loading.value = true
  try {
    const next = await api.get<Project>(`/api/analysis/doc-projects/${id}`)
    if (!Array.isArray(next.route.functions)) next.route.functions = next.route.function ? [next.route.function] : []
    if (!Array.isArray(next.route.business_scenes)) next.route.business_scenes = next.route.business_scene ? [next.route.business_scene] : []
    project.value = next
    if (before) {
      for (const q of next.questions) if ((edits[q.key] ?? '') === (before[q.key] ?? '')) edits[q.key] = q.value
      return
    }
    for (const k of Object.keys(edits)) delete edits[k]
    for (const q of next.questions) edits[q.key] = q.value
    if (next.archived) showArchive.value = true
    const want = typeof route.query.q === 'string' ? route.query.q : (typeof route.query.question === 'string' ? route.query.question : '')
    if (want) { gotoQuestion(want); void router.replace({ query: { project: id } }) }
    // 从法规知识库被本企业合规文件引用过来：停在那份文件
    const wantDoc = typeof route.query.doc === 'string' ? route.query.doc : ''
    if (wantDoc && !want) {
      if (next.applicability?.[wantDoc]?.state === 'na') showNaDocs.value = true
      window.setTimeout(() => {
        const el = document.querySelector<HTMLElement>(`[data-testid="doc-${wantDoc}"]`)
        el?.scrollIntoView({ block: 'center' })
        el?.classList.add('flash')
        window.setTimeout(() => el?.classList.remove('flash'), 2400)
      }, 200)
      void router.replace({ query: { project: id } })
    }
  } catch (e) { ElMessage.error((e as Error).message); if (!before) await router.replace({ query: {} }) } finally { loading.value = false }
}
async function create() {
  if (!startForm.source || !startForm.recipient) { ElMessage.warning('请先选择数据发出国与数据接收国'); return }
  if (startForm.source.trim() === startForm.recipient.trim()) { ElMessage.warning('数据发出国与接收国不能相同，请选择境外目的国或地区'); return }
  if (!startForm.functions.length && !startForm.scenes.length && !startForm.text.trim()) { ElMessage.warning('请选择车辆数据主要用途（支持多场景多选）或填写业务说明'); return }
  busy.value = 'create'
  try {
    const p = await api.post<Project>('/api/analysis/doc-projects', {
      source_country: startForm.source, recipient_country: startForm.recipient,
      functions: startForm.functions, function: startForm.functions[0] || null,
      business_scenes: startForm.scenes, business_scene: startForm.scenes[0] || null,
      scenario_text: startForm.text,
      vehicle_model: startForm.vehicle, company_name: startForm.company,
    })
    await loadList()
    await router.push({ query: { project: p.id } })
  } catch (e) { ElMessage.error((e as Error).message) } finally { busy.value = '' }
}
async function saveRoute() {
  if (!project.value) return
  const r = project.value.route
  if (r.source_country.trim() === r.recipient_country.trim()) {
    ElMessage.warning('数据发出国与接收国不能相同，请选择境外目的国或地区')
    return
  }
  busy.value = 'route'
  try {
    const fns = Array.isArray(r.functions) ? r.functions : (r.function ? [r.function] : [])
    const scs = Array.isArray(r.business_scenes) ? r.business_scenes : (r.business_scene ? [r.business_scene] : [])
    const next = await api.patch<Project>(`/api/analysis/doc-projects/${project.value.id}`, {
      source_country: r.source_country, recipient_country: r.recipient_country,
      functions: fns, function: fns[0] || '',
      business_scenes: scs, business_scene: scs[0] || '',
      scenario_text: r.scenario_text, vehicle_model: r.vehicle_model, company_name: r.company_name,
    })
    if (!Array.isArray(next.route.functions)) next.route.functions = next.route.function ? [next.route.function] : []
    if (!Array.isArray(next.route.business_scenes)) next.route.business_scenes = next.route.business_scene ? [next.route.business_scene] : []
    project.value = next
    editingRoute.value = false
    await loadList()
    ElMessage.success('已更新流向与多场景配置，受影响的文件与属地准入事项已同步重新核对')
  } catch (e) { ElMessage.error((e as Error).message) } finally { busy.value = '' }
}
// 安全评估的两个日期：选了就保存，清空就清除
async function saveDate(key: 'self_assessed_on' | 'result_issued_on', value: string | null) {
  if (!project.value) return
  busy.value = key
  try {
    project.value = await api.patch<Project>(`/api/analysis/doc-projects/${project.value.id}`, { [key]: value || '' })
  } catch (e) { ElMessage.error((e as Error).message) } finally { busy.value = '' }
}
const afterToday = (d: Date) => d.getTime() > Date.now()
// 过了期限：申报截止写已超过，到期日写已过期（与准入批文台账同一套说法）
const periodText = (p: Period, over = '已过期') => (p.days_left >= 0 ? `${p.date}，还剩 ${p.days_left} 天` : `${p.date}，${over} ${-p.days_left} 天`)
const changed = computed(() => (project.value?.questions ?? []).filter((q) => (edits[q.key] ?? '') !== q.value).map((q) => q.key))
const NEG_COUNT_RE = /(?:^|[\s：:，,（(=])-\s*\d/
const PURE_ASCII_NONNUM_RE = /^[A-Za-z_.\s]+$/
async function saveAnswers(keys?: string[]) {
  if (!project.value) return
  const send = keys ?? changed.value
  if (!send.length) return
  for (const k of send) {
    const val = (edits[k] ?? '').trim()
    if (!val) continue
    if ((k === 'data_volume' || k === 'sensitive_pi' || k === 'employee_count') && NEG_COUNT_RE.test(val)) {
      ElMessage.warning('人数或数据规模不能为负数，请填写有效的非负数量说明')
      return
    }
    if (k === 'data_volume' && PURE_ASCII_NONNUM_RE.test(val)) {
      ElMessage.warning('出境数据规模请包含具体人数或数量级（例如：5 万人、约 12 万条）')
      return
    }
  }
  busy.value = 'save'
  try {
    const answers: Record<string, string> = {}
    for (const k of send) answers[k] = edits[k] ?? ''
    const next = await api.put<Project>(`/api/analysis/doc-projects/${project.value.id}/answers`, { answers })
    project.value = next
    for (const q of next.questions) if (!send.includes(q.key)) edits[q.key] = edits[q.key] ?? q.value
  } catch (e) { ElMessage.error((e as Error).message) } finally { busy.value = '' }
}
const pasted = ref('')
const fileEl = ref<HTMLInputElement | null>(null)
async function readText() {
  if (!project.value || !pasted.value.trim()) { ElMessage.warning('请先输入或粘贴业务情况说明'); return }
  busy.value = 'suggest'
  try {
    await api.post(`/api/analysis/doc-projects/${project.value.id}/suggest`, { text: pasted.value })
    await open(project.value.id)
  } catch (e) { ElMessage.error((e as Error).message) } finally { busy.value = '' }
}
async function uploadFromChat(file: File): Promise<string | void> {
  if (!project.value) return
  busy.value = 'suggest'
  try {
    const res = await fetch(`/api/analysis/doc-projects/${project.value.id}/suggest-file?filename=${encodeURIComponent(file.name)}`, {
      method: 'POST', headers: { Authorization: `Bearer ${api.token()}`, 'Content-Type': 'application/octet-stream' }, body: file,
    })
    const body = await res.json().catch(() => ({}))
    if (!res.ok) throw new Error(body.detail || '读取失败，可重试')
    if (body.note) ElMessage.info(body.note)
    await open(project.value.id)
    return body.note ? `${body.note} 已启动材料提取与《隐私政策》一致性核对，识别结果稍后显示在左侧。` : undefined
  } finally { busy.value = '' }
}
async function readFile(e: Event) {
  const input = e.target as HTMLInputElement
  const file = input.files?.[0]
  input.value = ''
  if (!file || !project.value) return
  try { await uploadFromChat(file) } catch (err) { ElMessage.error((err as Error).message) }
}
async function resolveSuggestions(keys: string[] | null, dismiss = false) {
  if (!project.value) return
  busy.value = 'accept'
  try {
    project.value = await api.post<Project>(`/api/analysis/doc-projects/${project.value.id}/suggestions/accept`, { keys, dismiss })
    for (const q of project.value.questions) edits[q.key] = q.value
    if (!dismiss) ElMessage.success('已采用，文件已更新；如有不符可在下方修改')
  } catch (e) { ElMessage.error((e as Error).message) } finally { busy.value = '' }
}
const SUGGEST_LABELS: Record<string, string> = { company_name: '公司名称', vehicle_model: '车型或车队', functions: '识别到的多场景用途' }
const pending = computed(() => {
  const out: { key: string; label: string; sg: Suggestion }[] = []
  for (const [k, sg] of Object.entries(project.value?.route_suggestions ?? {})) out.push({ key: k, label: SUGGEST_LABELS[k] ?? k, sg })
  for (const q of project.value?.questions ?? []) if (q.suggestion) out.push({ key: q.key, label: q.label, sg: q.suggestion })
  return out
})
function goPending() { document.querySelector('[data-testid=doc-pending]')?.scrollIntoView({ behavior: 'smooth', block: 'start' }) }
// 对话里说了业务情况：助手在后台识别，项目页随后刷新出请核对的内容
function refreshSoon() { if (project.value) void open(project.value.id) }
function unsure(q: Question) { edits[q.key] = '暂不确定（待补充）'; void saveAnswers([q.key]) }

async function fetchFile(url: string, fallback: string) {
  const res = await fetch(url, { headers: { Authorization: `Bearer ${api.token()}` } })
  if (!res.ok) {
    let msg = '下载失败，可重试'
    try { msg = (await res.json()).detail || msg } catch { /* 非 JSON */ }
    throw new Error(msg)
  }
  const name = /filename\*=UTF-8''([^;]+)/i.exec(res.headers.get('content-disposition') || '')
  const href = URL.createObjectURL(await res.blob())
  const a = document.createElement('a')
  a.href = href; a.download = name ? decodeURIComponent(name[1]) : fallback
  document.body.appendChild(a); a.click(); a.remove()
  window.setTimeout(() => URL.revokeObjectURL(href), 30000)
}
type DocDlCmd = 'docx' | 'pdf' | 'xlsx' | 'en-docx' | 'en-pdf' | 'en-preview' | 'i18n-docx' | 'i18n-pdf' | 'i18n-preview'
const hostLang = ref<HostReportLang>('fr')
const hostLangMeta = computed(() => HOST_REPORT_LANGUAGES.find((l) => l.code === hostLang.value) || HOST_REPORT_LANGUAGES[0])

watch(
  () => [project.value?.id, project.value?.route?.recipient_country, project.value?.route?.source_country] as const,
  ([, rcp, src]) => {
    if (rcp || src) hostLang.value = inferHostLanguage(rcp, src)
  },
  { immediate: true },
)

async function download(d: DocView, kind: DocDlCmd) {
  if (!project.value) return
  if (kind === 'en-preview') {
    await openPreviewForeign(d, 'en')
    return
  }
  if (kind === 'i18n-preview') {
    await openPreviewForeign(d, hostLang.value)
    return
  }
  if (!downloadable.value) return
  if (kind === 'en-docx' || kind === 'en-pdf') {
    const ext = kind === 'en-docx' ? 'docx' : 'pdf'
    busy.value = `dl-${d.code}-en-${ext}`
    try {
      await fetchFile(`/api/analysis/tasks/${project.value.id}/documents/${d.code}/export-en.${ext}?draft=${d.state !== 'ready'}`, `${d.code}_EN.${ext}`)
    } catch (e) { ElMessage.error((e as Error).message) } finally { busy.value = '' }
    return
  }
  if (kind === 'i18n-docx' || kind === 'i18n-pdf') {
    const ext = kind === 'i18n-docx' ? 'docx' : 'pdf'
    const lg = hostLang.value
    busy.value = `dl-${d.code}-${lg}-${ext}`
    try {
      await fetchFile(`/api/analysis/tasks/${project.value.id}/documents/${d.code}/export-i18n.${ext}?lang=${lg}&draft=${d.state !== 'ready'}`, `${d.code}_${lg.toUpperCase()}.${ext}`)
    } catch (e) { ElMessage.error((e as Error).message) } finally { busy.value = '' }
    return
  }
  busy.value = kind === 'docx' ? `dl-${d.code}` : `dl-${d.code}-${kind}`
  try {
    await fetchFile(`/api/analysis/tasks/${project.value.id}/documents/${d.code}/export.${kind}?draft=${d.state !== 'ready'}`, `${d.name}.${kind}`)
  } catch (e) { ElMessage.error((e as Error).message) } finally { busy.value = '' }
}
async function openPreviewForeign(d: DocView, lg: 'en' | HostReportLang) {
  if (!project.value) return
  previewCode.value = d.code
  previewDraft.value = d.state !== 'ready'
  previewIsEdpbEn.value = true
  previewAidKind.value = ''
  previewEditMode.value = false
  previewEdits.value = []
  showEditHistory.value = false
  const lgLabel = lg === 'en' ? '英文版' : `${hostLangMeta.value.nameZh}版`
  previewTitle.value = `${d.name}（${lgLabel}预览）`
  previewBlocks.value = []
  previewVisible.value = true
  previewLoading.value = true
  try {
    const ep =
      lg === 'en'
        ? `/api/analysis/tasks/${project.value.id}/documents/${d.code}/preview-en?draft=${d.state !== 'ready'}`
        : `/api/analysis/tasks/${project.value.id}/documents/${d.code}/preview-i18n?lang=${lg}&draft=${d.state !== 'ready'}`
    const res = await api.get<{ name: string; kind: 'edited' | 'official_docx' | 'standard'; blocks: PreviewBlock[] }>(ep)
    previewTitle.value = `${d.name} · ${res.name || lgLabel}`
    previewKind.value = res.kind
    previewBlocks.value = res.blocks || []
  } catch (e) {
    ElMessage.error((e as Error).message)
    previewVisible.value = false
  } finally {
    previewLoading.value = false
  }
}
async function downloadEdpbEn(d: DocView, kind: 'docx' | 'pdf') {
  if (!project.value || !downloadable.value) return
  busy.value = `dl-edpb-${kind}`
  try {
    await fetchFile(`/api/analysis/tasks/${project.value.id}/documents/dpia/export-edpb-en.${kind}?draft=${d.state !== 'ready'}`, `EDPB_2026_DPIA_Standard_EN.${kind}`)
  } catch (e) { ElMessage.error((e as Error).message) } finally { busy.value = '' }
}
async function openPreviewEdpbEn(d: DocView) {
  if (!project.value) return
  previewCode.value = 'dpia'
  previewDraft.value = d.state !== 'ready'
  previewIsEdpbEn.value = true
  previewAidKind.value = ''
  previewEditMode.value = false
  previewEdits.value = []
  showEditHistory.value = false
  previewTitle.value = '数据保护影响评估（欧盟标准英文范本）'
  previewBlocks.value = []
  previewVisible.value = true
  previewLoading.value = true
  try {
    const res = await api.get<{ name: string; kind: 'edited' | 'official_docx' | 'standard'; blocks: PreviewBlock[] }>(
      `/api/analysis/tasks/${project.value.id}/documents/dpia/preview-edpb-en?draft=${d.state !== 'ready'}`,
    )
    previewTitle.value = res.name || previewTitle.value
    previewKind.value = res.kind
    previewBlocks.value = res.blocks || []
  } catch (e) {
    ElMessage.error((e as Error).message)
    previewVisible.value = false
  } finally {
    previewLoading.value = false
  }
}
async function downloadAll(draft: boolean, fmt: 'docx' | 'pdf' | 'xlsx' = 'docx', lang: ReportOutputLang = 'zh') {
  if (!project.value || !downloadable.value) return
  busy.value = 'zip'
  const suffix = lang === 'zh' ? '' : `_${lang.toUpperCase()}`
  try { await fetchFile(`/api/analysis/doc-projects/${project.value.id}/export.zip?fmt=${fmt}&draft=${draft}&lang=${lang}`, `${project.value.title}${suffix}.zip`) } catch (e) { ElMessage.error((e as Error).message) } finally { busy.value = '' }
}
async function writePaper(d: DocView) {
  if (!project.value) return
  try {
    await api.post(`/api/analysis/doc-projects/${project.value.id}/documents/${d.code}/paper`, {})
    await open(project.value.id)
    ElMessage.success('已开始生成论述，约需几分钟，完成后会提示')
  } catch (e) { ElMessage.error((e as Error).message) }
}
const isDocxOfficialForm = (f: { code: string; kind?: string }) => f.kind === 'docx' || f.code === 'SG_IMDA_SDOC'
async function downloadOfficial(f: { code: string; kind?: string }) {
  if (!project.value || !downloadable.value) return
  busy.value = `of-${f.code}`
  const ext = isDocxOfficialForm(f) ? 'docx' : 'pdf'
  try { await fetchFile(`/api/analysis/doc-projects/${project.value.id}/official-forms/${f.code}/export.pdf`, `${f.code}.${ext}`) }
  catch (e) { ElMessage.error((e as Error).message) } finally { busy.value = '' }
}
// 申报表一栏：发出国有官方表格时，申报表就是官方原表（不另给通用申报表）；数据从中国发出时申报表是网信办 Word 原件
const officialOnly = (d: DocView) => d.official_only
async function openOfficial(code: string, instanceId?: string | null) {
  if (!project.value) return
  const query = { back: `/documents?project=${project.value.id}` }
  if (!editable.value) {
    if (instanceId) await router.push({ name: 'form', params: { id: instanceId }, query })
    return
  }
  try {
    const made = await api.post<{ id: string }>('/api/forms/instances', { form_code: code, task_id: project.value.id })
    await router.push({ name: 'form', params: { id: made.id }, query })
  } catch (e) { ElMessage.error((e as Error).message) }
}
async function archive(it: { id: string; title: string }) {
  try {
    await ElMessageBox.confirm(`该项目将移入已归档列表，随时可以恢复；如需彻底清除可在已归档列表中操作。\n\n《${it.title}》`, '确认归档项目', { confirmButtonText: '归档', cancelButtonText: '取消', type: 'warning' })
  } catch { return }
  await api.delete(`/api/analysis/tasks/${it.id}`)
  await loadList()
  if (openId.value === it.id) { project.value = null; await router.replace({ query: {} }) }
  ElMessage.success('已移入已归档列表')
}
async function restore(it: { id: string }) {
  await api.post(`/api/analysis/tasks/${it.id}/restore`, {})
  await loadList(); showArchive.value = false
  await router.push({ query: { project: it.id } })
}
async function forGood(it: { id: string; title: string }) {
  try {
    await ElMessageBox.confirm(`彻底删除后无法恢复。\n\n《${it.title}》`, '确认彻底删除', { confirmButtonText: '彻底删除', cancelButtonText: '取消', type: 'error' })
  } catch { return }
  await api.delete(`/api/analysis/tasks/${it.id}?permanent=true`)
  await loadList()
}
async function rename(it: { id: string; title: string }) {
  let value = ''
  try {
    const r = await ElMessageBox.prompt('请输入项目名称（不超过 60 字）', '重命名项目', { confirmButtonText: '保存', cancelButtonText: '取消', inputValue: it.title, inputPattern: /\S/, inputErrorMessage: '项目名称不能为空' })
    value = (r as { value: string }).value.slice(0, 60)
  } catch { return }
  await api.patch(`/api/analysis/tasks/${it.id}`, { title: value })
  await loadList()
  if (openId.value === it.id) await open(it.id)
}
async function cloneProject(it: { id: string; title: string }) {
  busy.value = 'clone'
  try {
    const cloned = await api.post<Project>(`/api/analysis/doc-projects/${it.id}/clone`, {})
    await loadList()
    await router.push({ query: { project: cloned.id } })
    ElMessage.success(`已复制项目：${cloned.title}`)
  } catch (e) {
    ElMessage.error((e as Error).message)
  } finally {
    busy.value = ''
  }
}
async function batchArchive() {
  if (!items.value.length) return
  try {
    await ElMessageBox.confirm(`将当前列表中的 ${items.value.length} 个合规文件项目全部移入已归档？移入后随时可一键恢复。`, '一键归档全部项目？', { confirmButtonText: '一键归档', cancelButtonText: '取消', type: 'warning' })
  } catch { return }
  busy.value = 'batch-archive'
  try {
    const res = await api.post<{ archived_count: number; skipped_running: number; ids: string[] }>('/api/analysis/tasks/batch-archive', { kind: 'documents' })
    await loadList()
    if (openId.value && res.ids?.includes(openId.value)) {
      project.value = null
      await router.replace({ query: {} })
    }
    ElMessage.success(res.skipped_running ? `已归档 ${res.archived_count} 个项目（跳过 ${res.skipped_running} 个运行中项目）` : `已归档 ${res.archived_count} 个项目`)
  } catch (e) { ElMessage.error((e as Error).message) } finally { busy.value = '' }
}
async function batchRestore() {
  if (!archivedItems.value.length) return
  try {
    await ElMessageBox.confirm(`将归档中的 ${archivedItems.value.length} 个合规文件项目全部恢复至项目列表？`, '一键恢复归档项目？', { confirmButtonText: '一键恢复', cancelButtonText: '取消', type: 'info' })
  } catch { return }
  busy.value = 'batch-restore'
  try {
    const res = await api.post<{ restored_count: number; ids: string[] }>('/api/analysis/tasks/batch-restore', { kind: 'documents' })
    await loadList()
    showArchive.value = false
    if (openId.value && res.ids?.includes(openId.value)) await open(openId.value)
    ElMessage.success(`已恢复 ${res.restored_count} 个项目`)
  } catch (e) { ElMessage.error((e as Error).message) } finally { busy.value = '' }
}
async function batchDelete() {
  if (!archivedItems.value.length) return
  try {
    await ElMessageBox.confirm(`彻底清空归档中的 ${archivedItems.value.length} 个合规文件项目？删除后无法恢复。`, '一键清空已归档项目？', { confirmButtonText: '彻底清空', cancelButtonText: '取消', type: 'error' })
  } catch { return }
  busy.value = 'batch-delete'
  try {
    const res = await api.post<{ deleted_count: number; ids: string[] }>('/api/analysis/tasks/batch-delete', { kind: 'documents' })
    await loadList()
    if (openId.value && res.ids?.includes(openId.value)) {
      project.value = null
      await router.replace({ query: {} })
    }
    ElMessage.success(`已彻底删除 ${res.deleted_count} 个已归档项目`)
  } catch (e) { ElMessage.error((e as Error).message) } finally { busy.value = '' }
}
async function toggleManualInclude(d: DocView, include: boolean) {
  if (!project.value) return
  busy.value = `inc-${d.code}`
  try {
    project.value = await api.post<Project>(`/api/analysis/doc-projects/${project.value.id}/documents/${d.code}/include`, { include })
    ElMessage.success(include ? `已将《${d.name}》纳入本次出件清单` : `《${d.name}》已恢复按出境路径自动判断`)
  } catch (e) { ElMessage.error((e as Error).message) } finally { busy.value = '' }
}
interface DocEditLog { user_name: string; place: string; before: string; after: string; at: string | null }
interface DocVersionItem { version_no: number; edit_count: number; note: string; user_name: string; created_at: string | null; is_current: boolean }
const previewVisible = ref(false)
const previewLoading = ref(false)
const previewSaving = ref(false)
const previewTitle = ref('')
const previewCode = ref('')
const previewDraft = ref(true)
const previewIsEdpbEn = ref(false)
const previewAidKind = ref<'' | 'signoff' | 'third-country'>('')
const showThirdCountry = ref(false)
const previewEditMode = ref(false)
const showEditHistory = ref(false)
const showVersionHistory = ref(false)
const previewEdits = ref<DocEditLog[]>([])
const previewVersions = ref<DocVersionItem[]>([])
const viewingVersionNo = ref<number | null>(null)
const viewingVersionBlocks = ref<PreviewBlock[]>([])
const pendingVersionSeal = ref(false)
let versionSealTimer: number | undefined
const previewKind = ref<'edited' | 'official_docx' | 'standard'>('standard')
const previewBlocks = ref<PreviewBlock[]>([])
const displayedPreviewBlocks = computed(() =>
  viewingVersionNo.value !== null && viewingVersionBlocks.value.length ? viewingVersionBlocks.value : previewBlocks.value,
)
const previewHasOutline = computed(
  () => previewBlocks.value.filter((b) => (b.type === 'heading' || b.type === 'subheading') && (b.text || '').trim()).length >= 2,
)
const previewSubtitle = computed(() => {
  if (previewEditMode.value) return '点击任意正文段落或表格单元格即可直接修订，保存后自动同步至下载文件'
  if (previewHasOutline.value) {
    return previewAidKind.value
      ? '左侧目录支持一键跳至各章节；支持直接核对各项内容并导出正式文件'
      : '左侧目录支持一键跳至各章节；未填写项在初稿中显示为【待补充】'
  }
  return '支持直接核对各项内容并导出正式文件'
})
function riskClearedCount(evo?: RiskEvolution | null): number {
  if (!evo) return 0
  const initSum = (evo.init_counts['高风险'] ?? 0) + (evo.init_counts['中风险'] ?? 0) + (evo.init_counts['低风险'] ?? 0)
  const curSum = (evo.current_counts['高风险'] ?? 0) + (evo.current_counts['中风险'] ?? 0) + (evo.current_counts['低风险'] ?? 0)
  return Math.max(evo.excluded_count || 0, Math.max(0, Math.max(evo.total || 0, initSum) - curSum))
}
async function openAidPreview(kind: 'signoff' | 'third-country') {
  if (!project.value) return
  previewCode.value = ''
  previewIsEdpbEn.value = false
  previewAidKind.value = kind
  previewEditMode.value = false
  showEditHistory.value = false
  previewEdits.value = []
  previewTitle.value = kind === 'signoff' ? '正式申报递交前人工核签清单' : '库外法域属地法律待核查清单'
  previewBlocks.value = []
  previewVisible.value = true
  previewLoading.value = true
  try {
    const res = await api.get<{ name: string; kind: 'standard'; blocks: PreviewBlock[] }>(
      `/api/analysis/doc-projects/${project.value.id}/delivery-aid/${kind}/preview`,
    )
    previewTitle.value = res.name || previewTitle.value
    previewKind.value = 'standard'
    previewBlocks.value = res.blocks || []
  } catch (e) {
    ElMessage.error((e as Error).message)
    previewVisible.value = false
  } finally {
    previewLoading.value = false
  }
}
async function downloadAid(kind: 'signoff' | 'third-country', fmt: 'docx' | 'pdf' | 'xlsx') {
  if (!project.value || !downloadable.value) return
  busy.value = `aid-${kind}-${fmt}`
  const fallback = kind === 'signoff' ? `正式申报递交前人工核签清单.${fmt}` : `库外法域属地法律待核查清单.${fmt}`
  try {
    await fetchFile(`/api/analysis/doc-projects/${project.value.id}/delivery-aid/${kind}/export.${fmt}`, fallback)
  } catch (e) {
    ElMessage.error((e as Error).message)
  } finally {
    busy.value = ''
  }
}
async function openPreview(d: DocView) {
  if (!project.value) return
  previewAidKind.value = ''
  previewCode.value = d.code
  previewDraft.value = d.state !== 'ready'
  previewIsEdpbEn.value = false
  previewEditMode.value = false
  showEditHistory.value = false
  showVersionHistory.value = false
  previewEdits.value = []
  previewVersions.value = []
  viewingVersionNo.value = null
  viewingVersionBlocks.value = []
  pendingVersionSeal.value = false
  if (versionSealTimer) { window.clearTimeout(versionSealTimer); versionSealTimer = undefined }
  previewTitle.value = d.name
  previewBlocks.value = []
  previewVisible.value = true
  previewLoading.value = true
  try {
    const [res, saved] = await Promise.all([
      api.get<{ name: string; kind: 'edited' | 'official_docx' | 'standard'; blocks: PreviewBlock[] }>(`/api/analysis/tasks/${project.value.id}/documents/${d.code}/preview`),
      api.get<{ exists: boolean; edits?: DocEditLog[]; versions?: DocVersionItem[] }>(`/api/analysis/tasks/${project.value.id}/documents/${d.code}/saved`).catch(() => ({ exists: false as const })),
    ])
    previewTitle.value = res.name || d.name
    previewKind.value = res.kind
    previewBlocks.value = res.blocks || []
    if (saved.exists && saved.edits) previewEdits.value = saved.edits
    if (saved.exists && saved.versions) previewVersions.value = saved.versions
  } catch (e) {
    ElMessage.error((e as Error).message)
    previewVisible.value = false
  } finally {
    previewLoading.value = false
  }
}
async function sealPreviewVersion(note = '') {
  if (versionSealTimer) { window.clearTimeout(versionSealTimer); versionSealTimer = undefined }
  if (!pendingVersionSeal.value || !project.value || !previewCode.value || previewIsEdpbEn.value || locked.value) return
  const taskId = project.value.id
  const docCode = previewCode.value
  pendingVersionSeal.value = false
  try {
    const res = await api.post<{ exists: boolean; created: boolean; version_no?: number; versions?: DocVersionItem[] }>(
      `/api/analysis/tasks/${taskId}/documents/${docCode}/versions`,
      { note },
    )
    if (previewCode.value === docCode && res.versions) {
      previewVersions.value = res.versions
    }
  } catch {
    // 后台存版静默容错，下次停笔或切出时仍会自动补齐
  }
}
function scheduleVersionSeal() {
  if (versionSealTimer) window.clearTimeout(versionSealTimer)
  pendingVersionSeal.value = true
  versionSealTimer = window.setTimeout(() => {
    void sealPreviewVersion('停止编辑自动存版')
  }, 2200)
}
watch(previewEditMode, (on, wasOn) => {
  if (on) {
    viewingVersionNo.value = null
    viewingVersionBlocks.value = []
  } else if (wasOn && pendingVersionSeal.value) {
    void sealPreviewVersion('完成编辑自动存版')
  }
})
watch(previewVisible, (vis, wasVis) => {
  if (!vis && wasVis) {
    if (pendingVersionSeal.value) void sealPreviewVersion('停止编辑自动存版')
    viewingVersionNo.value = null
    viewingVersionBlocks.value = []
  }
})
async function inspectVersion(v: DocVersionItem) {
  if (!project.value || !previewCode.value) return
  if (v.is_current) {
    viewingVersionNo.value = null
    viewingVersionBlocks.value = []
    return
  }
  previewLoading.value = true
  try {
    const res = await api.get<{ version_no: number; blocks: PreviewBlock[] }>(
      `/api/analysis/tasks/${project.value.id}/documents/${previewCode.value}/versions/${v.version_no}`,
    )
    previewEditMode.value = false
    viewingVersionNo.value = res.version_no
    viewingVersionBlocks.value = res.blocks || []
  } catch (e) {
    ElMessage.error((e as Error).message)
  } finally {
    previewLoading.value = false
  }
}
async function rollbackToVersion(v: DocVersionItem) {
  if (!project.value || !previewCode.value || previewIsEdpbEn.value || locked.value) return
  const tsLabel = localTime(v.created_at) || `第 ${v.version_no} 版`
  try {
    await ElMessageBox.confirm(
      `确认将当前文件回退至第 ${v.version_no} 版（${tsLabel}）？回退前的修订内容仍保留在历史版本记录中，可随时按日期再次切换。`,
      '按日期回退历史版本',
      { confirmButtonText: '确认回退', cancelButtonText: '取消', type: 'warning' },
    )
  } catch {
    return
  }
  if (versionSealTimer) { window.clearTimeout(versionSealTimer); versionSealTimer = undefined }
  pendingVersionSeal.value = false
  previewSaving.value = true
  try {
    const res = await api.post<{ exists: boolean; blocks: PreviewBlock[]; edits: DocEditLog[]; versions: DocVersionItem[] }>(
      `/api/analysis/tasks/${project.value.id}/documents/${previewCode.value}/versions/${v.version_no}/rollback`,
      {},
    )
    viewingVersionNo.value = null
    viewingVersionBlocks.value = []
    previewEditMode.value = false
    previewKind.value = 'edited'
    previewBlocks.value = res.blocks || previewBlocks.value
    previewEdits.value = res.edits || []
    previewVersions.value = res.versions || []
    const row = project.value.documents.find((x) => x.code === previewCode.value)
    if (row) row.edited = true
    ElMessage.success(`已回退至第 ${v.version_no} 版（${tsLabel}），下载文件同步生效`)
  } catch (e) {
    ElMessage.error((e as Error).message)
  } finally {
    previewSaving.value = false
  }
}
async function savePreviewEdit(payload: { block: number; text: string; item?: number; row?: number; col?: number }) {
  if (!project.value || !previewCode.value || previewIsEdpbEn.value || locked.value) return
  previewSaving.value = true
  try {
    if (previewKind.value !== 'edited') {
      await api.post(`/api/analysis/tasks/${project.value.id}/documents/${previewCode.value}/saved?draft=${previewDraft.value}`, {})
    }
    const res = await api.patch<{ exists: boolean; name: string; blocks: PreviewBlock[]; edit_count: number; edits: DocEditLog[]; versions?: DocVersionItem[] }>(
      `/api/analysis/tasks/${project.value.id}/documents/${previewCode.value}/saved`,
      payload,
    )
    viewingVersionNo.value = null
    viewingVersionBlocks.value = []
    previewKind.value = 'edited'
    previewBlocks.value = res.blocks || previewBlocks.value
    previewEdits.value = res.edits || []
    if (res.versions) previewVersions.value = res.versions
    const row = project.value.documents.find((x) => x.code === previewCode.value)
    if (row) row.edited = true
    scheduleVersionSeal()
    ElMessage.success('已保存修订，停止编辑后将自动生成带日期的历史版本')
  } catch (e) {
    ElMessage.error((e as Error).message)
  } finally {
    previewSaving.value = false
  }
}
async function resetPreviewEdits() {
  if (!project.value || !previewCode.value || previewIsEdpbEn.value || locked.value) return
  try {
    await ElMessageBox.confirm('恢复为自动生成版后，这份文件的手动修改记录将被清除。确认恢复？', '恢复自动生成版', {
      confirmButtonText: '确认恢复',
      cancelButtonText: '取消',
      type: 'warning',
    })
  } catch {
    return
  }
  if (versionSealTimer) { window.clearTimeout(versionSealTimer); versionSealTimer = undefined }
  pendingVersionSeal.value = false
  previewSaving.value = true
  try {
    await api.delete(`/api/analysis/tasks/${project.value.id}/documents/${previewCode.value}/saved`)
    previewEditMode.value = false
    showEditHistory.value = false
    showVersionHistory.value = false
    previewEdits.value = []
    previewVersions.value = []
    viewingVersionNo.value = null
    viewingVersionBlocks.value = []
    const res = await api.get<{ name: string; kind: 'edited' | 'official_docx' | 'standard'; blocks: PreviewBlock[] }>(
      `/api/analysis/tasks/${project.value.id}/documents/${previewCode.value}/preview`,
    )
    previewKind.value = res.kind
    previewBlocks.value = res.blocks || []
    const row = project.value.documents.find((x) => x.code === previewCode.value)
    if (row) row.edited = false
    ElMessage.success('已恢复为自动生成版')
  } catch (e) {
    ElMessage.error((e as Error).message)
  } finally {
    previewSaving.value = false
  }
}
const showNaDocs = ref(false)
const diagCollapsed = ref(true)

let timer: number | undefined
const running = computed(() => !!project.value?.documents.some((d) => d.paper?.state === 'running') || project.value?.suggest.state === 'running')
watch(running, (on) => {
  if (timer) { window.clearInterval(timer); timer = undefined }
  if (on) timer = window.setInterval(() => { if (openId.value) void open(openId.value, true) }, 5000)
})
watch(() => project.value?.suggest.state, (now, before) => {
  if (now === 'running') diagCollapsed.value = false
  if (before === 'running' && now === 'done' && !pending.value.length) ElMessage.info('已识别完毕，没有发现可新增的内容（已填写的填写项不会重复识别）。')
  else if (before === 'running' && now === 'done') ElMessage.success(`已识别 ${pending.value.length} 项，请在上方核对`)
})
watch(openId, (id) => {
  showNaDocs.value = false
  showThirdCountry.value = false
  showMainRiskEditor.value = false
  diagCollapsed.value = true
  if (id) void open(id)
  else if (!editable.value && items.value.length > 0) void router.replace({ query: { project: items.value[0].id } })
  else project.value = null
})
onMounted(async () => {
  options.value = await api.get<Options>('/api/analysis/doc-projects/options')
  await loadList()
  if (openId.value) await open(openId.value)
  else if (!editable.value && items.value.length > 0) await router.replace({ query: { project: items.value[0].id } })
})
onUnmounted(() => { if (timer) window.clearInterval(timer) })

const stateText = (d: DocView) =>
  d.paper?.state === 'running' ? `论述生成中，已 ${Math.floor((d.paper.seconds ?? 0) / 60)} 分 ${(d.paper.seconds ?? 0) % 60} 秒`
    : STATE_LABEL[d.state]
const STATE_LABEL: Record<DocView['state'], string> = { not_started: '未开始', filling: '填写中', needs_paper: '待生成论述', ready: '已就绪' }
const docName = (code: string) => project.value?.documents.find((d) => d.code === code)?.name ?? code
const open_ = (q: Question) => !q.optional && (!(q.value || '').trim() || !!q.lacking?.length)
const tierOpen = (tier: string) => (project.value?.questions ?? []).filter((q) => q.tier === tier && open_(q))
const tierDone = (tier: string) => (project.value?.questions ?? []).filter((q) => q.tier === tier && !open_(q))
const tierCollapse = ref<string[]>(['business'])
const way = ref<'say' | 'file' | 'step'>('say')
const showAll = ref(false)
const skipped = reactive(new Set<string>())
const TIER_ORDER = ['business', 'company', 'system']
const TIER_NAME: Record<string, string> = { business: '业务信息', company: '企业信息', system: '评估与结论' }
const saving = reactive(new Set<string>())  // 已点保存、正在后台保存的项：立刻从队列里拿掉，不等服务端
let saveChain: Promise<unknown> = Promise.resolve()
// 正在集中补的那份文件：它缺的项排在最前；其余按层级，再按用到它的文件多少（一项补上、多份文件受益的先填）
const focusDoc = ref('')
const focusKeys = computed(() => new Set((project.value?.documents.find((d) => d.code === focusDoc.value)?.missing ?? []).map((m) => m.key)))
// 点去回答时要先答的题（出境路径、某份文件要不要靠它判断）：排在逐项填写的最前面
const askFirst = ref<string[]>([])
const askFor = ref('')  // 这几题用来判断什么（显示在逐项填写的题目上方）
const isNa = (code: string) => project.value?.applicability?.[code]?.state === 'na'
const isOnlyForNa = (q: Question) => q.used_in.length > 0 && q.used_in.every((c) => isNa(c))
const requiredDocs = computed(() => (project.value?.documents ?? []).filter((d) => !isNa(d.code)))
const naDocs = computed(() => (project.value?.documents ?? []).filter((d) => isNa(d.code)))
const queue = computed(() =>
  (project.value?.questions ?? [])
    .filter((q) => open_(q) && (!isOnlyForNa(q) || askFirst.value.includes(q.key) || focusKeys.value.has(q.key)) && !q.suggestion && !skipped.has(q.key) && !saving.has(q.key))
    .sort((a, b) =>
      Number(askFirst.value.includes(b.key)) - Number(askFirst.value.includes(a.key))
      || (askFirst.value.includes(a.key) ? askFirst.value.indexOf(a.key) - askFirst.value.indexOf(b.key) : 0)
      || Number(focusKeys.value.has(b.key)) - Number(focusKeys.value.has(a.key))
      || TIER_ORDER.indexOf(a.tier) - TIER_ORDER.indexOf(b.tier)
      || b.used_in.length - a.used_in.length),
)
// 离成稿最近的文件（缺得最少的），给用户一个最省事的下一步；按目前情况不需要的文件不推荐
const nearest = computed(() => {
  const open = requiredDocs.value.filter((d) => (d.state === 'not_started' || d.state === 'filling') && d.missing.length && !officialOnly(d))
  return open.sort((a, b) => a.missing.length - b.missing.length)[0] ?? null
})
// 各份文件的状态计数：只统计本次应出文件的四态；免出件文件单独说明
const stateCounts = computed(() => {
  const req = requiredDocs.value
  const n = (st: DocView['state']) => req.filter((d) => d.state === st).length
  return { not_started: n('not_started'), filling: n('filling'), paper: n('needs_paper'), ready: n('ready'), na: naDocs.value.length }
})
const advisorSummary = computed(() => {
  if (!project.value) return ''
  if (project.value.cn_route?.missing?.length) {
    return '出件范围说明：请先补充上方关键规模与数据类型信息，平台将自动判定法定出境路径并筛选本次必备报告清单，无需预先填满全部 8 份文件。'
  }
  if (naDocs.value.length > 0) {
    const naNames = naDocs.value.map((d) => `《${d.name}》`).join('、')
    return `出件范围说明：结合当前流向与法定路径判定，本次仅需编制并递交下方 ${requiredDocs.value.length} 份必备文件；${naNames}不适用当前路径，已收起归入下方无需单独准备的文件区，无需填写与生成。`
  }
  return `出件范围说明：结合当前跨境业务流向与合法性基础，建议完整编制下方 ${requiredDocs.value.length} 份合规文件以形成完整齐套的合规证明文件。`
})
const showMainRiskEditor = ref(false)
watch(showAll, (v) => { if (v) showMainRiskEditor.value = false })
const RISK_LEVELS = ['低', '中', '高']
const RISK_MATRIX_IDX = [[0, 0, 1], [0, 1, 2], [1, 2, 2]]
const RISK_NAMES = ['低风险', '中风险', '高风险']
interface ParsedRiskRow { id: string; risk: string; likelihood: string; severity: string; measures: string; mitigated: string; init_likelihood: string; init_severity: string }
const riskQuestion = computed(() => (project.value?.questions ?? []).find((q) => q.key === 'risk_rating') ?? null)
const parsedMainRisks = computed<ParsedRiskRow[]>(() => {
  const raw = (edits.risk_rating ?? riskQuestion.value?.value ?? '').trim()
  if (!raw || raw.startsWith('暂不确定') || !raw.startsWith('[')) return []
  try {
    const arr = JSON.parse(raw)
    if (!Array.isArray(arr)) return []
    return arr.map((r: Record<string, unknown>, i: number) => ({
      id: String(r?.id || `r${i + 1}`),
      risk: String(r?.risk || '').trim(),
      likelihood: RISK_LEVELS.includes(String(r?.likelihood || '')) ? String(r.likelihood) : '',
      severity: RISK_LEVELS.includes(String(r?.severity || '')) ? String(r.severity) : '',
      measures: String(r?.measures || '').trim(),
      mitigated: r?.mitigated === '1' || r?.mitigated === true || r?.mitigated === 'true' ? '1' : '',
      init_likelihood: RISK_LEVELS.includes(String(r?.init_likelihood || '')) ? String(r.init_likelihood) : '',
      init_severity: RISK_LEVELS.includes(String(r?.init_severity || '')) ? String(r.init_severity) : '',
    })).filter((r) => r.risk || r.likelihood || r.severity || r.measures)
  } catch {
    return []
  }
})
function riskRowLevel(l: string, s: string): string {
  if (!RISK_LEVELS.includes(l) || !RISK_LEVELS.includes(s)) return ''
  return RISK_NAMES[RISK_MATRIX_IDX[RISK_LEVELS.indexOf(l)][RISK_LEVELS.indexOf(s)]]
}
const mainRiskGrid = computed(() =>
  [2, 1, 0].map((i) => ({
    label: `可能性：${RISK_LEVELS[i]}`,
    cells: [0, 1, 2].map((j) => {
      const activeLabels: string[] = []
      const movedFrom: string[] = []
      parsedMainRisks.value.forEach((r, idx) => {
        const n = idx + 1
        const curHere = r.mitigated !== '1' && r.likelihood === RISK_LEVELS[i] && r.severity === RISK_LEVELS[j]
        const il = r.init_likelihood || r.likelihood
        const isv = r.init_severity || r.severity
        const initHere = il === RISK_LEVELS[i] && isv === RISK_LEVELS[j]
        const curLv = riskRowLevel(r.likelihood, r.severity)
        const initLv = riskRowLevel(il, isv) || curLv
        if (curHere) {
          activeLabels.push(initLv && curLv && initLv !== curLv ? `风险 ${n}（原${initLv.replace('风险', '')}）` : `风险 ${n}`)
        } else if (initHere && (r.mitigated === '1' || (curLv && (r.likelihood !== il || r.severity !== isv)))) {
          const rankMap: Record<string, number> = { 低风险: 0, 中风险: 1, 高风险: 2 }
          let suffix = '已调整'
          if (r.mitigated === '1') suffix = '已整改'
          else if ((rankMap[curLv] ?? 0) < (rankMap[initLv] ?? 0)) suffix = '已降级'
          else if ((rankMap[curLv] ?? 0) > (rankMap[initLv] ?? 0)) suffix = '已上调'
          movedFrom.push(`原风险 ${n}（${suffix}）`)
        }
      })
      return { name: RISK_NAMES[RISK_MATRIX_IDX[i][j]], tone: RISK_MATRIX_IDX[i][j], activeLabels, movedFrom }
    }),
  })),
)
function saveInlineRisk() {
  if (riskQuestion.value) saveInBackground(riskQuestion.value)
}

function applyDiagnosedProject(next: Project) {
  project.value = next
  const rq = (next.questions || []).find((x) => x.key === 'risk_rating')
  if (rq) edits.risk_rating = rq.value
}
// 出境路径还缺的回答：跳到含这一问的文件逐项补
function answerRoute() {
  const r = project.value?.cn_route
  const labels = new Set(r?.missing ?? [])
  answerKeys(r?.missing_keys?.length ? r.missing_keys : (project.value?.questions ?? []).filter((q) => labels.has(q.label)).map((q) => q.key), '出境路径')
}
// 判断某件事还缺的回答（出境路径、某份文件要不要）：跳到含这一问的文件逐项补
function answerKeys(keys: string[], what: string) {
  const doc = (project.value?.documents ?? []).find((d) => d.missing.some((m) => keys.includes(m.key)))
  if (doc) { fillDoc(doc.code); askFirst.value = keys; askFor.value = what; return }
  // 缺的是已经答过、还要补一句的题（如选了是还没写人数）：打开全部信息，定位到这一题
  const q = (project.value?.questions ?? []).find((x) => keys.includes(x.key))
  if (q) gotoQuestion(q.key, true)
}
// 打开全部信息、展开那一段、滚到那道题并把光标放进去；流向、车型、公司名这类项目信息在修改流向与业务里
function gotoQuestion(key: string, other = false) {
  const q = (project.value?.questions ?? []).find((x) => x.key === key)
  if (!q) {
    editingRoute.value = true
    window.setTimeout(() => document.querySelector('[data-testid="doc-edit-route"]')?.scrollIntoView({ behavior: 'smooth', block: 'center' }), 120)
    return
  }
  showAll.value = true
  if (!tierCollapse.value.includes(q.tier)) tierCollapse.value.push(q.tier)
  if (other) otherOpen.add(q.key)
  window.setTimeout(() => {
    const el = document.querySelector<HTMLElement>(`[data-testid="q-${q.key}"]`)
    if (!el) return
    el.scrollIntoView({ behavior: 'smooth', block: 'center' })
    const box = el.matches('input, textarea') ? el : el.querySelector<HTMLElement>('input, textarea')
    box?.focus({ preventScroll: true })
  }, 360)
}
// 每段缺几项：与成稿缺项清单同一套（只算要的文件缺的）；只有按目前情况不需要的文件用到的题不计，单独标出
// 段内顺序：要填的缺项在前，只有不需要的文件用到的空题其次，已填的最后
const tierList = (tier: string) => [...tierOpen(tier).filter((q) => blockingKeys.value.has(q.key)), ...tierOpen(tier).filter((q) => !blockingKeys.value.has(q.key)), ...tierDone(tier)]
const tierMissing = (tier: string) => (project.value?.final.missing ?? []).filter((m) => m.tier === tier)
const blockingKeys = computed(() => new Set((project.value?.final.missing ?? []).map((m) => m.key)))
// 只有按目前情况不需要的文件用到、还空着的题：写明仅某文件用到，按目前情况不需要
function onlyNa(q: Question): string {
  if (!open_(q) || blockingKeys.value.has(q.key) || !q.used_in.length || !q.used_in.every((c) => isNa(c))) return ''
  return `仅${q.used_in.map((c) => `《${docName(c)}》`).join('')}用到，按目前情况不需要`
}
const TIER_LABEL: Record<string, string> = { business: '业务信息', company: '企业信息', system: '评估与结论', project: '项目信息' }
const finalGroups = computed(() => {
  const ms = project.value?.final.missing ?? []
  return ['project', 'business', 'company', 'system'].map((t) => ({ tier: t, title: TIER_LABEL[t], items: ms.filter((m) => m.tier === t) })).filter((g) => g.items.length)
})
const showGate = ref(false)
// 平台检查结果：题旁只在框里还是服务端算过的那个值时显示（改了、还没保存的，保存后重新算）
const LEVEL_TEXT: Record<Issue['level'], string> = { must: '必须', advise: '建议' }
const issuesOf = (q: Question) => ((edits[q.key] || '') === q.value ? q.issues ?? [] : [])
const densityOf = (q: Question): DensityHint | null => {
  const cur = (edits[q.key] ?? '').trim()
  if (!cur || cur.startsWith('暂不确定')) return null
  if (cur === (q.value || '').trim()) return q.density_hint ?? null
  if (cur.length >= 42) return null
  return q.density_hint ?? null
}
const thinQuestions = computed(() => (project.value?.questions ?? []).filter((q) => !isOnlyForNa(q) && !!q.density_hint))
function applyDensityDraft(q: Question) {
  const dh = densityOf(q) || q.density_hint
  if (!dh?.draft_template) return
  edits[q.key] = dh.draft_template
  ElMessage.info('已填入细化草稿，请将【】内的提示修改为本企业实际情况后保存')
}
const showIssues = ref(false)
function openIssue(i: Issue) {
  if (i.to.kind === 'form') { void router.push({ name: 'form', params: { id: i.to.instance_id }, query: { item: i.to.item } }); return }
  if (i.to.kind === 'route') { gotoQuestion('__route__'); return }
  gotoQuestion(i.to.key)
}
// 成稿旁前后不一致提示里点一处：跳到挂着它的那一题（企业名称、车型这类在项目信息里）
function gotoIssue(i: Issue) {
  gotoQuestion((project.value?.questions ?? []).some((q) => q.key === i.key) ? i.key : '__route__')
}
const ROUTE_LABEL: Record<string, string> = { company_name: '企业名称', vehicle_model: '车型或车队', source_country: '数据发出国', recipient_country: '数据接收国' }
const labelOf = (key: string) => (project.value?.questions ?? []).find((q) => q.key === key)?.label || ROUTE_LABEL[key] || key
const moreEl = ref<HTMLElement | null>(null)
// 第 7.5 项二轮：每行只留一行短状态，长理由收进为什么；等出境路径的，前提只在顶部框里说一次
const routeUndecided = computed(() => !!project.value?.cn_route?.missing?.length)
function appliesShort(code: string): string {
  const a = project.value?.applicability?.[code]
  if (!a) return ''
  if (a.state === 'na') return '按目前情况不需要'
  if (a.state === 'need') return '需要'
  const routeKeys = new Set(project.value?.cn_route?.missing_keys ?? [])
  if (routeUndecided.value && a.missing_keys.length && a.missing_keys.every((k) => routeKeys.has(k))) return '等出境路径'
  return '待判断'
}
// 页头唯一的主操作：继续填写（还缺 N 项）
function continueFill() {
  askFirst.value = []; focusDoc.value = ''; way.value = 'step'; skipped.clear()
  window.setTimeout(() => moreEl.value?.scrollIntoView({ behavior: 'smooth', block: 'start' }), 50)
}
// 助手展开时左侧项目列表自动收起，助手关上再恢复；助手宽度不让中间区窄于 640px
const wrapEl = ref<HTMLElement | null>(null)
const wrapW = ref(0)
let wrapObs: ResizeObserver | null = null
const railBeforeChat = ref<boolean | null>(null)
function onChatOpen(v: boolean) {
  if (window.innerWidth <= 900) return
  if (v) { if (railBeforeChat.value === null) railBeforeChat.value = railCollapsed.value; railCollapsed.value = true }
  else if (railBeforeChat.value !== null) { railCollapsed.value = railBeforeChat.value; railBeforeChat.value = null }
}
const chatMax = computed(() => Math.max(320, wrapW.value - (railCollapsed.value ? 48 : 264) - 560))
onMounted(() => { wrapObs = new ResizeObserver((e) => { wrapW.value = e[0].contentRect.width }); if (wrapEl.value) wrapObs.observe(wrapEl.value) })
onUnmounted(() => wrapObs?.disconnect())
function fillDoc(code: string) {
  askFirst.value = []
  focusDoc.value = code
  way.value = 'step'
  skipped.clear()
  window.setTimeout(() => moreEl.value?.scrollIntoView({ behavior: 'smooth', block: 'start' }), 50)
}
// 去回答的题答完就停：结论按回答重新判断，不接着把人带去填这份文件（它可能已判为不需要）
watch(queue, (q) => {
  if (!askFirst.value.length || q.some((x) => askFirst.value.includes(x.key))) return
  ElMessage.success(`已按你的回答重新判断${askFor.value}`)
  askFirst.value = []; askFor.value = ''; focusDoc.value = ''
  window.setTimeout(() => document.querySelector('[data-testid="doc-list"]')?.scrollIntoView({ behavior: 'smooth', block: 'start' }), 600)
})
watch(focusKeys, (keys) => { if (focusDoc.value && !keys.size) { ElMessage.success(`《${docName(focusDoc.value)}》需要的信息已补齐`); focusDoc.value = '' } })
const current = computed(() => queue.value[0] ?? null)
const totalCount = computed(() => (project.value?.questions ?? []).filter((q) => !q.optional && !isOnlyForNa(q)).length)
const doneCount = computed(() => (project.value?.questions ?? []).filter((q) => !q.optional && !isOnlyForNa(q) && !open_(q) && !(q.value || '').startsWith('暂不确定')).length)
const percent = computed(() => (totalCount.value ? Math.round((doneCount.value / totalCount.value) * 100) : 0))
function saveInBackground(q: Question) {
  saving.add(q.key)
  saveChain = saveChain.then(() => saveAnswers([q.key])).finally(() => saving.delete(q.key))  // 按点击顺序一项项保存，避免后到的旧结果盖掉新的
}
function stepSave(q: Question) {
  if (!(edits[q.key] || '').trim()) { ElMessage.warning('请先填写，或选择跳过'); return }
  if (q.kind === 'risks') {  // 还缺档位或措施时留在这一题，并说清还缺什么
    void saveAnswers([q.key]).then(() => {
      const now = project.value?.questions.find((x) => x.key === q.key)
      if (now?.lacking?.length) ElMessage.warning(`已保存。还缺：${now.lacking.join('；')}`)
    })
    return
  }
  saveInBackground(q)
}
function stepSkip(q: Question) { skipped.add(q.key) }
// 把各项风险的措施生成整改跟踪报告的条目：已有内容保留，新条目追加在后面并标出来源
async function remediate(q: Question) {
  if (!project.value) return
  await saveChain  // 先等风险表还没存完的修改存好
  if (changed.value.includes(q.key)) await saveAnswers([q.key])
  busy.value = 'remediate'
  try {
    const n = project.value.questions.find((x) => x.key === q.key)?.remediate?.pending ?? 0
    const next = await api.post<Project>(`/api/analysis/doc-projects/${project.value.id}/risks/remediate`, {})
    project.value = next
    for (const k of ['issues', 'measures']) edits[k] = next.questions.find((x) => x.key === k)?.value ?? ''
    ElMessage.success(`已生成 ${n} 条整改项，追加到《合规问题整改跟踪报告》的问题清单和整改措施`)
  } catch (e) { ElMessage.error((e as Error).message) } finally { busy.value = '' }
}
function stepUnsure(q: Question) { edits[q.key] = '暂不确定（待补充）'; saveInBackground(q) }
const otherOpen = reactive(new Set<string>())
const isUnsure = (v: string) => (v || '').startsWith('暂不确定')
function choiceOf(q: Question) {
  const v = edits[q.key] || ''
  if (q.choices.includes(v)) return v
  for (const c of q.choices) {
    if (c !== '其他' && v.startsWith(c) && /^[，,（(：:；;\s]/.test(v.slice(c.length))) return c
  }
  return q.other && (otherOpen.has(q.key) || (v && !isUnsure(v))) ? '其他' : ''
}
// 选中后要再写一句：选其他写明内容，或选 detail_on（如是）后写明人数
const needsText = (q: Question) => (!!q.other && choiceOf(q) === '其他') || (!!q.detail_on && choiceOf(q) === q.detail_on)
function detailOf(q: Question) {
  const v = edits[q.key] || ''
  const p = q.detail_on || ''
  if (!p || !v.startsWith(p)) return ''
  const rest = v.slice(p.length).trim()
  if (rest.startsWith('，') || rest.startsWith(',')) return rest.slice(1).trim()
  if ((rest.startsWith('（') && rest.endsWith('）')) || (rest.startsWith('(') && rest.endsWith(')'))) return rest.slice(1, -1).trim()
  return rest.replace(/^[：:；;\s]+/, '')
}
function setDetail(q: Question, v: string) { edits[q.key] = v.trim() ? `${q.detail_on}，${v}` : q.detail_on || '' }
function pickChoice(q: Question, c: string, wizard = false) {
  if (q.other && c === '其他') { otherOpen.add(q.key); edits[q.key] = ''; return }
  otherOpen.delete(q.key)
  edits[q.key] = c
  if (q.detail_on && c === q.detail_on) {
    otherOpen.add(q.key)
    if (!wizard) void saveAnswers([q.key])  // 逐项填写时等写明后点保存下一项再存
    return
  }
  if (wizard) saveInBackground(q)
  else void saveAnswers([q.key])
}
// 助手给出的文字：放进这一题并保存（选择题直接选上）
// 点已从……带入：回到来源去改。另一个项目 → 打开那个项目的这一题；本项目的流向与业务 → 打开修改流向与业务；
// 你提供的业务情况 → 展开原话，光标放进这一题
const quoteOpen = reactive(new Set<string>())
function openCarried(key: string, c: Carried) {
  if (c.kind === 'risk') { gotoQuestion('risk_rating'); return }
  if (c.kind === 'project' && c.project_id) { void router.push({ path: '/documents', query: { project: c.project_id, q: key } }); return }
  if (c.kind === 'route' || key === 'company_name') { gotoQuestion('__route__'); return }
  if (quoteOpen.has(key)) quoteOpen.delete(key); else quoteOpen.add(key)
  gotoQuestion(key)
}
// 全部信息里点到或光标落在哪题，就显示哪题的依据卡片
const activeKey = ref('')
// 光标进了文字框就算选中；按钮、单选要等点完再换卡片（按下时换会让版面移动，这一下点击就落空了）
function focusQuestion(e: FocusEvent, key: string) {
  if ((e.target as HTMLElement | null)?.matches('input[type=text], input:not([type]), textarea')) activeKey.value = key
}
function adoptFromAssistant(q: Question, value: string) {
  if (q.kind === 'risks') {  // 助手给的文字作为新的一项风险，档位和措施由用户自己选
    let rows: Record<string, string>[] = []
    try { const v = JSON.parse(edits[q.key] || '[]'); if (Array.isArray(v)) rows = v } catch { rows = edits[q.key] ? [{ id: 'r1', risk: edits[q.key] }] : [] }
    rows.push({ id: Math.random().toString(16).slice(2, 10), risk: value })
    edits[q.key] = JSON.stringify(rows); void saveAnswers([q.key])
    ElMessage.success('已作为新的一项风险加入，请选可能性、严重程度并写应对措施'); return
  }
  if (q.choices.length && !q.choices.includes(value)) { edits[q.key] = value; void saveAnswers([q.key]); return }
  edits[q.key] = value
  if (q.choices.length) { saveInBackground(q); return }
  void saveAnswers([q.key])
  ElMessage.success('已填入，可以继续修改')
}
function undoFromAssistant(q: Question, previousValue: string) {
  edits[q.key] = previousValue
  void saveAnswers([q.key])
  ElMessage.info('已撤回这一项的填入内容')
}
const chatRef = ref<InstanceType<typeof ProjectChat> | null>(null)
function openCopilot() {
  chatRef.value?.openPanel()
}
function highlightFilledQuestions(keys: string[]) {
  if (!keys.length || !project.value) return
  const qFirst = project.value.questions.find((x) => keys.includes(x.key))
  if (qFirst) {
    showAll.value = true
    for (const k of keys) {
      const q = project.value.questions.find((x) => x.key === k)
      if (q && !tierCollapse.value.includes(q.tier)) tierCollapse.value.push(q.tier)
    }
    window.setTimeout(() => {
      const firstWrap = document.querySelector<HTMLElement>(`[data-testid="q-wrap-${qFirst.key}"]`)
      firstWrap?.scrollIntoView({ behavior: 'smooth', block: 'center' })
      for (const k of keys) {
        const el = document.querySelector<HTMLElement>(`[data-testid="q-wrap-${k}"]`)
        el?.classList.add('flash')
        window.setTimeout(() => el?.classList.remove('flash'), 2400)
      }
    }, 220)
  } else if (keys.some((k) => k === 'company_name' || k === 'vehicle_model')) {
    editingRoute.value = true
    window.setTimeout(() => {
      document.querySelector('[data-testid="doc-edit-route"]')?.scrollIntoView({ behavior: 'smooth', block: 'center' })
    }, 140)
  }
}
async function applyCopilotProposals(items: CopilotProposal[]) {
  if (!project.value || !items.length) return
  const routePatch: Record<string, string> = {}
  const answerPatch: Record<string, string> = {}
  for (const it of items) {
    const val = Array.isArray(it.value) ? it.value.join('、') : String(it.value ?? '')
    if (it.target === 'route' || it.key === 'company_name' || it.key === 'vehicle_model') routePatch[it.key] = val
    else answerPatch[it.key] = val
  }
  if (Object.keys(routePatch).length) {
    project.value = await api.patch<Project>(`/api/analysis/doc-projects/${project.value.id}`, routePatch)
    await loadList()
  }
  if (Object.keys(answerPatch).length) {
    const next = await api.put<Project>(`/api/analysis/doc-projects/${project.value.id}/answers`, { answers: answerPatch })
    project.value = next
    for (const [k, v] of Object.entries(answerPatch)) edits[k] = v
  }
  const hitPending = items.map((x) => x.key).filter((k) => pending.value.some((p) => p.key === k))
  if (hitPending.length && project.value) {
    project.value = await api.post<Project>(`/api/analysis/doc-projects/${project.value.id}/suggestions/accept`, { keys: hitPending, dismiss: true })
  }
  for (const q of project.value.questions) edits[q.key] = q.value
  highlightFilledQuestions(items.map((x) => x.key))
  ElMessage.success(`已联动填入 ${items.length} 项信息，文件完整度已同步更新`)
}
async function undoCopilotProposals(items: CopilotProposal[]) {
  if (!project.value || !items.length) return
  const routePatch: Record<string, string> = {}
  const answerPatch: Record<string, string> = {}
  for (const it of items) {
    const prev = Array.isArray(it.previous_value) ? it.previous_value.join('、') : String(it.previous_value ?? '')
    if (it.target === 'route' || it.key === 'company_name' || it.key === 'vehicle_model') routePatch[it.key] = prev
    else answerPatch[it.key] = prev
  }
  if (Object.keys(routePatch).length) {
    project.value = await api.patch<Project>(`/api/analysis/doc-projects/${project.value.id}`, routePatch)
    await loadList()
  }
  if (Object.keys(answerPatch).length) {
    project.value = await api.put<Project>(`/api/analysis/doc-projects/${project.value.id}/answers`, { answers: answerPatch })
  }
  for (const q of project.value.questions) edits[q.key] = q.value
  ElMessage.info(`已撤回刚才填入的 ${items.length} 项内容`)
}
const TIERS = [
  { key: 'business', title: '业务信息', hint: '' },
  { key: 'company', title: '企业信息（填写一次，新项目从上一个项目带入）', hint: '' },
  { key: 'system', title: '评估与结论', hint: '风险判断、问题与整改、评估结论属于企业自身的评估意见，需由企业填写；暂不填写的，文件中标注为【待补充】。' },
] as const

const basisNote = computed(() => {
  const r = project.value?.route
  if (!r) return ''
  const have = (r.library_country || '').split('、').filter(Boolean)
  const missing = [r.source_country, r.recipient_country].filter((c) => c && !have.some((h) => c.includes(h) || h.includes(c)))
  if (!missing.length) return ''
  return have.length ? `${missing.join('、')}的法规尚未收录，依据取自${have.join('、')}的法规` : `${missing.join('、')}的法规还没有收录，文件按各国通行的要求编写`
})
// 标题里已有流向和业务；这里只补标题里没有的：车型或车队、企业名称
const routeNote = computed(() => {
  const r = project.value?.route
  return r ? [r.vehicle_model, r.company_name].filter((x) => x && !project.value!.title.includes(x)).join(' · ') : ''
})
const allReady = computed(() => !!project.value?.final.ok)

// ---------- 极简双项目并排对比弹窗（纯读已有概览接口，不改原项目数据） ----------
const compareVisible = ref(false)
const compareLoading = ref(false)
const compareLeftId = ref('')
const compareRightId = ref('')
const compareLeft = ref<Project | null>(null)
const compareRight = ref<Project | null>(null)

async function fetchProjectForCompare(id: string): Promise<Project | null> {
  if (!id) return null
  if (project.value?.id === id) return project.value
  return await api.get<Project>(`/api/analysis/doc-projects/${id}`)
}

async function loadComparePair() {
  if (!compareLeftId.value && !compareRightId.value) return
  compareLoading.value = true
  try {
    const [left, right] = await Promise.all([
      fetchProjectForCompare(compareLeftId.value),
      fetchProjectForCompare(compareRightId.value),
    ])
    compareLeft.value = left
    compareRight.value = right
  } catch (e) {
    ElMessage.error((e as Error).message || '加载项目对比数据失败')
  } finally {
    compareLoading.value = false
  }
}

function openCompare() {
  const pool = items.value
  if (pool.length < 2) return
  const curId = openId.value && pool.some((x) => x.id === openId.value) ? openId.value : pool[0].id
  const curItem = pool.find((x) => x.id === curId) || pool[0]
  const baseTitle = curItem.title.replace(/（副本）$/, '')
  const twin = pool.find((x) => x.id !== curId && (x.title === `${baseTitle}（副本）` || x.title === baseTitle))
  const other = twin || pool.find((x) => x.id !== curId) || pool[1]
  compareLeftId.value = curId
  compareRightId.value = other.id
  compareVisible.value = true
  void loadComparePair()
}

function swapCompare() {
  const tmpId = compareLeftId.value
  compareLeftId.value = compareRightId.value
  compareRightId.value = tmpId
  const tmpData = compareLeft.value
  compareLeft.value = compareRight.value
  compareRight.value = tmpData
}

function cmpLabelOf(key: string | undefined, list: { key: string; label: string }[]): string {
  const k = (key || '').trim()
  if (!k) return ''
  const hit = list.find((x) => x.key === k || x.label === k)
  if (hit) return hit.label
  return k
}

function cmpRouteLine(p: Project | null): string {
  if (!p) return '—'
  const r = p.route
  const fnText = (r.functions?.length ? r.functions : [r.function]).map((k) => cmpLabelOf(k, options.value.functions)).filter(Boolean).join('、')
  const scText = (r.business_scenes?.length ? r.business_scenes : [r.business_scene]).map((k) => cmpLabelOf(k, options.value.scenes)).filter(Boolean).join('、')
  const sceneText = [fnText, scText].filter(Boolean).join(' / ')
  return [
    `${r.source_country || '未填'} → ${r.recipient_country || '未填'}`,
    r.vehicle_model,
    sceneText,
  ].filter(Boolean).join(' · ')
}

function cmpStatutoryRoute(p: Project | null): string {
  if (!p) return '—'
  return p.project_verdict?.route_text || p.cn_route?.text || '依据双方现行法规开展评估'
}

function cmpRiskSummary(p: Project | null): string {
  if (!p) return '—'
  const evo = p.risk_evolution
  if (!evo || !evo.total) return '尚未识别风险项'
  const c = evo.current_counts || {}
  const init = evo.init_counts || {}
  const base = `当前 高 ${c['高风险'] || 0} · 中 ${c['中风险'] || 0} · 低 ${c['低风险'] || 0}`
  const initLine = `初评 高 ${init['高风险'] || 0} · 中 ${init['中风险'] || 0} · 低 ${init['低风险'] || 0}`
  const changes = [
    evo.downgraded_count ? `已降级 ${evo.downgraded_count} 项` : '',
    evo.upgraded_count ? `已上调 ${evo.upgraded_count} 项` : '',
    evo.excluded_count ? `已整改 ${evo.excluded_count} 项` : '',
  ].filter(Boolean).join('，')
  return changes ? `${base}（${initLine}；${changes}）` : `${base}（${initLine}）`
}

function cmpRequiredDocs(p: Project | null): DocView[] {
  if (!p) return []
  return p.documents.filter((d) => p.applicability?.[d.code]?.state !== 'na')
}

function cmpAdmissionSummary(p: Project | null): { label: string; tone: string; detail: string } {
  if (!p) return { label: '—', tone: '', detail: '' }
  const ap = p.applicability?.admission
  if (ap?.state === 'na') return { label: '无需办理', tone: '', detail: '不涉及海外属地准入办理' }
  const doc = p.documents.find((d) => d.code === 'admission')
  if (doc?.verdict) {
    const detail = (doc.verdict.text || '').trim() || (ap?.why || '').trim()
    return { label: doc.verdict.label, tone: doc.verdict.tone || '', detail }
  }
  return { label: '待核定', tone: '', detail: (ap?.why || '').trim() || '需按目标国法规履行属地准入' }
}

const compareDocDiff = computed(() => {
  const aDocs = cmpRequiredDocs(compareLeft.value)
  const bDocs = cmpRequiredDocs(compareRight.value)
  const aMap = new Map(aDocs.map((d) => [d.code, d]))
  const bMap = new Map(bDocs.map((d) => [d.code, d]))
  const onlyA = aDocs.filter((d) => !bMap.has(d.code)).map((d) => d.name)
  const onlyB = bDocs.filter((d) => !aMap.has(d.code)).map((d) => d.name)
  const unionCodes: string[] = []
  for (const d of [...aDocs, ...bDocs]) {
    if (!unionCodes.includes(d.code)) unionCodes.push(d.code)
  }
  const rows = unionCodes.map((code) => {
    const da = aMap.get(code) || null
    const db = bMap.get(code) || null
    const labelA = da ? (da.verdict?.label || (da.state === 'ready' ? '已就绪' : '待完善')) : '无需办理'
    const labelB = db ? (db.verdict?.label || (db.state === 'ready' ? '已就绪' : '待完善')) : '无需办理'
    return { code, name: da?.name || db?.name || code, labelA, labelB, toneA: da?.verdict?.tone || '', toneB: db?.verdict?.tone || '', diff: labelA !== labelB }
  })
  return { onlyA, onlyB, rows }
})

function jumpFromCompare(id: string) {
  if (!id) return
  compareVisible.value = false
  void router.push({ query: { project: id } })
}
</script>

<template>
  <div class="docs-page">
    <AssessmentSwitch />
    <div ref="wrapEl" class="docs-wrap">
      <aside class="side" :class="{ collapsed: railCollapsed }">
        <button v-if="railCollapsed" type="button" class="rail-open" title="展开项目列表" aria-label="展开项目列表" data-testid="doc-rail-open" @click="railCollapsed = false">
          <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M6 3l5 5-5 5" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" /></svg>
          <span>合规文件项目</span>
        </button>
        <template v-else>
        <div class="side-top">
          <strong>合规文件项目</strong>
          <span class="side-top-actions">
            <el-button v-if="editable" size="small" data-testid="doc-new" @click="router.push({ query: {} })">+ 新建</el-button>
            <button v-if="!isNew" type="button" class="cy-rail-toggle" title="收起项目列表" aria-label="收起项目列表" data-testid="doc-rail-close" @click="railCollapsed = true">
              <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><path d="M10 3L5 8l5 5" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" /></svg>
            </button>
          </span>
        </div>
        <div class="archive-bar">
          <button type="button" class="archive-toggle" data-testid="doc-archive-toggle" @click="showArchive = !showArchive">{{ showArchive ? '← 返回项目' : `已归档（${archivedItems.length}）` }}</button>
          <span class="batch-bar">
            <button v-if="!showArchive && items.length >= 2" type="button" class="batch-link cmp-trigger" data-testid="doc-compare-open" @click="openCompare">方案对比</button>
            <button v-if="editable && !showArchive && items.length" type="button" class="batch-link" :disabled="busy === 'batch-archive'" data-testid="doc-batch-archive" @click="batchArchive">一键归档</button>
            <template v-else-if="editable && showArchive && archivedItems.length">
              <button type="button" class="batch-link" :disabled="busy === 'batch-restore'" data-testid="doc-batch-restore" @click="batchRestore">一键恢复</button>
              <button type="button" class="batch-link danger" :disabled="busy === 'batch-delete'" data-testid="doc-batch-delete" @click="batchDelete">一键清空</button>
            </template>
          </span>
        </div>
        <div v-if="showArchive" class="list" data-testid="doc-archive-list">
          <p v-if="!archivedItems.length" class="muted pad">暂无已归档项目。</p>
          <div v-for="it in archivedItems" :key="it.id" class="item" :class="{ on: openId === it.id }" @click="router.push({ query: { project: it.id } })">
            <div class="t">{{ it.title }}</div>
            <div v-if="editable" class="actions"><el-button size="small" @click.stop="restore(it)">恢复</el-button><el-button size="small" plain type="danger" @click.stop="forGood(it)">彻底删除</el-button></div>
          </div>
        </div>
        <div v-else class="list">
          <p v-if="!listLoaded" class="muted pad">正在加载项目列表…</p>
          <p v-else-if="!items.length" class="muted pad">{{ editable ? '暂无项目，点击上方「+ 新建」即可按数据流向与业务场景创建合规文件项目。' : '当前单位暂无已创建的合规评估项目。' }}</p>
          <div v-for="it in items" :key="it.id" class="item" :class="{ on: openId === it.id }" data-testid="doc-item" @click="router.push({ query: { project: it.id } })">
            <div class="t">{{ it.title }}</div>
            <div class="sub"><span>{{ it.updated_at ? `${localTime(it.updated_at)} 更新` : '' }}</span><span v-if="editable" class="tools"><button type="button" data-testid="doc-clone-item" @click.stop="cloneProject(it)">复制</button><button type="button" @click.stop="rename(it)">重命名</button><button type="button" data-testid="doc-delete" @click.stop="archive(it)">归档</button></span></div>
          </div>
        </div>
        </template>
      </aside>

      <div v-loading="loading" class="main">
        <!-- 开始 -->
        <section v-if="isNew && editable" class="start" data-testid="doc-start">
          <h2>生成合规文件</h2>
          <el-form label-position="top" class="start-form">
            <div class="row2">
              <el-form-item label="数据发出国">
                <el-select v-model="startForm.source" filterable allow-create default-first-option placeholder="请选择，或直接输入国家名称" data-testid="doc-source">
                  <el-option v-for="c in options.countries" :key="c" :label="c" :value="c" />
                </el-select>
              </el-form-item>
              <el-form-item label="数据接收国">
                <el-select v-model="startForm.recipient" filterable allow-create default-first-option placeholder="请选择，或直接输入国家名称" data-testid="doc-recipient">
                  <el-option v-for="c in options.countries" :key="c" :label="c" :value="c" />
                </el-select>
              </el-form-item>
            </div>
            <div class="row2">
              <el-form-item label="车辆数据主要用途（可多选，支持直接输入添加自定义场景）">
                <el-select v-model="startForm.functions" multiple filterable allow-create default-first-option clearable placeholder="请选择一个或多个预设场景，或输入新场景回车添加" data-testid="doc-function">
                  <el-option v-for="f in options.functions" :key="f.key" :label="f.label" :value="f.key" />
                </el-select>
              </el-form-item>
              <el-form-item label="业务阶段（可多选，支持直接输入添加）">
                <el-select v-model="startForm.scenes" multiple filterable allow-create default-first-option clearable placeholder="请选择业务阶段，或输入自定义阶段回车添加" data-testid="doc-scene">
                  <el-option v-for="s in options.scenes" :key="s.key" :label="s.label" :value="s.key" />
                </el-select>
              </el-form-item>
            </div>
            <el-form-item label="其他用途说明（已选择上述用途的可不填）"><el-input v-model="startForm.text" maxlength="200" placeholder="例如：车载摄像头数据回传总部，用于算法训练" /></el-form-item>
            <div class="row2">
              <el-form-item label="车型或车队（选填）"><el-input v-model="startForm.vehicle" maxlength="128" placeholder="例如：某品牌 L4 自动驾驶 SUV" /></el-form-item>
              <el-form-item label="企业名称（选填）"><el-input v-model="startForm.company" maxlength="120" /></el-form-item>
            </div>
            <el-button v-if="editable" type="primary" size="large" :loading="busy === 'create'" data-testid="doc-create" @click="create">生成合规文件</el-button>
          </el-form>
        </section>

        <section v-else-if="isNew && !editable" class="start viewer-empty-box" data-testid="doc-viewer-empty">
          <h2>合规评估项目</h2>
          <p class="lead">{{ !listLoaded ? '正在加载合规评估项目…' : items.length ? '请在左侧选择任一合规评估项目查看详情。' : '当前单位暂无已创建的合规评估项目。您可以前往法规知识库查阅各国法规条文与国家合规简报，或使用咨询助手提问查法。' }}</p>
        </section>

        <!-- 项目 -->
        <template v-else-if="project">
          <div v-if="!editable && !project.archived" class="archived-banner" data-testid="viewer-banner">只读账号：可以查看这个项目{{ downloadable ? '和下载文件' : '' }}，不能修改。</div>
          <div v-if="project.archived" class="archived-banner">该项目在已归档列表中，只能查看。<el-button v-if="editable" size="small" type="primary" plain @click="restore(project)">恢复后继续</el-button></div>
          <header class="head">
            <h2>{{ project.title }}</h2>
            <div class="head-row">
              <p class="route" data-testid="doc-route">
                <span v-if="routeNote" class="route-note">{{ routeNote }}</span>
                <el-button v-if="editable" link type="primary" size="small" :disabled="locked" @click="editingRoute = !editingRoute">{{ editingRoute ? '收起' : '修改流向与业务' }}</el-button>
              </p>
              <div class="head-actions">
              <el-button v-if="editable && !locked" data-testid="doc-open-copilot" @click="openCopilot">辅助填写</el-button>
              <el-button v-if="editable && !locked && !allReady && queue.length" type="primary" data-testid="doc-continue" @click="continueFill">继续填写</el-button>
              <el-select
                v-if="downloadable"
                v-model="hostLang"
                style="width: 176px"
                aria-label="属地报告输出语种"
                data-testid="doc-host-lang-select"
              >
                <el-option
                  v-for="lg in HOST_REPORT_LANGUAGES"
                  :key="lg.code"
                  :label="`属地语种：${lg.nameZh}`"
                  :value="lg.code"
                />
              </el-select>
              <el-dropdown v-if="downloadable" trigger="click" placement="bottom-end" @command="(c: string) => { const [kind, f, lg] = c.split(':'); void downloadAll(kind === 'draft', f as 'docx' | 'pdf' | 'xlsx', (lg as ReportOutputLang) || 'zh') }">
                <el-button :type="allReady || !editable ? 'primary' : 'default'" :loading="busy === 'zip'" data-testid="doc-zip-menu">下载</el-button>
                <template #dropdown><el-dropdown-menu>
                  <el-dropdown-item command="draft:docx:zh" data-testid="doc-zip-draft">全部初稿 · 中文 Word（ZIP）</el-dropdown-item>
                  <el-dropdown-item command="draft:pdf:zh">全部初稿 · 中文 PDF（ZIP）</el-dropdown-item>
                  <el-dropdown-item command="draft:xlsx:zh" data-testid="doc-zip-draft-xlsx">全部初稿 · 中文 Excel（ZIP）</el-dropdown-item>
                  <el-dropdown-item command="draft:docx:en" data-testid="doc-zip-draft-en">全部初稿 · 英文 Word（ZIP）</el-dropdown-item>
                  <el-dropdown-item command="draft:pdf:en">全部初稿 · 英文 PDF（ZIP）</el-dropdown-item>
                  <el-dropdown-item :command="`draft:docx:${hostLang}`" data-testid="doc-zip-draft-i18n">全部初稿 · {{ hostLangMeta.nameZh }} Word（ZIP）</el-dropdown-item>
                  <el-dropdown-item :command="`draft:pdf:${hostLang}`">全部初稿 · {{ hostLangMeta.nameZh }} PDF（ZIP）</el-dropdown-item>
                  <el-dropdown-item command="final:docx:zh" :disabled="!allReady" divided data-testid="doc-zip-final">全部成稿 · 中文 Word（ZIP）</el-dropdown-item>
                  <el-dropdown-item command="final:pdf:zh" :disabled="!allReady">全部成稿 · 中文 PDF（ZIP）</el-dropdown-item>
                  <el-dropdown-item command="final:xlsx:zh" :disabled="!allReady" data-testid="doc-zip-final-xlsx">全部成稿 · 中文 Excel（ZIP）</el-dropdown-item>
                  <el-dropdown-item command="final:docx:en" :disabled="!allReady" data-testid="doc-zip-final-en">全部成稿 · 英文 Word（ZIP）</el-dropdown-item>
                  <el-dropdown-item command="final:pdf:en" :disabled="!allReady">全部成稿 · 英文 PDF（ZIP）</el-dropdown-item>
                  <el-dropdown-item :command="`final:docx:${hostLang}`" :disabled="!allReady" data-testid="doc-zip-final-i18n">全部成稿 · {{ hostLangMeta.nameZh }} Word（ZIP）</el-dropdown-item>
                  <el-dropdown-item :command="`final:pdf:${hostLang}`" :disabled="!allReady">全部成稿 · {{ hostLangMeta.nameZh }} PDF（ZIP）</el-dropdown-item>
                </el-dropdown-menu></template>
              </el-dropdown>
              <el-tooltip v-else content="当前只读账号未开通导出权限" placement="top">
                <span><el-button disabled data-testid="doc-zip-menu">下载</el-button></span>
              </el-tooltip>
              </div>
            </div>
            <div class="final-gate" data-testid="doc-final-gate">
              <template v-if="!project.final.ok">
                <span>下载成稿还缺：<template v-if="project.final.missing.length">{{ project.final.missing.length }} 项信息</template><template v-if="project.final.forms.length">{{ project.final.missing.length ? '、' : '' }}法定申报表 {{ project.final.forms.reduce((a, f) => a + f.missing, 0) }} 项</template><template v-if="project.final.papers.length">{{ project.final.missing.length || project.final.forms.length ? '、' : '' }}{{ project.final.papers.length }} 份待生成论述</template></span>
                <button type="button" class="inline-link" data-testid="doc-final-toggle" @click="showGate = !showGate">{{ showGate ? '收起' : '查看待补项' }}</button>
              </template>
              <span v-else>本次应出的 {{ requiredDocs.length }} 份文件均已就绪，可以下载成稿。</span>
              <span v-if="naDocs.length && project.final.ok" class="muted" data-testid="doc-final-skipped">（成稿包仅收录上述 {{ requiredDocs.length }} 份必备文件；其余 {{ naDocs.length }} 份按当前路径无需单独准备）</span>
              <button v-if="project.final.issues?.length" type="button" class="inline-link issue-link" data-testid="doc-final-issues" @click="project.final.issues.length === 1 ? gotoIssue(project.final.issues[0]) : (showIssues = !showIssues)">发现 {{ project.final.issues.length }} 处填写不一致</button>
            </div>
            <div v-if="showIssues && project.final.issues?.length > 1" class="final-list" data-testid="doc-final-issue-list">
              <div class="final-group">
                <b>填写口径不一致（{{ project.final.issues.length }} 处）</b>
                <button v-for="(i, n) in project.final.issues" :key="n" type="button" class="final-item" @click="gotoIssue(i)"><em class="lv" :class="i.level">{{ LEVEL_TEXT[i.level] }}</em>{{ labelOf(i.key) }}：{{ i.text }}</button>
              </div>
            </div>
            <div v-if="showGate && !project.final.ok" class="final-list" data-testid="doc-final-list">
              <div v-for="g in finalGroups" :key="g.tier" class="final-group">
                <b>{{ g.title }}（缺 {{ g.items.length }} 项）</b>
                <button v-for="m in g.items" :key="m.key" type="button" class="final-item" :title="`用于：${m.docs.join('、')}`" :data-testid="`final-q-${m.key}`" @click="gotoQuestion(m.key)">{{ m.label }}</button>
              </div>
              <div v-if="project.final.forms.length" class="final-group">
                <b>法定申报表</b>
                <button v-for="f in project.final.forms" :key="f.code" type="button" class="final-item" :data-testid="`final-form-${f.code}`" @click="openOfficial(f.code)">{{ f.title }}（还缺 {{ f.missing }} 项）</button>
              </div>
              <div v-if="project.final.papers.length" class="final-group">
                <b>待生成论述</b>
                <span v-for="x in project.final.papers" :key="x.code" class="final-item plain">{{ x.name }}（在下方对应文件旁点击生成论述）</span>
              </div>
            </div>
            <div v-if="project.third_country_checklists?.length" class="third-country-box" data-testid="doc-third-country-card">
              <div class="tc-head">
                <span><b>库外法域核查提示：</b>{{ basisNote }}</span>
                <span class="tc-actions">
                  <button type="button" class="inline-link" data-testid="doc-tc-toggle" @click="showThirdCountry = !showThirdCountry">{{ showThirdCountry ? '收起核查要点' : `展开 ${project.third_country_checklists[0].items.length} 项属地核查要点` }}</button>
                  <button type="button" class="inline-link" data-testid="doc-tc-preview" @click="openAidPreview('third-country')">预览清单</button>
                  <el-tooltip :disabled="downloadable" content="当前只读账号未开通导出权限" placement="top">
                    <span><button type="button" class="inline-link" :disabled="!downloadable || busy === 'aid-third-country-docx'" data-testid="doc-tc-dl" @click="downloadAid('third-country', 'docx')">下载 Word</button></span>
                  </el-tooltip>
                </span>
              </div>
              <div v-if="showThirdCountry" class="tc-body" data-testid="doc-tc-body">
                <div v-for="ck in project.third_country_checklists" :key="ck.country" class="tc-country">
                  <p class="tc-note">{{ ck.framework_note }}</p>
                  <ul class="tc-list">
                    <li v-for="it in ck.items" :key="it.seq"><b>{{ it.seq }} · {{ it.topic }}：</b>{{ it.detail }}</li>
                  </ul>
                  <p class="tc-import">{{ ck.import_guide }}</p>
                </div>
              </div>
            </div>
            <p v-else-if="basisNote" class="muted basis-note">{{ basisNote }}</p>
            <div v-if="project.signoff_summary" class="signoff-strip" data-testid="doc-signoff-strip">
              <span><b>递交前人工核签单：</b>已汇总本项目 {{ project.signoff_summary.check_items_count }} 项证照原件、实车网关配置与合同签章复核事项（下载全部成稿包时自动置于包首）。</span>
              <span class="tc-actions">
                <button type="button" class="inline-link" data-testid="doc-signoff-preview" @click="openAidPreview('signoff')">预览核签单</button>
                <el-tooltip :disabled="downloadable" content="当前只读账号未开通导出权限" placement="top">
                  <span><button type="button" class="inline-link" :disabled="!downloadable || busy === 'aid-signoff-docx'" data-testid="doc-signoff-dl-docx" @click="downloadAid('signoff', 'docx')">下载 Word</button></span>
                </el-tooltip>
                <el-tooltip :disabled="downloadable" content="当前只读账号未开通导出权限" placement="top">
                  <span><button type="button" class="inline-link" :disabled="!downloadable || busy === 'aid-signoff-pdf'" data-testid="doc-signoff-dl-pdf" @click="downloadAid('signoff', 'pdf')">下载 PDF</button></span>
                </el-tooltip>
              </span>
            </div>
          </header>

          <section v-if="editingRoute" class="edit-route" data-testid="doc-edit-route">
            <div class="row2">
              <el-form-item label="数据发出国"><el-select v-model="project.route.source_country" filterable allow-create default-first-option><el-option v-for="c in options.countries" :key="c" :label="c" :value="c" /></el-select></el-form-item>
              <el-form-item label="数据接收国"><el-select v-model="project.route.recipient_country" filterable allow-create default-first-option><el-option v-for="c in options.countries" :key="c" :label="c" :value="c" /></el-select></el-form-item>
            </div>
            <div class="row2">
              <el-form-item label="主要用途（可多选，支持自定义输入回车添加）"><el-select v-model="project.route.functions" multiple filterable allow-create default-first-option clearable placeholder="请选择或输入自定义业务场景"><el-option v-for="f in options.functions" :key="f.key" :label="f.label" :value="f.key" /></el-select></el-form-item>
              <el-form-item label="业务阶段（可多选，支持自定义输入回车添加）"><el-select v-model="project.route.business_scenes" multiple filterable allow-create default-first-option clearable placeholder="请选择或输入业务阶段"><el-option v-for="s in options.scenes" :key="s.key" :label="s.label" :value="s.key" /></el-select></el-form-item>
            </div>
            <div class="row2">
              <el-form-item label="车型或车队"><el-input v-model="project.route.vehicle_model" />
                <button v-for="i in project.route_issues.filter((x) => x.key === 'vehicle_model')" :key="i.id" type="button" class="q-issue" :class="i.level" @click="openIssue(i)"><em>{{ LEVEL_TEXT[i.level] }}</em>{{ i.text }}</button></el-form-item>
              <el-form-item label="企业名称"><el-input v-model="project.route.company_name" />
                <button v-if="project.route.company_name_carried?.project_id" type="button" class="q-carried" data-testid="route-company-carried" @click="router.push({ path: '/documents', query: { project: project.route.company_name_carried.project_id } })">{{ project.route.company_name_carried.text }}</button>
                <button v-for="i in project.route_issues.filter((x) => x.key === 'company_name')" :key="i.id" type="button" class="q-issue" :class="i.level" data-testid="route-issue-company_name" @click="openIssue(i)"><em>{{ LEVEL_TEXT[i.level] }}</em>{{ i.text }}</button></el-form-item>
            </div>
            <el-button type="primary" :loading="busy === 'route'" @click="saveRoute">保存修改</el-button>
          </section>

          <section class="summary" data-testid="doc-summary">
            <div v-if="project.project_verdict" class="verdict-overview-card" data-testid="doc-verdict-overview">
              <div class="vo-col">
                <span class="vo-label">发出国法定出境路径</span>
                <strong class="vo-val">
                  {{ project.cn_route ? (project.cn_route.route === 'unknown' ? '出境路径待判定' : project.cn_route.text) : (project.project_verdict.route_text || `${project.route.source_country} → ${project.route.recipient_country}`) }}
                </strong>
                <span class="vo-sub">{{ project.route.source_country }} → {{ project.route.recipient_country }}</span>
              </div>
              <div class="vo-col">
                <span class="vo-label">风险等级与压降进度</span>
                <strong v-if="project.risk_evolution && project.risk_evolution.total > 0" class="vo-val">
                  当前高 {{ project.risk_evolution.current_counts['高风险'] ?? 0 }} · 中 {{ project.risk_evolution.current_counts['中风险'] ?? 0 }} · 低 {{ project.risk_evolution.current_counts['低风险'] ?? 0 }}
                </strong>
                <strong v-else class="vo-val">待开展风险识别与定级</strong>
                <span class="vo-sub">
                  <template v-if="project.risk_evolution && project.risk_evolution.total > 0">
                    初评高 {{ project.risk_evolution.init_counts['高风险'] ?? 0 }} / 中 {{ project.risk_evolution.init_counts['中风险'] ?? 0 }} / 低 {{ project.risk_evolution.init_counts['低风险'] ?? 0 }}
                  </template>
                  <template v-else>完成风险定级后实时联动各报告结论</template>
                </span>
              </div>
              <div class="vo-col vo-col-verdict">
                <div class="vo-verdict-head">
                  <span class="vo-label">场景综合研判结论</span>
                  <span class="verdict-pill" :class="`tone-${project.project_verdict.tone}`" data-testid="project-verdict-label">
                    {{ project.project_verdict.label }}
                  </span>
                </div>
                <p class="vo-verdict-text" data-testid="project-verdict-text">{{ project.project_verdict.text }}</p>
              </div>
            </div>

            <div class="summary-line"><b>本次应出文件信息完整度 {{ percent }}%</b></div>
            <el-progress :percentage="percent" :show-text="false" :stroke-width="8" />
            <p class="state-counts" data-testid="doc-state-counts"><template v-if="stateCounts.na">本次应出 {{ requiredDocs.length }} 份：</template>未开始 {{ stateCounts.not_started }} 份 · 填写中 {{ stateCounts.filling }} 份 · 待生成论述 {{ stateCounts.paper }} 份 · 已就绪 {{ stateCounts.ready }} 份<template v-if="stateCounts.na">（另有 {{ stateCounts.na }} 份按当前路径无需单独准备，已折叠收起）</template></p>
            <div v-if="project.cn_route" class="cn-route" :class="project.cn_route.route" data-testid="doc-cn-route">
              <b>出境路径研判：{{ project.cn_route.route === 'unknown' ? '出境路径待判定' : project.cn_route.text }}</b>
              <span v-if="project.cn_route.because.length">判定事实：{{ project.cn_route.because.join('；') }}。</span>
              <span v-else-if="project.cn_route.missing.length" class="route-ask"><span v-if="locked">出境路径待判定</span><el-button v-if="!locked" type="primary" plain size="small" data-testid="doc-route-answer" @click="answerRoute">补充判定信息</el-button></span>
              <small class="muted">法规依据：{{ project.cn_route.basis }}</small>
              <p class="advisor-rec" data-testid="doc-advisor-rec">{{ advisorSummary }}</p>
              <div v-if="project.cn_route.validity" class="validity" data-testid="doc-validity">
                <div class="period" :class="{ over: project.cn_route.validity.filing && project.cn_route.validity.filing.days_left < 0 }" data-testid="doc-filing">
                  <span class="p-name">申报截止日</span>
                  <span v-if="project.cn_route.validity.issued_on">已取得评估结果，不再计申报截止日</span>
                  <template v-else-if="project.cn_route.validity.filing">
                    <b>{{ periodText(project.cn_route.validity.filing, '已超过') }}</b>
                    <span class="muted">{{ project.cn_route.validity.filing.days_left < 0 ? '超过 3 个月，须重新开展自评估后再申报' : '自评估完成后 3 个月内须申报' }}</span>
                  </template>
                  <span v-else class="p-ask" data-testid="doc-filing-ask">填写自评估完成日期后自动计算</span>
                  <label class="p-date" data-testid="doc-assessed-on">自评估完成日
                    <el-date-picker :model-value="project.cn_route.validity.assessed_on || null" type="date" value-format="YYYY-MM-DD" placeholder="选择日期" size="small" style="width: 140px" :disabled="locked || busy === 'self_assessed_on'" :disabled-date="afterToday" @update:model-value="(v: string | null) => saveDate('self_assessed_on', v)" />
                  </label>
                </div>
                <div class="period" :class="{ over: project.cn_route.validity.result && project.cn_route.validity.result.days_left < 0, soon: project.cn_route.validity.result?.state === '即将到期' }" data-testid="doc-result">
                  <span class="p-name">评估结果到期日</span>
                  <b v-if="project.cn_route.validity.result">{{ periodText(project.cn_route.validity.result) }}</b>
                  <span v-else class="p-ask" data-testid="doc-result-ask">填写评估结果出具日期后自动计算</span>
                  <span class="muted">出具之日起 3 年有效；{{ project.cn_route.validity.extend }}</span>
                  <label class="p-date" data-testid="doc-issued-on">评估结果出具日
                    <el-date-picker :model-value="project.cn_route.validity.issued_on || null" type="date" value-format="YYYY-MM-DD" placeholder="选择日期" size="small" style="width: 140px" :disabled="locked || busy === 'result_issued_on'" :disabled-date="afterToday" @update:model-value="(v: string | null) => saveDate('result_issued_on', v)" />
                  </label>
                </div>
                <small class="muted">期限依据：<template v-for="(b, i) in project.cn_route.validity.basis" :key="b.law + b.article"><template v-if="i">；</template><a v-if="b.link" :href="b.link" target="_blank" rel="opener" data-testid="doc-validity-link">{{ b.law }}{{ b.article }}</a><span v-else :title="b.point">{{ b.law }}{{ b.article }}</span></template></small>
              </div>
            </div>
            <div v-else class="cn-route overseas-route" data-testid="doc-advisor-card">
              <b>跨境流向研判：{{ project.route.source_country }} → {{ project.route.recipient_country }}</b>
              <p class="advisor-rec" data-testid="doc-advisor-rec">{{ advisorSummary }}</p>
            </div>
            <div v-if="project.risk_evolution && project.risk_evolution.total > 0" class="risk-evo-strip" data-testid="doc-risk-evolution">
              <b>风险点变化（共 {{ project.risk_evolution.total }} 项）</b>
              <span>初评：高 {{ project.risk_evolution.init_counts['高风险'] ?? 0 }} · 中 {{ project.risk_evolution.init_counts['中风险'] ?? 0 }} · 低 {{ project.risk_evolution.init_counts['低风险'] ?? 0 }}</span>
              <span class="muted">→</span>
              <span>当前正式报告：<b>高 {{ project.risk_evolution.current_counts['高风险'] ?? 0 }}</b> · <b>中 {{ project.risk_evolution.current_counts['中风险'] ?? 0 }}</b> · <b>低 {{ project.risk_evolution.current_counts['低风险'] ?? 0 }}</b><template v-if="riskClearedCount(project.risk_evolution) > 0">（含 {{ riskClearedCount(project.risk_evolution) }} 项已整改消除）</template></span>
              <span v-if="project.risk_evolution.downgraded_count > 0" class="evo-pill ok">已降级 {{ project.risk_evolution.downgraded_count }} 项</span>
              <span v-if="project.risk_evolution.excluded_count > 0" class="evo-pill ok">已完成整改 {{ project.risk_evolution.excluded_count }} 项</span>
              <span v-if="(project.risk_evolution.init_counts['高风险'] ?? 0) > 0 && (project.risk_evolution.current_counts['高风险'] ?? 0) === 0" class="evo-pill ok">高风险已清零</span>
              <button v-if="editable && !locked && !showAll" type="button" class="inline-link evo-jump" data-testid="doc-toggle-inline-risk" @click="showMainRiskEditor = !showMainRiskEditor">{{ showMainRiskEditor ? '收起在线定级' : '在线调整风险定级' }}</button>
              <button type="button" class="inline-link" :class="{ 'evo-jump': !(editable && !locked && !showAll) }" data-testid="doc-risk-evolution-go" @click="gotoQuestion('risk_rating')">{{ editable ? '定位至填报项' : '查看风险明细' }}</button>
            </div>

            <div v-if="parsedMainRisks.length > 0 || (showMainRiskEditor && editable && !locked && !showAll)" class="main-risk-panel" data-testid="doc-main-risk-panel">
              <template v-if="showMainRiskEditor && editable && !locked && !showAll && riskQuestion">
                <div class="mrp-head">
                  <b>在线调整主要风险与可能性 × 严重程度定级</b>
                  <span class="muted">修改风险档位或勾选已完成整改后，上方研判结论及下方 6 份评估报告结论将实时同步校准</span>
                </div>
                <RiskTable
                  v-model="edits.risk_rating"
                  :project-id="project.id"
                  :risk-sync="project.risk_sync"
                  :disabled="locked"
                  @save="saveInlineRisk"
                  @diagnosed="applyDiagnosedProject"
                />
              </template>
              <div v-else-if="parsedMainRisks.length > 0" class="main-risk-matrix" data-testid="doc-main-risk-matrix">
                <div class="mrp-head">
                  <b>风险等级分布（可能性 × 严重程度）</b>
                  <span class="muted">实框为当前有效风险，虚框标注已调整或已完成整改的初评位置</span>
                </div>
                <table>
                  <thead>
                    <tr>
                      <th class="corner">可能性 \ 严重程度</th>
                      <th>严重程度：低</th>
                      <th>严重程度：中</th>
                      <th>严重程度：高</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr v-for="g in mainRiskGrid" :key="g.label">
                      <th>{{ g.label }}</th>
                      <td v-for="(c, j) in g.cells" :key="j" :class="`rt-${c.tone}`">
                        <small class="rt-grade">{{ c.name }}</small>
                        <div v-if="c.activeLabels.length || c.movedFrom.length" class="rt-tags">
                          <span v-for="a in c.activeLabels" :key="a" class="rt-pill active">{{ a }}</span>
                          <span v-for="m in c.movedFrom" :key="m" class="rt-pill moved">{{ m }}</span>
                        </div>
                      </td>
                    </tr>
                  </tbody>
                </table>
              </div>
            </div>
            <p v-if="nearest && !locked" class="next-step" data-testid="doc-nearest">
              《{{ nearest.name }}》仅缺 {{ nearest.missing.length }} 项信息即可生成成稿。
              <button type="button" class="inline-link" data-testid="doc-nearest-go" @click="fillDoc(nearest.code)">立即补充</button>
            </p>
            <p v-if="thinQuestions.length && editable && !locked" class="next-step density-summary" data-testid="doc-density-summary">
              颗粒度预检：发现 {{ thinQuestions.length }} 项关键论述信息（{{ thinQuestions.slice(0, 3).map((x) => x.label).join('、') }}）填写较简略，建议补齐业务与控制要素以提升报告深度。
              <button type="button" class="inline-link" data-testid="doc-density-go" @click="gotoQuestion(thinQuestions[0].key)">前往细化</button>
            </p>
            <div v-if="pending.length && editable && !locked" class="pending-bar" data-testid="doc-pending-bar">
              <span>已从业务说明中提取 <b>{{ pending.length }}</b> 项可填信息，确认采用后将自动写入对应文件。</span>
              <el-button size="small" @click="goPending">逐项核对</el-button>
              <el-button size="small" :loading="busy === 'accept'" data-testid="doc-accept-all-top" @click="resolveSuggestions(null)">全部采用</el-button>
            </div>
          </section>

          <section class="docs" data-testid="doc-list">
            <div class="docs-group-head" data-testid="doc-required-head">
              <b>本次应出文件（{{ requiredDocs.length }} 份）</b>
              <span class="muted">{{ naDocs.length ? '已按数据流向与出境路径自动判定；完成本组文件即可下载成稿' : '分别列示每份文件的填写就绪状态与评估判定结论' }}</span>
            </div>
            <div class="docs-col-head" aria-hidden="true">
              <span>文件名称与填报提示</span>
              <span>填写状态</span>
              <span>判定结论</span>
              <span class="col-act">操作</span>
            </div>
            <div v-for="d in requiredDocs" :key="d.code" class="doc" :data-testid="`doc-${d.code}`">
              <div class="doc-main">
                <div class="doc-title">
                  <b>{{ d.name }}</b>
                  <span v-if="d.edited" class="basis-pill edited" :data-testid="`edited-${d.code}`">已在线修订</span>
                </div>
                <div class="doc-state-row">
                  <span v-if="project.applicability?.[d.code]?.manual_include" class="applies-short manual">已纳入本次出件</span>
                  <button v-if="project.applicability?.[d.code]?.manual_include && editable && !locked" type="button" class="why-link" :disabled="busy === `inc-${d.code}`" :data-testid="`uninclude-${d.code}`" @click="toggleManualInclude(d, false)">恢复自动判断</button>
                  <template v-if="project.applicability?.[d.code]?.state === 'unknown' && project.applicability[d.code].missing_keys.length && appliesShort(d.code) !== '等出境路径'">
                    <span class="applies-short unknown">待核定是否需要</span>
                    <button v-if="!locked" type="button" class="inline-link" :data-testid="`applies-answer-${d.code}`" @click="answerKeys(project.applicability[d.code].missing_keys, `《${d.name}》是否需要`)">补充信息</button>
                  </template>
                  <span v-if="officialOnly(d)" class="doc-state">按发出国法定申报表逐张填写后导出</span>
                  <button v-else-if="d.missing.length && !locked" type="button" class="doc-state missing as-link" :title="d.missing.map((m) => m.label).join('、')" :data-testid="`fill-${d.code}`" @click="fillDoc(d.code)">还缺 {{ d.missing.length }} 项</button>
                  <span v-else-if="d.state === 'needs_paper' && d.paper?.state !== 'running'" class="doc-state">信息已齐，点击右侧生成论述</span>
                </div>
                <div v-if="d.paper?.state === 'failed'" class="err">{{ d.paper.error }}</div>
                <div v-if="d.basis_updated && d.paper?.state !== 'running'" class="basis-updated" :data-testid="`basis-updated-${d.code}`">
                  <template v-if="locked">论述写成后，引用的条文有更新；现有论述仍按原条文。</template>
                  <template v-else>论述写成后，引用的条文有更新；现有文件照常可下载。<button type="button" class="inline-link" @click="writePaper(d)">按新条文重新生成论述</button></template>
                </div>
                <p v-if="d.official_forms.some((f) => f.note)" class="of-note">{{ d.official_forms.find((f) => f.note)!.note }}</p>
                <ul v-if="d.official_forms.length" class="officials" :data-testid="`officials-${d.code}`">
                  <li v-for="f in d.official_forms" :key="f.code">
                    <div class="of-main">
                      <span class="of-name">{{ f.title }}（{{ f.short }}）</span>
                      <span class="of-sub">{{ f.side_label }}填写 · {{ f.missing ? `还缺 ${f.missing} 项` : '已可导出' }}</span>
                    </div>
                    <div class="of-actions">
                      <el-button size="small" :disabled="locked && !f.instance_id" :data-testid="`official-${f.code}`" @click="openOfficial(f.code, f.instance_id)">{{ !editable ? (f.instance_id ? '查看表格' : '未填写') : (f.instance_id ? '继续填写' : '填写') }}</el-button>
                      <el-tooltip :disabled="downloadable" content="当前只读账号未开通导出权限" placement="top">
                        <span><el-button size="small" :disabled="!downloadable" :loading="busy === `of-${f.code}`" :data-testid="`official-pdf-${f.code}`" @click="downloadOfficial(f)">{{ isDocxOfficialForm(f) ? '下载申报表 Word' : '下载申报表 PDF' }}</el-button></span>
                      </el-tooltip>
                    </div>
                  </li>
                </ul>
              </div>
              <div class="doc-status"><span class="st" :class="d.state" :data-testid="`state-${d.code}`">{{ stateText(d) }}</span><el-tooltip v-if="project.applicability?.[d.code]?.state === 'unknown'" :content="`${appliesShort(d.code)}：${project.applicability[d.code].why}`" placement="top"><span class="st-hint" tabindex="0" aria-label="还不能判断是否需要这份文件">!</span></el-tooltip></div>
              <div class="doc-verdict">
                <span
                  v-if="d.verdict"
                  class="verdict-pill"
                  :class="`tone-${d.verdict.tone}`"
                  :title="d.verdict.text"
                  :data-testid="`verdict-${d.code}`"
                >
                  {{ d.verdict.label }}
                </span>
              </div>
              <div class="doc-actions">
                <AdmissionLaunch v-if="d.code === 'admission' && editable" :task-id="project.id" :disabled="locked" label="去办理" />
                <el-button v-if="d.narrative && d.state === 'needs_paper' && d.paper?.state !== 'running'" size="small" type="primary" plain :disabled="locked" @click="writePaper(d)">生成论述</el-button>
                <el-button v-if="!officialOnly(d)" size="small" :data-testid="`preview-${d.code}`" @click="openPreview(d)">{{ d.has_edpb_en ? '预览中文稿' : '预览' }}</el-button>
                <el-dropdown v-if="!officialOnly(d) && downloadable" trigger="click" @command="(f: DocDlCmd) => download(d, f)">
                  <el-button size="small" :loading="busy.startsWith(`dl-${d.code}`)" :data-testid="`dl-${d.code}`">{{ d.has_edpb_en ? '下载中文/多语版' : '下载（中/英/属地语）' }}</el-button>
                  <template #dropdown><el-dropdown-menu>
                    <el-dropdown-item command="docx" :data-testid="`dl-${d.code}-word`">{{ d.state === 'ready' ? '中文版 · Word' : '中文初稿 · Word' }}</el-dropdown-item>
                    <el-dropdown-item command="pdf" :data-testid="`dl-${d.code}-pdf`">{{ d.state === 'ready' ? '中文版 · PDF' : '中文初稿 · PDF' }}</el-dropdown-item>
                    <el-dropdown-item command="xlsx" :data-testid="`dl-${d.code}-xlsx`">{{ d.state === 'ready' ? '中文版 · Excel' : '中文初稿 · Excel' }}</el-dropdown-item>
                    <template v-if="!d.has_edpb_en">
                      <el-dropdown-item command="en-preview" divided :data-testid="`preview-${d.code}-en`">预览英文版</el-dropdown-item>
                      <el-dropdown-item command="en-docx" :data-testid="`dl-${d.code}-en-word`">{{ d.state === 'ready' ? '英文版 · Word' : '英文初稿 · Word' }}</el-dropdown-item>
                      <el-dropdown-item command="en-pdf" :data-testid="`dl-${d.code}-en-pdf`">{{ d.state === 'ready' ? '英文版 · PDF' : '英文初稿 · PDF' }}</el-dropdown-item>
                    </template>
                    <el-dropdown-item command="i18n-preview" divided :data-testid="`preview-${d.code}-i18n`">预览{{ hostLangMeta.nameZh }}版（{{ hostLangMeta.native }}）</el-dropdown-item>
                    <el-dropdown-item command="i18n-docx" :data-testid="`dl-${d.code}-i18n-word`">{{ d.state === 'ready' ? `${hostLangMeta.nameZh}版 · Word` : `${hostLangMeta.nameZh}初稿 · Word` }}</el-dropdown-item>
                    <el-dropdown-item command="i18n-pdf" :data-testid="`dl-${d.code}-i18n-pdf`">{{ d.state === 'ready' ? `${hostLangMeta.nameZh}版 · PDF` : `${hostLangMeta.nameZh}初稿 · PDF` }}</el-dropdown-item>
                  </el-dropdown-menu></template>
                </el-dropdown>
                <el-tooltip v-else-if="!officialOnly(d)" content="当前只读账号未开通导出权限" placement="top">
                  <span><el-button size="small" disabled :data-testid="`dl-${d.code}`">{{ d.has_edpb_en ? '下载中文版' : '下载' }}</el-button></span>
                </el-tooltip>
                <el-dropdown v-if="d.has_edpb_en" trigger="click" @command="(cmd: string) => cmd === 'preview' ? openPreviewEdpbEn(d) : downloadEdpbEn(d, cmd as 'docx' | 'pdf')">
                  <el-button size="small" type="primary" plain :loading="busy === 'dl-edpb-docx' || busy === 'dl-edpb-pdf'" data-testid="dl-dpia-edpb-en">欧盟标准英文范本</el-button>
                  <template #dropdown><el-dropdown-menu>
                    <el-dropdown-item command="preview" data-testid="preview-dpia-edpb-en">预览英文标准范本</el-dropdown-item>
                    <el-dropdown-item command="docx" :disabled="!downloadable" divided :title="!downloadable ? '当前只读账号未开通导出权限' : undefined" data-testid="dl-dpia-edpb-en-word">{{ d.state === 'ready' ? '英文范本 · Word' : '英文范本初稿 · Word' }}{{ !downloadable ? '（只读未开通导出权限）' : '' }}</el-dropdown-item>
                    <el-dropdown-item command="pdf" :disabled="!downloadable" :title="!downloadable ? '当前只读账号未开通导出权限' : undefined" data-testid="dl-dpia-edpb-en-pdf">{{ d.state === 'ready' ? '英文范本 · PDF' : '英文范本初稿 · PDF' }}{{ !downloadable ? '（只读未开通导出权限）' : '' }}</el-dropdown-item>
                  </el-dropdown-menu></template>
                </el-dropdown>
              </div>
            </div>
            <template v-if="naDocs.length">
              <button type="button" class="na-group-toggle" :aria-expanded="showNaDocs" data-testid="doc-na-toggle" @click="showNaDocs = !showNaDocs">
                <span>无需单独准备的文件（{{ naDocs.length }} 份）</span>
                <span class="na-toggle-act">{{ showNaDocs ? '收起' : '展开查看 / 按需纳入' }}</span>
              </button>
              <div v-show="showNaDocs" class="na-docs-wrap" data-testid="doc-na-list">
                <div v-for="d in naDocs" :key="d.code" class="doc na" :data-testid="`doc-${d.code}`">
                  <div class="doc-main">
                    <div class="doc-title">
                      <b>{{ d.name }}</b>
                      <span v-if="d.edited" class="basis-pill edited" :data-testid="`edited-${d.code}`">已在线修订</span>
                    </div>
                    <p v-if="project.applicability?.[d.code]?.why" class="applies na" :data-testid="`applies-${d.code}`">无需准备原因：{{ project.applicability[d.code].why }}</p>
                    <div v-if="d.paper?.state === 'failed'" class="err">{{ d.paper.error }}</div>
                    <p v-if="d.official_forms.some((f) => f.note)" class="of-note">{{ d.official_forms.find((f) => f.note)!.note }}</p>
                    <ul v-if="d.official_forms.length" class="officials" :data-testid="`officials-${d.code}`">
                      <li v-for="f in d.official_forms" :key="f.code">
                        <div class="of-main">
                          <span class="of-name">{{ f.title }}（{{ f.short }}）</span>
                          <span class="of-sub">{{ f.side_label }}填写 · {{ f.missing ? `还缺 ${f.missing} 项` : '已可导出' }}</span>
                        </div>
                        <div class="of-actions">
                          <el-button size="small" :disabled="locked && !f.instance_id" :data-testid="`official-${f.code}`" @click="openOfficial(f.code, f.instance_id)">{{ !editable ? (f.instance_id ? '查看表格' : '未填写') : (f.instance_id ? '继续填写' : '填写') }}</el-button>
                          <el-tooltip :disabled="downloadable" content="当前只读账号未开通导出权限" placement="top">
                            <span><el-button size="small" :disabled="!downloadable" :loading="busy === `of-${f.code}`" :data-testid="`official-pdf-${f.code}`" @click="downloadOfficial(f)">{{ isDocxOfficialForm(f) ? '下载申报表 Word' : '下载申报表 PDF' }}</el-button></span>
                          </el-tooltip>
                        </div>
                      </li>
                    </ul>
                  </div>
                  <div class="doc-status"><span class="st quiet" :class="d.state" :data-testid="`state-${d.code}`">{{ stateText(d) }}</span></div>
                  <div class="doc-verdict" />
                  <div class="doc-actions">
                    <el-button v-if="editable && !locked" size="small" plain :loading="busy === `inc-${d.code}`" :data-testid="`include-${d.code}`" @click="toggleManualInclude(d, true)">纳入本次出件</el-button>
                    <AdmissionLaunch v-if="d.code === 'admission' && editable" :task-id="project.id" :disabled="locked" label="去办理" />
                    <el-button v-if="d.narrative && d.state === 'needs_paper' && d.paper?.state !== 'running'" size="small" :disabled="locked" @click="writePaper(d)">生成论述</el-button>
                    <el-button v-if="!officialOnly(d)" size="small" :data-testid="`preview-${d.code}`" @click="openPreview(d)">预览</el-button>
                    <el-dropdown v-if="!officialOnly(d) && downloadable" trigger="click" @command="(f: DocDlCmd) => download(d, f)">
                      <el-button size="small" :loading="busy.startsWith(`dl-${d.code}`)" :data-testid="`dl-${d.code}`">下载（中/英/属地语）</el-button>
                      <template #dropdown><el-dropdown-menu>
                        <el-dropdown-item command="docx" :data-testid="`dl-${d.code}-word`">{{ d.state === 'ready' ? '中文版 · Word' : '中文初稿 · Word' }}</el-dropdown-item>
                        <el-dropdown-item command="pdf" :data-testid="`dl-${d.code}-pdf`">{{ d.state === 'ready' ? '中文版 · PDF' : '中文初稿 · PDF' }}</el-dropdown-item>
                        <el-dropdown-item command="xlsx" :data-testid="`dl-${d.code}-xlsx`">{{ d.state === 'ready' ? '中文版 · Excel' : '中文初稿 · Excel' }}</el-dropdown-item>
                        <el-dropdown-item command="en-preview" divided :data-testid="`preview-${d.code}-en`">预览英文版</el-dropdown-item>
                        <el-dropdown-item command="en-docx" :data-testid="`dl-${d.code}-en-word`">{{ d.state === 'ready' ? '英文版 · Word' : '英文初稿 · Word' }}</el-dropdown-item>
                        <el-dropdown-item command="en-pdf" :data-testid="`dl-${d.code}-en-pdf`">{{ d.state === 'ready' ? '英文版 · PDF' : '英文初稿 · PDF' }}</el-dropdown-item>
                        <el-dropdown-item command="i18n-preview" divided :data-testid="`preview-${d.code}-i18n`">预览{{ hostLangMeta.nameZh }}版（{{ hostLangMeta.native }}）</el-dropdown-item>
                        <el-dropdown-item command="i18n-docx" :data-testid="`dl-${d.code}-i18n-word`">{{ d.state === 'ready' ? `${hostLangMeta.nameZh}版 · Word` : `${hostLangMeta.nameZh}初稿 · Word` }}</el-dropdown-item>
                        <el-dropdown-item command="i18n-pdf" :data-testid="`dl-${d.code}-i18n-pdf`">{{ d.state === 'ready' ? `${hostLangMeta.nameZh}版 · PDF` : `${hostLangMeta.nameZh}初稿 · PDF` }}</el-dropdown-item>
                      </el-dropdown-menu></template>
                    </el-dropdown>
                    <el-tooltip v-else-if="!officialOnly(d)" content="当前只读账号未开通导出权限" placement="top">
                      <span><el-button size="small" disabled :data-testid="`dl-${d.code}`">下载</el-button></span>
                    </el-tooltip>
                  </div>
                </div>
              </div>
            </template>
          </section>

          <section v-if="project.suggest_diagnosis && (project.suggest_diagnosis.summary || project.suggest_diagnosis.gaps?.length || project.suggest_diagnosis.followups?.length)" class="diagnosis-card" :class="{ 'is-collapsed': diagCollapsed }" data-testid="doc-suggest-diagnosis">
            <div class="diag-head" @click="diagCollapsed = !diagCollapsed">
              <div class="diag-title-wrap">
                <b>导入材料合规差距分析与补充建议</b>
                <span v-if="project.suggest_diagnosis.doc_kind" class="diag-pill kind" data-testid="doc-suggest-kind">{{ project.suggest_diagnosis.doc_kind }}</span>
                <span class="diag-pill redact" data-testid="doc-redaction-badge">
                  <template v-if="project.suggest_diagnosis.redaction?.total">已隐去 {{ project.suggest_diagnosis.redaction.total }} 处敏感信息</template>
                  <template v-else>敏感信息保护已启用</template>
                </span>
              </div>
              <button type="button" class="diag-toggle" :aria-expanded="!diagCollapsed" data-testid="doc-diag-toggle" @click.stop="diagCollapsed = !diagCollapsed">{{ diagCollapsed ? '展开查看' : '收起' }}</button>
            </div>
            <template v-if="!diagCollapsed">
              <p v-if="project.suggest_diagnosis.redaction?.labels?.length" class="diag-redact-detail">敏感信息隐去明细：{{ project.suggest_diagnosis.redaction.labels.join('、') }}</p>
              <p v-if="project.suggest_diagnosis.summary" class="diag-summary">{{ project.suggest_diagnosis.summary }}</p>
              <div v-if="project.suggest_diagnosis.gaps?.length" class="diag-block">
                <span class="diag-label">当前材料缺失或与目标路径法规要求不一致之处：</span>
                <ul><li v-for="(g, gi) in project.suggest_diagnosis.gaps" :key="gi">{{ g }}</li></ul>
              </div>
              <div v-if="project.suggest_diagnosis.followups?.length" class="diag-block">
                <span class="diag-label">建议优先核实补充：</span>
                <ul><li v-for="(f, fi) in project.suggest_diagnosis.followups" :key="fi">{{ f }}</li></ul>
              </div>
            </template>
          </section>

          <section v-if="pending.length && editable && !locked" class="pending" data-testid="doc-pending">
            <div class="fill-head">
              <h3>已识别 {{ pending.length }} 项，请核对</h3>
              <el-button type="primary" size="small" :loading="busy === 'accept'" data-testid="doc-accept-all" @click="resolveSuggestions(null)">全部采用</el-button>
              <el-button size="small" @click="resolveSuggestions(null, true)">全部忽略</el-button>
            </div>
            <div v-for="it in pending" :key="it.key" class="sg" :data-testid="`sg-${it.key}`">
              <div class="sg-main">
                <b>{{ it.label }}</b><span v-if="it.sg.source !== 'text'" class="sg-general">依据行业通行做法拟稿，请结合实际核对</span>
                <p class="sg-text">{{ it.sg.text }}</p>
                <p v-if="it.sg.quote" class="sg-quote">原文：{{ it.sg.quote.length > 120 ? it.sg.quote.slice(0, 120) + '……' : it.sg.quote }}</p>
              </div>
              <div class="sg-actions"><el-button size="small" type="primary" plain @click="resolveSuggestions([it.key])">采用</el-button><el-button size="small" @click="resolveSuggestions([it.key], true)">忽略</el-button></div>
            </div>
          </section>

          <section v-if="editable" ref="moreEl" class="more" data-testid="doc-more">
            <h3>完善文件信息</h3>
            <div class="ways">
              <button type="button" class="way" :class="{ on: way === 'say' }" data-testid="way-say" @click="way = 'say'"><b>导入业务说明</b></button>
              <button type="button" class="way" :class="{ on: way === 'file' }" data-testid="way-file" @click="way = 'file'"><b>导入现有文件</b></button>
              <button type="button" class="way" :class="{ on: way === 'step' }" data-testid="way-step" @click="way = 'step'"><b>逐项填写</b></button>
            </div>

            <input ref="fileEl" type="file" accept=".pdf,.docx,.txt,.md" hidden data-testid="doc-file" @change="readFile" />

            <div v-if="way === 'say'" class="panel">
              <p class="file-guide" style="margin-bottom: 8px">综合信息区：多选多个业务场景后无需分栏重复录入，直接在此合并粘贴全部场景的业务与数据说明，系统将自动按场景拆分识别并填入对应合规模块。</p>
              <el-input v-model="pasted" type="textarea" :rows="4" maxlength="50000" :disabled="locked || project.suggest.state === 'running'" placeholder="例如：本车型在欧洲同时开展车联网远程诊断OTA、智能驾驶路测及售后服务：车端故障码与软件版本回传国内工程团队用于排障升级；路测摄像头画面在车端隐去人脸车牌后加密回传训练感知算法；预约维保客户信息加密同步售后系统保存2年。" data-testid="doc-paste" />
              <div class="easy-actions">
                <el-button :loading="busy === 'suggest' || project.suggest.state === 'running'" :disabled="locked" data-testid="doc-read" @click="readText">识别并预填</el-button>
                <span v-if="project.suggest.state === 'running'" class="muted">正在识别填报要素，约需半分钟，完成后自动提示。</span>
                <span v-else-if="project.suggest.state === 'failed'" class="err">{{ project.suggest.error }}</span>
              </div>
            </div>

            <div v-else-if="way === 'file'" class="panel">
              <p class="file-guide">支持上传企业现有的《数据安全管理制度》《数据分类分级规范》《隐私政策》或半成品合规底稿（PDF / Word / TXT）。系统将在调用外部模型前自动隐去敏感标识，提取已知业务与安全要素，并对照本次跨境路径法条诊断制度缺口与补充清单。</p>
              <div class="easy-actions">
                <el-button :loading="busy === 'suggest' || project.suggest.state === 'running'" :disabled="locked" data-testid="doc-read-file" @click="fileEl?.click()">选择制度或底稿文件</el-button>
                <span v-if="project.suggest.state === 'running'" class="muted">正在隐去敏感信息并提取填报要素，约需半分钟，完成后自动提示。</span>
                <span v-else-if="project.suggest.state === 'failed'" class="err">{{ project.suggest.error }}</span>
              </div>
            </div>

            <div v-else class="panel step" data-testid="doc-step">
              <template v-if="current">
                <div class="step-head">
                  <el-tag v-if="askFor && askFirst.includes(current.key)" size="small" type="warning" data-testid="step-ask-for">回答后判断{{ askFor }}</el-tag>
                  <el-tag v-else-if="focusDoc && focusKeys.has(current.key)" size="small">当前补充：{{ docName(focusDoc) }}（还缺 {{ focusKeys.size }} 项）</el-tag>
                  <el-tag v-else size="small" type="info">{{ TIER_NAME[current.tier] }}</el-tag>
                  
                </div>
                <div class="q-grid">
                <div class="q-main">
                <h4>{{ current.label }}</h4>
                <p v-if="issuesOf(current).length" class="step-issues"><button v-for="i in issuesOf(current)" :key="i.id" type="button" class="q-issue" :class="i.level" data-testid="step-issue" @click="openIssue(i)"><em>{{ LEVEL_TEXT[i.level] }}</em>{{ i.text }}</button></p>
                <div v-if="current.choices.length" class="choice-row">
                  <el-button v-for="c in current.choices" :key="c" size="large" :type="choiceOf(current) === c ? 'primary' : 'default'" :plain="choiceOf(current) === c" :disabled="locked" :data-testid="`choice-${c}`" @click="pickChoice(current, c, true)">{{ c }}</el-button>
                </div>
                <el-input v-if="current.other && choiceOf(current) === '其他'" v-model="edits[current.key]" maxlength="200" placeholder="请补充说明" :disabled="locked" data-testid="step-other" />
                <el-input v-else-if="current.detail_on && choiceOf(current) === current.detail_on" :model-value="detailOf(current)" maxlength="200" placeholder="请填写涉及人数（去重），如：约 2 万人" :disabled="locked" data-testid="step-detail" @update:model-value="(v: string) => setDetail(current!, v)" />
                <template v-if="current.kind === 'risks'">
                  <RiskTable v-model="edits[current.key]" :project-id="project.id" :risk-sync="project.risk_sync" :disabled="locked" @diagnosed="applyDiagnosedProject" />
                  <div class="risk-remediate"><el-button size="small" :disabled="locked || !current.remediate?.pending" :loading="busy === 'remediate'" data-testid="step-risk-remediate" @click="remediate(current)">同步至整改报告<template v-if="current.remediate?.pending">（{{ current.remediate.pending }} 条）</template></el-button></div>
                </template>
                <template v-else-if="!current.choices.length">
                  <el-input v-model="edits[current.key]" :type="current.short ? 'text' : 'textarea'" :rows="3" :maxlength="current.max || 1000" :disabled="locked" data-testid="step-input" />
                  <div v-if="densityOf(current) && editable && !locked" class="density-box" :data-testid="`step-density-${current.key}`">
                    <span>{{ densityOf(current)!.message }}</span>
                    <button type="button" class="inline-link" :data-testid="`step-density-expand-${current.key}`" @click="applyDensityDraft(current)">填入场景细化草稿（待核对）</button>
                  </div>
                </template>
                <div class="easy-actions">
                  <el-button v-if="!current.choices.length || needsText(current)" type="primary" :disabled="locked" data-testid="step-next" @click="stepSave(current)">保存并下一项</el-button>
                  <el-button :disabled="locked" data-testid="step-unsure" @click="stepUnsure(current)">暂不确定</el-button>
                  <el-button link :disabled="locked" data-testid="step-skip" @click="stepSkip(current)">跳过</el-button>
                </div>
                <QuestionAssistant :project-id="project.id" :question-key="current.key" :current-value="edits[current.key] || ''" :disabled="locked" @adopt="(v) => adoptFromAssistant(current!, v)" @undo="(prev) => undoFromAssistant(current!, prev)" />
                </div>
                <QuestionBasis :hint="current.hint" :used-for="current.used_for" :basis="current.basis" :example="current.example" />
                </div>
              </template>
              <p v-else class="muted">当前必填项已填写完毕；未填项在初稿中标注为【待补充】，可随时补充修改。</p>
            </div>

            <p class="all-link"><el-button link type="primary" data-testid="doc-show-all" @click="showAll = !showAll">{{ showAll ? '收起全部信息' : '查看并修改全部信息' }}</el-button></p>
          </section>

          <section v-if="showAll" class="fill" data-testid="doc-fill">
            <div class="fill-head">
              <h3>{{ editable ? '全部填报信息' : '项目评估信息' }}</h3>
              <span v-if="editable" class="muted">暂未确认的填报项可标记为暂不确定，初稿中将标注为【待补充】，不影响先行导出初稿。</span>
              <el-button v-if="editable" type="primary" size="small" :disabled="!changed.length || locked" :loading="busy === 'save'" data-testid="doc-save" @click="saveAnswers()">保存（{{ changed.length }} 处修改）</el-button>
            </div>
            <el-collapse v-model="tierCollapse">
              <el-collapse-item v-for="t in TIERS" :key="t.key" :name="t.key" :data-testid="`tier-${t.key}`">
                <template #title><b>{{ t.title }}</b>
                  <button v-if="tierMissing(t.key).length" type="button" class="tier-missing" :data-testid="`tier-missing-${t.key}`" @click.stop="gotoQuestion(tierMissing(t.key)[0].key)">缺 {{ tierMissing(t.key).length }} 项</button>
                  <span v-else class="muted" style="margin-left: 10px">已完成</span></template>
                <p v-if="t.hint" class="muted draft-bar">{{ t.hint }}</p>
                <div v-for="q in tierList(t.key)" :key="q.key" class="q" :class="{ active: activeKey === q.key }" :data-testid="`q-wrap-${q.key}`" @focusin="(e: FocusEvent) => focusQuestion(e, q.key)" @click="activeKey = q.key">
                  <div class="q-grid">
                  <div class="q-main">
                  <div class="q-head">
                    <label :for="`q-${q.key}`">{{ q.label }}</label>
                    <span v-if="onlyNa(q)" class="q-na" :data-testid="`q-na-${q.key}`">{{ onlyNa(q) }}</span>
                    <span v-else-if="blockingKeys.has(q.key)" class="q-need">待填</span>
                    <button v-if="editable && !(edits[q.key] || '').trim()" type="button" class="q-unsure" :disabled="locked" @click="unsure(q)">暂不确定</button>
                    <span v-else-if="isUnsure(edits[q.key])" class="q-flag">暂不确定，文件中标注为【待补充】</span>
                    <button v-if="q.carried && (edits[q.key] || '') === q.value" type="button" class="q-carried" :data-testid="`q-carried-${q.key}`" @click.stop="openCarried(q.key, q.carried)">{{ q.carried.text }}</button>
                    <button v-for="i in issuesOf(q)" :key="i.id" type="button" class="q-issue" :class="i.level" :data-testid="`q-issue-${q.key}`" :title="i.to.kind === 'form' ? `打开《${i.to.name}》的这一填写项` : '定位至不一致项'" @click.stop="openIssue(i)"><em>{{ LEVEL_TEXT[i.level] }}</em>{{ i.text }}</button>
                  </div>
                  <p v-if="q.carried?.kind === 'said' && q.carried.quote && quoteOpen.has(q.key)" class="q-quote">识别原文摘录：{{ q.carried.quote }}</p>
                  <p v-if="activeKey !== q.key" class="qnote"><span v-if="q.hint">{{ q.hint }}</span><span class="used">用于：{{ q.used_for }}</span></p>
                  <el-radio-group v-if="q.choices.length" :model-value="choiceOf(q)" :disabled="locked" :data-testid="`q-${q.key}`" @change="(c: string | number | boolean | undefined) => pickChoice(q, String(c))">
                    <el-radio-button v-for="c in q.choices" :key="c" :value="c">{{ c }}</el-radio-button>
                  </el-radio-group>
                  <el-input v-if="q.choices.length && q.other && choiceOf(q) === '其他'" v-model="edits[q.key]" class="q-other" maxlength="200" placeholder="请补充说明" :disabled="locked" @blur="saveAnswers([q.key])" />
                  <el-input v-else-if="q.detail_on && choiceOf(q) === q.detail_on" :model-value="detailOf(q)" class="q-other" maxlength="200" placeholder="请填写涉及人数（去重），如：约 2 万人" :disabled="locked" :data-testid="`q-detail-${q.key}`" @update:model-value="(v: string) => setDetail(q, v)" @blur="saveAnswers([q.key])" />
                  <template v-if="q.kind === 'risks'">
                    <div :data-testid="`q-${q.key}`"><RiskTable :id="`q-${q.key}`" v-model="edits[q.key]" :project-id="project.id" :risk-sync="project.risk_sync" :disabled="locked" @save="saveInBackground(q)" @diagnosed="applyDiagnosedProject" /></div>
                    <div v-if="editable" class="risk-remediate">
                      <el-button size="small" :disabled="locked || !q.remediate?.pending" :loading="busy === 'remediate'" data-testid="risk-remediate" @click.stop="remediate(q)">同步至整改报告<template v-if="q.remediate?.pending">（{{ q.remediate.pending }} 条）</template></el-button>
                      <span class="muted" data-testid="risk-remediate-note">{{ q.remediate?.pending ? '将应对措施同步写入《合规问题整改跟踪报告》，已有条目自动保留' : q.remediate?.done ? `已同步 ${q.remediate.done} 条整改项` : '完成风险定级并填写应对措施后，可一键同步至整改报告' }}</span>
                    </div>
                  </template>
                  <el-input v-else-if="!q.choices.length" :id="`q-${q.key}`" v-model="edits[q.key]" :type="q.short ? 'text' : 'textarea'" :autosize="q.short ? undefined : { minRows: 2, maxRows: 8 }" :maxlength="q.max || 1000" :disabled="locked" :data-testid="`q-${q.key}`" @blur="saveAnswers([q.key])" />
                  <div v-if="densityOf(q) && editable && !locked" class="density-box" :data-testid="`density-${q.key}`">
                    <span>{{ densityOf(q)!.message }}</span>
                    <button type="button" class="inline-link" :data-testid="`density-expand-${q.key}`" @click.stop="applyDensityDraft(q)">填入场景细化草稿（待核对）</button>
                  </div>
                  <QuestionAssistant v-if="editable" :project-id="project.id" :question-key="q.key" :current-value="edits[q.key] || ''" :disabled="locked" @adopt="(v) => adoptFromAssistant(q, v)" @undo="(prev) => undoFromAssistant(q, prev)" />
                  </div>
                  <QuestionBasis v-if="activeKey === q.key" :hint="q.hint" :used-for="q.used_for" :basis="q.basis" :example="q.example" />
                  </div>
                </div>
              </el-collapse-item>
            </el-collapse>
          </section>

        </template>
      </div>
      <ProjectChat v-if="project && !isNew" ref="chatRef" :project-id="project.id" :disabled="locked" :readonly="!editable" :max-width="chatMax" :apply-fn="applyCopilotProposals" :undo-fn="undoCopilotProposals" :upload-fn="uploadFromChat" @changed="refreshSoon" @open="onChatOpen" />
    </div>
    <el-drawer v-model="previewVisible" :title="previewTitle" size="min(1040px, 94vw)" append-to-body destroy-on-close>
      <div v-loading="previewLoading" class="preview-body" data-testid="doc-preview-drawer">
        <div class="preview-meta">
          <el-tag size="small" :type="previewKind === 'edited' ? 'warning' : previewKind === 'official_docx' ? 'success' : 'info'">{{ previewKind === 'edited' ? '在线修订版' : previewKind === 'official_docx' ? '法定申报表填报预览' : '标准合规报告预览' }}</el-tag>
          <span class="muted">{{ previewSubtitle }}</span>
          <span v-if="!previewIsEdpbEn && !previewAidKind" class="preview-meta-actions">
            <el-button v-if="editable && !locked" size="small" :type="previewEditMode ? 'primary' : 'default'" plain data-testid="doc-preview-edit-toggle" @click="previewEditMode = !previewEditMode">{{ previewEditMode ? '退出修改' : '在线修改' }}</el-button>
            <el-button v-if="previewVersions.length" size="small" plain data-testid="doc-preview-versions-toggle" @click="showVersionHistory = !showVersionHistory">{{ showVersionHistory ? '收起历史版本' : `历史版本（${previewVersions.length} 版）` }}</el-button>
            <el-button v-if="previewEdits.length" size="small" plain data-testid="doc-preview-history-toggle" @click="showEditHistory = !showEditHistory">{{ showEditHistory ? '收起修改记录' : `修改记录（${previewEdits.length} 处）` }}</el-button>
            <el-button v-if="editable && !locked && previewKind === 'edited'" size="small" type="warning" plain :loading="previewSaving" data-testid="doc-preview-reset" @click="resetPreviewEdits">恢复自动生成版</el-button>
          </span>
          <span v-else-if="previewAidKind && project" class="preview-meta-actions">
            <el-tooltip :disabled="downloadable" content="当前只读账号未开通导出权限" placement="top">
              <span><el-button size="small" :disabled="!downloadable" :loading="busy === `aid-${previewAidKind}-docx`" @click="downloadAid(previewAidKind, 'docx')">下载 Word</el-button></span>
            </el-tooltip>
            <el-tooltip :disabled="downloadable" content="当前只读账号未开通导出权限" placement="top">
              <span><el-button size="small" :disabled="!downloadable" :loading="busy === `aid-${previewAidKind}-pdf`" @click="downloadAid(previewAidKind, 'pdf')">下载 PDF</el-button></span>
            </el-tooltip>
          </span>
          <span v-else-if="previewIsEdpbEn && project" class="preview-meta-actions">
            <el-tooltip :disabled="downloadable" content="当前只读账号未开通导出权限" placement="top">
              <span><el-button size="small" :disabled="!downloadable" :loading="busy === 'dl-edpb-docx'" @click="downloadEdpbEn(project.documents.find((x) => x.code === 'dpia') || project.documents[0], 'docx')">下载 Word</el-button></span>
            </el-tooltip>
            <el-tooltip :disabled="downloadable" content="当前只读账号未开通导出权限" placement="top">
              <span><el-button size="small" :disabled="!downloadable" :loading="busy === 'dl-edpb-pdf'" @click="downloadEdpbEn(project.documents.find((x) => x.code === 'dpia') || project.documents[0], 'pdf')">下载 PDF</el-button></span>
            </el-tooltip>
          </span>
        </div>
        <div v-if="viewingVersionNo !== null" class="preview-version-banner" data-testid="doc-preview-version-banner">
          <span>正在查阅历史存档：第 {{ viewingVersionNo }} 版（只读预览，不会影响当前生效版本）</span>
          <el-button size="small" plain data-testid="doc-preview-version-exit" @click="viewingVersionNo = null; viewingVersionBlocks = []">返回当前生效版</el-button>
        </div>
        <div v-if="showVersionHistory && previewVersions.length" class="preview-history" data-testid="doc-preview-versions">
          <div v-for="ver in previewVersions" :key="ver.version_no" class="preview-history-item preview-version-row" :data-testid="`doc-version-item-${ver.version_no}`">
            <div class="preview-version-main">
              <b>第 {{ ver.version_no }} 版</b>
              <span v-if="ver.is_current" class="ver-cur-tag">当前生效</span>
              <span class="muted">{{ localTime(ver.created_at) }}<template v-if="ver.user_name"> · {{ ver.user_name }}</template> · {{ ver.note }}</span>
            </div>
            <div class="preview-version-actions">
              <el-button size="small" link type="primary" :data-testid="`doc-version-view-${ver.version_no}`" @click="inspectVersion(ver)">{{ viewingVersionNo === ver.version_no ? '正在查阅' : ver.is_current ? '查看当前版' : '按日期查阅' }}</el-button>
              <el-button v-if="editable && !locked && !ver.is_current" size="small" link type="primary" :loading="previewSaving" :data-testid="`doc-version-rollback-${ver.version_no}`" @click="rollbackToVersion(ver)">回退到此版</el-button>
            </div>
          </div>
        </div>
        <div v-if="showEditHistory && previewEdits.length" class="preview-history" data-testid="doc-preview-history">
          <div v-for="(ed, ei) in previewEdits" :key="ei" class="preview-history-item">
            <div class="preview-history-head"><b>{{ ed.place }}</b><span class="muted">{{ ed.user_name }} · {{ localTime(ed.at) }}</span></div>
            <div class="preview-history-diff"><span class="old">{{ ed.before }}</span><span class="arr">→</span><span class="new">{{ ed.after }}</span></div>
          </div>
        </div>
        <BlocksView v-if="displayedPreviewBlocks.length" :blocks="displayedPreviewBlocks" hide-title :outline="previewHasOutline" :editable="viewingVersionNo === null && previewEditMode && editable && !locked && !previewIsEdpbEn" :saving="previewSaving" @edit="savePreviewEdit" />
      </div>
    </el-drawer>
    <el-dialog v-model="compareVisible" title="合规项目方案对比" width="min(980px, 94vw)" append-to-body destroy-on-close data-testid="doc-compare-dialog">
      <div v-loading="compareLoading" class="cmp-wrap">
        <div class="cmp-bar">
          <div class="cmp-pick">
            <span class="cmp-pick-label">方案 A</span>
            <el-select v-model="compareLeftId" placeholder="选择项目 A" style="flex: 1" data-testid="doc-compare-left" @change="loadComparePair">
              <el-option v-for="it in items" :key="it.id" :label="it.title" :value="it.id" />
            </el-select>
          </div>
          <el-button size="small" @click="swapCompare">对调</el-button>
          <div class="cmp-pick">
            <span class="cmp-pick-label">方案 B</span>
            <el-select v-model="compareRightId" placeholder="选择项目 B" style="flex: 1" data-testid="doc-compare-right" @change="loadComparePair">
              <el-option v-for="it in items" :key="it.id" :label="it.title" :value="it.id" />
            </el-select>
          </div>
        </div>

        <table v-if="compareLeft && compareRight" class="cmp-table" data-testid="doc-compare-table">
          <thead>
            <tr>
              <th class="cmp-col-dim">对比维度</th>
              <th>
                <div class="cmp-th-head">
                  <span>{{ compareLeft.title }}</span>
                  <button type="button" class="cmp-jump" @click="jumpFromCompare(compareLeft.id)">打开</button>
                </div>
              </th>
              <th>
                <div class="cmp-th-head">
                  <span>{{ compareRight.title }}</span>
                  <button type="button" class="cmp-jump" @click="jumpFromCompare(compareRight.id)">打开</button>
                </div>
              </th>
            </tr>
          </thead>
          <tbody>
            <tr :class="{ 'cmp-diff': cmpRouteLine(compareLeft) !== cmpRouteLine(compareRight) }">
              <th>跨境流向与场景</th>
              <td>{{ cmpRouteLine(compareLeft) }}</td>
              <td>{{ cmpRouteLine(compareRight) }}</td>
            </tr>
            <tr :class="{ 'cmp-diff': cmpStatutoryRoute(compareLeft) !== cmpStatutoryRoute(compareRight) }">
              <th>发出国法定路径</th>
              <td>{{ cmpStatutoryRoute(compareLeft) }}</td>
              <td>{{ cmpStatutoryRoute(compareRight) }}</td>
            </tr>
            <tr :class="{ 'cmp-diff': compareLeft.project_verdict?.label !== compareRight.project_verdict?.label }">
              <th>场景综合判定结论</th>
              <td>
                <div v-if="compareLeft.project_verdict" class="cmp-verdict-cell">
                  <span class="verdict-pill" :class="`tone-${compareLeft.project_verdict.tone}`">{{ compareLeft.project_verdict.label }}</span>
                  <p>{{ compareLeft.project_verdict.text }}</p>
                </div>
                <span v-else class="muted">待完善必填要素</span>
              </td>
              <td>
                <div v-if="compareRight.project_verdict" class="cmp-verdict-cell">
                  <span class="verdict-pill" :class="`tone-${compareRight.project_verdict.tone}`">{{ compareRight.project_verdict.label }}</span>
                  <p>{{ compareRight.project_verdict.text }}</p>
                </div>
                <span v-else class="muted">待完善必填要素</span>
              </td>
            </tr>
            <tr :class="{ 'cmp-diff': cmpRiskSummary(compareLeft) !== cmpRiskSummary(compareRight) }">
              <th>风险等级与压降进度</th>
              <td>{{ cmpRiskSummary(compareLeft) }}</td>
              <td>{{ cmpRiskSummary(compareRight) }}</td>
            </tr>
            <tr :class="{ 'cmp-diff': compareDocDiff.onlyA.length > 0 || compareDocDiff.onlyB.length > 0 }">
              <th>本次应出文件（份数与差异）</th>
              <td>
                <div><strong>{{ compareLeft.ready_count }} / {{ cmpRequiredDocs(compareLeft).length }} 份已就绪</strong></div>
                <div v-if="compareDocDiff.onlyA.length" class="cmp-only">仅本方案必办：{{ compareDocDiff.onlyA.join('、') }}</div>
              </td>
              <td>
                <div><strong>{{ compareRight.ready_count }} / {{ cmpRequiredDocs(compareRight).length }} 份已就绪</strong></div>
                <div v-if="compareDocDiff.onlyB.length" class="cmp-only">仅本方案必办：{{ compareDocDiff.onlyB.join('、') }}</div>
              </td>
            </tr>
            <tr :class="{ 'cmp-diff': cmpAdmissionSummary(compareLeft).label !== cmpAdmissionSummary(compareRight).label }">
              <th>海外属地准入结论</th>
              <td>
                <div class="cmp-verdict-cell">
                  <span v-if="cmpAdmissionSummary(compareLeft).tone" class="verdict-pill" :class="`tone-${cmpAdmissionSummary(compareLeft).tone}`">{{ cmpAdmissionSummary(compareLeft).label }}</span>
                  <strong v-else>{{ cmpAdmissionSummary(compareLeft).label }}</strong>
                  <p v-if="cmpAdmissionSummary(compareLeft).detail">{{ cmpAdmissionSummary(compareLeft).detail }}</p>
                </div>
              </td>
              <td>
                <div class="cmp-verdict-cell">
                  <span v-if="cmpAdmissionSummary(compareRight).tone" class="verdict-pill" :class="`tone-${cmpAdmissionSummary(compareRight).tone}`">{{ cmpAdmissionSummary(compareRight).label }}</span>
                  <strong v-else>{{ cmpAdmissionSummary(compareRight).label }}</strong>
                  <p v-if="cmpAdmissionSummary(compareRight).detail">{{ cmpAdmissionSummary(compareRight).detail }}</p>
                </div>
              </td>
            </tr>
            <tr>
              <th>逐项文件结论对照</th>
              <td colspan="2" class="cmp-sub-cell">
                <table class="cmp-sub-table">
                  <thead>
                    <tr>
                      <th>合规文件</th>
                      <th>方案 A</th>
                      <th>方案 B</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr v-for="row in compareDocDiff.rows" :key="row.code" :class="{ 'cmp-diff': row.diff }">
                      <td>{{ row.name }}</td>
                      <td><span v-if="row.toneA" class="verdict-pill" :class="`tone-${row.toneA}`">{{ row.labelA }}</span><span v-else>{{ row.labelA }}</span></td>
                      <td><span v-if="row.toneB" class="verdict-pill" :class="`tone-${row.toneB}`">{{ row.labelB }}</span><span v-else>{{ row.labelB }}</span></td>
                    </tr>
                  </tbody>
                </table>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </el-dialog>
  </div>
</template>

<style scoped>
.doc.flash, .q.flash { outline: 2px solid var(--el-color-primary); outline-offset: 2px; background: #f8fbff; transition: outline-color .4s, background .4s; }
.docs-page { min-height: calc(100vh - 100px); }
/* 第 7.5 项 规则 17–19：三栏——左项目列表可收起，中间填写区占主宽度，右侧助手默认收成竖条 */
.docs-wrap { position: relative; display: flex; gap: 0; min-height: 640px; background: #fff; border: 1px solid var(--cy-line); border-radius: 10px; overflow: hidden; }
.side { width: 264px; flex: none; border-right: 1px solid var(--cy-line); display: flex; flex-direction: column; background: #fff; transition: width .2s; }
.side.collapsed { width: 48px; background: var(--cy-surface-sunken); }
.rail-open { display: flex; flex-direction: column; align-items: center; gap: 10px; width: 100%; padding: 14px 0; border: 0; background: transparent; color: var(--cy-text-2); cursor: pointer; }
.rail-open svg { width: 16px; height: 16px; }
.rail-open span { writing-mode: vertical-rl; letter-spacing: .2em; font-size: 14px; }
.rail-open:hover { color: var(--el-color-primary); }
.side-top { display: flex; justify-content: space-between; align-items: center; gap: 8px; padding: 12px; border-bottom: 1px solid var(--cy-line); }
.side-top-actions { display: inline-flex; align-items: center; gap: 6px; }
.side-top-actions .cy-rail-toggle { width: 28px; height: 28px; }
.archive-bar { padding: 4px 12px 0; display: flex; align-items: center; justify-content: space-between; gap: 8px; flex-wrap: wrap; }
.archive-toggle { border: 0; background: transparent; color: var(--cy-text-2); font-size: 13px; cursor: pointer; padding: 4px 0; }
.archive-toggle:hover { color: #2563eb; }
.batch-bar { display: inline-flex; align-items: center; gap: 8px; }
.batch-link { border: 0; background: transparent; color: var(--cy-text-3); font-size: 13px; cursor: pointer; padding: 2px 0; }
.batch-link:hover { color: #2563eb; text-decoration: underline; }
.batch-link.danger:hover { color: #dc2626; }
.list { overflow-y: auto; padding: 8px; flex: 1; }
.item { position: relative; padding: 10px 12px; border-radius: 8px; cursor: pointer; margin-bottom: 4px; }
.item:hover, .item.on { background: #eff6ff; }
.item .t { font-size: 14px; font-weight: 600; color: var(--cy-text-1); line-height: 1.5; }
.item .sub { font-size: 13px; color: var(--cy-text-3); margin-top: 4px; display: flex; justify-content: space-between; align-items: center; gap: 3px 8px; flex-wrap: wrap; }
.item .tools { display: none; gap: 6px; flex: none; margin-left: auto; }
.item:hover .tools, .item.on .tools { display: inline-flex; }
.item .tools button { border: 0; background: transparent; color: var(--cy-text-3); font-size: 13px; cursor: pointer; padding: 0 2px; }
.item .tools button:hover { color: #2563eb; }
.item .actions { margin-top: 6px; display: flex; gap: 6px; }
.main { flex: 1; min-width: 0; padding: 24px 32px; overflow-y: auto; max-height: calc(100vh - 100px); container-type: inline-size; }
.muted { color: var(--cy-text-3); font-size: 13px; }
.pad { padding: 8px 6px; line-height: 1.7; }
.lead { color: #475569; line-height: 1.8; max-width: 720px; }
.start-form { max-width: 760px; margin-top: 12px; }
.row2 { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
.start-form :deep(.el-select), .edit-route :deep(.el-select) { width: 100%; }
.hint { color: var(--cy-text-2); font-size: 13px; margin-top: 4px; display: block; width: 100%; }
/* 规则 20：中间区够宽（≥ 760px）时填写项和依据说明左右并排约 2:1，窄了上下排 */
.q-grid { display: grid; grid-template-columns: minmax(0, 1fr); gap: 16px; align-items: start; }
.q-grid > .q-main { min-width: 0; }
@container (min-width: 760px) { .q-grid { grid-template-columns: minmax(0, 2fr) minmax(240px, 1fr); gap: 24px; } .q:not(.active) .q-grid { grid-template-columns: minmax(0, 1fr); } }
.q-carried { border: 0; background: #ecfdf5; color: #047857; font-size: 13px; border-radius: 4px; padding: 1px 6px; cursor: pointer; margin-left: 6px; }
.q-carried:hover { text-decoration: underline; }
/* 平台检查结果：与已从……带入同一行，用琥珀/红色区分 */
.q-issue { border: 1px solid #fcd34d; background: #fffbeb; color: #92400e; font-size: 13px; border-radius: 4px; padding: 1px 6px; cursor: pointer; margin-left: 6px; text-align: left; line-height: 1.6; }
.q-issue.must { border-color: #fca5a5; background: #fef2f2; color: #b91c1c; }
.q-issue em, .final-item em.lv { font-style: normal; font-weight: 600; margin-right: 4px; }
.q-issue:hover { text-decoration: underline; }
.step-issues { margin: 4px 0 8px; display: flex; flex-wrap: wrap; gap: 6px; }
.step-issues .q-issue { margin-left: 0; }
.issue-link { color: #b45309; margin-left: 10px; }
.final-list .final-item { text-align: left; }
.final-item em.lv.must { color: #b91c1c; }
.final-item em.lv.advise { color: #b45309; }
.q-quote { margin: 4px 0 0; font-size: 13px; color: var(--cy-text-2); background: #f8fafc; border-left: 3px solid #cbd5e1; padding: 4px 8px; white-space: pre-line; }
.head { margin-bottom: 16px; }
.head h2 { margin: 0 0 6px; font-size: 18px; line-height: 1.4; }
.head-row { display: flex; justify-content: space-between; align-items: center; gap: 12px; flex-wrap: wrap; }
.route { margin: 0; color: #475569; font-size: 13px; display: flex; align-items: center; gap: 10px; }
.head-actions { display: flex; gap: 8px; flex-wrap: wrap; }
.head-actions .el-button + .el-button { margin-left: 0; }
.basis-note { margin: 6px 0 0; }
.third-country-box { margin-top: 8px; padding: 8px 12px; border-radius: 8px; background: #fffbeb; border: 1px solid #fde68a; font-size: 13px; color: #334155; line-height: 1.65; }
.tc-head, .signoff-strip { display: flex; justify-content: space-between; align-items: baseline; gap: 8px 14px; flex-wrap: wrap; }
.tc-actions { display: inline-flex; gap: 10px; align-items: center; flex-wrap: wrap; }
.tc-body { margin-top: 8px; padding-top: 8px; border-top: 1px dashed #fcd34d; }
.tc-note { margin: 0 0 6px; color: #78350f; }
.tc-list { margin: 0 0 6px 18px; padding: 0; }
.tc-list li { margin-bottom: 4px; }
.tc-import { margin: 0; color: #065f46; background: #ecfdf5; border-radius: 6px; padding: 6px 10px; }
.signoff-strip { margin-top: 8px; padding: 7px 12px; border-radius: 8px; background: #f8fafc; border: 1px solid #e2e8f0; font-size: 13px; color: #334155; line-height: 1.6; }
.density-box { margin-top: 6px; padding: 6px 10px; border-radius: 6px; background: #eff6ff; border: 1px solid #bfdbfe; color: #1e3a8a; font-size: 13px; line-height: 1.6; display: flex; justify-content: space-between; align-items: baseline; gap: 8px; flex-wrap: wrap; }
.density-summary { color: #92400e; }
.edit-route { background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 8px; padding: 14px 16px 10px; margin-bottom: 14px; }
/* 规则 12：文件列表统一行——名称说明在左，状态固定宽度列左对齐，操作固定宽度列右对齐 */
.docs { border: 1px solid var(--cy-line); border-radius: 10px; overflow: hidden; }
.docs-group-head { display: flex; justify-content: space-between; align-items: baseline; flex-wrap: wrap; gap: 6px 12px; padding: 10px 18px; background: #f8fafc; border-bottom: 1px solid var(--cy-line); font-size: 13px; }
.docs-group-head b { color: var(--cy-text-1); font-size: 14px; }
.docs-col-head { display: grid; grid-template-columns: minmax(0, 1fr) 92px 136px 296px; column-gap: 16px; padding: 7px 18px; background: #f8fafc; border-bottom: 1px solid var(--cy-line); font-size: 13px; color: #64748b; }
.docs-col-head .col-act { text-align: right; }
.na-group-toggle { width: 100%; display: flex; justify-content: space-between; align-items: center; gap: 10px; padding: 10px 18px; border: 0; border-top: 1px solid var(--cy-line); background: #f8fafc; color: var(--cy-text-2); font: inherit; font-size: 13px; cursor: pointer; text-align: left; }
.na-group-toggle:hover { background: #f1f5f9; color: var(--cy-text-1); }
.na-toggle-act { color: var(--el-color-primary); font-size: 13px; white-space: nowrap; flex-shrink: 0; }
.na-docs-wrap { border-top: 1px solid var(--cy-line); }
.doc { display: grid; grid-template-columns: minmax(0, 1fr) 92px 136px 296px; column-gap: 16px; align-items: start; padding: 14px 18px; border-bottom: 1px solid var(--cy-line); }
.doc-verdict { display: flex; align-items: center; padding-top: 2px; min-height: 24px; }
.verdict-pill { display: inline-block; font-size: 13px; font-weight: 600; padding: 2px 10px; border-radius: 999px; line-height: 20px; white-space: nowrap; }
.verdict-pill.tone-done { background: #dcfce7; color: #166534; }
.verdict-pill.tone-warn { background: #fef3c7; color: #92400e; }
.verdict-pill.tone-danger { background: #fee2e2; color: #991b1b; }
@container (max-width: 860px) {
  .docs-col-head { display: none; }
  .doc { grid-template-columns: minmax(0, 1fr) auto; row-gap: 8px; }
  .doc-verdict { grid-column: 1 / -1; }
  .doc-actions { grid-column: 1 / -1; justify-content: flex-start; }
}
/* 二轮 规则 12：全站统一行——第一行名称紧跟状态标签；第二行起说明；操作固定在右侧一列、与第一行顶部对齐；宽窄屏结构不变 */
.doc-title { gap: 8px; }
.basis-pill { font-size: 13px; padding: 1px 8px; border-radius: 999px; background: #eff6ff; color: #1d4ed8; font-weight: 600; }
.basis-pill.official { background: #ecfdf5; color: #047857; }
.basis-pill.eu { background: #eef2ff; color: #4338ca; }
.basis-pill.generic { background: #f1f5f9; color: #334155; }
.basis-pill.edited { background: #fffbeb; color: #b45309; border: 1px solid #fde68a; }
.basis-pill.quiet { background: #f1f5f9; color: var(--cy-text-3); }
.st-hint { display: inline-grid; place-items: center; width: 18px; height: 18px; margin-left: 6px; border-radius: 50%; background: var(--cy-tone-warn-bg); color: var(--cy-tone-warn-fg); font-size: 13px; font-weight: 600; cursor: help; vertical-align: 1px; }
.applies-short { font-size: 13px; color: var(--cy-text-2); }
.applies-short.unknown { color: var(--cy-tone-warn-fg); }
.applies-short.na { color: var(--cy-text-3); }
.applies-short.manual { color: #1d4ed8; font-weight: 600; }
.doc-status { display: inline-flex; align-items: center; justify-self: start; padding-top: 2px; }
.why-link { order: 9; border: 0; background: none; padding: 0; font: inherit; font-size: 13px; color: var(--el-color-primary); cursor: pointer; }
.route-ask { display: inline-flex; flex-wrap: wrap; align-items: center; gap: 8px; }
.doc-title b { font-size: 15px; }
.doc-main { flex: 1; min-width: 0; }
.doc:last-child { border-bottom: 0; }
.doc-title { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
.doc-state { font-size: 13px; margin-top: 4px; color: var(--cy-text-2); }
.doc-state.ready { color: #059669; }
.doc-state.needs_paper { color: #d97706; }
.basis-updated { font-size: 13px; color: #b45309; margin-top: 4px; line-height: 1.6; }
.officials { list-style: none; margin: 8px 0 0; padding: 0; display: flex; flex-direction: column; gap: 6px; }
.officials li { display: flex; align-items: center; justify-content: space-between; gap: 10px; padding: 8px 10px; background: #f7faf8; border: 1px solid #e3efe7; border-radius: 6px; }
.of-main { display: flex; flex-direction: column; min-width: 0; }
.of-name { font-size: 14px; font-weight: 600; color: #14532d; }
.of-sub { font-size: 13px; color: var(--cy-text-2); line-height: 1.5; }
.of-actions { display: flex; gap: 6px; flex: none; }
.of-actions .el-button + .el-button { margin-left: 0; }
.of-note { margin: 6px 0 0; font-size: 13px; color: var(--cy-text-2); }
.doc-state.as-link { background: none; border: 0; padding: 0; font: inherit; font-size: 13px; cursor: pointer; text-align: left; }
.doc-state.as-link:hover, .doc-state.as-link:focus-visible { color: #1d4ed8; text-decoration: underline; outline: none; }
.next-step { margin: 8px 0 0; font-size: 13px; color: #334155; }
.inline-link { font: inherit; color: #1d4ed8; background: none; border: 0; padding: 0 0 0 4px; cursor: pointer; white-space: nowrap; flex-shrink: 0; }
.inline-link:hover, .inline-link:focus-visible { text-decoration: underline; outline: none; }
.inline-link:disabled { color: #94a3b8; cursor: not-allowed; text-decoration: none; }
.err { color: #dc2626; font-size: 13px; margin-top: 4px; }
.doc-actions { display: flex; gap: 6px; flex-wrap: wrap; justify-content: flex-end; align-items: center; }
.doc-actions .el-button + .el-button { margin-left: 0; }
.diagnosis-card { margin-top: 18px; padding: 12px 16px; border-radius: 10px; background: #fffbeb; border: 1px solid #fde68a; font-size: 13px; line-height: 1.7; color: #334155; }
.diagnosis-card.is-collapsed { padding: 10px 16px; }
.diag-head { display: flex; justify-content: space-between; align-items: center; gap: 16px; cursor: pointer; }
.diag-head b { color: #92400e; font-size: 14px; }
.diag-title-wrap { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; min-width: 0; flex: 1; }
.diag-pill { display: inline-flex; align-items: center; padding: 2px 8px; border-radius: 999px; font-size: 13px; line-height: 18px; font-weight: 600; }
.diag-pill.kind { background: #fef3c7; color: #92400e; border: 1px solid #fde68a; }
.diag-pill.redact { background: #ecfdf5; color: #065f46; border: 1px solid #a7f3d0; }
.diag-toggle { flex: 0 0 auto; white-space: nowrap; font: inherit; font-size: 13px; font-weight: 600; color: #1d4ed8; background: none; border: 0; padding: 2px 0; cursor: pointer; }
.diag-toggle:hover, .diag-toggle:focus-visible { text-decoration: underline; outline: none; }
.diag-redact-detail { margin: 8px 0 4px; font-size: 13px; color: #065f46; }
.file-guide { margin: 0 0 8px; font-size: 13px; color: #475569; line-height: 1.65; }
.diag-summary { margin: 6px 0 8px; color: #1e293b; }
.diag-block { margin-top: 6px; }
.diag-label { font-weight: 600; color: #78350f; }
.diag-block ul { margin: 4px 0 0 18px; padding: 0; }
.preview-body { min-height: 280px; padding: 0 4px 20px; }
.preview-meta { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; margin-bottom: 14px; padding-bottom: 10px; border-bottom: 1px solid var(--cy-line); }
.preview-meta-actions { margin-left: auto; display: inline-flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.preview-history { margin-bottom: 14px; padding: 10px 14px; border-radius: 8px; background: #f8fafc; border: 1px solid #e2e8f0; display: flex; flex-direction: column; gap: 8px; max-height: 220px; overflow-y: auto; }
.preview-history-item { padding-bottom: 6px; border-bottom: 1px dashed #e2e8f0; font-size: 13px; }
.preview-history-item:last-child { padding-bottom: 0; border-bottom: 0; }
.preview-history-head { display: flex; justify-content: space-between; gap: 10px; margin-bottom: 2px; }
.preview-history-diff { display: flex; flex-wrap: wrap; gap: 6px; align-items: baseline; line-height: 1.6; }
.preview-history-diff .old { color: #64748b; text-decoration: line-through; }
.preview-history-diff .arr { color: #94a3b8; }
.preview-history-diff .new { color: #0f766e; font-weight: 600; }
.preview-version-banner { margin-bottom: 12px; padding: 8px 14px; border-radius: 6px; background: #f8fafc; border: 1px solid #cbd5e1; display: flex; justify-content: space-between; align-items: center; gap: 12px; font-size: 13px; color: #0f172a; }
.preview-version-row { display: flex; justify-content: space-between; align-items: center; gap: 12px; flex-wrap: wrap; }
.preview-version-main { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; font-size: 13px; color: #0f172a; }
.ver-cur-tag { padding: 1px 8px; border-radius: 4px; border: 1px solid #0f172a; background: #ffffff; color: #0f172a; font-size: 13px; font-weight: 600; }
.preview-version-actions { display: inline-flex; align-items: center; gap: 8px; }
.summary { margin-bottom: 14px; }
.summary-line { display: flex; gap: 12px; align-items: baseline; flex-wrap: wrap; margin-bottom: 6px; }
.more { margin-top: 22px; }
.more h3 { margin: 0 0 10px; }
.ways { display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; }
.way { text-align: left; border: 1px solid #e2e8f0; background: #fff; border-radius: 10px; padding: 12px 14px; cursor: pointer; display: flex; flex-direction: column; gap: 4px; }
.way b { font-size: 14px; color: #1e293b; }
.way span { font-size: 13px; color: var(--cy-text-2); }
.way:hover { border-color: #93c5fd; }
.way.on { border-color: #2563eb; background: #eff6ff; }
.panel { margin-top: 12px; background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 10px; padding: 14px 16px; }
.step h4 { margin: 8px 0 2px; font-size: 16px; }
.step-head { display: flex; gap: 10px; align-items: center; }
.choice-row { display: flex; gap: 10px; margin: 8px 0 8px; flex-wrap: wrap; }
.choice-row .el-button + .el-button { margin-left: 0; }
.all-link { margin: 10px 0 0; }
.easy, .pending { margin-top: 22px; background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 10px; padding: 14px 16px; }
.easy h3, .pending h3 { margin: 0 0 6px; }
.easy-actions { display: flex; gap: 10px; align-items: center; margin-top: 10px; flex-wrap: wrap; }
.sg { display: flex; justify-content: space-between; gap: 12px; padding: 10px 0; border-top: 1px dashed #e2e8f0; }
.sg-general { margin-left: 8px; color: #b45309; font-size: 13px; }
.item .sub span:first-child { white-space: nowrap; flex-shrink: 0; }
.sg-text { margin: 4px 0 2px; color: #1e293b; font-size: 13px; line-height: 1.7; }
.sg-quote { margin: 0; color: var(--cy-text-3); font-size: 13px; line-height: 1.6; }
.draft-bar { display: flex; justify-content: space-between; gap: 12px; align-items: center; padding: 6px 4px 10px; }
.sg-actions { display: flex; gap: 6px; align-items: flex-start; flex-shrink: 0; }
.fill { margin-top: 22px; }
.fill-head { display: flex; align-items: center; gap: 14px; flex-wrap: wrap; margin-bottom: 8px; }
.fill-head h3 { margin: 0; }
.q { padding: 8px 4px 12px; border-bottom: 1px dashed #e2e8f0; }
.q-head { display: flex; align-items: baseline; justify-content: flex-start; flex-wrap: wrap; gap: 6px 10px; }
.q-head > label { flex: 0 0 auto; white-space: nowrap; }
.q-head .q-issue, .q-head .q-carried { margin-left: 0; }
.q label { font-size: 15px; font-weight: 600; color: var(--cy-text-1); }
.q-unsure { border: 0; background: none; padding: 0; color: var(--cy-text-2); font-size: 13px; cursor: pointer; flex: none; margin-left: auto; }
.q-unsure:hover { color: #2563eb; }
.q-flag { color: #b45309; font-size: 13px; flex: none; margin-left: auto; }
.qnote { margin: 3px 0 8px; font-size: 13px; color: var(--cy-text-2); line-height: 1.65; }
.qnote .used { color: var(--cy-text-3); margin-left: 8px; white-space: nowrap; }
.qnote .used:first-child { margin-left: 0; }
.q-other { margin-top: 8px; }
.archived-banner { display: flex; gap: 12px; align-items: center; background: #fef3c7; color: #92400e; border: 1px solid #fde68a; padding: 8px 14px; border-radius: 8px; margin-bottom: 12px; font-size: 13px; }
@media (max-width: 900px) { .docs-wrap { flex-direction: column; } .side { width: auto; } .row2, .ways { grid-template-columns: 1fr; } }
.pending-bar { display: flex; align-items: center; gap: 10px; margin-top: 12px; padding: 10px 14px; border-radius: 8px; background: #eff6ff; border: 1px solid #bfdbfe; font-size: 13px; }
.pending-bar span { flex: 1; }
.state-counts { margin: 8px 0 0; font-size: 13px; color: #475569; }
.cn-route { margin-top: 10px; padding: 10px 14px; border-radius: 8px; background: #f1f5f9; border: 1px solid #e2e8f0; display: flex; flex-wrap: wrap; gap: 4px 10px; align-items: baseline; font-size: 13px; line-height: 1.7; }
.cn-route.security_assessment { background: #fffbeb; border-color: #fde68a; }
.cn-route.standard_contract { background: #f0f9ff; border-color: #bae6fd; }
.cn-route.exempt { background: #f0fdf4; border-color: #bbf7d0; }
.cn-route.overseas-route { background: #f8fafc; border-color: #cbd5e1; }
.cn-route small { width: 100%; font-size: 13px; }
.advisor-rec { width: 100%; margin: 2px 0 0; padding-top: 6px; border-top: 1px dashed rgba(100, 116, 139, 0.25); color: #1e293b; font-weight: 400; line-height: 1.65; }
.risk-remediate { display: flex; flex-wrap: wrap; gap: 6px 10px; align-items: center; margin-top: 6px; font-size: 13px; }
.risk-remediate .muted { flex: 1 1 220px; min-width: 0; white-space: normal; }
.validity { width: 100%; display: flex; flex-direction: column; gap: 4px; padding-top: 6px; border-top: 1px dashed #e5c97a; }
.validity .period { display: flex; flex-wrap: wrap; gap: 2px 10px; align-items: center; }
.validity .p-name { font-weight: 600; min-width: 7em; }
.validity .p-ask { color: #b45309; }
.validity .p-date { margin-left: auto; display: inline-flex; gap: 6px; align-items: center; color: #475569; }
.validity .period.soon b { color: #b45309; }
.validity .period.over b { color: #dc2626; }
.applies { display: block; font-size: 13px; font-weight: normal; color: #0f766e; margin-top: 4px; line-height: 1.65; }
.applies.na { color: var(--cy-text-3); }
.applies.unknown { color: #b45309; }
.doc-state.missing.quiet { color: var(--cy-text-3); }
.doc.na { background: #f8fafc; }
.doc.na .doc-title b { color: var(--cy-text-2); }
.doc-state-row { display: flex; gap: 8px; align-items: baseline; flex-wrap: wrap; margin-top: 4px; }
.doc-state-row .doc-state { margin-top: 0; }
/* 规则 15：状态色全站一套（灰 未开始 / 蓝 填写中 / 橙 待生成论述 / 绿字 已就绪） */
.st { display: inline-block; font-size: 13px; padding: 2px 12px; border-radius: 999px; line-height: 20px; white-space: nowrap; background: var(--cy-tone-muted-bg); color: var(--cy-tone-muted-fg); flex: none; }
.st.filling { background: var(--cy-tone-active-bg); color: var(--cy-tone-active-fg); }
.st.needs_paper { background: var(--cy-tone-warn-bg); color: var(--cy-tone-warn-fg); }
.st.ready { background: transparent; color: var(--cy-tone-done-fg); padding-inline: 0; font-weight: 600; }
.st.quiet { background: var(--cy-tone-muted-bg); color: var(--cy-text-3); }
.final-gate { margin-top: 6px; font-size: 13px; color: #475569; display: flex; gap: 10px; flex-wrap: wrap; align-items: baseline; }
.final-list { margin-top: 8px; padding: 10px 12px; background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 8px; font-size: 13px; }
.final-group { display: flex; flex-wrap: wrap; gap: 6px 10px; align-items: baseline; padding: 3px 0; }
.final-group b { color: #334155; font-weight: 600; margin-right: 4px; }
.final-item { font: inherit; font-size: 13px; color: #1d4ed8; background: none; border: 0; padding: 0; cursor: pointer; }
.final-item:hover, .final-item:focus-visible { text-decoration: underline; outline: none; }
.final-item.plain { color: #475569; cursor: default; }
.tier-missing { margin-left: 10px; font: inherit; font-size: 13px; color: #b45309; background: none; border: 0; padding: 0; cursor: pointer; }
.tier-missing:hover, .tier-missing:focus-visible { color: #1d4ed8; text-decoration: underline; outline: none; }
.q-na { font-size: 13px; color: #64748b; }
.q-need { font-size: 13px; color: #b45309; }
.risk-evo-strip { margin-top: 10px; padding: 9px 12px; border-radius: 8px; border: 1px solid #cbd5e1; background: #f8fafc; display: flex; flex-wrap: wrap; gap: 6px 10px; align-items: center; font-size: 13px; color: #334155; }
.evo-pill { padding: 1px 8px; border-radius: 999px; font-size: 13px; font-weight: 600; white-space: nowrap; }
.evo-pill.ok { background: #dcfce7; color: #166534; }
.evo-jump { margin-left: auto; }
.verdict-overview-card { display: grid; grid-template-columns: 1fr 1fr 1.45fr; gap: 12px; margin-bottom: 14px; padding: 12px 16px; border: 1px solid #cbd5e1; border-radius: 10px; background: #f8fafc; }
.vo-col { display: flex; flex-direction: column; gap: 4px; min-width: 0; padding-right: 12px; border-right: 1px dashed #cbd5e1; }
.vo-col-verdict { border-right: 0; padding-right: 0; }
.vo-label { font-size: 13px; color: #64748b; }
.vo-val { font-size: 14px; font-weight: 600; color: #0f172a; line-height: 1.5; }
.vo-sub { font-size: 13px; color: #475569; line-height: 1.5; }
.vo-verdict-head { display: flex; align-items: center; justify-content: space-between; gap: 8px; flex-wrap: wrap; }
.vo-verdict-text { margin: 2px 0 0; font-size: 13px; color: #1e293b; line-height: 1.6; }
.main-risk-panel { margin-top: 10px; padding: 12px 14px; border: 1px solid #e2e8f0; border-radius: 8px; background: #fff; display: flex; flex-direction: column; gap: 10px; }
.mrp-head { display: flex; justify-content: space-between; align-items: baseline; gap: 8px 12px; flex-wrap: wrap; font-size: 13px; }
.mrp-head b { font-size: 14px; color: #0f172a; }
.main-risk-matrix table { border-collapse: separate; border-spacing: 4px; width: 100%; max-width: 720px; table-layout: fixed; font-size: 13px; }
.main-risk-matrix th { font-weight: 600; color: #475569; text-align: left; font-size: 13px; padding: 4px 6px; white-space: nowrap; }
.main-risk-matrix th.corner { width: 118px; color: #64748b; font-weight: 400; }
.main-risk-matrix td { border-radius: 6px; padding: 6px 8px; vertical-align: top; min-height: 42px; }
.main-risk-matrix td.rt-0 { background: #dcfce7; color: #166534; }
.main-risk-matrix td.rt-1 { background: #fef3c7; color: #92400e; }
.main-risk-matrix td.rt-2 { background: #fee2e2; color: #991b1b; }
.rt-grade { display: block; font-size: 13px; opacity: 0.82; margin-bottom: 3px; }
.rt-tags { display: flex; flex-wrap: wrap; gap: 4px; }
.rt-pill { display: inline-block; padding: 1px 6px; border-radius: 4px; font-size: 13px; line-height: 1.45; }
.rt-pill.active { background: rgba(255, 255, 255, 0.9); color: #0f172a; font-weight: 600; border: 1px solid rgba(15, 23, 42, 0.14); }
.rt-pill.moved { background: rgba(255, 255, 255, 0.55); color: #64748b; border: 1px dashed rgba(100, 116, 139, 0.45); }
@container (max-width: 760px) {
  .verdict-overview-card { grid-template-columns: 1fr; }
  .vo-col { border-right: 0; padding-right: 0; border-bottom: 1px dashed #cbd5e1; padding-bottom: 8px; }
  .vo-col-verdict { border-bottom: 0; padding-bottom: 0; }
}
.cmp-wrap { display: flex; flex-direction: column; gap: 14px; max-height: 76vh; overflow-y: auto; padding-right: 2px; }
.cmp-trigger { color: #1d4ed8 !important; font-weight: 600 !important; }
.cmp-bar { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.cmp-pick { flex: 1; min-width: 240px; display: flex; align-items: center; gap: 8px; }
.cmp-pick-label { font-size: 13px; font-weight: 600; color: #334155; white-space: nowrap; }
.cmp-table { width: 100%; border-collapse: collapse; table-layout: fixed; font-size: 13px; color: #0f172a; background: #fff; }
.cmp-table th, .cmp-table td { border: 1px solid #cbd5e1; padding: 10px 12px; vertical-align: top; text-align: left; line-height: 1.65; word-break: break-word; }
.cmp-table thead th { background: #f8fafc; font-weight: 600; font-size: 14px; }
.cmp-table tbody th { background: #f8fafc; font-weight: 600; color: #334155; }
.cmp-col-dim { width: 164px; }
.cmp-th-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 8px; }
.cmp-jump { flex: none; border: 0; background: none; padding: 0; color: #1d4ed8; font: inherit; font-size: 13px; font-weight: 400; cursor: pointer; }
.cmp-jump:hover { text-decoration: underline; }
.cmp-diff > td { background: #f8fbff; }
.cmp-verdict-cell { display: flex; flex-direction: column; align-items: flex-start; gap: 6px; }
.cmp-verdict-cell p { margin: 0; font-size: 13px; color: #1e293b; line-height: 1.6; }
.cmp-only { margin-top: 4px; font-size: 13px; color: #92400e; }
.cmp-sub-cell { padding: 0 !important; }
.cmp-sub-table { width: 100%; border-collapse: collapse; font-size: 13px; }
.cmp-sub-table th, .cmp-sub-table td { border: 0; border-bottom: 1px solid #e2e8f0; border-right: 1px solid #e2e8f0; padding: 7px 10px; }
.cmp-sub-table tr:last-child td { border-bottom: 0; }
.cmp-sub-table th:last-child, .cmp-sub-table td:last-child { border-right: 0; }
.cmp-sub-table thead th { background: #f8fafc; font-size: 13px; font-weight: 600; color: #475569; }
</style>
