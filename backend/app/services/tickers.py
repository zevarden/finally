"""Ticker validation and tracked-set helpers."""

from __future__ import annotations

import re

from app.market import MarketDataSource, normalize_ticker

TICKER_RE = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")


def validate_ticker(ticker: object) -> str | None:
    """Return the normalized ticker, or None if it is not a valid symbol."""
    if not isinstance(ticker, str):
        return None
    t = normalize_ticker(ticker)
    return t if TICKER_RE.match(t) else None


def is_watched(db, ticker: str) -> bool:
    return any(row["ticker"] == ticker for row in db.list_watchlist())


def is_held(db, ticker: str) -> bool:
    return db.get_position(ticker) is not None


def tracked_tickers(db) -> list[str]:
    """Tickers the market source should track: watchlist ∪ held positions (watchlist order first)."""
    tickers = [row["ticker"] for row in db.list_watchlist()]
    tickers += [row["ticker"] for row in db.list_positions()]
    return list(dict.fromkeys(tickers))


async def untrack_if_unused(db, source: MarketDataSource, ticker: str) -> None:
    """Stop tracking `ticker` unless it is still watched or held."""
    if not is_watched(db, ticker) and not is_held(db, ticker):
        await source.remove_ticker(ticker)
