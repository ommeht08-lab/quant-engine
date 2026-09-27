"""CAT's ME&T margin reaches only CAT's Piotroski gross-margin factor."""

from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pandas as pd
import pytest

from src.backtesting import sec_pilot
from src.backtesting.historical_tester import TickerAnalysis
from src.backtesting.sec_pilot import (
    PilotConfig,
    PilotIssuer,
    SecQualityStatements,
    decide_portfolio,
)
from src.fundamentals.issuer_manifest import issuer_policy_for
from src.fundamentals.repository import InMemoryFundamentalsRepository
from src.valuation.piotroski import calculate_f_score_from_statements, f_score_factors_from_statements
from src.valuation_input import ValuationInputSource
from tests.fundamentals.segment_margin_fixtures import INGESTED_AT, pilot_facts

T, T1 = pd.Timestamp("2024-06-30"), pd.Timestamp("2023-06-30")
MET_CURRENT = Decimal(21605) / Decimal(63025)
MET_PRIOR = Decimal(18803) / Decimal(61793)


def _statements(*, gross_profit=True):
    # Consolidated gross margin falls (40% -> 30%); revenue/assets rises, so
    # asset turnover passes on consolidated revenue either way.
    income = {"Total Revenue": {T: 110.0, T1: 100.0}, "Net Income": {T: 10.0, T1: 9.0}}
    if gross_profit:
        income["Gross Profit"] = {T: 33.0, T1: 40.0}
    balance = {
        "Total Assets": {T: 200.0, T1: 200.0},
        "Current Assets": {T: 50.0, T1: 50.0},
        "Current Liabilities": {T: 40.0, T1: 40.0},
        "Long Term Debt": {T: 60.0, T1: 60.0},
        "Share Issued": {T: 10.0, T1: 10.0},
    }
    cash = {"Operating Cash Flow": {T: 12.0, T1: 11.0}}
    return pd.DataFrame(income).T, pd.DataFrame(balance).T, pd.DataFrame(cash).T


def test_override_changes_only_the_gross_margin_factor():
    income, balance, cash = _statements()
    base = calculate_f_score_from_statements(income, balance, cash)

    assert calculate_f_score_from_statements(income, balance, cash, gross_margin_override=(MET_CURRENT, MET_PRIOR)) == base + 1
    assert calculate_f_score_from_statements(income, balance, cash, gross_margin_override=(MET_PRIOR, MET_CURRENT)) == base
    # Asset turnover still uses consolidated revenue: removing the gross
    # profit rows changes nothing but factor 8.
    no_gp = _statements(gross_profit=False)
    assert calculate_f_score_from_statements(*no_gp) == base
    assert calculate_f_score_from_statements(*no_gp, gross_margin_override=(MET_CURRENT, MET_PRIOR)) == base + 1


# Each variant flips one of the eight other factors relative to _statements().
_OTHER_FACTOR_VARIANTS = {
    "roa_positive": ("income", "Net Income", T, -10.0),
    "cfo_positive": ("cash", "Operating Cash Flow", T, -12.0),
    "accruals": ("cash", "Operating Cash Flow", T, 5.0),
    "roa_improves": ("income", "Net Income", T1, 30.0),
    "leverage_falls": ("balance", "Long Term Debt", T, 50.0),
    "current_ratio_rises": ("balance", "Current Assets", T, 60.0),
    "no_dilution": ("balance", "Share Issued", T, 11.0),
    "asset_turnover_rises": ("income", "Total Revenue", T, 90.0),
}


def _variant(name, *, gross_profit=True):
    income, balance, cash = _statements(gross_profit=gross_profit)
    frames = {"income": income, "balance": balance, "cash": cash}
    frame, row, column, value = _OTHER_FACTOR_VARIANTS[name]
    frames[frame].loc[row, column] = value
    return frames["income"], frames["balance"], frames["cash"]


@pytest.mark.parametrize("name", [None, *_OTHER_FACTOR_VARIANTS])
def test_the_other_eight_factors_are_identical_with_and_without_the_override(name):
    consolidated = _variant(name) if name else _statements()
    segment_basis = _variant(name, gross_profit=False) if name else _statements(gross_profit=False)
    base = f_score_factors_from_statements(*consolidated)
    overridden = f_score_factors_from_statements(*segment_basis, gross_margin_override=(MET_CURRENT, MET_PRIOR))

    assert len(base) == len(overridden) == 9
    assert [base[i] for i in range(9) if i != 7] == [overridden[i] for i in range(9) if i != 7]
    assert (base[7], overridden[7]) == (False, True)
    assert calculate_f_score_from_statements(*consolidated) == sum(base)
    if name:
        # The variant really flips its factor, so this is not vacuous.
        assert f_score_factors_from_statements(*consolidated) != f_score_factors_from_statements(*_statements())


# --------------------------------------------------------------------------
# decide_portfolio: the rule applies to CAT alone
# --------------------------------------------------------------------------


class _Loader:
    def load(self, ticker, cutoff, vintage, source):
        assert source is ValuationInputSource.SEC
        return SimpleNamespace(
            is_complete=True,
            valuation_input=SimpleNamespace(
                provenance=SimpleNamespace(source=ValuationInputSource.SEC),
                financial_data={"current_price": 100.0, "shares_outstanding": 1.0, "beta": 1.0},
                default_assumptions=SimpleNamespace(tax_rate=0.21),
            ),
        )


@pytest.fixture
def harness(monkeypatch):
    calls = {"build": {}, "f_score": {}, "median_tickers": None}

    def build(history, *, optional_concepts):
        calls["build"][history] = optional_concepts
        income, balance, cash = _statements(gross_profit=bool(optional_concepts))
        income.attrs["ticker"] = history
        return SecQualityStatements(
            income_stmt=income, balance_sheet=balance, cash_flow=cash, cover_shares=10.0,
            ingestion_batch_ids=(), filing_accessions=(), approximations=(),
            income_period_ends=(date(2024, 6, 30), date(2023, 6, 30)),
        )

    def f_score(income, balance, cash, *, gross_margin_override=None):
        calls["f_score"][income.attrs["ticker"]] = gross_margin_override
        return calculate_f_score_from_statements(income, balance, cash, gross_margin_override=gross_margin_override)

    def medians(valuations):
        calls["median_tickers"] = sorted(v.ticker for v in valuations)
        return {}

    monkeypatch.setattr(sec_pilot, "load_sec_quality_history", lambda repo, policy, c, v: policy.ticker)
    monkeypatch.setattr(sec_pilot, "build_sec_quality_statements", build)
    monkeypatch.setattr(sec_pilot, "_provenance_dict", lambda provenance, quality: {})
    monkeypatch.setattr(sec_pilot, "run_dcf_valuation", lambda data, assumptions: {"intrinsic_value_per_share": 200.0, "wacc": 0.08, "fcf_yield": 0.03})
    monkeypatch.setattr(sec_pilot, "extract_valuation_inputs", lambda data: {"total_debt": 0.0, "cash_and_equivalents": 0.0})
    monkeypatch.setattr(sec_pilot, "altman_z_from_statements", lambda *a, **k: 5.0)
    monkeypatch.setattr(sec_pilot, "trend_passes_at", lambda *a: True)
    monkeypatch.setattr(sec_pilot, "rsi_at", lambda *a: 40.0)
    monkeypatch.setattr(sec_pilot, "calculate_f_score_from_statements", f_score)
    monkeypatch.setattr(sec_pilot, "calculate_sector_median_price_to_intrinsic", medians)
    monkeypatch.setattr(
        sec_pilot, "score_ticker",
        lambda valuation, medians: TickerAnalysis(valuation.ticker, valuation.as_of_date, valuation.sector, skip_reason="stub"),
    )
    return calls


def _decide(repository, universe=(PilotIssuer("AAPL", "Technology"), PilotIssuer("CAT", "Industrials"))):
    config = PilotConfig(universe=universe)
    decisions, _weights = decide_portfolio(
        config, loader=_Loader(), repository=repository, manifest_lookup=issuer_policy_for,
        prices=None, data_vintage_cutoff=INGESTED_AT + timedelta(hours=1),
    )
    return {record.ticker: record for record in decisions}


def test_cat_alone_gets_the_segment_margin_and_other_factors_stay_consolidated(harness):
    records = _decide(InMemoryFundamentalsRepository(pilot_facts()))
    consolidated = calculate_f_score_from_statements(*_statements())

    assert harness["build"] == {"AAPL": sec_pilot._QUALITY_FLOW_OPTIONAL, "CAT": ()}
    assert harness["f_score"] == {"AAPL": None, "CAT": (MET_CURRENT, MET_PRIOR)}
    assert records["AAPL"].segment_gross_margin is None
    assert records["AAPL"].piotroski_f_score == consolidated
    assert "piotroski_gross_margin_segment_basis" not in records["AAPL"].approximations

    cat = records["CAT"]
    assert cat.status == "rejected_by_strategy"
    assert cat.piotroski_f_score == consolidated + 1  # factor 8 only
    assert "piotroski_gross_margin_segment_basis" in cat.approximations
    assert cat.segment_gross_margin["gross_margin_current"] == str(MET_CURRENT)
    assert cat.segment_gross_margin["filing_accessions"] == [
        "0000018230-23-000047", "0000018230-24-000009", "0000018230-24-000045",
    ]
    assert harness["median_tickers"] == ["AAPL", "CAT"]


def test_cat_is_refused_before_valuation_when_segment_facts_are_missing(harness):
    records = _decide(InMemoryFundamentalsRepository(()))

    cat = records["CAT"]
    assert cat.status == "refused"
    assert cat.reason.startswith("SEC segment gross margin refused:")
    assert cat.piotroski_f_score is None and cat.segment_gross_margin is None
    # A refused CAT never enters the sector medians; AAPL is unaffected.
    assert harness["median_tickers"] == ["AAPL"]
    assert records["AAPL"].piotroski_f_score == calculate_f_score_from_statements(*_statements())


def test_every_other_manifest_issuer_keeps_consolidated_gross_margin(harness):
    assert [issuer.ticker for issuer in sec_pilot.PILOT_UNIVERSE] == ["AAPL", "MSFT", "WMT", "CAT"]
    records = _decide(InMemoryFundamentalsRepository(pilot_facts()), sec_pilot.PILOT_UNIVERSE)
    consolidated = calculate_f_score_from_statements(*_statements())

    for ticker in ("AAPL", "MSFT", "WMT"):
        assert harness["build"][ticker] == sec_pilot._QUALITY_FLOW_OPTIONAL
        assert harness["f_score"][ticker] is None
        assert records[ticker].segment_gross_margin is None
        assert records[ticker].piotroski_f_score == consolidated
        assert "piotroski_gross_margin_segment_basis" not in records[ticker].approximations
    assert harness["f_score"]["CAT"] == (MET_CURRENT, MET_PRIOR)
    assert records["CAT"].piotroski_f_score == consolidated + 1
