"""Real HTTP BFF -> Gateway -> service -> Worker -> authorization round trip.

Runs against disposable databases. Only the external model and object store are
deterministic doubles; DOCX rendering, transactions and HTTP admission are real.
"""

import copy
import socket
import threading
import time
from contextlib import contextmanager
from io import BytesIO
from unittest.mock import patch

import httpx
import uvicorn
from django.contrib.auth import get_user_model
from django.test import LiveServerTestCase, override_settings
from docx import Document as WordDocument
from fastapi import FastAPI
from rest_framework_simplejwt.tokens import AccessToken
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from src.apps.official_documents.backend import api, operations
from src.apps.official_documents.backend.models import (
    OfficialDocument,
    OfficialDocumentOperation,
    OfficialDocumentVersion,
)
from src.apps.official_documents.backend.tasks import execute_document_operation
from src.infrastructure.db.models import Base, Conversation, Message
from src.platform.kernel.registry import discover, mount_apps
from src.platform.tools.registry import invoke
from .models import AppGrant, AppInstallation


@override_settings(ALLOWED_HOSTS=["localhost", "127.0.0.1", "testserver"])
class PackageRoundTripTests(LiveServerTestCase):
    def test_create_generate_apply_export_revoke(self):
        user = get_user_model().objects.create_user(username="package-roundtrip")
        manifest = discover()["official-documents"]
        app = AppInstallation.objects.create(
            app_id=manifest.id,
            installed_version=manifest.data["version"],
            deployment_digest=manifest.digest,
            state="enabled",
            desired_enabled=True,
        )
        grant = AppGrant.objects.create(app=app, user=user)
        engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        Base.metadata.create_all(
            engine,
            tables=[
                Conversation.__table__,
                Message.__table__,
                OfficialDocument.__table__,
                OfficialDocumentVersion.__table__,
                OfficialDocumentOperation.__table__,
            ],
        )

        @contextmanager
        def db():
            with Session(engine, expire_on_commit=False) as session:
                try:
                    yield session
                    session.commit()
                except Exception:
                    session.rollback()
                    raise

        def dependency():
            with db() as session:
                yield session

        async def generate(payload):
            content = copy.deepcopy(payload["content"])
            for block in content["blocks"]:
                if block["role"] == "title":
                    block["runs"] = [{"text": "能力包验证通知", "marks": []}]
            return {"kind": "proposal", "message": "请核对建议", "content": content}

        class Storage:
            files = {}

            def is_ready(self):
                return True

            def upload_file(self, key, data, content_type):
                self.files[key] = data
                return True

            def download_file(self, key):
                return self.files.get(key)

        gateway = FastAPI()
        mount_apps(gateway)
        gateway.dependency_overrides[api.session] = dependency
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(gateway, log_level="error", lifespan="off"))
        worker = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
        queued = []
        secret = "isolated-roundtrip-secret-for-tests-only"
        try:
            with (
                patch.dict(
                    "os.environ",
                    {
                        "NETOPS_AUTHORITY_URL": self.live_server_url,
                        "NETOPS_APP_INTERNAL_SECRET": secret,
                    },
                ),
                patch("bff.decorators.token_is_revoked", return_value=False),
                patch.object(operations, "db_session", db),
                patch.object(operations, "dispatch", queued.append),
                patch.object(operations, "generate_result", generate),
                patch(
                    "src.infrastructure.storage.minio_client.get_minio_storage",
                    return_value=Storage(),
                ),
                override_settings(FASTAPI_BASE_URL=f"http://127.0.0.1:{port}"),
            ):
                worker.start()
                deadline = time.monotonic() + 5
                while not server.started and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertTrue(server.started)
                token = str(AccessToken.for_user(user))
                with httpx.Client(
                    base_url=self.live_server_url,
                    headers={"Authorization": "Bearer " + token},
                    timeout=15,
                ) as client:
                    response = client.post(
                        "/api/apps/official-documents/",
                        json={"title": "初始通知", "request_id": "create"},
                    )
                    self.assertEqual(response.status_code, 201, response.text)
                    doc = response.json()
                    root = "/api/apps/official-documents/" + doc["id"]
                    response = client.post(
                        root + "/assistant/",
                        json={
                            "base_revision": 0,
                            "request_id": "generate",
                            "action": "generate",
                            "instruction": "写测试通知",
                        },
                    )
                    self.assertEqual(response.status_code, 202, response.text)
                    job = response.json()["id"]
                    self.assertEqual(queued, [job])
                    execute_document_operation.run(job)
                    result = client.get(root + f"/operations/{job}/").json()
                    self.assertEqual(result["status"], "proposed", result)
                    response = client.post(
                        root + f"/operations/{job}/apply/",
                        json={"base_revision": 0, "request_id": "apply"},
                    )
                    self.assertEqual(response.status_code, 200, response.text)
                    doc = response.json()
                    response = client.post(
                        root + "/exports/",
                        json={
                            "version": doc["current_version"],
                            "base_revision": doc["revision"],
                            "request_id": "export",
                        },
                    )
                    self.assertEqual(response.status_code, 202, response.text)
                    export_id = response.json()["id"]
                    execute_document_operation.run(export_id)
                    response = client.get(root + f"/exports/{export_id}/download/")
                    self.assertEqual(
                        response.status_code,
                        200,
                        response.text[:200] if response.status_code != 200 else "",
                    )
                    word = WordDocument(BytesIO(response.content))
                    self.assertIn("能力包验证通知", "\n".join(p.text for p in word.paragraphs))
                    self.assertIn("attachment;", response.headers["content-disposition"])
                    tool_doc = invoke(
                        "official_documents__read_document",
                        {"document_id": doc["id"]},
                        actor_id=str(user.pk),
                    )
                    self.assertEqual(tool_doc["id"], doc["id"])
                    grant.delete()
                    for suffix in (
                        "/",
                        f"/exports/{export_id}/download/",
                        f"/operations/{job}/events/",
                    ):
                        self.assertEqual(client.get(root + suffix).status_code, 403)
                    self.assertEqual(client.get("/api/official-documents/").status_code, 403)
        finally:
            server.should_exit = True
            if worker.is_alive():
                worker.join(timeout=5)
            sock.close()
            engine.dispose()
