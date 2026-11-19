<script setup lang="ts">
import { nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { api } from '../api'
import CopilotFillCard, { type CopilotProposal } from './CopilotFillCard.vue'

interface Turn {
  id: string
  role: 'user' | 'assistant'
  text: string
  proposals?: CopilotProposal[]
  appliedKeys?: string[]
  undone?: boolean
}

const props = defineProps<{
  storageKey: string
  endpoint: string
  title: string
  tabLabel?: string
  welcome: string
  placeholder?: string
  disabled?: boolean
  readonly?: boolean
  applyFn: (items: CopilotProposal[]) => Promise<void>
  undoFn: (items: CopilotProposal[]) => Promise<void>
}>()

const emit = defineEmits<{ layout: [open: boolean, width: number] }>()

const OPEN_KEY = 'cyshuhe.copilot.open'
const WIDTH_KEY = 'cyshuhe.copilot.width'

const open = ref(false)
const width = ref(Math.min(520, Math.max(340, Number(localStorage.getItem(WIDTH_KEY)) || 390)))
const isMobile = ref(typeof window !== 'undefined' ? window.innerWidth <= 768 : false)
const dragging = ref(false)
const turns = ref<Turn[]>([])
const input = ref('')
const sending = ref(false)
const busyTurnId = ref('')
const streamEl = ref<HTMLElement | null>(null)
const inputRef = ref<{ focus: () => void } | null>(null)

const SLOW_MS = 20000
const waitedMs = ref(0)
let waitTimer: number | undefined
const failed = ref<{ text: string; why: string } | null>(null)

function cacheKey(k: string) {
  return `cyshuhe.copilot.turns.${k}`
}

function loadTurns() {
  if (!props.storageKey) {
    turns.value = []
    return
  }
  try {
    const raw = sessionStorage.getItem(cacheKey(props.storageKey))
    if (raw) {
      const parsed = JSON.parse(raw)
      if (Array.isArray(parsed) && parsed.length) {
        turns.value = parsed
        return
      }
    }
  } catch {
    /* sessionStorage 不可用时降级为内存态 */
  }
  turns.value = [{ id: 'welcome', role: 'assistant', text: props.welcome }]
}

function saveTurns() {
  if (!props.storageKey) return
  try {
    sessionStorage.setItem(cacheKey(props.storageKey), JSON.stringify(turns.value.slice(-30)))
  } catch {
    /* 忽略配额限制 */
  }
}

watch(
  () => [props.storageKey, props.welcome],
  () => {
    failed.value = null
    loadTurns()
  },
  { immediate: true },
)

watch(open, async (v) => {
  localStorage.setItem(OPEN_KEY, v ? '1' : '0')
  emit('layout', v, width.value)
  if (v) {
    await nextTick()
    streamEl.value?.scrollTo({ top: streamEl.value.scrollHeight })
    inputRef.value?.focus()
  }
})

watch(width, (w) => {
  if (open.value) emit('layout', true, w)
})

async function openPanel() {
  open.value = true
  await nextTick()
  streamEl.value?.scrollTo({ top: streamEl.value.scrollHeight })
  inputRef.value?.focus()
}

defineExpose({ openPanel })

function startDrag(ev: PointerEvent) {
  const x0 = ev.clientX
  const w0 = width.value
  dragging.value = true
  const move = (e: PointerEvent) => {
    width.value = Math.min(520, Math.max(340, w0 + (x0 - e.clientX)))
  }
  const up = () => {
    dragging.value = false
    localStorage.setItem(WIDTH_KEY, String(width.value))
    window.removeEventListener('pointermove', move)
    window.removeEventListener('pointerup', up)
  }
  window.addEventListener('pointermove', move)
  window.addEventListener('pointerup', up)
}

async function send(retryText?: string) {
  const text = (retryText ?? input.value).trim()
  if (!text || sending.value || !props.endpoint) return
  const historyPayload = turns.value
    .filter((t) => t.id !== 'welcome')
    .slice(-6)
    .map((t) => ({ role: t.role, text: t.text }))
  if (retryText === undefined) {
    input.value = ''
    turns.value.push({ id: `u-${Date.now()}`, role: 'user', text })
    saveTurns()
  }
  failed.value = null
  sending.value = true
  const started = Date.now()
  waitedMs.value = 0
  waitTimer = window.setInterval(() => {
    waitedMs.value = Date.now() - started
  }, 1000)
  await nextTick()
  streamEl.value?.scrollTo({ top: streamEl.value.scrollHeight })
  try {
    const res = await api.post<{ reply: string; proposals: CopilotProposal[] }>(props.endpoint, {
      message: text,
      history: historyPayload,
    })
    turns.value.push({
      id: `a-${Date.now()}`,
      role: 'assistant',
      text: res.reply,
      proposals: res.proposals?.length ? res.proposals : undefined,
      appliedKeys: [],
      undone: false,
    })
    saveTurns()
  } catch (e) {
    failed.value = { text, why: (e as Error).message || '服务暂未响应，请稍后重试' }
  } finally {
    window.clearInterval(waitTimer)
    sending.value = false
    await nextTick()
    streamEl.value?.scrollTo({ top: streamEl.value.scrollHeight })
  }
}

async function handleApply(turn: Turn, selected: CopilotProposal[]) {
  if (!selected.length || busyTurnId.value) return
  busyTurnId.value = turn.id
  try {
    await props.applyFn(selected)
    turn.appliedKeys = selected.map((s) => s.key)
    turn.undone = false
    saveTurns()
  } finally {
    busyTurnId.value = ''
  }
}

async function handleUndo(turn: Turn, applied: CopilotProposal[]) {
  if (!applied.length || busyTurnId.value) return
  busyTurnId.value = turn.id
  try {
    await props.undoFn(applied)
    turn.appliedKeys = []
    turn.undone = true
    saveTurns()
  } finally {
    busyTurnId.value = ''
  }
}

function clearChat() {
  turns.value = [{ id: 'welcome', role: 'assistant', text: props.welcome }]
  saveTurns()
}

function onEnter(ev: KeyboardEvent) {
  if (ev.shiftKey) return
  ev.preventDefault()
  void send()
}

function syncMobileState() {
  isMobile.value = window.innerWidth <= 768
}

function onGlobalKeydown(ev: KeyboardEvent) {
  if (ev.key === 'Escape' && open.value && isMobile.value) {
    open.value = false
  }
}

onMounted(() => {
  syncMobileState()
  window.addEventListener('resize', syncMobileState)
  window.addEventListener('keydown', onGlobalKeydown)
  if (localStorage.getItem(OPEN_KEY) === '1' && window.innerWidth >= 1280) open.value = true
  emit('layout', open.value, width.value)
})
onUnmounted(() => {
  window.clearInterval(waitTimer)
  window.removeEventListener('resize', syncMobileState)
  window.removeEventListener('keydown', onGlobalKeydown)
})
</script>

<template>
  <aside v-if="!open" class="cd-rail" data-testid="copilot-drawer">
    <button type="button" class="cy-aside-tab" data-testid="copilot-open" @click="openPanel">
      {{ tabLabel || title }}
      <i v-if="turns.length > 1" class="cd-dot" aria-hidden="true" />
    </button>
  </aside>

  <div
    v-else
    class="cd-overlay-wrap"
    :class="{ 'el-overlay is-mobile-modal': isMobile }"
    @click.self="isMobile && (open = false)"
  >
    <aside
      class="cd-panel"
      :class="{ 'el-drawer is-mobile': isMobile }"
      :role="isMobile ? 'dialog' : 'complementary'"
      :aria-modal="isMobile ? 'true' : undefined"
      :aria-label="title"
      :style="{ width: `${width}px` }"
      data-testid="copilot-drawer"
    >
      <span
        v-if="!isMobile"
        class="cy-resize-handle"
        :class="{ 'is-dragging': dragging }"
        role="separator"
        aria-orientation="vertical"
        aria-label="拖动调整助手宽度"
        @pointerdown.prevent="startDrag"
      />
      <header class="cd-head">
        <b>{{ title }}</b>
        <span class="cd-head-actions">
          <button v-if="turns.length > 1" type="button" class="cd-clear" @click="clearChat">清空对话</button>
          <button
            type="button"
            class="cy-rail-toggle cd-close"
            :class="{ 'el-drawer__close-btn is-mobile-close': isMobile }"
            title="收起助手"
            aria-label="收起助手"
            data-testid="copilot-close"
            @click="open = false"
          >
            <span v-if="isMobile" class="cd-close-label">收起助手</span>
            <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true">
              <path
                v-if="isMobile"
                d="M4 4l8 8M12 4l-8 8"
                fill="none"
                stroke="currentColor"
                stroke-width="1.6"
                stroke-linecap="round"
              />
              <path
                v-else
                d="M6 3l5 5-5 5"
                fill="none"
                stroke="currentColor"
                stroke-width="1.6"
                stroke-linecap="round"
                stroke-linejoin="round"
              />
            </svg>
          </button>
        </span>
      </header>

      <div ref="streamEl" class="cd-stream">
        <div v-for="t in turns" :key="t.id" class="cd-msg" :class="t.role" :data-testid="`copilot-${t.role}`">
          <div class="cd-text">{{ t.text }}</div>
          <CopilotFillCard
            v-if="t.role === 'assistant' && t.proposals?.length"
            :proposals="t.proposals"
            :applied-keys="t.appliedKeys"
            :undone="t.undone"
            :busy="busyTurnId === t.id"
            :disabled="disabled || readonly"
            @apply="(sel) => handleApply(t, sel)"
            @undo="(app) => handleUndo(t, app)"
          />
        </div>
        <div v-if="sending" class="cd-msg assistant cd-wait" role="status" aria-live="polite" data-testid="copilot-waiting">
          {{ waitedMs > SLOW_MS ? '回答时间较长，仍在处理' : '正在整理…' }}（已等待 {{ Math.floor(waitedMs / 1000) }} 秒）
        </div>
        <div v-if="failed && !sending" class="cd-msg assistant cd-wait" data-testid="copilot-failed">
          {{ failed.why }}。<el-button link type="primary" size="small" data-testid="copilot-retry" @click="send(failed.text)">重试</el-button>
        </div>
      </div>

      <p v-if="readonly" class="cd-readonly">只读账号：可查看说明，不能修改填报内容。</p>
      <div v-else class="cd-input">
        <el-input
          ref="inputRef"
          v-model="input"
          type="textarea"
          :rows="3"
          resize="none"
          maxlength="2000"
          :disabled="disabled || sending"
          :placeholder="placeholder || '输入企业、业务或安全措施说明自动提取填报项，或咨询填写口径'"
          data-testid="copilot-input"
          @keydown.enter="onEnter"
        />
        <el-button
          type="primary"
          :loading="sending"
          :disabled="disabled || !input.trim()"
          data-testid="copilot-send"
          @click="send()"
        >发送</el-button>
      </div>
    </aside>
  </div>
</template>

<style scoped>
.cd-rail {
  position: fixed;
  top: auto;
  bottom: 36px;
  right: 0;
  z-index: 24;
  padding: 6px 4px;
  background: var(--cy-surface-sunken, #f8fafc);
  border: 1px solid var(--cy-line);
  border-right: 0;
  border-radius: 10px 0 0 10px;
  box-shadow: -4px 4px 14px rgba(15, 23, 42, 0.06);
}
@media (max-width: 800px) {
  .cd-rail {
    position: static;
    display: inline-flex;
    margin-top: 14px;
    padding: 4px 6px;
    border-right: 1px solid var(--cy-line);
    border-radius: 8px;
    box-shadow: none;
  }
  .cd-rail :deep(.cy-aside-tab) {
    writing-mode: horizontal-tb !important;
    flex-direction: row !important;
    padding: 6px 12px !important;
    min-height: 0 !important;
    height: auto !important;
  }
}
.cd-dot {
  position: absolute;
  top: 6px;
  right: 6px;
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--el-color-primary);
}
.cd-overlay-wrap {
  display: contents;
}
.cd-overlay-wrap.is-mobile-modal {
  display: block;
  position: fixed;
  inset: 0;
  z-index: 2000;
  background: rgba(15, 23, 42, 0.45);
  backdrop-filter: blur(1px);
}
.cd-panel {
  position: fixed;
  top: 64px;
  right: 0;
  bottom: 0;
  z-index: 26;
  max-width: 100vw;
  box-sizing: border-box;
  display: flex;
  flex-direction: column;
  background: #fbfcfe;
  border-left: 1px solid var(--cy-line);
  box-shadow: -12px 0 28px rgba(15, 23, 42, 0.14);
}
.cd-panel .cy-resize-handle {
  left: -6px;
}
.cd-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 8px;
  padding: 12px 14px;
  border-bottom: 1px solid var(--cy-line);
  background: #fff;
}
.cd-head-actions {
  display: inline-flex;
  align-items: center;
  gap: 10px;
}
.cd-clear {
  border: 0;
  background: transparent;
  color: var(--cy-text-2);
  font-size: 13px;
  cursor: pointer;
  padding: 0;
}
.cd-clear:hover {
  color: var(--el-color-primary);
}
.cd-close {
  width: 28px;
  height: 28px;
}
.cd-close.is-mobile-close {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  width: auto;
  height: 30px;
  padding: 0 10px;
  font-size: 13px;
  font-weight: 600;
  color: #1e293b;
  background: #f8fafc;
  border: 1px solid #cbd5e1;
  border-radius: 6px;
  white-space: nowrap;
}
.cd-close-label {
  line-height: 1;
}
.cd-stream {
  flex: 1;
  overflow-y: auto;
  padding: 12px 14px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.cd-msg {
  font-size: 14px;
  line-height: 1.7;
  padding: 8px 11px;
  border-radius: 10px;
  max-width: 95%;
  word-break: break-word;
}
.cd-msg.assistant {
  background: #fff;
  border: 1px solid #e2e8f0;
  color: #1e293b;
  align-self: flex-start;
  width: 95%;
}
.cd-msg.user {
  background: #2563eb;
  color: #fff;
  align-self: flex-end;
  white-space: pre-wrap;
}
.cd-text {
  white-space: pre-wrap;
}
.cd-wait {
  color: var(--cy-text-3);
  width: auto !important;
}
.cd-input {
  padding: 10px 12px;
  border-top: 1px solid #eef2f7;
  background: #fff;
  display: flex;
  flex-direction: column;
  gap: 8px;
  align-items: flex-end;
}
.cd-readonly {
  margin: 0;
  padding: 12px;
  color: #64748b;
  font-size: 13px;
  border-top: 1px solid #e2e8f0;
}
@media (max-width: 768px) {
  .cd-panel,
  .cd-panel.el-drawer.is-mobile {
    position: fixed;
    top: 0;
    right: 0;
    bottom: 0;
    left: auto;
    height: 100%;
    width: min(90vw, 390px) !important;
    max-width: 100vw !important;
    z-index: 2001;
    box-sizing: border-box;
    background: #fbfcfe;
  }
}
</style>
