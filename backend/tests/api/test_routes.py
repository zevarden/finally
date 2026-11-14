import pytest


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["market"]["source"] == "fake"
    assert body["market"]["tickers"] == 10


def test_portfolio_initial(client):
    r = client.get("/api/portfolio")
    assert r.status_code == 200
    assert r.json() == {"cash_balance": 10000.0, "positions_value": 0.0, "total_value": 10000.0,
                        "unrealized_pnl": 0.0, "unrealized_pnl_percent": 0.0, "positions": []}


def test_buy_then_sell(client):
    r = client.post("/api/portfolio/trade", json={"ticker": "aapl", "quantity": 10, "side": "buy"})
    assert r.status_code == 200
    body = r.json()
    assert set(body["trade"]) >= {"id", "ticker", "side", "quantity", "price", "executed_at"}
    assert body["trade"]["ticker"] == "AAPL"
    assert body["portfolio"]["cash_balance"] == 8100.0
    assert body["portfolio"]["positions"][0]["quantity"] == 10

    r = client.post("/api/portfolio/trade", json={"ticker": "AAPL", "quantity": 10, "side": "sell"})
    assert r.status_code == 200
    assert r.json()["portfolio"]["positions"] == []
    assert client.get("/api/portfolio").json()["cash_balance"] == 10000.0


def test_fractional_trade(client):
    r = client.post("/api/portfolio/trade", json={"ticker": "AAPL", "quantity": 0.5, "side": "buy"})
    assert r.status_code == 200
    assert r.json()["portfolio"]["cash_balance"] == 9905.0


@pytest.mark.parametrize(
    "body, match",
    [
        ({"ticker": "NFLX", "quantity": 100, "side": "buy"}, "Insufficient cash"),
        ({"ticker": "AAPL", "quantity": 1, "side": "sell"}, "Insufficient shares"),
        ({"ticker": "AAPL", "quantity": 0, "side": "buy"}, "Quantity"),
        ({"ticker": "AAPL", "quantity": 1, "side": "short"}, "Side"),
        ({"ticker": "12$", "quantity": 1, "side": "buy"}, "Invalid ticker"),
        ({"ticker": "ZZZZ", "quantity": 1, "side": "buy"}, "No price"),
    ],
)
def test_trade_errors_are_400(client, body, match):
    r = client.post("/api/portfolio/trade", json=body)
    assert r.status_code == 400
    assert match in r.json()["detail"]


@pytest.mark.parametrize(
    "body",
    [{}, {"ticker": "AAPL", "side": "buy"}, {"ticker": "AAPL", "quantity": "lots", "side": "buy"}],
)
def test_trade_malformed_body_is_422(client, body):
    assert client.post("/api/portfolio/trade", json=body).status_code == 422


def test_history_shape(client):
    client.post("/api/portfolio/trade", json={"ticker": "AAPL", "quantity": 1, "side": "buy"})
    r = client.get("/api/portfolio/history")
    assert r.status_code == 200
    snaps = r.json()["snapshots"]
    assert len(snaps) == 2  # startup snapshot + post-trade snapshot
    assert all(set(s) == {"total_value", "recorded_at"} for s in snaps)
    assert snaps[0]["recorded_at"] <= snaps[1]["recorded_at"]


def test_watchlist_get(client):
    r = client.get("/api/watchlist")
    assert r.status_code == 200
    tickers = r.json()["tickers"]
    assert [t["ticker"] for t in tickers][:3] == ["AAPL", "GOOGL", "MSFT"]
    assert set(tickers[0]) == {"ticker", "added_at", "price", "previous_price",
                               "change_percent", "day_change_percent", "direction"}
    assert tickers[0]["price"] == 190.0


def test_watchlist_add_and_remove(client):
    r = client.post("/api/watchlist", json={"ticker": "pypl"})
    assert r.status_code == 201
    assert r.json()["ticker"] == "PYPL" and r.json()["price"] == 70.0
    assert "PYPL" in [t["ticker"] for t in client.get("/api/watchlist").json()["tickers"]]

    r = client.delete("/api/watchlist/pypl")
    assert r.status_code == 204
    assert r.content == b""
    assert "PYPL" not in [t["ticker"] for t in client.get("/api/watchlist").json()["tickers"]]


@pytest.mark.parametrize("ticker, status", [("bad ticker!", 400), ("ZZZZ", 404), ("AAPL", 409)])
def test_watchlist_add_errors(client, ticker, status):
    r = client.post("/api/watchlist", json={"ticker": ticker})
    assert r.status_code == status
    assert r.json()["detail"]


def test_watchlist_add_malformed_is_422(client):
    assert client.post("/api/watchlist", json={}).status_code == 422


def test_watchlist_delete_not_listed_is_404(client):
    assert client.delete("/api/watchlist/PYPL").status_code == 404


def test_static_index_served(make_client, static_dir):
    client = make_client(static_dir=static_dir)
    r = client.get("/")
    assert r.status_code == 200
    assert "FinAlly" in r.text
    assert client.get("/api/health").status_code == 200  # API still wins over static


def test_no_static_dir_api_only(client):
    assert client.get("/").status_code == 404
    assert client.get("/api/health").status_code == 200


def test_unknown_api_route_404(make_client, static_dir):
    assert make_client(static_dir=static_dir).get("/api/nope").status_code == 404


def test_lifespan_stops_source(make_client):
    client = make_client()
    source = client.app.state.market_source
    client.__exit__(None, None, None)
    assert source.stopped


def test_periodic_snapshot_task(db, tmp_path):
    import time

    from fastapi.testclient import TestClient

    from app.main import create_app
    from tests.services.fakes import PRICES, FakeSource

    async def start_source(cache, tickers, initial_prices=None):
        src = FakeSource(cache, PRICES)
        await src.start(tickers)
        return src

    app = create_app(db=db, static_dir=tmp_path / "none", start_source=start_source,
                     snapshot_interval=0.05)
    with TestClient(app) as client:
        deadline = time.time() + 5
        while len(db.list_snapshots()) < 3 and time.time() < deadline:
            client.get("/api/health")
            time.sleep(0.05)
    assert len(db.list_snapshots()) >= 3
