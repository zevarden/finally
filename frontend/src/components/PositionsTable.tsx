import type { Position } from "@/lib/types";
import { money, pct, price, qty, signedMoney, tone } from "@/lib/format";

const toneClass = { up: "text-up", down: "text-down", flat: "text-ink-2" } as const;

export default function PositionsTable({ positions, onSelect }: { positions: Position[]; onSelect?: (t: string) => void }) {
  return (
    <section aria-label="Positions" className="flex min-h-0 flex-col bg-panel">
      <header className="flex items-baseline justify-between border-b border-line px-3 py-2">
        <h2 className="text-[13px] font-semibold">Positions</h2>
        <span className="text-[11px] text-ink-3">{positions.length} open</span>
      </header>
      <div className="min-h-0 flex-1 overflow-auto">
        <table data-testid="positions-table" className="w-full border-collapse text-[12px]">
          <thead className="sticky top-0 bg-panel text-[11px] text-ink-3">
            <tr className="border-b border-line-soft">
              <th className="px-3 py-1 text-left font-normal">Symbol</th>
              <th className="px-2 py-1 text-right font-normal">Qty</th>
              <th className="px-2 py-1 text-right font-normal">Avg cost</th>
              <th className="px-2 py-1 text-right font-normal">Last</th>
              <th className="px-2 py-1 text-right font-normal">Value</th>
              <th className="px-2 py-1 text-right font-normal">Unrealized P&amp;L</th>
              <th className="px-3 py-1 text-right font-normal">Return</th>
            </tr>
          </thead>
          <tbody>
            {positions.map((p) => {
              const t = toneClass[tone(Math.round(p.unrealized_pnl * 100) / 100)];
              return (
                <tr key={p.ticker} data-testid={`position-row-${p.ticker}`}
                  onClick={() => onSelect?.(p.ticker)}
                  className="cursor-pointer border-b border-line-soft hover:bg-raised">
                  <td className="px-3 py-[5px] font-semibold">{p.ticker}</td>
                  <td className="num px-2 text-right" data-testid={`position-qty-${p.ticker}`}>{qty(p.quantity)}</td>
                  <td className="num px-2 text-right text-ink-2">{price(p.avg_cost)}</td>
                  <td className="num px-2 text-right">{price(p.current_price)}</td>
                  <td className="num px-2 text-right">{money(p.market_value)}</td>
                  <td className={`num px-2 text-right ${t}`}>{signedMoney(p.unrealized_pnl)}</td>
                  <td className={`num px-3 text-right ${t}`}>{pct(p.unrealized_pnl_percent)}</td>
                </tr>
              );
            })}
            {positions.length === 0 && (
              <tr><td colSpan={7} className="px-3 py-5 text-ink-3">No open positions.</td></tr>
            )}
          </tbody>
        </table>
      </div>
    </section>
  );
}
