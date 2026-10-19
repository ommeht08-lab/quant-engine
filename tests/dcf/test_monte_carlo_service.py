"""Service flow: allowlist, one snapshot per request, caching, locks and ownership."""

import json
import threading
import time
from datetime import datetime, timezone

import pytest

from src.dcf_model import monte_carlo_service as service_module
from src.dcf_model.monte_carlo import canonical_payload, results_identical
from src.dcf_model.monte_carlo_config import MonteCarloConfig, canonical_json
from src.dcf_model.monte_carlo_service import (
    MonteCarloService,
    ServiceError,
    result_cache_key,
    result_lock_key,
    snapshot_key,
    snapshot_lock_key,
)
from src.dcf_model.monte_carlo_store import InMemoryStore, StoreUnavailable, UpstashStore
from tests.dcf.monte_carlo_fixtures import make_financial_data
from tests.dcf.test_monte_carlo_snapshot import make_valuation_input
from tests.dcf.test_monte_carlo_store import FakeRedis

NOW = datetime(2026, 10, 20, 15, 30, tzinfo=timezone.utc)
RF = 0.04


class Provider:
    """Counts provider fetches; optionally slow, failing, or hooked."""

    def __init__(self, data=None, delay=0.0, error=None, hook=None):
        self.calls = 0
        self.data = data
        self.delay = delay
        self.error = error
        self.hook = hook
        self._lock = threading.Lock()

    def __call__(self, ticker):
        with self._lock:
            self.calls += 1
        if self.hook:
            self.hook()
        if self.delay:
            time.sleep(self.delay)
        if self.error:
            raise self.error
        return make_valuation_input(self.data or make_financial_data(ticker=ticker), ticker), RF


@pytest.fixture(autouse=True)
def fast_polling(monkeypatch):
    monkeypatch.setattr(service_module, "POLL_INTERVAL_SECONDS", 0.005)


def make_service(provider=None, store=None, **kwargs):
    store = store if store is not None else InMemoryStore()
    provider = provider or Provider()
    kwargs.setdefault("allowlist", ("AAPL", "KO", "JNJ"))
    kwargs.setdefault("clock", lambda: NOW)
    service = MonteCarloService(provider, store_factory=lambda: store, **kwargs)
    return service, provider, store


class TestValidation:
    def test_ticker_must_be_on_the_allowlist(self):
        service, provider, _ = make_service()
        for ticker in ("MSFT", "", "../etc"):
            with pytest.raises(ServiceError) as raised:
                service.get_result(ticker)
            assert raised.value.status_code == 404 and raised.value.code == "ticker_not_supported"
        assert provider.calls == 0  # nothing is fetched for a rejected ticker

    def test_lowercase_ticker_is_normalized(self):
        service, _, _ = make_service()
        assert service.get_result(" aapl ")["identity"]["ticker"] == "AAPL"

    @pytest.mark.parametrize("seed", [-1, 2**63, True, 1.5])
    def test_invalid_seed_is_rejected(self, seed):
        service, provider, _ = make_service()
        with pytest.raises(ServiceError) as raised:
            service.get_result("AAPL", seed)
        assert (raised.value.status_code, raised.value.code) == (422, "invalid_seed")
        assert provider.calls == 0

    def test_misconfigured_allowlist_is_a_503(self, monkeypatch):
        monkeypatch.setenv("MONTE_CARLO_TICKERS", "A,B,C,D,E,F")
        service, _, _ = make_service(allowlist=None)
        with pytest.raises(ServiceError) as raised:
            service.get_result("AAPL")
        assert raised.value.status_code == 503 and raised.value.code == "monte_carlo_misconfigured"

    def test_describe_exposes_the_allowlist_and_configuration(self):
        service, _, _ = make_service()
        description = service.describe()
        assert description["tickers"] == ["AAPL", "KO", "JNJ"]
        assert description["config"]["target_valid"] == 10_000
        assert len(description["config_hash"]) == 64 and description["disclosures"]


class TestSnapshotAndResultCaching:
    def test_first_request_fetches_once_then_everything_is_served_from_cache(self):
        service, provider, _ = make_service()
        first = service.get_result("AAPL")
        second = service.get_result("AAPL")
        assert provider.calls == 1
        assert (first["meta"]["cache"], first["meta"]["snapshot_cache"]) == ("miss", "miss")
        assert (second["meta"]["cache"], second["meta"]["snapshot_cache"]) == ("hit", "hit")
        assert results_identical(first, second) and first["result_digest"] == second["result_digest"]

    def test_a_new_seed_reuses_the_snapshot_but_computes_a_new_result(self):
        service, provider, _ = make_service()
        a = service.get_result("AAPL", 1)
        b = service.get_result("AAPL", 2)
        assert provider.calls == 1 and b["meta"]["snapshot_cache"] == "hit" and b["meta"]["cache"] == "miss"
        assert a["identity"]["snapshot_id"] == b["identity"]["snapshot_id"]
        assert a["result_digest"] != b["result_digest"]

    def test_provider_is_never_called_per_simulation_draw(self):
        service, provider, _ = make_service()
        service.get_result("AAPL")
        assert provider.calls == 1  # 40,000 valuations, one fetch

    def test_cache_identity_covers_ticker_snapshot_seed_config_and_model_version(self, monkeypatch):
        base = result_cache_key("AAPL", "snap", 1, MonteCarloConfig())
        assert result_cache_key("AAPL", "snap", 1, MonteCarloConfig()) == base
        assert result_cache_key("KO", "snap", 1, MonteCarloConfig()) != base
        assert result_cache_key("AAPL", "snap2", 1, MonteCarloConfig()) != base
        assert result_cache_key("AAPL", "snap", 2, MonteCarloConfig()) != base
        assert result_cache_key("AAPL", "snap", 1, MonteCarloConfig(wacc_half_width=0.02)) != base
        monkeypatch.setattr(service_module, "MONTE_CARLO_MODEL_VERSION", "mc-dcf-v2")
        assert result_cache_key("AAPL", "snap", 1, MonteCarloConfig()) != base

    def test_cache_identity_covers_the_stored_result_format(self, monkeypatch):
        base = result_cache_key("AAPL", "snap", 1, MonteCarloConfig())
        monkeypatch.setattr(service_module, "RESULT_FORMAT_VERSION", service_module.RESULT_FORMAT_VERSION + 1)
        assert result_cache_key("AAPL", "snap", 1, MonteCarloConfig()) != base

    def test_an_entry_written_in_an_older_result_format_is_never_served(self, monkeypatch):
        store = InMemoryStore()
        service, provider, _ = make_service(store=store)
        snapshot, _ = service._acquire_snapshot(store, "AAPL")
        # An older deployment cached a result (no provenance block, old disclosure copy) under ITS key.
        monkeypatch.setattr(service_module, "RESULT_FORMAT_VERSION", 1)
        old_key = result_cache_key("AAPL", snapshot["snapshot_id"], 20261101, MonteCarloConfig())
        legacy = service._compute_entry(snapshot, 20261101)
        del legacy["result"]["baseline_provenance"]
        legacy["result"]["disclosures"] = [{"id": "legacy", "text": "old copy"}]
        store.set(old_key, json.dumps(legacy), 100)
        monkeypatch.undo()
        result = service.get_result("AAPL")
        assert result["meta"]["cache"] == "miss"
        assert "baseline_provenance" in result and result["disclosures"][0]["id"] != "legacy"
        assert store.get(old_key) is not None  # the old entry is simply left to expire

    def test_replay_export_carries_provenance_and_is_intact(self):
        service, _, _ = make_service()
        run = service.get_result("AAPL")
        bundle = service.get_replay_bundle(run["identity"]["snapshot_id"])
        assert bundle["baseline_provenance"]["provenance_digest"] == run["baseline_provenance"]["provenance_digest"]
        from src.dcf_model.monte_carlo_snapshot import replay_bundle

        outcome = replay_bundle(json.loads(json.dumps(bundle)))
        assert outcome["matches_expected"] is True and outcome["bundle_integrity_ok"] is True
        assert outcome["provenance_matches_expected"] is True

    @pytest.mark.parametrize("snapshot_id", ["abc", "../x", "A" * 64, "g" * 64, ""])
    def test_malformed_snapshot_ids_are_unknown_without_touching_the_store(self, snapshot_id):
        service, provider, store = make_service()
        with pytest.raises(ServiceError) as raised:
            service.get_replay_bundle(snapshot_id)
        assert (raised.value.status_code, raised.value.code) == (404, "snapshot_not_found")
        assert provider.calls == 0 and store._data == {}

    def test_a_changed_configuration_cannot_reuse_a_stale_result(self):
        store = InMemoryStore()
        first, _, _ = make_service(store=store)
        second, _, _ = make_service(store=store, config=MonteCarloConfig(wacc_half_width=0.005))
        a = first.get_result("AAPL")
        b = second.get_result("AAPL")
        assert b["meta"]["cache"] == "miss" and a["config_hash"] != b["config_hash"]

    def test_expired_pointer_triggers_a_fresh_snapshot_with_a_new_identity(self):
        store_clock, now = [1000.0], [NOW]
        store = InMemoryStore(clock=lambda: store_clock[0])
        service, provider, _ = make_service(store=store, clock=lambda: now[0])
        first = service.get_result("AAPL")
        store_clock[0] += service_module.POINTER_TTL_SECONDS + 1
        now[0] = NOW.replace(day=21)
        second = service.get_result("AAPL")
        assert provider.calls == 2
        assert first["identity"]["snapshot_id"] != second["identity"]["snapshot_id"]
        assert second["identity"]["snapshot_captured_at"] == now[0].isoformat()

    def test_meta_reports_timings_environment_and_when_it_was_computed(self):
        meta = make_service()[0].get_result("AAPL")["meta"]
        assert meta["computed_at"] == NOW.isoformat() and set(meta["timings_ms"]) >= {"snapshot", "result", "total"}
        assert meta["environment"]["model_version"]

    def test_corrupt_snapshot_entries_are_treated_as_misses(self):
        service, provider, store = make_service()
        first = service.get_result("AAPL")
        store.set(snapshot_key(first["identity"]["snapshot_id"]), "{not json", 100)
        service.get_result("AAPL", 3)
        assert provider.calls == 2

    def test_whole_run_refusals_are_cached_and_not_recomputed(self, monkeypatch):
        broken = make_financial_data()
        broken["income_statement"] = broken["income_statement"].drop(index="Total Revenue")
        service, _, _ = make_service(provider=Provider(data=broken))
        computed = []
        real = service_module.compute_from_snapshot
        monkeypatch.setattr(service_module, "compute_from_snapshot",
                            lambda *a, **k: computed.append(1) or real(*a, **k))
        for _ in range(2):
            with pytest.raises(ServiceError) as raised:
                service.get_result("AAPL")
            assert raised.value.status_code == 422 and raised.value.code == "input_incomplete"
        assert len(computed) == 1


class TestProviderFailures:
    def test_provider_refusal_propagates_and_releases_the_snapshot_lock(self):
        provider = Provider(error=ServiceError(422, "input_incomplete", "Yahoo did not provide statements."))
        service, _, store = make_service(provider=provider)
        with pytest.raises(ServiceError) as raised:
            service.get_result("AAPL")
        assert raised.value.code == "input_incomplete"
        assert store.get(snapshot_lock_key("AAPL")) is None  # not stuck behind a dead lock
        provider.error = None
        assert service.get_result("AAPL")["status"] == "complete"

    def test_unexpected_provider_error_still_releases_the_lock(self):
        provider = Provider(error=RuntimeError("yahoo exploded"))
        service, _, store = make_service(provider=provider)
        with pytest.raises(RuntimeError):
            service.get_result("AAPL")
        assert store.get(snapshot_lock_key("AAPL")) is None

    def test_simulation_failure_releases_the_result_lock(self, monkeypatch):
        service, _, store = make_service()

        def boom(*args, **kwargs):
            raise RuntimeError("kernel exploded")

        monkeypatch.setattr(service_module, "compute_from_snapshot", boom)
        with pytest.raises(RuntimeError):
            service.get_result("AAPL")
        assert not any(":lock:" in key for key in store._data)


class TestConcurrency:
    def test_concurrent_cold_requests_fetch_once_and_compute_once(self, monkeypatch):
        provider = Provider(delay=0.15)
        service, _, store = make_service(provider=provider, snapshot_wait_seconds=10, result_wait_seconds=10)
        computed, counter_lock = [], threading.Lock()
        real = service_module.compute_from_snapshot

        def counting(*args, **kwargs):
            with counter_lock:
                computed.append(1)
            time.sleep(0.1)
            return real(*args, **kwargs)

        monkeypatch.setattr(service_module, "compute_from_snapshot", counting)
        results, errors = [], []

        def worker():
            try:
                results.append(service.get_result("AAPL"))
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
        assert not errors and len(results) == 8
        assert provider.calls == 1 and len(computed) == 1
        assert len({r["result_digest"] for r in results}) == 1
        assert sorted(r["meta"]["cache"] for r in results).count("miss") == 1
        assert not any(":lock:" in key for key in store._data)

    def test_waiters_for_a_slow_snapshot_time_out_with_a_retryable_409(self):
        service, provider, store = make_service()
        store.set(snapshot_lock_key("AAPL"), "someone-else", 100)
        service._snapshot_wait = 0.05
        with pytest.raises(ServiceError) as raised:
            service.get_result("AAPL")
        assert (raised.value.status_code, raised.value.code) == (409, "snapshot_in_progress")
        assert raised.value.retry_after == 3
        assert provider.calls == 0
        assert store.get(snapshot_lock_key("AAPL")) == "someone-else"  # we never touched their lock

    def test_waiters_for_a_running_simulation_time_out_with_a_retryable_409(self):
        service, provider, store = make_service()
        snapshot, _ = service._acquire_snapshot(store, "AAPL")
        key = result_cache_key("AAPL", snapshot["snapshot_id"], 20261101, MonteCarloConfig())
        store.set(result_lock_key(key), "someone-else", 100)
        service._result_wait = 0.05
        with pytest.raises(ServiceError) as raised:
            service.get_result("AAPL")
        assert (raised.value.status_code, raised.value.code) == (409, "computation_in_progress")
        assert store.get(result_lock_key(key)) == "someone-else"

    def test_a_waiter_returns_the_holders_result_when_it_lands(self):
        service, _, store = make_service(snapshot_wait_seconds=5, result_wait_seconds=5)
        snapshot, _ = service._acquire_snapshot(store, "AAPL")
        key = result_cache_key("AAPL", snapshot["snapshot_id"], 20261101, MonteCarloConfig())
        store.set(result_lock_key(key), "holder", 100)

        def holder_finishes():
            time.sleep(0.1)
            entry = service._compute_entry(snapshot, 20261101)
            store.set(key, json.dumps(entry), 100)
            store.release_if_owner(result_lock_key(key), "holder")

        thread = threading.Thread(target=holder_finishes)
        thread.start()
        result = service.get_result("AAPL")
        thread.join()
        assert result["meta"]["cache"] == "hit" and result["status"] == "complete"

    def test_a_crashed_holders_expired_lock_is_taken_over(self):
        clock = [0.0]
        store = InMemoryStore(clock=lambda: clock[0])
        service, provider, _ = make_service(store=store, monotonic=lambda: clock[0],
                                            sleep=lambda s: clock.__setitem__(0, clock[0] + 10),
                                            snapshot_wait_seconds=60)
        store.set(snapshot_lock_key("AAPL"), "crashed-holder", 45)
        result = service.get_result("AAPL")  # waits (virtually) past the 45 s TTL, then proceeds
        assert result["status"] == "complete" and provider.calls == 1

    def test_snapshot_lock_release_is_owner_checked_when_our_lock_expired_midway(self):
        store = InMemoryStore()

        def lock_expires_and_is_retaken():
            store._data[snapshot_lock_key("AAPL")] = ("new-owner", None)  # as if expired, then re-acquired

        service, provider, _ = make_service(store=store, provider=Provider(hook=lock_expires_and_is_retaken))
        service.get_result("AAPL")
        assert store.get(snapshot_lock_key("AAPL")) == "new-owner"  # our release did not delete theirs

    def test_result_lock_release_is_owner_checked_when_our_lock_expired_midway(self, monkeypatch):
        service, _, store = make_service()
        real = service_module.compute_from_snapshot
        taken = {}

        def compute_while_lock_is_stolen(snapshot, seed, config):
            key = result_lock_key(result_cache_key(snapshot["ticker"], snapshot["snapshot_id"], seed, config))
            store._data[key] = ("new-owner", None)
            taken["key"] = key
            return real(snapshot, seed, config)

        monkeypatch.setattr(service_module, "compute_from_snapshot", compute_while_lock_is_stolen)
        service.get_result("AAPL")
        assert store.get(taken["key"]) == "new-owner"


class TestStoreFailures:
    def test_a_failed_lock_release_does_not_fail_a_finished_request(self):
        class FlakyRelease(InMemoryStore):
            def release_if_owner(self, key, token):
                raise StoreUnavailable("redis blip")

        service, provider, store = make_service(store=FlakyRelease())
        result = service.get_result("AAPL")
        assert result["status"] == "complete" and provider.calls == 1
        assert any(":lock:" in key for key in store._data)  # left to expire by TTL, not deleted

    def test_store_that_cannot_be_created_is_a_503(self):
        def unavailable():
            raise StoreUnavailable("no redis")

        service = MonteCarloService(Provider(), store_factory=unavailable, allowlist=("AAPL",))
        with pytest.raises(ServiceError) as raised:
            service.get_result("AAPL")
        assert (raised.value.status_code, raised.value.code) == (503, "cache_unavailable")

    def test_store_failing_mid_request_is_a_503(self):
        service, provider, _ = make_service(store=UpstashStore(FakeRedis(fail=True)))
        with pytest.raises(ServiceError) as raised:
            service.get_result("AAPL")
        assert raised.value.status_code == 503 and provider.calls == 0

    def test_replay_bundle_store_failure_is_a_503(self):
        service, _, _ = make_service(store=UpstashStore(FakeRedis(fail=True)))
        with pytest.raises(ServiceError) as raised:
            service.get_replay_bundle("a" * 64)
        assert raised.value.status_code == 503


class TestReplayBundleExport:
    def test_bundle_includes_snapshot_seed_config_and_the_expected_digest(self):
        service, _, _ = make_service()
        result = service.get_result("AAPL", 77)
        bundle = service.get_replay_bundle(result["identity"]["snapshot_id"], 77)
        assert bundle["snapshot"]["snapshot_id"] == result["identity"]["snapshot_id"]
        assert bundle["replay"]["seed"] == 77
        assert bundle["replay"]["config"] == result["config"]
        assert bundle["replay"]["expected_result_digest"] == result["result_digest"]

    def test_digest_is_null_before_any_result_exists_for_that_seed(self):
        service, _, _ = make_service()
        result = service.get_result("AAPL", 1)
        bundle = service.get_replay_bundle(result["identity"]["snapshot_id"], 2)
        assert bundle["replay"]["expected_result_digest"] is None

    def test_unknown_snapshot_is_a_404(self):
        service, _, _ = make_service()
        with pytest.raises(ServiceError) as raised:
            service.get_replay_bundle("deadbeef")
        assert raised.value.status_code == 404 and raised.value.code == "snapshot_not_found"

    def test_exported_bundle_replays_to_the_identical_payload_offline(self):
        from src.dcf_model.monte_carlo_snapshot import replay_bundle

        service, _, _ = make_service()
        live = service.get_result("AAPL", 5)
        bundle = json.loads(json.dumps(service.get_replay_bundle(live["identity"]["snapshot_id"], 5)))
        outcome = replay_bundle(bundle)
        assert outcome["matches_expected"] is True
        assert canonical_json(canonical_payload(outcome["result"])) == canonical_json(canonical_payload(live))
