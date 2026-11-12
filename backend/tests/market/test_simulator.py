"""Tests for GBMSimulator."""

import numpy as np

from app.market.seed_prices import SEED_PRICES, TICKER_PARAMS, fallback_seed_price
from app.market.simulator import GBMSimulator


class TestGBMSimulator:
    """Unit tests for the GBM price simulator."""

    def test_step_returns_all_tickers(self):
        """Test that step() returns prices for all tickers."""
        sim = GBMSimulator(tickers=["AAPL", "GOOGL"])
        result = sim.step()
        assert set(result.keys()) == {"AAPL", "GOOGL"}

    def test_prices_are_positive(self):
        """GBM prices can never go negative (exp() is always positive)."""
        sim = GBMSimulator(tickers=["AAPL"])
        for _ in range(10_000):
            prices = sim.step()
            assert prices["AAPL"] > 0

    def test_initial_prices_match_seeds(self):
        """Test that initial prices match seed prices."""
        sim = GBMSimulator(tickers=["AAPL"])
        # Before any step, price should be the seed price
        assert sim.get_price("AAPL") == SEED_PRICES["AAPL"]

    def test_add_ticker(self):
        """Test adding a ticker dynamically."""
        sim = GBMSimulator(tickers=["AAPL"])
        sim.add_ticker("TSLA")
        result = sim.step()
        assert "TSLA" in result

    def test_remove_ticker(self):
        """Test removing a ticker."""
        sim = GBMSimulator(tickers=["AAPL", "GOOGL"])
        sim.remove_ticker("GOOGL")
        result = sim.step()
        assert "GOOGL" not in result
        assert "AAPL" in result

    def test_add_duplicate_is_noop(self):
        """Test that adding a duplicate ticker is a no-op."""
        sim = GBMSimulator(tickers=["AAPL"])
        sim.add_ticker("AAPL")
        assert len(sim._tickers) == 1

    def test_remove_nonexistent_is_noop(self):
        """Test that removing a non-existent ticker is a no-op."""
        sim = GBMSimulator(tickers=["AAPL"])
        sim.remove_ticker("NOPE")  # Should not raise

    def test_unknown_ticker_gets_fallback_seed_price(self):
        """Unknown tickers get a stable seed price in [50, 300)."""
        sim = GBMSimulator(tickers=["ZZZZ"])
        price = sim.get_price("ZZZZ")
        assert price is not None
        assert 50.0 <= price <= 300.0

    def test_empty_step(self):
        """Test stepping with no tickers."""
        sim = GBMSimulator(tickers=[])
        result = sim.step()
        assert result == {}

    def test_prices_change_over_time(self):
        """After many steps, prices should have drifted from their seeds."""
        sim = GBMSimulator(tickers=["AAPL"], rng=np.random.default_rng(0))
        initial_price = sim.get_price("AAPL")

        for _ in range(1000):
            sim.step()

        final_price = sim.get_price("AAPL")
        # Price should have changed (extremely unlikely to be exactly the seed)
        assert final_price != initial_price

    def test_cholesky_rebuilds_on_add(self):
        """Test that Cholesky matrix is rebuilt when tickers are added."""
        sim = GBMSimulator(tickers=["AAPL"])
        assert sim._cholesky is None  # Only 1 ticker, no correlation matrix
        sim.add_ticker("GOOGL")
        assert sim._cholesky is not None  # Now 2 tickers, matrix exists

    def test_cholesky_none_with_one_ticker(self):
        """Test that Cholesky is None with only one ticker."""
        sim = GBMSimulator(tickers=["AAPL"])
        assert sim._cholesky is None

    def test_get_price_returns_none_for_unknown(self):
        """Test that get_price returns None for unknown ticker."""
        sim = GBMSimulator(tickers=["AAPL"])
        assert sim.get_price("UNKNOWN") is None

    def test_pairwise_correlation_tech_stocks(self):
        """Test that tech stocks have high correlation."""
        corr = GBMSimulator._pairwise_correlation("AAPL", "GOOGL")
        assert corr == 0.6

    def test_pairwise_correlation_finance_stocks(self):
        """Test that finance stocks have moderate correlation."""
        corr = GBMSimulator._pairwise_correlation("JPM", "V")
        assert corr == 0.5

    def test_pairwise_correlation_tsla(self):
        """Test that TSLA has lower correlation with everything."""
        corr = GBMSimulator._pairwise_correlation("TSLA", "AAPL")
        assert corr == 0.3
        corr = GBMSimulator._pairwise_correlation("TSLA", "JPM")
        assert corr == 0.3

    def test_pairwise_correlation_cross_sector(self):
        """Test cross-sector correlation."""
        corr = GBMSimulator._pairwise_correlation("AAPL", "JPM")
        assert corr == 0.3

    def test_default_dt_is_reasonable(self):
        """Test that default dt is a reasonable small value."""
        assert 0 < GBMSimulator.DEFAULT_DT < 0.0001

    def test_prices_rounded_to_two_decimals(self):
        """Test that prices are rounded to 2 decimal places."""
        sim = GBMSimulator(tickers=["AAPL"])
        result = sim.step()
        price_str = str(result["AAPL"])
        # Check that we have at most 2 decimal places
        if "." in price_str:
            decimal_part = price_str.split(".")[1]
            assert len(decimal_part) <= 2

    # --- reproducibility, statistics and the design-doc fixes ---------------------

    def test_reproducible_with_seed(self):
        a = GBMSimulator(["AAPL", "TSLA"], rng=np.random.default_rng(7))
        b = GBMSimulator(["AAPL", "TSLA"], rng=np.random.default_rng(7))
        assert [a.step() for _ in range(50)] == [b.step() for _ in range(50)]

    def test_log_return_statistics(self):
        dt = 1 / 252
        sim = GBMSimulator(["AAPL"], dt=dt, event_probability=0, rng=np.random.default_rng(1))
        prices = [190.0]
        for _ in range(20_000):
            sim.step()
            prices.append(sim._prices["AAPL"])
        r = np.diff(np.log(prices))
        mu, sigma = 0.05, 0.22
        assert abs(r.mean() - (mu - sigma**2 / 2) * dt) < 3 * sigma * np.sqrt(dt / len(r))
        assert abs(r.std() - sigma * np.sqrt(dt)) / (sigma * np.sqrt(dt)) < 0.03

    def test_correlation_structure(self):
        tickers = ["AAPL", "MSFT", "JPM", "V", "TSLA"]
        sim = GBMSimulator(tickers, dt=1 / 252, event_probability=0, rng=np.random.default_rng(3))
        rows = [sim.step() for _ in range(20_000)]
        lr = {t: np.diff(np.log([r[t] for r in rows])) for t in tickers}

        def corr(a, b):
            return np.corrcoef(lr[a], lr[b])[0, 1]

        assert abs(corr("AAPL", "MSFT") - 0.6) < 0.05
        assert abs(corr("JPM", "V") - 0.5) < 0.05
        assert abs(corr("AAPL", "JPM") - 0.3) < 0.05
        assert abs(corr("TSLA", "AAPL") - 0.3) < 0.05

    def test_unknown_ticker_seed_is_stable_across_instances_and_readd(self):
        s1, s2 = GBMSimulator(["PYPL"]), GBMSimulator(["PYPL"])
        assert s1.get_price("PYPL") == s2.get_price("PYPL") == round(fallback_seed_price("PYPL"), 2)
        s1.remove_ticker("PYPL")
        s1.add_ticker("PYPL")
        assert s1.get_price("PYPL") == s2.get_price("PYPL")

    def test_initial_prices_win_over_seeds(self):
        sim = GBMSimulator(["AAPL", "PYPL"], initial_prices={"aapl": 230.0})
        assert sim.get_price("AAPL") == 230.0
        assert sim.get_price("PYPL") == round(fallback_seed_price("PYPL"), 2)

    def test_tickers_are_normalized(self):
        sim = GBMSimulator([" aapl"])
        sim.add_ticker("AAPL")
        assert sim.get_tickers() == ["AAPL"]
        assert sim.get_price("aapl") == SEED_PRICES["AAPL"]
        sim.remove_ticker("aapl ")
        assert sim.get_tickers() == []

    def test_params_are_copied_not_shared(self):
        sim = GBMSimulator(["AAPL"])
        assert sim._params["AAPL"] == TICKER_PARAMS["AAPL"]
        assert sim._params["AAPL"] is not TICKER_PARAMS["AAPL"]

    def test_events_move_at_least_two_percent(self):
        sim = GBMSimulator(["AAPL"], event_probability=1.0, rng=np.random.default_rng(0))
        p = sim.step()["AAPL"]
        assert 0.0199 <= abs(p / 190.0 - 1) <= 0.0501

    def test_vectors_follow_add_and_remove(self):
        sim = GBMSimulator(["AAPL", "TSLA"])
        sim.add_ticker("JPM")
        assert sim._mu.shape == sim._sigma.shape == (3,)
        assert sim._cholesky.shape == (3, 3)
        sim.remove_ticker("TSLA")
        assert sim._sigma.tolist() == [0.22, 0.18]
        assert sim._cholesky.shape == (2, 2)

    def test_realistic_tick_size(self):
        sim = GBMSimulator(["AAPL"], event_probability=0, rng=np.random.default_rng(5))
        prices = [190.0] + [sim.step()["AAPL"] for _ in range(500)]
        moves = np.abs(np.diff(prices))
        assert moves.max() < 0.10  # sub-dime per 500 ms tick without events
