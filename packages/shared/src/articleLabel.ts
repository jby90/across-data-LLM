/**
 * How an article number reads in Chinese: “43” → “第43条”, “premier”/“1er” → “第1条”,
 * “الأولى” / “الحادية عشرة” / “(27)” → “第1条” / “第11条” / “第27条”. Section-style numbers
 * (“19.8”, “26D”, “2.1”) are kept as printed; anything not recognised is returned unchanged.
 * The same rule is in backend app/knowledge/reading.py (article_label).
 */
const AR_UNITS: Record<string, number> = {
  'الأولى': 1, 'الأول': 1, 'الحادية': 1, 'الحادي': 1, 'الثانية': 2, 'الثاني': 2, 'الثالثة': 3, 'الثالث': 3,
  'الرابعة': 4, 'الرابع': 4, 'الخامسة': 5, 'الخامس': 5, 'السادسة': 6, 'السادس': 6, 'السابعة': 7,
  'السابع': 7, 'الثامنة': 8, 'الثامن': 8, 'التاسعة': 9, 'التاسع': 9, 'العاشرة': 10, 'العاشر': 10,
}
const AR_TENS: Record<string, number> = {
  'العشرون': 20, 'الثلاثون': 30, 'الأربعون': 40, 'الخمسون': 50, 'الستون': 60, 'السبعون': 70,
  'الثمانون': 80, 'التسعون': 90, 'المائة': 100, 'المئة': 100,
}

function arabicOrdinal(text: string): number | null {
  const words = text
    .replace(/[()（）:：.]/g, ' ')
    .trim()
    .split(/\s+/)
    .map((w) => (w.startsWith('وال') ? w.slice(1) : w))
  let total = 0
  let seen = false
  for (const w of words) {
    if (!w) continue
    if (w in AR_UNITS) { total += AR_UNITS[w]; seen = true }
    else if (w === 'عشرة' || w === 'عشر') { total += 10; seen = true }
    else if (w in AR_TENS) { total += AR_TENS[w]; seen = true }
    else return null
  }
  return seen ? total : null
}

export function articleLabel(raw: string | null | undefined): string {
  // 阿拉伯-印度数字（“٥١”）按普通数字读
  const value = (raw || '').trim().replace(/[٠-٩]/g, (d) => String(d.charCodeAt(0) - 0x0660)).replace(/[۰-۹]/g, (d) => String(d.charCodeAt(0) - 0x06f0))
  if (!value) return ''
  if (/^(premier|1er|first)$/i.test(value)) return '第1条'
  if (/^(وحيدة|unique|sole)\s*[:：]?$/i.test(value)) return '单一条款'
  const plain = value.replace(/^[(（]\s*|\s*[)）]$/g, '')
  if (/^\d{1,4}[A-Za-z]?$/.test(plain) && (plain === value || /^[(（]/.test(value))) return `第${plain}条`
  if (/^[一二三四五六七八九十百零]+$/.test(value)) return `第${value}条`
  const ar = /[؀-ۿ]/.test(value) ? arabicOrdinal(value) : null
  if (ar) return `第${ar}条`
  return value
}
