import type { PricePoint } from "@/hooks/usePriceStream";

const MAX = 80;

export default function Sparkline({ ticker, points, width = 84, height = 24 }: {
  ticker: string; points: PricePoint[] | undefined; width?: number; height?: number;
}) {
  const pts = (points ?? []).slice(-MAX);
  let path = "";
  let stroke = "var(--color-ink-3)";
  if (pts.length >= 2) {
    const vals = pts.map((p) => p.value);
    const min = Math.min(...vals);
    const max = Math.max(...vals);
    const span = max - min || 1;
    const step = width / (MAX - 1);
    const x0 = width - (pts.length - 1) * step; // fill in from the right as data accumulates
    path = pts
      .map((p, i) => `${i ? "L" : "M"}${(x0 + i * step).toFixed(1)},${(2 + (1 - (p.value - min) / span) * (height - 4)).toFixed(1)}`)
      .join("");
    const delta = vals[vals.length - 1] - vals[0];
    stroke = delta > 0 ? "var(--color-up)" : delta < 0 ? "var(--color-down)" : "var(--color-ink-3)";
  }
  return (
    <svg data-testid={`sparkline-${ticker}`} data-points={pts.length} width={width} height={height}
      viewBox={`0 0 ${width} ${height}`} aria-hidden="true" className="block">
      {path && <path d={path} fill="none" stroke={stroke} strokeWidth={1.5} strokeLinejoin="round" strokeLinecap="round" />}
    </svg>
  );
}
