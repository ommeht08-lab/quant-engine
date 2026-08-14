"""
Monte Carlo Value at Risk (VaR) / Expected Shortfall (CVaR) simulation engine.

Simulates correlated forward portfolio return paths from the historical
covariance structure of daily log returns, rather than assuming
independent per-asset risk — a portfolio concentrated in correlated
positions (e.g. two names in the same sector) carries materially more
tail risk than the same weights spread across uncorrelated names, and
only a joint (multivariate) simulation captures that. A single-holding
portfolio falls back to a univariate normal simulation using that one
asset's own mean/variance instead of being treated as unavailable.

Every price fetch goes through the existing Upstash Redis caching layer
(`src.utils.cache.cached`), the same pattern used throughout this
codebase, so repeated runs (or a dry run immediately followed by a live
run) reuse cached history instead of re-hitting Yahoo Finance.

Result shape
------------
`calculate_portfolio_var` returns a `VaRResult`, not a bare dict of
floats: `status` distinguishes a genuinely computed result ("ok") from
data that simply isn't available yet ("insufficient_data") or a
computation failure ("error") — a caller can no longer mistake "VaR is
unavailable" for "VaR is exactly zero," which a plain `{"var_95": 0.0}`
sentinel made impossible to tell apart. `var_95`/`cvar_95` on an "ok"
result are **simple (arithmetic) returns**, not the log returns used
internally for the simulation itself. For a loss (negative return), the
raw cumulative log return is always MORE negative than the corresponding
simple return (`ln(1+R) <= R` for all `R > -1`) — so multiplying dollar
equity by the raw log return instead of the converted simple return
OVERSTATES the loss magnitude, and increasingly so at larger VaR values.

Portfolio-return aggregation: convert PER ASSET, then weight-sum
------------------------------------------------------------------
Simple (arithmetic) returns combine linearly across a portfolio — for
value weights `w_i` and each asset's simple return `R_i`, the exact
portfolio simple return is `sum(w_i * R_i)`. Log returns do NOT combine
this way: `sum(w_i * log_return_i)` is only a first-order approximation
of the portfolio's true log return, and the approximation error grows
with the size of the move — exactly wrong for a VaR tail calculation,
which by definition cares about large moves. Both `_simulate_univariate`
and `_simulate_multivariate` therefore convert each asset's simulated
CUMULATIVE LOG return to a simple return (`math.expm1`, applied
per-asset, per-simulated-path) BEFORE weighting/summing into a portfolio
return — never the other way around. `_summarize` receives, and reports,
already-simple portfolio returns; it does not itself perform any
log-to-simple conversion.
"""

import logging
import math
import warnings
from dataclasses import dataclass
from typing import Dict, Iterable, Optional

import numpy as np
import pandas as pd

from src.data_ingestion.fetch_financials import get_ticker_object
from src.utils.cache import cached

logger = logging.getLogger(__name__)

VAR_PRICE_HISTORY_CACHE_TTL_SECONDS = 86400  # 24 hours
VAR_LOOKBACK_TRADING_DAYS = 252  # ~1 trading year
DEFAULT_SIMULATIONS = 10_000
DEFAULT_HORIZON_DAYS = 21  # ~1 trading month
VAR_TAIL_PERCENTILE = 5  # 95% VaR/CVaR => 5th percentile of the return distribution


@dataclass
class VaRResult:
    """
    Result of a `calculate_portfolio_var` run.

    Attributes:
        status: "ok" (both fields are populated simple returns),
            "insufficient_data" (not enough usable price history — an
            everyday, non-exceptional outcome, e.g. a brand-new ticker),
            or "error" (the simulation itself failed, e.g. a covariance
            matrix that couldn't be sampled from).
        var_95: 95% VaR as a simple-return fraction (e.g. -0.05 == -5%),
            or None unless status == "ok".
        cvar_95: 95% CVaR (Expected Shortfall), same units, or None
            unless status == "ok".
        message: Human-readable detail, always populated when status
            isn't "ok".
    """

    status: str
    var_95: Optional[float] = None
    cvar_95: Optional[float] = None
    message: Optional[str] = None

    @property
    def is_ok(self) -> bool:
        return self.status == "ok" and self.var_95 is not None and self.cvar_95 is not None


@cached(ttl_seconds=VAR_PRICE_HISTORY_CACHE_TTL_SECONDS, prefix="var_price_history")
def _get_daily_close_history(ticker_obj):
    """Fetch ~1 trading year of daily price history, or None on failure/empty."""
    try:
        history = ticker_obj.history(period="1y")
    except Exception as exc:  # noqa: BLE001 - one bad ticker shouldn't kill the simulation
        logger.warning("VaR price history fetch failed for %s: %s", ticker_obj.ticker, exc)
        return None

    if history is None or history.empty:
        return None
    return history


def _log_returns_by_ticker(tickers: Iterable[str]) -> Dict[str, pd.Series]:
    """
    Fetch price history and compute daily log returns for each ticker,
    keyed by symbol. A ticker whose history can't be fetched, or has
    fewer than 2 usable closes, is simply omitted rather than aborting
    the whole simulation.
    """
    returns_by_ticker: Dict[str, pd.Series] = {}
    for ticker in tickers:
        try:
            ticker_obj = get_ticker_object(ticker)
        except ValueError as exc:
            logger.warning("Invalid ticker for VaR simulation: %s", exc)
            continue

        history = _get_daily_close_history(ticker_obj)
        if history is None:
            continue

        closes = history["Close"].dropna().tail(VAR_LOOKBACK_TRADING_DAYS)
        if len(closes) < 2:
            continue

        if closes.index.tz is not None:
            closes.index = closes.index.tz_localize(None)

        log_returns = np.log(closes / closes.shift(1)).dropna()
        if log_returns.empty:
            continue

        returns_by_ticker[ticker] = log_returns

    return returns_by_ticker


def _summarize(simulated_simple_returns: np.ndarray) -> VaRResult:
    """
    Percentile/tail-mean the simulated PORTFOLIO SIMPLE-return paths.

    Callers must pass already-converted simple returns — see the module
    docstring's "Portfolio-return aggregation" section — never raw log
    returns; this function does not itself perform a log-to-simple
    conversion (unlike an earlier version of this module, which
    aggregated log returns across assets and converted only once at the
    very end — the aggregation itself must happen in simple-return space
    for the weighted sum to be exact, not just the final unit
    conversion). Returns an "error" result if the simulation itself
    produced any non-finite value.
    """
    if simulated_simple_returns.size == 0 or not np.all(np.isfinite(simulated_simple_returns)):
        return VaRResult(status="error", message="Simulation produced non-finite or empty results.")

    var_95 = float(np.percentile(simulated_simple_returns, VAR_TAIL_PERCENTILE))
    tail_losses = simulated_simple_returns[simulated_simple_returns <= var_95]
    cvar_95 = float(tail_losses.mean()) if tail_losses.size > 0 else var_95

    return VaRResult(status="ok", var_95=var_95, cvar_95=cvar_95)


def _simulate_univariate(
    returns: pd.Series, weight: float, simulations: int, horizon_days: int, rng: np.random.Generator
) -> VaRResult:
    """Single-asset fallback: a univariate normal simulation using that asset's own mean/std."""
    mean_return = float(returns.mean())
    std_return = float(returns.std())
    if not math.isfinite(std_return) or std_return <= 0:
        return VaRResult(
            status="insufficient_data",
            message="The single usable holding's return series has zero or non-finite variance.",
        )

    with warnings.catch_warnings():
        # See _simulate_multivariate for why this specific, narrowly-scoped
        # suppression is safe (a documented, verified-benign BLAS quirk on
        # large batched draws, not evidence of an actual numerical problem).
        warnings.filterwarnings("ignore", category=RuntimeWarning)
        simulated_daily_returns = rng.normal(mean_return, std_return, size=(simulations, horizon_days))
        cumulative_log_return = simulated_daily_returns.sum(axis=1)
        # Convert THIS asset's cumulative log return to a simple return
        # before applying `weight` — simple returns weight linearly
        # (portfolio_simple_return = weight * asset_simple_return when
        # this is the sole contributing holding), log returns do not.
        simulated_simple_returns = np.expm1(cumulative_log_return) * weight

    return _summarize(simulated_simple_returns)


def _simulate_multivariate(
    returns_df: pd.DataFrame, weights: np.ndarray, simulations: int, horizon_days: int, rng: np.random.Generator
) -> VaRResult:
    """Multi-asset case: correlated multivariate-normal simulation over the historical covariance."""
    mean_returns = returns_df.mean().to_numpy()
    cov_matrix = returns_df.cov().to_numpy()

    try:
        # size=(simulations, horizon_days) draws one correlated return
        # vector (length = number of assets) per simulated day, for every
        # simulated path, in a single vectorized call. On some BLAS
        # backends (e.g. macOS Accelerate) these large batched matmuls
        # emit spurious "overflow"/"divide by zero"/"invalid value"
        # RuntimeWarnings from discarded intermediate buffers even though
        # every actual result is finite and statistically valid (verified:
        # never any NaN/Inf in the output) — suppressed here, scoped
        # tightly around just the large-matmul calls, rather than left to
        # alarm operators reading the trading log.
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", category=RuntimeWarning)
            simulated_daily_returns = rng.multivariate_normal(
                mean_returns, cov_matrix, size=(simulations, horizon_days)
            )
            # Log returns are additive across TIME: summing each path's
            # daily returns gives the cumulative log return per asset over
            # the horizon. They are NOT additive across ASSETS, though —
            # convert each asset's cumulative log return to a simple
            # return first (`expm1`), THEN weight-sum those simple
            # returns into the portfolio return. Weight-summing the LOG
            # returns directly (the former approach) is only a first-
            # order approximation that increasingly understates losses
            # as the simulated move gets larger — exactly the regime a
            # VaR tail calculation is meant to capture accurately.
            cumulative_asset_log_returns = simulated_daily_returns.sum(axis=1)  # (simulations, num_assets)
            cumulative_asset_simple_returns = np.expm1(cumulative_asset_log_returns)  # (simulations, num_assets)
            simulated_portfolio_returns = cumulative_asset_simple_returns @ weights  # (simulations,)
    except np.linalg.LinAlgError as exc:
        return VaRResult(status="error", message=f"Covariance matrix could not be sampled from: {exc}")

    return _summarize(simulated_portfolio_returns)


def calculate_portfolio_var(
    holdings: Dict[str, float],
    cache_client=None,
    simulations: int = DEFAULT_SIMULATIONS,
    horizon_days: int = DEFAULT_HORIZON_DAYS,
    rng: Optional[np.random.Generator] = None,
) -> VaRResult:
    """
    Monte Carlo 95% VaR / CVaR (Expected Shortfall) for a portfolio over
    `horizon_days`, via simulation of daily log returns drawn from the
    assets' own historical mean and covariance — multivariate (correlated)
    for 2+ usable holdings, univariate for exactly 1.

    Args:
        holdings: {ticker: portfolio weight}, e.g. {"AAPL": 0.40, "LIN": 0.60}.
        cache_client: Unused — accepted for interface compatibility. Every
            fetch here already goes through the module-level `@cached`
            Redis layer (`src.utils.cache`), the same read-through-cache
            pattern used everywhere else in this codebase, rather than a
            client object threaded through call sites.
        simulations: Number of Monte Carlo paths to simulate. Must be positive.
        horizon_days: Forward horizon in trading days (e.g. 21 ~= 1 month). Must be positive.
        rng: Optional `numpy.random.Generator` for deterministic output
            (e.g. `np.random.default_rng(seed=42)` in tests). Defaults to
            a fresh, non-deterministic generator.

    Returns:
        A `VaRResult`. See the class docstring for the status/units contract.

    Raises:
        ValueError: `simulations`/`horizon_days` aren't positive, or any
            holdings weight is negative or non-finite — these are caller
            bugs, distinct from the `"insufficient_data"`/`"error"`
            statuses used for ordinary real-world data gaps.
    """
    if simulations <= 0:
        raise ValueError("simulations must be a positive integer.")
    if horizon_days <= 0:
        raise ValueError("horizon_days must be a positive integer.")

    if not holdings:
        return VaRResult(status="insufficient_data", message="No holdings provided.")

    weights_array = np.array(list(holdings.values()), dtype=float)
    if not np.all(np.isfinite(weights_array)):
        raise ValueError("Holdings weights must all be finite.")
    if np.any(weights_array < 0):
        raise ValueError("Holdings weights must all be non-negative.")

    returns_by_ticker = _log_returns_by_ticker(holdings.keys())
    if not returns_by_ticker:
        logger.warning("VaR simulation: no ticker in the portfolio had usable price history.")
        return VaRResult(status="insufficient_data", message="No ticker had usable price history.")

    # Align every ticker's return series on their common trading dates;
    # any ticker with a materially shorter history (e.g. a recent IPO)
    # only trims the overlap rather than being dropped outright.
    returns_df = pd.DataFrame(returns_by_ticker).dropna(how="any")
    if returns_df.shape[0] < 2:
        return VaRResult(
            status="insufficient_data",
            message="Fewer than 2 overlapping return observations across the usable holdings.",
        )

    rng = rng if rng is not None else np.random.default_rng()
    tickers = list(returns_df.columns)
    weights = np.array([holdings[ticker] for ticker in tickers])

    if len(tickers) == 1:
        return _simulate_univariate(returns_df.iloc[:, 0], float(weights[0]), simulations, horizon_days, rng)
    return _simulate_multivariate(returns_df, weights, simulations, horizon_days, rng)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    result = calculate_portfolio_var({"AAPL": 0.40, "LIN": 0.60}, rng=np.random.default_rng(seed=7))
    if result.is_ok:
        print(f"1-Month 95% VaR: {result.var_95:.2%}")
        print(f"1-Month 95% CVaR (Expected Shortfall): {result.cvar_95:.2%}")
    else:
        print(f"VaR unavailable ({result.status}): {result.message}")
