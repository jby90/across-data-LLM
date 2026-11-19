export interface LegalTerm {
  rendering_label?: string
  id: string
  source_term: string
  target_zh: string
  aliases?: string[]
  concept_code?: string
  definition_original?: string | null
  definition_zh?: string | null
  source_label: string
  source_reference: string
  source_url?: string | null
  document_id?: string | null
  version_id?: string | null
  source_kind?: string
  alternatives?: LegalTerm[]
  clause_id?: string | null
  source_scope?: string
  scope_path?: string
}
export function splitLegalText(
  text: string,
  terms: LegalTerm[],
): Array<{ text: string; term?: LegalTerm }> {
  if (!text || !terms.length) return text ? [{ text }] : []
  const priority = (term: LegalTerm) =>
    ({
      law_definition: 1,
      regulator_guidance: 2,
      official_parallel: 3,
      international_reference: 4,
    })[term.source_kind || ''] || 5
  const lower = text.toLowerCase()
  const privacyContext =
    /personal data|personal information|données personnelles|données à caractère personnel|البيانات الشخصية|المعطيات الشخصية|个人数据|个人信息/.test(
      lower,
    )
  interface Candidate {
    alias: string
    aliasLower: string
    wordBounded: boolean
    prio: number
    term: LegalTerm
  }
  const candidates: Candidate[] = terms
    .flatMap((term) => {
      const prio = priority(term)
      return [term.target_zh, term.source_term, ...(term.aliases || [])]
        .filter((t): t is string => Boolean(t && t.length > 1 && !t.includes(' / ')))
        .filter(
          (t) =>
            privacyContext ||
            term.source_kind === 'law_definition' ||
            !['data_controller', 'data_processor', 'processing'].includes(
              term.concept_code || '',
            ) ||
            !/^[\p{Script=Latin}\p{Script=Arabic}]/u.test(t),
        )
        .map((alias) => ({
          alias,
          aliasLower: alias.toLowerCase(),
          wordBounded: /^[\p{Script=Latin}\p{Script=Arabic}]/u.test(alias),
          prio,
          term,
        }))
    })
    .sort((a, b) => a.prio - b.prio || b.alias.length - a.alias.length)

  if (!candidates.length) return [{ text }]

  const byFirstChar = new Map<string, Candidate[]>()
  const byAliasLower = new Map<string, Candidate[]>()
  for (const c of candidates) {
    const ch = c.aliasLower[0]
    if (!ch) continue
    const bucket = byFirstChar.get(ch)
    if (bucket) bucket.push(c)
    else byFirstChar.set(ch, [c])

    const aBucket = byAliasLower.get(c.aliasLower)
    if (aBucket) aBucket.push(c)
    else byAliasLower.set(c.aliasLower, [c])
  }

  const out: Array<{ text: string; term?: LegalTerm }> = []
  let start = 0
  let cursor = 0
  while (cursor < text.length) {
    const bucket = byFirstChar.get(lower[cursor])
    const hit = bucket?.find((c) => {
      if (!lower.startsWith(c.aliasLower, cursor)) return false
      if (c.wordBounded) {
        return (
          !/[\p{L}\p{N}]/u.test(text[cursor - 1] || '') &&
          !/[\p{L}\p{N}]/u.test(text[cursor + c.alias.length] || '')
        )
      }
      return true
    })
    if (!hit) {
      cursor++
      continue
    }
    if (cursor > start) out.push({ text: text.slice(start, cursor) })
    const sameAlias = byAliasLower.get(hit.aliasLower) || [hit]
    const alternatives = [
      ...new Map(
        sameAlias
          .filter((c) => c.prio === hit.prio)
          .map((c) => [
            `${(c.term.definition_zh || c.term.definition_original || '').trim()}|${(c.term.source_reference || '').replace(/\s*·\s*v\d+\b/gi, '')}`,
            c.term,
          ]),
      ).values(),
    ]
    out.push({
      text: text.slice(cursor, cursor + hit.alias.length),
      term: alternatives.length > 1 ? { ...hit.term, alternatives } : hit.term,
    })
    cursor += hit.alias.length
    start = cursor
  }
  if (start < text.length) out.push({ text: text.slice(start) })
  return out
}
