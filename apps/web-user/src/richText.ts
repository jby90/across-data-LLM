// 报告文字里的加粗标记：只认 **重点** 这一种，和后端 rich_text.py 的规则一致。
// 任何情况下返回的片段拼起来都不含 `**`；未配对的标记当作普通字符去掉，不会让整段变粗。

export interface Segment {
  text: string
  bold: boolean
}

export function parseBold(input: string): Segment[] {
  const text = input ?? ''
  const out: Segment[] = []
  const re = /\*\*([\s\S]+?)\*\*/g
  let pos = 0
  for (let m = re.exec(text); m; m = re.exec(text)) {
    if (m.index > pos) out.push({ text: text.slice(pos, m.index), bold: false })
    if (m[1].trim()) out.push({ text: m[1], bold: true })
    pos = m.index + m[0].length
  }
  if (pos < text.length) out.push({ text: text.slice(pos), bold: false })
  return out
    .map((s) => ({ ...s, text: s.text.replaceAll('**', '') }))
    .filter((s) => s.text.length > 0)
}

export function stripBold(input: string): string {
  return parseBold(input)
    .map((s) => s.text)
    .join('')
}
