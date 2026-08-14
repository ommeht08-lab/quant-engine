"""
Group D: conviction scoring — negative*negative can't score positive,
eligibility gate, P/IV-floor-bounded blow-up protection, and the
documented 60/40 blend weighting actually behaving as documented.
"""

import pandas as pd
import pytest

from src.backtesting.historical_tester import CONVICTION_RAW_CAP, ValuationResult, score_ticker
from src.trading.alpaca_execution import (
    DCF_CONVICTION_BLEND_WEIGHT,
    FCF_YIELD_BLEND_WEIGHT,
    FCF_YIELD_NORMALIZED_CAP,
    _blend_conviction_with_fcf_yield,
)


def _make_valuation(fcf_growth_positive: bool, roic_positive: bool, price_to_intrinsic: float = 1.0) -> ValuationResult:
    prior_date = pd.Timestamp("2022-12-31")
    latest_date = pd.Timestamp("2023-12-31")

    latest_fcf = 150.0 if fcf_growth_positive else 50.0
    prior_fcf = 100.0
    cash_flow = pd.DataFrame({
        prior_date: {"Free Cash Flow": prior_fcf},
        latest_date: {"Free Cash Flow": latest_fcf},
    })

    ebit = 100.0 if roic_positive else -50.0
    income_stmt = pd.DataFrame({latest_date: {"EBIT": ebit}})
    balance_sheet = pd.DataFrame({latest_date: {"Stockholders Equity": 500.0}})

    return ValuationResult(
        ticker="TEST",
        as_of_date="2024-01-01",
        sector="Technology",
        historical_price=100.0,
        historical_intrinsic_value=100.0 / price_to_intrinsic,
        price_to_intrinsic=price_to_intrinsic,
        wacc=0.08,
        beta=1.0,
        income_stmt=income_stmt,
        balance_sheet=balance_sheet,
        cash_flow=cash_flow,
        tax_rate=0.21,
        total_debt=0.0,
        cash_and_equivalents=0.0,
    )


GENEROUS_SECTOR_MEDIANS = {"Technology": 10.0}


class TestNegativeTimesNegativeCannotScorePositive:
    def test_both_negative_is_ineligible_not_a_positive_score(self):
        valuation = _make_valuation(fcf_growth_positive=False, roic_positive=False)
        analysis = score_ticker(valuation, GENEROUS_SECTOR_MEDIANS)

        assert analysis.is_valid is False
        assert analysis.conviction_score is None
        assert "positive FCF growth" in analysis.skip_reason
        assert "positive ROIC" in analysis.skip_reason

    def test_negative_fcf_positive_roic_is_ineligible(self):
        valuation = _make_valuation(fcf_growth_positive=False, roic_positive=True)
        analysis = score_ticker(valuation, GENEROUS_SECTOR_MEDIANS)
        assert analysis.is_valid is False

    def test_positive_fcf_negative_roic_is_ineligible(self):
        valuation = _make_valuation(fcf_growth_positive=True, roic_positive=False)
        analysis = score_ticker(valuation, GENEROUS_SECTOR_MEDIANS)
        assert analysis.is_valid is False

    def test_both_positive_is_eligible_and_scores_positive(self):
        valuation = _make_valuation(fcf_growth_positive=True, roic_positive=True)
        analysis = score_ticker(valuation, GENEROUS_SECTOR_MEDIANS)

        assert analysis.is_valid is True
        assert analysis.conviction_score is not None
        assert analysis.conviction_score > 0


class TestPriceToIntrinsicFloorBoundsBlowUp:
    def test_extreme_near_zero_piv_does_not_blow_up(self):
        valuation = _make_valuation(fcf_growth_positive=True, roic_positive=True, price_to_intrinsic=0.0001)
        analysis = score_ticker(valuation, GENEROUS_SECTOR_MEDIANS)

        assert analysis.is_valid is True
        assert 0 < analysis.conviction_score < CONVICTION_RAW_CAP

    def test_normal_piv_also_stays_within_documented_range(self):
        valuation = _make_valuation(fcf_growth_positive=True, roic_positive=True, price_to_intrinsic=0.8)
        analysis = score_ticker(valuation, GENEROUS_SECTOR_MEDIANS)
        assert 0 < analysis.conviction_score < CONVICTION_RAW_CAP


class TestBlendWeightingBehavesAsDocumented:
    def _analysis(self, conviction_score, fcf_yield):
        from src.backtesting.historical_tester import TickerAnalysis

        return TickerAnalysis(
            ticker="T", as_of_date="2024-01-01", sector="Technology",
            conviction_score=conviction_score, fcf_yield=fcf_yield,
        )

    def test_varying_fcf_yield_moves_score_by_its_documented_weight(self):
        low = _blend_conviction_with_fcf_yield(self._analysis(conviction_score=1.0, fcf_yield=0.0))
        high = _blend_conviction_with_fcf_yield(self._analysis(conviction_score=1.0, fcf_yield=0.20))
        # normalized_fcf goes from 0.0 -> FCF_YIELD_NORMALIZED_CAP (2.0) as
        # fcf_yield crosses the 10%-per-1.0-multiplier normalization.
        delta = high.conviction_score - low.conviction_score
        assert delta == pytest.approx(FCF_YIELD_BLEND_WEIGHT * FCF_YIELD_NORMALIZED_CAP, abs=1e-9)

    def test_varying_dcf_conviction_moves_score_by_its_documented_weight(self):
        low = _blend_conviction_with_fcf_yield(self._analysis(conviction_score=0.0, fcf_yield=0.0))
        high = _blend_conviction_with_fcf_yield(self._analysis(conviction_score=2.0, fcf_yield=0.0))
        delta = high.conviction_score - low.conviction_score
        assert delta == pytest.approx(DCF_CONVICTION_BLEND_WEIGHT * 2.0, abs=1e-9)

    def test_weights_sum_to_one(self):
        assert DCF_CONVICTION_BLEND_WEIGHT + FCF_YIELD_BLEND_WEIGHT == pytest.approx(1.0)
