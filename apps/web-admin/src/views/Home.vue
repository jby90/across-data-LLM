<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { api } from '../api'
import AppIcon from '../components/AppIcon.vue'
import { useAuth } from '../auth'
import Cockpit from './Cockpit.vue'

const auth = useAuth()
const isSuper = computed(() => auth.user?.role === 'platform_admin')

const cards = [
  {
    path: '/documents',
    icon: 'book',
    title: '法规文档',
    description: '查看各国法规的官方原文、条款和版本。',
  },
  {
    path: '/translations',
    icon: 'translate',
    title: '翻译与术语',
    description: '对照原文核对中文译文和法律术语。',
  },
  {
    path: '/knowledge-graph',
    icon: 'graph',
    title: '知识图谱',
    description: '探索法规、义务与数据类型之间的关系，追溯条款证据。',
  },
  {
    path: '/whitepapers',
    icon: 'brief',
    title: '国家合规简报',
    description: '管理各国八章节合规简报，核验条文引证状态并支持更新与导出。',
  },
  {
    path: '/admission',
    icon: 'flow',
    title: '海外准入事项',
    description: '维护各国准入事项资料：受理机构、办理环节、材料和递交清单。',
  },
  {
    path: '/sites',
    icon: 'globe',
    title: '采集站点',
    description: '管理官方来源与更新计划，检查采集运行记录。',
  },
]

interface Todo {
  key: string
  text: string
  to: string
}
const todos = ref<Todo[]>([])
const todosLoaded = ref(false)

onMounted(async () => {
  if (isSuper.value) return
  const out: Todo[] = []
  const [cardsRes, wpRes, sitesRes] = await Promise.allSettled([
    api.get<Array<{ passed: boolean; todo?: Array<{ action: string }> }>>('/api/admin/quality-cards'),
    api.get<Array<{ outdated?: boolean | null; country_name_zh: string }>>('/admin/knowledge/whitepapers'),
    api.get<Array<{ attention: string | null; enabled: boolean }>>('/admin/sites'),
  ])
  if (cardsRes.status === 'fulfilled') {
    const parseIssues = cardsRes.value.filter(
      (c) => !c.passed && ((c.todo || []).some((t) => t.action !== 'translate') || !(c.todo || []).length),
    ).length
    if (parseIssues) {
      out.push({ key: 'quality', text: `入库质量：${parseIssues} 份法规待完成版面复查`, to: '/intake-quality' })
    }
  }
  if (wpRes.status === 'fulfilled') {
    const ws = wpRes.value.filter((w) => w.outdated)
    if (ws.length) {
      out.push({
        key: 'wp',
        text: `国家合规简报：${ws.map((w) => w.country_name_zh).join('、')}有新法规未纳入，可重新生成`,
        to: '/whitepapers',
      })
    }
  }
  if (sitesRes.status === 'fulfilled') {
    const n = sitesRes.value.filter((x) => x.enabled && x.attention).length
    if (n) out.push({ key: 'sites', text: `采集站点：${n} 个站点需要处理`, to: '/sites?attention=1' })
  }
  todos.value = out
  todosLoaded.value = true
})
</script>

<template>
  <Cockpit v-if="isSuper" />
  <div v-else class="ops-home-wrap">
    <!-- 运维管理员同样配备全球法规知识底座与巡检态势大屏（严格隔离企业租户与审计敏感数据） -->
    <Cockpit ops-mode />

    <section v-if="todosLoaded" class="todo-box" data-testid="admin-todos">
      <div class="todo-head">
        <h2>运维待办事项</h2>
        <span class="todo-badge" :class="{ clean: !todos.length }">
          {{ todos.length ? `${todos.length} 项待处理` : '全项正常' }}
        </span>
      </div>
      <p v-if="!todos.length" class="todo-none">当前入库核验、国家合规简报与官方采集站点各项指标正常，暂无待办事项。</p>
      <RouterLink v-for="t in todos" :key="t.key" class="todo-row" :to="t.to">
        <span>{{ t.text }}</span>
        <span class="todo-go">前往处理 →</span>
      </RouterLink>
    </section>

    <div class="section-heading">
      <h2>常用维护工作台</h2>
    </div>
    <div class="feature-grid" :style="{ '--cards': Math.min(cards.length, 3) }">
      <article v-for="card in cards" :key="card.path" class="feature-card">
        <span class="feature-icon"><AppIcon :name="card.icon" /></span>
        <h3>{{ card.title }}</h3>
        <p>{{ card.description }}</p>
        <RouterLink :to="card.path">进入{{ card.title }}<AppIcon name="arrow" /></RouterLink>
      </article>
    </div>
  </div>
</template>

<style scoped>
.ops-home-wrap {
  display: flex;
  flex-direction: column;
  gap: 18px;
}
.ops-home-wrap h2,
.ops-home-wrap h3,
.ops-home-wrap strong {
  font-weight: 600;
}
.todo-box {
  background: #ffffff;
  border: 1px solid var(--cy-line);
  border-radius: 10px;
  padding: 14px 18px;
}
.todo-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 8px;
}
.todo-box h2 {
  font-size: 16px;
  margin: 0;
}
.todo-badge {
  font-size: 13px;
  font-weight: 600;
  padding: 2px 10px;
  border-radius: 999px;
  background: #fef3c7;
  color: #b45309;
}
.todo-badge.clean {
  background: #ecfdf5;
  color: #047857;
}
.todo-none {
  margin: 0;
  color: var(--cy-text-2);
  font-size: 14px;
}
.todo-row {
  display: flex;
  justify-content: space-between;
  gap: 16px;
  padding: 10px 14px;
  margin-top: 8px;
  background: #f8fafc;
  border: 1px solid var(--cy-line);
  border-radius: 8px;
  color: var(--cy-text-1);
  font-size: 14px;
}
.todo-row:hover {
  border-color: var(--el-color-primary-light-5);
}
.todo-go {
  flex: none;
  color: var(--el-color-primary);
  font-weight: 600;
}
.section-heading {
  margin: 0;
}
.section-heading h2 {
  margin: 0;
  font-size: 16px;
}
</style>
