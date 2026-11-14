"use client";
import { useEffect, useMemo, useRef } from "react";
import { BaselineSeries, createChart, type ISeriesApi, type IChartApi } from "lightweight-charts";
import type { Snapshot } from "@/lib/types";
import { baseChartOptions, toSeriesData } from "@/lib/chartTheme";
import { signedMoney, pct, tone } from "@/lib/format";

export const STARTING_VALUE = 10000;
const toneClass = { up: "text-up", down: "text-down", flat: "text-ink-2" } as const;

export function snapshotsToPoints(snaps: Snapshot[]) {
  return snaps
    .map((s) => ({ time: Date.parse(s.recorded_at) / 1000, value: s.total_value }))
    .filter((p) => Number.isFinite(p.time) && Number.isFinite(p.value))
    .sort((a, b) => a.time - b.time);
}

export default function PnlChart({ snapshots, liveTotal }: { snapshots: Snapshot[]; liveTotal: number | null }) {
  const host = useRef<HTMLDivElement>(null);
  const chart = useRef<IChartApi | null>(null);
  const series = useRef<ISeriesApi<"Baseline"> | null>(null);
  const data = useMemo(() => toSeriesData(snapshotsToPoints(snapshots)), [snapshots]);

  useEffect(() => {
    if (!host.current) return;
    const c = createChart(host.current, {
      ...baseChartOptions,
      timeScale: { ...baseChartOptions.timeScale, secondsVisible: false },
    });
    series.current = c.addSeries(BaselineSeries, {
      baseValue: { type: "price", price: STARTING_VALUE },
      lineWidth: 2,
      topLineColor: "#2ebd74",
      topFillColor1: "rgba(46, 189, 116, 0.22)",
      topFillColor2: "rgba(46, 189, 116, 0.02)",
      bottomLineColor: "#ef5350",
      bottomFillColor1: "rgba(239, 83, 80, 0.02)",
      bottomFillColor2: "rgba(239, 83, 80, 0.22)",
      priceLineVisible: false,
    });
    chart.current = c;
    return () => { c.remove(); chart.current = null; series.current = null; };
  }, []);

  useEffect(() => {
    series.current?.setData(data);
    chart.current?.timeScale().fitContent();
  }, [data]);

  const total = liveTotal ?? (data.length ? data[data.length - 1].value : null);
  const delta = total != null ? total - STARTING_VALUE : null;

  return (
    <section data-testid="pnl-chart" data-points={snapshots.length} aria-label="Portfolio value over time"
      className="flex min-h-0 flex-col bg-panel">
      <header className="flex items-baseline justify-between border-b border-line px-3 py-2">
        <h2 className="text-[13px] font-semibold">Portfolio value</h2>
        <span className={`num text-[12px] ${toneClass[tone(delta)]}`}>
          {signedMoney(delta)} ({pct(delta != null ? (delta / STARTING_VALUE) * 100 : null)}) vs $10,000 start
        </span>
      </header>
      <div className="relative min-h-0 flex-1">
        <div ref={host} className="absolute inset-0" />
        {snapshots.length === 0 && (
          <p className="pointer-events-none absolute inset-0 flex items-center justify-center text-ink-3">
            Value is recorded every 30 seconds and after each trade.
          </p>
        )}
      </div>
    </section>
  );
}
