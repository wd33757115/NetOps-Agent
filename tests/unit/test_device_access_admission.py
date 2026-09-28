"""Admission interoperability without changing or migrating inspection."""

import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Event
from types import SimpleNamespace

import pytest

from src.core.patrol import concurrency as legacy
from src.platform.device_access import concurrency as shared
from src.platform.device_access.admission import SSHAdmission
from src.platform.device_access.contracts import ConnectionProfile, Target


class Redis:
    """Small protocol double shared by old and new lease implementations."""

    def __init__(self):
        self.zsets, self.values, self.counters = {}, {}, {}

    def eval(self, script, count, *args):
        if script in (shared._ACQUIRE_SCRIPT, legacy._ACQUIRE_SCRIPT):
            key, now, expires, limit, token = args
            active = {k: v for k, v in self.zsets.get(key, {}).items() if v > now}
            self.zsets[key] = active
            if len(active) >= limit:
                return 0
            active[token] = expires
            return 1
        if script in (shared._RENEW_SCRIPT, legacy._RENEW_SCRIPT):
            key, token, expires, ttl = args
            if token not in self.zsets.get(key, {}):
                return 0
            self.zsets[key][token] = expires
            return 1
        if script == shared._VERIFY_SCRIPT:
            key, token = args
            return int(self.zsets.get(key, {}).get(token, 0) > time.time() * 1000)
        if script in (shared._EXCLUSIVE_ACQUIRE_SCRIPT, legacy._EXCLUSIVE_ACQUIRE_SCRIPT):
            key, fence_key, token, ttl = args
            if key in self.values:
                return 0
            self.counters[fence_key] = self.counters.get(fence_key, 0) + 1
            self.values[key] = token
            return self.counters[fence_key]
        if script in (shared._EXCLUSIVE_VERIFY_SCRIPT, shared._EXCLUSIVE_RENEW_SCRIPT):
            key, token = args[:2]
            return int(self.values.get(key) == token)
        if script == shared._EXCLUSIVE_RELEASE_SCRIPT:
            key, token = args
            if self.values.get(key) == token:
                del self.values[key]
                return 1
            return 0
        raise AssertionError("unexpected Lua script")

    def zrem(self, key, token):
        self.zsets.get(key, {}).pop(token, None)


@pytest.fixture
def redis(monkeypatch):
    instance = Redis()
    monkeypatch.setattr(shared, "get_redis", lambda: instance)
    monkeypatch.setattr(legacy, "get_redis", lambda: instance)
    return instance


def test_original_patrol_and_new_module_share_global_capacity(redis):
    old = legacy.DistributedPatrolSemaphore(limit=1, lease_seconds=30, wait_seconds=1)
    new = shared.DistributedSSHQuota(limit=1, lease_seconds=30, wait_seconds=1)
    lease = old.acquire()
    assert lease.key == new.key == "netops:patrol:ssh:global"
    with pytest.raises(TimeoutError):
        new.acquire()
    old.release(lease)
    next_lease = new.acquire()
    new.assert_lease(next_lease)
    new.release(next_lease)


def test_new_hierarchy_uses_same_device_fence_and_releases_every_scope(redis):
    manager = shared.HierarchicalSSHLeaseManager(
        global_semaphore=shared.DistributedSSHQuota(limit=3, lease_seconds=30, wait_seconds=1),
        site_limit=2,
        lease_seconds=30,
        wait_seconds=1,
    )
    with manager.reserve(device_id="device1", site="site1") as lease:
        expected, _ = legacy.RedisExclusiveFencedLease(lease_seconds=30, wait_seconds=1)._keys(
            "device1"
        )
        assert lease.device_lease.key == expected
        manager.assert_lease(lease)
        assert len(redis.values) == 1
    assert not redis.values and all(not members for members in redis.zsets.values())


def test_global_lease_eviction_is_detected_before_next_heartbeat(redis):
    quota = shared.DistributedSSHQuota(limit=2, lease_seconds=30, wait_seconds=1)
    lease = quota.acquire()
    redis.zrem(lease.key, lease.token)
    with pytest.raises(shared.ConcurrencyLeaseLostError):
        quota.assert_lease(lease)


def test_later_scope_failure_releases_earlier_scopes(redis, monkeypatch):
    manager = shared.HierarchicalSSHLeaseManager(
        global_semaphore=shared.DistributedSSHQuota(limit=3, lease_seconds=30, wait_seconds=1),
        site_limit=2,
        lease_seconds=30,
        wait_seconds=1,
    )

    def fail(scope):
        raise TimeoutError("device occupied")

    monkeypatch.setattr(manager.device_leases, "acquire", fail)
    with pytest.raises(TimeoutError):
        with manager.reserve(device_id="device1", site="site1"):
            pytest.fail("should not acquire device")
    assert all(not members for members in redis.zsets.values())


def settings(**overrides):
    return SimpleNamespace(
        **{
            "PATROL_MAX_CONCURRENCY": 2,
            "PATROL_SITE_MAX_CONCURRENCY": 2,
            "PATROL_CONCURRENCY_WAIT_SECONDS": 1,
            "PATROL_CONCURRENCY_LEASE_SECONDS": 30,
            "PATROL_DISTRIBUTED_CONCURRENCY_ENABLED": False,
            **overrides,
        }
    )


def test_local_device_limit_is_shared_across_service_instances(monkeypatch):
    monkeypatch.setattr("src.common.config.get_settings", lambda: settings())
    target = Target("a", "192.0.2.1", device_id="device1")
    profile = ConnectionProfile("manual", "operator")
    entered = Event()

    def wait_for_slot():
        with SSHAdmission().hold(target, profile):
            entered.set()

    with ThreadPoolExecutor(max_workers=1) as pool:
        with SSHAdmission().hold(target, profile):
            future = pool.submit(wait_for_slot)
            assert not entered.wait(0.05)
        future.result(timeout=2)
    assert entered.is_set()


def test_overlapping_addresses_in_different_domains_do_not_share_device_lock(monkeypatch):
    monkeypatch.setattr("src.common.config.get_settings", lambda: settings())
    target = Target("a", "192.0.2.1", management_domain="domain1")
    profile = ConnectionProfile("manual", "operator")
    with SSHAdmission().hold(target, profile):
        with SSHAdmission().hold(
            replace(target, target_ref="b", management_domain="domain2"), profile
        ):
            pass


def test_redis_unavailable_does_not_fall_back_and_connect(monkeypatch):
    monkeypatch.setattr(
        "src.common.config.get_settings",
        lambda: settings(PATROL_DISTRIBUTED_CONCURRENCY_ENABLED=True),
    )
    monkeypatch.setattr(shared, "get_redis", lambda: None)
    with pytest.raises(shared.ConcurrencyUnavailableError):
        with SSHAdmission().hold(Target("a", "192.0.2.1"), ConnectionProfile("manual", "operator")):
            pytest.fail("must not connect")
