"""Pure SEC fact classification against an explicit issuer fiscal calendar.

The public interface converts extracted SEC source records into the existing
``FinancialFact`` domain type. It performs no I/O and never infers a calendar
from SEC filing labels, month/day heuristics, or duration-length tolerances.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from enum import Enum
from typing import Dict, Iterable, Optional, Tuple

from .adapters.sec_companyfacts import SecExtractedFact
from .concept_map import FactPeriodType
from .types import (
    FactContext,
    FactIdentity,
    FilingProvenance,
    FinancialFact,
    StatementKind,
    StatementPeriod,
    normalize_cik,
)


@dataclass(frozen=True)
class FiscalYearDefinition:
    """Exact period boundaries for one issuer fiscal year."""

    fiscal_year: int
    period_start: date
    quarter_ends: Tuple[date, date, date, date]

    def __post_init__(self) -> None:
        if isinstance(self.fiscal_year, bool) or not isinstance(self.fiscal_year, int):
            raise ValueError("FiscalYearDefinition.fiscal_year must be an integer.")
        if self.fiscal_year <= 0:
            raise ValueError("FiscalYearDefinition.fiscal_year must be positive.")
        if not _is_plain_date(self.period_start):
            raise ValueError("FiscalYearDefinition.period_start must be a date.")
        try:
            quarter_ends = tuple(self.quarter_ends)
        except TypeError:
            raise ValueError(
                "FiscalYearDefinition.quarter_ends must contain exactly four dates."
            ) from None
        if len(quarter_ends) != 4 or any(not _is_plain_date(value) for value in quarter_ends):
            raise ValueError(
                "FiscalYearDefinition.quarter_ends must contain exactly four dates."
            )
        if self.period_start > quarter_ends[0]:
            raise ValueError("A fiscal year must start on or before its first quarter end.")
        if any(left >= right for left, right in zip(quarter_ends, quarter_ends[1:])):
            raise ValueError("Fiscal quarter ends must be strictly increasing.")
        object.__setattr__(self, "quarter_ends", quarter_ends)

    @property
    def period_end(self) -> date:
        return self.quarter_ends[-1]


@dataclass(frozen=True)
class OpenFiscalYearDefinition:
    """Exact boundaries filed so far for an issuer's unfinished fiscal year.

    An open year deliberately contains only completed quarter ends. It can
    classify those filed quarters, but it cannot classify Q4 or an annual
    period until an official filing closes the year.
    """

    fiscal_year: int
    period_start: date
    quarter_ends: Tuple[date, ...]

    def __post_init__(self) -> None:
        if isinstance(self.fiscal_year, bool) or not isinstance(self.fiscal_year, int):
            raise ValueError("OpenFiscalYearDefinition.fiscal_year must be an integer.")
        if self.fiscal_year <= 0:
            raise ValueError("OpenFiscalYearDefinition.fiscal_year must be positive.")
        if not _is_plain_date(self.period_start):
            raise ValueError("OpenFiscalYearDefinition.period_start must be a date.")
        try:
            quarter_ends = tuple(self.quarter_ends)
        except TypeError:
            raise ValueError(
                "OpenFiscalYearDefinition.quarter_ends must contain one to three dates."
            ) from None
        if not 1 <= len(quarter_ends) <= 3 or any(
            not _is_plain_date(value) for value in quarter_ends
        ):
            raise ValueError(
                "OpenFiscalYearDefinition.quarter_ends must contain one to three dates."
            )
        if self.period_start > quarter_ends[0]:
            raise ValueError("An open fiscal year must start on or before its first quarter end.")
        if any(left >= right for left, right in zip(quarter_ends, quarter_ends[1:])):
            raise ValueError("Open fiscal quarter ends must be strictly increasing.")
        object.__setattr__(self, "quarter_ends", quarter_ends)

    @property
    def period_end(self) -> date:
        """Last completed quarter end; never an inferred fiscal-year end."""

        return self.quarter_ends[-1]


@dataclass(frozen=True)
class FiscalTransitionDefinition:
    """Exact stub-period geometry and the SEC transition form that reports it."""

    fiscal_year: int
    period_start: date
    period_end: date
    form_type: str

    def __post_init__(self) -> None:
        if isinstance(self.fiscal_year, bool) or not isinstance(self.fiscal_year, int):
            raise ValueError("FiscalTransitionDefinition.fiscal_year must be an integer.")
        if self.fiscal_year <= 0:
            raise ValueError("FiscalTransitionDefinition.fiscal_year must be positive.")
        if not _is_plain_date(self.period_start) or not _is_plain_date(self.period_end):
            raise ValueError("Fiscal transition boundaries must be dates.")
        if self.period_start > self.period_end:
            raise ValueError("A fiscal transition must start on or before its period end.")
        if self.form_type not in ("10-KT", "10-QT"):
            raise ValueError("FiscalTransitionDefinition.form_type must be '10-KT' or '10-QT'.")


@dataclass(frozen=True)
class IssuerFiscalCalendarPolicy:
    """Versioned exact fiscal calendars for one CIK."""

    cik: str
    version: str
    fiscal_years: Tuple[FiscalYearDefinition, ...]
    transitions: Tuple[FiscalTransitionDefinition, ...] = ()
    open_fiscal_years: Tuple[OpenFiscalYearDefinition, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "cik", normalize_cik(self.cik))
        if not isinstance(self.version, str) or not self.version.strip():
            raise ValueError("IssuerFiscalCalendarPolicy.version must be non-empty text.")
        try:
            fiscal_years = tuple(self.fiscal_years)
        except TypeError:
            raise ValueError(
                "IssuerFiscalCalendarPolicy.fiscal_years must be a collection."
            ) from None
        if not fiscal_years:
            raise ValueError("IssuerFiscalCalendarPolicy.fiscal_years must not be empty.")
        if any(not isinstance(item, FiscalYearDefinition) for item in fiscal_years):
            raise ValueError(
                "IssuerFiscalCalendarPolicy.fiscal_years must contain FiscalYearDefinition values."
            )
        years = [item.fiscal_year for item in fiscal_years]
        if len(years) != len(set(years)):
            raise ValueError("Fiscal year numbers must be unique within one policy.")

        try:
            transitions = tuple(self.transitions)
        except TypeError:
            raise ValueError("IssuerFiscalCalendarPolicy.transitions must be a collection.") from None
        if any(not isinstance(item, FiscalTransitionDefinition) for item in transitions):
            raise ValueError(
                "IssuerFiscalCalendarPolicy.transitions must contain FiscalTransitionDefinition values."
            )
        try:
            open_fiscal_years = tuple(self.open_fiscal_years)
        except TypeError:
            raise ValueError(
                "IssuerFiscalCalendarPolicy.open_fiscal_years must be a collection."
            ) from None
        if any(not isinstance(item, OpenFiscalYearDefinition) for item in open_fiscal_years):
            raise ValueError(
                "IssuerFiscalCalendarPolicy.open_fiscal_years must contain "
                "OpenFiscalYearDefinition values."
            )
        if len(open_fiscal_years) > 1:
            raise ValueError("A fiscal-calendar policy can contain at most one open fiscal year.")

        all_definitions = fiscal_years + transitions + open_fiscal_years
        all_years = [item.fiscal_year for item in all_definitions]
        if len(all_years) != len(set(all_years)):
            raise ValueError("Fiscal year numbers must be unique within one policy.")

        chronological = tuple(sorted(all_definitions, key=lambda item: item.period_start))
        if [item.fiscal_year for item in chronological] != sorted(all_years):
            raise ValueError("Fiscal year numbers must increase with their calendar dates.")
        for previous, current in zip(chronological, chronological[1:]):
            if current.period_start <= previous.period_end:
                raise ValueError("Fiscal year definitions must not overlap.")
        if open_fiscal_years and chronological[-1] is not open_fiscal_years[0]:
            raise ValueError("An open fiscal year must be the latest definition in a policy.")
        object.__setattr__(self, "fiscal_years", tuple(sorted(fiscal_years, key=lambda item: item.fiscal_year)))
        object.__setattr__(self, "transitions", tuple(sorted(transitions, key=lambda item: item.fiscal_year)))
        object.__setattr__(self, "open_fiscal_years", open_fiscal_years)


class FiscalCalendarIssueCode(str, Enum):
    EMPTY_FACTS = "empty_facts"
    INVALID_FACT = "invalid_fact"
    ISSUER_MISMATCH = "issuer_mismatch"
    UNRECOGNIZED_FILING_PERIOD = "unrecognized_filing_period"
    FORM_PERIOD_MISMATCH = "form_period_mismatch"
    UNCLASSIFIABLE_FACT_PERIOD = "unclassifiable_fact_period"


@dataclass(frozen=True)
class FiscalCalendarIssue:
    code: FiscalCalendarIssueCode
    message: str
    accession_number: Optional[str] = None
    raw_tag: Optional[str] = None

    def __post_init__(self) -> None:
        if not isinstance(self.message, str) or not self.message.strip():
            raise ValueError("FiscalCalendarIssue.message must be non-empty text.")


@dataclass(frozen=True)
class FiscalCalendarClassificationResult:
    cik: str
    calendar_version: str
    facts: Tuple[FinancialFact, ...] = field(default_factory=tuple)
    issues: Tuple[FiscalCalendarIssue, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        object.__setattr__(self, "cik", normalize_cik(self.cik))
        if not isinstance(self.calendar_version, str) or not self.calendar_version.strip():
            raise ValueError("calendar_version must be non-empty text.")
        if bool(self.facts) == bool(self.issues):
            raise ValueError("Classification must contain either facts or refusal issues.")

    @property
    def is_complete(self) -> bool:
        return bool(self.facts)


@dataclass(frozen=True)
class _PeriodMetadata:
    fiscal_year: int
    fiscal_period: str
    periodicity: str
    expected_base_form: Optional[str] = None


def _is_plain_date(value: object) -> bool:
    return isinstance(value, date) and not isinstance(value, datetime)


def _calendar_indexes(
    policy: IssuerFiscalCalendarPolicy,
) -> Tuple[
    Dict[Tuple[date, date], _PeriodMetadata],
    Dict[date, _PeriodMetadata],
]:
    duration_periods: Dict[Tuple[date, date], _PeriodMetadata] = {}
    instant_periods: Dict[date, _PeriodMetadata] = {}
    one_day = timedelta(days=1)

    for definition in policy.fiscal_years:
        q1_end, q2_end, q3_end, q4_end = definition.quarter_ends
        q2_start = q1_end + one_day
        q3_start = q2_end + one_day
        q4_start = q3_end + one_day
        duration_periods.update(
            {
                (definition.period_start, q1_end): _PeriodMetadata(
                    definition.fiscal_year, "Q1", "quarterly"
                ),
                (q2_start, q2_end): _PeriodMetadata(
                    definition.fiscal_year, "Q2", "quarterly"
                ),
                (definition.period_start, q2_end): _PeriodMetadata(
                    definition.fiscal_year, "Q2YTD", "ytd"
                ),
                (q3_start, q3_end): _PeriodMetadata(
                    definition.fiscal_year, "Q3", "quarterly"
                ),
                (definition.period_start, q3_end): _PeriodMetadata(
                    definition.fiscal_year, "Q3YTD", "ytd"
                ),
                (q4_start, q4_end): _PeriodMetadata(
                    definition.fiscal_year, "Q4", "quarterly"
                ),
                (definition.period_start, q4_end): _PeriodMetadata(
                    definition.fiscal_year, "FY", "annual"
                ),
            }
        )
        instant_periods.update(
            {
                q1_end: _PeriodMetadata(definition.fiscal_year, "Q1", "quarterly", "10-Q"),
                q2_end: _PeriodMetadata(definition.fiscal_year, "Q2", "quarterly", "10-Q"),
                q3_end: _PeriodMetadata(definition.fiscal_year, "Q3", "quarterly", "10-Q"),
                q4_end: _PeriodMetadata(definition.fiscal_year, "FY", "annual", "10-K"),
            }
        )
    for definition in policy.open_fiscal_years:
        previous_end: Optional[date] = None
        for quarter_index, quarter_end in enumerate(definition.quarter_ends, start=1):
            quarter_start = (
                definition.period_start if previous_end is None else previous_end + one_day
            )
            fiscal_period = f"Q{quarter_index}"
            duration_periods[(quarter_start, quarter_end)] = _PeriodMetadata(
                definition.fiscal_year, fiscal_period, "quarterly"
            )
            if quarter_index > 1:
                duration_periods[(definition.period_start, quarter_end)] = _PeriodMetadata(
                    definition.fiscal_year, f"{fiscal_period}YTD", "ytd"
                )
            instant_periods[quarter_end] = _PeriodMetadata(
                definition.fiscal_year, fiscal_period, "quarterly", "10-Q"
            )
            previous_end = quarter_end
    for transition in policy.transitions:
        metadata = _PeriodMetadata(
            transition.fiscal_year,
            "TRANSITION",
            "transition",
            transition.form_type,
        )
        duration_periods[(transition.period_start, transition.period_end)] = metadata
        instant_periods[transition.period_end] = metadata
    return duration_periods, instant_periods


def _refusal(
    policy: IssuerFiscalCalendarPolicy,
    code: FiscalCalendarIssueCode,
    message: str,
    fact: Optional[SecExtractedFact] = None,
) -> FiscalCalendarClassificationResult:
    issue = FiscalCalendarIssue(
        code=code,
        message=message,
        accession_number=fact.provenance_accession_number if fact is not None else None,
        raw_tag=fact.raw_tag if fact is not None else None,
    )
    return FiscalCalendarClassificationResult(
        cik=policy.cik,
        calendar_version=policy.version,
        issues=(issue,),
    )


def _fact_sort_key(fact: SecExtractedFact) -> Tuple[object, ...]:
    return (
        fact.period_end,
        fact.period_start or date.min,
        fact.statement_kind.value,
        fact.canonical_concept,
        fact.unit,
        fact.currency or "",
        fact.provenance_accession_number,
        fact.taxonomy,
        fact.raw_tag,
        fact.value,
        (fact.filing_fiscal_year is None, fact.filing_fiscal_year or 0),
        fact.filing_fiscal_period or "",
        fact.frame or "",
    )


def _validate_source_shape(fact: SecExtractedFact) -> Optional[str]:
    if not isinstance(fact.statement_kind, StatementKind):
        return "Extracted fact has an invalid statement kind."
    if not isinstance(fact.period_type, FactPeriodType):
        return "Extracted fact has an invalid period type."
    expected_period_type = {
        StatementKind.INCOME_STATEMENT: FactPeriodType.DURATION,
        StatementKind.CASH_FLOW: FactPeriodType.DURATION,
        StatementKind.BALANCE_SHEET: FactPeriodType.INSTANT,
        StatementKind.COVER: FactPeriodType.COVER,
    }[fact.statement_kind]
    if fact.period_type is not expected_period_type:
        return "Extracted fact's statement kind and period type disagree."
    if not _is_plain_date(fact.period_end):
        return "Extracted fact period_end must be a date."
    if fact.period_type is FactPeriodType.DURATION:
        if not _is_plain_date(fact.period_start):
            return "Duration fact period_start must be a date."
    elif fact.period_start is not None:
        return "Instant and cover facts must not have period_start."
    if not _is_plain_date(fact.report_date):
        return "Extracted fact report_date must be a date."
    if not _is_plain_date(fact.filed_date):
        return "Extracted fact filed_date must be a date."
    if fact.form_type not in (
        "10-K", "10-K/A", "10-Q", "10-Q/A", "10-KT", "10-KT/A", "10-QT", "10-QT/A"
    ):
        return "Extracted fact has an unsupported filing form."
    if not isinstance(fact.is_amendment, bool):
        return "Extracted fact is_amendment must be boolean."
    if fact.is_amendment != fact.form_type.endswith("/A"):
        return "Extracted fact amendment status disagrees with its filing form."
    return None


def _validate_filing_period(
    fact: SecExtractedFact,
    instant_periods: Dict[date, _PeriodMetadata],
) -> Tuple[Optional[_PeriodMetadata], Optional[FiscalCalendarIssueCode], Optional[str]]:
    filing_period = instant_periods.get(fact.report_date)
    if filing_period is None:
        return (
            None,
            FiscalCalendarIssueCode.UNRECOGNIZED_FILING_PERIOD,
            f"Filing report date {fact.report_date} is absent from the issuer fiscal calendar.",
        )
    base_form = fact.form_type[:-2] if fact.form_type.endswith("/A") else fact.form_type
    if filing_period.expected_base_form != base_form:
        return (
            None,
            FiscalCalendarIssueCode.FORM_PERIOD_MISMATCH,
            f"Form {fact.form_type} does not match the fiscal-calendar period at its report date.",
        )
    return filing_period, None, None


def _statement_period(
    fact: SecExtractedFact,
    filing_period: _PeriodMetadata,
    duration_periods: Dict[Tuple[date, date], _PeriodMetadata],
    instant_periods: Dict[date, _PeriodMetadata],
) -> Optional[StatementPeriod]:
    if fact.period_type is FactPeriodType.COVER:
        return StatementPeriod(
            fiscal_year=filing_period.fiscal_year,
            fiscal_period="COVER",
            period_start=None,
            period_end=fact.period_end,
            periodicity=None,
        )
    if fact.period_type is FactPeriodType.INSTANT:
        metadata = instant_periods.get(fact.period_end)
    else:
        metadata = duration_periods.get((fact.period_start, fact.period_end))
    if metadata is None:
        return None
    return StatementPeriod(
        fiscal_year=metadata.fiscal_year,
        fiscal_period=metadata.fiscal_period,
        period_start=fact.period_start,
        period_end=fact.period_end,
        periodicity=metadata.periodicity,
    )


def _to_financial_fact(
    fact: SecExtractedFact,
    period: StatementPeriod,
    calendar_version: str,
) -> FinancialFact:
    return FinancialFact(
        statement_kind=fact.statement_kind,
        period=period,
        identity=FactIdentity(
            concept=fact.canonical_concept,
            period_start=fact.period_start,
            period_end=fact.period_end,
            unit=fact.unit,
            currency=fact.currency,
            context=FactContext(entity_cik=fact.entity_cik, dimensions=fact.dimensions),
        ),
        value=fact.value,
        raw_tag=fact.raw_tag,
        taxonomy=fact.taxonomy,
        provenance=FilingProvenance(
            accession_number=fact.provenance_accession_number,
            form_type=fact.form_type,
            is_amendment=fact.is_amendment,
            filed_date=fact.filed_date,
            accepted_at=fact.accepted_at,
        ),
        lineage=replace(fact.lineage, fiscal_calendar_version=calendar_version),
    )


def classify_sec_facts(
    facts: Iterable[SecExtractedFact],
    policy: IssuerFiscalCalendarPolicy,
) -> FiscalCalendarClassificationResult:
    """Classify a complete extracted fact set; one bad fact refuses all output."""

    try:
        source_facts = tuple(facts)
    except TypeError:
        return _refusal(
            policy,
            FiscalCalendarIssueCode.INVALID_FACT,
            "facts must be an iterable of SecExtractedFact values.",
        )
    if not source_facts:
        return _refusal(
            policy,
            FiscalCalendarIssueCode.EMPTY_FACTS,
            "At least one extracted fact is required.",
        )
    if any(not isinstance(fact, SecExtractedFact) for fact in source_facts):
        return _refusal(
            policy,
            FiscalCalendarIssueCode.INVALID_FACT,
            "facts must contain only SecExtractedFact values.",
        )

    for fact in source_facts:
        problem = _validate_source_shape(fact)
        if problem is not None:
            return _refusal(policy, FiscalCalendarIssueCode.INVALID_FACT, problem, fact)
    ordered_facts = tuple(sorted(source_facts, key=_fact_sort_key))
    duration_periods, instant_periods = _calendar_indexes(policy)
    converted = []
    for fact in ordered_facts:
        if fact.entity_cik != policy.cik:
            return _refusal(
                policy,
                FiscalCalendarIssueCode.ISSUER_MISMATCH,
                f"Fact CIK {fact.entity_cik} does not match policy CIK {policy.cik}.",
                fact,
            )
        filing_period, issue_code, message = _validate_filing_period(fact, instant_periods)
        if issue_code is not None:
            return _refusal(policy, issue_code, message, fact)
        period = _statement_period(
            fact,
            filing_period,
            duration_periods,
            instant_periods,
        )
        if period is None:
            return _refusal(
                policy,
                FiscalCalendarIssueCode.UNCLASSIFIABLE_FACT_PERIOD,
                "Fact period geometry is absent from the issuer fiscal calendar.",
                fact,
            )
        converted.append(_to_financial_fact(fact, period, policy.version))

    return FiscalCalendarClassificationResult(
        cik=policy.cik,
        calendar_version=policy.version,
        facts=tuple(converted),
    )
