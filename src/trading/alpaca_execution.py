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

Altman Z-Score distress filter
---------------------------------
Before a ticker is even valued (and before WACC is computed for it), its
Altman Z-Score (`src.valuation.altman_z.calculate_altman_z`) is checked
against the standard Distress Zone threshold of 1.8. Any ticker whose
Z-Score is unavailable or below that threshold is rejected outright —
logged and excluded from Pass 1 valuation entirely, so it can never be
scored, ranked, or bought. This is a credit-health gate, independent of
(and applied before) the DCF valuation and Conviction Score pipeline.

Position sizing: Inverse Volatility Weighting on beta
-------------------------------------------------------
Rather than an equal weight per Top-N ticker, each pick's target weight
is `(1 / max(beta, 0.5)) / sum(1 / max(beta, 0.5) for all picks)` — so
lower-beta (less volatile) tickers receive proportionally more equity
than higher-beta ones. Beta is floored at 0.5 before inverting so an
artificially low-beta ticker can't dominate the allocation. See
`rebalance_target_positions` / `_inverse_risk`.

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
This script is built against Alpaca's **paper trading** environment. It
reads `APCA_API_KEY_ID` / `APCA_API_SECRET_KEY` / `APCA_API_BASE_URL`
(and, for trade telemetry, `DATABASE_URL`) from a local `.env` file
(never commit real keys — `.env` is already gitignored) and warns loudly,
without blocking, if `APCA_API_BASE_URL` doesn't look like Alpaca's paper
endpoint. It is "autonomous" by design (no interactive confirmation
prompt) so it can run unattended, e.g. from a scheduler — pass `--dry-run`
to preview the full scan and every order it *would* place without
submitting anything.

Usage:
    python -m src.trading.alpaca_execution [--dry-run] [--top-n 10]
"""

import argparse
import datetime
import logging
import os
import sys
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Dict, List, Optional

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from alpaca.common.exceptions import APIError
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderSide, TimeInForce
from alpaca.trading.requests import MarketOrderRequest

from src.api.sector_medians import save_sector_medians
from src.backtesting.historical_tester import (
    DEFAULT_SP500_TOP_100_TICKERS,
    DEFAULT_TOP_N,
    TickerAnalysis,
    ValuationResult,
    calculate_sector_median_price_to_intrinsic,
    compute_valuation,
    score_ticker,
)
from src.dcf_model.dcf import DEFAULT_BETA, DCFAssumptions
from src.utils.db import log_trade
from src.valuation.altman_z import calculate_altman_z

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(message)s")

MIN_BETA_FLOOR = 0.5  # floor beta at this level to prevent overallocating to low-beta anomalies
MIN_ORDER_NOTIONAL_USD = 1.00  # Alpaca's own minimum notional order size
ALTMAN_Z_DISTRESS_THRESHOLD = 1.8  # below this, Altman classifies a company as in the "Distress Zone"


@dataclass
class AlpacaConfig:
    """Alpaca credentials/endpoint loaded from `.env`."""

    api_key: str
    secret_key: str
    base_url: str

    @property
    def is_paper(self) -> bool:
        """Whether `base_url` looks like Alpaca's paper trading endpoint."""
        return "paper" in self.base_url.lower()


def load_config() -> AlpacaConfig:
    """
    Load Alpaca credentials from `.env` via `python-dotenv`.

    Returns:
        An AlpacaConfig.

    Raises:
        RuntimeError: If any of APCA_API_KEY_ID, APCA_API_SECRET_KEY, or
            APCA_API_BASE_URL is missing, with a message telling the user
            what to add to `.env` rather than a bare KeyError.
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
        logger.warning(
            "⚠️  APCA_API_BASE_URL (%s) does not look like Alpaca's paper trading "
            "endpoint. This script places real orders against whatever account "
            "these credentials point to — double check before proceeding.",
            base_url,
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


# --------------------------------------------------------------------------
# Today's two-pass sector-relative DCF scan
# --------------------------------------------------------------------------

def run_todays_scan(
    tickers: List[str], assumptions: Optional[DCFAssumptions] = None
) -> tuple:
    """
    Run the two-pass sector-relative DCF scan (Pass 1 valuation, sector
    median, Pass 2 filter + Conviction Score) as of today.

    Args:
        tickers: Universe of ticker symbols to scan.
        assumptions: DCF assumptions applied uniformly. Defaults to
            DCFAssumptions() (dynamic, per-company historical growth/margin).

    Returns:
        (analyses, sector_medians, valuations) — the final TickerAnalysis
        list, the sector -> median P/IV dict, and the raw Pass 1
        ValuationResult list (used to refresh the API's sector-median
        cache).
    """
    assumptions = assumptions or DCFAssumptions()
    today = datetime.date.today().isoformat()

    logger.info("Running Pass 1: valuing %d tickers as of %s...", len(tickers), today)
    valuations: List[ValuationResult] = []
    for ticker in tickers:
        # Credit-health gate: reject distressed companies before WACC is
        # even computed for them, let alone before they're ranked or sized.
        z_score = calculate_altman_z(ticker)
        if z_score is None or z_score < ALTMAN_Z_DISTRESS_THRESHOLD:
            score_display = f"{z_score:.2f}" if z_score is not None else "unavailable"
            logger.warning(
                "REJECTED: %s failed Altman Z-Score check (score=%s, distress threshold=%.1f).",
                ticker,
                score_display,
                ALTMAN_Z_DISTRESS_THRESHOLD,
            )
            valuations.append(
                ValuationResult(
                    ticker=ticker,
                    as_of_date=today,
                    skip_reason=f"Failed Altman Z-Score distress filter (score={score_display}).",
                )
            )
            continue

        try:
            valuation = compute_valuation(ticker, today, assumptions)
            valuation = replace(valuation, altman_z_score=z_score)
        except Exception as exc:  # noqa: BLE001 - never let one bad ticker kill the run
            logger.warning("Unexpected failure valuing %s: %s", ticker, exc)
            valuation = ValuationResult(
                ticker=ticker, as_of_date=today, altman_z_score=z_score, skip_reason=f"Unexpected error: {exc}"
            )
        valuations.append(valuation)

    sector_medians = calculate_sector_median_price_to_intrinsic(valuations)

    logger.info("Running Pass 2: applying the sector-relative filter...")
    analyses: List[TickerAnalysis] = [score_ticker(v, sector_medians) for v in valuations]

    return analyses, sector_medians, valuations


def refresh_sector_median_cache(sector_medians: Dict[str, float], valuations: List[ValuationResult]) -> None:
    """
    Persist today's freshly computed sector medians to the live API's
    cache (`src.api.sector_medians`), so `GET /api/evaluate/{ticker}`
    stays reasonably current between trading runs too.
    """
    tickers_used = sum(1 for v in valuations if v.is_valid)
    cache = {
        "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "universe_size": len(valuations),
        "tickers_used": tickers_used,
        "sector_medians": sector_medians,
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
) -> None:
    """
    Call `log_trade`, but never let a telemetry failure (e.g. Postgres
    unreachable, `DATABASE_URL` unset) abort or interrupt trade execution.
    Failures are logged as warnings and swallowed.
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
        )
    except Exception as exc:  # noqa: BLE001 - telemetry must never block execution
        logger.warning("Trade telemetry logging failed for %s %s: %s", action, ticker, exc)


def liquidate_non_target_positions(
    trading_client: TradingClient,
    positions: Dict[str, object],
    target_tickers: set,
    analyses_by_ticker: Dict[str, TickerAnalysis],
    dry_run: bool,
) -> List[dict]:
    """
    Fully liquidate any held position not in `target_tickers`.

    Args:
        trading_client: An initialized alpaca-py TradingClient.
        positions: Current positions, keyed by symbol (from
            `get_current_positions`).
        target_tickers: Symbols that should remain/be established.
        analyses_by_ticker: This run's full scan results, keyed by
            ticker, used to attach WACC/beta/Conviction Score to the
            trade log when a liquidated ticker happened to be scored
            (even though it isn't in the Top N).
        dry_run: If True, log what would be liquidated without submitting
            any order.

    Returns:
        A list of {"symbol", "qty", "market_value", "status"} dicts, one
        per position considered for liquidation.
    """
    results = []
    for symbol, position in positions.items():
        if symbol in target_tickers:
            continue

        record = {
            "symbol": symbol,
            "qty": position.qty,
            "market_value": float(position.market_value),
        }

        if dry_run:
            record["status"] = "DRY-RUN (would liquidate)"
            results.append(record)
            continue

        try:
            order = trading_client.close_position(symbol)
            record["status"] = "LIQUIDATED"

            filled_qty = float(order.filled_qty) if order.filled_qty else float(position.qty)
            filled_price = (
                float(order.filled_avg_price) if order.filled_avg_price else float(position.current_price)
            )
            analysis = analyses_by_ticker.get(symbol)
            _safe_log_trade(
                ticker=symbol,
                action="SELL",
                quantity=filled_qty,
                execution_price=filled_price,
                wacc=analysis.wacc if analysis else None,
                beta=analysis.beta if analysis else None,
                conviction_score=analysis.conviction_score if analysis else None,
                altman_z_score=analysis.altman_z_score if analysis else None,
            )
        except APIError as exc:
            logger.warning("Failed to liquidate %s: %s", symbol, exc)
            record["status"] = f"FAILED ({exc})"

        results.append(record)

    return results


def _inverse_risk(beta: Optional[float]) -> float:
    """1 / beta, flooring beta at MIN_BETA_FLOOR to cap overallocation to
    artificially low-beta anomalies. Missing beta falls back to DEFAULT_BETA
    (matching the same fallback `calculate_wacc` uses)."""
    return 1.0 / max(beta if beta is not None else DEFAULT_BETA, MIN_BETA_FLOOR)


def rebalance_target_positions(
    trading_client: TradingClient,
    positions: Dict[str, object],
    top_picks: List[TickerAnalysis],
    equity: float,
    dry_run: bool,
) -> List[dict]:
    """
    Buy each Top-N ticker up to a dynamic, risk-adjusted target notional
    value using Inverse Volatility Weighting on beta: total_equity is
    allocated across the Top-N picks proportionally to each ticker's
    inverse beta (1 / beta, floored at MIN_BETA_FLOOR), so lower-beta
    (less volatile) tickers receive a larger weight than higher-beta ones.
    Tickers already at or above target are skipped; tickers with an
    existing position only get an order for the remaining delta, not the
    full target amount again.

    Args:
        trading_client: An initialized alpaca-py TradingClient.
        positions: Current positions (pre-liquidation snapshot is fine —
            liquidation only touches non-target symbols), keyed by symbol.
        top_picks: This run's Top-N TickerAnalysis, ranked by Conviction Score.
        equity: Current account equity.
        dry_run: If True, log what would be bought without submitting
            any order.

    Returns:
        A list of {"symbol", "target_notional", "current_notional",
        "order_notional", "status"} dicts, one per Top-N ticker.
    """
    total_inverse_risk = sum(_inverse_risk(pick.beta) for pick in top_picks)
    results = []

    for pick in top_picks:
        symbol = pick.ticker
        weight = _inverse_risk(pick.beta) / total_inverse_risk
        target_notional = equity * weight

        current_position = positions.get(symbol)
        current_notional = float(current_position.market_value) if current_position else 0.0
        order_notional = target_notional - current_notional

        record = {
            "symbol": symbol,
            "target_notional": target_notional,
            "current_notional": current_notional,
            "order_notional": max(order_notional, 0.0),
        }

        if order_notional < MIN_ORDER_NOTIONAL_USD:
            record["status"] = "SKIPPED (already at/above target weight)"
            results.append(record)
            continue

        if dry_run:
            record["status"] = "DRY-RUN (would buy)"
            results.append(record)
            continue

        try:
            order = trading_client.submit_order(
                order_data=MarketOrderRequest(
                    symbol=symbol,
                    notional=round(order_notional, 2),
                    side=OrderSide.BUY,
                    time_in_force=TimeInForce.DAY,
                )
            )
            record["status"] = "ORDER SUBMITTED"

            filled_price = float(order.filled_avg_price) if order.filled_avg_price else pick.historical_price
            filled_qty = (
                float(order.filled_qty)
                if order.filled_qty
                else (order_notional / filled_price if filled_price else 0.0)
            )
            _safe_log_trade(
                ticker=symbol,
                action="BUY",
                quantity=filled_qty,
                execution_price=filled_price if filled_price else 0.0,
                wacc=pick.wacc,
                beta=pick.beta,
                conviction_score=pick.conviction_score,
                altman_z_score=pick.altman_z_score,
            )
        except APIError as exc:
            logger.warning("Failed to submit buy order for %s: %s", symbol, exc)
            record["status"] = f"FAILED ({exc})"

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
) -> None:
    """Print a clean, human-readable execution report to stdout."""
    valid_count = sum(1 for a in analyses if a.is_valid)
    mode = "DRY RUN" if dry_run else "LIVE (paper)"

    print("=" * 88)
    print(f"ALPACA AUTONOMOUS EXECUTION — {mode} — {datetime.date.today().isoformat()}")
    print("=" * 88)

    print(f"\nScanned {len(analyses)} tickers; {valid_count} passed the sector-relative filter.")
    print("\nSector Median P/IV:")
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

    print(f"\nLiquidations ({len(liquidations)} position(s) held but no longer in the Top {len(top_picks)}):")
    if liquidations:
        for record in liquidations:
            print(
                f"  - {record['symbol']:<6} qty={record['qty']:>10}  "
                f"market_value=${record['market_value']:>12,.2f}  [{record['status']}]"
            )
    else:
        print("  (none — no held position fell out of the Top N)")

    print("\nBuys / Rebalances (inverse-beta risk-adjusted target weight each):")
    for record in buys:
        print(
            f"  - {record['symbol']:<6} target=${record['target_notional']:>10,.2f}  "
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
        type=int,
        default=DEFAULT_TOP_N,
        help=f"Number of top Conviction Score tickers to hold (default: {DEFAULT_TOP_N}).",
    )
    args = parser.parse_args()

    config = load_config()
    trading_client = build_trading_client(config)

    # Verify Alpaca connectivity before running the (multi-minute) DCF scan,
    # so bad credentials fail fast rather than after a long wait.
    try:
        equity_before = float(trading_client.get_account().equity)
        positions = get_current_positions(trading_client)
    except APIError as exc:
        logger.error("Could not reach Alpaca with the configured credentials: %s", exc)
        sys.exit(1)

    analyses, sector_medians, valuations = run_todays_scan(DEFAULT_SP500_TOP_100_TICKERS)
    refresh_sector_median_cache(sector_medians, valuations)

    top_picks = select_top_picks(analyses, args.top_n)
    if not top_picks:
        logger.error("No tickers passed the sector-relative filter today; nothing to trade.")
        return
    target_tickers = {pick.ticker for pick in top_picks}
    analyses_by_ticker = {a.ticker: a for a in analyses}

    liquidations = liquidate_non_target_positions(
        trading_client, positions, target_tickers, analyses_by_ticker, args.dry_run
    )
    buys = rebalance_target_positions(
        trading_client, positions, top_picks, equity_before, args.dry_run
    )

    equity_after = None
    if not args.dry_run:
        equity_after = float(trading_client.get_account().equity)

    print_execution_report(
        analyses, sector_medians, top_picks, liquidations, buys, equity_before, equity_after, args.dry_run
    )


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as exc:
        logger.error("%s", exc)
        sys.exit(1)
