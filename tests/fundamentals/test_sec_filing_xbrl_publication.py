"""One publication, two sources: the Company Facts and filing-XBRL batches commit together or not at all."""

import datetime as dt
from dataclasses import replace
from decimal import Decimal

import pytest

from src.fundamentals import sec_backfill_verification
from src.fundamentals.repository import InMemoryFundamentalsRepository
from src.fundamentals.sec_ingestion import publish_sec_ingestion_dry_run
from src.fundamentals.store import (
    INSERT_BATCH_SQL,
    SELECT_BATCH_SQL,
    SELECT_EXISTING_FACTS_SQL,
    FundamentalsPublishError,
    append_facts,
    source_qualified_batch_id,
)
from tests.fundamentals.test_sec_filing_xbrl_ingestion import DOCUMENTS, MAP, InstanceSource, _payload, _run, _utc
from tests.fundamentals.test_sec_ingestion import CIK, _policy
from tests.fundamentals.test_store import _FakeConnection, _database_row, _stub_execute_values

BATCH = "dry-run-001"
XBRL_BATCH = "dry-run-001+sec_filing_xbrl"
CUTOFF = _utc(2024, 1, 1)


def _dry_run():
    result = _run(CUTOFF, fetcher=InstanceSource(DOCUMENTS))
    assert result.is_complete
    return result


def _batch_row(facts, source):
    sample = next(fact for fact in facts if fact.lineage.source_adapter == source)
    lineage = sample.lineage
    return (lineage.ingestion_batch_id, source, lineage.concept_map_version, lineage.fiscal_calendar_version, lineage.ingested_at)


def _by_source(facts, source):
    return tuple(_database_row(fact) for fact in facts if fact.lineage.source_adapter == source)


class _SplitLookupConnection(_FakeConnection):
    """Existing-fact lookups return each source's rows in turn."""

    def __init__(self, *, one_rows, lookups):
        super().__init__(one_rows=one_rows)
        cursor = self.cursor_instance
        remaining = list(lookups)

        def fetchall():
            cursor.events.append(("fetchall",))
            return list(remaining.pop(0))

        cursor.fetchall = fetchall


def _publish(dry_run, connection):
    return publish_sec_ingestion_dry_run(dry_run, publisher=lambda facts: append_facts(facts, conn=connection))


def test_composed_facts_carry_the_source_qualified_batch_id():
    facts = _dry_run().classified_facts

    assert source_qualified_batch_id(BATCH, "sec_filing_xbrl") == XBRL_BATCH
    assert {(fact.lineage.source_adapter, fact.lineage.ingestion_batch_id) for fact in facts} == {
        ("sec_companyfacts", BATCH),
        ("sec_filing_xbrl", XBRL_BATCH),
    }


def test_both_sources_publish_in_one_transaction_with_one_commit(monkeypatch):
    facts = _dry_run().classified_facts
    connection = _SplitLookupConnection(
        one_rows=((BATCH,), (XBRL_BATCH,)),
        lookups=(_by_source(facts, "sec_companyfacts"), _by_source(facts, "sec_filing_xbrl")),
    )
    _stub_execute_values(monkeypatch, inserted_ids=tuple(range(len(facts))))

    result = _publish(_dry_run(), connection)

    assert result.is_complete and result.inserted_fact_count == len(facts)
    batch_inserts = [event[2] for event in connection.events if event[0] == "execute" and event[1] == INSERT_BATCH_SQL]
    assert batch_inserts == [_batch_row(facts, "sec_companyfacts"), _batch_row(facts, "sec_filing_xbrl")]
    lookups = [event[2][1] for event in connection.events if event[0] == "execute" and event[1] == SELECT_EXISTING_FACTS_SQL]
    assert lookups == ["sec_companyfacts", "sec_filing_xbrl"]
    assert [event for event in connection.events if event[0] == "execute_values"][0][2].__len__() == len(facts)
    assert connection.events.count(("commit",)) == 1
    assert ("rollback",) not in connection.events


def test_identical_retry_is_an_idempotent_no_op(monkeypatch):
    facts = _dry_run().classified_facts
    connection = _SplitLookupConnection(
        one_rows=(None, _batch_row(facts, "sec_companyfacts"), None, _batch_row(facts, "sec_filing_xbrl")),
        lookups=(_by_source(facts, "sec_companyfacts"), _by_source(facts, "sec_filing_xbrl")),
    )
    _stub_execute_values(monkeypatch)

    result = _publish(_dry_run(), connection)

    assert result.is_complete and result.inserted_fact_count == 0
    executed = [event[1] for event in connection.events if event[0] == "execute"]
    assert executed[-6:] == [INSERT_BATCH_SQL, SELECT_BATCH_SQL, INSERT_BATCH_SQL, SELECT_BATCH_SQL,
                             SELECT_EXISTING_FACTS_SQL, SELECT_EXISTING_FACTS_SQL]
    assert connection.events.count(("commit",)) == 1


def test_a_conflicting_composed_fact_refuses_and_rolls_back_both_sources(monkeypatch):
    facts = _dry_run().classified_facts
    conflicting = [list(row) for row in _by_source(facts, "sec_filing_xbrl")]
    conflicting[0][8] = Decimal("999")
    connection = _SplitLookupConnection(
        one_rows=(None, _batch_row(facts, "sec_companyfacts"), None, _batch_row(facts, "sec_filing_xbrl")),
        lookups=(_by_source(facts, "sec_companyfacts"), tuple(tuple(row) for row in conflicting)),
    )
    _stub_execute_values(monkeypatch)

    result = _publish(_dry_run(), connection)

    assert not result.is_complete
    assert result.issues[0].code == "publish_failed"
    assert ("rollback",) in connection.events
    assert ("commit",) not in connection.events


def test_a_reused_supplemental_batch_id_with_other_metadata_refuses_and_rolls_back(monkeypatch):
    facts = _dry_run().classified_facts
    foreign = (XBRL_BATCH, "sec_filing_xbrl", "other-map", "other-calendar", _utc(2020, 1, 1))
    connection = _SplitLookupConnection(one_rows=((BATCH,), None, foreign), lookups=())
    _stub_execute_values(monkeypatch)

    result = _publish(_dry_run(), connection)

    assert result.issues[0].code == "publish_failed"
    assert ("rollback",) in connection.events
    assert not any(event[0] == "execute_values" for event in connection.events)
    assert ("commit",) not in connection.events


@pytest.mark.parametrize(
    "rebatch",
    (
        lambda fact: "unrelated-batch",  # supplement not qualified from the primary
        lambda fact: BATCH,  # both sources under one ID
    ),
)
def test_mixed_source_batches_refuse_cleanly_before_connecting(monkeypatch, rebatch):
    monkeypatch.setattr("src.fundamentals.store._connect", lambda *a, **k: pytest.fail("must not connect"))
    dry_run = _dry_run()
    facts = tuple(
        replace(fact, lineage=replace(fact.lineage, ingestion_batch_id=rebatch(fact)))
        if fact.lineage.source_adapter == "sec_filing_xbrl" else fact
        for fact in dry_run.classified_facts
    )
    object.__setattr__(dry_run, "classified_facts", facts)

    result = publish_sec_ingestion_dry_run(dry_run, publisher=lambda items: append_facts(items, database_url="unused"))

    assert not result.is_complete
    assert result.issues[0].code == "publish_failed"


def test_mismatched_ingestion_times_across_sources_refuse():
    facts = _dry_run().classified_facts
    shifted = tuple(
        replace(fact, lineage=replace(fact.lineage, ingested_at=fact.lineage.ingested_at + dt.timedelta(seconds=1)))
        if fact.lineage.source_adapter == "sec_filing_xbrl" else fact
        for fact in facts
    )
    with pytest.raises(FundamentalsPublishError):
        append_facts(shifted, database_url="unused")


class _Downloader:
    def __init__(self):
        self.instances = InstanceSource(DOCUMENTS)

    def fetch_issuer(self, cik):
        return _payload()

    def fetch_filing_instance(self, cik, accession):
        return self.instances(cik, accession)


class _Catalog:
    @staticmethod
    def policy_for(cik):
        return _policy()


@pytest.fixture
def composing_issuer(monkeypatch):
    monkeypatch.setattr(sec_backfill_verification, "concept_map_for_issuer", lambda cik: MAP)
    monkeypatch.setattr(sec_backfill_verification, "SEC_FISCAL_CALENDAR_CATALOG_V1", _Catalog)
    monkeypatch.setattr(sec_backfill_verification, "VALUATION_TTM_CONCEPTS", ("revenue",))


def _verify(facts, batch_id=BATCH):
    return sec_backfill_verification.verify_published_backfill(
        cik=CIK,
        ingestion_batch_id=batch_id,
        knowledge_cutoffs=(_utc(2023, 3, 15), CUTOFF),
        data_vintage_cutoff=_utc(2027, 1, 1),
        downloader=_Downloader(),
        repository=InMemoryFundamentalsRepository(facts),
    )


def test_verifier_requires_and_reports_exactly_both_batch_ids(composing_issuer):
    result = _verify(_dry_run().classified_facts)

    assert result.is_verified, [cutoff.problems for cutoff in result.cutoffs]
    assert result.ingestion_batch_ids == (BATCH, XBRL_BATCH)
    assert sec_backfill_verification._summary(result)["batch_ids"] == [BATCH, XBRL_BATCH]


@pytest.mark.parametrize("wrong_id", (BATCH, "dry-run-002+sec_filing_xbrl"))
def test_verifier_rejects_composed_facts_under_any_other_batch_id(composing_issuer, wrong_id):
    facts = tuple(
        replace(fact, lineage=replace(fact.lineage, ingestion_batch_id=wrong_id))
        if fact.lineage.source_adapter == "sec_filing_xbrl" else fact
        for fact in _dry_run().classified_facts
    )

    result = _verify(facts)

    assert not result.is_verified
    assert any("expected ingestion batch" in problem for cutoff in result.cutoffs for problem in cutoff.problems)


def test_verifier_rejects_a_publication_missing_the_supplemental_batch(composing_issuer):
    facts = tuple(fact for fact in _dry_run().classified_facts if fact.lineage.source_adapter != "sec_filing_xbrl")

    result = _verify(facts)

    assert not result.is_verified
