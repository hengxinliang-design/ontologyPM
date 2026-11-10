import type { ReactNode } from "react";
import type { OntologyValueType } from "./types";

type Json = Record<string, unknown>;
type Labeler = (valueType: string, value: unknown) => string;
type Tone = "neutral" | "good" | "warn" | "bad";

const HEALTH_TONE: Record<string, Tone> = { 健康: "good", 正常: "good", 工作量结算: "good", 额外投入: "bad", 高风险: "bad", 工时充足: "good", 基本达标: "good", 需关注: "warn", 需要关注: "warn", 接近上限: "warn", 已超预算: "bad", 严重不足: "bad", 无基线: "neutral" };
const RISK_TONE: Record<string, Tone> = { healthy: "good", warning: "warn", critical: "bad" };
const URGENCY_TONE: Record<string, Tone> = { overdue: "bad", due_today: "warn", due_soon: "warn", upcoming: "neutral", completed: "good" };
const RESOURCE_TONE: Record<string, Tone> = { overloaded: "bad", fatigue: "warn", fragmented: "warn", idle: "neutral", first_month: "neutral", healthy: "good" };
const PLAN_STATUS: Record<string, [string, Tone]> = { behind: ["落后", "bad"], on_track: ["正常", "good"], no_target: ["无目标", "neutral"], no_data: ["无数据", "neutral"] };
const PAYMENT_STATUS: Record<string, string> = { planned: "计划中", invoiced: "已开票", partially_paid: "部分到账", paid: "已到账" };
const MILESTONE_STATUS: Record<string, string> = { planned: "计划中", in_progress: "进行中", completed: "已完成", blocked: "受阻" };
const GENERIC_LABELS: Record<string, string> = {
  total_projects: "项目数", contract_value: "合同总额", received_amount: "已回款", critical_projects: "严重风险",
  warning_projects: "需关注", healthy_projects: "健康", incomplete_maintenance: "维护未完成"
};

export function money(value: unknown): string {
  if (value === null || value === undefined || value === "") return "—";
  const number = Number(value);
  if (Number.isNaN(number)) return String(value);
  return `¥${number.toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

export function days(value: unknown): string {
  if (value === null || value === undefined || value === "") return "—";
  return `${value} 人天`;
}

export function hours(value: unknown): string {
  if (value === null || value === undefined || value === "") return "—";
  return `${value} 小时`;
}

function percentOf(value: unknown): number | null {
  if (value === null || value === undefined || value === "") return null;
  const number = Number(value);
  return Number.isNaN(number) ? null : number;
}

export function makeLabeler(valueTypes: OntologyValueType[]): Labeler {
  const index = new Map(valueTypes.map(v => [v.apiName, new Map(v.values.map(x => [x.value, x.label]))]));
  return (valueType, value) => index.get(valueType)?.get(String(value)) ?? (value === null || value === undefined ? "—" : String(value));
}

// ------------------------------------------------------------------ primitives
function Stat({ label, value, hint, tone = "neutral" }: { label: string; value: ReactNode; hint?: ReactNode; tone?: Tone }) {
  return <div className={`fr-stat ${tone}`}><span>{label}</span><strong>{value}</strong>{hint && <small>{hint}</small>}</div>;
}

function Badge({ tone, children }: { tone: Tone; children: ReactNode }) {
  return <span className={`fr-badge ${tone}`}>{children}</span>;
}

function Meter({ label, percent, caption, tone }: { label: string; percent: number | null; caption?: ReactNode; tone?: Tone }) {
  const value = percent ?? 0;
  const resolved = tone ?? (value > 100 ? "bad" : value >= 80 ? "warn" : "good");
  return <div className="fr-meter">
    <div className="fr-meter-head"><span>{label}</span><b>{percent === null ? "—" : `${value}%`}</b></div>
    <div className="fr-meter-track"><i className={resolved} style={{ width: `${Math.min(100, Math.max(0, value))}%` }} /></div>
    {caption && <small>{caption}</small>}
  </div>;
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return <div className="fr-section"><h5>{title}</h5>{children}</div>;
}

function Deadline({ deadline }: { deadline?: Json | null }) {
  if (!deadline) return null;
  return <Badge tone={URGENCY_TONE[String(deadline.urgency)] ?? "neutral"}>{String(deadline.label)}</Badge>;
}

function Empty({ children }: { children: ReactNode }) {
  return <p className="fr-empty">{children}</p>;
}

// ---------------------------------------------------------------- per function
function RiskView({ result, label }: { result: Json; label: Labeler }) {
  const alerts = (result.anomalyAlerts as Json[] | undefined) ?? [];
  const reasons = (result.riskReasons as string[] | undefined) ?? [];
  const maintenance = (result.maintenanceReasons as string[] | undefined) ?? [];
  return <div className="fr">
    <div className="fr-headline">
      <Badge tone={RISK_TONE[String(result.riskLevel)] ?? "neutral"}>风险等级：{label("RiskLevel", result.riskLevel)}</Badge>
      <span>{reasons.length ? `${reasons.length} 条风险原因` : "未发现风险"}</span>
    </div>
    <div className="fr-stats">
      <Stat label="逾期里程碑" value={String(result.overdueMilestones ?? 0)} tone={Number(result.overdueMilestones) > 0 ? "bad" : "good"} />
      <Stat label="逾期回款节点" value={String(result.overduePaymentNodes ?? 0)} tone={Number(result.overduePaymentNodes) > 0 ? "bad" : "good"} />
      <Stat label="人工维护" value={result.maintenanceComplete ? "已完整" : "待补充"} tone={result.maintenanceComplete ? "good" : "warn"} hint={maintenance.length ? maintenance.join("、") : undefined} />
    </div>
    {alerts.length > 0 && <Section title="异常提醒">
      <ul className="fr-alerts">{alerts.map((alert, index) => <li key={`${alert.code}-${index}`} className={String(alert.severity)}>
        <div><strong>{String(alert.title)}</strong><Badge tone={alert.severity === "critical" ? "bad" : "warn"}>{alert.severity === "critical" ? "严重" : "关注"}</Badge></div>
        <p>{String(alert.detail)}</p>
      </li>)}</ul>
    </Section>}
  </div>;
}

function BudgetView({ result, label }: { result: Json; label: Labeler }) {
  const status = String(result.status ?? "");
  return <div className="fr">
    <div className="fr-headline">
      <Badge tone={HEALTH_TONE[status] ?? (status.startsWith("无上限") ? "neutral" : "warn")}>{status}</Badge>
      <span>{label("ContractType", result.contractType)} · 每人天 {String(result.hoursPerPersonDay)} 小时</span>
    </div>
    <Meter label="预算使用率" percent={percentOf(result.budgetUtilization)} caption={result.plannedPersonDays ? `已填报 ${result.reportedPersonDays} / 预算 ${result.plannedPersonDays} 人天` : "未设置预算人天"} />
    <div className="fr-stats">
      <Stat label="预算人天" value={days(result.plannedPersonDays)} hint={result.budgetHours ? `${result.budgetHours} 小时` : undefined} />
      <Stat label="已填报人天" value={days(result.reportedPersonDays)} />
      <Stat label="剩余人天" value={days(result.remainingPersonDays)} tone={Number(result.remainingPersonDays) < 0 ? "bad" : "neutral"} />
      <Stat label="客户确认人天" value={days(result.customerConfirmedPersonDays)} />
      <Stat label="确认缺口" value={days(result.confirmationGapPersonDays)} tone={Number(result.confirmationGapPersonDays) > 0 ? "warn" : "good"} />
      {result.billablePersonDays !== null && result.billablePersonDays !== undefined && <Stat label="可结算人天" value={days(result.billablePersonDays)} />}
    </div>
  </div>;
}

function PaymentView({ result }: { result: Json }) {
  const plan = (result.paymentPlan as Json | undefined) ?? {};
  const next = plan.next_node as Json | null | undefined;
  return <div className="fr">
    <div className="fr-headline">
      <Badge tone={result.contractStatus === "已结束" ? "good" : result.contractStatus ? "warn" : "neutral"}>合同{String(result.contractStatus ?? "未签约")}</Badge>
      {Number(result.overduePaymentNodes) > 0 && <Badge tone="bad">{String(result.overduePaymentNodes)} 个回款节点逾期</Badge>}
    </div>
    <div className="fr-stats">
      <Stat label="合同金额" value={money(result.contractValue)} />
      <Stat label="已开票" value={money(result.invoicedAmount)} />
      <Stat label="CRM 已回款" value={money(result.crmReceivedAmount)} />
      <Stat label="待回款" value={money(result.outstandingReceivableAmount)} tone={Number(result.outstandingReceivableAmount) > 0 ? "warn" : "neutral"} />
    </div>
    <Meter label="回款率" percent={percentOf(result.collectionRate)} tone={Number(result.collectionRate) >= 100 ? "good" : "warn"} caption="CRM 已回款 / 合同金额" />
    <Section title="回款计划">
      <div className="fr-stats">
        <Stat label="已到账节点" value={`${plan.paid_nodes ?? 0} / ${plan.total_nodes ?? 0}`} />
        <Stat label="计划回款" value={money(plan.planned_amount)} />
        <Stat label="节点已到账" value={money(plan.actual_amount)} />
      </div>
      {next ? <div className="fr-next">
        <span>下一个回款节点</span>
        <strong>{String(next.name)}</strong>
        <small>{String(next.planned_date)} · {money(next.planned_amount)} · {PAYMENT_STATUS[String(next.status)] ?? String(next.status)}</small>
        <Deadline deadline={next.deadline as Json} />
      </div> : <Empty>{plan.total_nodes ? "回款节点已全部到账" : "尚未设置回款节点"}</Empty>}
    </Section>
  </div>;
}

function HealthView({ result, label }: { result: Json; label: Labeler }) {
  const period = (result.period as Json | undefined) ?? {};
  const health = result.health as Json | null | undefined;
  const periodLabel = period.start_month === period.end_month ? String(period.start_month) : `${period.start_month} ~ ${period.end_month}`;
  if (!health) return <div className="fr"><Empty>{periodLabel} 没有该项目的健康度数据。</Empty></div>;
  const milestone = health.current_milestone as Json | null | undefined;
  const reasons = (health.risk_reasons as string[] | undefined) ?? [];
  return <div className="fr">
    <div className="fr-headline">
      <Badge tone={HEALTH_TONE[String(health.health_status)] ?? "neutral"}>{String(health.health_status)}</Badge>
      <Badge tone={HEALTH_TONE[String(health.overrun_warning)] ?? "warn"}>超支预警：{String(health.overrun_warning)}</Badge>
      <span>统计期间 {periodLabel} · {label("LifecycleStatus", health.project_status)}</span>
    </div>
    <Meter label="预算燃尽率" percent={percentOf(health.burn_rate_percent)} caption={`累计 ${health.cumulative_actual_person_days} / 预算 ${health.budget_person_days ?? "—"} 人天`} />
    <div className="fr-stats">
      <Stat label="本期投入" value={days(health.period_actual_person_days)} hint={hours(health.period_actual_hours)} />
      <Stat label="上期投入" value={days(health.previous_period_person_days ?? health.previous_month_person_days)} hint={`${String(health.monthly_trend ?? "")}${health.previous_period ? ` · 对比 ${(health.previous_period as Json).start_month} ~ ${(health.previous_period as Json).end_month}` : ""}`} />
      <Stat label="累计实际" value={days(health.cumulative_actual_person_days)} />
      <Stat label="剩余预算" value={days(health.remaining_budget_person_days)} />
      <Stat label="月均预算" value={days(health.monthly_budget_person_days)} hint={health.plan_months ? `计划 ${health.plan_months} 个月` : undefined} />
      <Stat label="参与人数" value={`${health.participant_count ?? 0} 人`} />
      <Stat label="预算耗尽预测" value={String((health.budget_exhaustion_forecast as Json | undefined)?.label ?? "—")} hint={(health.budget_exhaustion_forecast as Json | undefined)?.basis as string | undefined} />
      <Stat label="计划周期" value={`${health.planned_start ?? "—"} ~ ${health.planned_end ?? "—"}`} />
    </div>
    {milestone && <div className="fr-next">
      <span>当前里程碑</span>
      <strong>{String(milestone.name)}</strong>
      <small>计划 {String(milestone.planned_end)} · {MILESTONE_STATUS[String(milestone.status)] ?? String(milestone.status)}</small>
      <Deadline deadline={milestone.deadline as Json} />
    </div>}
    {reasons.length > 0 && <Section title="风险原因"><ul className="fr-list">{reasons.map(reason => <li key={reason}>{reason}</li>)}</ul></Section>}
  </div>;
}

function CustomerAnnualView({ result }: { result: Json }) {
  const years = (result.years as Json[] | undefined) ?? [];
  const undated = Number(result.undated_contract_count ?? 0);
  return <div className="fr">
    <div className="fr-headline">
      <Badge tone="neutral">{result.year ? `${result.year} 年` : "全部年度"}</Badge>
      <span>按合同签约年度归集；开票、回款是这些合同至今的 CRM 金额，不是该年实际到账</span>
    </div>
    {undated > 0 && <p className="fr-empty">{undated} 份合同没有签约时间，未计入任何年度。</p>}
    {years.length === 0 && <Empty>该年度没有签订的合同。</Empty>}
    {years.map(year => <Section key={String(year.year)} title={`${year.year} 年 · ${year.contract_count} 份合同 · ${year.project_count} 个项目 · ${year.customer_count} 个客户`}>
      <div className="fr-stats">
        <Stat label="新签合同额" value={money(year.signed_amount)} />
        <Stat label="累计开票" value={money(year.invoiced_amount)} hint="这些合同至今的 CRM 开票" />
        <Stat label="累计回款" value={money(year.received_amount)} hint="这些合同至今的 CRM 回款，不是该年到账" />
      </div>
      <table className="fr-table"><thead><tr><th>客户</th><th>合同</th><th>项目</th><th>新签合同额</th><th>累计开票</th><th>累计回款</th></tr></thead>
        <tbody>{((year.customers as Json[] | undefined) ?? []).map(row => <tr key={String(row.customer_name)}>
          <td>{String(row.customer_name)}</td><td>{String(row.contract_count)}</td><td>{String(row.project_count)}</td>
          <td>{money(row.signed_amount)}</td><td>{money(row.invoiced_amount)}</td><td>{money(row.received_amount)}</td>
        </tr>)}</tbody></table>
    </Section>)}
  </div>;
}

function CalendarView({ result }: { result: Json }) {
  return <div className="fr">
    <div className="fr-headline">
      <Badge tone={result.available ? "good" : "warn"}>{result.available ? (result.stale ? "节假日日历（缓存，已过期）" : "中国法定节假日日历") : "日历不可用 · 仅按周一至周五"}</Badge>
      <span>{String(result.start_date)} ~ {String(result.end_date)}</span>
    </div>
    <div className="fr-stats">
      <Stat label="工作日" value={`${result.working_days} 天`} />
      <Stat label="扣除法定节假日" value={`${result.holiday_excluded_days ?? 0} 天`} />
      <Stat label="调休补班" value={`${result.makeup_workdays ?? 0} 天`} />
      <Stat label="周期基线工时" value={hours(result.baseline_hours)} hint="工作日 × 标准每日小时数" />
    </div>
  </div>;
}

function UtilizationView({ result }: { result: Json }) {
  const assessment = result.assessment as Json | null | undefined;
  if (!assessment) return <div className="fr"><Empty>{String(result.employeeName)} 在 {String(result.period)} 没有工时记录。</Empty></div>;
  return <div className="fr">
    <div className="fr-headline">
      <Badge tone={HEALTH_TONE[String(assessment.status)] ?? "neutral"}>{String(assessment.status)}</Badge>
      <span>{String(result.employeeName)} · {String(result.period)} · 基线 {String(result.baselineHours)} 小时</span>
    </div>
    <Meter label="工时利用率" percent={percentOf(assessment.utilization_percent)} tone={HEALTH_TONE[String(assessment.status)]} caption={`填报 ${assessment.reported_hours} / 基线 ${assessment.baseline_hours} 小时`} />
    <div className="fr-stats">
      <Stat label="填报工时" value={hours(assessment.reported_hours)} hint={days(assessment.reported_person_days)} />
      <Stat label="确认工时" value={hours(assessment.approved_hours)} hint={days(assessment.approved_person_days)} />
      <Stat label="确认缺口" value={hours(assessment.confirmation_gap_hours)} tone={Number(assessment.confirmation_gap_hours) > 0 ? "warn" : "good"} />
      <Stat label="加班" value={hours(assessment.overtime_hours)} />
      <Stat label="记录数" value={`${assessment.entry_count ?? 0} 条`} />
      <Stat label="参与项目" value={`${assessment.project_count ?? 0} 个`} />
    </div>
  </div>;
}

function Distribution({ title, counts, valueType, label }: { title: string; counts: Record<string, number>; valueType: string; label: Labeler }) {
  const entries = Object.entries(counts).sort((a, b) => b[1] - a[1]);
  const max = Math.max(1, ...entries.map(([, count]) => count));
  return <Section title={title}>
    {entries.length ? <ul className="fr-bars">{entries.map(([key, count]) => <li key={key}>
      <span>{label(valueType, key)}</span>
      <i><em style={{ width: `${(count / max) * 100}%` }} /></i>
      <b>{count}</b>
    </li>)}</ul> : <Empty>暂无数据</Empty>}
  </Section>;
}

function PortfolioView({ result, label }: { result: Json; label: Labeler }) {
  return <div className="fr">
    <div className="fr-stats">
      <Stat label="项目数" value={String(result.total_projects ?? 0)} />
      <Stat label="合同总额" value={money(result.contract_value)} />
      <Stat label="已回款" value={money(result.received_amount)} />
      <Stat label="严重风险" value={String(result.critical_projects ?? 0)} tone={Number(result.critical_projects) > 0 ? "bad" : "good"} />
      <Stat label="需关注" value={String(result.warning_projects ?? 0)} tone={Number(result.warning_projects) > 0 ? "warn" : "good"} />
      <Stat label="健康" value={String(result.healthy_projects ?? 0)} tone="good" />
      <Stat label="维护未完成" value={String(result.incomplete_maintenance ?? 0)} tone={Number(result.incomplete_maintenance) > 0 ? "warn" : "good"} />
    </div>
    <div className="fr-columns">
      <Distribution title="按合同类型" counts={(result.by_contract_type as Record<string, number>) ?? {}} valueType="ContractType" label={label} />
      <Distribution title="按生命周期" counts={(result.by_lifecycle_status as Record<string, number>) ?? {}} valueType="LifecycleStatus" label={label} />
    </div>
  </div>;
}

const OUTCOME_TONE: Record<string, Tone> = { won: "good", open: "warn", lost: "bad", abandoned: "bad", na: "neutral" };

function InvestmentProfileView({ result, label }: { result: Json; label: Labeler }) {
  const flags = (result.data_flags as string[] | undefined) ?? [];
  const outcome = String(result.outcome);
  return <div className="fr">
    <div className="fr-headline">
      <Badge tone={OUTCOME_TONE[outcome] ?? "neutral"}>{label("OpportunityOutcome", outcome)}</Badge>
      <span>{label("CodeRegistration", result.registration)}{result.conversion_date ? ` · 签约日 ${result.conversion_date}` : ""}{result.outcome_source ? ` · 结论来源：${result.outcome_source}` : ""}</span>
    </div>
    <div className="fr-stats">
      <Stat label="总投入" value={days(result.total_person_days)} hint={result.estimated_cost ? money(result.estimated_cost) : undefined} />
      {outcome === "won" && <>
        <Stat label="获客成本" value={days(result.presales_converted_person_days)} hint={result.cost_to_win_share != null ? `占比 ${result.cost_to_win_share}%` : undefined} />
        <Stat label="履约成本" value={days(result.delivery_person_days)} tone="good" />
      </>}
      {outcome === "open" && <>
        <Stat label="在途投入" value={days(result.at_risk_person_days)} tone="warn" hint={result.win_probability != null ? `赢率 ${result.win_probability}%` : "未填写赢率"} />
        <Stat label="预期沉没" value={days(result.expected_sunk_person_days)} tone="warn" hint={String(result.probability_source ?? "")} />
        <Stat label="在途账龄" value={result.presales_age_days != null ? `${result.presales_age_days} 天` : "—"} tone={result.stalled ? "bad" : "neutral"} hint={result.stalled ? "疑似停滞" : undefined} />
      </>}
      {(outcome === "lost" || outcome === "abandoned") && <Stat label="沉没成本" value={days(result.sunk_person_days)} tone="bad" hint={String(result.outcome_reason ?? "")} />}
      {Number(result.unregistered_person_days) > 0 && <Stat label="未建档投入" value={days(result.unregistered_person_days)} tone="warn" />}
      {Number(result.internal_person_days) > 0 && <Stat label="内部投入" value={days(result.internal_person_days)} />}
      <Stat label="投入人数" value={`${result.employee_count ?? 0} 人`} hint={`${result.first_work_date ?? "—"} ~ ${result.last_work_date ?? "—"}`} />
    </div>
    {flags.length > 0 && <Section title="数据提示"><ul className="fr-list">{flags.map(flag => <li key={flag}>{flag}</li>)}</ul></Section>}
  </div>;
}

function InvestmentAnalysisView({ result }: { result: Json }) {
  const totals = (result.totals as Json | undefined) ?? {};
  const ratios = (result.ratios as Json | undefined) ?? {};
  const pct = (value: unknown) => value == null ? "—" : `${value}%`;
  return <div className="fr">
    <div className="fr-stats">
      <Stat label="总投入" value={days(totals.total_person_days)} />
      <Stat label="履约成本" value={days(totals.delivery_person_days)} tone="good" />
      <Stat label="获客成本" value={days(totals.presales_converted_person_days)} />
      <Stat label="在途投入" value={days(totals.at_risk_person_days)} tone="warn" hint={`预期沉没 ${days(totals.expected_sunk_person_days)}`} />
      <Stat label="已确认沉没" value={days(totals.sunk_person_days)} tone="bad" hint={`沉没成本率 ${pct(ratios.sunk_rate)}`} />
      <Stat label="未建档投入" value={days(totals.unregistered_person_days)} tone="warn" />
      <Stat label="赢单率" value={pct(ratios.win_rate_by_count)} hint={`按投入加权 ${pct(ratios.win_rate_by_effort)}`} />
      <Stat label="平均获单成本" value={days(ratios.average_cost_to_win_person_days)} />
    </div>
  </div>;
}

const pct = (value: unknown) => value === null || value === undefined ? "—" : `${value}%`;

function Notes({ items }: { items: unknown }) {
  const list = (items as string[] | undefined) ?? [];
  return list.length ? <ul className="fr-list">{list.map(item => <li key={item}>{item}</li>)}</ul> : null;
}

function ResourceHealthView({ result }: { result: Json }) {
  const summary = (result.summary as Record<string, number> | undefined) ?? {};
  // A first month is not a finding (D14): counted in the summary, not listed among the people needing attention.
  const rows = ((result.data as Json[] | undefined) ?? []).filter(row => row.status !== "healthy" && row.status !== "first_month");
  const labels: Record<string, string> = { overloaded: "持续过载", fatigue: "疲劳风险", fragmented: "碎片化", idle: "闲置", first_month: "首月", healthy: "健康" };
  return <div className="fr">
    <div className="fr-headline"><span>{String(result.month ?? "")} 资源健康</span></div>
    <div className="fr-stats">{Object.keys(labels).map(key => <Stat key={key} label={labels[key]} value={`${summary[key] ?? 0} 人`} tone={RESOURCE_TONE[key]} />)}</div>
    {rows.length > 0 && <Section title="需要关注的员工">
      <ul className="fr-alerts">{rows.slice(0, 20).map(row => <li key={String(row.employee_id ?? row.employee_name)} className={RESOURCE_TONE[String(row.status)] === "bad" ? "critical" : "warning"}>
        <div><strong>{String(row.employee_name)}</strong><Badge tone={RESOURCE_TONE[String(row.status)] ?? "neutral"}>{labels[String(row.status)] ?? String(row.status)}</Badge></div>
        <p>{((row.reasons as string[] | undefined) ?? []).join("；")}</p>
      </li>)}</ul>
    </Section>}
  </div>;
}

function DepartmentReviewView({ result }: { result: Json }) {
  const kpis = (result.kpis as Record<string, Json> | undefined) ?? {};
  const signals = (result.signals as Json[] | undefined) ?? [];
  return <div className="fr">
    <div className="fr-headline"><span>{String(result.year ?? "")} 年 · 截至 {String(result.month ?? "")} · 在岗 {String(result.headcount ?? 0)} 人</span></div>
    <div className="fr-stats">{Object.entries(kpis).map(([key, kpi]) => {
      const [statusLabel, tone] = PLAN_STATUS[String(kpi.status)] ?? [String(kpi.status), "neutral" as Tone];
      return <Stat key={key} label={String(kpi.label)} value={kpi.actual == null ? "—" : String(kpi.actual)} tone={tone}
        hint={`${statusLabel}${kpi.target != null ? ` · 目标 ${kpi.target}` : ""}${kpi.completion_percent == null ? "" : kpi.measure === "cumulative" ? ` · 完成 ${kpi.completion_percent}% / 时间 ${kpi.time_progress_percent}%` : ` · 达成 ${kpi.completion_percent}%`}`} />;
    })}</div>
    <Section title="策略信号（按优先级，只作建议）">
      <ul className="fr-alerts">{signals.map(signal => <li key={String(signal.signal)} className={signal.signal === "hold" ? "" : Number(signal.priority) <= 2 ? "critical" : "warning"}>
        <div><strong>{String(signal.priority)}. {String(signal.label)}</strong></div>
        <p>{((signal.evidence as string[] | undefined) ?? []).join("；")}</p>
      </li>)}</ul>
    </Section>
  </div>;
}

// CQ-W5–W7 (requirements baseline D18–D21): capacity, demand and gap per month; demand is not allocated to people.
function ResourceForecastView({ result }: { result: Json }) {
  const months = (result.months as Json[] | undefined) ?? [];
  const projects = (result.projects as Json[] | undefined) ?? [];
  const gaps = (result.data_gaps as Json[] | undefined) ?? [];
  const assumptions = (result.assumptions as Json | undefined) ?? {};
  const method: Record<string, string> = { budget: "预算剩余", run_rate: "近期月均" };
  return <div className="fr">
    <div className="fr-headline"><span>{String(result.period_start ?? "")} 至 {String(result.period_end ?? "")} · 在岗 {String(result.headcount ?? 0)} 人（{String(result.headcount_month ?? "—")} 有工时）</span></div>
    <div className="fr-stats">{months.map(month => {
      const short = month.status === "shortage";
      return <Stat key={String(month.month)} label={String(month.month)} tone={short ? "bad" : "good"}
        value={short ? `缺口 ${days(month.gap_person_days)}` : `富余 ${days(String(-Number(month.gap_person_days)))}`}
        hint={`产能 ${days(month.capacity_person_days)} · 需求 ${days(month.demand_person_days)} · ${String(month.working_days)} 个工作日`} />;
    })}</div>
    {months.some(month => ((month.top_projects as Json[] | undefined) ?? []).length > 0) && <Section title="各月拉动需求的项目">
      <ul className="fr-alerts">{months.filter(month => ((month.top_projects as Json[] | undefined) ?? []).length > 0).map(month =>
        <li key={String(month.month)} className={month.status === "shortage" ? "critical" : ""}>
          <div><strong>{String(month.month)}</strong></div>
          <p>{((month.top_projects as Json[]) ?? []).map(p => `${String(p.project_number)} ${days(p.person_days)}`).join("；")}</p>
        </li>)}</ul>
    </Section>}
    <Section title="项目需求（标准人天）">
      {projects.length ? <table className="fr-table"><thead><tr><th>项目</th><th>口径</th>{months.map(m => <th key={String(m.month)}>{String(m.month)}</th>)}<th>合计</th></tr></thead>
        <tbody>{projects.map(p => <tr key={String(p.project_number)}><td>{String(p.project_number)}</td><td title={String(p.basis ?? "")}>{method[String(p.method)] ?? String(p.method)}</td>
          {months.map(m => <td key={String(m.month)}>{String(((p.months as Json) ?? {})[String(m.month)] ?? "0.00")}</td>)}<td>{String(p.total)}</td></tr>)}</tbody></table>
        : <Empty>测算期内没有可测算的项目需求</Empty>}
    </Section>
    {gaps.length > 0 && <Section title="无法测算的项目（数据缺口）">
      <ul className="fr-alerts">{gaps.map(gap => <li key={`${String(gap.project_number)}-${String(gap.reason)}`} className="warning">
        <div><strong>{String(gap.project_number)}</strong></div><p>{String(gap.reason)}{gap.remaining_person_days ? `（剩余 ${days(gap.remaining_person_days)}）` : ""}</p>
      </li>)}</ul>
    </Section>}
    <Empty>{`可用产能：${String(assumptions.capacity ?? "")}（最低利用率 ${String(assumptions.minimum_utilization_percent ?? "—")}%）。需求：${String(assumptions.demand ?? "")}。${String(result.limitations ?? "")}`}</Empty>
  </div>;
}

function MaintenanceWorklistView({ result }: { result: Json }) {
  const summary = (result.summary as Json[] | undefined) ?? [];
  const rows = (result.data as Json[] | undefined) ?? [];
  return <div className="fr">
    <div className="fr-headline">
      <Badge tone={rows.length ? "warn" : "good"}>{rows.length ? `${rows.length} 个对象待维护` : "全部已维护"}</Badge>
      <span>按元数据声明的维护要求推导</span>
    </div>
    <div className="fr-stats">{summary.map(item => <Stat key={`${item.objectType}-${item.requirement}`} label={String(item.label)}
      value={`${String(item.missing)} 待维护`} tone={Number(item.missing) > 0 ? "warn" : "good"}
      hint={`已维护 ${String(item.maintained)} / 适用 ${String(item.applicable)}`} />)}</div>
    {rows.length > 0 && <Section title="待维护对象">
      <ul className="fr-alerts">{rows.slice(0, 30).map(row => <li key={`${row.objectType}-${row.primaryKey}`} className="warning">
        <div><strong>{String(row.primaryKey)} · {String(row.title ?? "")}</strong></div>
        <p>{((row.pending as string[] | undefined) ?? []).join("、")}</p>
      </li>)}</ul>
    </Section>}
  </div>;
}

// projectOverview (user 2026-11-10: 「项目全景」 showed every raw field in a grid): the headline, the numbers that
// matter, and the lists behind them; lists that are done fold into one line (feedback: collapse finished content).
const INTERVENTION_STATUS: Record<string, [string, Tone]> = { open: ["待处理", "warn"], in_progress: ["处理中", "warn"], closed: ["已关闭", "good"] };

function Fold({ title, count, open = false, children }: { title: string; count?: ReactNode; open?: boolean; children: ReactNode }) {
  return <details className="fr-fold" open={open}>
    <summary><span>{title}</span>{count !== undefined && <em>{count}</em>}</summary>
    <div>{children}</div>
  </details>;
}

function ProjectOverviewView({ result, label }: { result: Json; label: Labeler }) {
  // AI alerts (AI_RISK_FLAG, AI_PROPOSAL_PENDING) are listed under AI 关注事项, not beside the rules' reasons (D53).
  const alerts = ((result.anomaly_alerts as Json[] | undefined) ?? []).filter(alert => !String(alert.code).startsWith("AI_"));
  const reasons = (result.risk_reasons as string[] | undefined) ?? [];
  const attention = (result.ai_attention as string[] | undefined) ?? [];
  const milestones = (result.milestones as Json[] | undefined) ?? [];
  const nodes = (result.payment_nodes as Json[] | undefined) ?? [];
  const interventions = (result.interventions as Json[] | undefined) ?? [];
  const people = (result.pmo_resource_rows as Json[] | undefined) ?? [];
  const months = (result.monthly_time_entries as Json[] | undefined) ?? [];
  const todos = ((result.todo_events as Json[] | undefined) ?? []).filter(todo => !todo.completed);
  const maintenance = (result.maintenance_reasons as string[] | undefined) ?? [];
  const resources = (result.pmo_resource_summary as Json | undefined) ?? {};
  const openInterventions = interventions.filter(item => item.status !== "closed");
  return <div className="fr">
    <div className="fr-headline">
      <Badge tone={RISK_TONE[String(result.risk_level)] ?? "neutral"}>风险等级：{label("RiskLevel", result.risk_level)}</Badge>
      <span>{String(result.subject ?? "")} · {label("LifecycleStatus", result.lifecycle_status)} · {label("ContractType", result.contract_type)} · 项目经理 {String(result.project_manager ?? "未指定")} · {String(result.planned_start ?? "—")} ~ {String(result.planned_end ?? "—")}</span>
    </div>
    <div className="fr-stats">
      <Stat label="合同金额" value={money(result.contract_value)} />
      <Stat label="已回款" value={money(result.received_amount)} hint={`CRM 已回款 ${money(result.crm_received_amount)}`} />
      <Stat label="待回款" value={money(result.outstanding_receivable_amount)} tone={Number(result.outstanding_receivable_amount) > 0 ? "warn" : "neutral"} />
      <Stat label="里程碑" value={`${result.milestone_completed ?? 0} / ${result.milestone_total ?? 0}`} hint={Number(result.overdue_milestones) ? `${result.overdue_milestones} 个逾期` : "无逾期"} tone={Number(result.overdue_milestones) ? "bad" : "neutral"} />
      <Stat label="管理干预" value={`${openInterventions.length} 项未关闭`} hint={Number(result.overdue_interventions) ? `${result.overdue_interventions} 项逾期` : undefined} tone={Number(result.overdue_interventions) ? "bad" : "neutral"} />
      <Stat label="待办" value={`${result.pending_todos ?? 0} 项`} hint={result.next_todo ? `下一项：${(result.next_todo as Json).name}（${(result.next_todo as Json).label}）` : undefined} />
    </div>
    <Meter label="预算使用率" percent={percentOf(result.budget_utilization)} caption={result.planned_person_days ? `已填报 ${result.reported_person_days} / 预算 ${result.planned_person_days} 人天 · 客户确认 ${result.customer_confirmed_person_days} 人天` : "未设置预算人天"} />
    {(reasons.length > 0 || alerts.length > 0) && <Section title="风险原因（规则）与提醒">
      <ul className="fr-alerts">{alerts.map((alert, index) => <li key={`${alert.code}-${index}`} className={String(alert.severity)}>
        <div><strong>{String(alert.title)}</strong><Badge tone={alert.severity === "critical" ? "bad" : "warn"}>{alert.severity === "critical" ? "严重" : "关注"}</Badge></div>
        <p>{String(alert.detail)}</p>
      </li>)}</ul>
    </Section>}
    {attention.length > 0 && <Section title="AI 关注事项"><ul className="fr-list">{attention.map(line => <li key={line}>{line}</li>)}</ul></Section>}
    {maintenance.length > 0 && <Section title="待人工维护"><ul className="fr-list">{maintenance.map(item => <li key={item}>{item}</li>)}</ul></Section>}
    <Fold title="管理干预" count={interventions.length ? `${openInterventions.length} 项未关闭 / 共 ${interventions.length} 项` : "无"} open={openInterventions.length > 0}>
      {interventions.length ? <table className="fr-table"><thead><tr><th>事项</th><th>类型</th><th>状态</th><th>负责人</th><th>截止</th></tr></thead>
        <tbody>{interventions.map(item => {
          const [text, tone] = INTERVENTION_STATUS[String(item.status)] ?? [String(item.status), "neutral" as Tone];
          return <tr key={String(item.intervention_id)}><td>{String(item.title)}{item.conclusion ? <small className="fr-sub">结论：{String(item.conclusion)}</small> : null}</td>
            <td>{label("InterventionType", item.intervention_type)}</td><td><Badge tone={item.overdue ? "bad" : tone}>{item.overdue ? "已逾期" : text}</Badge></td>
            <td>{String(item.owner ?? "—")}</td><td>{String(item.due_date ?? "—")}</td></tr>;
        })}</tbody></table> : <Empty>没有管理干预</Empty>}
    </Fold>
    <Fold title="里程碑与回款节点" count={`里程碑 ${result.milestone_completed ?? 0}/${milestones.length} 完成 · 回款节点 ${(result.payment_summary as Json | undefined)?.paid_nodes ?? 0}/${nodes.length} 到账`} open={todos.length > 0 && Number(result.overdue_milestones) + Number(result.overdue_payment_nodes) > 0}>
      <table className="fr-table"><thead><tr><th>里程碑</th><th>计划完成</th><th>状态</th><th>期限</th></tr></thead>
        <tbody>{milestones.map(item => <tr key={String(item.milestone_id)}><td>{String(item.name)}</td><td>{String(item.planned_end ?? item.planned_date ?? "—")}</td>
          <td>{label("MilestoneStatus", item.status)}</td><td><Deadline deadline={item.deadline as Json} /></td></tr>)}</tbody></table>
      <table className="fr-table"><thead><tr><th>回款节点</th><th>计划日期</th><th>计划金额</th><th>状态</th><th>到账</th></tr></thead>
        <tbody>{nodes.map(item => <tr key={String(item.payment_node_id)}><td>{String(item.name)}</td><td>{String(item.planned_date ?? "—")}</td><td>{money(item.planned_amount)}</td>
          <td>{PAYMENT_STATUS[String(item.status)] ?? String(item.status)}</td><td>{item.actual_date ? `${item.actual_date} · ${money(item.actual_amount)}` : "—"}</td></tr>)}</tbody></table>
    </Fold>
    <Fold title="人员投入" count={`${resources.participant_count ?? people.length} 人 · ${resources.reported_person_days ?? "—"} 人天`}>
      <table className="fr-table"><thead><tr><th>人员</th><th>填报人天</th><th>加班人天</th><th>占项目投入</th><th>活跃月数</th></tr></thead>
        <tbody>{people.map(row => <tr key={String(row.employee_id ?? row.employee_name)}><td>{String(row.employee_name)}</td><td>{String(row.reported_person_days)}</td>
          <td>{String(row.overtime_person_days)}</td><td>{pct(row.project_effort_share_percent)}</td><td>{String(row.active_month_count ?? "—")}</td></tr>)}</tbody></table>
    </Fold>
    <Fold title="月度工时" count={`${months.length} 个月`}>
      <table className="fr-table"><thead><tr><th>月份</th><th>填报人天</th><th>确认人天</th><th>确认缺口</th><th>加班人天</th><th>记录数</th></tr></thead>
        <tbody>{months.map(row => <tr key={String(row.month)}><td>{String(row.month)}</td><td>{String(row.reported_person_days)}</td><td>{String(row.approved_person_days)}</td>
          <td>{String(row.confirmation_gap_person_days)}</td><td>{String(row.overtime_person_days)}</td><td>{String(row.entry_count)}</td></tr>)}</tbody></table>
    </Fold>
  </div>;
}

// aiSuggestionOverview (user 2026-11-10: 「AI 提案与标记」 was four raw columns of keys): what waits for approval, the
// AI risk flags in force, the interventions that came from AI and whether their basis still holds, what was decided.
function basisLines(basis: unknown): string[] {
  return ((basis as Json[] | undefined) ?? []).map(fact => String(fact.text ?? "")).filter(Boolean);
}

function AiOverviewView({ result }: { result: Json }) {
  const pending = (result.pending as Json[] | undefined) ?? [];
  const flags = (result.riskFlags as Json[] | undefined) ?? [];
  const basis = Object.entries((result.interventionBasis as Record<string, Json> | undefined) ?? {});
  const recent = (result.recent as Json[] | undefined) ?? [];
  return <div className="fr">
    <div className="fr-headline">
      <Badge tone={pending.length ? "warn" : "good"}>{pending.length ? `${pending.length} 条待批准` : "没有待批准的提案"}</Badge>
      <span>有效 AI 风险标记 {flags.length} 条 · 来自 AI 的干预 {basis.length} 项 · 近 30 天处理 {recent.length} 条</span>
    </div>
    {pending.length > 0 && <Section title="待批准的 AI 提案">
      <ul className="fr-alerts">{pending.map(item => <li key={String(item.proposalId)} className="warning">
        <div><strong>{String(item.title)}</strong><Badge tone="warn">{String(item.kindLabel)}</Badge></div>
        <p>{String(item.submittedBy)} 提交于 {String(item.submittedAt ?? "").slice(0, 16).replace("T", " ")} · {String(item.expiresOn)} 前处理{item.projectNumber ? ` · ${item.projectNumber}` : ""}{item.canApprove ? "" : " · 你无权批准"}</p>
      </li>)}</ul>
    </Section>}
    {flags.length > 0 && <Section title="有效的 AI 风险标记">
      <ul className="fr-alerts">{flags.map(flag => <li key={String(flag.flagId)} className={flag.basisHolds ? "warning" : ""}>
        <div><strong>{String(flag.projectNumber)} · {String(flag.title)}</strong><Badge tone={flag.basisHolds ? "warn" : "neutral"}>{flag.basisHolds ? "依据成立" : "依据已不成立"}</Badge></div>
        <p>{String(flag.approvedBy)} 批准 · 有效至 {String(flag.validUntil)}</p>
        <details className="fr-more"><summary>说明与依据</summary>
          <p>{String(flag.detail ?? "")}</p>
          <ul className="fr-list">{basisLines(flag.basis).map(line => <li key={line}>{line}</li>)}</ul>
        </details>
      </li>)}</ul>
    </Section>}
    {basis.length > 0 && <Section title="来自 AI 的管理干预">
      <ul className="fr-alerts">{basis.map(([interventionId, item]) => <li key={interventionId} className={item.basisHolds ? "" : "warning"}>
        <div><strong>{String(item.strategyTitle ?? interventionId)}</strong><Badge tone={item.basisHolds ? "good" : "warn"}>{item.basisHolds ? "依据仍成立" : "依据已变化"}</Badge></div>
        <p>{item.strategyTitle ? `部门策略（AI）· ${item.strategyStatus === "active" ? "有效" : String(item.strategyStatus)}` : "管理干预提案"} · 干预 {interventionId}</p>
        {item.basisNote ? <details className="fr-more"><summary>依据复核</summary>
          <ul className="fr-list">{String(item.basisNote).split("；").filter(Boolean).map(line => <li key={line}>{line}</li>)}</ul>
        </details> : null}
      </li>)}</ul>
    </Section>}
    <Fold title="近 30 天已处理" count={recent.length ? `${recent.length} 条` : "无"}>
      {recent.length ? <table className="fr-table"><thead><tr><th>提案</th><th>类型</th><th>结果</th><th>处理人</th><th>处理时间</th></tr></thead>
        <tbody>{recent.map(item => <tr key={String(item.proposalId)}><td>{String(item.title)}{item.projectNumber ? <small className="fr-sub">{String(item.projectNumber)}</small> : null}</td>
          <td>{String(item.kindLabel)}</td><td>{String(item.statusLabel)}</td><td>{String(item.decidedBy ?? "—")}</td>
          <td>{String(item.decidedAt ?? "").slice(0, 16).replace("T", " ") || "—"}</td></tr>)}</tbody></table> : <Empty>近 30 天没有处理过的提案</Empty>}
    </Fold>
  </div>;
}

function GenericValue({ value }: { value: unknown }): ReactNode {
  if (value === null || value === undefined || value === "") return <span className="muted">—</span>;
  if (Array.isArray(value)) {
    if (!value.length) return <span className="muted">—</span>;
    if (value.every(item => typeof item !== "object")) return <>{value.join("、")}</>;
    return <div className="fr-generic-list">{value.map((item, index) => <GenericObject key={index} value={item as Json} />)}</div>;
  }
  if (typeof value === "object") return <GenericObject value={value as Json} />;
  if (typeof value === "boolean") return <>{value ? "是" : "否"}</>;
  return <>{String(value)}</>;
}

function GenericObject({ value }: { value: Json }) {
  return <dl className="fr-generic">{Object.entries(value).map(([key, item]) => <div key={key}>
    <dt>{GENERIC_LABELS[key] ?? key}</dt><dd><GenericValue value={item} /></dd>
  </div>)}</dl>;
}

export function FunctionResultView({ outputType, result, valueTypes }: { outputType: string; result: unknown; valueTypes: OntologyValueType[] }) {
  if (result === null || result === undefined) return <div className="fr"><Empty>函数没有返回结果。</Empty></div>;
  if (typeof result !== "object" || Array.isArray(result)) return <div className="fr"><GenericValue value={result} /></div>;
  const label = makeLabeler(valueTypes);
  const data = result as Json;
  switch (outputType) {
    case "RiskAssessment": return <RiskView result={data} label={label} />;
    case "BudgetStatus": return <BudgetView result={data} label={label} />;
    case "PaymentStatus": return <PaymentView result={data} />;
    case "ProjectHealth": return <HealthView result={data} label={label} />;
    case "EmployeeUtilization": return <UtilizationView result={data} />;
    case "PortfolioSummary": return <PortfolioView result={data} label={label} />;
    case "InvestmentProfile": return <InvestmentProfileView result={data} label={label} />;
    case "InvestmentAnalysis": return <InvestmentAnalysisView result={data} />;
    case "WorkingCalendar": return <CalendarView result={data} />;
    case "CustomerAnnualSummary": return <CustomerAnnualView result={data} />;
    case "ResourceHealth": return <ResourceHealthView result={data} />;
    case "ResourceForecast": return <ResourceForecastView result={data} />;
    case "MaintenanceWorklist": return <MaintenanceWorklistView result={data} />;
    case "DepartmentOperatingReview": return <DepartmentReviewView result={data} />;
    case "ProjectOverview": return <ProjectOverviewView result={data} label={label} />;
    case "AiSuggestionOverview": return <AiOverviewView result={data} />;
    default: return <div className="fr"><GenericObject value={data} /></div>;
  }
}

export function RawJson({ value }: { value: unknown }) {
  return <details className="fr-raw">
    <summary>查看原始 JSON</summary>
    <pre>{JSON.stringify(value, null, 2)}</pre>
  </details>;
}
