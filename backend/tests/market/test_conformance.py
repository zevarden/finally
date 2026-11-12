"""Interface conformance: both sources must honour the MarketDataSource contract."""

import pytest

from app.market.cache import PriceCache
from app.market.interface import MarketDataSource, normalize_ticker
from app.market.massive_client import MassiveDataSource
from app.market.simulator import SimulatorDataSource

from .fakes import FakeClient

PRICES = {t: (100.0 + i, 99.0) for i, t in enumerate(["AAPL", "MSFT", "TSLA", "NVDA"])}


@pytest.fixture(params=["sim", "massive"])
def make(request):
    def _make(cache: PriceCache) -> MarketDataSource:
        if request.param == "sim":
            return SimulatorDataSource(cache, update_interval=60, seed=1)
        return MassiveDataSource("k", cache, poll_interval=60, client=FakeClient(PRICES))

    return _make


def test_normalize_ticker():
    assert normalize_ticker("  brk.b ") == "BRK.B"


async def test_lifecycle_contract(make):
    cache = PriceCache()
    src = make(cache)
    assert isinstance(src, MarketDataSource)
    await src.start([" aapl ", "MSFT", "msft"])
    assert src.get_tickers() == ["AAPL", "MSFT"]  # normalized + de-duped
    assert "AAPL" in cache and "MSFT" in cache  # seeded before start() returns
    assert cache.get("AAPL").session_open is not None

    await src.add_ticker("tsla")
    await src.add_ticker("TSLA ")
    assert src.get_tickers().count("TSLA") == 1
    assert "TSLA" in cache  # priced immediately

    v = cache.version
    await src.remove_ticker("msft")
    await src.remove_ticker("MSFT")
    assert "MSFT" not in cache and "MSFT" not in src.get_tickers()
    assert cache.version > v  # SSE will push a frame without MSFT

    assert set(src.status()) >= {"source", "mode", "tickers", "last_success", "last_error"}
    assert src.status()["tickers"] == 2

    await src.stop()
    await src.stop()  # idempotent


async def test_add_before_start_is_included(make):
    cache = PriceCache()
    src = make(cache)
    await src.add_ticker("nvda")
    await src.start(["AAPL"])
    assert set(src.get_tickers()) == {"AAPL", "NVDA"}
    assert "NVDA" in cache
    await src.stop()


async def test_no_writes_after_stop(make):
    cache = PriceCache()
    src = make(cache)
    await src.start(["AAPL"])
    await src.stop()
    v = cache.version
    await src.remove_ticker("AAPL")  # eviction still allowed
    assert cache.version == v + 1
    assert src._task is None
