<script setup lang="ts">
import { serverFilename, localTime } from '@cyshuhe/shared'
import { useAuth } from '../auth'
import { ElMessage, ElMessageBox } from 'element-plus'
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { api } from '../api'

interface Row {
  country_code: string; country_name_zh: string; available: boolean; version_no: number | null; updated_at: string | null
  updating: boolean; tokens?: number; dropped?: number; documents?: number; clauses?: number; outdated?: boolean | null; chars?: number; new_documents?: number
}
const rows = ref<Row[]>([])
const loading = ref(false)
const error = ref('')
let timer: number | undefined

const when = (s: string | null) => localTime(s) || '—'

async function load() {
  loading.value = rows.value.length === 0
  error.value = ''
  try {
    rows.value = await api.get<Row[]>('/admin/knowledge/whitepapers')
  } catch (e) { error.value = (e as Error).message } finally { loading.value = false }
}
async function update(r: Row) {
  try {
    await ElMessageBox.confirm(
      `重新生成${r.country_name_zh}合规简报约需数分钟；生成期间企业用户看到的仍是当前已发布内容，生成完成后自动切换为新版并提醒在该国有业务项目的企业。`,
      '重新生成简报', { confirmButtonText: '开始生成', cancelButtonText: '取消', type: 'warning' },
    )
  } catch { return }
  try {
    await api.post(`/admin/knowledge/whitepapers/${r.country_code}/update`, {})
    ElMessage.success('已开始重新生成，完成后状态将自动刷新')
    await load()
  } catch (e) { ElMessage.error((e as Error).message) }
}
async function download(r: Row, kind: 'pdf' | 'docx') {
  try {
    const res = await fetch(`/api/admin/knowledge/whitepapers/${r.country_code}/export.${kind}`, { headers: { Authorization: `Bearer ${api.token()}` } })
    if (!res.ok) throw new Error('下载失败，可重试')
    const url = URL.createObjectURL(await res.blob())
    const a = document.createElement('a'); a.href = url; a.download = serverFilename(res, `${r.country_name_zh}汽车数据合规洞察简报.${kind}`)
    document.body.appendChild(a); a.click(); a.remove(); window.setTimeout(() => URL.revokeObjectURL(url), 30000)
  } catch (e) { ElMessage.error((e as Error).message) }
}
const highlightedCountry = ref('')
const isMobile = ref(typeof window !== 'undefined' ? window.innerWidth <= 768 : false)
function syncViewport() {
  if (typeof window !== 'undefined') isMobile.value = window.innerWidth <= 768
}

function focusCountryRow(code: string) {
  if (!code) return
  highlightedCountry.value = code
  void nextTick(() => {
    const el = document.querySelector(`[data-wp-country="${code}"]`)
    const rowEl = el?.closest('tr') || el
    rowEl?.scrollIntoView({ behavior: 'smooth', block: 'center' })
  })
}

function applyRouteCountryQuery() {
  const qCountry = typeof route.query.country === 'string' ? route.query.country.trim().toUpperCase() : ''
  if (!qCountry) {
    highlightedCountry.value = ''
    return
  }
  focusCountryRow(qCountry)
  if (route.query.sync === '1') {
    const found = rows.value.find((r) => r.country_code === qCountry && r.available)
    const rawDims = typeof route.query.dims === 'string' ? route.query.dims.split(',').map((x) => x.trim().toUpperCase()) : []
    if (found) openSyncDialog(found, rawDims)
  }
}

onMounted(async () => {
  syncViewport()
  window.addEventListener('resize', syncViewport)
  await load()
  applyRouteCountryQuery()
  timer = window.setInterval(() => { if (rows.value.some((r) => r.updating)) load() }, 8000)
})
onUnmounted(() => {
  window.removeEventListener('resize', syncViewport)
  if (timer) window.clearInterval(timer)
})
const auth = useAuth()
const route = useRoute()
watch(
  () => [route.query.country, route.query.sync, route.query.dims],
  () => applyRouteCountryQuery(),
)
const isAdmin = computed(() => auth.user?.role === 'platform_admin')
const canSyncDims = computed(() => ['platform_admin', 'ops_admin'].includes(auth.user?.role || ''))

const DIMENSIONS_META = [
  { code: 'D1', label: '一、属地数据法规体系概述' },
  { code: 'D2', label: '二、汽车核心数据定义与分类' },
  { code: 'D3', label: '三、数据跨境传输合规路径与前置条件' },
  { code: 'D4', label: '四、用户授权、告知、同意的合规要求细则' },
  { code: 'D5', label: '五、数据存储、留存、销毁时效与规范' },
  { code: 'D6', label: '六、违规行为分级、处罚标准、监管追责机制' },
  { code: 'D7', label: '七、属地市场准入认证、申报材料、流程节点要求' },
  { code: 'D8', label: '八、企业合规整改建议与落地执行方案' },
]
const VALID_DIM_CODES = new Set(DIMENSIONS_META.map((d) => d.code))
const syncDialogRow = ref<Row | null>(null)
const selectedDims = ref<string[]>([])
const syncingDims = ref(false)
function openSyncDialog(r: Row, presetDims: string[] = []) {
  syncDialogRow.value = r
  const validPreset = presetDims.filter((c) => VALID_DIM_CODES.has(c))
  selectedDims.value = !isAdmin.value && validPreset.length >= DIMENSIONS_META.length
    ? validPreset.slice(0, DIMENSIONS_META.length - 1)
    : validPreset
}
function toggleDim(code: string) {
  const idx = selectedDims.value.indexOf(code)
  if (idx >= 0) {
    selectedDims.value.splice(idx, 1)
    return
  }
  if (!isAdmin.value && selectedDims.value.length >= DIMENSIONS_META.length - 1) {
    ElMessage.warning('全篇重新生成须由超级管理员操作，按章节更新最多勾选 7 个受影响章节')
    return
  }
  selectedDims.value.push(code)
}
async function submitSyncDims() {
  if (!syncDialogRow.value || !selectedDims.value.length) return
  syncingDims.value = true
  try {
    await api.post(`/admin/knowledge/whitepapers/${syncDialogRow.value.country_code}/sync-dimensions`, {
      dimensions: selectedDims.value,
    })
    const labels = DIMENSIONS_META.filter((d) => selectedDims.value.includes(d.code)).map((d) => d.label)
    ElMessage.success(`已启动 ${syncDialogRow.value.country_name_zh}简报受影响章节更新（${labels.join('；')}），并自动同步刷新篇首摘要`)
    syncDialogRow.value = null
    await load()
  } catch (e) {
    ElMessage.error((e as Error).message)
  } finally {
    syncingDims.value = false
  }
}

// 核对时去掉的句子：每一句写了什么、为什么去掉、它引的是哪几条
interface Dropped { dimension: string; title: string; text: string; why: string; cites: { document: string; clause: string; text: string }[] }
const droppedOf = ref<{ row: Row; items: Dropped[] } | null>(null)
async function showDropped(r: Row) {
  try {
    const res = await api.get<{ items: Dropped[] }>(`/admin/knowledge/whitepapers/${r.country_code}/dropped`)
    droppedOf.value = { row: r, items: res.items }
  } catch (e) { ElMessage.error((e as Error).message) }
}
</script>

<template>
  <div class="management-page" v-loading="loading">
    <header class="page-heading">
      <div><h1>国家合规简报</h1><p>每国一份涵盖 8 个章节的合规简报，企业用户在法规知识库选定国家后阅读和下载。编写时逐句核对，数字或依据在所引条文里找不到的句子不写进简报。</p>
        </div>
      <el-button @click="load">刷新</el-button>
    </header>
    <el-alert v-if="error" type="error" :closable="false" :title="`加载失败：${error}`" />
    <el-table
      :data="rows"
      border
      data-testid="whitepaper-table"
      :row-class-name="({ row }: { row: Row }) => (row.country_code === highlightedCountry ? 'wp-row-highlight' : '')"
    >
      <el-table-column label="国家" width="84" class-name="cy-col-nowrap"><template #default="{ row }"><span :data-wp-country="row.country_code">{{ row.country_name_zh }}</span></template></el-table-column>
      <el-table-column label="状态" min-width="144">
        <template #default="{ row }">
          <el-tag v-if="row.updating" type="warning" size="small">更新中…</el-tag>
          <el-tag v-else-if="!row.available" type="info" size="small">未生成</el-tag>
          <el-tag v-else-if="row.outdated" type="warning" size="small">{{ row.new_documents ? `有 ${row.new_documents} 份新入库法规未纳入，可更新简报` : '法规库有变化，可更新简报' }}</el-tag>
          <el-tag v-else-if="row.outdated === null" type="info" size="small">正在核对有无新入库法规</el-tag>
          <el-tag v-else type="success" size="small">已按最新法规生成（{{ when(row.updated_at) }}）</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="正文篇幅" width="96" class-name="cy-col-nowrap"><template #default="{ row }">{{ row.chars ? `${row.chars.toLocaleString()} 字` : '—' }}</template></el-table-column>
      <el-table-column label="核心引证依据" min-width="194" class-name="cy-col-nowrap"><template #default="{ row }"><span class="wp-cite-basis">{{ row.documents ? `引证 ${row.documents} 部法规 · ${row.clauses} 条` : '—' }}</span></template></el-table-column>
      <el-table-column label="引文逐句核验" min-width="154" class-name="cy-col-nowrap"><template #default="{ row }"><el-button v-if="row.available && row.dropped" link type="primary" :data-testid="`wp-dropped-${row.country_code}`" @click="showDropped(row)">已拦截剔除 {{ row.dropped }} 句</el-button><span v-else class="cy-status cy-status--done">{{ row.available ? '全部引证已核验' : '—' }}</span></template></el-table-column>
      <el-table-column label="操作" width="312" :fixed="isMobile ? false : 'right'">
        <template #default="{ row }">
          <div class="wp-ops">
            <el-button v-if="canSyncDims && row.available" size="small" type="primary" plain :disabled="row.updating" :data-testid="`wp-sync-${row.country_code}`" @click="openSyncDialog(row)">按章节更新</el-button>
            <el-button v-if="isAdmin" size="small" :disabled="row.updating" :data-testid="`wp-update-${row.country_code}`" @click="update(row)">{{ row.available ? '全篇更新' : '生成' }}</el-button>
            <el-button v-if="row.available" size="small" link type="primary" @click="download(row, 'pdf')">PDF</el-button>
            <el-button v-if="row.available" size="small" link type="primary" @click="download(row, 'docx')">Word</el-button>
          </div>
        </template>
      </el-table-column>
    </el-table>

    <el-dialog :model-value="!!syncDialogRow" :title="syncDialogRow ? `按受影响章节更新 · ${syncDialogRow.country_name_zh}合规简报` : ''" width="min(620px, 94vw)" @close="syncDialogRow = null">
      <div v-if="syncDialogRow" class="sync-body">
        <p class="drop-lead">法规发生局部修订并完成审核后，请勾选本次受影响的简报章节。系统将重新检索对应条文并编写所选章节，同步刷新篇首摘要，其余未变动章节原样保留。</p>
        <div class="dim-tags" data-testid="wp-sync-dims">
          <el-check-tag v-for="d in DIMENSIONS_META" :key="d.code" :checked="selectedDims.includes(d.code)" :data-testid="`wp-dim-${d.code}`" @change="toggleDim(d.code)">{{ d.label }}</el-check-tag>
        </div>
      </div>
      <template #footer>
        <el-button @click="syncDialogRow = null">取消</el-button>
        <el-button type="primary" :disabled="!selectedDims.length" :loading="syncingDims" data-testid="wp-sync-submit" @click="submitSyncDims">确认更新所选章节（{{ selectedDims.length }} 章）</el-button>
      </template>
    </el-dialog>

    <el-drawer :model-value="!!droppedOf" size="min(640px, 94vw)" :title="droppedOf ? `${droppedOf.row.country_name_zh}简报：未通过原文核验的语句（${droppedOf.items.length} 句）` : ''" @close="droppedOf = null">
      <template v-if="droppedOf">
        <p class="drop-lead">以上未通过原文核验的语句未写入正式简报。下方列出每句原文、剔除原因及对应引用条文。</p>
        <article v-for="(d, i) in droppedOf.items" :key="i" class="drop-item" data-testid="wp-dropped-item">
          <h4>{{ d.title }}</h4>
          <p class="drop-text">{{ d.text || '（该版本仅记录剔除原因，重新生成后可查看对应语句原文）' }}</p>
          <p class="drop-why">剔除原因：{{ d.why }}</p>
          <details v-if="d.cites.length">
            <summary>所引条文（{{ d.cites.length }} 条）</summary>
            <p v-for="(c, j) in d.cites" :key="j" class="drop-cite"><strong>{{ c.document }} {{ c.clause }}</strong>：{{ c.text }}</p>
          </details>
        </article>
      </template>
    </el-drawer>
  </div>
</template>

<style scoped>
.drop-lead { color: var(--el-text-color-regular); line-height: 1.7; margin: 0 0 12px; }
.drop-item { border: 1px solid var(--el-border-color-light); border-radius: 10px; padding: 12px 16px; margin-bottom: 12px; }
.drop-item h4 { margin: 0 0 6px; font-size: 14px; }
.drop-text { line-height: 1.7; margin: 6px 0; }
.drop-why { color: var(--el-color-warning-dark-2, #b88230); margin: 6px 0; }
.drop-cite { font-size: 13px; line-height: 1.6; color: var(--el-text-color-regular); }
.dim-tags { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 8px; }
.wp-cite-basis { font-weight: 500; color: #20323f; white-space: nowrap; }
.wp-ops { display: flex; align-items: center; gap: 6px; white-space: nowrap; }
.wp-ops :deep(.el-button.is-link) { padding: 4px 6px; }
:deep(.el-table__row.wp-row-highlight > td.el-table__cell) {
  background-color: #ecf5ff !important;
  transition: background-color 0.3s ease;
}
</style>
