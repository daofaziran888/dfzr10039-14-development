/** 纯前端数据层的公共类型：与全栈版后端返回的结构保持一致，换回后端时页面不用改。 */

export type EntryRow = {
  id: number
  status: string
  pending: boolean
  abnormal: boolean
  [field: string]: string | number | boolean
}

export type ModuleMeta = {
  key: string
  name: string
  entity: string
  desc: string
  fields: string[]
  statuses: string[]
  actions: string[]
  actionTargets: Record<string, string>
  metrics: string[]
}

export type PageResult = {
  items: EntryRow[]
  total: number
  page: number
  size: number
}

export type ActionResult = {
  ok: boolean
  message: string
}

export type OverviewResult = {
  cards: { label: string; value: number }[]
  modules: { name: string; created: number; pending: number; abnormal: number }[]
}

export type TrialField = {
  field: string
  xpath: string
  present: boolean
  raw_value: string | null
  converted_value: string | number | boolean | null
  value_type: string
  problems: { code: string; message: string }[]
}

export type TrialSpecimen = {
  specimen_id: string
  trace_id: string
  run_id: string
  candidate_id: string
  candidate_name: string
  page_id: string
  page_group: string
  page_title: string
  page_revision: number
  applicable: boolean
  state: string
  signed_at: string | null
  signed_by: string | null
  problem_count: number
  problems: { code: string; message: string }[]
  rule_digest: string
  fields?: Record<string, TrialField>
}

export type TrialPageGroup = {
  page_group: string
  specimen_count: number
  out_of_scope_count: number
  signed_count: number
  deferred_count: number
  eligible: boolean
  status: string
  pages: { page_id: string; signed: number; deferred: number; out_of_scope: number; reasons: string[] }[]
}

export type TrialAcceptance = {
  acceptance_id: string
  run_id: string
  sample_group: string
  operator: string
  note: string
  confirmed_at: string
  received_count: number
  received: { specimen_id: string; trace_id: string; candidate_id: string; page_id: string; page_group: string; page_revision: number; state: string }[]
  eligible_tests: { test_id: string; trace_id: string; candidate_id: string; page_id: string; page_group: string; page_revision: number; publish_ready: boolean }[]
  deferred: { specimen_id: string; trace_id: string; candidate_id: string; page_id: string; page_group: string; page_revision: number; reasons: string[]; problems: { code: string; message: string }[] }[]
  page_groups: TrialPageGroup[]
  conflicts: { code: string; detail: string; page_id?: string; page_group?: string; candidate_id?: string }[]
  not_covered: { candidate_id: string; page_id: string; reason: string }[]
  pending_retry: { candidate_id: string; page_id: string; reason: string }[]
  formal_writes: boolean
  run_state_at_confirm: { status: string; complete: boolean; produced_specimens: number; expected_specimens: number }
}

export type TrialRun = {
  run_id: string
  sample_group: string
  candidate_ids: string[]
  page_ids: string[]
  operator: string
  status: string
  complete: boolean
  interrupted: boolean
  produced_specimens: number
  expected_specimens: number
  retry_count: number
  pending_combos: [string, string][]
  created_at: string
  updated_at: string
}

export type TrialCandidate = {
  candidate_id: string
  name: string
  target_site: string
  applicable_page_groups: string[]
  field_mapping: Record<string, string>
  field_types: Record<string, string>
  date_formats: string[]
  description: string
}

export type TrialSnapshot = {
  page_id: string
  page_group: string
  title: string
  url: string
  nodes: Record<string, string>
  revision: number
  revisions: { revision: number; nodes: Record<string, string>; changed_at: string; note: string }[]
}

export type TrialState = {
  candidates: TrialCandidate[]
  snapshots: TrialSnapshot[]
  runs: TrialRun[]
  acceptances: TrialAcceptance[]
  formal_clean_rules: { 规则编号: string; 目标字段: string; 清洗类型: string; 处理表达式: string; 规则状态: string }[]
  formal_unchanged: boolean
  formal_detail: { changed_modules: string[] }
  trial_writes_formal: boolean
}

export type TrialReport = {
  run: TrialRun
  page_count: number
  specimen_count: number
  pages: {
    page_id: string
    page_group: string
    title: string
    url: string
    revision: number
    specimens: TrialSpecimen[]
  }[]
  problem_totals: Record<string, number>
  state_totals: Record<string, number>
  formal_writes: boolean
}

export type CleaningManifestItem = {
  specimen_id: string
  trace_id: string
  candidate_id: string
  page_id: string
  page_group: string
  page_revision: number
  field: string
  xpath: string
  raw_value: string | null
  converted_value: string | number | boolean | null
  value_type: string
  problems: { code: string; message: string }[]
  state: string
}

export type CleaningManifest = {
  run_id: string
  sample_group: string
  source: string
  synced_at: string
  items: CleaningManifestItem[]
  field_type_summary: Record<string, string[]>
  formal_clean_rules: TrialState['formal_clean_rules']
  formal_writes: boolean
}

export type TrialRetryResult = {
  run: TrialRun
  report: TrialReport
  replaced: {
    specimen_id: string
    old_trace_id: string
    new_trace_id: string
    candidate_id: string
    page_id: string
    old_revision: number
    new_revision: number
    problem_count: number
  }[]
  kept_signed: {
    specimen_id: string
    trace_id: string
    candidate_id: string
    page_id: string
    page_revision: number
    signed_at: string | null
  }[]
  formal_writes: boolean
}

export type TrialHistory = {
  run: TrialRun
  specimens: TrialSpecimen[]
  history: {
    specimen_id: string
    trace_id: string
    replaces: string | null
    history: {
      action?: string
      reason?: string
      operator?: string
      note?: string
      at?: string
      superseded_at?: string
      acceptance_id?: string
      trace_id?: string
      page_revision?: number
      specimen?: TrialSpecimen
      superseded_by?: string
    }[]
  }[]
  acceptances: TrialAcceptance[]
}
