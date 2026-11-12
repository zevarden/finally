"""Tests for MassiveDataSource, using real massive response models (see fakes.py)."""

from datetime import date, timedelta

import pytest
from massive.exceptions import BadResponse

from app.market.cache import PriceCache
from app.market.factory import start_market_data
from app.market.massive_client import (
    MassiveAuthError,
    MassiveDataSource,
    classify_error,
    extract_price,
    extract_session_open,
)

from .fakes import AUTH_401, PLAN_403, RATE_429, FakeClient, snap


def _days_ago(n: int) -> str:
    return (date.today() - timedelta(days=n)).isoformat()


# --- pure helpers --------------------------------------------------------------


class TestExtractPrice:
    def test_uses_last_trade_with_sip_timestamp_in_ns(self):
        assert extract_price(snap("AAPL", 190.5)) == (190.5, 1_763_000_000.0)

    def test_falls_back_to_minute_bar_in_ms(self):
        assert extract_price(snap("AAPL", min_close=189.0)) == (189.0, 1_763_000_000.0)

    def test_falls_back_to_day_then_prev_day_close(self):
        assert extract_price(snap("AAPL", day_close=187.0, prev_close=186.0)) == (187.0, None)
        assert extract_price(snap("AAPL", prev_close=188.0)) == (188.0, None)

    def test_no_usable_price(self):
        assert extract_price(snap("AAPL")) is None

    def test_session_open_is_prev_day_close(self):
        assert extract_session_open(snap("AAPL", 190.0, prev_close=188.0)) == 188.0
        assert extract_session_open(snap("AAPL", 190.0)) is None


@pytest.mark.parametrize(
    "body,kind",
    [
        (AUTH_401, "auth"),
        (PLAN_403, "plan"),
        (RATE_429, "rate_limit"),
        ('{"status":"NOT_FOUND"}', "other"),
        ("<html>502 Bad Gateway</html>", "other"),
        ("[1, 2]", "other"),
    ],
)
def test_classify_error(body, kind):
    assert classify_error(BadResponse(body)) == kind


def test_classify_non_massive_error():
    assert classify_error(ConnectionError("boom")) == "other"


# --- live mode -----------------------------------------------------------------


async def test_live_mode_seeds_cache_with_session_open():
    cache = PriceCache()
    src = MassiveDataSource(
        "k", cache, poll_interval=60, client=FakeClient({"AAPL": (190.5, 188.0)})
    )
    await src.start(["aapl"])
    try:
        u = cache.get("AAPL")
        assert src.status()["mode"] == "live"
        assert (u.price, u.session_open, u.timestamp) == (190.5, 188.0, 1_763_000_000.0)
        assert u.day_change_percent == pytest.approx(1.3298, abs=1e-4)
        assert src.status()["last_success"] is not None
    finally:
        await src.stop()


async def test_one_api_call_per_poll_with_copied_ticker_list():
    client = FakeClient({"AAPL": (190.0, 189.0), "MSFT": (420.0, 419.0)})
    src = MassiveDataSource("k", PriceCache(), poll_interval=60, client=client)
    await src.start(["AAPL", "MSFT"])
    await src._poll_live()
    assert client.calls == [("all", ("AAPL", "MSFT"))] * 2
    await src.stop()


async def test_add_ticker_fetches_immediately_and_unknown_is_not_cached():
    cache = PriceCache()
    client = FakeClient({"AAPL": (190.0, 189.0), "PYPL": (70.0, 69.0)})
    src = MassiveDataSource("k", cache, poll_interval=60, client=client)
    await src.start(["AAPL"])
    await src.add_ticker("pypl")
    await src.add_ticker("NOPE")  # 404 from Massive: logged, not raised
    assert cache.get_price("PYPL") == 70.0
    assert "NOPE" not in cache
    assert ("one", "PYPL") in client.calls
    await src.stop()


async def test_unknown_ticker_in_poll_is_skipped():
    cache = PriceCache()
    src = MassiveDataSource(
        "k", cache, poll_interval=60, client=FakeClient({"AAPL": (190.0, 189.0)})
    )
    await src.start(["AAPL", "ZZZZ"])
    assert "AAPL" in cache and "ZZZZ" not in cache
    assert "ZZZZ" in src._missing_logged
    await src.stop()


async def test_ticker_removed_mid_poll_does_not_come_back():
    cache = PriceCache()
    client = FakeClient({"AAPL": (190.0, 189.0), "MSFT": (420.0, 419.0)})
    src = MassiveDataSource("k", cache, poll_interval=60, client=client)
    await src.start(["AAPL", "MSFT"])
    # Simulate remove_ticker() landing while the worker thread is fetching
    client.before_return = lambda: src._tickers.remove("MSFT")
    cache.remove("MSFT")
    await src._poll_live()
    assert "MSFT" not in cache
    assert "AAPL" in cache
    await src.stop()


async def test_poll_error_keeps_last_price():
    client = FakeClient({"AAPL": (190.0, 189.0)})
    cache = PriceCache()
    src = MassiveDataSource("k", cache, poll_interval=60, client=client)
    await src.start(["AAPL"])
    client.snapshot_error = RATE_429
    await src._safe(src._poll_live)
    assert cache.get_price("AAPL") == 190.0
    assert src.status()["last_error"].startswith("rate_limit")
    await src.stop()


async def test_network_error_at_start_keeps_app_up():
    class Down(FakeClient):
        def get_snapshot_all(self, market_type, tickers):
            raise ConnectionError("network unreachable")

    src = MassiveDataSource("k", PriceCache(), poll_interval=60, client=Down())
    await src.start(["AAPL"])  # must not raise
    assert src.status()["mode"] == "live"
    assert src.status()["last_error"].startswith("other")
    await src.stop()


async def test_poll_interval_clamped():
    assert MassiveDataSource("k", PriceCache(), poll_interval=0.1).status()["poll_interval"] == 1.0


async def test_empty_tickers_skips_poll():
    client = FakeClient()
    src = MassiveDataSource("k", PriceCache(), poll_interval=60, client=client)
    await src.start([])
    assert client.calls == []
    await src.stop()


# --- auth / plan handling ----------------------------------------------------------


async def test_bad_key_raises_auth_error():
    src = MassiveDataSource("bad", PriceCache(), client=FakeClient(snapshot_error=AUTH_401))
    with pytest.raises(MassiveAuthError):
        await src.start(["AAPL"])


async def test_bad_key_falls_back_to_simulator(monkeypatch):
    monkeypatch.setenv("MASSIVE_API_KEY", "bad")
    monkeypatch.setattr(
        "app.market.massive_client.RESTClient", lambda **kw: FakeClient(snapshot_error=AUTH_401)
    )
    cache = PriceCache()
    src = await start_market_data(cache, ["AAPL"])
    try:
        assert src.status()["source"] == "simulator"
        assert "AAPL" in cache
    finally:
        await src.stop()


async def test_rest_client_built_without_retries(monkeypatch):
    seen = {}

    def fake_ctor(**kw):
        seen.update(kw)
        return FakeClient({"AAPL": (1.0, 1.0)})

    monkeypatch.setattr("app.market.massive_client.RESTClient", fake_ctor)
    src = MassiveDataSource("key-123", PriceCache(), poll_interval=60)
    await src.start(["AAPL"])
    assert seen == {"api_key": "key-123", "retries": 0}
    await src.stop()


async def test_free_plan_falls_back_to_eod():
    client = FakeClient(
        snapshot_error=PLAN_403,
        grouped={_days_ago(1): {"AAPL": 191.0, "PYPL": 70.0}, _days_ago(2): {"AAPL": 189.0}},
    )
    cache = PriceCache()
    src = MassiveDataSource("k", cache, client=client)
    await src.start(["AAPL"])
    try:
        status = src.status()
        assert status["mode"] == "eod" and status["poll_interval"] == 3600.0
        u = cache.get("AAPL")
        assert (u.price, u.session_open) == (191.0, 189.0)
        assert u.timestamp == 1_762_900_000.0  # bar ms -> s
        await src.add_ticker("PYPL")  # served from bars already in memory
        assert cache.get_price("PYPL") == 70.0
        assert cache.get("PYPL").session_open is None  # not in the prior session
        assert sum(1 for c in client.calls if c[0] == "grouped") == 2
        assert not any(c[0] == "one" for c in client.calls)
    finally:
        await src.stop()


async def test_eod_walks_back_over_weekends_and_holidays():
    # Only days 3 and 5 have data (e.g. a weekend in between)
    client = FakeClient(
        snapshot_error=PLAN_403,
        grouped={_days_ago(3): {"AAPL": 191.0}, _days_ago(5): {"AAPL": 190.0}},
    )
    cache = PriceCache()
    src = MassiveDataSource("k", cache, client=client)
    await src.start(["AAPL"])
    assert (cache.get("AAPL").price, cache.get("AAPL").session_open) == (191.0, 190.0)
    assert [c[1] for c in client.calls if c[0] == "grouped"] == [_days_ago(n) for n in range(1, 6)]
    await src.stop()


async def test_eod_with_no_data_leaves_cache_empty():
    src = MassiveDataSource("k", PriceCache(), client=FakeClient(snapshot_error=PLAN_403))
    await src.start(["AAPL"])
    assert src.status()["mode"] == "eod"
    assert len(src._cache) == 0
    await src.stop()


# --- lifecycle -----------------------------------------------------------------


async def test_stop_cancels_task_and_is_idempotent():
    src = MassiveDataSource("k", PriceCache(), poll_interval=60, client=FakeClient())
    await src.stop()  # before start
    await src.start(["AAPL"])
    assert src._task is not None and not src._task.done()
    await src.stop()
    assert src._task is None
    await src.stop()
