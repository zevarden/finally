import pytest

from app.services import TradeError, execute_trade, get_portfolio, record_snapshot


async def buy(db, cache, source, ticker, qty):
    return await execute_trade(db, cache, source, ticker, "buy", qty)


async def sell(db, cache, source, ticker, qty):
    return await execute_trade(db, cache, source, ticker, "sell", qty)


async def test_buy_debits_cash_and_creates_position(db, cache, source):
    result = await buy(db, cache, source, "AAPL", 10)
    trade = result["trade"]
    assert trade["ticker"] == "AAPL" and trade["side"] == "buy"
    assert trade["quantity"] == 10 and trade["price"] == 190.0
    assert {"id", "executed_at"} <= trade.keys()
    assert db.get_cash() == pytest.approx(8100.0)
    pos = db.get_position("AAPL")
    assert pos["quantity"] == 10 and pos["avg_cost"] == pytest.approx(190.0)
    pf = result["portfolio"]
    assert pf["cash_balance"] == 8100.0
    assert pf["total_value"] == 10000.0
    assert pf["positions"][0]["ticker"] == "AAPL"


async def test_buy_twice_uses_weighted_average_cost(db, cache, source):
    await buy(db, cache, source, "AAPL", 10)  # @190
    source.set_price("AAPL", 210.0)
    await buy(db, cache, source, "AAPL", 30)  # @210
    pos = db.get_position("AAPL")
    assert pos["quantity"] == 40
    assert pos["avg_cost"] == pytest.approx((10 * 190 + 30 * 210) / 40)
    assert db.get_cash() == pytest.approx(10000 - 1900 - 6300)


async def test_ticker_and_side_are_normalized(db, cache, source):
    result = await execute_trade(db, cache, source, "  aapl ", " BUY ", 1)
    assert result["trade"]["ticker"] == "AAPL" and result["trade"]["side"] == "buy"


async def test_insufficient_cash(db, cache, source):
    with pytest.raises(TradeError, match="Insufficient cash"):
        await buy(db, cache, source, "NFLX", 100)  # $65,000
    assert db.get_cash() == 10000.0
    assert db.get_position("NFLX") is None
    assert db.list_trades() == []


async def test_buy_exactly_all_cash(db, cache, source):
    await buy(db, cache, source, "JPM", 50)  # 50 * 200 = 10000
    assert db.get_cash() == pytest.approx(0.0)


async def test_oversell_rejected(db, cache, source):
    await buy(db, cache, source, "AAPL", 5)
    with pytest.raises(TradeError, match="Insufficient shares"):
        await sell(db, cache, source, "AAPL", 6)
    assert db.get_position("AAPL")["quantity"] == 5


async def test_sell_without_position_rejected(db, cache, source):
    with pytest.raises(TradeError, match="Insufficient shares"):
        await sell(db, cache, source, "AAPL", 1)


async def test_partial_sell_keeps_avg_cost(db, cache, source):
    await buy(db, cache, source, "AAPL", 10)
    source.set_price("AAPL", 200.0)
    await sell(db, cache, source, "AAPL", 4)
    pos = db.get_position("AAPL")
    assert pos["quantity"] == 6 and pos["avg_cost"] == pytest.approx(190.0)
    assert db.get_cash() == pytest.approx(10000 - 1900 + 800)


async def test_sell_at_a_loss(db, cache, source):
    await buy(db, cache, source, "TSLA", 10)  # @250
    source.set_price("TSLA", 200.0)
    pf = get_portfolio(db, cache)
    assert pf["unrealized_pnl"] == -500.0
    assert pf["positions"][0]["unrealized_pnl_percent"] == -20.0
    await sell(db, cache, source, "TSLA", 10)
    assert db.get_cash() == pytest.approx(9500.0)
    assert db.get_position("TSLA") is None


async def test_sell_to_zero_deletes_position_but_keeps_watched_ticker_tracked(db, cache, source):
    await buy(db, cache, source, "AAPL", 3)
    result = await sell(db, cache, source, "AAPL", 3)
    assert db.get_position("AAPL") is None
    assert result["portfolio"]["positions"] == []
    assert "AAPL" in source.get_tickers()  # still on the watchlist


async def test_fractional_shares_and_float_safe_sell_all(db, cache, source):
    await buy(db, cache, source, "AAPL", 0.1)
    await buy(db, cache, source, "AAPL", 0.2)  # 0.30000000000000004
    await sell(db, cache, source, "AAPL", 0.3)
    assert db.get_position("AAPL") is None
    assert db.get_cash() == pytest.approx(10000.0)
    assert db.list_trades()[0]["quantity"] == pytest.approx(0.3)


@pytest.mark.parametrize("ticker", ["", "123", "TOOLONGTICKER", "A$B", None])
async def test_invalid_ticker(db, cache, source, ticker):
    with pytest.raises(TradeError, match="Invalid ticker"):
        await buy(db, cache, source, ticker, 1)


@pytest.mark.parametrize("qty", [0, -1, float("nan"), float("inf"), "abc", True])
async def test_invalid_quantity(db, cache, source, qty):
    with pytest.raises(TradeError, match="Quantity"):
        await buy(db, cache, source, "AAPL", qty)


async def test_invalid_side(db, cache, source):
    with pytest.raises(TradeError, match="Side"):
        await execute_trade(db, cache, source, "AAPL", "hold", 1)


async def test_unknown_ticker_has_no_price_and_is_not_left_tracked(db, cache, source):
    with pytest.raises(TradeError, match="No price"):
        await buy(db, cache, source, "ZZZZ", 1)
    assert "ZZZZ" not in source.get_tickers()


async def test_buy_unwatched_ticker_tracks_it_and_sell_all_untracks(db, cache, source):
    assert "PYPL" not in source.get_tickers()
    await buy(db, cache, source, "PYPL", 2)
    assert "PYPL" in source.get_tickers()
    assert db.get_position("PYPL")["quantity"] == 2
    await sell(db, cache, source, "PYPL", 2)
    assert "PYPL" not in source.get_tickers()
    assert "PYPL" not in cache


async def test_failed_buy_of_unwatched_ticker_untracks_it(db, cache, source):
    with pytest.raises(TradeError, match="Insufficient cash"):
        await buy(db, cache, source, "BRK.B", 1000)
    assert "BRK.B" not in source.get_tickers()


async def test_trade_records_snapshot(db, cache, source):
    await buy(db, cache, source, "AAPL", 1)
    snaps = db.list_snapshots()
    assert len(snaps) == 1 and snaps[0]["total_value"] == pytest.approx(10000.0)


def test_portfolio_values_uncached_position_at_avg_cost(db, cache):
    db.upsert_position("XYZ", 2, 50.0)
    pf = get_portfolio(db, cache)
    assert pf["positions"][0]["current_price"] == 50.0
    assert pf["total_value"] == 10100.0
    assert pf["unrealized_pnl"] == 0.0


async def test_portfolio_shape_weights_and_sorting(db, cache, source):
    await buy(db, cache, source, "AAPL", 10)  # 1900
    await buy(db, cache, source, "MSFT", 10)  # 4200
    source.set_price("AAPL", 209.0)
    pf = get_portfolio(db, cache)
    assert set(pf) == {"cash_balance", "positions_value", "total_value", "unrealized_pnl",
                       "unrealized_pnl_percent", "positions"}
    assert [p["ticker"] for p in pf["positions"]] == ["MSFT", "AAPL"]
    aapl = pf["positions"][1]
    assert set(aapl) == {"ticker", "quantity", "avg_cost", "current_price", "market_value",
                         "unrealized_pnl", "unrealized_pnl_percent", "weight"}
    assert aapl["market_value"] == 2090.0
    assert aapl["unrealized_pnl"] == 190.0
    assert aapl["unrealized_pnl_percent"] == 10.0
    assert pf["total_value"] == 10190.0
    assert aapl["weight"] == round(2090 / 10190, 4)
    assert pf["unrealized_pnl_percent"] == round(190 / 6100 * 100, 2)


def test_empty_portfolio(db, cache):
    pf = get_portfolio(db, cache)
    assert pf == {"cash_balance": 10000.0, "positions_value": 0.0, "total_value": 10000.0,
                  "unrealized_pnl": 0.0, "unrealized_pnl_percent": 0.0, "positions": []}


def test_record_snapshot(db, cache):
    row = record_snapshot(db, cache)
    assert row["total_value"] == 10000.0
    assert len(db.list_snapshots()) == 1
