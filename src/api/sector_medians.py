"""
Sector median Price / Intrinsic Value (P/IV) cache for the live API.

`GET /api/evaluate/{ticker}` values a single ticker against *current* data
on demand — there's no "universe" in that request to compare it to. A
true sector-relative comparison (as used in `src.backtesting.historical_tester`)
requires valuing every ticker in a reference universe and taking each
sector's median, which means running the full DCF pipeline for ~100
tickers — far too slow to do synchronously inside a single HTTP request.

Instead, sector medians are precomputed by `generate_sector_medians`
(run manually / periodically, not on the request path) against the
current-data DCF, cached to `data/sector_medians.json`, and looked up
instantly at request time by `get_sector_median_price_to_intrinsic`.

This cache goes stale as prices and fundamentals move — regenerate it by
running this module directly:

    python -m src.api.sector_medians

Every ticker in a given run is valued with the same discount-rate macro
input: `src.utils.macro.get_risk_free_rate` is called once per run (not
once per ticker) and applied uniformly, so the cached medians reflect one
consistent snapshot of the risk-free rate — the same helper the live
single-ticker API (`src.api.main`) and the backtester
(`src.backtesting.historical_tester`) use, keeping WACC synchronized
across the whole application.

The cache also records the exact DCF assumptions, risk-free rate,
generation timestamp, universe size, valid-ticker count, and a per-sector
sample count. `get_sector_median_price_to_intrinsic` uses this metadata
to refuse a comparison — returning `None` plus an explicit reason,
never a misleading number — when the cache is stale, was generated with
different assumptions OR a materially different risk-free rate than the
current request (a cache generated when the 10Y Treasury yield was, say,
1% is not a valid peer-comparison denominator for a ticker valued today
at 5% -- the two P/IV ratios were computed under different discount-rate
regimes and are not comparable even if every other assumption matches),
or a sector's sample size is too small to trust.

This is the SAME cache file `src.trading.alpaca_execution.refresh_sector_median_cache`
writes to after each live trading scan. That's intentional, not a bug to
route around: as long as both writers value the full, unscreened
reference universe (which the trading engine now does — its Altman/
trend/Piotroski/RSI entry gates apply strictly after every ticker has
been valued, never before) and stamp the same metadata described above,
"freshest wins" is the correct behavior for a single shared cache, and a
consumer that cares about provenance can already tell a stale/incompatible
write from a good one via the metadata rather than needing a second file.
"""

import datetime
import json
import logging
import os
import statistics
import sys
import tempfile
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.api.sector_median_thresholds import (
    CACHE_MAX_STALENESS,
    MAX_FUTURE_SKEW,
    MIN_OVERALL_COVERAGE_FRACTION,
    MIN_SECTOR_SAMPLE_SIZE,
    RISK_FREE_RATE_COMPARISON_TOLERANCE,
    SectorMedianUnavailableCode,
    _is_valid_finite_number,
    _is_valid_nonneg_int,
)
from src.data_ingestion.fetch_financials import fetch_company_financials
from src.dcf_model.dcf import DCFAssumptions, run_dcf_valuation
from src.utils.macro import get_risk_free_rate
from src.utils.ticker_universe import DEFAULT_SP500_TOP_100_TICKERS

logger = logging.getLogger(__name__)

CACHE_PATH = Path(__file__).resolve().parent / "data" / "sector_medians.json"

EMPTY_CACHE = {
    "generated_at": None,
    "universe_size": 0,
    "tickers_used": 0,
    "risk_free_rate": None,
    "assumptions": None,
    "sector_medians": {},
    "sector_sample_counts": {},
}

# Internal-only marker key `load_sector_medians` stamps onto an
# EMPTY_CACHE-shaped fallback when the cache file's content parsed as
# valid JSON but the wrong TOP-LEVEL shape (e.g. `[]`, `null`, a bare
# string/number/bool) or contained genuinely invalid JSON syntax --
# distinct from a cache that simply doesn't exist yet. Never present on
# a real generated cache; checked (and stripped from view) by
# `get_sector_median_price_to_intrinsic` so a caller gets a specific
# "malformed" reason rather than the generic "not been generated yet"
# one, without changing either function's public return type.
_MALFORMED_CACHE_REASON_KEY = "_malformed_cache_reason"


def _serialize_comparable_assumptions(assumptions: DCFAssumptions) -> dict:
    """
    The subset of `DCFAssumptions` that determines whether a cached P/IV
    is comparable to a freshly-computed one for the SAME ticker/sector —
    `risk_free_rate` is tracked separately (it's a run-wide macro input,
    not a per-comparison override), and the capital-structure defaults
    (da/capex/nwc %, market_risk_premium) don't materially change P/IV
    rankings the way growth/margin/terminal-growth overrides do.
    """
    return {
        "revenue_growth_rate": assumptions.revenue_growth_rate,
        "operating_margin": assumptions.operating_margin,
        "terminal_growth_rate": assumptions.terminal_growth_rate,
    }


def _compute_current_price_to_intrinsic(ticker: str, assumptions: DCFAssumptions) -> Optional[Dict]:
    """
    Value a single ticker against *current* (not point-in-time) data and
    compute its Price / Intrinsic Value ratio and sector.

    Returns:
        dict with "sector" and "price_to_intrinsic", or None if the
        ticker's data is unavailable or the valuation isn't usable.
    """
    try:
        financial_data = fetch_company_financials(ticker)
    except ValueError as exc:
        logger.warning("Skipping %s: %s", ticker, exc)
        return None

    try:
        result = run_dcf_valuation(financial_data, assumptions)
    except ValueError as exc:
        logger.warning("Skipping %s: DCF valuation failed: %s", ticker, exc)
        return None

    intrinsic_value = result["intrinsic_value_per_share"]
    current_price = result["current_market_price"]
    if not intrinsic_value or intrinsic_value <= 0 or not current_price:
        logger.warning("Skipping %s: invalid price/intrinsic value.", ticker)
        return None

    return {
        "sector": financial_data.get("sector", "Unknown"),
        "price_to_intrinsic": current_price / intrinsic_value,
    }


def generate_sector_medians(
    tickers: Optional[List[str]] = None,
    assumptions: Optional[DCFAssumptions] = None,
    compute_ticker: Optional[Callable[[str, DCFAssumptions], Optional[Dict]]] = None,
) -> dict:
    """
    Value every ticker in `tickers` against current data, group by
    sector, and compute each sector's median P/IV and sample count.

    Args:
        tickers: Universe to value. Defaults to
            `DEFAULT_SP500_TOP_100_TICKERS`.
        assumptions: DCF assumptions applied uniformly. Defaults to
            `DCFAssumptions()` (dynamic, per-company historical growth/margin).
            Its `risk_free_rate` is overridden with a single live 10-Year
            Treasury yield (`src.utils.macro.get_risk_free_rate`), fetched
            once and reused for every ticker, so the whole cache reflects
            one consistent macro snapshot rather than ~100 independent
            (and potentially slightly different) quotes.
        compute_ticker: Overrides how each ticker is valued. Defaults to
            `_compute_current_price_to_intrinsic`. `src.api.publish_sector_medians`
            passes a retrying wrapper here so a single transient failure
            (a network blip, a rate limit) can retry JUST that one
            ticker with backoff, without regenerating the tickers that
            already succeeded or re-running the whole universe.

    Returns:
        dict with "generated_at" (ISO timestamp), "universe_size",
        "tickers_used", "risk_free_rate", "assumptions" (the comparable
        subset — see `_serialize_comparable_assumptions`), "sector_medians"
        (sector -> median P/IV), and "sector_sample_counts" (sector ->
        number of valid tickers backing that median).
    """
    tickers = tickers if tickers is not None else DEFAULT_SP500_TOP_100_TICKERS
    assumptions = assumptions or DCFAssumptions()
    compute_ticker = compute_ticker if compute_ticker is not None else _compute_current_price_to_intrinsic

    risk_free_rate = get_risk_free_rate()
    assumptions = replace(assumptions, risk_free_rate=risk_free_rate)

    ratios_by_sector: Dict[str, List[float]] = {}
    tickers_used = 0
    for ticker in tickers:
        try:
            valuation = compute_ticker(ticker, assumptions)
        except Exception as exc:  # noqa: BLE001 - never let one bad ticker kill the run
            logger.warning("Unexpected failure valuing %s: %s", ticker, exc)
            continue
        if valuation is None:
            continue
        tickers_used += 1
        ratios_by_sector.setdefault(valuation["sector"], []).append(valuation["price_to_intrinsic"])

    sector_medians = {sector: statistics.median(ratios) for sector, ratios in ratios_by_sector.items()}
    sector_sample_counts = {sector: len(ratios) for sector, ratios in ratios_by_sector.items()}

    return {
        "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "universe_size": len(tickers),
        "tickers_used": tickers_used,
        "risk_free_rate": risk_free_rate,
        "assumptions": _serialize_comparable_assumptions(assumptions),
        "sector_medians": sector_medians,
        "sector_sample_counts": sector_sample_counts,
    }


def save_sector_medians(cache: dict, path: Path = CACHE_PATH) -> None:
    """
    Write a `generate_sector_medians`-shaped result to disk as JSON,
    atomically: the payload is written to a uniquely-named temporary file
    in the SAME directory (so the later rename stays on one filesystem),
    then moved into place with `os.replace`, which is atomic on both
    POSIX and Windows. This cache has two independent writers (this
    module and `src.trading.alpaca_execution.refresh_sector_median_cache`
    -- see the module docstring's "freshest wins" note), so a concurrent
    reader (`load_sector_medians`, on every live API request) must never
    be able to observe a partially-written file mid-write. The temporary
    file is cleaned up if the write itself fails before the replace.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(cache, f, indent=2)
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def load_sector_medians(path: Path = CACHE_PATH) -> dict:
    """
    Load the cached sector medians from disk.

    Returns:
        The cache dict, or an `EMPTY_CACHE`-shaped fallback (never raises,
        since a missing or malformed cache should degrade to
        "unavailable" rather than break the live API with a 500) if:
          - the file doesn't exist yet (e.g. before the first generation
            run) -- returns a bare `EMPTY_CACHE` copy;
          - the file contains invalid or unsupported JSON content (including
            an integer beyond Python's safe digit-conversion limit), or can't
            be read/decoded (an I/O error, or content that isn't valid UTF-8) --
            returns an `EMPTY_CACHE` copy tagged with
            `_MALFORMED_CACHE_REASON_KEY`;
          - the file contains syntactically VALID JSON whose top-level
            value isn't a JSON object (e.g. `[]`, `null`, a bare string,
            number, or bool) -- same tagged fallback. Every downstream
            consumer of this cache calls `.get(...)` on the top-level
            value; a non-dict top level previously reached that call
            directly and leaked a raw `AttributeError`
            (`'list' object has no attribute 'get'`) instead of being
            recognized here as an unusable cache.
        `get_sector_median_price_to_intrinsic` checks for the tag to
        report a specific "malformed" reason distinct from "not been
        generated yet" — this function's own return type/contract
        (always a plain `dict`, shaped like a cache payload) is
        otherwise unchanged.
    """
    if not path.exists():
        logger.warning("Sector median cache not found at %s; run `python -m src.api.sector_medians`.", path)
        return dict(EMPTY_CACHE)

    try:
        with open(path) as f:
            payload = json.load(f)
    except (ValueError, UnicodeDecodeError) as exc:
        logger.warning(
            "Sector median cache at %s contains invalid or unsupported JSON content (%s); "
            "treating as unavailable.",
            path,
            exc,
        )
        malformed = dict(EMPTY_CACHE)
        malformed[_MALFORMED_CACHE_REASON_KEY] = f"invalid or unsupported JSON content ({exc})"
        return malformed
    except OSError as exc:
        logger.warning("Sector median cache at %s could not be read (%s); treating as unavailable.", path, exc)
        return dict(EMPTY_CACHE)

    if not isinstance(payload, dict):
        logger.warning(
            "Sector median cache at %s has an invalid top-level shape (expected a JSON object, got %s); "
            "treating as unavailable.",
            path,
            type(payload).__name__,
        )
        malformed = dict(EMPTY_CACHE)
        malformed[_MALFORMED_CACHE_REASON_KEY] = (
            f"top-level JSON value is a {type(payload).__name__}, not an object"
        )
        return malformed

    return payload


def _weekend_adjusted_max_staleness(
    generated_at: datetime.datetime,
    now: datetime.datetime,
    base: datetime.timedelta,
) -> datetime.timedelta:
    """
    Extend `base` by one day for every Saturday/Sunday calendar date that
    falls within `[generated_at, now)`, so a snapshot generated before a
    weekend (when nothing regenerates it — the refresh workflow runs on
    trading days) isn't refused as stale purely because non-trading
    weekend days elapsed with no new data to generate from. A snapshot
    generated Friday and checked the following Monday morning spans
    exactly two weekend dates and gets two extra days of headroom; a
    snapshot generated and checked entirely within a single work week
    gets none, so staleness on an ordinary weekday is unchanged from
    `base`. Both arguments must be timezone-aware; this never raises.
    """
    extra_days = 0
    cursor = generated_at.date()
    end = now.date()
    while cursor < end:
        if cursor.weekday() >= 5:  # Saturday=5, Sunday=6
            extra_days += 1
        cursor += datetime.timedelta(days=1)
    return base + datetime.timedelta(days=extra_days)


def _evaluate_sector_median_cache_full(
    cache: dict,
    sector: str,
    assumptions: Optional[DCFAssumptions] = None,
    max_staleness: datetime.timedelta = CACHE_MAX_STALENESS,
    min_sample_size: int = MIN_SECTOR_SAMPLE_SIZE,
    min_overall_coverage_fraction: float = MIN_OVERALL_COVERAGE_FRACTION,
    risk_free_rate_tolerance: float = RISK_FREE_RATE_COMPARISON_TOLERANCE,
    now: Optional[datetime.datetime] = None,
) -> Tuple[Optional[float], Optional[SectorMedianUnavailableCode], Optional[str]]:
    """
    The actual validation body behind both `evaluate_sector_median_cache`
    (the public 2-tuple contract `get_sector_median_price_to_intrinsic`
    and its own test suite rely on) and
    `get_live_sector_median_price_to_intrinsic` (which additionally needs
    a stable `SectorMedianUnavailableCode`, not just the free-text
    reason, to hand back to `/api/evaluate` callers). Single source of
    truth for the refusal logic so the two public entry points can never
    silently drift in what they consider trustworthy.

    Refusing means returning `(None, code, reason)` — never a misleading
    number or a leaked exception (`AttributeError`/`TypeError`/
    `ZeroDivisionError`/a raw JSON or timezone-arithmetic error) — when:
        - the cache is malformed (see `_MALFORMED_CACHE_REASON_KEY`) or
          has no entry for this sector,
        - the cache's generation timestamp is missing, unparseable, or not
          timezone-aware,
        - the cache is older than `max_staleness` (extended over any
          weekend dates it spans — see `_weekend_adjusted_max_staleness`),
        - too small a share of the WHOLE universe was successfully valued
          this run (`min_overall_coverage_fraction`) — a systemic problem
          with the run itself, independent of any one sector's own count,
        - `assumptions` is given and doesn't match what the cache was
          generated with (see `_serialize_comparable_assumptions`), OR
          `assumptions.risk_free_rate` differs from the cache's own
          generation-time risk-free rate by more than
          `risk_free_rate_tolerance` — see `RISK_FREE_RATE_COMPARISON_TOLERANCE`,
        - the sector's sample size is below `min_sample_size`.

    This is the shared validation body behind both
    `get_sector_median_price_to_intrinsic` (file-based cache) and
    `get_live_sector_median_price_to_intrinsic` (Supabase-backed live
    store) — both loaders produce the exact same dict shape, so neither
    can silently apply different trust rules than the other.

    Args:
        cache: An already-loaded cache dict (see `load_sector_medians` /
            `src.api.sector_median_store.fetch_latest_snapshot`).
        sector: Sector name, e.g. "Technology".
        assumptions: The DCF assumptions the CALLER is about to compare
            against this median, including the risk-free rate it actually
            used for this specific valuation (`assumptions.risk_free_rate`
            — e.g. `src.api.main` always sets this to a live
            `get_risk_free_rate()` call before comparing). If omitted, the
            assumption- and risk-free-rate-compatibility checks are both
            skipped (the caller is presumed to already know it's comparing
            like-for-like — e.g. an internal batch job using the cache's
            own generation assumptions).
        max_staleness: Maximum cache age before it's refused (before any
            weekend adjustment).
        min_sample_size: Minimum per-sector sample count before it's refused.
        min_overall_coverage_fraction: Minimum fraction of the WHOLE
            universe (`tickers_used / universe_size`) that must have been
            successfully valued before ANY sector's median from this
            cache is trusted, regardless of that sector's own count.
        risk_free_rate_tolerance: Maximum allowed absolute difference
            between the cache's generation-time risk-free rate and the
            caller's own, before the comparison is refused as having been
            computed under materially different discount-rate regimes.
        now: Overrides "the current time" (for deterministic weekend-
            staleness tests). Defaults to the real current UTC time;
            production callers never pass this.

    Returns:
        (median_p_iv, unavailable_code, unavailable_reason) — `median_p_iv`
        is None iff `unavailable_code`/`unavailable_reason` are set.
    """
    UNAVAILABLE = SectorMedianUnavailableCode.SNAPSHOT_UNAVAILABLE
    INCOMPATIBLE = SectorMedianUnavailableCode.INCOMPATIBLE_ASSUMPTIONS
    INSUFFICIENT = SectorMedianUnavailableCode.INSUFFICIENT_PEERS

    malformed_reason = cache.get(_MALFORMED_CACHE_REASON_KEY)
    if malformed_reason is not None:
        return None, UNAVAILABLE, f"Sector median cache is malformed: {malformed_reason}."

    generated_at = cache.get("generated_at")
    if generated_at is None:
        return None, UNAVAILABLE, "Sector median cache has not been generated yet."

    try:
        generated_at_ts = datetime.datetime.fromisoformat(generated_at)
    except (TypeError, ValueError):
        return None, UNAVAILABLE, "Sector median cache has an unparseable generation timestamp."

    if generated_at_ts.tzinfo is None:
        # A genuine write always stamps an explicit UTC-aware timestamp
        # (`datetime.now(timezone.utc)`); a timezone-naive value here
        # means the cache was hand-edited or written by something other
        # than this module's own writer, and guessing which timezone was
        # intended risks silently comparing against a cache that is
        # actually far staler (or fresher) than it appears. Refused
        # cleanly rather than crashing on the aware-vs-naive subtraction
        # below.
        return None, UNAVAILABLE, "Sector median cache has a timezone-naive generation timestamp."

    now = now if now is not None else datetime.datetime.now(datetime.timezone.utc)
    if generated_at_ts > now + MAX_FUTURE_SKEW:
        # A materially-future timestamp can only be clock skew, a bug, or
        # tampered/corrupted data -- refused on the READ path too (not
        # just at publish time), the same way a too-stale one is, rather
        # than trusting a snapshot dated after "now".
        return None, UNAVAILABLE, "Sector median cache has a generated_at timestamp in the future."

    effective_max_staleness = _weekend_adjusted_max_staleness(generated_at_ts, now, max_staleness)
    if now - generated_at_ts > effective_max_staleness:
        return None, UNAVAILABLE, (
            f"Sector median cache is stale (generated {generated_at}; "
            f"max age {effective_max_staleness})."
        )

    universe_size = cache.get("universe_size")
    tickers_used = cache.get("tickers_used")
    if not _is_valid_nonneg_int(universe_size):
        return None, UNAVAILABLE, "Sector median cache is unhealthy: universe_size is missing or invalid."
    if not _is_valid_nonneg_int(tickers_used):
        return None, UNAVAILABLE, "Sector median cache is unhealthy: tickers_used is missing or invalid."
    if universe_size <= 0:
        return None, UNAVAILABLE, "Sector median cache is unhealthy: universe_size is zero or missing."
    if tickers_used > universe_size:
        return None, UNAVAILABLE, (
            f"Sector median cache is unhealthy: tickers_used ({tickers_used}) "
            f"exceeds universe_size ({universe_size})."
        )
    coverage = tickers_used / universe_size
    if coverage < min_overall_coverage_fraction:
        return None, UNAVAILABLE, (
            f"Sector median cache is unhealthy: only {tickers_used}/{universe_size} "
            f"({coverage:.0%}) of the universe was successfully valued this run "
            f"(minimum {min_overall_coverage_fraction:.0%})."
        )

    if assumptions is not None:
        cached_assumptions = cache.get("assumptions")
        if cached_assumptions is not None and not isinstance(cached_assumptions, dict):
            return None, UNAVAILABLE, "Sector median cache has a malformed assumptions container."
        requested_assumptions = _serialize_comparable_assumptions(assumptions)
        if cached_assumptions != requested_assumptions:
            return None, INCOMPATIBLE, "Sector median cache was generated with different DCF assumptions."

        cached_risk_free_rate = cache.get("risk_free_rate")
        if not _is_valid_finite_number(cached_risk_free_rate):
            return None, UNAVAILABLE, "Sector median cache has a missing or invalid risk-free rate."

        requested_risk_free_rate = assumptions.risk_free_rate
        if not _is_valid_finite_number(requested_risk_free_rate):
            return None, UNAVAILABLE, "The requested risk-free rate is not a finite number."

        # Both operands already passed `_is_valid_finite_number` above --
        # true for ANY `int` (arbitrary-precision, always finite by
        # definition) or finite `float` -- but subtracting an
        # astronomically large `int` (e.g. `10**10000`) from a `float`
        # still raises `OverflowError` when Python converts the int side
        # to a C `double`. Caught here, narrowly, rather than letting it
        # escape this function's own "never raises, always
        # (None, code, reason)" contract.
        try:
            risk_free_rate_diff = abs(requested_risk_free_rate - cached_risk_free_rate)
        except (ArithmeticError, OverflowError) as exc:
            return None, UNAVAILABLE, f"Sector median cache risk-free rate comparison failed: {exc}."
        if not _is_valid_finite_number(risk_free_rate_diff):
            return None, UNAVAILABLE, "Sector median cache risk-free rate comparison produced a non-finite result."

        if risk_free_rate_diff > risk_free_rate_tolerance:
            return None, INCOMPATIBLE, (
                "Sector median cache was generated with a different risk-free rate "
                f"({cached_risk_free_rate:.4%} vs. the requested {requested_risk_free_rate:.4%}) "
                "-- the two P/IV ratios were computed under different discount-rate regimes "
                "and are not comparable."
            )

    sector_medians_map = cache.get("sector_medians")
    if not isinstance(sector_medians_map, dict):
        return None, UNAVAILABLE, "Sector median cache has a malformed sector_medians container."
    median = sector_medians_map.get(sector)
    if median is None:
        return None, UNAVAILABLE, f"No cached sector median for sector '{sector}'."
    if not _is_valid_finite_number(median):
        return None, UNAVAILABLE, f"Sector median cache has an invalid median value for sector '{sector}'."
    if median <= 0:
        return None, UNAVAILABLE, f"Sector median cache has a non-positive median value for sector '{sector}'."

    sector_sample_counts_map = cache.get("sector_sample_counts")
    if not isinstance(sector_sample_counts_map, dict):
        return None, UNAVAILABLE, "Sector median cache has a malformed sector_sample_counts container."
    sample_count = sector_sample_counts_map.get(sector, 0)
    if not _is_valid_nonneg_int(sample_count):
        # A malformed count (a non-integer type, a negative number) is a
        # data-integrity problem with the snapshot itself -- distinct
        # from a GENUINE, validly-counted sector that simply has too few
        # samples (handled separately below with INSUFFICIENT_PEERS).
        return None, UNAVAILABLE, f"Sector '{sector}' has an invalid sample count in the cache."
    if sample_count < min_sample_size:
        return None, INSUFFICIENT, (
            f"Sector '{sector}' has only {sample_count!r} sample(s) in the cache "
            f"(minimum {min_sample_size})."
        )

    return median, None, None


def evaluate_sector_median_cache(
    cache: dict,
    sector: str,
    assumptions: Optional[DCFAssumptions] = None,
    max_staleness: datetime.timedelta = CACHE_MAX_STALENESS,
    min_sample_size: int = MIN_SECTOR_SAMPLE_SIZE,
    min_overall_coverage_fraction: float = MIN_OVERALL_COVERAGE_FRACTION,
    risk_free_rate_tolerance: float = RISK_FREE_RATE_COMPARISON_TOLERANCE,
    now: Optional[datetime.datetime] = None,
) -> Tuple[Optional[float], Optional[str]]:
    """
    Public 2-tuple wrapper around `_evaluate_sector_median_cache_full` —
    drops the stable `SectorMedianUnavailableCode` and keeps only
    `(median_p_iv, unavailable_reason)`. This is the contract
    `get_sector_median_price_to_intrinsic` and
    `tests/api/test_sector_medians.py` already depend on; see
    `_evaluate_sector_median_cache_full` for the full refusal contract
    and `get_live_sector_median_price_to_intrinsic` for the typed,
    code-carrying result the live `/api/evaluate` path uses instead.
    """
    median, _code, reason = _evaluate_sector_median_cache_full(
        cache,
        sector,
        assumptions=assumptions,
        max_staleness=max_staleness,
        min_sample_size=min_sample_size,
        min_overall_coverage_fraction=min_overall_coverage_fraction,
        risk_free_rate_tolerance=risk_free_rate_tolerance,
        now=now,
    )
    return median, reason


def get_sector_median_price_to_intrinsic(
    sector: str,
    assumptions: Optional[DCFAssumptions] = None,
    path: Path = CACHE_PATH,
    max_staleness: datetime.timedelta = CACHE_MAX_STALENESS,
    min_sample_size: int = MIN_SECTOR_SAMPLE_SIZE,
    min_overall_coverage_fraction: float = MIN_OVERALL_COVERAGE_FRACTION,
    risk_free_rate_tolerance: float = RISK_FREE_RATE_COMPARISON_TOLERANCE,
    now: Optional[datetime.datetime] = None,
) -> Tuple[Optional[float], Optional[str]]:
    """
    Load the FILE-based cache at `path` and validate it via
    `evaluate_sector_median_cache` — see that function's docstring for
    the full refusal contract. This file-based lookup is no longer what
    the live `/api/evaluate` endpoint uses (see
    `get_live_sector_median_price_to_intrinsic`, which reads the
    Supabase-backed store instead) — it remains for local/manual use
    (the `python -m src.api.sector_medians` CLI below) and is what
    `tests/api/test_sector_medians.py` exercises directly against
    `tmp_path` fixtures.
    """
    cache = load_sector_medians(path)
    return evaluate_sector_median_cache(
        cache,
        sector,
        assumptions=assumptions,
        max_staleness=max_staleness,
        min_sample_size=min_sample_size,
        min_overall_coverage_fraction=min_overall_coverage_fraction,
        risk_free_rate_tolerance=risk_free_rate_tolerance,
        now=now,
    )


@dataclass(frozen=True)
class SectorMedianSnapshotProvenance:
    """
    Where a live sector-median comparison's denominator came from —
    typed so `src.api.main` can build its own response model directly
    from named attributes and never needs to read a raw snapshot dict or
    know anything about how `src.api.sector_median_store` shapes one.
    """

    generated_at: str
    universe_size: int
    tickers_used: int
    sector_sample_count: int


@dataclass(frozen=True)
class LiveSectorMedianResult:
    """
    The full result of a live sector-median lookup. Exactly one of
    `median`/`unavailable_code` is set. `unavailable_reason` is an
    internal, free-text diagnostic (never shown to an end user verbatim
    — see `SectorMedianUnavailableCode`'s own docstring for the stable,
    user-facing vocabulary a caller should actually switch on).
    `provenance` is populated whenever ANY snapshot was fetched, even one
    that failed validation for this specific sector/assumptions
    combination — it is `None` only when no snapshot could be fetched at
    all (`DATABASE_URL` unset, the database unreachable, or nothing has
    ever been published).
    """

    median: Optional[float]
    unavailable_code: Optional[SectorMedianUnavailableCode]
    unavailable_reason: Optional[str]
    provenance: Optional[SectorMedianSnapshotProvenance]


def _build_snapshot_provenance(snapshot: dict, sector: str) -> Optional[SectorMedianSnapshotProvenance]:
    """
    Builds provenance ONLY from structurally validated fields — never
    indexes or casts a raw snapshot value on the assumption that it's
    already valid, and never truncates a value with `int()` unless it
    has already passed `_is_valid_nonneg_int` (which only accepts a
    genuine `int`, so the cast that follows can never lose information).
    Returns `None` (never raises) if `generated_at` isn't a genuine,
    parseable, timezone-aware, non-future ISO string,
    `universe_size`/`tickers_used` aren't genuine non-negative INTEGERS
    (a `float` or a `bool` is refused, not silently coerced),
    `sector_sample_counts` isn't a dict, or this sector's own count in it
    isn't a genuine non-negative integer — a structurally untrustworthy
    snapshot (a missing key, an invalid count like `"corrupt"` or a
    fractional `3.9`, a malformed container, an invalid timestamp) must
    never produce a provenance object built from garbage.
    """
    generated_at = snapshot.get("generated_at")
    if not isinstance(generated_at, str) or not generated_at:
        return None
    try:
        generated_at_ts = datetime.datetime.fromisoformat(generated_at)
    except (TypeError, ValueError):
        return None
    if generated_at_ts.tzinfo is None:
        return None
    if generated_at_ts > datetime.datetime.now(datetime.timezone.utc) + MAX_FUTURE_SKEW:
        return None

    universe_size = snapshot.get("universe_size")
    tickers_used = snapshot.get("tickers_used")
    if not _is_valid_nonneg_int(universe_size) or not _is_valid_nonneg_int(tickers_used):
        return None

    sector_sample_counts = snapshot.get("sector_sample_counts")
    if not isinstance(sector_sample_counts, dict):
        return None
    sector_sample_count = sector_sample_counts.get(sector, 0)
    if not _is_valid_nonneg_int(sector_sample_count):
        return None

    return SectorMedianSnapshotProvenance(
        generated_at=generated_at,
        universe_size=int(universe_size),
        tickers_used=int(tickers_used),
        sector_sample_count=int(sector_sample_count),
    )


def get_live_sector_median_price_to_intrinsic(
    sector: str,
    assumptions: Optional[DCFAssumptions] = None,
) -> LiveSectorMedianResult:
    """
    The production lookup behind `/api/evaluate` (`src.api.main`): reads
    the newest PUBLISHED sector-median snapshot from the Supabase-backed
    store (`src.api.sector_median_store`, short in-process cached, read-
    only — see that module's own docstring) instead of the local
    `data/sector_medians.json` file `get_sector_median_price_to_intrinsic`
    reads — that file ships frozen into the Vercel deployment and can
    never be refreshed once deployed (see this module's own top-of-file
    docstring). Applies the exact same staleness/coverage/assumption/
    sample-size validation either way
    (`_evaluate_sector_median_cache_full`), so the two lookups can never
    silently drift in what they consider trustworthy.

    Returns a `LiveSectorMedianResult` — a public "never raises" contract
    enforced by a single outer guard (logging only the failing
    exception's class, never its message or any snapshot/database
    contents) on top of `_evaluate_sector_median_cache_full`'s own
    non-raising design and `_build_snapshot_provenance`'s structural
    validation. Never hands the caller a raw snapshot dict — `src.api.main`
    should never need to inspect one directly. `provenance` is `None`
    whenever it cannot be trusted (a missing key, an invalid count, a
    malformed container, or an invalid/future timestamp), even if a
    median could otherwise have been computed — in that case `median` is
    also forced to `None` with `unavailable_code=SNAPSHOT_UNAVAILABLE`,
    since a snapshot whose own shape can't be trusted shouldn't be used
    to report a comparison result either.
    """
    try:
        from src.api.sector_median_store import get_cached_latest_snapshot

        snapshot, fetch_reason = get_cached_latest_snapshot()
        if snapshot is None:
            return LiveSectorMedianResult(
                median=None,
                unavailable_code=SectorMedianUnavailableCode.SNAPSHOT_UNAVAILABLE,
                unavailable_reason=fetch_reason,
                provenance=None,
            )

        median, code, reason = _evaluate_sector_median_cache_full(snapshot, sector, assumptions=assumptions)

        provenance = _build_snapshot_provenance(snapshot, sector)
        if provenance is None:
            return LiveSectorMedianResult(
                median=None,
                unavailable_code=SectorMedianUnavailableCode.SNAPSHOT_UNAVAILABLE,
                unavailable_reason=reason if reason is not None else "Sector median snapshot has an invalid shape.",
                provenance=None,
            )

        return LiveSectorMedianResult(
            median=median, unavailable_code=code, unavailable_reason=reason, provenance=provenance
        )
    except Exception as exc:  # noqa: BLE001 - public "never raises" contract; log only the exception class
        logger.warning("Live sector median lookup failed unexpectedly (%s).", type(exc).__name__)
        return LiveSectorMedianResult(
            median=None,
            unavailable_code=SectorMedianUnavailableCode.SNAPSHOT_UNAVAILABLE,
            unavailable_reason="Sector median comparison is temporarily unavailable.",
            provenance=None,
        )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    result = generate_sector_medians()
    save_sector_medians(result)
    print(f"Generated sector medians from {result['tickers_used']}/{result['universe_size']} tickers:")
    print(f"Risk-free rate used: {result['risk_free_rate']:.2%}")
    for sector, median in sorted(result["sector_medians"].items(), key=lambda kv: kv[1]):
        count = result["sector_sample_counts"].get(sector, 0)
        print(f"  - {sector}: {median:.2f}x (n={count})")
    print(f"\nSaved to {CACHE_PATH}")
