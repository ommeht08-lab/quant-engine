"""Incremental publication: prove inserted identities before commit, and verify refreshes by lineage.

The scenarios use a two-source issuer (Company Facts plus filing-XBRL
compositions) whose filings arrive over time:

* backfill at 2023-06-01: Q1 and Q2 (4 facts) in ``backfill-1``;
* refresh at 2023-09-01: Q3 is new, so 2 facts go into ``refresh-2`` and
  4 are reused from ``backfill-1``;
* refresh at 2023-10-01: nothing new, a no-op spanning two earlier batches;
* refresh at 2024-01-01: the 10-K is new, 6 facts are reused from two batches.
"""

import datetime as dt
from dataclasses import replace

import pytest

from src.fundamentals import sec_backfill_verification
from src.fundamentals.incremental_publication import (
    _written_batch_keys,
    INSERT_FACTS_RETURNING_SQL,
    SELECT_BATCH_ROWS_SQL,
    SELECT_ISSUER_FACTS_SQL,
    IncrementalPublicationError,
    IncrementalPublicationReceipt,
    batch_lineage_problems,
    paired_primary_batch_id,
    prove_incremental_publication,
    publication_batch_problems,
    publication_issuer_problems,
    publish_incremental,
)
from src.fundamentals.repository import InMemoryFundamentalsRepository
from src.fundamentals.sec_ingestion import publish_sec_ingestion_dry_run
from src.fundamentals.store import (
    FundamentalsPublishError,
    _publication_batch_keys,
    _source_identity_key,
    source_qualified_batch_id,
)
from tests.fundamentals.test_sec_filing_xbrl_ingestion import DOCUMENTS, InstanceSource, _run, _utc
from tests.fundamentals import test_sec_filing_xbrl_publication as _xbrl_publication
from tests.fundamentals.test_sec_filing_xbrl_publication import _Downloader
from tests.fundamentals.test_sec_ingestion import CIK
from tests.fundamentals.test_store import _FakeConnection, _database_row

XBRL = "sec_filing_xbrl"
HISTORICAL = _utc(2023, 6, 1)

# Shared fixture: the verifier sees the two-source test issuer's policy.
composing_issuer = _xbrl_publication.composing_issuer


def _rebatch(facts, batch_id, ingested_at):
    return tuple(
        replace(
            fact,
            lineage=replace(
                fact.lineage,
                ingestion_batch_id=(
                    source_qualified_batch_id(batch_id, XBRL) if fact.lineage.source_adapter == XBRL else batch_id
                ),
                ingested_at=ingested_at,
            ),
        )
        for fact in facts
    )


def _publication(cutoff, batch_id, ingested_at):
    run = _run(cutoff, fetcher=InstanceSource(DOCUMENTS))
    assert run.is_complete
    return _rebatch(run.classified_facts, batch_id, ingested_at)


class _Store:
    """An in-memory model of one publish transaction's database effects."""

    def __init__(self):
        self.facts = {}
        self.batches = {}

    def publish(self, incoming, cutoff, history_frozen_through=None):
        """Simulate the transaction and return the proof's inputs, before any commit.

        Mirrors publish_incremental: read the issuer's stored facts, decide
        what to insert, write batch rows only for sources with new facts (plus
        the primary a written supplement pairs with), insert, read back.
        """

        batch_keys = _publication_batch_keys(incoming)
        incoming_ids = {_source_identity_key(fact) for fact in incoming}
        stored_before = [
            fact for fact in self.facts.values()
            if fact.identity.context.entity_cik == CIK and fact.provenance.eligible_at <= cutoff
        ]
        pre_insert = {
            _source_identity_key(fact): fact for fact in stored_before if _source_identity_key(fact) in incoming_ids
        }
        to_insert = [fact for fact in incoming if _source_identity_key(fact) not in pre_insert]
        written = _written_batch_keys(batch_keys, to_insert)
        batches = dict(self.batches)
        for key in written:
            batches.setdefault(key[0], key)
        returned = list(to_insert) if written else []
        return dict(
            incoming=incoming,
            pre_insert=pre_insert,
            returned=returned,
            stored=stored_before + returned,
            batch_rows=batches,
            batch_keys=batch_keys,
            written_batch_keys=written,
            knowledge_cutoff=cutoff,
            cik=CIK,
            history_frozen_through=history_frozen_through,
        ), batches

    def commit(self, incoming, cutoff, history_frozen_through=None):
        inputs, batches = self.publish(incoming, cutoff, history_frozen_through)
        receipt = prove_incremental_publication(**inputs)
        if receipt.batch_written:  # a no-op is rolled back and writes nothing
            for fact in inputs["returned"]:
                self.facts[_source_identity_key(fact)] = fact
            self.batches = batches
        return receipt

    def fact_ciks(self, batch_ids):
        """Every fact's issuer per batch, across all issuers (the verifier's binding read)."""

        result = {}
        for fact in self.facts.values():
            if fact.lineage.ingestion_batch_id in set(batch_ids):
                counts = result.setdefault(fact.lineage.ingestion_batch_id, {})
                cik = fact.identity.context.entity_cik
                counts[cik] = counts.get(cik, 0) + 1
        return result


@pytest.fixture
def history():
    """A store after the backfill and the first refresh."""

    store = _Store()
    store.commit(_publication(_utc(2023, 6, 1), "backfill-1", _utc(2023, 6, 2)), _utc(2023, 6, 1))
    store.commit(_publication(_utc(2023, 9, 1), "refresh-2", _utc(2023, 9, 2)), _utc(2023, 9, 1))
    return store


# --- The proof ---------------------------------------------------------------

def test_backfill_inserts_every_fact_into_its_own_two_batches():
    store = _Store()
    receipt = store.commit(_publication(_utc(2023, 6, 1), "backfill-1", _utc(2023, 6, 2)), _utc(2023, 6, 1))

    assert receipt.batch_ids == ("backfill-1", "backfill-1+sec_filing_xbrl")
    assert dict(receipt.inserted_by_batch) == {"backfill-1": 2, "backfill-1+sec_filing_xbrl": 2}
    assert receipt.reused_by_batch == ()
    assert not receipt.is_no_op


def test_new_filing_inserts_only_its_facts_and_reuses_the_rest_from_the_backfill():
    store = _Store()
    store.commit(_publication(_utc(2023, 6, 1), "backfill-1", _utc(2023, 6, 2)), _utc(2023, 6, 1))

    receipt = store.commit(_publication(_utc(2023, 9, 1), "refresh-2", _utc(2023, 9, 2)), _utc(2023, 9, 1))

    assert dict(receipt.inserted_by_batch) == {"refresh-2": 1, "refresh-2+sec_filing_xbrl": 1}
    assert dict(receipt.reused_by_batch) == {"backfill-1": 2, "backfill-1+sec_filing_xbrl": 2}
    assert (receipt.inserted_fact_count, receipt.reused_fact_count) == (2, 4)


def test_no_op_refresh_inserts_nothing_and_accepts_facts_spread_across_earlier_batches(history):
    receipt = history.commit(_publication(_utc(2023, 10, 1), "refresh-3", _utc(2023, 10, 2)), _utc(2023, 10, 1))

    assert receipt.is_no_op
    assert receipt.written_batch_ids == () and receipt.inserted_by_batch == ()
    assert dict(receipt.reused_by_batch) == {
        "backfill-1": 2,
        "backfill-1+sec_filing_xbrl": 2,
        "refresh-2": 1,
        "refresh-2+sec_filing_xbrl": 1,
    }


def test_new_filing_after_several_batches(history):
    receipt = history.commit(_publication(_utc(2024, 1, 1), "refresh-4", _utc(2024, 1, 2)), _utc(2024, 1, 1))

    assert (receipt.inserted_fact_count, receipt.reused_fact_count) == (2, 6)


def _refuses(inputs, match):
    with pytest.raises(IncrementalPublicationError, match=match):
        prove_incremental_publication(**inputs)


def test_an_insert_that_silently_skipped_a_new_fact_refuses(history):
    inputs, _ = history.publish(_publication(_utc(2024, 1, 1), "refresh-4", _utc(2024, 1, 2)), _utc(2024, 1, 1))
    inputs["returned"] = inputs["returned"][:1]

    _refuses(inputs, "neither inserted nor already stored")


def test_a_returned_identity_that_was_already_stored_refuses(history):
    inputs, _ = history.publish(_publication(_utc(2024, 1, 1), "refresh-4", _utc(2024, 1, 2)), _utc(2024, 1, 1))
    already = next(iter(inputs["pre_insert"].values()))
    inputs["returned"] = list(inputs["returned"]) + [replace(already, lineage=inputs["returned"][0].lineage)]

    _refuses(inputs, "already stored before the insert")


def test_a_repeated_returned_identity_refuses(history):
    inputs, _ = history.publish(_publication(_utc(2024, 1, 1), "refresh-4", _utc(2024, 1, 2)), _utc(2024, 1, 1))
    inputs["returned"] = list(inputs["returned"]) + [inputs["returned"][0]]

    _refuses(inputs, "more than once")


def test_an_inserted_fact_outside_this_publications_batch_refuses(history):
    inputs, _ = history.publish(_publication(_utc(2024, 1, 1), "refresh-4", _utc(2024, 1, 2)), _utc(2024, 1, 1))
    first = inputs["returned"][0]
    inputs["returned"] = [replace(first, lineage=replace(first.lineage, ingestion_batch_id="refresh-2"))] + list(
        inputs["returned"][1:]
    )

    _refuses(inputs, "not in this publication's batch")


def test_a_stored_fact_that_conflicts_with_the_publication_refuses(history):
    inputs, _ = history.publish(_publication(_utc(2024, 1, 1), "refresh-4", _utc(2024, 1, 2)), _utc(2024, 1, 1))
    identity, fact = next(iter(inputs["pre_insert"].items()))
    inputs["pre_insert"] = {**inputs["pre_insert"], identity: replace(fact, value=fact.value + 1)}

    _refuses(inputs, "conflict with the incoming publication")


def test_a_stored_fact_the_publication_no_longer_contains_refuses(history):
    incoming = _publication(_utc(2024, 1, 1), "refresh-4", _utc(2024, 1, 2))
    inputs, _ = history.publish(incoming[1:], _utc(2024, 1, 1))

    _refuses(inputs, "1 unexpected")


def test_a_supplemental_batch_without_a_matching_primary_refuses(history):
    # The foreign key would accept this: the companion's own batch row exists
    # and matches its facts. Only the pairing rule notices the primary differs.
    inputs, _ = history.publish(_publication(_utc(2024, 1, 1), "refresh-4", _utc(2024, 1, 2)), _utc(2024, 1, 1))
    primary = inputs["batch_rows"]["backfill-1"]
    inputs["batch_rows"] = dict(inputs["batch_rows"], **{"backfill-1": primary[:4] + (_utc(2020, 1, 1),)})

    _refuses(inputs, "lack a matching primary batch")


def test_a_missing_primary_batch_row_refuses(history):
    inputs, _ = history.publish(_publication(_utc(2024, 1, 1), "refresh-4", _utc(2024, 1, 2)), _utc(2024, 1, 1))
    inputs["batch_rows"] = {key: row for key, row in inputs["batch_rows"].items() if key != "refresh-2"}

    _refuses(inputs, "refresh-2\\+sec_filing_xbrl")


def test_frozen_history_refuses_a_refresh_that_inserts_a_fact_visible_at_the_frozen_cutoff():
    # A fact from the Q1 filing was never stored (as after a concept-map change
    # or a late SEC addition), so this refresh would insert it into history.
    store = _Store()
    backfill = _publication(_utc(2023, 6, 1), "backfill-1", _utc(2023, 6, 2))
    assert backfill  # the store is empty: every Q1/Q2 fact would be newly inserted
    incoming = _publication(_utc(2023, 9, 1), "refresh-2", _utc(2023, 9, 2))

    with pytest.raises(IncrementalPublicationError, match="frozen historical cutoff"):
        prove_incremental_publication(**store.publish(incoming, _utc(2023, 9, 1), HISTORICAL)[0])


def test_frozen_history_accepts_a_refresh_that_only_adds_later_filings(history):
    receipt = history.commit(
        _publication(_utc(2024, 1, 1), "refresh-4", _utc(2024, 1, 2)), _utc(2024, 1, 1), HISTORICAL
    )

    assert receipt.inserted_fact_count == 2


def test_a_same_batch_retry_is_a_replay_not_earlier_batch_reuse(history):
    receipt = history.commit(_publication(_utc(2023, 9, 1), "refresh-2", _utc(2023, 9, 2)), _utc(2023, 9, 1))

    assert receipt.is_no_op
    assert receipt.replayed_fact_count == 2
    assert dict(receipt.reused_by_batch) == {"backfill-1": 2, "backfill-1+sec_filing_xbrl": 2}


def test_pairing_rules_directly():
    row = ("b+sec_filing_xbrl", XBRL, "map", "cal", _utc(2024, 1, 1))
    primary = ("b", "sec_companyfacts", "map", "cal", _utc(2024, 1, 1))

    assert paired_primary_batch_id("b+sec_filing_xbrl", XBRL) == "b"
    assert paired_primary_batch_id("+sec_filing_xbrl", XBRL) is None
    assert paired_primary_batch_id("b", XBRL) is None
    assert publication_batch_problems(["b", row[0]], {"b": primary, row[0]: row}) == ()
    assert publication_batch_problems(["b", row[0]], {row[0]: row})  # primary row missing
    wrong_source = ("b", XBRL, "map", "cal", _utc(2024, 1, 1))
    assert publication_batch_problems([row[0]], {"b": wrong_source, row[0]: row})
    assert batch_lineage_problems((), {}) == ()


# --- The transaction ---------------------------------------------------------

class _TransactionConnection(_FakeConnection):
    """Scripted reads for one incremental publish, recording every statement."""

    def __init__(self, *, stored_before_rows, issuer_rows, batch_rows):
        super().__init__(one_rows=(("b",), ("b",)))
        cursor = self.cursor_instance
        reads = [tuple(stored_before_rows), tuple(issuer_rows), tuple(batch_rows)]

        def fetchall():
            cursor.events.append(("fetchall",))
            return list(reads.pop(0))

        cursor.fetchall = fetchall


def _transaction(monkeypatch, store, incoming, cutoff, *, returned=None):
    inputs, batches = store.publish(incoming, cutoff)
    connection = _TransactionConnection(
        stored_before_rows=[_database_row(fact) for fact in inputs["pre_insert"].values()],
        issuer_rows=[_database_row(fact) for fact in inputs["stored"]],
        batch_rows=list(batches.values()),
    )
    inserted = inputs["returned"] if returned is None else returned

    def insert_returning(cursor, rows):
        cursor.events.append(("insert_returning", INSERT_FACTS_RETURNING_SQL, len(rows)))
        return [_database_row(fact) for fact in inserted]

    monkeypatch.setattr("src.fundamentals.incremental_publication._insert_returning", insert_returning)
    return connection


def test_the_offline_run_publishes_incrementally_by_default(monkeypatch):
    from src.fundamentals import sec_pipeline_command

    calls = []

    def fake_publish(facts, **kwargs):
        calls.append(kwargs)
        return IncrementalPublicationReceipt(
            batch_ids=("b",), written_batch_ids=(), inserted_by_batch=(), reused_by_batch=()
        )

    monkeypatch.setattr(sec_pipeline_command, "publish_incremental", fake_publish)
    monkeypatch.setattr(sec_pipeline_command, "run_sec_ingestion_dry_run", lambda **kwargs: _run(_utc(2024, 1, 1), fetcher=InstanceSource(DOCUMENTS)))
    request = sec_pipeline_command.OfflineSecIngestionRequest(
        cik=CIK, knowledge_cutoff=_utc(2024, 1, 1), ingestion_batch_id="b", publish=True,
        history_frozen_through=HISTORICAL,
    )

    result = sec_pipeline_command.run_offline_sec_ingestion(request, downloader=None)

    assert result.publish_result.publication.is_no_op
    assert calls == [{"knowledge_cutoff": _utc(2024, 1, 1), "history_frozen_through": HISTORICAL, "database_url": None}]


def test_the_transaction_serializes_before_any_schema_statement(monkeypatch, history):
    from src.fundamentals.incremental_publication import (
        LOCK_PUBLICATIONS_SQL,
        PUBLISH_LOCK_WAIT_MS,
        SET_LOCAL_STATEMENT_TIMEOUT_SQL,
    )
    from src.fundamentals.store import PUBLISH_STATEMENT_TIMEOUT_MS

    incoming = _publication(_utc(2024, 1, 1), "refresh-4", _utc(2024, 1, 2))
    connection = _transaction(monkeypatch, history, incoming, _utc(2024, 1, 1))

    publish_incremental(incoming, knowledge_cutoff=_utc(2024, 1, 1), conn=connection)

    executed = [event[1:] for event in connection.events if event[0] == "execute"]
    # The lock wait runs under its own timeout, before ensure_schema's DDL.
    assert executed[:3] == [
        (SET_LOCAL_STATEMENT_TIMEOUT_SQL, (str(PUBLISH_LOCK_WAIT_MS),)),
        (LOCK_PUBLICATIONS_SQL, None),
        (SET_LOCAL_STATEMENT_TIMEOUT_SQL, (str(PUBLISH_STATEMENT_TIMEOUT_MS),)),
    ]
    assert "CREATE" in executed[3][0]


def test_the_transaction_reads_the_pre_insert_state_before_inserting_and_commits_once(monkeypatch, history):
    incoming = _publication(_utc(2024, 1, 1), "refresh-4", _utc(2024, 1, 2))
    connection = _transaction(monkeypatch, history, incoming, _utc(2024, 1, 1))

    receipt = publish_incremental(incoming, knowledge_cutoff=_utc(2024, 1, 1), conn=connection)

    assert (receipt.inserted_fact_count, receipt.reused_fact_count) == (2, 6)
    kinds = [event[0] if event[0] != "execute" else event[1] for event in connection.events]
    insert_at = kinds.index("insert_returning")
    issuer_reads = [index for index, kind in enumerate(kinds) if kind == SELECT_ISSUER_FACTS_SQL]
    assert issuer_reads[0] < insert_at < issuer_reads[1]
    assert kinds.index(SELECT_BATCH_ROWS_SQL) > insert_at
    assert connection.events.count(("commit",)) == 1
    assert ("rollback",) not in connection.events


def test_a_failed_proof_rolls_the_transaction_back(monkeypatch, history):
    incoming = _publication(_utc(2024, 1, 1), "refresh-4", _utc(2024, 1, 2))
    connection = _transaction(monkeypatch, history, incoming, _utc(2024, 1, 1), returned=[])

    with pytest.raises(IncrementalPublicationError, match="neither inserted nor already stored"):
        publish_incremental(incoming, knowledge_cutoff=_utc(2024, 1, 1), conn=connection)

    assert ("rollback",) in connection.events
    assert ("commit",) not in connection.events


def test_the_ingestion_result_reports_the_refusal_and_inserts_nothing(monkeypatch, history):
    run = _run(_utc(2024, 1, 1), fetcher=InstanceSource(DOCUMENTS))
    incoming = _rebatch(run.classified_facts, "refresh-4", _utc(2024, 1, 2))
    connection = _transaction(monkeypatch, history, incoming, _utc(2024, 1, 1), returned=[])
    rebatched = replace(run, classified_facts=incoming)

    result = publish_sec_ingestion_dry_run(
        rebatched,
        publisher=lambda facts: publish_incremental(facts, knowledge_cutoff=_utc(2024, 1, 1), conn=connection),
    )

    assert not result.is_complete
    assert result.inserted_fact_count == 0 and result.publication is None
    assert "neither inserted nor already stored" in result.issues[0].message


def test_the_ingestion_result_carries_the_receipt(monkeypatch, history):
    run = _run(_utc(2024, 1, 1), fetcher=InstanceSource(DOCUMENTS))
    incoming = _rebatch(run.classified_facts, "refresh-4", _utc(2024, 1, 2))
    connection = _transaction(monkeypatch, history, incoming, _utc(2024, 1, 1))

    result = publish_sec_ingestion_dry_run(
        replace(run, classified_facts=incoming),
        publisher=lambda facts: publish_incremental(facts, knowledge_cutoff=_utc(2024, 1, 1), conn=connection),
    )

    assert result.is_complete
    assert isinstance(result.publication, IncrementalPublicationReceipt)
    assert result.inserted_fact_count == result.publication.inserted_fact_count == 2


def test_more_than_one_issuer_refuses_before_connecting(monkeypatch):
    incoming = _publication(_utc(2023, 6, 1), "backfill-1", _utc(2023, 6, 2))
    other = replace(
        incoming[0],
        identity=replace(incoming[0].identity, context=replace(incoming[0].identity.context, entity_cik="0000000001")),
    )
    monkeypatch.setattr("src.fundamentals.store._connect", lambda *a, **k: pytest.fail("must not connect"))

    with pytest.raises(FundamentalsPublishError, match="exactly one issuer"):
        publish_incremental(incoming + (other,), knowledge_cutoff=_utc(2023, 6, 1), database_url="unused")


# --- Post-publication refresh verification ----------------------------------

def _verify_refresh(store, batch_id, publish_cutoff, historical=(HISTORICAL,)):
    return sec_backfill_verification.verify_refresh_publication(
        cik=CIK,
        ingestion_batch_id=batch_id,
        publish_cutoff=publish_cutoff,
        historical_cutoffs=historical,
        data_vintage_cutoff=_utc(2027, 1, 1),
        downloader=_Downloader(),
        repository=InMemoryFundamentalsRepository(tuple(store.facts.values())),
        batch_reader=lambda ids: {key: row for key, row in store.batches.items() if key in set(ids)},
        batch_fact_reader=store.fact_ciks,
    )


def test_refresh_verification_separates_inserted_facts_from_earlier_batches(composing_issuer, history):
    history.commit(_publication(_utc(2024, 1, 1), "refresh-4", _utc(2024, 1, 2)), _utc(2024, 1, 1))

    result = _verify_refresh(history, "refresh-4", _utc(2024, 1, 1))

    assert result.is_verified, (result.publication_problems, [item.problems for item in result.cutoffs])
    assert not result.is_no_op
    historical, publish = result.cutoffs
    assert (historical.historical, historical.this_refresh_fact_count, historical.earlier_batch_fact_count) == (True, 0, 4)
    assert (publish.this_refresh_fact_count, publish.earlier_batch_fact_count) == (2, 6)
    summary = sec_backfill_verification._refresh_summary(result)
    assert summary["cutoffs"][1]["facts_inserted_by_this_refresh"] == 2
    assert summary["cutoffs"][1]["facts_in_earlier_batches"] == 6


def test_a_no_op_writes_no_batch_and_verification_says_so(composing_issuer, history):
    batches_before = dict(history.batches)
    receipt = history.commit(_publication(_utc(2023, 10, 1), "refresh-3", _utc(2023, 10, 2)), _utc(2023, 10, 1))

    assert receipt.is_no_op and not receipt.batch_written
    assert history.batches == batches_before

    result = _verify_refresh(history, "refresh-3", _utc(2023, 10, 1))

    # Only the issuer's stored result is verified; no batch is claimed.
    assert result.is_verified, (result.publication_problems, [item.problems for item in result.cutoffs])
    assert result.is_no_op and not result.batch_rows_present
    summary = sec_backfill_verification._refresh_summary(result)
    assert summary["no_op"] is True and summary["batch_rows_present"] is False


def test_the_backfill_verifier_rejects_a_valid_refresh_which_is_why_refresh_mode_exists(composing_issuer, history):
    history.commit(_publication(_utc(2024, 1, 1), "refresh-4", _utc(2024, 1, 2)), _utc(2024, 1, 1))

    result = sec_backfill_verification.verify_published_backfill(
        cik=CIK,
        ingestion_batch_id="refresh-4",
        knowledge_cutoffs=(_utc(2024, 1, 1),),
        data_vintage_cutoff=_utc(2027, 1, 1),
        downloader=_Downloader(),
        repository=InMemoryFundamentalsRepository(tuple(history.facts.values())),
    )

    assert not result.is_verified


def test_refresh_verification_rejects_refresh_facts_visible_at_the_historical_cutoff(composing_issuer, history):
    # A Q1 fact filed under the new refresh batch would change 2024-09-03-style history.
    history.commit(_publication(_utc(2024, 1, 1), "refresh-4", _utc(2024, 1, 2)), _utc(2024, 1, 1))
    key, fact = next(
        (key, fact) for key, fact in history.facts.items()
        if fact.lineage.ingestion_batch_id == "backfill-1"
    )
    history.facts[key] = replace(
        fact, lineage=replace(fact.lineage, ingestion_batch_id="refresh-4", ingested_at=_utc(2024, 1, 2))
    )

    result = _verify_refresh(history, "refresh-4", _utc(2024, 1, 1))

    assert not result.is_verified
    assert any("historical cutoff" in problem for problem in result.cutoffs[0].problems)


def test_refresh_verification_rejects_an_unpaired_companion_batch(composing_issuer, history):
    history.commit(_publication(_utc(2024, 1, 1), "refresh-4", _utc(2024, 1, 2)), _utc(2024, 1, 1))
    del history.batches["refresh-4"]

    result = _verify_refresh(history, "refresh-4", _utc(2024, 1, 1))

    assert not result.is_verified
    assert result.publication_problems


def test_refresh_verification_rejects_a_missing_fact(composing_issuer, history):
    history.commit(_publication(_utc(2024, 1, 1), "refresh-4", _utc(2024, 1, 2)), _utc(2024, 1, 1))
    history.facts.pop(next(key for key, fact in history.facts.items() if fact.lineage.ingestion_batch_id == "refresh-4"))

    result = _verify_refresh(history, "refresh-4", _utc(2024, 1, 1))

    assert not result.is_verified
    assert any("1 missing" in problem for problem in result.cutoffs[-1].problems)


def test_refresh_verification_requires_historical_cutoffs_before_the_publish_cutoff(composing_issuer, history):
    with pytest.raises(ValueError, match="precede"):
        _verify_refresh(history, "refresh-2", _utc(2023, 9, 1), historical=(_utc(2023, 9, 1),))


def test_refresh_mode_cli_requires_a_publish_cutoff(monkeypatch, capsys):
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    code = sec_backfill_verification.main(
        ["--cik", CIK, "--batch-id", "b", "--cutoff", "2024-09-03T16:00:00-04:00", "--mode", "refresh"]
    )

    assert code == 1
    assert "--publish-cutoff" in capsys.readouterr().out


def test_ingested_at_is_part_of_the_pairing(history):
    later = dt.timedelta(seconds=1)
    rows = dict(history.batches)
    companion = rows["refresh-2+sec_filing_xbrl"]
    rows["refresh-2+sec_filing_xbrl"] = companion[:4] + (companion[4] + later,)

    assert publication_batch_problems(["refresh-2", "refresh-2+sec_filing_xbrl"], rows)


# --- Cross-issuer substitution ----------------------------------------------

def _foreign_no_op_batch_rows(store, batch_id):
    """Batch rows another issuer's no-op refresh left behind: rows, but no facts.

    The batch table carries no issuer, and the mapping and calendar versions
    are copied from this issuer's rows, so nothing in the rows says whose they are.
    """

    primary = store.batches["refresh-2"]
    companion = store.batches["refresh-2+sec_filing_xbrl"]
    ingested_at = _utc(2023, 10, 3)
    store.batches[batch_id] = (batch_id,) + primary[1:4] + (ingested_at,)
    store.batches[f"{batch_id}+sec_filing_xbrl"] = (f"{batch_id}+sec_filing_xbrl",) + companion[1:4] + (ingested_at,)


def test_another_issuers_zero_fact_batch_does_not_verify_as_this_issuers_no_op(composing_issuer, history):
    _foreign_no_op_batch_rows(history, "msft-sec-9-1")

    result = _verify_refresh(history, "msft-sec-9-1", _utc(2023, 10, 1))

    assert not result.is_verified
    assert any("issuer" in problem for problem in result.publication_problems)


def test_another_issuers_batch_with_facts_does_not_verify_as_this_issuers_refresh(composing_issuer, history):
    # MSFT's refresh inserted facts; none belong to this issuer, so every one
    # of this issuer's facts sits in an earlier batch and would look like a no-op.
    foreign_cik = "0000789019"
    incoming = _publication(_utc(2024, 1, 1), "msft-sec-8-1", _utc(2024, 1, 2))
    fact = incoming[-1]
    foreign = replace(
        fact, identity=replace(fact.identity, context=replace(fact.identity.context, entity_cik=foreign_cik))
    )
    history.facts[("foreign",)] = foreign
    history.batches.update(
        {key[0]: key for key in _publication_batch_keys(incoming)}
    )

    result = _verify_refresh(history, "msft-sec-8-1", _utc(2023, 10, 1))

    assert not result.is_verified
    assert any("other issuers: 0000789019" in problem for problem in result.publication_problems)


def test_issuer_binding_rules_directly():
    t = _utc(2024, 1, 1)
    primary = ("b", "sec_companyfacts", "map", "cal", t)
    companion = ("b+sec_filing_xbrl", XBRL, "map", "cal", t)
    both = {"b": primary, "b+sec_filing_xbrl": companion}

    assert publication_issuer_problems(CIK, {"b": primary}, {"b": {CIK: 3}}) == ()
    # A primary with no facts is bound through its paired supplement's facts.
    assert publication_issuer_problems(CIK, both, {"b+sec_filing_xbrl": {CIK: 1}}) == ()
    # A supplement with no facts is never bound, even beside a primary that has facts.
    assert "b+sec_filing_xbrl hold no facts" in publication_issuer_problems(CIK, both, {"b": {CIK: 1}})[0]
    assert "no facts" in publication_issuer_problems(CIK, {"b": primary}, {})[0]
    assert "other issuers" in publication_issuer_problems(CIK, {"b": primary}, {"b": {CIK: 1, "0000000002": 1}})[0]


def test_only_sources_with_new_facts_get_batch_rows():
    # Only the Company Facts fact is new: no filing-XBRL batch row is written,
    # so no zero-fact supplemental batch can ever be committed.
    store = _Store()
    full = _publication(_utc(2023, 9, 1), "backfill-1", _utc(2023, 9, 2))
    new_primary_fact = next(fact for fact in full if fact.lineage.source_adapter != XBRL and fact.period.period_end.month == 7)
    store.commit(tuple(fact for fact in full if fact is not new_primary_fact), _utc(2023, 9, 1))

    receipt = store.commit(_publication(_utc(2023, 9, 1), "refresh-2", _utc(2023, 9, 2)), _utc(2023, 9, 1))

    assert receipt.written_batch_ids == ("refresh-2",)
    assert "refresh-2+sec_filing_xbrl" not in store.batches


def test_a_new_supplemental_fact_writes_its_primary_row_to_pair_with(composing_issuer):
    # Only the composed fact is new: the primary row is written without facts
    # so the supplement can pair; it is bound through the supplement's facts.
    store = _Store()
    full = _publication(_utc(2023, 9, 1), "backfill-1", _utc(2023, 9, 2))
    new_composed_fact = next(fact for fact in full if fact.lineage.source_adapter == XBRL and fact.period.period_end.month == 7)
    store.commit(tuple(fact for fact in full if fact is not new_composed_fact), _utc(2023, 9, 1))

    receipt = store.commit(_publication(_utc(2023, 9, 1), "refresh-2", _utc(2023, 9, 2)), _utc(2023, 9, 1))

    assert receipt.written_batch_ids == ("refresh-2", "refresh-2+sec_filing_xbrl")
    assert dict(receipt.inserted_by_batch) == {"refresh-2": 0, "refresh-2+sec_filing_xbrl": 1}
    result = _verify_refresh(store, "refresh-2", _utc(2023, 9, 1))
    assert result.is_verified, (result.publication_problems, [item.problems for item in result.cutoffs])


def test_a_committed_zero_fact_supplement_beside_a_fact_bearing_primary_does_not_verify(composing_issuer, history):
    # The review's case: summing counts across batches would hide an empty supplement.
    history.commit(_publication(_utc(2024, 1, 1), "refresh-4", _utc(2024, 1, 2)), _utc(2024, 1, 1))
    key = next(key for key, fact in history.facts.items() if fact.lineage.ingestion_batch_id == "refresh-4+sec_filing_xbrl")
    del history.facts[key]

    result = _verify_refresh(history, "refresh-4", _utc(2024, 1, 1))

    assert not result.is_verified
    assert any("refresh-4+sec_filing_xbrl hold no facts" in problem for problem in result.publication_problems)


def test_the_proof_refuses_to_commit_a_zero_fact_supplement(history):
    # A transaction that wrote a supplemental row but inserted nothing into it.
    inputs, _ = history.publish(_publication(_utc(2024, 1, 1), "refresh-4", _utc(2024, 1, 2)), _utc(2024, 1, 1))
    inputs["returned"] = [fact for fact in inputs["returned"] if fact.lineage.source_adapter != XBRL]
    inputs["pre_insert"] = {
        **inputs["pre_insert"],
        **{
            _source_identity_key(fact): fact
            for fact in _publication(_utc(2024, 1, 1), "refresh-4", _utc(2024, 1, 2))
            if fact.lineage.source_adapter == XBRL and _source_identity_key(fact) not in inputs["pre_insert"]
        },
    }

    _refuses(inputs, "hold no facts")


def test_selection_is_independent_of_input_order(history):
    from src.fundamentals.incremental_publication import point_in_time_selection

    facts = list(history.facts.values())
    assert point_in_time_selection(facts, _utc(2024, 1, 1), CIK) == point_in_time_selection(
        list(reversed(facts)), _utc(2024, 1, 1), CIK
    )
