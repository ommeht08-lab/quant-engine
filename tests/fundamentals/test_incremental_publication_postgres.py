"""Real PostgreSQL contract tests for incremental (refresh) publication.

Like ``test_store_postgres``, these run only in the dedicated GitHub Actions
step against the synthetic loopback database; the shared ``empty_store``
fixture truncates it before and after each test.
"""

import psycopg2
import pytest

from src.fundamentals import sec_backfill_verification
from src.fundamentals.incremental_publication import (
    IncrementalPublicationError,
    prove_incremental_publication,
    publish_incremental,
    read_batch_fact_ciks,
    read_batch_rows,
)
from src.fundamentals.store import (
    INSERT_BATCH_SQL,
    PostgresFundamentalsRepository,
    _execute_values,
    _fact_to_row,
    _source_identity_key as _identity,
)
from tests.fundamentals.test_incremental_publication import XBRL, _publication, _rebatch
from tests.fundamentals.test_sec_filing_xbrl_ingestion import _utc
from tests.fundamentals import test_sec_filing_xbrl_publication as _xbrl_publication
from tests.fundamentals import test_store_postgres as _store_postgres
from tests.fundamentals.test_sec_filing_xbrl_publication import _Downloader
from tests.fundamentals.test_sec_ingestion import CIK
from tests.fundamentals.test_store_postgres import DATABASE_URL, _store_contents

# Shared fixtures: the truncated loopback store and the two-source issuer policy.
empty_store = _store_postgres.empty_store
composing_issuer = _xbrl_publication.composing_issuer

pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="requires the dedicated loopback PostgreSQL integration database",
)

BACKFILL = (_utc(2023, 6, 1), "backfill-1", _utc(2023, 6, 2))
NEW_FILING = (_utc(2023, 9, 1), "refresh-2", _utc(2023, 9, 2))
NO_OP = (_utc(2023, 10, 1), "refresh-3", _utc(2023, 10, 2))
TEN_K = (_utc(2024, 1, 1), "refresh-4", _utc(2024, 1, 2))


def _publish(step, **kwargs):
    cutoff, batch_id, ingested_at = step
    return publish_incremental(
        _publication(cutoff, batch_id, ingested_at), knowledge_cutoff=cutoff, database_url=DATABASE_URL, **kwargs
    )


def _facts_by_batch():
    batches, facts = _store_contents()
    counts = {batch_id: 0 for batch_id, _ in batches}
    for batch_id, *_ in facts:
        counts[batch_id] += 1
    return counts


def test_backfill_new_filing_no_op_and_ten_k_prove_exactly_what_each_inserted(empty_store):
    receipts = [_publish(step) for step in (BACKFILL, NEW_FILING, NO_OP, TEN_K)]

    assert [(r.inserted_fact_count, r.reused_fact_count) for r in receipts] == [(4, 0), (2, 4), (0, 6), (2, 6)]
    assert receipts[2].is_no_op
    # The receipts' per-batch inserted counts are exactly what the database holds.
    stored = _facts_by_batch()
    for receipt in (r for r in receipts if r.batch_written):
        for batch_id, inserted in receipt.inserted_by_batch:
            assert stored[batch_id] == inserted
    # A no-op is rolled back: it records no batch rows at all.
    assert "refresh-3" not in stored and "refresh-3+sec_filing_xbrl" not in stored
    assert dict(receipts[3].reused_by_batch) == {
        "backfill-1": 2,
        "backfill-1+sec_filing_xbrl": 2,
        "refresh-2": 1,
        "refresh-2+sec_filing_xbrl": 1,
    }


def test_an_injected_mismatch_rolls_back_every_batch_row_and_fact(empty_store):
    _publish(BACKFILL)
    before = _store_contents()

    def silently_skipped(**inputs):
        return prove_incremental_publication(**dict(inputs, returned=inputs["returned"][:-1]))

    with pytest.raises(IncrementalPublicationError, match="neither inserted nor already stored"):
        _publish(NEW_FILING, prove=silently_skipped)

    assert _store_contents() == before


def test_a_retry_of_the_same_publication_inserts_nothing(empty_store):
    first = _publish(NEW_FILING)
    retry = _publish(NEW_FILING)

    assert first.inserted_fact_count == 6
    # A same-batch retry replays its own facts; nothing is attributed to an earlier batch.
    assert retry.inserted_fact_count == 0 and retry.is_no_op
    assert retry.replayed_fact_count == 6 and retry.reused_fact_count == 0


def _raw_insert(batch_rows, facts):
    """Write rows the publishers would refuse, bypassing every publication check."""

    connection = psycopg2.connect(DATABASE_URL)
    try:
        with connection.cursor() as cursor:
            for row in batch_rows:
                cursor.execute(INSERT_BATCH_SQL, row)
            _execute_values(cursor, [_fact_to_row(fact) for fact in facts])
        connection.commit()
    finally:
        connection.close()


def test_a_companion_batch_the_foreign_key_accepts_but_no_primary_pairs_with_rolls_back(empty_store):
    # "legacy+sec_filing_xbrl" has its own batch row, so the foreign key is
    # satisfied, but its primary "legacy" was ingested at a different time.
    cutoff, _, _ = BACKFILL
    companion = tuple(
        fact for fact in _rebatch(_publication(*BACKFILL), "legacy", _utc(2023, 6, 3)) if fact.lineage.source_adapter == XBRL
    )
    sample = companion[0].lineage
    _raw_insert(
        [
            ("legacy", "sec_companyfacts", sample.concept_map_version, sample.fiscal_calendar_version, _utc(2023, 6, 2)),
            ("legacy+sec_filing_xbrl", XBRL, sample.concept_map_version, sample.fiscal_calendar_version, _utc(2023, 6, 3)),
        ],
        companion,
    )
    before = _store_contents()

    with pytest.raises(IncrementalPublicationError, match="lack a matching primary batch"):
        _publish(BACKFILL)

    assert _store_contents() == before


def test_refresh_verification_reads_postgres_by_lineage(composing_issuer, empty_store):
    for step in (BACKFILL, NEW_FILING, NO_OP, TEN_K):
        _publish(step)

    def verify(batch_id, publish_cutoff):
        return sec_backfill_verification.verify_refresh_publication(
            cik=CIK,
            ingestion_batch_id=batch_id,
            publish_cutoff=publish_cutoff,
            historical_cutoffs=(_utc(2023, 6, 1),),
            data_vintage_cutoff=_utc(2027, 1, 1),
            downloader=_Downloader(),
            repository=PostgresFundamentalsRepository(database_url=DATABASE_URL),
            batch_reader=lambda ids: read_batch_rows(ids, database_url=DATABASE_URL),
            batch_fact_reader=lambda ids: read_batch_fact_ciks(ids, database_url=DATABASE_URL),
        )

    ten_k = verify("refresh-4", _utc(2024, 1, 1))
    no_op = verify("refresh-3", _utc(2023, 10, 1))

    assert ten_k.is_verified, (ten_k.publication_problems, [item.problems for item in ten_k.cutoffs])
    assert (ten_k.cutoffs[-1].this_refresh_fact_count, ten_k.cutoffs[-1].earlier_batch_fact_count) == (2, 6)
    assert ten_k.cutoffs[0].this_refresh_fact_count == 0
    assert no_op.is_verified and no_op.is_no_op and not no_op.batch_rows_present


def test_a_refresh_that_would_rewrite_frozen_history_rolls_back(empty_store):
    # Only the Q2 facts are stored, so a refresh would insert Q1 facts that are
    # visible at the frozen 2023-06-01 cutoff.
    partial = tuple(fact for fact in _publication(*BACKFILL) if fact.period.period_end.year == 2023)
    publish_incremental(partial, knowledge_cutoff=_utc(2023, 5, 2), database_url=DATABASE_URL)
    before = _store_contents()

    with pytest.raises(IncrementalPublicationError, match="frozen historical cutoff"):
        _publish(NEW_FILING, history_frozen_through=_utc(2023, 6, 1))

    assert _store_contents() == before


def test_overlapping_publishes_for_one_issuer_serialize_instead_of_refusing(empty_store):
    """Two overlapping publishes both succeed; the second waits and reuses the first's facts.

    The issuer advisory lock guarantees this. Today ensure_schema's DDL also
    happens to block behind the first transaction, so this test alone cannot
    distinguish the two; the lock keeps the guarantee if that DDL moves out.
    """
    import threading
    import time

    first_holds_lock = threading.Event()
    results = {}

    def slow_proof(**inputs):
        first_holds_lock.set()
        time.sleep(0.5)
        return prove_incremental_publication(**inputs)

    def first():
        results["first"] = _publish(NEW_FILING, prove=slow_proof)

    thread = threading.Thread(target=first)
    thread.start()
    assert first_holds_lock.wait(5)
    # Same facts under another batch ID, started while the first transaction
    # still holds the issuer lock with its inserts uncommitted.
    started = time.monotonic()
    results["second"] = _publish((NEW_FILING[0], "refresh-2b", NEW_FILING[2]))
    waited = time.monotonic() - started
    thread.join(5)

    assert results["first"].inserted_fact_count == 6
    assert results["second"].inserted_fact_count == 0
    assert results["second"].reused_fact_count == 6
    assert waited >= 0.3


def _verify_refresh_in_postgres(batch_id, publish_cutoff):
    return sec_backfill_verification.verify_refresh_publication(
        cik=CIK,
        ingestion_batch_id=batch_id,
        publish_cutoff=publish_cutoff,
        historical_cutoffs=(_utc(2023, 6, 1),),
        data_vintage_cutoff=_utc(2027, 1, 1),
        downloader=_Downloader(),
        repository=PostgresFundamentalsRepository(database_url=DATABASE_URL),
        batch_reader=lambda ids: read_batch_rows(ids, database_url=DATABASE_URL),
        batch_fact_reader=lambda ids: read_batch_fact_ciks(ids, database_url=DATABASE_URL),
    )


def test_another_issuers_zero_fact_batch_does_not_verify_in_postgres(composing_issuer, empty_store):
    for step in (BACKFILL, NEW_FILING):
        _publish(step)
    # A legacy no-op refresh for another issuer: batch rows with no facts. The
    # rows carry no issuer, and here even share this issuer's versions.
    sample = _publication(*NEW_FILING)[0].lineage
    _raw_insert(
        [
            ("msft-sec-9-1", "sec_companyfacts", sample.concept_map_version, sample.fiscal_calendar_version, _utc(2023, 10, 3)),
            ("msft-sec-9-1+sec_filing_xbrl", XBRL, sample.concept_map_version, sample.fiscal_calendar_version, _utc(2023, 10, 3)),
        ],
        (),
    )

    result = _verify_refresh_in_postgres("msft-sec-9-1", _utc(2023, 10, 1))

    assert not result.is_verified
    assert any("issuer" in problem for problem in result.publication_problems)


def test_another_issuers_batch_with_facts_does_not_verify_in_postgres(composing_issuer, empty_store):
    for step in (BACKFILL, NEW_FILING):
        _publish(step)
    # Another issuer's refresh that did insert facts, under versions equal to this issuer's.
    from dataclasses import replace

    fact = _publication(*TEN_K)[0]
    foreign = replace(
        fact,
        identity=replace(fact.identity, context=replace(fact.identity.context, entity_cik="0000789019")),
        lineage=replace(fact.lineage, ingestion_batch_id="msft-sec-8-1"),
    )
    lineage = foreign.lineage
    _raw_insert(
        [("msft-sec-8-1", lineage.source_adapter, lineage.concept_map_version, lineage.fiscal_calendar_version, lineage.ingested_at)],
        (foreign,),
    )

    result = _verify_refresh_in_postgres("msft-sec-8-1", _utc(2023, 10, 1))

    assert not result.is_verified
    assert any("other issuers: 0000789019" in problem for problem in result.publication_problems)


def test_a_legacy_zero_fact_batch_of_this_issuer_is_not_claimed_verified(composing_issuer, empty_store):
    # Before this change a no-op still committed its batch rows. Even when the
    # name suggests this issuer, nothing stored ties the rows to it.
    for step in (BACKFILL, NEW_FILING):
        _publish(step)
    sample = _publication(*NEW_FILING)[0].lineage
    _raw_insert(
        [
            ("apple-sec-7-1", "sec_companyfacts", sample.concept_map_version, sample.fiscal_calendar_version, _utc(2023, 10, 3)),
            ("apple-sec-7-1+sec_filing_xbrl", XBRL, sample.concept_map_version, sample.fiscal_calendar_version, _utc(2023, 10, 3)),
        ],
        (),
    )

    result = _verify_refresh_in_postgres("apple-sec-7-1", _utc(2023, 10, 1))

    assert not result.is_verified
    assert any("cannot establish their issuer" in problem for problem in result.publication_problems)


def _batch_rows_in_postgres():
    return {batch_id for batch_id, _ in _store_contents()[0]}


def test_a_refresh_with_only_new_company_facts_writes_no_supplemental_row(composing_issuer, empty_store):
    full = _publication(*NEW_FILING)
    new_primary = next(f for f in full if f.lineage.source_adapter != XBRL and f.period.period_end.month == 7)
    publish_incremental(
        tuple(f for f in _publication(NEW_FILING[0], "backfill-1", NEW_FILING[2]) if _identity(f) != _identity(new_primary)),
        knowledge_cutoff=NEW_FILING[0],
        database_url=DATABASE_URL,
    )

    receipt = _publish((NEW_FILING[0], "refresh-2", _utc(2023, 9, 3)))

    assert receipt.written_batch_ids == ("refresh-2",)
    assert "refresh-2+sec_filing_xbrl" not in _batch_rows_in_postgres()
    assert _verify_refresh_in_postgres("refresh-2", NEW_FILING[0]).is_verified


def test_a_refresh_with_only_a_new_composed_fact_binds_its_primary_through_the_supplement(composing_issuer, empty_store):
    full = _publication(*NEW_FILING)
    new_composed = next(f for f in full if f.lineage.source_adapter == XBRL and f.period.period_end.month == 7)
    publish_incremental(
        tuple(f for f in _publication(NEW_FILING[0], "backfill-1", NEW_FILING[2]) if _identity(f) != _identity(new_composed)),
        knowledge_cutoff=NEW_FILING[0],
        database_url=DATABASE_URL,
    )

    receipt = _publish((NEW_FILING[0], "refresh-2", _utc(2023, 9, 3)))

    assert receipt.written_batch_ids == ("refresh-2", "refresh-2+sec_filing_xbrl")
    assert dict(receipt.inserted_by_batch) == {"refresh-2": 0, "refresh-2+sec_filing_xbrl": 1}
    result = _verify_refresh_in_postgres("refresh-2", NEW_FILING[0])
    assert result.is_verified, (result.publication_problems, [item.problems for item in result.cutoffs])


def test_a_committed_empty_supplement_beside_a_fact_bearing_primary_does_not_verify(composing_issuer, empty_store):
    # Rows an older publisher could have committed: the primary holds facts,
    # the supplement holds none. Summing across the pair would hide it.
    for step in (BACKFILL, NEW_FILING):
        _publish(step)
    from dataclasses import replace

    primary_fact = next(f for f in _publication(*TEN_K) if f.lineage.source_adapter != XBRL and f.period.period_end.year == 2023 and f.period.period_end.month == 9)
    lineage = replace(primary_fact.lineage, ingestion_batch_id="legacy-5")
    _raw_insert(
        [
            ("legacy-5", lineage.source_adapter, lineage.concept_map_version, lineage.fiscal_calendar_version, lineage.ingested_at),
            ("legacy-5+sec_filing_xbrl", XBRL, lineage.concept_map_version, lineage.fiscal_calendar_version, lineage.ingested_at),
        ],
        (replace(primary_fact, lineage=lineage),),
    )

    result = _verify_refresh_in_postgres("legacy-5", _utc(2024, 1, 1))

    assert not result.is_verified
    assert any("legacy-5+sec_filing_xbrl hold no facts" in problem for problem in result.publication_problems)
