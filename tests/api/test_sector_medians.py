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

import pytest

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


class TestRiskFreeRateCompatibility:
    """
    Regression for Finding 2: the cache records its own generation-time
    `risk_free_rate`, but comparability was never actually checked
    against it -- a cache generated at 1% was silently accepted for a
    request valued at 10%. `assumptions.risk_free_rate` (the SAME rate
    the caller fed into its own `calculate_wacc` call) must be checked
    against the cached rate within `RISK_FREE_RATE_COMPARISON_TOLERANCE`.
    """

    def test_exact_match_is_accepted(self, tmp_path):
        path = tmp_path / "cache.json"
        _write_cache(path)  # risk_free_rate=0.04 (see _write_cache's default)
        assumptions = DCFAssumptions(risk_free_rate=0.04)

        median, reason = get_sector_median_price_to_intrinsic("Technology", assumptions=assumptions, path=path)
        assert median == 1.2
        assert reason is None

    def test_within_tolerance_boundary_is_accepted(self, tmp_path):
        path = tmp_path / "cache.json"
        _write_cache(path)  # risk_free_rate=0.04
        from src.api.sector_medians import RISK_FREE_RATE_COMPARISON_TOLERANCE

        # Comfortably inside the tolerance (90% of it) -- avoids float-
        # precision flakiness from asserting exactly at the boundary.
        assumptions = DCFAssumptions(risk_free_rate=0.04 + RISK_FREE_RATE_COMPARISON_TOLERANCE * 0.9)

        median, reason = get_sector_median_price_to_intrinsic("Technology", assumptions=assumptions, path=path)
        assert median == 1.2
        assert reason is None

    def test_material_mismatch_is_refused(self, tmp_path):
        path = tmp_path / "cache.json"
        _write_cache(path)  # risk_free_rate=0.04
        # A cache generated near a 1% risk-free rate accepted for a
        # request valued at 10% -- the exact reproduced defect.
        assumptions = DCFAssumptions(risk_free_rate=0.10)

        median, reason = get_sector_median_price_to_intrinsic("Technology", assumptions=assumptions, path=path)
        assert median is None
        assert "risk-free rate" in reason.lower()

    def test_huge_integer_requested_rate_never_leaks_a_raw_overflow_error(self, tmp_path):
        """
        Regression for Track A Phase 1.5C requirement 4: a Python `int`
        is arbitrary-precision and always finite by definition, so
        `10**10000` passes `_is_valid_finite_number`'s input check -- but
        subtracting it from the cached (float) risk-free rate would raise
        a raw `OverflowError` while converting the int to a C `double`.
        Must degrade to the documented (None, reason) refusal, never leak.
        """
        path = tmp_path / "cache.json"
        _write_cache(path)  # risk_free_rate=0.04
        assumptions = DCFAssumptions(risk_free_rate=10**10000)

        median, reason = get_sector_median_price_to_intrinsic("Technology", assumptions=assumptions, path=path)
        assert median is None
        assert reason is not None

    def test_huge_integer_cached_rate_never_leaks_a_raw_overflow_error(self, tmp_path):
        """Same as above, but the astronomically large integer is in the CACHE FILE itself (a malformed JSON payload)."""
        path = tmp_path / "cache.json"
        cache = _write_cache(path)
        cache["risk_free_rate"] = 10**10000
        with open(path, "w") as f:
            json.dump(cache, f)
        assumptions = DCFAssumptions(risk_free_rate=0.04)

        median, reason = get_sector_median_price_to_intrinsic("Technology", assumptions=assumptions, path=path)
        assert median is None
        assert reason is not None

    def test_just_beyond_tolerance_is_refused(self, tmp_path):
        path = tmp_path / "cache.json"
        _write_cache(path)  # risk_free_rate=0.04
        from src.api.sector_medians import RISK_FREE_RATE_COMPARISON_TOLERANCE

        # Comfortably outside the tolerance (2x it) -- avoids float-
        # precision flakiness from asserting exactly at the boundary.
        assumptions = DCFAssumptions(risk_free_rate=0.04 + RISK_FREE_RATE_COMPARISON_TOLERANCE * 2)

        median, reason = get_sector_median_price_to_intrinsic("Technology", assumptions=assumptions, path=path)
        assert median is None
        assert "risk-free rate" in reason.lower()

    def test_missing_cached_rate_is_refused(self, tmp_path):
        path = tmp_path / "cache.json"
        cache = _write_cache(path)
        cache["risk_free_rate"] = None
        with open(path, "w") as f:
            json.dump(cache, f)
        assumptions = DCFAssumptions(risk_free_rate=0.04)

        median, reason = get_sector_median_price_to_intrinsic("Technology", assumptions=assumptions, path=path)
        assert median is None
        assert "risk-free rate" in reason.lower()

    def test_non_finite_cached_rate_is_refused(self, tmp_path):
        path = tmp_path / "cache.json"
        cache = _write_cache(path)
        cache["risk_free_rate"] = float("nan")
        with open(path, "w") as f:
            json.dump(cache, f)
        assumptions = DCFAssumptions(risk_free_rate=0.04)

        median, reason = get_sector_median_price_to_intrinsic("Technology", assumptions=assumptions, path=path)
        assert median is None
        assert "risk-free rate" in reason.lower()

    def test_check_skipped_when_assumptions_omitted(self, tmp_path):
        """An internal caller that doesn't pass `assumptions` still gets the raw cached value, unaffected."""
        path = tmp_path / "cache.json"
        cache = _write_cache(path)
        cache["risk_free_rate"] = None  # would fail the check if it ran
        with open(path, "w") as f:
            json.dump(cache, f)

        median, reason = get_sector_median_price_to_intrinsic("Technology", path=path)
        assert median == 1.2
        assert reason is None

    def test_historical_growth_mode_with_matching_rate_is_accepted(self, tmp_path):
        """Historical-derived (None) growth/margin assumptions plus a matching risk-free rate are comparable."""
        path = tmp_path / "cache.json"
        _write_cache(
            path,
            assumptions={
                "revenue_growth_rate": None,
                "operating_margin": None,
                "terminal_growth_rate": 0.025,
            },
        )
        assumptions = DCFAssumptions(risk_free_rate=0.04)

        median, reason = get_sector_median_price_to_intrinsic("Technology", assumptions=assumptions, path=path)
        assert median == 1.2
        assert reason is None

    def test_custom_assumptions_with_mismatched_rate_is_refused(self, tmp_path):
        """A custom (slider) assumption set that otherwise matches is still refused on rate alone."""
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
            revenue_growth_rate=0.08, operating_margin=0.2, terminal_growth_rate=0.025, risk_free_rate=0.10
        )

        median, reason = get_sector_median_price_to_intrinsic("Technology", assumptions=assumptions, path=path)
        assert median is None
        assert "risk-free rate" in reason.lower()


class TestFileCacheHardening:
    """
    Regression for Finding 2's "harden the file cache" requirement:
    corrupt JSON, timezone-naive timestamps, and atomic writes.
    """

    def test_corrupt_json_is_refused_not_a_crash(self, tmp_path):
        path = tmp_path / "cache.json"
        path.write_text("{not valid json::")

        median, reason = get_sector_median_price_to_intrinsic("Technology", path=path)
        assert median is None
        assert reason is not None

    def test_unreadable_binary_content_is_refused_not_a_crash(self, tmp_path):
        path = tmp_path / "cache.json"
        path.write_bytes(b"\xff\xfe\x00\x01not-json-either")

        median, reason = get_sector_median_price_to_intrinsic("Technology", path=path)
        assert median is None
        assert reason is not None

    def test_timezone_naive_timestamp_is_refused_cleanly(self, tmp_path):
        path = tmp_path / "cache.json"
        cache = _write_cache(path)
        cache["generated_at"] = "2024-01-01T00:00:00"  # no UTC offset -- naive
        with open(path, "w") as f:
            json.dump(cache, f)

        median, reason = get_sector_median_price_to_intrinsic("Technology", path=path)
        assert median is None
        assert "timezone" in reason.lower()

    def test_save_is_atomic_no_partial_file_left_behind_on_success(self, tmp_path):
        path = tmp_path / "cache.json"
        cache = _write_cache(path)

        # No stray temp files left in the directory after a successful save.
        leftover = [p for p in tmp_path.iterdir() if p != path]
        assert leftover == []
        # And the file itself is valid, complete JSON.
        with open(path) as f:
            loaded = json.load(f)
        assert loaded["sector_medians"] == cache["sector_medians"]

    def test_save_cleans_up_temp_file_on_write_failure(self, tmp_path, monkeypatch):
        from src.api import sector_medians as sm

        path = tmp_path / "cache.json"

        def _boom(*args, **kwargs):
            raise OSError("simulated disk failure")

        monkeypatch.setattr(sm.json, "dump", _boom)

        with pytest.raises(OSError):
            sm.save_sector_medians({"sector_medians": {}}, path=path)

        # The real target file must not exist, and no temp file left behind.
        assert not path.exists()
        assert list(tmp_path.iterdir()) == []


class TestMalformedTopLevelShape:
    """
    Regression for Track A Phase 1.5B discrepancy 3: valid JSON whose
    top-level value isn't a JSON object must degrade to an explicit
    unavailable reason, never leak `AttributeError: 'list' object has no
    attribute 'get'` (the exact reproduced defect for `[]`) or any other
    raw exception through `get_sector_median_price_to_intrinsic`.
    """

    @pytest.mark.parametrize(
        "top_level_json",
        [
            "[]",
            "null",
            '"just a string"',
            "42",
            "3.14",
            "true",
            "false",
            "[1, 2, 3]",
            '{"not": "a valid cache shape but still a dict"}',
        ],
    )
    def test_wrong_top_level_shape_is_refused_not_a_crash(self, tmp_path, top_level_json):
        path = tmp_path / "cache.json"
        path.write_text(top_level_json)

        median, reason = get_sector_median_price_to_intrinsic("Technology", path=path)

        assert median is None
        assert reason is not None

    @pytest.mark.parametrize("top_level_json", ["[]", "null", '"a string"', "42", "true"])
    def test_wrong_top_level_shape_reason_distinguishes_from_missing_cache(self, tmp_path, top_level_json):
        """A malformed (but present) cache must be reported distinctly from a cache that was never generated."""
        path = tmp_path / "cache.json"
        path.write_text(top_level_json)
        _, malformed_reason = get_sector_median_price_to_intrinsic("Technology", path=path)

        _, missing_reason = get_sector_median_price_to_intrinsic("Technology", path=tmp_path / "does-not-exist.json")

        assert malformed_reason != missing_reason
        assert "malformed" in malformed_reason.lower()
        assert "not been generated" in missing_reason.lower()

    def test_valid_dict_cache_with_unrelated_extra_top_level_shape_still_reads_normally(self, tmp_path):
        """A dict-shaped payload is never treated as a malformed top-level shape, even if unusual."""
        path = tmp_path / "cache.json"
        _write_cache(path)

        median, reason = get_sector_median_price_to_intrinsic("Technology", path=path)

        assert median == 1.2
        assert reason is None


class TestMalformedNestedContainers:
    """
    Regression for Track A Phase 1.5B discrepancy 3: a valid top-level
    JSON object whose NESTED containers are the wrong type must also
    degrade cleanly, never leak AttributeError/TypeError/ZeroDivisionError.
    """

    def test_sector_medians_not_a_dict_is_refused(self, tmp_path):
        path = tmp_path / "cache.json"
        cache = _write_cache(path)
        cache["sector_medians"] = ["Technology", 1.2]  # a list, not a dict
        with open(path, "w") as f:
            json.dump(cache, f)

        median, reason = get_sector_median_price_to_intrinsic("Technology", path=path)
        assert median is None
        assert "sector_medians" in reason

    def test_sector_sample_counts_not_a_dict_is_refused(self, tmp_path):
        path = tmp_path / "cache.json"
        cache = _write_cache(path)
        cache["sector_sample_counts"] = "not-a-dict"
        with open(path, "w") as f:
            json.dump(cache, f)

        median, reason = get_sector_median_price_to_intrinsic("Technology", path=path)
        assert median is None
        assert "sector_sample_counts" in reason

    def test_assumptions_not_a_dict_is_refused_with_specific_reason(self, tmp_path):
        path = tmp_path / "cache.json"
        cache = _write_cache(path)
        cache["assumptions"] = ["revenue_growth_rate", None]  # a list, not a dict
        with open(path, "w") as f:
            json.dump(cache, f)

        median, reason = get_sector_median_price_to_intrinsic(
            "Technology", assumptions=DCFAssumptions(), path=path
        )
        assert median is None
        assert "assumptions" in reason.lower()

    @pytest.mark.parametrize("bad_universe_size", ["100", True, None, [], {}, float("nan"), float("inf"), -5])
    def test_invalid_universe_size_is_refused(self, tmp_path, bad_universe_size):
        path = tmp_path / "cache.json"
        cache = _write_cache(path)
        cache["universe_size"] = bad_universe_size
        with open(path, "w") as f:
            json.dump(cache, f)

        median, reason = get_sector_median_price_to_intrinsic("Technology", path=path)
        assert median is None
        assert "universe_size" in reason

    @pytest.mark.parametrize("bad_tickers_used", ["95", True, None, [], {}])
    def test_invalid_tickers_used_is_refused(self, tmp_path, bad_tickers_used):
        path = tmp_path / "cache.json"
        cache = _write_cache(path)
        cache["tickers_used"] = bad_tickers_used
        with open(path, "w") as f:
            json.dump(cache, f)

        median, reason = get_sector_median_price_to_intrinsic("Technology", path=path)
        assert median is None
        assert "tickers_used" in reason

    @pytest.mark.parametrize("bad_median", ["1.2", True, [1.2], {"x": 1.2}])
    def test_invalid_median_value_is_refused(self, tmp_path, bad_median):
        path = tmp_path / "cache.json"
        cache = _write_cache(path)
        cache["sector_medians"] = {"Technology": bad_median}
        with open(path, "w") as f:
            json.dump(cache, f)

        median, reason = get_sector_median_price_to_intrinsic("Technology", path=path)
        assert median is None
        assert reason is not None

    @pytest.mark.parametrize("bad_sample_count", ["10", True, [10], -1])
    def test_invalid_sample_count_is_refused(self, tmp_path, bad_sample_count):
        path = tmp_path / "cache.json"
        cache = _write_cache(path)
        cache["sector_sample_counts"] = {"Technology": bad_sample_count}
        with open(path, "w") as f:
            json.dump(cache, f)

        median, reason = get_sector_median_price_to_intrinsic("Technology", path=path)
        assert median is None
        assert reason is not None

    def test_normal_valid_cache_still_reads_correctly_no_regression(self, tmp_path):
        path = tmp_path / "cache.json"
        _write_cache(path)

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
