"""Deterministic mock LLM (LLM_MOCK=true). Rules are fixed by TEAM_CONTRACT.md §6."""

from __future__ import annotations

import re

from .models import LLMResponse, TradeAction, WatchlistChange

_TICKER = r"([A-Za-z][A-Za-z0-9.\-]{0,9})"
_QTY = r"(\d+(?:\.\d+)?|\.\d+)"

_BUY = re.compile(rf"\bbuy\s+{_QTY}\s+{_TICKER}", re.IGNORECASE)
_SELL = re.compile(rf"\bsell\s+{_QTY}\s+{_TICKER}", re.IGNORECASE)
# (?!list\b): "remove X from my watch list" must not be read as "watch LIST".
_ADD = re.compile(rf"\b(?:add|watch)\s+(?!list\b){_TICKER}", re.IGNORECASE)
_REMOVE = re.compile(rf"\b(?:remove|unwatch)\s+{_TICKER}", re.IGNORECASE)


def _ticker(raw: str) -> str:
    # "buy 5 AAPL." -> AAPL (trailing sentence punctuation is not part of the symbol)
    return raw.rstrip(".-").upper()


def format_quantity(qty: float) -> str:
    """5.0 -> '5', 2.5 -> '2.5'."""
    if qty == int(qty):
        return str(int(qty))
    return f"{qty:f}".rstrip("0").rstrip(".")


def format_cash(cash: float) -> str:
    return f"${cash:,.2f}"


def mock_response(user_message: str, cash: float) -> LLMResponse:
    """Apply the first matching rule to the user's message."""
    if m := _BUY.search(user_message):
        qty, ticker = float(m.group(1)), _ticker(m.group(2))
        return LLMResponse(
            message=f"Mock: buying {format_quantity(qty)} {ticker}.",
            trades=[TradeAction(ticker=ticker, side="buy", quantity=qty)],
        )
    if m := _SELL.search(user_message):
        qty, ticker = float(m.group(1)), _ticker(m.group(2))
        return LLMResponse(
            message=f"Mock: selling {format_quantity(qty)} {ticker}.",
            trades=[TradeAction(ticker=ticker, side="sell", quantity=qty)],
        )
    if m := _ADD.search(user_message):
        ticker = _ticker(m.group(1))
        return LLMResponse(
            message=f"Mock: adding {ticker} to your watchlist.",
            watchlist_changes=[WatchlistChange(ticker=ticker, action="add")],
        )
    if m := _REMOVE.search(user_message):
        ticker = _ticker(m.group(1))
        return LLMResponse(
            message=f"Mock: removing {ticker} from your watchlist.",
            watchlist_changes=[WatchlistChange(ticker=ticker, action="remove")],
        )
    return LLMResponse(
        message=(
            "Mock response: I'm FinAlly, your AI trading assistant. "
            f"You have {format_cash(cash)} in cash."
        )
    )
