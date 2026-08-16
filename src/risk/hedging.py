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

`hedge_budget_dollars` is a THEORETICAL premium ceiling, not an
enforceable actual-spend ceiling
-------------------------------------------------------------------------
`calculate_spy_hedge`'s `hedge_budget_dollars` parameter caps the number
of contracts against `current_put_price` — this module's own BSM-modeled
theoretical price at the current spot/strike/expiry/implied-vol inputs,
computed here with no live market data. The actual order this sizing
feeds (`src.trading.alpaca_execution.execute_spy_var_hedge`) submits a
real MARKET order, which fills at whatever the option's real bid/ask
happens to be at that moment — a real spread, real slippage, and a real
implied volatility that can all differ from `HEDGE_IMPLIED_VOL`'s assumed
value. `hedge_budget_dollars` therefore bounds a MODELED estimate of
premium spend, not a guarantee of actual premium spend: a real
fill can cost more (or less) than the number this function computed.
This is documented here explicitly as a known, currently-unaddressed
limitation (see `docs/limitations-register.md` L-016) rather than left
implicit in a parameter name that reads as a hard cap. Closing this gap
for real would require fetching a live option quote and submitting a
LIMIT order bounded by that quote before this budget check can be an
enforceable ceiling on actual spend — not implemented in this pass; see
that limitation entry for why (no option-quote data client currently
exists anywhere in this codebase, and adding one is a genuine scope
expansion, not a bounded fix).
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

    Raises:
        This function has NO hardening of its own and is NOT covered by
        `calculate_spy_hedge`'s "never raises" contract — it is a thin,
        direct implementation of the textbook formula. A caller invoking
        it directly with an invalid/extreme input can trigger, among
        others: `ZeroDivisionError` (`implied_vol=0` or
        `time_to_expiry_years=0`), `ValueError` (`math.log`/`math.sqrt`
        on a non-positive argument, e.g. `spot_price<=0`,
        `strike_price<=0`, or `time_to_expiry_years<0`), or
        `OverflowError` (an astronomically large-but-finite
        `implied_vol`/`risk_free_rate`, e.g. `1e308`, overflowing the
        `** 2`/multiplication inside the `d1` numerator). Only
        `calculate_spy_hedge` validates and defends against these before
        calling into this function.
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

    Raises:
        This function has NO hardening of its own — see
        `calculate_bsm_d1_d2`'s docstring; it calls that function
        directly and additionally calls `math.exp(-risk_free_rate *
        time_to_expiry_years)`, which itself raises `OverflowError` for
        an extreme-but-finite `risk_free_rate`/`time_to_expiry_years`
        combination (e.g. `risk_free_rate=-1e308`). Only
        `calculate_spy_hedge` validates and defends against these before
        calling into this function.
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

    Raises:
        This function has NO hardening of its own — see
        `calculate_bsm_d1_d2`'s docstring; it calls that function
        directly and inherits every failure mode documented there. It is
        not called anywhere in `calculate_spy_hedge`'s sizing path
        (scenario-based, not Delta-linear — see the module docstring) and
        has no caller in this codebase that validates its inputs.
    """
    d1, _ = calculate_bsm_d1_d2(
        spot_price, strike_price, time_to_expiry_years, risk_free_rate, implied_vol
    )
    return norm.cdf(d1) - 1.0


# Bounds beyond which a "finite" implied_vol/risk_free_rate is no longer
# economically plausible AND can overflow the BSM math itself — e.g.
# `implied_vol ** 2` on an astronomically large-but-finite float (like
# `1e308`) raises `OverflowError` in CPython even though the value
# itself passes `math.isfinite`, and `math.exp(-risk_free_rate * T)`
# raises `OverflowError` once its argument exceeds roughly 709. These
# bounds are deliberately generous: real SPY implied volatility
# essentially never exceeds ~300% even in a severe market dislocation,
# and risk-free rates essentially never exceed +/-100% — no legitimate
# caller value is anywhere near either bound.
MAX_ABS_IMPLIED_VOL = 10.0  # 1,000% annualized -- already economically absurd
MAX_ABS_RISK_FREE_RATE = 5.0  # +/-500% annualized -- already economically absurd


def _is_finite_number(value) -> bool:
    """
    Genuinely non-raising: True for a real, finite `int`/`float` —
    rejects `None`, `bool` (a `bool` is technically an `int` subclass in
    Python; silently treating `True`/`False` as `1`/`0` here would be an
    easy, real mistake, not a defensive-programming nicety), NaN, and
    +/-infinity.

    Deliberately does NOT call `math.isfinite(value)` on an `int` — a
    Python `int` is arbitrary-precision and always finite by definition
    (it has no NaN/infinity representation), but `math.isfinite` still
    has to convert its argument to a C `double` first, and that
    conversion itself raises `OverflowError` for an `int` too large to
    fit in a float (e.g. `10**10000`). Calling `math.isfinite` on an
    `int` this large would make this "non-raising" check raise, which in
    turn would make `calculate_spy_hedge`'s "never raises" contract
    false for exactly the inputs it exists to guard against. `float`
    values are always safe to pass to `math.isfinite` directly — an
    actual `float` object can only ever be a finite value, NaN, or
    +/-infinity; the conversion step that can overflow only applies to
    `int` inputs.
    """
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return True
    if isinstance(value, float):
        return math.isfinite(value)
    return False


def _coerce_whole_number(value, *, minimum: int) -> Optional[int]:
    """
    Return `value` as a genuine `int` if it is a whole number >= `minimum`
    — an `int`, or a `float` with no fractional part (e.g. `30.0`) — and
    not a `bool`. Returns `None` for anything else: a fractional float
    (e.g. `1.5`), a non-finite float, a bool, a value below `minimum`, or
    a non-numeric type. Used to validate `days_to_expiry` and
    `max_contracts` without silently truncating a corrupted fractional
    value into an apparently-valid integer.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= minimum else None
    if isinstance(value, float) and math.isfinite(value) and value.is_integer():
        whole = int(value)
        return whole if whole >= minimum else None
    return None


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
        hedge_budget_dollars: Optional cap on total premium spent, but
            ONLY against this function's own BSM-modeled theoretical
            `current_put_price` (`contracts * current_put_price * 100`)
            — NOT a guarantee of actual execution cost. A real market
            order can fill at a different price than this module's
            modeled estimate (real spread, real slippage, real implied
            volatility). See this module's docstring and
            `docs/limitations-register.md` L-016.
        max_contracts: Optional hard cap on the number of contracts.

    Returns:
        A genuine, non-negative `int` (never a `bool`, never a `float`) —
        the number of put contracts to buy. This function NEVER raises:
        it returns 0 for any invalid or unusable input, uniformly —
        non-positive, non-finite (NaN/infinity), a boolean passed where a
        number is expected, an `implied_vol`/`risk_free_rate` outside
        `MAX_ABS_IMPLIED_VOL`/`MAX_ABS_RISK_FREE_RATE` (extreme-but-finite
        values like `1e308` are economically meaningless AND can overflow
        the underlying BSM arithmetic — see those constants' docstring),
        a fractional `days_to_expiry`/`max_contracts` (both must be
        genuine whole numbers), a non-positive modeled payoff (e.g.
        `stress_move_fraction` outside `(0, 1)`), a zero/negative/
        non-finite `hedge_budget_dollars`, or an unexpected arithmetic
        failure (`ArithmeticError`/`OverflowError`/`ValueError`) from the
        BSM pricing itself for any OTHER extreme-but-finite input
        combination this function's own range checks didn't anticipate
        (e.g. an extreme `days_to_expiry` combined with a boundary
        `risk_free_rate`) — "no hedge needed/possible" is a valid
        everyday outcome (e.g. VaR is unavailable, or a caller passed
        corrupted data) rather than an error a caller must handle via a
        try/except. `calculate_bsm_put_price`/`calculate_bsm_d1_d2`
        themselves are NOT hardened this way — see their own docstrings;
        only this function's own "never raises" contract is guaranteed.
    """
    if (
        portfolio_var_dollars is None
        or spy_price is None
        or strike_price is None
        or not _is_finite_number(portfolio_var_dollars)
        or not _is_finite_number(spy_price)
        or not _is_finite_number(strike_price)
        or not _is_finite_number(implied_vol)
        or not _is_finite_number(risk_free_rate)
        or not _is_finite_number(stress_move_fraction)
        or portfolio_var_dollars <= 0
        or spy_price <= 0
        or strike_price <= 0
        or implied_vol <= 0
        or implied_vol > MAX_ABS_IMPLIED_VOL
        or abs(risk_free_rate) > MAX_ABS_RISK_FREE_RATE
        or not (0 < stress_move_fraction < 1)
    ):
        return 0

    validated_days_to_expiry = _coerce_whole_number(days_to_expiry, minimum=1)
    if validated_days_to_expiry is None:
        return 0
    days_to_expiry = validated_days_to_expiry

    if hedge_budget_dollars is not None and not _is_finite_number(hedge_budget_dollars):
        return 0

    if max_contracts is not None:
        # 0 is a legitimate explicit "no hedge allowed" cap, distinct
        # from a genuinely corrupted value (fractional, non-finite, bool).
        validated_max_contracts = _coerce_whole_number(max_contracts, minimum=0)
        if validated_max_contracts is None:
            return 0
        max_contracts = validated_max_contracts

    # Everything from here on touches BSM arithmetic (directly, or via a
    # division derived from a BSM-priced value) — wrapped in one try/except
    # as defense-in-depth on top of the range checks above. OverflowError
    # is already an ArithmeticError subclass; listed separately anyway so
    # the contract is self-documenting for a reader who doesn't recall
    # Python's exception hierarchy. Deliberately narrow: BaseException,
    # KeyboardInterrupt, and SystemExit are never caught here.
    try:
        time_to_expiry_years = days_to_expiry / 365.0
        current_put_price = calculate_bsm_put_price(
            spy_price, strike_price, time_to_expiry_years, risk_free_rate, implied_vol
        )
        if not math.isfinite(current_put_price):
            return 0

        stressed_spot = spy_price * (1 - stress_move_fraction)
        stressed_put_price = calculate_bsm_put_price(
            stressed_spot, strike_price, time_to_expiry_years, risk_free_rate, implied_vol
        )
        if not math.isfinite(stressed_put_price):
            return 0

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
    except (ArithmeticError, OverflowError, ValueError) as exc:
        logger.warning(
            "SPY hedge BSM pricing failed for an extreme/degenerate input combination; "
            "treating as no hedge sizeable: %s",
            exc,
        )
        return 0

    if max_contracts is not None:
        contracts = min(contracts, max_contracts)

    result = max(contracts, 0)
    # Final, explicit contract enforcement: always a genuine int, never a
    # bool or float, regardless of how `contracts` was produced above.
    return int(result)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    var_dollars = 5_000.0
    spy_price = 580.0
    contracts = calculate_spy_hedge(var_dollars, spy_price, strike_price=spy_price)
    print(f"Portfolio VaR: ${var_dollars:,.2f}")
    print(f"SPY price: ${spy_price:,.2f}")
    print(f"ATM SPY put contracts to buy: {contracts}")
