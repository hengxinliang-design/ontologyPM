// PM Assistant: questions and the asking user's saved conversations (requirements baseline D50).
import { request } from "../api";
import type { AssistantAnswer, ConversationDetail, ConversationSummary } from "./types";

/** Ask a question; with a conversation id it is a follow-up there, without one a new conversation is started. */
export async function askAssistant(message: string, conversationId: string | null): Promise<AssistantAnswer> {
  const response = await request<{ data: AssistantAnswer }>("/api/v1/pm-assistant/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(conversationId ? { message, conversation_id: conversationId } : { message })
  });
  return response.data;
}

export async function listConversations(query = ""): Promise<ConversationSummary[]> {
  const params = new URLSearchParams({ limit: "200" });
  if (query.trim()) params.set("q", query.trim());
  return (await request<{ data: ConversationSummary[] }>(`/api/v1/pm-assistant/conversations?${params}`)).data;
}

export async function getConversation(conversationId: string): Promise<ConversationDetail> {
  return (await request<{ data: ConversationDetail }>(`/api/v1/pm-assistant/conversations/${encodeURIComponent(conversationId)}`)).data;
}

export async function deleteConversation(conversationId: string): Promise<void> {
  await request(`/api/v1/pm-assistant/conversations/${encodeURIComponent(conversationId)}`, { method: "DELETE" });
}

export async function deleteAllConversations(): Promise<number> {
  return (await request<{ data: { deleted: number } }>("/api/v1/pm-assistant/conversations", { method: "DELETE" })).data.deleted;
}
