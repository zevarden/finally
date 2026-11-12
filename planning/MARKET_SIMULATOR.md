# Market Simulator — Approach and Code Structure

The default market data source when `MASSIVE_API_KEY` is not set. It produces realistic-looking, correlated, live stock prices entirely in-process, with no network, no API key and no cost. It implements the same `MarketDataSource` interface as the Massive client (see [MARKET_INTERFACE.md](MARKET_INTERFACE.md)), so the rest of the app can't tell the two apart.

Code: `backend/app/market/simulator.py` and `backend/app/market/seed_prices.py` · Tests: `backend/tests/market/test_simulator*.py`

Status legend: **[built]** implemented · **[proposed]** recommended, not yet implemented.

---

## 1. Requirements (from PLAN.md §6)

| Requirement | How it's met |
|---|---|
| Geometric Brownian Motion with per-ticker drift and volatility | `GBMSimulator.step()`, with `TICKER_PARAMS` |
| ~500 ms updates | `SimulatorDataSource(update_interval=0.5)` |
| Correlated moves (tech moves together) | Cholesky-factored sector correlation matrix |
| Occasional 2–5 % "events" for drama | Per-tick Bernoulli shock, `event_probability=0.001` |
| Realistic seed prices (AAPL ~$190 …) | `SEED_PRICES` |
| In-process background task | `asyncio.create_task(_run_loop())` |

## 2. The Model

### 2.1 Geometric Brownian Motion

Each tick, every price evolves by the exact GBM solution:

```
S(t+Δt) = S(t) · exp( (μ − σ²/2)·Δt  +  σ·√Δt·Z )
```

| Symbol | Meaning | Value |
|---|---|---|
| `μ` | annualized drift | 0.03–0.08 |
| `σ` | annualized volatility | 0.17–0.50 |
| `Δt` | tick length as a fraction of a trading year | `0.5 / (252 × 6.5 × 3600)` ≈ **8.48 × 10⁻⁸** |
| `Z` | standard normal, **correlated across tickers** | from Cholesky |

Why GBM:
- Prices **can never go negative** (exponential form).
- Moves are **proportional** to price: a $800 NVDA moves more in dollars than a $175 GOOGL, which looks right.
- The `−σ²/2` term (Itô correction) keeps the expected price growth equal to `μ` instead of drifting upward.
- It is the textbook model, which makes it easy for students to understand and test.

Treating the 500 ms tick as real trading time gives believable magnitudes:

| Ticker | σ | per-tick move | per-hour 1σ | per-day 1σ |
|---|---|---|---|---|
| V | 0.17 | 0.005 % (~$0.01) | 0.42 % | 1.07 % |
| AAPL | 0.22 | 0.006 % (~$0.01) | 0.54 % | 1.39 % |
| TSLA | 0.50 | 0.015 % (~$0.04) | 1.24 % | 3.15 % |

Drift is negligible at this timescale (`μ·Δt ≈ 4 × 10⁻⁹`). The visible action comes from `σ` and from events. The UI still gets a price flash on almost every tick, because nearly every tick changes the 2-dp price by at least a cent.

### 2.2 Correlated moves via Cholesky

Independent draws would make every ticker wander on its own, which doesn't look like a real market. Instead:

1. Build an `n × n` correlation matrix `C` from sector membership:

   | Pair | ρ |
   |---|---|
   | tech ↔ tech (AAPL, GOOGL, MSFT, AMZN, META, NVDA, NFLX) | **0.6** |
   | finance ↔ finance (JPM, V) | **0.5** |
   | anything ↔ TSLA | **0.3** (it does its own thing) |
   | cross-sector / unknown tickers | **0.3** |

2. Factor `C = L·Lᵀ` once (`np.linalg.cholesky`).
3. Each tick: `z = L @ standard_normal(n)` gives a vector of standard normals with exactly correlation `C`.

`L` is rebuilt only when tickers are added or removed. That costs O(n³), which is trivial for n < 50. The per-tick cost is one O(n²) matrix-vector product.

These matrices are always positive definite (they are block-equicorrelated with ρ ∈ (0, 1)), so `cholesky` can't fail for any watchlist the user builds.

### 2.3 Random events

After the GBM step, each ticker independently has probability `p = 0.001` of a shock:

```python
if random.random() < p:
    price *= 1 + random.choice([-1, 1]) * random.uniform(0.02, 0.05)
```

With 10 tickers at 2 ticks/s, that is 20 draws/s, so **about one event every 50 s** somewhere on the board. That's often enough for a demo to see a big red or green flash, and rare enough not to look broken. Shocks are idiosyncratic (not correlated), like a news headline.

## 3. Code Structure

```
backend/app/market/
├── seed_prices.py   # data only: SEED_PRICES, TICKER_PARAMS, DEFAULT_PARAMS,
│                    #            CORRELATION_GROUPS, *_CORR constants
├── simulator.py
│   ├── GBMSimulator         # pure math + state; synchronous; no asyncio, no cache
│   └── SimulatorDataSource  # MarketDataSource adapter: asyncio loop → PriceCache
├── interface.py     # MarketDataSource ABC
└── cache.py         # PriceCache
```

The split matters. `GBMSimulator` is a **pure, synchronous, deterministic-given-RNG** engine. You can unit-test the math by calling `step()` in a loop with no event loop, cache or sleeps. `SimulatorDataSource` is a thin adapter that handles scheduling and cache writes.

### 3.1 `seed_prices.py` **[built]**

```python
SEED_PRICES = {"AAPL": 190.0, "GOOGL": 175.0, "MSFT": 420.0, "AMZN": 185.0, "TSLA": 250.0,
               "NVDA": 800.0, "META": 500.0, "JPM": 195.0, "V": 280.0, "NFLX": 600.0}

TICKER_PARAMS = {
    "AAPL": {"sigma": 0.22, "mu": 0.05}, "GOOGL": {"sigma": 0.25, "mu": 0.05},
    "MSFT": {"sigma": 0.20, "mu": 0.05}, "AMZN":  {"sigma": 0.28, "mu": 0.05},
    "TSLA": {"sigma": 0.50, "mu": 0.03}, "NVDA":  {"sigma": 0.40, "mu": 0.08},
    "META": {"sigma": 0.30, "mu": 0.05}, "JPM":   {"sigma": 0.18, "mu": 0.04},
    "V":    {"sigma": 0.17, "mu": 0.04}, "NFLX":  {"sigma": 0.35, "mu": 0.05},
}
DEFAULT_PARAMS = {"sigma": 0.25, "mu": 0.05}          # for user-added tickers

CORRELATION_GROUPS = {"tech": {"AAPL", "GOOGL", "MSFT", "AMZN", "META", "NVDA", "NFLX"},
                      "finance": {"JPM", "V"}}
INTRA_TECH_CORR, INTRA_FINANCE_CORR, CROSS_GROUP_CORR, TSLA_CORR = 0.6, 0.5, 0.3, 0.3
```

Tuning lives here, as data, so nobody has to touch the math to change behavior.

### 3.2 `GBMSimulator` **[built]**

```python
class GBMSimulator:
    TRADING_SECONDS_PER_YEAR = 252 * 6.5 * 3600
    DEFAULT_DT = 0.5 / TRADING_SECONDS_PER_YEAR

    def __init__(self, tickers: list[str], dt: float = DEFAULT_DT,
                 event_probability: float = 0.001) -> None: ...

    def step(self) -> dict[str, float]:          # hot path, every 500 ms
        z = np.random.standard_normal(n)
        z = self._cholesky @ z if self._cholesky is not None else z
        for i, t in enumerate(self._tickers):
            mu, sigma = self._params[t]["mu"], self._params[t]["sigma"]
            self._prices[t] *= math.exp((mu - 0.5 * sigma**2) * self._dt
                                        + sigma * math.sqrt(self._dt) * z[i])
            if random.random() < self._event_prob:
                self._prices[t] *= 1 + random.choice([-1, 1]) * random.uniform(0.02, 0.05)
            result[t] = round(self._prices[t], 2)
        return result

    def add_ticker(self, ticker: str) -> None: ...     # seed + params, rebuild Cholesky
    def remove_ticker(self, ticker: str) -> None: ...  # drop state, rebuild Cholesky
    def get_price(self, ticker: str) -> float | None: ...
    def get_tickers(self) -> list[str]: ...

    # internals
    def _add_ticker_internal(self, ticker: str) -> None: ...   # no rebuild (batch init)
    def _rebuild_cholesky(self) -> None: ...                    # n<=1 → None
    @staticmethod
    def _pairwise_correlation(t1: str, t2: str) -> float: ...
```

Notes:
- Internal prices keep **full float precision**. Only the returned and cached values are rounded to cents, so rounding error never compounds.
- Unknown tickers (for example `PYPL` added via chat) get `DEFAULT_PARAMS`, a seed of `uniform(50, 300)`, and cross-group correlation 0.3.

### 3.3 `SimulatorDataSource` **[built]**

```python
class SimulatorDataSource(MarketDataSource):
    def __init__(self, price_cache: PriceCache, update_interval: float = 0.5,
                 event_probability: float = 0.001) -> None: ...

    async def start(self, tickers):      # build GBMSimulator, seed cache, spawn loop
    async def stop(self):                # cancel task, swallow CancelledError
    async def add_ticker(self, ticker):  # sim.add_ticker + seed cache immediately
    async def remove_ticker(self, ticker):  # sim.remove_ticker + cache.remove
    def get_tickers(self): ...

    async def _run_loop(self):
        while True:
            try:
                for t, p in self._sim.step().items():
                    self._cache.update(ticker=t, price=p)
            except Exception:
                logger.exception("Simulator step failed")   # never kill the stream
            await asyncio.sleep(self._interval)
```

Each step takes microseconds for 10 tickers, so running it directly on the event loop is fine. No thread is needed.

## 4. Data Flow per Tick

```
asyncio loop (every 0.5 s)
  └─ GBMSimulator.step()
       ├─ z ~ N(0, I)  →  L @ z         (correlate)
       ├─ S *= exp(drift + σ√Δt·z_i)    (GBM)
       └─ maybe shock ±2–5 %            (event)
  └─ PriceCache.update(t, p)  ×n        (previous_price, direction, version++)
SSE generator (every 0.5 s, own loop)
  └─ version changed? → data: {all PriceUpdate.to_dict()}
```

The simulator loop and the SSE loop run independently, both at 500 ms. Because the SSE side uses version checks, a slight phase drift between them never produces duplicate or empty frames.

## 5. Testing Approach **[built]**, extend as needed

| Test | Technique |
|---|---|
| Prices always positive, rounded to 2 dp | run 10k steps |
| GBM statistics | `np.random.seed`, `event_probability=0`, large `dt`; mean and variance of log returns ≈ `(μ−σ²/2)Δt` and `σ²Δt` |
| Correlation | sample many steps; empirical corr(AAPL, MSFT) ≈ 0.6, corr(AAPL, JPM) ≈ 0.3 |
| Events | `event_probability=1.0` → every tick moves at least 2 % |
| add/remove | Cholesky shape tracks n; idempotent; unknown ticker gets default params |
| n = 0 / n = 1 | `step()` returns `{}` / no Cholesky |
| Source adapter | `start` seeds cache before the first tick; `remove_ticker` evicts; `stop` idempotent |

Seed with `np.random.seed(...)` **and** `random.seed(...)`, because the module uses both RNGs.

## 6. Proposed Improvements **[proposed]**

These are small, optional refinements. None of them change the interface.

1. **Single RNG.** Replace the `random` and global `np.random` calls with one `np.random.Generator` passed to the constructor (`rng: np.random.Generator | None = None`). Tests then get exact reproducibility from one seed, and parallel tests can't interfere through global state.
2. **Stable seeds for unknown tickers.** Derive the seed price from a hash of the ticker (for example `50 + (crc32(ticker) % 250)`) instead of `uniform(50, 300)`. Then `PYPL` gets the same price after a restart or after being removed and re-added, instead of jumping.
3. **Resume from last known price on restart.** The simulator restarts from `SEED_PRICES`, but positions (`avg_cost`) persist in SQLite, so after a container restart a position bought at $230 suddenly shows a large P&L jump. Option: on `start()`, accept an optional `initial_prices: dict[str, float]` (for example the last trade price per held ticker), with seeds as the fallback.
4. **`session_open` for daily change %.** Record each ticker's starting price and pass it to the cache (see MARKET_INTERFACE.md §3.1), so the watchlist's "daily change %" means "since session start" rather than tick-to-tick.
5. **Mean-reverting volatility "regimes" (stretch).** Occasionally scale σ by 1.5–2× for a minute to create visibly calmer and choppier periods. This is purely cosmetic and not needed for MVP.
