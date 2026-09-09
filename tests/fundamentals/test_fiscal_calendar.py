import datetime as dt
from dataclasses import replace
from decimal import Decimal

import pytest

from src.fundamentals.adapters.sec_companyfacts import SecExtractedFact
from src.fundamentals.concept_map import FactPeriodType
from src.fundamentals.fiscal_calendar import (
    FiscalCalendarIssueCode,
    FiscalTransitionDefinition,
    FiscalYearDefinition,
    IssuerFiscalCalendarPolicy,
    OpenFiscalYearDefinition,
    classify_sec_facts,
)
from src.fundamentals.types import FactLineage, StatementKind

CIK = "0000320193"
ACCEPTED_AT = dt.datetime(2024, 11, 1, 22, 1, 36, tzinfo=dt.timezone.utc)
INGESTED_AT = dt.datetime(2026, 9, 7, 14, 0, tzinfo=dt.timezone.utc)

FY2023 = FiscalYearDefinition(
    fiscal_year=2023,
    period_start=dt.date(2022, 9, 25),
    quarter_ends=(
        dt.date(2022, 12, 31),
        dt.date(2023, 4, 1),
        dt.date(2023, 7, 1),
        dt.date(2023, 9, 30),
    ),
)
FY2024 = FiscalYearDefinition(
    fiscal_year=2024,
    period_start=dt.date(2023, 10, 1),
    quarter_ends=(
        dt.date(2023, 12, 30),
        dt.date(2024, 3, 30),
        dt.date(2024, 6, 29),
        dt.date(2024, 9, 28),
    ),
)
TRANSITION_2025 = FiscalTransitionDefinition(
    fiscal_year=2025,
    period_start=dt.date(2024, 9, 29),
    period_end=dt.date(2024, 12, 31),
    form_type="10-KT",
)
OPEN_FY2025 = OpenFiscalYearDefinition(
    fiscal_year=2025,
    period_start=dt.date(2024, 9, 29),
    quarter_ends=(
        dt.date(2024, 12, 28),
        dt.date(2025, 3, 29),
        dt.date(2025, 6, 28),
    ),
)


def _policy(
    *years,
    cik=CIK,
    version="aapl-calendar-v1",
    transitions=(),
    open_fiscal_years=(),
):
    return IssuerFiscalCalendarPolicy(
        cik=cik,
        version=version,
        fiscal_years=years or (FY2023, FY2024),
        transitions=transitions,
        open_fiscal_years=open_fiscal_years,
    )


def _fact(
    *,
    period_start=dt.date(2023, 10, 1),
    period_end=dt.date(2024, 9, 28),
    report_date=dt.date(2024, 9, 28),
    statement_kind=StatementKind.INCOME_STATEMENT,
    period_type=FactPeriodType.DURATION,
    form_type="10-K",
    accession="0000320193-24-000123",
    raw_tag="RevenueFromContractWithCustomerExcludingAssessedTax",
    canonical_concept="revenue",
    value=Decimal("391035000000"),
    unit="USD",
    currency="USD",
    filing_fiscal_year=1900,
    filing_fiscal_period="wrong-on-purpose",
    frame=None,
    cik=CIK,
    is_amendment=None,
):
    if is_amendment is None:
        is_amendment = form_type.endswith("/A")
    return SecExtractedFact(
        entity_cik=cik,
        entity_name="Apple Inc.",
        taxonomy="us-gaap" if statement_kind is not StatementKind.COVER else "dei",
        raw_tag=raw_tag,
        canonical_concept=canonical_concept,
        statement_kind=statement_kind,
        period_type=period_type,
        period_start=period_start,
        period_end=period_end,
        value=value,
        unit=unit,
        currency=currency,
        provenance_accession_number=accession,
        form_type=form_type,
        is_amendment=is_amendment,
        filed_date=dt.date(2024, 11, 1),
        accepted_at=ACCEPTED_AT,
        report_date=report_date,
        primary_document="aapl-20240928.htm",
        filing_fiscal_year=filing_fiscal_year,
        filing_fiscal_period=filing_fiscal_period,
        frame=frame,
        lineage=FactLineage(
            source_adapter="sec_companyfacts",
            source_document_url="https://www.sec.gov/Archives/fixture.htm",
            concept_map_version="sec-companyfacts-v1",
            ingestion_batch_id="batch-sec-001",
            ingested_at=INGESTED_AT,
        ),
    )


def _assert_issue(result, code):
    assert not result.is_complete
    assert result.facts == ()
    assert len(result.issues) == 1
    assert result.issues[0].code is code


class TestDurationClassification:
    @pytest.mark.parametrize(
        ("period_start", "period_end", "fiscal_period", "periodicity"),
        (
            (dt.date(2023, 10, 1), dt.date(2023, 12, 30), "Q1", "quarterly"),
            (dt.date(2023, 12, 31), dt.date(2024, 3, 30), "Q2", "quarterly"),
            (dt.date(2023, 10, 1), dt.date(2024, 3, 30), "Q2YTD", "ytd"),
            (dt.date(2024, 3, 31), dt.date(2024, 6, 29), "Q3", "quarterly"),
            (dt.date(2023, 10, 1), dt.date(2024, 6, 29), "Q3YTD", "ytd"),
            (dt.date(2024, 6, 30), dt.date(2024, 9, 28), "Q4", "quarterly"),
            (dt.date(2023, 10, 1), dt.date(2024, 9, 28), "FY", "annual"),
        ),
    )
    def test_classifies_exact_duration_geometry(
        self, period_start, period_end, fiscal_period, periodicity
    ):
        result = classify_sec_facts(
            (_fact(period_start=period_start, period_end=period_end),),
            _policy(),
        )

        assert result.is_complete
        assert result.facts[0].period.fiscal_year == 2024
        assert result.facts[0].period.fiscal_period == fiscal_period
        assert result.facts[0].period.periodicity == periodicity

    def test_classifies_comparative_geometry_not_the_filings_raw_label(self):
        comparative = _fact(
            period_start=FY2023.period_start,
            period_end=FY2023.period_end,
            filing_fiscal_year=2024,
            filing_fiscal_period="FY",
        )

        result = classify_sec_facts((comparative,), _policy())

        assert result.is_complete
        assert result.facts[0].period.fiscal_year == 2023
        assert result.facts[0].period.fiscal_period == "FY"

    def test_one_10q_classifies_standalone_quarter_and_ytd_separately(self):
        quarter = _fact(
            period_start=dt.date(2023, 12, 31),
            period_end=dt.date(2024, 3, 30),
            report_date=dt.date(2024, 3, 30),
            form_type="10-Q",
        )
        year_to_date = replace(
            quarter,
            period_start=FY2024.period_start,
            raw_tag="NetIncomeLoss",
            canonical_concept="net_income",
        )

        result = classify_sec_facts((year_to_date, quarter), _policy())

        assert result.is_complete
        assert [fact.period.fiscal_period for fact in result.facts] == [
            "Q2YTD",
            "Q2",
        ]
        assert [fact.period.periodicity for fact in result.facts] == ["ytd", "quarterly"]

    def test_52_week_year_is_classified_from_exact_boundaries(self):
        result = classify_sec_facts((_fact(),), _policy(FY2024))

        assert result.is_complete
        assert (FY2024.period_end - FY2024.period_start).days + 1 == 364
        assert result.facts[0].period.periodicity == "annual"

    def test_53_week_year_is_classified_without_day_count_tolerance(self):
        fiscal_2025 = FiscalYearDefinition(
            fiscal_year=2025,
            period_start=dt.date(2024, 9, 29),
            quarter_ends=(
                dt.date(2024, 12, 28),
                dt.date(2025, 3, 29),
                dt.date(2025, 6, 28),
                dt.date(2025, 10, 4),
            ),
        )
        fact = _fact(
            period_start=fiscal_2025.period_start,
            period_end=fiscal_2025.period_end,
            report_date=fiscal_2025.period_end,
        )

        result = classify_sec_facts((fact,), _policy(fiscal_2025))

        assert result.is_complete
        assert (fiscal_2025.period_end - fiscal_2025.period_start).days + 1 == 371
        assert result.facts[0].period.fiscal_year == 2025

    def test_explicit_policy_handles_a_fiscal_year_end_shift(self):
        shifted_2025 = FiscalYearDefinition(
            fiscal_year=2025,
            period_start=dt.date(2024, 9, 29),
            quarter_ends=(
                dt.date(2024, 12, 28),
                dt.date(2025, 3, 29),
                dt.date(2025, 6, 28),
                dt.date(2025, 12, 31),
            ),
        )
        fact = _fact(
            period_start=shifted_2025.period_start,
            period_end=shifted_2025.period_end,
            report_date=shifted_2025.period_end,
        )

        result = classify_sec_facts((fact,), _policy(FY2024, shifted_2025))

        assert result.is_complete
        period = result.facts[0].period
        assert period.fiscal_year == 2025
        assert period.fiscal_period == "FY"
        assert period.period_start == dt.date(2024, 9, 29)
        assert period.period_end == dt.date(2025, 12, 31)
        assert period.periodicity == "annual"

    def test_classifies_exact_10kt_stub_without_calling_it_an_ordinary_year(self):
        fact = _fact(
            period_start=TRANSITION_2025.period_start,
            period_end=TRANSITION_2025.period_end,
            report_date=TRANSITION_2025.period_end,
            form_type="10-KT",
        )

        result = classify_sec_facts(
            (fact,),
            _policy(FY2024, transitions=(TRANSITION_2025,)),
        )

        assert result.is_complete
        assert result.facts[0].period.fiscal_year == 2025
        assert result.facts[0].period.fiscal_period == "TRANSITION"
        assert result.facts[0].period.periodicity == "transition"

    def test_transition_form_without_explicit_transition_policy_fails_loudly(self):
        fact = _fact(
            period_start=TRANSITION_2025.period_start,
            period_end=TRANSITION_2025.period_end,
            report_date=TRANSITION_2025.period_end,
            form_type="10-KT",
        )

        result = classify_sec_facts((fact,), _policy(FY2024))

        _assert_issue(result, FiscalCalendarIssueCode.UNRECOGNIZED_FILING_PERIOD)

    @pytest.mark.parametrize(
        ("period_start", "period_end", "fiscal_period", "periodicity"),
        (
            (dt.date(2024, 9, 29), dt.date(2024, 12, 28), "Q1", "quarterly"),
            (dt.date(2024, 12, 29), dt.date(2025, 3, 29), "Q2", "quarterly"),
            (dt.date(2024, 9, 29), dt.date(2025, 3, 29), "Q2YTD", "ytd"),
            (dt.date(2025, 3, 30), dt.date(2025, 6, 28), "Q3", "quarterly"),
            (dt.date(2024, 9, 29), dt.date(2025, 6, 28), "Q3YTD", "ytd"),
        ),
    )
    def test_open_year_classifies_only_completed_periods(
        self, period_start, period_end, fiscal_period, periodicity
    ):
        fact = _fact(
            period_start=period_start,
            period_end=period_end,
            report_date=period_end,
            form_type="10-Q",
        )

        result = classify_sec_facts(
            (fact,),
            _policy(FY2024, open_fiscal_years=(OPEN_FY2025,)),
        )

        assert result.is_complete
        assert result.facts[0].period.fiscal_year == 2025
        assert result.facts[0].period.fiscal_period == fiscal_period
        assert result.facts[0].period.periodicity == periodicity

    def test_open_year_never_invents_a_fourth_quarter_or_annual_period(self):
        unfiled_annual = _fact(
            period_start=OPEN_FY2025.period_start,
            period_end=dt.date(2025, 9, 27),
            report_date=dt.date(2025, 9, 27),
            form_type="10-K",
        )

        result = classify_sec_facts(
            (unfiled_annual,),
            _policy(FY2024, open_fiscal_years=(OPEN_FY2025,)),
        )

        _assert_issue(result, FiscalCalendarIssueCode.UNRECOGNIZED_FILING_PERIOD)


class TestInstantAndCoverClassification:
    @pytest.mark.parametrize(
        ("period_end", "fiscal_period", "periodicity"),
        (
            (dt.date(2023, 12, 30), "Q1", "quarterly"),
            (dt.date(2024, 3, 30), "Q2", "quarterly"),
            (dt.date(2024, 6, 29), "Q3", "quarterly"),
            (dt.date(2024, 9, 28), "FY", "annual"),
        ),
    )
    def test_classifies_balance_sheet_instants(self, period_end, fiscal_period, periodicity):
        fact = _fact(
            period_start=None,
            period_end=period_end,
            statement_kind=StatementKind.BALANCE_SHEET,
            period_type=FactPeriodType.INSTANT,
            raw_tag="InventoryNet",
            canonical_concept="inventory",
        )

        result = classify_sec_facts((fact,), _policy())

        assert result.is_complete
        assert result.facts[0].period.fiscal_period == fiscal_period
        assert result.facts[0].period.periodicity == periodicity

    def test_cover_date_uses_the_filings_report_period_for_fiscal_year(self):
        fact = _fact(
            period_start=None,
            period_end=dt.date(2024, 10, 18),
            statement_kind=StatementKind.COVER,
            period_type=FactPeriodType.COVER,
            raw_tag="EntityCommonStockSharesOutstanding",
            canonical_concept="common_shares_outstanding",
            unit="shares",
            currency=None,
        )

        result = classify_sec_facts((fact,), _policy())

        assert result.is_complete
        period = result.facts[0].period
        assert period.fiscal_year == 2024
        assert period.fiscal_period == "COVER"
        assert period.period_end == dt.date(2024, 10, 18)
        assert period.periodicity is None


class TestConversionIntegrity:
    def test_preserves_value_identity_provenance_and_lineage(self):
        source = _fact(is_amendment=True, form_type="10-K/A")

        result = classify_sec_facts((source,), _policy())

        fact = result.facts[0]
        assert fact.value is source.value
        assert fact.identity.concept == source.canonical_concept
        assert fact.identity.unit == source.unit
        assert fact.identity.currency == source.currency
        assert fact.identity.context.entity_cik == source.entity_cik
        assert fact.raw_tag == source.raw_tag
        assert fact.taxonomy == source.taxonomy
        assert fact.provenance.accession_number == source.provenance_accession_number
        assert fact.provenance.form_type == "10-K/A"
        assert fact.provenance.is_amendment
        assert fact.provenance.accepted_at == source.accepted_at
        assert fact.lineage.source_adapter == source.lineage.source_adapter
        assert fact.lineage.concept_map_version == source.lineage.concept_map_version
        assert fact.lineage.fiscal_calendar_version == "aapl-calendar-v1"
        assert result.calendar_version == "aapl-calendar-v1"

    def test_output_is_deterministic_for_reversed_input(self):
        annual = _fact()
        comparative = _fact(
            period_start=FY2023.period_start,
            period_end=FY2023.period_end,
            value=Decimal("383285000000"),
        )

        assert classify_sec_facts((annual, comparative), _policy()) == classify_sec_facts(
            (comparative, annual), _policy()
        )


class TestFailClosedClassification:
    def test_one_unclassifiable_fact_discards_valid_output(self):
        invalid = _fact(period_end=dt.date(2024, 9, 27))

        result = classify_sec_facts((_fact(), invalid), _policy())

        _assert_issue(result, FiscalCalendarIssueCode.UNCLASSIFIABLE_FACT_PERIOD)

    def test_unrecognized_filing_report_date_is_refused(self):
        result = classify_sec_facts(
            (_fact(report_date=dt.date(2024, 9, 27)),),
            _policy(),
        )

        _assert_issue(result, FiscalCalendarIssueCode.UNRECOGNIZED_FILING_PERIOD)

    @pytest.mark.parametrize(
        ("form_type", "report_date"),
        (
            ("10-K", dt.date(2024, 6, 29)),
            ("10-K/A", dt.date(2024, 6, 29)),
            ("10-Q", dt.date(2024, 9, 28)),
            ("10-Q/A", dt.date(2024, 9, 28)),
        ),
    )
    def test_form_must_match_the_report_period(self, form_type, report_date):
        result = classify_sec_facts(
            (_fact(form_type=form_type, report_date=report_date),),
            _policy(),
        )

        _assert_issue(result, FiscalCalendarIssueCode.FORM_PERIOD_MISMATCH)

    def test_wrong_issuer_is_refused(self):
        result = classify_sec_facts((_fact(cik="0000789019"),), _policy())

        _assert_issue(result, FiscalCalendarIssueCode.ISSUER_MISMATCH)

    def test_statement_kind_and_period_type_must_agree(self):
        source = _fact()
        malformed = replace(source, period_type=FactPeriodType.INSTANT, period_start=None)

        result = classify_sec_facts((malformed,), _policy())

        _assert_issue(result, FiscalCalendarIssueCode.INVALID_FACT)

    @pytest.mark.parametrize(
        "changes",
        (
            {"form_type": "8-K"},
            {"form_type": "10-K/A", "is_amendment": False},
            {"form_type": "10-K", "is_amendment": True},
            {"filed_date": "2024-11-01"},
        ),
    )
    def test_malformed_source_metadata_is_refused(self, changes):
        malformed = replace(_fact(), **changes)

        result = classify_sec_facts((malformed,), _policy())

        _assert_issue(result, FiscalCalendarIssueCode.INVALID_FACT)

    def test_empty_input_is_refused(self):
        _assert_issue(
            classify_sec_facts((), _policy()),
            FiscalCalendarIssueCode.EMPTY_FACTS,
        )

    def test_non_iterable_input_is_refused(self):
        _assert_issue(
            classify_sec_facts(None, _policy()),
            FiscalCalendarIssueCode.INVALID_FACT,
        )


class TestFiscalCalendarPolicyIntegrity:
    def test_policy_is_normalized_and_sorted(self):
        policy = _policy(FY2024, FY2023, cik="320193")

        assert policy.cik == CIK
        assert policy.fiscal_years == (FY2023, FY2024)

    @pytest.mark.parametrize("fiscal_year", (True, 0, "2024"))
    def test_fiscal_year_must_be_a_positive_integer(self, fiscal_year):
        with pytest.raises(ValueError, match="fiscal_year"):
            replace(FY2024, fiscal_year=fiscal_year)

    def test_requires_exactly_four_ordered_quarter_ends(self):
        with pytest.raises(ValueError, match="exactly four"):
            replace(FY2024, quarter_ends=FY2024.quarter_ends[:3])
        with pytest.raises(ValueError, match="strictly increasing"):
            replace(
                FY2024,
                quarter_ends=(
                    FY2024.quarter_ends[0],
                    FY2024.quarter_ends[0],
                    FY2024.quarter_ends[2],
                    FY2024.quarter_ends[3],
                ),
            )

    def test_rejects_duplicate_fiscal_year_numbers(self):
        with pytest.raises(ValueError, match="unique"):
            _policy(FY2024, FY2024)

    def test_rejects_overlapping_fiscal_years(self):
        overlapping = replace(FY2024, fiscal_year=2025)
        with pytest.raises(ValueError, match="overlap"):
            _policy(FY2024, overlapping)

    def test_rejects_calendar_dates_that_reverse_fiscal_year_numbers(self):
        earlier_2025 = replace(FY2023, fiscal_year=2025)
        later_2024 = replace(FY2024, fiscal_year=2024)
        with pytest.raises(ValueError, match="increase"):
            _policy(earlier_2025, later_2024)

    def test_transition_must_not_overlap_an_ordinary_fiscal_year(self):
        overlapping = replace(
            TRANSITION_2025,
            fiscal_year=2024,
            period_start=dt.date(2024, 6, 1),
        )
        with pytest.raises(ValueError, match="unique|overlap"):
            _policy(FY2024, transitions=(overlapping,))

    def test_transition_form_is_explicit(self):
        with pytest.raises(ValueError, match="form_type"):
            replace(TRANSITION_2025, form_type="10-K")

    @pytest.mark.parametrize("quarter_ends", ((), OPEN_FY2025.quarter_ends + (dt.date(2025, 9, 27),)))
    def test_open_year_requires_one_to_three_completed_quarter_ends(self, quarter_ends):
        with pytest.raises(ValueError, match="one to three"):
            replace(OPEN_FY2025, quarter_ends=quarter_ends)

    def test_policy_allows_only_one_latest_open_year(self):
        second_open = replace(
            OPEN_FY2025,
            fiscal_year=2026,
            period_start=dt.date(2025, 9, 28),
            quarter_ends=(dt.date(2025, 12, 27),),
        )
        with pytest.raises(ValueError, match="at most one"):
            _policy(FY2024, open_fiscal_years=(OPEN_FY2025, second_open))

        closed_2026 = FiscalYearDefinition(
            fiscal_year=2026,
            period_start=dt.date(2025, 9, 28),
            quarter_ends=(
                dt.date(2025, 12, 27),
                dt.date(2026, 3, 28),
                dt.date(2026, 6, 27),
                dt.date(2026, 9, 26),
            ),
        )
        with pytest.raises(ValueError, match="latest"):
            _policy(FY2024, closed_2026, open_fiscal_years=(OPEN_FY2025,))
