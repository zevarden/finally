"use client";
import { useState } from "react";
import type { PriceHistory } from "@/hooks/usePriceStream";
import type { PriceMap, WatchlistEntry } from "@/lib/types";
import { pct, tone } from "@/lib/format";
import PriceCell from "./PriceCell";
import Sparkline from "./Sparkline";

interface Props {
  entries: WatchlistEntry[];
  prices: PriceMap;
  history: PriceHistory;
  selected: string | null;
  onSelect: (ticker: string) => void;
  onAdd: (ticker: string) => Promise<void>;
  onRemove: (ticker: string) => Promise<void>;
}

const toneClass = { up: "text-up", down: "text-down", flat: "text-ink-2" } as const;

export default function Watchlist({ entries, prices, history, selected, onSelect, onAdd, onRemove }: Props) {
  const [draft, setDraft] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    const t = draft.trim().toUpperCase();
    if (!t) return;
    setBusy(true);
    setError(null);
    try {
      await onAdd(t);
      setDraft("");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  const remove = async (t: string) => {
    setError(null);
    try {
      await onRemove(t);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  return (
    <section data-testid="watchlist" aria-label="Watchlist" className="flex min-h-0 flex-1 flex-col bg-panel">
      <header className="flex items-baseline justify-between border-b border-line px-3 py-2">
        <h2 className="text-[13px] font-semibold text-ink">Watchlist</h2>
        <span className="text-[11px] text-ink-3">{entries.length} symbols</span>
      </header>

      <div className="grid grid-cols-[1fr_auto_auto_auto_20px] gap-x-2 border-b border-line-soft px-3 py-1 text-[11px] text-ink-3">
        <span>Symbol</span>
        <span className="w-[84px]">Since open</span>
        <span className="w-[72px] text-right">Last</span>
        <span className="w-[56px] text-right">Day</span>
        <span />
      </div>

      <ul className="min-h-0 flex-1 overflow-y-auto">
        {entries.map((e) => {
          const live = prices[e.ticker];
          const p = live?.price ?? e.price;
          const day = live ? live.day_change_percent : e.day_change_percent;
          const isSel = selected === e.ticker;
          return (
            <li key={e.ticker}
              data-testid={`watchlist-row-${e.ticker}`}
              data-selected={isSel ? "true" : "false"}
              onClick={() => onSelect(e.ticker)}
              onKeyDown={(k) => { if (k.key === "Enter" || k.key === " ") { k.preventDefault(); onSelect(e.ticker); } }}
              tabIndex={0}
              role="button"
              aria-pressed={isSel}
              className={`group grid cursor-pointer grid-cols-[1fr_auto_auto_auto_20px] items-center gap-x-2 border-b border-line-soft py-[5px] pr-3 pl-[10px] border-l-2 hover:bg-raised ${isSel ? "border-l-accent bg-raised" : "border-l-transparent"}`}>
              <span className={`font-semibold tracking-wide ${isSel ? "text-accent" : "text-ink"}`}>{e.ticker}</span>
              <span className="w-[84px]"><Sparkline ticker={e.ticker} points={history[e.ticker]} /></span>
              <span className="w-[72px] text-right"><PriceCell ticker={e.ticker} value={p} /></span>
              <span className={`num w-[56px] text-right text-[12px] ${toneClass[tone(day)]}`}>{pct(day)}</span>
              <button
                type="button"
                data-testid={`watchlist-remove-${e.ticker}`}
                aria-label={`Remove ${e.ticker} from watchlist`}
                onClick={(ev) => { ev.stopPropagation(); void remove(e.ticker); }}
                className="h-5 w-5 rounded text-ink-3 opacity-40 hover:bg-line hover:text-down group-hover:opacity-100 focus-visible:opacity-100">
                ×
              </button>
            </li>
          );
        })}
        {entries.length === 0 && (
          <li className="px-3 py-6 text-ink-3">Your watchlist is empty. Add a symbol below.</li>
        )}
      </ul>

      <form onSubmit={submit} className="border-t border-line p-2">
        <div className="flex gap-2">
          <input
            data-testid="watchlist-add-input"
            value={draft}
            onChange={(e) => setDraft(e.target.value.toUpperCase())}
            placeholder="Add symbol, e.g. PYPL"
            aria-label="Symbol to add"
            maxLength={10}
            className="num min-w-0 flex-1 rounded border border-line bg-canvas px-2 py-1 text-[12px] uppercase placeholder:normal-case placeholder:text-ink-3"
          />
          <button data-testid="watchlist-add-button" type="submit" disabled={busy || !draft.trim()}
            className="rounded bg-purple px-3 py-1 text-[12px] font-medium text-white hover:bg-purple-hi disabled:opacity-50">
            Add
          </button>
        </div>
        {error && <p role="alert" data-testid="watchlist-error" className="mt-1 text-[11px] text-down">{error}</p>}
      </form>
    </section>
  );
}
