"""Thread-safe in-memory price cache."""

from __future__ import annotations

import time
from threading import Lock

from .models import PriceUpdate

_UNSET: object = object()


class PriceCache:
    """Latest price per ticker. One writer (the active source), many readers.

    A threading.Lock (not asyncio.Lock) is used because MassiveDataSource talks
    to the API from a worker thread via asyncio.to_thread().
    """

    def __init__(self) -> None:
        self._prices: dict[str, PriceUpdate] = {}
        self._lock = Lock()
        self._version = 0

    def update(
        self,
        ticker: str,
        price: float,
        timestamp: float | None = None,
        session_open: float | None | object = _UNSET,
    ) -> PriceUpdate:
        """Record a new price and return the resulting PriceUpdate.

        - First update for a ticker: previous_price == price (direction 'flat').
        - session_open omitted: carried forward from the previous update.
        - timestamp None: current wall-clock time (0 is a valid timestamp).
        """
        with self._lock:
            prev = self._prices.get(ticker)
            if session_open is _UNSET:
                session_open = prev.session_open if prev else None
            rounded = round(price, 2)
            update = PriceUpdate(
                ticker=ticker,
                price=rounded,
                previous_price=prev.price if prev else rounded,
                timestamp=time.time() if timestamp is None else timestamp,
                session_open=round(session_open, 2) if session_open else None,
            )
            self._prices[ticker] = update
            self._version += 1
            return update

    def get(self, ticker: str) -> PriceUpdate | None:
        """Latest PriceUpdate for a ticker, or None if unknown."""
        with self._lock:
            return self._prices.get(ticker)

    def get_price(self, ticker: str) -> float | None:
        """Convenience: just the price, or None."""
        update = self.get(ticker)
        return update.price if update else None

    def get_all(self) -> dict[str, PriceUpdate]:
        """Shallow copy; safe to iterate while the source keeps writing."""
        with self._lock:
            return dict(self._prices)

    def remove(self, ticker: str) -> None:
        """Evict a ticker. Bumps version (so SSE pushes a frame) only if it was present."""
        with self._lock:
            if self._prices.pop(ticker, None) is not None:
                self._version += 1

    @property
    def version(self) -> int:
        """Monotonic counter, bumped on every change. Used by SSE to skip idle frames."""
        return self._version

    def __len__(self) -> int:
        with self._lock:
            return len(self._prices)

    def __contains__(self, ticker: object) -> bool:
        with self._lock:
            return ticker in self._prices
