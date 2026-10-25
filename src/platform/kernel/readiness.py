"""Verify actual deployed gateway, production assets, workers and Beat."""

import hashlib
from urllib.parse import urljoin, urlparse

import httpx

from .runtime import token


def check_worker_reports(manifest, queues, reports):
    watched = {manifest.data["backend"]["queue"], *manifest.data["backend"].get("legacy_queues", [])}
    consumers = [
        name
        for name, items in queues.items()
        if any(q["name"] in watched for q in items)
    ]
    if not any(any(q["name"] == manifest.data["backend"]["queue"] for q in items) for items in queues.values()):
        raise ValueError("No Worker consumes the package queue")
    for name in consumers:
        report = reports.get(name, {})
        if manifest.id in report.get("dependency_errors", {}):
            raise ValueError(f"Worker package dependencies are unavailable: {name}")
        if report.get("apps", {}).get(manifest.id) != manifest.digest:
            raise ValueError(f"Worker code mismatch or missing inspection: {name}")
        if not set(manifest.data["backend"]["task_names"]) <= set(report.get("tasks", [])):
            raise ValueError(f"Worker task registration incomplete: {name}")
    return consumers


def probe(manifest, gateway_urls, frontend_url, celery_app=None):
    if not gateway_urls or not frontend_url:
        raise ValueError("Supply all Gateway instance URLs and the deployed frontend URL")
    evidence = {"digest": manifest.digest, "gateways": [], "frontend": frontend_url}
    with httpx.Client(timeout=10, follow_redirects=False) as client:
        for base in gateway_urls:
            response = client.get(
                base.rstrip("/") + "/_internal/app-packages",
                headers={"Authorization": "Bearer " + token()},
            )
            response.raise_for_status()
            if manifest.id in response.json().get("dependency_errors", {}):
                raise ValueError("Gateway package dependencies are unavailable")
            if response.json().get("apps", {}).get(manifest.id) != manifest.digest:
                raise ValueError("Gateway deployment does not match package code")
            evidence["gateways"].append(base)
        base = frontend_url.rstrip("/") + "/"
        response = client.get(urljoin(base, "netops-apps.json"))
        response.raise_for_status()
        report = response.json()
        if report.get("apps", {}).get(manifest.id) != manifest.digest:
            raise ValueError("Frontend build does not match package code")
        if not report.get("assets") or report.get("mode") != "production":
            raise ValueError("A complete production frontend build is required")
        # Verify served bytes as well as metadata; prevent stale metadata-only deploys.
        for asset, digest in report["assets"].items():
            if (
                asset.startswith("/")
                or ".." in asset.split("/")
                or urlparse(asset).scheme
                or "\\" in asset
            ):
                raise ValueError("Invalid asset path in deployment metadata")
            url = urljoin(base, asset)
            if not url.startswith(base):
                raise ValueError("Asset escapes frontend deployment")
            result = client.get(url)
            result.raise_for_status()
            if hashlib.sha256(result.content).hexdigest() != digest:
                raise ValueError(f"Frontend deployed asset mismatch: {asset}")
    backend = manifest.data["backend"]
    if backend.get("task_names"):
        if celery_app is None:
            import os

            from celery import Celery

            celery_app = Celery(
                "netops-deployment-inspector",
                broker=os.environ["CELERY_BROKER_URL"],
                backend=os.environ["CELERY_RESULT_BACKEND"],
            )
        inspector = celery_app.control.inspect(timeout=5)
        queues = inspector.active_queues() or {}
        replies = celery_app.control.broadcast("app_packages", reply=True, timeout=5)
        reports = {name: result for reply in replies for name, result in reply.items()}
        evidence["workers"] = check_worker_reports(manifest, queues, reports)
    if backend.get("schedules"):
        from .scheduler import beat_reports

        reports = beat_reports(celery_app)
        if not reports or any(
            r.get("apps", {}).get(manifest.id) != manifest.digest for r in reports
        ):
            raise ValueError("Beat heartbeat missing or running a different package version")
        if len(reports) != 1:
            raise ValueError("Multiple package Beat schedulers detected; run one scheduler")
        for schedule in backend["schedules"]:
            key = f"app.{manifest.id}.{schedule['name']}"
            if reports[0].get("schedules", {}).get(key) != schedule["task"]:
                raise ValueError("Beat package schedule registration is incomplete")
        evidence["beat"] = reports[0]["instance"]
    return evidence
