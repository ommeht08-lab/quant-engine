"""
Deterministic, network-free fixture builders for constructing
`FinancialFact` test data — used by `tests/fundamentals/test_selection.py`
and any future test that needs realistic, hand-authored facts without
touching SEC or Supabase.

This is the builder library used alongside `InMemoryFundamentalsRepository`:
tests construct facts here, then exercise the same bounded repository seam
as the PostgreSQL adapter. Everything here is pure construction — no
randomness, no clock reads, no I/O — so two calls with the same arguments
always produce equal objects.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Optional, Tuple, Union

from ..types import (
    FactContext,
    FactIdentity,
    FactLineage,
    FilingProvenance,
    FinancialFact,
    StatementKind,
    StatementPeriod,
)

DEFAULT_TEST_CIK = "0001111111"  # synthetic — not a real issuer
DEFAULT_TEST_INGESTED_AT = datetime(2024, 1, 1, tzinfo=timezone.utc)


def make_lineage(
    *,
    source_adapter: str = "fixture",
    source_document_url: str = "fixture://fundamentals/test-document",
    concept_map_version: str = "fixture-v1",
    ingestion_batch_id: str = "fixture-batch-001",
    ingested_at: datetime = DEFAULT_TEST_INGESTED_AT,
    fiscal_calendar_version: str = "fixture-calendar-v1",
) -> FactLineage:
    """Build deterministic dataset lineage for a normalized fixture fact."""
    return FactLineage(
        source_adapter=source_adapter,
        source_document_url=source_document_url,
        concept_map_version=concept_map_version,
        ingestion_batch_id=ingestion_batch_id,
        ingested_at=ingested_at,
        fiscal_calendar_version=fiscal_calendar_version,
    )


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
    lineage: Optional[FactLineage] = None,
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
        lineage=lineage if lineage is not None else make_lineage(),
    )
