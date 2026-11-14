"""Fixtures for the LLM chat layer: a real Database on tmp_path, a fixed-price market
source, and a minimal FastAPI app with only the chat router mounted."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.db import Database
from app.llm import client as llm_client
from app.llm import create_chat_router
from app.market import MarketDataSource, PriceCache, normalize_ticker

PRICES = {
    "AAPL": 190.0, "GOOGL": 175.0, "MSFT": 420.0, "AMZN": 185.0, "TSLA": 250.0,
    "NVDA": 120.0, "META": 500.0, "JPM": 200.0, "V": 280.0, "NFLX": 650.0, "PYPL": 70.0,
}


class FixedSource(MarketDataSource):
    """Prices from a fixed table; tickers not in it are unknown (never cached)."""

    def __init__(self, cache: PriceCache) -> None:
        self.cache = cache
        self.tickers: list[str] = []

    async def start(self, tickers, initial_prices=None) -> None:
        for t in tickers:
            await self.add_ticker(t)

    async def stop(self) -> None:
        pass

    async def add_ticker(self, ticker: str) -> None:
        t = normalize_ticker(ticker)
        if t not in self.tickers:
            self.tickers.append(t)
        if t in PRICES:
            self.cache.update(t, PRICES[t], session_open=PRICES[t])

    async def remove_ticker(self, ticker: str) -> None:
        t = normalize_ticker(ticker)
        if t in self.tickers:
            self.tickers.remove(t)
        self.cache.remove(t)

    def get_tickers(self) -> list[str]:
        return list(self.tickers)

    def status(self) -> dict[str, Any]:
        return {"source": "fixed", "mode": "simulated", "tickers": len(self.tickers),
                "last_success": None, "last_error": None}


def fake_completion(content: str | None) -> SimpleNamespace:
    """Shape of a litellm ModelResponse, as far as the client reads it."""
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])


class FakeLLM:
    """Replaces client._acompletion. Pops queued replies; an Exception instance is raised."""

    def __init__(self) -> None:
        self.replies: list[Any] = []
        self.calls: list[dict] = []

    def queue(self, *replies: Any) -> FakeLLM:
        for r in replies:
            self.replies.append(json.dumps(r) if isinstance(r, dict) else r)
        return self

    async def __call__(self, **kwargs: Any) -> SimpleNamespace:
        self.calls.append(kwargs)
        reply = self.replies.pop(0)
        if isinstance(reply, BaseException):
            raise reply
        return fake_completion(reply)


@pytest.fixture(autouse=True)
def _llm_env(monkeypatch, tmp_path):
    for var in ("LLM_MOCK", "LITELLM_API_BASE", "LLM_MODEL", "LITELLM_API_KEY",
                "FINALLY_IN_DOCKER"):
        monkeypatch.delenv(var, raising=False)
    # Not in a container unless a test says so, whatever machine runs the suite.
    monkeypatch.setattr(llm_client, "DOCKERENV_PATH", str(tmp_path / "no-dockerenv"))
    monkeypatch.setenv("LITELLM_MASTER_KEY", "test-key")


@pytest.fixture
def fake_llm(monkeypatch) -> FakeLLM:
    fake = FakeLLM()
    monkeypatch.setattr(llm_client, "_acompletion", fake)
    return fake


@pytest.fixture
def mock_mode(monkeypatch):
    monkeypatch.setenv("LLM_MOCK", "true")

    async def _no_network(**kwargs):  # mock mode must never reach the network
        raise AssertionError("LLM called in mock mode")

    monkeypatch.setattr(llm_client, "_acompletion", _no_network)


@pytest.fixture
def db(tmp_path) -> Database:
    return Database(tmp_path / "test.db")


@pytest.fixture
def app(db) -> FastAPI:
    app = FastAPI()
    app.include_router(create_chat_router())
    cache = PriceCache()
    app.state.db = db
    app.state.price_cache = cache
    app.state.market_source = FixedSource(cache)
    return app


@pytest.fixture
def api(app, db):
    with TestClient(app) as c:
        import anyio

        anyio.run(app.state.market_source.start, [w["ticker"] for w in db.list_watchlist()])
        yield c
