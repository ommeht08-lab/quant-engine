"""
Black-Scholes-Merton (BSM) options pricing, used to size a SPY put hedge
against the portfolio's Monte Carlo Value at Risk
(`src.risk.monte_carlo.calculate_portfolio_var`).

Hedge sizing (`calculate_spy_hedge`) uses a scenario-based method rather
than a linear Delta-only approximation: it prices the candidate put at
the current SPY spot AND at an assumed stressed spot
(`spy_price * (1 - stress_move_fraction)`), and sizes the number of
contracts from the *modeled option payoff* under that scenario — which
captures the put's actual convexity (gamma) within the stress move,
unlike a pure Delta-linear estimate. It still ignores theta decay and
changes in implied volatility over the hedge's life, and the stress-move
magnitude itself is a stated assumption, not derived from the VaR
horizon — appropriate for a first-order daily hedge-sizing check, not a
precise options risk model.
"""

import logging
import math
from typing import Optional

from scipy.stats import norm

logger = logging.getLogger(__name__)

CONTRACT_MULTIPLIER = 100  # one standard US equity option contract covers 100 shares
DEFAULT_STRESS_MOVE_FRACTION = 0.07  # assumed SPY drawdown scenario used to size the hedge's payoff


def calculate_bsm_d1_d2(
    spot_price: float,
    strike_price: float,
    time_to_expiry_years: float,
    risk_free_rate: float,
    implied_vol: float,
) -> tuple:
    """
    The two standard BSM intermediate terms:

        d1 = (ln(S/K) + (r + sigma^2/2) * T) / (sigma * sqrt(T))
        d2 = d1 - sigma * sqrt(T)

    Args:
        spot_price: Current price of the underlying (S).
        strike_price: Option strike price (K).
        time_to_expiry_years: Time to expiry, in years (T).
        risk_free_rate: Annualized risk-free rate (r), as a decimal.
        implied_vol: Annualized implied volatility (sigma), as a decimal.

    Returns:
        (d1, d2) tuple of floats.
    """
    sqrt_t = math.sqrt(time_to_expiry_years)
    d1 = (
        math.log(spot_price / strike_price)
        + (risk_free_rate + 0.5 * implied_vol**2) * time_to_expiry_years
    ) / (implied_vol * sqrt_t)
    d2 = d1 - implied_vol * sqrt_t
    return d1, d2


def calculate_bsm_put_price(
    spot_price: float,
    strike_price: float,
    time_to_expiry_years: float,
    risk_free_rate: float,
    implied_vol: float,
) -> float:
    """
    BSM European put price:

        P = K * exp(-r*T) * N(-d2) - S * N(-d1)

    Returns:
        The put's theoretical price as a float.
    """
    d1, d2 = calculate_bsm_d1_d2(
        spot_price, strike_price, time_to_expiry_years, risk_free_rate, implied_vol
    )
    return (
        strike_price * math.exp(-risk_free_rate * time_to_expiry_years) * norm.cdf(-d2)
        - spot_price * norm.cdf(-d1)
    )


def calculate_bsm_put_delta(
    spot_price: float,
    strike_price: float,
    time_to_expiry_years: float,
    risk_free_rate: float,
    implied_vol: float,
) -> float:
    """
    BSM put Delta:

        Delta_put = N(d1) - 1

    Always in [-1, 0] — a long put's value moves inversely with the
    underlying.

    Returns:
        The put's Delta as a float.
    """
    d1, _ = calculate_bsm_d1_d2(
        spot_price, strike_price, time_to_expiry_years, risk_free_rate, implied_vol
    )
    return norm.cdf(d1) - 1.0


def calculate_spy_hedge(
    portfolio_var_dollars: float,
    spy_price: float,
    strike_price: float,
    days_to_expiry: int = 30,
    implied_vol: float = 0.15,
    risk_free_rate: float = 0.04,
    stress_move_fraction: float = DEFAULT_STRESS_MOVE_FRACTION,
    hedge_budget_dollars: Optional[float] = None,
    max_contracts: Optional[int] = None,
) -> int:
    """
    Size a SPY put hedge intended to cover `portfolio_var_dollars` of
    downside exposure under an assumed SPY stress scenario, by pricing
    the put at both the current spot and a stressed spot and sizing from
    the modeled payoff difference (not a linear Delta approximation):

        stressed_spot     = spy_price * (1 - stress_move_fraction)
        pnl_per_contract  = (BSM_put_price(stressed_spot, ...) - BSM_put_price(spy_price, ...)) * 100
        contracts         = ceil(portfolio_var_dollars / pnl_per_contract)

    Rounds UP (`ceil`, not floor) since the goal is to fully cover a
    stated loss — under-hedging by a fraction of a contract defeats the
    purpose. `hedge_budget_dollars`/`max_contracts`, if given, are then
    applied as hard ceilings on top of that; either can leave the sized
    hedge covering less than the full stated VaR — that's the point of
    having a budget, not a bug.

    Args:
        portfolio_var_dollars: Dollar amount of portfolio VaR to cover
            (a positive number — the magnitude of the loss, not signed).
        spy_price: Current SPY price (the BSM spot price).
        strike_price: Put strike price — pass the *actual* listed
            contract's strike, not a synthetic ATM value, once a real
            contract has been selected (see
            `src.trading.alpaca_execution.execute_spy_var_hedge`).
        days_to_expiry: Days to the put's actual expiry (calendar days).
        implied_vol: Assumed/observed annualized implied volatility (decimal).
        risk_free_rate: Assumed annualized risk-free rate (decimal).
        stress_move_fraction: Assumed SPY drawdown scenario used to price
            the stressed put, e.g. 0.07 == a 7% SPY decline. This is a
            stated modeling assumption, not derived from the VaR horizon.
        hedge_budget_dollars: Optional hard cap on total premium spent
            (`contracts * current_put_price * 100`).
        max_contracts: Optional hard cap on the number of contracts.

    Returns:
        The integer number of put contracts to buy (>= 0). Returns 0 for
        any non-positive/invalid input, a non-positive modeled payoff
        (e.g. `stress_move_fraction` outside `(0, 1)`), or a zero/negative
        `hedge_budget_dollars` — "no hedge needed/possible" is a valid
        everyday outcome (e.g. VaR is unavailable) rather than an error.
    """
    if (
        portfolio_var_dollars is None
        or spy_price is None
        or strike_price is None
        or portfolio_var_dollars <= 0
        or spy_price <= 0
        or strike_price <= 0
        or days_to_expiry <= 0
        or implied_vol <= 0
        or not (0 < stress_move_fraction < 1)
    ):
        return 0

    time_to_expiry_years = days_to_expiry / 365.0
    current_put_price = calculate_bsm_put_price(
        spy_price, strike_price, time_to_expiry_years, risk_free_rate, implied_vol
    )
    stressed_spot = spy_price * (1 - stress_move_fraction)
    stressed_put_price = calculate_bsm_put_price(
        stressed_spot, strike_price, time_to_expiry_years, risk_free_rate, implied_vol
    )

    pnl_per_contract = (stressed_put_price - current_put_price) * CONTRACT_MULTIPLIER
    if pnl_per_contract <= 0 or not math.isfinite(pnl_per_contract):
        return 0

    raw_contracts = portfolio_var_dollars / pnl_per_contract
    if not math.isfinite(raw_contracts):
        return 0

    contracts = math.ceil(raw_contracts)

    if hedge_budget_dollars is not None:
        if hedge_budget_dollars <= 0 or current_put_price <= 0:
            return 0
        max_affordable = math.floor(hedge_budget_dollars / (current_put_price * CONTRACT_MULTIPLIER))
        contracts = min(contracts, max_affordable)

    if max_contracts is not None:
        contracts = min(contracts, max_contracts)

    return max(contracts, 0)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    var_dollars = 5_000.0
    spy_price = 580.0
    contracts = calculate_spy_hedge(var_dollars, spy_price, strike_price=spy_price)
    print(f"Portfolio VaR: ${var_dollars:,.2f}")
    print(f"SPY price: ${spy_price:,.2f}")
    print(f"ATM SPY put contracts to buy: {contracts}")
