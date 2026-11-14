import type { ConnectionStatus } from "@/lib/types";
import { money, pct, signedMoney, tone } from "@/lib/format";

const dot: Record<ConnectionStatus, string> = {
  connected: "bg-up",
  reconnecting: "bg-accent",
  disconnected: "bg-down",
};
const label: Record<ConnectionStatus, string> = {
  connected: "Live",
  reconnecting: "Reconnecting",
  disconnected: "Offline",
};
const toneClass = { up: "text-up", down: "text-down", flat: "text-ink-2" } as const;

interface Props {
  total: number | null;
  cash: number | null;
  pnl: number | null;
  pnlPercent: number | null;
  status: ConnectionStatus;
}

export default function Header({ total, cash, pnl, pnlPercent, status }: Props) {
  return (
    <header className="flex items-center gap-6 border-b border-line bg-panel px-4 py-2">
      <div className="flex items-baseline gap-2">
        <span className="text-[20px] font-semibold tracking-tight text-ink">Fin<span className="text-accent">Ally</span></span>
        <span className="hidden text-[11px] text-ink-3 md:inline">AI trading workstation</span>
      </div>

      <div className="ml-auto flex items-center gap-6">
        <div className="flex flex-col items-end">
          <span className="text-[11px] text-ink-3">Portfolio value</span>
          <span data-testid="total-value" className="num text-[22px] font-medium leading-none text-accent">{money(total)}</span>
        </div>
        <div className="flex flex-col items-end">
          <span className="text-[11px] text-ink-3">Unrealized P&amp;L</span>
          <span className={`num text-[14px] leading-tight ${toneClass[tone(pnl)]}`}>
            {signedMoney(pnl)} <span className="text-[12px]">{pct(pnlPercent)}</span>
          </span>
        </div>
        <div className="flex flex-col items-end">
          <span className="text-[11px] text-ink-3">Cash</span>
          <span data-testid="cash-balance" className="num text-[14px] leading-tight text-ink">{money(cash)}</span>
        </div>
        <div className="flex items-center gap-2 rounded border border-line px-2 py-1" title={`Price stream: ${label[status]}`}>
          <span data-testid="connection-status" data-status={status} role="status" aria-label={`Price stream ${label[status]}`}
            className={`h-2 w-2 rounded-full ${dot[status]}`} />
          <span className="text-[11px] text-ink-2">{label[status]}</span>
        </div>
      </div>
    </header>
  );
}
