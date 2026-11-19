<script setup lang="ts">
import { nextTick, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../api'
import CopilotFillCard, { type CopilotProposal } from './CopilotFillCard.vue'

// 文件项目旁边的对话：和咨询助手是同一条对话。说业务情况，自动提取成可勾选预览卡片一键填入（支持撤回）；问法规、问怎么填照常回答
const props = defineProps<{
  projectId: string
  disabled?: boolean
  readonly?: boolean
  maxWidth?: number
  applyFn?: (items: CopilotProposal[]) => Promise<void>
  undoFn?: (items: CopilotProposal[]) => Promise<void>
  uploadFn?: (file: File) => Promise<string | void>
}>()
const emit = defineEmits<{ changed: []; open: [boolean] }>()
const router = useRouter()

interface Msg { id: string; role: 'user' | 'assistant'; content: string }
interface CardState { proposals: CopilotProposal[]; appliedKeys: string[]; undone: boolean }

const chatId = ref('')
const messages = ref<Msg[]>([])
const cards = reactive<Record<string, CardState>>({})
const busyMsgId = ref('')
const input = ref('')
const sending = ref(false)
const uploading = ref(false)
const chatFileEl = ref<HTMLInputElement | null>(null)
const QUICK_PROMPTS = [
  '查看待补关键信息',
  '判断当前出境合规路径',
  '核查现有防护措施',
  '核对隐私政策披露要点',
]
const streamEl = ref<HTMLElement | null>(null)
const inputRef = ref<{ focus: () => void } | null>(null)

function cardsStorageKey(pid: string) {
  return `cyshuhe.pchat.cards.${pid}`
}
function loadCards(pid: string) {
  for (const k of Object.keys(cards)) delete cards[k]
  if (!pid) return
  try {
    const raw = sessionStorage.getItem(cardsStorageKey(pid))
    if (raw) {
      const parsed = JSON.parse(raw)
      if (parsed && typeof parsed === 'object') Object.assign(cards, parsed)
    }
  } catch {
    /* 忽略存储异常 */
  }
}
function saveCards() {
  if (!props.projectId) return
  try {
    sessionStorage.setItem(cardsStorageKey(props.projectId), JSON.stringify(cards))
  } catch {
    /* 忽略配额限制 */
  }
}

const TOKEN = /\[([^\]\r\n]{1,20})\]\((\/(?:documents|knowledge|admission|forms)[^)\s]*)\)|\*\*([\s\S]+?)\*\*/g
function segments(rawText: string): { text: string; href?: string; bold?: boolean }[] {
  const text = rawText.replace(/^#{1,4}\s+/gm, '')
  const out: { text: string; href?: string; bold?: boolean }[] = []
  let last = 0
  for (const m of text.matchAll(TOKEN)) {
    if (m.index! > last) out.push({ text: text.slice(last, m.index).replace(/\*\*/g, '') })
    if (m[1] !== undefined && m[2] !== undefined) out.push({ text: m[1], href: m[2] })
    else if (m[3] !== undefined) out.push({ text: m[3], bold: true })
    last = m.index! + m[0].length
  }
  if (last < text.length) out.push({ text: text.slice(last).replace(/\*\*/g, '') })
  return out
}
// 链接指向本项目时不用跳转（已经在这个页面了）
const isHere = (href: string) => href.includes(props.projectId)

const loadedProjectId = ref('')
let deferTimer: number | undefined

async function load() {
  const pid = props.projectId
  if (!pid) return
  if (loadedProjectId.value === pid && (chatId.value || props.readonly || !open.value)) return
  try {
    const r = (props.readonly || !open.value)
      ? await api.get<{ chat_id: string | null }>(`/api/analysis/doc-projects/${pid}/chat`)
      : await api.post<{ chat_id: string }>(`/api/analysis/doc-projects/${pid}/chat`, {})
    if (props.projectId !== pid) return
    if (!r.chat_id) {
      chatId.value = ''
      messages.value = []
      loadedProjectId.value = pid
      return
    }
    chatId.value = r.chat_id
    const d = await api.get<{ messages: Msg[] }>(`/api/analysis/tasks/${r.chat_id}`)
    if (props.projectId !== pid) return
    messages.value = d.messages
    loadedProjectId.value = pid
    await nextTick(); streamEl.value?.scrollTo({ top: streamEl.value.scrollHeight })
  } catch {
    /* 后台预载对话异常不打断项目页渲染 */
  }
}

function scheduleLoad() {
  window.clearTimeout(deferTimer)
  if (open.value) {
    void load()
  } else {
    deferTimer = window.setTimeout(() => { void load() }, 350)
  }
}

const SLOW_MS = 20000
const waitedMs = ref(0)
let waitTimer: number | undefined
const failed = ref<{ text: string; why: string } | null>(null)

async function send(retryText?: string) {
  const text = (retryText ?? input.value).trim()
  if (!text || sending.value || !chatId.value) return
  const historyPayload = messages.value.slice(-6).map((m) => ({ role: m.role, text: m.content }))
  if (retryText === undefined) {
    input.value = ''
    messages.value.push({ id: `tmp-${Date.now()}`, role: 'user', content: text })
  }
  failed.value = null
  sending.value = true
  const started = Date.now()
  waitedMs.value = 0
  waitTimer = window.setInterval(() => { waitedMs.value = Date.now() - started }, 1000)
  await nextTick(); streamEl.value?.scrollTo({ top: streamEl.value.scrollHeight })
  try {
    const canExtract = !props.readonly && !props.disabled && Boolean(props.projectId)
    const [d, ext] = await Promise.all([
      api.post<{ messages: Msg[] }>(
        `/api/analysis/tasks/${chatId.value}/messages${canExtract ? '?copilot=true' : ''}`,
        { content: text },
      ),
      canExtract
        ? api.post<{ proposals: CopilotProposal[] }>(`/api/analysis/doc-projects/${props.projectId}/copilot-extract`, {
            message: text,
            history: historyPayload,
          }).catch(() => ({ proposals: [] as CopilotProposal[] }))
        : Promise.resolve({ proposals: [] as CopilotProposal[] }),
    ])
    messages.value = d.messages
    const lastAssistant = [...d.messages].reverse().find((m) => m.role === 'assistant')
    if (lastAssistant && ext.proposals?.length) {
      cards[lastAssistant.id] = { proposals: ext.proposals, appliedKeys: [], undone: false }
      saveCards()
    }
    emit('changed')
  } catch (e) {
    failed.value = { text, why: (e as Error).message || '服务暂未响应，请稍后重试' }
  } finally {
    window.clearInterval(waitTimer)
    sending.value = false
    await nextTick(); streamEl.value?.scrollTo({ top: streamEl.value.scrollHeight })
  }
}

async function handleApply(msgId: string, selected: CopilotProposal[]) {
  if (!props.applyFn || !selected.length || busyMsgId.value) return
  const c = cards[msgId]
  if (!c) return
  busyMsgId.value = msgId
  try {
    await props.applyFn(selected)
    c.appliedKeys = selected.map((s) => s.key)
    c.undone = false
    saveCards()
  } finally {
    busyMsgId.value = ''
  }
}

async function handleUndo(msgId: string, applied: CopilotProposal[]) {
  if (!props.undoFn || !applied.length || busyMsgId.value) return
  const c = cards[msgId]
  if (!c) return
  busyMsgId.value = msgId
  try {
    await props.undoFn(applied)
    c.appliedKeys = []
    c.undone = true
    saveCards()
  } finally {
    busyMsgId.value = ''
  }
}

onUnmounted(() => { window.clearInterval(waitTimer); window.clearTimeout(deferTimer) })

// 第 7.5 项 规则 19、38：助手默认收成右侧竖条，点开再展开；展开后左边沿可拖宽（320–560px）；收起与宽度记在本机
const OPEN_KEY = 'cyshuhe.pchat.open'
const WIDTH_KEY = 'cyshuhe.pchat.width'
const open = ref(localStorage.getItem(OPEN_KEY) === '1')
const width = ref(Math.min(560, Math.max(320, Number(localStorage.getItem(WIDTH_KEY)) || 380)))
const dragging = ref(false)
watch(open, (v) => {
  emit('open', v)
  localStorage.setItem(OPEN_KEY, v ? '1' : '0')
  if (v) {
    window.clearTimeout(deferTimer)
    void load()
    void nextTick(() => {
      streamEl.value?.scrollTo({ top: streamEl.value.scrollHeight })
      inputRef.value?.focus()
    })
  }
})

async function openPanel() {
  open.value = true
  await nextTick()
  streamEl.value?.scrollTo({ top: streamEl.value.scrollHeight })
  inputRef.value?.focus()
}
defineExpose({ openPanel })

function startDrag(ev: PointerEvent) {
  const x0 = ev.clientX, w0 = width.value
  dragging.value = true
  const move = (e: PointerEvent) => { width.value = Math.min(560, Math.max(320, w0 + (x0 - e.clientX))) }
  const up = () => {
    dragging.value = false
    localStorage.setItem(WIDTH_KEY, String(width.value))
    window.removeEventListener('pointermove', move); window.removeEventListener('pointerup', up)
  }
  window.addEventListener('pointermove', move); window.addEventListener('pointerup', up)
}
function onEnter(ev: KeyboardEvent) { if (ev.shiftKey) return; ev.preventDefault(); void send() }
async function onPickFile(ev: Event) {
  const el = ev.target as HTMLInputElement
  const file = el.files?.[0]
  el.value = ''
  if (!file || !props.uploadFn || uploading.value) return
  uploading.value = true
  messages.value.push({ id: `up-u-${Date.now()}`, role: 'user', content: `上传材料：${file.name}` })
  await nextTick(); streamEl.value?.scrollTo({ top: streamEl.value.scrollHeight })
  try {
    const note = await props.uploadFn(file)
    messages.value.push({
      id: `up-a-${Date.now()}`,
      role: 'assistant',
      content: note || `已按敏感信息隐去规则读取《${file.name}》，正在后台提取企业制度或底稿中的已知要素并对照目标路径法条开展差距分析，识别结果稍后将显示在左侧已识别信息区与导入材料差距分析卡片。`,
    })
  } catch (e) {
    messages.value.push({ id: `up-e-${Date.now()}`, role: 'assistant', content: (e as Error).message || '文件读取失败，请重试' })
  } finally {
    uploading.value = false
    await nextTick(); streamEl.value?.scrollTo({ top: streamEl.value.scrollHeight })
  }
}
onMounted(() => { loadCards(props.projectId); scheduleLoad(); if (open.value) emit('open', true) })
watch(() => props.projectId, (pid) => {
  messages.value = []
  chatId.value = ''
  loadedProjectId.value = ''
  loadCards(pid)
  scheduleLoad()
})
</script>

<template>
  <aside v-if="!open" class="pchat-rail" data-testid="project-chat">
    <button type="button" class="cy-aside-tab" data-testid="pchat-open" @click="open = true">项目助手<i v-if="messages.length" class="pchat-dot" aria-hidden="true" /></button>
  </aside>
  <aside v-else class="pchat" :style="{ width: `${Math.min(width, props.maxWidth ?? 560)}px` }" data-testid="project-chat">
    <span class="cy-resize-handle" :class="{ 'is-dragging': dragging }" role="separator" aria-orientation="vertical" aria-label="拖动调整项目助手宽度" @pointerdown.prevent="startDrag" />
    <header class="pchat-head">
      <b>项目助手</b>
      <span class="pchat-head-actions">
        <a v-if="chatId" class="pchat-full" :href="`/analysis?task=${chatId}`" @click.prevent="router.push({ path: '/analysis', query: { task: chatId } })">在咨询页打开</a>
        <button type="button" class="cy-rail-toggle pchat-close" title="收起项目助手" aria-label="收起项目助手" data-testid="pchat-close" @click="open = false">
          <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><path d="M6 3l5 5-5 5" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" /></svg>
        </button>
      </span>
    </header>
    <div ref="streamEl" class="pchat-stream">
      <div v-for="m in messages" :key="m.id" class="pchat-msg" :class="[m.role, { 'has-card': m.role === 'assistant' && cards[m.id]?.proposals?.length }]" :data-testid="`pchat-${m.role}`">
        <template v-if="m.role === 'assistant'">
          <div class="pchat-content"><template v-for="(seg, i) in segments(m.content)" :key="i"><span v-if="seg.href && isHere(seg.href)" class="pchat-here">{{ seg.text }}</span><a v-else-if="seg.href" class="pchat-link" :href="seg.href" @click.prevent="router.push(seg.href)">{{ seg.text }}</a><strong v-else-if="seg.bold">{{ seg.text }}</strong><template v-else>{{ seg.text }}</template></template></div>
          <CopilotFillCard
            v-if="cards[m.id]?.proposals?.length"
            :proposals="cards[m.id].proposals"
            :applied-keys="cards[m.id].appliedKeys"
            :undone="cards[m.id].undone"
            :busy="busyMsgId === m.id"
            :disabled="disabled || readonly"
            @apply="(sel) => handleApply(m.id, sel)"
            @undo="(app) => handleUndo(m.id, app)"
          />
        </template>
        <template v-else>{{ m.content }}</template>
      </div>
      <div v-if="sending" class="pchat-msg assistant pchat-wait" role="status" aria-live="polite" data-testid="pchat-waiting">
        {{ waitedMs > SLOW_MS ? '回答时间较长，仍在处理' : '正在回答并提取填写项…' }}（已等待 {{ Math.floor(waitedMs / 1000) }} 秒）</div>
      <div v-if="failed && !sending" class="pchat-msg assistant pchat-wait" data-testid="pchat-failed">
        {{ failed.why }}。<el-button link type="primary" size="small" data-testid="pchat-retry" @click="send(failed.text)">重试</el-button></div>
    </div>
    <p v-if="readonly" class="pchat-readonly">只读账号：可以查看对话，不能提问。</p>
    <div v-else class="pchat-input">
      <div class="pchat-chips" data-testid="pchat-quick-chips">
        <button v-for="qp in QUICK_PROMPTS" :key="qp" type="button" class="pchat-chip" :disabled="disabled || sending || uploading" @click="send(qp)">{{ qp }}</button>
      </div>
      <el-input ref="inputRef" v-model="input" type="textarea" :rows="3" resize="none" maxlength="2000" :disabled="disabled || sending || uploading" placeholder="描述业务情况或安全措施，或咨询法规与填写口径" data-testid="pchat-input" @keydown.enter="onEnter" />
      <div class="pchat-bar">
        <input v-if="uploadFn" ref="chatFileEl" type="file" accept=".pdf,.docx,.txt,.md" hidden @change="onPickFile" />
        <el-button v-if="uploadFn" size="small" plain :loading="uploading" :disabled="disabled || sending" data-testid="pchat-upload" @click="chatFileEl?.click()">上传材料</el-button>
        <span class="pchat-grow" />
        <el-button type="primary" size="small" :loading="sending" :disabled="disabled || uploading || !input.trim()" data-testid="pchat-send" @click="send()">发送</el-button>
      </div>
    </div>
  </aside>
</template>

<style scoped>
.pchat-rail { flex: none; display: flex; flex-direction: column; align-items: center; justify-content: flex-start; width: 56px; padding: 12px 0; border-left: 1px solid var(--cy-line); background: var(--cy-surface-sunken); }
.pchat-rail .cy-aside-tab { position: sticky; top: 12px; }
.pchat-dot { position: absolute; top: 6px; right: 6px; width: 7px; height: 7px; border-radius: 50%; background: var(--el-color-primary); }
.pchat { position: sticky; top: 0; flex: none; border-left: 1px solid var(--cy-line); display: flex; flex-direction: column; background: #fbfcfe; height: calc(100vh - 100px); max-height: calc(100vh - 100px); }
.pchat .cy-resize-handle { left: -6px; }
.pchat-head { display: flex; justify-content: space-between; align-items: center; gap: 8px; padding: 12px 14px; border-bottom: 1px solid var(--cy-line); }
.pchat-head-actions { display: inline-flex; align-items: center; gap: 10px; }
.pchat-close { width: 28px; height: 28px; }
.pchat-full { font-size: 13px; color: var(--cy-text-2); }
.pchat-full:hover { color: #2563eb; }
.pchat-stream { flex: 1; overflow-y: auto; padding: 12px 14px; display: flex; flex-direction: column; gap: 10px; }
.pchat-msg { font-size: 14px; line-height: 1.75; padding: 8px 11px; border-radius: 10px; max-width: 92%; white-space: pre-wrap; word-break: break-word; }
.pchat-msg.has-card { width: 95%; max-width: 95%; }
.pchat-content { white-space: pre-wrap; }
.pchat-msg.assistant { background: #fff; border: 1px solid #e2e8f0; color: #1e293b; align-self: flex-start; }
.pchat-msg.user { background: #2563eb; color: #fff; align-self: flex-end; }
.pchat-wait { color: var(--cy-text-3); }
.pchat-link { color: #1d4ed8; text-decoration: underline; }
.pchat-here { font-weight: 600; }
.pchat-input { padding: 10px 12px; border-top: 1px solid #eef2f7; display: flex; flex-direction: column; gap: 8px; }
.pchat-chips { display: flex; flex-wrap: wrap; gap: 6px; }
.pchat-chip { border: 1px solid #dbeafe; background: #eff6ff; color: #1d4ed8; border-radius: 999px; padding: 2px 10px; font-size: 13px; line-height: 1.5; cursor: pointer; transition: background .15s; }
.pchat-chip:hover:not(:disabled) { background: #dbeafe; }
.pchat-chip:disabled { opacity: 0.55; cursor: not-allowed; }
.pchat-bar { display: flex; align-items: center; gap: 8px; width: 100%; }
.pchat-grow { flex: 1; }
@media (max-width: 900px) { .pchat { width: auto !important; border-left: 0; border-top: 1px solid var(--cy-line); max-height: none; } .pchat-rail { width: auto; border-left: 0; border-top: 1px solid var(--cy-line); } .pchat-rail .cy-aside-tab { writing-mode: horizontal-tb; width: auto; padding: 8px 16px; } }
.pchat-readonly { margin: 0; padding: 12px; color: #64748b; font-size: 13px; border-top: 1px solid #e2e8f0; }
</style>
