"""Prompt construction."""

from app.llm.prompt import SYSTEM_PROMPT, build_messages, build_portfolio_context

PORTFOLIO = {
    "cash_balance": 8100.0, "positions_value": 1950.0, "total_value": 10050.0,
    "unrealized_pnl": 50.0, "unrealized_pnl_percent": 2.63,
    "positions": [{"ticker": "AAPL", "quantity": 10.0, "avg_cost": 190.0, "current_price": 195.0,
                   "market_value": 1950.0, "unrealized_pnl": 50.0,
                   "unrealized_pnl_percent": 2.63, "weight": 0.194}],
}
WATCHLIST = [
    {"ticker": "AAPL", "price": 195.0, "day_change_percent": 1.2},
    {"ticker": "PYPL", "price": None, "day_change_percent": None},
]


def test_context_contains_portfolio_and_watchlist():
    ctx = build_portfolio_context(PORTFOLIO, WATCHLIST)
    for s in ("$8,100.00", "$10,050.00", "+2.63%", "AAPL | 10 | $190.00 | $195.00",
              "19.4%", "PYPL | n/a | n/a", "+1.20%"):
        assert s in ctx


def test_context_empty_portfolio():
    ctx = build_portfolio_context(
        {"cash_balance": 10000.0, "positions_value": 0, "total_value": 10000.0,
         "unrealized_pnl": 0, "unrealized_pnl_percent": 0, "positions": []}, [])
    assert "Positions: none" in ctx and "Watchlist: empty" in ctx


def test_messages_order_and_history():
    history = [
        {"role": "user", "content": "buy 5 AAPL", "actions": None},
        {"role": "assistant", "content": "Buying.", "actions": {"trades": [{"ticker": "AAPL"}],
                                                                "watchlist_changes": []}},
    ]
    msgs = build_messages("CTX", history, "how am I doing?")
    assert msgs[0]["role"] == "system"
    assert "FinAlly, an AI trading assistant" in msgs[0]["content"] and "CTX" in msgs[0]["content"]
    assert SYSTEM_PROMPT in msgs[0]["content"]
    assert [m["role"] for m in msgs[1:]] == ["user", "assistant", "user"]
    assert "executed actions" in msgs[2]["content"]
    assert msgs[-1] == {"role": "user", "content": "how am I doing?"}
