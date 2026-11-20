/**
 * Text taken from PDF gazettes keeps the printed line breaks: a sentence is broken every
 * 60–80 characters, and line numbers of the gazette page (“5”, “30”) sit on lines of their own.
 * For reading, lines that continue a sentence are joined again; a new item of a list, a new
 * paragraph, a heading ending in a colon and a short title line keep their own line.
 * Only the display changes; the stored text and the quotes cited from it are untouched.
 */
const CROSSREF_CONT =
  /^\s*(?:[（(][a-zA-Z0-9]{1,4}[)）]\s*(?:or|and|to|et|ou|à|,|、|或|及|以及)\s*[（(][a-zA-Z0-9]{1,4}[)）]|\([A-Z]\)\s+(?:under|in|of|for|by|with|from|is|are|was|has|have|must|may|shall|who|whom|whose|that|which|where|when)\b)/i
const FR_UNIT_AFTER_NUM =
  /^\([0-9]{1,2}\)\s*(?:ans?|mois|jours?|heures?|minutes?|semaines?|pour\s+cent|dirhams?|dinars?|livres?|euros?|dollars?|tonnes?|essieux|roues|places|exemplaires|fois)\b/i

const ITEM =
  /^\s*[“"«‘']?\s*(?:[a-zA-Z0-9]{1,4}[.)]\s|\d{1,3}[A-Za-z]{0,3}\.\s*(?:—+|–+|--+)|[（(](?:[a-zA-Z]{1,4}|[0-9]{1,3}[A-Za-z]{0,2}|[一二三四五六七八九十]{1,3})[)）]|[-–—−]\s*(?=[A-Za-zÀ-ÿ0-9«"“\u4e00-\u9fff])|[•·▪]\s*|\d+(?:\.\d+)+\s|[一二三四五六七八九十]+[、.]|第\s*[零〇一二三四五六七八九十百0-9]+[A-Za-z]*\s*(?:部分|分部|小节|附表|[编章节篇条款项])|附件\s*[零〇一二三四五六七八九十百0-9A-Za-z]+|(?:PART|Division|DIVISION|Chapitre|CHAPITRE|Sous-section|SOUS-SECTION|Section|SECTION|Paragraphe|PARAGRAPHE|ANNEXE|Annexe)\b|\[)/
const END_LATIN = /[.;:!?—]\s*$|(?:[.;][)\]]|\.\d{4})\s*$/
const END_CJK = /[。；：！？）」』】]\s*$/
const SEPARATOR = /^\s*(?:\*\s*)+\s*$/
const MARGIN_NUMBER = /^\s*\d{1,3}\s*$/
const RUNNING_HEADER =
  /^\s*(?:BULLETIN\s+OFFICIEL|JOURNAL\s+OFFICIEL(?:\s+DE\s+LA\s+R[ÉE]PUBLIQUE\s+[A-ZÀ-Ÿ]+)?|GOVERNMENT\s+GAZETTE|官方公报)\s*$/i
const CJK = /[㐀-鿿　-〿＀-￯]/ // 汉字和中文标点

const LEFT_MARGIN_MULTIPLE_OF_5 =
  /^(\s*)(?:[5]|1[05]|2[05]|3[05]|4[05]|5[05])(?:\s+(?=[“"«‘']?(?:[（(][0-9a-zA-Z]{1,4}[)）](?:\s|(?=[\u4e00-\u9fff“"«]))|第\s*[（(]?[0-9]+[A-Za-z]*[)）]?\s*[条款]|\d{1,3}[A-Za-z]{0,3}[.。]\s*[—–-]))|\s*(?=（[0-9a-zA-Z]{1,4}）))/

export function reflow(text: string): string {
  if (!text || !text.includes('\n')) return text || ''
  const lines = text
    .split('\n')
    .filter((l) => !MARGIN_NUMBER.test(l) && !RUNNING_HEADER.test(l))
    .map((l) => l.replace(LEFT_MARGIN_MULTIPLE_OF_5, '$1'))
  const out: string[] = []
  for (const raw of lines) {
    const line = raw.replace(/\s+$/, '')
    const prev = out[out.length - 1]
    if (prev === undefined || !line.trim() || !prev.trim()) {
      out.push(line)
      continue
    }
    if (SEPARATOR.test(prev) && SEPARATOR.test(line)) {
      out[out.length - 1] = `${prev.trim()} ${line.trim()}`
      continue
    }
    if (SEPARATOR.test(prev) || SEPARATOR.test(line)) {
      out.push(line)
      continue
    }
    // 行尾的断字连字符（“informa-” + “tion.”）：两边都是小写字母时合回一个词，去掉连字符
    if (/[a-z]-$/.test(prev) && /^[a-z]/.test(line.trim())) {
      out[out.length - 1] = prev.slice(0, -1) + line.trim()
      continue
    }
    const cjk = CJK.test(prev.slice(-1)) || CJK.test(line.trim().charAt(0))
    // 独立成行的连接词（如分号换行后的 “and” / “or”）或跨行残尾（“第(1)(b)段所述。”）并回上一段，防止错位占行
    if (
      /^\s*(?:and|or|et|ou)\s*$/i.test(line) ||
      /^第\s*[（(][0-9]+[)）](?:[（(][a-zA-Z0-9]+[)）])*[段款]所述[。.]?\s*$/.test(line.trim())
    ) {
      const g = cjk ? '' : ' '
      out[out.length - 1] = prev + g + line.trim()
      continue
    }
    const ended = cjk ? END_CJK.test(prev) || END_LATIN.test(prev) : END_LATIN.test(prev)
    const shortTitle = !cjk && prev.trim().length < 40 && !/[a-z,]$/.test(prev.trim())
    const shortCjk =
      cjk &&
      prev.trim().length <= 28 &&
      !/^\s*(?:[（(][a-zA-Z0-9ivxlcdmIVXLCDM]+[)）]|[-–—−•·▪])/.test(prev)
    const caps = (t: string) =>
      /^[A-ZÀ-Ÿ0-9\s\-–—.:()°']+$/.test(t.trim()) &&
      /[A-Z]{2,}/.test(t) &&
      !/[.;,]$/.test(t.trim()) // 全大写的标题行（PART II、目录项），排除带书名号或句末分号的跨行词组
    const isItemLine =
      ITEM.test(line) && !FR_UNIT_AFTER_NUM.test(line.trim()) && !CROSSREF_CONT.test(line.trim())
    if (ended || isItemLine || shortTitle || shortCjk || caps(line) || caps(prev)) {
      out.push(line)
      continue
    }
    const glue = cjk && CJK.test(prev.slice(-1)) && CJK.test(line.trim().charAt(0)) ? '' : ' '
    out[out.length - 1] = prev + glue + line.trim()
  }
  return out.join('\n')
}

const CN_DIGITS: Record<string, number> = {
  零: 0, 〇: 0, 一: 1, 二: 2, 两: 2, 三: 3, 四: 4,
  五: 5, 六: 6, 七: 7, 八: 8, 九: 9,
}

const ROMAN_VAL: Record<string, number> = {
  I: 1, V: 5, X: 10, L: 50, C: 100, D: 500, M: 1000,
}

function normOrd(raw: string): string {
  const s = (raw || '').trim().replace(/^[(\uff08]|[)\uff09]$/g, '').trim()
  if (!s) return s
  if (/^(?:premier|premi[eè]re|1er|1[eè]re|الأول|الأولى|first)$/i.test(s)) return '1'
  if (/^\d+$/.test(s)) return String(Number(s))
  if (/^[IVXLCDM]+$/i.test(s)) {
    const up = s.toUpperCase()
    let total = 0
    for (let i = 0; i < up.length; i++) {
      const cur = ROMAN_VAL[up[i]] || 0
      const nxt = ROMAN_VAL[up[i + 1]] || 0
      total += cur < nxt ? -cur : cur
    }
    if (total > 0) return String(total)
  }
  if (/^[零〇一二两三四五六七八九十百]+$/.test(s)) {
    let total = 0
    let cur = 0
    for (const ch of s) {
      if (ch === '百') {
        total += (cur || 1) * 100
        cur = 0
      } else if (ch === '十') {
        total += (cur || 1) * 10
        cur = 0
      } else {
        cur = CN_DIGITS[ch] ?? 0
      }
    }
    total += cur
    if (total > 0) return String(total)
  }
  return s.toLowerCase()
}

function splitInlineSubclauses(text: string): string {
  if (!text) return ''
  // 当译文或原文把导语破折号/冒号后的首个款项或定义连在同一行（如“除文意另有所指外——“上诉秘书”指…”、“符合下列条件：（一）…”）时拆开；
  // 绝不拆开条号前缀（如“8.––(1)”、“第8条——(1)”）
  return text
    .split('\n')
    .map((ln) =>
      /(?:第\s*[0-9零〇一二三四五六七八九十百]+[A-Za-z]*\s*条|\b\d{1,3}[A-Za-z]{0,3}\s*[.。]|\b\d{1,3}[A-Z]{1,3}\b)\s*(?:[^。\n；;：（(]{0,28}?[.。]?\s*)?(?:——|––|--|[—–.:：])+\s*[（(]1[)）]/.test(
        ln,
      )
        ? ln.replace(
            /(?<=[A-Za-zÀ-ÿ]{2}|[\u4e00-\u9fff]{2})\s*(——|[：:—–])\s*(?:(?:[5]|1[05]|2[05]|3[05]|4[05]|5[05])\s*)?(?=[（(](?:[a-zA-Z]|[一二三四五六七八九十]{1,3})[)）]\s*[^\s)）])/g,
            '$1\n',
          )
        : ln.replace(
            /(?<=[A-Za-zÀ-ÿ]{1}|[\u4e00-\u9fff]{1})\s*(——|[：:—–])\s*(?:(?:[5]|1[05]|2[05]|3[05]|4[05]|5[05])\s*)?(?=[（(](?:[0-9]{1,2}[A-Za-z]{0,2}|[a-zA-Z]|[一二三四五六七八九十]{1,3})[)）]\s*[^\s)）])/g,
            '$1\n',
          ),
    )
    .join('\n')
    .replace(
      /(?<=(?:insert|substitute|插入|取代|如下))\s*(——|[—–:])\s*(?=[“"«])/gi,
      '$1\n',
    )
    .replace(
      /([。！？；;])\s*(?:(?:[5]|1[05]|2[05]|3[05]|4[05]|5[05])\s+)?(?=(?:[（(][0-9]{1,2}[A-Za-z]{0,2}[)）]|第\s*[（(]?[0-9]{1,2}[A-Za-z]{0,2}[)）]?\s*款)(?:\s|(?=[\u4e00-\u9fff“"«A-Za-z]))(?![、,，;；]))/g,
      '$1\n',
    )
    .replace(
      /((?:；\s*(?:或|及|以及|和)?|;\s*(?:or|and)?))\s*(?:(?:[5]|1[05]|2[05]|3[05]|4[05]|5[05])\s+)?(?=[（(][a-z][)）](?:\s|(?=[\u4e00-\u9fffA-Za-z“"«]))(?![、,，;；]))/g,
      '$1\n',
    )
    .replace(
      /(?<=[A-Za-zÀ-ÿ]{3}|[\u4e00-\u9fff]{2})\s*(——|––|--|—)\s*(?=[“"«][^”"»\n]{1,50}[”"»]\s*(?:指|是指|包括|具有|means\b|désigne\b|signifie\b))/gi,
      '$1\n',
    )
    .split('\n')
    .map((ln) =>
      /^\s*[（(]1[)）]\s+\S/.test(ln) && /[（(]2[)）]\s+\S/.test(ln)
        ? ln.replace(/([,，;；])\s*(?=[（(][2-9][)）]\s+\S)/g, '$1\n')
        : ln,
    )
    .join('\n')
}

const BARE_ARTICLE_STUB =
  /^(?:(?:Art(?:icle)?|ART|Reg(?:ulation)?)\.?\s*(?:premier|1er|\d+[A-Za-z]*)(?:\s*(?:bis|ter|quater))?|\d+[A-Za-z]*\.(?:––|—|--|–|-)?|الفصل\s*(?:الأول|\(?\d+\)?)|第\s*[0-9零〇一二三四五六七八九十百]+\s*条(?:\s*之[一二三四五六七八九十])?)\s*[.:—–\-]*$/i

const BARE_CHAPTER_STUB =
  /^(?:Chapitre\s+(?:premier|[IVXLCDM]+|\d+)|PART\s+(?:[IVXLCDM]+|\d+)|第\s*[0-9零〇一二三四五六七八九十百]+\s*[章编])\s*[.:—–\-]*$/i

function glueBareArticleStubs(lines: string[]): string[] {
  const out: string[] = []
  for (let i = 0; i < lines.length; i++) {
    const cur = lines[i]
    if (BARE_ARTICLE_STUB.test(cur) && i + 1 < lines.length) {
      out.push(`${cur} ${lines[i + 1]}`)
      i += 1
    } else if (BARE_CHAPTER_STUB.test(cur) && i + 1 < lines.length && !anchorKey(lines[i + 1])) {
      out.push(`${cur} — ${lines[i + 1]}`)
      i += 1
    } else {
      out.push(cur)
    }
  }
  return out
}

function parasFromReflow(text: string): string[] {
  const rawLines = reflow(splitInlineSubclauses(text || ''))
    .split(/\n+/)
    .map((x) => x.trim())
    .filter(Boolean)
  return glueBareArticleStubs(rawLines)
}

const ABJAD_ORD: Record<string, string> = {
  أ: '1', ا: '1', ب: '2', ج: '3', د: '4', هـ: '5', ه: '5', و: '6', ز: '7', ح: '8', ط: '9', ي: '10',
}

const ARABIC_INDIC_DIGITS = '٠١٢٣٤٥٦٧٨٩'
function asciiDigits(raw: string): string {
  return (raw || '').replace(/[٠-٩]/g, (ch) => String(ARABIC_INDIC_DIGITS.indexOf(ch)))
}

function normSecId(raw: string): string {
  const s = asciiDigits((raw || '').trim())
    .replace(/^[(\uff08]|[)\uff09.]$/g, '')
    .replace(/\s*之二$/, 'bis')
    .replace(/\s*之三$/, 'ter')
    .replace(/\s*之四$/, 'quater')
    .replace(/[\s.\-–]+/g, '')
  const m = /^0*(\d+)([A-Za-z]*)$/.exec(s)
  if (m) return `${Number(m[1])}${m[2].toLowerCase()}`
  return normOrd(s)
}

function anchorKey(line: string): string | null {
  const rawTrim = line.trim()
  if (/^(?:\*\s*)+$/.test(rawTrim)) return `sep:${rawTrim.replace(/\s+/g, '')}`
  // 剥离修订法案引文开头的引号（如 “18L.—(1) 或 “第18L条 （1）），但保留词汇定义行 “xxx”指 的引号供下方 def:* 识别
  const s = /^[“"«‘']+\s*(?:(?:Art|Reg|Section|Chapitre|PART|\d+[A-Za-z]*[.。]|[A-Za-zÀ-ÿ\u4e00-\u9fff,.'’\-\s]{0,50}?(?:\d+[A-Za-z]*[.。]\s*[—–-]|第\s*[(\uff08]?[0-9零〇一二三四五六七八九十百])|[（(][0-9a-zA-Z一二三四五六七八九十]))/i.test(
    rawTrim,
  )
    ? rawTrim.replace(/^[“"«‘']+\s*/, '')
    : rawTrim

  // 条号/附表段号后紧跟首款编号（如 “8.––(1) Subject...” / “Civil penalty 37A.—(1)” / “仅限道路使用的PAB... 5 47H.—（1）” / “第110A条 关联司机的一般暂停——第(1)款”）
  // 严禁把以 (a)/(2) 开头的款项内交叉引用（如 “(e) 按照第24条在(1)(b)所述…”）误判为新条头！
  let m =
    /^(?![（(][0-9a-zA-Z一二三四五六七八九十]{1,4}[)）])([A-Za-zÀ-ÿ\u4e00-\u9fff（）(),.'’“”"—–/\-\s]{0,180}?\s*(?:(?:5|10|15|20|25|30|35)\s+)?)?(?:(?:Art(?:icle)?|ART|Reg(?:ulation)?|Section)\.?\s*(\d+[A-Za-z]*)|(\d+[A-Za-z]*)[.。]|(\d{1,3}[A-Z]{1,3})(?=\s*[\u4e00-\u9fff]{0,8}\s*(?:—+|–+|——))|第\s*[（(]?([0-9零〇一二三四五六七八九十百]+[A-Za-z]*(?:\s*(?:[-–]\s*bis\.?|之[一二三四]))?)[)）]?\s*条)\s*(?:[^。\n；;：（(]{0,28}?[.。]?\s*)?(?:––|--|——|[.:—–\-：])*\s*(?:[（(](1|一|a|A)[)）]|第\s*[（(]?(1|一)[)）]?\s*[款项])(?:\s|$|[^\w])/.exec(
      s,
    )
  if (
    m &&
    !/(?:根据|依据|按照|违反|遵守|所述|\b(?:under|pursuant\s+to|in\s+accordance\s+with)\s+(?:section|regulation|article|rule|subsection)\b)/i.test(
      m[1] || '',
    )
  ) {
    const sec = normSecId(m[2] || m[3] || m[4] || m[5])
    const subRaw = m[6] || m[7]
    const subKey = /^[aA]$/.test(subRaw) ? `alpha:${subRaw.toLowerCase()}` : 'sub:1'
    return `secsub:${sec}:${subKey}`
  }
  // 内嵌章节/小节/附件标题锚点（注意区分全大写标题 PART 3 与句中交叉引用 Part 3, 3A...）
  m = /^(?:(?:Chapitre|CHAPITRE)\s+(premier|PREMIER|[IVXLCDM]+|\d+)|PART\s+([IVXLCDM]+|\d+[A-Za-z]*)|第\s*([0-9零〇一二三四五六七八九十百]+[A-Za-z]*)\s*(?:章|编|部分))(?:\b|\s|$|[.:—–\-：])/.exec(s)
  if (m && !/[.;。；]$/.test(s) && !/\b(?:or|and|regardless|punishable|under)\b/i.test(s)) {
    return `chap:${normSecId(m[1] || m[2] || m[3])}`
  }
  m = /^(?:Sous-section\s+(premi[eè]re|1er|\d+)|Paragraphe\s+(premier|1er|\d+)|第\s*([0-9零〇一二三四五六七八九十百]+)\s*小节)(?:\b|\s|$|[.:—–\-：])/i.exec(s)
  if (m) return `subsec:${normOrd(m[1] || m[2] || m[3])}`
  m = /^(?:Section\s+(premi[eè]re|1er|[IVXLCDM]+|\d+)|第\s*([0-9零〇一二三四五六七八九十百]+)\s*节)(?:\b|\s|$|[.:—–\-：])/i.exec(s)
  if (m) return `sec:${normOrd(m[1] || m[2])}`
  m = /^(?:ANNEXE\s*(?:n°\s*)?([IVXLCDM]+|\d+|l)|附件\s*([0-9零〇一二三四五六七八九十百]+))(?:\b|\s|$|[.:—–\-：])/i.exec(s)
  if (m) {
    const raw = (m[1] || m[2] || '').replace(/^l$/, '1')
    return `annex:${normOrd(raw)}`
  }
  // 内嵌条号锚点（ART. 2 / Article 3 / Article (6-bis.) / 3. In this Part / الفصل (19) / 第48J条 / 第(2 – bis.)条）
  m =
    /^(?:(?:Art(?:icle)?|ART|Section|Reg(?:ulation)?)\.?\s*[（(]?(premier|1er|\d+[A-Za-z]*(?:[-\s–]*(?:bis|ter|quater))?)\.?[)）]?|(\d+[A-Za-z]*)[.。](?=\s+[A-Z“"\u4e00-\u9fff])|الفصل\s*(الأول|\(\d+\)|\d+)|المادة\s*(?:\(\d+\)|\d+)|第\s*[（(]?([0-9零〇一二三四五六七八九十百]+[A-Za-z]*(?:[-\s–]*(?:bis|ter|quater)\.?)?)[)）]?\s*条(?:\s*(?:之[一二三四五六七八九十]|bis))?)(?:\s|$|[.:。—–\-：،,]|(?=[\u4e00-\u9fff]))/i.exec(
      s,
    )
  if (m) {
    const rawArt = m[1] || m[2] || m[3] || m[4] || ''
    if (rawArt) return `art:${normSecId(rawArt)}`
  }
  // 中文显式“第N款 / 第(N)款 / 第4A款”与数字括号款号 (1), （1）, (4A), （4A）统一为 sub:N（严格与罗马字目 (i)/(ii) 隔离！）
  m = /^第\s*[（(]?([0-9零〇一二三四五六七八九十百]+[A-Za-z]{0,2})[)）]?\s*款(?:\s|$|[.:。—–\-：，,])/.exec(s)
  if (m) {
    return `sub:${normSecId(m[1])}`
  }
  m = /^[（(]([0-9٠-٩]{1,2}[A-Za-z]{0,2})[)）](?:\s+|$|(?=[\u4e00-\u9fff“"«]))(?![、,，;；.)）])/.exec(s)
  if (m && !FR_UNIT_AFTER_NUM.test(s)) {
    return `sub:${normSecId(m[1])}`
  }
  // 字母或罗马数字子项 (a), （a）, (i), (ii), (iv), (v) — 保持字面小写 alpha:，绝不转成数字与 (1)/(2)/(5)/(100) 碰撞！
  m = /^[（(]([a-zA-Z]{1,5})[)）](?:\s|$|[^\w])/.exec(s)
  if (m) {
    return `alpha:${m[1].toLowerCase()}`
  }
  // 中文（一）（二）与阿文 أ- ب- 分项
  m = /^[（(]([一二三四五六七八九十]{1,3})[)）](?:\s|$|[^\w])/.exec(s)
  if (m) return `orditem:${normOrd(m[1])}`
  m = /^(هـ|[أابجدهوزحطي])\s*[-–).]\s*(?=\S)/.exec(s)
  if (m && ABJAD_ORD[m[1]]) return `orditem:${ABJAD_ORD[m[1]]}`
  // 1., 1. –, 1), 1°, 1-, ١., 一、
  m = /^([0-9٠-٩]{1,2})(?:[.)°]\s*(?:[-–—−]\s*)?|[-–—−]\s*)(?=\S)/.exec(s)
  if (m) return `n:${normSecId(m[1])}`
  m = /^([一二三四五六七八九十]{1,3})[、.]/.exec(s)
  if (m) return `n:${normOrd(m[1])}`
  // Bullet -, •, ▪, −
  if (/^(?:[-–—−•·▪])\s*\S/.test(rawTrim)) return 'b:*'
  // Quoted legal definition “xxx”指 / "xxx" means
  if (/^[“"«][^”"»]{1,55}[”"»]\s*(?:，|,)?\s*(?:就|在)?[^。；;\n]{0,30}(?:指|是指|包括|具有|means\b|includes\b|has the meaning\b|désigne\b|s'entend\b)/i.test(rawTrim)) {
    return 'def:*'
  }
  return null
}

function isResetKey(k: string | null): boolean {
  if (!k) return false
  return k === 'sub:1' || k.startsWith('secsub:') || k.startsWith('art:') || k.startsWith('chap:')
}

function normalizeMatchKey(raw: string | null): string | null {
  if (!raw) return null
  if (raw.startsWith('secsub:')) {
    const [, , rest] = /^secsub:([^:]+):(.+)$/.exec(raw) || []
    return rest || 'sub:1'
  }
  return raw
}

function extractRawSecId(raw: string | null): string | null {
  if (!raw || !raw.startsWith('secsub:')) return null
  const m = /^secsub:([^:]+):/.exec(raw)
  return m ? m[1] : null
}

function scopedAnchorKeys(
  lines: string[],
  sharedSecs: Set<string>,
): { matchKeys: (string | null)[]; resets: boolean[] } {
  let secScope = ''
  let subScope = ''
  const useSecScope = sharedSecs.size >= 2
  const matchKeys: (string | null)[] = []
  const resets: boolean[] = []
  lines.forEach((ln, idx) => {
    const raw = anchorKey(ln)
    resets.push(idx > 0 && isResetKey(raw))
    const rawSec = extractRawSecId(raw)
    if (rawSec) {
      secScope = useSecScope && sharedSecs.has(rawSec) ? `sec:${rawSec}` : ''
    } else if (isResetKey(raw)) {
      secScope = ''
    }
    const mk = normalizeMatchKey(raw)
    if (!mk) {
      matchKeys.push(null)
      return
    }
    if (mk.startsWith('art:') || mk.startsWith('chap:')) {
      subScope = ''
      matchKeys.push(mk)
      return
    }
    if (mk.startsWith('sub:')) {
      subScope = secScope ? `${secScope}:${mk}` : mk
      matchKeys.push(subScope)
      return
    }
    if (mk.startsWith('alpha:') || mk.startsWith('orditem:') || mk.startsWith('n:')) {
      const pfx = subScope || secScope
      matchKeys.push(pfx ? `${pfx}:${mk}` : mk)
      return
    }
    matchKeys.push(mk)
  })
  return { matchKeys, resets }
}

/** 单调不跳桶分组：保证 1 <= k <= list.length 时返回的 k 个组每一组都非空，绝不产生空串或邻行错位 */
export function groupMonotone(list: string[], k: number): string[] {
  if (list.length === 0) return []
  if (k <= 1) return [list.join('\n')]
  if (list.length <= k) return [...list]
  const total = list.reduce((n, x) => n + x.length, 0) || 1
  const out: string[][] = Array.from({ length: k }, () => [])
  let acc = 0
  let g = 0
  for (let i = 0; i < list.length; i++) {
    const x = list[i]
    const remaining = list.length - i
    const bucketsAfter = k - 1 - g
    if (out[g].length > 0) {
      if (remaining <= bucketsAfter) {
        g += 1
      } else {
        const boundary = ((g + 1) / k) * total
        if (acc + x.length * 0.45 >= boundary && g < k - 1) {
          g += 1
        }
      }
    }
    out[g].push(x)
    acc += x.length
  }
  return out.map((o) => o.join('\n'))
}

function alignSpan(subA: string[], subB: string[]): [string, string][] {
  if (!subA.length && !subB.length) return []
  if (subA.length === subB.length) return subA.map((x, i) => [x, subB[i]])
  const k = Math.min(subA.length, subB.length, 96)
  if (k <= 1) return [[subA.join('\n'), subB.join('\n')]]
  const ga = groupMonotone(subA, k)
  const gb = groupMonotone(subB, k)
  return ga.map((x, i) => [x, gb[i]])
}

/** 原文与中文译文逐段结构对齐：保证只要双方均非空，绝不出现空行（“暂无中文对照”）或款项错行 */
export function alignBilingualParagraphs(
  origBody: string,
  zhBody: string,
  strippedHeading = '',
): [string, string][] {
  const cleanOrig = (origBody || '').trim()
  const cleanZh = (zhBody || '').trim()
  if (!cleanOrig || !cleanZh) return [[origBody || '', zhBody || '']]

  const a = parasFromReflow(cleanOrig)
  let b = parasFromReflow(cleanZh)
  if (!a.length || !b.length) return [[cleanOrig, cleanZh]]

  // 若原文已剥离条标题，而译文首行仍保留了独立短标题行，将译文首行并入下一段避免整体下移一行
  if (
    strippedHeading &&
    b.length === a.length + 1 &&
    b.length >= 2 &&
    b[0].length <= 55 &&
    !/[。；！？.;!?]$/.test(b[0]) &&
    !anchorKey(b[0])
  ) {
    b = [`${b[0]} ${b[1]}`, ...b.slice(2)]
  }

  // 即使 a.length === b.length，也必须先跑显式款项/章节/条号锚点对齐，防止前部多拆一行、尾部少拆一行时被盲目下标配对冲掉锚点
  // 提取两侧单调递增的显式款项锚点（仅采纳两侧唯一或相对位置邻近的同号锚点，防止嵌套子项跨段误配）
  const secCountA = new Map<string, number>()
  const secCountB = new Map<string, number>()
  for (const ln of a) {
    const sc = extractRawSecId(anchorKey(ln))
    if (sc) secCountA.set(sc, (secCountA.get(sc) || 0) + 1)
  }
  for (const ln of b) {
    const sc = extractRawSecId(anchorKey(ln))
    if (sc) secCountB.set(sc, (secCountB.get(sc) || 0) + 1)
  }
  const sharedSecs = new Set(
    [...secCountA.keys()].filter((sc) => secCountA.get(sc) === 1 && secCountB.get(sc) === 1),
  )

  const { matchKeys: keysA, resets: resetsA } = scopedAnchorKeys(a, sharedSecs)
  const { matchKeys: keysB, resets: resetsB } = scopedAnchorKeys(b, sharedSecs)
  const countA = new Map<string, number>()
  const countB = new Map<string, number>()
  for (const k of keysA) if (k) countA.set(k, (countA.get(k) || 0) + 1)
  for (const k of keysB) if (k) countB.set(k, (countB.get(k) || 0) + 1)

  const anchors: [number, number][] = []
  let prevAnchorA = -1
  let biStart = 0
  for (let ai = 0; ai < a.length; ai++) {
    const ka = keysA[ai]
    if (!ka || ka === 'b:*' || ka === 'def:*') continue
    const uniqueBoth = countA.get(ka) === 1 && countB.get(ka) === 1
    const isTopReset =
      ka === 'sub:1' || ka.endsWith(':sub:1') || ka.startsWith('art:') || ka.startsWith('chap:')
    const aCrossedReset =
      !isTopReset &&
      prevAnchorA >= 0 &&
      resetsA.slice(prevAnchorA + 1, ai).some(Boolean)
    for (let bi = biStart; bi < b.length; bi++) {
      if (!isTopReset && !aCrossedReset && bi > biStart && resetsB[bi]) {
        // 禁止低阶款项（如 (2)、(3)）跨过右侧新条文的 (1)/条头重置边界去误配下一条的同号款项！
        break
      }
      if (keysB[bi] === ka) {
        const relDiff = Math.abs(ai / a.length - bi / b.length)
        const isSubKey = ka.startsWith('sub:') || ka.includes(':sub:')
        const maxDiff = uniqueBoth
          ? Math.max(a.length, b.length) >= 12
            ? 0.35
            : 0.55
          : isSubKey
            ? 0.3
            : 0.18
        if (relDiff <= maxDiff) {
          anchors.push([ai, bi])
          prevAnchorA = ai
          biStart = bi + 1
        }
        break
      }
    }
  }

  if (!anchors.length) {
    return alignSpan(a, b)
  }

  const pairs: [string, string][] = []
  let prevA = 0
  let prevB = 0
  const stops: [number, number][] = [...anchors, [a.length, b.length]]
  for (const [endA, endB] of stops) {
    const segA = a.slice(prevA, endA)
    const segB = b.slice(prevB, endB)
    if (segA.length > 0 && segB.length > 0) {
      pairs.push(...alignSpan(segA, segB))
    } else if (segA.length > 0) {
      if (pairs.length > 0) {
        pairs[pairs.length - 1][0] += '\n' + segA.join('\n')
      } else if (endA < a.length) {
        a[endA] = segA.join('\n') + '\n' + a[endA]
      }
    } else if (segB.length > 0) {
      if (pairs.length > 0) {
        pairs[pairs.length - 1][1] += '\n' + segB.join('\n')
      } else if (endB < b.length) {
        b[endB] = segB.join('\n') + '\n' + b[endB]
      }
    }
    prevA = endA
    prevB = endB
  }
  return pairs.length ? pairs : [[cleanOrig, cleanZh]]
}
