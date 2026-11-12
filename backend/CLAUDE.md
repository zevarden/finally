# Backend — Developer Guide

## Project Setup

```bash
cd backend
uv sync --extra dev   # Install all dependencies including test/lint tools
```

## Market Data API

The market data subsystem lives in `app/market/`. Full design: `planning/MARKET_DATA_DESIGN.md`. Import only from the package:

```python
from app.market import (
    PriceCache, PriceUpdate, MarketDataSource, normalize_ticker,
    create_market_data_source, start_market_data, create_stream_router,
)
```

### Rules

1. **Consumers read only the `PriceCache`.** Never call the simulator or Massive for a price.
2. **Normalize tickers** with `normalize_ticker()` (strip + upper) at API boundaries. Sources also normalize.
3. **The tracked set is watchlist ∪ held positions.** The API layer decides which tickers to add or remove; the market layer tracks what it is told.

### Core Types

- **`PriceUpdate`**: an immutable dataclass with `ticker`, `price`, `previous_price`, `timestamp` (Unix seconds) and `session_open`. Its properties:
  - `change`, `change_percent`, `direction` (`"up"`/`"down"`/`"flat"`): tick-to-tick, used for the flash animation.
  - `day_change`, `day_change_percent`: change since `session_open`, shown as the watchlist's "daily change %". Both are `None` if unknown.
  - `to_dict()` is the JSON/SSE contract (all ten fields).

- **`PriceCache`** is a thread-safe in-memory store:
  - `update(ticker, price, timestamp=None, session_open=<carry forward>) -> PriceUpdate`
  - `get(ticker)`, `get_price(ticker)`, `get_all()` (a copy), `remove(ticker)`, `len()`, `in`
  - `version`: a monotonic counter, bumped on every update **and** removal (used by SSE change detection).

- **`MarketDataSource`** is the ABC that both sources implement: `start(tickers, initial_prices=None)`, `add_ticker()`, `remove_ticker()`, `get_tickers()`, `status()`, `stop()`.
  - `start()` seeds the cache before it returns.
  - `add_ticker()` prices the new ticker immediately. In Massive mode an unknown ticker is simply never cached; treat `ticker not in cache` after `add_ticker()` as a 404.
  - `remove_ticker()` always evicts the ticker from the cache.
  - `status()` returns a dict like `{"source": "simulator"|"massive", "mode": "simulated"|"live"|"eod", "tickers", "last_success", "last_error"}`, intended for `/api/health`.

- **`start_market_data(cache, tickers, initial_prices=None)`** is async and is the recommended entry point. It creates and starts the source chosen from env vars. A rejected Massive key falls back to the simulator.
- **`create_market_data_source(cache)`** returns an unstarted source: `MassiveDataSource` if `MASSIVE_API_KEY` is set, otherwise `SimulatorDataSource`.

### Sources

- **Simulator**: correlated GBM ticking every 500 ms, with random 2–5 % events. `initial_prices` lets it resume near the last trade prices after a restart. `session_open` is the price when the ticker started being simulated.
- **Massive** probes the API at start:
  - Paid key: **live** mode, one snapshot call per `MASSIVE_POLL_INTERVAL` (default 15 s). `session_open` is the previous day's close.
  - Free/Basic key (403 on snapshots): **eod** mode, grouped-daily closes refreshed hourly.
  - Bad key (401): raises `MassiveAuthError`, and `start_market_data()` falls back to the simulator.

### App wiring (sketch)

```python
@asynccontextmanager
async def lifespan(app):
    cache = PriceCache()
    app.state.price_cache = cache
    app.state.market_source = await start_market_data(cache, tracked_tickers, initial_prices=last_prices)
    yield
    await app.state.market_source.stop()

app = FastAPI(lifespan=lifespan)
app.include_router(create_stream_router())  # reads app.state.price_cache per request
```

### SSE Streaming

`GET /api/stream/prices` (text/event-stream). It sends `retry: 1000`, then one `data:` frame holding the **full map** `{ticker: PriceUpdate.to_dict()}` each time the cache changes (checked every 500 ms). `data: {}` means no tickers are tracked. A `: ping` comment follows 15 s without data. Clients should replace their whole price state with each frame.

### Environment Variables

| Variable | Default | Meaning |
|---|---|---|
| `MASSIVE_API_KEY` | empty | Set → Massive; empty → simulator |
| `MASSIVE_POLL_INTERVAL` | `15` | Seconds between live polls (≥ 1) |
| `MARKET_SIM_SEED` | unset | Integer seed for deterministic simulator prices |

### Seed Data

Default tickers: AAPL, GOOGL, MSFT, AMZN, TSLA, NVDA, META, JPM, V, NFLX. Seed prices, per-ticker volatility/drift and correlation groups are in `app/market/seed_prices.py`. Unknown tickers get a stable seed price in [50, 300) from `fallback_seed_price()`.

## Running Tests

```bash
uv run --extra dev pytest -v              # All tests
uv run --extra dev pytest --cov=app       # With coverage
uv run --extra dev ruff check app/ tests/ # Lint
```

Massive tests use `tests/market/fakes.py`, which builds **real** `massive` model objects. Don't use `MagicMock` for API responses: it accepts any attribute name and hides field-name bugs.

## Demo

```bash
uv run --extra dev market_data_demo.py   # Live terminal dashboard with simulated prices
```
