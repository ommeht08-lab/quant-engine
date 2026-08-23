"""
Autonomous paper trading execution for the sector-relative DCF Conviction
Score strategy.

Runs the exact same two-pass sector-relative DCF scan as the backtester
(`src.backtesting.historical_tester`) — but as of *today* instead of a
past date — over the 100-ticker universe, takes the Top 10 by Conviction
Score, and rebalances an Alpaca account to hold each at a dynamic,
risk-adjusted target weight: liquidating anything held that fell out of
the Top 10, then buying the remaining top picks up to their target
weight.

Entry gates are applied AFTER valuation, never before the sector benchmark
--------------------------------------------------------------------------
Every ticker in the universe is valued via `compute_valuation` (Pass 1)
UNCONDITIONALLY — there is no pre-valuation rejection anymore. Sector
medians (`calculate_sector_median_price_to_intrinsic`) are computed from
that full, unscreened, successfully-valued set. Only THEN are four entry
gates applied, purely to decide Top-N *eligibility*, never to alter the
sector benchmark other tickers get compared against:

    - Altman Z-Score distress filter (`src.valuation.altman_z.calculate_altman_z`,
      threshold 1.8) — a credit-health gate. The original manufacturing-
      era formula isn't sector-appropriate for Financial Services,
      Utilities, or Real Estate names (see `ALTMAN_Z_EXCLUDED_SECTORS`),
      so it's marked not-applicable (neither pass nor fail) for those
      sectors rather than misapplied.
    - 200-day SMA trend filter ("value trap protection",
      `check_trend_filter`) — a stock trading meaningfully below its own
      long-term trend is more likely a falling knife than a bargain.
      Requires at least TREND_SMA_WINDOW_DAYS (200) valid closes; fewer
      fails safe rather than silently averaging a shorter window.
    - Piotroski F-Score (`src.valuation.piotroski.calculate_f_score`,
      minimum 5/9) — a fundamental quality/financial-health check.
    - 14-day RSI, Wilder's Smoothing (`src.valuation.technical.calculate_rsi`)
      — only enters on a technical micro-dip (RSI < RSI_MAX_ENTRY_THRESHOLD).

All four fail safe (reject) on missing/unusable data. Valuing every
ticker regardless of gate outcome costs more DCF calls per run than the
old gate-before-value order (absorbed by the existing Redis cache), but
is what a sector benchmark free of technical/quality-state bias requires:
before this, a ticker's own RSI or trend state could change which
tickers contributed to (and therefore the value of) the median every
OTHER ticker in its sector was compared against.

FCF Yield-blended Conviction Score
---------------------------------------
For tickers that pass every gate, the DCF-derived Conviction Score is
blended with a normalized FCF Yield ((OCF - CapEx) / Enterprise Value,
`src.dcf_model.dcf.calculate_fcf_yield`) — 60% original Conviction Score,
40% normalized FCF Yield (a 10% yield maps to a 1.0 multiplier, capped at
2.0) — via `_blend_conviction_with_fcf_yield`. This only affects live/
paper trading ranking and sizing; the backtester keeps the unblended score.

Position sizing: Inverse Volatility Weighting on beta, with risk caps
-------------------------------------------------------------------------
Rather than an equal weight per Top-N ticker, each pick's raw target
weight is `(1 / max(beta, 0.5)) / sum(1 / max(beta, 0.5) for all picks)`
— so lower-beta (less volatile) tickers receive proportionally more
equity than higher-beta ones. Beta is floored at 0.5 before inverting so
an artificially low-beta ticker can't dominate the allocation.

Two institutional risk caps are then applied on top of that raw weight
(`calculate_inverse_beta_weights`):
    - MAX_POSITION_WEIGHT (15%): no single position may exceed this
      share of equity.
    - MAX_SECTOR_WEIGHT (25%): no single GICS sector's combined weight
      (summed across every Top-N pick in that sector) may exceed this.
Both caps are enforced by proportionally redistributing the excess
weight to positions/sectors still under their own cap, iterated until
stable. If the candidate set can't absorb the excess (e.g. every pick is
in the same, already-capped sector), the shortfall is simply left
unallocated — i.e. that capital sits in cash — rather than breaching
either cap.

Rebalance drift threshold (churn control) — now bidirectional
--------------------------------------------------------------
`rebalance_target_positions` submits an order for an already-held Top-N
pick whenever its current weight (current position value / equity) has
drifted from its freshly-calculated target weight by more than
DRIFT_THRESHOLD (3 percentage points) — in EITHER direction: an
underweight pick is bought up toward target, an overweight pick is sold
down toward target (previously buy-only; an overweight target position
was simply left alone, which meant the position/sector caps in
`calculate_inverse_beta_weights` were never actually enforced against a
position that grew past its cap through price appreciation). This
avoids generating a stream of tiny, cost-and-slippage-only rebalance
trades every run for positions already close enough to target. Full
liquidations (a ticker leaving the Top N, or profit-taking — see below)
are never subject to this threshold; they always execute.

Profit-taking exits
-----------------------
Independent of whether a ticker is still a Top-N pick this run, any
currently held EQUITY position whose market price has risen to or above
its own DCF intrinsic value is liquidated (`liquidate_non_target_positions`)
— once the market price catches up to fair value, it's no longer a
margin-of-safety opportunity, regardless of how it still ranks by
Conviction Score. A ticker exited this way is excluded from this run's
buy/rebalance pass so it isn't immediately bought back. Any held OPTION
position (i.e. a previously-bought SPY VaR hedge) is explicitly excluded
from this liquidation sweep — it isn't a Top-N equity target and never
was meant to be evaluated as one; letting it fall through the "fell out
of Top N" branch used to mean a hedge bought on one run would simply be
sold on the very next one.

Order confirmation: submit → poll → log only actual fills
-------------------------------------------------------------
No order is assumed filled just because `submit_order`/`close_position`
returned — both are polled via `_await_order_resolution` until Alpaca
reports a resolved status or a bounded number of polls elapses. Only the
ACTUAL confirmed filled quantity/price is ever written to `trade_logs`;
a still-pending, partially-filled, canceled, or rejected order is logged
clearly (and, for partial fills, its real partial quantity) rather than
silently assumed to have executed in full at an estimated price. Before
submitting anything, `_open_order_symbols` checks for an already-open
order on that symbol (e.g. left over from a prior run that hasn't
resolved) and skips a duplicate submission rather than stacking one.

Reusing today's date through the point-in-time machinery
-----------------------------------------------------------
`compute_valuation` / `score_ticker` were built for backtesting a past
`as_of_date`, filtering out any statement period after it. Passing in
*today's* date is not a special case — it's the same function with the
same contract, and since nothing can be dated after today, the point-in-
time filter is simply a no-op: every currently available statement,
price, and share count passes through untouched. This lets today's live
scan reuse the backtester's Pass 1 (`compute_valuation`) / sector-median
(`calculate_sector_median_price_to_intrinsic`) / Pass 2 (`score_ticker`)
pipeline verbatim, with no duplicated valuation logic.

As a side effect, each run also refreshes the live API's sector-median
cache (`src.api.sector_medians`) with today's freshly computed medians,
so `GET /api/evaluate/{ticker}` stays reasonably current between runs
too.

Trade telemetry
----------------
Every successfully submitted order (a liquidation or a Top-N buy) is
logged to a Postgres `trade_logs` table via `src.utils.db.log_trade`,
recording the ticker, action, quantity, execution price, and the WACC /
beta / Conviction Score behind the decision. This is best-effort
telemetry, not a source of truth: logging failures (e.g. `DATABASE_URL`
unset, database unreachable) are caught and logged as warnings — they
never abort or roll back an already-submitted trade. Dry runs never log
anything, since no orders are actually submitted.

Safety
------
This project is **paper-trading only, by design, with no bypass of any
kind**. It reads `APCA_API_KEY_ID` / `APCA_API_SECRET_KEY` /
`APCA_API_BASE_URL` (and, for trade telemetry, `DATABASE_URL`) from a
local `.env` file (never commit real keys — `.env` is already
gitignored). `load_config` FAILS CLOSED: if `APCA_API_BASE_URL`'s host
isn't exactly Alpaca's paper endpoint (`PAPER_TRADING_HOSTNAME`), it
raises unconditionally — no `TradingClient` is ever constructed and no
scan ever runs. There is no environment variable, CLI flag, sentinel
value, or any other opt-in that can make this succeed against a
non-paper host; placing real orders requires editing this source file.
This gate is the only way real orders could ever be placed, and it's
built to be impossible to trip via configuration, by accident or
otherwise.

`_market_is_open` checks Alpaca's market clock immediately before EACH
individual `submit_order`/`close_position` call — liquidations,
overweight-drift sells, underweight-drift buys, post-fill cap-trim
sells, and the SPY VaR hedge buy each recheck independently, right
before their own submission, rather than once for the whole run. If the
market is closed (or the clock itself can't be fetched — fails safe the
same way) at that specific moment, only THAT submission is skipped (with
an explicit "SKIPPED (market closed)" status); the rest of the run —
scan, report, risk-calc, and any other order whose own recheck still
finds the market open — proceeds normally. A single stale open/closed
determination taken before the (multi-minute) scan started is exactly
the failure mode this guards against: the market can close mid-run, and
the daily cron schedule doesn't itself know about market holidays or
early closes.

It is "autonomous" by design (no interactive confirmation prompt) so it
can run unattended, e.g. from a scheduler — pass `--dry-run` to preview
the full scan and every order it *would* place without submitting
anything (dry runs never construct real orders, poll for fills, or write
to `trade_logs`).

Usage:
    python -m src.trading.alpaca_execution [--dry-run] [--top-n 10]
"""

import argparse
import datetime
import logging
import math
import os
import sys
import time
from dataclasses import dataclass, replace
from decimal import Decimal, ROUND_DOWN
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import urlparse

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from alpaca.common.exceptions import APIError
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import (
    AssetClass,
    AssetStatus,
    ContractType,
    OrderSide,
    OrderStatus,
    QueryOrderStatus,
    TimeInForce,
)
from alpaca.trading.requests import GetOptionContractsRequest, GetOrdersRequest, MarketOrderRequest

from src.api.sector_medians import save_sector_medians
from src.backtesting.historical_tester import (
    DEFAULT_TOP_N,
    TickerAnalysis,
    ValuationResult,
    calculate_sector_median_price_to_intrinsic,
    calculate_sector_sample_counts,
    compute_valuation,
    score_ticker,
)
from src.data_ingestion.fetch_financials import get_current_price, get_ticker_object
from src.dcf_model.dcf import DEFAULT_BETA, DCFAssumptions
from src.risk.hedging import calculate_spy_hedge
from src.risk.monte_carlo import calculate_portfolio_var
from src.utils.db import ensure_schema, log_trade
from src.utils.macro import get_risk_free_rate
from src.utils.ticker_universe import DEFAULT_SP500_TOP_100_TICKERS
from src.valuation.altman_z import calculate_altman_z
from src.valuation.piotroski import calculate_f_score
from src.valuation.technical import calculate_rsi

logger = logging.getLogger(__name__)

PAPER_TRADING_HOSTNAME = "paper-api.alpaca.markets"

MIN_BETA_FLOOR = 0.5  # floor beta at this level to prevent overallocating to low-beta anomalies
MIN_ORDER_NOTIONAL_USD = 1.00  # Alpaca's own minimum notional order size
ALTMAN_Z_DISTRESS_THRESHOLD = 1.8  # below this, Altman classifies a company as in the "Distress Zone"
# The original 1968 Altman Z-Score is a manufacturing/industrial-company
# formula; its working-capital and asset-turnover terms aren't meaningful
# for these sectors' balance-sheet structures, so the gate is marked
# not-applicable (neither pass nor fail) rather than misapplied to them.
ALTMAN_Z_EXCLUDED_SECTORS = {"Financial Services", "Financials", "Utilities", "Real Estate"}
TREND_SMA_WINDOW_DAYS = 200  # trading days
TREND_SMA_TOLERANCE = 0.98  # current price must be >= 98% of the 200-day SMA to pass

PIOTROSKI_MIN_F_SCORE = 5  # below this, fundamental quality is considered too weak to value
RSI_MAX_ENTRY_THRESHOLD = 45  # only enter while technically cooling off / oversold, not while hot

FCF_YIELD_NORMALIZATION_FACTOR = 10  # a 10% FCF yield maps to a normalized multiplier of 1.0
FCF_YIELD_NORMALIZED_CAP = 2.0  # ceiling on the normalized FCF yield multiplier
DCF_CONVICTION_BLEND_WEIGHT = 0.60  # weight on the original DCF-based Conviction Score
FCF_YIELD_BLEND_WEIGHT = 0.40  # weight on the normalized FCF Yield multiplier

MAX_POSITION_WEIGHT = 0.15  # no single position may exceed 15% of equity
MAX_SECTOR_WEIGHT = 0.25  # no single GICS sector may exceed 25% of equity, combined
_CAP_ITERATION_LIMIT = 20  # water-filling redistribution rounds before giving up
_CAP_EPSILON = 1e-9  # floating-point tolerance so redistribution converges cleanly

DRIFT_THRESHOLD = 0.03  # only rebalance an already-held pick if weight drifts > 3 points

# Tolerance applied when proving a post-fill cap-trim actually restored a
# position to at/under MAX_POSITION_WEIGHT (see `_check_post_fill_caps`).
# This is a DOLLAR/notional tolerance, not a weight-fraction one — a fixed
# weight-fraction tolerance scales with account size (0.1 percentage point
# is $100 of slack on a $100,000 account, and $10,000 on a $10,000,000
# one), which is far more slack than the rounding it's meant to absorb
# actually requires. The only real source of residual "shortfall" this
# needs to cover is cent-level notional rounding (`round(excess_value,
# 2)` at submission time) and ordinary floating-point representation
# noise in the market_value/equity arithmetic above — both are on the
# order of a single cent, regardless of account size. One cent plus a
# small explicit floating-point epsilon comfortably covers both without
# ever being wide enough to silently accept a genuine, actionable
# remaining breach (a real $1/$50/$100 residual must still be reported
# incomplete — see `TestPostFillCapNotionalTolerance`).
POST_TRIM_NOTIONAL_TOLERANCE_DOLLARS = 0.01 + 1e-6  # one cent + float-precision epsilon

ORDER_POLL_MAX_ATTEMPTS = 10  # how many times to poll a submitted order for a resolved status
ORDER_POLL_INTERVAL_SECONDS = 0.5  # delay between polls

HEDGE_UNDERLYING_SYMBOL = "SPY"
HEDGE_DAYS_TO_EXPIRY = 30  # target expiry for the VaR-offsetting put, in calendar days
HEDGE_EXPIRY_SEARCH_WINDOW_DAYS = 10  # +/- window around the target expiry to search Alpaca's listed chain
HEDGE_STRIKE_SEARCH_BAND = 0.10  # search strikes within +/-10% of the current SPY price for the ATM contract
HEDGE_IMPLIED_VOL = 0.15  # assumed implied volatility for the selected contract (see execute_spy_var_hedge)
HEDGE_STRESS_MOVE_FRACTION = 0.07  # assumed SPY drawdown scenario used to size the hedge's payoff
HEDGE_BUDGET_FRACTION_OF_EQUITY = 0.02  # hard cap: hedge premium spend, as a fraction of account equity
HEDGE_MAX_CONTRACTS = 50  # hard cap: absolute maximum number of put contracts per run

# Order statuses `_await_order_resolution` treats as fully resolved / no
# further change expected this run.
_TERMINAL_ORDER_STATUSES = {
    OrderStatus.FILLED,
    OrderStatus.CANCELED,
    OrderStatus.EXPIRED,
    OrderStatus.REJECTED,
    OrderStatus.DONE_FOR_DAY,
    OrderStatus.STOPPED,
    OrderStatus.SUSPENDED,
    OrderStatus.CALCULATED,
}


@dataclass
class AlpacaConfig:
    """Alpaca credentials/endpoint loaded from `.env`."""

    api_key: str
    secret_key: str
    base_url: str

    @property
    def is_paper(self) -> bool:
        """Whether `base_url`'s host is exactly Alpaca's paper trading endpoint."""
        return urlparse(self.base_url).hostname == PAPER_TRADING_HOSTNAME


def load_config() -> AlpacaConfig:
    """
    Load Alpaca credentials from `.env` via `python-dotenv`.

    This project is paper-trading only by design (see the module
    docstring's Safety section). If `APCA_API_BASE_URL` doesn't resolve
    to Alpaca's paper host, this raises unconditionally — no
    `TradingClient` is ever constructed and no scan ever runs. There is
    no environment variable, CLI flag, or other configuration that can
    bypass this.

    Returns:
        An AlpacaConfig.

    Raises:
        RuntimeError: If any of APCA_API_KEY_ID, APCA_API_SECRET_KEY, or
            APCA_API_BASE_URL is missing, or if the endpoint isn't the
            paper host.
    """
    load_dotenv()

    api_key = os.getenv("APCA_API_KEY_ID")
    secret_key = os.getenv("APCA_API_SECRET_KEY")
    base_url = os.getenv("APCA_API_BASE_URL")

    missing = [
        name
        for name, value in [
            ("APCA_API_KEY_ID", api_key),
            ("APCA_API_SECRET_KEY", secret_key),
            ("APCA_API_BASE_URL", base_url),
        ]
        if not value
    ]
    if missing:
        raise RuntimeError(
            f"Missing required .env variable(s): {', '.join(missing)}. "
            "Add them to a `.env` file in the project root, e.g.:\n"
            "  APCA_API_KEY_ID=your-key-id\n"
            "  APCA_API_SECRET_KEY=your-secret-key\n"
            "  APCA_API_BASE_URL=https://paper-api.alpaca.markets"
        )

    config = AlpacaConfig(api_key=api_key, secret_key=secret_key, base_url=base_url)
    if not config.is_paper:
        raise RuntimeError(
            f"APCA_API_BASE_URL ({base_url!r}) is not Alpaca's paper trading "
            f"endpoint ({PAPER_TRADING_HOSTNAME!r}). This project is paper-only "
            "by design and unconditionally refuses to construct a trading client "
            "or run a scan against a non-paper endpoint. There is no "
            "configuration flag, environment variable, or opt-in that bypasses "
            "this — placing real orders is not supported by this codebase."
        )
    return config


def build_trading_client(config: AlpacaConfig) -> TradingClient:
    """Construct an alpaca-py TradingClient from a loaded AlpacaConfig."""
    return TradingClient(
        api_key=config.api_key,
        secret_key=config.secret_key,
        paper=config.is_paper,
        url_override=config.base_url,
    )


def _positive_int(value: str) -> int:
    """argparse type: accepts only a strictly positive integer."""
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"{value!r} is not an integer.") from exc
    if parsed <= 0:
        raise argparse.ArgumentTypeError(f"{value!r} must be a positive integer.")
    return parsed


def _market_is_open(trading_client: TradingClient) -> bool:
    """
    Whether Alpaca's market clock currently reports the market as open.

    Fails safe (treats the market as closed) if the clock itself can't be
    fetched — submitting DAY market orders while genuinely unsure whether
    the market is open is exactly the failure mode this guards against.
    """
    try:
        return bool(trading_client.get_clock().is_open)
    except APIError as exc:
        logger.warning("Could not fetch Alpaca market clock; treating market as closed: %s", exc)
        return False


def _open_order_symbols(trading_client: TradingClient) -> Optional[set]:
    """
    Symbols with at least one currently open order on Alpaca, used to
    avoid submitting a duplicate order for a symbol whose previous order
    (e.g. left over from a prior run, or one still working) hasn't
    resolved yet.

    Returns:
        The set of symbols with an open order, or None if the lookup
        itself failed — callers should then treat every submission as
        unsafe (skip it) rather than risk stacking a duplicate order on
        top of a real API failure they can't see past.
    """
    try:
        orders = trading_client.get_orders(filter=GetOrdersRequest(status=QueryOrderStatus.OPEN))
    except APIError as exc:
        logger.warning("Could not fetch open orders; treating all submissions as unsafe this run: %s", exc)
        return None
    return {order.symbol for order in orders}


def _is_duplicate_submission(symbol: str, open_symbols: Optional[set]) -> bool:
    """True if `symbol` should be skipped this run because it may already have an open order."""
    return open_symbols is None or symbol in open_symbols


def _mark_symbol_pending(symbol: str, open_order_symbols: Optional[set]) -> None:
    """
    Record `symbol` as having an in-flight order, mutating
    `open_order_symbols` in place immediately after a `submit_order`/
    `close_position` call succeeds — before polling for resolution.

    `open_order_symbols` is threaded by reference through every
    submission phase in a single `main()` run (liquidate -> rebalance ->
    post-fill cap trim -> hedge); marking a symbol pending the instant an
    order is accepted means a LATER phase in the same run sees it as
    unsafe via `_is_duplicate_submission`, even if that order hasn't
    resolved by the time the later phase runs. A `None` set (the
    open-orders lookup itself failed) is left alone — every submission is
    already being treated as unsafe in that case.
    """
    if open_order_symbols is not None:
        open_order_symbols.add(symbol)


def _mark_symbol_resolved(symbol: str, order: object, open_order_symbols: Optional[set]) -> None:
    """
    Clear `symbol`'s pending marker once its order reaches a terminal
    status (`_TERMINAL_ORDER_STATUSES`) — filled, rejected, canceled,
    expired, etc. all mean this specific order is no longer open, so a
    later phase should not be blocked from submitting a fresh order for
    the same symbol on its account. If `order` is None (the poll itself
    failed) or still non-terminal (polling window exhausted while still
    working), the symbol is deliberately left marked pending — fails safe
    against a possible duplicate rather than assuming it resolved.
    """
    if open_order_symbols is None:
        return
    if order is not None and order.status in _TERMINAL_ORDER_STATUSES:
        open_order_symbols.discard(symbol)


def _await_order_resolution(
    trading_client: TradingClient,
    order_id,
    max_attempts: int = ORDER_POLL_MAX_ATTEMPTS,
    poll_interval_seconds: float = ORDER_POLL_INTERVAL_SECONDS,
):
    """
    Poll `get_order_by_id` until Alpaca reports a resolved status
    (`_TERMINAL_ORDER_STATUSES`) or `max_attempts` polls elapse.

    Never assumes a fill: the caller must read the RETURNED order's own
    `status`/`filled_qty`/`filled_avg_price` to decide what actually
    happened — this only decides when to stop polling, returning
    whatever the order's latest known state is (which may still be
    non-terminal, e.g. a still-working partial fill, if the polling
    window was exhausted first).

    Returns:
        The most recently fetched Order. If the lookup itself fails on
        every attempt, returns None — the caller must treat that as "fill
        status unknown," never as "filled."
    """
    order = None
    for _ in range(max_attempts):
        try:
            order = trading_client.get_order_by_id(order_id)
        except APIError as exc:
            logger.warning("Failed to poll order %s status: %s", order_id, exc)
            return order
        if order.status in _TERMINAL_ORDER_STATUSES:
            return order
        time.sleep(poll_interval_seconds)
    return order


def _blend_conviction_with_fcf_yield(analysis: TickerAnalysis) -> TickerAnalysis:
    """
    Blend the original DCF-based Conviction Score with a normalized FCF
    Yield multiplier into the final Conviction Score used to rank/size
    Top-N picks:

        normalized_fcf = clamp(fcf_yield * FCF_YIELD_NORMALIZATION_FACTOR, 0.0, FCF_YIELD_NORMALIZED_CAP)
        final_conviction = (dcf_conviction * DCF_CONVICTION_BLEND_WEIGHT)
                          + (normalized_fcf * FCF_YIELD_BLEND_WEIGHT)

    A missing FCF Yield (statement data unavailable) contributes 0 to the
    blend rather than rejecting the ticker outright — the DCF Conviction
    Score alone still carries 60% of the final score. Leaves analyses
    that failed an earlier gate (`is_valid` False) untouched.

    Only applied here, in the live/paper trading engine — the backtester
    (`src.backtesting.historical_tester.score_ticker`) keeps the original,
    unblended Conviction Score so historical results stay comparable
    across runs.
    """
    if not analysis.is_valid:
        return analysis

    normalized_fcf = max(0.0, min((analysis.fcf_yield or 0.0) * FCF_YIELD_NORMALIZATION_FACTOR, FCF_YIELD_NORMALIZED_CAP))
    final_conviction = (analysis.conviction_score * DCF_CONVICTION_BLEND_WEIGHT) + (
        normalized_fcf * FCF_YIELD_BLEND_WEIGHT
    )
    # Explicit float() cast: conviction_score can otherwise carry a
    # numpy.float64 through from pandas-derived upstream arithmetic, which
    # psycopg2 can't adapt when this later reaches log_trade().
    return replace(analysis, conviction_score=float(final_conviction))


def check_trend_filter(ticker_symbol: str) -> bool:
    """
    200-day SMA trend filter ("value trap protection").

    A stock trading meaningfully below its own 200-trading-day simple
    moving average is more likely to be a genuine value trap than an
    undervalued opportunity, even if it screens cheap on P/IV.

    Args:
        ticker_symbol: Stock ticker symbol, e.g. "AAPL".

    Returns:
        True if the current price is at or above TREND_SMA_TOLERANCE
        (98%) of the 200-day SMA. False if it's measurably below that
        trend, if fewer than TREND_SMA_WINDOW_DAYS (200) valid closes are
        available — a "200-day SMA" computed over a shorter window isn't
        actually the 200-day average, so this fails safe rather than
        silently averaging whatever's there (e.g. for a recent IPO) — or
        if the price history is unavailable at all. Missing/insufficient
        trend data fails safe, the same posture as the Altman Z-Score
        distress gate.
    """
    try:
        ticker_obj = get_ticker_object(ticker_symbol)
        # Fetch a full year of calendar days to reliably cover
        # TREND_SMA_WINDOW_DAYS (200) *trading* days after weekends/holidays,
        # then take exactly the most recent 200 trading days.
        history = ticker_obj.history(period="1y")
    except Exception as exc:  # noqa: BLE001 - a missing trend signal must not crash the scan
        logger.warning("Trend filter price history failed for %s: %s", ticker_symbol, exc)
        return False

    if history is None or history.empty:
        logger.warning("No price history available for %s trend filter.", ticker_symbol)
        return False

    valid_history = history.dropna(subset=["Close"]).tail(TREND_SMA_WINDOW_DAYS)
    if len(valid_history) < TREND_SMA_WINDOW_DAYS:
        logger.warning(
            "Only %d valid close(s) available for %s trend filter (need >= %d); failing safe.",
            len(valid_history), ticker_symbol, TREND_SMA_WINDOW_DAYS,
        )
        return False

    current_price = float(valid_history["Close"].iloc[-1])
    sma_200 = float(valid_history["Close"].mean())
    if sma_200 <= 0:
        return False

    return current_price >= sma_200 * TREND_SMA_TOLERANCE


# --------------------------------------------------------------------------
# Today's two-pass sector-relative DCF scan
# --------------------------------------------------------------------------

def _altman_gate_reason(sector: str, z_score: Optional[float]) -> Optional[str]:
    """None if the Altman Z-Score gate passes or doesn't apply to `sector`; else a rejection reason."""
    if sector in ALTMAN_Z_EXCLUDED_SECTORS:
        return None
    if z_score is None or z_score < ALTMAN_Z_DISTRESS_THRESHOLD:
        score_display = f"{z_score:.2f}" if z_score is not None else "unavailable"
        return f"Failed Altman Z-Score distress filter (score={score_display})."
    return None


def _entry_gate_failure_reason(ticker: str, sector: str, z_score: Optional[float]) -> Optional[str]:
    """
    Apply the four entry gates (Altman Z, 200-SMA trend, Piotroski
    F-Score, RSI micro-dip) in order, short-circuiting on the first
    failure. Called AFTER a ticker has already been valued and scored —
    see the module docstring's "Entry gates are applied AFTER valuation"
    section for why this ordering matters.

    Returns:
        None if every applicable gate passes; otherwise a human-readable
        rejection reason.
    """
    altman_reason = _altman_gate_reason(sector, z_score)
    if altman_reason is not None:
        return altman_reason

    if not check_trend_filter(ticker):
        return "Failed 200-SMA trend check (value trap protection)."

    f_score = calculate_f_score(ticker)
    if f_score < PIOTROSKI_MIN_F_SCORE:
        return (
            f"Failed Piotroski F-Score quality check (F-Score={f_score}, "
            f"minimum={PIOTROSKI_MIN_F_SCORE})."
        )

    rsi = calculate_rsi(ticker)
    if rsi is None or rsi >= RSI_MAX_ENTRY_THRESHOLD:
        rsi_display = f"{rsi:.1f}" if rsi is not None else "unavailable"
        return f"RSI at {rsi_display}. Waiting for micro-dip < {RSI_MAX_ENTRY_THRESHOLD}."

    return None


def run_todays_scan(
    tickers: List[str], assumptions: Optional[DCFAssumptions] = None
) -> tuple:
    """
    Run the two-pass sector-relative DCF scan (Pass 1 valuation, sector
    median, Pass 2 filter + Conviction Score) as of today, then apply the
    four entry gates purely to decide Top-N *eligibility* — never to
    influence which tickers contributed to the sector median (see the
    module docstring).

    Args:
        tickers: Universe of ticker symbols to scan.
        assumptions: DCF assumptions applied uniformly. Defaults to
            DCFAssumptions() (dynamic, per-company historical growth/margin).

    Returns:
        (analyses, sector_medians, valuations, risk_free_rate) — the
        final, gated TickerAnalysis list, the sector -> median P/IV dict
        (from the FULL unscreened universe), the raw Pass 1
        ValuationResult list, and the single risk-free rate used for
        every valuation this run (all three of the latter feed
        `refresh_sector_median_cache`).
    """
    assumptions = assumptions or DCFAssumptions()
    today = datetime.date.today().isoformat()

    # Shared macro input, fetched once for the whole scan (not once per
    # ticker) — see compute_valuation's docstring.
    risk_free_rate = get_risk_free_rate(as_of_date=today)

    logger.info("Running Pass 1: valuing %d tickers as of %s (unscreened)...", len(tickers), today)
    valuations: List[ValuationResult] = []
    for ticker in tickers:
        z_score = calculate_altman_z(ticker)
        try:
            valuation = compute_valuation(ticker, today, assumptions, risk_free_rate=risk_free_rate)
            valuation = replace(valuation, altman_z_score=z_score)
        except Exception as exc:  # noqa: BLE001 - never let one bad ticker kill the run
            logger.warning("Unexpected failure valuing %s: %s", ticker, exc)
            valuation = ValuationResult(
                ticker=ticker, as_of_date=today, altman_z_score=z_score, skip_reason=f"Unexpected error: {exc}"
            )
        valuations.append(valuation)

    # Sector medians reflect the FULL, unscreened, successfully-valued
    # universe — computed before any entry gate is applied below.
    sector_medians = calculate_sector_median_price_to_intrinsic(valuations)

    logger.info("Running Pass 2: applying the sector-relative filter and Conviction Score...")
    analyses: List[TickerAnalysis] = [score_ticker(v, sector_medians) for v in valuations]

    logger.info("Applying entry gates (Altman Z, 200-SMA trend, Piotroski F-Score, RSI) for Top-N eligibility...")
    gated_analyses: List[TickerAnalysis] = []
    for analysis, valuation in zip(analyses, valuations):
        if not analysis.is_valid:
            gated_analyses.append(analysis)
            continue
        gate_reason = _entry_gate_failure_reason(analysis.ticker, analysis.sector, valuation.altman_z_score)
        if gate_reason is not None:
            logger.warning("REJECTED (entry gate): %s — %s", analysis.ticker, gate_reason)
            gated_analyses.append(replace(analysis, conviction_score=None, skip_reason=gate_reason))
        else:
            gated_analyses.append(analysis)

    gated_analyses = [_blend_conviction_with_fcf_yield(a) for a in gated_analyses]

    return gated_analyses, sector_medians, valuations, risk_free_rate


def refresh_sector_median_cache(
    sector_medians: Dict[str, float],
    valuations: List[ValuationResult],
    assumptions: DCFAssumptions,
    risk_free_rate: float,
) -> None:
    """
    Persist today's freshly computed sector medians to the live API's
    cache (`src.api.sector_medians`), so `GET /api/evaluate/{ticker}`
    stays reasonably current between trading runs too.

    This now writes the SAME full-metadata schema
    `src.api.sector_medians.generate_sector_medians` does (assumptions,
    risk-free rate, per-sector sample counts) — safe to share one cache
    file between the two writers as long as both value the full,
    unscreened universe (which `run_todays_scan` now does; see its
    docstring), so "freshest wins" is correct rather than one silently
    overwriting the other with a narrower population.

    Args:
        sector_medians: From `run_todays_scan`.
        valuations: From `run_todays_scan` (the raw Pass 1 list).
        assumptions: The growth/margin/terminal-growth assumptions used
            for this scan (its own `risk_free_rate` field is NOT
            necessarily set — pass the actual rate used separately).
        risk_free_rate: The single risk-free rate `run_todays_scan`
            actually used for every valuation this run.
    """
    tickers_used = sum(1 for v in valuations if v.is_valid)
    cache = {
        "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "universe_size": len(valuations),
        "tickers_used": tickers_used,
        "risk_free_rate": risk_free_rate,
        "assumptions": {
            "revenue_growth_rate": assumptions.revenue_growth_rate,
            "operating_margin": assumptions.operating_margin,
            "terminal_growth_rate": assumptions.terminal_growth_rate,
        },
        "sector_medians": sector_medians,
        "sector_sample_counts": calculate_sector_sample_counts(valuations),
    }
    save_sector_medians(cache)
    logger.info("Refreshed API sector-median cache from %d/%d tickers.", tickers_used, len(valuations))


def select_top_picks(analyses: List[TickerAnalysis], top_n: int) -> List[TickerAnalysis]:
    """Rank sector-filter survivors by Conviction Score and take the top N."""
    ranked = sorted(
        (a for a in analyses if a.is_valid),
        key=lambda a: a.conviction_score,
        reverse=True,
    )
    return ranked[:top_n]


# --------------------------------------------------------------------------
# Account / order execution
# --------------------------------------------------------------------------

def get_current_positions(trading_client: TradingClient) -> Dict[str, object]:
    """Fetch current open positions, keyed by ticker symbol."""
    positions = trading_client.get_all_positions()
    return {position.symbol: position for position in positions}


def _safe_log_trade(
    ticker: str,
    action: str,
    quantity: float,
    execution_price: float,
    wacc: Optional[float],
    beta: Optional[float],
    conviction_score: Optional[float],
    altman_z_score: Optional[float],
    var_95: Optional[float] = None,
    cvar_95: Optional[float] = None,
) -> None:
    """
    Call `log_trade`, but never let a telemetry failure (e.g. Postgres
    unreachable, `DATABASE_URL` unset) abort or interrupt trade execution.
    Failures are logged as warnings and swallowed.

    `var_95`/`cvar_95` are portfolio-level, not per-trade — every call
    site except the end-of-run risk snapshot leaves them at their None
    default.
    """
    try:
        log_trade(
            ticker=ticker,
            action=action,
            quantity=quantity,
            execution_price=execution_price,
            wacc=wacc,
            beta=beta,
            conviction_score=conviction_score,
            altman_z_score=altman_z_score,
            var_95=var_95,
            cvar_95=cvar_95,
        )
    except Exception as exc:  # noqa: BLE001 - telemetry must never block execution
        logger.warning("Trade telemetry logging failed for %s %s: %s", action, ticker, exc)


def _is_profit_take_candidate(position: object, analysis: Optional[TickerAnalysis]) -> bool:
    """
    Whether a held position's current market price has risen to or above
    its own DCF intrinsic value — no longer a margin-of-safety
    opportunity regardless of Conviction Score rank.
    """
    if analysis is None or analysis.historical_intrinsic_value is None:
        return False
    try:
        current_price = float(position.current_price)
    except (TypeError, ValueError):
        return False
    return current_price >= analysis.historical_intrinsic_value


def liquidate_non_target_positions(
    trading_client: TradingClient,
    positions: Dict[str, object],
    target_tickers: set,
    analyses_by_ticker: Dict[str, TickerAnalysis],
    dry_run: bool,
    open_order_symbols: Optional[set] = None,
) -> List[dict]:
    """
    Liquidate a held EQUITY position for either of two independent reasons:

        1. The ticker fell out of the Top-N target portfolio entirely
           (not in `target_tickers`).
        2. Profit-taking: the ticker is still a target pick, but its
           current market price has risen to or above its DCF intrinsic
           value (`_is_profit_take_candidate`) — exited even though it
           would otherwise be rebought, since it no longer represents a
           margin-of-safety opportunity.

    Any position whose `asset_class` is NOT `AssetClass.US_EQUITY` (i.e.
    an options position — in practice, a previously-bought SPY VaR hedge
    put) is skipped entirely: it was never a Top-N equity target and was
    never meant to be evaluated as one. Without this check, a hedge
    position's OCC option symbol simply never matches anything in
    `target_tickers`, so it would otherwise be swept up and liquidated
    here on the very next run after it was bought.

    Args:
        trading_client: An initialized alpaca-py TradingClient.
        positions: Current positions, keyed by symbol (from
            `get_current_positions`).
        target_tickers: Symbols that should remain/be established.
        analyses_by_ticker: This run's full scan results, keyed by
            ticker, used both to attach WACC/beta/Conviction Score/
            Altman Z-Score to the trade log and to evaluate the
            profit-taking check.
        dry_run: If True, log what would be liquidated without submitting
            any order.
        open_order_symbols: From `_open_order_symbols` — a symbol with an
            already-open order is skipped rather than closed again, and
            `None` means the open-orders lookup itself failed, in which
            case EVERY submission this call would otherwise make is
            skipped (fails safe against a possible duplicate).

    Returns:
        A list of {"symbol", "qty", "market_value", "reason", "status"}
        dicts, one per position actually liquidated (or that would be, in
        a dry run, or that was skipped). Callers should treat any symbol
        with status "LIQUIDATED" or "DRY-RUN (would liquidate)" as no
        longer held, so it isn't immediately rebought in the same run.
    """
    results = []
    for symbol, position in positions.items():
        if getattr(position, "asset_class", AssetClass.US_EQUITY) != AssetClass.US_EQUITY:
            logger.info("Held intentionally (option hedge position, not an equity rebalance target): %s", symbol)
            continue

        analysis = analyses_by_ticker.get(symbol)
        is_profit_take = symbol in target_tickers and _is_profit_take_candidate(position, analysis)

        if symbol not in target_tickers:
            reason = "fell out of Top N"
        elif is_profit_take:
            reason = "profit-taking: price >= intrinsic value"
        else:
            continue

        record = {
            "symbol": symbol,
            "qty": position.qty,
            "market_value": float(position.market_value),
            "reason": reason,
        }

        if dry_run:
            record["status"] = "DRY-RUN (would liquidate)"
            results.append(record)
            continue

        if _is_duplicate_submission(symbol, open_order_symbols):
            record["status"] = "SKIPPED (open order already pending)"
            results.append(record)
            continue

        if not _market_is_open(trading_client):
            record["status"] = "SKIPPED (market closed)"
            results.append(record)
            continue

        try:
            submitted = trading_client.close_position(symbol)
        except APIError as exc:
            logger.warning("Failed to submit liquidation for %s: %s", symbol, exc)
            record["status"] = f"FAILED ({exc})"
            results.append(record)
            continue

        _mark_symbol_pending(symbol, open_order_symbols)
        order = _await_order_resolution(trading_client, submitted.id)
        _mark_symbol_resolved(symbol, order, open_order_symbols)
        if order is None:
            record["status"] = "UNKNOWN (could not confirm order status)"
            results.append(record)
            continue

        filled_qty = float(order.filled_qty) if order.filled_qty else 0.0
        filled_price = float(order.filled_avg_price) if order.filled_avg_price else None

        if order.status == OrderStatus.REJECTED:
            record["status"] = "REJECTED"
        elif order.status == OrderStatus.CANCELED:
            record["status"] = f"CANCELED (filled_qty={filled_qty})"
        elif order.status == OrderStatus.EXPIRED and filled_qty <= 0:
            record["status"] = "EXPIRED (no fill)"
        elif filled_qty <= 0:
            record["status"] = f"PENDING (status={order.status.value}, not yet filled)"
        else:
            if order.status == OrderStatus.FILLED:
                status_label = "LIQUIDATED"
            elif order.status == OrderStatus.EXPIRED:
                status_label = f"PARTIALLY_LIQUIDATED THEN EXPIRED (qty={filled_qty})"
            else:
                status_label = f"PARTIALLY_LIQUIDATED (qty={filled_qty})"
            record["status"] = status_label
            _safe_log_trade(
                ticker=symbol,
                action="SELL",
                quantity=filled_qty,
                execution_price=filled_price if filled_price else 0.0,
                wacc=analysis.wacc if analysis else None,
                beta=analysis.beta if analysis else None,
                conviction_score=analysis.conviction_score if analysis else None,
                altman_z_score=analysis.altman_z_score if analysis else None,
            )

        results.append(record)

    return results


def _inverse_risk(beta: Optional[float]) -> float:
    """1 / beta, flooring beta at MIN_BETA_FLOOR to cap overallocation to
    artificially low-beta anomalies. Missing beta falls back to DEFAULT_BETA
    (matching the same fallback `calculate_wacc` uses)."""
    return 1.0 / max(beta if beta is not None else DEFAULT_BETA, MIN_BETA_FLOOR)


def _distribute_capped(
    weights: Dict[str, float], recipients: List[str], amount: float, cap: float
) -> None:
    """
    Distribute `amount` pro-rata (by current weight) across `recipients`,
    in place on `weights`, without letting any recipient's weight exceed
    `cap`.

    A single proportional pass can overshoot some recipients' remaining
    headroom (e.g. a recipient already close to `cap`). This iterates:
    give each recipient its pro-rata share, clipped to its own headroom;
    whatever a recipient couldn't absorb is pooled and redistributed
    pro-rata among the *remaining* recipients that still have headroom,
    repeating until the full amount is placed or no recipient has any
    headroom left — in which case the leftover is simply not allocated
    (it sits in cash rather than breaching `cap`).
    """
    remaining_recipients = list(recipients)
    remaining_amount = amount

    while remaining_amount > _CAP_EPSILON and remaining_recipients:
        pool_total = sum(weights[t] for t in remaining_recipients)
        if pool_total > 0:
            shares = {t: remaining_amount * (weights[t] / pool_total) for t in remaining_recipients}
        else:
            shares = {t: remaining_amount / len(remaining_recipients) for t in remaining_recipients}

        newly_capped = []
        leftover = 0.0
        for ticker in remaining_recipients:
            headroom = max(cap - weights[ticker], 0.0)
            give = min(shares[ticker], headroom)
            if give < shares[ticker] - _CAP_EPSILON:
                leftover += shares[ticker] - give
                newly_capped.append(ticker)
            weights[ticker] += give

        remaining_amount = leftover
        remaining_recipients = [t for t in remaining_recipients if t not in newly_capped]
        if not newly_capped:
            break  # Fully absorbed this round (or no capacity left to iterate further).


def calculate_inverse_beta_weights(picks: List[TickerAnalysis]) -> Dict[str, float]:
    """
    Compute each pick's target portfolio weight using Inverse Volatility
    Weighting on beta, then apply two institutional risk caps:

        1. MAX_POSITION_WEIGHT (15%): no single position may exceed this
           share of equity. Excess is redistributed pro-rata across
           positions still under their own cap.
        2. MAX_SECTOR_WEIGHT (25%): no single sector's combined weight
           (summed across every pick in that sector) may exceed this.
           Excess is redistributed pro-rata to positions in sectors
           still under their own cap.

    Both caps are re-checked and re-applied together, iteratively, since
    redistributing excess into a position/sector can itself push that
    position/sector over its own cap. Critically, *every* redistribution
    step — whether freed up by the position cap or the sector cap — is
    itself bounded by MAX_POSITION_WEIGHT (`_distribute_capped`), so
    money flowing in to satisfy the sector cap can never re-breach an
    individual position's own cap; without that, the two caps can fight
    each other indefinitely (redistribute into a position -> breaches
    position cap -> redistribute back out -> re-breaches sector cap ->
    ...) instead of converging. If the candidate set can't absorb the
    excess even respecting both caps (e.g. every remaining pick is in a
    single sector already at MAX_SECTOR_WEIGHT), the shortfall is simply
    left unallocated — weights are not guaranteed to sum to 1.0; the
    difference sits in cash rather than breaching either cap.

    Args:
        picks: This run's Top-N TickerAnalysis, ranked by Conviction Score.

    Returns:
        {ticker: weight} — weight as a decimal fraction of equity.
    """
    if not picks:
        return {}

    raw_weights = {pick.ticker: _inverse_risk(pick.beta) for pick in picks}
    total_raw = sum(raw_weights.values())
    weights = {ticker: raw / total_raw for ticker, raw in raw_weights.items()}

    sector_by_ticker = {pick.ticker: (pick.sector or "Unknown") for pick in picks}

    for _ in range(_CAP_ITERATION_LIMIT):
        changed = False

        # --- Position cap ---
        over_position_cap = {t: w for t, w in weights.items() if w > MAX_POSITION_WEIGHT + _CAP_EPSILON}
        if over_position_cap:
            changed = True
            excess = sum(w - MAX_POSITION_WEIGHT for w in over_position_cap.values())
            for ticker in over_position_cap:
                weights[ticker] = MAX_POSITION_WEIGHT

            uncapped = [t for t in weights if t not in over_position_cap]
            _distribute_capped(weights, uncapped, excess, MAX_POSITION_WEIGHT)

        # --- Sector cap ---
        sector_totals: Dict[str, float] = {}
        for ticker, weight in weights.items():
            sector = sector_by_ticker[ticker]
            sector_totals[sector] = sector_totals.get(sector, 0.0) + weight

        over_sector_cap = {s: w for s, w in sector_totals.items() if w > MAX_SECTOR_WEIGHT + _CAP_EPSILON}
        if over_sector_cap:
            changed = True
            for sector, sector_total in over_sector_cap.items():
                scale = MAX_SECTOR_WEIGHT / sector_total
                sector_excess = sector_total - MAX_SECTOR_WEIGHT
                sector_tickers = [t for t in weights if sector_by_ticker[t] == sector]
                for ticker in sector_tickers:
                    weights[ticker] *= scale

                under_cap_tickers = [
                    t
                    for t in weights
                    if sector_by_ticker[t] != sector
                    and sector_totals.get(sector_by_ticker[t], 0.0) < MAX_SECTOR_WEIGHT - _CAP_EPSILON
                ]
                # Bounded by MAX_POSITION_WEIGHT too — a ticker whose sector
                # has room can still be individually at/near its own cap.
                _distribute_capped(weights, under_cap_tickers, sector_excess, MAX_POSITION_WEIGHT)

        if not changed:
            break

    return weights


def _allocate_cent_safe_buy_notionals(pending_buys: List[dict], buying_power: float) -> None:
    """
    Convert each pending BUY record's `order_notional` (already, if
    needed, proportionally scaled toward `buying_power` — see
    `rebalance_target_positions`) into a cent-exact dollar amount, in
    place, such that the SUM of every resulting value is mathematically
    incapable of exceeding `buying_power` — closing the gap where relying
    on each order's later independent `round(x, 2)` at submission time
    could push the aggregate over the cap (several orders each rounding
    up by close to half a cent compounds across the batch).

    Uses `Decimal` built from `str(...)` — never directly from a binary
    float, which would reintroduce the exact imprecision this exists to
    avoid — and `ROUND_DOWN` exclusively: an order's allocated cents are
    never rounded UP past its own (already-proportional) target. A
    running `remaining_cents` budget, consumed in the fixed order
    `pending_buys` was given, additionally clamps each allocation, so
    even compounding floor-rounding error across many orders can only
    ever leave a few cents unallocated (sitting out this run, not lost) —
    never push the total over budget.

    Safe to call whether or not the batch actually needed proportional
    scaling: when the batch is already comfortably under `buying_power`,
    this only floors each order to the nearest cent (can only reduce,
    never increase, what a plain `round(x, 2)` would have submitted).

    Mutates each dict in `pending_buys` in place, replacing
    `order_notional` with the allocated float (always a whole number of
    cents, always >= 0). Does not touch SELL records — callers must
    filter to BUY-side, still-pending records before calling this.
    """
    if not pending_buys:
        return

    remaining_cents = int(
        (Decimal(str(max(buying_power, 0.0))) * 100).to_integral_value(rounding=ROUND_DOWN)
    )

    for record in pending_buys:
        ideal_dollars = max(record["order_notional"], 0.0)
        ideal_cents = int((Decimal(str(ideal_dollars)) * 100).to_integral_value(rounding=ROUND_DOWN))
        allocated_cents = min(ideal_cents, remaining_cents)
        remaining_cents -= allocated_cents
        record["order_notional"] = allocated_cents / 100.0


def rebalance_target_positions(
    trading_client: TradingClient,
    positions: Dict[str, object],
    top_picks: List[TickerAnalysis],
    equity: float,
    dry_run: bool,
    open_order_symbols: Optional[set] = None,
    buying_power: Optional[float] = None,
) -> List[dict]:
    """
    Rebalance each Top-N ticker toward a dynamic, risk-adjusted target
    notional value using Inverse Volatility Weighting on beta with the
    MAX_POSITION_WEIGHT / MAX_SECTOR_WEIGHT institutional caps applied
    (`calculate_inverse_beta_weights`) — BUYING an underweight pick up
    toward target, or SELLING an overweight one down toward target. An
    already-held pick is only rebalanced if its current weight has
    drifted from target by more than DRIFT_THRESHOLD in EITHER direction
    (churn control) — otherwise it's left alone even if not exactly on
    target.

    Args:
        trading_client: An initialized alpaca-py TradingClient.
        positions: Current positions, keyed by symbol. Callers should
            pass a snapshot fetched AFTER this run's liquidations have
            been submitted/confirmed, not the pre-liquidation snapshot —
            sizing off stale positions could double-count capital that's
            actually already been freed up (or not yet freed up) by a
            liquidation earlier in the same run.
        top_picks: This run's Top-N TickerAnalysis, ranked by Conviction Score.
        equity: Current account equity — callers should pass a value
            refetched AFTER this run's liquidations settle, not the
            pre-liquidation figure, so sizing reflects actual realized
            proceeds rather than an estimate.
        dry_run: If True, log what would be bought/sold without
            submitting any order.
        open_order_symbols: From `_open_order_symbols` — see
            `liquidate_non_target_positions` for the exact semantics
            (`None` means fail safe and skip every submission).
        buying_power: Account buying power, refetched at the same time as
            `equity` (see above). If the sum of every planned BUY's
            notional would exceed this, every planned buy is scaled down
            proportionally (not order-dependent — no pick is fully funded
            at another's expense) until the aggregate fits; any buy that
            scales below MIN_ORDER_NOTIONAL_USD is skipped with an
            explicit reason instead of submitting a dust order. Whenever
            `buying_power` is supplied (scaling needed or not), every
            pending BUY's final notional is additionally run through
            `_allocate_cent_safe_buy_notionals` — a `Decimal`/`ROUND_DOWN`
            allocation with a running remaining-cents budget — so
            `sum(submitted BUY notionals) <= buying_power` is an exact
            invariant, never merely true "up to independent per-order
            cent rounding." `None` (not supplied) disables this check
            entirely — no picks are liquidation-slippage/capital
            constrained.

    Returns:
        A list of {"symbol", "target_notional", "current_notional",
        "order_notional", "side", "status"} dicts, one per Top-N ticker.
        `order_notional` is signed: positive means a buy was/would be
        needed, negative means a sell.
    """
    target_weights = calculate_inverse_beta_weights(top_picks)
    picks_by_symbol = {pick.ticker: pick for pick in top_picks}
    records = []

    for pick in top_picks:
        symbol = pick.ticker
        weight = target_weights.get(symbol, 0.0)
        target_notional = equity * weight

        current_position = positions.get(symbol)
        current_notional = float(current_position.market_value) if current_position else 0.0
        current_weight = (current_notional / equity) if equity else 0.0
        drift = abs(current_weight - weight)
        order_notional = target_notional - current_notional
        side = OrderSide.BUY if order_notional >= 0 else OrderSide.SELL

        record = {
            "symbol": symbol,
            "target_notional": target_notional,
            "current_notional": current_notional,
            "order_notional": order_notional,
            "side": side.value,
        }

        if drift <= DRIFT_THRESHOLD:
            record["status"] = f"SKIPPED (within {DRIFT_THRESHOLD:.0%} drift threshold)"
            records.append(record)
            continue

        if abs(order_notional) < MIN_ORDER_NOTIONAL_USD:
            record["status"] = "SKIPPED (already at/above target weight)"
            records.append(record)
            continue

        if side == OrderSide.SELL and abs(order_notional) > current_notional:
            # Never sell more than is actually held (e.g. rounding at the
            # cap boundary); trim to the held notional instead.
            order_notional = -current_notional
            record["order_notional"] = order_notional

        record["_pending"] = True
        records.append(record)

    # Constrain the AGGREGATE of every still-pending BUY to available
    # buying power (SELLs raise cash and are never constrained by it).
    # Scaling proportionally, rather than fully funding earlier picks in
    # iteration order and starving later ones, keeps the outcome
    # deterministic and independent of `top_picks` ordering.
    if buying_power is not None:
        pending_buys = [r for r in records if r.get("_pending") and r["side"] == OrderSide.BUY.value]
        total_buy_notional = sum(r["order_notional"] for r in pending_buys)
        if total_buy_notional > 0 and total_buy_notional > buying_power + _CAP_EPSILON:
            scale = max(buying_power, 0.0) / total_buy_notional
            for r in pending_buys:
                r["order_notional"] = r["order_notional"] * scale
                r["_capital_scaled"] = True
        # Cent-safe allocation runs regardless of whether scaling was
        # needed above — see `_allocate_cent_safe_buy_notionals`'s
        # docstring. This is what makes `sum(submitted BUY notionals) <=
        # buying_power` an invariant rather than something that merely
        # holds "in float arithmetic, before each order's own
        # independent round(x, 2) at submission time."
        _allocate_cent_safe_buy_notionals(pending_buys, buying_power)

    results = []
    for record in records:
        symbol = record["symbol"]
        pick = picks_by_symbol[symbol]
        pending = record.pop("_pending", False)
        capital_scaled = record.pop("_capital_scaled", False)
        if not pending:
            results.append(record)
            continue

        order_notional = record["order_notional"]
        side = OrderSide.BUY if record["side"] == OrderSide.BUY.value else OrderSide.SELL

        if capital_scaled and abs(order_notional) < MIN_ORDER_NOTIONAL_USD:
            record["status"] = "SKIPPED (insufficient buying power)"
            results.append(record)
            continue

        if dry_run:
            status = f"DRY-RUN (would {'buy' if side == OrderSide.BUY else 'sell'})"
            if capital_scaled:
                status += " [capital-constrained: scaled down to available buying power]"
            record["status"] = status
            results.append(record)
            continue

        if _is_duplicate_submission(symbol, open_order_symbols):
            record["status"] = "SKIPPED (open order already pending)"
            results.append(record)
            continue

        if not _market_is_open(trading_client):
            record["status"] = "SKIPPED (market closed)"
            results.append(record)
            continue

        try:
            submitted = trading_client.submit_order(
                order_data=MarketOrderRequest(
                    symbol=symbol,
                    notional=round(abs(order_notional), 2),
                    side=side,
                    time_in_force=TimeInForce.DAY,
                )
            )
        except APIError as exc:
            logger.warning("Failed to submit %s order for %s: %s", side.value, symbol, exc)
            record["status"] = f"FAILED ({exc})"
            results.append(record)
            continue

        _mark_symbol_pending(symbol, open_order_symbols)
        order = _await_order_resolution(trading_client, submitted.id)
        _mark_symbol_resolved(symbol, order, open_order_symbols)
        if order is None:
            record["status"] = "UNKNOWN (could not confirm order status)"
            results.append(record)
            continue

        filled_qty = float(order.filled_qty) if order.filled_qty else 0.0
        filled_price = float(order.filled_avg_price) if order.filled_avg_price else None

        if order.status == OrderStatus.REJECTED:
            record["status"] = "REJECTED"
        elif order.status == OrderStatus.CANCELED:
            record["status"] = f"CANCELED (filled_qty={filled_qty})"
        elif order.status == OrderStatus.EXPIRED and filled_qty <= 0:
            record["status"] = "EXPIRED (no fill)"
        elif filled_qty <= 0:
            record["status"] = f"PENDING (status={order.status.value}, not yet filled)"
        else:
            if order.status == OrderStatus.FILLED:
                status_label = "ORDER FILLED"
            elif order.status == OrderStatus.EXPIRED:
                status_label = f"PARTIALLY FILLED THEN EXPIRED (qty={filled_qty})"
            else:
                status_label = f"PARTIALLY FILLED (qty={filled_qty})"
            record["status"] = status_label
            _safe_log_trade(
                ticker=symbol,
                action=side.value.upper(),
                quantity=filled_qty,
                execution_price=filled_price if filled_price else 0.0,
                wacc=pick.wacc,
                beta=pick.beta,
                conviction_score=pick.conviction_score,
                altman_z_score=pick.altman_z_score,
            )

        results.append(record)

    return results


# --------------------------------------------------------------------------
# Reporting
# --------------------------------------------------------------------------

def print_execution_report(
    analyses: List[TickerAnalysis],
    sector_medians: Dict[str, float],
    top_picks: List[TickerAnalysis],
    liquidations: List[dict],
    buys: List[dict],
    equity_before: float,
    equity_after: Optional[float],
    dry_run: bool,
    target_weights: Optional[Dict[str, float]] = None,
) -> None:
    """Print a clean, human-readable execution report to stdout."""
    valid_count = sum(1 for a in analyses if a.is_valid)
    mode = "DRY RUN" if dry_run else "LIVE (paper)"

    print("=" * 88)
    print(f"ALPACA AUTONOMOUS EXECUTION — {mode} — {datetime.date.today().isoformat()}")
    print("=" * 88)

    print(f"\nScanned {len(analyses)} tickers; {valid_count} passed the sector-relative filter and entry gates.")
    print("\nSector Median P/IV (full unscreened universe):")
    for sector, median in sorted(sector_medians.items(), key=lambda kv: kv[1]):
        print(f"  - {sector}: {median:.2f}x")

    print(f"\nTop {len(top_picks)} by Conviction Score (target portfolio):")
    header = f"{'Ticker':<8}{'Sector':<24}{'Conviction':>11}{'P/IV':>8}{'Sector Med':>12}"
    print(header)
    print("-" * len(header))
    for pick in top_picks:
        print(
            f"{pick.ticker:<8}{pick.sector:<24}{pick.conviction_score:>11.3f}"
            f"{pick.price_to_intrinsic:>7.2f}x{pick.sector_median_price_to_intrinsic:>11.2f}x"
        )

    if target_weights is not None:
        cash_weight = 1.0 - sum(target_weights.values())
        print(f"\nUnallocated (cash) weight after position/sector caps: {cash_weight:.2%}")

    print(f"\nLiquidations ({len(liquidations)} position(s) — fell out of the Top N, or profit-taking):")
    if liquidations:
        for record in liquidations:
            print(
                f"  - {record['symbol']:<6} qty={record['qty']:>10}  "
                f"market_value=${record['market_value']:>12,.2f}  "
                f"reason={record.get('reason', 'n/a'):<40} [{record['status']}]"
            )
    else:
        print("  (none)")

    print("\nBuys / Sells (inverse-beta risk-adjusted target weight, position/sector-capped, drift-gated, bidirectional):")
    for record in buys:
        print(
            f"  - {record['symbol']:<6} [{record.get('side', '?'):<4}] target=${record['target_notional']:>10,.2f}  "
            f"current=${record['current_notional']:>10,.2f}  "
            f"order=${record['order_notional']:>10,.2f}  [{record['status']}]"
        )

    print()
    print(f"Account Equity (before): ${equity_before:,.2f}")
    if equity_after is not None:
        print(f"Account Equity (after):  ${equity_after:,.2f}")
    else:
        print("Account Equity (after):  N/A (dry run — no orders submitted)")
    print("=" * 88)


# --------------------------------------------------------------------------
# VaR-based SPY put hedge
# --------------------------------------------------------------------------

def _select_atm_put_contract(trading_client: TradingClient, spy_price: float, days_to_expiry: int):
    """
    Query Alpaca's listed SPY put chain for the tradable contract closest
    to at-the-money, among expirations closest to `days_to_expiry`
    calendar days out.

    Returns:
        The closest-matching `OptionContract`, or None if the lookup
        fails or no tradable contract is found within the search window
        — a missing contract skips the hedge rather than crashing the run.
    """
    target_expiry = datetime.date.today() + datetime.timedelta(days=days_to_expiry)
    window = datetime.timedelta(days=HEDGE_EXPIRY_SEARCH_WINDOW_DAYS)

    try:
        response = trading_client.get_option_contracts(
            GetOptionContractsRequest(
                underlying_symbols=[HEDGE_UNDERLYING_SYMBOL],
                type=ContractType.PUT,
                status=AssetStatus.ACTIVE,
                expiration_date_gte=(target_expiry - window).isoformat(),
                expiration_date_lte=(target_expiry + window).isoformat(),
                strike_price_gte=str(round(spy_price * (1 - HEDGE_STRIKE_SEARCH_BAND), 2)),
                strike_price_lte=str(round(spy_price * (1 + HEDGE_STRIKE_SEARCH_BAND), 2)),
                limit=200,
            )
        )
    except APIError as exc:
        logger.warning("Failed to fetch %s option chain for VaR hedge: %s", HEDGE_UNDERLYING_SYMBOL, exc)
        return None

    tradable = [c for c in response.option_contracts if c.tradable]
    if not tradable:
        return None

    def _distance(contract) -> tuple:
        expiry = datetime.date.fromisoformat(str(contract.expiration_date))
        return (abs((expiry - target_expiry).days), abs(float(contract.strike_price) - spy_price))

    return min(tradable, key=_distance)


@dataclass
class ExistingHedgeExposure:
    """
    Existing SPY put hedge exposure, split by whether each held position
    is the EXACT SAME contract (same underlying, expiry, type, and
    strike) as the one selected for a new hedge this run, or a DIFFERENT
    SPY put contract that still provides some downside protection but is
    NOT fungible, contract-for-contract, with the newly selected one — a
    deep-ITM put and an ATM put (or two different expiries) don't pay off
    the same amount per contract under the same stress scenario, so their
    raw counts must never be summed together as if they were.

    `matching_contract_qty` is the only figure `execute_spy_var_hedge`
    subtracts from this run's freshly-sized target — `other_contracts`
    (a different-contract symbol -> qty map) is reported separately, for
    visibility, never folded into the incremental-sizing subtraction.
    """

    matching_contract_qty: float
    matching_contract_market_value: float
    other_contracts: Dict[str, float]
    other_contracts_market_value: float

    @property
    def other_contracts_qty(self) -> float:
        return sum(self.other_contracts.values())

    @property
    def total_market_value(self) -> float:
        """Dollar value of ALL existing SPY put exposure (matching + other) — used for hedge-budget accounting."""
        return self.matching_contract_market_value + self.other_contracts_market_value


def _existing_spy_hedge_exposure(
    existing_positions: Optional[Dict[str, object]], selected_contract_symbol: str
) -> ExistingHedgeExposure:
    """
    Split every currently-held SPY put hedge position into "the exact
    same contract as the one selected for a new hedge this run" (matched
    by exact OCC option symbol — since an OCC symbol already uniquely
    encodes underlying/expiry/type/strike, an exact string match IS an
    exact strike+expiry match; no separate parsing needed) vs. "a
    different SPY put contract" — so a new run can top up the SAME
    contract incrementally, while reporting other, non-fungible SPY put
    exposure separately rather than silently treating its raw contract
    count as equivalent protection.

    This project only ever buys options via `execute_spy_var_hedge` (SPY
    puts) — no other code path creates an option position — so any
    `US_OPTION`-class position whose symbol starts with
    `HEDGE_UNDERLYING_SYMBOL` is, by construction, a previously-bought
    hedge contract from an earlier run.
    """
    matching_qty = 0.0
    matching_market_value = 0.0
    other: Dict[str, float] = {}
    other_market_value = 0.0

    if existing_positions:
        for symbol, position in existing_positions.items():
            if getattr(position, "asset_class", AssetClass.US_EQUITY) != AssetClass.US_OPTION:
                continue
            if not symbol.startswith(HEDGE_UNDERLYING_SYMBOL):
                continue
            try:
                qty = float(position.qty)
            except (TypeError, ValueError):
                continue
            try:
                market_value = float(position.market_value)
            except (TypeError, ValueError, AttributeError):
                market_value = 0.0

            if symbol == selected_contract_symbol:
                matching_qty += qty
                matching_market_value += market_value
            else:
                other[symbol] = other.get(symbol, 0.0) + qty
                other_market_value += market_value

    return ExistingHedgeExposure(
        matching_contract_qty=matching_qty,
        matching_contract_market_value=matching_market_value,
        other_contracts=other,
        other_contracts_market_value=other_market_value,
    )


def execute_spy_var_hedge(
    trading_client: TradingClient,
    portfolio_var_dollars: float,
    equity: float,
    dry_run: bool,
    open_order_symbols: Optional[set] = None,
    existing_positions: Optional[Dict[str, object]] = None,
) -> None:
    """
    Select a real, tradable SPY put contract FIRST, then size the hedge
    using THAT contract's actual strike and actual days-to-expiry (not a
    synthetic ATM/30-day contract sized before knowing what's really
    listed, then separately swapped for a different real one) via the
    scenario-based sizing in `calculate_spy_hedge`, then buy only the
    INCREMENTAL contracts needed on top of the SAME contract's existing
    holdings (see `_existing_spy_hedge_exposure` — matched by exact OCC
    symbol, i.e. exact strike AND expiry, never just raw SPY-put contract
    counts summed across different strikes/expiries) — unless `dry_run`
    — so a hedge that's already adequately sized this run buys nothing,
    and a run's hedge never simply accumulates on top of every prior
    run's. Any OTHER held SPY put (a different strike or expiry) is
    reported separately, never counted toward this contract's
    incremental sizing — it isn't fungible, contract-for-contract, with
    the newly selected one.

    A hedge budget (`HEDGE_BUDGET_FRACTION_OF_EQUITY` of `equity`) and an
    absolute contract cap (`HEDGE_MAX_CONTRACTS`) are always applied to
    the TARGET (pre-existing-holdings) size. The budget is further
    reduced by the dollar market value of EVERY currently-held SPY put
    (matching contract and other contracts alike) before sizing, so the
    total premium outstanding across existing-plus-new hedge exposure —
    not just this run's incremental purchase — stays within
    `HEDGE_BUDGET_FRACTION_OF_EQUITY` of equity. Either cap can leave the
    hedge covering less than the full stated VaR, which is the point of
    having a budget. `HEDGE_IMPLIED_VOL` is a configurable assumed
    volatility, not the contract's real live IV: Alpaca's trading-only
    client doesn't reliably expose that, and this is documented as a
    remaining, unavoidable approximation.

    IMPORTANT — this budget is a THEORETICAL premium ceiling, not an
    enforceable actual-spend ceiling. `HEDGE_BUDGET_FRACTION_OF_EQUITY`
    bounds `calculate_spy_hedge`'s contract count against its own
    BSM-modeled `current_put_price` (computed from `HEDGE_IMPLIED_VOL`
    and the selected contract's strike/expiry) — but the order actually
    submitted below is a real MARKET order, which fills at the option's
    real bid/ask at that moment. A real fill can cost more (or less) per
    contract than the modeled price this budget was checked against,
    meaning actual premium spent is not guaranteed to stay within
    `HEDGE_BUDGET_FRACTION_OF_EQUITY` of equity, only the MODELED
    estimate is. See `src.risk.hedging`'s module docstring and
    `docs/limitations-register.md` L-016 (High severity). Enforcing an
    actual-spend ceiling would require fetching a live option quote and
    submitting a LIMIT order bounded by it — not implemented in this
    pass, since no option-quote data client exists anywhere in this
    codebase today and adding one is a genuine scope expansion, not a
    bounded fix; proposed as the next bounded task.

    Joint buying-power safety (live runs only): immediately before
    sizing, a FRESH `trading_client.get_account().buying_power` reading
    is taken and used as an ADDITIONAL ceiling — `hedge_budget_dollars`
    becomes `min(policy_budget_after_existing_exposure,
    fresh_buying_power)` — so this purchase can never be sized against
    stale capacity left over from before the equity-rebalance phase's
    buys/sells settled. This reuses `calculate_spy_hedge`'s own
    dollar-budget-to-max-affordable-contracts floor-division (no
    duplicated pricing logic, no signature change to
    `src.risk.hedging.calculate_spy_hedge`). Alpaca's own
    `buying_power` already reflects submitted/open orders broker-side,
    so this is never separately double-subtracted against the equity
    phase's spend. If the fresh read fails (an `APIError`) or comes back
    non-finite or negative, the ENTIRE hedge is skipped this run — never
    submitted against a guessed or stale capacity figure. This fetch is
    skipped entirely in a dry run (a dry run must never contact the
    broker for anything beyond what it already does elsewhere in
    `main()`); a dry run's "would buy" preview therefore reflects only
    the policy budget/existing-exposure caps, not a live buying-power
    reading.

    Never raises: an unavailable SPY price, no listed contract within the
    search window, or the order itself being rejected (e.g. the paper
    account isn't approved for options trading) are each caught and
    logged as a warning, so a hedge that can't be sized or placed never
    takes down the day's already-completed equity rebalance.
    """
    try:
        spy_price = get_current_price(get_ticker_object(HEDGE_UNDERLYING_SYMBOL))
    except ValueError as exc:
        logger.warning("Could not fetch %s price for VaR hedge: %s", HEDGE_UNDERLYING_SYMBOL, exc)
        return

    if spy_price is None:
        logger.warning("%s price unavailable; skipping VaR hedge.", HEDGE_UNDERLYING_SYMBOL)
        return

    contract = _select_atm_put_contract(trading_client, spy_price, HEDGE_DAYS_TO_EXPIRY)
    if contract is None:
        logger.warning("No tradable %s put contract found near the target strike/expiry; skipping VaR hedge.", HEDGE_UNDERLYING_SYMBOL)
        return

    actual_days_to_expiry = (
        datetime.date.fromisoformat(str(contract.expiration_date)) - datetime.date.today()
    ).days
    if actual_days_to_expiry <= 0:
        logger.warning(
            "Selected %s put contract %s expires today or has already expired; skipping VaR hedge.",
            HEDGE_UNDERLYING_SYMBOL, contract.symbol,
        )
        return

    exposure = _existing_spy_hedge_exposure(existing_positions, contract.symbol)
    if exposure.other_contracts:
        logger.info(
            "Other existing SPY put position(s) held (different strike/expiry than %s, NOT counted "
            "toward this contract's incremental sizing — not fungible with it): %s",
            contract.symbol, exposure.other_contracts,
        )

    hedge_budget_dollars = HEDGE_BUDGET_FRACTION_OF_EQUITY * equity if equity else None
    if hedge_budget_dollars is not None:
        # The budget covers TOTAL hedge premium outstanding, not just
        # this run's incremental purchase — subtract what's already
        # committed across every existing SPY put position (matching
        # contract and other contracts alike).
        hedge_budget_dollars = max(hedge_budget_dollars - exposure.total_market_value, 0.0)

    if not dry_run:
        # Fresh, broker-reported buying power, read immediately before
        # sizing — after the equity-rebalance phase's own buys/sells
        # have already been submitted (see `main`'s call ordering) — so
        # this purchase is never sized against stale/guessed capacity.
        # A dry run never reaches this branch: it must not contact the
        # broker for a check whose only purpose is gating a REAL
        # submission it isn't going to make anyway.
        try:
            fresh_buying_power = float(trading_client.get_account().buying_power)
        except (APIError, TypeError, ValueError, AttributeError) as exc:
            logger.warning(
                "SKIPPED SPY VaR hedge: could not obtain a fresh buying-power reading (%s); "
                "refusing to size a hedge against stale or guessed capacity.",
                exc,
            )
            return

        if not math.isfinite(fresh_buying_power) or fresh_buying_power < 0:
            logger.warning(
                "SKIPPED SPY VaR hedge: fresh buying-power reading is invalid (%r); "
                "refusing to size a hedge against stale or guessed capacity.",
                fresh_buying_power,
            )
            return

        hedge_budget_dollars = (
            fresh_buying_power if hedge_budget_dollars is None else min(hedge_budget_dollars, fresh_buying_power)
        )

    contracts = calculate_spy_hedge(
        portfolio_var_dollars=portfolio_var_dollars,
        spy_price=spy_price,
        strike_price=float(contract.strike_price),
        days_to_expiry=actual_days_to_expiry,
        implied_vol=HEDGE_IMPLIED_VOL,
        stress_move_fraction=HEDGE_STRESS_MOVE_FRACTION,
        hedge_budget_dollars=hedge_budget_dollars,
        max_contracts=HEDGE_MAX_CONTRACTS,
    )

    if contracts <= 0:
        logger.info(
            "No SPY VaR hedge needed/affordable: $%.2f VaR against contract %s.",
            portfolio_var_dollars, contract.symbol,
        )
        return

    already_held = exposure.matching_contract_qty
    contracts = int(contracts - already_held)
    if contracts <= 0:
        logger.info(
            "SPY VaR hedge already covered by %s existing contract(s) of %s (target %s this run); "
            "buying nothing to avoid accumulating.",
            already_held, contract.symbol, contracts + already_held,
        )
        return

    if dry_run:
        logger.info(
            "HEDGE (DRY-RUN): would buy %d additional SPY Put Contracts (%s already held of the SAME "
            "contract) (%s, strike=$%.2f, expiry=%s) to offset $%.2f VaR exposure.",
            contracts, already_held, contract.symbol, float(contract.strike_price), contract.expiration_date,
            portfolio_var_dollars,
        )
        return

    if _is_duplicate_submission(contract.symbol, open_order_symbols):
        logger.info("SKIPPED SPY VaR hedge: an open order already exists for %s.", contract.symbol)
        return

    if not _market_is_open(trading_client):
        logger.info("SKIPPED SPY VaR hedge: market closed at submission time.")
        return

    try:
        submitted = trading_client.submit_order(
            order_data=MarketOrderRequest(
                symbol=contract.symbol,
                qty=contracts,
                side=OrderSide.BUY,
                time_in_force=TimeInForce.DAY,
            )
        )
    except APIError as exc:
        logger.warning("Failed to submit SPY VaR hedge order (%s): %s", contract.symbol, exc)
        return

    _mark_symbol_pending(contract.symbol, open_order_symbols)
    order = _await_order_resolution(trading_client, submitted.id)
    _mark_symbol_resolved(contract.symbol, order, open_order_symbols)
    if order is None:
        logger.warning("Could not confirm SPY VaR hedge order status for %s.", contract.symbol)
        return

    filled_qty = float(order.filled_qty) if order.filled_qty else 0.0
    filled_price = float(order.filled_avg_price) if order.filled_avg_price else None

    if order.status == OrderStatus.REJECTED:
        logger.warning("SPY VaR hedge order for %s was REJECTED.", contract.symbol)
        return
    if order.status == OrderStatus.CANCELED:
        logger.warning("SPY VaR hedge order for %s was CANCELED (filled_qty=%s).", contract.symbol, filled_qty)
        return
    if order.status == OrderStatus.EXPIRED and filled_qty <= 0:
        logger.warning("SPY VaR hedge order for %s EXPIRED with no fill.", contract.symbol)
        return
    if filled_qty <= 0:
        logger.warning(
            "SPY VaR hedge order for %s still pending (status=%s); not confirmed this run.",
            contract.symbol, order.status.value,
        )
        return

    _safe_log_trade(
        ticker=contract.symbol,
        action="BUY",
        quantity=filled_qty,
        execution_price=filled_price if filled_price else 0.0,
        wacc=None,
        beta=None,
        conviction_score=None,
        altman_z_score=None,
    )

    logger.info(
        "HEDGE EXECUTED: Bought %d SPY Put Contracts to offset $%.2f VaR exposure.",
        int(filled_qty),
        portfolio_var_dollars,
    )


def _trim_position_to_cap(
    trading_client: TradingClient,
    symbol: str,
    excess_value: float,
    open_order_symbols: Optional[set] = None,
) -> Optional[float]:
    """
    Submit a SELL of roughly `excess_value` (the dollar amount a
    position sits over MAX_POSITION_WEIGHT) to trim it back toward cap,
    await resolution, and log the fill via `_safe_log_trade` same as any
    other trade this module submits.

    Applies the same two guards every other order-submitting function in
    this module applies before `submit_order`:
        - `_is_duplicate_submission` against `open_order_symbols` — if an
          earlier phase this run (e.g. a rebalance SELL for the same
          symbol) submitted an order that hasn't resolved yet, this skips
          rather than stacking a second, overlapping SELL on top of it.
        - `_market_is_open` — rechecked immediately before this specific
          submission, not reused from an earlier point in the run.

    Never raises: a failed submission, a rejection, or an order that
    can't be confirmed filled are each caught and logged as a warning.

    Args:
        open_order_symbols: From `_open_order_symbols`, ideally refreshed
            immediately before the corrective-trim phase begins (see
            `main`) so it reflects orders submitted earlier in this same
            run, not just what was open before the run started. `None`
            means the lookup failed — every submission is treated as
            unsafe (skipped) rather than risking a duplicate.

    Returns:
        The confirmed filled notional value (`filled_qty *
        filled_avg_price`, always > 0) if the trim SELL resolved with a
        positive confirmed fill — including a partial fill, which still
        returns its own (smaller) actual notional rather than the
        requested `excess_value`. Returns `None` if nothing was
        submitted (duplicate/closed-market skip), the submission failed,
        the order was rejected, the fill status couldn't be confirmed at
        all, or the confirmed fill quantity is zero.

        A non-`None` return is NOT proof the cap was fully restored — a
        partial fill smaller than `excess_value` still returns a
        (smaller) positive value. Callers must compare the returned
        notional against how much trim was actually required — see
        `_check_post_fill_caps`, which recomputes the resulting weight
        from this value rather than assuming any positive fill was
        sufficient.
    """
    if _is_duplicate_submission(symbol, open_order_symbols):
        logger.warning(
            "POST-FILL CAP CORRECTION for %s SKIPPED: an open order already exists for this symbol "
            "this run (e.g. a still-pending rebalance sell) — not submitting an overlapping trim SELL.",
            symbol,
        )
        return None

    if not _market_is_open(trading_client):
        logger.warning("POST-FILL CAP CORRECTION for %s SKIPPED: market closed at submission time.", symbol)
        return None

    try:
        submitted = trading_client.submit_order(
            order_data=MarketOrderRequest(
                symbol=symbol,
                notional=round(excess_value, 2),
                side=OrderSide.SELL,
                time_in_force=TimeInForce.DAY,
            )
        )
    except APIError as exc:
        logger.warning("Failed to submit post-fill cap-trim SELL for %s: %s", symbol, exc)
        return None

    _mark_symbol_pending(symbol, open_order_symbols)
    order = _await_order_resolution(trading_client, submitted.id)
    _mark_symbol_resolved(symbol, order, open_order_symbols)
    if order is None:
        logger.warning("Could not confirm post-fill cap-trim SELL order status for %s.", symbol)
        return None

    filled_qty = float(order.filled_qty) if order.filled_qty else 0.0
    if order.status == OrderStatus.REJECTED or filled_qty <= 0:
        logger.warning(
            "Post-fill cap-trim SELL for %s did not fill (status=%s).", symbol, order.status.value
        )
        return None

    filled_price = float(order.filled_avg_price) if order.filled_avg_price else 0.0
    filled_notional = filled_qty * filled_price
    logger.info(
        "POST-FILL CAP CORRECTION: trimmed %s by %.4f shares (~$%.2f of the $%.2f required).",
        symbol, filled_qty, filled_notional, excess_value,
    )
    _safe_log_trade(
        ticker=symbol,
        action="SELL",
        quantity=filled_qty,
        execution_price=filled_price,
        wacc=None,
        beta=None,
        conviction_score=None,
        altman_z_score=None,
    )
    return filled_notional if filled_notional > 0 else None


def _check_post_fill_caps(
    trading_client: TradingClient,
    positions: Dict[str, object],
    equity: float,
    top_picks: List[TickerAnalysis],
    dry_run: bool,
    open_order_symbols: Optional[set] = None,
) -> bool:
    """
    Recompute ACTUAL post-fill weights — not the intended pre-trade
    sizing — against MAX_POSITION_WEIGHT / MAX_SECTOR_WEIGHT. A market
    order's exact fill price/notional can't be perfectly predicted in
    advance, so some drift is expected; this is what catches it.

    A single POSITION over MAX_POSITION_WEIGHT is corrected immediately
    (a trim-to-cap SELL via `_trim_position_to_cap`) unless `dry_run` —
    there's exactly one unambiguous ticker to act on. A SECTOR over
    MAX_SECTOR_WEIGHT is NOT auto-corrected: trimming would require
    choosing which of potentially several tickers to cut, a policy
    decision this function isn't positioned to make safely — it's
    logged at warning level and left for the caller to report as an
    incomplete rebalance instead of silently deferring to "next run"
    indefinitely.

    Args:
        open_order_symbols: Passed straight through to
            `_trim_position_to_cap` — see its docstring. Callers should
            fetch this fresh (via `_open_order_symbols`) immediately
            before calling this function, so it reflects orders
            submitted earlier in the SAME run (e.g. a rebalance sell
            that hasn't resolved yet), not a stale snapshot from before
            the run started.

    Returns:
        True if the post-fill portfolio is within both caps (whether it
        always was, or a position breach was corrected here). False if
        any breach remains uncorrected — a sector breach, a `dry_run`
        detection, or a trim that failed/couldn't fully resolve —
        callers should report the run as an INCOMPLETE rebalance.
    """
    if not equity:
        return True

    sector_by_ticker = {pick.ticker: (pick.sector or "Unknown") for pick in top_picks}
    sector_totals: Dict[str, float] = {}
    fully_resolved = True

    for symbol, position in list(positions.items()):
        if getattr(position, "asset_class", AssetClass.US_EQUITY) != AssetClass.US_EQUITY:
            continue
        market_value = float(position.market_value)
        weight = market_value / equity

        if weight > MAX_POSITION_WEIGHT + _CAP_EPSILON:
            excess_value = market_value - (MAX_POSITION_WEIGHT * equity)
            if dry_run:
                logger.warning(
                    "POST-FILL CAP CHECK: %s actual weight %.2f%% exceeds MAX_POSITION_WEIGHT (%.0f%%) "
                    "after fills — dry run, not corrected.",
                    symbol, weight * 100, MAX_POSITION_WEIGHT * 100,
                )
                fully_resolved = False
            else:
                logger.warning(
                    "POST-FILL CAP CHECK: %s actual weight %.2f%% exceeds MAX_POSITION_WEIGHT (%.0f%%) "
                    "after fills — submitting a trim-to-cap SELL.",
                    symbol, weight * 100, MAX_POSITION_WEIGHT * 100,
                )
                trimmed_notional = _trim_position_to_cap(trading_client, symbol, excess_value, open_order_symbols)
                if trimmed_notional is None:
                    fully_resolved = False
                else:
                    # Prove the remaining weight from the CONFIRMED fill,
                    # never assume any positive fill was sufficient — a
                    # tiny partial fill must still be reported incomplete
                    # (see the module's Finding-1 fix notes). The
                    # tolerance here is a DOLLAR/notional amount (one
                    # cent + a float-precision epsilon), never a fixed
                    # weight-fraction — a fixed percentage tolerance
                    # would scale into real dollars on a larger account
                    # (see POST_TRIM_NOTIONAL_TOLERANCE_DOLLARS's own
                    # docstring) and silently accept a genuine remaining
                    # breach as "close enough."
                    remaining_market_value = max(market_value - trimmed_notional, 0.0)
                    maximum_allowed_value = MAX_POSITION_WEIGHT * equity
                    remaining_weight = remaining_market_value / equity
                    if remaining_market_value <= maximum_allowed_value + POST_TRIM_NOTIONAL_TOLERANCE_DOLLARS:
                        weight = min(remaining_weight, MAX_POSITION_WEIGHT)
                    else:
                        weight = remaining_weight
                        logger.warning(
                            "POST-FILL CAP CORRECTION for %s trimmed only $%.2f of the $%.2f required — "
                            "remaining value $%.2f still exceeds the $%.2f cap (+ $%.4f tolerance); "
                            "rebalance marked incomplete.",
                            symbol, trimmed_notional, excess_value, remaining_market_value,
                            maximum_allowed_value, POST_TRIM_NOTIONAL_TOLERANCE_DOLLARS,
                        )
                        fully_resolved = False

        sector = sector_by_ticker.get(symbol, "Unknown")
        sector_totals[sector] = sector_totals.get(sector, 0.0) + weight

    for sector, weight in sector_totals.items():
        if weight > MAX_SECTOR_WEIGHT + _CAP_EPSILON:
            logger.warning(
                "POST-FILL CAP CHECK: sector '%s' actual weight %.2f%% exceeds MAX_SECTOR_WEIGHT (%.0f%%) "
                "after fills — spans multiple positions, not auto-corrected; rebalance marked incomplete.",
                sector, weight * 100, MAX_SECTOR_WEIGHT * 100,
            )
            fully_resolved = False

    return fully_resolved


# --------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Autonomous sector-relative DCF paper trading execution.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run the full scan and print every order that would be placed, without submitting anything.",
    )
    parser.add_argument(
        "--top-n",
        type=_positive_int,
        default=DEFAULT_TOP_N,
        help=f"Number of top Conviction Score tickers to hold (default: {DEFAULT_TOP_N}). Must be a positive integer.",
    )
    args = parser.parse_args()

    # Fails closed (raises RuntimeError -> sys.exit(1) below) unless
    # APCA_API_BASE_URL is exactly Alpaca's paper endpoint — see
    # `load_config`'s docstring: there is no bypass of any kind. No
    # TradingClient is constructed before this check, and no scan runs
    # either.
    config = load_config()
    trading_client = build_trading_client(config)

    if not args.dry_run:
        try:
            ensure_schema()
        except Exception as exc:  # noqa: BLE001 - schema setup must never block trading itself
            logger.warning("Could not ensure database schema; trade telemetry may fail this run: %s", exc)

    # Verify Alpaca connectivity before running the (multi-minute) DCF scan,
    # so bad credentials fail fast rather than after a long wait.
    try:
        equity_before = float(trading_client.get_account().equity)
        positions = get_current_positions(trading_client)
    except APIError as exc:
        logger.error("Could not reach Alpaca with the configured credentials: %s", exc)
        sys.exit(1)

    # Purely informational: each order-submitting function below (see
    # `liquidate_non_target_positions`, `rebalance_target_positions`,
    # `_trim_position_to_cap`, `execute_spy_var_hedge`) independently
    # rechecks `_market_is_open` immediately before its own submission,
    # so a stale reading taken here — before the multi-minute scan even
    # starts — is never what actually gates an order. This just logs
    # a heads-up early; it does not (and must not) turn the whole run
    # into a dry run.
    if not _market_is_open(trading_client) and not args.dry_run:
        logger.warning(
            "Alpaca market clock reports the market is CLOSED at scan start. The scan/analysis/"
            "risk-calc below still runs; individual order submissions each recheck the clock "
            "immediately before submitting and will skip if still closed at that moment."
        )
    effective_dry_run = args.dry_run

    assumptions = DCFAssumptions()
    analyses, sector_medians, valuations, risk_free_rate = run_todays_scan(
        DEFAULT_SP500_TOP_100_TICKERS, assumptions
    )
    refresh_sector_median_cache(sector_medians, valuations, assumptions, risk_free_rate)

    top_picks = select_top_picks(analyses, args.top_n)
    if not top_picks:
        logger.error("No tickers passed the sector-relative filter and entry gates today; nothing to trade.")
        return
    target_tickers = {pick.ticker for pick in top_picks}
    analyses_by_ticker = {a.ticker: a for a in analyses}

    # Idempotency: fetched once, reused by every submission step below —
    # None means the lookup itself failed, in which case every submission
    # this run is skipped rather than risking a duplicate.
    open_order_symbols = None if effective_dry_run else _open_order_symbols(trading_client)

    liquidations = liquidate_non_target_positions(
        trading_client, positions, target_tickers, analyses_by_ticker, effective_dry_run, open_order_symbols
    )

    # A pick that just got liquidated (fully or partially) this run
    # (fell out of the Top N, or profit-taking) must not be immediately
    # rebought below.
    exited_symbols = {
        record["symbol"]
        for record in liquidations
        if record["status"].startswith(("LIQUIDATED", "PARTIALLY_LIQUIDATED", "DRY-RUN"))
    }
    rebalance_picks = [pick for pick in top_picks if pick.ticker not in exited_symbols]

    # Never size the rebalance off the pre-liquidation snapshot: refetch
    # now that this run's liquidations have actually been submitted and
    # confirmed above. A dry run never changed anything, so its original
    # snapshot is already accurate and refetching would be a wasted call.
    # Account state (equity/cash/buying power) is refetched at the same
    # time, for the same reason — liquidation proceeds/slippage mean the
    # pre-liquidation equity_before figure is no longer accurate, and buy
    # sizing must respect actual buying power, not an assumption that
    # cash freed up by a sell is instantly and fully available.
    if effective_dry_run:
        positions_for_rebalance = positions
        rebalance_equity = equity_before
        rebalance_buying_power = None
    else:
        positions_for_rebalance = get_current_positions(trading_client)
        refreshed_account = trading_client.get_account()
        rebalance_equity = float(refreshed_account.equity)
        rebalance_buying_power = float(refreshed_account.buying_power)

    buys = rebalance_target_positions(
        trading_client, positions_for_rebalance, rebalance_picks, rebalance_equity, effective_dry_run,
        open_order_symbols, buying_power=rebalance_buying_power,
    )

    equity_after = None
    if not effective_dry_run:
        equity_after = float(trading_client.get_account().equity)

    target_weights = calculate_inverse_beta_weights(top_picks)
    print_execution_report(
        analyses, sector_medians, top_picks, liquidations, buys, equity_before, equity_after,
        effective_dry_run, target_weights=target_weights,
    )

    # Confirmed post-fill position snapshot, used both for the post-fill
    # cap check and — below — for the portfolio risk calculation. A dry
    # run's positions never changed, so `positions_for_rebalance` is
    # already the accurate snapshot to reuse.
    if effective_dry_run:
        post_fill_positions = positions_for_rebalance
    else:
        post_fill_positions = get_current_positions(trading_client)
        # Refresh open-order state immediately before the corrective-trim
        # phase — ground truth from Alpaca, not the in-memory
        # approximation carried over from the liquidate/rebalance phases
        # above, since a sell submitted earlier in this same run may
        # still be genuinely open and must not be duplicated by a trim.
        trim_open_order_symbols = _open_order_symbols(trading_client)
        caps_ok = _check_post_fill_caps(
            trading_client, post_fill_positions, equity_after or equity_before, top_picks, dry_run=False,
            open_order_symbols=trim_open_order_symbols,
        )
        if not caps_ok:
            logger.warning(
                "REBALANCE INCOMPLETE: one or more position/sector caps remain breached after "
                "this run's corrections — see POST-FILL CAP CHECK warnings above."
            )

    # Portfolio risk telemetry: Monte Carlo VaR/CVaR on the CONFIRMED
    # post-fill EQUITY holdings (an option hedge position, if held, is
    # excluded — yfinance can't price an OCC option symbol the way this
    # simulation needs).
    reference_equity = equity_after if equity_after is not None else equity_before
    holdings = {
        symbol: float(position.market_value) / reference_equity
        for symbol, position in post_fill_positions.items()
        if reference_equity and getattr(position, "asset_class", AssetClass.US_EQUITY) == AssetClass.US_EQUITY
    }

    risk_result = calculate_portfolio_var(holdings)

    logger.info("*" * 88)
    if risk_result.is_ok:
        logger.info(
            "RISK METRICS: 1-Month 95%% VaR: %.2f%% | Expected Shortfall (CVaR): %.2f%%",
            risk_result.var_95 * 100,
            risk_result.cvar_95 * 100,
        )
    else:
        logger.warning("RISK METRICS UNAVAILABLE (%s): %s", risk_result.status, risk_result.message)
    logger.info("*" * 88)

    if risk_result.is_ok and reference_equity:
        portfolio_var_dollars = abs(reference_equity * risk_result.var_95)
        execute_spy_var_hedge(
            trading_client,
            portfolio_var_dollars,
            reference_equity,
            effective_dry_run,
            open_order_symbols,
            existing_positions=post_fill_positions,
        )
    else:
        logger.info("Skipping SPY VaR hedge: portfolio VaR is unavailable this run.")

    # Final database logging call of the run: a synthetic portfolio-level
    # "RISK_SNAPSHOT" row (ticker/action are placeholders — this row
    # carries no per-trade data) so the Next.js dashboard's /api/risk
    # route can read back the latest run's risk status without a separate
    # table. Written for every actual (non-dry-run) invocation, including
    # when VaR is unavailable — with var_95/cvar_95 left NULL, never a
    # zero/placeholder value, since that would be indistinguishable from a
    # real zero. Without a row on the unavailable path, the dashboard
    # would have no way to tell "VaR failed this run" from "just showing
    # an increasingly stale number from days ago."
    if not args.dry_run:
        _safe_log_trade(
            ticker="PORTFOLIO",
            action="RISK_SNAPSHOT",
            quantity=0.0,
            execution_price=0.0,
            wacc=None,
            beta=None,
            conviction_score=None,
            altman_z_score=None,
            var_95=risk_result.var_95 if risk_result.is_ok else None,
            cvar_95=risk_result.cvar_95 if risk_result.is_ok else None,
        )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    try:
        main()
    except RuntimeError as exc:
        logger.error("%s", exc)
        sys.exit(1)
