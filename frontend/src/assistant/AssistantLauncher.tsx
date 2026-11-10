import { useCallback, useState } from "react";
import { AssistantThread, useAssistantThread } from "./AssistantThread";
import "./assistant.css";

/**
 * The floating PM Assistant for quick questions on any page. It works in the same conversation as the PM Assistant
 * page (the app keeps the id), saves into the same history, and can open that page to read at full width.
 */
export function AssistantLauncher({ conversationId, onSelect, onChanged, onOpenPage }: {
  conversationId: string | null;
  onSelect: (conversationId: string | null) => void;
  onChanged: () => void;
  onOpenPage: () => void;
}) {
  const [open, setOpen] = useState(false);
  const saved = useCallback((id: string) => { onSelect(id); onChanged(); }, [onSelect, onChanged]);
  const thread = useAssistantThread(open ? conversationId : null, saved);

  return <div className="assistant-launcher">
    {open && <section className="assistant-panel" role="dialog" aria-label="PM Assistant">
      <header className="assistant-panel-head">
        <h2>PM Assistant</h2>
        <div>
          <button type="button" className="assistant-panel-action" disabled={!conversationId || thread.sending} onClick={() => onSelect(null)}>新对话</button>
          <button type="button" className="assistant-panel-action" onClick={() => { setOpen(false); onOpenPage(); }}>历史与全屏</button>
          <button type="button" className="assistant-panel-close" aria-label="关闭 PM Assistant" onClick={() => setOpen(false)}>×</button>
        </div>
      </header>
      <AssistantThread thread={thread} compact />
    </section>}
    <button type="button" className="assistant-launcher-button" aria-expanded={open} onClick={() => setOpen(!open)}><span>AI</span><strong>PM Assistant</strong></button>
  </div>;
}
