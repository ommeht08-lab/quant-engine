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
    read_batch_rows,
)
from src.fundamentals.store import INSERT_BATCH_SQL, PostgresFundamentalsRepository, _execute_values, _fact_to_row
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
    for receipt in receipts:
        for batch_id, inserted in receipt.inserted_by_batch:
            assert stored[batch_id] == inserted
    # A no-op still records its two batch rows, with no facts in them.
    assert stored["refresh-3"] == stored["refresh-3+sec_filing_xbrl"] == 0
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
    assert retry.inserted_fact_count == 0 and retry.reused_fact_count == 6


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
        )

    ten_k = verify("refresh-4", _utc(2024, 1, 1))
    no_op = verify("refresh-3", _utc(2023, 10, 1))

    assert ten_k.is_verified, (ten_k.publication_problems, [item.problems for item in ten_k.cutoffs])
    assert (ten_k.cutoffs[-1].this_refresh_fact_count, ten_k.cutoffs[-1].earlier_batch_fact_count) == (2, 6)
    assert ten_k.cutoffs[0].this_refresh_fact_count == 0
    assert no_op.is_verified and no_op.is_no_op
