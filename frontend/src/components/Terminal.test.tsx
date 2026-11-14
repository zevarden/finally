import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import Terminal from "./Terminal";

class FakeEventSource {
  static CONNECTING = 0;
  static OPEN = 1;
  static CLOSED = 2;
  static instances: FakeEventSource[] = [];
  readyState = 0;
  onopen: (() => void) | null = null;
  onmessage: ((e: { data: string }) => void) | null = null;
  onerror: (() => void) | null = null;
  constructor(public url: string) { FakeEventSource.instances.push(this); }
  close() { this.readyState = 2; }
  open() { this.readyState = 1; this.onopen?.(); }
  emit(frame: unknown) { this.onmessage?.({ data: JSON.stringify(frame) }); }
}

const frame = (aapl: number, ts: number) => ({
  AAPL: { ticker: "AAPL", price: aapl, previous_price: 190, timestamp: ts, change: aapl - 190,
    change_percent: 0, direction: "up", session_open: 190, day_change: aapl - 190, day_change_percent: 1 },
});

const portfolio = {
  cash_balance: 8100, positions_value: 1900, total_value: 10000, unrealized_pnl: 0, unrealized_pnl_percent: 0,
  positions: [{ ticker: "AAPL", quantity: 10, avg_cost: 190, current_price: 190, market_value: 1900,
    unrealized_pnl: 0, unrealized_pnl_percent: 0, weight: 0.19 }],
};

function json(body: unknown, status = 200) {
  return Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }));
}

let fetchMock: ReturnType<typeof vi.fn>;

beforeEach(() => {
  FakeEventSource.instances = [];
  vi.stubGlobal("EventSource", FakeEventSource);
  fetchMock = vi.fn((url: string, init?: RequestInit) => {
    const method = init?.method ?? "GET";
    if (url === "/api/watchlist" && method === "GET") {
      return json({ tickers: [{ ticker: "AAPL", added_at: "", price: 190, previous_price: 190, change_percent: 0, day_change_percent: 0, direction: "flat" }] });
    }
    if (url === "/api/portfolio") return json(portfolio);
    if (url === "/api/portfolio/history") return json({ snapshots: [{ total_value: 10000, recorded_at: "2026-11-13T12:00:00Z" }] });
    if (url === "/api/chat/history") return json({ messages: [] });
    if (url === "/api/chat") {
      return json({ id: "a1", role: "assistant", content: "Mock: buying 1 AAPL.", created_at: "",
        actions: { trades: [{ ticker: "AAPL", side: "buy", quantity: 1, status: "executed", price: 191, error: null }] } });
    }
    if (url === "/api/portfolio/trade") return json({ detail: "Insufficient cash" }, 400);
    return json({ detail: "not found" }, 404);
  });
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => vi.unstubAllGlobals());

describe("Terminal", () => {
  it("loads data, streams prices and revalues the portfolio live", async () => {
    render(<Terminal />);
    await screen.findByTestId("watchlist-row-AAPL");
    await waitFor(() => expect(screen.getByTestId("total-value")).toHaveTextContent("$10,000.00"));
    expect(screen.getByTestId("cash-balance")).toHaveTextContent("$8,100.00");
    expect(screen.getByTestId("main-chart")).toHaveAttribute("data-ticker", "AAPL");
    expect(screen.getByTestId("pnl-chart")).toHaveAttribute("data-points", "1");

    const es = FakeEventSource.instances[0];
    expect(es.url).toBe("/api/stream/prices");
    expect(screen.getByTestId("connection-status")).toHaveAttribute("data-status", "reconnecting");
    act(() => es.open());
    expect(screen.getByTestId("connection-status")).toHaveAttribute("data-status", "connected");

    act(() => es.emit(frame(200, 1)));
    expect(screen.getByTestId("price-AAPL")).toHaveTextContent("200.00");
    expect(screen.getByTestId("total-value")).toHaveTextContent("$10,100.00");
    expect(screen.getByTestId("heatmap-cell-AAPL")).toHaveAttribute("data-pnl", "positive");

    act(() => { es.readyState = 0; es.onerror?.(); });
    expect(screen.getByTestId("connection-status")).toHaveAttribute("data-status", "reconnecting");
    act(() => { es.readyState = 2; es.onerror?.(); });
    expect(screen.getByTestId("connection-status")).toHaveAttribute("data-status", "disconnected");
  });

  it("shows trade errors from the API", async () => {
    render(<Terminal />);
    await screen.findByTestId("watchlist-row-AAPL");
    fireEvent.change(screen.getByTestId("trade-quantity"), { target: { value: "1000" } });
    fireEvent.click(screen.getByTestId("trade-buy"));
    expect(await screen.findByTestId("trade-error")).toHaveTextContent("Insufficient cash");
  });

  it("sends chat messages and renders the reply with actions", async () => {
    render(<Terminal />);
    await screen.findByTestId("watchlist-row-AAPL");
    fireEvent.change(screen.getByTestId("chat-input"), { target: { value: "buy 1 AAPL" } });
    fireEvent.click(screen.getByTestId("chat-send"));
    expect(screen.getByTestId("chat-loading")).toBeInTheDocument();
    expect(await screen.findByTestId("chat-action")).toHaveAttribute("data-status", "executed");
    expect(screen.queryByTestId("chat-loading")).toBeNull();
    expect(screen.getAllByTestId("chat-message").map((m) => m.dataset.role)).toEqual(["user", "assistant"]);
    const body = JSON.parse(fetchMock.mock.calls.find((c) => c[0] === "/api/chat")![1].body);
    expect(body).toEqual({ message: "buy 1 AAPL" });
  });
});
