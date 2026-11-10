import { useCallback, useEffect, useState } from "react";
import { deleteAllConversations, deleteConversation, listConversations } from "./api";
import { AssistantThread, useAssistantThread } from "./AssistantThread";
import "./assistant.css";
import { whenUsed } from "./time";
import type { ConversationSummary } from "./types";

/**
 * The PM Assistant page (requirements baseline D50): the user's saved conversations on the left, one conversation on
 * the right. Only the user's own conversations are listed; they stay until the user deletes them.
 */
export function AssistantPage({ conversationId, onSelect, revision, onChanged }: {
  conversationId: string | null;
  onSelect: (conversationId: string | null) => void;
  revision: number;
  onChanged: () => void;
}) {
  const [query, setQuery] = useState("");
  const [conversations, setConversations] = useState<ConversationSummary[] | null>(null);
  const [listError, setListError] = useState<string | null>(null);
  const saved = useCallback((id: string) => { onSelect(id); onChanged(); }, [onSelect, onChanged]);
  const thread = useAssistantThread(conversationId, saved);

  useEffect(() => {
    let live = true;
    const timer = window.setTimeout(() => {
      listConversations(query)
        .then(rows => { if (live) { setConversations(rows); setListError(null); } })
        .catch(err => { if (live) setListError(err instanceof Error ? err.message : "无法读取历史对话。"); });
    }, query ? 250 : 0);
    return () => { live = false; window.clearTimeout(timer); };
  }, [query, revision]);

  async function remove(item: ConversationSummary) {
    if (!window.confirm(`确认删除对话「${item.title}」？删除后无法恢复。`)) return;
    try {
      await deleteConversation(item.conversation_id);
      if (item.conversation_id === conversationId) onSelect(null);
      onChanged();
    } catch (err) {
      setListError(err instanceof Error ? err.message : "删除失败，请稍后重试。");
    }
  }

  async function removeAll() {
    if (!window.confirm("确认删除你的全部历史对话？删除后无法恢复。")) return;
    try {
      await deleteAllConversations();
      onSelect(null);
      onChanged();
    } catch (err) {
      setListError(err instanceof Error ? err.message : "删除失败，请稍后重试。");
    }
  }

  const current = conversations?.find(item => item.conversation_id === conversationId);
  return <section className="assistant-page">
    <aside className="assistant-history" aria-label="历史对话">
      <div className="assistant-history-head">
        <button type="button" className="button primary" onClick={() => onSelect(null)} disabled={!conversationId}>＋ 新对话</button>
        <input type="search" value={query} onChange={event => setQuery(event.target.value)} placeholder="搜索问题或回答" aria-label="搜索历史对话" />
      </div>
      {listError && <p className="assistant-error" role="alert">{listError}</p>}
      <ul className="assistant-history-list">
        {conversations?.map(item => <li key={item.conversation_id} className={item.conversation_id === conversationId ? "active" : ""}>
          <button type="button" className="assistant-history-open" onClick={() => onSelect(item.conversation_id)} aria-current={item.conversation_id === conversationId ? "true" : undefined}>
            <strong>{item.title}</strong>
            <small>{whenUsed(item.updated_at)} · {Math.ceil(item.message_count / 2)} 问</small>
          </button>
          <button type="button" className="assistant-history-delete" aria-label={`删除对话 ${item.title}`} title="删除" onClick={() => void remove(item)}>×</button>
        </li>)}
      </ul>
      {conversations && !conversations.length && <p className="assistant-note">{query ? "没有找到相关的对话。" : "还没有历史对话。提问后会自动保存在这里，只有你自己能看到。"}</p>}
      {conversations && conversations.length > 0 && !query && <button type="button" className="assistant-history-clear" onClick={() => void removeAll()}>删除全部对话</button>}
    </aside>
    <div className="assistant-main">
      <header className="assistant-main-head">
        <div><h2>{current?.title ?? (conversationId ? "对话" : "新对话")}</h2>
          <small>{conversationId ? "可以继续追问，助手会参考这段对话最近 6 条消息" : "按你的权限调用系统本体回答；问答自动保存到历史对话，只有你自己能看到"}</small></div>
      </header>
      <AssistantThread thread={thread} />
    </div>
  </section>;
}
