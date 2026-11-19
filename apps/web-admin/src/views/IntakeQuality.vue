<script setup lang="ts">
// 入库质量：超级管理员要知道两件事——哪些法规已经能给用户用；哪些还不行、要做什么（每件事配一个去做的按钮）
import { ElMessage } from 'element-plus'
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { COUNTRY_NAMES } from '@cyshuhe/shared/src/regulationDirectory'
import { api } from '../api'

interface Todo { text: string; action: 'review' | 'translate'; label: string }
interface Card {
  document_id: string
  country_code: string
  title: string
  passed: boolean
  scope_note?: string
  todo: Todo[]
}

const cards = ref<Card[]>([])
const loading = ref(false)
const route = useRoute()
const country = ref(String(route.query.country || route.query.country_code || '').trim())
watch(
  () => [route.query.country, route.query.country_code],
  ([c1, c2]) => {
    const next = String(c1 || c2 || '').trim()
    if (next) country.value = next
  },
)
const onlyTodo = ref(false)
const working = ref('')
const router = useRouter()
const COUNTRIES = COUNTRY_NAMES

const dangling = ref(0)  // 报告、合规文件里指向已不存在条文的引用
const repairing = ref(false)
const tableReady = ref(false)
let initializedFilter = false
async function load() {
  loading.value = true
  try {
    const [nextCards, nextDangling] = await Promise.all([
      api.get<Card[]>('/api/admin/quality-cards'),
      api.get<{ dangling: number }>('/api/admin/quality-cards/references').then((r) => r.dangling).catch(() => 0),
    ])
    if (!initializedFilter) {
      onlyTodo.value = nextCards.some((c) => !c.passed)
      initializedFilter = true
    }
    cards.value = nextCards
    dangling.value = nextDangling
  } catch (e) {
    ElMessage.error((e as Error).message || '加载失败')
  } finally {
    loading.value = false
    await nextTick()
    tableReady.value = true
  }
}

const shown = computed(() =>
  cards.value.filter((c) => (!country.value || c.country_code === country.value) && (!onlyTodo.value || !c.passed)),
)
const summary = computed(() => {
  const by: Record<string, { ok: number; todo: number }> = {}
  for (const c of cards.value) {
    const s = (by[c.country_code] ||= { ok: 0, todo: 0 })
    if (c.passed) s.ok += 1
    else s.todo += 1
  }
  return Object.entries(by).map(([code, s]) => ({ code, name: COUNTRIES[code] || code, ...s }))
})
function showTodo(code: string) {
  country.value = code
  onlyTodo.value = true
}

async function act(c: Card, t: Todo) {
  if (t.action === 'review') {
    await router.push({
      path: '/documents',
      query: { document_id: c.document_id, country_code: c.country_code },
    })
    return
  }
  working.value = c.document_id
  try {
    const r = await api.post<{ attempted_count: number; translated_count: number; failed_count: number }>(`/admin/knowledge/documents/${c.document_id}/translate`)
    if (r.failed_count > 0 && r.translated_count === 0) {
      ElMessage.warning(`共处理 ${r.attempted_count} 条，仍有 ${r.failed_count} 条暂未生成译文`)
    } else if (r.failed_count > 0) {
      ElMessage.warning(`已补全译文 ${r.translated_count} 条，另有 ${r.failed_count} 条暂未生成译文`)
    } else {
      ElMessage.success(`已补全译文 ${r.translated_count} 条`)
    }
    await load()
  } catch (e) {
    ElMessage.error((e as Error).message)
  } finally {
    working.value = ''
  }
}

async function repairRefs() {
  repairing.value = true
  try {
    const r = await api.post<{ found: number; dangling: number }>('/api/admin/quality-cards/references/repair')
    dangling.value = r.dangling
    ElMessage[r.dangling ? 'warning' : 'success'](r.dangling ? `已修复关联 ${r.found} 处，尚有 ${r.dangling} 处未匹配到现行条文` : `已修复关联 ${r.found} 处`)
  } catch (e) {
    ElMessage.error((e as Error).message)
  } finally {
    repairing.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="management-page">
    <div class="page-heading">
      <div><h1>入库质量</h1></div>
      <div class="page-actions">
        <el-select v-model="country" placeholder="全部国家" clearable style="width: 150px">
          <el-option v-for="s in summary" :key="s.code" :label="s.name" :value="s.code" />
        </el-select>
        <el-switch v-model="onlyTodo" active-text="仅看待完善项" />
      </div>
    </div>

    <div class="summary">
      <div v-for="s in summary" :key="s.code" class="summary-item" data-testid="quality-summary">
        <div class="name">{{ s.name }}</div>
        <div class="line">已就绪 <b>{{ s.ok }}</b> 份</div>
        <div class="line">待完善
          <el-button v-if="s.todo" link type="warning" class="todo-link" @click="showTodo(s.code)">{{ s.todo }} 份</el-button>
          <b v-else>0 份</b>
        </div>
      </div>
    </div>

    <div v-if="dangling" class="todo refs" data-testid="quality-refs">
      <span>报告与合规文件中有 {{ dangling }} 处引用的条文已修订或下线</span>
      <el-button size="small" :loading="repairing" @click="repairRefs">重新关联现行条文</el-button>
    </div>

    <div v-if="!tableReady" v-loading="loading" style="min-height: 240px" />
    <el-table v-else :data="shown" v-loading="loading" border data-testid="quality-table">
      <el-table-column label="国家" width="84" class-name="cy-col-nowrap">
        <template #default="{ row }">{{ COUNTRIES[row.country_code] || row.country_code }}</template>
      </el-table-column>
      <el-table-column label="法规" min-width="260">
        <template #default="{ row }"><span dir="auto" class="q-title">{{ row.title }}</span><small v-if="row.scope_note" class="scope-note" data-testid="scope-note">{{ row.scope_note }}</small></template>
      </el-table-column>
      <el-table-column label="可用状态" width="110" class-name="cy-col-nowrap">
        <template #default="{ row }">
          <el-tag :type="row.passed ? 'success' : 'warning'" size="small">{{ row.passed ? '已就绪' : '待完善' }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="待完善事项" min-width="300">
        <template #default="{ row }">
          <div v-if="row.passed && !row.todo.length" class="sub">原文分条、中文译文与效力核验均已齐备</div>
          <div v-for="(t, i) in row.todo" :key="i" class="todo" data-testid="quality-todo">
            <span>{{ t.text }}</span>
            <el-button size="small" style="flex-shrink: 0" :loading="working === row.document_id && t.action === 'translate'" @click="act(row, t)">{{ t.action === 'translate' ? (working === row.document_id ? '补译中…' : '补全译文') : t.label }}</el-button>
          </div>
        </template>
      </el-table-column>
      <template #empty>{{ onlyTodo ? '当前全部法规均已通过入库质量核验，无待完善项' : '暂无已收录法规' }}</template>
    </el-table>
  </div>
</template>

<style scoped>
.summary { display: flex; gap: 12px; margin-bottom: 16px; flex-wrap: wrap; }
.summary-item { border: 1px solid #e2e8f0; border-radius: 6px; padding: 10px 16px; min-width: 150px; }
.summary-item .name { color: #64748b; font-size: 13px; margin-bottom: 4px; }
.summary-item .line { font-size: 14px; line-height: 1.8; }
.todo-link { font-size: 14px; font-weight: 600; }
.refs { border: 1px solid #fcd34d; background: #fffbeb; border-radius: 6px; padding: 10px 14px; margin-bottom: 12px; }
.todo { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 3px 0; }
.scope-note { display: block; color: var(--el-text-color-secondary); font-size: 13px; line-height: 1.5; white-space: normal; }
</style>
