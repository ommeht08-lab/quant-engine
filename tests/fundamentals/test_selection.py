import datetime as dt
from decimal import Decimal

import pytest

from src.fundamentals.adapters.fixture import DEFAULT_TEST_CIK, make_fact, make_period, make_provenance
from src.fundamentals.selection import select_point_in_time
from src.fundamentals.time_policy import US_EASTERN, knowledge_cutoff_for_date
from src.fundamentals.types import FactContext, FactIdentity, FinancialFact, StatementKind

CIK = DEFAULT_TEST_CIK
OTHER_CIK = "0002222222"


def _annual_period(fiscal_year: int, period_end: dt.date):
    """A duration (income-statement / cash-flow) annual period."""
    return make_period(
        fiscal_year=fiscal_year,
        fiscal_period="FY",
        period_start=dt.date(period_end.year - 1, period_end.month, period_end.day) + dt.timedelta(days=1),
        period_end=period_end,
        periodicity="annual",
    )


def _instant_period(fiscal_year: int, period_end: dt.date, fiscal_period: str = "FY", periodicity: str = "annual"):
    """A balance-sheet instant period — no period_start, ever."""
    return make_period(
        fiscal_year=fiscal_year, fiscal_period=fiscal_period, period_end=period_end, periodicity=periodicity
    )


def _find_period(periods, period_end):
    matches = [p for p in periods if p.period.period_end == period_end]
    assert len(matches) == 1, f"expected exactly one period ending {period_end}, found {len(matches)}"
    return matches[0]


class TestBeforeAfterKnowledgeCutoff:
    def test_fact_eligible_before_cutoff_is_selected(self):
        provenance = make_provenance(
            accession_number="0000000001-24-000001",
            filed_date=dt.date(2024, 2, 1),
            accepted_at=dt.datetime(2024, 2, 1, 10, 0, tzinfo=US_EASTERN),
        )
        period = _annual_period(2023, dt.date(2023, 12, 31))
        fact = make_fact(
            statement_kind=StatementKind.INCOME_STATEMENT, concept="revenue", period=period,
            value="1000", provenance=provenance,
        )
        cutoff = dt.datetime(2024, 3, 1, tzinfo=US_EASTERN)

        history = select_point_in_time([fact], cutoff, cik=CIK)

        assert len(history.income_statement_periods) == 1
        assert history.income_statement_periods[0].fact_for(fact.identity).value == Decimal("1000")

    def test_fact_eligible_exactly_at_cutoff_is_selected(self):
        accepted = dt.datetime(2024, 2, 1, 16, 0, tzinfo=US_EASTERN)
        provenance = make_provenance(
            accession_number="0000000001-24-000001", filed_date=dt.date(2024, 2, 1), accepted_at=accepted
        )
        period = _annual_period(2023, dt.date(2023, 12, 31))
        fact = make_fact(
            statement_kind=StatementKind.INCOME_STATEMENT, concept="revenue", period=period,
            value="1000", provenance=provenance,
        )

        history = select_point_in_time([fact], accepted, cik=CIK)  # cutoff == eligible_at exactly

        assert len(history.income_statement_periods) == 1

    def test_fact_eligible_after_cutoff_is_excluded(self):
        provenance = make_provenance(
            accession_number="0000000001-24-000001",
            filed_date=dt.date(2024, 4, 1),
            accepted_at=dt.datetime(2024, 4, 1, 10, 0, tzinfo=US_EASTERN),
        )
        period = _annual_period(2023, dt.date(2023, 12, 31))
        fact = make_fact(
            statement_kind=StatementKind.INCOME_STATEMENT, concept="revenue", period=period,
            value="1000", provenance=provenance,
        )
        cutoff = dt.datetime(2024, 3, 1, tzinfo=US_EASTERN)  # before the filing was accepted

        history = select_point_in_time([fact], cutoff, cik=CIK)

        assert history.income_statement_periods == ()


class TestFilingAcceptedAfterMarketClose:
    def test_same_day_after_close_filing_is_excluded_then_included_next_cutoff(self):
        filed = dt.date(2024, 11, 1)
        accepted_after_close = dt.datetime(2024, 11, 1, 16, 32, tzinfo=US_EASTERN)
        provenance = make_provenance(
            accession_number="0000000001-24-000001", filed_date=filed, accepted_at=accepted_after_close
        )
        period = _instant_period(2024, dt.date(2024, 9, 30))
        fact = make_fact(
            statement_kind=StatementKind.BALANCE_SHEET, concept="total_assets", period=period,
            value="500", provenance=provenance,
        )

        same_day_cutoff = knowledge_cutoff_for_date(dt.date(2024, 11, 1))  # 16:00 ET, before 16:32 acceptance
        next_day_cutoff = knowledge_cutoff_for_date(dt.date(2024, 11, 4))

        same_day_history = select_point_in_time([fact], same_day_cutoff, cik=CIK)
        next_day_history = select_point_in_time([fact], next_day_cutoff, cik=CIK)

        assert same_day_history.balance_sheet_periods == ()
        assert len(next_day_history.balance_sheet_periods) == 1


class TestMissingAcceptedTimestampFallback:
    def test_fact_with_no_accepted_at_is_eligible_only_after_conservative_fallback(self):
        filed = dt.date(2024, 5, 1)
        provenance = make_provenance(
            accession_number="0000000001-24-000002", filed_date=filed, accepted_at=None
        )
        period = _annual_period(2023, dt.date(2023, 12, 31))
        fact = make_fact(
            statement_kind=StatementKind.CASH_FLOW, concept="operating_cash_flow", period=period,
            value="200", provenance=provenance,
        )

        before_fallback = dt.datetime(2024, 5, 1, 12, 0, tzinfo=US_EASTERN)
        at_true_end_of_day = dt.datetime(2024, 5, 1, 23, 59, 59, 999999, tzinfo=US_EASTERN)

        assert select_point_in_time([fact], before_fallback, cik=CIK).cash_flow_periods == ()
        assert len(select_point_in_time([fact], at_true_end_of_day, cik=CIK).cash_flow_periods) == 1


class TestPartialAmendment:
    def test_amendment_replaces_only_the_restated_identity(self):
        original_provenance = make_provenance(
            accession_number="0000000001-23-000010",
            filed_date=dt.date(2023, 2, 1),
            accepted_at=dt.datetime(2023, 2, 1, 10, 0, tzinfo=US_EASTERN),
        )
        amendment_provenance = make_provenance(
            accession_number="0000000001-23-000099",
            filed_date=dt.date(2023, 6, 1),
            accepted_at=dt.datetime(2023, 6, 1, 10, 0, tzinfo=US_EASTERN),
            form_type="10-K/A",
            is_amendment=True,
        )
        period = _instant_period(2022, dt.date(2022, 12, 31))

        original_assets = make_fact(
            statement_kind=StatementKind.BALANCE_SHEET, concept="total_assets", period=period,
            value="39572", provenance=original_provenance,
        )
        amended_assets = make_fact(
            statement_kind=StatementKind.BALANCE_SHEET, concept="total_assets", period=period,
            value="36171", provenance=amendment_provenance,
        )
        # A different concept, same period, reported ONLY by the original
        # filing — the amendment never touches it.
        untouched_liabilities = make_fact(
            statement_kind=StatementKind.BALANCE_SHEET, concept="total_liabilities", period=period,
            value="20000", provenance=original_provenance,
        )

        cutoff_before_amendment = dt.datetime(2023, 3, 1, tzinfo=US_EASTERN)
        cutoff_after_amendment = dt.datetime(2023, 7, 1, tzinfo=US_EASTERN)

        before = select_point_in_time(
            [original_assets, amended_assets, untouched_liabilities], cutoff_before_amendment, cik=CIK
        )
        after = select_point_in_time(
            [original_assets, amended_assets, untouched_liabilities], cutoff_after_amendment, cik=CIK
        )

        before_period = _find_period(before.balance_sheet_periods, period.period_end)
        after_period = _find_period(after.balance_sheet_periods, period.period_end)

        assert before_period.fact_for(original_assets.identity).value == Decimal("39572")
        assert after_period.fact_for(amended_assets.identity).value == Decimal("36171")
        assert before_period.fact_for(untouched_liabilities.identity).value == Decimal("20000")
        assert after_period.fact_for(untouched_liabilities.identity).value == Decimal("20000")


class TestLaterComparativeRestatement:
    def test_a_later_filings_repeated_comparative_can_still_win_without_a_formal_amendment(self):
        period = _instant_period(2022, dt.date(2022, 12, 31))

        original = make_provenance(
            accession_number="0000000001-23-000010",
            filed_date=dt.date(2023, 2, 1),
            accepted_at=dt.datetime(2023, 2, 1, 10, 0, tzinfo=US_EASTERN),
        )
        later_10q_comparative = make_provenance(
            accession_number="0000000001-23-000050",
            filed_date=dt.date(2023, 8, 1),
            accepted_at=dt.datetime(2023, 8, 1, 10, 0, tzinfo=US_EASTERN),
            form_type="10-Q",
            is_amendment=False,
        )

        original_fact = make_fact(
            statement_kind=StatementKind.BALANCE_SHEET, concept="total_assets", period=period,
            value="100", provenance=original,
        )
        comparative_fact = make_fact(
            statement_kind=StatementKind.BALANCE_SHEET, concept="total_assets", period=period,
            value="97", provenance=later_10q_comparative,  # quietly corrected in a later comparative
        )

        history = select_point_in_time(
            [original_fact, comparative_fact], dt.datetime(2023, 9, 1, tzinfo=US_EASTERN), cik=CIK
        )

        resolved_period = _find_period(history.balance_sheet_periods, period.period_end)
        assert resolved_period.fact_for(original_fact.identity).value == Decimal("97")


class TestQuarterVsYtdIsolation:
    def test_quarter_and_ytd_sharing_a_period_end_are_never_merged(self):
        provenance = make_provenance(
            accession_number="0000000001-24-000003",
            filed_date=dt.date(2024, 7, 25),
            accepted_at=dt.datetime(2024, 7, 25, 10, 0, tzinfo=US_EASTERN),
        )
        quarter_only = make_period(
            fiscal_year=2024, fiscal_period="Q2", period_start=dt.date(2024, 4, 1),
            period_end=dt.date(2024, 6, 30), periodicity="quarterly",
        )
        year_to_date = make_period(
            fiscal_year=2024, fiscal_period="Q2YTD", period_start=dt.date(2024, 1, 1),
            period_end=dt.date(2024, 6, 30), periodicity="ytd",
        )
        q2_fact = make_fact(
            statement_kind=StatementKind.CASH_FLOW, concept="operating_cash_flow",
            period=quarter_only, value="30", provenance=provenance,
        )
        ytd_fact = make_fact(
            statement_kind=StatementKind.CASH_FLOW, concept="operating_cash_flow",
            period=year_to_date, value="65", provenance=provenance,
        )

        history = select_point_in_time(
            [q2_fact, ytd_fact], dt.datetime(2024, 8, 1, tzinfo=US_EASTERN), cik=CIK
        )

        assert len(history.cash_flow_periods) == 2
        quarter_result = next(p for p in history.cash_flow_periods if p.period.periodicity == "quarterly")
        ytd_result = next(p for p in history.cash_flow_periods if p.period.periodicity == "ytd")
        assert quarter_result.fact_for(q2_fact.identity).value == Decimal("30")
        assert ytd_result.fact_for(ytd_fact.identity).value == Decimal("65")


class TestUnitCurrencyContextIsolation:
    def _base(self):
        provenance = make_provenance(
            accession_number="0000000001-24-000004",
            filed_date=dt.date(2024, 2, 1),
            accepted_at=dt.datetime(2024, 2, 1, 10, 0, tzinfo=US_EASTERN),
        )
        period = _annual_period(2023, dt.date(2023, 12, 31))
        return provenance, period

    def test_different_currency_facts_both_survive(self):
        provenance, period = self._base()
        usd_fact = make_fact(
            statement_kind=StatementKind.INCOME_STATEMENT, concept="revenue", period=period,
            value="1000", provenance=provenance, currency="USD",
        )
        eur_fact = make_fact(
            statement_kind=StatementKind.INCOME_STATEMENT, concept="revenue", period=period,
            value="920", provenance=provenance, currency="EUR",
        )

        history = select_point_in_time(
            [usd_fact, eur_fact], dt.datetime(2024, 3, 1, tzinfo=US_EASTERN), cik=CIK
        )

        resolved = _find_period(history.income_statement_periods, period.period_end)
        assert len(resolved.facts) == 2
        assert resolved.fact_for(usd_fact.identity).value == Decimal("1000")
        assert resolved.fact_for(eur_fact.identity).value == Decimal("920")

    def test_different_unit_facts_both_survive(self):
        provenance, period = self._base()
        dollars = make_fact(
            statement_kind=StatementKind.INCOME_STATEMENT, concept="eps", period=period,
            value="2.50", provenance=provenance, unit="USD_per_share",
        )
        shares = make_fact(
            statement_kind=StatementKind.INCOME_STATEMENT, concept="eps", period=period,
            value="400", provenance=provenance, unit="shares", currency=None,
        )

        history = select_point_in_time(
            [dollars, shares], dt.datetime(2024, 3, 1, tzinfo=US_EASTERN), cik=CIK
        )

        resolved = _find_period(history.income_statement_periods, period.period_end)
        assert len(resolved.facts) == 2

    def test_dimensional_context_never_merges_with_consolidated(self):
        provenance, period = self._base()
        consolidated = make_fact(
            statement_kind=StatementKind.INCOME_STATEMENT, concept="revenue", period=period,
            value="1000", provenance=provenance, dimensions=(),
        )
        segment = make_fact(
            statement_kind=StatementKind.INCOME_STATEMENT, concept="revenue", period=period,
            value="400", provenance=provenance,
            dimensions=(("StatementBusinessSegmentsAxis", "SegmentA"),),
        )

        history = select_point_in_time(
            [consolidated, segment], dt.datetime(2024, 3, 1, tzinfo=US_EASTERN), cik=CIK
        )

        resolved = _find_period(history.income_statement_periods, period.period_end)
        assert len(resolved.facts) == 2
        assert resolved.fact_for(consolidated.identity).value == Decimal("1000")
        assert resolved.fact_for(segment.identity).value == Decimal("400")


class TestCoverPageExclusion:
    def test_cover_fact_never_appears_in_any_statement_period_collection(self):
        provenance = make_provenance(
            accession_number="0000000001-24-000005",
            filed_date=dt.date(2024, 4, 18),
            accepted_at=dt.datetime(2024, 4, 18, 10, 0, tzinfo=US_EASTERN),
        )
        cover_period = make_period(
            fiscal_year=2024, fiscal_period="COVER", period_end=dt.date(2024, 4, 18), periodicity=None
        )
        cover_fact = make_fact(
            statement_kind=StatementKind.COVER, concept="shares_outstanding_cover", period=cover_period,
            value="15000", provenance=provenance, unit="shares", currency=None,
        )

        history = select_point_in_time([cover_fact], dt.datetime(2024, 5, 1, tzinfo=US_EASTERN), cik=CIK)

        assert history.income_statement_periods == ()
        assert history.balance_sheet_periods == ()
        assert history.cash_flow_periods == ()

    def test_eligible_cover_fact_is_retained_in_its_own_collection(self):
        provenance = make_provenance(
            accession_number="0000000001-24-000005",
            filed_date=dt.date(2024, 4, 18),
            accepted_at=dt.datetime(2024, 4, 18, 10, 0, tzinfo=US_EASTERN),
        )
        cover_period = make_period(
            fiscal_year=2024, fiscal_period="COVER", period_end=dt.date(2024, 4, 18), periodicity=None
        )
        cover_fact = make_fact(
            statement_kind=StatementKind.COVER, concept="shares_outstanding_cover", period=cover_period,
            value="15000", provenance=provenance, unit="shares", currency=None,
        )

        history = select_point_in_time([cover_fact], dt.datetime(2024, 5, 1, tzinfo=US_EASTERN), cik=CIK)

        assert history.cover_facts == (cover_fact,)

    def test_ineligible_cover_fact_is_absent_from_cover_facts(self):
        provenance = make_provenance(
            accession_number="0000000001-24-000005",
            filed_date=dt.date(2024, 4, 18),
            accepted_at=dt.datetime(2024, 4, 18, 10, 0, tzinfo=US_EASTERN),
        )
        cover_period = make_period(
            fiscal_year=2024, fiscal_period="COVER", period_end=dt.date(2024, 4, 18), periodicity=None
        )
        cover_fact = make_fact(
            statement_kind=StatementKind.COVER, concept="shares_outstanding_cover", period=cover_period,
            value="15000", provenance=provenance, unit="shares", currency=None,
        )

        history = select_point_in_time([cover_fact], dt.datetime(2024, 4, 1, tzinfo=US_EASTERN), cik=CIK)

        assert history.cover_facts == ()


class TestDeterministicOrderingAndTieBreak:
    def test_periods_are_ordered_most_recent_first(self):
        provenance = make_provenance(
            accession_number="0000000001-24-000006",
            filed_date=dt.date(2024, 2, 1),
            accepted_at=dt.datetime(2024, 2, 1, 10, 0, tzinfo=US_EASTERN),
        )
        period_2021 = _annual_period(2021, dt.date(2021, 12, 31))
        period_2022 = _annual_period(2022, dt.date(2022, 12, 31))
        period_2023 = _annual_period(2023, dt.date(2023, 12, 31))
        facts = [
            make_fact(statement_kind=StatementKind.INCOME_STATEMENT, concept="revenue",
                      period=period_2022, value="200", provenance=provenance),
            make_fact(statement_kind=StatementKind.INCOME_STATEMENT, concept="revenue",
                      period=period_2021, value="150", provenance=provenance),
            make_fact(statement_kind=StatementKind.INCOME_STATEMENT, concept="revenue",
                      period=period_2023, value="250", provenance=provenance),
        ]

        history = select_point_in_time(facts, dt.datetime(2024, 3, 1, tzinfo=US_EASTERN), cik=CIK)

        ends = [p.period.period_end for p in history.income_statement_periods]
        assert ends == sorted(ends, reverse=True)

    def test_tie_break_on_identical_eligible_at_is_deterministic_and_order_independent(self):
        same_instant = dt.datetime(2024, 2, 1, 10, 0, tzinfo=US_EASTERN)
        lower_accession = make_provenance(
            accession_number="0000000001-24-000001", filed_date=dt.date(2024, 2, 1), accepted_at=same_instant
        )
        higher_accession = make_provenance(
            accession_number="0000000001-24-000002", filed_date=dt.date(2024, 2, 1), accepted_at=same_instant
        )
        period = _annual_period(2023, dt.date(2023, 12, 31))
        fact_a = make_fact(statement_kind=StatementKind.INCOME_STATEMENT, concept="revenue",
                            period=period, value="100", provenance=lower_accession)
        fact_b = make_fact(statement_kind=StatementKind.INCOME_STATEMENT, concept="revenue",
                            period=period, value="200", provenance=higher_accession)

        cutoff = dt.datetime(2024, 3, 1, tzinfo=US_EASTERN)
        result_order_1 = select_point_in_time([fact_a, fact_b], cutoff, cik=CIK)
        result_order_2 = select_point_in_time([fact_b, fact_a], cutoff, cik=CIK)

        resolved_1 = _find_period(result_order_1.income_statement_periods, period.period_end)
        resolved_2 = _find_period(result_order_2.income_statement_periods, period.period_end)
        # The higher accession number wins regardless of input order.
        assert resolved_1.fact_for(fact_a.identity).value == Decimal("200")
        assert resolved_2.fact_for(fact_a.identity).value == Decimal("200")


class TestInputOrderIndependence:
    def test_complete_multi_statement_history_is_identical_regardless_of_input_order(self):
        original_bs_provenance = make_provenance(
            accession_number="0000000001-23-000010",
            filed_date=dt.date(2023, 2, 1),
            accepted_at=dt.datetime(2023, 2, 1, 10, 0, tzinfo=US_EASTERN),
        )
        amendment_provenance = make_provenance(
            accession_number="0000000001-23-000099",
            filed_date=dt.date(2023, 6, 1),
            accepted_at=dt.datetime(2023, 6, 1, 10, 0, tzinfo=US_EASTERN),
            form_type="10-K/A",
            is_amendment=True,
        )
        bs_period_2022 = _instant_period(2022, dt.date(2022, 12, 31))
        is_period_2022 = _annual_period(2022, dt.date(2022, 12, 31))
        is_period_2023 = _annual_period(2023, dt.date(2023, 12, 31))
        cover_period = make_period(
            fiscal_year=2023, fiscal_period="COVER", period_end=dt.date(2023, 2, 1), periodicity=None
        )

        facts = [
            make_fact(statement_kind=StatementKind.BALANCE_SHEET, concept="total_assets",
                      period=bs_period_2022, value="39572", provenance=original_bs_provenance),
            make_fact(statement_kind=StatementKind.BALANCE_SHEET, concept="total_assets",
                      period=bs_period_2022, value="36171", provenance=amendment_provenance),
            make_fact(statement_kind=StatementKind.INCOME_STATEMENT, concept="revenue",
                      period=is_period_2022, value="900", provenance=original_bs_provenance),
            make_fact(statement_kind=StatementKind.INCOME_STATEMENT, concept="revenue",
                      period=is_period_2023, value="1000", provenance=original_bs_provenance),
            make_fact(statement_kind=StatementKind.CASH_FLOW, concept="operating_cash_flow",
                      period=is_period_2023, value="150", provenance=original_bs_provenance),
            make_fact(statement_kind=StatementKind.COVER, concept="shares_outstanding_cover",
                      period=cover_period, value="5000", provenance=original_bs_provenance,
                      unit="shares", currency=None),
        ]

        cutoff = dt.datetime(2023, 7, 1, tzinfo=US_EASTERN)
        forward = select_point_in_time(facts, cutoff, cik=CIK)
        reversed_order = select_point_in_time(list(reversed(facts)), cutoff, cik=CIK)

        assert forward == reversed_order


class TestSynonymConflict:
    def test_disagreeing_tags_in_the_same_filing_are_flagged_not_silently_resolved(self):
        provenance = make_provenance(
            accession_number="0000000001-24-000007",
            filed_date=dt.date(2024, 2, 1),
            accepted_at=dt.datetime(2024, 2, 1, 10, 0, tzinfo=US_EASTERN),
        )
        period = _instant_period(2023, dt.date(2023, 12, 31))
        tag_a = make_fact(
            statement_kind=StatementKind.BALANCE_SHEET, concept="cash_and_equivalents", period=period,
            value="500", provenance=provenance, raw_tag="CashAndCashEquivalentsAtCarryingValue",
        )
        tag_b = make_fact(
            statement_kind=StatementKind.BALANCE_SHEET, concept="cash_and_equivalents", period=period,
            value="472", provenance=provenance,
            raw_tag="CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
        )

        history = select_point_in_time([tag_a, tag_b], dt.datetime(2024, 3, 1, tzinfo=US_EASTERN), cik=CIK)

        assert history.balance_sheet_periods == ()  # no winner — the conflict blocks resolution
        assert len(history.conflicts) == 1
        conflict = history.conflicts[0]
        assert conflict.accession_number == "0000000001-24-000007"
        assert set(conflict.raw_tags) == {
            "CashAndCashEquivalentsAtCarryingValue",
            "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
        }
        assert set(conflict.values) == {Decimal("500"), Decimal("472")}

    def test_agreeing_synonyms_in_the_same_filing_are_not_a_conflict(self):
        provenance = make_provenance(
            accession_number="0000000001-24-000008",
            filed_date=dt.date(2024, 2, 1),
            accepted_at=dt.datetime(2024, 2, 1, 10, 0, tzinfo=US_EASTERN),
        )
        period = _annual_period(2023, dt.date(2023, 12, 31))
        tag_a = make_fact(
            statement_kind=StatementKind.INCOME_STATEMENT, concept="revenue", period=period,
            value="1000", provenance=provenance, raw_tag="SalesRevenueNet",
        )
        tag_b = make_fact(
            statement_kind=StatementKind.INCOME_STATEMENT, concept="revenue", period=period,
            value="1000", provenance=provenance, raw_tag="Revenues",
        )

        history = select_point_in_time([tag_a, tag_b], dt.datetime(2024, 3, 1, tzinfo=US_EASTERN), cik=CIK)

        assert history.conflicts == ()
        resolved = _find_period(history.income_statement_periods, period.period_end)
        assert len(resolved.facts) == 1
        assert resolved.facts[0].value == Decimal("1000")

    def test_newest_conflicted_accession_yields_no_winner_even_with_an_older_clean_one(self):
        """The newest eligible accession for an identity is the ONLY one
        that gets to win — if it's conflicted, an older clean accession
        must never be used as a fallback."""
        period = _instant_period(2023, dt.date(2023, 12, 31))
        older_clean = make_provenance(
            accession_number="0000000001-24-000001",
            filed_date=dt.date(2024, 1, 15),
            accepted_at=dt.datetime(2024, 1, 15, 10, 0, tzinfo=US_EASTERN),
        )
        newer_conflicted = make_provenance(
            accession_number="0000000001-24-000009",
            filed_date=dt.date(2024, 2, 1),
            accepted_at=dt.datetime(2024, 2, 1, 10, 0, tzinfo=US_EASTERN),
        )
        clean_fact = make_fact(
            statement_kind=StatementKind.BALANCE_SHEET, concept="cash_and_equivalents", period=period,
            value="500", provenance=older_clean,
        )
        conflicted_a = make_fact(
            statement_kind=StatementKind.BALANCE_SHEET, concept="cash_and_equivalents", period=period,
            value="600", provenance=newer_conflicted, raw_tag="TagA",
        )
        conflicted_b = make_fact(
            statement_kind=StatementKind.BALANCE_SHEET, concept="cash_and_equivalents", period=period,
            value="601", provenance=newer_conflicted, raw_tag="TagB",
        )

        history = select_point_in_time(
            [clean_fact, conflicted_a, conflicted_b], dt.datetime(2024, 3, 1, tzinfo=US_EASTERN), cik=CIK
        )

        assert history.balance_sheet_periods == ()  # no winner at all — never falls back to the older clean fact
        assert len(history.conflicts) == 1
        assert history.conflicts[0].accession_number == "0000000001-24-000009"

    def test_older_conflicted_accession_does_not_block_a_newer_clean_one(self):
        """The mirror case: a conflict in an OLDER accession must never
        prevent a newer, clean accession from winning normally."""
        period = _instant_period(2023, dt.date(2023, 12, 31))
        older_conflicted = make_provenance(
            accession_number="0000000001-24-000001",
            filed_date=dt.date(2024, 1, 15),
            accepted_at=dt.datetime(2024, 1, 15, 10, 0, tzinfo=US_EASTERN),
        )
        newer_clean = make_provenance(
            accession_number="0000000001-24-000009",
            filed_date=dt.date(2024, 2, 1),
            accepted_at=dt.datetime(2024, 2, 1, 10, 0, tzinfo=US_EASTERN),
        )
        conflicted_a = make_fact(
            statement_kind=StatementKind.BALANCE_SHEET, concept="cash_and_equivalents", period=period,
            value="600", provenance=older_conflicted, raw_tag="TagA",
        )
        conflicted_b = make_fact(
            statement_kind=StatementKind.BALANCE_SHEET, concept="cash_and_equivalents", period=period,
            value="601", provenance=older_conflicted, raw_tag="TagB",
        )
        clean_fact = make_fact(
            statement_kind=StatementKind.BALANCE_SHEET, concept="cash_and_equivalents", period=period,
            value="500", provenance=newer_clean,
        )

        history = select_point_in_time(
            [conflicted_a, conflicted_b, clean_fact], dt.datetime(2024, 3, 1, tzinfo=US_EASTERN), cik=CIK
        )

        resolved = _find_period(history.balance_sheet_periods, period.period_end)
        assert resolved.fact_for(clean_fact.identity).value == Decimal("500")
        # The older conflict is still reported, it just doesn't block the winner.
        assert len(history.conflicts) == 1
        assert history.conflicts[0].accession_number == "0000000001-24-000001"


class TestNaiveDatetimeRejection:
    def test_naive_knowledge_cutoff_is_rejected(self):
        naive_cutoff = dt.datetime(2024, 3, 1)  # no tzinfo
        with pytest.raises(ValueError):
            select_point_in_time([], naive_cutoff, cik=CIK)


class TestMultiPeriodHistoryRetained:
    def test_all_available_annual_periods_are_retained_not_just_the_latest(self):
        provenance = make_provenance(
            accession_number="0000000001-24-000010",
            filed_date=dt.date(2024, 2, 1),
            accepted_at=dt.datetime(2024, 2, 1, 10, 0, tzinfo=US_EASTERN),
        )
        periods = [_annual_period(year, dt.date(year, 12, 31)) for year in (2020, 2021, 2022, 2023)]
        facts = [
            make_fact(statement_kind=StatementKind.INCOME_STATEMENT, concept="revenue",
                      period=p, value=str(100 + i * 10), provenance=provenance)
            for i, p in enumerate(periods)
        ]

        history = select_point_in_time(facts, dt.datetime(2024, 3, 1, tzinfo=US_EASTERN), cik=CIK)

        assert len(history.income_statement_periods) == 4
        assert {p.period.fiscal_year for p in history.income_statement_periods} == {2020, 2021, 2022, 2023}


class TestIssuerScoping:
    def test_a_fact_belonging_to_another_cik_is_rejected(self):
        provenance = make_provenance(
            accession_number="0000000002-24-000001",
            filed_date=dt.date(2024, 2, 1),
            accepted_at=dt.datetime(2024, 2, 1, 10, 0, tzinfo=US_EASTERN),
        )
        period = _annual_period(2023, dt.date(2023, 12, 31))
        wrong_issuer_fact = make_fact(
            statement_kind=StatementKind.INCOME_STATEMENT, concept="revenue", period=period,
            value="1000", provenance=provenance, entity_cik=OTHER_CIK,
        )

        with pytest.raises(ValueError, match="scoped to CIK"):
            select_point_in_time([wrong_issuer_fact], dt.datetime(2024, 3, 1, tzinfo=US_EASTERN), cik=CIK)

    def test_history_carries_the_normalized_cik(self):
        provenance = make_provenance(
            accession_number="0000000001-24-000001",
            filed_date=dt.date(2024, 2, 1),
            accepted_at=dt.datetime(2024, 2, 1, 10, 0, tzinfo=US_EASTERN),
        )
        period = _annual_period(2023, dt.date(2023, 12, 31))
        fact = make_fact(
            statement_kind=StatementKind.INCOME_STATEMENT, concept="revenue", period=period,
            value="1000", provenance=provenance,
        )

        history = select_point_in_time([fact], dt.datetime(2024, 3, 1, tzinfo=US_EASTERN), cik="1111111")

        assert history.cik == "0001111111"  # normalized to 10 digits


class TestFactContextDimensionIntegrity:
    def test_dimensions_are_canonically_sorted_regardless_of_input_order(self):
        one_order = FactContext(entity_cik=CIK, dimensions=(("AxisB", "M2"), ("AxisA", "M1")))
        other_order = FactContext(entity_cik=CIK, dimensions=(("AxisA", "M1"), ("AxisB", "M2")))
        assert one_order == other_order
        assert one_order.dimensions == (("AxisA", "M1"), ("AxisB", "M2"))

    def test_duplicate_axis_is_rejected(self):
        with pytest.raises(ValueError):
            FactContext(entity_cik=CIK, dimensions=(("AxisA", "M1"), ("AxisA", "M2")))


class TestStatementConsistency:
    def test_same_identity_with_different_statement_kind_is_rejected(self):
        provenance = make_provenance(
            accession_number="0000000001-24-000011",
            filed_date=dt.date(2024, 2, 1),
            accepted_at=dt.datetime(2024, 2, 1, 10, 0, tzinfo=US_EASTERN),
        )
        period = _annual_period(2023, dt.date(2023, 12, 31))
        as_income = make_fact(
            statement_kind=StatementKind.INCOME_STATEMENT, concept="revenue", period=period,
            value="1000", provenance=provenance,
        )
        # Same identity in every respect except the statement it claims to
        # belong to — a structural defect, not a legitimate restatement.
        as_cash_flow = make_fact(
            statement_kind=StatementKind.CASH_FLOW, concept="revenue", period=period,
            value="1000", provenance=provenance,
        )

        with pytest.raises(ValueError, match="reported inconsistently"):
            select_point_in_time(
                [as_income, as_cash_flow], dt.datetime(2024, 3, 1, tzinfo=US_EASTERN), cik=CIK
            )

    def test_same_identity_with_incompatible_period_metadata_is_rejected(self):
        provenance = make_provenance(
            accession_number="0000000001-24-000012",
            filed_date=dt.date(2024, 2, 1),
            accepted_at=dt.datetime(2024, 2, 1, 10, 0, tzinfo=US_EASTERN),
        )
        period_a = _annual_period(2023, dt.date(2023, 12, 31))
        # Same period_start/end (so the same FactIdentity) but a different
        # fiscal_year label — inconsistent metadata for one identity.
        period_b = make_period(
            fiscal_year=2024, fiscal_period="FY",
            period_start=period_a.period_start, period_end=period_a.period_end, periodicity="annual",
        )
        fact_a = make_fact(
            statement_kind=StatementKind.INCOME_STATEMENT, concept="revenue", period=period_a,
            value="1000", provenance=provenance,
        )
        fact_b = make_fact(
            statement_kind=StatementKind.INCOME_STATEMENT, concept="revenue", period=period_b,
            value="1000", provenance=provenance,
        )

        with pytest.raises(ValueError, match="reported inconsistently"):
            select_point_in_time([fact_a, fact_b], dt.datetime(2024, 3, 1, tzinfo=US_EASTERN), cik=CIK)


class TestFutureDataDoesNotInfluenceEarlierHistory:
    def test_a_future_inconsistent_fact_cannot_change_or_fail_an_earlier_history(self):
        """An ineligible fact must be filtered out BEFORE consistency
        validation ever sees it — so a structural defect that only shows
        up in not-yet-eligible data can neither raise nor change the
        result for an earlier knowledge cutoff."""
        period = _annual_period(2023, dt.date(2023, 12, 31))
        past_provenance = make_provenance(
            accession_number="0000000001-24-000001",
            filed_date=dt.date(2024, 2, 1),
            accepted_at=dt.datetime(2024, 2, 1, 10, 0, tzinfo=US_EASTERN),
        )
        future_provenance = make_provenance(
            accession_number="0000000001-24-000002",
            filed_date=dt.date(2024, 5, 1),
            accepted_at=dt.datetime(2024, 5, 1, 10, 0, tzinfo=US_EASTERN),
        )
        past_fact = make_fact(
            statement_kind=StatementKind.INCOME_STATEMENT, concept="revenue", period=period,
            value="1000", provenance=past_provenance,
        )
        # Same identity, but a LATER, not-yet-eligible fact that reports it
        # under a DIFFERENT (inconsistent) statement kind — a structural
        # defect if it were eligible, but it must never even be examined.
        future_inconsistent_fact = make_fact(
            statement_kind=StatementKind.CASH_FLOW, concept="revenue", period=period,
            value="1000", provenance=future_provenance,
        )

        cutoff = dt.datetime(2024, 3, 1, tzinfo=US_EASTERN)  # before the future fact is eligible

        history_with_future_fact = select_point_in_time([past_fact, future_inconsistent_fact], cutoff, cik=CIK)
        history_without_future_fact = select_point_in_time([past_fact], cutoff, cik=CIK)

        assert history_with_future_fact == history_without_future_fact


class TestPeriodGeometryCoherence:
    def test_different_concepts_sharing_period_geometry_must_agree_on_fiscal_metadata(self):
        provenance = make_provenance(
            accession_number="0000000001-24-000014",
            filed_date=dt.date(2024, 2, 1),
            accepted_at=dt.datetime(2024, 2, 1, 10, 0, tzinfo=US_EASTERN),
        )
        period_start = dt.date(2023, 1, 1)
        period_end = dt.date(2023, 12, 31)
        revenue_period = make_period(
            fiscal_year=2023, fiscal_period="FY", period_start=period_start, period_end=period_end,
            periodicity="annual",
        )
        # Same geometry (period_start/end/periodicity) but a DIFFERENT
        # fiscal_year — a different concept, so _validate_identity_consistency
        # (which only checks WITHIN one identity) would never catch this.
        mismatched_period = make_period(
            fiscal_year=2024, fiscal_period="FY", period_start=period_start, period_end=period_end,
            periodicity="annual",
        )
        revenue_fact = make_fact(
            statement_kind=StatementKind.INCOME_STATEMENT, concept="revenue", period=revenue_period,
            value="1000", provenance=provenance,
        )
        pretax_income_fact = make_fact(
            statement_kind=StatementKind.INCOME_STATEMENT, concept="pretax_income", period=mismatched_period,
            value="200", provenance=provenance,
        )

        with pytest.raises(ValueError, match="inconsistent fiscal_year/fiscal_period"):
            select_point_in_time(
                [revenue_fact, pretax_income_fact], dt.datetime(2024, 3, 1, tzinfo=US_EASTERN), cik=CIK
            )


class TestSameAccessionProvenanceConsistency:
    def test_inconsistent_same_accession_provenance_is_rejected_regardless_of_input_order(self):
        period = _annual_period(2023, dt.date(2023, 12, 31))
        provenance_a = make_provenance(
            accession_number="0000000001-24-000015",
            filed_date=dt.date(2024, 2, 1),
            accepted_at=dt.datetime(2024, 2, 1, 10, 0, tzinfo=US_EASTERN),
        )
        # Same accession_number, but a data bug gives it a DIFFERENT
        # accepted_at — the two facts disagree on when THEIR OWN shared
        # filing became eligible, which must never be silently resolved by
        # picking whichever one happens to appear first in the input.
        provenance_b = make_provenance(
            accession_number="0000000001-24-000015",
            filed_date=dt.date(2024, 2, 1),
            accepted_at=dt.datetime(2024, 2, 1, 14, 0, tzinfo=US_EASTERN),
        )
        fact_a = make_fact(
            statement_kind=StatementKind.INCOME_STATEMENT, concept="revenue", period=period,
            value="1000", provenance=provenance_a, raw_tag="Revenues",
        )
        fact_b = make_fact(
            statement_kind=StatementKind.INCOME_STATEMENT, concept="revenue", period=period,
            value="1000", provenance=provenance_b, raw_tag="Revenues",
        )
        cutoff = dt.datetime(2024, 3, 1, tzinfo=US_EASTERN)

        with pytest.raises(ValueError, match="inconsistent provenance"):
            select_point_in_time([fact_a, fact_b], cutoff, cik=CIK)
        with pytest.raises(ValueError, match="inconsistent provenance"):
            select_point_in_time([fact_b, fact_a], cutoff, cik=CIK)


class TestFinancialFactConstructionValidation:
    """FinancialFact.__post_init__ invariants — type-level, not
    selection-level, so exercised by constructing directly via make_fact."""

    def _provenance(self):
        return make_provenance(
            accession_number="0000000001-24-000013",
            filed_date=dt.date(2024, 2, 1),
            accepted_at=dt.datetime(2024, 2, 1, 10, 0, tzinfo=US_EASTERN),
        )

    def test_non_finite_value_is_rejected(self):
        period = _annual_period(2023, dt.date(2023, 12, 31))
        with pytest.raises(ValueError):
            make_fact(
                statement_kind=StatementKind.INCOME_STATEMENT, concept="revenue", period=period,
                value=Decimal("NaN"), provenance=self._provenance(),
            )

    def test_cover_fact_with_a_periodicity_is_rejected(self):
        cover_period = make_period(
            fiscal_year=2024, fiscal_period="COVER", period_end=dt.date(2024, 4, 18), periodicity="annual"
        )
        with pytest.raises(ValueError):
            make_fact(
                statement_kind=StatementKind.COVER, concept="shares_outstanding_cover", period=cover_period,
                value="15000", provenance=self._provenance(), unit="shares", currency=None,
            )

    def test_cover_fact_with_the_wrong_fiscal_period_label_is_rejected(self):
        cover_period = make_period(
            fiscal_year=2024, fiscal_period="FY", period_end=dt.date(2024, 4, 18), periodicity=None
        )
        with pytest.raises(ValueError, match="fiscal_period must be 'COVER'"):
            make_fact(
                statement_kind=StatementKind.COVER, concept="shares_outstanding_cover", period=cover_period,
                value="15000", provenance=self._provenance(), unit="shares", currency=None,
            )

    def test_cover_fact_with_a_period_start_is_rejected(self):
        cover_period = make_period(
            fiscal_year=2024, fiscal_period="COVER", period_start=dt.date(2024, 1, 1),
            period_end=dt.date(2024, 4, 18), periodicity=None,
        )
        with pytest.raises(ValueError, match="period_start must be None"):
            make_fact(
                statement_kind=StatementKind.COVER, concept="shares_outstanding_cover", period=cover_period,
                value="15000", provenance=self._provenance(), unit="shares", currency=None,
            )

    def test_non_cover_fact_with_fiscal_period_cover_is_rejected(self):
        period = make_period(
            fiscal_year=2023, fiscal_period="COVER",
            period_start=dt.date(2023, 1, 1), period_end=dt.date(2023, 12, 31), periodicity="annual",
        )
        with pytest.raises(ValueError, match="must not have fiscal_period='COVER'"):
            make_fact(
                statement_kind=StatementKind.INCOME_STATEMENT, concept="revenue", period=period,
                value="1000", provenance=self._provenance(),
            )

    def test_non_cover_fact_without_a_periodicity_is_rejected(self):
        period = make_period(
            fiscal_year=2023, fiscal_period="FY",
            period_start=dt.date(2023, 1, 1), period_end=dt.date(2023, 12, 31), periodicity=None,
        )
        with pytest.raises(ValueError):
            make_fact(
                statement_kind=StatementKind.INCOME_STATEMENT, concept="revenue", period=period,
                value="1000", provenance=self._provenance(),
            )

    def test_balance_sheet_fact_with_a_period_start_is_rejected(self):
        period = make_period(
            fiscal_year=2023, fiscal_period="FY",
            period_start=dt.date(2023, 1, 1), period_end=dt.date(2023, 12, 31), periodicity="annual",
        )
        with pytest.raises(ValueError):
            make_fact(
                statement_kind=StatementKind.BALANCE_SHEET, concept="total_assets", period=period,
                value="1000", provenance=self._provenance(),
            )

    def test_income_statement_fact_without_a_period_start_is_rejected(self):
        period = _instant_period(2023, dt.date(2023, 12, 31))
        with pytest.raises(ValueError):
            make_fact(
                statement_kind=StatementKind.INCOME_STATEMENT, concept="revenue", period=period,
                value="1000", provenance=self._provenance(),
            )

    def test_non_decimal_value_is_rejected_with_a_clear_domain_error(self):
        # Bypasses make_fact's Decimal(value) coercion on purpose, to
        # exercise FinancialFact's own constructor-level guard.
        period = _annual_period(2023, dt.date(2023, 12, 31))
        identity = FactIdentity(
            concept="revenue", period_start=period.period_start, period_end=period.period_end,
            unit="USD", currency="USD", context=FactContext(entity_cik=CIK),
        )
        with pytest.raises(ValueError, match="must be a Decimal"):
            FinancialFact(
                statement_kind=StatementKind.INCOME_STATEMENT, period=period, identity=identity,
                value=1000.0,  # a float, not a Decimal
                raw_tag="Revenues", taxonomy="us-gaap", provenance=self._provenance(),
            )
