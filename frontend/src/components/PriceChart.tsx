"use client";
import { useEffect, useRef } from "react";
import { AreaSeries, createChart, type IChartApi, type ISeriesApi, type Time } from "lightweight-charts";
import type { PricePoint } from "@/hooks/usePriceStream";
import type { PriceUpdate } from "@/lib/types";
import { baseChartOptions, toSeriesData } from "@/lib/chartTheme";
import { pct, price as fmtPrice, tone } from "@/lib/format";

interface Props {
  ticker: string | null;
  points: PricePoint[] | undefined;
  latest: PriceUpdate | undefined;
}

const toneClass = { up: "text-up", down: "text-down", flat: "text-ink-2" } as const;

export default function PriceChart({ ticker, points, latest }: Props) {
  const host = useRef<HTMLDivElement>(null);
  const chart = useRef<IChartApi | null>(null);
  const series = useRef<ISeriesApi<"Area"> | null>(null);
  const lastTime = useRef<number>(0);
  const count = points?.length ?? 0;

  useEffect(() => {
    if (!host.current) return;
    const c = createChart(host.current, baseChartOptions);
    series.current = c.addSeries(AreaSeries, {
      lineColor: "#209dd7",
      lineWidth: 2,
      topColor: "rgba(32, 157, 215, 0.28)",
      bottomColor: "rgba(32, 157, 215, 0.02)",
      priceLineColor: "#ecad0a",
      priceLineStyle: 2,
      crosshairMarkerRadius: 4,
      crosshairMarkerBorderColor: "#0d1117",
      crosshairMarkerBorderWidth: 2,
    });
    chart.current = c;
    return () => {
      c.remove();
      chart.current = null;
      series.current = null;
    };
  }, []);

  // Reset when the selected ticker changes.
  useEffect(() => {
    const data = toSeriesData(points ?? []);
    series.current?.setData(data);
    lastTime.current = data.length ? (data[data.length - 1].time as number) : 0;
    chart.current?.timeScale().fitContent();
    // eslint-disable-next-line react-hooks/exhaustive-deps -- only on ticker switch; streaming handled below
  }, [ticker]);

  // Stream new points.
  useEffect(() => {
    const s = series.current;
    const last = points?.[points.length - 1];
    if (!s || !last) return;
    const t = Math.floor(last.time);
    if (t < lastTime.current) return;
    if (lastTime.current === 0) {
      s.setData(toSeriesData(points!));
    } else {
      s.update({ time: t as Time, value: last.value });
    }
    // Keep the whole session since page load in view as it accumulates.
    if (t !== lastTime.current) chart.current?.timeScale().fitContent();
    lastTime.current = t;
  }, [count, points]);

  const day = latest?.day_change_percent ?? null;

  return (
    <section data-testid="main-chart" data-ticker={ticker ?? ""} aria-label={`${ticker ?? "No symbol"} price chart`}
      className="flex min-h-0 flex-col bg-panel">
      <header className="flex items-baseline gap-4 border-b border-line px-3 py-2">
        <h2 className="text-[18px] font-semibold tracking-wide text-accent">{ticker ?? "—"}</h2>
        <span className="num text-[18px] text-ink">{fmtPrice(latest?.price)}</span>
        <span className={`num text-[12px] ${toneClass[tone(latest?.day_change)]}`}>
          {latest?.day_change != null ? `${latest.day_change >= 0 ? "+" : ""}${latest.day_change.toFixed(2)}` : "—"} ({pct(day)}) today
        </span>
        <span className="ml-auto text-[11px] text-ink-3">{count} ticks since page load</span>
      </header>
      <div className="relative min-h-0 flex-1">
        <div ref={host} className="absolute inset-0" />
        {count < 2 && (
          <p className="pointer-events-none absolute inset-0 flex items-center justify-center text-ink-3">
            {ticker ? `Collecting live prices for ${ticker}…` : "Select a symbol from the watchlist."}
          </p>
        )}
      </div>
    </section>
  );
}
