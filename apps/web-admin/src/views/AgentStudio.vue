<script setup lang="ts">
import { ElMessage, ElMessageBox } from 'element-plus'
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { startProgressStream, type StreamState, localTime, COUNTRY_NAMES } from '@cyshuhe/shared'
import { api } from '../api'
import { useAuth } from '../auth'
import {
  boundedPosition,
  dragPosition,
  readPositions,
  spacedLayout,
  NODE_WIDTH,
  NODE_HEIGHT,
  type NodePosition,
} from '../components/workflowLayout'

// 毫秒 → 自然中文时长（如 452000ms -> 约 7 分 32 秒）
function humanDuration(ms?: number | null): string {
  const s = Math.round((ms || 0) / 1000)
  if (s < 1) return '不到 1 秒'
  if (s < 60) return `约 ${s} 秒`
  const m = Math.floor(s / 60)
  const rem = s % 60
  return rem === 0 ? `约 ${m} 分钟` : `约 ${m} 分 ${rem} 秒`
}
const seconds = (ms: number | null | undefined) => (ms ? humanDuration(ms) : '—')

interface McpToolSchema {
  tool_name: string
  display_name: string
  description: string
  input_schema: Record<string, unknown>
  output_schema: Record<string, unknown>
}

interface WorkflowNode {
  node_id: string
  title: string
  subtitle: string
  node_type: string
  category_label: string
  icon: string
  color: string
  position_x: number
  position_y: number
  readonly_locked: boolean
  lock_reason: string
  model_provider: string
  model_name: string
  parameters: Record<string, unknown>
  system_prompt: string
  code_entrypoint: string
  mcp_tools: McpToolSchema[]
  input_variables: Array<{ name: string; type: string; desc: string }>
  output_variables: Array<{ name: string; type: string; desc: string }>
  responsibility_desc: string
  runtime_settings: Record<string, unknown>
  configurable_fields: string[]
}

interface WorkflowEdge {
  edge_id: string
  source_node_id: string
  target_node_id: string
  edge_type: string
  label: string
  condition_desc: string
  animated: boolean
}

interface WorkflowConfig {
  id: string
  workflow_key: string
  version_no: number
  name: string
  description: string
  dsl_version: string
  is_active: boolean
  readonly_locked: boolean
  executable: boolean
  runtime_schema_version: string | null
  nodes: WorkflowNode[]
  edges: WorkflowEdge[]
  change_note: string | null
  created_at: string
}

interface TaskSummary {
  id: string
  conversation_code: string
  org_id: string | null
  org_name: string
  creator_email: string
  creator_name: string
  title: string
  country_code: string
  country_name_zh: string
  vehicle_model: string
  scenario_type: string
  scenario_label: string
  status: string
  sufficiency_score: number
  missing_facts_count: number
  messages_count: number
  steps_count: number
  current_active_node_id: string | null
  current_active_node_title: string | null
  findings_count: number
  latest_report_version: number
  is_partial_sample: boolean
  total_tokens: number
  total_duration_ms: number
  mcp_calls_count: number
  backtrack_count: number
  archived_at?: string | null
  is_archived?: boolean
  created_at: string
  updated_at: string
}

interface StepTrace {
  workflow_version_id: string | null
  id: string
  seq_no: number
  agent_node_id: string
  agent_name: string
  phase: string
  status: string
  business_summary: string
  is_backtrack: boolean
  backtrack_reason: string | null
  backtrack_resolution: string | null
  tool_calls: Array<{
    call_id: string
    tool_name: string
    tool_label: string
    arguments: Record<string, unknown>
    result_summary: string
    duration_ms: number
    verified: boolean
  }>
  input_snapshot: Record<string, unknown>
  output_snapshot: Record<string, unknown>
  tokens_used: number
  duration_ms: number
  created_at: string
}

interface ChatMessageItem {
  id: string
  seq_no: number
  role: string
  content: string
  options: string[]
  slot: string | null
  created_at: string
}

interface FollowUpQuestionItem {
  fact_key: string
  dimension_code: string
  question_zh: string
  why_needed: string
  current_value: string | null
  is_fulfilled: boolean
}

interface FindingItem {
  finding_id: string
  dimension_code: string
  business_fact: string
  clause_id: string | null
  document_title: string
  clause_number: string
  version_no: number
  compliance_obligation: string
  risk_level: string
  basis_quote: string
  basis_zh: string
  verification_status: string
}

interface ReportVersionItem {
  id: string
  version_no: number
  status: string
  is_partial_sample: boolean
  change_note: string
  executive_summary: string
  dimensions: Array<{
    dimension_code: string
    dimension_title: string
    status: string
    risk_level: string
    content_markdown: string
    key_recommendations: string[]
    citations: Array<{
      citation_tag: string
      document_title: string
      clause_number: string
      quote_original: string
      quote_zh: string
    }>
    missing_gap_note: string | null
  }>
}

interface TaskDetail {
  workflow_version_id: string | null
  workflow_version_no: number
  id: string
  conversation_code: string
  org_id: string | null
  org_name: string
  creator_email: string
  creator_name: string
  current_active_node_id: string | null
  current_active_node_title: string | null
  title: string
  country_code: string
  country_name_zh: string
  vehicle_model: string
  scenario_type: string
  scenario_label: string
  status: string
  sufficiency_score: number
  facts: Record<string, string>
  missing_facts: FollowUpQuestionItem[]
  findings: FindingItem[]
  step_traces: StepTrace[]
  report_versions: ReportVersionItem[]
  messages: ChatMessageItem[]
  total_tokens: number
  total_duration_ms: number
  mcp_calls_count: number
  backtrack_count: number
  archived_at?: string | null
  is_archived?: boolean
  created_at: string
  updated_at: string
}

const STATUS_LABEL_MAP: Record<string, string> = {
  running: '实时运行中',
  collecting_facts: '业务信息采集中',
  awaiting_user_facts: '待补充情况',
  ready_to_run: '待启动分析',
  completed: '已出报告',
  partial_sample: '已出报告',
  failed: '执行失败',
}

const STATUS_TAG_TYPE: Record<string, 'primary' | 'success' | 'warning' | 'danger' | 'info'> = {
  running: 'primary',
  collecting_facts: 'info',
  awaiting_user_facts: 'warning',
  ready_to_run: 'info',
  completed: 'success',
  partial_sample: 'warning',
  failed: 'danger',
}

const PHASE_LABEL_MAP: Record<string, string> = {
  fact_check: '环节 1 · 八方面情况完备度核验',
  retrieval: '环节 2 · 法规检索与现行状态核验',
  analysis: '环节 3 · 八方面合规分析初稿',
  verification: '环节 4 · 条文原文与数额核验',
  re_analysis: '环节 4 复核 · 退回补充依据后重新分析',
  re_verification: '环节 4 复检 · 补充结论二次核验',
  compilation: '环节 5 · 八章国别报告与对照表编制',
  update_linkage: '环节 6 · 法规修订影响联动追踪',
  error: '异常终止',
  user_fact_question: '分析中追问 · 等待业务信息',
  user_fact_answer: '业务信息回答 · 局部继续评估',
  user_fact_exhausted: '两轮追问后记录待补充事项',
}

const EIGHT_DIM_TITLES: Record<string, string> = {
  D1: '属地数据法规体系概述',
  D2: '汽车核心数据定义与分类',
  D3: '数据跨境传输合规路径与前置条件',
  D4: '用户授权、告知、同意',
  D5: '数据存储、留存、销毁',
  D6: '违规行为分级、处罚标准、监管追责',
  D7: '属地市场准入认证、申报材料',
  D8: '企业合规整改建议',
}

const DIM_SHORT_MAP: Record<string, string> = {
  D1: '第一章 属地数据法规体系概述',
  D2: '第二章 汽车核心数据定义与分类',
  D3: '第三章 数据跨境传输合规路径与前置条件',
  D4: '第四章 用户授权、告知、同意',
  D5: '第五章 数据存储、留存、销毁',
  D6: '第六章 违规行为分级、处罚标准、监管追责',
  D7: '第七章 属地市场准入认证、申报材料',
  D8: '第八章 企业合规整改建议',
}

const RISK_LABEL_MAP: Record<string, string> = {
  CRITICAL: '高风险',
  HIGH: '高风险',
  MEDIUM: '中风险',
  LOW: '低风险',
  INFO: '低风险',
  NONE: '无明显风险',
  NA: '待评估',
  'N/A': '待评估',
  高: '高风险',
  中: '中风险',
  低: '低风险',
  高风险: '高风险',
  中风险: '中风险',
  低风险: '低风险',
}

const SCENARIO_LABEL_MAP: Record<string, string> = {
  data_return: '车端数据跨境回传',
  vehicle_data_return: '车端数据跨境回传',
  cross_border_transfer: '数据跨境传输',
  cross_border_rd: '跨国协同研发测试',
  ota_upgrade: '远程软件升级服务',
  ota: '远程软件升级服务',
  autonomous_driving: '智能驾驶路测与训练',
  adas_training: '辅助驾驶算法训练',
  cockpit_interaction: '智能座舱语音与交互',
  smart_cockpit: '智能座舱服务',
  connected_car_ops: '车联网平台日常运营',
  telematics: '车载联网远控服务',
  remote_diagnostics: '远程故障诊断与售后',
  after_sales: '海外售后服务与维修',
  fleet_ops: '海外车队运营管理',
  v2x: '车路云协同通信',
  marketing_crm: '海外车主营销与客户服务',
  supply_chain: '跨境供应链协同',
  cloud_overseas: '境外本地云存储',
  cloud_cn: '回传中国境内存储',
  hybrid: '境内外混合存储',
  yes: '是',
  no: '否',
  true: '是',
  false: '否',
  unknown: '未明确',
  unsure: '暂不确定',
}

const STUDIO_CLEAN_RULES: Array<[RegExp, string]> = [
  [/内置\s*MCP\s*工具箱/gi, '法规与知识图谱检索组件'],
  [/MCP\s*法规混合检索\s*Agent/gi, '现行法规与图谱检索'],
  [/MCP\s*法规混合检索/gi, '现行法规与图谱检索'],
  [/MCP\s*工具箱|MCP\s*工具|MCP\s*服务/gi, '法规与知识图谱检索组件'],
  [/\bMCP\b/g, '法规检索'],
  [new RegExp('sufficiency' + '_score\\s*(?:==|>=|=)\\s*100(?:\\s*[（(][^）)]*[)）])?', 'gi'), '必填业务信息已齐备'],
  [new RegExp('\\bsufficiency' + '_score\\b', 'gi'), '业务信息完备度'],
  [new RegExp('SHA-?256\\s*归档状态及外文\\s*' + 'OCR\\s*[/／]\\s*翻译复核资格', 'gi'), '官方原文完整性与中文译文状态'],
  [new RegExp('外文\\s*' + 'OCR\\s*[/／]\\s*翻译复核(?:资格)?', 'gi'), '中文译文与扫描件核对状态'],
  [/SHA-?256\s*(?:归档状态|完整性|哈希|校验)?/gi, '官方原文完整性'],
  [new RegExp('\\bO' + 'CR\\b', 'g'), '文字识别'],
  [/[（(]\s*in_force\s*[)）]/gi, '（现行有效）'],
  [/\bin_force\b/gi, '现行有效'],
  [/Clause\.original_text/gi, '在库现行法规条文原文'],
  [/\bClause\b/g, '法规条款'],
  [/\s*[（(]\s*SG\/EG\/TN\/MA\/LB\s*[)）]/gi, ''],
  [/\bSG\/EG\/TN\/MA\/LB\b/gi, '目标国家与地区'],
  [/D1\s*[~～\-—－至到]\s*D8\s*(?:八个?维度|8\s*个?维度)?/gi, '八个合规分析维度'],
  [new RegExp('Fact\\s*' + 'Collector(?:\\s*&\\s*Sufficiency\\s*Guard)?', 'gi'), '业务信息采集与完备度核验'],
  [/Sufficiency\s*Guard/gi, '完备度核验'],
  [/Effective\s*Law\s*&\s*KG\s*RAG\s*Retriever|KG\s*RAG\s*Retriever|RAG\s*Retriever/gi, '现行法规与知识图谱检索'],
  [/\bRAG\b/g, '法规检索'],
  [/8-Dimension\s*Compliance\s*Matrix\s*Analyst/gi, '八方面合规分析'],
  [new RegExp('Programmatic\\s*Citation\\s*&\\s*Substring\\s*' + 'Gate(?:\\s*Verifier)?', 'gi'), '条文引用逐字核验'],
  [new RegExp('Substring\\s*' + 'Gate(?:\\s*Verifier)?', 'gi'), '条文引用逐字核验'],
  [/Programmatic\s*Citation/gi, '条文引用核对'],
  [/8-Dimension\s*Report\s*Compiler(?:\s*&\s*Exporter)?/gi, '八章国别报告编制'],
  [/72h\s*Legal\s*Amendment\s*Impact\s*Watcher/gi, '法规修订影响联动监测'],
  [new RegExp('\\bBACK' + 'TRACK_REVISE\\b', 'gi'), '退回补充依据重算'],
  [new RegExp('\\bBACK' + 'TRACK\\b', 'gi'), '退回补充依据'],
  [/\bSOURCE_EVIDENCE_PENDING\b/gi, '待补充法规原文依据'],
  [/\bS12\b/g, '图谱关联关系'],
  [/\bS6\b/g, '图谱核心要素'],
  [new RegExp('六' + '要素', 'g'), '图谱要素'],
  [/十二关系/g, '图谱关联关系'],
  [/\bCONV-[A-Za-z0-9_-]+\b/g, '当前评估项目'],
  [/检索命中/g, '匹配相关条文'],
  [new RegExp('补' + '证闭环', 'g'), '补充依据核对完成'],
  [new RegExp('退回补' + '证', 'g'), '退回补充依据'],
  [new RegExp('补' + '证', 'g'), '补充依据'],
  [new RegExp('拦截' + '退回', 'g'), '核验重生成'],
  [new RegExp('等待补充' + '业务事实', 'g'), '待补充情况'],
  [/依据子串核验与退回质检/g, '原文逐字核验与退回补充依据'],
  [/子串核验\s*100%\s*通过/g, '引用原文全部核对无误'],
  [/子串核验/g, '原文逐字核验'],
  [new RegExp('待' + '核实', 'g'), '待确认'],
  [new RegExp('底' + '座', 'g'), '知识库'],
  [new RegExp('译' + '校', 'g'), '译文核对'],
  [new RegExp('办' + '件', 'g'), '项目'],
  [/\s*\bAgent\b/gi, '环节'],
  [/法规检索\s*法规混合检索(?:\s*环节)?/g, '现行法规与图谱检索'],
]

function cleanDiagText(raw?: unknown): string {
  let s = String(raw ?? '')
  if (!s) return ''
  for (const [re, rep] of STUDIO_CLEAN_RULES) {
    s = s.replace(re, rep)
  }
  s = s.replace(/涉及\s*D([1-8])\b/gi, (_, d: string) => `涉及「${EIGHT_DIM_TITLES[`D${d}`] || `第${d}章`}」`)
  s = s.replace(/\bD([1-8])\b/g, (_, d: string) => EIGHT_DIM_TITLES[`D${d}`] || DIM_SHORT_MAP[`D${d}`] || `第${d}章`)
  s = s.replace(
    /\b(data_return|vehicle_data_return|cross_border_transfer|cross_border_rd|ota_upgrade|autonomous_driving|adas_training|cockpit_interaction|smart_cockpit|connected_car_ops|telematics|remote_diagnostics|after_sales|fleet_ops|v2x|marketing_crm|supply_chain|cloud_overseas|cloud_cn|hybrid)\b/gi,
    (m) => SCENARIO_LABEL_MAP[m.toLowerCase()] || m,
  )
  s = s.replace(/\b(CRITICAL|HIGH|MEDIUM|LOW)\b/g, (m) => RISK_LABEL_MAP[m] || m)
  s = s.replace(/\b(SG|EG|TN|MA|LB|CN|EU|DE|GB|US|JP|KR|TH|AE|BR)\b/g, (m) => COUNTRY_NAMES[m] || m)
  s = s.replace(/\(\s*([^()（）]*[\u4e00-\u9fff][^()（）]*)\s*\)/g, '（$1）')
  return s.replace(/\s{2,}/g, ' ').trim()
}

function riskLabel(raw?: unknown): string {
  const s = String(raw ?? '').trim()
  if (!s) return '—'
  return RISK_LABEL_MAP[s.toUpperCase()] || RISK_LABEL_MAP[s] || cleanDiagText(s)
}

function scenarioLabel(label?: string | null, type?: string | null): string {
  const raw = (label || type || '').trim()
  if (!raw) return '待选择场景'
  if (SCENARIO_LABEL_MAP[raw]) return SCENARIO_LABEL_MAP[raw]
  if (SCENARIO_LABEL_MAP[raw.toLowerCase()]) return SCENARIO_LABEL_MAP[raw.toLowerCase()]
  return cleanDiagText(raw)
}

function dimLabel(code?: unknown, fullTitle?: unknown): string {
  const c = String(code || '').trim().toUpperCase()
  const ft = cleanDiagText(fullTitle)
  if (ft && !/^D[1-8]$/i.test(ft)) {
    const idx = c.startsWith('D') ? Number(c.slice(1)) : 0
    const ch = ['一', '二', '三', '四', '五', '六', '七', '八'][idx - 1]
    return ch && !ft.startsWith('第') ? `第${ch}章 ${ft}` : ft
  }
  return DIM_SHORT_MAP[c] || EIGHT_DIM_TITLES[c] || '综合章节'
}

function sanitizeWorkflowConfig(wf: WorkflowConfig): WorkflowConfig {
  return {
    ...wf,
    name: cleanDiagText(wf.name),
    description: cleanDiagText(wf.description),
    change_note: wf.change_note ? cleanDiagText(wf.change_note) : null,
    nodes: (wf.nodes || []).map((n) => ({
      ...n,
      title: cleanDiagText(n.title),
      subtitle: cleanDiagText(n.subtitle),
      category_label: cleanDiagText(n.category_label),
      lock_reason: cleanDiagText(n.lock_reason),
      responsibility_desc: cleanDiagText(n.responsibility_desc),
      system_prompt: cleanDiagText(n.system_prompt),
      mcp_tools: (n.mcp_tools || []).map((t) => ({
        ...t,
        display_name: cleanDiagText(t.display_name),
        description: cleanDiagText(t.description),
      })),
      input_variables: (n.input_variables || []).map((v) => ({
        ...v,
        desc: cleanDiagText(v.desc),
      })),
      output_variables: (n.output_variables || []).map((v) => ({
        ...v,
        desc: cleanDiagText(v.desc),
      })),
    })),
    edges: (wf.edges || []).map((e) => ({
      ...e,
      label: cleanDiagText(e.label),
      condition_desc: cleanDiagText(e.condition_desc),
    })),
  }
}

function plainMarkdown(raw?: unknown): string {
  return cleanDiagText(raw)
    .replace(/^#{1,6}\s+/gm, '')
    .replace(/\*\*([^*]+)\*\*/g, '$1')
    .replace(/^\s*[-*]\s+/gm, '· ')
    .trim()
}

const loading = ref(false)
const workflow = ref<WorkflowConfig | null>(null)

const tasks = ref<TaskSummary[]>([])
const selectedTaskId = ref<string>('')
const selectedTaskDetail = ref<TaskDetail | null>(null)

const inspectorOpen = ref(false)
// 流程配置里的环节名、衔接说明换成人话
const PLAIN: Record<string, string> = {
  业务场景输入触发器: '业务场景输入登记', 输入触发器: '流程启动', 信息采集智能体: '业务信息核验', 法规检索智能体: '现行法规检索',
  合规分析智能体: '合规维度分析', 质检核验智能体: '条文引用核验', 报告编制智能体: '合规报告编制', 修订联动智能体: '法规修订联动监测',
  信息采集与追问环节: '业务信息采集与追问', 现行法规与图谱检索: '现行法规与图谱检索',
  八维度合规矩阵分析环节: '按八个合规维度分析', 原文逐字核验与退回补充依据环节: '条文原文逐字核验与退回补充依据',
  八维度国别报告编制环节: '编制八章国别合规报告', '法规修订 72h 影响联动环节': '法规修订 72 小时影响联动提醒',
  法规混合检索: '检索现行有效法规', 八维度合规矩阵分析: '按八个合规维度分析', 原文逐字核验与退回质检: '原文逐字核验与退回补充依据',
  八维度国别报告编制: '编制八章国别合规报告', '法规修订 72h 影响联动': '法规修订 72 小时影响联动提醒',
  提交场景与初始事实: '提交业务要素', '完备度 100% 放行': '完备度达标放行', 交付有效条款与图谱边: '输出有效条款与关联关系',
  提交矩阵与八维初稿: '输出八章分析初稿', 退回分析补充依据: '退回补充法条依据', '原文逐字核验 100% 通过': '引用原文全部核对无误',
  '绑定 72h 修订监测': '登记法规修订联动监测', 法规与知识图谱检索组件: '法规与知识图谱检索组件',
}
function plainTitle(t?: string | null): string {
  const base = cleanDiagText(t || '').trim()
  return PLAIN[base] ?? base
}
const selectedNodeId = ref<string>('node_dimension_analyst')
const inspectorTab = ref<'config' | 'trace'>('config')
const canvasScale = ref<number>(0.85)
const userZoomed = ref(false)
const auth = useAuth()
const canvasScroll = ref<HTMLDivElement | null>(null)
const customPositions = ref<Record<string, NodePosition>>({})
const nodeHeights = ref<Record<string, number>>({})
const nodeElements = new Map<string, HTMLElement>()
let nodeObserver: ResizeObserver | undefined
let viewportObserver: ResizeObserver | undefined
const draggingNodeId = ref('')
let drag: {
  id: string
  pointerId: number
  target: HTMLElement
  startX: number
  startY: number
  scrollX: number
  scrollY: number
  origin: NodePosition
  previous?: NodePosition
  moved: boolean
} | null = null
let suppressClickUntil = 0
const layoutKey = computed(() =>
  workflow.value && auth.user
    ? `cysh:studio-layout:v2:${auth.user.id}:${workflow.value.id}:${workflow.value.version_no}`
    : '',
)
const defaultPositions = computed(() =>
  spacedLayout(workflow.value?.nodes || [], nodeHeights.value),
)
function positionOf(node: WorkflowNode): NodePosition {
  return (
    customPositions.value[node.node_id] || defaultPositions.value[node.node_id] || { x: 24, y: 48 }
  )
}
function heightOf(node: WorkflowNode) {
  return nodeHeights.value[node.node_id] || NODE_HEIGHT
}
const stageSize = computed(() => ({
  width: Math.max(
    880,
    ...(workflow.value?.nodes || []).map((n) => positionOf(n).x + NODE_WIDTH + 32),
  ),
  height: Math.max(
    460,
    ...(workflow.value?.nodes || []).map((n) => positionOf(n).y + heightOf(n) + 56),
  ),
}))
watch(layoutKey, () => {
  cancelDrag()
  try {
    customPositions.value = readPositions(
      localStorage.getItem(layoutKey.value),
      workflow.value?.nodes || [],
    )
  } catch {
    customPositions.value = {}
  }
})
function saveLayout() {
  if (!layoutKey.value) return
  try {
    localStorage.setItem(
      layoutKey.value,
      JSON.stringify({ version: 1, positions: customPositions.value }),
    )
  } catch {
    ElMessage.warning('布局已调整，但当前浏览器无法保存，刷新后将恢复默认布局。')
  }
}
function registerNode(id: string, element: unknown) {
  if (!(element instanceof HTMLElement) || nodeElements.get(id) === element) return
  const old = nodeElements.get(id)
  if (old) nodeObserver?.unobserve(old)
  nodeElements.set(id, element)
  nodeObserver?.observe(element)
}
function startNodeDrag(event: PointerEvent, node: WorkflowNode) {
  if (event.button !== 0 || !event.isPrimary || drag) return
  const target = event.currentTarget as HTMLElement
  drag = {
    id: node.node_id,
    pointerId: event.pointerId,
    target,
    startX: event.clientX,
    startY: event.clientY,
    scrollX: canvasScroll.value?.scrollLeft || 0,
    scrollY: canvasScroll.value?.scrollTop || 0,
    origin: { ...positionOf(node) },
    previous: customPositions.value[node.node_id],
    moved: false,
  }
  target.setPointerCapture(event.pointerId)
  target.focus({ preventScroll: true })
}
function moveNodeDrag(event: PointerEvent) {
  if (!drag || drag.pointerId !== event.pointerId) return
  const dx = event.clientX - drag.startX + (canvasScroll.value?.scrollLeft || 0) - drag.scrollX
  const dy = event.clientY - drag.startY + (canvasScroll.value?.scrollTop || 0) - drag.scrollY
  if (!drag.moved && Math.hypot(dx, dy) < 4) return
  drag.moved = true
  draggingNodeId.value = drag.id
  customPositions.value = {
    ...customPositions.value,
    [drag.id]: dragPosition(drag.origin, dx, dy, canvasScale.value),
  }
}
function finishNodeDrag(event: PointerEvent) {
  if (!drag || drag.pointerId !== event.pointerId) return
  const active = drag
  drag = null
  draggingNodeId.value = ''
  if (active.target.hasPointerCapture(active.pointerId))
    active.target.releasePointerCapture(active.pointerId)
  if (active.moved) {
    suppressClickUntil = Date.now() + 350
    saveLayout()
  }
}
function cancelDrag() {
  if (!drag) return
  const active = drag
  drag = null
  draggingNodeId.value = ''
  const positions = { ...customPositions.value }
  if (active.previous) positions[active.id] = active.previous
  else delete positions[active.id]
  customPositions.value = positions
  if (active.target.hasPointerCapture(active.pointerId))
    active.target.releasePointerCapture(active.pointerId)
  if (active.moved) suppressClickUntil = Date.now() + 350
}
function clickNode(nodeId: string) {
  if (Date.now() < suppressClickUntil) return
  selectNode(nodeId)
}
function moveNodeWithKeyboard(event: KeyboardEvent, node: WorkflowNode) {
  if (event.key === 'Escape') {
    cancelDrag()
    return
  }
  if (event.key === 'Enter' || event.key === ' ') {
    event.preventDefault()
    selectNode(node.node_id)
    return
  }
  const direction: Record<string, [number, number]> = {
    ArrowLeft: [-1, 0],
    ArrowRight: [1, 0],
    ArrowUp: [0, -1],
    ArrowDown: [0, 1],
  }
  const delta = direction[event.key]
  if (!delta || drag) return
  event.preventDefault()
  const position = positionOf(node)
  const step = event.shiftKey ? 32 : 8
  customPositions.value = {
    ...customPositions.value,
    [node.node_id]: boundedPosition(position.x + delta[0] * step, position.y + delta[1] * step),
  }
  saveLayout()
}
async function fitCanvas(resetManual = false) {
  if (resetManual) userZoomed.value = false
  await nextTick()
  const viewport = canvasScroll.value
  if (!viewport || viewport.clientWidth <= 0) return
  const availW = Math.max(320, viewport.clientWidth - 36)
  const availH = Math.max(240, viewport.clientHeight - 36)
  const fitRatio = Math.min(availW / stageSize.value.width, availH / stageSize.value.height)
  canvasScale.value = +Math.min(1, Math.max(0.5, fitRatio)).toFixed(3)
  viewport.scrollTo({ left: 0, top: 0 })
}
async function resetLayout() {
  cancelDrag()
  customPositions.value = {}
  saveLayout()
  await fitCanvas(true)
}

// 实时轮询与对话工作流历史管理状态
const autoPollEnabled = ref(true)
const showArchived = ref(false)
const progressState = ref<StreamState>('idle')
let progressStream: {close: () => void} | undefined
let progressRefresh: ReturnType<typeof setTimeout> | undefined

function connectLiveEvents() {
  progressStream?.close()
  clearTimeout(progressRefresh)
  progressRefresh = undefined
  if (!autoPollEnabled.value) return
  progressStream = startProgressStream({
    url: '/api/admin/agent-studio/events', token: api.token,
    onState: state => { progressState.value = state },
    onUnauthorized: () => { api.setToken(null); window.location.assign(import.meta.env.BASE_URL + 'login') },
    onEvent: () => {
      if (progressRefresh !== undefined) return
      progressRefresh = setTimeout(() => {
        progressRefresh = undefined
        void pollLiveWorkflows()
      }, 120)
    },
  })
}
watch(autoPollEnabled, connectLiveEvents)
const autoFollowRunning = ref(true)
const conversationHistoryVisible = ref(false)
const historyFilterStatus = ref<string>('')
const historyFilterCountry = ref<string>('')
const historySearchKeyword = ref<string>('')
const historyCountryOptions = computed(() => {
  const codes = new Set<string>()
  for (const t of tasks.value) {
    if (t.country_code && t.country_code !== '--' && t.country_code !== 'CN') {
      codes.add(t.country_code)
    }
  }
  return Array.from(codes).map((code) => ({
    code,
    label: COUNTRY_NAMES[code] || code,
  }))
})

// 展开查看每个环节产出抽屉
const stageDeliverablesVisible = ref(false)
const expandedStageNames = ref<string[]>([])

const runningTasks = computed(() => tasks.value.filter((t) => t.status === 'running'))
const collectingTasks = computed(() => tasks.value.filter((t) => t.status === 'collecting_facts'))
const awaitingFactTasks = computed(() => tasks.value.filter((t) => t.status === 'awaiting_user_facts'))
const filteredHistoryTasks = computed(() => {
  return tasks.value.filter((t) => {
    if (!showArchived.value && (t.archived_at || t.is_archived)) return false
    if (historyFilterStatus.value && t.status !== historyFilterStatus.value) return false
    if (historyFilterCountry.value && t.country_code !== historyFilterCountry.value) return false
    if (historySearchKeyword.value.trim()) {
      const kw = historySearchKeyword.value.trim().toLowerCase()
      const hit =
        t.id.toLowerCase().includes(kw) ||
        cleanDiagText(t.title).toLowerCase().includes(kw) ||
        (t.vehicle_model || '').toLowerCase().includes(kw) ||
        (t.org_name || '').toLowerCase().includes(kw) ||
        (t.creator_email || '').toLowerCase().includes(kw) ||
        (t.creator_name || '').toLowerCase().includes(kw)
      if (!hit) return false
    }
    return true
  })
})

const nodeMap = computed(() => {
  const m = new Map<string, WorkflowNode>()
  for (const n of workflow.value?.nodes || []) {
    m.set(n.node_id, n)
  }
  return m
})

const selectedNode = computed<WorkflowNode | null>(() => {
  if (!workflow.value) return null
  return nodeMap.value.get(selectedNodeId.value) || workflow.value.nodes[0] || null
})

const upstreamEdges = computed(() => {
  if (!workflow.value || !selectedNode.value) return []
  return workflow.value.edges.filter((e) => e.target_node_id === selectedNode.value?.node_id)
})

const downstreamEdges = computed(() => {
  if (!workflow.value || !selectedNode.value) return []
  return workflow.value.edges.filter((e) => e.source_node_id === selectedNode.value?.node_id)
})

const nodeTraceStatsMap = computed(() => {
  const map = new Map<
    string,
    {
      runs: number
      duration_ms: number
      tokens: number
      hasBacktrack: boolean
      status: string
    }
  >()
  for (const tr of versionTraces.value) {
    const prev = map.get(tr.agent_node_id) || {
      runs: 0,
      duration_ms: 0,
      tokens: 0,
      hasBacktrack: false,
      status: 'passed',
    }
    prev.runs += 1
    prev.duration_ms += tr.duration_ms
    prev.tokens += tr.tokens_used
    if (tr.is_backtrack) prev.hasBacktrack = true
    prev.status = tr.status
    map.set(tr.agent_node_id, prev)
  }
  return map
})

const versionTraces = computed(() => (selectedTaskDetail.value?.step_traces || []).filter(tr =>
  tr.workflow_version_id ? tr.workflow_version_id === workflow.value?.id : !workflow.value?.executable,
))

const selectedNodeTraces = computed(() => {
  if (!selectedNode.value || !selectedTaskDetail.value) return []
  return versionTraces.value.filter(
    (t) => t.agent_node_id === selectedNode.value?.node_id,
  )
})

const computedSvgEdges = computed(() => {
  if (!workflow.value) return []
  return workflow.value.edges
    .map((e) => {
      const src = nodeMap.value.get(e.source_node_id)
      const dst = nodeMap.value.get(e.target_node_id)
      if (!src || !dst) return null
      const sourcePosition = positionOf(src)
      const targetPosition = positionOf(dst)

      if (e.edge_type === 'backtrack') {
        // 核验未通过退回补充依据连线：从核验环节连回分析环节底部
        const x1 = sourcePosition.x + NODE_WIDTH / 2
        const y1 = sourcePosition.y
        const x2 = targetPosition.x + NODE_WIDTH / 2
        const y2 = targetPosition.y + heightOf(dst)
        const path = `M ${x1} ${y1} C ${x1 - 90} ${y1 - 40}, ${x2 - 90} ${y2 + 40}, ${x2} ${y2}`
        return {
          ...e,
          path,
          labelX: x1 - 95,
          labelY: (y1 + y2) / 2,
        }
      }

      const x1 = sourcePosition.x + NODE_WIDTH
      const y1 = sourcePosition.y + heightOf(src) / 2
      const x2 = targetPosition.x
      const y2 = targetPosition.y + heightOf(dst) / 2
      const dx = Math.max(Math.abs(x2 - x1) * 0.45, 50)
      const path = `M ${x1} ${y1} C ${x1 + dx} ${y1}, ${x2 - dx} ${y2}, ${x2} ${y2}`
      return {
        ...e,
        path,
        labelX: (x1 + x2) / 2,
        labelY: (y1 + y2) / 2 - 12,
      }
    })
    .filter((item): item is NonNullable<typeof item> => item !== null)
})

function tasksEndpoint(): string {
  return `/api/admin/agent-studio/tasks?include_archived=${showArchived.value}&exclude_empty=true`
}

function normalizeTaskList(raw: TaskSummary[]): TaskSummary[] {
  return (raw || [])
    .filter((t) => showArchived.value || (!t.archived_at && !t.is_archived))
    .map((t) => ({
      ...t,
      title: cleanDiagText(t.title),
      scenario_label: scenarioLabel(t.scenario_label, t.scenario_type),
    }))
}

async function reloadTasksList() {
  try {
    const list = normalizeTaskList(await api.get<TaskSummary[]>(tasksEndpoint()))
    tasks.value = list
    if (selectedTaskId.value && !list.some((t) => t.id === selectedTaskId.value)) {
      selectedTaskId.value = list[0]?.id || ''
      if (selectedTaskId.value) await loadTaskTrace(selectedTaskId.value)
      else selectedTaskDetail.value = null
    }
  } catch (e: unknown) {
    ElMessage.error((e as Error).message || '加载评估执行记录失败')
  }
}

async function loadStudioData(versionNo?: number) {
  loading.value = true
  try {
    const query = versionNo ? `?version_no=${versionNo}` : ''
    const [wfRaw, rawList] = await Promise.all([
      api.get<WorkflowConfig>(`/api/admin/agent-studio/workflow${query}`),
      api.get<TaskSummary[]>(tasksEndpoint()),
    ])
    const wf = sanitizeWorkflowConfig(wfRaw)
    const taskList = normalizeTaskList(rawList)
    workflow.value = wf
    tasks.value = taskList
    if (wf.nodes.length > 0 && !wf.nodes.some((n) => n.node_id === selectedNodeId.value)) {
      selectedNodeId.value = wf.nodes[0].node_id
    }
    if (!userZoomed.value) {
      await fitCanvas()
    }
    if (!selectedTaskId.value && taskList.length > 0) {
      const runningFirst = taskList.find((t) => t.status === 'running')
      const completedFirst =
        runningFirst ||
        taskList.find(
          (t) => (t.status === 'completed' || t.status === 'partial_sample') && t.steps_count > 0,
        ) ||
        taskList.find((t) => t.steps_count > 0) ||
        taskList[0]
      selectedTaskId.value = completedFirst.id
      await loadTaskTrace(completedFirst.id)
    }
  } catch (e: unknown) {
    ElMessage.error((e as Error).message || '加载评估执行记录失败')
  } finally {
    loading.value = false
    if (!userZoomed.value) {
      void fitCanvas()
    }
  }
}

async function loadTaskTrace(taskId: string): Promise<boolean> {
  if (!taskId) return false
  try {
    const detail = await api.get<TaskDetail>(`/api/admin/agent-studio/tasks/${taskId}`)
    selectedTaskDetail.value = {
      ...detail,
      title: cleanDiagText(detail.title),
      scenario_label: scenarioLabel(detail.scenario_label, detail.scenario_type),
    }
    if (detail.workflow_version_id && workflow.value?.id !== detail.workflow_version_id) {
      const wfRaw = await api.get<WorkflowConfig>(`/api/admin/agent-studio/workflow?version_no=${detail.workflow_version_no}`)
      workflow.value = sanitizeWorkflowConfig(wfRaw)
    }
    if (detail.status === 'running' && detail.current_active_node_id && autoFollowRunning.value) {
      selectedNodeId.value = detail.current_active_node_id
    }
    if (expandedStageNames.value.length === 0 && detail.step_traces.length > 0) {
      expandedStageNames.value = [
        'stage-chat',
        ...detail.step_traces.map((tr) => `stage-step-${tr.seq_no}`),
      ]
    }
    return true
  } catch (e: unknown) {
    const msg = (e as Error).message || ''
    if (msg.includes('未找到') || msg.includes('不存在') || msg.includes('404')) {
      ElMessage.warning('该评估记录已被删除，已同步刷新列表')
      selectedTaskDetail.value = null
      selectedTaskId.value = ''
      await reloadTasksList()
      return false
    }
    ElMessage.error(msg || '加载评估执行记录失败')
    return false
  }
}

let pollTimer: number | undefined
async function pollLiveWorkflows() {
  if (!autoPollEnabled.value) return
  try {
    const list = normalizeTaskList(await api.get<TaskSummary[]>(tasksEndpoint()))
    tasks.value = list
    const liveTask = list.find((t) => t.status === 'running')
    if (liveTask && autoFollowRunning.value && selectedTaskId.value !== liveTask.id) {
      selectedTaskId.value = liveTask.id
      await loadTaskTrace(liveTask.id)
      return
    }
    if (selectedTaskId.value) {
      const curr = list.find((t) => t.id === selectedTaskId.value)
      if (
        curr &&
        (curr.status === 'running' ||
          curr.status === 'collecting_facts' ||
          curr.status === 'awaiting_user_facts' ||
          curr.steps_count !== (selectedTaskDetail.value?.step_traces.length || 0) ||
          curr.status !== selectedTaskDetail.value?.status)
      ) {
        await loadTaskTrace(selectedTaskId.value)
      }
    }
  } catch {
    /* 静默跳过单次轮询网络抖动 */
  }
}

async function trackConversationOnCanvas(task: TaskSummary) {
  selectedTaskId.value = task.id
  const ok = await loadTaskTrace(task.id)
  if (!ok) return
  if (task.current_active_node_id) {
    selectedNodeId.value = task.current_active_node_id
  }
  conversationHistoryVisible.value = false
  ElMessage.success(`已切换画布查看评估项目：${cleanDiagText(task.title)}`)
}

async function openStageDeliverables(task?: TaskSummary) {
  if (task && task.id !== selectedTaskId.value) {
    selectedTaskId.value = task.id
    const ok = await loadTaskTrace(task.id)
    if (!ok) return
  } else if (task && !selectedTaskDetail.value) {
    const ok = await loadTaskTrace(task.id)
    if (!ok) return
  }
  if (selectedTaskDetail.value) {
    expandedStageNames.value = [
      'stage-chat',
      ...selectedTaskDetail.value.step_traces.map((tr) => `stage-step-${tr.seq_no}`),
    ]
  }
  stageDeliverablesVisible.value = true
}

function expandAllStages() {
  if (!selectedTaskDetail.value) return
  expandedStageNames.value = [
    'stage-chat',
    ...selectedTaskDetail.value.step_traces.map((tr) => `stage-step-${tr.seq_no}`),
  ]
}

function collapseAllStages() {
  expandedStageNames.value = []
}

async function rerunTaskWorkflow(task: TaskSummary) {
  try {
    const detail = await api.post<TaskDetail>(`/api/admin/agent-studio/tasks/${task.id}/rerun`, {})
    selectedTaskId.value = detail.id
    selectedTaskDetail.value = {
      ...detail,
      title: cleanDiagText(detail.title),
      scenario_label: scenarioLabel(detail.scenario_label, detail.scenario_type),
    }
    await reloadTasksList()
    ElMessage.success(`已启动「${cleanDiagText(task.title)}」的重新评估`)
  } catch (e: unknown) {
    ElMessage.error((e as Error).message || '启动重新评估失败')
  }
}

async function deleteTaskRecord(task: TaskSummary) {
  try {
    await ElMessageBox.confirm(
      `确定删除评估执行记录「${cleanDiagText(task.title)}」吗？`,
      '删除评估执行记录',
      {
        type: 'warning',
        confirmButtonText: '确认删除',
        cancelButtonText: '取消',
      },
    )
  } catch {
    return
  }
  try {
    await api.delete(`/api/admin/agent-studio/tasks/${task.id}`)
    ElMessage.success(`已删除评估执行记录「${cleanDiagText(task.title)}」`)
    const list = normalizeTaskList(await api.get<TaskSummary[]>(tasksEndpoint()))
    tasks.value = list
    if (selectedTaskId.value === task.id) {
      selectedTaskId.value = list[0]?.id || ''
      if (selectedTaskId.value) {
        await loadTaskTrace(selectedTaskId.value)
      } else {
        selectedTaskDetail.value = null
      }
    }
  } catch (e: unknown) {
    ElMessage.error((e as Error).message || '删除失败')
  }
}

function formatTime(iso?: string): string {
  if (!iso) return '-'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  return localTime(d.toISOString())
}

function selectNode(nodeId: string) {
  inspectorOpen.value = true
  selectedNodeId.value = nodeId
}

onMounted(() => {
  nodeObserver = new ResizeObserver((entries) => {
    const heights = { ...nodeHeights.value }
    let changed = false
    for (const entry of entries) {
      const element = entry.target as HTMLElement
      const id = element.dataset.nodeId
      if (id && element.offsetHeight && heights[id] !== element.offsetHeight) {
        heights[id] = element.offsetHeight
        changed = true
      }
    }
    if (changed) {
      nodeHeights.value = heights
      if (!userZoomed.value && !drag) void fitCanvas()
    }
  })
  for (const element of nodeElements.values()) nodeObserver.observe(element)
  viewportObserver = new ResizeObserver(() => {
    if (!userZoomed.value && !drag) void fitCanvas()
  })
  if (canvasScroll.value) viewportObserver.observe(canvasScroll.value)
  void Promise.resolve(loadStudioData()).then(() => {
    void fitCanvas()
    window.setTimeout(() => {
      if (!userZoomed.value) void fitCanvas()
    }, 180)
  })
  connectLiveEvents()
  pollTimer = window.setInterval(() => {
    if (progressState.value !== 'live') void pollLiveWorkflows()
  }, 2000)
})

onUnmounted(() => {
  progressStream?.close()
  clearTimeout(progressRefresh)
  cancelDrag()
  nodeObserver?.disconnect()
  viewportObserver?.disconnect()
  nodeElements.clear()
  if (pollTimer !== undefined) window.clearInterval(pollTimer)
})
</script>

<template>
  <div class="agent-studio-wrap">
    <header class="page-heading">
      <div>
        <h1 class="workflow-title">评估执行记录</h1>
        <p class="workflow-sub">查看企业合规评估各环节的执行记录、相关法条匹配情况、原文逐字核验与退回补充依据过程。</p>
      </div>
    </header>
  <div class="agent-studio-page" v-loading="loading">
    <div class="studio-topbar" data-testid="agent-studio-toolbar">
      <div class="topbar-actions">
        <div class="task-selector">
          <span class="ctrl-label">选择评估记录：</span>
          <el-select
            v-model="selectedTaskId"
            aria-label="选择评估记录"
            size="small"
            style="width: 320px"
            data-testid="agent-trace-task-select"
            @change="loadTaskTrace"
          >
            <el-option
              v-for="t in tasks"
              :key="t.id"
              :label="`${t.title}（${STATUS_LABEL_MAP[t.status] || t.status}）`"
              :value="t.id"
            />
          </el-select>
        </div>

        <div class="seg-switch" role="group" aria-label="查看方式">
          <el-button
            size="small"
            data-testid="btn-open-conversation-history"
            @click="conversationHistoryVisible = true"
          >
            全部评估记录（{{ tasks.length }}）
          </el-button><el-button
            size="small"
            data-testid="btn-expand-stage-outputs"
            :disabled="!selectedTaskDetail"
            @click="openStageDeliverables()"
          >
            各环节执行记录
          </el-button>
        </div>
        <el-button size="small" @click="inspectorOpen = !inspectorOpen">{{
          inspectorOpen ? '收起环节说明' : '环节说明'
        }}</el-button>
      </div>
    </div>

    <div class="live-monitor-bar" data-testid="live-monitor-bar">
      <div class="live-monitor-left">
        <span class="live-dot" :class="{ active: runningTasks.length > 0 }" />
        <span class="live-summary" data-testid="live-running-count-badge">评估执行中 {{ runningTasks.length }} 个 · 信息采集中 {{ collectingTasks.length }} 个 · <span data-testid="awaiting-user-facts-count">待补充信息 {{ awaitingFactTasks.length }} 个</span> · 累计记录 {{ tasks.length }} 次 · <span data-testid="admin-progress-transport">{{ progressState === 'live' ? '实时同步中' : '定时同步中' }}</span></span>

        <template v-if="runningTasks.length > 0">
          <div class="live-running-pills">
            <el-button
              v-for="rt in runningTasks"
              :key="rt.id"
              size="small"
              type="primary"
              plain
              data-testid="live-running-task-pill"
              @click="trackConversationOnCanvas(rt)"
            >
              {{ cleanDiagText(rt.title) || '评估项目' }} ·
              {{ plainTitle(rt.current_active_node_title) || '执行中' }}
            </el-button>
          </div>
        </template>
      </div>

      <div class="live-monitor-right">
        <template v-if="selectedTaskDetail">
          <el-tag size="small" :type="STATUS_TAG_TYPE[selectedTaskDetail.status] || 'info'">
            {{ STATUS_LABEL_MAP[selectedTaskDetail.status] || selectedTaskDetail.status }}
          </el-tag>
          <el-tag
            v-if="selectedTaskDetail.current_active_node_title"
            size="small"
            type="warning"
            effect="dark"
            data-testid="canvas-active-node-banner"
          >
            当前环节：{{ plainTitle(selectedTaskDetail.current_active_node_title) }}
          </el-tag>
        </template>
        <el-switch
          aria-label="自动同步"
          v-model="autoPollEnabled"
          active-text="自动同步"
        />
        <el-button size="small" @click="reloadTasksList">刷新</el-button>
      </div>
    </div>
    <!-- 主体：左侧流程节点连线画布 + 右侧只读详情与轨迹栏 -->
    <div class="studio-workspace">
      <!-- 左侧画布 -->
      <div class="canvas-wrapper" data-testid="dify-workflow-canvas">
        <div class="canvas-floating-bar">
          <el-tag size="small" effect="plain">
            共 {{ workflow?.nodes.length || 0 }} 个环节
          </el-tag>
          <el-tag size="small" effect="plain" type="success">
            流程衔接 {{ workflow?.edges.length || 0 }} 条
          </el-tag>
          <el-popover v-if="selectedTaskDetail" trigger="click" placement="bottom" :width="320"
            ><template #reference><el-button size="small" text>执行统计</el-button></template>
            <div class="task-metrics-list">
              <el-tag size="small" type="primary">
                交互轮次：{{ selectedTaskDetail.messages.length }} 条
              </el-tag>
              <el-tag size="small" type="primary">
                检索与核验调用：{{ selectedTaskDetail.mcp_calls_count }} 次
              </el-tag>
              <el-tag size="small" type="warning">
                退回补充依据：{{ selectedTaskDetail.backtrack_count }} 次
              </el-tag>
              <el-tag size="small" type="info">
                总用时：{{ humanDuration(selectedTaskDetail.total_duration_ms) }}
              </el-tag>
            </div></el-popover
          >
          <div class="zoom-controls">
            <el-button size="small" @click="resetLayout" data-testid="btn-reset-layout"
              >恢复布局</el-button
            >
            <el-button size="small" @click="fitCanvas(true)" data-testid="btn-fit-canvas"
              >适应画布</el-button
            >
            <el-button
              size="small"
              circle
              aria-label="缩小画布"
              @click="userZoomed = true; canvasScale = Math.max(0.5, +(canvasScale - 0.1).toFixed(2))"
            >
              -
            </el-button>
            <span class="zoom-text">{{ Math.round(canvasScale * 100) }}%</span>
            <el-button
              size="small"
              circle
              aria-label="放大画布"
              @click="userZoomed = true; canvasScale = Math.min(1.25, +(canvasScale + 0.1).toFixed(2))"
            >
              +
            </el-button>
          </div>
        </div>

        <div class="canvas-scroll-area" ref="canvasScroll">
          <div
            class="canvas-extent"
            :style="{
              width: `${stageSize.width * canvasScale}px`,
              height: `${stageSize.height * canvasScale}px`,
            }"
          >
            <div
              class="canvas-stage"
              :style="{
                width: `${stageSize.width}px`,
                height: `${stageSize.height}px`,
                transform: `scale(${canvasScale})`,
                transformOrigin: '0 0',
                '--cz': String(canvasScale),
              }"
            >
              <!-- SVG 连线层 -->
              <svg class="edges-svg" :width="stageSize.width" :height="stageSize.height">
                <defs>
                  <marker
                    id="arrow-forward"
                    viewBox="0 0 10 10"
                    refX="8"
                    refY="5"
                    markerWidth="6"
                    markerHeight="6"
                    orient="auto-start-reverse"
                  >
                    <path d="M 0 1 L 10 5 L 0 9 z" fill="#3b82f6" />
                  </marker>
                  <marker
                    id="arrow-backtrack"
                    viewBox="0 0 10 10"
                    refX="8"
                    refY="5"
                    markerWidth="6"
                    markerHeight="6"
                    orient="auto-start-reverse"
                  >
                    <path d="M 0 1 L 10 5 L 0 9 z" fill="#ea580c" />
                  </marker>
                  <marker
                    id="arrow-event"
                    viewBox="0 0 10 10"
                    refX="8"
                    refY="5"
                    markerWidth="6"
                    markerHeight="6"
                    orient="auto-start-reverse"
                  >
                    <path d="M 0 1 L 10 5 L 0 9 z" fill="#ca8a04" />
                  </marker>
                </defs>

                <g v-for="edge in computedSvgEdges" :key="edge.edge_id" class="edge-group">
                  <path
                    :d="edge.path"
                    fill="none"
                    :stroke="
                      edge.edge_type === 'backtrack'
                        ? '#ea580c'
                        : edge.edge_type === 'event_trigger'
                          ? '#ca8a04'
                          : '#3b82f6'
                    "
                    :stroke-width="edge.edge_type === 'backtrack' ? 2.6 : 2.2"
                    :stroke-dasharray="edge.edge_type === 'backtrack' ? '6,4' : undefined"
                    :marker-end="
                      edge.edge_type === 'backtrack'
                        ? 'url(#arrow-backtrack)'
                        : edge.edge_type === 'event_trigger'
                          ? 'url(#arrow-event)'
                          : 'url(#arrow-forward)'
                    "
                  />
                  <g class="edge-label" :transform="`translate(${edge.labelX}, ${edge.labelY})`">
                    <g :transform="`scale(${1 / canvasScale})`">
                    <rect
                      x="-78"
                      y="-11"
                      width="156"
                      height="20"
                      rx="10"
                      :fill="edge.edge_type === 'backtrack' ? '#fff7ed' : '#f8fafc'"
                      :stroke="edge.edge_type === 'backtrack' ? '#fdba74' : '#cbd5e1'"
                    />
                    <text
                      text-anchor="middle"
                      dominant-baseline="middle"
                      font-size="13"
                      :fill="edge.edge_type === 'backtrack' ? '#c2410c' : '#334155'"
                      font-weight="600"
                    >
                      {{ plainTitle(edge.label) }}
                    </text>
                  </g>
                  </g>
                </g>
              </svg>

              <!-- Dify 风格节点卡片层 -->
              <div
                v-for="node in workflow?.nodes || []"
                :key="node.node_id"
                :ref="(el) => registerNode(node.node_id, el)"
                class="dify-node-card"
                :class="{
                  active: selectedNode?.node_id === node.node_id,
                  'is-dragging': draggingNodeId === node.node_id,
                  'has-backtrack': nodeTraceStatsMap.get(node.node_id)?.hasBacktrack,
                  'is-live-running':
                    selectedTaskDetail?.workflow_version_id === workflow?.id &&
                    selectedTaskDetail?.status === 'running' &&
                    selectedTaskDetail?.current_active_node_id === node.node_id,
                  'is-collecting':
                    selectedTaskDetail?.status === 'collecting_facts' &&
                    selectedTaskDetail?.current_active_node_id === node.node_id,
                }"
                :style="{
                  left: `${positionOf(node).x}px`,
                  top: `${positionOf(node).y}px`,
                  width: `${NODE_WIDTH}px`,
                  minHeight: `${NODE_HEIGHT}px`,
                }"
                data-testid="dify-node-card"
                :data-node-id="node.node_id"
                role="button"
                tabindex="0"
                :aria-label="`${node.title}，可拖动或使用方向键调整位置，按回车查看详情`"
                
                @pointerdown="startNodeDrag($event, node)"
                @pointermove="moveNodeDrag"
                @pointerup="finishNodeDrag"
                @pointercancel="cancelDrag"
                @lostpointercapture="finishNodeDrag"
                @keydown="moveNodeWithKeyboard($event, node)"
                @click="clickNode(node.node_id)"
              >
                <!-- 左侧输入端口圆点 -->
                <span
                  v-if="node.node_type !== 'trigger'"
                  class="port-dot port-in"
                  :style="{ borderColor: node.color }"
                />
                <!-- 右侧输出端口圆点 -->
                <span class="port-dot port-out" :style="{ borderColor: node.color }" />

                <div class="node-card-header" :style="{ borderTop: `3px solid ${node.color}` }">
                  <span
                    class="node-type-pill"
                    :style="{
                      backgroundColor: `${node.color}18`,
                      color: node.color,
                    }"
                  >
                    {{ plainTitle(node.category_label) }}
                  </span>
                </div>

                <div class="node-card-title">{{ plainTitle(node.title) }}</div>
                <div class="node-card-subtitle">{{ cleanDiagText(node.responsibility_desc) }}</div>

                <div class="node-card-meta">
                  <div v-if="node.mcp_tools.length" class="meta-row">
                    <span class="meta-key">法规检索与核验：</span>
                    <span class="meta-val">{{ node.mcp_tools.length }} 项能力</span>
                  </div>
                </div>

                <!-- 实时运行中指示条 -->
                <div
                  v-if="
                    selectedTaskDetail?.workflow_version_id === workflow?.id &&
                    selectedTaskDetail?.status === 'running' &&
                    selectedTaskDetail?.current_active_node_id === node.node_id
                  "
                  class="node-live-running-banner"
                  data-testid="node-live-running-badge"
                >
                  正在执行本环节...
                </div>
                <div
                  v-else-if="
                    selectedTaskDetail?.status === 'collecting_facts' &&
                    selectedTaskDetail?.current_active_node_id === node.node_id
                  "
                  class="node-collecting-banner"
                >
                  业务信息采集中（{{ selectedTaskDetail.sufficiency_score }}%）
                </div>
                <div
                  v-else-if="
                    selectedTaskDetail?.workflow_version_id === workflow?.id &&
                    selectedTaskDetail?.status === 'awaiting_user_facts' &&
                    selectedTaskDetail?.current_active_node_id === node.node_id
                  "
                  class="node-collecting-banner"
                  data-testid="node-awaiting-user-facts-badge"
                >
                  待补充情况
                </div>

                <!-- 叠加任务运行耗时与退回状态条 -->
                <div v-if="nodeTraceStatsMap.get(node.node_id)" class="node-trace-footer">
                  <el-tag
                    size="small"
                    :type="
                      nodeTraceStatsMap.get(node.node_id)?.status === 'skipped' ? 'info'
                      : nodeTraceStatsMap.get(node.node_id)?.status === 'failed' ? 'danger'
                      : nodeTraceStatsMap.get(node.node_id)?.status === 'awaiting_user_facts' ? 'warning'
                      : nodeTraceStatsMap.get(node.node_id)?.hasBacktrack ? 'warning' : 'success'
                    "
                  >
                    {{
                      nodeTraceStatsMap.get(node.node_id)?.hasBacktrack
                        ? `执行 ${nodeTraceStatsMap.get(node.node_id)?.runs} 轮（含退回重算）`
                        : nodeTraceStatsMap.get(node.node_id)?.status === 'skipped' ? '已跳过'
                        : nodeTraceStatsMap.get(node.node_id)?.status === 'failed' ? '未完成'
                        : nodeTraceStatsMap.get(node.node_id)?.status === 'awaiting_user_facts' ? '待补充情况'
                        : '已完成'
                    }}
                  </el-tag>
                  <span class="trace-ms">
                    {{ humanDuration(nodeTraceStatsMap.get(node.node_id)?.duration_ms) }}
                  </span>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>

      <!-- 右侧环节说明与本环节执行记录栏（只读） -->
      <div
        v-if="selectedNode && inspectorOpen"
        class="inspector-sidebar"
        data-testid="dify-inspector-panel"
      >
        <div class="inspector-header">
          <div class="inspector-title-row">
            <span class="inspector-dot" :style="{ backgroundColor: selectedNode.color }" />
            <span class="inspector-node-title" data-testid="inspector-node-title">
              {{ plainTitle(selectedNode.title) }}
            </span>
          </div>
          <div class="inspector-node-sub">
            {{ plainTitle(selectedNode.category_label) }}
          </div>
        </div>

        <el-tabs v-model="inspectorTab" class="inspector-tabs">
          <!-- Tab 1: 环节职责、上下游衔接与输入产出 -->
          <el-tab-pane label="环节职责与衔接" name="config">
            <div class="inspector-section">
              <div class="sec-title">职责说明</div>
              <p class="sec-desc">{{ cleanDiagText(selectedNode.responsibility_desc) }}</p>
            </div>

            <div class="inspector-section">
              <div class="sec-title">上下游衔接关系</div>
              <div class="link-group">
                <div class="link-sub">上一环节（{{ upstreamEdges.length }}）：</div>
                <div v-if="upstreamEdges.length === 0" class="empty-link">
                  无（当前为起始环节）
                </div>
                <div
                  v-for="e in upstreamEdges"
                  :key="e.edge_id"
                  class="link-item"
                  @click="selectNode(e.source_node_id)"
                >
                  <el-tag size="small" :type="e.edge_type === 'backtrack' ? 'warning' : 'info'">
                    {{ plainTitle(e.label) }}
                  </el-tag>
                  <span class="link-node-name"> ← {{ plainTitle(nodeMap.get(e.source_node_id)?.title) }} </span>
                  <div class="link-cond">{{ cleanDiagText(e.condition_desc) }}</div>
                </div>
              </div>

              <div class="link-group" style="margin-top: 8px">
                <div class="link-sub">下一环节（{{ downstreamEdges.length }}）：</div>
                <div v-if="downstreamEdges.length === 0" class="empty-link">
                  无（当前为收尾环节）
                </div>
                <div
                  v-for="e in downstreamEdges"
                  :key="e.edge_id"
                  class="link-item"
                  @click="selectNode(e.target_node_id)"
                >
                  <el-tag size="small" :type="e.edge_type === 'backtrack' ? 'danger' : 'success'">
                    {{ plainTitle(e.label) }}
                  </el-tag>
                  <span class="link-node-name"> → {{ plainTitle(nodeMap.get(e.target_node_id)?.title) }} </span>
                  <div class="link-cond">{{ cleanDiagText(e.condition_desc) }}</div>
                </div>
              </div>
            </div>

            <div v-if="selectedNode.mcp_tools.length > 0" class="inspector-section">
              <div class="sec-title">法规检索与核验能力</div>
              <div v-for="tool in selectedNode.mcp_tools" :key="tool.tool_name" class="mcp-tool-card">
                <div class="tool-header">
                  <span class="tool-title">{{ cleanDiagText(tool.display_name) }}</span>
                </div>
                <p class="tool-desc">{{ cleanDiagText(tool.description) }}</p>
              </div>
            </div>

            <div class="inspector-section">
              <div class="sec-title">本环节输入与产出信息</div>
              <div class="io-title">输入信息</div>
              <el-table
                :data="selectedNode.input_variables"
                size="small"
                border
                style="margin-bottom: 10px"
              >
                <el-table-column label="信息内容说明">
                  <template #default="{ row }">{{ cleanDiagText(row.desc) }}</template>
                </el-table-column>
              </el-table>
              <div class="io-title">产出信息</div>
              <el-table :data="selectedNode.output_variables" size="small" border>
                <el-table-column label="产出结果说明">
                  <template #default="{ row }">{{ cleanDiagText(row.desc) }}</template>
                </el-table-column>
              </el-table>
            </div>
          </el-tab-pane>

          <!-- Tab 2: 本环节执行记录 -->
          <el-tab-pane :label="`本环节执行记录（${selectedNodeTraces.length}）`" name="trace">
            <div v-if="selectedTaskDetail" class="inspector-trace-top">
              <div>
                <div style="font-size: 13px; font-weight: 600; color: #0f172a">
                  {{ cleanDiagText(selectedTaskDetail.title) }}
                </div>
                <div style="font-size: 13px; color: #64748b">
                  {{ COUNTRY_NAMES[selectedTaskDetail.country_code] || selectedTaskDetail.country_name_zh || '未定法域' }} ·
                  {{ STATUS_LABEL_MAP[selectedTaskDetail.status] || selectedTaskDetail.status }}
                </div>
              </div>
              <el-button
                size="small"
                data-testid="btn-inspector-expand-all-stages"
                @click="openStageDeliverables()"
              >
                各环节记录详情
              </el-button>
            </div>

            <div v-if="selectedNodeTraces.length === 0" class="empty-state-box">
              <template
                v-if="
                  selectedTaskDetail?.workflow_version_id === workflow?.id &&
                    selectedTaskDetail?.status === 'running' &&
                  selectedTaskDetail?.current_active_node_id === selectedNode.node_id
                "
              >
                本环节正在执行中，完成后将在此记录执行结论与核验明细。
              </template>
              <template v-else>
                当前评估记录在本环节尚无执行明细（或正处于业务信息补充阶段）。
              </template>
            </div>
            <div
              v-for="tr in selectedNodeTraces"
              :key="tr.id"
              class="trace-step-card"
              :class="{ 'trace-backtrack': tr.is_backtrack }"
              data-testid="inspector-trace-step-card"
            >
              <div class="trace-step-head">
                <el-tag size="small" :type="tr.is_backtrack ? 'danger' : 'success'">
                  步骤 #{{ tr.seq_no }} ·
                  {{ PHASE_LABEL_MAP[tr.phase] || tr.phase }}
                </el-tag>
                <span class="trace-metrics">
                  用时 {{ seconds(tr.duration_ms) }}
                </span>
              </div>
              <div class="trace-summary">{{ cleanDiagText(tr.business_summary) }}</div>

              <div v-if="tr.is_backtrack" class="backtrack-detail-box">
                <div><strong>退回原因：</strong>{{ cleanDiagText(tr.backtrack_reason) }}</div>
                <div style="margin-top: 4px">
                  <strong>补充核对结果：</strong>{{ cleanDiagText(tr.backtrack_resolution) }}
                </div>
              </div>

              <div v-if="tr.tool_calls.length > 0" class="trace-tools-list">
                <div class="sub-label">检索与核验步骤：</div>
                <div v-for="tc in tr.tool_calls" :key="tc.call_id" class="trace-tool-item">
                  <div class="tc-top">
                    <el-tag size="small" effect="plain">
                      {{ cleanDiagText(tc.tool_label) || '法规检索与核验' }}
                    </el-tag>
                    <span>{{ seconds(tc.duration_ms) }}</span>
                  </div>
                  <div class="tc-res">结果摘要：{{ cleanDiagText(tc.result_summary) }}</div>
                </div>
              </div>
            </div>
          </el-tab-pane>
        </el-tabs>
      </div>
    </div>

    <!-- 全部评估执行记录抽屉 -->
    <el-drawer
      v-model="conversationHistoryVisible"
      title="全部评估执行记录"
      size="min(95vw, 1320px)"
    >
      <div data-testid="conversation-history-drawer">
        <div class="history-filter-bar">
          <el-input
            v-model="historySearchKeyword"
            size="small"
            clearable
            placeholder="搜索项目标题 / 车型 / 企业 / 发起人"
            style="width: 300px"
            data-testid="conv-history-search"
          />
          <el-select
            v-model="historyFilterStatus"
            size="small"
            clearable
            placeholder="按评估状态筛选"
            style="width: 170px"
            data-testid="conv-history-status-select"
          >
            <el-option label="全部状态" value="" />
            <el-option label="实时运行中" value="running" />
            <el-option label="业务信息采集中" value="collecting_facts" />
            <el-option label="待补充情况" value="awaiting_user_facts" />
            <el-option label="已出报告" value="completed" />
            <el-option label="执行失败" value="failed" />
          </el-select>
          <el-select
            v-model="historyFilterCountry"
            size="small"
            clearable
            placeholder="按国家筛选"
            style="width: 150px"
          >
            <el-option label="全部国家" value="" />
            <el-option
              v-for="opt in historyCountryOptions"
              :key="opt.code"
              :label="opt.label"
              :value="opt.code"
            />
          </el-select>
          <el-checkbox
            v-model="showArchived"
            data-testid="conv-history-show-archived"
            @change="reloadTasksList"
          >
            显示已归档记录
          </el-checkbox>
          <el-checkbox v-model="autoFollowRunning">
            新评估启动时自动跟随画布
          </el-checkbox>
          <el-button size="small" type="primary" plain @click="reloadTasksList">
            立即同步
          </el-button>
        </div>

        <el-table
          :data="filteredHistoryTasks"
          border
          stripe
          size="small"
          data-testid="conv-history-table"
        >
          <el-table-column label="评估项目与业务场景" min-width="190">
            <template #default="{ row }">
              <div style="font-weight: 600">{{ cleanDiagText(row.title) }}</div>
              <div class="cell-muted">
                {{ COUNTRY_NAMES[row.country_code] || row.country_name_zh || '未定法域' }} ·
                {{ row.vehicle_model || '待填写车型' }} ·
                {{ scenarioLabel(row.scenario_label, row.scenario_type) }}
              </div>
            </template>
          </el-table-column>

          <el-table-column label="所属企业 / 发起用户" min-width="165">
            <template #default="{ row }">
              <div style="font-weight: 600">
                {{ row.org_name || '示范企业' }}
              </div>
              <div class="cell-muted">
                {{ row.creator_name || '企业用户' }}
                <span v-if="row.creator_email">（{{ row.creator_email }}）</span>
              </div>
            </template>
          </el-table-column>

          <el-table-column label="评估状态与环节进度" min-width="185">
            <template #default="{ row }">
              <div>
                <el-tag size="small" :type="STATUS_TAG_TYPE[row.status] || 'info'">
                  {{ STATUS_LABEL_MAP[row.status] || row.status }}
                </el-tag>
                <el-tag size="small" type="info" style="margin-left: 4px">
                  信息完备度 {{ row.sufficiency_score }}%
                </el-tag>
              </div>
              <div v-if="row.current_active_node_title" class="cell-active-node">
                {{ plainTitle(row.current_active_node_title) }}
              </div>
              <div v-else class="cell-muted">
                已完成 {{ row.steps_count }} 个环节 · 合规结论 {{ row.findings_count }} 条
              </div>
            </template>
          </el-table-column>

          <el-table-column label="问答轮次与用时" min-width="176">
            <template #default="{ row }">
              <div>交互问答：{{ row.messages_count }} 条</div>
              <div class="cell-muted">
                用时 {{ seconds(row.total_duration_ms) }} · 退回重算 {{ row.backtrack_count }} 次
              </div>
            </template>
          </el-table-column>

          <el-table-column label="最后更新时间" width="148" class-name="cy-col-nowrap">
            <template #default="{ row }">
              {{ formatTime(row.updated_at) }}
            </template>
          </el-table-column>

          <el-table-column label="操作" width="226" fixed="right" class-name="cy-col-nowrap">
            <template #default="{ row }">
              <div class="row-actions-inline">
                <el-button
                  link
                  type="primary"
                  size="small"
                  data-testid="btn-track-on-canvas"
                  @click="trackConversationOnCanvas(row)"
                >
                  画布查看
                </el-button>
                <el-button
                  link
                  type="primary"
                  size="small"
                  data-testid="btn-inspect-stages"
                  @click="openStageDeliverables(row)"
                >
                  各环节记录
                </el-button>
                <el-dropdown trigger="click">
                  <el-button link size="small" data-testid="btn-more-workflow-actions">更多</el-button>
                  <template #dropdown>
                    <el-dropdown-menu>
                      <el-dropdown-item
                        v-if="row.sufficiency_score >= 100 && !['running','awaiting_user_facts'].includes(row.status)"
                        data-testid="btn-rerun-workflow"
                        @click="rerunTaskWorkflow(row)"
                      >
                        重新评估
                      </el-dropdown-item>
                      <el-dropdown-item
                        data-testid="btn-delete-workflow"
                        @click="deleteTaskRecord(row)"
                      >
                        删除记录
                      </el-dropdown-item>
                    </el-dropdown-menu>
                  </template>
                </el-dropdown>
              </div>
            </template>
          </el-table-column>
        </el-table>
      </div>
    </el-drawer>

    <!-- 各环节执行记录详情抽屉 -->
    <el-drawer
      v-model="stageDeliverablesVisible"
      append-to-body
      :title="selectedTaskDetail ? `各环节执行记录详情 · ${cleanDiagText(selectedTaskDetail.title)}` : '各环节执行记录详情'"
      size="min(92vw, 1160px)"
    >
      <div v-if="selectedTaskDetail" data-testid="stage-deliverables-drawer">
        <div class="stage-drawer-top">
          <div class="stage-meta-info">
            <el-tag type="primary" effect="dark" size="small">
              {{ cleanDiagText(selectedTaskDetail.title) }}
            </el-tag>
            <el-tag :type="STATUS_TAG_TYPE[selectedTaskDetail.status] || 'info'" size="small">
              {{ STATUS_LABEL_MAP[selectedTaskDetail.status] || selectedTaskDetail.status }}
            </el-tag>
            <span class="stage-meta-text">
              企业：{{ selectedTaskDetail.org_name }} · 发起人：{{
                selectedTaskDetail.creator_name || selectedTaskDetail.creator_email || '企业用户'
              }}
              · 国家：{{ COUNTRY_NAMES[selectedTaskDetail.country_code] || selectedTaskDetail.country_name_zh }} · 车型：{{
                selectedTaskDetail.vehicle_model || '待填写'
              }}
            </span>
          </div>
          <div class="stage-drawer-actions">
            <el-button
              size="small"
              data-testid="btn-expand-all-stages"
              @click="expandAllStages"
            >
              全部展开
            </el-button>
            <el-button size="small" @click="collapseAllStages">全部收起</el-button>
          </div>
        </div>

        <el-alert
          v-if="selectedTaskDetail.status === 'running'"
          type="info"
          :closable="false"
          show-icon
          style="margin-bottom: 12px"
        >
          <template #title>
            当前评估正在执行中（当前环节：{{
              plainTitle(selectedTaskDetail.current_active_node_title) || '执行中'
            }}），每完成一个环节将自动更新最新记录。
          </template>
        </el-alert>

        <el-collapse v-model="expandedStageNames">
          <!-- 环节 0：对话交互与业务信息采集 -->
          <el-collapse-item name="stage-chat" data-testid="stage-collapse-chat">
            <template #title>
              <div class="stage-collapse-header">
                <el-tag size="small" type="primary">环节 0 · 对话交互与业务信息采集</el-tag>
                <b
                  >对话问答记录（{{ selectedTaskDetail.messages.length }}
                  条）与业务情况采集明细（完备度
                  {{ selectedTaskDetail.sufficiency_score }}%）</b
                >
              </div>
            </template>

            <div class="stage-deliverable-box">
              <div class="sub-label">一、对话交互问答记录：</div>
              <div v-if="selectedTaskDetail.messages.length === 0" class="cell-muted">
                该记录由评估项目表单直接带入初始业务信息，未发生多轮问答对话。
              </div>
              <div v-else class="chat-transcript-list">
                <div
                  v-for="m in selectedTaskDetail.messages"
                  :key="m.id"
                  class="transcript-item"
                  :class="m.role"
                >
                  <el-tag size="small" :type="m.role === 'user' ? 'success' : 'info'">
                    {{ m.role === 'user' ? '企业填写' : '合规助手' }}
                  </el-tag>
                  <span class="transcript-content">{{ cleanDiagText(m.content) }}</span>
                </div>
              </div>

              <div class="sub-label" style="margin-top: 12px">
                二、八方面必填业务信息采集表：
              </div>
              <el-table :data="selectedTaskDetail.missing_facts" border size="small">
                <el-table-column label="所属章节" width="210">
                  <template #default="{ row }">{{ dimLabel(row.dimension_code) }}</template>
                </el-table-column>
                <el-table-column label="核实事项" min-width="240">
                  <template #default="{ row }">{{ cleanDiagText(row.question_zh) }}</template>
                </el-table-column>
                <el-table-column label="已确认的业务情况" min-width="240">
                  <template #default="{ row }">
                    <span v-if="row.current_value">{{ scenarioLabel(row.current_value) }}</span>
                    <span v-else class="cell-muted">（待补充）</span>
                  </template>
                </el-table-column>
                <el-table-column label="状态" width="95">
                  <template #default="{ row }">
                    <el-tag size="small" :type="row.is_fulfilled ? 'success' : 'warning'">
                      {{ row.is_fulfilled ? '已采集' : '待补充' }}
                    </el-tag>
                  </template>
                </el-table-column>
              </el-table>
            </div>
          </el-collapse-item>

          <!-- 环节 1..N：每个环节的执行记录 -->
          <el-collapse-item
            v-for="tr in selectedTaskDetail.step_traces"
            :key="tr.id"
            :name="`stage-step-${tr.seq_no}`"
            :data-testid="`stage-collapse-step-${tr.seq_no}`"
          >
            <template #title>
              <div class="stage-collapse-header">
                <el-tag
                  size="small"
                  :type="
                    tr.is_backtrack ? 'danger' : tr.status === 'passed' ? 'success' : 'warning'
                  "
                >
                  步骤 #{{ tr.seq_no }} ·
                  {{ PHASE_LABEL_MAP[tr.phase] || tr.phase }}
                </el-tag>
                <b>{{ plainTitle(tr.agent_name) }}</b>
                <span class="cell-muted">
                  （用时 {{ seconds(tr.duration_ms) }}）
                </span>
              </div>
            </template>

            <div class="stage-deliverable-box">
              <div class="stage-summary-line">
                <strong>本环节执行结论：</strong>{{ cleanDiagText(tr.business_summary) }}
              </div>

              <div v-if="tr.is_backtrack" class="backtrack-detail-box" style="margin-bottom: 10px">
                <div><strong>退回核对原因：</strong>{{ cleanDiagText(tr.backtrack_reason) }}</div>
                <div v-if="tr.backtrack_resolution" style="margin-top: 4px">
                  <strong>补充依据核对结果：</strong>{{ cleanDiagText(tr.backtrack_resolution) }}
                </div>
              </div>

              <!-- 针对法规检索环节：展示每个章节匹配出的候选条款明细 -->
              <template
                v-if="tr.phase === 'retrieval' && tr.output_snapshot.retrieved_clauses_by_dimension"
              >
                <div class="sub-label">本环节匹配的各章法规条款明细：</div>
                <div
                  v-for="(clauses, dimCode) in tr.output_snapshot
                    .retrieved_clauses_by_dimension as Record<
                    string,
                    Array<Record<string, unknown>>
                  >"
                  :key="dimCode"
                  class="dim-output-card"
                >
                  <div class="dim-output-title">
                    <b>{{ dimLabel(dimCode) }}</b> · 匹配法规条款 {{ clauses.length }} 条
                  </div>
                  <el-table v-if="clauses.length > 0" :data="clauses" border size="small">
                    <el-table-column prop="document_title" label="法规文件" min-width="180" />
                    <el-table-column prop="clause_number" label="条款号" width="110" />
                    <el-table-column prop="snippet" label="原文片段摘录" min-width="280" />
                  </el-table>
                </div>
              </template>

              <!-- 针对分析环节：展示每个章节的分析初稿产出 -->
              <template
                v-if="
                  (tr.phase === 'analysis' || tr.phase === 're_analysis') &&
                  tr.output_snapshot.dimension_outputs
                "
              >
                <div class="sub-label">本环节各章合规分析记录：</div>
                <div
                  v-for="(dimOut, dimCode) in tr.output_snapshot.dimension_outputs as Record<
                    string,
                    Record<string, unknown>
                  >"
                  :key="dimCode"
                  class="dim-output-card"
                >
                  <div class="dim-output-title">
                    <b>{{ dimLabel(dimCode, dimOut.dimension_title) }}</b>
                    <el-tag
                      size="small"
                      :type="Number(dimOut.findings_count || 0) > 0 ? 'success' : 'warning'"
                      style="margin-left: 8px"
                    >
                      形成结论 {{ dimOut.findings_count || 0 }} 条
                    </el-tag>
                  </div>
                  <div v-if="dimOut.summary" style="margin: 6px 0; font-size: 13px">
                    <strong>章节小结：</strong>{{ cleanDiagText(dimOut.summary) }}
                  </div>
                  <div v-if="dimOut.gaps" class="cell-muted" style="margin-bottom: 6px">
                    <strong>补充说明：</strong>{{ cleanDiagText(dimOut.gaps) }}
                  </div>
                  <el-table
                    v-if="Array.isArray(dimOut.findings) && dimOut.findings.length > 0"
                    :data="dimOut.findings as Array<Record<string, unknown>>"
                    border
                    size="small"
                  >
                    <el-table-column label="对应业务情况" min-width="140">
                      <template #default="{ row }">{{ cleanDiagText(row.business_fact) }}</template>
                    </el-table-column>
                    <el-table-column label="提取合规义务" min-width="180">
                      <template #default="{ row }">{{ cleanDiagText(row.obligation) }}</template>
                    </el-table-column>
                    <el-table-column label="风险等级" width="90">
                      <template #default="{ row }">{{ riskLabel(row.risk_level) }}</template>
                    </el-table-column>
                    <el-table-column label="引用法规原文片段" min-width="220">
                      <template #default="{ row }">{{ cleanDiagText(row.quote) }}</template>
                    </el-table-column>
                  </el-table>
                </div>
              </template>

              <!-- 针对核验环节：展示每条结论的原文与数额核对产出 -->
              <template
                v-if="
                  (tr.phase === 'verification' || tr.phase === 're_verification') &&
                  tr.output_snapshot.verification_items
                "
              >
                <div class="sub-label">
                  本环节条文原文与数额核验明细（核验通过
                  {{ tr.output_snapshot.verified_passed_count ?? (tr.output_snapshot.kept_sentences || 0) }} 条 / 核验重生成
                  {{ tr.output_snapshot.verified_failed_count ?? (tr.output_snapshot.dropped_sentences || 0) }} 条）：
                </div>
                <el-table
                  :data="tr.output_snapshot.verification_items as Array<Record<string, unknown>>"
                  border
                  size="small"
                >
                  <el-table-column label="所属章节" width="190">
                    <template #default="{ row }">{{ dimLabel(row.dimension_code) }}</template>
                  </el-table-column>
                  <el-table-column prop="document_title" label="法规文件" min-width="160" />
                  <el-table-column prop="clause_number" label="条款号" width="95" />
                  <el-table-column label="合规义务与数额核对" min-width="180">
                    <template #default="{ row }">{{ cleanDiagText(row.obligation) }}</template>
                  </el-table-column>
                  <el-table-column prop="quote" label="核对原文片段" min-width="200" />
                  <el-table-column label="核验结果" width="105">
                    <template #default="{ row }">
                      <el-tag size="small" :type="row.passed ? 'success' : 'danger'">
                        {{ row.passed ? '核验通过' : '核验重生成' }}
                      </el-tag>
                    </template>
                  </el-table-column>
                </el-table>
              </template>

              <!-- 针对报告编制环节：展示八章报告内容产出 -->
              <template
                v-if="tr.phase === 'compilation' && tr.output_snapshot.dimensions_deliverables"
              >
                <div class="sub-label">
                  本环节编制的八章报告内容明细：
                </div>
                <el-table
                  :data="
                    tr.output_snapshot.dimensions_deliverables as Array<Record<string, unknown>>
                  "
                  border
                  size="small"
                >
                  <el-table-column label="章节标题" width="230">
                    <template #default="{ row }">{{ dimLabel(row.dimension_code, row.dimension_title) }}</template>
                  </el-table-column>
                  <el-table-column label="状态" width="100">
                    <template #default="{ row }">
                      <el-tag
                        size="small"
                        :type="row.status === 'COMPLETE' ? 'success' : 'warning'"
                      >
                        {{ row.status === 'COMPLETE' ? '依据已核验' : '待补充信息' }}
                      </el-tag>
                    </template>
                  </el-table-column>
                  <el-table-column label="风险等级" width="100">
                    <template #default="{ row }">{{ riskLabel(row.risk_level) }}</template>
                  </el-table-column>
                  <el-table-column prop="citations_count" label="引用条文数" width="95" />
                  <el-table-column label="章节正文内容" min-width="280">
                    <template #default="{ row }">{{ plainMarkdown(row.content_markdown) }}</template>
                  </el-table-column>
                </el-table>
              </template>

              <!-- 检索与核验步骤摘要表 -->
              <div v-if="tr.tool_calls.length > 0" style="margin-top: 10px">
                <div class="sub-label">
                  本环节执行的检索与核验步骤（{{ tr.tool_calls.length }} 项）：
                </div>
                <el-table :data="tr.tool_calls" border size="small">
                  <el-table-column label="步骤名称" min-width="170">
                    <template #default="{ row }">
                      {{ cleanDiagText(row.tool_label) || '法规检索与核验' }}
                    </template>
                  </el-table-column>
                  <el-table-column label="执行结果摘要" min-width="260">
                    <template #default="{ row }">{{ cleanDiagText(row.result_summary) }}</template>
                  </el-table-column>
                  <el-table-column label="用时" width="100">
                    <template #default="{ row }">
                      {{ seconds(row.duration_ms) }}
                    </template>
                  </el-table-column>
                </el-table>
              </div>
            </div>
          </el-collapse-item>
        </el-collapse>
      </div>
    </el-drawer>
  </div>
  </div>
</template>

<style scoped>
.runtime-settings { white-space: pre-wrap; overflow-wrap: anywhere; font-size: 13px; max-height: 240px; overflow: auto; }
.agent-studio-page {
  display: flex;
  flex-direction: column;
  height: calc(100vh - 100px);
  min-height: 700px;
  background: #f8fafc;
  border: 1px solid #e2e8f0;
  border-radius: 10px;
  overflow: hidden;
}

.studio-topbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 18px;
  background: #ffffff;
  border-bottom: 1px solid #e2e8f0;
  flex-wrap: wrap;
  gap: 12px;
}

.topbar-left {
  display: flex;
  align-items: center;
  gap: 12px;
}

.studio-badge {
  background: linear-gradient(135deg, #1d4ed8, #4f46e5);
  color: #fff;
  font-weight: 600;
  font-size: 13px;
  padding: 6px 10px;
  border-radius: 6px;
  letter-spacing: 0.3px;
}

.row-actions-inline {
  display: inline-flex;
  align-items: center;
  gap: 12px;
}

.workflow-meta {
  display: flex;
  align-items: center;
  gap: 8px;
}

.topbar-actions {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

.version-selector,
.task-selector {
  display: flex;
  align-items: center;
  gap: 6px;
}

.ctrl-label {
  font-size: 13px;
  color: #475569;
  font-weight: 600;
}

.studio-workspace {
  display: flex;
  flex: 1;
  min-height: 0;
  overflow: hidden;
}

.canvas-wrapper {
  flex: 1;
  position: relative;
  display: flex;
  flex-direction: column;
  background-color: #f1f5f9;
  background-image: radial-gradient(#cbd5e1 1.2px, transparent 1.2px);
  background-size: 20px 20px;
  overflow: hidden;
}

.canvas-floating-bar {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 14px;
  background: rgba(255, 255, 255, 0.92);
  border-bottom: 1px solid #e2e8f0;
  z-index: 5;
  flex-wrap: wrap;
}

.zoom-controls {
  margin-left: auto;
  display: flex;
  align-items: center;
  gap: 6px;
}

.zoom-text {
  font-size: 13px;
  font-weight: 600;
  color: #334155;
  min-width: 42px;
  text-align: center;
}

.canvas-scroll-area {
  flex: 1;
  overflow: auto;
  position: relative;
  padding: 16px;
}

.canvas-stage {
  position: relative;
  width: 1580px;
  height: 550px;
}

.edges-svg {
  position: absolute;
  left: 0;
  top: 0;
  pointer-events: none;
  z-index: 1;
}

.dify-node-card {
  position: absolute;
  z-index: 2;
  background: #ffffff;
  border: 2px solid #e2e8f0;
  border-radius: 12px;
  padding: 10px 12px;
  box-shadow: 0 4px 14px rgba(15, 23, 42, 0.06);
  cursor: pointer;
  transition: all 0.16s ease;
  user-select: none;
}

.dify-node-card:hover {
  transform: translateY(-2px);
  box-shadow: 0 8px 20px rgba(37, 99, 235, 0.14);
}

.dify-node-card.active {
  border-color: #2563eb;
  box-shadow: 0 0 0 3px rgba(37, 99, 235, 0.22);
}

.dify-node-card.has-backtrack {
  border-color: #f97316;
}

.port-dot {
  position: absolute;
  top: 72px;
  width: 10px;
  height: 10px;
  background: #ffffff;
  border: 2.5px solid #2563eb;
  border-radius: 50%;
}

.port-in {
  left: -6px;
}

.port-out {
  right: -6px;
}

.node-card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding-top: 6px;
  margin-bottom: 6px;
}

.node-type-pill {
  font-size: 13px;
  font-weight: 600;
  padding: 2px 8px;
  border-radius: 999px;
}

.node-lock-tag {
  font-size: 13px;
  color: #64748b;
  font-weight: 600;
}

.node-card-title {
  font-size: 14px;
  font-weight: 600;
  color: #0f172a;
  line-height: 1.3;
}

.node-card-subtitle {
  font-size: 13px;
  color: #64748b;
  margin-bottom: 8px;
  line-height: 1.45;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.node-card-meta {
  background: #f8fafc;
  border-radius: 6px;
  padding: 6px 8px;
  font-size: 13px;
  border: 1px solid #f1f5f9;
}

.meta-row {
  display: flex;
  justify-content: space-between;
  line-height: 1.5;
}

.meta-key {
  color: #64748b;
}

.meta-val {
  color: #1e293b;
  font-weight: 600;
}

.node-trace-footer {
  margin-top: 8px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding-top: 6px;
  border-top: 1px dashed #e2e8f0;
}

.trace-ms {
  font-size: 13px;
  color: #475569;
  font-weight: 600;
}

.inspector-sidebar {
  width: 440px;
  background: #ffffff;
  border-left: 1px solid #e2e8f0;
  display: flex;
  flex-direction: column;
  overflow-y: auto;
  padding: 14px 16px;
}

.inspector-header {
  padding-bottom: 10px;
  border-bottom: 1px solid #f1f5f9;
  flex-shrink: 0;
}

.inspector-title-row {
  display: flex;
  align-items: center;
  gap: 8px;
}

.inspector-dot {
  width: 10px;
  height: 10px;
  border-radius: 50%;
}

.inspector-node-title {
  font-size: 16px;
  font-weight: 600;
  color: #0f172a;
}

.inspector-node-sub {
  font-size: 13px;
  color: #64748b;
  margin-top: 4px;
}

.readonly-banner {
  margin: 10px 0;
  flex-shrink: 0;
}

.inspector-section {
  margin-bottom: 16px;
  padding-bottom: 12px;
  border-bottom: 1px solid #f1f5f9;
}

.sec-title {
  font-size: 13px;
  font-weight: 600;
  color: #0f172a;
  margin-bottom: 8px;
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.sec-desc {
  margin: 0;
  font-size: 13px;
  color: #334155;
  line-height: 1.6;
}

.link-sub {
  font-size: 13px;
  font-weight: 600;
  color: #475569;
  margin-bottom: 4px;
}

.link-item {
  background: #f8fafc;
  border: 1px solid #e2e8f0;
  border-radius: 6px;
  padding: 6px 10px;
  margin-bottom: 6px;
  cursor: pointer;
}

.link-item:hover {
  border-color: #3b82f6;
}

.link-node-name {
  font-size: 13px;
  font-weight: 600;
  color: #1e293b;
  margin-left: 6px;
}

.link-cond {
  font-size: 13px;
  color: #64748b;
  margin-top: 3px;
}

.empty-link {
  font-size: 13px;
  color: #64748b;
}

.readonly-form-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 8px;
}

.form-item label {
  display: block;
  font-size: 13px;
  color: #475569;
  margin-bottom: 3px;
}

.code-readonly-box {
  background: #0f172a;
  color: #38bdf8;
  padding: 8px 12px;
  border-radius: 6px;
  font-size: 13px;
}

.io-title {
  font-size: 13px;
  font-weight: 600;
  color: #475569;
  margin-bottom: 4px;
}

.mcp-tool-card {
  background: #f8fafc;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  padding: 10px 12px;
  margin-bottom: 10px;
}

.tool-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 6px;
}

.tool-title {
  font-weight: 600;
  font-size: 13px;
  color: #0f172a;
}

.tool-desc {
  font-size: 13px;
  color: #475569;
  margin: 0 0 8px 0;
}

.tool-schema-row {
  font-size: 13px;
  color: #334155;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.trace-step-card {
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  padding: 10px 12px;
  margin-bottom: 12px;
  background: #f8fafc;
}

.trace-step-card.trace-backtrack {
  border-color: #f97316;
  background: #fff7ed;
}

.trace-step-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 6px;
}

.trace-metrics {
  font-size: 13px;
  color: #475569;
  font-weight: 600;
}

.trace-summary {
  font-size: 13px;
  font-weight: 600;
  color: #0f172a;
  line-height: 1.5;
}

.backtrack-detail-box {
  margin-top: 8px;
  padding: 8px;
  background: #ffedd5;
  border-left: 3px solid #ea580c;
  border-radius: 4px;
  font-size: 13px;
  color: #9a3412;
}

.trace-tools-list {
  margin-top: 8px;
}

.sub-label {
  font-size: 13px;
  font-weight: 600;
  color: #334155;
  margin-bottom: 4px;
}

.trace-tool-item {
  background: #ffffff;
  border: 1px solid #e2e8f0;
  border-radius: 6px;
  padding: 6px 8px;
  margin-bottom: 6px;
  font-size: 13px;
}

.tc-top {
  display: flex;
  justify-content: space-between;
  margin-bottom: 3px;
}

.snapshot-block pre {
  background: #0f172a;
  color: #e2e8f0;
  padding: 6px 8px;
  border-radius: 6px;
  font-size: 13px;
  overflow-x: auto;
  max-height: 140px;
}

.benchmark-summary-cards {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 12px;
}

.bm-card {
  background: #f8fafc;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  padding: 12px;
  text-align: center;
}

.bm-num {
  font-size: 24px;
  font-weight: 600;
  color: #1d4ed8;
}

.bm-label {
  font-size: 13px;
  color: #475569;
  margin-top: 4px;
}

.live-monitor-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 18px;
  background: #f0f9ff;
  border-bottom: 1px solid #bae6fd;
  flex-wrap: wrap;
  gap: 10px;
}

.live-monitor-left,
.live-monitor-right {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.live-running-pills {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}

.live-dot {
  width: 9px;
  height: 9px;
  border-radius: 50%;
  background: #10b981;
  display: inline-block;
}

.live-dot.active {
  background: #2563eb;
  box-shadow: 0 0 0 4px rgba(37, 99, 235, 0.25);
  animation: pulse-dot 1.2s infinite ease-in-out;
}

@keyframes pulse-dot {
  0%,
  100% {
    transform: scale(1);
    opacity: 1;
  }
  50% {
    transform: scale(1.25);
    opacity: 0.75;
  }
}

.dify-node-card.is-live-running {
  border-color: #2563eb;
  box-shadow:
    0 0 0 4px rgba(37, 99, 235, 0.28),
    0 8px 24px rgba(37, 99, 235, 0.22);
  animation: pulse-card 1.5s infinite ease-in-out;
}

.dify-node-card.is-collecting {
  border-color: #0891b2;
  box-shadow: 0 0 0 3px rgba(8, 145, 178, 0.2);
}

@keyframes pulse-card {
  0%,
  100% {
    box-shadow:
      0 0 0 3px rgba(37, 99, 235, 0.22),
      0 6px 18px rgba(37, 99, 235, 0.16);
  }
  50% {
    box-shadow:
      0 0 0 6px rgba(37, 99, 235, 0.35),
      0 10px 26px rgba(37, 99, 235, 0.28);
  }
}

.node-live-running-banner {
  margin-top: 8px;
  padding: 4px 8px;
  border-radius: 6px;
  background: linear-gradient(90deg, #2563eb, #1d4ed8);
  color: #ffffff;
  font-size: 13px;
  font-weight: 600;
  text-align: center;
}

.node-collecting-banner {
  margin-top: 8px;
  padding: 4px 8px;
  border-radius: 6px;
  background: #ecfeff;
  border: 1px solid #a5f3fc;
  color: #0e7490;
  font-size: 13px;
  font-weight: 600;
  text-align: center;
}

.inspector-trace-top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 10px;
  background: #f8fafc;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  margin-bottom: 10px;
}

.history-filter-bar {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  margin-bottom: 14px;
}

.conv-code-cell {
  display: flex;
  align-items: center;
  gap: 6px;
  font-weight: 600;
}

.cell-muted {
  font-size: 13px;
  color: #64748b;
}

.cell-active-node {
  margin-top: 4px;
  font-size: 13px;
  font-weight: 600;
  color: #1d4ed8;
}

.stage-drawer-top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 12px;
  background: #f8fafc;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  margin-bottom: 12px;
  flex-wrap: wrap;
  gap: 8px;
}

.stage-meta-info {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.stage-meta-text {
  font-size: 13px;
  color: #334155;
  font-weight: 600;
}

.stage-drawer-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

.stage-collapse-header {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.stage-deliverable-box {
  padding: 8px 4px;
}

.stage-summary-line {
  font-size: 13px;
  color: #0f172a;
  margin-bottom: 8px;
}

.chat-transcript-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
  max-height: 240px;
  overflow-y: auto;
  padding: 8px;
  background: #f8fafc;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
}

.transcript-item {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  font-size: 13px;
  line-height: 1.5;
}

.transcript-content {
  white-space: pre-line;
  color: #1e293b;
}

.dim-output-card {
  background: #f8fafc;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  padding: 10px 12px;
  margin-bottom: 10px;
}

.dim-output-title {
  display: flex;
  align-items: center;
  margin-bottom: 6px;
  font-size: 13px;
  color: #0f172a;
}

.agent-studio-page {
  height: calc(100dvh - 145px);
  min-height: 700px;
  border-radius: 14px;
  background: #fff;
  border-color: var(--cy-line);
}
.studio-topbar {
  padding: 20px 24px;
  gap: 18px;
}
.topbar-left {
  gap: 12px;
}
.studio-badge {
  display: none;
}
.workflow-meta {
  gap: 8px;
}
.topbar-actions {
  gap: 10px;
}
.ctrl-label {
  font-size: 13px;
  color: #8493a0;
  font-weight: 400;
}
.canvas-wrapper {
  background-color: #f7f9fc;
  background-image: radial-gradient(#dce4ed 0.8px, transparent 0.8px);
  background-size: 22px 22px;
}
.canvas-floating-bar {
  padding: 12px 16px;
  gap: 10px;
}
.canvas-floating-bar .el-tag {
  background: transparent;
  border-color: transparent;
  color: #8293a0;
}
.dify-node-card {
  border-width: 1px;
  padding: 16px;
  border-color: #d9e2ed;
  box-shadow: 0 4px 16px #273f5810;
  transition:
    border-color 0.18s,
    box-shadow 0.18s;
}
.dify-node-card.active {
  border-width: 2px;
  box-shadow: 0 5px 20px #315fe817;
}
.node-card-title {
  font-size: 16px;
  font-weight: 600;
}
.node-card-subtitle {
  font-size: 13px;
  line-height: 1.7;
}
.inspector-sidebar {
  width: 370px;
  max-width: 40%;
  min-width: 300px;
  background: #fff;
}
.live-monitor-bar {
  background: #f9fbfd;
  border-color: var(--cy-line);
  padding: 12px 20px;
  gap: 12px;
}
.live-monitor-left,
.live-monitor-right {
  gap: 10px;
}
.live-monitor-left .el-tag,
.live-monitor-right .el-tag {
  font-size: 13px;
}
.live-monitor-bar .el-tag--dark {
  background: #edf3fa;
  border-color: #dce5ee;
  color: #506d89;
}
.live-dot {
  display: none;
}
.readonly-banner {
  background: #f4f6f9;
  border-radius: 8px;
}
.history-filter-bar,
.stage-drawer-actions {
  gap: 10px;
  flex-wrap: wrap;
}
.trace-step-card {
  padding: 18px;
}
.form-item label {
  font-size: 13px;
}
.node-type-pill,
.node-lock-tag {
  font-size: 13px;
}
@media (max-width: 1100px) {
  .studio-topbar {
    padding: 18px;
  }
  .topbar-actions {
    width: 100%;
  }
  .inspector-sidebar {
    position: absolute;
    right: 0;
    top: 0;
    bottom: 0;
    z-index: 10;
    box-shadow: -8px 0 32px #263e5522;
    max-width: 90%;
  }
  .studio-workspace {
    position: relative;
  }
  .live-monitor-left,
  .live-monitor-right {
    flex-wrap: wrap;
  }
  .task-selector .el-select {
    width: 240px !important;
  }
}
@media (max-width: 800px) {
  .agent-studio-page {
    height: auto;
    min-height: 820px;
  }
  .studio-workspace {
    min-height: 550px;
  }
  .studio-topbar {
    padding: 16px;
  }
  .topbar-actions {
    align-items: flex-start;
  }
  .task-selector {
    width: 100%;
    flex-wrap: wrap;
  }
  .task-selector .el-select {
    width: 100% !important;
  }
  .live-monitor-bar {
    padding: 12px;
    flex-wrap: wrap;
  }
  .canvas-floating-bar {
    gap: 4px;
  }
  .canvas-stage {
    transform-origin: 0 0;
  }
  .inspector-sidebar {
    min-width: 0;
    width: 340px;
  }
  .zoom-controls {
    margin-left: 0;
  }
}

/* Node cards show the business role; implementation details remain in the inspector. */
.node-card-subtitle,
.node-lock-tag {
  display: none;
}
.node-card-meta .meta-row:first-child {
  display: none;
}
.node-card-title {
  font-size: 18px;
  line-height: 1.5;
}
.task-metrics-list {
  display: grid;
  gap: 10px;
}
.layout-hint {
  padding: 9px 16px;
  color: #667b8b;
  font-size: 13px;
  border-bottom: 1px solid #e7edf3;
  background: #ffffffa8;
}
.canvas-extent {
  position: relative;
  margin: 0 auto;
}
.canvas-stage {
  position: absolute;
  left: 0;
  top: 0;
}
.canvas-scroll-area {
  /* 画布在可用高度内自己滚动；固定最小高度会把底部挤出屏幕 */
  min-height: 0;
}
.dify-node-card {
  cursor: grab;
  touch-action: none;
  padding: 13px 12px;
}
.dify-node-card:hover {
  transform: none;
}
.dify-node-card.is-dragging {
  cursor: grabbing;
  z-index: 4;
  transition: none;
  box-shadow: 0 10px 30px #263e552b;
}
.dify-node-card.active {
  border-width: 1px;
}
.port-dot {
  top: 50%;
  transform: translateY(-50%);
}
.node-trace-footer {
  flex-wrap: wrap;
  gap: 6px;
}
.row-actions-inline {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  white-space: nowrap;
}
.row-actions-inline :deep(.el-button.is-link) {
  padding: 2px 4px;
}
.row-actions-inline :deep(.el-dropdown) {
  margin-left: 0 !important;
}

/* 页头与其他页统一；画布自适应一屏展示全部 7 个环节节点，节点文字清晰可读 */
.workflow-title { font-size: 24px; font-weight: 600; margin: 0 0 6px; }
.workflow-sub { font-size: 14px; color: var(--cy-text-2); }
.seg-switch { display: inline-flex; }
.seg-switch .el-button { border-radius: 0; margin: 0; }
.seg-switch .el-button:first-child { border-radius: 6px 0 0 6px; }
.seg-switch .el-button:last-child { border-radius: 0 6px 6px 0; margin-left: -1px; }
/* 画布文字按缩放自适应补偿，屏幕上正文与小标签清晰可读且不撑破卡片；连线说明默认隐藏，鼠标移到连线上再显示 */
.canvas-stage { --cz: 1; }
.canvas-stage .node-type-pill,
.canvas-stage .node-card-meta,
.canvas-stage .node-trace-footer,
.canvas-stage .node-card-subtitle,
.canvas-stage .node-live-running-banner,
.canvas-stage .node-collecting-banner,
.canvas-stage .node-card-meta *,
.canvas-stage .node-trace-footer *,
.canvas-stage .node-lock-tag { font-size: calc(12.5px / max(var(--cz), 0.72)) !important; }
.canvas-stage .node-card-title { font-size: calc(14.5px / max(var(--cz), 0.72)) !important; line-height: 1.38; }
.edge-group .edge-label { opacity: 0; pointer-events: none; transition: opacity 0.12s; }
.edge-group { pointer-events: stroke; }
.edge-group:hover .edge-label { opacity: 1; }
.edge-group path { pointer-events: stroke; cursor: default; }
</style>

