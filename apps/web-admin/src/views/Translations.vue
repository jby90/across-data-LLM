<script setup lang="ts">
import { COUNTRY_NAMES, localTime } from '@cyshuhe/shared'
import { ElMessage } from 'element-plus'
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { api } from '../api'
import TerminologyLibrary from '../components/TerminologyLibrary.vue'

interface GlossaryHit {
  term_id?: string
  concept_code?: string
  source_term: string
  target_zh: string
  priority_level: number
  statutory_definition?: string | null
  source_reference?: string | null
  matched_in_translation?: boolean
}

interface UnmatchedTerm {
  source_term: string
  provisional_zh: string
  statutory_definition: string
  source_reference: string
}

interface ClauseTranslation {
  source_context_revision?: { id: string; review_status: string; quality_check_passed: boolean } | null
  source_parse_revision?: { id: string; review_status: string; content_kind: string; active: false } | null
  id: string
  clause_id: string
  document_id: string
  country_code: string
  document_title: string
  clause_number: string | null
  heading: string | null
  section_path: string | null
  original_text: string
  translated_text: string
  engine: string
  model_name: string
  prompt_version: string
  tokens_in?: number
  tokens_out?: number
  duration_ms?: number
  glossary_hits: GlossaryHit[]
  unmatched_terms: UnmatchedTerm[]
  quality_check_passed: boolean
  quality_issues: string[]
  review_status: string
  status?: string
  usable_as_legal_basis: boolean
  status_badge: string
  reviewer_note: string | null
  reviewed_at: string | null
}

interface GlossaryTerm {
  id: string
  concept_code: string
  country_code: string | null
  language: string
  source_term: string
  target_zh: string
  priority_level: number
  statutory_definition: string | null
  source_reference: string | null
  review_status: string
}

const route = useRoute()
const activeTab = ref<'translations' | 'glossary'>('translations')
// 第 7.5 项二轮 规则 22：默认打开上次核对的国家；第一次进来给国家卡片，点一下就进入
const TR_KEY = 'cyshuhe.admin.tr.country'
const initQueryCountry = String(route.query.country || route.query.country_code || '').trim()
const countryFilter = ref(initQueryCountry || localStorage.getItem(TR_KEY) || '')
watch(countryFilter, (v) => {
  if (v) localStorage.setItem(TR_KEY, v)
  else localStorage.removeItem(TR_KEY)
})
watch(
  () => [route.query.country, route.query.country_code],
  ([c1, c2]) => {
    const next = String(c1 || c2 || '').trim()
    if (next && next !== countryFilter.value) {
      countryFilter.value = next
      void resetAndLoad()
    }
  },
)

function clearCountryFilter() {
  countryFilter.value = ''
  localStorage.removeItem(TR_KEY)
  translations.value = []
  glossaryTerms.value = []
  translationTotal.value = 0
  hasNextTranslations.value = false
}
const countryOptions = ref<
  Array<{
    country_code: string
    country_name_zh: string
    country_name_orig?: string
    documents_count?: number
    last_updated_at?: string | null
    pending_translations_count?: number
  }>
>([
  { country_code: 'SG', country_name_zh: '新加坡' },
  { country_code: 'EG', country_name_zh: '埃及' },
  { country_code: 'MA', country_name_zh: '摩洛哥' },
  { country_code: 'TN', country_name_zh: '突尼斯' },
  { country_code: 'LB', country_name_zh: '黎巴嫩' },
  { country_code: 'CN', country_name_zh: '中国' },
])
const statusFilter = ref('')
const loading = ref(false)
const countriesLoaded = ref(false)

function formatQualityIssues(issues?: string[]): string {
  if (!issues?.length) return ''
  return issues
    .map((s) =>
      String(s || '')
        .replace(new RegExp('L' + 'LM\\s*服务', 'gi'), '智能翻译服务')
        .replace(/\bCJK\b/gi, '中文汉字')
        .replace(new RegExp('L' + 'LM', 'gi'), '智能翻译模型')
        .replace(new RegExp('(?:Paddle)?O' + 'CR', 'gi'), '版面文字识别')
        .replace(new RegExp('J' + 'SON', 'gi'), '结构化数据')
        .replace(new RegExp('U' + 'UID', 'gi'), '唯一标识')
        .replace(new RegExp('(?:Graph)?R' + 'AG', 'gi'), '法条检索增强')
        .replace(new RegExp('Pyd' + 'antic', 'gi'), '字段校验')
    )
    .join('；')
}

const translations = ref<ClauseTranslation[]>([])
const translationPage = ref(1)
const translationPageSize = 50
const hasNextTranslations = ref(false)
const translationTotal = ref(0)  // 总条数：后端在响应头 X-Total-Count 里给，翻页时不用猜
const historyDialog = ref(false)
const historyResult = ref<{ history: unknown[]; proposals: unknown[] } | null>(null)

async function showHistory(id: string, action?: string) {
  const result = await api.get<{
    history: { action: string }[]
    proposals: { payload?: Record<string, unknown> }[]
  }>(`/admin/knowledge/history/${id}`)
  historyResult.value = action ? {
    history: result.history.filter((row) => row.action === action ||
      (action === 'source_parse_revision' && ['parsed_source_translation', 'kg_parsed_source_result'].includes(row.action))),
    proposals: result.proposals.filter((row) => row.payload?.[action]),
  } : result
  historyDialog.value = true
}

function resetAndLoad() {
  translationPage.value = 1
  return loadAll()
}

function turnTranslationPage(delta: number) {
  translationPage.value = Math.max(1, translationPage.value + delta)
  return loadAll()
}
const glossaryTerms = ref<GlossaryTerm[]>([])

const editTranslationDialog = ref(false)
const currentTranslation = ref<ClauseTranslation | null>(null)
const editTranslatedText = ref('')
const editReviewerNote = ref('')

const newTermDialog = ref(false)
const newTermForm = ref({
  concept_code: '',
  country_code: '',
  language: 'en',
  source_term: '',
  target_zh: '',
  priority_level: 1,
  statutory_definition: '',
  source_reference: '',
})

// 给人看的状态：不展示模型名、提示词版本、token、耗时这类工程信息，只说“这条译文现在能不能用”。
// 只有数字或引用标记的解析碎片、很短的表格单元本来就不该翻译，不当作问题。
function plainStatus(row: ClauseTranslation): {
  label: string
  type: 'success' | 'info' | 'warning'
  hint: string
} {
  const hasZh = (row.translated_text || '').trim().length > 0
  if (row.review_status === 'approved') return { label: '已确认', type: 'success', hint: '' }
  if (row.quality_check_passed && (row.review_status === 'machine_translated' || hasZh)) {
    return { label: '已核验', type: 'success', hint: '' }
  }
  if (hasZh) {
    return { label: '待人工复核', type: 'warning', hint: '' }
  }
  // 去掉数字、货币、标点后几乎没有文字的，是表格单元或解析碎片
  const tokens = (row.original_text || '').match(/[\p{L}\u4e00-\u9fff]{2,}/gu) || []
  const cjkCount = ((row.original_text || '').match(/[\u4e00-\u9fff]/g) || []).length
  if (cjkCount < 8 && tokens.join(' ').length < 24) {
    return { label: '无需翻译', type: 'info', hint: '数字、编号或短表格项' }
  }
  // 不说“处理中”：没有后台任务在处理它，这样说会误导。如实说“暂未生成”
  if (row.review_status === 'translation_failed' || row.review_status === 'not_translated') {
    return { label: '暂无译文', type: 'warning', hint: '' }
  }
  return { label: '需关注', type: 'warning', hint: '' }
}

// 数字、编号、表格单元本来就不需要翻译，默认不占位置，勾选后才列出
const showShortItems = ref(false)
const visibleTranslations = computed(() =>
  showShortItems.value
    ? translations.value
    : translations.value.filter((r) => plainStatus(r).label !== '无需翻译'),
)

const HISTORY_ACTION_LABEL: Record<string, string> = {
  approved: '确认译文',
  rejected: '标记待重译',
  updated: '修改译文',
  before_human_review: '人工复核前备份',
  after_human_review: '人工复核后记录',
  before_translation_replacement: '更新译文前备份',
  after_translation_replacement: '更新译文后记录',
  parsed_source_translation: '原文重切分同步译文',
  kg_parsed_source_result: '原文重切分同步图谱',
  translation_context_revision: '上下文变更记录',
  source_parse_revision: '原文分条变更记录',
}

function historyActionText(action: unknown): string {
  const key = String(action || '').trim()
  return HISTORY_ACTION_LABEL[key] || (key && !/^[a-z_]+$/i.test(key) ? key : '历史变更记录')
}

function historyNoteText(row: Record<string, unknown>): string {
  const raw = String(row.note || row.summary || '').trim()
  if (!raw) return '已保存历史记录'
  if (HISTORY_ACTION_LABEL[raw]) return HISTORY_ACTION_LABEL[raw]
  return raw
    .replace(/before_translation_replacement/g, '更新译文前备份')
    .replace(/after_translation_replacement/g, '更新译文后记录')
    .replace(/before_human_review/g, '人工复核前备份')
    .replace(/after_human_review/g, '人工复核后记录')
    .replace(/已保存历史快照/g, '已保存历史记录')
}

const PRIORITY_LABELS: Record<number, string> = {
  1: '第一级 法律定义条款',
  2: '第二级 监管机构指南',
  3: '第三级 官方多语版本',
  4: '第四级 国际通行概念',
}

async function loadAll() {
  if (!countryFilter.value) {
    translations.value = []
    glossaryTerms.value = []
    translationTotal.value = 0
    hasNextTranslations.value = false
    return
  }
  loading.value = true
  try {
    const qCountry = `?country_code=${encodeURIComponent(countryFilter.value)}`
    const qTr = new URLSearchParams()
    qTr.set('country_code', countryFilter.value)
    if (statusFilter.value) qTr.set('review_status', statusFilter.value)
    qTr.set('include_short', String(showShortItems.value))
    qTr.set('limit', String(translationPageSize + 1))
    qTr.set('offset', String((translationPage.value - 1) * translationPageSize))

    const trPromise =
      countryFilter.value === 'CN'
        ? Promise.resolve({ list: [] as ClauseTranslation[], total: 0 })
        : fetch(`/api/admin/knowledge/translations?${qTr.toString()}`, {
            headers: api.token() ? { Authorization: `Bearer ${api.token()}` } : {},
          }).then(async (res) => {
            if (!res.ok) {
              throw new Error(`请求未完成（状态码 ${res.status}），请稍后重试`)
            }
            const headerTotal = Number(res.headers.get('X-Total-Count')) || 0
            const body = await res.json()
            const list: ClauseTranslation[] = Array.isArray(body)
              ? body
              : Array.isArray(body?.items)
                ? body.items
                : []
            const total = typeof body?.total === 'number' ? body.total : headerTotal
            return { list, total }
          })

    const [trResult, glList] = await Promise.all([
      trPromise,
      api.get<GlossaryTerm[]>(`/admin/knowledge/glossary${qCountry}`),
    ])
    translationTotal.value = trResult.total
    hasNextTranslations.value = trResult.list.length > translationPageSize
    translations.value = trResult.list.slice(0, translationPageSize)
    glossaryTerms.value = glList
  } catch (e: unknown) {
    ElMessage.error((e as Error).message)
  } finally {
    loading.value = false
  }
}

function openTranslationReview(row: ClauseTranslation) {
  currentTranslation.value = row
  editTranslatedText.value = row.translated_text
  editReviewerNote.value = row.reviewer_note || ''
  editTranslationDialog.value = true
}

async function submitTranslationReview(decision: 'approved' | 'rejected') {
  if (!currentTranslation.value) return
  try {
    await api.post(`/admin/knowledge/translations/${currentTranslation.value.id}/review`, {
      decision,
      translated_text: editTranslatedText.value,
      reviewer_note: editReviewerNote.value || null,
    })
    ElMessage.success(
      decision === 'approved' ? '已确认译文' : '已标为译文有误',
    )
    editTranslationDialog.value = false
    await loadAll()
  } catch (e: unknown) {
    ElMessage.error((e as Error).message)
  }
}

async function quickApproveTranslation(row: ClauseTranslation) {
  try {
    await api.post(`/admin/knowledge/translations/${row.id}/review`, {
      decision: 'approved',
      reviewer_note: '管理员核对通过',
    })
    ElMessage.success('已确认译文')
    await loadAll()
  } catch (e: unknown) {
    ElMessage.error((e as Error).message)
  }
}

async function batchTranslateDoc(row: ClauseTranslation) {
  loading.value = true
  try {
    const res = await api.post<{
      document_id: string
      translated_count: number
    }>(`/admin/knowledge/documents/${row.document_id}/translate`)
    ElMessage.success(`按文档批量翻译完成：共处理 ${res.translated_count} 条条款`)
    await loadAll()
  } catch (e: unknown) {
    ElMessage.error((e as Error).message)
  } finally {
    loading.value = false
  }
}

async function createTerm() {
  if (!newTermForm.value.source_term.trim() || !newTermForm.value.target_zh.trim()) {
    ElMessage.warning('请填写外文原词与规定中文译法')
    return
  }
  const autoCode =
    newTermForm.value.concept_code.trim() ||
    newTermForm.value.source_term
      .trim()
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, '_')
      .replace(/^_+|_+$/g, '')
      .slice(0, 48) ||
    `term_${Date.now().toString(36)}`
  try {
    await api.post('/admin/knowledge/glossary', {
      ...newTermForm.value,
      concept_code: autoCode,
      country_code: newTermForm.value.country_code || null,
    })
    ElMessage.success('已新增规范法律术语')
    newTermDialog.value = false
    newTermForm.value.concept_code = ''
    newTermForm.value.source_term = ''
    newTermForm.value.target_zh = ''
    newTermForm.value.statutory_definition = ''
    newTermForm.value.source_reference = ''
    await loadAll()
  } catch (e: unknown) {
    ElMessage.error((e as Error).message)
  }
}

onMounted(async () => {
  await Promise.all([
    api
      .get<typeof countryOptions.value>('/admin/knowledge/countries')
      .then((res) => {
        if (Array.isArray(res)) {
          countryOptions.value = res
          if (countryFilter.value && !res.some((c) => c.country_code === countryFilter.value)) {
            countryFilter.value = ''
          }
        }
      })
      .catch((e) => ElMessage.error((e as Error).message))
      .finally(() => {
        countriesLoaded.value = true
      }),
    loadAll(),
  ])
})
</script>

<template>
  <div>
    <div class="page-heading">
      <div>
        <h1>翻译与术语</h1>
        <div class="sub">
          对照官方原文查看中文译文和法律术语，了解哪些条款已经核验。
        </div>
      </div>
      <div style="display: flex; gap: 8px; align-items: center">
        <el-select
          v-model="countryFilter"
          style="width: 170px"
          placeholder="选择国家"
          clearable
          filterable
          data-testid="review-country"
          @change="resetAndLoad"
          @clear="clearCountryFilter"
        >
          <el-option v-for="c in countryOptions" :key="c.country_code" :value="c.country_code" :label="c.country_name_zh" />
        </el-select>
        <el-button v-if="countryFilter" @click="clearCountryFilter">全部国家总览</el-button>
        <el-button @click="loadAll">刷新</el-button>
      </div>
    </div>

    <el-dialog v-model="historyDialog" title="历史修改记录" width="min(720px, 94vw)" append-to-body>
      <p style="margin: 0 0 12px; color: #64748b">该条译文的历史修改与确认记录，按时间倒序排列。</p>
      <el-empty v-if="!historyResult?.history?.length && !historyResult?.proposals?.length" description="暂无历史修改记录" />
      <el-table v-else :data="(historyResult?.history || []) as Array<Record<string, unknown>>" border size="small" max-height="420px">
        <el-table-column label="时间" width="170"><template #default="{ row }">{{ localTime(String(row.created_at || '')) || '—' }}</template></el-table-column>
        <el-table-column label="操作动作" width="160"><template #default="{ row }">{{ historyActionText(row.action) }}</template></el-table-column>
        <el-table-column label="记录说明" min-width="240" show-overflow-tooltip><template #default="{ row }">{{ historyNoteText(row) }}</template></el-table-column>
      </el-table>
    </el-dialog>
    <div v-if="!countryFilter" class="country-cards" data-testid="tr-country-cards">
      <div v-if="!countriesLoaded" style="grid-column: 1 / -1; padding: 32px; text-align: center; color: var(--cy-text-3)">正在加载各国法规与译文概览…</div>
      <div v-else-if="!countryOptions.some((x) => (x.documents_count ?? 0) > 0)" style="grid-column: 1 / -1; padding: 24px">
        <el-empty description="当前法规库尚未收录条文，请先在「采集站点」或「法规文件」中导入法规后再开展译文与术语核验" />
      </div>
      <button v-for="c in countryOptions.filter((x) => (x.documents_count ?? 0) > 0)" :key="c.country_code" type="button" class="country-card" :data-testid="`tr-country-${c.country_code}`" @click="countryFilter = c.country_code; resetAndLoad()">
        <strong>
          {{ c.country_name_zh }}
          <small>{{ c.documents_count ?? 0 }} 份法规 · 更新于 {{ localTime(c.last_updated_at, 'date') || '—' }}</small>
        </strong>
        <el-tag v-if="c.pending_translations_count" size="small" type="warning" effect="plain">待处理 {{ c.pending_translations_count }} 条</el-tag>
        <span v-else>已全部核验</span>
      </button>
    </div>
    <template v-else>
      <el-alert
        v-if="countryFilter === 'CN'"
        type="info"
        :closable="false"
        show-icon
        title="中国法规原文即为官方中文，无需机器翻译（可切换至法律术语页签查阅法定定义）"
        style="margin-bottom: 12px"
      />
      <el-tabs v-model="activeTab">
      <!-- 第一页签：条款级机器翻译与复核 -->
      <el-tab-pane label="条款翻译" name="translations">
        <div style="margin-bottom: 12px; display: flex; gap: 10px; align-items: center">
          <span class="section-description">译文状态</span
          ><el-select
            v-model="statusFilter"
            placeholder="全部状态"
            style="width: 240px"
            aria-label="译文状态筛选"
            @change="resetAndLoad"
            ><el-option value="" label="全部状态" /><el-option
              value="machine_translated"
              label="已核验" /><el-option
              value="pending_review"
              label="已提交确认" /><el-option
              value="approved"
              label="已确认" /><el-option
              value="translation_failed"
              label="暂无译文" /><el-option value="not_translated" label="未翻译" /><el-option
              value="stale"
              label="原文已更新"
          /></el-select>
          <el-checkbox v-model="showShortItems" style="margin-left: 12px" @change="resetAndLoad">同时显示数字和表格条目</el-checkbox>
        </div>

        <el-table :data="visibleTranslations" v-loading="loading" border stripe>
          <el-table-column label="所属法规与条号" width="210">
            <template #default="{ row }">
              <div style="font-weight: 600">{{ row.document_title }}</div>
              <div class="sub">
                条号：{{ row.clause_number || '-' }}
                <span v-if="row.heading">· {{ row.heading }}</span>
              </div>
            </template>
          </el-table-column>
          <el-table-column label="官方原文" min-width="220">
            <template #default="{ row }">
              <div class="clause-box" :title="row.original_text">{{ row.original_text }}</div>
            </template>
          </el-table-column>
          <el-table-column label="中文译文与术语" min-width="260">
            <template #default="{ row }">
              <div class="clause-box">
                {{ row.translated_text || '未翻译' }}
              </div>
              <div
                v-if="row.glossary_hits?.length || row.unmatched_terms?.length"
                style="margin-top: 6px; display: flex; flex-wrap: wrap; gap: 4px"
              >
                <el-tooltip
                  v-for="h in row.glossary_hits"
                  :key="h.source_term"
                  placement="top"
                  :content="`法定定义（第${h.priority_level}级）：${h.statutory_definition || '见法规定义条款'} · 法规依据：${h.source_reference || '-'}`"
                >
                  <el-tag size="small" type="success">
                    术语：{{ h.source_term }} → {{ h.target_zh }}
                  </el-tag>
                </el-tooltip>
                <el-tooltip
                  v-for="u in row.unmatched_terms"
                  :key="u.source_term"
                  placement="top"
                  :content="`暂译解释：${u.statutory_definition}（参考：${u.source_reference}）`"
                >
                  <el-tag size="small" type="warning">
                    原词保留：{{ u.source_term }}（{{ u.provisional_zh }}）
                  </el-tag>
                </el-tooltip>
              </div>
            </template>
          </el-table-column>
          <el-table-column label="状态" width="150">
            <template #default="{ row }">
              <el-tooltip
                placement="left"
                :disabled="!row.quality_issues?.length"
                :content="formatQualityIssues(row.quality_issues)"
              >
                <el-tag :type="plainStatus(row).type" size="small">
                  {{ plainStatus(row).label }}
                </el-tag>
              </el-tooltip>
              <div v-if="plainStatus(row).hint" class="sub" style="margin-top: 4px">
                {{ plainStatus(row).hint }}
              </div>
            </template>
          </el-table-column>
          <el-table-column label="操作" width="160" fixed="right"
            ><template #default="{ row }"
              ><el-button size="small" type="primary" link @click="openTranslationReview(row)"
                >查看译文</el-button
              ><el-dropdown trigger="click"
                ><el-button size="small" text>更多</el-button
                ><template #dropdown
                  ><el-dropdown-menu
                    ><el-dropdown-item @click="quickApproveTranslation(row)"
                      >标记为已确认</el-dropdown-item
                    ><el-dropdown-item @click="batchTranslateDoc(row)"
                      >补全本文档译文</el-dropdown-item
                    ><el-dropdown-item @click="showHistory(row.id)"
                      >修改记录</el-dropdown-item
                    ><el-dropdown-item v-if="row.source_context_revision" @click="showHistory(row.clause_id, 'translation_context_revision')"
                      >查看上下文变更记录</el-dropdown-item
                    ><el-dropdown-item v-if="row.source_parse_revision" @click="showHistory(row.clause_id, 'source_parse_revision')"
                      >查看原文分条变更记录</el-dropdown-item
                    ></el-dropdown-menu
                  ></template
                ></el-dropdown
              ></template
            ></el-table-column
          >
        </el-table>
        <div v-if="countryFilter !== 'CN' && (visibleTranslations.length > 0 || translationPage > 1)" class="tr-pager"><el-pagination class="cy-pager" :current-page="translationPage" :page-size="translationPageSize" :total="translationTotal || (hasNextTranslations ? (translationPage + 1) * translationPageSize : visibleTranslations.length)" layout="total, prev, pager, next" :disabled="loading" @current-change="(n: number) => turnTranslationPage(n - translationPage)" /></div>
      </el-tab-pane>

      <!-- 第二页签：解读要点确认单 -->
      <el-tab-pane label="法律术语" name="glossary">
        <TerminologyLibrary :country="countryFilter" />
        <h3 style="font-size: 16px; margin: 16px 0 8px">人工补充词条</h3>
        <div style="margin-bottom: 12px; display: flex; justify-content: space-between">
          <div class="sub">
            按四级优先级管理：第一级 法律自身定义条款 &gt; 第二级 监管指南 &gt; 第三级 官方多语版本 &gt; 第四级
            国际通行概念。本国覆盖自动优先于通用概念。
          </div>
          <el-button
            :type="newTermDialog ? 'default' : 'primary'"
            size="small"
            @click="newTermForm.country_code = countryFilter; newTermDialog = true"
          >
            新增术语词条
          </el-button>
        </div>

        <el-table :data="glossaryTerms" border stripe>
          <el-table-column label="适用范围" width="130">
            <template #default="{ row }">
              <el-tag :type="row.country_code ? 'primary' : 'info'" size="small">
                {{ row.country_code ? `${COUNTRY_NAMES[row.country_code] || row.country_code}专用` : '多国通用术语' }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="来源优先级" width="150">
            <template #default="{ row }">
              {{ PRIORITY_LABELS[row.priority_level] || `第${row.priority_level}级` }}
            </template>
          </el-table-column>
          <el-table-column prop="source_term" label="外文原词" width="210" />
          <el-table-column prop="target_zh" label="规定中文译法" width="170" />
          <el-table-column prop="statutory_definition" label="法定定义与说明" min-width="260" />
          <el-table-column prop="source_reference" label="依据法规" width="220" />
        </el-table>
      </el-tab-pane>
      </el-tabs>
    </template>

    <!-- 条款校订复核弹窗 -->
    <el-dialog v-model="editTranslationDialog" title="校订与复核法规条款译文" width="min(680px, 94vw)">
      <template v-if="currentTranslation">
        <div style="margin-bottom: 8px; font-weight: 600">
          {{ currentTranslation.document_title }} · 条号
          {{ currentTranslation.clause_number || '-' }}
        </div>
        <div class="clause-box" style="background: #f5f7fa; padding: 10px; margin-bottom: 12px">
          {{ currentTranslation.original_text }}
        </div>
        <el-form label-width="96px">
          <el-form-item label="中文审定译文">
            <el-input v-model="editTranslatedText" type="textarea" :rows="5" />
          </el-form-item>
          <el-form-item label="复核备注">
            <el-input v-model="editReviewerNote" placeholder="可填写核对说明或依据" />
          </el-form-item>
        </el-form>
      </template>
      <template #footer>
        <el-button @click="editTranslationDialog = false">取消</el-button>
        <el-button type="danger" plain @click="submitTranslationReview('rejected')">
          驳回重译
        </el-button>
        <el-button type="success" @click="submitTranslationReview('approved')">
          确认无误（可作依据法规引用）
        </el-button>
      </template>
    </el-dialog>

    <!-- 新增术语弹窗 -->
    <el-dialog v-model="newTermDialog" title="新增规范法律术语" width="min(560px, 94vw)">
      <el-form :model="newTermForm" label-width="110px">
        <el-form-item label="适用国家">
          <el-select
            v-model="newTermForm.country_code"
            clearable
            filterable
            style="width: 100%"
            placeholder="留空表示多国通用术语"
          >
            <el-option value="" label="多国通用术语（不限单一国家）" />
            <el-option
              v-for="c in countryOptions"
              :key="c.country_code"
              :value="c.country_code"
              :label="c.country_name_zh"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="语种">
          <el-select v-model="newTermForm.language" style="width: 100%">
            <el-option value="en" label="英语" />
            <el-option value="de" label="德语" />
            <el-option value="fr" label="法语" />
            <el-option value="ar" label="阿拉伯语" />
          </el-select>
        </el-form-item>
        <el-form-item label="外文原词">
          <el-input v-model="newTermForm.source_term" placeholder="填写法规原文中的术语名称" />
        </el-form-item>
        <el-form-item label="规定中文译法">
          <el-input v-model="newTermForm.target_zh" placeholder="填写统一规范的中文译名" />
        </el-form-item>
        <el-form-item label="来源优先级">
          <el-select v-model="newTermForm.priority_level" style="width: 100%">
            <el-option :value="1" label="第一级：法律自身定义条款（最高）" />
            <el-option :value="2" label="第二级：监管机构指南与问答" />
            <el-option :value="3" label="第三级：官方多语种版本" />
            <el-option :value="4" label="第四级：国际通行概念" />
          </el-select>
        </el-form-item>
        <el-form-item label="法定定义">
          <el-input v-model="newTermForm.statutory_definition" type="textarea" :rows="3" placeholder="可填写法规定义说明" />
        </el-form-item>
        <el-form-item label="依据法规出处">
          <el-input v-model="newTermForm.source_reference" placeholder="如：个人数据保护法第 2 条" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="newTermDialog = false">取消</el-button>
        <el-button type="primary" @click="createTerm">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.bar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 16px;
}
.sub {
  color: #606266;
  font-size: 13px;
}
.clause-box {
  display: -webkit-box;
  -webkit-line-clamp: 4;
  -webkit-box-orient: vertical;
  overflow: hidden;
  white-space: pre-wrap;
  font-size: 13px;
  line-height: 1.5;
}
.country-cards { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 16px; margin-top: 8px; }
.country-card { display: flex; justify-content: space-between; align-items: center; gap: 12px; padding: 16px 18px; border: 1px solid var(--cy-line); border-radius: 10px; background: #fff; font: inherit; font-size: 15px; cursor: pointer; text-align: left; }
.country-card > span { color: var(--cy-text-3); font-size: 13px; white-space: nowrap; flex-shrink: 0; }
.country-card :deep(.el-tag) { white-space: nowrap; flex-shrink: 0; }
.country-card strong { display: flex; flex-direction: column; align-items: flex-start; gap: 4px; font-size: 16px; text-align: left; min-width: 0; }
.country-card strong small { font-weight: 400; font-size: 13px; color: var(--cy-text-2); }
.country-card:hover { border-color: var(--el-color-primary-light-5); color: var(--el-color-primary); }
.tr-pager { display: flex; align-items: center; justify-content: flex-end; gap: 16px; margin: 8px 0 24px; }
.tr-pager .cy-pager { margin: 0; }
.clause-box { text-overflow: ellipsis; }
:deep(.el-table .cell) { overflow: hidden; }
</style>
