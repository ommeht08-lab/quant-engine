"""Point-in-time CAT ME&T gross margins: cutoffs, amendments, conflicts, gaps."""

from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from src.fundamentals.adapters.sec_downloader import SecIssuerPayload
from src.fundamentals.repository import InMemoryFundamentalsRepository
from src.fundamentals.segment_gross_margin import (
    CAT_MET_SUPPLEMENTAL_RULE,
    REVIEWED_AMENDMENTS_WITHOUT_TABLE,
    SEGMENT_COST_OF_GOODS_SOLD,
    SEGMENT_SALES,
    SegmentGrossMarginRefusal,
    load_segment_gross_margin_pair,
    periodic_filing_documents,
    run_segment_document_dry_run,
    segment_gross_margin_rule_for,
)
from tests.fundamentals.segment_margin_fixtures import (
    CAT_CALENDAR,
    FIXTURE_FILE,
    FIXTURES,
    FY2022_10K,
    FY2023_10K,
    INGESTED_AT,
    PILOT_CUTOFF,
    Q1_2023_10Q,
    Q1_2024_10Q,
    Q2_2023_10Q,
    Q2_2024_10Q,
    Q3_2022_10Q,
    Q3_2023_10Q,
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
    # SEC's own format ("...T14:40:25.000Z") unless a test needs microseconds.
    accepted = filing.accepted_at.isoformat().replace("+00:00", "Z")
    if not filing.accepted_at.microsecond:
        accepted = accepted.replace("Z", ".000Z")
    return (
        filing.accession_number, filing.filed_date.isoformat(), accepted, filing.report_date.isoformat(),
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


class _Clock:
    """Each read is one second later than the last, starting after the
    submissions download."""

    def __init__(self, start=INGESTED_AT):
        self.reads = []
        self._next = start

    def __call__(self):
        self._next += timedelta(seconds=1)
        self.reads.append(self._next)
        return self._next


def _dry_run(downloader, cutoff=PILOT_CUTOFF, **kwargs):
    kwargs.setdefault("clock", _Clock())
    return run_segment_document_dry_run(
        downloader=downloader, rule=CAT_MET_SUPPLEMENTAL_RULE, calendar_policy=CAT_CALENDAR,
        ingestion_batch_id="segment-dry-run-1", knowledge_cutoff=cutoff, **kwargs,
    )


PILOT_ROWS = (FY2023_10K, Q2_2023_10Q, Q2_2024_10Q)
PART_III_ONLY = b"<html><body><p>Part III</p></body></html>"


def _amendment(accepted_at, accession="0000018230-24-000060"):
    return reference(accession, "10-Q/A", accepted_at.date(), accepted_at, date(2024, 6, 30), "cat-20240630a.htm")


def _with_amendment(amendment, document):
    documents = _documents()
    documents[amendment.accession_number] = document
    downloader = _FakeDownloader([_row(filing) for filing in PILOT_ROWS] + [_row(amendment)], documents)
    return downloader


def test_dry_run_reads_each_periodic_filing_and_its_output_reproduces_the_pair():
    part_iii = reference("0000018230-24-000011", "10-K/A", date(2024, 3, 1),
                         datetime(2024, 3, 1, 12, tzinfo=timezone.utc), date(2023, 12, 31), "cat-10ka.htm")
    future = reference("0000018230-24-000053", "10-Q", date(2024, 11, 1),
                       datetime(2024, 11, 1, 12, tzinfo=timezone.utc), date(2024, 9, 30), "cat-20240930.htm")
    earnings = ("0000018230-24-000042", "2024-08-06", "2024-08-06T10:32:05.000Z", "2024-08-06", "8-K", "cat-20240806.htm")
    documents = _documents()
    documents[part_iii.accession_number] = PART_III_ONLY
    downloader = _FakeDownloader(
        [_row(FY2023_10K), _row(Q2_2023_10Q), _row(Q2_2024_10Q), _row(part_iii), _row(future), earnings], documents
    )

    result = _dry_run(downloader, reviewed_amendments_without_table={part_iii.accession_number: "Part III only"})

    assert result.is_complete, result.issues
    assert sorted(downloader.fetched) == sorted(
        [FY2023_10K.accession_number, Q2_2023_10Q.accession_number, Q2_2024_10Q.accession_number, part_iii.accession_number]
    )
    assert result.amendments_without_table == ((part_iii.accession_number, "Part III only"),)
    assert {fact.lineage.ingestion_batch_id for fact in result.facts} == {"segment-dry-run-1+sec_filing_document"}
    assert all(len(item.sha256) == 64 for item in result.documents)
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


# --------------------------------------------------------------------------
# Amendments: no silent skip, exact cutoff boundary
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "accepted_at",
    [PILOT_CUTOFF - timedelta(microseconds=1), PILOT_CUTOFF],
    ids=["one-microsecond-before-cutoff", "at-cutoff"],
)
def test_an_unreviewed_amendment_without_the_table_accepted_by_the_cutoff_refuses(accepted_at):
    amendment = _amendment(accepted_at)
    downloader = _with_amendment(amendment, PART_III_ONLY)

    result = _dry_run(downloader)

    assert amendment.accession_number in downloader.fetched
    assert not result.is_complete and not result.facts
    assert "no reviewed exception" in result.issues[0]
    assert amendment.accession_number in result.issues[0]


def test_an_amendment_accepted_after_the_cutoff_is_never_read():
    amendment = _amendment(PILOT_CUTOFF + timedelta(microseconds=1))
    downloader = _with_amendment(amendment, PART_III_ONLY)

    result = _dry_run(downloader)

    assert result.is_complete, result.issues
    assert amendment.accession_number not in downloader.fetched
    assert result.amendments_without_table == ()
    # Once the cutoff passes its acceptance, the same amendment refuses.
    later = _dry_run(_with_amendment(amendment, PART_III_ONLY), cutoff=amendment.accepted_at)
    assert not later.is_complete and "no reviewed exception" in later.issues[0]


def test_an_amendment_in_an_unrecognized_layout_refuses_even_with_an_exception():
    amendment = _amendment(PILOT_CUTOFF - timedelta(microseconds=1))
    original = _documents()[Q2_2024_10Q.accession_number]
    # Same numbers under an unfamiliar column header: the title is
    # recognized, so the parser refuses the layout outright.
    relabelled = original.replace(b"Consolidated", b"Total Company")
    # Title no longer recognized: nothing qualifies, yet the rows are there.
    renamed = original.replace(b"Results of Operations", b"Results of Segments")

    for name, document in (("relabelled", relabelled), ("renamed", renamed)):
        for reviewed in ({}, {amendment.accession_number: "reviewed as Part III only"}):
            result = _dry_run(_with_amendment(amendment, document), reviewed_amendments_without_table=reviewed)
            assert not result.is_complete and not result.facts, (name, reviewed)
            assert amendment.accession_number in result.issues[0]


def test_an_excepted_amendment_that_contains_the_table_refuses_as_a_stale_review():
    amendment = _amendment(PILOT_CUTOFF - timedelta(microseconds=1))
    document = _documents()[Q2_2024_10Q.accession_number]

    result = _dry_run(
        _with_amendment(amendment, document),
        reviewed_amendments_without_table={amendment.accession_number: "reviewed as Part III only"},
    )

    assert not result.is_complete
    assert "but one was found" in result.issues[0]


def test_a_recognized_amendment_is_read_and_supersedes_only_after_acceptance():
    amendment = _amendment(PILOT_CUTOFF - timedelta(microseconds=1))
    document = _documents()[Q2_2024_10Q.accession_number].replace(b"19,816", b"19,817").replace(b"19,812", b"19,813")

    result = _dry_run(_with_amendment(amendment, document))

    assert result.is_complete, result.issues
    assert _pair(result.facts).current == Decimal(63025 - 41421) / Decimal(63025)
    assert _pair(result.facts, cutoff=amendment.accepted_at - timedelta(microseconds=1)).current == (
        Decimal(21605) / Decimal(63025)
    )


def test_no_cat_amendment_has_a_reviewed_exception_yet():
    assert REVIEWED_AMENDMENTS_WITHOUT_TABLE == {CAT_MET_SUPPLEMENTAL_RULE.cik: {}}


# --------------------------------------------------------------------------
# Data vintage: facts are never available before their documents
# --------------------------------------------------------------------------


def test_dry_run_vintage_is_the_last_document_capture_not_the_submissions_download():
    clock = _Clock()
    result = _dry_run(_FakeDownloader([_row(filing) for filing in PILOT_ROWS], _documents()), clock=clock)

    assert result.is_complete, result.issues
    assert result.submissions_downloaded_at == INGESTED_AT
    captures = [item.captured_at for item in result.documents]
    assert captures == clock.reads == [INGESTED_AT + timedelta(seconds=n) for n in (1, 2, 3)]
    last_capture = captures[-1]
    assert result.ingested_at == last_capture
    assert {fact.lineage.ingested_at for fact in result.facts} == {last_capture}

    # The old stamp (the submissions download) would have exposed facts
    # whose documents had not yet been captured.
    for vintage in (INGESTED_AT, captures[0], last_capture - timedelta(microseconds=1)):
        with pytest.raises(SegmentGrossMarginRefusal, match="missing"):
            _pair(result.facts, vintage=vintage)
    assert _pair(result.facts, vintage=last_capture).current == Decimal(21605) / Decimal(63025)
    assert _pair(result.facts, vintage=last_capture + timedelta(microseconds=1)).current == Decimal(21605) / Decimal(63025)


def test_a_document_captured_before_its_submissions_list_refuses():
    clock = _Clock(start=INGESTED_AT - timedelta(seconds=2))
    result = _dry_run(_FakeDownloader([_row(filing) for filing in PILOT_ROWS], _documents()), clock=clock)

    assert not result.is_complete and not result.facts and result.ingested_at is None
    assert "before the submissions" in result.issues[0]
    assert result.submissions_downloaded_at == INGESTED_AT


def test_a_naive_capture_clock_is_rejected():
    with pytest.raises(ValueError, match="timezone-aware"):
        _dry_run(
            _FakeDownloader([_row(filing) for filing in PILOT_ROWS], _documents()),
            clock=lambda: datetime(2026, 9, 25, 13),
        )


def test_a_refused_dry_run_keeps_the_captures_it_made_but_stamps_no_vintage():
    documents = _documents()
    documents[Q2_2024_10Q.accession_number] = b"<html><body><p>layout changed</p></body></html>"
    result = _dry_run(_FakeDownloader([_row(filing) for filing in PILOT_ROWS], documents))

    assert not result.is_complete and result.ingested_at is None
    assert len(result.documents) == 3 and result.submissions_downloaded_at == INGESTED_AT


# --------------------------------------------------------------------------
# Filing list: acceptance exactly at the cutoff is in, one microsecond later is out
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "offset, included",
    [(timedelta(microseconds=-1), True), (timedelta(0), True), (timedelta(microseconds=1), False)],
    ids=["cutoff-after-acceptance", "cutoff-at-acceptance", "cutoff-before-acceptance"],
)
def test_filing_list_includes_a_filing_accepted_at_or_before_the_cutoff(offset, included):
    # The cutoff moves around Q2 2024's acceptance: 1 microsecond before it,
    # exactly at it, and 1 microsecond after (offset is acceptance - cutoff).
    cutoff = Q2_2024_10Q.accepted_at - offset
    listed = periodic_filing_documents(
        (_submissions(*[_row(filing) for filing in PILOT_ROWS]),),
        cik="18230", accepted_by=cutoff, report_date_floor=date(2020, 1, 1),
    )
    assert (Q2_2024_10Q in listed) is included
    assert {FY2023_10K, Q2_2023_10Q} <= set(listed)


@pytest.mark.parametrize(
    "offset, included",
    [(timedelta(microseconds=-1), True), (timedelta(0), True), (timedelta(microseconds=1), False)],
    ids=["cutoff-after-acceptance", "cutoff-at-acceptance", "cutoff-before-acceptance"],
)
def test_dry_run_reads_a_10q_only_once_its_acceptance_is_at_or_before_the_cutoff(offset, included):
    downloader = _FakeDownloader([_row(filing) for filing in PILOT_ROWS], _documents())
    result = _dry_run(downloader, cutoff=Q2_2024_10Q.accepted_at - offset)

    assert result.is_complete, result.issues
    assert (Q2_2024_10Q.accession_number in downloader.fetched) is included
    accessions = {fact.provenance.accession_number for fact in result.facts}
    assert (Q2_2024_10Q.accession_number in accessions) is included


# --------------------------------------------------------------------------
# Trailing-year selection: annual, Q1, Q3, and three-month vs year-to-date
# --------------------------------------------------------------------------

SELECTION_FILINGS = (Q3_2022_10Q, FY2022_10K, Q1_2023_10Q, Q3_2023_10Q, FY2023_10K, Q1_2024_10Q)


def _selection_facts():
    return tuple(fact for filing in SELECTION_FILINGS for fact in facts_for(filing))


def _parts(pair):
    return {
        (item.concept, item.fiscal_period, item.period_start, item.period_end, item.accession_number)
        for item in pair.components
    }


def test_annual_margins_use_the_fiscal_year_facts_alone():
    pair = _pair(_selection_facts(), latest=date(2023, 12, 31), prior=date(2022, 12, 31))

    assert pair.current == Decimal(63869 - 42776) / Decimal(63869)
    assert pair.prior == Decimal(56574 - 41356) / Decimal(56574)
    assert {item.fiscal_period for item in pair.components} == {"FY"}
    assert {(item.period_start, item.period_end) for item in pair.components} == {
        (date(2023, 1, 1), date(2023, 12, 31)), (date(2022, 1, 1), date(2022, 12, 31)),
    }


def test_q1_trailing_year_is_prior_fy_plus_q1_less_prior_q1():
    cutoff = Q1_2024_10Q.accepted_at
    pair = _pair(_selection_facts(), cutoff=cutoff, latest=date(2024, 3, 31), prior=date(2023, 3, 31))

    # TTM Mar 2024 = FY2023 + Q1 2024 - Q1 2023; TTM Mar 2023 = FY2022 + Q1 2023 - Q1 2022.
    assert pair.current == Decimal((63869 + 14960 - 15099) - (42776 + 9664 - 10104)) / Decimal(63869 + 14960 - 15099)
    assert pair.prior == Decimal((56574 + 15099 - 12886) - (41356 + 10104 - 9560)) / Decimal(56574 + 15099 - 12886)
    q1 = {(c, fp, start, end) for c, fp, start, end, _a in _parts(pair) if fp == "Q1"}
    assert q1 == {
        (concept, "Q1", date(year, 1, 1), date(year, 3, 31))
        for concept in (SEGMENT_SALES, SEGMENT_COST_OF_GOODS_SOLD)
        for year in (2022, 2023, 2024)
    }
    assert all(item.accepted_at <= cutoff for item in pair.components)


def test_q3_trailing_year_uses_nine_month_ytd_never_the_three_month_quarter():
    facts = _selection_facts()
    # Both bases are present for every Q3 end.
    assert {
        fact.period.fiscal_period for fact in facts if fact.identity.period_end == date(2023, 9, 30)
    } == {"Q3", "Q3YTD"}
    pair = _pair(facts, cutoff=Q3_2023_10Q.accepted_at, latest=date(2023, 9, 30), prior=date(2022, 9, 30))

    # TTM Sep 2023 = FY2022 + 9M 2023 - 9M 2022; TTM Sep 2022 = FY2021 + 9M 2022 - 9M 2021.
    sales, cost = 56574 + 47632 - 40703, 41356 + 31758 - 29741
    prior_sales, prior_cost = 48188 + 40703 - 35091, 35521 + 29741 - 25515
    assert pair.current == Decimal(sales - cost) / Decimal(sales)
    assert pair.prior == Decimal(prior_sales - prior_cost) / Decimal(prior_sales)
    assert {item.fiscal_period for item in pair.components} == {"FY", "Q3YTD"}
    assert all(
        item.period_start.month == 1 for item in pair.components if item.fiscal_period == "Q3YTD"
    )


def test_q2_trailing_year_uses_six_month_ytd_when_three_month_tables_are_read():
    # The pilot's Q2 10-Qs in full print three- and six-month tables; the
    # three-month figures are read too, and must not change the pair.
    full = {
        Q2_2023_10Q.accession_number: "cat-20230630-all-supplemental.htm",
        Q2_2024_10Q.accession_number: "cat-20240630-all-supplemental.htm",
    }

    def read(filing):
        name = full.get(filing.accession_number)
        return (FIXTURES / name).read_bytes() if name else None

    facts = tuple(
        fact
        for filing in PILOT_ROWS
        for fact in (facts_for(filing, document_bytes=read(filing)) if read(filing) else facts_for(filing))
    )
    assert {
        fact.period.fiscal_period for fact in facts if fact.identity.period_end == LATEST
    } == {"Q2", "Q2YTD"}
    pair = _pair(facts)
    assert (pair.current, pair.prior) == (_pair(pilot_facts()).current, _pair(pilot_facts()).prior)
    assert {item.fiscal_period for item in pair.components} == {"FY", "Q2YTD"}


def test_two_annual_facts_for_one_year_end_refuse_instead_of_picking_one():
    fy2023 = _find(pilot_facts(), SEGMENT_SALES, date(2023, 1, 1), date(2023, 12, 31))
    rival = replace(
        fy2023,
        value=fy2023.value + 1,
        identity=replace(fy2023.identity, period_start=date(2022, 12, 31)),
        period=replace(fy2023.period, period_start=date(2022, 12, 31)),
        raw_tag=fy2023.raw_tag + "|rival",
    )
    assert rival.period.fiscal_period == "FY"
    # A trailing year ending on the fiscal year end reads the annual fact directly.
    annual = {"latest": date(2023, 12, 31), "prior": date(2022, 12, 31)}
    assert _pair(pilot_facts(), **annual).current == Decimal(63869 - 42776) / Decimal(63869)
    with pytest.raises(SegmentGrossMarginRefusal, match="Ambiguous annual segment_sales ending 2023-12-31"):
        _pair(pilot_facts() + (rival,), **annual)
    # A mid-year trailing year that needs the same annual fact refuses too.
    with pytest.raises(SegmentGrossMarginRefusal, match="ambiguous"):
        _pair(pilot_facts() + (rival,))
