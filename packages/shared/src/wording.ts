/**
 * 全站统一用词（第 7.5 项二轮 规则 10）。两端界面上同一件事只用一种说法，新写界面文字先查这里。
 * 只管“怎么叫”，不管业务结论：缺几项、要不要做，仍以后端给的为准。
 */
export const WORDING = {
  /** 缺项：一律“还缺 N 项”，不写“尚缺 / 还差 / 未填 N 项” */
  missing: (n: number, what = '') => `还缺 ${n} 项${what}`,
  /** 进入填写：已开始的叫“继续填写”，没开始的叫“开始准备” */
  continueFill: '继续填写',
  startPrepare: '开始准备',
  /** 有前提需补充信息后判定的（出境路径、适用范围）：叫“补充信息” */
  answerFirst: '补充信息',
  /** 已经建了项目的事项 */
  alreadyPreparing: '已在准备',
  /** 打开某个已有对象 */
  view: '查看',
} as const

/** 状态只用 5 种色（与 statusTone 一致）：灰 未开始 / 蓝 进行中 / 橙 需要你处理 / 绿 完成 / 红 只给失败或逾期 */
export const STATUS_WORDS = {
  muted: ['未开始', '未填写', '未生成'],
  active: ['填写中', '准备中', '已递交', '已在准备'],
  warn: ['待补正', '补正中', '即将到期', '需重新生成', '等出境路径', '待判断'],
  done: ['已就绪', '已生成', '已填好', '通过', '已取得批文'],
  danger: ['未通过', '已过期', '失败'],
} as const

/** 后端给的状态句里还有“还差”的旧说法：显示时一律改成词表里的“还缺”（只改显示，数字不动） */
export function missingWording(text: string | null | undefined): string {
  return (text || '').replace(/还差/g, '还缺').replace(/尚缺/g, '还缺')
}
