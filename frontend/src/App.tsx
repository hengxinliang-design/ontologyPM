import { ChangeEvent, FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import DatePicker, { registerLocale } from "react-datepicker";
import { zhCN } from "date-fns/locale";
import "react-datepicker/dist/react-datepicker.css";
import {
  confirmMilestone,
  confirmPayment,
  chatWithPmAssistant,
  ApiError,
  analyzeProjectWithPmAssistant,
  createMilestone,
  deleteMilestone,
  deletePaymentNode,
  downloadExport,
  fetchCrmImportLogs,
  fetchEmployeeWorktime,
  fetchResourceOperations,
  fetchProject,
  fetchProjectHealth,
  fetchProjects,
  fetchRecords,
  fetchSyncStatus,
  fetchWorktimeBaseline,
  importTimeEntriesXlsx,
  importCrmFile,
  clearSession,
  createUser,
  fetchCurrentUser,
  fetchUsers,
  getStoredUser,
  login,
  logout,
  updateMilestone,
  updatePaymentNode,
  updateProjectSettings,
  updateWorktimeBaseline,
  updateUser
} from "./api";
import type {
  AuthenticatedUser,
  CrmRecord,
  CrmImportLogsResponse,
  EmployeeWorktimeResponse,
  EmployeeWorktimeRow,
  ManagedUser,
  MenuPermission,
  PmAssistantChatMessage,
  PortfolioResponse,
  ProjectDetail,
  ProjectHealthResponse,
  ProjectHealthRow,
  ProjectSummary,
  ResourceOperationsResponse,
  RecordsResponse,
  SyncStatus,
  TimeEntry,
  WorktimeBaseline
} from "./types";

const PAGE_SIZE = 20;
const IMPORT_LOG_PAGE_SIZE = 10;
const WORKTIME_MONTH_STORAGE_KEY = "tsg-pm-worktime-month";
const RESOURCE_MONTH_STORAGE_KEY = "tsg-pm-resource-month";
type View = "projects" | "health" | "resource" | "employeeWorktime" | "crm" | "sync" | "settings";

registerLocale("zh-CN", zhCN);

function parseMonth(value: string): Date | null {
  const match = /^(\d{4})-(\d{2})$/.exec(value);
  if (!match) return null;
  return new Date(Number(match[1]), Number(match[2]) - 1, 1);
}

function formatMonth(value: Date): string {
  return `${value.getFullYear()}-${String(value.getMonth() + 1).padStart(2, "0")}`;
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

export function App() {
  const [view, setView] = useState<View>("projects");
  const [authUser, setAuthUser] = useState<AuthenticatedUser | null>(() => getStoredUser());
  const [authLoading, setAuthLoading] = useState(() => Boolean(getStoredUser()));
  const [portfolio, setPortfolio] = useState<PortfolioResponse | null>(null);
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
  const [riskLevel, setRiskLevel] = useState("");
  const [contractStatus, setContractStatus] = useState("");
  const [healthMode, setHealthMode] = useState<"month" | "range">("month");
  const [healthContractStatus, setHealthContractStatus] = useState("");
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
  const [page, setPage] = useState(1);
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

  useEffect(() => {
    const routePermission: Record<View, MenuPermission> = {
      projects: "projects", health: "health", resource: "resource", crm: "crm",
      employeeWorktime: "employeeWorktime", sync: "sync", settings: "settings"
    };
    if (!authUser) return;
    if (!canAccess(routePermission[view])) {
      const fallback = (["projects", "health", "resource", "crm", "employeeWorktime", "sync"] as View[])
        .find(candidate => canAccess(routePermission[candidate]));
      if (fallback) setView(fallback);
    }
  }, [authUser, canAccess, view]);

  const load = useCallback(async () => {
    if (!authUser) { setLoading(false); return; }
    setLoading(true);
    setError(null);
    try {
      if (canAccess("sync")) setSync(await fetchSyncStatus());
      else setSync(null);
      if (view === "projects") {
        setPortfolio(await fetchProjects({ query, contractType, riskLevel, contractStatus }));
      } else if (view === "health") {
        setHealth(await fetchProjectHealth({
          month: healthMode === "month" ? healthMonth : undefined,
          startMonth: healthMode === "range" ? healthStartMonth : undefined,
          endMonth: healthMode === "range" ? healthEndMonth : undefined,
          query: query || undefined,
          contractStatus: healthContractStatus || undefined,
        }));
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
      } else if (view === "resource") {
        setResourceOperations(await fetchResourceOperations({
          month: resourcePeriodMode === "month" ? resourceMonth : undefined,
          startMonth: resourcePeriodMode === "range" ? resourceStartMonth : undefined,
          endMonth: resourcePeriodMode === "range" ? resourceEndMonth : undefined,
          employeeName: resourceEmployeeName || undefined
        }));
      } else if (view === "crm") {
        setRecords(await fetchRecords({ dataType, query, status, page, pageSize: PAGE_SIZE }));
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
  }, [authUser, canAccess, view, query, contractType, riskLevel, contractStatus, dataType, status, page, healthMode, healthContractStatus, healthMonth, healthStartMonth, healthEndMonth, worktimePeriod, worktimeMonth, worktimeYear, resourceMonth, resourcePeriodMode, resourceStartMonth, resourceEndMonth, resourceEmployeeName, crmImportLogPage]);

  useEffect(() => { void load(); }, [load]);

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
    { label: "项目总数", value: portfolio?.summary.total_projects ?? "—", tone: "blue" },
    { label: "合同总额", value: money(portfolio?.summary.contract_value), tone: "green" },
    { label: "已回款", value: money(portfolio?.summary.received_amount), tone: "amber" },
    { label: "风险项目", value: (portfolio?.summary.critical_projects ?? 0) + (portfolio?.summary.warning_projects ?? 0), tone: "violet" }
  ] : view === "health" ? [
    { label: "监控项目", value: health?.summary.total_projects ?? "—", tone: "blue" },
    { label: "健康", value: health?.summary.healthy_projects ?? "—", tone: "green" },
    { label: "需关注", value: health?.summary.warning_projects ?? "—", tone: "amber" },
    { label: "已超预算", value: health?.summary.critical_projects ?? "—", tone: "violet" }
  ] : view === "employeeWorktime" ? [
    { label: "统计员工", value: employeeWorktime?.summary.total_employees ?? "—", tone: "blue" },
    { label: "工时充足", value: employeeWorktime?.summary.sufficient_count ?? "—", tone: "green" },
    { label: "需要关注", value: employeeWorktime?.summary.attention_count ?? "—", tone: "amber" },
    { label: "严重不足", value: employeeWorktime?.summary.critical_count ?? "—", tone: "violet" }
  ] : view === "resource" ? [
    { label: "纳入监控人员", value: resourceOperations?.summary.total_employees ?? "—", tone: "blue" },
    { label: "可调剂人员", value: resourceOperations?.summary.available_count ?? "—", tone: "green" },
    { label: "负荷较高", value: resourceOperations?.summary.high_load_count ?? "—", tone: "amber" },
    { label: "需补充资源项目", value: resourceOperations?.summary.projects_needing_support ?? "—", tone: "violet" }
  ] : view === "sync" ? [
    { label: "导入状态", value: statusLabel(sync?.status), tone: sync?.status === "succeeded" ? "green" : "amber" },
    { label: "合同 / 订单源记录", value: sync?.source_stats.contract_order ?? "—", tone: "blue" },
    { label: "销售机会源记录", value: sync?.source_stats.sales_opportunity ?? "—", tone: "amber" },
    { label: "转项目已合并", value: sync?.validation.merged_opportunities ?? "—", tone: "violet" }
  ] : view === "settings" ? [] : [
    { label: "有效记录", value: records?.meta.total_records ?? "—", tone: "blue" },
    { label: "合同 / 订单", value: sync?.source_stats.contract_order ?? "—", tone: "green" },
    { label: "销售机会", value: sync?.source_stats.sales_opportunity ?? "—", tone: "amber" },
    { label: "转项目商机", value: sync?.validation.merged_opportunities ?? "—", tone: "violet" }
  ], [view, portfolio, health, employeeWorktime, resourceOperations, records, sync]);

  function switchView(next: View) {
    setView(next); setQuery(""); setQueryInput(""); setPage(1); setSelected(null);
    if (next === "sync") setCrmImportLogPage(1);
  }

  function submitSearch(event: FormEvent) {
    event.preventDefault(); setPage(1); setQuery(queryInput.trim());
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
        <nav>
          <div className="nav-group">
            <p>查询与运营监控</p>
            {canAccess("projects") && <button className={`nav-item ${view === "projects" ? "active" : ""}`} onClick={() => switchView("projects")}><span>◫</span> 项目总览</button>}
            {canAccess("health") && <button className={`nav-item ${view === "health" ? "active" : ""}`} onClick={() => switchView("health")}><span>▣</span> 项目运营监控</button>}
            {canAccess("resource") && <button className={`nav-item ${view === "resource" ? "active" : ""}`} onClick={() => switchView("resource")}><span>◉</span> 资源运营监控</button>}
            {canAccess("crm") && <button className={`nav-item ${view === "crm" ? "active" : ""}`} onClick={() => switchView("crm")}><span>⌘</span> CRM 数据查询</button>}
          </div>
          <div className="nav-group nav-group-maintenance">
            <p>数据维护</p>
            {canAccess("employeeWorktime") && <button className={`nav-item ${view === "employeeWorktime" ? "active" : ""}`} onClick={() => switchView("employeeWorktime")}><span>◷</span> 员工工时数据</button>}
            {canAccess("sync") && <button className={`nav-item ${view === "sync" ? "active" : ""}`} onClick={() => switchView("sync")}><span>⇧</span> 项目/商机数据维护</button>}
          </div>
          <div className="nav-group nav-group-system">
            {authUser.is_admin && <><p>系统</p><button className={`nav-item ${view === "settings" ? "active" : ""}`} onClick={() => switchView("settings")}><span>⚙</span> 设置</button></>}
          </div>
        </nav>
        <div className="sidebar-foot">
          <span className={`status-dot ${sync?.status === "succeeded" ? "ok" : "warn"}`} />
          <div><strong>{authUser.username}</strong><small>{authUser.is_admin ? "管理员" : "受限访问用户"}</small></div>
          <button type="button" className="logout-button" onClick={() => void handleLogout()}>Logout</button>
        </div>
      </aside>

      <main>
        <header className="topbar">
          <div><p className="eyebrow">PROJECT LIFECYCLE CONTROL</p><h1>{view === "projects" ? "项目总览" : view === "health" ? "项目运营监控" : view === "resource" ? "资源运营监控" : view === "employeeWorktime" ? "员工工时数据" : view === "sync" ? "项目/商机数据维护" : view === "settings" ? "系统设置" : "CRM 数据查询"}</h1></div>
          {view === "crm" && <div className="top-actions">
            <button className="button secondary" onClick={() => void downloadExport("csv")}>导出 CSV</button>
          </div>}
        </header>
        {error && <div className="error-banner">{error}</div>}
        {cards.length > 0 && <section className="metrics">{cards.map(card => <article className={`metric ${card.tone}`} key={card.label}><span>{card.label}</span><strong>{card.value}</strong></article>)}</section>}

        {view === "projects" ? (
          <ProjectPortfolio
            portfolio={portfolio} loading={loading} queryInput={queryInput}
            setQueryInput={setQueryInput} submitSearch={submitSearch}
            contractType={contractType} setContractType={setContractType}
            riskLevel={riskLevel} setRiskLevel={setRiskLevel}
            contractStatus={contractStatus} setContractStatus={setContractStatus} openProject={openProject}
          />
        ) : view === "health" ? (
          <ProjectHealth
            health={health} loading={loading} queryInput={queryInput}
            setQueryInput={setQueryInput} submitSearch={submitSearch}
            mode={healthMode} setMode={setHealthMode}
            contractStatus={healthContractStatus} setContractStatus={setHealthContractStatus}
            month={healthMonth} setMonth={setHealthMonth}
            startMonth={healthStartMonth} setStartMonth={setHealthStartMonth}
            endMonth={healthEndMonth} setEndMonth={setHealthEndMonth}
            openProject={openProject}
          />
        ) : view === "employeeWorktime" ? (
          <EmployeeWorktime
            assessment={employeeWorktime} baseline={worktimeBaseline}
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
            openProject={openProject}
          />
        ) : view === "sync" ? (
          <SyncStatusPanel sync={sync} logs={crmImportLogs} logPage={crmImportLogPage} setLogPage={setCrmImportLogPage} refresh={load} reportError={setError} />
        ) : view === "settings" ? (
          <UserSettings />
        ) : (
          <CrmRecords
            records={records} loading={loading} queryInput={queryInput}
            setQueryInput={setQueryInput} submitSearch={submitSearch}
            dataType={dataType} setDataType={setDataType} status={status}
            setStatus={setStatus} page={page} setPage={setPage}
          />
        )}
      </main>
      {selected && <ProjectDrawer
        project={selected}
        close={() => setSelected(null)}
        refresh={async () => {
          const refreshed = await fetchProject(selected.project_number);
          setSelected(refreshed);
          setPortfolio(await fetchProjects({ query, contractType, riskLevel, contractStatus }));
        }}
      />}
      {canAccess("projects") && <PmAssistant />}
    </div>
  );
}

const MENU_OPTIONS: Array<{ value: MenuPermission; label: string; group: string }> = [
  { value: "projects", label: "项目总览", group: "查询与运营监控" },
  { value: "health", label: "项目运营监控", group: "查询与运营监控" },
  { value: "resource", label: "资源运营监控", group: "查询与运营监控" },
  { value: "crm", label: "CRM 数据查询", group: "查询与运营监控" },
  { value: "employeeWorktime", label: "员工工时数据", group: "数据维护" },
  { value: "sync", label: "项目/商机数据维护", group: "数据维护" },
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

function UserSettings() {
  const [users, setUsers] = useState<ManagedUser[]>([]);
  const [selected, setSelected] = useState<ManagedUser | null>(null);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [isAdmin, setIsAdmin] = useState(false);
  const [isActive, setIsActive] = useState(true);
  const [permissions, setPermissions] = useState<MenuPermission[]>(["projects"]);
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
    setSelected(null); setUsername(""); setPassword(""); setIsAdmin(false); setIsActive(true); setPermissions(["projects"]); setMessage(null);
  }
  function beginEdit(user: ManagedUser) {
    setSelected(user); setUsername(user.username); setPassword(""); setIsAdmin(user.is_admin); setIsActive(user.is_active); setPermissions(user.permissions); setMessage(null);
  }
  function togglePermission(permission: MenuPermission) {
    setPermissions(current => current.includes(permission) ? current.filter(item => item !== permission) : [...current, permission]);
  }
  async function submit(event: FormEvent) {
    event.preventDefault();
    setSaving(true); setMessage(null);
    try {
      if (selected) {
        await updateUser(selected.username, { password: password || undefined, is_admin: isAdmin, is_active: isActive, permissions });
        setMessage("用户权限已保存");
      } else {
        await createUser({ username, password, is_admin: isAdmin, permissions });
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
          <div><strong>{user.username}</strong><small>{user.is_admin ? "管理员 · 全部菜单" : `${user.permissions.length} 项菜单权限`} · {user.is_active ? "已启用" : "已停用"}</small></div>
          <button type="button" className="button secondary" onClick={() => beginEdit(user)}>编辑</button>
        </article>)}{!loading && !users.length && <p className="detail-empty">暂无用户</p>}</div>
      </section>
      <form className="panel user-form" onSubmit={submit}>
        <div className="settings-section-head"><div><h3>{selected ? `编辑用户：${selected.username}` : "添加用户"}</h3><p>{selected ? "留空密码表示不修改原密码。" : "新增用户后按选择的菜单权限访问系统。"}</p></div>{selected && <button type="button" className="text-button" onClick={beginCreate}>取消编辑</button>}</div>
        {message && <div className="settings-message">{message}</div>}
        <div className="user-form-fields"><label>登录账号<input value={username} onChange={event => setUsername(event.target.value)} disabled={Boolean(selected) || saving} required /></label>
        <label>{selected ? "新密码（可选）" : "登录密码"}<input type="password" value={password} onChange={event => setPassword(event.target.value)} disabled={saving} minLength={selected ? undefined : 8} required={!selected} /></label></div>
        <label className="check-row"><input type="checkbox" checked={isAdmin} onChange={event => setIsAdmin(event.target.checked)} disabled={saving || selected?.username === "admin"} />管理员（可访问全部菜单并管理账户）</label>
        {selected && <label className="check-row"><input type="checkbox" checked={isActive} onChange={event => setIsActive(event.target.checked)} disabled={saving || selected.username === "admin"} />启用此账户</label>}
        {!isAdmin && <fieldset><legend>可访问菜单</legend><div className="permission-grid">{MENU_OPTIONS.filter(item => item.value !== "settings").map(item => <label key={item.value}><input type="checkbox" checked={permissions.includes(item.value)} onChange={() => togglePermission(item.value)} disabled={saving} />{item.label}<small>{item.group}</small></label>)}</div></fieldset>}
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
        <div><span className="pm-assistant-kicker">AI PROJECT COPILOT</span><h2>PM Assistant</h2><p>基于当前系统资料回答项目管理问题</p></div>
        <button type="button" className="pm-assistant-close" aria-label="关闭 PM Assistant" onClick={() => setOpen(false)}>×</button>
      </header>
      <div className="pm-assistant-conversation">
        {!messages.length && <div className="pm-assistant-welcome"><strong>可以问我什么？</strong><p>我会检索项目、CRM、里程碑与工时资料后给出结论；资料不足时会说明缺少什么。</p><div className="pm-assistant-suggestions">{["哪些项目需要优先关注？", "目前有哪些待回款项目？", "工时超预算的项目有哪些？"].map(question => <button key={question} type="button" onClick={() => void send(question)}>{question}</button>)}</div></div>}
        {messages.map((item, index) => <article key={`${item.role}-${index}`} className={`pm-message ${item.role}`}><span>{item.role === "user" ? "你" : "PM Assistant"}</span><p>{item.content}</p></article>)}
        {sending && <article className="pm-message assistant pending"><span>PM Assistant</span><p>正在检索系统资料并整理回答…</p></article>}
      </div>
      {sources.length > 0 && <div className="pm-assistant-sources"><span>本次检索</span>{sources.map(source => <small key={source}>{source}</small>)}</div>}
      {error && <p className="pm-assistant-error">{error}</p>}
      <form className="pm-assistant-form" onSubmit={submit}><label htmlFor="pm-assistant-input">你的问题</label><div><input id="pm-assistant-input" value={draft} onChange={event => setDraft(event.target.value)} disabled={sending} placeholder="例如：哪些项目本月需要跟进？" maxLength={2000} /><button className="button primary" disabled={sending || !draft.trim()} type="submit">发送</button></div></form>
    </section>}
    <button type="button" className="pm-assistant-launcher" aria-expanded={open} onClick={() => setOpen(!open)}><span>AI</span><strong>PM Assistant</strong></button>
  </div>;
}

function ProjectPortfolio(props: {
  portfolio: PortfolioResponse | null; loading: boolean; queryInput: string;
  setQueryInput: (value: string) => void; submitSearch: (event: FormEvent) => void;
  contractType: string; setContractType: (value: string) => void;
  riskLevel: string; setRiskLevel: (value: string) => void;
  contractStatus: string; setContractStatus: (value: string) => void;
  openProject: (projectNumber: string) => void;
}) {
  return <section className="panel">
    <div className="panel-head"><div><h2>项目主数据库</h2><p>按项目代码汇总 CRM 主数据、员工工时、里程碑与回款状态</p></div><Search value={props.queryInput} setValue={props.setQueryInput} submit={props.submitSearch} placeholder="搜索项目名称、客户名称或项目编号" /></div>
    <div className="data-boundary"><span className="source-tag">系统接口数据</span><p>合同、商机、金额和工时由接口增量同步，只读</p><span className="manual-tag">人工维护数据</span><p>合同类别、预算人天、里程碑和确认状态由管理人员维护</p></div>
    <div className="filters">
      <select value={props.contractType} onChange={e => props.setContractType(e.target.value)}><option value="">全部合同类型</option><option value="construction">建设类</option><option value="maintenance">维护类</option><option value="open_ended">开口类</option><option value="sales">销售类</option><option value="unknown">未分类</option></select>
      <select value={props.riskLevel} onChange={e => props.setRiskLevel(e.target.value)}><option value="">全部风险</option><option value="critical">严重</option><option value="warning">关注</option><option value="healthy">健康</option></select>
      <select value={props.contractStatus} onChange={e => props.setContractStatus(e.target.value)}><option value="">全部合同状态</option><option value="进行中">进行中</option><option value="已结束">已结束</option><option value="未签约">未签约</option></select>
      <span className="result-count">共 {props.portfolio?.summary.total_projects ?? 0} 个项目</span>
    </div>
    <div className="table-wrap"><table><thead><tr><th>风险</th><th>项目代码</th><th>项目名称</th><th>类别</th><th>状态</th><th>合同状态</th><th>里程碑</th><th>项目类型</th><th>回款</th><th>预算人天</th><th>累计实际人天</th></tr></thead><tbody>
      {props.loading ? <LoadingRows /> : props.portfolio?.data.map(project => <ProjectRow key={project.project_number} project={project} open={() => props.openProject(project.project_number)} />)}
      {!props.loading && props.portfolio?.data.length === 0 && <tr><td className="empty" colSpan={11}>没有符合条件的项目</td></tr>}
    </tbody></table></div>
  </section>;
}

function ProjectRow({ project, open }: { project: ProjectSummary; open: () => void }) {
  return <tr className="clickable" onClick={open}>
    <td><RiskBadge project={project} /></td>
    <td><strong className="project-code">{project.project_number}</strong></td>
    <td><div className="subject">{displayProjectName(project.project_number, project.subject)}</div><small>{project.customer ?? "—"}</small></td>
    <td><span className="category-pill">{project.master_category}</span></td>
    <td><div>{lifecycleLabel(project.status)}</div><small>{project.project_manager ?? "未设置负责人"}</small></td>
    <td><ContractStatusCell contractStatus={project.contract_status} outstandingReceivableAmount={project.outstanding_receivable_amount} /></td>
    <td>{project.current_milestone ? <><div>{project.current_milestone.name}</div><small className={project.current_milestone.deadline.urgency === "overdue" ? "danger-text" : ""}>{project.current_milestone.deadline.label}</small></> : <span>未设置</span>}</td>
    <td>{contractTypeLabel(project.contract_type)}</td>
    <td><PaymentCell project={project} /></td>
    <td><Budget project={project} /></td>
    <td><div>{project.actual_person_days} 人天</div></td>
  </tr>;
}

function RiskBadge({ project }: { project: ProjectSummary }) {
  const alerts = project.anomaly_alerts ?? [];
  const hasDetails = alerts.length > 0 || project.risk_reasons.length > 0;
  return <div className="risk-cell" onClick={event => event.stopPropagation()}>
    <span className={`risk ${project.risk_level}`}>{riskLabel(project.risk_level)}</span>
    {project.anomaly_count > 0 && <small className="danger-text">{project.anomaly_count} 项提醒</small>}
    {hasDetails && <div className="risk-popover" role="tooltip">
      <h4>{project.project_number} 风险点</h4>
      {alerts.length > 0 ? alerts.map((alert, index) => <article key={`${alert.code}-${index}`}>
        <div><strong>{alert.title}</strong><span className={alert.severity}>{alert.severity === "critical" ? "严重" : alert.severity === "warning" ? "关注" : "提示"}</span></div>
        <p>{alert.detail}</p>
      </article>) : project.risk_reasons.map(reason => <article key={reason}><p>{reason}</p></article>)}
    </div>}
  </div>;
}

function Budget({ project }: { project: ProjectSummary }) {
  if (project.contract_type === "open_ended") return <><div>{project.billable_person_days} 人天</div><small>可结算</small></>;
  if (project.planned_person_days == null || project.budget_hours == null) return <span>未设置</span>;
  return <div className="budget"><i><em style={{ width: `${Math.min(project.budget_utilization ?? 0, 100)}%` }} /></i><small>{project.actual_person_days}/{project.planned_person_days} 人天</small></div>;
}

function PaymentCell({ project }: { project: ProjectSummary }) {
  const summary = project.payment_summary;
  if (!summary.total_nodes) return <span>未设置</span>;
  return <><div>{summary.paid_nodes}/{summary.total_nodes} 已完成</div>{summary.next_node ? <small className={summary.next_node.deadline.urgency === "overdue" ? "danger-text" : ""}>下个：{summary.next_node.name} · {summary.next_node.deadline.label}</small> : <small>全部回款节点完成</small>}</>;
}

function ContractStatusCell({ contractStatus, outstandingReceivableAmount }: {
  contractStatus: "已结束" | "进行中" | null;
  outstandingReceivableAmount: string | null;
}) {
  if (!contractStatus) return <span>—</span>;
  if (contractStatus === "已结束") return <span className="state success">已结束</span>;
  return <><span className="state unfinished">进行中</span><small>待回款 {money(outstandingReceivableAmount)}</small></>;
}

function ProjectHealth(props: {
  health: ProjectHealthResponse | null; loading: boolean; queryInput: string;
  setQueryInput: (value: string) => void; submitSearch: (event: FormEvent) => void;
  mode: "month" | "range"; setMode: (value: "month" | "range") => void;
  contractStatus: string; setContractStatus: (value: string) => void;
  month: string; setMonth: (value: string) => void;
  startMonth: string; setStartMonth: (value: string) => void;
  endMonth: string; setEndMonth: (value: string) => void;
  openProject: (projectNumber: string) => void;
}) {
  return <section className="panel">
    <div className="panel-head"><div><h2>项目运营监控</h2><p>按月份或时间范围监控预算燃尽、超支风险、月度趋势和剩余预算</p></div><Search value={props.queryInput} setValue={props.setQueryInput} submit={props.submitSearch} placeholder="搜索项目名称、客户名称或项目编号" /></div>
    <div className="filters health-filters">
      <select value={props.mode} onChange={e => props.setMode(e.target.value as "month" | "range")}><option value="month">按单月</option><option value="range">按时间范围</option></select>
      {props.mode === "month" ? <label>统计月份<MonthPicker value={props.month} onChange={props.setMonth} label="选择统计月份" /></label> : <><label>开始月份<MonthPicker value={props.startMonth} onChange={props.setStartMonth} label="选择开始月份" /></label><label>结束月份<MonthPicker value={props.endMonth} onChange={props.setEndMonth} label="选择结束月份" /></label></>}
      <select value={props.contractStatus} aria-label="选择合同状态" onChange={e => props.setContractStatus(e.target.value)}><option value="">全部合同状态</option><option value="进行中">进行中</option><option value="已结束">已结束</option><option value="未签约">未签约</option></select>
      <span className="result-count">统计范围：{props.health?.meta.start_month ?? "—"} 至 {props.health?.meta.end_month ?? "—"} · 共 {props.health?.summary.total_projects ?? 0} 个项目</span>
    </div>
    <div className="table-wrap"><table><thead><tr><th>项目代码</th><th>项目名称</th><th>PM负责人</th><th>项目状态</th><th>合同状态</th><th>计划开始</th><th>计划结束</th><th>预算人天</th><th>计划月数</th><th>月均预算(人天)</th><th>累计实际(人天)</th><th>本期投入(人天)</th><th>上月投入(人天)</th><th>参与人数</th><th>燃尽率</th><th>超支预警</th><th>月度趋势</th><th>综合健康状态</th><th>预计完工</th><th>剩余预算(人天)</th></tr></thead><tbody>
      {props.loading ? <LoadingRows colSpan={20} /> : props.health?.data.map(row => <ProjectHealthRowView key={row.project_number} row={row} open={() => props.openProject(row.project_number)} />)}
      {!props.loading && props.health?.data.length === 0 && <tr><td className="empty" colSpan={20}>没有符合条件的健康度数据</td></tr>}
    </tbody></table></div>
  </section>;
}

function ProjectHealthRowView({ row, open }: { row: ProjectHealthRow; open: () => void }) {
  return <tr className="clickable" onClick={open}>
    <td><strong className="project-code">{row.project_number}</strong></td>
    <td><div className="subject">{displayProjectName(row.project_number, row.project_name)}</div>{row.risk_reasons.length > 0 && <small className="danger-text">{row.risk_reasons[0]}</small>}</td>
    <td>{row.pm_owner ?? "请添加PM负责人"}</td>
    <td>{lifecycleLabel(row.project_status)}</td>
    <td><ContractStatusCell contractStatus={row.contract_status} outstandingReceivableAmount={row.outstanding_receivable_amount} /></td>
    <td>{row.planned_start ?? "请填写"}</td>
    <td>{row.planned_end ?? "请填写"}</td>
    <td>{row.budget_person_days ?? "请填预算"}</td>
    <td>{row.plan_months ?? "—"}</td>
    <td>{row.monthly_budget_person_days ?? "—"}</td>
    <td><strong>{row.cumulative_actual_person_days}</strong></td>
    <td>{row.period_actual_person_days}</td>
    <td>{row.previous_month_person_days}</td>
    <td>{row.participant_count}</td>
    <td>{row.burn_rate_percent ? `${row.burn_rate_percent}%` : "—"}</td>
    <td><span className={`health-tag ${healthClass(row.overrun_warning)}`}>{row.overrun_warning}</span></td>
    <td>{row.monthly_trend}</td>
    <td><span className={`health-tag ${healthClass(row.health_status)}`}>{row.health_status}</span></td>
    <td>{row.completion_forecast}</td>
    <td>{row.remaining_budget_person_days ?? "—"}</td>
  </tr>;
}

function ResourceOperations({ data, loading, month, setMonth, periodMode, setPeriodMode, startMonth, setStartMonth, endMonth, setEndMonth, employeeInput, setEmployeeInput, submitEmployeeSearch, clearEmployeeSearch, openProject }: {
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
}) {
  const candidates = data?.staffing_candidates ?? [];
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
      <span className="result-count">统计范围：{data?.meta.start_date ?? "—"} 至 {data?.meta.end_date ?? "—"} · {data?.meta.employee_name ? `人员：${data.meta.employee_name} · ` : ""}基线 {hoursToDays(data?.meta.baseline_hours)} 人天</span>
    </div>
    <div className="resource-analysis-context">
      <div className="resource-method-note"><span>资源建议</span><p>{data?.meta.recommendation_limitations ?? "基于已导入的工时、工时基线与项目计划计算。"}</p></div>
      <section className="resource-priority-section">
        <div className="section-title"><div><h3>优先补充资源项目</h3><small>仅展示计划期内、本期投入低于月度计划 70% 的项目；建议先由 PM 确认范围和技能匹配。</small></div><span className="resource-count">{candidates.length} 个候选项目</span></div>
        {loading ? <div className="resource-loading">正在分析项目投入缺口…</div> : candidates.length > 0 ? <div className="resource-candidate-grid">{candidates.map(candidate => <article key={candidate.project_number} className={`resource-candidate ${candidate.priority === "高优先" ? "high" : ""}`}>
          <div><span className={`health-tag ${candidate.priority === "高优先" ? "critical" : "warning"}`}>{candidate.priority}</span><button type="button" onClick={() => void openProject(candidate.project_number)}>{candidate.project_number}</button></div>
          <h4>{displayProjectName(candidate.project_number, candidate.project_name)}</h4>
          <p>{candidate.reason}</p>
          <footer><strong>预计缺口 {candidate.resource_gap_person_days} 人天</strong><small>月度计划 {candidate.monthly_budget_person_days} 人天</small></footer>
        </article>)}</div> : <p className="detail-empty">当前没有满足“计划投入不足”条件的项目。未维护项目计划或预算的项目不会纳入资源缺口推算。</p>}
      </section>
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
  </section>;
}

function resourceAllocationClass(status: string) {
  if (status === "负荷过高") return "critical";
  if (status === "负荷较高") return "warning";
  if (status === "可优先调剂" || status === "可适度支持") return "healthy";
  return "baseline";
}

function EmployeeWorktime(props: {
  assessment: EmployeeWorktimeResponse | null;
  baseline: WorktimeBaseline | null;
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
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<Partial<WorktimeBaseline>>({});
  const [saving, setSaving] = useState(false);
  const [importing, setImporting] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [importNotice, setImportNotice] = useState<{ kind: "success" | "error"; text: string } | null>(null);

  useEffect(() => {
    if (props.baseline) setDraft(props.baseline);
  }, [props.baseline]);

  async function saveBaseline(event: FormEvent) {
    event.preventDefault();
    setSaving(true); setMessage(null);
    try {
      await updateWorktimeBaseline(draft);
      await props.refresh();
      setEditing(false);
      setMessage("工时基线参数已保存");
    } catch (err) {
      setMessage(err instanceof Error ? err.message : "保存失败");
    } finally {
      setSaving(false);
    }
  }

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
    <div className="panel-head employee-worktime-head"><div><h2>员工工时数据</h2><p>导入员工每日项目工时，按项目代码汇总并关联项目运营监控</p></div><div className="panel-actions employee-worktime-actions"><label className={`button primary import-timesheet-button ${importing ? "disabled" : ""}`}>{importing ? "导入中…" : "导入工时 Excel"}<input type="file" accept=".xlsx" onChange={uploadTimesheet} disabled={importing} hidden /></label><Search value={props.queryInput} setValue={props.setQueryInput} submit={props.submitSearch} /></div></div>
    {importNotice && <div className={`import-notice ${importNotice.kind}`} role="status"><span aria-hidden="true">{importNotice.kind === "success" ? "✓" : "!"}</span><div><strong>{importNotice.kind === "success" ? "工时导入完成" : "工时导入未完成"}</strong><p>{importNotice.text}</p></div><button type="button" aria-label="关闭导入提示" onClick={() => setImportNotice(null)}>×</button></div>}
    <div className="filters health-filters employee-worktime-filters">
      <select value={props.period} onChange={e => props.setPeriod(e.target.value as "month" | "year")}><option value="month">按月统计</option><option value="year">年度汇总</option></select>
      {props.period === "month" ? <label>统计月份<MonthPicker value={props.month} onChange={props.setMonth} label="选择员工工时统计月份" /></label> : <label>统计年度<input type="number" min="2000" max="2100" value={props.year} onChange={e => props.setYear(e.target.value)} /></label>}
      <span className="result-count">统计范围：{props.assessment?.meta.start_date ?? "—"} 至 {props.assessment?.meta.end_date ?? "—"} · 基线 {hoursToDays(props.assessment?.meta.baseline_hours)} 人天</span>
    </div>
    <div className="employee-worktime-baseline-region">
    <section className="baseline-box">
      <div className="section-title"><div><h3>工时基线参数 <span className="manual-tag">人工维护</span></h3><small>作为员工工时利用率评估和后续其他表单引用的统一配置</small></div><button className="button secondary" onClick={() => setEditing(!editing)}>{editing ? "收起配置" : "编辑配置"}</button></div>
      {message && <div className="inline-message">{message}</div>}
      {props.baseline && !editing && <div className="baseline-grid"><span>标准工作日/年：<b>{props.baseline.standard_work_days_per_year}</b> 天</span><span>折算标准：<b>1 人天 = {props.baseline.standard_hours_per_day}</b> 小时</span><span>年度基线：<b>{hoursToDays(props.baseline.annual_baseline_hours)}</b> 人天</span><span>有效基线：<b>{hoursToDays(props.baseline.effective_baseline_hours)}</b> 人天</span><span>月均基线：<b>{hoursToDays(props.baseline.monthly_baseline_hours)}</b> 人天</span><span>季度基线：<b>{hoursToDays(props.baseline.quarterly_baseline_hours)}</b> 人天</span><span>最低目标：<b>{props.baseline.minimum_utilization_percent}%</b></span><span>优秀目标：<b>{props.baseline.excellent_utilization_percent}%</b></span></div>}
      {editing && <form className="config-form" onSubmit={saveBaseline}>
        <label>标准工作日/年<input value={draft.standard_work_days_per_year ?? ""} onChange={e => setDraft({ ...draft, standard_work_days_per_year: e.target.value })} /></label>
        <label>标准工时/天<input value={draft.standard_hours_per_day ?? ""} onChange={e => setDraft({ ...draft, standard_hours_per_day: e.target.value })} /></label>
        <label>年度工时基线(h)<input value={draft.annual_baseline_hours ?? ""} onChange={e => setDraft({ ...draft, annual_baseline_hours: e.target.value })} /></label>
        <label>有效工时基线(h)<input value={draft.effective_baseline_hours ?? ""} onChange={e => setDraft({ ...draft, effective_baseline_hours: e.target.value })} /></label>
        <label>月均工时基线(h)<input value={draft.monthly_baseline_hours ?? ""} onChange={e => setDraft({ ...draft, monthly_baseline_hours: e.target.value })} /></label>
        <label>季度工时基线(h)<input value={draft.quarterly_baseline_hours ?? ""} onChange={e => setDraft({ ...draft, quarterly_baseline_hours: e.target.value })} /></label>
        <label>最低利用率目标(%)<input value={draft.minimum_utilization_percent ?? ""} onChange={e => setDraft({ ...draft, minimum_utilization_percent: e.target.value })} /></label>
        <label>优秀利用率目标(%)<input value={draft.excellent_utilization_percent ?? ""} onChange={e => setDraft({ ...draft, excellent_utilization_percent: e.target.value })} /></label>
        <label>关注阈值(%)<input value={draft.attention_utilization_percent ?? ""} onChange={e => setDraft({ ...draft, attention_utilization_percent: e.target.value })} /></label>
        <label className="span-2">备注<input value={draft.notes ?? ""} onChange={e => setDraft({ ...draft, notes: e.target.value })} /></label>
        <button className="button primary span-2" disabled={saving}>{saving ? "保存中…" : "保存工时基线配置"}</button>
      </form>}
    </section>
    </div>
    <div className="table-wrap"><table><thead><tr><th>姓名</th><th>填报人天</th><th>加班人天</th><th>确认人天</th><th>确认缺口(人天)</th><th>基线(人天)</th><th>工时利用率</th><th>项目数</th><th>记录数</th><th>工时利用率评价</th></tr></thead><tbody>
      {props.loading ? <LoadingRows colSpan={10} /> : props.assessment?.data.map(row => <EmployeeWorktimeRowView key={row.employee_name} row={row} />)}
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

function ProjectDrawer({ project, close, refresh }: { project: ProjectDetail; close: () => void; refresh: () => Promise<void> }) {
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [confirmedBy, setConfirmedBy] = useState(project.project_manager ?? project.owner ?? "");
  const [settings, setSettings] = useState({
    contract_type: project.contract_type,
    lifecycle_status: project.lifecycle_status,
    planned_person_days: project.planned_person_days ?? "",
    hours_per_person_day: project.settings.hours_per_person_day ?? "8.00",
    project_manager: project.project_manager ?? ""
  });
  const [milestone, setMilestone] = useState({ name: "", planned_start: "", planned_end: "", owner: project.project_manager ?? "", deliverable: "", is_payment_node: false, payment_date: "", payment_amount: "" });
  const [editingMilestoneId, setEditingMilestoneId] = useState<string | null>(null);
  const [milestoneFormOpen, setMilestoneFormOpen] = useState(false);
  const [expandedMonth, setExpandedMonth] = useState(project.monthly_time_entries[0]?.month ?? "");
  const [configEditing, setConfigEditing] = useState(false);
  const [aiAnalysis, setAiAnalysis] = useState<{ answer: string; sources: string[] } | null>(null);
  const [analyzing, setAnalyzing] = useState(false);
  const [analysisError, setAnalysisError] = useState<string | null>(null);

  useEffect(() => {
    setSettings({ contract_type: project.contract_type, lifecycle_status: project.lifecycle_status, planned_person_days: project.planned_person_days ?? "", hours_per_person_day: project.settings.hours_per_person_day ?? "8.00", project_manager: project.project_manager ?? "" });
    setExpandedMonth(project.monthly_time_entries[0]?.month ?? "");
    setConfigEditing(false);
    setMilestoneFormOpen(false);
    setEditingMilestoneId(null);
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
      if (editingMilestoneId) await updateMilestone(project.project_number, editingMilestoneId, milestone);
      else await createMilestone(project.project_number, milestone);
      resetMilestoneForm();
    }, editingMilestoneId ? "里程碑修改已保存" : "里程碑已加入待办");
  }

  function resetMilestoneForm() {
    setEditingMilestoneId(null);
    setMilestone({ name: "", planned_start: "", planned_end: "", owner: settings.project_manager, deliverable: "", is_payment_node: false, payment_date: "", payment_amount: "" });
    setMilestoneFormOpen(false);
  }

  function openNewMilestoneForm() {
    setEditingMilestoneId(null);
    setMilestone({ name: "", planned_start: "", planned_end: "", owner: settings.project_manager, deliverable: "", is_payment_node: false, payment_date: "", payment_amount: "" });
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
      payment_amount: item.payment_amount ?? ""
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

  return <div className="drawer-backdrop" onClick={close}><aside className="project-drawer" onClick={e => e.stopPropagation()}>
    <button className="drawer-close" onClick={close}>×</button>
    <p className="eyebrow">{project.project_number}</p><h2>{displayProjectName(project.project_number, project.subject)}</h2>
    <div className="drawer-badges"><span>{contractTypeLabel(project.contract_type)}</span><span>{lifecycleLabel(project.lifecycle_status)}</span><span className={`risk ${project.risk_level}`}>{riskLabel(project.risk_level)}</span></div>
    {actionAlerts.length > 0 && <section className="alert-stack" aria-label="项目待处理事项"><div className="alert-stack-head"><div className="alert-stack-icon" aria-hidden="true">!</div><div><h3>项目待处理事项</h3><small>按优先级处理风险和人工维护事项</small></div><span className="alert-stack-count">{actionAlerts.length} 项</span><button type="button" className="ai-analysis-button" disabled={analyzing} onClick={() => void requestAiAnalysis()}>{analyzing ? "正在分析…" : aiAnalysis ? "重新分析" : "AI 分析"}</button></div><div className="alert-stack-list">{actionAlerts.map((alert, index) => <article className={`anomaly-alert ${alert.severity}`} key={`${alert.code}-${index}`}><span className="anomaly-alert-icon" aria-hidden="true">{alert.severity === "critical" ? "!" : "◷"}</span><div className="anomaly-alert-content"><div><strong>{alert.title}</strong><span>{alert.severity === "critical" ? "严重" : "关注"}</span></div><p>{alert.detail}</p></div></article>)}</div>{aiAnalysis && <article className="ai-analysis-result"><header><div><span>AI 项目研判</span><small>基于当前 CRM、工时、里程碑与回款资料</small></div><b>已生成</b></header><p>{aiAnalysis.answer}</p><footer>{aiAnalysis.sources.map(source => <small key={source}>{source}</small>)}</footer></article>}{analysisError && <p className="ai-analysis-error">{analysisError}</p>}</section>}
    {message && <div className="inline-message">{message}</div>}
    <section className="detail-section source-section"><div className="section-title"><div><h3>项目主数据库汇总 <span className="source-tag">系统接口 · 只读</span></h3><small>合同金额取 CRM 导入模板；工时与人工维护字段分别独立汇总</small></div></div><div className="detail-metrics six"><article><span>合同金额（CRM）</span><b>{money(project.contract_value)}</b></article><article><span>已开票（CRM）</span><b>{money(project.invoiced_amount)}</b></article><article><span>已回款（CRM）</span><b>{money(project.crm_received_amount)}</b></article><article><span>预算人天</span><b>{project.planned_person_days ? `${project.planned_person_days} 人天` : "未设置"}</b></article><article><span>累计实际人天</span><b>{project.actual_person_days} 人天</b></article><article className={Number(project.confirmation_gap_person_days) > 0 ? "metric-alert" : ""}><span>待客户确认缺口</span><b>{project.confirmation_gap_person_days} 人天</b></article></div></section>

    <section className="detail-section"><div className="section-title"><div><h3>月度工时清单 <span className="source-tag">系统接口 · 只读</span></h3><small>点击月份展开员工填报明细</small></div></div>
      <div className="month-list">{project.monthly_time_entries.map(month => <article className="month-card" key={month.month}><button type="button" onClick={() => setExpandedMonth(expandedMonth === month.month ? "" : month.month)}><strong>{month.month}</strong><span>{month.entry_count} 条 · 填报 {month.reported_person_days} 人天{Number(month.overtime_person_days) > 0 && `（含加班 ${month.overtime_person_days} 人天）`} · 确认 {month.approved_person_days} 人天</span><b>{expandedMonth === month.month ? "收起" : "展开"}</b></button>{expandedMonth === month.month && <div className="month-entries">{month.entries.map(item => <div className="detail-row compact" key={item.external_id}><div><strong>{item.employee_name} · 填报 {timeEntryPersonDays(item)} 人天{Number(item.overtime_hours) > 0 && `（加班 ${hoursToDays(item.overtime_hours)} 人天）`} / 确认 {hoursToDays(item.approved_hours)} 人天</strong><small>{item.work_content}</small></div><time>{item.work_date}</time></div>)}</div>}</article>)}</div>
      {project.monthly_time_entries.length === 0 && <p className="detail-empty">暂无月度工时数据</p>}
    </section>

    <PmoResourceTable project={project} />

    <section className="detail-section todo-section"><div className="todo-section-head"><div><p className="todo-section-kicker">TO-DO</p><h3>待办处理区</h3><small>按当前日期自动计算；请在完成后确认，逾期事项会持续提示</small></div><label>当前确认人<input value={confirmedBy} onChange={e => setConfirmedBy(e.target.value)} placeholder="管理人员姓名" /></label></div>
      <div className="todo-grid">{project.todo_events.filter(item => !item.completed).map(item => {
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
      {project.todo_events.every(item => item.completed) && <p className="detail-empty">当前没有待办事件</p>}
    </section>

    <section className="detail-section"><div className="section-title"><div><h3>项目基础配置 <span className="manual-tag">人工维护</span></h3><small>同步不会覆盖以下字段</small></div><button type="button" className="button secondary" disabled={saving} onClick={() => setConfigEditing(!configEditing)}>{configEditing ? "取消编辑" : "编辑"}</button></div>
      {!configEditing ? <div className="config-summary">
        <article><span>合同类别</span><strong>{contractTypeLabel(project.contract_type)}</strong></article>
        <article><span>生命周期</span><strong>{lifecycleLabel(project.lifecycle_status)}</strong></article>
        <article><span>初始预算人天</span><strong>{project.planned_person_days ? `${project.planned_person_days} 人天` : project.contract_type === "open_ended" ? "无上限" : "未设置"}</strong></article>
        <article><span>每人天小时</span><strong>{project.settings.hours_per_person_day ?? "8.00"} h</strong></article>
        <article className="span-2"><span>项目经理</span><strong>{project.project_manager || "未设置"}</strong></article>
      </div> : <form className="config-form" onSubmit={saveSettings}>
        <label>合同类别<select value={settings.contract_type} onChange={e => setSettings({ ...settings, contract_type: e.target.value as ProjectDetail["contract_type"] })}><option value="unknown">请选择</option><option value="construction">建设类</option><option value="maintenance">维护类</option><option value="open_ended">开口类（无上限）</option><option value="sales">销售类</option></select></label>
        <label>生命周期<select value={settings.lifecycle_status} onChange={e => setSettings({ ...settings, lifecycle_status: e.target.value as ProjectDetail["lifecycle_status"] })}><option value="planned">计划中</option><option value="active">执行中</option><option value="on_hold">已暂停</option><option value="completed">已完成</option><option value="closed">已关闭</option></select></label>
        <label>初始预算人天<input type="number" min="0" step="0.01" disabled={settings.contract_type === "open_ended"} required={["construction", "maintenance"].includes(settings.contract_type)} value={settings.contract_type === "open_ended" ? "" : settings.planned_person_days} placeholder={settings.contract_type === "open_ended" ? "无上限，无需填写" : "例如 120"} onChange={e => setSettings({ ...settings, planned_person_days: e.target.value })} /></label>
        <label>每人天小时<input type="number" min="0.1" step="0.1" required value={settings.hours_per_person_day} onChange={e => setSettings({ ...settings, hours_per_person_day: e.target.value })} /></label>
        <label className="span-2">项目经理<input required value={settings.project_manager} onChange={e => setSettings({ ...settings, project_manager: e.target.value })} /></label>
        <div className="form-actions span-2"><button className="button primary" disabled={saving}>保存基础配置</button><button type="button" className="button secondary" disabled={saving} onClick={() => { setSettings({ contract_type: project.contract_type, lifecycle_status: project.lifecycle_status, planned_person_days: project.planned_person_days ?? "", hours_per_person_day: project.settings.hours_per_person_day ?? "8.00", project_manager: project.project_manager ?? "" }); setConfigEditing(false); }}>取消</button></div>
      </form>}
    </section>

    <section className="detail-section"><div className="section-title"><div><h3>里程碑计划 <span className="manual-tag">人工维护</span></h3><small>项目里程碑与商务回款节点统一维护，同步不会覆盖</small></div>{!milestoneFormOpen && <button type="button" className="button secondary add-action" disabled={saving} onClick={openNewMilestoneForm}>＋ 添加</button>}</div>
      <div className="milestone-list">{project.milestones.map(item => <article key={item.milestone_id} className={`milestone-item ${item.deadline.urgency} ${editingMilestoneId === item.milestone_id ? "editing" : ""}`}><span className="sequence">{item.sequence_no ?? "—"}</span><div><strong>{item.name}</strong><small>{item.planned_start} 至 {item.planned_end} · {item.deadline.label}</small><small>输出物：{item.deliverable || "未填写"}{item.is_payment_node ? ` · 回款 ${item.payment_date} ${money(item.payment_amount)}` : ""}</small></div><div className="milestone-actions"><span className={`mini-state ${item.status}`}>{item.status === "completed" ? "已完成" : "待确认"}</span>{item.confirmed_by && <small>{item.confirmed_by}</small>}{item.status !== "completed" && <div className="milestone-action-buttons"><button type="button" onClick={() => editMilestone(item)}>编辑</button><button type="button" className="danger-action" disabled={saving} onClick={() => removeMilestone(item)}>删除</button></div>}</div></article>)}</div>
      {milestoneFormOpen && <>{editingMilestoneId ? <div className="editing-banner"><strong>编辑待确认里程碑</strong><span>保存后将重新计算待办日期和回款提醒</span></div> : <div className="editing-banner"><strong>添加里程碑</strong><span>填写后会生成里程碑待办；勾选回款节点会同步生成回款待办</span></div>}
      <form className="milestone-form" onSubmit={addMilestone}>
        <label>里程碑名称<input required value={milestone.name} onChange={e => setMilestone({ ...milestone, name: e.target.value })} /></label>
        <label>负责人<input value={milestone.owner} onChange={e => setMilestone({ ...milestone, owner: e.target.value })} /></label>
        <label>开始日期<input type="date" required value={milestone.planned_start} onChange={e => setMilestone({ ...milestone, planned_start: e.target.value })} /></label>
        <label>结束日期<input type="date" required value={milestone.planned_end} onChange={e => setMilestone({ ...milestone, planned_end: e.target.value })} /></label>
        <label className="span-2">输出物<input value={milestone.deliverable} onChange={e => setMilestone({ ...milestone, deliverable: e.target.value })} placeholder="如：需求说明书、软件设计方案" /></label>
        <label className="check-label span-2"><input type="checkbox" checked={milestone.is_payment_node} onChange={e => setMilestone({ ...milestone, is_payment_node: e.target.checked })} />同时设为商务回款节点</label>
        {milestone.is_payment_node && <><label>回款日期<input type="date" required value={milestone.payment_date} onChange={e => setMilestone({ ...milestone, payment_date: e.target.value })} /></label><label>回款金额（元）<input type="number" min="0" step="0.01" required value={milestone.payment_amount} onChange={e => setMilestone({ ...milestone, payment_amount: e.target.value })} /></label></>}
        <div className="form-actions span-2"><button className="button primary" disabled={saving}>{editingMilestoneId ? "保存修改" : "添加里程碑"}</button><button type="button" className="button secondary" disabled={saving} onClick={resetMilestoneForm}>取消</button></div>
      </form></>}
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
      <p>系统只更新 CRM 主数据字段；合同类别、预算人天、项目经理、里程碑、回款确认等人工维护信息始终保留。当前可上传 Excel、CSV 或 JSON；待你确认首版模板后，可将字段别名锁定为固定模板。</p>
    </div>
  </section>;
}

function ImportCard(props: { dataType: "contract_order" | "sales_opportunity"; importing: "contract_order" | "sales_opportunity" | null; upload: (dataType: "contract_order" | "sales_opportunity", event: ChangeEvent<HTMLInputElement>) => Promise<void> }) {
  const isContract = props.dataType === "contract_order";
  const active = props.importing === props.dataType;
  return <article className={`import-card ${props.dataType}`}>
    <span className="import-icon">{isContract ? "▤" : "◎"}</span>
    <div><h3>{isContract ? "合同 / 订单数据" : "销售机会数据"}</h3><p>{isContract ? "主题、客户、金额、签约时间、所有者" : "机会主题、负责人、可能性、状态"}</p></div>
    <label className={`button ${isContract ? "primary" : "secondary"} ${active ? "disabled" : ""}`}>{active ? "导入中…" : "选择文件上传"}<input type="file" accept=".xlsx,.csv,.json" hidden disabled={props.importing !== null} onChange={event => void props.upload(props.dataType, event)} /></label>
  </article>;
}

function CrmRecords(props: { records: RecordsResponse | null; loading: boolean; queryInput: string; setQueryInput: (value: string) => void; submitSearch: (event: FormEvent) => void; dataType: string; setDataType: (value: string) => void; status: string; setStatus: (value: string) => void; page: number; setPage: React.Dispatch<React.SetStateAction<number>>; }) {
  return <section className="panel"><div className="panel-head"><div><h2>CRM 数据记录</h2><p>展示全部成功解析并入库的合同与销售机会；项目主数据库另按项目编号汇总</p></div><Search value={props.queryInput} setValue={props.setQueryInput} submit={props.submitSearch} /></div><div className="filters"><select value={props.dataType} onChange={e => { props.setDataType(e.target.value); props.setPage(1); }}><option value="">全部数据类型</option><option value="contract_order">合同 / 订单</option><option value="sales_opportunity">销售机会</option></select><select value={props.status} onChange={e => { props.setStatus(e.target.value); props.setPage(1); }}><option value="">全部状态</option><option value="已完成">合同已完成</option><option value="未完成">合同未完成</option><option value="成功">商机成功</option><option value="跟踪">商机跟踪</option></select><span className="result-count">共 {props.records?.meta.total_records ?? 0} 条成功入库记录</span></div><div className="table-wrap"><table><thead><tr><th>类型</th><th>项目编号</th><th>主题</th><th>签约时间</th><th>客户 / 分类</th><th>所有者</th><th>总金额</th><th>回款金额</th><th>开票金额</th><th>可能性</th><th>状态</th></tr></thead><tbody>{props.loading ? <LoadingRows colSpan={11} /> : props.records?.data.map(record => <RecordRow key={record.record_id} record={record} />)}</tbody></table></div><div className="pagination"><button disabled={props.page <= 1} onClick={() => props.setPage(value => value - 1)}>上一页</button><span>第 {props.records?.meta.page ?? props.page} / {props.records?.meta.total_pages || 1} 页</span><button disabled={!props.records || props.page >= props.records.meta.total_pages} onClick={() => props.setPage(value => value + 1)}>下一页</button></div></section>;
}

function Search({ value, setValue, submit, placeholder = "搜索主题、客户或项目编号" }: { value: string; setValue: (value: string) => void; submit: (event: FormEvent) => void; placeholder?: string }) { return <form className="search" onSubmit={submit}><input value={value} onChange={e => setValue(e.target.value)} placeholder={placeholder} /><button type="submit">搜索</button></form>; }
function RecordRow({ record }: { record: CrmRecord }) { return <tr><td><span className={`type-pill ${record.data_type}`}>{record.data_type === "contract_order" ? "合同/订单" : "销售机会"}</span></td><td><strong className="project-code">{record.project_number ?? "—"}</strong></td><td><div className="subject">{record.subject}</div></td><td>{record.signed_at ?? "—"}</td><td><div>{record.customer ?? "—"}</div><small>{record.category ?? ""}</small></td><td>{record.owner}</td><td className="money">{record.total_amount?.display ?? "—"}</td><td className="money">{record.received_amount?.display ?? "—"}</td><td className="money">{record.invoiced_amount?.display ?? "—"}</td><td>{record.probability == null ? "—" : `${record.probability}%`}</td><td><span className={`state ${record.status === "成功" || record.status === "已完成" ? "success" : record.status === "未完成" ? "unfinished" : "tracking"}`}>{record.status ?? "—"}</span></td></tr>; }
function LoadingRows({ colSpan = 10 }: { colSpan?: number }) { return <>{Array.from({ length: 5 }, (_, index) => <tr className="skeleton" key={index}><td colSpan={colSpan}><span /></td></tr>)}</>; }
function money(value?: string | null) { if (value == null) return "—"; return new Intl.NumberFormat("zh-CN", { style: "currency", currency: "CNY", maximumFractionDigits: 0 }).format(Number(value)); }
function formatImportTime(value: string) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : new Intl.DateTimeFormat("zh-CN", {
    year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false
  }).format(date).replace(/\//g, "-");
}
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
function contractTypeLabel(value: ProjectSummary["contract_type"]) { return ({ construction: "建设类", maintenance: "维护类", open_ended: "开口类", sales: "销售类", unknown: "未分类" } as const)[value]; }
function lifecycleLabel(value: ProjectSummary["lifecycle_status"]) { return ({ planned: "计划中", active: "执行中", on_hold: "已暂停", completed: "已完成", closed: "已关闭" } as const)[value]; }
function riskLabel(value: ProjectSummary["risk_level"]) { return ({ healthy: "健康", warning: "关注", critical: "严重" } as const)[value]; }
function healthClass(value: string) { if (value === "已超预算" || value === "高风险") return "critical"; if (value === "需关注" || value === "待维护预算") return "warning"; if (value === "无基线") return "baseline"; return "healthy"; }
function employeeStatusClass(value: string) { if (value === "工时充足") return "healthy"; if (value === "基本达标") return "baseline"; if (value === "需要关注") return "warning"; return "critical"; }
function statusLabel(value?: SyncStatus["status"]) { return ({ succeeded: "数据可用", running: "处理中", failed: "导入失败", auth_required: "需要登录", never_run: "尚未导入" } as const)[value ?? "never_run"]; }
function syncModeLabel(sync: SyncStatus | null) {
  if (sync?.validation.import_kind === "manual_upload") return `手工导入 · 新增 ${sync.validation.inserted_records ?? 0} / 更新 ${sync.validation.updated_records ?? 0}`;
  if (sync?.validation.sync_mode === "incremental") return `历史导入 · 新增 ${sync.validation.inserted_records ?? 0} / 更新 ${sync.validation.updated_records ?? 0}`;
  if (sync?.validation.sync_mode === "full") return "初始数据已导入";
  return "等待手工上传";
}
