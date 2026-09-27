"""CAT ME&T supplemental-table fixtures shared by the segment-margin tests.

The HTML excerpts under tests/fixtures/sec_filing_document are cut from the
real filings named below (each excerpt records its source URL and the full
document's SHA-256); the filing metadata is copied from SEC's submissions
JSON for CIK 0000018230.
"""

from dataclasses import replace
from datetime import date, datetime, timezone
from pathlib import Path

from src.fundamentals.adapters.sec_filing_document import (
    FilingDocumentReference,
    extract_supplemental_facts,
)
from src.fundamentals.calendar_catalog import SEC_FISCAL_CALENDAR_CATALOG_V1
from src.fundamentals.segment_gross_margin import CAT_SEGMENT_MARGIN_SOURCE

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
# 2020 10-Qs in EDGAR's older layout: a footnoted ME&T column header and
# negatives split across two cells ("(1" then ")").
Q1_2020_10Q = reference(
    "0000018230-20-000155", "10-Q", date(2020, 5, 6),
    datetime(2020, 5, 6, 17, 9, 58, tzinfo=timezone.utc), date(2020, 3, 31), "cat10q3312020.htm",
)
Q2_2020_10Q = reference(
    "0000018230-20-000214", "10-Q", date(2020, 8, 5),
    datetime(2020, 8, 5, 14, 50, 24, tzinfo=timezone.utc), date(2020, 6, 30), "cat10q6302020.htm",
)
# Filings behind the Q1, Q3, and annual margin-selection tests.
Q3_2022_10Q = reference(
    "0000018230-22-000223", "10-Q", date(2022, 11, 2),
    datetime(2022, 11, 2, 14, 37, 8, tzinfo=timezone.utc), date(2022, 9, 30), "cat-20220930.htm",
)
FY2022_10K = reference(
    "0000018230-23-000011", "10-K", date(2023, 2, 15),
    datetime(2023, 2, 15, 15, 27, 56, tzinfo=timezone.utc), date(2022, 12, 31), "cat-20221231.htm",
)
Q1_2023_10Q = reference(
    "0000018230-23-000022", "10-Q", date(2023, 5, 3),
    datetime(2023, 5, 3, 14, 19, 47, tzinfo=timezone.utc), date(2023, 3, 31), "cat-20230331.htm",
)
Q3_2023_10Q = reference(
    "0000018230-23-000056", "10-Q", date(2023, 11, 1),
    datetime(2023, 11, 1, 14, 3, 2, tzinfo=timezone.utc), date(2023, 9, 30), "cat-20230930.htm",
)
Q1_2024_10Q = reference(
    "0000018230-24-000020", "10-Q", date(2024, 5, 1),
    datetime(2024, 5, 1, 14, 5, 24, tzinfo=timezone.utc), date(2024, 3, 31), "cat-20240331.htm",
)
FIXTURE_FILE = {
    FY2023_10K.accession_number: "cat-20231231-supplemental.htm",
    Q2_2023_10Q.accession_number: "cat-20230630-supplemental.htm",
    Q2_2024_10Q.accession_number: "cat-20240630-supplemental.htm",
}
# The last ME&T filings and the first MP&E filings (CAT renamed ME&T to
# "Machinery, Power & Energy" from its FY2025 10-K), cut from the copies
# captured from SEC at 2026-09-27T18:54Z.
FY2024_10K = reference(
    "0000018230-25-000008", "10-K", date(2025, 2, 14),
    datetime(2025, 2, 14, 14, 36, 30, tzinfo=timezone.utc), date(2024, 12, 31), "cat-20241231.htm",
)
Q1_2025_10Q = reference(
    "0000018230-25-000016", "10-Q", date(2025, 5, 7),
    datetime(2025, 5, 7, 13, 31, 50, tzinfo=timezone.utc), date(2025, 3, 31), "cat-20250331.htm",
)
Q2_2025_10Q = reference(
    "0000018230-25-000040", "10-Q", date(2025, 8, 6),
    datetime(2025, 8, 6, 13, 35, 30, tzinfo=timezone.utc), date(2025, 6, 30), "cat-20250630.htm",
)
Q3_2025_10Q = reference(
    "0000018230-25-000048", "10-Q", date(2025, 11, 3),
    datetime(2025, 11, 3, 16, 6, 23, tzinfo=timezone.utc), date(2025, 9, 30), "cat-20250930.htm",
)
FY2025_10K = reference(
    "0000018230-26-000008", "10-K", date(2026, 2, 13),
    datetime(2026, 2, 13, 15, 18, 27, tzinfo=timezone.utc), date(2025, 12, 31), "cat-20251231.htm",
)
Q1_2026_10Q = reference(
    "0000018230-26-000021", "10-Q", date(2026, 5, 6),
    datetime(2026, 5, 6, 15, 8, 9, tzinfo=timezone.utc), date(2026, 3, 31), "cat-20260331.htm",
)
Q2_2026_10Q = reference(
    "0000018230-26-000046", "10-Q", date(2026, 8, 5),
    datetime(2026, 8, 5, 12, 43, 40, tzinfo=timezone.utc), date(2026, 6, 30), "cat-20260630.htm",
)
# Kept apart from FIXTURE_FILE, which is exactly the pilot's three filings.
MORE_FIXTURE_FILES = {
    Q1_2020_10Q.accession_number: "cat-20200331-supplemental.htm",
    Q2_2020_10Q.accession_number: "cat-20200630-supplemental.htm",
    Q3_2022_10Q.accession_number: "cat-20220930-supplemental.htm",
    FY2022_10K.accession_number: "cat-20221231-supplemental.htm",
    Q1_2023_10Q.accession_number: "cat-20230331-supplemental.htm",
    Q3_2023_10Q.accession_number: "cat-20230930-supplemental.htm",
    Q1_2024_10Q.accession_number: "cat-20240331-supplemental.htm",
    FY2024_10K.accession_number: "cat-20241231-supplemental.htm",
    Q1_2025_10Q.accession_number: "cat-20250331-supplemental.htm",
    Q2_2025_10Q.accession_number: "cat-20250630-supplemental.htm",
    Q3_2025_10Q.accession_number: "cat-20250930-supplemental.htm",
    FY2025_10K.accession_number: "cat-20251231-supplemental.htm",
    Q1_2026_10Q.accession_number: "cat-20260331-supplemental.htm",
    Q2_2026_10Q.accession_number: "cat-20260630-supplemental.htm",
}


def document(filing) -> bytes:
    name = FIXTURE_FILE.get(filing.accession_number) or MORE_FIXTURE_FILES[filing.accession_number]
    return (FIXTURES / name).read_bytes()


def rule_for(filing):
    """The reviewed layout (ME&T or MP&E) covering the filing's report date."""

    return CAT_SEGMENT_MARGIN_SOURCE.basis_for(filing.report_date).rule


def facts_for(filing, *, ingested_at=INGESTED_AT, batch=BATCH, source=None, document_bytes=None, rule=None):
    return extract_supplemental_facts(
        document_bytes if document_bytes is not None else document(source or filing),
        rule or rule_for(filing),
        filing=filing,
        calendar_policy=CAT_CALENDAR,
        ingestion_batch_id=batch,
        ingested_at=ingested_at,
    )


def pilot_facts(**kwargs):
    return tuple(fact for filing in (FY2023_10K, Q2_2023_10Q, Q2_2024_10Q) for fact in facts_for(filing, **kwargs))


def with_value(fact, value, **provenance):
    return replace(fact, value=value, provenance=replace(fact.provenance, **provenance))
