export type DataType = "contract_order" | "sales_opportunity";

export type MenuPermission = "projects" | "health" | "resource" | "crm" | "investment" | "department" | "employeeWorktime" | "sync" | "ontology" | "settings";

export interface AuthenticatedUser {
  username: string;
  is_admin: boolean;
  permissions: MenuPermission[];
  is_active: boolean;
}

export interface AuthSession {
  token: string;
  user: AuthenticatedUser;
  expires_at: string | null;
}

export interface ManagedUser extends AuthenticatedUser {
  created_at: string;
  updated_at: string;
}

export interface Money {
  amount: string;
  currency: "CNY";
  display: string;
}

export interface CrmRecord {
  record_id: string;
  data_type: DataType;
  project_number: string | null;
  subject: string;
  customer: string | null;
  category: string | null;
  signed_at: string | null;
  total_amount: Money | null;
  received_amount: Money | null;
  invoiced_amount: Money | null;
  owner: string;
  probability: number | null;
  sales_stage: string | null;
  status: string | null;
  source_updated_at: string | null;
  merged_from_opportunity: boolean;
  updated_at: string;
}

export interface ChinaHolidayCalendarDay {
  date: string;
  name: string;
  kind: "holiday" | "makeup_workday";
}

export interface ChinaHolidayCalendarResponse {
  data: ChinaHolidayCalendarDay[];
  meta: {
    available: boolean;
    stale: boolean;
    cached_at: string | null;
    source: string;
  };
}

export interface RecordsResponse {
  data: CrmRecord[];
  meta: {
    page_size: number;
    total_records: number;
    total_pages: number;
    next_cursor: string | null;
    schema_version: string;
    updated_at: string;
  };
  links: { self: string; next: string | null; previous: string | null };
}

export interface PmAssistantChatMessage {
  role: "user" | "assistant";
  content: string;
}

export interface PmAssistantResponse {
  answer: string;
  sources: string[];
  model: string;
}

export interface SyncStatus {
  status: "succeeded" | "failed" | "running" | "auth_required" | "never_run";
  last_success_at: string | null;
  next_scheduled_at: string;
  timezone: string;
  source_stats: Record<DataType, number>;
  output_stats: Record<DataType, number>;
  validation: {
    complete: boolean;
    merged_opportunities: number;
    cross_type_duplicate_project_ids: number;
    sync_mode?: "full" | "incremental";
    import_kind?: "manual_upload";
    import_data_type?: DataType;
    import_filename?: string;
    inserted_records?: number;
    updated_records?: number;
    deleted_records?: number;
    unchanged_records?: number;
  };
}

export interface CrmImportLog {
  log_id: number;
  snapshot_id: number;
  data_type: DataType;
  filename: string;
  parsed_rows: number;
  inserted_records: number;
  updated_records: number;
  unchanged_records: number;
  imported_at: string;
}

export interface CrmImportLogsResponse {
  data: CrmImportLog[];
  meta: {
    page: number;
    page_size: number;
    total_records: number;
    total_pages: number;
  };
}

export type ContractType = "construction" | "maintenance" | "open_ended" | "sales" | "unknown";
export type LifecycleStatus = "planned" | "active" | "on_hold" | "completed" | "closed";
export type RiskLevel = "healthy" | "warning" | "critical";

export interface CurrentMilestone {
  milestone_id: string;
  name: string;
  status: string;
  planned_start: string | null;
  planned_end: string;
  deadline: Deadline;
}

export interface PaymentSummary {
  total_nodes: number;
  paid_nodes: number;
  planned_amount: string;
  actual_amount: string;
  next_node: {
    payment_node_id: string;
    name: string;
    planned_date: string;
    planned_amount: string;
    status: string;
    deadline: Deadline;
  } | null;
}

export interface TimeEntry {
  external_id: string;
  employee_name: string;
  work_date: string;
  hours: string;
  overtime_hours: string;
  approved_hours: string;
  approval_status: string;
  work_content: string;
}

export interface MonthlyTimeEntries {
  month: string;
  entry_count: number;
  reported_hours: string;
  overtime_hours: string;
  approved_hours: string;
  confirmation_gap_hours: string;
  reported_person_days: string;
  overtime_person_days: string;
  approved_person_days: string;
  confirmation_gap_person_days: string;
  excluded_entry_count: number;
  excluded_person_days: string;
  entries: TimeEntry[];
}

export interface PmoResourceSummary {
  cost_basis: "人天";
  monetary_cost_available: boolean;
  participant_count: number;
  normal_person_days: string;
  overtime_person_days: string;
  reported_person_days: string;
  approved_person_days: string;
  confirmation_gap_person_days: string;
}

export interface PmoResourceRow {
  employee_name: string;
  employee_id: string | null;
  entry_count: number;
  active_day_count: number;
  active_month_count: number;
  normal_person_days: string;
  overtime_person_days: string;
  reported_person_days: string;
  approved_person_days: string;
  confirmation_gap_person_days: string;
  project_effort_share_percent: number;
  overtime_ratio_percent: number;
  monthly_average_person_days: string;
}

export interface ProjectSummary {
  project_number: string;
  subject: string;
  customer: string | null;
  /** Imported CRM owner / signer, representing the salesperson. */
  sales_owner: string | null;
  owner: string | null;
  project_manager: string | null;
  master_category: "项目" | "商机";
  /** Corresponds to the imported contract/order CSV's “分类” field. */
  category: string | null;
  contract_type: ContractType;
  lifecycle_status: LifecycleStatus;
  status: LifecycleStatus;
  planned_start: string | null;
  planned_end: string | null;
  contract_count: number;
  opportunity_count: number;
  contract_value: string;
  crm_received_amount: string;
  /** 开口合同按已确认工作量结算，不以框架合同额计算回款比例。 */
  revenue_settlement_basis: "contract" | "workload";
  /** CRM 开票金额，作为开口合同的已确认收入。 */
  recognized_revenue_amount: string;
  outstanding_receivable_amount: string;
  received_amount: string;
  invoiced_amount: string;
  planned_person_days: string | null;
  budget_hours: string | null;
  actual_hours: string;
  actual_person_days: string;
  reported_hours: string;
  reported_person_days: string;
  customer_confirmed_hours: string;
  customer_confirmed_person_days: string;
  confirmation_gap_hours: string;
  confirmation_gap_person_days: string;
  budget_utilization: number | null;
  billable_person_days: string | null;
  milestone_total: number;
  milestone_completed: number;
  overdue_milestones: number;
  current_milestone: CurrentMilestone | null;
  payment_node_total: number;
  overdue_payment_nodes: number;
  payment_summary: PaymentSummary;
  payment_term_summary: Array<{
    name: string;
    payment_ratio: string | null;
    planned_amount: string;
    linked_milestone_id: string | null;
    payment_node_id: string | null;
    node_status: string | null;
    planned_date: string | null;
  }>;
  open_interventions: number;
  maintenance_complete: boolean;
  maintenance_reasons: string[];
  /** Every applicable maintenance requirement and whether it is met (ontology derivation maintenanceStatus). */
  maintenance_checklist?: Array<{ requirement: string; label: string; met: boolean; action: string; property: string | null; link: string | null }>;
  pending_todos: number;
  overdue_todos: number;
  next_todo: TodoEvent | null;
  anomaly_count: number;
  anomaly_alerts: Array<{
    code: string;
    severity: "critical" | "warning" | "info";
    title: string;
    detail: string;
  }>;
  risk_level: RiskLevel;
  risk_reasons: string[];
}

export interface ProjectDetail extends ProjectSummary {
  crm_records: CrmRecord[];
  monthly_time_entries: MonthlyTimeEntries[];
  pmo_resource_summary: PmoResourceSummary;
  pmo_resource_rows: PmoResourceRow[];
  milestones: Array<{
    milestone_id: string;
    sequence_no: number | null;
    name: string;
    planned_date: string;
    planned_start: string;
    planned_end: string;
    completed_date: string | null;
    status: string;
    owner: string | null;
    deliverable: string | null;
    is_payment_node: number;
    payment_node_id: string | null;
    payment_term_id?: string | null;
    payment_date: string | null;
    payment_amount: string | null;
    payment_status: string | null;
    confirmed_by: string | null;
    confirmed_at: string | null;
    deadline: Deadline;
  }>;
  payment_nodes: Array<{
    payment_node_id: string;
    name: string;
    planned_date: string;
    planned_amount: string;
    actual_amount: string;
    status: string;
    notes?: string | null;
  }>;
  payment_terms: Array<{
    payment_term_id: string;
    name: string;
    payment_ratio: string | null;
    planned_amount: string;
    notes?: string | null;
    linked_milestone_id: string | null;
    payment_node_id: string | null;
  }>;
  interventions: Array<{
    intervention_id: string;
    title: string;
    status: string;
    owner: string | null;
    due_date: string | null;
  }>;
  todo_events: TodoEvent[];
  settings: {
    project_number?: string;
    hours_per_person_day?: string;
    notes?: string | null;
  };
  /** Markings the viewer lacks; their values are null (FINANCE: amounts, PERSONNEL: per-person hours). */
  redacted_markings?: string[];
}

export interface Deadline {
  days_remaining: number;
  urgency: "completed" | "overdue" | "due_today" | "due_soon" | "upcoming";
  label: string;
}

export interface TodoEvent extends Deadline {
  event_type: "milestone" | "payment";
  event_id: string;
  name: string;
  due_date: string;
  amount: string | null;
  deliverable: string | null;
  completed: boolean;
  completed_at: string | null;
  confirmed_by: string | null;
  completion_state: "on_time" | "delayed" | "unknown" | null;
  completion_label: string | null;
  delay_days: number;
}

export interface PortfolioResponse {
  data: ProjectSummary[];
  summary: {
    total_projects: number;
    contract_value: string;
    received_amount: string;
    outstanding_receivable_amount: string;
    critical_projects: number;
    warning_projects: number;
  };
  meta: {
    page_size: number;
    next_cursor: string | null;
    lifecycle_statuses: LifecycleStatus[];
    categories: string[];
  };
}

export interface ProjectHealthRow {
  project_number: string;
  project_name: string;
  customer: string | null;
  master_category: ProjectSummary["master_category"];
  category: string | null;
  contract_type: ContractType;
  risk_level: ProjectSummary["risk_level"];
  contract_value: string;
  crm_received_amount: string;
  revenue_settlement_basis: ProjectSummary["revenue_settlement_basis"];
  recognized_revenue_amount: string;
  outstanding_receivable_amount: string | null;
  payment_term_summary: ProjectSummary["payment_term_summary"];
  anomaly_count: number;
  anomaly_alerts: ProjectSummary["anomaly_alerts"];
  budget_utilization: number | null;
  billable_person_days: string | null;
  actual_person_days: string;
  pm_owner: string | null;
  sales_owner: string | null;
  project_status: LifecycleStatus;
  planned_start: string | null;
  planned_end: string | null;
  budget_person_days: string | null;
  plan_months: number | null;
  monthly_budget_person_days: string | null;
  cumulative_actual_person_days: string;
  period_actual_hours: string;
  period_actual_person_days: string;
  previous_month_person_days: string;
  previous_period: { start_month: string; end_month: string };
  previous_period_person_days: string;
  participant_count: number;
  cumulative_participant_count: number;
  burn_rate_percent: string | null;
  overrun_warning: string;
  monthly_trend: string;
  health_status: "健康" | "需关注" | "额外投入" | "已超预算" | "无基线";
  budget_exhaustion_forecast: {
    label: string;
    basis: string;
  };
  remaining_budget_person_days: string | null;
  current_milestone: CurrentMilestone | null;
  payment_summary: PaymentSummary;
  risk_reasons: string[];
}

export interface ProjectHealthResponse {
  /** Markings the viewer lacks; their values are null. */
  redacted_markings?: string[];
  data: ProjectHealthRow[];
  summary: {
    total_projects: number;
    contract_value: string;
    received_amount: string;
    outstanding_receivable_amount: string;
    risk_critical_projects: number;
    risk_warning_projects: number;
    /** Projects with unmet maintenance requirements (declared in the ontology metadata). */
    maintenance_pending_projects?: number;
    critical_projects: number;
    warning_projects: number;
    healthy_projects: number;
    no_baseline_projects: number;
  };
  meta: {
    start_month: string;
    end_month: string;
    mode: "month" | "range";
    page_size: number;
    next_cursor: string | null;
    lifecycle_statuses: LifecycleStatus[];
    categories: string[];
  };
}

export interface WorktimeBaseline {
  config_id: string;
  standard_work_days_per_year: string;
  standard_hours_per_day: string;
  annual_baseline_hours: string;
  effective_baseline_hours: string;
  monthly_baseline_hours: string;
  quarterly_baseline_hours: string;
  minimum_utilization_percent: string;
  excellent_utilization_percent: string;
  attention_utilization_percent: string;
  overload_utilization_percent: string;
  budget_attention_percent: string;
  budget_high_risk_percent: string;
  notes: string | null;
  updated_at: string;
}

export interface EmployeeWorktimeRow {
  employee_id: string;
  employee_name: string;
  reported_hours: string;
  overtime_hours: string;
  approved_hours: string;
  confirmation_gap_hours: string;
  reported_person_days: string;
  overtime_person_days: string;
  approved_person_days: string;
  baseline_hours: string;
  utilization_percent: string;
  entry_count: number;
  project_count: number;
  status: "工时充足" | "基本达标" | "需要关注" | "严重不足";
}

export interface EmployeeWorktimeResponse {
  data: EmployeeWorktimeRow[];
  summary: {
    total_employees: number;
    sufficient_count: number;
    qualified_count: number;
    attention_count: number;
    critical_count: number;
  };
  baseline: WorktimeBaseline;
  meta: {
    period: "month" | "year";
    period_label: string;
    start_date: string;
    end_date: string;
    baseline_hours: string;
    working_days: number;
    holiday_excluded_days: number;
    makeup_workdays: number;
    calendar_available: boolean;
    calendar_stale: boolean;
    latest_work_month: string | null;
  };
}

export interface ResourceOperationProject {
  project_number: string;
  project_name: string;
  reported_person_days: string;
  overtime_person_days: string;
  is_internal_nonproject?: boolean;
}

export interface ResourceOperationRow extends EmployeeWorktimeRow {
  allocation_status: "可优先调剂" | "可适度支持" | "负荷过高" | "负荷较高" | "安排基本均衡";
  available_person_days: string;
  internal_nonproject_person_days: string;
  internal_nonproject_entry_count: number;
  assignments: ResourceOperationProject[];
}

export interface ResourceOperationsResponse {
  data: ResourceOperationRow[];
  summary: {
    total_employees: number;
    available_count: number;
    high_load_count: number;
    overloaded_count: number;
    available_person_days: string;
  };
  baseline: WorktimeBaseline;
  meta: {
    month: string;
    period_label: string;
    start_month: string;
    end_month: string;
    employee_name: string | null;
    start_date: string;
    end_date: string;
    baseline_hours: string;
    working_days: number;
    holiday_excluded_days: number;
    makeup_workdays: number;
    calendar_available: boolean;
    calendar_stale: boolean;
    latest_work_month: string | null;
    recommendation_limitations: string;
    page_size: number;
    next_cursor: string | null;
  };
}

