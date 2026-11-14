import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from tests.services.fakes import PRICES, FakeSource, make_db


@pytest.fixture
def db(tmp_path):
    return make_db(tmp_path)


@pytest.fixture
def static_dir(tmp_path):
    d = tmp_path / "static"
    d.mkdir()
    (d / "index.html").write_text("<html><body>FinAlly</body></html>", encoding="utf-8")
    return d


@pytest.fixture
def make_client(db, tmp_path):
    clients = []

    def _make(static_dir=None):
        async def start_source(cache, tickers, initial_prices=None):
            src = FakeSource(cache, PRICES)
            await src.start(tickers, initial_prices)
            return src

        app = create_app(
            db=db,
            static_dir=static_dir if static_dir is not None else tmp_path / "no-static",
            start_source=start_source,
        )
        client = TestClient(app)
        client.__enter__()  # run lifespan
        clients.append(client)
        return client

    yield _make
    for c in clients:
        c.__exit__(None, None, None)


@pytest.fixture
def client(make_client):
    return make_client()
