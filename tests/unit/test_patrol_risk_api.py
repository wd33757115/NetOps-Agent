from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from src.auth.models import CurrentUser
from src.gateway import patrol_api
from src.core.check_items.risk import RiskConflictError
from src.core.check_items.risk_association import (
    RiskAssociationConflictError,
)


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


def test_confirm_recovery_uses_server_candidate_and_audits(monkeypatch):
    calls: list[tuple[str, dict]] = []

    class FakeService:
        def confirm_recovery(self, risk_case_id, **kwargs):
            calls.append(
                (
                    "confirm_recovery",
                    {"risk_case_id": risk_case_id, **kwargs},
                )
            )
            return {
                "risk_case": {
                    "id": risk_case_id,
                    "status": "resolved",
                    "verification_status": "resolved",
                },
                "confirmation": {
                    "kind": "recovery_event",
                    "execution_id": "execution-2",
                    "assessment_id": "assessment-2",
                    "event_occurrence_id": "occurrence-2",
                },
            }

    def fake_audit(**kwargs):
        calls.append(("audit", kwargs))

    monkeypatch.setattr(patrol_api, "RiskCaseService", FakeService)
    monkeypatch.setattr(patrol_api, "write_audit_log", fake_audit)

    result = patrol_api.confirm_risk_recovery(
        "risk-1",
        patrol_api.RiskRecoveryConfirmRequest(
            recovery_candidate_id=(
                "recovery-candidate:v1:" + "a" * 64
            ),
            reason="已复核执行、评估和恢复事件，确认技术恢复",
        ),
        _request(),
        idempotency_key="confirm-recovery-0001",
        user=_user(),
    )

    assert result["risk_case"]["status"] == "resolved"
    assert calls[0][1]["actor"] == "engineer"
    assert calls[0][1]["idempotency_key"] == "confirm-recovery-0001"
    assert calls[1][1]["action"] == "risk_recovery_confirm"
    assert calls[1][1]["detail"]["event_occurrence_id"] == "occurrence-2"
    assert calls[1][1]["idempotency_key"] == (
        "risk:risk_recovery_confirm:risk-1:confirm-recovery-0001"
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


def test_typed_risk_state_conflict_returns_409():
    with pytest.raises(HTTPException) as exc_info:
        patrol_api._raise_risk_error(
            RiskConflictError(
                "no-remediation closure is invalid while remediation work exists"
            )
        )

    assert exc_info.value.status_code == 409
    assert "no-remediation closure" in str(exc_info.value.detail)


def test_risk_episode_association_proposal_and_review_are_audited(monkeypatch):
    calls: list[tuple[str, dict]] = []

    class FakeAssociationService:
        def propose(self, **kwargs):
            calls.append(("propose", kwargs))
            return {
                "id": "association-1",
                "primary_risk_case_id": kwargs["primary_risk_case_id"],
                "related_risk_case_id": kwargs["related_risk_case_id"],
                "status": "pending_review",
            }

        def review(self, association_id, **kwargs):
            calls.append(
                ("review", {"association_id": association_id, **kwargs})
            )
            return {
                "id": association_id,
                "primary_risk_case_id": "risk-primary",
                "related_risk_case_id": "risk-related",
                "status": kwargs["decision"],
            }

    def fake_audit(**kwargs):
        calls.append(("audit", kwargs))

    monkeypatch.setattr(
        patrol_api,
        "RiskEpisodeAssociationService",
        FakeAssociationService,
    )
    monkeypatch.setattr(patrol_api, "write_audit_log", fake_audit)

    proposed = patrol_api.propose_risk_episode_association(
        "risk-primary",
        patrol_api.RiskAssociationProposeRequest(
            related_risk_case_id="risk-related",
            relation_type="causal_symptom",
            reason="两项症状来自同一物理接口故障",
            evidence_refs=["event-occurrence:1", "event-occurrence:2"],
        ),
        _request(),
        idempotency_key="association-propose-0001",
        user=_user(),
    )
    reviewed = patrol_api.review_risk_episode_association(
        "association-1",
        patrol_api.RiskAssociationReviewRequest(
            decision="approved",
            reason="已独立核对设备、接口、时间和证据",
        ),
        _request(),
        idempotency_key="association-review-0001",
        user=_admin(),
    )

    assert proposed["association"]["status"] == "pending_review"
    assert reviewed["association"]["status"] == "approved"
    assert calls[0][1]["proposed_by"] == "engineer"
    assert calls[1][1]["action"] == "risk_episode_association_propose"
    assert calls[2][1]["reviewed_by"] == "admin"
    assert calls[3][1]["action"] == "risk_episode_association_review"


def test_risk_association_conflict_returns_409():
    with pytest.raises(HTTPException) as exc_info:
        patrol_api._raise_risk_error(
            RiskAssociationConflictError(
                "association would create multiple primary issues"
            )
        )

    assert exc_info.value.status_code == 409


def test_remediation_acceptance_and_risk_closure_are_independent_audits(
    monkeypatch,
):
    calls: list[tuple[str, dict]] = []

    class FakeService:
        def accept_remediation(
            self,
            risk_case_id,
            remediation_id,
            **kwargs,
        ):
            calls.append(
                (
                    "accept",
                    {
                        "risk_case_id": risk_case_id,
                        "remediation_id": remediation_id,
                        **kwargs,
                    },
                )
            )
            return {
                "id": remediation_id,
                "risk_case_id": risk_case_id,
                "status": "accepted",
            }

        def close_case(self, risk_case_id, **kwargs):
            calls.append(
                ("close", {"risk_case_id": risk_case_id, **kwargs})
            )
            return {
                "id": risk_case_id,
                "status": "resolved",
                "closed_by": kwargs["actor"],
            }

    def fake_audit(**kwargs):
        calls.append(("audit", kwargs))

    monkeypatch.setattr(patrol_api, "RiskCaseService", FakeService)
    monkeypatch.setattr(patrol_api, "write_audit_log", fake_audit)

    accepted = patrol_api.accept_risk_remediation(
        "risk-1",
        "remediation-1",
        patrol_api.RiskRemediationAcceptRequest(
            reason="独立核对整改结果",
            evidence_refs=["acceptance:review-1"],
        ),
        _request(),
        idempotency_key="accept-remediation-0001",
        user=_user(),
    )
    closed = patrol_api.close_risk_case(
        "risk-1",
        patrol_api.RiskClosureRequest(
            disposition="remediation_accepted",
            reason="技术恢复与整改验收均已完成",
            evidence_refs=["closure:approval-1"],
        ),
        _request(),
        idempotency_key="close-risk-0001",
        user=_admin(),
    )

    assert accepted["remediation"]["status"] == "accepted"
    assert closed["risk_case"]["closed_by"] == "admin"
    assert calls[0][1]["actor"] == "engineer"
    assert calls[1][1]["action"] == "risk_remediation_accept"
    assert calls[2][1]["disposition"] == "remediation_accepted"
    assert calls[3][1]["action"] == "risk_close"
    assert calls[3][1]["detail"]["disposition"] == "remediation_accepted"


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


def test_report_content_review_start_binds_actor_and_audits(monkeypatch):
    calls: list[tuple[str, dict]] = []

    class FakeManifestService:
        def get(self, report_manifest_id):
            assert report_manifest_id == "report-1"
            return {
                "id": "report-1",
                "patrol_batch_id": "batch-1",
                "status": "final",
            }

    class FakeContentReviewService:
        def start(self, report_manifest_id, *, created_by):
            calls.append(
                (
                    "start",
                    {
                        "report_manifest_id": report_manifest_id,
                        "created_by": created_by,
                    },
                )
            )
            return {
                "id": "content-review-1",
                "status": "in_review",
                "review_plan_digest": "a" * 64,
                "required_item_count": 17,
            }

    monkeypatch.setattr(
        patrol_api,
        "ReportManifestService",
        FakeManifestService,
    )
    monkeypatch.setattr(
        patrol_api,
        "ReportContentReviewService",
        FakeContentReviewService,
    )
    monkeypatch.setattr(
        patrol_api,
        "write_audit_log",
        lambda **kwargs: calls.append(("audit", kwargs)),
    )

    result = patrol_api.start_report_content_review(
        "batch-1",
        "report-1",
        _request(),
        idempotency_key="content-review-start-0001",
        user=_user(),
    )

    assert result["review"]["status"] == "in_review"
    assert calls[0][1] == {
        "report_manifest_id": "report-1",
        "created_by": "engineer",
    }
    assert calls[1][1]["action"] == "patrol_report_content_review_start"
    assert calls[1][1]["idempotency_key"] == (
        "patrol:report-content-review-start:report-1:"
        "content-review-start-0001"
    )


def test_report_content_machine_validation_failure_is_a_conflict():
    with pytest.raises(HTTPException) as raised:
        patrol_api._raise_report_error(
            ValueError(
                "report content machine validation failed: "
                "artifact_objects_verified"
            )
        )

    assert raised.value.status_code == 409
    assert "artifact_objects_verified" in str(raised.value.detail)


def test_report_content_review_decision_forwards_exact_items(monkeypatch):
    calls: list[tuple[str, dict]] = []

    class FakeManifestService:
        def get(self, _report_manifest_id):
            return {
                "id": "report-1",
                "patrol_batch_id": "batch-1",
                "status": "final",
            }

    class FakeContentReviewService:
        def decide_items(self, report_manifest_id, **kwargs):
            calls.append(
                (
                    "decision",
                    {
                        "report_manifest_id": report_manifest_id,
                        **kwargs,
                    },
                )
            )
            return {
                "id": "content-review-1",
                "status": "in_review",
                "accepted_item_count": 2,
            }

    monkeypatch.setattr(
        patrol_api,
        "ReportManifestService",
        FakeManifestService,
    )
    monkeypatch.setattr(
        patrol_api,
        "ReportContentReviewService",
        FakeContentReviewService,
    )
    monkeypatch.setattr(
        patrol_api,
        "write_audit_log",
        lambda **kwargs: calls.append(("audit", kwargs)),
    )

    result = patrol_api.decide_report_content_review_items(
        "batch-1",
        "report-1",
        patrol_api.ReportContentReviewDecisionRequest(
            review_item_ids=["item-1", "item-2"],
            decision="accepted",
            review_comment="Evidence and conclusion were checked.",
        ),
        _request(),
        idempotency_key="content-decision-0001",
        user=_user(),
    )

    assert result["review"]["accepted_item_count"] == 2
    assert calls[0][1]["review_item_ids"] == ["item-1", "item-2"]
    assert calls[0][1]["reviewed_by"] == "engineer"
    assert calls[1][1]["detail"]["decision"] == "accepted"


def test_report_content_acceptance_binds_artifact_hashes_and_audits(
    monkeypatch,
):
    calls: list[tuple[str, dict]] = []
    artifacts = {
        "markdown": "1" * 64,
        "docx": "2" * 64,
        "excel": "3" * 64,
        "json": "4" * 64,
        "evidence": "5" * 64,
    }
    confirmations = [
        "machine_reconciliation_reviewed",
        "critical_unknown_and_lifecycle_reviewed",
        "sampled_facts_reviewed",
        "traceability_verified",
        "narrative_readability_accepted",
        "actions_actionable",
    ]

    class FakeManifestService:
        def get(self, _report_manifest_id):
            return {
                "id": "report-1",
                "patrol_batch_id": "batch-1",
                "status": "final",
            }

    class FakeContentReviewService:
        def accept(self, report_manifest_id, **kwargs):
            calls.append(
                (
                    "accept",
                    {
                        "report_manifest_id": report_manifest_id,
                        **kwargs,
                    },
                )
            )
            return {
                "id": "content-review-1",
                "status": "accepted",
                "acceptance_digest": "a" * 64,
                "review_plan_digest": "b" * 64,
            }

    monkeypatch.setattr(
        patrol_api,
        "ReportManifestService",
        FakeManifestService,
    )
    monkeypatch.setattr(
        patrol_api,
        "ReportContentReviewService",
        FakeContentReviewService,
    )
    monkeypatch.setattr(
        patrol_api,
        "write_audit_log",
        lambda **kwargs: calls.append(("audit", kwargs)),
    )

    result = patrol_api.accept_report_content_review(
        "batch-1",
        "report-1",
        patrol_api.ReportContentAcceptanceRequest(
            acceptance_comment="The frozen report content is accepted.",
            confirmations=confirmations,
            verified_artifact_sha256=artifacts,
        ),
        _request(),
        idempotency_key="content-accept-0001",
        user=_user(),
    )

    assert result["review"]["status"] == "accepted"
    assert calls[0][1]["accepted_by"] == "engineer"
    assert calls[0][1]["confirmations"] == confirmations
    assert calls[0][1]["verified_artifact_sha256"] == artifacts
    assert calls[1][1]["detail"]["acceptance_digest"] == "a" * 64
    assert calls[1][1]["idempotency_key"] == (
        "patrol:report-content-acceptance:report-1:content-accept-0001"
    )


def test_report_version_list_keeps_released_superseded_manifest(
    monkeypatch,
):
    manifests = [
        {
            "id": "report-v2",
            "patrol_batch_id": "batch-1",
            "version": 2,
            "status": "final",
        },
        {
            "id": "report-v1",
            "patrol_batch_id": "batch-1",
            "version": 1,
            "status": "superseded",
        },
    ]

    class FakeManifestService:
        def list_for_batch(self, patrol_batch_id, *, limit, offset):
            assert patrol_batch_id == "batch-1"
            assert limit == 10
            assert offset == 10
            return {
                "total": 2,
                "items": manifests,
            }

    class FakeReleaseService:
        def list_for_batch(self, patrol_batch_id):
            assert patrol_batch_id == "batch-1"
            return [
                {
                    "report_manifest_id": "report-v1",
                    "status": "released",
                }
            ]

    class FakeContentReviewService:
        def list_for_manifest_ids(self, manifest_ids):
            assert manifest_ids == ["report-v2", "report-v1"]
            return {}

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
        "ReportContentReviewService",
        FakeContentReviewService,
    )
    monkeypatch.setattr(
        patrol_api,
        "_report_release_payload",
        lambda manifest, release, _content_review: {
            "report_manifest_id": manifest["id"],
            "version": manifest["version"],
            "facts_status": manifest["status"],
            "release_status": (
                release["status"] if release else "pending_signoff"
            ),
        },
    )

    result = patrol_api.list_batch_report_versions(
        "batch-1",
        page=2,
        page_size=10,
        _user=_user(),
    )

    assert result["total"] == 2
    assert result["page"] == 2
    assert result["items"] == [
        {
            "report_manifest_id": "report-v2",
            "version": 2,
            "facts_status": "final",
            "release_status": "pending_signoff",
        },
        {
            "report_manifest_id": "report-v1",
            "version": 1,
            "facts_status": "superseded",
            "release_status": "released",
        },
    ]


def test_report_materialization_is_queued_and_audited(
    monkeypatch,
):
    calls: list[dict] = []
    dispatched: list[dict] = []
    state = {
        "patrol_batch_id": "batch-1",
        "status": "queued",
        "requested_at": "2026-07-29T10:00:00+00:00",
        "completed_at": None,
        "error": None,
        "report_manifest_id": "report-previous",
        "report_manifest_digest": "a" * 64,
    }

    class FakeRepository:
        def report_generation_state(self, patrol_batch_id):
            assert patrol_batch_id == "batch-1"
            return dict(state)

        def request_terminal_report(
            self,
            patrol_batch_id,
            *,
            allow_regenerate,
            idempotency_key,
            task_id,
            requested_by,
        ):
            assert patrol_batch_id == "batch-1"
            assert allow_regenerate is True
            assert idempotency_key == "materialize-report-0001"
            assert requested_by == "api:engineer"
            state.update(
                {
                    "request_id": "report-request-1",
                    "idempotency_key": idempotency_key,
                    "task_id": task_id,
                    "requested_by": requested_by,
                }
            )
            return True

        def report_request_state(self, *_args, **_kwargs):
            return None

    monkeypatch.setattr(
        patrol_api,
        "PatrolBatchManifestRepository",
        FakeRepository,
    )
    monkeypatch.setattr(
        patrol_api.materialize_patrol_batch_report_task,
        "apply_async",
        lambda **kwargs: dispatched.append(kwargs),
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

    assert result["success"] is True
    assert result["accepted"] is True
    assert result["status"] == "queued"
    assert result["task_id"]
    assert dispatched == [
        {
            "args": [
                "batch-1",
                "api:engineer",
                "report-request-1",
            ],
            "queue": "netops.patrol",
            "task_id": result["task_id"],
        }
    ]
    assert calls[0]["action"] == "patrol_report_materialize_requested"
    assert calls[0]["resource_type"] == "patrol_batch"
    assert calls[0]["idempotency_key"] == (
        "patrol:report-materialize:batch-1:"
        "materialize-report-0001"
    )


def test_report_materialization_does_not_enqueue_duplicate(monkeypatch):
    class FakeRepository:
        def report_generation_state(self, _patrol_batch_id):
            return {
                "patrol_batch_id": "batch-1",
                "status": "generating",
                "requested_at": None,
                "completed_at": None,
                "error": None,
                "report_manifest_id": None,
                "report_manifest_digest": None,
            }

        def request_terminal_report(self, *_args, **_kwargs):
            return False

        def report_request_state(self, *_args, **_kwargs):
            return None

    monkeypatch.setattr(
        patrol_api,
        "PatrolBatchManifestRepository",
        FakeRepository,
    )
    result = patrol_api.materialize_report(
        "batch-1",
        _request(),
        idempotency_key="materialize-report-0002",
        user=_user(),
    )
    assert result["accepted"] is False
    assert result["reason"] == "already_in_progress"
    assert result["status"] == "generating"


def test_report_materialization_replays_completed_idempotency_key(
    monkeypatch,
):
    dispatched: list[dict] = []

    class FakeRepository:
        def report_generation_state(self, _patrol_batch_id):
            return {
                "patrol_batch_id": "batch-1",
                "status": "completed",
                "requested_at": "2026-07-29T10:00:00+00:00",
                "completed_at": "2026-07-29T10:01:00+00:00",
                "error": None,
                "report_manifest_id": "report-1",
                "report_manifest_digest": "a" * 64,
            }

        def request_terminal_report(self, *_args, **_kwargs):
            return False

        def report_request_state(
            self,
            patrol_batch_id,
            idempotency_key,
        ):
            assert patrol_batch_id == "batch-1"
            assert idempotency_key == "materialize-report-0003"
            return {
                "request_id": "report-request-1",
                "patrol_batch_id": "batch-1",
                "idempotency_key": idempotency_key,
                "task_id": "task-1",
                "status": "completed",
                "requested_by": "api:engineer",
                "requested_at": "2026-07-29T10:00:00+00:00",
                "completed_at": "2026-07-29T10:01:00+00:00",
                "error": None,
                "report_manifest_id": "report-1",
                "report_manifest_digest": "a" * 64,
            }

    monkeypatch.setattr(
        patrol_api,
        "PatrolBatchManifestRepository",
        FakeRepository,
    )
    monkeypatch.setattr(
        patrol_api.materialize_patrol_batch_report_task,
        "apply_async",
        lambda **kwargs: dispatched.append(kwargs),
    )

    result = patrol_api.materialize_report(
        "batch-1",
        _request(),
        idempotency_key="materialize-report-0003",
        user=_user(),
    )

    assert result["accepted"] is False
    assert result["reason"] == "idempotent_replay"
    assert result["status"] == "completed"
    assert result["task_id"] == "task-1"
    assert dispatched == []


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
