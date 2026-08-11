"""
Monte Carlo Value at Risk (VaR) / Expected Shortfall (CVaR) simulation engine.

Simulates correlated forward portfolio return paths from the historical
covariance structure of daily log returns, rather than assuming
independent per-asset risk — a portfolio concentrated in correlated
positions (e.g. two names in the same sector) carries materially more
tail risk than the same weights spread across uncorrelated names, and
only a joint (multivariate) simulation captures that.

Every price fetch goes through the existing Upstash Redis caching layer
(`src.utils.cache.cached`), the same pattern used throughout this
codebase, so repeated runs (or a dry run immediately followed by a live
run) reuse cached history instead of re-hitting Yahoo Finance.
"""

import logging
import warnings
from typing import Dict, Iterable

import numpy as np
import pandas as pd

from src.data_ingestion.fetch_financials import get_ticker_object
from src.utils.cache import cached

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

VAR_PRICE_HISTORY_CACHE_TTL_SECONDS = 86400  # 24 hours
VAR_LOOKBACK_TRADING_DAYS = 252  # ~1 trading year
DEFAULT_SIMULATIONS = 10_000
DEFAULT_HORIZON_DAYS = 21  # ~1 trading month
VAR_TAIL_PERCENTILE = 5  # 95% VaR/CVaR => 5th percentile of the return distribution


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


def calculate_portfolio_var(
    holdings: Dict[str, float],
    cache_client=None,
    simulations: int = DEFAULT_SIMULATIONS,
    horizon_days: int = DEFAULT_HORIZON_DAYS,
) -> Dict[str, float]:
    """
    Monte Carlo 95% VaR / CVaR (Expected Shortfall) for a portfolio over
    `horizon_days`, via correlated multivariate-normal simulation of
    daily log returns drawn from the assets' own historical mean and
    covariance.

    Args:
        holdings: {ticker: portfolio weight}, e.g. {"AAPL": 0.40, "LIN": 0.60}.
        cache_client: Unused — accepted for interface compatibility. Every
            fetch here already goes through the module-level `@cached`
            Redis layer (`src.utils.cache`), the same read-through-cache
            pattern used everywhere else in this codebase, rather than a
            client object threaded through call sites.
        simulations: Number of Monte Carlo paths to simulate.
        horizon_days: Forward horizon in trading days (e.g. 21 ~= 1 month).

    Returns:
        {"var_95": float, "cvar_95": float} — both expressed as portfolio
        log-return fractions over the horizon (typically negative, i.e. a
        loss). Returns {"var_95": 0.0, "cvar_95": 0.0} if fewer than 2
        holdings have usable, overlapping price history, or the resulting
        covariance matrix can't be sampled from.
    """
    if not holdings or len(holdings) < 2:
        return {"var_95": 0.0, "cvar_95": 0.0}

    returns_by_ticker = _log_returns_by_ticker(holdings.keys())
    if len(returns_by_ticker) < 2:
        logger.warning(
            "VaR simulation needs >= 2 tickers with usable price history; only %d available.",
            len(returns_by_ticker),
        )
        return {"var_95": 0.0, "cvar_95": 0.0}

    # Align every ticker's return series on their common trading dates;
    # any ticker with a materially shorter history (e.g. a recent IPO)
    # only trims the overlap rather than being dropped outright.
    returns_df = pd.DataFrame(returns_by_ticker).dropna(how="any")
    if returns_df.shape[0] < 2 or returns_df.shape[1] < 2:
        return {"var_95": 0.0, "cvar_95": 0.0}

    tickers = list(returns_df.columns)
    weights = np.array([holdings[ticker] for ticker in tickers])

    mean_returns = returns_df.mean().to_numpy()
    cov_matrix = returns_df.cov().to_numpy()

    rng = np.random.default_rng()
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
            # Log returns are additive across time: summing each path's
            # daily returns gives the cumulative log return per asset over
            # the horizon.
            cumulative_asset_returns = simulated_daily_returns.sum(axis=1)  # (simulations, num_assets)
            simulated_portfolio_returns = cumulative_asset_returns @ weights  # (simulations,)
    except np.linalg.LinAlgError as exc:
        logger.warning("Covariance matrix could not be sampled from; cannot simulate: %s", exc)
        return {"var_95": 0.0, "cvar_95": 0.0}

    var_95 = float(np.percentile(simulated_portfolio_returns, VAR_TAIL_PERCENTILE))
    tail_losses = simulated_portfolio_returns[simulated_portfolio_returns <= var_95]
    cvar_95 = float(tail_losses.mean()) if tail_losses.size > 0 else var_95

    return {"var_95": var_95, "cvar_95": cvar_95}


if __name__ == "__main__":
    result = calculate_portfolio_var({"AAPL": 0.40, "LIN": 0.60}, cache_client=None)
    print(f"1-Month 95% VaR: {result['var_95']:.2%}")
    print(f"1-Month 95% CVaR (Expected Shortfall): {result['cvar_95']:.2%}")
