"""Real PostgreSQL contract tests for the point-in-time fact store.

These tests run only in the dedicated GitHub Actions step. The URL must name
the synthetic loopback-only database below; any other configured destination
is rejected before a connection is attempted.
"""

import datetime as dt
import os
from dataclasses import replace
from decimal import Decimal
from urllib.parse import urlparse

import psycopg2
import pytest

from src.fundamentals.adapters.fixture import make_fact, make_lineage, make_period, make_provenance
from src.fundamentals.repository import FundamentalsQuery
from src.fundamentals.store import (
    FundamentalsPublishError,
    PostgresFundamentalsRepository,
    append_facts,
    ensure_schema,
)
from src.fundamentals.types import StatementKind

DATABASE_URL = os.getenv("FUNDAMENTALS_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="requires the dedicated loopback PostgreSQL integration database",
)

UTC = dt.timezone.utc
CIK = "0001111111"
MAIN_INGESTED_AT = dt.datetime(2025, 2, 20, tzinfo=UTC)


def _assert_safe_database_url(database_url: str) -> None:
    parsed = urlparse(database_url)
    assert parsed.scheme in {"postgres", "postgresql"}
    assert parsed.hostname == "127.0.0.1"
    assert parsed.path == "/valuation_engine_test"


@pytest.fixture
def empty_store():
    _assert_safe_database_url(DATABASE_URL)
    connection = psycopg2.connect(DATABASE_URL)
    try:
        connection.set_session(readonly=False, autocommit=False)
        ensure_schema(connection)
        with connection.cursor() as cursor:
            cursor.execute(
                "TRUNCATE TABLE fundamentals_facts, fundamentals_ingestion_batches "
                "RESTART IDENTITY"
            )
        connection.commit()
    finally:
        connection.close()

    yield

    connection = psycopg2.connect(DATABASE_URL)
    try:
        connection.set_session(readonly=False, autocommit=False)
        with connection.cursor() as cursor:
            cursor.execute(
                "TRUNCATE TABLE fundamentals_facts, fundamentals_ingestion_batches "
                "RESTART IDENTITY"
            )
        connection.commit()
    finally:
        connection.close()


def _period(statement_kind: StatementKind, year: int):
    if statement_kind in (StatementKind.BALANCE_SHEET, StatementKind.COVER):
        return make_period(
            fiscal_year=year,
            fiscal_period="COVER" if statement_kind is StatementKind.COVER else "FY",
            period_end=dt.date(year, 12, 31),
            periodicity=None if statement_kind is StatementKind.COVER else "annual",
        )
    return make_period(
        fiscal_year=year,
        fiscal_period="FY",
        period_start=dt.date(year, 1, 1),
        period_end=dt.date(year, 12, 31),
        periodicity="annual",
    )


def _fact(
    statement_kind: StatementKind,
    concept: str,
    year: int,
    *,
    value: str,
    batch_id: str = "batch-main",
    ingested_at: dt.datetime = MAIN_INGESTED_AT,
    accepted_at: dt.datetime = None,
):
    accepted_at = accepted_at or dt.datetime(year + 1, 2, 1, 15, 0, tzinfo=UTC)
    return make_fact(
        statement_kind=statement_kind,
        concept=concept,
        period=_period(statement_kind, year),
        value=value,
        provenance=make_provenance(
            accession_number=f"0001111111-{str(year + 1)[-2:]}-{statement_kind.value[:2]}0001",
            filed_date=accepted_at.date(),
            accepted_at=accepted_at,
        ),
        unit="shares" if statement_kind is StatementKind.COVER else "USD",
        currency=None if statement_kind is StatementKind.COVER else "USD",
        lineage=make_lineage(
            source_adapter="sec_edgar",
            source_document_url=f"https://www.sec.gov/Archives/{year}/fixture.json",
            concept_map_version="sec-v1",
            ingestion_batch_id=batch_id,
            ingested_at=ingested_at,
        ),
    )


def _query(**overrides):
    values = {
        "cik": CIK,
        "knowledge_cutoff": dt.datetime(2025, 3, 1, tzinfo=UTC),
        "data_vintage_cutoff": dt.datetime(2025, 3, 2, tzinfo=UTC),
        "concepts": ("revenue", "total_assets", "operating_cash_flow", "shares_outstanding"),
        "source_adapter": "sec_edgar",
        "concept_map_version": "sec-v1",
        "fiscal_calendar_version": "fixture-calendar-v1",
        "max_periods_per_statement": 2,
    }
    values.update(overrides)
    return FundamentalsQuery(**values)


def _row_count() -> int:
    connection = psycopg2.connect(DATABASE_URL)
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT count(*) FROM fundamentals_facts")
            return cursor.fetchone()[0]
    finally:
        connection.close()


def test_schema_append_idempotence_and_bounded_point_in_time_reads(empty_store):
    definitions = (
        (StatementKind.INCOME_STATEMENT, "revenue"),
        (StatementKind.BALANCE_SHEET, "total_assets"),
        (StatementKind.CASH_FLOW, "operating_cash_flow"),
        (StatementKind.COVER, "shares_outstanding"),
    )
    facts = []
    for statement_kind, concept in definitions:
        for year in (2021, 2022, 2023):
            value = (
                "12345678901234567890.123456789"
                if statement_kind is StatementKind.INCOME_STATEMENT and year == 2023
                else str(year)
            )
            facts.append(_fact(statement_kind, concept, year, value=value))
    future_filing = _fact(
        StatementKind.INCOME_STATEMENT,
        "revenue",
        2025,
        value="999",
        accepted_at=dt.datetime(2026, 2, 1, tzinfo=UTC),
    )
    facts.append(future_filing)

    assert append_facts(facts, database_url=DATABASE_URL) == 13
    assert append_facts(reversed(facts), database_url=DATABASE_URL) == 0

    result = PostgresFundamentalsRepository(DATABASE_URL).get_facts(_query())
    assert len(result) == 8
    assert {fact.statement_kind for fact in result} == set(StatementKind)
    assert all(fact.period.fiscal_year in {2022, 2023} for fact in result)
    exact = next(
        fact
        for fact in result
        if fact.statement_kind is StatementKind.INCOME_STATEMENT
        and fact.period.fiscal_year == 2023
    )
    assert exact.value == Decimal("12345678901234567890.123456789")
    assert future_filing not in result

    late_vintage = _fact(
        StatementKind.INCOME_STATEMENT,
        "revenue",
        2024,
        value="444",
        batch_id="batch-late",
        ingested_at=dt.datetime(2025, 3, 10, tzinfo=UTC),
    )
    assert append_facts((late_vintage,), database_url=DATABASE_URL) == 1
    assert late_vintage not in PostgresFundamentalsRepository(DATABASE_URL).get_facts(_query())
    later_result = PostgresFundamentalsRepository(DATABASE_URL).get_facts(
        _query(data_vintage_cutoff=dt.datetime(2025, 3, 20, tzinfo=UTC))
    )
    assert {
        fact.period.fiscal_year
        for fact in later_result
        if fact.statement_kind is StatementKind.INCOME_STATEMENT
    } == {2023, 2024}

    conflicting = _fact(
        StatementKind.INCOME_STATEMENT,
        "revenue",
        2023,
        value="777",
    )
    with pytest.raises(FundamentalsPublishError, match="conflicts"):
        append_facts((conflicting,), database_url=DATABASE_URL)
    assert _row_count() == 14

    reused_batch = _fact(
        StatementKind.INCOME_STATEMENT,
        "revenue",
        2020,
        value="2020",
        batch_id="batch-main",
        ingested_at=MAIN_INGESTED_AT + dt.timedelta(seconds=1),
    )
    with pytest.raises(FundamentalsPublishError, match="batch ID"):
        append_facts((reused_batch,), database_url=DATABASE_URL)
    assert _row_count() == 14


def test_calendar_policy_corrections_coexist_and_are_selected_explicitly(empty_store):
    original = _fact(
        StatementKind.INCOME_STATEMENT,
        "revenue",
        2023,
        value="100",
        batch_id="batch-calendar-v1",
    )
    corrected = replace(
        original,
        period=replace(original.period, fiscal_year=2024, fiscal_period="Q1"),
        lineage=replace(
            original.lineage,
            fiscal_calendar_version="fixture-calendar-v2",
            ingestion_batch_id="batch-calendar-v2",
            ingested_at=original.lineage.ingested_at + dt.timedelta(seconds=1),
        ),
    )

    assert append_facts((original,), database_url=DATABASE_URL) == 1
    assert append_facts((corrected,), database_url=DATABASE_URL) == 1
    assert _row_count() == 2

    repository = PostgresFundamentalsRepository(DATABASE_URL)
    assert repository.get_facts(_query(fiscal_calendar_version="fixture-calendar-v1")) == (
        original,
    )
    assert repository.get_facts(_query(fiscal_calendar_version="fixture-calendar-v2")) == (
        corrected,
    )


def test_postgres_enforces_the_read_only_transaction_profile(empty_store):
    connection = psycopg2.connect(DATABASE_URL)
    try:
        connection.set_session(readonly=True, autocommit=False)
        with connection.cursor() as cursor:
            cursor.execute("SHOW transaction_read_only")
            assert cursor.fetchone() == ("on",)
            with pytest.raises(psycopg2.errors.ReadOnlySqlTransaction):
                cursor.execute(
                    "INSERT INTO fundamentals_facts "
                    "(cik, statement_kind, canonical_concept, raw_tag, taxonomy, unit, "
                    "dimensions, value, period_end, fiscal_year, fiscal_period, periodicity, "
                    "accession_number, form_type, is_amendment, filed_date, eligible_at, "
                    "source_adapter, source_document_url, concept_map_version, "
                    "fiscal_calendar_version, "
                    "ingestion_batch_id, ingested_at) "
                    "VALUES ('0001111111', 'balance_sheet', 'x', 'x', 'us-gaap', 'USD', "
                    "'[]'::jsonb, 1, DATE '2024-12-31', 2024, 'FY', 'annual', 'x', '10-K', "
                    "false, DATE '2025-01-01', TIMESTAMPTZ '2025-01-01 00:00:00+00', "
                    "'fixture', 'fixture://x', 'fixture-v1', 'fixture-calendar-v1', 'batch', "
                    "TIMESTAMPTZ '2025-01-02 00:00:00+00')"
                )
    finally:
        connection.rollback()
        connection.close()


def test_postgres_schema_enforces_exact_numeric_and_append_only_rows(empty_store):
    fact = _fact(
        StatementKind.INCOME_STATEMENT,
        "revenue",
        2023,
        value="12345678901234567890.123456789",
    )
    assert append_facts((fact,), database_url=DATABASE_URL) == 1

    connection = psycopg2.connect(DATABASE_URL)
    try:
        connection.set_session(readonly=False, autocommit=False)
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT data_type FROM information_schema.columns "
                "WHERE table_schema = current_schema() "
                "AND table_name = 'fundamentals_facts' AND column_name = 'value'"
            )
            assert cursor.fetchone() == ("numeric",)
            with pytest.raises(psycopg2.errors.RaiseException, match="append-only"):
                cursor.execute("UPDATE fundamentals_facts SET value = value + 1")
        connection.rollback()

        with connection.cursor() as cursor:
            with pytest.raises(psycopg2.errors.RaiseException, match="append-only"):
                cursor.execute("DELETE FROM fundamentals_facts")
        connection.rollback()

        with connection.cursor() as cursor:
            with pytest.raises(psycopg2.errors.RaiseException, match="append-only"):
                cursor.execute("DELETE FROM fundamentals_ingestion_batches")
    finally:
        connection.rollback()
        connection.close()
