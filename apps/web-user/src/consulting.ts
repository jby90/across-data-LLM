// 咨询版报告的接口结构；字段含义见 backend/app/analysis/consulting.py

export const ROADMAP_DIMENSION = 'D8' // 第八个方面整改建议与落地执行方案本身就是路线图

export const PHASE_LABELS: Record<number, string> = {
  1: '第一阶段：30 天内',
  2: '第二阶段：31–60 天',
  3: '第三阶段：61 天以后'
}

export interface Assessment {
  id: string
  requirement: string
  business_state: string
  status: 'compliant' | 'partial' | 'non_compliant' | 'insufficient_info'
  level: 'high' | 'medium' | 'low' | 'unknown'
  rationale: string
  needs_fact: string
  ref_numbers: number[]
}

export interface Action {
  id: string
  priority?: number
  phase?: number
  depends_on?: string[]
  category: string
  title: string
  action: string
  owner_role: string
  due_in_days: number
  done_criteria: string
  addresses: string[]
}

export interface ConsultingDimension {
  code: string
  title: string
  status: 'ok' | 'no_evidence' | 'failed'
  note: string
  conclusion: string
  overview?: string
  assessments: Assessment[]
  actions: Action[]
}

export interface Reference {
  n: number
  clause_id: string | null
  document_title: string
  clause_number: string
  version_no: number
  source_url: string | null
  quote_original: string
  source_review_status?: string
  translation_review_status?: string
  source_notice?: string
  points?: string[]
  /** 咨询层用自己的话写的一句话说明（旧报告没有） */
  summary?: string
}

export interface ConclusiveTier {
  option: string
  mark: string
  basis: string
}

export interface ConclusiveMatrix {
  counts: { high: number; medium: number; low: number; unknown: number }
  tiers: ConclusiveTier[]
  closure_rule: string
}

export interface Consulting {
  status: 'ok' | 'partial' | 'failed'
  failure_reason: string
  generated_at?: string | null
  created_at?: string | null
  verdict: { label: string; headline: string; summary: string; must_do_action_ids: string[] }
  conclusive_matrix?: ConclusiveMatrix
  top_risks: Array<{ assessment_id: string; title: string; level: string; ref_numbers: number[] }>
  dimensions: ConsultingDimension[]
  roadmap: Action[]
  roadmap_intro?: string
  references: Reference[]
  edited?: { dimensions: string[]; executive_summary: boolean }
}
