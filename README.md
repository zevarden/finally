# FinAlly — AI Trading Workstation

A visually stunning AI-powered trading workstation that streams live market data, simulates portfolio trading, and integrates an LLM chat assistant that can analyze positions and execute trades via natural language.

Built entirely by coding agents as a capstone project for an agentic AI coding course.

## Features

- **Live price streaming** via SSE with green/red flash animations
- **Simulated portfolio** — $10k virtual cash, market orders, instant fills
- **Portfolio visualizations** — heatmap (treemap), P&L chart, positions table
- **AI chat assistant** — analyzes holdings, suggests and auto-executes trades
- **Watchlist management** — track tickers manually or via AI
- **Dark terminal aesthetic** — Bloomberg-inspired, data-dense layout

## Architecture

Single Docker container serving everything on port 8000:

- **Frontend**: Next.js (static export) with TypeScript and Tailwind CSS
- **Backend**: FastAPI (Python/uv) with SSE streaming
- **Database**: SQLite with lazy initialization (named Docker volume `finally-data`)
- **AI**: LiteLLM → `xai/grok-4.7` with structured outputs
- **Market data**: Built-in GBM simulator (default) or Massive API (optional)

## Run it

Requires Docker. First, configure your environment:

```bash
cp .env.example .env    # then set LITELLM_MASTER_KEY (the start scripts create .env for you if missing)
```

Write `.env` values without quotes (`KEY=value`): `docker --env-file` keeps quotes literally.
If you route chat through a LiteLLM proxy on your machine, `LITELLM_API_BASE=http://localhost:4000` is fine.
Inside the container, localhost URLs are rewritten to `host.docker.internal` (the scripts and compose map it to the host gateway).

**macOS / Linux**

```bash
./scripts/start_mac.sh            # builds the image on first run, starts the container, opens the browser
./scripts/start_mac.sh --build    # force a rebuild (add --no-open to skip the browser)
./scripts/stop_mac.sh             # stop and remove the container (data is kept)
```

**Windows (PowerShell)**

```powershell
.\scripts\start_windows.ps1            # add -Build to force a rebuild, -NoOpen to skip the browser
.\scripts\stop_windows.ps1
```

If script execution is blocked, run `powershell -ExecutionPolicy Bypass -File .\scripts\start_windows.ps1`.

**Plain Docker / Compose**

```bash
docker build -t finally .
docker run -d --name finally --add-host=host.docker.internal:host-gateway \
  -v finally-data:/app/db -p 8000:8000 --env-file .env finally
# or: docker compose up -d --build   /   docker compose down
```

Then open http://localhost:8000. Your portfolio lives in the `finally-data` volume and survives restarts;
`docker volume rm finally-data` resets it.

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `LITELLM_MASTER_KEY` | Yes (for AI chat) | LiteLLM key used to call `xai/grok-4.7` |
| `LITELLM_API_BASE` | No | LiteLLM proxy base URL. Empty = call xAI directly with `LITELLM_MASTER_KEY`; set = route chat through the proxy |
| `LITELLM_API_KEY` | No | Proxy virtual key, used only when `LITELLM_API_BASE` is set (empty = falls back to `LITELLM_MASTER_KEY`) |
| `LLM_MODEL` | No | LLM model id (default `xai/grok-4.7`) |
| `MASSIVE_API_KEY` | No | Massive (Polygon.io) key for real market data; empty = built-in simulator |
| `MASSIVE_POLL_INTERVAL` | No | Seconds between Massive polls (default 15, the free-tier limit) |
| `MARKET_SIM_SEED` | No | Fixed RNG seed for deterministic simulator prices |
| `LLM_MOCK` | No | `true` (or `1`/`yes`/`on`) for deterministic mock LLM responses (testing) |
| `DB_PATH` | No | Override the SQLite file path (default `db/finally.db`, `/app/db/finally.db` in Docker) |
| `FINALLY_IN_DOCKER` | No | Set to `1` in the image; makes a localhost `LITELLM_API_BASE` point at `host.docker.internal`. `0` disables the rewrite |
| `STATIC_DIR` | No | Override the static frontend directory (default `backend/static`) |

## Project Structure

```
finally/
├── frontend/    # Next.js static export
├── backend/     # FastAPI uv project
├── planning/    # Project documentation and agent contracts
├── test/        # Playwright E2E tests
├── db/          # SQLite volume mount (runtime)
└── scripts/     # Start/stop helpers
```

## License

See [LICENSE](LICENSE).
