import pytest

from app.services import (
    WatchlistError,
    add_ticker,
    execute_trade,
    get_watchlist,
    remove_ticker,
)


async def test_add_ticker_tracks_and_returns_entry(db, cache, source):
    entry = await add_ticker(db, cache, source, " pypl ")
    assert entry["ticker"] == "PYPL"
    assert entry["price"] == 70.0 and entry["previous_price"] == 70.0
    assert entry["direction"] == "flat" and entry["day_change_percent"] == 0.0
    assert entry["added_at"]
    assert "PYPL" in source.get_tickers()
    assert get_watchlist(db, cache)[-1]["ticker"] == "PYPL"


async def test_add_ticker_with_dot(db, cache, source):
    assert (await add_ticker(db, cache, source, "brk.b"))["ticker"] == "BRK.B"


@pytest.mark.parametrize("ticker", ["", "1ABC", "TOOLONGTICKER", "AB CD"])
async def test_add_invalid(db, cache, source, ticker):
    with pytest.raises(WatchlistError) as e:
        await add_ticker(db, cache, source, ticker)
    assert e.value.status_code == 400


async def test_add_duplicate(db, cache, source):
    with pytest.raises(WatchlistError) as e:
        await add_ticker(db, cache, source, "aapl")
    assert e.value.status_code == 409


async def test_add_unknown(db, cache, source):
    with pytest.raises(WatchlistError) as e:
        await add_ticker(db, cache, source, "ZZZZ")
    assert e.value.status_code == 404
    assert "ZZZZ" not in source.get_tickers()
    assert all(r["ticker"] != "ZZZZ" for r in db.list_watchlist())


async def test_remove_untracks(db, cache, source):
    await remove_ticker(db, cache, source, "nflx")
    assert "NFLX" not in source.get_tickers()
    assert "NFLX" not in cache
    assert all(r["ticker"] != "NFLX" for r in db.list_watchlist())


async def test_remove_held_ticker_keeps_tracking(db, cache, source):
    await execute_trade(db, cache, source, "NFLX", "buy", 1)
    await remove_ticker(db, cache, source, "NFLX")
    assert "NFLX" in source.get_tickers()
    assert "NFLX" in cache


async def test_remove_not_listed(db, cache, source):
    with pytest.raises(WatchlistError) as e:
        await remove_ticker(db, cache, source, "PYPL")
    assert e.value.status_code == 404


async def test_remove_invalid(db, cache, source):
    with pytest.raises(WatchlistError) as e:
        await remove_ticker(db, cache, source, "$$$")
    assert e.value.status_code == 400


def test_get_watchlist_null_prices_when_uncached(db, cache):
    entries = get_watchlist(db, cache)
    assert len(entries) == 10
    assert entries[0]["ticker"] == "AAPL"
    assert entries[0]["price"] is None and entries[0]["direction"] is None
