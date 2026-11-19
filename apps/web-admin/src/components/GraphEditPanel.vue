<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api } from '../api'

interface EditNode {
  id: string
  country_code?: string
  node_type: string
  name_zh: string
  summary?: string | null
  evidence_clause_id?: string | null
  review_status: string
}
interface EditEdge {
  id: string
  source_node_id: string
  source_name_zh: string
  target_node_id: string
  target_name_zh: string
  relation_type: string
  evidence_clause_id?: string
  clause_number?: string | null
  document_title?: string | null
  evidence_quote: string
  review_status: string
}
interface ClauseInfo {
  id: string
  document_title: string
  clause_number: string | null
  original_text: string
}

const NODE_TYPE_ZH: Record<string, string> = {
  COUNTRY: '国家/地区',
  REGULATOR: '监管机构',
  INSTRUMENT: '法规文件',
  OBLIGATION: '合规义务',
  DATA_CATEGORY: '数据类型',
  PENALTY: '违规罚则',
}

const props = defineProps<{
  countryCode?: string
  node: EditNode
  nodes: EditNode[]
  edges: EditEdge[]
  relationLabels: Record<string, string>
}>()
const emit = defineEmits<{ changed: [] }>()

const RELATIONS = computed(() => Object.keys(props.relationLabels))
const name = ref('')
const summary = ref('')
const saving = ref(false)
const addOpen = ref(false)

const myEdges = computed(() =>
  props.edges.filter(
    (e) =>
      (e.source_node_id === props.node.id || e.target_node_id === props.node.id) &&
      !['superseded', 'stale'].includes(e.review_status),
  ),
)
const readOnly = computed(() => ['superseded', 'stale'].includes(props.node.review_status))

watch(
  () => props.node.id,
  () => {
    name.value = props.node.name_zh
    summary.value = props.node.summary || ''
    addOpen.value = false
  },
  { immediate: true },
)

function fail(e: unknown) {
  ElMessage.error((e as Error).message || '操作失败')
}

async function saveName() {
  saving.value = true
  try {
    await api.patch(`/admin/knowledge/nodes/${props.node.id}`, {
      name_zh: name.value,
      summary: summary.value,
    })
    ElMessage.success('已保存，该要素回到待复核')
    emit('changed')
  } catch (e) {
    fail(e)
  } finally {
    saving.value = false
  }
}

async function retireNode() {
  try {
    await ElMessageBox.confirm(
      '停用后该要素和挂在它上面的关系不再展示；历史记录保留，可恢复。',
      '停用要素',
      { type: 'warning', confirmButtonText: '停用', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  try {
    const r = await api.delete<{ retired_edges: number }>(`/admin/knowledge/nodes/${props.node.id}`)
    ElMessage.success(`已停用，同时停用 ${r.retired_edges} 条关系`)
    emit('changed')
  } catch (e) {
    fail(e)
  }
}

async function changeRelation(edge: EditEdge, relation: string) {
  try {
    await api.patch(`/admin/knowledge/edges/${edge.id}`, { relation_type: relation })
    ElMessage.success('关系类型已修改，回到待复核')
    emit('changed')
  } catch (e) {
    fail(e)
  }
}

async function retireEdge(edge: EditEdge) {
  try {
    await ElMessageBox.confirm('停用这条关系？历史记录保留，可恢复。', '停用关系', {
      type: 'warning',
      confirmButtonText: '停用',
      cancelButtonText: '取消',
    })
  } catch {
    return
  }
  try {
    await api.delete(`/admin/knowledge/edges/${edge.id}`)
    ElMessage.success('已停用')
    emit('changed')
  } catch (e) {
    fail(e)
  }
}

// ---- 新增关系：优先取两端要素的依据条款；若起止要素自身无绑定条款，支持按当前国家检索或选取在库条款
const targetId = ref('')
const relation = ref('GOVERNS')
const clauseId = ref('')
const quote = ref('')
const clauses = ref<ClauseInfo[]>([])
const clauseCache = ref<Record<string, ClauseInfo>>({})
const clauseLoading = ref(false)

const activeCountry = computed(() => props.countryCode || props.node.country_code || '')

const candidates = computed(() =>
  props.nodes.filter(
    (n) => n.id !== props.node.id && !['superseded', 'stale'].includes(n.review_status),
  ),
)
const chosenClause = computed(
  () => clauses.value.find((c) => c.id === clauseId.value) || clauseCache.value[clauseId.value],
)
const flat = (t: string) => t.split(/\s+/).filter(Boolean).join(' ')
const quoteOk = computed(
  () =>
    !!chosenClause.value &&
    flat(quote.value).length >= 8 &&
    flat(chosenClause.value.original_text).includes(flat(quote.value)),
)

function rememberClauses(list: ClauseInfo[]) {
  const next = { ...clauseCache.value }
  for (const c of list) {
    if (c?.id) next[c.id] = c
  }
  clauseCache.value = next
}

async function searchClauses(keyword = '') {
  const kw = keyword.trim().toLowerCase()
  clauseLoading.value = true
  try {
    const qs = new URLSearchParams()
    if (activeCountry.value) qs.set('country_code', activeCountry.value)
    if (keyword.trim()) qs.set('q', keyword.trim())
    qs.set('limit', '30')
    try {
      const res = await api.get<ClauseInfo[] | { items?: ClauseInfo[] }>(
        `/admin/knowledge/clauses?${qs.toString()}`,
      )
      const rawList = Array.isArray(res) ? res : res?.items || []
      if (rawList.length > 0) {
        const normalized = rawList.map((item) => ({
          id: item.id,
          document_title: item.document_title || '官方法规文本',
          clause_number: item.clause_number ?? null,
          original_text: item.original_text || '',
        }))
        rememberClauses(normalized)
        clauses.value = normalized
        if (!clauseId.value && normalized[0]) clauseId.value = normalized[0].id
        return
      }
    } catch {
      /* 若后端未提供列表检索路由，回退从当前图谱已有关系的条款中检索 */
    }

    const matchedIds: string[] = []
    const seen = new Set<string>()
    for (const e of props.edges) {
      if (!e.evidence_clause_id || seen.has(e.evidence_clause_id)) continue
      const hay = `${e.document_title || ''} ${e.clause_number || ''} ${e.evidence_quote || ''}`.toLowerCase()
      if (!kw || hay.includes(kw)) {
        seen.add(e.evidence_clause_id)
        matchedIds.push(e.evidence_clause_id)
        if (matchedIds.length >= 12) break
      }
    }
    const fetched: ClauseInfo[] = []
    for (const id of matchedIds) {
      if (clauseCache.value[id]) {
        fetched.push(clauseCache.value[id])
        continue
      }
      try {
        const c = await api.get<ClauseInfo>(`/admin/knowledge/clauses/${id}`)
        fetched.push(c)
      } catch {
        /* 忽略单条获取失败 */
      }
    }
    rememberClauses(fetched)
    clauses.value = fetched
    if (!clauseId.value && fetched[0]) clauseId.value = fetched[0].id
  } finally {
    clauseLoading.value = false
  }
}

async function loadClauses() {
  const ids = new Set<string>()
  if (props.node.evidence_clause_id) ids.add(props.node.evidence_clause_id)
  const other = props.nodes.find((n) => n.id === targetId.value)
  if (other?.evidence_clause_id) ids.add(other.evidence_clause_id)
  const direct: ClauseInfo[] = []
  for (const id of ids) {
    try {
      const c = clauseCache.value[id] || (await api.get<ClauseInfo>(`/admin/knowledge/clauses/${id}`))
      direct.push(c)
    } catch (e) {
      fail(e)
    }
  }
  if (direct.length > 0) {
    rememberClauses(direct)
    clauses.value = direct
    clauseId.value = direct[0]?.id || ''
  } else {
    await searchClauses('')
  }
}

watch(addOpen, (open) => {
  if (open && !clauses.value.length) {
    void loadClauses()
  }
})

async function addEdge() {
  try {
    await api.post('/admin/knowledge/edges', {
      source_node_id: props.node.id,
      target_node_id: targetId.value,
      relation_type: relation.value,
      evidence_clause_id: clauseId.value,
      evidence_quote: quote.value,
    })
    ElMessage.success('关系已新增，待复核')
    addOpen.value = false
    quote.value = ''
    targetId.value = ''
    emit('changed')
  } catch (e) {
    fail(e)
  }
}
</script>

<template>
  <section class="graph-edit" data-testid="kg-edit-panel">
    <h4>编辑</h4>
    <el-alert
      v-if="readOnly"
      type="info"
      :closable="false"
      title="该要素已停用，不能修改。"
      show-icon
    />
    <template v-else>
      <el-form label-position="top" @submit.prevent>
        <el-form-item label="名称">
          <el-input v-model="name" maxlength="255" />
        </el-form-item>
        <el-form-item label="说明">
          <el-input v-model="summary" type="textarea" :rows="2" maxlength="4000" />
        </el-form-item>
        <el-button :type="addOpen ? '' : 'primary'" :loading="saving" :disabled="!name.trim()" @click="saveName"
          >保存</el-button
        >
        <el-button type="danger" plain @click="retireNode">停用要素</el-button>
      </el-form>

      <h4>关系（{{ myEdges.length }}）</h4>
      <div v-for="e in myEdges" :key="e.id" class="edit-edge">
        <div class="edit-edge-head">
          <span>{{ e.source_name_zh }}</span>
          <el-select
            :model-value="e.relation_type"
            size="small"
            style="width: 130px"
            @change="(v: string) => changeRelation(e, v)"
          >
            <el-option v-for="r in RELATIONS" :key="r" :label="relationLabels[r]" :value="r" />
          </el-select>
          <span>{{ e.target_name_zh }}</span>
          <el-button size="small" link type="danger" @click="retireEdge(e)">停用</el-button>
        </div>
        <blockquote v-if="e.evidence_quote">{{ e.evidence_quote }}</blockquote>
      </div>
      <el-empty v-if="!myEdges.length" description="暂无关系" :image-size="48" />

      <el-button v-if="!addOpen" @click="addOpen = true">新增关系</el-button>
      <div v-else class="edit-add">
        <el-form label-position="top" @submit.prevent>
          <el-form-item label="指向的要素">
            <el-select
              v-model="targetId"
              filterable
              placeholder="选择要素"
              style="width: 100%"
              @change="loadClauses"
            >
              <el-option
                v-for="n in candidates"
                :key="n.id"
                :label="`${n.name_zh}（${NODE_TYPE_ZH[n.node_type] || n.node_type}）`"
                :value="n.id"
              />
            </el-select>
          </el-form-item>
          <el-form-item label="关系类型">
            <el-select v-model="relation" style="width: 100%">
              <el-option v-for="r in RELATIONS" :key="r" :label="relationLabels[r]" :value="r" />
            </el-select>
          </el-form-item>
          <el-form-item label="依据条款">
            <el-select
              v-model="clauseId"
              filterable
              remote
              reserve-keyword
              :remote-method="searchClauses"
              :loading="clauseLoading"
              style="width: 100%"
              placeholder="输入条号、法规名或关键词搜索依据条款"
            >
              <el-option
                v-for="c in clauses"
                :key="c.id"
                :label="`${c.document_title} ${c.clause_number ? '第 ' + c.clause_number + ' 条' : ''}`"
                :value="c.id"
              />
            </el-select>
          </el-form-item>
          <el-collapse v-if="chosenClause">
            <el-collapse-item title="查看条款原文">
              <pre class="edit-clause">{{ chosenClause.original_text }}</pre>
            </el-collapse-item>
          </el-collapse>
          <el-form-item label="依据原句（从条款原文中复制）">
            <el-input v-model="quote" type="textarea" :rows="3" />
            <div v-if="quote" :class="quoteOk ? 'quote-ok' : 'quote-bad'">
              {{ quoteOk ? '原句在条款原文中' : '原句不在所选条款的原文里（或少于 8 个字符）' }}
            </div>
          </el-form-item>
          <el-button type="primary" :disabled="!quoteOk || !targetId" @click="addEdge"
            >新增</el-button
          >
          <el-button @click="addOpen = false">取消</el-button>
        </el-form>
      </div>
    </template>
  </section>
</template>

<style scoped>
.graph-edit {
  margin-top: 18px;
  padding-top: 12px;
  border-top: 1px solid #e2e8f0;
}
.graph-edit h4 {
  margin: 10px 0 8px;
  font-size: 14px;
}
.edit-edge {
  margin-bottom: 10px;
  font-size: 13px;
}
.edit-edge-head {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.edit-edge blockquote {
  margin: 4px 0 0;
  padding-left: 8px;
  border-left: 2px solid #cbd5e1;
  color: #64748b;
  font-size: 13px;
  white-space: pre-wrap;
}
.edit-add {
  margin-top: 10px;
}
.edit-clause {
  max-height: 180px;
  overflow: auto;
  white-space: pre-wrap;
  font-size: 13px;
}
.quote-ok {
  color: #15803d;
  font-size: 13px;
}
.quote-bad {
  color: #b45309;
  font-size: 13px;
}
</style>
