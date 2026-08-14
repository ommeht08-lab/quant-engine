"""
Group G: sector-median cache contract.

`get_sector_median_price_to_intrinsic` must refuse — return (None, reason),
never a misleading number — a stale cache, an assumption mismatch between
the cache and the caller's current slider values, or a sector with too
few samples to be a meaningful comparison. Everything here operates on a
local JSON file (via `tmp_path`); no network, no real cache file touched.
"""

import datetime
import json

from src.api.sector_medians import (
    MIN_OVERALL_COVERAGE_FRACTION,
    MIN_SECTOR_SAMPLE_SIZE,
    get_sector_median_price_to_intrinsic,
    save_sector_medians,
)
from src.dcf_model.dcf import DCFAssumptions


def _write_cache(
    path,
    *,
    generated_at=None,
    assumptions=None,
    sector_medians=None,
    sample_counts=None,
    universe_size=100,
    tickers_used=95,
):
    cache = {
        "generated_at": (generated_at or datetime.datetime.now(datetime.timezone.utc)).isoformat(),
        "universe_size": universe_size,
        "tickers_used": tickers_used,
        "risk_free_rate": 0.04,
        "assumptions": assumptions
        or {
            "revenue_growth_rate": None,
            "operating_margin": None,
            "terminal_growth_rate": 0.025,
        },
        "sector_medians": sector_medians or {"Technology": 1.2},
        "sector_sample_counts": sample_counts or {"Technology": 10},
    }
    save_sector_medians(cache, path=path)
    return cache


class TestMissingOrEmptyCache:
    def test_cache_never_generated_is_refused(self, tmp_path):
        median, reason = get_sector_median_price_to_intrinsic("Technology", path=tmp_path / "missing.json")
        assert median is None
        assert "not been generated" in reason

    def test_sector_not_in_cache_is_refused(self, tmp_path):
        path = tmp_path / "cache.json"
        _write_cache(path, sector_medians={"Healthcare": 0.9}, sample_counts={"Healthcare": 10})

        median, reason = get_sector_median_price_to_intrinsic("Technology", path=path)
        assert median is None
        assert "Technology" in reason


class TestStaleness:
    def test_fresh_cache_is_accepted(self, tmp_path):
        path = tmp_path / "cache.json"
        _write_cache(path, generated_at=datetime.datetime.now(datetime.timezone.utc))

        median, reason = get_sector_median_price_to_intrinsic("Technology", path=path)
        assert median == 1.2
        assert reason is None

    def test_stale_cache_is_refused(self, tmp_path):
        path = tmp_path / "cache.json"
        old = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=72)
        _write_cache(path, generated_at=old)

        median, reason = get_sector_median_price_to_intrinsic(
            "Technology", path=path, max_staleness=datetime.timedelta(hours=48)
        )
        assert median is None
        assert "stale" in reason.lower()

    def test_unparseable_timestamp_is_refused(self, tmp_path):
        path = tmp_path / "cache.json"
        cache = _write_cache(path)
        cache["generated_at"] = "not-a-timestamp"
        with open(path, "w") as f:
            json.dump(cache, f)

        median, reason = get_sector_median_price_to_intrinsic("Technology", path=path)
        assert median is None
        assert "timestamp" in reason.lower()


class TestAssumptionMismatch:
    def test_matching_assumptions_are_accepted(self, tmp_path):
        path = tmp_path / "cache.json"
        _write_cache(
            path,
            assumptions={
                "revenue_growth_rate": 0.08,
                "operating_margin": 0.2,
                "terminal_growth_rate": 0.025,
            },
        )
        assumptions = DCFAssumptions(
            revenue_growth_rate=0.08, operating_margin=0.2, terminal_growth_rate=0.025
        )

        median, reason = get_sector_median_price_to_intrinsic("Technology", assumptions=assumptions, path=path)
        assert median == 1.2
        assert reason is None

    def test_mismatched_slider_assumptions_are_refused(self, tmp_path):
        path = tmp_path / "cache.json"
        _write_cache(
            path,
            assumptions={
                "revenue_growth_rate": None,
                "operating_margin": None,
                "terminal_growth_rate": 0.025,
            },
        )
        # Caller has moved a slider (explicit revenue_growth_rate override) —
        # no longer comparable to a cache generated with historical-derived defaults.
        assumptions = DCFAssumptions(revenue_growth_rate=0.15)

        median, reason = get_sector_median_price_to_intrinsic("Technology", assumptions=assumptions, path=path)
        assert median is None
        assert "assumptions" in reason.lower()

    def test_assumption_check_skipped_when_omitted(self, tmp_path):
        """An internal caller that doesn't pass `assumptions` gets the raw cached value."""
        path = tmp_path / "cache.json"
        _write_cache(
            path,
            assumptions={
                "revenue_growth_rate": None,
                "operating_margin": None,
                "terminal_growth_rate": 0.025,
            },
        )

        median, reason = get_sector_median_price_to_intrinsic("Technology", path=path)
        assert median == 1.2
        assert reason is None


class TestSampleSize:
    def test_below_minimum_sample_size_is_refused(self, tmp_path):
        path = tmp_path / "cache.json"
        _write_cache(path, sample_counts={"Technology": MIN_SECTOR_SAMPLE_SIZE - 1})

        median, reason = get_sector_median_price_to_intrinsic("Technology", path=path)
        assert median is None
        assert "sample" in reason.lower()

    def test_at_minimum_sample_size_is_accepted(self, tmp_path):
        path = tmp_path / "cache.json"
        _write_cache(path, sample_counts={"Technology": MIN_SECTOR_SAMPLE_SIZE})

        median, reason = get_sector_median_price_to_intrinsic("Technology", path=path)
        assert median == 1.2
        assert reason is None


class TestOverallCoverageHealth:
    """
    A run where an excessive share of the WHOLE universe failed to value
    must be refused entirely — independent of any single sector's own
    (possibly still-sufficient) sample count. This is exactly the
    scenario the stale committed `src/api/data/sector_medians.json`
    (6/100 tickers used) represents.
    """

    def test_low_overall_coverage_is_refused_even_with_sufficient_sector_sample(self, tmp_path):
        path = tmp_path / "cache.json"
        # The sector itself has plenty of samples (10 >= MIN_SECTOR_SAMPLE_SIZE),
        # but the RUN that produced it only valued 6% of the universe —
        # exactly the shape of the real stale committed cache file.
        _write_cache(path, universe_size=100, tickers_used=6, sample_counts={"Technology": 10})

        median, reason = get_sector_median_price_to_intrinsic("Technology", path=path)
        assert median is None
        assert "unhealthy" in reason.lower()
        assert "6/100" in reason

    def test_coverage_at_minimum_threshold_is_accepted(self, tmp_path):
        path = tmp_path / "cache.json"
        universe_size = 100
        tickers_used = int(MIN_OVERALL_COVERAGE_FRACTION * universe_size)
        _write_cache(path, universe_size=universe_size, tickers_used=tickers_used)

        median, reason = get_sector_median_price_to_intrinsic("Technology", path=path)
        assert median == 1.2
        assert reason is None

    def test_coverage_just_below_minimum_threshold_is_refused(self, tmp_path):
        path = tmp_path / "cache.json"
        universe_size = 100
        tickers_used = int(MIN_OVERALL_COVERAGE_FRACTION * universe_size) - 1
        _write_cache(path, universe_size=universe_size, tickers_used=tickers_used)

        median, reason = get_sector_median_price_to_intrinsic("Technology", path=path)
        assert median is None
        assert "unhealthy" in reason.lower()

    def test_zero_universe_size_is_refused_without_a_divide_by_zero_crash(self, tmp_path):
        path = tmp_path / "cache.json"
        _write_cache(path, universe_size=0, tickers_used=0)

        median, reason = get_sector_median_price_to_intrinsic("Technology", path=path)
        assert median is None
        assert "unhealthy" in reason.lower()
