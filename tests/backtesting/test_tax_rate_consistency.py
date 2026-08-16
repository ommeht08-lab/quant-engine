"""
Group K: backtester tax-rate consistency.

Regression for Finding 3: `compute_valuation` used to derive the tax rate
stored on its `ValuationResult` (later consumed by `calculate_roic` /
`score_ticker` for the Conviction Score) independently of the tax rate
`run_dcf_valuation` actually used internally for the DCF's own FCF/WACC
math -- an explicit `DCFAssumptions.tax_rate` override was silently
ignored by the former while still honored by the latter, letting one
ticker use two different tax rates across its own DCF and scoring
calculations in the same run. No network; every yfinance-backed lookup
is monkeypatched onto synthetic, deterministic data.
"""

import types

import pandas as pd
import pytest

from src.backtesting import historical_tester as engine
from src.dcf_model.dcf import DEFAULT_TAX_RATE, DCFAssumptions


def _patch_point_in_time_lookups(
    monkeypatch,
    *,
    income_stmt: pd.DataFrame,
    balance_sheet: pd.DataFrame,
    cash_flow=None,
    price: float = 50.0,
    shares: float = 100.0,
    beta: float = 1.0,
    sector: str = "Technology",
) -> None:
    """Monkeypatch every yfinance-backed lookup `compute_valuation` makes onto synthetic data."""
    monkeypatch.setattr(engine, "get_ticker_object", lambda ticker: types.SimpleNamespace(ticker=ticker))
    monkeypatch.setattr(engine, "get_sector", lambda ticker_obj: sector)
    monkeypatch.setattr(engine, "get_income_statement", lambda ticker_obj: income_stmt)
    monkeypatch.setattr(engine, "get_balance_sheet", lambda ticker_obj: balance_sheet)
    monkeypatch.setattr(engine, "get_cash_flow_statement", lambda ticker_obj: cash_flow)
    monkeypatch.setattr(engine, "get_historical_price", lambda ticker_obj, as_of_ts: price)
    monkeypatch.setattr(
        engine, "get_historical_shares_outstanding", lambda ticker_obj, as_of_ts: (shares, False)
    )
    monkeypatch.setattr(engine, "get_beta", lambda ticker_obj: beta)


def _income_statement(pretax_income=None, tax_provision=None, ebit=200.0) -> pd.DataFrame:
    row = {"Total Revenue": 1000.0, "EBIT": ebit}
    if pretax_income is not None:
        row["Pretax Income"] = pretax_income
    if tax_provision is not None:
        row["Tax Provision"] = tax_provision
    return pd.DataFrame({pd.Timestamp("2023-12-31"): row})


def _balance_sheet() -> pd.DataFrame:
    return pd.DataFrame({pd.Timestamp("2023-12-31"): {"Total Debt": 0.0, "Cash And Cash Equivalents": 0.0}})


AS_OF_DATE = "2024-06-01"  # ~5 months after the 2023-12-31 statement period end -- clears the 90-day filing lag


class TestExplicitOverrideConsistentAcrossDCFAndScoringInputs:
    def test_explicit_tax_rate_override_matches_what_the_dcf_actually_used(self, monkeypatch):
        """
        Statement-derived rate would be 30/100 = 30%; an explicit 10%
        override must win in BOTH the DCF's own FCF/NOPAT math and the
        `ValuationResult.tax_rate` field consumed downstream for ROIC.
        """
        income_stmt = _income_statement(pretax_income=100.0, tax_provision=30.0)
        balance_sheet = _balance_sheet()
        _patch_point_in_time_lookups(monkeypatch, income_stmt=income_stmt, balance_sheet=balance_sheet)

        captured = {}
        real_run_dcf_valuation = engine.run_dcf_valuation

        def _capturing_run_dcf_valuation(financial_data, assumptions):
            result = real_run_dcf_valuation(financial_data, assumptions)
            captured["dcf_result"] = result
            return result

        monkeypatch.setattr(engine, "run_dcf_valuation", _capturing_run_dcf_valuation)

        assumptions = DCFAssumptions(tax_rate=0.10, revenue_growth_rate=0.05, operating_margin=0.15)
        result = engine.compute_valuation("AAPL", AS_OF_DATE, assumptions, risk_free_rate=0.04)

        assert result.skip_reason is None, f"unexpected skip: {result.skip_reason}"

        fcf_projection = captured["dcf_result"]["fcf_projection"]
        ebit_y1 = fcf_projection.loc[1, "ebit"]
        nopat_y1 = fcf_projection.loc[1, "nopat"]
        tax_rate_actually_used_by_dcf = 1 - (nopat_y1 / ebit_y1)

        assert tax_rate_actually_used_by_dcf == pytest.approx(0.10, abs=1e-9)
        # The two paths must agree -- not just both happen to equal 0.10.
        assert result.tax_rate == pytest.approx(tax_rate_actually_used_by_dcf, abs=1e-9)
        assert result.tax_rate == pytest.approx(0.10, abs=1e-9)

    def test_no_override_falls_through_to_statement_derived_rate_in_both_paths(self, monkeypatch):
        """With no explicit override, both paths must agree on the statement-derived rate (30/100 = 30%)."""
        income_stmt = _income_statement(pretax_income=100.0, tax_provision=30.0)
        balance_sheet = _balance_sheet()
        _patch_point_in_time_lookups(monkeypatch, income_stmt=income_stmt, balance_sheet=balance_sheet)

        captured = {}
        real_run_dcf_valuation = engine.run_dcf_valuation

        def _capturing_run_dcf_valuation(financial_data, assumptions):
            result = real_run_dcf_valuation(financial_data, assumptions)
            captured["dcf_result"] = result
            return result

        monkeypatch.setattr(engine, "run_dcf_valuation", _capturing_run_dcf_valuation)

        assumptions = DCFAssumptions(revenue_growth_rate=0.05, operating_margin=0.15)  # tax_rate left None
        result = engine.compute_valuation("AAPL", AS_OF_DATE, assumptions, risk_free_rate=0.04)

        assert result.skip_reason is None, f"unexpected skip: {result.skip_reason}"

        fcf_projection = captured["dcf_result"]["fcf_projection"]
        ebit_y1 = fcf_projection.loc[1, "ebit"]
        nopat_y1 = fcf_projection.loc[1, "nopat"]
        tax_rate_actually_used_by_dcf = 1 - (nopat_y1 / ebit_y1)

        assert tax_rate_actually_used_by_dcf == pytest.approx(0.30, abs=1e-9)
        assert result.tax_rate == pytest.approx(tax_rate_actually_used_by_dcf, abs=1e-9)

    def test_no_override_and_no_derivable_rate_falls_back_to_default_in_both_paths(self, monkeypatch):
        """Neither an override nor a derivable statement rate -- both paths must fall back to DEFAULT_TAX_RATE."""
        income_stmt = _income_statement(pretax_income=None, tax_provision=None)  # no derivable tax rate
        balance_sheet = _balance_sheet()
        _patch_point_in_time_lookups(monkeypatch, income_stmt=income_stmt, balance_sheet=balance_sheet)

        captured = {}
        real_run_dcf_valuation = engine.run_dcf_valuation

        def _capturing_run_dcf_valuation(financial_data, assumptions):
            result = real_run_dcf_valuation(financial_data, assumptions)
            captured["dcf_result"] = result
            return result

        monkeypatch.setattr(engine, "run_dcf_valuation", _capturing_run_dcf_valuation)

        assumptions = DCFAssumptions(revenue_growth_rate=0.05, operating_margin=0.15)
        result = engine.compute_valuation("AAPL", AS_OF_DATE, assumptions, risk_free_rate=0.04)

        assert result.skip_reason is None, f"unexpected skip: {result.skip_reason}"

        fcf_projection = captured["dcf_result"]["fcf_projection"]
        ebit_y1 = fcf_projection.loc[1, "ebit"]
        nopat_y1 = fcf_projection.loc[1, "nopat"]
        tax_rate_actually_used_by_dcf = 1 - (nopat_y1 / ebit_y1)

        assert tax_rate_actually_used_by_dcf == pytest.approx(DEFAULT_TAX_RATE, abs=1e-9)
        assert result.tax_rate == pytest.approx(DEFAULT_TAX_RATE, abs=1e-9)
