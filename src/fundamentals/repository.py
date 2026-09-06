"""
Bounded read interface for normalized point-in-time fundamentals.

Callers provide one immutable FundamentalsQuery. Adapters may obtain the
candidate facts from memory or PostgreSQL, but they must apply the same issuer,
knowledge-time, data-vintage, source, mapping-version, concept, and per-statement
period bounds. They deliberately do not decide which amendment wins:
`selection.select_point_in_time` remains the only module allowed to do that.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from typing import Iterable, Optional, Protocol, Tuple

from .time_policy import is_aware
from .types import FinancialFact, FundamentalHistory, StatementKind, StatementPeriod, normalize_cik


def _require_nonempty_text(field_name: str, value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string.")


@dataclass(frozen=True)
class FundamentalsQuery:
    """Everything a repository needs to reproduce one bounded fact read."""

    cik: str
    knowledge_cutoff: datetime
    data_vintage_cutoff: datetime
    concepts: Tuple[str, ...]
    source_adapter: str
    concept_map_version: str
    max_periods_per_statement: int = 8

    def __post_init__(self) -> None:
        object.__setattr__(self, "cik", normalize_cik(self.cik))
        if not is_aware(self.knowledge_cutoff):
            raise ValueError("FundamentalsQuery.knowledge_cutoff must be timezone-aware.")
        if not is_aware(self.data_vintage_cutoff):
            raise ValueError("FundamentalsQuery.data_vintage_cutoff must be timezone-aware.")
        if isinstance(self.max_periods_per_statement, bool) or not isinstance(
            self.max_periods_per_statement, int
        ):
            raise ValueError("FundamentalsQuery.max_periods_per_statement must be a genuine integer.")
        if self.max_periods_per_statement <= 0:
            raise ValueError("FundamentalsQuery.max_periods_per_statement must be positive.")

        _require_nonempty_text("FundamentalsQuery.source_adapter", self.source_adapter)
        _require_nonempty_text("FundamentalsQuery.concept_map_version", self.concept_map_version)

        if isinstance(self.concepts, str):
            raise ValueError("FundamentalsQuery.concepts must be a tuple of concept names, not a string.")
        concepts = tuple(self.concepts)
        if not concepts:
            raise ValueError("FundamentalsQuery.concepts must not be empty.")
        for concept in concepts:
            _require_nonempty_text("FundamentalsQuery concept", concept)
        if len(concepts) != len(set(concepts)):
            raise ValueError("FundamentalsQuery.concepts must not contain duplicates.")
        object.__setattr__(self, "concepts", tuple(sorted(concepts)))


class FundamentalsRepository(Protocol):
    """The single read seam shared by production and deterministic tests."""

    def get_facts(self, query: FundamentalsQuery) -> Tuple[FinancialFact, ...]:
        """Return bounded candidates; amendment resolution happens after this seam."""


class FundamentalsRepositoryUnavailable(RuntimeError):
    """A sanitized repository failure safe to surface to orchestration code."""


class FundamentalIssueCode(str, Enum):
    TICKER_NOT_MAPPED = "ticker_not_mapped"
    STORE_UNAVAILABLE = "store_unavailable"
    NO_FACTS_ELIGIBLE = "no_facts_eligible"
    MISSING_REQUIRED_CONCEPT = "missing_required_concept"
    SYNONYM_CONFLICT = "synonym_conflict"
    UNSUPPORTED_ISSUER = "unsupported_issuer"
    STALE_DATASET = "stale_dataset"


@dataclass(frozen=True)
class FundamentalIssue:
    code: FundamentalIssueCode
    message: str
    concepts: Tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        _require_nonempty_text("FundamentalIssue.message", self.message)
        object.__setattr__(self, "concepts", tuple(sorted(self.concepts)))


@dataclass(frozen=True)
class FundamentalsLoadResult:
    """Exactly one of a complete history or typed refusal issues."""

    query: FundamentalsQuery
    history: Optional[FundamentalHistory] = None
    issues: Tuple[FundamentalIssue, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        has_history = self.history is not None
        has_issues = bool(self.issues)
        if has_history == has_issues:
            raise ValueError(
                "FundamentalsLoadResult must contain either one complete history or refusal issues."
            )
        if self.history is not None:
            if self.history.cik != self.query.cik:
                raise ValueError("FundamentalsLoadResult history CIK must match its query.")
            if self.history.knowledge_cutoff != self.query.knowledge_cutoff:
                raise ValueError("FundamentalsLoadResult history cutoff must match its query.")

    @property
    def is_complete(self) -> bool:
        return self.history is not None


def _period_sort_key(period: StatementPeriod):
    return (
        period.period_end,
        period.period_start or date.min,
        period.periodicity or "",
        period.fiscal_year,
        period.fiscal_period,
    )


def _fact_sort_key(fact: FinancialFact):
    identity = fact.identity
    return (
        fact.statement_kind.value,
        identity.period_end,
        identity.period_start or date.min,
        identity.concept,
        identity.unit,
        identity.currency or "",
        identity.context.dimensions,
        fact.provenance.eligible_at,
        fact.provenance.accession_number,
        fact.raw_tag,
    )


def _bounded_periods(
    facts: Iterable[FinancialFact], max_periods_per_statement: int
) -> dict[StatementKind, frozenset[StatementPeriod]]:
    periods_by_statement: dict[StatementKind, set[StatementPeriod]] = {
        statement_kind: set() for statement_kind in StatementKind
    }
    for fact in facts:
        periods_by_statement[fact.statement_kind].add(fact.period)

    return {
        statement_kind: frozenset(
            sorted(periods, key=_period_sort_key, reverse=True)[:max_periods_per_statement]
        )
        for statement_kind, periods in periods_by_statement.items()
    }


class InMemoryFundamentalsRepository:
    """Deterministic adapter used to test the repository interface without I/O."""

    def __init__(self, facts: Iterable[FinancialFact]):
        self._facts = tuple(facts)

    def get_facts(self, query: FundamentalsQuery) -> Tuple[FinancialFact, ...]:
        concepts = frozenset(query.concepts)
        eligible = tuple(
            fact
            for fact in self._facts
            if fact.identity.context.entity_cik == query.cik
            and fact.identity.concept in concepts
            and fact.provenance.eligible_at <= query.knowledge_cutoff
            and fact.lineage.ingested_at <= query.data_vintage_cutoff
            and fact.lineage.source_adapter == query.source_adapter
            and fact.lineage.concept_map_version == query.concept_map_version
        )
        allowed_periods = _bounded_periods(eligible, query.max_periods_per_statement)
        bounded = (
            fact for fact in eligible if fact.period in allowed_periods[fact.statement_kind]
        )
        return tuple(sorted(bounded, key=_fact_sort_key))
