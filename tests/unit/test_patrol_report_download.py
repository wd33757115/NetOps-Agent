from __future__ import annotations

import pytest
from fastapi import HTTPException

from src.core.check_items.reporting import ReportArtifact
from src.apps.inspection.backend import batch_api as workflow_api


def test_released_report_download_uses_attached_artifact(monkeypatch):
    manifest = {
        "id": "report-1",
        "patrol_batch_id": "batch-1",
        "status": "final",
        "source_digest": "a" * 64,
        "artifact_sha256_json": {"docx": "b" * 64},
    }

    class FakeManifestService:
        def latest_for_batch(self, patrol_batch_id):
            assert patrol_batch_id == "batch-1"
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

    class FakeBatchRepository:
        def has_runtime_manifest(self, patrol_batch_id):
            assert patrol_batch_id == "batch-1"
            return True

    monkeypatch.setattr(
        workflow_api,
        "PatrolBatchManifestRepository",
        FakeBatchRepository,
    )
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
            filename="batch-1-patrol-report.docx",
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


def test_unsigned_materialized_report_download_uses_attached_artifact(
    monkeypatch,
):
    manifest = {
        "id": "report-1",
        "patrol_batch_id": "batch-1",
        "status": "final",
        "source_digest": "a" * 64,
        "artifact_object_keys_json": {
            "markdown": "reports/batch-1/report.md",
        },
        "artifact_sha256_json": {"markdown": "b" * 64},
    }

    class FakeManifestService:
        def latest_for_batch(self, patrol_batch_id):
            assert patrol_batch_id == "batch-1"
            return manifest

    class FakeReleaseService:
        def get_for_manifest(self, manifest_id):
            assert manifest_id == "report-1"
            return None

    class FakeBatchRepository:
        def has_runtime_manifest(self, patrol_batch_id):
            assert patrol_batch_id == "batch-1"
            return True

    monkeypatch.setattr(
        workflow_api,
        "PatrolBatchManifestRepository",
        FakeBatchRepository,
    )
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
            filename="batch-1-report.md",
            content_type="text/markdown",
            content=b"frozen preview bytes",
        ),
    )
    frozen_manifest, artifact = (
        workflow_api._build_check_item_report_artifact(
            "batch-1",
            "markdown",
        )
    )
    response = workflow_api._check_item_report_response(
        frozen_manifest,
        artifact,
    )

    assert response.body == b"frozen preview bytes"
    assert response.headers["x-netops-report-status"] == "pending_signoff"
    assert "batch-1-report-preview.md" in response.headers[
        "content-disposition"
    ]


def test_exact_superseded_manifest_can_be_downloaded_without_rebuilding(
    monkeypatch,
):
    manifest = {
        "id": "report-v1",
        "patrol_batch_id": "batch-1",
        "version": 1,
        "status": "superseded",
        "source_digest": "a" * 64,
        "artifact_object_keys_json": {
            "json": "reports/batch-1/v1/report.json",
        },
        "artifact_sha256_json": {"json": "b" * 64},
    }

    class FakeManifestService:
        def get(self, manifest_id):
            assert manifest_id == "report-v1"
            return manifest

    class FakeReleaseService:
        def get_for_manifest(self, manifest_id):
            assert manifest_id == "report-v1"
            return {
                "status": "released",
                "source_facts_digest": "a" * 64,
                "artifact_sha256_json": {"json": "b" * 64},
                "release_digest": "c" * 64,
            }

    class FakeBatchRepository:
        def has_runtime_manifest(self, patrol_batch_id):
            assert patrol_batch_id == "batch-1"
            return True

    monkeypatch.setattr(
        workflow_api,
        "PatrolBatchManifestRepository",
        FakeBatchRepository,
    )
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
            filename="batch-1-report.json",
            content_type="application/json",
            content=b'{"version":1}',
        ),
    )

    frozen_manifest, artifact = (
        workflow_api._build_check_item_report_artifact(
            "batch-1",
            "json",
            report_manifest_id="report-v1",
        )
    )
    response = workflow_api._check_item_report_response(
        frozen_manifest,
        artifact,
    )

    assert response.body == b'{"version":1}'
    assert response.headers["x-netops-report-status"] == "released"
    assert response.headers["x-netops-report-facts-status"] == "superseded"
    assert response.headers["x-netops-report-manifest-id"] == "report-v1"
    assert response.headers["x-netops-report-version"] == "1"


def test_download_rejects_unmaterialized_manifest(monkeypatch):
    class FakeManifestService:
        def latest_for_batch(self, patrol_batch_id):
            assert patrol_batch_id == "batch-1"
            return {
                "id": "report-1",
                "patrol_batch_id": "batch-1",
                "status": "final",
                "artifact_object_keys_json": {},
                "artifact_sha256_json": {},
            }

    class FakeBatchRepository:
        def has_runtime_manifest(self, _patrol_batch_id):
            return True

    class FakeReleaseService:
        def get_for_manifest(self, _manifest_id):
            return None

    monkeypatch.setattr(
        workflow_api,
        "PatrolBatchManifestRepository",
        FakeBatchRepository,
    )
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

    with pytest.raises(
        HTTPException,
        match="has not been materialized",
    ):
        workflow_api._build_check_item_report_artifact(
            "batch-1",
            "markdown",
        )
