"use client";
import { useEffect, useState } from "react";
import { money, qty as fmtQty } from "@/lib/format";

export interface TradeResult { ticker: string; side: "buy" | "sell"; quantity: number; price: number }

interface Props {
  defaultTicker: string | null;
  onTrade: (ticker: string, quantity: number, side: "buy" | "sell") => Promise<TradeResult>;
}

export default function TradeBar({ defaultTicker, onTrade }: Props) {
  const [ticker, setTicker] = useState(defaultTicker ?? "");
  const [quantity, setQuantity] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // Follow the selected symbol.
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- keep the symbol input in sync with the selection
    if (defaultTicker) setTicker(defaultTicker);
  }, [defaultTicker]);

  const submit = async (side: "buy" | "sell") => {
    setError(null);
    setNotice(null);
    const t = ticker.trim().toUpperCase();
    const q = Number(quantity);
    if (!t) return setError("Enter a symbol.");
    if (!quantity.trim() || !Number.isFinite(q) || q <= 0) return setError("Enter a quantity greater than 0.");
    setBusy(true);
    try {
      const r = await onTrade(t, q, side);
      setNotice(`${side === "buy" ? "Bought" : "Sold"} ${fmtQty(r.quantity)} ${r.ticker} at ${money(r.price)}`);
      setQuantity("");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <form aria-label="Trade" onSubmit={(e) => { e.preventDefault(); void submit("buy"); }}
      className="flex flex-wrap items-center gap-2 bg-panel px-3 py-2">
      <h2 className="mr-1 text-[13px] font-semibold">Market order</h2>
      <input data-testid="trade-ticker" aria-label="Symbol" value={ticker} maxLength={10}
        onChange={(e) => setTicker(e.target.value.toUpperCase())} placeholder="Symbol"
        className="num w-24 rounded border border-line bg-canvas px-2 py-1 uppercase placeholder:normal-case placeholder:text-ink-3" />
      <input data-testid="trade-quantity" aria-label="Quantity" value={quantity} inputMode="decimal"
        type="number" min="0" step="any" onChange={(e) => setQuantity(e.target.value)} placeholder="Shares"
        className="num w-24 rounded border border-line bg-canvas px-2 py-1 placeholder:text-ink-3" />
      <button data-testid="trade-buy" type="submit" disabled={busy}
        className="rounded bg-purple px-4 py-1 font-semibold text-white hover:bg-purple-hi disabled:opacity-50">
        Buy
      </button>
      <button data-testid="trade-sell" type="button" disabled={busy} onClick={() => void submit("sell")}
        className="rounded border border-purple px-4 py-1 font-semibold text-ink hover:bg-purple/30 disabled:opacity-50">
        Sell
      </button>
      <span aria-live="polite" className="min-w-0 flex-1 truncate text-[12px]">
        {error && <span data-testid="trade-error" role="alert" className="text-down">{error}</span>}
        {notice && <span data-testid="trade-notice" className="text-ink-2">{notice}</span>}
      </span>
    </form>
  );
}
