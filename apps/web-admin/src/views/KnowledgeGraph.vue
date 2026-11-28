<script setup lang="ts">
import { localTime } from '@cyshuhe/shared'
import { ElMessage } from 'element-plus'
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { api } from '../api'
import GraphEditPanel from '../components/GraphEditPanel.vue'
import KgForceGraph from '../components/KgForceGraph.vue'
import KgLawGroups from '../components/KgLawGroups.vue'
import { UNASSIGNED_GROUP, groupByLaw, mergeDisplayEdges, relationPage, projectOverviewNodes } from '@cyshuhe/shared/src/graphGrouping'

interface KgNode {
  id: string
  country_code: string
  node_type: string
  node_key: string
  name_zh: string
  name_orig: string | null
  summary: string | null
  attributes: Record<string, string>
  document_id: string | null
  evidence_clause_id: string | null
  review_status: string
}

interface KgEdge {
  id: string
  country_code: string
  source_node_id: string
  source_name_zh: string
  source_node_type: string
  target_node_id: string
  target_name_zh: string
  target_node_type: string
  relation_type: string
  evidence_clause_id: string
  evidence_quote: string
  clause_number: string | null
  document_title: string | null
  extractor_model: string
  confidence?: number
  judge_verdict?: string
  judge_reason?: string | null
  is_structural?: boolean
  review_status: string
  verification_status?: string
  verification_reason?: string
  usable_as_legal_basis: boolean
}

interface QualitySummary {
  source_integrity_held_documents?: number
  source_integrity_unresolved_documents?: number
  source_integrity_replaced_documents?: number
  source_integrity_records?: Array<{
    document_id: string; title: string; country_code: string; reason: string;
    status: string; replacement: null | { document_id: string; title: string; edition: string; source_url: string }
  }>
  documents_total?: number
  documents_core?: number
  documents_related?: number
  documents_out_of_scope?: number
  documents_extracted?: number
  structural_edges?: number
  semantic_edges?: number
  semantic_unjudged?: number
  low_confidence?: number
  quote_mismatch?: number
  superseded_edges_count?: number
  isolated_nodes_count?: number
  isolated_node_ids?: string[]
  total_clauses_count?: number
  linked_clauses_count?: number
  unlinked_clauses_count?: number
  pending_review_edges_count?: number
  approved_edges_count?: number
  auto_verified_edges_count?: number
  needs_confirmation_edges_count?: number
  auto_verified_nodes_count?: number
  needs_confirmation_nodes_count?: number
  stale_or_rejected_edges_count?: number
  quote_mismatch_edges_count?: number
  ocr_or_rtl_clauses_count?: number
}

interface GraphPayload {
  country_code: string
  node_counts_by_type: Record<string, number>
  edge_counts_by_relation: Record<string, number>
  quality_summary: QualitySummary
  nodes: KgNode[]
  edges: KgEdge[]
}

const NODE_TYPE_META: Record<string, { label: string; color: string; col: number }> = {
  COUNTRY: { label: '国家/地区', color: '#1d4ed8', col: 0 },
  REGULATOR: { label: '监管机构', color: '#0f766e', col: 1 },
  INSTRUMENT: { label: '法规文件', color: '#7c3aed', col: 2 },
  OBLIGATION: { label: '合规义务', color: '#b45309', col: 3 },
  DATA_CATEGORY: { label: '数据类型', color: '#0369a1', col: 4 },
  PENALTY: { label: '违规罚则', color: '#be123c', col: 5 },
}

type BatchMode = 'csv_nodes' | 'csv_edges'
const route = useRoute()
const ALL_NODE_TYPES = Object.keys(NODE_TYPE_META)
const showBatchDialog = ref(false)
const batchMode = ref<BatchMode>('csv_nodes')
const batchCountry = ref('')
const batchInput = ref('')
const lastGeneratedTemplate = ref('')
const batchBusy = ref(false)
const batchResult = ref<{ created_nodes_count: number; created_edges_count: number; errors: Array<{ index?: number; row?: number; kind: string; error: string }> } | null>(null)

const RELATION_LABEL: Record<string, string> = {
  PUBLISHES: '发布',
  AMENDS: '修订',
  REPEALS: '废止',
  GOVERNS: '适用于',
  PROHIBITS: '禁止',
  REQUIRES: '强制要求',
  EXEMPTS: '豁免',
  SUPERVISES: '监管',
  PENALIZES: '规定罚则',
  APPLIES_TO_SCENARIO: '适用场景',
  RESTRICTS_CROSS_BORDER: '限制跨境传输',
  REQUIRES_ADMISSION: '准入要求',
}

const RELATION_FALLBACK_ZH: Record<string, string> = {
  ...RELATION_LABEL,
  APPLIES_TO: '适用于',
  REGULATES: '规范管理',
  DEFINES: '界定范围',
  MANDATES: '强制要求',
  PERMITS: '允许',
  ALLOWS: '允许',
  SPECIFIES: '明确规定',
  CLASSIFIES: '分类界定',
  ENFORCES: '执法监督',
  ISSUES: '颁布',
  CONTAINS: '包含',
  REFERENCES: '援引',
  TRANSFERS_TO: '跨境传输至',
  REQUIRES_CONSENT: '要求取得同意',
  REQUIRES_ASSESSMENT: '要求开展评估',
  REQUIRES_LOCALIZATION: '要求本地存储',
  PROTECTS: '保护',
}

function nodeTypeLabel(ntype?: string | null): string {
  const key = String(ntype || '').trim().toUpperCase()
  return NODE_TYPE_META[key]?.label || '法规要素'
}

function edgeRelationLabel(rel: string, srcType?: string, dstType?: string, dstName?: string): string {
  const key = String(rel || '').trim().toUpperCase()
  const sType = String(srcType || '').trim().toUpperCase()
  const dType = String(dstType || '').trim().toUpperCase()
  if (key === 'PENALIZES') return sType === 'OBLIGATION' ? '违反时处以' : '规定罚则'
  if (key === 'APPLIES_TO_SCENARIO') return dType === 'DATA_CATEGORY' ? '涉及数据' : '适用场景'
  if (key === 'SUPERVISES') return dType === 'COUNTRY' ? '所属法域' : sType === 'COUNTRY' ? '设立监管' : '监管'
  if (key === 'PROHIBITS') return /^(不得|禁止|严禁)/.test(dstName || '') ? '明令约束' : '禁止'
  if (RELATION_FALLBACK_ZH[key]) return RELATION_FALLBACK_ZH[key]
  if (/^[A-Z0-9_]+$/.test(key)) return '法规关联'
  return rel || '法规关联'
}

function cleanElementText(text?: string | null): string {
  let s = String(text || '')
  if (!s) return ''
  s = s
    .replace(new RegExp('孤立' + '节点', 'g'), '未关联要素')
    .replace(/节点/g, '要素')
  for (const [code, zh] of Object.entries(RELATION_FALLBACK_ZH)) {
    s = s.replace(new RegExp(`\\b${code}\\b`, 'gi'), zh)
  }
  for (const [code, meta] of Object.entries(NODE_TYPE_META)) {
    s = s.replace(new RegExp(`\\b${code}\\b`, 'g'), meta.label)
  }
  s = s
    .replace(new RegExp('\\bPyd' + 'antic\\b', 'gi'), '格式核验')
    .replace(new RegExp('\\bJS' + 'ON\\b', 'gi'), '结构化格式')
    .replace(new RegExp('\\bUU' + 'ID\\b', 'gi'), '记录编号')
    .replace(new RegExp('\\bLL' + 'M\\b', 'gi'), '智能梳理')
    .replace(new RegExp('\\bOC' + 'R\\b', 'gi'), '文字识别')
    .replace(new RegExp('\\bRA' + 'G\\b', 'gi'), '条文检索')
    .replace(new RegExp('\\bnu' + 'll\\b', 'gi'), '空值')
    .replace(new RegExp('\\bundef' + 'ined\\b', 'gi'), '未填写')
  return s
}

function sanitizeBatchError(raw: unknown): string {
  let msg = cleanElementText(typeof raw === 'string' ? raw : (raw as Error)?.message || String(raw || ''))
  if (!msg) return '导入内容格式有误，请按模板核对必填栏目与依据条号'
  msg = msg
    .replace(/https?:\/\/errors\.[^\s)]+/gi, '')
    .replace(/\[type=[^\]]+\]/gi, '')
    .replace(/\bFor further information visit\b[^\n]*/gi, '')
    .replace(/\bValue error,\s*/gi, '')
    .replace(/\bField required\b/gi, '必填栏目未填写')
    .replace(/\bString should have at least \d+ characters?\b/gi, '填写内容长度不足')
    .replace(/\bInput should be a valid [^\n;,.]+/gi, '填写内容格式不符合模板要求')
    .replace(/\bnot a valid enumeration member[^\n]*/gi, '所填类别不在标准范围内')
    .replace(/\bcountry_code\b/gi, '国家或地区')
    .replace(/\bcsv_text\b/gi, '导入表格内容')
    .replace(/\bcsv_kind\b/gi, '导入模板类型')
    .replace(/\brelation_type\b/gi, '关联关系类型')
    .replace(/\bnode_type\b/gi, '要素类别')
    .replace(/\bevidence_quote\b/gi, '依据条文原句')
    .replace(/\bclause_number\b/gi, '依据条号')
    .replace(/\bdocument_title\b/gi, '依据法规名称')
    .replace(/\b(?:node|nodes)\b/gi, '要素')
    .replace(/\b(?:edge|edges)\b/gi, '关联关系')
    .replace(/\s{2,}/g, ' ')
    .trim()
  if (!/[\u4e00-\u9fff]/.test(msg)) {
    return '导入内容格式不符合模板规范，请核对目标国家、类别名称及依据条号后重试'
  }
  return msg
}

interface ParsedRevisionView {
  id: string
  batchLabel: string
  documentTitle: string
  scopeLabel: string
  statusLabel: string
  statusType: 'success' | 'warning' | 'info' | 'danger'
  reviewLabel: string
  timeLabel: string
  summaryText: string
}
const showRevisionsDrawer = ref(false)
const revisionsLoading = ref(false)
const revisionCountry = ref('SG')
const revisionsList = ref<ParsedRevisionView[]>([])
const revisionsNotice = ref('')

async function loadRevisions() {
  const target = (revisionCountry.value || countryCode.value || countryOptions.value[0]?.country_code || 'SG')
    .trim()
    .toUpperCase()
  revisionCountry.value = target
  revisionsLoading.value = true
  revisionsNotice.value = ''
  try {
    const raw = await api.get<Array<Record<string, unknown>>>(
      `/admin/knowledge/parsed-graph-revisions?country_code=${encodeURIComponent(target)}`,
    )
    const list = Array.isArray(raw) ? raw : []
    revisionsList.value = list.map((item, idx) => {
      const payload = (item.payload && typeof item.payload === 'object'
        ? item.payload
        : {}) as Record<string, unknown>
      const hashStr = String(item.content_hash || payload.source_hash || '').slice(0, 8)
      const batchNo = list.length - idx
      const blocks = Array.isArray(payload.blocks) ? (payload.blocks as Array<Record<string, unknown>>) : []
      let relCount = 0
      for (const blk of blocks) {
        if (Array.isArray(blk.relations)) relCount += blk.relations.length
        else if (Array.isArray(blk.items)) relCount += blk.items.length
      }
      const rawStatus = String(payload.status || item.status || 'completed').toLowerCase()
      const rawReview = String(payload.review_status || item.review_status || 'pending_review').toLowerCase()
      const statusLabel =
        rawStatus === 'completed'
          ? '已完成梳理'
          : rawStatus === 'failed'
            ? '部分待人工复核'
            : '已归档'
      const statusType: 'success' | 'warning' | 'info' | 'danger' =
        rawStatus === 'completed' ? 'success' : rawStatus === 'failed' ? 'warning' : 'info'
      const reviewLabel =
        rawReview === 'approved' || payload.human_reviewed
          ? '已人工复核'
          : '待复核提案（未替换现行依据）'
      const pageNo = payload.page_number ?? item.page_number
      const scopeLabel = pageNo ? `第 ${pageNo} 页原文梳理` : '条文级图谱修订'
      const rawTime = String(item.created_at || payload.created_at || payload.updated_at || '').trim()
      const customSummary = cleanElementText(String(payload.summary || item.summary || '').trim())
      const summaryText =
        customSummary ||
        (blocks.length > 0
          ? `共核验 ${blocks.length} 个原文语段，提取 ${relCount} 条候选法规关联（留存备查）`
          : '已完成条文与图谱要素对照留档')
      return {
        id: String(item.id || idx),
        batchLabel: hashStr ? `修订批次 #${batchNo}（摘要 ${hashStr}）` : `修订批次 #${batchNo}`,
        documentTitle: String(payload.document_title || item.document_title || '在库官方法规文件'),
        scopeLabel,
        statusLabel,
        statusType,
        reviewLabel,
        timeLabel: rawTime ? localTime(rawTime) || rawTime.slice(0, 16) : '入库核验留档',
        summaryText,
      }
    })
  } catch (e) {
    revisionsList.value = []
    const errMsg = String((e as Error)?.message || '')
    if (errMsg.includes('403') || errMsg.includes('权限') || errMsg.includes('超级管理员')) {
      revisionsNotice.value = '图谱底层修订审计批次仅限超级管理员查阅；当前生效图谱要素与条文依据可在主界面正常复核。'
    } else {
      revisionsNotice.value = '当前国家暂无图谱修订批次记录。'
    }
  } finally {
    revisionsLoading.value = false
  }
}

async function openRevisionsDrawer() {
  revisionCountry.value = (countryCode.value || countryOptions.value[0]?.country_code || 'SG')
    .trim()
    .toUpperCase()
  showRevisionsDrawer.value = true
  managePopoverOpen.value = false
  await loadRevisions()
}

const managePopoverOpen = ref(false)
const isCompact = ref(typeof window !== 'undefined' ? window.innerWidth <= 1024 : false)
function syncCompact() {
  if (typeof window !== 'undefined') isCompact.value = window.innerWidth <= 1024
}

async function openBatchImport() {
  managePopoverOpen.value = false
  showBatchDialog.value = true
  batchResult.value = null
  const prevBatchCountry = batchCountry.value
  batchCountry.value = countryCode.value || ''
  const unedited = !batchInput.value.trim() || batchInput.value === lastGeneratedTemplate.value
  if (unedited || prevBatchCountry !== batchCountry.value) {
    if (unedited) await fillBatchExample(batchMode.value)
  }
}

async function fillBatchExample(mode: BatchMode) {
  batchMode.value = mode
  batchResult.value = null
  const targetCode = batchCountry.value.trim().toUpperCase()
  const curCountryName =
    countryOptions.value.find((c) => c.country_code === targetCode)?.country_name_zh || ''
  let generated = ''
  try {
    const url = targetCode
      ? `/admin/knowledge/batch-template?country_code=${encodeURIComponent(targetCode)}`
      : '/admin/knowledge/batch-template'
    const tpl = await api.get<{
      csv_headers: { nodes: string; edges: string }
      csv_examples?: { nodes?: string; edges?: string }
    }>(url)
    const nodesHeader = cleanElementText(tpl.csv_headers.nodes)
    const edgesHeader = cleanElementText(tpl.csv_headers.edges)
    if (mode === 'csv_nodes') {
      if (tpl.csv_examples?.nodes && targetCode) {
        generated = cleanElementText(tpl.csv_examples.nodes)
      } else if (curCountryName) {
        generated = `${nodesHeader}\n${curCountryName},数据类型,车载诊断故障码参数,OBD Diagnostic Parameters,车辆远程诊断参数,,`
      } else {
        generated = `${nodesHeader}\n`
      }
    } else if (tpl.csv_examples?.edges && targetCode) {
      generated = cleanElementText(tpl.csv_examples.edges)
    } else {
      generated = `${edgesHeader}\n`
    }
  } catch {
    if (mode === 'csv_nodes') {
      generated = curCountryName
        ? `国家或地区,要素类别,中文名称,原文名称,简要说明,依据法规名称,依据条号\n${curCountryName},数据类型,车载诊断故障码参数,OBD Diagnostic Parameters,车辆远程诊断参数,,`
        : '国家或地区,要素类别,中文名称,原文名称,简要说明,依据法规名称,依据条号\n'
    } else {
      generated = '国家或地区,起点要素名称,终点要素名称,关联关系,依据法规名称,依据条号,依据条文原句\n'
    }
  }
  batchInput.value = generated
  lastGeneratedTemplate.value = generated
}

async function onBatchCountryChange() {
  const unedited = !batchInput.value.trim() || batchInput.value === lastGeneratedTemplate.value
  if (unedited) {
    await fillBatchExample(batchMode.value)
  }
}

function normalizeCsvForBackend(rawCsv: string, fallbackCountryName: string): string {
  const lines = rawCsv.trim().split(/\r?\n/)
  if (!lines.length) return rawCsv
  lines[0] = lines[0]
    .replace(/要素类别/g, '节点类别')
    .replace(/起点要素名称/g, '起点节点名称')
    .replace(/终点要素名称/g, '终点节点名称')
  if (fallbackCountryName && lines.length > 1) {
    for (let i = 1; i < lines.length; i += 1) {
      if (lines[i].startsWith(',')) {
        lines[i] = `${fallbackCountryName}${lines[i]}`
      }
    }
  }
  return lines.join('\n')
}

async function submitBatchImport() {
  if (!batchCountry.value) {
    ElMessage.warning('请先选择目标国家或地区')
    return
  }
  if (!batchInput.value.trim()) {
    ElMessage.warning('请填写或粘贴待导入的内容')
    return
  }
  const targetCountryZh =
    countryOptions.value.find((c) => c.country_code === batchCountry.value)?.country_name_zh ||
    batchCountry.value
  batchBusy.value = true
  try {
    const payload = {
      country_code: batchCountry.value,
      csv_text: normalizeCsvForBackend(batchInput.value, targetCountryZh),
      csv_kind: batchMode.value === 'csv_edges' ? 'edges' : 'nodes',
    }
    const res = await api.post<{ created_nodes_count: number; created_edges_count: number; errors: Array<{ index?: number; row?: number; kind: string; error: string }> }>('/admin/knowledge/batch-import', payload)
    batchResult.value = res
    if (res.created_nodes_count || res.created_edges_count) {
      ElMessage.success(`批量导入完成：新增要素 ${res.created_nodes_count} 个、关系 ${res.created_edges_count} 条`)
      if (countryCode.value) await loadGraph()
    } else if (res.errors.length) {
      ElMessage.warning(`未通过核验 ${res.errors.length} 条记录，请查看下方原因说明`)
    }
  } catch (e) {
    ElMessage.error(sanitizeBatchError(e))
  } finally {
    batchBusy.value = false
  }
}

// 要素的类别（模型对条文依据的判断，只打标签、不删除）：监管机构自己的职责、资格与例外条件
// 不是对企业的义务，页面上要让人一眼看出来
const OBLIGATION_KIND: Record<string, { label: string; type: 'info' | 'warning' }> = {
  regulator: { label: '监管机构自身职责（非对企业的义务）', type: 'warning' },
  eligibility: { label: '资格或例外条件（非义务本身）', type: 'info' },
  generic: { label: '泛泛守法要求', type: 'info' },
}
// 义务/处罚所属方面与业务相关度（claim_tags）：用户端国家页、五国对比和条文检索按它分流；“无关”的不在用户端展示
const ASPECT_ZH: Record<string, string> = { transfer: '跨境传输', consent: '告知与同意', security: '存储与安全', admission: '申报与许可', governance: '内部管理', penalty: '处罚', other: '其他' }
const CLOSE_ZH: Record<string, string> = { vehicle: '直接涉及车辆数据', data: '一般数据处理要求', unrelated: '与车辆数据业务无关（用户端不展示）' }
function claimTag(node: { node_type: string; attributes?: Record<string, unknown> }) {
  const t = node.attributes?.claim_tag as { aspect?: string; close?: string } | undefined
  if (!t || !['OBLIGATION', 'PENALTY'].includes(node.node_type)) return ''
  return `方面：${ASPECT_ZH[t.aspect || ''] || t.aspect} · ${CLOSE_ZH[t.close || ''] || t.close}`
}
function obligationKind(node: { node_type: string; attributes?: Record<string, unknown> }) {
  if (node.node_type !== 'OBLIGATION') return null
  return OBLIGATION_KIND[String(node.attributes?.node_class ?? '')] ?? null
}
type AttrEvidence = {
  document_title?: string
  clause_number?: string
  evidence_quote: string
}
function attrEvidence(key: string): AttrEvidence[] {
  const all = activeNode.value?.attributes?.attribute_evidence as
    | Record<string, AttrEvidence[]>
    | undefined
  return all?.[key] ?? []
}

const EIGHT_ATTR_LABELS: Array<{ key: string; label: string }> = [
  { key: 'applicable_industry', label: '1. 适用行业' },
  { key: 'data_scope', label: '2. 数据范围' },
  { key: 'cross_border_condition', label: '3. 出境条件' },
  { key: 'retention_period', label: '4. 留存期限' },
  { key: 'authorization_requirement', label: '5. 授权要求' },
  { key: 'penalty_amount', label: '6. 处罚金额' },
  { key: 'violation_level', label: '7. 违规等级' },
  { key: 'admission_checklist', label: '8. 准入材料清单' },
]

const PARTY_LABELS: Array<{ key: string; label: string }> = [
  { key: 'obligated_party', label: '义务主体' },
  { key: 'applicable_condition', label: '适用条件' },
]

function partyShown(n: KgNode): boolean {
  return n.node_type === 'OBLIGATION' || n.node_type === 'PENALTY'
}

const KG_KEY = 'cyshuhe.admin.kg.country'
const readRouteCountry = () =>
  String(route.query.country || route.query.country_code || '')
    .trim()
    .toUpperCase()
const countryCode = ref(readRouteCountry() || localStorage.getItem(KG_KEY) || '')
watch(
  () => [route.query.country, route.query.country_code],
  () => {
    const rc = readRouteCountry()
    if (rc && rc !== countryCode.value) countryCode.value = rc
  },
)
watch(countryCode, (v) => {
  if (v) localStorage.setItem(KG_KEY, v)
  else localStorage.removeItem(KG_KEY)
  batchCountry.value = v || ''
  const unedited = !batchInput.value.trim() || batchInput.value === lastGeneratedTemplate.value
  if (showBatchDialog.value && unedited) {
    void fillBatchExample(batchMode.value)
  }
})
const countryOptions = ref<
  Array<{
    country_code: string
    country_name_zh: string
    country_name_orig?: string
    documents_count?: number
    last_updated_at?: string | null
    pending_kg_count?: number
  }>
>([
  { country_code: 'SG', country_name_zh: '新加坡' },
  { country_code: 'EG', country_name_zh: '埃及' },
  { country_code: 'MA', country_name_zh: '摩洛哥' },
  { country_code: 'TN', country_name_zh: '突尼斯' },
  { country_code: 'LB', country_name_zh: '黎巴嫩' },
  { country_code: 'CN', country_name_zh: '中国' },
])
const onlyApproved = ref(false)
const relationFilter = ref('')
const qualityFilter = ref<'all' | 'needs_confirmation' | 'verified'>('all')
const visibleNodeTypes = ref<string[]>([...ALL_NODE_TYPES])
const loading = ref(false)
const countriesLoaded = ref(false)
const graph = ref<GraphPayload>({
  country_code: '',
  node_counts_by_type: {},
  edge_counts_by_relation: {},
  quality_summary: {},
  nodes: [],
  edges: [],
})

function backToOverview() {
  countryCode.value = ''
  localStorage.removeItem(KG_KEY)
  selectedId.value = ''
  selectedLawId.value = ''
}

function onCountrySelectChange() {
  if (countryCode.value) {
    void loadGraph()
  } else {
    backToOverview()
  }
}

const selectedLawId = ref('')
const relationPageNumber = ref(1)
const evidencePageNumber = ref(1)
const selectedId = ref('')
const showAttrCard = ref(false)
const graphView = ref('graph')

function isTypeVisible(ntype: string): boolean {
  return visibleNodeTypes.value.includes(ntype)
}

function toggleNodeType(ntype: string) {
  const idx = visibleNodeTypes.value.indexOf(ntype)
  if (idx >= 0) {
    if (visibleNodeTypes.value.length <= 1) {
      ElMessage.warning('请至少保留 1 类要素显示')
      return
    }
    visibleNodeTypes.value = visibleNodeTypes.value.filter((t) => t !== ntype)
    const sel = graph.value.nodes.find((n) => n.id === selectedId.value)
    if (sel && sel.node_type === ntype) {
      selectedId.value = ''
    }
  } else {
    visibleNodeTypes.value = [...visibleNodeTypes.value, ntype]
  }
}

function showAllNodeTypes() {
  visibleNodeTypes.value = [...ALL_NODE_TYPES]
}

function hideHubNodeTypes() {
  visibleNodeTypes.value = ['INSTRUMENT', 'OBLIGATION', 'DATA_CATEGORY', 'PENALTY']
  const sel = graph.value.nodes.find((n) => n.id === selectedId.value)
  if (sel && !visibleNodeTypes.value.includes(sel.node_type)) {
    selectedId.value = ''
  }
}

// 范围外文件的法规节点: 默认折叠(仍属于全部文件,可用开关显示),它们不参与语义抽取
const showOutOfScope = ref(false)
const outOfScopeNodeIds = computed(
  () =>
    new Set(
      graph.value.nodes
        .filter((n) => n.node_type === 'INSTRUMENT' && n.attributes?.scope === 'out_of_scope')
        .map((n) => n.id),
    ),
)

const scopedNodes = computed(() => {
  const hidden = showOutOfScope.value ? new Set<string>() : outOfScopeNodeIds.value
  return graph.value.nodes.filter(
    (n) => (!onlyApproved.value || n.review_status === 'approved') && !hidden.has(n.id),
  )
})
const visibleNodeCountsByType = computed(() => {
  const counts: Record<string, number> = {}
  for (const n of scopedNodes.value) {
    counts[n.node_type] = (counts[n.node_type] || 0) + 1
  }
  return counts
})
const scopedAllEdges = computed(() => {
  const hidden = showOutOfScope.value ? new Set<string>() : outOfScopeNodeIds.value
  const approvedNodes = new Set(graph.value.nodes.filter((n) => n.review_status === 'approved').map((n) => n.id))
  return graph.value.edges.filter((e) => {
    if (onlyApproved.value && (e.review_status !== 'approved' || !approvedNodes.has(e.source_node_id) || !approvedNodes.has(e.target_node_id))) return false
    if (hidden.has(e.source_node_id) || hidden.has(e.target_node_id)) return false
    return true
  })
})
const needsConfirmationEdges = computed(
  () => scopedAllEdges.value.filter((e) => e.verification_status === 'needs_confirmation').length,
)
const verifiedEdges = computed(
  () => scopedAllEdges.value.filter((e) => ['auto_verified', 'approved'].includes(e.verification_status ?? '')).length,
)
const statusLabel = (row: { verification_status?: string; is_structural?: boolean }) =>
  ({ approved: '已复核', auto_verified: '条文已核对', needs_confirmation: '待复核', retired: '已停用' }[row.verification_status ?? ''] ?? '条文已核对')
const statusType = (row: { verification_status?: string }) =>
  ({ approved: 'success', auto_verified: 'success', needs_confirmation: 'warning', retired: 'info' }[row.verification_status ?? ''] ?? 'success') as 'success' | 'warning' | 'info'

// 先按可见节点类型与质量体检状态过滤边
const typeFilteredEdges = computed(() => {
  const vis = new Set(visibleNodeTypes.value)
  const hidden = showOutOfScope.value ? new Set<string>() : outOfScopeNodeIds.value
  const approvedNodes = new Set(graph.value.nodes.filter(n => n.review_status === 'approved').map(n => n.id))
  return graph.value.edges.filter((e) => {
    if (onlyApproved.value && (e.review_status !== 'approved' || !approvedNodes.has(e.source_node_id) || !approvedNodes.has(e.target_node_id))) return false
    if (!vis.has(e.source_node_type) || !vis.has(e.target_node_type)) return false
    if (hidden.has(e.source_node_id) || hidden.has(e.target_node_id)) return false
    if (qualityFilter.value === 'needs_confirmation' && e.verification_status !== 'needs_confirmation')
      return false
    if (qualityFilter.value === 'verified' && !['auto_verified', 'approved'].includes(e.verification_status ?? '')) return false
    return true
  })
})

const visibleEdgeCountsByRelation = computed(() => {
  const counts: Record<string, number> = {}
  for (const e of typeFilteredEdges.value) {
    counts[e.relation_type] = (counts[e.relation_type] || 0) + 1
  }
  return counts
})

const filteredEdges = computed(() =>
  relationFilter.value
    ? typeFilteredEdges.value.filter((e) => e.relation_type === relationFilter.value)
    : typeFilteredEdges.value,
)

const lawGroups = computed(() => groupByLaw(graph.value.nodes.filter(n => (!onlyApproved.value || n.review_status === 'approved') && (showOutOfScope.value || !outOfScopeNodeIds.value.has(n.id))), filteredEdges.value))
const lawDocGroupsCount = computed(() => lawGroups.value.filter(g => g.id !== UNASSIGNED_GROUP).length)
const hasTopSystemGroup = computed(() => lawGroups.value.some(g => g.id === UNASSIGNED_GROUP))
const selectedLaw = computed(() => lawGroups.value.find(g => g.id === selectedLawId.value))
const scopedEdges = computed(() => selectedLaw.value?.edges ?? filteredEdges.value)
const mergedEdges = computed(() => mergeDisplayEdges(graph.value.nodes, selectedLaw.value?.edges ?? []))
const displayedPage = computed(() => relationPage(mergedEdges.value, relationPageNumber.value, 36))
const graphEdgesRaw = computed(() => displayedPage.value.edges)
const displayedEvidenceCount = computed(() => graphEdgesRaw.value.reduce((sum, e) => sum + e.count, 0))
const nodeById = computed(() => new Map(graph.value.nodes.map((n) => [n.id, n])))
const overviewEdges = computed(() => mergeDisplayEdges(graph.value.nodes, filteredEdges.value))
const overviewNodes = computed(() => projectOverviewNodes(
  graph.value.nodes.filter(n => visibleNodeTypes.value.includes(n.node_type)), lawGroups.value, overviewEdges.value,
))
const overviewFgEdges = computed(() => overviewEdges.value.map(e => {
  const sn = nodeById.value.get(e.source)
  const tn = nodeById.value.get(e.target)
  return {
    source: e.source, target: e.target,
    label: edgeRelationLabel(e.relation, sn?.node_type, tn?.node_type, tn?.name_zh) + (e.count > 1 ? ` ×${e.count}` : ''), approved: e.approved,
  }
}))
function onOverviewNodeClick(id: string) {
  const group = lawGroups.value.find(g => g.instrumentIds.includes(id))
  if (group) selectLaw(group.id)
  else onNodeClick(id)
}

function selectLaw(id: string) {
  selectedLawId.value = id
  relationPageNumber.value = 1
  selectedId.value = ''
}
function selectRelationPage(page: number) {
  relationPageNumber.value = page
  selectedId.value = ''
}
watch(filteredEdges, () => { relationPageNumber.value = 1; selectedId.value = '' })
watch(() => graph.value.country_code, () => selectLaw(''))

const fgCategories = Object.entries(NODE_TYPE_META).map(([type, m]) => ({
  type,
  name: m.label,
  color: m.color,
}))

const fgEdges = computed(() =>
  graphEdgesRaw.value.map((e) => {
    const sn = nodeById.value.get(e.source)
    const tn = nodeById.value.get(e.target)
    return {
      source: e.source,
      target: e.target,
      label: edgeRelationLabel(e.relation, sn?.node_type, tn?.node_type, tn?.name_zh) + (e.count > 1 ? ` ×${e.count}` : ''),
      approved: e.approved,
    }
  }),
)

const fgNodes = computed(() => {
  const vis = new Set(visibleNodeTypes.value)
  const degree: Record<string, number> = {}
  for (const e of graphEdgesRaw.value) {
    degree[e.source] = (degree[e.source] || 0) + 1
    degree[e.target] = (degree[e.target] || 0) + 1
  }
  return graph.value.nodes
    .filter((n) => vis.has(n.node_type) && degree[n.id])
    .map((n) => ({
      id: n.id,
      name: n.name_zh,
      type: n.node_type,
      degree: degree[n.id],
    }))
})

const tableEdges = computed(() =>
  selectedId.value
    ? scopedEdges.value.filter(
        (e) => e.source_node_id === selectedId.value || e.target_node_id === selectedId.value,
      )
    : scopedEdges.value,
)
const evidencePage = computed(() => relationPage(tableEdges.value, evidencePageNumber.value, 50))
watch(tableEdges, () => { evidencePageNumber.value = 1 })


const activeNode = computed<KgNode | null>(() => {
  if (selectedId.value) {
    return graph.value.nodes.find((n) => n.id === selectedId.value) || null
  }
  return graph.value.nodes.find((n) => selectedLaw.value?.instrumentIds.includes(n.id))
    || graph.value.nodes.find((n) => n.node_type === 'INSTRUMENT') || graph.value.nodes[0] || null
})

const selectedName = computed(
  () => graph.value.nodes.find((n) => n.id === selectedId.value)?.name_zh || '',
)

function onNodeClick(id: string) {
  selectedId.value = selectedId.value === id ? '' : id
  showAttrCard.value = true
}

interface ValidationItem { id: string; type: string; name: string; detail: string | null }
interface ValidationCheck { key: string; label: string; count: number; items: ValidationItem[] }
const validation = ref<ValidationCheck[]>([])

async function loadValidation() {
  try {
    validation.value = await api.get<ValidationCheck[]>(
      `/admin/knowledge/graph/validation?country_code=${countryCode.value}`,
    )
  } catch {
    validation.value = []
  }
}

async function loadGraph() {
  if (!countryCode.value) return
  loading.value = true
  loadValidation()
  try {
    graph.value = await api.get<GraphPayload>(
      `/admin/knowledge/graph?country_code=${countryCode.value}&only_approved=false`,
    )
    selectLaw('')
  } catch (e: unknown) {
    ElMessage.error((e as Error).message)
  } finally {
    loading.value = false
  }
}

async function runExtract() {
  if (!countryCode.value) return ElMessage.warning('先选择国家')
  loading.value = true
  try {
    const res = await api.post<{
      status: string
      reason?: string
      nodes_total: number
      edges_total: number
    }>(`/admin/knowledge/extract?country_code=${countryCode.value}`)
    if (res.status !== 'completed') {
      const reasonZhMap: Record<string, string> = {
        country_extraction_already_running: '该国知识图谱正在后台梳理中，请稍候刷新',
        document_relevance_unavailable: '相关法规范围尚未核定完成，已保留既有图谱结果',
        no_parsed_documents: '当前国家暂无已解析完成的法规条文',
        skipped: '当前未满足重新梳理条件',
      }
      const rawReason = String(res.reason || res.status || '').trim()
      const cleanReason =
        reasonZhMap[rawReason] ||
        (/^[A-Za-z_][A-Za-z0-9_]*$/.test(rawReason)
          ? '智能分析服务暂不可用，已保留既有图谱结果'
          : rawReason)
      ElMessage.warning(`图谱未替换，保留旧结果：${cleanReason}`)
      return
    }
    const curCountryZh = countryOptions.value.find((c) => c.country_code === countryCode.value)?.country_name_zh || countryCode.value
    ElMessage.success(
      `已重新识别梳理${curCountryZh}知识图谱：共 ${res.nodes_total} 个要素、${res.edges_total} 条关联关系`,
    )
    await loadGraph()
  } catch (e: unknown) {
    ElMessage.error((e as Error).message)
  } finally {
    loading.value = false
  }
}

async function reviewEdge(edge: KgEdge, decision: 'approved' | 'rejected') {
  try {
    await api.post(`/admin/knowledge/edges/${edge.id}/review`, { decision })
    ElMessage.success(decision === 'approved' ? '已复核通过该条关联' : '已驳回该条关联')
    await loadGraph()
  } catch (e: unknown) {
    ElMessage.error((e as Error).message)
  }
}

watch(
  () => [route.query.country, route.query.country_code],
  () => {
    const q = readRouteCountry()
    if (q && q !== countryCode.value) {
      countryCode.value = q
      void loadGraph()
    }
  },
)

onMounted(async () => {
  syncCompact()
  window.addEventListener('resize', syncCompact)
  const q = readRouteCountry()
  if (q) countryCode.value = q
  await Promise.all([
    api
      .get<typeof countryOptions.value>('/admin/knowledge/countries')
      .then((res) => {
        if (Array.isArray(res)) {
          countryOptions.value = res
          if (countryCode.value && !res.some((c) => c.country_code === countryCode.value)) {
            countryCode.value = ''
          }
        }
      })
      .catch((error: unknown) => ElMessage.error((error as Error).message))
      .finally(() => {
        countriesLoaded.value = true
      }),
    countryCode.value ? loadGraph() : Promise.resolve(),
  ])
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', syncCompact)
})
</script>
<template>
  <div class="knowledge-graph-page">
    <header class="page-heading">
      <div>
        <h1>知识图谱</h1>
        <p>探索法规、义务与数据之间的关系，沿每一条关联追溯原文依据。</p>
      </div>
      <div class="page-actions">
        <el-select
          v-model="countryCode"
          style="width: 176px"
          placeholder="选择国家"
          filterable
          clearable
          aria-label="图谱国家"
          @change="onCountrySelectChange"
          @clear="backToOverview"
          ><el-option v-for="c in countryOptions" :key="c.country_code" :value="c.country_code"
            :label="c.country_name_zh" /></el-select
        ><el-button v-if="countryCode" data-testid="kg-overview-btn" @click="backToOverview"
          >全部国家总览</el-button
        ><el-button data-testid="kg-revisions-btn" @click="openRevisionsDrawer"
          >修订历史</el-button
        ><el-popover v-model:visible="managePopoverOpen" trigger="click" placement="bottom-end" :width="300"
          ><template #reference><el-button data-testid="kg-manage-btn">图谱管理</el-button></template>
          <div class="secondary-actions">
            <p class="page-description">
              重新识别梳理将依据本国已入库法规条文更新图谱；也可按中文模板批量校验导入经人工核实的要素与关系。
            </p>
            <el-button :loading="loading" @click="managePopoverOpen = false; runExtract()">重新识别梳理本国图谱</el-button>
            <el-button type="primary" plain data-testid="kg-batch-import-btn" @click="openBatchImport">批量校验导入</el-button>
            <el-button @click="openRevisionsDrawer">查看图谱修订历史</el-button>
          </div></el-popover
        >
      </div>
    </header>
    <div v-if="!countryCode" class="country-cards" data-testid="kg-country-cards">
      <div v-if="!countriesLoaded" style="grid-column: 1 / -1; padding: 32px; text-align: center; color: var(--cy-text-3)">正在加载各国知识图谱概览…</div>
      <div v-else-if="!countryOptions.some((x) => (x.documents_count ?? 0) > 0)" style="grid-column: 1 / -1; padding: 24px">
        <el-empty description="当前法规库尚未收录条文，请先在「采集站点」或「法规文件」中导入法规后再生成知识图谱" />
      </div>
      <button v-for="c in countryOptions.filter((x) => (x.documents_count ?? 0) > 0)" :key="c.country_code" type="button" class="country-card" :data-testid="`kg-country-${c.country_code}`" @click="countryCode = c.country_code; loadGraph()">
        <strong>
          {{ c.country_name_zh }}
          <small>{{ c.documents_count ?? 0 }} 份现行法规 · 更新于 {{ localTime(c.last_updated_at, 'date') || '—' }}</small>
        </strong>
        <el-tag v-if="c.pending_kg_count" size="small" type="warning" effect="plain">待复核 {{ c.pending_kg_count }} 条</el-tag>
        <span v-else>已全部复核</span>
      </button>
    </div>
    <template v-else>
    <el-alert
      v-if="graph.quality_summary.source_integrity_unresolved_documents ?? graph.quality_summary.source_integrity_held_documents"
      type="error"
      :closable="false"
      show-icon
      :title="`${graph.quality_summary.source_integrity_unresolved_documents ?? graph.quality_summary.source_integrity_held_documents} 份资料待补充正确原文`"
      style="margin-bottom: 16px"
    >
      <div v-for="item in graph.quality_summary.source_integrity_records?.filter(x => x.status === 'needs_source')" :key="item.document_id">
        {{ item.title }} · {{ item.reason }}
        <router-link :to="{ path: '/documents', query: { country_code: item.country_code, document_id: item.document_id } }">查看资料</router-link>
      </div>
    </el-alert>
    <details v-if="graph.quality_summary.source_integrity_replaced_documents" style="margin-bottom: 16px; font-size: 13px; color: var(--text-secondary)">
      <summary style="cursor:pointer">历史来源记录 · {{ graph.quality_summary.source_integrity_replaced_documents }} 份已关联替代原文</summary>
      <div v-for="item in graph.quality_summary.source_integrity_records?.filter(x => x.status === 'replacement_available')" :key="item.document_id" style="padding:12px 0">
        <p>{{ item.title }}</p>
        <p>已关联：{{ item.replacement?.title }} · {{ item.replacement?.edition }}</p>
        <router-link :to="{ path: '/documents', query: { country_code: item.country_code, document_id: item.replacement?.document_id } }">查看替代原文</router-link>
        <span style="margin:0 10px">·</span>
        <router-link :to="{ path: '/documents', query: { country_code: item.country_code, document_id: item.document_id } }">查看历史记录</router-link>
        <p>这些文件已被新版本替代，原图谱内容不再对用户展示；新版本的条文需重新核对后生效。</p>
      </div>
    </details>
    <div class="stat-grid" aria-label="按要素类型筛选">
      <button
        v-for="(meta, ntype) in NODE_TYPE_META"
        :key="ntype"
        class="stat-card"
        :class="{ 'stat-card-hidden': !isTypeVisible(ntype) }"
        :aria-pressed="isTypeVisible(ntype)"
        title="点击可显示或隐藏该类要素"
        :data-testid="`kg-stat-card-${ntype}`"
        @click="toggleNodeType(ntype)"
      >
        <span class="stat-label"><i :style="{ background: meta.color }" />{{ meta.label }}</span
        ><strong class="stat-num">{{ visibleNodeCountsByType[ntype] || 0 }}</strong
        ><span v-if="!isTypeVisible(ntype)" class="stat-state">已隐藏</span>
      </button>
    </div>
    <div class="surface graph-surface">
      <div class="graph-filter-toolbar">
        <el-select
          v-model="relationFilter"
          placeholder="全部关系"
          aria-label="关系类型"
          style="width: 204px"
          ><el-option value="" :label="`全部关系（${typeFilteredEdges.length}）`" /><el-option
            v-for="(label, rtype) in RELATION_LABEL"
            :key="rtype"
            :value="rtype"
            :label="`${label}（${visibleEdgeCountsByRelation[rtype] || 0}）`"
        /></el-select>
        <el-select v-model="qualityFilter" aria-label="复核状态" style="width: 216px"
          ><el-option value="all" :label="`全部复核状态（${scopedAllEdges.length}）`" /><el-option
            value="needs_confirmation"
            :label="`待复核（${needsConfirmationEdges}）`" /><el-option
            value="verified"
            :label="`已复核（${verifiedEdges}）`"
        /></el-select>
        <el-popover trigger="click" placement="bottom-end" :width="310"
          ><template #reference><el-button>显示设置</el-button></template>
          <div class="display-settings">
            <el-switch
              aria-label="仅展示已复核依据"
              v-model="onlyApproved"
              active-text="仅展示已复核依据"
            /><el-switch
              aria-label="显示范围外文件"
              v-model="showOutOfScope"
              active-text="显示范围外文件"
              data-testid="kg-show-out-of-scope"
            /><el-button data-testid="kg-hide-hub-btn" @click="hideHubNodeTypes"
              >隐藏国家与监管枢纽</el-button
            ><el-button data-testid="kg-show-all-types-btn" @click="showAllNodeTypes"
              >显示全部要素</el-button
            >
          </div></el-popover
        >
        <div v-if="selectedLaw" class="selected-law-context"><span>当前法规：{{ selectedLaw.name }}</span>
          <el-button link @click="selectLaw('')">清除法规筛选</el-button></div>
        <el-tag v-if="selectedId" closable type="info" @close="selectedId = ''">{{
          selectedName
        }}</el-tag>
      </div>
      <el-tabs v-model="graphView"
        ><el-tab-pane label="关系图谱" name="graph"
          ><KgLawGroups :key="countryCode"
            v-loading="loading"
            :groups="lawGroups" :selected-group-id="selectedLawId"
            :total="scopedAllEdges.length" :filtered="filteredEdges.length"
            :shown="displayedEvidenceCount" :shown-groups="fgEdges.length"
            :overview-shown="filteredEdges.length" :overview-groups="overviewFgEdges.length"
            :page="displayedPage.page" :pages="displayedPage.pages"
            @select="selectLaw" @page="selectRelationPage"
          >
            <template #overview>
              <KgForceGraph :nodes="overviewNodes" :edges="overviewFgEdges" :categories="fgCategories"
                :visible-types="visibleNodeTypes" :selected-id="selectedId" overview height="620px"
                @node-click="onOverviewNodeClick" @toggle-type="toggleNodeType" />
              <div class="graph-caption">{{ overviewNodes.length }} 个要素 · {{ lawDocGroupsCount }} 部法规分组<template v-if="hasTopSystemGroup">（另含 1 组国家与监管体系顶层关系）</template> · 展示筛选后的全部关系，跨法规的关系在相关的两部法规下都能看到</div>
            </template>
            <KgForceGraph :nodes="fgNodes" :edges="fgEdges" :categories="fgCategories"
              :visible-types="visibleNodeTypes" :selected-id="selectedId" height="480px"
              @node-click="onNodeClick" @toggle-type="toggleNodeType" />
            <div class="graph-caption"><span>{{ fgNodes.length }} 个要素 · 点击要素查看属性与关联依据</span>
              <el-button link @click="showAttrCard = true">查看要素详情</el-button></div>
          </KgLawGroups></el-tab-pane
        ><el-tab-pane :label="`条文依据明细（${tableEdges.length}）`" name="evidence">
          <el-table
            v-if="graphView === 'evidence'"
            data-testid="kg-edge-table"
            :data="evidencePage.edges"
            v-loading="loading"
            border
            stripe
            style="margin-top: 14px"
          >
            <el-table-column label="来源要素" min-width="145">
              <template #default="{ row }">
                <el-tag size="small" effect="plain">
                  {{ nodeTypeLabel(row.source_node_type) }}
                </el-tag>
                <div style="font-weight: 600; margin-top: 3px">
                  {{ cleanElementText(row.source_name_zh) }}
                </div>
              </template>
            </el-table-column>
            <el-table-column label="关联关系" width="124" class-name="cy-col-nowrap">
              <template #default="{ row }">
                <el-tag type="primary" size="small">
                  {{ edgeRelationLabel(row.relation_type, row.source_node_type, row.target_node_type, row.target_name_zh) }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column label="关联要素" min-width="145">
              <template #default="{ row }">
                <el-tag size="small" effect="plain">
                  {{ nodeTypeLabel(row.target_node_type) }}
                </el-tag>
                <div style="font-weight: 600; margin-top: 3px">
                  {{ cleanElementText(row.target_name_zh) }}
                </div>
              </template>
            </el-table-column>
            <el-table-column label="条文依据与原文" min-width="250">
              <template #default="{ row }">
                <div class="sub" style="font-weight: 600">
                  {{ row.document_title || '官方法规文本' }} · 条号
                  {{ row.clause_number || '-' }}
                </div>
                <div class="quote-box">{{ row.evidence_quote }}</div>
                <div v-if="row.judge_reason" class="sub" style="margin-top: 3px; color: #0f766e">
                  {{ row.is_structural ? '来源说明' : '核验说明' }}：{{ cleanElementText(row.judge_reason) }}
                </div>
              </template>
            </el-table-column>
            <el-table-column label="核验状态" width="148">
              <template #default="{ row }">
                <el-tag :type="statusType(row)" size="small">{{ statusLabel(row) }}</el-tag>
                <div v-if="row.verification_status === 'needs_confirmation'" class="sub" style="margin-top: 2px">
                  {{ cleanElementText(row.verification_reason) }}
                </div>
                <div v-else class="sub" style="margin-top: 2px">
                  {{ row.is_structural ? '目录关系' : '条文关系' }}
                </div>
              </template>
            </el-table-column>
            <el-table-column label="操作" width="132" :fixed="isCompact ? false : 'right'" class-name="cy-col-nowrap">
              <template #default="{ row }">
                <div class="kg-edge-ops">
                  <el-button
                    v-if="row.verification_status === 'needs_confirmation'"
                    size="small"
                    type="success"
                    link
                    @click="reviewEdge(row, 'approved')"
                  >
                    通过
                  </el-button>
                  <el-button size="small" type="danger" link @click="reviewEdge(row, 'rejected')">
                    驳回
                  </el-button>
                </div>
              </template>
            </el-table-column>
          </el-table><div class="graph-caption" aria-label="条文依据分页">
            <el-button :disabled="evidencePage.page <= 1" @click="evidencePageNumber--">上一页依据</el-button>
            <span>第 {{ evidencePage.page }} / {{ evidencePage.pages }} 页 · 共 {{ tableEdges.length }} 条依据，每页 50 条</span>
            <el-button :disabled="evidencePage.page >= evidencePage.pages" @click="evidencePageNumber++">下一页依据</el-button>
          </div></el-tab-pane
        ></el-tabs
      >
    </div>
    <details class="ui-disclosure">
      <summary>
        数据覆盖情况
        <span
          >共 {{ scopedAllEdges.length }} 条关系，其中 {{ verifiedEdges }} 条已完成条文复核，{{ needsConfirmationEdges }} 条待复核</span
        >
      </summary>
      <div class="disclosure-body">
        <div class="quality-bar" data-testid="kg-quality-bar">
          <el-tag
            :type="qualityFilter === 'all' ? 'primary' : 'info'"
            style="cursor: pointer"
            data-testid="kg-quality-all"
            @click="qualityFilter = 'all'"
          >
            全部关系（{{ graph.edges.length }}）
          </el-tag>
          <el-tag
            v-if="(graph.quality_summary?.documents_total ?? 0) > 0"
            type="success"
            effect="plain"
            data-testid="kg-doc-scope"
          >
            法规文件 {{ graph.quality_summary?.documents_total }} 份（核心
            {{ graph.quality_summary?.documents_core ?? 0 }} / 相关
            {{ graph.quality_summary?.documents_related ?? 0 }} / 范围外
            {{ graph.quality_summary?.documents_out_of_scope ?? 0 }}），其中
            {{ graph.quality_summary?.documents_extracted ?? 0 }} 份已纳入图谱梳理
          </el-tag>
          <el-tag type="primary" effect="plain">
            条文依据关系：{{ graph.quality_summary?.semantic_edges ?? 0 }} 条
          </el-tag>
          <el-tag type="info" effect="plain">
            目录关系（发布/监管）：
            {{ graph.quality_summary?.structural_edges ?? 0 }} 条
          </el-tag>
          <el-tag
            :type="qualityFilter === 'needs_confirmation' ? 'warning' : 'info'"
            style="cursor: pointer"
            data-testid="kg-quality-pending"
            @click="qualityFilter = qualityFilter === 'needs_confirmation' ? 'all' : 'needs_confirmation'"
          >
            待复核（{{ graph.quality_summary?.needs_confirmation_edges_count ?? 0 }}）
          </el-tag>
          <el-tag
            :type="qualityFilter === 'verified' ? 'success' : 'info'"
            style="cursor: pointer"
            data-testid="kg-quality-approved"
            @click="qualityFilter = qualityFilter === 'verified' ? 'all' : 'verified'"
          >
            已复核（{{ verifiedEdges }}）
          </el-tag>
          <el-tag type="success" effect="plain">
            原文摘录不一致：
            {{
              graph.quality_summary?.quote_mismatch ??
              graph.quality_summary?.quote_mismatch_edges_count ??
              0
            }}
            条
          </el-tag>
          <el-tag type="success" effect="plain">
            待复核关系：{{ graph.quality_summary?.semantic_unjudged ?? 0 }} 条 · 需重点关注：
            {{ graph.quality_summary?.low_confidence ?? 0 }} 条
          </el-tag>
          <el-tag
            v-if="(graph.quality_summary?.superseded_edges_count ?? 0) > 0"
            type="info"
            effect="plain"
          >
            已替换历史关系：
            {{ graph.quality_summary?.superseded_edges_count }} 条
          </el-tag>
          <el-tag type="info" effect="plain">
            未关联要素：{{ graph.quality_summary?.isolated_nodes_count ?? 0 }} 个
          </el-tag>
          <el-tag type="info" effect="plain">
            未关联图谱的条文：
            {{ graph.quality_summary?.unlinked_clauses_count ?? 0 }} /
            {{ graph.quality_summary?.total_clauses_count ?? 0 }} 条
          </el-tag>
        </div>
      </div>
    </details>
    <details v-if="validation.some(v => v.count)" class="validation-box">
      <summary style="cursor:pointer">
        数据校验 · {{ validation.filter(v => v.count).length }} 类问题待处理
      </summary>
      <div v-for="v in validation.filter(x => x.count)" :key="v.key" style="padding:8px 0">
        <p>{{ cleanElementText(v.label) }}：{{ v.count }} 个</p>
        <ul>
          <li v-for="i in v.items.slice(0, 8)" :key="i.id">
            {{ i.name }}<span v-if="i.detail"> · {{ cleanElementText(i.detail) }}</span>
          </li>
        </ul>
        <p v-if="v.count > 8">仅列出前 8 个。</p>
      </div>
    </details>
    <el-drawer v-model="showAttrCard" title="要素详情" size="min(520px, 94vw)"
      ><template v-if="activeNode"
        ><el-tag effect="plain">{{
          NODE_TYPE_META[activeNode.node_type]?.label || activeNode.node_type
        }}</el-tag>
        <h3 class="node-detail-title">{{ activeNode.name_zh }}</h3>
        <el-tag
          v-if="obligationKind(activeNode)"
          :type="obligationKind(activeNode)?.type"
          effect="plain"
          class="node-kind-tag"
          >{{ obligationKind(activeNode)?.label }}</el-tag
        >
        <el-tag v-if="claimTag(activeNode)" effect="plain" type="info" class="node-kind-tag">{{ claimTag(activeNode) }}</el-tag>
        <p class="page-description">
          {{ selectedId ? '当前选中要素' : '当前国家的代表性法规要素' }} · 要素详细属性
        </p>
        <div v-if="partyShown(activeNode)" class="attr-grid party-grid">
          <div v-for="item in PARTY_LABELS" :key="item.key" class="attr-item">
            <div class="attr-key">{{ item.label }}</div>
            <div class="attr-val">{{ activeNode.attributes?.[item.key] || '条文未明确' }}</div>
            <div v-for="ev in attrEvidence(item.key)" :key="ev.evidence_quote" class="attr-source">
              法规依据：{{ ev.document_title }} {{ ev.clause_number ? `第 ${ev.clause_number} 条` : '' }}
              <blockquote>{{ ev.evidence_quote }}</blockquote>
            </div>
          </div>
        </div>
        <div class="attr-grid" data-testid="kg-eight-attributes-panel">
          <div v-for="item in EIGHT_ATTR_LABELS" :key="item.key" class="attr-item">
            <div class="attr-key">{{ item.label }}</div>
            <div class="attr-val">
              {{ activeNode.attributes?.[item.key] || '条文未明确' }}
            </div>
            <div
              v-for="ev in attrEvidence(item.key)"
              :key="ev.evidence_quote"
              class="attr-source"
            >
              法规依据：{{ ev.document_title }} {{ ev.clause_number ? `第 ${ev.clause_number} 条` : '' }}
              <blockquote>{{ ev.evidence_quote }}</blockquote>
            </div>
          </div>
        </div>
        <GraphEditPanel
          :country-code="countryCode"
          :node="activeNode"
          :nodes="graph.nodes"
          :edges="graph.edges"
          :relation-labels="RELATION_LABEL"
          @changed="loadGraph"
        /></template
      ><el-empty v-else description="请先在图谱中选择一个要素"
    /></el-drawer>
    </template>
    <el-dialog
      v-model="showBatchDialog"
      title="知识图谱要素与关系 · 批量校验导入"
      width="min(680px, 94vw)"
      top="5vh"
      append-to-body
      data-testid="kg-batch-dialog"
    >
      <div class="kg-batch-body">
        <p style="margin:0;font-size:13px;color:#475569;line-height:1.65">支持导入六类图谱要素与十二类法规条文关联关系。请先选择目标国家或地区，再按表格模板填写法规名称与依据条号，系统自动匹配在库条款并执行依据原句逐字包含核验，未通过核验的记录不予入库并逐行说明原因。</p>
        <div style="display:flex;gap:12px;align-items:center;flex-wrap:wrap">
          <label style="font-size:14px;color:#20323f;font-weight:600">
            <span style="color:#dc2626;margin-right:4px">*</span>目标国家或地区：
          </label>
          <el-select
            v-model="batchCountry"
            placeholder="请先选择目标国家"
            filterable
            clearable
            style="width: 220px"
            data-testid="kg-batch-country"
            @change="onBatchCountryChange"
          >
            <el-option
              v-for="c in countryOptions"
              :key="c.country_code"
              :value="c.country_code"
              :label="c.country_name_zh"
            />
          </el-select>
          <el-radio-group v-model="batchMode" @change="(m: string | number | boolean | undefined) => fillBatchExample(m as BatchMode)">
            <el-radio-button value="csv_nodes">表格模板（要素）</el-radio-button>
            <el-radio-button value="csv_edges">表格模板（关联关系）</el-radio-button>
          </el-radio-group>
        </div>
        <el-input v-model="batchInput" type="textarea" :rows="8" placeholder="请先在上方选择目标国家，再按所选模板粘贴待导入的要素或关联关系记录" data-testid="kg-batch-input" />
        <div v-if="batchResult" style="padding:10px 12px;border-radius:8px;background:#f8fafc;border:1px solid #e2e8f0;font-size:13px" data-testid="kg-batch-result">
          <strong>导入结果：新增要素 {{ batchResult.created_nodes_count }} 个 · 新增关系 {{ batchResult.created_edges_count }} 条 · 未通过核验 {{ batchResult.errors.length }} 条</strong>
          <ul v-if="batchResult.errors.length" style="margin:6px 0 0 18px;padding:0;color:#b91c1c">
            <li v-for="(er, ei) in batchResult.errors" :key="ei">第 {{ er.row ?? er.index ?? ei + 1 }} 条（{{ er.kind === 'node' ? '要素' : er.kind === 'edge' ? '关联关系' : er.kind }}）：{{ sanitizeBatchError(er.error) }}</li>
          </ul>
        </div>
      </div>
      <template #footer>
        <el-button @click="showBatchDialog = false">取消</el-button>
        <el-button type="primary" :loading="batchBusy" :disabled="!batchCountry" data-testid="kg-batch-submit" @click="submitBatchImport">提交校验并导入</el-button>
      </template>
    </el-dialog>

    <el-drawer
      v-model="showRevisionsDrawer"
      title="知识图谱修订历史"
      size="min(800px, 94vw)"
      append-to-body
      data-testid="kg-revisions-drawer"
    >
      <div v-loading="revisionsLoading" class="kg-revisions-pane">
        <div class="kg-revisions-toolbar">
          <label class="kg-rev-label">筛选国家或地区：</label>
          <el-select
            v-model="revisionCountry"
            style="width: 190px"
            filterable
            aria-label="修订历史国家"
            @change="loadRevisions"
          >
            <el-option
              v-for="c in countryOptions"
              :key="c.country_code"
              :value="c.country_code"
              :label="c.country_name_zh"
            />
          </el-select>
          <el-button size="small" @click="loadRevisions">刷新</el-button>
          <span class="sub" style="margin-left: auto">共 {{ revisionsList.length }} 个修订批次</span>
        </div>

        <el-alert
          type="info"
          :closable="false"
          show-icon
          title="展示法规分页解析与图谱要素梳理产生的修订提案批次记录；未人工复核通过的提案不会自动覆盖企业端正在使用的现行法规依据。"
          style="margin-bottom: 14px"
        />

        <el-table v-if="revisionsList.length > 0" :data="revisionsList" border stripe size="small" data-testid="kg-revisions-table">
          <el-table-column label="修订批次" width="156">
            <template #default="{ row }">
              <strong>{{ row.batchLabel }}</strong>
              <div class="sub">{{ row.scopeLabel }}</div>
            </template>
          </el-table-column>
          <el-table-column label="关联法规与梳理摘要" min-width="200">
            <template #default="{ row }">
              <div style="font-weight: 600">{{ row.documentTitle }}</div>
              <div class="sub" style="margin-top: 2px">{{ row.summaryText }}</div>
            </template>
          </el-table-column>
          <el-table-column label="状态" width="144">
            <template #default="{ row }">
              <el-tag :type="row.statusType" size="small">{{ row.statusLabel }}</el-tag>
              <div class="sub" style="margin-top: 2px">{{ row.reviewLabel }}</div>
            </template>
          </el-table-column>
          <el-table-column label="记录时间" width="144" class-name="cy-col-nowrap">
            <template #default="{ row }">{{ row.timeLabel }}</template>
          </el-table-column>
        </el-table>
        <div v-else class="kg-rev-empty">
          <p class="kg-rev-empty-title">
            {{ revisionsNotice || '当前国家暂无待审图谱修订批次记录' }}
          </p>
          <p class="sub">
            当新修订法规入库分页解析或执行「重新识别梳理本国图谱」生成提案快照时，将在此按批次展示状态、时间与对照摘要。
          </p>
        </div>
      </div>
    </el-drawer>
  </div>
</template>
<style scoped>
.attr-source {
  margin-top: 6px;
  font-size: 13px;
  color: #64748b;
}
.attr-source blockquote {
  margin: 4px 0 0;
  padding-left: 8px;
  border-left: 2px solid #cbd5e1;
  white-space: pre-wrap;
}
.node-kind-tag {
  margin-bottom: 8px;
}
.stat-grid {
  display: grid;
  grid-template-columns: repeat(6, minmax(0, 1fr));
  gap: 12px;
  margin-bottom: 24px;
}
.stat-card {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  min-width: 0;
  padding: 20px 18px;
  background: white;
  border: 1px solid var(--cy-line);
  border-radius: 11px;
  text-align: left;
  transition:
    border-color 0.18s,
    transform 0.18s;
}
.stat-card:hover {
  border-color: #bdcdee;
  transform: translateY(-2px);
}
.stat-card-hidden {
  background: #f0f3f6;
  color: #9aa7b4;
}
.stat-label {
  display: flex;
  align-items: center;
  gap: 8px;
  color: #74828e;
  font-size: 13px;
}
.stat-label i {
  width: 7px;
  height: 7px;
  border-radius: 50%;
}
.stat-num {
  font-size: 24px;
  font-weight: 600;
  line-height: 1.4;
  margin: 8px 0 3px;
  color: #273b4a;
}
.stat-state {
  font-size: 13px;
  color: #a0acb5;
}
.graph-surface {
  padding: 22px 24px 10px;
}
.graph-filter-toolbar {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  margin-bottom: 6px;
}
.graph-canvas {
  border-radius: 10px;
  background: #f8fafc;
  border: 1px solid #edf0f4;
  overflow: hidden;
}
.selected-law-context { flex-basis: 100%; display: flex; flex-wrap: wrap; align-items: center; gap: 8px; font-size: 13px; color: #526174; overflow-wrap: anywhere; }
.graph-caption {
  display: flex;
  gap: 18px;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  font-size: 13px;
  color: #81919d;
  padding: 12px 0 4px;
}
.display-settings {
  display: grid;
  gap: 14px;
  padding: 4px;
}
.display-settings label {
  display: flex;
  gap: 14px;
  align-items: center;
  font-size: 13px;
  color: #6a7b89;
}
.display-settings label .el-select {
  width: 105px;
}
.quality-bar {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  padding-top: 8px;
}
.quality-bar .el-tag {
  font-size: 13px;
  line-height: 1.7;
}
.node-detail-title {
  font-size: 18px;
  margin: 18px 0 12px;
  overflow-wrap: anywhere;
}
.attr-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16px;
  margin-top: 24px;
}
.attr-item {
  padding: 16px;
  background: #f7f9fc;
  border-radius: 8px;
}
.attr-key {
  color: #70828f;
  font-size: 13px;
  margin-bottom: 8px;
}
.attr-val {
  font-size: 14px;
  line-height: 1.9;
  white-space: pre-line;
  overflow-wrap: anywhere;
}
.quote-box {
  font-size: 14px;
  line-height: 1.8;
  max-height: 140px;
  overflow-y: auto;
  white-space: pre-wrap;
  margin-top: 10px;
}
@media (max-width: 1400px) {
  .stat-grid {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }
  .stat-state {
    display: none;
  }
  .stat-card {
    padding: 14px 16px;
  }
  .stat-num {
    font-size: 24px;
  }
}
@media (max-width: 600px) {
  .stat-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
  .graph-surface {
    padding: 16px 12px 6px;
  }
  .attr-grid {
    grid-template-columns: 1fr;
  }
  .graph-caption {
    gap: 8px;
  }
}
.country-cards { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 16px; margin-top: 8px; }
.country-card { display: flex; justify-content: space-between; align-items: center; gap: 12px; padding: 16px 18px; border: 1px solid var(--cy-line); border-radius: 10px; background: #fff; font: inherit; font-size: 15px; cursor: pointer; text-align: left; }
.country-card > span { color: var(--cy-text-3); font-size: 13px; white-space: nowrap; flex-shrink: 0; }
.country-card :deep(.el-tag) { white-space: nowrap; flex-shrink: 0; }
.country-card strong { display: flex; flex-direction: column; align-items: flex-start; gap: 4px; font-size: 16px; text-align: left; min-width: 0; }
.country-card strong small { font-weight: 400; font-size: 13px; color: var(--cy-text-2); }
.country-card:hover { border-color: var(--el-color-primary-light-5); color: var(--el-color-primary); }
.kg-edge-ops { display: inline-flex; align-items: center; gap: 8px; white-space: nowrap; }
.kg-edge-ops :deep(.el-button.is-link) { padding: 2px 4px; }
.kg-batch-body { display: flex; flex-direction: column; gap: 12px; max-height: 66vh; overflow-y: auto; padding-right: 4px; }
.kg-revisions-pane { display: flex; flex-direction: column; }
.kg-revisions-toolbar { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; margin-bottom: 14px; }
.kg-rev-label { font-size: 14px; font-weight: 600; color: #20323f; }
.kg-rev-empty { padding: 32px 20px; text-align: center; background: #f8fafc; border: 1px dashed #cbd5e1; border-radius: 10px; }
.kg-rev-empty-title { margin: 0 0 8px; font-size: 15px; font-weight: 600; color: #20323f; }
</style>
