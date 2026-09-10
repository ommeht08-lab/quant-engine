import datetime as dt
from dataclasses import replace
from decimal import Decimal

import pytest

from src.fundamentals.adapters.fixture import make_fact, make_lineage, make_period, make_provenance
from src.fundamentals.repository import FundamentalsQuery, FundamentalsRepositoryUnavailable
from src.fundamentals.store import (
    CREATE_APPEND_ONLY_FUNCTION_SQL,
    CREATE_APPEND_ONLY_TRIGGER_SQL,
    CREATE_BATCH_INDEX_SQL,
    CREATE_INGESTION_BATCH_TABLE_SQL,
    CREATE_PIT_INDEX_SQL,
    CREATE_TABLE_SQL,
    INSERT_FACTS_SQL,
    INSERT_BATCH_SQL,
    PUBLISH_PAGE_SIZE,
    SELECT_BATCH_SQL,
    SELECT_EXISTING_FACTS_SQL,
    SELECT_FACTS_SQL,
    FundamentalsPublishError,
    PostgresFundamentalsRepository,
    _fact_to_row,
    _row_to_fact,
    append_facts,
)
from src.fundamentals.types import StatementKind

CIK = "0001111111"
UTC = dt.timezone.utc
KNOWLEDGE_CUTOFF = dt.datetime(2025, 3, 1, tzinfo=UTC)
DATA_VINTAGE_CUTOFF = dt.datetime(2025, 3, 2, tzinfo=UTC)


def _query(**overrides):
    values = {
        "cik": CIK,
        "knowledge_cutoff": KNOWLEDGE_CUTOFF,
        "data_vintage_cutoff": DATA_VINTAGE_CUTOFF,
        "concepts": ("revenue",),
        "source_adapter": "sec_edgar",
        "concept_map_version": "sec-v1",
        "fiscal_calendar_version": "fixture-calendar-v1",
        "max_periods_per_statement": 5,
    }
    values.update(overrides)
    return FundamentalsQuery(**values)


def _fact(*, value="123.456789", batch_id="batch-001", ingested_at=None):
    period = make_period(
        fiscal_year=2023,
        fiscal_period="FY",
        period_start=dt.date(2023, 1, 1),
        period_end=dt.date(2023, 12, 31),
        periodicity="annual",
    )
    return make_fact(
        statement_kind=StatementKind.INCOME_STATEMENT,
        concept="revenue",
        period=period,
        value=value,
        provenance=make_provenance(
            accession_number="0001111111-24-000001",
            filed_date=dt.date(2024, 2, 1),
            accepted_at=dt.datetime(2024, 2, 1, 21, 30, tzinfo=UTC),
        ),
        dimensions=(("us-gaap:ProductAxis", "example:ProductMember"),),
        lineage=make_lineage(
            source_adapter="sec_edgar",
            source_document_url="https://www.sec.gov/Archives/fixture.json",
            concept_map_version="sec-v1",
            ingestion_batch_id=batch_id,
            ingested_at=ingested_at or dt.datetime(2025, 2, 20, tzinfo=UTC),
        ),
    )


def _database_row(fact):
    row = list(_fact_to_row(fact))
    row[7] = [list(dimension) for dimension in fact.identity.context.dimensions]
    return tuple(row)


class _FakeCursor:
    def __init__(self, events, *, one_rows=(), all_rows=()):
        self.events = events
        self.one_rows = list(one_rows)
        self.all_rows = list(all_rows)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def execute(self, sql, params=None):
        self.events.append(("execute", sql, params))

    def fetchone(self):
        row = self.one_rows.pop(0)
        self.events.append(("fetchone", row))
        return row

    def fetchall(self):
        self.events.append(("fetchall",))
        return list(self.all_rows)


class _FakeConnection:
    def __init__(self, *, one_rows=(), all_rows=()):
        self.events = []
        self.cursor_instance = _FakeCursor(
            self.events, one_rows=one_rows, all_rows=all_rows
        )

    def set_session(self, **kwargs):
        self.events.append(("set_session", kwargs))

    def cursor(self):
        self.events.append(("cursor",))
        return self.cursor_instance

    def commit(self):
        self.events.append(("commit",))

    def rollback(self):
        self.events.append(("rollback",))

    def close(self):
        self.events.append(("close",))


def _stub_execute_values(monkeypatch, *, inserted_ids=()):
    def execute_values(cursor, rows):
        cursor.events.append(
            ("execute_values", INSERT_FACTS_SQL, tuple(rows), PUBLISH_PAGE_SIZE)
        )
        return [(inserted_id,) for inserted_id in inserted_ids]

    monkeypatch.setattr("src.fundamentals.store._execute_values", execute_values)


class TestRowMapping:
    def test_round_trip_preserves_exact_value_provenance_and_lineage(self):
        fact = _fact(value="12345678901234567890.123456789")
        rebuilt = _row_to_fact(_database_row(fact))

        assert rebuilt == fact
        assert rebuilt.value == Decimal("12345678901234567890.123456789")

    def test_rejects_stored_eligibility_that_contradicts_provenance(self):
        row = list(_database_row(_fact()))
        row[19] = row[19] + dt.timedelta(seconds=1)

        with pytest.raises(ValueError, match="contradicts"):
            _row_to_fact(tuple(row))


class TestPostgresFundamentalsRepository:
    def test_sets_transaction_read_only_before_first_sql_and_uses_exact_query(self, monkeypatch):
        fact = _fact()
        connection = _FakeConnection(all_rows=(_database_row(fact),))
        monkeypatch.setattr("src.fundamentals.store._connect", lambda *args, **kwargs: connection)
        query = _query()

        result = PostgresFundamentalsRepository("postgresql://not-used").get_facts(query)

        assert result == (fact,)
        set_session_index = connection.events.index(
            ("set_session", {"readonly": True, "autocommit": False})
        )
        first_execute_index = next(
            index for index, event in enumerate(connection.events) if event[0] == "execute"
        )
        assert set_session_index < first_execute_index
        execute_event = connection.events[first_execute_index]
        assert execute_event == (
            "execute",
            SELECT_FACTS_SQL,
            (
                query.cik,
                list(query.concepts),
                query.source_adapter,
                query.concept_map_version,
                query.fiscal_calendar_version,
                query.knowledge_cutoff,
                query.data_vintage_cutoff,
                query.max_periods_per_statement,
            ),
        )
        assert all(
            ddl not in execute_event[1]
            for ddl in (CREATE_TABLE_SQL, CREATE_PIT_INDEX_SQL, CREATE_BATCH_INDEX_SQL)
        )
        assert ("rollback",) in connection.events
        assert connection.events[-1] == ("close",)

    def test_missing_configuration_is_a_typed_sanitized_failure(self, monkeypatch):
        monkeypatch.setattr("src.fundamentals.store._get_database_url", lambda: None)

        with pytest.raises(FundamentalsRepositoryUnavailable, match="not configured"):
            PostgresFundamentalsRepository().get_facts(_query())

    def test_connection_error_never_exposes_database_url(self, monkeypatch, caplog):
        secret_url = "postgresql://user:do-not-leak@database.invalid/private"

        def fail(*args, **kwargs):
            raise RuntimeError(f"driver echoed {secret_url}")

        monkeypatch.setattr("src.fundamentals.store._connect", fail)
        with pytest.raises(FundamentalsRepositoryUnavailable) as caught:
            PostgresFundamentalsRepository(secret_url).get_facts(_query())

        assert secret_url not in str(caught.value)
        assert secret_url not in caplog.text

    def test_cleanup_failures_cannot_replace_the_sanitized_domain_error(self, monkeypatch):
        class BrokenConnection:
            def set_session(self, **kwargs):
                raise RuntimeError("sensitive driver failure")

            def rollback(self):
                raise RuntimeError("sensitive rollback failure")

            def close(self):
                raise RuntimeError("sensitive close failure")

        monkeypatch.setattr(
            "src.fundamentals.store._connect",
            lambda *args, **kwargs: BrokenConnection(),
        )

        with pytest.raises(FundamentalsRepositoryUnavailable) as caught:
            PostgresFundamentalsRepository("postgresql://secret").get_facts(_query())

        assert str(caught.value) == "point-in-time fundamentals store is unavailable."


class TestAppendFacts:
    def test_rejects_a_batch_with_mixed_lineage_before_connecting(self, monkeypatch):
        monkeypatch.setattr(
            "src.fundamentals.store._connect",
            lambda *args, **kwargs: pytest.fail("invalid batch must not connect"),
        )

        with pytest.raises(ValueError, match="one source, mapping version, calendar version, batch ID"):
            append_facts((_fact(batch_id="a"), _fact(batch_id="b")), database_url="unused")

    def test_creates_schema_and_commits_one_atomic_batch(self, monkeypatch):
        fact = _fact()
        connection = _FakeConnection(
            one_rows=(("batch-001",),), all_rows=(_database_row(fact),)
        )
        _stub_execute_values(monkeypatch, inserted_ids=(1,))

        inserted = append_facts((fact,), conn=connection)

        assert inserted == 1
        assert connection.events[0] == (
            "set_session",
            {"readonly": False, "autocommit": False},
        )
        executed_sql = [event[1] for event in connection.events if event[0] == "execute"]
        assert executed_sql == [
            CREATE_INGESTION_BATCH_TABLE_SQL,
            CREATE_TABLE_SQL,
            CREATE_PIT_INDEX_SQL,
            CREATE_BATCH_INDEX_SQL,
            CREATE_APPEND_ONLY_FUNCTION_SQL,
            CREATE_APPEND_ONLY_TRIGGER_SQL,
            INSERT_BATCH_SQL,
            SELECT_EXISTING_FACTS_SQL,
        ]
        assert any(event[0] == "execute_values" for event in connection.events)
        assert ("commit",) in connection.events
        assert ("rollback",) not in connection.events

    def test_idempotent_replay_does_not_insert_or_fail(self, monkeypatch):
        fact = _fact()
        connection = _FakeConnection(
            one_rows=(
                None,
                (
                    "batch-001", "sec_edgar", "sec-v1", "fixture-calendar-v1",
                    fact.lineage.ingested_at,
                ),
            ),
            all_rows=(_database_row(fact),),
        )
        _stub_execute_values(monkeypatch)

        assert append_facts((fact,), conn=connection) == 0
        executed_sql = [event[1] for event in connection.events if event[0] == "execute"]
        assert executed_sql[-3:] == [
            INSERT_BATCH_SQL,
            SELECT_BATCH_SQL,
            SELECT_EXISTING_FACTS_SQL,
        ]
        assert ("commit",) in connection.events

    def test_mixed_existing_and_new_facts_are_validated_together(self, monkeypatch):
        existing = _fact()
        new = replace(
            existing,
            identity=replace(existing.identity, concept="operating_income"),
            raw_tag="OperatingIncomeLoss",
            value=Decimal("25"),
        )
        connection = _FakeConnection(
            one_rows=(("batch-001",),),
            all_rows=(_database_row(existing), _database_row(new)),
        )
        _stub_execute_values(monkeypatch, inserted_ids=(2,))

        assert append_facts((existing, new), conn=connection) == 1
        lookup = next(
            event for event in connection.events
            if event[0] == "execute" and event[1] == SELECT_EXISTING_FACTS_SQL
        )
        assert lookup[2] == (
            [CIK],
            "sec_edgar",
            "sec-v1",
            "fixture-calendar-v1",
            ["0001111111-24-000001"],
        )
        assert ("commit",) in connection.events

    def test_conflicting_replay_rolls_back_the_whole_batch(self, monkeypatch):
        fact = _fact()
        conflicting_row = list(_database_row(fact))
        conflicting_row[8] = Decimal("999")
        connection = _FakeConnection(
            one_rows=(
                None,
                (
                    "batch-001", "sec_edgar", "sec-v1", "fixture-calendar-v1",
                    fact.lineage.ingested_at,
                ),
            ),
            all_rows=(tuple(conflicting_row),),
        )
        _stub_execute_values(monkeypatch)

        with pytest.raises(FundamentalsPublishError, match="conflicts"):
            append_facts((fact,), conn=connection)

        assert ("rollback",) in connection.events
        assert ("commit",) not in connection.events

    def test_reused_batch_id_with_different_metadata_is_rejected(self, monkeypatch):
        fact = _fact()
        connection = _FakeConnection(
            one_rows=(
                None,
                (
                    "batch-001",
                    "sec_edgar",
                    "sec-v1",
                    "fixture-calendar-v1",
                    fact.lineage.ingested_at - dt.timedelta(days=1),
                ),
            )
        )
        monkeypatch.setattr(
            "src.fundamentals.store._execute_values",
            lambda *args, **kwargs: pytest.fail("facts must not be inserted"),
        )

        with pytest.raises(FundamentalsPublishError, match="batch ID"):
            append_facts((fact,), conn=connection)

        assert ("rollback",) in connection.events
        assert not any(event[0] == "execute_values" for event in connection.events)

    def test_bulk_insert_failure_is_sanitized_and_rolls_back(self, monkeypatch):
        fact = _fact()
        connection = _FakeConnection(one_rows=(("batch-001",),))

        def fail(*args, **kwargs):
            raise RuntimeError("database details must not escape")

        monkeypatch.setattr("src.fundamentals.store._execute_values", fail)

        with pytest.raises(FundamentalsPublishError) as caught:
            append_facts((fact,), conn=connection)

        assert str(caught.value) == "point-in-time fundamentals publish failed."
        assert ("rollback",) in connection.events
        assert ("commit",) not in connection.events
