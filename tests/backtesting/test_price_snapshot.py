import copy
import datetime as dt
import json

import pytest

from src.backtesting import sec_pilot
from src.backtesting.price_snapshot import (
    RecordingPriceProvider,
    SnapshotIntegrityError,
    SnapshotPriceProvider,
    canonical_bytes,
    snapshot_sha256,
)
from src.backtesting.sec_pilot import estimate_beta, rsi_at, trend_passes_at
from tests.backtesting.test_sec_pilot import DECISION, _prices

CAPTURED_AT = dt.datetime(2026, 9, 23, tzinfo=dt.timezone.utc)


def _recorded():
    recording = RecordingPriceProvider(_prices())
    signals = (
        estimate_beta(recording, "AAA", "SPY", DECISION),
        rsi_at(recording, "AAA", DECISION),
        trend_passes_at(recording, "AAA", DECISION),
        recording.raw_close("AAA", DECISION),
    )
    return signals, recording.snapshot(source="test", captured_at=CAPTURED_AT)


def test_offline_replay_reproduces_every_signal_exactly():
    signals, snapshot = _recorded()
    replay = SnapshotPriceProvider(json.loads(canonical_bytes(snapshot)))

    assert (
        estimate_beta(replay, "AAA", "SPY", DECISION),
        rsi_at(replay, "AAA", DECISION),
        trend_passes_at(replay, "AAA", DECISION),
        replay.raw_close("AAA", DECISION),
    ) == signals


def test_checksum_is_stable_across_serialization_and_detects_tampering():
    _, snapshot = _recorded()
    digest = snapshot_sha256(snapshot)

    assert snapshot_sha256(json.loads(canonical_bytes(snapshot))) == digest
    tampered = copy.deepcopy(snapshot)
    first_day = next(iter(tampered["symbols"]["AAA"]["adjusted_open_close"]))
    tampered["symbols"]["AAA"]["adjusted_open_close"][first_day][1] += 1e-9
    assert snapshot_sha256(tampered) != digest


def test_replay_refuses_reads_the_snapshot_does_not_cover():
    _, snapshot = _recorded()
    replay = SnapshotPriceProvider(snapshot)

    with pytest.raises(SnapshotIntegrityError, match="does not cover"):
        replay.adjusted("AAA", DECISION, DECISION + dt.timedelta(days=30))
    with pytest.raises(SnapshotIntegrityError, match="raw close"):
        replay.raw_close("AAA", DECISION - dt.timedelta(days=1))
    with pytest.raises(SnapshotIntegrityError, match="does not cover"):
        replay.adjusted("ZZZ", DECISION, DECISION)


def test_pinned_run_refuses_a_stored_copy_that_fails_its_checksum(monkeypatch):
    monkeypatch.setattr(sec_pilot, "run_pilot", lambda config, **kwargs: {"label": "pipeline_validation"})
    vault = {}

    def store(snapshot):
        digest = snapshot_sha256(snapshot)
        corrupted = copy.deepcopy(snapshot)
        corrupted["source"] = "altered"
        vault[digest] = corrupted
        return digest

    with pytest.raises(SnapshotIntegrityError, match="checksum"):
        sec_pilot.run_pinned_pilot(
            sec_pilot.PilotConfig(),
            repository=None,
            live_prices=_prices(),
            manifest_lookup=lambda ticker: None,
            data_vintage_cutoff=CAPTURED_AT,
            store=store,
            load=vault.__getitem__,
            github_run_id="1",
        )


def test_pinned_run_refuses_when_the_replay_differs(monkeypatch):
    outputs = iter([{"value": 1.0}, {"value": 1.0000001}])
    monkeypatch.setattr(sec_pilot, "run_pilot", lambda config, **kwargs: next(outputs))
    vault = {}

    def store(snapshot):
        vault[snapshot_sha256(snapshot)] = snapshot
        return snapshot_sha256(snapshot)

    with pytest.raises(SnapshotIntegrityError, match="did not reproduce"):
        sec_pilot.run_pinned_pilot(
            sec_pilot.PilotConfig(),
            repository=None,
            live_prices=_prices(),
            manifest_lookup=lambda ticker: None,
            data_vintage_cutoff=CAPTURED_AT,
            store=store,
            load=vault.__getitem__,
            github_run_id="1",
        )


def test_pinned_record_carries_checksum_and_audit_trail_but_no_prices(monkeypatch):
    def fake_run(config, *, prices, **kwargs):
        prices.adjusted("AAA", DECISION, DECISION)
        prices.raw_close("AAA", DECISION)
        return {"label": "pipeline_validation"}

    monkeypatch.setattr(sec_pilot, "run_pilot", fake_run)
    vault = {}

    def store(snapshot):
        vault[snapshot_sha256(snapshot)] = json.loads(canonical_bytes(snapshot))
        return snapshot_sha256(snapshot)

    report = sec_pilot.run_pinned_pilot(
        sec_pilot.PilotConfig(),
        repository=None,
        live_prices=_prices(),
        manifest_lookup=lambda ticker: None,
        data_vintage_cutoff=CAPTURED_AT,
        store=store,
        load=vault.__getitem__,
        github_run_id="42",
        prior_run_ids=["35809011675"],
    )

    assert report["price_snapshot"]["sha256"] in vault
    assert report["price_snapshot"]["offline_replay_identical"] is True
    assert report["price_snapshot"]["captured_in_run"] == "42"
    assert report["audit_trail"] == {"run_id": "42", "prior_provisional_run_ids": ["35809011675"]}
    serialized = json.dumps(report)
    assert "adjusted_open_close" not in serialized
    assert "raw_close" not in serialized


def _price_reading_run(config, *, prices, data_vintage_cutoff, **kwargs):
    frame = prices.adjusted("AAA", DECISION - dt.timedelta(days=30), DECISION)
    return {
        "label": "pipeline_validation",
        "data_vintage_cutoff": data_vintage_cutoff.isoformat(),
        "signal": float(frame["Close"].sum()),
        "curves_base_cost": {"spy_buy_and_hold": [["2024-09-04", float(frame["Close"].iloc[-1])]]},
    }


def test_archive_replay_rebuilds_the_pinned_runs_full_record_exactly(monkeypatch):
    from src.backtesting.price_snapshot import public_record

    monkeypatch.setattr(sec_pilot, "run_pilot", _price_reading_run)
    vault = {}

    def store(snapshot):
        vault[snapshot_sha256(snapshot)] = json.loads(canonical_bytes(snapshot))
        return snapshot_sha256(snapshot)

    pinned = sec_pilot.run_pinned_pilot(
        sec_pilot.PilotConfig(),
        repository=None,
        live_prices=_prices(),
        manifest_lookup=lambda ticker: None,
        data_vintage_cutoff=CAPTURED_AT,
        store=store,
        load=vault.__getitem__,
        github_run_id="35809776344",
        prior_run_ids=["35809011675"],
    )
    digest = pinned["price_snapshot"]["sha256"]
    archived = sec_pilot.replay_pinned_run(
        sec_pilot.PilotConfig(),
        repository=None,
        snapshot=vault[digest],
        digest=digest,
        manifest_lookup=lambda ticker: None,
        data_vintage_cutoff=CAPTURED_AT,
        original_run_id="35809776344",
        prior_run_ids=["35809011675"],
    )

    assert snapshot_sha256(archived) == snapshot_sha256(json.loads(json.dumps(pinned)))
    reduced = public_record(archived, private_record_sha256=snapshot_sha256(archived))
    assert "curves_base_cost" not in reduced
    assert reduced["signal"] == pinned["signal"]
    assert reduced["private_full_record"]["sha256"] == snapshot_sha256(archived)
    assert reduced["private_full_record"]["withheld_fields"] == ["curves_base_cost"]


def test_archive_replay_refuses_a_snapshot_that_fails_its_checksum():
    _, snapshot = _recorded()

    with pytest.raises(SnapshotIntegrityError, match="checksum"):
        sec_pilot.replay_pinned_run(
            sec_pilot.PilotConfig(),
            repository=None,
            snapshot=snapshot,
            digest="0" * 64,
            manifest_lookup=lambda ticker: None,
            data_vintage_cutoff=CAPTURED_AT,
            original_run_id="1",
        )
