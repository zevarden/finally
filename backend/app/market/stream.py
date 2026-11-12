"""SSE endpoint: GET /api/stream/prices."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncGenerator
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from .cache import PriceCache

logger = logging.getLogger(__name__)

STREAM_INTERVAL = 0.5
KEEPALIVE_SECONDS = 15.0


def create_stream_router(
    price_cache: PriceCache | None = None,
    interval: float = STREAM_INTERVAL,
) -> APIRouter:
    """Create a new router (one per call, so repeated calls never duplicate routes).

    If price_cache is None it is read from request.app.state.price_cache, which
    works with lifespan wiring and with a source swapped at startup.
    """
    router = APIRouter(prefix="/api/stream", tags=["streaming"])

    @router.get("/prices")
    async def stream_prices(request: Request) -> StreamingResponse:
        """Stream the full price map whenever it changes (checked every ~500 ms).

        retry: 1000
        data: {"AAPL": {"ticker": "AAPL", "price": 190.5, ...}, ...}
        : ping            (after 15 s without data)
        """
        cache = price_cache if price_cache is not None else request.app.state.price_cache
        return StreamingResponse(
            generate_events(cache, request, interval),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",  # disable nginx buffering if proxied
            },
        )

    return router


async def generate_events(
    cache: PriceCache,
    request: Any,
    interval: float = STREAM_INTERVAL,
    keepalive: float = KEEPALIVE_SECONDS,
) -> AsyncGenerator[str, None]:
    """Yield SSE frames until the client disconnects.

    - A `data:` frame holding the full map is sent only when cache.version changes;
      an empty map (`data: {}`) is sent when the last ticker is removed.
    - A `: ping` comment is sent after `keepalive` seconds without data so proxies
      don't close a quiet stream (Massive mode updates only every 15 s or hourly).
    """
    yield "retry: 1000\n\n"  # EventSource reconnects after 1 s
    client = request.client.host if request.client else "unknown"
    logger.info("SSE connected: %s", client)

    last_version = -1
    idle = 0.0
    try:
        while not await request.is_disconnected():
            version = cache.version
            if version != last_version:
                last_version = version
                payload = {t: u.to_dict() for t, u in cache.get_all().items()}
                yield f"data: {json.dumps(payload, separators=(',', ':'))}\n\n"
                idle = 0.0
            elif idle >= keepalive:
                yield ": ping\n\n"
                idle = 0.0
            await asyncio.sleep(interval)
            idle += interval
    finally:
        logger.info("SSE disconnected: %s", client)
