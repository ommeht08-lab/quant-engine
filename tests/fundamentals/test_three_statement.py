import datetime as dt
from decimal import Decimal

import pytest

from src.fundamentals.adapters.fixture import make_fact, make_period, make_provenance
from src.fundamentals.quarterly import (
    QuarterSeriesKey,
    QuarterValueOrigin,
    QuarterlyFundamentals,
    StandaloneQuarterValue,
)
from src.fundamentals.selection import select_point_in_time
from src.fundamentals.three_statement import (
    LINKED_DURATION_CONCEPTS,
    ThreeStatementAssemblyIssueCode,
    ThreeStatementIssueCode,
    ThreeStatementPeriodInput,
    assemble_linked_three_statement_history,
    link_three_statements,
)
from src.fundamentals.types import FactContext, StatementKind


CIK = "0001111111"
CUTOFF = dt.datetime(2025, 1, 1, tzinfo=dt.timezone.utc)
QUARTER_DATES = (
    (dt.date(2023, 10, 1), dt.date(2023, 12, 30)),
    (dt.date(2023, 12, 31), dt.date(2024, 3, 30)),
    (dt.date(2024, 3, 31), dt.date(2024, 6, 29)),
    (dt.date(2024, 6, 30), dt.date(2024, 9, 28)),
)


def _input(**overrides):
    values = {
        "cik": "320193",
        "fiscal_year": 2024,
        "fiscal_quarter": 1,
        "period_start": dt.date(2023, 10, 1),
        "period_end": dt.date(2023, 12, 30),
        "currency": "USD",
        "revenue": Decimal("119575"),
        "operating_income": Decimal("40373"),
        "net_income": Decimal("33916"),
        "depreciation_and_amortization": Decimal("2848"),
        "operating_cash_flow": Decimal("39895"),
        "capital_expenditures": Decimal("2392"),
        "investing_cash_flow": Decimal("1927"),
        "financing_cash_flow": Decimal("-30585"),
        "exchange_rate_effect": Decimal("0"),
        "net_change_in_cash": Decimal("11237"),
        "beginning_cash": Decimal("30737"),
        "ending_cash": Decimal("41974"),
        "total_assets": Decimal("353514"),
        "total_liabilities": Decimal("279414"),
        "total_equity": Decimal("74100"),
    }
    values.update(overrides)
    return ThreeStatementPeriodInput(**values)


def _linked_fixture(*, equity_concept="total_equity", broken_balance_quarter=None):
    quarter_values = {
        "revenue": Decimal("100"),
        "operating_income": Decimal("20"),
        "net_income": Decimal("10"),
        "depreciation_and_amortization": Decimal("3"),
        "operating_cash_flow": Decimal("20"),
        "capital_expenditures": Decimal("2"),
        "investing_cash_flow": Decimal("-5"),
        "financing_cash_flow": Decimal("-5"),
        "net_change_in_cash": Decimal("10"),
    }
    income_concepts = {"revenue", "operating_income", "net_income"}
    all_facts = []
    standalone = []
    for fiscal_quarter, (period_start, period_end) in enumerate(QUARTER_DATES, start=1):
        form_type = "10-K" if fiscal_quarter == 4 else "10-Q"
        provenance = make_provenance(
            accession_number=f"0001111111-24-{fiscal_quarter:06d}",
            filed_date=period_end + dt.timedelta(days=30),
            accepted_at=dt.datetime.combine(
                period_end + dt.timedelta(days=30),
                dt.time(12),
                tzinfo=dt.timezone.utc,
            ),
            form_type=form_type,
        )
        for concept in LINKED_DURATION_CONCEPTS:
            statement_kind = (
                StatementKind.INCOME_STATEMENT
                if concept in income_concepts
                else StatementKind.CASH_FLOW
            )
            fact = make_fact(
                statement_kind=statement_kind,
                concept=concept,
                period=make_period(
                    fiscal_year=2024,
                    fiscal_period=f"Q{fiscal_quarter}",
                    period_start=period_start,
                    period_end=period_end,
                    periodicity="quarterly",
                ),
                value=quarter_values[concept],
                provenance=provenance,
            )
            all_facts.append(fact)
            standalone.append(
                StandaloneQuarterValue(
                    key=QuarterSeriesKey(
                        statement_kind=statement_kind,
                        concept=concept,
                        unit="USD",
                        currency="USD",
                        context=FactContext(CIK),
                    ),
                    fiscal_year=2024,
                    fiscal_quarter=fiscal_quarter,
                    period_start=period_start,
                    period_end=period_end,
                    value=quarter_values[concept],
                    origin=QuarterValueOrigin.REPORTED,
                    source_facts=(fact,),
                )
            )

        balance_period = make_period(
            fiscal_year=2024,
            fiscal_period="FY" if fiscal_quarter == 4 else f"Q{fiscal_quarter}",
            period_end=period_end,
            periodicity="annual" if fiscal_quarter == 4 else "quarterly",
        )
        balances = {
            "cash_and_restricted_cash": Decimal(90 + fiscal_quarter * 10),
            "total_assets": Decimal(
                501 if broken_balance_quarter == fiscal_quarter else 500
            ),
            "total_liabilities": Decimal("300"),
            equity_concept: Decimal("200"),
        }
        for concept, value in balances.items():
            all_facts.append(
                make_fact(
                    statement_kind=StatementKind.BALANCE_SHEET,
                    concept=concept,
                    period=balance_period,
                    value=value,
                    provenance=provenance,
                )
            )

    history = select_point_in_time(all_facts, CUTOFF, cik=CIK)
    quarterly = QuarterlyFundamentals(
        cik=CIK,
        knowledge_cutoff=CUTOFF,
        quarters=tuple(standalone),
    )
    return history, quarterly


def test_links_one_exact_period_and_derives_fcf_and_cash_conversion_bridge():
    result = link_three_statements(_input())

    assert result.is_complete
    assert result.source.cik == "0000320193"
    assert result.linked_period.free_cash_flow == Decimal("37503")
    assert result.linked_period.operating_cash_conversion_adjustments == Decimal("3131")
    assert result.issues == ()


@pytest.mark.parametrize(
    ("overrides", "code", "difference"),
    (
        (
            {"total_assets": Decimal("353515")},
            ThreeStatementIssueCode.BALANCE_SHEET_DOES_NOT_BALANCE,
            Decimal("1"),
        ),
        (
            {"financing_cash_flow": Decimal("-30584")},
            ThreeStatementIssueCode.CASH_FLOW_DOES_NOT_SUM,
            Decimal("1"),
        ),
        (
            {"ending_cash": Decimal("41973")},
            ThreeStatementIssueCode.CASH_ROLLFORWARD_DOES_NOT_BALANCE,
            Decimal("1"),
        ),
    ),
)
def test_refuses_broken_accounting_links_without_partial_output(
    overrides, code, difference
):
    result = link_three_statements(_input(**overrides))

    assert not result.is_complete
    assert result.linked_period is None
    assert [(issue.code, issue.difference) for issue in result.issues] == [
        (code, difference)
    ]


def test_reports_every_broken_link_in_deterministic_statement_order():
    result = link_three_statements(
        _input(
            total_assets=Decimal("353515"),
            financing_cash_flow=Decimal("-30584"),
            ending_cash=Decimal("41973"),
        )
    )

    assert [issue.code for issue in result.issues] == [
        ThreeStatementIssueCode.BALANCE_SHEET_DOES_NOT_BALANCE,
        ThreeStatementIssueCode.CASH_FLOW_DOES_NOT_SUM,
        ThreeStatementIssueCode.CASH_ROLLFORWARD_DOES_NOT_BALANCE,
    ]


def test_requires_exact_finite_decimal_inputs():
    with pytest.raises(ValueError, match="revenue"):
        _input(revenue=119575)


@pytest.mark.parametrize("fiscal_quarter", (True, 1.0, 0, 5))
def test_fiscal_quarter_must_be_an_integer_from_one_through_four(fiscal_quarter):
    with pytest.raises(ValueError, match="fiscal_quarter"):
        _input(fiscal_quarter=fiscal_quarter)


def test_period_boundaries_must_be_plain_dates():
    with pytest.raises(ValueError, match="period"):
        _input(period_end=dt.datetime(2023, 12, 30))


def test_capital_expenditures_uses_positive_spend_convention():
    with pytest.raises(ValueError, match="positive-spend"):
        _input(capital_expenditures=Decimal("-1"))


class TestLinkedHistoryAssembly:
    def test_integrates_quarters_and_balance_snapshots_into_linked_periods(self):
        history, quarterly = _linked_fixture()

        result = assemble_linked_three_statement_history(history, quarterly)

        assert result.is_complete
        assert [period.source.fiscal_quarter for period in result.periods] == [2, 3, 4]
        assert [period.source.beginning_cash for period in result.periods] == [
            Decimal("100"),
            Decimal("110"),
            Decimal("120"),
        ]
        assert [period.free_cash_flow for period in result.periods] == [
            Decimal("18"),
            Decimal("18"),
            Decimal("18"),
        ]

    def test_shareholders_equity_is_safe_only_when_the_balance_identity_proves_it(self):
        history, quarterly = _linked_fixture(equity_concept="shareholders_equity")

        result = assemble_linked_three_statement_history(history, quarterly)

        assert result.is_complete
        assert all(period.source.total_equity == Decimal("200") for period in result.periods)

    def test_one_broken_accounting_link_refuses_the_entire_history(self):
        history, quarterly = _linked_fixture(broken_balance_quarter=3)

        result = assemble_linked_three_statement_history(history, quarterly)

        assert not result.is_complete
        assert result.periods == ()
        assert result.issues[0].code is ThreeStatementAssemblyIssueCode.ACCOUNTING_LINK_FAILURE
        assert result.issues[0].fiscal_quarter == 3
        assert result.issues[0].linkage_issues[0].code is (
            ThreeStatementIssueCode.BALANCE_SHEET_DOES_NOT_BALANCE
        )

    def test_missing_required_quarter_value_refuses_without_partial_output(self):
        history, quarterly = _linked_fixture()
        quarterly = QuarterlyFundamentals(
            cik=quarterly.cik,
            knowledge_cutoff=quarterly.knowledge_cutoff,
            quarters=tuple(
                value
                for value in quarterly.quarters
                if not (
                    value.fiscal_quarter == 3
                    and value.key.concept == "operating_cash_flow"
                )
            ),
        )

        result = assemble_linked_three_statement_history(history, quarterly)

        assert not result.is_complete
        assert result.periods == ()
        assert result.issues[0].code is ThreeStatementAssemblyIssueCode.MISSING_QUARTER_VALUE
        assert result.issues[0].concept == "operating_cash_flow"
