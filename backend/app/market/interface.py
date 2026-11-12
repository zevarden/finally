"""Abstract interface for market data sources."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


def normalize_ticker(ticker: str) -> str:
    """Canonical ticker form: stripped and uppercase (Massive is case-sensitive)."""
    return ticker.strip().upper()


class MarketDataSource(ABC):
    """Contract for market data providers.

    Implementations push PriceUpdates into a shared PriceCache from a background
    task. Callers never ask a source for a price; they read the cache.

    Contract (verified for both sources by tests/market/test_conformance.py):
      * Tickers are normalized (strip + upper) and de-duplicated at every entry point.
      * start() populates the cache for the initial tickers before returning
        (best effort for network sources), then starts the background task.
        Tickers added before start() are included.
      * add_ticker() is idempotent and makes a price available as soon as possible.
        After it returns (on a started source), `ticker in cache` is False only if
        the ticker is unknown or the source is temporarily unreachable.
      * remove_ticker() is idempotent and always evicts the ticker from the cache.
      * Exceptions inside the background task are logged, never raised.
      * stop() is idempotent; afterwards the source never writes to the cache.

    Lifecycle:
        source = await start_market_data(cache, ["AAPL", "GOOGL"])
        await source.add_ticker("TSLA")
        await source.remove_ticker("GOOGL")
        await source.stop()
    """

    @abstractmethod
    async def start(
        self, tickers: list[str], initial_prices: dict[str, float] | None = None
    ) -> None:
        """Begin producing prices. Call once.

        initial_prices: optional starting prices (e.g. last trade price per held
        ticker). Used by the simulator; ignored by real-data sources.
        """

    @abstractmethod
    async def stop(self) -> None:
        """Cancel the background task. Safe to call multiple times."""

    @abstractmethod
    async def add_ticker(self, ticker: str) -> None:
        """Start tracking a ticker. No-op if already tracked."""

    @abstractmethod
    async def remove_ticker(self, ticker: str) -> None:
        """Stop tracking a ticker and evict it from the cache. No-op if absent."""

    @abstractmethod
    def get_tickers(self) -> list[str]:
        """Currently tracked tickers."""

    @abstractmethod
    def status(self) -> dict[str, Any]:
        """Diagnostics for /api/health, e.g.
        {"source": "massive", "mode": "live", "tickers": 10,
         "last_success": 1763000000.0, "last_error": None}
        """
