import datetime as dt

from src.fundamentals.adapters.fixture import make_fact, make_lineage, make_period, make_provenance
from src.fundamentals.quarterly import (
    QuarterValueOrigin,
    QuarterlyAssemblyIssueCode,
    assemble_quarterly_fundamentals,
)
from src.fundamentals.selection import select_point_in_time
from src.fundamentals.types import StatementKind

CIK = "0001111111"
CUTOFF = dt.datetime(2025, 1, 1, tzinfo=dt.timezone.utc)

FY2023_DATES = (
    dt.date(2022, 10, 2),
    dt.date(2022, 12, 31),
    dt.date(2023, 4, 1),
    dt.date(2023, 7, 1),
    dt.date(2023, 9, 30),
)
FY2024_DATES = (
    dt.date(2023, 10, 1),
    dt.date(2023, 12, 30),
    dt.date(2024, 3, 30),
    dt.date(2024, 6, 29),
    dt.date(2024, 9, 28),
)


def _provenance(fiscal_year, quarter=None, *, accepted_at=None, amendment=False):
    sequence = quarter or 9
    if amendment:
        sequence += 100
    form = "10-K" if quarter is None else "10-Q"
    if amendment:
        form += "/A"
    return make_provenance(
        accession_number=f"0001111111-{str(fiscal_year)[-2:]}-{sequence:06d}",
        filed_date=dt.date(fiscal_year, min(sequence + 1, 12), 1),
        accepted_at=accepted_at
        or dt.datetime(fiscal_year, min(sequence + 1, 12), 1, tzinfo=dt.timezone.utc),
        form_type=form,
        is_amendment=amendment,
    )


def _year_facts(
    fiscal_year,
    dates,
    values,
    *,
    concept="revenue",
    statement_kind=StatementKind.INCOME_STATEMENT,
):
    start, q1_end, q2_end, q3_end, fy_end = dates
    starts = (start, q1_end + dt.timedelta(days=1), q2_end + dt.timedelta(days=1))
    facts = []
    for quarter, (period_start, period_end, value) in enumerate(
        zip(starts, (q1_end, q2_end, q3_end), values[:3]), start=1
    ):
        facts.append(
            make_fact(
                statement_kind=statement_kind,
                concept=concept,
                period=make_period(
                    fiscal_year=fiscal_year,
                    fiscal_period=f"Q{quarter}",
                    period_start=period_start,
                    period_end=period_end,
                    periodicity="quarterly",
                ),
                value=value,
                provenance=_provenance(fiscal_year, quarter),
            )
        )
    facts.append(
        make_fact(
            statement_kind=statement_kind,
            concept=concept,
            period=make_period(
                fiscal_year=fiscal_year,
                fiscal_period="FY",
                period_start=start,
                period_end=fy_end,
                periodicity="annual",
            ),
            value=values[3],
            provenance=_provenance(fiscal_year),
        )
    )
    return facts


def _ytd_year_facts(
    fiscal_year,
    dates,
    values,
    *,
    concept="operating_cash_flow",
):
    start, q1_end, q2_end, q3_end, fy_end = dates
    q1, q2, q3, q4 = values
    return [
        make_fact(
            statement_kind=StatementKind.CASH_FLOW,
            concept=concept,
            period=make_period(
                fiscal_year=fiscal_year,
                fiscal_period="Q1",
                period_start=start,
                period_end=q1_end,
                periodicity="quarterly",
            ),
            value=q1,
            provenance=_provenance(fiscal_year, 1),
        ),
        make_fact(
            statement_kind=StatementKind.CASH_FLOW,
            concept=concept,
            period=make_period(
                fiscal_year=fiscal_year,
                fiscal_period="Q2YTD",
                period_start=start,
                period_end=q2_end,
                periodicity="ytd",
            ),
            value=q1 + q2,
            provenance=_provenance(fiscal_year, 2),
        ),
        make_fact(
            statement_kind=StatementKind.CASH_FLOW,
            concept=concept,
            period=make_period(
                fiscal_year=fiscal_year,
                fiscal_period="Q3YTD",
                period_start=start,
                period_end=q3_end,
                periodicity="ytd",
            ),
            value=q1 + q2 + q3,
            provenance=_provenance(fiscal_year, 3),
        ),
        make_fact(
            statement_kind=StatementKind.CASH_FLOW,
            concept=concept,
            period=make_period(
                fiscal_year=fiscal_year,
                fiscal_period="FY",
                period_start=start,
                period_end=fy_end,
                periodicity="annual",
            ),
            value=q1 + q2 + q3 + q4,
            provenance=_provenance(fiscal_year),
        ),
    ]


def _history(facts, cutoff=CUTOFF):
    return select_point_in_time(facts, cutoff, cik=CIK)


class TestExactQuarterAssembly:
    def test_preserves_reported_quarters_and_exposes_a_q4_spike(self):
        history = _history(_year_facts(2023, FY2023_DATES, (100, 105, 110, 515)))

        result = assemble_quarterly_fundamentals(history, required_concepts=("revenue",))

        assert result.is_complete
        assert [quarter.value for quarter in result.quarters] == [100, 105, 110, 200]
        assert [quarter.origin for quarter in result.quarters] == [
            QuarterValueOrigin.REPORTED,
            QuarterValueOrigin.REPORTED,
            QuarterValueOrigin.REPORTED,
            QuarterValueOrigin.DERIVED_Q4,
        ]
        assert result.ttm_values[0].value == 515
        assert result.ttm_values[0].quarters == result.quarters

    def test_partial_current_year_is_valid_and_ttm_uses_four_consecutive_quarters(self):
        facts = _year_facts(2023, FY2023_DATES, (80, 90, 100, 400))
        start, q1_end, q2_end, _q3_end, _fy_end = FY2024_DATES
        facts.extend(
            (
                make_fact(
                    statement_kind=StatementKind.INCOME_STATEMENT,
                    concept="revenue",
                    period=make_period(
                        fiscal_year=2024,
                        fiscal_period="Q1",
                        period_start=start,
                        period_end=q1_end,
                        periodicity="quarterly",
                    ),
                    value=120,
                    provenance=_provenance(2024, 1),
                ),
                make_fact(
                    statement_kind=StatementKind.INCOME_STATEMENT,
                    concept="revenue",
                    period=make_period(
                        fiscal_year=2024,
                        fiscal_period="Q2",
                        period_start=q1_end + dt.timedelta(days=1),
                        period_end=q2_end,
                        periodicity="quarterly",
                    ),
                    value=130,
                    provenance=_provenance(2024, 2),
                ),
            )
        )

        result = assemble_quarterly_fundamentals(
            _history(facts), required_concepts=("revenue",)
        )

        assert [quarter.value for quarter in result.quarters] == [80, 90, 100, 130, 120, 130]
        assert result.ttm_values[-1].value == 480
        assert [q.value for q in result.ttm_values[-1].quarters] == [100, 130, 120, 130]

    def test_point_in_time_amendment_changes_only_its_selected_quarter(self):
        facts = _year_facts(2023, FY2023_DATES, (100, 100, 100, 500))
        original_q2 = facts[1]
        amended_q2 = make_fact(
            statement_kind=StatementKind.INCOME_STATEMENT,
            concept="revenue",
            period=original_q2.period,
            value=125,
            provenance=_provenance(
                2023,
                2,
                accepted_at=dt.datetime(2024, 1, 1, tzinfo=dt.timezone.utc),
                amendment=True,
            ),
        )

        result = assemble_quarterly_fundamentals(
            _history((*facts, amended_q2)), required_concepts=("revenue",)
        )

        assert [quarter.value for quarter in result.quarters] == [100, 125, 100, 175]
        assert result.quarters[1].source_facts == (amended_q2,)

    def test_leading_annual_only_history_before_quarterly_coverage_is_ignored(self):
        annual_only = make_fact(
            statement_kind=StatementKind.INCOME_STATEMENT,
            concept="revenue",
            period=make_period(
                fiscal_year=2022,
                fiscal_period="FY",
                period_start=dt.date(2021, 9, 26),
                period_end=dt.date(2022, 9, 24),
                periodicity="annual",
            ),
            value=350,
            provenance=_provenance(2022),
        )
        facts = (annual_only, *_year_facts(2023, FY2023_DATES, (80, 90, 100, 400)))

        result = assemble_quarterly_fundamentals(
            _history(facts), required_concepts=("revenue",)
        )

        assert result.is_complete
        assert {quarter.fiscal_year for quarter in result.quarters} == {2023}

    def test_derives_standalone_cash_flows_from_exact_ytd_differences(self):
        facts = _ytd_year_facts(2024, FY2024_DATES, (10, 20, 30, 40))

        result = assemble_quarterly_fundamentals(
            _history(facts), required_concepts=("operating_cash_flow",)
        )

        assert result.is_complete
        assert [quarter.value for quarter in result.quarters] == [10, 20, 30, 40]
        assert [quarter.origin for quarter in result.quarters] == [
            QuarterValueOrigin.REPORTED,
            QuarterValueOrigin.DERIVED_YTD_DIFFERENCE,
            QuarterValueOrigin.DERIVED_YTD_DIFFERENCE,
            QuarterValueOrigin.DERIVED_Q4,
        ]
        assert result.ttm_values[0].value == 100

    def test_partial_ytd_cash_flow_year_is_valid(self):
        facts = _ytd_year_facts(2024, FY2024_DATES, (10, 20, 30, 40))[:2]

        result = assemble_quarterly_fundamentals(
            _history(facts), required_concepts=("operating_cash_flow",)
        )

        assert result.is_complete
        assert [quarter.value for quarter in result.quarters] == [10, 20]

    def test_optional_concept_is_assembled_when_present_and_ignored_when_absent(self):
        revenue = _year_facts(2024, FY2024_DATES, (100, 110, 120, 500))

        absent = assemble_quarterly_fundamentals(
            _history(revenue),
            required_concepts=("revenue",),
            optional_concepts=("exchange_rate_effect",),
        )
        present = assemble_quarterly_fundamentals(
            _history(
                revenue
                + _year_facts(
                    2024,
                    FY2024_DATES,
                    (1, 2, 3, 10),
                    concept="exchange_rate_effect",
                    statement_kind=StatementKind.CASH_FLOW,
                )
            ),
            required_concepts=("revenue",),
            optional_concepts=("exchange_rate_effect",),
        )

        assert absent.is_complete
        assert {quarter.key.concept for quarter in absent.quarters} == {"revenue"}
        assert present.is_complete
        assert {quarter.key.concept for quarter in present.quarters} == {
            "exchange_rate_effect",
            "revenue",
        }


class TestFailClosedQuarterAssembly:
    def test_full_year_with_a_missing_reported_quarter_refuses_all_output(self):
        facts = _year_facts(2023, FY2023_DATES, (100, 110, 120, 500))

        result = assemble_quarterly_fundamentals(
            _history((facts[0], facts[2], facts[3])), required_concepts=("revenue",)
        )

        assert not result.is_complete
        assert result.quarters == ()
        assert result.issues[0].code is QuarterlyAssemblyIssueCode.MISSING_PERIOD

    def test_reported_q4_must_equal_the_full_year_reconciliation(self):
        facts = _year_facts(2023, FY2023_DATES, (100, 110, 120, 500))
        q4 = make_fact(
            statement_kind=StatementKind.INCOME_STATEMENT,
            concept="revenue",
            period=make_period(
                fiscal_year=2023,
                fiscal_period="Q4",
                period_start=dt.date(2023, 7, 2),
                period_end=dt.date(2023, 9, 30),
                periodicity="quarterly",
            ),
            value=169,
            provenance=_provenance(2023),
        )

        result = assemble_quarterly_fundamentals(
            _history((*facts, q4)), required_concepts=("revenue",)
        )

        assert result.issues[0].code is QuarterlyAssemblyIssueCode.CONTRADICTORY_PERIOD

    def test_reported_q2_must_equal_the_six_month_ytd_difference(self):
        facts = _ytd_year_facts(2024, FY2024_DATES, (10, 20, 30, 40))
        start, q1_end, q2_end, _q3_end, _fy_end = FY2024_DATES
        facts.append(
            make_fact(
                statement_kind=StatementKind.CASH_FLOW,
                concept="operating_cash_flow",
                period=make_period(
                    fiscal_year=2024,
                    fiscal_period="Q2",
                    period_start=q1_end + dt.timedelta(days=1),
                    period_end=q2_end,
                    periodicity="quarterly",
                ),
                value=21,
                provenance=_provenance(2024, 2),
            )
        )

        result = assemble_quarterly_fundamentals(
            _history(facts), required_concepts=("operating_cash_flow",)
        )

        assert result.issues[0].code is QuarterlyAssemblyIssueCode.CONTRADICTORY_PERIOD

    def test_prior_quarterly_year_cannot_be_treated_as_still_open(self):
        facts = _year_facts(2023, FY2023_DATES, (80, 90, 100, 400))[:-1]
        start, q1_end, _q2_end, _q3_end, _fy_end = FY2024_DATES
        facts.append(
            make_fact(
                statement_kind=StatementKind.INCOME_STATEMENT,
                concept="revenue",
                period=make_period(
                    fiscal_year=2024,
                    fiscal_period="Q1",
                    period_start=start,
                    period_end=q1_end,
                    periodicity="quarterly",
                ),
                value=120,
                provenance=_provenance(2024, 1),
            )
        )

        result = assemble_quarterly_fundamentals(
            _history(facts), required_concepts=("revenue",)
        )

        assert result.issues[0].code is QuarterlyAssemblyIssueCode.MISSING_PERIOD

    def test_dimensional_fact_refuses_consolidated_arithmetic(self):
        facts = _year_facts(2023, FY2023_DATES, (100, 110, 120, 500))
        dimensional = make_fact(
            statement_kind=StatementKind.INCOME_STATEMENT,
            concept="revenue",
            period=facts[0].period,
            value=40,
            provenance=facts[0].provenance,
            dimensions=(("BusinessSegmentsAxis", "ConsumerMember"),),
        )

        result = assemble_quarterly_fundamentals(
            _history((*facts, dimensional)), required_concepts=("revenue",)
        )

        assert result.issues[0].code is QuarterlyAssemblyIssueCode.DIMENSIONAL_CONTEXT

    def test_mixed_calendar_lineage_refuses_arithmetic(self):
        facts = _year_facts(2023, FY2023_DATES, (100, 110, 120, 500))
        facts[1] = make_fact(
            statement_kind=StatementKind.INCOME_STATEMENT,
            concept="revenue",
            period=facts[1].period,
            value=facts[1].value,
            provenance=facts[1].provenance,
            lineage=make_lineage(fiscal_calendar_version="different-calendar"),
        )

        result = assemble_quarterly_fundamentals(
            _history(facts), required_concepts=("revenue",)
        )

        assert result.issues[0].code is QuarterlyAssemblyIssueCode.INCOMPATIBLE_LINEAGE

    def test_balance_sheet_snapshot_is_not_mislabeled_as_a_quarterly_flow(self):
        balance_sheet_fact = make_fact(
            statement_kind=StatementKind.BALANCE_SHEET,
            concept="cash_and_cash_equivalents",
            period=make_period(
                fiscal_year=2023,
                fiscal_period="Q1",
                period_end=dt.date(2022, 12, 31),
                periodicity="quarterly",
            ),
            value=50,
            provenance=_provenance(2023, 1),
        )

        result = assemble_quarterly_fundamentals(
            _history((balance_sheet_fact,)),
            required_concepts=("cash_and_cash_equivalents",),
        )

        assert result.issues[0].code is QuarterlyAssemblyIssueCode.UNSUPPORTED_SERIES

    def test_required_concepts_must_be_nonempty_and_unique(self):
        history = _history(_year_facts(2023, FY2023_DATES, (100, 110, 120, 500)))

        for concepts in ((), ("revenue", "revenue")):
            result = assemble_quarterly_fundamentals(
                history, required_concepts=concepts
            )
            assert result.issues[0].code is QuarterlyAssemblyIssueCode.INVALID_REQUIRED_CONCEPTS

    def test_required_and_optional_concepts_must_not_overlap(self):
        history = _history(_year_facts(2023, FY2023_DATES, (100, 110, 120, 500)))

        result = assemble_quarterly_fundamentals(
            history,
            required_concepts=("revenue",),
            optional_concepts=("revenue",),
        )

        assert result.issues[0].code is QuarterlyAssemblyIssueCode.INVALID_REQUIRED_CONCEPTS
