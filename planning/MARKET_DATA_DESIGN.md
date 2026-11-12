# Market Data Backend — Detailed Design

This is the implementation design for FinAlly's market data subsystem (`backend/app/market/`). It brings the earlier research docs into one buildable spec:

- [MARKET_INTERFACE.md](MARKET_INTERFACE.md): the unified interface and the changes it proposed
- [MARKET_SIMULATOR.md](MARKET_SIMULATOR.md): GBM model and the reasons behind it
- [MASSIVE_API.md](MASSIVE_API.md): Massive (formerly Polygon.io) endpoints, plans and gotchas

Each module below is shown as **complete, drop-in code**. A block that starts with a `# backend/app/market/<file>.py` header is the full content of that file. Other blocks are excerpts or usage examples. The code targets the current `massive` client (`RESTClient`, `TickerSnapshot`, `GroupedDailyAgg`) and Python 3.12+.

The design keeps the public API that already exists (`PriceCache`, `PriceUpdate`, `MarketDataSource`, `create_market_data_source`, `create_stream_router`), so existing callers and most tests keep working. It also fixes the defects found during research and adds the proposed features. §11 lists every change.

---

## 1. Requirements

| # | Requirement (PLAN.md §6, §10) | Where it is met |
|---|---|---|
| R1 | One interface, two implementations, chosen by `MASSIVE_API_KEY` | `MarketDataSource` ABC + `create_market_data_source()` |
| R2 | Simulator: GBM, ~500 ms, correlated, random 2–5 % events, realistic seeds | `GBMSimulator`, `SimulatorDataSource` |
| R3 | Massive: REST polling of the union of watched tickers, configurable interval | `MassiveDataSource` (live mode) |
| R4 | Free Massive tier must still work (snapshots return 403 on Basic) | `MassiveDataSource` (EOD mode) |
| R5 | Shared in-memory cache: latest price, previous price, timestamp | `PriceCache` |
| R6 | SSE `GET /api/stream/prices`, ~500 ms cadence, ticker/price/prev/ts/direction | `create_stream_router()` |
| R7 | Watchlist shows **daily** change % | `PriceUpdate.session_open` / `day_change_percent` |
| R8 | Watchlist add/remove propagates at runtime; new tickers price quickly | `add_ticker()` / `remove_ticker()` |
| R9 | Errors never crash the app; last known prices are kept | try/except in every background loop |
| R10 | Users can see which source is active | `MarketDataSource.status()` → `/api/health` |

## 2. Architecture

```
                     env: MASSIVE_API_KEY, MASSIVE_POLL_INTERVAL
                                   │
                    create_market_data_source(cache)          (factory.py)
                       ┌───────────┴────────────┐
                       ▼                        ▼
            SimulatorDataSource         MassiveDataSource                 ← MarketDataSource (ABC)
            └ GBMSimulator (pure)       ├ mode "live": snapshot poll      (interface.py)
              500 ms asyncio loop       ├ mode "eod":  grouped-daily
                                        └ 401 → raises MassiveAuthError → factory falls back to simulator
                       └───────────┬────────────┘
                                   │ cache.update(ticker, price, timestamp, session_open)
                                   ▼
                     PriceCache  (threading.Lock, version counter)        (cache.py)
        ┌──────────────┬───────────┴──────┬───────────────────┬──────────────────┐
        ▼              ▼                  ▼                   ▼                  ▼
 /api/stream/prices  /api/watchlist   POST /portfolio/trade  portfolio value   LLM prompt context
 (SSE, 500 ms)       (latest prices)  (fill price)           + snapshots       (cache.get_all())
```

Design rules:

1. **Consumers read only the cache.** Nothing outside `app/market/` calls the simulator or Massive for a price.
2. **Sources push.** Each source owns a single background `asyncio.Task` that writes to the cache.
3. **One producer at a time.** Exactly one source is active per process.
4. **Tickers are normalized** (`strip().upper()`) at every public entry point.
5. **The tracked set is watchlist ∪ held positions.** The API layer decides this (§9). The market layer only tracks what it is told.

### Module layout

```
backend/app/market/
├── __init__.py        # public exports
├── models.py          # PriceUpdate (frozen dataclass)
├── cache.py           # PriceCache
├── interface.py       # MarketDataSource ABC + normalize_ticker()
├── seed_prices.py     # data: seed prices, GBM params, correlation groups
├── simulator.py       # GBMSimulator (pure math) + SimulatorDataSource (adapter)
├── massive_client.py  # MassiveDataSource (live + eod modes) + snapshot parsing helpers
├── factory.py         # create_market_data_source(), start_market_data()
└── stream.py          # create_stream_router() → GET /api/stream/prices
```

## 3. Configuration

| Variable | Default | Meaning |
|---|---|---|
| `MASSIVE_API_KEY` | empty | Set and non-blank → Massive. Empty, blank or missing → simulator |
| `MASSIVE_POLL_INTERVAL` | `15` | Seconds between snapshot polls in live mode. Use `2`–`5` on paid tiers. Clamped to ≥ 1 |
| `MARKET_SIM_SEED` | unset | Optional integer. Makes the simulator deterministic (useful for E2E tests and demos) |

Add the optional ones to `.env.example`:

```bash
# Optional: seconds between Massive polls (paid tiers can go as low as 2)
MASSIVE_POLL_INTERVAL=15
# Optional: fixed RNG seed for the simulator (deterministic prices)
MARKET_SIM_SEED=
```

---

## 4. Core Types

### 4.1 `models.py` — `PriceUpdate`

Adds `session_open`, the reference price for the **daily change %** the watchlist displays. `change` and `change_percent` stay tick-to-tick, because the flash animation and direction use them.

| Source | `session_open` means |
|---|---|
| Simulator | Price when the ticker started being simulated ("since session start") |
| Massive live | `prev_day.close` from the snapshot (the standard definition of daily change) |
| Massive EOD | Close of the trading day before the last completed one |

```python
# backend/app/market/models.py
"""Data models for market data."""

from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class PriceUpdate:
    """Immutable snapshot of a single ticker's price at a point in time."""

    ticker: str
    price: float
    previous_price: float
    timestamp: float = field(default_factory=time.time)  # Unix seconds
    session_open: float | None = None  # reference for daily change; None if unknown

    @property
    def change(self) -> float:
        """Absolute change since the previous update (tick-to-tick)."""
        return round(self.price - self.previous_price, 4)

    @property
    def change_percent(self) -> float:
        """Percent change since the previous update (tick-to-tick)."""
        if self.previous_price == 0:
            return 0.0
        return round((self.price - self.previous_price) / self.previous_price * 100, 4)

    @property
    def direction(self) -> str:
        """'up', 'down' or 'flat' relative to the previous update."""
        if self.price > self.previous_price:
            return "up"
        if self.price < self.previous_price:
            return "down"
        return "flat"

    @property
    def day_change(self) -> float | None:
        if not self.session_open:
            return None
        return round(self.price - self.session_open, 4)

    @property
    def day_change_percent(self) -> float | None:
        if not self.session_open:
            return None
        return round((self.price - self.session_open) / self.session_open * 100, 4)

    def to_dict(self) -> dict:
        """Serialize for JSON / SSE. These keys are the frontend contract."""
        return {
            "ticker": self.ticker,
            "price": self.price,
            "previous_price": self.previous_price,
            "timestamp": self.timestamp,
            "change": self.change,
            "change_percent": self.change_percent,
            "direction": self.direction,
            "session_open": self.session_open,
            "day_change": self.day_change,
            "day_change_percent": self.day_change_percent,
        }
```

Example:

```python
>>> u = PriceUpdate("AAPL", price=191.00, previous_price=190.50, timestamp=1763000000.0, session_open=190.00)
>>> u.direction, u.change, u.change_percent, u.day_change_percent
('up', 0.5, 0.2625, 0.5263)
```

### 4.2 `cache.py` — `PriceCache`

Changes from the current code:

- `update()` takes `session_open=`. If it is omitted, the previous value is carried forward, so a simulator tick doesn't need to resend it.
- `remove()` **bumps `version`**, so SSE pushes a frame without the removed ticker even when no other price changes (important in Massive mode, which updates only every 15 s).
- `timestamp=0` is no longer treated as "missing" (`is None` check rather than `or`).

```python
# backend/app/market/cache.py
"""Thread-safe in-memory price cache."""

from __future__ import annotations

import time
from threading import Lock

from .models import PriceUpdate

_UNSET = object()


class PriceCache:
    """Latest price per ticker. One writer (the active source), many readers.

    A threading.Lock (not asyncio.Lock) is used because MassiveDataSource parses
    responses in a worker thread via asyncio.to_thread().
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
        """
        with self._lock:
            prev = self._prices.get(ticker)
            if session_open is _UNSET:
                session_open = prev.session_open if prev else None
            update = PriceUpdate(
                ticker=ticker,
                price=round(price, 2),
                previous_price=prev.price if prev else round(price, 2),
                timestamp=time.time() if timestamp is None else timestamp,
                session_open=round(session_open, 2) if session_open else None,
            )
            self._prices[ticker] = update
            self._version += 1
            return update

    def get(self, ticker: str) -> PriceUpdate | None:
        with self._lock:
            return self._prices.get(ticker)

    def get_price(self, ticker: str) -> float | None:
        update = self.get(ticker)
        return update.price if update else None

    def get_all(self) -> dict[str, PriceUpdate]:
        """Shallow copy; safe to iterate while the source keeps writing."""
        with self._lock:
            return dict(self._prices)

    def remove(self, ticker: str) -> None:
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
```

### 4.3 `interface.py` — `MarketDataSource`

Two additions: `start()` accepts optional `initial_prices`, so the simulator can resume near the last traded prices after a restart instead of jumping back to seed values; and `status()` reports which source and mode are active.

```python
# backend/app/market/interface.py
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

    Contract:
      * start() populates the cache for the initial tickers before returning
        (best effort for network sources), then starts the background task.
      * add_ticker() is idempotent and makes a price available as soon as possible.
        After it returns, `ticker in cache` is False only if the ticker is unknown
        or the source is temporarily unreachable.
      * remove_ticker() is idempotent and always evicts the ticker from the cache.
      * Exceptions inside the background task are logged, never raised.
      * stop() is idempotent; afterwards the source never writes to the cache.
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
```

---

## 5. Simulator

### 5.1 `seed_prices.py`

This file is unchanged: `SEED_PRICES`, `TICKER_PARAMS`, `DEFAULT_PARAMS`, `CORRELATION_GROUPS`, `INTRA_TECH_CORR=0.6`, `INTRA_FINANCE_CORR=0.5`, `CROSS_GROUP_CORR=0.3`, `TSLA_CORR=0.3`. One helper is added for unknown tickers:

```python
# appended to backend/app/market/seed_prices.py (put `import zlib` at the top of the file)
import zlib  # noqa: E402


def fallback_seed_price(ticker: str) -> float:
    """Stable pseudo-random seed in [50, 300) for tickers without a SEED_PRICES entry.

    Deterministic per ticker, so PYPL starts at the same price after a restart or
    after being removed and re-added (random.uniform would make it jump).
    """
    return 50.0 + (zlib.crc32(ticker.encode()) % 25_000) / 100.0
```

### 5.2 The math (summary; see MARKET_SIMULATOR.md §2)

```
S(t+Δt) = S(t) · exp((μ − σ²/2)·Δt + σ·√Δt·Z),   Δt = 0.5 / (252·6.5·3600) ≈ 8.48e-8
Z = L·ε,  ε ~ N(0, I),  L = cholesky(C),  C[i,j] = sector correlation (0.6 / 0.5 / 0.3)
event: with p = 0.001 per ticker per tick, S *= 1 ± U(0.02, 0.05)
```

### 5.3 `simulator.py`

Changes from the current code:

- **One RNG** (`np.random.Generator`) for normals, event draws and signs. A single seed makes runs fully reproducible, and tests no longer share global state.
- The **vectorized step**: one NumPy expression for all tickers instead of a Python loop.
- `initial_prices` and the stable `fallback_seed_price` for unknown tickers.
- **`session_open`** is recorded per ticker and passed to the cache.
- Ticker normalization in the adapter.

```python
# backend/app/market/simulator.py
"""GBM-based market simulator (default data source)."""

from __future__ import annotations

import asyncio
import logging
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
        # Cached per-ticker vectors, rebuilt alongside the Cholesky factor
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

        # Idiosyncratic "news" shocks: ±2–5 % with probability p per ticker per tick
        hits = self._rng.random(n) < self._event_prob
        if hits.any():
            k = int(hits.sum())
            mags = self._rng.uniform(0.02, 0.05, k)
            signs = self._rng.choice([-1.0, 1.0], k)
            factors[hits] *= 1.0 + signs * mags
            for t, m, s in zip(np.array(self._tickers)[hits], mags, signs):
                logger.debug("Sim event %s %+.1f%%", t, s * m * 100)

        out: dict[str, float] = {}
        for i, t in enumerate(self._tickers):
            self._prices[t] *= float(factors[i])
            out[t] = round(self._prices[t], 2)
        return out

    def add_ticker(self, ticker: str) -> None:
        if ticker in self._prices:
            return
        self._add_internal(ticker)
        self._rebuild()

    def remove_ticker(self, ticker: str) -> None:
        if ticker not in self._prices:
            return
        self._tickers.remove(ticker)
        del self._prices[ticker]
        del self._params[ticker]
        self._rebuild()

    def get_price(self, ticker: str) -> float | None:
        p = self._prices.get(ticker)
        return round(p, 2) if p is not None else None

    def get_tickers(self) -> list[str]:
        return list(self._tickers)

    # --- internals ----------------------------------------------------------

    def _add_internal(self, ticker: str) -> None:
        if ticker in self._prices:
            return
        self._tickers.append(ticker)
        self._prices[ticker] = float(
            self._initial.get(ticker) or SEED_PRICES.get(ticker) or fallback_seed_price(ticker)
        )
        self._params[ticker] = dict(TICKER_PARAMS.get(ticker, DEFAULT_PARAMS))

    def _rebuild(self) -> None:
        """Rebuild parameter vectors and the Cholesky factor. O(n³), n is small."""
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
        # Block-equicorrelated with rho in (0,1) → always positive definite
        self._cholesky = np.linalg.cholesky(corr)

    @staticmethod
    def _pairwise_correlation(t1: str, t2: str) -> float:
        if t1 == "TSLA" or t2 == "TSLA":
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
        self._session_open: dict[str, float] = {}
        self._last_success: float | None = None
        self._last_error: str | None = None

    async def start(
        self, tickers: list[str], initial_prices: dict[str, float] | None = None
    ) -> None:
        tickers = list(dict.fromkeys(normalize_ticker(t) for t in tickers))
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
        if self._sim is None or ticker in self._sim.get_tickers():
            return
        self._sim.add_ticker(ticker)
        self._seed_cache(ticker)  # price available immediately
        logger.info("Simulator: added %s", ticker)

    async def remove_ticker(self, ticker: str) -> None:
        ticker = normalize_ticker(ticker)
        if self._sim:
            self._sim.remove_ticker(ticker)
        self._session_open.pop(ticker, None)
        self._cache.remove(ticker)
        logger.info("Simulator: removed %s", ticker)

    def get_tickers(self) -> list[str]:
        return self._sim.get_tickers() if self._sim else []

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
        price = self._sim.get_price(ticker) if self._sim else None
        if price is None:
            return
        self._session_open[ticker] = price
        self._cache.update(ticker, price, session_open=price)

    async def _run_loop(self) -> None:
        loop = asyncio.get_running_loop()
        while True:
            try:
                if self._sim:
                    for t, p in self._sim.step().items():
                        self._cache.update(t, p)  # session_open carried forward
                    self._last_success = loop.time()
            except Exception as e:  # never let one bad tick kill the stream
                self._last_error = repr(e)
                logger.exception("Simulator step failed")
            await asyncio.sleep(self._interval)
```

> `last_success` uses `loop.time()` (monotonic) here. Use `time.time()` if `/api/health` should show wall-clock time; the Massive source below does that.

Usage without asyncio (for notebooks and tests):

```python
from app.market.simulator import GBMSimulator
import numpy as np

sim = GBMSimulator(["AAPL", "MSFT", "JPM"], rng=np.random.default_rng(42), event_probability=0)
for _ in range(3):
    print(sim.step())
# {'AAPL': 190.01, 'MSFT': 420.03, 'JPM': 194.99} ... (deterministic for seed 42)
```

---

## 6. Massive API Source

### 6.1 Behavior

`MassiveDataSource` has two **modes**, picked by a capability probe in `start()`:

| Probe result (`get_snapshot_all` for the start tickers) | Mode | Poll | Price written | `session_open` |
|---|---|---|---|---|
| 200 | `live` | snapshot every `poll_interval` (15 s default) | `last_trade.price`, falling back to `min.close` → `day.close` → `prev_day.close` | `prev_day.close` |
| 403 (`NOT_AUTHORIZED`, Basic/free plan) | `eod` | grouped-daily every hour | last completed trading day's close | close of the trading day before that |
| 401 / unknown API key | — | — | raise `MassiveAuthError`; the factory falls back to the simulator | — |
| network error / 5xx / 429 | `live` (assumed) | keeps polling | nothing until a poll succeeds | — |

Other points:

- **One API call per poll** in live mode, whatever the size of the watchlist. Grouped daily is also a single call for all US stocks, so EOD mode uses 2 calls at start (two trading days) and 2 calls per hour.
- The sync `RESTClient` always runs in `asyncio.to_thread`, and the thread gets a **copy** of the ticker list.
- `RESTClient(retries=0)`: our own loop is the retry. The client's built-in 429 retry would burn free-tier quota (5 calls/min).
- **`add_ticker` fetches immediately.** In live mode it calls `get_snapshot_ticker` (1 call). In EOD mode it reads from the grouped-daily bars already in memory (0 calls). If the ticker is unknown, nothing is cached, and the route turns that into a 404 (§9.2).
- Unknown tickers are **silently omitted** by `get_snapshot_all`. The source logs the difference once per ticker.
- Timestamps: `last_trade.sip_timestamp` is **nanoseconds** → `/ 1e9`. Bar `timestamp`s are **milliseconds** → `/ 1e3`.

### 6.2 Error classification

`massive.exceptions.BadResponse` has no status code. Its message is the raw response body (`raise BadResponse(resp.data.decode())`), so we classify it by content:

```json
// 401
{"status":"ERROR","request_id":"...","error":"Unknown API Key"}
// 403 on Basic
{"status":"NOT_AUTHORIZED","request_id":"...","message":"You are not entitled to this data. Please upgrade your plan at https://massive.com/pricing"}
// 429
{"status":"ERROR","request_id":"...","error":"You've exceeded the maximum requests per minute, please wait or upgrade your subscription to continue. https://massive.com/pricing"}
```

### 6.3 `massive_client.py`

```python
# backend/app/market/massive_client.py
"""Massive (formerly Polygon.io) REST data source."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import date, timedelta
from typing import Any, Literal

from massive import RESTClient
from massive.exceptions import BadResponse
from massive.rest.models import SnapshotMarketType

from .cache import PriceCache
from .interface import MarketDataSource, normalize_ticker

logger = logging.getLogger(__name__)

Mode = Literal["starting", "live", "eod"]
ErrorKind = Literal["auth", "plan", "rate_limit", "other"]

EOD_REFRESH_SECONDS = 3600.0
EOD_MAX_LOOKBACK_DAYS = 10


class MassiveAuthError(RuntimeError):
    """The API key was rejected (401). The factory falls back to the simulator."""


# --- pure helpers (unit-tested directly) -------------------------------------


def classify_error(exc: BaseException) -> ErrorKind:
    """Map a massive BadResponse (whose message is the raw JSON body) to a kind."""
    if not isinstance(exc, BadResponse):
        return "other"
    text = str(exc)
    try:
        body = json.loads(text)
    except ValueError:
        body = {}
    status = str(body.get("status", "")).upper()
    detail = f"{body.get('error', '')} {body.get('message', '')} {text}".lower()
    if "api key" in detail or (status != "NOT_AUTHORIZED" and "unauthorized" in detail):
        return "auth"
    if status == "NOT_AUTHORIZED" or "not entitled" in detail or "upgrade your plan" in detail:
        return "plan"
    if "exceeded" in detail or "too many" in detail:
        return "rate_limit"
    return "other"


def extract_price(snap: Any) -> tuple[float, float | None] | None:
    """(price, unix_seconds | None) from a TickerSnapshot, or None if no usable price.

    Prefers the last trade; falls back to bar closes, e.g. after the overnight
    snapshot reset (~3:30 AM ET) when last_trade is empty.
    """
    trade = getattr(snap, "last_trade", None)
    if trade is not None and trade.price:
        ts = trade.sip_timestamp / 1e9 if trade.sip_timestamp else None  # ns → s
        return float(trade.price), ts
    minute = getattr(snap, "min", None)
    if minute is not None and minute.close:
        ts = minute.timestamp / 1e3 if minute.timestamp else None  # ms → s
        return float(minute.close), ts
    for bar in (getattr(snap, "day", None), getattr(snap, "prev_day", None)):
        if bar is not None and bar.close:
            return float(bar.close), None
    return None


def extract_session_open(snap: Any) -> float | None:
    prev = getattr(snap, "prev_day", None)
    return float(prev.close) if prev is not None and prev.close else None


class MassiveDataSource(MarketDataSource):
    """Polls Massive and writes prices to the PriceCache.

    live mode: GET /v2/snapshot/locale/us/markets/stocks/tickers?tickers=... every poll_interval
    eod mode:  GET /v2/aggs/grouped/locale/us/market/stocks/{date} hourly (free Basic plan)
    """

    def __init__(
        self,
        api_key: str,
        price_cache: PriceCache,
        poll_interval: float = 15.0,
        client: Any | None = None,  # inject a fake in tests
    ) -> None:
        self._api_key = api_key
        self._cache = price_cache
        self._interval = max(1.0, poll_interval)
        self._client = client
        self._tickers: list[str] = []
        self._task: asyncio.Task | None = None
        self._mode: Mode = "starting"
        # eod mode: ticker -> (close, previous_close, bar_timestamp_s)
        self._eod_bars: dict[str, tuple[float, float | None, float | None]] = {}
        self._missing_logged: set[str] = set()
        self._last_success: float | None = None
        self._last_error: str | None = None

    # --- lifecycle ----------------------------------------------------------

    async def start(
        self, tickers: list[str], initial_prices: dict[str, float] | None = None
    ) -> None:
        # initial_prices is ignored: real data needs no seed.
        self._tickers = list(dict.fromkeys(normalize_ticker(t) for t in tickers))
        if self._client is None:
            self._client = RESTClient(api_key=self._api_key, retries=0)

        await self._probe()  # sets mode and seeds the cache; may raise MassiveAuthError
        self._task = asyncio.create_task(self._loop(), name="massive-poller")
        logger.info(
            "Massive started: mode=%s, %d tickers, interval=%.0fs",
            self._mode, len(self._tickers), self._current_interval(),
        )

    async def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None
        logger.info("Massive stopped")

    async def add_ticker(self, ticker: str) -> None:
        ticker = normalize_ticker(ticker)
        if ticker in self._tickers:
            return
        self._tickers.append(ticker)
        try:
            if self._mode == "eod":
                self._write_eod(ticker)
            else:
                snap = await asyncio.to_thread(
                    self._client.get_snapshot_ticker, SnapshotMarketType.STOCKS, ticker
                )
                self._write_snapshot(snap)
        except Exception as e:  # unknown ticker (404) or transient; next poll retries
            logger.warning("Massive: immediate fetch for %s failed: %s", ticker, e)

    async def remove_ticker(self, ticker: str) -> None:
        ticker = normalize_ticker(ticker)
        self._tickers = [t for t in self._tickers if t != ticker]
        self._missing_logged.discard(ticker)
        self._cache.remove(ticker)

    def get_tickers(self) -> list[str]:
        return list(self._tickers)

    def status(self) -> dict[str, Any]:
        return {
            "source": "massive",
            "mode": self._mode,
            "tickers": len(self._tickers),
            "poll_interval": self._current_interval(),
            "last_success": self._last_success,
            "last_error": self._last_error,
        }

    # --- probe & loop -------------------------------------------------------

    async def _probe(self) -> None:
        try:
            await self._poll_live()
            self._mode = "live"
        except BadResponse as e:
            kind = classify_error(e)
            if kind == "auth":
                raise MassiveAuthError(str(e)) from e
            if kind == "plan":
                logger.warning(
                    "Massive key has no snapshot access (free/Basic plan); "
                    "using end-of-day closes, refreshed hourly"
                )
                self._mode = "eod"
                await self._safe(self._poll_eod)
            else:
                self._record_error(e)
                self._mode = "live"  # assume live; the loop keeps retrying
        except Exception as e:  # network down at boot: keep the app up
            self._record_error(e)
            self._mode = "live"

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(self._current_interval())
            await self._safe(self._poll_eod if self._mode == "eod" else self._poll_live)

    async def _safe(self, fn) -> None:
        try:
            await fn()
        except Exception as e:
            self._record_error(e)

    def _current_interval(self) -> float:
        return EOD_REFRESH_SECONDS if self._mode == "eod" else self._interval

    def _record_error(self, e: BaseException) -> None:
        kind = classify_error(e)
        self._last_error = f"{kind}: {e}"[:300]
        logger.warning("Massive poll failed (%s): %s", kind, e)

    # --- live mode ----------------------------------------------------------

    async def _poll_live(self) -> None:
        tickers = list(self._tickers)  # copy: the list may change while the thread runs
        if not tickers:
            return
        snaps = await asyncio.to_thread(
            self._client.get_snapshot_all, SnapshotMarketType.STOCKS, tickers
        )
        seen = {s.ticker for s in snaps if self._write_snapshot(s)}
        for t in set(tickers) - seen - self._missing_logged:
            logger.warning("Massive returned no price for %s (unknown ticker?)", t)
            self._missing_logged.add(t)
        self._last_success = time.time()
        self._last_error = None

    def _write_snapshot(self, snap: Any) -> bool:
        ticker = getattr(snap, "ticker", None)
        if not ticker or ticker not in self._tickers:  # removed while in flight
            return False
        extracted = extract_price(snap)
        if extracted is None:
            return False
        price, ts = extracted
        self._cache.update(ticker, price, timestamp=ts, session_open=extract_session_open(snap))
        return True

    # --- eod mode -----------------------------------------------------------

    async def _poll_eod(self) -> None:
        bars = await asyncio.to_thread(self._fetch_last_two_sessions)
        if bars:
            self._eod_bars = bars
        for t in list(self._tickers):
            self._write_eod(t)
        self._last_success = time.time()
        self._last_error = None

    def _write_eod(self, ticker: str) -> None:
        bar = self._eod_bars.get(ticker)
        if bar is None:
            return
        close, prev_close, ts = bar
        self._cache.update(ticker, close, timestamp=ts, session_open=prev_close)

    def _fetch_last_two_sessions(self) -> dict[str, tuple[float, float | None, float | None]]:
        """Sync (runs in a thread). Walk back from yesterday to the last two trading days.

        Each grouped-daily call returns every US stock (~10k rows) in one request.
        Weekends and holidays return an empty list. On Basic, today's bar is not
        available until after midnight ET, so we start from yesterday.
        """
        sessions: list[dict[str, Any]] = []
        d = date.today() - timedelta(days=1)
        for _ in range(EOD_MAX_LOOKBACK_DAYS):
            if len(sessions) == 2:
                break
            rows = self._client.get_grouped_daily_aggs(d.isoformat(), adjusted=True)
            if rows:
                sessions.append({r.ticker: r for r in rows if r.ticker and r.close})
            d -= timedelta(days=1)
        if not sessions:
            return {}
        latest, prior = sessions[0], (sessions[1] if len(sessions) > 1 else {})
        return {
            t: (
                float(r.close),
                float(prior[t].close) if t in prior else None,
                r.timestamp / 1e3 if r.timestamp else None,
            )
            for t, r in latest.items()
        }
```

Notes:

- **Memory in EOD mode:** `_eod_bars` holds about 10k small tuples (well under 2 MB). Keeping the whole market means `add_ticker` in EOD mode needs no API call, and it doubles as ticker validation.
- **Rate budget on the free tier (5 calls/min):** the probe is 1 call (a 403 still counts) plus 2–4 grouped-daily calls (more if it walks back over a weekend). That stays inside the limit. A long holiday weekend could exceed 5. In that case the 429 is logged, the loop retries an hour later, and the cache keeps whatever was fetched.
- **Market hours:** no special handling is needed. Outside trading hours, live snapshots return the last trade, so prices simply stop changing. SSE sends no frames because `cache.version` doesn't move.

Direct client usage, for exploring the API in a REPL:

```python
from massive import RESTClient
from massive.rest.models import SnapshotMarketType
from app.market.massive_client import extract_price, extract_session_open

client = RESTClient(retries=0)  # reads MASSIVE_API_KEY
for s in client.get_snapshot_all(SnapshotMarketType.STOCKS, ["AAPL", "NVDA"]):
    print(s.ticker, extract_price(s), extract_session_open(s))
# AAPL (228.41, 1763061599.81) 226.96
# NVDA (191.12, 1763061599.95) 193.80
```

---

## 7. Factory — `factory.py`

`create_market_data_source()` stays synchronous and returns an unstarted source, as it does today. A new async helper, `start_market_data()`, starts the source and **falls back to the simulator if the Massive key is rejected**, so a typo in `.env` never leaves the user without prices.

```python
# backend/app/market/factory.py
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
    try:
        return float(os.environ.get(name, "").strip() or default)
    except ValueError:
        logger.warning("Invalid %s; using %s", name, default)
        return default


def _env_int(name: str) -> int | None:
    raw = os.environ.get(name, "").strip()
    return int(raw) if raw.lstrip("-").isdigit() else None


def create_simulator(price_cache: PriceCache) -> SimulatorDataSource:
    return SimulatorDataSource(price_cache=price_cache, seed=_env_int("MARKET_SIM_SEED"))


def create_market_data_source(price_cache: PriceCache) -> MarketDataSource:
    """MASSIVE_API_KEY non-blank → MassiveDataSource, otherwise SimulatorDataSource.

    Returns an unstarted source; prefer start_market_data() which also handles fallback.
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
```

```python
# backend/app/market/__init__.py
"""Market data subsystem. Consumers import only from here."""

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
```

---

## 8. SSE Streaming — `stream.py`

Changes from the current code:

- The `APIRouter` is created **inside** the factory, so calling it twice (for example in tests) doesn't register the route twice.
- A **keep-alive comment** (`: ping`) is sent after 15 s without data, so proxies and load balancers (App Runner, Render) don't close a quiet Massive stream.
- An empty map is sent (`data: {}`) when the last ticker is removed, so the client clears its table.
- The cache is read **through `request.app.state`** when no cache is passed, so the router works with the lifespan wiring and its fallback swap (§9.1).

### Wire format

```
retry: 1000

data: {"AAPL":{"ticker":"AAPL","price":190.12,"previous_price":190.05,"timestamp":1763000000.5,"change":0.07,"change_percent":0.0368,"direction":"up","session_open":190.0,"day_change":0.12,"day_change_percent":0.0632},"MSFT":{...}}

: ping

```

Every event holds the **full map** of tracked tickers. The client replaces its state with each frame, so removals take effect without a separate event type.

```python
# backend/app/market/stream.py
"""SSE endpoint: GET /api/stream/prices."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncGenerator

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from .cache import PriceCache

logger = logging.getLogger(__name__)

STREAM_INTERVAL = 0.5
KEEPALIVE_SECONDS = 15.0


def create_stream_router(
    price_cache: PriceCache | None = None,
    interval: float = STREAM_INTERVAL,
) -> APIRouter:
    """Router factory. If price_cache is None, it is read from request.app.state.price_cache."""
    router = APIRouter(prefix="/api/stream", tags=["streaming"])

    @router.get("/prices")
    async def stream_prices(request: Request) -> StreamingResponse:
        cache = price_cache or request.app.state.price_cache
        return StreamingResponse(
            generate_events(cache, request, interval),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    return router


async def generate_events(
    cache: PriceCache, request: Request, interval: float = STREAM_INTERVAL
) -> AsyncGenerator[str, None]:
    yield "retry: 1000\n\n"  # EventSource reconnects after 1 s
    client = request.client.host if request.client else "unknown"
    logger.info("SSE connected: %s", client)

    last_version = -1
    idle = 0.0
    try:
        while not await request.is_disconnected():
            version = cache.version
            if version != last_version:
                last_version = version
                payload = {t: u.to_dict() for t, u in cache.get_all().items()}
                yield f"data: {json.dumps(payload, separators=(',', ':'))}\n\n"
                idle = 0.0
            elif idle >= KEEPALIVE_SECONDS:
                yield ": ping\n\n"
                idle = 0.0
            await asyncio.sleep(interval)
            idle += interval
    except asyncio.CancelledError:
        pass
    finally:
        logger.info("SSE disconnected: %s", client)
```

Payload size: about 260 bytes per ticker, so 10 tickers × 2 frames/s ≈ 5 KB/s per client. That's fine for one user. If it ever matters, send only the tickers whose `PriceUpdate` changed since the last frame (track per-ticker versions).

---

## 9. Integration with the Rest of the Backend

### 9.1 App lifespan (`backend/app/main.py`)

```python
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app import db  # owned by the backend agent
from app.market import PriceCache, create_stream_router, start_market_data


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init()  # lazy create + seed
    tracked = sorted(set(db.get_watchlist_tickers()) | set(db.get_position_tickers()))
    # Simulator resumes near the last fill price of held tickers (no fake P&L jump on restart)
    initial = db.get_last_trade_prices(tracked)  # {ticker: price}

    cache = PriceCache()
    source = await start_market_data(cache, tracked, initial_prices=initial)
    app.state.price_cache = cache
    app.state.market_source = source
    try:
        yield
    finally:
        await source.stop()


app = FastAPI(lifespan=lifespan)
app.include_router(create_stream_router())  # reads app.state.price_cache per request
```

### 9.2 Watchlist routes

```python
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.market import normalize_ticker

router = APIRouter(prefix="/api/watchlist")


class AddTicker(BaseModel):
    ticker: str


@router.get("")
async def list_watchlist(request: Request):
    cache = request.app.state.price_cache
    return [
        {"ticker": t, **(cache.get(t).to_dict() if t in cache else {"price": None})}
        for t in db.get_watchlist_tickers()
    ]


@router.post("", status_code=201)
async def add(body: AddTicker, request: Request):
    ticker = normalize_ticker(body.ticker)
    if not ticker.replace(".", "").isalnum() or len(ticker) > 10:
        raise HTTPException(422, "Invalid ticker")
    source, cache = request.app.state.market_source, request.app.state.price_cache
    await source.add_ticker(ticker)
    if ticker not in cache:  # Massive didn't recognize it (the simulator always prices)
        if not db.has_position(ticker):
            await source.remove_ticker(ticker)
        raise HTTPException(404, f"Unknown ticker {ticker}")
    db.add_watchlist(ticker)
    return cache.get(ticker).to_dict()


@router.delete("/{ticker}", status_code=204)
async def remove(ticker: str, request: Request):
    ticker = normalize_ticker(ticker)
    db.remove_watchlist(ticker)
    if not db.has_position(ticker):  # keep pricing what the user still holds
        await request.app.state.market_source.remove_ticker(ticker)
```

The LLM's `watchlist_changes` call the same functions, so the "tracked = watchlist ∪ positions" rule lives in one place.

### 9.3 Trade execution

```python
async def get_fill_price(request: Request, ticker: str) -> float:
    ticker = normalize_ticker(ticker)
    cache, source = request.app.state.price_cache, request.app.state.market_source
    if ticker not in cache:  # trading a ticker that isn't tracked yet
        await source.add_ticker(ticker)
    price = cache.get_price(ticker)
    if price is None:
        raise HTTPException(400, f"No price available for {ticker}")
    return price
```

After a **sell that closes a position** on a ticker that isn't in the watchlist, call `source.remove_ticker(ticker)`.

### 9.4 Portfolio valuation, snapshots and LLM context

```python
def portfolio_value(cache: PriceCache, cash: float, positions: list[dict]) -> dict:
    rows, total = [], cash
    for p in positions:
        u = cache.get(p["ticker"])
        price = u.price if u else p["avg_cost"]  # stale fallback, flagged
        mv = price * p["quantity"]
        total += mv
        rows.append({
            **p,
            "current_price": price,
            "market_value": round(mv, 2),
            "unrealized_pnl": round((price - p["avg_cost"]) * p["quantity"], 2),
            "pnl_percent": round((price / p["avg_cost"] - 1) * 100, 2) if p["avg_cost"] else 0.0,
            "stale": u is None,
        })
    return {"cash": cash, "total_value": round(total, 2), "positions": rows}


# LLM prompt context: compact live quotes
def market_context(cache: PriceCache) -> str:
    return "\n".join(
        f"{t}: ${u.price:.2f} ({u.day_change_percent or 0:+.2f}% today)"
        for t, u in sorted(cache.get_all().items())
    )
```

The 30-second `portfolio_snapshots` task (backend agent) calls `portfolio_value()` with the same cache.

### 9.5 Health

```python
@app.get("/api/health")
async def health(request: Request):
    return {"status": "ok", "market_data": request.app.state.market_source.status()}
# {"status":"ok","market_data":{"source":"massive","mode":"eod","tickers":10,
#   "poll_interval":3600.0,"last_success":1763000000.0,"last_error":null}}
```

The frontend can show a small `SIM`, `LIVE` or `EOD` badge next to the connection dot.

---

## 10. Frontend Contract (for the Frontend agent)

```ts
// frontend: types + hook outline
export interface PriceUpdate {
  ticker: string;
  price: number;
  previous_price: number;
  timestamp: number;           // unix seconds
  change: number;              // tick-to-tick
  change_percent: number;      // tick-to-tick
  direction: "up" | "down" | "flat";
  session_open: number | null;
  day_change: number | null;
  day_change_percent: number | null;  // show this as "daily change %"
}
export type PriceMap = Record<string, PriceUpdate>;

const es = new EventSource("/api/stream/prices");
es.onopen = () => setStatus("connected");
es.onerror = () => setStatus(es.readyState === EventSource.CLOSED ? "disconnected" : "reconnecting");
es.onmessage = (ev) => {
  const prices: PriceMap = JSON.parse(ev.data);
  setPrices(prices);                       // full replace: removed tickers disappear
  for (const u of Object.values(prices)) { // sparkline history, de-duped by timestamp
    appendPoint(u.ticker, u.timestamp, u.price);
  }
};
```

- Flash: when `direction !== "flat"` **and** `timestamp` changed since the last frame, add the class `flash-up` or `flash-down` and remove it after 500 ms.
- In Massive mode, prices change every 15 s (live) or hourly (EOD). Sparklines fill slowly, which is expected.

---

## 11. Changes Relative to the Current Code

| # | File | Change | Reason |
|---|---|---|---|
| 1 | `massive_client.py` | `last_trade.sip_timestamp / 1e9` (was `.timestamp / 1000`) plus the bar-close fallback chain | **Bug**: `AttributeError` on every snapshot, so Massive mode never wrote a price |
| 2 | `massive_client.py` | Startup probe; `eod` mode via grouped daily; `MassiveAuthError` on 401 | The free key got 403 forever and the cache stayed empty |
| 3 | `massive_client.py` | Immediate fetch in `add_ticker`; ticker list copied into the thread; `retries=0`; diff log for unknown tickers | New tickers usable at once; no data races; no wasted quota |
| 4 | `factory.py` | `MASSIVE_POLL_INTERVAL`, `MARKET_SIM_SEED`, `start_market_data()` with simulator fallback | Configurable; a bad key no longer breaks the app |
| 5 | `models.py`, `cache.py` | `session_open`, `day_change`, `day_change_percent`; carry-forward; `remove()` bumps version; `timestamp is None` | Daily change % (PLAN §10); SSE reflects removals |
| 6 | `interface.py` | `start(..., initial_prices=None)`, `status()`, `normalize_ticker()` | Restart continuity; health visibility; one normalization rule |
| 7 | `simulator.py` | Single `np.random.Generator`, vectorized step, stable seeds for unknown tickers, `session_open` | Reproducible tests; no price jumps on re-add |
| 8 | `stream.py` | Router created per call, keep-alive ping, empty-map frame, `app.state` fallback | Test isolation; proxy-safe; correct removals |

Steps 1–3 are the critical path. Make them first.

---

## 12. Testing Plan

Tests live in `backend/tests/market/`. Run them with `uv run --extra dev pytest -v`.

### 12.1 Fake Massive client built on real models

Use real `TickerSnapshot.from_dict()` objects, not `MagicMock`. A `MagicMock` invents any attribute you ask for, which is exactly how the `.timestamp` bug got through.

```python
# tests/market/fakes.py
from massive.exceptions import BadResponse
from massive.rest.models import GroupedDailyAgg, TickerSnapshot

PLAN_403 = '{"status":"NOT_AUTHORIZED","message":"You are not entitled to this data. Please upgrade your plan"}'
AUTH_401 = '{"status":"ERROR","error":"Unknown API Key"}'


def snap(ticker, price=None, prev_close=None, day_close=None, ts_ns=1_763_000_000_000_000_000):
    d = {"ticker": ticker}
    if price is not None:
        d["lastTrade"] = {"p": price, "t": ts_ns}
    if prev_close is not None:
        d["prevDay"] = {"c": prev_close}
    if day_close is not None:
        d["day"] = {"c": day_close}
    return TickerSnapshot.from_dict(d)


class FakeClient:
    def __init__(self, prices=None, snapshot_error=None, grouped=None):
        self.prices = prices or {}           # ticker -> (price, prev_close)
        self.snapshot_error = snapshot_error  # str body to raise as BadResponse
        self.grouped = grouped or {}          # "YYYY-MM-DD" -> {ticker: close}
        self.calls = []

    def get_snapshot_all(self, market_type, tickers):
        self.calls.append(("all", tuple(tickers)))
        if self.snapshot_error:
            raise BadResponse(self.snapshot_error)
        return [snap(t, *self.prices[t]) for t in tickers if t in self.prices]

    def get_snapshot_ticker(self, market_type, ticker):
        self.calls.append(("one", ticker))
        if ticker not in self.prices:
            raise BadResponse('{"status":"NOT_FOUND"}')
        return snap(ticker, *self.prices[ticker])

    def get_grouped_daily_aggs(self, d, adjusted=True):
        self.calls.append(("grouped", d))
        return [GroupedDailyAgg(ticker=t, close=c, timestamp=0) for t, c in self.grouped.get(d, {}).items()]
```

### 12.2 Massive tests

```python
# tests/market/test_massive.py
from datetime import date, timedelta

import pytest
from massive.exceptions import BadResponse

from app.market.cache import PriceCache
from app.market.factory import start_market_data
from app.market.massive_client import MassiveDataSource, classify_error, extract_price

from .fakes import AUTH_401, PLAN_403, FakeClient, snap


def test_extract_price_uses_sip_timestamp_ns():
    assert extract_price(snap("AAPL", 190.5)) == (190.5, 1_763_000_000.0)


def test_extract_price_falls_back_to_bar_close():
    assert extract_price(snap("AAPL", prev_close=188.0)) == (188.0, None)
    assert extract_price(snap("AAPL")) is None


@pytest.mark.parametrize("body,kind", [(AUTH_401, "auth"), (PLAN_403, "plan"),
    ('{"status":"ERROR","error":"You\'ve exceeded the maximum requests per minute"}', "rate_limit")])
def test_classify_error(body, kind):
    assert classify_error(BadResponse(body)) == kind


async def test_live_mode_seeds_cache_with_session_open():
    cache = PriceCache()
    src = MassiveDataSource("k", cache, poll_interval=60, client=FakeClient({"AAPL": (190.5, 188.0)}))
    await src.start(["aapl"])
    try:
        u = cache.get("AAPL")
        assert src.status()["mode"] == "live"
        assert (u.price, u.session_open) == (190.5, 188.0)
    finally:
        await src.stop()


async def test_add_ticker_fetches_immediately_and_unknown_is_not_cached():
    cache, client = PriceCache(), FakeClient({"AAPL": (190.0, 189.0), "PYPL": (70.0, 69.0)})
    src = MassiveDataSource("k", cache, poll_interval=60, client=client)
    await src.start(["AAPL"])
    await src.add_ticker("pypl")
    await src.add_ticker("NOPE")
    assert cache.get_price("PYPL") == 70.0 and "NOPE" not in cache
    await src.stop()


async def test_free_plan_falls_back_to_eod():
    y1 = (date.today() - timedelta(days=1)).isoformat()
    y2 = (date.today() - timedelta(days=2)).isoformat()
    client = FakeClient(snapshot_error=PLAN_403,
                        grouped={y1: {"AAPL": 191.0, "PYPL": 70.0}, y2: {"AAPL": 189.0}})
    cache = PriceCache()
    src = MassiveDataSource("k", cache, client=client)
    await src.start(["AAPL"])
    assert src.status()["mode"] == "eod"
    assert cache.get("AAPL").price == 191.0 and cache.get("AAPL").session_open == 189.0
    await src.add_ticker("PYPL")  # served from bars already in memory
    assert cache.get_price("PYPL") == 70.0
    assert sum(1 for c in client.calls if c[0] == "grouped") == 2
    await src.stop()


async def test_bad_key_falls_back_to_simulator(monkeypatch):
    monkeypatch.setenv("MASSIVE_API_KEY", "bad")
    monkeypatch.setattr("app.market.massive_client.RESTClient",
                        lambda **kw: FakeClient(snapshot_error=AUTH_401))
    cache = PriceCache()
    src = await start_market_data(cache, ["AAPL"])
    assert src.status()["source"] == "simulator" and "AAPL" in cache
    await src.stop()


async def test_poll_error_keeps_last_price():
    client = FakeClient({"AAPL": (190.0, 189.0)})
    cache = PriceCache()
    src = MassiveDataSource("k", cache, poll_interval=60, client=client)
    await src.start(["AAPL"])
    client.snapshot_error = '{"status":"ERROR","error":"You\'ve exceeded the maximum requests per minute"}'
    await src._safe(src._poll_live)
    assert cache.get_price("AAPL") == 190.0
    assert src.status()["last_error"].startswith("rate_limit")
    await src.stop()
```

### 12.3 Simulator tests (new or updated)

```python
# tests/market/test_simulator.py (additions)
import numpy as np

from app.market.simulator import GBMSimulator


def test_reproducible_with_seed():
    a = GBMSimulator(["AAPL", "TSLA"], rng=np.random.default_rng(7))
    b = GBMSimulator(["AAPL", "TSLA"], rng=np.random.default_rng(7))
    assert [a.step() for _ in range(50)] == [b.step() for _ in range(50)]


def test_log_return_statistics():
    dt = 1 / 252
    sim = GBMSimulator(["AAPL"], dt=dt, event_probability=0, rng=np.random.default_rng(1))
    prices = [190.0] + [sim._prices["AAPL"] for _ in range(20_000) if sim.step()]
    r = np.diff(np.log(prices))
    mu, sigma = 0.05, 0.22
    assert abs(r.mean() - (mu - sigma**2 / 2) * dt) < 3 * sigma * np.sqrt(dt / len(r))
    assert abs(r.std() - sigma * np.sqrt(dt)) / (sigma * np.sqrt(dt)) < 0.03


def test_correlation_structure():
    sim = GBMSimulator(["AAPL", "MSFT", "JPM"], dt=1 / 252, event_probability=0,
                       rng=np.random.default_rng(3))
    rows = [sim.step() for _ in range(20_000)]
    lr = {t: np.diff(np.log([r[t] for r in rows])) for t in ("AAPL", "MSFT", "JPM")}
    assert abs(np.corrcoef(lr["AAPL"], lr["MSFT"])[0, 1] - 0.6) < 0.05
    assert abs(np.corrcoef(lr["AAPL"], lr["JPM"])[0, 1] - 0.3) < 0.05


def test_unknown_ticker_seed_is_stable_and_initial_prices_win():
    s1 = GBMSimulator(["PYPL"])
    s2 = GBMSimulator(["PYPL"])
    assert s1.get_price("PYPL") == s2.get_price("PYPL")
    assert GBMSimulator(["AAPL"], initial_prices={"aapl": 230.0}).get_price("AAPL") == 230.0


def test_events_always_move_at_least_two_percent():
    sim = GBMSimulator(["AAPL"], event_probability=1.0, rng=np.random.default_rng(0))
    p = sim.step()["AAPL"]
    assert abs(p / 190.0 - 1) >= 0.0199
```

### 12.4 Interface conformance (both sources)

```python
# tests/market/test_conformance.py
import pytest

from app.market.cache import PriceCache
from app.market.massive_client import MassiveDataSource
from app.market.simulator import SimulatorDataSource

from .fakes import FakeClient

PRICES = {t: (100.0 + i, 99.0) for i, t in enumerate(["AAPL", "MSFT", "TSLA"])}


@pytest.fixture(params=["sim", "massive"])
def make(request):
    def _make(cache):
        if request.param == "sim":
            return SimulatorDataSource(cache, update_interval=60, seed=1)
        return MassiveDataSource("k", cache, poll_interval=60, client=FakeClient(PRICES))
    return _make


async def test_lifecycle_contract(make):
    cache = PriceCache()
    src = make(cache)
    await src.start([" aapl ", "MSFT", "MSFT"])
    assert src.get_tickers() == ["AAPL", "MSFT"]   # normalized + de-duped
    assert "AAPL" in cache and "MSFT" in cache     # seeded before start() returns
    await src.add_ticker("tsla")
    await src.add_ticker("TSLA")
    assert src.get_tickers().count("TSLA") == 1 and "TSLA" in cache
    v = cache.version
    await src.remove_ticker("msft")
    await src.remove_ticker("MSFT")
    assert "MSFT" not in cache and cache.version > v
    assert set(src.status()) >= {"source", "mode", "tickers", "last_success", "last_error"}
    await src.stop()
    await src.stop()  # idempotent
```

### 12.5 Cache and SSE

```python
# tests/market/test_cache.py (additions)
from app.market.cache import PriceCache


def test_session_open_carries_forward_and_remove_bumps_version():
    c = PriceCache()
    c.update("AAPL", 190.0, session_open=189.0)
    c.update("AAPL", 191.0)
    assert c.get("AAPL").session_open == 189.0 and c.get("AAPL").previous_price == 190.0
    v = c.version
    c.remove("AAPL")
    assert c.version == v + 1
    c.remove("AAPL")
    assert c.version == v + 1  # no-op does not bump


async def test_sse_emits_only_on_change():
    from app.market.stream import generate_events

    class Req:
        client = None
        n = 0
        async def is_disconnected(self):
            self.n += 1
            return self.n > 4

    c = PriceCache()
    c.update("AAPL", 190.0)
    frames = [f async for f in generate_events(c, Req(), interval=0)]
    assert frames[0] == "retry: 1000\n\n"
    assert sum(f.startswith("data:") for f in frames) == 1  # version unchanged afterwards
```

### 12.6 Manual checks

```bash
cd backend && uv run market_data_demo.py
```

```bash
curl -N http://localhost:8000/api/stream/prices
```

```bash
MASSIVE_API_KEY=your_key uv run uvicorn app.main:app --port 8000
```

With a real key, check `/api/health`: a free key should report `"mode": "eod"`, and a paid key `"mode": "live"`.

---

## 13. Implementation Order

1. `models.py`, `cache.py`, `interface.py`, then update `tests/market/test_models.py` and `test_cache.py`.
2. `massive_client.py` and `tests/market/fakes.py` + `test_massive.py`. **This fixes the production bug.**
3. `simulator.py` + `seed_prices.fallback_seed_price`, then update the simulator tests. Existing tests that call `np.random.seed`/`random.seed` should pass `rng=np.random.default_rng(seed)` instead.
4. `factory.py`, `__init__.py`, `stream.py`, then `test_factory.py` and `test_conformance.py`.
5. Wire the lifespan, `/api/health` and the watchlist/trade helpers (§9) when the backend API is built.
6. Update `backend/CLAUDE.md` with the new exports (`start_market_data`, `normalize_ticker`, `status()`, `session_open`).
