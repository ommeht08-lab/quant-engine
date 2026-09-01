"""
Core data types for point-in-time fundamentals — see CONTEXT.md for the
glossary these names refer to. Every type here is a frozen dataclass, and
every collection they expose is a tuple, never a dict/list/set — nothing
here can be mutated in place after construction.

This module has no I/O and no knowledge of SEC, Supabase, or any other
data source — it defines the shapes `src.fundamentals.selection` operates
on, nothing more.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Optional, Tuple

from .time_policy import eligible_at as _compute_eligible_at
from .time_policy import is_aware


def normalize_cik(value: str) -> str:
    """Canonicalizes a CIK to its 10-digit, zero-padded string form —
    the form every fact's `FactContext.entity_cik` and every
    `FundamentalHistory.cik` is stored/compared as."""
    if not isinstance(value, str) or not value.isdigit():
        raise ValueError(f"CIK must be a string of digits, got {value!r}.")
    if len(value) > 10:
        raise ValueError(f"CIK must be at most 10 digits, got {value!r}.")
    return value.zfill(10)


class StatementKind(str, Enum):
    """Which of the three financial statements a fact belongs to, or
    COVER for a filing cover-page fact (e.g. shares outstanding as of the
    cover date) that is never a fiscal statement period and is always
    excluded from the three statement-period collections."""

    INCOME_STATEMENT = "income_statement"
    BALANCE_SHEET = "balance_sheet"
    CASH_FLOW = "cash_flow"
    COVER = "cover"


@dataclass(frozen=True)
class FactContext:
    """
    The XBRL reporting context a fact was tagged under, beyond period and
    concept alone: which issuer (`entity_cik`, normalized to 10 digits),
    and whether it's the fully consolidated entity value or a dimensional
    segment/member breakout. `dimensions` is canonicalized on
    construction — sorted, and rejected outright if the same axis appears
    twice (an ambiguous, invalid context) — so two logically-identical
    dimension sets supplied in a different order always compare equal.
    """

    entity_cik: str
    dimensions: Tuple[Tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "entity_cik", normalize_cik(self.entity_cik))
        axes = [axis for axis, _member in self.dimensions]
        if len(axes) != len(set(axes)):
            raise ValueError(f"FactContext.dimensions has a duplicate axis: {self.dimensions!r}.")
        object.__setattr__(self, "dimensions", tuple(sorted(self.dimensions)))


@dataclass(frozen=True)
class FactIdentity:
    """
    The full identity two facts must share to be considered "the same
    fact, possibly restated" — see CONTEXT.md's Fact identity entry.
    `period_start`/`period_end` are carried here (in addition to living on
    the fact's own StatementPeriod) specifically so identity comparison
    never needs to reach through to a separate object: a quarter and its
    year-to-date cumulative can share a `period_end` while differing in
    `period_start`, and must never be conflated.
    """

    concept: str
    period_start: Optional[date]
    period_end: date
    unit: str
    currency: Optional[str]
    context: FactContext


@dataclass(frozen=True)
class StatementPeriod:
    """
    One `(period_start, period_end, periodicity)` combination. `fiscal_year`
    and `fiscal_period` are metadata derived from the period's own
    geometry (plus, in a later PR, the issuer's fiscal-calendar reference)
    — never trusted from a filing's own contextual fy/fp label, which
    describes the filing, not the period. `periodicity` is `None` only for
    a COVER period; every genuine statement period has one.
    """

    fiscal_year: int
    fiscal_period: str  # "Q1" | "Q2" | "Q3" | "Q4" | "FY" | "Q2YTD" | "Q3YTD" | "COVER"
    period_start: Optional[date]
    period_end: date
    periodicity: Optional[str]  # "annual" | "quarterly" | "ytd" | None (None for COVER)


@dataclass(frozen=True)
class FilingProvenance:
    """
    Where one fact came from and exactly when it became knowable.
    `accepted_at` is SEC's own raw acceptance timestamp, nullable only
    when genuinely unresolved from the submissions history. `eligible_at`
    is deliberately NOT a stored field — it is a property, always computed
    fresh from `accepted_at`/`filed_date` through
    `time_policy.eligible_at`, so it is structurally impossible to
    construct a `FilingProvenance` whose stored eligibility contradicts
    its own acceptance/filing dates.
    """

    accession_number: str
    form_type: str
    is_amendment: bool
    filed_date: date
    accepted_at: Optional[datetime]

    def __post_init__(self) -> None:
        if self.accepted_at is not None and not is_aware(self.accepted_at):
            raise ValueError("FilingProvenance.accepted_at must be timezone-aware if provided.")

    @property
    def eligible_at(self) -> datetime:
        return _compute_eligible_at(self.accepted_at, self.filed_date)


@dataclass(frozen=True)
class FinancialFact:
    """
    One reported number: its identity, its value, where it came from, and
    which statement (or COVER) it belongs to. Construction enforces the
    structural invariants no downstream logic should have to re-check:
    identity/period bounds agree, the value is a genuine finite number,
    COVER facts never carry a periodicity, every non-COVER fact does, and
    a balance-sheet fact is an instant (no `period_start`) while an
    income-statement or cash-flow fact is a duration (`period_start` set).
    """

    statement_kind: StatementKind
    period: StatementPeriod
    identity: FactIdentity
    value: Decimal
    raw_tag: str
    taxonomy: str
    provenance: FilingProvenance

    def __post_init__(self) -> None:
        if (
            self.identity.period_start != self.period.period_start
            or self.identity.period_end != self.period.period_end
        ):
            raise ValueError(
                "FinancialFact.identity period bounds must match FinancialFact.period exactly "
                f"(identity=({self.identity.period_start}, {self.identity.period_end}), "
                f"period=({self.period.period_start}, {self.period.period_end}))."
            )

        if not isinstance(self.value, Decimal):
            raise ValueError(
                f"FinancialFact.value must be a Decimal, got {type(self.value).__name__}: {self.value!r}."
            )
        if not self.value.is_finite():
            raise ValueError(f"FinancialFact.value must be a finite number, got {self.value!r}.")

        if self.statement_kind is StatementKind.COVER:
            if self.period.fiscal_period != "COVER":
                raise ValueError("A COVER fact's period.fiscal_period must be 'COVER'.")
            if self.period.period_start is not None:
                raise ValueError("A COVER fact's period.period_start must be None.")
            if self.period.periodicity is not None:
                raise ValueError("A COVER fact's period.periodicity must be None.")
            return

        if self.period.fiscal_period == "COVER":
            raise ValueError(f"A {self.statement_kind.value} fact must not have fiscal_period='COVER'.")
        if self.period.periodicity is None:
            raise ValueError(f"A {self.statement_kind.value} fact must have period.periodicity set.")
        if self.statement_kind is StatementKind.BALANCE_SHEET and self.period.period_start is not None:
            raise ValueError("Balance sheet facts are instant facts; period.period_start must be None.")
        if (
            self.statement_kind in (StatementKind.INCOME_STATEMENT, StatementKind.CASH_FLOW)
            and self.period.period_start is None
        ):
            raise ValueError(
                f"{self.statement_kind.value} facts are duration facts; period.period_start must be set."
            )


@dataclass(frozen=True)
class SynonymConflict:
    """
    Detected when two (or more) facts sharing one FactIdentity AND one
    accession_number disagree in value — e.g. two XBRL tags mapped to the
    same canonical concept that turn out not to be true synonyms within
    this specific filing. Never silently resolved by picking one; see
    `src.fundamentals.selection` for exactly how a conflicted accession
    affects (or doesn't affect) that identity's winner.
    """

    identity: FactIdentity
    accession_number: str
    raw_tags: Tuple[str, ...]
    values: Tuple[Decimal, ...]


@dataclass(frozen=True)
class StatementPeriodFacts:
    """
    The winning facts for one statement period, as an ordered tuple —
    never a dict — so this object stays fully immutable end to end. Two
    facts for the same concept and period but a different unit, currency,
    or context are genuinely different identities and both appear here,
    never collapsed onto one slot. `facts` is ordered deterministically
    (sorted by identity), not by construction order.
    """

    period: StatementPeriod
    facts: Tuple[FinancialFact, ...]

    def fact_for(self, identity: FactIdentity) -> Optional[FinancialFact]:
        """Linear lookup by identity — this period bundle is typically
        small (one filing's worth of concepts), so no index is kept."""
        for fact in self.facts:
            if fact.identity == identity:
                return fact
        return None


@dataclass(frozen=True)
class FundamentalHistory:
    """
    The result of resolving one issuer's facts against one knowledge
    cutoff. Deliberately carries no wall-clock generation timestamp and no
    singular source label — provenance lives on each individual
    FinancialFact, and a history assembled from multiple sources has no
    single "the" source to name at this level. `cover_facts` retains
    eligible COVER facts (e.g. cover-page shares outstanding) on their
    own, deterministically ordered — they never appear in any of the
    three statement-period collections.
    """

    cik: str
    knowledge_cutoff: datetime
    income_statement_periods: Tuple[StatementPeriodFacts, ...]
    balance_sheet_periods: Tuple[StatementPeriodFacts, ...]
    cash_flow_periods: Tuple[StatementPeriodFacts, ...]
    cover_facts: Tuple[FinancialFact, ...] = field(default_factory=tuple)
    conflicts: Tuple[SynonymConflict, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        object.__setattr__(self, "cik", normalize_cik(self.cik))
        if not is_aware(self.knowledge_cutoff):
            raise ValueError("FundamentalHistory.knowledge_cutoff must be timezone-aware.")
