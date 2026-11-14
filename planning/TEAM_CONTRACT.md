# FinAlly — Team Contract

This is the shared contract for the agent team building the rest of FinAlly. `PLAN.md` is the product spec, and this file pins down the interfaces between team members. **If you need to change something here, message the owner of that section and `team-lead` first. Don't change it on your own.** Additions that don't break anything (new optional fields, new helper functions) are fine. Record them here.

## 1. Team & Ownership

| Name | Owns (writes) | Tests in |
|---|---|---|
| `db-engineer` | `backend/app/db/` (schema SQL, seed, lazy init, data access) | `backend/tests/db/` |
| `backend-engineer` | `backend/app/main.py`, `backend/app/api/`, `backend/app/services/`, `backend/pyproject.toml`, `backend/uv.lock` | `backend/tests/api/`, `backend/tests/services/` |
| `llm-engineer` | `backend/app/llm/` | `backend/tests/llm/` |
| `frontend-engineer` | `frontend/` | `frontend/` (Vitest + React Testing Library) |
| `devops-engineer` | `Dockerfile`, `.dockerignore`, `docker-compose.yml`, `scripts/`, `.env.example`, root `README.md` run instructions | smoke-test the image |
| `integration-tester` | `test/` (Playwright + `docker-compose.test.yml`) | `test/` |

Rules:
- **Only write files you own.** If something you need is in someone else's area, `SendMessage` them with the exact problem (file, line, failing command or output, expected vs. actual).
- `backend/app/market/` is finished and frozen. Use it through `from app.market import ...` only (see `backend/CLAUDE.md`).
- **Dependencies:** `backend-engineer` is the only one who runs `uv add`/`uv remove` (to avoid lockfile races). `litellm` and `httpx` (dev) are already installed. Python is pinned to 3.12 (`backend/.python-version`).
- **Git:** nobody commits, branches, stashes or resets. `team-lead` commits at milestones.
- **Don't run long-lived servers on port 8000**, which is reserved for the integration tester's/DevOps' container. For local manual runs use a different port (e.g. `uvicorn app.main:app --port 8010`), and stop it when you're done.
- When your piece is done and its unit tests pass, `SendMessage` `team-lead` with a short summary (what's done, how to run its tests, any contract additions).

## 2. Runtime Layout

| Thing | Local (repo) | Container |
|---|---|---|
| Backend project | `backend/` | `/app/backend` (WORKDIR for uvicorn) |
| Frontend static export | `frontend/out/` (`next build` with `output: 'export'`) | `/app/backend/static/` |
| SQLite DB | `db/finally.db` | `/app/db/finally.db` (named volume `finally-data`) |

- `DB_PATH` env var overrides the DB file. Default: `<backend dir>/../db/finally.db`, computed from the `app/db` package location, so it is correct both locally and in the container. Parent dirs are created if missing.
- `STATIC_DIR` env var overrides the static dir. Default: `<backend dir>/static`. If the dir doesn't exist, the backend still starts (API only).
- Server command: `uv run uvicorn app.main:app --host 0.0.0.0 --port 8000` from the backend dir.
- Env vars: see `.env.example` (`LITELLM_MASTER_KEY`, `MASSIVE_API_KEY`, `MASSIVE_POLL_INTERVAL`, `MARKET_SIM_SEED`, `LLM_MOCK`), plus `DB_PATH`, `STATIC_DIR`, and anything `llm-engineer` adds (e.g. `LITELLM_API_BASE`). Record additions here and tell `devops-engineer`.
- LLM env vars (added by `llm-engineer`, all optional): `LITELLM_API_BASE` (LiteLLM proxy URL; when set, chat calls go to `litellm_proxy/<model>` at that base using `LITELLM_API_KEY` (the proxy's virtual key) as the key, falling back to `LITELLM_MASTER_KEY` if `LITELLM_API_KEY` is empty; when empty, `LITELLM_MASTER_KEY` is sent directly to xAI as the API key), `LLM_MODEL` (default `xai/grok-4.7`: the model id is hyphenated; `grok4.7` is rejected by the proxy). `LLM_MOCK` accepts `true`/`1`/`yes`/`on`. All LLM env values are stripped and lose one layer of matching surrounding quotes (`docker --env-file` keeps quotes literally). In a container (`/.dockerenv` exists, or `FINALLY_IN_DOCKER=1`; `FINALLY_IN_DOCKER=0` disables), a `LITELLM_API_BASE` on localhost/127.0.0.1/::1 is rewritten to `host.docker.internal` (scheme/port/path kept); on Linux hosts the container needs `--add-host=host.docker.internal:host-gateway`. The backend reads these from the process environment; `app.main` also loads the root `.env` (override=False) for local runs.

## 3. Database layer — `app.db` (owner: db-engineer)

Uses the stdlib `sqlite3` module (no ORM). The schema is exactly PLAN.md §7 (all tables have `user_id` default `"default"`, UUID text ids, ISO-8601 UTC timestamps). Schema SQL lives in `backend/app/db/schema.sql`. (PLAN §4 mentions `backend/db/`; it lives inside the `app` package so it can be imported.)

```python
from app.db import Database, get_database

db = Database(path)          # opens/creates the file; lazily creates tables + seeds if missing/empty
db = get_database()          # process-wide singleton using DB_PATH / default path

# All methods take user_id: str = "default" as a keyword arg. Return plain dicts / lists of dicts.
db.get_cash() -> float
db.set_cash(amount: float) -> None

db.list_watchlist() -> list[dict]            # [{"ticker", "added_at"}] ordered by added_at
db.add_watchlist(ticker) -> bool             # False if already present
db.remove_watchlist(ticker) -> bool          # False if not present

db.list_positions() -> list[dict]            # [{"ticker", "quantity", "avg_cost", "updated_at"}]
db.get_position(ticker) -> dict | None
db.upsert_position(ticker, quantity, avg_cost) -> None
db.delete_position(ticker) -> None

db.insert_trade(ticker, side, quantity, price) -> dict   # returns the full row incl. id, executed_at
db.list_trades(limit: int = 100) -> list[dict]           # newest first
db.last_trade_prices() -> dict[str, float]               # latest trade price per ticker (simulator resume)

db.insert_snapshot(total_value) -> dict
db.list_snapshots(limit: int = 1000) -> list[dict]       # [{"total_value", "recorded_at"}] oldest first

db.insert_chat_message(role, content, actions: dict | None = None) -> dict  # actions stored as JSON
db.list_chat_messages(limit: int = 50) -> list[dict]     # oldest first; "actions" decoded back to dict|None

with db.transaction():       # atomic block; nested db calls inside join the same transaction
    ...
```

Additions (db-engineer): `app.db` also exports `reset_database()` (close + forget the singleton; for tests), `default_db_path()`, `Database.close()`, and constants `DEFAULT_TICKERS`, `DEFAULT_CASH`, `DEFAULT_USER`. `transaction()` holds the DB lock for the whole block; nested blocks (and every write method) join it via SAVEPOINTs, so an exception escaping the outer block rolls back everything (an exception caught inside an inner block rolls back only that inner block). The db does not normalize tickers (callers do). CHECK constraints: `trades.side` in buy/sell, `chat_messages.role` in user/assistant. `list_positions()` is ordered by ticker. The default watchlist is seeded only when the default profile row is first created (an emptied watchlist is not refilled on restart).

The class is thread-safe: FastAPI sync endpoints run in a threadpool, and the snapshot task runs concurrently. Seeding: one `users_profile` row (`default`, 10000.0) plus the 10 default watchlist tickers. Re-running init on an existing DB never duplicates or overwrites data. Tests use `tmp_path` DB files.

## 4. Services — `app.services` (owner: backend-engineer)

Business logic shared by the REST routes and the LLM chat. **The LLM layer must call these. It must not reimplement trade or watchlist logic.**

```python
from app.services import (
    TradeError, WatchlistError,
    execute_trade, get_portfolio, add_ticker, remove_ticker, record_snapshot,
)

execute_trade(db, cache, source, ticker, side, quantity) -> dict
    # side in {"buy","sell"}; quantity > 0 (fractional OK). Fills at cache price.
    # Raises TradeError(msg) for: invalid ticker, no price available, quantity <= 0,
    # insufficient cash, insufficient shares. Atomic (db.transaction()).
    # Sell to zero deletes the position. Tracks the ticker in `source` if newly held;
    # untracks when a position closes and the ticker is not on the watchlist.
    # Records a portfolio snapshot afterwards.
    # Returns {"trade": <trade row>, "portfolio": <get_portfolio(...) result>}

get_portfolio(db, cache) -> dict          # shape = GET /api/portfolio response below
add_ticker(db, cache, source, ticker) -> dict      # returns watchlist entry; raises WatchlistError
remove_ticker(db, cache, source, ticker) -> None   # raises WatchlistError; keeps tracking if still held
record_snapshot(db, cache) -> dict

# WatchlistError has .status_code (400 invalid, 404 unknown ticker / not on list, 409 duplicate)
```

Implementation notes (backend-engineer):
- `execute_trade`, `add_ticker` and `remove_ticker` are **coroutines** (`await` them), because the market source's `add_ticker`/`remove_ticker` are async. `get_portfolio` and `record_snapshot` are sync.
- Additions: `get_watchlist(db, cache) -> list[entry]` (the GET /api/watchlist entries), `validate_ticker(t) -> str | None`, `tracked_tickers(db) -> list[str]`, `ServiceError` (base of both errors).
- Buying a ticker that isn't tracked yet is allowed: it is priced via `source.add_ticker()` first (untracked again if the trade fails).
- `app.main.create_app(db=None, static_dir=None, start_source=None, snapshot_interval=30.0)` builds the app (tests inject these); `app.main.app` is the default instance. A snapshot is also recorded at startup.

Ticker validation: `normalize_ticker()` then match `^[A-Z][A-Z0-9.\-]{0,9}$`.

Valuation: a position with no cached price is valued at `avg_cost`.

## 5. REST API (owner: backend-engineer; the chat route is mounted from `app.llm`)

Errors use FastAPI's default `{"detail": "<human-readable message>"}`. All money and quantity values are JSON numbers.

**GET `/api/health`** → `200 {"status": "ok", "market": <source.status()>}`

**GET `/api/portfolio`** →
```json
{
  "cash_balance": 8100.0,
  "positions_value": 1950.0,
  "total_value": 10050.0,
  "unrealized_pnl": 50.0,
  "unrealized_pnl_percent": 2.63,
  "positions": [
    {"ticker": "AAPL", "quantity": 10, "avg_cost": 190.0, "current_price": 195.0,
     "market_value": 1950.0, "unrealized_pnl": 50.0, "unrealized_pnl_percent": 2.63,
     "weight": 0.194}
  ]
}
```
`weight` = market_value / total_value (0–1). Positions are sorted by market_value, descending.

**POST `/api/portfolio/trade`** body `{"ticker": "AAPL", "quantity": 10, "side": "buy"}` → `200 {"trade": {id, ticker, side, quantity, price, executed_at}, "portfolio": {...as above}}`. `400 {"detail"}` on TradeError, `422` on malformed body.

**GET `/api/portfolio/history`** → `200 {"snapshots": [{"total_value": 10000.0, "recorded_at": "2026-11-13T12:00:00+00:00"}, ...]}`, oldest first. A background task records a snapshot every 30 s, and one is also recorded after each trade.

**GET `/api/watchlist`** → `200 {"tickers": [{"ticker", "added_at", "price", "previous_price", "change_percent", "day_change_percent", "direction"}]}`. Price fields are `null` if not cached yet.

**POST `/api/watchlist`** body `{"ticker": "PYPL"}` → `201 <entry as above>`. `400` invalid, `404` unknown ticker (Massive), `409` already present.

**DELETE `/api/watchlist/{ticker}`** → `204`. `404` if not on the watchlist.

**GET `/api/stream/prices`** → SSE, already built (`create_stream_router()`). Each frame carries the **full map** `{TICKER: PriceUpdate.to_dict()}`, and clients replace their state with it. The fields are: `ticker, price, previous_price, timestamp, change, change_percent, direction, session_open, day_change, day_change_percent`. Tracked set = watchlist ∪ held positions.

**POST `/api/chat`** body `{"message": "buy 5 AAPL"}` →
```json
{
  "id": "uuid", "role": "assistant", "content": "Bought 5 AAPL at $190.12.",
  "created_at": "ISO",
  "actions": {
    "trades": [{"ticker": "AAPL", "side": "buy", "quantity": 5, "status": "executed", "price": 190.12, "error": null}],
    "watchlist_changes": [{"ticker": "PYPL", "action": "add", "status": "failed", "error": "PYPL is already on the watchlist"}]
  }
}
```
`status` is `"executed"` or `"failed"`. `actions` is `null` when there are none. `502 {"detail"}` if the LLM call fails entirely.

**GET `/api/chat/history`** → `200 {"messages": [{id, role, content, actions, created_at}, ...]}` oldest first (last 50). The frontend loads this on page load.

Static: everything not under `/api/` is served from `STATIC_DIR` with `html=True` (the Next.js export's `index.html`). Mount it **after** the API routers.

## 6. LLM layer — `app.llm` (owner: llm-engineer)

```python
from app.llm import create_chat_router   # APIRouter with POST /api/chat and GET /api/chat/history
```
The router reads `request.app.state.db`, `.price_cache` and `.market_source`. `backend-engineer` sets these in the lifespan. Flow per PLAN §9: build context (cash, positions with P&L, watchlist with live prices, total value) → last ~20 chat messages → LiteLLM `xai/grok-4.7` with structured output `{message, trades[], watchlist_changes[]}` → execute each action through `app.services` (catch TradeError/WatchlistError per action → `status: "failed"`) → store the user message and the assistant message + actions → return. If the reply doesn't parse, retry once, then return a friendly message with no actions (don't 500).

### Mock mode (`LLM_MOCK=true`), which the E2E tests depend on

Deterministic, no network. Parse the user message case-insensitively and apply the first rule that matches:
1. `buy <qty> <TICKER>` → one buy trade. Message: `"Mock: buying <qty> <TICKER>."`
2. `sell <qty> <TICKER>` → one sell trade. Message: `"Mock: selling <qty> <TICKER>."`
3. `add <TICKER>` / `watch <TICKER>` → watchlist add. Message: `"Mock: adding <TICKER> to your watchlist."`
4. `remove <TICKER>` / `unwatch <TICKER>` → watchlist remove. Message: `"Mock: removing <TICKER> from your watchlist."`
5. Anything else → no actions. Message: `"Mock response: I'm FinAlly, your AI trading assistant. You have $<cash formatted with 2 decimals> in cash."`

The actions still run through the real services, so a mocked trade really executes.

Formatting details (as implemented in `app/llm/mock.py`): `<TICKER>` is upper-cased with trailing `.`/`-` stripped (`buy 5 aapl.` -> `AAPL`); `<qty>` is printed without trailing zeros (`5`, `2.5`); matching is a search, not anchored (`please buy 5 AAPL` matches); cash uses thousands separators: `$10,000.00`. In mock mode the message is exactly the rule string even if the action fails; the failure is only in `actions[...].status/error`.

Chat persistence: on 502 nothing is stored. Otherwise the user message and the assistant message (with `actions`) are both stored. In real (non-mock) mode, failed actions are also appended to the assistant `content` as lines starting `Note: Could not ...`.

## 7. Frontend (owner: frontend-engineer)

Next.js + TypeScript + Tailwind, `output: 'export'` → `frontend/out/`. Same-origin API calls (`/api/...`). In dev, proxy `/api` to `http://localhost:8010` (or `NEXT_PUBLIC_API_BASE`), but the production build must use relative URLs. The SSE client is native `EventSource('/api/stream/prices')`. Connection status comes from EventSource `onopen` / `onerror` and `readyState`.

### `data-testid` contract (the Playwright tests depend on this)

| testid | Element / notes |
|---|---|
| `connection-status` | Header dot; attribute `data-status="connected" \| "reconnecting" \| "disconnected"` |
| `total-value` | Header total portfolio value (text like `$10,000.00`) |
| `cash-balance` | Header cash (text like `$10,000.00`) |
| `watchlist` | Watchlist panel container |
| `watchlist-row-<TICKER>` | One row per ticker (clicking it selects the ticker) |
| `price-<TICKER>` | Price cell; gets class `flash-up` / `flash-down` briefly on change |
| `sparkline-<TICKER>` | Sparkline element |
| `watchlist-add-input`, `watchlist-add-button` | Add-ticker form |
| `watchlist-remove-<TICKER>` | Remove button on each row |
| `main-chart` | Main chart container; attribute `data-ticker="<selected>"` |
| `trade-ticker`, `trade-quantity`, `trade-buy`, `trade-sell` | Trade bar inputs/buttons |
| `trade-error` | Visible trade error message (when the API returns 400) |
| `positions-table` | Positions table |
| `position-row-<TICKER>` | Row per position; contains the quantity |
| `heatmap` | Treemap container |
| `heatmap-cell-<TICKER>` | Cell; attribute `data-pnl="positive" \| "negative" \| "flat"` |
| `pnl-chart` | P&L chart container; attribute `data-points="<n>"` = number of snapshots |
| `chat-panel`, `chat-toggle` | Chat sidebar and its collapse toggle |
| `chat-input`, `chat-send` | Message input and send button |
| `chat-message` | Each message bubble; attribute `data-role="user" \| "assistant"` |
| `chat-loading` | Visible only while waiting for the response |
| `chat-action` | Each inline trade/watchlist confirmation; attribute `data-status="executed" \| "failed"` |

## 8. E2E (owner: integration-tester)

`test/docker-compose.test.yml`: a `finally-app` service built from the root `Dockerfile` (env `LLM_MOCK=true`, `MARKET_SIM_SEED=42`, fresh DB with no named volume) plus a `playwright` service (official `mcr.microsoft.com/playwright` image matching the `@playwright/test` version) running the suite against `http://finally-app:8000`. Don't name the service `app`: Chromium HSTS-preloads the `.app` TLD and forces `http://app:8000` to HTTPS. The suite can also run locally from `test/` against `BASE_URL` (default `http://localhost:8000`). Scenarios: PLAN §12.

Bug reports go to the owner by `SendMessage`, and a copy is logged in `test/ISSUES.md` (status: open/fixed/verified).
