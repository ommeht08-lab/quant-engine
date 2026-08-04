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
"""

import json
import logging
import statistics
import sys
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.backtesting.historical_tester import DEFAULT_SP500_TOP_100_TICKERS
from src.data_ingestion.fetch_financials import fetch_company_financials
from src.dcf_model.dcf import DCFAssumptions, run_dcf_valuation

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

CACHE_PATH = Path(__file__).resolve().parent / "data" / "sector_medians.json"


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
    sector, and compute each sector's median P/IV.

    Args:
        tickers: Universe to value. Defaults to
            `DEFAULT_SP500_TOP_100_TICKERS`.
        assumptions: DCF assumptions applied uniformly. Defaults to
            `DCFAssumptions()` (dynamic, per-company historical growth/margin).

    Returns:
        dict with "generated_at" (ISO timestamp), "universe_size",
        "tickers_used", and "sector_medians" (sector -> median P/IV).
    """
    import datetime

    tickers = tickers if tickers is not None else DEFAULT_SP500_TOP_100_TICKERS
    assumptions = assumptions or DCFAssumptions()

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

    return {
        "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "universe_size": len(tickers),
        "tickers_used": tickers_used,
        "sector_medians": sector_medians,
    }


def save_sector_medians(cache: dict, path: Path = CACHE_PATH) -> None:
    """Write a `generate_sector_medians` result to disk as JSON."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(cache, f, indent=2)


def load_sector_medians(path: Path = CACHE_PATH) -> dict:
    """
    Load the cached sector medians from disk.

    Returns:
        The cache dict, or an empty "sector_medians" cache if the file
        doesn't exist yet (e.g. before the first generation run) — never
        raises, since a missing cache should degrade to "unavailable"
        rather than break the live API.
    """
    if not path.exists():
        logger.warning("Sector median cache not found at %s; run `python -m src.api.sector_medians`.", path)
        return {"generated_at": None, "universe_size": 0, "tickers_used": 0, "sector_medians": {}}

    with open(path) as f:
        return json.load(f)


def get_sector_median_price_to_intrinsic(sector: str, path: Path = CACHE_PATH) -> Optional[float]:
    """
    Look up a sector's cached median P/IV.

    Args:
        sector: Sector name, e.g. "Technology".
        path: Cache file path (overridable for testing).

    Returns:
        The median P/IV, or None if the cache is missing or has no entry
        for this sector (e.g. "Unknown", or a sector not represented in
        the reference universe).
    """
    cache = load_sector_medians(path)
    return cache.get("sector_medians", {}).get(sector)


if __name__ == "__main__":
    result = generate_sector_medians()
    save_sector_medians(result)
    print(f"Generated sector medians from {result['tickers_used']}/{result['universe_size']} tickers:")
    for sector, median in sorted(result["sector_medians"].items(), key=lambda kv: kv[1]):
        print(f"  - {sector}: {median:.2f}x")
    print(f"\nSaved to {CACHE_PATH}")
