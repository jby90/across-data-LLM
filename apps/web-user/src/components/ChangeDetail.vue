<script setup lang="ts">
// 一次法规变化写给企业看的内容：改了哪些条文（修改前 / 修改后）、对企业的影响、要做的事。
// 订阅通知和引用条文有更新的通知共用。
export interface Side { text: string; zh: string; clause_id: string }
export interface Advice {
  pairs: Array<{ kind: string; label: string; before: Side; after: Side }>
  impact: string
  actions: string[]
  state: 'pending' | 'ready' | 'failed'
}
defineProps<{ advice: Advice | null }>()
const shown = (s: Side) => s.zh || s.text
// 修改前后相同的开头、结尾照常显示，中间改了的那段标出来
function marked(a: string, b: string, side: 'before' | 'after') {
  let head = 0
  while (head < Math.min(a.length, b.length) && a[head] === b[head]) head++
  let tail = 0
  while (tail < Math.min(a.length, b.length) - head && a[a.length - 1 - tail] === b[b.length - 1 - tail]) tail++
  const t = side === 'before' ? a : b
  return { pre: t.slice(0, head), mid: t.slice(head, t.length - tail), post: t.slice(t.length - tail) }
}
const piece = (p: Advice['pairs'][number], side: 'before' | 'after') =>
  marked(p.before.zh && p.after.zh ? p.before.zh : p.before.text, p.before.zh && p.after.zh ? p.after.zh : p.after.text, side)
</script>
<template>
  <div v-if="advice" class="change-detail" data-testid="change-detail">
    <section v-if="advice.pairs.length" class="pairs">
      <h4>条文修订对照</h4>
      <div v-for="(p, i) in advice.pairs" :key="i" class="pair" data-testid="change-pair">
        <p class="pair-label">{{ p.kind }}<template v-if="p.label"> · {{ p.label }}</template></p>
        <div class="pair-cols">
          <div class="col before"><small>修订前</small>
            <p v-if="shown(p.before) && shown(p.after)">{{ piece(p, 'before').pre }}<mark class="del">{{ piece(p, 'before').mid }}</mark>{{ piece(p, 'before').post }}</p>
            <p v-else>{{ shown(p.before) || '（原无此条）' }}</p>
            <details v-if="p.before.zh && p.before.text"><summary>原文</summary><p dir="auto">{{ p.before.text }}</p></details></div>
          <div class="col after"><small>修订后</small>
            <p v-if="shown(p.before) && shown(p.after)">{{ piece(p, 'after').pre }}<mark class="add">{{ piece(p, 'after').mid }}</mark>{{ piece(p, 'after').post }}</p>
            <p v-else>{{ shown(p.after) || '（本条已删去）' }}</p>
            <details v-if="p.after.zh && p.after.text"><summary>原文</summary><p dir="auto">{{ p.after.text }}</p></details></div>
        </div>
      </div>
    </section>
    <template v-if="advice.state === 'ready'">
      <section class="impact"><h4>对企业的影响</h4><p data-testid="change-impact">{{ advice.impact }}</p></section>
      <section v-if="advice.actions.length" class="actions"><h4>应对建议</h4>
        <ol data-testid="change-actions"><li v-for="(a, i) in advice.actions" :key="i">{{ a }}</li></ol></section>
    </template>
    <p v-else-if="advice.state === 'pending'" class="wait" data-testid="change-pending">修订影响分析与应对建议正在生成，请稍后刷新查看。</p>
    <p v-else class="wait">修订影响分析暂未生成，稍后重新打开本页将自动重试。</p>
  </div>
</template>
<style scoped>
.change-detail { display: flex; flex-direction: column; gap: 12px; margin: 10px 0; }
h4 { margin: 0 0 6px; font-size: 14px; color: var(--el-text-color-primary); }
.pair { margin-bottom: 10px; }
.pair-label { margin: 0 0 4px !important; font-size: 13px; color: var(--el-text-color-secondary) !important; }
.pair-cols { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
.col { padding: 10px 12px; border-radius: 8px; font-size: 13px; line-height: 1.7; }
.col p { margin: 2px 0 !important; white-space: pre-line; color: var(--el-text-color-regular); }
.col small { color: var(--el-text-color-secondary); font-size: 13px; }
.before { background: #f8fafc; border: 1px solid #e2e8f0; }
.after { background: #f0fdf4; border: 1px solid #bbf7d0; }
mark { padding: 0 2px; border-radius: 3px; }
mark.del { background: #fee2e2; color: #991b1b; text-decoration: line-through; }
mark.add { background: #bbf7d0; color: #14532d; font-weight: 600; }
details summary { cursor: pointer; font-size: 13px; color: var(--el-text-color-secondary); }
.impact p { margin: 0 !important; line-height: 1.8; }
.actions ol { margin: 0; padding-left: 20px; line-height: 1.8; font-size: 14px; color: var(--el-text-color-regular); }
.wait { margin: 0 !important; font-size: 13px; color: var(--el-text-color-secondary) !important; }
@media (max-width: 800px) { .pair-cols { grid-template-columns: 1fr; } }
</style>
