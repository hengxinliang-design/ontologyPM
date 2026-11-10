// AI suggestions written back after a person approves them (ontology 8.6.0, requirements baseline D51-D55).
// Every write is an ontology action on the business channel: it checks the cited facts, the role and writes the audit.
import { ontologyAction, ontologyFunction } from "../api";
import type { AiOverview, ProposalDraft } from "./types";

export function fetchAiOverview(projectNumber: string): Promise<AiOverview> {
  return ontologyFunction<AiOverview>("aiSuggestionOverview", { project: projectNumber });
}

export function submitAiProposal(draft: ProposalDraft): Promise<void> {
  return ontologyAction("submitAiProposal", { ...draft });
}

export const approveAiProposal = (proposal: string, note?: string) => ontologyAction("approveAiProposal", { proposal, note: note || undefined });
export const rejectAiProposal = (proposal: string, note?: string) => ontologyAction("rejectAiProposal", { proposal, note: note || undefined });
export const renewAiRiskFlag = (flag: string) => ontologyAction("renewAiRiskFlag", { flag });
export const closeAiRiskFlag = (flag: string, reason?: string) => ontologyAction("closeAiRiskFlag", { flag, reason: reason || undefined });
export const renewAiStrategyNote = (note: string) => ontologyAction("renewAiStrategyNote", { note });
export const closeAiStrategyNote = (note: string, reason?: string) => ontologyAction("closeAiStrategyNote", { note, reason: reason || undefined });
