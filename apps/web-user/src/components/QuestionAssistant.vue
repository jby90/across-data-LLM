<script setup lang="ts">
import { nextTick, onUnmounted, ref, watch } from 'vue'
import { api } from '../api'

// 填写页上的助手：只围绕这一项。解释这一项；用户用自己的话说了情况，给出可以放进这一项的文字，由用户点采用（支持撤回）
const props = defineProps<{ projectId: string; questionKey: string; currentValue?: string; disabled?: boolean }>()
const emit = defineEmits<{ adopt: [value: string]; undo: [previousValue: string] }>()

interface Turn {
  role: 'user' | 'assistant'
  text: string
  proposal?: string
  adopted?: boolean
  previousValue?: string
}
const open = ref(false)
const turns = ref<Turn[]>([])
const input = ref('')
const asking = ref(false)
const inputEl = ref<{ focus: () => void } | null>(null)

let previous: string | null = null  // 刚才那一项：用户询问与上一项的区别时一并带上
watch(() => props.questionKey, (_now, before) => { previous = before ?? null; turns.value = []; input.value = '' })

async function toggle() {
  open.value = !open.value
  if (open.value) { await nextTick(); inputEl.value?.focus() }
}
const SLOW_MS = 20000
const waitedMs = ref(0)
let waitTimer: number | undefined
const failed = ref<{ text: string; why: string } | null>(null)
async function ask(retryText?: string) {
  const text = (retryText ?? input.value).trim()
  if (!text || asking.value) return
  if (retryText === undefined) {
    turns.value.push({ role: 'user', text })
    input.value = ''
  }
  failed.value = null
  asking.value = true
  const started = Date.now()
  waitedMs.value = 0
  waitTimer = window.setInterval(() => { waitedMs.value = Date.now() - started }, 1000)
  try {
    const r = await api.post<{ reply: string; proposal: string }>(
      `/api/analysis/doc-projects/${props.projectId}/questions/${encodeURIComponent(props.questionKey)}/ask`, { message: text, previous })
    turns.value.push({ role: 'assistant', text: r.reply, proposal: r.proposal || undefined, adopted: false })
  } catch (e) {
    failed.value = { text, why: (e as Error).message || '服务暂未响应，请稍后重试' }
  } finally { window.clearInterval(waitTimer); asking.value = false }
}
onUnmounted(() => window.clearInterval(waitTimer))
function adopt(t: Turn) {
  if (!t.proposal) return
  t.previousValue = props.currentValue ?? ''
  t.adopted = true
  emit('adopt', t.proposal)
}
function undoAdopt(t: Turn) {
  t.adopted = false
  emit('undo', t.previousValue ?? '')
}
</script>

<template>
  <div class="qa">
    <button type="button" class="qa-toggle" :disabled="disabled" data-testid="qa-toggle" @click="toggle">{{ open ? '收起' : '填写咨询' }}</button>
    <div v-if="open" class="qa-box" data-testid="qa-box">
      <p v-if="!turns.length" class="qa-hint">可以咨询这一项的法规口径与填法，也可以直接说明实际情况由项目助手代拟填入文字。</p>
      <div v-for="(t, i) in turns" :key="i" class="qa-turn" :class="t.role">
        <span>{{ t.text }}</span>
        <div v-if="t.proposal && !t.adopted" class="qa-proposal" data-testid="qa-proposal">
          <span class="qa-proposal-text">{{ t.proposal }}</span>
          <el-button size="small" type="primary" plain data-testid="qa-adopt" @click="adopt(t)">采用</el-button>
        </div>
        <div v-else-if="t.proposal && t.adopted" class="qa-proposal adopted" data-testid="qa-adopted">
          <span class="qa-proposal-text ok">已填入这一项：{{ t.proposal }}</span>
          <el-button link type="primary" size="small" data-testid="qa-undo" @click="undoAdopt(t)">撤回</el-button>
        </div>
      </div>
      <p v-if="asking" class="qa-turn assistant qa-wait" role="status" aria-live="polite" data-testid="qa-waiting">
        {{ waitedMs > SLOW_MS ? '回答时间较长，仍在处理' : '正在回答…' }}（已等待 {{ Math.floor(waitedMs / 1000) }} 秒）</p>
      <p v-if="failed && !asking" class="qa-turn assistant" data-testid="qa-failed">
        {{ failed.why }}。<el-button link type="primary" size="small" data-testid="qa-retry" @click="ask(failed.text)">重试</el-button></p>
      <div class="qa-input">
        <el-input ref="inputEl" v-model="input" size="small" maxlength="1000" :disabled="disabled || asking" placeholder="输入疑问或描述实际情况（如：本项适用口径是什么 / 当前不涉及该场景）" data-testid="qa-input" @keydown.enter.prevent="ask()" />
        <el-button size="small" :loading="asking" :disabled="disabled || !input.trim()" data-testid="qa-send" @click="ask()">发送</el-button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.qa { margin-top: 6px; }
.qa-toggle { border: 0; background: transparent; color: #2563eb; font-size: 13px; cursor: pointer; padding: 2px 0; }
.qa-toggle:disabled { color: #64748b; cursor: not-allowed; }
.qa-box { margin-top: 6px; border: 1px solid #dbeafe; background: #f8fbff; border-radius: 8px; padding: 10px 12px; }
.qa-hint { margin: 0 0 8px; color: #64748b; font-size: 13px; }
.qa-turn { font-size: 13px; line-height: 1.7; margin-bottom: 6px; color: #1e293b; }
.qa-turn.user { color: #475569; }
.qa-turn.user::before { content: '我：'; color: #64748b; }
.qa-turn.assistant::before { content: '助手：'; color: #2563eb; }
.qa-proposal { display: flex; gap: 8px; align-items: center; margin-top: 4px; background: #fff; border: 1px dashed #93c5fd; border-radius: 6px; padding: 6px 8px; }
.qa-proposal.adopted { border-style: solid; border-color: #a7f3d0; background: #f3faf7; }
.qa-proposal-text { flex: 1; color: #0f172a; }
.qa-proposal-text.ok { color: #047857; }
.qa-input { display: flex; gap: 6px; margin-top: 6px; }
</style>
