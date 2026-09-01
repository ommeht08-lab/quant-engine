"""
Pure point-in-time fact selection — no I/O, no network, no database.

Given a bag of already-normalized `FinancialFact` objects for ONE issuer
(from ANY source — the fixture builder in this PR today, a future SEC
adapter, or a future Supabase-backed repository, none of which this
module knows or cares about) and a knowledge cutoff, `select_point_in_time`
resolves exactly which fact wins for every distinct `FactIdentity` and
buckets the winners into per-statement, per-period collections. Every
fact identity is resolved independently of every other — this is what
guarantees a partial amendment (or a later comparative that quietly
restates one number) can never accidentally overwrite an unrelated
identity, and never produces a "Frankenstein" statement mixing facts that
don't actually belong to the same reporting period.

Per-identity resolution:
  1. Group eligible facts by (identity, accession_number) — facts the
     SAME filing reported under possibly-different but supposedly-
     synonymous raw tags. Every value in one such group must agree; a
     disagreement is a `SynonymConflict`, and that specific accession
     contributes NO candidate for this identity.
  2. Across all of an identity's accessions (conflicted or not), find the
     one with the latest `eligible_at` (tie-broken by the greater
     `accession_number` — SEC accession numbers are fixed-width and
     chronological per issuer). If THAT accession is the conflicted one,
     the identity gets no winner at all — an older, individually-clean
     accession never substitutes for a newer, broken one. If an OLDER
     accession is conflicted but a newer one is clean, the newer one wins
     normally; the older conflict is still reported but never blocks it.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime
from typing import Dict, Iterable, List, Optional, Tuple

from .time_policy import is_aware
from .types import (
    FactIdentity,
    FilingProvenance,
    FinancialFact,
    FundamentalHistory,
    StatementKind,
    StatementPeriod,
    StatementPeriodFacts,
    SynonymConflict,
    normalize_cik,
)


def _require_aware_cutoff(knowledge_cutoff: datetime) -> None:
    if not is_aware(knowledge_cutoff):
        raise ValueError("select_point_in_time: knowledge_cutoff must be timezone-aware.")


def _identity_sort_key(identity: FactIdentity):
    return (
        identity.concept,
        identity.period_end,
        identity.period_start or date.min,
        identity.unit,
        identity.currency or "",
        identity.context.dimensions,
    )


def _period_sort_key(period: StatementPeriod):
    return (
        period.period_end,
        period.period_start or date.min,
        period.periodicity or "",
        period.fiscal_year,
        period.fiscal_period,
    )


def _validate_issuer_scope(facts: List[FinancialFact], cik: str) -> None:
    for fact in facts:
        if fact.identity.context.entity_cik != cik:
            raise ValueError(
                f"select_point_in_time is scoped to CIK {cik!r}, but received a fact for CIK "
                f"{fact.identity.context.entity_cik!r} (concept={fact.identity.concept!r}, "
                f"accession={fact.provenance.accession_number!r})."
            )


def _validate_identity_consistency(facts: List[FinancialFact]) -> None:
    """
    One FactIdentity must never silently change statement kind or period
    metadata across facts that claim to share it — a genuine restatement
    changes `value`, never `statement_kind`/`period`. A mismatch here is a
    structural ingestion defect, not a legitimate SEC data anomaly, so it
    raises rather than being folded into `SynonymConflict`.
    """
    seen: Dict[FactIdentity, Tuple[StatementKind, StatementPeriod]] = {}
    for fact in facts:
        current = (fact.statement_kind, fact.period)
        prior = seen.get(fact.identity)
        if prior is None:
            seen[fact.identity] = current
        elif prior != current:
            raise ValueError(
                f"Fact identity {fact.identity!r} was reported inconsistently: "
                f"{prior} vs {current}."
            )


def _validate_period_geometry_coherence(facts: List[FinancialFact]) -> None:
    """
    Different CONCEPTS within one statement kind that share the same
    period geometry (period_start, period_end, periodicity) must agree on
    fiscal_year/fiscal_period — `_group_into_periods` groups by the full
    StatementPeriod, so a disagreement here would otherwise silently split
    one real reporting period into two separate bundles instead of being
    rejected as the data defect it is.
    """
    seen: Dict[Tuple[StatementKind, object, date, object], Tuple[int, str]] = {}
    for fact in facts:
        geometry = (fact.statement_kind, fact.period.period_start, fact.period.period_end, fact.period.periodicity)
        metadata = (fact.period.fiscal_year, fact.period.fiscal_period)
        prior = seen.get(geometry)
        if prior is None:
            seen[geometry] = metadata
        elif prior != metadata:
            raise ValueError(
                f"Period geometry {geometry} was reported with inconsistent "
                f"fiscal_year/fiscal_period metadata: {prior} vs {metadata}."
            )


def _validate_same_accession_provenance(facts: List[FinancialFact]) -> None:
    """
    Facts sharing (identity, accession_number) come from the SAME filing
    and must report identical provenance (eligible_at, form_type,
    is_amendment, filed_date, accepted_at) — `_resolve_winners_and_conflicts`
    otherwise picks one group member's provenance to represent the whole
    group, which would make the result depend on input order if the group
    disagreed. Compares whole `FilingProvenance` objects (dataclass
    equality already covers every field but the derived `eligible_at`
    property, which is a pure function of two of them).
    """
    seen: Dict[Tuple[FactIdentity, str], FilingProvenance] = {}
    for fact in facts:
        key = (fact.identity, fact.provenance.accession_number)
        prior = seen.get(key)
        if prior is None:
            seen[key] = fact.provenance
        elif prior != fact.provenance:
            raise ValueError(
                f"Fact identity {key[0]!r}, accession {key[1]!r}, was reported with "
                f"inconsistent provenance: {prior} vs {fact.provenance}."
            )


def _resolve_winners_and_conflicts(
    eligible_facts: List[FinancialFact],
) -> Tuple[List[FinancialFact], List[SynonymConflict]]:
    by_identity_accession: Dict[Tuple[FactIdentity, str], List[FinancialFact]] = defaultdict(list)
    for fact in eligible_facts:
        by_identity_accession[(fact.identity, fact.provenance.accession_number)].append(fact)

    conflicts: List[SynonymConflict] = []
    # identity -> [(eligible_at, accession_number, clean_fact_or_None), ...]
    per_identity: Dict[FactIdentity, List[Tuple[datetime, str, Optional[FinancialFact]]]] = defaultdict(list)

    for (identity, accession_number), group in by_identity_accession.items():
        group_eligible_at = group[0].provenance.eligible_at
        distinct_values = {g.value for g in group}
        if len(distinct_values) > 1:
            ordered = sorted(group, key=lambda g: g.raw_tag)
            conflicts.append(
                SynonymConflict(
                    identity=identity,
                    accession_number=accession_number,
                    raw_tags=tuple(g.raw_tag for g in ordered),
                    values=tuple(g.value for g in ordered),
                )
            )
            per_identity[identity].append((group_eligible_at, accession_number, None))
        else:
            # Agreeing duplicates (one or more facts, all the same value):
            # deterministically keep exactly one representative regardless
            # of how many raw tags reported it or the input's order.
            representative = sorted(group, key=lambda g: g.raw_tag)[0]
            per_identity[identity].append((group_eligible_at, accession_number, representative))

    winners: List[FinancialFact] = []
    for entries in per_identity.values():
        _elig, _accession, latest_fact = max(entries, key=lambda entry: (entry[0], entry[1]))
        if latest_fact is not None:
            winners.append(latest_fact)

    winners.sort(key=lambda f: _identity_sort_key(f.identity))
    conflicts.sort(key=lambda c: (_identity_sort_key(c.identity), c.accession_number))
    return winners, conflicts


def _group_into_periods(
    winners: List[FinancialFact], statement_kind: StatementKind
) -> Tuple[StatementPeriodFacts, ...]:
    """Buckets winners of one statement kind into deterministically
    ordered StatementPeriodFacts — most recent period first."""
    by_period: Dict[StatementPeriod, List[FinancialFact]] = defaultdict(list)
    for fact in winners:
        if fact.statement_kind is statement_kind:
            by_period[fact.period].append(fact)

    ordered_periods = sorted(by_period.keys(), key=_period_sort_key, reverse=True)
    return tuple(
        StatementPeriodFacts(
            period=period,
            facts=tuple(sorted(by_period[period], key=lambda f: _identity_sort_key(f.identity))),
        )
        for period in ordered_periods
    )


def select_point_in_time(
    facts: Iterable[FinancialFact], knowledge_cutoff: datetime, cik: str
) -> FundamentalHistory:
    """
    The public entrypoint: resolve `facts` — all belonging to `cik` — against
    `knowledge_cutoff` into a `FundamentalHistory`.

    Order matters for one deliberate reason: a fact that is not yet
    eligible (`eligible_at > knowledge_cutoff`) must never influence the
    result for an earlier cutoff — not by winning, and not even by making
    this call raise. So issuer scope is checked on every input fact
    first (a wrong-issuer fact is a caller bug regardless of date), THEN
    facts are filtered down to only the eligible ones, and only THEN are
    the structural consistency checks (identity, period-geometry,
    same-accession provenance) run — exclusively against what's actually
    eligible. A future fact with inconsistent metadata is simply filtered
    out before any of those checks ever see it.
    """
    _require_aware_cutoff(knowledge_cutoff)
    normalized_cik = normalize_cik(cik)

    all_facts = list(facts)
    _validate_issuer_scope(all_facts, normalized_cik)

    eligible_facts = [f for f in all_facts if f.provenance.eligible_at <= knowledge_cutoff]

    _validate_identity_consistency(eligible_facts)
    _validate_period_geometry_coherence(eligible_facts)
    _validate_same_accession_provenance(eligible_facts)

    winners, conflicts = _resolve_winners_and_conflicts(eligible_facts)

    cover_facts = tuple(
        sorted(
            (w for w in winners if w.statement_kind is StatementKind.COVER),
            key=lambda f: _identity_sort_key(f.identity),
        )
    )
    non_cover_winners = [w for w in winners if w.statement_kind is not StatementKind.COVER]

    return FundamentalHistory(
        cik=normalized_cik,
        knowledge_cutoff=knowledge_cutoff,
        income_statement_periods=_group_into_periods(non_cover_winners, StatementKind.INCOME_STATEMENT),
        balance_sheet_periods=_group_into_periods(non_cover_winners, StatementKind.BALANCE_SHEET),
        cash_flow_periods=_group_into_periods(non_cover_winners, StatementKind.CASH_FLOW),
        cover_facts=cover_facts,
        conflicts=tuple(conflicts),
    )
