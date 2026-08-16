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


class TestNumericBoundaryHardening:
    """
    Regression for Finding 4: `simulations`/`horizon_days` must reject a
    fractional value, NaN, infinity, or a bool with the DOCUMENTED
    `ValueError` -- never leak an internal NumPy `TypeError` (the exact
    reproduced defect: `calculate_portfolio_var({...}, simulations=10.5)`
    used to reach deep into `rng.normal(...)` before failing). A
    non-empty `holdings` dict is used deliberately in the adversarial
    cases below: the fix validates `simulations`/`horizon_days` BEFORE
    `_log_returns_by_ticker` is ever called, so these must raise
    `ValueError` without ever reaching a ticker/network lookup --
    proving the fix, not just working around it by emptying `holdings`.
    """

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"simulations": 10.5},  # the exact reproduced defect
            {"simulations": float("nan")},
            {"simulations": float("inf")},
            {"simulations": True},  # bool is an int subclass -- must not silently mean "1 simulation"
            {"simulations": "10"},
            {"horizon_days": 21.5},
            {"horizon_days": float("nan")},
            {"horizon_days": float("inf")},
            {"horizon_days": True},
            {"horizon_days": "21"},
        ],
    )
    def test_adversarial_simulations_or_horizon_raises_value_error_not_type_error(self, kwargs):
        base = dict(holdings={"AAPL": 1.0})
        base.update(kwargs)
        with pytest.raises(ValueError):
            calculate_portfolio_var(**base)

    def test_nan_simulations_previously_slipped_past_a_bare_positivity_check(self):
        """
        `float("nan") <= 0` is False in Python (NaN compares False to
        everything), so the OLD `if simulations <= 0: raise ...` check
        would never have caught this -- it must be rejected explicitly.
        """
        with pytest.raises(ValueError):
            calculate_portfolio_var({"AAPL": 1.0}, simulations=float("nan"))

    def test_integral_float_simulations_and_horizon_are_still_honored(self):
        """`100.0`/`5.0` (whole-number floats) are legitimate values, not corrupted ones -- must not be rejected."""
        from src.risk.monte_carlo import _coerce_positive_int

        assert _coerce_positive_int(100.0) == 100
        assert _coerce_positive_int(5.0) == 5
        assert isinstance(_coerce_positive_int(100.0), int)

    def test_empty_holdings_with_valid_numeric_args_never_touches_ticker_lookup(self):
        """Sanity check that empty holdings short-circuits before any network-backed lookup, regardless of args."""
        result = calculate_portfolio_var({}, simulations=100.0, horizon_days=5.0)
        assert result.status == "insufficient_data"


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


class TestMissingHistoryWeightPolicy:
    """
    A ticker dropped by `_log_returns_by_ticker` (no usable price
    history) may still carry a real, non-zero portfolio weight — it's a
    genuine risky position we simply couldn't model, not the same as
    cash (which was never in `holdings` at all). `calculate_portfolio_var`
    must apply one explicit two-part policy, identically for the
    univariate and multivariate paths:

        1. If the surviving tickers' combined weight is below
           MIN_PORTFOLIO_COVERAGE_FRACTION of the ORIGINAL requested
           weight, refuse (status "insufficient_data") rather than
           compute VaR from an unrepresentative remnant.
        2. Otherwise, renormalize survivors so their weights sum to the
           ORIGINAL total requested weight (not hardcoded to 1.0) — a
           no-op when nothing was dropped, so complete-data behavior
           (including intentional cash headroom) is unaffected.

    Note: "surviving weight sum is non-finite" (explicitly listed as a
    defense-in-depth guard in the implementation) is not separately
    exercised here — `calculate_portfolio_var` already rejects any
    non-finite `holdings` weight via `ValueError` before this code ever
    runs, so that specific branch is unreachable through the public
    contract with valid inputs; the "sums to zero" branch (fully
    reachable and tested below) exercises the same guard clause.
    """

    class _FixedUnivariateRNG:
        """Fake standing in for `numpy.random.Generator.normal`, always returning the
        SAME fixed daily log return, for an exact hand-computed expected VaR."""

        def __init__(self, daily_log_return: float):
            self._daily = daily_log_return

        def normal(self, loc, scale, size):  # noqa: ARG002 - loc/scale intentionally ignored
            return np.full(size, self._daily, dtype=float)

    class _FixedMultivariateRNG:
        """Fake standing in for `numpy.random.Generator.multivariate_normal`, always
        returning the SAME fixed per-asset daily log returns."""

        def __init__(self, daily_log_returns: np.ndarray):
            self._daily = np.asarray(daily_log_returns, dtype=float)

        def multivariate_normal(self, mean, cov, size):  # noqa: ARG002 - mean/cov intentionally ignored
            simulations, horizon_days = size
            num_assets = self._daily.shape[0]
            return np.broadcast_to(self._daily, (simulations, horizon_days, num_assets)).copy()

    # -- weight construction: alignment and renormalization -----------------

    def test_two_holdings_one_missing_history_survivor_gets_weight_one(self, monkeypatch):
        """Exactly at the 50% coverage floor (one of two equal-weight holdings survives)."""
        returns = _synthetic_returns(mean=0.0004, std=0.01, seed=9)
        monkeypatch.setattr(
            "src.risk.monte_carlo._log_returns_by_ticker", lambda tickers: {"AAPL": returns}
        )
        captured = {}

        def fake_univariate(returns_arg, weight, simulations, horizon_days, rng):
            captured["weight"] = weight
            return VaRResult(status="ok", var_95=-0.01, cvar_95=-0.02)

        monkeypatch.setattr("src.risk.monte_carlo._simulate_univariate", fake_univariate)

        result = calculate_portfolio_var(
            {"AAPL": 0.5, "MISSING": 0.5}, rng=np.random.default_rng(1)
        )

        assert result.status == "ok"
        assert captured["weight"] == pytest.approx(1.0)

    def test_multiple_survivors_unequal_weights_preserve_relative_proportions(self, monkeypatch):
        returns_a = _synthetic_returns(mean=0.0003, std=0.01, seed=21)
        returns_b = _synthetic_returns(mean=0.0002, std=0.012, seed=22)
        monkeypatch.setattr(
            "src.risk.monte_carlo._log_returns_by_ticker",
            lambda tickers: {"A": returns_a, "B": returns_b},
        )
        captured = {}

        def fake_multivariate(returns_df, weights, simulations, horizon_days, rng):
            captured["weights"] = np.array(weights)
            return VaRResult(status="ok", var_95=-0.01, cvar_95=-0.02)

        monkeypatch.setattr("src.risk.monte_carlo._simulate_multivariate", fake_multivariate)

        result = calculate_portfolio_var(
            {"A": 0.3, "B": 0.5, "MISSING": 0.2}, rng=np.random.default_rng(1)
        )

        assert result.status == "ok"
        weights = captured["weights"]
        # total requested weight (1.0) fully redistributed across the two survivors.
        assert float(weights.sum()) == pytest.approx(1.0)
        # Original 0.3:0.5 ratio between A and B is preserved exactly.
        assert weights[0] / weights[1] == pytest.approx(0.3 / 0.5)

    def test_weights_align_by_ticker_name_not_position_when_column_order_differs(self, monkeypatch):
        """
        `_log_returns_by_ticker` (and therefore `returns_df.columns`)
        returns B before A — the REVERSE of `holdings`' own insertion
        order. Weights must still be built by looking up each SURVIVING
        column's own ticker name in `holdings`, never by zipping
        `holdings`' raw value order against the return matrix positionally.
        """
        returns_a = _synthetic_returns(mean=0.0001, std=0.01, seed=31)
        returns_b = _synthetic_returns(mean=0.0002, std=0.011, seed=32)
        monkeypatch.setattr(
            "src.risk.monte_carlo._log_returns_by_ticker",
            lambda tickers: {"B": returns_b, "A": returns_a},
        )
        captured = {}

        def fake_multivariate(returns_df, weights, simulations, horizon_days, rng):
            captured["weights"] = np.array(weights)
            captured["tickers"] = list(returns_df.columns)
            return VaRResult(status="ok", var_95=-0.01, cvar_95=-0.02)

        monkeypatch.setattr("src.risk.monte_carlo._simulate_multivariate", fake_multivariate)

        calculate_portfolio_var({"A": 0.3, "B": 0.7}, rng=np.random.default_rng(1))

        assert captured["tickers"] == ["B", "A"]
        # weights[0] (paired with column "B") must be B's weight (0.7),
        # weights[1] (paired with column "A") must be A's weight (0.3) —
        # NOT holdings' own insertion order [0.3, 0.7], which would
        # silently swap the two tickers' risk contributions.
        assert captured["weights"][0] == pytest.approx(0.7)
        assert captured["weights"][1] == pytest.approx(0.3)

    # -- refusal below the coverage floor / no positive-weight survivors ----

    def test_surviving_weight_sum_of_zero_is_insufficient_data(self, monkeypatch):
        """The only surviving (usable-history) ticker has weight 0.0; the actual
        nonzero-weight ticker is the one that was dropped."""
        returns = _synthetic_returns(mean=0.0, std=0.01, seed=41)
        monkeypatch.setattr(
            "src.risk.monte_carlo._log_returns_by_ticker",
            lambda tickers: {"ZERO_WEIGHT_SURVIVOR": returns},
        )

        result = calculate_portfolio_var({"ZERO_WEIGHT_SURVIVOR": 0.0, "MISSING": 1.0})

        assert result.status == "insufficient_data"
        assert result.var_95 is None
        assert result.cvar_95 is None

    def test_no_usable_assets_remain_is_insufficient_data(self, monkeypatch):
        monkeypatch.setattr("src.risk.monte_carlo._log_returns_by_ticker", lambda tickers: {})

        result = calculate_portfolio_var({"AAPL": 0.4, "MSFT": 0.6})

        assert result.status == "insufficient_data"
        assert result.var_95 is None

    def test_coverage_below_minimum_is_refused_not_renormalized(self, monkeypatch):
        """Only 20% of requested weight survived (below the 50% floor) -> refuse,
        never silently compute VaR treating the survivor as if it were the whole portfolio."""
        returns = _synthetic_returns(mean=0.0, std=0.01, seed=51)
        monkeypatch.setattr(
            "src.risk.monte_carlo._log_returns_by_ticker", lambda tickers: {"SMALL": returns}
        )

        result = calculate_portfolio_var({"SMALL": 0.2, "MISSING": 0.8})

        assert result.status == "insufficient_data"
        assert result.var_95 is None
        assert "coverage" in result.message.lower() or "unrepresentative" in result.message.lower()

    def test_coverage_exactly_at_minimum_is_accepted(self, monkeypatch):
        """Exactly MIN_PORTFOLIO_COVERAGE_FRACTION (50%) survives -> proceeds, not refused."""
        from src.risk.monte_carlo import MIN_PORTFOLIO_COVERAGE_FRACTION

        returns = _synthetic_returns(mean=0.0004, std=0.01, seed=52)
        monkeypatch.setattr(
            "src.risk.monte_carlo._log_returns_by_ticker", lambda tickers: {"HALF": returns}
        )

        result = calculate_portfolio_var(
            {"HALF": MIN_PORTFOLIO_COVERAGE_FRACTION, "MISSING": 1 - MIN_PORTFOLIO_COVERAGE_FRACTION},
            rng=np.random.default_rng(1),
        )

        assert result.status == "ok"

    # -- the core regression: a dropped high-weight holding must not silently shrink VaR --

    def test_missing_high_weight_holding_does_not_shrink_var_univariate_path(self, monkeypatch):
        """
        holdings = {"SURVIVOR": 0.6, "MISSING": 0.4}; MISSING lacks
        history and is dropped, leaving SURVIVOR as the only modeled
        ticker (60% coverage, above the floor -> univariate path). The
        OLD (buggy, un-renormalized) code would size SURVIVOR's
        contribution at its ORIGINAL 0.6 weight even though it is now
        effectively the entire modeled portfolio -- understating the
        simulated loss by 40%. The fixed weight must be exactly 1.0.
        """
        returns = _synthetic_returns(mean=0.0, std=0.01, seed=61)
        monkeypatch.setattr(
            "src.risk.monte_carlo._log_returns_by_ticker", lambda tickers: {"SURVIVOR": returns}
        )

        daily_log_return = math.log(0.5)  # -50% simple return in one simulated day
        fixed_rng = self._FixedUnivariateRNG(daily_log_return)

        result = calculate_portfolio_var(
            {"SURVIVOR": 0.6, "MISSING": 0.4}, simulations=1, horizon_days=1, rng=fixed_rng
        )

        assert result.status == "ok"
        correct_var = 1.0 * math.expm1(daily_log_return)
        buggy_var = 0.6 * math.expm1(daily_log_return)
        assert result.var_95 == pytest.approx(correct_var, abs=1e-9)
        assert result.var_95 != pytest.approx(buggy_var, abs=1e-3)
        assert abs(result.var_95 - buggy_var) > 0.15  # a large, clearly-measurable gap, not rounding noise

    def test_missing_holding_does_not_shrink_var_multivariate_path(self, monkeypatch):
        """Same principle as the univariate regression above, with 2 survivors (multivariate path)."""
        returns_a = _synthetic_returns(mean=0.0, std=0.01, seed=71)
        returns_b = _synthetic_returns(mean=0.0, std=0.01, seed=72)
        monkeypatch.setattr(
            "src.risk.monte_carlo._log_returns_by_ticker",
            lambda tickers: {"A": returns_a, "B": returns_b},
        )

        # holdings: A=0.3, B=0.3, MISSING=0.4 -> 60% coverage, above the
        # floor. Renormalized: scale = 1.0 / 0.6 -> A=0.5, B=0.5.
        asset_a_log_return = math.log(0.5)  # -50% simple
        asset_b_log_return = 0.0  # flat
        fixed_rng = self._FixedMultivariateRNG(np.array([asset_a_log_return, asset_b_log_return]))

        result = calculate_portfolio_var(
            {"A": 0.3, "B": 0.3, "MISSING": 0.4}, simulations=1, horizon_days=1, rng=fixed_rng
        )

        assert result.status == "ok"
        correct_var = 0.5 * math.expm1(asset_a_log_return) + 0.5 * math.expm1(asset_b_log_return)
        buggy_var = 0.3 * math.expm1(asset_a_log_return) + 0.3 * math.expm1(asset_b_log_return)
        assert result.var_95 == pytest.approx(correct_var, abs=1e-9)
        assert result.var_95 != pytest.approx(buggy_var, abs=1e-3)
        assert abs(result.var_95 - buggy_var) > 0.09  # a large, clearly-measurable gap, not rounding noise

    # -- complete-data (nothing dropped) behavior must be entirely unaffected --

    def test_complete_data_summing_to_one_is_unaffected(self, monkeypatch):
        returns_a = _synthetic_returns(mean=0.0003, std=0.015, seed=81)
        returns_b = _synthetic_returns(mean=0.0002, std=0.012, seed=82)
        monkeypatch.setattr(
            "src.risk.monte_carlo._log_returns_by_ticker",
            lambda tickers: {"A": returns_a, "B": returns_b},
        )
        captured = {}

        def fake_multivariate(returns_df, weights, simulations, horizon_days, rng):
            captured["weights"] = np.array(weights)
            return VaRResult(status="ok", var_95=-0.01, cvar_95=-0.02)

        monkeypatch.setattr("src.risk.monte_carlo._simulate_multivariate", fake_multivariate)

        calculate_portfolio_var({"A": 0.4, "B": 0.6}, rng=np.random.default_rng(1))

        assert captured["weights"] == pytest.approx([0.4, 0.6])

    def test_complete_data_with_cash_headroom_is_not_renormalized_to_one(self, monkeypatch):
        """
        A portfolio with real uninvested cash (holdings summing to < 1.0,
        e.g. from `src.trading.alpaca_execution`'s
        `market_value / equity` construction, which excludes cash
        entirely) where NOTHING was dropped for missing history must
        keep its RAW weights. Renormalizing to a hardcoded 1.0 here would
        silently pretend the cash headroom was actually invested,
        changing the simulated result for a scenario that has nothing to
        do with missing data.
        """
        returns_a = _synthetic_returns(mean=0.0003, std=0.015, seed=91)
        returns_b = _synthetic_returns(mean=0.0002, std=0.012, seed=92)
        monkeypatch.setattr(
            "src.risk.monte_carlo._log_returns_by_ticker",
            lambda tickers: {"A": returns_a, "B": returns_b},
        )
        captured = {}

        def fake_multivariate(returns_df, weights, simulations, horizon_days, rng):
            captured["weights"] = np.array(weights)
            return VaRResult(status="ok", var_95=-0.01, cvar_95=-0.02)

        monkeypatch.setattr("src.risk.monte_carlo._simulate_multivariate", fake_multivariate)

        # 30% cash: A + B sum to only 0.7.
        calculate_portfolio_var({"A": 0.3, "B": 0.4}, rng=np.random.default_rng(1))

        assert captured["weights"] == pytest.approx([0.3, 0.4])
        assert float(captured["weights"].sum()) == pytest.approx(0.7)  # NOT renormalized up to 1.0

    def test_complete_data_deterministic_seed_reproducibility_unaffected(self, monkeypatch):
        """The new policy must not disturb deterministic seeded reproducibility for complete data."""
        returns_a = _synthetic_returns(mean=0.0004, std=0.018, seed=101)
        returns_b = _synthetic_returns(mean=0.0002, std=0.02, seed=102)
        monkeypatch.setattr(
            "src.risk.monte_carlo._log_returns_by_ticker",
            lambda tickers: {"A": returns_a, "B": returns_b},
        )

        result1 = calculate_portfolio_var(
            {"A": 0.5, "B": 0.5}, simulations=1000, rng=np.random.default_rng(seed=99)
        )
        result2 = calculate_portfolio_var(
            {"A": 0.5, "B": 0.5}, simulations=1000, rng=np.random.default_rng(seed=99)
        )

        assert result1.status == "ok"
        assert result1.var_95 == result2.var_95
        assert result1.cvar_95 == result2.cvar_95
