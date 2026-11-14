"""Structured output parsing."""

import json

import pytest

from app.llm import ParseError, parse_llm_response
from app.llm.models import RESPONSE_FORMAT, RESPONSE_SCHEMA


def test_full_valid_response():
    raw = json.dumps({
        "message": "Buying 5 AAPL.",
        "trades": [{"ticker": "aapl ", "side": "BUY", "quantity": 5}],
        "watchlist_changes": [{"ticker": "pypl", "action": "Add"}],
    })
    r = parse_llm_response(raw)
    assert r.message == "Buying 5 AAPL."
    assert r.trades[0].ticker == "AAPL" and r.trades[0].side == "buy" and r.trades[0].quantity == 5
    assert r.watchlist_changes[0].ticker == "PYPL" and r.watchlist_changes[0].action == "add"


@pytest.mark.parametrize("payload", [
    {"message": "hi"},
    {"message": "hi", "trades": None, "watchlist_changes": None},
    {"message": "hi", "trades": [], "watchlist_changes": []},
])
def test_optional_fields_default_to_empty(payload):
    r = parse_llm_response(json.dumps(payload))
    assert r.message == "hi" and r.trades == [] and r.watchlist_changes == []


def test_fractional_quantity_and_numeric_string():
    r = parse_llm_response(json.dumps(
        {"message": "ok", "trades": [{"ticker": "V", "side": "sell", "quantity": "2.5"}]}))
    assert r.trades[0].quantity == 2.5


def test_code_fenced_json_is_accepted():
    r = parse_llm_response('```json\n{"message": "fenced"}\n```')
    assert r.message == "fenced"


def test_extra_fields_ignored():
    r = parse_llm_response(json.dumps({"message": "ok", "confidence": 0.9}))
    assert r.message == "ok"


@pytest.mark.parametrize("raw", [
    None, "", "   ", "not json", '{"message": "unterminated', "[1, 2]", '"just a string"',
])
def test_malformed_json(raw):
    with pytest.raises(ParseError):
        parse_llm_response(raw)


@pytest.mark.parametrize("payload", [
    {},                                                   # missing message
    {"message": ""},                                      # empty message
    {"message": 42},                                      # wrong type
    {"message": "x", "trades": "buy AAPL"},               # trades not a list
    {"message": "x", "trades": [{"ticker": "AAPL", "side": "hold", "quantity": 1}]},
    {"message": "x", "trades": [{"ticker": "AAPL", "side": "buy", "quantity": "lots"}]},
    {"message": "x", "trades": [{"ticker": "AAPL", "side": "buy"}]},
    {"message": "x", "watchlist_changes": [{"ticker": "PYPL", "action": "delete"}]},
    {"message": "x", "watchlist_changes": [{"action": "add"}]},
])
def test_wrong_types_rejected(payload):
    with pytest.raises(ParseError):
        parse_llm_response(json.dumps(payload))


def test_schema_is_strict_compatible():
    assert RESPONSE_FORMAT["type"] == "json_schema"
    assert RESPONSE_FORMAT["json_schema"]["strict"] is True
    assert set(RESPONSE_SCHEMA["required"]) == set(RESPONSE_SCHEMA["properties"])
    assert RESPONSE_SCHEMA["additionalProperties"] is False
