"""GET /api/health."""

from __future__ import annotations

from fastapi import APIRouter, Request

from .deps import get_source


def create_health_router() -> APIRouter:
    router = APIRouter(prefix="/api", tags=["system"])

    @router.get("/health")
    async def health(request: Request) -> dict:
        source = getattr(request.app.state, "market_source", None)
        return {"status": "ok", "market": get_source(request).status() if source else None}

    return router
