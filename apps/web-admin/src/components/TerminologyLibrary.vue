<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { api } from '../api'
import { useAuth } from '../auth'
interface Definition {
  id: string
  source_term: string
  target_zh: string
  source_label: string
  source_kind: string
  source_reference: string
  source_url: string | null
  definition_original: string | null
  definition_zh: string | null
  verification_status: string
}
interface Ambiguity {
  id: string
  country_code: string
  title: string
  question: string
  impact: string
  status: string
}
interface DocOption {
  id: string
  title: string
  latest_version?: { id: string; state: string } | null
}
interface ClauseOption {
  id: string
  ordinal: number
  clause_number: string | null
  heading: string | null
  original_text: string
}
const props = defineProps<{ country: string }>()
const registerVisible = ref(false),
  saving = ref(false),
  docsLoading = ref(false),
  clausesLoading = ref(false)
const docOptions = ref<DocOption[]>([]),
  clauseOptions = ref<ClauseOption[]>([])
const registration = reactive({
  document_id: '',
  clause_id: '',
  source_term: '',
  target_zh: '',
  quote: '',
  source_kind: 'law_definition',
  applies_to_document_id: '',
})
async function openRegisterDialog() {
  if (!props.country) {
    ElMessage.warning('请先在页面顶部选择国家后再补充来源定义')
    return
  }
  registration.document_id = ''
  registration.clause_id = ''
  registration.source_term = ''
  registration.target_zh = ''
  registration.quote = ''
  registration.source_kind = 'law_definition'
  registration.applies_to_document_id = ''
  clauseOptions.value = []
  registerVisible.value = true
  docsLoading.value = true
  try {
    const list = await api.get<DocOption[]>(
      `/admin/documents?country_code=${encodeURIComponent(props.country)}&limit=200`,
    )
    docOptions.value = list.filter((d) => d.latest_version?.state === 'parsed')
  } catch {
    docOptions.value = []
  } finally {
    docsLoading.value = false
  }
}
async function onPickDocument(docId: string) {
  registration.clause_id = ''
  clauseOptions.value = []
  const picked = docOptions.value.find((d) => d.id === docId)
  if (!picked?.latest_version?.id) return
  clausesLoading.value = true
  try {
    clauseOptions.value = await api.get<ClauseOption[]>(
      `/admin/versions/${picked.latest_version.id}/clauses?limit=500`,
    )
  } catch {
    clauseOptions.value = []
  } finally {
    clausesLoading.value = false
  }
}
function onPickClause(clauseId: string) {
  const picked = clauseOptions.value.find((c) => c.id === clauseId)
  if (picked && !registration.quote.trim()) {
    registration.quote = picked.original_text.slice(0, 600)
  }
}
async function register() {
  if (!props.country && registration.source_kind !== 'international_reference') {
    ElMessage.warning('请先在页面顶部选择适用国家')
    return
  }
  if (!registration.clause_id && !registration.document_id) {
    ElMessage.warning('请先选择来源法规与具体条款')
    return
  }
  saving.value = true
  try {
    await api.post('/admin/knowledge/terminology/references', {
      document_id: registration.document_id || null,
      clause_id: registration.clause_id || null,
      source_term: registration.source_term,
      target_zh: registration.target_zh,
      quote: registration.quote,
      source_kind: registration.source_kind,
      country_code: registration.source_kind === 'international_reference' ? null : props.country,
      applies_to_document_id: registration.applies_to_document_id || null,
    })
    ElMessage.success('已登记来源定义与译名')
    registerVisible.value = false
    await load()
  } catch (e) {
    ElMessage.error((e as Error).message)
  } finally {
    saving.value = false
  }
}
const items = ref<Definition[]>([]),
  points = ref<Ambiguity[]>([]),
  busy = ref(false),
  search = ref(''),
  page = ref(1)
const visible = computed(() =>
  items.value.filter((t) =>
    `${t.source_term} ${t.target_zh} ${t.source_reference}`
      .toLowerCase()
      .includes(search.value.toLowerCase()),
  ),
)
async function load() {
  if (!props.country) {
    items.value = []
    points.value = []
    return
  }
  busy.value = true
  try {
    const [d, p] = await Promise.all([
      api.get<{ items: Definition[] }>(
        `/admin/knowledge/terminology?country_code=${encodeURIComponent(props.country)}`,
      ),
      api.get<Ambiguity[]>('/admin/knowledge/terminology/ambiguities'),
    ])
    items.value = d.items
    points.value = p.filter((x) => !props.country || x.country_code === props.country)
  } catch (e) {
    ElMessage.error((e as Error).message)
  } finally {
    busy.value = false
  }
}
async function rebuild() {
  busy.value = true
  try {
    const r = await api.post<{ definitions_added: number }>(
      '/admin/knowledge/terminology/rebuild',
      { country_code: props.country || null },
    )
    ElMessage.success(`新增 ${r.definitions_added} 条来源定义`)
    await load()
  } catch (e) {
    ElMessage.error((e as Error).message)
  } finally {
    busy.value = false
  }
}
watch(
  () => props.country,
  () => {
    page.value = 1
    void load()
  },
  { immediate: true },
)
</script>
<template>
  <section v-loading="busy" class="terminology-library">
    <header>
      <div>
        <h3>来源定义库</h3>
        <p>每个术语的中文译名与出处。出处按可信程度排列：法律自身的定义、监管机构的说明、官方语种对照、国际通行用法。</p>
      </div>
      <div>
        <el-button :disabled="!props.country" @click="openRegisterDialog">补充权威定义</el-button
        ><el-button
          v-if="['platform_admin', 'ops_admin'].includes(useAuth().user?.role ?? '')"
          :disabled="!props.country"
          @click="rebuild"
          >更新来源定义</el-button
        >
      </div>
    </header>
    <el-input
      v-model="search"
      placeholder="搜索术语、译名或依据"
      clearable
      style="max-width: 460px; margin: 12px 0 20px"
      @input="page = 1"
    />
    <el-table :data="visible.slice((page - 1) * 20, page * 20)" border>
      <el-table-column label="术语与译名" min-width="200"
        ><template #default="{ row }"
          ><strong>{{ row.target_zh }}</strong>
          <div dir="auto">{{ row.source_term }}</div></template
        ></el-table-column
      >
      <el-table-column label="来源解释" min-width="300"
        ><template #default="{ row }"
          ><p v-if="row.definition_zh">{{ row.definition_zh }}</p>
          <details v-if="row.definition_original">
            <summary>来源定义原文</summary>
            <p dir="auto">{{ row.definition_original }}</p>
          </details>
          <span v-else>通用译名，不附加本国法律定义</span></template
        ></el-table-column
      >
      <el-table-column label="来源与适用范围" min-width="230"
        ><template #default="{ row }"
          ><el-tag size="small">{{ row.source_label }}</el-tag>
          <p>{{ row.source_reference }}</p>
          <a v-if="row.source_url" :href="row.source_url" target="_blank" rel="noopener noreferrer"
            >查看官方来源</a
          ></template
        ></el-table-column
      >
      <el-table-column label="自动检查" width="150"
        ><template #default="{ row }">{{
          row.verification_status === 'source_matched' ? '原文匹配' : '译名规范'
        }}</template></el-table-column
      >
    </el-table>
    <el-pagination
      v-if="visible.length > 20"
      v-model:current-page="page"
      :total="visible.length"
      :page-size="20"
      layout="prev, pager, next, total"
    />
    <h3>影响结论的术语歧义</h3>
    <el-empty v-if="!points.length" description="暂无单独登记的术语歧义" :image-size="60" />
    <el-table v-else :data="points" border
      ><el-table-column prop="title" label="术语" min-width="170" /><el-table-column
        prop="question"
        label="需要确认的问题"
        min-width="260"
      /><el-table-column prop="impact" label="对结论的影响" min-width="260" /><el-table-column
        label="状态"
        width="130"
        ><template #default="{ row }">{{
          row.status === 'confirmed' ? '已确认' : '待复核'
        }}</template></el-table-column
      ></el-table
    >
    <el-dialog v-model="registerVisible" title="登记来源定义与译名" width="min(650px, 94vw)" append-to-body>
      <el-form label-position="top">
        <el-form-item label="来源层级"
          ><el-select v-model="registration.source_kind"
            ><el-option label="法律自身定义" value="law_definition" /><el-option
              label="监管指南与问答"
              value="regulator_guidance" /><el-option
              label="官方其他语种版本"
              value="official_parallel" /><el-option
              label="国际参考"
              value="international_reference" /></el-select
        ></el-form-item>
        <el-form-item label="来源法规"
          ><el-select
            v-model="registration.document_id"
            filterable
            :loading="docsLoading"
            style="width: 100%"
            placeholder="选择已归档的官方法规文件"
            @change="onPickDocument"
          >
            <el-option v-for="d in docOptions" :key="d.id" :value="d.id" :label="d.title" />
          </el-select
        ></el-form-item>
        <el-form-item label="来源条款"
          ><el-select
            v-model="registration.clause_id"
            filterable
            :loading="clausesLoading"
            :disabled="!registration.document_id"
            style="width: 100%"
            placeholder="选择定义所在的条款"
            @change="onPickClause"
          >
            <el-option
              v-for="c in clauseOptions"
              :key="c.id"
              :value="c.id"
              :label="`${c.clause_number || `第 ${c.ordinal + 1} 段`} · ${(c.heading || c.original_text).replace(/\s+/g, ' ').slice(0, 42)}`"
            />
          </el-select
        ></el-form-item>
        <el-form-item label="原词"><el-input v-model="registration.source_term" placeholder="填写原文中的术语" /></el-form-item>
        <el-form-item label="规定中文译法"
          ><el-input v-model="registration.target_zh" placeholder="填写规范中文译法"
        /></el-form-item>
        <el-form-item label="完整定义原文"
          ><el-input
            v-model="registration.quote"
            type="textarea"
            :rows="5"
            placeholder="选择条款后自动带出原文，可截取该术语对应的定义原句"
        /></el-form-item>
        <el-form-item
          v-if="registration.source_kind === 'official_parallel'"
          label="适用的法规文件"
          ><el-select
            v-model="registration.applies_to_document_id"
            filterable
            clearable
            style="width: 100%"
            placeholder="选择该多语种译名适用的法规文件"
          >
            <el-option v-for="d in docOptions" :key="d.id" :value="d.id" :label="d.title" />
          </el-select
        ></el-form-item>
      </el-form>
      <template #footer
        ><el-button @click="registerVisible = false">取消</el-button
        ><el-button type="primary" :loading="saving" @click="register">登记</el-button></template
      >
    </el-dialog>
  </section>
</template>
<style scoped>
header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 20px;
  flex-wrap: wrap;
}
h3 {
  font-size: 16px;
  margin: 12px 0;
}
p {
  font-size: 13px;
  line-height: 1.8;
  color: #677b8e;
  margin: 8px 0;
}
details {
  font-size: 14px;
  line-height: 1.8;
}
summary {
  cursor: pointer;
}
.el-pagination {
  margin: 18px 0 28px;
  justify-content: center;
}
</style>
