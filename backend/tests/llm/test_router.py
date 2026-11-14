"""POST /api/chat and GET /api/chat/history, with real services and a real Database."""


def _reply(fake_llm, *trades, changes=(), message="On it."):
    fake_llm.queue({"message": message,
                    "trades": [dict(zip(("ticker", "side", "quantity"), t)) for t in trades],
                    "watchlist_changes": [dict(zip(("ticker", "action"), c)) for c in changes]})


FALLBACK_10K = "Mock response: I'm FinAlly, your AI trading assistant. You have $10,000.00 in cash."


# --------------------------------------------------------------------------- mock mode

def test_mock_buy_really_executes(api, db, mock_mode):
    r = api.post("/api/chat", json={"message": "buy 5 AAPL"})
    assert r.status_code == 200
    body = r.json()
    assert body["role"] == "assistant"
    assert body["content"] == "Mock: buying 5 AAPL."
    assert body["id"] and body["created_at"]
    assert body["actions"] == {
        "trades": [{"ticker": "AAPL", "side": "buy", "quantity": 5, "status": "executed",
                    "price": 190.0, "error": None}],
        "watchlist_changes": [],
    }
    assert db.get_position("AAPL")["quantity"] == 5
    assert db.get_cash() == 10000 - 5 * 190


def test_mock_sell_without_shares_fails_but_replies(api, db, mock_mode):
    r = api.post("/api/chat", json={"message": "sell 3 TSLA"})
    assert r.status_code == 200
    body = r.json()
    assert body["content"] == "Mock: selling 3 TSLA."
    t = body["actions"]["trades"][0]
    assert t["status"] == "failed" and "Insufficient shares" in t["error"]


def test_mock_insufficient_cash(api, db, mock_mode):
    body = api.post("/api/chat", json={"message": "buy 1000 NFLX"}).json()
    assert body["content"] == "Mock: buying 1000 NFLX."
    t = body["actions"]["trades"][0]
    assert t["status"] == "failed" and "Insufficient cash" in t["error"]
    assert db.get_cash() == 10000


def test_mock_watchlist_add_and_duplicate(api, db, mock_mode):
    body = api.post("/api/chat", json={"message": "add PYPL"}).json()
    assert body["content"] == "Mock: adding PYPL to your watchlist."
    assert body["actions"]["watchlist_changes"] == [
        {"ticker": "PYPL", "action": "add", "status": "executed", "error": None}]
    assert "PYPL" in [w["ticker"] for w in db.list_watchlist()]

    dup = api.post("/api/chat", json={"message": "watch pypl"}).json()["actions"]
    w = dup["watchlist_changes"][0]
    assert w["status"] == "failed" and "already on the watchlist" in w["error"]


def test_mock_remove(api, db, mock_mode):
    body = api.post("/api/chat", json={"message": "remove NFLX"}).json()
    assert body["content"] == "Mock: removing NFLX from your watchlist."
    assert body["actions"]["watchlist_changes"][0]["status"] == "executed"
    assert "NFLX" not in [w["ticker"] for w in db.list_watchlist()]


def test_mock_fallback_has_no_actions(api, mock_mode):
    body = api.post("/api/chat", json={"message": "hello"}).json()
    assert body["content"] == FALLBACK_10K
    assert body["actions"] is None


# --------------------------------------------------------------------------- real path

def test_llm_actions_execute_with_partial_failure(api, db, fake_llm):
    _reply(fake_llm, ("AAPL", "buy", 2), ("MSFT", "buy", 1000),
           changes=[("PYPL", "add"), ("AAPL", "add")], message="Buying AAPL and MSFT.")
    body = api.post("/api/chat", json={"message": "buy some stuff"}).json()
    trades, changes = body["actions"]["trades"], body["actions"]["watchlist_changes"]
    assert [t["status"] for t in trades] == ["executed", "failed"]
    assert trades[0]["price"] == 190.0
    assert "Insufficient cash" in trades[1]["error"]
    assert [c["status"] for c in changes] == ["executed", "failed"]
    assert "already on the watchlist" in changes[1]["error"]
    # the reply is annotated with what did not happen
    assert body["content"].startswith("Buying AAPL and MSFT.")
    assert "Could not buy 1000 MSFT" in body["content"]
    assert "Could not add AAPL" in body["content"]
    assert db.get_position("AAPL")["quantity"] == 2


def test_prompt_includes_context_and_history(api, db, fake_llm):
    _reply(fake_llm, ("AAPL", "buy", 1))
    api.post("/api/chat", json={"message": "first"})
    fake_llm.queue({"message": "second reply"})
    api.post("/api/chat", json={"message": "second"})

    msgs = fake_llm.calls[1]["messages"]
    assert msgs[0]["role"] == "system"
    assert "FinAlly" in msgs[0]["content"]
    assert "AAPL | 1 |" in msgs[0]["content"]           # position with P&L
    assert "NFLX | $650.00" in msgs[0]["content"]       # watchlist with live prices
    assert msgs[1] == {"role": "user", "content": "first"}
    assert msgs[2]["role"] == "assistant" and "executed actions" in msgs[2]["content"]
    assert msgs[-1] == {"role": "user", "content": "second"}


def test_prompt_history_is_limited(api, db, fake_llm):
    for i in range(30):
        db.insert_chat_message("user", f"old {i}")
    fake_llm.queue({"message": "ok"})
    api.post("/api/chat", json={"message": "new"})
    msgs = fake_llm.calls[0]["messages"]
    assert len(msgs) == 1 + 20 + 1
    assert msgs[1]["content"] == "old 10"


def test_malformed_twice_gives_friendly_reply(api, db, fake_llm):
    fake_llm.queue("nope", "{still not json")
    r = api.post("/api/chat", json={"message": "buy everything"})
    assert r.status_code == 200
    body = r.json()
    assert body["actions"] is None
    assert "try asking again" in body["content"]
    assert db.list_trades() == []


def test_llm_failure_is_502_and_stores_nothing(api, db, fake_llm):
    fake_llm.queue(RuntimeError("provider down"))
    r = api.post("/api/chat", json={"message": "hi"})
    assert r.status_code == 502
    assert "unavailable" in r.json()["detail"]
    assert db.list_chat_messages() == []


def test_blank_message_is_422(api, mock_mode):
    assert api.post("/api/chat", json={"message": "   "}).status_code == 422
    assert api.post("/api/chat", json={}).status_code == 422


# --------------------------------------------------------------------------- history

def test_history_roundtrip(api, mock_mode):
    assert api.get("/api/chat/history").json() == {"messages": []}
    api.post("/api/chat", json={"message": "buy 1 AAPL"})
    api.post("/api/chat", json={"message": "hello"})
    msgs = api.get("/api/chat/history").json()["messages"]
    assert [(m["role"], m["content"]) for m in msgs] == [
        ("user", "buy 1 AAPL"), ("assistant", "Mock: buying 1 AAPL."),
        ("user", "hello"),
        ("assistant", "Mock response: I'm FinAlly, your AI trading assistant. "
                      "You have $9,810.00 in cash."),
    ]
    assert msgs[0]["actions"] is None
    assert msgs[1]["actions"]["trades"][0]["status"] == "executed"
    assert set(msgs[0]) == {"id", "role", "content", "actions", "created_at"}


def test_history_limited_to_50(api, db):
    for i in range(60):
        db.insert_chat_message("user", f"m{i}")
    msgs = api.get("/api/chat/history").json()["messages"]
    assert len(msgs) == 50 and msgs[0]["content"] == "m10" and msgs[-1]["content"] == "m59"
