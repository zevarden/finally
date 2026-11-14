"""Mock-mode rules (TEAM_CONTRACT §6). The exact strings are relied on by the E2E tests."""

import pytest

from app.llm import mock_response


@pytest.mark.parametrize("text,qty,ticker,msg", [
    ("buy 5 AAPL", 5, "AAPL", "Mock: buying 5 AAPL."),
    ("BUY 5 aapl", 5, "AAPL", "Mock: buying 5 AAPL."),
    ("please buy 2.5 tsla.", 2.5, "TSLA", "Mock: buying 2.5 TSLA."),
    ("Buy 10 BRK.B", 10, "BRK.B", "Mock: buying 10 BRK.B."),
])
def test_buy(text, qty, ticker, msg):
    r = mock_response(text, 10000)
    assert r.message == msg
    assert [(t.side, t.quantity, t.ticker) for t in r.trades] == [("buy", qty, ticker)]
    assert r.watchlist_changes == []


def test_sell():
    r = mock_response("Sell 3 nvda", 10000)
    assert r.message == "Mock: selling 3 NVDA."
    assert [(t.side, t.quantity, t.ticker) for t in r.trades] == [("sell", 3, "NVDA")]


@pytest.mark.parametrize("text", ["add PYPL", "Watch pypl", "add pypl to my watchlist"])
def test_add(text):
    r = mock_response(text, 10000)
    assert r.message == "Mock: adding PYPL to your watchlist."
    assert [(w.action, w.ticker) for w in r.watchlist_changes] == [("add", "PYPL")]
    assert r.trades == []


@pytest.mark.parametrize("text", [
    "remove NFLX", "unwatch nflx", "Remove nflx from my watchlist",
    "remove NFLX from my watch list",
])
def test_remove(text):
    r = mock_response(text, 10000)
    assert r.message == "Mock: removing NFLX from your watchlist."
    assert [(w.action, w.ticker) for w in r.watchlist_changes] == [("remove", "NFLX")]


def test_first_matching_rule_wins():
    r = mock_response("sell 1 AAPL then buy 2 MSFT", 10000)
    assert r.message == "Mock: buying 2 MSFT."  # rule 1 (buy) is checked before rule 2


@pytest.mark.parametrize("text,cash,expected", [
    ("hello", 10000.0, "$10,000.00"),
    ("what should I buy?", 8123.456, "$8,123.46"),
    ("how am I doing", 0.5, "$0.50"),
])
def test_fallback(text, cash, expected):
    r = mock_response(text, cash)
    assert r.message == (
        f"Mock response: I'm FinAlly, your AI trading assistant. You have {expected} in cash."
    )
    assert r.trades == [] and r.watchlist_changes == []
