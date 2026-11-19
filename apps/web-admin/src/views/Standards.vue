<script setup lang="ts">
import { COUNTRY_NAMES } from '@cyshuhe/shared/src/regulationDirectory'
import { ElMessage } from 'element-plus'
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { api } from '../api'

interface Item {
  id: string
  country_code: string
  number: string
  title: string
  kind: string
  status: string
  published_on: string | null
  effective_on: string | null
  topics: string[]
  detail_url: string | null
  publisher: string
}
interface Summary {
  counted: number
  by_country: Record<string, Record<string, number>>
  by_topic: Record<string, number>
}

const route = useRoute()
const items = ref<Item[]>([])
const summary = ref<Summary>({ counted: 0, by_country: {}, by_topic: {} })
const country = ref(String(route.query.country || route.query.country_code || '').trim())
const status = ref(route.query.status !== undefined ? String(route.query.status).trim() : 'active')
const keyword = ref('')
const loading = ref(false)
// 第 7.5 项二轮 规则 20：分页，每页 50 条；表头固定
const page = ref(1)
const PAGE = 50
const pageItems = computed(() => items.value.slice((page.value - 1) * PAGE, page.value * PAGE))

const topics = computed(() => Object.entries(summary.value.by_topic).sort((a, b) => b[1] - a[1]))
const countries = computed(() => {
  const keys = Object.keys(summary.value.by_country)
  if (country.value && !keys.includes(country.value)) {
    return [...keys, country.value]
  }
  return keys
})
const nameOf = (code: string) =>
  code === 'ZZ'
    ? '国际通用标准'
    : COUNTRY_NAMES[code]
      ? code === 'CN'
        ? COUNTRY_NAMES[code]
        : `${COUNTRY_NAMES[code]}（适用国际标准）`
      : code

async function load() {
  loading.value = true
  try {
    const p = new URLSearchParams({ limit: '500' })
    if (country.value) p.set('country', country.value)
    if (status.value && status.value !== 'active') p.set('status', status.value)
    if (keyword.value.trim()) p.set('q', keyword.value.trim())
    const [list, sum] = await Promise.all([
      api.get<Item[]>(`/api/admin/standards/items?${p.toString()}`),
      api.get<Summary>('/api/admin/standards/summary'),
    ])
    items.value =
      status.value === 'active'
        ? list.filter((r) => ['现行', '即将实施', 'in_force', 'upcoming'].includes(r.status))
        : list
    page.value = 1
    summary.value = sum
  } catch (e) {
    ElMessage.error((e as Error).message || '加载失败')
  } finally {
    loading.value = false
  }
}

watch(
  () => [route.query.country, route.query.country_code, route.query.status],
  ([qCountry, qCountryCode, qStatus]) => {
    const nextCountry = String(qCountry || qCountryCode || '').trim()
    const nextStatus = qStatus !== undefined ? String(qStatus).trim() : 'active'
    if (nextCountry !== country.value || nextStatus !== status.value) {
      country.value = nextCountry
      status.value = nextStatus
      void load()
    }
  },
)

onMounted(load)
</script>

<template>
  <div class="management-page">
    <div class="page-heading">
      <div>
        <h1>标准目录</h1>
        <p>智能驾驶、车联网、车载数据相关标准的目录，按数据类别归类；数量只计现行及即将实施的标准。</p>
      </div>
    </div>

    <div class="summary">
      <div class="card"><div class="n">{{ summary.counted }}</div><div class="t">现行及即将实施的标准</div></div>
      <div v-for="[name, n] in topics" :key="name" class="card small"><div class="n">{{ n }}</div><div class="t">{{ name }}</div></div>
    </div>

    <div class="filters">
      <el-select v-model="country" placeholder="全部国家与国际" clearable style="width: 220px" @change="load">
        <el-option v-for="c in countries" :key="c" :label="nameOf(c)" :value="c" />
      </el-select>
      <el-select v-model="status" placeholder="全部状态" clearable style="width: 170px" @change="load">
        <el-option label="现行与即将实施" value="active" />
        <el-option label="现行" value="in_force" />
        <el-option label="即将实施" value="upcoming" />
        <el-option label="已废止" value="withdrawn" />
        <el-option label="制定中" value="draft" />
      </el-select>
      <el-input v-model="keyword" placeholder="搜索编号或名称" clearable style="width: 240px" @keyup.enter="load" @clear="load" />
      <el-button @click="load">查询</el-button>
    </div>

    <el-table :data="pageItems" v-loading="loading" border max-height="calc(100vh - 260px)" scrollbar-always-on>
      <el-table-column v-if="!country" label="归属" width="108" class-name="cy-col-nowrap"><template #default="{ row }">{{ nameOf(row.country_code) }}</template></el-table-column>
      <el-table-column prop="number" label="标准号" width="176" show-overflow-tooltip class-name="cy-col-nowrap" />
      <el-table-column prop="title" label="名称" min-width="184" class-name="cy-clamp-2"><template #default="{ row }"><span :title="row.title">{{ row.title }}</span><el-link v-if="row.detail_url" :href="row.detail_url" target="_blank" type="primary" class="src-link">官方↗</el-link></template></el-table-column>
      <el-table-column prop="kind" label="性质" width="72" class-name="cy-col-nowrap" />
      <el-table-column prop="status" label="状态" width="80" class-name="cy-col-nowrap" />
      <el-table-column label="实施日期" width="104" class-name="cy-col-nowrap"><template #default="{ row }">{{ row.effective_on || '—' }}</template></el-table-column>
      <el-table-column label="数据类别" min-width="176">
        <template #default="{ row }">
          <el-tag v-for="t in row.topics" :key="t" size="small" class="tag">{{ t }}</el-tag>
          <span v-if="!row.topics.length" class="muted">—</span>
        </template>
      </el-table-column>
      <template #empty>没有符合条件的标准</template>
    </el-table>
    <el-pagination v-if="items.length > PAGE" v-model:current-page="page" :page-size="PAGE" :total="items.length" layout="total, prev, pager, next" />
  </div>
</template>

<style scoped>
.summary { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; margin-bottom: 16px; }
.card { border: 1px solid #e2e8f0; border-radius: 6px; padding: 10px 16px; min-width: 0; }
.card .n { font-size: 24px; font-weight: 600; }
.card .t { color: #64748b; font-size: 13px; }
.card.small { min-width: 0; }
.card.small .n { font-size: 18px; }
.filters { display: flex; gap: 10px; margin-bottom: 14px; }
.muted { color: #64748b; }
.tag { margin: 0 4px 2px 0; }
.src-link { margin-left: 8px; font-size: 13px; }
:deep(.cy-col-full .cell) { overflow: visible; text-overflow: clip; white-space: nowrap; }
</style>
