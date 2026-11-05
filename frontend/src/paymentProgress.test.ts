import { describe, expect, it } from "vitest";
import { paymentProgress, type PaymentRow } from "./paymentProgress";

const money = (value: string | null) => (value == null ? "—" : `¥${Number(value).toLocaleString("en-US")}`);
const deadline = (urgency: "overdue" | "due_today" | "due_soon" | "upcoming", label: string) => ({ days_remaining: 0, urgency, label });
type Term = PaymentRow["payment_term_summary"][number];
const term = (name: string, ratio: string | null, node: string | null, status: string | null = null): Term =>
  ({ name, payment_ratio: ratio, planned_amount: "0", linked_milestone_id: node ? `ms_${node}` : null, payment_node_id: node, node_status: status, planned_date: null });

function row(overrides: Partial<PaymentRow> & { nodes?: [number, number]; actual?: string; next?: PaymentRow["payment_summary"]["next_node"] } = {}): PaymentRow {
  const [paid, total] = overrides.nodes ?? [0, 0];
  return {
    contract_value: "172250.00",
    crm_received_amount: "51675.00",
    payment_term_summary: overrides.payment_term_summary ?? [],
    payment_summary: { total_nodes: total, paid_nodes: paid, planned_amount: "0", actual_amount: overrides.actual ?? "0", next_node: overrides.next ?? null },
    ...("contract_value" in overrides ? { contract_value: overrides.contract_value! } : {}),
  } as PaymentRow;
}

describe("payment column progress line", () => {
  it("all nodes received: says so instead of naming a 'next' term that is already paid (SEY104)", () => {
    const lines = paymentProgress(row({ nodes: [2, 2], actual: "172250.00",
      payment_term_summary: [term("首付款", "30.00", "n1", "paid"), term("尾款", "70.00", "n2", "paid")] }), money);
    expect(lines.map(l => l.text)).toEqual(["✓ 回款节点已全部到账（2/2）", "系统已确认 ¥172,250 · CRM 待同步"]);
    expect(lines.map(l => l.tone)).toEqual(["done", "warning"]);
    expect(lines.some(l => l.text.includes("下一"))).toBe(false);
  });

  it("some received: the next unpaid node with its ratio and deadline; overdue is red", () => {
    const lines = paymentProgress(row({ nodes: [1, 2], actual: "51675.00",
      payment_term_summary: [term("尾款", "70.00", "n2"), term("首付款", "30.00", "n1", "paid")],
      next: { payment_node_id: "n2", name: "尾款", planned_date: "2026-10-31", planned_amount: "120575.00", status: "planned", deadline: deadline("overdue", "已延期 5 天") } }), money);
    expect(lines).toEqual([{ text: "1/2 个节点已到账", tone: "muted" }, { text: "下一笔：尾款 70% · 已延期 5 天", tone: "danger" }]);
  });

  it("due soon is amber, far away is muted", () => {
    const base = { nodes: [0, 1] as [number, number], payment_term_summary: [term("首付款", null, "n1")] };
    const soon = paymentProgress(row({ ...base, next: { payment_node_id: "n1", name: "首付款", planned_date: "x", planned_amount: "1", status: "planned", deadline: deadline("due_soon", "还有 3 天") } }), money);
    const later = paymentProgress(row({ ...base, next: { payment_node_id: "n1", name: "首付款", planned_date: "x", planned_amount: "1", status: "planned", deadline: deadline("upcoming", "还有 40 天") } }), money);
    expect(soon[1]).toEqual({ text: "下一笔：首付款 · 还有 3 天", tone: "warning" });
    expect(later[1].tone).toBe("muted");
  });

  it("no terms at all, or terms not yet linked to a milestone", () => {
    expect(paymentProgress(row(), money)).toEqual([{ text: "未约定回款节点", tone: "muted" }]);
    expect(paymentProgress(row({ payment_term_summary: [term("首付款", "30", null), term("尾款", "70", null)] }), money))
      .toEqual([{ text: "2 个回款约定待关联里程碑", tone: "warning" }]);
  });

  it("all linked nodes received but another term still unlinked: both are said", () => {
    const lines = paymentProgress(row({ nodes: [1, 1], actual: "51675.00",
      payment_term_summary: [term("尾款", "70", null), term("首付款", "30", "n1", "paid")] }), money);
    expect(lines.map(l => l.text)).toEqual(["✓ 回款节点已全部到账（1/1）", "另有 1 个回款约定待关联里程碑"]);
  });

  it("no sync hint when the CRM already shows at least what was confirmed, or when amounts are hidden", () => {
    const paid = { nodes: [2, 2] as [number, number], payment_term_summary: [term("首付款", "30", "n1", "paid"), term("尾款", "70", "n2", "paid")] };
    expect(paymentProgress(row({ ...paid, actual: "51675.00" }), money)).toHaveLength(1);
    const hidden = row({ ...paid, actual: "172250.00", contract_value: null });
    expect(paymentProgress({ ...hidden, crm_received_amount: null as unknown as string, payment_summary: { ...hidden.payment_summary, actual_amount: null as unknown as string } }, money)).toHaveLength(1);
  });
});
