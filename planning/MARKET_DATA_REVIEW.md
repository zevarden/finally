# Market Data Backend — Code Review

**Date:** 2026-11-12
**Scope:** `backend/app/market/` (9 modules), `backend/tests/market/` (6 test files), `backend/market_data_demo.py`, `backend/pyproject.toml`. Checked against `PLAN.md` §6/§10, `MARKET_INTERFACE.md`, `MARKET_SIMULATOR.md`, `MASSIVE_API.md` and `MARKET_DATA_DESIGN.md`.
**Environment:** Python 3.13.17, massive 2.2.0, fastapi 0.128.7, numpy 2.4.2, pytest 9.0.2, pytest-asyncio 1.3.0.

---

## 1. Summary

The simulator path works and is good quality. The GBM math is correct, the correlation structure checks out empirically, the cache is thread-safe, and the SSE endpoint streams correctly when served by uvicorn. All 73 tests pass, ruff is clean, and line coverage is 91 %.

**The Massive (real data) path does not work.** It never writes a price. The tests pass only because they use `MagicMock` snapshots, which accept the wrong attribute name.

**The code has not been updated to `MARKET_DATA_DESIGN.md`.** It is still the version that the design document calls "the current code". None of the 8 changes in design §11 are in place: no `session_open`/daily change %, no `status()`, no `normalize_ticker()`, no `start_market_data()` fallback, no EOD mode, no keep-alive. The design is the right fix for nearly every finding below.

**Verdict:** OK for simulator-only development of the rest of the backend. **Not ready** for real market data, and not yet meeting the PLAN requirement for "daily change %" in the watchlist. Implement design §11 steps 1–3 (Massive) and step 5 (models/cache) before the API and frontend agents build on the contract.

| Severity | Count |
|---|---|
| Critical | 2 |
| High | 4 |
| Medium | 7 |
| Low | 7 |

---

## 2. Test Results

```
$ cd backend && uv run --extra dev pytest -q --cov=app --cov-report=term-missing
73 passed in 3.12s

Name                           Stmts   Miss  Cover   Missing
app/market/cache.py               39      0   100%
app/market/factory.py             15      0   100%
app/market/interface.py           13      0   100%
app/market/massive_client.py      67      4    94%   85-87, 125
app/market/models.py              26      0   100%
app/market/simulator.py          139      3    98%   149, 268-269
app/market/stream.py              36     24    33%   26-48, 62-87
TOTAL                            349     31    91%

$ uv run --extra dev ruff check app/ tests/
All checks passed!
```

### Extra checks run for this review

Each check was run as a separate script against the real code (not part of the test suite):

| # | Check | Result |
|---|---|---|
| 1 | `MassiveDataSource._poll_once()` with **real** `TickerSnapshot.from_dict()` objects | **Cache stays empty (0 tickers)** — `AttributeError: 'LastTrade' object has no attribute 'timestamp'`, caught and logged for every ticker |
| 2 | `create_stream_router()` called twice | Returns the **same** router object with `/api/stream/prices` registered **twice** |
| 3 | `PriceCache.remove()` bumps `version` | **No** |
| 4 | SSE generator with an empty cache | Sends only `retry:`; no `data:` frame |
| 5 | `SimulatorDataSource.start(["aapl"])` then `add_ticker("AAPL")` | Tracks **both** `aapl` and `AAPL`; `aapl` gets a random price (254.34) |
| 6 | `GBMSimulator._params["AAPL"] is TICKER_PARAMS["AAPL"]` | **True** (shared mutable dict) |
| 7 | Unknown ticker removed and re-added | Price jumps 189.92 → 281.24 |
| 8 | GBM statistics, 20 000 steps at dt = 1/252, no events | Annualized σ 0.220 (expected 0.22). Correlations: AAPL–MSFT 0.598 (0.6), AAPL–JPM 0.290 (0.3), TSLA–AAPL 0.300 (0.3). Mean log return within ~2 standard errors of (μ − σ²/2)·dt |
| 9 | 200 real-speed ticks of AAPL | 63/199 ticks unchanged after rounding to cents (expected: σ·√dt·S ≈ $0.012 per tick) |
| 10 | `cache.update(..., timestamp=0)` | Stored as the current time, not 0 |
| 11 | Live SSE: uvicorn + simulator + `curl -N /api/stream/prices` | `200`, `text/event-stream`, correct headers, `retry: 1000`, then a full-map `data:` frame every ~500 ms |

---

## 3. Findings

### Critical

**C1. Massive mode never writes a price** — `massive_client.py:103`
`snap.last_trade.timestamp` does not exist. The massive 2.2.0 `LastTrade` model has `sip_timestamp`, `participant_timestamp` and `trf_timestamp`, all in **nanoseconds**. The `AttributeError` is caught per snapshot (line 110), so every ticker is skipped with a warning and the cache stays empty. With `MASSIVE_API_KEY` set, the app has no prices: no SSE data, no trades, no portfolio valuation. Confirmed in check #1.
*Fix:* design §6.3 `extract_price()`: use `last_trade.sip_timestamp / 1e9`, with fallbacks to `min.close` → `day.close` → `prev_day.close`. Without those fallbacks, `last_trade` is empty after the overnight snapshot reset.

**C2. The tests encode the bug instead of catching it** — `tests/market/test_massive.py:11-18, 95-112`
`_make_snapshot()` builds a `MagicMock` and sets `last_trade.timestamp` in **milliseconds**. `test_timestamp_conversion` then asserts the wrong conversion (`/1000`). A `MagicMock` accepts any attribute name, which is how C1 went unnoticed. A green suite gives false confidence about the Massive path.
*Fix:* design §12.1 `fakes.py`: build real `TickerSnapshot.from_dict()` objects and inject a fake client, not `patch.object(_fetch_snapshots)`.

### High

**H1. A free (Basic) Massive key gets no prices at all** — `massive_client.py`
Snapshots return 403 `NOT_AUTHORIZED` on the free plan (MASSIVE_API.md). The poller logs an error every 15 s forever and the cache stays empty. A bad key (401) behaves the same way, and nothing falls back to the simulator. This goes against PLAN §6 ("Free tier … poll every 15 seconds") and design R4/R9.
*Fix:* design §6: a capability probe in `start()`, EOD mode via grouped-daily on 403, `MassiveAuthError` on 401, and `start_market_data()` falling back to the simulator.

**H2. No daily change % anywhere** — `models.py`, `cache.py`
PLAN §10 asks the watchlist for "daily change %". `PriceUpdate` only has tick-to-tick `change`/`change_percent`, which are about 0.005 % per tick in the simulator and useless as a daily figure. The frontend contract is not final until this field exists.
*Fix:* design §4.1/§4.2: `session_open`, `day_change`, `day_change_percent`, carried forward in `cache.update()`.

**H3. A ticker removed mid-poll comes back as a ghost** — `massive_client.py:97-108`
`_poll_once` writes every snapshot it receives. If `remove_ticker()` runs while the thread is fetching, the old snapshot is written back into the cache after the removal. The next poll no longer asks for it, so the ticker stays in the cache and in every SSE frame, frozen, until restart.
*Fix:* skip snapshots whose ticker is no longer in `self._tickers` (design `_write_snapshot`), and pass a **copy** of the ticker list to the thread (today `self._tickers` is passed by reference to `get_snapshot_all`).

**H4. Router registered twice on repeated calls** — `stream.py:17-26`
`router` is a module-level global, and each `create_stream_router()` call adds another `/prices` route to it. Every call after the first also binds the route to the *new* cache, while the first route (which wins) keeps the old one. This breaks test isolation and any app factory. Confirmed in check #2.
*Fix:* create the `APIRouter` inside the factory (design §8).

### Medium

**M1. Removing a ticker isn't reflected in SSE** — `cache.py:59-62`, `stream.py:80`
`remove()` doesn't bump `version`, and the generator skips empty maps. In simulator mode the next tick hides this. In Massive mode a removed ticker stays on the client for up to 15 s (live) or forever (if it was the last ticker). Confirmed in checks #3 and #4.
*Fix:* bump `version` on remove when something was removed, and send `data: {}` when the map is empty.

**M2. No SSE keep-alive** — `stream.py`
In Massive mode, `version` changes only every 15 s (or hourly in EOD), and nothing is sent in between. Proxies with idle timeouts (App Runner, Render, nginx defaults of 60 s) will drop the connection, so the frontend's status dot flaps.
*Fix:* send `: ping\n\n` after 15 s without data.

**M3. Ticker normalization is inconsistent** — `simulator.py:219-255`, `massive_client.py:41-43`
The Massive source normalizes in `add_ticker`/`remove_ticker` but **not** in `start()`. The simulator normalizes nowhere, so `aapl` and `AAPL` are tracked separately and `aapl` gets a random seed price (check #5).
*Fix:* a single `normalize_ticker()` in `interface.py`, used at every public entry point, with de-duplication in `start()`.

**M4. Massive client retries burn the free-tier quota** — `massive_client.py:42`
`RESTClient` defaults to `retries=3`, with backoff on 429. On a 5 calls/min plan, one rate-limited poll can use the whole minute's budget.
*Fix:* `RESTClient(api_key=..., retries=0)`; the poll loop already retries.

**M5. Poll interval is not configurable** — `factory.py:28`
`MassiveDataSource` is always built with the 15 s default. PLAN §6 says "configurable interval" and paid tiers should poll every 2–5 s.
*Fix:* `MASSIVE_POLL_INTERVAL` env var, clamped to ≥ 1 s (design §3/§7).

**M6. SSE has almost no test coverage** — `stream.py` at 33 %
Neither the route nor the generator is tested: no tests for framing, version-based skipping, disconnect handling or headers. Check #11 confirms it works today, but nothing guards it against regressions.
*Fix:* design §12.5 generator test, plus one `httpx.AsyncClient`/`TestClient` test for the route.

**M7. No interface conformance test**
PLAN §12 asks that "both implementations conform to the abstract interface". There is no shared contract test run against both sources, which is how M3 and the `add_ticker`-before-`start()` mismatch (L5) got in.
*Fix:* design §12.4 `test_conformance.py`, parametrized over both sources.

### Low

**L1. The simulator's RNG is global and can't be seeded per instance** — `simulator.py:84, 105-107, 151`
The simulator uses `np.random` and `random` module state. Tests can't be reproduced without seeding global state, and parallel tests interfere. There is no `MARKET_SIM_SEED` for deterministic E2E runs.
*Fix:* inject an `np.random.Generator` (design §5.3).

**L2. Unknown-ticker seed price is random each time** — `simulator.py:151`
A removed and re-added ticker jumps to a new price (check #7), and so does every ticker on restart.
*Fix:* `fallback_seed_price()` based on CRC32.

**L3. Shared mutable params** — `simulator.py:152`
`TICKER_PARAMS.get(ticker, dict(DEFAULT_PARAMS))` copies only the default. Known tickers share the module-level dict (check #6), so a future per-instance tweak would leak across instances.
*Fix:* `dict(TICKER_PARAMS.get(ticker, DEFAULT_PARAMS))`.

**L4. `timestamp=0` is treated as missing** — `cache.py:30`
`timestamp or time.time()`; use `time.time() if timestamp is None else timestamp`. Check #10.

**L5. `add_ticker()` before `start()` behaves differently per source**
The simulator silently does nothing. Massive appends the ticker. The interface docstring doesn't say which is right.

**L6. `CancelledError` swallowed in the SSE generator** — `stream.py:86`
Suppressing cancellation is an asyncio anti-pattern. Log it in a `finally` block and let the error propagate.

**L7. Packaging and test hygiene**
- `rich` is a runtime dependency but only `market_data_demo.py` uses it. Move it to `dev`, or to a `demo` extra, to keep the Docker image lean.
- `tests/conftest.py` overrides `event_loop_policy` with `asyncio.DefaultEventLoopPolicy`, which is deprecated in Python 3.14 and unnecessary with `asyncio_mode = "auto"`. Delete it.
- Redundant `@pytest.mark.asyncio` class markers alongside `asyncio_mode = "auto"`.
- The Dockerfile plan targets Python 3.12, but the local venv runs 3.13 and there is no `.python-version` pin.
- Timing-based tests (`asyncio.sleep(0.05)` then asserting `version > initial + 2`) could flake on a loaded CI runner.

---

## 4. What Is Good

- **Clean architecture.** Every consumer reads the cache and sources push into it. One ABC, a small surface, and a factory with a single switch. This matches PLAN §6 exactly.
- **GBM is correct:** exact log-normal step with Itô correction, Cholesky-correlated normals and a positive-definite block correlation matrix. Volatility and correlations reproduce empirically to within ±0.01 (check #8). Prices can't go negative.
- **Sensible defaults:** dt calibrated to 500 ms of trading time, events at about one per 50 s across 10 tickers, and realistic seeds.
- **Thread-safe cache** with `threading.Lock`, which is correct given the `to_thread` writer. `get_all()` returns a copy, and the version counter lets SSE skip idle frames.
- **The sync Massive client runs in `asyncio.to_thread`**, so it never blocks the event loop. One API call per poll, whatever the watchlist size.
- **SSE is correct on the wire:** `retry: 1000`, `X-Accel-Buffering: no`, full-map frames (so the client can replace state), and disconnect detection.
- **Resilience:** both background loops catch exceptions, so one bad tick or poll never kills the task. `stop()` is idempotent.
- **Readable code:** small modules, clear docstrings, ruff-clean, and a useful terminal demo.

---

## 5. Documentation Notes

- `CLAUDE.md` says the market data component "is summarized in `planning/MARKET_DATA_SUMMARY.md` with more details in the `planning/archive` folder". **Neither exists** in the current tree. Either restore them or point `CLAUDE.md` at `MARKET_DATA_DESIGN.md` and this review.
- `backend/CLAUDE.md` correctly describes the *current* API. It will need the updates in design §13 step 6 once the design is implemented.
- `MARKET_INTERFACE.md` uses **[built]** / **[proposed]** tags. Those tags are accurate: everything marked proposed is still unbuilt.
- **Security (outside market-data scope):** `planning/PLAN.md` §5 contains what looks like a real `LITELLM_MASTER_KEY` value, and PLAN.md is committed. Rotate the key and replace it with a placeholder; real keys belong only in the gitignored `.env`.

---

## 6. Recommended Order of Work

1. **C1 + C2 + H1 + H3 + M4:** rewrite `massive_client.py` per design §6.3 and add `tests/market/fakes.py` with real models (design §12.1–12.2). This makes real data usable.
2. **H2 + M1 + L4:** `models.py`/`cache.py` per design §4. This settles the SSE/frontend contract (`session_open`, `day_change_percent`) before the frontend agent starts.
3. **H4 + M2 + M6:** `stream.py` per design §8, plus SSE tests.
4. **M3 + M5 + M7 + L1–L3, L5:** `normalize_ticker()`, `status()`, `start_market_data()`, factory env vars, the simulator RNG refactor, and the conformance test.
5. **L6, L7**, and the documentation fixes in §5.

All of these fixes are already specified as drop-in code in `MARKET_DATA_DESIGN.md`. No new design work is needed.

---

## 7. Resolution (2026-11-12)

All findings were addressed on branch `market-data-fixes`, following `MARKET_DATA_DESIGN.md`.

| Finding | Status | Where |
|---|---|---|
| C1 `last_trade.timestamp` | Fixed: `extract_price()` reads `sip_timestamp / 1e9`, with fallbacks to minute/day/prev-day bars | `massive_client.py` |
| C2 MagicMock tests | Fixed: `tests/market/fakes.py` builds real `TickerSnapshot` / `GroupedDailyAgg` objects | `test_massive.py` |
| H1 free/bad key | Fixed: start-up probe; 403 → `eod` mode (grouped-daily, hourly); 401 → `MassiveAuthError` → `start_market_data()` falls back to the simulator | `massive_client.py`, `factory.py` |
| H2 daily change % | Fixed: `session_open`, `day_change`, `day_change_percent` (carried forward in the cache) | `models.py`, `cache.py` |
| H3 ghost ticker | Fixed: snapshots for removed tickers are dropped; the thread gets a copy of the ticker list | `massive_client.py` |
| H4 duplicate route | Fixed: router created per call; cache read from `app.state` when not injected | `stream.py` |
| M1 removals in SSE | Fixed: `remove()` bumps `version`; empty map sent as `data: {}` | `cache.py`, `stream.py` |
| M2 keep-alive | Fixed: `: ping` after 15 s idle | `stream.py` |
| M3 normalization | Fixed: `normalize_ticker()` used at every entry point, de-duplicated in `start()` | all sources |
| M4 client retries | Fixed: `RESTClient(retries=0)` | `massive_client.py` |
| M5 poll interval | Fixed: `MASSIVE_POLL_INTERVAL` (clamped to ≥ 1 s); also added `MARKET_SIM_SEED` | `factory.py` |
| M6 SSE coverage | Fixed: `stream.py` now at 100 % | `test_stream.py` |
| M7 conformance | Fixed: contract tests parametrized over both sources | `test_conformance.py` |
| L1 global RNG | Fixed: injected `np.random.Generator`, vectorized step | `simulator.py` |
| L2 random fallback seed | Fixed: stable `fallback_seed_price()` based on CRC32 | `seed_prices.py` |
| L3 shared params | Fixed: params copied per instance | `simulator.py` |
| L4 `timestamp=0` | Fixed: `is None` check | `cache.py` |
| L5 add before start | Fixed: both sources queue the ticker and include it in `start()` | both sources |
| L6 `CancelledError` swallowed | Fixed: logged in `finally`; cancellation propagates | `stream.py` |
| L7 hygiene | Fixed: `rich` moved to the `dev` extra, `massive>=2.0.0`, deprecated `event_loop_policy` fixture and redundant asyncio markers removed, timing tests poll instead of fixed sleeps. **Not done:** a `.python-version` pin for 3.12. The sandbox can't download 3.12, and the pin would break local runs; `requires-python >=3.12` and the Dockerfile's base image enforce the version instead | `pyproject.toml`, tests |
| Docs | Fixed: root `CLAUDE.md` points at existing docs, `backend/CLAUDE.md` + `README.md` rewritten, `.env.example` added | — |
| Leaked key | Replaced with a placeholder in `PLAN.md`. **The key must still be rotated:** it remains in git history | `planning/PLAN.md` |

**Results after the fixes:** 129 tests pass (previously 73), coverage is 99 % (previously 91 %), `ruff check` and `ruff format` are clean, and the suite is stable across repeated runs (≈ 2 s).

**Live checks:**
- uvicorn + `start_market_data()` + `curl` on `/api/stream/prices` produced frames with all ten fields.
- Removing tickers produced frames `[AAPL, TSLA] → [TSLA] → {}`.
- `/api/health` reports `status()`.
- With a bogus `MASSIVE_API_KEY`, the sandbox's proxy returned a non-JSON 404. That was correctly classified as a transient error: the app stayed up and `last_error` was reported.

**Not verified:** the real-world 401/403 paths against api.massive.com, because the sandbox has no outbound access to it. Verify these with a real key before relying on EOD mode.
