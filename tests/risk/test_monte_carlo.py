"""
Group F: VaR — single-asset, missing-data status, log/simple-return
conversion, deterministic RNG. All price history is mocked; no real
yfinance/network calls in this suite.
"""

import math

import numpy as np
import pandas as pd
import pytest

from src.risk.monte_carlo import (
    VaRResult,
    _simulate_univariate,
    calculate_portfolio_var,
)


def _synthetic_returns(mean: float, std: float, n: int = 300, seed: int = 1) -> pd.Series:
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2023-01-01", periods=n, freq="D")
    return pd.Series(rng.normal(mean, std, size=n), index=dates)


class TestSingleAssetVaR:
    def test_univariate_simulation_returns_ok_status(self):
        returns = _synthetic_returns(mean=0.0005, std=0.02, seed=42)
        result = _simulate_univariate(
            returns, weight=1.0, simulations=2000, horizon_days=21, rng=np.random.default_rng(seed=7)
        )
        assert result.status == "ok"
        assert result.var_95 is not None
        assert result.cvar_95 is not None
        # CVaR (tail mean) must be at or below VaR (5th percentile) in magnitude for a loss.
        assert result.cvar_95 <= result.var_95

    def test_full_pipeline_with_one_usable_ticker(self, monkeypatch):
        """calculate_portfolio_var must not bail out at < 2 holdings anymore for a single usable ticker."""
        returns = _synthetic_returns(mean=0.0003, std=0.015, seed=3)
        monkeypatch.setattr(
            "src.risk.monte_carlo._log_returns_by_ticker", lambda tickers: {"AAPL": returns}
        )

        result = calculate_portfolio_var(
            {"AAPL": 1.0}, simulations=1000, horizon_days=21, rng=np.random.default_rng(seed=1)
        )

        assert result.status == "ok"
        assert isinstance(result.var_95, float)

    def test_zero_variance_series_is_insufficient_data_not_error(self):
        flat_returns = pd.Series([0.0] * 10, index=pd.date_range("2023-01-01", periods=10))
        result = _simulate_univariate(flat_returns, weight=1.0, simulations=100, horizon_days=21, rng=np.random.default_rng())
        assert result.status == "insufficient_data"
        assert result.var_95 is None


class TestMissingDataIsDistinguishableFromZeroRisk:
    def test_empty_holdings_returns_explicit_status_not_zero_floats(self):
        result = calculate_portfolio_var({})
        assert isinstance(result, VaRResult)
        assert result.status == "insufficient_data"
        assert result.var_95 is None  # NOT 0.0 — genuinely unavailable, not "zero risk"
        assert result.is_ok is False

    def test_no_usable_price_history_returns_explicit_status(self, monkeypatch):
        monkeypatch.setattr("src.risk.monte_carlo._log_returns_by_ticker", lambda tickers: {})
        result = calculate_portfolio_var({"BADTICKER": 1.0})
        assert result.status == "insufficient_data"
        assert result.var_95 is None

    def test_fewer_than_two_overlapping_observations(self, monkeypatch):
        one_row = pd.Series([0.01], index=[pd.Timestamp("2023-01-01")])
        monkeypatch.setattr(
            "src.risk.monte_carlo._log_returns_by_ticker",
            lambda tickers: {"AAPL": one_row, "MSFT": one_row},
        )
        result = calculate_portfolio_var({"AAPL": 0.5, "MSFT": 0.5})
        assert result.status == "insufficient_data"


class TestValidation:
    def test_non_positive_simulations_raises(self):
        with pytest.raises(ValueError):
            calculate_portfolio_var({"AAPL": 1.0}, simulations=0)

    def test_non_positive_horizon_raises(self):
        with pytest.raises(ValueError):
            calculate_portfolio_var({"AAPL": 1.0}, horizon_days=-1)

    def test_negative_weight_raises(self):
        with pytest.raises(ValueError):
            calculate_portfolio_var({"AAPL": -0.5, "MSFT": 1.5})

    def test_non_finite_weight_raises(self):
        with pytest.raises(ValueError):
            calculate_portfolio_var({"AAPL": float("inf")})


class TestLogToSimpleReturnConversion:
    def test_expm1_matches_manual_conversion(self):
        log_return = -0.05
        simple_return = math.expm1(log_return)
        assert simple_return == pytest.approx(math.exp(log_return) - 1)
        # ln(1+R) <= R for all R > -1, so for a loss (negative return) the
        # raw log return is always MORE negative than the corresponding
        # simple return — direct multiplication of dollar equity by the
        # raw log return OVERSTATES the true dollar loss.
        assert simple_return > log_return

    def test_summarize_passes_through_already_simple_returns(self, monkeypatch):
        """
        `_summarize` receives already-converted SIMPLE returns (the
        per-asset log-to-simple conversion happens earlier, inside
        `_simulate_univariate`/`_simulate_multivariate`, before
        weight-aggregation — see the module docstring's "Portfolio-return
        aggregation" section). It must report them as-is, never applying
        a second conversion.
        """
        from src.risk.monte_carlo import _summarize

        # A path of simulated cumulative SIMPLE returns with a clear -30%
        # tail comfortably larger than the 5th percentile cutoff (200/1000
        # = 20% of samples), so the 5th percentile lands squarely inside
        # the tail block at exactly -0.30 rather than interpolating at
        # its boundary with the rest of the distribution.
        simulated = np.array([-0.30] * 200 + [0.05] * 800)
        result = _summarize(simulated)
        assert result.status == "ok"
        assert result.var_95 == pytest.approx(-0.30, abs=1e-9)
        # Must NOT be re-converted as though it were still a log return
        # (that would produce math.expm1(-0.30) ~= -0.2592, a smaller-
        # magnitude, wrong value).
        assert result.var_95 != pytest.approx(math.expm1(-0.30), abs=1e-6)


class TestPortfolioReturnAggregationCorrectness:
    """
    Simple returns combine linearly across a portfolio
    (`sum(w_i * R_i)`); log returns do not. `_simulate_multivariate` and
    `_simulate_univariate` must convert each asset's cumulative LOG
    return to a SIMPLE return before weight-summing, never weight-sum
    the log returns and convert only once at the end — that former
    approach is merely a first-order approximation whose error GROWS
    with the size of the simulated move, which is exactly backwards for
    a VaR tail calculation.
    """

    class _FixedDailyReturnRNG:
        """
        A fake RNG standing in for `numpy.random.Generator` that always
        returns the SAME fixed per-asset daily log return, so a
        multi-asset simulation's output is fully deterministic and can
        be checked against an exact hand-computed expected value rather
        than a statistical approximation.
        """

        def __init__(self, daily_log_returns: np.ndarray):
            self._daily = np.asarray(daily_log_returns, dtype=float)

        def multivariate_normal(self, mean, cov, size):  # noqa: ARG002 - mean/cov intentionally ignored
            simulations, horizon_days = size
            num_assets = self._daily.shape[0]
            return np.broadcast_to(self._daily, (simulations, horizon_days, num_assets)).copy()

    def test_large_divergent_move_matches_exact_simple_return_weighting(self):
        """
        Two assets, one day, one horizon: Asset A drops exactly 50%
        (simple) in a single simulated day, Asset B is flat, equally
        weighted. The mathematically correct portfolio simple return is
        exactly 0.5 * (-0.5) + 0.5 * 0.0 = -0.25 (a -25% loss). The
        FORMER (buggy) approach — weight-summing the LOG returns first,
        converting only once at the end — would instead compute
        expm1(0.5 * ln(0.5) + 0.5 * 0.0) ~= -0.2929 (a -29.29% loss):
        overstating the true loss by ~4.3 percentage points, purely from
        aggregating in the wrong space. This confirms the fixed
        implementation produces the mathematically exact figure, not the
        former approximation.
        """
        from src.risk.monte_carlo import _simulate_multivariate

        # Only used for .mean()/.cov() shape — the fixed RNG below
        # ignores these values and returns a deterministic draw instead.
        returns_df = pd.DataFrame({"A": [-0.01, 0.01, 0.02], "B": [0.005, -0.005, 0.01]})
        weights = np.array([0.5, 0.5])

        asset_a_log_return = math.log(0.5)  # exactly -50% simple in one day
        asset_b_log_return = 0.0  # flat
        fixed_rng = self._FixedDailyReturnRNG(np.array([asset_a_log_return, asset_b_log_return]))

        result = _simulate_multivariate(returns_df, weights, simulations=1, horizon_days=1, rng=fixed_rng)

        assert result.status == "ok"
        correct_portfolio_return = 0.5 * math.expm1(asset_a_log_return) + 0.5 * math.expm1(asset_b_log_return)
        assert correct_portfolio_return == pytest.approx(-0.25, abs=1e-9)
        assert result.var_95 == pytest.approx(correct_portfolio_return, abs=1e-9)

        former_buggy_result = math.expm1(0.5 * asset_a_log_return + 0.5 * asset_b_log_return)
        assert former_buggy_result == pytest.approx(-0.29289, abs=1e-4)
        # The fixed implementation must NOT match the old (wrong) formula,
        # and the two must differ by a large, clearly-measurable amount —
        # not a rounding-level discrepancy.
        assert result.var_95 != pytest.approx(former_buggy_result, abs=1e-3)
        assert abs(result.var_95 - former_buggy_result) > 0.03


class TestDeterministicRNG:
    def test_same_seed_produces_identical_result(self, monkeypatch):
        returns_a = _synthetic_returns(mean=0.0004, std=0.018, seed=11)
        returns_b = _synthetic_returns(mean=0.0002, std=0.02, seed=12)

        def fake_returns(tickers):
            return {"A": returns_a, "B": returns_b}

        monkeypatch.setattr("src.risk.monte_carlo._log_returns_by_ticker", fake_returns)

        result1 = calculate_portfolio_var(
            {"A": 0.5, "B": 0.5}, simulations=1000, rng=np.random.default_rng(seed=99)
        )
        result2 = calculate_portfolio_var(
            {"A": 0.5, "B": 0.5}, simulations=1000, rng=np.random.default_rng(seed=99)
        )

        assert result1.var_95 == result2.var_95
        assert result1.cvar_95 == result2.cvar_95
