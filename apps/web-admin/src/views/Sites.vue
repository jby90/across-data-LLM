<script setup lang="ts">
// 网址里百分号编码的阿拉伯文等解码后显示，用户能读懂；链接本身不变
const readableUrl = (u: string) => { try { return decodeURI(u) } catch { return u } }
import {
  DOC_TYPE_LABEL,
  formatDocTypeZh,
  RUN_STATUS_LABEL,
  STRATEGY_LABEL,
  type RunOut,
  type SiteOut, localTime } from '@cyshuhe/shared'
import { ElMessage, ElMessageBox } from 'element-plus'
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '../api'
import { COUNTRY_NAMES } from '@cyshuhe/shared/src/regulationDirectory'

const route = useRoute()
const router = useRouter()
const sites = ref<SiteOut[]>([])
const knownSiteCountries = ref<string[]>([])
const country = ref(String(route.query.country || route.query.country_code || '').trim())
const onlyAttention = ref(route.query.attention === '1')
watch(() => route.query.attention, (v) => { onlyAttention.value = v === '1' })
watch(
  () => [route.query.country, route.query.country_code],
  ([c1, c2]) => {
    const next = String(c1 || c2 || '').trim()
    if (next !== country.value) {
      country.value = next
      void load()
    }
  },
)
const attentionCount = computed(() => sites.value.filter((s) => s.enabled && !!s.attention).length)
const filteredSites = computed(() =>
  onlyAttention.value ? sites.value.filter((s) => s.enabled && !!s.attention) : sites.value,
)
const siteCountryOptions = computed(() => {
  const codes = new Set<string>(knownSiteCountries.value)
  for (const s of sites.value) {
    if (s.country_code) codes.add(s.country_code)
  }
  if (country.value) codes.add(country.value)
  return [...codes]
    .sort((a, b) => a.localeCompare(b))
    .map((code) => ({ code, name: COUNTRY_NAMES[code] || code }))
})
// 第 7.5 项二轮：站点多（上百个），分页显示，每页 30 个；表头固定
const sitePage = ref(1)
const SITE_PAGE = 30
const pagedSites = computed(() => filteredSites.value.slice((sitePage.value - 1) * SITE_PAGE, sitePage.value * SITE_PAGE))
watch([sites, onlyAttention], () => { sitePage.value = 1 })
const scope = ref<'monitor' | 'imports'>('monitor')
const loading = ref(false)

const fmt = (s: string | null | undefined) => localTime(s || null) || '—'
const cleanRunMsg = (m: string | null | undefined) => {
  if (!m) return '—'
  return m
    .replace(/robots\.txt/gi, '站点访问声明')
    .replace(/[A-Za-z]+Error:/g, '连接异常：')
}

function importFileLabel(row: SiteOut): string {
  if (row.sample_titles?.length && row.sample_titles[0]) return row.sample_titles[0]
  const raw = readableUrl(row.entry_url || '')
  const seg = raw.split(/[?#]/)[0].split('/').filter(Boolean).pop() || ''
  if (seg && /\.[a-z0-9]{2,5}$/i.test(seg)) return `官方发布原件（${seg}）`
  if (seg && seg.length <= 48 && !/^index|^view|^get/i.test(seg)) return `官方发布页面（${seg}）`
  return '已核验归档原件（详见原始链接）'
}

function siteDocTypesLabel(row: SiteOut): string {
  const types = (row.doc_types && row.doc_types.length ? row.doc_types : [row.document_type])
    .map((t) => String(t || '').trim().toUpperCase())
    .filter((t) => t && t !== 'OTHER')
  if (!types.length) return row.document_count > 0 ? '综合法规与指南' : '官方法规目录'
  return [...new Set(types.map((t) => formatDocTypeZh(t, '官方法规')))].join('、')
}

function siteVerifyStatus(row: SiteOut): { label: string; type: 'success' | 'warning' | 'danger' | 'info' } {
  if (row.terms_status === 'rejected') return { label: '来源已停用', type: 'danger' }
  if (row.attention) return { label: '需人工处理', type: 'warning' }
  if (!row.enabled) return { label: '已暂停定期检查', type: 'info' }
  if (row.terms_status === 'approved' || row.document_count > 0) {
    return { label: '官方来源正常', type: 'success' }
  }
  return { label: '新增来源待确认', type: 'warning' }
}

async function toggleEnabled(site: SiteOut) {
  try {
    await api.patch(`/admin/sites/${site.id}`, { enabled: !site.enabled })
    ElMessage.success(site.enabled ? '已暂停该站点定期自动检查' : '已恢复该站点定期自动检查')
    await load()
  } catch (e) {
    ElMessage.error((e as Error).message)
  }
}

async function triggerRunNow(site: SiteOut) {
  try {
    await api.post(`/admin/sites/${site.id}/run`, {})
    ElMessage.success('已加入立即检查队列，后台完成后自动刷新状态')
    await load()
  } catch (e) {
    ElMessage.error((e as Error).message)
  }
}

async function load() {
  loading.value = true
  try {
    const cleanCountry = (country.value || '').trim()
    const params = new URLSearchParams({ scope: scope.value })
    if (cleanCountry) params.set('country_code', cleanCountry)
    const [list, allForScope] = await Promise.all([
      api.get<SiteOut[]>(`/admin/sites?${params.toString()}`),
      cleanCountry && !knownSiteCountries.value.length
        ? api.get<SiteOut[]>(`/admin/sites?scope=${scope.value}`).catch(() => [] as SiteOut[])
        : Promise.resolve([] as SiteOut[]),
    ])
    sites.value = list
    const sourceForCountries = cleanCountry ? allForScope : list
    if (sourceForCountries.length) {
      knownSiteCountries.value = [...new Set(sourceForCountries.map((s) => s.country_code).filter(Boolean))]
    }
  } finally {
    loading.value = false
  }
}

function goDocuments(site: SiteOut) {
  router.push({
    name: 'documents',
    query: {
      site_id: site.id,
      publisher: site.publisher,
      country_code: site.country_code,
      status: 'all',
    },
  })
}

// ---- 新增站点 ----
const createDialog = ref(false)
const form = reactive({
  country_code: '',
  publisher: '',
  language: 'en',
  document_type: 'GUIDANCE',
  entry_url: '',
  strategy: 'INDEX_PAGE',
  keywords: '',
  extensions: '.pdf',
  interval_hours: 168,
  terms_url: '',
})

const split = (s: string) =>
  s
    .split(/[,，\n]+/)
    .map((x) => x.trim())
    .filter(Boolean)

async function create() {
  try {
    const created = await api.post<SiteOut>('/admin/sites', {
      country_code: form.country_code.trim().toUpperCase(),
      publisher: form.publisher.trim(),
      language: form.language.trim(),
      document_type: form.document_type,
      entry_url: form.entry_url.trim(),
      strategy: form.strategy,
      interval_hours: form.interval_hours,
      terms_url: null,
      config: {
        keywords: split(form.keywords),
        extensions: split(form.extensions),
      },
    })
    if (created?.id) {
      await api.post(`/admin/sites/${created.id}/terms`, {
        status: 'approved',
        note: '官方公开法规发布门户登记入库',
      })
      if (form.strategy !== 'DIRECT') {
        await api.patch(`/admin/sites/${created.id}`, { enabled: true })
      }
    }
    ElMessage.success('已新增官方站点并启用定期自动检查')
    createDialog.value = false
    await load()
  } catch (e) {
    ElMessage.error((e as Error).message)
  }
}

// ---- 条款确认 ----
async function decideTerms(site: SiteOut, status: 'approved' | 'rejected') {
  const action = status === 'approved' ? '确认可以采集' : '停用来源'
  try {
    const { value } = await ElMessageBox.prompt(
      '请写明依据（如：法务确认说明、使用条款链接、核验日期），该记录将留存备查。',
      `${action}：${site.publisher}`,
      {
        inputPlaceholder: '依据说明',
        inputPattern: /\S+/,
        inputErrorMessage: '必须填写依据',
      },
    )
    await api.post(`/admin/sites/${site.id}/terms`, { status, note: value })
    ElMessage.success('已记录')
    await load()
  } catch (e) {
    if (e !== 'cancel') ElMessage.error((e as Error).message ?? String(e))
  }
}

// ---- 采集历史记录 ----
const runsDrawer = ref(false)
const runs = ref<RunOut[]>([])
const runsSite = ref<SiteOut | null>(null)
async function showRuns(site: SiteOut) {
  runsSite.value = site
  runs.value = await api.get<RunOut[]>(`/admin/sites/${site.id}/runs`)
  runsDrawer.value = true
}

onMounted(load)
</script>

<template>
  <div class="management-page">
    <div class="page-heading">
      <div>
        <h1>采集站点</h1>
        <p>分类管理各国官方法规发布门户、定期自动检查状态与单篇核验入库的法规来源档案。</p>
      </div>
      <div class="page-actions">
        <el-checkbox v-if="scope === 'monitor'" v-model="onlyAttention" border data-testid="sites-only-attention">
          仅看待处理（{{ attentionCount }}）
        </el-checkbox>
        <el-select
          v-model="country"
          placeholder="全部国家"
          aria-label="筛选国家"
          style="width: 160px"
          clearable
          filterable
          @change="load"
        >
          <el-option v-for="c in siteCountryOptions" :key="c.code" :label="c.name" :value="c.code" />
        </el-select>
        <el-button :type="createDialog ? 'default' : 'primary'" @click="createDialog = true">新增官方站点</el-button>
      </div>
    </div>
    <el-alert
      type="info"
      :closable="false"
      style="margin-bottom: 12px"
      :title="
        scope === 'monitor'
          ? '官方法规来源站点汇总各国数据保护、交通及通信主管部门的法规公报入口，所有已收录法规均绑定对应的官方发布渠道。'
          : '针对部分需人工核验下载的境外官方网站，由运维人员从官方渠道获取正式原件后单篇导入入库，逐份留存原始官方链接。'
      "
    />

    <el-radio-group v-model="scope" class="cy-page-tabs" @change="onlyAttention = false; knownSiteCountries = []; load()">
      <el-radio-button value="monitor">官方法规来源站点</el-radio-button>
      <el-radio-button value="imports">单份文件的导入记录</el-radio-button>
    </el-radio-group>

    <el-table
      v-if="scope === 'monitor'"
      :data="pagedSites"
      v-loading="loading"
      border
      max-height="calc(100vh - 240px)"
      scrollbar-always-on
    >
      <el-table-column v-if="!country" label="国家" width="70" class-name="cy-col-nowrap">
        <template #default="{ row }">{{ COUNTRY_NAMES[row.country_code] || row.country_code }}</template>
      </el-table-column>
      <el-table-column label="官方发布机构与门户入口" min-width="196">
        <template #default="{ row }">
          <div class="site-name">
            <b :title="row.publisher">{{ row.publisher }}</b>
            <a :href="row.entry_url" target="_blank" rel="noopener noreferrer" :title="readableUrl(row.entry_url)" class="site-link">官方门户↗</a>
          </div>
          <div v-if="row.attention" class="site-note attention-note">{{ row.attention }}</div>
          <div v-else-if="row.sample_titles?.length" class="site-note" :title="row.sample_titles.join(' / ')">
            代表法规：{{ row.sample_titles[0] }}<template v-if="row.document_count > 1"> 等共 {{ row.document_count }} 份</template>
          </div>
          <div v-else class="site-note">{{ readableUrl(row.entry_url) }}</div>
        </template>
      </el-table-column>
      <el-table-column label="收录法规类型" min-width="116">
        <template #default="{ row }">{{ siteDocTypesLabel(row) }}</template>
      </el-table-column>
      <el-table-column label="已收录法规" width="88" align="center" class-name="cy-col-nowrap">
        <template #default="{ row }">
          <el-button v-if="row.document_count > 0" link type="primary" size="small" @click="goDocuments(row)">
            {{ row.document_count }} 份
          </el-button>
          <span v-else style="color: #909399">待收录</span>
        </template>
      </el-table-column>
      <el-table-column label="监测与核验状态" width="130" align="center">
        <template #default="{ row }">
          <el-tag :type="siteVerifyStatus(row).type" size="small">
            {{ siteVerifyStatus(row).label }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="最近更新时间" width="144" class-name="cy-col-nowrap">
        <template #default="{ row }">{{ fmt(row.latest_discovered_at || row.last_run_at || row.terms_decided_at) }}</template>
      </el-table-column>
      <el-table-column label="操作" width="166" fixed="right" class-name="cy-col-nowrap cy-col-ops">
        <template #default="{ row }">
          <div class="site-ops">
            <el-button v-if="row.attention && row.enabled && row.terms_status === 'approved'" link type="primary" size="small" @click="triggerRunNow(row)">立即检查</el-button>
            <el-button v-else-if="row.document_count > 0" link type="primary" size="small" @click="goDocuments(row)">查看收录文件</el-button>
            <el-button v-else link type="primary" size="small" @click="showRuns(row)">检查记录</el-button>
            <el-dropdown trigger="click" teleported>
              <el-button link size="small">更多</el-button>
              <template #dropdown>
                <el-dropdown-menu>
                  <el-dropdown-item v-if="row.enabled && row.terms_status === 'approved'" @click="triggerRunNow(row)">立即检查更新</el-dropdown-item>
                  <el-dropdown-item v-if="row.terms_status === 'approved'" @click="toggleEnabled(row)">{{ row.enabled ? '暂停定期自动检查' : '恢复定期自动检查' }}</el-dropdown-item>
                  <el-dropdown-item v-if="row.document_count > 0" @click="goDocuments(row)">查看已收录法规</el-dropdown-item>
                  <el-dropdown-item @click="showRuns(row)">查看历史检查记录</el-dropdown-item>
                </el-dropdown-menu>
              </template>
            </el-dropdown>
          </div>
        </template>
      </el-table-column>
    </el-table>

    <el-table
      v-else
      :data="pagedSites"
      v-loading="loading"
      border
      max-height="calc(100vh - 240px)"
      scrollbar-always-on
    >
      <el-table-column v-if="!country" label="国家" width="70" class-name="cy-col-nowrap">
        <template #default="{ row }">{{ COUNTRY_NAMES[row.country_code] || row.country_code }}</template>
      </el-table-column>
      <el-table-column label="官方发布机构" min-width="156" show-overflow-tooltip>
        <template #default="{ row }"><b>{{ row.publisher }}</b></template>
      </el-table-column>
      <el-table-column label="导入的法规文件" min-width="200">
        <template #default="{ row }">
          <div>{{ importFileLabel(row) }}</div>
          <a :href="row.entry_url" target="_blank" rel="noopener noreferrer" :title="readableUrl(row.entry_url)" class="site-link">查看官方原始链接↗</a>
        </template>
      </el-table-column>
      <el-table-column label="文件类型" width="108">
        <template #default="{ row }">{{ siteDocTypesLabel(row) }}</template>
      </el-table-column>
      <el-table-column label="单篇导入说明" min-width="168">
        <template #default="{ row }">
          <span class="site-note" style="white-space: normal">
            {{ (row.terms_note || '已从官方发布渠道获取正式原件并核验入库').replace(/防爬校验|人机防护|跨境链路访问受限|跨境链路受限/g, '官网直接下载核验') }}
          </span>
        </template>
      </el-table-column>
      <el-table-column label="入库时间" width="144" class-name="cy-col-nowrap">
        <template #default="{ row }">{{ fmt(row.latest_discovered_at || row.terms_decided_at) }}</template>
      </el-table-column>
      <el-table-column label="操作" width="124" align="center" fixed="right" class-name="cy-col-nowrap">
        <template #default="{ row }">
          <el-button v-if="row.document_count > 0" link type="primary" size="small" @click="goDocuments(row)">查看法规详情</el-button>
          <span v-else style="color: #909399">已归档</span>
        </template>
      </el-table-column>
    </el-table>
    <el-pagination v-if="filteredSites.length > SITE_PAGE" v-model:current-page="sitePage" :page-size="SITE_PAGE" :total="filteredSites.length" layout="total, prev, pager, next" class="pager" />

    <el-dialog v-model="createDialog" title="新增采集站点" width="min(580px, 94vw)">
      <el-form label-width="110px">
        <el-form-item label="国家"
          ><el-select v-model="form.country_code" filterable allow-create style="width: 100%" placeholder="选择或输入国家">
            <el-option v-for="(n, c) in COUNTRY_NAMES" :key="c" :label="n" :value="c" />
          </el-select></el-form-item>
        <el-form-item label="发布机构"><el-input v-model="form.publisher" /></el-form-item>
        <el-form-item label="原文语言"
          ><el-select v-model="form.language" style="width: 100%">
            <el-option label="英语" value="en" /><el-option label="法语" value="fr" /><el-option label="阿拉伯语" value="ar" /><el-option label="中文" value="zh" />
          </el-select></el-form-item>
        <el-form-item label="文件类型">
          <el-select v-model="form.document_type" style="width: 100%">
            <el-option
              v-for="(label, key) in DOC_TYPE_LABEL"
              :key="key"
              :label="label"
              :value="key"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="入口地址"
          ><el-input v-model="form.entry_url" placeholder="https://"
        /></el-form-item>
        <el-form-item label="发现方式">
          <el-radio-group v-model="form.strategy">
            <el-radio v-for="(label, key) in STRATEGY_LABEL" :key="key" :value="key">{{
              label
            }}</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="关键词"
          ><el-input v-model="form.keywords" placeholder="多个关键词用逗号分隔，留空不过滤"
        /></el-form-item>
        <el-form-item label="收录文档格式"
          ><el-input v-model="form.extensions" placeholder="默认收录官方常见格式（如 PDF）"
        /></el-form-item>
        <el-form-item label="检查间隔（小时）"
          ><el-input-number v-model="form.interval_hours" :min="1" :max="2160"
        /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="createDialog = false">取消</el-button>
        <el-button type="primary" @click="create">新增</el-button>
      </template>
    </el-dialog>

    <el-drawer v-model="runsDrawer" :title="`历史检查记录：${runsSite?.publisher ?? ''}`" size="min(840px, 94vw)">
      <div class="runs-table-scroll">
        <el-table :data="runs" border size="small" class="runs-table">
          <el-table-column label="开始时间" width="148" class-name="cy-col-nowrap"
            ><template #default="{ row }">{{ fmt(row.started_at) }}</template></el-table-column
          >
          <el-table-column label="方式" width="92" class-name="cy-col-nowrap"
            ><template #default="{ row }">{{
              row.trigger === 'manual' ? '人工触发' : '定时巡检'
            }}</template></el-table-column
          >
          <el-table-column label="结果" width="88" class-name="cy-col-nowrap"
            ><template #default="{ row }">{{
              RUN_STATUS_LABEL[row.status]
            }}</template></el-table-column
          >
          <el-table-column prop="links_found" label="发现" width="68" class-name="cy-col-nowrap" />
          <el-table-column prop="versions_new" label="新版本" width="76" class-name="cy-col-nowrap" />
          <el-table-column prop="errors" label="异常" width="68" class-name="cy-col-nowrap" />
          <el-table-column label="说明" min-width="140" show-overflow-tooltip>
            <template #default="{ row }">{{ cleanRunMsg(row.message) }}</template>
          </el-table-column>
        </el-table>
      </div>
    </el-drawer>
  </div>
</template>
<style scoped>
.site-name { display: flex; align-items: baseline; gap: 10px; }
.site-name b { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.site-link { flex: none; font-size: 13px; }
.site-note.terms-note { white-space: normal; overflow: visible; }
.site-note { display: block; font-size: 13px; line-height: 1.5; color: var(--cy-text-2); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
:deep(.cy-col-ops .cell) { overflow: visible; text-overflow: clip; }
.site-ops { display: inline-flex; align-items: center; gap: 8px; white-space: nowrap; }
.site-ops :deep(.el-button.is-link) { padding: 2px 4px; }
.site-ops :deep(.el-dropdown) { margin-left: 0 !important; }
.site-note.attention-note { color: #b45309; white-space: normal; overflow: visible; }
.site-note.time-note { overflow: visible; text-overflow: clip; white-space: nowrap; }
:deep(.cy-col-time .cell) { overflow: visible; text-overflow: clip; white-space: nowrap; }
.pager { margin-top: 16px; justify-content: flex-end; }
.runs-table-scroll { width: 100%; overflow-x: auto; -webkit-overflow-scrolling: touch; }
.runs-table { min-width: 656px; }
</style>
