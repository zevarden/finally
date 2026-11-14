"use client";
import { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "@/lib/api";
import { revalue } from "@/lib/portfolio";
import type { ChatMessage, Portfolio, Snapshot, WatchlistEntry } from "@/lib/types";
import { usePriceStream } from "@/hooks/usePriceStream";
import Header from "./Header";
import Watchlist from "./Watchlist";
import PriceChart from "./PriceChart";
import PnlChart from "./PnlChart";
import Heatmap from "./Heatmap";
import PositionsTable from "./PositionsTable";
import TradeBar, { type TradeResult } from "./TradeBar";
import ChatPanel from "./ChatPanel";

const PORTFOLIO_POLL_MS = 15_000;
const HISTORY_POLL_MS = 30_000;

export default function Terminal() {
  const { prices, status, history } = usePriceStream();
  const [watchlist, setWatchlist] = useState<WatchlistEntry[]>([]);
  const [portfolio, setPortfolio] = useState<Portfolio | null>(null);
  const [snapshots, setSnapshots] = useState<Snapshot[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [chatOpen, setChatOpen] = useState(true);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [chatLoading, setChatLoading] = useState(false);
  const [chatError, setChatError] = useState<string | null>(null);

  const loadWatchlist = useCallback(async () => {
    try {
      const list = await api.watchlist();
      setWatchlist(list);
      setSelected((s) => (s && list.some((e) => e.ticker === s) ? s : list[0]?.ticker ?? s));
    } catch { /* keep last known */ }
  }, []);
  const loadPortfolio = useCallback(async () => {
    try { setPortfolio(await api.portfolio()); } catch { /* keep last known */ }
  }, []);
  const loadHistory = useCallback(async () => {
    try { setSnapshots(await api.history()); } catch { /* keep last known */ }
  }, []);

  useEffect(() => {
    /* eslint-disable react-hooks/set-state-in-effect -- initial data load */
    void loadWatchlist();
    void loadPortfolio();
    void loadHistory();
    /* eslint-enable react-hooks/set-state-in-effect */
    api.chatHistory().then(setMessages).catch(() => {});
    const p = setInterval(loadPortfolio, PORTFOLIO_POLL_MS);
    const h = setInterval(loadHistory, HISTORY_POLL_MS);
    return () => { clearInterval(p); clearInterval(h); };
  }, [loadWatchlist, loadPortfolio, loadHistory]);

  const live = useMemo(() => (portfolio ? revalue(portfolio, prices) : null), [portfolio, prices]);

  const onTrade = useCallback(async (ticker: string, quantity: number, side: "buy" | "sell"): Promise<TradeResult> => {
    const res = await api.trade(ticker, quantity, side);
    setPortfolio(res.portfolio);
    void loadHistory();
    const t = res.trade as { ticker?: string; quantity?: number; price?: number };
    return { ticker: t.ticker ?? ticker, side, quantity: t.quantity ?? quantity, price: t.price ?? 0 };
  }, [loadHistory]);

  const onAdd = useCallback(async (ticker: string) => {
    const entry = await api.addTicker(ticker);
    setWatchlist((w) => (w.some((e) => e.ticker === entry.ticker) ? w : [...w, entry]));
    setSelected((s) => s ?? entry.ticker);
  }, []);

  const onRemove = useCallback(async (ticker: string) => {
    await api.removeTicker(ticker);
    setWatchlist((w) => {
      const next = w.filter((e) => e.ticker !== ticker);
      setSelected((s) => (s === ticker ? next[0]?.ticker ?? null : s));
      return next;
    });
  }, []);

  const onSend = useCallback(async (text: string) => {
    const temp: ChatMessage = {
      id: `local-${Date.now()}`, role: "user", content: text, actions: null, created_at: new Date().toISOString(),
    };
    setMessages((m) => [...m, temp]);
    setChatLoading(true);
    setChatError(null);
    try {
      const reply = await api.chat(text);
      setMessages((m) => [...m, reply]);
      if (reply.actions) {
        void loadPortfolio();
        void loadWatchlist();
        void loadHistory();
      }
    } catch (e) {
      setChatError(e instanceof Error ? `The assistant could not respond: ${e.message}` : "The assistant could not respond.");
    } finally {
      setChatLoading(false);
    }
  }, [loadPortfolio, loadWatchlist, loadHistory]);

  return (
    <div className="flex h-screen min-h-[600px] flex-col">
      <Header
        total={live?.total_value ?? null}
        cash={live?.cash_balance ?? null}
        pnl={live?.unrealized_pnl ?? null}
        pnlPercent={live?.unrealized_pnl_percent ?? null}
        status={status}
      />
      <div className="flex min-h-0 flex-1">
        <main className="grid min-h-0 min-w-0 flex-1 grid-cols-1 gap-px overflow-y-auto bg-line lg:grid-cols-[minmax(300px,340px)_1fr] lg:overflow-hidden">
          <div className="flex min-h-[360px] flex-col lg:min-h-0">
            <Watchlist entries={watchlist} prices={prices} history={history} selected={selected}
              onSelect={setSelected} onAdd={onAdd} onRemove={onRemove} />
          </div>
          <div className="grid min-h-0 min-w-0 grid-rows-[minmax(260px,1fr)_auto_minmax(200px,0.8fr)_minmax(160px,0.65fr)] gap-px">
            <PriceChart ticker={selected} points={selected ? history[selected] : undefined}
              latest={selected ? prices[selected] : undefined} />
            <TradeBar defaultTicker={selected} onTrade={onTrade} />
            <div className="grid min-h-0 grid-cols-1 gap-px md:grid-cols-2">
              <Heatmap positions={live?.positions ?? []} />
              <PnlChart snapshots={snapshots} liveTotal={live?.total_value ?? null} />
            </div>
            <PositionsTable positions={live?.positions ?? []} onSelect={setSelected} />
          </div>
        </main>
        <ChatPanel open={chatOpen} onToggle={() => setChatOpen((o) => !o)} messages={messages}
          loading={chatLoading} error={chatError} onSend={onSend} />
      </div>
    </div>
  );
}
