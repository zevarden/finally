"use client";
import { useEffect, useRef, useState } from "react";
import type { Position } from "@/lib/types";
import { squarify } from "@/lib/treemap";
import { pct, signedMoney, tone } from "@/lib/format";

const FLAT = [58, 67, 81];
const UP = [46, 189, 116];
const DOWN = [239, 83, 80];
const FULL_AT = 5; // % move that reaches full colour

export function pnlState(pnl: number): "positive" | "negative" | "flat" {
  const t = tone(Math.round(pnl * 100) / 100);
  return t === "up" ? "positive" : t === "down" ? "negative" : "flat";
}

export function cellColor(pnlPercent: number): string {
  const k = Math.min(1, Math.abs(pnlPercent) / FULL_AT);
  const target = pnlPercent > 0 ? UP : pnlPercent < 0 ? DOWN : FLAT;
  // keep a floor so small moves still read as their sign
  const mix = pnlPercent === 0 ? 0 : 0.35 + 0.55 * k;
  const c = FLAT.map((f, i) => Math.round(f + (target[i] - f) * mix));
  return `rgb(${c[0]} ${c[1]} ${c[2]})`;
}

export default function Heatmap({ positions }: { positions: Position[] }) {
  const host = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState({ w: 400, h: 200 });

  useEffect(() => {
    const el = host.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(([entry]) => {
      const { width, height } = entry.contentRect;
      if (width > 0 && height > 0) setSize({ w: width, h: height });
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const cells = squarify(
    positions.map((p) => ({ value: Math.max(p.market_value, 0), data: p })),
    { x: 0, y: 0, w: size.w, h: size.h },
  );

  return (
    <section data-testid="heatmap" aria-label="Portfolio heatmap" className="flex min-h-0 flex-col bg-panel">
      <header className="flex items-baseline justify-between border-b border-line px-3 py-2">
        <h2 className="text-[13px] font-semibold">Holdings by weight</h2>
        <span className="text-[11px] text-ink-3">Colour shows unrealized P&amp;L</span>
      </header>
      <div ref={host} className="relative min-h-0 flex-1 m-[2px]">
        {cells.map(({ x, y, w, h, data: p }) => {
          const big = w > 70 && h > 40;
          return (
            <div key={p.ticker}
              data-testid={`heatmap-cell-${p.ticker}`}
              data-pnl={pnlState(p.unrealized_pnl)}
              title={`${p.ticker}: ${(p.weight * 100).toFixed(1)}% of portfolio, ${signedMoney(p.unrealized_pnl)} (${pct(p.unrealized_pnl_percent)})`}
              className="absolute overflow-hidden rounded-[3px] p-1.5 text-white"
              style={{
                left: `${(x / size.w) * 100}%`, top: `${(y / size.h) * 100}%`,
                width: `calc(${(w / size.w) * 100}% - 2px)`, height: `calc(${(h / size.h) * 100}% - 2px)`,
                backgroundColor: cellColor(p.unrealized_pnl_percent),
              }}>
              <div className="text-[12px] font-semibold leading-tight">{p.ticker}</div>
              {big && (
                <div className="num text-[11px] leading-tight opacity-90">
                  {pct(p.unrealized_pnl_percent)}<br />{(p.weight * 100).toFixed(1)}%
                </div>
              )}
            </div>
          );
        })}
        {positions.length === 0 && (
          <p className="absolute inset-0 flex items-center justify-center px-4 text-center text-ink-3">
            No holdings yet. Buy shares in the trade bar or ask the assistant.
          </p>
        )}
      </div>
    </section>
  );
}
