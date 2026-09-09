import datetime as dt
from dataclasses import replace
from decimal import Decimal

import pandas as pd
import pytest

from src.fundamentals.repository import InMemoryFundamentalsRepository
from src.fundamentals.types import StatementKind
from src.fundamentals.valuation_integration import (
    APPLE_SEC_YAHOO_ALIGNMENT_FY2026_Q3_V1,
    SEC_DCF_SHADOW_GATE_V1,
    SecDCFIntegrationIssueCode,
    SecDCFPolicy,
    SecDCFShadowGatePolicy,
    SecDCFShadowGateStatus,
    SecDCFShadowThreshold,
    SecYahooPeriodAlignment,
    ValuationMarketObservations,
    YahooTTMStatementBundle,
    evaluate_sec_dcf_shadow_gate,
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


YAHOO_PERIOD_END = dt.date(2024, 9, 30)
ALIGNMENT = SecYahooPeriodAlignment(
    version="test-sec-yahoo-alignment-v1",
    cik=CIK,
    sec_period_end=dt.date(2024, 9, 28),
    yahoo_period_end=YAHOO_PERIOD_END,
)


def _yahoo_ttm_bundle(**overrides):
    latest = pd.Timestamp(YAHOO_PERIOD_END)
    values = {
        "ticker": "TEST",
        "observed_at": dt.datetime(2024, 12, 31, tzinfo=dt.timezone.utc),
        "ttm_income_statement": pd.DataFrame(
            {
                latest: {
                    "Total Revenue": 520.0,
                    "Operating Income": 104.0,
                },
            }
        ),
        "quarterly_balance_sheet": pd.DataFrame(
            {latest: {"Total Debt": 120.0, "Cash And Cash Equivalents": 45.0}}
        ),
        "ttm_cash_flow": pd.DataFrame(
            {latest: {"Operating Cash Flow": 155.0, "Capital Expenditure": -55.0}}
        ),
    }
    values.update(overrides)
    return YahooTTMStatementBundle(**values)


def test_apple_alignment_records_exact_sec_and_yahoo_period_labels():
    assert APPLE_SEC_YAHOO_ALIGNMENT_FY2026_Q3_V1.cik == "0000320193"
    assert APPLE_SEC_YAHOO_ALIGNMENT_FY2026_Q3_V1.sec_period_end == dt.date(
        2026, 6, 27
    )
    assert APPLE_SEC_YAHOO_ALIGNMENT_FY2026_Q3_V1.yahoo_period_end == dt.date(
        2026, 6, 30
    )


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
    yahoo_ttm = _yahoo_ttm_bundle()

    result = run_sec_dcf_shadow(prepared, yahoo_ttm, ALIGNMENT)

    assert result.is_complete
    report = result.report
    assert report.sec.source == "sec_snapshot"
    assert report.legacy.source == "yahoo_ttm_statements"
    assert report.period_alignment == ALIGNMENT
    assert report.sec.base_revenue == Decimal("500.0")
    assert report.legacy.base_revenue == Decimal("520.0")
    assert report.sec.total_debt == Decimal("100.0")
    assert report.legacy.total_debt == Decimal("120.0")
    assert report.legacy.revenue_growth_rate == report.sec.revenue_growth_rate
    assert report.legacy.operating_margin == report.sec.operating_margin
    assert report.legacy.tax_rate == report.sec.tax_rate
    assert report.legacy.cost_of_debt == report.sec.cost_of_debt
    assert tuple(yahoo_ttm.ttm_income_statement.columns) == (
        pd.Timestamp(YAHOO_PERIOD_END),
    )
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
    malformed = _yahoo_ttm_bundle(
        ttm_income_statement=pd.DataFrame(
            {pd.Timestamp(YAHOO_PERIOD_END): {"database_url": "secret-value"}}
        )
    )

    result = run_sec_dcf_shadow(prepared, malformed, ALIGNMENT)

    assert not result.is_complete
    assert (
        result.issues[0].code
        is SecDCFIntegrationIssueCode.LEGACY_PERIOD_ALIGNMENT_FAILED
    )
    assert "secret" not in result.issues[0].message


@pytest.mark.parametrize(
    "bundle,alignment",
    (
        (
            _yahoo_ttm_bundle(ticker="WRONG"),
            ALIGNMENT,
        ),
        (
                _yahoo_ttm_bundle(
                    observed_at=dt.datetime(2025, 1, 2, tzinfo=dt.timezone.utc)
                ),
            ALIGNMENT,
        ),
        (
            _yahoo_ttm_bundle(
                ttm_cash_flow=pd.DataFrame(
                    {
                        pd.Timestamp(YAHOO_PERIOD_END): {
                            "Operating Cash Flow": 155.0,
                            "Capital Expenditure": 55.0,
                        }
                    }
                )
            ),
            ALIGNMENT,
        ),
        (
            _yahoo_ttm_bundle(),
            replace(ALIGNMENT, yahoo_period_end=dt.date(2024, 9, 29)),
        ),
    ),
)
def test_shadow_refuses_unaligned_or_invalid_yahoo_ttm_inputs(bundle, alignment):
    prepared = prepare_sec_dcf_inputs(_snapshot(), _market()).prepared

    result = run_sec_dcf_shadow(prepared, bundle, alignment)

    assert not result.is_complete
    assert (
        result.issues[0].code
        is SecDCFIntegrationIssueCode.LEGACY_PERIOD_ALIGNMENT_FAILED
    )


def _reports_on_distinct_dates(report, count=5):
    reports = []
    for day_offset in range(count):
        observed_at = report.prepared.market.observed_at - dt.timedelta(days=day_offset)
        prepared = replace(
            report.prepared,
            market=replace(report.prepared.market, observed_at=observed_at),
        )
        reports.append(
            replace(
                report,
                prepared=prepared,
                yahoo_statements_observed_at=observed_at,
            )
        )
    return tuple(reports)


def test_shadow_gate_requires_repeated_evidence_before_it_can_pass_or_fail():
    prepared = prepare_sec_dcf_inputs(_snapshot(), _market()).prepared
    report = run_sec_dcf_shadow(prepared, _yahoo_ttm_bundle(), ALIGNMENT).report

    result = evaluate_sec_dcf_shadow_gate((report,))

    assert result.status is SecDCFShadowGateStatus.INSUFFICIENT_EVIDENCE
    assert result.report_count == 1
    assert result.observation_date_count == 1
    assert result.base_period_end_count == 1
    assert result.breaches


def test_shadow_gate_fails_repeated_evidence_that_exceeds_a_threshold():
    prepared = prepare_sec_dcf_inputs(_snapshot(), _market()).prepared
    report = run_sec_dcf_shadow(prepared, _yahoo_ttm_bundle(), ALIGNMENT).report

    one_period_policy = replace(SEC_DCF_SHADOW_GATE_V1, minimum_base_period_ends=1)
    result = evaluate_sec_dcf_shadow_gate(
        _reports_on_distinct_dates(report),
        policy=one_period_policy,
    )

    assert result.status is SecDCFShadowGateStatus.FAILED
    assert {breach.metric for breach in result.breaches}
    assert result.policy_version == SEC_DCF_SHADOW_GATE_V1.version


def test_shadow_gate_can_pass_sufficient_evidence_within_an_explicit_policy():
    prepared = prepare_sec_dcf_inputs(_snapshot(), _market()).prepared
    report = run_sec_dcf_shadow(prepared, _yahoo_ttm_bundle(), ALIGNMENT).report
    permissive = SecDCFShadowGatePolicy(
        version="test-shadow-gate",
        minimum_reports=5,
        minimum_observation_dates=5,
        minimum_base_period_ends=1,
        thresholds=tuple(
            SecDCFShadowThreshold(
                delta.metric,
                max_absolute_difference=abs(delta.absolute_difference) + Decimal("1"),
            )
            for delta in report.deltas
        ),
    )

    result = evaluate_sec_dcf_shadow_gate(
        _reports_on_distinct_dates(report),
        policy=permissive,
    )

    assert result.status is SecDCFShadowGateStatus.PASSED
    assert result.breaches == ()
