import { renderToStaticMarkup } from "react-dom/server";
import { beforeAll, describe, expect, it } from "vitest";
import type { AssistantMessage } from "./types";

type Module = typeof import("./AssistantThread");
let ui: Module;
beforeAll(async () => {
  (globalThis as unknown as { window: unknown }).window ??= { localStorage: { getItem: () => null, setItem: () => undefined, removeItem: () => undefined } };
  ui = await import("./AssistantThread");
});

const thread = (messages: AssistantMessage[], extra: Partial<{ loading: boolean; sending: boolean; error: string | null }> = {}) => ({
  messages, loading: false, sending: false, error: null, send: async () => true, ...extra
});
const text = (html: string) => html.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ");

describe("an answer in the thread", () => {
  const answer: AssistantMessage = {
    role: "assistant", content: "**结论** WDR101 预算偏紧。",
    sources: ["函数 项目预算消耗（projectBudgetStatus）", "对象 项目"],
    suggestions: [
      { actionType: "submitAiProposal", displayName: "提交 AI 提案", valid: true, reasons: [],
        parameters: { kind: "riskFlag", project: "WDR101", title: "预算可能不够", basis: [{ type: "fact", objectType: "Project", primaryKey: "WDR101", property: "plannedPersonDays", op: ">=", value: 100 }] } },
      { actionType: "editMilestone", displayName: "编辑里程碑", valid: true, reasons: [], parameters: { milestone: "ms_1", plannedEnd: "2026-12-31" } }
    ]
  };

  it("shows the AI suggestion as a card, other actions as suggestions, and folds the sources", () => {
    const html = renderToStaticMarkup(<ui.AssistantThread thread={thread([{ role: "user", content: "WDR101 有什么风险？" }, answer])} />);
    expect(text(html)).toContain("风险标记候选");
    expect(html).toMatch(/>提交审批</);
    expect(text(html)).toContain("建议动作（未执行）");
    expect(text(html)).toContain("编辑里程碑");
    expect(html).toMatch(/<details class="assistant-basis"><summary>依据 2 项<\/summary>/);
  });

  it("shows the welcome with starter questions when empty, and the pending line while sending", () => {
    expect(text(renderToStaticMarkup(<ui.AssistantThread thread={thread([])} />))).toContain("可以问我什么？");
    const sending = renderToStaticMarkup(<ui.AssistantThread thread={thread([{ role: "user", content: "问" }], { sending: true })} />);
    expect(text(sending)).toContain("正在查询系统本体并整理回答");
    expect(sending).toMatch(/<button[^>]*disabled=""[^>]*>回答中<\/button>/);
  });
});
