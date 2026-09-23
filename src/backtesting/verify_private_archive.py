"""
Independent, read-only check of the SEC pilot's private archive.

Fetches the stored price snapshot and the stored full pilot record by hash,
recomputes both checksums from the stored bytes, and confirms the full
record references the expected snapshot and pinned run. It opens a
read-only session, never writes, and prints only hashes and metadata --
never prices or daily curves (the repository and its Actions logs are
public). Deliberately standalone: it imports nothing from the code that
wrote the rows, and needs only psycopg2.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from typing import List, Optional, Sequence

SELECT_SNAPSHOT_SQL = """
SELECT snapshot_sha256, format_version, source, captured_at, github_run_id,
       pilot_policy_version, stored_at, payload
FROM backtest_price_snapshots WHERE snapshot_sha256 = %s;
"""
SELECT_RECORD_SQL = """
SELECT record_sha256, snapshot_sha256, github_run_id, stored_at, payload
FROM backtest_pilot_private_records WHERE record_sha256 = %s;
"""
_SHA = re.compile(r"^[0-9a-f]{64}$")


def _canonical_sha(payload: str) -> str:
    parsed = json.loads(payload)
    canonical = json.dumps(parsed, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def verify_archive(snapshot_row, record_row, *, snapshot_sha: str, record_sha: str, run_id: str) -> dict:
    problems: List[str] = []
    result = {"expected": {"snapshot_sha256": snapshot_sha, "record_sha256": record_sha, "run_id": run_id}}
    if snapshot_row is None:
        problems.append("Price snapshot row not found.")
    if record_row is None:
        problems.append("Private full record row not found.")
    if snapshot_row is not None:
        key, fmt, source, captured, snap_run, policy, stored, payload = snapshot_row
        raw = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        canonical = _canonical_sha(payload)
        result["snapshot"] = {
            "key": key, "format_version": fmt, "source": source, "captured_at": str(captured),
            "github_run_id": snap_run, "pilot_policy_version": policy, "stored_at": str(stored),
            "payload_bytes": len(payload.encode("utf-8")),
            "recomputed_sha256_raw": raw, "recomputed_sha256_canonical": canonical,
            "symbols": sorted(json.loads(payload).get("symbols", {})),
        }
        if not (key == raw == canonical == snapshot_sha):
            problems.append("Snapshot checksum mismatch.")
        if snap_run != run_id:
            problems.append("Snapshot was not captured in the expected run.")
    if record_row is not None:
        key, ref_snapshot, rec_run, stored, payload = record_row
        raw = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        canonical = _canonical_sha(payload)
        record = json.loads(payload)
        embedded_snapshot = (record.get("price_snapshot") or {}).get("sha256")
        embedded_run = (record.get("audit_trail") or {}).get("run_id")
        captured_in = (record.get("price_snapshot") or {}).get("captured_in_run")
        result["record"] = {
            "key": key, "snapshot_sha256_column": ref_snapshot, "github_run_id": rec_run, "stored_at": str(stored),
            "payload_bytes": len(payload.encode("utf-8")),
            "recomputed_sha256_raw": raw, "recomputed_sha256_canonical": canonical,
            "embedded_price_snapshot_sha256": embedded_snapshot, "embedded_run_id": embedded_run,
            "embedded_captured_in_run": captured_in, "label": record.get("label"),
            "has_curves": "curves_base_cost" in record,
            "curve_names": sorted(record.get("curves_base_cost", {})),
            "curve_points": {name: len(points) for name, points in sorted(record.get("curves_base_cost", {}).items())},
        }
        if not (key == raw == canonical == record_sha):
            problems.append("Full record checksum mismatch.")
        if ref_snapshot != snapshot_sha or embedded_snapshot != snapshot_sha:
            problems.append("Full record does not reference the expected snapshot.")
        if rec_run != run_id or embedded_run != run_id or captured_in != run_id:
            problems.append("Full record does not reference the expected pinned run.")
        if not result["record"]["has_curves"]:
            problems.append("Full record is missing its private curves.")
    result["problems"] = problems
    result["status"] = "verified" if not problems else "failed"
    return result


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only check of the SEC pilot private archive.")
    parser.add_argument("--snapshot-sha256", required=True)
    parser.add_argument("--record-sha256", required=True)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args(argv)
    if not (_SHA.match(args.snapshot_sha256) and _SHA.match(args.record_sha256) and args.run_id.isdigit()):
        print(json.dumps({"status": "failed", "message": "Invalid hash or run ID."}))
        return 1
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        print(json.dumps({"status": "failed", "message": "DATABASE_URL must be set."}))
        return 1
    import psycopg2

    connection = psycopg2.connect(database_url, application_name="sec-pilot-archive-verify", connect_timeout=10)
    try:
        connection.set_session(readonly=True, autocommit=False)
        with connection.cursor() as cursor:
            cursor.execute(SELECT_SNAPSHOT_SQL, (args.snapshot_sha256,))
            snapshot_row = cursor.fetchone()
            cursor.execute(SELECT_RECORD_SQL, (args.record_sha256,))
            record_row = cursor.fetchone()
        connection.rollback()
    finally:
        connection.close()
    result = verify_archive(
        snapshot_row, record_row,
        snapshot_sha=args.snapshot_sha256, record_sha=args.record_sha256, run_id=args.run_id,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["status"] == "verified" else 1


if __name__ == "__main__":
    raise SystemExit(main())
