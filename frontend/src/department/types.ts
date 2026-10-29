// Department review (ontology 4.0.0). Amounts are decimal strings; null means "unknown" or "redacted".
// Person-day economics is paused, so the review carries no revenue, margin or backlog figures.
export type ResourceHealthStatus = "overloaded" | "fatigue" | "fragmented" | "idle" | "healthy";
export type PlanStatus = "no_data" | "no_target" | "behind" | "on_track";
export type StrategySignalKey = "push_acceptance" | "control_cost" | "staffing_support" | "win_contracts" | "release_resources" | "hold";

export interface ResourceHealthRow {
  employee_id: string | null;
  employee_name: string;
  month: string;
  status: ResourceHealthStatus;
  statuses: ResourceHealthStatus[];
  reasons: string[];
  utilization_percent: number;
  consecutive_overload_months: number;
  overtime_share_percent: number;
  concurrent_project_count: number;
  first_month: boolean;
}

export interface ResourceHealthResponse {
  month: string;
  data: ResourceHealthRow[];
  summary: Partial<Record<ResourceHealthStatus, number>>;
}

export interface PlanCompletion {
  label: string;
  actual: string | null;
  target: string | null;
  completion_percent: number | null;
  time_progress_percent: number;
  lag_points?: number;
  status: PlanStatus;
}

export const PLAN_FIELDS = [
  "new_contract_target", "collection_target", "capacity_person_days_target", "headcount_target", "utilization_target_percent",
] as const;
export type PlanField = typeof PLAN_FIELDS[number];

export type AnnualPlan = Record<PlanField, string | null> & {
  plan_id: string;
  year: number;
  department: string;
  notes: string | null;
  updated_by: string | null;
  updated_at: string;
};

export interface StrategySignal {
  signal: StrategySignalKey;
  label: string;
  priority: number;
  evidence: string[];
}

export type KpiKey = "new_contracts" | "collections" | "capacity_used" | "utilization" | "headcount";

export interface DepartmentReview {
  year: number;
  month: string;
  plan: AnnualPlan | null;
  kpis: Record<KpiKey, PlanCompletion>;
  utilization_by_month: Array<{ month: string; utilization_percent: number; active_employees: number }>;
  signals: StrategySignal[];
  headcount: number;
}

export const HEALTH_STATUS_LABEL: Record<ResourceHealthStatus, string> = {
  overloaded: "过载风险", fatigue: "疲劳风险", fragmented: "碎片化", idle: "闲置", healthy: "健康",
};
export const HEALTH_STATUS_TONE: Record<ResourceHealthStatus, "critical" | "warning" | "baseline" | "healthy"> = {
  overloaded: "critical", fatigue: "warning", fragmented: "warning", idle: "baseline", healthy: "healthy",
};
export const PLAN_STATUS_LABEL: Record<PlanStatus, string> = {
  no_data: "无数据", no_target: "无目标", behind: "落后", on_track: "正常",
};
