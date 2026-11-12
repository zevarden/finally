"""Integration tests for SimulatorDataSource."""

import asyncio

from app.market.cache import PriceCache
from app.market.simulator import SimulatorDataSource


class TestSimulatorDataSource:
    """Integration tests for the SimulatorDataSource."""

    async def test_start_populates_cache(self):
        """Test that start() immediately populates the cache."""
        cache = PriceCache()
        source = SimulatorDataSource(price_cache=cache, update_interval=0.1)
        await source.start(["AAPL", "GOOGL"])

        # Cache should have seed prices immediately (before first loop tick)
        assert cache.get("AAPL") is not None
        assert cache.get("GOOGL") is not None

        await source.stop()

    async def test_prices_update_over_time(self):
        """Test that prices are updated periodically."""
        cache = PriceCache()
        source = SimulatorDataSource(price_cache=cache, update_interval=0.05)
        await source.start(["AAPL"])

        initial_version = cache.version
        for _ in range(100):
            if cache.version > initial_version:
                break
            await asyncio.sleep(0.01)

        # Version should have incremented (prices updated)
        assert cache.version > initial_version

        await source.stop()

    async def test_stop_is_clean(self):
        """Test that stop() is clean and idempotent."""
        cache = PriceCache()
        source = SimulatorDataSource(price_cache=cache, update_interval=0.1)
        await source.start(["AAPL"])
        await source.stop()
        # Double stop should not raise
        await source.stop()

    async def test_add_ticker(self):
        """Test adding a ticker dynamically."""
        cache = PriceCache()
        source = SimulatorDataSource(price_cache=cache, update_interval=0.1)
        await source.start(["AAPL"])

        await source.add_ticker("TSLA")
        assert "TSLA" in source.get_tickers()
        assert cache.get("TSLA") is not None

        await source.stop()

    async def test_remove_ticker(self):
        """Test removing a ticker."""
        cache = PriceCache()
        source = SimulatorDataSource(price_cache=cache, update_interval=0.1)
        await source.start(["AAPL", "TSLA"])

        await source.remove_ticker("TSLA")
        assert "TSLA" not in source.get_tickers()
        assert cache.get("TSLA") is None

        await source.stop()

    async def test_get_tickers(self):
        """Test getting the list of active tickers."""
        cache = PriceCache()
        source = SimulatorDataSource(price_cache=cache, update_interval=0.1)
        await source.start(["AAPL", "GOOGL"])

        tickers = source.get_tickers()
        assert set(tickers) == {"AAPL", "GOOGL"}

        await source.stop()

    async def test_empty_start(self):
        """Test starting with no tickers."""
        cache = PriceCache()
        source = SimulatorDataSource(price_cache=cache, update_interval=0.1)
        await source.start([])

        assert len(cache) == 0
        assert source.get_tickers() == []

        await source.stop()

    async def test_exception_resilience(self):
        """Test that simulator continues running after errors."""
        cache = PriceCache()
        source = SimulatorDataSource(price_cache=cache, update_interval=0.05)

        # Start with a valid ticker
        await source.start(["AAPL"])

        # Wait for some updates
        await asyncio.sleep(0.15)

        # Task should still be running
        assert source._task is not None
        assert not source._task.done()

        await source.stop()

    async def test_custom_update_interval(self):
        """Test using a custom update interval."""
        cache = PriceCache()
        source = SimulatorDataSource(price_cache=cache, update_interval=0.01)
        await source.start(["AAPL"])

        initial_version = cache.version
        for _ in range(100):  # poll instead of a fixed sleep: robust on slow CI
            if cache.version > initial_version + 2:
                break
            await asyncio.sleep(0.01)
        assert cache.version > initial_version + 2

        await source.stop()

    async def test_custom_event_probability(self):
        """Test creating source with custom event probability."""
        cache = PriceCache()
        # Very high event probability for testing
        source = SimulatorDataSource(price_cache=cache, update_interval=0.1, event_probability=1.0)
        await source.start(["AAPL"])
        for _ in range(100):
            if cache.version > 1:  # at least one tick after the seed write
                break
            await asyncio.sleep(0.01)
        await source.stop()
        # Every tick is a 2-5% shock, so each tick-to-tick move is at least ~2%
        assert abs(cache.get("AAPL").change_percent) > 1.9

    async def test_seed_makes_prices_reproducible(self):
        async def run():
            cache = PriceCache()
            source = SimulatorDataSource(price_cache=cache, update_interval=60, seed=42)
            await source.start(["AAPL", "MSFT"])
            prices = [source._sim.step() for _ in range(20)]
            await source.stop()
            return prices

        assert await run() == await run()

    async def test_initial_prices_and_session_open(self):
        cache = PriceCache()
        source = SimulatorDataSource(price_cache=cache, update_interval=0.01)
        await source.start(["AAPL"], initial_prices={"AAPL": 230.0})
        assert cache.get("AAPL").price == 230.0
        assert cache.get("AAPL").session_open == 230.0
        for _ in range(100):
            if cache.get("AAPL").timestamp and cache.version > 3:
                break
            await asyncio.sleep(0.01)
        assert cache.get("AAPL").session_open == 230.0  # carried forward by ticks
        await source.stop()

    async def test_status(self):
        cache = PriceCache()
        source = SimulatorDataSource(price_cache=cache, update_interval=0.01)
        await source.start(["AAPL", "MSFT"])
        for _ in range(100):
            if source.status()["last_success"]:
                break
            await asyncio.sleep(0.01)
        status = source.status()
        assert status["source"] == "simulator" and status["mode"] == "simulated"
        assert status["tickers"] == 2 and status["last_success"] and status["last_error"] is None
        await source.stop()

    async def test_step_error_is_recorded_and_loop_survives(self):
        cache = PriceCache()
        source = SimulatorDataSource(price_cache=cache, update_interval=0.01)
        await source.start(["AAPL"])

        def boom():
            raise RuntimeError("bad tick")

        source._sim.step = boom
        for _ in range(100):
            if source.status()["last_error"]:
                break
            await asyncio.sleep(0.01)
        assert "bad tick" in source.status()["last_error"]
        assert not source._task.done()
        await source.stop()
