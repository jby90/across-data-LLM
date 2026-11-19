<script setup lang="ts">
import { canEdit as _canEdit, canExport as _canExport } from '@cyshuhe/shared'
import { computed as _computed } from 'vue'
import { useAuth } from '../auth'
const _auth = useAuth()
const editable = _computed(() => _canEdit(_auth.user))
const downloadable = _computed(() => _canExport(_auth.user))
import { localTime } from '@cyshuhe/shared'
import { ElMessage } from 'element-plus'
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../api'
import { receiveExport } from '../chatFeedback'

const props = defineProps<{ taskId: string; disabled?: boolean }>()

interface Question {
  key: string
  label: string
  question: string
  short?: boolean
  why: string
}
interface Status {
  code: string
  name: string
  ready: boolean
  questions: Question[]
  optional_questions?: Question[]
  needs_paper?: boolean
  official_forms?: Array<{ code: string; name_zh: string; authority_zh: string }>
}

interface Block {
  type: string
  text?: string
  items?: string[]
  head?: string[]
  rows?: string[][]
}
interface EditLog {
  user_name: string
  place: string
  before: string
  after: string
  at: string | null
}
interface Saved {
  exists: boolean
  blocks: Block[]
  edit_count: number
  edits: EditLog[]
}
interface Where {
  block: number
  item?: number
  row?: number
  col?: number
}

const router = useRouter()
const open = ref(false)
const saved = ref<Saved | null>(null)
const editing = ref<Where | null>(null)
const draft = ref('')
const templates = ref<{ code: string; name: string }[]>([])
const current = ref<Status | null>(null)
const answers = ref<Record<string, string>>({})
const filledCount = computed(() => (current.value?.questions ?? []).filter((q) => (answers.value[q.key] || '').trim()).length)
const busy = ref(false)

async function show() {
  open.value = true
  current.value = null
  try {
    templates.value = await api.get('/api/analysis/document-templates')
  } catch (e) {
    ElMessage.error((e as Error).message || '加载失败')
  }
}

async function choose(code: string) {
  busy.value = true
  saved.value = null
  editing.value = null
  try {
    answers.value = {}
    current.value = await api.get<Status>(`/api/analysis/tasks/${props.taskId}/documents/${code}`)
  } catch (e) {
    ElMessage.error((e as Error).message || '加载失败')
  } finally {
    busy.value = false
  }
}

async function submit() {
  const cur = current.value
  if (!cur) return
  const filled = Object.fromEntries(
    Object.entries(answers.value).filter(([, v]) => v && v.trim()),
  )
  if (!Object.keys(filled).length) {
    ElMessage.info('请先填写下面的内容')
    return
  }
  busy.value = true
  try {
    for (const [key, value] of Object.entries(filled)) {
      current.value = await api.post<Status>(
        `/api/analysis/tasks/${props.taskId}/documents/${cur.code}/answers`,
        { answers: { [key]: value.trim() } },
      )
    }
    answers.value = {}
  } catch (e) {
    ElMessage.error((e as Error).message || '保存失败')
  } finally {
    busy.value = false
  }
}

async function openOfficial(formCode: string) {
  busy.value = true
  try {
    if (!editable.value) {
      const list = await api.get<Array<{ id: string; task_id: string | null; form: { code: string } }>>('/api/forms/instances')
      const hit = list.find((r) => r.form.code === formCode && (!props.taskId || r.task_id === props.taskId)) || list.find((r) => r.form.code === formCode)
      if (!hit) {
        ElMessage.info('当前项目暂未填写该官方表格')
        return
      }
      open.value = false
      await router.push({ name: 'form', params: { id: hit.id } })
      return
    }
    const made = await api.post<{ id: string }>('/api/forms/instances', { form_code: formCode, task_id: props.taskId })
    open.value = false
    await router.push({ name: 'form', params: { id: made.id } })
  } catch (e) {
    ElMessage.error((e as Error).message || '打开失败')
  } finally {
    busy.value = false
  }
}

async function writePaper() {
  const cur = current.value
  if (!cur) return
  busy.value = true
  try {
    current.value = await api.post<Status>(`/api/analysis/tasks/${props.taskId}/documents/${cur.code}/paper`, {})
    ElMessage.success('论述已生成')
  } catch (e) {
    ElMessage.error((e as Error).message || '生成失败，可补充信息后重试')
  } finally {
    busy.value = false
  }
}

async function openEditor() {
  const cur = current.value
  if (!cur) return
  busy.value = true
  try {
    saved.value = await api.post<Saved>(`/api/analysis/tasks/${props.taskId}/documents/${cur.code}/saved`, {})
  } catch (e) {
    ElMessage.error((e as Error).message || '打开失败')
  } finally {
    busy.value = false
  }
}

function startEdit(where: Where, text: string) {
  if (!editable.value) return
  editing.value = where
  draft.value = text
}

const same = (a: Where | null, b: Where) =>
  !!a && a.block === b.block && a.item === b.item && a.row === b.row && a.col === b.col

async function saveEdit() {
  const cur = current.value
  if (!cur || !editing.value) return
  busy.value = true
  try {
    saved.value = await api.patch<Saved>(`/api/analysis/tasks/${props.taskId}/documents/${cur.code}/saved`, {
      ...editing.value,
      text: draft.value,
    })
    editing.value = null
    ElMessage.success('已保存修改')
  } catch (e) {
    ElMessage.error((e as Error).message || '保存失败')
  } finally {
    busy.value = false
  }
}

const when = (s: string | null) => localTime(s)

async function download(kind: 'docx' | 'xlsx' | 'pdf' = 'docx') {
  const cur = current.value
  if (!cur || !cur.ready || !downloadable.value) return
  busy.value = true
  try {
    const res = await fetch(`/api/analysis/tasks/${props.taskId}/documents/${cur.code}/export.${kind}`, {
      headers: { Authorization: `Bearer ${api.token()}` },
    })
    const blob = await receiveExport(res, () => {})
    const name = /filename\*=UTF-8''([^;]+)/i.exec(res.headers.get('content-disposition') || '')
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = name ? decodeURIComponent(name[1]) : `${cur.name}.${kind}`
    document.body.appendChild(a)
    a.click()
    a.remove()
    window.setTimeout(() => URL.revokeObjectURL(url), 30000)
    ElMessage.success(`${cur.name} 已交给浏览器下载`)
  } catch (e) {
    ElMessage.error((e as Error).message || '下载失败，可重试')
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <el-button size="small" :disabled="disabled" data-testid="btn-compliance-documents" @click="show">
    合规文件
  </el-button>
  <el-dialog v-model="open" title="生成合规文件" width="820px" append-to-body>
    <div v-if="!current">
      <p class="hint">选择要生成的文件，系统会用这次评估里已有的信息填写，缺少的会请你补充。</p>
      <div class="choices">
        <el-button
          v-for="t in templates"
          :key="t.code"
          :loading="busy"
          :data-testid="`doc-${t.code}`"
          @click="choose(t.code)"
        >
          {{ t.name }}
        </el-button>
      </div>
    </div>
    <div v-else>
      <div v-if="current.needs_paper" class="paper" data-testid="doc-needs-paper">
        <b>{{ current.name }}</b>
        <p>信息已齐全。这份文件的核心是一篇论述，系统会据你提供的信息和目标国法规条文写成，每个法律判断都标注参考条文，写完后可以逐处修改。生成大约需要 1 分钟。</p>
        <el-button type="primary" :loading="busy" v-if="editable" data-testid="btn-doc-paper" @click="writePaper">生成论述</el-button>
        <el-button link type="primary" @click="current = null">换一种文件</el-button>
      </div>
      <div v-else>
      <el-alert v-if="current.official_forms?.length" type="success" :closable="false" show-icon class="official" data-testid="doc-official">
        <template #title>数据从该国发出时，主管机构有官方表格：{{ current.official_forms.map((f) => f.name_zh).join('、') }}</template>
        <template #default>
          <span>评估里已有的信息会自动带入，缺的逐项问，导出的是官方原表，可直接打印盖章。</span>
          <el-button v-for="f in current.official_forms" :key="f.code" size="small" type="success" style="margin-left: 10px" :loading="busy" data-testid="btn-open-official" @click="openOfficial(f.code)">{{ editable ? '填写官方表格' : '查看官方表格' }}</el-button>
        </template>
      </el-alert>
      <div class="head">
        <b>{{ current.name }}</b>
        <el-button link type="primary" @click="current = null">换一种文件</el-button>
      </div>
      <div v-if="current.ready" class="ready">
        <div v-if="!saved">
          <p>信息已齐全，可以生成文件。</p>
          <el-collapse v-if="current.optional_questions?.length" class="optional" data-testid="doc-optional">
            <el-collapse-item :title="`另有 ${current.optional_questions.length} 项企业内部选填信息可按需补充（暂未填写的在文件中标为【待企业填写】）`">
              <div v-for="q in current.optional_questions" :key="q.key" class="q">
                <div class="q-text">{{ q.question }}</div>
                <el-input v-if="q.short" v-model="answers[q.key]" maxlength="200" :disabled="!editable" :data-testid="`doc-answer-${q.key}`" />
                <el-input v-else v-model="answers[q.key]" type="textarea" :rows="2" maxlength="1000" :disabled="!editable" :data-testid="`doc-answer-${q.key}`" />
              </div>
              <el-button type="primary" :loading="busy" v-if="editable" data-testid="btn-doc-submit-optional" @click="submit">保存补充</el-button>
            </el-collapse-item>
          </el-collapse>
          <div class="row-actions">
            <el-tooltip :disabled="downloadable" content="当前只读账号未开通导出权限" placement="top">
              <span><el-button type="primary" :loading="busy" :disabled="!downloadable" data-testid="btn-doc-download" @click="download('docx')">下载 Word 文件</el-button></span>
            </el-tooltip>
            <el-tooltip :disabled="downloadable" content="当前只读账号未开通导出权限" placement="top">
              <span><el-button :loading="busy" :disabled="!downloadable" data-testid="btn-doc-download-pdf" @click="download('pdf')">下载 PDF 文件</el-button></span>
            </el-tooltip>
            <el-tooltip :disabled="downloadable" content="当前只读账号未开通导出权限" placement="top">
              <span><el-button :loading="busy" :disabled="!downloadable" data-testid="btn-doc-download-xlsx" @click="download('xlsx')">下载 Excel 文件</el-button></span>
            </el-tooltip>
            <el-button :loading="busy" v-if="editable" data-testid="btn-doc-edit" @click="openEditor">查看并修改文字</el-button>
          </div>
        </div>
        <div v-else>
          <p class="hint">点击任意一段文字即可修改，下载的文件会带上你的修改。每次修改都会留下记录。</p>
          <div class="doc">
            <template v-for="(b, bi) in saved.blocks" :key="bi">
              <h3 v-if="b.type === 'title'" class="d-title">{{ b.text }}</h3>
              <h4 v-else-if="b.type === 'heading'" class="d-heading">{{ b.text }}</h4>
              <div v-else-if="b.type === 'paragraph'">
                <div v-if="same(editing, { block: bi })">
                  <el-input v-model="draft" type="textarea" :rows="3" maxlength="4000" />
                  <div class="row-actions">
                    <el-button size="small" type="primary" :loading="busy" data-testid="btn-edit-save" @click="saveEdit">保存</el-button>
                    <el-button size="small" @click="editing = null">取消</el-button>
                  </div>
                </div>
                <p v-else class="editable" data-testid="doc-paragraph" @click="startEdit({ block: bi }, b.text || '')">{{ b.text }}</p>
              </div>
              <ul v-else-if="b.type === 'list'">
                <li v-for="(it, ii) in b.items" :key="ii">
                  <div v-if="same(editing, { block: bi, item: ii })">
                  <el-input v-model="draft" type="textarea" :rows="3" maxlength="4000" />
                  <div class="row-actions">
                    <el-button size="small" type="primary" :loading="busy" data-testid="btn-edit-save" @click="saveEdit">保存</el-button>
                    <el-button size="small" @click="editing = null">取消</el-button>
                  </div>
                </div>
                  <span v-else class="editable" @click="startEdit({ block: bi, item: ii }, it)">{{ it }}</span>
                </li>
              </ul>
              <table v-else-if="b.type === 'table'" class="d-table">
                <thead><tr><th v-for="h in b.head" :key="h">{{ h }}</th></tr></thead>
                <tbody>
                  <tr v-for="(r, ri) in b.rows" :key="ri">
                    <td v-for="(c, ci) in r" :key="ci">
                      <div v-if="same(editing, { block: bi, row: ri, col: ci })">
                  <el-input v-model="draft" type="textarea" :rows="3" maxlength="4000" />
                  <div class="row-actions">
                    <el-button size="small" type="primary" :loading="busy" data-testid="btn-edit-save" @click="saveEdit">保存</el-button>
                    <el-button size="small" @click="editing = null">取消</el-button>
                  </div>
                </div>
                      <span v-else class="editable" @click="startEdit({ block: bi, row: ri, col: ci }, c)">{{ c }}</span>
                    </td>
                  </tr>
                </tbody>
              </table>
            </template>
          </div>
          <div class="row-actions">
            <el-tooltip :disabled="downloadable" content="当前只读账号未开通导出权限" placement="top">
              <span><el-button type="primary" :loading="busy" :disabled="!downloadable" data-testid="btn-doc-download" @click="download('docx')">下载 Word 文件</el-button></span>
            </el-tooltip>
            <el-tooltip :disabled="downloadable" content="当前只读账号未开通导出权限" placement="top">
              <span><el-button :loading="busy" :disabled="!downloadable" @click="download('pdf')">下载 PDF 文件</el-button></span>
            </el-tooltip>
            <el-tooltip :disabled="downloadable" content="当前只读账号未开通导出权限" placement="top">
              <span><el-button :loading="busy" :disabled="!downloadable" @click="download('xlsx')">下载 Excel 文件</el-button></span>
            </el-tooltip>
          </div>
          <el-collapse v-if="saved.edits.length" class="log">
            <el-collapse-item :title="`修改记录（${saved.edit_count} 处）`">
              <div v-for="(e, i) in saved.edits" :key="i" class="log-row">
                <div>{{ e.user_name }} · {{ when(e.at) }} · {{ e.place }}</div>
                <div class="log-before">原：{{ e.before }}</div>
                <div>改：{{ e.after }}</div>
              </div>
            </el-collapse-item>
          </el-collapse>
        </div>
      </div>
      <div v-else>
        <p class="hint" data-testid="doc-question-count">请补充以下 {{ current.questions.length }} 项填报信息（已完成 {{ filledCount }} 项）：</p>
        <div v-for="q in current.questions" :key="q.key" class="q">
          <div class="q-text">{{ q.question }}</div>
          <div v-if="q.why" class="q-why">填报说明：{{ q.why }}</div>
          <el-input
            v-if="q.short"
            v-model="answers[q.key]"
            maxlength="200"
            :disabled="!editable"
            :data-testid="`doc-answer-${q.key}`"
          />
          <el-input
            v-else
            v-model="answers[q.key]"
            type="textarea"
            :rows="2"
            maxlength="1000"
            show-word-limit
            :disabled="!editable"
            :data-testid="`doc-answer-${q.key}`"
          />
        </div>
        <el-button type="primary" :loading="busy" v-if="editable" data-testid="btn-doc-submit" @click="submit">
          保存回答
        </el-button>
      </div>
    </div>
      </div>
  </el-dialog>
</template>

<style scoped>
.hint { color: #64748b; margin: 0 0 12px; }
.choices { display: flex; gap: 12px; flex-wrap: wrap; }
.head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; }
.q { margin-bottom: 16px; }
.q-text { font-weight: 600; margin-bottom: 4px; }
.doc { max-height: 52vh; overflow: auto; border: 1px solid #e2e8f0; border-radius: 6px; padding: 8px 16px; margin-bottom: 12px; }
.d-title { margin: 8px 0; }
.d-heading { margin: 14px 0 4px; color: #1d4ed8; }
.editable { cursor: text; border-radius: 3px; }
.editable:hover { background: #f1f5f9; }
.d-table { width: 100%; border-collapse: collapse; font-size: 13px; margin: 6px 0; }
.d-table th, .d-table td { border: 1px solid #e2e8f0; padding: 6px 8px; text-align: left; vertical-align: top; }
.row-actions { display: flex; gap: 8px; margin: 6px 0; }
.log { margin-top: 12px; }
.log-row { margin-bottom: 10px; font-size: 13px; color: #334155; }
.log-before { color: #64748b; text-decoration: line-through; }
.q-why { color: #64748b; font-size: 13px; margin-bottom: 6px; }
.optional { margin: 10px 0 14px; }
.paper p { color: var(--el-text-color-regular); line-height: 1.8; margin: 8px 0 14px; }
.official { margin-bottom: 12px; }
</style>
