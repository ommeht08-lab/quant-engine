"""
Exact, replayable price inputs for the SEC backtest pilot.

Yahoo's adjusted price history is not bit-stable between downloads, so a
pilot record is only auditable if it preserves the observations its signals
were computed from. ``RecordingPriceProvider`` captures every price the run
reads, in the same run as the calculations; ``SnapshotPriceProvider``
replays them offline and refuses any read the snapshot does not cover.

Snapshots are serialized canonically (sorted keys, ``repr`` floats, which
round-trip exactly) and identified by the SHA-256 of those bytes. They are
stored append-only in the project's private Postgres database. The public
validation record carries only the checksum and provenance: the repository
is public and Yahoo's redistribution terms have not been cleared.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timezone
from typing import Dict, Optional, Tuple

import pandas as pd

from src.backtesting.sec_pilot import PriceProvider

SNAPSHOT_FORMAT_VERSION = "pilot-price-snapshot-v1"
SNAPSHOT_TABLE = "backtest_price_snapshots"

CREATE_SNAPSHOT_TABLE_SQL = f"""
CREATE TABLE IF NOT EXISTS {SNAPSHOT_TABLE} (
    snapshot_sha256 TEXT PRIMARY KEY,
    format_version TEXT NOT NULL,
    source TEXT NOT NULL,
    captured_at TIMESTAMPTZ NOT NULL,
    github_run_id TEXT,
    pilot_policy_version TEXT NOT NULL,
    payload TEXT NOT NULL,
    stored_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""
INSERT_SNAPSHOT_SQL = f"""
INSERT INTO {SNAPSHOT_TABLE}
    (snapshot_sha256, format_version, source, captured_at, github_run_id, pilot_policy_version, payload)
VALUES (%s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (snapshot_sha256) DO NOTHING;
"""
SELECT_SNAPSHOT_SQL = f"SELECT payload FROM {SNAPSHOT_TABLE} WHERE snapshot_sha256 = %s;"


class SnapshotIntegrityError(RuntimeError):
    """A stored or supplied snapshot does not match its checksum or coverage."""


def _day(value) -> date:
    return value if isinstance(value, date) and not isinstance(value, datetime) else pd.Timestamp(value).date()


class RecordingPriceProvider(PriceProvider):
    """Delegates to a live provider and records exactly what it returned."""

    def __init__(self, inner: PriceProvider):
        self._inner = inner
        self._adjusted: Dict[str, Dict[str, Tuple[float, float]]] = {}
        self._requests: Dict[str, Tuple[date, date]] = {}
        self._raw: Dict[str, Dict[str, Optional[float]]] = {}

    def adjusted(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        frame = self._inner.adjusted(symbol, start, end)
        rows = self._adjusted.setdefault(symbol, {})
        for ts, row in frame.iterrows():
            rows[_day(ts).isoformat()] = (float(row["Open"]), float(row["Close"]))
        low, high = self._requests.get(symbol, (start, end))
        self._requests[symbol] = (min(low, start), max(high, end))
        return frame

    def raw_close(self, symbol: str, on: date) -> Optional[float]:
        value = self._inner.raw_close(symbol, on)
        self._raw.setdefault(symbol, {})[on.isoformat()] = None if value is None else float(value)
        return value

    def snapshot(self, *, source: str, captured_at: datetime) -> dict:
        return {
            "format_version": SNAPSHOT_FORMAT_VERSION,
            "source": source,
            "captured_at": captured_at.astimezone(timezone.utc).isoformat(),
            "symbols": {
                symbol: {
                    # Requested range: replay refuses reads outside it, and an
                    # absent day inside it is a genuine non-session.
                    "requested_range": [low.isoformat(), high.isoformat()],
                    "adjusted_open_close": {day: list(values) for day, values in sorted(self._adjusted.get(symbol, {}).items())},
                    "raw_close": dict(sorted(self._raw.get(symbol, {}).items())),
                }
                for symbol, (low, high) in sorted(self._requests.items())
            }
            | {
                symbol: {"requested_range": None, "adjusted_open_close": {}, "raw_close": dict(sorted(values.items()))}
                for symbol, values in sorted(self._raw.items())
                if symbol not in self._requests
            },
        }


def canonical_bytes(snapshot: dict) -> bytes:
    return json.dumps(snapshot, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def snapshot_sha256(snapshot: dict) -> str:
    return hashlib.sha256(canonical_bytes(snapshot)).hexdigest()


class SnapshotPriceProvider(PriceProvider):
    """Offline replay; any read outside the captured coverage is refused."""

    def __init__(self, snapshot: dict):
        if snapshot.get("format_version") != SNAPSHOT_FORMAT_VERSION:
            raise SnapshotIntegrityError("Unsupported price snapshot format.")
        self._symbols = snapshot["symbols"]

    def adjusted(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        entry = self._symbols.get(symbol)
        requested = entry and entry["requested_range"]
        if not requested or start < date.fromisoformat(requested[0]) or end > date.fromisoformat(requested[1]):
            raise SnapshotIntegrityError(f"Snapshot does not cover {symbol} {start}..{end}.")
        rows = [
            (pd.Timestamp(day), values[0], values[1])
            for day, values in entry["adjusted_open_close"].items()
            if start <= date.fromisoformat(day) <= end
        ]
        frame = pd.DataFrame(rows, columns=["Date", "Open", "Close"]).set_index("Date")
        frame.index = pd.DatetimeIndex(frame.index)
        return frame

    def raw_close(self, symbol: str, on: date) -> Optional[float]:
        values = (self._symbols.get(symbol) or {}).get("raw_close", {})
        if on.isoformat() not in values:
            raise SnapshotIntegrityError(f"Snapshot does not cover the raw close for {symbol} on {on}.")
        return values[on.isoformat()]


def store_snapshot(
    snapshot: dict, *, database_url: str, github_run_id: Optional[str], pilot_policy_version: str
) -> str:
    """Append the snapshot to private storage, then read it back and re-verify."""

    import psycopg2

    digest = snapshot_sha256(snapshot)
    payload = canonical_bytes(snapshot).decode("utf-8")
    connection = psycopg2.connect(database_url, application_name="sec-pilot-price-snapshot", connect_timeout=10)
    try:
        with connection:
            with connection.cursor() as cursor:
                cursor.execute(CREATE_SNAPSHOT_TABLE_SQL)
                cursor.execute(
                    INSERT_SNAPSHOT_SQL,
                    (
                        digest,
                        snapshot["format_version"],
                        snapshot["source"],
                        snapshot["captured_at"],
                        github_run_id,
                        pilot_policy_version,
                        payload,
                    ),
                )
    finally:
        connection.close()
    stored = load_snapshot(digest, database_url=database_url)
    if snapshot_sha256(stored) != digest:
        raise SnapshotIntegrityError("Stored snapshot does not match its checksum.")
    return digest


def load_snapshot(digest: str, *, database_url: str) -> dict:
    import psycopg2

    connection = psycopg2.connect(database_url, application_name="sec-pilot-price-snapshot-read", connect_timeout=10)
    try:
        connection.set_session(readonly=True)
        with connection.cursor() as cursor:
            cursor.execute(SELECT_SNAPSHOT_SQL, (digest,))
            row = cursor.fetchone()
        connection.rollback()
    finally:
        connection.close()
    if row is None:
        raise SnapshotIntegrityError(f"No stored price snapshot {digest}.")
    snapshot = json.loads(row[0])
    if snapshot_sha256(snapshot) != digest:
        raise SnapshotIntegrityError("Stored snapshot does not match its checksum.")
    return snapshot


# --------------------------------------------------------------------------
# Private full pilot records (daily curves are Yahoo-derived: never public)
# --------------------------------------------------------------------------

PRIVATE_RECORD_TABLE = "backtest_pilot_private_records"
PRIVATE_RECORD_FIELDS = ("curves_base_cost",)

CREATE_PRIVATE_RECORD_TABLE_SQL = f"""
CREATE TABLE IF NOT EXISTS {PRIVATE_RECORD_TABLE} (
    record_sha256 TEXT PRIMARY KEY,
    snapshot_sha256 TEXT NOT NULL REFERENCES {SNAPSHOT_TABLE} (snapshot_sha256),
    github_run_id TEXT,
    payload TEXT NOT NULL,
    stored_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""
INSERT_PRIVATE_RECORD_SQL = f"""
INSERT INTO {PRIVATE_RECORD_TABLE} (record_sha256, snapshot_sha256, github_run_id, payload)
VALUES (%s, %s, %s, %s)
ON CONFLICT (record_sha256) DO NOTHING;
"""
SELECT_PRIVATE_RECORD_SQL = f"SELECT payload FROM {PRIVATE_RECORD_TABLE} WHERE record_sha256 = %s;"


def public_record(full_record: dict, *, private_record_sha256: str) -> dict:
    """The full record minus Yahoo-derived daily series, plus the checksum of
    the private full record that holds them."""

    reduced = {key: value for key, value in full_record.items() if key not in PRIVATE_RECORD_FIELDS}
    reduced["private_full_record"] = {
        "sha256": private_record_sha256,
        "storage": f"private postgres table {PRIVATE_RECORD_TABLE} (not redistributed)",
        "withheld_fields": list(PRIVATE_RECORD_FIELDS),
    }
    return reduced


def store_private_record(
    full_record: dict, *, snapshot_digest: str, database_url: str, github_run_id: Optional[str]
) -> str:
    """Append the full record privately, then read it back and re-verify."""

    import psycopg2

    digest = snapshot_sha256(full_record)
    payload = canonical_bytes(full_record).decode("utf-8")
    connection = psycopg2.connect(database_url, application_name="sec-pilot-private-record", connect_timeout=10)
    try:
        with connection:
            with connection.cursor() as cursor:
                cursor.execute(CREATE_PRIVATE_RECORD_TABLE_SQL)
                cursor.execute(INSERT_PRIVATE_RECORD_SQL, (digest, snapshot_digest, github_run_id, payload))
            with connection.cursor() as cursor:
                cursor.execute(SELECT_PRIVATE_RECORD_SQL, (digest,))
                row = cursor.fetchone()
    finally:
        connection.close()
    if row is None or snapshot_sha256(json.loads(row[0])) != digest:
        raise SnapshotIntegrityError("Stored private record does not match its checksum.")
    return digest
