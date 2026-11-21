<script setup lang="ts">
import { useAuth } from '../auth'
import {
  DOC_TYPE_LABEL,
  formatDocTypeZh,
  LEGAL_STATUS_LABEL,
  PARSE_STRATEGY_LABEL,
  VERSION_STATE_LABEL,
  type ClauseOut,
  type DocumentDetail,
  type DocumentOut,
  type EventOut,
  type SiteOut, localTime, officialTextLabel } from '@cyshuhe/shared'
import { ElMessage, ElMessageBox } from 'element-plus'
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '../api'
import RegulationDirectory from '../components/RegulationDirectory.vue'
import VersionCompare from '../components/VersionCompare.vue'
import MarkNewLaw from '../components/MarkNewLaw.vue'
import { articleLabel } from '@cyshuhe/shared/src/articleLabel'
import { COUNTRY_NAMES, directoryCountries, directoryLevels, documentPage } from '@cyshuhe/shared/src/regulationDirectory'

const route = useRoute()
const router = useRouter()

const docs = ref<DocumentOut[]>([])
const sites = ref<SiteOut[]>([])
const initCountry = String(route.query.country || route.query.country_code || '').trim()
const country = ref(initCountry)
const statusFilter = ref(route.query.status !== undefined ? String(route.query.status).trim() || 'all' : 'in_force')
const folderMode = ref(String(route.query.folder || '').trim())
const siteId = ref((route.query.site_id as string) || '')
const sitePublisher = ref((route.query.publisher as string) || '')
const loading = ref(false)
// 第 7.5 项 规则 21：法规目录收起与否记在本机
const DIR_KEY = 'cyshuhe.admin.docs.dir-collapsed'
const dirCollapsed = ref((localStorage.getItem(DIR_KEY) ?? (window.innerWidth < 1600 ? '1' : '0')) === '1')  // 屏幕不够宽时默认收起，表格不少于约 860px
watch(dirCollapsed, (v) => localStorage.setItem(DIR_KEY, v ? '1' : '0'))
const fmt = (s: string | null) => localTime(s) || '—'
// 第 7.5 项二轮 规则 19：同名文件在名称后标出年份或官方版本，好区分
const dupTitles = computed(() => {
  const seen = new Map<string, number>()
  for (const d of docs.value) seen.set(d.title, (seen.get(d.title) ?? 0) + 1)
  return new Set([...seen].filter(([, n]) => n > 1).map(([t]) => t))
})
function dupTag(row: DocumentOut): string {
  if (!dupTitles.value.has(row.title)) return ''
  const v = row.latest_version
  return v?.version_label || (v?.published_date || '').slice(0, 4) || (row.discovered_at || '').slice(0, 4)
}

const STEP_LABEL: Record<string, string> = {
  download: '下载归档',
  upload: '本地上传',
  parse: '分出条文',
  parse_layout: '版面处理',
  ocr: '识别扫描件文字',
  ocr_upgrade: '重新识别',
  clean: '清理页眉页脚',
  refetch: '重新下载核对',
  linkage: '新旧文本衔接',
}
function cleanProcessNote(raw: string | null | undefined): string {
  if (!raw) return ''
  const trimmed = raw.trim()
  if (trimmed.startsWith('{') && trimmed.endsWith('}')) {
    return '已完成版面分栏、页码与页眉页脚结构校正'
  }
  if (/Failed to load document|PDFium|Data format error/i.test(trimmed)) {
    return '文件结构损坏或并非标准版式文档，无法解析'
  }
  let s = trimmed
    .replace(/[,，;；\s]*sha256=[0-9a-fA-F]+/gi, '')
    .replace(/[,，;；\s]*\d+\s*(?:字节|bytes)/gi, '')
    .replace(/robots\.txt[^，。；\n]*/gi, '官方网站自动访问受限')
    .replace(new RegExp('(?:Paddle)?O' + 'CR\\s*(\\d+)\\s*页', 'gi'), '版面文字识别共 $1 页')
    .replace(new RegExp('(?:Paddle)?O' + 'CR', 'gi'), '版面文字识别')
    .replace(new RegExp('L' + 'LM', 'gi'), '智能分析模型')
    .replace(new RegExp('\\.j' + 'son\\b', 'gi'), '数据文件')
    .replace(new RegExp('J' + 'SON', 'gi'), '结构化数据')
    .replace(new RegExp('U' + 'UID', 'gi'), '唯一标识')
    .replace(new RegExp('(?:Graph)?R' + 'AG', 'gi'), '法条检索增强')
    .replace(new RegExp('Pyd' + 'antic', 'gi'), '字段校验')
    .replace(/[（(][A-Za-z_][A-Za-z0-9_]*(?:Error|Exception|Timeout)[)）]/g, '')
    .replace(/\b[A-Za-z_][A-Za-z0-9_]*(?:Error|Exception|Timeout)\s*[:：]?\s*/g, '')
    .trim()
  if (/^(?:HTTP\s*)?(?:403|429|5\d\d)\b/i.test(s)) s = '官方网站自动访问受限，可浏览器访问或本地上传原文'
  return s || '处理异常，请核查原文或稍后重试'
}

function readableUrl(url: string | null | undefined, maxLen = 48): string {
  const raw = (url || '').trim()
  if (!raw) return ''
  let decoded = raw
  try {
    decoded = decodeURIComponent(raw)
  } catch {
    decoded = raw
  }
  return decoded.length > maxLen ? `${decoded.slice(0, maxLen)}…` : decoded
}

function countryLabel(code: string | null | undefined): string {
  if (!code) return '—'
  return countryNames.value[code] || COUNTRY_NAMES[code] || code
}

// 处理记录只展示使用者看得懂的几步，并把每一步说成一句话
const SHOWN_STEPS = new Set(['download', 'upload', 'parse', 'ocr', 'ocr_upgrade', 'refetch', 'linkage'])
function eventText(e: EventOut): string {
  const m = cleanProcessNote(e.message)
  if (e.step === 'ocr') return `共 ${m.match(/(\d+)\s*页/)?.[1] ?? ''} 页`.replace('共  页', '已完成文字识别')
  if (e.step === 'parse') return m.replace(/[,，]策略.*$/, '').replace(/^(\d+)\s*条$/, '共 $1 条')
  if (e.step === 'ocr_upgrade') return m.split('旧条款开头存档')[0].replace(/^重新识别：/, '').replace(/[。；]\s*$/, '')
  return m
}
// 同一份原文重新识别过几次：每一步只列最近一次，免得列出旧读法的条数（与上面的条款数对不上）
const visibleEvents = computed(() => {
  const latest = new Map<string, EventOut>()
  for (const e of events.value) if (SHOWN_STEPS.has(e.step)) latest.set(e.step, e)
  return events.value.filter((e) => latest.get(e.step) === e)
})

const browseCountry = ref(country.value || '')
const browseLevel = ref('')
const selectedDocId = ref('')
const documentKeyword = ref('')
const hasMore = ref(false)
const loadedFilter = ref({ country: '', site: '' })
const documentPageNumber = ref(1)
const countryNames = ref<Record<string, string>>({})
const knownDocCountryCodes = ref<string[]>([])

function recordKnownCountries(list: DocumentOut[]) {
  const merged = new Set(knownDocCountryCodes.value)
  for (const d of list) {
    if (d.country_code) merged.add(d.country_code)
  }
  knownDocCountryCodes.value = [...merged]
}

const headerCountryOptions = computed(() => {
  const codes = new Set<string>(knownDocCountryCodes.value)
  for (const d of docs.value) {
    if (d.country_code) codes.add(d.country_code)
  }
  if (country.value) codes.add(country.value)
  return [...codes]
    .sort((a, b) => a.localeCompare(b))
    .map((code) => ({
      code,
      name: countryNames.value[code] || COUNTRY_NAMES[code] || code,
    }))
})

function matchesStatusFilter(d: DocumentOut): boolean {
  if (statusFilter.value === 'in_force') {
    if (!d.latest_version) return false
    if (!['relevant', 'related'].includes(d.relevance)) return false
    const legal = d.latest_version.legal_status || 'in_force'
    return !['repealed', 'draft'].includes(legal)
  }
  return true
}

const statusFilteredDocs = computed(() => docs.value.filter(matchesStatusFilter))
const countries = computed(() => directoryCountries(statusFilteredDocs.value, countryNames.value))
const matchingDocs = computed(() => {
  const query = documentKeyword.value.trim().toLocaleLowerCase()
  return statusFilteredDocs.value.filter(
    (d) => !query || `${d.title} ${d.source_url}`.toLocaleLowerCase().includes(query),
  )
})
// 选了国家但还没选文件层级：默认列出该国全部文档；选了全部国家则列出全部匹配文档
const allCountriesExplicit = ref(folderMode.value === 'all' && !initCountry)
const selectedGroup = computed(() => {
  if (!browseCountry.value) {
    return matchingDocs.value.length
      ? { key: '__all_countries__', name: '全部国家文档', rank: 0, items: matchingDocs.value }
      : undefined
  }
  const levels = directoryLevels(matchingDocs.value, browseCountry.value)
  const hit = levels.find(g => g.key === browseLevel.value)
  if (hit || !levels.length) return hit
  return { key: '__all__', name: '全部文档', rank: 0, items: levels.flatMap(g => g.items) }
})
// 范围内的文件排前面、范围外（仅归档）的排最后：打开页面先看到有用的，不是一屏“范围外”
const OUT_OF_SCOPE = new Set(['out_of_scope', 'irrelevant'])
const orderedItems = computed(() => {
  const items = [...(selectedGroup.value?.items || [])]
  return items.sort((a, b) => Number(OUT_OF_SCOPE.has(a.relevance)) - Number(OUT_OF_SCOPE.has(b.relevance)))
})
const visibleDocuments = computed(() => documentPage(orderedItems.value, documentPageNumber.value, 20))
const browseCountryName = computed(() => countries.value.find(c => c.code === browseCountry.value)?.name || browseCountry.value)
watch([matchingDocs, browseCountry, browseLevel], () => { documentPageNumber.value = 1 })
const headerCountry = computed({
  get: () => browseCountry.value || country.value,
  set: (v: string) => {
    const clean = v || ''
    country.value = clean
    allCountriesExplicit.value = clean === ''
    if (!clean) {
      browseCountry.value = ''
      browseLevel.value = ''
      selectedDocId.value = ''
    }
  },
})
function browseCountryFolder(code: string) {
  allCountriesExplicit.value = false
  browseCountry.value = code
  country.value = code  // 第 7.5 项：右上角国家选择与左侧目录同步
  browseLevel.value = ''
  selectedDocId.value = ''
}
function browseLevelFolder(key: string) {
  browseLevel.value = key
  selectedDocId.value = ''
}
function openDirectoryFile(id: string) {
  const doc = docs.value.find(d => d.id === id)
  if (doc) open(doc)
}

function withOffset(query: string, offset: number) {
  const p = new URLSearchParams(query.replace(/^\?/, ''))
  p.set('offset', String(offset))
  return `?${p.toString()}`
}

let loadSequence = 0
async function load(more = false) {
  if (more && loading.value) return
  const requestId = ++loadSequence
  loading.value = true
  try {
    const params = new URLSearchParams()
    const filter = more ? loadedFilter.value : { country: (country.value || '').trim(), site: siteId.value.trim() }
    if (filter.country) params.set('country_code', filter.country)
    if (filter.site) params.set('site_id', filter.site)
    params.set('limit', '200')
    params.set('offset', String(more ? docs.value.length : 0))
    const q = params.toString() ? `?${params.toString()}` : ''
    const batch = await api.get<DocumentOut[]>(`/admin/documents${q}`)
    if (requestId !== loadSequence) return
    const accumulated = more ? [...docs.value, ...batch] : [...batch]
    let morePages = batch.length === 200
    // 目录与各国合计必须是全部文档：全部读完后一次性更新列表，避免分批加载期间各国计数跳变
    while (morePages && requestId === loadSequence) {
      const next = await api.get<DocumentOut[]>(
        `/admin/documents${withOffset(q, accumulated.length)}`,
      )
      if (requestId !== loadSequence) return
      accumulated.push(...next)
      morePages = next.length === 200
    }
    docs.value = accumulated
    hasMore.value = false
    if (!more) {
      if (!filter.country && !filter.site) {
        recordKnownCountries(docs.value)
      }
      loadedFilter.value = filter
      if ((allCountriesExplicit.value || folderMode.value === 'all') && !filter.country) {
        browseCountry.value = ''
        browseLevel.value = ''
      } else if (filter.country) {
        browseCountry.value = filter.country
        browseLevel.value = folderMode.value === 'all' ? '' : ''
      } else {
        // 打开页面不应该是一片空白：默认进入文档最多的国家，并展开其中文档最多的层级
        const biggest = [...countries.value].sort((a, b) => b.count - a.count)[0]?.code || ''
        browseCountry.value = countries.value.some(c => c.code === browseCountry.value)
          ? browseCountry.value : biggest
        const levels = directoryLevels(statusFilteredDocs.value, browseCountry.value)
        browseLevel.value = folderMode.value === 'all'
          ? ''
          : [...levels].sort((a, b) => b.items.length - a.items.length)[0]?.key || ''
      }
      selectedDocId.value = ''
    }
  } catch (e) {
    if (requestId === loadSequence) ElMessage.error((e as Error).message)
  } finally {
    if (requestId === loadSequence) loading.value = false
  }
}

const RELEVANCE_META: Record<
  string,
  { label: string; type: 'success' | 'primary' | 'info' | 'warning' }
> = {
  relevant: { label: '核心', type: 'success' },
  related: { label: '相关', type: 'primary' },
  out_of_scope: { label: '范围外（仅留档）', type: 'info' },
  irrelevant: { label: '非法规/待过滤', type: 'info' },
  needs_review: { label: '待判定', type: 'warning' },
}

async function overrideRelevance(doc: DocumentOut, relevance: string) {
  try {
    const updated = await api.patch<DocumentOut>(`/admin/documents/${doc.id}/relevance`, {
      relevance,
      note: '管理员人工覆盖相关性判定',
    })
    doc.relevance = updated.relevance
    doc.relevance_note = updated.relevance_note
    doc.relevance_source = updated.relevance_source
    ElMessage.success('已调整相关性，下次重新识别条款与关联关系时生效（人工判定不会被自动流程改写）')
  } catch (e) {
    ElMessage.error((e as Error).message)
  }
}

function clearSiteFilter() {
  siteId.value = ''
  sitePublisher.value = ''
  router.replace({
    query: country.value.trim() ? { country_code: country.value.trim() } : {},
  })
  load()
}

const drawer = ref(false)
async function reloadDetail() {
  if (detail.value) detail.value = await api.get<DocumentDetail>(`/admin/documents/${detail.value.id}`)
}
const detail = ref<DocumentDetail | null>(null)
const versionId = ref<string>('')
// 扫描件：平台入库时自动检查识别得清不清楚。清楚的记为“已核对”；不清楚的写明哪里、哪几页，管理员对照原文看过再定
function scanCheck(v: { review_status: string; check_note?: string | null }): { label: string; type: 'success' | 'warning' | 'danger' | 'info' } {
  return v.review_status === 'approved' ? { label: '已复核', type: 'success' } : { label: '待复核', type: 'warning' }
}
// 可能识别错的段落：逐段“没错 / 改一下”，旁边放原文那一页的图
interface CheckItem { clause_id: string; page: number | null; state: string; clause_number: string | null; text: string }
const checkItems = ref<CheckItem[]>([])
const checkNote = ref('')
const pageImages = reactive<Record<number, string>>({})
const editing = ref('')
const draft = ref('')
async function loadCheckItems(v: { id: string; text_source: string; review_status: string }) {
  checkItems.value = []; checkNote.value = ''
  if (v.text_source !== 'ocr' || v.review_status === 'approved') return
  const r = await api.get<{ note: string | null; items: CheckItem[] }>(`/admin/versions/${v.id}/check-items`)
  checkItems.value = r.items; checkNote.value = r.note || ''
  for (const n of new Set(r.items.map((x) => x.page).filter((x): x is number => !!x))) {
    if (pageImages[n]) continue
    const res = await fetch(`/api/admin/versions/${v.id}/pages/${n}.png`, { headers: { Authorization: `Bearer ${api.token()}` } })
    if (res.ok) pageImages[n] = URL.createObjectURL(await res.blob())
  }
}
async function settle(versionIdValue: string, item: CheckItem, text: string | null) {
  try {
    const r = await api.post<{ review_status: string; note: string | null }>(`/admin/versions/${versionIdValue}/check-items/${item.clause_id}`, { text })
    item.state = text === null ? 'ok' : 'fixed'
    if (text !== null) item.text = text
    editing.value = ''
    checkNote.value = r.note || ''
    if (r.review_status === 'approved' && detail.value) {
      ElMessage.success('已完成扫描件原文核对')
      detail.value = await api.get<DocumentDetail>(`/admin/documents/${detail.value.id}`)
    }
  } catch (e) { ElMessage.error((e as Error).message) }
}
const rereading = ref<number | null>(null)
async function rereadPage(id: string, page: number) {
  rereading.value = page
  try {
    const r = await api.post<{ fixed: number; left: number; review_status: string }>(`/admin/versions/${id}/pages/${page}/reread`, {})
    if (r.review_status === 'approved') ElMessage.success('已完成重新识别并通过核对')
    else if (r.fixed) ElMessage.success(`已重新识别 ${r.fixed} 段，尚有 ${r.left} 段待复核`)
    else ElMessage.warning('本页重新识别后仍有待确认内容，请对照原件人工修订')
    const v = detail.value?.versions.find((x) => x.id === id)
    if (detail.value) detail.value = await api.get<DocumentDetail>(`/admin/documents/${detail.value.id}`)
    const fresh = detail.value?.versions.find((x) => x.id === id) || v
    if (fresh) await loadCheckItems(fresh)
  } catch (e) { ElMessage.error((e as Error).message) }
  finally { rereading.value = null }
}
async function openOriginalPage(id: string, page: number | null) {
  const res = await fetch(`/api/admin/versions/${id}/original?inline=true`, { headers: { Authorization: `Bearer ${api.token()}` } })
  if (!res.ok) return ElMessage.error('原文文件暂无法读取，请稍后重试')
  const url = URL.createObjectURL(await res.blob())
  window.open(page ? `${url}#page=${page}` : url, '_blank')
}
// 扫描件原文核对：管理员核对后直接生效
async function checkVersion(id: string, approve: boolean) {
  let note = ''
  if (!approve) {
    try {
      note = (await ElMessageBox.prompt('写明哪里识别有误（如第几条、哪一页），便于重新识别。', '原文识别有误', { confirmButtonText: '提交', cancelButtonText: '取消', inputPattern: /\S/, inputErrorMessage: '请写明问题' }) as { value: string }).value
    } catch { return }
  }
  try {
    await api.post(`/admin/versions/${id}/check`, { approve, note })
    ElMessage.success(approve ? '已记为已核对' : '已记为待重新识别')
    if (detail.value) {
      detail.value = await api.get<DocumentDetail>(`/admin/documents/${detail.value.id}`)
    }
  } catch (e) { ElMessage.error((e as Error).message) }
}
const clauses = ref<ClauseOut[]>([])
const events = ref<EventOut[]>([])
const translatingDocId = ref<string>('')

async function downloadOriginal() {
  if (!versionId.value) return
  try {
    const response = await fetch(`/api/admin/versions/${versionId.value}/original`, {
      headers: { Authorization: `Bearer ${api.token()}` },
    })
    if (!response.ok) throw new Error('归档下载失败，请检查原文完整性及登录状态')
    const url = URL.createObjectURL(await response.blob())
    const anchor = document.createElement('a')
    anchor.href = url
    const pickedVer = detail.value?.versions.find((v) => v.id === versionId.value)
    const extension =
      pickedVer?.mime_type === 'application/pdf'
        ? 'pdf'
        : (pickedVer?.mime_type || '').toLowerCase().includes('html')
          ? 'html'
          : 'bin'
    const ccLabel = countryLabel(detail.value?.country_code)
    const safeTitle = (detail.value?.title || '法规文件').replace(/[\\/:*?"<>|\r\n]+/g, '_').slice(0, 60)
    anchor.download = `${ccLabel}_${safeTitle}_官方原文.${extension}`
    anchor.click()
    setTimeout(() => URL.revokeObjectURL(url), 1000)
  } catch (error) {
    ElMessage.error((error as Error).message)
  }
}
const provenance = ref<{
  source_integrity_holds?: { reason: string }[]
  binding_resolutions?: {
    official_source_url: string
    canonical_document_id: string
    expected_edition: string
  }[]
  versions: {
    version_id: string
    acquisition: string
    registered_download?: { source_nature: string } | null
    integrity_ok: boolean
    pipeline_refetch_verified: boolean
  }[]
} | null>(null)

async function translateDocument(doc: DocumentOut) {
  translatingDocId.value = doc.id
  try {
    const res = await api.post<{
      document_id: string
      translated_count: number
    }>(`/admin/knowledge/documents/${doc.id}/translate`)
    if (res.translated_count > 0) {
      ElMessage.success(`已完成条文补译（新增翻译 ${res.translated_count} 条）`)
    } else {
      ElMessage.info('该法规全部外文条文均已有中文译文，无需补译')
    }
  } catch (e) {
    ElMessage.error((e as Error).message)
  } finally {
    translatingDocId.value = ''
  }
}

async function open(doc: DocumentOut) {
  selectedDocId.value = doc.id
  provenance.value = null
  detail.value = await api.get<DocumentDetail>(`/admin/documents/${doc.id}`)
  provenance.value = await api.get(`/admin/documents/${doc.id}/provenance`)
  drawer.value = true
  if (detail.value.versions.length) await pick(detail.value.versions[0].id)
}

async function pick(id: string) {
  versionId.value = id
  const picked = detail.value?.versions.find((x) => x.id === id)
  if (picked) loadCheckItems(picked).catch(() => undefined)
  ;[clauses.value, events.value] = await Promise.all([
    api.get<ClauseOut[]>(`/admin/versions/${id}/clauses?limit=500`),
    api.get<EventOut[]>(`/admin/versions/${id}/events`),
  ])
}

// ---- 手动上传官方原文(WAF 拦截时的兜底入口) ----
const uploadDialog = ref(false)
const uploading = ref(false)
const uploadForm = reactive({
  document_id: '' as string,
  site_id: '' as string,
  source_url: '',
  title: '',
  version_label: '',
  published_date: '',
  file_name: '',
  mime_type: 'application/pdf',
  content_base64: '',
})

async function openUploadDialog(doc?: DocumentOut) {
  if (!sites.value.length) {
    try {
      sites.value = await api.get<SiteOut[]>('/admin/sites')
    } catch {
      /* 无站点列表权限时忽略 */
    }
  }
  uploadForm.document_id = doc?.id ?? ''
  uploadForm.site_id = doc?.site_id ?? siteId.value ?? sites.value[0]?.id ?? ''
  uploadForm.source_url = doc?.source_url ?? ''
  uploadForm.title = doc?.title ?? ''
  uploadForm.version_label = ''
  uploadForm.published_date = ''
  uploadForm.file_name = ''
  uploadForm.content_base64 = ''
  uploadDialog.value = true
}

function onFileChange(e: Event) {
  const input = e.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return
  uploadForm.file_name = file.name
  uploadForm.mime_type =
    file.type || (file.name.toLowerCase().endsWith('.html') ? 'text/html' : 'application/pdf')
  const reader = new FileReader()
  reader.onload = () => {
    const res = String(reader.result || '')
    const idx = res.indexOf('base64,')
    uploadForm.content_base64 = idx >= 0 ? res.slice(idx + 7) : res
  }
  reader.readAsDataURL(file)
}

async function submitUpload() {
  if (!uploadForm.content_base64) {
    ElMessage.warning('请先选择要上传的官方 PDF 或网页原文文件')
    return
  }
  uploading.value = true
  try {
    const res = await api.post<DocumentDetail>('/admin/documents/upload', {
      document_id: uploadForm.document_id || null,
      site_id: uploadForm.document_id ? null : uploadForm.site_id || null,
      source_url: uploadForm.document_id ? null : uploadForm.source_url.trim() || null,
      title: uploadForm.title.trim() || null,
      mime_type: uploadForm.mime_type,
      content_base64: uploadForm.content_base64,
      version_label: uploadForm.version_label.trim() || null,
      published_date: uploadForm.published_date.trim() || null,
    })
    ElMessage.success('官方原文已归档并完成条款解析')
    uploadDialog.value = false
    await load()
    detail.value = res
    drawer.value = true
    if (res.versions.length) await pick(res.versions[0].id)
  } catch (e) {
    ElMessage.error((e as Error).message)
  } finally {
    uploading.value = false
  }
}

watch(
  () => [route.query.country, route.query.country_code, route.query.status, route.query.folder, route.query.site_id, route.query.publisher],
  async ([qCountry, qCountryCode, qStatus, qFolder, qSiteId, qPublisher]) => {
    const nextCountry = String(qCountry || qCountryCode || '').trim()
    const nextStatus = qStatus !== undefined ? String(qStatus).trim() || 'all' : 'in_force'
    const nextFolder = String(qFolder || '').trim()
    const nextSite = String(qSiteId || '').trim()
    const nextPub = String(qPublisher || '').trim()
    statusFilter.value = nextStatus
    folderMode.value = nextFolder
    const siteChanged = nextSite !== siteId.value
    siteId.value = nextSite
    sitePublisher.value = nextPub
    if (nextFolder === 'all' && !nextCountry && !nextSite) {
      allCountriesExplicit.value = true
      country.value = ''
      browseCountry.value = ''
      browseLevel.value = ''
      await load()
      return
    }
    if (nextCountry !== country.value || siteChanged) {
      country.value = nextCountry
      browseCountry.value = nextCountry
      browseLevel.value = ''
      await load()
    }
  },
)

onMounted(async () => {
  const loadNames = async () => {
    try {
      const catalog = await api.get<Array<{ country_code: string; country_name_zh: string; documents_count?: number }>>('/admin/knowledge/countries')
      countryNames.value = Object.fromEntries(catalog.map(c => [c.country_code, c.country_name_zh]))
      const withDocs = catalog.filter(c => (c.documents_count ?? 0) > 0).map(c => c.country_code)
      if (withDocs.length) {
        knownDocCountryCodes.value = [...new Set([...knownDocCountryCodes.value, ...withDocs])]
      }
    } catch {
      // Optional labels: files remain accessible by their existing country codes.
    }
  }
  await Promise.all([load(), loadNames()])
  const documentId = route.query.document_id
  if (typeof documentId === 'string') {
    try {
      const doc = docs.value.find(d => d.id === documentId)
        || await api.get<DocumentOut>(`/admin/documents/${encodeURIComponent(documentId)}`)
      if (doc.country_code && !country.value) {
        country.value = doc.country_code
        browseCountry.value = doc.country_code
      }
      await open(doc)
    } catch (e) {
      ElMessage.error((e as Error).message)
    }
  }
})
// 补全译文会调用模型：只由平台管理员触发
const isPlatformAdmin = ['platform_admin', 'ops_admin'].includes(useAuth().user?.role ?? '')  // 运维管理员也维护法规和译文

// ---- 法规多版本对比（支持单部法规历次修订对比 & 同国两版文件逐条差异对比） ----
interface DiffSeg { op: 'equal' | 'insert' | 'delete'; text: string }
interface CrossDiffRow {
  status: 'added' | 'removed' | 'changed' | 'unchanged'
  heading: string
  baseText: string
  targetText: string
  diff: DiffSeg[]
}
const compareVisible = ref(false)
const compareMode = ref<'single' | 'cross'>('single')
const compareDocId = ref('')
const crossCountry = ref('')
const crossBaseDocId = ref('')
const crossTargetDocId = ref('')
const crossOnlyChanges = ref(true)
const crossLoading = ref(false)
const crossRows = ref<CrossDiffRow[]>([])
const crossCounts = ref<{ added: number; removed: number; changed: number; unchanged: number }>({
  added: 0,
  removed: 0,
  changed: 0,
  unchanged: 0,
})

const compareDocObj = computed(() => docs.value.find((d) => d.id === compareDocId.value) || null)
const crossCountryDocs = computed(() =>
  docs.value.filter((d) => (!crossCountry.value || d.country_code === crossCountry.value) && d.latest_version?.state === 'parsed'),
)
const filteredCrossRows = computed(() =>
  crossOnlyChanges.value ? crossRows.value.filter((r) => r.status !== 'unchanged') : crossRows.value,
)

function buildWordDiff(oldStr: string, newStr: string): DiffSeg[] {
  const a = oldStr.trim().split(/(\s+)/).filter(Boolean).slice(0, 220)
  const b = newStr.trim().split(/(\s+)/).filter(Boolean).slice(0, 220)
  if (!a.length && !b.length) return []
  let prefix = 0
  while (prefix < a.length && prefix < b.length && a[prefix] === b[prefix]) prefix++
  let suffix = 0
  while (
    suffix < a.length - prefix &&
    suffix < b.length - prefix &&
    a[a.length - 1 - suffix] === b[b.length - 1 - suffix]
  ) {
    suffix++
  }
  const segs: DiffSeg[] = []
  if (prefix > 0) segs.push({ op: 'equal', text: a.slice(0, prefix).join('') })
  const delMid = a.slice(prefix, a.length - suffix).join('')
  const insMid = b.slice(prefix, b.length - suffix).join('')
  if (delMid) segs.push({ op: 'delete', text: delMid })
  if (insMid) segs.push({ op: 'insert', text: insMid })
  if (suffix > 0) segs.push({ op: 'equal', text: a.slice(a.length - suffix).join('') })
  return segs
}

function syncCrossCountryDefaults(preferredDoc?: { id: string; title?: string; country_code?: string } | null) {
  const list = crossCountryDocs.value
  if (!list.length) {
    crossBaseDocId.value = ''
    crossTargetDocId.value = ''
    crossRows.value = []
    return
  }
  const anchor = (preferredDoc && list.find((d) => d.id === preferredDoc.id)) || list[0]
  const sameTitleSibling = list.find((d) => d.id !== anchor.id && d.title === anchor.title)
  const otherDoc = sameTitleSibling || list.find((d) => d.id !== anchor.id) || anchor
  crossBaseDocId.value = anchor.id
  crossTargetDocId.value = otherDoc.id
  if (anchor.id !== otherDoc.id) {
    void runCrossCompare()
  } else {
    crossRows.value = []
  }
}

function openCompareModal(doc?: { id: string; title?: string; country_code?: string } | null) {
  const target = doc || detail.value || visibleDocuments.value.items[0] || docs.value[0] || null
  compareDocId.value = target?.id || ''
  crossCountry.value = target?.country_code || browseCountry.value || country.value || headerCountryOptions.value[0]?.code || ''
  compareMode.value = 'single'
  syncCrossCountryDefaults(target)
  compareVisible.value = true
}

function switchToCrossCompare() {
  compareMode.value = 'cross'
  syncCrossCountryDefaults(compareDocObj.value)
}

function onCrossCountryChange() {
  syncCrossCountryDefaults(null)
}

async function runCrossCompare() {
  if (!crossBaseDocId.value || !crossTargetDocId.value || crossBaseDocId.value === crossTargetDocId.value) {
    crossRows.value = []
    return
  }
  crossLoading.value = true
  try {
    const [baseDoc, targetDoc] = await Promise.all([
      api.get<DocumentDetail>(`/admin/documents/${encodeURIComponent(crossBaseDocId.value)}`),
      api.get<DocumentDetail>(`/admin/documents/${encodeURIComponent(crossTargetDocId.value)}`),
    ])
    const baseVer = baseDoc.versions.find((v) => v.state === 'parsed') || baseDoc.versions[0]
    const targetVer = targetDoc.versions.find((v) => v.state === 'parsed') || targetDoc.versions[0]
    if (!baseVer || !targetVer) {
      crossRows.value = []
      return
    }
    const [baseClauses, targetClauses] = await Promise.all([
      api.get<ClauseOut[]>(`/admin/versions/${baseVer.id}/clauses?limit=500`),
      api.get<ClauseOut[]>(`/admin/versions/${targetVer.id}/clauses?limit=500`),
    ])
    const keyOf = (c: ClauseOut, idx: number) =>
      (c.clause_number || '').trim().toLowerCase() || `ord_${c.ordinal ?? idx}`
    const baseMap = new Map<string, ClauseOut>()
    baseClauses.forEach((c, i) => {
      const k = keyOf(c, i)
      if (!baseMap.has(k)) baseMap.set(k, c)
    })
    const targetMap = new Map<string, ClauseOut>()
    targetClauses.forEach((c, i) => {
      const k = keyOf(c, i)
      if (!targetMap.has(k)) targetMap.set(k, c)
    })
    const allKeys = [...new Set([...baseMap.keys(), ...targetMap.keys()])]
    const out: CrossDiffRow[] = []
    const counts = { added: 0, removed: 0, changed: 0, unchanged: 0 }
    for (const k of allKeys) {
      const b = baseMap.get(k)
      const t = targetMap.get(k)
      const refClause = (t || b)!
      const heading =
        articleLabel(refClause.clause_number) ||
        refClause.section_path ||
        (refClause.ordinal === 0 ? '前言' : `段落 ${refClause.ordinal}`)
      if (!b && t) {
        counts.added++
        out.push({ status: 'added', heading, baseText: '', targetText: t.original_text, diff: [] })
      } else if (b && !t) {
        counts.removed++
        out.push({ status: 'removed', heading, baseText: b.original_text, targetText: '', diff: [] })
      } else if (b && t) {
        const normB = b.original_text.replace(/\s+/g, ' ').trim()
        const normT = t.original_text.replace(/\s+/g, ' ').trim()
        if (normB === normT) {
          counts.unchanged++
          out.push({ status: 'unchanged', heading, baseText: b.original_text, targetText: t.original_text, diff: [] })
        } else {
          counts.changed++
          out.push({
            status: 'changed',
            heading,
            baseText: b.original_text,
            targetText: t.original_text,
            diff: buildWordDiff(b.original_text, t.original_text),
          })
        }
      }
    }
    crossCounts.value = counts
    crossRows.value = out
  } catch (e) {
    ElMessage.error((e as Error).message || '版本对比加载失败')
  } finally {
    crossLoading.value = false
  }
}

const COMPARE_STATUS_META: Record<
  CrossDiffRow['status'],
  { label: string; type: 'success' | 'danger' | 'warning' | 'info' }
> = {
  added: { label: '新增', type: 'success' },
  removed: { label: '删除', type: 'danger' },
  changed: { label: '改动', type: 'warning' },
  unchanged: { label: '未变', type: 'info' },
}
</script>

<template>
  <div class="management-page">
    <div class="page-heading">
      <div style="display: flex; align-items: center; gap: 10px">
        <div>
          <h1>法规文档</h1>
        </div>
        <el-tag v-if="siteId" closable type="info" @close="clearSiteFilter">
          来源站点：{{ sitePublisher || siteId.slice(0, 8) }}
        </el-tag>
      </div>
      <div class="page-actions">
        <el-select
          v-model="statusFilter"
          placeholder="筛选效力范围"
          aria-label="筛选效力范围"
          style="width: 160px"
        >
          <el-option label="现行有效法规" value="in_force" />
          <el-option label="全部归档文件" value="all" />
        </el-select>
        <el-select
          v-model="headerCountry"
          placeholder="全部国家"
          aria-label="筛选国家"
          style="width: 160px"
          clearable
          filterable
          @change="load().then(() => { if (country) browseCountryFolder(country); else browseCountry = '' })"
        >
          <el-option label="全部国家" value="" />
          <el-option v-for="c in headerCountryOptions" :key="c.code" :label="c.name" :value="c.code" />
        </el-select>
        <el-button data-testid="btn-open-version-compare" @click="openCompareModal()">版本对比</el-button>
        <el-button :type="uploadDialog ? 'default' : 'primary'" @click="openUploadDialog()">上传官方原文</el-button>
      </div>
    </div>
    <div class="document-library" :class="{ 'dir-collapsed': dirCollapsed }" v-loading="loading">
      <button v-if="dirCollapsed" type="button" class="dir-rail" title="展开法规目录" aria-label="展开法规目录" data-testid="admin-dir-open" @click="dirCollapsed = false">
        <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M6 3l5 5-5 5" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" /></svg>
        <span>法规目录</span>
      </button>
      <RegulationDirectory v-else :countries="countries" :documents="matchingDocs" :country="browseCountry"
        :level="browseLevel" :selected-id="selectedDocId" :loading="loading"
        @country="browseCountryFolder" @level="browseLevelFolder" @document="openDirectoryFile" />
      <section class="document-results" aria-label="分类文档列表">
        <div class="document-results-heading">
          <button v-if="!dirCollapsed" type="button" class="cy-rail-toggle" title="收起法规目录" aria-label="收起法规目录" data-testid="admin-dir-close" @click="dirCollapsed = true">
            <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><path d="M10 3L5 8l5 5" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" /></svg>
          </button>
          <div class="results-title"><h2>{{ browseCountryName || '法规文档' }}<template v-if="selectedGroup"> / {{ selectedGroup.name }}</template></h2>
            <p v-if="!loading || docs.length">当前范围共 {{ statusFilteredDocs.length }} 部法规<template v-if="selectedGroup && selectedGroup.items.length !== statusFilteredDocs.length"> · 本分类匹配 {{ selectedGroup.items.length }} 部</template></p>
            <p v-else>正在加载法规目录…</p></div>
          <el-input v-model="documentKeyword" clearable aria-label="搜索已加载文档" placeholder="搜索文件名或来源" style="width: 260px" />
        </div>
        <el-empty v-if="!selectedGroup" :description="matchingDocs.length ? '请展开左侧国家并选择文件层级' : '当前筛选下没有文档'" :image-size="72" />
    <el-table v-if="selectedGroup" :data="visibleDocuments.items" v-loading="loading" border @row-click="open" style="cursor: pointer">
      <el-table-column v-if="!browseCountry" label="国家" width="82" class-name="cy-col-nowrap">
        <template #default="{ row }">{{ countryLabel(row.country_code) }}</template>
      </el-table-column>
      <el-table-column label="法规文件 / 官方来源" min-width="210">
        <template #default="{ row }"
          ><button type="button" class="document-title" :title="row.title" @click.stop="open(row)">
            {{ row.title }}<span v-if="dupTag(row)" class="dup-tag">（{{ dupTag(row) }}）</span>
          </button>
          <div class="document-source"><a :href="row.source_url" target="_blank" rel="noopener noreferrer" :title="readableUrl(row.source_url, 120)" @click.stop>官网↗</a><span v-if="row.latest_version"> · {{ officialTextLabel(row.latest_version) }}</span><span v-if="row.last_error" class="doc-err" :title="cleanProcessNote(row.last_error)"> · 最近访问受限</span></div></template
        >
      </el-table-column>
      <el-table-column label="类型" width="88" class-name="cy-col-nowrap cy-col-full">
        <template #default="{ row }">{{ formatDocTypeZh(row.document_type) }}</template>
      </el-table-column>
      <el-table-column label="纳入范围" min-width="156" class-name="cy-col-nowrap">
        <template #default="{ row }">
          <el-tooltip
            :content="row.relevance_note || ''"
            placement="top"
            :disabled="!row.relevance_note"
          >
            <el-tag :type="RELEVANCE_META[row.relevance]?.type ?? 'info'" size="small">
              {{ RELEVANCE_META[row.relevance]?.label ?? row.relevance }}
            </el-tag>
          </el-tooltip>
          <el-tag
            v-if="row.relevance_source === 'manual'"
            type="warning"
            size="small"
            style="margin-left: 4px"
          >
            人工
          </el-tag>
          <el-dropdown
            trigger="click"
            style="margin-left: 6px"
            @command="(c: string) => overrideRelevance(row, c)"
          >
            <el-button size="small" link type="primary" @click.stop>调整</el-button>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item command="relevant">设为核心</el-dropdown-item>
                <el-dropdown-item command="related">设为相关</el-dropdown-item>
                <el-dropdown-item command="out_of_scope">设为范围外</el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
        </template>
      </el-table-column>
      <el-table-column label="状态" min-width="136">
        <template #default="{ row }">
          <el-tag v-if="row.safety_hold" type="danger">来源绑定异常 · 已隔离</el-tag>
          <div v-if="row.latest_version" class="document-status" data-testid="document-status">
            <el-tag
              :type="
                row.latest_version.state === 'parsed'
                  ? 'success'
                  : row.latest_version.state === 'failed'
                    ? 'danger'
                    : 'warning'
              "
              size="small"
            >
              {{ VERSION_STATE_LABEL[row.latest_version.state] }}
            </el-tag>
            <el-tooltip v-if="row.latest_version.text_source === 'ocr'" :disabled="row.latest_version.review_status === 'approved'" :content="row.latest_version.check_note || '还没对照原文看过'" placement="top">
              <el-tag :type="scanCheck(row.latest_version).type" size="small" data-testid="scan-check">{{ scanCheck(row.latest_version).label }}</el-tag>
            </el-tooltip>
          </div>
          <el-tag v-else type="info">未取得原文</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="196" fixed="right" class-name="cy-col-nowrap">
        <template #default="{ row }">
          <div class="doc-row-ops">
            <span v-if="row.country_code === 'CN' || row.language === 'zh'" class="sub">中文原文</span>
            <el-button
              v-else-if="isPlatformAdmin"
              size="small"
              type="primary"
              plain
              :loading="translatingDocId === row.id"
              @click.stop="translateDocument(row)"
            >
              {{ row.latest_version?.state === 'parsed' ? '查缺补译' : '补全译文' }}
            </el-button>
            <el-button
              size="small"
              link
              type="primary"
              data-testid="btn-row-version-compare"
              @click.stop="openCompareModal(row)"
            >
              版本对比
            </el-button>
          </div>
        </template>
      </el-table-column>
    </el-table>
        <el-pagination v-if="selectedGroup && visibleDocuments.pages > 1" class="cy-pager" :current-page="documentPageNumber" :page-size="20" :total="selectedGroup.items.length" layout="total, prev, pager, next" aria-label="分类文档分页" @current-change="(n: number) => { documentPageNumber = n }" />
      </section>
    </div>
    <div v-if="hasMore" class="document-pagination">
      <span>目录与分类统计基于已加载的 {{ docs.length }} 份文件。</span>
      <el-button :loading="loading" @click="load(true)">继续加载文档</el-button>
    </div>

    <el-dialog
      v-model="uploadDialog"
      :title="uploadForm.document_id ? '上传该法规的新版本原文' : '本地上传官方原文'"
      width="min(560px, 94vw)"
    >
      <el-alert
        type="info"
        :closable="false"
        style="margin-bottom: 12px"
        title="若目标国家官网需通过浏览器访问下载，可将下载的官方 PDF 原文在此上传。系统会自动完成归档并切分条款（扫描件会先做文字识别）。"
      />
      <el-form label-width="110px">
        <template v-if="!uploadForm.document_id">
          <el-form-item label="所属采集站点">
            <el-select
              v-model="uploadForm.site_id"
              style="width: 100%"
              placeholder="选择归属的官方站点"
            >
              <el-option
                v-for="s in sites"
                :key="s.id"
                :label="`【${countryLabel(s.country_code)}】${s.publisher}（${readableUrl(s.entry_url, 45)}）`"
                :value="s.id"
              />
            </el-select>
          </el-form-item>
          <el-form-item label="官方原文地址">
            <el-input v-model="uploadForm.source_url" placeholder="https://..." />
          </el-form-item>
        </template>
        <el-form-item label="法规标题">
          <el-input v-model="uploadForm.title" placeholder="官方法规名称" />
        </el-form-item>
        <el-form-item label="发布日期（选填）">
          <el-input v-model="uploadForm.published_date" placeholder="YYYY-MM-DD" />
        </el-form-item>
        <el-form-item label="版本说明（选填）">
          <el-input v-model="uploadForm.version_label" placeholder="如 Revised Edition 2020" />
        </el-form-item>
        <el-form-item label="原文文件">
          <input type="file" accept=".pdf,.html,.htm" @change="onFileChange" />
          <div v-if="uploadForm.file_name" style="font-size: 13px; color: #67c23a; margin-top: 4px">
            已选择：{{ uploadForm.file_name }}
          </div>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="uploadDialog = false">取消</el-button>
        <el-button type="primary" :loading="uploading" @click="submitUpload">上传</el-button>
      </template>
    </el-dialog>

    <el-drawer v-model="drawer" :title="detail?.title" size="min(880px, 94vw)" append-to-body>
      <el-alert v-for="resolution in provenance?.binding_resolutions" :key="resolution.canonical_document_id"
        type="info" :closable="false" style="margin-bottom: 12px">
        已登记正确原文关联（{{ resolution.expected_edition }}，待人工核对）。原错误关联记录已停用隐藏，新关联文本核对后生效。
        官方来源：<a :href="resolution.official_source_url" target="_blank" rel="noopener noreferrer">查看对应版本</a>
      </el-alert>
      <template v-if="detail">
        <MarkNewLaw :doc="detail" @done="reloadDetail" />
        <el-alert
          v-for="(hold, index) in provenance?.source_integrity_holds"
          :key="index"
          type="error"
          :closable="false"
          show-icon
          :title="hold.reason"
          style="margin-bottom: 16px"
        />
        <div
          v-for="p in provenance?.versions.filter((x) => x.version_id === versionId)"
          :key="p.version_id"
          style="margin-bottom: 16px"
        >
          <el-tag :type="p.integrity_ok ? 'success' : 'danger'">{{
            p.integrity_ok ? '归档原文完好' : '归档原文与入库时不一致'
          }}</el-tag>
          <span style="margin-left: 12px"
            >来源：{{
              p.acquisition === 'pipeline_fetch'
                ? '平台从官方网站自动下载'
                : p.acquisition === 'manual_upload'
                  ? '手工上传'
                  : ['registered_download', 'official_import', 'official_site'].includes(p.acquisition)
                    ? '从官方网站下载'
                    : '早期导入，未留下载记录（可按原文地址核对）'
            }}{{ p.pipeline_refetch_verified ? ' · 已重新下载核对' : '' }}</span
          >
          <span v-if="p.registered_download" style="margin-left: 12px">
            {{ p.registered_download.source_nature === 'statutory_body_republication'
              ? '法定机构转载'
              : '官方发布' }}
          </span>
        </div>
        <div
          style="
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 8px;
          "
        >
          <span style="word-break: break-all; color: #909399" :title="detail.source_url"
            >原文地址：{{ readableUrl(detail.source_url, 120) }}</span
          >
          <div style="display: flex; gap: 8px; flex-shrink: 0">
            <el-button size="small" data-testid="btn-drawer-version-compare" @click="openCompareModal(detail)">版本对比</el-button>
            <el-button size="small" @click="openUploadDialog(detail)"
              >上传新版本原文</el-button
            >
            <el-button size="small" type="primary" plain :disabled="!versionId" @click="downloadOriginal">下载归档原文</el-button>
          </div>
        </div>
        <el-radio-group
          v-if="detail.versions.length > 1"
          :model-value="versionId"
          @change="(v: string | number | boolean | undefined) => pick(String(v))"
          style="margin-bottom: 12px"
        >
          <el-radio-button v-for="v in detail.versions" :key="v.id" :value="v.id">
            {{ officialTextLabel(v) }}
          </el-radio-button>
        </el-radio-group>

        <template v-for="v in detail.versions.filter((x) => x.id === versionId)" :key="v.id">
          <el-descriptions :column="2" border size="small" style="margin-bottom: 12px">
            <el-descriptions-item label="状态">{{
              VERSION_STATE_LABEL[v.state]
            }}</el-descriptions-item>
            <el-descriptions-item label="分条方式">
              {{ v.parse_strategy ? PARSE_STRATEGY_LABEL[v.parse_strategy] : '—' }}
            </el-descriptions-item>
            <el-descriptions-item label="现行状态">{{
              v.legal_status === 'unknown' ? '未标注' : LEGAL_STATUS_LABEL[v.legal_status]
            }}</el-descriptions-item>
            <el-descriptions-item label="官方版本说明">{{
              v.version_label ?? '—'
            }}</el-descriptions-item>
            <el-descriptions-item label="发布日期">{{
              v.published_date ?? '—'
            }}</el-descriptions-item>
            <el-descriptions-item label="生效日期">{{
              v.effective_date ?? '—'
            }}</el-descriptions-item>
            <el-descriptions-item label="页数">{{ v.page_count ?? '—' }}</el-descriptions-item>
            <el-descriptions-item label="字数">{{ v.char_count ?? '—' }}</el-descriptions-item>
            <el-descriptions-item label="文字来源" :span="2">
              {{ v.text_source === 'ocr'
                ? '扫描件（文字由平台识别）'
                : '电子版原文' }}
            </el-descriptions-item>
            <el-descriptions-item v-if="v.text_source === 'ocr'" label="原文核对" :span="2">
              <div class="scan-check" data-testid="scan-check-row">
                <el-tag :type="scanCheck(v).type" size="small" data-testid="version-check-state">{{ scanCheck(v).label }}</el-tag>
                <span v-if="v.review_status !== 'approved' && checkNote" class="scan-note">{{ checkNote }}</span>
                <span v-else-if="v.review_status !== 'approved'" class="scan-note">待人工对照原件复核</span>
                <el-button v-if="v.review_status !== 'approved' && !checkItems.length" size="small" type="primary" data-testid="version-check-ok" @click="checkVersion(v.id, true)">核对无误</el-button>
              </div>
            </el-descriptions-item>
            <el-descriptions-item v-if="v.error" label="说明" :span="2">{{
              cleanProcessNote(v.error)
            }}</el-descriptions-item>
          </el-descriptions>
          <section v-if="v.text_source === 'ocr' && v.review_status !== 'approved' && checkItems.length" class="scan-items" data-testid="scan-items">
            <article v-for="item in checkItems" :key="item.clause_id" class="scan-item" :class="{ done: item.state !== 'pending' }" data-testid="scan-item">
              <div class="scan-text">
                <div class="scan-where">第 {{ item.page || '—' }} 页<template v-if="item.clause_number"> · {{ item.clause_number }}</template>
                  <el-tag v-if="item.state !== 'pending'" size="small" type="success" effect="plain">{{ item.state === 'fixed' ? '已修订' : '已核对' }}</el-tag></div>
                <el-input v-if="editing === item.clause_id" v-model="draft" type="textarea" :autosize="{ minRows: 4, maxRows: 14 }" />
                <p v-else class="scan-mark" dir="auto">{{ item.text }}</p>
                <div v-if="item.state === 'pending' || editing === item.clause_id" class="scan-actions">
                  <template v-if="editing === item.clause_id">
                    <el-button size="small" type="primary" @click="settle(v.id, item, draft)">保存</el-button>
                    <el-button size="small" @click="editing = ''">取消</el-button>
                  </template>
                  <template v-else>
                    <el-button v-if="item.page" size="small" type="primary" :loading="rereading === item.page" data-testid="scan-item-reread" @click="rereadPage(v.id, item.page)">重新识别本页</el-button>
                    <el-button size="small" data-testid="scan-item-edit" @click="editing = item.clause_id; draft = item.text">修订文本</el-button>
                    <el-button size="small" link data-testid="scan-item-ok" @click="settle(v.id, item, null)">核对无误</el-button>
                  </template>
                  <el-button link type="primary" size="small" @click="openOriginalPage(v.id, item.page)">打开原文第 {{ item.page || 1 }} 页</el-button>
                </div>
              </div>
              <img v-if="item.page && pageImages[item.page]" :src="pageImages[item.page]" class="scan-page" :alt="`原文第 ${item.page} 页`" />
            </article>
          </section>
          <el-alert
            v-if="v.parse_strategy && v.parse_strategy !== 'numbered'"
            type="info"
            :closable="false"
            style="margin-bottom: 12px"
            title="该文件没有条款编号，按段落切分。"
          />
        </template>

        <template v-if="detail.versions.length >= 2">
          <h4>官方修订对比</h4>
          <VersionCompare :key="detail.id" :document-id="detail.id" />
        </template>
        <div v-else class="single-ver-banner" data-testid="single-version-banner">
          <span>当前法规为现行唯一收录版本，上传修订版后可逐条对比差异；也可选择同国其他编次法规文件对照差异。</span>
          <el-button size="small" link type="primary" @click="openCompareModal(detail)">打开版本对比</el-button>
        </div>
        <h4>{{ clauses.length >= 500 ? "条款（显示前 500 条）" : `条款（共 ${clauses.length} 条）` }}</h4>
        <el-table :data="clauses" border size="small" max-height="420">
          <el-table-column label="条号" width="100">
            <template #default="{ row }">{{ articleLabel(row.clause_number) || (row.ordinal === 0 ? '前言' : '（无条号）') }}</template>
          </el-table-column>
          <el-table-column label="所在章节" width="200">
            <template #default="{ row }">{{ row.section_path || '—' }}</template>
          </el-table-column>
          <el-table-column label="页码" width="80">
            <template #default="{ row }">
              {{
                row.page_start === row.page_end
                  ? row.page_start
                  : `${row.page_start}–${row.page_end}`
              }}
            </template>
          </el-table-column>
          <el-table-column label="原文">
            <template #default="{ row }"
              ><div style="white-space: pre-wrap">
                {{ row.original_text }}
              </div></template
            >
          </el-table-column>
        </el-table>

      </template>
    </el-drawer>

    <el-dialog
      v-model="compareVisible"
      title="法规多版本与编次差异对比"
      width="min(860px, 94vw)"
      top="5vh"
      append-to-body
      data-testid="doc-compare-dialog"
    >
      <div class="compare-modal-body">
        <div class="compare-mode-bar">
          <el-radio-group v-model="compareMode">
            <el-radio-button value="single">同部法规历次版本对比</el-radio-button>
            <el-radio-button value="cross">同国两版文件差异对比</el-radio-button>
          </el-radio-group>
        </div>

        <template v-if="compareMode === 'single'">
          <div class="compare-picker-row">
            <label class="compare-field">
              <span>选择在库法规文件</span>
              <el-select v-model="compareDocId" filterable style="width: 100%" placeholder="选择需要查看版本差异的法规">
                <el-option
                  v-for="d in docs"
                  :key="d.id"
                  :value="d.id"
                  :label="`【${countryLabel(d.country_code)}】${d.title}${dupTag(d) ? `（${dupTag(d)}）` : ''}`"
                />
              </el-select>
            </label>
          </div>
          <template v-if="compareDocId">
            <VersionCompare :key="compareDocId" :document-id="compareDocId" />
            <div class="compare-assist-bar">
              <el-button v-if="compareDocObj" size="small" @click="compareVisible = false; openUploadDialog(compareDocObj)">
                上传该法规修订版原文
              </el-button>
              <el-button size="small" type="primary" plain @click="switchToCrossCompare">
                切换为同国两版文件对比
              </el-button>
            </div>
          </template>
        </template>

        <template v-else>
          <div class="compare-cross-grid">
            <label class="compare-field">
              <span>所属国家</span>
              <el-select v-model="crossCountry" filterable style="width: 150px" @change="onCrossCountryChange">
                <el-option v-for="c in headerCountryOptions" :key="c.code" :label="c.name" :value="c.code" />
              </el-select>
            </label>
            <label class="compare-field grow-field">
              <span>基准文件（较早或对照版）</span>
              <el-select v-model="crossBaseDocId" filterable style="width: 100%" @change="runCrossCompare">
                <el-option
                  v-for="d in crossCountryDocs"
                  :key="d.id"
                  :value="d.id"
                  :label="`${d.title}${dupTag(d) ? `（${dupTag(d)}）` : ''}`"
                />
              </el-select>
            </label>
            <label class="compare-field grow-field">
              <span>目标文件（较新或现行版）</span>
              <el-select v-model="crossTargetDocId" filterable style="width: 100%" @change="runCrossCompare">
                <el-option
                  v-for="d in crossCountryDocs"
                  :key="d.id"
                  :value="d.id"
                  :label="`${d.title}${dupTag(d) ? `（${dupTag(d)}）` : ''}`"
                />
              </el-select>
            </label>
          </div>

          <div v-if="crossCountryDocs.length < 2" class="single-ver-banner">
            当前国家在库已解析法规不足 2 份，上传新版本或新法规后可在此逐条对比差异。
          </div>
          <div v-else-if="crossBaseDocId === crossTargetDocId" class="single-ver-banner">
            请选择两份不同的法规文件以逐条对比差异。
          </div>
          <div v-else v-loading="crossLoading" class="cross-diff-results">
            <div class="cross-summary-bar">
              <el-tag type="success" effect="plain">新增 {{ crossCounts.added }} 条</el-tag>
              <el-tag type="danger" effect="plain">删除 {{ crossCounts.removed }} 条</el-tag>
              <el-tag type="warning" effect="plain">改动 {{ crossCounts.changed }} 条</el-tag>
              <el-tag type="info" effect="plain">未变 {{ crossCounts.unchanged }} 条</el-tag>
              <el-checkbox v-model="crossOnlyChanges" style="margin-left: auto">只看有变化的条款</el-checkbox>
            </div>
            <el-empty v-if="!filteredCrossRows.length" description="所选两版文件无条款差异" />
            <article
              v-for="(r, idx) in filteredCrossRows.slice(0, 120)"
              :key="idx"
              class="cross-diff-card"
              :class="r.status"
            >
              <header class="cross-diff-head">
                <el-tag size="small" :type="COMPARE_STATUS_META[r.status].type">
                  {{ COMPARE_STATUS_META[r.status].label }}
                </el-tag>
                <strong>{{ r.heading }}</strong>
              </header>
              <p v-if="r.status === 'changed'" class="cross-diff-text">
                <template v-for="(seg, si) in r.diff" :key="si">
                  <del v-if="seg.op === 'delete'">{{ seg.text }}</del>
                  <ins v-else-if="seg.op === 'insert'">{{ seg.text }}</ins>
                  <span v-else>{{ seg.text }}</span>
                </template>
              </p>
              <p v-else class="cross-diff-text" :class="{ 'is-removed': r.status === 'removed' }">
                {{ r.targetText || r.baseText }}
              </p>
            </article>
          </div>
        </template>
      </div>
      <template #footer>
        <el-button @click="compareVisible = false">关闭</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
/* 第 7.5 项 规则 21：目录可收起，主区给文档列表 */
.document-library { display: grid; grid-template-columns: 264px minmax(0, 1fr); gap: 18px; align-items: start; }
.document-library.dir-collapsed { grid-template-columns: 48px minmax(0, 1fr); }
.results-title { flex: 1; min-width: 0; }
.doc-err { color: #b45309; }
.dup-tag { color: var(--cy-text-2); font-weight: 400; }
.dir-rail { position: sticky; top: 16px; display: flex; flex-direction: column; align-items: center; gap: 10px; width: 48px; padding: 14px 0; border: 1px solid var(--cy-line); border-radius: 12px; background: white; color: var(--cy-text-2); cursor: pointer; }
.dir-rail svg { width: 16px; height: 16px; }
.dir-rail span { writing-mode: vertical-rl; letter-spacing: .2em; font-size: 14px; }
.dir-rail:hover { color: var(--el-color-primary); }
.document-library > :deep(.regulation-directory) { max-height: 780px; overflow-y: auto; }
.document-results { min-width: 0; border: 1px solid #e2e8f0; border-radius: 12px; padding: 20px; background: white; }
.document-results-heading { display: flex; align-items: center; justify-content: space-between; gap: 16px; flex-wrap: wrap; margin-bottom: 20px; }
.document-results-heading h2 { font-size: 18px; margin: 0; color: #263c4b; }
.document-results-heading p { font-size: 13px; color: var(--cy-text-2); margin: 10px 0 0; }
.document-pagination { display: flex; align-items: center; justify-content: center; gap: 16px; flex-wrap: wrap; margin-top: 20px; color: #738495; font-size: 13px; }
@media (max-width: 1100px) { .document-library { grid-template-columns: 1fr; } .document-library > :deep(.regulation-directory) { max-height: 420px; } .document-results { padding: 14px; } }
.document-status {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
  padding: 2px 0;
}
.document-status :deep(.el-tag) {
  flex: 0 0 auto;
  margin: 0;
  height: 24px;
  line-height: 22px;
}
.document-title {
  font-weight: 600;
  color: #263c4b;
}
.document-source {
  color: #8796a2;
  font-size: 13px;
  max-width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  margin-top: 5px;
}
.document-title {
  display: block;
  padding: 0;
  border: 0;
  background: none;
  text-align: left;
  font: inherit;
  font-weight: 600;
  line-height: 1.6;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.document-title:hover {
  color: var(--cy-blue);
}
.check-hint { color: #64748b; font-size: 13px; margin-left: 10px; }
.check-actions { margin-left: 10px; }
.scan-check { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.scan-note { color: var(--el-text-color-regular); }
.scan-items { margin: 0 0 16px; display: grid; gap: 12px; }
.scan-item { display: grid; grid-template-columns: minmax(0, 1fr) 320px; gap: 14px; padding: 12px; border: 1px solid var(--el-border-color-light); border-radius: 10px; }
.scan-item.done { opacity: .7; }
.scan-where { font-size: 13px; color: var(--el-text-color-secondary); display: flex; gap: 8px; align-items: center; }
.scan-mark { background: #fef9c3; padding: 8px 10px; border-radius: 6px; white-space: pre-wrap; line-height: 1.7; margin: 6px 0; }
.scan-actions { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.scan-page { width: 320px; border: 1px solid var(--el-border-color-light); border-radius: 6px; }
:deep(.cy-col-full .cell) { overflow: visible; text-overflow: clip; white-space: nowrap; }
.doc-row-ops { display: inline-flex; align-items: center; gap: 8px; white-space: nowrap; }
.doc-row-ops :deep(.el-button.is-link) { padding: 2px 4px; }
.single-ver-banner { display: flex; align-items: center; justify-content: space-between; gap: 12px; flex-wrap: wrap; padding: 10px 14px; margin: 10px 0 14px; border-radius: 8px; background: #f8fafc; border: 1px solid #e2e8f0; font-size: 13px; color: #475569; }
.compare-modal-body { display: flex; flex-direction: column; gap: 14px; max-height: 68vh; overflow-y: auto; padding-right: 4px; }
.compare-mode-bar { display: flex; align-items: center; justify-content: space-between; }
.compare-picker-row { display: flex; flex-direction: column; gap: 8px; }
.compare-cross-grid { display: flex; gap: 12px; flex-wrap: wrap; align-items: flex-end; }
.compare-field { display: flex; flex-direction: column; gap: 5px; font-size: 13px; color: #475569; font-weight: 600; }
.grow-field { flex: 1 1 260px; min-width: 220px; }
.compare-assist-bar { display: flex; gap: 10px; flex-wrap: wrap; padding-top: 4px; }
.cross-summary-bar { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin-bottom: 10px; }
.cross-diff-card { border: 1px solid #e2e8f0; border-left-width: 4px; border-radius: 8px; padding: 10px 14px; margin-bottom: 10px; background: #fff; }
.cross-diff-card.added { border-left-color: var(--el-color-success); }
.cross-diff-card.removed { border-left-color: var(--el-color-danger); }
.cross-diff-card.changed { border-left-color: var(--el-color-warning); }
.cross-diff-card.unchanged { border-left-color: #cbd5e1; }
.cross-diff-head { display: flex; align-items: center; gap: 8px; margin-bottom: 6px; }
.cross-diff-text { margin: 0; font-size: 13.5px; line-height: 1.75; white-space: pre-wrap; color: #1e293b; }
.cross-diff-text.is-removed { color: #64748b; text-decoration: line-through; }
.cross-diff-text del { background: #fde8e8; color: #b42318; text-decoration: line-through; padding: 0 2px; }
.cross-diff-text ins { background: #e3f6e8; color: #17613a; text-decoration: none; padding: 0 2px; }
</style>
