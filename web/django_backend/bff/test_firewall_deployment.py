"""Opt-in firewall delivery: real Worker/Beat, BFF/Gateway, MinIO and artifacts.

Set TEST_PACKAGE_BROKER_URL to a disposable Redis instance, never a production
broker. This test does not purge queues or touch normal business databases.
"""

import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import skipUnless
from unittest.mock import patch

import httpx
import uvicorn
from celery import Celery
from django.contrib.auth import get_user_model
from django.test import LiveServerTestCase, override_settings
from fastapi import FastAPI
from rest_framework_simplejwt.tokens import AccessToken

from src.apps.firewall_policy.backend import service
from src.platform.kernel.migrations import configured_engine
from src.platform.kernel.readiness import probe
from src.platform.kernel.registry import ROOT, mount_apps
from src.platform.kernel.runtime import snapshot
from src.platform.tools.registry import invoke

from .app_lifecycle import operate
from .models import AppGrant


def until(check, timeout=30):
    deadline = time.monotonic() + timeout
    last_error = None
    while time.monotonic() < deadline:
        try:
            value = check()
            if value:
                return value
        except Exception as exc:
            last_error = exc
        time.sleep(0.3)
    raise AssertionError(f"Condition did not become ready: {last_error}")


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


@skipUnless(
    os.getenv("TEST_PACKAGE_BROKER_URL"), "Requires explicitly isolated broker and local MinIO"
)
@override_settings(ALLOWED_HOSTS=["127.0.0.1", "localhost", "testserver"])
class FirewallDeploymentTests(LiveServerTestCase):
    def test_real_policy_ticket_download_revocation_and_drain(self):
        broker = os.environ["TEST_PACKAGE_BROKER_URL"]
        with tempfile.TemporaryDirectory(prefix="netops-p3-") as directory:
            path = Path(directory)
            environment = {
                "NETOPS_PACKAGE_DATABASE_URL": "sqlite:///"
                + (path / "business.sqlite3").as_posix(),
                "NETOPS_AUTHORITY_URL": self.live_server_url,
                "NETOPS_APP_INTERNAL_SECRET": "isolated-package-deployment-test-secret",
                "CELERY_BROKER_URL": broker,
                "CELERY_RESULT_BACKEND": broker,
                "CELERY_WORKER_ROLE": "packages",
            }
            with patch.dict(os.environ, environment):
                snapshot.cache_clear()
                user = get_user_model().objects.create_user(username="package-deployment")
                record = operate("install", "firewall-policy")
                grant = AppGrant.objects.create(app=record, user=user)
                gateway = FastAPI()
                mount_apps(gateway)
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
                client_app = Celery("delivery-test", broker=broker, backend=broker)
                logs = []
                processes = []
                gateway_thread.start()
                frontend_thread.start()
                frontend_url = f"http://127.0.0.1:{frontend.server_port}"
                try:
                    until(lambda: server.started)
                    with self.assertRaises(ValueError):
                        operate(
                            "enable",
                            "firewall-policy",
                            gateway_urls=[gateway_url],
                            frontend_url=frontend_url,
                        )
                    for role, args in [
                        (
                            "worker",
                            [
                                "worker",
                                "-Q",
                                "app.firewall-policy",
                                "-P",
                                "threads",
                                "--concurrency=2",
                                "--hostname=p3-validation@%h",
                                "--loglevel=warning",
                            ],
                        ),
                        (
                            "beat",
                            [
                                "beat",
                                "--schedule",
                                str(path / "beat-schedule"),
                                "--loglevel=warning",
                            ],
                        ),
                    ]:
                        log = (path / f"{role}.log").open("w", encoding="utf-8")
                        logs.append(log)
                        child_env = os.environ.copy()
                        child_env.pop("DJANGO_SETTINGS_MODULE", None)
                        processes.append(
                            subprocess.Popen(
                                [
                                    sys.executable,
                                    "-m",
                                    "celery",
                                    "-A",
                                    "src.platform.kernel.package_celery:app",
                                    *args,
                                ],
                                cwd=ROOT,
                                env=child_env,
                                stdout=log,
                                stderr=log,
                                creationflags=subprocess.CREATE_NO_WINDOW
                                if sys.platform == "win32"
                                else 0,
                            )
                        )

                    def ready():
                        from src.platform.kernel.registry import discover
                        from src.platform.kernel.scheduler import beat_reports

                        if not client_app.control.inspect(timeout=1).ping() or not beat_reports(
                            client_app
                        ):
                            return False
                        return probe(
                            discover()["firewall-policy"], [gateway_url], frontend_url, client_app
                        )

                    until(ready, timeout=45)
                    operate(
                        "enable",
                        "firewall-policy",
                        gateway_urls=[gateway_url],
                        frontend_url=frontend_url,
                    )
                    with (
                        override_settings(FASTAPI_BASE_URL=gateway_url),
                        patch("bff.decorators.token_is_revoked", return_value=False),
                        patch(
                            "src.apps.firewall_policy.backend.tasks.execute",
                            client_app.signature(
                                "app.firewall_policy.execute", queue="app.firewall-policy"
                            ),
                        ),
                    ):
                        with httpx.Client(
                            base_url=self.live_server_url,
                            headers={"Authorization": "Bearer " + str(AccessToken.for_user(user))},
                            timeout=15,
                            follow_redirects=True,
                        ) as client:
                            import io
                            import json

                            import pandas as pd
                            from sqlalchemy import select

                            from src.apps.firewall_policy.backend.models import Run, Upload
                            from src.apps.firewall_policy.backend.storage import storage

                            self.assertTrue(storage().is_ready())
                            buffer = io.BytesIO()
                            pd.DataFrame(
                                [["10.0.0.1", "10.1.0.1", "443", "tcp", "permit"]],
                                columns=["src_ip", "dst_ip", "port", "proto", "action"],
                            ).to_excel(buffer, index=False)
                            # Exercise Django raw bodies above its old 2.5 MiB limit.
                            # ZIP allows trailing data only within its end-record search window;
                            # use a padded JSON topology for the large-upload boundary instead.
                            topology = json.dumps(
                                {
                                    "firewalls": [
                                        {
                                            "name": "TEST-FW",
                                            "type": "华为",
                                            "security_domains": [
                                                {"name": "Trust", "ip_ranges": ["10.0.0.0/24"]},
                                                {"name": "Untrust", "ip_ranges": ["10.1.0.0/24"]},
                                            ],
                                        }
                                    ]
                                }
                            ).encode() + b" " * (3 * 1024 * 1024)

                            def upload_file(name, kind, data):
                                response = client.post(
                                    "/api/apps/firewall-policy/uploads",
                                    params={"filename": name, "kind": kind, "request_id": kind},
                                    content=data,
                                    headers={"Content-Type": "application/octet-stream"},
                                )
                                self.assertEqual(response.status_code, 201, response.text)
                                return response.json()["id"]

                            policy_id = upload_file("policy.xlsx", "policy", buffer.getvalue())
                            topology_id = upload_file("topology.json", "topology", topology)
                            request = {
                                "request_id": "real-broker",
                                "policy_upload_id": policy_id,
                                "topology_upload_id": topology_id,
                                "ticket_id": "CHG-INTEGRATION",
                            }
                            response = client.post("/api/apps/firewall-policy/runs", json=request)
                            self.assertEqual(response.status_code, 202, response.text)
                            run_id = response.json()["id"]
                            root = "/api/apps/firewall-policy/runs/" + run_id
                            until(
                                lambda: client.get(root).json().get("status") == "completed",
                                timeout=45,
                            )
                            result = client.get(root).json()
                            self.assertEqual(
                                invoke(
                                    "firewall_policy__read_run",
                                    {"run_id": run_id},
                                    actor_id=str(user.pk),
                                )["status"],
                                "completed",
                            )
                            url = result["artifacts"]["config_zip"]["download_url"]
                            self.assertEqual(client.get(url).content[:2], b"PK")
                            response = client.post(
                                root + "/tickets",
                                json={
                                    "request_id": "ticket",
                                    "fields": {"ticket_title": "真实工单"},
                                },
                            )
                            self.assertEqual(response.status_code, 202, response.text)
                            ticket_id = response.json()["id"]
                            until(
                                lambda: (
                                    client.get("/api/apps/firewall-policy/runs/" + ticket_id)
                                    .json()
                                    .get("status")
                                    == "completed"
                                )
                            )
                            ticket = client.get(
                                "/api/apps/firewall-policy/runs/" + ticket_id
                            ).json()
                            self.assertEqual(
                                ticket["result"]["policy_plan_id"],
                                result["result"]["policy_plan"]["plan_id"],
                            )
                            self.assertEqual(
                                client.get(
                                    ticket["artifacts"]["change_excel"]["download_url"]
                                ).content[:2],
                                b"PK",
                            )
                            grant.delete()
                            self.assertEqual(client.get(url).status_code, 403)
                            self.assertEqual(
                                client.post(
                                    root + "/tickets", json={"request_id": "revoked"}
                                ).status_code,
                                403,
                            )
                            grant = AppGrant.objects.create(app=record, user=user)
                            # Stop consumption, persist queued work, then close admission and drain.
                            client_app.control.cancel_consumer(
                                "app.firewall-policy", reply=True, timeout=3
                            )
                            response = client.post(
                                "/api/apps/firewall-policy/runs",
                                json={**request, "request_id": "drain"},
                            )
                            self.assertEqual(response.status_code, 202, response.text)
                            stopped = operate("disable", "firewall-policy")
                            self.assertEqual(stopped.state, "disabled")
                            self.assertEqual(service.pending(), 0)
                            self.assertEqual(
                                client.get("/api/apps/firewall-policy/runs/" + run_id).status_code,
                                404,
                            )
                            grant.delete()
                finally:
                    for process in processes:
                        process.terminate()
                    for process in processes:
                        try:
                            process.wait(timeout=10)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait(timeout=5)
                    for log in logs:
                        log.close()
                    # Keep failure logs outside the temporary DB directory for diagnosis.
                    import shutil

                    output = ROOT / ".tmp/p3-deployment"
                    output.mkdir(exist_ok=True)
                    for log in path.glob("*.log"):
                        shutil.copy2(log, output / log.name)
                    server.should_exit = True
                    gateway_thread.join(timeout=5)
                    sock.close()
                    frontend.shutdown()
                    frontend.server_close()
                    frontend_thread.join(timeout=5)
                    client_app.close()
                    from sqlalchemy import select

                    from src.apps.firewall_policy.backend.models import Run, Upload
                    from src.apps.firewall_policy.backend.storage import PREFIX, storage

                    with service.sessions() as session:
                        keys = [row.object_key for row in session.scalars(select(Upload))]
                        for row in session.scalars(select(Run)):
                            keys.extend(
                                item["object_key"] for item in (row.artifacts_json or {}).values()
                            )
                    for key in keys:
                        assert key.startswith(PREFIX)
                        storage().delete_object(key)
                    configured_engine(environment["NETOPS_PACKAGE_DATABASE_URL"]).dispose()
                    configured_engine.cache_clear()
                    snapshot.cache_clear()
