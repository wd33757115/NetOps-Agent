"""Serialized administrative lifecycle; access closes before external work begins."""

from contextlib import contextmanager
from dataclasses import replace
from threading import RLock
from zipfile import ZipFile

import yaml
from django.db import connection, transaction

from src.platform.kernel.dependencies import check_dependencies
from src.platform.kernel.migrations import migrate
from src.platform.kernel.readiness import probe
from src.platform.kernel.registry import ROOT, discover, load_symbol
from src.platform.kernel.releases import archive

from .models import AppInstallation

_lock = RLock()


@contextmanager
def sqlite_file_lock():
    import hashlib
    import os
    import tempfile
    from pathlib import Path

    key = hashlib.sha256(str(connection.settings_dict["NAME"]).encode()).hexdigest()
    with (Path(tempfile.gettempdir()) / f"netops-app-lifecycle-{key}.lock").open("a+b") as handle:
        handle.write(b"0")
        handle.flush()
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)


@contextmanager
def lifecycle_lock():
    # Session advisory lock serializes different package dependency transitions.
    # Release even on failure; do not hold an uncommitted metadata transaction
    # while contacting the authority or executing migrations on another connection.
    with _lock:
        if connection.vendor == "sqlite":
            with sqlite_file_lock():
                yield
            return
        if connection.vendor == "postgresql":
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_lock(728031923)")
        elif connection.vendor != "sqlite":
            raise ValueError("Unsupported lifecycle database")
        try:
            yield
        finally:
            if connection.vendor == "postgresql":
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_advisory_unlock(728031923)")


def dependents(manifests, app_id):
    return [
        m.id
        for m in manifests.values()
        if app_id in m.data.get("dependencies", {})
        and AppInstallation.objects.filter(pk=m.id, state__in=["enabled", "draining"]).exists()
    ]


def live_changes(records, manifests):
    """Live packages that cannot run as they are, by app ID with the reason.

    Each needs disable/drain and an upgrade. Meanwhile it stays closed (admission
    answers 503) while the platform and every other application keep running.
    """
    held = {}
    for key, row in records.items():
        if row.state == "draining":
            held[key] = f"draining; finish with start.ps1 -UpgradeApps {key}"
        elif row.desired_enabled and (
            key not in manifests
            or row.deployment_digest != manifests[key].digest
            or row.state != "enabled"
        ):
            held[key] = f"code changed since deployment; upgrade with start.ps1 -UpgradeApps {key}"
    return held


def pending(manifest):
    hook = manifest.data.get("lifecycle", {}).get("pending")
    value = load_symbol(hook)() if hook else 0
    if type(value) is not int or value < 0:
        raise ValueError("pending hook must return a nonnegative integer")
    return value


def stopped(record):
    """No process of the deployed release answers, so nothing of it is in flight.

    Like a terminated Pod, a stopped deployment has no requests or tasks to wait
    for; draining it once failed on a refused connection and blocked every upgrade.
    A process that answers, even with an error or after a timeout, is running.
    """
    import httpx

    for base in record.readiness.get("gateways", []):
        try:
            httpx.get(base.rstrip("/") + "/_internal/app-packages", timeout=5)
        except httpx.ConnectError:
            continue
        except httpx.HTTPError:
            return False
        return False
    if record.readiness.get("workers"):
        from src.core.celery_tasks.celery_app import celery

        if celery.control.ping(timeout=2):
            return False
    return True


def active_requests(record):
    import httpx

    from src.platform.kernel.runtime import token

    count = 0
    manifest = discover()[record.app_id]
    if manifest.digest != record.deployment_digest:
        # Drain the release that is still running. The new source can change
        # both its digest and queue/task ownership before deployment starts.
        release = ROOT / ".runtime/app-package-releases" / record.app_id / (
            record.deployment_digest + ".zip"
        )
        with ZipFile(release) as retained:
            data = yaml.safe_load(retained.read("app.yaml"))
        if not isinstance(data, dict) or data.get("id") != record.app_id:
            raise ValueError("Retained deployment manifest does not match application")
        manifest = replace(manifest, data=data, digest=record.deployment_digest)
    if manifest.data["backend"].get("legacy_queues"):
        # Legacy queue consumers must be upgraded too; their in-flight work can
        # otherwise outlive closing the new application queue.
        from src.core.celery_tasks.celery_app import celery
        workers = record.readiness.get("workers", [])
        if not workers:
            raise ValueError("Verify deployed workers before handing off legacy tasks")
        from src.platform.kernel.readiness import check_worker_reports
        queues = celery.control.inspect(timeout=5).active_queues() or {}
        replies = celery.control.broadcast("app_packages", reply=True, timeout=5)
        current_reports = {name: report for reply in replies for name, report in reply.items()}
        workers = check_worker_reports(manifest, queues, current_reports)
        reports = celery.control.inspect(destination=workers, timeout=5).active() or {}
        if not set(workers) <= set(reports):
            raise ValueError("Worker activity inspection incomplete")
        owned = set(manifest.data["backend"].get("task_names", []))
        shared = {"execute_skill_task", "dispatch_workflow_step_task", "execute_workflow_step_only",
                  "preview_offline_patrol_job", "src.core.celery_tasks.tasks.execute_device_patrol_task"}
        count += sum(task.get("name") in owned | shared for items in reports.values() for task in items)
    for base in record.readiness.get("gateways", []):
        response = httpx.get(
            base.rstrip("/") + "/_internal/app-packages",
            headers={"Authorization": "Bearer " + token()},
            timeout=5,
        )
        response.raise_for_status()
        count += response.json()["active"][record.app_id]
    return count


def operate(action, app_id, *, gateway_urls=None, frontend_url=None, force=False):
    with lifecycle_lock():
        manifests = discover()
        if app_id not in manifests:
            raise ValueError("Unknown application")
        manifest = manifests[app_id]
        record = AppInstallation.objects.filter(pk=app_id).first()
        if not record:
            if action != "install":
                raise ValueError("Install the application first")
            record = AppInstallation.objects.create(
                app_id=app_id, installed_version="", deployment_digest="", state="discovered"
            )
        if action in {"install", "upgrade"}:
            if record.desired_enabled or record.state == "draining":
                raise ValueError("Disable and finish draining before changing a package")
            for dependency in manifest.data.get("dependencies", {}):
                other = manifests[dependency]
                if not AppInstallation.objects.filter(
                    pk=dependency,
                    deployment_digest=other.digest,
                    state__in=["installed", "disabled", "enabled"],
                ).exists():
                    raise ValueError(f"Install dependency first: {dependency}")
            # installed rows are safe to retry after a process crash or failed DDL.
            record.state, record.target_digest, record.error = "installing", manifest.digest, ""
            record.desired_enabled = False
            record.save()
            try:
                check_dependencies(manifest)
                archive(manifest)
                # Schema changes belong to the platform deployment entry point.
                # Installing an App records its release; it never runs DDL.
                head = migrate(manifest, check=True)
                record.migration_head = head or ""
                record.installed_version = manifest.data["version"]
                record.deployment_digest = manifest.digest
                record.state, record.readiness = "installed", {}
            except Exception as exc:
                record.state = "failed"
                record.error = (
                    str(exc)
                    if isinstance(exc, ValueError)
                    else type(exc).__name__ + ": see deployment logs"
                )
                record.save()
                raise ValueError(record.error) from exc
            record.save()
        elif action in {"enable", "verify"}:
            if record.deployment_digest != manifest.digest or record.state not in {
                "installed",
                "disabled",
                "enabled",
            }:
                raise ValueError("Install/upgrade the current package and finish draining first")
            for dependency in manifest.data.get("dependencies", {}):
                other = manifests[dependency]
                if not AppInstallation.objects.filter(
                    pk=dependency,
                    deployment_digest=other.digest,
                    desired_enabled=True,
                    state="enabled",
                ).exists():
                    raise ValueError(f"Dependency is not ready/enabled: {dependency}")
            check_dependencies(manifest)
            migrate(manifest, check=True)
            try:
                evidence = probe(manifest, gateway_urls, frontend_url)
            except Exception as exc:
                record.error = (
                    str(exc)
                    if isinstance(exc, ValueError)
                    else type(exc).__name__ + ": deployment probe failed"
                )
                record.save(update_fields=["error", "updated_at"])
                raise
            with transaction.atomic():
                record.readiness, record.error = evidence, ""
                if action == "enable":
                    record.state, record.desired_enabled = "enabled", True
                record.save()
        elif action in {"disable", "drain"}:
            if action == "drain" and record.state not in {"draining", "disabled"}:
                raise ValueError("Disable the application before polling drain")
            blockers = dependents(manifests, app_id)
            if blockers:
                raise ValueError(f"Disable dependent applications first: {blockers}")
            record.desired_enabled, record.state = False, "draining"
            record.save()
            try:
                # Admission is already closed. Let requests admitted before that
                # boundary finish committing, then settle durable work. As with a
                # Pod's grace period: once it has expired (force), or nothing of the
                # release runs any more, stop waiting. Durable records stay in the
                # database for the next release to resume or reconcile.
                finished = force or stopped(record)
                if not finished and active_requests(record):
                    return record
                hook = manifest.data.get("lifecycle", {}).get("drain")
                if hook:
                    load_symbol(hook)()
                left = pending(manifest)
                if left == 0 or finished:
                    record.state = "disabled"
                record.error = (
                    f"{left} durable work item(s) left for the next release"
                    if left and finished
                    else ""
                )
            except Exception as exc:
                record.error = type(exc).__name__ + ": drain incomplete; retain package code"
                record.save()
                raise ValueError(record.error) from exc
            record.save()
        else:
            raise ValueError("Unknown lifecycle action")
        return record
