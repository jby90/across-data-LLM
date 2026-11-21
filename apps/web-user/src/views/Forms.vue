<script setup lang="ts">
import { canEdit as _canEdit, canExport as _canExport } from '@cyshuhe/shared'
import { computed as _computed } from 'vue'
import { useAuth } from '../auth'
const _auth = useAuth()
const editable = _computed(() => _canEdit(_auth.user))
const downloadable = _computed(() => _canExport(_auth.user))
import { serverFilename, localTime, COUNTRY_NAMES } from '@cyshuhe/shared'
import { ElMessage, ElMessageBox } from 'element-plus'
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '../api'
import FormMatrix, { type Meta as MatrixMeta, type RowValue } from '../components/FormMatrix.vue'
import CopilotDrawer from '../components/CopilotDrawer.vue'
import type { CopilotProposal } from '../components/CopilotFillCard.vue'

interface FormSummary {
  code: string; country: string; direction: string; name_zh: string; authority_zh: string; version: string
  legal_basis_zh: string; prerequisite_zh: string; timing_zh: string; source_url: string; language: string; kind?: 'pdf' | 'docx'; items: number; required: number
}
interface Item {
  id: string; label_zh: string; label_fr: string; label_original?: string; kind: 'text' | 'radio' | 'check' | 'matrix'; options: string[] | null; matrix: MatrixMeta | null
  required: boolean; help_zh: string; question_zh: string; max_chars: number | null; store_chars?: number | null; option_labels: Record<string, string>
  value: string | string[] | Record<string, RowValue>; source: 'answer' | 'assessment' | 'missing'
  carried: { text: string; to: string } | null  // 带入的值从哪来、点了去哪改
  inline_after?: string | null  // 这一项排在哪道题的右边（如选其他时的说明）
}
interface Section { id: string; title_zh: string; title_fr: string; title_original?: string; items: Item[] }
interface Instance {
  id: string; task_id: string | null; form: FormSummary; sections: Section[]
  missing: Array<{ id: string; label: string; question: string; section: string }>; ready: boolean
}
interface Row { id: string; form: FormSummary; task_id: string | null; task_title?: string; missing: number; ready: boolean; updated_at: string }

const COUNTRIES: Record<string, string> = COUNTRY_NAMES
const DIRECTION: Record<string, string> = { export: '数据从该国出境', import: '数据进入该国', processing: '在该国处理数据' }
async function remove(r: Row) {
  try {
    await ElMessageBox.confirm(`删除后本表已填写内容将不再保留（关联项目内的基础信息不受影响）。\n\n《${r.form.name_zh}》`, '确认删除表格', { confirmButtonText: '删除', cancelButtonText: '取消', type: 'warning' })
  } catch { return }
  try { await api.delete(`/api/forms/instances/${r.id}`); await loadList(); ElMessage.success('已删除') } catch (e) { ElMessage.error((e as Error).message) }
}
const LANGUAGE_ZH: Record<string, string> = { fr: '法语', ar: '阿拉伯语', en: '英语', zh: '中文' }
const OPTION_ZH: Record<string, string> = {
  physique: '自然人', morale: '法人', cni: '身份证（C.N.I）', passeport: '护照', autre: '其他', rc: '商事登记（RC）', siren: 'SIREN',
  oui: '是', non: '否', declaration: '申报', autorisation: '授权', succursales: '分支机构', siege: '总部', clients: '客户',
  sous_traitant: '分包商', fournisseurs: '供应商', autres: '其他',
}

const route = useRoute()
const router = useRouter()
const catalog = ref<FormSummary[]>([])
const rows = ref<Row[]>([])
const current = ref<Instance | null>(null)
const draft = ref<Record<string, string | string[] | Record<string, RowValue>>>({})
const loading = ref(false)
const saving = ref(false)
const exporting = ref(false)
const showFilled = ref(false)

const instanceId = computed(() => (typeof route.params.id === 'string' ? route.params.id : ''))
const previewCode = computed(() => (instanceId.value.startsWith('preview:') ? instanceId.value.slice(8) : ''))

async function ensureMaterializedId(): Promise<string> {
  if (!previewCode.value) return instanceId.value
  const taskId = typeof route.query.task_id === 'string' && route.query.task_id ? route.query.task_id : null
  const made = await api.post<Instance>('/forms/instances', { form_code: previewCode.value, task_id: taskId })
  await router.replace({ name: 'form', params: { id: made.id }, query: route.query })
  return made.id
}

async function loadList() {
  loading.value = true
  try {
    ;[catalog.value, rows.value] = await Promise.all([api.get<FormSummary[]>('/forms/catalog'), api.get<Row[]>('/forms/instances')])
  } catch (e) { ElMessage.error((e as Error).message) } finally { loading.value = false }
}
async function loadOne() {
  if (!instanceId.value) { current.value = null; return }
  loading.value = true
  try {
    if (previewCode.value) {
      const qs = new URLSearchParams()
      if (typeof route.query.task_id === 'string' && route.query.task_id) qs.set('task_id', route.query.task_id)
      if (typeof route.query.project === 'string' && route.query.project) qs.set('project_id', route.query.project)
      const qstr = qs.toString()
      current.value = await api.get<Instance>(`/forms/preview/${encodeURIComponent(previewCode.value)}${qstr ? `?${qstr}` : ''}`)
      if (current.value && !current.value.id.startsWith('preview:')) {
        await router.replace({ name: 'form', params: { id: current.value.id }, query: route.query })
      }
    } else {
      current.value = await api.get<Instance>(`/forms/instances/${instanceId.value}`)
    }
    draft.value = {}
    if (!editable.value) showFilled.value = true
    void loadHistorySources()
  } catch (e) { ElMessage.error((e as Error).message) } finally { loading.value = false }
  locate()
}
// 链接直达某一填写项（?item=编号）：显示已填项、展开所在的组、滚过去并放光标
const locatedId = ref('')
function locate() {
  const id = typeof route.query.item === 'string' ? route.query.item : ''
  const sec = current.value?.sections.find((s) => s.items.some((i) => i.id === id))
  if (!id || !sec) return
  showFilled.value = true
  const it = sec.items.find((i) => i.id === id)!
  const g = groupsOf({ ...sec, items: sec.items }).find((x) => x.items.some((i) => i.id === id))
  if (g?.fold && !opened.value.includes(sec.id + g.title)) opened.value.push(sec.id + g.title)
  locatedId.value = it.id
  window.setTimeout(() => {
    const el = document.querySelector<HTMLElement>(`[data-item="${CSS.escape(id)}"]`)
    el?.scrollIntoView({ behavior: 'smooth', block: 'center' })
    el?.querySelector<HTMLElement>('input, textarea')?.focus({ preventScroll: true })
  }, 250)
}
// 固定操作条下一项：跳到下一个还没填的必填项
function nextMissing() {
  const ids = Array.from(document.querySelectorAll<HTMLElement>('.field.missing')).map((e) => e.dataset.item || '')
  const after = ids.findIndex((x) => x === locatedId.value)
  const id = ids[(after + 1) % Math.max(ids.length, 1)]
  if (!id) return
  locatedId.value = id
  const el = document.querySelector<HTMLElement>(`[data-item="${CSS.escape(id)}"]`)
  el?.scrollIntoView({ behavior: 'smooth', block: 'center' })
  el?.querySelector<HTMLElement>('input, textarea')?.focus({ preventScroll: true })
}
const inputLimit = (it: Item) => Math.max(it.store_chars || 0, it.max_chars || 0) || 400
const exceedsBox = (it: Item) => it.kind === 'text' && it.max_chars != null && String(value(it) ?? '').trim().length > it.max_chars
const annexItemsCount = computed(() => {
  if (!current.value || current.value.form.kind === 'docx') return 0
  let n = 0
  for (const sec of current.value.sections) {
    for (const it of sec.items) if (exceedsBox(it)) n++
  }
  return n
})
const nearLimit = (it: Item) => (it.max_chars != null && it.max_chars <= 120) || String(value(it) ?? '').length >= (it.max_chars || 400) * 0.6
function start(code: string) {
  router.push({ name: 'form', params: { id: `preview:${code}` }, query: { back: '/forms' } })
}
const optionText = (it: Item, o: string) => it.option_labels?.[o] || OPTION_ZH[o] || o
const versionText = (v: string) => (/^\d/.test(v) ? `${v} 版` : v)
const NOISE_ORIGINAL_RE = /^[.\s…_·\-—_/\\{}｛｝()（）[\]【】☐☑☒,:;]+$/
function cleanOriginal(raw?: string | null): string {
  const t = (raw || '')
    .replace(/[{}｛｝☐☑☒]/g, '')
    .replace(/\s*:\s*$/, '')
    .replace(/^[.\s…_·\-—_/\\,]+|[.\s…_·\-—_/\\,]+$/g, '')
    .replace(/\s{2,}/g, ' ')
    .trim()
  if (!t || NOISE_ORIGINAL_RE.test(t) || /^\([a-z0-9]\)$/i.test(t)) return ''
  if (!/[A-Za-z0-9\u00C0-\u024F\u4e00-\u9fff\u0600-\u06ff]/.test(t)) return ''
  return t
}
function itemOriginal(it: Item): string {
  return cleanOriginal(it.label_original ?? it.label_fr)
}
function sectionOriginal(sec: Section): string {
  return cleanOriginal(sec.title_original ?? sec.title_fr)
}
function baseZhLabel(label: string): string {
  return (label.includes('：') ? label.split('：').slice(1).join('：') : label).trim()
}
const hasDuplicateSignDate = computed(() => {
  if (!current.value) return false
  let count = 0
  for (const sec of current.value.sections) {
    for (const it of sec.items) {
      if (baseZhLabel(it.label_zh) === '签署日期') count++
    }
  }
  return count >= 2
})
function displayItemLabel(it: Item, stripPrefix = false): string {
  const base = stripPrefix && it.label_zh.includes('：') ? it.label_zh.split('：').slice(1).join('：') : it.label_zh
  if (!hasDuplicateSignDate.value || baseZhLabel(it.label_zh) !== '签署日期') return base
  const orig = (it.label_original ?? it.label_fr ?? '').trim()
  const hasAr = /[\u0600-\u06FF]/.test(orig) || /(^|[_-])ar($|[_-])/i.test(it.id)
  const hasLat = /[A-Za-zÀ-ÿ]/.test(orig) || /(^|[_-])fr($|[_-])/i.test(it.id)
  if (hasAr && !hasLat) return `${base}（阿拉伯文）`
  if (hasLat && !hasAr) return `${base}（法文）`
  return base
}
// 每一题的状态只看现在框里有没有值（含刚输入还没保存的），四种：必填未填 / 选填未填 / 已填（来自合规评估或企业填写）/ 已改未保存
const isEmpty = (v: unknown): boolean => v == null || (typeof v === 'string' && !v.trim()) || (Array.isArray(v) && !v.length)
  || (typeof v === 'object' && !Array.isArray(v) && !Object.values(v as Record<string, RowValue>).some((r) => r && (r as RowValue).on))
const filled = (it: Item) => !isEmpty(value(it))
function status(it: Item): { text: string; type: 'warning' | 'info' | 'success' | 'primary' } {
  if (!filled(it)) return it.required ? { text: '必填 · 未填', type: 'warning' } : { text: '选填', type: 'info' }
  if (it.id in draft.value) return { text: '已改 · 待保存', type: 'primary' }
  return it.source === 'assessment' ? { text: it.carried?.text || '已带入', type: 'success' } : { text: '已填写', type: 'success' }
}
// 带入的填写项：点了回到来源（项目、另一张官方表格）去改；改过待保存的不再算带入
const carriedTo = (it: Item) => (it.source === 'assessment' && !(it.id in draft.value) && filled(it) ? it.carried?.to || '' : '')
function goCarried(it: Item) { const to = carriedTo(it); if (to) router.push(to) }
// 还差几项必填：本组或本节里必填且现在为空的题；矩阵题另算服务器核出的缺项（改过就等保存后重新核）
const lacking = (items: Item[]) => items.filter((i) => i.required && !filled(i)).length
  + (current.value?.missing || []).filter((m) => items.some((i) => m.id.startsWith(i.id + '/') && !(i.id in draft.value))).length
const optional = (items: Item[]) => items.every((i) => !i.required)
const value = (it: Item) => (it.id in draft.value ? draft.value[it.id] : it.value)
const setText = (it: Item, v: string) => { if (!editable.value) return; draft.value = { ...draft.value, [it.id]: v } }
const setMatrix = (it: Item, v: Record<string, RowValue>) => { if (!editable.value) return; draft.value = { ...draft.value, [it.id]: v } }
const matrixValue = (it: Item) => value(it) as Record<string, RowValue>
const setCheck = (it: Item, opt: string, on: boolean) => {
  if (!editable.value) return
  const base = new Set(Array.isArray(value(it)) ? (value(it) as string[]) : [])
  on ? base.add(opt) : base.delete(opt)
  draft.value = { ...draft.value, [it.id]: [...base] }
}
const shown = (sec: Section) => (showFilled.value ? sec.items : sec.items.filter((i) => i.source === 'missing' || i.id in draft.value))
const dirty = computed(() => Object.keys(draft.value).length)
// 填写项较多时按前缀归成一组，三项以上的组可折叠
interface Group { title: string; items: Item[]; fold: boolean }
// 排在某道题右边的补充项（选其他时的说明）：不单独占一行，跟在那道题后面
const followersOf = (sec: Section, lead: Item) => sec.items.filter((f) => f.inline_after === lead.id)
function groupsOf(sec: Section): Group[] {
  const out: Group[] = []
  for (const it of shown(sec).filter((x) => !x.inline_after)) {
    const title = it.label_zh.includes('：') ? it.label_zh.split('：')[0] : it.label_zh
    const last = out[out.length - 1]
    if (last && last.title === title) last.items.push(it)
    else out.push({ title, items: [it], fold: false })
  }
  for (const g of out) g.fold = g.items.length >= 3
  return out
}
const opened = ref<string[]>([])

interface FieldDiff {
  id: string
  label: string
  before: string
  after: string
}
interface FormSnapshotEntry {
  id: string
  created_at: string
  source_label: string
  changes: FieldDiff[]
  answers?: Record<string, unknown>
}
interface ProjectDraftFormVersion {
  draft_id: string
  project_title: string
  created_at: string
  missing_count: number
  form_code: string
  form_name: string
}

const historyOpen = ref(false)
const historyLoading = ref(false)
const serverSnapshots = ref<FormSnapshotEntry[]>([])
const localSnapshots = ref<FormSnapshotEntry[]>([])
const projectDraftVersions = ref<ProjectDraftFormVersion[]>([])

function formatAnswerDisplay(it: Item | undefined, val: unknown): string {
  if (val == null) return '（未填写）'
  if (typeof val === 'string') return val.trim() || '（未填写）'
  if (Array.isArray(val)) {
    if (!val.length) return '（未填写）'
    return val.map((v) => (it ? optionText(it, String(v)) : String(v))).join('、')
  }
  if (typeof val === 'object') {
    const picked = Object.entries(val as Record<string, RowValue>)
      .filter(([, rv]) => rv && rv.on)
      .map(([rk]) => it?.matrix?.rows.find((r) => r.id === rk)?.label_zh || rk)
    return picked.length ? `已选 ${picked.length} 类：${picked.join('、')}` : '（未勾选类别）'
  }
  return String(val)
}

function itemMapOf(inst: Instance | null): Map<string, Item> {
  const map = new Map<string, Item>()
  if (!inst) return map
  for (const sec of inst.sections) {
    for (const it of sec.items) map.set(it.id, it)
  }
  return map
}

function snapshotStorageKey(id: string): string {
  return `cyshuhe.form.snapshots.${id}`
}

function loadLocalSnapshots(id: string) {
  if (!id || id.startsWith('preview:')) {
    localSnapshots.value = []
    return
  }
  try {
    const raw = localStorage.getItem(snapshotStorageKey(id))
    const parsed = raw ? JSON.parse(raw) : []
    localSnapshots.value = Array.isArray(parsed) ? parsed : []
  } catch {
    localSnapshots.value = []
  }
}

function recordLocalSnapshot(realId: string, beforeInst: Instance, patch: Record<string, unknown>, sourceLabel: string) {
  const map = itemMapOf(beforeInst)
  const changes: FieldDiff[] = []
  for (const [k, nextVal] of Object.entries(patch)) {
    const it = map.get(k)
    const beforeStr = formatAnswerDisplay(it, it?.value)
    const afterStr = formatAnswerDisplay(it, nextVal)
    if (beforeStr !== afterStr) {
      changes.push({
        id: k,
        label: it ? displayItemLabel(it, false) : k,
        before: beforeStr,
        after: afterStr,
      })
    }
  }
  if (!changes.length) return
  const entry: FormSnapshotEntry = {
    id: `local-${Date.now()}`,
    created_at: new Date().toISOString(),
    source_label: sourceLabel,
    changes,
  }
  const next = [entry, ...localSnapshots.value].slice(0, 20)
  localSnapshots.value = next
  try {
    localStorage.setItem(snapshotStorageKey(realId), JSON.stringify(next))
  } catch {
    /* 忽略本地存储容量限制 */
  }
}

async function loadHistorySources() {
  if (!current.value) return
  const cur = current.value
  if (!previewCode.value) loadLocalSnapshots(cur.id)
  else localSnapshots.value = []

  historyLoading.value = true
  try {
    serverSnapshots.value = []

    const versions: ProjectDraftFormVersion[] = []
    const projIdQuery = typeof route.query.project === 'string' ? route.query.project : ''
    const projectIds: string[] = []
    if (projIdQuery) projectIds.push(projIdQuery)
    else {
      const allAdm = await api
        .get<Array<{ id: string; country_code: string; source: { id: string } | null }>>('/admission/projects')
        .catch(() => [])
      for (const p of allAdm) {
        if (p.country_code === cur.form.country && (!cur.task_id || p.source?.id === cur.task_id)) {
          projectIds.push(p.id)
        }
      }
    }
    for (const pid of projectIds.slice(0, 4)) {
      const detail = await api
        .get<{
          title: string
          drafts: Array<{ id: string; created_at: string; missing: string[]; forms: Array<{ code: string; name: string }> }>
        }>(`/admission/projects/${pid}`)
        .catch(() => null)
      if (!detail?.drafts?.length) continue
      for (const d of detail.drafts) {
        const matched = (d.forms || []).find((f) => f.code === cur.form.code)
        if (matched) {
          versions.push({
            draft_id: d.id,
            project_title: detail.title,
            created_at: d.created_at,
            missing_count: d.missing?.length || 0,
            form_code: matched.code,
            form_name: matched.name || cur.form.name_zh,
          })
        }
      }
    }
    projectDraftVersions.value = versions
  } finally {
    historyLoading.value = false
  }
}

const combinedSnapshots = computed(() => {
  return serverSnapshots.value.length ? serverSnapshots.value : localSnapshots.value
})
const totalHistoryCount = computed(() => combinedSnapshots.value.length + projectDraftVersions.value.length)

function openHistoryDrawer() {
  historyOpen.value = true
  void loadHistorySources()
}

async function downloadSnapshotForm(v: ProjectDraftFormVersion) {
  if (!downloadable.value) return
  try {
    const res = await fetch(`/api/admission/drafts/${v.draft_id}/forms/${v.form_code}`, {
      headers: { Authorization: `Bearer ${api.token()}` },
    })
    if (!res.ok) throw new Error('下载历史版本原表失败，请稍后重试')
    const url = URL.createObjectURL(await res.blob())
    const a = document.createElement('a')
    a.href = url
    a.download = serverFilename(res, `${v.form_name}.pdf`)
    document.body.appendChild(a)
    a.click()
    a.remove()
    window.setTimeout(() => URL.revokeObjectURL(url), 30000)
  } catch (e) {
    ElMessage.error((e as Error).message)
  }
}

async function save() {
  if (!current.value || !dirty.value) return
  saving.value = true
  try {
    const beforeSnap = current.value
    const pendingPatch = { ...draft.value }
    const realId = await ensureMaterializedId()
    current.value = await api.put<Instance>(`/forms/instances/${realId}/answers`, { answers: draft.value })
    recordLocalSnapshot(realId, beforeSnap, pendingPatch, '手动保存填报内容')
    draft.value = {}
    ElMessage.success(current.value.ready ? '必填项已全部填完，可以导出官方表格' : `已保存，还有 ${current.value.missing.length} 项必填未填`)
  } catch (e) { ElMessage.error((e as Error).message) } finally { saving.value = false }
}
async function exportPdf() {
  if (!current.value || !downloadable.value) return
  if (dirty.value) await save()
  exporting.value = true
  try {
    const realId = await ensureMaterializedId()
    const needAnnex = annexItemsCount.value > 0
    let res = await fetch(`/api/forms/instances/${realId}/export.pdf${needAnnex ? '?annex=true' : ''}`, { headers: { Authorization: `Bearer ${api.token()}` } })
    if (res.status === 409 && !needAnnex && current.value.form.kind !== 'docx') {
      const errBody = await res.clone().json().catch(() => ({ detail: '' }))
      if (String(errBody.detail || '').includes('放不进官方表格栏位') && !String(errBody.detail || '').includes('法文或英文')) {
        res = await fetch(`/api/forms/instances/${realId}/export.pdf?annex=true`, { headers: { Authorization: `Bearer ${api.token()}` } })
      }
    }
    if (!res.ok) {
      let msg = '导出失败，可重试'
      try { msg = (await res.json()).detail || msg } catch { /* 非标准响应体 */ }
      throw new Error(msg)
    }
    const url = URL.createObjectURL(await res.blob())
    const a = document.createElement('a')
    a.href = url; a.download = serverFilename(res, `${current.value.form.name_zh}.${current.value.form.kind === 'docx' ? 'docx' : 'pdf'}`)
    document.body.appendChild(a); a.click(); a.remove()
    window.setTimeout(() => URL.revokeObjectURL(url), 30000)
    ElMessage.success(annexItemsCount.value > 0 ? '已导出官方表格（超长栏目已自动生成附页续表合并至末尾），请核对后签章' : '已导出官方表格，请核对后打印签字盖章')
  } catch (e) { ElMessage.error((e as Error).message) } finally { exporting.value = false }
}
const backTarget = computed(() => {
  const back = typeof route.query.back === 'string' ? route.query.back : ''
  if (back.startsWith('/')) return back
  if (previewCode.value) {
    if (typeof route.query.task_id === 'string' && route.query.task_id) {
      return `/documents?project=${encodeURIComponent(route.query.task_id)}`
    }
    if (typeof route.query.project === 'string' && route.query.project) {
      return `/admission?project=${encodeURIComponent(route.query.project)}`
    }
    return '/forms'
  }
  if (current.value?.task_id) {
    return `/documents?project=${encodeURIComponent(current.value.task_id)}`
  }
  return '/forms'
})
const backButtonText = computed(() => {
  const target = backTarget.value
  if (target.startsWith('/admission?tab=forms') || target === '/forms') return '← 返回法定申报表目录'
  if (target.startsWith('/admission')) return '← 返回准入办理项目'
  if (target.startsWith('/documents')) return '← 返回关联评估项目'
  return '← 返回上一页'
})
function backToReport() {
  void router.push(backTarget.value)
}

const copilotRef = ref<InstanceType<typeof CopilotDrawer> | null>(null)
const copilotOpen = ref(false)
const copilotWidth = ref(390)
const copilotStyle = computed(() => {
  if (!instanceId.value) return undefined
  return copilotOpen.value ? { '--copilot-pad': `${Math.max(360, copilotWidth.value + 16)}px` } : { '--copilot-pad': '56px' }
})
const formCopilotWelcome = computed(() => {
  if (!current.value) return ''
  const f = current.value.form
  const lang = LANGUAGE_ZH[f.language] || f.language || '原表语种'
  return `您好，我是《${f.name_zh}》的项目助手，本表须使用${lang}填写。您可以直接用中文说明企业、联系人或数据处理情况，系统将按${lang}生成预览填报项供您核对填入；也可随时咨询任意栏目的填写口径。`
})
function openCopilot() {
  copilotRef.value?.openPanel()
}
async function applyFormProposals(items: CopilotProposal[]) {
  if (!current.value || !items.length) return
  const beforeSnap = current.value
  const realId = await ensureMaterializedId()
  const patch: Record<string, unknown> = { ...draft.value }
  for (const it of items) patch[it.key] = it.value
  current.value = await api.put<Instance>(`/forms/instances/${realId}/answers`, { answers: patch })
  recordLocalSnapshot(realId, beforeSnap, patch, '项目助手联动填入')
  draft.value = {}
  showFilled.value = true
  for (const it of items) {
    const sec = current.value.sections.find((s) => s.items.some((x) => x.id === it.key))
    if (!sec) continue
    const g = groupsOf({ ...sec, items: sec.items }).find((x) => x.items.some((i) => i.id === it.key))
    if (g?.fold && !opened.value.includes(sec.id + g.title)) opened.value.push(sec.id + g.title)
  }
  locatedId.value = items[0].key
  window.setTimeout(() => {
    const el = document.querySelector<HTMLElement>(`[data-item="${CSS.escape(items[0].key)}"]`)
    el?.scrollIntoView({ behavior: 'smooth', block: 'center' })
  }, 180)
  ElMessage.success(`已联动填入 ${items.length} 个填写项`)
}
async function undoFormProposals(items: CopilotProposal[]) {
  if (!current.value || !items.length) return
  const patch: Record<string, unknown> = {}
  for (const it of items) patch[it.key] = it.previous_value ?? (Array.isArray(it.value) ? [] : '')
  current.value = await api.put<Instance>(`/forms/instances/${current.value.id}/answers`, { answers: patch })
  ElMessage.info(`已撤回刚才填入的 ${items.length} 个填写项`)
}

onMounted(() => (instanceId.value ? loadOne() : loadList()))
watch(instanceId, () => (instanceId.value ? loadOne() : loadList()))
</script>

<template>
  <div class="management-page forms-page" :class="{ 'has-copilot': !!instanceId, 'is-copilot-open': !!instanceId && copilotOpen }" :style="copilotStyle" v-loading="loading">
    <template v-if="!instanceId">
      <header class="page-heading">
        <div><h1>法定申报表</h1><p>汇集各国主管机关发布的法定申报表，自动带入已关联项目的填报信息并按正式版式导出。</p></div>
      </header>
      <section v-if="rows.length">
        <h2>{{ editable ? '填写中的官方表格' : '本企业已填表格' }}</h2>
        <button v-for="r in rows" :key="r.id" class="row" data-testid="form-instance" @click="router.push({ name: 'form', params: { id: r.id }, query: { back: '/forms' } })">
          <span class="main"><strong>{{ r.form.name_zh }}</strong><span>{{ r.task_title ? `关联项目：${r.task_title}` : '未关联项目' }} · {{ localTime(r.updated_at, 'date') }} 更新</span></span>
          <el-tag :type="r.ready ? 'success' : 'warning'" size="small">{{ r.ready ? (editable ? '可导出' : '已填齐') : `还缺 ${r.missing} 项必填` }}</el-tag>
          <span v-if="editable" class="del" role="button" tabindex="0" data-testid="form-delete" @click.stop="remove(r)" @keydown.enter.stop="remove(r)">删除</span>
        </button>
      </section>
      <section>
        <h2>官方表格目录</h2>
        <article v-for="f in catalog" :key="f.code" class="card" data-testid="form-catalog">
          <div class="main">
            <strong>{{ f.name_zh }}</strong>
            <span>{{ COUNTRIES[f.country] }} · {{ DIRECTION[f.direction] }} · {{ f.authority_zh }} · {{ versionText(f.version) }}</span>
            <small>依据法规：{{ f.legal_basis_zh }}。{{ f.timing_zh }}。</small>
            <small class="warn">办理前提：{{ f.prerequisite_zh }}</small>
          </div>
          <div class="side"><el-button v-if="editable" @click="start(f.code)">开始填写</el-button><a :href="f.source_url" target="_blank" rel="noopener noreferrer">官方原件 ↗</a></div>
        </article>
        <p class="hint">目前收录中国、新加坡、埃及、摩洛哥、突尼斯、黎巴嫩等国的官方申报与准入表格；暂未发布专用官方表格的事项，系统按目标国法条生成申请材料清单。</p>
      </section>
    </template>

    <template v-else-if="current">
      <header class="page-heading">
        <div>
          <el-button link data-testid="form-back" @click="backToReport">{{ backButtonText }}</el-button>
          <h1>{{ current.form.name_zh }}</h1>
          <p>{{ current.form.authority_zh }} · {{ versionText(current.form.version) }} · 依据法规：{{ current.form.legal_basis_zh }}</p>
        </div>
      </header>
      <el-alert v-if="!editable" type="info" :closable="false" show-icon title="只读查看模式：可查阅本企业已填项内容，不能新建或修改。" style="margin-bottom: 10px" />
      <el-alert
        v-else-if="current.form.language && current.form.language !== 'zh'"
        type="warning"
        :closable="false"
        show-icon
        :title="`本表须使用${LANGUAGE_ZH[current.form.language] || current.form.language}填写`"
      />
      <div class="form-bar" data-testid="form-bar">
        <span class="form-bar-main" data-testid="form-progress">{{ current.ready ? '必填项已全部填齐' : `还缺 ${current.missing.length} 项必填` }}<span v-if="annexItemsCount" class="annex-pill" data-testid="form-annex-badge">{{ annexItemsCount }} 项超框将自动附页续表</span><span v-if="dirty" class="unsaved">{{ dirty }} 处修改未保存</span></span>
        <el-checkbox v-model="showFilled">显示已填项</el-checkbox>
        <el-button v-if="!previewCode || route.query.project || current.task_id || totalHistoryCount > 0" size="small" data-testid="form-version-history" @click="openHistoryDrawer">版本记录<template v-if="totalHistoryCount">（{{ totalHistoryCount }}）</template></el-button>
        <el-button v-if="editable" size="small" data-testid="form-open-copilot" @click="openCopilot">辅助填写</el-button>
        <el-button v-if="editable && !current.ready" size="small" data-testid="form-next" @click="nextMissing">下一项</el-button>
        <el-button v-if="editable" size="small" :type="!current.ready || !downloadable ? 'primary' : 'default'" :loading="saving" :disabled="!dirty" @click="save">保存</el-button>
        <el-tooltip :disabled="downloadable" content="当前只读账号未开通导出权限" placement="top">
          <span>
            <el-button
              size="small"
              :type="downloadable && (current.ready || !editable) ? 'primary' : 'default'"
              :disabled="!downloadable"
              :loading="exporting"
              data-testid="form-export"
              @click="exportPdf"
            >导出{{ current.form.kind === 'docx' ? ' Word' : ' PDF' }}</el-button>
          </span>
        </el-tooltip>
      </div>
      <section v-for="sec in current.sections" :key="sec.id" class="section">
        <h2>{{ sec.title_zh }}<small v-if="sectionOriginal(sec)">{{ sectionOriginal(sec) }}</small></h2>
        <el-empty v-if="!shown(sec).length" description="本节必填项已全部填齐" :image-size="48" />
        <template v-for="g in groupsOf(sec)" :key="sec.id + g.title">
          <el-collapse v-if="g.fold" v-model="opened" class="fold">
            <el-collapse-item :name="sec.id + g.title">
              <template #title><strong>{{ g.title }}</strong></template>
              <div v-for="it in g.items" :key="it.id" class="field" :class="{ missing: it.required && !filled(it), located: locatedId === it.id }" :data-item="it.id" data-testid="form-field">
                <div class="label"><strong>{{ displayItemLabel(it, true) }}<em v-if="it.required">*</em><el-tooltip v-if="it.help_zh" :content="it.help_zh" placement="top-start"><span class="help-ic" tabindex="0" aria-label="填写提示">?</span></el-tooltip></strong><small v-if="itemOriginal(it)">{{ itemOriginal(it) }}</small>
                  <el-tag v-if="status(it).text !== '选填' && status(it).text !== '必填 · 未填'" size="small" :type="status(it).type" effect="plain" data-testid="form-field-state" :class="{ link: carriedTo(it) }" @click="goCarried(it)">{{ status(it).text }}</el-tag></div>
                <div class="input">
                  <FormMatrix v-if="it.kind === 'matrix' && it.matrix" :meta="it.matrix" :language="current.form.language" :disabled="!editable" :model-value="matrixValue(it)" @update:model-value="(v: Record<string, RowValue>) => setMatrix(it, v)" />
                  <el-input v-else-if="it.kind === 'text'" :disabled="!editable" :model-value="(value(it) as string)" :placeholder="it.question_zh" :maxlength="inputLimit(it)" :show-word-limit="nearLimit(it)"
                    @update:model-value="(v: string) => setText(it, v)" />
                  <small v-if="exceedsBox(it)" class="annex-hint" data-testid="form-annex-hint">当前 {{ String(value(it) ?? '').trim().length }} 字（原表框格上限 {{ it.max_chars }} 字）：导出 PDF 时原表框内填入摘要索引，末尾自动追加《附页续表》完整呈现。</small>
                  <el-radio-group v-else-if="it.kind === 'radio'" :disabled="!editable" :model-value="(value(it) as string)" @update:model-value="(v: string) => setText(it, v)">
                    <el-radio v-for="o in it.options" :key="o" :value="o">{{ optionText(it, o) }}</el-radio>
                  </el-radio-group>
                  <span v-else><el-checkbox v-for="o in it.options" :key="o" :disabled="!editable" :model-value="Array.isArray(value(it)) && (value(it) as string[]).includes(o)" @update:model-value="(on: boolean) => setCheck(it, o, on)">{{ optionText(it, o) }}</el-checkbox></span>
                  <el-input v-for="f in followersOf(sec, it)" :key="f.id" class="follow" :disabled="!editable" :model-value="(value(f) as string)" :placeholder="f.question_zh" :maxlength="f.max_chars || 400" :show-word-limit="nearLimit(f)" data-testid="form-follow" @update:model-value="(v: string) => setText(f, v)" />
                  
                </div>
              </div>
            </el-collapse-item>
          </el-collapse>
          <div v-else v-for="it in g.items" :key="it.id" class="field" :class="{ missing: it.required && !filled(it), located: locatedId === it.id }" :data-item="it.id" data-testid="form-field">
            <div class="label"><strong>{{ displayItemLabel(it, false) }}<em v-if="it.required">*</em><el-tooltip v-if="it.help_zh" :content="it.help_zh" placement="top-start"><span class="help-ic" tabindex="0" aria-label="填写提示">?</span></el-tooltip></strong><small v-if="itemOriginal(it)">{{ itemOriginal(it) }}</small>
              <el-tag v-if="status(it).text !== '选填' && status(it).text !== '必填 · 未填'" size="small" :type="status(it).type" effect="plain" data-testid="form-field-state" :class="{ link: carriedTo(it) }" @click="goCarried(it)">{{ status(it).text }}</el-tag></div>
            <div class="input">
              <FormMatrix v-if="it.kind === 'matrix' && it.matrix" :meta="it.matrix" :language="current.form.language" :disabled="!editable" :model-value="matrixValue(it)" @update:model-value="(v: Record<string, RowValue>) => setMatrix(it, v)" />
              <el-input v-else-if="it.kind === 'text'" :disabled="!editable" :model-value="(value(it) as string)" :placeholder="it.question_zh" :maxlength="inputLimit(it)" :show-word-limit="nearLimit(it)"
                @update:model-value="(v: string) => setText(it, v)" />
              <small v-if="exceedsBox(it)" class="annex-hint" data-testid="form-annex-hint">当前 {{ String(value(it) ?? '').trim().length }} 字（原表框格上限 {{ it.max_chars }} 字）：导出 PDF 时原表框内填入摘要索引，末尾自动追加《附页续表》完整呈现。</small>
              <el-radio-group v-else-if="it.kind === 'radio'" :disabled="!editable" :model-value="(value(it) as string)" @update:model-value="(v: string) => setText(it, v)">
                <el-radio v-for="o in it.options" :key="o" :value="o">{{ optionText(it, o) }}</el-radio>
              </el-radio-group>
              <span v-else><el-checkbox v-for="o in it.options" :key="o" :disabled="!editable" :model-value="Array.isArray(value(it)) && (value(it) as string[]).includes(o)" @update:model-value="(on: boolean) => setCheck(it, o, on)">{{ optionText(it, o) }}</el-checkbox></span>
              <el-input v-for="f in followersOf(sec, it)" :key="f.id" class="follow" :disabled="!editable" :model-value="(value(f) as string)" :placeholder="f.question_zh" :maxlength="f.max_chars || 400" :show-word-limit="nearLimit(f)" data-testid="form-follow" @update:model-value="(v: string) => setText(f, v)" />
              
            </div>
          </div>
        </template>
      </section>
      <el-drawer
        v-model="historyOpen"
        title="表单版本记录与修改对比"
        size="min(680px, 92vw)"
        append-to-body
      >
        <div v-loading="historyLoading" class="history-drawer" data-testid="form-history-drawer">
          <section v-if="projectDraftVersions.length" class="hist-sec">
            <h3>关联项目存档版本（{{ projectDraftVersions.length }}）</h3>
            <p class="hist-tip">准入项目每次生成申请材料时会自动留存当时的官方表格快照，可按需核对或下载对应历史版本。</p>
            <div v-for="v in projectDraftVersions" :key="v.draft_id" class="hist-card" data-testid="form-history-draft">
              <div class="hist-card-head">
                <div>
                  <strong>{{ v.project_title }}</strong>
                  <span class="hist-time">{{ localTime(v.created_at) }} 留存 · {{ v.missing_count ? `留存时缺 ${v.missing_count} 项` : '留存时已填齐' }}</span>
                </div>
                <el-tooltip :disabled="downloadable" content="当前只读账号未开通导出权限" placement="top">
                  <span>
                    <el-button size="small" :disabled="!downloadable" @click="downloadSnapshotForm(v)">下载该版原表</el-button>
                  </span>
                </el-tooltip>
              </div>
            </div>
          </section>

          <section class="hist-sec">
            <h3>填报修改对比记录<template v-if="combinedSnapshots.length">（{{ combinedSnapshots.length }}）</template></h3>
            <p v-if="!combinedSnapshots.length" class="hist-empty">
              当前表格尚无历史修改快照。每次点击「保存」或采用项目助手填入内容时，将自动记录字段修改前后对比。
            </p>
            <div v-for="snap in combinedSnapshots" :key="snap.id" class="hist-card" data-testid="form-history-snapshot">
              <div class="hist-card-head">
                <strong>{{ snap.source_label }}</strong>
                <span class="hist-time">{{ snap.created_at ? localTime(snap.created_at) : '' }} · 变更 {{ snap.changes.length }} 项</span>
              </div>
              <ul v-if="snap.changes.length" class="hist-diff-list">
                <li v-for="ch in snap.changes" :key="ch.id" class="hist-diff-item">
                  <span class="hist-field">{{ ch.label }}</span>
                  <div class="hist-diff-vals">
                    <span class="hist-old">{{ ch.before }}</span>
                    <span class="hist-arr">→</span>
                    <span class="hist-new">{{ ch.after }}</span>
                  </div>
                </li>
              </ul>
            </div>
          </section>
        </div>
      </el-drawer>

      <CopilotDrawer
        ref="copilotRef"
        :storage-key="`form.${current.id}`"
        :endpoint="`/api/forms/instances/${current.id}/assist`"
        title="项目助手"
        tab-label="项目助手"
        :welcome="formCopilotWelcome"
        placeholder="用中文说明企业、联系人或处理情况，或咨询填写口径"
        :readonly="!editable"
        :apply-fn="applyFormProposals"
        :undo-fn="undoFormProposals"
        @layout="(o: boolean, w: number) => { copilotOpen = o; copilotWidth = w }"
      />
    </template>
  </div>
</template>

<style scoped>
.help-ic { display: inline-grid; place-items: center; width: 18px; height: 18px; margin-left: 8px; border: 1px solid var(--el-border-color); border-radius: 50%; font-size: 13px; font-weight: 400; color: var(--cy-text-2); cursor: help; vertical-align: 1px; }
.help-ic:hover { color: var(--el-color-primary); border-color: var(--el-color-primary-light-5); }
.field.missing { background: #fffbeb; }
h2 { font-size: 16px; margin: 24px 0 10px; display: flex; gap: 10px; align-items: baseline; }
h2 small { font-weight: 400; color: var(--el-text-color-secondary); font-size: 13px; }
.row, .card { display: flex; gap: 18px; align-items: center; width: 100%; text-align: left; padding: 14px 18px; margin-bottom: 8px; background: white; border: 1px solid var(--el-border-color-light); border-radius: 12px; font: inherit; }
.row { display: grid; grid-template-columns: minmax(0, 1fr) 136px auto; column-gap: 18px; cursor: pointer; } .row:hover { border-color: var(--el-color-primary-light-5); }
.row :deep(.el-tag) { justify-self: start; }
.row .del { color: #64748b; font-size: 13px; flex: none; } .row .del:hover { color: #dc2626; }
.main { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 4px; }
.main span, .main small { color: var(--el-text-color-secondary); font-size: 13px; line-height: 1.6; }
.main .warn { color: #9a5b00; }
.side { display: flex; flex-direction: column; gap: 8px; align-items: flex-end; }
.hint { color: var(--el-text-color-secondary); font-size: 13px; }
.el-tag.link { cursor: pointer; }
.el-tag.link:hover { text-decoration: underline; }
.actions { display: flex; gap: 10px; }
.forms-page.has-copilot { padding-right: var(--copilot-pad, 56px); transition: padding-right 0.18s ease; }
@media (max-width: 1100px) { .forms-page.has-copilot { padding-right: 52px; } }
.history-drawer { display: flex; flex-direction: column; gap: 20px; padding-bottom: 20px; }
.hist-sec h3 { margin: 0 0 8px; font-size: 15px; color: #0f172a; }
.hist-tip, .hist-empty { margin: 0 0 10px; font-size: 13px; color: #64748b; line-height: 1.65; }
.hist-card { border: 1px solid #e2e8f0; border-radius: 10px; padding: 12px 14px; background: #f8fafc; margin-bottom: 10px; }
.hist-card-head { display: flex; justify-content: space-between; align-items: center; gap: 12px; flex-wrap: wrap; }
.hist-card-head strong { font-size: 14px; color: #1e293b; display: block; }
.hist-time { font-size: 13px; color: #64748b; }
.hist-diff-list { list-style: none; margin: 10px 0 0; padding: 8px 0 0; border-top: 1px dashed #cbd5e1; display: flex; flex-direction: column; gap: 8px; }
.hist-diff-item { font-size: 13px; line-height: 1.6; }
.hist-field { font-weight: 600; color: #334155; display: block; margin-bottom: 2px; }
.hist-diff-vals { display: flex; flex-wrap: wrap; gap: 6px; align-items: baseline; }
.hist-old { color: #64748b; text-decoration: line-through; word-break: break-word; }
.hist-arr { color: #94a3b8; }
.hist-new { color: #0f766e; font-weight: 600; word-break: break-word; }
.form-bar { position: sticky; top: 68px; z-index: 18; display: flex; align-items: center; gap: 10px; flex-wrap: wrap; margin: 12px 0 0; padding: 10px 16px; background: #fff; border: 1px solid var(--cy-line); border-radius: 10px; box-shadow: 0 4px 12px rgba(15, 23, 42, .06); }
.form-bar-main { flex: 1; font-size: 14px; font-weight: 600; }
.form-bar .el-button + .el-button { margin-left: 0; }
.help-i { display: inline-block; margin-top: 4px; font-size: 13px; color: var(--el-color-primary); cursor: help; border-bottom: 1px dashed var(--el-color-primary-light-5); }
.alert-more { font-size: 13px; color: var(--cy-text-2); }
.progress { margin: 14px 0 0; display: flex; gap: 18px; align-items: center; color: var(--el-text-color-regular); }
.section { background: white; border: 1px solid var(--el-border-color-light); border-radius: 12px; padding: 4px 22px 14px; margin-top: 14px; }
.unsaved { margin-left: 10px; color: var(--el-color-primary); font-size: 13px; }
.annex-pill { margin-left: 10px; padding: 2px 8px; border-radius: 999px; background: #eff6ff; color: #1d4ed8; font-size: 13px; font-weight: 400; }
.annex-hint { color: #1d4ed8; font-size: 13px; line-height: 1.55; }
.fold { border: none; margin: 4px 0; } .fold :deep(.el-collapse-item__header) { font-size: 14px; }
.field { display: grid; grid-template-columns: 300px 1fr; gap: 16px; padding: 10px 0; border-bottom: 1px solid var(--el-border-color-extra-light); }
.field.missing { background: linear-gradient(90deg, #fff8e8, transparent 40%); }
.field.located { background: #eff6ff; box-shadow: inset 3px 0 0 #2563eb; padding-left: 12px; transition: background 0.3s; }
.forms-page small { font-size: 13px; }
.label { display: flex; flex-direction: column; gap: 3px; align-items: flex-start; }
.label small { color: var(--el-text-color-secondary); font-size: 13px; }
.label em { color: var(--el-color-danger); margin-left: 3px; font-style: normal; }
.input { display: flex; flex-direction: column; gap: 6px; }
.input:has(.follow) { flex-direction: row; flex-wrap: wrap; align-items: center; gap: 12px 16px; }
.input .follow { flex: 1 1 260px; max-width: 360px; }
.input .help { color: var(--el-text-color-secondary); line-height: 1.6; }
:deep(.el-input__count-inner) { font-size: 13px; }
@media (max-width: 1360px) { .forms-page.is-copilot-open .field { grid-template-columns: 1fr; gap: 8px; } }
@media (max-width: 768px) { .field { grid-template-columns: 1fr; gap: 8px; } }
</style>
