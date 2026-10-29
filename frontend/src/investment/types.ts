export type CostPhase = "delivery" | "presales_converted" | "at_risk" | "sunk" | "internal" | "unregistered";

export interface CostObjectInvestment {
  code: string;
  subject: string | null;
  customer: string | null;
  crm_owner: string | null;
  crm_status: string | null;
  registration: "contracted" | "opportunity" | "unregistered" | "internal";
  outcome: "won" | "open" | "lost" | "abandoned" | "na";
  outcome_source: string | null;
  outcome_reason: string | null;
  outcome_date: string | null;
  outcome_recorded_by: string | null;
  win_probability: number | null;
  conversion_date: string | null;
  conversion_date_source: string | null;
  first_work_date: string | null;
  last_work_date: string | null;
  entry_count: number;
  employee_count: number;
  total_person_days: string;
  presales_person_days: string;
  presales_converted_person_days: string;
  delivery_person_days: string;
  at_risk_person_days: string;
  sunk_person_days: string;
  internal_person_days: string;
  unregistered_person_days: string;
  expected_sunk_person_days: string | null;
  probability_source: string | null;
  cost_to_win_share: number | null;
  presales_age_days: number | null;
  stalled: boolean;
  data_flags: string[];
}

export interface InvestmentSettings {
  standard_person_day_cost: string | null;
  stall_days: number;
  lost_statuses: string[];
  internal_codes: string[];
  updated_at?: string;
}

export type MonthlyInvestment = { month: string } & Record<CostPhase, string>;

export interface InvestmentAnalysis {
  period: { start_month: string | null; end_month: string | null };
  settings: InvestmentSettings;
  cost_enabled: boolean;
  totals: Record<`${CostPhase}_person_days` | "total_person_days" | "expected_sunk_person_days", string> & {
    total_cost: string | null; sunk_cost: string | null; at_risk_cost: string | null;
  };
  structure: { phase: CostPhase; label: string; person_days: string; share: number | null; cost: string | null }[];
  ratios: {
    won_count: number; lost_count: number; open_count: number; unregistered_count: number;
    win_rate_by_count: number | null; win_rate_by_effort: number | null; sunk_rate: number | null;
    average_cost_to_win_person_days: string | null; cost_to_win_share: number | null; effective_investment_share: number | null;
  };
  aging: { bucket: string; count: number; person_days: string }[];
  monthly: MonthlyInvestment[];
  open_opportunities: CostObjectInvestment[];
  sunk_items: CostObjectInvestment[];
  sunk_reasons: { reason: string; count: number; person_days: string }[];
  unregistered: CostObjectInvestment[];
  won_items: CostObjectInvestment[];
}
