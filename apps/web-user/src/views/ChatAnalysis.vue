<script setup lang="ts">
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  HOST_REPORT_LANGUAGES,
  startProgressStream,
  type StreamState,
  canEdit,
  canExport,
  inferHostLanguage,
  localTime,
  type HostReportLang,
  type ReportOutputLang,
} from '@cyshuhe/shared'
import { computed, nextTick, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '../api'
import AssessmentSwitch from '../components/AssessmentSwitch.vue'
import BlocksView, { type Block } from '../components/BlocksView.vue'
import { useAuth } from '../auth'
import { conversationStamp, conversationSignal, elapsedLabel, receiveExport } from '../chatFeedback'
import ConsultingReport from '../components/ConsultingReport.vue'
import LegalText from '../components/LegalText.vue'
import RichText from '../components/RichText.vue'
import type { Consulting } from '../consulting'
import { stripBold } from '../richText'
import type { LegalTerm } from '@cyshuhe/shared/src/legalText'
function readableMessage(text: string) {
  return text.replace(/信息已经齐备[\s\S]*下面会实时显示每个智能体的进度。/, '信息已齐备，正在查找适用法规并生成合规报告。')
    .replace('然后由多个智能体对照目标国现行法规,', '再对照目标国法规，')
    .replace('所有回答只用于本次评估。', '')
    .replaceAll('车研数合的合规助手', '车研数合的咨询助手')
    .replaceAll('要出合规文件请到合规评估页面', '如需生成正式合规评估文件，请切换到上方生成合规文件页签')
    .replace(/[（(]\s*(?:对话(?:编号|流水号)[:：]\s*)?CONV-[A-Za-z0-9_-]+\s*[)）]/g, '')
    .replace(/\bCONV-[A-Za-z0-9_-]+\b/g, '')
    .replace(/^#{1,4}\s+(.+)$/gm, '**$1**')
}
function cleanChatTitle(raw?: string | null) {
  const stripped = (raw || '')
    .replace(/[（(]\s*CONV-[A-Za-z0-9_-]+\s*[)）]/g, '')
    .replace(/\bCONV-[A-Za-z0-9_-]+\b/g, '')
    .trim()
  return stripped || '新对话'
}
// 助手回复里的站内链接：[文字](/documents?...)，只认站内的几个页面，其余按原样显示
const LINK = /\[([^\]\r\n]{1,20})\]\((\/(?:documents|knowledge|admission|forms)[^)\s]*)\)/g
function segments(text: string): { text: string; href?: string }[] {
  const out: { text: string; href?: string }[] = []
  let last = 0
  for (const m of text.matchAll(LINK)) {
    if (m.index! > last) out.push({ text: text.slice(last, m.index) })
    out.push({ text: m[1], href: m[2] })
    last = m.index! + m[0].length
  }
  if (last < text.length) out.push({ text: text.slice(last) })
  return out
}
function readableContent(text: string) {
  return text.replace('依据与合规义务（程序子串/数字核验通过；人工复核状态逐条标注）', '依据与合规义务')
    .replaceAll('；待核对（原文/译文尚未全部人工复核）', '')
    .replaceAll('；引用材料已复核（分析结论仍为机器草稿）', '')
}
function businessSummary(text: string) {
  return /多智能体|程序核验|tokens|MCP/.test(text)
    ? `本报告根据你的业务场景，对照适用法规形成八个维度的分析与建议。各章节可查看依据、修订内容并单独导出。` : text
}
const route = useRoute()
const router = useRouter()
const auth = useAuth()
let openRequest = 0

interface ChatMessage {
  id: string
  seq_no: number
  role: 'user' | 'assistant'
  content: string
  options: string[]
  slot: string | null
}

interface Finding {
  finding_id: string
  dimension_code: string
  business_fact: string
  clause_id: string | null
  document_title: string
  clause_number: string
  version_no: number
  compliance_obligation: string
  risk_level: string
  basis_quote: string
  basis_zh: string
  verification_status: string
}


interface Citation {
  source_review_status?: string
  translation_review_status?: string
  language?: string
  citation_tag: string
  clause_id: string | null
  document_title: string
  clause_number: string
  version_no: number
  quote_original: string
}

interface Dimension {
  findings?: Finding[]
  dimension_code: string
  dimension_title: string
  status: string
  risk_level: string
  content_markdown: string
  key_recommendations: string[]
  citations: Citation[]
  missing_gap_note: string | null
}

interface StaleImpact {
  dimension_code: string
  clause_ref: string
  amendment_summary: string
  action_required: string
}

interface ReportVersion {
  regulatory_updates?: Array<{ id: string; change_id: string; dimension_code: string; document_title: string; summary: string; state: string }>
  citation_policy: string
  citation_review_summary: { approved: number; pending: number; total: number; occurrences: number }
  source_integrity_alerts?: string[]
  version_no: number
  created_at?: string | null
  generated_at?: string | null
  status: string
  is_partial_sample: boolean
  change_note: string
  executive_summary: string
  dimensions: Dimension[]
  stale_impacts: StaleImpact[]
  consulting?: Consulting | null
}

interface TaskSummary {
  messages_count: number
  project_id?: string | null
  viewer_own?: boolean
  id: string
  conversation_code: string
  title: string
  status: string
  country_code: string
  latest_report_version: number
}

interface TaskDetail {
  archived?: boolean
  viewer_own?: boolean
  failure_summary?: {
    title: string
    summary: string
    issues: Array<{ label: string; dimension_codes: string[] }>
    next_action: string
    retained_citations: number
    report_version: number | null
  } | null
  activity?: {label: string; state: string; started_at: string} | null
  id: string
  conversation_code: string
  created_at?: string | null
  updated_at?: string | null
  current_active_node_id: string | null
  current_active_node_title: string | null
  title: string
  country_code: string
  country_name_zh: string
  status: string
  messages: ChatMessage[]
  findings: Finding[]
  progress: Array<{label: string; state: string}>
  report_versions: ReportVersion[]
  pending_user_facts: Array<{ id: string; fact_key: string; dimension_codes: string[] }>
  fact_followup_rounds: number
}

interface CitationCard {
  clause_id: string
  language?: string
  is_native_zh?: boolean
  section_path?: string | null
  source_review_status?: string
  translation_review_status?: string
  source_integrity_alerts?: string[]
  document_title: string
  clause_number: string
  version_no: number
  source_url: string | null
  original_text: string
  translated_text: string
  review_status: string
  terms: LegalTerm[]
  related_requirements: Array<{id: string; title: string; topic_label: string; quote: string}>
}
interface DiffPayload {
  from_version: number
  to_version: number
  dimension_diffs: Array<{
    dimension_code: string
    dimension_title: string
    changed: boolean
    from_markdown: string
    to_markdown: string
  }>
}

const RISK_LABEL: Record<string, string> = {
  HIGH: '高风险',
  MEDIUM: '中风险',
  LOW: '低风险',
  PENDING_VERIFICATION: '待核查',
}
const STATUS_LABEL: Record<string, string> = {
  completed: '已出报告',
  collecting_facts: '采集信息中',
  ready_to_run: '待启动',
  partial_sample: '已出报告',
  running: '分析中',
  awaiting_user_facts: '待补充情况',
  failed: '分析失败',
}
// 这两类问题直接点选即发送;事实类问题的示例只填入输入框,由用户按实际情况修改后再发送
const CHOICE_SLOTS = new Set(['country_code', 'scenario_type'])

const tasks = ref<TaskSummary[]>([])
const archivedTasks = ref<TaskSummary[]>([])
const showArchive = ref(false)
const detail = ref<TaskDetail | null>(null)
watch(() => detail.value?.id, () => loadAttachments())
const input = ref('')
const pendingSends = reactive(new Map<string, number>())
const sending = computed(() => !!detail.value && pendingSends.has(detail.value.id))
const clockNow = ref(Date.now())
const seen = ref<Record<string, string>>({})
const followStream = ref(true)
const exportJob = ref<{taskId: string; label: string; started: number; phase: 'preparing' | 'receiving'; received: number; total: number | null} | null>(null)
const currentExport = computed(() => exportJob.value?.taskId === detail.value?.id ? exportJob.value : null)
const sendElapsed = computed(() => elapsedLabel(pendingSends.get(detail.value?.id || '') || clockNow.value, clockNow.value))
const activityElapsed = computed(() => detail.value?.activity?.started_at
  ? elapsedLabel(Date.parse(detail.value.activity.started_at), clockNow.value) : '')
const exportElapsed = computed(() => elapsedLabel(exportJob.value?.started || clockNow.value, clockNow.value))
let mounted = true
let lastSidebarRefresh = 0
let polling = false
let listRequest = 0
const streamEl = ref<HTMLDivElement | null>(null)
const inputEl = ref<{ focus: () => void } | null>(null)

const reportVersionNo = ref(1)
const openChapters = ref<string[]>([])
const openExtras = ref<string[]>([])

const drawerVisible = ref(false)
const drawerLoading = ref(false)
const card = ref<CitationCard | null>(null)
const anchorQuote = ref('')

const editVisible = ref(false)
const editNote = ref('业务人员在线修订')
const editSummary = ref('')
const editDims = reactive<Record<string, string>>({})
const savingEdit = ref(false)

const diffVisible = ref(false)
const diffData = ref<DiffPayload | null>(null)

const status = computed(() => detail.value?.status ?? '')
const progressState = ref<StreamState>('idle')
let progressStream: {close: () => void} | undefined
let progressRefresh: ReturnType<typeof setTimeout> | undefined
const lastAssistant = computed(() => {
  const list = detail.value?.messages.filter((m) => m.role === 'assistant') ?? []
  return list[list.length - 1]
})
const showChips = computed(
  () =>
    ['collecting_facts', 'awaiting_user_facts'].includes(status.value) &&
    !!lastAssistant.value &&
    lastAssistant.value.options.length > 0 &&
    detail.value?.messages[detail.value.messages.length - 1]?.role === 'assistant',
)
const hasReport = computed(
  () => (detail.value?.report_versions.length ?? 0) > 0 &&
    !['running', 'awaiting_user_facts'].includes(status.value),
)
const report = computed<ReportVersion | null>(() => {
  const list = detail.value?.report_versions ?? []
  return list.find((r) => r.version_no === reportVersionNo.value) ?? list[0] ?? null
})
function reportGeneratedAt(r?: ReportVersion | null): string {
  if (!r) return ''
  return localTime(
    r.generated_at ||
    r.created_at ||
    r.consulting?.generated_at ||
    r.consulting?.created_at ||
    detail.value?.updated_at ||
    detail.value?.created_at ||
    null
  ) || ''
}
const versionFindings = computed(() => report.value?.dimensions.flatMap(d => d.findings || []) || [])
// 咨询版报告：先结论与建议，法条只在文末作为参考文献；旧报告仍按证据版展示
const consultingReady = computed(() =>
  ['ok', 'partial'].includes(report.value?.consulting?.status ?? ''))
// 面向业务人员的提示里不显示 D1…D8 这类内部编号，统一换成方面名称
const DIMENSION_NAMES: Record<string, string> = {
  D1: '属地数据法规体系',
  D2: '汽车数据定义与分类',
  D3: '数据跨境传输',
  D4: '用户授权与告知同意',
  D5: '数据存储与留存销毁',
  D6: '违规处罚与追责',
  D7: '市场准入',
  D8: '整改建议'
}
function dimensionName(code: string): string {
  const fromReport = report.value?.dimensions.find((d) => d.dimension_code === code)?.dimension_title
  return fromReport || DIMENSION_NAMES[code] || '相关方面'
}
const dimensionTexts = computed(() =>
  Object.fromEntries((report.value?.dimensions ?? []).map((d) => [d.dimension_code, d.content_markdown])))
const EXAMPLE_PROMPTS = [
  '我们想把一款 L4 自动驾驶 SUV 拿到新加坡做公共道路测试，需要满足哪些数据合规要求？',
  '车辆的语音和驾驶员状态数据要回传到中国云平台，在埃及要办什么手续？',
  '我们的车联网平台要把摩洛哥用户的车辆遥测数据同步到境外服务器，要怎么合规？',
]
const isViewer = computed(() => !canEdit(auth.user))
const downloadable = computed(() => canExport(auth.user))
function canManageTask(t: { viewer_own?: boolean } | null | undefined) {
  return canEdit(auth.user) || !!t?.viewer_own
}
const inputPlaceholder = computed(() => {
  if (status.value === 'running') return '正在分析业务场景，请稍候…'
  if (isViewer.value) {
    if (detail.value && !detail.value.viewer_own) {
      return '请输入法规咨询问题（将自动新建仅本人可见的咨询对话，不改动当前共享对话）…'
    }
    return '请输入法规咨询问题（Enter 发送，Shift+Enter 换行）'
  }
  if (status.value === 'awaiting_user_facts') return '请补充业务事实说明；暂未明确的事项可如实填写'
  if (status.value === 'completed' || status.value === 'partial_sample')
    return '当前为历史评估报告，可继续提问法规问题；如需生成正式合规文件，请切换至上方生成合规文件页签'
  const slot = lastAssistant.value?.slot
  if (slot === 'country_code') return `输入或点选目标国家，也可直接描述需求，例如：${EXAMPLE_PROMPTS[0]}`
  if (slot === 'vehicle_model') return '请输入车型或车队名称…'
  return '请输入汽车数据合规咨询问题，或上传制度文件进行法规对标（Enter 发送，Shift+Enter 换行）'
})
const inputDisabled = computed(() => sending.value || status.value === 'running' || archived.value)

function scrollBottom(force = false) {
  nextTick(() => {
    const el = streamEl.value
    if (!el || (!force && !followStream.value)) return
    const summary = el.querySelector<HTMLElement>('[data-testid="report-card"]')
    if (hasReport.value && summary) {
      el.scrollTop += summary.getBoundingClientRect().top - el.getBoundingClientRect().top - 20
    } else {
      el.scrollTop = el.scrollHeight
    }
  })
}

function trackScroll() {
  const el = streamEl.value
  if (el) followStream.value = el.scrollHeight - el.scrollTop - el.clientHeight < 100
}
function seenKey() { return `cyshuhe.chat-seen.v1.${auth.user?.id || 'session'}` }
function markSeen(task: TaskSummary) {
  seen.value[task.id] = conversationStamp(task)
  try { localStorage.setItem(seenKey(), JSON.stringify(seen.value)) } catch { /* Storage can be disabled. */ }
}
// 只有看过之后又有了新回复才算新动态；从没在本机看过的对话不标（换电脑或清缓存时不会满屏都是新动态）
function isUnread(task: TaskSummary) {
  const before = seen.value[task.id]
  return before !== undefined && before !== conversationStamp(task) && task.id !== detail.value?.id
}

function apply(d: TaskDetail) {
  const previousLatest = detail.value?.report_versions[0]?.version_no
  const followLatest = detail.value?.id !== d.id || reportVersionNo.value === previousLatest
  detail.value = d
  const summary: TaskSummary = {
    id: d.id, conversation_code: d.conversation_code, title: d.title, status: d.status,
    country_code: d.country_code, messages_count: d.messages.length,
    latest_report_version: d.report_versions[0]?.version_no || 0,
    viewer_own: d.viewer_own,
  }
  // 归档的对话只在已归档里，不进当前对话列表（打开它、刷新它都不会把它放回来）
  const index = tasks.value.findIndex(t => t.id === d.id)
  if (d.archived) {
    if (index >= 0) tasks.value.splice(index, 1)
  } else if (index >= 0) tasks.value[index] = summary
  else tasks.value.push(summary)
  if (document.visibilityState === 'visible') markSeen(summary)
  if (
    d.report_versions.length > 0 &&
    (followLatest || !d.report_versions.some((r) => r.version_no === reportVersionNo.value))
  ) {
    reportVersionNo.value = d.report_versions[0].version_no
  }
}

async function refreshTasks() {
  const request = ++listRequest
  const next = await api.get<TaskSummary[]>('/api/analysis/tasks?standalone_only=true')
  void api.get<TaskSummary[]>('/api/analysis/tasks?archived=true&standalone_only=true').then((a) => { archivedTasks.value = a }).catch(() => { /* 归档列表下次再刷新 */ })
  if (!mounted || request !== listRequest) return
  const previous = new Map(tasks.value.map(t => [t.id, t]))
  tasks.value = next
  lastSidebarRefresh = Date.now()
  for (const task of next) {
    const old = previous.get(task.id)
    if (old && task.id !== detail.value?.id && conversationStamp(old) !== conversationStamp(task)) {
      if (task.status === 'awaiting_user_facts') ElMessage.info(`${task.title}：有新的业务追问`)
      else if (task.latest_report_version > old.latest_report_version) ElMessage.success(`${task.title}：报告已生成`)
    }
  }
}

interface Attachment {
  id: string; filename: string; pages: number; chars: number; readable: boolean; note: string
  reviewed: boolean; in_merged?: boolean; message_id?: string | null; review_state?: string | null; review_seconds?: number; review_error?: string | null; summary: { covered: number; partial: number; missing: number } | null
}
const attachments = ref<Attachment[]>([])
const uploading = ref(false)
const reviewOpen = ref(false)
const reviewBlocks = ref<Block[]>([])
const reviewFor = ref<Attachment | null>(null)
const reviewMerged = ref(false)
const readableCount = computed(() => attachments.value.filter((x) => x.readable).length)
// 还在输入框里的文件（没随消息发出）；发出的文件属于它那条消息，显示在聊天记录里
const trayFiles = computed(() => attachments.value.filter((x) => !x.message_id))
const pendingFiles = computed(() => trayFiles.value.filter((x) => x.readable))
const countryKnown = computed(() => !!detail.value && detail.value.country_code !== '--' && detail.value.country_code !== '')
const filesOf = (messageId: string) => attachments.value.filter((x) => x.message_id === messageId)
// 对照状态属于每个对话自己（服务端存的）：只要这个对话里有文件在对照，就每隔几秒刷新一次
const anyReviewing = computed(() => attachments.value.some((x) => x.review_state === 'running'))
function fileState(a: Attachment): string {
  if (!a.readable || a.reviewed || a.in_merged) return ''
  if (a.review_state === 'running') return `对标核查中，已用时 ${Math.floor((a.review_seconds ?? 0) / 60)} 分 ${(a.review_seconds ?? 0) % 60} 秒`
  if (a.review_state === 'failed') return '对标核查未完成'
  return countryKnown.value ? '' : '待确认目标国家'
}
let attachmentTimer: number | undefined
watch(anyReviewing, (on) => {
  if (attachmentTimer) { window.clearInterval(attachmentTimer); attachmentTimer = undefined }
  if (!on) return
  attachmentTimer = window.setInterval(async () => {
    const id = detail.value?.id
    await loadAttachments()
    if (id && detail.value?.id === id && !attachments.value.some((x) => x.review_state === 'running')) await openTask(id)  // 完成：对话里有了结论
  }, 4000)
})
onUnmounted(() => { if (attachmentTimer) window.clearInterval(attachmentTimer) })
const fileEl = ref<HTMLInputElement | null>(null)

async function loadAttachments() {
  if (!detail.value) { attachments.value = []; return }
  try { attachments.value = await api.get<Attachment[]>(`/api/analysis/tasks/${detail.value.id}/attachments`) } catch { attachments.value = [] }
}
async function onPick(e: Event) {
  const input = e.target as HTMLInputElement
  const files = Array.from(input.files ?? [])
  input.value = ''
  if (!files.length || !detail.value) return
  uploading.value = true
  try {
    for (const file of files) {
    const res = await fetch(`/api/analysis/tasks/${detail.value.id}/attachments?filename=${encodeURIComponent(file.name)}`, {
      method: 'POST', headers: { Authorization: `Bearer ${api.token()}`, 'Content-Type': 'application/octet-stream' }, body: file,
    })
    if (!res.ok) { let m = '上传失败，可重试'; try { m = (await res.json()).detail || m } catch { /* 非 JSON */ } throw new Error(`${file.name}：${m}`) }
    }
    await loadAttachments()
    await openTask(detail.value.id)
    ElMessage.success('文件已读取，发送消息后会对照检查')
  } catch (err) { ElMessage.error((err as Error).message) } finally { uploading.value = false }
}
async function viewReview(a: Attachment) {
  if (!detail.value) return
  try {
    const r = await api.get<{ blocks: Block[] }>(`/api/analysis/tasks/${detail.value.id}/attachments/${a.id}/review`)
    reviewBlocks.value = r.blocks; reviewFor.value = a; reviewMerged.value = false; reviewOpen.value = true
  } catch (err) { ElMessage.error((err as Error).message) }
}
async function viewMerged() {
  if (!detail.value) return
  try {
    const r = await api.get<{ blocks: Block[] }>(`/api/analysis/tasks/${detail.value.id}/attachments/merged-review`)
    reviewBlocks.value = r.blocks; reviewFor.value = null; reviewMerged.value = true; reviewOpen.value = true
  } catch (err) { ElMessage.error((err as Error).message) }
}
async function retryReview() {
  if (!detail.value) return
  try {
    await api.post(`/api/analysis/tasks/${detail.value.id}/attachments/review-pending`, {})
    await loadAttachments()
  } catch (err) { ElMessage.error((err as Error).message) }
}
async function downloadReview(kind: 'pdf' | 'docx') {
  if (!downloadable.value || !detail.value || (!reviewFor.value && !reviewMerged.value)) return
  try {
    const part = reviewMerged.value ? 'merged-review' : `${reviewFor.value!.id}/review`
    const res = await fetch(`/api/analysis/tasks/${detail.value.id}/attachments/${part}/export.${kind}`, { headers: { Authorization: `Bearer ${api.token()}` } })
    if (!res.ok) throw new Error('导出失败，可重试')
    const url = URL.createObjectURL(await res.blob())
    const a = document.createElement('a'); a.href = url; a.download = serverFilename(res) || (reviewMerged.value ? `合规现状对照_合并.${kind}` : `${reviewFor.value!.filename.replace(/\.[^.]+$/, '')}_合规现状对照.${kind}`)
    document.body.appendChild(a); a.click(); a.remove(); window.setTimeout(() => URL.revokeObjectURL(url), 30000)
  } catch (err) { ElMessage.error((err as Error).message) }
}
async function deleteConversation(t: { id: string; title: string }) {
  try {
    await ElMessageBox.confirm(
      `该对话将移入已归档列表，对话记录、已上传文件及评估报告均予保留，可随时在已归档列表中恢复或彻底删除。\n\n${t.title}`,
      '确认归档对话',
      { confirmButtonText: '归档', cancelButtonText: '取消', type: 'warning' },
    )
  } catch { return }
  try {
    await api.delete(`/api/analysis/tasks/${t.id}`)
    const wasOpen = detail.value?.id === t.id
    await refreshTasks()
    ElMessage.success('已移入已归档列表')
    if (wasOpen) {
      detail.value = null
      const next = [...tasks.value].reverse()[0]
      if (next) await openTask(next.id)
      else await router.push({ query: { ...route.query, task: undefined } })
    }
  } catch (e: unknown) { ElMessage.error((e as Error).message || '归档失败') }
}
const menu = reactive({ open: false, x: 0, y: 0, task: null as { id: string; title: string; viewer_own?: boolean } | null })
function openMenu(e: MouseEvent, t: { id: string; title: string; viewer_own?: boolean }) {
  if (!canManageTask(t)) return  // 本企业共享对话只对企业用户开放改名、删除；查看者仅管理自己的私密对话
  menu.open = true; menu.x = Math.max(8, Math.min(e.clientX, window.innerWidth - 156)); menu.y = Math.max(8, Math.min(e.clientY, window.innerHeight - 132)); menu.task = t
}
function closeMenu() { menu.open = false }
async function renameConversation(t: { id: string; title: string }) {
  closeMenu()
  let value = ''
  try {
    const r = await ElMessageBox.prompt('请输入对话名称（不超过 60 字）', '重命名对话', {
      confirmButtonText: '保存', cancelButtonText: '取消', inputValue: t.title, inputPattern: /\S/, inputErrorMessage: '对话名称不能为空',
    })
    value = (r as { value: string }).value
  } catch { return }
  try {
    await api.patch(`/api/analysis/tasks/${t.id}`, { title: value })
    await refreshTasks()
    if (detail.value?.id === t.id) detail.value = { ...detail.value, title: value.trim().replace(/\s+/g, ' ') }
    ElMessage.success('已重命名')
  } catch (e: unknown) { ElMessage.error((e as Error).message || '重命名失败') }
}
async function cloneConversation(t: { id: string; title: string }) {
  closeMenu()
  try {
    const cloned = await api.post<{ id: string; title: string }>(`/api/analysis/tasks/${t.id}/clone`, {})
    await refreshTasks()
    showArchive.value = false
    await openTask(cloned.id)
    ElMessage.success(`已复制对话：${cloned.title}`)
  } catch (e: unknown) { ElMessage.error((e as Error).message || '复制对话失败') }
}
async function restoreConversation(t: { id: string }) {
  try {
    await api.post(`/api/analysis/tasks/${t.id}/restore`, {})
    await refreshTasks()
    ElMessage.success('已恢复至对话列表')
    showArchive.value = false
    await openTask(t.id)
  } catch (e: unknown) { ElMessage.error((e as Error).message || '恢复失败') }
}
async function deleteForGood(t: { id: string; title: string }) {
  try {
    await ElMessageBox.confirm(
      `彻底删除后，该对话的记录、已上传文件、对标结果及评估报告将一并清除且无法恢复。\n\n${t.title}`,
      '确认彻底删除',
      { confirmButtonText: '彻底删除', cancelButtonText: '取消', type: 'error' },
    )
  } catch { return }
  try {
    await api.delete(`/api/analysis/tasks/${t.id}?permanent=true`)
    await refreshTasks()
    ElMessage.success('已彻底删除')
  } catch (e: unknown) { ElMessage.error((e as Error).message || '删除失败') }
}
const manageableTasks = computed(() => tasks.value.filter((t) => canManageTask(t) && !t.project_id))
const manageableArchivedTasks = computed(() => archivedTasks.value.filter((t) => canManageTask(t)))
async function batchArchiveChats() {
  if (!manageableTasks.value.length) return
  try {
    await ElMessageBox.confirm(
      `当前列表中的 ${manageableTasks.value.length} 条咨询对话将移入已归档列表，随时可一键恢复。`,
      '确认一键归档对话',
      { confirmButtonText: '一键归档', cancelButtonText: '取消', type: 'warning' },
    )
  } catch { return }
  try {
    let archivedCount = 0
    let skippedRunning = 0
    if (!canEdit(auth.user)) {
      for (const t of manageableTasks.value) {
        if (t.status === 'running') { skippedRunning++; continue }
        await api.delete(`/api/analysis/tasks/${t.id}`)
        archivedCount++
      }
    } else {
      const r = await api.post<{ archived_count: number; skipped_running: number }>('/api/analysis/tasks/batch-archive', {
        kind: 'chat',
        ids: manageableTasks.value.map((t) => t.id),
      })
      archivedCount = r.archived_count
      skippedRunning = r.skipped_running
    }
    const closingIds = new Set(manageableTasks.value.map((t) => t.id))
    await refreshTasks()
    if (detail.value && closingIds.has(detail.value.id)) await newChat()
    ElMessage.success(`已归档 ${archivedCount} 条对话${skippedRunning ? `（${skippedRunning} 条运行中已保留）` : ''}`)
  } catch (e: unknown) { ElMessage.error((e as Error).message || '批量归档失败') }
}
async function batchRestoreChats() {
  if (!manageableArchivedTasks.value.length) return
  try {
    let restoredCount = 0
    let restoredIds: string[] = []
    if (!canEdit(auth.user)) {
      for (const t of manageableArchivedTasks.value) {
        await api.post(`/api/analysis/tasks/${t.id}/restore`)
        restoredCount++
        restoredIds.push(t.id)
      }
    } else {
      const r = await api.post<{ restored_count: number; ids: string[] }>('/api/analysis/tasks/batch-restore', {
        kind: 'chat',
        ids: manageableArchivedTasks.value.map((t) => t.id),
      })
      restoredCount = r.restored_count
      restoredIds = r.ids || []
    }
    await refreshTasks()
    showArchive.value = false
    if (restoredIds.length) await openTask(restoredIds[restoredIds.length - 1])
    ElMessage.success(`已一键恢复 ${restoredCount} 条对话`)
  } catch (e: unknown) { ElMessage.error((e as Error).message || '批量恢复失败') }
}
async function batchPurgeChats() {
  if (!manageableArchivedTasks.value.length) return
  try {
    await ElMessageBox.confirm(
      `彻底删除归档中的 ${manageableArchivedTasks.value.length} 条对话后无法恢复，是否继续？`,
      '确认清空已归档对话',
      { confirmButtonText: '彻底清空', cancelButtonText: '取消', type: 'error' },
    )
  } catch { return }
  try {
    let deletedCount = 0
    if (!canEdit(auth.user)) {
      for (const t of manageableArchivedTasks.value) {
        await api.delete(`/api/analysis/tasks/${t.id}?permanent=true`)
        deletedCount++
      }
    } else {
      const r = await api.post<{ deleted_count: number }>('/api/analysis/tasks/batch-delete', {
        kind: 'chat',
        ids: manageableArchivedTasks.value.map((t) => t.id),
      })
      deletedCount = r.deleted_count
    }
    await refreshTasks()
    ElMessage.success(`已彻底删除 ${deletedCount} 条归档对话`)
  } catch (e: unknown) { ElMessage.error((e as Error).message || '批量删除失败') }
}
async function openArchived(t: { id: string }) {
  await openTask(t.id)  // 留在已归档列表里，只是打开它看
}
const archived = computed(() => !!detail.value?.archived)
watch(archived, (on) => { if (on) showArchive.value = true })
async function removeAttachment(a: Attachment) {
  if (!detail.value) return
  try { await api.delete(`/api/analysis/tasks/${detail.value.id}/attachments/${a.id}`); await loadAttachments() } catch (err) { ElMessage.error((err as Error).message) }
}

async function openTask(id: string) {
  const request = ++openRequest
  try {
    const next = await api.get<TaskDetail>(`/api/analysis/tasks/${id}`)
    if (request !== openRequest) return
    apply(next)
    if (route.query.task !== id) await router.push({ query: { ...route.query, task: id } })
    openChapters.value = []
    openExtras.value = []
    followStream.value = true
    scrollBottom(true)
  } catch (e: unknown) {
    ElMessage.error((e as Error).message || '加载对话失败')
  }
}

const creatingChat = ref(false)
const sessionListEl = ref<HTMLElement | null>(null)

async function newChat(silent = false) {
  if (creatingChat.value) return
  creatingChat.value = true
  showArchive.value = false
  input.value = ''
  failedSend.value = null
  const request = ++openRequest
  try {
    const d = await api.post<TaskDetail>('/api/analysis/chats?advice=true', {})
    if (request !== openRequest) return
    apply(d)
    if (route.query.task !== d.id) {
      await router.push({ query: { ...route.query, task: d.id } })
    }
    await refreshTasks()
    followStream.value = true
    await nextTick()
    sessionListEl.value?.scrollTo({ top: 0, left: 0, behavior: 'smooth' })
    scrollBottom(true)
    inputEl.value?.focus()
    if (!silent) ElMessage.success('已开启新对话')
  } catch (e: unknown) {
    ElMessage.error((e as Error).message || '新建对话失败')
  } finally {
    creatingChat.value = false
  }
}

// 发出去没回上来（超时、出错）：在对话里写明，给重试按钮，不让用户去别处
const failedSend = ref<{ taskId: string; content: string; why: string } | null>(null)
// 空对话时给几个能直接点的常见问法
const STARTERS = ['中国车企向埃及传输车辆数据，要做哪些合规准备？', '出境数据什么情况下需要申报安全评估？', '车联网数据放在海外服务器，有哪些本地化要求？', '上传我们现有的制度文件，帮我对照缺什么']
const visibleStarters = computed(() => isViewer.value ? STARTERS.filter((q) => !q.includes('上传')) : STARTERS)
async function send(text?: string) {
  failedSend.value = null
  const files = isViewer.value ? [] : pendingFiles.value.map((f) => f.id)
  // 只带文件没有文字时当作请帮我对照上传的文件，和平常聊天里发文件一样
  const typed = (text ?? input.value).trim() || (files.length ? '请帮我对照上传的文件' : '')
  if (!typed || inputDisabled.value) return
  if (isViewer.value && (!detail.value || !detail.value.viewer_own)) {
    try {
      const created = await api.post<TaskDetail>('/api/analysis/chats?advice=true', {})
      apply(created)
      await router.push({ query: { ...route.query, task: created.id } })
    } catch (e: unknown) {
      ElMessage.error((e as Error).message || '新建咨询对话失败')
      return
    }
  }
  if (!detail.value) return
  const id = detail.value.id
  const content = typed
  const request = openRequest
  const questionId = status.value === 'awaiting_user_facts' ? detail.value.pending_user_facts?.[0]?.id : undefined
  pendingSends.set(id, Date.now())
  try {
    input.value = ''
    // 先把用户的话显示出来,再等待助手回复
    detail.value.messages.push({
      id: `tmp-${Date.now()}`,
      seq_no: 9999,
      role: 'user',
      content,
      options: [],
      slot: null,
    })
    followStream.value = true
    scrollBottom(true)
    const next = await api.post<TaskDetail>(`/api/analysis/tasks/${id}/messages`, {
        content,
        question_id: questionId,
        attachment_ids: files,
      })
    if (!mounted) return
    if (detail.value?.id === id && request === openRequest) apply(next)
    // The answer is saved and the next question is available. Refreshing the
    // sidebar must not keep the composer locked while another request is slow.
    void refreshTasks().catch(() => { /* A later refresh can update the sidebar. */ })
    scrollBottom()
    if (mounted && detail.value?.id === id) {
      await loadAttachments()  // 发出的文件此刻挂到了这条消息上，输入框里清空；对照由服务端在后台做
    }
  } catch (e: unknown) {
    failedSend.value = { taskId: id, content, why: (e as Error).message || '请求暂未响应，请重试' }
    if (mounted && detail.value?.id === id && request === openRequest) await openTask(id)
  } finally {
    pendingSends.delete(id)
  }
}

function clickChip(opt: string) {
  const slot = lastAssistant.value?.slot ?? ''
  if (slot === 'advice') {
    if (opt === '直接生成文件') { void router.push('/documents'); return }
    if (opt === '上传现有制度文件') { fileEl.value?.click(); return }
    void send(opt)
    return
  }
  if (CHOICE_SLOTS.has(slot)) {
    send(opt)
  } else {
    input.value = opt
    inputEl.value?.focus()
  }
}

function onEnter(ev: KeyboardEvent) {
  if (ev.shiftKey) return
  ev.preventDefault()
  send()
}

let timer: number | undefined
let clockTimer: number | undefined
async function poll() {
  if (polling || document.visibilityState !== 'visible') return
  polling = true
  try {
    if (progressState.value !== 'live') await refreshProgress()
    // A background conversation can finish while a different one is open.
    if (Date.now() - lastSidebarRefresh > 10000) {
      await refreshTasks()
      const summary = tasks.value.find(t => t.id === detail.value?.id)
      if (summary && detail.value && !sending.value &&
          (summary.status !== detail.value.status || summary.messages_count !== detail.value.messages.length ||
           summary.latest_report_version !== (detail.value.report_versions[0]?.version_no || 0))) {
        const id = detail.value.id
        const next = await api.get<TaskDetail>(`/api/analysis/tasks/${id}`)
        if (mounted && detail.value?.id === id) apply(next)
      }
    }
  } catch { /* A later refresh retries; preserve the conversation. */ }
  finally { polling = false }
}
async function refreshProgress() {
  if (detail.value?.status !== 'running') return
  const id = detail.value.id
  try {
    const next = await api.get<TaskDetail>(`/api/analysis/tasks/${id}`)
    if (!mounted || detail.value?.id !== id) return
    apply(next)
    scrollBottom()
  } catch {
    /* 下一轮重试 */
  }
}

watch(() => [detail.value?.id, detail.value?.status], () => {
  progressStream?.close()
  clearTimeout(progressRefresh)
  progressRefresh = undefined
  progressState.value = 'idle'
  if (!detail.value || detail.value.status !== 'running') return
  progressStream = startProgressStream({
    url: `/api/analysis/tasks/${detail.value.id}/events`, token: api.token,
    onState: state => { progressState.value = state },
    onUnauthorized: () => { api.setToken(null); window.location.assign(import.meta.env.BASE_URL + 'login') },
    onEvent: event => {
      if (event.type === 'connected' || progressRefresh !== undefined) return
      progressRefresh = setTimeout(() => {
        progressRefresh = undefined
        void refreshProgress()
      }, 80)
    },
  })
})

watch(
  () => [detail.value?.messages.length, detail.value?.progress.length, detail.value?.status],
  () => scrollBottom(),
)

async function openCitation(clauseId: string | null, quote = '') {
  if (!clauseId) {
    ElMessage.warning('该条目没有可定位的入库条款')
    return
  }
  anchorQuote.value = quote
  drawerVisible.value = true
  drawerLoading.value = true
  try {
    card.value = await api.get<CitationCard>(`/api/analysis/clauses/${clauseId}/citation-card`)
  } catch (e: unknown) {
    ElMessage.error((e as Error).message || '加载条款失败')
  } finally {
    drawerLoading.value = false
  }
}

function openEdit() {
  if (!report.value) return
  editSummary.value = report.value.executive_summary
  for (const d of report.value.dimensions) editDims[d.dimension_code] = stripBold(d.content_markdown)
  editVisible.value = true
}

async function saveEdit() {
  if (!detail.value) return
  savingEdit.value = true
  try {
    const rv = await api.post<ReportVersion>(
      `/api/analysis/tasks/${detail.value.id}/reports/edit`,
      {
        change_note: editNote.value,
        executive_summary: editSummary.value,
        // 只提交用户真正改过的章节；没改的保持原来的结构化内容
        dimension_updates: Object.fromEntries(
          report.value!.dimensions
            .filter((d) => editDims[d.dimension_code] !== stripBold(d.content_markdown))
            .map((d) => [d.dimension_code, editDims[d.dimension_code]])
        ),
      },
    )
    editVisible.value = false
    await openTask(detail.value.id)
    reportVersionNo.value = rv.version_no
    ElMessage.success('已保存修订内容')
  } catch (e: unknown) {
    ElMessage.error((e as Error).message || '保存失败')
  } finally {
    savingEdit.value = false
  }
}

async function openDiff() {
  const vers = (detail.value?.report_versions ?? []).map((r) => r.version_no).sort((a, b) => a - b)
  if (!detail.value || vers.length < 2) {
    ElMessage.info('需要至少两个报告版本才能对比，请先在线修订保存新版本')
    return
  }
  try {
    diffData.value = await api.get<DiffPayload>(
      `/api/analysis/tasks/${detail.value.id}/reports/diff?from_version=${vers[0]}&to_version=${vers[vers.length - 1]}`,
    )
    diffVisible.value = true
  } catch (e: unknown) {
    ElMessage.error((e as Error).message || '加载对比失败')
  }
}

// 文件名由服务端决定（日期_国家_业务_内容_版本），前端只负责原样使用
function serverFilename(res: Response): string | null {
  const header = res.headers.get('Content-Disposition') || ''
  const star = /filename\*=UTF-8''([^;]+)/i.exec(header)
  if (star) {
    try {
      return decodeURIComponent(star[1])
    } catch {
      return null
    }
  }
  return /filename="([^"]+)"/i.exec(header)?.[1] ?? null
}

const reportHostLang = ref<HostReportLang>('en')
const reportHostMeta = computed(() => HOST_REPORT_LANGUAGES.find((l) => l.code === reportHostLang.value) || HOST_REPORT_LANGUAGES[0])

watch(
  () => detail.value?.country_code,
  (cc) => {
    reportHostLang.value = cc ? inferHostLanguage(cc) : 'en'
  },
  { immediate: true },
)

async function download(ext: 'docx' | 'pdf' | 'zip', dimension?: string, lang: ReportOutputLang = 'zh') {
  if (!downloadable.value || !detail.value || !report.value || exportJob.value) return
  if (report.value.source_integrity_alerts?.length) {
    ElMessage.error('报告引用的原文来源异常，已停止导出，请核实来源后重新分析')
    return
  }
  const id = detail.value.id
  const country = detail.value.country_code
  const ver = report.value.version_no
  const langTag = lang === 'zh' ? '' : lang === 'en' ? '（英文版）' : `（${HOST_REPORT_LANGUAGES.find((l) => l.code === lang)?.nameZh || lang}版）`
  const label = `${dimension ? `${dimensionName(dimension)}章节` : '完整报告'}${langTag} · ${ext.toUpperCase()}`
  exportJob.value = {taskId: id, label, started: Date.now(), phase: 'preparing', received: 0, total: null}
  try {
    const q = new URLSearchParams()
    if (dimension) q.set('dimension', dimension)
    if (lang !== 'zh') q.set('lang', lang)
    const qs = q.toString()
    const res = await fetch(`/api/analysis/tasks/${id}/reports/${ver}/export.${ext}${qs ? '?' + qs : ''}`, {
      headers: { Authorization: `Bearer ${api.token()}` },
    })
    const blob = await receiveExport(res, (received, total) => {
      if (exportJob.value) Object.assign(exportJob.value, {phase: 'receiving', received, total})
    })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = serverFilename(res) || `${country}_${dimension || '完整报告'}.${ext}`
    document.body.appendChild(a)
    a.click()
    a.remove()
    window.setTimeout(() => URL.revokeObjectURL(url), 30000)
    if (mounted) ElMessage.success(`${label} 已交给浏览器下载，请查看下载列表`)
  } catch (e: unknown) {
    if (mounted) ElMessage.error((e as Error).message || '导出失败，可重试')
  } finally {
    exportJob.value = null
  }
}

async function downloadChatNotes(ext: 'docx' | 'pdf') {
  if (!downloadable.value || !detail.value || exportJob.value) return
  const id = detail.value.id
  const title = cleanChatTitle(detail.value.title)
  const label = `合规咨询问答纪要 · ${ext.toUpperCase()}`
  exportJob.value = { taskId: id, label, started: Date.now(), phase: 'preparing', received: 0, total: null }
  try {
    const res = await fetch(`/api/analysis/tasks/${id}/chat-export.${ext}`, {
      headers: { Authorization: `Bearer ${api.token()}` },
    })
    const blob = await receiveExport(res, (received, total) => {
      if (exportJob.value) Object.assign(exportJob.value, { phase: 'receiving', received, total })
    })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = serverFilename(res) || `${title}_合规咨询问答纪要.${ext}`
    document.body.appendChild(a)
    a.click()
    a.remove()
    window.setTimeout(() => URL.revokeObjectURL(url), 30000)
    if (mounted) ElMessage.success(`${label} 已交给浏览器下载，请查看下载列表`)
  } catch (e: unknown) {
    if (mounted) ElMessage.error((e as Error).message || '导出失败，可重试')
  } finally {
    exportJob.value = null
  }
}

async function refreshRegulatoryUpdates() {
  const id = detail.value?.id
  if (!id || detail.value?.status === 'running') return
  try {
    const next = await api.get<TaskDetail>(`/api/analysis/tasks/${id}`)
    if (detail.value?.id === id) apply(next)
  } catch { /* The notification page exposes retryable loading errors. */ }
}
watch(() => route.query.task, (id) => {
  if (typeof id === 'string' && id !== detail.value?.id) void openTask(id)
})
onMounted(async () => {
  try {
    const saved = JSON.parse(localStorage.getItem(seenKey()) || '{}')
    if (saved && typeof saved === 'object' && !Array.isArray(saved)) seen.value = saved
  } catch { /* Unavailable or old browser state does not block chat. */ }
  clockTimer = window.setInterval(() => { clockNow.value = Date.now() }, 1000)
  window.addEventListener('regulatory-notifications-updated', refreshRegulatoryUpdates)
  try {
    await refreshTasks()
    if (typeof route.query.task === 'string') {
      await openTask(route.query.task)
    } else if (isViewer.value) {
      // 查看者优先打开自己最近一条私密咨询对话；若还没有，先展示企业已有对话（不提前写库），提问时自动新建私密对话
      const myOwn = [...tasks.value].reverse().find((t) => t.viewer_own)
      const fallback = [...tasks.value].reverse().find((t) => !t.project_id) || [...tasks.value].reverse()[0]
      if (myOwn) await openTask(myOwn.id)
      else if (fallback) await openTask(fallback.id)
    } else {
      // 从咨询助手直接进来：打开最近一条普通咨询对话；文件项目旁边的对话留在列表里，点了才打开
      const own = [...tasks.value].reverse().find((t) => !t.project_id)
      if (own) await openTask(own.id)
      else await newChat(true)
    }
  } catch (e: unknown) {
    ElMessage.error((e as Error).message || '加载失败')
  }
  timer = window.setInterval(poll, 2000)
})
onUnmounted(() => {
  mounted = false
  window.removeEventListener('regulatory-notifications-updated', refreshRegulatoryUpdates)
  progressStream?.close()
  clearTimeout(progressRefresh)
  if (timer !== undefined) window.clearInterval(timer)
  if (clockTimer !== undefined) window.clearInterval(clockTimer)
})
</script>

<template>
  <div>
  <AssessmentSwitch />
  <div class="chat-page">
    <aside class="sessions">
      <div class="sessions-top">
        <strong>咨询对话</strong>
        <span class="top-actions">
          <el-button size="small" :loading="creatingChat" data-testid="btn-new-chat" :title="isViewer ? '新建一个仅你本人可见的法规咨询对话' : '新开一个对话：问法规、上传文件对照，或从这里进入生成合规文件'" @click="newChat()">
            + 新对话
          </el-button>
        </span>
      </div>
      <div class="archive-bar">
        <button type="button" class="archive-toggle" data-testid="archive-toggle" @click="showArchive = !showArchive">{{ showArchive ? '← 返回对话' : `查看已归档的对话（${archivedTasks.length}）` }}</button>
        <span class="batch-links">
          <button v-if="!showArchive && manageableTasks.length > 0" type="button" class="batch-link" data-testid="chat-batch-archive" @click="batchArchiveChats">一键归档</button>
          <template v-else-if="showArchive && manageableArchivedTasks.length">
            <button type="button" class="batch-link" data-testid="chat-batch-restore" @click="batchRestoreChats">一键恢复</button>
            <button type="button" class="batch-link danger" data-testid="chat-batch-delete" @click="batchPurgeChats">一键清空</button>
          </template>
        </span>
      </div>
      <div v-if="showArchive" class="session-list" data-testid="archive-list">
        <p v-if="!archivedTasks.length" class="archive-empty">暂无已归档对话。归档后的历史对话可在此恢复。</p>
        <div
          v-for="t in [...archivedTasks].reverse()"
          :key="t.id"
          class="session-item archived-item"
          :class="{ active: detail?.id === t.id }"
          data-testid="archived-item"
          role="button"
          tabindex="0"
          @click="openArchived(t)"
          @keydown.enter="openArchived(t)"
        >
          <div class="s-title">{{ cleanChatTitle(t.title) }}</div>
          <div v-if="canManageTask(t)" class="archive-actions">
            <el-button size="small" data-testid="archive-restore" @click.stop="restoreConversation(t)">恢复</el-button>
            <el-button size="small" type="danger" plain data-testid="archive-delete" @click.stop="deleteForGood(t)">彻底删除</el-button>
          </div>
        </div>
      </div>
      <div v-else ref="sessionListEl" class="session-list">
        <div
          v-for="t in [...tasks].reverse()"
          :key="t.id"
          class="session-item"
          :class="{ active: detail?.id === t.id }"
          data-testid="analysis-task-item"
          role="button"
          tabindex="0"
          :aria-current="detail?.id === t.id ? 'true' : undefined"
          @click="openTask(t.id)"
          @contextmenu.prevent="openMenu($event, t)"
          @keydown.enter="openTask(t.id)"
          @keydown.space.prevent="openTask(t.id)"
        >
          <div class="s-title" :title="cleanChatTitle(t.title)">
            <span class="session-dot" :class="conversationSignal(t).tone" aria-hidden="true" /><span class="s-title-text">{{ cleanChatTitle(t.title) }}</span>
          </div>
          <div class="s-meta" data-testid="session-conversation-code">
            <span class="s-code">
              <span v-if="t.viewer_own" class="s-project">仅自己可见</span>
              <a v-if="t.project_id" class="s-project" :href="`/documents?project=${t.project_id}`" title="关联的合规评估项目" @click.stop.prevent="router.push({ path: '/documents', query: { project: t.project_id } })">文件项目</a>
            </span>
            <span v-if="canManageTask(t)" class="s-tools">
              <button v-if="canEdit(auth.user)" class="s-delete" type="button" title="复制当前对话与报告快照" data-testid="session-clone" @click.stop="cloneConversation(t)" @keydown.enter.stop @keydown.space.stop>复制</button>
              <button class="s-delete" type="button" title="重命名对话（支持右键菜单）" data-testid="session-rename" @click.stop="renameConversation(t)" @keydown.enter.stop @keydown.space.stop>重命名</button>
              <button class="s-delete" type="button" title="冻结保存至已归档列表" data-testid="session-delete" @click.stop="deleteConversation(t)" @keydown.enter.stop @keydown.space.stop>归档</button>
            </span>
          </div>
          <div v-if="conversationSignal(t).label || isUnread(t)" class="session-state" :class="conversationSignal(t).tone">
            {{ conversationSignal(t).label }}<span v-if="isUnread(t)" class="unread-label">新动态</span>
          </div>
        </div>
      </div>
    </aside>

    <teleport to="body">
      <div v-if="menu.open" class="ctx-mask" @click="closeMenu" @contextmenu.prevent="closeMenu" />
      <ul v-if="menu.open && menu.task" class="ctx-menu" :style="{ left: menu.x + 'px', top: menu.y + 'px' }" data-testid="session-menu">
        <li v-if="canEdit(auth.user)" data-testid="menu-clone" @click="cloneConversation(menu.task!)">复制对话</li>
        <li data-testid="menu-rename" @click="renameConversation(menu.task!)">重命名</li>
        <li class="danger" data-testid="menu-delete" @click="closeMenu(); deleteConversation(menu.task!)">归档冻结</li>
      </ul>
    </teleport>

    <section class="chat-main">
      <header class="chat-head">
        <span class="chat-title">{{ detail ? cleanChatTitle(detail.title) : '新对话' }}</span>
        <el-tag v-if="detail && STATUS_LABEL[status] && status !== 'collecting_facts'" size="small" :type="status === 'running' ? 'primary' : 'info'">
          {{ STATUS_LABEL[status] || status }}
        </el-tag>
        <div v-if="detail && detail.messages.length >= 2" class="conv-id-bar">
          <el-dropdown v-if="downloadable" trigger="click" teleported>
            <el-button size="small" :disabled="!!exportJob" data-testid="btn-chat-export">导出问答纪要</el-button>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item data-testid="btn-chat-export-docx" @click="downloadChatNotes('docx')">Word 文档（.docx）</el-dropdown-item>
                <el-dropdown-item data-testid="btn-chat-export-pdf" @click="downloadChatNotes('pdf')">PDF 文档（.pdf）</el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
          <el-tooltip v-else content="当前只读账号未开通导出权限" placement="bottom">
            <span><el-button size="small" disabled data-testid="btn-chat-export-disabled">导出问答纪要</el-button></span>
          </el-tooltip>
        </div>
      </header>

      <div v-if="exportJob" class="export-feedback" data-testid="export-feedback" role="status" aria-live="polite">
        <span class="activity-spinner" aria-hidden="true" />
        <div><strong>{{ exportJob.phase === 'preparing' ? '正在生成文件' : '正在接收文件' }}</strong><span>{{ exportJob.label }} · {{ exportElapsed }}</span>
          <small v-if="exportJob.phase === 'preparing'">{{ exportJob.label.includes('PDF') ? '正在完成分页和字体排版，完成后自动下载。' : '文件准备好后自动下载。' }}</small>
          <small v-else>已接收 {{ Math.ceil(exportJob.received / 1024) }} KB<span v-if="exportJob.total"> / {{ Math.ceil(exportJob.total / 1024) }} KB</span></small>
        </div>
      </div>

      <div v-if="archived && detail" class="archived-banner" data-testid="archived-banner">
        当前对话已归档，处于只读状态。
        <el-button v-if="canManageTask(detail)" size="small" @click="restoreConversation(detail)">恢复后继续</el-button>
      </div>
      <div ref="streamEl" class="stream" data-testid="chat-stream" @scroll.passive="trackScroll">
        <template v-if="detail">
          <div
            v-for="m in detail.messages"
            :key="m.id"
            class="row"
            :class="m.role"
            :data-testid="`chat-msg-${m.role}`"
          >
            <div class="avatar">{{ m.role === 'assistant' ? '助' : '我' }}</div>
            <div class="bubble">
              <template v-if="m.role === 'assistant'"><template v-for="(seg, i) in segments(readableMessage(m.content))" :key="i"><a v-if="seg.href" class="inline-link" :href="seg.href" data-testid="msg-link" @click.prevent="router.push(seg.href)">{{ seg.text }}</a><RichText v-else :text="seg.text" /></template></template>
              <template v-else>{{ m.content }}</template>
              <div v-if="m.role === 'user' && filesOf(m.id).length" class="msg-files" data-testid="msg-files">
                <div v-for="a in filesOf(m.id)" :key="a.id" class="msg-file" data-testid="attachment-row">
                  <span class="a-name">📎 {{ a.filename }}<small>{{ a.pages ? `${a.pages} 页，` : '' }}约 {{ a.chars }} 字</small></span>
                  <el-tag v-if="a.summary" size="small" type="info">已涵盖 {{ a.summary.covered }} · 部分 {{ a.summary.partial }} · 未涵盖 {{ a.summary.missing }}</el-tag>
                  <el-tag v-else-if="fileState(a)" size="small" type="info">{{ fileState(a) }}</el-tag>
                  <el-button v-if="a.reviewed" size="small" data-testid="attachment-view" @click="viewReview(a)">查看对照</el-button>
                  <el-button v-else-if="a.in_merged" size="small" data-testid="attachment-view" @click="viewMerged">查看合并对照</el-button>
                  <el-button v-else-if="a.review_state === 'failed'" size="small" data-testid="attachment-retry" @click="retryReview">重试</el-button>
                </div>
              </div>
            </div>
          </div>

          <div v-if="sending" class="row assistant" data-testid="chat-waiting" role="status" aria-live="polite">
            <div class="avatar">助</div>
            <div class="bubble waiting-bubble">
              <div class="activity-title"><span class="activity-spinner" aria-hidden="true" />正在整理你的回答</div>
              <p>正在更新业务信息并准备下一步。</p>
              <small>已等待 {{ sendElapsed }}<span v-if="pendingSends.get(detail.id) && clockNow - pendingSends.get(detail.id)! > 20000"> · 回答时间较长，仍在处理</span></small>
            </div>
          </div>
          <div v-if="failedSend && failedSend.taskId === detail.id && !sending" class="row assistant" data-testid="chat-failed" role="alert">
            <div class="avatar">助</div>
            <div class="bubble">{{ failedSend.why }}。<el-button link type="primary" data-testid="chat-retry" @click="send(failedSend.content)">重试</el-button></div>
          </div>

          <div v-if="showChips && !sending" class="chips" data-testid="chat-options">
            <span v-if="!CHOICE_SLOTS.has(lastAssistant?.slot ?? '') && lastAssistant?.slot !== 'advice'" class="chip-hint">
              以下只是示例回答，点击会填入输入框，请按你的实际情况修改后再发送：
            </span>
            <el-button
              v-for="opt in lastAssistant?.options"
              :key="opt"
              size="small"
              round
              data-testid="chat-option-chip"
              @click="clickChip(opt)"
            >
              {{ opt }}
            </el-button>
          </div>

          <div v-if="showChips && !sending && lastAssistant?.slot === 'country_code'" class="chips" data-testid="chat-examples">
            <span class="chip-hint">也可以直接描述你的需求，点击示例会填入输入框，改成你的实际情况再发送：</span>
            <el-button v-for="ex in EXAMPLE_PROMPTS" :key="ex" size="small" round @click="clickChip(ex)">{{ ex }}</el-button>
          </div>

          <el-alert v-if="status === 'awaiting_user_facts' && !sending" type="info" :closable="false" data-testid="analysis-user-fact-pause" title="请补充业务信息" description="回答上方问题后继续生成报告。" />
          <div v-if="status === 'running'" class="card progress" data-testid="progress-card" role="status" aria-live="polite">
            <div class="card-title"><span class="activity-title" data-testid="task-running-tag"><span class="activity-spinner" aria-hidden="true" />{{ detail.activity?.label || '正在评估你的业务场景' }}</span><small v-if="activityElapsed">本环节 {{ activityElapsed }}</small></div>
            <div class="progress-stages"><span v-for="(s,i) in detail.progress" :key="i" class="progress-stage" :class="s.state"><span aria-hidden="true">{{ s.state === 'complete' ? '✓' : s.state === 'failed' ? '!' : '·' }}</span> {{s.label}}</span></div>
          </div>
          <el-alert v-if="status === 'failed'" type="error" :closable="false" :title="detail.failure_summary?.title || '本次评估暂未完成'" data-testid="analysis-failure-summary">
            <template v-if="detail.failure_summary">
              <p>{{ detail.failure_summary.summary }}</p>
              <ul v-if="detail.failure_summary.issues.length">
                <li v-for="issue in detail.failure_summary.issues" :key="issue.label">{{ issue.dimension_codes.map(dimensionName).join('、') }}：{{ issue.label }}</li>
              </ul>
              <p>{{ detail.failure_summary.next_action }}</p>
            </template>
            <p v-else>请稍后重试，或联系超级管理员协助排查；已填写的回答会保留。</p>
          </el-alert>

          <!-- 报告卡片 -->
          <div v-if="hasReport && report" class="card report" data-testid="report-card">
            <div class="card-title">
              <span class="report-title-wrap">
                <span>{{ consultingReady ? '合规评估报告' : '八个方面合规评估报告' }}</span>
                <small v-if="!consultingReady && reportGeneratedAt(report)" class="report-gen-time">生成时间：{{ reportGeneratedAt(report) }}</small>
              </span>
              <el-tag
                :type="
                  report.status === 'analysis_failed' || report.source_integrity_alerts?.length
                    ? 'danger'
                    : report.is_partial_sample
                      ? 'warning'
                      : 'success'
                "
              >
                {{
                  report.status === 'analysis_failed'
                    ? '暂未完成'
                    : report.source_integrity_alerts?.length
                      ? '来源异常 · 暂停导出'
                      : report.is_partial_sample
                        ? '部分方面未检索到依据'
                        : '已生成'
                }}
              </el-tag>
            </div>
            <p v-if="!consultingReady" class="summary">{{ businessSummary(report.executive_summary) }}</p>
            <el-alert v-if="report.regulatory_updates?.length" type="warning" :closable="false"
                :title="report.regulatory_updates.some(u => u.state === 'open') ? '引用来源已更新，请查看更新通知并重新评估。' : '已有后续报告版本，可切换查看。'"
              data-testid="report-regulatory-update" style="margin-bottom:12px" />
            <el-alert
              v-for="alert in report.source_integrity_alerts"
              :key="alert"
              type="error"
              :closable="false"
              show-icon
              :title="alert"
              style="margin-bottom: 10px"
            />

            <el-alert
              v-for="(imp, i) in report.stale_impacts"
              :key="i"
              type="warning"
              :closable="false"
              show-icon
              :title="`${dimensionName(imp.dimension_code)}：${imp.amendment_summary}。${imp.action_required}`"
              data-testid="report-stale-impact-alert"
              style="margin-bottom: 6px"
            />

            <div class="actions">
              <el-select
                v-if="detail.report_versions.length > 1"
                v-model="reportVersionNo"
                size="small"
                style="width: 170px"
                data-testid="report-version-select"
              >
                <el-option
                  v-for="(r, ri) in detail.report_versions"
                  :key="r.version_no"
                  :label="reportGeneratedAt(r) || (ri === 0 ? '最新记录' : '历史记录')"
                  :value="r.version_no"
                />
              </el-select>
              <el-select
                v-if="downloadable"
                v-model="reportHostLang"
                size="small"
                style="width: 156px"
                aria-label="属地报告语种"
                data-testid="chat-host-lang-select"
              >
                <el-option
                  v-for="lg in HOST_REPORT_LANGUAGES"
                  :key="lg.code"
                  :label="`属地语种：${lg.nameZh}`"
                  :value="lg.code"
                />
              </el-select>
              <el-dropdown v-if="downloadable" trigger="click"
                ><el-button size="small" :loading="!!currentExport" :disabled="!!exportJob || report.status === 'analysis_failed'">{{ currentExport ? '正在导出' : '导出报告' }}</el-button
                ><template #dropdown
                  ><el-dropdown-menu
                    ><el-dropdown-item data-testid="btn-export-docx" @click="download('docx')"
                      >中文报告 · Word</el-dropdown-item
                    ><el-dropdown-item data-testid="btn-export-pdf" @click="download('pdf')"
                      >中文报告 · PDF</el-dropdown-item
                    ><el-dropdown-item data-testid="btn-export-zip" @click="download('zip')"
                      >中文整份与八章 Word 压缩包</el-dropdown-item
                    ><el-dropdown-item divided data-testid="btn-export-en-docx" @click="download('docx', undefined, 'en')"
                      >英文报告 · Word</el-dropdown-item
                    ><el-dropdown-item data-testid="btn-export-en-pdf" @click="download('pdf', undefined, 'en')"
                      >英文报告 · PDF</el-dropdown-item
                    ><el-dropdown-item data-testid="btn-export-en-zip" @click="download('zip', undefined, 'en')"
                      >英文整份与八章 Word 压缩包</el-dropdown-item
                    ><template v-if="reportHostLang !== 'en'"
                      ><el-dropdown-item divided data-testid="btn-export-host-docx" @click="download('docx', undefined, reportHostLang)"
                        >{{ reportHostMeta.nameZh }}报告 · Word</el-dropdown-item
                      ><el-dropdown-item data-testid="btn-export-host-pdf" @click="download('pdf', undefined, reportHostLang)"
                        >{{ reportHostMeta.nameZh }}报告 · PDF</el-dropdown-item
                      ><el-dropdown-item data-testid="btn-export-host-zip" @click="download('zip', undefined, reportHostLang)"
                        >{{ reportHostMeta.nameZh }}整份与八章 Word 压缩包</el-dropdown-item
                      ></template
                    ></el-dropdown-menu
                  ></template
                ></el-dropdown
              >
              <el-tooltip v-else content="当前只读账号未开通导出权限" placement="top">
                <span><el-button size="small" disabled>导出报告</el-button></span>
              </el-tooltip>
            </div>
            <p class="legacy-note">{{ downloadable ? '历史报告，仅供查看和下载。如需生成正式合规评估文件，请切换到上方生成合规文件页签。' : '历史报告，仅供在线查阅。如需生成正式合规评估文件，请切换到上方生成合规文件页签。' }}</p>

            <ConsultingReport
              v-if="consultingReady && report.consulting"
              :consulting="report.consulting"
              :dimension-texts="dimensionTexts"
              :executive-summary="report.executive_summary"
              :generated-at="reportGeneratedAt(report)"
              :export-disabled="!!exportJob || report.status === 'analysis_failed'"
              :can-export="downloadable"
              @open-citation="openCitation"
              @export-dimension="(code, ext) => download(ext, code)"
            />
            <div v-if="!consultingReady" class="sub-title">八个合规维度</div>
            <el-collapse v-if="!consultingReady" v-model="openChapters">
              <el-collapse-item
                v-for="d in report.dimensions"
                :key="d.dimension_code"
                :name="d.dimension_code"
                :data-testid="`report-chapter-${d.dimension_code}`"
              >
                <template #title>
                  <b>{{ d.dimension_title }}</b>&nbsp;
                  <el-tag size="small" :type="d.status === 'COMPLETE' ? 'success' : 'warning'">
                    {{ d.status === 'COMPLETE' ? '分析完成' : d.status === 'ANALYSIS_ERROR' ? '暂未完成' : '有缺口' }}
                  </el-tag>
                  &nbsp;
                  <el-tag size="small" :type="d.risk_level === 'HIGH' ? 'danger' : 'info'">
                    {{ RISK_LABEL[d.risk_level] || d.risk_level }}
                  </el-tag>
                </template>
                <div style="display:flex;gap:8px;margin:8px 0 12px">
                  <el-tooltip :disabled="downloadable" content="当前只读账号未开通导出权限" placement="top">
                    <span><el-button size="small" :disabled="!downloadable || !!exportJob || report.status === 'analysis_failed'" :data-testid="`export-${d.dimension_code}-docx`" @click="download('docx', d.dimension_code)">导出本章 Word</el-button></span>
                  </el-tooltip>
                  <el-tooltip :disabled="downloadable" content="当前只读账号未开通导出权限" placement="top">
                    <span><el-button size="small" :disabled="!downloadable || !!exportJob || report.status === 'analysis_failed'" :data-testid="`export-${d.dimension_code}-pdf`" @click="download('pdf', d.dimension_code)">导出本章 PDF</el-button></span>
                  </el-tooltip>
                </div>
                <el-alert
                  v-if="d.missing_gap_note"
                  type="warning"
                  :closable="false"
                  show-icon
                  :title="d.missing_gap_note"
                  style="margin-bottom: 8px"
                />
                <div class="md">{{ readableContent(d.content_markdown) }}</div>
                <ul v-if="d.key_recommendations.length" class="recs">
                  <li v-for="(r, i) in d.key_recommendations" :key="i">
                    {{ r }}
                  </li>
                </ul>
                <div class="cites">
                  <el-button
                    v-for="c in d.citations"
                    :key="c.citation_tag"
                    size="small"
                    data-testid="report-citation-badge"
                    @click="openCitation(c.clause_id, c.quote_original)"
                  >
                    {{ c.citation_tag }} {{ c.document_title.slice(0, 22) }} ·
                    {{ c.clause_number }}
                  </el-button>
                </div>
              </el-collapse-item>
            </el-collapse>

            <el-collapse v-model="openExtras" style="margin-top: 10px">
              <el-collapse-item
                name="matrix"
                :title="
                  consultingReady
                    ? '依据条文与原文对照'
                    : '合规对照简表'
                "
              >
                <el-table
                  :data="versionFindings"
                  border
                  size="small"
                  data-testid="compliance-findings-table"
                >
                  <template #empty>该报告未附合规对照简表，请查阅各章引用条文。</template>
                  <el-table-column prop="business_fact" label="业务事实" min-width="140" />
                  <el-table-column label="适用条款" min-width="170">
                    <template #default="{ row }">
                      <el-button
                        link
                        type="primary"
                        data-testid="finding-clause-link"
                        @click="openCitation(row.clause_id, row.basis_quote)"
                      >
                        {{ row.document_title.slice(0, 24) }} ·
                        {{ row.clause_number }}
                      </el-button>
                    </template>
                  </el-table-column>
                  <el-table-column prop="compliance_obligation" label="合规义务" min-width="200" />
                  <el-table-column label="风险" width="80">
                    <template #default="{ row }">{{
                      RISK_LABEL[row.risk_level] || row.risk_level
                    }}</template>
                  </el-table-column>
                  <el-table-column label="判断依据（原文）" min-width="200">
                    <template #default="{ row }">
                      <i>{{ row.basis_quote }}</i>
                      <div class="muted">中文对照：{{ row.basis_zh }}</div>
                    </template>
                  </el-table-column>
                </el-table>
              </el-collapse-item>

            </el-collapse>
          </div>
          <div v-if="(canEdit(auth.user) || detail.viewer_own) && !inputDisabled && detail.messages.length <= 1" class="starters" data-testid="chat-starters">
            <button v-for="q in visibleStarters" :key="q" type="button" class="starter" @click="send(q)">{{ q }}</button>
          </div>
        </template>
        <template v-else-if="isViewer">
          <div class="row assistant" data-testid="chat-msg-assistant">
            <div class="avatar">助</div>
            <div class="bubble">您好，我是车研数合法规咨询助手。您可以咨询各国汽车数据跨境与属地准入法规要求，或查阅引证条文原文与中文对照。</div>
          </div>
          <div v-if="!inputDisabled" class="starters" data-testid="chat-starters">
            <button v-for="q in visibleStarters" :key="q" type="button" class="starter" @click="send(q)">{{ q }}</button>
          </div>
        </template>
      </div>

      <div v-if="trayFiles.length" class="attachments" data-testid="attachments">
        <div v-for="a in trayFiles" :key="a.id" class="attachment" data-testid="tray-file">
          <span class="a-name">📎 {{ a.filename }}<small>{{ a.pages ? `${a.pages} 页，` : '' }}约 {{ a.chars }} 字</small></span>
          <el-tag v-if="!a.readable" size="small" type="danger">未能提取文本：{{ a.note }}</el-tag>
          <span class="a-actions"><el-button size="small" link type="danger" @click="removeAttachment(a)">移除</el-button></span>
        </div>
      </div>
      <p v-if="isViewer && !archived" class="viewer-note" data-testid="viewer-chat-note">
        {{ detail && !detail.viewer_own ? '当前查看的是本企业共享对话；在下方输入提问将自动新建仅你本人可见的咨询对话，不会改动企业项目。' : '普通查看者：你的咨询对话仅本人可见，不能新建或修改本企业的评估与准入项目。' }}
      </p>
      <footer v-if="!archived" class="composer">
        <template v-if="canEdit(auth.user)">
          <input ref="fileEl" type="file" multiple accept=".pdf,.docx,.txt,.md" hidden data-testid="attach-input" @change="onPick" />
          <el-button :loading="uploading" :disabled="!detail || inputDisabled" title="上传现有的制度文件（PDF、Word、文本，可多选，每份不超过 10 MB）；发送消息时一并对照，指出还缺什么" data-testid="attach-btn" @click="fileEl?.click()">📎 上传文件</el-button>
        </template>
        <el-input
          ref="inputEl"
          v-model="input"
          type="textarea"
          :rows="2"
          resize="none"
          :disabled="inputDisabled"
          :placeholder="inputPlaceholder"
          data-testid="chat-input"
          @keydown.enter="onEnter"
        />
        <el-button
          type="primary"
          :loading="sending"
          :disabled="inputDisabled || (!input.trim() && (isViewer || !pendingFiles.length))"
          data-testid="chat-send"
          @click="send()"
        >
          发送
        </el-button>
      </footer>
    </section>

    <el-dialog v-model="reviewOpen" :title="reviewMerged ? '多份文件合并的合规现状对照' : reviewFor ? `《${reviewFor.filename}》合规现状对照` : '合规现状对照'" width="1000px" top="5vh" data-testid="review-dialog">
      <BlocksView :blocks="reviewBlocks.filter((b) => b.type !== 'title')" />
      <template #footer>
        <el-tooltip :disabled="downloadable" content="当前只读账号未开通导出权限" placement="top">
          <span style="margin-right: 8px"><el-button :disabled="!downloadable" @click="downloadReview('docx')">下载 Word</el-button></span>
        </el-tooltip>
        <el-tooltip :disabled="downloadable" content="当前只读账号未开通导出权限" placement="top">
          <span><el-button type="primary" :disabled="!downloadable" @click="downloadReview('pdf')">下载 PDF</el-button></span>
        </el-tooltip>
      </template>
    </el-dialog>

    <el-drawer v-model="drawerVisible" title="引用来源与术语释义" size="60%">
      <div v-loading="drawerLoading" data-testid="citation-detail-drawer">
        <el-alert v-for="alert in card?.source_integrity_alerts" :key="alert"
          :title="alert" type="warning" :closable="false" show-icon />
        <template v-if="card">
          <h4>
            {{ card.document_title }} · {{ card.clause_number }}
          </h4>
          <p v-if="card.translated_text && card.translation_review_status !== 'approved'" class="muted">中文为参考译文，以官方原文为准。</p>
          <div v-if="card.source_url" class="muted">
            官方来源：<a :href="card.source_url" target="_blank" rel="noopener">{{
              card.source_url
            }}</a>
          </div>
          <div class="dual" :style="(card.is_native_zh || card.language?.toLowerCase().startsWith('zh')) ? 'grid-template-columns: 1fr' : undefined">
            <div>
              <div class="col-label">{{ (card.is_native_zh || card.language?.toLowerCase().startsWith('zh')) ? '官方原文（中文现行条文）' : '官方原文' }}</div>
              <LegalText :text="card.original_text" :terms="card.terms" :clause-id="card.clause_id" :section-path="card.section_path" />
              <div v-if="anchorQuote" class="anchor">
                报告锚定的原文片段：<mark>{{ anchorQuote }}</mark>
              </div>
            </div>
            <div v-if="!(card.is_native_zh || card.language?.toLowerCase().startsWith('zh'))">
              <div class="col-label">中文对照</div>
              <LegalText :text="card.translated_text || '暂无中文对照'" :terms="card.terms" :clause-id="card.clause_id" :section-path="card.section_path" />
            </div>
          </div>
          <div v-if="card.related_requirements?.length" class="col-label" style="margin-top:24px">相关合规要求</div>
          <article v-for="r in card.related_requirements" :key="r.id" class="step"><span class="muted">{{r.topic_label}}</span><h4>{{r.title}}</h4><p>{{r.quote}}</p></article>
        </template>
      </div>
    </el-drawer>

    <el-dialog v-model="editVisible" title="在线修订并保存为新版本" width="720px">
      <el-input v-model="editNote" placeholder="修订说明" style="margin-bottom: 8px" />
      <el-input v-model="editSummary" type="textarea" :rows="3" style="margin-bottom: 8px" />
      <div v-if="report" style="max-height: 340px; overflow-y: auto">
        <div v-for="d in report.dimensions" :key="d.dimension_code" style="margin-bottom: 8px">
          <b>{{ d.dimension_title }}</b>
          <el-input v-model="editDims[d.dimension_code]" type="textarea" :rows="3" />
        </div>
      </div>
      <template #footer>
        <el-button @click="editVisible = false">取消</el-button>
        <el-button
          type="primary"
          :loading="savingEdit"
          data-testid="btn-confirm-save-report-edit"
          @click="saveEdit"
        >
          保存为新版本
        </el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="diffVisible" title="报告版本对比" width="780px">
      <div v-if="diffData" data-testid="report-diff-content">
        <div
          v-for="x in diffData.dimension_diffs"
          :key="x.dimension_code"
          class="diff-row"
          :class="{ changed: x.changed }"
        >
          <b>{{ x.dimension_title }}</b>
          <el-tag size="small" :type="x.changed ? 'warning' : 'info'">{{
            x.changed ? '有修订' : '一致'
          }}</el-tag>
          <div v-if="x.changed" class="dual">
            <pre>{{ stripBold(x.from_markdown) }}</pre>
            <pre>{{ stripBold(x.to_markdown) }}</pre>
          </div>
        </div>
      </div>
    </el-dialog>
  </div>
  </div>
</template>

<style scoped>
.chat-page {
  display: flex;
  height: calc(100vh - 100px);
  min-height: 640px;
  background: #f8fafc;
  border: 1px solid #e2e8f0;
  border-radius: 10px;
  overflow: hidden;
}
.sessions {
  width: 240px;
  background: #fff;
  border-right: 1px solid #e2e8f0;
  display: flex;
  flex-direction: column;
}
.sessions-top {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 12px;
  border-bottom: 1px solid #f1f5f9;
}
.session-list {
  flex: 1;
  overflow-y: auto;
  padding: 8px;
}
.session-item {
  padding: 8px 10px;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  margin-bottom: 8px;
  cursor: pointer;
}
.session-item.active {
  border-color: #2563eb;
  background: #eff6ff;
}
.s-title {
  font-size: 13px;
  font-weight: 600;
  margin-bottom: 2px;
  word-break: break-all;
}
.s-code {
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-size: 13px;
  color: #64748b;
  margin-bottom: 4px;
}
.conv-id-bar {
  margin-left: auto;
  display: flex;
  align-items: center;
  gap: 8px;
}
.chat-main {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
}
.chat-head {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 10px;
  padding: 12px 18px;
  background: #fff;
  border-bottom: 1px solid #e2e8f0;
}
.chat-title {
  font-weight: 600;
}
.stream {
  flex: 1;
  overflow-y: auto;
  padding: 18px;
}
.row {
  display: flex;
  gap: 10px;
  margin-bottom: 14px;
}
.row.user {
  flex-direction: row-reverse;
}
.avatar {
  width: 32px;
  height: 32px;
  border-radius: 50%;
  background: #1d4ed8;
  color: #fff;
  font-size: 13px;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}
.row.user .avatar {
  background: #0f766e;
}
.bubble {
  max-width: 70%;
  padding: 10px 14px;
  border-radius: 12px;
  background: #fff;
  border: 1px solid #e2e8f0;
  white-space: pre-line;
  line-height: 1.65;
  font-size: 14px;
}
.row.user .bubble {
  background: #dbeafe;
  border-color: #bfdbfe;
}
.chips {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin: 0 0 14px 42px;
  align-items: center;
}
.chip-hint {
  width: 100%;
  font-size: 13px;
  color: #92400e;
}
.card {
  background: #fff;
  border: 1px solid #e2e8f0;
  border-radius: 12px;
  padding: 14px 16px;
  margin: 0 0 14px 42px;
}
.card.progress {
  border-color: #93c5fd;
}
.card-title {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-weight: 600;
  margin-bottom: 8px;
}
.step {
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  padding: 8px 10px;
  margin-bottom: 8px;
  font-size: 13px;
  background: #f8fafc;
}
.step.back {
  border-color: #f97316;
  background: #fff7ed;
}
.step-head {
  display: flex;
  gap: 8px;
  align-items: center;
  margin-bottom: 4px;
}
.back-reason {
  margin-top: 4px;
  color: #9a3412;
  white-space: pre-line;
}
.muted {
  color: #64748b;
  font-size: 13px;
}
.summary {
  white-space: pre-line;
  line-height: 1.7;
  margin: 0 0 10px;
}
.actions {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  margin: 8px 0 12px;
}
.sub-title {
  font-weight: 600;
  margin-bottom: 6px;
}
.md {
  white-space: pre-line;
  line-height: 1.7;
  font-size: 14px;
}
.recs {
  margin: 8px 0;
  padding-left: 20px;
  font-size: 13px;
}
.cites {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 8px;
}
.viewer-note { margin: 0; padding: 12px 20px; color: #64748b; font-size: 13px; border-top: 1px solid #e2e8f0; }
.composer {
  display: flex;
  gap: 10px;
  padding: 12px 18px;
  background: #fff;
  border-top: 1px solid #e2e8f0;
  align-items: flex-end;
}
.dual {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
  margin-top: 8px;
}
.col-label {
  font-weight: 600;
  font-size: 13px;
  color: #475569;
  margin-bottom: 4px;
}
.anchor {
  margin-top: 8px;
  padding: 6px 8px;
  background: #fef9c3;
  border-radius: 6px;
  font-size: 13px;
}
.diff-row {
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  padding: 8px 10px;
  margin-bottom: 8px;
}
.diff-row.changed {
  border-color: #f59e0b;
  background: #fffbeb;
}
.diff-row pre {
  white-space: pre-wrap;
  font-size: 13px;
  margin: 0;
}

.chat-page {
  height: calc(100dvh - 145px);
  min-height: 650px;
  border-color: var(--cy-line);
  border-radius: 14px;
  background: #f7f9fc;
}
.sessions {
  width: 240px;
  flex-shrink: 0;
  background: #fbfcfe;
  border-color: var(--cy-line);
}
.sessions-top {
  padding: 16px;
  gap: 10px;
  flex-wrap: wrap;
}
.sessions-top strong { white-space: nowrap; }
.top-actions { width: 100%; }
.archived-banner { display: flex; align-items: center; gap: 12px; padding: 8px 18px; font-size: 13px; background: #fef3c7; color: #92400e; border-bottom: 1px solid #fde68a; }
.archive-bar { padding: 4px 12px 0; }
.archive-toggle { border: 0; background: transparent; color: #64748b; font-size: 13px; cursor: pointer; padding: 4px 0; }
.archive-toggle:hover { color: #2563eb; }
.inline-link { color: #1d4ed8; text-decoration: underline; text-underline-offset: 2px; cursor: pointer; font-weight: 400; }
.archive-empty { color: #64748b; font-size: 13px; line-height: 1.7; padding: 8px 4px; }
.archive-actions { display: flex; gap: 8px; margin-top: 8px; }
.archived-item .s-title { cursor: pointer; }
.top-actions .el-button { flex: 1; }
.session-list {
  padding: 12px;
}
.session-item {
  padding: 14px;
  border-color: transparent;
  background: transparent;
  margin-bottom: 8px;
  transition: background 0.15s;
}
.session-item:hover {
  background: #f0f4fa;
}
.session-item.active {
  background: #eaf0fc;
  border-color: #d4dff6;
}
.s-title {
  font-size: 14px;
  line-height: 1.6;
  margin-bottom: 6px;
}
.s-code {
  font-size: 13px;
  color: #98a5b2;
}
.chat-head {
  padding: 18px 24px;
  flex-wrap: wrap;
  gap: 10px;
  min-height: 76px;
}
.chat-title {
  font-size: 16px;
  font-weight: 600;
}
.conv-id-bar {
  font-size: 13px;
}
.stream {
  padding: 30px 28px;
}
.row {
  gap: 12px;
  margin-bottom: 24px;
}
.avatar {
  border-radius: 11px;
  width: 34px;
  height: 34px;
  font-size: 13px;
  background: #4767a5;
}
.bubble {
  font-size: 15px;
  line-height: 1.9;
  padding: 16px 20px;
  border-color: #e8edf3;
  max-width: 85%;
}
.row.user .bubble {
  background: #eaf0ff;
  border-color: #dce5f8;
}
.row.user .avatar {
  background: #5c8d84;
}
.card {
  padding: 22px 24px;
  margin-left: 46px;
  border-color: #e4eaf1;
}
.card-title {
  flex-wrap: wrap;
  gap: 12px;
  font-size: 16px;
}
.summary {
  font-size: 15px;
  line-height: 1.9;
}
.composer {
  padding: 18px 24px;
  gap: 12px;
}
.composer .el-button {
  min-height: 44px;
}
.composer .el-textarea__inner {
  min-height: 48px;
}
.md {
  font-size: 15px;
  line-height: 1.9;
}
.actions {
  gap: 10px;
  margin: 20px 0;
}
.sub-title {
  font-size: 14px;
  color: #738492;
  margin-top: 26px;
}
.step {
  font-size: 14px;
  padding: 14px;
}
.muted {
  font-size: 13px;
}
.recs {
  font-size: 14px;
  line-height: 1.9;
}
.el-collapse-item {
  margin-top: 4px;
}
.session-dot { display: inline-block; width: 8px; height: 8px; margin: 0 8px 1px 0; border-radius: 50%; background: #a2afbf; flex-shrink: 0; }
.session-dot.green { background: #159b71; }
.session-dot.amber { background: #d99a23; }
.session-dot.blue { background: #4168e8; }
.session-dot.red { background: #df6666; }
.session-state { margin-top: 10px; font-size: 13px; line-height: 1.6; color: #7b899a; }
.session-state.green { color: #168563; }
.session-state.amber { color: #9d711a; }
.session-state.blue { color: #4163c8; }
.session-state.red { color: #bb4c4c; }
.unread-label { display: inline-block; margin-left: 7px; padding: 0 5px; border-radius: 4px; background: #e8efff; color: #365bc7; font-size: 13px; }
.activity-title { display: flex; align-items: center; gap: 10px; }
.activity-spinner { display: inline-block; width: 15px; height: 15px; flex-shrink: 0; border: 2px solid #dce5f9; border-top-color: #5275cf; border-radius: 50%; animation: activity-spin 1s linear infinite; }
.waiting-bubble { min-width: 230px; }
.waiting-bubble p { margin: 7px 0; color: #78889a; font-size: 13px; }
.waiting-bubble small, .progress small { font-size: 13px; color: #8794a4; font-weight: 400; }
.progress-stages { display: flex; flex-wrap: wrap; gap: 10px 18px; margin-top: 16px; }
.progress-stage { font-size: 13px; color: #708197; }
.progress-stage.complete > span { color: #1c9576; }
.progress-stage.warning, .progress-stage.waiting { color: #a17b37; }
.progress-stage.failed { color: #b45959; }
.export-feedback { display: flex; align-items: center; gap: 12px; margin: 12px 24px 0; padding: 12px 16px; border-radius: 10px; background: #eef3ff; color: #395ca7; font-size: 13px; }
.export-feedback strong { margin-right: 12px; font-weight: 600; }
.export-feedback small { display: block; margin-top: 5px; color: #71829e; font-size: 13px; }
@keyframes activity-spin { to { transform: rotate(360deg); } }
@media (prefers-reduced-motion: reduce) { .activity-spinner { animation: none; border-top-color: #5275cf; } }
@media (max-width: 1100px) {
  .sessions {
    width: 205px;
  }
  .stream {
    padding: 24px 18px;
  }
  .card {
    margin-left: 0;
  }
  .conv-id-bar {
    margin-left: 0;
    width: 100%;
  }
}
@media (max-width: 800px) {
  .chat-page {
    flex-direction: column;
    height: calc(100dvh - 115px);
    min-height: 680px;
  }
  .sessions {
    width: 100%;
    max-height: 170px;
    border-right: 0;
    border-bottom: 1px solid var(--cy-line);
  }
  .sessions-top {
    padding: 12px 16px;
  }
  .session-list {
    display: flex;
    overflow-x: auto;
    gap: 10px;
    flex: 1;
    padding: 8px 12px;
  }
  .session-item {
    min-width: 200px;
    margin-bottom: 0;
  }
  .s-code {
    display: none;
  }
  .session-item .el-tag {
    font-size: 13px;
  }
  .chat-main {
    min-height: 0;
  }
  .chat-head {
    padding: 14px 16px;
  }
  .stream {
    padding: 20px 14px;
  }
  .bubble {
    max-width: 90%;
  }
  .card {
    padding: 18px 16px;
  }
  .composer {
    padding: 12px;
  }
  .dual {
    grid-template-columns: 1fr;
  }
}
.guided-tip { margin: 0 0 14px; color: var(--el-text-color-secondary); line-height: 1.7; }
.guided-scenarios { display: flex; flex-direction: column; align-items: flex-start; gap: 8px; }
.msg-files { margin-top: 8px; display: flex; flex-direction: column; gap: 6px; }
.msg-file { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; font-size: 13px; background: rgba(255,255,255,.7); border-radius: 8px; padding: 6px 10px; color: #1e293b; }
.a-hint { margin: 0; padding: 6px 18px; font-size: 13px; color: #64748b; background: #fff; }
.top-actions { display: inline-flex; gap: 6px; }
.s-meta { display: flex; align-items: center; justify-content: space-between; gap: 6px; min-height: 20px; margin-top: 2px; }
.s-tools { float: none; flex-shrink: 0; display: none; gap: 6px; margin-left: auto; }
.session-item:hover .s-tools, .session-item:focus-within .s-tools, .session-item.active .s-tools { display: inline-flex; }
.ctx-mask { position: fixed; inset: 0; z-index: 2999; }
.ctx-menu { position: fixed; z-index: 3000; margin: 0; padding: 4px 0; list-style: none; min-width: 120px; background: #fff; border: 1px solid #e2e8f0; border-radius: 8px; box-shadow: 0 8px 24px rgba(15,23,42,.14); font-size: 13px; }
.ctx-menu li { padding: 7px 14px; cursor: pointer; color: #1e293b; }
.ctx-menu li:hover { background: #f1f5f9; }
.ctx-menu li.danger { color: #dc2626; }
.s-delete { float: none; border: 0; background: transparent; color: #64748b; font-size: 13px; cursor: pointer; padding: 0 2px; }
.s-tools .s-delete:first-child:hover { color: #2563eb; }
.s-delete:hover { color: #dc2626; }
.attachments { padding: 8px 18px 0; background: #fff; border-top: 1px solid #e2e8f0; }
.attachment { display: flex; gap: 10px; align-items: center; padding: 4px 0; font-size: 13px; }
.chat-page small { font-size: 13px; }
.report-title-wrap { display: inline-flex; align-items: baseline; gap: 10px; flex-wrap: wrap; }
.report-gen-time { font-size: 13px; font-weight: 400; color: #64748b; }
.a-name small { color: var(--el-text-color-secondary); margin-left: 8px; font-size: 13px; }
.a-actions { margin-left: auto; }
.a-hint { margin: 4px 0 6px; color: var(--el-text-color-secondary); font-size: 13px; }
.s-project { margin-right: 6px; font-size: 13px; color: #2563eb; white-space: nowrap; }
.legacy-note { color: #64748b; font-size: 13px; margin: 6px 0 10px; }
/* 左侧对话列表加宽到 288px，标题支持两行完整展示，已归档放在列表底部 */
.sessions { width: 288px !important; display: flex; flex-direction: column; }
.sessions .archive-bar { order: 99; margin-top: auto; padding: 10px 12px; border-top: 1px solid var(--cy-line); display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 6px; }
.batch-links { display: inline-flex; align-items: center; gap: 8px; }
.batch-link { border: 0; background: transparent; color: #2563eb; font-size: 13px; cursor: pointer; padding: 0; }
.batch-link:hover { text-decoration: underline; }
.batch-link.danger { color: #dc2626; }
.session-item .s-title { display: flex; align-items: flex-start; gap: 7px; word-break: break-word; margin-bottom: 4px; }
.session-item .session-dot { margin: 7px 0 0 0; }
.session-item .s-title-text { flex: 1; min-width: 0; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; white-space: normal; overflow: hidden; line-height: 1.48; }
.starters { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; margin: 16px 0 4px 46px; max-width: 720px; }
.starter { text-align: left; padding: 10px 14px; border: 1px solid var(--cy-line); border-radius: 10px; background: #fff; font: inherit; font-size: 14px; line-height: 1.5; color: var(--cy-text-1); cursor: pointer; }
.starter:hover { border-color: var(--el-color-primary-light-5); color: var(--el-color-primary); }
@media (max-width: 900px) { .starters { grid-template-columns: minmax(0, 1fr); margin-left: 0; } }
/* 第 7.5 项三轮 规则 2：聊天区高度 = 视口高度减去页头，消息区自己滚，输入区固定在底部 */
.chat-page { height: calc(100dvh - 232px) !important; min-height: 520px !important; }
@media (max-width: 800px) {
  .chat-page {
    flex-direction: column;
    height: auto !important;
    min-height: calc(100dvh - 140px) !important;
  }
  .sessions {
    width: 100% !important;
    max-height: none;
    flex: none;
    border-right: 0;
    border-bottom: 1px solid var(--cy-line);
  }
  .sessions-top {
    padding: 10px 14px;
    flex-wrap: nowrap;
    align-items: center;
    justify-content: space-between;
  }
  .top-actions {
    width: auto;
    flex: none;
  }
  .top-actions .el-button {
    flex: none;
  }
  .session-list {
    display: flex;
    flex-direction: row;
    align-items: stretch;
    overflow-x: auto;
    overflow-y: hidden;
    gap: 10px;
    min-height: 96px;
    max-height: 148px;
    flex: none;
    padding: 8px 12px;
  }
  .session-item {
    min-width: 224px;
    max-width: 264px;
    flex: 0 0 auto;
    margin-bottom: 0;
    padding: 10px 12px;
  }
  .s-meta {
    display: flex;
  }
  .sessions .archive-bar {
    padding: 6px 12px;
  }
  .chat-main {
    flex: 1;
    min-height: 440px;
  }
}
</style>
