"use client";
import { useEffect, useRef, useState } from "react";
import type { ChatActions, ChatMessage } from "@/lib/types";
import { money, qty } from "@/lib/format";

interface Props {
  open: boolean;
  onToggle: () => void;
  messages: ChatMessage[];
  loading: boolean;
  error: string | null;
  onSend: (text: string) => void;
}

export function describeActions(actions: ChatActions | null | undefined) {
  const out: { key: string; status: "executed" | "failed"; text: string }[] = [];
  if (!actions) return out;
  (actions.trades ?? []).forEach((t, i) => {
    const verb = t.side === "buy" ? "Bought" : "Sold";
    const text = t.status === "executed"
      ? `${verb} ${qty(t.quantity)} ${t.ticker}${t.price != null ? ` at ${money(t.price)}` : ""}`
      : `${t.side === "buy" ? "Buy" : "Sell"} ${qty(t.quantity)} ${t.ticker} failed${t.error ? `: ${t.error}` : ""}`;
    out.push({ key: `t${i}`, status: t.status, text });
  });
  (actions.watchlist_changes ?? []).forEach((w, i) => {
    const text = w.status === "executed"
      ? w.action === "add" ? `Added ${w.ticker} to watchlist` : `Removed ${w.ticker} from watchlist`
      : `${w.action === "add" ? "Add" : "Remove"} ${w.ticker} failed${w.error ? `: ${w.error}` : ""}`;
    out.push({ key: `w${i}`, status: w.status, text });
  });
  return out;
}

const SUGGESTIONS = ["How is my portfolio doing?", "Buy 5 AAPL", "Add PYPL to my watchlist"];

export default function ChatPanel({ open, onToggle, messages, loading, error, onSend }: Props) {
  const [draft, setDraft] = useState("");
  const scroller = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const el = scroller.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages.length, loading, open]);

  const send = (e?: React.FormEvent) => {
    e?.preventDefault();
    const text = draft.trim();
    if (!text || loading) return;
    onSend(text);
    setDraft("");
  };

  return (
    <aside data-testid="chat-panel" data-open={open ? "true" : "false"} aria-label="AI assistant"
      className={`flex min-h-0 flex-col border-l border-line bg-panel ${open ? "w-[360px]" : "w-10"} shrink-0`}>
      <header className={`flex items-center border-b border-line ${open ? "justify-between px-3 py-2" : "justify-center py-2"}`}>
        {open && (
          <div className="flex items-baseline gap-2">
            <h2 className="text-[13px] font-semibold">Assistant</h2>
            <span className="text-[11px] text-ink-3">Can trade and edit your watchlist</span>
          </div>
        )}
        <button data-testid="chat-toggle" type="button" onClick={onToggle}
          aria-expanded={open} aria-label={open ? "Collapse assistant" : "Open assistant"}
          className="flex h-6 w-6 items-center justify-center rounded text-ink-2 hover:bg-raised hover:text-ink">
          {open ? "»" : "«"}
        </button>
      </header>

      {!open && (
        <button type="button" onClick={onToggle}
          className="mt-3 text-[12px] text-ink-2 [writing-mode:vertical-rl] hover:text-accent">
          Ask FinAlly
        </button>
      )}

      {open && (
        <>
          <div ref={scroller} className="min-h-0 flex-1 space-y-3 overflow-y-auto px-3 py-3">
            {messages.length === 0 && !loading && (
              <div className="text-ink-2">
                <p className="mb-2">Ask about your positions, risk or a symbol, or tell me to place a trade.</p>
                <div className="flex flex-wrap gap-1.5">
                  {SUGGESTIONS.map((s) => (
                    <button key={s} type="button" onClick={() => onSend(s)}
                      className="rounded border border-line px-2 py-0.5 text-[12px] hover:border-blue hover:text-ink">
                      {s}
                    </button>
                  ))}
                </div>
              </div>
            )}
            {messages.map((m) => (
              <div key={m.id} data-testid="chat-message" data-role={m.role}
                className={m.role === "user" ? "ml-8 rounded bg-raised px-2.5 py-1.5" : "mr-4 border-l-2 border-blue pl-2.5"}>
                <p className="whitespace-pre-wrap text-[13px] leading-snug">{m.content}</p>
                {describeActions(m.actions).map((a) => (
                  <div key={a.key} data-testid="chat-action" data-status={a.status}
                    className={`mt-1.5 flex items-start gap-1.5 rounded px-2 py-1 text-[12px] ${a.status === "executed" ? "bg-up/10 text-up" : "bg-down/10 text-down"}`}>
                    <span aria-hidden="true">{a.status === "executed" ? "✓" : "✕"}</span>
                    <span className="text-ink">{a.text}</span>
                  </div>
                ))}
              </div>
            ))}
            {loading && (
              <div data-testid="chat-loading" role="status" aria-label="Assistant is thinking"
                className="chat-dots mr-4 border-l-2 border-blue py-1 pl-2.5">
                <span /><span /><span />
              </div>
            )}
            {error && <p role="alert" className="text-[12px] text-down">{error}</p>}
          </div>

          <form onSubmit={send} className="border-t border-line p-2">
            <div className="flex gap-2">
              <input data-testid="chat-input" value={draft} onChange={(e) => setDraft(e.target.value)}
                placeholder="Ask or instruct, e.g. sell half my TSLA" aria-label="Message the assistant"
                className="min-w-0 flex-1 rounded border border-line bg-canvas px-2 py-1.5 placeholder:text-ink-3" />
              <button data-testid="chat-send" type="submit" disabled={loading || !draft.trim()}
                className="rounded bg-purple px-3 py-1.5 font-medium text-white hover:bg-purple-hi disabled:opacity-50">
                Send
              </button>
            </div>
          </form>
        </>
      )}
    </aside>
  );
}
