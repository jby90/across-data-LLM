<script setup lang="ts">
import { canExport, serverFilename } from '@cyshuhe/shared'
import CountryFlag from './CountryFlag.vue'
import { computed as _computed } from 'vue'
import { useAuth } from '../auth'
const _auth = useAuth()
const downloadable = _computed(() => canExport(_auth.user))
import { ElMessage } from 'element-plus'
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { reflow } from '@cyshuhe/shared/src/reflow'
import { api } from '../api'
import BlocksView, { type Block } from './BlocksView.vue'

const props = defineProps<{ country: string; countries?: { country_code: string; country_name_zh: string }[] }>()
const emit = defineEmits<{ choose: [code: string]; open: [documentId: string, clauseId?: string] }>()
interface Fact { label: string; text: string }
interface Item { title: string; sub: string; facts: Fact[]; ref: number | null; doc_id: string }
interface Dimension { code: string; title: string; total: number; unit: string; headline: string; intro: string; items: Item[]; regulators?: Item[] }
interface Ref { n: number; doc_id: string; clause_id: string; title: string; original_title: string; label: string; source: string; original: string; zh: string }
interface Page {
  country_code: string; country_name_zh: string; documents: number; clauses: number
  overview: { label: string; text: string }[]; dimensions: Dimension[]; refs: Ref[]; updated?: string
  has_whitepaper?: boolean
}
const page = ref<Page | null>(null)
const loading = ref(false)
const failed = ref('')
const open = ref(false)
const current = ref<Dimension | null>(null)
const PAGE_STEP = 10
const shownCount = ref(PAGE_STEP)
const refOpen = ref(false)
const refShown = ref<Ref | null>(null)
const fullOpen = ref(false)
const fullBusy = ref(false)
const full = ref<Block[]>([])
async function readFull() {
  fullOpen.value = true
  if (full.value.length) return
  fullBusy.value = true
  try { full.value = (await api.get<{ blocks: Block[] }>(`/knowledge/whitepapers/${props.country}`)).blocks }
  catch (e) { ElMessage.error((e as Error).message) } finally { fullBusy.value = false }
}

const items = computed(() => (current.value ? current.value.items.slice(0, shownCount.value) : []))
const remaining = computed(() => (current.value ? Math.max(0, current.value.items.length - shownCount.value) : 0))
const refs = computed(() => new Map((page.value?.refs || []).map((r) => [r.n, r])))

async function load() {
  if (!props.country) return
  loading.value = true
  failed.value = ''
  page.value = null
  try {
    page.value = await api.get<Page>(`/knowledge/whitepapers/${props.country}/page`)
  } catch (e) {
    failed.value = (e as Error).message || '加载失败'
  } finally { loading.value = false }
}
function openDimension(d: Dimension) { current.value = d; shownCount.value = PAGE_STEP; open.value = true }
function openRef(n: number | null) {
  const hit = n ? refs.value.get(n) : undefined
  if (hit) { refShown.value = hit; refOpen.value = true }
}
function openFullCite(n: number) {
  const refTbl = [...full.value].reverse().find((b) => b.type === 'table' && Array.isArray(b.rows))
  const row = refTbl?.rows?.find((r) => String(r[0] || '').replace(/[\[\]\s]/g, '') === String(n))
  if (row) {
    const srcRaw = String(row[1] || '').trim()
    const zhRaw = String(row[2] || '').trim()
    const cleanSrc = srcRaw.replace(/（[^）]+）/, '').replace(/\s+/g, '').trim()
    const norm = (s: string) => (s || '').replace(/[\s，,。.；;：:（）()“”"'「」『』【】\[\]—\-]/g, '')
    const zhNorm = norm(zhRaw.replace(/^第\s*[0-9A-Za-z一二三四五六七八九十百.]+\s*[条段节款]\s*/, ''))
    const artM = srcRaw.match(/第\s*([0-9A-Za-z一二三四五六七八九十百.]+)\s*[条段节]/)
    const artKey = artM ? artM[1] : ''
    const allRefs = page.value?.refs || []
    const matched =
      allRefs.find((r) => r.source.replace(/\s+/g, '') === cleanSrc) ||
      (zhNorm.length >= 10
        ? allRefs.find((r) => {
            const rz = norm(r.zh)
            return rz.includes(zhNorm.slice(0, 16)) || (rz.length >= 16 && zhNorm.includes(rz.slice(0, 16)))
          })
        : undefined) ||
      (artKey
        ? allRefs.find((r) => r.label.replace(/\s+/g, '').includes(artKey) && cleanSrc.includes(r.title.slice(0, 4)))
        : undefined)
    if (matched) {
      refShown.value = matched
      refOpen.value = true
      return
    }
    refShown.value = {
      n,
      doc_id: '',
      clause_id: '',
      title: cleanSrc,
      original_title: cleanSrc,
      label: '',
      source: srcRaw || `参考条文 [${n}]`,
      original: '（详见下方参考条文列表或法规浏览）',
      zh: zhRaw,
    }
    refOpen.value = true
    return
  }
  openRef(n)
}
function readInLibrary(documentId: string, clauseId?: string) {
  if (!documentId) return
  refOpen.value = false
  fullOpen.value = false
  open.value = false
  emit('open', documentId, clauseId)
}
async function download(kind: 'pdf' | 'docx') {
  if (!downloadable.value) return
  try {
    const res = await fetch(`/api/knowledge/whitepapers/${props.country}/export.${kind}`, { headers: { Authorization: `Bearer ${api.token()}` } })
    if (!res.ok) throw new Error('完整简报还没有生成，暂时无法下载')
    const url = URL.createObjectURL(await res.blob())
    const a = document.createElement('a')
    a.href = url; a.download = serverFilename(res, `${page.value?.country_name_zh || props.country}汽车数据合规洞察简报.${kind}`)
    document.body.appendChild(a); a.click(); a.remove()
    window.setTimeout(() => URL.revokeObjectURL(url), 30000)
  } catch (e) { ElMessage.error((e as Error).message) }
}
function onWindowKeydown(ev: KeyboardEvent) {
  if (ev.key !== 'Escape') return
  if (refOpen.value) {
    refOpen.value = false
  } else if (fullOpen.value) {
    fullOpen.value = false
  } else if (open.value) {
    open.value = false
  }
}
onMounted(() => window.addEventListener('keydown', onWindowKeydown))
onUnmounted(() => window.removeEventListener('keydown', onWindowKeydown))
watch(() => props.country, () => { full.value = []; void load() }, { immediate: true })
</script>

<template>
  <div v-loading="loading" class="wp-page" data-testid="whitepaper">
    <div v-if="!country" class="pick" data-testid="wp-pick">
      <p>选择一个国家，查看该国的汽车数据合规洞察简报：</p>
      <div class="pick-list"><el-button v-for="c in countries || []" :key="c.country_code" size="large" @click="emit('choose', c.country_code)"><CountryFlag :code="c.country_code" size="sm" style="margin-right: 6px" />{{ c.country_name_zh }}</el-button></div>
    </div>
    <el-alert v-else-if="failed" type="error" :closable="false" :title="failed" />
    <el-empty
      v-else-if="page && page.documents === 0 && page.has_whitepaper === false"
      :description="`${page.country_name_zh}暂未收录现行法规条文，待平台管理员导入法规并生成国家合规简报后即可在此查阅`"
      data-testid="wp-empty"
    />
    <template v-else-if="page">
      <section class="overview" data-testid="wp-overview">
        <h3><CountryFlag :code="page.country_code" style="margin-right: 8px" />{{ page.country_name_zh }}数据合规概述<small class="updated" data-testid="wp-updated">引证核心法规 {{ page.documents }} 部 · {{ page.clauses }} 条<template v-if="page.updated"> · 法规最后更新 {{ page.updated }}</template></small></h3>
        <div v-for="o in page.overview" :key="o.label" class="line"><span class="tag">{{ o.label }}</span><p>{{ o.text }}</p></div>
      </section>

      <section class="dims" data-testid="wp-dimensions">
        <button v-for="d in page.dimensions" :key="d.code" class="dim" :data-testid="`wp-dim-${d.code}`" @click="openDimension(d)">
          <b>{{ d.title }}</b>
          <span class="n">{{ d.total }}<small>{{ d.unit }}</small></span>
          <em>{{ d.headline }}</em>
        </button>
      </section>

      <div class="bar">
        <el-tooltip :disabled="page.has_whitepaper !== false" content="该国完整合规简报尚未生成，请先查阅上方八大维度要点" placement="top">
          <span><el-button type="primary" :disabled="page.has_whitepaper === false" data-testid="wp-read" @click="readFull">阅读完整简报</el-button></span>
        </el-tooltip>
        <el-tooltip :disabled="downloadable && page.has_whitepaper !== false" :content="page.has_whitepaper === false ? '该国完整合规简报尚未生成' : '当前只读账号未开通导出权限'" placement="top">
          <span><el-button :disabled="!downloadable || page.has_whitepaper === false" data-testid="wp-pdf" @click="download('pdf')">下载 PDF</el-button></span>
        </el-tooltip>
        <el-tooltip :disabled="downloadable && page.has_whitepaper !== false" :content="page.has_whitepaper === false ? '该国完整合规简报尚未生成' : '当前只读账号未开通导出权限'" placement="top">
          <span><el-button :disabled="!downloadable || page.has_whitepaper === false" data-testid="wp-docx" @click="download('docx')">下载 Word</el-button></span>
        </el-tooltip>
      </div>
      <el-drawer v-model="fullOpen" :title="`${page.country_name_zh}汽车数据合规洞察简报`" size="min(1040px, 94vw)" :close-on-press-escape="true" append-to-body data-testid="wp-full">
        <div v-loading="fullBusy"><BlocksView v-if="full.length" :blocks="full.filter((b) => b.type !== 'title')" outline clickable-cites @cite="openFullCite" /></div>
      </el-drawer>

      <el-drawer v-model="open" :title="current ? `${page.country_name_zh} · ${current.title}` : ''" size="min(620px, 94vw)" :close-on-press-escape="true" append-to-body data-testid="wp-drawer">
        <template v-if="current">
          <p class="intro">{{ current.intro }}</p>
          <h4 v-if="current.regulators?.length && items.length" class="section-subhead">已收录相关法规（共 {{ current.items.length }} 份）</h4>
          <ol v-if="items.length" class="points">
            <li v-for="(it, i) in items" :key="i" class="point" data-testid="wp-point">
              <h4>{{ it.title }}</h4>
              <p v-if="it.sub" class="sub">{{ it.sub }}</p>
              <dl v-if="it.facts.length && !it.doc_id">
                <template v-for="f in it.facts" :key="f.label"><dt>{{ f.label }}</dt><dd>{{ f.text }}</dd></template>
              </dl>
              <div class="point-foot">
                <button v-if="it.ref && refs.get(it.ref)" class="cite" type="button" data-testid="wp-cite" @click="openRef(it.ref)">
                  依据：{{ refs.get(it.ref)!.source }}
                </button>
                <button v-if="it.doc_id" class="cite" type="button" @click="readInLibrary(it.doc_id)">查阅法规全文</button>
              </div>
            </li>
          </ol>
          <div v-if="remaining > 0" class="more-bar">
            <el-button v-if="remaining > PAGE_STEP * 2" link type="primary" data-testid="wp-more-step" @click="shownCount += PAGE_STEP * 2">继续展开 {{ PAGE_STEP * 2 }} 项</el-button>
            <el-button link type="primary" data-testid="wp-more-all" @click="shownCount = current.items.length">显示全部其余 {{ remaining }} {{ current.code === 'D1' ? '份法规' : '项' }}（共 {{ current.items.length }} {{ current.code === 'D1' ? '份' : '项' }}）</el-button>
          </div>

          <section v-if="current.regulators?.length" class="regulators-block" data-testid="wp-regulators">
            <h4 class="section-subhead">主要监管与主管机构（共 {{ current.regulators.length }} 个）</h4>
            <ul class="regulator-list">
              <li v-for="(reg, ri) in current.regulators" :key="ri" class="regulator-item" data-testid="wp-regulator">
                <div class="regulator-head">
                  <span class="regulator-badge">主管机构</span>
                  <strong>{{ reg.title }}</strong>
                </div>
                <p v-if="reg.facts.length" class="regulator-desc">{{ reg.facts[0].text }}</p>
                <button v-if="reg.ref && refs.get(reg.ref)" class="cite" type="button" @click="openRef(reg.ref)">
                  条文依据：{{ refs.get(reg.ref)!.source }}
                </button>
              </li>
            </ul>
          </section>
        </template>
      </el-drawer>
      <el-dialog v-model="refOpen" :title="refShown?.source || '条文'" width="min(1080px, 94vw)" :close-on-press-escape="true" append-to-body data-testid="wp-ref">
        <template v-if="refShown">
          <p v-if="refShown.original_title !== refShown.title" class="src">{{ refShown.original_title }}</p>
          <div class="ref-cols">
            <section><h5>原文</h5><div class="quote" dir="auto">{{ reflow(refShown.original) }}</div></section>
            <section><h5>中文对照（参考译文，以官方原文为准）</h5><div class="quote">{{ reflow(refShown.zh) || '暂无中文译文。' }}</div></section>
          </div>
          <div v-if="refShown.doc_id" class="ref-foot"><el-button type="primary" plain @click="readInLibrary(refShown.doc_id, refShown.clause_id)">跳转至法规正文定位该条</el-button></div>
        </template>
      </el-dialog>
    </template>
  </div>
</template>

<style scoped>
.pick { padding: 28px 8px; }
.pick p { color: #475569; margin: 0 0 14px; }
.pick-list { display: flex; gap: 10px; flex-wrap: wrap; }
.wp-page { min-height: 200px; }
.overview { background: #fff; border: 1px solid var(--el-border-color-light); border-radius: 12px; padding: 18px 22px; }
.overview h3 { margin: 0 0 12px; font-size: 16px; }
.line { display: flex; gap: 12px; margin-bottom: 10px; }
.line p { margin: 0; font-size: 13px; line-height: 1.7; color: #334155; }
.tag { flex: none; align-self: flex-start; background: #eff6ff; color: #1d4ed8; font-size: 13px; padding: 1px 8px; border-radius: 4px; margin-top: 2px; }
.overview small { font-size: 13px; color: var(--el-text-color-secondary); }
.dims { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; margin: 16px 0; }
.dim { text-align: left; background: #fff; border: 1px solid var(--el-border-color-light); border-radius: 10px; padding: 12px 14px; cursor: pointer; display: flex; flex-direction: column; gap: 4px; font: inherit; }
.dim:hover, .dim:focus-visible { border-color: #3b82f6; outline: none; }
.dim b { font-size: 15px; line-height: 1.4; min-height: 2.8em; }
.dim em { min-height: 3em; }
.dim .n { font-size: 24px; font-weight: 600; color: #1e3a8a; }
.dim .n small { font-size: 13px; font-weight: 400; margin-left: 3px; color: #64748b; }
.dim em { font-style: normal; font-size: 13px; color: #64748b; line-height: 1.5; }
.bar { display: flex; align-items: center; gap: 8px; }
.muted { color: var(--el-text-color-secondary); font-size: 13px; }
.intro { font-size: 13px; line-height: 1.8; color: #334155; margin: 0 0 14px; }
.section-subhead { margin: 8px 0 10px; font-size: 14px; font-weight: 600; color: #1e293b; }
.more-bar { display: flex; gap: 16px; flex-wrap: wrap; padding: 8px 0 4px; }
.regulators-block { margin-top: 22px; padding-top: 18px; border-top: 2px solid #e2e8f0; }
.regulator-list { list-style: none; padding: 0; margin: 0; display: flex; flex-direction: column; gap: 10px; }
.regulator-item { background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 8px; padding: 12px 14px; }
.regulator-head { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin-bottom: 4px; }
.regulator-head strong { font-size: 15px; font-weight: 600; color: #0f172a; }
.regulator-badge { font-size: 13px; color: #1d4ed8; background: #eff6ff; border: 1px solid #dbeafe; border-radius: 4px; padding: 0 6px; line-height: 1.6; }
.regulator-desc { margin: 4px 0 6px; font-size: 13px; color: #475569; line-height: 1.65; }
.points { list-style: none; padding: 0; margin: 0; counter-reset: pt; }
.point { counter-increment: pt; border-top: 1px solid #eef2f7; padding: 14px 0 14px 30px; position: relative; }
.point::before { content: counter(pt); position: absolute; left: 0; top: 15px; width: 20px; height: 20px; border-radius: 50%; background: #eff6ff; color: #1d4ed8; font-size: 13px; text-align: center; line-height: 20px; }
.point h4 { margin: 0 0 4px; font-size: 16px; font-weight: 600; line-height: 1.55; color: #0f172a; }
.sub { margin: 0 0 6px; font-size: 13px; color: #64748b; line-height: 1.6; overflow-wrap: anywhere; }
dl { display: grid; grid-template-columns: 5.5em 1fr; gap: 4px 10px; margin: 8px 0 0; font-size: 13px; line-height: 1.75; }
dt { color: #64748b; }
dd { margin: 0; color: #1e293b; overflow-wrap: anywhere; }
.point-foot { display: flex; gap: 14px; flex-wrap: wrap; margin-top: 8px; }
.cite { font: inherit; font-size: 13px; color: #1d4ed8; background: none; border: 0; padding: 0; cursor: pointer; text-align: left; line-height: 1.6; }
.cite:hover, .cite:focus-visible { text-decoration: underline; outline: none; }
.src { color: #64748b; font-size: 13px; margin: -6px 0 12px; }
.ref-cols { display: grid; grid-template-columns: 1fr 1fr; gap: 18px; }
.ref-cols h5 { margin: 0 0 8px; font-size: 13px; font-weight: 600; color: #64748b; }
.quote { font-size: 14px; line-height: 1.85; white-space: pre-wrap; max-height: 60vh; overflow: auto; background: #f8fafc; border-radius: 8px; padding: 12px 14px; color: #1e293b; }
.ref-foot { margin-top: 14px; text-align: right; }
@media (max-width: 760px) { .ref-cols { grid-template-columns: 1fr; } .quote { max-height: 40vh; } }
.updated { margin-left: 10px; font-size: 13px; font-weight: normal; color: #64748b; }
</style>
