# Massive API (formerly Polygon.io) — Stock Price Reference

Research notes for FinAlly: how to retrieve **real-time / delayed** and **end-of-day** prices for **multiple tickers** from Massive, with Python examples using the official `massive` client.

> Researched 2026-11-12 against massive.com/docs and the `massive-com/client-python` source (`master`). Polygon.io rebranded as Massive; the API shape is unchanged from Polygon's v2/v3 REST API, only the host, package name and env var changed.

---

## 1. Basics

| Item | Value |
|---|---|
| Base URL | `https://api.massive.com` (old `api.polygon.io` paths are identical) |
| Auth | `Authorization: Bearer <KEY>` header **or** `?apiKey=<KEY>` query param |
| API key | https://massive.com/dashboard/keys |
| Python package | `massive` (`pip install -U massive` / `uv add massive`), Python ≥ 3.9 |
| Import | `from massive import RESTClient` |
| Env var read by client | `MASSIVE_API_KEY` (default for `RESTClient(api_key=...)`) |
| Pagination | JSON responses carry `next_url`; the Python client follows it automatically for `list_*` methods |
| Ticker case | **Case-sensitive** — always send uppercase (`AAPL`, not `aapl`) |

### Python client construction

```python
from massive import RESTClient

client = RESTClient()                       # reads MASSIVE_API_KEY from env
client = RESTClient(api_key="...")          # or explicit
client = RESTClient(
    api_key="...",
    connect_timeout=10.0,   # seconds (default 10)
    read_timeout=10.0,      # seconds (default 10)
    retries=3,              # urllib3 Retry; retries on 413, 429, 499, 500, 502, 503, 504
    trace=False,            # True prints request URLs (key redacted)
)
```

- The client is **synchronous** (urllib3). In an asyncio app (FastAPI) call it via `asyncio.to_thread(...)`.
- Every method accepts `raw=True` to get the `urllib3.HTTPResponse` instead of a parsed model — handy for debugging field names.
- Missing key → `massive.exceptions.AuthError` at construction. HTTP errors → `massive.exceptions.BadResponse`.

---

## 2. Plans, Recency and Rate Limits

What matters for FinAlly is **which endpoints each plan can call** and **how fresh the data is**.

| Plan | Price | Rate limit | Snapshots | Aggregates (prev close / grouped daily / bars) | Recency |
|---|---|---|---|---|---|
| **Stocks Basic** (free) | $0 | **5 calls/min** | **Not included (403)** | Included | **End-of-day only** |
| Stocks Starter | paid | unlimited* | Included | Included | 15-min delayed |
| Stocks Developer | paid | unlimited* | Included | Included | 15-min delayed |
| Stocks Advanced | $199/mo | unlimited* | Included | Included | Real-time |
| Stocks Business | paid | unlimited* | Included | Included | Real-time (+ `fmv`) |

\* Paid plans advertise no hard per-minute cap. Stay under ~100 req/s to be polite.

**Key consequences**

1. **The free key cannot call snapshot endpoints.** `GET /v2/snapshot/...` and `GET /v3/snapshot` return **HTTP 403** `NOT_AUTHORIZED` on Basic. On Basic, the only "price" you can get is the **previous trading day's close** (and older bars).
2. On Basic, **today's** daily bar is not available until **after 00:00 ET** the next day (requesting it earlier → 403 `"Your plan doesn't include this data timeframe"`).
3. Exceeding 5 req/min on Basic → **HTTP 429**. The client retries 429 automatically (3 times by default), which on Basic just burns more quota — use `retries=0` or a long poll interval.
4. Snapshot data is **cleared daily around 3:30 AM ET** (docs; the client docstring says 12 AM ET) and repopulates from ~4:00 AM ET as exchanges report. Overnight, `last_trade` may be missing — fall back to `prev_day.close`.

---

## 3. Endpoints for Current Prices (multiple tickers)

### 3.1 Full Market Snapshot — **recommended for polling** (Starter+)

`GET /v2/snapshot/locale/us/markets/stocks/tickers?tickers=AAPL,MSFT,TSLA`

One call returns the latest trade, quote, minute bar, today's bar and previous day's bar for **every ticker in the list** (omit `tickers` for the whole market, ~10k symbols). This is the most efficient way to poll a watchlist: **1 API call per poll regardless of watchlist size**.

| Query param | Type | Notes |
|---|---|---|
| `tickers` | comma-separated | Case-sensitive. Empty → all tickers |
| `include_otc` | bool | Default `false` |

Response (abridged):

```json
{
  "status": "OK",
  "count": 1,
  "tickers": [
    {
      "ticker": "AAPL",
      "todaysChange": -0.124,
      "todaysChangePerc": -0.601,
      "updated": 1605192894630916600,
      "day":     {"o": 20.64, "h": 20.64, "l": 20.506, "c": 20.506, "v": 37216, "vw": 20.616},
      "prevDay": {"o": 20.79, "h": 21.0,  "l": 20.5,   "c": 20.63,  "v": 292738, "vw": 20.6939},
      "min":     {"o": 20.506, "h": 20.506, "l": 20.506, "c": 20.506, "v": 5000, "t": 1684428600000, "n": 1, "av": 37216},
      "lastTrade": {"p": 20.506, "s": 2416, "t": 1605192894630916600, "x": 4, "i": "71675577320245", "c": [14, 41]},
      "lastQuote": {"p": 20.5, "s": 13, "P": 20.6, "S": 22, "t": 1605192959994246100}
    }
  ]
}
```

> ⚠️ **Timestamp units differ by field.** `lastTrade.t`, `lastQuote.t` and `updated` are **nanoseconds** (19 digits). Bar timestamps (`min.t`, aggregate `t`) are **milliseconds** (13 digits).

Python:

```python
from massive import RESTClient
from massive.rest.models import SnapshotMarketType

client = RESTClient()
snaps = client.get_snapshot_all(
    market_type=SnapshotMarketType.STOCKS,   # or "stocks"
    tickers=["AAPL", "MSFT", "TSLA"],        # list or "AAPL,MSFT,TSLA"
)

for s in snaps:                               # list[TickerSnapshot]
    trade = s.last_trade                       # LastTrade | None
    if trade and trade.price:
        price = trade.price
        ts = trade.sip_timestamp / 1e9         # ns → Unix seconds
    else:                                      # overnight / pre-market reset
        price = s.prev_day.close if s.prev_day else None
        ts = None
    print(s.ticker, price, s.todays_change_percent, s.prev_day.close if s.prev_day else None)
```

Python model field mapping (`massive.rest.models.TickerSnapshot`):

| JSON | Python attribute | Type |
|---|---|---|
| `ticker` | `.ticker` | str |
| `lastTrade` | `.last_trade` → `LastTrade` | `.price`, `.size`, `.exchange`, **`.sip_timestamp`** (ns), `.participant_timestamp`, `.conditions`, `.id` |
| `lastQuote` | `.last_quote` → `LastQuote` | `.bid_price`, `.bid_size`, `.ask_price`, `.ask_size`, `.sip_timestamp` |
| `day` | `.day` → `Agg` | `.open`, `.high`, `.low`, `.close`, `.volume`, `.vwap` |
| `prevDay` | `.prev_day` → `Agg` | same as `day` |
| `min` | `.min` → `MinuteSnapshot` | `.open` … `.close`, `.volume`, `.accumulated_volume`, `.timestamp` (ms) |
| `todaysChange` | `.todays_change` | float |
| `todaysChangePerc` | `.todays_change_percent` | float |
| `updated` | `.updated` | int (ns) |
| `fmv` | `.fair_market_value` | float (Business only) |

> ⚠️ `LastTrade` has **no `.timestamp` attribute** — it is `.sip_timestamp`. Accessing `.timestamp` raises `AttributeError`.

### 3.2 Single Ticker Snapshot (Starter+)

`GET /v2/snapshot/locale/us/markets/stocks/tickers/{ticker}` — same object as above for one ticker.

```python
snap = client.get_snapshot_ticker("stocks", "AAPL")   # TickerSnapshot
print(snap.last_trade.price, snap.day.close, snap.prev_day.close)
```

Use for ad-hoc validation ("does this ticker exist?") when adding to the watchlist; not for polling.

### 3.3 Universal Snapshot (Starter+)

`GET /v3/snapshot?ticker.any_of=AAPL,NVDA,X:BTCUSD` — multi-asset (stocks, options, forex `C:`, crypto `X:`, indices `I:`). Up to **250 tickers** per call, `limit` max 250 (default **10** — set it!). Unknown tickers come back as `{"ticker": "...", "error": "NOT_FOUND"}` rather than being silently dropped, which makes it useful for validation.

```python
for s in client.list_universal_snapshots(
    ticker_any_of=["AAPL", "NVDA", "NOPE"],
    limit=250,
):
    if s.error:
        print(s.ticker, "->", s.error, s.message)
        continue
    price = (s.last_trade.price if s.last_trade else None) or (s.session.price if s.session else None)
    print(s.ticker, price, s.session.change_percent, s.session.previous_close)
```

`UniversalSnapshotSession` fields: `price`, `open`, `high`, `low`, `close`, `previous_close`, `change`, `change_percent`, `volume`, `vwap`, plus `early_/regular_/late_trading_change[_percent]`.

### 3.4 Last Trade / Last Quote (per ticker)

`GET /v2/last/trade/{ticker}`, `GET /v2/last/nbbo/{ticker}` — one call **per ticker**. Avoid for watchlists; the snapshot gets all tickers in one call.

```python
t = client.get_last_trade("AAPL")    # LastTrade: .price, .size, .sip_timestamp (ns)
q = client.get_last_quote("AAPL")    # LastQuote: .bid_price, .ask_price
```

---

## 4. Endpoints for End-of-Day Prices

All of these work on the **free Basic plan** (for completed days).

### 4.1 Daily Market Summary (Grouped Daily) — **all tickers, one call**

`GET /v2/aggs/grouped/locale/us/market/stocks/{date}` — one OHLCV bar for **every** US stock on `date` (YYYY-MM-DD). This is the best way to get EOD prices for a whole watchlist on the free tier: **1 call, filter client-side**.

| Param | Notes |
|---|---|
| `date` | Trading date; weekend/holiday → empty `results` |
| `adjusted` | Split-adjusted (default `true`) |
| `include_otc` | Default `false` |

```json
{"status": "OK", "adjusted": true, "resultsCount": 10812, "queryCount": 10812,
 "results": [{"T": "AAPL", "o": 189.1, "h": 191.0, "l": 188.4, "c": 190.2,
              "v": 51234567, "vw": 189.9, "n": 612345, "t": 1731618000000}]}
```

```python
from datetime import date, timedelta

def last_completed_trading_day_bars(client, tickers: set[str], max_back: int = 7) -> dict[str, float]:
    """Walk back from yesterday until a trading day returns data."""
    d = date.today() - timedelta(days=1)
    for _ in range(max_back):
        bars = client.get_grouped_daily_aggs(d.isoformat(), adjusted=True)  # list[GroupedDailyAgg]
        if bars:
            return {b.ticker: b.close for b in bars if b.ticker in tickers}
        d -= timedelta(days=1)          # weekend / holiday
    return {}

closes = last_completed_trading_day_bars(client, {"AAPL", "MSFT", "TSLA"})
```

`GroupedDailyAgg` fields: `.ticker`, `.open`, `.high`, `.low`, `.close`, `.volume`, `.vwap`, `.timestamp` (ms), `.transactions`.

> The response is large (~10k rows, ~1 MB). Fine for a once-per-startup / once-per-day call.

### 4.2 Previous Day Bar — per ticker

`GET /v2/aggs/ticker/{ticker}/prev` — the previous trading day's OHLCV.

```python
prev = client.get_previous_close_agg("AAPL")     # list[PreviousCloseAgg] (results array)
bar = prev[0] if isinstance(prev, list) else prev
print(bar.ticker, bar.close, bar.timestamp)       # timestamp in ms
```

One call per ticker → 10 tickers = 10 calls = 2 minutes of Basic quota. Prefer grouped daily (4.1) for a watchlist.

### 4.3 Daily Ticker Summary (Open/Close) — per ticker, specific date

`GET /v1/open-close/{ticker}/{date}` — open, high, low, close, **pre-market** and **after-hours** prices.

```python
oc = client.get_daily_open_close_agg("AAPL", "2026-11-11")   # DailyOpenCloseAgg
print(oc.symbol, oc.open, oc.close, oc.pre_market, oc.after_hours)
```

### 4.4 Custom Bars (historical range) — per ticker

`GET /v2/aggs/ticker/{ticker}/range/{multiplier}/{timespan}/{from}/{to}` — e.g. daily bars for the last 30 days (useful to pre-fill a chart).

```python
bars = client.get_aggs("AAPL", 1, "day", "2026-10-01", "2026-11-11", adjusted=True, limit=50000)
for b in bars:                                   # list[Agg]
    print(b.timestamp, b.open, b.high, b.low, b.close, b.volume)

# list_aggs() returns an iterator that follows pagination for big ranges
for b in client.list_aggs("AAPL", 5, "minute", "2026-11-10", "2026-11-11"):
    ...
```

`timespan`: `second | minute | hour | day | week | month | quarter | year`.

---

## 5. Choosing an Endpoint

| Need | Free (Basic) | Paid (Starter+) |
|---|---|---|
| Live-ish price for N watchlist tickers | ❌ not possible — use last EOD close | **Full Market Snapshot** (§3.1), 1 call/poll |
| Daily change % reference | Grouped daily of the prior 2 trading days | `todays_change_percent` / `prev_day.close` from snapshot |
| EOD close for N tickers | **Grouped Daily** (§4.1), 1 call | same |
| Validate a new ticker exists | Previous Day Bar (empty results → unknown) | Universal Snapshot (`error: NOT_FOUND`) |
| Chart history | Custom Bars (§4.4) | same |

---

## 6. Poll-Loop Example (async, FastAPI-friendly)

```python
import asyncio, logging
from massive import RESTClient
from massive.exceptions import BadResponse
from massive.rest.models import SnapshotMarketType

log = logging.getLogger(__name__)

async def poll_prices(tickers: list[str], on_price, interval: float = 15.0):
    client = RESTClient(retries=1)     # avoid stacked retries eating rate limit
    while True:
        try:
            snaps = await asyncio.to_thread(
                client.get_snapshot_all, SnapshotMarketType.STOCKS, tickers
            )
            for s in snaps:
                t = s.last_trade
                if t and t.price:
                    on_price(s.ticker, t.price, t.sip_timestamp / 1e9)
                elif s.prev_day and s.prev_day.close:
                    on_price(s.ticker, s.prev_day.close, None)
        except BadResponse as e:          # 401/403 (plan), 429 (rate), 5xx
            log.warning("Massive poll failed: %s", e)
        except Exception:
            log.exception("Massive poll crashed")
        await asyncio.sleep(interval)
```

---

## 7. Gotchas Checklist

- **Uppercase tickers** — the API is case-sensitive.
- **Nanosecond vs millisecond timestamps** — trades/quotes/`updated` are ns; bars are ms.
- **`LastTrade.sip_timestamp`**, not `.timestamp`.
- **`last_trade` can be `None`** (plan without trades, overnight reset, halted/illiquid ticker) — fall back to `min.close`, then `day.close`, then `prev_day.close`.
- **Unknown tickers are silently omitted** from `get_snapshot_all` results (no error) — detect by diffing requested vs returned.
- **Free tier = 403 on snapshots.** Detect it once at startup and degrade to EOD (see MARKET_INTERFACE.md §6).
- **Built-in retries on 429** multiply call count; set `retries` low on the free tier.
- **Synchronous client** — never call it directly on the event loop.
- **Weekends/holidays** — grouped daily for a non-trading date returns no results; walk back.

## 8. Implications for the Existing `backend/app/market/massive_client.py`

Reviewing the current implementation against the client source turned up three problems:

1. **`snap.last_trade.timestamp` doesn't exist** → `AttributeError` on every snapshot, which is caught and logged as a warning, so **no prices ever reach the cache in Massive mode**. The unit tests use `MagicMock` snapshots, so they don't catch this. Fix: use `snap.last_trade.sip_timestamp`.
2. **Wrong unit conversion** — it divides by `1000` (ms); `sip_timestamp` is **nanoseconds** → divide by `1e9`.
3. **The free tier can't call snapshots.** PLAN.md says "Free tier: poll every 15 s", but on Basic the snapshot endpoint returns 403. A free key needs the EOD fallback described in MARKET_INTERFACE.md.

## Sources

- [REST Quickstart](https://massive.com/docs/rest/quickstart.md)
- [Full Market Snapshot](https://massive.com/docs/rest/stocks/snapshots/full-market-snapshot.md)
- [Single Ticker Snapshot](https://massive.com/docs/rest/stocks/snapshots/single-ticker-snapshot.md)
- [Universal Snapshot](https://massive.com/docs/rest/stocks/snapshots/unified-snapshot.md)
- [Daily Market Summary](https://massive.com/docs/rest/stocks/aggregates/daily-market-summary.md)
- [Previous Day Bar](https://massive.com/docs/rest/stocks/aggregates/previous-day-bar.md)
- [KB: Basic plan previous-day timing](https://massive.com/knowledge-base/article/on-the-free-basic-plan-when-can-i-pull-the-previous-trading-days-data)
- [Pricing](https://massive.com/pricing)
- [massive-com/client-python](https://github.com/massive-com/client-python) — `massive/rest/{snapshot,aggs,trades}.py`, `massive/rest/models/{snapshot,aggs,trades}.py`, `massive/rest/base.py`
