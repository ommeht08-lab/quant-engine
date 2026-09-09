import datetime as dt
from decimal import Decimal

import pandas as pd
import pytest

from src.fundamentals.repository import InMemoryFundamentalsRepository
from src.fundamentals.types import StatementKind
from src.fundamentals.valuation_integration import (
    SecDCFIntegrationIssueCode,
    SecDCFPolicy,
    ValuationMarketObservations,
    prepare_sec_dcf_inputs,
    run_sec_dcf_shadow,
)
from src.fundamentals.valuation_snapshot import load_valuation_fundamentals_snapshot
from tests.fundamentals.test_valuation_snapshot import (
    CIK,
    _complete_facts,
    _duration_year,
    _request,
)


FY2022 = (
    dt.date(2021, 10, 3),
    dt.date(2021, 12, 31),
    dt.date(2022, 4, 2),
    dt.date(2022, 7, 2),
    dt.date(2022, 10, 1),
)


def _three_year_facts():
    facts = list(_complete_facts())
    for concept, statement_kind, values in (
        ("revenue", StatementKind.INCOME_STATEMENT, (100, 100, 100, 400)),
        ("operating_income", StatementKind.INCOME_STATEMENT, (20, 20, 20, 80)),
        ("operating_cash_flow", StatementKind.CASH_FLOW, (30, 30, 30, 120)),
        ("capital_expenditures", StatementKind.CASH_FLOW, (10, 10, 10, 40)),
    ):
        facts.extend(
            _duration_year(
                2022,
                FY2022,
                values,
                concept=concept,
                statement_kind=statement_kind,
            )
        )
    return tuple(facts)


def _snapshot(*, three_years=True):
    facts = _three_year_facts() if three_years else _complete_facts()
    result = load_valuation_fundamentals_snapshot(
        InMemoryFundamentalsRepository(facts),
        _request(),
    )
    assert result.is_complete
    return result.snapshot


def _market(**overrides):
    values = {
        "cik": CIK,
        "ticker": "test",
        "observed_at": dt.datetime(2024, 12, 31, tzinfo=dt.timezone.utc),
        "current_price": Decimal("50"),
        "current_shares_outstanding": Decimal("100"),
        "levered_beta": Decimal("1"),
        "sector": "Technology",
        "risk_free_rate": Decimal("0.04"),
        "equity_source_adapter": "yahoo_finance",
        "risk_free_rate_source_adapter": "yahoo_finance_tnx",
    }
    values.update(overrides)
    return ValuationMarketObservations(**values)


def _legacy_financial_data():
    prior = pd.Timestamp("2023-09-30")
    latest = pd.Timestamp("2024-09-28")
    return {
        "ticker": "WRONG",
        "income_statement": pd.DataFrame(
            {
                prior: {
                    "Total Revenue": 400.0,
                    "Operating Income": 72.0,
                    "Pretax Income": 70.0,
                    "Tax Provision": 14.0,
                    "Interest Expense": 6.0,
                },
                latest: {
                    "Total Revenue": 520.0,
                    "Operating Income": 104.0,
                    "Pretax Income": 90.0,
                    "Tax Provision": 18.0,
                    "Interest Expense": 6.0,
                },
            }
        ),
        "balance_sheet": pd.DataFrame(
            {latest: {"Total Debt": 120.0, "Cash And Cash Equivalents": 45.0}}
        ),
        "cash_flow": pd.DataFrame(
            {latest: {"Operating Cash Flow": 155.0, "Capital Expenditure": -55.0}}
        ),
        "current_price": 999.0,
        "shares_outstanding": 999.0,
        "beta": 9.0,
        "sector": "Wrong",
    }


def test_preparation_smooths_one_isolated_q4_increase_across_recent_ttm_history():
    result = prepare_sec_dcf_inputs(_snapshot(), _market())

    assert result.is_complete
    prepared = result.prepared
    # The latest TTM comparison is 25%, but the recent four-observation
    # median is 0% because Q1-Q3 were flat. Q4 is not made permanent.
    assert prepared.snapshot.comparable_revenue_growth[-1].rate == Decimal("0.25")
    assert prepared.revenue_growth_rate == Decimal("0")
    assert prepared.operating_margin == Decimal("0.2")
    assert prepared.capital_expenditures_pct_revenue == Decimal("0.1")
    assert prepared.snapshot.latest_balance.reported_term_debt == Decimal("100")


def test_shadow_run_uses_sec_statements_and_the_same_market_observations_on_both_sides():
    prepared = prepare_sec_dcf_inputs(_snapshot(), _market()).prepared
    legacy_data = _legacy_financial_data()

    result = run_sec_dcf_shadow(prepared, legacy_data)

    assert result.is_complete
    report = result.report
    assert report.sec.source == "sec_snapshot"
    assert report.legacy.source == "legacy_yahoo_statements"
    assert report.sec.base_revenue == Decimal("500.0")
    assert report.legacy.base_revenue == Decimal("520.0")
    assert report.sec.total_debt == Decimal("100.0")
    assert report.legacy.total_debt == Decimal("120.0")
    assert legacy_data["current_price"] == 999.0
    assert legacy_data["shares_outstanding"] == 999.0
    assert {delta.metric for delta in report.deltas} == {
        "base_revenue",
        "revenue_growth_rate",
        "operating_margin",
        "tax_rate",
        "cost_of_debt",
        "total_debt",
        "cash_and_equivalents",
        "wacc",
        "enterprise_value",
        "equity_value",
        "intrinsic_value_per_share",
    }


def test_preparation_refuses_future_market_data_and_issuer_mismatch():
    snapshot = _snapshot()
    future = prepare_sec_dcf_inputs(
        snapshot,
        _market(observed_at=dt.datetime(2025, 1, 2, tzinfo=dt.timezone.utc)),
    )
    mismatch = prepare_sec_dcf_inputs(snapshot, _market(cik="999999"))

    assert future.issues[0].code is SecDCFIntegrationIssueCode.FUTURE_MARKET_OBSERVATION
    assert mismatch.issues[0].code is SecDCFIntegrationIssueCode.ISSUER_MISMATCH


def test_preparation_refuses_history_that_cannot_smooth_across_four_ttm_observations():
    result = prepare_sec_dcf_inputs(_snapshot(three_years=False), _market())

    assert not result.is_complete
    assert result.issues[0].code is SecDCFIntegrationIssueCode.INSUFFICIENT_SMOOTHING_HISTORY


def test_policy_rejects_implicit_or_economically_invalid_values():
    with pytest.raises(ValueError, match="cost_of_debt"):
        SecDCFPolicy(cost_of_debt=Decimal("-0.01"))


def test_legacy_shadow_failure_is_typed_and_sanitized():
    prepared = prepare_sec_dcf_inputs(_snapshot(), _market()).prepared

    result = run_sec_dcf_shadow(prepared, {"database_url": "secret-value"})

    assert not result.is_complete
    assert result.issues[0].code is SecDCFIntegrationIssueCode.LEGACY_DCF_FAILED
    assert "secret" not in result.issues[0].message
