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
different assumptions than the current request, or a sector's sample
size is too small to trust.

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
import statistics
import sys
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

EMPTY_CACHE = {
    "generated_at": None,
    "universe_size": 0,
    "tickers_used": 0,
    "risk_free_rate": None,
    "assumptions": None,
    "sector_medians": {},
    "sector_sample_counts": {},
}


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
    """Write a `generate_sector_medians`-shaped result to disk as JSON."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(cache, f, indent=2)


def load_sector_medians(path: Path = CACHE_PATH) -> dict:
    """
    Load the cached sector medians from disk.

    Returns:
        The cache dict, or `EMPTY_CACHE` if the file doesn't exist yet
        (e.g. before the first generation run) — never raises, since a
        missing cache should degrade to "unavailable" rather than break
        the live API.
    """
    if not path.exists():
        logger.warning("Sector median cache not found at %s; run `python -m src.api.sector_medians`.", path)
        return dict(EMPTY_CACHE)

    with open(path) as f:
        return json.load(f)


def get_sector_median_price_to_intrinsic(
    sector: str,
    assumptions: Optional[DCFAssumptions] = None,
    path: Path = CACHE_PATH,
    max_staleness: datetime.timedelta = CACHE_MAX_STALENESS,
    min_sample_size: int = MIN_SECTOR_SAMPLE_SIZE,
    min_overall_coverage_fraction: float = MIN_OVERALL_COVERAGE_FRACTION,
) -> Tuple[Optional[float], Optional[str]]:
    """
    Look up a sector's cached median P/IV, refusing — returning `None`
    plus an explicit reason, never a misleading number — when:
        - the cache hasn't been generated yet, or has no entry for this sector,
        - the cache is older than `max_staleness`,
        - too small a share of the WHOLE universe was successfully valued
          this run (`min_overall_coverage_fraction`) — a systemic problem
          with the run itself, independent of any one sector's own count,
        - `assumptions` is given and doesn't match what the cache was
          generated with (see `_serialize_comparable_assumptions`),
        - the sector's sample size is below `min_sample_size`.

    Args:
        sector: Sector name, e.g. "Technology".
        assumptions: The DCF assumptions the CALLER is about to compare
            against this median. If omitted, the assumption-compatibility
            check is skipped (the caller is presumed to already know
            it's comparing like-for-like — e.g. an internal batch job
            using the cache's own generation assumptions).
        path: Cache file path (overridable for testing).
        max_staleness: Maximum cache age before it's refused.
        min_sample_size: Minimum per-sector sample count before it's refused.
        min_overall_coverage_fraction: Minimum fraction of the WHOLE
            universe (`tickers_used / universe_size`) that must have been
            successfully valued before ANY sector's median from this
            cache is trusted, regardless of that sector's own count.

    Returns:
        (median_p_iv, unavailable_reason) — exactly one of the two is None.
    """
    cache = load_sector_medians(path)

    generated_at = cache.get("generated_at")
    if generated_at is None:
        return None, "Sector median cache has not been generated yet."

    try:
        generated_at_ts = datetime.datetime.fromisoformat(generated_at)
    except (TypeError, ValueError):
        return None, "Sector median cache has an unparseable generation timestamp."

    now = datetime.datetime.now(datetime.timezone.utc)
    if now - generated_at_ts > max_staleness:
        return None, f"Sector median cache is stale (generated {generated_at}; max age {max_staleness})."

    universe_size = cache.get("universe_size") or 0
    tickers_used = cache.get("tickers_used") or 0
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
        requested_assumptions = _serialize_comparable_assumptions(assumptions)
        if cached_assumptions != requested_assumptions:
            return None, "Sector median cache was generated with different DCF assumptions."

    median = cache.get("sector_medians", {}).get(sector)
    if median is None:
        return None, f"No cached sector median for sector '{sector}'."

    sample_count = cache.get("sector_sample_counts", {}).get(sector, 0)
    if sample_count < min_sample_size:
        return None, (
            f"Sector '{sector}' has only {sample_count} sample(s) in the cache "
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
