<script setup lang="ts">
// 题目旁的依据卡片：填写说明、所依据的法条（点了在法规知识库打开那一条）、一个示例答案
// 示例只供参考：不写进答案，也不进文件
import { useRoute } from 'vue-router'
export interface BasisCard { law: string; article: string; point: string; link: string }
defineProps<{ hint: string; usedFor: string; basis: BasisCard[]; example: string }>()
const route = useRoute()
function withFrom(link: string) {
  if (!link) return ''
  const sep = link.includes('?') ? '&' : '?'
  return `${link}${sep}from=${encodeURIComponent(route.fullPath)}`
}
</script>

<template>
  <aside class="basis-card" data-testid="q-basis-card">
    <section>
      <h5>填写说明</h5>
      <p v-if="hint">{{ hint }}</p>
      <p class="muted">用于：{{ usedFor }}</p>
    </section>
    <section>
      <h5>所依据的法条</h5>
      <template v-if="basis.length">
        <p v-for="b in basis" :key="b.link" class="law">
          <a :href="withFrom(b.link)" target="_blank" rel="opener" data-testid="q-basis-link">{{ b.law }}{{ b.article }}</a>
          <span>{{ b.point }}</span>
        </p>
      </template>
      <p v-else class="muted" data-testid="q-basis-none">暂无对应条文</p>
    </section>
    <section v-if="example">
      <h5>示例</h5>
      <p class="example" data-testid="q-basis-example">{{ example }}</p>
    </section>
  </aside>
</template>

<style scoped>
.basis-card { border: 1px solid #e2e8f0; background: #f8fafc; border-radius: 8px; padding: 10px 12px; font-size: 13px; line-height: 1.65; color: #334155; }
.basis-card section + section { margin-top: 8px; padding-top: 8px; border-top: 1px dashed #e2e8f0; }
.basis-card h5 { margin: 0 0 2px; font-size: 13px; color: #64748b; font-weight: 600; }
.basis-card p { margin: 0; }
.basis-card .muted { color: #64748b; }
.basis-card .law { display: flex; flex-direction: column; margin-bottom: 4px; }
.basis-card .law a { color: #2563eb; text-decoration: none; font-weight: 600; }
.basis-card .law a:hover { text-decoration: underline; }
.basis-card .example { white-space: pre-line; color: #475569; }
</style>
