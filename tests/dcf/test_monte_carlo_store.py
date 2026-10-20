"""Store semantics: TTLs, set-if-absent, and ownership-token (owner-checked) locks."""

import pytest

from src.dcf_model import monte_carlo_store as store_module
from src.dcf_model.monte_carlo_store import (
    RELEASE_LOCK_SCRIPT,
    InMemoryStore,
    StoreUnavailable,
    UpstashStore,
    acquire_lock,
    default_store,
    release_lock,
    shared_store_required,
)


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class FakeRedis:
    """Just enough of the Upstash client: get / set(nx, ex) / eval(release script)."""

    def __init__(self, fail=False):
        self.data = {}
        self.fail = fail
        self.evals = []

    def get(self, key):
        self._maybe_fail()
        return self.data.get(key)

    def set(self, key, value, nx=None, ex=None):
        self._maybe_fail()
        if nx and key in self.data:
            return None
        self.data[key] = value
        return True

    def eval(self, script, keys=None, args=None):
        self._maybe_fail()
        self.evals.append((script, keys, args))
        assert script == RELEASE_LOCK_SCRIPT
        if self.data.get(keys[0]) == args[0]:
            del self.data[keys[0]]
            return 1
        return 0

    def _maybe_fail(self):
        if self.fail:
            raise ConnectionError("boom")


@pytest.fixture(params=["memory", "upstash"])
def store(request):
    return InMemoryStore() if request.param == "memory" else UpstashStore(FakeRedis())


class TestSharedContract:
    def test_get_set_and_missing(self, store):
        assert store.get("k") is None
        store.set("k", "v", 10)
        assert store.get("k") == "v"

    def test_set_if_absent_is_exclusive(self, store):
        assert store.set_if_absent("lock", "a", 10) is True
        assert store.set_if_absent("lock", "b", 10) is False
        assert store.get("lock") == "a"

    def test_acquire_returns_a_unique_token_only_to_the_first_caller(self, store):
        first = acquire_lock(store, "lock", 10)
        assert first and acquire_lock(store, "lock", 10) is None
        assert release_lock(store, "lock", first) is True
        second = acquire_lock(store, "lock", 10)
        assert second and second != first

    def test_release_is_owner_checked(self, store):
        token = acquire_lock(store, "lock", 10)
        assert release_lock(store, "lock", "not-the-owner") is False
        assert store.get("lock") == token  # the real owner's lock survives a wrong release
        assert release_lock(store, "lock", token) is True and store.get("lock") is None

    def test_releasing_a_lock_that_no_longer_exists_is_harmless(self, store):
        assert release_lock(store, "gone", "t") is False


class TestInMemoryExpiry:
    def test_entries_and_locks_expire(self):
        clock = FakeClock()
        store = InMemoryStore(clock=clock)
        store.set("k", "v", 5)
        assert acquire_lock(store, "lock", 5) is not None
        clock.now += 4.9
        assert store.get("k") == "v" and acquire_lock(store, "lock", 5) is None
        clock.now += 0.2
        assert store.get("k") is None
        assert acquire_lock(store, "lock", 5) is not None  # expired lock can be taken over

    def test_an_expired_owner_cannot_release_the_new_owners_lock(self):
        clock = FakeClock()
        store = InMemoryStore(clock=clock)
        old = acquire_lock(store, "lock", 5)
        clock.now += 6
        new = acquire_lock(store, "lock", 5)
        assert release_lock(store, "lock", old) is False
        assert store.get("lock") == new


class TestUpstashAdapter:
    def test_lock_uses_set_nx_with_ttl_and_release_uses_the_compare_and_delete_script(self):
        client = FakeRedis()
        recorded = {}
        original = client.set

        def spy(key, value, nx=None, ex=None):
            recorded.update(nx=nx, ex=ex)
            return original(key, value, nx=nx, ex=ex)

        client.set = spy
        store = UpstashStore(client)
        token = acquire_lock(store, "lock", 45)
        assert recorded == {"nx": True, "ex": 45}
        release_lock(store, "lock", token)
        assert client.evals == [(RELEASE_LOCK_SCRIPT, ["lock"], [token])]
        assert "get" in RELEASE_LOCK_SCRIPT and "del" in RELEASE_LOCK_SCRIPT

    def test_transport_failures_become_store_unavailable(self):
        store = UpstashStore(FakeRedis(fail=True))
        for call in (
            lambda: store.get("k"), lambda: store.set("k", "v", 1),
            lambda: store.set_if_absent("k", "v", 1), lambda: store.release_if_owner("k", "t"),
        ):
            with pytest.raises(StoreUnavailable):
                call()


class TestDefaultStore:
    def test_shared_store_is_required_on_vercel_by_default(self):
        assert shared_store_required({"VERCEL": "1"}) is True
        assert shared_store_required({}) is False
        assert shared_store_required({"VERCEL": "1", "MONTE_CARLO_REQUIRE_SHARED_STORE": "0"}) is False
        assert shared_store_required({"MONTE_CARLO_REQUIRE_SHARED_STORE": "1"}) is True

    def test_uses_upstash_when_configured(self, monkeypatch):
        client = FakeRedis()
        monkeypatch.setattr(store_module, "_shared_client", lambda: client)
        assert isinstance(default_store({}), UpstashStore)

    def test_missing_redis_is_fatal_when_a_shared_store_is_required(self, monkeypatch):
        monkeypatch.setattr(store_module, "_shared_client", lambda: None)
        with pytest.raises(StoreUnavailable):
            default_store({"VERCEL": "1"})

    def test_missing_redis_falls_back_to_one_process_local_store_otherwise(self, monkeypatch):
        monkeypatch.setattr(store_module, "_shared_client", lambda: None)
        monkeypatch.setattr(store_module, "_local_store", None)
        first = default_store({})
        assert isinstance(first, InMemoryStore) and default_store({}) is first


class TestSharedClientIsBuiltOnceUnderConcurrency:
    def test_concurrent_cold_starts_all_get_the_shared_store(self, monkeypatch):
        """Regression: a lazy-init helper that flags itself initialized before the client
        exists hands `None` to threads arriving mid-construction, silently demoting them
        to a process-local store. 16 racing threads must all get the same shared client."""
        import sys
        import threading
        import time
        import types

        built = []

        class SlowRedis:
            def __init__(self, url, token):
                time.sleep(0.15)  # a slow constructor widens the race window
                built.append(self)

        monkeypatch.setitem(sys.modules, "upstash_redis", types.SimpleNamespace(Redis=SlowRedis))
        monkeypatch.setenv("MONTE_CARLO_UPSTASH_REDIS_REST_URL", "http://redis.invalid")
        monkeypatch.setenv("MONTE_CARLO_UPSTASH_REDIS_REST_TOKEN", "not-a-real-token")
        monkeypatch.setattr(store_module, "_client", None)
        monkeypatch.setattr(store_module, "_client_checked", False)

        stores, lock = [], threading.Lock()

        def worker():
            result = default_store({"VERCEL": "1"})  # required: a None client would raise
            with lock:
                stores.append(result)

        threads = [threading.Thread(target=worker) for _ in range(16)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert len(stores) == 16 and all(isinstance(s, UpstashStore) for s in stores)
        assert len(built) == 1
        assert len({id(s._client) for s in stores}) == 1

    def test_missing_credentials_mean_no_client_and_are_not_retried_forever(self, monkeypatch):
        for name in ("MONTE_CARLO_UPSTASH_REDIS_REST_URL", "MONTE_CARLO_UPSTASH_REDIS_REST_TOKEN"):
            monkeypatch.delenv(name, raising=False)
        monkeypatch.setattr(store_module, "_client", None)
        monkeypatch.setattr(store_module, "_client_checked", False)
        assert store_module._shared_client() is None
        with pytest.raises(StoreUnavailable):
            default_store({"VERCEL": "1"})


class TestCredentialIsolation:
    """The Monte Carlo store and the generic cache helper are configured independently."""

    @staticmethod
    def _fake_upstash(monkeypatch):
        import sys
        import types

        built = []

        class RecordingRedis:
            def __init__(self, url, token):
                built.append((url, token))

        monkeypatch.setitem(sys.modules, "upstash_redis", types.SimpleNamespace(Redis=RecordingRedis))
        monkeypatch.setattr(store_module, "_client", None)
        monkeypatch.setattr(store_module, "_client_checked", False)
        return built

    @staticmethod
    def _fresh_generic_cache(monkeypatch):
        from src.utils import cache

        monkeypatch.setattr(cache, "load_dotenv", lambda *a, **k: None)
        monkeypatch.setattr(cache, "_redis_client", None)
        monkeypatch.setattr(cache, "_client_initialized", False)
        return cache

    def test_monte_carlo_credentials_alone_do_not_activate_the_generic_cache(self, monkeypatch):
        built = self._fake_upstash(monkeypatch)
        monkeypatch.delenv("UPSTASH_REDIS_REST_URL", raising=False)
        monkeypatch.delenv("UPSTASH_REDIS_REST_TOKEN", raising=False)
        monkeypatch.setenv("MONTE_CARLO_UPSTASH_REDIS_REST_URL", "http://mc.invalid")
        monkeypatch.setenv("MONTE_CARLO_UPSTASH_REDIS_REST_TOKEN", "mc-token")
        cache = self._fresh_generic_cache(monkeypatch)

        assert isinstance(default_store({"VERCEL": "1"}), UpstashStore)
        assert built == [("http://mc.invalid", "mc-token")]
        assert cache._get_redis_client() is None  # generic caching stays a passthrough
        assert built == [("http://mc.invalid", "mc-token")]  # and built no second client

    def test_generic_credentials_alone_do_not_satisfy_the_required_store(self, monkeypatch):
        built = self._fake_upstash(monkeypatch)
        monkeypatch.delenv("MONTE_CARLO_UPSTASH_REDIS_REST_URL", raising=False)
        monkeypatch.delenv("MONTE_CARLO_UPSTASH_REDIS_REST_TOKEN", raising=False)
        monkeypatch.setenv("UPSTASH_REDIS_REST_URL", "http://generic.invalid")
        monkeypatch.setenv("UPSTASH_REDIS_REST_TOKEN", "generic-token")

        with pytest.raises(StoreUnavailable):
            default_store({"VERCEL": "1"})
        assert built == []

    @pytest.mark.parametrize("present", ["url", "token"])
    def test_half_configured_monte_carlo_credentials_fail_closed(self, monkeypatch, present):
        built = self._fake_upstash(monkeypatch)
        monkeypatch.delenv("MONTE_CARLO_UPSTASH_REDIS_REST_URL", raising=False)
        monkeypatch.delenv("MONTE_CARLO_UPSTASH_REDIS_REST_TOKEN", raising=False)
        monkeypatch.setenv(
            "MONTE_CARLO_UPSTASH_REDIS_REST_URL" if present == "url" else "MONTE_CARLO_UPSTASH_REDIS_REST_TOKEN", "x"
        )
        with pytest.raises(StoreUnavailable):
            default_store({"VERCEL": "1"})
        assert built == []

    def test_missing_credentials_fail_closed_on_vercel_without_the_override(self, monkeypatch):
        self._fake_upstash(monkeypatch)
        for name in ("MONTE_CARLO_UPSTASH_REDIS_REST_URL", "MONTE_CARLO_UPSTASH_REDIS_REST_TOKEN"):
            monkeypatch.delenv(name, raising=False)
        monkeypatch.delenv("MONTE_CARLO_REQUIRE_SHARED_STORE", raising=False)
        with pytest.raises(StoreUnavailable):
            default_store({"VERCEL": "1"})
