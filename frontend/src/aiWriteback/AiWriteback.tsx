import { useState } from "react";
import type { FormEvent } from "react";
import { CalendarDatePicker } from "../components/CalendarDatePicker";
import type { PmAssistantSuggestion } from "../types";
import {
  approveAiProposal, closeAiRiskFlag, closeAiStrategyNote, rejectAiProposal, renewAiRiskFlag, renewAiStrategyNote, submitAiProposal
} from "./api";
import "./ai.css";
import { INTERVENTION_TYPE_LABELS, KIND_LABELS, STRATEGY_CATEGORY_LABELS } from "./types";
import type { AiKind, AiOverview, AiProposal, AiStrategyNote, AiStrategyProposal, BasisFact } from "./types";

type Act = (run: () => Promise<void>, done: string) => Promise<boolean>;

function errorText(err: unknown): string {
  return err instanceof Error ? err.message : "操作失败，请稍后重试。";
}

/** The facts an item rests on (D55): how each read when submitted, and the latest recheck when it no longer holds. */
export function BasisList({ basis, holds, note }: { basis: BasisFact[]; holds: boolean; note: string | null }) {
  return <div className="ai-basis">
    <ul>{basis.map((fact, index) => <li key={index}>{fact.text ?? JSON.stringify(fact)}</li>)}</ul>
    {!holds && note && <p className="ai-basis-stale">{note}</p>}
  </div>;
}

function addDays(days: number): string {
  const date = new Date();
  date.setDate(date.getDate() + days);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
}

/**
 * Project drawer: AI suggestions waiting for approval and the project's active AI risk flags (D51-D54). Renders
 * nothing when the project has neither, and folds the recently decided ones into one line.
 */
export function ProjectAiSection({ overview, saving, act }: { overview: AiOverview | null; saving: boolean; act: Act }) {
  const [rejecting, setRejecting] = useState<string | null>(null);
  const [reason, setReason] = useState("");
  if (!overview || (!overview.pending.length && !overview.riskFlags.length && !overview.recent.length)) return null;
  const decide = (item: AiProposal, approve: boolean) => approve
    ? act(() => approveAiProposal(item.proposalId), `已批准，生成 ${item.kindLabel ?? KIND_LABELS[item.kind]}`)
    : act(() => rejectAiProposal(item.proposalId, reason.trim()), "已驳回").then(ok => { if (ok) { setRejecting(null); setReason(""); } return ok; });
  return <section className="detail-section ai-section">
    <div className="section-title"><div><h3>AI 建议与风险标记 <span className="ai-tag">需批准</span></h3>
      <small>AI 只给候选，批准后才写进系统；每条都引用系统事实，系统每小时复核，依据不成立或到期会自动关闭。风险标记不改变风险等级。</small></div></div>
    {overview.pending.length > 0 && <ul className="panel-rows">{overview.pending.map(item => <li key={item.proposalId} className={`panel-row ai-row ${item.basisHolds ? "" : "stale"}`}>
      <span className="panel-row-lead">待批准</span>
      <div className="panel-row-main">
        <strong>{item.title}</strong>
        <small>{item.kindLabel ?? KIND_LABELS[item.kind]}{item.kind === "intervention" && item.parameters.dueDate ? ` · 截止 ${item.parameters.dueDate}` : ""} · {item.submittedBy} 提交 · {item.expiresOn} 前处理</small>
        {item.detail && <p className="ai-detail">{item.detail}</p>}
        <BasisList basis={item.basis} holds={item.basisHolds} note={item.basisNote} />
        {rejecting === item.proposalId && <div className="ai-inline-reason">
          <input value={reason} maxLength={500} placeholder="驳回原因（可不填）" onChange={event => setReason(event.target.value)} aria-label="驳回原因" />
          <button type="button" className="row-action danger-action" disabled={saving} onClick={() => void decide(item, false)}>确认驳回</button>
          <button type="button" className="row-action" disabled={saving} onClick={() => setRejecting(null)}>取消</button>
        </div>}
      </div>
      <b className="panel-row-status">{item.basisHolds ? "依据成立" : "依据已不成立"}</b>
      <div className="panel-row-actions">{item.canApprove ? <>
        <button type="button" className="row-action" disabled={saving || !item.basisHolds} title={item.basisHolds ? "批准后写进系统" : "依据已不成立，不能批准"} onClick={() => void decide(item, true)}>批准</button>
        <button type="button" className="row-action danger-action" disabled={saving} onClick={() => { setRejecting(item.proposalId); setReason(""); }}>驳回</button>
      </> : <small className="ai-muted">等待有权限的人批准</small>}</div>
    </li>)}</ul>}
    {overview.riskFlags.length > 0 && <ul className="panel-rows">{overview.riskFlags.map(flag => <li key={flag.flagId} className={`panel-row ai-row ${flag.basisHolds ? "" : "stale"}`}>
      <span className="panel-row-lead">AI 标记</span>
      <div className="panel-row-main">
        <strong>{flag.title}</strong>
        <small>有效至 {flag.validUntil} · {flag.approvedBy} {flag.approvedAt.slice(0, 10)} 批准</small>
        {flag.detail && <p className="ai-detail">{flag.detail}</p>}
        <BasisList basis={flag.basis} holds={flag.basisHolds} note={flag.basisNote} />
      </div>
      <b className="panel-row-status">{flag.basisHolds ? "有效" : "依据已不成立"}</b>
      <div className="panel-row-actions">{overview.canApproveProjectItems && <>
        <button type="button" className="row-action" disabled={saving || !flag.basisHolds} onClick={() => void act(() => renewAiRiskFlag(flag.flagId), "已续期")}>续期</button>
        <button type="button" className="row-action danger-action" disabled={saving} onClick={() => void act(() => closeAiRiskFlag(flag.flagId, "已处理"), "已关闭")}>关闭</button>
      </>}</div>
    </li>)}</ul>}
    {overview.recent.length > 0 && <details className="completed-todo-section collapsible panel-done">
      <summary className="completed-todo-head"><div><h4>最近 30 天处理过 {overview.recent.length} 条</h4><small>批准、驳回、过期或被取代</small></div><span className="collapsible-toggle" /></summary>
      <ul className="panel-rows">{overview.recent.map(item => <li key={item.proposalId} className="panel-row completed">
        <span className="panel-row-lead">{item.statusLabel}</span>
        <div className="panel-row-main"><strong>{item.title}</strong><small>{item.kindLabel} · {item.decidedBy === "system" ? "系统" : item.decidedBy} {item.decidedAt?.slice(0, 10)}{item.decisionNote ? ` · ${item.decisionNote}` : ""}</small></div>
      </li>)}</ul>
    </details>}
  </section>;
}

/** Under an alert's AI action in the drawer: submit that action as an intervention suggestion, citing the alert (D52, D55). */
export function InterventionDraft({ project, code, alertTitle, action, owner, act, saving }: {
  project: string; code: string; alertTitle: string; action: string; owner: string; act: Act; saving: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState({ title: action, dueDate: addDays(14), interventionType: "management", owner });
  if (!open) return <button type="button" className="ai-draft-open" onClick={() => setOpen(true)}>提交为干预建议</button>;
  async function submit(event: FormEvent) {
    event.preventDefault();
    const ok = await act(() => submitAiProposal({
      kind: "intervention", project, title: draft.title.trim(), detail: `来自 AI 分析：${alertTitle}`, dueDate: draft.dueDate,
      interventionType: draft.interventionType, owner: draft.owner.trim() || undefined, source: "projectAnalysis",
      basis: [{ type: "alert", project, code }]
    }), "已提交，等待批准");
    if (ok) setOpen(false);
  }
  return <form className="milestone-form inline-form ai-draft" onSubmit={submit}>
    <div className="inline-form-head"><strong>提交为管理干预建议</strong><span>依据：本项预警；批准后成为一条管理干预</span></div>
    <label className="span-2">事项<input required maxLength={120} value={draft.title} onChange={event => setDraft({ ...draft, title: event.target.value })} /></label>
    <label>截止日期<CalendarDatePicker value={draft.dueDate} onChange={dueDate => setDraft({ ...draft, dueDate })} label="选择干预截止日期" minDate={addDays(0)} /></label>
    <label>类型<select value={draft.interventionType} onChange={event => setDraft({ ...draft, interventionType: event.target.value })}>
      {Object.entries(INTERVENTION_TYPE_LABELS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
    <label className="span-2">负责人<input value={draft.owner} placeholder="不填则为项目经理" onChange={event => setDraft({ ...draft, owner: event.target.value })} /></label>
    <div className="form-actions span-2"><button type="button" className="button secondary" disabled={saving} onClick={() => setOpen(false)}>取消</button>
      <button className="button primary" disabled={saving || !draft.title.trim() || !draft.dueDate}>提交审批</button></div>
  </form>;
}

/** 部门经营 → 策略信号: AI strategy notes in force and strategy suggestions to approve (D51, D53). */
export function StrategyAiPanel({ notes, proposals, canApprove, onChanged }: {
  notes: AiStrategyNote[]; proposals: AiStrategyProposal[]; canApprove: boolean; onChanged: () => void;
}) {
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState<{ text: string; tone: "ok" | "error" } | null>(null);
  if (!notes.length && !proposals.length) return null;
  async function act(run: () => Promise<void>, done: string) {
    setSaving(true);
    try {
      await run();
      setMessage({ text: done, tone: "ok" });
      onChanged();
    } catch (err) {
      setMessage({ text: errorText(err), tone: "error" });
    } finally {
      setSaving(false);
    }
  }
  return <div className="ai-strategy">
    <h3>AI 策略建议 <span className="ai-tag">经批准</span></h3>
    <p className="ai-muted">AI 提出、由管理员或部门负责人批准；依据不再成立或到期时系统自动关闭。上面的规则信号不受影响。</p>
    {message && <p className={`ai-message ${message.tone}`} role="status">{message.text}</p>}
    <ul className="panel-rows">
      {proposals.map(item => <li key={item.proposalId} className={`panel-row ai-row ${item.basisHolds ? "" : "stale"}`}>
        <span className="panel-row-lead">待批准</span>
        <div className="panel-row-main"><strong>{item.title}</strong>
          <small>{item.categoryLabel} · {item.submittedBy} 提交 · {item.expiresOn} 前处理</small>
          {item.detail && <p className="ai-detail">{item.detail}</p>}
          <BasisList basis={item.basis} holds={item.basisHolds} note={item.basisNote} /></div>
        <b className="panel-row-status">{item.basisHolds ? "依据成立" : "依据已不成立"}</b>
        <div className="panel-row-actions">{canApprove ? <>
          <button type="button" className="row-action" disabled={saving || !item.basisHolds} onClick={() => void act(() => approveAiProposal(item.proposalId), "已采纳")}>采纳</button>
          <button type="button" className="row-action danger-action" disabled={saving} onClick={() => void act(() => rejectAiProposal(item.proposalId), "已驳回")}>驳回</button>
        </> : <small className="ai-muted">等待管理员或部门负责人</small>}</div>
      </li>)}
      {notes.map(note => <li key={note.noteId} className={`panel-row ai-row ${note.basisHolds ? "" : "stale"}`}>
        <span className="panel-row-lead">{note.categoryLabel || STRATEGY_CATEGORY_LABELS[note.category]}</span>
        <div className="panel-row-main"><strong>{note.title}</strong>
          <small>有效至 {note.validUntil} · {note.approvedBy} {note.approvedAt.slice(0, 10)} 批准</small>
          {note.detail && <p className="ai-detail">{note.detail}</p>}
          <BasisList basis={note.basis} holds={note.basisHolds} note={note.basisNote} /></div>
        <b className="panel-row-status">{note.basisHolds ? "有效" : "依据已不成立"}</b>
        <div className="panel-row-actions">{canApprove && <>
          <button type="button" className="row-action" disabled={saving || !note.basisHolds} onClick={() => void act(() => renewAiStrategyNote(note.noteId), "已续期")}>续期</button>
          <button type="button" className="row-action danger-action" disabled={saving} onClick={() => void act(() => closeAiStrategyNote(note.noteId, "已执行或不再适用"), "已关闭")}>关闭</button>
        </>}</div>
      </li>)}
    </ul>
  </div>;
}

function readable(fact: Record<string, unknown>): string {
  if (fact.type === "alert") return `${fact.project} 预警 ${fact.code}`;
  if (fact.type === "signal") return `策略信号 ${STRATEGY_CATEGORY_LABELS[String(fact.signal)] ?? fact.signal}${fact.project ? `（${fact.project}）` : ""}`;
  return `${fact.objectType} ${fact.primaryKey} 的 ${fact.property} ${fact.op} ${String(fact.value)}`;
}

/** PM Assistant: a drafted AI suggestion (submitAiProposal pre-checked, nothing written) and its 「提交审批」 button. */
export function ProposalDraftCard({ item }: { item: PmAssistantSuggestion }) {
  const [state, setState] = useState<{ status: "idle" | "saving" | "done" | "error"; text?: string }>({ status: "idle" });
  const p = item.parameters as Record<string, unknown>;
  const kind = (p.kind as AiKind) ?? "riskFlag";
  const basis = Array.isArray(p.basis) ? p.basis as Record<string, unknown>[] : [];
  async function submit() {
    setState({ status: "saving" });
    try {
      await submitAiProposal({ ...(p as Record<string, unknown>), source: "assistant" } as never);
      setState({ status: "done", text: "已提交，等待批准。可在项目详情或部门经营中查看。" });
    } catch (err) {
      setState({ status: "error", text: errorText(err) });
    }
  }
  return <article className={`ai-draft-card ${item.valid ? "valid" : "invalid"}`}>
    <header><strong>{KIND_LABELS[kind]}候选</strong><i>{item.valid ? "预检通过" : "预检未通过"}</i></header>
    <p className="ai-draft-title">{String(p.title ?? "")}{p.project ? ` · ${p.project}` : ""}{p.category ? ` · ${STRATEGY_CATEGORY_LABELS[String(p.category)] ?? p.category}` : ""}{p.dueDate ? ` · 截止 ${p.dueDate}` : ""}</p>
    {p.detail ? <p>{String(p.detail)}</p> : null}
    {basis.length > 0 && <ul>{basis.map((fact, index) => <li key={index}>依据：{item.basisTexts?.[index] ?? readable(fact)}</li>)}</ul>}
    {item.reasons.length > 0 && <ul className="ai-draft-reasons">{item.reasons.map(reason => <li key={reason}>{reason}</li>)}</ul>}
    <footer>
      {state.status === "done" ? <span className="ai-message ok">{state.text}</span> : <>
        <button type="button" className="row-action" disabled={!item.valid || state.status === "saving"} onClick={() => void submit()}>{state.status === "saving" ? "提交中" : "提交审批"}</button>
        <small>提交后由有权限的人批准才写进系统；提交时会重新核对依据。</small>
      </>}
      {state.status === "error" && <span className="ai-message error">{state.text}</span>}
    </footer>
  </article>;
}
