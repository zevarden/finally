"use client";
import { useEffect, useState } from "react";
import type { ConnectionStatus, PriceMap } from "@/lib/types";

export interface PricePoint { time: number; value: number }
export type PriceHistory = Record<string, PricePoint[]>;

const MAX_POINTS = 3000;
const RECONNECT_MS = 3000;

/** Append each ticker's latest price if its timestamp advanced. Mutates `history`. */
export function appendHistory(history: PriceHistory, frame: PriceMap, max = MAX_POINTS): void {
  for (const [ticker, u] of Object.entries(frame)) {
    if (!u || !Number.isFinite(u.price)) continue;
    const arr = (history[ticker] ??= []);
    const last = arr[arr.length - 1];
    if (last && u.timestamp <= last.time) continue;
    arr.push({ time: u.timestamp, value: u.price });
    if (arr.length > max) arr.splice(0, arr.length - max);
  }
}

/**
 * Native EventSource on /api/stream/prices. Each frame is the full ticker map,
 * so state is replaced wholesale. History accumulates since page load.
 */
export function usePriceStream(url = "/api/stream/prices") {
  const [prices, setPrices] = useState<PriceMap>({});
  const [status, setStatus] = useState<ConnectionStatus>("reconnecting");
  // Stable mutable store (mutated per frame; re-render is driven by `prices`).
  const [history] = useState<PriceHistory>(() => ({}));

  useEffect(() => {
    let es: EventSource | null = null;
    let retry: ReturnType<typeof setTimeout> | null = null;
    let disposed = false;

    const connect = () => {
      if (disposed) return;
      es = new EventSource(url);
      es.onopen = () => setStatus("connected");
      es.onmessage = (ev) => {
        try {
          const frame = JSON.parse(ev.data) as PriceMap;
          appendHistory(history, frame);
          setPrices(frame);
          setStatus("connected");
        } catch {
          /* ignore malformed frame */
        }
      };
      es.onerror = () => {
        if (!es) return;
        if (es.readyState === EventSource.CLOSED) {
          // Browser gave up; reconnect manually.
          setStatus("disconnected");
          es.close();
          retry = setTimeout(connect, RECONNECT_MS);
        } else {
          setStatus("reconnecting");
        }
      };
    };

    connect();
    return () => {
      disposed = true;
      if (retry) clearTimeout(retry);
      es?.close();
    };
  }, [url, history]);

  return { prices, status, history };
}
