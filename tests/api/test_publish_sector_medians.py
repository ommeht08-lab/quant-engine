"""
Group N: the sector-median publish runner's retry design.

Two independent retry layers, tested separately:
  - per-ticker retries happen INSIDE one `generate_sector_medians` call
    (via its `compute_ticker` parameter) -- a ticker that eventually
    succeeds is retried in place; tickers that already succeeded are
    never re-fetched; the whole universe is never regenerated.
  - publish retries reuse the SAME already-generated snapshot dict.

No network, no real database: `_compute_current_price_to_intrinsic` and
`publish_sector_median_snapshot` are monkeypatched throughout.
"""

from src.api import publish_sector_medians as runner
from src.dcf_model.dcf import DCFAssumptions


def _no_sleep(_seconds):
    pass


class TestComputeTickerWithRetries:
    def test_a_ticker_that_succeeds_on_the_first_attempt_is_not_retried(self, monkeypatch):
        calls = []

        def _fake_compute(ticker, assumptions):
            calls.append(ticker)
            return {"sector": "Technology", "price_to_intrinsic": 1.2}

        monkeypatch.setattr(runner, "_compute_current_price_to_intrinsic", _fake_compute)

        result = runner._compute_ticker_with_retries("AAPL", DCFAssumptions(), sleep=_no_sleep)

        assert result == {"sector": "Technology", "price_to_intrinsic": 1.2}
        assert calls == ["AAPL"]

    def test_a_ticker_that_fails_then_succeeds_is_retried_and_included(self, monkeypatch):
        attempts = {"n": 0}

        def _fake_compute(ticker, assumptions):
            attempts["n"] += 1
            if attempts["n"] < 3:
                return None
            return {"sector": "Technology", "price_to_intrinsic": 1.2}

        monkeypatch.setattr(runner, "_compute_current_price_to_intrinsic", _fake_compute)

        result = runner._compute_ticker_with_retries(
            "AAPL", DCFAssumptions(), max_attempts=3, sleep=_no_sleep
        )

        assert result == {"sector": "Technology", "price_to_intrinsic": 1.2}
        assert attempts["n"] == 3

    def test_a_ticker_that_never_succeeds_is_excluded_after_max_attempts(self, monkeypatch):
        attempts = {"n": 0}

        def _fake_compute(ticker, assumptions):
            attempts["n"] += 1
            return None

        monkeypatch.setattr(runner, "_compute_current_price_to_intrinsic", _fake_compute)

        result = runner._compute_ticker_with_retries(
            "AAPL", DCFAssumptions(), max_attempts=3, sleep=_no_sleep
        )

        assert result is None
        assert attempts["n"] == 3

    def test_a_raised_exception_is_treated_as_a_retryable_failure(self, monkeypatch):
        attempts = {"n": 0}

        def _fake_compute(ticker, assumptions):
            attempts["n"] += 1
            if attempts["n"] < 2:
                raise ConnectionError("transient network blip")
            return {"sector": "Technology", "price_to_intrinsic": 1.2}

        monkeypatch.setattr(runner, "_compute_current_price_to_intrinsic", _fake_compute)

        result = runner._compute_ticker_with_retries(
            "AAPL", DCFAssumptions(), max_attempts=3, sleep=_no_sleep
        )

        assert result == {"sector": "Technology", "price_to_intrinsic": 1.2}
        assert attempts["n"] == 2

    def test_backoff_is_applied_between_attempts_not_after_the_last_one(self, monkeypatch):
        sleeps = []
        monkeypatch.setattr(runner, "_compute_current_price_to_intrinsic", lambda t, a: None)

        runner._compute_ticker_with_retries(
            "AAPL", DCFAssumptions(), max_attempts=3, backoff_seconds=(2.0, 5.0), sleep=sleeps.append
        )

        assert sleeps == [2.0, 5.0]  # two backoffs for three attempts, none after the last

    def test_a_raised_exceptions_log_line_includes_only_the_exception_class_not_its_message(
        self, monkeypatch, caplog
    ):
        secret = "sk-live-supersecret-do-not-leak-9f8e7d"

        def _fake_compute(ticker, assumptions):
            raise ConnectionError(f"failed talking to postgresql://user:{secret}@host/db")

        monkeypatch.setattr(runner, "_compute_current_price_to_intrinsic", _fake_compute)

        with caplog.at_level("WARNING", logger="src.api.publish_sector_medians"):
            result = runner._compute_ticker_with_retries(
                "AAPL", DCFAssumptions(), max_attempts=2, sleep=_no_sleep
            )

        log_text = "\n".join(record.getMessage() for record in caplog.records)
        assert result is None
        assert secret not in log_text
        assert "ConnectionError" in log_text


class TestRunDoesNotRegenerateSuccessfulTickers:
    def test_generate_sector_medians_is_called_exactly_once_per_run(self, monkeypatch):
        generate_calls = {"n": 0}

        def _fake_generate(tickers=None, assumptions=None, compute_ticker=None):
            generate_calls["n"] += 1
            return {"generated_at": "x", "universe_size": 1, "tickers_used": 1}

        monkeypatch.setattr(runner, "generate_sector_medians", _fake_generate)
        monkeypatch.setattr(runner, "publish_sector_median_snapshot", lambda snapshot: (True, None))

        runner.run(sleep=_no_sleep)

        assert generate_calls["n"] == 1

    def test_each_ticker_is_computed_at_most_once_across_a_full_run(self, monkeypatch):
        """Confirms `generate_sector_medians`'s own per-ticker loop calls
        `compute_ticker` once per ticker (retries happen INSIDE that one
        call via `_compute_ticker_with_retries`, not by looping over the
        universe again) -- using the REAL `generate_sector_medians`."""
        from src.api.sector_medians import generate_sector_medians as real_generate

        call_counts = {}

        def _fake_compute(ticker, assumptions):
            call_counts[ticker] = call_counts.get(ticker, 0) + 1
            return {"sector": "Technology", "price_to_intrinsic": 1.2}

        monkeypatch.setattr(runner, "_compute_current_price_to_intrinsic", _fake_compute)
        monkeypatch.setattr("src.api.sector_medians.get_risk_free_rate", lambda: 0.04)

        def _compute_ticker(ticker, assumptions):
            return runner._compute_ticker_with_retries(ticker, assumptions, sleep=_no_sleep)

        real_generate(tickers=["AAA", "BBB", "CCC"], compute_ticker=_compute_ticker)

        assert call_counts == {"AAA": 1, "BBB": 1, "CCC": 1}


class TestRunPublishRetries:
    def test_a_transient_publish_failure_is_retried_with_the_same_snapshot(self, monkeypatch):
        snapshot = {"generated_at": "fixed-snapshot"}
        monkeypatch.setattr(runner, "generate_sector_medians", lambda **kwargs: snapshot)

        seen_snapshots = []
        attempts = {"n": 0}

        def _fake_publish(published_snapshot):
            seen_snapshots.append(published_snapshot)
            attempts["n"] += 1
            if attempts["n"] < 2:
                return False, "could not connect to the sector median database."
            return True, None

        monkeypatch.setattr(runner, "publish_sector_median_snapshot", _fake_publish)

        result = runner.run(publish_max_attempts=3, sleep=_no_sleep)

        assert result is True
        assert attempts["n"] == 2
        assert seen_snapshots == [snapshot, snapshot]  # the identical dict, never regenerated

    def test_a_validation_rejection_stops_immediately_without_exhausting_retries(self, monkeypatch):
        monkeypatch.setattr(runner, "generate_sector_medians", lambda **kwargs: {"generated_at": "x"})

        attempts = {"n": 0}

        def _fake_publish(snapshot):
            attempts["n"] += 1
            return False, "snapshot rejected: only 6/100 (6%) of the universe was successfully valued."

        monkeypatch.setattr(runner, "publish_sector_median_snapshot", _fake_publish)

        sleeps = []
        result = runner.run(publish_max_attempts=5, sleep=sleeps.append)

        assert result is False
        assert attempts["n"] == 1  # never retried a guaranteed-repeat rejection
        assert sleeps == []  # no wasted backoff either

    def test_repeated_transient_failures_eventually_give_up_and_return_false(self, monkeypatch):
        monkeypatch.setattr(runner, "generate_sector_medians", lambda **kwargs: {"generated_at": "x"})
        monkeypatch.setattr(
            runner,
            "publish_sector_median_snapshot",
            lambda snapshot: (False, "could not connect to the sector median database."),
        )

        sleeps = []
        result = runner.run(publish_max_attempts=3, publish_backoff_seconds=(1.0, 2.0), sleep=sleeps.append)

        assert result is False
        assert sleeps == [1.0, 2.0]  # backoff between attempts 1->2 and 2->3, none after the last


class TestRunGenerationFailure:
    """If `generate_sector_medians` itself raises unexpectedly (not a
    per-ticker failure — those are already handled inline by
    `_compute_ticker_with_retries` — but something breaking the whole
    generation call, e.g. `get_risk_free_rate()` raising), `run()` must
    return False, must NEVER call the publisher (there is no valid
    snapshot to publish), and therefore never touch — let alone
    overwrite — whatever snapshot is already published in the database."""

    def test_a_generation_failure_returns_false_and_never_calls_publish(self, monkeypatch):
        publish_calls = []

        def _boom(**kwargs):
            raise RuntimeError("get_risk_free_rate failed")

        monkeypatch.setattr(runner, "generate_sector_medians", _boom)
        monkeypatch.setattr(
            runner, "publish_sector_median_snapshot", lambda snapshot: publish_calls.append(snapshot) or (True, None)
        )

        result = runner.run(sleep=_no_sleep)

        assert result is False
        assert publish_calls == []  # the publisher must never be reached

    def test_a_generation_failure_logs_only_the_exception_class_not_its_message(self, monkeypatch, caplog):
        secret = "sk-live-supersecret-do-not-leak-9f8e7d"

        def _boom(**kwargs):
            raise RuntimeError(f"failed talking to postgresql://user:{secret}@host/db")

        monkeypatch.setattr(runner, "generate_sector_medians", _boom)
        monkeypatch.setattr(runner, "publish_sector_median_snapshot", lambda snapshot: (True, None))

        with caplog.at_level("WARNING", logger="src.api.publish_sector_medians"):
            result = runner.run(sleep=_no_sleep)

        log_text = "\n".join(record.getMessage() for record in caplog.records)
        assert result is False
        assert secret not in log_text
        assert "RuntimeError" in log_text
