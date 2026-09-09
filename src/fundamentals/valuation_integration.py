"""Prepare and shadow-test SEC-backed inputs for the existing DCF.

This module is the only translation seam between a valuation fundamentals
snapshot and ``run_dcf_valuation``.  It accepts already-fetched market
observations, performs no I/O, and never substitutes market-provider statement
data for a missing SEC fact.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
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
SEC_DCF_SHADOW_GATE_VERSION = "sec-dcf-shadow-gate-v1"
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
class YahooTTMStatementBundle:
    """Already-fetched Yahoo TTM statements and one quarter-end balance sheet."""

    ticker: str
    observed_at: datetime
    ttm_income_statement: pd.DataFrame
    ttm_cash_flow: pd.DataFrame
    quarterly_balance_sheet: pd.DataFrame
    source_adapter: str = "yahoo_ttm_statements"

    def __post_init__(self) -> None:
        _require_clean_text("ticker", self.ticker)
        object.__setattr__(self, "ticker", self.ticker.upper())
        if not isinstance(self.observed_at, datetime) or not is_aware(self.observed_at):
            raise ValueError("observed_at must be a timezone-aware datetime.")
        _require_clean_text("source_adapter", self.source_adapter)
        if self.source_adapter != "yahoo_ttm_statements":
            raise ValueError("source_adapter must identify Yahoo TTM statements.")
        for field_name in (
            "ttm_income_statement",
            "ttm_cash_flow",
            "quarterly_balance_sheet",
        ):
            statement = getattr(self, field_name)
            if not isinstance(statement, pd.DataFrame) or statement.empty:
                raise ValueError(f"{field_name} must be a non-empty DataFrame.")
            object.__setattr__(self, field_name, statement.copy(deep=True))


@dataclass(frozen=True)
class SecYahooPeriodAlignment:
    """Explicit mapping from one Yahoo normalized date to one exact SEC period."""

    version: str
    cik: str
    sec_period_end: date
    yahoo_period_end: date

    def __post_init__(self) -> None:
        _require_clean_text("version", self.version)
        object.__setattr__(self, "cik", normalize_cik(self.cik))
        for field_name in ("sec_period_end", "yahoo_period_end"):
            value = getattr(self, field_name)
            if not isinstance(value, date) or isinstance(value, datetime):
                raise ValueError(f"{field_name} must be a date.")


APPLE_SEC_YAHOO_ALIGNMENT_FY2026_Q3_V1 = SecYahooPeriodAlignment(
    version="apple-sec-yahoo-fy2026-q3-v1",
    cik="0000320193",
    sec_period_end=date(2026, 6, 27),
    yahoo_period_end=date(2026, 6, 30),
)


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
    LEGACY_PERIOD_ALIGNMENT_FAILED = "legacy_period_alignment_failed"
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
    period_alignment: SecYahooPeriodAlignment
    yahoo_statements_observed_at: datetime
    sec: DCFShadowValuation
    legacy: DCFShadowValuation
    deltas: Tuple[DCFShadowDelta, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.prepared, PreparedSecDCFInputs):
            raise ValueError("prepared must be PreparedSecDCFInputs.")
        if not isinstance(self.period_alignment, SecYahooPeriodAlignment):
            raise ValueError("period_alignment must be a SecYahooPeriodAlignment.")
        if self.period_alignment.cik != self.prepared.snapshot.request.cik:
            raise ValueError("period_alignment must belong to the prepared issuer.")
        if self.period_alignment.sec_period_end != self.prepared.base_period_end:
            raise ValueError("period_alignment must identify the prepared SEC period end.")
        if not isinstance(self.yahoo_statements_observed_at, datetime) or not is_aware(
            self.yahoo_statements_observed_at
        ):
            raise ValueError("yahoo_statements_observed_at must be timezone-aware.")
        if self.yahoo_statements_observed_at > self.prepared.snapshot.request.knowledge_cutoff:
            raise ValueError("Yahoo statement evidence cannot occur after the knowledge cutoff.")
        if not isinstance(self.sec, DCFShadowValuation) or not isinstance(
            self.legacy, DCFShadowValuation
        ):
            raise ValueError("sec and legacy must be DCFShadowValuation values.")
        object.__setattr__(self, "deltas", tuple(self.deltas))
        if not self.deltas or any(not isinstance(delta, DCFShadowDelta) for delta in self.deltas):
            raise ValueError("deltas must contain typed shadow differences.")
        metrics = [delta.metric for delta in self.deltas]
        if len(metrics) != len(set(metrics)):
            raise ValueError("Shadow delta metrics must be unique.")


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


class SecDCFShadowGateStatus(str, Enum):
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    PASSED = "passed"
    FAILED = "failed"


@dataclass(frozen=True)
class SecDCFShadowThreshold:
    """Maximum acceptable difference for one shadow metric."""

    metric: str
    max_absolute_difference: Optional[Decimal] = None
    max_relative_difference: Optional[Decimal] = None

    def __post_init__(self) -> None:
        _require_clean_text("metric", self.metric)
        if self.max_absolute_difference is None and self.max_relative_difference is None:
            raise ValueError("A shadow threshold must declare at least one limit.")
        for field_name in ("max_absolute_difference", "max_relative_difference"):
            value = getattr(self, field_name)
            if value is not None:
                _require_finite_decimal(field_name, value)
                if value < 0:
                    raise ValueError(f"{field_name} must be non-negative.")


@dataclass(frozen=True)
class SecDCFShadowGatePolicy:
    """Versioned evidence requirement and metric thresholds for cutover review."""

    version: str
    minimum_reports: int
    minimum_observation_dates: int
    minimum_base_period_ends: int
    thresholds: Tuple[SecDCFShadowThreshold, ...]

    def __post_init__(self) -> None:
        _require_clean_text("version", self.version)
        for field_name in (
            "minimum_reports",
            "minimum_observation_dates",
            "minimum_base_period_ends",
        ):
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{field_name} must be a positive integer.")
        if self.minimum_observation_dates > self.minimum_reports:
            raise ValueError("minimum_observation_dates cannot exceed minimum_reports.")
        if self.minimum_base_period_ends > self.minimum_reports:
            raise ValueError("minimum_base_period_ends cannot exceed minimum_reports.")
        object.__setattr__(self, "thresholds", tuple(self.thresholds))
        if not self.thresholds or any(
            not isinstance(item, SecDCFShadowThreshold) for item in self.thresholds
        ):
            raise ValueError("thresholds must contain SecDCFShadowThreshold values.")
        metrics = [item.metric for item in self.thresholds]
        if len(metrics) != len(set(metrics)):
            raise ValueError("Shadow threshold metrics must be unique.")


@dataclass(frozen=True)
class SecDCFShadowBreach:
    report_index: int
    metric: str
    absolute_difference: Decimal
    relative_difference: Optional[Decimal]

    def __post_init__(self) -> None:
        if (
            isinstance(self.report_index, bool)
            or not isinstance(self.report_index, int)
            or self.report_index < 0
        ):
            raise ValueError("report_index must be a non-negative integer.")
        _require_clean_text("metric", self.metric)
        _require_finite_decimal("absolute_difference", self.absolute_difference)
        if self.relative_difference is not None:
            _require_finite_decimal("relative_difference", self.relative_difference)


@dataclass(frozen=True)
class SecDCFShadowGateResult:
    policy_version: str
    status: SecDCFShadowGateStatus
    report_count: int
    observation_date_count: int
    base_period_end_count: int
    breaches: Tuple[SecDCFShadowBreach, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        _require_clean_text("policy_version", self.policy_version)
        if not isinstance(self.status, SecDCFShadowGateStatus):
            raise ValueError("status must be a SecDCFShadowGateStatus.")
        for field_name in (
            "report_count",
            "observation_date_count",
            "base_period_end_count",
        ):
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{field_name} must be a non-negative integer.")
        object.__setattr__(self, "breaches", tuple(self.breaches))
        if any(not isinstance(item, SecDCFShadowBreach) for item in self.breaches):
            raise ValueError("breaches must contain SecDCFShadowBreach values.")
        if self.status is SecDCFShadowGateStatus.PASSED and self.breaches:
            raise ValueError("A passed shadow gate cannot contain breaches.")
        if self.status is SecDCFShadowGateStatus.FAILED and not self.breaches:
            raise ValueError("A failed shadow gate must contain at least one breach.")


# Absolute limits are percentage-point tolerances for rate assumptions. Relative
# limits apply to financial amounts and valuation outputs. The gate also requires
# repeated observations so one provider snapshot can never authorize cutover.
SEC_DCF_SHADOW_GATE_V1 = SecDCFShadowGatePolicy(
    version=SEC_DCF_SHADOW_GATE_VERSION,
    minimum_reports=5,
    minimum_observation_dates=5,
    minimum_base_period_ends=4,
    thresholds=(
        SecDCFShadowThreshold("base_revenue", max_relative_difference=Decimal("0.05")),
        SecDCFShadowThreshold(
            "revenue_growth_rate", max_absolute_difference=Decimal("0.03")
        ),
        SecDCFShadowThreshold(
            "operating_margin", max_absolute_difference=Decimal("0.02")
        ),
        SecDCFShadowThreshold("tax_rate", max_absolute_difference=Decimal("0.03")),
        SecDCFShadowThreshold("cost_of_debt", max_absolute_difference=Decimal("0.01")),
        SecDCFShadowThreshold("total_debt", max_relative_difference=Decimal("0.10")),
        SecDCFShadowThreshold(
            "cash_and_equivalents", max_relative_difference=Decimal("0.10")
        ),
        SecDCFShadowThreshold("wacc", max_absolute_difference=Decimal("0.01")),
        SecDCFShadowThreshold("enterprise_value", max_relative_difference=Decimal("0.10")),
        SecDCFShadowThreshold("equity_value", max_relative_difference=Decimal("0.10")),
        SecDCFShadowThreshold(
            "intrinsic_value_per_share", max_relative_difference=Decimal("0.10")
        ),
    ),
)


def evaluate_sec_dcf_shadow_gate(
    reports: Tuple[SecDCFShadowReport, ...],
    policy: SecDCFShadowGatePolicy = SEC_DCF_SHADOW_GATE_V1,
) -> SecDCFShadowGateResult:
    """Evaluate reproducible shadow reports without authorizing a cutover."""

    if isinstance(reports, (str, bytes)):
        raise ValueError("reports must be a collection of SecDCFShadowReport values.")
    try:
        reports = tuple(reports)
    except TypeError:
        raise ValueError(
            "reports must be a collection of SecDCFShadowReport values."
        ) from None
    if any(not isinstance(report, SecDCFShadowReport) for report in reports):
        raise ValueError("reports must contain SecDCFShadowReport values.")
    if not isinstance(policy, SecDCFShadowGatePolicy):
        raise ValueError("policy must be a SecDCFShadowGatePolicy.")

    threshold_by_metric = {item.metric: item for item in policy.thresholds}
    breaches = []
    for report_index, report in enumerate(reports):
        delta_by_metric = {item.metric: item for item in report.deltas}
        missing = set(threshold_by_metric) - set(delta_by_metric)
        if missing:
            raise ValueError("Every shadow report must contain every threshold metric.")
        for metric, threshold in threshold_by_metric.items():
            delta = delta_by_metric[metric]
            absolute_breach = (
                threshold.max_absolute_difference is not None
                and abs(delta.absolute_difference) > threshold.max_absolute_difference
            )
            relative_breach = False
            if threshold.max_relative_difference is not None:
                relative_breach = (
                    delta.relative_difference is None
                    or abs(delta.relative_difference) > threshold.max_relative_difference
                )
            if absolute_breach or relative_breach:
                breaches.append(
                    SecDCFShadowBreach(
                        report_index=report_index,
                        metric=metric,
                        absolute_difference=delta.absolute_difference,
                        relative_difference=delta.relative_difference,
                    )
                )

    observation_dates = {
        report.yahoo_statements_observed_at.date() for report in reports
    }
    base_period_ends = {report.prepared.base_period_end for report in reports}
    if (
        len(reports) < policy.minimum_reports
        or len(observation_dates) < policy.minimum_observation_dates
        or len(base_period_ends) < policy.minimum_base_period_ends
    ):
        status = SecDCFShadowGateStatus.INSUFFICIENT_EVIDENCE
    elif breaches:
        status = SecDCFShadowGateStatus.FAILED
    else:
        status = SecDCFShadowGateStatus.PASSED
    return SecDCFShadowGateResult(
        policy_version=policy.version,
        status=status,
        report_count=len(reports),
        observation_date_count=len(observation_dates),
        base_period_end_count=len(base_period_ends),
        breaches=tuple(breaches),
    )


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


def _column_for_date(statement: pd.DataFrame, period_end: date):
    matches = []
    for column in statement.columns:
        try:
            column_date = pd.Timestamp(column).date()
        except (TypeError, ValueError):
            continue
        if column_date == period_end:
            matches.append(column)
    if len(matches) != 1:
        raise ValueError("The required Yahoo statement period is missing or ambiguous.")
    return matches[0]


def _aligned_statement(statement: pd.DataFrame, source_column, sec_period_end: date) -> pd.DataFrame:
    aligned = statement.loc[:, [source_column]].copy(deep=True)
    aligned.columns = (pd.Timestamp(sec_period_end),)
    return aligned


def _finite_row_value(
    statement: pd.DataFrame,
    aliases: Tuple[str, ...],
) -> Optional[Decimal]:
    column = statement.columns[0]
    for alias in aliases:
        if alias not in statement.index:
            continue
        try:
            value = Decimal(str(statement.loc[alias, column]))
        except (ArithmeticError, TypeError, ValueError):
            continue
        if value.is_finite():
            return value
    return None


def _aligned_yahoo_ttm_financial_data(
    prepared: PreparedSecDCFInputs,
    bundle: YahooTTMStatementBundle,
    alignment: SecYahooPeriodAlignment,
) -> Dict[str, object]:
    if bundle.ticker != prepared.market.ticker:
        raise ValueError("Yahoo statements and market observations identify different tickers.")
    if bundle.observed_at > prepared.snapshot.request.knowledge_cutoff:
        raise ValueError("Yahoo statements occur after the snapshot knowledge cutoff.")
    if alignment.cik != prepared.snapshot.request.cik:
        raise ValueError("The period alignment belongs to a different issuer.")
    if alignment.sec_period_end != prepared.base_period_end:
        raise ValueError("The period alignment does not match the SEC TTM period end.")

    income = _aligned_statement(
        bundle.ttm_income_statement,
        _column_for_date(bundle.ttm_income_statement, alignment.yahoo_period_end),
        alignment.sec_period_end,
    )
    cash_flow = _aligned_statement(
        bundle.ttm_cash_flow,
        _column_for_date(bundle.ttm_cash_flow, alignment.yahoo_period_end),
        alignment.sec_period_end,
    )
    balance = _aligned_statement(
        bundle.quarterly_balance_sheet,
        _column_for_date(bundle.quarterly_balance_sheet, alignment.yahoo_period_end),
        alignment.sec_period_end,
    )
    values = {
        "revenue": _finite_row_value(income, ("Total Revenue", "TotalRevenue")),
        "operating_income": _finite_row_value(
            income, ("Operating Income", "OperatingIncome", "EBIT", "Ebit")
        ),
        "operating_cash_flow": _finite_row_value(
            cash_flow,
            (
                "Operating Cash Flow",
                "Cash Flow From Continuing Operating Activities",
                "Total Cash From Operating Activities",
            ),
        ),
        "capital_expenditures": _finite_row_value(
            cash_flow,
            ("Capital Expenditure", "CapitalExpenditure", "Purchase Of PPE"),
        ),
        "total_debt": _finite_row_value(balance, ("Total Debt", "TotalDebt")),
        "cash_and_equivalents": _finite_row_value(
            balance,
            (
                "Cash And Cash Equivalents",
                "CashAndCashEquivalents",
                "Cash Cash Equivalents And Short Term Investments",
            ),
        ),
    }
    if any(value is None for value in values.values()):
        raise ValueError("The aligned Yahoo statements are missing a required finite value.")
    if values["revenue"] <= 0:
        raise ValueError("Aligned Yahoo TTM revenue must be positive.")
    if values["capital_expenditures"] > 0:
        raise ValueError("Aligned Yahoo capital expenditures must use the negative-outflow sign.")
    if values["total_debt"] < 0 or values["cash_and_equivalents"] < 0:
        raise ValueError("Aligned Yahoo debt and cash must be non-negative.")
    return {
        "ticker": bundle.ticker,
        "income_statement": income,
        "balance_sheet": balance,
        "cash_flow": cash_flow,
    }


def run_sec_dcf_shadow(
    prepared: PreparedSecDCFInputs,
    yahoo_ttm_statements: YahooTTMStatementBundle,
    period_alignment: SecYahooPeriodAlignment,
) -> SecDCFShadowResult:
    """Run SEC and Yahoo TTM valuations on one explicitly aligned period."""

    if not isinstance(prepared, PreparedSecDCFInputs):
        raise ValueError("prepared must be PreparedSecDCFInputs.")
    if not isinstance(yahoo_ttm_statements, YahooTTMStatementBundle):
        raise ValueError("yahoo_ttm_statements must be a YahooTTMStatementBundle.")
    if not isinstance(period_alignment, SecYahooPeriodAlignment):
        raise ValueError("period_alignment must be a SecYahooPeriodAlignment.")

    try:
        sec_result = run_dcf_valuation(_sec_financial_data(prepared), _sec_assumptions(prepared))
    except ValueError:
        return SecDCFShadowResult(
            issues=_issue(
                SecDCFIntegrationIssueCode.SEC_DCF_FAILED,
                "The prepared SEC inputs were refused by the DCF.",
            )
        )

    try:
        legacy_data = _aligned_yahoo_ttm_financial_data(
            prepared,
            yahoo_ttm_statements,
            period_alignment,
        )
    except ValueError:
        return SecDCFShadowResult(
            issues=_issue(
                SecDCFIntegrationIssueCode.LEGACY_PERIOD_ALIGNMENT_FAILED,
                "Yahoo TTM statements could not be aligned to the SEC snapshot period.",
            )
        )
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
            _sec_assumptions(prepared),
        )
    except ValueError:
        return SecDCFShadowResult(
            issues=_issue(
                SecDCFIntegrationIssueCode.LEGACY_DCF_FAILED,
                "The legacy statement inputs were refused by the DCF.",
            )
        )

    sec = _valuation("sec_snapshot", sec_result)
    legacy = _valuation(yahoo_ttm_statements.source_adapter, legacy_result)
    return SecDCFShadowResult(
        report=SecDCFShadowReport(
            prepared=prepared,
            period_alignment=period_alignment,
            yahoo_statements_observed_at=yahoo_ttm_statements.observed_at,
            sec=sec,
            legacy=legacy,
            deltas=_deltas(sec, legacy),
        )
    )
