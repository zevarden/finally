"""Test doubles: an in-memory Database (TEAM_CONTRACT §3) and a controllable market source.

`make_db(tmp_path)` returns the real app.db.Database when it is importable, else the fake.
"""

from __future__ import annotations

import threading
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any

from app.market import MarketDataSource, PriceCache, normalize_ticker

DEFAULT_TICKERS = ["AAPL", "GOOGL", "MSFT", "AMZN", "TSLA", "NVDA", "META", "JPM", "V", "NFLX"]


def _now() -> str:
    return datetime.now(UTC).isoformat()


class FakeDatabase:
    """Minimal in-memory implementation of the app.db.Database interface (single user)."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.cash = 10000.0
        self.watchlist: list[dict] = [{"ticker": t, "added_at": _now()} for t in DEFAULT_TICKERS]
        self.positions: dict[str, dict] = {}
        self.trades: list[dict] = []
        self.snapshots: list[dict] = []
        self.chat: list[dict] = []

    @contextmanager
    def transaction(self):
        with self._lock:
            saved = (self.cash, [dict(w) for w in self.watchlist],
                     {k: dict(v) for k, v in self.positions.items()}, list(self.trades))
            try:
                yield
            except BaseException:
                self.cash, self.watchlist, self.positions, self.trades = saved
                raise

    def get_cash(self, *, user_id: str = "default") -> float:
        return self.cash

    def set_cash(self, amount: float, *, user_id: str = "default") -> None:
        self.cash = amount

    def list_watchlist(self, *, user_id: str = "default") -> list[dict]:
        return [dict(w) for w in self.watchlist]

    def add_watchlist(self, ticker: str, *, user_id: str = "default") -> bool:
        if any(w["ticker"] == ticker for w in self.watchlist):
            return False
        self.watchlist.append({"ticker": ticker, "added_at": _now()})
        return True

    def remove_watchlist(self, ticker: str, *, user_id: str = "default") -> bool:
        before = len(self.watchlist)
        self.watchlist = [w for w in self.watchlist if w["ticker"] != ticker]
        return len(self.watchlist) < before

    def list_positions(self, *, user_id: str = "default") -> list[dict]:
        return [dict(p) for p in self.positions.values()]

    def get_position(self, ticker: str, *, user_id: str = "default") -> dict | None:
        p = self.positions.get(ticker)
        return dict(p) if p else None

    def upsert_position(self, ticker, quantity, avg_cost, *, user_id: str = "default") -> None:
        self.positions[ticker] = {"ticker": ticker, "quantity": quantity,
                                  "avg_cost": avg_cost, "updated_at": _now()}

    def delete_position(self, ticker: str, *, user_id: str = "default") -> None:
        self.positions.pop(ticker, None)

    def insert_trade(self, ticker, side, quantity, price, *, user_id: str = "default") -> dict:
        row = {"id": str(uuid.uuid4()), "ticker": ticker, "side": side,
               "quantity": quantity, "price": price, "executed_at": _now()}
        self.trades.append(row)
        return dict(row)

    def list_trades(self, limit: int = 100, *, user_id: str = "default") -> list[dict]:
        return list(reversed(self.trades))[:limit]

    def last_trade_prices(self, *, user_id: str = "default") -> dict[str, float]:
        return {t["ticker"]: t["price"] for t in self.trades}

    def insert_snapshot(self, total_value: float, *, user_id: str = "default") -> dict:
        row = {"id": str(uuid.uuid4()), "total_value": total_value, "recorded_at": _now()}
        self.snapshots.append(row)
        return dict(row)

    def list_snapshots(self, limit: int = 1000, *, user_id: str = "default") -> list[dict]:
        return [dict(s) for s in self.snapshots[-limit:]]

    def insert_chat_message(self, role, content, actions=None, *, user_id: str = "default"):
        row = {"id": str(uuid.uuid4()), "role": role, "content": content,
               "actions": actions, "created_at": _now()}
        self.chat.append(row)
        return dict(row)

    def list_chat_messages(self, limit: int = 50, *, user_id: str = "default") -> list[dict]:
        return [dict(m) for m in self.chat[-limit:]]


def make_db(tmp_path):
    try:
        from app.db import Database
    except ImportError:
        return FakeDatabase()
    return Database(str(tmp_path / "test.db"))


class FakeSource(MarketDataSource):
    """Prices come from a fixed table; tickers not in the table are 'unknown' (never cached)."""

    def __init__(self, cache: PriceCache, prices: dict[str, float] | None = None) -> None:
        self.cache = cache
        self.prices = dict(prices or {})
        self.tickers: list[str] = []
        self.stopped = False

    async def start(self, tickers, initial_prices=None) -> None:
        for t in tickers:
            await self.add_ticker(t)

    async def stop(self) -> None:
        self.stopped = True

    async def add_ticker(self, ticker: str) -> None:
        t = normalize_ticker(ticker)
        if t not in self.tickers:
            self.tickers.append(t)
        if t in self.prices:
            self.cache.update(t, self.prices[t], session_open=self.prices[t])

    async def remove_ticker(self, ticker: str) -> None:
        t = normalize_ticker(ticker)
        if t in self.tickers:
            self.tickers.remove(t)
        self.cache.remove(t)

    def get_tickers(self) -> list[str]:
        return list(self.tickers)

    def status(self) -> dict[str, Any]:
        return {"source": "fake", "mode": "simulated", "tickers": len(self.tickers),
                "last_success": None, "last_error": None}

    def set_price(self, ticker: str, price: float) -> None:
        """Move the market: update the table and the cache."""
        self.prices[ticker] = price
        if ticker in self.tickers:
            self.cache.update(ticker, price)


PRICES = {
    "AAPL": 190.0, "GOOGL": 175.0, "MSFT": 420.0, "AMZN": 185.0, "TSLA": 250.0,
    "NVDA": 120.0, "META": 500.0, "JPM": 200.0, "V": 280.0, "NFLX": 650.0,
    "PYPL": 70.0, "BRK.B": 450.0,
}
