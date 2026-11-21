// 需求 2.7 四级角色：管理端 超级管理员 / 运维管理员；用户端 企业用户（业务编辑）/ 普通查看者
export type Role = 'platform_admin' | 'ops_admin' | 'member' | 'viewer'
export { startProgressStream, parseSseBlock, type ProgressEvent, type StreamState } from './liveEvents'
export { STATUS_TONE, statusTone, statusClass, TONE_TAG_TYPE, type StatusTone } from './statusTone'
export { WORDING, STATUS_WORDS, missingWording } from './wording'
export { COUNTRY_NAMES } from './regulationDirectory'
export { MAP_VIEWBOX, WORLD_COUNTRY_PATHS, WORLD_COUNTRY_HUBS, getCountryHub, getCountryLabelOffset, buildRouteArcPath } from './worldMapData'

export const ROLE_LABEL: Record<Role, string> = {
  platform_admin: '超级管理员',
  ops_admin: '运维管理员',
  member: '企业用户',
  viewer: '普通查看者',
}
export const ROLE_HINT: Record<Role, string> = {
  platform_admin: '全部功能，含企业与账号、审计记录、评估执行记录',
  ops_admin: '维护法规、采集站点、译文术语、图谱、简报、准入事项等基础数据',
  member: '本企业的评估和准入项目：新建、填写、生成、下载',
  viewer: '查看本企业的评估、文件、简报和准入项目；可在咨询助手提问（仅自己可见）',
}
/** 用户端能不能新建、修改（普通查看者不能） */
export function canEdit(u: { role?: Role } | null | undefined): boolean {
  return !!u && u.role !== 'viewer'
}
/** 能不能下载生成的文件（查看者要开通下载权限） */
export function canExport(u: { role?: Role; can_export?: boolean } | null | undefined): boolean {
  return !!u && (u.role !== 'viewer' || !!u.can_export)
}

/** 法规的一个官方文本怎么称呼：官方的修正案名称或发布日期。不用“第 N 版”，不用入库时间
 *（版本只表示官方的修订；重复导入、重新解析不算版本） */
export function officialTextLabel(v: { version_label?: string | null; published_date?: string | null }): string {
  if (v.version_label) return v.version_label
  if (v.published_date) return `${v.published_date} 发布的官方文本`
  return '官方文本（发布日期未记录）'
}

const BUILTIN_ROLE_CODES = new Set(['org-admin', 'member', 'admin', 'ops', 'viewer', 'reviewer'])

/** 界面上显示的用户名：优先使用用户设置的姓名（排除内置英文代号），否则回退登录邮箱 @ 前缀或“用户” */
export function userName(
  u: { email?: string | null; display_name?: string | null } | null | undefined,
): string {
  const custom = u?.display_name?.trim()
  if (custom && !BUILTIN_ROLE_CODES.has(custom.toLowerCase())) {
    return custom
  }
  return (u?.email || '').split('@')[0] || '用户'
}
export interface UserOut {
  id: string
  org_id: string | null
  email: string
  display_name: string
  role: Role
  is_active: boolean
  signup_source?: string
  can_export?: boolean
}

export interface OrgOut {
  id: string
  name: string
  is_active: boolean
  is_demo?: boolean
  llm_redaction_terms: string[]
}

export interface TokenResponse {
  access_token: string
  token_type: string
  role: Role
  org_id: string | null
}

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message)
  }
}

export type Side = 'user' | 'admin'

function humanizeValidationMessage(raw: string): string {
  const msg = raw.replace(/^Value error,\s*/i, '').trim()
  const lower = msg.toLowerCase()
  if (!msg) return '请检查填写内容是否正确'
  if (lower.includes('field required') || lower.includes('missing')) return '请完整填写必填内容'
  if (lower.includes('at least') || lower.includes('too_short') || lower.includes('min_length')) {
    return '输入内容长度不符合要求'
  }
  if (lower.includes('at most') || lower.includes('too_long') || lower.includes('max_length')) {
    return '输入内容超出长度限制'
  }
  if (lower.includes('email')) return '请输入正确的邮箱地址'
  if (
    lower.includes('value is not a valid') ||
    lower.includes('input should be') ||
    lower.includes('invalid')
  ) {
    return '输入内容格式不正确'
  }
  if (lower.includes('extra inputs are not permitted')) return '包含不支持的填写项'
  if (/^[\x20-\x7e]+$/.test(msg)) return '请检查填写内容是否完整且格式正确'
  return msg
}

function detailToMessage(detail: unknown, fallback: string): string {
  if (typeof detail === 'string') return detail
  if (detail && typeof detail === 'object' && 'message' in detail) {
    const data = detail as { message?: unknown; issues?: unknown }
    if (typeof data.message === 'string') {
      const issues = Array.isArray(data.issues)
        ? data.issues.filter((x): x is string => typeof x === 'string')
        : []
      return [data.message, ...issues.slice(0, 5)].join('；')
    }
  }
  if (Array.isArray(detail) && detail.length > 0) {
    const first = detail[0] as { msg?: string }
    return first?.msg ? humanizeValidationMessage(first.msg) : fallback
  }
  return fallback
}

/** 创建某一端的 API 客户端。令牌放 sessionStorage,关闭标签页即失效;两端使用不同的存储键,互不干扰。 */
export function createClient(side: Side, onUnauthorized: () => void) {
  const key = `cyshuhe.${side}.token`

  const token = () => sessionStorage.getItem(key)
  const setToken = (t: string | null) =>
    t ? sessionStorage.setItem(key, t) : sessionStorage.removeItem(key)

  async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
    const headers: Record<string, string> = {}
    const t = token()
    if (t) headers.Authorization = `Bearer ${t}`
    if (body !== undefined) headers['Content-Type'] = 'application/json'
    const url = path.startsWith('/api/') ? path : `/api${path}`
    const isAuthOrProbe =
      path.startsWith('/auth/') ||
      path === '/user/me' ||
      path === '/admin/me' ||
      path.startsWith('/api/auth/') ||
      path === '/api/user/me' ||
      path === '/api/admin/me'
    const timeoutMs = isAuthOrProbe ? 12000 : 90000
    const ctrl = new AbortController()
    const timer = globalThis.setTimeout(() => ctrl.abort(), timeoutMs)
    let res: Response
    try {
      res = await fetch(url, {
        method,
        headers,
        body: body === undefined ? undefined : JSON.stringify(body),
        signal: ctrl.signal,
      })
    } catch (err) {
      // 网络断开、超时或请求被中止：浏览器给的是英文（Failed to fetch / AbortError），界面上写中文
      const isAbort = err instanceof DOMException && err.name === 'AbortError'
      throw new ApiError(0, isAbort ? '服务响应超时，请稍后重试' : '网络连接中断，请稍后重试')
    } finally {
      globalThis.clearTimeout(timer)
    }
    if (res.status === 401 && !path.startsWith('/auth/')) {
      setToken(null)
      onUnauthorized()
    }
    if (!res.ok) {
      let message = `请求未完成（状态码 ${res.status}），请稍后重试`
      try {
        message = detailToMessage((await res.json()).detail, message)
      } catch {
        /* 非 JSON 响应 */
      }
      throw new ApiError(res.status, message)
    }
    return (await res.json()) as T
  }

  return {
    token,
    setToken,
    get: <T>(path: string) => request<T>('GET', path),
    post: <T>(path: string, body?: unknown) => request<T>('POST', path, body),
    put: <T>(path: string, body?: unknown) => request<T>('PUT', path, body),
    patch: <T>(path: string, body?: unknown) => request<T>('PATCH', path, body),
    delete: <T>(path: string) => request<T>('DELETE', path),
    login: (email: string, password: string) =>
      request<TokenResponse>('POST', `/auth/${side}/login`, { email, password }),
    me: () => request<UserOut>('GET', `/${side}/me`),
  }
}

// ---------- 采集与文档库(管理端) ----------

export interface SiteOut {
  terms_approval_scope?: 'legacy' | 'development' | 'production'
  id: string
  country_code: string
  publisher: string
  language: string
  document_type: string
  entry_url: string
  strategy: string
  config: Record<string, unknown>
  enabled: boolean
  interval_hours: number
  next_run_at: string | null
  last_run_at: string | null
  last_status: string | null
  consecutive_failures: number
  attention: string | null
  terms_status: 'pending' | 'approved' | 'rejected'
  terms_url: string | null
  terms_note: string | null
  terms_decided_at: string | null
  document_count: number
  sample_titles: string[]
  doc_types?: string[]
  latest_discovered_at?: string | null
}

export interface RunOut {
  id: string
  trigger: string
  started_at: string
  finished_at: string | null
  status: string
  pages_read: number
  links_found: number
  documents_new: number
  versions_new: number
  errors: number
  message: string | null
}

export interface VersionOut {
  id: string
  version_no: number
  content_hash: string
  size_bytes: number
  mime_type: string
  fetched_at: string
  state: string
  page_count: number | null
  char_count: number | null
  parse_strategy: string | null
  error: string | null
  text_source: 'text_layer' | 'ocr'
  ocr_confidence: number | null
  ocr_required: boolean
  review_status: 'pending_review' | 'approved' | 'rejected'
  check_note?: string | null  // 扫描件识别质量检查的结论
  official_number: string | null
  version_label: string | null
  published_date: string | null
  effective_date: string | null
  repealed_date: string | null
  legal_status: string
  metadata_source: string | null
}

export interface DocumentOut {
  safety_hold?: boolean
  safety_note?: string | null
  id: string
  site_id: string
  country_code: string
  source_url: string
  title: string
  language: string
  document_type: string
  discovered_at: string
  last_checked_at: string | null
  last_error: string | null
  instrument_family: string | null
  instrument_kind: string | null
  relevance: 'relevant' | 'related' | 'out_of_scope' | 'irrelevant' | 'needs_review'
  relevance_note: string | null
  relevance_source?: 'rule' | 'llm' | 'manual'
  latest_version: VersionOut | null
}

export interface DocumentDetail extends DocumentOut {
  versions: VersionOut[]
}

export interface ClauseOut {
  id: string
  ordinal: number
  clause_number: string | null
  heading: string | null
  section_path: string | null
  original_text: string
  page_start: number | null
  page_end: number | null
  language: string
  ocr_required: boolean
  needs_review: boolean
}

export interface EventOut {
  step: string
  status: string
  message: string | null
  created_at: string
}

// 界面一律用中文业务用语,不暴露枚举值
export const TERMS_LABEL: Record<string, string> = {
  pending: '待确认',
  approved: '已确认可采集',
  rejected: '已否决',
}
export const RUN_STATUS_LABEL: Record<string, string> = {
  running: '运行中',
  success: '成功',
  partial: '部分成功',
  failed: '失败',
  skipped: '已跳过',
}
export const VERSION_STATE_LABEL: Record<string, string> = {
  downloaded: '已下载，待解析条款',
  parsed: '已入库',
  needs_ocr: '扫描件，待识别文字',
  failed: '条款解析未完成',
}
export const DOC_TYPE_LABEL: Record<string, string> = {
  LAW: '法律',
  REGULATION: '法规',
  GUIDANCE: '监管指南',
  STANDARD: '标准',
  ADMISSION: '准入申报材料',
  FORM: '申报表',
  OTHER: '其他',
}

const DOC_TYPE_EXTRA_LABEL: Record<string, string> = {
  ...DOC_TYPE_LABEL,
  ACT: '法律',
  SUBSIDIARY: '法规',
  DECREE: '法规',
  RULE: '法规',
  ORDER: '法规',
  GUIDELINE: '监管指南',
  NOTICE: '监管指南',
  CIRCULAR: '监管指南',
}

export function formatDocTypeZh(raw: string | null | undefined, fallback = '其他文件'): string {
  const s = String(raw || '').trim()
  if (!s) return '未分类'
  const k = s.toUpperCase()
  return DOC_TYPE_EXTRA_LABEL[k] || (/[\u4e00-\u9fff]/.test(s) ? s : fallback)
}
export const STRATEGY_LABEL: Record<string, string> = {
  INDEX_PAGE: '目录页发现',
  DIRECT: '直接链接',
  EGAZETTE: '电子宪报检索发现',
}
export const PARSE_STRATEGY_LABEL: Record<string, string> = {
  numbered: '按条款编号',
  chunk: '按篇幅分块（原文无可靠条款编号）',
  page: '按页（未识别到编号）',
  paragraph: '按段落（未识别到编号）',
  line: '按行（网页）',
}
export const LEGAL_STATUS_LABEL: Record<string, string> = {
  unknown: '有效状态未标注',
  in_force: '现行有效',
  amended: '已被修订',
  repealed: '已废止',
  draft: '草案（尚未生效）',
}

/** 下载文件名以服务端给的为准（全站统一“项目或国家_文件名”）；服务端没给时用 fallback */
export function serverFilename(res: Response, fallback: string): string {
  const m = /filename\*=UTF-8''([^;]+)/i.exec(res.headers.get('content-disposition') || '')
  return m ? decodeURIComponent(m[1]) : fallback
}

/** 全站统一的时间显示：按本机时区，“2026-11-07 15:32”；only='date' 只到日期。后端给的是带时区的 ISO 时间。 */
export function localTime(value: string | null | undefined, only?: 'date'): string {
  if (!value) return ''
  const d = new Date(value)
  if (Number.isNaN(d.getTime())) return value
  const p = (n: number) => String(n).padStart(2, '0')
  const day = `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`
  return only === 'date' ? day : `${day} ${p(d.getHours())}:${p(d.getMinutes())}`
}
