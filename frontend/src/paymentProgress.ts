import type { ProjectHealthRow } from "./types";

export type PaymentRow = Pick<ProjectHealthRow, "payment_summary" | "payment_term_summary" | "crm_received_amount" | "contract_value">;
export interface PaymentLine { text: string; tone: "muted" | "warning" | "danger" | "done" }

// The payment column: the CRM figure (official, imported) on top, then where the system's payment nodes stand.
// Every case gets one plain line: no terms yet, terms waiting for a milestone, the next unpaid node (with its
// deadline), or everything received; plus a hint when the confirmed receipts are ahead of the CRM.
export function paymentProgress(row: PaymentRow, money: (value: string | null) => string): PaymentLine[] {
  const summary = row.payment_summary;
  const terms = row.payment_term_summary ?? [];
  const unlinked = terms.filter(term => !term.payment_node_id).length;
  const lines: PaymentLine[] = [];
  if (summary.total_nodes === 0) {
    lines.push(terms.length === 0 ? { text: "未约定回款节点", tone: "muted" } : { text: `${unlinked} 个回款约定待关联里程碑`, tone: "warning" });
    return lines;
  }
  const next = summary.next_node;
  if (next) {
    const term = terms.find(item => item.payment_node_id === next.payment_node_id);
    const ratio = term?.payment_ratio ? ` ${Number(term.payment_ratio)}%` : "";
    const tone = next.deadline.urgency === "overdue" ? "danger" : next.deadline.urgency === "due_today" || next.deadline.urgency === "due_soon" ? "warning" : "muted";
    lines.push({ text: `${summary.paid_nodes}/${summary.total_nodes} 个节点已到账`, tone: "muted" });
    lines.push({ text: `下一笔：${next.name}${ratio} · ${next.deadline.label}`, tone });
  } else {
    lines.push({ text: `✓ 回款节点已全部到账（${summary.paid_nodes}/${summary.total_nodes}）`, tone: "done" });
  }
  if (unlinked > 0) lines.push({ text: `另有 ${unlinked} 个回款约定待关联里程碑`, tone: "warning" });
  const confirmed = Number(summary.actual_amount);
  const crm = Number(row.crm_received_amount);
  if (row.contract_value !== null && summary.actual_amount !== null && Number.isFinite(confirmed) && Number.isFinite(crm) && confirmed - crm > 0.005) {
    lines.push({ text: `系统已确认 ${money(summary.actual_amount)} · CRM 待同步`, tone: "warning" });
  }
  return lines;
}
