"""GBM-based market simulator (default data source)."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import numpy as np

from .cache import PriceCache
from .interface import MarketDataSource, normalize_ticker
from .seed_prices import (
    CORRELATION_GROUPS,
    CROSS_GROUP_CORR,
    DEFAULT_PARAMS,
    INTRA_FINANCE_CORR,
    INTRA_TECH_CORR,
    SEED_PRICES,
    TICKER_PARAMS,
    TSLA_CORR,
    fallback_seed_price,
)

logger = logging.getLogger(__name__)


class GBMSimulator:
    """Correlated Geometric Brownian Motion. Pure and synchronous: no asyncio, no cache.

    Math:
        S(t+dt) = S(t) * exp((mu - sigma^2/2) * dt + sigma * sqrt(dt) * Z)
        Z = L @ eps,  eps ~ N(0, I),  L = cholesky(C)

    The tiny dt (~8.5e-8 for 500 ms ticks over 252 trading days * 6.5 h/day)
    produces sub-cent moves per tick that accumulate naturally over time.
    Internal prices keep full float precision; only returned values are rounded.
    """

    TRADING_SECONDS_PER_YEAR = 252 * 6.5 * 3600  # 5,896,800
    DEFAULT_DT = 0.5 / TRADING_SECONDS_PER_YEAR  # ~8.48e-8 (one 500 ms tick)

    def __init__(
        self,
        tickers: list[str],
        dt: float = DEFAULT_DT,
        event_probability: float = 0.001,
        rng: np.random.Generator | None = None,
        initial_prices: dict[str, float] | None = None,
    ) -> None:
        self._dt = dt
        self._event_prob = event_probability
        self._rng = rng if rng is not None else np.random.default_rng()
        self._initial = {normalize_ticker(k): v for k, v in (initial_prices or {}).items()}

        self._tickers: list[str] = []
        self._prices: dict[str, float] = {}
        self._params: dict[str, dict[str, float]] = {}
        self._cholesky: np.ndarray | None = None
        # Per-ticker parameter vectors, rebuilt alongside the Cholesky factor
        self._mu = np.empty(0)
        self._sigma = np.empty(0)

        for t in tickers:
            self._add_internal(t)
        self._rebuild()

    # --- public -------------------------------------------------------------

    def step(self) -> dict[str, float]:
        """Advance every ticker one tick. Returns {ticker: price rounded to cents}."""
        n = len(self._tickers)
        if n == 0:
            return {}

        z = self._rng.standard_normal(n)
        if self._cholesky is not None:
            z = self._cholesky @ z

        log_ret = (self._mu - 0.5 * self._sigma**2) * self._dt + self._sigma * np.sqrt(self._dt) * z
        factors = np.exp(log_ret)

        # Idiosyncratic "news" shocks: +/-2-5 % with probability p per ticker per tick.
        # With 10 tickers at 2 ticks/s and p=0.001, expect one event every ~50 s.
        hits = self._rng.random(n) < self._event_prob
        if hits.any():
            k = int(hits.sum())
            mags = self._rng.uniform(0.02, 0.05, k)
            signs = self._rng.choice([-1.0, 1.0], k)
            factors[hits] *= 1.0 + signs * mags
            for t, m, s in zip(np.array(self._tickers)[hits], mags, signs, strict=True):
                logger.debug("Sim event %s %+.1f%%", t, s * m * 100)

        out: dict[str, float] = {}
        for i, t in enumerate(self._tickers):
            self._prices[t] *= float(factors[i])
            out[t] = round(self._prices[t], 2)
        return out

    def add_ticker(self, ticker: str) -> None:
        """Add a ticker and rebuild the correlation matrix. No-op if present."""
        ticker = normalize_ticker(ticker)
        if ticker in self._prices:
            return
        self._add_internal(ticker)
        self._rebuild()

    def remove_ticker(self, ticker: str) -> None:
        """Remove a ticker and rebuild the correlation matrix. No-op if absent."""
        ticker = normalize_ticker(ticker)
        if ticker not in self._prices:
            return
        self._tickers.remove(ticker)
        del self._prices[ticker]
        del self._params[ticker]
        self._rebuild()

    def get_price(self, ticker: str) -> float | None:
        """Current price rounded to cents, or None if not tracked."""
        p = self._prices.get(normalize_ticker(ticker))
        return round(p, 2) if p is not None else None

    def get_tickers(self) -> list[str]:
        return list(self._tickers)

    # --- internals ----------------------------------------------------------

    def _add_internal(self, ticker: str) -> None:
        ticker = normalize_ticker(ticker)
        if ticker in self._prices:
            return
        self._tickers.append(ticker)
        self._prices[ticker] = float(
            self._initial.get(ticker) or SEED_PRICES.get(ticker) or fallback_seed_price(ticker)
        )
        # Copy so per-instance changes never leak into the module-level tables
        self._params[ticker] = dict(TICKER_PARAMS.get(ticker, DEFAULT_PARAMS))

    def _rebuild(self) -> None:
        """Rebuild parameter vectors and the Cholesky factor. O(n^3), n is small."""
        n = len(self._tickers)
        self._mu = np.array([self._params[t]["mu"] for t in self._tickers])
        self._sigma = np.array([self._params[t]["sigma"] for t in self._tickers])
        if n <= 1:
            self._cholesky = None
            return
        corr = np.eye(n)
        for i in range(n):
            for j in range(i + 1, n):
                corr[i, j] = corr[j, i] = self._pairwise_correlation(
                    self._tickers[i], self._tickers[j]
                )
        # Block-equicorrelated with rho in (0, 1) -> always positive definite
        self._cholesky = np.linalg.cholesky(corr)

    @staticmethod
    def _pairwise_correlation(t1: str, t2: str) -> float:
        """Sector-based correlation: tech 0.6, finance 0.5, TSLA/cross/unknown 0.3."""
        if t1 == "TSLA" or t2 == "TSLA":  # TSLA is in tech but does its own thing
            return TSLA_CORR
        tech, fin = CORRELATION_GROUPS["tech"], CORRELATION_GROUPS["finance"]
        if t1 in tech and t2 in tech:
            return INTRA_TECH_CORR
        if t1 in fin and t2 in fin:
            return INTRA_FINANCE_CORR
        return CROSS_GROUP_CORR


class SimulatorDataSource(MarketDataSource):
    """MarketDataSource adapter: steps GBMSimulator every `update_interval` seconds."""

    def __init__(
        self,
        price_cache: PriceCache,
        update_interval: float = 0.5,
        event_probability: float = 0.001,
        seed: int | None = None,
    ) -> None:
        self._cache = price_cache
        self._interval = update_interval
        self._event_prob = event_probability
        self._seed = seed
        self._sim: GBMSimulator | None = None
        self._task: asyncio.Task | None = None
        self._pending: list[str] = []  # tickers added before start()
        self._last_success: float | None = None
        self._last_error: str | None = None

    async def start(
        self, tickers: list[str], initial_prices: dict[str, float] | None = None
    ) -> None:
        tickers = list(dict.fromkeys(normalize_ticker(t) for t in [*self._pending, *tickers]))
        self._pending = []
        self._sim = GBMSimulator(
            tickers=tickers,
            event_probability=self._event_prob,
            rng=np.random.default_rng(self._seed),
            initial_prices=initial_prices,
        )
        for t in tickers:
            self._seed_cache(t)
        self._task = asyncio.create_task(self._run_loop(), name="simulator-loop")
        logger.info("Simulator started with %d tickers", len(tickers))

    async def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None
        logger.info("Simulator stopped")

    async def add_ticker(self, ticker: str) -> None:
        ticker = normalize_ticker(ticker)
        if self._sim is None:
            if ticker not in self._pending:
                self._pending.append(ticker)
            return
        if ticker in self._sim.get_tickers():
            return
        self._sim.add_ticker(ticker)
        self._seed_cache(ticker)  # price available immediately
        logger.info("Simulator: added %s", ticker)

    async def remove_ticker(self, ticker: str) -> None:
        ticker = normalize_ticker(ticker)
        if ticker in self._pending:
            self._pending.remove(ticker)
        if self._sim:
            self._sim.remove_ticker(ticker)
        self._cache.remove(ticker)
        logger.info("Simulator: removed %s", ticker)

    def get_tickers(self) -> list[str]:
        return self._sim.get_tickers() if self._sim else list(self._pending)

    def status(self) -> dict[str, Any]:
        return {
            "source": "simulator",
            "mode": "simulated",
            "tickers": len(self.get_tickers()),
            "last_success": self._last_success,
            "last_error": self._last_error,
        }

    # --- internals ----------------------------------------------------------

    def _seed_cache(self, ticker: str) -> None:
        """Write the starting price; it is also the session open for daily change %."""
        price = self._sim.get_price(ticker) if self._sim else None
        if price is None:
            return
        self._cache.update(ticker, price, session_open=price)

    async def _run_loop(self) -> None:
        while True:
            try:
                if self._sim:
                    for t, p in self._sim.step().items():
                        self._cache.update(t, p)  # session_open carried forward
                    self._last_success = time.time()
            except Exception as e:  # never let one bad tick kill the stream
                self._last_error = repr(e)
                logger.exception("Simulator step failed")
            await asyncio.sleep(self._interval)
