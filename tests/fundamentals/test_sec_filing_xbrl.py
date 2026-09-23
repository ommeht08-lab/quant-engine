"""Filing-XBRL balance composition against official Caterpillar filing excerpts.

Fixtures in tests/fixtures/sec_filing_xbrl/cat/ are excerpts of the official
SEC XBRL instances (source URL and full-document sha256 in each header).
Negative cases are derived from those official documents by a single,
explicit edit so every refusal is tested against real tagging.
"""

import datetime as dt
from decimal import Decimal
from pathlib import Path

import pytest

from src.fundamentals.adapters.sec_filing_xbrl import (
    SOURCE_ADAPTER,
    FilingReference,
    FilingXbrlError,
    FilingXbrlIssueCode,
    compose_consolidated_balances,
    parse_xbrl_instance,
)
from src.fundamentals.concept_map import (
    SEC_CONCEPT_MAP_V3,
    SEC_CONCEPT_MAP_V4,
    BalanceCompositionRule,
    concept_map_for_issuer,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "sec_filing_xbrl" / "cat"
CAT = "0000018230"
Q2_2024 = "0000018230-24-000045"
FY_2023 = "0000018230-24-000009"
FY_2025 = "0000018230-26-000008"
MILLION = Decimal("1000000")


def _document(accession: str) -> bytes:
    return (FIXTURES / f"{accession}.xml").read_bytes()


def _reference(accession=Q2_2024, form="10-Q", accepted=dt.datetime(2024, 8, 7, 14, 40, 25, tzinfo=dt.timezone.utc)):
    return FilingReference(
        cik=CAT,
        entity_name="CATERPILLAR INC",
        accession_number=accession,
        form_type=form,
        filed_date=accepted.date(),
        accepted_at=accepted,
        report_date=dt.date(2024, 6, 30),
        primary_document="cat-20240630.htm",
        instance_url=f"https://www.sec.gov/Archives/edgar/data/18230/{accession.replace('-', '')}/instance.xml",
    )


def _compose(document: bytes, reference=None, rules=None):
    return compose_consolidated_balances(
        parse_xbrl_instance(document),
        rules if rules is not None else SEC_CONCEPT_MAP_V4.balance_compositions_for(CAT),
        filing=reference or _reference(),
        concept_map_version=SEC_CONCEPT_MAP_V4.version,
        ingestion_batch_id="test-batch",
        ingested_at=dt.datetime(2026, 9, 23, tzinfo=dt.timezone.utc),
    )


def _by_key(facts):
    return {(fact.canonical_concept, fact.period_end): fact for fact in facts}


def _millions(fact):
    return fact.value / MILLION


class TestOfficialCaterpillarFilings:
    def test_q2_2024_10q_composes_consolidated_term_debt_at_the_pilot_balance_date(self):
        facts = _by_key(_compose(_document(Q2_2024)))

        current = facts[("current_debt", dt.date(2024, 6, 30))]
        noncurrent = facts[("long_term_debt", dt.date(2024, 6, 30))]
        assert _millions(current) == 8_177  # ME&T 45 + Financial Products 8,132
        assert _millions(noncurrent) == 23_836  # ME&T 8,537 + Financial Products 15,299
        # The 2023-12-31 comparative in the same filing also composes.
        assert _millions(facts[("long_term_debt", dt.date(2023, 12, 31))]) == 24_472

    def test_composition_reconciles_to_reported_total_liabilities(self):
        parsed = parse_xbrl_instance(_document(Q2_2024))
        consolidated = {
            fact.name: fact.value / MILLION
            for fact in parsed
            if fact.instant == dt.date(2024, 6, 30) and not fact.dimensions
        }
        short_term_financial_products = next(
            fact.value / MILLION
            for fact in parsed
            if fact.name == "ShortTermBorrowings" and fact.instant == dt.date(2024, 6, 30)
        )
        composed = _by_key(_compose(_document(Q2_2024)))
        current_debt = _millions(composed[("current_debt", dt.date(2024, 6, 30))])
        long_term_debt = _millions(composed[("long_term_debt", dt.date(2024, 6, 30))])

        current_lines = (
            short_term_financial_products
            + consolidated["AccountsPayableCurrent"]
            + consolidated["AccruedLiabilitiesCurrent"]
            + consolidated["EmployeeRelatedLiabilitiesCurrent"]
            + consolidated["ContractWithCustomerLiabilityCurrent"]
            + consolidated["DividendsPayableCurrent"]
            + consolidated["OtherLiabilitiesCurrent"]
            + current_debt
        )
        noncurrent_lines = (
            long_term_debt
            + consolidated["PensionAndOtherPostretirementAndPostemploymentBenefitPlansLiabilitiesNoncurrent"]
            + consolidated["OtherLiabilitiesNoncurrent"]
        )
        assert current_lines == consolidated["LiabilitiesCurrent"] == 33_564
        assert noncurrent_lines == consolidated["Liabilities"] - consolidated["LiabilitiesCurrent"] == 32_636

    def test_year_end_segment_mirror_is_excluded_and_matches_reported_noncurrent_total(self):
        parsed = parse_xbrl_instance(_document(FY_2023))
        reported = {
            fact.instant: fact.value / MILLION
            for fact in parsed
            if fact.name == "LongTermDebtNoncurrent"
        }
        facts = _by_key(_compose(_document(FY_2023), _reference(FY_2023, "10-K")))

        for year_end in (dt.date(2023, 12, 31), dt.date(2022, 12, 31)):
            assert _millions(facts[("long_term_debt", year_end)]) == reported[year_end]
        assert "FinancialProductsSegmentMember" not in facts[("long_term_debt", dt.date(2023, 12, 31))].raw_tag

    def test_renamed_machinery_member_is_accepted_as_a_complete_set(self):
        facts = _by_key(_compose(_document(FY_2025), _reference(FY_2025, "10-K")))

        assert _millions(facts[("long_term_debt", dt.date(2025, 12, 31))]) == 30_696
        assert _millions(facts[("current_debt", dt.date(2025, 12, 31))]) == 7_120
        assert "cat:MachineryPowerEnergyMember" in facts[("long_term_debt", dt.date(2025, 12, 31))].raw_tag

    def test_composed_fact_preserves_filing_context_unit_period_and_lineage(self):
        fact = _by_key(_compose(_document(Q2_2024)))[("current_debt", dt.date(2024, 6, 30))]

        assert fact.raw_tag == (
            "LongTermDebtAndCapitalLeaseObligationsCurrent#sum[srt:ProductOrServiceAxis:"
            "cat:FinancialProductsMember@c-21+cat:MachineryEnergyTransportationMember@c-23]"
        )
        assert fact.taxonomy == "us-gaap"
        assert (fact.unit, fact.currency, fact.period_start) == ("USD", "USD", None)
        assert fact.provenance_accession_number == Q2_2024
        assert fact.accepted_at == dt.datetime(2024, 8, 7, 14, 40, 25, tzinfo=dt.timezone.utc)
        assert fact.is_amendment is False
        assert fact.lineage.source_adapter == SOURCE_ADAPTER
        assert fact.lineage.source_document_url.endswith("/000001823024000045/instance.xml")
        assert fact.lineage.concept_map_version == "sec-companyfacts-v4"
        assert fact.lineage.ingestion_batch_id == "test-batch"

    def test_amendment_form_is_marked_as_an_amendment(self):
        fact = _compose(_document(Q2_2024), _reference(form="10-Q/A"))[0]
        assert fact.is_amendment is True


def _edit(accession: str, old: str, new: str) -> bytes:
    text = _document(accession).decode("utf-8")
    assert text.count(old) == 1, old
    return text.replace(old, new).encode("utf-8")


class TestRefusals:
    def test_segment_only_financial_products_refuses(self):
        document = _edit(
            Q2_2024,
            '<us-gaap:LongTermDebtAndCapitalLeaseObligationsCurrent contextRef="c-23" decimals="-6" id="f-204" unitRef="usd">45000000</us-gaap:LongTermDebtAndCapitalLeaseObligationsCurrent>',
            "",
        )
        with pytest.raises(FilingXbrlError) as error:
            _compose(document)
        assert error.value.code is FilingXbrlIssueCode.INCOMPLETE_MEMBER_SET

    def test_undeclared_member_on_the_axis_refuses(self):
        document = _edit(
            Q2_2024,
            'dimension="srt:ProductOrServiceAxis">cat:MachineryEnergyTransportationMember</xbrldi:explicitMember></segment></entity><period><instant>2024-06-30',
            'dimension="srt:ProductOrServiceAxis">cat:ConsolidatingAdjustmentsMember</xbrldi:explicitMember></segment></entity><period><instant>2024-06-30',
        )
        with pytest.raises(FilingXbrlError) as error:
            _compose(document)
        assert error.value.code is FilingXbrlIssueCode.INCOMPLETE_MEMBER_SET

    def test_mixing_old_and_renamed_members_refuses(self):
        document = _document(FY_2025).decode().replace(
            "</xbrl>",
            '<context id="x-met"><entity><identifier scheme="http://www.sec.gov/CIK">0000018230</identifier><segment>'
            '<xbrldi:explicitMember dimension="srt:ProductOrServiceAxis">cat:MachineryEnergyTransportationMember'
            "</xbrldi:explicitMember></segment></entity><period><instant>2025-12-31</instant></period></context>"
            '<us-gaap:LongTermDebtAndCapitalLeaseObligations contextRef="x-met" decimals="-6" unitRef="usd">1</us-gaap:LongTermDebtAndCapitalLeaseObligations></xbrl>',
        )
        with pytest.raises(FilingXbrlError) as error:
            _compose(document.encode(), _reference(FY_2025, "10-K"))
        assert error.value.code is FilingXbrlIssueCode.INCOMPLETE_MEMBER_SET

    def test_conflicting_reported_total_refuses_and_an_agreeing_total_is_accepted(self):
        def with_total(value):
            return _document(Q2_2024).decode().replace(
                "</xbrl>",
                f'<us-gaap:LongTermDebtAndCapitalLeaseObligationsCurrent contextRef="c-4" decimals="-6" '
                f'unitRef="usd">{value}</us-gaap:LongTermDebtAndCapitalLeaseObligationsCurrent></xbrl>',
            ).encode()

        with pytest.raises(FilingXbrlError) as error:
            _compose(with_total(8_000_000_000))
        assert error.value.code is FilingXbrlIssueCode.CONFLICTING_TOTAL
        agreed = _by_key(_compose(with_total(8_177_000_000)))
        assert _millions(agreed[("current_debt", dt.date(2024, 6, 30))]) == 8_177

    def test_member_reported_twice_with_different_values_refuses(self):
        document = _document(Q2_2024).decode().replace(
            "</xbrl>",
            '<us-gaap:LongTermDebtAndCapitalLeaseObligationsCurrent contextRef="c-21" decimals="-6" '
            'unitRef="usd">8133000000</us-gaap:LongTermDebtAndCapitalLeaseObligationsCurrent></xbrl>',
        ).encode()
        with pytest.raises(FilingXbrlError) as error:
            _compose(document)
        assert error.value.code is FilingXbrlIssueCode.CONFLICTING_COMPONENT

    def test_segment_mirror_that_disagrees_with_its_counterpart_refuses(self):
        document = _edit(
            FY_2023,
            'contextRef="c-658" decimals="-6" id="f-2824" unitRef="usd">15893000000<',
            'contextRef="c-658" decimals="-6" id="f-2824" unitRef="usd">15894000000<',
        )
        with pytest.raises(FilingXbrlError) as error:
            _compose(document, _reference(FY_2023, "10-K"))
        assert error.value.code is FilingXbrlIssueCode.CONFLICTING_COMPONENT

    def test_member_fact_with_an_additional_dimension_refuses(self):
        document = _edit(
            Q2_2024,
            'dimension="srt:ProductOrServiceAxis">cat:FinancialProductsMember</xbrldi:explicitMember></segment></entity><period><instant>2024-06-30',
            'dimension="srt:ProductOrServiceAxis">cat:FinancialProductsMember</xbrldi:explicitMember>'
            '<xbrldi:explicitMember dimension="us-gaap:LongtermDebtTypeAxis">us-gaap:SeniorNotesMember'
            "</xbrldi:explicitMember></segment></entity><period><instant>2024-06-30",
        )
        with pytest.raises(FilingXbrlError) as error:
            _compose(document)
        assert error.value.code is FilingXbrlIssueCode.UNSUPPORTED_COMPONENT

    def test_facts_on_another_axis_are_ignored(self):
        document = _document(Q2_2024).decode().replace(
            "</xbrl>",
            '<context id="x-vie"><entity><identifier scheme="http://www.sec.gov/CIK">0000018230</identifier><segment>'
            '<xbrldi:explicitMember dimension="srt:ConsolidatedEntitiesAxis">us-gaap:VariableInterestEntityPrimaryBeneficiaryMember'
            "</xbrldi:explicitMember></segment></entity><period><instant>2024-06-30</instant></period></context>"
            '<us-gaap:LongTermDebtAndCapitalLeaseObligations contextRef="x-vie" decimals="-6" unitRef="usd">1</us-gaap:LongTermDebtAndCapitalLeaseObligations></xbrl>',
        ).encode()
        facts = _by_key(_compose(document))
        assert _millions(facts[("long_term_debt", dt.date(2024, 6, 30))]) == 23_836

    def test_invalid_instance_refuses(self):
        with pytest.raises(FilingXbrlError) as error:
            _compose(b"<xbrl><not-closed>")
        assert error.value.code is FilingXbrlIssueCode.INVALID_INSTANCE

    def test_rules_for_other_issuers_do_not_apply(self):
        assert _compose(_document(Q2_2024), rules=SEC_CONCEPT_MAP_V3.balance_compositions) == ()


class TestPolicy:
    def test_only_caterpillar_has_compositions_and_other_issuers_are_unchanged(self):
        assert SEC_CONCEPT_MAP_V4.rules == SEC_CONCEPT_MAP_V3.rules
        assert SEC_CONCEPT_MAP_V4.issuer_tag_exclusions == SEC_CONCEPT_MAP_V3.issuer_tag_exclusions
        assert {rule.cik for rule in SEC_CONCEPT_MAP_V4.balance_compositions} == {CAT}
        for cik in ("320193", "789019", "104169"):
            assert concept_map_for_issuer(cik).balance_compositions_for(cik) == ()

    def test_rule_requires_complete_member_sets_and_valid_mirrors(self):
        kwargs = dict(
            cik=CAT, taxonomy="us-gaap", raw_tag="X", canonical_concept="current_debt",
            axis="srt:ProductOrServiceAxis", reason="test",
        )
        with pytest.raises(ValueError, match="at least two"):
            BalanceCompositionRule(member_sets=(frozenset({"cat:FinancialProductsMember"}),), **kwargs)
        with pytest.raises(ValueError, match="mirror"):
            BalanceCompositionRule(
                member_sets=(frozenset({"a", "b"}),),
                equivalent_members=(("a", "b"),),
                **kwargs,
            )


def test_duplicate_context_ids_refuse_the_instance():
    context = (
        '<context id="c-1"><entity><identifier scheme="http://www.sec.gov/CIK">0000018230</identifier></entity>'
        "<period><instant>{}</instant></period></context>"
    )
    document = (
        '<xbrl xmlns="http://www.xbrl.org/2003/instance">'
        + context.format("2024-06-30")
        + context.format("2023-12-31")
        + "</xbrl>"
    ).encode()

    with pytest.raises(FilingXbrlError) as error:
        parse_xbrl_instance(document)
    assert error.value.code is FilingXbrlIssueCode.INVALID_INSTANCE
