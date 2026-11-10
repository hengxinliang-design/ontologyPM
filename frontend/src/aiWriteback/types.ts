export type AiKind = "intervention" | "riskFlag" | "strategyNote";

/** One cited fact (D55), with how it read when submitted. */
export interface BasisFact {
  type: "fact" | "alert" | "signal";
  text?: string;
  [key: string]: unknown;
}

export interface AiProposal {
  proposalId: string;
  kind: AiKind;
  kindLabel?: string;
  projectNumber: string | null;
  category: string | null;
  title: string;
  detail: string | null;
  basis: BasisFact[];
  parameters: { interventionType?: string; owner?: string; dueDate?: string };
  status: "pending" | "approved" | "rejected" | "expired" | "superseded";
  statusLabel?: string;
  submittedBy: string;
  submittedAt: string;
  expiresOn: string;
  decidedBy: string | null;
  decidedAt: string | null;
  decisionNote: string | null;
  resultId: string | null;
  basisHolds: boolean;
  basisNote: string | null;
  canApprove?: boolean;
}

export interface AiFinding {
  title: string;
  detail: string | null;
  basis: BasisFact[];
  proposalId: string;
  status: "active" | "closed" | "expired" | "superseded";
  approvedBy: string;
  approvedAt: string;
  validUntil: string;
  closedReason: string | null;
  basisHolds: boolean;
  basisNote: string | null;
}

export interface AiRiskFlag extends AiFinding { flagId: string; projectNumber: string }
export interface AiStrategyNote extends AiFinding { noteId: string; category: string; categoryLabel: string }

export interface AiOverview {
  pending: AiProposal[];
  riskFlags: AiRiskFlag[];
  interventionBasis: Record<string, { proposalId: string; basisHolds: boolean; basisNote: string | null }>;
  recent: AiProposal[];
  canApproveProjectItems: boolean;
}

/** A strategy suggestion waiting for approval, as the department review returns it. */
export interface AiStrategyProposal {
  proposalId: string;
  title: string;
  detail: string | null;
  category: string | null;
  categoryLabel: string;
  basis: BasisFact[];
  submittedBy: string;
  submittedAt: string;
  expiresOn: string;
  basisHolds: boolean;
  basisNote: string | null;
}

export interface ProposalDraft {
  kind: AiKind;
  project?: string;
  title: string;
  detail?: string;
  basis: unknown;
  interventionType?: string;
  owner?: string;
  dueDate?: string;
  category?: string;
  source?: "assistant" | "projectAnalysis";
  sourceRef?: string;
}

export const KIND_LABELS: Record<AiKind, string> = { intervention: "管理干预", riskFlag: "AI 风险标记", strategyNote: "AI 策略建议" };

// Labels of the ontology value type InterventionType (ontology.yaml, D34); App.tsx and the AI drafts share them.
export const INTERVENTION_TYPE_LABELS: Record<string, string> = { schedule: "进度", resource: "资源", payment: "回款", customer: "客户", quality: "质量", management: "其他" };

// Labels of the ontology value type StrategyCategory (8.6.0); the first five are the rule signals' own names.
export const STRATEGY_CATEGORY_LABELS: Record<string, string> = {
  push_acceptance: "推动验收回款", control_cost: "控制成本", staffing_support: "招聘或外部支援", win_contracts: "争取新合同",
  release_resources: "释放资源", other: "其他"
};
