import { ChangeEvent, FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import DatePicker, { registerLocale } from "react-datepicker";
import { DayPicker } from "react-day-picker";
import { zhCN } from "date-fns/locale";
import "react-datepicker/dist/react-datepicker.css";
import { Markdown } from "./AssistantMarkdown";
import { OntologyExplorer } from "./ontology/OntologyExplorer";
import { milestonePayload, showsLegacyPaymentNote } from "./milestoneForm";
import { ROLE_OPTIONS, defaultMenus, menuAllowed, projectManagerHint, roleOption } from "./roles";
import { InvestmentAnalysisPage } from "./investment/InvestmentAnalysis";
import { DepartmentOperationsPage } from "./department/DepartmentOperations";
import "react-day-picker/style.css";
import {
  confirmMilestone,
  confirmPayment,
  chatWithPmAssistant,
  ApiError,
  analyzeProjectWithPmAssistant,
  createMilestone,
  createPaymentTerm,
  openIntervention,
  updateInterventionStatus,
  deleteMilestone,
  deletePaymentNode,
  deletePaymentTerm,
  downloadExport,
  fetchCrmImportLogs,
  fetchChinaHolidayCalendar,
  fetchEmployeeWorktime,
  fetchResourceOperations,
  fetchProject,
  fetchProjectHealth,
  fetchRecords,
  fetchSyncStatus,
  fetchWorktimeBaseline,
  importTimeEntriesXlsx,
  importCrmFile,
  clearSession,
  createUser,
  fetchCurrentUser,
  fetchUsers,
  fetchProjectManagerNames,
  getStoredUser,
  login,
  logout,
  updateMilestone,
  updatePaymentNode,
  updatePaymentTerm,
  updateProjectSettings,
  updateWorktimeBaseline,
  updateUser
} from "./api";
import type {
  AuthenticatedUser,
  ChinaHolidayCalendarDay,
  CrmRecord,
  CrmImportLogsResponse,
  EmployeeWorktimeResponse,
  EmployeeWorktimeRow,
  ManagedUser,
  MenuPermission,
  PaymentSummary,
  PmAssistantChatMessage,
  PmAssistantSuggestion,
  ProjectDetail,
  ProjectHealthResponse,
  ProjectHealthRow,
  ProjectSummary,
  ResourceOperationsResponse,
  RecordsResponse,
  SyncStatus,
  UserRole,
  TimeEntry,
  WorktimeBaseline
} from "./types";

const PAGE_SIZE = 20;
const ANALYTICS_PAGE_SIZE = 50;
const IMPORT_LOG_PAGE_SIZE = 10;
const WORKTIME_MONTH_STORAGE_KEY = "tsg-pm-worktime-month";
const RESOURCE_MONTH_STORAGE_KEY = "tsg-pm-resource-month";
type View = "projects" | "investment" | "department" | "resource" | "employeeWorktime" | "crm" | "sync" | "ontology" | "settings";

registerLocale("zh-CN", zhCN);

function parseMonth(value: string): Date | null {
  const match = /^(\d{4})-(\d{2})$/.exec(value);
  if (!match) return null;
  return new Date(Number(match[1]), Number(match[2]) - 1, 1);
}

function formatMonth(value: Date): string {
  return `${value.getFullYear()}-${String(value.getMonth() + 1).padStart(2, "0")}`;
}

function parseDate(value: string): Date | null {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value);
  if (!match) return null;
  return new Date(Number(match[1]), Number(match[2]) - 1, Number(match[3]));
}

function formatDate(value: Date): string {
  return `${value.getFullYear()}-${String(value.getMonth() + 1).padStart(2, "0")}-${String(value.getDate()).padStart(2, "0")}`;
}

type ChinaHolidayCalendarMarkers = Record<string, ChinaHolidayCalendarDay>;

let chinaHolidayCalendarCache: ChinaHolidayCalendarMarkers | null = null;
let chinaHolidayCalendarRequest: Promise<ChinaHolidayCalendarMarkers> | null = null;

function loadChinaHolidayCalendar(): Promise<ChinaHolidayCalendarMarkers> {
  if (chinaHolidayCalendarCache) return Promise.resolve(chinaHolidayCalendarCache);
  if (!chinaHolidayCalendarRequest) {
    chinaHolidayCalendarRequest = fetchChinaHolidayCalendar()
      .then(response => {
        const markers = Object.fromEntries(response.data.map(day => [day.date, day]));
        if (response.meta.available) chinaHolidayCalendarCache = markers;
        return markers;
      })
      .catch(() => ({}))
      .finally(() => { chinaHolidayCalendarRequest = null; });
  }
  return chinaHolidayCalendarRequest;
}

function useChinaHolidayCalendarMarkers(): ChinaHolidayCalendarMarkers {
  const [markers, setMarkers] = useState<ChinaHolidayCalendarMarkers>(chinaHolidayCalendarCache ?? {});
  useEffect(() => {
    let active = true;
    void loadChinaHolidayCalendar().then(days => { if (active) setMarkers(days); });
    return () => { active = false; };
  }, []);
  return markers;
}

function MonthPicker({ value, onChange, label }: { value: string; onChange: (value: string) => void; label: string }) {
  return <div className="month-picker">
    <DatePicker
      aria-label={label}
      selected={parseMonth(value)}
      onChange={(date: Date | null) => onChange(date ? formatMonth(date) : "")}
      dateFormat="yyyy年MM月"
      locale="zh-CN"
      placeholderText="选择月份"
      showMonthYearPicker
      showFullMonthYearPicker
      showIcon
      toggleCalendarOnIconClick
      calendarStartDay={1}
      popperPlacement="bottom-start"
    />
  </div>;
}

function CalendarDatePicker({ value, onChange, label, minDate, clearable = false }: {
  value: string;
  onChange: (value: string) => void;
  label: string;
  minDate?: string;
  clearable?: boolean;
}) {
  const holidayMarkers = useChinaHolidayCalendarMarkers();
  const wrapperRef = useRef<HTMLDivElement>(null);
  const popoverRef = useRef<HTMLDivElement>(null);
  const selectedDate = parseDate(value) ?? undefined;
  const earliestDate = parseDate(minDate ?? "") ?? undefined;
  const [open, setOpen] = useState(false);
  const [month, setMonth] = useState<Date>(() => selectedDate ?? new Date());
  const [position, setPosition] = useState({ left: 12, top: 12, placement: "bottom" });
  const visibleYear = month.getFullYear();

  const positionPopover = useCallback(() => {
    const anchor = wrapperRef.current;
    if (!anchor) return;
    const rect = anchor.getBoundingClientRect();
    const popoverRect = popoverRef.current?.getBoundingClientRect();
    const width = popoverRect?.width ?? 282;
    const height = popoverRect?.height ?? 310;
    const canOpenAbove = rect.top - height - 8 >= 12;
    const placement = rect.bottom + height + 8 > window.innerHeight - 12 && canOpenAbove ? "top" : "bottom";
    const top = placement === "top"
      ? Math.max(12, rect.top - height - 8)
      : Math.min(rect.bottom + 8, Math.max(12, window.innerHeight - height - 12));
    setPosition({ left: Math.max(12, Math.min(rect.left, window.innerWidth - width - 12)), top, placement });
  }, []);

  useEffect(() => {
    if (selectedDate) setMonth(selectedDate);
  }, [value]);

  useEffect(() => {
    if (!open) return;
    const closeWhenOutside = (event: MouseEvent) => {
      const target = event.target as Node;
      if (!wrapperRef.current?.contains(target) && !popoverRef.current?.contains(target)) setOpen(false);
    };
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", closeWhenOutside);
    document.addEventListener("keydown", closeOnEscape);
    return () => {
      document.removeEventListener("mousedown", closeWhenOutside);
      document.removeEventListener("keydown", closeOnEscape);
    };
  }, [open]);

  useEffect(() => {
    if (!open) return;
    positionPopover();
    const frame = window.requestAnimationFrame(positionPopover);
    window.addEventListener("resize", positionPopover);
    window.addEventListener("scroll", positionPopover, true);
    return () => {
      window.cancelAnimationFrame(frame);
      window.removeEventListener("resize", positionPopover);
      window.removeEventListener("scroll", positionPopover, true);
    };
  }, [open, positionPopover]);

  return <div className="date-picker" ref={wrapperRef}>
    <div className="date-picker-trigger">
      <input aria-label={label} value={selectedDate ? formatDate(selectedDate).replaceAll("-", "/") : ""} placeholder="请选择日期" readOnly onClick={() => setOpen(true)} />
      {clearable && selectedDate && <button type="button" className="date-picker-clear" aria-label="清除日期" onClick={() => onChange("")}>×</button>}
      <button type="button" className="date-picker-toggle" aria-label="打开日历" aria-expanded={open} onClick={() => setOpen(current => !current)}>
        <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M7 3v3m10-3v3M4.5 9.5h15M6 5h12a1.5 1.5 0 0 1 1.5 1.5v12A1.5 1.5 0 0 1 18 20H6a1.5 1.5 0 0 1-1.5-1.5v-12A1.5 1.5 0 0 1 6 5Z" /><path d="M8 13h3m2 0h3m-8 3h3m2 0h3" /></svg>
      </button>
    </div>
    {open && createPortal(<div ref={popoverRef} className="day-picker-popover" data-placement={position.placement} role="dialog" aria-label={`${label}日历`} style={{ left: position.left, top: position.top }}>
      <DayPicker
        mode="single"
        selected={selectedDate}
        month={month}
        onMonthChange={setMonth}
        onSelect={date => { if (date) { onChange(formatDate(date)); setOpen(false); } }}
        disabled={earliestDate ? { before: earliestDate } : undefined}
        locale={zhCN}
        weekStartsOn={1}
        showOutsideDays
        fixedWeeks
        captionLayout="dropdown"
        navLayout="around"
        startMonth={new Date(visibleYear - 2, 0)}
        endMonth={new Date(visibleYear + 2, 11)}
        modifiers={{
          holiday: date => holidayMarkers[formatDate(date)]?.kind === "holiday",
          makeupWorkday: date => holidayMarkers[formatDate(date)]?.kind === "makeup_workday"
        }}
        modifiersClassNames={{ holiday: "rdp-day_holiday", makeupWorkday: "rdp-day_makeup-workday" }}
      />
      <div className="day-picker-legend" aria-label="日历标识说明"><span><i className="holiday" />法定节假日</span><span><i className="makeup" />调休补班</span></div>
    </div>, document.body)}
  </div>;
}

export function App() {
  const [view, setView] = useState<View>("projects");
  const [authUser, setAuthUser] = useState<AuthenticatedUser | null>(() => getStoredUser());
  const [authLoading, setAuthLoading] = useState(() => Boolean(getStoredUser()));
  const [health, setHealth] = useState<ProjectHealthResponse | null>(null);
  const [employeeWorktime, setEmployeeWorktime] = useState<EmployeeWorktimeResponse | null>(null);
  const [resourceOperations, setResourceOperations] = useState<ResourceOperationsResponse | null>(null);
  const [worktimeBaseline, setWorktimeBaseline] = useState<WorktimeBaseline | null>(null);
  const [records, setRecords] = useState<RecordsResponse | null>(null);
  const [sync, setSync] = useState<SyncStatus | null>(null);
  const [crmImportLogs, setCrmImportLogs] = useState<CrmImportLogsResponse | null>(null);
  const [crmImportLogPage, setCrmImportLogPage] = useState(1);
  const [selected, setSelected] = useState<ProjectDetail | null>(null);
  const [queryInput, setQueryInput] = useState("");
  const [query, setQuery] = useState("");
  const [contractType, setContractType] = useState("");
  const [lifecycleStatus, setLifecycleStatus] = useState("");
  const [projectCategory, setProjectCategory] = useState("");
  const [riskLevel, setRiskLevel] = useState("");
  const [maintenanceFilter, setMaintenanceFilter] = useState("");
  const [masterCategory, setMasterCategory] = useState("");
  const [healthMode, setHealthMode] = useState<"month" | "range">("month");
  const [healthMonth, setHealthMonth] = useState(new Date().toISOString().slice(0, 7));
  const [healthStartMonth, setHealthStartMonth] = useState("");
  const [healthEndMonth, setHealthEndMonth] = useState("");
  const [worktimePeriod, setWorktimePeriod] = useState<"month" | "year">("month");
  const [worktimeMonth, setWorktimeMonth] = useState(() => window.sessionStorage.getItem(WORKTIME_MONTH_STORAGE_KEY) ?? new Date().toISOString().slice(0, 7));
  const [worktimeMonthResolved, setWorktimeMonthResolved] = useState(() => Boolean(window.sessionStorage.getItem(WORKTIME_MONTH_STORAGE_KEY)));
  const [worktimeYear, setWorktimeYear] = useState(new Date().toISOString().slice(0, 4));
  const [resourceMonth, setResourceMonth] = useState(() => window.sessionStorage.getItem(RESOURCE_MONTH_STORAGE_KEY) ?? new Date().toISOString().slice(0, 7));
  const [resourceMonthResolved, setResourceMonthResolved] = useState(() => Boolean(window.sessionStorage.getItem(RESOURCE_MONTH_STORAGE_KEY)));
  const [resourcePeriodMode, setResourcePeriodMode] = useState<"month" | "range">("month");
  const [resourceStartMonth, setResourceStartMonth] = useState("");
  const [resourceEndMonth, setResourceEndMonth] = useState("");
  const [resourceEmployeeInput, setResourceEmployeeInput] = useState("");
  const [resourceEmployeeName, setResourceEmployeeName] = useState("");
  const [dataType, setDataType] = useState("");
  const [status, setStatus] = useState("");
  const [salesStage, setSalesStage] = useState("");
  const [page, setPage] = useState(1);
  const [recordCursors, setRecordCursors] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const canAccess = useCallback((permission: MenuPermission) => Boolean(
    authUser?.is_admin || authUser?.permissions.includes(permission)
  ), [authUser]);

  useEffect(() => {
    if (!authUser) { setAuthLoading(false); return; }
    void fetchCurrentUser().then(setAuthUser).catch(() => {
      clearSession();
      setAuthUser(null);
    }).finally(() => setAuthLoading(false));
  }, []);

  // 项目管理 replaces the former 项目总览 and 项目运营监控 pages. Either menu
  // permission opens it; only "projects" may open the drawer to maintain data.
  const canMaintainProjects = canAccess("projects");

  useEffect(() => {
    const routePermission: Record<View, MenuPermission[]> = {
      projects: ["projects", "health"], investment: ["investment"], department: ["department"], resource: ["resource"], crm: ["crm"],
      employeeWorktime: ["employeeWorktime"], sync: ["sync"], ontology: ["ontology"], settings: ["settings"]
    };
    const allowed = (candidate: View) => routePermission[candidate].some(canAccess);
    if (!authUser) return;
    if (!allowed(view)) {
      const fallback = (["projects", "investment", "department", "resource", "crm", "employeeWorktime", "sync", "ontology"] as View[]).find(allowed);
      if (fallback) setView(fallback);
    }
  }, [authUser, canAccess, view]);

  function projectHealthFilters() {
    return {
      month: healthMode === "month" ? healthMonth : undefined,
      startMonth: healthMode === "range" ? healthStartMonth : undefined,
      endMonth: healthMode === "range" ? healthEndMonth : undefined,
      lifecycleStatus: lifecycleStatus || undefined,
      contractType: contractType || undefined,
      riskLevel: riskLevel || undefined,
      maintenance: maintenanceFilter || undefined,
      masterCategory: masterCategory || undefined,
      category: projectCategory || undefined,
      query: query || undefined,
      pageSize: ANALYTICS_PAGE_SIZE
    };
  }

  const load = useCallback(async () => {
    if (!authUser) { setLoading(false); return; }
    setLoading(true);
    setError(null);
    try {
      if (canAccess("sync")) setSync(await fetchSyncStatus());
      else setSync(null);
      if (view === "projects") {
        setHealth(await fetchProjectHealth(projectHealthFilters()));
      } else if (view === "employeeWorktime") {
        const [assessment, baseline] = await Promise.all([
          fetchEmployeeWorktime({
            period: worktimePeriod,
            month: worktimePeriod === "month" ? worktimeMonth : undefined,
            year: worktimePeriod === "year" ? worktimeYear : undefined,
            employeeName: query
          }),
          fetchWorktimeBaseline()
        ]);
        setEmployeeWorktime(assessment);
        setWorktimeBaseline(baseline);
      } else if (view === "settings") {
        setWorktimeBaseline(await fetchWorktimeBaseline());
      } else if (view === "resource") {
        setResourceOperations(await fetchResourceOperations({
          month: resourcePeriodMode === "month" ? resourceMonth : undefined,
          startMonth: resourcePeriodMode === "range" ? resourceStartMonth : undefined,
          endMonth: resourcePeriodMode === "range" ? resourceEndMonth : undefined,
          employeeName: resourceEmployeeName || undefined,
          pageSize: ANALYTICS_PAGE_SIZE
        }));
      } else if (view === "crm") {
        setRecords(await fetchRecords({ dataType, query, status, salesStage, page, pageSize: PAGE_SIZE, cursor: page > 1 ? recordCursors[page - 2] : undefined }));
      } else if (view === "sync") {
        setCrmImportLogs(await fetchCrmImportLogs({ page: crmImportLogPage, pageSize: IMPORT_LOG_PAGE_SIZE }));
      }
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        clearSession();
        setAuthUser(null);
        return;
      }
      setError(err instanceof Error ? err.message : "数据加载失败");
    } finally {
      setLoading(false);
    }
  }, [authUser, canAccess, view, query, contractType, riskLevel, maintenanceFilter, masterCategory, dataType, status, page, healthMode, healthMonth, healthStartMonth, healthEndMonth, worktimePeriod, worktimeMonth, worktimeYear, resourceMonth, resourcePeriodMode, resourceStartMonth, resourceEndMonth, resourceEmployeeName, crmImportLogPage, lifecycleStatus, projectCategory, salesStage, recordCursors]);

  useEffect(() => { void load(); }, [load]);

  useEffect(() => {
    setPage(1);
    setRecordCursors([]);
  }, [dataType, status, salesStage, query]);

  async function loadMoreHealth() {
    const cursor = health?.meta.next_cursor;
    if (!cursor) return;
    try {
      const next = await fetchProjectHealth({ ...projectHealthFilters(), cursor });
      setHealth(current => current ? { ...next, data: [...current.data, ...next.data] } : next);
    } catch (err) { setError(err instanceof Error ? err.message : "加载更多监控项目失败"); }
  }

  async function loadMoreResources() {
    const cursor = resourceOperations?.meta.next_cursor;
    if (!cursor) return;
    try {
      const next = await fetchResourceOperations({ month: resourcePeriodMode === "month" ? resourceMonth : undefined, startMonth: resourcePeriodMode === "range" ? resourceStartMonth : undefined, endMonth: resourcePeriodMode === "range" ? resourceEndMonth : undefined, employeeName: resourceEmployeeName || undefined, cursor, pageSize: ANALYTICS_PAGE_SIZE });
      setResourceOperations(current => current ? { ...next, data: [...current.data, ...next.data] } : next);
    } catch (err) { setError(err instanceof Error ? err.message : "加载更多人员失败"); }
  }

  function nextRecordsPage() {
    const cursor = records?.meta.next_cursor;
    if (!cursor) return;
    setRecordCursors(current => [...current.slice(0, page - 1), cursor]);
    setPage(current => current + 1);
  }

  useEffect(() => {
    if (view !== "employeeWorktime" || worktimePeriod !== "month" || worktimeMonthResolved) return;
    const latestMonth = employeeWorktime?.meta.latest_work_month;
    if (!latestMonth) return;
    setWorktimeMonth(latestMonth);
    window.sessionStorage.setItem(WORKTIME_MONTH_STORAGE_KEY, latestMonth);
    setWorktimeMonthResolved(true);
  }, [view, worktimePeriod, worktimeMonthResolved, employeeWorktime?.meta.latest_work_month]);

  function setPersistedWorktimeMonth(value: string) {
    setWorktimeMonth(value);
    window.sessionStorage.setItem(WORKTIME_MONTH_STORAGE_KEY, value);
    setWorktimeMonthResolved(true);
  }

  useEffect(() => {
    if (view !== "resource" || resourcePeriodMode !== "month" || resourceMonthResolved) return;
    const latestMonth = resourceOperations?.meta.latest_work_month;
    if (!latestMonth) return;
    setResourceMonth(latestMonth);
    window.sessionStorage.setItem(RESOURCE_MONTH_STORAGE_KEY, latestMonth);
    setResourceMonthResolved(true);
  }, [view, resourcePeriodMode, resourceMonthResolved, resourceOperations?.meta.latest_work_month]);

  function setPersistedResourceMonth(value: string) {
    setResourceMonth(value);
    window.sessionStorage.setItem(RESOURCE_MONTH_STORAGE_KEY, value);
    setResourceMonthResolved(true);
  }

  const cards = useMemo(() => view === "projects" ? [
    { label: "项目总数", value: health?.summary.total_projects ?? "—", tone: "blue" },
    { label: "合同总额", value: money(health?.summary.contract_value), tone: "green" },
    { label: "待回款金额", value: money(health?.summary.outstanding_receivable_amount), tone: "amber" },
    { label: "风险项目", value: health ? (health.summary.risk_critical_projects ?? 0) + (health.summary.risk_warning_projects ?? 0) : "—", tone: "violet" },
    { label: "待维护项目", value: health?.summary.maintenance_pending_projects ?? "—", tone: "amber" }
  ] : view === "employeeWorktime" ? [
    { label: "统计员工", value: employeeWorktime?.summary.total_employees ?? "—", tone: "blue" },
    { label: "工时充足", value: employeeWorktime?.summary.sufficient_count ?? "—", tone: "green" },
    { label: "需要关注", value: employeeWorktime?.summary.attention_count ?? "—", tone: "amber" },
    { label: "严重不足", value: employeeWorktime?.summary.critical_count ?? "—", tone: "violet" }
  ] : view === "resource" ? [
    { label: "纳入监控人员", value: resourceOperations?.summary.total_employees ?? "—", tone: "blue" },
    { label: "可调剂人员", value: resourceOperations?.summary.available_count ?? "—", tone: "green" },
    { label: "负荷较高", value: resourceOperations?.summary.high_load_count ?? "—", tone: "amber" },
    { label: "可调剂人天", value: resourceOperations?.summary.available_person_days ?? "—", tone: "violet" }
  ] : view === "sync" ? [
    { label: "导入状态", value: statusLabel(sync?.status), tone: sync?.status === "succeeded" ? "green" : "amber" },
    { label: "合同 / 订单源记录", value: sync?.source_stats.contract_order ?? "—", tone: "blue" },
    { label: "销售机会源记录", value: sync?.source_stats.sales_opportunity ?? "—", tone: "amber" },
    { label: "转项目已合并", value: sync?.validation.merged_opportunities ?? "—", tone: "violet" }
  ] : view === "settings" || view === "ontology" || view === "investment" || view === "department" ? [] : [
    { label: "有效记录", value: records?.meta.total_records ?? "—", tone: "blue" },
    { label: "合同 / 订单", value: sync?.source_stats.contract_order ?? "—", tone: "green" },
    { label: "销售机会", value: sync?.source_stats.sales_opportunity ?? "—", tone: "amber" },
    { label: "转项目商机", value: sync?.validation.merged_opportunities ?? "—", tone: "violet" }
  ], [view, health, employeeWorktime, resourceOperations, records, sync]);

  function switchView(next: View) {
    setView(next); setQuery(""); setQueryInput(""); setPage(1); setRecordCursors([]); setSelected(null);
    if (next === "sync") setCrmImportLogPage(1);
  }

  function submitSearch(event: FormEvent) {
    event.preventDefault(); setPage(1); setRecordCursors([]); setQuery(queryInput.trim());
  }

  async function openProject(projectNumber: string) {
    setError(null);
    try { setSelected(await fetchProject(projectNumber)); }
    catch (err) { setError(err instanceof Error ? err.message : "项目加载失败"); }
  }

  async function handleLogout() {
    await logout();
    setAuthUser(null);
    setSelected(null);
  }

  if (authLoading) return <div className="login-page"><div className="login-card loading-login">正在验证登录状态…</div></div>;
  if (!authUser) return <LoginScreen onLogin={user => setAuthUser(user)} />;

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand"><span className="brand-mark">PM</span><span>TSG PM</span></div>
        <nav className="sidebar-nav" aria-label="主菜单">
          <div className="nav-group">
            <p>查询与运营监控</p>
            {(canAccess("projects") || canAccess("health")) && <button className={`nav-item ${view === "projects" ? "active" : ""}`} onClick={() => switchView("projects")}><span>◫</span> 项目管理</button>}
            {canAccess("investment") && <button className={`nav-item ${view === "investment" ? "active" : ""}`} onClick={() => switchView("investment")}><span>◔</span> 投入成本分析</button>}
            {canAccess("department") && <button className={`nav-item ${view === "department" ? "active" : ""}`} onClick={() => switchView("department")}><span>◈</span> 部门经营</button>}
            {canAccess("resource") && <button className={`nav-item ${view === "resource" ? "active" : ""}`} onClick={() => switchView("resource")}><span>◉</span> 资源运营监控</button>}
            {canAccess("crm") && <button className={`nav-item ${view === "crm" ? "active" : ""}`} onClick={() => switchView("crm")}><span>⌘</span> CRM 数据查询</button>}
          </div>
          <div className="nav-group nav-group-maintenance">
            <p>数据维护</p>
            {canAccess("employeeWorktime") && <button className={`nav-item ${view === "employeeWorktime" ? "active" : ""}`} onClick={() => switchView("employeeWorktime")}><span>◷</span> 员工工时数据</button>}
            {canAccess("sync") && <button className={`nav-item ${view === "sync" ? "active" : ""}`} onClick={() => switchView("sync")}><span>⇧</span> 项目/商机数据维护</button>}
          </div>
          {canAccess("ontology") && <div className="nav-group nav-group-maintenance">
            <p>语义层</p>
            <button className={`nav-item ${view === "ontology" ? "active" : ""}`} onClick={() => switchView("ontology")}><span>⬡</span> 本体浏览器</button>
          </div>}
          <div className="nav-group nav-group-system">
            {authUser.is_admin && <><p>系统</p><button className={`nav-item ${view === "settings" ? "active" : ""}`} onClick={() => switchView("settings")}><span>⚙</span> 设置</button></>}
          </div>
        </nav>
        <div className="sidebar-foot">
          <span className={`status-dot ${sync?.status === "succeeded" ? "ok" : "warn"}`} />
          <div><strong>{authUser.username}</strong><small>{authUser.role_label ?? (authUser.is_admin ? "管理员" : "受限访问用户")}{authUser.data_scope === "own" ? " · 仅本人项目" : ""}</small></div>
          <button type="button" className="logout-button" onClick={() => void handleLogout()}>Logout</button>
        </div>
      </aside>

      <main>
        <header className="topbar">
          <div><p className="eyebrow">PROJECT LIFECYCLE CONTROL</p><h1>{view === "projects" ? "项目管理" : view === "investment" ? "投入成本分析" : view === "department" ? "部门经营" : view === "resource" ? "资源运营监控" : view === "employeeWorktime" ? "员工工时数据" : view === "sync" ? "项目/商机数据维护" : view === "settings" ? "系统设置" : view === "ontology" ? "本体浏览器" : "CRM 数据查询"}</h1></div>
          {view === "crm" && <div className="top-actions">
            <button className="button secondary" onClick={() => void downloadExport("csv")}>导出 CSV</button>
          </div>}
        </header>
        {error && <div className="error-banner">{error}</div>}
        {cards.length > 0 && <section className="metrics">{cards.map(card => <article className={`metric ${card.tone}`} key={card.label}><span>{card.label}</span><strong>{card.value}</strong></article>)}</section>}

        {view === "projects" ? (
          <ProjectWorkspace
            health={health} loading={loading}
            queryInput={queryInput} setQueryInput={setQueryInput} submitSearch={submitSearch}
            contractType={contractType} setContractType={setContractType}
            lifecycleStatus={lifecycleStatus} setLifecycleStatus={setLifecycleStatus}
            category={projectCategory} setCategory={setProjectCategory}
            riskLevel={riskLevel} setRiskLevel={setRiskLevel}
            maintenance={maintenanceFilter} setMaintenance={setMaintenanceFilter}
            masterCategory={masterCategory} setMasterCategory={setMasterCategory}
            mode={healthMode} setMode={setHealthMode}
            month={healthMonth} setMonth={setHealthMonth}
            startMonth={healthStartMonth} setStartMonth={setHealthStartMonth}
            endMonth={healthEndMonth} setEndMonth={setHealthEndMonth}
            openProject={canMaintainProjects ? openProject : undefined} loadMore={loadMoreHealth}
          />
        ) : view === "employeeWorktime" ? (
          <EmployeeWorktime
            assessment={employeeWorktime}
            loading={loading} queryInput={queryInput}
            setQueryInput={setQueryInput} submitSearch={submitSearch}
            period={worktimePeriod} setPeriod={setWorktimePeriod}
            month={worktimeMonth} setMonth={setPersistedWorktimeMonth}
            year={worktimeYear} setYear={setWorktimeYear}
            refresh={load}
          />
        ) : view === "resource" ? (
          <ResourceOperations
            data={resourceOperations} loading={loading} month={resourceMonth}
            setMonth={setPersistedResourceMonth}
            periodMode={resourcePeriodMode} setPeriodMode={setResourcePeriodMode}
            startMonth={resourceStartMonth} setStartMonth={setResourceStartMonth}
            endMonth={resourceEndMonth} setEndMonth={setResourceEndMonth}
            employeeInput={resourceEmployeeInput} setEmployeeInput={setResourceEmployeeInput}
            submitEmployeeSearch={(event) => { event.preventDefault(); setResourceEmployeeName(resourceEmployeeInput.trim()); }}
            clearEmployeeSearch={() => { setResourceEmployeeInput(""); setResourceEmployeeName(""); }}
            openProject={openProject} loadMore={loadMoreResources}
          />
        ) : view === "sync" ? (
          <SyncStatusPanel sync={sync} logs={crmImportLogs} logPage={crmImportLogPage} setLogPage={setCrmImportLogPage} refresh={load} reportError={setError} />
        ) : view === "settings" ? (
          <SystemSettings baseline={worktimeBaseline} onBaselineSaved={setWorktimeBaseline} />
        ) : view === "ontology" ? (
          <OntologyExplorer />
        ) : view === "department" ? (
          <DepartmentOperationsPage canEditPlan={canAccess("department")} />
        ) : view === "investment" ? (
          <InvestmentAnalysisPage
            canSeeCost={Boolean(authUser.is_admin || canAccess("projects") || canAccess("crm"))}
            canEditOutcome={canAccess("projects") || canAccess("investment")}
          />
        ) : (
          <CrmRecords
            records={records} loading={loading} queryInput={queryInput}
            setQueryInput={setQueryInput} submitSearch={submitSearch}
            dataType={dataType} setDataType={setDataType} status={status}
            setStatus={setStatus} salesStage={salesStage} setSalesStage={setSalesStage} page={page}
            previousPage={() => setPage(value => Math.max(1, value - 1))}
            nextPage={nextRecordsPage}
          />
        )}
      </main>
      {selected && <ProjectDrawer
        project={selected}
        close={() => setSelected(null)}
        refresh={async () => {
          const refreshed = await fetchProject(selected.project_number);
          setSelected(refreshed);
          await load();
        }}
      />}
      {canAccess("projects") && <PmAssistant />}
    </div>
  );
}

const MENU_OPTIONS: Array<{ value: MenuPermission; label: string; group: string }> = [
  { value: "projects", label: "项目管理（查看与维护）", group: "查询与运营监控" },
  { value: "health", label: "项目管理（只读查看）", group: "查询与运营监控" },
  { value: "resource", label: "资源运营监控", group: "查询与运营监控" },
  { value: "investment", label: "投入成本分析", group: "查询与运营监控" },
  { value: "department", label: "部门经营（含金额与人员健康）", group: "查询与运营监控" },
  { value: "crm", label: "CRM 数据查询", group: "查询与运营监控" },
  { value: "employeeWorktime", label: "员工工时数据", group: "数据维护" },
  { value: "sync", label: "项目/商机数据维护", group: "数据维护" },
  { value: "ontology", label: "本体浏览器", group: "语义层" },
  { value: "settings", label: "系统设置", group: "系统" }
];

function LoginScreen({ onLogin }: { onLogin: (user: AuthenticatedUser) => void }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!username.trim() || !password) { setError("请输入账号和密码"); return; }
    setSubmitting(true); setError(null);
    try {
      const session = await login(username.trim(), password);
      onLogin(session.user);
    } catch (err) {
      setError(err instanceof Error ? err.message : "登录失败，请稍后重试");
    } finally { setSubmitting(false); }
  }

  return <main className="login-page">
    <form className="login-card" onSubmit={submit}>
      <div className="login-brand"><span className="brand-mark">PM</span><span>TSG PM</span></div>
      <p className="eyebrow">PROJECT LIFECYCLE CONTROL</p>
      <h1>Login</h1>
      <p>登录后按账户权限访问项目运营与数据维护功能。</p>
      {error && <div className="login-error" role="alert">{error}</div>}
      <label>账号<input autoComplete="username" value={username} onChange={event => setUsername(event.target.value)} disabled={submitting} /></label>
      <label>密码<input type="password" autoComplete="current-password" value={password} onChange={event => setPassword(event.target.value)} disabled={submitting} /></label>
      <button type="submit" className="button primary login-submit" disabled={submitting}>{submitting ? "登录中…" : "Login"}</button>
    </form>
  </main>;
}

function SystemSettings(props: {
  baseline: WorktimeBaseline | null;
  onBaselineSaved: (baseline: WorktimeBaseline) => void;
}) {
  return <>
    <section className="settings-page settings-operation-page">
      <header className="settings-page-head"><div><h2>运营评估与产能基线</h2><p>集中维护工时核算口径、资源调剂判断和员工利用率评价标准；修改后立即用于资源运营监控和员工工时统计。</p></div></header>
      <WorktimeReviewSettings baseline={props.baseline} onSaved={props.onBaselineSaved} />
    </section>
    <UserSettings />
  </>;
}

function WorktimeReviewSettings(props: {
  baseline: WorktimeBaseline | null;
  onSaved: (baseline: WorktimeBaseline) => void;
}) {
  const [draft, setDraft] = useState<Partial<WorktimeBaseline>>({});
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => {
    if (props.baseline) setDraft(props.baseline);
  }, [props.baseline]);

  function setValue(field: keyof WorktimeBaseline, value: string) {
    setDraft(current => ({ ...current, [field]: value }));
  }

  async function save(event: FormEvent) {
    event.preventDefault();
    setSaving(true); setMessage(null);
    try {
      const updated = await updateWorktimeBaseline(draft);
      setDraft(updated);
      props.onSaved(updated);
      setMessage("运营评估与产能基线已保存，并已应用到后续统计。");
    } catch (err) {
      setMessage(err instanceof Error ? err.message : "保存失败");
    } finally {
      setSaving(false);
    }
  }

  const attention = draft.attention_utilization_percent ?? "—";
  const minimum = draft.minimum_utilization_percent ?? "—";
  const excellent = draft.excellent_utilization_percent ?? "—";
  const overload = draft.overload_utilization_percent ?? "—";
  return <section className="panel review-settings-panel">
    <div className="settings-section-head"><div><h3>工时与资源复核规则 <span className="manual-tag">人工维护</span></h3><p>当期产能会按中国法定工作日、节假日及调休补班自动计算；以下阈值均为占当期个人可用工时的比例。</p></div></div>
    {message && <div className="settings-message review-settings-message">{message}</div>}
    <div className="review-definition-grid">
      <article><span>产能基线</span><strong>实际工作日 × 标准工时/天</strong><p>法定节假日自动扣除，调休补班自动计入；年度参考数只供管理对照。</p></article>
      <article><span>员工工时利用率</span><strong>填报工时 ÷ 当期产能</strong><p>用于识别工时不足、需要关注、基本达标与工时充足的人员。</p></article>
      <article><span>资源调剂复核</span><strong>项目投入 + 内部可调剂工时</strong><p>依据以下阈值给出可调剂、均衡、高负荷或超负荷建议。</p></article>
    </div>
    <form className="review-settings-form" onSubmit={save}>
      <div className="review-settings-form-head"><div><h4>运行参数</h4><p>利用率阈值必须满足：调剂优先 &lt; 适度支持 &lt; 高负荷起点 &lt; 超负荷起点；预算阈值必须满足：关注线 &lt; 高风险线 &lt; 100%。</p></div></div>
      <div className="review-settings-inputs">
        <label>标准工时/天<input type="number" min="0.1" step="0.1" value={draft.standard_hours_per_day ?? ""} onChange={event => setValue("standard_hours_per_day", event.target.value)} disabled={saving} required /></label>
        <label>调剂优先阈值(%)<input type="number" min="0.1" max="299.9" step="0.1" value={draft.attention_utilization_percent ?? ""} onChange={event => setValue("attention_utilization_percent", event.target.value)} disabled={saving} required /></label>
        <label>适度支持阈值(%)<input type="number" min="0.1" max="299.9" step="0.1" value={draft.minimum_utilization_percent ?? ""} onChange={event => setValue("minimum_utilization_percent", event.target.value)} disabled={saving} required /></label>
        <label>高负荷起点(%)<input type="number" min="0.1" max="299.9" step="0.1" value={draft.excellent_utilization_percent ?? ""} onChange={event => setValue("excellent_utilization_percent", event.target.value)} disabled={saving} required /></label>
        <label>超负荷起点(%)<input type="number" min="0.1" max="300" step="0.1" value={draft.overload_utilization_percent ?? ""} onChange={event => setValue("overload_utilization_percent", event.target.value)} disabled={saving} required /></label>
        <label>预算关注线(%)<input type="number" min="0.1" max="99.9" step="0.1" value={draft.budget_attention_percent ?? ""} onChange={event => setValue("budget_attention_percent", event.target.value)} disabled={saving} required /></label>
        <label>预算高风险线(%)<input type="number" min="0.1" max="99.9" step="0.1" value={draft.budget_high_risk_percent ?? ""} onChange={event => setValue("budget_high_risk_percent", event.target.value)} disabled={saving} required /></label>
        <label className="review-notes">配置说明<input value={draft.notes ?? ""} onChange={event => setValue("notes", event.target.value)} disabled={saving} placeholder="例如：2026 年度资源复核口径" /></label>
      </div>
      <div className="review-rule-legend" aria-label="当前资源调剂判定规则">
        <span>≤ {attention}%：可优先调剂</span><span>{attention}%–{minimum}%：可适度支持</span><span>{minimum}%–{excellent}%：安排基本均衡</span><span>{excellent}%–{overload}%：负荷较高</span><span>&gt; {overload}%：本月负荷过高（单月；连续多月为资源健康的「持续过载」）</span>
      </div>
      <div className="review-rule-legend" aria-label="当前项目预算判定规则">
        <span>预算使用率 &lt; {draft.budget_attention_percent ?? "—"}%：正常</span><span>≥ {draft.budget_attention_percent ?? "—"}%：需关注（项目风险同为关注）</span><span>≥ {draft.budget_high_risk_percent ?? "—"}%：高风险</span><span>&gt; 100%：已超预算</span><span>按工作量结算的项目不参与预算规则</span>
      </div>
      <details className="review-reference-fields"><summary>年度参考数据（不参与当期产能计算）</summary><div>
        <label>年度参考工作日<input type="number" min="0.1" step="0.1" value={draft.standard_work_days_per_year ?? ""} onChange={event => setValue("standard_work_days_per_year", event.target.value)} disabled={saving} /></label>
        <label>年度参考工时(h)<input type="number" min="0.1" step="0.1" value={draft.annual_baseline_hours ?? ""} onChange={event => setValue("annual_baseline_hours", event.target.value)} disabled={saving} /></label>
        <label>有效参考工时(h)<input type="number" min="0.1" step="0.1" value={draft.effective_baseline_hours ?? ""} onChange={event => setValue("effective_baseline_hours", event.target.value)} disabled={saving} /></label>
        <label>月均参考工时(h)<input type="number" min="0.1" step="0.1" value={draft.monthly_baseline_hours ?? ""} onChange={event => setValue("monthly_baseline_hours", event.target.value)} disabled={saving} /></label>
        <label>季度参考工时(h)<input type="number" min="0.1" step="0.1" value={draft.quarterly_baseline_hours ?? ""} onChange={event => setValue("quarterly_baseline_hours", event.target.value)} disabled={saving} /></label>
      </div></details>
      <div className="review-settings-actions"><small>当前配置：{props.baseline ? `更新于 ${new Date(props.baseline.updated_at).toLocaleString("zh-CN", { hour12: false })}` : "正在加载…"}</small><button className="button primary" type="submit" disabled={saving || !props.baseline}>{saving ? "保存中…" : "保存运营评估配置"}</button></div>
    </form>
  </section>;
}

function UserSettings() {
  const [users, setUsers] = useState<ManagedUser[]>([]);
  const [selected, setSelected] = useState<ManagedUser | null>(null);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [role, setRole] = useState<UserRole>("project_manager");
  const [personName, setPersonName] = useState("");
  const [isActive, setIsActive] = useState(true);
  const [permissions, setPermissions] = useState<MenuPermission[]>(defaultMenus("project_manager"));
  const isAdmin = role === "admin";
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    try { setUsers(await fetchUsers()); }
    catch (err) { setMessage(err instanceof Error ? err.message : "用户列表加载失败"); }
    finally { setLoading(false); }
  }, []);
  useEffect(() => { void refresh(); }, [refresh]);

  function beginCreate() {
    setSelected(null); setUsername(""); setPassword(""); setRole("project_manager"); setPersonName(""); setIsActive(true); setPermissions(defaultMenus("project_manager")); setMessage(null);
  }
  function beginEdit(user: ManagedUser) {
    setSelected(user); setUsername(user.username); setPassword(""); setRole(user.role ?? (user.is_admin ? "admin" : "pmo")); setPersonName(user.person_name ?? ""); setIsActive(user.is_active); setPermissions(user.permissions); setMessage(null);
  }
  // A role brings its default menus; menus can still be adjusted afterwards (D9).
  function chooseRole(next: UserRole) {
    setRole(next); setPermissions(defaultMenus(next));
  }
  function togglePermission(permission: MenuPermission) {
    setPermissions(current => current.includes(permission) ? current.filter(item => item !== permission) : [...current, permission]);
  }
  async function submit(event: FormEvent) {
    event.preventDefault();
    setSaving(true); setMessage(null);
    try {
      if (selected) {
        await updateUser(selected.username, { password: password || undefined, role, person_name: personName.trim() || null, is_active: isActive, permissions });
        setMessage("用户权限已保存");
      } else {
        await createUser({ username, password, role, person_name: personName.trim() || null, permissions });
        setMessage("用户已添加");
      }
      await refresh();
      beginCreate();
    } catch (err) { setMessage(err instanceof Error ? err.message : "保存失败"); }
    finally { setSaving(false); }
  }

  return <section className="settings-page">
    <header className="settings-page-head"><div><h2>账户与菜单权限</h2><p>管理可访问本系统的账户和页面范围；密码仅以安全摘要保存。</p></div><button className="button secondary" type="button" onClick={beginCreate}>+ 添加用户</button></header>
    <div className="settings-grid">
      <section className="panel user-list-panel"><div className="settings-section-head"><div><h3>系统用户</h3><p>{loading ? "正在加载…" : `共 ${users.length} 个账户`}</p></div></div>
        <div className="user-list">{users.map(user => <article className={`user-row ${selected?.username === user.username ? "selected" : ""}`} key={user.username}>
          <div><strong>{user.username}{user.person_name ? `（${user.person_name}）` : ""}</strong><small>{user.role_label ?? (user.is_admin ? "管理员" : "未设角色")} · {user.is_admin ? "全部菜单" : `${user.permissions.length} 项菜单权限`}{user.data_scope === "own" ? " · 仅本人项目" : ""} · {user.is_active ? "已启用" : "已停用"}</small></div>
          <button type="button" className="button secondary" onClick={() => beginEdit(user)}>编辑</button>
        </article>)}{!loading && !users.length && <p className="detail-empty">暂无用户</p>}</div>
      </section>
      <form className="panel user-form" onSubmit={submit}>
        <div className="settings-section-head"><div><h3>{selected ? `编辑用户：${selected.username}` : "添加用户"}</h3><p>{selected ? "留空密码表示不修改原密码。" : "新增用户后按选择的菜单权限访问系统。"}</p></div>{selected && <button type="button" className="text-button" onClick={beginCreate}>取消编辑</button>}</div>
        {message && <div className="settings-message">{message}</div>}
        <div className="user-form-fields"><label>登录账号<input value={username} onChange={event => setUsername(event.target.value)} disabled={Boolean(selected) || saving} required /></label>
        <label>{selected ? "新密码（可选）" : "登录密码"}<input type="password" value={password} onChange={event => setPassword(event.target.value)} disabled={saving} minLength={selected ? undefined : 8} required={!selected} /></label></div>
        <div className="user-form-fields">
          <label>角色<select value={role} onChange={event => chooseRole(event.target.value as UserRole)} disabled={saving || selected?.username === "admin"}>
            {ROLE_OPTIONS.map(option => <option key={option.value} value={option.value}>{option.label}</option>)}
          </select><small>{roleOption(role)?.hint}</small></label>
          <label>姓名{role === "project_manager" ? "（必填）" : "（可选）"}<input value={personName} onChange={event => setPersonName(event.target.value)} disabled={saving} required={role === "project_manager"} placeholder="与项目的「项目经理」字段一致" /></label>
        </div>
        {selected && <label className="check-row"><input type="checkbox" checked={isActive} onChange={event => setIsActive(event.target.checked)} disabled={saving || selected.username === "admin"} />启用此账户</label>}
        {!isAdmin && <fieldset><legend>可访问菜单</legend><div className="permission-grid">{MENU_OPTIONS.filter(item => item.value !== "settings").map(item => <label key={item.value}><input type="checkbox" checked={permissions.includes(item.value)} onChange={() => togglePermission(item.value)} disabled={saving || !menuAllowed(role, item.value)} />{item.label}<small>{menuAllowed(role, item.value) ? item.group : "项目经理不可用"}</small></label>)}</div></fieldset>}
        <button className="button primary settings-save" type="submit" disabled={saving}>{saving ? "保存中…" : selected ? "保存用户设置" : "添加用户"}</button>
      </form>
    </div>
  </section>;
}

function PmAssistant() {
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState("");
  const [messages, setMessages] = useState<PmAssistantChatMessage[]>([]);
  const [sources, setSources] = useState<string[]>([]);
  const [suggestions, setSuggestions] = useState<PmAssistantSuggestion[]>([]);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function send(question = draft) {
    const content = question.trim();
    if (!content || sending) return;
    const nextHistory = [...messages, { role: "user" as const, content }];
    setMessages(nextHistory);
    setDraft("");
    setSending(true);
    setError(null);
    try {
      const response = await chatWithPmAssistant(content, messages);
      setMessages([...nextHistory, { role: "assistant", content: response.answer }]);
      setSources(response.sources);
      setSuggestions(response.suggestions ?? []);
    } catch (err) {
      setError(err instanceof Error ? err.message : "PM Assistant 暂时无法回答，请稍后重试。");
    } finally {
      setSending(false);
    }
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    void send();
  }

  return <div className="pm-assistant" aria-live="polite">
    {open && <section className="pm-assistant-panel" role="dialog" aria-label="PM Assistant">
      <header className="pm-assistant-head">
        <div><span className="pm-assistant-kicker">AI PROJECT COPILOT</span><h2>PM Assistant</h2><p>按你的权限调用系统本体回答项目管理问题</p></div>
        <button type="button" className="pm-assistant-close" aria-label="关闭 PM Assistant" onClick={() => setOpen(false)}>×</button>
      </header>
      <div className="pm-assistant-conversation">
        {!messages.length && <div className="pm-assistant-welcome"><strong>可以问我什么？</strong><p>我会调用系统的本体函数和对象查询，按「结论、数据、依据」回答；资料不足时说明缺少什么。需要改数据时只给出经系统预检的建议，不会直接修改。</p><div className="pm-assistant-suggestions">{["哪些项目需要优先关注？", "目前有哪些待回款项目？", "工时超预算的项目有哪些？"].map(question => <button key={question} type="button" onClick={() => void send(question)}>{question}</button>)}</div></div>}
        {messages.map((item, index) => <article key={`${item.role}-${index}`} className={`pm-message ${item.role}`}><span>{item.role === "user" ? "你" : "PM Assistant"}</span>{item.role === "assistant" ? <Markdown text={item.content} /> : <p>{item.content}</p>}</article>)}
        {sending && <article className="pm-message assistant pending"><span>PM Assistant</span><p>正在查询系统本体并整理回答…</p></article>}
      </div>
      {sources.length > 0 && <div className="pm-assistant-sources"><span>本次依据</span>{sources.map(source => <small key={source}>{source}</small>)}</div>}
      {suggestions.length > 0 && <div className="pm-assistant-actions"><span>建议动作（未执行）</span>
        {suggestions.map((item, index) => <article key={`${item.actionType}-${index}`} className={item.valid ? "valid" : "invalid"}>
          <header><strong>{item.displayName}</strong><i>{item.valid ? "预检通过" : "预检未通过"}</i></header>
          <p>{Object.entries(item.parameters).map(([key, value]) => `${key}：${typeof value === "string" ? value : JSON.stringify(value)}`).join("；")}</p>
          {item.reason && <p>理由：{item.reason}</p>}
          {item.reasons.length > 0 && <ul>{item.reasons.map(reason => <li key={reason}>{reason}</li>)}</ul>}
        </article>)}
        <small>请在业务页面执行；执行时系统会再次校验。</small>
      </div>}
      {error && <p className="pm-assistant-error">{error}</p>}
      <form className="pm-assistant-form" onSubmit={submit}><label htmlFor="pm-assistant-input">你的问题</label><div><input id="pm-assistant-input" value={draft} onChange={event => setDraft(event.target.value)} disabled={sending} placeholder="例如：哪些项目本月需要跟进？" maxLength={2000} /><button className="button primary" disabled={sending || !draft.trim()} type="submit">发送</button></div></form>
    </section>}
    <button type="button" className="pm-assistant-launcher" aria-expanded={open} onClick={() => setOpen(!open)}><span>AI</span><strong>PM Assistant</strong></button>
  </div>;
}

function RiskBadge({ project }: { project: Pick<ProjectSummary, "project_number" | "risk_level" | "anomaly_count" | "anomaly_alerts" | "risk_reasons"> }) {
  const alerts = project.anomaly_alerts ?? [];
  const hasDetails = alerts.length > 0 || project.risk_reasons.length > 0;
  const anchorRef = useRef<HTMLDivElement>(null);
  const popoverRef = useRef<HTMLDivElement>(null);
  const [open, setOpen] = useState(false);
  const [position, setPosition] = useState({ left: 12, top: 12, placement: "bottom" });

  const positionPopover = useCallback(() => {
    const anchor = anchorRef.current;
    if (!anchor) return;
    const rect = anchor.getBoundingClientRect();
    const width = Math.min(320, window.innerWidth - 24);
    const height = popoverRef.current?.getBoundingClientRect().height ?? 180;
    const canOpenAbove = rect.top - height - 10 >= 12;
    const placement = rect.bottom + height + 10 > window.innerHeight - 12 && canOpenAbove ? "top" : "bottom";
    const top = placement === "top"
      ? Math.max(12, rect.top - height - 10)
      : Math.min(rect.bottom + 10, Math.max(12, window.innerHeight - height - 12));
    setPosition({ left: Math.max(12, Math.min(rect.left, window.innerWidth - width - 12)), top, placement });
  }, []);

  useEffect(() => {
    if (!open) return;
    positionPopover();
    const frame = window.requestAnimationFrame(positionPopover);
    window.addEventListener("resize", positionPopover);
    window.addEventListener("scroll", positionPopover, true);
    return () => {
      window.cancelAnimationFrame(frame);
      window.removeEventListener("resize", positionPopover);
      window.removeEventListener("scroll", positionPopover, true);
    };
  }, [open, positionPopover]);

  if (!hasDetails) return <div className="risk-cell"><span className={`risk ${project.risk_level}`}>{riskLabel(project.risk_level)}</span>{project.anomaly_count > 0 && <small className="danger-text">{project.anomaly_count} 项提醒</small>}</div>;

  return <div className="risk-cell" ref={anchorRef} onClick={event => event.stopPropagation()} onPointerEnter={() => setOpen(true)} onPointerLeave={() => setOpen(false)}>
    <button type="button" className="risk-trigger" aria-expanded={open} aria-describedby={`risk-popover-${project.project_number}`} onFocus={() => setOpen(true)} onBlur={() => setOpen(false)} onClick={event => { event.stopPropagation(); setOpen(current => !current); }}>
      <span className={`risk ${project.risk_level}`}>{riskLabel(project.risk_level)}</span>
      {project.anomaly_count > 0 && <small className="danger-text">{project.anomaly_count} 项提醒</small>}
    </button>
    {open && createPortal(<div ref={popoverRef} id={`risk-popover-${project.project_number}`} className="risk-popover" data-placement={position.placement} role="tooltip" style={{ left: position.left, top: position.top }}>
      <h4>{project.project_number} 风险点</h4>
      {alerts.length > 0 ? alerts.map((alert, index) => <article key={`${alert.code}-${index}`}>
        <div><strong>{alert.title}</strong><span className={alert.severity}>{alert.severity === "critical" ? "严重" : alert.severity === "warning" ? "关注" : "提示"}</span></div>
        <p>{alert.detail}</p>
      </article>) : project.risk_reasons.map(reason => <article key={reason}><p>{reason}</p></article>)}
    </div>, document.body)}
  </div>;
}

function BudgetCell({ row }: { row: ProjectHealthRow }) {
  if (row.contract_type === "open_ended") return <><div>{row.billable_person_days ?? "0.00"} 人天</div><small>开口合同 · 可结算</small></>;
  if (row.budget_person_days == null) return <span className="danger-text">请填预算</span>;
  const percent = Number(row.burn_rate_percent ?? 0);
  return <div className="budget"><i><em className={percent > 100 ? "over" : percent >= 80 ? "near" : undefined} style={{ width: `${Math.min(percent, 100)}%` }} /></i><small>{row.cumulative_actual_person_days}/{row.budget_person_days} 人天 · {row.burn_rate_percent ?? "—"}%</small></div>;
}

function PaymentCell({ row }: { row: ProjectHealthRow }) {
  const summary = row.payment_summary;
  const contractAmount = Number(row.contract_value);
  const receivedAmount = Number(row.crm_received_amount);
  const hasContractAmount = Number.isFinite(contractAmount) && contractAmount > 0;
  const receivedRatio = hasContractAmount && Number.isFinite(receivedAmount) ? Math.min(Math.max(receivedAmount / contractAmount * 100, 0), 100) : null;
  const nextTerm = row.payment_term_summary?.[0];
  return <div className="payment-cell">
    {row.contract_value === null ? <small>金额需要财务金额权限</small> : row.revenue_settlement_basis === "workload" ? <>
      <strong>确认收入 {money(row.recognized_revenue_amount)}</strong>
      <small>已回款 {money(row.crm_received_amount)} · 待回款 {money(row.outstanding_receivable_amount)}</small>
      <small className="payment-workload-note">按工作量结算 · 框架额 {money(row.contract_value)}</small>
    </> : hasContractAmount ? <>
      <strong>已回款 {receivedRatio?.toFixed(1)}%</strong>
      <small>{money(row.crm_received_amount)} / {money(row.contract_value)}</small>
    </> : <small>暂无合同金额</small>}
    {nextTerm && <small className="payment-term-summary">下一回款约定：{nextTerm.name}{nextTerm.payment_ratio ? ` ${nextTerm.payment_ratio}%` : ""} · {nextTerm.node_status === "paid" ? "已到账" : nextTerm.planned_date ? `计划 ${nextTerm.planned_date}` : "待排期"}</small>}
    {summary.total_nodes > 0 && <small className={summary.next_node?.deadline.urgency === "overdue" ? "danger-text" : ""}>{summary.paid_nodes}/{summary.total_nodes} 个节点已到账{summary.next_node ? ` · 下个：${summary.next_node.name} ${summary.next_node.deadline.label}` : ""}</small>}
  </div>;
}

function ProjectWorkspace(props: {
  health: ProjectHealthResponse | null; loading: boolean;
  queryInput: string; setQueryInput: (value: string) => void; submitSearch: (event: FormEvent) => void;
  contractType: string; setContractType: (value: string) => void;
  riskLevel: string; setRiskLevel: (value: string) => void;
  maintenance: string; setMaintenance: (value: string) => void;
  masterCategory: string; setMasterCategory: (value: string) => void;
  lifecycleStatus: string; setLifecycleStatus: (value: string) => void;
  category: string; setCategory: (value: string) => void;
  loadMore: () => Promise<void>;
  mode: "month" | "range"; setMode: (value: "month" | "range") => void;
  month: string; setMonth: (value: string) => void;
  startMonth: string; setStartMonth: (value: string) => void;
  endMonth: string; setEndMonth: (value: string) => void;
  openProject?: (projectNumber: string) => void;
}) {
  return <section className="panel project-workspace">
    <div className="panel-head">
      <div>
        <h2>项目主数据库</h2>
        <p>按项目代码汇总 CRM 主数据、里程碑、回款与员工工时，并按统计期间监控预算燃尽、超支风险和月度趋势{props.openProject ? "；点击项目可维护合同类别、预算人天、里程碑、回款和管理干预" : ""}</p>
      </div>
      <Search value={props.queryInput} setValue={props.setQueryInput} submit={props.submitSearch} placeholder="搜索项目名称、客户名称或项目编号" />
    </div>
    <div className="data-boundary"><span className="source-tag">系统接口数据</span><p>合同、商机、金额、销售人员和工时来自导入，只读</p><span className="manual-tag">人工维护数据</span><p>项目类型、生命周期、项目经理、预算人天、里程碑和回款约定由管理人员维护</p></div>
    <div className="filters health-filters">
      <select value={props.masterCategory} aria-label="选择主数据类别" onChange={e => props.setMasterCategory(e.target.value)}><option value="">项目与商机</option><option value="项目">项目</option><option value="商机">商机</option></select>
      <select value={props.category} aria-label="选择 CRM 分类" onChange={e => props.setCategory(e.target.value)}><option value="">全部 CRM 分类</option>{(props.health?.meta.categories ?? []).map(category => <option key={category} value={category}>{category}</option>)}</select>
      <select value={props.contractType} aria-label="选择项目类型" onChange={e => props.setContractType(e.target.value)}><option value="">全部项目类型</option><option value="construction">系统实施</option><option value="maintenance">TSnT365</option><option value="open_ended">开口合同</option><option value="sales">售前阶段</option><option value="unknown">未分类</option></select>
      <select value={props.lifecycleStatus} aria-label="选择项目状态" onChange={e => props.setLifecycleStatus(e.target.value)}><LifecycleStatusOptions statuses={props.health?.meta.lifecycle_statuses} /></select>
      <select value={props.riskLevel} aria-label="选择风险等级" onChange={e => props.setRiskLevel(e.target.value)}><option value="">全部风险</option><option value="critical">严重</option><option value="warning">关注</option><option value="healthy">健康</option></select>
      <select value={props.maintenance} aria-label="选择维护状态" onChange={e => props.setMaintenance(e.target.value)}><option value="">全部维护状态</option><option value="missing">待维护</option><option value="complete">已维护</option></select>
      <span className="filter-divider" aria-hidden="true" />
      <select value={props.mode} aria-label="选择统计方式" onChange={e => props.setMode(e.target.value as "month" | "range")}><option value="month">按单月</option><option value="range">按时间范围</option></select>
      {props.mode === "month" ? <label>统计月份<MonthPicker value={props.month} onChange={props.setMonth} label="选择统计月份" /></label> : <><label>开始月份<MonthPicker value={props.startMonth} onChange={props.setStartMonth} label="选择开始月份" /></label><label>结束月份<MonthPicker value={props.endMonth} onChange={props.setEndMonth} label="选择结束月份" /></label></>}
      <span className="result-count">统计期间：{props.health?.meta.start_month ?? "—"} 至 {props.health?.meta.end_month ?? "—"} · 共 {props.health?.summary.total_projects ?? 0} 个项目</span>
      {props.health?.redacted_markings?.length ? <RedactionNote markings={props.health.redacted_markings} /> : null}
    </div>
    <div className="table-wrap sticky-columns project-table"><table>
      <thead>
        <tr className="column-groups">
          <th colSpan={2}>项目</th><th colSpan={9}>项目台账</th><th colSpan={10}>运营监控（统计期间）</th>
        </tr>
        <tr><th>项目代码</th><th>项目名称</th><th>风险</th><th>主数据类别</th><th>CRM 分类</th><th>项目类型</th><th>PM负责人</th><th>销售人员</th><th>项目状态</th><th>当前里程碑</th><th>回款</th><th>计划周期</th><th>预算消耗</th><th>月均预算(人天)</th><th>本期投入(人天)</th><th>上期投入(人天)</th><th>参与人数</th><th>超支预警</th><th>月度趋势</th><th>综合健康状态</th><th>预算耗尽预测</th></tr>
      </thead>
      <tbody>
        {props.loading ? <LoadingRows colSpan={21} /> : props.health?.data.map(row => <ProjectWorkspaceRow key={row.project_number} row={row} open={props.openProject ? () => props.openProject?.(row.project_number) : undefined} />)}
        {!props.loading && props.health?.data.length === 0 && <tr><td className="empty" colSpan={21}>没有符合条件的项目</td></tr>}
      </tbody>
    </table></div>
    {props.health?.meta.next_cursor && <div className="pagination cursor-pagination"><button type="button" onClick={() => void props.loadMore()}>加载更多项目</button></div>}
  </section>;
}

function ProjectWorkspaceRow({ row, open }: { row: ProjectHealthRow; open?: () => void }) {
  const milestone = row.current_milestone;
  return <tr className={open ? "clickable" : undefined} onClick={open} tabIndex={open ? 0 : undefined}
    onKeyDown={open ? event => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); open(); } } : undefined}>
    <td><strong className="project-code">{row.project_number}</strong></td>
    <td><div className="subject">{displayProjectName(row.project_number, row.project_name)}</div><small>{row.customer ?? "—"}</small></td>
    <td><RiskBadge project={row} /></td>
    <td><span className="category-pill">{row.master_category}</span></td>
    <td>{row.category ?? "未设置"}</td>
    <td>{contractTypeLabel(row.contract_type)}</td>
    <td>{row.pm_owner ?? <span className="danger-text">请添加PM负责人</span>}</td>
    <td>{row.sales_owner ?? "未匹配"}</td>
    <td>{lifecycleLabel(row.project_status)}</td>
    <td>{milestone ? <><div>{milestone.name}</div><small className={milestone.deadline.urgency === "overdue" ? "danger-text" : ""}>{milestone.deadline.label}</small></> : <span>未设置</span>}</td>
    <td><PaymentCell row={row} /></td>
    <td>{row.planned_start || row.planned_end ? <><div>{row.planned_start ?? "—"} ~ {row.planned_end ?? "—"}</div><small>{row.plan_months ? `${row.plan_months} 个月` : "—"}</small></> : <span className="danger-text">请填写</span>}</td>
    <td><BudgetCell row={row} /></td>
    <td>{row.monthly_budget_person_days ?? "—"}</td>
    <td><strong>{row.period_actual_person_days}</strong></td>
    <td title={`上期：${row.previous_period.start_month} 至 ${row.previous_period.end_month}，与本期月数相同`}><div>{row.previous_period_person_days}</div><small>{row.previous_period.start_month === row.previous_period.end_month ? row.previous_period.start_month : `${row.previous_period.start_month}~${row.previous_period.end_month}`}</small></td>
    <td>{row.participant_count}</td>
    <td><span className={`health-tag ${healthClass(row.overrun_warning)}`}>{row.overrun_warning}</span></td>
    <td>{row.monthly_trend}</td>
    <td><span className={`health-tag ${healthClass(row.health_status)}`}>{row.health_status}</span></td>
    <td title="按统计期实际投入速度推算预算耗尽月份，不代表项目计划完工日期"><div>{row.budget_exhaustion_forecast.label}</div><small>{row.budget_exhaustion_forecast.basis} · 剩余 {row.remaining_budget_person_days ?? "—"} 人天</small></td>
  </tr>;
}

// Where each maintaining action lives in the drawer. The requirements themselves are declared in the
// ontology metadata (Project.maintenanceRequirements), so this view only renders what the ontology derives.
const MAINTENANCE_PLACE: Record<string, string> = { configureProject: "项目基础配置", createMilestone: "里程碑计划", editMilestone: "里程碑计划", createPaymentTerm: "合同回款约定" };

function MaintenanceChecklist({ project }: { project: ProjectDetail }) {
  const checklist = project.maintenance_checklist ?? [];
  if (!checklist.length) return null;
  const pending = checklist.filter(item => !item.met).length;
  return <section className="detail-section maintenance-checklist">
    <div className="section-title"><div><h3>维护清单 <span className={`health-tag ${pending ? "warning" : "healthy"}`}>{pending ? `待维护 ${pending} 项` : "已全部维护"}</span></h3><small>导入后需要人工补充的内容，由本体按维护要求推导</small></div></div>
    <ul>{checklist.map(item => <li key={item.requirement} className={item.met ? "met" : "pending"}>
      <span aria-hidden="true">{item.met ? "✓" : "○"}</span>
      <strong>{item.label}</strong>
      <small>{item.met ? "已维护" : `待维护 · 在「${MAINTENANCE_PLACE[item.action] ?? item.action}」中处理`}</small>
    </li>)}</ul>
  </section>;
}

const MARKING_LABEL: Record<string, string> = { FINANCE: "金额", PERSONNEL: "个人工时" };

function RedactionNote({ markings }: { markings: string[] }) {
  return <span className="redaction-note" title="数据来自本体语义层，按安全标记脱敏">🔒 当前账号没有{markings.map(m => MARKING_LABEL[m] ?? m).join("、")}权限，相关数值已隐藏</span>;
}

function ResourceOperations({ data, loading, month, setMonth, periodMode, setPeriodMode, startMonth, setStartMonth, endMonth, setEndMonth, employeeInput, setEmployeeInput, submitEmployeeSearch, clearEmployeeSearch, openProject, loadMore }: {
  data: ResourceOperationsResponse | null;
  loading: boolean;
  month: string;
  setMonth: (value: string) => void;
  periodMode: "month" | "range";
  setPeriodMode: (value: "month" | "range") => void;
  startMonth: string;
  setStartMonth: (value: string) => void;
  endMonth: string;
  setEndMonth: (value: string) => void;
  employeeInput: string;
  setEmployeeInput: (value: string) => void;
  submitEmployeeSearch: (event: FormEvent) => void;
  clearEmployeeSearch: () => void;
  openProject: (projectNumber: string) => void;
  loadMore: () => Promise<void>;
}) {
  return <section className="panel resource-operations-panel">
    <div className="panel-head">
      <div><h2>资源运营监控</h2><p>从人员负荷与项目计划缺口两端识别调剂机会，辅助 PMO 安排投入优先级。</p></div>
    </div>
    <div className="filters health-filters resource-operation-filters">
      <div className="resource-filter-group">
        <span className="resource-filter-label">统计周期</span>
        <select value={periodMode} aria-label="选择统计周期" onChange={event => {
          const nextMode = event.target.value as "month" | "range";
          setPeriodMode(nextMode);
          if (nextMode === "range" && !startMonth && !endMonth) {
            setStartMonth(month);
            setEndMonth(month);
          }
        }}><option value="month">按单月</option><option value="range">按时间范围</option></select>
      </div>
      <div className="resource-filter-group resource-period-group">
        <span className="resource-filter-label">{periodMode === "month" ? "统计月份" : "统计范围"}</span>
        {periodMode === "month" ? <MonthPicker value={month} onChange={setMonth} label="选择资源运营监控月份" /> : <div className="resource-month-range"><MonthPicker value={startMonth} onChange={setStartMonth} label="选择资源投入开始月份" /><span>至</span><MonthPicker value={endMonth} onChange={setEndMonth} label="选择资源投入结束月份" /></div>}
      </div>
      <form className="resource-employee-search" onSubmit={submitEmployeeSearch}>
        <label htmlFor="resource-employee-search">人员检索</label>
        <div className="resource-search-input">
          <input id="resource-employee-search" value={employeeInput} onChange={event => setEmployeeInput(event.target.value)} placeholder="输入姓名进行筛选" />
          <button className="resource-search-submit" type="submit">查询</button>
        </div>
        {data?.meta.employee_name && <button className="resource-search-clear" type="button" onClick={clearEmployeeSearch}>清除</button>}
      </form>
      <span className="result-count">统计范围：{data?.meta.start_date ?? "—"} 至 {data?.meta.end_date ?? "—"} · {data?.meta.employee_name ? `人员：${data.meta.employee_name} · ` : ""}{data?.meta.working_days ?? "—"} 个工作日 · 基线 {hoursToDays(data?.meta.baseline_hours)} 人天{data?.meta.holiday_excluded_days ? `（已扣除公休 ${data.meta.holiday_excluded_days} 天）` : ""}{data?.meta.makeup_workdays ? `（含补班 ${data.meta.makeup_workdays} 天）` : ""}{data && !data.meta.calendar_available ? "（节假日订阅暂不可用，当前按周末排除）" : ""}</span>
    </div>
    <div className="resource-analysis-context">
      <div className="resource-method-note"><span>资源建议</span><p>{data?.meta.recommendation_limitations ?? "基于已导入的工时、工时基线与项目计划计算。"}</p></div>
    </div>
    <div className="table-wrap resource-operations-table"><table><thead><tr><th>人员</th><th>本期投入</th><th>加班</th><th>参与项目</th><th>负荷评估</th><th>可调剂人天</th><th>当前项目安排</th></tr></thead><tbody>
      {loading ? <LoadingRows colSpan={7} /> : data?.data.map(row => <tr key={`${row.employee_id}-${row.employee_name}`}>
        <td><strong className="project-code">{row.employee_name}</strong><small>{row.entry_count} 条项目工时{row.internal_nonproject_entry_count > 0 ? ` · ${row.internal_nonproject_entry_count} 条内部可调剂工时` : ""}</small></td>
        <td><strong>{row.reported_person_days} 人天</strong><small>利用率 {row.utilization_percent}%</small></td>
        <td className={Number(row.overtime_person_days) > 0 ? "overtime-text" : ""}>{row.overtime_person_days} 人天<small>加班占比 {row.reported_person_days === "0.00" ? "0" : `${(Number(row.overtime_person_days) / Number(row.reported_person_days) * 100).toFixed(1)}%`}</small></td>
        <td>{row.project_count} 个</td>
        <td><span className={`health-tag ${resourceAllocationClass(row.allocation_status)}`}>{row.allocation_status}</span></td>
        <td>{Number(row.available_person_days) > 0 ? <><strong className="available-days">{row.available_person_days} 人天</strong>{Number(row.internal_nonproject_person_days) > 0 && <small>含 TSG999 {row.internal_nonproject_person_days} 人天</small>}</> : "—"}</td>
        <td>{row.assignments.length > 0 ? <div className="resource-project-list">{row.assignments.map(project => project.is_internal_nonproject ? <span className="internal-allocation" key={project.project_number}><strong>{project.project_number}</strong><small>{project.reported_person_days} 人天 · 可调剂</small></span> : <button key={project.project_number} type="button" onClick={() => void openProject(project.project_number)}>{project.project_number}<small>{project.reported_person_days} 人天</small></button>)}</div> : <span className="resource-muted">无项目</span>}</td>
      </tr>)}
      {!loading && data?.data.length === 0 && <tr><td className="empty" colSpan={7}>该筛选条件下暂无已导入员工工时，无法生成资源安排分析。</td></tr>}
    </tbody></table></div>
    {data?.meta.next_cursor && <div className="pagination cursor-pagination"><button type="button" onClick={() => void loadMore()}>加载更多人员</button></div>}
  </section>;
}

function resourceAllocationClass(status: string) {
  if (status === "本月负荷过高") return "critical";
  if (status === "负荷较高") return "warning";
  if (status === "可优先调剂" || status === "可适度支持") return "healthy";
  return "baseline";
}

function EmployeeWorktime(props: {
  assessment: EmployeeWorktimeResponse | null;
  loading: boolean;
  queryInput: string;
  setQueryInput: (value: string) => void;
  submitSearch: (event: FormEvent) => void;
  period: "month" | "year";
  setPeriod: (value: "month" | "year") => void;
  month: string;
  setMonth: (value: string) => void;
  year: string;
  setYear: (value: string) => void;
  refresh: () => Promise<void>;
}) {
  const [importing, setImporting] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [importNotice, setImportNotice] = useState<{ kind: "success" | "error"; text: string } | null>(null);

  async function uploadTimesheet(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (!file) return;
    setImporting(true); setMessage(null); setImportNotice(null);
    try {
      const result = await importTimeEntriesXlsx(file);
      await props.refresh();
      const latestMonth = result.date_range?.end.slice(0, 7);
      if (latestMonth) props.setMonth(latestMonth);
      const dateText = result.date_range ? `，数据范围 ${result.date_range.start} 至 ${result.date_range.end}` : "";
      const unchangedText = result.unchanged > 0 ? `；${result.unchanged} 条已存在记录保持不变` : "";
      setImportNotice({ kind: "success", text: `导入完成：新增 ${result.imported} 条员工项目工时记录${unchangedText}${dateText}。${latestMonth ? `已切换到 ${latestMonth} 查看。` : ""}` });
    } catch (err) {
      setImportNotice({ kind: "error", text: `导入失败：${err instanceof Error ? err.message : "请检查 Excel 格式后重试"}` });
    } finally {
      setImporting(false);
      event.target.value = "";
    }
  }

  return <section className="panel employee-worktime-panel">
    <div className="panel-head employee-worktime-head"><div><h2>员工工时数据</h2><p>导入员工每日项目工时，按项目代码汇总并关联项目运营监控</p></div><div className="panel-actions employee-worktime-actions"><label className={`button primary import-timesheet-button ${importing ? "disabled" : ""}`}>{importing ? "导入中…" : "导入工时 Excel"}<input type="file" accept=".xlsx" onChange={uploadTimesheet} disabled={importing} hidden /></label><Search value={props.queryInput} setValue={props.setQueryInput} submit={props.submitSearch} placeholder="搜索员工姓名" /></div></div>
    {importNotice && <div className={`import-notice ${importNotice.kind}`} role="status"><span aria-hidden="true">{importNotice.kind === "success" ? "✓" : "!"}</span><div><strong>{importNotice.kind === "success" ? "工时导入完成" : "工时导入未完成"}</strong><p>{importNotice.text}</p></div><button type="button" aria-label="关闭导入提示" onClick={() => setImportNotice(null)}>×</button></div>}
    <div className="filters health-filters employee-worktime-filters">
      <select value={props.period} onChange={e => props.setPeriod(e.target.value as "month" | "year")}><option value="month">按月统计</option><option value="year">年度汇总</option></select>
      {props.period === "month" ? <label>统计月份<MonthPicker value={props.month} onChange={props.setMonth} label="选择员工工时统计月份" /></label> : <label>统计年度<input type="number" min="2000" max="2100" value={props.year} onChange={e => props.setYear(e.target.value)} /></label>}
      <span className="result-count">统计范围：{props.assessment?.meta.start_date ?? "—"} 至 {props.assessment?.meta.end_date ?? "—"} · {props.assessment?.meta.working_days ?? "—"} 个工作日 · 基线 {hoursToDays(props.assessment?.meta.baseline_hours)} 人天{props.assessment?.meta.holiday_excluded_days ? `（已扣除公休 ${props.assessment.meta.holiday_excluded_days} 天）` : ""}{props.assessment?.meta.makeup_workdays ? `（含补班 ${props.assessment.meta.makeup_workdays} 天）` : ""}{props.assessment && !props.assessment.meta.calendar_available ? "（节假日订阅暂不可用，当前按周末排除）" : ""}</span>
    </div>
    <div className="employee-worktime-settings-note">工时基线、利用率标准和资源调剂复核规则已统一迁移至 <b>系统设置</b> 维护。</div>
    <div className="table-wrap"><table><thead><tr><th>姓名</th><th>填报人天</th><th>加班人天</th><th>确认人天</th><th>确认缺口(人天)</th><th>基线(人天)</th><th>工时利用率</th><th>项目数</th><th>记录数</th><th>工时利用率评价</th></tr></thead><tbody>
      {props.loading ? <LoadingRows colSpan={10} /> : props.assessment?.data.map(row => <EmployeeWorktimeRowView key={row.employee_id || row.employee_name} row={row} />)}
      {!props.loading && props.assessment?.data.length === 0 && <tr><td className="empty" colSpan={10}>暂无员工工时数据</td></tr>}
    </tbody></table></div>
  </section>;
}

function EmployeeWorktimeRowView({ row }: { row: EmployeeWorktimeRow }) {
  return <tr>
    <td><strong className="project-code">{row.employee_name}</strong></td>
    <td>{row.reported_person_days}</td>
    <td>{row.overtime_person_days}</td>
    <td>{row.approved_person_days}</td>
    <td className={Number(row.confirmation_gap_hours) > 0 ? "danger-text" : ""}>{hoursToDays(row.confirmation_gap_hours)}</td>
    <td>{hoursToDays(row.baseline_hours)}</td>
    <td><strong>{row.utilization_percent}%</strong></td>
    <td>{row.project_count}</td>
    <td>{row.entry_count}</td>
    <td><span className={`health-tag ${employeeStatusClass(row.status)}`}>{row.status}</span></td>
  </tr>;
}

function PmoResourceTable({ project }: { project: ProjectDetail }) {
  const summary = project.pmo_resource_summary;
  const rows = project.pmo_resource_rows;
  return <section className="detail-section pmo-resource-section">
    <div className="section-title">
      <div>
        <h3>PMO 人员资源与人力成本 <span className="source-tag">系统接口 · 只读</span></h3>
        <small>成本以已导入工时折算为人天；正常投入与加班投入单独展示，未配置员工成本单价，不计算人民币金额。</small>
      </div>
      <span className="pmo-cost-basis">成本口径：{summary.cost_basis}</span>
    </div>
    <div className="pmo-resource-metrics">
      <article><span>参与人员</span><b>{summary.participant_count} 人</b></article>
      <article><span>常规投入</span><b>{summary.normal_person_days} 人天</b></article>
      <article className={Number(summary.overtime_person_days) > 0 ? "overtime" : ""}><span>加班投入</span><b>{summary.overtime_person_days} 人天</b></article>
      <article><span>人力成本（人天）</span><b>{summary.reported_person_days} 人天</b></article>
      <article className={Number(summary.confirmation_gap_person_days) > 0 ? "gap" : ""}><span>待客户确认</span><b>{summary.confirmation_gap_person_days} 人天</b></article>
    </div>
    {rows.length > 0 ? <div className="table-wrap pmo-resource-table"><table><thead><tr><th>人员</th><th>参与月份</th><th>记录 / 参与天数</th><th>常规投入</th><th>加班投入</th><th>人力成本（人天）</th><th>客户确认</th><th>确认缺口</th><th>项目投入占比</th><th>加班占比</th><th>月均投入</th></tr></thead><tbody>
      {rows.map(row => <tr key={`${row.employee_id ?? "name"}-${row.employee_name}`}>
        <td><strong className="project-code">{row.employee_name}</strong></td>
        <td>{row.active_month_count} 个月</td>
        <td>{row.entry_count} 条 / {row.active_day_count} 天</td>
        <td>{row.normal_person_days} 人天</td>
        <td className={Number(row.overtime_person_days) > 0 ? "overtime-text" : ""}>{row.overtime_person_days} 人天</td>
        <td><strong>{row.reported_person_days} 人天</strong></td>
        <td>{row.approved_person_days} 人天</td>
        <td className={Number(row.confirmation_gap_person_days) > 0 ? "danger-text" : ""}>{row.confirmation_gap_person_days} 人天</td>
        <td>{row.project_effort_share_percent}%</td>
        <td className={row.overtime_ratio_percent > 0 ? "overtime-text" : ""}>{row.overtime_ratio_percent}%</td>
        <td>{row.monthly_average_person_days} 人天</td>
      </tr>)}
    </tbody></table></div> : <p className="detail-empty">暂无员工项目工时，导入工时后将按项目代码自动汇总人员资源。</p>}
  </section>;
}

// Labels of the ontology value types InterventionType and InterventionStatus (ontology.yaml, D34).
const INTERVENTION_TYPE_LABELS: Record<string, string> = { schedule: "进度", resource: "资源", payment: "回款", customer: "客户", quality: "质量", management: "其他" };
const INTERVENTION_STATUS_LABELS: Record<string, string> = { open: "待处理", in_progress: "处理中", closed: "已关闭" };

function ProjectDrawer({ project, close, refresh }: { project: ProjectDetail; close: () => void; refresh: () => Promise<void> }) {
  const [saving, setSaving] = useState(false);
  // Project-manager accounts' names (D10); an empty list just means no suggestions.
  const [managerNames, setManagerNames] = useState<string[]>([]);
  useEffect(() => { fetchProjectManagerNames().then(setManagerNames).catch(() => setManagerNames([])); }, []);
  const [message, setMessage] = useState<string | null>(null);
  const [confirmedBy, setConfirmedBy] = useState(project.project_manager ?? "");
  const [settings, setSettings] = useState({
    contract_type: project.contract_type,
    lifecycle_status: project.lifecycle_status,
    planned_person_days: project.planned_person_days ?? "",
    hours_per_person_day: project.settings.hours_per_person_day ?? "8.00",
    planned_start: project.planned_start ?? "",
    planned_end: project.planned_end ?? "",
    project_manager: project.project_manager ?? ""
  });
  const [milestone, setMilestone] = useState({ name: "", planned_start: "", planned_end: "", owner: project.project_manager ?? "", deliverable: "", is_payment_node: false, payment_date: "", payment_amount: "", payment_term_id: "" });
  const [editingMilestoneId, setEditingMilestoneId] = useState<string | null>(null);
  const editingTermId = project.milestones.find(item => item.milestone_id === editingMilestoneId)?.payment_term_id ?? null;
  const [milestoneFormOpen, setMilestoneFormOpen] = useState(false);
  const [paymentTerm, setPaymentTerm] = useState({ name: "", payment_ratio: "", planned_amount: "", notes: "" });
  const [editingPaymentTermId, setEditingPaymentTermId] = useState<string | null>(null);
  const [paymentTermFormOpen, setPaymentTermFormOpen] = useState(false);
  const emptyIntervention = { title: "", intervention_type: "management", owner: "", due_date: "", notes: "" };
  const [intervention, setIntervention] = useState(emptyIntervention);
  const [interventionFormOpen, setInterventionFormOpen] = useState(false);
  const [expandedMonth, setExpandedMonth] = useState(project.monthly_time_entries[0]?.month ?? "");
  const [configEditing, setConfigEditing] = useState(false);
  const [aiAnalysis, setAiAnalysis] = useState<{ answer: string; sources: string[] } | null>(null);
  const [analyzing, setAnalyzing] = useState(false);
  const [analysisError, setAnalysisError] = useState<string | null>(null);

  useEffect(() => {
    setSettings({ contract_type: project.contract_type, lifecycle_status: project.lifecycle_status, planned_person_days: project.planned_person_days ?? "", hours_per_person_day: project.settings.hours_per_person_day ?? "8.00", planned_start: project.planned_start ?? "", planned_end: project.planned_end ?? "", project_manager: project.project_manager ?? "" });
    setExpandedMonth(project.monthly_time_entries[0]?.month ?? "");
    setConfigEditing(false);
    setMilestoneFormOpen(false);
    setEditingMilestoneId(null);
    setPaymentTermFormOpen(false);
    setEditingPaymentTermId(null);
    setInterventionFormOpen(false);
    setAiAnalysis(null);
    setAnalysisError(null);
  }, [project]);

  async function run(action: () => Promise<void>, success: string) {
    setSaving(true); setMessage(null);
    try { await action(); await refresh(); setMessage(success); }
    catch (err) { setMessage(err instanceof Error ? err.message : "操作失败"); }
    finally { setSaving(false); }
  }

  function saveSettings(event: FormEvent) {
    event.preventDefault();
    void run(async () => {
      await updateProjectSettings(project.project_number, {
        ...settings,
        planned_person_days: settings.contract_type === "open_ended" || settings.planned_person_days === "" ? null : settings.planned_person_days
      });
      setConfigEditing(false);
    }, "项目基础信息已保存");
  }

  function addMilestone(event: FormEvent) {
    event.preventDefault();
    void run(async () => {
      const payload = milestonePayload(milestone, editingTermId);
      if (editingMilestoneId) await updateMilestone(project.project_number, editingMilestoneId, payload);
      else await createMilestone(project.project_number, payload);
      resetMilestoneForm();
    }, editingMilestoneId ? "里程碑修改已保存" : "里程碑已加入待办");
  }

  function resetMilestoneForm() {
    setEditingMilestoneId(null);
    setMilestone({ name: "", planned_start: "", planned_end: "", owner: settings.project_manager, deliverable: "", is_payment_node: false, payment_date: "", payment_amount: "", payment_term_id: "" });
    setMilestoneFormOpen(false);
  }

  function openNewMilestoneForm() {
    setEditingMilestoneId(null);
    setMilestone({ name: "", planned_start: "", planned_end: "", owner: settings.project_manager, deliverable: "", is_payment_node: false, payment_date: "", payment_amount: "", payment_term_id: "" });
    setMilestoneFormOpen(true);
  }

  function editMilestone(item: ProjectDetail["milestones"][number]) {
    if (item.status === "completed") return;
    setMilestoneFormOpen(true);
    setEditingMilestoneId(item.milestone_id);
    setMilestone({
      name: item.name,
      planned_start: item.planned_start,
      planned_end: item.planned_end,
      owner: item.owner ?? "",
      deliverable: item.deliverable ?? "",
      is_payment_node: Boolean(item.is_payment_node),
      payment_date: item.payment_date ?? "",
      payment_amount: item.payment_amount ?? "",
      payment_term_id: item.payment_term_id ?? ""
    });
    setMessage("正在编辑待确认里程碑");
  }

  function removeMilestone(item: ProjectDetail["milestones"][number]) {
    if (item.status === "completed") return;
    const confirmed = window.confirm(`确认删除里程碑“${item.name}”？${item.is_payment_node ? "关联的未到账回款节点也会同步删除。" : ""}`);
    if (!confirmed) return;
    void run(async () => {
      await deleteMilestone(project.project_number, item.milestone_id);
      if (editingMilestoneId === item.milestone_id) resetMilestoneForm();
    }, "待确认里程碑已删除");
  }

  function editPaymentNode(item: ProjectDetail["payment_nodes"][number]) {
    if (item.status === "paid") return;
    const name = window.prompt("回款节点名称", item.name);
    if (name === null) return;
    const plannedDate = window.prompt("计划回款日期（YYYY-MM-DD）", item.planned_date);
    if (plannedDate === null) return;
    const plannedAmount = window.prompt("计划回款金额（元）", item.planned_amount);
    if (plannedAmount === null) return;
    void run(async () => {
      await updatePaymentNode(project.project_number, item.payment_node_id, {
        name: name.trim() || item.name,
        planned_date: plannedDate.trim() || item.planned_date,
        planned_amount: plannedAmount.trim() || item.planned_amount,
        notes: item.notes ?? undefined
      });
    }, "回款节点修改已保存");
  }

  function removePaymentNode(item: ProjectDetail["payment_nodes"][number]) {
    if (item.status === "paid") return;
    const confirmed = window.confirm(`确认删除回款节点“${item.name}”？`);
    if (!confirmed) return;
    void run(async () => {
      await deletePaymentNode(project.project_number, item.payment_node_id);
    }, "未确认回款节点已删除");
  }

  function resetPaymentTermForm() {
    setPaymentTerm({ name: "", payment_ratio: "", planned_amount: "", notes: "" });
    setEditingPaymentTermId(null);
    setPaymentTermFormOpen(false);
  }

  function editPaymentTerm(item: ProjectDetail["payment_terms"][number]) {
    setPaymentTerm({ name: item.name, payment_ratio: item.payment_ratio ?? "", planned_amount: item.planned_amount, notes: item.notes ?? "" });
    setEditingPaymentTermId(item.payment_term_id);
    setPaymentTermFormOpen(true);
  }

  function savePaymentTerm(event: FormEvent) {
    event.preventDefault();
    void run(async () => {
      if (editingPaymentTermId) await updatePaymentTerm(project.project_number, editingPaymentTermId, paymentTerm);
      else await createPaymentTerm(project.project_number, paymentTerm);
      resetPaymentTermForm();
    }, editingPaymentTermId ? "回款约定已更新" : "合同回款约定已添加");
  }

  function saveIntervention(event: FormEvent) {
    event.preventDefault();
    if (!intervention.due_date) { setMessage("请选择截止日期"); return; }
    void run(async () => {
      await openIntervention(project.project_number, intervention);
      setIntervention(emptyIntervention);
      setInterventionFormOpen(false);
    }, "管理干预已发起");
  }

  function moveIntervention(item: ProjectDetail["interventions"][number], status: "in_progress" | "closed") {
    let notes: string | undefined;
    if (status === "closed") {
      const answer = window.prompt(`关闭「${item.title}」：填写处理结论（可留空）。关闭后不能重新打开。`, item.notes ?? "");
      if (answer === null) return;
      notes = answer.trim();
    }
    void run(async () => { await updateInterventionStatus(item.intervention_id, status, notes); },
      status === "closed" ? "管理干预已关闭" : "管理干预已转为处理中");
  }

  function removePaymentTerm(item: ProjectDetail["payment_terms"][number]) {
    if (item.linked_milestone_id) { setMessage("已关联里程碑的回款约定，请在对应里程碑中解除关联后再删除"); return; }
    if (!window.confirm(`确认删除回款约定“${item.name}”？`)) return;
    void run(async () => { await deletePaymentTerm(project.project_number, item.payment_term_id); }, "回款约定已删除");
  }

  function actor(): string | null {
    const value = confirmedBy.trim();
    if (!value) { setMessage("请先填写当前确认人"); return null; }
    return value;
  }

  async function requestAiAnalysis() {
    if (analyzing) return;
    setAnalyzing(true);
    setAnalysisError(null);
    try {
      const result = await analyzeProjectWithPmAssistant(project.project_number);
      setAiAnalysis({ answer: result.answer, sources: result.sources });
    } catch (err) {
      setAnalysisError(err instanceof Error ? err.message : "暂时无法生成 AI 项目研判，请稍后重试。");
    } finally {
      setAnalyzing(false);
    }
  }

  const actionAlerts = (project.anomaly_alerts?.length ?? 0) > 0
    ? project.anomaly_alerts
    : [
      ...(!project.maintenance_complete ? [{
        code: "maintenance-required",
        severity: "warning" as const,
        title: "同步后待人工维护",
        detail: (project.maintenance_reasons ?? []).join("、")
      }] : []),
      ...project.risk_reasons.map((reason, index) => ({
        code: `risk-${index}`,
        severity: "warning" as const,
        title: "项目风险提示",
        detail: reason
      }))
    ];
  const pendingTodoEvents = project.todo_events.filter(item => !item.completed);
  const completedTodoEvents = project.todo_events
    .filter(item => item.completed)
    .sort((left, right) => (right.completed_at ?? right.due_date).localeCompare(left.completed_at ?? left.due_date));

  return <div className="drawer-backdrop" onClick={close}><aside className="project-drawer" onClick={e => e.stopPropagation()}>
    <button className="drawer-close" onClick={close}>×</button>
    <p className="eyebrow">{project.project_number}</p><h2>{displayProjectName(project.project_number, project.subject)}</h2>
    <div className="drawer-badges"><span>{contractTypeLabel(project.contract_type)}</span><span>{lifecycleLabel(project.lifecycle_status)}</span><span className={`risk ${project.risk_level}`}>{riskLabel(project.risk_level)}</span>{project.redacted_markings?.length ? <RedactionNote markings={project.redacted_markings} /> : null}</div>
    {actionAlerts.length > 0 && <section className="alert-stack" aria-label="项目待处理事项"><div className="alert-stack-head"><div className="alert-stack-icon" aria-hidden="true">!</div><div><h3>项目待处理事项</h3><small>按优先级处理风险和人工维护事项</small></div><span className="alert-stack-count">{actionAlerts.length} 项</span><button type="button" className="ai-analysis-button" disabled={analyzing} onClick={() => void requestAiAnalysis()}>{analyzing ? "正在分析…" : aiAnalysis ? "重新分析" : "AI 分析"}</button></div><div className="alert-stack-list">{actionAlerts.map((alert, index) => <article className={`anomaly-alert ${alert.severity}`} key={`${alert.code}-${index}`}><span className="anomaly-alert-icon" aria-hidden="true">{alert.severity === "critical" ? "!" : "◷"}</span><div className="anomaly-alert-content"><div><strong>{alert.title}</strong><span>{alert.severity === "critical" ? "严重" : "关注"}</span></div><p>{alert.detail}</p></div></article>)}</div>{aiAnalysis && <article className="ai-analysis-result"><header><div><span>AI 项目研判</span><small>基于当前 CRM、工时、里程碑与回款资料</small></div><b>已生成</b></header><Markdown text={aiAnalysis.answer} /><footer>{aiAnalysis.sources.map(source => <small key={source}>{source}</small>)}</footer></article>}{analysisError && <p className="ai-analysis-error">{analysisError}</p>}</section>}
    {message && <div className="inline-message">{message}</div>}
    <section className="detail-section source-section"><div className="section-title"><div><h3>项目主数据库汇总 <span className="source-tag">系统接口 · 只读</span></h3><small>{project.revenue_settlement_basis === "workload" ? "开口合同按工作量确认收入：合同金额仅作框架额参考，不用于计算回款完成率。" : "合同金额取 CRM 导入模板；工时与人工维护字段分别独立汇总"}</small></div></div><div className="detail-metrics six"><article><span>{project.revenue_settlement_basis === "workload" ? "合同框架额（CRM）" : "合同金额（CRM）"}</span><b>{money(project.contract_value)}</b></article><article><span>{project.revenue_settlement_basis === "workload" ? "确认收入（开票）" : "已开票（CRM）"}</span><b>{money(project.revenue_settlement_basis === "workload" ? project.recognized_revenue_amount : project.invoiced_amount)}</b></article><article><span>{project.revenue_settlement_basis === "workload" ? "实际已回款（CRM）" : "已回款（CRM）"}</span><b>{money(project.crm_received_amount)}</b></article><article><span>预算人天</span><b>{project.planned_person_days ? `${project.planned_person_days} 人天` : "未设置"}</b></article><article><span>累计实际人天</span><b>{project.actual_person_days} 人天</b></article><article className={Number(project.confirmation_gap_person_days) > 0 ? "metric-alert" : ""}><span>待客户确认缺口</span><b>{project.confirmation_gap_person_days} 人天</b></article></div></section>

    <section className="detail-section"><div className="section-title"><div><h3>月度工时清单 <span className="source-tag">系统接口 · 只读</span></h3><small>点击月份展开员工填报明细</small></div></div>
      <div className="month-list">{project.monthly_time_entries.map(month => <article className="month-card" key={month.month}><button type="button" onClick={() => setExpandedMonth(expandedMonth === month.month ? "" : month.month)}><strong>{month.month}</strong><span>{month.entry_count} 条 · 填报 {month.reported_person_days} 人天{Number(month.overtime_person_days) > 0 && `（含加班 ${month.overtime_person_days} 人天）`} · 确认 {month.approved_person_days} 人天{month.excluded_entry_count > 0 && ` · 不计入 ${month.excluded_entry_count} 条（${month.excluded_person_days} 人天）`}</span><b>{expandedMonth === month.month ? "收起" : "展开"}</b></button>{expandedMonth === month.month && <div className="month-entries">{month.entries.map(item => <div className="detail-row compact" key={item.external_id}><div><strong>{NOT_EFFORT_STATUS[item.approval_status] && <span className="mini-state excluded">{NOT_EFFORT_STATUS[item.approval_status]} · 不计入</span>}{item.employee_name} · 填报 {timeEntryPersonDays(item)} 人天{Number(item.overtime_hours) > 0 && `（加班 ${hoursToDays(item.overtime_hours)} 人天）`} / 确认 {hoursToDays(item.approved_hours)} 人天</strong><small>{item.work_content}</small></div><time>{item.work_date}</time></div>)}</div>}</article>)}</div>
      {project.monthly_time_entries.length === 0 && <p className="detail-empty">暂无月度工时数据</p>}
    </section>

    <PmoResourceTable project={project} />

    <section className="detail-section todo-section"><div className="todo-section-head"><div><p className="todo-section-kicker">TO-DO</p><h3>待办处理区</h3><small>按当前日期自动计算；请在完成后确认，逾期事项会持续提示</small></div><label>当前确认人<input value={confirmedBy} onChange={e => setConfirmedBy(e.target.value)} placeholder="管理人员姓名" /></label></div>
      <div className="todo-grid">{pendingTodoEvents.map(item => {
        const linkedMilestone = item.event_type === "milestone" ? project.milestones.find(milestoneItem => milestoneItem.milestone_id === item.event_id) : null;
        const linkedPayment = item.event_type === "payment" ? project.payment_nodes.find(paymentItem => paymentItem.payment_node_id === item.event_id) : null;
        const paymentMilestone = linkedPayment ? project.milestones.find(milestoneItem => milestoneItem.payment_node_id === linkedPayment.payment_node_id) : null;
        return <article className={`todo-card ${item.urgency}`} key={`${item.event_type}-${item.event_id}`}>
          <div><span>{item.event_type === "payment" ? "回款" : "里程碑"}</span><b>{item.label}</b></div>
          <h4>{item.name}</h4>
          <p>{item.due_date}{item.amount && ` · ${money(item.amount)}`}{item.deliverable && ` · 输出物：${item.deliverable}`}</p>
          {item.event_type === "milestone" ? <div className="todo-actions">
            {linkedMilestone && <button type="button" className="icon-action secondary-action" aria-label="编辑里程碑" title="编辑" disabled={saving} onClick={() => editMilestone(linkedMilestone)}>✎</button>}
            {linkedMilestone && <button type="button" className="icon-action danger-action" aria-label="删除里程碑" title="删除" disabled={saving} onClick={() => removeMilestone(linkedMilestone)}>×</button>}
            <button className="icon-action confirm-action" aria-label="确认完成" title="确认完成" disabled={saving} onClick={() => { const name = actor(); if (name) void run(() => confirmMilestone(project.project_number, item.event_id, name), "里程碑已确认完成"); }}>✓</button>
          </div> : <div className="todo-actions">
            {paymentMilestone && paymentMilestone.status !== "completed" && <button type="button" className="icon-action secondary-action" aria-label="编辑回款节点" title="编辑" disabled={saving} onClick={() => editMilestone(paymentMilestone)}>✎</button>}
            {linkedPayment && !paymentMilestone && <button type="button" className="icon-action secondary-action" aria-label="编辑回款节点" title="编辑" disabled={saving} onClick={() => editPaymentNode(linkedPayment)}>✎</button>}
            {paymentMilestone && paymentMilestone.status !== "completed" && <button type="button" className="icon-action danger-action" aria-label="删除回款节点" title="删除" disabled={saving} onClick={() => removeMilestone(paymentMilestone)}>×</button>}
            {linkedPayment && !paymentMilestone && <button type="button" className="icon-action danger-action" aria-label="删除回款节点" title="删除" disabled={saving} onClick={() => removePaymentNode(linkedPayment)}>×</button>}
            <button className="icon-action confirm-action" aria-label="确认到账" title="确认到账" disabled={saving} onClick={() => { const name = actor(); if (name) void run(() => confirmPayment(project.project_number, item.event_id, name), "回款已确认到账"); }}>✓</button>
          </div>}
        </article>;
      })}</div>
      {pendingTodoEvents.length === 0 && <p className="detail-empty">当前没有待办事件</p>}
      <div className="completed-todo-section"><div className="completed-todo-head"><div><h4>已完成事项</h4><small>保留完成内容、计划日期及实际完成状态，便于复核。</small></div><span>{completedTodoEvents.length} 项</span></div>
        {completedTodoEvents.length > 0 ? <div className="completed-todo-list">{completedTodoEvents.map(item => <article className={`completed-todo-row ${item.completion_state === "delayed" ? "delayed-completed" : ""}`} key={`${item.event_type}-${item.event_id}`}><span className="completed-todo-type">{item.event_type === "payment" ? "回款" : "里程碑"}</span><strong>{item.name}</strong><span className="completed-todo-status">{item.completion_label ?? "已完成"}</span><small>计划：{item.due_date}{item.amount && ` · ${money(item.amount)}`}{item.deliverable && ` · 输出物：${item.deliverable}`}</small><small>实际{item.event_type === "payment" ? "到账" : "完成"}：{item.completed_at ?? "未记录"}{item.confirmed_by && ` · 确认人：${item.confirmed_by}`}</small></article>)}</div> : <p className="detail-empty">暂无已完成事项</p>}
      </div>
    </section>

    <MaintenanceChecklist project={project} />

    <section className="detail-section"><div className="section-title"><div><h3>项目基础配置 <span className="manual-tag">人工维护</span></h3><small>同步不会覆盖以下字段</small></div><button type="button" className="button secondary" disabled={saving} onClick={() => setConfigEditing(!configEditing)}>{configEditing ? "取消编辑" : "编辑"}</button></div>
      {!configEditing ? <div className="config-summary">
        <article><span>项目类型</span><strong>{contractTypeLabel(project.contract_type)}</strong></article>
        <article><span>生命周期</span><strong>{lifecycleLabel(project.lifecycle_status)}</strong></article>
        <article><span>初始预算人天</span><strong>{project.planned_person_days ? `${project.planned_person_days} 人天` : project.contract_type === "open_ended" ? "无上限" : "未设置"}</strong></article>
        <article><span>每人天小时</span><strong>{project.settings.hours_per_person_day ?? "8.00"} h</strong></article>
        <article><span>计划开始</span><strong>{project.planned_start ?? "未设置"}</strong></article>
        <article><span>计划结束</span><strong>{project.planned_end ?? "未设置"}</strong></article>
        <article><span>项目经理</span><strong>{project.project_manager || "未设置"}</strong></article>
        <article><span>销售人员（CRM）</span><strong>{project.sales_owner || "未匹配"}</strong></article>
      </div> : <form className="config-form" onSubmit={saveSettings}>
        <label>项目类型<select value={settings.contract_type} onChange={e => setSettings({ ...settings, contract_type: e.target.value as ProjectDetail["contract_type"] })}><option value="unknown">请选择</option><option value="construction">系统实施</option><option value="maintenance">TSnT365</option><option value="open_ended">开口合同</option><option value="sales">售前阶段</option></select></label>
        <label>生命周期<select value={settings.lifecycle_status} onChange={e => setSettings({ ...settings, lifecycle_status: e.target.value as ProjectDetail["lifecycle_status"] })}><option value="planned">计划中</option><option value="active">执行中</option><option value="on_hold">已暂停</option><option value="completed">结束</option><option value="closed">已关闭</option></select></label>
        <label>初始预算人天<input type="number" min="0" step="0.01" disabled={settings.contract_type === "open_ended"} required={["construction", "maintenance"].includes(settings.contract_type)} value={settings.contract_type === "open_ended" ? "" : settings.planned_person_days} placeholder={settings.contract_type === "open_ended" ? "无上限，无需填写" : "例如 120"} onChange={e => setSettings({ ...settings, planned_person_days: e.target.value })} /></label>
        <label>每人天小时<input type="number" min="0.1" step="0.1" required value={settings.hours_per_person_day} onChange={e => setSettings({ ...settings, hours_per_person_day: e.target.value })} /></label>
        <label>计划开始<CalendarDatePicker value={settings.planned_start} onChange={planned_start => setSettings({ ...settings, planned_start })} label="选择项目计划开始日期" clearable /></label>
        <label>计划结束<CalendarDatePicker value={settings.planned_end} onChange={planned_end => setSettings({ ...settings, planned_end })} label="选择项目计划结束日期" minDate={settings.planned_start} clearable /></label>
        <label className="span-2">项目经理<input required list="project-manager-names" value={settings.project_manager} onChange={e => setSettings({ ...settings, project_manager: e.target.value })} />
          <datalist id="project-manager-names">{managerNames.map(name => <option key={name} value={name} />)}</datalist>
          {projectManagerHint(settings.project_manager, managerNames) && <small className="field-hint warn-text">{projectManagerHint(settings.project_manager, managerNames)}</small>}</label>
        <div className="form-actions span-2"><button className="button primary" disabled={saving}>保存基础配置</button><button type="button" className="button secondary" disabled={saving} onClick={() => { setSettings({ contract_type: project.contract_type, lifecycle_status: project.lifecycle_status, planned_person_days: project.planned_person_days ?? "", hours_per_person_day: project.settings.hours_per_person_day ?? "8.00", planned_start: project.planned_start ?? "", planned_end: project.planned_end ?? "", project_manager: project.project_manager ?? "" }); setConfigEditing(false); }}>取消</button></div>
      </form>}
    </section>

    <section className="detail-section"><div className="section-title"><div><h3>合同回款约定 <span className="manual-tag">人工维护</span></h3><small>初始化时维护比例、节点内容和金额；不要求填写绝对回款日期。</small></div>{!paymentTermFormOpen && <button type="button" className="button secondary add-action" disabled={saving} onClick={() => setPaymentTermFormOpen(true)}>＋ 添加约定</button>}</div>
      <div className="payment-term-list">{project.payment_terms.map(item => <article className={`payment-term-item ${item.linked_milestone_id ? "linked" : ""}`} key={item.payment_term_id}><div className="payment-term-copy"><strong>{item.name}</strong><small>{item.payment_ratio ? `${item.payment_ratio}% · ` : ""}{money(item.planned_amount)}{item.notes ? ` · ${item.notes}` : ""}</small></div><div className="payment-term-actions"><span className={item.linked_milestone_id ? "mini-state linked" : "mini-state"}>{item.linked_milestone_id ? "已关联里程碑" : "待关联"}</span><div className="row-action-buttons"><button type="button" className="row-action" disabled={saving} onClick={() => editPaymentTerm(item)}>编辑</button><button type="button" className="row-action danger-action" disabled={saving || Boolean(item.linked_milestone_id)} onClick={() => removePaymentTerm(item)}>删除</button></div></div></article>)}</div>
      {project.payment_terms.length === 0 && !paymentTermFormOpen && <p className="detail-empty">暂无合同回款约定。可先录入比例、节点内容和金额，再在里程碑中关联。</p>}
      {paymentTermFormOpen && <form className="milestone-form" onSubmit={savePaymentTerm}><label>回款节点内容<input required value={paymentTerm.name} placeholder="如：合同生效首期款、蓝图确认款" onChange={e => setPaymentTerm({ ...paymentTerm, name: e.target.value })} /></label><label>回款比例（%）<input type="number" min="0" max="100" step="0.01" value={paymentTerm.payment_ratio} placeholder="如：30" onChange={e => setPaymentTerm({ ...paymentTerm, payment_ratio: e.target.value })} /></label><label>约定金额（元）<input type="number" required min="0" step="0.01" value={paymentTerm.planned_amount} placeholder="如：206700" onChange={e => setPaymentTerm({ ...paymentTerm, planned_amount: e.target.value })} /></label><label>付款条件 / 备注<input value={paymentTerm.notes} placeholder="如：收到等额发票后 30 日内支付" onChange={e => setPaymentTerm({ ...paymentTerm, notes: e.target.value })} /></label><div className="form-actions span-2"><button className="button primary" disabled={saving}>{editingPaymentTermId ? "保存约定" : "添加回款约定"}</button><button type="button" className="button secondary" disabled={saving} onClick={resetPaymentTermForm}>取消</button></div></form>}
    </section>

    <section className="detail-section"><div className="section-title"><div><h3>里程碑计划 <span className="manual-tag">人工维护</span></h3><small>关联回款约定后，系统将使用里程碑结束日期生成并跟踪回款事件。</small></div>{!milestoneFormOpen && <button type="button" className="button secondary add-action" disabled={saving} onClick={openNewMilestoneForm}>＋ 添加</button>}</div>
      <div className="milestone-list">{project.milestones.map(item => <article key={item.milestone_id} className={`milestone-item ${item.deadline.urgency} ${editingMilestoneId === item.milestone_id ? "editing" : ""}`}><span className="sequence">{item.sequence_no ?? "—"}</span><div><strong>{item.name}</strong><small>{item.planned_start} 至 {item.planned_end} · {item.deadline.label}</small><small>输出物：{item.deliverable || "未填写"}{item.is_payment_node ? ` · 关联回款 ${item.payment_date} ${money(item.payment_amount)}` : ""}</small></div><div className="milestone-actions"><span className={`mini-state ${item.status}`}>{item.status === "completed" ? "已完成" : "待确认"}</span>{item.confirmed_by && <small>{item.confirmed_by}</small>}{item.status !== "completed" && <div className="row-action-buttons"><button type="button" className="row-action" onClick={() => editMilestone(item)}>编辑</button><button type="button" className="row-action danger-action" disabled={saving} onClick={() => removeMilestone(item)}>删除</button></div>}</div></article>)}</div>
      {milestoneFormOpen && <>{editingMilestoneId ? <div className="editing-banner"><strong>编辑待确认里程碑</strong><span>保存后将重新计算待办日期和关联回款提醒</span></div> : <div className="editing-banner"><strong>添加里程碑</strong><span>选择回款约定后，会按该里程碑结束日期生成回款跟踪事件</span></div>}
      <form className="milestone-form" onSubmit={addMilestone}>
        <label>里程碑名称<input required value={milestone.name} onChange={e => setMilestone({ ...milestone, name: e.target.value })} /></label>
        <label>负责人<input value={milestone.owner} onChange={e => setMilestone({ ...milestone, owner: e.target.value })} /></label>
        <label>开始日期<CalendarDatePicker value={milestone.planned_start} onChange={planned_start => setMilestone({ ...milestone, planned_start })} label="选择里程碑开始日期" /></label>
        <label>结束日期<CalendarDatePicker value={milestone.planned_end} onChange={planned_end => setMilestone({ ...milestone, planned_end })} label="选择里程碑结束日期" minDate={milestone.planned_start} /></label>
        <label className="span-2">输出物<input value={milestone.deliverable} onChange={e => setMilestone({ ...milestone, deliverable: e.target.value })} placeholder="如：需求说明书、软件设计方案" /></label>
        <label className="span-2">关联合同回款约定<select value={milestone.payment_term_id} onChange={e => setMilestone({ ...milestone, payment_term_id: e.target.value })}><option value="">不关联回款约定</option>{project.payment_terms.filter(term => !term.linked_milestone_id || term.linked_milestone_id === editingMilestoneId).map(term => <option key={term.payment_term_id} value={term.payment_term_id}>{term.name} · {term.payment_ratio ? `${term.payment_ratio}% · ` : ""}{money(term.planned_amount)}</option>)}</select><small>关联后以本里程碑结束日期作为回款跟踪日期。</small></label>
        {showsLegacyPaymentNote(milestone, editingTermId) && <p className="legacy-payment-note span-2">这是历史回款事件，仍按原有回款日期和金额维护；新建回款请先添加合同回款约定。</p>}
        <div className="form-actions span-2"><button className="button primary" disabled={saving}>{editingMilestoneId ? "保存修改" : "添加里程碑"}</button><button type="button" className="button secondary" disabled={saving} onClick={resetMilestoneForm}>取消</button></div>
      </form></>}
    </section>

    <section className="detail-section"><div className="section-title"><div><h3>管理干预 <span className="manual-tag">人工维护</span></h3><small>针对项目风险需要跟踪到关闭的事项；过了截止日期仍未关闭会进入待处理事项提醒，不影响风险等级。</small></div>{!interventionFormOpen && <button type="button" className="button secondary add-action" disabled={saving} onClick={() => { setIntervention({ ...emptyIntervention, owner: settings.project_manager }); setInterventionFormOpen(true); }}>＋ 发起干预</button>}</div>
      <div className="payment-term-list intervention-list">{project.interventions.map(item => <article key={item.intervention_id} className={`payment-term-item intervention-item ${item.status} ${item.overdue ? "overdue" : ""}`}>
        <div className="payment-term-copy"><strong>{item.title}</strong>
          <small>{INTERVENTION_TYPE_LABELS[item.intervention_type] ?? item.intervention_type} · 负责人 {item.owner || "未指定"} · {item.due_date ? `截止 ${item.due_date}` : "未设截止日期"}{item.overdue ? " · 已逾期" : ""}</small>
          {item.notes && <small>备注：{item.notes}</small>}</div>
        <div className="payment-term-actions"><span className={`mini-state ${item.status === "closed" ? "completed" : item.overdue ? "overdue" : ""}`}>{INTERVENTION_STATUS_LABELS[item.status] ?? item.status}</span>
          {item.status !== "closed" && <div className="row-action-buttons">{item.status === "open" && <button type="button" className="row-action" disabled={saving} onClick={() => moveIntervention(item, "in_progress")}>开始处理</button>}<button type="button" className="row-action" disabled={saving} onClick={() => moveIntervention(item, "closed")}>关闭</button></div>}</div>
      </article>)}</div>
      {project.interventions.length === 0 && !interventionFormOpen && <p className="detail-empty">暂无管理干预。项目出现需要管理者介入的风险时，在这里发起并跟踪到关闭。</p>}
      {interventionFormOpen && <form className="milestone-form" onSubmit={saveIntervention}>
        <label className="span-2">事项<input required value={intervention.title} placeholder="如：协调测试资源、与客户确认验收范围" onChange={e => setIntervention({ ...intervention, title: e.target.value })} /></label>
        <label>类型<select value={intervention.intervention_type} onChange={e => setIntervention({ ...intervention, intervention_type: e.target.value })}>{Object.entries(INTERVENTION_TYPE_LABELS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
        <label>负责人<input value={intervention.owner} placeholder="不填则为项目经理" list="project-manager-names" onChange={e => setIntervention({ ...intervention, owner: e.target.value })} /></label>
        <label>截止日期<CalendarDatePicker value={intervention.due_date} onChange={due_date => setIntervention({ ...intervention, due_date })} label="选择干预截止日期" /></label>
        <label>备注<input value={intervention.notes} placeholder="背景、期望结果" onChange={e => setIntervention({ ...intervention, notes: e.target.value })} /></label>
        <div className="form-actions span-2"><button className="button primary" disabled={saving}>发起干预</button><button type="button" className="button secondary" disabled={saving} onClick={() => { setIntervention(emptyIntervention); setInterventionFormOpen(false); }}>取消</button></div>
      </form>}
    </section>
  </aside></div>;
}

function SyncStatusPanel({ sync, logs, logPage, setLogPage, refresh, reportError }: { sync: SyncStatus | null; logs: CrmImportLogsResponse | null; logPage: number; setLogPage: React.Dispatch<React.SetStateAction<number>>; refresh: () => Promise<void>; reportError: (message: string | null) => void }) {
  const [importing, setImporting] = useState<"contract_order" | "sales_opportunity" | null>(null);
  const [result, setResult] = useState<{ dataType: "contract_order" | "sales_opportunity"; value: Awaited<ReturnType<typeof importCrmFile>> } | null>(null);
  async function upload(dataType: "contract_order" | "sales_opportunity", event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;
    setImporting(dataType); reportError(null);
    try {
      const value = await importCrmFile(dataType, file);
      setResult({ dataType, value });
      if (logPage !== 1) setLogPage(1);
      else await refresh();
    } catch (err) {
      reportError(err instanceof Error ? err.message : "CRM 数据导入失败");
    } finally {
      setImporting(null);
    }
  }
  return <section className="panel">
    <div className="panel-head">
      <div><h2>手工导入 CRM 数据</h2><p>分别上传合同/订单与销售机会文件；系统按 CRM 内部 ID 或项目编号识别新增、更新和无变化记录。</p></div>
      <div className="panel-actions">
        <button className="button secondary" onClick={() => void refresh()}>刷新状态</button>
      </div>
    </div>
    <div className="import-grid">
      <ImportCard dataType="contract_order" importing={importing} upload={upload} />
      <ImportCard dataType="sales_opportunity" importing={importing} upload={upload} />
    </div>
    {result && <div className="import-result"><strong>{result.dataType === "contract_order" ? "合同 / 订单" : "销售机会"}已导入</strong><span>{result.value.filename} · 解析 {result.value.parsed_rows} 行</span><b>新增 {result.value.validation.inserted_records ?? 0}</b><b>更新 {result.value.validation.updated_records ?? 0}</b><b>无变化 {result.value.validation.unchanged_records ?? 0}</b>{result.value.unmatched_project_rows > 0 && <em>待补充项目编号 {result.value.unmatched_project_rows} 条</em>}</div>}
    <section className="import-log-section">
      <div className="section-heading"><div><h3>导入记录日志</h3><p>仅记录已成功解析并保存的数据文件，最新上传显示在最前。</p></div></div>
      <div className="table-wrap">
        <table>
          <thead><tr><th>上传成功时间</th><th>数据类型</th><th>文件名称</th><th>解析成功</th><th>新增</th><th>更新</th><th>无变化</th></tr></thead>
          <tbody>{logs?.data.length ? logs.data.map(log => <tr key={log.log_id}><td>{formatImportTime(log.imported_at)}</td><td><span className={`type-pill ${log.data_type}`}>{log.data_type === "contract_order" ? "合同 / 订单" : "销售机会"}</span></td><td className="file-name">{log.filename}</td><td>{log.parsed_rows}</td><td>{log.inserted_records}</td><td>{log.updated_records}</td><td>{log.unchanged_records}</td></tr>) : <tr><td colSpan={7} className="empty-cell">暂无成功上传记录</td></tr>}</tbody>
        </table>
      </div>
      {(logs?.meta.total_pages ?? 0) > 1 && <div className="pagination import-log-pagination"><button disabled={logPage <= 1} onClick={() => setLogPage(page => page - 1)}>上一页</button><span>第 {logs?.meta.page ?? logPage} / {logs?.meta.total_pages ?? 1} 页 · 共 {logs?.meta.total_records ?? 0} 条</span><button disabled={!logs || logPage >= logs.meta.total_pages} onClick={() => setLogPage(page => page + 1)}>下一页</button></div>}
    </section>
    <div className="sync-note">
      <span className="manual-tag">导入规则</span>
      <p>系统只更新 CRM 主数据字段；项目类型、预算人天、项目经理、里程碑、回款确认等人工维护信息始终保留。当前可上传 Excel、CSV 或 JSON；待你确认首版模板后，可将字段别名锁定为固定模板。</p>
    </div>
  </section>;
}

function ImportCard(props: { dataType: "contract_order" | "sales_opportunity"; importing: "contract_order" | "sales_opportunity" | null; upload: (dataType: "contract_order" | "sales_opportunity", event: ChangeEvent<HTMLInputElement>) => Promise<void> }) {
  const isContract = props.dataType === "contract_order";
  const active = props.importing === props.dataType;
  return <article className={`import-card ${props.dataType}`}>
    <span className="import-icon">{isContract ? "▤" : "◎"}</span>
    <div><h3>{isContract ? "合同 / 订单数据" : "销售机会数据"}</h3><p>{isContract ? "主题、客户、金额、签约时间、所有者" : "项目编号、机会主题、预计签单日期、可能性、阶段、负责人、状态、更新时间"}</p></div>
    <label className={`button ${isContract ? "primary" : "secondary"} ${active ? "disabled" : ""}`}>{active ? "导入中…" : "选择文件上传"}<input type="file" accept=".xlsx,.csv,.json" hidden disabled={props.importing !== null} onChange={event => void props.upload(props.dataType, event)} /></label>
  </article>;
}

function CrmRecords(props: { records: RecordsResponse | null; loading: boolean; queryInput: string; setQueryInput: (value: string) => void; submitSearch: (event: FormEvent) => void; dataType: string; setDataType: (value: string) => void; status: string; setStatus: (value: string) => void; salesStage: string; setSalesStage: (value: string) => void; page: number; previousPage: () => void; nextPage: () => void; }) {
  return <section className="panel"><div className="panel-head"><div><h2>CRM 数据记录</h2><p>展示全部成功解析并入库的合同与销售机会；项目主数据库另按项目编号汇总</p></div><Search value={props.queryInput} setValue={props.setQueryInput} submit={props.submitSearch} /></div><div className="filters"><select value={props.dataType} onChange={e => props.setDataType(e.target.value)}><option value="">全部数据类型</option><option value="contract_order">合同 / 订单</option><option value="sales_opportunity">销售机会</option></select><select value={props.status} onChange={e => props.setStatus(e.target.value)}><option value="">全部状态</option><option value="已完成">合同已完成</option><option value="未完成">合同未完成</option><option value="成功">商机成功</option><option value="跟踪">商机跟踪</option></select><input className="crm-stage-filter" value={props.salesStage} onChange={e => props.setSalesStage(e.target.value)} placeholder="按销售阶段筛选" aria-label="按销售阶段筛选" /><span className="result-count">共 {props.records?.meta.total_records ?? 0} 条成功入库记录</span></div><div className="table-wrap"><table><thead><tr><th>类型</th><th>项目编号</th><th>主题</th><th>签约 / 预计签单</th><th>客户 / 分类</th><th>所有者</th><th>总金额</th><th>回款金额</th><th>开票金额</th><th>可能性</th><th>阶段</th><th>状态</th><th>源更新时间</th></tr></thead><tbody>{props.loading ? <LoadingRows colSpan={13} /> : props.records?.data.map(record => <RecordRow key={record.record_id} record={record} />)}</tbody></table></div><div className="pagination"><button disabled={props.page <= 1} onClick={props.previousPage}>上一页</button><span>第 {props.page} 页 · 共 {props.records?.meta.total_records ?? 0} 条</span><button disabled={!props.records?.meta.next_cursor} onClick={props.nextPage}>下一页</button></div></section>;
}

function Search({ value, setValue, submit, placeholder = "搜索主题、客户或项目编号" }: { value: string; setValue: (value: string) => void; submit: (event: FormEvent) => void; placeholder?: string }) { return <form className="search" onSubmit={submit}><input value={value} onChange={e => setValue(e.target.value)} placeholder={placeholder} /><button type="submit">搜索</button></form>; }
function RecordRow({ record }: { record: CrmRecord }) { return <tr><td><span className={`type-pill ${record.data_type}`}>{record.data_type === "contract_order" ? "合同/订单" : "销售机会"}</span></td><td><strong className="project-code">{record.project_number ?? "—"}</strong></td><td><div className="subject">{record.subject}</div></td><td><div>{record.signed_at ?? "—"}</div>{record.data_type === "sales_opportunity" && <small>预计签单日期</small>}</td><td><div>{record.customer ?? "—"}</div><small>{record.category ?? ""}</small></td><td>{record.owner}</td><td className="money">{record.total_amount?.display ?? "—"}</td><td className="money">{record.received_amount?.display ?? "—"}</td><td className="money">{record.invoiced_amount?.display ?? "—"}</td><td>{record.probability == null ? "—" : `${record.probability}%`}</td><td>{record.sales_stage ? <span className="stage-pill">{record.sales_stage}</span> : "—"}</td><td><span className={`state ${record.status === "成功" || record.status === "已完成" ? "success" : record.status === "未完成" ? "unfinished" : "tracking"}`}>{record.status ?? "—"}</span></td><td>{record.source_updated_at ?? "—"}</td></tr>; }
function LoadingRows({ colSpan = 10 }: { colSpan?: number }) { return <>{Array.from({ length: 5 }, (_, index) => <tr className="skeleton" key={index}><td colSpan={colSpan}><span /></td></tr>)}</>; }
function money(value?: string | null) { if (value == null) return "—"; return new Intl.NumberFormat("zh-CN", { style: "currency", currency: "CNY", maximumFractionDigits: 0 }).format(Number(value)); }
function formatImportTime(value: string) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : new Intl.DateTimeFormat("zh-CN", {
    year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false
  }).format(date).replace(/\//g, "-");
}
// Rejected and draft time stays listed but is not effort (ontology TimeEntry.countsAsEffort).
const NOT_EFFORT_STATUS: Record<string, string> = { rejected: "已驳回", draft: "草稿" };
function hoursToDays(value?: string | null) {
  if (value == null || value === "") return "—";
  return (Number(value) / 8).toFixed(2);
}
function timeEntryPersonDays(entry: Pick<TimeEntry, "hours" | "overtime_hours">) {
  return ((Number(entry.hours) + Number(entry.overtime_hours ?? 0)) / 8).toFixed(2);
}
function displayProjectName(projectNumber: string, subject: string) {
  const escaped = projectNumber.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const stripped = subject.replace(new RegExp(`^${escaped}\\s*[-—–_：:]\\s*`, "i"), "").trim();
  return stripped || subject;
}
function contractTypeLabel(value: ProjectSummary["contract_type"]) { return ({ construction: "系统实施", maintenance: "TSnT365", open_ended: "开口合同", sales: "售前阶段", unknown: "未分类" } as const)[value]; }
function LifecycleStatusOptions({ statuses }: { statuses: ProjectSummary["lifecycle_status"][] | undefined }) {
  return <>
    <option value="">全部项目状态</option>
    {(statuses ?? []).map(status => <option key={status} value={status}>{lifecycleLabel(status)}</option>)}
  </>;
}
function lifecycleLabel(value: ProjectSummary["lifecycle_status"]) { return ({ planned: "计划中", active: "执行中", on_hold: "已暂停", completed: "结束", closed: "已关闭" } as const)[value]; }
function riskLabel(value: ProjectSummary["risk_level"]) { return ({ healthy: "健康", warning: "关注", critical: "严重" } as const)[value]; }
function healthClass(value: string) { if (value === "额外投入" || value === "已超预算" || value === "高风险") return "critical"; if (value === "需关注" || value === "待维护预算") return "warning"; if (value === "无基线") return "baseline"; return "healthy"; }
function employeeStatusClass(value: string) { if (value === "工时充足") return "healthy"; if (value === "基本达标") return "baseline"; if (value === "需要关注") return "warning"; return "critical"; }
function statusLabel(value?: SyncStatus["status"]) { return ({ succeeded: "数据可用", running: "处理中", failed: "导入失败", auth_required: "需要登录", never_run: "尚未导入" } as const)[value ?? "never_run"]; }
function syncModeLabel(sync: SyncStatus | null) {
  if (sync?.validation.import_kind === "manual_upload") return `手工导入 · 新增 ${sync.validation.inserted_records ?? 0} / 更新 ${sync.validation.updated_records ?? 0}`;
  if (sync?.validation.sync_mode === "incremental") return `历史导入 · 新增 ${sync.validation.inserted_records ?? 0} / 更新 ${sync.validation.updated_records ?? 0}`;
  if (sync?.validation.sync_mode === "full") return "初始数据已导入";
  return "等待手工上传";
}
