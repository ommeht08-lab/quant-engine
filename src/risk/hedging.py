"""
Black-Scholes-Merton (BSM) options pricing, used to size a SPY put hedge
against the portfolio's Monte Carlo Value at Risk
(`src.risk.monte_carlo.calculate_portfolio_var`).

The hedge sizing logic (`calculate_spy_hedge`) asks: "how many SPY put
contracts, each moving by its own Delta per $1 move in SPY, are needed so
that a 1-for-1 offsetting move in the puts would cover the portfolio's
dollar VaR?" It is a linear (Delta-only) approximation — it ignores
gamma, theta decay, and changes in implied volatility over the hedge's
life — appropriate for a first-order daily hedge-sizing check, not a
precise options risk model.
"""

import logging
import math

from scipy.stats import norm

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

CONTRACT_MULTIPLIER = 100  # one standard US equity option contract covers 100 shares


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
) -> int:
    """
    Size a SPY put hedge intended to offset `portfolio_var_dollars` of
    downside exposure, using the put's BSM Delta:

        Contracts = portfolio_var_dollars / (abs(Put_Delta) * spy_price * 100)

    i.e. each contract's dollar exposure per $1 move in SPY is
    `abs(Put_Delta) * spy_price * 100` (100 shares/contract); dividing
    the total dollar VaR by that per-contract sensitivity gives the
    number of contracts whose combined Delta-hedge notional matches it.

    Args:
        portfolio_var_dollars: Dollar amount of portfolio VaR to offset
            (a positive number — the magnitude of the loss, not signed).
        spy_price: Current SPY price, used both as the BSM spot price and
            (via `strike_price`) to size each contract's exposure.
        strike_price: Put strike price. Pass `spy_price` itself for an
            at-the-money (ATM) hedge.
        days_to_expiry: Days to the put's expiry (calendar days).
        implied_vol: Assumed annualized implied volatility (decimal).
        risk_free_rate: Assumed annualized risk-free rate (decimal).

    Returns:
        The integer number of put contracts to buy (floored, never
        negative). Returns 0 for any non-positive/invalid input
        (`portfolio_var_dollars`, `spy_price`, `strike_price`,
        `days_to_expiry`, or `implied_vol` <= 0) rather than raising,
        since "no hedge needed/possible" is a valid everyday outcome
        (e.g. VaR is 0 because fewer than 2 holdings are priced).
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
    ):
        return 0

    time_to_expiry_years = days_to_expiry / 365.0
    put_delta = calculate_bsm_put_delta(
        spot_price=spy_price,
        strike_price=strike_price,
        time_to_expiry_years=time_to_expiry_years,
        risk_free_rate=risk_free_rate,
        implied_vol=implied_vol,
    )

    per_contract_exposure = abs(put_delta) * spy_price * CONTRACT_MULTIPLIER
    if per_contract_exposure <= 0:
        return 0

    contracts = portfolio_var_dollars / per_contract_exposure
    if not math.isfinite(contracts):
        return 0

    return int(math.floor(contracts))


if __name__ == "__main__":
    var_dollars = 5_000.0
    spy_price = 580.0
    contracts = calculate_spy_hedge(var_dollars, spy_price, strike_price=spy_price)
    print(f"Portfolio VaR: ${var_dollars:,.2f}")
    print(f"SPY price: ${spy_price:,.2f}")
    print(f"ATM SPY put contracts to buy: {contracts}")
