<script setup lang="ts">
import { ElMessage } from 'element-plus'
import { computed, onMounted, ref } from 'vue'
import { api } from '../api'
import { localTime, serverFilename } from '@cyshuhe/shared'

interface Fact { key: string; label: string; question: string; why: string; from?: string }
interface Block { type: string; text?: string; items?: string[]; keys?: string[]; key?: string; name?: string; dimensions?: string[]; limit?: number }
interface NarrativePart { key: string; heading: string; brief: string; facts: string[]; side?: string; dimensions?: string[]; flow?: boolean; risks?: boolean; field_table?: boolean }
interface Section { heading?: string; narrative?: boolean; blocks?: Block[]; when?: Record<string, string>; unless?: Record<string, string> }
interface Definition { title: string; facts: Fact[]; sections: Section[]; narrative?: NarrativePart[] }
interface Row { code: string; version_no: number; name: string; status: string; customized?: boolean; updated_at?: string | null }

const rows = ref<Row[]>([])
const editing = ref<{ code: string; name: string; version_no: number; def: Definition } | null>(null)
const busy = ref(false)
const editorMode = ref<'edit' | 'preview'>('edit')
const showFactsPanel = ref(false)
const activeOutline = ref('tpl-title')

const BLOCK_LABEL: Record<string, string> = {
  text: '正文段落',
  checklist: '核查要点清单',
  facts_table: '结构化信息表',
  citations: '适用法规依据表',
  risk_matrix: '风险评估清单与等级判定表',
  slot: '业务联动台账表',
  static_table: '固定对照表',
}

const SLOT_LABEL: Record<string, string> = {
  route: '出境路径结论',
  issues: '合规问题清单',
  fix_route: '分阶段整改路线图',
  advice: '整改建议清单',
  risk_evolution: '风险整改前后对比台账',
  scc_modules: '标准合同模块条款',
  admission_items: '准入事项一览表',
  admission_materials: '准入材料清单',
  admission_filing: '递交要求说明',
  admission_route: '准入办理步骤表',
  admission_clauses: '适用条文对照',
}

const BASE_FACT_LABELS: Record<string, string> = {
  company_name: '企业名称',
  source_country: '数据发出国',
  recipient_country: '数据接收国',
  vehicle_model: '车型或车队',
  scenario: '业务场景',
  country_name: '目标国名称',
  purpose: '处理目的',
  data_types: '传输的数据类型',
  data_volume: '涉及自然人数量',
  recipient: '境外接收方',
  retention_and_blackbox: '保存期限与存放位置',
  risk_measures: '已采取的安全保护措施',
  law_compliance: '遵守法律法规情况',
  cross_border_mechanism: '接收方与传输保障机制',
  supplementary_measures: '补充保护措施',
  reevaluation: '复评安排',
}

const DIM_OPTIONS = [
  { value: 'D1', label: '属地数据法规体系概述' },
  { value: 'D2', label: '汽车核心数据定义与分类' },
  { value: 'D3', label: '数据跨境传输合规路径与前置条件' },
  { value: 'D4', label: '用户授权、告知、同意' },
  { value: 'D5', label: '数据存储、留存、销毁' },
  { value: 'D6', label: '违规行为分级、处罚标准、监管追责' },
  { value: 'D7', label: '属地市场准入认证、申报材料' },
  { value: 'D8', label: '企业合规整改建议' },
]

const origFactLabels = ref<Record<string, string>>({})

function factDisplayLabel(f: Fact): string {
  return (f.label || '').trim() || origFactLabels.value[f.key] || BASE_FACT_LABELS[f.key] || f.key
}

function factLabelByKey(key: string): string {
  const hit = editing.value?.def.facts.find((x) => x.key === key)
  if (hit) return factDisplayLabel(hit)
  return origFactLabels.value[key] || BASE_FACT_LABELS[key] || key
}

function buildKeyToLabelMap(facts: Fact[]): Record<string, string> {
  const map: Record<string, string> = { ...BASE_FACT_LABELS }
  const usedLabels = new Set<string>()
  for (const f of facts) {
    let lbl = (f.label || '').trim() || BASE_FACT_LABELS[f.key] || f.key
    if (usedLabels.has(lbl)) {
      lbl = `${lbl}（${f.key}）`
    }
    usedLabels.add(lbl)
    map[f.key] = lbl
  }
  return map
}

function rawToDisplay(text: string | undefined, keyToLabel: Record<string, string>): string {
  if (!text) return ''
  return text
    .replace(/\{\{\s*([a-z0-9_]+)\s*\}\}/g, (_, key: string) => {
      const label = keyToLabel[key] || BASE_FACT_LABELS[key] || key
      return `【${label}】`
    })
    .replace(/【+([^【】]+)】+/g, '【$1】')
}

function displayToRaw(text: string | undefined, facts: Fact[]): string {
  if (!text) return ''
  let out = text
  const pairs: Array<[string, string]> = []
  for (const f of facts) {
    const cur = (f.label || '').trim()
    const orig = origFactLabels.value[f.key]
    const base = BASE_FACT_LABELS[f.key]
    if (cur) pairs.push([cur, f.key])
    if (orig && orig !== cur) pairs.push([orig, f.key])
    if (base && base !== cur && base !== orig) pairs.push([base, f.key])
  }
  pairs.sort((a, b) => b[0].length - a[0].length)
  for (const [lbl, key] of pairs) {
    out = out.split(`【${lbl}】`).join(`{{${key}}}`)
  }
  return out
}

function sanitizeBrief(raw: string | undefined, keyToLabel: Record<string, string>): string {
  if (!raw) return ''
  return rawToDisplay(raw, keyToLabel)
    .replace(/context\.legit_basis/g, '系统梳理的合法利益法定依据')
    .replace(/context\.project_facts/g, '项目已确认的业务情况')
    .replace(/context\.recipient_law/g, '接收国法律与监管概况')
    .replace(/context\.route/g, '评估确定的法定出境路径')
    .replace(/\bcontext\b/g, '项目背景信息')
    .replace(/\brecipient_country\b/g, '数据接收国')
    .replace(/\bsource_country\b/g, '数据发出国')
    .replace(/\breevaluation\b/g, '复评安排')
    .replace(/\bclauses\b/g, '适用法规条文')
    .replace(/\bfacts\b/g, '企业填写项')
    .replace(/（Purpose Test）/g, '（目的测试）')
    .replace(/（Necessity Test）/g, '（必要性测试）')
    .replace(/（Balancing Test）/g, '（平衡测试）')
    .replace(/（Know Your Transfer）/g, '（梳理跨境传输全貌）')
    .replace(/（Identify the Transfer Tool）/g, '（识别传输保障机制）')
    .replace(/（Adopt Supplementary Measures）/g, '（补充保护措施评估）')
    .replace(new RegExp('写“待' + '核实”的，就如实写待' + '核实', 'g'), '条文未作明确规定的，如实写明条文未明确')
    .replace(/已核实的/g, '已确认的')
    .replace(new RegExp('待' + '核实', 'g'), '条文未明确')
}

function hydrateDefinitionForDisplay(def: Definition): Definition {
  const copy: Definition = JSON.parse(JSON.stringify(def))
  const keyToLabel = buildKeyToLabelMap(copy.facts || [])
  origFactLabels.value = { ...keyToLabel }
  copy.title = rawToDisplay(copy.title, keyToLabel)
  for (const s of copy.sections || []) {
    if (s.heading) s.heading = rawToDisplay(s.heading, keyToLabel)
    for (const b of s.blocks || []) {
      if (typeof b.text === 'string') b.text = rawToDisplay(b.text, keyToLabel)
      if (Array.isArray(b.items)) {
        b.items = b.items.map((item) => rawToDisplay(item, keyToLabel))
      }
    }
  }
  for (const np of copy.narrative || []) {
    np.brief = sanitizeBrief(np.brief, keyToLabel)
  }
  return copy
}

function serializeDefinitionForSave(def: Definition): Definition {
  const copy: Definition = JSON.parse(JSON.stringify(def))
  const facts = copy.facts || []
  copy.title = displayToRaw(copy.title, facts)
  for (const s of copy.sections || []) {
    if (s.heading) s.heading = displayToRaw(s.heading, facts)
    for (const b of s.blocks || []) {
      if (typeof b.text === 'string') b.text = displayToRaw(b.text, facts)
      if (Array.isArray(b.items)) {
        b.items = b.items.map((item) => displayToRaw(item, facts))
      }
    }
  }
  for (const np of copy.narrative || []) {
    np.brief = sanitizeBrief(np.brief, buildKeyToLabelMap(facts))
  }
  return copy
}

function insertFactIntoBlock(b: Block, f: Fact) {
  const token = `【${factDisplayLabel(f)}】`
  if (b.type === 'text') {
    b.text = `${b.text || ''}${token}`
  } else if (b.type === 'checklist') {
    const items = [...(b.items || [])]
    if (!items.length) items.push(token)
    else items[items.length - 1] = `${items[items.length - 1]}${token}`
    b.items = items
  }
}

function insertFactKeyIntoBlock(b: Block, key: string) {
  const f = editing.value?.def.facts.find((x) => x.key === key)
  if (f) insertFactIntoBlock(b, f)
}

function insertFactIntoTitle(f: Fact) {
  if (!editing.value) return
  editing.value.def.title = `${editing.value.def.title || ''}【${factDisplayLabel(f)}】`
}

function insertFactKeyIntoTitle(key: string) {
  const f = editing.value?.def.facts.find((x) => x.key === key)
  if (f) insertFactIntoTitle(f)
}

const quickFacts = computed(() => (editing.value?.def.facts || []).slice(0, 4))
const moreFacts = computed(() => (editing.value?.def.facts || []).slice(4))

const factOptions = computed(() => {
  const out: { value: string; label: string }[] = []
  const seen = new Set<string>()
  for (const f of editing.value?.def.facts || []) {
    if (!seen.has(f.key)) {
      out.push({ value: f.key, label: f.label || BASE_FACT_LABELS[f.key] || f.key })
      seen.add(f.key)
    }
  }
  for (const [k, label] of Object.entries(BASE_FACT_LABELS)) {
    if (!seen.has(k)) {
      out.push({ value: k, label })
      seen.add(k)
    }
  }
  return out
})

interface SectionSubItem {
  ni: number
  chapNo: number
  badge: string
  cardBadge: string
  heading: string
  anchorId: string
}
interface SectionNumberMeta {
  si: number
  isNarrative: boolean
  badge: string
  outlineIdx: string
  outlineTitle: string
  rangeSummary: string
  subItems: SectionSubItem[]
}

function countDefinitionChapters(def?: Definition | null): number {
  if (!def) return 0
  const secs = def.sections || []
  const nars = def.narrative || []
  let total = 0
  secs.forEach((s, si) => {
    if (s.narrative) {
      if (nars.length > 0) {
        const prevH = si > 0 ? (secs[si - 1]?.heading || '').trim() : ''
        const firstNarH = (nars[0]?.heading || '').trim()
        total += prevH && prevH === firstNarH ? Math.max(0, nars.length - 1) : nars.length
      } else {
        total += 1
      }
    } else if (!/^附[录件]/.test((s.heading || '').trim())) {
      total += 1
    }
  })
  return total || secs.length
}

const sectionMetaList = computed<SectionNumberMeta[]>(() => {
  const def = editing.value?.def
  if (!def) return []
  const secs = def.sections || []
  const nars = def.narrative || []
  const hasMainUnlessCn = secs.some((x) => x.unless?.source_country === 'CN')
  let chap = 1
  let cnSeq = 1

  return secs.map((s, si) => {
    const h = (s.heading || '').trim()
    if (s.narrative) {
      if (!nars.length) {
        return {
          si,
          isNarrative: true,
          badge: '论述部分',
          outlineIdx: '论述',
          outlineTitle: '正文论述（暂无小节）',
          rangeSummary: '',
          subItems: [],
        }
      }
      const prevH = si > 0 ? (secs[si - 1]?.heading || '').trim() : ''
      const firstNarH = (nars[0]?.heading || '').trim()
      const sharePrevChap = Boolean(prevH && prevH === firstNarH && chap > 1)
      const subItems: SectionSubItem[] = nars.map((np, ni) => {
        const chapNo = sharePrevChap
          ? ni === 0
            ? chap - 1
            : chap + ni - 1
          : chap + ni
        return {
          ni,
          chapNo,
          badge: `第 ${chapNo} 章`,
          cardBadge: `第 ${chapNo} 章 · 论述第 ${ni + 1} 节`,
          heading: (np.heading || '').trim() || `论述第 ${ni + 1} 节`,
          anchorId: `tpl-nar-${si}-${ni}`,
        }
      })
      chap += sharePrevChap ? Math.max(0, nars.length - 1) : nars.length
      const startChap = subItems[0].chapNo
      const endChap = subItems[subItems.length - 1].chapNo
      const rangeLabel = startChap === endChap ? `第 ${startChap} 章` : `第 ${startChap}–${endChap} 章`
      const firstTitle = subItems[0].heading
      const lastTitle = subItems[subItems.length - 1].heading
      const rangeSummary = subItems.length > 1 ? `${firstTitle} ～ ${lastTitle}` : firstTitle
      return {
        si,
        isNarrative: true,
        badge: `${rangeLabel}（论述部分，共 ${nars.length} 节）`,
        outlineIdx: rangeLabel,
        outlineTitle: `论述部分（共 ${nars.length} 节）：${rangeSummary}`,
        rangeSummary,
        subItems,
      }
    }
    if (/^附[录件]/.test(h)) {
      return {
        si,
        isNarrative: false,
        badge: '附录',
        outlineIdx: '附录',
        outlineTitle: h,
        rangeSummary: '',
        subItems: [],
      }
    }
    if (hasMainUnlessCn && s.when?.source_country === 'CN') {
      const idxNo = cnSeq++
      return {
        si,
        isNarrative: false,
        badge: `中国范本 · ${idxNo}`,
        outlineIdx: `范本 ${idxNo}`,
        outlineTitle: h || `中国标准范本第 ${idxNo} 节`,
        rangeSummary: '',
        subItems: [],
      }
    }
    const cNo = chap++
    return {
      si,
      isNarrative: false,
      badge: `第 ${cNo} 章`,
      outlineIdx: `第 ${cNo} 章`,
      outlineTitle: h || `第 ${cNo} 章（未命名）`,
      rangeSummary: '',
      subItems: [],
    }
  })
})

interface TemplateVersionItem {
  version_no: number
  name: string
  status_label: string
  customized: boolean
  updated_at_label: string
  chapters_count: number | null
  facts_count: number | null
  is_current: boolean
}
const historyVisible = ref(false)
const historyLoading = ref(false)
const historyTargetName = ref('')
const historyTargetCode = ref('')
const historyVersions = ref<TemplateVersionItem[]>([])

function formatTplStatus(status?: string | null, isCurrent?: boolean): string {
  if (isCurrent) return '现行生效'
  const s = (status || '').trim().toLowerCase()
  if (s === 'published' || s === 'active') return '历史已发布'
  if (s === 'draft') return '草稿'
  if (s === 'archived' || s === 'superseded') return '历史归档'
  return '历史版本'
}

async function openVersionHistory(code: string, fallbackName: string, fallbackVerNo: number, currentDef?: Definition) {
  historyTargetCode.value = code
  historyTargetName.value = fallbackName
  historyVisible.value = true
  historyLoading.value = true
  historyVersions.value = []

  let defForStats: Definition | undefined = currentDef
  if (!defForStats) {
    try {
      const d = await api.get<{ code: string; name: string; version_no: number; definition: Definition }>(
        `/api/admin/document-templates/${encodeURIComponent(code)}`,
      )
      defForStats = d.definition
      if (d.name) historyTargetName.value = d.name
    } catch {
      /* 降级使用列表已有元数据 */
    }
  }

  let rawList: Array<Record<string, unknown>> = []
  try {
    const res = await api.get<unknown>(`/api/admin/document-templates/${encodeURIComponent(code)}/versions`)
    if (Array.isArray(res)) {
      rawList = res as Array<Record<string, unknown>>
    } else if (res && typeof res === 'object') {
      const rec = res as Record<string, unknown>
      if (Array.isArray(rec.items)) rawList = rec.items as Array<Record<string, unknown>>
      else if (Array.isArray(rec.versions)) rawList = rec.versions as Array<Record<string, unknown>>
    }
  } catch {
    rawList = []
  }

  if (rawList.length > 0) {
    const sorted = [...rawList].sort((a, b) => Number(b.version_no || 0) - Number(a.version_no || 0))
    const maxVer = Number(sorted[0]?.version_no || fallbackVerNo)
    historyVersions.value = sorted.map((item) => {
      const vNo = Number(item.version_no || fallbackVerNo)
      const isCurrent = vNo === maxVer
      const itemDef = (item.definition as Definition | undefined) || (isCurrent ? defForStats : undefined)
      const rawTime = String(item.updated_at || item.created_at || '').trim()
      const chaptersCount =
        typeof item.chapters_count === 'number'
          ? item.chapters_count
          : typeof item.sections_count === 'number'
            ? item.sections_count
            : itemDef
              ? countDefinitionChapters(itemDef)
              : null
      const factsCount =
        typeof item.facts_count === 'number'
          ? item.facts_count
          : itemDef
            ? (itemDef.facts || []).length
            : null
      return {
        version_no: vNo,
        name: String(item.name || item.name_zh || historyTargetName.value),
        status_label: formatTplStatus(String(item.status || ''), isCurrent),
        customized: Boolean(item.customized ?? (vNo > 1 || rawTime)),
        updated_at_label: rawTime ? localTime(rawTime) || rawTime.slice(0, 16) : '系统标准预置',
        chapters_count: chaptersCount,
        facts_count: factsCount,
        is_current: isCurrent,
      }
    })
  } else {
    const localRows = rows.value
      .filter((r) => r.code === code)
      .sort((a, b) => b.version_no - a.version_no)
    if (localRows.length > 0) {
      const maxVer = localRows[0].version_no
      historyVersions.value = localRows.map((r) => {
        const isCurrent = r.version_no === maxVer
        return {
          version_no: r.version_no,
          name: r.name || historyTargetName.value,
          status_label: formatTplStatus(r.status, isCurrent),
          customized: Boolean(r.customized || r.version_no > 1),
          updated_at_label: r.updated_at ? localTime(r.updated_at) || r.updated_at.slice(0, 16) : '系统标准预置',
          chapters_count: isCurrent && defForStats ? countDefinitionChapters(defForStats) : null,
          facts_count: isCurrent && defForStats ? (defForStats.facts || []).length : null,
          is_current: isCurrent,
        }
      })
    } else {
      historyVersions.value = [
        {
          version_no: fallbackVerNo,
          name: historyTargetName.value,
          status_label: '现行生效',
          customized: fallbackVerNo > 1,
          updated_at_label: '系统标准预置',
          chapters_count: defForStats ? countDefinitionChapters(defForStats) : null,
          facts_count: defForStats ? (defForStats.facts || []).length : null,
          is_current: true,
        },
      ]
    }
  }
  historyLoading.value = false
}

function jumpTo(anchorId: string, expandFacts = false) {
  activeOutline.value = anchorId
  if (expandFacts) showFactsPanel.value = true
  if (editorMode.value !== 'edit' && anchorId === 'tpl-facts') {
    editorMode.value = 'edit'
  }
  window.setTimeout(() => {
    document.getElementById(anchorId)?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }, 60)
}

function addSection() {
  if (!editing.value) return
  editing.value.def.sections.push({ heading: '新增章节', blocks: [{ type: 'text', text: '' }] })
  const nextIdx = editing.value.def.sections.length - 1
  editorMode.value = 'edit'
  jumpTo(`tpl-sec-${nextIdx}`)
}

function moveSection(si: number, delta: number) {
  if (!editing.value) return
  const arr = editing.value.def.sections
  const target = si + delta
  if (target < 0 || target >= arr.length) return
  const [moved] = arr.splice(si, 1)
  arr.splice(target, 0, moved)
}

async function load() {
  rows.value = await api.get<Row[]>('/api/admin/document-templates')
}

const latest = computed(() => {
  const seen = new Map<string, Row>()
  for (const r of rows.value) if (!seen.has(r.code) || seen.get(r.code)!.version_no < r.version_no) seen.set(r.code, r)
  return [...seen.values()]
})
const hasCustomized = computed(() => latest.value.some((r) => !!r.customized))

async function edit(code: string) {
  busy.value = true
  try {
    const d = await api.get<{ code: string; name: string; version_no: number; definition: Definition }>(
      `/api/admin/document-templates/${code}`,
    )
    editorMode.value = 'edit'
    showFactsPanel.value = false
    activeOutline.value = 'tpl-title'
    editing.value = {
      code: d.code,
      name: d.name,
      version_no: d.version_no,
      def: hydrateDefinitionForDisplay(d.definition),
    }
  } catch (e) {
    ElMessage.error((e as Error).message || '加载失败')
  } finally {
    busy.value = false
  }
}

function listText(b: Block): string {
  return (b.items || []).join('\n')
}
function setList(b: Block, v: string) {
  b.items = v.split('\n').map((s) => s.trim()).filter(Boolean)
}

async function save() {
  const e = editing.value
  if (!e) return
  busy.value = true
  try {
    await api.put<{ version_no: number }>(`/api/admin/document-templates/${e.code}`, {
      name_zh: e.name,
      definition: serializeDefinitionForSave(e.def),
    })
    ElMessage.success('已保存修改，仅对后续新生成的文件生效，已有文件不受影响')
    editing.value = null
    await load()
  } catch (err) {
    ElMessage.error((err as Error).message || '保存失败')
  } finally {
    busy.value = false
  }
}

interface Official { code: string; country_zh: string; name_zh: string; authority_zh: string; version: string; file: string; source_url: string; used_for: string }
const officials = ref<Official[]>([])
const officialGroups = computed(() => [
  { title: '数据出境合规评估官方原表', hint: '数据发出国配有官方原表时，对应合规文件直接按官方原件版式填报导出', rows: officials.value.filter((o) => !o.used_for.startsWith('海外属地准入')) },
  { title: '海外属地准入官方申报原表', hint: '企业在海外属地准入办理事项时，平台按官方原表版式带入填报信息', rows: officials.value.filter((o) => o.used_for.startsWith('海外属地准入')) },
].filter((g) => g.rows.length))
async function loadOfficial() {
  try { officials.value = await api.get<Official[]>('/admin/document-templates/official') } catch (err) { ElMessage.error((err as Error).message) }
}
async function downloadOriginal(o: Official) {
  const res = await fetch(`/api/admin/document-templates/official/${o.code}/original`, { headers: { Authorization: `Bearer ${api.token()}` } })
  if (!res.ok) { ElMessage.error('下载失败'); return }
  const url = URL.createObjectURL(await res.blob())
  const a = document.createElement('a'); a.href = url; a.download = serverFilename(res, o.name_zh); a.click()
  window.setTimeout(() => URL.revokeObjectURL(url), 30000)
}

onMounted(() => { load(); loadOfficial() })
</script>

<template>
  <div class="management-page tpl-page">
    <header v-if="!editing" class="page-heading">
      <div>
        <h1>合规文件模板</h1>
        <p>管理 8 类合规文件的标准结构大纲与各国监管官方原件目录；保存修订后仅作用于新生成的文件，历史已出具报告保持不变。</p>
      </div>
    </header>

    <el-table v-if="!editing" :data="latest" size="small" border>
      <el-table-column prop="name" label="合规文件名称" min-width="220" />
      <el-table-column label="当前版本" width="120">
        <template #default="{ row }">
          <span class="muted">第 {{ row.version_no }} 版</span>
        </template>
      </el-table-column>
      <el-table-column v-if="hasCustomized" label="状态" width="180">
        <template #default="{ row }">
          <span v-if="row.customized" class="cy-status cy-status--active">已自定义{{ row.updated_at ? `（${row.updated_at.slice(0, 10)}）` : '' }}</span>
          <span v-else class="muted">系统标准模板</span>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="196" align="right" fixed="right" class-name="cy-col-nowrap">
        <template #default="{ row }">
          <el-button
            link
            type="primary"
            size="small"
            data-testid="btn-tpl-history-list"
            @click="openVersionHistory(row.code, row.name, row.version_no)"
          >
            版本历史
          </el-button>
          <el-button link type="primary" size="small" data-testid="btn-edit-template" @click="edit(row.code)">编辑大纲</el-button>
        </template>
      </el-table-column>
    </el-table>

    <section v-if="!editing" class="official" data-testid="official-templates">
      <template v-for="g in officialGroups" :key="g.title">
        <h3>{{ g.title }}<small class="muted">（{{ g.rows.length }} 份）{{ g.hint }}</small></h3>
        <el-table :data="g.rows" size="small" border>
          <el-table-column prop="country_zh" label="国家" width="84" />
          <el-table-column label="官方原表" min-width="220">
            <template #default="{ row }">
              <strong>{{ row.name_zh }}</strong>
              <div class="muted">{{ row.authority_zh }}</div>
            </template>
          </el-table-column>
          <el-table-column prop="version" label="版本" min-width="180" />
          <el-table-column prop="used_for" label="适用场景" min-width="210" />
          <el-table-column label="操作" width="168" align="right" fixed="right" class-name="cy-col-nowrap">
            <template #default="{ row }">
              <el-button v-if="row.file" link type="primary" size="small" @click="downloadOriginal(row)">下载原件</el-button>
              <a :href="row.source_url" target="_blank" rel="noopener noreferrer" class="src">官方来源</a>
            </template>
          </el-table-column>
        </el-table>
      </template>
    </section>

    <div v-else class="editor-studio" data-testid="template-editor-studio">
      <div class="studio-topbar">
        <button type="button" class="back-btn" @click="editing = null">← 返回模板目录</button>
        <span class="sep">/</span>
        <el-input v-model="editing.name" class="tpl-name-input" aria-label="文件模板名称" />
        <span class="ver-pill">第 {{ editing.version_no }} 版</span>
        <el-button
          size="small"
          data-testid="btn-tpl-version-history"
          @click="openVersionHistory(editing.code, editing.name, editing.version_no, editing.def)"
        >
          版本历史
        </el-button>
        <div class="mode-switch" role="group" aria-label="模板查看方式">
          <button type="button" class="mode-btn" :class="{ active: editorMode === 'edit' }" data-testid="tpl-mode-edit" @click="editorMode = 'edit'">结构编辑</button>
          <button type="button" class="mode-btn" :class="{ active: editorMode === 'preview' }" data-testid="tpl-mode-preview" @click="editorMode = 'preview'">成稿版式预览</button>
        </div>
        <span class="grow" />
        <el-button @click="editing = null">放弃修改</el-button>
        <el-button type="primary" :loading="busy" data-testid="tpl-save" @click="save">保存修改</el-button>
      </div>

      <p class="studio-note">修改保存后仅对后续新生成的报告生效，企业已生成的历史成稿保持原样。正文或标题中的【栏目名称】将在导出时自动替换为企业填报内容。</p>

      <div class="studio-body">
        <aside class="outline-rail" data-testid="tpl-outline-rail">
          <div class="rail-head">文档章节大纲</div>
          <button type="button" class="outline-item" :class="{ active: activeOutline === 'tpl-title' }" @click="jumpTo('tpl-title')">
            <span class="o-idx">封面</span>
            <span class="o-text">文件标题格式</span>
          </button>
          <button type="button" class="outline-item" :class="{ active: activeOutline === 'tpl-facts' }" @click="jumpTo('tpl-facts', true)">
            <span class="o-idx">采集</span>
            <span class="o-text">关联填报项（{{ editing.def.facts.length }}）</span>
          </button>
          <div class="rail-divider" />
          <template v-for="(s, si) in editing.def.sections" :key="`nav-${si}`">
            <button
              type="button"
              class="outline-item"
              :class="{ active: activeOutline === `tpl-sec-${si}`, 'is-nar-group': Boolean(s.narrative) }"
              :title="sectionMetaList[si]?.outlineTitle"
              @click="jumpTo(`tpl-sec-${si}`)"
            >
              <span class="o-idx" :class="{ 'o-idx-nar': Boolean(s.narrative) }">{{ sectionMetaList[si]?.outlineIdx || `第 ${si + 1} 章` }}</span>
              <span class="o-text">{{ sectionMetaList[si]?.outlineTitle }}</span>
            </button>
            <button
              v-for="sub in sectionMetaList[si]?.subItems || []"
              :key="sub.anchorId"
              type="button"
              class="outline-item outline-sub-item"
              :class="{ active: activeOutline === sub.anchorId }"
              :title="sub.heading"
              @click="jumpTo(sub.anchorId)"
            >
              <span class="o-idx o-idx-sub">{{ sub.badge }}</span>
              <span class="o-text">{{ sub.heading }}</span>
            </button>
          </template>
          <el-button size="small" class="add-sec-rail" @click="addSection">+ 新增章节</el-button>
        </aside>

        <div v-if="editorMode === 'edit'" class="editor-canvas">
          <section id="tpl-title" class="tpl-card">
            <div class="card-head">
              <strong>封面与文件标题</strong>
              <span class="muted">支持插入企业名称、国家或业务场景等动态栏目</span>
            </div>
            <div v-if="editing.def.facts.length" class="fact-bar">
              <span class="capsule-hint">常用栏目：</span>
              <button
                v-for="f in quickFacts"
                :key="`title-${f.key}`"
                type="button"
                class="fact-capsule"
                @click="insertFactIntoTitle(f)"
              >
                + 【{{ factDisplayLabel(f) }}】
              </button>
              <el-dropdown v-if="moreFacts.length" trigger="click" @command="(k: string) => insertFactKeyIntoTitle(k)">
                <button type="button" class="fact-more-btn">更多填报项（{{ moreFacts.length }}）▾</button>
                <template #dropdown>
                  <el-dropdown-menu>
                    <el-dropdown-item v-for="mf in moreFacts" :key="`tm-${mf.key}`" :command="mf.key">
                      【{{ factDisplayLabel(mf) }}】
                    </el-dropdown-item>
                  </el-dropdown-menu>
                </template>
              </el-dropdown>
            </div>
            <el-input v-model="editing.def.title" placeholder="请输入导出报告标题模板" />
          </section>

          <section id="tpl-facts" class="tpl-card">
            <div class="card-head clickable" @click="showFactsPanel = !showFactsPanel">
              <div>
                <strong>关联采集信息项（共 {{ editing.def.facts.length }} 项）</strong>
                <span class="muted" style="margin-left: 10px">优先自动带入评估项目已有信息，未填栏目向企业经办人采集</span>
              </div>
              <button type="button" class="fold-btn" data-testid="tpl-facts-toggle">{{ showFactsPanel ? '收起栏目表' : '展开配置栏目' }}</button>
            </div>
            <div v-if="!showFactsPanel" class="facts-summary">
              <span v-for="f in editing.def.facts.slice(0, 8)" :key="`sum-${f.key}`" class="sum-chip">{{ factDisplayLabel(f) }}</span>
              <span v-if="editing.def.facts.length > 8" class="muted">等共 {{ editing.def.facts.length }} 个采集项</span>
            </div>
            <div v-else class="facts-editor-list">
              <div class="fact-table-head">
                <span>序号</span>
                <span>栏目名称</span>
                <span>向经办人展示的填报问题</span>
                <span>法规依据与用途说明</span>
                <span>操作</span>
              </div>
              <div v-for="(f, i) in editing.def.facts" :key="f.key" class="fact-row">
                <span class="fact-idx">第 {{ i + 1 }} 项</span>
                <el-input v-model="f.label" size="small" placeholder="栏目名称" />
                <el-input v-model="f.question" size="small" placeholder="填报提示问题" />
                <el-input v-model="f.why" size="small" placeholder="法规依据与用途说明" />
                <el-button link type="danger" @click="editing.def.facts.splice(i, 1)">移除</el-button>
              </div>
            </div>
          </section>

          <section v-for="(s, si) in editing.def.sections" :id="`tpl-sec-${si}`" :key="si" class="tpl-card section-card">
            <template v-if="s.narrative">
              <div class="card-head">
                <div class="sec-title-left">
                  <span class="sec-badge nar">{{ sectionMetaList[si]?.badge || '核心论述' }}</span>
                  <div class="nar-title-stack">
                    <strong>{{ sectionMetaList[si]?.rangeSummary || `深度论述章节（共 ${(editing.def.narrative || []).length} 节）` }}</strong>
                    <span class="muted">按项目已确认事实与适用法规条文生成的连续论述章节</span>
                  </div>
                </div>
                <div class="sec-actions">
                  <el-button link :disabled="si === 0" @click="moveSection(si, -1)">上移</el-button>
                  <el-button link :disabled="si === editing.def.sections.length - 1" @click="moveSection(si, 1)">下移</el-button>
                </div>
              </div>
              <div
                v-for="(np, ni) in editing.def.narrative || []"
                :id="`tpl-nar-${si}-${ni}`"
                :key="np.key || ni"
                class="nar-part"
              >
                <div class="nar-head">
                  <span class="nar-idx">{{ sectionMetaList[si]?.subItems?.[ni]?.cardBadge || `论述第 ${ni + 1} 节` }}</span>
                  <el-input v-model="np.heading" placeholder="本节标题" />
                </div>
                <div class="nar-meta muted">
                  关联栏目：{{ (np.facts || []).map((k) => factLabelByKey(k)).join('、') || '项目通用背景' }}
                  <span v-if="np.flow"> · 自动附数据处理流程图与环节表</span>
                  <span v-if="np.field_table"> · 自动拆解数据项明细表</span>
                  <span v-if="np.risks"> · 自动附主要风险评估与等级判定表</span>
                </div>
                <el-input v-model="np.brief" type="textarea" :rows="3" placeholder="本节编写要求与法理论证要点" />
              </div>
            </template>

            <template v-else>
              <div class="card-head">
                <div class="sec-title-left grow">
                  <span class="sec-badge">{{ sectionMetaList[si]?.badge || `第 ${si + 1} 章` }}</span>
                  <el-input v-model="s.heading" placeholder="请输入本章标题" />
                </div>
                <div class="sec-actions">
                  <el-button link :disabled="si === 0" @click="moveSection(si, -1)">上移</el-button>
                  <el-button link :disabled="si === editing.def.sections.length - 1" @click="moveSection(si, 1)">下移</el-button>
                  <el-button link type="danger" @click="editing.def.sections.splice(si, 1)">删除本章</el-button>
                </div>
              </div>

              <div v-for="(b, bi) in s.blocks || []" :key="bi" class="block-card">
                <div class="block-top">
                  <span class="block-type-pill">{{ BLOCK_LABEL[b.type] || b.type }}</span>
                  <div v-if="(b.type === 'text' || b.type === 'checklist') && editing.def.facts.length" class="fact-bar compact">
                    <button
                      v-for="f in quickFacts"
                      :key="`blk-${si}-${bi}-${f.key}`"
                      type="button"
                      class="fact-capsule"
                      @click="insertFactIntoBlock(b, f)"
                    >
                      + 【{{ factDisplayLabel(f) }}】
                    </button>
                    <el-dropdown v-if="moreFacts.length" trigger="click" @command="(k: string) => insertFactKeyIntoBlock(b, k)">
                      <button type="button" class="fact-more-btn">更多栏目 ▾</button>
                      <template #dropdown>
                        <el-dropdown-menu>
                          <el-dropdown-item v-for="mf in moreFacts" :key="`bm-${si}-${bi}-${mf.key}`" :command="mf.key">
                            【{{ factDisplayLabel(mf) }}】
                          </el-dropdown-item>
                        </el-dropdown-menu>
                      </template>
                    </el-dropdown>
                  </div>
                  <span class="grow" />
                  <el-button link type="danger" @click="(s.blocks || []).splice(bi, 1)">删除内容块</el-button>
                </div>

                <el-input v-if="b.type === 'text'" v-model="b.text" type="textarea" :rows="3" placeholder="请输入段落内容，可点击上方按钮插入企业填报项" />
                <el-input
                  v-else-if="b.type === 'checklist'"
                  :model-value="listText(b)"
                  type="textarea"
                  :rows="4"
                  placeholder="每行填写一条核查要点"
                  @update:model-value="(v: string) => setList(b, v)"
                />
                <div v-else-if="b.type === 'facts_table'" class="facts-select-box">
                  <span class="muted">本表展示的填报栏目：</span>
                  <el-select
                    v-model="b.keys"
                    multiple
                    filterable
                    style="width: 100%"
                    placeholder="选择本信息表纳入的栏目"
                  >
                    <el-option v-for="fo in factOptions" :key="fo.value" :label="fo.label" :value="fo.value" />
                  </el-select>
                </div>
                <div v-else-if="b.type === 'citations'" class="citations-cfg muted">
                  自动引用评估匹配的现行有效条文，最多展示
                  <el-input-number v-model="b.limit" :min="1" :max="30" size="small" /> 条；限定合规维度：
                  <el-select
                    v-model="b.dimensions"
                    multiple
                    clearable
                    style="width: 280px"
                    placeholder="全部八个维度"
                  >
                    <el-option v-for="dm in DIM_OPTIONS" :key="dm.value" :label="dm.label" :value="dm.value" />
                  </el-select>
                </div>
                <div v-else class="auto-block-note">
                  已绑定组件：<strong>{{ BLOCK_LABEL[b.type] || '业务表格' }}{{ b.name && SLOT_LABEL[b.name] ? `（${SLOT_LABEL[b.name]}）` : '' }}</strong>，导出报告时按项目填报数据自动渲染标准表格。
                </div>
              </div>

              <div class="add-block-bar">
                <el-button size="small" @click="(s.blocks || (s.blocks = [])).push({ type: 'text', text: '' })">+ 添加段落</el-button>
                <el-button size="small" @click="(s.blocks || (s.blocks = [])).push({ type: 'checklist', items: [''] })">+ 添加要点清单</el-button>
                <el-button size="small" @click="(s.blocks || (s.blocks = [])).push({ type: 'facts_table', keys: [] })">+ 添加信息表</el-button>
              </div>
            </template>
          </section>
        </div>

        <div v-else class="preview-canvas" data-testid="tpl-a4-preview">
          <article class="a4-sheet">
            <header class="a4-header">
              <div class="a4-badge">导出成稿版式结构预览</div>
              <h2 id="tpl-title">{{ editing.def.title || editing.name }}</h2>
              <p class="muted">注：方括号【栏目名】在企业实际导出 Word / PDF 时自动替换为项目真实填报值。</p>
            </header>

            <section v-for="(s, si) in editing.def.sections" :id="`tpl-sec-${si}`" :key="`pv-${si}`" class="a4-sec">
              <template v-if="s.narrative">
                <div
                  v-for="(np, ni) in editing.def.narrative || []"
                  :id="`tpl-nar-${si}-${ni}`"
                  :key="`pvn-${ni}`"
                  class="a4-nar-sec"
                >
                  <h3>{{ np.heading }}</h3>
                  <div class="a4-nar-box">
                    <div class="a4-nar-tag">{{ sectionMetaList[si]?.subItems?.[ni]?.cardBadge || '深度论述章节' }}</div>
                    <p>{{ np.brief }}</p>
                    <div class="muted">
                      引用事实源：{{ (np.facts || []).map((k) => `【${factLabelByKey(k)}】`).join('、') || '项目基础信息' }}
                    </div>
                  </div>
                </div>
              </template>
              <template v-else>
                <h3 v-if="s.heading">{{ s.heading }}</h3>
                <div v-for="(b, bi) in s.blocks || []" :key="`pvb-${bi}`" class="a4-block">
                  <p v-if="b.type === 'text'" class="a4-p">{{ b.text || '（空段落）' }}</p>
                  <ul v-else-if="b.type === 'checklist'" class="a4-list">
                    <li v-for="(it, ii) in b.items || []" :key="ii">{{ it }}</li>
                  </ul>
                  <table v-else-if="b.type === 'facts_table'" class="a4-table">
                    <thead><tr><th style="width: 32%">填报事项</th><th>企业项目对应内容</th></tr></thead>
                    <tbody>
                      <tr v-for="fk in b.keys || []" :key="fk">
                        <td>{{ factLabelByKey(fk) }}</td>
                        <td class="muted">【{{ factLabelByKey(fk) }}】</td>
                      </tr>
                    </tbody>
                  </table>
                  <div v-else class="a4-slot-box">
                    <strong>{{ BLOCK_LABEL[b.type] || '自动生成表格' }}{{ b.name && SLOT_LABEL[b.name] ? `：${SLOT_LABEL[b.name]}` : '' }}</strong>
                    <span class="muted">（生成正式 Word / PDF 报告时自动排入标准双栏或多列表格）</span>
                  </div>
                </div>
              </template>
            </section>
          </article>
        </div>
      </div>
    </div>

    <el-drawer
      v-model="historyVisible"
      :title="`模板版本历史 · ${historyTargetName}`"
      size="min(720px, 94vw)"
      append-to-body
      data-testid="tpl-version-history-drawer"
    >
      <div v-loading="historyLoading" class="tpl-history-pane">
        <el-alert
          type="info"
          :closable="false"
          show-icon
          title="每次保存大纲修订均生成新版本并留存历史快照；新版本仅作用于后续新生成的合规文件，企业已出具的历史文件保持原版不变。"
        />
        <el-table :data="historyVersions" border size="small" style="margin-top: 14px">
          <el-table-column label="版本号" width="136" class-name="cy-col-nowrap">
            <template #default="{ row }">
              <strong>v{{ row.version_no }}</strong>
              <span class="muted" style="margin-left: 4px">（第 {{ row.version_no }} 版）</span>
            </template>
          </el-table-column>
          <el-table-column label="发布状态" width="160">
            <template #default="{ row }">
              <el-tag :type="row.is_current ? 'success' : 'info'" size="small">{{ row.status_label }}</el-tag>
              <span class="muted" style="display: block; margin-top: 2px">
                {{ row.customized ? '管理员修订版' : '系统标准模板' }}
              </span>
            </template>
          </el-table-column>
          <el-table-column label="章节与采集项统计" min-width="156">
            <template #default="{ row }">
              <span v-if="row.chapters_count !== null || row.facts_count !== null">
                共 {{ row.chapters_count ?? '—' }} 章 · 采集项 {{ row.facts_count ?? '—' }} 项
              </span>
              <span v-else class="muted">历史版本结构已归档</span>
            </template>
          </el-table-column>
          <el-table-column label="更新时间" width="152" class-name="cy-col-nowrap">
            <template #default="{ row }">{{ row.updated_at_label }}</template>
          </el-table-column>
        </el-table>
      </div>
    </el-drawer>
  </div>
</template>

<style scoped>
.tpl-page strong, .tpl-page b, .tpl-page h1, .tpl-page h2, .tpl-page h3, .tpl-page h4 { font-weight: 600; }
.muted { color: #64748b; font-size: 13px; }
.grow { flex: 1; }
.official { margin-top: 28px; }
.official h3 { font-size: 16px; margin: 18px 0 10px; }
.src { margin-left: 10px; font-size: 13px; color: #1d4ed8; }

.editor-studio { display: flex; flex-direction: column; gap: 10px; }
.studio-topbar { position: sticky; top: 0; z-index: 6; display: flex; align-items: center; gap: 10px; flex-wrap: wrap; padding: 10px 14px; background: #fff; border: 1px solid #e2e8f0; border-radius: 10px; box-shadow: 0 2px 8px rgba(15, 23, 42, 0.04); }
.back-btn { border: 0; background: none; color: #1d4ed8; font-size: 13px; cursor: pointer; padding: 4px 2px; white-space: nowrap; }
.back-btn:hover { text-decoration: underline; }
.sep { color: #cbd5e1; font-size: 14px; }
.tpl-name-input { flex: 1 1 320px; max-width: 440px; min-width: 240px; }
.ver-pill { font-size: 13px; color: #475569; background: #f1f5f9; border-radius: 999px; padding: 2px 10px; white-space: nowrap; }
.mode-switch { display: inline-flex; background: #f1f5f9; border-radius: 8px; padding: 2px; border: 1px solid #e2e8f0; }
.mode-btn { border: 0; background: transparent; color: #475569; font-size: 13px; padding: 4px 12px; border-radius: 6px; cursor: pointer; }
.mode-btn.active { background: #fff; color: #1d4ed8; font-weight: 600; box-shadow: 0 1px 3px rgba(15, 23, 42, 0.08); }
.studio-note { margin: 0; font-size: 13px; color: #64748b; }

.studio-body { display: grid; grid-template-columns: 248px minmax(0, 1fr); gap: 16px; align-items: start; }
.outline-rail { position: sticky; top: 68px; background: #fff; border: 1px solid #e2e8f0; border-radius: 10px; padding: 12px 10px; display: flex; flex-direction: column; gap: 4px; max-height: calc(100vh - 120px); overflow-y: auto; }
.rail-head { font-size: 13px; font-weight: 600; color: #334155; padding: 2px 6px 6px; }
.rail-divider { height: 1px; background: #f1f5f9; margin: 4px 0; }
.outline-item { display: flex; align-items: center; gap: 8px; width: 100%; border: 0; background: transparent; padding: 7px 8px; border-radius: 6px; text-align: left; cursor: pointer; color: #334155; font-size: 13px; }
.outline-item:hover { background: #f8fafc; }
.outline-item.active { background: #eff6ff; color: #1d4ed8; font-weight: 600; }
.outline-sub-item { padding: 5px 8px 5px 18px; font-size: 12.5px; color: #475569; }
.o-idx { flex: none; font-size: 12px; color: #64748b; background: #f1f5f9; border-radius: 4px; padding: 1px 6px; white-space: nowrap; }
.o-idx-nar { color: #0f766e; background: #f0fdfa; border: 1px solid #99f6e4; }
.o-idx-sub { color: #0f766e; background: #f8fafc; border: 1px solid #cbd5e1; }
.outline-item.active .o-idx { background: #dbeafe; color: #1d4ed8; }
.o-text { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; min-width: 0; }
.add-sec-rail { margin-top: 8px; width: 100%; }

.editor-canvas { display: flex; flex-direction: column; gap: 14px; min-width: 0; }
.tpl-card { background: #fff; border: 1px solid #e2e8f0; border-radius: 10px; padding: 14px 16px; display: flex; flex-direction: column; gap: 10px; }
.card-head { display: flex; justify-content: space-between; align-items: center; gap: 12px; flex-wrap: wrap; }
.card-head.clickable { cursor: pointer; }
.fold-btn { border: 0; background: none; color: #1d4ed8; font-size: 13px; cursor: pointer; }
.fold-btn:hover { text-decoration: underline; }

.facts-summary { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; }
.sum-chip { font-size: 13px; color: #334155; background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 6px; padding: 2px 8px; }
.facts-editor-list { display: flex; flex-direction: column; gap: 8px; padding-top: 6px; border-top: 1px dashed #e2e8f0; }
.fact-table-head { display: grid; grid-template-columns: 76px 1.1fr 1.8fr 1.5fr 52px; gap: 8px; font-size: 13px; color: #64748b; padding: 0 4px; }
.fact-row { display: grid; grid-template-columns: 76px 1.1fr 1.8fr 1.5fr 52px; gap: 8px; align-items: center; }
.fact-idx { font-size: 13px; color: #475569; background: #f1f5f9; border-radius: 6px; padding: 4px 6px; text-align: center; }

.sec-title-left { display: flex; align-items: center; gap: 10px; min-width: 0; }
.sec-badge { flex: none; font-size: 13px; font-weight: 600; color: #1d4ed8; background: #eff6ff; border: 1px solid #bfdbfe; border-radius: 6px; padding: 3px 9px; white-space: nowrap; }
.sec-badge.nar { color: #0f766e; background: #f0fdfa; border-color: #99f6e4; }
.sec-actions { display: inline-flex; align-items: center; gap: 6px; flex: none; }

.nar-title-stack { display: flex; flex-direction: column; gap: 2px; min-width: 0; }
.nar-part { background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 8px; padding: 12px; display: flex; flex-direction: column; gap: 6px; }
.nar-head { display: grid; grid-template-columns: 152px 1fr; gap: 10px; align-items: center; }
.nar-idx { font-size: 13px; font-weight: 600; color: #0f766e; white-space: nowrap; }
.nar-meta { line-height: 1.5; }
.tpl-history-pane { display: flex; flex-direction: column; }

.block-card { background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 8px; padding: 10px 12px; display: flex; flex-direction: column; gap: 8px; }
.block-top { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.block-type-pill { font-size: 13px; font-weight: 600; color: #334155; background: #e2e8f0; border-radius: 6px; padding: 2px 8px; }
.fact-bar { display: flex; flex-wrap: wrap; align-items: center; gap: 6px; }
.fact-bar.compact { gap: 5px; }
.capsule-hint { font-size: 13px; color: #64748b; }
.fact-capsule { border: 1px solid #cbd5e1; background: #fff; color: #1d4ed8; border-radius: 999px; padding: 2px 9px; font-size: 13px; line-height: 1.5; cursor: pointer; }
.fact-capsule:hover { background: #eff6ff; border-color: #93c5fd; }
.fact-more-btn { border: 1px dashed #94a3b8; background: #fff; color: #334155; border-radius: 999px; padding: 2px 10px; font-size: 13px; line-height: 1.5; cursor: pointer; }
.fact-more-btn:hover { color: #1d4ed8; border-color: #60a5fa; }
.facts-select-box { display: flex; flex-direction: column; gap: 6px; }
.citations-cfg { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.auto-block-note { font-size: 13px; color: #475569; padding: 4px 2px; }
.add-block-bar { display: flex; gap: 8px; flex-wrap: wrap; padding-top: 4px; }

.preview-canvas { min-width: 0; }
.a4-sheet { background: #fff; border: 1px solid #cbd5e1; border-radius: 10px; padding: 28px 34px; box-shadow: 0 4px 16px rgba(15, 23, 42, 0.05); display: flex; flex-direction: column; gap: 18px; }
.a4-header { border-bottom: 2px solid #1e293b; padding-bottom: 14px; text-align: center; }
.a4-badge { display: inline-block; font-size: 13px; color: #1d4ed8; background: #eff6ff; border-radius: 999px; padding: 2px 12px; margin-bottom: 8px; }
.a4-header h2 { margin: 0 0 6px; font-size: 18px; color: #0f172a; }
.a4-sec { display: flex; flex-direction: column; gap: 10px; }
.a4-sec h3 { margin: 6px 0 2px; font-size: 15px; color: #0f172a; border-left: 3px solid #2563eb; padding-left: 8px; }
.a4-p { margin: 0; font-size: 14px; line-height: 1.75; color: #1e293b; white-space: pre-line; }
.a4-list { margin: 0; padding-left: 20px; font-size: 14px; line-height: 1.75; color: #1e293b; }
.a4-table { width: 100%; border-collapse: collapse; font-size: 13px; }
.a4-table th, .a4-table td { border: 1px solid #cbd5e1; padding: 7px 10px; text-align: left; }
.a4-table th { background: #f8fafc; color: #334155; font-weight: 600; }
.a4-nar-box { background: #f8fafc; border: 1px dashed #94a3b8; border-radius: 8px; padding: 12px 14px; font-size: 13px; line-height: 1.7; color: #334155; }
.a4-nar-tag { display: inline-block; font-size: 13px; font-weight: 600; color: #0f766e; margin-bottom: 4px; }
.a4-slot-box { background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 6px; padding: 10px 12px; font-size: 13px; display: flex; gap: 8px; align-items: baseline; flex-wrap: wrap; }

@media (max-width: 1100px) {
  .studio-body { grid-template-columns: 1fr; }
  .outline-rail { position: static; max-height: none; flex-direction: row; flex-wrap: wrap; }
  .outline-item { width: auto; }
  .fact-table-head { display: none; }
  .fact-row { grid-template-columns: 1fr; }
}
</style>
