"""Watchlist management."""

from __future__ import annotations

from app.market import MarketDataSource, PriceCache

from .errors import WatchlistError
from .tickers import is_held, is_watched, untrack_if_unused, validate_ticker


def watchlist_entry(ticker: str, added_at: str | None, cache: PriceCache) -> dict:
    """One watchlist row with live price fields (null if not cached yet)."""
    u = cache.get(ticker)
    return {
        "ticker": ticker,
        "added_at": added_at,
        "price": u.price if u else None,
        "previous_price": u.previous_price if u else None,
        "change_percent": u.change_percent if u else None,
        "day_change_percent": u.day_change_percent if u else None,
        "direction": u.direction if u else None,
    }


def get_watchlist(db, cache: PriceCache) -> list[dict]:
    """All watchlist entries (ordered by added_at) with live prices."""
    return [watchlist_entry(r["ticker"], r["added_at"], cache) for r in db.list_watchlist()]


async def add_ticker(db, cache: PriceCache, source: MarketDataSource, ticker: str) -> dict:
    """Add a ticker to the watchlist and start tracking it. Returns the entry."""
    t = validate_ticker(ticker)
    if t is None:
        raise WatchlistError(f"Invalid ticker: {ticker!r}", 400)
    if is_watched(db, t):
        raise WatchlistError(f"{t} is already on the watchlist", 409)

    await source.add_ticker(t)
    if t not in cache:
        if not is_held(db, t):
            await source.remove_ticker(t)
        raise WatchlistError(f"Unknown ticker: {t}", 404)

    if not db.add_watchlist(t):
        raise WatchlistError(f"{t} is already on the watchlist", 409)
    added_at = next((r["added_at"] for r in db.list_watchlist() if r["ticker"] == t), None)
    return watchlist_entry(t, added_at, cache)


async def remove_ticker(db, cache: PriceCache, source: MarketDataSource, ticker: str) -> None:
    """Remove a ticker from the watchlist. Keeps tracking it if a position is still held."""
    t = validate_ticker(ticker)
    if t is None:
        raise WatchlistError(f"Invalid ticker: {ticker!r}", 400)
    if not db.remove_watchlist(t):
        raise WatchlistError(f"{t} is not on the watchlist", 404)
    await untrack_if_unused(db, source, t)
