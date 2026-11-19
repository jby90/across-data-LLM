<script setup lang="ts">
// 主要风险按行填写：每项风险写内容、选可能性和严重程度（低/中/高）、至少一项应对措施，可写责任人和完成时限。
// 支持结合项目已填业务与整改措施执行初评识别与落实措施后动态复评，实时呈现风险降级与可控风险剔除轨迹。
import { ElMessage } from 'element-plus'
import { computed, ref, watch } from 'vue'
import { api } from '../api'

interface Row {
  id: string
  risk: string
  likelihood: string
  severity: string
  measures: string
  owner: string
  due: string
  mitigated?: string
  init_likelihood?: string
  init_severity?: string
  reset_init?: boolean
}
const props = defineProps<{
  modelValue: string
  disabled?: boolean
  projectId?: string
  riskSync?: { stale?: boolean; can_diagnose?: boolean } | null
}>()
const emit = defineEmits<{
  (e: 'update:modelValue', v: string): void
  (e: 'save'): void
  (e: 'diagnosed', project: any): void
}>()

const LEVELS = ['低', '中', '高']
const MATRIX = [[0, 0, 1], [0, 1, 2], [1, 2, 2]]
const NAMES = ['低风险', '中风险', '高风险']
const newId = () => Math.random().toString(16).slice(2, 10)
const blank = (): Row => ({
  id: newId(),
  risk: '',
  likelihood: '',
  severity: '',
  measures: '',
  owner: '',
  due: '',
  mitigated: '',
  init_likelihood: '',
  init_severity: '',
})

function parse(v: string): { rows: Row[]; legacy: boolean } {
  const text = (v || '').trim()
  if (!text || text.startsWith('暂不确定')) return { rows: [], legacy: false }
  if (text.startsWith('[')) {
    try {
      const data = JSON.parse(text)
      if (Array.isArray(data)) {
        return {
          rows: data.map((r) => ({
            ...blank(),
            ...r,
            mitigated: (r?.mitigated === '1' || r?.mitigated === true || r?.mitigated === 'true') ? '1' : '',
            init_likelihood: LEVELS.includes(r?.init_likelihood) ? r.init_likelihood : '',
            init_severity: LEVELS.includes(r?.init_severity) ? r.init_severity : '',
          })),
          legacy: false,
        }
      }
    } catch { /* 不是行格式：按整段文字处理 */ }
  }
  return { rows: [{ ...blank(), id: 'r1', risk: text }], legacy: true }
}

function dump(rows: Row[]) {
  const clean = rows.map((r) => {
    const out: Record<string, unknown> = {
      id: r.id,
      risk: (r.risk || '').trim(),
      likelihood: LEVELS.includes(r.likelihood) ? r.likelihood : '',
      severity: LEVELS.includes(r.severity) ? r.severity : '',
      measures: (r.measures || '').trim(),
      owner: (r.owner || '').trim(),
      due: (r.due || '').trim(),
      mitigated: r.mitigated === '1' ? '1' : '',
      init_likelihood: LEVELS.includes(r.init_likelihood || '') && LEVELS.includes(r.init_severity || '') ? r.init_likelihood : '',
      init_severity: LEVELS.includes(r.init_likelihood || '') && LEVELS.includes(r.init_severity || '') ? r.init_severity : '',
    }
    if (r.reset_init) out.reset_init = true
    return out
  }).filter((r) => r.risk || r.likelihood || r.severity || r.measures || r.owner || r.due)
  return clean.length ? JSON.stringify(clean) : ''
}

const rows = ref<Row[]>([])
const legacy = ref(false)
const editingId = ref('')
const diagnosing = ref(false)
let last: string | null = null
watch(() => props.modelValue, (v) => {
  if (v === last) return
  const got = parse(v)
  rows.value = got.rows.length ? got.rows : [blank()]
  legacy.value = got.legacy
  last = v
}, { immediate: true })

function sync() { last = dump(rows.value); emit('update:modelValue', last) }
function save() {
  sync()
  emit('save')
  for (const r of rows.value) delete r.reset_init
}
function add() {
  const row = blank()
  rows.value.push(row)
  editingId.value = row.id
  sync()
}
function remove(i: number) {
  const removed = rows.value[i]?.id
  rows.value.splice(i, 1)
  if (removed && editingId.value === removed) editingId.value = ''
  if (!rows.value.length) rows.value.push(blank())
  save()
}
function resetInitialGrades() {
  for (const r of rows.value) {
    if (LEVELS.includes(r.likelihood) && LEVELS.includes(r.severity)) {
      r.init_likelihood = r.likelihood
      r.init_severity = r.severity
    } else {
      r.init_likelihood = ''
      r.init_severity = ''
    }
    r.reset_init = true
  }
  save()
}

async function diagnose() {
  if (!props.projectId || diagnosing.value) return
  diagnosing.value = true
  try {
    const res = await api.post<any>(`/api/analysis/doc-projects/${props.projectId}/risks/diagnose`, {})
    const q = (res.questions || []).find((x: any) => x.key === 'risk_rating')
    if (q) {
      const nextVal = String(q.value || '')
      const got = parse(nextVal)
      rows.value = got.rows.length ? got.rows : [blank()]
      legacy.value = got.legacy
      last = nextVal
      editingId.value = ''
      emit('update:modelValue', nextVal)
    }
    emit('diagnosed', res)
    ElMessage.success(res.diagnose_note || '已完成风险研判与等级更新')
  } catch (e) {
    ElMessage.error((e as Error).message)
  } finally {
    diagnosing.value = false
  }
}

const level = (r: Row) => (LEVELS.includes(r.likelihood) && LEVELS.includes(r.severity) ? NAMES[MATRIX[LEVELS.indexOf(r.likelihood)][LEVELS.indexOf(r.severity)]] : '')
const initLevel = (r: Row) => {
  if (LEVELS.includes(r.init_likelihood || '') && LEVELS.includes(r.init_severity || '')) {
    return NAMES[MATRIX[LEVELS.indexOf(r.init_likelihood!)][LEVELS.indexOf(r.init_severity!)]]
  }
  return level(r)
}
const isDown = (r: Row) => {
  const ilv = initLevel(r); const lv = level(r)
  return !!(ilv && lv && NAMES.indexOf(lv) < NAMES.indexOf(ilv))
}
const gaps = (r: Row) => [['risk', '风险内容'], ['likelihood', '可能性'], ['severity', '严重程度'], ['measures', '应对措施']]
  .filter(([k]) => !(r[k as keyof Row] as string || '').trim()).map(([, label]) => label)
const filled = computed(() => rows.value.filter((r) => r.risk || r.likelihood || r.severity || r.measures))
const isEditing = (r: Row) => !props.disabled && (editingId.value === r.id || gaps(r).length > 0 || (!r.risk && !r.measures))

const evo = computed(() => {
  const init = [0, 0, 0]
  const cur = [0, 0, 0]
  let down = 0; let up = 0; let excluded = 0; let graded = 0
  for (const r of rows.value) {
    const ilv = initLevel(r); const lv = level(r)
    if (ilv) { init[NAMES.indexOf(ilv)]++; graded++ }
    if (r.mitigated === '1') {
      if (r.risk || lv || ilv) excluded++
      continue
    }
    if (lv) cur[NAMES.indexOf(lv)]++
    if (ilv && lv && ilv !== lv) {
      if (NAMES.indexOf(lv) < NAMES.indexOf(ilv)) down++
      else up++
    }
  }
  return { init, cur, down, up, excluded, graded, hasChange: down > 0 || up > 0 || excluded > 0 }
})

const grid = computed(() => [2, 1, 0].map((i) => ({
  label: `可能性：${LEVELS[i]}`,
  cells: [0, 1, 2].map((j) => {
    const activeLabels: string[] = []
    const movedFrom: string[] = []
    rows.value.forEach((r, idx) => {
      const n = idx + 1
      const curHere = r.mitigated !== '1' && r.likelihood === LEVELS[i] && r.severity === LEVELS[j]
      const il = r.init_likelihood || r.likelihood
      const isv = r.init_severity || r.severity
      const initHere = il === LEVELS[i] && isv === LEVELS[j]
      if (curHere) {
        const ilv = initLevel(r); const lv = level(r)
        activeLabels.push(ilv && lv && ilv !== lv ? `风险 ${n}（原${ilv.replace('风险', '')}）` : `风险 ${n}`)
      } else if (initHere && (r.mitigated === '1' || (level(r) && (r.likelihood !== il || r.severity !== isv)))) {
        movedFrom.push(r.mitigated === '1' ? `原风险 ${n}（已整改）` : `原风险 ${n}（已降级）`)
      }
    })
    return {
      name: NAMES[MATRIX[i][j]],
      tone: MATRIX[i][j],
      activeLabels,
      movedFrom,
    }
  }),
})))
</script>

<template>
  <div class="risks" data-testid="risk-table">
    <div v-if="evo.graded > 0 || evo.excluded > 0 || (projectId && !disabled)" class="evo-bar" data-testid="risk-evolution-bar">
      <div v-if="evo.graded > 0 || evo.excluded > 0" class="evo-main">
        <b>风险动态变化</b>
        <span class="evo-pair">初评：高 {{ evo.init[2] }} · 中 {{ evo.init[1] }} · 低 {{ evo.init[0] }}</span>
        <span class="evo-arrow">→</span>
        <span class="evo-pair">当前正式报告：<b>高 {{ evo.cur[2] }}</b> · <b>中 {{ evo.cur[1] }}</b> · <b>低 {{ evo.cur[0] }}</b></span>
        <span v-if="evo.down > 0" class="lv t0">已降级 {{ evo.down }} 项</span>
        <span v-if="evo.excluded > 0" class="lv t0">已完成整改 {{ evo.excluded }} 项</span>
        <span v-if="evo.init[2] > 0 && evo.cur[2] === 0" class="lv t0">高风险已清零</span>
        <span v-if="evo.up > 0" class="lv t2">上调 {{ evo.up }} 项</span>
      </div>
      <div v-else class="evo-main">
        <span class="evo-empty">结合已填业务事实与安全防护措施，支持自动识别初评风险并在落实整改后重新评估降级</span>
      </div>
      <div v-if="!disabled" class="evo-actions">
        <el-button v-if="projectId" size="small" type="primary" plain :loading="diagnosing" data-testid="risk-ai-diagnose" @click="diagnose">
          {{ filled.length ? '按已填措施重新评估风险' : '按业务信息识别初评风险' }}
        </el-button>
        <button v-if="evo.hasChange" type="button" class="evo-reset" data-testid="risk-reset-init" @click="resetInitialGrades">设为初评起点</button>
      </div>
    </div>

    <div v-if="filled.length && riskSync?.stale && !disabled && projectId" class="stale-banner" data-testid="risk-stale-banner">
      <span>检测到已填业务信息或安全防护措施已更新，建议结合最新填报内容重新评估风险等级变化。</span>
      <el-button size="small" type="warning" plain :loading="diagnosing" data-testid="risk-stale-refresh" @click="diagnose">重新评估风险</el-button>
    </div>

    <p v-if="legacy" class="legacy" data-testid="risk-legacy">这是之前写的整段内容，已原样放在风险 1。请拆成每项风险一行，并为每项选可能性和严重程度、写应对措施。</p>

    <div v-for="(r, i) in rows" :key="r.id" class="row" :class="{ 'is-mitigated': r.mitigated === '1', 'is-card': !isEditing(r) }" :data-testid="`risk-row-${i + 1}`">
      <template v-if="!isEditing(r)">
        <div class="card-head">
          <span class="idx-pill">风险 {{ i + 1 }}</span>
          <span class="card-risk">{{ r.risk }}</span>
          <span v-if="r.mitigated === '1'" class="lv excluded" data-testid="risk-change">初评{{ initLevel(r) || level(r) || '待定级' }} → 已完成整改</span>
          <span v-else-if="initLevel(r) && initLevel(r) !== level(r)" class="lv" :class="isDown(r) ? 't0' : 't2'" data-testid="risk-change">初评{{ initLevel(r) }} → 当前{{ level(r) }}（{{ isDown(r) ? '已降级' : '已上调' }}）</span>
          <span v-else-if="level(r)" class="lv" :class="`t${NAMES.indexOf(level(r))}`" data-testid="risk-level">{{ level(r) }}</span>
          <span v-if="!disabled" class="card-tools">
            <button type="button" class="act-btn" :data-testid="`risk-edit-${i + 1}`" @click="editingId = r.id">调整</button>
            <button type="button" class="del" title="删除这项风险" data-testid="risk-del" @click="remove(i)">删除</button>
          </span>
        </div>
        <div class="card-measures"><b>防控与整改措施：</b>{{ r.measures }}</div>
        <div class="card-foot">
          <span class="meta-chip">当前定级：可能性{{ r.likelihood }} × 严重程度{{ r.severity }}</span>
          <span v-if="r.owner" class="meta-chip">责任方：{{ r.owner }}</span>
          <span v-if="r.due" class="meta-chip">时限：{{ r.due }}</span>
          <el-checkbox v-if="!disabled" :model-value="r.mitigated === '1'" class="mitigate-chk" data-testid="risk-mitigated" @change="(v: any) => { r.mitigated = v ? '1' : ''; save() }">已完成整改（归入整改跟踪报告）</el-checkbox>
          <span v-else-if="r.mitigated === '1'" class="meta-chip">已完成整改，归入整改跟踪报告</span>
        </div>
      </template>

      <template v-else>
        <div class="top">
          <span class="idx-pill">风险 {{ i + 1 }}</span>
          <el-input v-model="r.risk" type="textarea" :autosize="{ minRows: 1, maxRows: 4 }" maxlength="300" placeholder="风险内容，如：跨境诊断数据在传输途中被截获或接收方超范围留存" :disabled="disabled" data-testid="risk-text" @input="sync" @blur="save" />
          <button v-if="!gaps(r).length" type="button" class="act-btn" @click="editingId = ''">完成</button>
          <button v-if="!disabled" type="button" class="del" title="删除这项风险" data-testid="risk-del" @click="remove(i)">删除</button>
        </div>
        <div class="grade">
          <span class="pair"><span>可能性</span>
          <el-radio-group v-model="r.likelihood" size="small" :disabled="disabled" data-testid="risk-likelihood" @change="save">
            <el-radio-button v-for="l in LEVELS" :key="l" :value="l">{{ l }}</el-radio-button>
          </el-radio-group></span>
          <span class="pair"><span>严重程度</span>
          <el-radio-group v-model="r.severity" size="small" :disabled="disabled" data-testid="risk-severity" @change="save">
            <el-radio-button v-for="l in LEVELS" :key="l" :value="l">{{ l }}</el-radio-button>
          </el-radio-group></span>
          <span v-if="r.mitigated === '1'" class="lv excluded" data-testid="risk-change">初评{{ initLevel(r) || level(r) || '待定级' }} → 已完成整改</span>
          <template v-else-if="level(r)">
            <span v-if="initLevel(r) && initLevel(r) !== level(r)" class="lv" :class="isDown(r) ? 't0' : 't2'" data-testid="risk-change">初评{{ initLevel(r) }} → 当前{{ level(r) }}（{{ isDown(r) ? '已降级' : '已上调' }}）</span>
            <span v-else class="lv" :class="`t${NAMES.indexOf(level(r))}`" data-testid="risk-level">{{ level(r) }}</span>
          </template>
        </div>
        <el-input v-model="r.measures" type="textarea" :autosize="{ minRows: 1, maxRows: 5 }" maxlength="500" placeholder="应对与整改措施（至少一项），如：车端模糊隐去、双向证书加密传输、合同明确禁止再转移" :disabled="disabled" data-testid="risk-measures" @input="sync" @blur="save" />
        <div class="who">
          <span class="pair"><span>责任方</span>
          <el-input v-model="r.owner" size="small" maxlength="60" placeholder="选填，如车联网安全部" style="width: 168px" :disabled="disabled" data-testid="risk-owner" @input="sync" @blur="save" /></span>
          <span class="pair"><span>完成时限</span>
          <el-date-picker v-model="r.due" type="date" size="small" value-format="YYYY-MM-DD" placeholder="选填" style="width: 140px" :disabled="disabled" data-testid="risk-due" @change="save" /></span>
          <el-checkbox :model-value="r.mitigated === '1'" :disabled="disabled" data-testid="risk-mitigated" @change="(v: any) => { r.mitigated = v ? '1' : ''; save() }">已完成整改（归入整改跟踪报告）</el-checkbox>
          <span v-if="(r.risk || r.likelihood || r.severity || r.measures) && gaps(r).length" class="lack" data-testid="risk-lack">还缺：{{ gaps(r).join('、') }}</span>
        </div>
      </template>
    </div>

    <div v-if="!disabled" class="add-row">
      <el-button size="small" data-testid="risk-add" @click="add">＋ 添加一项风险</el-button>
    </div>

    <div v-if="filled.length" class="matrix" data-testid="risk-matrix">
      <div class="m-title">风险等级分布</div>
      <table>
        <thead><tr><th class="corner">可能性 \ 严重程度</th><th>严重程度：低</th><th>严重程度：中</th><th>严重程度：高</th></tr></thead>
        <tbody>
          <tr v-for="g in grid" :key="g.label">
            <th>{{ g.label }}</th>
            <td v-for="(c, j) in g.cells" :key="j" :class="`t${c.tone}`">
              <small class="cell-grade">{{ c.name }}</small>
              <div v-if="c.activeLabels.length || c.movedFrom.length" class="cell-tags">
                <span v-for="a in c.activeLabels" :key="a" class="cell-pill active">{{ a }}</span>
                <span v-for="m in c.movedFrom" :key="m" class="cell-pill moved">{{ m }}</span>
              </div>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>

<style scoped>
.risks { display: flex; flex-direction: column; gap: 10px; }
.evo-bar { display: flex; flex-wrap: wrap; justify-content: space-between; align-items: center; gap: 8px 14px; padding: 10px 14px; border: 1px solid #cbd5e1; border-radius: 8px; background: #f8fafc; font-size: 13px; color: #334155; }
.evo-main { display: flex; flex-wrap: wrap; align-items: center; gap: 6px 8px; flex: 1 1 360px; }
.evo-empty { color: #475569; }
.evo-arrow { color: #64748b; font-weight: 600; }
.evo-actions { display: inline-flex; align-items: center; gap: 10px; flex-shrink: 0; }
.evo-reset { border: none; background: none; color: #2563eb; cursor: pointer; font-size: 13px; padding: 0; white-space: nowrap; }
.evo-reset:hover { text-decoration: underline; }
.stale-banner { display: flex; flex-wrap: wrap; justify-content: space-between; align-items: center; gap: 8px 12px; padding: 8px 12px; border: 1px solid #fcd34d; border-radius: 8px; background: #fffbeb; color: #92400e; font-size: 13px; }
.legacy { margin: 0; padding: 6px 10px; border-radius: 6px; background: #fef3c7; color: #92400e; font-size: 13px; }
.row { border: 1px solid #e2e8f0; border-radius: 8px; padding: 10px 12px; display: flex; flex-direction: column; gap: 6px; background: #fff; transition: border-color .15s; }
.row.is-card:hover { border-color: #cbd5e1; }
.row.is-mitigated { background: #f8fafc; border-style: dashed; }
.idx-pill { display: inline-flex; align-items: center; padding: 1px 8px; border-radius: 6px; background: #eff6ff; color: #1d4ed8; font-size: 13px; font-weight: 600; white-space: nowrap; flex-shrink: 0; }
.row.is-mitigated .idx-pill { background: #e2e8f0; color: #475569; }
.card-head { display: flex; align-items: flex-start; gap: 8px; flex-wrap: wrap; }
.card-risk { flex: 1 1 260px; font-size: 14px; font-weight: 600; color: #0f172a; line-height: 1.55; }
.row.is-mitigated .card-risk { color: #475569; font-weight: 400; }
.card-tools { display: inline-flex; align-items: center; gap: 8px; margin-left: auto; flex-shrink: 0; }
.act-btn { border: none; background: none; color: #2563eb; cursor: pointer; font-size: 13px; padding: 0; white-space: nowrap; }
.act-btn:hover { text-decoration: underline; }
.card-measures { font-size: 13px; color: #334155; line-height: 1.6; padding-left: 2px; }
.card-measures b { color: #475569; font-weight: 600; }
.card-foot { display: flex; flex-wrap: wrap; align-items: center; gap: 6px 12px; font-size: 13px; color: #64748b; padding-top: 2px; }
.meta-chip { background: #f1f5f9; color: #475569; padding: 1px 8px; border-radius: 4px; white-space: nowrap; }
.mitigate-chk { margin-left: auto; }
.top { display: flex; gap: 8px; align-items: flex-start; }
.del { border: none; background: none; color: #64748b; cursor: pointer; font-size: 13px; padding: 0; white-space: nowrap; }
.del:hover { color: #dc2626; }
.grade, .who { display: flex; flex-wrap: wrap; gap: 6px 10px; align-items: center; font-size: 13px; color: #475569; }
.pair { display: inline-flex; gap: 6px; align-items: center; white-space: nowrap; }
.lv { padding: 1px 8px; border-radius: 999px; font-size: 13px; font-weight: 600; white-space: nowrap; flex-shrink: 0; }
.t0 { background: #dcfce7; color: #166534; }
.t1 { background: #fef3c7; color: #92400e; }
.t2 { background: #fee2e2; color: #991b1b; }
.lv.excluded { background: #e2e8f0; color: #334155; }
.lack { color: #b45309; }
.add-row { display: flex; align-items: center; gap: 8px; }
.matrix { margin-top: 4px; font-size: 13px; background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 8px; padding: 10px 12px; }
.m-title { font-weight: 600; color: #1e293b; margin-bottom: 8px; font-size: 13px; }
.matrix table { border-collapse: separate; border-spacing: 4px; width: 100%; max-width: 680px; table-layout: fixed; }
.matrix th { font-weight: 600; color: #475569; text-align: left; font-size: 13px; padding: 4px 6px; white-space: nowrap; }
.matrix th.corner { width: 118px; color: #64748b; font-weight: 400; }
.matrix td { border-radius: 6px; padding: 6px 8px; vertical-align: top; min-height: 44px; }
.cell-grade { display: block; font-size: 13px; opacity: 0.78; margin-bottom: 3px; }
.cell-tags { display: flex; flex-wrap: wrap; gap: 4px; }
.cell-pill { display: inline-block; padding: 1px 6px; border-radius: 4px; font-size: 13px; line-height: 1.45; }
.cell-pill.active { background: rgba(255, 255, 255, 0.88); color: #0f172a; font-weight: 600; border: 1px solid rgba(15, 23, 42, 0.12); }
.cell-pill.moved { background: rgba(255, 255, 255, 0.52); color: #64748b; border: 1px dashed rgba(100, 116, 139, 0.45); font-size: 13px; }
</style>
