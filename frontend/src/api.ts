import type { ContractType, EmployeeWorktimeResponse, LifecycleStatus, PortfolioResponse, ProjectDetail, ProjectHealthResponse, RecordsResponse, SyncStatus, WorktimeBaseline } from "./types";

const API_BASE = import.meta.env.VITE_API_BASE ?? "";
const API_TOKEN = import.meta.env.VITE_API_TOKEN ?? "";

function headers(): HeadersInit {
  return API_TOKEN ? { Authorization: `Bearer ${API_TOKEN}` } : {};
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: { ...headers(), ...(init?.headers ?? {}) }
  });
  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: response.statusText }));
    throw new Error(error.detail ?? `HTTP ${response.status}`);
  }
  return response.json() as Promise<T>;
}

export interface RecordFilters {
  dataType?: string;
  query?: string;
  status?: string;
  owner?: string;
  page: number;
  pageSize: number;
}

export function fetchRecords(filters: RecordFilters): Promise<RecordsResponse> {
  const params = new URLSearchParams({
    page: String(filters.page),
    page_size: String(filters.pageSize),
    sort: "-signed_at"
  });
  if (filters.dataType) params.set("data_type", filters.dataType);
  if (filters.query) params.set("q", filters.query);
  if (filters.status) params.set("status", filters.status);
  if (filters.owner) params.set("owner", filters.owner);
  return request(`/api/v1/records?${params}`);
}

export function fetchSyncStatus(): Promise<SyncStatus> {
  return request("/api/v1/sync-status");
}

export function fetchProjects(filters: {
  query?: string;
  contractType?: string;
  lifecycleStatus?: string;
  riskLevel?: string;
}): Promise<PortfolioResponse> {
  const params = new URLSearchParams();
  if (filters.query) params.set("q", filters.query);
  if (filters.contractType) params.set("contract_type", filters.contractType);
  if (filters.lifecycleStatus) params.set("lifecycle_status", filters.lifecycleStatus);
  if (filters.riskLevel) params.set("risk_level", filters.riskLevel);
  const query = params.toString();
  return request(`/api/v1/projects${query ? `?${query}` : ""}`);
}

export function fetchProjectHealth(filters: {
  month?: string;
  startMonth?: string;
  endMonth?: string;
  projectNumber?: string;
}): Promise<ProjectHealthResponse> {
  const params = new URLSearchParams();
  if (filters.month) params.set("month", filters.month);
  if (filters.startMonth) params.set("start_month", filters.startMonth);
  if (filters.endMonth) params.set("end_month", filters.endMonth);
  if (filters.projectNumber) params.set("project_number", filters.projectNumber);
  const query = params.toString();
  return request(`/api/v1/project-health${query ? `?${query}` : ""}`);
}

export function fetchEmployeeWorktime(filters: {
  period: "month" | "year";
  month?: string;
  year?: string;
  employeeName?: string;
}): Promise<EmployeeWorktimeResponse> {
  const params = new URLSearchParams({ period: filters.period });
  if (filters.month) params.set("month", filters.month);
  if (filters.year) params.set("year", filters.year);
  if (filters.employeeName) params.set("employee_name", filters.employeeName);
  return request(`/api/v1/employee-worktime?${params}`);
}

export async function fetchWorktimeBaseline(): Promise<WorktimeBaseline> {
  const response = await request<{ data: WorktimeBaseline }>("/api/v1/worktime-baseline");
  return response.data;
}

export async function updateWorktimeBaseline(payload: Partial<WorktimeBaseline>): Promise<WorktimeBaseline> {
  const response = await request<{ data: WorktimeBaseline }>("/api/v1/worktime-baseline", {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload)
  });
  return response.data;
}

export async function fetchProject(projectNumber: string): Promise<ProjectDetail> {
  const response = await request<{ data: ProjectDetail }>(`/api/v1/projects/${projectNumber}`);
  return response.data;
}

export async function updateProjectSettings(projectNumber: string, payload: {
  contract_type: ContractType;
  lifecycle_status: LifecycleStatus;
  planned_person_days: string | null;
  hours_per_person_day: string;
  project_manager: string;
}): Promise<ProjectDetail> {
  const response = await request<{ data: ProjectDetail }>(`/api/v1/projects/${projectNumber}/settings`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload)
  });
  return response.data;
}

export interface MilestoneInput {
  name: string;
  planned_start: string;
  planned_end: string;
  owner: string;
  deliverable: string;
  is_payment_node: boolean;
  payment_date?: string;
  payment_amount?: string;
}

export async function createMilestone(projectNumber: string, payload: MilestoneInput): Promise<void> {
  await request(`/api/v1/projects/${projectNumber}/milestones`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload)
  });
}

export async function updateMilestone(projectNumber: string, milestoneId: string, payload: MilestoneInput): Promise<void> {
  await request(`/api/v1/projects/${projectNumber}/milestones/${milestoneId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload)
  });
}

export async function confirmMilestone(projectNumber: string, milestoneId: string, confirmedBy: string): Promise<void> {
  await request(`/api/v1/projects/${projectNumber}/milestones/${milestoneId}/confirm`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ confirmed_by: confirmedBy })
  });
}

export async function confirmPayment(projectNumber: string, paymentNodeId: string, confirmedBy: string): Promise<void> {
  await request(`/api/v1/projects/${projectNumber}/payment-nodes/${paymentNodeId}/confirm`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ confirmed_by: confirmedBy })
  });
}

export function triggerSync(): Promise<{ job_id: string; status: string }> {
  return request("/api/v1/sync-jobs", { method: "POST" });
}

export function exportUrl(format: "json" | "csv"): string {
  return `${API_BASE}/api/v1/exports/latest?format=${format}`;
}

export async function downloadExport(format: "json" | "csv"): Promise<void> {
  const response = await fetch(exportUrl(format), { headers: headers() });
  if (!response.ok) throw new Error(`导出失败：HTTP ${response.status}`);
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = `xtcrm-latest.${format}`;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}
