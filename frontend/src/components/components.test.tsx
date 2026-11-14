import { describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import PriceCell from "./PriceCell";
import Watchlist from "./Watchlist";
import Heatmap from "./Heatmap";
import PositionsTable from "./PositionsTable";
import ChatPanel from "./ChatPanel";
import TradeBar from "./TradeBar";
import Header from "./Header";
import PnlChart from "./PnlChart";
import Sparkline from "./Sparkline";
import type { ChatMessage, Position, WatchlistEntry } from "@/lib/types";

const entry = (ticker: string, price: number | null = 100): WatchlistEntry => ({
  ticker, added_at: "2026-11-13T00:00:00Z", price, previous_price: price, change_percent: 0,
  day_change_percent: 1.5, direction: "flat",
});

const pos = (ticker: string, pnl: number, mv = 1000): Position => ({
  ticker, quantity: 10, avg_cost: (mv - pnl) / 10, current_price: mv / 10, market_value: mv,
  unrealized_pnl: pnl, unrealized_pnl_percent: (pnl / (mv - pnl)) * 100, weight: 0.1,
});

describe("PriceCell", () => {
  it("flashes up on a price increase and down on a decrease, then clears", () => {
    vi.useFakeTimers();
    const { rerender } = render(<PriceCell ticker="AAPL" value={100} />);
    const cell = screen.getByTestId("price-AAPL");
    expect(cell).toHaveTextContent("100.00");
    expect(cell.className).not.toMatch(/flash-/);

    rerender(<PriceCell ticker="AAPL" value={101} />);
    expect(cell).toHaveClass("flash-up");
    act(() => { vi.advanceTimersByTime(600); });
    expect(cell).not.toHaveClass("flash-up");

    rerender(<PriceCell ticker="AAPL" value={99} />);
    expect(cell).toHaveClass("flash-down");
    vi.useRealTimers();
  });

  it("does not flash when the price is unchanged", () => {
    const { rerender } = render(<PriceCell ticker="MSFT" value={50} />);
    rerender(<PriceCell ticker="MSFT" value={50} />);
    expect(screen.getByTestId("price-MSFT").className).not.toMatch(/flash-/);
  });
});

describe("Watchlist", () => {
  const setup = (over: Partial<React.ComponentProps<typeof Watchlist>> = {}) => {
    const props = {
      entries: [entry("AAPL"), entry("MSFT", 300)],
      prices: {},
      history: { AAPL: [{ time: 1, value: 1 }, { time: 2, value: 2 }] },
      selected: "AAPL",
      onSelect: vi.fn(),
      onAdd: vi.fn().mockResolvedValue(undefined),
      onRemove: vi.fn().mockResolvedValue(undefined),
      ...over,
    };
    render(<Watchlist {...props} />);
    return props;
  };

  it("renders rows with price, day change and sparkline", () => {
    setup();
    expect(screen.getByTestId("watchlist")).toBeInTheDocument();
    expect(screen.getByTestId("watchlist-row-AAPL")).toBeInTheDocument();
    expect(screen.getByTestId("price-MSFT")).toHaveTextContent("300.00");
    expect(screen.getByTestId("watchlist-row-AAPL")).toHaveTextContent("+1.50%");
    expect(screen.getByTestId("sparkline-AAPL")).toHaveAttribute("data-points", "2");
  });

  it("prefers live SSE prices", () => {
    setup({ prices: { AAPL: { ticker: "AAPL", price: 123.45, previous_price: 120, timestamp: 1, change: 3.45,
      change_percent: 2.9, direction: "up", session_open: 100, day_change: 23.45, day_change_percent: 23.45 } } });
    expect(screen.getByTestId("price-AAPL")).toHaveTextContent("123.45");
    expect(screen.getByTestId("watchlist-row-AAPL")).toHaveTextContent("+23.45%");
  });

  it("adds a ticker (uppercased) and removes one", async () => {
    const p = setup();
    fireEvent.change(screen.getByTestId("watchlist-add-input"), { target: { value: "pypl" } });
    fireEvent.click(screen.getByTestId("watchlist-add-button"));
    await waitFor(() => expect(p.onAdd).toHaveBeenCalledWith("PYPL"));
    await waitFor(() => expect(screen.getByTestId("watchlist-add-input")).toHaveValue(""));

    fireEvent.click(screen.getByTestId("watchlist-remove-MSFT"));
    expect(p.onRemove).toHaveBeenCalledWith("MSFT");
    expect(p.onSelect).not.toHaveBeenCalled();
  });

  it("shows the API error when adding fails", async () => {
    setup({ onAdd: vi.fn().mockRejectedValue(new Error("PYPL is already on the watchlist")) });
    fireEvent.change(screen.getByTestId("watchlist-add-input"), { target: { value: "PYPL" } });
    fireEvent.click(screen.getByTestId("watchlist-add-button"));
    expect(await screen.findByRole("alert")).toHaveTextContent("already on the watchlist");
  });

  it("selects a ticker on click", () => {
    const p = setup();
    fireEvent.click(screen.getByTestId("watchlist-row-MSFT"));
    expect(p.onSelect).toHaveBeenCalledWith("MSFT");
  });
});

describe("Sparkline", () => {
  it("draws a path once two points exist", () => {
    const { container, rerender } = render(<Sparkline ticker="X" points={[{ time: 1, value: 1 }]} />);
    expect(container.querySelector("path")).toBeNull();
    rerender(<Sparkline ticker="X" points={[{ time: 1, value: 1 }, { time: 2, value: 3 }]} />);
    expect(container.querySelector("path")).not.toBeNull();
  });
});

describe("Heatmap and positions", () => {
  it("renders a cell per position with P&L state", () => {
    render(<Heatmap positions={[pos("AAPL", 50), pos("TSLA", -20, 500), pos("V", 0, 200)]} />);
    expect(screen.getByTestId("heatmap-cell-AAPL")).toHaveAttribute("data-pnl", "positive");
    expect(screen.getByTestId("heatmap-cell-TSLA")).toHaveAttribute("data-pnl", "negative");
    expect(screen.getByTestId("heatmap-cell-V")).toHaveAttribute("data-pnl", "flat");
  });

  it("shows an empty state with no positions", () => {
    render(<Heatmap positions={[]} />);
    expect(screen.getByTestId("heatmap")).toHaveTextContent("No holdings yet");
  });

  it("renders the positions table", () => {
    render(<PositionsTable positions={[pos("AAPL", 50, 1950)]} />);
    const row = screen.getByTestId("position-row-AAPL");
    expect(row).toHaveTextContent("10");
    expect(row).toHaveTextContent("$1,950.00");
    expect(row).toHaveTextContent("+$50.00");
  });
});

describe("Header", () => {
  it("shows totals and connection status", () => {
    render(<Header total={10050} cash={8100} pnl={50} pnlPercent={2.63} status="connected" />);
    expect(screen.getByTestId("total-value")).toHaveTextContent("$10,050.00");
    expect(screen.getByTestId("cash-balance")).toHaveTextContent("$8,100.00");
    expect(screen.getByTestId("connection-status")).toHaveAttribute("data-status", "connected");
  });
});

describe("PnlChart", () => {
  it("exposes the snapshot count", () => {
    render(<PnlChart liveTotal={null} snapshots={[
      { total_value: 10000, recorded_at: "2026-11-13T12:00:00+00:00" },
      { total_value: 10020, recorded_at: "2026-11-13T12:00:30+00:00" },
    ]} />);
    expect(screen.getByTestId("pnl-chart")).toHaveAttribute("data-points", "2");
    expect(screen.getByTestId("pnl-chart")).toHaveTextContent("+$20.00");
  });
});

describe("TradeBar", () => {
  it("submits buys and sells and shows API errors", async () => {
    const onTrade = vi.fn()
      .mockResolvedValueOnce({ ticker: "AAPL", side: "buy", quantity: 5, price: 190 })
      .mockRejectedValueOnce(new Error("Insufficient shares"));
    render(<TradeBar defaultTicker="AAPL" onTrade={onTrade} />);
    expect(screen.getByTestId("trade-ticker")).toHaveValue("AAPL");
    fireEvent.change(screen.getByTestId("trade-quantity"), { target: { value: "5" } });
    fireEvent.click(screen.getByTestId("trade-buy"));
    await waitFor(() => expect(onTrade).toHaveBeenCalledWith("AAPL", 5, "buy"));
    expect(await screen.findByTestId("trade-notice")).toHaveTextContent("Bought 5 AAPL at $190.00");

    fireEvent.change(screen.getByTestId("trade-quantity"), { target: { value: "500" } });
    fireEvent.click(screen.getByTestId("trade-sell"));
    expect(await screen.findByTestId("trade-error")).toHaveTextContent("Insufficient shares");
    expect(onTrade).toHaveBeenLastCalledWith("AAPL", 500, "sell");
  });

  it("validates quantity before calling the API", () => {
    const onTrade = vi.fn();
    render(<TradeBar defaultTicker="AAPL" onTrade={onTrade} />);
    fireEvent.click(screen.getByTestId("trade-buy"));
    expect(screen.getByTestId("trade-error")).toHaveTextContent("quantity");
    expect(onTrade).not.toHaveBeenCalled();
  });
});

describe("ChatPanel", () => {
  const messages: ChatMessage[] = [
    { id: "1", role: "user", content: "buy 5 AAPL", actions: null, created_at: "" },
    { id: "2", role: "assistant", content: "Mock: buying 5 AAPL.", created_at: "", actions: {
      trades: [{ ticker: "AAPL", side: "buy", quantity: 5, status: "executed", price: 190.12, error: null }],
      watchlist_changes: [{ ticker: "PYPL", action: "add", status: "failed", error: "PYPL is already on the watchlist" }],
    } },
  ];

  it("renders messages and inline actions", () => {
    render(<ChatPanel open onToggle={() => {}} messages={messages} loading={false} error={null} onSend={() => {}} />);
    const bubbles = screen.getAllByTestId("chat-message");
    expect(bubbles.map((b) => b.getAttribute("data-role"))).toEqual(["user", "assistant"]);
    const actions = screen.getAllByTestId("chat-action");
    expect(actions[0]).toHaveAttribute("data-status", "executed");
    expect(actions[0]).toHaveTextContent("Bought 5 AAPL at $190.12");
    expect(actions[1]).toHaveAttribute("data-status", "failed");
    expect(actions[1]).toHaveTextContent("already on the watchlist");
    expect(screen.queryByTestId("chat-loading")).toBeNull();
  });

  it("shows the loading indicator and sends input", () => {
    const onSend = vi.fn();
    const { rerender } = render(<ChatPanel open onToggle={() => {}} messages={[]} loading={false} error={null} onSend={onSend} />);
    fireEvent.change(screen.getByTestId("chat-input"), { target: { value: "hello" } });
    fireEvent.click(screen.getByTestId("chat-send"));
    expect(onSend).toHaveBeenCalledWith("hello");
    expect(screen.getByTestId("chat-input")).toHaveValue("");
    rerender(<ChatPanel open onToggle={() => {}} messages={[]} loading error={null} onSend={onSend} />);
    expect(screen.getByTestId("chat-loading")).toBeVisible();
  });

  it("collapses via the toggle", () => {
    const onToggle = vi.fn();
    const { rerender } = render(<ChatPanel open onToggle={onToggle} messages={[]} loading={false} error={null} onSend={() => {}} />);
    fireEvent.click(screen.getByTestId("chat-toggle"));
    expect(onToggle).toHaveBeenCalled();
    rerender(<ChatPanel open={false} onToggle={onToggle} messages={[]} loading={false} error={null} onSend={() => {}} />);
    expect(screen.getByTestId("chat-panel")).toHaveAttribute("data-open", "false");
    expect(screen.queryByTestId("chat-input")).toBeNull();
  });
});
