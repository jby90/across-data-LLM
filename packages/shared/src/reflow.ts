/**
 * Text taken from PDF gazettes keeps the printed line breaks: a sentence is broken every
 * 60–80 characters, and line numbers of the gazette page (“5”, “30”) sit on lines of their own.
 * For reading, lines that continue a sentence are joined again; a new item of a list, a new
 * paragraph, a heading ending in a colon and a short title line keep their own line.
 * Only the display changes; the stored text and the quotes cited from it are untouched.
 */
const ITEM =
  /^\s*(?:[a-zA-Z0-9]{1,4}[.)]\s|\((?:[a-zA-Z]{1,4}|[0-9]{1,3}(?!\s*[a-zà-ÿ]))\)|[-–—−]\s*(?=[A-Za-zÀ-ÿ0-9«"“\u4e00-\u9fff])|[•·▪]\s*|\d+(?:\.\d+)+\s|[（(][一二三四五六七八九十0-9]{1,3}[)）]|[一二三四五六七八九十]+[、.]|第\s*[零〇一二三四五六七八九十百0-9]+[A-Za-z]*\s*(?:部分|小节|附表|[编章节篇条款项])|附件\s*[零〇一二三四五六七八九十百0-9A-Za-z]+|(?:PART|Part|Chapitre|CHAPITRE|Sous-section|SOUS-SECTION|Section|SECTION|ANNEXE|Annexe)\b|\[)/
const END_LATIN = /[.;:!?—]\s*$|(?:[.;][)\]]|\.\d{4})\s*$/
const END_CJK = /[。；：！？）」』】]\s*$/
const SEPARATOR = /^\s*(?:\*\s*)+\s*$/
const MARGIN_NUMBER = /^\s*\d{1,3}\s*$/
const RUNNING_HEADER =
  /^\s*(?:BULLETIN\s+OFFICIEL|JOURNAL\s+OFFICIEL(?:\s+DE\s+LA\s+R[ÉE]PUBLIQUE\s+[A-ZÀ-Ÿ]+)?|GOVERNMENT\s+GAZETTE|官方公报)\s*$/i
const CJK = /[㐀-鿿　-〿＀-￯]/ // 汉字和中文标点

export function reflow(text: string): string {
  if (!text || !text.includes('\n')) return text || ''
  const lines = text.split('\n').filter((l) => !MARGIN_NUMBER.test(l) && !RUNNING_HEADER.test(l))
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
    if (ended || ITEM.test(line) || shortTitle || shortCjk || caps(line) || caps(prev)) {
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
    .replace(
      /(?<=[A-Za-zÀ-ÿ]{3}|[\u4e00-\u9fff]{2})\s*([：:—–]|——)\s*(?=[（(](?:[0-9]{1,2}|[a-zA-Z]|[一二三四五六七八九十]{1,3})[)）]\s*[^\s)）])/g,
      '$1\n',
    )
    .replace(
      /(?<=[A-Za-zÀ-ÿ]{3}|[\u4e00-\u9fff]{2})\s*(——|––|--|—)\s*(?=[“"«][^”"»\n]{1,50}[”"»]\s*(?:指|是指|包括|具有|means\b|désigne\b|signifie\b))/gi,
      '$1\n',
    )
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

function anchorKey(line: string): string | null {
  const s = line.trim()
  if (/^(?:\*\s*)+$/.test(s)) return `sep:${s.replace(/\s+/g, '')}`
  // 条号后紧跟首款编号（如 “8.––(1) Subject...” 或 “第8条 (1) 除...”），统一归一为首款锚点 p:1
  let m =
    /^(?:(?:Art(?:icle)?|ART|Reg(?:ulation)?|Section)\.?\s*\d+[A-Za-z]*|\d+[A-Za-z]*\.|第\s*[0-9零〇一二三四五六七八九十百]+\s*条)\s*(?:––|--|——|[.:—–\-：])*\s*[（(](1|一|a|A)[)）](?:\s|$|[^\w])/.exec(
      s,
    )
  if (m) {
    return `p:${normOrd(m[1])}`
  }
  // 内嵌章节/小节/附件标题锚点
  m = /^(?:Chapitre\s+(premier|[IVXLCDM]+|\d+)|PART\s+([IVXLCDM]+|\d+)|第\s*([0-9零〇一二三四五六七八九十百]+)\s*[章编])(?:\b|\s|$|[.:—–\-：])/i.exec(s)
  if (m) return `chap:${normOrd(m[1] || m[2] || m[3])}`
  m = /^(?:Sous-section\s+(premi[eè]re|1er|\d+)|第\s*([0-9零〇一二三四五六七八九十百]+)\s*小节)(?:\b|\s|$|[.:—–\-：])/i.exec(s)
  if (m) return `subsec:${normOrd(m[1] || m[2])}`
  m = /^(?:Section\s+(premi[eè]re|1er|[IVXLCDM]+|\d+)|第\s*([0-9零〇一二三四五六七八九十百]+)\s*节)(?:\b|\s|$|[.:—–\-：])/i.exec(s)
  if (m) return `sec:${normOrd(m[1] || m[2])}`
  m = /^(?:ANNEXE\s*(?:n°\s*)?([IVXLCDM]+|\d+|l)|附件\s*([0-9零〇一二三四五六七八九十百]+))(?:\b|\s|$|[.:—–\-：])/i.exec(s)
  if (m) {
    const raw = (m[1] || m[2] || '').replace(/^l$/, '1')
    return `annex:${normOrd(raw)}`
  }
  // 内嵌条号锚点（ART. 2 / Article 3 / الفصل (19) / 第2条 / 第 46 条。）
  m =
    /^(?:(?:Art(?:icle)?|ART)\.?\s*(premier|1er|\d+)(?:\s*(?:bis|ter|quater))?|الفصل\s*(الأول|\(\d+\)|\d+)|第\s*([0-9零〇一二三四五六七八九十百]+)\s*条(?:\s*之[一二三四五六七八九十])?)(?:\s|[.:。—–\-：،,]|(?=[\u4e00-\u9fff]))/i.exec(
      s,
    )
  if (m) {
    return `art:${normOrd(m[1] || m[2] || m[3])}`
  }
  // (1), （1）, (a), （a）, （一） — 排除法语数字单位如 “(3) ans.”
  m = /^[（(]([0-9]{1,2}|[a-zA-Z]|[ivxlcdmIVXLCDM]{1,5}|[一二三四五六七八九十]{1,3})[)）](?:\s|$|[^\w])/.exec(s)
  if (m && !(/^\d+$/.test(m[1]) && /^\([0-9]{1,2}\)\s*[a-zà-ÿ]/.test(s))) {
    return `p:${normOrd(m[1])}`
  }
  // 1., 1. –, 1), 1°, 1-, 一、
  m = /^([0-9]{1,2})(?:[.)°]\s*(?:[-–—−]\s*)?|[-–—−]\s*)(?=\S)/.exec(s)
  if (m) return `n:${m[1]}`
  m = /^([一二三四五六七八九十]{1,3})[、.]/.exec(s)
  if (m) return `n:${normOrd(m[1])}`
  // Bullet -, •, ▪, −
  if (/^(?:[-–—−•·▪])\s*\S/.test(s)) return 'b:*'
  // Quoted legal definition “xxx”指 / "xxx" means
  if (/^[“"«][^”"»]{1,55}[”"»]\s*(?:，|,)?\s*(?:就|在)?[^。；;\n]{0,30}(?:指|是指|包括|具有|means\b|includes\b|has the meaning\b|désigne\b|s'entend\b)/i.test(s)) {
    return 'def:*'
  }
  return null
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
  const keysA = a.map(anchorKey)
  const keysB = b.map(anchorKey)
  const countA = new Map<string, number>()
  const countB = new Map<string, number>()
  for (const k of keysA) if (k) countA.set(k, (countA.get(k) || 0) + 1)
  for (const k of keysB) if (k) countB.set(k, (countB.get(k) || 0) + 1)

  const anchors: [number, number][] = []
  let biStart = 0
  for (let ai = 0; ai < a.length; ai++) {
    const ka = keysA[ai]
    if (!ka || ka === 'b:*' || ka === 'def:*') continue
    const uniqueBoth = countA.get(ka) === 1 && countB.get(ka) === 1
    for (let bi = biStart; bi < b.length; bi++) {
      if (keysB[bi] === ka) {
        const relDiff = Math.abs(ai / a.length - bi / b.length)
        if (uniqueBoth || relDiff <= 0.22) {
          anchors.push([ai, bi])
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
