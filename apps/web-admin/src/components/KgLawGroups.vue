<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { UNASSIGNED_GROUP, type LawGroup } from '@cyshuhe/shared/src/graphGrouping'

const props = defineProps<{
  groups: LawGroup[]
  selectedGroupId: string
  total: number
  filtered: number
  shown: number
  shownGroups: number
  page: number
  pages: number
  overviewShown: number
  overviewGroups: number
}>()
const emit = defineEmits<{
  (e: 'select', id: string): void
  (e: 'page', page: number): void
}>()
const view = ref<'overview' | 'directory'>('overview')
const search = ref('')
const directoryPage = ref(1)
const matched = computed(() => props.groups.filter(g => g.name.toLowerCase().includes(search.value.trim().toLowerCase())))
const directoryPages = computed(() => Math.max(1, Math.ceil(matched.value.length / 12)))
const directory = computed(() => matched.value.slice((directoryPage.value - 1) * 12, directoryPage.value * 12))
const lawOnlyCount = computed(() => props.groups.filter(g => g.id !== UNASSIGNED_GROUP).length)
const hasSystemGroup = computed(() => props.groups.some(g => g.id === UNASSIGNED_GROUP))
const selected = computed(() => props.groups.find(g => g.id === props.selectedGroupId))
watch([search, () => props.groups], () => { directoryPage.value = 1 })
</script>

<template>
  <section class="law-browser" aria-label="按法规浏览图谱">
    <div class="law-counts" data-testid="kg-display-counts" aria-live="polite">
      <span>全部关系 <strong>{{ total }}</strong></span>
      <span>筛选后 <strong>{{ filtered }}</strong></span>
      <span>当前展示 <strong>{{ !selected && view === 'overview' ? overviewShown : shown }}</strong> 条 · {{ !selected && view === 'overview' ? overviewGroups : shownGroups }} 组连线</span>
      <span class="law-muted-inline">相同要素间的多处条文依据合并展示为一条连线</span>
    </div>
    <div v-if="!selected" class="law-view-switch" role="group" aria-label="图谱展示方式">
      <el-button :class="{ 'is-active-view': view === 'overview' }" :aria-pressed="view === 'overview'" @click="view = 'overview'">全局总览</el-button>
      <el-button :class="{ 'is-active-view': view === 'directory' }" :aria-pressed="view === 'directory'" @click="view = 'directory'">法规分组</el-button>
      <el-select v-if="view === 'overview'" :model-value="''" placeholder="选择法规并展开…" filterable aria-label="从总览展开法规" style="width: 360px" @change="(id: string) => id && emit('select', id)">
        <el-option v-for="group in groups" :key="group.id" :value="group.id" :label="`${group.name} · ${group.edges.length} 条关系`" />
      </el-select>
    </div>
    <template v-if="!selected && view === 'overview'">
      <slot name="overview" />
    </template>
    <template v-else-if="!selected">
      <div class="law-toolbar">
        <div><strong>法规目录</strong><span class="law-muted">{{ lawOnlyCount }} 部法规<template v-if="hasSystemGroup"> · 另含 1 组国家与监管顶层关联</template> · 点击展开关系</span></div>
        <el-input v-model="search" clearable aria-label="搜索图谱法规" placeholder="搜索法规名称" style="width: 260px" />
      </div>
      <div class="law-grid" data-testid="kg-law-directory">
        <button v-for="group in directory" :key="group.id" type="button" class="law-card" @click="emit('select', group.id)">
          <span class="law-name">{{ group.name }}</span>
          <span v-if="groups.some(other => other.id !== group.id && other.name === group.name)" class="law-hint">同名文件</span>
          <span class="law-card-footer">{{ group.edges.length }} 条关系 <span>展开 →</span></span>
        </button>
      </div>
      <p v-if="!directory.length" class="law-hint">没有匹配的法规，请调整搜索或筛选条件。</p>
      <div v-if="directoryPages > 1" class="law-pagination">
        <el-button :disabled="directoryPage === 1" @click="directoryPage--">上一页法规</el-button>
        <span>第 {{ directoryPage }} / {{ directoryPages }} 页 · {{ matched.length }} 组</span>
        <el-button :disabled="directoryPage === directoryPages" @click="directoryPage++">下一页法规</el-button>
      </div>
    </template>
    <template v-else>
      <div class="law-toolbar">
        <div class="law-selection"><el-button @click="emit('select', '')">← {{ view === 'overview' ? '返回全局总览' : '返回法规目录' }}</el-button><h3>{{ selected.name }}</h3></div>
        <span class="law-muted">本法规 {{ selected.edges.length }} 条关系</span>
      </div>
      <slot />
      <div class="law-pagination" data-testid="kg-relation-pagination">
        <el-button :disabled="page <= 1" @click="emit('page', page - 1)">上一页关系</el-button>
        <span>第 {{ page }} / {{ pages }} 页 · 每页最多 36 组连线</span>
        <el-button :disabled="page >= pages" @click="emit('page', page + 1)">下一页关系</el-button>
      </div>
    </template>
  </section>
</template>

<style scoped>
.law-browser { padding: 16px 20px; }
.law-view-switch { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; margin: 16px 0; }
.law-view-switch :deep(.el-button + .el-button) { margin-left: 0; }
.law-view-switch :deep(.el-button.is-active-view) {
  color: var(--el-color-primary);
  border-color: var(--el-color-primary-light-5);
  background: var(--el-color-primary-light-9);
  font-weight: 600;
}
.law-counts { display: flex; flex-wrap: wrap; align-items: baseline; gap: 8px 24px; color: #526174; font-size: 14px; }
.law-counts strong { color: #172c43; font-size: 18px; margin-left: 6px; }
.law-muted-inline { color: #64748b; font-size: 13px; }
.law-hint, .law-muted { color: #64748b; font-size: 13px; line-height: 1.7; }
.law-hint { margin: 12px 0 16px; }
.law-toolbar { display: flex; flex-wrap: wrap; justify-content: space-between; align-items: center; gap: 16px; margin: 16px 0; }
.law-muted { margin-left: 12px; }
.law-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(min(100%, 250px), 1fr)); gap: 16px; }
.law-card { font: inherit; cursor: pointer; color: #244766; background: white; border: 1px solid #d8e1eb; border-radius: 8px; display: flex; flex-direction: column; gap: 20px; justify-content: space-between; text-align: left; padding: 20px; transition: border-color .18s, background .18s; }
.law-card:focus-visible { outline: 2px solid #2563eb; outline-offset: 3px; }
.law-card:hover { border-color: #3b82f6; background: #f5f9ff; }
.law-name { font-size: 15px; font-weight: 600; line-height: 1.6; overflow-wrap: anywhere; }
.law-card-footer { display: flex; justify-content: space-between; color: #64748b; font-size: 13px; }
.law-card-footer span { color: #2563eb; }
.law-selection { display: flex; align-items: center; flex-wrap: wrap; gap: 16px; min-width: 0; }
h3 { margin: 0; font-size: 16px; line-height: 1.6; overflow-wrap: anywhere; flex: 1; min-width: 180px; }
.law-pagination { display: flex; justify-content: center; align-items: center; gap: 16px; margin-top: 20px; flex-wrap: wrap; font-size: 13px; color: #64748b; }
@media (max-width: 640px) { .law-browser { padding: 14px; } .law-counts { gap: 12px; } .law-counts strong { font-size: 18px; } }
@media (prefers-reduced-motion: reduce) { .law-card { transition: none; } }
</style>
