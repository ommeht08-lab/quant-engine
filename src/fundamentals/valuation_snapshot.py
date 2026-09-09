"""Load one typed, point-in-time SEC fundamentals snapshot for valuation.

The module owns the seam between the append-only fundamentals repository and
future valuation callers.  It does not download filings, publish facts, fetch
market data, or invoke the DCF.  A caller receives either one internally
consistent SEC-derived snapshot or one typed refusal.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Dict, Optional, Tuple

from .quarterly import QuarterlyFundamentals, TrailingTwelveMonthValue, assemble_quarterly_fundamentals
from .repository import (
    FundamentalsQuery,
    FundamentalsRepository,
    FundamentalsRepositoryUnavailable,
)
from .selection import select_point_in_time
from .time_policy import is_aware
from .types import FinancialFact, FundamentalHistory, normalize_cik


VALUATION_TTM_CONCEPTS = (
    "capital_expenditures",
    "operating_cash_flow",
    "operating_income",
    "revenue",
)
VALUATION_BALANCE_CONCEPTS = (
    "cash_and_cash_equivalents",
    "current_debt",
    "long_term_debt",
)
VALUATION_FUNDAMENTALS_CONCEPTS = tuple(
    sorted(VALUATION_TTM_CONCEPTS + VALUATION_BALANCE_CONCEPTS)
)
_MAX_REPOSITORY_PERIODS_PER_STATEMENT = 64


@dataclass(frozen=True)
class ValuationFundamentalsRequest:
    """Reproducibility coordinates for one SEC-derived valuation snapshot."""

    cik: str
    knowledge_cutoff: datetime
    data_vintage_cutoff: datetime
    source_adapter: str
    concept_map_version: str
    fiscal_calendar_version: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "cik", normalize_cik(self.cik))
        for field_name in ("knowledge_cutoff", "data_vintage_cutoff"):
            value = getattr(self, field_name)
            if not isinstance(value, datetime) or not is_aware(value):
                raise ValueError(f"{field_name} must be a timezone-aware datetime.")
        for field_name in (
            "source_adapter",
            "concept_map_version",
            "fiscal_calendar_version",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip() or value != value.strip():
                raise ValueError(f"{field_name} must be non-empty, whitespace-trimmed text.")

@dataclass(frozen=True)
class ValuationTrailingPeriod:
    """Four consecutive fiscal quarters on one exact consolidated USD basis."""

    fiscal_year: int
    fiscal_quarter: int
    period_start: date
    period_end: date
    revenue: Decimal
    operating_income: Decimal
    operating_cash_flow: Decimal
    capital_expenditures: Decimal
    free_cash_flow: Decimal
    source_values: Tuple[TrailingTwelveMonthValue, ...]

    def __post_init__(self) -> None:
        if isinstance(self.fiscal_year, bool) or not isinstance(self.fiscal_year, int):
            raise ValueError("fiscal_year must be an integer.")
        if self.fiscal_year <= 0:
            raise ValueError("fiscal_year must be positive.")
        if (
            isinstance(self.fiscal_quarter, bool)
            or not isinstance(self.fiscal_quarter, int)
            or self.fiscal_quarter not in (1, 2, 3, 4)
        ):
            raise ValueError("fiscal_quarter must be from 1 through 4.")
        if (
            not isinstance(self.period_start, date)
            or isinstance(self.period_start, datetime)
            or not isinstance(self.period_end, date)
            or isinstance(self.period_end, datetime)
            or self.period_start > self.period_end
        ):
            raise ValueError("Trailing-period boundaries must be valid dates.")
        for field_name in (
            "revenue",
            "operating_income",
            "operating_cash_flow",
            "capital_expenditures",
            "free_cash_flow",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, Decimal) or not value.is_finite():
                raise ValueError(f"{field_name} must be a finite Decimal.")
        if self.revenue <= 0:
            raise ValueError("revenue must be positive.")
        if self.capital_expenditures < 0:
            raise ValueError("capital_expenditures must use a positive-spend convention.")
        if self.free_cash_flow != self.operating_cash_flow - self.capital_expenditures:
            raise ValueError("free_cash_flow must equal operating cash flow minus capital expenditures.")
        object.__setattr__(self, "source_values", tuple(self.source_values))
        if len(self.source_values) != len(VALUATION_TTM_CONCEPTS) or any(
            not isinstance(value, TrailingTwelveMonthValue) for value in self.source_values
        ):
            raise ValueError("source_values must contain one TTM value per required concept.")
        sources = {value.key.concept: value for value in self.source_values}
        if set(sources) != set(VALUATION_TTM_CONCEPTS):
            raise ValueError("source_values must cover the exact valuation TTM concept set.")
        if any(
            value.period_start != self.period_start
            or value.period_end != self.period_end
            or value.quarters[-1].fiscal_year != self.fiscal_year
            or value.quarters[-1].fiscal_quarter != self.fiscal_quarter
            for value in self.source_values
        ):
            raise ValueError("source_values must match the trailing period's exact boundaries.")
        expected_values = {
            "revenue": self.revenue,
            "operating_income": self.operating_income,
            "operating_cash_flow": self.operating_cash_flow,
            "capital_expenditures": self.capital_expenditures,
        }
        if any(sources[concept].value != value for concept, value in expected_values.items()):
            raise ValueError("source_values must equal the trailing period's reported values.")

    @property
    def operating_margin(self) -> Decimal:
        return self.operating_income / self.revenue


@dataclass(frozen=True)
class ComparableTrailingRevenueGrowth:
    """Revenue growth between TTM periods ending exactly four fiscal quarters apart."""

    prior: ValuationTrailingPeriod
    current: ValuationTrailingPeriod
    rate: Decimal

    def __post_init__(self) -> None:
        if not isinstance(self.prior, ValuationTrailingPeriod) or not isinstance(
            self.current, ValuationTrailingPeriod
        ):
            raise ValueError("Comparable growth periods must be ValuationTrailingPeriod values.")
        if _quarter_ordinal(self.current) - _quarter_ordinal(self.prior) != 4:
            raise ValueError("Comparable TTM periods must end exactly four fiscal quarters apart.")
        if not isinstance(self.rate, Decimal) or not self.rate.is_finite():
            raise ValueError("rate must be a finite Decimal.")
        if self.rate != self.current.revenue / self.prior.revenue - Decimal("1"):
            raise ValueError("rate must equal exact year-over-year TTM revenue growth.")


@dataclass(frozen=True)
class ValuationBalancePosition:
    """SEC balance facts at the end of the snapshot's latest TTM period."""

    period_end: date
    cash_and_cash_equivalents: Decimal
    current_debt: Decimal
    long_term_debt: Decimal
    source_facts: Tuple[FinancialFact, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.period_end, date) or isinstance(self.period_end, datetime):
            raise ValueError("period_end must be a date.")
        for field_name in (
            "cash_and_cash_equivalents",
            "current_debt",
            "long_term_debt",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, Decimal) or not value.is_finite() or value < 0:
                raise ValueError(f"{field_name} must be a finite, non-negative Decimal.")
        object.__setattr__(self, "source_facts", tuple(self.source_facts))
        if len(self.source_facts) != len(VALUATION_BALANCE_CONCEPTS) or any(
            not isinstance(fact, FinancialFact) for fact in self.source_facts
        ):
            raise ValueError("source_facts must contain one fact per required balance concept.")
        sources = {fact.identity.concept: fact for fact in self.source_facts}
        if set(sources) != set(VALUATION_BALANCE_CONCEPTS):
            raise ValueError("source_facts must cover the exact valuation balance concept set.")
        expected_values = {
            "cash_and_cash_equivalents": self.cash_and_cash_equivalents,
            "current_debt": self.current_debt,
            "long_term_debt": self.long_term_debt,
        }
        if any(
            sources[concept].period.period_end != self.period_end
            or sources[concept].value != value
            for concept, value in expected_values.items()
        ):
            raise ValueError("source_facts must match the balance position's date and values.")

    @property
    def reported_term_debt(self) -> Decimal:
        """Current plus noncurrent term debt; not a claim of total indebtedness."""

        return self.current_debt + self.long_term_debt


@dataclass(frozen=True)
class ValuationFundamentalsSnapshot:
    request: ValuationFundamentalsRequest
    trailing_periods: Tuple[ValuationTrailingPeriod, ...]
    comparable_revenue_growth: Tuple[ComparableTrailingRevenueGrowth, ...]
    latest_balance: ValuationBalancePosition

    def __post_init__(self) -> None:
        if not isinstance(self.request, ValuationFundamentalsRequest):
            raise ValueError("request must be a ValuationFundamentalsRequest.")
        object.__setattr__(self, "trailing_periods", tuple(self.trailing_periods))
        object.__setattr__(
            self, "comparable_revenue_growth", tuple(self.comparable_revenue_growth)
        )
        if len(self.trailing_periods) < 5 or any(
            not isinstance(period, ValuationTrailingPeriod) for period in self.trailing_periods
        ):
            raise ValueError("A valuation snapshot requires at least five TTM periods.")
        if tuple(sorted(self.trailing_periods, key=_quarter_ordinal)) != self.trailing_periods:
            raise ValueError("TTM periods must be ordered chronologically.")
        if any(
            _quarter_ordinal(right) - _quarter_ordinal(left) != 1
            for left, right in zip(self.trailing_periods, self.trailing_periods[1:])
        ):
            raise ValueError("TTM periods must advance by one fiscal quarter.")
        if not self.comparable_revenue_growth or any(
            not isinstance(item, ComparableTrailingRevenueGrowth)
            for item in self.comparable_revenue_growth
        ):
            raise ValueError("A valuation snapshot requires comparable TTM revenue growth.")
        expected = tuple(
            ComparableTrailingRevenueGrowth(
                prior=self.trailing_periods[index - 4],
                current=self.trailing_periods[index],
                rate=(
                    self.trailing_periods[index].revenue
                    / self.trailing_periods[index - 4].revenue
                    - Decimal("1")
                ),
            )
            for index in range(4, len(self.trailing_periods))
        )
        if self.comparable_revenue_growth != expected:
            raise ValueError("Comparable growth must contain every exact four-quarter comparison.")
        if not isinstance(self.latest_balance, ValuationBalancePosition):
            raise ValueError("latest_balance must be a ValuationBalancePosition.")
        if self.latest_balance.period_end != self.trailing_periods[-1].period_end:
            raise ValueError("The balance position must match the latest TTM period end.")
        source_facts = tuple(
            fact
            for period in self.trailing_periods
            for value in period.source_values
            for quarter in value.quarters
            for fact in quarter.source_facts
        ) + self.latest_balance.source_facts
        if any(fact.identity.context.entity_cik != self.request.cik for fact in source_facts):
            raise ValueError("Every snapshot source fact must belong to the requested issuer.")
        if any(
            fact.provenance.eligible_at > self.request.knowledge_cutoff
            or fact.lineage.ingested_at > self.request.data_vintage_cutoff
            or fact.lineage.source_adapter != self.request.source_adapter
            or fact.lineage.concept_map_version != self.request.concept_map_version
            or fact.lineage.fiscal_calendar_version != self.request.fiscal_calendar_version
            for fact in source_facts
        ):
            raise ValueError("Every snapshot source fact must match the requested cutoffs and lineage.")

    @property
    def latest(self) -> ValuationTrailingPeriod:
        return self.trailing_periods[-1]


class ValuationSnapshotIssueCode(str, Enum):
    STORE_UNAVAILABLE = "store_unavailable"
    NO_ELIGIBLE_FACTS = "no_eligible_facts"
    INCONSISTENT_FACTS = "inconsistent_facts"
    QUARTERLY_ASSEMBLY_FAILED = "quarterly_assembly_failed"
    INCOMPATIBLE_TRAILING_PERIODS = "incompatible_trailing_periods"
    INSUFFICIENT_COMPARABLE_HISTORY = "insufficient_comparable_history"
    MISSING_BALANCE_POSITION = "missing_balance_position"


@dataclass(frozen=True)
class ValuationSnapshotIssue:
    code: ValuationSnapshotIssueCode
    message: str

    def __post_init__(self) -> None:
        if not isinstance(self.code, ValuationSnapshotIssueCode):
            raise ValueError("code must be a ValuationSnapshotIssueCode.")
        if not isinstance(self.message, str) or not self.message.strip():
            raise ValueError("message must be non-empty text.")


@dataclass(frozen=True)
class ValuationSnapshotResult:
    request: ValuationFundamentalsRequest
    snapshot: Optional[ValuationFundamentalsSnapshot] = None
    issues: Tuple[ValuationSnapshotIssue, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if not isinstance(self.request, ValuationFundamentalsRequest):
            raise ValueError("request must be a ValuationFundamentalsRequest.")
        object.__setattr__(self, "issues", tuple(self.issues))
        if any(not isinstance(issue, ValuationSnapshotIssue) for issue in self.issues):
            raise ValueError("issues must contain only ValuationSnapshotIssue values.")
        if (self.snapshot is None) == (not self.issues):
            raise ValueError("A result must contain one complete snapshot or refusal issues.")
        if self.snapshot is not None:
            if not isinstance(self.snapshot, ValuationFundamentalsSnapshot):
                raise ValueError("snapshot must be a ValuationFundamentalsSnapshot.")
            if self.snapshot.request != self.request:
                raise ValueError("The snapshot must preserve the exact request.")

    @property
    def is_complete(self) -> bool:
        return self.snapshot is not None


def _refusal(
    request: ValuationFundamentalsRequest,
    code: ValuationSnapshotIssueCode,
    message: str,
) -> ValuationSnapshotResult:
    return ValuationSnapshotResult(
        request=request,
        issues=(ValuationSnapshotIssue(code=code, message=message),),
    )


def _repository_query(request: ValuationFundamentalsRequest) -> FundamentalsQuery:
    return FundamentalsQuery(
        cik=request.cik,
        knowledge_cutoff=request.knowledge_cutoff,
        data_vintage_cutoff=request.data_vintage_cutoff,
        concepts=VALUATION_FUNDAMENTALS_CONCEPTS,
        source_adapter=request.source_adapter,
        concept_map_version=request.concept_map_version,
        fiscal_calendar_version=request.fiscal_calendar_version,
        max_periods_per_statement=_MAX_REPOSITORY_PERIODS_PER_STATEMENT,
    )


def _quarter_ordinal(period: ValuationTrailingPeriod) -> int:
    return period.fiscal_year * 4 + period.fiscal_quarter - 1


def _ttm_by_concept_and_end(
    quarterly: QuarterlyFundamentals,
) -> Dict[Tuple[str, date], TrailingTwelveMonthValue]:
    index: Dict[Tuple[str, date], TrailingTwelveMonthValue] = {}
    for value in quarterly.ttm_values:
        key = (value.key.concept, value.period_end)
        if key in index:
            raise ValueError("A concept has more than one TTM series at the same period end.")
        if (
            value.key.unit != "USD"
            or value.key.currency != "USD"
            or value.key.context.dimensions
        ):
            raise ValueError("Valuation TTM periods require consolidated USD facts.")
        index[key] = value
    return index


def _assemble_trailing_periods(
    quarterly: QuarterlyFundamentals,
) -> Tuple[ValuationTrailingPeriod, ...]:
    index = _ttm_by_concept_and_end(quarterly)
    period_ends_by_concept = {
        concept: {period_end for indexed_concept, period_end in index if indexed_concept == concept}
        for concept in VALUATION_TTM_CONCEPTS
    }
    common_ends = set.intersection(*(ends for ends in period_ends_by_concept.values()))
    result = []
    for period_end in sorted(common_ends):
        values = {concept: index[(concept, period_end)] for concept in VALUATION_TTM_CONCEPTS}
        period_starts = {value.period_start for value in values.values()}
        ending_quarters = {
            (value.quarters[-1].fiscal_year, value.quarters[-1].fiscal_quarter)
            for value in values.values()
        }
        if len(period_starts) != 1 or len(ending_quarters) != 1:
            raise ValueError("Aligned valuation TTM concepts must share exact fiscal boundaries.")
        fiscal_year, fiscal_quarter = next(iter(ending_quarters))
        operating_cash_flow = values["operating_cash_flow"].value
        capital_expenditures = values["capital_expenditures"].value
        result.append(
            ValuationTrailingPeriod(
                fiscal_year=fiscal_year,
                fiscal_quarter=fiscal_quarter,
                period_start=next(iter(period_starts)),
                period_end=period_end,
                revenue=values["revenue"].value,
                operating_income=values["operating_income"].value,
                operating_cash_flow=operating_cash_flow,
                capital_expenditures=capital_expenditures,
                free_cash_flow=operating_cash_flow - capital_expenditures,
                source_values=tuple(values[concept] for concept in VALUATION_TTM_CONCEPTS),
            )
        )
    return tuple(result)


def _balance_fact(
    history: FundamentalHistory,
    *,
    concept: str,
    period_end: date,
) -> Optional[FinancialFact]:
    matches = tuple(
        fact
        for period in history.balance_sheet_periods
        for fact in period.facts
        if fact.identity.concept == concept
        and fact.period.period_end == period_end
        and fact.identity.unit == "USD"
        and fact.identity.currency == "USD"
        and not fact.identity.context.dimensions
    )
    return matches[0] if len(matches) == 1 else None


def _latest_balance(
    history: FundamentalHistory,
    period_end: date,
) -> Optional[ValuationBalancePosition]:
    facts = {
        concept: _balance_fact(history, concept=concept, period_end=period_end)
        for concept in VALUATION_BALANCE_CONCEPTS
    }
    if any(fact is None for fact in facts.values()):
        return None
    return ValuationBalancePosition(
        period_end=period_end,
        cash_and_cash_equivalents=facts["cash_and_cash_equivalents"].value,
        current_debt=facts["current_debt"].value,
        long_term_debt=facts["long_term_debt"].value,
        source_facts=tuple(facts[concept] for concept in VALUATION_BALANCE_CONCEPTS),
    )


def load_valuation_fundamentals_snapshot(
    repository: FundamentalsRepository,
    request: ValuationFundamentalsRequest,
) -> ValuationSnapshotResult:
    """Load, select, assemble, and validate one SEC fundamentals snapshot."""

    if not isinstance(request, ValuationFundamentalsRequest):
        raise ValueError("request must be a ValuationFundamentalsRequest.")
    if not hasattr(repository, "get_facts") or not callable(repository.get_facts):
        raise ValueError("repository must provide get_facts(query).")

    try:
        candidates = tuple(repository.get_facts(_repository_query(request)))
    except FundamentalsRepositoryUnavailable:
        return _refusal(
            request,
            ValuationSnapshotIssueCode.STORE_UNAVAILABLE,
            "The point-in-time fundamentals store is unavailable.",
        )
    if not candidates:
        return _refusal(
            request,
            ValuationSnapshotIssueCode.NO_ELIGIBLE_FACTS,
            "No SEC fundamentals are eligible for the requested cutoffs and lineage.",
        )

    try:
        history = select_point_in_time(
            candidates,
            request.knowledge_cutoff,
            cik=request.cik,
        )
    except ValueError:
        return _refusal(
            request,
            ValuationSnapshotIssueCode.INCONSISTENT_FACTS,
            "Eligible SEC fundamentals are structurally inconsistent.",
        )

    quarterly = assemble_quarterly_fundamentals(
        history,
        required_concepts=VALUATION_TTM_CONCEPTS,
    )
    if not quarterly.is_complete:
        return _refusal(
            request,
            ValuationSnapshotIssueCode.QUARTERLY_ASSEMBLY_FAILED,
            "Eligible SEC fundamentals cannot form complete standalone-quarter series.",
        )

    try:
        trailing_periods = _assemble_trailing_periods(quarterly)
    except ValueError:
        return _refusal(
            request,
            ValuationSnapshotIssueCode.INCOMPATIBLE_TRAILING_PERIODS,
            "SEC concepts do not share one exact consolidated USD TTM basis.",
        )
    if len(trailing_periods) < 5:
        return _refusal(
            request,
            ValuationSnapshotIssueCode.INSUFFICIENT_COMPARABLE_HISTORY,
            "At least eight consecutive quarters are required for comparable TTM growth.",
        )
    if any(
        _quarter_ordinal(right) - _quarter_ordinal(left) != 1
        for left, right in zip(trailing_periods, trailing_periods[1:])
    ):
        return _refusal(
            request,
            ValuationSnapshotIssueCode.INCOMPATIBLE_TRAILING_PERIODS,
            "Aligned SEC TTM periods do not form one consecutive fiscal-quarter history.",
        )

    comparisons = tuple(
        ComparableTrailingRevenueGrowth(
            prior=trailing_periods[index - 4],
            current=trailing_periods[index],
            rate=(
                trailing_periods[index].revenue
                / trailing_periods[index - 4].revenue
                - Decimal("1")
            ),
        )
        for index in range(4, len(trailing_periods))
    )
    latest_balance = _latest_balance(history, trailing_periods[-1].period_end)
    if latest_balance is None:
        return _refusal(
            request,
            ValuationSnapshotIssueCode.MISSING_BALANCE_POSITION,
            "The latest TTM end lacks one unambiguous consolidated USD cash or term-debt balance.",
        )

    return ValuationSnapshotResult(
        request=request,
        snapshot=ValuationFundamentalsSnapshot(
            request=request,
            trailing_periods=trailing_periods,
            comparable_revenue_growth=comparisons,
            latest_balance=latest_balance,
        ),
    )
