"""
SEC-only $100,000 backtest pilot — PIPELINE VALIDATION, not performance evidence.

One formation date (2024-09-03, after the 16:00 ET close), four companies
(AAPL, MSFT, WMT, CAT), one 252-session holding period. Four companies and
one date cannot establish an investment edge; the result validates that the
point-in-time SEC pipeline, the live strategy rules, and the execution
accounting compose correctly without look-ahead.

What is point-in-time here
--------------------------
* Statements: every fundamental comes from published SEC facts through the
  shared valuation-input loader with ``source=sec`` (no Yahoo fallback) and
  from the same fact store for the quality metrics, all bounded by the
  knowledge cutoff (each filing's SEC acceptance time) and a recorded
  data-vintage cutoff.
* Share count: the SEC cover-page share count public at the cutoff.
* Prices and price signals: only sessions on or before the decision date
  feed the valuation price, beta, RSI, and 200-day trend.

Strategy rules
--------------
The live paper engine's own functions are reused unchanged: sector-relative
P/IV filter and Conviction Score (``score_ticker``), the five entry gates
in live order (fair value, Altman Z, 200-SMA trend, Piotroski, RSI) through
the same pure gate cores, the FCF-yield blend, Top-N selection, and
inverse-beta sizing with the 15% position and 25% sector caps. Capital the
caps cannot place stays in cash.

Refusals
--------
A company whose SEC inputs are refused is excluded and its 1/N share of
capital is held as unallocated cash in the strategy and the equal-weight
control; it is never redistributed. Every refusal is reported with its
reason.

Execution
---------
Entry at the open of the first session after the decision date (one-session
delay); exit at the close of the 252nd session counted from entry. Each
side pays the cost case (5, 10, or 25 basis points of traded notional; 10 is
the base case). The equal-weight control and SPY buy-and-hold use the same
dates and cost treatment. Cash earns nothing.
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import os
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Callable, Dict, List, Mapping, Optional, Sequence, Tuple

import pandas as pd

from src.backtesting.historical_tester import (
    DEFAULT_TOP_N,
    TickerAnalysis,
    ValuationResult,
    calculate_sector_median_price_to_intrinsic,
    score_ticker,
)
from src.dcf_model.dcf import extract_valuation_inputs, run_dcf_valuation
from src.fundamentals.issuer_manifest import IssuerValuationPolicy
from src.fundamentals.quarterly import assemble_quarterly_fundamentals
from src.fundamentals.repository import FundamentalsQuery
from src.fundamentals.selection import select_point_in_time
from src.fundamentals.time_policy import knowledge_cutoff_for_date
from src.fundamentals.types import FundamentalHistory
from src.fundamentals.valuation_integration import ValuationMarketObservations
from src.trading.alpaca_execution import (
    PIOTROSKI_MIN_F_SCORE,
    RSI_MAX_ENTRY_THRESHOLD,
    _altman_gate_reason,
    _blend_conviction_with_fcf_yield,
    calculate_inverse_beta_weights,
    select_top_picks,
    trend_filter_passes,
)
from src.valuation.altman_z import altman_z_from_statements
from src.valuation.piotroski import calculate_f_score_from_statements
from src.valuation.technical import DEFAULT_RSI_PERIOD, rsi_from_closes
from src.valuation_input import (
    SecValuationInputAdapter,
    ValuationInputLoader,
    ValuationInputProvenance,
    ValuationInputSource,
    YahooValuationInputAdapter,
)

logger = logging.getLogger(__name__)

PILOT_POLICY_VERSION = "sec-backtest-pilot-v1"
PILOT_RESULT_LABEL = "pipeline_validation"
PILOT_DISCLAIMER = (
    "Pipeline validation only. Four companies and one formation date cannot "
    "establish an investment edge; this result is not evidence of investment "
    "performance."
)

BETA_LOOKBACK_YEARS = 5
BETA_MIN_MONTHLY_OBSERVATIONS = 36
RSI_LOOKBACK_CALENDAR_DAYS = 92  # the live gate's "3mo" window
TREND_LOOKBACK_CALENDAR_DAYS = 366  # the live gate's "1y" window
_TTM_YEAR_AGO_TOLERANCE_DAYS = 20

# Point-in-time quality inputs read from the same SEC fact store.
_QUALITY_FLOW_REQUIRED = (
    "capital_expenditures",
    "net_income",
    "operating_cash_flow",
    "operating_income",
    "revenue",
)
_QUALITY_FLOW_OPTIONAL = ("cost_of_revenue", "gross_profit")
_QUALITY_BALANCE = (
    "cash_and_cash_equivalents",
    "current_assets",
    "current_debt",
    "current_liabilities",
    "long_term_debt",
    "retained_earnings",
    "shareholders_equity",
    "total_assets",
    "total_equity",
    "total_liabilities",
)
_QUALITY_COVER = ("common_shares_outstanding",)


@dataclass(frozen=True)
class PilotIssuer:
    ticker: str
    # The live engine groups by Yahoo's sector labels. Historical sector is
    # not point-in-time; these four classifications did not change.
    sector: str


PILOT_UNIVERSE: Tuple[PilotIssuer, ...] = (
    PilotIssuer("AAPL", "Technology"),
    PilotIssuer("MSFT", "Technology"),
    PilotIssuer("WMT", "Consumer Defensive"),
    PilotIssuer("CAT", "Industrials"),
)


@dataclass(frozen=True)
class PilotConfig:
    decision_date: date = date(2024, 9, 3)
    initial_capital: float = 100_000.0
    benchmark: str = "SPY"
    holding_sessions: int = 252
    base_cost_bps: int = 10
    cost_cases_bps: Tuple[int, ...] = (5, 10, 25)
    top_n: int = DEFAULT_TOP_N
    universe: Tuple[PilotIssuer, ...] = PILOT_UNIVERSE

    @property
    def knowledge_cutoff(self) -> datetime:
        return knowledge_cutoff_for_date(self.decision_date)

    def __post_init__(self) -> None:
        if self.base_cost_bps not in self.cost_cases_bps:
            raise ValueError("The base cost case must be one of the cost cases.")
        if self.holding_sessions <= 0 or self.initial_capital <= 0:
            raise ValueError("holding_sessions and initial_capital must be positive.")


class PriceProvider:
    """Daily prices. ``adjusted`` rows are split- and dividend-adjusted
    (total-return proxy); ``raw_close`` is the actual printed close."""

    def adjusted(self, symbol: str, start: date, end: date) -> pd.DataFrame:  # pragma: no cover - interface
        raise NotImplementedError

    def raw_close(self, symbol: str, on: date) -> Optional[float]:  # pragma: no cover - interface
        raise NotImplementedError


class YahooPriceProvider(PriceProvider):
    def __init__(self):
        import yfinance

        self._yf = yfinance
        self._cache: Dict[Tuple[str, bool], pd.DataFrame] = {}

    def _history(self, symbol: str, auto_adjust: bool) -> pd.DataFrame:
        key = (symbol, auto_adjust)
        if key not in self._cache:
            frame = self._yf.Ticker(symbol).history(period="max", auto_adjust=auto_adjust)
            frame.index = pd.DatetimeIndex([ts.date() for ts in frame.index])
            self._cache[key] = frame
        return self._cache[key]

    def adjusted(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        frame = self._history(symbol, True)
        return frame.loc[(frame.index >= pd.Timestamp(start)) & (frame.index <= pd.Timestamp(end)), ["Open", "Close"]]

    def raw_close(self, symbol: str, on: date) -> Optional[float]:
        frame = self._history(symbol, False)
        row = frame.loc[frame.index == pd.Timestamp(on)]
        if row.empty:
            return None
        split_adjusted_close = float(row["Close"].iloc[0])
        # yfinance back-adjusts for later splits; undo them to recover the print.
        splits = self._yf.Ticker(symbol).splits
        later = [float(ratio) for ts, ratio in splits.items() if ts.date() > on and ratio > 0]
        return split_adjusted_close * math.prod(later) if later else split_adjusted_close


# --------------------------------------------------------------------------
# Point-in-time signals
# --------------------------------------------------------------------------


def _closes_through(prices: PriceProvider, symbol: str, decision: date, lookback_days: int) -> pd.Series:
    frame = prices.adjusted(symbol, decision - timedelta(days=lookback_days), decision)
    return frame["Close"].loc[frame.index <= pd.Timestamp(decision)].dropna()


def estimate_beta(prices: PriceProvider, symbol: str, benchmark: str, decision: date) -> Optional[float]:
    """Five-year monthly beta versus the benchmark from closes on/before ``decision``."""

    lookback = BETA_LOOKBACK_YEARS * 366
    asset = _closes_through(prices, symbol, decision, lookback)
    market = _closes_through(prices, benchmark, decision, lookback)
    monthly = pd.concat(
        [asset.resample("ME").last(), market.resample("ME").last()], axis=1, keys=["asset", "market"]
    ).dropna()
    returns = monthly.pct_change().dropna()
    if len(returns) < BETA_MIN_MONTHLY_OBSERVATIONS:
        return None
    variance = float(returns["market"].var())
    if variance <= 0:
        return None
    return float(returns["asset"].cov(returns["market"]) / variance)


def rsi_at(prices: PriceProvider, symbol: str, decision: date) -> Optional[float]:
    return rsi_from_closes(
        _closes_through(prices, symbol, decision, RSI_LOOKBACK_CALENDAR_DAYS),
        period=DEFAULT_RSI_PERIOD,
        label=symbol,
    )


def trend_passes_at(prices: PriceProvider, symbol: str, decision: date) -> bool:
    return trend_filter_passes(
        _closes_through(prices, symbol, decision, TREND_LOOKBACK_CALENDAR_DAYS), label=symbol
    )


# --------------------------------------------------------------------------
# SEC-derived statements for quality metrics
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class SecQualityStatements:
    """Yahoo-shaped two-column (t, t-1) statements built only from SEC facts."""

    income_stmt: pd.DataFrame
    balance_sheet: pd.DataFrame
    cash_flow: pd.DataFrame
    cover_shares: Optional[float]
    ingestion_batch_ids: Tuple[str, ...]
    filing_accessions: Tuple[str, ...]
    approximations: Tuple[str, ...]


def _balance_value(period_facts, concept: str) -> Optional[float]:
    for fact in period_facts.facts:
        if fact.identity.concept == concept and not fact.identity.context.dimensions:
            return float(fact.value)
    return None


def _year_ago(items, key_end, latest_end: date):
    target = latest_end - timedelta(days=365)
    candidates = [item for item in items if abs((key_end(item) - target).days) <= _TTM_YEAR_AGO_TOLERANCE_DAYS]
    return min(candidates, key=lambda item: abs((key_end(item) - target).days)) if candidates else None


def build_sec_quality_statements(history: FundamentalHistory) -> Optional[SecQualityStatements]:
    """Two comparable periods, one year apart, from point-in-time SEC facts."""

    quarterly = assemble_quarterly_fundamentals(
        history,
        required_concepts=_QUALITY_FLOW_REQUIRED,
        optional_concepts=_QUALITY_FLOW_OPTIONAL,
    )
    if not quarterly.is_complete:
        return None
    ttm: Dict[str, Dict[date, float]] = {}
    for value in quarterly.ttm_values:
        ttm.setdefault(value.key.concept, {})[value.period_end] = float(value.value)
    latest_end = max(ttm["revenue"])
    prior_end = _year_ago(list(ttm["revenue"]), lambda end: end, latest_end)
    if prior_end is None:
        return None

    balances = [period for period in history.balance_sheet_periods if period.period.period_end <= latest_end]
    if not balances:
        return None
    balance_t = max(balances, key=lambda period: period.period.period_end)
    balance_t1 = _year_ago(balances, lambda period: period.period.period_end, balance_t.period.period_end)
    if balance_t1 is None:
        return None

    approximations: List[str] = ["fcf_growth_ttm_year_over_year"]
    columns = (pd.Timestamp(latest_end), pd.Timestamp(prior_end))
    income_rows: Dict[str, Dict[pd.Timestamp, float]] = {}
    for yahoo_row, concept in (
        ("Total Revenue", "revenue"),
        ("Operating Income", "operating_income"),
        ("Net Income", "net_income"),
        ("Gross Profit", "gross_profit"),
        ("Cost Of Revenue", "cost_of_revenue"),
    ):
        values = ttm.get(concept, {})
        if latest_end in values and prior_end in values:
            income_rows[yahoo_row] = {columns[0]: values[latest_end], columns[1]: values[prior_end]}
    cash_rows = {
        "Operating Cash Flow": {
            columns[0]: ttm["operating_cash_flow"][latest_end],
            columns[1]: ttm["operating_cash_flow"].get(prior_end, float("nan")),
        },
        # The legacy statement parsers expect cash outflows to be negative.
        "Capital Expenditure": {
            columns[0]: -ttm["capital_expenditures"][latest_end],
            columns[1]: -ttm["capital_expenditures"].get(prior_end, float("nan")),
        },
    }

    balance_columns = (pd.Timestamp(balance_t.period.period_end), pd.Timestamp(balance_t1.period.period_end))
    balance_rows: Dict[str, Dict[pd.Timestamp, float]] = {}
    for yahoo_row, concept in (
        ("Total Assets", "total_assets"),
        ("Current Assets", "current_assets"),
        ("Current Liabilities", "current_liabilities"),
        ("Long Term Debt", "long_term_debt"),
        ("Retained Earnings", "retained_earnings"),
        ("Stockholders Equity", "shareholders_equity"),
        ("Cash And Cash Equivalents", "cash_and_cash_equivalents"),
    ):
        row = {}
        for column, period in zip(balance_columns, (balance_t, balance_t1)):
            value = _balance_value(period, concept)
            if value is not None:
                row[column] = value
        if row:
            balance_rows[yahoo_row] = row
    liabilities = {}
    for column, period in zip(balance_columns, (balance_t, balance_t1)):
        reported = _balance_value(period, "total_liabilities")
        if reported is None:
            assets = _balance_value(period, "total_assets")
            equity = _balance_value(period, "total_equity") or _balance_value(period, "shareholders_equity")
            if assets is not None and equity is not None:
                reported = assets - equity
                if "total_liabilities_derived_as_assets_minus_equity" not in approximations:
                    approximations.append("total_liabilities_derived_as_assets_minus_equity")
        if reported is not None:
            liabilities[column] = reported
    if liabilities:
        balance_rows["Total Liabilities Net Minority Interest"] = liabilities

    # Piotroski's dilution factor compares share counts one year apart; the
    # SEC cover page is the point-in-time share source.
    cover = sorted(
        (fact for fact in history.cover_facts if fact.identity.concept == "common_shares_outstanding"),
        key=lambda fact: (fact.identity.period_end, fact.provenance.accepted_at),
    )
    cover_shares = float(cover[-1].value) if cover else None
    if cover:
        cover_t1 = _year_ago(cover, lambda fact: fact.identity.period_end, cover[-1].identity.period_end)
        shares_row = {balance_columns[0]: float(cover[-1].value)}
        if cover_t1 is not None:
            shares_row[balance_columns[1]] = float(cover_t1.value)
        balance_rows["Share Issued"] = shares_row
        approximations.append("piotroski_shares_from_cover_page")

    facts = [
        fact
        for periods in (history.income_statement_periods, history.balance_sheet_periods, history.cash_flow_periods)
        for period in periods
        for fact in period.facts
    ] + list(history.cover_facts)
    return SecQualityStatements(
        income_stmt=pd.DataFrame(income_rows).T,
        balance_sheet=pd.DataFrame(balance_rows).T,
        cash_flow=pd.DataFrame(cash_rows).T,
        cover_shares=cover_shares,
        ingestion_batch_ids=tuple(sorted({fact.lineage.ingestion_batch_id for fact in facts})),
        filing_accessions=tuple(sorted({fact.provenance.accession_number for fact in facts})),
        approximations=tuple(approximations),
    )


def describe_quality_gap(history: FundamentalHistory) -> str:
    """Name what ``build_sec_quality_statements`` could not find."""

    quarterly = assemble_quarterly_fundamentals(
        history, required_concepts=_QUALITY_FLOW_REQUIRED, optional_concepts=_QUALITY_FLOW_OPTIONAL
    )
    if not quarterly.is_complete:
        issue = quarterly.issues[0]
        return f"{issue.concept or 'flows'} ({issue.code.value})"
    return "no comparable period one year earlier"


def load_sec_quality_history(
    repository, policy: IssuerValuationPolicy, knowledge_cutoff: datetime, data_vintage_cutoff: datetime
) -> FundamentalHistory:
    facts = repository.get_facts(
        FundamentalsQuery(
            cik=policy.cik,
            knowledge_cutoff=knowledge_cutoff,
            data_vintage_cutoff=data_vintage_cutoff,
            concepts=_QUALITY_FLOW_REQUIRED + _QUALITY_FLOW_OPTIONAL + _QUALITY_BALANCE + _QUALITY_COVER,
            source_adapter=policy.source_adapter,
            concept_map_version=policy.concept_map_version,
            fiscal_calendar_version=policy.fiscal_calendar_version,
            max_periods_per_statement=64,
            supplemental_source_adapters=policy.supplemental_source_adapters,
        )
    )
    return select_point_in_time(facts, knowledge_cutoff, cik=policy.cik)


# --------------------------------------------------------------------------
# Selection at the decision date
# --------------------------------------------------------------------------


@dataclass
class IssuerDecision:
    ticker: str
    sector: str
    status: str  # "refused" | "rejected_by_strategy" | "selected"
    reason: Optional[str] = None
    price: Optional[float] = None
    intrinsic_value: Optional[float] = None
    price_to_intrinsic: Optional[float] = None
    sector_median_price_to_intrinsic: Optional[float] = None
    beta: Optional[float] = None
    altman_z_score: Optional[float] = None
    piotroski_f_score: Optional[int] = None
    rsi: Optional[float] = None
    trend_passes: Optional[bool] = None
    fcf_yield: Optional[float] = None
    fcf_growth_rate: Optional[float] = None
    roic: Optional[float] = None
    conviction_score: Optional[float] = None
    target_weight: float = 0.0
    approximations: List[str] = field(default_factory=list)
    sec_provenance: Optional[dict] = None


def _provenance_dict(provenance: ValuationInputProvenance, quality: Optional[SecQualityStatements]) -> dict:
    batches = set(provenance.ingestion_batch_ids) | set(quality.ingestion_batch_ids if quality else ())
    accessions = set(provenance.filing_accessions) | set(quality.filing_accessions if quality else ())
    return {
        "source": provenance.source.value,
        "source_selection_reason": provenance.source_selection_reason,
        "knowledge_cutoff": provenance.knowledge_cutoff.isoformat(),
        "data_vintage_cutoff": provenance.data_vintage_cutoff.isoformat(),
        "statement_period_start": provenance.statement_period_start.isoformat()
        if provenance.statement_period_start
        else None,
        "statement_period_end": provenance.statement_period_end.isoformat(),
        "policy_version": provenance.policy_version,
        "source_adapter": provenance.source_adapter,
        "concept_map_version": provenance.concept_map_version,
        "fiscal_calendar_version": provenance.fiscal_calendar_version,
        "ingestion_batch_ids": sorted(batches),
        "filing_accessions": sorted(accessions),
    }


def decide_portfolio(
    config: PilotConfig,
    *,
    loader: ValuationInputLoader,
    repository,
    manifest_lookup: Callable[[str], Optional[IssuerValuationPolicy]],
    prices: PriceProvider,
    data_vintage_cutoff: datetime,
) -> Tuple[List[IssuerDecision], Dict[str, float]]:
    """Value, score, gate, select, and size the universe at the decision cutoff."""

    cutoff = config.knowledge_cutoff
    decision = config.decision_date
    decisions: Dict[str, IssuerDecision] = {}
    valuations: List[ValuationResult] = []

    for issuer in config.universe:
        record = IssuerDecision(ticker=issuer.ticker, sector=issuer.sector, status="refused")
        decisions[issuer.ticker] = record
        record.approximations.append("sector_current_classification")
        result = loader.load(issuer.ticker, cutoff, data_vintage_cutoff, ValuationInputSource.SEC)
        if not result.is_complete:
            record.reason = "SEC input refused: " + "; ".join(
                f"{issue.code.value}: {issue.message}" for issue in result.issues
            )
            continue
        valuation_input = result.valuation_input
        if valuation_input.provenance.source is not ValuationInputSource.SEC:
            record.reason = "Loader returned a non-SEC source; refusing."
            continue
        policy = manifest_lookup(issuer.ticker)
        try:
            history = load_sec_quality_history(repository, policy, cutoff, data_vintage_cutoff)
            quality = build_sec_quality_statements(history)
        except (ValueError, RuntimeError) as error:
            quality = None
            record.reason = f"SEC quality history unavailable: {error}"
        record.sec_provenance = _provenance_dict(valuation_input.provenance, quality)
        if quality is None:
            record.reason = record.reason or (
                "SEC quality statements are incomplete at the cutoff: " + describe_quality_gap(history)
            )
            continue
        record.approximations.extend(quality.approximations)

        financial_data = dict(valuation_input.financial_data)
        assumptions = valuation_input.default_assumptions
        try:
            dcf = run_dcf_valuation(financial_data, assumptions)
        except ValueError as error:
            record.reason = f"DCF valuation failed: {error}"
            continue
        intrinsic = float(dcf["intrinsic_value_per_share"])
        price = float(financial_data["current_price"])
        if not math.isfinite(intrinsic) or intrinsic <= 0:
            record.reason = "Intrinsic value is not positive and finite."
            continue
        inputs = extract_valuation_inputs(financial_data)
        altman = altman_z_from_statements(
            quality.balance_sheet,
            quality.income_stmt,
            current_price=price,
            shares_outstanding=float(financial_data["shares_outstanding"]),
            label=issuer.ticker,
        )
        valuations.append(
            ValuationResult(
                ticker=issuer.ticker,
                as_of_date=decision.isoformat(),
                sector=issuer.sector,
                historical_price=price,
                historical_intrinsic_value=intrinsic,
                price_to_intrinsic=price / intrinsic,
                wacc=dcf["wacc"],
                beta=float(financial_data["beta"]),
                altman_z_score=altman,
                fcf_yield=dcf.get("fcf_yield"),
                income_stmt=quality.income_stmt,
                balance_sheet=quality.balance_sheet,
                cash_flow=quality.cash_flow,
                tax_rate=assumptions.tax_rate,
                total_debt=inputs["total_debt"],
                cash_and_equivalents=inputs["cash_and_equivalents"],
                approximations=list(record.approximations),
            )
        )
        record.status = "rejected_by_strategy"
        record.reason = None
        record.price = price
        record.intrinsic_value = intrinsic
        record.price_to_intrinsic = price / intrinsic
        record.beta = float(financial_data["beta"])
        record.altman_z_score = altman
        record.fcf_yield = dcf.get("fcf_yield")
        # Every price/quality signal is recorded for audit even when an
        # earlier gate decides the outcome; the gates still apply in live order.
        record.trend_passes = trend_passes_at(prices, issuer.ticker, decision)
        record.piotroski_f_score = calculate_f_score_from_statements(
            quality.income_stmt, quality.balance_sheet, quality.cash_flow
        )
        record.rsi = rsi_at(prices, issuer.ticker, decision)

    sector_medians = calculate_sector_median_price_to_intrinsic(valuations)
    analyses: List[TickerAnalysis] = []
    for valuation in valuations:
        analysis = score_ticker(valuation, sector_medians)
        record = decisions[valuation.ticker]
        record.sector_median_price_to_intrinsic = sector_medians.get(valuation.sector)
        record.fcf_growth_rate = analysis.fcf_growth_rate
        record.roic = analysis.roic
        if analysis.is_valid:
            gate = _point_in_time_gate_failure(analysis, valuation, record)
            if gate is not None:
                analysis = replace(analysis, conviction_score=None, skip_reason=gate)
        analysis = _blend_conviction_with_fcf_yield(analysis)
        record.conviction_score = analysis.conviction_score
        if not analysis.is_valid:
            record.reason = analysis.skip_reason
        analyses.append(analysis)

    picks = select_top_picks(analyses, config.top_n)
    refused = [record.ticker for record in decisions.values() if record.status == "refused"]
    scaled = strategy_weights_with_refusals(
        calculate_inverse_beta_weights(picks), refused_count=len(refused), universe_size=len(config.universe)
    )
    for ticker, weight in scaled.items():
        decisions[ticker].status = "selected"
        decisions[ticker].reason = None
        decisions[ticker].target_weight = weight
    return [decisions[issuer.ticker] for issuer in config.universe], scaled


def strategy_weights_with_refusals(
    weights: Mapping[str, float], *, refused_count: int, universe_size: int
) -> Dict[str, float]:
    """Reserve each refused company's 1/N share as cash without resizing the
    live strategy's positions.

    The live weights stand as sized (caps included) whenever the cash they
    already leave covers the reserve. Only if they would invest into the
    reserve are they scaled down, pro rata and exactly enough to leave it
    untouched. Refused capital is never redistributed.
    """

    available_fraction = (universe_size - refused_count) / universe_size
    invested = sum(weights.values())
    if invested <= available_fraction + 1e-12:
        return dict(weights)
    scale = available_fraction / invested
    return {ticker: weight * scale for ticker, weight in weights.items()}


def equal_weight_control(tickers: Sequence[str], refused: Sequence[str]) -> Dict[str, float]:
    """1/N per company; a refused company's 1/N stays in cash."""

    return {ticker: 1.0 / len(tickers) for ticker in tickers if ticker not in set(refused)}


def _point_in_time_gate_failure(
    analysis: TickerAnalysis,
    valuation: ValuationResult,
    record: IssuerDecision,
) -> Optional[str]:
    """The live engine's five entry gates, in live order, at the decision date."""

    ratio = analysis.price_to_intrinsic
    if ratio is None or not math.isfinite(ratio) or ratio >= 1.0:
        return (
            "Failed absolute fair-value entry gate "
            f"(price/intrinsic={ratio:.3f}, required < 1.000)."
        )
    altman_reason = _altman_gate_reason(analysis.sector, valuation.altman_z_score)
    if altman_reason is not None:
        return altman_reason
    if not record.trend_passes:
        return "Failed 200-SMA trend check (value trap protection)."
    if record.piotroski_f_score < PIOTROSKI_MIN_F_SCORE:
        return (
            f"Failed Piotroski F-Score quality check (F-Score={record.piotroski_f_score}, "
            f"minimum={PIOTROSKI_MIN_F_SCORE})."
        )
    if record.rsi is None or record.rsi >= RSI_MAX_ENTRY_THRESHOLD:
        display = f"{record.rsi:.1f}" if record.rsi is not None else "unavailable"
        return f"RSI at {display}. Waiting for micro-dip < {RSI_MAX_ENTRY_THRESHOLD}."
    return None


# --------------------------------------------------------------------------
# Execution simulation
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class ExecutionWindow:
    entry_date: date
    exit_date: date
    sessions: Tuple[date, ...]


def execution_window(prices: PriceProvider, config: PilotConfig) -> ExecutionWindow:
    """Benchmark sessions from the first session after the decision date."""

    frame = prices.adjusted(
        config.benchmark,
        config.decision_date + timedelta(days=1),
        config.decision_date + timedelta(days=int(config.holding_sessions * 1.6) + 30),
    )
    sessions = tuple(ts.date() for ts in frame.index if ts.date() > config.decision_date)
    if len(sessions) < config.holding_sessions:
        raise ValueError("Not enough completed sessions after the decision date.")
    window = sessions[: config.holding_sessions]
    return ExecutionWindow(entry_date=window[0], exit_date=window[-1], sessions=window)


@dataclass(frozen=True)
class CurveResult:
    name: str
    cost_bps: int
    weights: Mapping[str, float]
    unallocated_cash_fraction: float
    daily_values: Tuple[Tuple[str, float], ...]
    ending_value: float
    net_return: float
    costs_paid: float
    max_drawdown: float
    annualized_volatility: float


def simulate_curve(
    name: str,
    weights: Mapping[str, float],
    *,
    prices: PriceProvider,
    window: ExecutionWindow,
    capital: float,
    cost_bps: int,
) -> CurveResult:
    """Buy at the entry open, mark at each close, sell at the exit close."""

    rate = cost_bps / 10_000.0
    invested = {ticker: weight for ticker, weight in weights.items() if weight > 0}
    cash = capital * (1.0 - sum(invested.values()))
    closes: Dict[str, pd.Series] = {}
    shares: Dict[str, float] = {}
    costs = 0.0
    for ticker, weight in invested.items():
        frame = prices.adjusted(ticker, window.entry_date, window.exit_date)
        frame = frame.loc[[pd.Timestamp(day) for day in window.sessions if pd.Timestamp(day) in frame.index]]
        if len(frame) != len(window.sessions):
            raise ValueError(f"{ticker} is missing prices on execution sessions.")
        notional = capital * weight
        costs += notional * rate
        shares[ticker] = notional * (1.0 - rate) / float(frame["Open"].iloc[0])
        closes[ticker] = frame["Close"]

    values: List[Tuple[str, float]] = []
    for index, day in enumerate(window.sessions):
        marked = cash + sum(shares[t] * float(closes[t].iloc[index]) for t in shares)
        if index == len(window.sessions) - 1:
            exit_notional = sum(shares[t] * float(closes[t].iloc[index]) for t in shares)
            costs += exit_notional * rate
            marked = cash + exit_notional * (1.0 - rate)
        values.append((day.isoformat(), marked))

    series = [capital] + [value for _, value in values]
    peak, drawdown = series[0], 0.0
    for value in series:
        peak = max(peak, value)
        drawdown = min(drawdown, value / peak - 1.0)
    returns = pd.Series(series).pct_change().dropna()
    volatility = float(returns.std(ddof=1) * math.sqrt(252)) if len(returns) > 1 else 0.0
    ending = values[-1][1]
    return CurveResult(
        name=name,
        cost_bps=cost_bps,
        weights=dict(invested),
        unallocated_cash_fraction=1.0 - sum(invested.values()),
        daily_values=tuple(values),
        ending_value=ending,
        net_return=ending / capital - 1.0,
        costs_paid=costs,
        max_drawdown=drawdown,
        annualized_volatility=volatility,
    )


# --------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------


def market_observation_loader_for(
    config: PilotConfig, prices: PriceProvider, sector_by_ticker: Mapping[str, str], repository, data_vintage_cutoff: datetime
):
    """Point-in-time market inputs for the SEC adapter at the decision date."""

    def load(ticker: str, policy: IssuerValuationPolicy, knowledge_cutoff: datetime) -> ValuationMarketObservations:
        decision = config.decision_date
        price = prices.raw_close(ticker, decision)
        beta = estimate_beta(prices, ticker, config.benchmark, decision)
        tnx = prices.raw_close("^TNX", decision)
        history = load_sec_quality_history(repository, policy, knowledge_cutoff, data_vintage_cutoff)
        cover = sorted(
            (fact for fact in history.cover_facts if fact.identity.concept == "common_shares_outstanding"),
            key=lambda fact: (fact.identity.period_end, fact.provenance.accepted_at),
        )
        if price is None or beta is None or tnx is None or not cover:
            raise ValueError("Point-in-time market observations are incomplete.")
        return ValuationMarketObservations(
            cik=policy.cik,
            ticker=ticker,
            observed_at=knowledge_cutoff,
            current_price=Decimal(str(price)),
            current_shares_outstanding=cover[-1].value,
            levered_beta=Decimal(str(round(beta, 6))),
            sector=sector_by_ticker[ticker],
            risk_free_rate=Decimal(str(round(tnx / 100.0, 6))),
            equity_source_adapter="yahoo_close_at_decision+sec_cover_shares",
            risk_free_rate_source_adapter="yahoo_tnx_close_at_decision",
        )

    return load


def run_pilot(
    config: PilotConfig,
    *,
    repository,
    prices: PriceProvider,
    manifest_lookup: Callable[[str], Optional[IssuerValuationPolicy]],
    data_vintage_cutoff: datetime,
) -> dict:
    sector_by_ticker = {issuer.ticker: issuer.sector for issuer in config.universe}
    loader = ValuationInputLoader(
        # Never consulted: the pilot only requests source=sec.
        yahoo_adapter=YahooValuationInputAdapter(fetcher=_refuse_yahoo),
        sec_adapter=SecValuationInputAdapter(
            repository=repository,
            market_observation_loader=market_observation_loader_for(
                config, prices, sector_by_ticker, repository, data_vintage_cutoff
            ),
        ),
    )
    decisions, strategy_weights = decide_portfolio(
        config,
        loader=loader,
        repository=repository,
        manifest_lookup=manifest_lookup,
        prices=prices,
        data_vintage_cutoff=data_vintage_cutoff,
    )
    window = execution_window(prices, config)
    control_weights = equal_weight_control(
        [issuer.ticker for issuer in config.universe],
        [record.ticker for record in decisions if record.status == "refused"],
    )
    curves = {"strategy": strategy_weights, "equal_weight_control": control_weights, "spy_buy_and_hold": {config.benchmark: 1.0}}

    results = {}
    for cost in config.cost_cases_bps:
        results[cost] = {
            name: simulate_curve(name, weights, prices=prices, window=window, capital=config.initial_capital, cost_bps=cost)
            for name, weights in curves.items()
        }
    gross = {
        name: simulate_curve(name, weights, prices=prices, window=window, capital=config.initial_capital, cost_bps=0)
        for name, weights in curves.items()
    }
    return _report(config, decisions, window, results, gross, data_vintage_cutoff)


def _refuse_yahoo(ticker: str) -> dict:
    raise ValueError("Yahoo statements are prohibited in the SEC backtest pilot.")


def _curve_summary(curve: CurveResult, gross: CurveResult, benchmark: CurveResult) -> dict:
    return {
        "weights": {ticker: round(weight, 6) for ticker, weight in curve.weights.items()},
        "unallocated_cash_fraction": round(curve.unallocated_cash_fraction, 6),
        "ending_value": round(curve.ending_value, 2),
        "gross_return": round(gross.net_return, 6),
        "net_return": round(curve.net_return, 6),
        "excess_return_vs_benchmark": round(curve.net_return - benchmark.net_return, 6),
        "costs_paid": round(curve.costs_paid, 2),
        "max_drawdown": round(curve.max_drawdown, 6),
        "annualized_volatility": round(curve.annualized_volatility, 6),
    }


def _report(config, decisions, window, results, gross, data_vintage_cutoff) -> dict:
    base = results[config.base_cost_bps]
    return {
        "label": PILOT_RESULT_LABEL,
        "disclaimer": PILOT_DISCLAIMER,
        "policy_version": PILOT_POLICY_VERSION,
        "decision_date": config.decision_date.isoformat(),
        "knowledge_cutoff": config.knowledge_cutoff.isoformat(),
        "data_vintage_cutoff": data_vintage_cutoff.isoformat(),
        "initial_capital": config.initial_capital,
        "benchmark": config.benchmark,
        "entry_date": window.entry_date.isoformat(),
        "entry_price": "open",
        "exit_date": window.exit_date.isoformat(),
        "exit_price": "close",
        "holding_sessions": config.holding_sessions,
        "execution_delay_sessions": 1,
        "base_cost_bps": config.base_cost_bps,
        "universe": [issuer.ticker for issuer in config.universe],
        "strategy_outcome": (
            "no_eligible_candidates"
            if not any(record.status == "selected" for record in decisions)
            else "positions_selected"
        ),
        "rejected_issuers": [
            {"ticker": record.ticker, "reason": record.reason, "unallocated_capital_fraction": round(1.0 / len(config.universe), 6)}
            for record in decisions
            if record.status == "refused"
        ],
        "issuers": [
            {key: (round(value, 6) if isinstance(value, float) else value) for key, value in vars(record).items()}
            for record in decisions
        ],
        "cost_cases": {
            str(cost): {
                name: _curve_summary(curve, gross[name], curves["spy_buy_and_hold"])
                for name, curve in curves.items()
            }
            for cost, curves in results.items()
        },
        "curves_base_cost": {
            name: [[day, round(value, 2)] for day, value in curve.daily_values] for name, curve in base.items()
        },
        "portfolio_approximations": [
            "cash_earns_zero",
            "prices_yahoo_split_and_dividend_adjusted_total_return_proxy",
            "beta_estimated_from_five_year_monthly_returns_vs_spy",
            "risk_free_rate_from_tnx_close_at_decision",
            "sector_current_classification",
        ],
    }


def run_pinned_pilot(
    config: PilotConfig,
    *,
    repository,
    live_prices: PriceProvider,
    manifest_lookup: Callable[[str], Optional[IssuerValuationPolicy]],
    data_vintage_cutoff: datetime,
    store,
    load,
    github_run_id: Optional[str],
    prior_run_ids: Sequence[str] = (),
) -> dict:
    """Run once against live prices while capturing them, persist the capture,
    then replay offline from the stored copy and require identical output."""

    from src.backtesting.price_snapshot import (
        RecordingPriceProvider,
        SnapshotIntegrityError,
        SnapshotPriceProvider,
        snapshot_sha256,
    )

    recording = RecordingPriceProvider(live_prices)
    report = run_pilot(
        config,
        repository=repository,
        prices=recording,
        manifest_lookup=manifest_lookup,
        data_vintage_cutoff=data_vintage_cutoff,
    )
    snapshot = recording.snapshot(source="yahoo_finance_via_yfinance", captured_at=datetime.now(timezone.utc))
    digest = store(snapshot)
    stored = load(digest)
    if snapshot_sha256(stored) != digest:
        raise SnapshotIntegrityError("Stored snapshot does not match its checksum.")
    replay = run_pilot(
        config,
        repository=repository,
        prices=SnapshotPriceProvider(stored),
        manifest_lookup=manifest_lookup,
        data_vintage_cutoff=data_vintage_cutoff,
    )
    if json.dumps(replay, sort_keys=True) != json.dumps(report, sort_keys=True):
        raise SnapshotIntegrityError("Offline replay from the stored snapshot did not reproduce the run exactly.")
    return attach_pinned_metadata(report, stored, digest, github_run_id=github_run_id, prior_run_ids=prior_run_ids)


def attach_pinned_metadata(
    report: dict, snapshot: dict, digest: str, *, github_run_id: Optional[str], prior_run_ids: Sequence[str]
) -> dict:
    report["price_snapshot"] = {
        "sha256": digest,
        "format_version": snapshot["format_version"],
        "source": snapshot["source"],
        "captured_at": snapshot["captured_at"],
        "captured_in_run": github_run_id,
        "storage": "private postgres table backtest_price_snapshots (not redistributed)",
        "symbols": sorted(snapshot["symbols"]),
        "adjusted_rows": sum(len(item["adjusted_open_close"]) for item in snapshot["symbols"].values()),
        "offline_replay_identical": True,
    }
    report["audit_trail"] = {
        "run_id": github_run_id,
        "prior_provisional_run_ids": list(prior_run_ids),
    }
    return report


def replay_pinned_run(
    config: PilotConfig,
    *,
    repository,
    snapshot: dict,
    digest: str,
    manifest_lookup: Callable[[str], Optional[IssuerValuationPolicy]],
    data_vintage_cutoff: datetime,
    original_run_id: str,
    prior_run_ids: Sequence[str] = (),
) -> dict:
    """Rebuild a pinned run's full record offline from its stored snapshot and
    its recorded data-vintage cutoff; the result is that run's record exactly."""

    from src.backtesting.price_snapshot import SnapshotIntegrityError, SnapshotPriceProvider, snapshot_sha256

    if snapshot_sha256(snapshot) != digest:
        raise SnapshotIntegrityError("Stored snapshot does not match its checksum.")
    report = run_pilot(
        config,
        repository=repository,
        prices=SnapshotPriceProvider(snapshot),
        manifest_lookup=manifest_lookup,
        data_vintage_cutoff=data_vintage_cutoff,
    )
    return attach_pinned_metadata(report, snapshot, digest, github_run_id=original_run_id, prior_run_ids=prior_run_ids)


def main(argv: Optional[Sequence[str]] = None) -> int:
    from src.backtesting.price_snapshot import (
        load_snapshot,
        public_record,
        store_private_record,
        store_snapshot,
    )
    from src.fundamentals.issuer_manifest import issuer_policy_for
    from src.fundamentals.store import PostgresFundamentalsRepository

    parser = argparse.ArgumentParser(description="Run the SEC-only $100,000 backtest pilot with pinned prices.")
    parser.add_argument("--output", required=True, help="path for the reduced public JSON record")
    parser.add_argument("--github-run-id", default=None)
    parser.add_argument("--prior-run-id", action="append", default=[], help="earlier provisional run to cite")
    parser.add_argument("--replay-snapshot", default=None, help="archive mode: stored snapshot SHA-256 to replay")
    parser.add_argument("--replay-data-vintage-cutoff", default=None, help="archive mode: the run's recorded cutoff")
    parser.add_argument("--replay-of-run", default=None, help="archive mode: the run whose record is rebuilt")
    args = parser.parse_args(argv)
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        print(json.dumps({"status": "failed", "message": "DATABASE_URL must be set."}))
        return 1
    run_ids = args.prior_run_id + [value for value in (args.github_run_id, args.replay_of_run) if value]
    if any(not run.isdigit() for run in run_ids):
        print(json.dumps({"status": "failed", "message": "Run IDs must be numeric."}))
        return 1
    replay_args = (args.replay_snapshot, args.replay_data_vintage_cutoff, args.replay_of_run)
    if any(replay_args) and not all(replay_args):
        print(json.dumps({"status": "failed", "message": "Archive mode needs snapshot, cutoff, and run."}))
        return 1
    repository = PostgresFundamentalsRepository(database_url=database_url)

    if args.replay_snapshot:
        full = replay_pinned_run(
            PilotConfig(),
            repository=repository,
            snapshot=load_snapshot(args.replay_snapshot, database_url=database_url),
            digest=args.replay_snapshot,
            manifest_lookup=issuer_policy_for,
            data_vintage_cutoff=datetime.fromisoformat(args.replay_data_vintage_cutoff),
            original_run_id=args.replay_of_run,
            prior_run_ids=args.prior_run_id,
        )
        record_run = args.replay_of_run
    else:
        full = run_pinned_pilot(
            PilotConfig(),
            repository=repository,
            live_prices=YahooPriceProvider(),
            manifest_lookup=issuer_policy_for,
            data_vintage_cutoff=datetime.now(timezone.utc),
            store=lambda snapshot: store_snapshot(
                snapshot,
                database_url=database_url,
                github_run_id=args.github_run_id,
                pilot_policy_version=PILOT_POLICY_VERSION,
            ),
            load=lambda digest: load_snapshot(digest, database_url=database_url),
            github_run_id=args.github_run_id,
            prior_run_ids=args.prior_run_id,
        )
        record_run = args.github_run_id
    private_digest = store_private_record(
        full,
        snapshot_digest=full["price_snapshot"]["sha256"],
        database_url=database_url,
        github_run_id=record_run,
    )
    reduced = public_record(full, private_record_sha256=private_digest)
    with open(args.output, "w", encoding="utf-8") as handle:
        json.dump(reduced, handle, indent=2, sort_keys=True)
        handle.write("\n")
    print(
        json.dumps(
            {
                "status": "complete",
                "mode": "archive_replay" if args.replay_snapshot else "pinned_run",
                "executed_in_run": args.github_run_id,
                "record_of_run": record_run,
                "label": reduced["label"],
                "price_snapshot_sha256": reduced["price_snapshot"]["sha256"],
                "private_full_record_sha256": private_digest,
                "rejected_issuers": reduced["rejected_issuers"],
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
