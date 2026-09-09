"""Prepare and shadow-test SEC-backed inputs for the existing DCF.

This module is the only translation seam between a valuation fundamentals
snapshot and ``run_dcf_valuation``.  It accepts already-fetched market
observations, performs no I/O, and never substitutes market-provider statement
data for a missing SEC fact.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Dict, Optional, Tuple

import pandas as pd

from src.dcf_model.dcf import (
    MAX_EXPLICIT_OPERATING_MARGIN,
    MAX_EXPLICIT_REVENUE_GROWTH_RATE,
    MAX_EXPLICIT_TERMINAL_GROWTH_RATE,
    MIN_EXPLICIT_OPERATING_MARGIN,
    MIN_EXPLICIT_REVENUE_GROWTH_RATE,
    MIN_EXPLICIT_TERMINAL_GROWTH_RATE,
    DCFAssumptions,
    run_dcf_valuation,
)

from .time_policy import is_aware
from .types import normalize_cik
from .valuation_snapshot import ValuationFundamentalsSnapshot


SEC_DCF_POLICY_VERSION = "sec-dcf-composition-v1"
_RECENT_TTM_OBSERVATIONS = 4


def _require_finite_decimal(field_name: str, value: Decimal) -> None:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise ValueError(f"{field_name} must be a finite Decimal.")


def _require_clean_text(field_name: str, value: str) -> None:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ValueError(f"{field_name} must be non-empty, whitespace-trimmed text.")


def _median(values: Tuple[Decimal, ...]) -> Decimal:
    ordered = tuple(sorted(values))
    midpoint = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[midpoint]
    return (ordered[midpoint - 1] + ordered[midpoint]) / Decimal("2")


@dataclass(frozen=True)
class ValuationMarketObservations:
    """Already-fetched market-only values used beside one SEC snapshot."""

    cik: str
    ticker: str
    observed_at: datetime
    current_price: Decimal
    current_shares_outstanding: Decimal
    levered_beta: Decimal
    sector: str
    risk_free_rate: Decimal
    equity_source_adapter: str
    risk_free_rate_source_adapter: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "cik", normalize_cik(self.cik))
        _require_clean_text("ticker", self.ticker)
        object.__setattr__(self, "ticker", self.ticker.upper())
        if not isinstance(self.observed_at, datetime) or not is_aware(self.observed_at):
            raise ValueError("observed_at must be a timezone-aware datetime.")
        for field_name in (
            "current_price",
            "current_shares_outstanding",
            "levered_beta",
            "risk_free_rate",
        ):
            _require_finite_decimal(field_name, getattr(self, field_name))
        if self.current_price <= 0:
            raise ValueError("current_price must be positive.")
        if self.current_shares_outstanding <= 0:
            raise ValueError("current_shares_outstanding must be positive.")
        for field_name in (
            "sector",
            "equity_source_adapter",
            "risk_free_rate_source_adapter",
        ):
            _require_clean_text(field_name, getattr(self, field_name))


@dataclass(frozen=True)
class SecDCFPolicy:
    """Explicit model policy for quantities not supplied by the SEC snapshot."""

    version: str = SEC_DCF_POLICY_VERSION
    terminal_growth_rate: Decimal = Decimal("0.025")
    market_risk_premium: Decimal = Decimal("0.055")
    tax_rate: Decimal = Decimal("0.21")
    cost_of_debt: Decimal = Decimal("0.05")
    depreciation_and_amortization_pct_revenue: Decimal = Decimal("0.03")
    net_working_capital_pct_revenue_change: Decimal = Decimal("0.01")
    projection_years: int = 5

    def __post_init__(self) -> None:
        _require_clean_text("version", self.version)
        if self.version != SEC_DCF_POLICY_VERSION:
            raise ValueError("version must identify the implemented SEC DCF policy.")
        for field_name in (
            "terminal_growth_rate",
            "market_risk_premium",
            "tax_rate",
            "cost_of_debt",
            "depreciation_and_amortization_pct_revenue",
            "net_working_capital_pct_revenue_change",
        ):
            _require_finite_decimal(field_name, getattr(self, field_name))
        if not Decimal("0") <= self.tax_rate < Decimal("1"):
            raise ValueError("tax_rate must be in [0, 1).")
        if not (
            Decimal(str(MIN_EXPLICIT_TERMINAL_GROWTH_RATE))
            <= self.terminal_growth_rate
            <= Decimal(str(MAX_EXPLICIT_TERMINAL_GROWTH_RATE))
        ):
            raise ValueError("terminal_growth_rate is outside the DCF's supported range.")
        if self.cost_of_debt < 0:
            raise ValueError("cost_of_debt must be non-negative.")
        if self.depreciation_and_amortization_pct_revenue < 0:
            raise ValueError("depreciation_and_amortization_pct_revenue must be non-negative.")
        if self.net_working_capital_pct_revenue_change < 0:
            raise ValueError("net_working_capital_pct_revenue_change must be non-negative.")
        if (
            isinstance(self.projection_years, bool)
            or not isinstance(self.projection_years, int)
            or self.projection_years <= 0
        ):
            raise ValueError("projection_years must be a positive integer.")


@dataclass(frozen=True)
class PreparedSecDCFInputs:
    """Complete SEC-backed inputs and explicit assumptions for one DCF run."""

    snapshot: ValuationFundamentalsSnapshot
    market: ValuationMarketObservations
    policy: SecDCFPolicy
    revenue_growth_rate: Decimal
    operating_margin: Decimal
    capital_expenditures_pct_revenue: Decimal

    def __post_init__(self) -> None:
        if not isinstance(self.snapshot, ValuationFundamentalsSnapshot):
            raise ValueError("snapshot must be a ValuationFundamentalsSnapshot.")
        if not isinstance(self.market, ValuationMarketObservations):
            raise ValueError("market must be ValuationMarketObservations.")
        if not isinstance(self.policy, SecDCFPolicy):
            raise ValueError("policy must be SecDCFPolicy.")
        for field_name in (
            "revenue_growth_rate",
            "operating_margin",
            "capital_expenditures_pct_revenue",
        ):
            _require_finite_decimal(field_name, getattr(self, field_name))
        if self.snapshot.request.cik != self.market.cik:
            raise ValueError("SEC and market observations must identify the same issuer.")
        if self.market.observed_at > self.snapshot.request.knowledge_cutoff:
            raise ValueError("Market observations cannot occur after the knowledge cutoff.")
        if not (
            Decimal(str(MIN_EXPLICIT_REVENUE_GROWTH_RATE))
            <= self.revenue_growth_rate
            <= Decimal(str(MAX_EXPLICIT_REVENUE_GROWTH_RATE))
        ):
            raise ValueError("revenue_growth_rate is outside the DCF's supported range.")
        if not (
            Decimal(str(MIN_EXPLICIT_OPERATING_MARGIN))
            <= self.operating_margin
            <= Decimal(str(MAX_EXPLICIT_OPERATING_MARGIN))
        ):
            raise ValueError("operating_margin is outside the DCF's supported range.")
        if self.capital_expenditures_pct_revenue < 0:
            raise ValueError("capital_expenditures_pct_revenue must be non-negative.")

    @property
    def base_period_end(self):
        return self.snapshot.latest.period_end


class SecDCFIntegrationIssueCode(str, Enum):
    ISSUER_MISMATCH = "issuer_mismatch"
    FUTURE_MARKET_OBSERVATION = "future_market_observation"
    INSUFFICIENT_SMOOTHING_HISTORY = "insufficient_smoothing_history"
    UNSUPPORTED_DERIVED_ASSUMPTION = "unsupported_derived_assumption"
    SEC_DCF_FAILED = "sec_dcf_failed"
    LEGACY_DCF_FAILED = "legacy_dcf_failed"


@dataclass(frozen=True)
class SecDCFIntegrationIssue:
    code: SecDCFIntegrationIssueCode
    message: str

    def __post_init__(self) -> None:
        if not isinstance(self.code, SecDCFIntegrationIssueCode):
            raise ValueError("code must be SecDCFIntegrationIssueCode.")
        _require_clean_text("message", self.message)


@dataclass(frozen=True)
class SecDCFPreparationResult:
    prepared: Optional[PreparedSecDCFInputs] = None
    issues: Tuple[SecDCFIntegrationIssue, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        object.__setattr__(self, "issues", tuple(self.issues))
        if self.prepared is not None and not isinstance(self.prepared, PreparedSecDCFInputs):
            raise ValueError("prepared must be PreparedSecDCFInputs.")
        if any(not isinstance(issue, SecDCFIntegrationIssue) for issue in self.issues):
            raise ValueError("issues must contain only SecDCFIntegrationIssue values.")
        if (self.prepared is None) == (not self.issues):
            raise ValueError("Preparation must return complete inputs or refusal issues.")

    @property
    def is_complete(self) -> bool:
        return self.prepared is not None


@dataclass(frozen=True)
class DCFShadowValuation:
    source: str
    intrinsic_value_per_share: Decimal
    enterprise_value: Decimal
    equity_value: Decimal
    wacc: Decimal
    revenue_growth_rate: Decimal
    operating_margin: Decimal
    tax_rate: Decimal
    cost_of_debt: Decimal
    base_revenue: Decimal
    total_debt: Decimal
    cash_and_equivalents: Decimal

    def __post_init__(self) -> None:
        _require_clean_text("source", self.source)
        for field_name in (
            "intrinsic_value_per_share",
            "enterprise_value",
            "equity_value",
            "wacc",
            "revenue_growth_rate",
            "operating_margin",
            "tax_rate",
            "cost_of_debt",
            "base_revenue",
            "total_debt",
            "cash_and_equivalents",
        ):
            _require_finite_decimal(field_name, getattr(self, field_name))


@dataclass(frozen=True)
class DCFShadowDelta:
    metric: str
    sec_value: Decimal
    legacy_value: Decimal
    absolute_difference: Decimal
    relative_difference: Optional[Decimal]

    def __post_init__(self) -> None:
        _require_clean_text("metric", self.metric)
        for field_name in (
            "sec_value",
            "legacy_value",
            "absolute_difference",
        ):
            _require_finite_decimal(field_name, getattr(self, field_name))
        if self.relative_difference is not None:
            _require_finite_decimal("relative_difference", self.relative_difference)
        if self.absolute_difference != self.sec_value - self.legacy_value:
            raise ValueError("absolute_difference must equal SEC minus legacy.")


@dataclass(frozen=True)
class SecDCFShadowReport:
    prepared: PreparedSecDCFInputs
    sec: DCFShadowValuation
    legacy: DCFShadowValuation
    deltas: Tuple[DCFShadowDelta, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.prepared, PreparedSecDCFInputs):
            raise ValueError("prepared must be PreparedSecDCFInputs.")
        if not isinstance(self.sec, DCFShadowValuation) or not isinstance(
            self.legacy, DCFShadowValuation
        ):
            raise ValueError("sec and legacy must be DCFShadowValuation values.")
        object.__setattr__(self, "deltas", tuple(self.deltas))
        if not self.deltas or any(not isinstance(delta, DCFShadowDelta) for delta in self.deltas):
            raise ValueError("deltas must contain typed shadow differences.")


@dataclass(frozen=True)
class SecDCFShadowResult:
    report: Optional[SecDCFShadowReport] = None
    issues: Tuple[SecDCFIntegrationIssue, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        object.__setattr__(self, "issues", tuple(self.issues))
        if self.report is not None and not isinstance(self.report, SecDCFShadowReport):
            raise ValueError("report must be a SecDCFShadowReport.")
        if any(not isinstance(issue, SecDCFIntegrationIssue) for issue in self.issues):
            raise ValueError("issues must contain only SecDCFIntegrationIssue values.")
        if (self.report is None) == (not self.issues):
            raise ValueError("Shadow execution must return one report or refusal issues.")

    @property
    def is_complete(self) -> bool:
        return self.report is not None


def _issue(
    code: SecDCFIntegrationIssueCode,
    message: str,
) -> Tuple[SecDCFIntegrationIssue, ...]:
    return (SecDCFIntegrationIssue(code=code, message=message),)


def prepare_sec_dcf_inputs(
    snapshot: ValuationFundamentalsSnapshot,
    market: ValuationMarketObservations,
    policy: Optional[SecDCFPolicy] = None,
) -> SecDCFPreparationResult:
    """Combine one complete SEC snapshot with market-only observations."""

    if not isinstance(snapshot, ValuationFundamentalsSnapshot):
        raise ValueError("snapshot must be a ValuationFundamentalsSnapshot.")
    if not isinstance(market, ValuationMarketObservations):
        raise ValueError("market must be ValuationMarketObservations.")
    policy = policy or SecDCFPolicy()
    if not isinstance(policy, SecDCFPolicy):
        raise ValueError("policy must be SecDCFPolicy.")
    if snapshot.request.cik != market.cik:
        return SecDCFPreparationResult(
            issues=_issue(
                SecDCFIntegrationIssueCode.ISSUER_MISMATCH,
                "SEC and market observations identify different issuers.",
            )
        )
    if market.observed_at > snapshot.request.knowledge_cutoff:
        return SecDCFPreparationResult(
            issues=_issue(
                SecDCFIntegrationIssueCode.FUTURE_MARKET_OBSERVATION,
                "Market observations occur after the snapshot knowledge cutoff.",
            )
        )
    if len(snapshot.comparable_revenue_growth) < _RECENT_TTM_OBSERVATIONS:
        return SecDCFPreparationResult(
            issues=_issue(
                SecDCFIntegrationIssueCode.INSUFFICIENT_SMOOTHING_HISTORY,
                "Four comparable TTM observations are required to resist one-quarter distortion.",
            )
        )

    recent_growth = tuple(
        item.rate for item in snapshot.comparable_revenue_growth[-_RECENT_TTM_OBSERVATIONS:]
    )
    recent_periods = snapshot.trailing_periods[-_RECENT_TTM_OBSERVATIONS:]
    revenue_growth_rate = _median(recent_growth)
    operating_margin = _median(tuple(period.operating_margin for period in recent_periods))
    capital_expenditures_pct_revenue = _median(
        tuple(period.capital_expenditures / period.revenue for period in recent_periods)
    )

    if not (
        Decimal(str(MIN_EXPLICIT_REVENUE_GROWTH_RATE))
        <= revenue_growth_rate
        <= Decimal(str(MAX_EXPLICIT_REVENUE_GROWTH_RATE))
    ) or not (
        Decimal(str(MIN_EXPLICIT_OPERATING_MARGIN))
        <= operating_margin
        <= Decimal(str(MAX_EXPLICIT_OPERATING_MARGIN))
    ):
        return SecDCFPreparationResult(
            issues=_issue(
                SecDCFIntegrationIssueCode.UNSUPPORTED_DERIVED_ASSUMPTION,
                "Smoothed SEC growth or margin falls outside the DCF's supported range.",
            )
        )

    return SecDCFPreparationResult(
        prepared=PreparedSecDCFInputs(
            snapshot=snapshot,
            market=market,
            policy=policy,
            revenue_growth_rate=revenue_growth_rate,
            operating_margin=operating_margin,
            capital_expenditures_pct_revenue=capital_expenditures_pct_revenue,
        )
    )


def _sec_financial_data(prepared: PreparedSecDCFInputs) -> Dict[str, object]:
    latest = prepared.snapshot.latest
    balance = prepared.snapshot.latest_balance
    column = pd.Timestamp(latest.period_end)
    return {
        "ticker": prepared.market.ticker,
        "income_statement": pd.DataFrame(
            {column: {"Total Revenue": float(latest.revenue), "Operating Income": float(latest.operating_income)}}
        ),
        "balance_sheet": pd.DataFrame(
            {
                column: {
                    "Total Debt": float(balance.reported_term_debt),
                    "Cash And Cash Equivalents": float(balance.cash_and_cash_equivalents),
                }
            }
        ),
        "cash_flow": pd.DataFrame(
            {
                column: {
                    "Operating Cash Flow": float(latest.operating_cash_flow),
                    # The legacy DCF parser expects cash outflows to be negative.
                    "Capital Expenditure": -float(latest.capital_expenditures),
                }
            }
        ),
        "current_price": float(prepared.market.current_price),
        "shares_outstanding": float(prepared.market.current_shares_outstanding),
        "beta": float(prepared.market.levered_beta),
        "sector": prepared.market.sector,
    }


def _sec_assumptions(prepared: PreparedSecDCFInputs) -> DCFAssumptions:
    policy = prepared.policy
    return DCFAssumptions(
        revenue_growth_rate=float(prepared.revenue_growth_rate),
        operating_margin=float(prepared.operating_margin),
        terminal_growth_rate=float(policy.terminal_growth_rate),
        projection_years=policy.projection_years,
        tax_rate=float(policy.tax_rate),
        cost_of_debt=float(policy.cost_of_debt),
        risk_free_rate=float(prepared.market.risk_free_rate),
        market_risk_premium=float(policy.market_risk_premium),
        da_pct_revenue=float(policy.depreciation_and_amortization_pct_revenue),
        capex_pct_revenue=float(prepared.capital_expenditures_pct_revenue),
        nwc_pct_revenue_change=float(policy.net_working_capital_pct_revenue_change),
    )


def _decimal(value) -> Decimal:
    return Decimal(str(value))


def _valuation(source: str, result: dict) -> DCFShadowValuation:
    return DCFShadowValuation(
        source=source,
        intrinsic_value_per_share=_decimal(result["intrinsic_value_per_share"]),
        enterprise_value=_decimal(result["enterprise_value"]),
        equity_value=_decimal(result["equity_value"]),
        wacc=_decimal(result["wacc"]),
        revenue_growth_rate=_decimal(result["revenue_growth_rate"]),
        operating_margin=_decimal(result["operating_margin"]),
        tax_rate=_decimal(result["tax_rate"]),
        cost_of_debt=_decimal(result["cost_of_debt"]),
        base_revenue=_decimal(result["base_revenue"]),
        total_debt=_decimal(result["total_debt"] or 0),
        cash_and_equivalents=_decimal(result["cash_and_equivalents"] or 0),
    )


def _deltas(
    sec: DCFShadowValuation,
    legacy: DCFShadowValuation,
) -> Tuple[DCFShadowDelta, ...]:
    metrics = (
        "base_revenue",
        "revenue_growth_rate",
        "operating_margin",
        "tax_rate",
        "cost_of_debt",
        "total_debt",
        "cash_and_equivalents",
        "wacc",
        "enterprise_value",
        "equity_value",
        "intrinsic_value_per_share",
    )
    result = []
    for metric in metrics:
        sec_value = getattr(sec, metric)
        legacy_value = getattr(legacy, metric)
        absolute = sec_value - legacy_value
        result.append(
            DCFShadowDelta(
                metric=metric,
                sec_value=sec_value,
                legacy_value=legacy_value,
                absolute_difference=absolute,
                relative_difference=(absolute / abs(legacy_value) if legacy_value else None),
            )
        )
    return tuple(result)


def run_sec_dcf_shadow(
    prepared: PreparedSecDCFInputs,
    legacy_financial_data: dict,
) -> SecDCFShadowResult:
    """Run candidate and legacy valuations offline with identical market values."""

    if not isinstance(prepared, PreparedSecDCFInputs):
        raise ValueError("prepared must be PreparedSecDCFInputs.")
    if not isinstance(legacy_financial_data, dict):
        raise ValueError("legacy_financial_data must be a dict.")

    try:
        sec_result = run_dcf_valuation(_sec_financial_data(prepared), _sec_assumptions(prepared))
    except ValueError:
        return SecDCFShadowResult(
            issues=_issue(
                SecDCFIntegrationIssueCode.SEC_DCF_FAILED,
                "The prepared SEC inputs were refused by the DCF.",
            )
        )

    legacy_data = dict(legacy_financial_data)
    legacy_data.update(
        {
            "ticker": prepared.market.ticker,
            "current_price": float(prepared.market.current_price),
            "shares_outstanding": float(prepared.market.current_shares_outstanding),
            "beta": float(prepared.market.levered_beta),
            "sector": prepared.market.sector,
        }
    )
    try:
        legacy_result = run_dcf_valuation(
            legacy_data,
            DCFAssumptions(
                terminal_growth_rate=float(prepared.policy.terminal_growth_rate),
                projection_years=prepared.policy.projection_years,
                risk_free_rate=float(prepared.market.risk_free_rate),
                market_risk_premium=float(prepared.policy.market_risk_premium),
            ),
        )
    except ValueError:
        return SecDCFShadowResult(
            issues=_issue(
                SecDCFIntegrationIssueCode.LEGACY_DCF_FAILED,
                "The legacy statement inputs were refused by the DCF.",
            )
        )

    sec = _valuation("sec_snapshot", sec_result)
    legacy = _valuation("legacy_yahoo_statements", legacy_result)
    return SecDCFShadowResult(
        report=SecDCFShadowReport(
            prepared=prepared,
            sec=sec,
            legacy=legacy,
            deltas=_deltas(sec, legacy),
        )
    )
