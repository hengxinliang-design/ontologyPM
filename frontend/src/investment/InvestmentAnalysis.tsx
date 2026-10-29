import { Fragment, useCallback, useEffect, useMemo, useState } from "react";
import { ApiError } from "../api";
import { applyAction } from "../ontology/api";
import { fetchInvestmentAnalysis, updateInvestmentSettings } from "./api";
import "./investment.css";
import type { CostObjectInvestment, CostPhase, InvestmentAnalysis as Analysis, InvestmentSettings } from "./types";

// Fixed categorical order (reference palette slots 1–6, validated for adjacent
// CVD separation). Color follows the phase, never its rank.
export const PHASE_ORDER: CostPhase[] = ["delivery", "presales_converted", "at_risk", "sunk", "internal", "unregistered"];
export const PHASE_LABEL: Record<CostPhase, string> = {
  delivery: "履约成本", presales_converted: "获客成本", at_risk: "在途投入",
  sunk: "沉没成本", internal: "内部投入", unregistered: "未建档投入"
};
const PHASE_HINT: Record<CostPhase, string> = {
  delivery: "赢单项目签约日及之后的投入",
  presales_converted: "赢单项目签约日之前的售前投入",
  at_risk: "进行中商机的投入（风险敞口）",
  sunk: "已丢单 / 已放弃商机的投入",
  internal: "内部代码（如 TSG999）的投入",
  unregistered: "CRM 中没有记录的项目代码"
};
const OUTCOME_LABEL: Record<CostObjectInvestment["outcome"], string> = { won: "已赢单", open: "进行中", lost: "已丢单", abandoned: "已放弃", na: "—" };
const REASONS = ["竞争失败", "客户取消", "预算不足", "主动放弃", "其他"];

export function num(value: string | number | null | undefined): number {
  const parsed = Number(value ?? 0);
  return Number.isFinite(parsed) ? parsed : 0;
}

export function days(value: string | null | undefined): string {
  return value == null ? "—" : `${num(value).toLocaleString("zh-CN", { minimumFractionDigits: 1, maximumFractionDigits: 1 })} 人天`;
}

function pct(value: number | null | undefined): string {
  return value == null ? "—" : `${value}%`;
}

function money(value: string | null | undefined): string | null {
  return value == null ? null : `¥${num(value).toLocaleString("zh-CN", { maximumFractionDigits: 0 })}`;
}

function errorMessage(err: unknown, fallback: string): string {
  return err instanceof Error ? err.message : fallback;
}

export function InvestmentAnalysisPage({ canSeeCost, canEditOutcome }: { canSeeCost: boolean; canEditOutcome: boolean }) {
  const [mode, setMode] = useState<"all" | "range">("all");
  const [startMonth, setStartMonth] = useState("");
  const [endMonth, setEndMonth] = useState("");
  const [analysis, setAnalysis] = useState<Analysis | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [showSettings, setShowSettings] = useState(false);

  const load = useCallback(async () => {
    setLoading(true); setError(null);
    try {
      setAnalysis(await fetchInvestmentAnalysis(mode === "range" ? { startMonth: startMonth || undefined, endMonth: endMonth || undefined } : {}));
    } catch (err) {
      setError(errorMessage(err, "投入分析加载失败"));
    } finally {
      setLoading(false);
    }
  }, [mode, startMonth, endMonth]);

  useEffect(() => { void load(); }, [load]);

  return <div className="investment">
    <section className="panel investment-intro">
      <div className="panel-head">
        <div>
          <h2>投入与沉没成本</h2>
          <p>以项目代码为成本对象归集全部工时（单位：人天）。按商机结论把投入分为获客、履约、在途与沉没成本——这是管理口径，用于复盘获单效率；会计上售前人工仍按当期费用处理。</p>
        </div>
        <div className="investment-period">
          <select value={mode} aria-label="统计期间" onChange={e => setMode(e.target.value as "all" | "range")}>
            <option value="all">全部期间</option><option value="range">按月份区间</option>
          </select>
          {mode === "range" && <>
            <input type="month" aria-label="开始月份" value={startMonth} onChange={e => setStartMonth(e.target.value)} />
            <span>至</span>
            <input type="month" aria-label="结束月份" value={endMonth} onChange={e => setEndMonth(e.target.value)} />
          </>}
          <button type="button" className="button secondary" onClick={() => setShowSettings(value => !value)}>{showSettings ? "收起设置" : "分析设置"}</button>
        </div>
      </div>
      {showSettings && analysis && <SettingsForm settings={analysis.settings} canSeeCost={canSeeCost} saved={() => void load()} />}
    </section>

    {error && <div className="error-banner">{error}</div>}
    {loading && !analysis ? <section className="panel"><p className="empty">正在计算投入结构…</p></section> : analysis && <>
      <KpiGrid analysis={analysis} />
      <section className="panel">
        <div className="panel-head"><div><h2>投入结构</h2><p>{analysis.period.start_month ? `${analysis.period.start_month} 至 ${analysis.period.end_month}` : "全部期间"} · 共 {days(analysis.totals.total_person_days)}；悬停色块查看明细</p></div></div>
        <StructureBar analysis={analysis} />
        <MonthlyChart analysis={analysis} />
      </section>
      <OpenOpportunities analysis={analysis} canEdit={canEditOutcome} changed={() => void load()} />
      <SunkReview analysis={analysis} canEdit={canEditOutcome} changed={() => void load()} />
      <div className="investment-columns">
        <WonProjects items={analysis.won_items} />
        <Unregistered items={analysis.unregistered} canEdit={canEditOutcome} changed={() => void load()} />
      </div>
    </>}
  </div>;
}

function KpiGrid({ analysis }: { analysis: Analysis }) {
  const { totals, ratios } = analysis;
  const tiles: Array<{ label: string; value: string; hint?: string | null; tone?: string }> = [
    { label: "总投入", value: days(totals.total_person_days), hint: money(totals.total_cost) },
    { label: "履约成本", value: days(totals.delivery_person_days), hint: `有效投入率 ${pct(ratios.effective_investment_share)}` },
    { label: "获客成本", value: days(totals.presales_converted_person_days), hint: `平均每单 ${days(ratios.average_cost_to_win_person_days)}` },
    { label: "在途投入", value: days(totals.at_risk_person_days), hint: `${ratios.open_count} 个进行中商机${totals.at_risk_cost ? ` · ${money(totals.at_risk_cost)}` : ""}`, tone: "warn" },
    { label: "预期沉没", value: days(totals.expected_sunk_person_days), hint: "在途投入 ×（1 − 赢率）", tone: "warn" },
    { label: "已确认沉没", value: days(totals.sunk_person_days), hint: `沉没成本率 ${pct(ratios.sunk_rate)}${totals.sunk_cost ? ` · ${money(totals.sunk_cost)}` : ""}`, tone: "bad" },
    { label: "未建档投入", value: days(totals.unregistered_person_days), hint: `${ratios.unregistered_count} 个代码待在 CRM 建档`, tone: "warn" },
    { label: "赢单率", value: pct(ratios.win_rate_by_count), hint: `按投入加权 ${pct(ratios.win_rate_by_effort)} · 赢 ${ratios.won_count} / 丢 ${ratios.lost_count}` }
  ];
  return <section className="investment-kpis">{tiles.map(tile => <article key={tile.label} className={`investment-kpi ${tile.tone ?? ""}`}>
    <span>{tile.label}</span><strong>{tile.value}</strong>{tile.hint && <small>{tile.hint}</small>}
  </article>)}</section>;
}

function StructureBar({ analysis }: { analysis: Analysis }) {
  const total = num(analysis.totals.total_person_days);
  const byPhase = new Map(analysis.structure.map(item => [item.phase, item]));
  return <div className="structure">
    <div className="structure-bar" role="img" aria-label="投入结构">
      {total > 0 ? PHASE_ORDER.map(phase => {
        const item = byPhase.get(phase);
        const value = num(item?.person_days);
        if (value <= 0) return null;
        return <span key={phase} className={`seg phase-${phase}`} style={{ flexGrow: value }}
          title={`${PHASE_LABEL[phase]}：${days(item?.person_days)}（${pct(item?.share)}）`} />;
      }) : <span className="structure-empty">该期间没有工时</span>}
    </div>
    <table className="structure-legend">
      <tbody>{PHASE_ORDER.map(phase => {
        const item = byPhase.get(phase);
        return <tr key={phase}>
          <td><i className={`swatch phase-${phase}`} /><strong>{PHASE_LABEL[phase]}</strong><small>{PHASE_HINT[phase]}</small></td>
          <td className="num">{days(item?.person_days)}</td>
          <td className="num">{pct(item?.share)}</td>
          {analysis.cost_enabled && <td className="num">{money(item?.cost) ?? "—"}</td>}
        </tr>;
      })}</tbody>
    </table>
  </div>;
}

function MonthlyChart({ analysis }: { analysis: Analysis }) {
  const rows = analysis.monthly;
  const max = Math.max(1, ...rows.map(row => PHASE_ORDER.reduce((sum, phase) => sum + num(row[phase]), 0)));
  if (!rows.length) return null;
  return <div className="monthly">
    <h3>按月投入</h3>
    <div className="monthly-chart" role="img" aria-label="按月投入结构">
      {rows.map(row => {
        const total = PHASE_ORDER.reduce((sum, phase) => sum + num(row[phase]), 0);
        return <div key={row.month} className="monthly-col">
          <div className="monthly-stack" style={{ height: `${(total / max) * 100}%` }}
            title={`${row.month}\n${PHASE_ORDER.filter(p => num(row[p]) > 0).map(p => `${PHASE_LABEL[p]}：${days(row[p])}`).join("\n")}`}>
            {PHASE_ORDER.slice().reverse().map(phase => num(row[phase]) > 0 && <span key={phase} className={`seg phase-${phase}`} style={{ flexGrow: num(row[phase]) }} />)}
          </div>
          <b>{total.toFixed(0)}</b>
          <small>{row.month.slice(2).replace("-", "/")}</small>
        </div>;
      })}
    </div>
  </div>;
}

function OpenOpportunities({ analysis, canEdit, changed }: { analysis: Analysis; canEdit: boolean; changed: () => void }) {
  const [active, setActive] = useState<string | null>(null);
  const items = analysis.open_opportunities;
  const maxAge = Math.max(1, ...analysis.aging.map(a => num(a.person_days)));
  return <section className="panel">
    <div className="panel-head"><div><h2>在途商机</h2><p>按风险敞口排序。预期沉没 = 在途投入 ×（1 − 赢率），没有赢率时按历史赢单率估算；超过 {analysis.settings.stall_days} 天无新投入标为疑似停滞。</p></div></div>
    <div className="aging">{analysis.aging.map(bucket => <div key={bucket.bucket}>
      <span>{bucket.bucket}</span>
      <i><em style={{ width: `${(num(bucket.person_days) / maxAge) * 100}%` }} /></i>
      <b>{days(bucket.person_days)} · {bucket.count} 个</b>
    </div>)}</div>
    <div className="table-wrap"><table>
      <thead><tr><th>项目代码</th><th>名称 / 客户</th><th>赢率</th><th>在途投入</th><th>预期沉没</th><th>账龄</th><th>最近投入</th><th>人数</th>{canEdit && <th>操作</th>}</tr></thead>
      <tbody>{items.length ? items.map(item => <FragmentRow key={item.code} item={item} active={active === item.code} canEdit={canEdit}
        toggle={() => setActive(active === item.code ? null : item.code)} changed={() => { setActive(null); changed(); }} />)
        : <tr><td className="empty-cell" colSpan={canEdit ? 9 : 8}>当前没有进行中的商机投入</td></tr>}</tbody>
    </table></div>
  </section>;
}

function FragmentRow({ item, active, canEdit, toggle, changed }: { item: CostObjectInvestment; active: boolean; canEdit: boolean; toggle: () => void; changed: () => void }) {
  return <>
    <tr>
      <td><strong className="project-code">{item.code}</strong></td>
      <td><div className="subject">{item.subject ?? item.code}</div><small>{item.customer ?? "—"}</small></td>
      <td>{item.win_probability == null ? <span className="muted">未填写</span> : `${item.win_probability}%`}</td>
      <td><strong>{days(item.at_risk_person_days)}</strong></td>
      <td>{days(item.expected_sunk_person_days)}<small>{item.probability_source ?? ""}</small></td>
      <td>{item.presales_age_days == null ? "—" : `${item.presales_age_days} 天`}</td>
      <td>{item.last_work_date ?? "—"}{item.stalled && <span className="flag warn">疑似停滞</span>}</td>
      <td>{item.employee_count}</td>
      {canEdit && <td><button type="button" className="link-button" onClick={toggle}>{active ? "取消" : "记录结论"}</button></td>}
    </tr>
    {active && <tr className="inline-form-row"><td colSpan={9}><OutcomeForm code={item.code} done={changed} /></td></tr>}
  </>;
}

function OutcomeForm({ code, done }: { code: string; done: () => void }) {
  const [outcome, setOutcome] = useState("lost");
  const [reason, setReason] = useState(REASONS[0]);
  const [outcomeDate, setOutcomeDate] = useState("");
  const [notes, setNotes] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  async function submit() {
    setBusy(true); setMessage(null);
    try {
      await applyAction("recordOpportunityOutcome", { costObject: code, outcome, reason, outcomeDate: outcomeDate || undefined, notes: notes || undefined });
      done();
    } catch (err) {
      setMessage(err instanceof ApiError ? err.message : errorMessage(err, "记录失败"));
    } finally {
      setBusy(false);
    }
  }
  return <div className="outcome-form">
    <strong>记录 {code} 的商机结论</strong>
    <select value={outcome} aria-label="结论" onChange={e => setOutcome(e.target.value)}><option value="lost">已丢单</option><option value="abandoned">已放弃</option></select>
    <select value={reason} aria-label="原因" onChange={e => setReason(e.target.value)}>{REASONS.map(r => <option key={r}>{r}</option>)}</select>
    <input type="date" aria-label="结论日期" value={outcomeDate} onChange={e => setOutcomeDate(e.target.value)} />
    <input type="text" aria-label="备注" placeholder="备注（可选）" value={notes} onChange={e => setNotes(e.target.value)} />
    <button type="button" className="button danger" disabled={busy} onClick={() => void submit()}>{busy ? "提交中…" : "确认沉没"}</button>
    <small>确认后，该代码的售前投入计为沉没成本；可通过“重新激活”撤销。</small>
    {message && <p className="action-message error">{message}</p>}
  </div>;
}

function SunkReview({ analysis, canEdit, changed }: { analysis: Analysis; canEdit: boolean; changed: () => void }) {
  const [message, setMessage] = useState<string | null>(null);
  const [reopening, setReopening] = useState<string | null>(null);
  const [reopenReason, setReopenReason] = useState("");
  const maxReason = Math.max(1, ...analysis.sunk_reasons.map(r => num(r.person_days)));
  async function reopen(code: string) {
    if (!reopenReason.trim()) { setMessage("请填写重新激活的理由"); return; }
    setMessage(null);
    try { await applyAction("reopenOpportunity", { costObject: code, reason: reopenReason.trim() }); setReopening(null); setReopenReason(""); changed(); }
    catch (err) { setMessage(errorMessage(err, "重新激活失败")); }
  }
  return <section className="panel">
    <div className="panel-head"><div><h2>沉没成本复盘</h2><p>沉没成本只用于事后复盘（哪些商机不值得投入、原因是什么），不应作为“已经投了很多所以继续投”的依据。</p></div></div>
    {message && <div className="error-banner inset">{message}</div>}
    {analysis.sunk_items.length ? <div className="sunk-layout">
      <div className="reason-bars">{analysis.sunk_reasons.map(reason => <div key={reason.reason}>
        <span>{reason.reason}</span>
        <i><em style={{ width: `${(num(reason.person_days) / maxReason) * 100}%` }} /></i>
        <b>{days(reason.person_days)} · {reason.count} 个</b>
      </div>)}</div>
      <div className="table-wrap"><table>
        <thead><tr><th>项目代码</th><th>名称 / 客户</th><th>结论</th><th>沉没投入</th><th>原因</th><th>来源</th><th>结论日期</th>{canEdit && <th>操作</th>}</tr></thead>
        <tbody>{analysis.sunk_items.map(item => <Fragment key={item.code}><tr>
          <td><strong className="project-code">{item.code}</strong></td>
          <td><div className="subject">{item.subject ?? item.code}</div><small>{item.customer ?? "—"}</small></td>
          <td><span className="flag bad">{OUTCOME_LABEL[item.outcome]}</span></td>
          <td><strong>{days(item.sunk_person_days)}</strong></td>
          <td>{item.outcome_reason ?? "—"}</td>
          <td>{item.outcome_source}{item.outcome_recorded_by && <small>{item.outcome_recorded_by}</small>}</td>
          <td>{item.outcome_date ?? "—"}</td>
          {canEdit && <td>{item.outcome_source === "人工确认" ? <button type="button" className="link-button" onClick={() => { setReopening(reopening === item.code ? null : item.code); setReopenReason(""); }}>{reopening === item.code ? "取消" : "重新激活"}</button> : <span className="muted">以 CRM 为准</span>}</td>}
        </tr>
        {reopening === item.code && <tr className="inline-form-row"><td colSpan={8}><div className="outcome-form">
          <strong>重新激活 {item.code}</strong>
          <input type="text" aria-label="重新激活理由" placeholder="理由，例如：客户重启采购" value={reopenReason} onChange={e => setReopenReason(e.target.value)} />
          <button type="button" className="button primary" onClick={() => void reopen(item.code)}>确认激活</button>
          <small>激活后，该代码的投入重新计为在途投入（商机）或未建档投入。</small>
        </div></td></tr>}
        </Fragment>)}</tbody>
      </table></div>
    </div> : <p className="empty">暂无已确认的沉没成本。</p>}
  </section>;
}

function WonProjects({ items }: { items: CostObjectInvestment[] }) {
  return <section className="panel">
    <div className="panel-head"><div><h2>赢单项目的获客成本</h2><p>签约日前的售前投入占项目总投入的比例。</p></div></div>
    <div className="table-wrap"><table>
      <thead><tr><th>项目代码</th><th>签约日</th><th>获客成本</th><th>履约成本</th><th>获客占比</th></tr></thead>
      <tbody>{items.filter(i => num(i.total_person_days) > 0).length ? items.filter(i => num(i.total_person_days) > 0).map(item => <tr key={item.code}>
        <td><strong className="project-code">{item.code}</strong><small>{item.subject ?? ""}</small></td>
        <td>{item.conversion_date ?? <span className="flag warn">缺少签约日</span>}</td>
        <td>{days(item.presales_converted_person_days)}</td>
        <td>{days(item.delivery_person_days)}</td>
        <td>{pct(item.cost_to_win_share)}</td>
      </tr>) : <tr><td className="empty-cell" colSpan={5}>暂无赢单项目的投入</td></tr>}</tbody>
    </table></div>
  </section>;
}

function Unregistered({ items, canEdit, changed }: { items: CostObjectInvestment[]; canEdit: boolean; changed: () => void }) {
  const [active, setActive] = useState<string | null>(null);
  const [expanded, setExpanded] = useState(false);
  const visible = expanded ? items : items.slice(0, 10);
  return <section className="panel">
    <div className="panel-head"><div><h2>未建档代码</h2><p>有工时但 CRM 中没有记录：请在 CRM 补录商机或合同；确定已放弃的可直接记录结论。</p></div></div>
    <div className="table-wrap"><table>
      <thead><tr><th>项目代码</th><th>投入</th><th>人数</th><th>投入期间</th>{canEdit && <th>操作</th>}</tr></thead>
      <tbody>{visible.length ? visible.map(item => <UnregisteredRow key={item.code} item={item} canEdit={canEdit} active={active === item.code}
        toggle={() => setActive(active === item.code ? null : item.code)} changed={() => { setActive(null); changed(); }} />)
        : <tr><td className="empty-cell" colSpan={canEdit ? 5 : 4}>所有投入都已在 CRM 建档</td></tr>}</tbody>
    </table></div>
    {items.length > 10 && <div className="pagination"><button onClick={() => setExpanded(value => !value)}>{expanded ? "收起" : `显示全部 ${items.length} 个代码`}</button></div>}
  </section>;
}

function UnregisteredRow({ item, canEdit, active, toggle, changed }: { item: CostObjectInvestment; canEdit: boolean; active: boolean; toggle: () => void; changed: () => void }) {
  return <>
    <tr>
      <td><strong className="project-code">{item.code}</strong></td>
      <td>{days(item.unregistered_person_days)}</td>
      <td>{item.employee_count}</td>
      <td>{item.first_work_date ?? "—"} ~ {item.last_work_date ?? "—"}</td>
      {canEdit && <td><button type="button" className="link-button" onClick={toggle}>{active ? "取消" : "记录结论"}</button></td>}
    </tr>
    {active && <tr className="inline-form-row"><td colSpan={5}><OutcomeForm code={item.code} done={changed} /></td></tr>}
  </>;
}

function SettingsForm({ settings, canSeeCost, saved }: { settings: InvestmentSettings; canSeeCost: boolean; saved: () => void }) {
  const [cost, setCost] = useState(settings.standard_person_day_cost ?? "");
  const [stallDays, setStallDays] = useState(String(settings.stall_days));
  const [lost, setLost] = useState(settings.lost_statuses.join("，"));
  const [internal, setInternal] = useState(settings.internal_codes.join("，"));
  const [message, setMessage] = useState<string | null>(null);
  const split = (value: string) => value.split(/[,，\s]+/).map(v => v.trim()).filter(Boolean);
  const payload = useMemo(() => ({
    ...(canSeeCost ? { standard_person_day_cost: cost || null } : {}),
    stall_days: Number(stallDays), lost_statuses: split(lost), internal_codes: split(internal)
  }), [canSeeCost, cost, stallDays, lost, internal]);
  async function save() {
    setMessage(null);
    try { await updateInvestmentSettings(payload); setMessage("已保存"); saved(); }
    catch (err) { setMessage(errorMessage(err, "保存失败")); }
  }
  return <div className="investment-settings">
    {canSeeCost && <label>标准人天成本（元）<input inputMode="decimal" value={cost} placeholder="留空则只显示人天" onChange={e => setCost(e.target.value)} /></label>}
    <label>停滞判定天数<input inputMode="numeric" value={stallDays} onChange={e => setStallDays(e.target.value)} /></label>
    <label>CRM 中表示丢单的状态<input value={lost} onChange={e => setLost(e.target.value)} /></label>
    <label>内部代码<input value={internal} onChange={e => setInternal(e.target.value)} /></label>
    <button type="button" className="button primary" onClick={() => void save()}>保存设置</button>
    {message && <small>{message}</small>}
  </div>;
}
