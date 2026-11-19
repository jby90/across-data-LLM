import { reactive } from 'vue'
import { articleLabel } from '@cyshuhe/shared/src/articleLabel'
import type { LegalTerm } from '@cyshuhe/shared/src/legalText'

interface RegisteredTerm {
  term: LegalTerm
  docTitle?: string
}

const termRegistry = new Map<string, RegisteredTerm>()
const docTitleRegistry = new Map<string, string>()

export function registerDocTitle(docId?: string | null, titleZh?: string | null) {
  if (docId && titleZh?.trim()) {
    docTitleRegistry.set(docId, titleZh.trim())
  }
}

export function registerTerm(term: LegalTerm, docTitle?: string): string {
  const key = term.alternatives?.length
    ? `${term.id}:alt:${term.alternatives.map((a) => a.id).join(',')}`
    : term.id || `${term.source_term}:${term.target_zh}`
  if (termRegistry.size > 6000) {
    const firstKey = termRegistry.keys().next().value
    if (firstKey) termRegistry.delete(firstKey)
  }
  termRegistry.set(key, { term, docTitle })
  return key
}

export function normTermText(raw?: string | null): string {
  return (raw || '')
    .replace(/[\s“”"'‘’（）()【】\[\]，,。.；;：:、·\-—_]/g, '')
    .toLowerCase()
}

export function dedupeParagraphLines(raw?: string | null): string {
  const text = (raw || '').trim()
  if (!text) return ''
  const lines = text.split(/\n+/).map((l) => l.trim()).filter(Boolean)
  if (lines.length <= 1) {
    // 检查单段内是否完全前后重复两遍
    const half = Math.floor(text.length / 2)
    if (text.length >= 16) {
      const a = text.slice(0, half).trim()
      const b = text.slice(half).trim()
      if (a && normTermText(a) === normTermText(b)) return a
    }
    return text
  }
  const seen = new Set<string>()
  const kept: string[] = []
  for (const line of lines) {
    const k = normTermText(line)
    if (!k || seen.has(k)) continue
    seen.add(k)
    kept.push(line)
  }
  return kept.join('\n')
}

export function formatClauseNumberLabel(raw?: string | null): string {
  const v = (raw || '').trim()
  if (!v) return ''
  // 过滤裸中文序号或内部章节代码（如“四”、“ch1”、“sec2”）
  if (/^[一二三四五六七八九十百零〇]+$/.test(v)) return ''
  if (/^(?:v\d+(?:\.\d+)*|(?:ch|sec|part|s|p)[-_]?\d+)$/i.test(v)) return ''
  const artMatch = v.match(/^(?:Article|Art\.?|Section|Sec\.?)\s*(\d+[A-Za-z]?(?:\([^)]+\))*)$/i)
  if (artMatch) return `第 ${artMatch[1]} 条`
  const subMatch = v.match(/^(\d+[A-Za-z]?)(\([0-9a-zA-Z]+\)+)$/)
  if (subMatch) return `第 ${subMatch[1]}${subMatch[2]} 条`
  const converted = articleLabel(v)
  return converted.replace(/^第\s*(\d+[A-Za-z]?(?:\([^)]+\))*)\s*([条段节章])$/, '第 $1 $2')
}

export function cleanTermReference(
  rawRef?: string | null,
  sourceLabel?: string | null,
  docId?: string | null,
): string {
  const ref = (rawRef || '').trim()
  const label = (sourceLabel || '').trim()
  if (!ref && !label) return ''
  if (!ref || ref === '通用译名') {
    return label && label !== '通用译名' ? label : '跨法域通用规范译名'
  }
  const segments = ref
    .replace(/\s*[（(]?\bv\d+(?:\.\d+)*\b[)）]?/gi, '')
    .split(/\s*·\s*/)
    .map((s) => s.trim())
    .filter(Boolean)

  const cleaned: string[] = []
  segments.forEach((seg, idx) => {
    if (/^v\d+(?:\.\d+)*$/i.test(seg)) return
    if (idx > 0 && /^[一二三四五六七八九十百零〇]+$/.test(seg)) return
    if (idx > 0 && /^(?:ch|sec|part|s|p)[-_]?\d+$/i.test(seg)) return
    if (idx === 0) {
      const zhTitle = (docId && docTitleRegistry.get(docId)) || ''
      const title = zhTitle || seg
      if (/[\u4e00-\u9fff]/.test(title) && !title.startsWith('《') && title !== '通用译名') {
        cleaned.push(`《${title}》`)
      } else {
        cleaned.push(title)
      }
      return
    }
    const fmt = formatClauseNumberLabel(seg)
    if (fmt) cleaned.push(fmt)
  })

  const refOut = cleaned.join(' · ')
  if (!label || refOut.includes(label) || label === refOut) return refOut
  return refOut ? `${label} · ${refOut}` : label
}

export interface DisplayTermEntry {
  id: string
  zh: string
  orig: string
  ref: string
}

export interface SanitizedTermView {
  headingMain: string
  headingSub: string
  entries: DisplayTermEntry[]
  isMultiple: boolean
  genericFallback: string
  sourceUrl: string
}

function buildSingleEntry(t: LegalTerm, includeLabel: boolean): DisplayTermEntry {
  const zh = dedupeParagraphLines(t.definition_zh)
  const rawOrig = dedupeParagraphLines(t.definition_original)
  const zhNorm = normTermText(zh)
  const origNorm = normTermText(rawOrig)
  // 若原文与中文释义归一化后相同，或原文不含外文字母且已被中文释义覆盖，则不再重复展示第二遍
  const origIsDuplicateZh =
    !rawOrig ||
    (zhNorm && origNorm === zhNorm) ||
    (zhNorm && !/[A-Za-zÀ-ÿ\u0600-\u06FF]/.test(rawOrig) && (zhNorm.includes(origNorm) || origNorm.includes(zhNorm)))
  const orig = origIsDuplicateZh ? '' : rawOrig
  const ref = cleanTermReference(t.source_reference, includeLabel ? t.source_label : '', t.document_id)
  return {
    id: t.id,
    zh,
    orig,
    ref,
  }
}

export function sanitizeTermView(term: LegalTerm): SanitizedTermView {
  const src = (term.source_term || '').trim()
  const tgt = (term.target_zh || '').trim()
  const sameHeading = !tgt || normTermText(src) === normTermText(tgt)
  const headingMain = tgt || src
  const headingSub = !sameHeading && src ? src : ''

  let entries: DisplayTermEntry[] = []
  if (term.alternatives && term.alternatives.length > 1) {
    const byDef = new Map<string, DisplayTermEntry>()
    for (const alt of term.alternatives) {
      const built = buildSingleEntry(alt, false)
      const key = normTermText(built.zh || built.orig) || built.ref || alt.id
      const existing = byDef.get(key)
      if (!existing || (!existing.zh && built.zh)) {
        byDef.set(key, built)
      }
    }
    entries = [...byDef.values()]
  } else {
    entries = [buildSingleEntry(term, true)]
  }

  if (entries.length === 1 && term.alternatives && term.alternatives.length > 1) {
    entries[0].ref = cleanTermReference(term.source_reference, term.source_label, term.document_id)
  }

  const hasAnyDef = entries.some((e) => e.zh || e.orig)
  return {
    headingMain,
    headingSub,
    entries,
    isMultiple: entries.length > 1,
    genericFallback: hasAnyDef ? '' : `${tgt || src} · 跨法域通用规范译名`,
    sourceUrl: term.source_url || '',
  }
}

export const termPopoverState = reactive<{
  visible: boolean
  term: LegalTerm | null
  anchorEl: HTMLElement | null
  top: number
  left: number
  placement: 'top' | 'bottom'
}>({
  visible: false,
  term: null,
  anchorEl: null,
  top: 0,
  left: 0,
  placement: 'bottom',
})

export function updateTermPopoverPosition() {
  const el = termPopoverState.anchorEl
  if (!el || !termPopoverState.visible || !document.body.contains(el)) {
    closeTermPopover()
    return
  }
  const rect = el.getBoundingClientRect()
  const popW = Math.min(396, window.innerWidth - 24)
  const estH = 250
  const left = Math.max(12, Math.min(rect.left, window.innerWidth - popW - 12))
  if (rect.bottom + estH + 16 <= window.innerHeight || rect.top < estH) {
    termPopoverState.placement = 'bottom'
    termPopoverState.top = Math.min(window.innerHeight - 80, rect.bottom + 8)
  } else {
    termPopoverState.placement = 'top'
    termPopoverState.top = Math.max(12, rect.top - 8)
  }
  termPopoverState.left = left
}

export function openTermPopover(el: HTMLElement, term: LegalTerm) {
  if (termPopoverState.visible && termPopoverState.anchorEl === el) {
    closeTermPopover()
    return
  }
  termPopoverState.anchorEl = el
  termPopoverState.term = term
  termPopoverState.visible = true
  updateTermPopoverPosition()
}

export function closeTermPopover() {
  termPopoverState.visible = false
  termPopoverState.anchorEl = null
}

export function handleTermDelegation(ev: Event) {
  const target = ev.target as HTMLElement | null
  const mark = target?.closest?.('.term-mark') as HTMLElement | null
  if (!mark) return
  if (ev.type === 'keydown') {
    const ke = ev as KeyboardEvent
    if (ke.key !== 'Enter' && ke.key !== ' ') return
    ke.preventDefault()
  }
  const key = mark.dataset.termKey
  if (!key) return
  const found = termRegistry.get(key)
  if (!found) return
  ev.stopPropagation()
  openTermPopover(mark, found.term)
}
