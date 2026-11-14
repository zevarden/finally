import pytest

from app.market import PriceCache
from app.services import tracked_tickers

from .fakes import PRICES, FakeSource, make_db


@pytest.fixture
def db(tmp_path):
    return make_db(tmp_path)


@pytest.fixture
def cache():
    return PriceCache()


@pytest.fixture
async def source(db, cache):
    src = FakeSource(cache, PRICES)
    await src.start(tracked_tickers(db))
    return src
