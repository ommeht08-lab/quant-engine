import datetime as dt

import pytest

from src.fundamentals.time_policy import (
    DEFAULT_MARKET_CLOSE,
    US_EASTERN,
    eligible_at,
    knowledge_cutoff_for_date,
)


class TestKnowledgeCutoffForDate:
    def test_defaults_to_market_close_eastern(self):
        cutoff = knowledge_cutoff_for_date(dt.date(2024, 6, 3))
        assert cutoff.tzinfo is not None
        assert cutoff.timetz().replace(tzinfo=None) == DEFAULT_MARKET_CLOSE
        assert cutoff.tzinfo is US_EASTERN or cutoff.tzname() in ("EDT", "EST")

    def test_caller_can_override_market_time(self):
        cutoff = knowledge_cutoff_for_date(dt.date(2024, 6, 3), market_time=dt.time(9, 30))
        assert cutoff.hour == 9 and cutoff.minute == 30

    def test_is_dst_aware_winter_offset(self):
        winter_cutoff = knowledge_cutoff_for_date(dt.date(2024, 1, 15))
        assert winter_cutoff.utcoffset() == dt.timedelta(hours=-5)

    def test_is_dst_aware_summer_offset(self):
        summer_cutoff = knowledge_cutoff_for_date(dt.date(2024, 7, 15))
        assert summer_cutoff.utcoffset() == dt.timedelta(hours=-4)

    def test_same_wall_clock_time_different_utc_instant_across_dst(self):
        winter_cutoff = knowledge_cutoff_for_date(dt.date(2024, 1, 15))
        summer_cutoff = knowledge_cutoff_for_date(dt.date(2024, 7, 15))
        # Same local wall-clock time (16:00) on both dates...
        assert winter_cutoff.hour == summer_cutoff.hour == 16
        # ...but a genuinely different UTC offset, proving this isn't a
        # fixed-offset shortcut that would be wrong for half the year.
        assert winter_cutoff.utcoffset() != summer_cutoff.utcoffset()


class TestEligibleAt:
    def test_uses_accepted_at_when_present(self):
        accepted = dt.datetime(2024, 11, 1, 16, 32, tzinfo=US_EASTERN)
        result = eligible_at(accepted, filed_date=dt.date(2024, 11, 1))
        assert result == accepted

    def test_falls_back_to_true_end_of_day_eastern_when_accepted_at_missing(self):
        result = eligible_at(None, filed_date=dt.date(2024, 3, 1))
        # The TRUE end of day (time.max = 23:59:59.999999), not merely
        # 23:59:59 — the latest instant still consistent with the date.
        assert result == dt.datetime(2024, 3, 1, 23, 59, 59, 999999, tzinfo=US_EASTERN)
        assert result.time() == dt.time.max

    def test_fallback_is_dst_aware(self):
        winter_fallback = eligible_at(None, filed_date=dt.date(2024, 1, 15))
        summer_fallback = eligible_at(None, filed_date=dt.date(2024, 7, 15))
        assert winter_fallback.utcoffset() == dt.timedelta(hours=-5)
        assert summer_fallback.utcoffset() == dt.timedelta(hours=-4)

    def test_fallback_is_the_latest_moment_consistent_with_filed_date(self):
        # Conservative direction: the fallback must never be earlier than
        # any other moment on the same calendar date.
        result = eligible_at(None, filed_date=dt.date(2024, 3, 1))
        earlier_same_day = dt.datetime(2024, 3, 1, 23, 59, 58, tzinfo=US_EASTERN)
        assert result > earlier_same_day

    def test_rejects_naive_accepted_at(self):
        naive = dt.datetime(2024, 11, 1, 16, 32)  # no tzinfo
        with pytest.raises(ValueError):
            eligible_at(naive, filed_date=dt.date(2024, 11, 1))
