import { useState } from "react";
import { ontologyFunction } from "../api";
import type { InterventionHistory, StrategyHistoryItem } from "./types";

function when(iso: string | null): string {
  if (!iso) return "";
  const time = new Date(iso);
  if (Number.isNaN(time.getTime())) return iso.slice(0, 16);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${time.getFullYear()}-${pad(time.getMonth() + 1)}-${pad(time.getDate())} ${pad(time.getHours())}:${pad(time.getMinutes())}`;
}

/** D61/D62: an intervention's timeline, loaded when the row is expanded. */
export function InterventionTimeline({ interventionId }: { interventionId: string }) {
  const [open, setOpen] = useState(false);
  const [data, setData] = useState<InterventionHistory | null>(null);
  const [error, setError] = useState<string | null>(null);
  function toggle() {
    const next = !open;
    setOpen(next);
    if (next && !data) {
      ontologyFunction<InterventionHistory>("interventionHistory", { intervention: interventionId })
        .then(setData).catch(err => setError(err instanceof Error ? err.message : "无法读取历史"));
    }
  }
  return <div className="ai-timeline-box">
    <button type="button" className="ai-draft-open" aria-expanded={open} onClick={toggle}>{open ? "收起历史" : "历史"}</button>
    {open && <TimelineBody data={data} error={error} />}
  </div>;
}

export function TimelineBody({ data, error }: { data: InterventionHistory | null; error: string | null }) {
  if (error) return <p className="ai-basis-stale">{error}</p>;
  if (!data) return <p className="ai-muted">正在读取…</p>;
  return <div className="ai-timeline">
    {data.origin && <p className="ai-timeline-origin">
      {data.origin.type === "strategy" ? "来自部门策略" : "来自 AI 提案"}「{data.origin.title}」{data.origin.statusLabel ? `（${data.origin.statusLabel}）` : ""}
      {data.origin.basis.length > 0 && <small>依据：{data.origin.basis.join("；")}</small>}
    </p>}
    <ol>{data.events.map((event, index) => <li key={index} className={`ai-event ${event.kind}`}>
      <time>{when(event.at)}</time>
      <span><strong>{event.title}</strong>{event.actor ? <em>{event.actor}</em> : null}</span>
      {event.detail && <small>{event.detail}</small>}
    </li>)}</ol>
  </div>;
}

/** D61: strategies no longer in force, with their interventions and conclusions; searchable by title. */
export function StrategyHistory({ items }: { items: StrategyHistoryItem[] }) {
  const [query, setQuery] = useState("");
  if (!items.length) return null;
  const shown = query.trim() ? items.filter(item => item.title.includes(query.trim())) : items;
  return <details className="ai-gaps ai-history">
    <summary><strong>历史策略 {items.length} 条</strong><span>已关闭、已过期或被取代的 部门策略（AI），及其落实干预与处理结论</span></summary>
    <input type="search" value={query} onChange={event => setQuery(event.target.value)} placeholder="按标题搜索" aria-label="搜索历史策略" />
    <ul className="ai-history-list">{shown.map(item => <li key={item.noteId}>
      <div><strong>{item.title}</strong><b>{item.statusLabel}</b></div>
      <small>{item.categoryLabel} · {item.approvedBy} {item.approvedAt.slice(0, 10)} 采纳{item.closedAt ? ` · ${item.closedBy === "system" ? "系统" : item.closedBy} ${item.closedAt.slice(0, 10)} ${item.statusLabel}` : ""}{item.closedReason ? `：${item.closedReason}` : ""}</small>
      {item.followUps.length > 0 ? <ul>{item.followUps.map(f => <li key={f.interventionId}>{f.projectNumber} {f.statusLabel}{f.conclusion ? `：${f.conclusion}` : ""}</li>)}</ul>
        : <small className="ai-muted">未分解到项目</small>}
      {item.basis.length > 0 && <small className="ai-gap-basis">依据：{item.basis.join("；")}</small>}
    </li>)}</ul>
    {query && !shown.length && <p className="ai-muted">没有找到相关的历史策略。</p>}
  </details>;
}
