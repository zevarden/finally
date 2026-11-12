"""Market data subsystem for FinAlly. Consumers import only from here.

Public API:
    PriceUpdate               - Immutable price snapshot dataclass
    PriceCache                - Thread-safe in-memory price store
    MarketDataSource          - Abstract interface for data providers
    normalize_ticker          - Canonical ticker form (strip + upper)
    create_market_data_source - Factory that selects simulator or Massive (unstarted)
    start_market_data         - Create + start, falling back to the simulator on a bad key
    create_stream_router      - FastAPI router factory for the SSE endpoint
"""

from .cache import PriceCache
from .factory import create_market_data_source, start_market_data
from .interface import MarketDataSource, normalize_ticker
from .models import PriceUpdate
from .stream import create_stream_router

__all__ = [
    "PriceUpdate",
    "PriceCache",
    "MarketDataSource",
    "normalize_ticker",
    "create_market_data_source",
    "start_market_data",
    "create_stream_router",
]
