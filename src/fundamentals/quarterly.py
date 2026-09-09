"""Pure assembly of point-in-time standalone quarters and trailing values.

The public interface consumes an already-resolved ``FundamentalHistory``. It
does no I/O, performs no fact selection, and never treats year-to-date values
as standalone quarters. Q4 is synthetic and exists only when exact arithmetic
can reconcile a full year with reported Q1, Q2, and Q3 values.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from enum import Enum
from typing import Dict, Iterable, List, Optional, Tuple

from .time_policy import is_aware
from .types import FactContext, FinancialFact, FundamentalHistory, StatementKind, normalize_cik


class QuarterValueOrigin(str, Enum):
    REPORTED = "reported"
    DERIVED_YTD_DIFFERENCE = "derived_ytd_difference"
    DERIVED_Q4 = "derived_q4"


@dataclass(frozen=True)
class QuarterSeriesKey:
    """The non-period identity shared by values in one arithmetic series."""

    statement_kind: StatementKind
    concept: str
    unit: str
    currency: Optional[str]
    context: FactContext

    def __post_init__(self) -> None:
        if self.statement_kind not in (
            StatementKind.INCOME_STATEMENT,
            StatementKind.CASH_FLOW,
        ):
            raise ValueError("Quarter series must belong to a duration statement.")
        for field_name in ("concept", "unit"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"QuarterSeriesKey.{field_name} must be non-empty text.")
        if self.currency is not None and (
            not isinstance(self.currency, str) or not self.currency.strip()
        ):
            raise ValueError("QuarterSeriesKey.currency must be non-empty text when present.")
        if not isinstance(self.context, FactContext):
            raise ValueError("QuarterSeriesKey.context must be a FactContext.")


@dataclass(frozen=True)
class StandaloneQuarterValue:
    key: QuarterSeriesKey
    fiscal_year: int
    fiscal_quarter: int
    period_start: date
    period_end: date
    value: Decimal
    origin: QuarterValueOrigin
    source_facts: Tuple[FinancialFact, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.key, QuarterSeriesKey):
            raise ValueError("A standalone quarter key must be a QuarterSeriesKey.")
        if (
            isinstance(self.fiscal_year, bool)
            or not isinstance(self.fiscal_year, int)
            or self.fiscal_year <= 0
        ):
            raise ValueError("fiscal_year must be a positive integer.")
        if (
            isinstance(self.fiscal_quarter, bool)
            or not isinstance(self.fiscal_quarter, int)
            or self.fiscal_quarter not in (1, 2, 3, 4)
        ):
            raise ValueError("fiscal_quarter must be from 1 through 4.")
        if not _is_plain_date(self.period_start) or not _is_plain_date(self.period_end):
            raise ValueError("Standalone-quarter boundaries must be dates.")
        if self.period_start > self.period_end:
            raise ValueError("A standalone quarter cannot start after it ends.")
        if not isinstance(self.value, Decimal) or not self.value.is_finite():
            raise ValueError("A standalone quarter value must be a finite Decimal.")
        if not isinstance(self.origin, QuarterValueOrigin):
            raise ValueError("A standalone quarter origin must be a QuarterValueOrigin.")
        object.__setattr__(self, "source_facts", tuple(self.source_facts))
        if not self.source_facts or any(
            not isinstance(fact, FinancialFact) for fact in self.source_facts
        ):
            raise ValueError("A standalone quarter must preserve FinancialFact sources.")
        if any(
            fact.statement_kind is not self.key.statement_kind
            or fact.identity.concept != self.key.concept
            or fact.identity.unit != self.key.unit
            or fact.identity.currency != self.key.currency
            or fact.identity.context != self.key.context
            or fact.period.fiscal_year != self.fiscal_year
            for fact in self.source_facts
        ):
            raise ValueError("Standalone-quarter sources must match its series identity.")
        if self.origin is QuarterValueOrigin.REPORTED:
            source = self.source_facts[0]
            if len(self.source_facts) != 1 or (
                source.period.fiscal_period != f"Q{self.fiscal_quarter}"
                or source.period.period_start != self.period_start
                or source.period.period_end != self.period_end
                or source.value != self.value
            ):
                raise ValueError("A reported quarter must exactly match its single source fact.")


@dataclass(frozen=True)
class TrailingTwelveMonthValue:
    key: QuarterSeriesKey
    period_start: date
    period_end: date
    value: Decimal
    quarters: Tuple[StandaloneQuarterValue, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.key, QuarterSeriesKey):
            raise ValueError("A TTM key must be a QuarterSeriesKey.")
        object.__setattr__(self, "quarters", tuple(self.quarters))
        if len(self.quarters) != 4 or any(
            not isinstance(quarter, StandaloneQuarterValue) for quarter in self.quarters
        ):
            raise ValueError("A TTM value must contain exactly four standalone quarters.")
        if not isinstance(self.value, Decimal) or not self.value.is_finite():
            raise ValueError("A TTM value must be a finite Decimal.")
        if any(quarter.key != self.key for quarter in self.quarters):
            raise ValueError("Every TTM quarter must share its series key.")
        if not all(
            _expected_next(left, right)
            for left, right in zip(self.quarters, self.quarters[1:])
        ):
            raise ValueError("TTM quarters must be consecutive.")
        if self.period_start != self.quarters[0].period_start or self.period_end != self.quarters[-1].period_end:
            raise ValueError("TTM boundaries must match its first and last quarter.")
        if self.value != sum((quarter.value for quarter in self.quarters), Decimal("0")):
            raise ValueError("A TTM value must equal the exact sum of its quarters.")


class QuarterlyAssemblyIssueCode(str, Enum):
    INVALID_REQUIRED_CONCEPTS = "invalid_required_concepts"
    CONFLICTING_FACTS = "conflicting_facts"
    UNSUPPORTED_SERIES = "unsupported_series"
    DIMENSIONAL_CONTEXT = "dimensional_context"
    AMBIGUOUS_SERIES = "ambiguous_series"
    INCOMPATIBLE_LINEAGE = "incompatible_lineage"
    MISSING_PERIOD = "missing_period"
    CONTRADICTORY_PERIOD = "contradictory_period"


@dataclass(frozen=True)
class QuarterlyAssemblyIssue:
    code: QuarterlyAssemblyIssueCode
    message: str
    concept: Optional[str] = None
    fiscal_year: Optional[int] = None

    def __post_init__(self) -> None:
        if not isinstance(self.code, QuarterlyAssemblyIssueCode):
            raise ValueError("QuarterlyAssemblyIssue.code must be a QuarterlyAssemblyIssueCode.")
        if not isinstance(self.message, str) or not self.message.strip():
            raise ValueError("QuarterlyAssemblyIssue.message must be non-empty text.")


@dataclass(frozen=True)
class QuarterlyFundamentals:
    cik: str
    knowledge_cutoff: datetime
    quarters: Tuple[StandaloneQuarterValue, ...] = field(default_factory=tuple)
    ttm_values: Tuple[TrailingTwelveMonthValue, ...] = field(default_factory=tuple)
    issues: Tuple[QuarterlyAssemblyIssue, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        object.__setattr__(self, "cik", normalize_cik(self.cik))
        if not isinstance(self.knowledge_cutoff, datetime) or not is_aware(
            self.knowledge_cutoff
        ):
            raise ValueError("knowledge_cutoff must be timezone-aware.")
        object.__setattr__(self, "quarters", tuple(self.quarters))
        object.__setattr__(self, "ttm_values", tuple(self.ttm_values))
        object.__setattr__(self, "issues", tuple(self.issues))
        if any(not isinstance(quarter, StandaloneQuarterValue) for quarter in self.quarters):
            raise ValueError("quarters must contain only StandaloneQuarterValue values.")
        if any(not isinstance(value, TrailingTwelveMonthValue) for value in self.ttm_values):
            raise ValueError("ttm_values must contain only TrailingTwelveMonthValue values.")
        if any(not isinstance(issue, QuarterlyAssemblyIssue) for issue in self.issues):
            raise ValueError("issues must contain only QuarterlyAssemblyIssue values.")
        if any(quarter.key.context.entity_cik != self.cik for quarter in self.quarters):
            raise ValueError("Every standalone quarter must belong to the result CIK.")
        if any(
            fact.provenance.eligible_at > self.knowledge_cutoff
            for quarter in self.quarters
            for fact in quarter.source_facts
        ):
            raise ValueError("Standalone-quarter sources must be public by the knowledge cutoff.")
        if bool(self.quarters) == bool(self.issues):
            raise ValueError("Quarterly fundamentals must contain quarters or refusal issues.")
        if self.issues and self.ttm_values:
            raise ValueError("Refused quarterly fundamentals cannot contain TTM values.")

    @property
    def is_complete(self) -> bool:
        return bool(self.quarters)


def _refusal(
    history: FundamentalHistory,
    code: QuarterlyAssemblyIssueCode,
    message: str,
    *,
    concept: Optional[str] = None,
    fiscal_year: Optional[int] = None,
) -> QuarterlyFundamentals:
    return QuarterlyFundamentals(
        cik=history.cik,
        knowledge_cutoff=history.knowledge_cutoff,
        issues=(QuarterlyAssemblyIssue(code, message, concept, fiscal_year),),
    )


def _is_plain_date(value: object) -> bool:
    return isinstance(value, date) and not isinstance(value, datetime)


def _normalize_required_concepts(required_concepts: Iterable[str]) -> Tuple[str, ...]:
    if isinstance(required_concepts, (str, bytes)):
        raise ValueError("required_concepts must be a collection of concept names.")
    try:
        concepts = tuple(required_concepts)
    except TypeError:
        raise ValueError("required_concepts must be a collection of concept names.") from None
    if not concepts or any(not isinstance(item, str) or not item.strip() for item in concepts):
        raise ValueError("required_concepts must contain non-empty concept names.")
    if len(concepts) != len(set(concepts)):
        raise ValueError("required_concepts must not contain duplicates.")
    return tuple(sorted(concepts))


def _normalize_optional_concepts(optional_concepts: Iterable[str]) -> Tuple[str, ...]:
    if isinstance(optional_concepts, (str, bytes)):
        raise ValueError("optional_concepts must be a collection of concept names.")
    try:
        concepts = tuple(optional_concepts)
    except TypeError:
        raise ValueError("optional_concepts must be a collection of concept names.") from None
    if any(not isinstance(item, str) or not item.strip() for item in concepts):
        raise ValueError("optional_concepts must contain only non-empty concept names.")
    if len(concepts) != len(set(concepts)):
        raise ValueError("optional_concepts must not contain duplicates.")
    return tuple(sorted(concepts))


def _series_key(fact: FinancialFact) -> QuarterSeriesKey:
    return QuarterSeriesKey(
        statement_kind=fact.statement_kind,
        concept=fact.identity.concept,
        unit=fact.identity.unit,
        currency=fact.identity.currency,
        context=fact.identity.context,
    )


def _quarter_sort_key(value: StandaloneQuarterValue) -> Tuple[object, ...]:
    return (
        value.key.statement_kind.value,
        value.key.concept,
        value.key.unit,
        value.key.currency or "",
        value.fiscal_year,
        value.fiscal_quarter,
        value.period_end,
    )


def _reported_quarter(fact: FinancialFact, quarter: int) -> StandaloneQuarterValue:
    if fact.period.period_start is None:
        raise ValueError("A reported standalone quarter must be a duration fact.")
    return StandaloneQuarterValue(
        key=_series_key(fact),
        fiscal_year=fact.period.fiscal_year,
        fiscal_quarter=quarter,
        period_start=fact.period.period_start,
        period_end=fact.period.period_end,
        value=fact.value,
        origin=QuarterValueOrigin.REPORTED,
        source_facts=(fact,),
    )


def _derived_quarter(
    *,
    key: QuarterSeriesKey,
    fiscal_year: int,
    fiscal_quarter: int,
    period_start: date,
    period_end: date,
    value: Decimal,
    origin: QuarterValueOrigin,
    source_facts: Tuple[FinancialFact, ...],
) -> StandaloneQuarterValue:
    return StandaloneQuarterValue(
        key=key,
        fiscal_year=fiscal_year,
        fiscal_quarter=fiscal_quarter,
        period_start=period_start,
        period_end=period_end,
        value=value,
        origin=origin,
        source_facts=source_facts,
    )


def _same_quarter(
    reported: StandaloneQuarterValue,
    derived: StandaloneQuarterValue,
) -> bool:
    return (
        reported.period_start == derived.period_start
        and reported.period_end == derived.period_end
        and reported.value == derived.value
    )


def _expected_next(left: StandaloneQuarterValue, right: StandaloneQuarterValue) -> bool:
    if right.period_start != left.period_end + timedelta(days=1):
        return False
    if left.fiscal_quarter < 4:
        return (
            right.fiscal_year == left.fiscal_year
            and right.fiscal_quarter == left.fiscal_quarter + 1
        )
    return right.fiscal_year == left.fiscal_year + 1 and right.fiscal_quarter == 1


def _ttm_windows(
    key: QuarterSeriesKey,
    quarters: Tuple[StandaloneQuarterValue, ...],
) -> Tuple[TrailingTwelveMonthValue, ...]:
    chronological = tuple(sorted(quarters, key=lambda item: item.period_end))
    windows = []
    for index in range(3, len(chronological)):
        window = chronological[index - 3 : index + 1]
        if not all(_expected_next(left, right) for left, right in zip(window, window[1:])):
            continue
        windows.append(
            TrailingTwelveMonthValue(
                key=key,
                period_start=window[0].period_start,
                period_end=window[-1].period_end,
                value=sum((quarter.value for quarter in window), Decimal("0")),
                quarters=window,
            )
        )
    return tuple(windows)


def _all_statement_facts(history: FundamentalHistory) -> Tuple[FinancialFact, ...]:
    periods = (
        history.income_statement_periods
        + history.balance_sheet_periods
        + history.cash_flow_periods
    )
    return tuple(fact for period in periods for fact in period.facts)


def assemble_quarterly_fundamentals(
    history: FundamentalHistory,
    *,
    required_concepts: Iterable[str],
    optional_concepts: Iterable[str] = (),
) -> QuarterlyFundamentals:
    """Build exact standalone-quarter series or return one deterministic refusal."""

    if not isinstance(history, FundamentalHistory):
        raise ValueError("history must be a FundamentalHistory.")
    try:
        required = _normalize_required_concepts(required_concepts)
        optional = _normalize_optional_concepts(optional_concepts)
        if set(required) & set(optional):
            raise ValueError("required_concepts and optional_concepts must not overlap.")
    except ValueError as error:
        return _refusal(
            history,
            QuarterlyAssemblyIssueCode.INVALID_REQUIRED_CONCEPTS,
            str(error),
        )

    all_facts = _all_statement_facts(history)
    available_concepts = {fact.identity.concept for fact in all_facts}
    concepts = tuple(sorted(required + tuple(
        concept for concept in optional if concept in available_concepts
    )))

    conflicted_concepts = {
        conflict.identity.concept
        for conflict in history.conflicts
        if conflict.identity.concept in concepts
    }
    if conflicted_concepts:
        concept = sorted(conflicted_concepts)[0]
        return _refusal(
            history,
            QuarterlyAssemblyIssueCode.CONFLICTING_FACTS,
            "A required concept has unresolved contradictory facts at the cutoff.",
            concept=concept,
        )

    assembled: List[StandaloneQuarterValue] = []
    by_key: Dict[QuarterSeriesKey, List[FinancialFact]] = {}

    for concept in concepts:
        concept_facts = [fact for fact in all_facts if fact.identity.concept == concept]
        if not concept_facts:
            return _refusal(
                history,
                QuarterlyAssemblyIssueCode.MISSING_PERIOD,
                "A required concept has no eligible duration facts.",
                concept=concept,
            )
        if any(fact.identity.context.dimensions for fact in concept_facts):
            return _refusal(
                history,
                QuarterlyAssemblyIssueCode.DIMENSIONAL_CONTEXT,
                "Quarter assembly supports consolidated facts only.",
                concept=concept,
            )
        if any(
            fact.statement_kind
            not in (StatementKind.INCOME_STATEMENT, StatementKind.CASH_FLOW)
            for fact in concept_facts
        ):
            return _refusal(
                history,
                QuarterlyAssemblyIssueCode.UNSUPPORTED_SERIES,
                "Quarter assembly supports income-statement and cash-flow duration facts only.",
                concept=concept,
            )
        keys = {_series_key(fact) for fact in concept_facts}
        if len(keys) != 1:
            return _refusal(
                history,
                QuarterlyAssemblyIssueCode.AMBIGUOUS_SERIES,
                "A required concept resolves to more than one unit, currency, context, or statement.",
                concept=concept,
            )
        key = next(iter(keys))
        by_key[key] = concept_facts

    for key, facts in sorted(
        by_key.items(),
        key=lambda item: (
            item[0].statement_kind.value,
            item[0].concept,
            item[0].unit,
            item[0].currency or "",
        ),
    ):
        lineage_versions = {
            (
                fact.lineage.source_adapter,
                fact.lineage.concept_map_version,
                fact.lineage.fiscal_calendar_version,
            )
            for fact in facts
        }
        if len(lineage_versions) != 1:
            return _refusal(
                history,
                QuarterlyAssemblyIssueCode.INCOMPATIBLE_LINEAGE,
                "Facts used in one arithmetic series have incompatible mapping or calendar lineage.",
                concept=key.concept,
            )

        relevant = [
            fact
            for fact in facts
            if (
                fact.period.periodicity == "quarterly"
                and fact.period.fiscal_period in ("Q1", "Q2", "Q3", "Q4")
            )
            or (
                fact.period.periodicity == "ytd"
                and fact.period.fiscal_period in ("Q2YTD", "Q3YTD")
            )
            or (fact.period.periodicity == "annual" and fact.period.fiscal_period == "FY")
        ]
        if not relevant:
            return _refusal(
                history,
                QuarterlyAssemblyIssueCode.MISSING_PERIOD,
                "A required concept has no eligible standalone-quarter or full-year facts.",
                concept=key.concept,
            )

        by_year: Dict[int, Dict[str, List[FinancialFact]]] = {}
        for fact in relevant:
            by_year.setdefault(fact.period.fiscal_year, {}).setdefault(
                fact.period.fiscal_period, []
            ).append(fact)

        quarter_years = sorted(
            fiscal_year
            for fiscal_year, periods in by_year.items()
            if any(
                label in periods
                for label in ("Q1", "Q2", "Q2YTD", "Q3", "Q3YTD", "Q4")
            )
        )
        if not quarter_years:
            return _refusal(
                history,
                QuarterlyAssemblyIssueCode.MISSING_PERIOD,
                "A required concept has no eligible reported standalone quarters.",
                concept=key.concept,
            )
        first_quarter_year = quarter_years[0]
        by_year = {
            fiscal_year: periods
            for fiscal_year, periods in by_year.items()
            if fiscal_year >= first_quarter_year
        }
        ordered_years = sorted(by_year)
        if ordered_years != list(range(ordered_years[0], ordered_years[-1] + 1)):
            return _refusal(
                history,
                QuarterlyAssemblyIssueCode.MISSING_PERIOD,
                "Quarterly coverage contains a missing fiscal year.",
                concept=key.concept,
            )

        key_quarters: List[StandaloneQuarterValue] = []
        for fiscal_year, periods in sorted(by_year.items()):
            duplicates = [label for label, values in periods.items() if len(values) != 1]
            if duplicates:
                return _refusal(
                    history,
                    QuarterlyAssemblyIssueCode.CONTRADICTORY_PERIOD,
                    "A fiscal period has more than one selected fact in the same series.",
                    concept=key.concept,
                    fiscal_year=fiscal_year,
                )

            selected = {label: values[0] for label, values in periods.items()}
            q1_fact = selected.get("Q1")
            if q1_fact is None:
                return _refusal(
                    history,
                    QuarterlyAssemblyIssueCode.MISSING_PERIOD,
                    "Quarterly assembly requires a reported Q1 before later quarters or YTD values.",
                    concept=key.concept,
                    fiscal_year=fiscal_year,
                )

            q1 = _reported_quarter(q1_fact, 1)
            resolved = [q1]

            q2_ytd = selected.get("Q2YTD")
            q2_reported = (
                _reported_quarter(selected["Q2"], 2) if "Q2" in selected else None
            )
            q2_derived = None
            if q2_ytd is not None:
                if (
                    q2_ytd.period.period_start != q1.period_start
                    or q2_ytd.period.period_end <= q1.period_end
                ):
                    return _refusal(
                        history,
                        QuarterlyAssemblyIssueCode.CONTRADICTORY_PERIOD,
                        "Six-month YTD geometry does not begin with Q1 and end after it.",
                        concept=key.concept,
                        fiscal_year=fiscal_year,
                    )
                q2_derived = _derived_quarter(
                    key=key,
                    fiscal_year=fiscal_year,
                    fiscal_quarter=2,
                    period_start=q1.period_end + timedelta(days=1),
                    period_end=q2_ytd.period.period_end,
                    value=q2_ytd.value - q1.value,
                    origin=QuarterValueOrigin.DERIVED_YTD_DIFFERENCE,
                    source_facts=(q2_ytd, q1_fact),
                )
            if q2_reported is not None and q2_derived is not None and not _same_quarter(
                q2_reported, q2_derived
            ):
                return _refusal(
                    history,
                    QuarterlyAssemblyIssueCode.CONTRADICTORY_PERIOD,
                    "Reported Q2 contradicts the exact six-month YTD reconciliation.",
                    concept=key.concept,
                    fiscal_year=fiscal_year,
                )
            q2 = q2_reported or q2_derived
            if q2 is not None:
                if not _expected_next(q1, q2):
                    return _refusal(
                        history,
                        QuarterlyAssemblyIssueCode.CONTRADICTORY_PERIOD,
                        "Q2 boundaries are not consecutive with Q1.",
                        concept=key.concept,
                        fiscal_year=fiscal_year,
                    )
                resolved.append(q2)

            q3_ytd = selected.get("Q3YTD")
            q3_reported = (
                _reported_quarter(selected["Q3"], 3) if "Q3" in selected else None
            )
            q3_derived = None
            if q3_ytd is not None:
                if q2 is None:
                    return _refusal(
                        history,
                        QuarterlyAssemblyIssueCode.MISSING_PERIOD,
                        "Nine-month YTD cannot be converted without Q1 and Q2 coverage.",
                        concept=key.concept,
                        fiscal_year=fiscal_year,
                    )
                if (
                    q3_ytd.period.period_start != q1.period_start
                    or q3_ytd.period.period_end <= q2.period_end
                ):
                    return _refusal(
                        history,
                        QuarterlyAssemblyIssueCode.CONTRADICTORY_PERIOD,
                        "Nine-month YTD geometry does not begin with Q1 and end after Q2.",
                        concept=key.concept,
                        fiscal_year=fiscal_year,
                    )
                prior_ytd_value = q1.value + q2.value
                prior_source_facts = q1.source_facts + q2.source_facts
                if q2_ytd is not None:
                    prior_ytd_value = q2_ytd.value
                    prior_source_facts = (q2_ytd,)
                q3_derived = _derived_quarter(
                    key=key,
                    fiscal_year=fiscal_year,
                    fiscal_quarter=3,
                    period_start=q2.period_end + timedelta(days=1),
                    period_end=q3_ytd.period.period_end,
                    value=q3_ytd.value - prior_ytd_value,
                    origin=QuarterValueOrigin.DERIVED_YTD_DIFFERENCE,
                    source_facts=(q3_ytd,) + prior_source_facts,
                )
            if q3_reported is not None and q3_derived is not None and not _same_quarter(
                q3_reported, q3_derived
            ):
                return _refusal(
                    history,
                    QuarterlyAssemblyIssueCode.CONTRADICTORY_PERIOD,
                    "Reported Q3 contradicts the exact nine-month YTD reconciliation.",
                    concept=key.concept,
                    fiscal_year=fiscal_year,
                )
            q3 = q3_reported or q3_derived
            if q3 is not None:
                if q2 is None:
                    return _refusal(
                        history,
                        QuarterlyAssemblyIssueCode.MISSING_PERIOD,
                        "Q3 cannot be assembled without contiguous Q1 and Q2 coverage.",
                        concept=key.concept,
                        fiscal_year=fiscal_year,
                    )
                if not _expected_next(q2, q3):
                    return _refusal(
                        history,
                        QuarterlyAssemblyIssueCode.CONTRADICTORY_PERIOD,
                        "Q3 boundaries are not consecutive with Q2.",
                        concept=key.concept,
                        fiscal_year=fiscal_year,
                    )
                resolved.append(q3)

            full_year = selected.get("FY")
            if full_year is None:
                if fiscal_year != ordered_years[-1]:
                    return _refusal(
                        history,
                        QuarterlyAssemblyIssueCode.MISSING_PERIOD,
                        "A completed historical fiscal year is missing its full-year fact.",
                        concept=key.concept,
                        fiscal_year=fiscal_year,
                    )
                if "Q4" in selected:
                    return _refusal(
                        history,
                        QuarterlyAssemblyIssueCode.MISSING_PERIOD,
                        "A standalone Q4 cannot be accepted without its full-year reconciliation fact.",
                        concept=key.concept,
                        fiscal_year=fiscal_year,
                    )
                if q2 is None and any(
                    label in selected for label in ("Q2YTD", "Q3", "Q3YTD")
                ):
                    return _refusal(
                        history,
                        QuarterlyAssemblyIssueCode.MISSING_PERIOD,
                        "Quarterly coverage must remain contiguous after Q1.",
                        concept=key.concept,
                        fiscal_year=fiscal_year,
                    )
                key_quarters.extend(resolved)
                continue

            if q2 is None or q3 is None:
                return _refusal(
                    history,
                    QuarterlyAssemblyIssueCode.MISSING_PERIOD,
                    "A full-year fact requires exact Q1, Q2, and Q3 coverage to derive Q4.",
                    concept=key.concept,
                    fiscal_year=fiscal_year,
                )
            if (
                full_year.period.period_start != q1.period_start
                or full_year.period.period_end <= q3.period_end
            ):
                return _refusal(
                    history,
                    QuarterlyAssemblyIssueCode.CONTRADICTORY_PERIOD,
                    "Full-year and reported-quarter boundaries do not reconcile.",
                    concept=key.concept,
                    fiscal_year=fiscal_year,
                )
            derived_q4 = _derived_quarter(
                key=key,
                fiscal_year=fiscal_year,
                fiscal_quarter=4,
                period_start=q3.period_end + timedelta(days=1),
                period_end=full_year.period.period_end,
                value=full_year.value - q1.value - q2.value - q3.value,
                origin=QuarterValueOrigin.DERIVED_Q4,
                source_facts=(full_year,) + tuple(
                    fact
                    for quarter in (q1, q2, q3)
                    for fact in quarter.source_facts
                ),
            )
            reported_q4 = selected.get("Q4")
            if reported_q4 is not None and (
                reported_q4.period.period_start != derived_q4.period_start
                or reported_q4.period.period_end != derived_q4.period_end
                or reported_q4.value != derived_q4.value
            ):
                return _refusal(
                    history,
                    QuarterlyAssemblyIssueCode.CONTRADICTORY_PERIOD,
                    "A reported Q4 contradicts the exact full-year reconciliation.",
                    concept=key.concept,
                    fiscal_year=fiscal_year,
                )
            key_quarters.extend((*resolved, derived_q4))

        ordered_key_quarters = tuple(sorted(key_quarters, key=_quarter_sort_key))
        assembled.extend(ordered_key_quarters)

    ordered = tuple(sorted(assembled, key=_quarter_sort_key))
    ttm_values = []
    for key in sorted(
        by_key,
        key=lambda item: (
            item.statement_kind.value,
            item.concept,
            item.unit,
            item.currency or "",
        ),
    ):
        key_quarters = tuple(quarter for quarter in ordered if quarter.key == key)
        ttm_values.extend(_ttm_windows(key, key_quarters))

    return QuarterlyFundamentals(
        cik=history.cik,
        knowledge_cutoff=history.knowledge_cutoff,
        quarters=ordered,
        ttm_values=tuple(ttm_values),
    )
