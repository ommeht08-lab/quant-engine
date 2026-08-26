"""
Group M: Supabase-backed sector-median snapshot store.

`publish_sector_median_snapshot` / `fetch_latest_snapshot` /
`get_cached_latest_snapshot` must never touch a real database or the
network — every test here injects a fake, in-memory connection double
(`_FakeConnection`/`_FakeCursor`) implementing just the DB-API surface
this module actually calls (`cursor()`, `execute()`, `fetchone()`,
`commit()`, `close()`), so these tests exercise the real SQL statement
selection and row round-tripping without a real psycopg2 connection.
"""

import datetime
import json

import pytest

from src.api import sector_median_store as store
from src.api import sector_medians
from src.api.sector_median_thresholds import MAX_FUTURE_SKEW


def _valid_snapshot(
    *,
    generated_at="2026-08-20T12:00:00+00:00",
    universe_size=100,
    tickers_used=90,
    risk_free_rate=0.04,
    assumptions=None,
    sector_medians=None,
    sector_sample_counts=None,
):
    # Defaults are internally consistent: sector_sample_counts sums to
    # EXACTLY tickers_used (60 + 30 = 90) and coverage is 90% -- both
    # required to pass `_passes_publish_thresholds` by default. Tests
    # that override sector_medians/sector_sample_counts to something
    # deliberately inconsistent must also pass a matching tickers_used/
    # universe_size, or specify a defect that's rejected before that
    # check is even reached.
    return {
        "generated_at": generated_at,
        "universe_size": universe_size,
        "tickers_used": tickers_used,
        "risk_free_rate": risk_free_rate,
        "assumptions": assumptions
        if assumptions is not None
        else {
            "revenue_growth_rate": None,
            "operating_margin": None,
            "terminal_growth_rate": 0.025,
        },
        "sector_medians": sector_medians or {"Technology": 1.2, "Industrials": 0.9},
        "sector_sample_counts": sector_sample_counts or {"Technology": 60, "Industrials": 30},
    }


class _FakeCursor:
    def __init__(self, conn):
        self._conn = conn
        self._result = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, sql, params=None):
        normalized = " ".join(sql.split())
        if normalized.startswith("CREATE TABLE"):
            self._conn.schema_created += 1
        elif normalized.startswith("CREATE INDEX"):
            self._conn.index_created += 1
        elif normalized.startswith("INSERT INTO sector_median_snapshots"):
            (
                generated_at,
                universe_size,
                tickers_used,
                risk_free_rate,
                assumptions,
                sector_medians,
                sector_sample_counts,
            ) = params
            self._conn.rows.append(
                {
                    "id": len(self._conn.rows) + 1,
                    "generated_at": generated_at,
                    "universe_size": universe_size,
                    "tickers_used": tickers_used,
                    "risk_free_rate": risk_free_rate,
                    "assumptions": json.loads(assumptions),
                    "sector_medians": json.loads(sector_medians),
                    "sector_sample_counts": json.loads(sector_sample_counts),
                }
            )
        elif normalized.startswith("SELECT generated_at"):
            if not self._conn.rows:
                self._result = None
            else:
                latest = max(self._conn.rows, key=lambda r: (r["generated_at"], r["id"]))
                self._result = (
                    latest["generated_at"],
                    latest["universe_size"],
                    latest["tickers_used"],
                    latest["risk_free_rate"],
                    latest["assumptions"],
                    latest["sector_medians"],
                    latest["sector_sample_counts"],
                )
        else:
            raise AssertionError(f"Unexpected SQL statement: {sql}")

    def fetchone(self):
        return self._result


class _FakeConnection:
    def __init__(self):
        self.rows = []
        self.schema_created = 0
        self.index_created = 0
        self.committed = 0
        self.closed = False

    def cursor(self):
        return _FakeCursor(self)

    def commit(self):
        self.committed += 1

    def close(self):
        self.closed = True


class _RaisingCursor:
    """Simulates a read against a table that doesn't exist yet (or any
    other query-level failure) -- `execute` raises, `fetchone` is never
    reached."""

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, sql, params=None):
        raise RuntimeError('relation "sector_median_snapshots" does not exist')

    def fetchone(self):  # pragma: no cover - never reached
        return None


class _RaisingConnection:
    """A connection whose cursor always raises on `execute` -- proves a
    read failure degrades gracefully without ever attempting schema
    creation (the read path must never run DDL, regardless of why the
    query itself failed)."""

    def __init__(self):
        self.schema_created = 0
        self.index_created = 0
        self.closed = False

    def cursor(self):
        return _RaisingCursor()

    def close(self):
        self.closed = True


class _FixedRowCursor:
    """Returns a pre-built row verbatim from `fetchone`, whatever its
    shape — lets a test hand `fetch_latest_snapshot` a malformed row
    (wrong column count, invalid JSON text in a JSONB-shaped column)
    without needing a real INSERT to get it there."""

    def __init__(self, row):
        self._row = row

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, sql, params=None):
        pass

    def fetchone(self):
        return self._row


class _FixedRowConnection:
    def __init__(self, row):
        self._row = row
        self.schema_created = 0
        self.index_created = 0
        self.closed = False

    def cursor(self):
        return _FixedRowCursor(self._row)

    def close(self):
        self.closed = True


class _ConnectSpy:
    """Records every call to `store._connect` (args/kwargs) and returns a
    pre-built fake connection, so a test can assert exactly what
    application_name/timeouts a code path used to connect."""

    def __init__(self, conn):
        self.calls = []
        self._conn = conn

    def __call__(self, database_url, **kwargs):
        self.calls.append({"database_url": database_url, **kwargs})
        return self._conn


@pytest.fixture(autouse=True)
def _reset_module_state():
    store._reset_cache_for_tests()
    yield
    store._reset_cache_for_tests()


class TestSharedFutureSkewConstant:
    """The 5-minute future-skew tolerance is now a single source of
    truth in `sector_median_thresholds` -- both the publisher
    (`sector_median_store`) and the reader (`sector_medians`) must
    import the SAME object, not each pin their own copy."""

    def test_store_and_sector_medians_import_the_identical_constant_object(self):
        assert store.MAX_FUTURE_SKEW is MAX_FUTURE_SKEW
        assert sector_medians.MAX_FUTURE_SKEW is MAX_FUTURE_SKEW

    def test_the_shared_constant_is_five_minutes(self):
        assert MAX_FUTURE_SKEW == datetime.timedelta(minutes=5)


class TestPassesPublishThresholds:
    def test_a_healthy_snapshot_passes(self):
        ok, reason = store._passes_publish_thresholds(_valid_snapshot())
        assert ok is True
        assert reason is None

    def test_missing_generated_at_is_rejected(self):
        snapshot = _valid_snapshot()
        snapshot["generated_at"] = None
        ok, reason = store._passes_publish_thresholds(snapshot)
        assert ok is False
        assert "generated_at" in reason

    def test_unparseable_generated_at_is_rejected(self):
        snapshot = _valid_snapshot(generated_at="not-a-timestamp")
        ok, reason = store._passes_publish_thresholds(snapshot)
        assert ok is False
        assert "unparseable" in reason.lower()

    def test_timezone_naive_generated_at_is_rejected(self):
        snapshot = _valid_snapshot(generated_at="2026-08-20T12:00:00")
        ok, reason = store._passes_publish_thresholds(snapshot)
        assert ok is False
        assert "timezone-naive" in reason.lower()

    def test_a_future_generated_at_is_rejected(self):
        future = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=1)
        snapshot = _valid_snapshot(generated_at=future.isoformat())
        ok, reason = store._passes_publish_thresholds(snapshot)
        assert ok is False
        assert "future" in reason.lower()

    def test_a_timestamp_within_the_future_clock_skew_tolerance_is_accepted(self):
        near_future = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=30)
        snapshot = _valid_snapshot(generated_at=near_future.isoformat())
        ok, reason = store._passes_publish_thresholds(snapshot)
        assert ok is True
        assert reason is None

    def test_malformed_assumptions_missing_a_key_is_rejected(self):
        snapshot = _valid_snapshot(assumptions={"revenue_growth_rate": None, "operating_margin": None})
        ok, reason = store._passes_publish_thresholds(snapshot)
        assert ok is False
        assert "assumptions" in reason.lower()

    def test_malformed_assumptions_extra_key_is_rejected(self):
        snapshot = _valid_snapshot(
            assumptions={
                "revenue_growth_rate": None,
                "operating_margin": None,
                "terminal_growth_rate": 0.025,
                "unexpected_extra_key": 1,
            }
        )
        ok, reason = store._passes_publish_thresholds(snapshot)
        assert ok is False
        assert "assumptions" in reason.lower()

    def test_malformed_assumptions_wrong_value_type_is_rejected(self):
        snapshot = _valid_snapshot(
            assumptions={"revenue_growth_rate": "not-a-number", "operating_margin": None, "terminal_growth_rate": 0.025}
        )
        ok, reason = store._passes_publish_thresholds(snapshot)
        assert ok is False
        assert "assumptions" in reason.lower()

    def test_low_overall_coverage_is_rejected_even_with_a_healthy_sector(self):
        # Mirrors the exact shape of the old stale committed cache: a
        # sector with plenty of samples, but the run only valued 6% of
        # the whole universe.
        snapshot = _valid_snapshot(
            universe_size=100,
            tickers_used=6,
            sector_medians={"Technology": 1.2},
            sector_sample_counts={"Technology": 6},
        )
        ok, reason = store._passes_publish_thresholds(snapshot)
        assert ok is False
        assert "6/100" in reason

    def test_zero_universe_size_is_rejected_without_a_divide_by_zero_crash(self):
        snapshot = _valid_snapshot(universe_size=0, tickers_used=0, sector_medians={}, sector_sample_counts={})
        ok, reason = store._passes_publish_thresholds(snapshot)
        assert ok is False

    def test_tickers_used_exceeding_universe_size_is_rejected(self):
        snapshot = _valid_snapshot(universe_size=50, tickers_used=90)
        ok, reason = store._passes_publish_thresholds(snapshot)
        assert ok is False
        assert "exceeds" in reason.lower()

    def test_a_float_count_that_happens_to_be_whole_is_rejected(self):
        """`universe_size`/`tickers_used` must be genuine `int`s -- a
        `float` (even a whole one like `100.0`) indicates the generator's
        own counting logic was bypassed or tampered with."""
        snapshot = _valid_snapshot(universe_size=100.0, tickers_used=90)
        ok, reason = store._passes_publish_thresholds(snapshot)
        assert ok is False

    def test_a_non_positive_sector_median_is_rejected(self):
        snapshot = _valid_snapshot(
            sector_medians={"Technology": 0.0, "Industrials": 0.9},
            sector_sample_counts={"Technology": 60, "Industrials": 30},
        )
        ok, reason = store._passes_publish_thresholds(snapshot)
        assert ok is False
        assert "positive" in reason.lower()

    def test_a_negative_sector_median_is_rejected(self):
        snapshot = _valid_snapshot(
            sector_medians={"Technology": -1.2, "Industrials": 0.9},
            sector_sample_counts={"Technology": 60, "Industrials": 30},
        )
        ok, reason = store._passes_publish_thresholds(snapshot)
        assert ok is False
        assert "positive" in reason.lower()

    def test_a_non_finite_sector_median_is_rejected(self):
        snapshot = _valid_snapshot(
            sector_medians={"Technology": float("inf"), "Industrials": 0.9},
            sector_sample_counts={"Technology": 60, "Industrials": 30},
        )
        ok, reason = store._passes_publish_thresholds(snapshot)
        assert ok is False

    def test_a_non_integer_sector_sample_count_is_rejected(self):
        snapshot = _valid_snapshot(sector_sample_counts={"Technology": 60.0, "Industrials": 30})
        ok, reason = store._passes_publish_thresholds(snapshot)
        assert ok is False
        assert "integer" in reason.lower()

    def test_sector_sample_counts_summing_below_tickers_used_is_rejected(self):
        snapshot = _valid_snapshot(
            tickers_used=90, sector_sample_counts={"Technology": 12, "Industrials": 4}  # sums to 16, not 90
        )
        ok, reason = store._passes_publish_thresholds(snapshot)
        assert ok is False
        assert "doesn't match" in reason.lower()

    def test_sector_sample_counts_summing_above_tickers_used_is_rejected(self):
        snapshot = _valid_snapshot(
            tickers_used=90, sector_sample_counts={"Technology": 60, "Industrials": 40}  # sums to 100, not 90
        )
        ok, reason = store._passes_publish_thresholds(snapshot)
        assert ok is False
        assert "doesn't match" in reason.lower()

    def test_no_sector_reaching_minimum_sample_size_is_rejected(self):
        snapshot = _valid_snapshot(
            universe_size=6,
            tickers_used=3,
            sector_medians={"Technology": 1.2, "Industrials": 0.9},
            sector_sample_counts={"Technology": 2, "Industrials": 1},
        )
        ok, reason = store._passes_publish_thresholds(snapshot)
        assert ok is False
        assert "minimum sample size" in reason

    def test_at_least_one_qualifying_sector_is_sufficient(self):
        # One thin sector alongside one healthy one still publishes —
        # per-sector refusal for the thin one happens at READ time
        # instead (see evaluate_sector_median_cache), not here.
        snapshot = _valid_snapshot(
            universe_size=20,
            tickers_used=13,
            sector_medians={"Technology": 1.2, "Industrials": 0.9},
            sector_sample_counts={"Technology": 12, "Industrials": 1},
        )
        ok, reason = store._passes_publish_thresholds(snapshot)
        assert ok is True
        assert reason is None

    def test_mismatched_sector_keys_are_rejected(self):
        snapshot = _valid_snapshot()
        snapshot["sector_sample_counts"] = {"Technology": 90}  # missing "Industrials"
        ok, reason = store._passes_publish_thresholds(snapshot)
        assert ok is False

    def test_invalid_risk_free_rate_is_rejected(self):
        snapshot = _valid_snapshot(risk_free_rate=float("nan"))
        ok, reason = store._passes_publish_thresholds(snapshot)
        assert ok is False
        assert "risk_free_rate" in reason


class TestPublishSectorMedianSnapshot:
    def test_valid_snapshot_publishes_and_is_fetchable(self):
        conn = _FakeConnection()
        snapshot = _valid_snapshot()

        published, reason = store.publish_sector_median_snapshot(snapshot, conn=conn)

        assert published is True
        assert reason is None
        assert conn.schema_created == 1
        # One commit for ensure_schema's DDL, one for the INSERT itself.
        assert conn.committed == 2
        assert len(conn.rows) == 1

        fetched, fetch_reason = store.fetch_latest_snapshot(conn=conn)
        assert fetch_reason is None
        assert fetched["sector_medians"] == snapshot["sector_medians"]
        assert fetched["tickers_used"] == snapshot["tickers_used"]

    def test_invalid_snapshot_is_rejected_without_touching_the_connection(self):
        conn = _FakeConnection()
        snapshot = _valid_snapshot(universe_size=100, tickers_used=6, sector_sample_counts={"Technology": 6})

        published, reason = store.publish_sector_median_snapshot(snapshot, conn=conn)

        assert published is False
        assert reason is not None
        assert conn.rows == []
        assert conn.schema_created == 0

    def test_a_rejected_run_preserves_the_prior_valid_snapshot_as_latest(self):
        conn = _FakeConnection()
        good_snapshot = _valid_snapshot(generated_at="2026-08-18T12:00:00+00:00")
        bad_snapshot = _valid_snapshot(
            generated_at="2026-08-20T12:00:00+00:00",
            universe_size=100,
            tickers_used=6,
            sector_sample_counts={"Technology": 6},
        )

        published_good, _ = store.publish_sector_median_snapshot(good_snapshot, conn=conn)
        published_bad, reason_bad = store.publish_sector_median_snapshot(bad_snapshot, conn=conn)

        assert published_good is True
        assert published_bad is False
        assert reason_bad is not None

        fetched, fetch_reason = store.fetch_latest_snapshot(conn=conn)
        assert fetch_reason is None
        assert fetched["generated_at"] == good_snapshot["generated_at"]

    def test_a_future_timestamp_snapshot_preserves_the_prior_valid_snapshot(self):
        conn = _FakeConnection()
        good_snapshot = _valid_snapshot(generated_at="2026-08-18T12:00:00+00:00")
        future = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=1)
        future_snapshot = _valid_snapshot(generated_at=future.isoformat())

        store.publish_sector_median_snapshot(good_snapshot, conn=conn)
        published, reason = store.publish_sector_median_snapshot(future_snapshot, conn=conn)

        assert published is False
        assert reason is not None
        fetched, _ = store.fetch_latest_snapshot(conn=conn)
        assert fetched["generated_at"] == good_snapshot["generated_at"]
        assert len(conn.rows) == 1

    def test_malformed_assumptions_preserves_the_prior_valid_snapshot(self):
        conn = _FakeConnection()
        good_snapshot = _valid_snapshot(generated_at="2026-08-18T12:00:00+00:00")
        malformed_snapshot = _valid_snapshot(
            generated_at="2026-08-20T12:00:00+00:00",
            assumptions={"revenue_growth_rate": "bogus", "operating_margin": None, "terminal_growth_rate": 0.025},
        )

        store.publish_sector_median_snapshot(good_snapshot, conn=conn)
        published, reason = store.publish_sector_median_snapshot(malformed_snapshot, conn=conn)

        assert published is False
        fetched, _ = store.fetch_latest_snapshot(conn=conn)
        assert fetched["generated_at"] == good_snapshot["generated_at"]
        assert len(conn.rows) == 1

    def test_malformed_sector_medians_preserves_the_prior_valid_snapshot(self):
        conn = _FakeConnection()
        good_snapshot = _valid_snapshot(generated_at="2026-08-18T12:00:00+00:00")
        malformed_snapshot = _valid_snapshot(
            generated_at="2026-08-20T12:00:00+00:00",
            sector_medians={"Technology": -5.0, "Industrials": 0.9},
        )

        store.publish_sector_median_snapshot(good_snapshot, conn=conn)
        published, reason = store.publish_sector_median_snapshot(malformed_snapshot, conn=conn)

        assert published is False
        fetched, _ = store.fetch_latest_snapshot(conn=conn)
        assert fetched["generated_at"] == good_snapshot["generated_at"]
        assert len(conn.rows) == 1

    def test_malformed_sector_sample_counts_preserves_the_prior_valid_snapshot(self):
        conn = _FakeConnection()
        good_snapshot = _valid_snapshot(generated_at="2026-08-18T12:00:00+00:00")
        malformed_snapshot = _valid_snapshot(
            generated_at="2026-08-20T12:00:00+00:00",
            sector_sample_counts={"Technology": 60.0, "Industrials": 30},
        )

        store.publish_sector_median_snapshot(good_snapshot, conn=conn)
        published, reason = store.publish_sector_median_snapshot(malformed_snapshot, conn=conn)

        assert published is False
        fetched, _ = store.fetch_latest_snapshot(conn=conn)
        assert fetched["generated_at"] == good_snapshot["generated_at"]
        assert len(conn.rows) == 1

    def test_a_second_valid_run_supersedes_the_first_as_latest(self):
        conn = _FakeConnection()
        first = _valid_snapshot(generated_at="2026-08-18T12:00:00+00:00")
        second = _valid_snapshot(generated_at="2026-08-20T12:00:00+00:00")

        store.publish_sector_median_snapshot(first, conn=conn)
        store.publish_sector_median_snapshot(second, conn=conn)

        fetched, _ = store.fetch_latest_snapshot(conn=conn)
        assert fetched["generated_at"] == second["generated_at"]
        assert len(conn.rows) == 2  # append-only: the first row is never deleted/overwritten

    def test_missing_database_url_fails_gracefully_without_raising(self, monkeypatch):
        monkeypatch.setattr(store, "_get_database_url", lambda: None)

        published, reason = store.publish_sector_median_snapshot(_valid_snapshot())

        assert published is False
        assert "DATABASE_URL" in reason

    def test_a_connection_failure_returns_a_generic_reason_with_no_leaked_details(self, monkeypatch):
        secret_url = "postgresql://realuser:supersecret@prod-host:5432/realdb"
        monkeypatch.setattr(store, "_get_database_url", lambda: secret_url)

        def _boom(database_url, **kwargs):
            raise ConnectionError(f"could not connect to {database_url}")

        monkeypatch.setattr(store, "_connect", _boom)

        published, reason = store.publish_sector_median_snapshot(_valid_snapshot())

        assert published is False
        assert "supersecret" not in reason
        assert "realuser" not in reason

    def test_a_connection_failure_logs_only_the_exception_class_not_its_message(self, monkeypatch, caplog):
        secret_url = "postgresql://realuser:supersecret@prod-host:5432/realdb"
        monkeypatch.setattr(store, "_get_database_url", lambda: secret_url)

        def _boom(database_url, **kwargs):
            raise ConnectionError(f"could not connect to {database_url}")

        monkeypatch.setattr(store, "_connect", _boom)

        with caplog.at_level("WARNING", logger="src.api.sector_median_store"):
            store.publish_sector_median_snapshot(_valid_snapshot())

        log_text = "\n".join(record.getMessage() for record in caplog.records)
        assert "supersecret" not in log_text
        assert "realuser" not in log_text
        assert "ConnectionError" in log_text

    def test_publish_connects_with_the_bounded_publish_profile(self, monkeypatch):
        conn = _FakeConnection()
        spy = _ConnectSpy(conn)
        monkeypatch.setattr(store, "_get_database_url", lambda: "postgresql://x/y")
        monkeypatch.setattr(store, "_connect", spy)

        store.publish_sector_median_snapshot(_valid_snapshot())

        assert len(spy.calls) == 1
        call = spy.calls[0]
        assert call["application_name"] == store.PUBLISH_APPLICATION_NAME
        assert call["connect_timeout_seconds"] == store.PUBLISH_CONNECT_TIMEOUT_SECONDS
        assert call["statement_timeout_ms"] == store.PUBLISH_STATEMENT_TIMEOUT_MS


class TestFetchLatestSnapshot:
    def test_empty_table_reports_nothing_published_yet(self):
        conn = _FakeConnection()
        snapshot, reason = store.fetch_latest_snapshot(conn=conn)
        assert snapshot is None
        assert "published" in reason.lower()
        # The read path must never run schema DDL, even against a
        # brand-new/empty table.
        assert conn.schema_created == 0
        assert conn.index_created == 0

    def test_the_read_path_never_issues_schema_ddl_even_on_a_populated_table(self):
        conn = _FakeConnection()
        store.publish_sector_median_snapshot(_valid_snapshot(), conn=conn)
        schema_created_after_publish = conn.schema_created
        assert schema_created_after_publish >= 1  # the publish path DID create it

        store.fetch_latest_snapshot(conn=conn)

        # The read immediately after must not have created (or
        # re-created) the schema again.
        assert conn.schema_created == schema_created_after_publish
        assert conn.index_created == schema_created_after_publish

    def test_a_query_failure_degrades_gracefully_without_attempting_schema_creation(self):
        """Simulates reading against a table that doesn't exist yet (or
        any other query-level failure): the read path must degrade to a
        generic "unavailable" reason, never raise, and never attempt to
        create the schema itself -- that's the publisher's job alone."""
        conn = _RaisingConnection()

        snapshot, reason = store.fetch_latest_snapshot(conn=conn)

        assert snapshot is None
        assert reason is not None
        assert conn.schema_created == 0
        assert conn.index_created == 0

    def test_missing_database_url_fails_gracefully_without_raising(self, monkeypatch):
        monkeypatch.setattr(store, "_get_database_url", lambda: None)

        snapshot, reason = store.fetch_latest_snapshot()

        assert snapshot is None
        assert "DATABASE_URL" in reason

    def test_a_read_failure_returns_a_generic_reason_with_no_leaked_details(self, monkeypatch):
        secret_url = "postgresql://realuser:supersecret@prod-host:5432/realdb"
        monkeypatch.setattr(store, "_get_database_url", lambda: secret_url)

        def _boom(database_url, **kwargs):
            raise ConnectionError(f"could not connect to {database_url}")

        monkeypatch.setattr(store, "_connect", _boom)

        snapshot, reason = store.fetch_latest_snapshot()

        assert snapshot is None
        assert "supersecret" not in reason
        assert "realuser" not in reason

    def test_a_read_failure_logs_only_the_exception_class_not_its_message(self, monkeypatch, caplog):
        secret_url = "postgresql://realuser:supersecret@prod-host:5432/realdb"
        monkeypatch.setattr(store, "_get_database_url", lambda: secret_url)

        def _boom(database_url, **kwargs):
            raise ConnectionError(f"could not connect to {database_url}")

        monkeypatch.setattr(store, "_connect", _boom)

        with caplog.at_level("WARNING", logger="src.api.sector_median_store"):
            store.fetch_latest_snapshot()

        log_text = "\n".join(record.getMessage() for record in caplog.records)
        assert "supersecret" not in log_text
        assert "realuser" not in log_text
        assert "ConnectionError" in log_text

    def test_fetch_connects_with_the_bounded_read_profile(self, monkeypatch):
        conn = _FakeConnection()
        spy = _ConnectSpy(conn)
        monkeypatch.setattr(store, "_get_database_url", lambda: "postgresql://x/y")
        monkeypatch.setattr(store, "_connect", spy)

        store.fetch_latest_snapshot()

        assert len(spy.calls) == 1
        call = spy.calls[0]
        assert call["application_name"] == store.READ_APPLICATION_NAME
        assert call["connect_timeout_seconds"] == store.READ_CONNECT_TIMEOUT_SECONDS
        assert call["statement_timeout_ms"] == store.READ_STATEMENT_TIMEOUT_MS

    def test_read_timeouts_are_short_bounded_seconds_well_under_vercels_40s_limit(self):
        """Documents the actual bound, not just that a value exists: the
        read path's worst-case connect+statement timeout must leave
        comfortable headroom under Vercel's 40s /api/evaluate function
        ceiling, shared with a live yfinance fetch and a full DCF
        valuation."""
        worst_case_seconds = store.READ_CONNECT_TIMEOUT_SECONDS + (store.READ_STATEMENT_TIMEOUT_MS / 1000)
        assert worst_case_seconds <= 10

    def test_generated_at_round_trips_as_an_iso_string_even_from_a_datetime_object(self):
        """A real TIMESTAMPTZ column comes back from psycopg2 as an
        actual timezone-aware `datetime`, not a string -- `_row_to_snapshot`
        must normalize it back to ISO text so `evaluate_sector_median_cache`
        (which calls `datetime.fromisoformat`, requiring a `str`) keeps
        working identically for both the file-based and database-backed
        loaders."""
        row = (
            datetime.datetime(2026, 8, 20, 12, 0, 0, tzinfo=datetime.timezone.utc),
            100,
            90,
            0.04,
            {"revenue_growth_rate": None, "operating_margin": None, "terminal_growth_rate": 0.025},
            {"Technology": 1.2},
            {"Technology": 12},
        )
        snapshot = store._row_to_snapshot(row)
        assert snapshot["generated_at"] == "2026-08-20T12:00:00+00:00"
        assert isinstance(snapshot["generated_at"], str)


class TestFetchLatestSnapshotMalformedRow:
    """`fetch_latest_snapshot`'s own 'never raises' contract must hold
    even when the DATABASE ITSELF hands back a malformed row -- a wrong
    column count (tuple-unpack inside `_row_to_snapshot` raises
    `ValueError`) or invalid JSON text in a JSONB-shaped column
    (`json.loads` raises `json.JSONDecodeError`)."""

    def test_a_row_with_the_wrong_column_count_never_raises(self):
        conn = _FixedRowConnection(("only", "three", "fields"))

        snapshot, reason = store.fetch_latest_snapshot(conn=conn)

        assert snapshot is None
        assert reason is not None
        assert conn.schema_created == 0  # still never attempts DDL

    def test_invalid_json_text_in_a_jsonb_column_never_raises(self):
        row = (
            "2026-08-20T12:00:00+00:00",
            100,
            90,
            0.04,
            "{not valid json",  # assumptions
            "{}",
            "{}",
        )
        conn = _FixedRowConnection(row)

        snapshot, reason = store.fetch_latest_snapshot(conn=conn)

        assert snapshot is None
        assert reason is not None

    def test_a_non_string_non_dict_jsonb_value_never_raises(self):
        """`json.loads` on a non-str/bytes value (e.g. a bare int
        leaking through from a corrupted column) raises `TypeError`, not
        `json.JSONDecodeError` -- both must be caught."""
        row = ("2026-08-20T12:00:00+00:00", 100, 90, 0.04, 12345, "{}", "{}")
        conn = _FixedRowConnection(row)

        snapshot, reason = store.fetch_latest_snapshot(conn=conn)

        assert snapshot is None
        assert reason is not None

    def test_malformed_row_logs_only_the_exception_class(self, caplog):
        secret = "sk-live-supersecret-do-not-leak-9f8e7d"
        row = (
            "2026-08-20T12:00:00+00:00",
            100,
            90,
            0.04,
            f'{{"password": "{secret}"',  # deliberately invalid JSON (unterminated) containing a sentinel
            "{}",
            "{}",
        )
        conn = _FixedRowConnection(row)

        with caplog.at_level("WARNING", logger="src.api.sector_median_store"):
            snapshot, reason = store.fetch_latest_snapshot(conn=conn)

        log_text = "\n".join(record.getMessage() for record in caplog.records)
        assert snapshot is None
        assert secret not in log_text
        assert secret not in (reason or "")
        assert "Error" in log_text  # the exception CLASS is still logged


class TestGetCachedLatestSnapshot:
    def test_repeated_calls_within_ttl_hit_the_database_only_once(self, monkeypatch):
        call_count = {"n": 0}

        def _fake_fetch(*args, **kwargs):
            call_count["n"] += 1
            return _valid_snapshot(), None

        monkeypatch.setattr(store, "fetch_latest_snapshot", _fake_fetch)

        first, _ = store.get_cached_latest_snapshot(ttl_seconds=60.0)
        second, _ = store.get_cached_latest_snapshot(ttl_seconds=60.0)

        assert call_count["n"] == 1
        assert first == second

    def test_a_call_after_ttl_expiry_refetches(self, monkeypatch):
        call_count = {"n": 0}
        fake_time = {"t": 1000.0}

        def _fake_fetch(*args, **kwargs):
            call_count["n"] += 1
            return _valid_snapshot(generated_at=f"2026-08-{18 + call_count['n']:02d}T00:00:00+00:00"), None

        monkeypatch.setattr(store, "fetch_latest_snapshot", _fake_fetch)
        monkeypatch.setattr(store.time, "monotonic", lambda: fake_time["t"])

        first, _ = store.get_cached_latest_snapshot(ttl_seconds=10.0)
        fake_time["t"] += 20.0  # advance past the TTL
        second, _ = store.get_cached_latest_snapshot(ttl_seconds=10.0)

        assert call_count["n"] == 2
        assert first != second

    def test_a_fetch_failure_is_cached_too_not_retried_every_call_within_ttl(self, monkeypatch):
        call_count = {"n": 0}

        def _fake_fetch(*args, **kwargs):
            call_count["n"] += 1
            return None, "No sector median snapshot has been published yet."

        monkeypatch.setattr(store, "fetch_latest_snapshot", _fake_fetch)

        snapshot, reason = store.get_cached_latest_snapshot(ttl_seconds=60.0)
        snapshot2, reason2 = store.get_cached_latest_snapshot(ttl_seconds=60.0)

        assert call_count["n"] == 1
        assert snapshot is None and snapshot2 is None
        assert reason == reason2
