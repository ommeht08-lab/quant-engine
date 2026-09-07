import copy
import datetime as dt
from decimal import Decimal

import pytest

from src.fundamentals.adapters.sec_companyfacts import (
    SOURCE_ADAPTER,
    SecIngestionIssueCode,
    extract_sec_company_facts,
)
from src.fundamentals.concept_map import (
    SEC_CONCEPT_MAP_V1,
    ConceptMap,
    ConceptRule,
    FactPeriodType,
)
from src.fundamentals.types import StatementKind

CIK = "0000320193"
ACCESSION = "0000320193-24-000123"
AMENDMENT_ACCESSION = "0000320193-24-000124"
INGESTED_AT = dt.datetime(2026, 9, 7, 14, 0, tzinfo=dt.timezone.utc)


def _submission_payload(*, amendment=False):
    accessions = [ACCESSION]
    forms = ["10-K"]
    filing_dates = ["2024-11-01"]
    acceptance_times = ["2024-11-01T06:01:36.000Z"]
    report_dates = ["2024-09-28"]
    documents = ["aapl-20240928.htm"]
    if amendment:
        accessions.append(AMENDMENT_ACCESSION)
        forms.append("10-K/A")
        filing_dates.append("2024-11-08")
        acceptance_times.append("2024-11-08T17:30:00-05:00")
        report_dates.append("2024-09-28")
        documents.append("aapl-20240928x10ka.htm")
    return {
        "cik": "0000320193",
        "filings": {
            "recent": {
                "accessionNumber": accessions,
                "filingDate": filing_dates,
                "acceptanceDateTime": acceptance_times,
                "reportDate": report_dates,
                "form": forms,
                "primaryDocument": documents,
            }
        }
    }


def _duration_entry(
    *,
    accession=ACCESSION,
    value=391_035_000_000,
    start="2023-10-01",
    end="2024-09-28",
    form="10-K",
    filed="2024-11-01",
    fy=2024,
    fp="FY",
):
    return {
        "start": start,
        "end": end,
        "val": value,
        "accn": accession,
        "fy": fy,
        "fp": fp,
        "form": form,
        "filed": filed,
    }


def _company_facts(entries=None):
    if entries is None:
        entries = [_duration_entry()]
    return {
        "cik": 320193,
        "entityName": "Apple Inc.",
        "facts": {
            "us-gaap": {
                "RevenueFromContractWithCustomerExcludingAssessedTax": {
                    "label": "Revenue",
                    "description": "Revenue from contracts with customers.",
                    "units": {"USD": entries},
                }
            }
        },
    }


def _extract(company_facts=None, submissions=None, **overrides):
    values = {
        "expected_cik": CIK,
        "concept_map": SEC_CONCEPT_MAP_V1,
        "ingestion_batch_id": "batch-sec-001",
        "ingested_at": INGESTED_AT,
    }
    values.update(overrides)
    return extract_sec_company_facts(
        company_facts or _company_facts(),
        submissions or (_submission_payload(),),
        **values,
    )


def _assert_issue(result, code):
    assert not result.is_complete
    assert result.facts == ()
    assert len(result.issues) == 1
    assert result.issues[0].code is code


class TestSuccessfulExtraction:
    def test_joins_submission_metadata_and_preserves_exact_lineage(self):
        result = _extract()

        assert result.is_complete
        assert result.cik == CIK
        assert result.concept_map_version == SEC_CONCEPT_MAP_V1.version
        assert len(result.facts) == 1
        fact = result.facts[0]
        assert fact.entity_cik == CIK
        assert fact.entity_name == "Apple Inc."
        assert fact.canonical_concept == "revenue"
        assert fact.value == Decimal("391035000000")
        assert fact.period_start == dt.date(2023, 10, 1)
        assert fact.period_end == dt.date(2024, 9, 28)
        assert fact.provenance_accession_number == ACCESSION
        assert fact.accepted_at == dt.datetime(2024, 11, 1, 6, 1, 36, tzinfo=dt.timezone.utc)
        assert fact.filing_fiscal_year == 2024
        assert fact.filing_fiscal_period == "FY"
        assert fact.lineage.source_adapter == SOURCE_ADAPTER
        assert fact.lineage.ingestion_batch_id == "batch-sec-001"
        assert fact.lineage.ingested_at == INGESTED_AT
        assert fact.source_document_url == (
            "https://www.sec.gov/Archives/edgar/data/320193/"
            "000032019324000123/aapl-20240928.htm"
        )

    def test_preserves_comparative_facts_in_the_same_filing(self):
        current = _duration_entry()
        comparative = _duration_entry(
            value=383_285_000_000,
            start="2022-09-25",
            end="2023-09-30",
            fy=2024,
            fp="FY",
        )

        result = _extract(_company_facts([current, comparative]))

        assert result.is_complete
        assert [fact.period_end for fact in result.facts] == [
            dt.date(2023, 9, 30),
            dt.date(2024, 9, 28),
        ]
        assert all(fact.filing_fiscal_year == 2024 for fact in result.facts)

    def test_preserves_amendment_as_a_separate_source_record(self):
        original = _duration_entry()
        amendment = _duration_entry(
            accession=AMENDMENT_ACCESSION,
            value=391_100_000_000,
            form="10-K/A",
            filed="2024-11-08",
        )

        result = _extract(
            _company_facts([original, amendment]),
            (_submission_payload(amendment=True),),
        )

        assert result.is_complete
        assert [fact.is_amendment for fact in result.facts] == [False, True]
        assert {fact.provenance_accession_number for fact in result.facts} == {
            ACCESSION,
            AMENDMENT_ACCESSION,
        }

    def test_cover_fact_is_kept_as_cover_source_record(self):
        payload = _company_facts([])
        payload["facts"]["dei"] = {
            "EntityCommonStockSharesOutstanding": {
                "units": {
                    "shares": [
                        {
                            "end": "2024-10-18",
                            "val": 15_115_823_000,
                            "accn": ACCESSION,
                            "fy": 2024,
                            "fp": "FY",
                            "form": "10-K",
                            "filed": "2024-11-01",
                        }
                    ]
                }
            }
        }

        result = _extract(payload)

        assert result.is_complete
        fact = result.facts[0]
        assert fact.statement_kind is StatementKind.COVER
        assert fact.period_type is FactPeriodType.COVER
        assert fact.period_start is None
        assert fact.currency is None
        assert fact.unit == "shares"

    def test_ignores_unmapped_tags_and_unsupported_forms(self):
        payload = _company_facts([_duration_entry(form="8-K")])
        payload["facts"]["us-gaap"]["UnmappedTag"] = {
            "units": {"USD": [_duration_entry()]}
        }

        result = _extract(payload)

        _assert_issue(result, SecIngestionIssueCode.NO_MAPPED_FACTS)

    def test_output_is_independent_of_payload_and_submission_order(self):
        amendment = _duration_entry(
            accession=AMENDMENT_ACCESSION,
            value=391_100_000_000,
            form="10-K/A",
            filed="2024-11-08",
        )
        payload = _company_facts([_duration_entry(), amendment])
        reversed_payload = copy.deepcopy(payload)
        reversed_payload["facts"]["us-gaap"][
            "RevenueFromContractWithCustomerExcludingAssessedTax"
        ]["units"]["USD"].reverse()
        submissions = _submission_payload(amendment=True)
        reversed_submissions = copy.deepcopy(submissions)
        for column in reversed_submissions["filings"]["recent"].values():
            column.reverse()

        assert _extract(payload, (submissions,)) == _extract(
            reversed_payload, (reversed_submissions,)
        )

    def test_output_is_deterministic_when_facts_differ_only_by_frame(self):
        first = _duration_entry()
        first["frame"] = "CY2024"
        second = _duration_entry()
        second["frame"] = "CY2024Q3"

        forward = _extract(_company_facts([first, second]))
        reverse = _extract(_company_facts([second, first]))

        assert forward == reverse
        assert [fact.frame for fact in forward.facts] == ["CY2024", "CY2024Q3"]


class TestFailClosedValidation:
    def test_wrong_issuer_is_refused(self):
        payload = _company_facts()
        payload["cik"] = 789019
        _assert_issue(_extract(payload), SecIngestionIssueCode.ISSUER_MISMATCH)

    def test_wrong_submissions_issuer_is_refused(self):
        submissions = _submission_payload()
        submissions["cik"] = "0000789019"
        _assert_issue(
            _extract(submissions=(submissions,)),
            SecIngestionIssueCode.ISSUER_MISMATCH,
        )

    def test_missing_submission_metadata_is_refused(self):
        _assert_issue(
            _extract(submissions=(_submission_payload(amendment=True),), company_facts=_company_facts([
                _duration_entry(accession="0000320193-24-999999")
            ])),
            SecIngestionIssueCode.MISSING_SUBMISSION_METADATA,
        )

    def test_mismatched_filing_metadata_is_refused(self):
        _assert_issue(
            _extract(_company_facts([_duration_entry(filed="2024-11-02")])),
            SecIngestionIssueCode.FILING_METADATA_MISMATCH,
        )

    @pytest.mark.parametrize("form", (None, "", []))
    def test_malformed_fact_form_is_refused_without_raising(self, form):
        _assert_issue(
            _extract(_company_facts([_duration_entry(form=form)])),
            SecIngestionIssueCode.MALFORMED_FACT,
        )

    def test_conflicting_duplicate_submission_metadata_is_refused(self):
        first = _submission_payload()
        second = _submission_payload()
        second["filings"]["recent"]["primaryDocument"][0] = "different.htm"
        _assert_issue(
            _extract(submissions=(first, second)),
            SecIngestionIssueCode.CONFLICTING_SUBMISSION_METADATA,
        )

    def test_different_submission_column_lengths_are_refused(self):
        submissions = _submission_payload()
        submissions["filings"]["recent"]["form"].append("10-Q")
        _assert_issue(_extract(submissions=(submissions,)), SecIngestionIssueCode.INVALID_PAYLOAD)

    def test_unrelated_submission_forms_may_omit_report_metadata(self):
        submissions = _submission_payload()
        recent = submissions["filings"]["recent"]
        recent["accessionNumber"].append("0000320193-24-000125")
        recent["filingDate"].append("")
        recent["acceptanceDateTime"].append("")
        recent["reportDate"].append("")
        recent["form"].append("4")
        recent["primaryDocument"].append("xslF345X05/form4.xml")

        result = _extract(submissions=(submissions,))

        assert result.is_complete


    @pytest.mark.parametrize("field", ("segment", "dimensions"))
    def test_dimensional_context_is_never_silently_collapsed(self, field):
        entry = _duration_entry()
        entry[field] = {"dimension": "member"}
        _assert_issue(
            _extract(_company_facts([entry])),
            SecIngestionIssueCode.DIMENSIONAL_CONTEXT_UNSUPPORTED,
        )

    def test_unsupported_unit_is_refused(self):
        payload = _company_facts([])
        tag = payload["facts"]["us-gaap"][
            "RevenueFromContractWithCustomerExcludingAssessedTax"
        ]
        tag["units"] = {"EUR": [_duration_entry()]}
        _assert_issue(_extract(payload), SecIngestionIssueCode.UNSUPPORTED_UNIT)

    def test_duration_fact_without_start_is_refused(self):
        entry = _duration_entry()
        del entry["start"]
        _assert_issue(_extract(_company_facts([entry])), SecIngestionIssueCode.MALFORMED_FACT)

    def test_instant_fact_with_start_is_refused(self):
        payload = _company_facts([])
        payload["facts"]["us-gaap"]["InventoryNet"] = {
            "units": {
                "USD": [
                    {
                        **_duration_entry(value=7_286_000_000),
                        "start": "2023-10-01",
                    }
                ]
            }
        }
        _assert_issue(_extract(payload), SecIngestionIssueCode.MALFORMED_FACT)

    @pytest.mark.parametrize("value", (1.5, True, "NaN"))
    def test_inexact_or_nonfinite_values_are_refused(self, value):
        _assert_issue(
            _extract(_company_facts([_duration_entry(value=value)])),
            SecIngestionIssueCode.MALFORMED_FACT,
        )

    def test_synonyms_that_disagree_in_one_filing_are_refused(self):
        payload = _company_facts()
        payload["facts"]["us-gaap"]["Revenues"] = {
            "units": {"USD": [_duration_entry(value=390_000_000_000)]}
        }
        result = _extract(payload)

        _assert_issue(result, SecIngestionIssueCode.SYNONYM_CONFLICT)
        assert result.issues[0].raw_tags == (
            "RevenueFromContractWithCustomerExcludingAssessedTax",
            "Revenues",
        )

    def test_any_error_discards_other_valid_facts(self):
        payload = _company_facts()
        payload["facts"]["us-gaap"]["InventoryNet"] = {
            "units": {"EUR": [{**_duration_entry(value=7_286_000_000), "start": None}]}
        }
        result = _extract(payload)

        assert result.facts == ()
        assert result.issues

    def test_ingested_at_must_be_aware(self):
        _assert_issue(
            _extract(ingested_at=dt.datetime(2026, 9, 7, 14, 0)),
            SecIngestionIssueCode.INVALID_PAYLOAD,
        )

    def test_ingested_at_must_be_a_datetime(self):
        _assert_issue(
            _extract(ingested_at="2026-09-07T14:00:00Z"),
            SecIngestionIssueCode.INVALID_PAYLOAD,
        )

    def test_submissions_must_be_iterable(self):
        _assert_issue(
            _extract(submissions=1),
            SecIngestionIssueCode.INVALID_PAYLOAD,
        )


class TestConceptMapIntegrity:
    def _rule(self, **overrides):
        values = {
            "taxonomy": "us-gaap",
            "raw_tag": "ExampleTag",
            "canonical_concept": "example",
            "statement_kind": StatementKind.INCOME_STATEMENT,
            "period_type": FactPeriodType.DURATION,
            "allowed_units": ("USD",),
        }
        values.update(overrides)
        return ConceptRule(**values)

    def test_rejects_duplicate_raw_tag_keys(self):
        rule = self._rule()
        with pytest.raises(ValueError, match="unique"):
            ConceptMap(version="test-v1", rules=(rule, rule))

    @pytest.mark.parametrize(
        ("field", "value", "message"),
        (
            ("statement_kind", "income_statement", "statement_kind"),
            ("period_type", "duration", "period_type"),
            ("allowed_units", "USD", "allowed_units"),
        ),
    )
    def test_rejects_wrong_runtime_policy_types(self, field, value, message):
        with pytest.raises(ValueError, match=message):
            self._rule(**{field: value})

    def test_rejects_non_rule_map_members(self):
        with pytest.raises(ValueError, match="ConceptRule"):
            ConceptMap(version="test-v1", rules=("not-a-rule",))

    def test_rejects_non_collection_rules(self):
        with pytest.raises(ValueError, match="collection"):
            ConceptMap(version="test-v1", rules=None)

    def test_rejects_inconsistent_synonym_policies(self):
        first = self._rule()
        second = self._rule(
            raw_tag="OtherTag",
            statement_kind=StatementKind.BALANCE_SHEET,
            period_type=FactPeriodType.INSTANT,
        )
        with pytest.raises(ValueError, match="share statement"):
            ConceptMap(version="test-v1", rules=(first, second))

    @pytest.mark.parametrize(
        ("statement_kind", "period_type"),
        (
            (StatementKind.BALANCE_SHEET, FactPeriodType.DURATION),
            (StatementKind.INCOME_STATEMENT, FactPeriodType.INSTANT),
            (StatementKind.CASH_FLOW, FactPeriodType.INSTANT),
        ),
    )
    def test_rejects_statement_and_period_type_mismatches(
        self, statement_kind, period_type
    ):
        with pytest.raises(ValueError, match="period type"):
            self._rule(statement_kind=statement_kind, period_type=period_type)

    def test_map_version_is_explicit_and_lookup_is_taxonomy_scoped(self):
        assert SEC_CONCEPT_MAP_V1.version == "sec-companyfacts-v1"
        assert SEC_CONCEPT_MAP_V1.rule_for(
            "us-gaap", "RevenueFromContractWithCustomerExcludingAssessedTax"
        ).canonical_concept == "revenue"
        assert SEC_CONCEPT_MAP_V1.rule_for(
            "dei", "RevenueFromContractWithCustomerExcludingAssessedTax"
        ) is None
