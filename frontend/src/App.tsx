import { ChangeEvent, FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import {
  confirmMilestone,
  confirmPayment,
  createMilestone,
  deleteMilestone,
  deletePaymentNode,
  downloadExport,
  fetchEmployeeWorktime,
  fetchProject,
  fetchProjectHealth,
  fetchProjects,
  fetchRecords,
  fetchSyncStatus,
  fetchWorktimeBaseline,
  importTimeEntriesXlsx,
  triggerSync,
  updateMilestone,
  updatePaymentNode,
  updateProjectSettings,
  updateWorktimeBaseline
} from "./api";
import type {
  CrmRecord,
  EmployeeWorktimeResponse,
  EmployeeWorktimeRow,
  PortfolioResponse,
  ProjectDetail,
  ProjectHealthResponse,
  ProjectHealthRow,
  ProjectSummary,
  RecordsResponse,
  SyncStatus,
  WorktimeBaseline
} from "./types";

const PAGE_SIZE = 20;
type View = "projects" | "health" | "employeeWorktime" | "crm" | "sync";

export function App() {
  const [view, setView] = useState<View>("projects");
  const [portfolio, setPortfolio] = useState<PortfolioResponse | null>(null);
  const [health, setHealth] = useState<ProjectHealthResponse | null>(null);
  const [employeeWorktime, setEmployeeWorktime] = useState<EmployeeWorktimeResponse | null>(null);
  const [worktimeBaseline, setWorktimeBaseline] = useState<WorktimeBaseline | null>(null);
  const [records, setRecords] = useState<RecordsResponse | null>(null);
  const [sync, setSync] = useState<SyncStatus | null>(null);
  const [selected, setSelected] = useState<ProjectDetail | null>(null);
  const [queryInput, setQueryInput] = useState("");
  const [query, setQuery] = useState("");
  const [contractType, setContractType] = useState("");
  const [riskLevel, setRiskLevel] = useState("");
  const [healthMode, setHealthMode] = useState<"month" | "range">("month");
  const [healthMonth, setHealthMonth] = useState(new Date().toISOString().slice(0, 7));
  const [healthStartMonth, setHealthStartMonth] = useState("");
  const [healthEndMonth, setHealthEndMonth] = useState("");
  const [worktimePeriod, setWorktimePeriod] = useState<"month" | "year">("month");
  const [worktimeMonth, setWorktimeMonth] = useState(new Date().toISOString().slice(0, 7));
  const [worktimeYear, setWorktimeYear] = useState(new Date().toISOString().slice(0, 4));
  const [dataType, setDataType] = useState("");
  const [status, setStatus] = useState("");
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [syncing, setSyncing] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const syncResult = await fetchSyncStatus();
      setSync(syncResult);
      if (view === "projects") {
        setPortfolio(await fetchProjects({ query, contractType, riskLevel }));
      } else if (view === "health") {
        setHealth(await fetchProjectHealth({
          month: healthMode === "month" ? healthMonth : undefined,
          startMonth: healthMode === "range" ? healthStartMonth : undefined,
          endMonth: healthMode === "range" ? healthEndMonth : undefined,
          projectNumber: query
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
      } else {
        setRecords(await fetchRecords({ dataType, query, status, page, pageSize: PAGE_SIZE }));
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "数据加载失败");
    } finally {
      setLoading(false);
    }
  }, [view, query, contractType, riskLevel, dataType, status, page, healthMode, healthMonth, healthStartMonth, healthEndMonth, worktimePeriod, worktimeMonth, worktimeYear]);

  useEffect(() => { void load(); }, [load]);

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
  ] : view === "sync" ? [
    { label: "同步状态", value: statusLabel(sync?.status), tone: sync?.status === "succeeded" ? "green" : "amber" },
    { label: "合同 / 订单", value: sync?.output_stats.contract_order ?? "—", tone: "blue" },
    { label: "销售机会", value: sync?.output_stats.sales_opportunity ?? "—", tone: "amber" },
    { label: "已合并机会", value: sync?.validation.merged_opportunities ?? "—", tone: "violet" }
  ] : [
    { label: "有效记录", value: records?.meta.total_records ?? "—", tone: "blue" },
    { label: "合同 / 订单", value: sync?.output_stats.contract_order ?? "—", tone: "green" },
    { label: "销售机会", value: sync?.output_stats.sales_opportunity ?? "—", tone: "amber" },
    { label: "已合并机会", value: sync?.validation.merged_opportunities ?? "—", tone: "violet" }
  ], [view, portfolio, health, employeeWorktime, records, sync]);

  function switchView(next: View) {
    setView(next); setQuery(""); setQueryInput(""); setPage(1); setSelected(null);
  }

  function submitSearch(event: FormEvent) {
    event.preventDefault(); setPage(1); setQuery(queryInput.trim());
  }

  async function openProject(projectNumber: string) {
    setError(null);
    try { setSelected(await fetchProject(projectNumber)); }
    catch (err) { setError(err instanceof Error ? err.message : "项目加载失败"); }
  }

  async function startSync() {
    setSyncing(true); setError(null);
    try { await triggerSync(); window.setTimeout(() => void load(), 800); }
    catch (err) { setError(err instanceof Error ? err.message : "同步任务创建失败"); }
    finally { setSyncing(false); }
  }

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand"><span className="brand-mark">PM</span><span>TSG PM</span></div>
        <nav>
          <button className={`nav-item ${view === "projects" ? "active" : ""}`} onClick={() => switchView("projects")}><span>◫</span> 项目驾驶舱</button>
          <button className={`nav-item ${view === "health" ? "active" : ""}`} onClick={() => switchView("health")}><span>▣</span> 项目健康度</button>
          <button className={`nav-item ${view === "employeeWorktime" ? "active" : ""}`} onClick={() => switchView("employeeWorktime")}><span>◷</span> 员工工时评估</button>
          <button className={`nav-item ${view === "crm" ? "active" : ""}`} onClick={() => switchView("crm")}><span>⌘</span> CRM 数据</button>
          <button className={`nav-item ${view === "sync" ? "active" : ""}`} onClick={() => switchView("sync")}><span>↻</span> 同步状态</button>
          <a className="nav-item" href="/docs" target="_blank"><span>⌁</span> API 文档</a>
        </nav>
        <div className="sidebar-foot">
          <span className={`status-dot ${sync?.status === "succeeded" ? "ok" : "warn"}`} />
          <div><strong>{statusLabel(sync?.status)}</strong><small>{syncModeLabel(sync)} · 每日 03:00</small></div>
        </div>
      </aside>

      <main>
        <header className="topbar">
          <div><p className="eyebrow">PROJECT LIFECYCLE CONTROL</p><h1>{view === "projects" ? "项目组合与生命周期" : view === "health" ? "项目健康度监控" : view === "employeeWorktime" ? "员工工时状况评估" : view === "sync" ? "数据同步状态" : "CRM 合同与销售机会"}</h1></div>
          <div className="top-actions">
            <button className="button secondary" onClick={() => void downloadExport("csv")}>导出 CSV</button>
            <button className="button primary" disabled={syncing} onClick={startSync}>{syncing ? "正在创建…" : "立即同步"}</button>
          </div>
        </header>
        {error && <div className="error-banner">{error}</div>}
        <section className="metrics">{cards.map(card => <article className={`metric ${card.tone}`} key={card.label}><span>{card.label}</span><strong>{card.value}</strong></article>)}</section>

        {view === "projects" ? (
          <ProjectPortfolio
            portfolio={portfolio} loading={loading} queryInput={queryInput}
            setQueryInput={setQueryInput} submitSearch={submitSearch}
            contractType={contractType} setContractType={setContractType}
            riskLevel={riskLevel} setRiskLevel={setRiskLevel} openProject={openProject}
          />
        ) : view === "health" ? (
          <ProjectHealth
            health={health} loading={loading} queryInput={queryInput}
            setQueryInput={setQueryInput} submitSearch={submitSearch}
            mode={healthMode} setMode={setHealthMode}
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
            month={worktimeMonth} setMonth={setWorktimeMonth}
            year={worktimeYear} setYear={setWorktimeYear}
            refresh={load}
          />
        ) : view === "sync" ? (
          <SyncStatusPanel sync={sync} syncing={syncing} startSync={startSync} refresh={load} />
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
          setPortfolio(await fetchProjects({ query, contractType, riskLevel }));
        }}
      />}
    </div>
  );
}

function ProjectPortfolio(props: {
  portfolio: PortfolioResponse | null; loading: boolean; queryInput: string;
  setQueryInput: (value: string) => void; submitSearch: (event: FormEvent) => void;
  contractType: string; setContractType: (value: string) => void;
  riskLevel: string; setRiskLevel: (value: string) => void;
  openProject: (projectNumber: string) => void;
}) {
  return <section className="panel">
    <div className="panel-head"><div><h2>项目主数据库</h2><p>按项目代码汇总 CRM 主数据、员工工时、里程碑与回款状态</p></div><Search value={props.queryInput} setValue={props.setQueryInput} submit={props.submitSearch} /></div>
    <div className="data-boundary"><span className="source-tag">系统接口数据</span><p>合同、商机、金额和工时由接口增量同步，只读</p><span className="manual-tag">人工维护数据</span><p>合同类别、预算人天、里程碑和确认状态由管理人员维护</p></div>
    <div className="filters">
      <select value={props.contractType} onChange={e => props.setContractType(e.target.value)}><option value="">全部合同类型</option><option value="construction">建设类</option><option value="maintenance">维护类</option><option value="open_ended">开口类</option><option value="sales">销售类</option><option value="unknown">未分类</option></select>
      <select value={props.riskLevel} onChange={e => props.setRiskLevel(e.target.value)}><option value="">全部风险</option><option value="critical">严重</option><option value="warning">关注</option><option value="healthy">健康</option></select>
      <span className="result-count">共 {props.portfolio?.summary.total_projects ?? 0} 个项目</span>
    </div>
    <div className="table-wrap"><table><thead><tr><th>风险</th><th>项目代码</th><th>项目名称</th><th>类别</th><th>状态</th><th>里程碑</th><th>项目类型</th><th>回款</th><th>预算人天</th><th>累计实际人天</th></tr></thead><tbody>
      {props.loading ? <LoadingRows /> : props.portfolio?.data.map(project => <ProjectRow key={project.project_number} project={project} open={() => props.openProject(project.project_number)} />)}
      {!props.loading && props.portfolio?.data.length === 0 && <tr><td className="empty" colSpan={10}>没有符合条件的项目</td></tr>}
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

function ProjectHealth(props: {
  health: ProjectHealthResponse | null; loading: boolean; queryInput: string;
  setQueryInput: (value: string) => void; submitSearch: (event: FormEvent) => void;
  mode: "month" | "range"; setMode: (value: "month" | "range") => void;
  month: string; setMonth: (value: string) => void;
  startMonth: string; setStartMonth: (value: string) => void;
  endMonth: string; setEndMonth: (value: string) => void;
  openProject: (projectNumber: string) => void;
}) {
  return <section className="panel">
    <div className="panel-head"><div><h2>项目健康度监控</h2><p>按月份或时间范围监控预算燃尽、超支风险、月度趋势和剩余预算</p></div><Search value={props.queryInput} setValue={props.setQueryInput} submit={props.submitSearch} /></div>
    <div className="filters health-filters">
      <select value={props.mode} onChange={e => props.setMode(e.target.value as "month" | "range")}><option value="month">按单月</option><option value="range">按时间范围</option></select>
      {props.mode === "month" ? <label>统计月份<input type="month" value={props.month} onChange={e => props.setMonth(e.target.value)} /></label> : <><label>开始月份<input type="month" value={props.startMonth} onChange={e => props.setStartMonth(e.target.value)} /></label><label>结束月份<input type="month" value={props.endMonth} onChange={e => props.setEndMonth(e.target.value)} /></label></>}
      <span className="result-count">统计范围：{props.health?.meta.start_month ?? "—"} 至 {props.health?.meta.end_month ?? "—"} · 共 {props.health?.summary.total_projects ?? 0} 个项目</span>
    </div>
    <div className="table-wrap"><table><thead><tr><th>项目代码</th><th>项目名称</th><th>PM负责人</th><th>项目状态</th><th>计划开始</th><th>计划结束</th><th>预算人天</th><th>计划月数</th><th>月均预算(人天)</th><th>累计实际(人天)</th><th>本期投入(人天)</th><th>上月投入(人天)</th><th>参与人数</th><th>燃尽率</th><th>超支预警</th><th>月度趋势</th><th>综合健康状态</th><th>预计完工</th><th>剩余预算(人天)</th></tr></thead><tbody>
      {props.loading ? <LoadingRows colSpan={19} /> : props.health?.data.map(row => <ProjectHealthRowView key={row.project_number} row={row} open={() => props.openProject(row.project_number)} />)}
      {!props.loading && props.health?.data.length === 0 && <tr><td className="empty" colSpan={19}>没有符合条件的健康度数据</td></tr>}
    </tbody></table></div>
  </section>;
}

function ProjectHealthRowView({ row, open }: { row: ProjectHealthRow; open: () => void }) {
  return <tr className="clickable" onClick={open}>
    <td><strong className="project-code">{row.project_number}</strong></td>
    <td><div className="subject">{displayProjectName(row.project_number, row.project_name)}</div>{row.risk_reasons.length > 0 && <small className="danger-text">{row.risk_reasons[0]}</small>}</td>
    <td>{row.pm_owner ?? "请添加PM负责人"}</td>
    <td>{lifecycleLabel(row.project_status)}</td>
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
    setImporting(true); setMessage(null);
    try {
      const result = await importTimeEntriesXlsx(file);
      await props.refresh();
      setMessage(`已导入 ${result.imported} 条工时记录`);
    } catch (err) {
      setMessage(err instanceof Error ? err.message : "导入失败");
    } finally {
      setImporting(false);
      event.target.value = "";
    }
  }

  return <section className="panel">
    <div className="panel-head"><div><h2>员工工时状况评估</h2><p>按月度或年度汇总员工填报工时，并按人工维护的基线参数计算利用率</p></div><div className="panel-actions"><label className={`button secondary ${importing ? "disabled" : ""}`}>{importing ? "导入中…" : "导入工时 Excel"}<input type="file" accept=".xlsx" onChange={uploadTimesheet} disabled={importing} hidden /></label><Search value={props.queryInput} setValue={props.setQueryInput} submit={props.submitSearch} /></div></div>
    <div className="filters health-filters">
      <select value={props.period} onChange={e => props.setPeriod(e.target.value as "month" | "year")}><option value="month">按月统计</option><option value="year">年度汇总</option></select>
      {props.period === "month" ? <label>统计月份<input type="month" value={props.month} onChange={e => props.setMonth(e.target.value)} /></label> : <label>统计年度<input type="number" min="2000" max="2100" value={props.year} onChange={e => props.setYear(e.target.value)} /></label>}
      <span className="result-count">统计范围：{props.assessment?.meta.start_date ?? "—"} 至 {props.assessment?.meta.end_date ?? "—"} · 基线 {hoursToDays(props.assessment?.meta.baseline_hours)} 人天</span>
    </div>
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
    <div className="table-wrap"><table><thead><tr><th>姓名</th><th>填报人天</th><th>确认人天</th><th>确认缺口(人天)</th><th>基线(人天)</th><th>工时利用率</th><th>项目数</th><th>记录数</th><th>工时利用率评价</th></tr></thead><tbody>
      {props.loading ? <LoadingRows colSpan={9} /> : props.assessment?.data.map(row => <EmployeeWorktimeRowView key={row.employee_name} row={row} />)}
      {!props.loading && props.assessment?.data.length === 0 && <tr><td className="empty" colSpan={9}>暂无员工工时评估数据</td></tr>}
    </tbody></table></div>
  </section>;
}

function EmployeeWorktimeRowView({ row }: { row: EmployeeWorktimeRow }) {
  return <tr>
    <td><strong className="project-code">{row.employee_name}</strong></td>
    <td>{row.reported_person_days}</td>
    <td>{row.approved_person_days}</td>
    <td className={Number(row.confirmation_gap_hours) > 0 ? "danger-text" : ""}>{hoursToDays(row.confirmation_gap_hours)}</td>
    <td>{hoursToDays(row.baseline_hours)}</td>
    <td><strong>{row.utilization_percent}%</strong></td>
    <td>{row.project_count}</td>
    <td>{row.entry_count}</td>
    <td><span className={`health-tag ${employeeStatusClass(row.status)}`}>{row.status}</span></td>
  </tr>;
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

  useEffect(() => {
    setSettings({ contract_type: project.contract_type, lifecycle_status: project.lifecycle_status, planned_person_days: project.planned_person_days ?? "", hours_per_person_day: project.settings.hours_per_person_day ?? "8.00", project_manager: project.project_manager ?? "" });
    setExpandedMonth(project.monthly_time_entries[0]?.month ?? "");
    setConfigEditing(false);
    setMilestoneFormOpen(false);
    setEditingMilestoneId(null);
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

  return <div className="drawer-backdrop" onClick={close}><aside className="project-drawer" onClick={e => e.stopPropagation()}>
    <button className="drawer-close" onClick={close}>×</button>
    <p className="eyebrow">{project.project_number}</p><h2>{displayProjectName(project.project_number, project.subject)}</h2>
    <div className="drawer-badges"><span>{contractTypeLabel(project.contract_type)}</span><span>{lifecycleLabel(project.lifecycle_status)}</span><span className={`risk ${project.risk_level}`}>{riskLabel(project.risk_level)}</span></div>
    {!project.maintenance_complete && <section className="maintenance-box"><strong>同步后待人工维护</strong><p>{(project.maintenance_reasons ?? []).join("、")}</p></section>}
    {project.risk_reasons.length > 0 && <section className="risk-box"><strong>管理关注</strong>{project.risk_reasons.map(reason => <p key={reason}>• {reason}</p>)}</section>}
    {(project.anomaly_alerts ?? []).length > 0 && <section className="alert-stack"><div className="section-title"><div><h3>异常与临期提醒</h3><small>里程碑、工时预算和客户确认缺口</small></div></div>{project.anomaly_alerts.map((alert, index) => <article className={`anomaly-alert ${alert.severity}`} key={`${alert.code}-${index}`}><div><strong>{alert.title}</strong><span>{alert.severity === "critical" ? "严重" : "提醒"}</span></div><p>{alert.detail}</p></article>)}</section>}
    {message && <div className="inline-message">{message}</div>}
    <section className="detail-section source-section"><div className="section-title"><div><h3>项目主数据库汇总 <span className="source-tag">系统接口 · 只读</span></h3><small>由 CRM 主数据、员工工时、里程碑和人工维护字段汇总生成</small></div></div><div className="detail-metrics six"><article><span>合同金额</span><b>{money(project.contract_value)}</b></article><article><span>已开票</span><b>{money(project.invoiced_amount)}</b></article><article><span>已回款</span><b>{money(project.received_amount)}</b></article><article><span>预算人天</span><b>{project.planned_person_days ? `${project.planned_person_days} 人天` : "未设置"}</b></article><article><span>累计实际人天</span><b>{project.actual_person_days} 人天</b></article><article className={Number(project.confirmation_gap_person_days) > 0 ? "metric-alert" : ""}><span>待客户确认缺口</span><b>{project.confirmation_gap_person_days} 人天</b></article></div></section>

    <section className="detail-section"><div className="section-title"><div><h3>月度工时清单 <span className="source-tag">系统接口 · 只读</span></h3><small>点击月份展开员工填报明细</small></div></div>
      <div className="month-list">{project.monthly_time_entries.map(month => <article className="month-card" key={month.month}><button type="button" onClick={() => setExpandedMonth(expandedMonth === month.month ? "" : month.month)}><strong>{month.month}</strong><span>{month.entry_count} 条 · 填报 {month.reported_person_days} 人天 · 确认 {month.approved_person_days} 人天</span><b>{expandedMonth === month.month ? "收起" : "展开"}</b></button>{expandedMonth === month.month && <div className="month-entries">{month.entries.map(item => <div className="detail-row compact" key={item.external_id}><div><strong>{item.employee_name} · 填报 {hoursToDays(item.hours)} 人天 / 确认 {hoursToDays(item.approved_hours)} 人天</strong><small>{item.work_content}</small></div><time>{item.work_date}</time></div>)}</div>}</article>)}</div>
      {project.monthly_time_entries.length === 0 && <p className="detail-empty">暂无月度工时数据</p>}
    </section>

    <section className="detail-section todo-section"><div className="section-title"><div><h3>待办提醒</h3><small>按当前日期自动计算，逾期未确认将持续提醒</small></div><label>当前确认人<input value={confirmedBy} onChange={e => setConfirmedBy(e.target.value)} placeholder="管理人员姓名" /></label></div>
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
    <DetailSection title="最近工时（系统接口 · 只读）" empty="暂无工时数据">{project.time_entries.map(item => <div className="detail-row" key={item.external_id}><div><strong>{item.employee_name} · 填报 {hoursToDays(item.hours)} 人天 / 客户确认 {hoursToDays(item.approved_hours)} 人天</strong><small>{item.work_content}</small></div><time>{item.work_date}</time></div>)}</DetailSection>
  </aside></div>;
}

function DetailSection({ title, empty, children }: { title: string; empty: string; children: React.ReactNode }) {
  const count = Array.isArray(children) ? children.length : children ? 1 : 0;
  return <section className="detail-section"><h3>{title}</h3>{count ? children : <p className="detail-empty">{empty}</p>}</section>;
}

function SyncStatusPanel({ sync, syncing, startSync, refresh }: { sync: SyncStatus | null; syncing: boolean; startSync: () => Promise<void>; refresh: () => Promise<void> }) {
  return <section className="panel">
    <div className="panel-head">
      <div><h2>同步状态</h2><p>查看 CRM 合同/商机同步结果、校验统计和下次计划执行时间</p></div>
      <div className="panel-actions">
        <button className="button secondary" onClick={() => void refresh()}>刷新状态</button>
        <button className="button primary" disabled={syncing} onClick={() => void startSync()}>{syncing ? "正在创建…" : "立即同步"}</button>
      </div>
    </div>
    <div className="sync-grid">
      <article><span>当前状态</span><strong>{statusLabel(sync?.status)}</strong><small>{syncModeLabel(sync)}</small></article>
      <article><span>上次成功时间</span><strong>{sync?.last_success_at ?? "—"}</strong><small>时区：{sync?.timezone ?? "—"}</small></article>
      <article><span>下次计划时间</span><strong>{sync?.next_scheduled_at ?? "—"}</strong><small>每日 03:00 自动执行</small></article>
      <article><span>校验结果</span><strong>{sync?.validation.complete ? "通过" : "需检查"}</strong><small>合并机会 {sync?.validation.merged_opportunities ?? 0} 条</small></article>
    </div>
    <div className="table-wrap">
      <table>
        <thead><tr><th>数据类型</th><th>源记录数</th><th>输出记录数</th><th>说明</th></tr></thead>
        <tbody>
          <tr><td>合同 / 订单</td><td>{sync?.source_stats.contract_order ?? 0}</td><td>{sync?.output_stats.contract_order ?? 0}</td><td>CRM 合同主数据，用于项目名称、客户、金额和签约信息</td></tr>
          <tr><td>销售机会</td><td>{sync?.source_stats.sales_opportunity ?? 0}</td><td>{sync?.output_stats.sales_opportunity ?? 0}</td><td>销售机会与合同项目编号相同会合并，不重复展示</td></tr>
        </tbody>
      </table>
    </div>
    <div className="sync-note">
      <span className="source-tag">项目名称来源</span>
      <p>项目名称优先使用 CRM 主数据主题。仅存在工时数据、但 CRM 未同步到对应项目编号时，项目名称只能临时显示项目代码，并标注“未匹配 CRM 主数据”。</p>
    </div>
  </section>;
}

function CrmRecords(props: { records: RecordsResponse | null; loading: boolean; queryInput: string; setQueryInput: (value: string) => void; submitSearch: (event: FormEvent) => void; dataType: string; setDataType: (value: string) => void; status: string; setStatus: (value: string) => void; page: number; setPage: React.Dispatch<React.SetStateAction<number>>; }) {
  return <section className="panel"><div className="panel-head"><div><h2>CRM 数据记录</h2><p>合同与销售机会审计视图</p></div><Search value={props.queryInput} setValue={props.setQueryInput} submit={props.submitSearch} /></div><div className="filters"><select value={props.dataType} onChange={e => { props.setDataType(e.target.value); props.setPage(1); }}><option value="">全部数据类型</option><option value="contract_order">合同 / 订单</option><option value="sales_opportunity">销售机会</option></select><select value={props.status} onChange={e => { props.setStatus(e.target.value); props.setPage(1); }}><option value="">全部状态</option><option value="成功">成功</option><option value="跟踪">跟踪</option></select><span className="result-count">共 {props.records?.meta.total_records ?? 0} 条结果</span></div><div className="table-wrap"><table><thead><tr><th>类型</th><th>项目编号</th><th>主题</th><th>客户 / 分类</th><th>所有者</th><th>金额</th><th>可能性</th><th>状态</th></tr></thead><tbody>{props.loading ? <LoadingRows /> : props.records?.data.map(record => <RecordRow key={record.record_id} record={record} />)}</tbody></table></div><div className="pagination"><button disabled={props.page <= 1} onClick={() => props.setPage(value => value - 1)}>上一页</button><span>第 {props.records?.meta.page ?? props.page} / {props.records?.meta.total_pages || 1} 页</span><button disabled={!props.records || props.page >= props.records.meta.total_pages} onClick={() => props.setPage(value => value + 1)}>下一页</button></div></section>;
}

function Search({ value, setValue, submit }: { value: string; setValue: (value: string) => void; submit: (event: FormEvent) => void }) { return <form className="search" onSubmit={submit}><input value={value} onChange={e => setValue(e.target.value)} placeholder="搜索主题、客户或项目编号" /><button type="submit">搜索</button></form>; }
function RecordRow({ record }: { record: CrmRecord }) { return <tr><td><span className={`type-pill ${record.data_type}`}>{record.data_type === "contract_order" ? "合同/订单" : "销售机会"}</span></td><td><strong className="project-code">{record.project_number ?? "—"}</strong></td><td><div className="subject">{record.subject}</div>{record.signed_at && <small>{record.signed_at}</small>}</td><td><div>{record.customer ?? "—"}</div><small>{record.category ?? ""}</small></td><td>{record.owner}</td><td className="money">{record.total_amount?.display ?? "—"}</td><td>{record.probability == null ? "—" : `${record.probability}%`}</td><td><span className={`state ${record.status === "成功" ? "success" : "tracking"}`}>{record.status ?? "—"}</span></td></tr>; }
function LoadingRows({ colSpan = 10 }: { colSpan?: number }) { return <>{Array.from({ length: 5 }, (_, index) => <tr className="skeleton" key={index}><td colSpan={colSpan}><span /></td></tr>)}</>; }
function money(value?: string | null) { if (value == null) return "—"; return new Intl.NumberFormat("zh-CN", { style: "currency", currency: "CNY", maximumFractionDigits: 0 }).format(Number(value)); }
function hoursToDays(value?: string | null) {
  if (value == null || value === "") return "—";
  return (Number(value) / 8).toFixed(2);
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
function statusLabel(value?: SyncStatus["status"]) { return ({ succeeded: "同步正常", running: "同步中", failed: "同步失败", auth_required: "需要登录", never_run: "尚未同步" } as const)[value ?? "never_run"]; }
function syncModeLabel(sync: SyncStatus | null) {
  if (sync?.validation.sync_mode === "incremental") return `增量 +${sync.validation.inserted_records ?? 0} / 更新 ${sync.validation.updated_records ?? 0}`;
  if (sync?.validation.sync_mode === "full") return "首次全量同步";
  return "增量同步模式";
}
