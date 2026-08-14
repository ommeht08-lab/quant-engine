"""
Group J: point-in-time conservatism.

`_columns_on_or_before` gates statement-column availability by
`STATEMENT_FILING_LAG_DAYS` — a fiscal period's mere END date isn't when
it was actually publicly filed, so treating it as "known" any earlier
than the lag allows is a look-ahead bias. No network; all statements are
synthetic pandas fixtures.
"""

import pandas as pd

from src.backtesting.historical_tester import STATEMENT_FILING_LAG_DAYS, _columns_on_or_before


def _statement_with_period_end(period_end: pd.Timestamp) -> pd.DataFrame:
    return pd.DataFrame({period_end: {"Total Revenue": 1000.0}})


class TestDefaultFilingLagIsConservative:
    def test_default_lag_is_at_least_90_days(self):
        """The independent review's specific ask: >= 90 days by default."""
        assert STATEMENT_FILING_LAG_DAYS >= 90

    def test_annual_statement_80_days_after_period_end_is_still_unavailable(self):
        """
        The exact regression case the review called out: under the OLD
        75-day default, a statement dated 80 days before `as_of_ts` would
        have incorrectly been treated as available. Under the current
        (>= 90 day) conservative default, it must still be refused.
        """
        period_end = pd.Timestamp("2023-12-31")
        as_of_ts = period_end + pd.Timedelta(days=80)
        statement = _statement_with_period_end(period_end)

        result = _columns_on_or_before(statement, as_of_ts)

        assert result is None or period_end not in result.columns

    def test_statement_becomes_available_once_default_lag_has_elapsed(self):
        period_end = pd.Timestamp("2023-12-31")
        as_of_ts = period_end + pd.Timedelta(days=STATEMENT_FILING_LAG_DAYS)
        statement = _statement_with_period_end(period_end)

        result = _columns_on_or_before(statement, as_of_ts)

        assert result is not None
        assert period_end in result.columns

    def test_statement_one_day_before_lag_elapses_is_still_unavailable(self):
        period_end = pd.Timestamp("2023-12-31")
        as_of_ts = period_end + pd.Timedelta(days=STATEMENT_FILING_LAG_DAYS - 1)
        statement = _statement_with_period_end(period_end)

        result = _columns_on_or_before(statement, as_of_ts)

        assert result is None or period_end not in result.columns


class TestFilingLagRemainsPerCallConfigurable:
    """
    `filing_lag_days` stays an overridable parameter — a caller wanting a
    different (e.g. per-statement-type) lag isn't locked into the module
    default.
    """

    def test_explicit_shorter_lag_overrides_the_default(self):
        period_end = pd.Timestamp("2023-12-31")
        as_of_ts = period_end + pd.Timedelta(days=80)  # unavailable under the >=90-day default
        statement = _statement_with_period_end(period_end)

        result = _columns_on_or_before(statement, as_of_ts, filing_lag_days=75)

        assert result is not None
        assert period_end in result.columns

    def test_zero_lag_falls_back_to_period_end_only_matching(self):
        period_end = pd.Timestamp("2023-12-31")
        as_of_ts = period_end  # same day
        statement = _statement_with_period_end(period_end)

        result = _columns_on_or_before(statement, as_of_ts, filing_lag_days=0)

        assert result is not None
        assert period_end in result.columns
