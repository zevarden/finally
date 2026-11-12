# FinAlly Backend

FastAPI backend for the FinAlly AI Trading Workstation.

## Structure

- `app/` - Application code
  - `market/` - Market data subsystem (see `CLAUDE.md` for the API)
    - `models.py` - `PriceUpdate` dataclass (tick and daily change)
    - `cache.py` - Thread-safe, versioned price cache
    - `interface.py` - `MarketDataSource` ABC + `normalize_ticker()`
    - `simulator.py` - Correlated GBM simulator (default source)
    - `massive_client.py` - Massive/Polygon.io REST source (live + end-of-day modes)
    - `factory.py` - Source selection from env vars, `start_market_data()` with fallback
    - `stream.py` - SSE endpoint `GET /api/stream/prices`
    - `seed_prices.py` - Seed prices, GBM parameters, correlation groups

- `tests/` - Unit and integration tests
  - `market/` - Market data tests (`fakes.py` holds a fake Massive client built on real models)

## Running Tests

```bash
uv sync --extra dev                       # install, including test/lint tools
uv run --extra dev pytest -v              # all tests
uv run --extra dev pytest --cov=app       # with coverage
uv run --extra dev ruff check app tests   # lint
uv run --extra dev ruff format app tests  # format
```

## Environment Variables

| Variable | Default | Meaning |
|---|---|---|
| `MASSIVE_API_KEY` | empty | Set -> real data from Massive. Empty -> simulator. A rejected key falls back to the simulator |
| `MASSIVE_POLL_INTERVAL` | `15` | Seconds between Massive snapshot polls (clamped to >= 1) |
| `MARKET_SIM_SEED` | unset | Integer seed for deterministic simulator prices |

## Demo

```bash
uv run --extra dev market_data_demo.py   # live terminal dashboard of simulated prices
```
