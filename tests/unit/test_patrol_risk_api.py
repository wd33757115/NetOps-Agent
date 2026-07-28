from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from src.auth.models import CurrentUser
from src.gateway import patrol_api


def _user() -> CurrentUser:
    return CurrentUser(
        user_id="user:engineer",
        username="engineer",
        role="operator",
    )


def _admin() -> CurrentUser:
    return CurrentUser(
        user_id="user:admin",
        username="admin",
        role="admin",
    )


def _request():
    return SimpleNamespace(client=SimpleNamespace(host="127.0.0.1"))


def test_risk_case_list_forwards_server_side_filters(monkeypatch):
    captured: dict = {}

    class FakeService:
        def list_cases(self, **kwargs):
            captured.update(kwargs)
            return {
                "items": [],
                "page": kwargs["page"],
                "page_size": kwargs["page_size"],
                "total": 0,
                "page_count": 0,
                "summary": {},
            }

    monkeypatch.setattr(patrol_api, "RiskCaseService", FakeService)

    result = patrol_api.list_risk_cases(
        page=3,
        page_size=50,
        search="core-sw1",
        status="recovering",
        severity="major",
        remediation_status="completed",
        check_item_id="system.cpu_usage",
        device_id="device-1",
        owner="network-team",
        overdue_only=True,
        _user=_user(),
    )

    assert result["page"] == 3
    assert captured == {
        "page": 3,
        "page_size": 50,
        "search": "core-sw1",
        "status": "recovering",
        "severity": "major",
        "remediation_status": "completed",
        "check_item_id": "system.cpu_usage",
        "device_id": "device-1",
        "owner": "network-team",
        "overdue_only": True,
    }


def test_acknowledge_risk_uses_idempotency_key_and_audits(monkeypatch):
    calls: list[tuple[str, dict]] = []

    class FakeService:
        def acknowledge(self, risk_case_id, **kwargs):
            calls.append(
                ("acknowledge", {"risk_case_id": risk_case_id, **kwargs})
            )
            return {"id": risk_case_id, "status": "acknowledged"}

    def fake_audit(**kwargs):
        calls.append(("audit", kwargs))

    monkeypatch.setattr(patrol_api, "RiskCaseService", FakeService)
    monkeypatch.setattr(patrol_api, "write_audit_log", fake_audit)

    result = patrol_api.acknowledge_risk_case(
        "risk-1",
        patrol_api.RiskAcknowledgeRequest(reason="已核实并安排处理"),
        _request(),
        idempotency_key="ack-risk-0001",
        user=_user(),
    )

    assert result["risk_case"]["status"] == "acknowledged"
    assert calls[0][1]["actor"] == "engineer"
    assert calls[0][1]["idempotency_key"] == "ack-risk-0001"
    assert calls[1][1]["idempotency_key"] == (
        "risk:risk_acknowledge:risk-1:ack-risk-0001"
    )


def test_completed_remediation_conflict_is_not_hidden(monkeypatch):
    class FakeService:
        def update_remediation(self, *_args, **_kwargs):
            raise ValueError(
                "remediation cannot transition from completed to in_progress"
            )

    monkeypatch.setattr(patrol_api, "RiskCaseService", FakeService)

    with pytest.raises(HTTPException) as exc_info:
        patrol_api.update_risk_remediation(
            "risk-1",
            "remediation-1",
            patrol_api.RiskRemediationUpdateRequest(
                status="in_progress",
                reason="尝试非法回退状态",
            ),
            _request(),
            idempotency_key="update-risk-0001",
            user=_user(),
        )

    assert exc_info.value.status_code == 409
    assert "cannot transition" in str(exc_info.value.detail)


def test_missing_risk_detail_returns_404(monkeypatch):
    class FakeService:
        def get_case(self, *_args, **_kwargs):
            raise ValueError("risk case not found")

    monkeypatch.setattr(patrol_api, "RiskCaseService", FakeService)

    with pytest.raises(HTTPException) as exc_info:
        patrol_api.get_risk_case(
            "missing-risk",
            occurrence_page=1,
            occurrence_page_size=50,
            _user=_user(),
        )

    assert exc_info.value.status_code == 404


def test_report_release_binds_exact_digests_and_audits(monkeypatch):
    calls: list[tuple[str, dict]] = []
    manifest = {
        "id": "report-1",
        "patrol_batch_id": "batch-1",
        "status": "final",
        "source_digest": "a" * 64,
        "artifact_sha256_json": {"markdown": "b" * 64},
    }

    class FakeManifestService:
        def get(self, report_manifest_id):
            assert report_manifest_id == "report-1"
            return manifest

    class FakeReleaseService:
        def release(self, report_manifest_id, **kwargs):
            calls.append(
                (
                    "release",
                    {
                        "report_manifest_id": report_manifest_id,
                        **kwargs,
                    },
                )
            )
            return {
                "id": "release-1",
                "status": "released",
                "source_facts_digest": "a" * 64,
                "release_digest": "c" * 64,
                "artifact_sha256_json": {"markdown": "b" * 64},
            }

    def fake_audit(**kwargs):
        calls.append(("audit", kwargs))

    monkeypatch.setattr(
        patrol_api,
        "ReportManifestService",
        FakeManifestService,
    )
    monkeypatch.setattr(
        patrol_api,
        "ReportReleaseService",
        FakeReleaseService,
    )
    monkeypatch.setattr(
        patrol_api,
        "_report_release_payload",
        lambda _manifest, release: {
            "release_status": release["status"],
            "release": release,
        },
    )
    monkeypatch.setattr(patrol_api, "write_audit_log", fake_audit)

    result = patrol_api.release_report(
        "batch-1",
        "report-1",
        patrol_api.ReportReleaseRequest(
            signoff_comment="同意作为本批次正式巡检报告交付",
            disclosure_confirmations=[
                "facts_reviewed",
                "unknown_scope_disclosed",
                "artifact_digests_verified",
            ],
            verified_artifact_sha256={"markdown": "b" * 64},
        ),
        _request(),
        idempotency_key="release-report-0001",
        user=_admin(),
    )

    assert result["release_status"] == "released"
    assert calls[0][1]["signed_by"] == "admin"
    assert calls[0][1]["verified_artifact_sha256"] == {
        "markdown": "b" * 64
    }
    assert calls[1][1]["resource_type"] == "report_release"
    assert calls[1][1]["idempotency_key"] == (
        "patrol:report-release:report-1:release-report-0001"
    )


def test_report_materialization_is_audited_and_returns_signoff_state(
    monkeypatch,
):
    calls: list[dict] = []
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

    class FakeMaterializer:
        def materialize(self, patrol_batch_id, *, generated_by):
            assert patrol_batch_id == "batch-1"
            assert generated_by == "api:engineer"
            return {"manifest": manifest}

    class FakeReleaseService:
        def get_for_manifest(self, manifest_id):
            assert manifest_id == "report-1"
            return None

    monkeypatch.setattr(
        patrol_api,
        "CheckItemReportMaterializer",
        FakeMaterializer,
    )
    monkeypatch.setattr(
        patrol_api,
        "ReportReleaseService",
        FakeReleaseService,
    )
    monkeypatch.setattr(
        patrol_api,
        "_report_release_payload",
        lambda _manifest, _release: {
            "release_status": "pending_signoff",
        },
    )
    monkeypatch.setattr(
        patrol_api,
        "write_audit_log",
        lambda **kwargs: calls.append(kwargs),
    )

    result = patrol_api.materialize_report(
        "batch-1",
        _request(),
        idempotency_key="materialize-report-0001",
        user=_user(),
    )

    assert result == {
        "success": True,
        "release_status": "pending_signoff",
    }
    assert calls[0]["resource_type"] == "report_manifest"
    assert calls[0]["idempotency_key"] == (
        "patrol:report-materialize:batch-1:"
        "materialize-report-0001"
    )


def test_report_release_rejects_batch_identity_mismatch(monkeypatch):
    class FakeManifestService:
        def get(self, _report_manifest_id):
            return {
                "id": "report-1",
                "patrol_batch_id": "another-batch",
                "status": "final",
            }

    monkeypatch.setattr(
        patrol_api,
        "ReportManifestService",
        FakeManifestService,
    )

    with pytest.raises(HTTPException) as exc_info:
        patrol_api.release_report(
            "batch-1",
            "report-1",
            patrol_api.ReportReleaseRequest(
                signoff_comment="同意交付",
                disclosure_confirmations=[
                    "facts_reviewed",
                    "unknown_scope_disclosed",
                    "artifact_digests_verified",
                ],
                verified_artifact_sha256={"markdown": "b" * 64},
            ),
            _request(),
            idempotency_key="release-report-0002",
            user=_admin(),
        )

    assert exc_info.value.status_code == 409
