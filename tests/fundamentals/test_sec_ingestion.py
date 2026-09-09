import copy
import datetime as dt

import pytest

from src.fundamentals.adapters.sec_downloader import (
    SecDownloadError,
    SecDownloadErrorCode,
    SecIssuerPayload,
)
from src.fundamentals.concept_map import SEC_CONCEPT_MAP_V1
from src.fundamentals.fiscal_calendar import FiscalYearDefinition, IssuerFiscalCalendarPolicy
from src.fundamentals.quarterly import QuarterValueOrigin
from src.fundamentals.sec_ingestion import (
    SecDryRunIssueStage,
    publish_sec_ingestion_dry_run,
    run_sec_ingestion_dry_run,
)
from src.fundamentals.store import FundamentalsPublishError

CIK = "0000320193"
DOWNLOADED_AT = dt.datetime(2026, 9, 8, tzinfo=dt.timezone.utc)

PERIODS = (
    ("Q1", "2022-10-02", "2022-12-31", 100, "0000320193-23-000001", "10-Q", "2023-02-01", "2023-02-01T10:00:00Z"),
    ("Q2", "2023-01-01", "2023-04-01", 110, "0000320193-23-000002", "10-Q", "2023-05-01", "2023-05-01T10:00:00Z"),
    ("Q3", "2023-04-02", "2023-07-01", 120, "0000320193-23-000003", "10-Q", "2023-08-01", "2023-08-01T10:00:00Z"),
    ("FY", "2022-10-02", "2023-09-30", 500, "0000320193-23-000009", "10-K", "2023-11-01", "2023-11-01T10:00:00Z"),
)


class FakeDownloader:
    def __init__(self, payload=None, error=None):
        self.payload = payload
        self.error = error
        self.calls = []

    def fetch_issuer(self, cik):
        self.calls.append(cik)
        if self.error is not None:
            raise self.error
        return self.payload


def _policy():
    return IssuerFiscalCalendarPolicy(
        cik=CIK,
        version="apple-calendar-v1",
        fiscal_years=(
            FiscalYearDefinition(
                fiscal_year=2023,
                period_start=dt.date(2022, 10, 2),
                quarter_ends=(
                    dt.date(2022, 12, 31),
                    dt.date(2023, 4, 1),
                    dt.date(2023, 7, 1),
                    dt.date(2023, 9, 30),
                ),
            ),
        ),
    )


def _payload(periods=PERIODS):
    entries = []
    columns = {
        "accessionNumber": [],
        "filingDate": [],
        "acceptanceDateTime": [],
        "reportDate": [],
        "form": [],
        "primaryDocument": [],
    }
    for label, start, end, value, accession, form, filed, accepted in periods:
        entries.append(
            {
                "start": start,
                "end": end,
                "val": value,
                "accn": accession,
                "fy": 2023,
                "fp": label,
                "form": form,
                "filed": filed,
            }
        )
        columns["accessionNumber"].append(accession)
        columns["filingDate"].append(filed)
        columns["acceptanceDateTime"].append(accepted)
        columns["reportDate"].append(end)
        columns["form"].append(form)
        columns["primaryDocument"].append(f"aapl-{label.lower()}.htm")
    company_facts = {
        "cik": 320193,
        "entityName": "Apple Inc.",
        "facts": {
            "us-gaap": {
                "RevenueFromContractWithCustomerExcludingAssessedTax": {
                    "units": {"USD": entries}
                }
            }
        },
    }
    submissions = {"cik": CIK, "filings": {"recent": columns, "files": []}}
    return SecIssuerPayload(
        cik=CIK,
        company_facts=company_facts,
        submissions=(submissions,),
        company_facts_url=f"https://data.sec.gov/api/xbrl/companyfacts/CIK{CIK}.json",
        submission_urls=(f"https://data.sec.gov/submissions/CIK{CIK}.json",),
        downloaded_at=DOWNLOADED_AT,
    )


def _run(downloader, *, cutoff=dt.datetime(2024, 1, 1, tzinfo=dt.timezone.utc)):
    return run_sec_ingestion_dry_run(
        downloader=downloader,
        cik=CIK,
        calendar_policy=_policy(),
        concept_map=SEC_CONCEPT_MAP_V1,
        ingestion_batch_id="dry-run-001",
        knowledge_cutoff=cutoff,
        required_concepts=("revenue",),
    )


class TestCompleteDryRun:
    def test_composes_download_extraction_classification_selection_and_assembly(self):
        downloader = FakeDownloader(_payload())

        result = _run(downloader)

        assert result.is_complete
        assert downloader.calls == [CIK]
        assert result.extracted_fact_count == 4
        assert result.eligible_fact_count == 4
        assert [quarter.value for quarter in result.quarterly.quarters] == [100, 110, 120, 170]
        assert result.quarterly.quarters[-1].origin is QuarterValueOrigin.DERIVED_Q4
        assert result.quarterly.ttm_values[0].value == 500
        assert result.history.knowledge_cutoff == dt.datetime(
            2024, 1, 1, tzinfo=dt.timezone.utc
        )

    def test_acceptance_cutoff_precedes_calendar_consistency_checks(self):
        future_invalid_period = (
            "Q2",
            "2024-01-01",
            "2024-05-05",
            999,
            "0000320193-25-000002",
            "10-Q",
            "2025-06-01",
            "2025-06-01T10:00:00Z",
        )
        downloader = FakeDownloader(_payload((PERIODS[0], future_invalid_period)))

        result = _run(
            downloader,
            cutoff=dt.datetime(2023, 3, 1, tzinfo=dt.timezone.utc),
        )

        assert result.is_complete
        assert result.extracted_fact_count == 1
        assert result.eligible_fact_count == 1
        assert [quarter.fiscal_quarter for quarter in result.quarterly.quarters] == [1]
        assert result.quarterly.ttm_values == ()


class TestDryRunRefusals:
    def test_invalid_required_concepts_fail_before_downloading(self):
        downloader = FakeDownloader(_payload())

        try:
            run_sec_ingestion_dry_run(
                downloader=downloader,
                cik=CIK,
                calendar_policy=_policy(),
                concept_map=SEC_CONCEPT_MAP_V1,
                ingestion_batch_id="dry-run-001",
                knowledge_cutoff=dt.datetime(2024, 1, 1, tzinfo=dt.timezone.utc),
                required_concepts=(),
            )
        except ValueError as error:
            assert "required_concepts" in str(error)
        else:
            raise AssertionError("invalid required_concepts must raise")

        assert downloader.calls == []

    def test_overlapping_optional_concepts_fail_before_downloading(self):
        downloader = FakeDownloader(_payload())

        with pytest.raises(ValueError, match="must not overlap"):
            run_sec_ingestion_dry_run(
                downloader=downloader,
                cik=CIK,
                calendar_policy=_policy(),
                concept_map=SEC_CONCEPT_MAP_V1,
                ingestion_batch_id="dry-run-001",
                knowledge_cutoff=dt.datetime(2024, 1, 1, tzinfo=dt.timezone.utc),
                required_concepts=("revenue",),
                optional_concepts=("revenue",),
            )

        assert downloader.calls == []

    def test_download_failure_is_typed_and_returns_no_partial_output(self):
        downloader = FakeDownloader(
            error=SecDownloadError(
                SecDownloadErrorCode.NETWORK_FAILURE,
                "SEC request failed after bounded retries.",
            )
        )

        result = _run(downloader)

        assert not result.is_complete
        assert result.history is None
        assert result.quarterly is None
        assert result.issues[0].stage is SecDryRunIssueStage.DOWNLOAD
        assert result.issues[0].code == "network_failure"

    def test_extraction_failure_returns_no_partial_output(self):
        payload = _payload()
        malformed = copy.deepcopy(payload.company_facts)
        malformed["entityName"] = ""
        payload = SecIssuerPayload(
            cik=payload.cik,
            company_facts=malformed,
            submissions=payload.submissions,
            company_facts_url=payload.company_facts_url,
            submission_urls=payload.submission_urls,
            downloaded_at=payload.downloaded_at,
        )

        result = _run(FakeDownloader(payload))

        assert result.issues[0].stage is SecDryRunIssueStage.EXTRACTION
        assert result.issues[0].code == "invalid_payload"

    def test_missing_closed_year_quarter_refuses_the_assembly(self):
        result = _run(FakeDownloader(_payload((PERIODS[0], PERIODS[2], PERIODS[3]))))

        assert result.issues[0].stage is SecDryRunIssueStage.QUARTERLY_ASSEMBLY
        assert result.issues[0].code == "missing_period"
        assert result.history is None
        assert result.quarterly is None

    def test_no_fact_public_by_cutoff_is_an_explicit_refusal(self):
        result = _run(
            FakeDownloader(_payload()),
            cutoff=dt.datetime(2022, 1, 1, tzinfo=dt.timezone.utc),
        )

        assert result.issues[0].stage is SecDryRunIssueStage.CUTOFF
        assert result.issues[0].code == "no_eligible_facts"
        assert result.extracted_fact_count == 0
        assert result.eligible_fact_count == 0


class TestDryRunPublishing:
    def test_publishes_the_complete_classified_batch_once(self):
        dry_run = _run(FakeDownloader(_payload()))
        published = []

        def publisher(facts):
            published.append(facts)
            return len(facts)

        result = publish_sec_ingestion_dry_run(dry_run, publisher=publisher)

        assert result.is_complete
        assert result.inserted_fact_count == len(dry_run.classified_facts)
        assert published == [dry_run.classified_facts]

    def test_idempotent_replay_may_insert_zero_facts(self):
        dry_run = _run(FakeDownloader(_payload()))

        result = publish_sec_ingestion_dry_run(dry_run, publisher=lambda facts: 0)

        assert result.is_complete
        assert result.inserted_fact_count == 0

    def test_atomic_publish_failure_is_sanitized(self):
        dry_run = _run(FakeDownloader(_payload()))

        def publisher(facts):
            raise FundamentalsPublishError("postgresql://secret@host/database")

        result = publish_sec_ingestion_dry_run(dry_run, publisher=publisher)

        assert not result.is_complete
        assert result.inserted_fact_count == 0
        assert result.issues[0].stage is SecDryRunIssueStage.PUBLISH
        assert result.issues[0].code == "publish_failed"
        assert "secret" not in result.issues[0].message

    def test_refused_dry_run_cannot_reach_the_publisher(self):
        dry_run = _run(
            FakeDownloader(_payload()),
            cutoff=dt.datetime(2022, 1, 1, tzinfo=dt.timezone.utc),
        )
        calls = []

        try:
            publish_sec_ingestion_dry_run(
                dry_run,
                publisher=lambda facts: calls.append(facts),
            )
        except ValueError as error:
            assert "complete" in str(error)
        else:
            raise AssertionError("an incomplete dry run must not be publishable")

        assert calls == []

    @pytest.mark.parametrize("invalid_count", (True, -1, 5))
    def test_publisher_must_report_a_possible_insert_count(self, invalid_count):
        dry_run = _run(FakeDownloader(_payload()))

        with pytest.raises(ValueError, match="inserted fact count"):
            publish_sec_ingestion_dry_run(
                dry_run,
                publisher=lambda facts: invalid_count,
            )
