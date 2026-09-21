"""Opt-in P5 delivery against a disposable PostgreSQL database and Redis broker.

The launcher must provision POSTGRES_DB=netops_p5_delivery_<unique suffix> before
importing Django/business modules. Never point this test at the normal database.
"""

import os
import socket
import subprocess
import sys
import tempfile
import threading
from functools import partial
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import skipUnless
from unittest.mock import patch

import httpx
import uvicorn
from celery import Celery
from django.contrib.auth import get_user_model
from django.test import LiveServerTestCase, override_settings
from fastapi import FastAPI, HTTPException
from rest_framework_simplejwt.tokens import AccessToken

from src.platform.kernel.readiness import probe
from src.platform.kernel.registry import ROOT, discover, mount_apps
from src.platform.kernel.runtime import snapshot

from .app_lifecycle import operate
from .models import AppGrant
from .test_firewall_deployment import QuietHandler, until


@skipUnless(
    os.getenv("TEST_PACKAGE_BROKER_URL")
    and os.getenv("POSTGRES_DB", "").startswith("netops_p5_delivery_"),
    "Requires explicitly disposable PostgreSQL database and Redis",
)
@override_settings(ALLOWED_HOSTS=["127.0.0.1", "localhost", "testserver"])
class InspectionDeploymentTests(LiveServerTestCase):
    def test_grant_fixed_task_restart_revocation_legacy_urls_and_drain(self):
        # Existing tables remain owned by the historical platform schema.
        # The launcher has run the unmodified historical Alembic chain to head.
        import uuid
        from datetime import datetime, timezone

        from src.infrastructure.db.models import OfflinePatrolJobRecord, WorkflowRun
        from src.infrastructure.db.postgres import get_db_session
        from src.platform.authorization.client import authorize_worker

        broker = os.environ["TEST_PACKAGE_BROKER_URL"]
        with tempfile.TemporaryDirectory(prefix="netops-p5-") as directory:
            path = Path(directory)
            with patch.dict(
                os.environ,
                {
                    "NETOPS_AUTHORITY_URL": self.live_server_url,
                    "NETOPS_APP_INTERNAL_SECRET": "isolated-p5-deployment-test-secret",
                    "CELERY_BROKER_URL": broker,
                    "CELERY_RESULT_BACKEND": broker,
                    "CELERY_WORKER_ROLE": "packages",
                    "EVENT_BUS_ENABLED": "false",
                },
            ):
                snapshot.cache_clear()
                user = get_user_model().objects.create_user(username="p5-viewer")
                from .roles import assign_user_role

                assign_user_role(user, "viewer")
                record = operate("install", "inspection")
                self.assertEqual(operate("install", "inspection").pk, record.pk)
                gateway = FastAPI()
                mount_apps(gateway)
                from src.gateway.offline_patrol_api import router

                gateway.include_router(router)
                sock = socket.socket()
                sock.bind(("127.0.0.1", 0))
                gateway_url = f"http://127.0.0.1:{sock.getsockname()[1]}"
                server = uvicorn.Server(uvicorn.Config(gateway, lifespan="off", log_level="error"))
                gateway_thread = threading.Thread(
                    target=server.run, kwargs={"sockets": [sock]}, daemon=True
                )
                frontend = ThreadingHTTPServer(
                    ("127.0.0.1", 0),
                    partial(QuietHandler, directory=str(ROOT / "web/react_frontend/dist")),
                )
                frontend_thread = threading.Thread(target=frontend.serve_forever, daemon=True)
                frontend_url = f"http://127.0.0.1:{frontend.server_port}"
                client_app = Celery("p5-delivery", broker=broker, backend=broker)
                processes, logs = [], []

                def launch(role):
                    args = (
                        [
                            "worker",
                            "-Q",
                            "netops.patrol",
                            "-P",
                            "threads",
                            "--concurrency=2",
                            "--hostname=p5-validation@%h",
                        ]
                        if role == "worker"
                        else ["beat", "--schedule", str(path / "beat-schedule")]
                    )
                    log = (path / f"{role}-{len(logs)}.log").open("w", encoding="utf-8")
                    logs.append(log)
                    environment = os.environ.copy()
                    environment.pop("DJANGO_SETTINGS_MODULE", None)
                    process = subprocess.Popen(
                        [
                            sys.executable,
                            "-m",
                            "celery",
                            "-A",
                            "src.platform.kernel.package_celery:app",
                            *args,
                            "--loglevel=warning",
                        ],
                        cwd=ROOT,
                        env=environment,
                        stdout=log,
                        stderr=log,
                        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
                    )
                    processes.append(process)
                    return process

                def stop(process):
                    if process.poll() is None:
                        process.terminate()
                        try:
                            process.wait(timeout=8)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait(timeout=5)

                gateway_thread.start()
                frontend_thread.start()
                try:
                    until(lambda: server.started)
                    worker = launch("worker")
                    launch("beat")
                    until(
                        lambda: probe(
                            discover()["inspection"], [gateway_url], frontend_url, client_app
                        ),
                        timeout=60,
                    )
                    operate(
                        "enable",
                        "inspection",
                        gateway_urls=[gateway_url],
                        frontend_url=frontend_url,
                    )
                    headers = {"Authorization": "Bearer " + str(AccessToken.for_user(user))}
                    with (
                        override_settings(FASTAPI_BASE_URL=gateway_url),
                        patch("bff.proxy_client.FASTAPI_BASE_URL", gateway_url),
                        # LiveServer uses WSGI and creates an event loop per async
                        # view. Do not reuse connections across those test loops.
                        patch(
                            "bff.proxy_client._client",
                            httpx.AsyncClient(
                                limits=httpx.Limits(max_keepalive_connections=0),
                                timeout=20,
                            ),
                        ),
                        patch("bff.decorators.token_is_revoked", return_value=False),
                        httpx.Client(headers=headers, timeout=20, follow_redirects=True) as client,
                    ):
                        root = self.live_server_url + "/api/apps/inspection"
                        legacy = self.live_server_url + "/api/patrol/offline-jobs/"
                        direct = gateway_url + "/api/v1/patrol/offline-jobs"
                        self.assertEqual(client.get(root + "/patrol/offline-jobs").status_code, 403)
                        self.assertEqual(client.get(direct).status_code, 403)
                        from django.contrib.auth.models import Group

                        group = Group.objects.create(name="inspection-users")
                        user.groups.add(group)
                        grant = AppGrant.objects.create(app=record, group=group)
                        self.assertEqual(client.get(root + "/patrol/offline-jobs").status_code, 200)
                        self.assertEqual(client.get(legacy).status_code, 200)
                        self.assertEqual(client.get(direct).status_code, 200)
                        self.assertEqual(
                            authorize_worker(str(user.pk), "inspection").role, "viewer"
                        )

                        def create_job(key):
                            response = client.post(
                                root + "/patrol/offline-jobs",
                                json={
                                    "job_type": "single_patrol",
                                    "name": "P5 isolated delivery",
                                    "periods": [
                                        {
                                            "role": "current",
                                            "label": "current",
                                            "observed_at": datetime.now(timezone.utc).isoformat(),
                                        }
                                    ],
                                    "idempotency_key": key,
                                },
                            )
                            self.assertEqual(response.status_code, 201, response.text)
                            payload = response.json()
                            return payload.get("data", payload)["id"]

                        def queue_job(job_id):
                            task_id, key = str(uuid.uuid4()), uuid.uuid4().hex
                            with get_db_session() as db:
                                job = db.get(OfflinePatrolJobRecord, job_id)
                                job.status, job.celery_task_id, job.execution_idempotency_key = (
                                    "queued",
                                    task_id,
                                    key,
                                )
                            return task_id, key

                        def read_job(job_id):
                            with get_db_session() as db:
                                row = db.get(OfflinePatrolJobRecord, job_id)
                                return {
                                    "status": row.status,
                                    "error": row.error_message,
                                    "result": row.result_json,
                                }

                        job_id = create_job("p5-create-" + uuid.uuid4().hex)
                        task_id, key = queue_job(job_id)
                        # A real fixed business task rejects missing evidence at its quality gate.
                        # No test-only Celery task, device connection, model call or mocked worker.
                        client_app.send_task(
                            "netops.inspection.execute_offline",
                            args=[job_id, key],
                            task_id=task_id,
                            queue="netops.patrol",
                        ).get(timeout=35)
                        self.assertEqual(read_job(job_id)["status"], "failed")
                        before = read_job(job_id)
                        client_app.send_task(
                            "netops.inspection.execute_offline",
                            args=[job_id, key],
                            task_id=task_id,
                            queue="netops.patrol",
                        ).get(timeout=20)
                        self.assertEqual(read_job(job_id), before)
                        with get_db_session() as db:
                            self.assertEqual(db.query(WorkflowRun).count(), 0)

                        stop(worker)
                        queued = create_job("p5-revoke-" + uuid.uuid4().hex)
                        queue_job(
                            queued
                        )  # Beat must recover this durable intent after Worker restart.
                        grant.delete()
                        self.assertEqual(client.get(legacy).status_code, 403)
                        self.assertEqual(client.get(direct).status_code, 403)
                        with self.assertRaises(HTTPException):
                            authorize_worker(str(user.pk), "inspection")
                        worker = launch("worker")
                        until(lambda: read_job(queued)["status"] == "failed", timeout=95)
                        self.assertIn("403", read_job(queued)["error"])
                        self.assertEqual((read_job(queued)["result"] or {}).get("steps"), {})
                        grant = AppGrant.objects.create(app=record, user=user)
                        pending_id = create_job("p5-drain-" + uuid.uuid4().hex)
                        with get_db_session() as db:
                            job = db.get(OfflinePatrolJobRecord, pending_id)
                            job.status = (
                                "queued"  # no broker ID: interrupted publication, must still drain
                            )
                        until(lambda: client_app.control.inspect(timeout=1).ping())
                        with patch("src.core.celery_tasks.celery_app.celery", client_app):
                            until(
                                lambda: operate("disable", "inspection").state == "disabled",
                                timeout=40,
                            )
                        self.assertEqual(read_job(pending_id)["status"], "failed")
                        self.assertIn("停用", read_job(pending_id)["error"])
                        self.assertEqual(client.get(root + "/patrol/offline-jobs").status_code, 404)
                        self.assertEqual(client.get(legacy).status_code, 404)
                finally:
                    for process in processes:
                        stop(process)
                    for log in logs:
                        log.close()
                    import shutil

                    output = ROOT / ".tmp/p5-final/deployment"
                    output.mkdir(parents=True, exist_ok=True)
                    for log in path.glob("*.log"):
                        shutil.copy2(log, output / log.name)
                    server.should_exit = True
                    gateway_thread.join(timeout=5)
                    sock.close()
                    frontend.shutdown()
                    frontend.server_close()
                    frontend_thread.join(timeout=5)
                    client_app.close()
