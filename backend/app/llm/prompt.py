"""System prompt and message-list construction for the chat LLM."""

from __future__ import annotations

import json
from typing import Any

SYSTEM_PROMPT = """You are FinAlly, an AI trading assistant inside a simulated trading workstation.
The user trades a simulated portfolio with fake money; market orders fill instantly at the
current price, with no fees. Fractional shares are allowed.

Your job:
- Analyze the portfolio: composition, risk concentration, diversification and P&L.
- Suggest trades with brief, data-driven reasoning.
- Execute trades when the user asks for them or agrees to a suggestion, by listing them in
  "trades". Never put a trade in "trades" that the user has not asked for or agreed to.
- Manage the watchlist proactively via "watchlist_changes" (add tickers the user mentions
  or is interested in; remove ones they no longer want).
- Only buy what the available cash covers and only sell shares the user holds.
- Be concise: a few short sentences or a compact list. Use the numbers in the context below.

Always respond with a single JSON object, and nothing else, of the form:
{"message": "<your reply to the user>",
 "trades": [{"ticker": "AAPL", "side": "buy" | "sell", "quantity": <number > 0>}],
 "watchlist_changes": [{"ticker": "PYPL", "action": "add" | "remove"}]}
Use empty arrays when there are no actions. Tickers are uppercase symbols.
Describe actions in "message" as being placed (e.g. "Buying 5 AAPL"); the system executes
them and reports any failure to the user."""


def _money(v: Any) -> str:
    return "n/a" if v is None else f"${v:,.2f}"


def _pct(v: Any) -> str:
    return "n/a" if v is None else f"{v:+.2f}%"


def build_portfolio_context(portfolio: dict, watchlist: list[dict]) -> str:
    """Render the portfolio (get_portfolio shape) and priced watchlist as compact text."""
    lines = [
        "CURRENT PORTFOLIO",
        f"Cash: {_money(portfolio.get('cash_balance'))}",
        f"Positions value: {_money(portfolio.get('positions_value'))}",
        f"Total value: {_money(portfolio.get('total_value'))}",
        f"Unrealized P&L: {_money(portfolio.get('unrealized_pnl'))} "
        f"({_pct(portfolio.get('unrealized_pnl_percent'))})",
    ]
    positions = portfolio.get("positions") or []
    if positions:
        lines.append("Positions (ticker | qty | avg cost | price | value | P&L | weight):")
        for p in positions:
            weight = p.get("weight")
            lines.append(
                f"- {p['ticker']} | {p['quantity']:g} | {_money(p.get('avg_cost'))} | "
                f"{_money(p.get('current_price'))} | {_money(p.get('market_value'))} | "
                f"{_money(p.get('unrealized_pnl'))} ({_pct(p.get('unrealized_pnl_percent'))}) | "
                f"{'n/a' if weight is None else f'{weight * 100:.1f}%'}"
            )
    else:
        lines.append("Positions: none")

    if watchlist:
        lines.append("Watchlist (ticker | price | change today):")
        for w in watchlist:
            lines.append(
                f"- {w['ticker']} | {_money(w.get('price'))} | {_pct(w.get('day_change_percent'))}"
            )
    else:
        lines.append("Watchlist: empty")
    return "\n".join(lines)


def _history_content(msg: dict) -> str:
    content = msg.get("content") or ""
    actions = msg.get("actions")
    if msg.get("role") == "assistant" and actions:
        content += "\n[executed actions: " + json.dumps(actions, separators=(",", ":")) + "]"
    return content


def build_messages(
    context: str, history: list[dict], user_message: str
) -> list[dict[str, str]]:
    """System prompt + context, prior turns (oldest first), then the new user message."""
    messages: list[dict[str, str]] = [
        {"role": "system", "content": f"{SYSTEM_PROMPT}\n\n{context}"}
    ]
    for msg in history:
        if msg.get("role") in ("user", "assistant"):
            messages.append({"role": msg["role"], "content": _history_content(msg)})
    messages.append({"role": "user", "content": user_message})
    return messages
