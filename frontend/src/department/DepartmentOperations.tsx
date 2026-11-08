import { useCallback, useEffect, useMemo, useState } from "react";
import type { FormEvent } from "react";
import { fetchDepartmentReview, recordOperatingCost, saveAnnualPlan } from "./api";
import { MonthPicker } from "../components/MonthPicker";
import "./department.css";
import { COST_FIELDS, COST_LABEL, PLAN_FIELDS, PLAN_STATUS_LABEL } from "./types";
import type { CostField, DepartmentReview, KpiKey, PlanCompletion, PlanField } from "./types";

// Annual plan metrics (requirements baseline D22–D29): operating metrics first, then the resource metrics that stay.
const KPI_ORDER: KpiKey[] = ["new_contracts", "collections", "revenue_after_tax", "operating_cost", "gross_profit", "gross_margin",
  "capacity_used", "utilization", "headcount"];
const KPI_UNIT: Record<KpiKey, "money" | "days" | "percent" | "people"> = {
  new_contracts: "money", collections: "money", revenue_after_tax: "money", operating_cost: "money", gross_profit: "money",
  gross_margin: "percent", capacity_used: "days", utilization: "percent", headcount: "people",
};
// Cost, gross profit and margin run up to the latest month with costs entered (D25).
const COST_BASED = new Set<KpiKey>(["operating_cost", "gross_profit", "gross_margin"]);
const PLAN_LABEL: Record<PlanField, string> = {
  new_contract_target: "合同额（元）", collection_target: "经营收入·税前（元）", operating_cost_target: "经营成本（元）",
  capacity_person_days_target: "项目投入人天", headcount_target: "人员编制（人）", utilization_target_percent: "利用率（%）",
};

export function money(value: string | null | undefined): string {
  if (value == null) return "—";
  const number = Number(value);
  if (!Number.isFinite(number)) return value;
  return Math.abs(number) >= 10000
    ? `¥${(number / 10000).toLocaleString("zh-CN", { maximumFractionDigits: 1 })} 万`
    : `¥${number.toLocaleString("zh-CN", { maximumFractionDigits: 0 })}`;
}

export function personDays(value: string | number | null | undefined): string {
  if (value == null || value === "") return "—";
  return `${Number(value).toLocaleString("zh-CN", { maximumFractionDigits: 1 })} 人天`;
}

function formatKpi(key: KpiKey, value: string | null): string {
  if (value == null) return "—";
  switch (KPI_UNIT[key]) {
    case "money": return money(value);
    case "days": return personDays(value);
    case "percent": return `${Number(value)}%`;
    default: return `${Number(value)} 人`;
  }
}

function errorMessage(err: unknown, fallback: string): string {
  return err instanceof Error ? err.message : fallback;
}

export function DepartmentOperationsPage({ canEditPlan }: { canEditPlan: boolean }) {
  const thisYear = new Date().getFullYear();
  const [year, setYear] = useState(thisYear);
  const [month, setMonth] = useState("");
  const [review, setReview] = useState<DepartmentReview | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [planOpen, setPlanOpen] = useState(false);

  const load = useCallback(async () => {
    setLoading(true); setError(null);
    try {
      setReview(await fetchDepartmentReview(year, month || undefined));
    } catch (err) {
      setError(errorMessage(err, "部门经营数据加载失败"));
    } finally {
      setLoading(false);
    }
  }, [year, month]);

  useEffect(() => { void load(); }, [load]);

  return <div className="department">
    <section className="panel department-intro">
      <div className="panel-head">
        <div>
          <h2>部门年度经营</h2>
          <p>按年度经营计划和时间进度对照合同额（当年签约，含税）、经营收入（当年到账回款，税前与税后）、经营成本（财务按月录入）、毛利润与毛利率（按税后收入，截至最近已录成本的月份），以及项目投入人天、产能利用率和在岗人数，并给出带依据的策略信号（只作建议）。</p>
        </div>
        <div className="department-period">
          <select value={year} aria-label="选择年度" onChange={e => { setYear(Number(e.target.value)); setMonth(""); }}>
            {[thisYear - 1, thisYear, thisYear + 1].map(y => <option key={y} value={y}>{y} 年</option>)}
          </select>
          <label>截至<MonthPicker value={month || review?.month || ""} onChange={setMonth} label="截至月份" min={`${year}-01`} max={`${year}-12`} /></label>
          {canEditPlan && <button type="button" className="button secondary" onClick={() => setPlanOpen(!planOpen)}>{planOpen ? "收起年度计划" : review?.plan ? "调整年度计划" : "设定年度计划"}</button>}
        </div>
      </div>
      {planOpen && review && <AnnualPlanForm year={year} review={review} onSaved={() => { setPlanOpen(false); void load(); }} reportError={setError} />}
    </section>

    {error && <div className="error-banner">{error}</div>}
    {loading && !review ? <section className="panel"><div className="resource-loading">正在汇总部门经营数据…</div></section> : review && <>
      <KpiGrid review={review} />
      <OperatingCostPanel review={review} canEdit={canEditPlan} onSaved={() => void load()} reportError={setError} />
      <div className="department-columns">
        <SignalPanel review={review} />
        <UtilizationPanel review={review} />
      </div>
    </>}
  </div>;
}

function AnnualPlanForm({ year, review, onSaved, reportError }: { year: number; review: DepartmentReview; onSaved: () => void; reportError: (message: string | null) => void }) {
  const initial = useMemo(() => Object.fromEntries(PLAN_FIELDS.map(field => [field, review.plan?.[field] ?? ""])) as Record<PlanField, string>, [review.plan]);
  const [values, setValues] = useState(initial);
  const [notes, setNotes] = useState(review.plan?.notes ?? "");
  const [saving, setSaving] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setSaving(true); reportError(null);
    try {
      await saveAnnualPlan(year, { ...Object.fromEntries(PLAN_FIELDS.map(field => [field, values[field] === "" ? null : values[field]])), notes: notes || null });
      onSaved();
    } catch (err) {
      reportError(errorMessage(err, "年度计划保存失败"));
    } finally {
      setSaving(false);
    }
  }

  return <form className="department-plan-form" onSubmit={submit}>
    {PLAN_FIELDS.map(field => <label key={field}>{PLAN_LABEL[field]}
      <input type="number" min="0" max={field.endsWith("_percent") ? 100 : undefined} step="0.01" value={values[field]}
        onChange={e => setValues({ ...values, [field]: e.target.value })} placeholder="未设定" />
    </label>)}
    {review.plan && <p className="span-all plan-derived">推算目标（增值税率 {Number(review.operating.tax_rate_percent)}%）：经营收入·税后 {money(review.plan.revenueTargetAfterTax ?? null)}　毛利润 {money(review.plan.grossProfitTarget ?? null)}　毛利率 {review.plan.grossMarginTargetPercent != null ? `${Number(review.plan.grossMarginTargetPercent)}%` : "—"}</p>}
    <label className="span-2">备注<input value={notes} onChange={e => setNotes(e.target.value)} placeholder="例如：年初经营会确定" /></label>
    <div className="form-actions"><button className="button primary" disabled={saving}>保存 {year} 年计划</button>
      {review.plan && <small>上次由 {review.plan.updated_by ?? "—"} 于 {review.plan.updated_at.slice(0, 10)} 更新</small>}</div>
  </form>;
}

// Annual indicators as one compact table: a row per indicator (actual, target, progress against time, status), grouped
// into 经营 and 资源; the cost caveat (D25) is said once for the group instead of on every cost-based tile.
const KPI_GROUPS: { title: string; keys: KpiKey[] }[] = [
  { title: "经营", keys: ["new_contracts", "collections", "revenue_after_tax", "operating_cost", "gross_profit", "gross_margin"] },
  { title: "资源", keys: ["capacity_used", "utilization", "headcount"] },
];

function percentText(value: number): string {
  return `${value.toLocaleString("zh-CN", { maximumFractionDigits: 1 })}%`;
}

function KpiGrid({ review }: { review: DepartmentReview }) {
  const missing = review.operating.missing_cost_months;
  const asOf = review.kpis.operating_cost?.as_of;
  const costNote = asOf
    ? `经营成本、毛利润、毛利率截至 ${asOf}（最近已录成本的月份）${missing.length ? `；缺 ${missing.length} 个月成本，成本偏低、毛利偏高，仅供参考` : ""}`
    : "尚未录入经营成本，成本与毛利暂无数据";
  return <section className="panel kpi-table-panel" aria-label="年度指标">
    <div className="table-wrap"><table className="kpi-table">
      <thead><tr><th>指标</th><th>实际</th><th>目标</th><th>完成进度</th><th>状态</th></tr></thead>
      {KPI_GROUPS.map(group => <tbody key={group.title}>
        <tr className="kpi-group"><th colSpan={5}><span>{group.title}</span>{group.title === "经营" && <small>{costNote}</small>}</th></tr>
        {group.keys.map(key => <KpiRow key={key} kpiKey={key} kpi={review.kpis[key]} />)}
      </tbody>)}
    </table></div>
    <p className="kpi-legend"><i className="kpi-legend-bar" />完成比例 <i className="kpi-legend-tick" />时间进度（累计类指标）</p>
  </section>;
}

function KpiRow({ kpiKey, kpi }: { kpiKey: KpiKey; kpi: PlanCompletion }) {
  // The ratio and the gap come from the ontology (D43); the page only formats them.
  const cumulative = kpi.measure === "cumulative";
  const ratio = kpi.completion_percent;
  const [gapValue, gapUnit] = kpi.measure === "count" ? [kpi.shortfall, " 人"] : [kpi.lag_points, " 个百分点"];
  const gap = gapValue != null && gapValue > 0 ? `${gapValue.toLocaleString("zh-CN", { maximumFractionDigits: 1 })}${gapUnit}` : null;
  return <tr className={`kpi-row ${kpi.status}`}>
    <td className="kpi-name">{kpi.label}</td>
    <td className="kpi-actual">{formatKpi(kpiKey, kpi.actual)}</td>
    <td className="kpi-target">{formatKpi(kpiKey, kpi.target)}</td>
    <td className="kpi-progress">{ratio == null ? <span className="muted">—</span> : <div title={cumulative ? "条为完成比例，竖线为时间进度" : "条为实际 ÷ 目标"}>
      <div className="kpi-meter" role="img" aria-label={cumulative ? `完成 ${percentText(ratio)}，时间进度 ${percentText(kpi.time_progress_percent)}` : `达成 ${percentText(ratio)}`}>
        <i style={{ width: `${Math.min(100, Math.max(0, ratio))}%` }} />
        {cumulative && <b style={{ left: `${Math.min(100, kpi.time_progress_percent)}%` }} />}
      </div>
      <small>{cumulative ? <>{percentText(ratio)}<span> / 时间 {percentText(kpi.time_progress_percent)}</span></> : percentText(ratio)}</small>
    </div>}</td>
    <td className="kpi-status"><em className={`plan-status ${kpi.status}`}>{PLAN_STATUS_LABEL[kpi.status]}</em>{gap && <small>{gap}</small>}</td>
  </tr>;
}

function OperatingCostPanel({ review, canEdit, onSaved, reportError }: { review: DepartmentReview; canEdit: boolean; onSaved: () => void; reportError: (message: string | null) => void }) {
  const operating = review.operating;
  const thisMonth = new Date().toISOString().slice(0, 7);
  const latestMonth = review.year === Number(thisMonth.slice(0, 4)) ? thisMonth : `${review.year}-12`;
  const [month, setMonth] = useState(latestMonth);
  const existing = operating.cost_entries.find(entry => entry.month === month);
  const [values, setValues] = useState<Record<CostField, string>>(() => Object.fromEntries(COST_FIELDS.map(f => [f, ""])) as Record<CostField, string>);
  const [notes, setNotes] = useState("");
  const [saving, setSaving] = useState(false);
  useEffect(() => {
    setValues(Object.fromEntries(COST_FIELDS.map(f => [f, existing ? existing[f] : ""])) as Record<CostField, string>);
    setNotes(existing?.notes ?? "");
  }, [month, existing]);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setSaving(true); reportError(null);
    try {
      await recordOperatingCost(month, Object.fromEntries(COST_FIELDS.map(f => [f, values[f] === "" ? "0" : values[f]])), notes || null);
      onSaved();
    } catch (err) {
      reportError(errorMessage(err, "经营成本保存失败"));
    } finally {
      setSaving(false);
    }
  }

  return <section className="panel department-costs">
    <div className="panel-head"><div><h2>经营成本（财务录入）</h2>
      <p>财务线下提供，按月录入人工成本、绩效奖金、差旅费、外包成本、培训成本、其他。毛利润 = 经营收入（税后）− 经营成本，与收入同样截至最近已录成本的月份。{operating.cost_cutoff_month ? `当前截至 ${operating.cost_cutoff_month}。` : "本年度还没有录入。"}</p>
      {operating.missing_cost_months.length > 0 && <p className="cost-missing">未录入的月份：{operating.missing_cost_months.join("、")}（毛利计算中按缺失处理，不按 0 计）</p>}
    </div></div>
    {operating.cost_entries.length > 0 && <div className="table-wrap"><table>
      <thead><tr><th>月份</th>{COST_FIELDS.map(f => <th key={f}>{COST_LABEL[f]}</th>)}<th>合计</th><th>录入人</th></tr></thead>
      <tbody>{operating.cost_entries.map(entry => <tr key={entry.month}><td>{entry.month}</td>
        {COST_FIELDS.map(f => <td key={f}>{money(entry[f])}</td>)}<td><strong>{money(entry.total)}</strong></td><td>{entry.updated_by ?? "—"}</td></tr>)}</tbody>
    </table></div>}
    {canEdit && <form className="department-plan-form" onSubmit={submit}>
      <label>月份<input type="month" value={month} min={`${review.year}-01`} max={latestMonth} required onChange={e => setMonth(e.target.value)} /></label>
      {COST_FIELDS.map(f => <label key={f}>{COST_LABEL[f]}（元）
        <input type="number" min="0" step="0.01" value={values[f]} placeholder="0" onChange={e => setValues({ ...values, [f]: e.target.value })} />
      </label>)}
      <label className="span-2">备注<input value={notes} onChange={e => setNotes(e.target.value)} placeholder="例如：财务 9 月成本报表" /></label>
      <div className="form-actions"><button className="button primary" disabled={saving}>{existing ? `修改 ${month} 成本` : `录入 ${month} 成本`}</button>
        {existing && <small>已由 {existing.updated_by ?? "—"} 录入；再次保存将覆盖，审计日志保留每次录入</small>}</div>
    </form>}
  </section>;
}

function SignalPanel({ review }: { review: DepartmentReview }) {
  return <section className="panel department-signals">
    <div className="panel-head"><div><h2>策略信号</h2><p>按优先级排列：推动验收回款 → 争取新合同 → 释放资源。每条信号都附触发依据，由部门管理者决定是否采纳。依赖经营口径的「控制成本」「招聘或外部支援」随经营口径暂停。</p></div></div>
    <ol className="signal-list">
      {review.signals.map((signal, index) => <li key={signal.signal} className={`signal ${signal.signal}`}>
        <div className="signal-head"><span className="signal-rank">{index + 1}</span><strong>{signal.label}</strong></div>
        <ul>{signal.evidence.map(item => <li key={item}>{item}</li>)}</ul>
      </li>)}
    </ol>
  </section>;
}

function UtilizationPanel({ review }: { review: DepartmentReview }) {
  const rows = review.utilization_by_month;
  const target = review.plan?.utilization_target_percent != null ? Number(review.plan.utilization_target_percent) : null;
  const top = Math.max(100, target ?? 0, ...rows.map(r => r.utilization_percent));
  const latest = rows[rows.length - 1];
  return <section className="panel department-utilization">
    <div className="panel-head"><div><h2>月度产能利用率</h2><p>当月项目工时 ÷（在岗人数 × 当月中国工作日 × 标准每日小时数），内部代码不计入。{target != null ? `虚线为年度目标 ${target}%。` : ""}</p></div></div>
    {rows.length === 0 ? <p className="department-empty">本年度还没有工时数据。</p> : <div className="util-chart" role="img" aria-label={`${review.year} 年各月利用率，最近一月 ${latest?.utilization_percent}%`}>
      {target != null && <i className="util-target" style={{ bottom: `${target / top * 100}%` }} />}
      {rows.map(row => <div key={row.month} className="util-col" title={`${row.month}：${row.utilization_percent}%（${row.active_employees} 人）`}>
        <div className="util-bar" style={{ height: `${row.utilization_percent / top * 100}%` }} />
        {row === latest && <b>{row.utilization_percent}%</b>}
        <small>{Number(row.month.slice(5))} 月</small>
      </div>)}
    </div>}
    {rows.length > 0 && <details className="department-data"><summary>查看数据</summary>
      <table><thead><tr><th>月份</th><th>利用率</th><th>在岗人数</th></tr></thead><tbody>
        {rows.map(row => <tr key={row.month}><td>{row.month}</td><td>{row.utilization_percent}%</td><td>{row.active_employees}</td></tr>)}
      </tbody></table>
    </details>}
  </section>;
}

