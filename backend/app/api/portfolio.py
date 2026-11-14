"""Portfolio routes: GET /api/portfolio, POST /api/portfolio/trade, GET /api/portfolio/history."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.services import TradeError, execute_trade, get_portfolio

from .deps import get_cache, get_db, get_source


class TradeRequest(BaseModel):
    ticker: str
    quantity: float
    side: str


def create_portfolio_router() -> APIRouter:
    router = APIRouter(prefix="/api/portfolio", tags=["portfolio"])

    @router.get("")
    async def portfolio(request: Request) -> dict:
        return get_portfolio(get_db(request), get_cache(request))

    @router.post("/trade")
    async def trade(body: TradeRequest, request: Request) -> dict:
        try:
            return await execute_trade(
                get_db(request),
                get_cache(request),
                get_source(request),
                body.ticker,
                body.side,
                body.quantity,
            )
        except TradeError as e:
            raise HTTPException(status_code=400, detail=str(e)) from None

    @router.get("/history")
    async def history(request: Request) -> dict:
        snapshots = get_db(request).list_snapshots()
        return {
            "snapshots": [
                {"total_value": s["total_value"], "recorded_at": s["recorded_at"]}
                for s in snapshots
            ]
        }

    return router
