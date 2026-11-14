"""Chat API: POST /api/chat and GET /api/chat/history (TEAM_CONTRACT.md §5, §6)."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, field_validator

from app.services import (
    TradeError,
    WatchlistError,
    add_ticker,
    execute_trade,
    get_portfolio,
    get_watchlist,
    remove_ticker,
)

from . import client
from .mock import mock_response
from .models import LLMResponse
from .prompt import build_messages, build_portfolio_context

logger = logging.getLogger(__name__)

HISTORY_FOR_PROMPT = 20
HISTORY_FOR_UI = 50


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)

    @field_validator("message")
    @classmethod
    def _strip(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("message must not be blank")
        return v


def _public(row: dict) -> dict:
    return {k: row.get(k) for k in ("id", "role", "content", "actions", "created_at")}


async def execute_actions(db, cache, source, response: LLMResponse) -> dict | None:
    """Run each requested action through app.services. Failures are recorded, not raised."""
    trades: list[dict[str, Any]] = []
    for t in response.trades:
        entry: dict[str, Any] = {
            "ticker": t.ticker, "side": t.side, "quantity": t.quantity,
            "status": "executed", "price": None, "error": None,
        }
        try:
            result = await execute_trade(db, cache, source, t.ticker, t.side, t.quantity)
            trade = result["trade"]
            entry.update(ticker=trade["ticker"], quantity=trade["quantity"], price=trade["price"])
        except TradeError as exc:
            entry.update(status="failed", error=str(exc))
        except Exception as exc:  # never let one action break the whole chat turn
            logger.exception("Unexpected error executing LLM trade %s", t)
            entry.update(status="failed", error=f"Trade failed: {exc}")
        trades.append(entry)

    changes: list[dict[str, Any]] = []
    for w in response.watchlist_changes:
        entry = {"ticker": w.ticker, "action": w.action, "status": "executed", "error": None}
        try:
            if w.action == "add":
                await add_ticker(db, cache, source, w.ticker)
            else:
                await remove_ticker(db, cache, source, w.ticker)
        except WatchlistError as exc:
            entry.update(status="failed", error=str(exc))
        except Exception as exc:
            logger.exception("Unexpected error applying LLM watchlist change %s", w)
            entry.update(status="failed", error=f"Watchlist change failed: {exc}")
        changes.append(entry)

    if not trades and not changes:
        return None
    return {"trades": trades, "watchlist_changes": changes}


def _failure_note(actions: dict | None) -> str:
    """One line per failed action, appended to a real-LLM reply (which was written
    before execution) so the user isn't told something happened when it didn't."""
    if not actions:
        return ""
    notes = []
    for t in actions["trades"]:
        if t["status"] == "failed":
            notes.append(f"Could not {t['side']} {t['quantity']:g} {t['ticker']}: {t['error']}")
    for w in actions["watchlist_changes"]:
        if w["status"] == "failed":
            verb = "add" if w["action"] == "add" else "remove"
            notes.append(f"Could not {verb} {w['ticker']}: {w['error']}")
    return "\n\n" + "\n".join(f"Note: {n}" for n in notes) if notes else ""


def create_chat_router() -> APIRouter:
    router = APIRouter(prefix="/api/chat", tags=["chat"])

    @router.post("")
    async def chat(body: ChatRequest, request: Request) -> dict:
        state = request.app.state
        db, cache, source = state.db, state.price_cache, state.market_source
        mock = client.is_mock_mode()

        if mock:
            response = mock_response(body.message, db.get_cash())
        else:
            context = build_portfolio_context(get_portfolio(db, cache), get_watchlist(db, cache))
            history = db.list_chat_messages(limit=HISTORY_FOR_PROMPT)
            messages = build_messages(context, history, body.message)
            try:
                response = await client.request_structured_response(messages)
            except client.LLMUnavailableError as exc:
                raise HTTPException(
                    status_code=502,
                    detail="The AI assistant is unavailable right now. Please try again shortly.",
                ) from exc

        db.insert_chat_message("user", body.message)
        actions = await execute_actions(db, cache, source, response)
        content = response.message if mock else response.message + _failure_note(actions)
        row = db.insert_chat_message("assistant", content, actions)
        return _public(row)

    @router.get("/history")
    async def history(request: Request) -> dict:
        rows = request.app.state.db.list_chat_messages(limit=HISTORY_FOR_UI)
        return {"messages": [_public(r) for r in rows]}

    return router
