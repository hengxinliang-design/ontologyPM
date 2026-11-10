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

describe("8.7.0: strategies the categories do not cover (D56)", () => {
  it("says on the card that the category is not covered and will go in as 其他", () => {
    const html = renderToStaticMarkup(<ui.ProposalDraftCard item={{
      actionType: "submitAiProposal", displayName: "提交 AI 建议", valid: true, reasons: [],
      notes: ["AI 提出的类别「售前与商机管理」不在现有策略类别中，将按「其他」提交"],
      parameters: { kind: "strategyNote", category: "other", proposedCategory: "售前与商机管理", title: "清理停滞商机", basis: [] }
    }} />);
    expect(text(html)).toContain("其他（AI：售前与商机管理）");
    expect(text(html)).toContain("不在现有策略类别中");
  });

  it("lists the gaps with counts, decisions and examples, and shows nothing without gaps", () => {
    expect(renderToStaticMarkup(<ui.StrategyGaps gaps={[]} />)).toBe("");
    const html = renderToStaticMarkup(<ui.StrategyGaps gaps={[{
      proposedCategory: "售前与商机管理", count: 2, pending: 1, approved: 1, rejected: 0, expired: 0, latestAt: "2026-11-10T00:00:00Z",
      examples: [{ proposalId: "aip_1", title: "清理停滞商机", status: "approved", statusLabel: "已批准", basis: [{ type: "fact", text: "成本对象 STL302 的疑似停滞 等于 是（当前 是）" }] }]
    }]} />);
    expect(text(html)).toContain("规则未覆盖的策略");
    expect(text(html)).toContain("售前与商机管理 2 已采纳 1 · 待批 1");
    expect(text(html)).toContain("清理停滞商机 （已批准）");
    expect(text(html)).toContain("STL302 的疑似停滞");
  });
});

describe("8.7.0: strategies carried out on projects (D58, D59)", () => {
  const base: AiStrategyNote = {
    noteId: "asn_2", category: "push_acceptance", categoryLabel: "推动验收回款", title: "集中推动验收回款", detail: null, basis: [],
    proposalId: "aip_3", status: "active", approvedBy: "head.a", approvedAt: "2026-11-10T00:00:00Z", validUntil: "2026-12-10",
    closedReason: null, basisHolds: true, basisNote: null
  };

  it("shows how far a strategy has been carried out, and says when it may be closed", () => {
    const partial = renderToStaticMarkup(<ui.FollowUps note={{ ...base, readyToClose: false, followUps: [
      { interventionId: "a", projectNumber: "SEY101", status: "in_progress", dueDate: "2026-11-20", overdue: false },
      { interventionId: "b", projectNumber: "SEY103", status: "closed", dueDate: "2026-11-20", overdue: false }] }} />);
    expect(text(partial)).toContain("落实进度 1/2");
    expect(text(partial)).toContain("SEY101 处理中 · SEY103 已关闭");
    expect(text(partial)).not.toContain("可以关闭这条策略");
    const done = renderToStaticMarkup(<ui.FollowUps note={{ ...base, readyToClose: true, followUps: [
      { interventionId: "b", projectNumber: "SEY103", status: "closed", dueDate: "2026-11-20", overdue: false }] }} />);
    expect(text(done)).toContain("干预已全部关闭，可以关闭这条策略");
    expect(text(renderToStaticMarkup(<ui.FollowUps note={base} />))).toContain("未分解到项目");
  });

  it("offers the cited projects, all ticked, when adopting", () => {
    const html = renderToStaticMarkup(<ui.AdoptForm projects={["SEY101", "SEY103"]} saving={false} onAdopt={() => undefined} onCancel={() => undefined} />);
    expect((html.match(/type="checkbox" checked=""/g) ?? []).length).toBe(2);
    expect(text(html)).toContain("采纳并建 2 项干预");
    expect(text(renderToStaticMarkup(<ui.AdoptForm projects={[]} saving={false} onAdopt={() => undefined} onCancel={() => undefined} />)))
      .toContain("只采纳，不建干预");
  });
});

describe("8.8.0: a submitted card says who, when and what happened (user 2026-11-10)", () => {
  const card = (submission?: object) => ({
    actionType: "submitAiProposal", displayName: "提交 AI 建议", valid: true, reasons: [],
    parameters: { kind: "riskFlag", project: "SEY103", title: "验收前预算耗尽", basis: [] },
    ...(submission ? { submission } : {})
  }) as never;

  it("shows the submitter, time and status instead of the button once submitted", () => {
    const html = renderToStaticMarkup(<ui.ProposalDraftCard item={card({ proposalId: "aip_1", status: "pending", statusLabel: "待批准",
      submittedBy: "test", submittedAt: "2026-11-10T02:35:56Z", expiresOn: "2026-11-17", decidedBy: null, decidedAt: null, decisionNote: null })} />);
    expect(text(html)).toContain("待批准");
    expect(text(html)).toMatch(/test 于 2026-11-10 \d\d:\d\d 提交审批/);
    expect(text(html)).toContain("2026-11-17 前未处理将过期");
    expect(html).not.toMatch(/>提交审批</);
  });

  it("shows the decision, and offers 重新提交 only when the last submission no longer counts", () => {
    const approved = renderToStaticMarkup(<ui.ProposalDraftCard item={card({ proposalId: "aip_1", status: "approved", statusLabel: "已批准",
      submittedBy: "test", submittedAt: "2026-11-10T02:35:56Z", expiresOn: "2026-11-17", decidedBy: "head.a", decidedAt: "2026-11-10T03:00:00Z", decisionNote: "同意" })} />);
    expect(text(approved)).toMatch(/head\.a 于 2026-11-10 \d\d:\d\d 已批准：同意/);
    expect(approved).not.toMatch(/重新提交/);
    const expired = renderToStaticMarkup(<ui.ProposalDraftCard item={card({ proposalId: "aip_1", status: "expired", statusLabel: "已过期",
      submittedBy: "test", submittedAt: "2026-11-01T02:35:56Z", expiresOn: "2026-11-08", decidedBy: "system", decidedAt: "2026-11-09T00:00:00Z", decisionNote: "超过待批天数" })} />);
    expect(text(expired)).toContain("系统 于");
    expect(expired).toMatch(/>重新提交</);
  });
});

