<script setup lang="ts">
import { serverFilename, localTime } from '@cyshuhe/shared'
import { useAuth } from '../auth'
import { ElMessage, ElMessageBox } from 'element-plus'
import { computed, nextTick, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { api } from '../api'

interface Row {
  country_code: string; country_name_zh: string; available: boolean; version_no: number | null; updated_at: string | null
  updating: boolean; tokens?: number; dropped?: number; documents?: number; clauses?: number; outdated?: boolean | null; chars?: number; new_documents?: number
  review_status?: 'approved' | 'auto_verified' | null; reviewed_at?: string | null; manual_edited?: boolean; edit_note?: string | null
}
interface WpBlock {
  type: 'title' | 'heading' | 'subheading' | 'paragraph' | 'list' | 'table'
  text?: string
  items?: string[]
  head?: string[]
  rows?: string[][]
  widths?: number[]
}
interface WpVersionItem {
  version_no: number
  created_at: string | null
  chars: number
  review_status: 'approved' | 'auto_verified'
  reviewed_at?: string | null
  manual_edited: boolean
  note: string
  is_latest: boolean
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
  const locked = r.review_status === 'approved'
  try {
    await ElMessageBox.confirm(
      locked
        ? `《${r.country_name_zh}合规简报》已完成人工审核归档锁定。为防止全篇重做覆盖人工审定内容并产生质量漂移，建议优先使用「按章节更新」仅针对新规关联章节做定向更新。\n\n确认仍要解除保护并全篇重新生成？`
        : `重新生成${r.country_name_zh}合规简报约需数分钟；生成期间企业用户看到的仍是当前已发布内容，生成完成后自动切换为新版并提醒在该国有业务项目的企业。`,
      locked ? '简报已归档锁定 · 确认全篇重生成？' : '重新生成简报',
      { confirmButtonText: locked ? '强制全篇生成' : '开始生成', cancelButtonText: '取消', type: 'warning' },
    )
  } catch { return }
  try {
    await api.post(`/admin/knowledge/whitepapers/${r.country_code}/update${locked ? '?force=true' : ''}`, {})
    ElMessage.success('已开始重新生成，完成后状态将自动刷新')
    await load()
  } catch (e) { ElMessage.error((e as Error).message) }
}
const downloadingKey = ref('')
async function download(r: Row, kind: 'pdf' | 'docx') {
  const key = `${r.country_code}-${kind}`
  if (downloadingKey.value) return
  downloadingKey.value = key
  try {
    const res = await fetch(`/api/admin/knowledge/whitepapers/${r.country_code}/export.${kind}`, { headers: { Authorization: `Bearer ${api.token()}` } })
    if (!res.ok) {
      let msg = '下载失败，可重试'
      try { msg = (await res.json()).detail || msg } catch { /* 非结构化错误 */ }
      throw new Error(msg)
    }
    const url = URL.createObjectURL(await res.blob())
    const a = document.createElement('a'); a.href = url; a.download = serverFilename(res, `${r.country_name_zh}汽车数据合规洞察简报.${kind}`)
    document.body.appendChild(a); a.click(); a.remove(); window.setTimeout(() => URL.revokeObjectURL(url), 30000)
  } catch (e) { ElMessage.error((e as Error).message) } finally { downloadingKey.value = '' }
}
const highlightedCountry = ref('')
const isMobile = ref(typeof window !== 'undefined' ? window.innerWidth <= 1360 : false)
function syncViewport() {
  if (typeof window !== 'undefined') isMobile.value = window.innerWidth <= 1360
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
  timer = window.setInterval(() => {
    if (rows.value.some((r) => r.updating || r.outdated === null)) load()
  }, 5000)
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
async function openSyncDialog(r: Row, presetDims: string[] = []) {
  if ((r.review_status === 'approved' || r.manual_edited) && !presetDims.length) {
    try {
      await ElMessageBox.confirm(
        `《${r.country_name_zh}合规简报》当前包含人工审定或归档锁定内容。按章节更新将重新生成所勾选章节（未勾选章节与已锁定篇首摘要保持不变）。确认继续选择受影响章节？`,
        '按受影响章节更新确认',
        { confirmButtonText: '继续选择章节', cancelButtonText: '取消', type: 'warning' },
      )
    } catch { return }
  }
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
const droppedLoading = ref('')
const droppedOf = ref<{ row: Row; items: Dropped[] } | null>(null)
async function showDropped(r: Row) {
  droppedLoading.value = r.country_code
  try {
    const res = await api.get<{ items: Dropped[] }>(`/admin/knowledge/whitepapers/${r.country_code}/dropped`)
    droppedOf.value = { row: r, items: res.items }
  } catch (e) { ElMessage.error((e as Error).message) } finally { droppedLoading.value = '' }
}

// 在线人工修订、审核归档锁定与历史版本回退
const origBlocksJson = ref('')
const previewVerLoading = ref<number | null>(null)
const editorDrawer = reactive({
  open: false,
  loading: false,
  saving: false,
  tab: 'edit' as 'edit' | 'versions',
  row: null as Row | null,
  versionNo: 0,
  reviewStatus: 'auto_verified' as 'approved' | 'auto_verified',
  blocks: [] as WpBlock[],
  versions: [] as WpVersionItem[],
  note: '',
  approveOnSave: true,
  previewVerNo: null as number | null,
  previewBlocks: [] as WpBlock[],
})

async function openEditor(r: Row, defaultTab: 'edit' | 'versions' = 'edit') {
  editorDrawer.open = true
  editorDrawer.loading = true
  editorDrawer.tab = defaultTab
  editorDrawer.row = r
  editorDrawer.note = ''
  editorDrawer.approveOnSave = r.review_status === 'approved'
  editorDrawer.previewVerNo = null
  editorDrawer.previewBlocks = []
  try {
    const res = await api.get<{
      version_no: number
      review_status: 'approved' | 'auto_verified'
      blocks: WpBlock[]
      versions: WpVersionItem[]
    }>(`/admin/knowledge/whitepapers/${r.country_code}/versions`)
    editorDrawer.versionNo = res.version_no
    editorDrawer.reviewStatus = res.review_status || 'auto_verified'
    editorDrawer.approveOnSave = editorDrawer.reviewStatus === 'approved'
    editorDrawer.blocks = JSON.parse(JSON.stringify(res.blocks || []))
    origBlocksJson.value = JSON.stringify(editorDrawer.blocks)
    editorDrawer.versions = res.versions || []
  } catch (e) {
    ElMessage.error((e as Error).message)
    editorDrawer.open = false
  } finally {
    editorDrawer.loading = false
  }
}

async function saveEditedBriefing() {
  if (!editorDrawer.row || editorDrawer.saving) return
  editorDrawer.saving = true
  try {
    await api.patch(`/admin/knowledge/whitepapers/${editorDrawer.row.country_code}/blocks`, {
      blocks: editorDrawer.blocks,
      note: editorDrawer.note.trim() || '人工在线修订定稿',
      approve: editorDrawer.approveOnSave,
    })
    ElMessage.success('已保存简报修订并生成新版本，Word 与 PDF 导出件已同步更新')
    await load()
    const updated = rows.value.find((x) => x.country_code === editorDrawer.row?.country_code) || editorDrawer.row
    await openEditor(updated, 'versions')
  } catch (e) {
    ElMessage.error((e as Error).message)
  } finally {
    editorDrawer.saving = false
  }
}

async function toggleReviewLock(nextApproved: boolean) {
  if (!editorDrawer.row || editorDrawer.saving) return
  if (nextApproved && JSON.stringify(editorDrawer.blocks) !== origBlocksJson.value) {
    try {
      await ElMessageBox.confirm(
        '检测到当前抽屉有尚未保存的在线修订正文，是否一并保存为新版本并标记为人工审核归档？',
        '一并保存未提交修订',
        { confirmButtonText: '保存并归档锁定', cancelButtonText: '取消', type: 'info' },
      )
    } catch { return }
    editorDrawer.approveOnSave = true
    await saveEditedBriefing()
    return
  }
  editorDrawer.saving = true
  try {
    await api.post(`/admin/knowledge/whitepapers/${editorDrawer.row.country_code}/review`, {
      approved: nextApproved,
      note: nextApproved ? '人工复核通过并归档锁定' : '解除归档锁定',
    })
    editorDrawer.reviewStatus = nextApproved ? 'approved' : 'auto_verified'
    editorDrawer.approveOnSave = nextApproved
    const verRes = await api.get<{ versions: WpVersionItem[] }>(`/admin/knowledge/whitepapers/${editorDrawer.row.country_code}/versions`).catch(() => null)
    if (verRes?.versions) editorDrawer.versions = verRes.versions
    ElMessage.success(nextApproved ? '已标记为人工审核归档，防止自动全篇重做造成内容漂移' : '已解除归档锁定，保存时默认保留待审状态')
    await load()
  } catch (e) {
    ElMessage.error((e as Error).message)
  } finally {
    editorDrawer.saving = false
  }
}

async function previewHistoricalVersion(v: WpVersionItem) {
  if (!editorDrawer.row) return
  if (editorDrawer.previewVerNo === v.version_no) {
    editorDrawer.previewVerNo = null
    editorDrawer.previewBlocks = []
    return
  }
  previewVerLoading.value = v.version_no
  try {
    const res = await api.get<{ blocks: WpBlock[] }>(
      `/admin/knowledge/whitepapers/${editorDrawer.row.country_code}/versions/${v.version_no}`,
    )
    editorDrawer.previewVerNo = v.version_no
    editorDrawer.previewBlocks = res.blocks || []
  } catch (e) {
    ElMessage.error((e as Error).message)
  } finally {
    previewVerLoading.value = null
  }
}

async function rollbackVersion(v: WpVersionItem) {
  if (!editorDrawer.row || editorDrawer.saving) return
  try {
    await ElMessageBox.confirm(
      `确认将《${editorDrawer.row.country_name_zh}合规简报》回退至第 ${v.version_no} 版（${when(v.created_at)}）？回退将生成新的历史版本记录，原有版本不会丢失。`,
      '确认回退简报版本',
      { confirmButtonText: '确认回退', cancelButtonText: '取消', type: 'warning' },
    )
  } catch { return }
  editorDrawer.saving = true
  try {
    await api.post(`/admin/knowledge/whitepapers/${editorDrawer.row.country_code}/versions/${v.version_no}/rollback`, {})
    ElMessage.success(`已回退恢复至第 ${v.version_no} 版内容`)
    await load()
    const updated = rows.value.find((x) => x.country_code === editorDrawer.row?.country_code) || editorDrawer.row
    await openEditor(updated, 'versions')
  } catch (e) {
    ElMessage.error((e as Error).message)
  } finally {
    editorDrawer.saving = false
  }
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
      <el-table-column label="国家" width="124" class-name="cy-col-nowrap"><template #default="{ row }"><span :data-wp-country="row.country_code">{{ row.country_name_zh }}</span></template></el-table-column>
      <el-table-column label="状态" min-width="144">
        <template #default="{ row }">
          <el-tag v-if="row.updating" type="warning" size="small">更新中…</el-tag>
          <el-tag v-else-if="!row.available" type="info" size="small">未生成</el-tag>
          <el-tag v-else-if="row.outdated" type="warning" size="small">{{ row.new_documents ? `有 ${row.new_documents} 份新入库法规未纳入，可按章节更新` : '法规库有变化，可按章节更新' }}</el-tag>
          <el-tag v-else-if="row.outdated === null" type="info" size="small">正在核对有无新入库法规</el-tag>
          <span v-else class="wp-state-group">
            <span v-if="row.review_status === 'approved'" class="cy-status cy-status--done" :data-testid="`wp-approved-${row.country_code}`">已人工审核归档</span>
            <el-tag v-else type="success" size="small">已按最新法规生成（{{ when(row.updated_at) }}）</el-tag>
            <span v-if="row.manual_edited" class="wp-sub-note">（含人工修订 · 第 {{ row.version_no }} 版）</span>
          </span>
        </template>
      </el-table-column>
      <el-table-column label="正文篇幅" width="96" class-name="cy-col-nowrap"><template #default="{ row }">{{ row.chars ? `${row.chars.toLocaleString()} 字` : '—' }}</template></el-table-column>
      <el-table-column label="核心引证依据" min-width="186" class-name="cy-col-nowrap"><template #default="{ row }"><span class="wp-cite-basis">{{ row.documents ? `引证 ${row.documents} 部法规 · ${row.clauses} 条` : '—' }}</span></template></el-table-column>
      <el-table-column label="引文逐句核验" min-width="148" class-name="cy-col-nowrap"><template #default="{ row }"><el-button v-if="row.available && row.dropped" link type="primary" :loading="droppedLoading === row.country_code" :data-testid="`wp-dropped-${row.country_code}`" @click="showDropped(row)">已拦截剔除 {{ row.dropped }} 句</el-button><span v-else class="cy-status cy-status--done">{{ row.available ? '全部引证已核验' : '—' }}</span></template></el-table-column>
      <el-table-column label="操作" width="426" :fixed="isMobile ? false : 'right'">
        <template #default="{ row }">
          <div class="wp-ops">
            <el-button v-if="canSyncDims && row.available" size="small" :disabled="row.updating" :data-testid="`wp-edit-${row.country_code}`" @click="openEditor(row, 'edit')">在线修订/版本</el-button>
            <el-button v-if="canSyncDims && row.available" size="small" type="primary" plain :disabled="row.updating" :data-testid="`wp-sync-${row.country_code}`" @click="openSyncDialog(row)">按章节更新</el-button>
            <el-button v-if="isAdmin" size="small" :disabled="row.updating" :data-testid="`wp-update-${row.country_code}`" @click="update(row)">{{ row.available ? '全篇更新' : '生成' }}</el-button>
            <el-button v-if="row.available" size="small" link type="primary" :loading="downloadingKey === `${row.country_code}-pdf`" @click="download(row, 'pdf')">PDF</el-button>
            <el-button v-if="row.available" size="small" link type="primary" :loading="downloadingKey === `${row.country_code}-docx`" @click="download(row, 'docx')">Word</el-button>
          </div>
        </template>
      </el-table-column>
    </el-table>

    <el-dialog :model-value="!!syncDialogRow" :title="syncDialogRow ? `按受影响章节更新 · ${syncDialogRow.country_name_zh}合规简报` : ''" width="min(620px, 94vw)" @close="syncDialogRow = null">
      <div v-if="syncDialogRow" class="sync-body">
        <p class="drop-lead">法规发生局部修订并完成审核后，请勾选本次受影响的简报章节（<template v-if="!isAdmin">运维权限单次最多勾选 7 个受影响章节；</template>其余未变动章节原样保留）。</p>
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

    <el-drawer
      :model-value="editorDrawer.open"
      size="min(780px, 95vw)"
      :title="editorDrawer.row ? `${editorDrawer.row.country_name_zh}国家合规简报 · 在线修订与版本管理（当前第 ${editorDrawer.versionNo} 版）` : ''"
      @close="editorDrawer.open = false"
    >
      <div v-loading="editorDrawer.loading" class="wp-editor-wrap" data-testid="wp-editor-drawer">
        <div class="wp-editor-head">
          <el-radio-group v-model="editorDrawer.tab" size="small" data-testid="wp-editor-tabs">
            <el-radio-button value="edit">在线人工修订</el-radio-button>
            <el-radio-button value="versions">历史版本与回退（{{ editorDrawer.versions.length }}）</el-radio-button>
          </el-radio-group>
          <div class="wp-review-bar">
            <span v-if="editorDrawer.reviewStatus === 'approved'" class="cy-status cy-status--done">已人工审核归档（锁定防漂移）</span>
            <span v-else class="wp-sub-note">当前状态：自动核验稿</span>
            <el-button
              v-if="editorDrawer.reviewStatus !== 'approved'"
              size="small"
              type="primary"
              plain
              :loading="editorDrawer.saving"
              data-testid="wp-btn-approve"
              @click="toggleReviewLock(true)"
            >标记为人工审核归档</el-button>
            <el-button
              v-else
              size="small"
              :loading="editorDrawer.saving"
              data-testid="wp-btn-unlock"
              @click="toggleReviewLock(false)"
            >解除归档锁定</el-button>
          </div>
        </div>

        <template v-if="editorDrawer.tab === 'edit'">
          <p class="drop-lead">可直接修改简报各章节段落与要点。保存后自动生成带日期的新版本并刷新 Word、PDF 导出件；勾选人工审核归档后，系统将锁定定稿以防止全篇重做引发内容漂移（法规局部修订时仅按关联章节定向更新）。</p>
          <div class="wp-save-bar">
            <el-input v-model="editorDrawer.note" placeholder="修订说明（选填，例如：复核第三章出境申报门槛措辞）" maxlength="120" style="max-width: 340px" data-testid="wp-edit-note" />
            <el-checkbox v-model="editorDrawer.approveOnSave" data-testid="wp-edit-approve-chk">保存后标记为人工审核归档</el-checkbox>
            <el-button type="primary" :loading="editorDrawer.saving" data-testid="wp-btn-save-blocks" @click="saveEditedBriefing">保存为新版本</el-button>
          </div>
          <div class="wp-blocks-list">
            <div v-for="(blk, bi) in editorDrawer.blocks" :key="bi" class="wp-blk-card">
              <template v-if="blk.type === 'title'">
                <label class="wp-blk-lbl">简报主标题</label>
                <el-input v-model="blk.text" :data-testid="`wp-blk-${bi}`" />
              </template>
              <template v-else-if="blk.type === 'heading' || blk.type === 'subheading'">
                <strong class="wp-blk-heading">{{ blk.text }}</strong>
              </template>
              <template v-else-if="blk.type === 'paragraph'">
                <el-input v-model="blk.text" type="textarea" :autosize="{ minRows: 2, maxRows: 6 }" :data-testid="`wp-blk-${bi}`" />
              </template>
              <template v-else-if="blk.type === 'list' && Array.isArray(blk.items)">
                <div v-for="(_, li) in blk.items" :key="li" class="wp-list-row">
                  <span class="wp-bullet">•</span>
                  <el-input v-model="blk.items[li]" type="textarea" :autosize="{ minRows: 1, maxRows: 4 }" :data-testid="`wp-blk-${bi}-${li}`" />
                </div>
              </template>
              <template v-else-if="blk.type === 'table'">
                <div class="wp-tbl-note">参考条文/准入附表（共 {{ (blk.rows || []).length }} 行，随引证自动维护）</div>
              </template>
            </div>
          </div>
        </template>

        <template v-else>
          <p class="drop-lead">每次自动生成、按受影响章节定向更新、人工在线修订与版本回退均完整保留历史快照，支持按日期预览任意版本并一键回退恢复。</p>
          <div v-for="v in editorDrawer.versions" :key="v.version_no" class="drop-item" :data-testid="`wp-ver-${v.version_no}`">
            <div class="wp-ver-head">
              <div>
                <strong>第 {{ v.version_no }} 版</strong>
                <span class="wp-sub-note"> · {{ when(v.created_at) }} · {{ v.chars.toLocaleString() }} 字</span>
                <span v-if="v.is_latest" class="cy-status cy-status--done" style="margin-left: 8px">当前生效</span>
                <span v-if="v.review_status === 'approved'" class="wp-sub-note"> · 已审核归档</span>
              </div>
              <div class="wp-ops">
                <el-button size="small" link type="primary" :loading="previewVerLoading === v.version_no" :data-testid="`wp-ver-preview-${v.version_no}`" @click="previewHistoricalVersion(v)">{{ editorDrawer.previewVerNo === v.version_no ? '收起预览' : '预览正文' }}</el-button>
                <el-button v-if="!v.is_latest" size="small" type="primary" plain :loading="editorDrawer.saving" :data-testid="`wp-ver-rollback-${v.version_no}`" @click="rollbackVersion(v)">一键回退至此版</el-button>
              </div>
            </div>
            <p class="wp-ver-note">版本说明：{{ v.note }}</p>
            <div v-if="editorDrawer.previewVerNo === v.version_no" class="wp-ver-preview" :data-testid="`wp-ver-body-${v.version_no}`">
              <template v-for="(pb, pi) in editorDrawer.previewBlocks" :key="pi">
                <h4 v-if="pb.type === 'title' || pb.type === 'heading' || pb.type === 'subheading'">{{ pb.text }}</h4>
                <p v-else-if="pb.type === 'paragraph'">{{ pb.text }}</p>
                <ul v-else-if="pb.type === 'list' && pb.items"><li v-for="(it, ii) in pb.items" :key="ii">{{ it }}</li></ul>
              </template>
            </div>
          </div>
        </template>
      </div>
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
.wp-cite-basis { font-weight: 600; color: #20323f; white-space: nowrap; }
.wp-ops { display: flex; align-items: center; gap: 6px; white-space: nowrap; }
.wp-ops :deep(.el-button.is-link) { padding: 4px 6px; }
.wp-state-group { display: inline-flex; align-items: center; gap: 4px; flex-wrap: wrap; }
.wp-sub-note { font-size: 13px; color: #64748b; font-weight: 400; }
.wp-editor-wrap { display: flex; flex-direction: column; gap: 12px; }
.wp-editor-head { display: flex; align-items: center; justify-content: space-between; gap: 12px; flex-wrap: wrap; padding-bottom: 8px; border-bottom: 1px solid var(--el-border-color-lighter); }
.wp-review-bar { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.wp-save-bar { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; padding: 8px 0; }
.wp-blocks-list { display: flex; flex-direction: column; gap: 10px; }
.wp-blk-card { padding: 6px 0; }
.wp-blk-lbl { display: block; font-size: 13px; font-weight: 600; margin-bottom: 4px; color: #20323f; }
.wp-blk-heading { display: block; font-size: 14px; font-weight: 600; color: #111827; padding-top: 6px; }
.wp-list-row { display: flex; align-items: flex-start; gap: 8px; margin-bottom: 6px; }
.wp-bullet { font-size: 14px; line-height: 2; color: #111827; }
.wp-tbl-note { font-size: 13px; color: #4b5e6c; padding: 6px 10px; border: 1px solid var(--el-border-color-lighter); border-radius: 6px; }
.wp-ver-head { display: flex; align-items: center; justify-content: space-between; gap: 10px; flex-wrap: wrap; }
.wp-ver-note { margin: 6px 0 0; font-size: 13px; color: #4b5e6c; }
.wp-ver-preview { margin-top: 10px; padding: 10px 12px; border-top: 1px solid var(--el-border-color-lighter); font-size: 13px; line-height: 1.7; max-height: 320px; overflow-y: auto; }
.wp-ver-preview h4 { margin: 8px 0 4px; font-size: 14px; font-weight: 600; }
.wp-ver-preview p { margin: 4px 0; }
:deep(.el-table__row.wp-row-highlight > td.el-table__cell) {
  background-color: #ecf5ff !important;
  transition: background-color 0.3s ease;
}
</style>
