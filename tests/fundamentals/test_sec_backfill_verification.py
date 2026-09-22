import dataclasses
import datetime as dt
import json

from src.fundamentals import sec_backfill_verification
from src.fundamentals.repository import InMemoryFundamentalsRepository
from src.fundamentals.sec_backfill_verification import verify_published_backfill
from src.fundamentals.sec_pipeline_command import (
    OfflineSecIngestionRequest,
    run_offline_sec_ingestion,
)
from tests.fundamentals.test_sec_pipeline_command import CIK, FakeDownloader, _payload

BATCH_ID = "backfill-320193-001"
PUBLISH_CUTOFF = dt.datetime(2024, 1, 1, tzinfo=dt.timezone.utc)
# Only the Q1 and Q2 10-Qs (accepted February 1 and May 1, 2023) were public.
EARLY_CUTOFF = dt.datetime(2023, 6, 1, tzinfo=dt.timezone.utc)
DATA_VINTAGE_CUTOFF = dt.datetime(2026, 9, 22, tzinfo=dt.timezone.utc)


def _published_facts():
    published = []

    def publisher(facts):
        published.extend(facts)
        return len(facts)

    result = run_offline_sec_ingestion(
        OfflineSecIngestionRequest(
            cik=CIK,
            knowledge_cutoff=PUBLISH_CUTOFF,
            ingestion_batch_id=BATCH_ID,
            publish=True,
        ),
        downloader=FakeDownloader(_payload()),
        publisher=publisher,
    )
    assert result.is_complete
    return tuple(published)


def _verify(repository, *, batch_id=BATCH_ID, cutoffs=(EARLY_CUTOFF, PUBLISH_CUTOFF)):
    return verify_published_backfill(
        cik=CIK,
        ingestion_batch_id=batch_id,
        knowledge_cutoffs=cutoffs,
        data_vintage_cutoff=DATA_VINTAGE_CUTOFF,
        downloader=FakeDownloader(_payload()),
        repository=repository,
    )


class CutoffIgnoringRepository:
    """Simulates a read path that failed to apply the knowledge cutoff."""

    def __init__(self, facts):
        self.facts = tuple(facts)

    def get_facts(self, query):
        return self.facts


def test_current_backfill_reproduces_both_historical_cutoffs():
    result = _verify(InMemoryFundamentalsRepository(_published_facts()))

    assert result.is_verified
    early, current = result.cutoffs
    assert early.knowledge_cutoff == EARLY_CUTOFF
    assert early.expected_fact_count == early.published_fact_count == 8
    assert current.expected_fact_count == current.published_fact_count == 16
    assert result.concept_map_version == "sec-companyfacts-v2"


def test_missing_published_fact_fails_verification():
    facts = _published_facts()

    result = _verify(InMemoryFundamentalsRepository(facts[1:]))

    assert not result.is_verified
    assert any("1 missing" in problem for cutoff in result.cutoffs for problem in cutoff.problems)


def test_fact_public_after_the_cutoff_fails_verification():
    result = _verify(
        CutoffIgnoringRepository(_published_facts()),
        cutoffs=(EARLY_CUTOFF,),
    )

    assert not result.is_verified
    assert any("after the cutoff" in problem for problem in result.cutoffs[0].problems)


def test_facts_from_another_batch_fail_verification():
    result = _verify(InMemoryFundamentalsRepository(_published_facts()), batch_id="other-batch")

    assert not result.is_verified
    assert "other ingestion batches" in result.cutoffs[0].problems[0]


def test_changed_filing_availability_fails_verification():
    facts = _published_facts()
    shifted = tuple(
        dataclasses.replace(
            fact,
            provenance=dataclasses.replace(
                fact.provenance,
                accepted_at=fact.provenance.accepted_at - dt.timedelta(hours=1),
            ),
        )
        for fact in facts
    )

    result = _verify(InMemoryFundamentalsRepository(shifted))

    assert not result.is_verified


def test_command_fails_closed_without_sec_credentials(monkeypatch, capsys):
    monkeypatch.delenv("SEC_USER_AGENT", raising=False)

    exit_code = sec_backfill_verification.main(
        ["--cik", "320193", "--batch-id", BATCH_ID, "--cutoff", "2024-09-03T16:00:00-04:00"]
    )

    assert exit_code == 1
    assert json.loads(capsys.readouterr().out)["status"] == "failed"
