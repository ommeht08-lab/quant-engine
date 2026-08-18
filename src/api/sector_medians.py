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
import math
import os
import statistics
import sys
import tempfile
from dataclasses import replace
from pathlib import Path
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.backtesting.historical_tester import DEFAULT_SP500_TOP_100_TICKERS
from src.data_ingestion.fetch_financials import fetch_company_financials
from src.dcf_model.dcf import DCFAssumptions, run_dcf_valuation
from src.utils.macro import get_risk_free_rate

logger = logging.getLogger(__name__)

CACHE_PATH = Path(__file__).resolve().parent / "data" / "sector_medians.json"

# A comparison against a cache older than this is refused rather than
# silently trusted, even if the requested sector/assumptions otherwise match.
CACHE_MAX_STALENESS = datetime.timedelta(hours=48)

# A sector median backed by fewer valid samples than this is refused —
# a "median" of 1-2 tickers isn't a meaningful peer comparison.
MIN_SECTOR_SAMPLE_SIZE = 3

# Overall generation-health guard, independent of any single sector's
# sample size: if an excessive share of the whole universe failed to
# value (e.g. a broad data-provider outage, a bug that broke every
# ticker's statement parsing), that's a systemic problem with the RUN,
# not sector-specific noise — a sector with exactly MIN_SECTOR_SAMPLE_SIZE
# survivors can still look individually "healthy" while the run that
# produced it was actually badly broken. Refuse the whole cache in that
# case rather than trust a technically-sufficient sector sample drawn
# from an unhealthy run.
MIN_OVERALL_COVERAGE_FRACTION = 0.5

# A comparison is refused if the caller's actual risk-free rate (the same
# rate it fed into `calculate_wacc` for the ticker it's about to compare)
# differs from the cache's own generation-time risk-free rate by more
# than this — otherwise the numerator (freshly computed P/IV) and the
# denominator (a cached peer median) would have been discounted under
# materially different macro regimes, making the ratio comparison
# meaningless even though every other assumption matches. 5 basis points
# comfortably covers the normal drift `get_risk_free_rate`'s own 1-hour
# cache TTL can introduce between two calls made close together, without
# being wide enough to paper over a genuine days-old rate move.
RISK_FREE_RATE_COMPARISON_TOLERANCE = 0.0005

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


def _is_valid_finite_number(value) -> bool:
    """
    Genuinely non-raising: True only for a genuine finite `int`/`float`
    -- not a `bool` (a `bool` is technically an `int` subclass in
    Python), not a non-numeric type, and not NaN/+-infinity.

    Deliberately does NOT call `math.isfinite(value)` on an `int` -- a
    Python `int` is arbitrary-precision and always finite by definition,
    but `math.isfinite` still converts its argument to a C `double`
    first, and that conversion itself raises `OverflowError` for an
    `int` too large to fit in a float (e.g. a malformed cache JSON field
    like `10**10000`). Calling `math.isfinite` on an `int` this large
    would make this "non-raising" cache-validation check itself raise.
    `float` values are always safe to pass to `math.isfinite` directly.
    """
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return True
    if isinstance(value, float):
        return math.isfinite(value)
    return False


def _is_valid_nonneg_count(value) -> bool:
    """True only for a genuine, non-negative, finite numeric count -- see `_is_valid_finite_number`."""
    return _is_valid_finite_number(value) and value >= 0


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
    tickers: Optional[List[str]] = None, assumptions: Optional[DCFAssumptions] = None
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

    Returns:
        dict with "generated_at" (ISO timestamp), "universe_size",
        "tickers_used", "risk_free_rate", "assumptions" (the comparable
        subset — see `_serialize_comparable_assumptions`), "sector_medians"
        (sector -> median P/IV), and "sector_sample_counts" (sector ->
        number of valid tickers backing that median).
    """
    tickers = tickers if tickers is not None else DEFAULT_SP500_TOP_100_TICKERS
    assumptions = assumptions or DCFAssumptions()

    risk_free_rate = get_risk_free_rate()
    assumptions = replace(assumptions, risk_free_rate=risk_free_rate)

    ratios_by_sector: Dict[str, List[float]] = {}
    tickers_used = 0
    for ticker in tickers:
        try:
            valuation = _compute_current_price_to_intrinsic(ticker, assumptions)
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


def get_sector_median_price_to_intrinsic(
    sector: str,
    assumptions: Optional[DCFAssumptions] = None,
    path: Path = CACHE_PATH,
    max_staleness: datetime.timedelta = CACHE_MAX_STALENESS,
    min_sample_size: int = MIN_SECTOR_SAMPLE_SIZE,
    min_overall_coverage_fraction: float = MIN_OVERALL_COVERAGE_FRACTION,
    risk_free_rate_tolerance: float = RISK_FREE_RATE_COMPARISON_TOLERANCE,
) -> Tuple[Optional[float], Optional[str]]:
    """
    Look up a sector's cached median P/IV, refusing — returning `None`
    plus an explicit reason, never a misleading number or a leaked
    exception (`AttributeError`/`TypeError`/`ZeroDivisionError`/a raw
    JSON or timezone-arithmetic error) — when:
        - the cache hasn't been generated yet, has an invalid top-level
          shape or invalid JSON (a DISTINCT reason from "not generated
          yet" — see `_MALFORMED_CACHE_REASON_KEY`), or has no entry for
          this sector,
        - the cache's generation timestamp is missing, unparseable, or not
          timezone-aware,
        - the cache is older than `max_staleness`,
        - too small a share of the WHOLE universe was successfully valued
          this run (`min_overall_coverage_fraction`) — a systemic problem
          with the run itself, independent of any one sector's own count,
        - `assumptions` is given and doesn't match what the cache was
          generated with (see `_serialize_comparable_assumptions`), OR
          `assumptions.risk_free_rate` differs from the cache's own
          generation-time risk-free rate by more than
          `risk_free_rate_tolerance` — see `RISK_FREE_RATE_COMPARISON_TOLERANCE`,
        - the sector's sample size is below `min_sample_size`.

    Args:
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
        path: Cache file path (overridable for testing).
        max_staleness: Maximum cache age before it's refused.
        min_sample_size: Minimum per-sector sample count before it's refused.
        min_overall_coverage_fraction: Minimum fraction of the WHOLE
            universe (`tickers_used / universe_size`) that must have been
            successfully valued before ANY sector's median from this
            cache is trusted, regardless of that sector's own count.
        risk_free_rate_tolerance: Maximum allowed absolute difference
            between the cache's generation-time risk-free rate and the
            caller's own, before the comparison is refused as having been
            computed under materially different discount-rate regimes.

    Returns:
        (median_p_iv, unavailable_reason) — exactly one of the two is None.
    """
    cache = load_sector_medians(path)

    malformed_reason = cache.get(_MALFORMED_CACHE_REASON_KEY)
    if malformed_reason is not None:
        return None, f"Sector median cache is malformed: {malformed_reason}."

    generated_at = cache.get("generated_at")
    if generated_at is None:
        return None, "Sector median cache has not been generated yet."

    try:
        generated_at_ts = datetime.datetime.fromisoformat(generated_at)
    except (TypeError, ValueError):
        return None, "Sector median cache has an unparseable generation timestamp."

    if generated_at_ts.tzinfo is None:
        # A genuine write from `save_sector_medians` always stamps an
        # explicit UTC-aware timestamp (`datetime.now(timezone.utc)`); a
        # timezone-naive value here means the file was hand-edited or
        # written by something other than this module's own writer, and
        # guessing which timezone was intended risks silently comparing
        # against a cache that is actually far staler (or fresher) than
        # it appears. Refused cleanly rather than crashing on the
        # aware-vs-naive subtraction below.
        return None, "Sector median cache has a timezone-naive generation timestamp."

    now = datetime.datetime.now(datetime.timezone.utc)
    if now - generated_at_ts > max_staleness:
        return None, f"Sector median cache is stale (generated {generated_at}; max age {max_staleness})."

    universe_size = cache.get("universe_size")
    tickers_used = cache.get("tickers_used")
    if not _is_valid_nonneg_count(universe_size):
        return None, "Sector median cache is unhealthy: universe_size is missing or invalid."
    if not _is_valid_nonneg_count(tickers_used):
        return None, "Sector median cache is unhealthy: tickers_used is missing or invalid."
    if universe_size <= 0:
        return None, "Sector median cache is unhealthy: universe_size is zero or missing."
    coverage = tickers_used / universe_size
    if coverage < min_overall_coverage_fraction:
        return None, (
            f"Sector median cache is unhealthy: only {tickers_used}/{universe_size} "
            f"({coverage:.0%}) of the universe was successfully valued this run "
            f"(minimum {min_overall_coverage_fraction:.0%})."
        )

    if assumptions is not None:
        cached_assumptions = cache.get("assumptions")
        if cached_assumptions is not None and not isinstance(cached_assumptions, dict):
            return None, "Sector median cache has a malformed assumptions container."
        requested_assumptions = _serialize_comparable_assumptions(assumptions)
        if cached_assumptions != requested_assumptions:
            return None, "Sector median cache was generated with different DCF assumptions."

        cached_risk_free_rate = cache.get("risk_free_rate")
        if not _is_valid_finite_number(cached_risk_free_rate):
            return None, "Sector median cache has a missing or invalid risk-free rate."

        requested_risk_free_rate = assumptions.risk_free_rate
        if not _is_valid_finite_number(requested_risk_free_rate):
            return None, "The requested risk-free rate is not a finite number."

        # Both operands already passed `_is_valid_finite_number` above --
        # true for ANY `int` (arbitrary-precision, always finite by
        # definition) or finite `float` -- but subtracting an
        # astronomically large `int` (e.g. `10**10000`) from a `float`
        # still raises `OverflowError` when Python converts the int side
        # to a C `double`. Caught here, narrowly, rather than letting it
        # escape this function's own "never raises, always (None, reason)"
        # contract.
        try:
            risk_free_rate_diff = abs(requested_risk_free_rate - cached_risk_free_rate)
        except (ArithmeticError, OverflowError) as exc:
            return None, f"Sector median cache risk-free rate comparison failed: {exc}."
        if not _is_valid_finite_number(risk_free_rate_diff):
            return None, "Sector median cache risk-free rate comparison produced a non-finite result."

        if risk_free_rate_diff > risk_free_rate_tolerance:
            return None, (
                "Sector median cache was generated with a different risk-free rate "
                f"({cached_risk_free_rate:.4%} vs. the requested {requested_risk_free_rate:.4%}) "
                "-- the two P/IV ratios were computed under different discount-rate regimes "
                "and are not comparable."
            )

    sector_medians_map = cache.get("sector_medians")
    if not isinstance(sector_medians_map, dict):
        return None, "Sector median cache has a malformed sector_medians container."
    median = sector_medians_map.get(sector)
    if median is None:
        return None, f"No cached sector median for sector '{sector}'."
    if not _is_valid_finite_number(median):
        return None, f"Sector median cache has an invalid median value for sector '{sector}'."

    sector_sample_counts_map = cache.get("sector_sample_counts")
    if not isinstance(sector_sample_counts_map, dict):
        return None, "Sector median cache has a malformed sector_sample_counts container."
    sample_count = sector_sample_counts_map.get(sector, 0)
    if not _is_valid_nonneg_count(sample_count) or sample_count < min_sample_size:
        return None, (
            f"Sector '{sector}' has only {sample_count!r} sample(s) in the cache "
            f"(minimum {min_sample_size})."
        )

    return median, None


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
