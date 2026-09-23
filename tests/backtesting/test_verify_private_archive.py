import hashlib
import json
import subprocess
import sys
from pathlib import Path

from src.backtesting.verify_private_archive import verify_archive

RUN = "35809776344"


def _canonical(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _rows(*, record_run=RUN, embedded_snapshot=None, tamper=False):
    snapshot = {"format_version": "pilot-price-snapshot-v1", "symbols": {"SPY": {"adjusted_open_close": {"2024-09-04": [536.14, 536.88]}}}}
    snapshot_payload = _canonical(snapshot)
    snapshot_sha = hashlib.sha256(snapshot_payload.encode()).hexdigest()
    record = {
        "label": "pipeline_validation",
        "price_snapshot": {"sha256": embedded_snapshot or snapshot_sha, "captured_in_run": RUN},
        "audit_trail": {"run_id": RUN},
        "curves_base_cost": {"spy_buy_and_hold": [["2024-09-04", 123456.78]]},
    }
    record_payload = _canonical(record)
    record_sha = hashlib.sha256(record_payload.encode()).hexdigest()
    if tamper:
        record_payload = record_payload.replace("123456.78", "123456.79")
    snapshot_row = (snapshot_sha, "pilot-price-snapshot-v1", "yahoo", "2026-09-23", RUN, "sec-backtest-pilot-v1", "2026-09-23", snapshot_payload)
    record_row = (record_sha, snapshot_sha, record_run, "2026-09-23", record_payload)
    return snapshot_row, record_row, snapshot_sha, record_sha


def test_matching_rows_verify_and_output_carries_no_prices_or_curve_values():
    snapshot_row, record_row, snapshot_sha, record_sha = _rows()

    result = verify_archive(snapshot_row, record_row, snapshot_sha=snapshot_sha, record_sha=record_sha, run_id=RUN)

    assert result["status"] == "verified", result["problems"]
    assert result["record"]["curve_points"] == {"spy_buy_and_hold": 1}
    serialized = json.dumps(result)
    for secret in ("536.14", "536.88", "123456.78", "adjusted_open_close"):
        assert secret not in serialized


def test_tampered_record_payload_fails():
    snapshot_row, record_row, snapshot_sha, record_sha = _rows(tamper=True)

    result = verify_archive(snapshot_row, record_row, snapshot_sha=snapshot_sha, record_sha=record_sha, run_id=RUN)

    assert result["status"] == "failed"
    assert "Full record checksum mismatch." in result["problems"]


def test_wrong_snapshot_reference_or_run_fails():
    snapshot_row, record_row, snapshot_sha, record_sha = _rows(embedded_snapshot="f" * 64, record_run="1")

    result = verify_archive(snapshot_row, record_row, snapshot_sha=snapshot_sha, record_sha=record_sha, run_id=RUN)

    assert "Full record does not reference the expected snapshot." in result["problems"]
    assert "Full record does not reference the expected pinned run." in result["problems"]


def test_missing_rows_fail():
    result = verify_archive(None, None, snapshot_sha="a" * 64, record_sha="b" * 64, run_id=RUN)

    assert result["status"] == "failed"
    assert len(result["problems"]) == 2


def test_verifier_is_standalone_and_imports_no_writer_or_pandas():
    code = (
        "import sys, src.backtesting.verify_private_archive; "
        "heavy = [m for m in ('pandas', 'src.backtesting.price_snapshot', 'src.backtesting.sec_pilot') if m in sys.modules]; "
        "assert not heavy, heavy"
    )
    completed = subprocess.run([sys.executable, "-c", code], cwd=Path(__file__).resolve().parents[2], capture_output=True, text=True)

    assert completed.returncode == 0, completed.stderr
