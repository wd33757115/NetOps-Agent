from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace

from src.core.check_items.reporting import ReportArtifact
from src.gateway import workflow_api
from src.infrastructure.db import postgres


def test_released_report_download_uses_attached_artifact(monkeypatch):
    manifest = {
        "id": "report-1",
        "patrol_batch_id": "batch-1",
        "status": "final",
        "source_digest": "a" * 64,
        "artifact_sha256_json": {"docx": "b" * 64},
    }

    @contextmanager
    def fake_session():
        yield SimpleNamespace(
            get=lambda _model, _identity: SimpleNamespace(
                runtime_manifest_id="runtime-1"
            )
        )

    class FakeManifestService:
        def build(self, patrol_batch_id, *, generated_by):
            assert patrol_batch_id == "batch-1"
            assert generated_by == "api:patrol-report-download"
            return manifest

    class FakeReleaseService:
        def get_for_manifest(self, manifest_id):
            assert manifest_id == "report-1"
            return {
                "status": "released",
                "source_facts_digest": "a" * 64,
                "artifact_sha256_json": {"docx": "b" * 64},
                "release_digest": "c" * 64,
            }

    monkeypatch.setattr(postgres, "get_db_session", fake_session)
    monkeypatch.setattr(
        workflow_api,
        "ReportManifestService",
        FakeManifestService,
    )
    monkeypatch.setattr(
        workflow_api,
        "ReportReleaseService",
        FakeReleaseService,
    )
    monkeypatch.setattr(
        workflow_api,
        "load_attached_report_artifact",
        lambda _manifest, name: ReportArtifact(
            name=name,
            filename="batch-1-leadership-report.docx",
            content_type="application/docx",
            content=b"signed bytes",
        ),
    )

    frozen_manifest, artifact = (
        workflow_api._build_check_item_report_artifact(
            "batch-1",
            "docx",
        )
    )
    response = workflow_api._check_item_report_response(
        frozen_manifest,
        artifact,
    )

    assert response.body == b"signed bytes"
    assert response.headers["x-netops-report-status"] == "released"
    assert response.headers["x-netops-report-release-digest"] == "c" * 64
    assert "preview" not in response.headers["content-disposition"]


def test_unsigned_final_report_is_labelled_preview():
    response = workflow_api._check_item_report_response(
        {
            "status": "final",
            "source_digest": "a" * 64,
        },
        ReportArtifact(
            name="markdown",
            filename="batch-1-report.md",
            content_type="text/markdown",
            content=b"preview",
        ),
    )

    assert response.headers["x-netops-report-status"] == "pending_signoff"
    assert "batch-1-report-preview.md" in response.headers[
        "content-disposition"
    ]
