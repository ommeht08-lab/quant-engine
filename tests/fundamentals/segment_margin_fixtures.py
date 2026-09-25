"""CAT ME&T supplemental-table fixtures shared by the segment-margin tests.

The HTML excerpts under tests/fixtures/sec_filing_document are cut from the
real filings named below; the filing metadata is copied from SEC's
submissions JSON for CIK 0000018230.
"""

from dataclasses import replace
from datetime import date, datetime, timezone
from pathlib import Path

from src.fundamentals.adapters.sec_filing_document import (
    FilingDocumentReference,
    extract_supplemental_facts,
)
from src.fundamentals.calendar_catalog import SEC_FISCAL_CALENDAR_CATALOG_V1
from src.fundamentals.segment_gross_margin import CAT_MET_SUPPLEMENTAL_RULE

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "sec_filing_document"
CAT_CALENDAR = SEC_FISCAL_CALENDAR_CATALOG_V1.policy_for("0000018230")
INGESTED_AT = datetime(2026, 9, 25, 12, tzinfo=timezone.utc)
BATCH = "segment-test-1+sec_filing_document"
PILOT_CUTOFF = datetime(2024, 9, 3, 20, tzinfo=timezone.utc)  # 16:00 ET


def reference(accession, form, filed, accepted, report, document):
    return FilingDocumentReference(
        cik="0000018230",
        entity_name="CATERPILLAR INC",
        accession_number=accession,
        form_type=form,
        filed_date=filed,
        accepted_at=accepted,
        report_date=report,
        document_name=document,
        document_url=f"https://www.sec.gov/Archives/edgar/data/18230/{accession.replace('-', '')}/{document}",
    )


FY2023_10K = reference(
    "0000018230-24-000009", "10-K", date(2024, 2, 16),
    datetime(2024, 2, 16, 15, 5, 13, tzinfo=timezone.utc), date(2023, 12, 31), "cat-20231231.htm",
)
Q2_2023_10Q = reference(
    "0000018230-23-000047", "10-Q", date(2023, 8, 2),
    datetime(2023, 8, 2, 14, 16, 35, tzinfo=timezone.utc), date(2023, 6, 30), "cat-20230630.htm",
)
Q2_2024_10Q = reference(
    "0000018230-24-000045", "10-Q", date(2024, 8, 7),
    datetime(2024, 8, 7, 14, 40, 25, tzinfo=timezone.utc), date(2024, 6, 30), "cat-20240630.htm",
)
FIXTURE_FILE = {
    FY2023_10K.accession_number: "cat-20231231-supplemental.htm",
    Q2_2023_10Q.accession_number: "cat-20230630-supplemental.htm",
    Q2_2024_10Q.accession_number: "cat-20240630-supplemental.htm",
}


def document(filing) -> bytes:
    return (FIXTURES / FIXTURE_FILE[filing.accession_number]).read_bytes()


def facts_for(filing, *, ingested_at=INGESTED_AT, batch=BATCH, source=None):
    return extract_supplemental_facts(
        document(source or filing),
        CAT_MET_SUPPLEMENTAL_RULE,
        filing=filing,
        calendar_policy=CAT_CALENDAR,
        ingestion_batch_id=batch,
        ingested_at=ingested_at,
    )


def pilot_facts(**kwargs):
    return tuple(fact for filing in (FY2023_10K, Q2_2023_10Q, Q2_2024_10Q) for fact in facts_for(filing, **kwargs))


def with_value(fact, value, **provenance):
    return replace(fact, value=value, provenance=replace(fact.provenance, **provenance))
