// Department review (ontology 4.0.0). Amounts are decimal strings; null means "unknown" or "redacted".
// Person-day economics is paused, so the review carries no revenue, margin or backlog figures.
export type ResourceHealthStatus = "overloaded" | "fatigue" | "fragmented" | "idle" | "first_month" | "healthy";
export type PlanStatus = "no_data" | "no_target" | "behind" | "on_track" | "over_budget";
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
  // Cost, gross profit and margin run up to the latest month with costs entered (requirements baseline D25).
  as_of?: string | null;
}

export const PLAN_FIELDS = [
  "new_contract_target", "collection_target", "operating_cost_target", "capacity_person_days_target", "headcount_target", "utilization_target_percent",
] as const;
export type PlanField = typeof PLAN_FIELDS[number];

export type AnnualPlan = Record<PlanField, string | null> & {
  plan_id: string;
  year: number;
  department: string;
  notes: string | null;
  updated_by: string | null;
  updated_at: string;
  // Derived targets (D26): after-tax revenue, gross profit and margin follow from the entered ones.
  revenueTargetAfterTax?: string | null;
  grossProfitTarget?: string | null;
  grossMarginTargetPercent?: string | null;
};

export const COST_FIELDS = ["labor_cost", "bonus_cost", "travel_cost", "outsourcing_cost", "training_cost", "other_cost"] as const;
export type CostField = typeof COST_FIELDS[number];
export const COST_LABEL: Record<CostField, string> = {
  labor_cost: "人工成本", bonus_cost: "绩效奖金", travel_cost: "差旅费", outsourcing_cost: "外包成本", training_cost: "培训成本", other_cost: "其他",
};
export type CostEntry = Record<CostField, string> & { month: string; total: string; notes: string | null; updated_by: string | null };

export interface OperatingResult {
  tax_rate_percent: string;
  cost_entries: CostEntry[];
  cost_cutoff_month: string | null;
  missing_cost_months: string[];
  revenue_after_tax_to_cutoff: string | null;
  operating_cost: string | null;
  gross_profit: string | null;
  gross_margin_percent: string | null;
}

export interface StrategySignal {
  signal: StrategySignalKey;
  label: string;
  priority: number;
  evidence: string[];
}

export type KpiKey = "new_contracts" | "collections" | "revenue_after_tax" | "operating_cost" | "gross_profit" | "gross_margin"
  | "capacity_used" | "utilization" | "headcount";

export interface DepartmentReview {
  year: number;
  month: string;
  plan: AnnualPlan | null;
  kpis: Record<KpiKey, PlanCompletion>;
  operating: OperatingResult;
  utilization_by_month: Array<{ month: string; utilization_percent: number; active_employees: number }>;
  signals: StrategySignal[];
  headcount: number;
}

export const HEALTH_STATUS_LABEL: Record<ResourceHealthStatus, string> = {
  overloaded: "持续过载", fatigue: "疲劳风险", fragmented: "碎片化", idle: "闲置", first_month: "首月（不评价闲置）", healthy: "健康",
};
export const HEALTH_STATUS_TONE: Record<ResourceHealthStatus, "critical" | "warning" | "baseline" | "healthy"> = {
  overloaded: "critical", fatigue: "warning", fragmented: "warning", idle: "baseline", first_month: "baseline", healthy: "healthy",
};
export const PLAN_STATUS_LABEL: Record<PlanStatus, string> = {
  no_data: "无数据", no_target: "无目标", behind: "落后", on_track: "正常", over_budget: "超支",
};
