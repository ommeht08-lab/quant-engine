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

import pandas as pd

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

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
        "current_price": financial_data.get("current_price"),
        "shares_outstanding": financial_data.get("shares_outstanding"),
        "beta": financial_data.get("beta"),
    }


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

    Args:
        current_price: Current share price.
        shares_outstanding: Number of shares outstanding.
        total_debt: Total interest-bearing debt (book value).
        beta: Levered equity beta. Defaults to 1.0 if missing.
        risk_free_rate: Risk-free rate assumption (e.g. 10Y treasury yield).
        market_risk_premium: Equity market risk premium assumption.
        cost_of_debt: Pre-tax cost of debt. Defaults to 5% if missing.
        tax_rate: Effective/marginal tax rate used to tax-shield debt.

    Returns:
        WACC as a decimal (e.g. 0.081 for 8.1%), clamped to
        [MIN_DISCOUNT_RATE, MAX_DISCOUNT_RATE].

    Raises:
        ValueError: If current_price/shares_outstanding are missing, or if
            equity + debt is not positive.
    """
    if current_price is None or shares_outstanding is None:
        raise ValueError(
            "current_price and shares_outstanding are required to compute "
            "market capitalization for WACC."
        )

    if beta is None or not math.isfinite(beta):
        logger.warning("Beta unavailable or non-finite; defaulting to %.2f.", DEFAULT_BETA)
        beta = DEFAULT_BETA

    if cost_of_debt is None:
        logger.warning(
            "Cost of debt unavailable; defaulting to %.2f%%.", DEFAULT_COST_OF_DEBT * 100
        )
        cost_of_debt = DEFAULT_COST_OF_DEBT

    if tax_rate is None or not (0 <= tax_rate < 1):
        logger.warning("Tax rate unavailable/invalid; defaulting to %.1f%%.", DEFAULT_TAX_RATE * 100)
        tax_rate = DEFAULT_TAX_RATE

    total_debt = total_debt or 0.0
    market_cap = current_price * shares_outstanding
    total_capital = market_cap + total_debt

    if total_capital <= 0:
        raise ValueError("Total capital (market cap + debt) must be positive.")

    weight_equity = market_cap / total_capital
    weight_debt = total_debt / total_capital

    cost_of_equity = risk_free_rate + beta * market_risk_premium
    after_tax_cost_of_debt = cost_of_debt * (1 - tax_rate)

    wacc = weight_equity * cost_of_equity + weight_debt * after_tax_cost_of_debt
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
        FCF_t       = NOPAT_t + D&A_t - CapEx_t - ChangeInNWC_t

    D&A, CapEx, and the change in Net Working Capital are each modeled as
    constant percentages of projected revenue, since the caller only
    configures growth rate and operating margin.

    Args:
        base_revenue: Most recent actual (trailing) revenue, used as the
            starting point for projections.
        revenue_growth_rate: Constant annual revenue growth rate (decimal).
        operating_margin: Constant EBIT margin applied to projected revenue.
        tax_rate: Tax rate applied to EBIT to derive NOPAT.
        da_pct_revenue: Depreciation & amortization as a % of revenue.
        capex_pct_revenue: Capital expenditures as a % of revenue.
        nwc_pct_revenue_change: Change in net working capital as a % of
            revenue.
        years: Number of years to project.

    Returns:
        DataFrame indexed by projection year (1..years) with columns:
        revenue, ebit, nopat, da, capex, change_in_nwc, fcf.

    Raises:
        ValueError: If base_revenue is not positive or years < 1.
    """
    if base_revenue is None or base_revenue <= 0:
        raise ValueError("base_revenue must be a positive number.")
    if years < 1:
        raise ValueError("years must be at least 1.")

    rows = []
    revenue = base_revenue
    for year in range(1, years + 1):
        revenue = revenue * (1 + revenue_growth_rate)
        ebit = revenue * operating_margin
        nopat = ebit * (1 - tax_rate)
        da = revenue * da_pct_revenue
        capex = revenue * capex_pct_revenue
        change_in_nwc = revenue * nwc_pct_revenue_change
        fcf = nopat + da - capex - change_in_nwc

        rows.append(
            {
                "year": year,
                "revenue": revenue,
                "ebit": ebit,
                "nopat": nopat,
                "da": da,
                "capex": capex,
                "change_in_nwc": change_in_nwc,
                "fcf": fcf,
            }
        )

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
        ValueError: If wacc is not strictly greater than terminal_growth_rate
            (the perpetuity formula diverges otherwise).
    """
    if wacc <= terminal_growth_rate:
        raise ValueError(
            "WACC must be greater than the terminal growth rate for the "
            "perpetuity growth model to converge."
        )
    return final_year_fcf * (1 + terminal_growth_rate) / (wacc - terminal_growth_rate)


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
    """
    years = fcf_projection.index.to_series()
    discount_factors = 1 / (1 + wacc) ** years
    pv_fcf = fcf_projection["fcf"] * discount_factors

    final_year = fcf_projection.index.max()
    pv_terminal_value = terminal_value / (1 + wacc) ** final_year

    enterprise_value = pv_fcf.sum() + pv_terminal_value

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
        total_debt: Total interest-bearing debt. Treated as 0 if missing.
        cash_and_equivalents: Cash and cash equivalents. Treated as 0 if
            missing.
        shares_outstanding: Number of shares outstanding.

    Returns:
        Intrinsic value per share.

    Raises:
        ValueError: If shares_outstanding is missing or not positive.
    """
    if not shares_outstanding or shares_outstanding <= 0:
        raise ValueError("shares_outstanding must be a positive number.")

    total_debt = total_debt or 0.0
    cash_and_equivalents = cash_and_equivalents or 0.0

    equity_value = enterprise_value - total_debt + cash_and_equivalents
    return equity_value / shares_outstanding


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

    if inputs["revenue"] is None:
        raise ValueError(
            "Could not determine base revenue from the income statement; "
            "cannot run DCF."
        )

    tax_rate = inputs["tax_rate"]
    if tax_rate is None:
        tax_rate = assumptions.tax_rate if assumptions.tax_rate is not None else DEFAULT_TAX_RATE
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
