"""Selects and starts the market data source from environment variables."""

from __future__ import annotations

import logging
import os

from .cache import PriceCache
from .interface import MarketDataSource
from .massive_client import MassiveAuthError, MassiveDataSource
from .simulator import SimulatorDataSource

logger = logging.getLogger(__name__)


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        logger.warning("Invalid %s=%r; using %s", name, raw, default)
        return default


def _env_int(name: str) -> int | None:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        logger.warning("Invalid %s=%r; ignoring", name, raw)
        return None


def create_simulator(price_cache: PriceCache) -> SimulatorDataSource:
    """Simulator, seeded from MARKET_SIM_SEED when set (deterministic prices)."""
    return SimulatorDataSource(price_cache=price_cache, seed=_env_int("MARKET_SIM_SEED"))


def create_market_data_source(price_cache: PriceCache) -> MarketDataSource:
    """MASSIVE_API_KEY non-blank -> MassiveDataSource, otherwise SimulatorDataSource.

    Returns an unstarted source; prefer start_market_data(), which also handles fallback.
    """
    api_key = os.environ.get("MASSIVE_API_KEY", "").strip()
    if api_key:
        interval = _env_float("MASSIVE_POLL_INTERVAL", 15.0)
        logger.info("Market data source: Massive API (poll %.0fs)", interval)
        return MassiveDataSource(api_key=api_key, price_cache=price_cache, poll_interval=interval)
    logger.info("Market data source: GBM simulator")
    return create_simulator(price_cache)


async def start_market_data(
    price_cache: PriceCache,
    tickers: list[str],
    initial_prices: dict[str, float] | None = None,
) -> MarketDataSource:
    """Create and start the configured source. A rejected Massive key falls back to the simulator."""
    source = create_market_data_source(price_cache)
    try:
        await source.start(tickers, initial_prices=initial_prices)
        return source
    except MassiveAuthError as e:
        logger.error("MASSIVE_API_KEY rejected (%s); falling back to simulator", e)
        await source.stop()
        sim = create_simulator(price_cache)
        await sim.start(tickers, initial_prices=initial_prices)
        return sim
