"""Opt-in P4 delivery against a disposable PostgreSQL database and Redis broker.

The launcher must provision POSTGRES_DB=netops_p4_delivery_<unique suffix> before
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
    and os.getenv("POSTGRES_DB", "").startswith("netops_p4_delivery_"),
    "Requires explicitly disposable PostgreSQL database and Redis",
)
@override_settings(ALLOWED_HOSTS=["127.0.0.1", "localhost", "testserver"])
class RuleGovernanceDeploymentTests(LiveServerTestCase):
    def test_grant_worker_restart_revocation_legacy_urls_and_drain(self):
        # Existing tables remain owned by the historical platform schema.
        # The launcher has run the unmodified historical Alembic chain to head.
        import uuid

        from src.apps.rule_governance.backend.rule_control.generation_jobs import GenerationJobs
        from src.infrastructure.db.models import RuleGenerationJobRecord, WorkflowRun
        from src.infrastructure.db.postgres import get_db_session
        from src.platform.authorization.client import authorize_worker
        from src.platform.tools.registry import invoke

        seed_id = "p4-seed-" + uuid.uuid4().hex
        broker = os.environ["TEST_PACKAGE_BROKER_URL"]
        with tempfile.TemporaryDirectory(prefix="netops-p4-") as directory:
            path = Path(directory)
            with patch.dict(
                os.environ,
                {
                    "NETOPS_AUTHORITY_URL": self.live_server_url,
                    "NETOPS_APP_INTERNAL_SECRET": "isolated-p4-deployment-test-secret",
                    "CELERY_BROKER_URL": broker,
                    "CELERY_RESULT_BACKEND": broker,
                    "CELERY_WORKER_ROLE": "packages",
                    "EVENT_BUS_ENABLED": "false",
                },
            ):
                snapshot.cache_clear()
                user = get_user_model().objects.create_user(username="p4-viewer")
                from .roles import assign_user_role

                assign_user_role(user, "viewer")
                record = operate("install", "rule-governance")
                self.assertEqual(operate("install", "rule-governance").pk, record.pk)
                gateway = FastAPI()
                mount_apps(gateway)
                from src.apps.rule_governance.backend.api import router

                gateway.include_router(router, prefix="/api/v1")
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
                client_app = Celery("p4-delivery", broker=broker, backend=broker)
                processes, logs = [], []

                def launch(role):
                    args = (
                        [
                            "worker",
                            "-Q",
                            "app.rule-governance",
                            "-P",
                            "threads",
                            "--concurrency=2",
                            "--hostname=p4-validation@%h",
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
                            discover()["rule-governance"], [gateway_url], frontend_url, client_app
                        ),
                        timeout=60,
                    )
                    operate(
                        "enable",
                        "rule-governance",
                        gateway_urls=[gateway_url],
                        frontend_url=frontend_url,
                    )
                    headers = {"Authorization": "Bearer " + str(AccessToken.for_user(user))}
                    with (
                        override_settings(FASTAPI_BASE_URL=gateway_url),
                        patch("bff.decorators.token_is_revoked", return_value=False),
                        httpx.Client(headers=headers, timeout=20, follow_redirects=True) as client,
                    ):
                        root = self.live_server_url + "/api/apps/rule-governance"
                        legacy = self.live_server_url + "/api/parser/jobs/"
                        self.assertEqual(client.get(root + "/parser/jobs").status_code, 403)
                        self.assertEqual(
                            client.get(gateway_url + "/api/v1/parser/jobs").status_code, 403
                        )
                        grant = AppGrant.objects.create(app=record, user=user)
                        response = client.get(root + "/parser/jobs")
                        self.assertEqual(response.status_code, 200, response.text)
                        self.assertEqual(client.get(legacy).status_code, 200)
                        self.assertEqual(
                            client.get(gateway_url + "/api/v1/parser/jobs").status_code, 200
                        )
                        # Viewer is allowed the same business operation; no internal role gate.
                        response = client.post(
                            root + "/parser/asset-production/automation-control",
                            json={
                                "mode": "disabled",
                                "reason": "P4 isolated deployment verification",
                            },
                        )
                        self.assertEqual(response.status_code, 200, response.text)
                        self.assertEqual(
                            authorize_worker(str(user.pk), "rule-governance").role, "viewer"
                        )

                        with get_db_session() as db:
                            db.add(
                                RuleGenerationJobRecord(id=seed_id, job_type="OBSERVATION_CONTRACT")
                            )
                        jobs = GenerationJobs()
                        # No model call: deterministic invalid input exercises the real fixed
                        # generator subprocess and durable failed state, rather than a test task.
                        with patch.object(
                            jobs,
                            "dispatch",
                            lambda run_id: client_app.send_task(
                                "netops.rule_governance.generate",
                                args=[run_id],
                                task_id=run_id,
                                queue="app.rule-governance",
                            ),
                        ):
                            run_id = jobs.submit(
                                seed_id,
                                {"generation_provider": "builtin", "records": []},
                                user_id=str(user.pk),
                            )
                        until(lambda: jobs.read(run_id)["status"] == "failed", timeout=45)
                        self.assertIn("error", jobs.read(run_id))
                        self.assertEqual(
                            client.get(root + "/generation-runs/" + run_id).status_code, 200
                        )
                        self.assertEqual(
                            invoke(
                                "rule_governance__read_generation",
                                {"run_id": run_id},
                                actor_id=str(user.pk),
                            )["status"],
                            "failed",
                        )
                        with get_db_session() as db:
                            self.assertEqual(db.query(WorkflowRun).count(), 0)

                        stop(worker)
                        with patch.object(
                            jobs,
                            "dispatch",
                            lambda run_id: client_app.send_task(
                                "netops.rule_governance.generate",
                                args=[run_id],
                                task_id=run_id,
                                queue="app.rule-governance",
                            ),
                        ):
                            queued = jobs.submit(
                                seed_id,
                                {"generation_provider": "builtin", "records": []},
                                user_id=str(user.pk),
                            )
                        grant.delete()
                        self.assertEqual(client.get(legacy).status_code, 403)
                        self.assertEqual(
                            client.get(root + "/generation-runs/" + run_id).status_code, 403
                        )
                        self.assertEqual(
                            client.get(gateway_url + "/api/v1/parser/jobs").status_code, 403
                        )
                        with self.assertRaises(HTTPException):
                            invoke(
                                "rule_governance__read_generation",
                                {"run_id": run_id},
                                actor_id=str(user.pk),
                            )
                        worker = launch("worker")
                        until(lambda: jobs.read(queued)["status"] == "failed", timeout=45)
                        self.assertEqual(jobs.read(queued)["steps"], {})
                        grant = AppGrant.objects.create(app=record, user=user)
                        # Prepare a queued row without publishing, then ensure disable fences it.
                        with patch.object(jobs, "dispatch", return_value=False):
                            pending = jobs.submit(seed_id, {}, user_id=str(user.pk))
                        until(lambda: client_app.control.inspect(timeout=1).ping())
                        with patch("src.core.celery_tasks.celery_app.celery", client_app):
                            until(
                                lambda: operate("disable", "rule-governance").state == "disabled",
                                timeout=35,
                            )
                        self.assertEqual(jobs.read(pending)["status"], "cancelled")
                        client_app.send_task(
                            "netops.rule_governance.generate",
                            args=[pending],
                            task_id=pending,
                            queue="app.rule-governance",
                        ).get(timeout=20)
                        self.assertEqual(jobs.read(pending)["status"], "cancelled")
                        self.assertEqual(client.get(root + "/parser/jobs").status_code, 404)
                        self.assertEqual(client.get(legacy).status_code, 404)
                finally:
                    for process in processes:
                        stop(process)
                    for log in logs:
                        log.close()
                    import shutil

                    output = ROOT / ".tmp/p4-final/deployment"
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
