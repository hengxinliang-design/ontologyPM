import type { AuthenticatedUser, AuthSession, ContractType, CrmImportLogsResponse, EmployeeWorktimeResponse, LifecycleStatus, ManagedUser, MenuPermission, PmAssistantChatMessage, PmAssistantResponse, PortfolioResponse, ProjectDetail, ProjectHealthResponse, RecordsResponse, ResourceOperationsResponse, SyncStatus, WorktimeBaseline } from "./types";

const API_BASE = import.meta.env.VITE_API_BASE ?? "";
const API_TOKEN = import.meta.env.VITE_API_TOKEN ?? "";
const SESSION_TOKEN_KEY = "tsg-pm-session-token";
const SESSION_USER_KEY = "tsg-pm-session-user";
let sessionToken = window.localStorage.getItem(SESSION_TOKEN_KEY) ?? "";

export class ApiError extends Error {
  constructor(message: string, public readonly status: number) { super(message); }
}

function headers(): HeadersInit {
  const token = sessionToken || API_TOKEN;
  return token ? { Authorization: `Bearer ${token}` } : {};
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: { ...headers(), ...(init?.headers ?? {}) }
  });
  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: response.statusText }));
    throw new ApiError(error.detail ?? `HTTP ${response.status}`, response.status);
  }
  return response.json() as Promise<T>;
}

export function getStoredUser(): AuthenticatedUser | null {
  try {
    const raw = window.localStorage.getItem(SESSION_USER_KEY);
    return raw ? JSON.parse(raw) as AuthenticatedUser : null;
  } catch {
    return null;
  }
}

export function clearSession(): void {
  sessionToken = "";
  window.localStorage.removeItem(SESSION_TOKEN_KEY);
  window.localStorage.removeItem(SESSION_USER_KEY);
}

function storeSession(session: AuthSession): void {
  sessionToken = session.token;
  window.localStorage.setItem(SESSION_TOKEN_KEY, session.token);
  window.localStorage.setItem(SESSION_USER_KEY, JSON.stringify(session.user));
}

export async function login(username: string, password: string): Promise<AuthSession> {
  const response = await request<{ data: AuthSession }>("/api/v1/auth/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password })
  });
  storeSession(response.data);
  return response.data;
}

export async function fetchCurrentUser(): Promise<AuthenticatedUser> {
  const response = await request<{ data: AuthenticatedUser }>("/api/v1/auth/me");
  window.localStorage.setItem(SESSION_USER_KEY, JSON.stringify(response.data));
  return response.data;
}

export async function logout(): Promise<void> {
  try { await request("/api/v1/auth/logout", { method: "POST" }); }
  finally { clearSession(); }
}

export async function fetchUsers(): Promise<ManagedUser[]> {
  const response = await request<{ data: ManagedUser[] }>("/api/v1/users");
  return response.data;
}

export async function createUser(payload: { username: string; password: string; is_admin: boolean; permissions: MenuPermission[] }): Promise<ManagedUser> {
  const response = await request<{ data: ManagedUser }>("/api/v1/users", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload)
  });
  return response.data;
}

export async function updateUser(username: string, payload: { password?: string; is_admin?: boolean; is_active?: boolean; permissions?: MenuPermission[] }): Promise<ManagedUser> {
  const response = await request<{ data: ManagedUser }>(`/api/v1/users/${encodeURIComponent(username)}`, {
    method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload)
  });
  return response.data;
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

export function fetchCrmImportLogs(filters: { page: number; pageSize: number }): Promise<CrmImportLogsResponse> {
  const params = new URLSearchParams({ page: String(filters.page), page_size: String(filters.pageSize) });
  return request(`/api/v1/crm-imports?${params}`);
}

export function fetchProjects(filters: {
  query?: string;
  contractType?: string;
  lifecycleStatus?: string;
  riskLevel?: string;
  contractStatus?: string;
}): Promise<PortfolioResponse> {
  const params = new URLSearchParams();
  if (filters.query) params.set("q", filters.query);
  if (filters.contractType) params.set("contract_type", filters.contractType);
  if (filters.lifecycleStatus) params.set("lifecycle_status", filters.lifecycleStatus);
  if (filters.riskLevel) params.set("risk_level", filters.riskLevel);
  if (filters.contractStatus) params.set("contract_status", filters.contractStatus);
  const query = params.toString();
  return request(`/api/v1/projects${query ? `?${query}` : ""}`);
}

export function fetchProjectHealth(filters: {
  month?: string;
  startMonth?: string;
  endMonth?: string;
  projectNumber?: string;
  contractStatus?: string;
  query?: string;
}): Promise<ProjectHealthResponse> {
  const params = new URLSearchParams();
  if (filters.month) params.set("month", filters.month);
  if (filters.startMonth) params.set("start_month", filters.startMonth);
  if (filters.endMonth) params.set("end_month", filters.endMonth);
  if (filters.projectNumber) params.set("project_number", filters.projectNumber);
  if (filters.contractStatus) params.set("contract_status", filters.contractStatus);
  if (filters.query) params.set("q", filters.query);
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

export function fetchResourceOperations(filters: {
  month?: string;
  startMonth?: string;
  endMonth?: string;
  employeeName?: string;
} = {}): Promise<ResourceOperationsResponse> {
  const params = new URLSearchParams();
  if (filters.month) params.set("month", filters.month);
  if (filters.startMonth) params.set("start_month", filters.startMonth);
  if (filters.endMonth) params.set("end_month", filters.endMonth);
  if (filters.employeeName) params.set("employee_name", filters.employeeName);
  const query = params.toString();
  return request(`/api/v1/resource-operations${query ? `?${query}` : ""}`);
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

export async function importTimeEntriesXlsx(file: File): Promise<{
  imported: number;
  unchanged: number;
  parsed: number;
  filename?: string;
  date_range?: { start: string; end: string };
}> {
  const content_base64 = await fileToBase64(file);
  return request("/api/v1/time-entries/import-xlsx", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ filename: file.name, content_base64 })
  });
}

function fileToBase64(file: File): Promise<string> {
  return file.arrayBuffer().then(buffer => {
    const bytes = new Uint8Array(buffer);
    let binary = "";
    const chunkSize = 0x8000;
    for (let index = 0; index < bytes.length; index += chunkSize) {
      binary += String.fromCharCode(...bytes.subarray(index, index + chunkSize));
    }
    return btoa(binary);
  });
}

export async function fetchProject(projectNumber: string): Promise<ProjectDetail> {
  const response = await request<{ data: ProjectDetail }>(`/api/v1/projects/${projectNumber}`);
  return response.data;
}

export async function chatWithPmAssistant(message: string, history: PmAssistantChatMessage[]): Promise<PmAssistantResponse> {
  const response = await request<{ data: PmAssistantResponse }>("/api/v1/pm-assistant/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message, history })
  });
  return response.data;
}

export async function analyzeProjectWithPmAssistant(projectNumber: string): Promise<PmAssistantResponse> {
  const response = await request<{ data: PmAssistantResponse }>(`/api/v1/projects/${projectNumber}/ai-analysis`, {
    method: "POST"
  });
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

export async function deleteMilestone(projectNumber: string, milestoneId: string): Promise<void> {
  await request(`/api/v1/projects/${projectNumber}/milestones/${milestoneId}`, {
    method: "DELETE"
  });
}

export interface PaymentNodeInput {
  name: string;
  planned_date: string;
  planned_amount: string;
  notes?: string;
}

export async function updatePaymentNode(projectNumber: string, paymentNodeId: string, payload: PaymentNodeInput): Promise<void> {
  await request(`/api/v1/projects/${projectNumber}/payment-nodes/${paymentNodeId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload)
  });
}

export async function deletePaymentNode(projectNumber: string, paymentNodeId: string): Promise<void> {
  await request(`/api/v1/projects/${projectNumber}/payment-nodes/${paymentNodeId}`, {
    method: "DELETE"
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

export interface CrmImportResult {
  filename: string;
  format: string;
  parsed_rows: number;
  importable_rows: number;
  recognized_fields: string[];
  unmatched_project_rows: number;
  unmatched_project_subjects: string[];
  mode: "full" | "incremental";
  imported_at: string;
  validation: {
    inserted_records?: number;
    updated_records?: number;
    unchanged_records?: number;
    deleted_records?: number;
    merged_opportunities?: number;
  };
}

export async function importCrmFile(dataType: "contract_order" | "sales_opportunity", file: File): Promise<CrmImportResult> {
  const content_base64 = await fileToBase64(file);
  const response = await request<{ data: CrmImportResult }>("/api/v1/crm-imports", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ data_type: dataType, filename: file.name, content_base64 })
  });
  return response.data;
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
