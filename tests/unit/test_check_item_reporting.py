from __future__ import annotations

import hashlib
import io
import json
import zipfile
from copy import deepcopy
from datetime import datetime, timezone

import pytest
from openpyxl import load_workbook

from src.core.check_items.reporting import (
    CheckItemReportMaterializer,
    _normalize_zip_archive,
    build_check_item_report_view,
    load_attached_report_artifact,
    render_check_item_artifacts,
)


def _digest(value):
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()


def _manifest(*, assessment_status="healthy", event_count=0):
    facts = {
        "schema_version": "patrol-report-source/v2",
        "patrol_batch_id": "batch-report",
        "readiness": {
            "ready_for_final": True,
            "reason_codes": [],
            "target_count": 1,
            "target_record_count": 1,
            "execution_count": 1,
            "expected_execution_count": 1,
            "assessment_count": 1,
        },
        "target_manifest_digest": "1" * 64,
        "runtime_manifest_digest": "2" * 64,
        "target_facts": [
            {
                "device_id": "device-1",
                "device_name": "Core-SW-1",
                "management_ip": "192.0.2.10",
                "selection_status": "launched",
                "preflight_status": "ready",
                "dispatch_status": "completed",
            }
        ],
        "comparison_contexts": [
            {
                "id": "comparison-1",
                "digest": "3" * 64,
                "comparison_status": "first_observation",
                "baseline_run_id": None,
                "baseline_snapshot_ids": [],
            }
        ],
        "check_item_by_version": {
            "check-item:system.cpu_usage:v1": "system.cpu_usage"
        },
        "check_items": {
            "check-item:system.cpu_usage:v1": {
                "check_item_id": "system.cpu_usage",
                "version": 1,
                "name": "CPU utilization",
                "category": "system",
                "description": "CPU health",
                "result_mode": "structured",
                "assessment_mode": "health",
                "canonical_contract": {
                    "observations": [
                        {
                            "observation_type": (
                                "device.cpu.utilization"
                            )
                        }
                    ]
                },
                "report_projection": "cpu",
                "digest": "4" * 64,
            }
        },
        "executions": [
            {
                "id": "execution-1",
                "device_id": "device-1",
                "check_item_version_id": (
                    "check-item:system.cpu_usage:v1"
                ),
                "runtime_bundle_digest": "5" * 64,
                "comparison_context_id": "comparison-1",
                "execution_status": "completed",
                "reason_codes_json": [],
                "observation_count": 1,
                "observed_entity_count": 1,
                "change_count": 0,
                "event_count": event_count,
                "current_finding_count": 0,
                "evidence_qualification_id": "qualification-1",
                "coverage_decision_id": "coverage-1",
                "source_identity_status": "confirmed",
                "output_quality_status": "complete",
                "freshness_status": "fresh",
                "empty_result_classification": "not_empty",
                "coverage_status": "complete",
                "coverage_basis": "fixed",
                "risk_verification_status": "no_current_risk",
                "evidence_refs_json": ["snapshot-1"],
            }
        ],
        "assessments": [
            {
                "id": "assessment-1",
                "execution_id": "execution-1",
                "device_id": "device-1",
                "check_item_version_id": (
                    "check-item:system.cpu_usage:v1"
                ),
                "assessment_status": assessment_status,
                "completeness_status": (
                    "complete"
                    if assessment_status == "healthy"
                    else "insufficient"
                ),
                "comparison_status": "first_observation",
                "expected_entity_count": None,
                "observed_entity_count": 1,
                "evaluated_entity_count": (
                    1 if assessment_status == "healthy" else 0
                ),
                "threshold_unknown_count": 0,
                "current_finding_ids_json": [],
                "risk_case_ids_json": [],
                "evidence_qualification_id": "qualification-1",
                "coverage_decision_id": "coverage-1",
                "source_identity_status": "confirmed",
                "output_quality_status": "complete",
                "freshness_status": "fresh",
                "empty_result_classification": "not_empty",
                "coverage_status": "complete",
                "coverage_basis": "fixed",
                "risk_verification_status": "no_current_risk",
                "blocking_reason_codes_json": (
                    []
                    if assessment_status == "healthy"
                    else ["current_state_rules_not_ready"]
                ),
                "evidence_digest": "6" * 64,
            }
        ],
        "observations": [
            {
                "id": "observation-1",
                "execution_ids": ["execution-1"],
                "snapshot_id": "snapshot-1",
                "device_id": "device-1",
                "entity_type": "cpu_scope",
                "entity_key": '["device","0"]',
                "field": "cpu_usage",
                "observation_type": "device.cpu.utilization",
                "semantic_type": "gauge",
                "unit": "percent",
                "dimensions": {"window": "instant"},
                "value": 35,
                "value_number": 35,
                "value_type": "number",
                "observed_at": "2026-07-28T12:00:00+00:00",
                "parser_template_id": "parser-1",
                "parser_version": "1",
                "contract_version": 1,
            }
        ],
        "findings": [],
        "changes": [],
        "events": [],
        "risks": [],
        "risk_occurrences": [],
        "risk_remediations": [],
        "evidence_index": [
            {
                "snapshot_id": "snapshot-1",
                "run_id": "run-1",
                "device_id": "device-1",
                "device_name": "Core-SW-1",
                "management_ip": "192.0.2.10",
                "vendor": "Cisco",
                "model": "N9K",
                "observed_at": "2026-07-28T12:00:00+00:00",
                "canonical_command": "show processes cpu",
                "raw_text_hash": "7" * 64,
                "parse_status": "parsed",
                "output_status": "success",
                "parser_template_id": "parser-1",
                "parser_version": "1",
                "contract_version": 1,
                "collection_action_id": "action-1",
            }
        ],
        "evidence_refs": ["snapshot-1"],
    }
    return {
        "id": "report-manifest-1",
        "patrol_batch_id": "batch-report",
        "version": 1,
        "status": "final",
        "target_manifest_digest": "1" * 64,
        "runtime_manifest_digest": "2" * 64,
        "generator_version": "check-item-report/v2",
        "generated_at": datetime(
            2026,
            7,
            28,
            12,
            tzinfo=timezone.utc,
        ),
        "report_cutoff_at": datetime(
            2026,
            7,
            28,
            12,
            tzinfo=timezone.utc,
        ),
        "qualification_fact_digest": "8" * 64,
        "coverage_fact_digest": "9" * 64,
        "source_facts_json": facts,
        "source_digest": _digest(facts),
    }


def test_completed_execution_and_zero_events_do_not_override_unable_assessment():
    report = build_check_item_report_view(
        _manifest(
            assessment_status="unable_to_determine",
            event_count=0,
        )
    )

    assert report["conclusion"]["code"] == "partially_unknown"
    assert report["conclusion"]["formal"] is True
    assert "无法判断" in report["conclusion"]["text"]
    assert "未发现异常" not in report["conclusion"]["text"]


def test_healthy_conclusion_requires_explicit_complete_health_assessment():
    report = build_check_item_report_view(_manifest())

    assert report["conclusion"]["code"] == "healthy"
    assert report["conclusion"]["text"] == "本次覆盖范围内未发现异常"
    assert report["coverage"]["complete_check_count"] == 1


def test_all_not_applicable_checks_do_not_claim_network_health():
    manifest = _manifest()
    assessment = manifest["source_facts_json"]["assessments"][0]
    assessment["assessment_status"] = "not_applicable"
    assessment["completeness_status"] = "complete"
    manifest["source_digest"] = _digest(manifest["source_facts_json"])

    report = build_check_item_report_view(manifest)

    assert report["conclusion"]["code"] == "informational_only"
    assert "未发现异常" not in report["conclusion"]["text"]


def test_current_risk_does_not_hide_unable_to_determine_scope():
    manifest = _manifest(assessment_status="at_risk")
    facts = manifest["source_facts_json"]
    second_execution = deepcopy(facts["executions"][0])
    second_execution["id"] = "execution-2"
    second_execution["device_id"] = "device-2"
    second_assessment = deepcopy(facts["assessments"][0])
    second_assessment["id"] = "assessment-2"
    second_assessment["execution_id"] = "execution-2"
    second_assessment["device_id"] = "device-2"
    second_assessment["assessment_status"] = "unable_to_determine"
    second_assessment["completeness_status"] = "insufficient"
    second_assessment["blocking_reason_codes_json"] = [
        "parser_missing"
    ]
    facts["executions"].append(second_execution)
    facts["assessments"].append(second_assessment)
    facts["target_facts"].append(
        {
            "device_id": "device-2",
            "device_name": "Core-SW-2",
            "management_ip": "192.0.2.11",
        }
    )
    facts["readiness"].update(
        {
            "target_count": 2,
            "target_record_count": 2,
            "execution_count": 2,
            "expected_execution_count": 2,
            "assessment_count": 2,
        }
    )
    manifest["source_digest"] = _digest(facts)

    report = build_check_item_report_view(manifest)

    assert report["conclusion"]["code"] == "at_risk_with_unknown"
    assert "风险" in report["conclusion"]["text"]
    assert "无法判断" in report["conclusion"]["text"]


def test_report_separates_current_historical_and_recovery_pending_risks():
    manifest = _manifest(assessment_status="at_risk")
    facts = manifest["source_facts_json"]
    facts["executions"][0]["current_finding_count"] = 1
    facts["executions"][0]["risk_verification_status"] = (
        "confirmed_current"
    )
    facts["assessments"][0]["current_finding_ids_json"] = [
        "finding-current"
    ]
    facts["assessments"][0]["risk_case_ids_json"] = [
        "risk-current",
        "risk-history",
        "risk-recovery",
    ]
    facts["assessments"][0]["risk_verification_status"] = (
        "confirmed_current"
    )
    facts["findings"] = [
        {
            "id": "finding-current",
            "execution_id": "execution-1",
            "device_id": "device-1",
        }
    ]
    base_risk = {
        "check_item_id": "system.cpu_usage",
        "device_id": "device-1",
        "entity_type": "cpu_scope",
        "entity_key": '["device","0"]',
        "risk_type": "CpuHigh",
        "severity": "major",
        "status": "open",
        "first_seen_at": "2026-07-01T00:00:00+00:00",
        "last_seen_at": "2026-07-28T12:00:00+00:00",
        "remediation_status": "not_started",
        "source_event_ids_json": [],
    }
    facts["risks"] = [
        {
            **base_risk,
            "id": "risk-current",
            "verification_status": "confirmed_current",
            "last_verification_execution_id": "execution-1",
            "source_finding_ids_json": ["finding-current"],
        },
        {
            **base_risk,
            "id": "risk-history",
            "verification_status": "not_reverified",
            "last_verification_execution_id": "execution-old",
            "source_finding_ids_json": ["finding-old"],
        },
        {
            **base_risk,
            "id": "risk-recovery",
            "verification_status": "recovery_pending",
            "last_verification_execution_id": "execution-1",
            "source_finding_ids_json": ["finding-old"],
        },
    ]
    manifest["source_digest"] = _digest(facts)

    report = build_check_item_report_view(manifest)

    assert [item["id"] for item in report["risks"]] == [
        "risk-current"
    ]
    assert [
        item["id"]
        for item in report["historical_not_reverified_risks"]
    ] == ["risk-history"]
    assert [
        item["id"] for item in report["recovery_pending_risks"]
    ] == ["risk-recovery"]
    assert report["risk_verification_counts"] == {
        "confirmed_current": 1,
        "recovery_pending": 1,
        "not_reverified": 1,
        "resolved": 0,
    }


def test_all_report_artifacts_are_deterministic_and_share_one_fact_source():
    manifest = _manifest()
    first = render_check_item_artifacts(manifest)
    second = render_check_item_artifacts(manifest)

    assert set(first) == {
        "markdown",
        "docx",
        "excel",
        "json",
        "evidence",
    }
    assert {
        name: artifact.sha256 for name, artifact in first.items()
    } == {
        name: artifact.sha256 for name, artifact in second.items()
    }
    assert manifest["source_digest"].encode() in first["markdown"].content
    assert (
        json.loads(first["json"].content)["source_digest"]
        == manifest["source_digest"]
    )

    workbook = load_workbook(io.BytesIO(first["excel"].content))
    assert workbook.sheetnames == [
        "总览",
        "检查项结论",
        "当前风险",
        "历史风险待复核",
        "恢复待确认",
        "整改跟踪",
        "事件",
        "变化",
        "当前观测",
        "证据索引",
    ]
    assert workbook["总览"]["B11"].value == manifest["source_digest"]


def test_office_archive_normalization_removes_save_time_variance():
    def archive(modified_at: str) -> bytes:
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w") as output:
            output.writestr(
                "docProps/core.xml",
                (
                    "<cp:coreProperties>"
                    "<dcterms:created>2026-07-28T12:00:00Z"
                    "</dcterms:created>"
                    f"<dcterms:modified>{modified_at}</dcterms:modified>"
                    "</cp:coreProperties>"
                ),
            )
            output.writestr("xl/workbook.xml", "<workbook />")
        return stream.getvalue()

    first = _normalize_zip_archive(archive("2026-07-28T12:00:01Z"))
    second = _normalize_zip_archive(archive("2026-07-28T12:00:59Z"))

    assert first == second


def test_evidence_package_has_index_and_manifest_but_no_raw_cli_body():
    artifacts = render_check_item_artifacts(_manifest())
    with zipfile.ZipFile(io.BytesIO(artifacts["evidence"].content)) as archive:
        names = set(archive.namelist())
        combined = b"\n".join(
            archive.read(name)
            for name in names
        )

    assert {
        "README.txt",
        "report-manifest.json",
        "check-assessments.csv",
        "evidence-index.csv",
        "risk-lifecycle.csv",
    } == names
    assert b'"raw_text":' not in combined
    assert b"show secret" not in combined


def test_materializer_attaches_manifest_only_after_every_upload_succeeds():
    manifest = _manifest()

    class ManifestService:
        def __init__(self):
            self.attached = None

        def build(self, patrol_batch_id, *, generated_by):
            assert patrol_batch_id == "batch-report"
            assert generated_by == "test"
            return manifest

        def attach_artifacts(
            self,
            manifest_id,
            *,
            object_keys,
            sha256_by_artifact,
        ):
            self.attached = (
                manifest_id,
                dict(object_keys),
                dict(sha256_by_artifact),
            )
            return {
                **manifest,
                "artifact_object_keys_json": dict(object_keys),
                "artifact_sha256_json": dict(sha256_by_artifact),
            }

    class Storage:
        def __init__(self):
            self.uploads = []

        def upload_file(self, object_key, content, content_type):
            self.uploads.append(
                (object_key, bytes(content), content_type)
            )
            return True

    service = ManifestService()
    storage = Storage()
    result = CheckItemReportMaterializer(
        manifest_service=service,
        object_storage=storage,
    ).materialize("batch-report", generated_by="test")

    assert result["success"] is True
    assert len(storage.uploads) == 5
    assert service.attached is not None
    assert set(service.attached[1]) == {
        "markdown",
        "docx",
        "excel",
        "json",
        "evidence",
    }
    assert all(
        manifest["source_digest"] in object_key
        for object_key, _content, _content_type in storage.uploads
    )


def test_materializer_does_not_attach_partial_artifact_set():
    manifest = _manifest()

    class ManifestService:
        attached = False

        def build(self, _patrol_batch_id, *, generated_by):
            assert generated_by == "test"
            return manifest

        def attach_artifacts(self, *_args, **_kwargs):
            self.attached = True
            raise AssertionError("partial artifacts must not be attached")

    class Storage:
        calls = 0

        def upload_file(
            self,
            _object_key,
            _content,
            content_type=None,
        ):
            assert content_type
            self.calls += 1
            return self.calls < 3

    service = ManifestService()
    with pytest.raises(RuntimeError, match="failed to upload"):
        CheckItemReportMaterializer(
            manifest_service=service,
            object_storage=Storage(),
        ).materialize("batch-report", generated_by="test")

    assert service.attached is False


def test_attached_artifact_reader_verifies_manifest_digest():
    content = b"immutable report bytes"
    digest = hashlib.sha256(content).hexdigest()

    class Storage:
        def download_file(self, object_key):
            assert object_key == "reports/batch/report.docx"
            return content

    artifact = load_attached_report_artifact(
        {
            "artifact_object_keys_json": {
                "docx": "reports/batch/report.docx",
            },
            "artifact_sha256_json": {"docx": digest},
        },
        "docx",
        object_storage=Storage(),
    )

    assert artifact.content == content
    assert artifact.filename == "report.docx"
    assert artifact.sha256 == digest


def test_attached_artifact_reader_rejects_tampered_bytes():
    class Storage:
        def download_file(self, _object_key):
            return b"tampered"

    with pytest.raises(RuntimeError, match="digest mismatch"):
        load_attached_report_artifact(
            {
                "artifact_object_keys_json": {
                    "markdown": "reports/batch/report.md",
                },
                "artifact_sha256_json": {
                    "markdown": hashlib.sha256(b"expected").hexdigest(),
                },
            },
            "markdown",
            object_storage=Storage(),
        )
