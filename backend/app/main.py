"""FastAPI application: REST API, SSE stream, chat, and the static frontend.

Run: uv run uvicorn app.main:app --host 0.0.0.0 --port 8000   (from backend/)
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parent.parent
ROOT_ENV_FILE = BACKEND_DIR.parent / ".env"


def load_root_env(path: Path = ROOT_ENV_FILE) -> bool:
    """Load the project-root .env (PLAN §5) without overriding variables already set.

    Real env vars (Docker --env-file, shell exports, test monkeypatching) take precedence.
    Skipped under pytest so a developer's .env never leaks into tests.
    """
    if "pytest" in sys.modules or not path.is_file():
        return False
    return load_dotenv(path, override=False)


load_root_env()  # before any app module reads the environment

import asyncio  # noqa: E402
import logging  # noqa: E402
from collections.abc import Awaitable, Callable  # noqa: E402
from contextlib import asynccontextmanager  # noqa: E402

from fastapi import FastAPI  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402

from app.api import (  # noqa: E402
    create_health_router,
    create_portfolio_router,
    create_watchlist_router,
)
from app.market import (  # noqa: E402
    MarketDataSource,
    PriceCache,
    create_stream_router,
    start_market_data,
)
from app.services import record_snapshot, tracked_tickers  # noqa: E402

logger = logging.getLogger(__name__)

SNAPSHOT_INTERVAL = 30.0

StartSource = Callable[[PriceCache, list[str], dict[str, float] | None], Awaitable[MarketDataSource]]


def default_static_dir() -> Path:
    return Path(os.environ.get("STATIC_DIR") or BACKEND_DIR / "static")


async def _snapshot_loop(app: FastAPI, interval: float) -> None:
    while True:
        await asyncio.sleep(interval)
        try:
            record_snapshot(app.state.db, app.state.price_cache)
        except Exception:
            logger.exception("Periodic portfolio snapshot failed")


def create_app(
    db=None,
    static_dir: str | Path | None = None,
    start_source: StartSource | None = None,
    snapshot_interval: float = SNAPSHOT_INTERVAL,
) -> FastAPI:
    """Build the app. Tests can inject a Database, a static dir and a market source starter."""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if db is None:
            from app.db import get_database

            app.state.db = get_database()
        else:
            app.state.db = db
        database = app.state.db

        cache = PriceCache()
        app.state.price_cache = cache
        starter = start_source or start_market_data
        app.state.market_source = await starter(
            cache, tracked_tickers(database), database.last_trade_prices()
        )

        try:
            record_snapshot(database, cache)  # the P&L chart has a point right away
        except Exception:
            logger.exception("Startup portfolio snapshot failed")
        task = asyncio.create_task(_snapshot_loop(app, snapshot_interval), name="snapshots")
        try:
            yield
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            await app.state.market_source.stop()

    app = FastAPI(title="FinAlly", lifespan=lifespan)
    app.include_router(create_health_router())
    app.include_router(create_portfolio_router())
    app.include_router(create_watchlist_router())
    app.include_router(create_stream_router())

    try:
        from app.llm import create_chat_router
    except ImportError:
        logger.warning("app.llm not available; /api/chat is disabled", exc_info=True)
    else:
        app.include_router(create_chat_router())

    static = Path(static_dir) if static_dir is not None else default_static_dir()
    if static.is_dir():
        app.mount("/", StaticFiles(directory=static, html=True), name="static")
    else:
        logger.info("Static dir %s not found; serving the API only", static)

    return app


app = create_app()
