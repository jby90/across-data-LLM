/**
 * 全站状态标签的色调（第 7.5 项 布局规则 14–16，出处见 内部交接/布局规则_7.5.md）。
 * 同一个状态在任何页面同一种颜色：
 *   muted   灰：还没开始 / 不能开始
 *   active  蓝：进行中、要你做
 *   warn    橙：要注意
 *   danger  红：只给出错、未通过、过期
 *   done    绿字无底色：已完成
 *   neutral 浅灰字无底色：仅作说明的状态（展示中、已读）
 * 页面用法：<span :class="['cy-status', statusClass(text)]">{{ text }}</span>
 * 状态词本身不在这里改；这里只决定颜色。
 */
export type StatusTone = 'muted' | 'active' | 'warn' | 'danger' | 'done' | 'neutral'

/** 状态词 → 色调。按“词里含有”匹配，长词优先（“未通过”先于“通过”，“需重新生成”先于“已生成”） */
export const STATUS_TONE: Record<string, StatusTone> = {
  // 灰：还没开始
  未开始: 'muted',
  还没开始: 'muted',
  未填写: 'muted',
  未生成: 'muted',
  不能开始: 'muted',
  已停用: 'muted',
  // 蓝：进行中
  填写中: 'active',
  准备中: 'active',
  进行中: 'active',
  生成中: 'active',
  待补充: 'active',
  已递交: 'active',
  已在准备: 'active',
  // 橙：要注意
  待补正: 'warn',
  补正中: 'warn',
  即将到期: 'warn',
  需重新生成: 'warn',
  待更新: 'warn',
  等出境路径: 'warn',
  待判断: 'warn',
  还差: 'warn',
  还缺: 'warn',
  // 红：出错
  缺失: 'danger',
  未通过: 'danger',
  已过期: 'danger',
  失败: 'danger',
  // 绿：已完成
  已就绪: 'done',
  已生成: 'done',
  已填好: 'done',
  已填写: 'done',
  已齐: 'done',
  通过: 'done',
  已取得批文: 'done',
  有效: 'done',
  已完成: 'done',
  已核对: 'done',
  已确认: 'done',
  // 说明性
  展示中: 'muted',
  已读: 'muted',
  范围外: 'muted',
}

const KEYS_LONGEST_FIRST = Object.keys(STATUS_TONE).sort((a, b) => b.length - a.length)

/** 状态文字的色调；认不出的按灰色（还没开始）处理 */
export function statusTone(text: string | null | undefined): StatusTone {
  const t = (text || '').trim()
  if (!t) return 'muted'
  if (STATUS_TONE[t]) return STATUS_TONE[t]
  for (const k of KEYS_LONGEST_FIRST) if (t.includes(k)) return STATUS_TONE[k]
  return 'muted'
}

/** 对应 theme.css 里的 class：cy-status--muted / --active / --warn / --danger / --done / --neutral */
export function statusClass(text: string | null | undefined): string {
  return `cy-status--${statusTone(text)}`
}

/** 仍用 el-tag 的地方：色调 → el-tag 的 type（done、neutral 建议改用 cy-status，去掉底色） */
export const TONE_TAG_TYPE: Record<StatusTone, 'info' | 'primary' | 'warning' | 'danger' | 'success'> = {
  muted: 'info',
  active: 'primary',
  warn: 'warning',
  danger: 'danger',
  done: 'success',
  neutral: 'info',
}
