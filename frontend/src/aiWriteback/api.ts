// AI suggestions written back after a person approves them (ontology 8.6.0, requirements baseline D51-D55).
// Every write is an ontology action on the business channel: it checks the cited facts, the role and writes the audit.
import { ontologyAction, ontologyActionObjects, ontologyFunction } from "../api";
import type { CardSubmission } from "../types";
import type { AiOverview, ProposalDraft } from "./types";

export function fetchAiOverview(projectNumber: string): Promise<AiOverview> {
  return ontologyFunction<AiOverview>("aiSuggestionOverview", { project: projectNumber });
}

export function submitAiProposal(draft: ProposalDraft): Promise<void> {
  return ontologyAction("submitAiProposal", { ...draft });
}

/** Submit a card and return what it became: the new suggestion's submitter, time and status. */
export async function submitAiProposalCard(draft: ProposalDraft): Promise<CardSubmission | null> {
  const objects = await ontologyActionObjects("submitAiProposal", { ...draft });
  const proposal = objects.find(item => item.objectType === "AiProposal")?.properties;
  if (!proposal) return null;
  return {
    proposalId: String(proposal.proposalId), status: proposal.status as CardSubmission["status"], statusLabel: "待批准",
    submittedBy: String(proposal.submittedBy), submittedAt: String(proposal.submittedAt), expiresOn: String(proposal.expiresOn),
    decidedBy: null, decidedAt: null, decisionNote: null
  };
}

export const approveAiProposal = (proposal: string, note?: string, breakdown?: { projects: string[]; dueDate: string }) =>
  ontologyAction("approveAiProposal", { proposal, note: note || undefined, ...(breakdown && breakdown.projects.length ? breakdown : {}) });
export const rejectAiProposal = (proposal: string, note?: string) => ontologyAction("rejectAiProposal", { proposal, note: note || undefined });
export const renewAiRiskFlag = (flag: string) => ontologyAction("renewAiRiskFlag", { flag });
export const closeAiRiskFlag = (flag: string, reason?: string) => ontologyAction("closeAiRiskFlag", { flag, reason: reason || undefined });
export const renewAiStrategyNote = (note: string) => ontologyAction("renewAiStrategyNote", { note });
export const breakDownAiStrategyNote = (note: string, breakdown: { projects: string[]; dueDate: string }) =>
  ontologyAction("breakDownAiStrategyNote", { note, ...breakdown });
export const closeAiStrategyNote = (note: string, reason?: string) => ontologyAction("closeAiStrategyNote", { note, reason: reason || undefined });
