"""
Where every baseline assumption came from.

The simulation anchors to one deterministic valuation (`run_dcf_valuation`).
This module reports how that valuation resolved each input: which statement
row, which fiscal periods, whether a documented default replaced a missing
figure, and which assumptions are fixed constants rather than data. It is
read-only and changes no number.

Provenance is derived from the resolution path, never inferred from the
resulting value:

* Row labels come from the deterministic model's own `_get_row_value` lookup,
  asked one candidate label at a time in the model's priority order, so the
  label reported is the label the model used.
* Fallback status comes from the model's own flags (`tax_rate_source`,
  `capex_pct_revenue_source`, `wacc_was_clamped`) or from the same
  `extract_valuation_inputs` call the model makes (`cost_of_debt is None`).
* Margin periods mirror the model's per-column loop; the block records whether
  the periods reproduce the margin the model actually used.

The block is covered by its own `provenance_digest` and sits outside the
numerical `result_digest`, so adding or refining provenance never changes a
simulation's digest.
"""

from __future__ import annotations

import math
from datetime import datetime
from typing import Dict, List, Mapping, Optional, Tuple

import pandas as pd

from src.dcf_model.dcf import (
    DEFAULT_BETA,
    DEFAULT_COST_OF_DEBT,
    DEFAULT_MARKET_RISK_PREMIUM,
    DEFAULT_TAX_RATE,
    MAX_REVENUE_GROWTH_RATE,
    _get_row_value,  # same-package helpers: reused so provenance cannot drift from the model
    _most_recent_column,
    calculate_historical_revenue_cagr,
    derive_historical_capex_pct_revenue,
    extract_valuation_inputs,
)
from src.dcf_model.monte_carlo_config import (
    PROVENANCE_SCHEMA,
    MonteCarloConfig,
    MonteCarloRefusal,
    canonical_json,
    sha256_hex,
)
from src.dcf_model.monte_carlo_history import HistoricalSpreads

# The deterministic model's candidate labels, in its priority order.
REVENUE_LABELS = ("Total Revenue", "TotalRevenue")
OPERATING_PROFIT_LABELS = (
    ("EBIT", ("EBIT", "Ebit")),
    ("Operating Income", ("Operating Income", "OperatingIncome")),
)
PRETAX_LABELS = ("Pretax Income", "PretaxIncome")
TAX_PROVISION_LABELS = ("Tax Provision", "TaxProvision", "Income Tax Expense")
INTEREST_EXPENSE_LABELS = ("Interest Expense", "InterestExpense", "Interest Expense Non Operating")
TOTAL_DEBT_LABELS = ("Total Debt", "TotalDebt")
CASH_LABELS = (
    "Cash And Cash Equivalents",
    "CashAndCashEquivalents",
    "Cash Cash Equivalents And Short Term Investments",
)
CASH_ONLY_LABELS = CASH_LABELS[:2]
SHORT_TERM_INVESTMENT_LABELS = ("Other Short Term Investments", "OtherShortTermInvestments", "Short Term Investments")

SPREAD_ILLUSTRATION_CONFIDENCE = 0.90


def _num(value) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _period(column) -> str:
    return pd.Timestamp(column).date().isoformat()


def _resolve(frame: Optional[pd.DataFrame], labels, column=None) -> Tuple[Optional[str], Optional[float]]:
    """The first candidate label the model would use, and its value."""
    for name in labels:
        value = _get_row_value(frame, [name], column=column)
        if value is not None:
            return name, value
    return None, None


def _latest_column(frame: Optional[pd.DataFrame]):
    if frame is None or frame.empty:
        return None
    return _most_recent_column(frame)


# --------------------------------------------------------------------------
# Sections
# --------------------------------------------------------------------------

def _margin_section(income: Optional[pd.DataFrame], history, used_margin: float) -> dict:
    """Mirror the model's per-column margin loop and record the row used for each period."""
    periods: List[dict] = []
    skipped: List[dict] = []
    if income is not None and not income.empty:
        for column in income.columns:
            revenue_label, revenue = _resolve(income, REVENUE_LABELS, column=column)
            row_used, profit = None, None
            for display, labels in OPERATING_PROFIT_LABELS:
                _, profit = _resolve(income, labels, column=column)
                if profit is not None:
                    row_used = display
                    break
            label = _period(column)
            if revenue is None or revenue <= 0:
                skipped.append({"period": label, "reason": "revenue_missing_or_nonpositive"})
            elif profit is None:
                skipped.append({"period": label, "reason": "operating_profit_row_missing"})
            else:
                periods.append(
                    {
                        "period": label,
                        "row_used": row_used,
                        "operating_profit": _num(profit),
                        "revenue": _num(revenue),
                        "margin": _num(profit / revenue),
                    }
                )
    margins = [p["margin"] for p in periods]
    mean = sum(margins) / len(margins) if margins else None
    rows_used = sorted({p["row_used"] for p in periods})
    spread_ok = isinstance(history, HistoricalSpreads)
    return {
        "definition": "Average of annual (operating-profit row / Total Revenue), one value per fiscal period.",
        "row_priority": ["EBIT", "Ebit", "Operating Income", "OperatingIncome"],
        "rows_used": rows_used,
        "mixed_rows": len(rows_used) > 1,
        "periods": periods,
        "periods_skipped": skipped,
        "center": _num(mean),
        "center_statistic": "arithmetic_mean",
        "spread": _num(history.margin_sd) if spread_ok else None,
        "spread_statistic": "sample_sd_ddof1" if spread_ok else None,
        "n_observations": len(periods),
        "model_baseline_margin": _num(used_margin),
        "periods_reproduce_model_margin": mean is not None and mean == used_margin,
    }


def _growth_section(income: Optional[pd.DataFrame], history, baseline_result: Mapping) -> dict:
    revenue_label = None
    if income is not None and not income.empty:
        revenue_label = next((name for name in REVENUE_LABELS if name in income.index), None)
    cagr = history.growth_center_cagr if isinstance(history, HistoricalSpreads) else calculate_historical_revenue_cagr(income)
    section = {
        "definition": "Compound annual growth of Total Revenue between the first and last annual period.",
        "revenue_row": revenue_label,
        "cagr_uncapped": _num(cagr),
        "ceiling_applied_by_baseline": MAX_REVENUE_GROWTH_RATE,
        "baseline_growth_rate": _num(baseline_result["revenue_growth_rate"]),
        "baseline_growth_was_capped": bool(cagr is not None and cagr > baseline_result["revenue_growth_rate"]),
    }
    if isinstance(history, HistoricalSpreads):
        section.update(
            {
                "periods": list(history.revenue_periods),
                "revenues": [_num(v) for v in history.revenues],
                "yoy_growths": [_num(v) for v in history.yoy_growths],
                "spread": _num(history.growth_sd),
                "spread_statistic": "sample_sd_ddof1",
                "n_observations": history.n_growth,
            }
        )
    else:
        section.update({"periods": None, "spread": None, "spread_statistic": None, "n_observations": 0})
    return section


def _tax_section(income: Optional[pd.DataFrame], baseline_result: Mapping) -> dict:
    column = _latest_column(income)
    pretax_label, pretax = _resolve(income, PRETAX_LABELS)
    provision_label, provision = _resolve(income, TAX_PROVISION_LABELS)
    model_label = baseline_result["tax_rate_source"]
    source = {"historical": "statement_derived", "fallback": "default_fallback", "custom": "caller_override"}[model_label]
    reason = None
    if model_label == "fallback":
        if not pretax:
            reason = "pretax_income_unavailable_or_zero"
        elif provision is None:
            reason = "tax_provision_unavailable"
        else:
            reason = "effective_rate_outside_0_to_1"
    return {
        "rate": _num(baseline_result["tax_rate"]),
        "source": source,
        "is_fallback": model_label == "fallback",
        "fallback_reason": reason,
        "default_rate": DEFAULT_TAX_RATE,
        "basis": "Tax Provision / Pretax Income of the most recent annual period (a single period, not an average).",
        "period": _period(column) if column is not None else None,
        "pretax_income_row": pretax_label,
        "pretax_income": _num(pretax),
        "tax_provision_row": provision_label,
        "tax_provision": _num(provision),
        "applied_to": "NOPAT in the forecast and the debt tax shield in WACC",
    }


def _cost_of_debt_section(income: Optional[pd.DataFrame], balance: Optional[pd.DataFrame], baseline_result: Mapping, derived) -> dict:
    column = _latest_column(income)
    interest_label, interest = _resolve(income, INTEREST_EXPENSE_LABELS)
    debt_label, debt = _resolve(balance, TOTAL_DEBT_LABELS)
    is_fallback = derived is None
    reason = None
    if is_fallback:
        reason = "interest_expense_unavailable_or_zero" if not interest else "total_debt_unavailable_or_zero"
    by_period: Dict[str, Optional[float]] = {}
    if income is not None and not income.empty:
        for col in sorted(income.columns, reverse=True):
            by_period[_period(col)] = _num(_resolve(income, INTEREST_EXPENSE_LABELS, column=col)[1])
    return {
        "rate": _num(baseline_result["cost_of_debt"]),
        "source": "default_fallback" if is_fallback else "statement_derived",
        "is_fallback": is_fallback,
        "fallback_reason": reason,
        "default_rate": DEFAULT_COST_OF_DEBT,
        "basis": "abs(Interest Expense) / Total Debt, both from the most recent annual period; earlier periods are not used.",
        "period": _period(column) if column is not None else None,
        "interest_expense_row": interest_label,
        "interest_expense": _num(interest),
        "total_debt_row": debt_label,
        "total_debt": _num(debt),
        "interest_expense_by_period": by_period,
        "affects": "WACC only (after-tax, weighted by book debt)",
    }


def _capex_section(income, cash_flow, baseline_result: Mapping, capex_pct: float) -> dict:
    context = derive_historical_capex_pct_revenue(cash_flow, income)
    return {
        "pct_of_revenue": _num(capex_pct),
        "source": baseline_result["capex_pct_revenue_source"],
        "fixed": True,
        "basis": "Flat model default applied to every forecast year and every draw; not derived from this company's statements.",
        "historical_ratio_for_context": {
            "used_in_valuation": False,
            "status": context.status,
            "ratio": _num(context.ratio),
            "row": context.capex_row,
            "periods": list(context.source_periods),
            "reason": context.reason,
        },
    }


def _fixed_assumption(value: float, basis: str) -> dict:
    return {"value": _num(value), "fixed": True, "source": "model_default", "basis": basis}


def _cash_section(balance: Optional[pd.DataFrame], baseline_result: Mapping, shares: Optional[float]) -> dict:
    column = _latest_column(balance)
    cash_label, cash = _resolve(balance, CASH_LABELS)
    sti_label, sti = _resolve(balance, SHORT_TERM_INVESTMENT_LABELS)
    includes_sti = cash_label is not None and cash_label not in CASH_ONLY_LABELS
    excluded = sti if (cash_label in CASH_ONLY_LABELS) else None
    return {
        "value": _num(baseline_result["cash_and_equivalents"]),
        "row_used": cash_label,
        "period": _period(column) if column is not None else None,
        "includes_short_term_investments": bool(includes_sti),
        "definition": (
            "Cash and equivalents plus short-term investments." if includes_sti
            else "Cash and cash equivalents only; short-term investments are not added to the equity bridge."
        ),
        "excluded_short_term_investments": _num(excluded),
        "excluded_short_term_investments_row": sti_label if excluded is not None else None,
        "excluded_per_share": _num(excluded / shares) if (excluded is not None and shares) else None,
    }


def _wacc_section(baseline_result: Mapping, specs, config: MonteCarloConfig) -> dict:
    interval = specs.wacc
    sampled = None
    if not isinstance(interval, MonteCarloRefusal):
        constraints = list(interval.binding_constraints)
        sampled = {
            "lower": interval.lower,
            "upper": interval.upper,
            "requested_half_width": interval.half_width,
            "restricted_by_bounds": bool(constraints),
            "binding_constraints": constraints,
        }
    return {
        "baseline_applied": _num(baseline_result["wacc"]),
        "baseline_pre_clamp": _num(baseline_result["wacc_pre_clamp"]),
        "baseline_was_clamped": bool(baseline_result["wacc_was_clamped"]),
        "model_bounds": [config.wacc_lower_bound, config.wacc_upper_bound],
        "sampled_interval": sampled,
        "sampled_interval_refusal_code": interval.code if isinstance(interval, MonteCarloRefusal) else None,
        "note": "Baseline clamping is the deterministic model's 5%-20% bound on the CAPM rate; "
        "interval restriction is separate and only narrows the sampled range around the applied baseline.",
    }


def _statements_timing(financial_data: Mapping) -> dict:
    out = {}
    for key in ("income_statement", "balance_sheet", "cash_flow"):
        frame = financial_data.get(key)
        if frame is None or getattr(frame, "empty", True):
            out[key] = {"periods": [], "latest_period": None}
            continue
        periods = sorted((_period(c) for c in frame.columns), reverse=True)
        out[key] = {"periods": periods, "latest_period": periods[0]}
    return out


def _age_days(captured_at: Optional[str], period_end: Optional[str]) -> Optional[int]:
    if not captured_at or not period_end:
        return None
    try:
        captured = datetime.fromisoformat(captured_at)
        return (captured.date() - datetime.fromisoformat(period_end).date()).days
    except ValueError:
        return None


def _timing_section(financial_data: Mapping, context: Mapping) -> dict:
    captured_at = context.get("snapshot_captured_at")
    observed = context.get("price_observed_at")
    capture_time_stand_in = bool(context.get("price_observed_at_is_capture_time"))
    if observed and not capture_time_stand_in:
        price = {"observed_at": observed, "status": "provider_quote_time"}
    else:
        # A capture time is never reported as a quote time.
        price = {"observed_at": None, "status": "not_provided_by_provider" if capture_time_stand_in else "not_recorded"}
    price["note"] = (
        "The provider supplied no quote timestamp to the snapshot, so the price time is unknown. "
        "The snapshot capture time bounds it from above only."
    )
    statements = _statements_timing(financial_data)
    latest = context.get("statement_period_end") or statements["income_statement"]["latest_period"]
    return {
        "snapshot_captured_at": captured_at,
        "latest_statement_period_end": latest,
        "latest_statement_age_days_at_capture": _age_days(captured_at, latest),
        "statements": statements,
        "market_price": price,
        "revenue_basis": "Latest annual fiscal year, not trailing twelve months.",
    }


def _spread_illustration(n_observations: int) -> Optional[dict]:
    """90% interval multipliers for a normal sample standard deviation (illustration only)."""
    degrees = n_observations - 1
    if degrees < 1:
        return None
    from scipy import stats  # lazy: scipy is heavy and only needed when a spread exists

    alpha = 1.0 - SPREAD_ILLUSTRATION_CONFIDENCE
    return {
        "n_observations": n_observations,
        "degrees_of_freedom": degrees,
        "confidence": SPREAD_ILLUSTRATION_CONFIDENCE,
        "lower_multiplier": math.sqrt(degrees / float(stats.chi2.ppf(1.0 - alpha / 2.0, degrees))),
        "upper_multiplier": math.sqrt(degrees / float(stats.chi2.ppf(alpha / 2.0, degrees))),
        "assumptions": ["independent observations", "a single normal distribution", "no change in the underlying process"],
        "statement": (
            "Illustration only. If these observations were independent draws from one normal distribution, "
            "a 90% interval for the true standard deviation would run from lower_multiplier to upper_multiplier "
            "times the sample estimate. Annual growth and margins are typically autocorrelated, skewed or "
            "shifting, so this is not a distribution-free confidence statement and the true uncertainty can be larger."
        ),
    }


# --------------------------------------------------------------------------
# Public
# --------------------------------------------------------------------------

def build_baseline_provenance(
    financial_data: Mapping,
    *,
    baseline,
    history,
    specs,
    config: MonteCarloConfig,
    risk_free_rate: float,
    context: Optional[Mapping] = None,
) -> dict:
    """Describe how each baseline input was resolved. Pure: same inputs, same block."""
    context = context or {}
    result = baseline.result
    income = financial_data.get("income_statement")
    balance = financial_data.get("balance_sheet")
    cash_flow = financial_data.get("cash_flow")
    derived = extract_valuation_inputs(dict(financial_data))
    kernel = baseline.kernel_inputs

    beta_fallback = derived["beta"] is None
    block = {
        "schema": PROVENANCE_SCHEMA,
        "status": "ok",
        "currency": {
            "reporting_currency": financial_data.get("reporting_currency"),
            "quote_currency": None,
            "quote_currency_recorded": False,
            "note": "The snapshot records the statement currency only; the quote currency is not stored.",
        },
        "timing": _timing_section(financial_data, context),
        "margin": _margin_section(income, history, baseline.fixed_margin),
        "growth": _growth_section(income, history, result),
        "tax_rate": _tax_section(income, result),
        "cost_of_debt": _cost_of_debt_section(income, balance, result, derived["cost_of_debt"]),
        "beta": {
            "value": _num(DEFAULT_BETA if beta_fallback else derived["beta"]),
            "source": "default_fallback" if beta_fallback else "provider",
            "is_fallback": beta_fallback,
        },
        "risk_free_rate": {
            "value": _num(risk_free_rate),
            "source": "captured_with_snapshot",
            "lookup_path": "unknown",
            "resolution_path_recorded": False,
            "note": "The snapshot stores the rate only. Whether it came from a live lookup or a fallback constant is unknown and is not inferred from the value.",
        },
        "market_risk_premium": _fixed_assumption(DEFAULT_MARKET_RISK_PREMIUM, "Model default equity risk premium (CAPM)."),
        "terminal_growth_rate": {"value": config.terminal_growth_rate, "fixed": True, "source": "configuration"},
        "capex": _capex_section(income, cash_flow, result, kernel.capex_pct_revenue),
        "depreciation_and_amortization": _fixed_assumption(kernel.da_pct_revenue, "Flat share of revenue; not derived from statements."),
        "working_capital": _fixed_assumption(
            kernel.nwc_pct_revenue_change, "Flat share of the year's revenue change; not derived from statements."
        ),
        "cash": _cash_section(balance, result, kernel.shares_outstanding),
        "debt": {"value": _num(result["total_debt"]), "row_used": _resolve(balance, TOTAL_DEBT_LABELS)[0]},
        "shares_outstanding": {
            "value": _num(result["shares_outstanding"]),
            "source": "provider",
            "field_recorded": False,
        },
        "market_price": {"value": _num(baseline.market_price), "source": "provider", "field_recorded": False},
        "wacc": _wacc_section(result, specs, config),
        "spread_illustration": {
            "growth": _spread_illustration(history.n_growth) if isinstance(history, HistoricalSpreads) else None,
            "margin": _spread_illustration(history.n_margin) if isinstance(history, HistoricalSpreads) else None,
        },
    }
    block["provenance_digest"] = provenance_digest(block)
    return block


def provenance_digest(block: Mapping) -> str:
    return sha256_hex(canonical_json({k: v for k, v in block.items() if k != "provenance_digest"}))


def unavailable_provenance(error: Exception) -> dict:
    """Placeholder when provenance could not be built; the numerical result is unaffected."""
    block = {"schema": PROVENANCE_SCHEMA, "status": "unavailable", "error_type": type(error).__name__}
    block["provenance_digest"] = provenance_digest(block)
    return block
