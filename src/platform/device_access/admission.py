"""One SSH admission implementation for collection, probes and future terminals."""

from contextlib import contextmanager
from threading import Condition
from time import monotonic

from .concurrency import (
    ConcurrencyWaitTimeoutError,
    DistributedSSHQuota,
    HierarchicalSSHLeaseManager,
)


def lease_manager():
    from src.common.config import get_settings

    settings = get_settings()
    options = dict(
        lease_seconds=settings.PATROL_CONCURRENCY_LEASE_SECONDS,
        wait_seconds=settings.PATROL_CONCURRENCY_WAIT_SECONDS,
        enabled=settings.PATROL_DISTRIBUTED_CONCURRENCY_ENABLED,
        # A new access path must never bypass admission when Redis is unavailable.
        fail_closed=True,
    )
    return HierarchicalSSHLeaseManager(
        global_semaphore=DistributedSSHQuota(limit=settings.PATROL_MAX_CONCURRENCY, **options),
        site_limit=settings.PATROL_SITE_MAX_CONCURRENCY,
        **options,
    )


# Shared across instances; the distributed deployment continues to use Redis.
_condition = Condition()
_active: dict[str, int] = {}


class SSHAdmission:
    @contextmanager
    def hold(self, target, profile):
        from src.common.config import get_settings

        settings = get_settings()
        # Inventory IDs deliberately retain the existing device fence identity.
        key = target.device_id or f"{target.management_domain}|{target.host}|{profile.port}"
        if settings.PATROL_DISTRIBUTED_CONCURRENCY_ENABLED:
            manager = lease_manager()
            with manager.reserve(device_id=key, site=target.site) as lease:
                yield lambda: manager.assert_lease(lease)
            return
        scopes = {
            "global": max(1, settings.PATROL_MAX_CONCURRENCY),
            f"site:{target.site}": max(1, settings.PATROL_SITE_MAX_CONCURRENCY),
            f"device:{key}": 1,
        }
        deadline = monotonic() + settings.PATROL_CONCURRENCY_WAIT_SECONDS
        with _condition:
            while any(_active.get(scope, 0) >= limit for scope, limit in scopes.items()):
                remaining = deadline - monotonic()
                if remaining <= 0:
                    raise ConcurrencyWaitTimeoutError("timed out waiting for local SSH admission")
                _condition.wait(remaining)
            for scope in scopes:
                _active[scope] = _active.get(scope, 0) + 1
        try:
            yield lambda: None
        finally:
            with _condition:
                for scope in scopes:
                    _active[scope] -= 1
                    if not _active[scope]:
                        del _active[scope]
                _condition.notify_all()
