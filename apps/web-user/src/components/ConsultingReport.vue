<script setup lang="ts">
import { localTime } from '@cyshuhe/shared'
import { computed } from 'vue'
import {
  PHASE_LABELS,
  ROADMAP_DIMENSION,
  type Action,
  type Assessment,
  type Consulting,
  type ConsultingDimension,
  type Reference
} from '../consulting'
import RichText from './RichText.vue'

const props = defineProps<{
  consulting: Consulting
  /** 用户在线修订过的维度文字（key 为内部编号，仅用于取值，不展示） */
  dimensionTexts: Record<string, string>
  executiveSummary: string
  generatedAt?: string | null
  exportDisabled?: boolean
  canExport?: boolean
}>()
const emit = defineEmits<{
  (e: 'open-citation', clauseId: string | null, quote: string): void
  (e: 'export-dimension', code: string, ext: 'docx' | 'pdf'): void
}>()

const CATEGORY: Record<string, string> = {
  vehicle: '车端',
  data_architecture: '数据架构',
  cloud: '云端',
  organization: '组织与流程'
}
const STATUS: Record<string, string> = {
  compliant: '已达到',
  partial: '部分达到',
  non_compliant: '未达到',
  insufficient_info: '需补充信息'
}
const LEVEL: Record<string, string> = { high: '高', medium: '中', low: '低', unknown: '待评估' }

const refMap = computed(() => new Map(props.consulting.references.map((r) => [r.n, r])))
const actionById = computed(() => new Map(props.consulting.roadmap.map((a) => [a.id, a])))
const mustDo = computed(() =>
  props.consulting.verdict.must_do_action_ids
    .map((id) => actionById.value.get(id))
    .filter((a): a is Action => !!a)
)
const riskRefs = computed(() => {
  const m = new Map<string, number[]>()
  for (const d of props.consulting.dimensions)
    for (const a of d.assessments) m.set(a.id, a.ref_numbers)
  return m
})
const edited = computed(() => props.consulting.edited ?? { dimensions: [], executive_summary: false })
const verdictTone = computed(() => {
  const l = props.consulting.verdict.label
  return l === '暂不建议' ? 'bad' : l === '可行' ? 'good' : l === '信息不足' ? 'unknown' : 'warn'
})
const displayGeneratedAt = computed(() =>
  props.generatedAt || localTime(props.consulting.generated_at || props.consulting.created_at || null) || ''
)
function cleanReportText(s?: string | null): string {
  return (s || '')
    .replace(/[（(]\s*(?:对话(?:编号|流水号)[:：]\s*)?CONV-[A-Za-z0-9_-]+\s*[)）]/g, '')
    .replace(/\bCONV-[A-Za-z0-9_-]+\b/g, '')
    .replace(/[（(]第\s*\d+\s*版[)）]/g, '')
}

function refsOf(action: Action): number[] {
  return [...new Set(action.addresses.flatMap((id) => riskRefs.value.get(id) ?? []))].sort(
    (a, b) => a - b
  )
}
// 参考文献条目只放一句话说明；完整的条款原文和中文对照点查看原文看
function refPoint(r: Reference): string {
  if (r.summary) return r.summary
  const first = (r.points ?? [])[0] ?? ''
  return first.length > 90 ? first.slice(0, 90) + '…' : first
}
function openRef(n: number) {
  const r = refMap.value.get(n)
  if (r) emit('open-citation', r.clause_id, r.quote_original)
}

// 七个方面的短文；没有已核验依据的方面不显示；第八个方面单独做成路线图
const analysed = computed(() =>
  props.consulting.dimensions.filter((d) => d.code !== ROADMAP_DIMENSION && d.status !== 'no_evidence')
)
const roadmapDim = computed(() => props.consulting.dimensions.find((d) => d.code === ROADMAP_DIMENSION))
const roadmapIntro = computed(() => {
  const d = roadmapDim.value
  return d && d.status === 'ok' ? d.conclusion : (props.consulting.roadmap_intro ?? '')
})
const phases = computed(() =>
  [1, 2, 3]
    .map((p) => ({ phase: p, label: PHASE_LABELS[p], items: props.consulting.roadmap.filter((a) => a.phase === p) }))
    .filter((p) => p.items.length)
)
function before(a: Action): string {
  return (a.depends_on ?? []).map((id) => actionById.value.get(id)?.title).filter(Boolean).join('、') || '无前置项'
}

const findings = (d: ConsultingDimension) => d.assessments.filter((a) => a.status !== 'insufficient_info')
const asks = (d: ConsultingDimension) => d.assessments.filter((a) => a.status === 'insufficient_info')
const todo = (a: Assessment) => props.consulting.roadmap.filter((x) => x.addresses.includes(a.id))

// 附录：逐项差距登记表（供逐条核对）
const register = computed(() =>
  props.consulting.dimensions.flatMap((d) =>
    d.assessments.map((a) => ({
      area: d.title,
      requirement: a.requirement,
      refs: a.ref_numbers,
      state: a.status === 'insufficient_info' ? '待补充确认' : a.business_state,
      status: STATUS[a.status],
      level: a.level,
      reason: a.status === 'insufficient_info' ? `需补充确认：${a.needs_fact}` : a.rationale
    }))
  )
)
</script>

<template>
  <div class="cr" data-testid="consulting-report">
    <!-- 第一层：先回答能不能做、卡在哪 -->
    <section class="verdict" :class="verdictTone" data-testid="consulting-verdict">
      <div class="verdict-head">
        <div class="verdict-label">{{ consulting.verdict.label }}</div>
        <small v-if="displayGeneratedAt" class="verdict-time" data-testid="consulting-generated-at">生成时间：{{ displayGeneratedAt }}</small>
      </div>
      <div class="verdict-headline"><RichText :text="cleanReportText(consulting.verdict.headline)" /></div>
      <p v-if="edited.executive_summary" class="verdict-summary pre"><RichText :text="cleanReportText(executiveSummary)" /></p>
      <p v-else class="verdict-summary"><RichText :text="cleanReportText(consulting.verdict.summary)" /></p>
    </section>

    <el-alert
      v-if="consulting.status === 'partial'"
      type="warning"
      :closable="false"
      show-icon
      :title="consulting.failure_reason"
      class="cr-gap"
    />

    <section
      v-if="consulting.conclusive_matrix && (analysed.length || roadmapDim)"
      class="block conclusive-box"
      data-testid="consulting-conclusive-matrix"
    >
      <div class="conclusive-head">
        <h3>结论性研判与放行条件核定</h3>
        <div class="conclusive-counts">
          <span class="chip high">高风险 {{ consulting.conclusive_matrix.counts.high }} 项</span>
          <span class="chip medium">中风险 {{ consulting.conclusive_matrix.counts.medium }} 项</span>
          <span class="chip low">低风险／已达标 {{ consulting.conclusive_matrix.counts.low }} 项</span>
          <span v-if="consulting.conclusive_matrix.counts.unknown" class="chip">
            待补充确认 {{ consulting.conclusive_matrix.counts.unknown }} 项
          </span>
        </div>
      </div>
      <p class="conclusive-rule">{{ consulting.conclusive_matrix.closure_rule }}</p>
      <el-table :data="consulting.conclusive_matrix.tiers" size="small" border class="tbl">
        <el-table-column prop="option" label="裁断决定类别" width="220" />
        <el-table-column prop="mark" label="核定结果" width="175" />
        <el-table-column prop="basis" label="合规研判依据与刚性执行要求" min-width="320" />
      </el-table>
    </section>

    <section v-if="mustDo.length" class="block">
      <h3>优先落地事项</h3>
      <ol class="must">
        <li v-for="a in mustDo" :key="a.id">
          <div class="act">
            <RichText :text="a.action" />
            <button v-for="n in refsOf(a)" :key="n" class="ref" type="button" @click="openRef(n)">[{{ n }}]</button>
          </div>
          <div class="meta">
            {{ CATEGORY[a.category] ?? a.category }} · {{ a.owner_role }} · {{ a.due_in_days }} 天内 ·
            完成标准：<RichText :text="a.done_criteria" />
          </div>
        </li>
      </ol>
    </section>

    <section v-if="consulting.top_risks.length" class="block">
      <h3>风险排序</h3>
      <ol class="risks">
        <li v-for="t in consulting.top_risks" :key="t.assessment_id">
          <span class="chip" :class="t.level">{{ LEVEL[t.level] }}</span>
          <RichText :text="t.title" />
          <button v-for="n in t.ref_numbers" :key="n" class="ref" type="button" @click="openRef(n)">[{{ n }}]</button>
        </li>
      </ol>
    </section>

    <!-- 第二层：各方面的短文 -->
    <section
      v-for="d in analysed"
      :key="d.code"
      class="block dim"
      :data-testid="`consulting-dim-${d.code}`"
    >
      <div class="dim-head">
        <h3>{{ d.title }}</h3>
        <span class="dim-actions">
          <el-tooltip :disabled="canExport !== false" content="当前只读账号未开通导出权限" placement="top">
            <span><el-button link size="small" :disabled="canExport === false || exportDisabled" @click="canExport !== false && emit('export-dimension', d.code, 'docx')">导出本节 Word</el-button></span>
          </el-tooltip>
          <el-tooltip :disabled="canExport !== false" content="当前只读账号未开通导出权限" placement="top">
            <span><el-button link size="small" :disabled="canExport === false || exportDisabled" @click="canExport !== false && emit('export-dimension', d.code, 'pdf')">导出本节 PDF</el-button></span>
          </el-tooltip>
        </span>
      </div>

      <p v-if="edited.dimensions.includes(d.code)" class="pre"><RichText :text="dimensionTexts[d.code]" /></p>
      <template v-else>
        <p v-if="d.status === 'failed'" class="muted">本节分析暂未完成，可稍后重新评估。</p>
        <template v-else>
          <p class="conclusion"><RichText :text="d.conclusion" /></p>
          <p v-if="d.overview" class="overview"><RichText :text="d.overview" /></p>

          <article v-for="a in findings(d)" :key="a.id" class="finding" :class="a.level">
            <div class="ftitle">
              <RichText :text="a.requirement" />
              <button v-for="n in a.ref_numbers" :key="n" class="ref" type="button" @click="openRef(n)">[{{ n }}]</button>
              <span class="chip" :class="a.level">{{ STATUS[a.status] }} · 风险{{ LEVEL[a.level] }}</span>
            </div>
            <p v-if="a.business_state" class="kv"><b>当前现状：</b><RichText :text="a.business_state" /></p>
            <p v-if="a.rationale" class="kv">
              <b>{{ a.status === 'compliant' ? '判断：' : '差距与原因：' }}</b><RichText :text="a.rationale" />
            </p>
            <p v-if="todo(a).length" class="kv">
              <b>建议：</b>{{ todo(a).map((x) => `${x.title}（${x.owner_role}，${x.due_in_days} 天内）`).join('；') }}
            </p>
          </article>

          <div v-if="asks(d).length" class="asks">
            <b>待补充核实事项</b>
            <ul>
              <li v-for="a in asks(d)" :key="a.id">
                <RichText :text="a.needs_fact" />
                <span class="muted">（<RichText :text="a.requirement" /></span>
                <button v-for="n in a.ref_numbers" :key="n" class="ref" type="button" @click="openRef(n)">[{{ n }}]</button>
                <span class="muted">）</span>
              </li>
            </ul>
          </div>
        </template>
      </template>
    </section>

    <!-- 没有依据的方面不显示；一个方面都写不出来时才用一句话说明 -->
    <section v-if="!analysed.length && !roadmapDim" class="block" data-testid="consulting-no-evidence">
      <p class="muted">目前法规库里还没有与这个场景直接对应的依据，补充相关法规后可重新评估。</p>
    </section>

    <!-- 第八个方面本身就是整改路线图：三个阶段，标出先后依赖 -->
    <section v-if="roadmapDim" class="block" data-testid="consulting-roadmap">
      <div class="dim-head">
        <h3>{{ roadmapDim.title }}</h3>
        <span class="dim-actions">
          <el-tooltip :disabled="canExport !== false" content="当前只读账号未开通导出权限" placement="top">
            <span><el-button link size="small" :disabled="canExport === false || exportDisabled" @click="canExport !== false && emit('export-dimension', roadmapDim.code, 'docx')">导出本节 Word</el-button></span>
          </el-tooltip>
          <el-tooltip :disabled="canExport !== false" content="当前只读账号未开通导出权限" placement="top">
            <span><el-button link size="small" :disabled="canExport === false || exportDisabled" @click="canExport !== false && emit('export-dimension', roadmapDim.code, 'pdf')">导出本节 PDF</el-button></span>
          </el-tooltip>
        </span>
      </div>
      <p v-if="edited.dimensions.includes(roadmapDim.code)" class="pre">
        <RichText :text="dimensionTexts[roadmapDim.code]" />
      </p>
      <template v-else>
        <p v-if="roadmapIntro" class="conclusion"><RichText :text="roadmapIntro" /></p>
        <div v-for="p in phases" :key="p.phase" class="phase">
          <h4>{{ p.label }}</h4>
          <el-table :data="p.items" size="small" border class="tbl">
            <el-table-column label="整改事项" min-width="260">
              <template #default="{ row }">
                【{{ CATEGORY[row.category] ?? row.category }}】<b>{{ row.title }}</b>：<RichText :text="row.action" />
                <button v-for="n in refsOf(row)" :key="n" class="ref" type="button" @click="openRef(n)">[{{ n }}]</button>
              </template>
            </el-table-column>
            <el-table-column prop="owner_role" label="责任岗位" width="120" />
            <el-table-column label="时限" width="84"><template #default="{ row }">{{ row.due_in_days }} 天内</template></el-table-column>
            <el-table-column label="前置依赖" width="150"><template #default="{ row }">{{ before(row) }}</template></el-table-column>
            <el-table-column label="完成标准" min-width="190">
              <template #default="{ row }"><RichText :text="row.done_criteria" /></template>
            </el-table-column>
          </el-table>
        </div>
      </template>
    </section>

    <!-- 第三层：附录 -->
    <el-collapse v-if="register.length" class="appendix" data-testid="consulting-register">
      <el-collapse-item title="附录：合规差距登记表" name="register">
        <el-table :data="register" size="small" border class="tbl">
          <el-table-column prop="area" label="方面" width="130" />
          <el-table-column label="法律要求" min-width="170">
            <template #default="{ row }">
              <RichText :text="row.requirement" />
              <button v-for="n in row.refs" :key="n" class="ref" type="button" @click="openRef(n)">[{{ n }}]</button>
            </template>
          </el-table-column>
          <el-table-column label="企业现状" min-width="150">
            <template #default="{ row }"><RichText :text="row.state" /></template>
          </el-table-column>
          <el-table-column prop="status" label="达到情况" width="92" />
          <el-table-column label="风险" width="70">
            <template #default="{ row }"><span class="chip" :class="row.level">{{ LEVEL[row.level] }}</span></template>
          </el-table-column>
          <el-table-column label="判断理由" min-width="190">
            <template #default="{ row }"><RichText :text="row.reason" /></template>
          </el-table-column>
        </el-table>
      </el-collapse-item>
    </el-collapse>

    <section class="block refs" data-testid="consulting-references">
      <h3>参考文献</h3>
      <p class="muted">点击正文里的角标或这里的查看原文，可以看到条款原文、中文对照和术语释义。</p>
      <ol>
        <li v-for="r in consulting.references" :id="`cref-${r.n}`" :key="r.n">
          <div>
            <b>[{{ r.n }}]</b> {{ r.document_title }} {{ r.clause_number }}
            <el-button link type="primary" size="small" @click="openRef(r.n)">查看原文</el-button>
          </div>
          <div v-if="refPoint(r)" class="meta">要点：{{ refPoint(r) }}</div>
          <div class="meta">
            <a v-if="r.source_url" :href="r.source_url" target="_blank" rel="noopener noreferrer">官方来源</a>
            <span v-else>来源未提供</span>
          </div>
        </li>
      </ol>
    </section>
  </div>
</template>

<style scoped>
.cr { color: #1f2d3d; line-height: 1.8 }
.cr small { font-size: 13px }
.verdict { padding: 16px 20px; border-radius: 6px; border-left: 4px solid #909399; background: #f4f6f9; margin-bottom: 14px }
.verdict.warn { border-left-color: #e6a23c; background: #fdf6ec }
.verdict.bad { border-left-color: #f56c6c; background: #fef0f0 }
.verdict.good { border-left-color: #67c23a; background: #f0f9eb }
.verdict-head { display: flex; justify-content: space-between; align-items: baseline; gap: 12px; flex-wrap: wrap }
.verdict-label { font-size: 13px; font-weight: 600; color: #606266 }
.verdict-time { font-size: 13px; color: #64748b }
.verdict-headline { font-size: 18px; font-weight: 600; line-height: 1.5; margin: 2px 0 6px }
.verdict-summary { margin: 0 }
.pre { white-space: pre-wrap }
.plain { margin: 4px 0 0; padding-left: 20px }
.cr-gap { margin-bottom: 12px }
.conclusive-box { padding: 14px 16px; border: 1px solid #e2e8f0; border-radius: 6px; background: #fcfdff }
.conclusive-head { display: flex; justify-content: space-between; align-items: center; gap: 12px; flex-wrap: wrap; margin-bottom: 6px }
.conclusive-head h3 { margin: 0 }
.conclusive-counts { display: flex; flex-wrap: wrap; gap: 6px }
.conclusive-rule { margin: 4px 0 8px; font-size: 13px; color: #475569; line-height: 1.7 }
.block { margin-top: 22px }
.block h3 { font-size: 16px; margin: 0 0 8px }
.must, .risks { margin: 0; padding-left: 22px }
.must li, .risks li { padding: 6px 0 }
.act { font-weight: 600 }
.meta { font-size: 13px; color: #606266 }
.muted { color: #909399; font-size: 13px }
.chip { display: inline-block; min-width: 34px; text-align: center; padding: 0 8px; margin-left: 6px; border-radius: 10px; font-size: 13px; background: #e9ecf1; color: #606266; font-weight: 400 }
.chip.high { background: #fde2e2; color: #c0392b }
.chip.medium { background: #faecd8; color: #a8680b }
.chip.low { background: #e1f3d8; color: #3d7f1f }
.dim-head { display: flex; justify-content: space-between; align-items: baseline; gap: 12px; flex-wrap: wrap }
.conclusion { font-size: 15px; margin: 4px 0 10px }
.overview { margin: 0 0 10px; color: #374151 }
.finding { margin: 10px 0; padding: 6px 0 6px 12px; border-left: 3px solid #dce4fc }
.finding.high { border-left-color: #f1a5a0 }
.finding.medium { border-left-color: #f0cf9a }
.ftitle { font-weight: 600 }
.kv { margin: 2px 0; font-size: 14px }
.kv b { color: #374151 }
.tbl { margin: 8px 0 }
.phase { margin-top: 12px }
.phase h4 { margin: 0 0 4px; font-size: 14px; color: #244bc3 }
.asks { margin-top: 10px }
.asks ul { margin: 4px 0 0; padding-left: 20px }
.appendix { margin-top: 22px }
.ref { border: 0; background: none; color: var(--el-color-primary, #409eff); cursor: pointer; padding: 0 1px; font-weight: 600; font-size: 13px; vertical-align: super; line-height: 1 }
.ref:hover, .ref:focus-visible { background: #ecf5ff; outline: none }
.refs ol { list-style: none; padding: 0; margin: 0 }
.refs li { padding: 8px 0; border-bottom: 1px solid #ebeef5 }
</style>
