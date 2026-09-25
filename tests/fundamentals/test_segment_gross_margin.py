"""Point-in-time CAT ME&T gross margins: cutoffs, amendments, conflicts, gaps."""

from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from src.fundamentals.adapters.sec_downloader import SecIssuerPayload
from src.fundamentals.repository import InMemoryFundamentalsRepository
from src.fundamentals.segment_gross_margin import (
    CAT_MET_SUPPLEMENTAL_RULE,
    SEGMENT_COST_OF_GOODS_SOLD,
    SEGMENT_SALES,
    SegmentGrossMarginRefusal,
    load_segment_gross_margin_pair,
    run_segment_document_dry_run,
    segment_gross_margin_rule_for,
)
from tests.fundamentals.segment_margin_fixtures import (
    CAT_CALENDAR,
    FIXTURE_FILE,
    FIXTURES,
    FY2023_10K,
    INGESTED_AT,
    PILOT_CUTOFF,
    Q2_2023_10Q,
    Q2_2024_10Q,
    facts_for,
    pilot_facts,
    reference,
    with_value,
)

VINTAGE = INGESTED_AT + timedelta(hours=1)
LATEST, PRIOR = date(2024, 6, 30), date(2023, 6, 30)

# Independent oracle: PR #56's manual transcription (USD millions), taken from
# the FY2023 10-K and the Q2 2023/Q2 2024 earnings 8-K exhibits. The source
# reads the 10-K/10-Q documents instead; both must agree exactly.
PR56_TRANSCRIPTION = {
    (SEGMENT_SALES, date(2022, 12, 31)): 56574, (SEGMENT_COST_OF_GOODS_SOLD, date(2022, 12, 31)): 41356,
    (SEGMENT_SALES, date(2023, 12, 31)): 63869, (SEGMENT_COST_OF_GOODS_SOLD, date(2023, 12, 31)): 42776,
    (SEGMENT_SALES, date(2022, 6, 30)): 26425, (SEGMENT_COST_OF_GOODS_SOLD, date(2022, 6, 30)): 19538,
    (SEGMENT_SALES, date(2023, 6, 30)): 31644, (SEGMENT_COST_OF_GOODS_SOLD, date(2023, 6, 30)): 21172,
    (SEGMENT_SALES, date(2024, 6, 30)): 30800, (SEGMENT_COST_OF_GOODS_SOLD, date(2024, 6, 30)): 19816,
}


def _pair(facts, *, cutoff=PILOT_CUTOFF, vintage=VINTAGE, latest=LATEST, prior=PRIOR, repository=None):
    return load_segment_gross_margin_pair(
        repository or InMemoryFundamentalsRepository(facts),
        rule=CAT_MET_SUPPLEMENTAL_RULE,
        fiscal_calendar_version=CAT_CALENDAR.version,
        knowledge_cutoff=cutoff,
        data_vintage_cutoff=vintage,
        latest_end=latest,
        prior_end=prior,
    )


def _find(facts, concept, start, end):
    return next(
        fact for fact in facts
        if fact.identity.concept == concept and (fact.identity.period_start, fact.identity.period_end) == (start, end)
    )


def test_pilot_cutoff_reproduces_both_trailing_year_margins_exactly():
    pair = _pair(pilot_facts())

    # TTM Jun 2024 = FY2023 + H1 2024 - H1 2023; TTM Jun 2023 likewise.
    assert pair.current == Decimal(63025 - 41420) / Decimal(63025)
    assert pair.prior == Decimal(61793 - 42990) / Decimal(61793)
    assert pair.current > pair.prior
    assert (pair.latest_end, pair.prior_end) == (LATEST, PRIOR)
    record = pair.audit_record()
    assert record["filing_accessions"] == [
        "0000018230-23-000047", "0000018230-24-000009", "0000018230-24-000045",
    ]
    assert record["ingestion_batch_ids"] == ["segment-test-1+sec_filing_document"]
    assert record["dimension"] == ["srt:ProductOrServiceAxis", "cat:MachineryEnergyTransportationMember"]


def test_every_component_matches_the_independent_manual_transcription():
    pair = _pair(pilot_facts())
    read = {(item.concept, item.period_end): item.value / Decimal(1_000_000) for item in pair.components}
    assert read == PR56_TRANSCRIPTION
    for item in pair.components:
        assert item.accepted_at <= PILOT_CUTOFF
        assert item.source_document_url.startswith("https://www.sec.gov/Archives/edgar/data/18230/")


def test_h1_2023_uses_the_latest_filing_and_lists_the_agreeing_earlier_one():
    pair = _pair(pilot_facts())
    h1_2023 = [item for item in pair.components if item.period_end == date(2023, 6, 30)]
    assert {item.accession_number for item in h1_2023} == {"0000018230-24-000045"}
    assert all(item.corroborating_accessions == ("0000018230-23-000047",) for item in h1_2023)
    assert all(item.superseded == () for item in h1_2023)


# --------------------------------------------------------------------------
# Cutoffs: nothing accepted or ingested later can leak backward
# --------------------------------------------------------------------------


def test_one_microsecond_before_the_q2_2024_10q_refuses_and_at_acceptance_succeeds():
    before = Q2_2024_10Q.accepted_at - timedelta(microseconds=1)
    with pytest.raises(SegmentGrossMarginRefusal, match="missing"):
        _pair(pilot_facts(), cutoff=before)
    assert _pair(pilot_facts(), cutoff=Q2_2024_10Q.accepted_at).current > 0


def test_earlier_formation_date_uses_only_filings_public_then():
    # In this fixture set FY2022 comes only from the FY2023 10-K, which was
    # not public at the Q2 2023 10-Q's acceptance: refuse, never borrow it.
    with pytest.raises(SegmentGrossMarginRefusal, match="missing"):
        _pair(pilot_facts(), cutoff=Q2_2023_10Q.accepted_at, latest=date(2023, 6, 30), prior=date(2022, 6, 30))


def test_facts_ingested_after_the_data_vintage_cutoff_are_invisible():
    with pytest.raises(SegmentGrossMarginRefusal, match="missing"):
        _pair(pilot_facts(), vintage=INGESTED_AT - timedelta(microseconds=1))


class _LeakyRepository:
    """A defective repository that ignores both cutoffs."""

    def __init__(self, facts):
        self._facts = tuple(facts)

    def get_facts(self, query):
        return self._facts


def test_a_repository_returning_a_post_cutoff_fact_refuses_instead_of_filtering():
    amended = with_value(
        _find(facts_for(Q2_2024_10Q), SEGMENT_COST_OF_GOODS_SOLD, date(2024, 1, 1), date(2024, 6, 30)),
        Decimal(1),
        accession_number="0000018230-24-000099",
        accepted_at=datetime(2024, 10, 1, tzinfo=timezone.utc),
    )
    with pytest.raises(SegmentGrossMarginRefusal, match="outside the cutoffs"):
        _pair((), repository=_LeakyRepository(pilot_facts() + (amended,)))


def test_an_out_of_scope_fact_from_the_repository_refuses():
    fact = pilot_facts()[0]
    consolidated = replace(fact, identity=replace(fact.identity, context=replace(fact.identity.context, dimensions=())))
    with pytest.raises(SegmentGrossMarginRefusal, match="out-of-scope"):
        _pair((), repository=_LeakyRepository(pilot_facts() + (consolidated,)))


# --------------------------------------------------------------------------
# Amendments and conflicts
# --------------------------------------------------------------------------


def _amended_h1_2024_cost(value, accepted_at, accession="0000018230-24-000099"):
    original = _find(facts_for(Q2_2024_10Q), SEGMENT_COST_OF_GOODS_SOLD, date(2024, 1, 1), date(2024, 6, 30))
    return with_value(
        original, value, accession_number=accession, form_type="10-Q/A", is_amendment=True,
        filed_date=accepted_at.date(), accepted_at=accepted_at,
    )


def test_an_amendment_changes_the_margin_only_after_its_acceptance():
    amendment = _amended_h1_2024_cost(Decimal(20_000_000_000), datetime(2024, 10, 1, 12, tzinfo=timezone.utc))
    facts = pilot_facts() + (amendment,)

    at_pilot = _pair(facts)
    assert at_pilot.current == Decimal(21605) / Decimal(63025)

    later = _pair(facts, cutoff=datetime(2024, 10, 2, tzinfo=timezone.utc))
    assert later.current == Decimal(63025 - 41604) / Decimal(63025)
    cost = next(i for i in later.components if i.concept == SEGMENT_COST_OF_GOODS_SOLD and i.period_end == LATEST)
    assert cost.accession_number == "0000018230-24-000099" and cost.form_type == "10-Q/A"
    assert cost.superseded == (("0000018230-24-000045", Decimal(19_816_000_000)),)


def test_disagreeing_values_within_one_filing_refuse():
    original = _find(facts_for(Q2_2024_10Q), SEGMENT_SALES, date(2024, 1, 1), date(2024, 6, 30))
    duplicate = replace(original, value=original.value + 1, raw_tag=original.raw_tag + "|table=99")
    with pytest.raises(SegmentGrossMarginRefusal, match="Conflicting"):
        _pair(pilot_facts() + (duplicate,))


def test_disagreeing_filings_accepted_at_the_same_instant_refuse():
    simultaneous = _amended_h1_2024_cost(Decimal(20_000_000_000), Q2_2024_10Q.accepted_at)
    with pytest.raises(SegmentGrossMarginRefusal, match="simultaneous"):
        _pair(pilot_facts() + (simultaneous,))


# --------------------------------------------------------------------------
# Missing data
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "concept, start, end",
    [
        (SEGMENT_SALES, date(2022, 1, 1), date(2022, 12, 31)),  # prior annual
        (SEGMENT_COST_OF_GOODS_SOLD, date(2024, 1, 1), date(2024, 6, 30)),  # latest YTD
        (SEGMENT_COST_OF_GOODS_SOLD, date(2022, 1, 1), date(2022, 6, 30)),  # year-ago YTD
    ],
)
def test_any_missing_component_refuses(concept, start, end):
    facts = tuple(f for f in pilot_facts() if (f.identity.concept, f.identity.period_start, f.identity.period_end) != (concept, start, end))
    with pytest.raises(SegmentGrossMarginRefusal, match="missing"):
        _pair(facts)


def test_no_published_segment_facts_refuses():
    with pytest.raises(SegmentGrossMarginRefusal, match="missing"):
        _pair(())


def test_invalid_trailing_sales_refuses():
    facts = tuple(
        with_value(f, Decimal(0)) if f.identity.concept == SEGMENT_SALES and f.period.fiscal_period == "FY" else f
        for f in pilot_facts()
    )
    with pytest.raises(SegmentGrossMarginRefusal, match="invalid"):
        _pair(facts)


def test_only_cat_has_a_segment_gross_margin_rule():
    assert segment_gross_margin_rule_for("18230") is CAT_MET_SUPPLEMENTAL_RULE
    for cik in ("320193", "789019", "104169"):
        assert segment_gross_margin_rule_for(cik) is None


# --------------------------------------------------------------------------
# Dry run: submissions-driven discovery, nothing published
# --------------------------------------------------------------------------


def _submissions(*rows):
    names = ("accessionNumber", "filingDate", "acceptanceDateTime", "reportDate", "form", "primaryDocument")
    return {"cik": "18230", "name": "CATERPILLAR INC", "filings": {"recent": {n: [r[i] for r in rows] for i, n in enumerate(names)}}}


def _row(filing):
    return (
        filing.accession_number, filing.filed_date.isoformat(),
        filing.accepted_at.isoformat().replace("+00:00", ".000Z"), filing.report_date.isoformat(),
        filing.form_type, filing.document_name,
    )


class _FakeDownloader:
    def __init__(self, rows, documents):
        self._payload = SecIssuerPayload(
            cik="18230", company_facts={}, submissions=(_submissions(*rows),),
            company_facts_url="https://data.sec.gov/api/xbrl/companyfacts/CIK0000018230.json",
            submission_urls=("https://data.sec.gov/submissions/CIK0000018230.json",),
            downloaded_at=INGESTED_AT,
        )
        self._documents = documents
        self.fetched = []

    def fetch_issuer(self, cik):
        return self._payload

    def fetch_filing_document(self, cik, accession, name):
        self.fetched.append(accession)
        url = f"https://www.sec.gov/Archives/edgar/data/18230/{accession.replace('-', '')}/{name}"
        return url, self._documents[accession]


def _documents():
    return {accession: (FIXTURES / name).read_bytes() for accession, name in FIXTURE_FILE.items()}


def _dry_run(downloader, cutoff=PILOT_CUTOFF):
    return run_segment_document_dry_run(
        downloader=downloader, rule=CAT_MET_SUPPLEMENTAL_RULE, calendar_policy=CAT_CALENDAR,
        ingestion_batch_id="segment-dry-run-1", knowledge_cutoff=cutoff,
    )


def test_dry_run_reads_each_periodic_filing_and_its_output_reproduces_the_pair():
    part_iii = reference("0000018230-24-000011", "10-K/A", date(2024, 3, 1),
                         datetime(2024, 3, 1, 12, tzinfo=timezone.utc), date(2023, 12, 31), "cat-10ka.htm")
    future = reference("0000018230-24-000053", "10-Q", date(2024, 11, 1),
                       datetime(2024, 11, 1, 12, tzinfo=timezone.utc), date(2024, 9, 30), "cat-20240930.htm")
    earnings = ("0000018230-24-000042", "2024-08-06", "2024-08-06T10:32:05.000Z", "2024-08-06", "8-K", "cat-20240806.htm")
    documents = _documents()
    documents[part_iii.accession_number] = b"<html><body><p>Part III</p></body></html>"
    downloader = _FakeDownloader(
        [_row(FY2023_10K), _row(Q2_2023_10Q), _row(Q2_2024_10Q), _row(part_iii), _row(future), earnings], documents
    )

    result = _dry_run(downloader)

    assert result.is_complete, result.issues
    assert sorted(downloader.fetched) == sorted(
        [FY2023_10K.accession_number, Q2_2023_10Q.accession_number, Q2_2024_10Q.accession_number, part_iii.accession_number]
    )
    assert result.amendments_without_table == (part_iii.accession_number,)
    assert {fact.lineage.ingestion_batch_id for fact in result.facts} == {"segment-dry-run-1+sec_filing_document"}
    assert all(len(sha) == 64 for _accession, _url, sha in result.documents)
    assert _pair(result.facts).current == Decimal(21605) / Decimal(63025)


def test_dry_run_refuses_an_original_periodic_filing_without_the_table():
    documents = _documents()
    documents[Q2_2023_10Q.accession_number] = b"<html><body><p>layout changed</p></body></html>"
    result = _dry_run(_FakeDownloader([_row(FY2023_10K), _row(Q2_2023_10Q), _row(Q2_2024_10Q)], documents))
    assert not result.is_complete and not result.facts
    assert "has no" in result.issues[0]


def test_dry_run_refuses_simultaneous_disagreeing_filings():
    clone = reference("0000018230-24-000046", "10-Q", Q2_2024_10Q.filed_date, Q2_2024_10Q.accepted_at,
                      Q2_2024_10Q.report_date, "cat-20240630b.htm")
    documents = _documents()
    documents[clone.accession_number] = documents[Q2_2024_10Q.accession_number].replace(b"19,816", b"19,817").replace(b"19,812", b"19,813")
    result = _dry_run(_FakeDownloader([_row(FY2023_10K), _row(Q2_2023_10Q), _row(Q2_2024_10Q), _row(clone)], documents))
    assert not result.is_complete
    assert "simultaneous" in result.issues[0]
