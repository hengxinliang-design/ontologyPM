import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import type { FormEvent, KeyboardEvent } from "react";
import { Markdown } from "../AssistantMarkdown";
import { ProposalDraftCard } from "../aiWriteback/AiWriteback";
import { askAssistant, getConversation } from "./api";
import type { AssistantMessage } from "./types";

export const STARTER_QUESTIONS = ["哪些项目需要优先关注？", "目前有哪些待回款项目？", "工时超预算的项目有哪些？"];
const FALLBACK_ERROR = "PM Assistant 暂时无法回答，请稍后重试。";

/**
 * One conversation: loads it from the server, sends questions into it, and reports a newly started conversation.
 * The floating panel and the PM Assistant page each hold one, on the same conversation id kept by the app.
 */
export function useAssistantThread(conversationId: string | null, onSaved: (conversationId: string) => void) {
  const [messages, setMessages] = useState<AssistantMessage[]>([]);
  const [loading, setLoading] = useState(false);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // The conversation now on screen; a conversation this thread just started is already shown and is not reloaded.
  const shown = useRef<string | null | undefined>(undefined);

  useEffect(() => {
    if (shown.current === conversationId) return;
    shown.current = conversationId;
    setError(null);
    if (!conversationId) {
      setMessages([]);
      return;
    }
    let live = true;
    setMessages([]);
    setLoading(true);
    getConversation(conversationId)
      .then(detail => { if (live) setMessages(detail.messages); })
      .catch(err => { if (live) setError(err instanceof Error ? err.message : "无法打开这段对话。"); })
      .finally(() => { if (live) setLoading(false); });
    return () => { live = false; };
  }, [conversationId]);

  const send = useCallback(async (question: string): Promise<boolean> => {
    const content = question.trim();
    if (!content || sending) return false;
    const startedIn = shown.current ?? null;
    setMessages(previous => [...previous, { role: "user", content }]);
    setSending(true);
    setError(null);
    try {
      const answer = await askAssistant(content, startedIn);
      if (shown.current === startedIn) {
        // Still on the same conversation: show the answer; a new conversation now has its id.
        setMessages(previous => [...previous, { message_id: answer.message_id, role: "assistant", content: answer.answer,
          sources: answer.sources, suggestions: answer.suggestions, model: answer.model }]);
        shown.current = answer.conversation_id;
      }
      onSaved(answer.conversation_id);
      return true;
    } catch (err) {
      if (shown.current === startedIn) {
        setMessages(previous => previous.slice(0, -1));
        setError(err instanceof Error ? err.message : FALLBACK_ERROR);
      }
      return false;
    } finally {
      setSending(false);
    }
  }, [onSaved, sending]);

  return { messages, loading, sending, error, send };
}

function AnswerBasis({ message }: { message: AssistantMessage }) {
  const sources = message.sources ?? [];
  // 8.6.0: drafted AI suggestions (submitAiProposal) get their own card with 「提交审批」; other actions stay suggestions.
  const drafts = (message.suggestions ?? []).filter(item => item.actionType === "submitAiProposal");
  const suggestions = (message.suggestions ?? []).filter(item => item.actionType !== "submitAiProposal");
  return <>
    {drafts.map((item, index) => <ProposalDraftCard key={`draft-${index}`} item={item}
      cardRef={message.message_id ? `${message.message_id}:${(message.suggestions ?? []).indexOf(item)}` : undefined} />)}
    {suggestions.length > 0 && <div className="assistant-suggested">
      <span>建议动作（未执行）</span>
      {suggestions.map((item, index) => <article key={`${item.actionType}-${index}`} className={item.valid ? "valid" : "invalid"}>
        <header><strong>{item.displayName}</strong><i>{item.valid ? "预检通过" : "预检未通过"}</i></header>
        <p>{Object.entries(item.parameters).map(([key, value]) => `${key}：${typeof value === "string" ? value : JSON.stringify(value)}`).join("；")}</p>
        {item.reason && <p>理由：{item.reason}</p>}
        {item.reasons.length > 0 && <ul>{item.reasons.map(reason => <li key={reason}>{reason}</li>)}</ul>}
      </article>)}
      <small>请在业务页面执行；执行时系统会按当时的数据再次校验。</small>
    </div>}
    {sources.length > 0 && <details className="assistant-basis">
      <summary>依据 {sources.length} 项</summary>
      <div>{sources.map(source => <small key={source}>{source}</small>)}</div>
    </details>}
  </>;
}

export function AssistantThread({ thread, compact = false }: {
  thread: ReturnType<typeof useAssistantThread>;
  compact?: boolean;
}) {
  const { messages, loading, sending, error, send } = thread;
  const [draft, setDraft] = useState("");
  const scroller = useRef<HTMLDivElement>(null);
  const input = useRef<HTMLTextAreaElement>(null);
  // Where to scroll after the next render: to the latest question, so a new answer is read from its first line.
  const scrollTo = useRef<"question" | "top" | null>("top");
  const previousCount = useRef(0);

  useLayoutEffect(() => {
    const box = scroller.current;
    if (!box) return;
    if (messages.length > previousCount.current && previousCount.current === 0 && !sending) scrollTo.current = "top";
    previousCount.current = messages.length;
    if (scrollTo.current === "top") {
      box.scrollTop = 0;
    } else if (scrollTo.current === "question") {
      const questions = box.querySelectorAll<HTMLElement>(".assistant-message.user");
      const last = questions[questions.length - 1];
      if (last) box.scrollTop = Math.max(0, last.offsetTop - 12);
    }
    if (!sending) scrollTo.current = null;
  }, [messages, sending]);

  useEffect(() => {
    const field = input.current;
    if (!field) return;
    field.style.height = "auto";
    field.style.height = `${Math.min(field.scrollHeight, 160)}px`;
  }, [draft]);

  async function ask(question: string) {
    if (!question.trim() || sending) return;
    scrollTo.current = "question";
    setDraft("");
    const ok = await send(question);
    if (!ok) setDraft(current => current || question);
    input.current?.focus();
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    void ask(draft);
  }

  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault();
      void ask(draft);
    }
  }

  return <div className={`assistant-thread ${compact ? "compact" : ""}`}>
    <div className="assistant-messages" ref={scroller} aria-live="polite">
      {loading && <p className="assistant-note">正在打开对话…</p>}
      {!loading && !messages.length && <div className="assistant-welcome">
        <strong>可以问我什么？</strong>
        <p>我会调用系统的本体函数和对象查询，按「结论、数据、依据」回答；资料不足时说明缺少什么。需要改数据时只给出经系统预检的建议，不会直接修改。</p>
        <div className="assistant-starters">{STARTER_QUESTIONS.map(question => <button key={question} type="button" disabled={sending} onClick={() => void ask(question)}>{question}</button>)}</div>
      </div>}
      {messages.map((message, index) => <article key={message.message_id ?? `${message.role}-${index}`} className={`assistant-message ${message.role}`}>
        {message.role === "assistant" ? <>
          <span className="assistant-author">PM Assistant</span>
          <Markdown text={message.content} />
          <AnswerBasis message={message} />
        </> : <p>{message.content}</p>}
      </article>)}
      {sending && <article className="assistant-message assistant pending"><span className="assistant-author">PM Assistant</span><p>正在查询系统本体并整理回答…</p></article>}
    </div>
    {error && <p className="assistant-error" role="alert">{error}</p>}
    <form className="assistant-composer" onSubmit={submit}>
      <label htmlFor={compact ? "assistant-input-panel" : "assistant-input-page"} className="sr-only">你的问题</label>
      <textarea id={compact ? "assistant-input-panel" : "assistant-input-page"} ref={input} rows={1} value={draft} maxLength={2000}
        placeholder={messages.length ? "继续追问，Enter 发送，Shift+Enter 换行" : "例如：哪些项目本月需要跟进？"}
        disabled={loading} onChange={event => setDraft(event.target.value)} onKeyDown={onKeyDown} />
      <button className="button primary" disabled={sending || loading || !draft.trim()} type="submit">{sending ? "回答中" : "发送"}</button>
    </form>
  </div>;
}
