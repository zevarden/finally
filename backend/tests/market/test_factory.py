"""Tests for market data source factory."""

import os
from unittest.mock import patch

from app.market.cache import PriceCache
from app.market.factory import create_market_data_source, start_market_data
from app.market.massive_client import MassiveDataSource
from app.market.simulator import SimulatorDataSource


class TestFactory:
    """Tests for create_market_data_source factory."""

    def test_creates_simulator_when_no_api_key(self):
        """Test that simulator is created when MASSIVE_API_KEY is not set."""
        cache = PriceCache()

        with patch.dict(os.environ, {}, clear=True):
            source = create_market_data_source(cache)

        assert isinstance(source, SimulatorDataSource)

    def test_creates_simulator_when_api_key_empty(self):
        """Test that simulator is created when MASSIVE_API_KEY is empty."""
        cache = PriceCache()

        with patch.dict(os.environ, {"MASSIVE_API_KEY": ""}, clear=True):
            source = create_market_data_source(cache)

        assert isinstance(source, SimulatorDataSource)

    def test_creates_simulator_when_api_key_whitespace(self):
        """Test that simulator is created when MASSIVE_API_KEY is whitespace."""
        cache = PriceCache()

        with patch.dict(os.environ, {"MASSIVE_API_KEY": "   "}, clear=True):
            source = create_market_data_source(cache)

        assert isinstance(source, SimulatorDataSource)

    def test_creates_massive_when_api_key_set(self):
        """Test that Massive client is created when MASSIVE_API_KEY is set."""
        cache = PriceCache()

        with patch.dict(os.environ, {"MASSIVE_API_KEY": "test-key"}, clear=True):
            source = create_market_data_source(cache)

        assert isinstance(source, MassiveDataSource)

    def test_massive_receives_api_key(self):
        """Test that Massive client receives the API key."""
        cache = PriceCache()

        with patch.dict(os.environ, {"MASSIVE_API_KEY": "test-key-123"}, clear=True):
            source = create_market_data_source(cache)

        assert isinstance(source, MassiveDataSource)
        assert source._api_key == "test-key-123"

    def test_simulator_receives_cache(self):
        """Test that simulator receives the cache reference."""
        cache = PriceCache()

        with patch.dict(os.environ, {}, clear=True):
            source = create_market_data_source(cache)

        assert isinstance(source, SimulatorDataSource)
        assert source._cache is cache

    def test_massive_receives_cache(self):
        """Test that Massive client receives the cache reference."""
        cache = PriceCache()

        with patch.dict(os.environ, {"MASSIVE_API_KEY": "test-key"}, clear=True):
            source = create_market_data_source(cache)

        assert isinstance(source, MassiveDataSource)
        assert source._cache is cache

    def test_poll_interval_from_env(self):
        env = {"MASSIVE_API_KEY": "k", "MASSIVE_POLL_INTERVAL": "5"}
        with patch.dict(os.environ, env, clear=True):
            source = create_market_data_source(PriceCache())
        assert source.status()["poll_interval"] == 5.0

    def test_invalid_poll_interval_uses_default(self):
        env = {"MASSIVE_API_KEY": "k", "MASSIVE_POLL_INTERVAL": "fast"}
        with patch.dict(os.environ, env, clear=True):
            source = create_market_data_source(PriceCache())
        assert source.status()["poll_interval"] == 15.0

    def test_sim_seed_from_env(self):
        with patch.dict(os.environ, {"MARKET_SIM_SEED": "42"}, clear=True):
            source = create_market_data_source(PriceCache())
        assert source._seed == 42
        with patch.dict(os.environ, {"MARKET_SIM_SEED": "abc"}, clear=True):
            assert create_market_data_source(PriceCache())._seed is None


async def test_start_market_data_uses_simulator_without_key(monkeypatch):
    monkeypatch.delenv("MASSIVE_API_KEY", raising=False)
    cache = PriceCache()
    source = await start_market_data(cache, ["AAPL", "msft"], initial_prices={"AAPL": 230.0})
    try:
        assert isinstance(source, SimulatorDataSource)
        assert cache.get_price("AAPL") == 230.0 and "MSFT" in cache
    finally:
        await source.stop()
