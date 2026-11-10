import type { PmAssistantResponse, PmAssistantSuggestion } from "../types";

export interface AssistantMessage {
  message_id?: string;
  role: "user" | "assistant";
  content: string;
  sources?: string[];
  suggestions?: PmAssistantSuggestion[];
  model?: string | null;
  created_at?: string;
}

export interface ConversationSummary {
  conversation_id: string;
  title: string;
  created_at: string;
  updated_at: string;
  message_count: number;
}

export interface ConversationDetail extends Omit<ConversationSummary, "message_count"> {
  messages: AssistantMessage[];
}

export interface AssistantAnswer extends PmAssistantResponse {
  conversation_id: string;
  /** The saved answer's id (8.8.0): cards submitted from it are linked by it, so their state survives a reload. */
  message_id?: string;
}
