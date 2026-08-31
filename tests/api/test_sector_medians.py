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

from src.api.sector_median_thresholds import SectorMedianUnavailableCode
from src.api.sector_medians import (
    MIN_OVERALL_COVERAGE_FRACTION,
    MIN_SECTOR_SAMPLE_SIZE,
    SectorMedianSnapshotProvenance,
    _evaluate_sector_median_cache_full,
    _weekend_adjusted_max_staleness,
    get_live_sector_median_price_to_intrinsic,
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
        # `now` is fixed so this doesn't depend on the real calendar date.
        # 10 days stays stale even with the max weekend extension (+4 days on the 48h base).
        now = datetime.datetime(2026, 1, 12, 12, 0, tzinfo=datetime.timezone.utc)
        old = now - datetime.timedelta(days=10)
        _write_cache(path, generated_at=old)

        median, reason = get_sector_median_price_to_intrinsic(
            "Technology", path=path, max_staleness=datetime.timedelta(hours=48), now=now
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
        cache["risk_free_rate"] = "__HUGE_INTEGER__"
        payload = json.dumps(cache).replace(
            '"__HUGE_INTEGER__"',
            "1" + "0" * 10000,
        )
        path.write_text(payload)
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


def _minimal_valid_cache(**overrides):
    cache = {
        "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "universe_size": 100,
        "tickers_used": 90,
        "risk_free_rate": 0.04,
        "assumptions": {
            "revenue_growth_rate": None,
            "operating_margin": None,
            "terminal_growth_rate": 0.025,
        },
        "sector_medians": {"Technology": 1.2},
        "sector_sample_counts": {"Technology": 10},
    }
    cache.update(overrides)
    return cache


class TestGenuineIntegerAndPositiveMedianValidation:
    """
    Read-time validation (`_evaluate_sector_median_cache_full`, shared by
    both the file-based and live lookups) requires GENUINE non-negative
    integers for `universe_size`/`tickers_used`/sector sample counts --
    never a float that merely happens to be whole (e.g. `3.9` or
    `100.0`), never a bool -- and a finite, POSITIVE sector median.
    Malformed counts/medians must classify as `SNAPSHOT_UNAVAILABLE`; a
    genuinely-counted sector that's simply below the peer minimum must
    still classify as `INSUFFICIENT_PEERS` -- the two must never be
    conflated. `assumptions` is omitted on every call here (calling with
    the default `assumptions=None`) so the assumption/risk-free-rate
    compatibility branch is skipped and each test isolates exactly the
    count/median check it targets.
    """

    def test_fractional_sector_sample_count_is_snapshot_unavailable(self):
        cache = _minimal_valid_cache(sector_sample_counts={"Technology": 3.9})

        median, code, reason = _evaluate_sector_median_cache_full(cache, "Technology")

        assert median is None
        assert code == SectorMedianUnavailableCode.SNAPSHOT_UNAVAILABLE
        assert "invalid sample count" in reason.lower()

    def test_fractional_universe_size_is_snapshot_unavailable(self):
        cache = _minimal_valid_cache(universe_size=100.0)

        median, code, reason = _evaluate_sector_median_cache_full(cache, "Technology")

        assert median is None
        assert code == SectorMedianUnavailableCode.SNAPSHOT_UNAVAILABLE
        assert "universe_size" in reason

    def test_fractional_tickers_used_is_snapshot_unavailable(self):
        cache = _minimal_valid_cache(tickers_used=90.0)

        median, code, reason = _evaluate_sector_median_cache_full(cache, "Technology")

        assert median is None
        assert code == SectorMedianUnavailableCode.SNAPSHOT_UNAVAILABLE
        assert "tickers_used" in reason

    def test_boolean_universe_size_is_snapshot_unavailable(self):
        """A `bool` is technically an `int` subclass in Python -- must
        still be refused, not silently treated as 0/1."""
        cache = _minimal_valid_cache(universe_size=True)

        median, code, reason = _evaluate_sector_median_cache_full(cache, "Technology")

        assert median is None
        assert code == SectorMedianUnavailableCode.SNAPSHOT_UNAVAILABLE

    def test_tickers_used_exceeding_universe_size_is_snapshot_unavailable(self):
        cache = _minimal_valid_cache(universe_size=50, tickers_used=90)

        median, code, reason = _evaluate_sector_median_cache_full(cache, "Technology")

        assert median is None
        assert code == SectorMedianUnavailableCode.SNAPSHOT_UNAVAILABLE
        assert "exceeds" in reason.lower()

    def test_zero_sector_median_is_snapshot_unavailable(self):
        cache = _minimal_valid_cache(sector_medians={"Technology": 0.0})

        median, code, reason = _evaluate_sector_median_cache_full(cache, "Technology")

        assert median is None
        assert code == SectorMedianUnavailableCode.SNAPSHOT_UNAVAILABLE
        assert "non-positive" in reason.lower()

    def test_negative_sector_median_is_snapshot_unavailable(self):
        cache = _minimal_valid_cache(sector_medians={"Technology": -1.5})

        median, code, reason = _evaluate_sector_median_cache_full(cache, "Technology")

        assert median is None
        assert code == SectorMedianUnavailableCode.SNAPSHOT_UNAVAILABLE
        assert "non-positive" in reason.lower()

    def test_a_valid_integer_count_below_the_peer_minimum_is_still_insufficient_peers(self):
        cache = _minimal_valid_cache(sector_sample_counts={"Technology": MIN_SECTOR_SAMPLE_SIZE - 1})

        median, code, reason = _evaluate_sector_median_cache_full(cache, "Technology")

        assert median is None
        assert code == SectorMedianUnavailableCode.INSUFFICIENT_PEERS
        assert "sample" in reason.lower()

    def test_a_valid_integer_count_at_the_peer_minimum_succeeds(self):
        cache = _minimal_valid_cache(sector_sample_counts={"Technology": MIN_SECTOR_SAMPLE_SIZE})

        median, code, reason = _evaluate_sector_median_cache_full(cache, "Technology")

        assert median == 1.2
        assert code is None
        assert reason is None


class TestWeekendAdjustedStaleness:
    """A snapshot generated before a weekend (the refresh workflow only
    runs on trading days) must get extra staleness headroom so it isn't
    refused purely because non-trading weekend days elapsed."""

    def test_no_weekend_spanned_leaves_the_base_threshold_unchanged(self):
        generated_at = datetime.datetime(2024, 1, 2, 12, 0, tzinfo=datetime.timezone.utc)  # Tuesday
        now = datetime.datetime(2024, 1, 3, 12, 0, tzinfo=datetime.timezone.utc)  # Wednesday
        base = datetime.timedelta(hours=48)

        assert _weekend_adjusted_max_staleness(generated_at, now, base) == base

    def test_a_spanned_weekend_adds_one_day_per_weekend_date(self):
        generated_at = datetime.datetime(2024, 1, 5, 20, 0, tzinfo=datetime.timezone.utc)  # Friday
        now = datetime.datetime(2024, 1, 8, 9, 0, tzinfo=datetime.timezone.utc)  # Monday
        base = datetime.timedelta(hours=48)

        # Spans Saturday 1/6 and Sunday 1/7 -> +2 days.
        assert _weekend_adjusted_max_staleness(generated_at, now, base) == base + datetime.timedelta(days=2)

    def test_friday_generated_cache_survives_through_monday_via_the_public_lookup(self, tmp_path):
        """61 hours elapsed (Friday 20:00 -> Monday 09:00) exceeds the
        base 48h threshold on its own, but is comfortably inside the
        weekend-adjusted 96h (48h + 2 weekend days) one."""
        path = tmp_path / "cache.json"
        generated_at = datetime.datetime(2024, 1, 5, 20, 0, tzinfo=datetime.timezone.utc)
        now = datetime.datetime(2024, 1, 8, 9, 0, tzinfo=datetime.timezone.utc)
        _write_cache(path, generated_at=generated_at)

        median, reason = get_sector_median_price_to_intrinsic("Technology", path=path, now=now)

        assert median == 1.2
        assert reason is None

    def test_a_genuinely_stale_cache_is_still_refused_even_after_a_weekend_extension(self, tmp_path):
        """A full week (168h) old cache is still refused even with the
        +2 day weekend extension applied (96h effective threshold)."""
        path = tmp_path / "cache.json"
        generated_at = datetime.datetime(2024, 1, 2, 12, 0, tzinfo=datetime.timezone.utc)  # Tuesday
        now = datetime.datetime(2024, 1, 9, 12, 0, tzinfo=datetime.timezone.utc)  # Following Tuesday
        _write_cache(path, generated_at=generated_at)

        median, reason = get_sector_median_price_to_intrinsic("Technology", path=path, now=now)

        assert median is None
        assert "stale" in reason.lower()


class TestGetLiveSectorMedianPriceToIntrinsic:
    """`get_live_sector_median_price_to_intrinsic` is the production
    lookup `/api/evaluate` calls — it must delegate to the Supabase-backed
    store, apply the exact same validation as the file-based lookup, and
    always return a fully-typed `LiveSectorMedianResult` (never a raw
    snapshot dict), without ever raising."""

    def test_no_snapshot_available_returns_the_fetch_reason_and_no_provenance(self, monkeypatch):
        monkeypatch.setattr(
            "src.api.sector_median_store.get_cached_latest_snapshot",
            lambda: (None, "DATABASE_URL is not configured."),
        )

        result = get_live_sector_median_price_to_intrinsic("Technology")

        assert result.median is None
        assert result.unavailable_code == SectorMedianUnavailableCode.SNAPSHOT_UNAVAILABLE
        assert result.unavailable_reason == "DATABASE_URL is not configured."
        assert result.provenance is None

    def test_a_valid_published_snapshot_is_validated_and_returned(self, monkeypatch):
        generated_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
        published = {
            "generated_at": generated_at,
            "universe_size": 100,
            "tickers_used": 90,
            "risk_free_rate": 0.04,
            "assumptions": {
                "revenue_growth_rate": None,
                "operating_margin": None,
                "terminal_growth_rate": 0.025,
            },
            "sector_medians": {"Technology": 1.4},
            "sector_sample_counts": {"Technology": 10},
        }
        monkeypatch.setattr(
            "src.api.sector_median_store.get_cached_latest_snapshot", lambda: (published, None)
        )

        result = get_live_sector_median_price_to_intrinsic("Technology")

        assert result.median == 1.4
        assert result.unavailable_code is None
        assert result.unavailable_reason is None
        assert result.provenance == SectorMedianSnapshotProvenance(
            generated_at=generated_at,
            universe_size=100,
            tickers_used=90,
            sector_sample_count=10,
        )

    def test_snapshot_is_still_returned_when_this_sectors_lookup_fails_validation(self, monkeypatch):
        """A fetched snapshot that exists but doesn't validate for THIS
        sector (e.g. too few samples) must still surface its own
        provenance — only the code/reason/median reflect the refusal."""
        published = {
            "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "universe_size": 100,
            "tickers_used": 90,
            "risk_free_rate": 0.04,
            "assumptions": {
                "revenue_growth_rate": None,
                "operating_margin": None,
                "terminal_growth_rate": 0.025,
            },
            "sector_medians": {"Technology": 1.4},
            "sector_sample_counts": {"Technology": 1},  # below MIN_SECTOR_SAMPLE_SIZE
        }
        monkeypatch.setattr(
            "src.api.sector_median_store.get_cached_latest_snapshot", lambda: (published, None)
        )

        result = get_live_sector_median_price_to_intrinsic("Technology")

        assert result.median is None
        assert result.unavailable_code == SectorMedianUnavailableCode.INSUFFICIENT_PEERS
        assert "sample" in result.unavailable_reason.lower()
        assert result.provenance.tickers_used == 90
        assert result.provenance.sector_sample_count == 1

    def test_incompatible_assumptions_get_the_incompatible_assumptions_code(self, monkeypatch):
        published = {
            "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "universe_size": 100,
            "tickers_used": 90,
            "risk_free_rate": 0.04,
            "assumptions": {
                "revenue_growth_rate": None,
                "operating_margin": None,
                "terminal_growth_rate": 0.025,
            },
            "sector_medians": {"Technology": 1.4},
            "sector_sample_counts": {"Technology": 10},
        }
        monkeypatch.setattr(
            "src.api.sector_median_store.get_cached_latest_snapshot", lambda: (published, None)
        )

        result = get_live_sector_median_price_to_intrinsic(
            "Technology",
            assumptions=DCFAssumptions(
                revenue_growth_rate=0.15, operating_margin=0.3, terminal_growth_rate=0.025, risk_free_rate=0.04
            ),
        )

        assert result.median is None
        assert result.unavailable_code == SectorMedianUnavailableCode.INCOMPATIBLE_ASSUMPTIONS


def _fully_valid_published(**overrides):
    generated_at = overrides.pop("generated_at", datetime.datetime.now(datetime.timezone.utc).isoformat())
    published = {
        "generated_at": generated_at,
        "universe_size": 100,
        "tickers_used": 90,
        "risk_free_rate": 0.04,
        "assumptions": {
            "revenue_growth_rate": None,
            "operating_margin": None,
            "terminal_growth_rate": 0.025,
        },
        "sector_medians": {"Technology": 1.4},
        "sector_sample_counts": {"Technology": 10},
    }
    published.update(overrides)
    return published


class TestGetLiveSectorMedianPriceToIntrinsicRobustness:
    """
    `get_live_sector_median_price_to_intrinsic` is a public "never
    raises" contract: even a structurally malformed snapshot handed back
    by `get_cached_latest_snapshot` (a missing key, an invalid count like
    `"corrupt"`, a malformed container, an invalid or future timestamp)
    must degrade to `median=None`, `unavailable_code=SNAPSHOT_UNAVAILABLE`,
    a safe reason, and `provenance=None` — never raise, and never build
    provenance from an unvalidated raw field.
    """

    def _assert_safely_unavailable(self, result):
        assert result.median is None
        assert result.unavailable_code == SectorMedianUnavailableCode.SNAPSHOT_UNAVAILABLE
        assert result.unavailable_reason is not None
        assert result.provenance is None

    def test_missing_generated_at_key_entirely(self, monkeypatch):
        published = _fully_valid_published()
        del published["generated_at"]
        monkeypatch.setattr(
            "src.api.sector_median_store.get_cached_latest_snapshot", lambda: (published, None)
        )

        result = get_live_sector_median_price_to_intrinsic("Technology")

        self._assert_safely_unavailable(result)

    def test_missing_tickers_used_key_entirely(self, monkeypatch):
        published = _fully_valid_published()
        del published["tickers_used"]
        monkeypatch.setattr(
            "src.api.sector_median_store.get_cached_latest_snapshot", lambda: (published, None)
        )

        result = get_live_sector_median_price_to_intrinsic("Technology")

        self._assert_safely_unavailable(result)

    def test_corrupt_string_tickers_used(self, monkeypatch):
        published = _fully_valid_published(tickers_used="corrupt")
        monkeypatch.setattr(
            "src.api.sector_median_store.get_cached_latest_snapshot", lambda: (published, None)
        )

        result = get_live_sector_median_price_to_intrinsic("Technology")

        self._assert_safely_unavailable(result)

    def test_corrupt_string_universe_size(self, monkeypatch):
        published = _fully_valid_published(universe_size="corrupt")
        monkeypatch.setattr(
            "src.api.sector_median_store.get_cached_latest_snapshot", lambda: (published, None)
        )

        result = get_live_sector_median_price_to_intrinsic("Technology")

        self._assert_safely_unavailable(result)

    def test_sector_sample_counts_is_not_a_dict(self, monkeypatch):
        published = _fully_valid_published(sector_sample_counts=["not", "a", "dict"])
        monkeypatch.setattr(
            "src.api.sector_median_store.get_cached_latest_snapshot", lambda: (published, None)
        )

        result = get_live_sector_median_price_to_intrinsic("Technology")

        self._assert_safely_unavailable(result)

    def test_sector_sample_counts_value_is_corrupt(self, monkeypatch):
        published = _fully_valid_published(sector_sample_counts={"Technology": "corrupt"})
        monkeypatch.setattr(
            "src.api.sector_median_store.get_cached_latest_snapshot", lambda: (published, None)
        )

        result = get_live_sector_median_price_to_intrinsic("Technology")

        self._assert_safely_unavailable(result)

    def test_unparseable_generated_at(self, monkeypatch):
        published = _fully_valid_published(generated_at="not-a-real-timestamp")
        monkeypatch.setattr(
            "src.api.sector_median_store.get_cached_latest_snapshot", lambda: (published, None)
        )

        result = get_live_sector_median_price_to_intrinsic("Technology")

        self._assert_safely_unavailable(result)

    def test_timezone_naive_generated_at(self, monkeypatch):
        published = _fully_valid_published(generated_at="2026-08-20T12:00:00")
        monkeypatch.setattr(
            "src.api.sector_median_store.get_cached_latest_snapshot", lambda: (published, None)
        )

        result = get_live_sector_median_price_to_intrinsic("Technology")

        self._assert_safely_unavailable(result)

    def test_a_materially_future_generated_at_is_refused(self, monkeypatch):
        future = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=1)
        published = _fully_valid_published(generated_at=future.isoformat())
        monkeypatch.setattr(
            "src.api.sector_median_store.get_cached_latest_snapshot", lambda: (published, None)
        )

        result = get_live_sector_median_price_to_intrinsic("Technology")

        self._assert_safely_unavailable(result)
        assert "future" in result.unavailable_reason.lower()

    def test_a_snapshot_within_the_future_clock_skew_tolerance_is_not_refused_as_future(self, monkeypatch):
        near_future = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=30)
        published = _fully_valid_published(generated_at=near_future.isoformat())
        monkeypatch.setattr(
            "src.api.sector_median_store.get_cached_latest_snapshot", lambda: (published, None)
        )

        result = get_live_sector_median_price_to_intrinsic("Technology")

        assert result.median == 1.4
        assert result.provenance is not None

    def test_get_cached_latest_snapshot_raising_unexpectedly_never_raises(self, monkeypatch):
        def _boom():
            raise RuntimeError("unexpected failure")

        monkeypatch.setattr("src.api.sector_median_store.get_cached_latest_snapshot", _boom)

        result = get_live_sector_median_price_to_intrinsic("Technology")

        self._assert_safely_unavailable(result)

    def test_a_sentinel_secret_embedded_in_an_unexpected_exception_never_leaks(self, monkeypatch, caplog):
        secret = "sk-live-supersecret-do-not-leak-9f8e7d"

        def _boom():
            raise RuntimeError(f"connection string was postgresql://user:{secret}@host/db")

        monkeypatch.setattr("src.api.sector_median_store.get_cached_latest_snapshot", _boom)

        with caplog.at_level("WARNING", logger="src.api.sector_medians"):
            result = get_live_sector_median_price_to_intrinsic("Technology")

        log_text = "\n".join(record.getMessage() for record in caplog.records)
        self._assert_safely_unavailable(result)
        assert secret not in log_text
        assert secret not in result.unavailable_reason
