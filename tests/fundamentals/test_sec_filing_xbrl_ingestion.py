"""Point-in-time behavior of filing-XBRL compositions inside the ingestion dry run."""

import datetime as dt
from decimal import Decimal

import pytest

from src.fundamentals.adapters.sec_downloader import SecDownloadError, SecDownloadErrorCode, SecIssuerPayload
from src.fundamentals.concept_map import SEC_CONCEPT_MAP_V2, BalanceCompositionRule, ConceptMap
from src.fundamentals.sec_ingestion import SecDryRunIssueStage, run_sec_ingestion_dry_run
from tests.fundamentals.test_sec_ingestion import CIK, DOWNLOADED_AT, PERIODS, FakeDownloader, _policy

Q1 = PERIODS[0][4]  # 10-Q for the quarter ended 2022-12-31, accepted 2023-02-01
FY = PERIODS[3][4]  # 10-K for FY2023, accepted 2023-11-01
AMENDMENT = "0000320193-23-000005"  # 10-Q/A for Q1, accepted 2023-03-01
AXIS = "srt:ProductOrServiceAxis"
MEMBERS = ("x:AlphaMember", "x:BetaMember")

MAP = ConceptMap(
    version="test-composition-v1",
    rules=SEC_CONCEPT_MAP_V2.rules,
    balance_compositions=(
        BalanceCompositionRule(
            cik=CIK,
            taxonomy="us-gaap",
            raw_tag="LongTermDebtAndCapitalLeaseObligationsCurrent",
            canonical_concept="current_debt",
            axis=AXIS,
            member_sets=(frozenset(MEMBERS),),
            reason="Test issuer tags current term debt only by member.",
        ),
    ),
)


def _instance(instant: str, alpha: int, beta: int, extra: str = "") -> bytes:
    contexts = "".join(
        f'<context id="c-{index}"><entity><identifier scheme="http://www.sec.gov/CIK">{CIK}</identifier>'
        f'<segment><xbrldi:explicitMember dimension="{AXIS}">{member}</xbrldi:explicitMember></segment>'
        f"</entity><period><instant>{instant}</instant></period></context>"
        for index, member in enumerate(MEMBERS)
    )
    facts = "".join(
        f'<us-gaap:LongTermDebtAndCapitalLeaseObligationsCurrent contextRef="c-{index}" unitRef="usd" decimals="0">{value}'
        "</us-gaap:LongTermDebtAndCapitalLeaseObligationsCurrent>"
        for index, value in enumerate((alpha, beta))
    )
    return (
        '<xbrl xmlns="http://www.xbrl.org/2003/instance" xmlns:xbrldi="http://xbrl.org/2006/xbrldi" '
        'xmlns:us-gaap="http://fasb.org/us-gaap/2024" xmlns:srt="http://fasb.org/srt/2024" xmlns:x="http://x.example/2024">'
        f'{contexts}<unit id="usd"><measure>iso4217:USD</measure></unit>{facts}{extra}</xbrl>'
    ).encode()


def _payload(*, amendment=False, reported_current_debt=None):
    rows = list(PERIODS)
    if amendment:
        rows.append(("Q1", "2022-10-02", "2022-12-31", 100, AMENDMENT, "10-Q/A", "2023-03-01", "2023-03-01T10:00:00Z"))
    entries, columns = [], {k: [] for k in ("accessionNumber", "filingDate", "acceptanceDateTime", "reportDate", "form", "primaryDocument")}
    for label, start, end, value, accession, form, filed, accepted in rows:
        entries.append({"start": start, "end": end, "val": value, "accn": accession, "fy": 2023, "fp": label, "form": form, "filed": filed})
        for key, item in (("accessionNumber", accession), ("filingDate", filed), ("acceptanceDateTime", accepted),
                          ("reportDate", end), ("form", form), ("primaryDocument", f"doc-{accession}.htm")):
            columns[key].append(item)
    tags = {"RevenueFromContractWithCustomerExcludingAssessedTax": {"units": {"USD": entries}}}
    if reported_current_debt is not None:
        tags["LongTermDebtCurrent"] = {"units": {"USD": [{
            "end": "2022-12-31", "val": reported_current_debt, "accn": Q1, "fy": 2023, "fp": "Q1", "form": "10-Q", "filed": "2023-02-01",
        }]}}
    return SecIssuerPayload(
        cik=CIK,
        company_facts={"cik": 320193, "entityName": "Test Issuer", "facts": {"us-gaap": tags}},
        submissions=({"cik": CIK, "filings": {"recent": columns, "files": []}},),
        company_facts_url=f"https://data.sec.gov/api/xbrl/companyfacts/CIK{CIK}.json",
        submission_urls=(f"https://data.sec.gov/submissions/CIK{CIK}.json",),
        downloaded_at=DOWNLOADED_AT,
    )


class InstanceSource:
    def __init__(self, documents):
        self.documents = documents
        self.calls = []

    def __call__(self, cik, accession):
        self.calls.append(accession)
        if accession not in self.documents:
            raise SecDownloadError(SecDownloadErrorCode.HTTP_STATUS, "missing")
        return f"https://www.sec.gov/Archives/edgar/data/320193/{accession.replace('-', '')}/i.xml", self.documents[accession]


DOCUMENTS = {
    PERIODS[0][4]: _instance("2022-12-31", 100, 250),
    PERIODS[1][4]: _instance("2023-04-01", 110, 260),
    PERIODS[2][4]: _instance("2023-07-01", 120, 270),
    PERIODS[3][4]: _instance("2023-09-30", 130, 280),
    AMENDMENT: _instance("2022-12-31", 100, 300),
}


def _run(cutoff, *, payload=None, fetcher=None, concept_map=MAP):
    return run_sec_ingestion_dry_run(
        downloader=FakeDownloader(payload or _payload()),
        cik=CIK,
        calendar_policy=_policy(),
        concept_map=concept_map,
        ingestion_batch_id="dry-run-001",
        knowledge_cutoff=cutoff,
        required_concepts=("revenue",),
        filing_instance_fetcher=fetcher,
    )


def _current_debt(result):
    return {
        fact.identity.period_end: (fact.value, fact.provenance.accession_number, fact.lineage.source_adapter)
        for period in result.history.balance_sheet_periods
        for fact in period.facts
        if fact.identity.concept == "current_debt"
    }


def _utc(*args):
    return dt.datetime(*args, tzinfo=dt.timezone.utc)


def test_composed_balances_are_classified_published_and_counted():
    result = _run(_utc(2024, 1, 1), fetcher=InstanceSource(DOCUMENTS))

    assert result.is_complete
    assert _current_debt(result)[dt.date(2022, 12, 31)] == (Decimal(350), Q1, "sec_filing_xbrl")
    assert _current_debt(result)[dt.date(2023, 9, 30)] == (Decimal(410), FY, "sec_filing_xbrl")
    assert result.extracted_fact_count == result.eligible_fact_count == len(result.classified_facts) == 8


def test_filings_accepted_after_the_cutoff_are_never_fetched_or_used():
    source = InstanceSource(DOCUMENTS)

    result = _run(_utc(2023, 3, 15), fetcher=source)

    assert result.is_complete
    assert source.calls == [Q1]
    assert set(_current_debt(result)) == {dt.date(2022, 12, 31)}


@pytest.mark.parametrize(
    ("cutoff", "expected"),
    (
        (_utc(2023, 2, 1, 9, 59), None),  # one minute before Q1 acceptance
        (_utc(2023, 2, 1, 10, 0), (Decimal(350), Q1)),  # at acceptance
    ),
)
def test_acceptance_time_is_the_boundary(cutoff, expected):
    result = _run(cutoff, fetcher=InstanceSource(DOCUMENTS))
    if expected is None:
        assert not result.is_complete
        assert result.issues[0].stage is SecDryRunIssueStage.CUTOFF
    else:
        assert _current_debt(result)[dt.date(2022, 12, 31)][:2] == expected


def test_later_amendment_does_not_leak_backward_and_wins_once_public():
    payload = _payload(amendment=True)

    before = _run(_utc(2023, 2, 15), payload=payload, fetcher=InstanceSource(DOCUMENTS))
    after = _run(_utc(2023, 3, 15), payload=payload, fetcher=InstanceSource(DOCUMENTS))

    assert _current_debt(before)[dt.date(2022, 12, 31)][:2] == (Decimal(350), Q1)
    assert _current_debt(after)[dt.date(2022, 12, 31)][:2] == (Decimal(400), AMENDMENT)


def test_missing_instance_refuses_the_whole_run():
    documents = dict(DOCUMENTS)
    del documents[FY]

    result = _run(_utc(2024, 1, 1), fetcher=InstanceSource(documents))

    assert not result.is_complete
    assert (result.issues[0].stage, result.issues[0].code) == (SecDryRunIssueStage.EXTRACTION, "filing_instance_unavailable")


def test_composition_without_a_filing_source_refuses():
    result = _run(_utc(2024, 1, 1), fetcher=None)
    assert result.issues[0].code == "filing_xbrl_required"


def test_incomplete_members_in_any_public_filing_refuse():
    documents = dict(DOCUMENTS)
    documents[Q1] = _instance("2022-12-31", 100, 250).replace(b'x:BetaMember', b'x:GammaMember')

    result = _run(_utc(2024, 1, 1), fetcher=InstanceSource(documents))

    assert result.issues[0].code == "incomplete_member_set"


def test_composition_must_agree_with_company_facts_for_the_same_filing_and_date():
    conflicting = _run(_utc(2024, 1, 1), payload=_payload(reported_current_debt=349), fetcher=InstanceSource(DOCUMENTS))
    agreeing = _run(_utc(2024, 1, 1), payload=_payload(reported_current_debt=350), fetcher=InstanceSource(DOCUMENTS))

    assert conflicting.issues[0].code == "synonym_conflict"
    assert agreeing.is_complete
    assert _current_debt(agreeing)[dt.date(2022, 12, 31)][0] == Decimal(350)


def test_issuers_without_compositions_never_read_filing_instances():
    source = InstanceSource({})

    result = _run(_utc(2024, 1, 1), fetcher=source, concept_map=SEC_CONCEPT_MAP_V2)

    assert result.is_complete
    assert source.calls == []
    assert _current_debt(result) == {}


def test_readers_see_composed_balances_only_when_the_supplemental_source_is_declared():
    from src.fundamentals.repository import FundamentalsQuery, InMemoryFundamentalsRepository

    result = _run(_utc(2024, 1, 1), fetcher=InstanceSource(DOCUMENTS))
    repository = InMemoryFundamentalsRepository(result.classified_facts)

    def query(**extra):
        return FundamentalsQuery(
            cik=CIK, knowledge_cutoff=_utc(2024, 1, 1), data_vintage_cutoff=_utc(2027, 1, 1),
            concepts=("current_debt", "revenue"), source_adapter="sec_companyfacts",
            concept_map_version=MAP.version, fiscal_calendar_version=_policy().version, **extra,
        )

    primary_only = repository.get_facts(query())
    with_supplement = repository.get_facts(query(supplemental_source_adapters=("sec_filing_xbrl",)))

    assert {fact.identity.concept for fact in primary_only} == {"revenue"}
    assert {fact.identity.concept for fact in with_supplement} == {"current_debt", "revenue"}


def test_composed_comparatives_before_the_calendar_window_are_dropped():
    documents = dict(DOCUMENTS)
    # The FY 10-K also reports a comparative before the calendar's first year.
    prior = _instance("2021-09-30", 1, 2).split(b"<unit")[0].split(b">", 1)[1].replace(b'id="c-', b'id="p-')
    prior_facts = (
        b'<us-gaap:LongTermDebtAndCapitalLeaseObligationsCurrent contextRef="p-0" unitRef="usd" decimals="0">1</us-gaap:LongTermDebtAndCapitalLeaseObligationsCurrent>'
        b'<us-gaap:LongTermDebtAndCapitalLeaseObligationsCurrent contextRef="p-1" unitRef="usd" decimals="0">2</us-gaap:LongTermDebtAndCapitalLeaseObligationsCurrent>'
    )
    documents[FY] = documents[FY].replace(b"</xbrl>", prior + prior_facts + b"</xbrl>")

    result = _run(_utc(2024, 1, 1), fetcher=InstanceSource(documents))

    assert result.is_complete
    assert dt.date(2021, 9, 30) not in _current_debt(result)
