"""
Discounted Cash Flow (DCF) valuation model.

Given the raw financial data produced by
`src.data_ingestion.fetch_financials.fetch_company_financials`, this module:

1. Estimates the Weighted Average Cost of Capital (WACC).
2. Projects unlevered Free Cash Flow (FCF) for a configurable number of
   years, using a revenue growth rate and operating margin that default
   to the company's own historical Revenue CAGR and average Operating
   Margin (each independently overridable).
3. Calculates Terminal Value using the perpetuity growth (Gordon Growth)
   method.
4. Discounts the projected cash flows and terminal value back to present
   value to derive an intrinsic value per share.

Each step is a standalone, independently testable function; a single
`run_dcf_valuation` orchestrator wires them together.
"""

import logging
import math
from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# Fallback assumptions used only when data cannot be derived from the
# fetched financial statements.
DEFAULT_RISK_FREE_RATE = 0.04
DEFAULT_MARKET_RISK_PREMIUM = 0.055
DEFAULT_BETA = 1.0
DEFAULT_COST_OF_DEBT = 0.05
DEFAULT_TAX_RATE = 0.21
DEFAULT_DA_PCT_REVENUE = 0.03
DEFAULT_CAPEX_PCT_REVENUE = 0.04
DEFAULT_NWC_PCT_REVENUE_CHANGE = 0.01

# Conservative fallbacks used only when historical Revenue CAGR / average
# Operating Margin cannot be computed from the fetched statements.
DEFAULT_REVENUE_GROWTH_RATE_FALLBACK = 0.08
DEFAULT_OPERATING_MARGIN_FALLBACK = 0.15

# Ceiling applied to historically-derived revenue growth so a hyper-growth
# anomaly can't blow up the terminal value math (WACC - g must stay positive
# and comfortably so).
MAX_REVENUE_GROWTH_RATE = 0.25

# WACC is clamped to this range so a degenerate input (e.g. an extreme beta
# combined with a risk-free rate spike) can't produce a discount rate that's
# no longer economically sane.
MIN_DISCOUNT_RATE = 0.05
MAX_DISCOUNT_RATE = 0.20

# Economic bounds for EXPLICITLY supplied (not historically-derived)
# revenue growth / operating margin / terminal growth — the range a
# caller (e.g. the dashboard's assumption sliders, or a direct API
# query-string value) is allowed to override the model with. These
# mirror the dashboard's own slider ranges (frontend/src/app/page.tsx)
# so the API can't be made to accept a value the UI itself would never
# offer, and exist specifically to reject economically meaningless
# crafted inputs (e.g. a 5000% growth assumption compounded over 5
# years) rather than silently returning an enormous, meaningless
# valuation. They apply ONLY to explicit values — `None` (derive from
# the company's own historical financials) is validated separately, via
# MAX_REVENUE_GROWTH_RATE above, and negative historical growth is left
# unbounded below since a genuinely shrinking company is a legitimate
# real-world case this model must still be able to value.
MIN_EXPLICIT_REVENUE_GROWTH_RATE = -0.10  # -10%: matches the frontend slider's floor
MAX_EXPLICIT_REVENUE_GROWTH_RATE = 0.40  # 40%: matches the frontend slider's ceiling
MIN_EXPLICIT_OPERATING_MARGIN = 0.0  # matches the frontend slider's floor
MAX_EXPLICIT_OPERATING_MARGIN = 0.60  # matches the frontend slider's ceiling
MIN_EXPLICIT_TERMINAL_GROWTH_RATE = 0.0  # matches the frontend slider's floor
MAX_EXPLICIT_TERMINAL_GROWTH_RATE = 0.05  # matches the frontend slider's ceiling; also must stay well under WACC


# --------------------------------------------------------------------------
# Numeric boundary validation helpers
# --------------------------------------------------------------------------

def _coerce_positive_int(value) -> Optional[int]:
    """
    Return `value` as a genuine positive `int` if it is a whole number
    greater than zero — an `int`, or a `float` with no fractional part
    (e.g. `5.0`) and not NaN/infinity — and not a `bool` (a `bool` is
    technically an `int` subclass in Python; silently treating
    `True`/`False` as `1`/`0` here would be an easy, real mistake, not a
    defensive-programming nicety). Returns `None` for anything else: a
    fractional value (e.g. `2.5` — the exact reproduced defect for
    `project_free_cash_flows`'s `years` parameter, which otherwise leaks
    an internal `range()` `TypeError` instead of a clean `ValueError`),
    NaN, +/-infinity, a non-positive value, or a non-numeric type.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, float) and math.isfinite(value) and value.is_integer():
        whole = int(value)
        return whole if whole > 0 else None
    return None


def _is_valid_finite_number(value) -> bool:
    """
    Genuinely non-raising check: True only for a genuine finite
    `int`/`float` — not a `bool` (a `bool` is technically an `int`
    subclass in Python; silently treating `True`/`False` as `1`/`0` in a
    financial input is an easy, real mistake, not a defensive-
    programming nicety), not a non-numeric type (a string, `None`, etc.),
    and not NaN/+-infinity. Used by boundaries that already have a
    "return `None`/graceful degradation" contract for missing data
    (`calculate_enterprise_value`, `calculate_fcf_yield`) — a malformed-
    but-present value degrades the same way a missing one does, rather
    than raising or silently producing a misleading number.

    Deliberately does NOT call `math.isfinite(value)` on an `int` — a
    Python `int` is arbitrary-precision and always finite by definition
    (it has no NaN/infinity representation), but `math.isfinite` still
    converts its argument to a C `double` first, and that conversion
    itself raises `OverflowError` for an `int` too large to fit in a
    float (e.g. `10**10000`). Calling `math.isfinite` on an `int` this
    large would make this "non-raising" check itself raise. `float`
    values are always safe to pass to `math.isfinite` directly.
    """
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return True
    if isinstance(value, float):
        return math.isfinite(value)
    return False


def _require_finite_numeric(value, field_name: str) -> float:
    """
    Validate `value` is a genuine finite real number (an `int`/`float`,
    not a `bool`, not a non-numeric type like a string) suitable for
    financial arithmetic, raising a clean, documented `ValueError` —
    never a raw `TypeError`/`OverflowError` leaked from a bare comparison
    or from `math.isfinite` on a non-numeric type or an astronomically
    large `int` (see `_is_valid_finite_number`), and never silently
    accepting a `bool` because Python treats it as an `int` subclass
    (e.g. `True <= 0` is a valid, silently-wrong comparison).

    Returns:
        `value` unchanged, if valid.

    Raises:
        ValueError: If `value` is `None`, a `bool`, not an `int`/`float`,
            or not finite (NaN/+-infinity, or an `int` too large to be a
            meaningful financial quantity in the first place).
    """
    if not _is_valid_finite_number(value):
        raise ValueError(f"{field_name} must be a finite number, got {value!r}.")
    return value


def _require_finite_numeric_or_none(value, field_name: str) -> Optional[float]:
    """Same as `_require_finite_numeric`, but `None` passes through unchanged (for fields
    where `None` has its own separate meaning, e.g. "derive from historicals")."""
    if value is None:
        return None
    return _require_finite_numeric(value, field_name)


# --------------------------------------------------------------------------
# Statement parsing helpers
# --------------------------------------------------------------------------

def _most_recent_column(df: pd.DataFrame):
    """Return the column (fiscal period) representing the most recent data."""
    try:
        return sorted(df.columns, reverse=True)[0]
    except Exception:
        return df.columns[0]


def _get_row_value(
    df: Optional[pd.DataFrame],
    row_names: list,
    column=None,
) -> Optional[float]:
    """
    Safely extract a single value from a financial statement DataFrame.

    yfinance labels the same line item differently across versions/tickers
    (e.g. "Total Revenue" vs "TotalRevenue"), so this tries each candidate
    label in order and returns the first match in the requested column
    (most recent fiscal period by default).

    Args:
        df: A statement DataFrame (rows = line items, columns = periods),
            or None.
        row_names: Candidate row labels to try, in priority order.
        column: Specific column to read; defaults to the most recent one.

    Returns:
        The value as a float, or None if the statement, row, or value is
        unavailable.
    """
    if df is None or df.empty:
        return None

    col = column if column is not None else _most_recent_column(df)
    for name in row_names:
        if name in df.index:
            try:
                value = df.loc[name, col]
                if pd.isna(value):
                    continue
                return float(value)
            except (KeyError, TypeError, ValueError):
                continue
    return None


def calculate_historical_revenue_cagr(income_stmt: Optional[pd.DataFrame]) -> Optional[float]:
    """
    Calculate the Compound Annual Growth Rate (CAGR) of revenue across all
    historical periods available in an income statement.

        CAGR = (Revenue_latest / Revenue_earliest) ** (1 / years_elapsed) - 1

    Args:
        income_stmt: Income statement DataFrame (rows = line items,
            columns = fiscal periods), or None.

    Returns:
        CAGR as a decimal (uncapped), or None if fewer than two periods of
        positive revenue are available.
    """
    if income_stmt is None or income_stmt.empty:
        return None

    revenue_row_label = next(
        (name for name in ("Total Revenue", "TotalRevenue") if name in income_stmt.index), None
    )
    if revenue_row_label is None:
        return None

    revenue_by_period = income_stmt.loc[revenue_row_label].dropna()
    if len(revenue_by_period) < 2:
        return None

    try:
        sorted_periods = sorted(revenue_by_period.index, key=pd.Timestamp)
    except (TypeError, ValueError):
        return None

    earliest_period, latest_period = sorted_periods[0], sorted_periods[-1]
    earliest_revenue = float(revenue_by_period[earliest_period])
    latest_revenue = float(revenue_by_period[latest_period])

    years_elapsed = (pd.Timestamp(latest_period) - pd.Timestamp(earliest_period)).days / 365.25
    if earliest_revenue <= 0 or latest_revenue <= 0 or years_elapsed <= 0:
        return None

    return (latest_revenue / earliest_revenue) ** (1 / years_elapsed) - 1


def calculate_historical_average_operating_margin(income_stmt: Optional[pd.DataFrame]) -> Optional[float]:
    """
    Calculate the average Operating Margin (EBIT / Revenue) across all
    historical periods available in an income statement.

    Args:
        income_stmt: Income statement DataFrame (rows = line items,
            columns = fiscal periods), or None.

    Returns:
        Average operating margin as a decimal, or None if no period has
        both a usable EBIT (or Operating Income) and positive revenue.
    """
    if income_stmt is None or income_stmt.empty:
        return None

    margins = []
    for column in income_stmt.columns:
        revenue = _get_row_value(income_stmt, ["Total Revenue", "TotalRevenue"], column=column)
        ebit = _get_row_value(income_stmt, ["EBIT", "Ebit"], column=column)
        if ebit is None:
            ebit = _get_row_value(income_stmt, ["Operating Income", "OperatingIncome"], column=column)
        if revenue is None or revenue <= 0 or ebit is None:
            continue
        margins.append(ebit / revenue)

    if not margins:
        return None

    return sum(margins) / len(margins)


def extract_valuation_inputs(financial_data: dict) -> dict:
    """
    Pull the specific line items the DCF needs out of raw financial data.

    Args:
        financial_data: dict as returned by
            `src.data_ingestion.fetch_financials.fetch_company_financials`.

    Returns:
        dict with keys: revenue, revenue_growth_rate, operating_margin,
        total_debt, cash_and_equivalents, tax_rate, cost_of_debt,
        current_price, shares_outstanding, beta. Any value that could not
        be derived is None.

        `revenue_growth_rate` is the historical Revenue CAGR across all
        available periods, capped at MAX_REVENUE_GROWTH_RATE.
        `operating_margin` is the historical average Operating Margin
        (EBIT / Revenue) across all available periods.
    """
    income_stmt = financial_data.get("income_statement")
    balance_sheet = financial_data.get("balance_sheet")
    cash_flow = financial_data.get("cash_flow")

    revenue = _get_row_value(income_stmt, ["Total Revenue", "TotalRevenue"])
    pretax_income = _get_row_value(income_stmt, ["Pretax Income", "PretaxIncome"])
    tax_provision = _get_row_value(
        income_stmt, ["Tax Provision", "TaxProvision", "Income Tax Expense"]
    )
    interest_expense = _get_row_value(
        income_stmt, ["Interest Expense", "InterestExpense", "Interest Expense Non Operating"]
    )

    total_debt = _get_row_value(balance_sheet, ["Total Debt", "TotalDebt"])
    cash_and_equivalents = _get_row_value(
        balance_sheet,
        [
            "Cash And Cash Equivalents",
            "CashAndCashEquivalents",
            "Cash Cash Equivalents And Short Term Investments",
        ],
    )

    tax_rate = None
    if pretax_income and tax_provision is not None and pretax_income != 0:
        computed_rate = tax_provision / pretax_income
        if 0 <= computed_rate < 1:
            tax_rate = computed_rate
        else:
            logger.warning(
                "Computed tax rate %.2f is outside a plausible 0-100%% range; ignoring.",
                computed_rate,
            )

    cost_of_debt = None
    if interest_expense and total_debt:
        cost_of_debt = abs(interest_expense) / total_debt

    operating_cash_flow = _get_row_value(
        cash_flow,
        [
            "Operating Cash Flow",
            "Cash Flow From Continuing Operating Activities",
            "Total Cash From Operating Activities",
        ],
    )
    # yfinance reports CapEx as a negative outflow (e.g. -20_000_000).
    capital_expenditures = _get_row_value(
        cash_flow, ["Capital Expenditure", "CapitalExpenditure", "Purchase Of PPE"]
    )

    revenue_cagr = calculate_historical_revenue_cagr(income_stmt)
    revenue_growth_rate = (
        min(revenue_cagr, MAX_REVENUE_GROWTH_RATE) if revenue_cagr is not None else None
    )

    operating_margin = calculate_historical_average_operating_margin(income_stmt)

    return {
        "revenue": revenue,
        "revenue_growth_rate": revenue_growth_rate,
        "operating_margin": operating_margin,
        "total_debt": total_debt,
        "cash_and_equivalents": cash_and_equivalents,
        "tax_rate": tax_rate,
        "cost_of_debt": cost_of_debt,
        "operating_cash_flow": operating_cash_flow,
        "capital_expenditures": capital_expenditures,
        "current_price": financial_data.get("current_price"),
        "shares_outstanding": financial_data.get("shares_outstanding"),
        "beta": financial_data.get("beta"),
    }


def _validate_capital_inputs(inputs: dict) -> None:
    """
    Validate the capital-structure inputs pulled from financial
    statements before they reach WACC / enterprise-value / intrinsic-
    value math, so a corrupt or nonsensical statement value (a negative
    share count, an infinite price from a bad data-provider response)
    fails loudly and immediately instead of silently producing a
    meaningless valuation several steps later.

    Missing (`None`) values are left alone here — that's a normal,
    already-handled "unavailable" case elsewhere in the pipeline (e.g.
    `calculate_wacc` requires price/shares, defaults/floors others).
    This only rejects values that are *present* but malformed — a
    `bool` (Python treats it as an `int` subclass), a non-numeric type
    (e.g. a string — previously leaked a raw `TypeError` from a bare
    `math.isfinite` call), not finite (NaN/+-infinity, or an
    astronomically large `int` too big to convert to a float — see
    `_is_valid_finite_number`), or present and economically nonsensical
    (negative price/shares/debt/cash).
    """
    for field_name in ("current_price", "shares_outstanding", "total_debt", "cash_and_equivalents"):
        value = inputs.get(field_name)
        if value is None:
            continue
        value = _require_finite_numeric(value, field_name)
        if value < 0:
            # Do not interpolate an untrusted numeric value here. Python
            # 3.11+ deliberately refuses to stringify integers above its
            # digit-safety limit, which would replace this domain error with
            # an unrelated integer-conversion ValueError.
            raise ValueError(f"{field_name} must not be negative.")


# --------------------------------------------------------------------------
# 1. WACC
# --------------------------------------------------------------------------

def calculate_wacc(
    current_price: Optional[float],
    shares_outstanding: Optional[float],
    total_debt: Optional[float],
    beta: Optional[float] = None,
    risk_free_rate: float = DEFAULT_RISK_FREE_RATE,
    market_risk_premium: float = DEFAULT_MARKET_RISK_PREMIUM,
    cost_of_debt: Optional[float] = None,
    tax_rate: float = DEFAULT_TAX_RATE,
) -> float:
    """
    Estimate the Weighted Average Cost of Capital.

    Cost of equity is estimated via CAPM:
        Re = risk_free_rate + beta * market_risk_premium
    Cost of debt is applied on an after-tax basis:
        Rd_after_tax = cost_of_debt * (1 - tax_rate)
    Capital weights use market value of equity (price * shares outstanding)
    and book value of debt (total debt) as a proxy for market value of debt.

    Missing beta/cost_of_debt fall back to module-level defaults (logged as
    warnings); missing price/shares are fatal since market cap cannot be
    computed without them.

    Sign/range invariants enforced directly by this function (Track A
    Phase 1.5D — not merely by the orchestration path's
    `_validate_capital_inputs` pre-check, so this function is safe to call
    independently):
        - `current_price` and `shares_outstanding` must be strictly > 0
          (a zero or negative price/share count can't produce a
          meaningful market cap).
        - A present `total_debt` must be >= 0 (`None` still falls back to
          0 debt).
        - A present `cost_of_debt` must be >= 0 (`None` still falls back
          to the default).
        - A present `tax_rate` must be in [0, 1) — see below.
        - `beta` and `risk_free_rate`/`market_risk_premium` have no sign
          bound: a negative beta or a negative risk-free rate can be
          legitimate real-world inputs and are not rejected on sign
          alone, only on type/finiteness.

    Args:
        current_price: Current share price. Must be strictly positive.
        shares_outstanding: Number of shares outstanding. Must be
            strictly positive.
        total_debt: Total interest-bearing debt (book value). A present
            value must be non-negative.
        beta: Levered equity beta. Defaults to 1.0 if missing.
        risk_free_rate: Risk-free rate assumption (e.g. 10Y treasury
            yield). May be negative (e.g. certain sovereign yields).
        market_risk_premium: Equity market risk premium assumption.
        cost_of_debt: Pre-tax cost of debt. Defaults to 5% if missing. A
            present value must be non-negative.
        tax_rate: Effective/marginal tax rate used to tax-shield debt.
            A present value must be in [0, 1).

    Returns:
        WACC as a decimal (e.g. 0.081 for 8.1%), clamped to
        [MIN_DISCOUNT_RATE, MAX_DISCOUNT_RATE].

    Raises:
        ValueError: in any of the following cases — this function never
            silently produces a plausible-looking WACC from bad data:

            - `current_price`/`shares_outstanding` are missing, a `bool`,
              a non-numeric type, non-finite, or not strictly positive
              (these are REQUIRED — there is no missing-data fallback for
              them, and a zero/negative value is economically
              meaningless here).
            - `risk_free_rate`/`market_risk_premium` are a `bool`, a
              non-numeric type, or non-finite. Unlike `beta`/
              `cost_of_debt`/`tax_rate` below, these two have no
              established "fall back to a default" contract on THIS
              function (their module-level defaults apply only when the
              caller omits the argument entirely, via the parameter's own
              default value) — an explicitly passed `None` is therefore
              also rejected here, the same as any other invalid value.
              Negative values are NOT rejected — a negative risk-free
              rate or a negative beta can be economically legitimate.
            - `beta`, `cost_of_debt`, or `tax_rate` is PRESENT but
              malformed (a `bool`, a non-numeric type, or non-finite).
              **`None` for these three still uses the documented default
              fallback (logged as a warning) — that is a distinct,
              intentional "missing data" contract, not weakened by this
              validation.** Only a value that is actually *present but
              wrong* raises; a value that is genuinely absent (`None`)
              keeps behaving exactly as before. `total_debt` follows the
              same rule (`None` → the documented zero-debt fallback;
              present-but-malformed → raises) — e.g. `total_debt=True`
              previously slipped past a bare `total_debt or 0.0`
              truthiness check and was silently added to market cap as
              if it were `1`; it now raises instead.
            - A present `cost_of_debt` or `total_debt` is negative — a
              negative interest rate or a negative debt balance is not a
              type malformation, but it is still economically nonsensical
              and is rejected the same way (raises, not a silent clamp to
              zero).
            - A present `tax_rate` is outside [0, 1) — including a
              finite, well-typed, out-of-range value like `-0.2` or
              `1.5`. As of Track A Phase 1.5D this raises `ValueError`
              instead of silently substituting the 21% default; only a
              genuinely missing (`None`) tax rate still falls back.
            - Any intermediate quantity (`market_cap`, `total_capital`,
              `cost_of_equity`, `after_tax_cost_of_debt`, or the final
              `wacc` itself) is not a finite number — e.g. from an
              arithmetic overflow combining a technically-finite but
              astronomically large `int` (Python integers are
              arbitrary-precision and always finite by definition, but
              multiplying/adding one with a `float` can still overflow
              the `float` side of the operation) with an ordinary value.
              This function never returns/clamps a non-finite result into
              an apparently-valid WACC.
            - `equity + debt` (`total_capital`) is not positive.
    """
    current_price = _require_finite_numeric(current_price, "current_price")
    if current_price <= 0:
        raise ValueError(f"current_price must be a positive number, got {current_price!r}.")

    shares_outstanding = _require_finite_numeric(shares_outstanding, "shares_outstanding")
    if shares_outstanding <= 0:
        raise ValueError(f"shares_outstanding must be a positive number, got {shares_outstanding!r}.")

    risk_free_rate = _require_finite_numeric(risk_free_rate, "risk_free_rate")
    market_risk_premium = _require_finite_numeric(market_risk_premium, "market_risk_premium")

    if beta is None:
        logger.warning("Beta unavailable; defaulting to %.2f.", DEFAULT_BETA)
        beta = DEFAULT_BETA
    else:
        beta = _require_finite_numeric(beta, "beta")

    if cost_of_debt is None:
        logger.warning("Cost of debt unavailable; defaulting to %.2f%%.", DEFAULT_COST_OF_DEBT * 100)
        cost_of_debt = DEFAULT_COST_OF_DEBT
    else:
        cost_of_debt = _require_finite_numeric(cost_of_debt, "cost_of_debt")
        if cost_of_debt < 0:
            raise ValueError(f"cost_of_debt must not be negative, got {cost_of_debt!r}.")

    if tax_rate is None:
        logger.warning("Tax rate unavailable; defaulting to %.1f%%.", DEFAULT_TAX_RATE * 100)
        tax_rate = DEFAULT_TAX_RATE
    else:
        tax_rate = _require_finite_numeric(tax_rate, "tax_rate")
        if not (0 <= tax_rate < 1):
            # A present, well-typed, finite tax_rate outside [0, 1) (e.g.
            # -0.2 or 1.5) is economically invalid, not merely "missing" —
            # it must be rejected the same as any other bad present value,
            # not silently swapped for the 21% default as if it had never
            # been supplied.
            raise ValueError(f"tax_rate must be in [0, 1), got {tax_rate!r}.")

    if total_debt is None:
        total_debt = 0.0
    else:
        total_debt = _require_finite_numeric(total_debt, "total_debt")
        if total_debt < 0:
            raise ValueError(f"total_debt must not be negative, got {total_debt!r}.")

    # Every remaining step is real arithmetic on values already confirmed
    # individually finite — but a technically-finite, astronomically
    # large `int` (arbitrary-precision; always finite by definition) can
    # still raise `OverflowError` the moment it's combined with a
    # `float` (the implicit int-to-float conversion is what overflows,
    # not the mathematical result). Wrapped defensively so that case
    # raises the SAME clean `ValueError` this function already raises
    # for other invalid-input cases, never a raw internal
    # `OverflowError`. `float`-only overflow (which saturates to `inf`
    # silently, without raising) is caught by the explicit finite checks
    # after each step below.
    try:
        market_cap = current_price * shares_outstanding
        if not _is_valid_finite_number(market_cap):
            raise ValueError("Computed market capitalization is not a finite number.")

        total_capital = market_cap + total_debt
        if not _is_valid_finite_number(total_capital):
            raise ValueError("Computed total capital is not a finite number.")

        if total_capital <= 0:
            raise ValueError("Total capital (market cap + debt) must be positive.")

        weight_equity = market_cap / total_capital
        weight_debt = total_debt / total_capital

        cost_of_equity = risk_free_rate + beta * market_risk_premium
        if not _is_valid_finite_number(cost_of_equity):
            raise ValueError("Computed cost of equity is not a finite number.")

        after_tax_cost_of_debt = cost_of_debt * (1 - tax_rate)
        if not _is_valid_finite_number(after_tax_cost_of_debt):
            raise ValueError("Computed after-tax cost of debt is not a finite number.")

        wacc = weight_equity * cost_of_equity + weight_debt * after_tax_cost_of_debt
        if not _is_valid_finite_number(wacc):
            raise ValueError("Computed WACC is not a finite number.")
    except (ArithmeticError, OverflowError) as exc:
        raise ValueError(f"WACC computation overflowed during arithmetic: {exc}") from exc

    return max(MIN_DISCOUNT_RATE, min(MAX_DISCOUNT_RATE, wacc))


# --------------------------------------------------------------------------
# 2. FCF projection
# --------------------------------------------------------------------------

def project_free_cash_flows(
    base_revenue: float,
    revenue_growth_rate: float,
    operating_margin: float,
    tax_rate: float = DEFAULT_TAX_RATE,
    da_pct_revenue: float = DEFAULT_DA_PCT_REVENUE,
    capex_pct_revenue: float = DEFAULT_CAPEX_PCT_REVENUE,
    nwc_pct_revenue_change: float = DEFAULT_NWC_PCT_REVENUE_CHANGE,
    years: int = 5,
) -> pd.DataFrame:
    """
    Project unlevered Free Cash Flow (FCF) for a number of future years.

        Revenue_t   = Revenue_{t-1} * (1 + revenue_growth_rate)
        EBIT_t      = Revenue_t * operating_margin
        NOPAT_t     = EBIT_t * (1 - tax_rate)
        ChangeInNWC_t = nwc_pct_revenue_change * (Revenue_t - Revenue_{t-1})
        FCF_t       = NOPAT_t + D&A_t - CapEx_t - ChangeInNWC_t

    D&A and CapEx are modeled as constant percentages of projected
    revenue. The change in Net Working Capital is modeled as a
    percentage of the *change* in revenue (a growing business ties up
    incremental working capital roughly in proportion to how much its
    revenue actually grew that year), not a percentage of revenue
    itself — a flat, ever-present drag off gross revenue would double-
    count NWC as if the business rebuilt its entire working-capital base
    from zero every year.

    Args:
        base_revenue: Most recent actual (trailing) revenue, used as the
            starting point for projections and as Year 0's revenue for
            the first year's change-in-NWC calculation.
        revenue_growth_rate: Constant annual revenue growth rate (decimal).
        operating_margin: Constant EBIT margin applied to projected revenue.
        tax_rate: Tax rate applied to EBIT to derive NOPAT.
        da_pct_revenue: Depreciation & amortization as a % of revenue.
        capex_pct_revenue: Capital expenditures as a % of revenue.
        nwc_pct_revenue_change: Change in net working capital as a % of
            the year-over-year change in revenue.
        years: Number of years to project.

    Returns:
        DataFrame indexed by projection year (1..years) with columns:
        revenue, ebit, nopat, da, capex, change_in_nwc, fcf.

    Raises:
        ValueError: If base_revenue is missing, a bool, a non-numeric
            type, non-finite, or not positive; if years isn't a genuine
            positive whole number (rejects a fractional value like `2.5`,
            a bool, or a non-finite value — see `_coerce_positive_int`);
            or if any of revenue_growth_rate/operating_margin/tax_rate/
            da_pct_revenue/capex_pct_revenue/nwc_pct_revenue_change is a
            bool, a non-numeric type, or non-finite (NaN/infinity would
            otherwise silently propagate into an apparently-valid but
            meaningless projection — e.g. an infinite `base_revenue`
            previously produced a DataFrame full of `inf`/`NaN` instead
            of being rejected up front, and `base_revenue=True` — a
            `bool`, which Python treats as an `int` subclass — previously
            passed every check silently and was projected as if it were
            `1`).
    """
    base_revenue = _require_finite_numeric(base_revenue, "base_revenue")
    if base_revenue <= 0:
        raise ValueError(f"base_revenue must be a finite, positive number, got {base_revenue!r}.")

    validated_years = _coerce_positive_int(years)
    if validated_years is None:
        raise ValueError(f"years must be a positive integer, got {years!r}.")
    years = validated_years

    revenue_growth_rate = _require_finite_numeric(revenue_growth_rate, "revenue_growth_rate")
    operating_margin = _require_finite_numeric(operating_margin, "operating_margin")
    tax_rate = _require_finite_numeric(tax_rate, "tax_rate")
    da_pct_revenue = _require_finite_numeric(da_pct_revenue, "da_pct_revenue")
    capex_pct_revenue = _require_finite_numeric(capex_pct_revenue, "capex_pct_revenue")
    nwc_pct_revenue_change = _require_finite_numeric(nwc_pct_revenue_change, "nwc_pct_revenue_change")

    rows = []
    prior_revenue = base_revenue
    revenue = base_revenue
    try:
        for year in range(1, years + 1):
            revenue = revenue * (1 + revenue_growth_rate)
            ebit = revenue * operating_margin
            nopat = ebit * (1 - tax_rate)
            da = revenue * da_pct_revenue
            capex = revenue * capex_pct_revenue
            change_in_nwc = nwc_pct_revenue_change * (revenue - prior_revenue)
            fcf = nopat + da - capex - change_in_nwc
            prior_revenue = revenue

            row = {
                "year": year,
                "revenue": revenue,
                "ebit": ebit,
                "nopat": nopat,
                "da": da,
                "capex": capex,
                "change_in_nwc": change_in_nwc,
                "fcf": fcf,
            }
            for column_name, value in row.items():
                if column_name != "year" and not _is_valid_finite_number(value):
                    raise ValueError(
                        f"Projected {column_name} for year {year} is not a finite number ({value!r})."
                    )
            rows.append(row)
    except (ArithmeticError, OverflowError) as exc:
        raise ValueError(f"Free cash flow projection overflowed: {exc}") from exc

    return pd.DataFrame(rows).set_index("year")


# --------------------------------------------------------------------------
# 3. Terminal value
# --------------------------------------------------------------------------

def calculate_terminal_value(
    final_year_fcf: float,
    wacc: float,
    terminal_growth_rate: float,
) -> float:
    """
    Calculate Terminal Value using the perpetuity growth (Gordon Growth)
    method:

        TV = FCF_n * (1 + terminal_growth_rate) / (WACC - terminal_growth_rate)

    Args:
        final_year_fcf: FCF in the last explicit projection year.
        wacc: Weighted Average Cost of Capital (decimal).
        terminal_growth_rate: Assumed perpetual growth rate beyond the
            explicit projection period (decimal).

    Returns:
        Terminal value as of the end of the final projection year.

    Raises:
        ValueError: If any input is missing, a `bool`, a non-numeric
            type, or non-finite (never a raw `TypeError` leaked from the
            `wacc <= terminal_growth_rate` comparison on a non-numeric
            type, or a `bool` silently compared as `0`/`1`); if wacc is
            not strictly greater than terminal_growth_rate (the
            perpetuity formula diverges otherwise); or if the computed
            terminal value is not a finite number (e.g. `wacc` and
            `terminal_growth_rate` separated by only a minuscule margin
            can still make the denominator small enough to blow the
            result up toward infinity even though `wacc >
            terminal_growth_rate` technically holds) — never returned as
            if it were a legitimate valuation.
    """
    final_year_fcf = _require_finite_numeric(final_year_fcf, "final_year_fcf")
    wacc = _require_finite_numeric(wacc, "wacc")
    terminal_growth_rate = _require_finite_numeric(terminal_growth_rate, "terminal_growth_rate")

    if wacc <= terminal_growth_rate:
        raise ValueError(
            "WACC must be greater than the terminal growth rate for the "
            "perpetuity growth model to converge."
        )

    try:
        terminal_value = final_year_fcf * (1 + terminal_growth_rate) / (wacc - terminal_growth_rate)
        if not _is_valid_finite_number(terminal_value):
            raise ValueError("Computed terminal value is not a finite number.")
    except (ArithmeticError, OverflowError) as exc:
        raise ValueError(f"Terminal value computation overflowed: {exc}") from exc

    return terminal_value


# --------------------------------------------------------------------------
# 4. Discounting to present value
# --------------------------------------------------------------------------

def discount_to_present_value(
    fcf_projection: pd.DataFrame,
    terminal_value: float,
    wacc: float,
) -> dict:
    """
    Discount projected FCFs and the terminal value back to present value.

    Args:
        fcf_projection: DataFrame from `project_free_cash_flows`, indexed
            by projection year with an "fcf" column.
        terminal_value: Terminal value as of the end of the final
            projection year (from `calculate_terminal_value`).
        wacc: Weighted Average Cost of Capital (decimal).

    Returns:
        dict with:
            pv_fcf (pd.Series): present value of each projected year's FCF.
            pv_terminal_value (float): present value of the terminal value.
            enterprise_value (float): sum of pv_fcf and pv_terminal_value.

    Raises:
        ValueError: in any of the following cases:
            - `terminal_value` or `wacc` is missing, a `bool`, a
              non-numeric type, or non-finite — never a raw `TypeError`
              leaked from `(1 + wacc) ** years` on a non-numeric `wacc`,
              and never a bool silently used as `0`/`1` (e.g. `wacc=True`
              would otherwise discount every cash flow as if WACC were
              100%).
            - `fcf_projection` isn't a usable, non-empty DataFrame; is
              missing the required `"fcf"` column; its index contains a
              value that isn't a genuine positive finite projection year;
              or its `"fcf"` column contains a value that isn't a genuine
              finite number (a bool, a non-numeric type, NaN, or
              infinity) — this function does not redesign the FCF
              projection data structure, it only confirms the specific
              pieces it actually reads from `fcf_projection` are usable
              before doing arithmetic with them.
            - Any computed present value (per-year `pv_fcf`,
              `pv_terminal_value`, or the final `enterprise_value`) is not
              a finite number — never returned as if it were a legitimate
              valuation.
    """
    terminal_value = _require_finite_numeric(terminal_value, "terminal_value")
    wacc = _require_finite_numeric(wacc, "wacc")

    if not isinstance(fcf_projection, pd.DataFrame) or fcf_projection.empty:
        raise ValueError("fcf_projection must be a non-empty pandas DataFrame.")
    if "fcf" not in fcf_projection.columns:
        raise ValueError("fcf_projection must contain an 'fcf' column.")

    for year in fcf_projection.index:
        if not _is_valid_finite_number(year) or year <= 0:
            raise ValueError(f"fcf_projection index must contain positive finite year values, got {year!r}.")
    for fcf_value in fcf_projection["fcf"]:
        if not _is_valid_finite_number(fcf_value):
            raise ValueError(f"fcf_projection 'fcf' column must contain finite numbers, got {fcf_value!r}.")

    try:
        # A deliberately extreme (but individually finite) combination of
        # `terminal_value`/`wacc` can overflow this vectorized numpy/pandas
        # arithmetic. By default numpy reports that as a `RuntimeWarning`
        # (e.g. "overflow encountered in scalar divide") rather than an
        # exception, which would otherwise let an unhandled warning escape
        # this function even though the `_is_valid_finite_number` checks
        # below correctly catch the resulting `inf`/`nan` and turn it into
        # this function's own clean `ValueError`. Scoping `np.errstate` to
        # just this block turns overflow/invalid/divide-by-zero floating-
        # point conditions into a raised `FloatingPointError` (a subclass
        # of `ArithmeticError`, already handled below) instead of a
        # warning — a warning is not a substitute for the exception this
        # function is documented to raise. This does not touch numpy's
        # global error-reporting state outside this `with` block.
        with np.errstate(over="raise", invalid="raise", divide="raise"):
            years = fcf_projection.index.to_series()
            discount_factors = 1 / (1 + wacc) ** years
            pv_fcf = fcf_projection["fcf"] * discount_factors
            if not all(_is_valid_finite_number(v) for v in pv_fcf):
                raise ValueError("Computed present value of a projected year's FCF is not a finite number.")

            final_year = fcf_projection.index.max()
            pv_terminal_value = terminal_value / (1 + wacc) ** final_year
            if not _is_valid_finite_number(pv_terminal_value):
                raise ValueError("Computed present value of the terminal value is not a finite number.")

            enterprise_value = pv_fcf.sum() + pv_terminal_value
            if not _is_valid_finite_number(enterprise_value):
                raise ValueError("Computed enterprise value is not a finite number.")
    except (ArithmeticError, OverflowError) as exc:
        raise ValueError(f"Discounting computation overflowed: {exc}") from exc

    return {
        "pv_fcf": pv_fcf,
        "pv_terminal_value": pv_terminal_value,
        "enterprise_value": enterprise_value,
    }


def calculate_intrinsic_value_per_share(
    enterprise_value: float,
    total_debt: Optional[float],
    cash_and_equivalents: Optional[float],
    shares_outstanding: Optional[float],
) -> float:
    """
    Bridge Enterprise Value to Equity Value and divide by share count.

        Equity Value = Enterprise Value - Total Debt + Cash & Equivalents
        Intrinsic Value per Share = Equity Value / Shares Outstanding

    Args:
        enterprise_value: Present value of projected FCFs + terminal value.
            May be negative (a legitimate cash-rich-company case); only
            its type/finiteness is checked, not its sign.
        total_debt: Total interest-bearing debt. Treated as 0 if missing;
            a present value must be non-negative.
        cash_and_equivalents: Cash and cash equivalents. Treated as 0 if
            missing; a present value must be non-negative.
        shares_outstanding: Number of shares outstanding. Must be
            strictly positive.

    Returns:
        Intrinsic value per share.

    Raises:
        ValueError: If shares_outstanding is missing, a `bool`, a
            non-numeric type, non-finite, or not positive; or if
            enterprise_value is a `bool`, a non-numeric type, or
            non-finite. `total_debt`/`cash_and_equivalents` keep their
            established "treat as 0 if MISSING (`None`)" contract — but a
            value that is PRESENT and malformed (a `bool`, a non-numeric
            type, or non-finite) now raises instead of silently falling
            back to 0, since that is a data-integrity problem, not an
            absence (e.g. `total_debt=True` previously slipped past a
            bare `total_debt or 0.0` truthiness check and was silently
            added to equity value as if it were `1`). A present
            `total_debt`/`cash_and_equivalents` that is negative is
            likewise rejected (a negative debt balance or negative cash
            position is not a type malformation, but it is still
            economically nonsensical). Also raises if the computed
            equity value or the final per-share result is not a finite
            number (e.g. from an arithmetic overflow), never returning a
            NaN/infinity valuation as if it were legitimate.
    """
    enterprise_value = _require_finite_numeric(enterprise_value, "enterprise_value")

    shares_outstanding = _require_finite_numeric(shares_outstanding, "shares_outstanding")
    if shares_outstanding <= 0:
        raise ValueError(f"shares_outstanding must be a positive number, got {shares_outstanding!r}.")

    if total_debt is None:
        total_debt = 0.0
    else:
        total_debt = _require_finite_numeric(total_debt, "total_debt")
        if total_debt < 0:
            raise ValueError(f"total_debt must not be negative, got {total_debt!r}.")

    if cash_and_equivalents is None:
        cash_and_equivalents = 0.0
    else:
        cash_and_equivalents = _require_finite_numeric(cash_and_equivalents, "cash_and_equivalents")
        if cash_and_equivalents < 0:
            raise ValueError(f"cash_and_equivalents must not be negative, got {cash_and_equivalents!r}.")

    try:
        equity_value = enterprise_value - total_debt + cash_and_equivalents
        if not _is_valid_finite_number(equity_value):
            raise ValueError("Computed equity value is not a finite number.")
        result = equity_value / shares_outstanding
        if not _is_valid_finite_number(result):
            raise ValueError("Computed intrinsic value per share is not a finite number.")
        result = float(result)
    except (ArithmeticError, OverflowError) as exc:
        raise ValueError(f"Intrinsic value per share computation overflowed: {exc}") from exc

    return result


# --------------------------------------------------------------------------
# Market-based Enterprise Value & FCF Yield
# --------------------------------------------------------------------------
# Distinct from the DCF-derived "enterprise_value" above (present value of
# projected FCF + terminal value) — this is the market-observed Enterprise
# Value, used only to compute FCF Yield as a valuation cross-check.

def calculate_enterprise_value(
    current_price: Optional[float],
    shares_outstanding: Optional[float],
    total_debt: Optional[float],
    cash_and_equivalents: Optional[float],
) -> Optional[float]:
    """
    Market-based Enterprise Value: EV = Market Cap + Total Debt - Cash & Equivalents.

    Returns:
        EV as a float, or `None` if:
          - price/shares are missing, a `bool` (Python treats it as an
            `int` subclass; a malformed `current_price=True` degrades the
            same way a missing one does, rather than being silently
            priced as `$1`), a non-numeric type, non-finite, or not
            strictly positive (market cap can't be meaningfully computed
            without a genuine positive price/share count — a zero or
            negative price/share count degrades to `None` the same as a
            missing one);
          - `total_debt`/`cash_and_equivalents` is MISSING (`None`) — in
            that case it is treated as `0`, per this function's
            established contract; but if either is PRESENT and malformed
            (a `bool`, a non-numeric type, non-finite) or PRESENT and
            negative, this function returns `None` too, rather than
            silently treating the bad value as `0` — a data-integrity or
            economically-nonsensical value is not the same as an
            absence, even though both currently degrade to the same
            `None` return here (this function has no raising contract to
            distinguish them with, unlike `calculate_intrinsic_value_per_share`);
          - any intermediate arithmetic result (market cap, or the final
            EV) is not a finite number.

        A NEGATIVE final EV is explicitly allowed and returned as-is — a
        cash-rich company whose cash and short-term investments exceed
        market cap plus debt can legitimately have a negative market
        Enterprise Value; only malformed/nonfinite arithmetic is
        rejected, never a merely-negative-but-otherwise-valid result.

        This function never raises — it degrades to `None` uniformly,
        never leaking a raw `TypeError`/`OverflowError` from a malformed
        or arithmetically-overflowing intermediate result.
    """
    if not _is_valid_finite_number(current_price) or current_price <= 0:
        return None
    if not _is_valid_finite_number(shares_outstanding) or shares_outstanding <= 0:
        return None

    if total_debt is None:
        total_debt = 0.0
    elif not _is_valid_finite_number(total_debt) or total_debt < 0:
        return None

    if cash_and_equivalents is None:
        cash_and_equivalents = 0.0
    elif not _is_valid_finite_number(cash_and_equivalents) or cash_and_equivalents < 0:
        return None

    try:
        market_cap = current_price * shares_outstanding
        if not _is_valid_finite_number(market_cap):
            return None
        enterprise_value = market_cap + total_debt - cash_and_equivalents
        if not _is_valid_finite_number(enterprise_value):
            return None
        result = float(enterprise_value)
    except (ArithmeticError, OverflowError):
        return None

    return result


def calculate_fcf_yield(
    operating_cash_flow: Optional[float],
    capital_expenditures: Optional[float],
    enterprise_value: Optional[float],
) -> Optional[float]:
    """
    FCF Yield = (Operating Cash Flow - CapEx) / Enterprise Value.

    `capital_expenditures` is expected on yfinance's convention (already
    a negative outflow), so it's added rather than subtracted here —
    `operating_cash_flow + capital_expenditures` is equivalent to
    `OCF - abs(CapEx)`.

    Returns:
        FCF Yield as a decimal (e.g. 0.05 == 5%), or `None` if any input
        is unavailable, a `bool` (Python treats it as an `int` subclass —
        a malformed `operating_cash_flow=True` degrades the same way a
        missing one does, rather than being silently treated as `$1`), a
        non-numeric type, non-finite, `enterprise_value` is not positive,
        or the computed sum/quotient is not a finite number (e.g. from an
        arithmetic overflow). This function never raises.
    """
    if not _is_valid_finite_number(operating_cash_flow) or not _is_valid_finite_number(capital_expenditures):
        return None
    if not _is_valid_finite_number(enterprise_value) or enterprise_value <= 0:
        return None

    try:
        free_cash_flow = operating_cash_flow + capital_expenditures
        if not _is_valid_finite_number(free_cash_flow):
            return None
        result = free_cash_flow / enterprise_value
        if not _is_valid_finite_number(result):
            return None
        result = float(result)
    except (ArithmeticError, OverflowError):
        return None

    return result


# --------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------

@dataclass
class DCFAssumptions:
    """
    Configurable assumptions driving the DCF valuation.

    `revenue_growth_rate` and `operating_margin` default to None, which
    tells the engine to derive them from the company's own historical
    financials (Revenue CAGR — capped at MAX_REVENUE_GROWTH_RATE — and
    average Operating Margin, respectively) instead of applying a
    one-size-fits-all figure. Pass an explicit value (e.g. from a user-
    facing slider) to override the historical calculation for that run.
    If historical data can't be derived either, the engine falls back to
    DEFAULT_REVENUE_GROWTH_RATE_FALLBACK / DEFAULT_OPERATING_MARGIN_FALLBACK.
    """

    revenue_growth_rate: Optional[float] = None
    operating_margin: Optional[float] = None
    terminal_growth_rate: float = 0.025
    projection_years: int = 5
    tax_rate: Optional[float] = None  # None => derive from financials, else DEFAULT_TAX_RATE
    risk_free_rate: float = DEFAULT_RISK_FREE_RATE
    market_risk_premium: float = DEFAULT_MARKET_RISK_PREMIUM
    da_pct_revenue: float = DEFAULT_DA_PCT_REVENUE
    capex_pct_revenue: float = DEFAULT_CAPEX_PCT_REVENUE
    nwc_pct_revenue_change: float = DEFAULT_NWC_PCT_REVENUE_CHANGE

    def __post_init__(self) -> None:
        """
        Validate every publicly-settable assumption eagerly, at
        construction time, rather than letting a bad value silently
        propagate into WACC/FCF math and fail confusingly (or not at
        all) many steps later.

        Beyond finiteness, `revenue_growth_rate`/`operating_margin`
        (when explicitly set — `None` means "derive from historicals"
        and is bounded separately by MAX_REVENUE_GROWTH_RATE) and
        `terminal_growth_rate` (always explicit) are checked against
        MIN/MAX_EXPLICIT_* — an economically meaningless input (e.g.
        revenue "growing" 5000% a year) must be rejected here, not
        silently projected into an enormous, meaningless valuation.

        Every numeric field is additionally validated to be a genuine
        finite number, never a `bool` — Python treats `bool` as an `int`
        subclass, so `risk_free_rate=True` would otherwise silently pass
        every finiteness/comparison check as if it were `1.0` (a
        100% risk-free rate), and `tax_rate=False` would otherwise
        silently pass the `[0, 1)` range check as if it were an
        intentional `0%` tax rate — and never a raw `TypeError` leaked
        from a bare `<=`/`math.isfinite` on a non-numeric type (e.g. a
        string). See `_require_finite_numeric`/`_require_finite_numeric_or_none`.
        """
        validated_projection_years = _coerce_positive_int(self.projection_years)
        if validated_projection_years is None:
            raise ValueError(
                f"projection_years must be a positive integer, got {self.projection_years!r}."
            )
        self.projection_years = validated_projection_years

        # Optional fields: `None` has its own meaning ("derive from
        # historicals" for revenue_growth_rate/operating_margin; "derive
        # from financials, else DEFAULT_TAX_RATE" for tax_rate) and must
        # pass through untouched — anything else must be a genuine
        # finite, non-bool number.
        self.revenue_growth_rate = _require_finite_numeric_or_none(
            self.revenue_growth_rate, "revenue_growth_rate"
        )
        self.operating_margin = _require_finite_numeric_or_none(self.operating_margin, "operating_margin")
        self.tax_rate = _require_finite_numeric_or_none(self.tax_rate, "tax_rate")

        # Required (never legitimately `None`) fields — even though the
        # dataclass itself doesn't enforce that at runtime, a caller
        # passing `None` explicitly here must get the same clean
        # ValueError as any other invalid value, not a TypeError from a
        # later unguarded comparison.
        for field_name in (
            "terminal_growth_rate",
            "risk_free_rate",
            "market_risk_premium",
            "da_pct_revenue",
            "capex_pct_revenue",
            "nwc_pct_revenue_change",
        ):
            setattr(self, field_name, _require_finite_numeric(getattr(self, field_name), field_name))

        if self.tax_rate is not None and not (0 <= self.tax_rate < 1):
            raise ValueError(f"tax_rate must be in [0, 1) if set, got {self.tax_rate}.")

        if self.revenue_growth_rate is not None and not (
            MIN_EXPLICIT_REVENUE_GROWTH_RATE <= self.revenue_growth_rate <= MAX_EXPLICIT_REVENUE_GROWTH_RATE
        ):
            raise ValueError(
                f"revenue_growth_rate must be in [{MIN_EXPLICIT_REVENUE_GROWTH_RATE}, "
                f"{MAX_EXPLICIT_REVENUE_GROWTH_RATE}] if explicitly set, got {self.revenue_growth_rate}. "
                "Leave it unset (None) to derive it from the company's own historical financials instead."
            )
        if self.operating_margin is not None and not (
            MIN_EXPLICIT_OPERATING_MARGIN <= self.operating_margin <= MAX_EXPLICIT_OPERATING_MARGIN
        ):
            raise ValueError(
                f"operating_margin must be in [{MIN_EXPLICIT_OPERATING_MARGIN}, "
                f"{MAX_EXPLICIT_OPERATING_MARGIN}] if explicitly set, got {self.operating_margin}. "
                "Leave it unset (None) to derive it from the company's own historical financials instead."
            )
        if not (
            MIN_EXPLICIT_TERMINAL_GROWTH_RATE <= self.terminal_growth_rate <= MAX_EXPLICIT_TERMINAL_GROWTH_RATE
        ):
            raise ValueError(
                f"terminal_growth_rate must be in [{MIN_EXPLICIT_TERMINAL_GROWTH_RATE}, "
                f"{MAX_EXPLICIT_TERMINAL_GROWTH_RATE}], got {self.terminal_growth_rate}."
            )


def run_dcf_valuation(financial_data: dict, assumptions: DCFAssumptions = None) -> dict:
    """
    Run the full DCF valuation pipeline end-to-end.

    Args:
        financial_data: dict as returned by
            `src.data_ingestion.fetch_financials.fetch_company_financials`.
        assumptions: DCFAssumptions controlling growth, margin, and
            discounting inputs. Defaults to DCFAssumptions() if omitted.

    Returns:
        dict with: wacc, revenue_growth_rate, operating_margin,
        fcf_projection (DataFrame), terminal_value, pv_fcf,
        pv_terminal_value, enterprise_value, equity_value,
        intrinsic_value_per_share, current_market_price.

        `revenue_growth_rate` and `operating_margin` reflect whatever was
        actually used for the projection: the explicit value from
        `assumptions` if provided, otherwise the historically-derived
        figure, otherwise the conservative fallback (see DCFAssumptions).

    Raises:
        ValueError: If required inputs (e.g. base revenue, share count)
            cannot be determined from financial_data.
    """
    assumptions = assumptions or DCFAssumptions()
    inputs = extract_valuation_inputs(financial_data)
    _validate_capital_inputs(inputs)

    if inputs["revenue"] is None:
        raise ValueError(
            "Could not determine base revenue from the income statement; "
            "cannot run DCF."
        )

    # An explicit `assumptions.tax_rate` always wins — it's a deliberate
    # caller override and must not be silently replaced by the rate
    # derived from the financial statements just because one happened to
    # be computable. Only fall through to the derived/default rate when
    # the caller left `tax_rate` unset.
    if assumptions.tax_rate is not None:
        tax_rate = assumptions.tax_rate
    elif inputs["tax_rate"] is not None:
        tax_rate = inputs["tax_rate"]
    else:
        tax_rate = DEFAULT_TAX_RATE
        logger.warning("Effective tax rate unavailable; defaulting to %.1f%%.", tax_rate * 100)

    revenue_growth_rate = assumptions.revenue_growth_rate
    if revenue_growth_rate is None:
        revenue_growth_rate = inputs["revenue_growth_rate"]
        if revenue_growth_rate is None:
            revenue_growth_rate = DEFAULT_REVENUE_GROWTH_RATE_FALLBACK
            logger.warning(
                "Historical revenue CAGR unavailable; defaulting growth rate to %.1f%%.",
                DEFAULT_REVENUE_GROWTH_RATE_FALLBACK * 100,
            )
        else:
            logger.info(
                "Using historical revenue CAGR of %.1f%% (capped at %.0f%%) as the growth assumption.",
                revenue_growth_rate * 100,
                MAX_REVENUE_GROWTH_RATE * 100,
            )

    operating_margin = assumptions.operating_margin
    if operating_margin is None:
        operating_margin = inputs["operating_margin"]
        if operating_margin is None:
            operating_margin = DEFAULT_OPERATING_MARGIN_FALLBACK
            logger.warning(
                "Historical operating margin unavailable; defaulting margin to %.1f%%.",
                DEFAULT_OPERATING_MARGIN_FALLBACK * 100,
            )
        else:
            logger.info(
                "Using historical average operating margin of %.1f%% as the margin assumption.",
                operating_margin * 100,
            )

    wacc = calculate_wacc(
        current_price=inputs["current_price"],
        shares_outstanding=inputs["shares_outstanding"],
        total_debt=inputs["total_debt"],
        beta=inputs["beta"],
        risk_free_rate=assumptions.risk_free_rate,
        market_risk_premium=assumptions.market_risk_premium,
        cost_of_debt=inputs["cost_of_debt"],
        tax_rate=tax_rate,
    )

    fcf_projection = project_free_cash_flows(
        base_revenue=inputs["revenue"],
        revenue_growth_rate=revenue_growth_rate,
        operating_margin=operating_margin,
        tax_rate=tax_rate,
        da_pct_revenue=assumptions.da_pct_revenue,
        capex_pct_revenue=assumptions.capex_pct_revenue,
        nwc_pct_revenue_change=assumptions.nwc_pct_revenue_change,
        years=assumptions.projection_years,
    )

    terminal_value = calculate_terminal_value(
        final_year_fcf=fcf_projection["fcf"].iloc[-1],
        wacc=wacc,
        terminal_growth_rate=assumptions.terminal_growth_rate,
    )

    discounting = discount_to_present_value(fcf_projection, terminal_value, wacc)

    intrinsic_value_per_share = calculate_intrinsic_value_per_share(
        enterprise_value=discounting["enterprise_value"],
        total_debt=inputs["total_debt"],
        cash_and_equivalents=inputs["cash_and_equivalents"],
        shares_outstanding=inputs["shares_outstanding"],
    )

    equity_value = (
        discounting["enterprise_value"]
        - (inputs["total_debt"] or 0.0)
        + (inputs["cash_and_equivalents"] or 0.0)
    )

    market_enterprise_value = calculate_enterprise_value(
        current_price=inputs["current_price"],
        shares_outstanding=inputs["shares_outstanding"],
        total_debt=inputs["total_debt"],
        cash_and_equivalents=inputs["cash_and_equivalents"],
    )
    fcf_yield = calculate_fcf_yield(
        operating_cash_flow=inputs["operating_cash_flow"],
        capital_expenditures=inputs["capital_expenditures"],
        enterprise_value=market_enterprise_value,
    )

    return {
        "wacc": wacc,
        "revenue_growth_rate": revenue_growth_rate,
        "operating_margin": operating_margin,
        "fcf_projection": fcf_projection,
        "terminal_value": terminal_value,
        "pv_fcf": discounting["pv_fcf"],
        "pv_terminal_value": discounting["pv_terminal_value"],
        "enterprise_value": discounting["enterprise_value"],
        "equity_value": equity_value,
        "intrinsic_value_per_share": intrinsic_value_per_share,
        "current_market_price": inputs["current_price"],
        "market_enterprise_value": market_enterprise_value,
        "fcf_yield": fcf_yield,
    }


if __name__ == "__main__":
    import sys
    from pathlib import Path

    # Allow running this file directly (`python src/dcf_model/dcf.py`) as
    # well as as a module (`python -m src.dcf_model.dcf`).
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from src.data_ingestion.fetch_financials import fetch_company_financials

    financial_data = fetch_company_financials("AAPL")

    # Leaving revenue_growth_rate/operating_margin unset lets the engine
    # derive them from AAPL's own historical financials.
    result = run_dcf_valuation(
        financial_data,
        DCFAssumptions(
            terminal_growth_rate=0.025,
            projection_years=5,
        ),
    )

    print(
        f"Revenue Growth Rate (historical CAGR, capped at {MAX_REVENUE_GROWTH_RATE:.0%}): "
        f"{result['revenue_growth_rate']:.2%}"
    )
    print(f"Operating Margin (historical average): {result['operating_margin']:.2%}")
    print(f"WACC: {result['wacc']:.2%}")
    print("\nFCF Projection:")
    print(result["fcf_projection"])
    print(f"\nTerminal Value: ${result['terminal_value']:,.0f}")
    print(f"Enterprise Value: ${result['enterprise_value']:,.0f}")
    print(f"Equity Value: ${result['equity_value']:,.0f}")
    print(f"Intrinsic Value per Share: ${result['intrinsic_value_per_share']:.2f}")
    print(f"Current Market Price: ${result['current_market_price']:.2f}")
