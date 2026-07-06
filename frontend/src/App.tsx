import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import {
  confirmMilestone,
  confirmPayment,
  createMilestone,
  downloadExport,
  fetchProject,
  fetchProjects,
  fetchRecords,
  fetchSyncStatus,
  triggerSync,
  updateMilestone,
  updateProjectSettings
} from "./api";
import type {
  CrmRecord,
  PortfolioResponse,
  ProjectDetail,
  ProjectSummary,
  RecordsResponse,
  SyncStatus
} from "./types";

const PAGE_SIZE = 20;
type View = "projects" | "crm";

export function App() {
  const [view, setView] = useState<View>("projects");
  const [portfolio, setPortfolio] = useState<PortfolioResponse | null>(null);
  const [records, setRecords] = useState<RecordsResponse | null>(null);
  const [sync, setSync] = useState<SyncStatus | null>(null);
  const [selected, setSelected] = useState<ProjectDetail | null>(null);
  const [queryInput, setQueryInput] = useState("");
  const [query, setQuery] = useState("");
  const [contractType, setContractType] = useState("");
  const [riskLevel, setRiskLevel] = useState("");
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
      } else {
        setRecords(await fetchRecords({ dataType, query, status, page, pageSize: PAGE_SIZE }));
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "数据加载失败");
    } finally {
      setLoading(false);
    }
  }, [view, query, contractType, riskLevel, dataType, status, page]);

  useEffect(() => { void load(); }, [load]);

  const cards = useMemo(() => view === "projects" ? [
    { label: "项目总数", value: portfolio?.summary.total_projects ?? "—", tone: "blue" },
    { label: "合同总额", value: money(portfolio?.summary.contract_value), tone: "green" },
    { label: "已回款", value: money(portfolio?.summary.received_amount), tone: "amber" },
    { label: "风险项目", value: (portfolio?.summary.critical_projects ?? 0) + (portfolio?.summary.warning_projects ?? 0), tone: "violet" }
  ] : [
    { label: "有效记录", value: records?.meta.total_records ?? "—", tone: "blue" },
    { label: "合同 / 订单", value: sync?.output_stats.contract_order ?? "—", tone: "green" },
    { label: "销售机会", value: sync?.output_stats.sales_opportunity ?? "—", tone: "amber" },
    { label: "已合并机会", value: sync?.validation.merged_opportunities ?? "—", tone: "violet" }
  ], [view, portfolio, records, sync]);

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
          <button className={`nav-item ${view === "crm" ? "active" : ""}`} onClick={() => switchView("crm")}><span>⌘</span> CRM 数据</button>
          <a className="nav-item" href="#sync"><span>↻</span> 同步状态</a>
          <a className="nav-item" href="/docs" target="_blank"><span>⌁</span> API 文档</a>
        </nav>
        <div className="sidebar-foot">
          <span className={`status-dot ${sync?.status === "succeeded" ? "ok" : "warn"}`} />
          <div><strong>{statusLabel(sync?.status)}</strong><small>{syncModeLabel(sync)} · 每日 03:00</small></div>
        </div>
      </aside>

      <main>
        <header className="topbar">
          <div><p className="eyebrow">PROJECT LIFECYCLE CONTROL</p><h1>{view === "projects" ? "项目组合与生命周期" : "CRM 合同与销售机会"}</h1></div>
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
    <div className="panel-head"><div><h2>项目组合</h2><p>按项目编号聚合合同、工时、里程碑与回款</p></div><Search value={props.queryInput} setValue={props.setQueryInput} submit={props.submitSearch} /></div>
    <div className="data-boundary"><span className="source-tag">系统接口数据</span><p>合同、商机、金额和工时由接口增量同步，只读</p><span className="manual-tag">人工维护数据</span><p>合同类别、预算人天、里程碑和确认状态由管理人员维护</p></div>
    <div className="filters">
      <select value={props.contractType} onChange={e => props.setContractType(e.target.value)}><option value="">全部合同类型</option><option value="construction">建设类</option><option value="maintenance">维护类</option><option value="open_ended">开口类</option><option value="sales">销售类</option><option value="unknown">未分类</option></select>
      <select value={props.riskLevel} onChange={e => props.setRiskLevel(e.target.value)}><option value="">全部风险</option><option value="critical">严重</option><option value="warning">关注</option><option value="healthy">健康</option></select>
      <span className="result-count">共 {props.portfolio?.summary.total_projects ?? 0} 个项目</span>
    </div>
    <div className="table-wrap"><table><thead><tr><th>风险</th><th>项目编号</th><th>项目主题</th><th>类型 / 阶段</th><th>项目经理</th><th>合同金额</th><th>人天消耗</th><th>待办 / 里程碑</th><th>回款</th></tr></thead><tbody>
      {props.loading ? <LoadingRows /> : props.portfolio?.data.map(project => <ProjectRow key={project.project_number} project={project} open={() => props.openProject(project.project_number)} />)}
      {!props.loading && props.portfolio?.data.length === 0 && <tr><td className="empty" colSpan={9}>没有符合条件的项目</td></tr>}
    </tbody></table></div>
  </section>;
}

function ProjectRow({ project, open }: { project: ProjectSummary; open: () => void }) {
  return <tr className="clickable" onClick={open}>
    <td><span className={`risk ${project.risk_level}`}>{riskLabel(project.risk_level)}</span>{project.anomaly_count > 0 && <small className="danger-text">{project.anomaly_count} 项提醒</small>}</td>
    <td><strong className="project-code">{project.project_number}</strong></td>
    <td><div className="subject">{project.subject}</div><small>{project.customer ?? "—"}</small></td>
    <td><div>{contractTypeLabel(project.contract_type)}</div><small>{lifecycleLabel(project.lifecycle_status)}</small></td>
    <td>{project.project_manager ?? "—"}</td><td className="money">{money(project.contract_value)}</td>
    <td><Budget project={project} /></td>
    <td><div>{project.pending_todos ?? 0} 项待办 · {project.milestone_completed}/{project.milestone_total}</div>{project.maintenance_complete === false && <small className="danger-text">待维护：{(project.maintenance_reasons ?? []).join("、")}</small>}{project.next_todo && project.maintenance_complete && <small className={project.next_todo.urgency === "overdue" ? "danger-text" : ""}>{project.next_todo.name} · {project.next_todo.label}</small>}</td>
    <td><div>{money(project.received_amount)}</div>{Number(project.contract_value) > 0 && <small>{Math.round(Number(project.received_amount) / Number(project.contract_value) * 100)}%</small>}</td>
  </tr>;
}

function Budget({ project }: { project: ProjectSummary }) {
  if (project.contract_type === "open_ended") return <><div>{project.billable_person_days} 人天</div><small>可结算</small></>;
  if (project.planned_person_days == null) return <span>未设置</span>;
  return <div className="budget"><div><span>{project.actual_person_days}/{project.planned_person_days} 人天</span><b>{project.budget_utilization}%</b></div><i><em style={{ width: `${Math.min(project.budget_utilization ?? 0, 100)}%` }} /></i></div>;
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

  useEffect(() => {
    setSettings({ contract_type: project.contract_type, lifecycle_status: project.lifecycle_status, planned_person_days: project.planned_person_days ?? "", hours_per_person_day: project.settings.hours_per_person_day ?? "8.00", project_manager: project.project_manager ?? "" });
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
  }

  function editMilestone(item: ProjectDetail["milestones"][number]) {
    if (item.status === "completed") return;
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

  function actor(): string | null {
    const value = confirmedBy.trim();
    if (!value) { setMessage("请先填写当前确认人"); return null; }
    return value;
  }

  return <div className="drawer-backdrop" onClick={close}><aside className="project-drawer" onClick={e => e.stopPropagation()}>
    <button className="drawer-close" onClick={close}>×</button>
    <p className="eyebrow">{project.project_number}</p><h2>{project.subject}</h2>
    <div className="drawer-badges"><span>{contractTypeLabel(project.contract_type)}</span><span>{lifecycleLabel(project.lifecycle_status)}</span><span className={`risk ${project.risk_level}`}>{riskLabel(project.risk_level)}</span></div>
    {!project.maintenance_complete && <section className="maintenance-box"><strong>同步后待人工维护</strong><p>{(project.maintenance_reasons ?? []).join("、")}</p></section>}
    {project.risk_reasons.length > 0 && <section className="risk-box"><strong>管理关注</strong>{project.risk_reasons.map(reason => <p key={reason}>• {reason}</p>)}</section>}
    {(project.anomaly_alerts ?? []).length > 0 && <section className="alert-stack"><div className="section-title"><div><h3>异常与临期提醒</h3><small>里程碑、工时预算和客户确认缺口</small></div></div>{project.anomaly_alerts.map((alert, index) => <article className={`anomaly-alert ${alert.severity}`} key={`${alert.code}-${index}`}><div><strong>{alert.title}</strong><span>{alert.severity === "critical" ? "严重" : "提醒"}</span></div><p>{alert.detail}</p></article>)}</section>}
    {message && <div className="inline-message">{message}</div>}
    <section className="detail-section source-section"><div className="section-title"><div><h3>合同与工时数据 <span className="source-tag">系统接口 · 只读</span></h3><small>增量同步仅更新发生变化的源数据</small></div></div><div className="detail-metrics six"><article><span>合同金额</span><b>{money(project.contract_value)}</b></article><article><span>已开票</span><b>{money(project.invoiced_amount)}</b></article><article><span>已回款</span><b>{money(project.received_amount)}</b></article><article><span>员工填报工时</span><b>{project.reported_person_days} 人天</b></article><article><span>客户确认工时</span><b>{project.customer_confirmed_person_days} 人天</b></article><article className={Number(project.confirmation_gap_person_days) > 0 ? "metric-alert" : ""}><span>待客户确认缺口</span><b>{project.confirmation_gap_person_days} 人天</b></article></div></section>

    <section className="detail-section todo-section"><div className="section-title"><div><h3>待办提醒</h3><small>按当前日期自动计算，逾期未确认将持续提醒</small></div><label>当前确认人<input value={confirmedBy} onChange={e => setConfirmedBy(e.target.value)} placeholder="管理人员姓名" /></label></div>
      <div className="todo-grid">{project.todo_events.filter(item => !item.completed).map(item => <article className={`todo-card ${item.urgency}`} key={`${item.event_type}-${item.event_id}`}><div><span>{item.event_type === "payment" ? "回款" : "里程碑"}</span><b>{item.label}</b></div><h4>{item.name}</h4><p>{item.due_date}{item.amount && ` · ${money(item.amount)}`}{item.deliverable && ` · 输出物：${item.deliverable}`}</p>{item.event_type === "milestone" ? <button disabled={saving} onClick={() => { const name = actor(); if (name) void run(() => confirmMilestone(project.project_number, item.event_id, name), "里程碑已确认完成"); }}>确认完成</button> : <button disabled={saving} onClick={() => { const name = actor(); if (name) void run(() => confirmPayment(project.project_number, item.event_id, name), "回款已确认到账"); }}>确认到账</button>}</article>)}</div>
      {project.todo_events.every(item => item.completed) && <p className="detail-empty">当前没有待办事件</p>}
    </section>

    <section className="detail-section"><div className="section-title"><div><h3>项目基础配置 <span className="manual-tag">人工维护</span></h3><small>同步不会覆盖以下字段</small></div></div><form className="config-form" onSubmit={saveSettings}>
      <label>合同类别<select value={settings.contract_type} onChange={e => setSettings({ ...settings, contract_type: e.target.value as ProjectDetail["contract_type"] })}><option value="unknown">请选择</option><option value="construction">建设类</option><option value="maintenance">维护类</option><option value="open_ended">开口类（无上限）</option><option value="sales">销售类</option></select></label>
      <label>生命周期<select value={settings.lifecycle_status} onChange={e => setSettings({ ...settings, lifecycle_status: e.target.value as ProjectDetail["lifecycle_status"] })}><option value="planned">计划中</option><option value="active">执行中</option><option value="on_hold">已暂停</option><option value="completed">已完成</option><option value="closed">已关闭</option></select></label>
      <label>初始预算人天<input type="number" min="0" step="0.01" disabled={settings.contract_type === "open_ended"} required={["construction", "maintenance"].includes(settings.contract_type)} value={settings.contract_type === "open_ended" ? "" : settings.planned_person_days} placeholder={settings.contract_type === "open_ended" ? "无上限，无需填写" : "例如 120"} onChange={e => setSettings({ ...settings, planned_person_days: e.target.value })} /></label>
      <label>每人天小时<input type="number" min="0.1" step="0.1" required value={settings.hours_per_person_day} onChange={e => setSettings({ ...settings, hours_per_person_day: e.target.value })} /></label>
      <label className="span-2">项目经理<input required value={settings.project_manager} onChange={e => setSettings({ ...settings, project_manager: e.target.value })} /></label>
      <button className="button primary span-2" disabled={saving}>保存基础配置</button>
    </form></section>

    <section className="detail-section"><div className="section-title"><div><h3>里程碑计划 <span className="manual-tag">人工维护</span></h3><small>项目里程碑与商务回款节点统一维护，同步不会覆盖</small></div></div>
      <div className="milestone-list">{project.milestones.map(item => <article key={item.milestone_id} className={`milestone-item ${item.deadline.urgency} ${editingMilestoneId === item.milestone_id ? "editing" : ""}`}><span className="sequence">{item.sequence_no ?? "—"}</span><div><strong>{item.name}</strong><small>{item.planned_start} 至 {item.planned_end} · {item.deadline.label}</small><small>输出物：{item.deliverable || "未填写"}{item.is_payment_node ? ` · 回款 ${item.payment_date} ${money(item.payment_amount)}` : ""}</small></div><div className="milestone-actions"><span className={`mini-state ${item.status}`}>{item.status === "completed" ? "已完成" : "待确认"}</span>{item.confirmed_by && <small>{item.confirmed_by}</small>}{item.status !== "completed" && <button type="button" onClick={() => editMilestone(item)}>admin 编辑</button>}</div></article>)}</div>
      {editingMilestoneId && <div className="editing-banner"><strong>编辑待确认里程碑</strong><span>保存后将重新计算待办日期和回款提醒</span></div>}
      <form className="milestone-form" onSubmit={addMilestone}>
        <label>里程碑名称<input required value={milestone.name} onChange={e => setMilestone({ ...milestone, name: e.target.value })} /></label>
        <label>负责人<input value={milestone.owner} onChange={e => setMilestone({ ...milestone, owner: e.target.value })} /></label>
        <label>开始日期<input type="date" required value={milestone.planned_start} onChange={e => setMilestone({ ...milestone, planned_start: e.target.value })} /></label>
        <label>结束日期<input type="date" required value={milestone.planned_end} onChange={e => setMilestone({ ...milestone, planned_end: e.target.value })} /></label>
        <label className="span-2">输出物<input value={milestone.deliverable} onChange={e => setMilestone({ ...milestone, deliverable: e.target.value })} placeholder="如：需求说明书、软件设计方案" /></label>
        <label className="check-label span-2"><input type="checkbox" checked={milestone.is_payment_node} onChange={e => setMilestone({ ...milestone, is_payment_node: e.target.checked })} />同时设为商务回款节点</label>
        {milestone.is_payment_node && <><label>回款日期<input type="date" required value={milestone.payment_date} onChange={e => setMilestone({ ...milestone, payment_date: e.target.value })} /></label><label>回款金额（元）<input type="number" min="0" step="0.01" required value={milestone.payment_amount} onChange={e => setMilestone({ ...milestone, payment_amount: e.target.value })} /></label></>}
        <div className="form-actions span-2"><button className="button primary" disabled={saving}>{editingMilestoneId ? "保存修改" : "添加里程碑"}</button>{editingMilestoneId && <button type="button" className="button secondary" disabled={saving} onClick={resetMilestoneForm}>取消编辑</button>}</div>
      </form>
    </section>
    <DetailSection title="最近工时（系统接口 · 只读）" empty="暂无工时数据">{project.time_entries.map(item => <div className="detail-row" key={item.external_id}><div><strong>{item.employee_name} · 填报 {item.hours}h / 客户确认 {item.approved_hours}h</strong><small>{item.work_content}</small></div><time>{item.work_date}</time></div>)}</DetailSection>
  </aside></div>;
}

function DetailSection({ title, empty, children }: { title: string; empty: string; children: React.ReactNode }) {
  const count = Array.isArray(children) ? children.length : children ? 1 : 0;
  return <section className="detail-section"><h3>{title}</h3>{count ? children : <p className="detail-empty">{empty}</p>}</section>;
}

function CrmRecords(props: { records: RecordsResponse | null; loading: boolean; queryInput: string; setQueryInput: (value: string) => void; submitSearch: (event: FormEvent) => void; dataType: string; setDataType: (value: string) => void; status: string; setStatus: (value: string) => void; page: number; setPage: React.Dispatch<React.SetStateAction<number>>; }) {
  return <section className="panel"><div className="panel-head"><div><h2>CRM 数据记录</h2><p>合同与销售机会审计视图</p></div><Search value={props.queryInput} setValue={props.setQueryInput} submit={props.submitSearch} /></div><div className="filters"><select value={props.dataType} onChange={e => { props.setDataType(e.target.value); props.setPage(1); }}><option value="">全部数据类型</option><option value="contract_order">合同 / 订单</option><option value="sales_opportunity">销售机会</option></select><select value={props.status} onChange={e => { props.setStatus(e.target.value); props.setPage(1); }}><option value="">全部状态</option><option value="成功">成功</option><option value="跟踪">跟踪</option></select><span className="result-count">共 {props.records?.meta.total_records ?? 0} 条结果</span></div><div className="table-wrap"><table><thead><tr><th>类型</th><th>项目编号</th><th>主题</th><th>客户 / 分类</th><th>所有者</th><th>金额</th><th>可能性</th><th>状态</th></tr></thead><tbody>{props.loading ? <LoadingRows /> : props.records?.data.map(record => <RecordRow key={record.record_id} record={record} />)}</tbody></table></div><div className="pagination"><button disabled={props.page <= 1} onClick={() => props.setPage(value => value - 1)}>上一页</button><span>第 {props.records?.meta.page ?? props.page} / {props.records?.meta.total_pages || 1} 页</span><button disabled={!props.records || props.page >= props.records.meta.total_pages} onClick={() => props.setPage(value => value + 1)}>下一页</button></div></section>;
}

function Search({ value, setValue, submit }: { value: string; setValue: (value: string) => void; submit: (event: FormEvent) => void }) { return <form className="search" onSubmit={submit}><input value={value} onChange={e => setValue(e.target.value)} placeholder="搜索主题、客户或项目编号" /><button type="submit">搜索</button></form>; }
function RecordRow({ record }: { record: CrmRecord }) { return <tr><td><span className={`type-pill ${record.data_type}`}>{record.data_type === "contract_order" ? "合同/订单" : "销售机会"}</span></td><td><strong className="project-code">{record.project_number ?? "—"}</strong></td><td><div className="subject">{record.subject}</div>{record.signed_at && <small>{record.signed_at}</small>}</td><td><div>{record.customer ?? "—"}</div><small>{record.category ?? ""}</small></td><td>{record.owner}</td><td className="money">{record.total_amount?.display ?? "—"}</td><td>{record.probability == null ? "—" : `${record.probability}%`}</td><td><span className={`state ${record.status === "成功" ? "success" : "tracking"}`}>{record.status ?? "—"}</span></td></tr>; }
function LoadingRows() { return <>{Array.from({ length: 5 }, (_, index) => <tr className="skeleton" key={index}><td colSpan={9}><span /></td></tr>)}</>; }
function money(value?: string | null) { if (value == null) return "—"; return new Intl.NumberFormat("zh-CN", { style: "currency", currency: "CNY", maximumFractionDigits: 0 }).format(Number(value)); }
function contractTypeLabel(value: ProjectSummary["contract_type"]) { return ({ construction: "建设类", maintenance: "维护类", open_ended: "开口类", sales: "销售类", unknown: "未分类" } as const)[value]; }
function lifecycleLabel(value: ProjectSummary["lifecycle_status"]) { return ({ planned: "计划中", active: "执行中", on_hold: "已暂停", completed: "已完成", closed: "已关闭" } as const)[value]; }
function riskLabel(value: ProjectSummary["risk_level"]) { return ({ healthy: "健康", warning: "关注", critical: "严重" } as const)[value]; }
function statusLabel(value?: SyncStatus["status"]) { return ({ succeeded: "同步正常", running: "同步中", failed: "同步失败", auth_required: "需要登录", never_run: "尚未同步" } as const)[value ?? "never_run"]; }
function syncModeLabel(sync: SyncStatus | null) {
  if (sync?.validation.sync_mode === "incremental") return `增量 +${sync.validation.inserted_records ?? 0} / 更新 ${sync.validation.updated_records ?? 0}`;
  if (sync?.validation.sync_mode === "full") return "首次全量同步";
  return "增量同步模式";
}
