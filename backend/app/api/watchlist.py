"""Watchlist routes: GET/POST /api/watchlist, DELETE /api/watchlist/{ticker}."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

from app.services import WatchlistError, add_ticker, get_watchlist, remove_ticker

from .deps import get_cache, get_db, get_source


class WatchlistAdd(BaseModel):
    ticker: str


def create_watchlist_router() -> APIRouter:
    router = APIRouter(prefix="/api/watchlist", tags=["watchlist"])

    @router.get("")
    async def list_watchlist(request: Request) -> dict:
        return {"tickers": get_watchlist(get_db(request), get_cache(request))}

    @router.post("", status_code=201)
    async def add(body: WatchlistAdd, request: Request) -> dict:
        try:
            return await add_ticker(
                get_db(request), get_cache(request), get_source(request), body.ticker
            )
        except WatchlistError as e:
            raise HTTPException(status_code=e.status_code, detail=str(e)) from None

    @router.delete("/{ticker}", status_code=204)
    async def remove(ticker: str, request: Request) -> Response:
        try:
            await remove_ticker(get_db(request), get_cache(request), get_source(request), ticker)
        except WatchlistError as e:
            raise HTTPException(status_code=e.status_code, detail=str(e)) from None
        return Response(status_code=204)

    return router
