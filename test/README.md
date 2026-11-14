# FinAlly E2E tests

Playwright (TypeScript, Chromium) tests for the scenarios in `planning/PLAN.md` §12, written against the
interfaces in `planning/TEAM_CONTRACT.md` (§5 REST API, §6 mock-LLM strings, §7 `data-testid`s).

The backend must run with `LLM_MOCK=true`. `MARKET_SIM_SEED=42` is recommended. The specs run serially
(`workers: 1`) in file order because they share one database. `01-fresh-start` expects a freshly seeded DB
to check the `$10,000.00` balance. On a used DB it falls back to comparing with `/api/portfolio` and adds a note.
The other specs set up their own state (each uses its own tickers and closes its positions).

| Spec | Covers |
|---|---|
| `01-fresh-start` | 10 default tickers, $10k cash, prices changing, flash classes, connection status, main-chart selection |
| `02-watchlist` | Add/remove a ticker through the UI |
| `03-trading` | Buy, sell (partial and to zero), trade errors |
| `04-portfolio-viz` | Heatmap `data-pnl`, P&L chart `data-points`, positions table |
| `05-chat` | Mock chat: trade executed inline, failed trade, watchlist via chat, loading indicator, history reload |
| `06-sse-resilience` | Network drop gives reconnecting/disconnected status, then back to connected |
| `07-api` | REST shapes and status codes, the SSE frame format, static serving |

## Run in Docker (the reference run)

From the repo root:

```bash
docker compose -f test/docker-compose.test.yml up --build --abort-on-container-exit --exit-code-from playwright
docker compose -f test/docker-compose.test.yml down -v
```

This builds the app from the root `Dockerfile` as service `finally-app` with a fresh DB (tmpfs, no volume), waits for
`/api/health`, and runs the
suite in `mcr.microsoft.com/playwright:v1.65.0-noble`, which matches `@playwright/test` 1.65.0. Keep the two
versions in sync. The HTML report lands in `test/playwright-report/`. The service is deliberately not called `app`:
Chromium HSTS-preloads the `.app` TLD and would force `http://app:8000` to HTTPS.

## Run locally against a running backend

```powershell
cd test
npm install
npx playwright install chromium   # first time only

# In another shell, start the backend on a spare port with a throwaway DB:
cd backend
$env:LLM_MOCK='true'; $env:MARKET_SIM_SEED='42'; $env:DB_PATH="$env:TEMP\finally-e2e.db"
$env:STATIC_DIR="$PWD\..\frontend\out"
uv run uvicorn app.main:app --port 8030

# Back in test/:
$env:BASE_URL='http://127.0.0.1:8030'; npm test   # 127.0.0.1: on Windows "localhost" may resolve to ::1 first
```

`BASE_URL` defaults to `http://localhost:8000`. Use `npm run report` to open the last HTML report and
`npx playwright test tests/05-chat.spec.ts` to run a single file. Delete the temp DB first to get a fresh start.

Bugs found are logged in `ISSUES.md`.
