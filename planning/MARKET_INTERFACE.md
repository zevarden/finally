# Market Data Interface — Unified Python API

How FinAlly gets stock prices from **one interface** that is backed by the **Massive API** when `MASSIVE_API_KEY` is set, or by the **built-in simulator** otherwise. Downstream code (SSE, portfolio, trades, LLM chat) never knows which source is active.

- Massive endpoint details: [MASSIVE_API.md](MASSIVE_API.md)
- Simulator internals: [MARKET_SIMULATOR.md](MARKET_SIMULATOR.md)
- Code: `backend/app/market/`

Status legend: **[built]** exists in `backend/app/market/` today · **[proposed]** recommended change, not yet implemented.

---

## 1. Design Goals

1. **Source-agnostic consumers.** Everything reads prices from one place, the `PriceCache`. Nothing calls Massive or the simulator directly to get a price.
2. **Push, not pull.** A source owns a background task that writes into the cache on its own cadence (500 ms for the simulator, 15 s or less for Massive). Consumers read the latest value in O(1) without waiting.
3. **One switch.** `MASSIVE_API_KEY` set and non-empty → Massive. Otherwise → simulator. No other config is required.
4. **Dynamic ticker set.** Watchlist add/remove propagates to the source at runtime.
5. **Degrade, don't crash.** Network errors, bad keys, rate limits or plan limits are logged and leave the last known prices in place. The app keeps serving.

## 2. Architecture

```
                 env MASSIVE_API_KEY
                         │
              create_market_data_source(cache)
                 ┌───────┴────────┐
                 ▼                ▼
       SimulatorDataSource   MassiveDataSource         ← MarketDataSource (ABC)
        (GBM, 500 ms)         (REST poll, 15 s)
                 └───────┬────────┘
                         │  cache.update(ticker, price, ts)
                         ▼
                    PriceCache  (thread-safe, versioned)
          ┌──────────────┼──────────────┬───────────────┐
          ▼              ▼              ▼               ▼
  /api/stream/prices  /api/portfolio  trade execution  LLM context
   (SSE, 500 ms)     (valuation)     (fill price)    (watchlist prices)
```

This is the **strategy pattern** (interchangeable sources) combined with a **shared cache** (one producer, many consumers).

---

## 3. Core Types

### 3.1 `PriceUpdate` — immutable price snapshot **[built]** (`models.py`)

```python
@dataclass(frozen=True, slots=True)
class PriceUpdate:
    ticker: str
    price: float            # rounded to 2 dp
    previous_price: float   # price at the previous update (tick-to-tick)
    timestamp: float        # Unix seconds

    @property
    def change(self) -> float: ...          # price - previous_price
    @property
    def change_percent(self) -> float: ...  # tick-to-tick %
    @property
    def direction(self) -> str: ...         # "up" | "down" | "flat"
    def to_dict(self) -> dict: ...          # SSE / JSON shape
```

This is the SSE payload shape the frontend uses: `{ticker, price, previous_price, timestamp, change, change_percent, direction}`.

**[proposed] Daily change reference.** PLAN.md asks the watchlist to show **daily change %**, but `change_percent` is tick-to-tick, which is a different number. Add an optional reference price:

```python
    session_open: float | None = None       # price at "start of day" for this ticker

    @property
    def day_change_percent(self) -> float | None:
        if not self.session_open:
            return None
        return round((self.price - self.session_open) / self.session_open * 100, 4)
```

- Simulator: `session_open` is the seed price at the moment the ticker was added (that is, "since app start").
- Massive: `session_open` is `snap.prev_day.close` from the snapshot, which matches the conventional definition of daily change.
- `PriceCache.update()` takes an optional `session_open=` and carries the previous value forward when it is omitted. `to_dict()` emits `session_open` and `day_change_percent`.

### 3.2 `MarketDataSource` — the abstract interface **[built]** (`interface.py`)

```python
class MarketDataSource(ABC):
    async def start(self, tickers: list[str]) -> None: ...   # begin background task; call once
    async def stop(self) -> None: ...                        # cancel task; idempotent
    async def add_ticker(self, ticker: str) -> None: ...     # no-op if present
    async def remove_ticker(self, ticker: str) -> None: ...  # no-op if absent; also evicts from cache
    def get_tickers(self) -> list[str]: ...                  # currently tracked set
```

The contract:

| Rule | Why |
|---|---|
| Sources **write** prices to the cache; they never return prices to callers | Keeps consumers decoupled from source timing |
| `start()` must leave the cache populated **before it returns**, when possible | So the first SSE frame and the first `/api/watchlist` call have data |
| `add_ticker()` should make a price available as soon as practical | The simulator does this immediately. Massive waits for the next poll, unless proposed §6.3 is adopted |
| `remove_ticker()` must call `cache.remove(ticker)` | Otherwise SSE keeps streaming a stale ticker |
| Exceptions inside the background loop are logged, never raised | One bad tick or poll must not kill the stream |
| Tickers are normalized to **uppercase, stripped** | Massive is case-sensitive, and the DB has a UNIQUE `(user_id, ticker)` constraint |

### 3.3 `PriceCache` — single source of truth **[built]** (`cache.py`)

```python
cache = PriceCache()
cache.update("AAPL", 190.12, timestamp=None) -> PriceUpdate   # computes previous/direction
cache.get("AAPL")        -> PriceUpdate | None
cache.get_price("AAPL")  -> float | None
cache.get_all()          -> dict[str, PriceUpdate]            # shallow copy
cache.remove("AAPL")
cache.version            -> int                               # bumps on every update
"AAPL" in cache; len(cache)
```

- A `threading.Lock` guards all access. This is safe even though the Massive client runs in a worker thread via `asyncio.to_thread`.
- `version` lets SSE skip a frame when nothing has changed, which matters on Massive where prices change every 15 s but SSE ticks every 500 ms.

### 3.4 Factory **[built]** (`factory.py`)

```python
def create_market_data_source(price_cache: PriceCache) -> MarketDataSource:
    api_key = os.environ.get("MASSIVE_API_KEY", "").strip()
    if api_key:
        return MassiveDataSource(api_key=api_key, price_cache=price_cache)
    return SimulatorDataSource(price_cache=price_cache)
```

**[proposed]** Also read `MASSIVE_POLL_INTERVAL` (float seconds, default `15.0`) and pass it as `poll_interval`, so paid-tier users can poll every 2–5 s without code changes. Add it to `.env.example` as optional.

---

## 4. Implementations

### 4.1 `SimulatorDataSource` **[built]**

A wrapper around `GBMSimulator`. Each tick (500 ms) it runs `step()` and writes every price to the cache. `start()` and `add_ticker()` seed the cache immediately. See MARKET_SIMULATOR.md.

### 4.2 `MassiveDataSource` **[built, needs fixes]**

```python
MassiveDataSource(api_key: str, price_cache: PriceCache, poll_interval: float = 15.0)
```

- `start()` creates the `RESTClient`, runs one immediate poll, then starts the `_poll_loop` task.
- Each poll makes **one** call, `client.get_snapshot_all(SnapshotMarketType.STOCKS, tickers=self._tickers)`, run in `asyncio.to_thread`.
- Failures (401/403/429/network) are logged and retried on the next interval.

**Required fixes (bugs found during API research, see MASSIVE_API.md §8):**

```python
# current (broken): LastTrade has no .timestamp, and the unit is ns, not ms
timestamp = snap.last_trade.timestamp / 1000.0

# fixed
def _extract(snap) -> tuple[float, float | None] | None:
    t = snap.last_trade
    if t is not None and t.price:
        ts = t.sip_timestamp / 1e9 if t.sip_timestamp else None
        return t.price, ts
    # overnight reset / no trades in plan → best available bar close
    for bar in (snap.min, snap.day, snap.prev_day):
        if bar is not None and bar.close:
            return bar.close, None
    return None
```

Update `tests/market/test_massive.py` to build real `TickerSnapshot.from_dict({...})` objects from sample JSON instead of `MagicMock`. A spec'd fake would have caught the `.timestamp` bug.

---

## 5. Lifecycle Wiring (FastAPI)

```python
# backend/app/main.py
from contextlib import asynccontextmanager
from fastapi import FastAPI
from app.market import PriceCache, create_market_data_source, create_stream_router

price_cache = PriceCache()
market_source = create_market_data_source(price_cache)

@asynccontextmanager
async def lifespan(app: FastAPI):
    tickers = db.get_watchlist_tickers(user_id="default")   # seeded with 10 defaults
    await market_source.start(tickers)
    app.state.price_cache = price_cache
    app.state.market_source = market_source
    yield
    await market_source.stop()

app = FastAPI(lifespan=lifespan)
app.include_router(create_stream_router(price_cache))
```

### Consumer patterns

```python
# Watchlist routes: keep DB and source in sync
@router.post("/api/watchlist")
async def add(body: AddTicker, request: Request):
    ticker = body.ticker.upper().strip()
    db.add_watchlist(ticker)
    await request.app.state.market_source.add_ticker(ticker)

@router.delete("/api/watchlist/{ticker}")
async def remove(ticker: str, request: Request):
    ticker = ticker.upper().strip()
    db.remove_watchlist(ticker)
    if not db.has_position(ticker):          # keep pricing held positions!
        await request.app.state.market_source.remove_ticker(ticker)

# Trade execution: fill at the cached price
price = request.app.state.price_cache.get_price(ticker)
if price is None:
    raise HTTPException(400, f"No price available for {ticker}")
```

> **Tracked set = watchlist ∪ held positions.** If a user removes a ticker from the watchlist but still holds shares, the source must keep pricing it, or portfolio valuation breaks. The watchlist routes (and the LLM's watchlist actions) own this rule. The market layer just tracks what it's told.

- **Trades** for a ticker not in the cache should first `add_ticker()` it (and add it to the watchlist), then read the price. With the simulator that price is available immediately. With Massive, poll once (see §6.3).
- **LLM context**: `cache.get_all()` gives live prices for the prompt.
- **Portfolio valuation / snapshots**: `cache.get_price(t)` per position. If it returns `None`, fall back to `avg_cost` and flag the value as stale.

### SSE **[built]** (`stream.py`)

`GET /api/stream/prices` sends `retry: 1000`, then every 500 ms, **if `cache.version` changed**, one event:

```
data: {"AAPL": {"ticker":"AAPL","price":190.12,"previous_price":190.05,"timestamp":1763000000.5,"change":0.07,"change_percent":0.0368,"direction":"up"}, "MSFT": {...}}
```

A whole-map payload per event keeps the client simple: `JSON.parse` once, then update each row. The `PriceUpdate` field names are the API contract with the frontend.

**[proposed]** Move `router = APIRouter(...)` inside `create_stream_router()`. As written, the router is module-level, so calling the factory twice (e.g. in tests) registers the route twice.

---

## 6. Free-Tier Handling **[proposed]**

On the free Massive Basic plan, the snapshot endpoint returns **403** (see MASSIVE_API.md §2). Today a free key gives an empty cache forever, so nothing is tradeable. Recommended behavior:

### 6.1 Startup capability probe

In `MassiveDataSource.start()`, make one snapshot call. Then:

| Result | Mode | Behavior |
|---|---|---|
| 200 | `live` | Poll snapshots every `poll_interval` |
| 403 `NOT_AUTHORIZED` | `eod` | Fetch **grouped daily** for the last completed trading day once (1 call for all tickers), seed the cache with closes, then refresh once per hour. Prices are static intraday. Log a clear warning: `"Massive key is on a plan without snapshots; using end-of-day closes"` |
| 401 | — | Log `"MASSIVE_API_KEY rejected"` and **fall back to `SimulatorDataSource`**, so the app still works |

### 6.2 Expose the mode

Add `MarketDataSource.status() -> dict` returning, for example, `{"source": "massive", "mode": "eod", "last_success": 1763000000.0, "last_error": None}`. Surface it in `/api/health` so the frontend can show "EOD data" or "Simulated" next to the connection dot.

### 6.3 Immediate price on `add_ticker`

For Massive, `add_ticker()` should fire a one-off `get_snapshot_ticker()` in live mode, or a `get_previous_close_agg()` in EOD mode, so a newly added ticker isn't blank for up to 15 s and a trade on it can fill right away. If that call shows the ticker is unknown (empty result), the route should return a 404-style error to the user or the LLM.

---

## 7. Testing the Interface

- **Conformance test** (parametrized over both sources, using a fake Massive client): `start` seeds the cache, `add_ticker` is idempotent, `remove_ticker` evicts from the cache, `stop` is idempotent, `get_tickers` reflects changes, and tickers are uppercased.
- **Factory**: env var unset, empty, or whitespace → simulator. Set → Massive.
- **Massive parsing**: real `TickerSnapshot.from_dict()` fixtures covering a normal case, `lastTrade` missing (fall back to bar close), and an unknown ticker omitted from the results.
- **Failure handling**: the fake client raises `BadResponse` with 403/429. The loop survives and the cache keeps its last values.
- **SSE**: no event when the version is unchanged; the payload matches `PriceUpdate.to_dict()`.

## 8. Summary of Proposed Changes

| # | Change | Why |
|---|---|---|
| 1 | `last_trade.sip_timestamp / 1e9`, plus bar-close fallback | **Bug** — Massive mode currently writes no prices |
| 2 | Massive tests use real model fixtures | Mocks hid bug #1 |
| 3 | Free-tier probe and EOD mode; 401 → simulator fallback | Free keys currently produce an empty cache |
| 4 | `session_open` and `day_change_percent` on `PriceUpdate` | PLAN.md requires daily change %, not tick change |
| 5 | `MASSIVE_POLL_INTERVAL` env var | Paid tiers can poll faster |
| 6 | Immediate fetch in `MassiveDataSource.add_ticker` | New tickers usable at once |
| 7 | `status()` surfaced in `/api/health` | User can see which data source is active |
| 8 | Router created inside `create_stream_router` | Avoid duplicate route registration |
