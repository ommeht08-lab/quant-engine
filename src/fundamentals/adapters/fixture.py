"""
Deterministic, network-free fixture builders for constructing
`FinancialFact` test data — used by `tests/fundamentals/test_selection.py`
and any future test that needs realistic, hand-authored facts without
touching SEC or Supabase.

This is a builder library, not yet a `FundamentalsRepository`
implementation: the repository Protocol and its production
(Supabase-backed) and fixture implementations arrive in a later PR, once
`get_fundamentals()` orchestration (ticker/CIK resolution, bounded
repository queries) exists. Everything here is pure construction — no
randomness, no clock reads, no I/O — so two calls with the same arguments
always produce equal objects.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Optional, Tuple, Union

from ..types import (
    FactContext,
    FactIdentity,
    FilingProvenance,
    FinancialFact,
    StatementKind,
    StatementPeriod,
)

DEFAULT_TEST_CIK = "0001111111"  # synthetic — not a real issuer


def make_provenance(
    *,
    accession_number: str,
    filed_date: date,
    form_type: str = "10-K",
    is_amendment: bool = False,
    accepted_at: Optional[datetime] = None,
) -> FilingProvenance:
    """`eligible_at` is never passed here — `FilingProvenance` computes it
    itself, as a property, from `accepted_at`/`filed_date`, so a fixture
    can never construct one that contradicts the real time policy."""
    return FilingProvenance(
        accession_number=accession_number,
        form_type=form_type,
        is_amendment=is_amendment,
        filed_date=filed_date,
        accepted_at=accepted_at,
    )


def make_period(
    *,
    fiscal_year: int,
    fiscal_period: str,
    period_end: date,
    period_start: Optional[date] = None,
    periodicity: Optional[str] = None,
) -> StatementPeriod:
    return StatementPeriod(
        fiscal_year=fiscal_year,
        fiscal_period=fiscal_period,
        period_start=period_start,
        period_end=period_end,
        periodicity=periodicity,
    )


def make_fact(
    *,
    statement_kind: StatementKind,
    concept: str,
    period: StatementPeriod,
    value: Union[Decimal, int, str],
    provenance: FilingProvenance,
    unit: str = "USD",
    currency: Optional[str] = "USD",
    dimensions: Tuple[Tuple[str, str], ...] = (),
    entity_cik: str = DEFAULT_TEST_CIK,
    raw_tag: Optional[str] = None,
    taxonomy: str = "us-gaap",
) -> FinancialFact:
    """
    Builds a FinancialFact from a StatementPeriod, deriving its
    FactIdentity's period bounds from that same period so the two can
    never disagree (FinancialFact itself would reject that mismatch).

    `raw_tag` defaults to `concept` itself when omitted — convenient for
    tests that don't care about tag/concept naming distinctness; tests
    exercising the synonym-conflict path pass distinct raw tags for the
    same concept explicitly.
    """
    identity = FactIdentity(
        concept=concept,
        period_start=period.period_start,
        period_end=period.period_end,
        unit=unit,
        currency=currency,
        context=FactContext(entity_cik=entity_cik, dimensions=dimensions),
    )
    return FinancialFact(
        statement_kind=statement_kind,
        period=period,
        identity=identity,
        value=Decimal(value),
        raw_tag=raw_tag if raw_tag is not None else concept,
        taxonomy=taxonomy,
        provenance=provenance,
    )
