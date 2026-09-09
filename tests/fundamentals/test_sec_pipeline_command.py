import datetime as dt

from src.fundamentals.adapters.sec_downloader import SecIssuerPayload
from src.fundamentals.sec_pipeline_command import (
    OfflineSecIngestionRequest,
    run_offline_sec_ingestion,
)


CIK = "0000320193"
CUTOFF = dt.datetime(2024, 1, 1, tzinfo=dt.timezone.utc)


class FakeDownloader:
    def __init__(self, payload):
        self.payload = payload

    def fetch_issuer(self, cik):
        assert cik == CIK
        return self.payload


def _payload():
    periods = (
        ("Q1", "2022-09-25", "2022-12-31", 100, "10-Q", "2023-02-01"),
        ("Q2", "2023-01-01", "2023-04-01", 110, "10-Q", "2023-05-01"),
        ("Q3", "2023-04-02", "2023-07-01", 120, "10-Q", "2023-08-01"),
        ("FY", "2022-09-25", "2023-09-30", 500, "10-K", "2023-11-01"),
    )
    tags = {
        "RevenueFromContractWithCustomerExcludingAssessedTax": 1,
        "OperatingIncomeLoss": 0.3,
        "PaymentsToAcquirePropertyPlantAndEquipment": 0.1,
        "NetCashProvidedByUsedInOperatingActivities": 0.4,
    }
    facts = {}
    accession_numbers = []
    filing_dates = []
    acceptance_times = []
    report_dates = []
    forms = []
    primary_documents = []
    for tag, multiplier in tags.items():
        entries = []
        for index, (label, start, end, value, form, filed) in enumerate(periods):
            accession = f"0000320193-23-{index + 1:06d}"
            entries.append(
                {
                    "start": start,
                    "end": end,
                    "val": int(value * multiplier),
                    "accn": accession,
                    "fy": 2023,
                    "fp": label,
                    "form": form,
                    "filed": filed,
                }
            )
            if tag == next(iter(tags)):
                accession_numbers.append(accession)
                filing_dates.append(filed)
                acceptance_times.append(f"{filed}T10:00:00Z")
                report_dates.append(end)
                forms.append(form)
                primary_documents.append(f"aapl-{label.lower()}.htm")
        facts[tag] = {"units": {"USD": entries}}
    submissions = {
        "cik": CIK,
        "filings": {
            "recent": {
                "accessionNumber": accession_numbers,
                "filingDate": filing_dates,
                "acceptanceDateTime": acceptance_times,
                "reportDate": report_dates,
                "form": forms,
                "primaryDocument": primary_documents,
            },
            "files": [],
        },
    }
    return SecIssuerPayload(
        cik=CIK,
        company_facts={
            "cik": 320193,
            "entityName": "Apple Inc.",
            "facts": {"us-gaap": facts},
        },
        submissions=(submissions,),
        company_facts_url=f"https://data.sec.gov/api/xbrl/companyfacts/CIK{CIK}.json",
        submission_urls=(f"https://data.sec.gov/submissions/CIK{CIK}.json",),
        downloaded_at=dt.datetime(2026, 9, 9, tzinfo=dt.timezone.utc),
    )


def _request(*, publish=False):
    return OfflineSecIngestionRequest(
        cik=CIK,
        knowledge_cutoff=CUTOFF,
        ingestion_batch_id="offline-sec-001",
        publish=publish,
    )


def test_offline_command_is_dry_run_only_by_default():
    calls = []

    result = run_offline_sec_ingestion(
        _request(),
        downloader=FakeDownloader(_payload()),
        publisher=lambda facts: calls.append(facts),
    )

    assert result.is_complete
    assert result.publish_result is None
    assert calls == []
    assert len(result.dry_run.classified_facts) == 16


def test_explicit_publish_uses_only_the_completed_verified_batch():
    published = []

    def publisher(facts):
        published.append(facts)
        return len(facts)

    result = run_offline_sec_ingestion(
        _request(publish=True),
        downloader=FakeDownloader(_payload()),
        publisher=publisher,
    )

    assert result.is_complete
    assert result.publish_result.inserted_fact_count == 16
    assert published == [result.dry_run.classified_facts]
