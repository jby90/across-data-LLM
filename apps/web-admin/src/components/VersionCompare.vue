<script setup lang="ts">
import { ElMessage } from 'element-plus'
import { computed, onMounted, ref, watch } from 'vue'
import { api } from '../api'

interface Version { version_no: number; kind: string; fetched_at: string | null; published_date: string | null; clauses: number }
interface Side { clause_id: string; number: string | null; heading: string | null; text: string; translated: string | null }
interface Row { status: 'added' | 'removed' | 'changed' | 'unchanged'; base: Side | null; target: Side | null; diff: Array<{ op: string; text: string }> }
interface Result {
  base_version: number; target_version: number; same_source: boolean; total_rows: number; offset: number
  counts: Record<string, number>; rows: Row[]
}

const props = defineProps<{ documentId: string }>()
const versions = ref<Version[]>([])
const base = ref<number | null>(null)
const target = ref<number | null>(null)
const onlyChanges = ref(true)
const result = ref<Result | null>(null)
const loading = ref(false)

const STATUS: Record<string, { label: string; type: 'success' | 'danger' | 'warning' | 'info' }> = {
  added: { label: '新增', type: 'success' },
  removed: { label: '删除', type: 'danger' },
  changed: { label: '改动', type: 'warning' },
  unchanged: { label: '未变', type: 'info' },
}
const label = (v: Version) => {
  const kindZh = v.kind === 'HTML' ? '网页原文' : v.kind === 'PDF' ? 'PDF 原件' : v.kind || '归档原文'
  const datePart = v.published_date || (v.fetched_at ? v.fetched_at.slice(0, 10) : '')
  return `第 ${v.version_no} 版 · ${kindZh}（${v.clauses} 条${datePart ? ` · ${datePart}` : ''}）`
}
const heading = (s: Side | null) => (s ? [s.number, s.heading].filter(Boolean).join(' · ') || '无编号条款' : '')
const summary = computed(() => result.value?.counts ?? {})

async function loadVersions() {
  try {
    const res = await api.get<{ versions: Version[] }>(`/admin/knowledge/documents/${props.documentId}/versions`)
    versions.value = res.versions
    if (versions.value.length >= 2) {
      base.value = versions.value[versions.value.length - 2].version_no
      target.value = versions.value[versions.value.length - 1].version_no
      await run()
    }
  } catch (e) { ElMessage.error((e as Error).message) }
}
async function run(offset = 0) {
  if (!base.value || !target.value || base.value === target.value) { result.value = null; return }
  loading.value = true
  try {
    const q = `base=${base.value}&target=${target.value}&only_changes=${onlyChanges.value}&offset=${offset}`
    const res = await api.get<Result>(`/admin/knowledge/documents/${props.documentId}/compare?${q}`)
    result.value = offset && result.value ? { ...res, rows: [...result.value.rows, ...res.rows] } : res
  } catch (e) { ElMessage.error((e as Error).message) } finally { loading.value = false }
}
watch([base, target, onlyChanges], () => run())
onMounted(loadVersions)
</script>

<template>
  <section class="compare" v-loading="loading" data-testid="version-compare">
    <p v-if="versions.length < 2" class="note">当前法规为现行唯一收录版本，上传修订版后可逐条对比差异。</p>
    <template v-else>
      <div class="pickers">
        <label class="pick">较早版本
          <el-select v-model="base" style="width:340px"><el-option v-for="v in versions" :key="v.version_no" :label="label(v)" :value="v.version_no" /></el-select></label>
        <label class="pick">较新版本
          <el-select v-model="target" style="width:340px"><el-option v-for="v in versions" :key="v.version_no" :label="label(v)" :value="v.version_no" /></el-select></label>
        <el-checkbox v-model="onlyChanges">只看有变化的条款</el-checkbox>
      </div>
      <p v-if="base === target" class="note">请选择两个不同的版本。</p>
      <template v-if="result">
        <div class="chips" data-testid="compare-summary">
          <el-tag v-for="(s, key) in STATUS" :key="key" :type="s.type" effect="plain">{{ s.label }} {{ summary[key] ?? 0 }} 条</el-tag>
        </div>
        <p v-if="result.same_source" class="note">以上两版源自同一官方原件，差异系重新识别带来的文字校勘修正，原法规条文未发生修订。</p>
        <el-empty v-if="!result.rows.length" description="这两个版本没有差异" />
        <article v-for="(r, i) in result.rows" :key="i" class="row" :class="r.status" data-testid="compare-row">
          <header><el-tag size="small" :type="STATUS[r.status].type">{{ STATUS[r.status].label }}</el-tag><strong>{{ heading(r.target || r.base) }}</strong></header>
          <p v-if="r.status === 'changed'" class="diff"><template v-for="(d, k) in r.diff" :key="k"><del v-if="d.op === 'delete'">{{ d.text }} </del><ins v-else-if="d.op === 'insert'">{{ d.text }} </ins><span v-else>{{ d.text }} </span></template></p>
          <p v-else class="plain">{{ (r.target || r.base)!.text }}</p>
          <p v-if="(r.target || r.base)?.translated" class="zh">{{ (r.target || r.base)!.translated }}</p>
        </article>
        <el-button v-if="result.rows.length < result.total_rows" @click="run(result.rows.length)">加载更多（还有 {{ result.total_rows - result.rows.length }} 条）</el-button>
      </template>
    </template>
  </section>
</template>

<style scoped>
.compare { padding: 6px 0 24px; }
.pickers { display: flex; gap: 20px; flex-wrap: wrap; align-items: end; margin-bottom: 14px; }
.pickers .pick { display: flex; flex-direction: column; gap: 6px; font-size: 13px; color: var(--el-text-color-secondary); }
.chips { display: flex; gap: 10px; margin-bottom: 12px; flex-wrap: wrap; }
.note { color: var(--el-text-color-secondary); font-size: 13px; line-height: 1.7; margin: 8px 0; }
.row { border: 1px solid var(--el-border-color-light); border-left-width: 4px; border-radius: 10px; padding: 12px 16px; margin-bottom: 10px; background: white; }
.row.added { border-left-color: var(--el-color-success); } .row.removed { border-left-color: var(--el-color-danger); }
.row.changed { border-left-color: var(--el-color-warning); } .row.unchanged { border-left-color: var(--el-border-color); }
.row header { display: flex; gap: 10px; align-items: center; }
.row p { margin: 8px 0 0; line-height: 1.8; white-space: pre-line; }
del { background: #fde8e8; color: #b42318; text-decoration: line-through; }
ins { background: #e3f6e8; color: #17613a; text-decoration: none; }
.zh { color: var(--el-text-color-secondary); font-size: 13px; }
.row.removed .plain { color: var(--el-text-color-secondary); text-decoration: line-through; }
</style>
