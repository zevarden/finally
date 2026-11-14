"""Business logic shared by the REST routes and the LLM chat (TEAM_CONTRACT.md §4).

execute_trade, add_ticker and remove_ticker are coroutines (they call the async
market source); get_portfolio, get_watchlist and record_snapshot are plain functions.
"""

from .errors import ServiceError, TradeError, WatchlistError
from .portfolio import execute_trade, get_portfolio, record_snapshot
from .tickers import tracked_tickers, validate_ticker
from .watchlist import add_ticker, get_watchlist, remove_ticker, watchlist_entry

__all__ = [
    "ServiceError",
    "TradeError",
    "WatchlistError",
    "execute_trade",
    "get_portfolio",
    "record_snapshot",
    "add_ticker",
    "remove_ticker",
    "get_watchlist",
    "watchlist_entry",
    "tracked_tickers",
    "validate_ticker",
]
