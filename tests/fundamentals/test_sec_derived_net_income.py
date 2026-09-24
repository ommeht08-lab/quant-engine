"""Caterpillar net income attributable to the parent, derived by the ASC 810
identity NetIncomeLoss = ProfitLoss - NetIncomeLossAttributableToNoncontrollingInterest.

The official fixture is an unchanged excerpt of CAT's SEC Company Facts and
Submissions for the Q2 2024 10-Q, the FY2020 10-K, and a DEF 14A proxy.
"""

import datetime as dt
import json
from decimal import Decimal
from pathlib import Path

import pytest

from src.fundamentals.adapters.sec_companyfacts import SecIngestionIssueCode, extract_sec_company_facts
from src.fundamentals.adapters.sec_downloader import SecIssuerPayload
from src.fundamentals.concept_map import (
    SEC_CONCEPT_MAP_V2,
    SEC_CONCEPT_MAP_V3,
    SEC_CONCEPT_MAP_V5,
    ConceptMap,
    DerivedFlowRule,
    concept_map_for_issuer,
)
from src.fundamentals.sec_ingestion import run_sec_ingestion_dry_run
from tests.fundamentals.test_sec_ingestion import CIK, DOWNLOADED_AT, PERIODS, FakeDownloader, _policy

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "sec_companyfacts" / "cat_net_income_excerpt.json"
CAT = "0000018230"
Q2_2024 = "0000018230-24-000045"
FY_2020 = "0000018230-21-000063"
MILLION = Decimal("1000000")
DERIVED_TAG = "ProfitLoss-NetIncomeLossAttributableToNoncontrollingInterest"


def _official():
    return json.loads(FIXTURE.read_text())


def _extract(fixture=None, concept_map=SEC_CONCEPT_MAP_V5, cutoff=None):
    fixture = fixture or _official()
    return extract_sec_company_facts(
        fixture["company_facts"],
        (fixture["submissions"],),
        expected_cik=CAT,
        concept_map=concept_map,
        ingestion_batch_id="test-batch",
        ingested_at=dt.datetime(2026, 9, 23, tzinfo=dt.timezone.utc),
        knowledge_cutoff=cutoff,
    )


def _net_income(result):
    return {
        (fact.provenance_accession_number, fact.period_start, fact.period_end): fact
        for fact in result.facts
        if fact.canonical_concept == "net_income"
    }


def _entries(fixture, tag):
    return fixture["company_facts"]["facts"]["us-gaap"][tag]["units"]["USD"]


class TestOfficialCaterpillarFacts:
    def test_derives_parent_net_income_for_the_pilot_filing_and_full_years(self):
        facts = _net_income(_extract())

        q2 = facts[(Q2_2024, dt.date(2024, 4, 1), dt.date(2024, 6, 30))]
        six_months = facts[(Q2_2024, dt.date(2024, 1, 1), dt.date(2024, 6, 30))]
        fy_2020 = facts[(FY_2020, dt.date(2020, 1, 1), dt.date(2020, 12, 31))]
        assert q2.value / MILLION == 2_681  # 2,681 - 0
        assert six_months.value / MILLION == 5_537  # 5,535 - (-2)
        assert fy_2020.value / MILLION == 2_998  # 3,003 - 5
        assert q2.raw_tag == DERIVED_TAG
        assert q2.accepted_at == dt.datetime(2024, 8, 7, 14, 40, 25, tzinfo=dt.timezone.utc)
        assert q2.lineage.source_adapter == "sec_companyfacts"
        assert q2.lineage.concept_map_version == "sec-companyfacts-v5"

    def test_identity_holds_against_reported_profit_attributable_to_common_in_every_period(self):
        fixture = _official()
        reported = {
            (entry["accn"], entry["start"], entry["end"]): Decimal(str(entry["val"]))
            for entry in _entries(fixture, "NetIncomeLossAvailableToCommonStockholdersBasic")
        }
        derived = _net_income(_extract(fixture))

        assert derived
        for (accession, start, end), fact in derived.items():
            assert fact.value == reported[(accession, start.isoformat(), end.isoformat())]

    def test_profit_including_nci_is_not_the_required_measure(self):
        fixture = _official()
        profit = {
            (entry["accn"], entry["start"], entry["end"]): Decimal(str(entry["val"]))
            for entry in _entries(fixture, "ProfitLoss")
        }
        derived = _net_income(_extract(fixture))

        differing = [key for key, fact in derived.items() if fact.value != profit[(key[0], key[1].isoformat(), key[2].isoformat())]]
        assert differing  # e.g. FY2020: 3,003 consolidated vs 2,998 attributable to Caterpillar

    def test_proxy_statement_net_income_and_10k_quarterly_notes_are_not_used(self):
        facts = _net_income(_extract())

        assert all(fact.form_type in ("10-Q", "10-K") for fact in facts.values())
        # The FY2020 10-K reports 2019-2020 quarters only as profit attributable
        # to common (a quarterly note); without both components no fact is derived.
        assert (FY_2020, dt.date(2020, 10, 1), dt.date(2020, 12, 31)) not in facts

    def test_earlier_policies_still_find_no_net_income(self):
        assert _net_income(_extract(concept_map=SEC_CONCEPT_MAP_V3)) == {}


class TestRefusals:
    def test_cross_check_disagreement_refuses(self):
        fixture = _official()
        for entry in _entries(fixture, "NetIncomeLossAvailableToCommonStockholdersBasic"):
            if entry["accn"] == Q2_2024 and entry["start"] == "2024-04-01":
                entry["val"] = entry["val"] + 1_000_000

        result = _extract(fixture)
        assert not result.is_complete
        assert result.issues[0].code is SecIngestionIssueCode.DERIVED_CROSS_CHECK_MISMATCH

    def test_missing_noncontrolling_interest_component_refuses(self):
        fixture = _official()
        entries = _entries(fixture, "NetIncomeLossAttributableToNoncontrollingInterest")
        entries[:] = [e for e in entries if not (e["accn"] == Q2_2024 and e["start"] == "2024-04-01")]

        result = _extract(fixture)
        assert result.issues[0].code is SecIngestionIssueCode.DERIVED_COMPONENT_MISSING

    def test_components_from_different_filings_are_never_combined(self):
        fixture = _official()
        fy_2020 = next(e for e in _entries(fixture, "ProfitLoss") if e["accn"] == FY_2020)
        for entry in _entries(fixture, "NetIncomeLossAttributableToNoncontrollingInterest"):
            if entry["accn"] == Q2_2024 and entry["start"] == "2024-04-01":
                # same period, moved wholesale into a different filing
                entry.update(accn=FY_2020, form=fy_2020["form"], filed=fy_2020["filed"])

        result = _extract(fixture)
        assert result.issues[0].code is SecIngestionIssueCode.DERIVED_COMPONENT_MISSING

    def test_a_reported_net_income_loss_must_agree_with_the_derivation(self):
        def with_reported(value):
            fixture = _official()
            q2 = next(e for e in _entries(fixture, "ProfitLoss") if e["accn"] == Q2_2024 and e["start"] == "2024-04-01")
            reported = dict(q2, val=value)
            _entries(fixture, "NetIncomeLoss").append(reported)
            return fixture

        assert _extract(with_reported(2_681_000_000)).is_complete
        conflict = _extract(with_reported(2_600_000_000))
        assert conflict.issues[0].code is SecIngestionIssueCode.SYNONYM_CONFLICT


class TestComponentMetadata:
    """Every contributing entry is held to a directly mapped tag's checks."""

    @staticmethod
    def _q2(fixture, tag):
        return next(e for e in _entries(fixture, tag) if e["accn"] == Q2_2024 and e["start"] == "2024-04-01")

    @pytest.mark.parametrize("tag", ("NetIncomeLossAttributableToNoncontrollingInterest",
                                     "NetIncomeLossAvailableToCommonStockholdersBasic"))
    @pytest.mark.parametrize("field, value", (("filed", "1999-01-01"), ("form", "10-K")))
    def test_component_or_cross_check_filing_metadata_must_match_submissions(self, tag, field, value):
        fixture = _official()
        self._q2(fixture, tag)[field] = value

        assert _extract(fixture).issues[0].code is SecIngestionIssueCode.FILING_METADATA_MISMATCH

    def test_dimensional_component_refuses(self):
        fixture = _official()
        self._q2(fixture, "NetIncomeLossAttributableToNoncontrollingInterest")["segment"] = {"axis": "x"}

        assert _extract(fixture).issues[0].code is SecIngestionIssueCode.DIMENSIONAL_CONTEXT_UNSUPPORTED

    @pytest.mark.parametrize(
        "mutate, code",
        (
            (lambda e: e.update(end="2024-13-45"), SecIngestionIssueCode.INVALID_PAYLOAD),
            (lambda e: e.pop("start"), SecIngestionIssueCode.MALFORMED_FACT),
        ),
    )
    def test_malformed_component_refuses_with_its_own_code(self, mutate, code):
        fixture = _official()
        mutate(self._q2(fixture, "NetIncomeLossAttributableToNoncontrollingInterest"))

        assert _extract(fixture).issues[0].code is code

    def test_non_object_component_entry_refuses(self):
        fixture = _official()
        _entries(fixture, "ProfitLoss").append("not-an-object")

        assert _extract(fixture).issues[0].code is SecIngestionIssueCode.INVALID_PAYLOAD

    def test_duplicate_entries_with_different_labels_keep_each_labelling(self):
        fixture = _official()
        q2 = self._q2(fixture, "ProfitLoss")
        _entries(fixture, "ProfitLoss").append(dict(q2, frame="CY2024Q2"))
        q2.pop("frame", None)

        result = _extract(fixture)

        assert result.is_complete
        frames = sorted(
            str(f.frame) for f in result.facts
            if f.canonical_concept == "net_income" and f.provenance_accession_number == Q2_2024
            and str(f.period_start) == "2024-04-01"
        )
        assert frames == ["CY2024Q2", "None"]


class TestPolicyScope:
    def test_only_caterpillar_derives_net_income(self):
        assert {flow.cik for flow in SEC_CONCEPT_MAP_V5.derived_flows} == {CAT}
        assert concept_map_for_issuer(CAT) is SEC_CONCEPT_MAP_V5
        for cik in ("320193", "789019", "104169"):
            assert concept_map_for_issuer(cik).derived_flows_for(cik) == ()

    def test_rule_requires_a_cross_check_and_unmapped_components(self):
        kwargs = dict(
            cik=CAT, canonical_concept="net_income", minuend=("us-gaap", "ProfitLoss"),
            subtrahend=("us-gaap", "NetIncomeLossAttributableToNoncontrollingInterest"),
            identity="x", reason="x",
        )
        with pytest.raises(ValueError, match="cross-check"):
            DerivedFlowRule(cross_checks=(), **kwargs)
        with pytest.raises(ValueError, match="mapped"):
            ConceptMap(
                version="t",
                rules=SEC_CONCEPT_MAP_V2.rules,
                derived_flows=(
                    DerivedFlowRule(
                        cik=CAT, canonical_concept="net_income", minuend=("us-gaap", "NetIncomeLoss"),
                        subtrahend=("us-gaap", "NetIncomeLossAttributableToNoncontrollingInterest"),
                        cross_checks=(("us-gaap", "NetIncomeLossAvailableToCommonStockholdersBasic"),),
                        identity="x", reason="x",
                    ),
                ),
            )


# --------------------------------------------------------------------------
# Point-in-time selection through the full dry run
# --------------------------------------------------------------------------

AMENDMENT = "0000320193-23-000005"
PIT_MAP = ConceptMap(
    version="test-derived-v1",
    rules=SEC_CONCEPT_MAP_V2.rules,
    derived_flows=(
        DerivedFlowRule(
            cik=CIK,
            canonical_concept="net_income",
            minuend=("us-gaap", "ProfitLoss"),
            subtrahend=("us-gaap", "NetIncomeLossAttributableToNoncontrollingInterest"),
            cross_checks=(("us-gaap", "NetIncomeLossAvailableToCommonStockholdersBasic"),),
            identity="NetIncomeLoss = ProfitLoss - NCI",
            reason="test",
        ),
    ),
)


def _payload(amendment_profit=None):
    rows = list(PERIODS)
    if amendment_profit is not None:
        rows.append(("Q1", "2022-10-02", "2022-12-31", 100, AMENDMENT, "10-Q/A", "2023-03-01", "2023-03-01T10:00:00Z"))
    columns = {k: [] for k in ("accessionNumber", "filingDate", "acceptanceDateTime", "reportDate", "form", "primaryDocument")}
    tags = {name: [] for name in (
        "RevenueFromContractWithCustomerExcludingAssessedTax", "ProfitLoss",
        "NetIncomeLossAttributableToNoncontrollingInterest", "NetIncomeLossAvailableToCommonStockholdersBasic",
    )}
    for label, start, end, value, accession, form, filed, accepted in rows:
        profit = amendment_profit if accession == AMENDMENT else value // 5
        base = {"start": start, "end": end, "accn": accession, "fy": 2023, "fp": label, "form": form, "filed": filed}
        tags["RevenueFromContractWithCustomerExcludingAssessedTax"].append(dict(base, val=value))
        tags["ProfitLoss"].append(dict(base, val=profit + 1))
        tags["NetIncomeLossAttributableToNoncontrollingInterest"].append(dict(base, val=1))
        tags["NetIncomeLossAvailableToCommonStockholdersBasic"].append(dict(base, val=profit))
        for key, item in (("accessionNumber", accession), ("filingDate", filed), ("acceptanceDateTime", accepted),
                          ("reportDate", end), ("form", form), ("primaryDocument", f"doc-{accession}.htm")):
            columns[key].append(item)
    return SecIssuerPayload(
        cik=CIK,
        company_facts={"cik": 320193, "entityName": "Test Issuer",
                       "facts": {"us-gaap": {name: {"units": {"USD": entries}} for name, entries in tags.items()}}},
        submissions=({"cik": CIK, "filings": {"recent": columns, "files": []}},),
        company_facts_url=f"https://data.sec.gov/api/xbrl/companyfacts/CIK{CIK}.json",
        submission_urls=(f"https://data.sec.gov/submissions/CIK{CIK}.json",),
        downloaded_at=DOWNLOADED_AT,
    )


def _selected_q1_net_income(cutoff, payload):
    result = run_sec_ingestion_dry_run(
        downloader=FakeDownloader(payload),
        cik=CIK,
        calendar_policy=_policy(),
        concept_map=PIT_MAP,
        ingestion_batch_id="dry-run-001",
        knowledge_cutoff=cutoff,
        required_concepts=("revenue", "net_income"),
    )
    if not result.is_complete:
        return result
    for period in result.history.income_statement_periods:
        for fact in period.facts:
            if fact.identity.concept == "net_income" and period.period.fiscal_period == "Q1":
                return fact.value, fact.provenance.accession_number
    return None


def _utc(*args):
    return dt.datetime(*args, tzinfo=dt.timezone.utc)


def test_derived_net_income_is_invisible_before_filing_acceptance():
    before = _selected_q1_net_income(_utc(2023, 2, 1, 9, 59), _payload())
    assert not before.is_complete  # nothing public yet: explicit cutoff refusal


def test_derived_net_income_is_selected_from_acceptance_onward():
    assert _selected_q1_net_income(_utc(2023, 2, 1, 10, 0), _payload()) == (Decimal(20), PERIODS[0][4])


def test_later_amendment_does_not_leak_backward_and_wins_once_public():
    payload = _payload(amendment_profit=25)

    assert _selected_q1_net_income(_utc(2023, 2, 15), payload) == (Decimal(20), PERIODS[0][4])
    assert _selected_q1_net_income(_utc(2023, 3, 15), payload) == (Decimal(25), AMENDMENT)
