import { renderToStaticMarkup } from "react-dom/server";
import { beforeAll, describe, expect, it } from "vitest";
import type { PmAssistantSuggestion } from "../types";
import type { AiOverview, AiProposal, AiStrategyNote } from "./types";

// The components import the API client, which reads the session from localStorage when loaded.
type Module = typeof import("./AiWriteback");
let ui: Module;
beforeAll(async () => {
  (globalThis as unknown as { window: unknown }).window ??= { localStorage: { getItem: () => null, setItem: () => undefined, removeItem: () => undefined } };
  ui = await import("./AiWriteback");
});

const act = async () => true;
const proposal = (over: Partial<AiProposal> = {}): AiProposal => ({
  proposalId: "aip_1", kind: "riskFlag", kindLabel: "AI 风险标记", projectNumber: "WDR101", category: null, title: "预算可能不够",
  detail: "已用大半", basis: [{ type: "fact", text: "项目 WDR101 的预算人天 ≥ 100（当前 120.00）" }], parameters: {}, status: "pending",
  submittedBy: "pmo.a", submittedAt: "2026-11-10T01:00:00Z", expiresOn: "2026-11-17", decidedBy: null, decidedAt: null,
  decisionNote: null, resultId: null, basisHolds: true, basisNote: null, canApprove: true, ...over
});
const overview = (over: Partial<AiOverview> = {}): AiOverview => ({
  pending: [], riskFlags: [], interventionBasis: {}, recent: [], canApproveProjectItems: true, ...over
});
const text = (html: string) => html.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ");

describe("project drawer: AI 建议与风险标记", () => {
  it("is not shown when there is nothing to approve, hold or review", () => {
    expect(renderToStaticMarkup(<ui.ProjectAiSection overview={overview()} saving={false} act={act} />)).toBe("");
    expect(renderToStaticMarkup(<ui.ProjectAiSection overview={null} saving={false} act={act} />)).toBe("");
  });

  it("shows a pending suggestion with its facts and the approver's buttons", () => {
    const html = renderToStaticMarkup(<ui.ProjectAiSection overview={overview({ pending: [proposal()] })} saving={false} act={act} />);
    expect(text(html)).toContain("预算可能不够");
    expect(text(html)).toContain("当前 120.00");
    expect(text(html)).toContain("2026-11-17 前处理");
    expect(html).toMatch(/<button[^>]*>批准<\/button>/);
    expect(html).toMatch(/>驳回</);
  });

  it("does not let a suggestion whose facts no longer hold be approved", () => {
    const stale = proposal({ basisHolds: false, basisNote: "依据不再成立：当前 60.00" });
    const html = renderToStaticMarkup(<ui.ProjectAiSection overview={overview({ pending: [stale] })} saving={false} act={act} />);
    expect(html).toMatch(/<button[^>]*disabled=""[^>]*>批准<\/button>/);
    expect(text(html)).toContain("依据不再成立：当前 60.00");
  });

  it("tells someone without the role to wait, and folds what was decided", () => {
    const html = renderToStaticMarkup(<ui.ProjectAiSection overview={overview({
      pending: [proposal({ canApprove: false })],
      recent: [proposal({ proposalId: "aip_2", status: "expired", statusLabel: "已过期", decidedBy: "system", decidedAt: "2026-11-09T00:00:00Z" })]
    })} saving={false} act={act} />);
    expect(html).not.toMatch(/>批准</);
    expect(text(html)).toContain("等待有权限的人批准");
    expect(text(html)).toContain("最近 30 天处理过 1 条");
    expect(text(html)).toContain("系统 2026-11-09");
  });
});

describe("部门经营: AI 策略建议", () => {
  const note: AiStrategyNote = {
    noteId: "asn_1", category: "win_contracts", categoryLabel: "争取新合同", title: "争取 WDR101 二期", detail: null,
    basis: [{ type: "signal", text: "策略信号「争取新合同」" }], proposalId: "aip_9", status: "active", approvedBy: "head.a",
    approvedAt: "2026-11-10T00:00:00Z", validUntil: "2026-12-10", closedReason: null, basisHolds: true, basisNote: null
  };

  it("renders nothing without notes or suggestions", () => {
    expect(renderToStaticMarkup(<ui.StrategyAiPanel notes={[]} proposals={[]} canApprove onChanged={() => undefined} />)).toBe("");
  });

  it("shows renew and close only to those who may approve", () => {
    const allowed = renderToStaticMarkup(<ui.StrategyAiPanel notes={[note]} proposals={[]} canApprove onChanged={() => undefined} />);
    const other = renderToStaticMarkup(<ui.StrategyAiPanel notes={[note]} proposals={[]} canApprove={false} onChanged={() => undefined} />);
    expect(text(allowed)).toContain("有效至 2026-12-10");
    expect(allowed).toMatch(/>续期</);
    expect(other).not.toMatch(/>续期</);
  });
});

describe("PM Assistant: drafted AI suggestion card", () => {
  const card = (valid: boolean, reasons: string[] = []): PmAssistantSuggestion => ({
    actionType: "submitAiProposal", displayName: "提交 AI 建议", valid, reasons,
    parameters: { kind: "intervention", project: "WDR101", title: "追加预算", dueDate: "2026-11-30",
      basis: [{ type: "alert", project: "WDR101", code: "MILESTONE_DUE" }] }
  });

  it("offers 提交审批 for a card that passed the pre-check, with its facts readable", () => {
    const html = renderToStaticMarkup(<ui.ProposalDraftCard item={card(true)} />);
    expect(text(html)).toContain("管理干预候选");
    expect(text(html)).toContain("截止 2026-11-30");
    expect(text(html)).toContain("依据：WDR101 预警 MILESTONE_DUE");
    expect(html).toMatch(/<button[^>]*>提交审批<\/button>/);
    expect(html).not.toMatch(/<button[^>]*disabled=""[^>]*>提交审批/);
  });

  it("uses the readable wording of the facts when the pre-check gave it", () => {
    const html = renderToStaticMarkup(<ui.ProposalDraftCard item={{ ...card(true), basisTexts: ["WDR101 预警「系统上线 · 已延期 3 天」"] }} />);
    expect(text(html)).toContain("依据：WDR101 预警「系统上线 · 已延期 3 天」");
  });

  it("disables it and says why when the pre-check failed", () => {
    const html = renderToStaticMarkup(<ui.ProposalDraftCard item={card(false, ["依据不再成立：当前 80.00"])} />);
    expect(html).toMatch(/<button[^>]*disabled=""[^>]*>提交审批/);
    expect(text(html)).toContain("依据不再成立：当前 80.00");
  });
});
