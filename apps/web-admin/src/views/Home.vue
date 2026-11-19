<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { api } from '../api'
import { useRouter } from 'vue-router'
import AppIcon from '../components/AppIcon.vue'
import { useAuth } from '../auth'
const router = useRouter()
const auth = useAuth()
const cards = [
  {
    path: '/documents',
    icon: 'book',
    title: '法规文档',
    description: '查看各国法规的官方原文、条款和版本。',
    admin: false,
  },
  {
    path: '/translations',
    icon: 'translate',
    title: '翻译与术语',
    description: '对照原文核对中文译文和法律术语。',
    admin: false,
  },
  {
    path: '/knowledge-graph',
    icon: 'graph',
    title: '知识图谱',
    description: '探索法规、义务与数据类型之间的关系，追溯条款证据。',
    admin: true,
  },
  {
    path: '/whitepapers',
    icon: 'brief',
    title: '国家合规简报',
    description: '管理各国八章节合规简报，核验条文引证状态并支持更新与导出。',
    admin: true,
  },
  {
    path: '/admission',
    icon: 'flow',
    title: '海外准入事项',
    description: '维护各国准入事项资料：受理机构、办理环节、材料和递交清单。',
    admin: true,
  },
  {
    path: '/agent-studio',
    icon: 'nodes',
    title: '评估执行记录',
    description: '查看每次合规评估各环节的执行记录、法条核验情况与退回补充依据过程。',
    admin: true,
    super: true,
  },
  {
    path: '/sites',
    icon: 'globe',
    title: '采集站点',
    description: '管理官方来源与更新计划，检查采集运行记录。',
    admin: true,
  },
  {
    path: '/orgs',
    icon: 'building',
    title: '企业与账号',
    description: '管理签约企业与四类角色账号，配置模型调用敏感词保护清单。',
    admin: true,
    super: true,
  },
]
const isSuper = computed(() => auth.user?.role === 'platform_admin')
// 第 7.5 项二轮 规则 21：第一屏“待办事项”——只取各页已有的计数，点了直达那一页
interface Todo { key: string; text: string; to: string }
const todos = ref<Todo[]>([])
const todosLoaded = ref(false)
onMounted(async () => {
  const out: Todo[] = []
  const [cardsRes, wpRes, sitesRes] = await Promise.allSettled([
    api.get<Array<{ passed: boolean }>>('/api/admin/quality-cards'),
    api.get<Array<{ outdated?: boolean | null; country_name_zh: string }>>('/admin/knowledge/whitepapers'),
    api.get<Array<{ attention: string | null; enabled: boolean }>>('/admin/sites'),
  ])
  if (cardsRes.status === 'fulfilled') { const n = cardsRes.value.filter((c) => !c.passed).length; if (n) out.push({ key: 'quality', text: `入库质量：${n} 份法规待完成入库核验`, to: '/intake-quality' }) }
  if (wpRes.status === 'fulfilled') { const ws = wpRes.value.filter((w) => w.outdated); if (ws.length) out.push({ key: 'wp', text: `国家合规简报：${ws.map((w) => w.country_name_zh).join('、')}有新法规未纳入，可重新生成`, to: '/whitepapers' }) }
  if (sitesRes.status === 'fulfilled') { const n = sitesRes.value.filter((x) => x.enabled && x.attention).length; if (n) out.push({ key: 'sites', text: `采集站点：${n} 个站点需要处理`, to: '/sites?attention=1' }) }
  todos.value = out
  todosLoaded.value = true
})
// 运维管理员：维护基础数据，不看企业与账号、评估流程诊断
const visibleCards = computed(() =>
  cards.filter((card) => !('super' in card) || isSuper.value),
)

</script>
<template>
  <div>
    <section class="welcome-hero welcome-hero--row">
      <div>
        <h1>欢迎，{{ isSuper ? '超级管理员' : '运维管理员' }}</h1>
        <p>{{ isSuper ? '维护各国法规与中文译文、核对图谱与简报、管理签约企业与角色账号。' : '维护各国法规与中文译文、采集站点、图谱与简报、准入事项资料。' }}</p>
      </div>
      <el-button v-if="isSuper" data-testid="home-open-cockpit" @click="router.push('/cockpit')">打开合规态势看板</el-button>
    </section>
    <section v-if="todosLoaded" class="todo-box" data-testid="admin-todos">
      <h2>待办事项</h2>
      <p v-if="!todos.length" class="todo-none">当前各项指标正常，暂无待办事项。</p>
      <RouterLink v-for="t in todos" :key="t.key" class="todo-row" :to="t.to"><span>{{ t.text }}</span><span class="todo-go">前往处理 →</span></RouterLink>
    </section>
    <div class="section-heading">
      <h2>常用功能</h2>
    </div>
    <!-- 第 7.5 项 规则 29：几张卡片就排几列（最多 4 列），不留单张孤行 -->
    <div class="feature-grid" :style="{ '--cards': Math.min(visibleCards.length, 4) }">
      <article v-for="card in visibleCards" :key="card.path" class="feature-card">
        <span class="feature-icon"><AppIcon :name="card.icon" /></span>
        <h3>{{ card.title }}</h3>
        <p>{{ card.description }}</p>
        <RouterLink :to="card.path">进入{{ card.title }}<AppIcon name="arrow" /></RouterLink>
      </article>
    </div>
  </div>
</template>

<style scoped>
.welcome-hero--row { display: flex; align-items: center; justify-content: space-between; gap: 16px; flex-wrap: wrap; }
.pending-note { color: #dc2626; font-weight: 600; margin-top: -4px; }
.todo-box { margin: -8px 0 28px; }
.todo-box h2 { font-size: 18px; margin: 0 0 12px; }
.todo-none { margin: 0; color: var(--cy-text-2); font-size: 14px; }
.todo-row { display: flex; justify-content: space-between; gap: 16px; padding: 12px 16px; margin-bottom: 8px; background: #fff; border: 1px solid var(--cy-line); border-radius: 8px; color: var(--cy-text-1); font-size: 14px; }
.todo-row:hover { border-color: var(--el-color-primary-light-5); }
.todo-go { flex: none; color: var(--el-color-primary); }
</style>
