<script setup lang="ts">
// 更新通知：法规、合规简报、准入事项更新后与本企业有关的提醒，加上自己订阅的法规变化（需求 2.3：按国家、
// 法规类型、业务领域订阅；内容含变更前后对比、影响说明、要做的事、关联项目）。同一次变化只一条。
import { canEdit, localTime, COUNTRY_NAMES } from '@cyshuhe/shared'
import { ElMessage } from 'element-plus'
import { computed, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../api'
import { useAuth } from '../auth'
import ChangeDetail, { type Advice } from '../components/ChangeDetail.vue'
interface Related { task_id?: string; admission_id?: string; title: string; kind: string; notice_id: string | null; cites: boolean; amended?: boolean }
interface CountryLink {
  country_code?: string
  code?: string
  country_name?: string
  country_zh?: string
  name?: string
  label?: string
  link?: string
  url?: string
}
interface Notice {
  id: string; kind: string; title?: string; body?: string; link?: string; country_links?: CountryLink[]; task_id: string; task_kind?: string; task_title: string; document_title: string; country_code: string
  summary: string; dimension_codes: string[]; read_at: string | null; due_at: string | null
  created_at: string; state: string; advice?: Advice | null; related?: Related[]; revised?: boolean
}
interface DocUpdate { project_id: string; project_title: string; project_created_at?: string | null; code: string; name: string; written_at: string | null; changed?: string[] }
interface Option { value: string; label: string }
interface Subscription { countries: string[]; doc_types: string[]; topics: string[]; summary: string; since: string | null; options: { countries: Option[]; doc_types: Option[]; topics: Option[] } }
interface DigestItem { id: string; title: string; publisher: string; published_on: string; level: string; level_label: string; url: string; summary?: string; reason?: string; country_code: string | null; country_label?: string }
interface WeeklyDigest { id: string; period_label: string; subject: string; high_count: number; medium_count: number; low_count: number; sent_at: string | null; items: DigestItem[] }
const items = ref<Notice[]>([]), total = ref(0), page = ref(1), busy = ref(false), executing = ref('')
const docUpdates = ref<DocUpdate[]>([])
const weeklyDigests = ref<WeeklyDigest[]>([])
const openDigestIds = reactive(new Set<string>())
const briefingCountries = ref<Set<string>>(new Set())
const stripQuotes = (s?: string | null) => (s || '').replace(/[\u201c\u201d]/g, '')
// 两个项目同名时（各自独立建的），在提醒里标出各自的建立时间，免得像同一条重复出现
const sameTitle = (u: DocUpdate) => new Set(docUpdates.value.filter((x) => x.project_title === u.project_title).map((x) => x.project_id)).size > 1
const router = useRouter()
const auth = useAuth()
const editable = computed(() => canEdit(auth.user))
const fresh = ref<Set<string>>(new Set())  // 这次进页面前还没看过的几条：列表里标新
const COUNTRY_ZH: Record<string, string> = COUNTRY_NAMES
// 打开通知列表且确有未读通知时记为已读，铃铛清零
async function markSeen() {
  if (!items.value.some((n) => !n.read_at)) return
  try {
    const r = await api.post<{ fresh: string[] }>('/notifications/read-all')
    r.fresh.forEach((id) => fresh.value.add(id))
  } catch { /* 记已读失败不挡看列表 */ }
  window.dispatchEvent(new Event('notifications-read'))
}
async function load() {
  busy.value = true
  try {
    const result = await api
      .get<{ items?: Notice[]; total?: number }>(`/notifications?offset=${(page.value - 1) * 20}&limit=20`)
      .catch((e) => {
        ElMessage.error((e as Error).message || '加载通知列表失败')
        return { items: [] as Notice[], total: 0 }
      })
    items.value = Array.isArray(result?.items) ? result.items : []
    total.value = typeof result?.total === 'number' ? result.total : items.value.length
  } finally {
    busy.value = false
  }

  const [docsRes, subRes, digestsRes, kcRes, wpsRes] = await Promise.allSettled([
    api.get<DocUpdate[]>('/analysis/doc-projects/updates'),
    api.get<Subscription>('/notifications/subscription'),
    api.get<WeeklyDigest[]>('/notifications/weekly-digests'),
    api.get<unknown>('/knowledge/overview'),
    api.get<Array<{ country_code: string; available: boolean }>>('/knowledge/whitepapers'),
  ])

  docUpdates.value = docsRes.status === 'fulfilled' && Array.isArray(docsRes.value) ? docsRes.value : []
  if (subRes.status === 'fulfilled' && subRes.value && typeof subRes.value === 'object') {
    subscription.value = subRes.value
  }
  const digests = digestsRes.status === 'fulfilled' && Array.isArray(digestsRes.value) ? digestsRes.value : []
  weeklyDigests.value = digests
  if (digests.length && !openDigestIds.size) openDigestIds.add(digests[0].id)

  let kcArr: Array<{ country_code?: string; code?: string }> = []
  if (kcRes.status === 'fulfilled' && kcRes.value) {
    if (Array.isArray(kcRes.value)) kcArr = kcRes.value as Array<{ country_code?: string; code?: string }>
    else if (typeof kcRes.value === 'object' && Array.isArray((kcRes.value as { countries?: unknown[] }).countries)) {
      kcArr = (kcRes.value as { countries: Array<{ country_code?: string; code?: string }> }).countries
    }
  }
  const wpsArr = wpsRes.status === 'fulfilled' && Array.isArray(wpsRes.value) ? wpsRes.value : []
  const dynamicCodes = kcArr.map((c) => c.country_code || c.code || '').filter(Boolean)
  const wpCodes = wpsArr.filter((w) => w.available).map((w) => w.country_code).filter(Boolean)
  briefingCountries.value = new Set(
    wpCodes.length ? wpCodes.filter((code) => !dynamicCodes.length || dynamicCodes.includes(code)) : dynamicCodes,
  )
}
async function read(n: Notice) {
  if (n.read_at) return
  try { await api.post(`/notifications/${n.id}/read`) } catch { /* 记已读失败不挡跳转 */ }
  window.dispatchEvent(new Event('notifications-read'))
}
// 点任何跳转按钮都记为已读，再打开对应页面
async function open(n: Notice) {
  await read(n)
  if (n.link) await router.push(n.link)
}
function countryLinkHref(cl: CountryLink): string {
  if (cl.link) return cl.link
  if (cl.url) return cl.url
  const code = cl.country_code || cl.code || ''
  return code ? `/knowledge?country=${encodeURIComponent(code)}&from=/notifications` : '/knowledge'
}
function countryLinkLabel(cl: CountryLink): string {
  if (cl.label) return stripQuotes(cl.label)
  const code = cl.country_code || cl.code || ''
  const name = cl.country_name || cl.country_zh || cl.name || COUNTRY_ZH[code] || code
  return name ? `查看${name}简报` : '查看简报'
}
async function openCountryLink(n: Notice, cl: CountryLink) {
  await read(n)
  await router.push(countryLinkHref(cl))
}
function digestMeta(it: DigestItem): string {
  const parts = [
    it.country_label || (it.country_code ? COUNTRY_ZH[it.country_code] || it.country_code : ''),
    it.publisher ? `发布机构：${stripQuotes(it.publisher)}` : '',
    it.published_on || '近期收录',
  ].filter(Boolean)
  return parts.join(' · ')
}
function digestLevelClass(level?: string): string {
  if (level === 'high') return 'high'
  if (level === 'medium') return 'medium'
  return 'low'
}
const projectLink = (id: string, kind?: string) =>
  kind === 'documents' ? { path: '/documents', query: { project: id } } : { path: '/analysis', query: { task: id } }
async function openTask(n: Notice, id: string, kind?: string) {
  await read(n)
  await router.push(projectLink(id, kind))
}
const OPEN_LABEL: Record<string, string> = { whitepaper: '查看简报', admission: '打开准入项目', subscription: '阅读这部法规' }
async function reevaluate(n: Notice, noticeId = n.id) {
  if (executing.value) return
  executing.value = noticeId
  try {
    const result = await api.post<{ task_id: string }>(`/notifications/${noticeId}/reevaluate`)
    window.dispatchEvent(new Event('notifications-read'))
    await router.push({ path: '/analysis', query: { task: result.task_id } })
  } catch (e) { ElMessage.error((e as Error).message) }
  finally { executing.value = '' }
}
const date = (value: string | null) => localTime(value) || '未记录'
// 用到改动条文的项目逐个列；不受影响的准入项目合成一句，不逐条重复
function shownRelated(n: Notice): Related[] {
  const all = n.related ?? []
  const quiet = all.filter((r) => r.admission_id && !r.cites)
  const rest = all.filter((r) => !(r.admission_id && !r.cites))
  if (quiet.length < 2) return all
  return [...rest, { title: quiet.map((r) => r.title).join('、'), kind: 'admission-group', notice_id: null, cites: false }]
}
// 每个相关项目一句：要不要重新生成、做什么
function relatedLine(n: Notice, r: Related) {
  if (r.kind === 'admission-group') return `你在${COUNTRY_ZH[n.country_code] ?? '这个国家'}的其他准入项目（${r.title}）不依据这部法规，申请材料不用重新生成。`
  if (r.admission_id) return r.cites
    ? (editable.value ? `准入项目（${r.title}）依据这部法规：按上方操作提示核对申请材料和递交清单，有改动的重新生成。` : `准入项目（${r.title}）依据这部法规：申请材料和递交清单可能要按新条文调整。`)
    : `准入项目（${r.title}）不依据这部法规：申请材料不用重新生成。`
  if (r.amended) return r.cites
    ? (editable.value ? `合规评估（${r.title}）的文件引用了被修改的条文：已生成的文件按修改前的条文写，打开项目按新条文重新生成。` : `合规评估（${r.title}）的文件引用了被修改的条文：现有文件按修改前的条文写成。`)
    : `合规评估（${r.title}）的文件没有引用被修改的条文：不用重新生成。`
  return r.cites
    ? (editable.value ? `合规评估（${r.title}）引用了改动的条文：已生成的文件按旧条文写成，要按新条文重新评估。` : `合规评估（${r.title}）引用了改动的条文：已生成的文件按旧条文写成。`)
    : `合规评估（${r.title}）没有引用这部法规：已生成的文件不用重新生成。`
}
// 一张卡一个标题加一行变化的方面；受影响项目列成小表；改动前后对照收进展开
function aspects(n: Notice): string {
  const seen = new Set<string>()
  for (const p of n.advice?.pairs ?? []) seen.add(p.label || p.kind)
  return [...seen].join('、')
}
const bodyOpen = reactive(new Set<string>())  // 长正文（如简报更新）默认只露 4 行
const relatedKind = (r: Related) => (r.kind === 'admission-group' || r.admission_id ? '准入项目' : '合规评估')
async function openRelated(n: Notice, r: Related) {
  if (r.admission_id) await openAdmission(n, r.admission_id)
  else if (r.task_id) await openTask(n, r.task_id, r.kind)
}
async function openAdmission(n: Notice, id: string) {
  await read(n)
  await router.push({ path: '/admission', query: { project: id } })
}

// ---------- 我的订阅 ----------
const subscription = ref<Subscription | null>(null)
const editing = ref(false)
const form = ref<{ countries: string[]; doc_types: string[]; topics: string[] }>({ countries: [], doc_types: [], topics: [] })
const saving = ref(false)
function editSubscription() {
  const s = subscription.value
  form.value = { countries: [...(s?.countries ?? [])], doc_types: [...(s?.doc_types ?? [])], topics: [...(s?.topics ?? [])] }
  editing.value = true
}
function toggle(list: string[], v: string) {
  const i = list.indexOf(v)
  if (i >= 0) list.splice(i, 1)
  else list.push(v)
}
async function saveSubscription(clear = false) {
  saving.value = true
  try {
    subscription.value = await api.put<Subscription>('/notifications/subscription', clear ? {} : form.value)
    editing.value = false
    ElMessage.success(clear ? '已取消订阅' : subscription.value.summary ? '已订阅，之后收录的变化会在这里提醒' : '没有选任何一项，已取消订阅')
  } catch (e) { ElMessage.error((e as Error).message) } finally { saving.value = false }
}
// 第 7.5 项 规则 31：按来源分组（每组一个短标题），组内仍按时间先后
const GROUPS: { key: string; title: string; kinds: string[] }[] = [
  { key: 'law', title: '法规变化', kinds: ['subscription', 'source_change'] },
  { key: 'whitepaper', title: '合规简报', kinds: ['whitepaper'] },
  { key: 'admission', title: '准入事项', kinds: ['admission'] },
]
const groups = computed(() => {
  const known = new Set(GROUPS.flatMap((g) => g.kinds))
  const out = GROUPS.map((g) => ({ key: g.key, title: g.title, items: items.value.filter((n) => g.kinds.includes(n.kind)) }))
  const rest = items.value.filter((n) => !known.has(n.kind))
  if (rest.length) out.push({ key: 'other', title: '其他', items: rest })
  return out.filter((g) => g.items.length)
})
const nothingChosen = computed(() => !form.value.countries.length && !form.value.doc_types.length && !form.value.topics.length)

onMounted(async () => { await load(); await markSeen() })
</script>
<template>
  <div class="management-page" v-loading="busy">
    <header class="page-heading"><div><h1>更新通知</h1><p>汇集与本企业评估及准入项目相关的法规修订、国家简报更新及行业合规周报，支持按国家与领域定制订阅。</p></div>
      </header>

    <section class="sub-bar" data-testid="subscription-bar">
      <template v-if="subscription?.summary">
        <span class="sub-main"><strong>本企业订阅范围</strong><span data-testid="subscription-summary">{{ subscription.summary }}</span></span>
        <el-button v-if="editable" size="small" data-testid="subscription-edit" @click="editSubscription">修改订阅</el-button>
      </template>
      <template v-else>
        <span class="sub-main"><strong>订阅法规变化</strong><span>{{ editable ? '支持按目标国家、法规类型与业务领域定制订阅；新规发布或条文修订时自动推送条文前后对照、影响分析及应对建议。' : '支持查阅本单位已订阅国家与领域的法规修订及国家简报更新通知；如需调整订阅范围，请联系企业用户设置。' }}</span></span>
        <el-button v-if="editable" type="primary" size="small" data-testid="subscription-edit" @click="editSubscription">设置订阅</el-button>
      </template>
    </section>

    <el-dialog v-model="editing" title="订阅法规变化" width="min(640px, 94vw)" data-testid="subscription-dialog">
      <div v-if="subscription" class="sub-form">
        <div class="sub-group"><p>国家<small>不选则不限国家</small></p>
          <div class="tags"><el-check-tag v-for="o in subscription.options.countries" :key="o.value" :checked="form.countries.includes(o.value)" :data-testid="`sub-country-${o.value}`" @change="toggle(form.countries, o.value)">{{ o.label }}</el-check-tag></div></div>
        <div class="sub-group"><p>法规类型<small>不选则不限类型</small></p>
          <div class="tags"><el-check-tag v-for="o in subscription.options.doc_types" :key="o.value" :checked="form.doc_types.includes(o.value)" :data-testid="`sub-type-${o.value}`" @change="toggle(form.doc_types, o.value)">{{ o.label }}</el-check-tag></div></div>
        <div class="sub-group"><p>业务领域<small>不选则不限领域</small></p>
          <div class="tags"><el-check-tag v-for="o in subscription.options.topics" :key="o.value" :checked="form.topics.includes(o.value)" :data-testid="`sub-topic-${o.value}`" @change="toggle(form.topics, o.value)">{{ o.label }}</el-check-tag></div></div>
        <p v-if="nothingChosen" class="sub-hint">三栏至少选一项才算订阅。</p>
      </div>
      <template #footer>
        <el-button v-if="subscription?.summary" link type="danger" @click="saveSubscription(true)">取消订阅</el-button>
        <el-button @click="editing = false">关闭</el-button>
        <el-button type="primary" :disabled="nothingChosen" :loading="saving" data-testid="subscription-save" @click="saveSubscription()">保存</el-button>
      </template>
    </el-dialog>

    <section v-if="!items.length && !docUpdates.length && !busy" class="empty-notices-wrap" data-testid="notifications-empty">
      <h2 v-if="weeklyDigests.length" class="group-h">站内消息通知<small>0</small></h2>
      <p class="cy-empty">暂无消息通知</p>
    </section>

    <template v-if="weeklyDigests.length">
      <h2 class="group-h">行业合规周报<small>{{ weeklyDigests.length }}</small></h2>
      <article v-for="dg in weeklyDigests" :key="dg.id" class="notice-card digest-card" data-testid="weekly-digest-section">
        <div class="notice-heading">
          <strong>{{ stripQuotes(dg.subject) }}</strong>
          <span class="digest-period">{{ stripQuotes(dg.period_label) }} · 共 {{ dg.items.length }} 条</span>
        </div>
        <div v-if="openDigestIds.has(dg.id)" class="digest-items">
          <div v-for="(it, idx) in dg.items" :key="it.id" class="digest-row">
            <span class="digest-idx">{{ idx + 1 }}.</span>
            <div class="digest-main">
              <div class="digest-title-line">
                <span v-if="it.level_label" class="digest-level" :class="digestLevelClass(it.level)">{{ stripQuotes(it.level_label) }}</span>
                <a :href="it.url" target="_blank" rel="noopener noreferrer" class="digest-link">{{ stripQuotes(it.title) }}</a>
              </div>
              <small>{{ digestMeta(it) }}</small>
              <small v-if="it.summary" class="digest-summary">{{ stripQuotes(it.summary) }}</small>
            </div>
            <el-button v-if="it.country_code && briefingCountries.has(it.country_code)" size="small" plain @click="router.push({ path: '/knowledge', query: { country: it.country_code, from: '/notifications' } })">查看该国简报</el-button>
          </div>
        </div>
        <div class="notice-foot">
          <span class="notice-time">推送于 {{ date(dg.sent_at) }}</span>
          <el-button size="small" @click="openDigestIds.has(dg.id) ? openDigestIds.delete(dg.id) : openDigestIds.add(dg.id)">{{ openDigestIds.has(dg.id) ? '收起周报条目' : `展开本期 ${dg.items.length} 条动态` }}</el-button>
        </div>
      </article>
    </template>
    <h2 v-if="docUpdates.length" class="group-h">合规文件<small>{{ docUpdates.length }}</small></h2>
    <article v-if="docUpdates.length" class="notice-card" data-testid="doc-update">
      <div class="notice-heading"><strong>{{ docUpdates.length }} 份合规文件引用的法规条文有更新</strong><el-tag type="warning">引用的条文有更新</el-tag></div>
      <div class="rel-table-wrap">
        <table class="rel-table doc-upd">
          <thead><tr><th class="c-project">项目 · 文件</th><th>变更内容</th><th class="c-act"></th></tr></thead>
          <tbody><tr v-for="u in docUpdates" :key="`${u.project_id}-${u.code}`" class="related-row" data-testid="doc-update-row">
            <td class="c-project">{{ u.project_title }}<template v-if="sameTitle(u)">（{{ date(u.project_created_at ?? null) }} 创建）</template> · {{ u.name }}</td>
            <td>{{ u.changed?.length ? `${u.changed.join('、')}有更新` : '引用的法规条文有更新' }}</td>
            <td class="c-act"><span class="related-actions"><el-button size="small" @click="router.push({ path: '/documents', query: { project: u.project_id } })">进入项目</el-button></span></td>
          </tr></tbody>
        </table>
      </div>
      <details class="change-more"><summary>更新原因与处理说明</summary>
        <p class="notice-time">论述生成后，所引用的法规条文发生了修订。{{ editable ? '现有文件仍可正常下载；点击按新条文重新生成后，正文论述与条文引用将同步更新。' : '现有文件内容保持不变，由企业用户重新生成后同步更新。' }}</p></details>
    </article>
    <template v-for="g in groups" :key="g.key">
    <h2 class="group-h">{{ g.title }}<small>{{ g.items.length }}</small></h2>
    <template v-for="n in g.items" :key="n.id">
    <!-- 订阅的法规有变化 -->
    <article v-if="n.kind === 'subscription'" class="notice-card" :class="{ 'is-fresh': fresh.has(n.id) }" data-testid="notice-subscription">
      <div class="notice-heading"><strong>{{ n.title }}</strong><el-tag size="small">{{ n.revised ? '修订' : '新发布' }}</el-tag>
        <el-tag v-if="fresh.has(n.id)" size="small" type="primary" data-testid="notice-new">新</el-tag></div>
      <p v-if="aspects(n)" class="aspect-line" data-testid="notice-aspects"><b>涉及维度</b>{{ aspects(n) }}</p>
      <p class="notice-time">{{ n.body ? `${n.body} ` : '' }}收录于 {{ date(n.created_at) }}</p>
      <section v-if="n.related?.length" class="related" data-testid="notice-related">
        <table class="rel-table">
          <thead><tr><th class="c-country">国家</th><th class="c-project">项目</th><th>处理建议</th><th class="c-act"></th></tr></thead>
          <tbody><tr v-for="r in shownRelated(n)" :key="r.task_id || r.admission_id || r.title" class="related-row">
            <td class="c-country">{{ COUNTRY_ZH[n.country_code] ?? n.country_code }}</td>
            <td class="c-project"><span class="rel-kind">{{ relatedKind(r) }}</span>{{ r.title }}</td>
            <td>{{ relatedLine(n, r) }}</td>
            <td class="c-act"><span class="related-actions"><el-button v-if="r.task_id || r.admission_id" size="small" @click="openRelated(n, r)">对照核对</el-button>
              <el-button v-if="r.cites && r.notice_id && editable" size="small" :loading="executing === r.notice_id" @click="reevaluate(n, r.notice_id)">按新条文重新评估</el-button></span></td>
          </tr></tbody>
        </table>
      </section>
      <p v-else class="notice-time" data-testid="notice-no-related">本企业当前评估与准入项目暂未引用该法规。</p>
      <details v-if="n.advice" class="change-more"><summary>条文修订对照、影响分析与应对建议</summary><ChangeDetail :advice="n.advice ?? null" /></details>
      <div class="notice-foot">
        <span class="notice-time">{{ date(n.created_at) }}</span>
        <el-button @click="open(n)">查阅法规全文</el-button>
      </div>
    </article>
    <!-- 简报出新版、准入事项有变化 -->
    <article v-else-if="n.kind !== 'source_change'" class="notice-card" :class="{ 'is-fresh': fresh.has(n.id) }" :data-testid="`notice-${n.kind}`">
      <div class="notice-heading"><strong>{{ stripQuotes(n.title) }}</strong>
        <el-tag v-if="fresh.has(n.id)" size="small" type="primary" data-testid="notice-new">新</el-tag></div>
      <p class="notice-body" :class="{ clamped: (n.body || '').length > 240 && !bodyOpen.has(n.id) }">{{ stripQuotes(n.body) }}</p>
      <button v-if="(n.body || '').length > 240" type="button" class="body-toggle" @click="bodyOpen.has(n.id) ? bodyOpen.delete(n.id) : bodyOpen.add(n.id)">{{ bodyOpen.has(n.id) ? '收起' : '展开全文' }}</button>
      <div class="notice-foot">
        <span class="notice-time">{{ date(n.created_at) }}</span>
        <span v-if="n.country_links?.length" class="country-jump-links" data-testid="notice-country-links">
          <el-button
            v-for="(cl, ci) in n.country_links"
            :key="cl.country_code || cl.code || cl.link || ci"
            size="small"
            @click="openCountryLink(n, cl)"
          >{{ countryLinkLabel(cl) }}</el-button>
        </span>
        <el-button v-else-if="n.link" @click="open(n)">{{ OPEN_LABEL[n.kind] || '查看' }}</el-button>
      </div>
    </article>
    <!-- 评估报告引用的条文有改动（没订阅这部法规时单独一条） -->
    <article v-else class="notice-card" :class="{ 'is-fresh': fresh.has(n.id) }" data-testid="update-notification">
      <div class="notice-heading"><strong>{{ COUNTRY_ZH[n.country_code] ?? n.country_code }}《{{ n.document_title }}》有修订</strong>
        <el-tag :type="n.state === 'resolved' ? 'info' : 'warning'">{{ n.state === 'resolved' ? '已按新条文重新评估' : '评估引用的条文有改动' }}</el-tag>
        <el-tag v-if="fresh.has(n.id)" size="small" type="primary" data-testid="notice-new">新</el-tag></div>
      <p v-if="aspects(n)" class="aspect-line"><b>涉及维度</b>{{ aspects(n) }}</p>
      <p class="notice-time">平台收录于 {{ date(n.created_at) }}</p>
      <section class="related">
        <table class="rel-table">
          <thead><tr><th class="c-country">国家</th><th class="c-project">项目</th><th>处理建议</th><th class="c-act"></th></tr></thead>
          <tbody><tr class="related-row">
            <td class="c-country">{{ COUNTRY_ZH[n.country_code] ?? n.country_code }}</td>
            <td class="c-project"><span class="rel-kind">合规评估</span>{{ n.task_title }}</td>
            <td>引用了改动的条文，涉及报告里 {{ n.dimension_codes.length }} 个方面。</td>
            <td class="c-act"><span class="related-actions"><el-button size="small" @click="openTask(n, n.task_id, n.task_kind)">对照核对</el-button>
              <el-button v-if="n.state !== 'resolved' && editable" size="small" :loading="executing === n.id" @click="reevaluate(n)">按新条文重新评估</el-button></span></td>
          </tr></tbody>
        </table>
      </section>
      <details v-if="n.advice" class="change-more"><summary>条文修订对照、影响分析与应对建议</summary><ChangeDetail :advice="n.advice ?? null" /></details>
    </article>
    </template>
    </template>
    <el-pagination v-if="total > 20" v-model:current-page="page" :page-size="20" :total="total"
      layout="prev, pager, next" @current-change="load" />
  </div>
</template>
<style scoped>
.management-page small { font-size: 13px; }
.digest-period { margin-left: auto; font-size: 13px; color: #64748b; }
.digest-items { margin: 10px 0 6px; display: flex; flex-direction: column; gap: 8px; }
.digest-row { display: grid; grid-template-columns: 28px minmax(0, 1fr) auto; align-items: start; gap: 8px; padding: 10px 12px; background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 8px; }
.digest-idx { font-size: 14px; font-weight: 600; color: #64748b; line-height: 1.5; }
.digest-main { display: flex; flex-direction: column; gap: 3px; min-width: 0; }
.digest-title-line { display: flex; flex-wrap: wrap; align-items: center; gap: 6px 8px; }
.digest-level { display: inline-block; padding: 1px 8px; border-radius: 999px; font-size: 13px; font-weight: 600; line-height: 1.4; white-space: nowrap; background: #e2e8f0; color: #334155; }
.digest-level.high { background: #fee2e2; color: #991b1b; }
.digest-level.medium { background: #fef3c7; color: #92400e; }
.digest-level.low { background: #f1f5f9; color: #475569; }
.digest-link { color: #0f172a; font-weight: 600; text-decoration: none; line-height: 1.5; font-size: 14px; }
.digest-link:hover { color: #2563eb; text-decoration: underline; }
.digest-main small { color: #64748b; font-size: 13px; line-height: 1.5; }
.digest-summary { color: #475569 !important; }
.country-jump-links { display: flex; flex-wrap: wrap; gap: 8px; justify-content: flex-end; }
.country-jump-links .el-button + .el-button { margin-left: 0; }
.notice-card{margin:0 0 12px;padding:20px 24px;border:1px solid var(--cy-line);border-radius:10px;background:white}
.notice-card.is-fresh{border-left:3px solid var(--el-color-primary);padding-left:22px}
.group-h{display:flex;align-items:baseline;gap:8px;margin:28px 0 12px;font-size:18px}.group-h small{font-size:13px;font-weight:400;color:var(--cy-text-3)}
.notice-heading{display:flex;align-items:center;gap:12px;flex-wrap:wrap}.notice-heading strong{font-size:16px;flex:1;min-width:240px}
.notice-card p{line-height:1.7;margin:10px 0;color:var(--el-text-color-regular)}.notice-card .notice-time{font-size:13px;color:var(--cy-text-2);margin:6px 0}
.notice-foot{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-top:8px}.notice-foot .notice-time{margin:0;white-space:nowrap;flex-shrink:0}
.notice-actions{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-top:12px}
.sub-bar{display:flex;align-items:center;gap:16px;padding:14px 18px;border:1px solid var(--el-border-color-light);border-radius:12px;background:#f8fafc;margin:4px 0 8px}
.sub-main{flex:1;display:flex;flex-direction:column;gap:2px;font-size:14px}.sub-main span{color:var(--cy-text-2);font-size:13px;line-height:1.6}
.sub-group{margin-bottom:16px}.sub-group p{margin:0 0 8px;font-weight:600;font-size:14px}.sub-group p small{margin-left:8px;font-weight:400;color:var(--cy-text-2);font-size:13px}
.tags{display:flex;flex-wrap:wrap;gap:8px}
.tags :deep(.el-check-tag){font-weight:600;font-size:13px}
.sub-hint{margin:0;color:var(--el-color-warning-dark-2);font-size:13px}
.notice-card .notice-body{white-space:pre-line}
.notice-body.clamped{display:-webkit-box;-webkit-line-clamp:4;-webkit-box-orient:vertical;overflow:hidden}
.body-toggle{border:0;background:none;padding:0;color:var(--el-color-primary);font:inherit;font-size:14px;cursor:pointer}
.related{margin-top:6px;width:100%;overflow-x:auto;-webkit-overflow-scrolling:touch}.related h4{margin:0 0 6px;font-size:14px}
.rel-table-wrap{width:100%;overflow-x:auto;-webkit-overflow-scrolling:touch}
.related-actions{display:flex;gap:6px;flex:none;justify-content:flex-end}.related-actions .el-button+.el-button{margin-left:0}
.aspect-line{margin:8px 0 4px !important;font-size:14px}.aspect-line b{margin-right:10px;color:var(--cy-text-2);font-weight:600}
.rel-table{width:100%;border-collapse:collapse;font-size:14px;margin-top:8px}
.rel-table th{text-align:left;font-weight:600;font-size:13px;color:var(--cy-text-2);padding:6px 10px;border-bottom:1px solid var(--cy-line);white-space:nowrap}
.rel-table td{padding:10px;border-bottom:1px solid var(--cy-line);vertical-align:top;line-height:1.6}
.rel-table .c-country{width:72px;white-space:nowrap}.rel-table .c-project{width:30%}.rel-table .c-act{width:1%;white-space:nowrap}
.rel-kind{display:inline-block;margin-right:6px;padding:0 6px;border-radius:4px;font-size:13px;line-height:20px;background:var(--cy-tone-muted-bg);color:var(--cy-text-2)}
.change-more{margin-top:12px}.change-more>summary{cursor:pointer;color:var(--el-color-primary);font-size:14px}
@media (max-width: 768px) {
  .notice-card{padding:16px}
  .notice-card.is-fresh{padding-left:14px}
  .notice-heading strong{min-width:0;flex-basis:100%}
  .sub-bar{flex-direction:column;align-items:flex-start;gap:10px;padding:12px 14px}
  .notice-foot{flex-direction:column;align-items:flex-start;gap:8px}
  .country-jump-links{justify-content:flex-start;width:100%}
  .digest-row{grid-template-columns:24px minmax(0,1fr)}
  .digest-row>.el-button{grid-column:2;justify-self:start}
}
</style>
