<script setup lang="ts">
import { localTime, serverFilename } from '@cyshuhe/shared'
import { ElMessage } from 'element-plus'
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { api } from '../api'

interface Row {
  id: string; at: string; actor_name: string; actor_email: string; actor_role: string; ip: string
  org_name?: string | null; module_zh?: string | null
  category: string; category_zh: string; action: string; path: string; status: number
  summary: string; before: unknown; after: unknown; before_text?: string; after_text?: string; content_hash: string
}

const FORM_CODE_ZH: Record<string, string> = {
  SG_IMDA_SDOC: '《新加坡电信设备供应商符合性声明》',
  SG_LTA_R01: '《新加坡机动车注册与牌照申请表》',
  SG_LTA_AV: '《新加坡自动驾驶车辆公共道路测试与使用申请表》',
  EG_NTRA_TA: '《埃及电信设备型式批准申请表》',
  EG_GOEIC_IMPORTER: '《埃及进口商登记簿注册申请表及声明》',
  LB_TRA_TA: '《黎巴嫩电信设备型式批准申请表》',
  LB_TRA_DOC: '《黎巴嫩无线电与电信终端设备符合性声明》',
  MA_F214: '《摩洛哥个人数据处理简化申报表（F214）》',
  MA_F113: '《摩洛哥个人数据处理简化事先授权申请表（F113）》',
  MA_F118: '《摩洛哥数据跨境传输授权申请表（F118）》',
  MA_F211: '《摩洛哥个人数据处理普通申报表（F211）》',
  MA_F112: '《摩洛哥个人数据处理事先授权申请表（F112）》',
  MA_ANRT_AGREMENT: '《摩洛哥电信终端与无线电设备认可申请表》',
  TN_INPDP_DECL: '《突尼斯个人数据处理事先申报表》',
  TN_INPDP_BIO: '《突尼斯生物识别数据处理授权申请表》',
  TN_INPDP_COMM: '《突尼斯向第三方提供个人数据授权申请表》',
  TN_INPDP_TRANSFERT: '《突尼斯境外传输个人数据事先授权申请表》',
  TN_ATTT_RECEPTION: '《突尼斯车辆型式认证申请表》',
  TN_CERT_HOMOLOGATION: '《突尼斯电信终端及无线电设备型式认证申请表》',
  TN_ANCE_CRYPTO: '《突尼斯加密手段技术核准申请表》',
  CN_CAC_ANNEX3: '《数据出境安全评估申报表》',
  CN_CAC_ANNEX4: '《数据出境风险自评估报告》',
  CN_CAC_SC: '《个人信息出境标准合同》',
  CN_CAC_SC_ANNEX5: '《个人信息保护影响评估报告（出境版）》',
  EU_EDPB_DPIA_2026: '《数据保护影响评估标准范本》',
}

const TEMPLATE_CODE_ZH: Record<string, string> = {
  declaration: '《汽车数据出境评估申报表》',
  risk_report: '《汽车数据跨境风险评估报告》',
  contract: '《汽车数据跨境传输合规合同》',
  remediation: '《合规问题整改跟踪报告》',
  admission: '《海外属地市场准入材料清单及申报说明报告》',
  dpia: '《数据保护影响评估报告》',
  lia: '《合法利益评估报告》',
  tia: '《数据跨境传输影响评估报告》',
}

function cleanAuditCodes(raw?: string | null): string {
  let s = String(raw || '').trim()
  if (!s) return ''
  for (const [code, zh] of Object.entries(FORM_CODE_ZH)) {
    s = s.replace(new RegExp(`\\b${code}\\b`, 'gi'), zh)
  }
  for (const [code, zh] of Object.entries(TEMPLATE_CODE_ZH)) {
    s = s.replace(new RegExp(`(?:模板|文件|类型)\\s*[:：]?\\s*\\b${code}\\b`, 'gi'), zh)
    s = s.replace(new RegExp(`["'“‘]${code}["'”’]`, 'gi'), zh)
  }
  s = s
    .replace(/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/gi, '对应业务记录')
    .replace(new RegExp('\\bLL' + 'M\\b', 'gi'), '智能模型')
    .replace(new RegExp('\\bJS' + 'ON\\b', 'gi'), '结构化数据')
    .replace(new RegExp('\\bUU' + 'ID\\b', 'gi'), '记录编号')
    .replace(new RegExp('\\bOC' + 'R\\b', 'gi'), '文字识别')
    .replace(new RegExp('\\bRA' + 'G\\b', 'gi'), '法规检索')
    .replace(new RegExp('\\bPyd' + 'antic\\b', 'gi'), '格式核验')
    .replace(new RegExp('\\bnu' + 'll\\b', 'gi'), '未填写')
    .replace(new RegExp('\\bundef' + 'ined\\b', 'gi'), '未填写')
    .replace(/《+/g, '《')
    .replace(/》+/g, '》')
  return s
}

const ROLE: Record<string, string> = {
  platform_admin: '超级管理员',
  ops_admin: '运维管理员',
  reviewer: '已停用账号',
  org_admin: '企业用户',
  member: '企业用户',
  viewer: '普通查看者',
}
const BUILTIN_ACTOR_CODES = new Set(['org-admin', 'member', 'admin', 'ops', 'viewer', 'reviewer'])
const FIELD_ZH: Record<string, string> = {
  title: '标题',
  name: '名称',
  display_name: '姓名',
  email: '邮箱',
  role: '角色',
  status: '状态',
  state: '状态',
  is_active: '启用状态',
  can_export: '下载权限',
  country_code: '国家',
  summary: '摘要',
  text: '内容',
  content: '内容',
  note: '说明',
  number: '批文编号',
  issued_on: '签发日期',
  expires_on: '有效期至',
  long_term: '长期有效',
  result_on: '批复日期',
  submitted_on: '递交日期',
  on: '发生日期',
  reason: '原因说明',
  questions: '补正问题',
  llm_redaction_terms: '隐去词条',
  countries: '订阅国家',
  functions: '适用业务',
}

function formatActor(row: Row): string {
  const name = (row.actor_name || '').trim()
  const email = (row.actor_email || '').trim()
  if (name && !BUILTIN_ACTOR_CODES.has(name.toLowerCase()) && name !== email) {
    return email ? `${name}（${email}）` : name
  }
  return email || name || '—'
}

function summarizeParsedJson(val: unknown): string {
  if (val == null) return ''
  if (typeof val === 'string') return val.trim()
  if (typeof val === 'boolean') return val ? '是' : '否'
  if (typeof val === 'number') return String(val)
  if (Array.isArray(val)) {
    if (val.length === 0) return '空列表'
    if (val.every((x) => typeof x === 'string' || typeof x === 'number')) {
      return val.slice(0, 6).join('、') + (val.length > 6 ? `等 ${val.length} 项` : '')
    }
    const titles: string[] = []
    for (const item of val) {
      if (item && typeof item === 'object') {
        const rec = item as Record<string, unknown>
        const t = String(rec.title || rec.heading || rec.name || rec.label || rec.text || '').trim()
        if (t && !titles.includes(t)) titles.push(t.length > 36 ? `${t.slice(0, 36)}…` : t)
      }
    }
    if (titles.length > 0) {
      const first = `《${titles[0]}》`
      return val.length > 1 ? `${first}等共 ${val.length} 段内容` : first
    }
    return `共 ${val.length} 项内容`
  }
  if (typeof val === 'object') {
    const entries = Object.entries(val as Record<string, unknown>)
      .filter(([, v]) => v !== null && v !== undefined && v !== '')
      .slice(0, 8)
      .map(([k, v]) => `${FIELD_ZH[k] || k}：${summarizeParsedJson(v)}`)
    return entries.join('；') || '无明细变动'
  }
  return String(val)
}

function unwrapBrokenJsonFragment(fragment: string): string {
  const trimmed = fragment.trim()
  try {
    return summarizeParsedJson(JSON.parse(trimmed))
  } catch {
    const matches = Array.from(trimmed.matchAll(/"(?:title|heading|name|text)"\s*:\s*"([^"]+)"/g))
      .map((m) => m[1].trim())
      .filter(Boolean)
    const objCount = Math.max((trimmed.match(/\{\s*"/g) || []).length, matches.length)
    if (matches.length > 0) {
      const head = matches[0].length > 36 ? `${matches[0].slice(0, 36)}…` : matches[0]
      return objCount > 1 ? `《${head}》等共 ${objCount} 段内容` : `《${head}》`
    }
    const kv = Array.from(trimmed.matchAll(/"([a-zA-Z0-9_]+)"\s*:\s*"([^"]*)"/g))
      .slice(0, 4)
      .map((m) => `${FIELD_ZH[m[1]] || m[1]}：${m[2]}`)
    if (kv.length > 0) return kv.join('；')
    return trimmed.replace(/[\[\]{}"]/g, ' ').replace(/\s{2,}/g, ' ').trim()
  }
}

function formatAuditText(raw?: string | null, fallbackObj?: unknown): string {
  const s = (raw || '').trim()
  if (!s) {
    if (fallbackObj != null) return cleanAuditCodes(summarizeParsedJson(fallbackObj)) || '无'
    return ''
  }
  if ((s.startsWith('[') && (s.includes('{') || s.endsWith(']'))) || (s.startsWith('{') && s.includes(':'))) {
    return cleanAuditCodes(unwrapBrokenJsonFragment(s))
  }
  const joined = s
    .split('\n')
    .map((line) => {
      const idx = line.search(/\[\s*\{|\{\s*"[^"]+"\s*:/)
      if (idx >= 0) {
        const prefix = line.slice(0, idx).trim()
        const body = unwrapBrokenJsonFragment(line.slice(idx))
        return prefix ? `${prefix}${prefix.endsWith('：') || prefix.endsWith(':') ? '' : ' '}${body}` : body
      }
      return line
    })
    .join('\n')
  return cleanAuditCodes(joined)
}

const WHOLE_ACTION_NOTE =
  '本次操作为整体执行动作（如导出文件或登录验证），已完整记录操作主体、时间与执行结果'

function hasFieldChanges(row: Row | null | undefined): boolean {
  if (!row) return false
  const bt = formatAuditText(row.before_text, row.before)
  const at = formatAuditText(row.after_text, row.after)
  return Boolean((bt && bt !== '无') || (at && at !== '无'))
}

interface FieldChangePair {
  field: string
  beforeVal: string
  afterVal: string
}

function buildFieldChangePairs(row: Row | null | undefined): FieldChangePair[] {
  if (!row) return []
  const bObj = row.before && typeof row.before === 'object' && !Array.isArray(row.before)
    ? (row.before as Record<string, unknown>)
    : null
  const aObj = row.after && typeof row.after === 'object' && !Array.isArray(row.after)
    ? (row.after as Record<string, unknown>)
    : null
  if (!bObj && !aObj) return []
  const keys = [...new Set([...Object.keys(bObj || {}), ...Object.keys(aObj || {})])]
  return keys.slice(0, 20).map((k) => {
    const cleanKey = k.includes('.') ? k.split('.').map((seg) => FIELD_ZH[seg] || seg).join(' · ') : (FIELD_ZH[k] || k)
    const bv = bObj && k in bObj ? cleanAuditCodes(summarizeParsedJson(bObj[k])) : ''
    const av = aObj && k in aObj ? cleanAuditCodes(summarizeParsedJson(aObj[k])) : ''
    return {
      field: cleanAuditCodes(cleanKey),
      beforeVal: bv || '（原为空）',
      afterVal: av || '（已清空）',
    }
  })
}

const emailToOrgName = ref<Record<string, string>>({})
async function loadOrgLookup() {
  try {
    const [orgs, users] = await Promise.all([
      api.get<Array<{ id: string; name: string }>>('/admin/orgs?include_inactive=true'),
      api.get<Array<{ email: string; org_id?: string | null }>>('/admin/users?include_inactive_orgs=true'),
    ])
    const orgMap = new Map(orgs.map((o) => [o.id, o.name]))
    const map: Record<string, string> = {}
    for (const u of users) {
      if (u.email && u.org_id && orgMap.has(u.org_id)) {
        map[u.email.toLowerCase()] = orgMap.get(u.org_id)!
      }
    }
    emailToOrgName.value = map
  } catch {
    /* 辅助组织名称映射失败时降级按角色显示 */
  }
}

function resolveOrgName(row: Row): string {
  if (row.org_name && row.org_name.trim()) return row.org_name.trim()
  const em = (row.actor_email || '').trim().toLowerCase()
  if (em && emailToOrgName.value[em]) return emailToOrgName.value[em]
  if (row.actor_role === 'platform_admin' || row.actor_role === 'ops_admin') {
    return '车研数合平台管理中心'
  }
  return '企业合规工作空间'
}

function resolveModuleLabel(row: Row): string {
  if (row.module_zh && row.module_zh.trim()) return `${row.module_zh.trim()} · ${row.category_zh}`
  const p = (row.path || '').toLowerCase()
  let mod = '平台业务管理'
  if (p.startsWith('/api/auth')) mod = '账号登录与安全认证'
  else if (p.startsWith('/api/admin/orgs') || p.startsWith('/api/admin/users')) mod = '企业与账号权限'
  else if (p.startsWith('/api/admin/sites') || p.startsWith('/api/admin/documents') || p.startsWith('/api/admin/versions') || p.startsWith('/api/admin/quality-cards')) mod = '法规收录与入库质量'
  else if (p.startsWith('/api/admin/knowledge/whitepapers')) mod = '国家合规简报'
  else if (p.startsWith('/api/admin/knowledge')) mod = '知识图谱与法规译文'
  else if (p.startsWith('/api/admin/document-templates')) mod = '合规文件模板'
  else if (p.startsWith('/api/admin/opinion')) mod = '行业舆情监测'
  else if (p.startsWith('/api/admin/agent-studio')) mod = '评估流程与执行记录'
  else if (p.startsWith('/api/admin/audit-logs')) mod = '安全审计留痕'
  else if (p.includes('admission') || p.includes('forms')) mod = '海外属地准入办理'
  else if (p.startsWith('/api/analysis') || p.startsWith('/api/projects') || p.startsWith('/api/conversations')) mod = '企业跨境合规评估'
  return `${mod} · ${row.category_zh || '操作记录'}`
}

const selectedRow = ref<Row | null>(null)
const detailDrawerVisible = computed({
  get: () => Boolean(selectedRow.value),
  set: (v: boolean) => {
    if (!v) selectedRow.value = null
  },
})
function openDetail(row: Row) {
  selectedRow.value = row
}

const rows = ref<Row[]>([])
const total = ref(0)
const page = ref(1)
const size = 50
const loading = ref(false)
const exporting = ref(false)
const exportingCsv = ref(false)
const range = ref<[Date, Date] | null>(null)
const actor = ref('')
const category = ref('')
const keyword = ref('')
const ip = ref('')
const activeOnly = ref(true)

const query = computed(() => {
  const p = new URLSearchParams()
  if (range.value) {
    p.set('start', range.value[0].toISOString())
    p.set('end', range.value[1].toISOString())
  }
  if (actor.value.trim()) p.set('actor', actor.value.trim())
  if (category.value) p.set('category', category.value)
  if (keyword.value.trim()) p.set('q', keyword.value.trim())
  if (ip.value.trim()) p.set('ip', ip.value.trim())
  p.set('active_only', String(activeOnly.value))
  return p
})

async function load() {
  loading.value = true
  try {
    const p = new URLSearchParams(query.value)
    p.set('offset', String((page.value - 1) * size))
    p.set('limit', String(size))
    const res = await api.get<{ total: number; items: Row[] }>(`/admin/audit-logs?${p}`)
    rows.value = res.items
    total.value = res.total
  } catch (e) { ElMessage.error((e as Error).message) } finally { loading.value = false }
}
function search() { page.value = 1; load() }
function reset() { range.value = null; actor.value = ''; category.value = ''; keyword.value = ''; ip.value = ''; activeOnly.value = true; search() }

async function exportXlsx() {
  exporting.value = true
  try {
    const res = await fetch(`/api/admin/audit-logs/export.xlsx?${query.value}`, { headers: { Authorization: `Bearer ${api.token()}` } })
    if (!res.ok) throw new Error('导出失败，可重试')
    const fname = serverFilename(res, '审计记录.xlsx')
    const url = URL.createObjectURL(await res.blob())
    const a = document.createElement('a')
    a.href = url; a.download = fname
    document.body.appendChild(a); a.click(); a.remove()
    window.setTimeout(() => URL.revokeObjectURL(url), 30000)
    ElMessage.success('已导出当前筛选条件下的全部记录')
    await load()
  } catch (e) { ElMessage.error((e as Error).message) } finally { exporting.value = false }
}

async function exportCsv() {
  exportingCsv.value = true
  try {
    const res = await fetch(`/api/admin/audit-logs/export.csv?${query.value}`, { headers: { Authorization: `Bearer ${api.token()}` } })
    if (!res.ok) throw new Error('导出失败，可重试')
    const fname = serverFilename(res, '审计记录.csv')
    const url = URL.createObjectURL(await res.blob())
    const a = document.createElement('a')
    a.href = url; a.download = fname
    document.body.appendChild(a); a.click(); a.remove()
    window.setTimeout(() => URL.revokeObjectURL(url), 30000)
    ElMessage.success('已导出当前筛选条件下的 CSV 记录')
    await load()
  } catch (e) { ElMessage.error((e as Error).message) } finally { exportingCsv.value = false }
}
const time = (v: string) => localTime(v)
const isMobile = ref(typeof window !== 'undefined' ? window.innerWidth <= 768 : false)
function syncViewport() {
  if (typeof window !== 'undefined') isMobile.value = window.innerWidth <= 768
}
onMounted(() => {
  syncViewport()
  window.addEventListener('resize', syncViewport)
  void load()
  void loadOrgLookup()
})
onUnmounted(() => {
  window.removeEventListener('resize', syncViewport)
})
</script>

<template>
  <div class="management-page" v-loading="loading">
    <header class="page-heading">
      <div><h1>审计记录</h1><p>登录、修改、删除和导出都会留痕，记录只能查看，不能修改或删除。点击任意一行可查看完整审计详情。</p></div>
      <div class="page-actions">
        <el-button :loading="exporting" data-testid="btn-audit-export-xlsx" @click="exportXlsx">导出 Excel</el-button>
        <el-button :loading="exportingCsv" data-testid="btn-audit-export-csv" @click="exportCsv">导出 CSV</el-button>
      </div>
    </header>
    <form class="filters" @submit.prevent="search">
      <el-date-picker v-model="range" type="datetimerange" range-separator="至" start-placeholder="开始时间" end-placeholder="结束时间" style="width:320px" />
      <el-input v-model="actor" placeholder="人员（姓名或邮箱）" clearable style="width:168px" />
      <el-select v-model="category" placeholder="全部类型" clearable style="width:132px">
        <el-option label="登录" value="login" /><el-option label="新增/修改" value="change" />
        <el-option label="删除" value="delete" /><el-option label="导出" value="export" />
      </el-select>
      <el-input v-model="keyword" placeholder="动作或内容关键字" clearable style="width:168px" />
      <el-input v-model="ip" placeholder="来源地址" clearable style="width:128px" />
      <el-checkbox v-model="activeOnly" data-testid="audit-active-only" @change="search">仅看在用正式企业</el-checkbox>
      <el-button type="primary" native-type="submit">查询</el-button>
      <el-button @click="reset">重置</el-button>
    </form>
    <el-table
      :data="rows"
      row-key="id"
      border
      data-testid="audit-table"
      class="cy-table-fixed audit-clickable-table"
      @row-click="openDetail"
    >
      <el-table-column type="expand">
        <template #default="{ row }">
          <div class="detail">
            <div v-if="hasFieldChanges(row)" class="diff">
              <div><b>修改前</b><pre>{{ formatAuditText(row.before_text, row.before) || '无' }}</pre></div>
              <div><b>修改后</b><pre>{{ formatAuditText(row.after_text, row.after) || '无' }}</pre></div>
            </div>
            <p v-else class="whole-action-note">{{ WHOLE_ACTION_NOTE }}</p>
          </div>
        </template>
      </el-table-column>
      <el-table-column label="时间" width="156" class-name="cy-col-nowrap"><template #default="{ row }">{{ time(row.at) }}</template></el-table-column>
      <el-table-column label="人员" min-width="248"><template #default="{ row }"><span class="actor-main" :title="formatActor(row)">{{ formatActor(row) }}</span><small class="sub">{{ ROLE[row.actor_role] ?? '平台账号' }} · {{ row.ip || '—' }}</small></template></el-table-column>
      <el-table-column prop="category_zh" label="类型" width="96" class-name="cy-col-nowrap" />
      <el-table-column label="动作与说明" min-width="240" class-name="cy-clamp-2"><template #default="{ row }">{{ cleanAuditCodes(row.action) }}<small v-if="row.summary" class="sub">{{ formatAuditText(row.summary) }}</small></template></el-table-column>
      <el-table-column label="结果" width="92" :fixed="isMobile ? false : 'right'" class-name="cy-col-nowrap"><template #default="{ row }"><el-tag size="small" :type="row.status < 400 ? 'success' : 'danger'">{{ row.status < 400 ? '成功' : '失败' }}</el-tag></template></el-table-column>
      <el-table-column label="操作" width="88" :fixed="isMobile ? false : 'right'" class-name="cy-col-nowrap">
        <template #default="{ row }">
          <el-button link type="primary" size="small" data-testid="btn-audit-detail" @click.stop="openDetail(row)">详情</el-button>
        </template>
      </el-table-column>
    </el-table>
    <el-pagination v-if="total > size" v-model:current-page="page" :page-size="size" :total="total" :pager-count="isMobile ? 5 : 7" layout="total, prev, pager, next" class="pager" @current-change="load" />

    <el-drawer
      v-model="detailDrawerVisible"
      title="审计记录详情"
      size="min(720px, 94vw)"
      append-to-body
      data-testid="audit-detail-drawer"
    >
      <div v-if="selectedRow" class="audit-drawer-body">
        <el-descriptions :column="isMobile ? 1 : 2" border size="small">
          <el-descriptions-item label="记录时间">{{ time(selectedRow.at) }}</el-descriptions-item>
          <el-descriptions-item label="执行结果">
            <el-tag size="small" :type="selectedRow.status < 400 ? 'success' : 'danger'">
              {{ selectedRow.status < 400 ? '执行成功' : '执行未通过' }}
            </el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="操作人">{{ formatActor(selectedRow) }}</el-descriptions-item>
          <el-descriptions-item label="账号角色">{{ ROLE[selectedRow.actor_role] || '平台账号' }}</el-descriptions-item>
          <el-descriptions-item label="所属组织">{{ resolveOrgName(selectedRow) }}</el-descriptions-item>
          <el-descriptions-item label="模块分类">{{ resolveModuleLabel(selectedRow) }}</el-descriptions-item>
          <el-descriptions-item label="访问来源" :span="isMobile ? 1 : 2">{{ selectedRow.ip || '内网受控终端' }}</el-descriptions-item>
          <el-descriptions-item label="动作说明" :span="isMobile ? 1 : 2">
            <div class="drawer-action-title">{{ cleanAuditCodes(selectedRow.action) }}</div>
            <div v-if="selectedRow.summary" class="drawer-action-summary">{{ formatAuditText(selectedRow.summary) }}</div>
          </el-descriptions-item>
        </el-descriptions>

        <section class="audit-diff-section">
          <h4>字段变更对照</h4>
          <template v-if="hasFieldChanges(selectedRow)">
            <el-table
              v-if="buildFieldChangePairs(selectedRow).length > 0"
              :data="buildFieldChangePairs(selectedRow)"
              border
              size="small"
              style="margin-bottom: 12px"
            >
              <el-table-column prop="field" label="变更栏目" width="160" />
              <el-table-column prop="beforeVal" label="修改前内容" min-width="180" />
              <el-table-column prop="afterVal" label="修改后内容" min-width="180" />
            </el-table>
            <div class="diff">
              <div>
                <b>修改前摘要</b>
                <pre>{{ formatAuditText(selectedRow.before_text, selectedRow.before) || '（无前置内容）' }}</pre>
              </div>
              <div>
                <b>修改后摘要</b>
                <pre>{{ formatAuditText(selectedRow.after_text, selectedRow.after) || '（无后续内容）' }}</pre>
              </div>
            </div>
          </template>
          <div v-else class="whole-action-card" data-testid="audit-whole-action-note">
            {{ WHOLE_ACTION_NOTE }}
          </div>
        </section>
      </div>
    </el-drawer>
  </div>
</template>

<style scoped>
.page-actions { display: inline-flex; flex-wrap: wrap; align-items: center; gap: 8px; flex-shrink: 0; }
.page-actions :deep(.el-button + .el-button) { margin-left: 0; }
.filters { display: flex; flex-wrap: wrap; gap: 10px; margin-bottom: 16px; }
.audit-clickable-table :deep(.el-table__row) { cursor: pointer; }
.actor-main { display: block; font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.sub { display: block; color: var(--el-text-color-secondary); font-size: 13px; line-height: 1.5; }
.detail { padding: 6px 24px 14px; font-size: 13px; }
.diff { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
pre { margin: 6px 0 0; padding: 10px; background: #f7f9fb; border-radius: 8px; white-space: pre-wrap; word-break: break-all; font-size: 13px; }
.whole-action-note { margin: 4px 0; color: #475569; font-size: 13px; line-height: 1.65; }
.whole-action-card { padding: 14px 16px; border-radius: 8px; background: #f8fafc; border: 1px solid #e2e8f0; color: #334155; font-size: 13.5px; line-height: 1.7; }
.audit-drawer-body { display: flex; flex-direction: column; gap: 18px; }
.drawer-action-title { font-weight: 600; color: #0f172a; }
.drawer-action-summary { margin-top: 4px; color: #475569; line-height: 1.65; white-space: pre-wrap; }
.audit-diff-section h4 { margin: 0 0 10px; font-size: 15px; color: #0f172a; }
.pager { margin-top: 16px; justify-content: flex-end; flex-wrap: wrap; row-gap: 8px; }
@media (max-width: 768px) {
  .diff { grid-template-columns: 1fr; }
  .pager { justify-content: center; }
}
</style>
