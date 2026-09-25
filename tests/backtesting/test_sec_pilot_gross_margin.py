"""Gross-margin inputs for the SEC pilot's quality statements.

Gross margin needs gross profit or cost of revenue. A partially tagged gross
profit series (Caterpillar tags ``GrossProfit`` only in one 10-K's quarterly
note, on a narrower basis than total revenues) is dropped wholesale when --
and only when -- cost of revenue is complete; it is never mixed with revenue
less cost of revenue across periods.
"""

from src.backtesting.sec_pilot import _GROSS_PROFIT_DROPPED, _assemble_quality_flows, describe_quality_gap
from src.fundamentals.types import StatementKind
from tests.fundamentals.test_quarterly import FY2023_DATES, FY2024_DATES, _history, _year_facts

FLOWS = {
    "revenue": StatementKind.INCOME_STATEMENT,
    "operating_income": StatementKind.INCOME_STATEMENT,
    "net_income": StatementKind.INCOME_STATEMENT,
    "operating_cash_flow": StatementKind.CASH_FLOW,
    "capital_expenditures": StatementKind.CASH_FLOW,
}
VALUES = (100, 110, 120, 460)


def _series(concept, kind=StatementKind.INCOME_STATEMENT, years=(2023, 2024)):
    dates = {2023: FY2023_DATES, 2024: FY2024_DATES}
    return [fact for year in years for fact in _year_facts(year, dates[year], VALUES, concept=concept, statement_kind=kind)]


def _with_gap(facts):
    """Drop FY2024 Q2: a hole inside the series, which quarterly assembly refuses."""
    return [f for f in facts if not (f.period.fiscal_year == 2024 and f.period.fiscal_period == "Q2")]


def _facts(*, gross_profit="none", cost="complete", drop=()):
    facts = [fact for concept, kind in FLOWS.items() if concept not in drop for fact in _series(concept, kind)]
    if cost != "none":
        cost_facts = _series("cost_of_revenue")
        facts += _with_gap(cost_facts) if cost == "gap" else cost_facts
    if gross_profit != "none":
        gross = _series("gross_profit")
        facts += _with_gap(gross) if gross_profit == "gap" else gross
    return facts


def _concepts(assembly):
    return {value.key.concept for value in assembly.ttm_values}


def test_complete_gross_profit_is_used_unchanged():
    assembly, approximations = _assemble_quality_flows(_history(_facts(gross_profit="complete")))

    assert assembly.is_complete and approximations == ()
    assert {"gross_profit", "cost_of_revenue"} <= _concepts(assembly)


def test_absent_gross_profit_needs_no_approximation():
    assembly, approximations = _assemble_quality_flows(_history(_facts()))

    assert assembly.is_complete and approximations == ()
    assert "gross_profit" not in _concepts(assembly)


def test_partial_gross_profit_is_dropped_wholesale_when_cost_of_revenue_is_complete():
    assembly, approximations = _assemble_quality_flows(_history(_facts(gross_profit="gap")))

    assert assembly.is_complete
    assert approximations == (_GROSS_PROFIT_DROPPED,)
    assert "gross_profit" not in _concepts(assembly)  # not even for the tagged quarters
    assert "cost_of_revenue" in _concepts(assembly)


def test_partial_gross_profit_still_refuses_without_cost_of_revenue():
    history = _history(_facts(gross_profit="gap", cost="none"))

    assembly, approximations = _assemble_quality_flows(history)

    assert not assembly.is_complete and approximations == ()
    assert assembly.issues[0].concept == "gross_profit"
    assert describe_quality_gap(history) == "gross_profit (missing_period)"


def test_partial_gross_profit_still_refuses_with_partial_cost_of_revenue():
    assembly, approximations = _assemble_quality_flows(_history(_facts(gross_profit="gap", cost="gap")))

    assert not assembly.is_complete and approximations == ()
    assert assembly.issues[0].concept == "cost_of_revenue"  # unchanged: refused before any fallback


def test_a_required_gap_is_reported_instead_of_the_dropped_series():
    history = _history(_facts(gross_profit="gap", drop=("net_income",)) + _with_gap(_series("net_income")))

    assembly, approximations = _assemble_quality_flows(history)

    assert not assembly.is_complete and approximations == ()
    assert assembly.issues[0].concept == "net_income"
    assert describe_quality_gap(history).startswith("net_income")
