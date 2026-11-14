"""Accessors for objects the lifespan stores on app.state."""

from __future__ import annotations

from fastapi import Request

from app.market import MarketDataSource, PriceCache


def get_db(request: Request):
    return request.app.state.db


def get_cache(request: Request) -> PriceCache:
    return request.app.state.price_cache


def get_source(request: Request) -> MarketDataSource:
    return request.app.state.market_source
