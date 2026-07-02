export type DataType = "contract_order" | "sales_opportunity";

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
  status: string | null;
  merged_from_opportunity: boolean;
  updated_at: string;
}

export interface RecordsResponse {
  data: CrmRecord[];
  meta: {
    page: number;
    page_size: number;
    total_records: number;
    total_pages: number;
    schema_version: string;
    updated_at: string;
  };
  links: { self: string; next: string | null; previous: string | null };
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
    inserted_records?: number;
    updated_records?: number;
    deleted_records?: number;
    unchanged_records?: number;
  };
}

export type ContractType = "construction" | "maintenance" | "open_ended" | "sales" | "unknown";
export type LifecycleStatus = "planned" | "active" | "on_hold" | "completed" | "closed";
export type RiskLevel = "healthy" | "warning" | "critical";

export interface ProjectSummary {
  project_number: string;
  subject: string;
  customer: string | null;
  owner: string | null;
  project_manager: string | null;
  contract_type: ContractType;
  lifecycle_status: LifecycleStatus;
  planned_start: string | null;
  planned_end: string | null;
  contract_count: number;
  opportunity_count: number;
  contract_value: string;
  received_amount: string;
  invoiced_amount: string;
  planned_person_days: string | null;
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
  payment_node_total: number;
  overdue_payment_nodes: number;
  open_interventions: number;
  maintenance_complete: boolean;
  maintenance_reasons: string[];
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
  time_entries: Array<{
    external_id: string;
    employee_name: string;
    work_date: string;
    hours: string;
    approved_hours: string;
    approval_status: string;
    work_content: string;
  }>;
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
}

export interface PortfolioResponse {
  data: ProjectSummary[];
  summary: {
    total_projects: number;
    contract_value: string;
    received_amount: string;
    critical_projects: number;
    warning_projects: number;
  };
}
