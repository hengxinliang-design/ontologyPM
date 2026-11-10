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
/** D58/D59: an intervention a strategy was broken into, on one of its projects. */
export interface StrategyFollowUp { interventionId: string; projectNumber: string; status: "open" | "in_progress" | "closed"; dueDate: string | null; overdue: boolean; conclusion?: string | null }

export interface AiStrategyNote extends AiFinding {
  noteId: string; category: string; categoryLabel: string; proposedCategory?: string | null;
  followUps?: StrategyFollowUp[]; readyToClose?: boolean; citedProjects?: string[];
}

export interface AiOverview {
  pending: AiProposal[];
  riskFlags: AiRiskFlag[];
  interventionBasis: Record<string, { proposalId: string; basisHolds: boolean; basisNote: string | null; strategyTitle?: string; strategyStatus?: string }>;
  recent: AiProposal[];
  canApproveProjectItems: boolean;
}

/** A strategy suggestion waiting for approval, as the department review returns it. */
export interface AiStrategyProposal {
  proposalId: string;
  title: string;
  detail: string | null;
  category: string | null;
  proposedCategory?: string | null;
  categoryLabel: string;
  basis: BasisFact[];
  /** D58: the projects its facts are about, the choices for breaking it into interventions. */
  citedProjects?: string[];
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

/** D56: strategies no existing category covers, grouped by the category the model proposed (last 90 days). */
export interface AiStrategyGap {
  proposedCategory: string;
  count: number;
  pending: number;
  approved: number;
  rejected: number;
  expired: number;
  latestAt: string;
  examples: Array<{ proposalId: string; title: string; status: string; statusLabel: string; basis: BasisFact[] }>;
}

/** D61/D62 (8.8.0): an intervention's timeline. */
export interface InterventionHistory {
  interventionId: string;
  projectNumber: string;
  title: string;
  status: string;
  conclusion: string | null;
  origin: { type: "strategy" | "suggestion"; title: string; category?: string; status: string | null; statusLabel: string | null; basis: string[] } | null;
  events: Array<{ at: string | null; actor: string | null; title: string; detail: string | null; kind: string }>;
}

/** D61: a strategy no longer in force, with its interventions and how they ended. */
export interface StrategyHistoryItem {
  noteId: string; title: string; detail: string | null; categoryLabel: string; status: string; statusLabel: string;
  approvedBy: string; approvedAt: string; closedBy: string | null; closedAt: string | null; closedReason: string | null;
  basis: string[];
  followUps: Array<{ interventionId: string; projectNumber: string; status: string; statusLabel: string; conclusion: string | null }>;
}

