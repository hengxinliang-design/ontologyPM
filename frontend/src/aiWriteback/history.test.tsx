import { renderToStaticMarkup } from "react-dom/server";
import { beforeAll, describe, expect, it } from "vitest";

type Module = typeof import("./history");
let ui: Module;
beforeAll(async () => {
  (globalThis as unknown as { window: unknown }).window ??= { localStorage: { getItem: () => null, setItem: () => undefined, removeItem: () => undefined } };
  ui = await import("./history");
});
const text = (html: string) => html.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ");

describe("8.8.0 history (D61-D63)", () => {
  it("shows where an intervention came from and what happened, in order", () => {
    const html = renderToStaticMarkup(<ui.TimelineBody error={null} data={{
      interventionId: "act_1", projectNumber: "SEY101", title: "落实部门策略：集中推动验收回款", status: "closed", conclusion: "客户已签验收单",
      origin: { type: "strategy", title: "集中推动验收回款", status: "closed", statusLabel: "已关闭", basis: ["SEY101 预警「上线款 · 已延期 2 天」"] },
      events: [
        { at: "2026-11-10T01:52:51Z", actor: "test", title: "提交 AI 策略「集中推动验收回款」", detail: null, kind: "origin" },
        { at: "2026-11-10T02:38:28Z", actor: "head.a", title: "部门策略分解到本项目", detail: "截止 2026-11-24，负责人 李经理", kind: "created" },
        { at: "2026-11-12T08:00:00Z", actor: "pm.a", title: "状态改为「已关闭」", detail: "处理结论：客户已签验收单", kind: "status" }]
    }} />);
    expect(text(html)).toContain("来自部门策略「集中推动验收回款」（已关闭）");
    expect(text(html)).toContain("依据：SEY101 预警「上线款 · 已延期 2 天」");
    expect(text(html).indexOf("提交 AI 策略")).toBeLessThan(text(html).indexOf("状态改为「已关闭」"));
    expect(text(html)).toContain("处理结论：客户已签验收单");
  });

  it("lists past strategies with their interventions and conclusions, and nothing when there are none", () => {
    expect(renderToStaticMarkup(<ui.StrategyHistory items={[]} />)).toBe("");
    const html = renderToStaticMarkup(<ui.StrategyHistory items={[{
      noteId: "asn_1", title: "集中推动验收回款", detail: null, categoryLabel: "推动验收回款", status: "closed", statusLabel: "已关闭",
      approvedBy: "head.a", approvedAt: "2026-11-10T00:00:00Z", closedBy: "head.a", closedAt: "2026-11-20T00:00:00Z", closedReason: "已落实",
      basis: ["项目 SEY103 的预算使用率 ≥ 80（当前 85.0）"],
      followUps: [{ interventionId: "act_1", projectNumber: "SEY101", status: "closed", statusLabel: "已关闭", conclusion: "客户已签验收单" }]
    }]} />);
    expect(text(html)).toContain("历史策略 1 条");
    expect(text(html)).toContain("head.a 2026-11-20 已关闭：已落实");
    expect(text(html)).toContain("SEY101 已关闭：客户已签验收单");
  });
});
