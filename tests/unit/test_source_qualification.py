from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone

import pytest

from src.core.check_items.report_contract import (
    build_report_authority_context,
    report_authority_context_is_valid,
)
from src.core.check_items.source_qualification import (
    build_attested_offline_current_qualification,
    source_qualification_is_valid,
)


def _qualification() -> dict:
    return build_attested_offline_current_qualification(
        qualification_id="qualification-1",
        patrol_batch_id="batch-offline-current",
        offline_job_id="offline-job-1",
        version=1,
        observed_at_min="2026-08-01T08:00:00+00:00",
        observed_at_max="2026-08-01T08:05:00+00:00",
        source_file_manifest=[
            {
                "source_id": "source-1",
                "period_role": "current",
                "relative_path": "device-1.txt",
                "sha256": "a" * 64,
                "size_bytes": 1024,
                "status": "verified",
            }
        ],
        identity_bindings=[
            {
                "external_device_id": "device-1",
                "inventory_device_id": "inventory-device-1",
                "match_status": "confirmed",
                "confirmed_by": "identity-reviewer",
            }
        ],
        scope_contract_digest="b" * 64,
        intended_target_matrix_digest="c" * 64,
        completeness_statement=(
            "Collector exported the complete configured command set."
        ),
        completeness_evidence_refs=["evidence://collection-log/1"],
        collection_tool="netops-offline-collector/2.0",
        collector_identity="engineer-a",
        imported_by="importer-a",
        attested_by="reviewer-b",
        attested_at=datetime(2026, 8, 1, 9, tzinfo=timezone.utc),
    )


def test_attested_offline_current_can_receive_current_authority() -> None:
    qualification = _qualification()

    context = build_report_authority_context(
        report_purpose="production_current",
        patrol_mode="production",
        launch_mode="workflow",
        workflow_name="patrol-offline-current",
        target_type="offline_import",
        readiness_ready=True,
        target_manifest_digest="d" * 64,
        inventory_snapshot_digest="e" * 64,
        runtime_manifest_digest="f" * 64,
        source_qualification=qualification,
        scope_contract_digest="b" * 64,
        intended_target_matrix_digest="c" * 64,
        patrol_batch_id="batch-offline-current",
    )

    assert source_qualification_is_valid(qualification)
    assert context["base_source_kind"] == "offline_import"
    assert context["source_kind"] == "attested_offline_current"
    assert context["source_qualification_valid"] is True
    assert context["current_state_eligible"] is True
    assert report_authority_context_is_valid(
        context,
        expected_purpose="production_current",
    )


def test_offline_current_qualification_cannot_be_transplanted() -> None:
    qualification = _qualification()

    context = build_report_authority_context(
        report_purpose="production_current",
        patrol_mode="production",
        launch_mode="workflow",
        workflow_name="patrol-offline-current",
        target_type="offline_import",
        readiness_ready=True,
        target_manifest_digest="d" * 64,
        inventory_snapshot_digest="e" * 64,
        runtime_manifest_digest="f" * 64,
        source_qualification=qualification,
        scope_contract_digest="b" * 64,
        intended_target_matrix_digest="c" * 64,
        patrol_batch_id="another-batch",
    )

    assert context["source_kind"] == "offline_import"
    assert context["source_qualification_valid"] is False
    assert context["current_state_eligible"] is False
    assert (
        "source_qualification_missing_or_invalid"
        in context["reason_codes"]
    )


def test_source_qualification_rejects_tampering_and_self_attestation() -> None:
    qualification = _qualification()
    tampered = deepcopy(qualification)
    tampered["completeness_evidence"]["statement"] = "edited"

    assert not source_qualification_is_valid(tampered)
    with pytest.raises(ValueError, match="independent attester"):
        build_attested_offline_current_qualification(
            qualification_id="qualification-2",
            patrol_batch_id="batch-2",
            offline_job_id="offline-job-2",
            version=1,
            observed_at_min="2026-08-01T08:00:00+00:00",
            observed_at_max="2026-08-01T08:00:00+00:00",
            source_file_manifest=[
                {
                    "relative_path": "device.txt",
                    "sha256": "a" * 64,
                    "status": "verified",
                }
            ],
            identity_bindings=[
                {
                    "external_device_id": "device",
                    "inventory_device_id": "inventory-device",
                    "match_status": "confirmed",
                    "confirmed_by": "reviewer",
                }
            ],
            scope_contract_digest="b" * 64,
            intended_target_matrix_digest="c" * 64,
            completeness_statement="Complete command set was exported.",
            completeness_evidence_refs=["evidence://1"],
            collection_tool="collector",
            collector_identity="same-user",
            imported_by="same-user",
            attested_by="same-user",
            attested_at="2026-08-01T09:00:00+00:00",
        )
