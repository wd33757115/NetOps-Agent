from __future__ import annotations

import hashlib
import io
import json
import zipfile
from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from docx import Document
from openpyxl import load_workbook

from src.core.check_items.report_claims import (
    build_report_claim_ledger,
    validate_report_claim_ledger,
)
from src.core.check_items.report_contract import (
    build_report_authority_context,
    build_report_conclusion_permission_from_source_facts,
    raw_evidence_set_digest,
    report_authority_context_is_valid,
)
from src.core.check_items.report_manifest import (
    _collection_resilience_facts,
    _event_fact,
)
from src.core.check_items.report_projection import (
    build_report_projection_snapshot,
)
from src.core.check_items.reporting import (
    CheckItemReportMaterializer,
    _normalize_zip_archive,
    build_check_item_report_view,
    load_attached_report_artifact,
    render_check_item_artifacts,
    render_check_item_markdown,
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


def _manifest(
    *,
    assessment_status="healthy",
    event_count=0,
    report_purpose="production_current",
):
    facts = {
        "schema_version": "patrol-report-source/v3",
        "patrol_batch_id": "batch-report",
        "report_purpose": report_purpose,
        "report_authority_context": build_report_authority_context(
            report_purpose=report_purpose,
            patrol_mode="production",
            launch_mode="workflow",
            workflow_name="patrol-change-event",
            target_type=("devices" if report_purpose == "production_current" else "offline_import"),
            readiness_ready=True,
            target_manifest_digest="1" * 64,
            inventory_snapshot_digest="a" * 64,
            runtime_manifest_digest="2" * 64,
        ),
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
        "inventory_snapshot_digest": "a" * 64,
        "runtime_manifest_digest": "2" * 64,
        "collection_resilience": [
            {
                "device_id": "device-1",
                "check_item_id": "system.cpu_usage",
                "check_item_version_id": (
                    "check-item:system.cpu_usage:v1"
                ),
                "decision_status": "resolved",
                "fallback_resilience": "reduced",
                "declared_command_count": 2,
                "executable_command_count": 1,
                "selected_commands": [
                    {
                        "collection_action_id": "action-cpu-fallback",
                        "command": "display cpu",
                        "canonical_command": "display cpu",
                        "fallback_rank": 1,
                    }
                ],
                "unavailable_commands": [
                    {
                        "command": "display cpu-usage",
                        "canonical_command": "display cpu-usage",
                        "fallback_rank": 0,
                        "reason_code": (
                            "asset_unavailable_at_plan_time"
                        ),
                    }
                ],
            }
        ],
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
        "check_item_by_version": {"check-item:system.cpu_usage:v1": "system.cpu_usage"},
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
                    "observations": [{"observation_type": ("device.cpu.utilization")}]
                },
                "applicability": {
                    "version": 1,
                    "conditions": [],
                },
                "report_projection": "cpu",
                "digest": "4" * 64,
            }
        },
        "executions": [
            {
                "id": "execution-1",
                "device_id": "device-1",
                "check_item_version_id": ("check-item:system.cpu_usage:v1"),
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
                "check_item_version_id": ("check-item:system.cpu_usage:v1"),
                "assessment_status": assessment_status,
                "completeness_status": (
                    "complete" if assessment_status == "healthy" else "insufficient"
                ),
                "comparison_status": "first_observation",
                "expected_entity_count": None,
                "observed_entity_count": 1,
                "evaluated_entity_count": (1 if assessment_status == "healthy" else 0),
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
                    [] if assessment_status == "healthy" else ["current_state_rules_not_ready"]
                ),
                "evidence_digest": "6" * 64,
            }
        ],
        "evidence_qualifications": [
            {
                "id": "qualification-1",
                "digest": "8" * 64,
                "check_execution_id": "execution-1",
                "device_id": "device-1",
                "check_item_version_id": (
                    "check-item:system.cpu_usage:v1"
                ),
                "snapshot_ids_json": ["snapshot-1"],
            }
        ],
        "coverage_decisions": [
            {
                "id": "coverage-1",
                "digest": "9" * 64,
                "check_execution_id": "execution-1",
                "device_id": "device-1",
                "check_item_version_id": (
                    "check-item:system.cpu_usage:v1"
                ),
                "coverage_status": "complete",
                "coverage_basis": "fixed",
                "expected_entity_count": 1,
                "observed_entity_count": 1,
                "evaluated_entity_count": 1,
                "denominator_ref_json": {
                    "model": "fixed",
                    "expected_count": 1,
                },
                "missing_entity_sample_json": [],
                "unexpected_entity_sample_json": [],
                "reason_codes_json": [],
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
    frozen_raw_evidence_set_digest = raw_evidence_set_digest(facts["evidence_index"])
    facts["raw_evidence_set_digest"] = frozen_raw_evidence_set_digest
    return {
        "id": "report-manifest-1",
        "patrol_batch_id": "batch-report",
        "version": 1,
        "schema_version": "patrol-report-manifest/v3",
        "status": "final",
        "report_purpose": report_purpose,
        "raw_evidence_set_digest": (frozen_raw_evidence_set_digest),
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


def _freeze_v4(
    manifest: dict[str, object],
) -> dict[str, object]:
    facts = manifest["source_facts_json"]
    facts["schema_version"] = "patrol-report-source/v4"
    facts["target_pairs"] = [
        {
            "device_id": item["device_id"],
            "check_item_version_id": item[
                "check_item_version_id"
            ],
        }
        for item in facts["executions"]
    ]
    facts["scope_contract"] = {
        "scope_contract_id": "test-production-scope",
        "claim_boundary": "selected_network_scope",
    }
    facts["intended_target_pairs"] = deepcopy(
        facts["target_pairs"]
    )
    facts["scope_decisions"] = [
        {
            **item,
            "decision": "included",
            "reason_codes": ["required_check_item_selected"],
        }
        for item in facts["target_pairs"]
    ]
    facts["unmatched_scope_device_ids"] = []
    facts["raw_evidence_set_digest"] = raw_evidence_set_digest(
        facts["evidence_index"]
    )
    facts["conclusion_permission"] = (
        build_report_conclusion_permission_from_source_facts(
            facts
        )
    )
    manifest["schema_version"] = "patrol-report-manifest/v4"
    manifest["generator_version"] = "check-item-report/v4"
    manifest["raw_evidence_set_digest"] = facts[
        "raw_evidence_set_digest"
    ]
    manifest["source_digest"] = _digest(facts)
    return manifest


def _v4_manifest(
    *,
    assessment_status: str = "healthy",
) -> dict[str, object]:
    return _freeze_v4(
        _manifest(assessment_status=assessment_status)
    )


def _freeze_v5(
    manifest: dict[str, object],
) -> dict[str, object]:
    _freeze_v4(manifest)
    facts = manifest["source_facts_json"]
    facts["schema_version"] = "patrol-report-source/v5"
    ledger = build_report_claim_ledger(facts)
    facts["claim_basis_digest"] = ledger["claim_basis_digest"]
    facts["claim_ledger"] = ledger
    facts["claim_ledger_digest"] = ledger[
        "claim_ledger_digest"
    ]
    manifest["schema_version"] = "patrol-report-manifest/v5"
    manifest["generator_version"] = "check-item-report/v5"
    manifest["claim_ledger_digest"] = ledger[
        "claim_ledger_digest"
    ]
    manifest["claim_count"] = len(ledger["claims"])
    manifest["source_digest"] = _digest(facts)
    return manifest


def _v5_manifest(
    *,
    assessment_status: str = "healthy",
) -> dict[str, object]:
    return _freeze_v5(
        _manifest(assessment_status=assessment_status)
    )


def test_v5_report_claims_are_deterministic_and_projected_to_outputs():
    manifest = _v5_manifest()
    facts = manifest["source_facts_json"]
    validate_report_claim_ledger(facts)
    assert build_report_claim_ledger(facts) == facts["claim_ledger"]

    report = build_check_item_report_view(manifest)
    assert report["conclusion"]["claim_id"]
    assert report["coverage"]["claim_id"]
    assert report["matrix"][0]["claim_id"]
    assert report["claim_ledger_digest"] == manifest[
        "claim_ledger_digest"
    ]

    artifacts = render_check_item_artifacts(manifest)
    assert "事实声明账本指纹" in artifacts["markdown"].content.decode(
        "utf-8"
    )
    workbook = load_workbook(
        io.BytesIO(artifacts["excel"].content),
        read_only=True,
    )
    assert "事实声明" in workbook.sheetnames
    with zipfile.ZipFile(io.BytesIO(artifacts["evidence"].content)) as archive:
        assert "report-claims.json" in archive.namelist()
        assert "report-claims.csv" in archive.namelist()


def test_superseded_final_report_keeps_its_frozen_conclusion():
    manifest = _v5_manifest()
    final_report = build_check_item_report_view(manifest)

    manifest["status"] = "superseded"
    superseded_report = build_check_item_report_view(manifest)

    assert superseded_report["report_status"] == "superseded"
    assert superseded_report["conclusion"] == final_report["conclusion"]
    assert superseded_report["conclusion"]["formal"] is True
    assert (
        superseded_report["current_state_authoritative"]
        == final_report["current_state_authoritative"]
    )


def test_v5_report_claim_scope_or_phrase_tampering_is_rejected():
    manifest = _v5_manifest()
    facts = manifest["source_facts_json"]
    facts["claim_ledger"]["claims"][0]["claim_scope"] = "network"
    facts["claim_ledger"]["claims"][0][
        "allowed_phrase_code"
    ] = "healthy"
    manifest["source_digest"] = _digest(facts)

    with pytest.raises(ValueError, match="claim ledger"):
        build_check_item_report_view(manifest)


def test_report_manifest_freezes_latest_event_evaluation_evidence():
    row = SimpleNamespace(
        id="event-1",
        check_execution_id="execution-1",
        device_id="device-1",
        check_item_id="interface.optical_power",
        check_item_version_id="check-item-optical-v1",
        event_type="OpticalRxPowerLow",
        severity="warning",
        status="open",
        entity_type="interface_optical",
        entity_key='["Ten-GigabitEthernet1/0/1"]',
        correlation_key="correlation-1",
        correlation_dimensions_json={"interface": "Ten-GigabitEthernet1/0/1"},
        source_change_id="change-1",
        run_id="run-1",
        latest_change_id="change-2",
        payload_json={
            "change": {"field": "rx_power_dbm", "old": -12.0, "new": -18.0},
            "latest_change": {
                "field": "rx_power_dbm",
                "old": -17.5,
                "new": -18.6,
            },
            "rule": {"rule_id": "optical-rx-low", "version": 1},
            "latest_rule": {
                "rule_id": "optical-rx-low",
                "version": 2,
                "condition": {
                    "kind": "threshold_field_lte",
                    "reference_field": "rx_low_warning_dbm",
                },
            },
            "latest_evaluation": {
                "condition_kind": "threshold_field_lte",
                "field": "rx_power_dbm",
                "previous_value": -17.5,
                "current_value": -18.6,
                "unit": "dBm",
                "reference_field": "rx_low_warning_dbm",
                "reference_value": -17.0,
                "reference_source": "device_output",
            },
        },
        occurred_at=datetime(2026, 7, 28, 12, tzinfo=timezone.utc),
        first_seen_at=datetime(2026, 7, 28, 12, tzinfo=timezone.utc),
        last_seen_at=datetime(2026, 7, 29, 12, tzinfo=timezone.utc),
        occurrence_count=2,
    )

    fact = _event_fact(row)

    assert fact["current_value"] == -18.6
    assert fact["reference_value"] == -17.0
    assert fact["reference_source"] == "device_output"
    assert fact["event_rule_id"] == "optical-rx-low"
    assert fact["event_rule_version"] == 2
    assert fact["condition_kind"] == "threshold_field_lte"


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
    assert report["conclusion_dimensions"] == {
        "risk_conclusion": {
            "code": "unknown",
            "label": "风险未知",
            "basis": ("当前事实不足以形成无风险结论，需结合无法判断项和覆盖范围复核。"),
        },
        "assessment_completeness": {
            "code": "insufficient",
            "label": "证据不足",
            "basis": "完整 0 项，部分完整 0 项，证据不足 1 项。",
        },
        "comparison_readiness": {
            "code": "no_baseline",
            "label": "暂无可用基线",
            "basis": ("可比较 0 项；首次观测或缺少基线 1 项；基线过期或口径不兼容 0 项。"),
        },
        "authority": {
            "code": "production_current",
            "label": "当前生产结论",
            "basis": (
                "本报告仅在身份、时效、完整性、实体覆盖和规则证据均满足时，"
                "对对应设备与检查项形成当前状态结论。"
            ),
        },
    }
    assert "不能据此判断未发生变化" in render_check_item_markdown(
        report
    )


def test_healthy_conclusion_requires_explicit_complete_health_assessment():
    report = build_check_item_report_view(_manifest())

    assert report["conclusion"]["code"] == "healthy"
    assert report["conclusion"]["text"] == "本次覆盖范围内未发现异常"
    assert report["coverage"]["complete_check_count"] == 1
    assert report["current_state_authoritative"] is True
    assert report["conclusion_dimensions"]["risk_conclusion"]["code"] == "no_confirmed_risk"
    assert report["conclusion_dimensions"]["assessment_completeness"]["code"] == "complete"
    assert report["conclusion_dimensions"]["authority"]["code"] == ("production_current")


def test_v4_all_healthy_is_the_only_path_to_overall_health() -> None:
    report = build_check_item_report_view(_v4_manifest())

    assert report["conclusion"]["code"] == "healthy"
    assert report["conclusion"]["text"] == (
        "本次巡检范围整体健康"
    )
    assert report["conclusion_permission"][
        "overall_healthy_allowed"
    ] is True


def test_v4_partial_unknown_uses_one_permission_across_artifacts() -> None:
    manifest = _manifest()
    facts = manifest["source_facts_json"]
    second_execution = deepcopy(facts["executions"][0])
    second_execution["id"] = "execution-2"
    second_execution["device_id"] = "device-2"
    second_execution["evidence_qualification_id"] = (
        "qualification-2"
    )
    second_execution["coverage_decision_id"] = "coverage-2"
    second_execution["evidence_refs_json"] = ["snapshot-2"]
    second_assessment = deepcopy(facts["assessments"][0])
    second_assessment["id"] = "assessment-2"
    second_assessment["execution_id"] = "execution-2"
    second_assessment["device_id"] = "device-2"
    second_assessment["evidence_qualification_id"] = (
        "qualification-2"
    )
    second_assessment["coverage_decision_id"] = "coverage-2"
    second_assessment["assessment_status"] = (
        "unable_to_determine"
    )
    second_assessment["completeness_status"] = "insufficient"
    second_assessment["blocking_reason_codes_json"] = [
        "parser_missing"
    ]
    facts["executions"].append(second_execution)
    facts["assessments"].append(second_assessment)
    second_qualification = deepcopy(
        facts["evidence_qualifications"][0]
    )
    second_qualification.update(
        {
            "id": "qualification-2",
            "digest": "a" * 64,
            "check_execution_id": "execution-2",
            "device_id": "device-2",
            "snapshot_ids_json": ["snapshot-2"],
        }
    )
    facts["evidence_qualifications"].append(
        second_qualification
    )
    second_coverage = deepcopy(facts["coverage_decisions"][0])
    second_coverage.update(
        {
            "id": "coverage-2",
            "digest": "b" * 64,
            "check_execution_id": "execution-2",
            "device_id": "device-2",
        }
    )
    facts["coverage_decisions"].append(second_coverage)
    second_evidence = deepcopy(facts["evidence_index"][0])
    second_evidence.update(
        {
            "snapshot_id": "snapshot-2",
            "run_id": "run-2",
            "device_id": "device-2",
            "device_name": "Core-SW-2",
            "management_ip": "192.0.2.11",
            "raw_text_hash": "c" * 64,
        }
    )
    facts["evidence_index"].append(second_evidence)
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
    manifest = _freeze_v4(manifest)

    report = build_check_item_report_view(manifest)
    artifacts = render_check_item_artifacts(manifest)
    expected_text = (
        "已评估范围内未发现异常，以下范围无法判断"
    )

    assert report["conclusion"]["text"] == expected_text
    assert report["conclusion_permission"][
        "evaluated_scope_no_abnormality_allowed"
    ] is True
    assert report["conclusion_permission"][
        "overall_healthy_allowed"
    ] is False
    assert expected_text in artifacts["markdown"].content.decode(
        "utf-8"
    )
    json_report = json.loads(
        artifacts["json"].content.decode("utf-8")
    )
    assert json_report["conclusion"]["text"] == expected_text
    assert (
        json_report["conclusion_permission"]["decision_digest"]
        == report["conclusion_permission"]["decision_digest"]
    )
    with zipfile.ZipFile(
        io.BytesIO(artifacts["docx"].content)
    ) as archive:
        document_xml = archive.read(
            "word/document.xml"
        ).decode("utf-8")
    assert expected_text in document_xml
    workbook = load_workbook(
        io.BytesIO(artifacts["excel"].content)
    )
    cell_values = {
        str(cell.value)
        for sheet in workbook.worksheets
        for row in sheet.iter_rows()
        for cell in row
        if cell.value is not None
    }
    assert expected_text in cell_values


def test_v4_rejects_a_tampered_conclusion_permission() -> None:
    manifest = _v4_manifest()
    manifest["source_facts_json"]["conclusion_permission"][
        "overall_healthy_allowed"
    ] = False
    manifest["source_digest"] = _digest(
        manifest["source_facts_json"]
    )

    with pytest.raises(
        ValueError,
        match="conclusion permission digest",
    ):
        build_check_item_report_view(manifest)


def test_historical_report_never_promotes_healthy_facts_to_current_health():
    manifest = _manifest(report_purpose="offline_historical")

    report = build_check_item_report_view(manifest)
    artifacts = render_check_item_artifacts(manifest)

    assert report["conclusion"]["code"] == "historical_result"
    assert report["current_state_authoritative"] is False
    assert report["conclusion_dimensions"]["authority"]["code"] == ("historical")
    assert "未发现异常" not in report["conclusion"]["text"]
    assert "不代表" in report["report_purpose_disclosure"]
    markdown = artifacts["markdown"].content.decode("utf-8")
    assert "历史离线巡检" in markdown
    assert "风险结果（非当前状态）" in markdown
    workbook = load_workbook(io.BytesIO(artifacts["excel"].content))
    assert "风险结果" in workbook.sheetnames
    assert "当前风险" not in workbook.sheetnames


def test_current_report_without_authority_context_cannot_claim_health():
    manifest = _manifest()
    manifest["source_facts_json"].pop("report_authority_context")
    manifest["source_digest"] = _digest(manifest["source_facts_json"])

    report = build_check_item_report_view(manifest)

    assert report["conclusion"]["code"] == "current_authority_blocked"
    assert report["current_state_authoritative"] is False
    assert report["conclusion_dimensions"]["authority"]["code"] == "blocked"
    assert "未发现异常" not in report["conclusion"]["text"]


def test_report_discloses_reduced_collection_resilience_in_all_views():
    manifest = _manifest()

    report = build_check_item_report_view(manifest)
    artifacts = render_check_item_artifacts(manifest)

    row = report["matrix"][0]
    assert row["fallback_resilience"] == "reduced"
    assert row["executable_command_count"] == 1
    assert row["declared_command_count"] == 2
    assert report["collection_resilience"]["reduced_count"] == 1

    markdown = artifacts["markdown"].content.decode("utf-8")
    assert "采集路径韧性" in markdown
    assert "韧性降低" in markdown
    assert "display cpu-usage" in markdown
    assert "冻结运行资产不支持该命令" in markdown

    workbook = load_workbook(io.BytesIO(artifacts["excel"].content))
    sheet = workbook["检查项结论"]
    headers = [cell.value for cell in sheet[1]]
    resilience_column = headers.index("采集路径韧性") + 1
    unavailable_column = headers.index("不可用路径及原因") + 1
    assert sheet.cell(2, resilience_column).value == "韧性降低"
    assert "display cpu-usage" in str(
        sheet.cell(2, unavailable_column).value
    )


def test_runtime_manifest_collection_resilience_excludes_collector_secrets():
    runtime_manifest = SimpleNamespace(
        manifest_json={
            "decisions": [
                {
                    "device_id": "device-1",
                    "check_item_id": "system.cpu_usage",
                    "check_item_version_id": "check-version-1",
                    "implementation_version_id": "implementation-1",
                    "runtime_bundle_version_id": "bundle-1",
                    "status": "resolved",
                    "fallback_resilience": "reduced",
                    "declared_command_count": 2,
                    "executable_command_count": 1,
                    "collection_action_ids": ["action-1"],
                    "unavailable_commands": [
                        {
                            "command": "display cpu-usage",
                            "canonical_command": "display cpu-usage",
                            "fallback_rank": 0,
                            "reason_code": (
                                "asset_unavailable_at_plan_time"
                            ),
                        }
                    ],
                }
            ],
            "collection_actions": [
                {
                    "id": "action-1",
                    "command": "display cpu",
                    "canonical_command": "display cpu",
                    "fallback_rank": 1,
                    "parameters": {
                        "password": "must-not-leak",
                        "token": "must-not-leak",
                    },
                }
            ],
        }
    )

    facts = _collection_resilience_facts(runtime_manifest)

    assert facts[0]["fallback_resilience"] == "reduced"
    assert facts[0]["selected_commands"][0]["command"] == (
        "display cpu"
    )
    assert "must-not-leak" not in json.dumps(facts)


def test_tampered_authority_context_cannot_claim_health():
    manifest = _manifest()
    context = manifest["source_facts_json"]["report_authority_context"]
    context["target_type"] = "offline_import"
    manifest["source_digest"] = _digest(manifest["source_facts_json"])

    report = build_check_item_report_view(manifest)

    assert report["conclusion"]["code"] == "current_authority_blocked"
    assert report["current_state_authoritative"] is False


def test_authority_context_must_match_frozen_inventory_digest():
    manifest = _manifest()
    manifest["source_facts_json"]["inventory_snapshot_digest"] = "b" * 64
    manifest["source_digest"] = _digest(manifest["source_facts_json"])

    report = build_check_item_report_view(manifest)

    assert report["conclusion"]["code"] == "current_authority_blocked"
    assert report["current_state_authoritative"] is False
    assert "report_authority_digest_mismatch" in report["conclusion"]["basis"]


def test_report_purpose_cannot_promote_an_offline_source_to_current():
    context = build_report_authority_context(
        report_purpose="production_current",
        patrol_mode="production",
        launch_mode="workflow",
        workflow_name="patrol-change-event",
        target_type="offline_import",
        readiness_ready=True,
        target_manifest_digest="1" * 64,
        inventory_snapshot_digest="a" * 64,
        runtime_manifest_digest="2" * 64,
    )

    assert context["source_kind"] == "offline_import"
    assert context["purpose_source_consistent"] is False
    assert context["current_state_eligible"] is False
    assert "report_purpose_source_mismatch" in context["reason_codes"]
    assert "source_type_not_current_authority" in context["reason_codes"]
    assert report_authority_context_is_valid(
        context,
        expected_purpose="production_current",
    )


def test_unclassified_production_target_fails_closed():
    context = build_report_authority_context(
        report_purpose="production_current",
        patrol_mode="production",
        launch_mode="workflow",
        workflow_name="legacy-patrol",
        target_type="legacy",
        readiness_ready=True,
        target_manifest_digest="1" * 64,
        inventory_snapshot_digest="a" * 64,
        runtime_manifest_digest="2" * 64,
    )

    assert context["source_kind"] == "unclassified"
    assert context["current_state_eligible"] is False
    assert context["purpose_source_consistent"] is False


def test_v3_manifest_rejects_a_changed_raw_evidence_identity_set():
    manifest = _manifest()
    manifest["source_facts_json"]["evidence_index"][0]["raw_text_hash"] = "f" * 64
    manifest["source_digest"] = _digest(manifest["source_facts_json"])

    with pytest.raises(
        ValueError,
        match="raw evidence set digest",
    ):
        build_check_item_report_view(manifest)


def test_legacy_v2_manifest_is_treated_as_shadow_validation():
    manifest = _manifest()
    facts = manifest["source_facts_json"]
    facts["schema_version"] = "patrol-report-source/v2"
    facts.pop("report_purpose")
    facts.pop("raw_evidence_set_digest")
    manifest["schema_version"] = "patrol-report-manifest/v2"
    manifest.pop("report_purpose")
    manifest.pop("raw_evidence_set_digest")
    manifest["source_digest"] = _digest(facts)

    report = build_check_item_report_view(manifest)

    assert report["report_purpose"] == "shadow_validation"
    assert report["current_state_authoritative"] is False
    assert report["conclusion"]["code"] == ("shadow_validation_result")
    assert "未发现异常" not in report["conclusion"]["text"]


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
    facts["assessments"][0]["completeness_status"] = "complete"
    second_execution = deepcopy(facts["executions"][0])
    second_execution["id"] = "execution-2"
    second_execution["device_id"] = "device-2"
    second_assessment = deepcopy(facts["assessments"][0])
    second_assessment["id"] = "assessment-2"
    second_assessment["execution_id"] = "execution-2"
    second_assessment["device_id"] = "device-2"
    second_assessment["assessment_status"] = "unable_to_determine"
    second_assessment["completeness_status"] = "insufficient"
    second_assessment["blocking_reason_codes_json"] = ["parser_missing"]
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
    assert report["conclusion_dimensions"]["risk_conclusion"]["code"] == "confirmed_risk"
    assert report["conclusion_dimensions"]["assessment_completeness"]["code"] == "partial"


def test_report_separates_current_historical_and_recovery_pending_risks():
    manifest = _manifest(assessment_status="at_risk")
    facts = manifest["source_facts_json"]
    facts["executions"][0]["current_finding_count"] = 1
    facts["executions"][0]["risk_verification_status"] = "confirmed_current"
    facts["assessments"][0]["current_finding_ids_json"] = ["finding-current"]
    facts["assessments"][0]["risk_case_ids_json"] = [
        "risk-current",
        "risk-history",
        "risk-recovery",
    ]
    facts["assessments"][0]["risk_verification_status"] = "confirmed_current"
    facts["findings"] = [
        {
            "id": "finding-current",
            "execution_id": "execution-1",
            "device_id": "device-1",
            "actual_value": 91,
            "actual_value_number": 91,
            "reference_value": 90,
            "reference_value_number": 90,
            "reference_source": "event_rule_static",
            "unit": "percent",
            "event_rule_version_id": "current-state-rule-v1",
            "rule_digest": "f" * 64,
            "observed_at": "2026-07-28T12:00:00+00:00",
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

    assert [item["id"] for item in report["risks"]] == ["risk-current"]
    assert [item["id"] for item in report["historical_not_reverified_risks"]] == ["risk-history"]
    assert [item["id"] for item in report["recovery_pending_risks"]] == ["risk-recovery"]
    assert report["risks"][0]["actual_value_number"] == 91
    assert report["risks"][0]["reference_value_number"] == 90
    assert report["risks"][0]["reference_source"] == "event_rule_static"
    assert report["risk_verification_counts"] == {
        "confirmed_current": 1,
        "recovery_pending": 1,
        "not_reverified": 1,
        "resolved": 0,
        "technical_recovered": 0,
        "closed": 0,
    }


def test_approved_cross_check_item_association_changes_only_leadership_problem_count():
    manifest = _manifest(assessment_status="at_risk", event_count=2)
    facts = manifest["source_facts_json"]
    facts["executions"][0]["risk_verification_status"] = "confirmed_current"
    facts["assessments"][0].update(
        {
            "completeness_status": "complete",
            "evaluated_entity_count": 1,
            "risk_case_ids_json": ["risk-down", "risk-optical"],
            "risk_verification_status": "confirmed_current",
            "blocking_reason_codes_json": [],
        }
    )
    facts["events"] = [
        {
            "id": "event-down",
            "execution_id": "execution-1",
            "device_id": "device-1",
            "check_item_id": "interface.oper_status",
            "event_type": "InterfaceDown",
            "severity": "major",
            "status": "open",
            "entity_type": "interface",
            "entity_key": "Ethernet1/1",
            "source_change_id": "change-down",
            "occurred_at": "2026-07-28T12:00:00+00:00",
        },
        {
            "id": "event-optical",
            "execution_id": "execution-1",
            "device_id": "device-1",
            "check_item_id": "interface.optical_power",
            "event_type": "OpticalPowerUnavailable",
            "severity": "warning",
            "status": "open",
            "entity_type": "interface",
            "entity_key": "Ethernet1/1",
            "source_change_id": "change-optical",
            "occurred_at": "2026-07-28T12:00:01+00:00",
        },
    ]
    facts["event_occurrences"] = [
        {
            "id": "occurrence-down",
            "aggregate_event_id": "event-down",
            "execution_id": "execution-1",
            "device_id": "device-1",
            "check_item_id": "interface.oper_status",
            "event_type": "InterfaceDown",
            "severity": "major",
            "status": "open",
            "occurrence_kind": "fault",
            "entity_type": "interface",
            "entity_key": "Ethernet1/1",
            "occurred_at": "2026-07-28T12:00:00+00:00",
        },
        {
            "id": "occurrence-optical",
            "aggregate_event_id": "event-optical",
            "execution_id": "execution-1",
            "device_id": "device-1",
            "check_item_id": "interface.optical_power",
            "event_type": "OpticalPowerUnavailable",
            "severity": "warning",
            "status": "open",
            "occurrence_kind": "fault",
            "entity_type": "interface",
            "entity_key": "Ethernet1/1",
            "occurred_at": "2026-07-28T12:00:01+00:00",
        },
    ]
    common_risk = {
        "device_id": "device-1",
        "entity_type": "interface",
        "entity_key": "Ethernet1/1",
        "correlation_dimensions_json": {"interface": "Ethernet1/1"},
        "status": "open",
        "first_seen_at": "2026-07-28T12:00:00+00:00",
        "last_seen_at": "2026-07-28T12:00:01+00:00",
        "verification_status": "confirmed_current",
        "last_verification_execution_id": "execution-1",
        "remediation_status": "not_started",
        "source_finding_ids_json": [],
    }
    facts["risks"] = [
        {
            **common_risk,
            "id": "risk-down",
            "episode_id": "episode-down",
            "check_item_id": "interface.oper_status",
            "risk_type": "InterfaceDown",
            "severity": "major",
            "source_event_ids_json": ["event-down"],
        },
        {
            **common_risk,
            "id": "risk-optical",
            "episode_id": "episode-optical",
            "check_item_id": "interface.optical_power",
            "risk_type": "OpticalPowerUnavailable",
            "severity": "warning",
            "source_event_ids_json": ["event-optical"],
        },
    ]
    facts["risk_episode_associations"] = [
        {
            "id": "association-approved",
            "primary_risk_case_id": "risk-down",
            "primary_risk_episode_id": "episode-down",
            "related_risk_case_id": "risk-optical",
            "related_risk_episode_id": "episode-optical",
            "relation_type": "causal_symptom",
            "status": "approved",
            "source_type": "manual",
            "evidence_refs_json": ["snapshot-1"],
            "reviewed_by": "network-sme",
            "reviewed_at": "2026-07-28T12:05:00+00:00",
        }
    ]
    _freeze_v5(manifest)

    report = build_check_item_report_view(manifest)

    assert len(report["risks"]) == 2
    assert len(report["events"]) == 2
    assert report["risk_issue_summary"]["problem_count"] == 1
    assert report["risk_issue_summary"]["risk_episode_count"] == 2
    assert report["risk_issue_summary"]["affected_entity_count"] == 1
    assert report["risk_issue_summary"]["event_occurrence_count"] == 2
    assert report["risk_issues"][0]["claim_id"]
    artifacts = render_check_item_artifacts(manifest)
    markdown = artifacts["markdown"].content.decode("utf-8")
    assert "巡检主问题数按经批准关联后的 RiskEpisode 主问题统计" in markdown
    workbook = load_workbook(io.BytesIO(artifacts["excel"].content))
    overview_values = {
        str(label): value
        for label, value in workbook["总览"].iter_rows(
            min_row=2,
            max_col=2,
            values_only=True,
        )
        if label
    }
    assert overview_values["当前风险"] == 1
    assert overview_values["风险Episode"] == 2
    assert overview_values["关联症状"] == 1
    assert workbook["巡检主问题"].max_row == 2
    assert workbook["当前风险"].max_row == 3


def test_report_separates_technical_recovery_from_formal_closure():
    manifest = _manifest()
    facts = manifest["source_facts_json"]
    base_risk = {
        "check_item_id": "system.cpu_usage",
        "device_id": "device-1",
        "entity_type": "cpu_scope",
        "entity_key": '["device","0"]',
        "risk_type": "CpuHigh",
        "severity": "major",
        "status": "resolved",
        "verification_status": "resolved",
        "first_seen_at": "2026-07-01T00:00:00+00:00",
        "last_seen_at": "2026-07-28T12:00:00+00:00",
        "resolved_at": "2026-07-28T12:30:00+00:00",
        "source_finding_ids_json": [],
        "source_event_ids_json": [],
    }
    facts["risks"] = [
        {
            **base_risk,
            "id": "risk-technical",
            "remediation_status": "completed",
        },
        {
            **base_risk,
            "id": "risk-closed",
            "entity_key": '["device","1"]',
            "remediation_status": "accepted",
            "closed_at": "2026-07-29T08:00:00+00:00",
            "closed_by": "risk-manager",
            "closure_reason": "整改已验收",
            "closure_disposition": "remediation_accepted",
            "closure_evidence_refs_json": ["closure:approval-1"],
        },
    ]
    manifest["source_digest"] = _digest(facts)

    report = build_check_item_report_view(manifest)

    assert report["risk_verification_counts"]["resolved"] == 2
    assert report["risk_verification_counts"]["technical_recovered"] == 1
    assert report["risk_verification_counts"]["closed"] == 1
    assert report["technically_recovered_risks"][0]["closure_status"] == (
        "pending_acceptance"
    )
    assert report["formally_closed_risks"][0]["closure_status"] == "closed"
    artifacts = render_check_item_artifacts(manifest)
    markdown = artifacts["markdown"].content.decode("utf-8")
    assert "技术恢复与正式关闭" in markdown
    assert "整改完成，待验收" in markdown
    assert "风险已正式关闭" in markdown
    workbook = load_workbook(io.BytesIO(artifacts["excel"].content))
    closure_sheet = workbook["技术恢复与关闭"]
    values = {
        closure_sheet.cell(row, 9).value
        for row in range(2, closure_sheet.max_row + 1)
    }
    assert values == {"整改完成，待验收", "风险已正式关闭"}


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
    assert {name: artifact.sha256 for name, artifact in first.items()} == {
        name: artifact.sha256 for name, artifact in second.items()
    }
    assert manifest["source_digest"].encode() in first["markdown"].content
    assert json.loads(first["json"].content)["source_digest"] == manifest["source_digest"]

    workbook = load_workbook(io.BytesIO(first["excel"].content))
    assert workbook.sheetnames == [
        "总览",
        "检查项结论",
        "巡检主问题",
        "当前风险",
        "历史风险待复核",
        "恢复待确认",
        "技术恢复与关闭",
        "整改跟踪",
        "事件",
        "变化",
        "当前观测",
        "事实声明",
        "证据索引",
    ]
    assert "A1:B1" in {str(item) for item in workbook["总览"].merged_cells.ranges}
    assert workbook["总览"]["A2"].value == "巡检批次"
    assert workbook["总览"]["B2"].value == manifest["patrol_batch_id"]
    overview_values = {
        str(label): value
        for label, value in workbook["总览"].iter_rows(
            min_row=2,
            max_col=2,
            values_only=True,
        )
        if label
    }
    assert overview_values["数据指纹"] == f"sha256:{manifest['source_digest']}"
    assert overview_values["当前风险"] == 0
    assert overview_values["风险Episode"] == 0
    assert overview_values["关联症状"] == 0
    assert workbook["总览"].sheet_view.showGridLines is False
    conclusion_sheet = workbook["检查项结论"]
    assert conclusion_sheet.freeze_panes == "D2"
    assert conclusion_sheet["A2"].value == "Core-SW-1"
    assert conclusion_sheet["C2"].value == "CPU 使用率"
    assert conclusion_sheet["D2"].value == "已完成"
    assert conclusion_sheet["E2"].value == "正常"
    assert conclusion_sheet["J2"].value == "时效合格"
    empty_risk_sheet = workbook["当前风险"]
    assert empty_risk_sheet["A2"].value == "暂无数据"
    assert "A2:X2" in {str(item) for item in empty_risk_sheet.merged_cells.ranges}
    assert empty_risk_sheet.freeze_panes == "D2"
    observation_sheet = workbook["当前观测"]
    assert observation_sheet.freeze_panes == "E2"
    assert observation_sheet["A2"].value == "Core-SW-1"
    assert observation_sheet["C2"].value == "CPU 使用率（system.cpu_usage）"
    assert observation_sheet["D2"].value == "device.cpu.utilization"
    evidence_sheet = workbook["证据索引"]
    assert evidence_sheet.freeze_panes == "F2"
    assert evidence_sheet["A2"].value == "Core-SW-1"
    assert evidence_sheet["E2"].value == "CPU 使用率（system.cpu_usage）"
    assert evidence_sheet["H2"].value == "采集成功"
    assert evidence_sheet["I2"].value == "解析成功"
    with zipfile.ZipFile(io.BytesIO(first["docx"].content)) as archive:
        document_xml = archive.read("word/document.xml")
    assert document_xml.count(b"<w:tblHeader") >= 4
    assert "结论维度".encode() in document_xml
    assert b'w:type="page"' not in document_xml
    assert "CPU 使用率（system.cpu_usage）".encode() in document_xml
    assert "事实已冻结".encode() in document_xml
    assert b">completed<" not in document_xml
    assert b">fresh<" not in document_xml


def test_excel_uses_the_projection_profile_frozen_in_the_manifest():
    manifest = _manifest()
    facts = manifest["source_facts_json"]
    snapshot = build_report_projection_snapshot(
        facts["check_items"]
    )
    facts["report_projection_snapshot"] = snapshot
    facts["report_projection_digest"] = snapshot["digest"]
    _freeze_v5(manifest)

    artifacts = render_check_item_artifacts(manifest)
    workbook = load_workbook(
        io.BytesIO(artifacts["excel"].content),
        data_only=True,
    )

    assert "CPU" in workbook.sheetnames
    sheet = workbook["CPU"]
    headers = {
        cell.value: index
        for index, cell in enumerate(sheet[1], start=1)
    }
    assert sheet.cell(2, headers["设备名称"]).value == "Core-SW-1"
    assert sheet.cell(2, headers["CPU即时使用率(%)"]).value == 35
    assert sheet.cell(2, headers["评估结论"]).value == "正常"
    assert sheet.cell(2, headers["快照ID"]).value == "snapshot-1"


def test_check_item_docx_uses_formal_patrol_chapter_structure():
    manifest = _manifest()
    facts = manifest["source_facts_json"]
    snapshot = build_report_projection_snapshot(facts["check_items"])
    facts["report_projection_snapshot"] = snapshot
    facts["report_projection_digest"] = snapshot["digest"]
    _freeze_v5(manifest)

    artifacts = render_check_item_artifacts(manifest)
    document = Document(io.BytesIO(artifacts["docx"].content))
    document_text = "\n".join(item.text for item in document.paragraphs)
    headings = [
        "第一章 概述",
        "1.1 巡检概述",
        "1.2 巡检说明",
        "1.3 巡检范围",
        "1.3.1 设备清单",
        "第二章 巡检内容",
        "2.1 网络设备巡检项",
        "2.2 巡检设备配置备份",
        "第三章 巡检结果",
        "3.1 网络设备",
        "3.1.1 设备基本运行状况",
        "设备 CPU 利用率状态",
        "第四章 巡检总结",
        "第五章 后续工作计划",
    ]

    assert all(item in document_text for item in headings)
    assert [document_text.index(item) for item in headings] == sorted(
        document_text.index(item) for item in headings
    )
    header_rows = {
        tuple(cell.text for cell in table.rows[0].cells)
        for table in document.tables
        if table.rows
    }
    assert any(
        headers[:3]
        == (
            "设备",
            "CPU即时使用率(%)（cpu_instant_pct）",
            "CPU 5秒使用率(%)（cpu_5s_pct）",
        )
        for headers in header_rows
    )
    assert all("采集时间" not in headers for headers in header_rows)


def test_report_projects_coverage_gap_entities_into_all_review_views():
    manifest = _manifest(
        assessment_status="unable_to_determine",
    )
    facts = manifest["source_facts_json"]
    execution = facts["executions"][0]
    assessment = facts["assessments"][0]
    coverage = facts["coverage_decisions"][0]
    execution.update(
        {
            "observation_count": 40,
            "observed_entity_count": 40,
            "coverage_status": "partial",
            "coverage_basis": "inventory_entity_set",
        }
    )
    assessment.update(
        {
            "completeness_status": "partial",
            "expected_entity_count": 48,
            "observed_entity_count": 40,
            "evaluated_entity_count": 40,
            "coverage_status": "partial",
            "coverage_basis": "inventory_entity_set",
            "blocking_reason_codes_json": [
                "entity_coverage_incomplete",
            ],
        }
    )
    coverage.update(
        {
            "coverage_status": "partial",
            "coverage_basis": "inventory_entity_set",
            "expected_entity_count": 48,
            "observed_entity_count": 40,
            "evaluated_entity_count": 40,
            "denominator_ref_json": {
                "basis": "inventory_entity_set",
                "inventory_snapshot_digest": "a" * 64,
            },
            "missing_entity_sample_json": [
                ["interface", "Ethernet1/41"],
                ["interface", "Ethernet1/42"],
            ],
            "unexpected_entity_sample_json": [
                ["interface", "Ethernet1/49"],
            ],
            "reason_codes_json": [
                "entity_coverage_incomplete",
                "entity_keys_missing",
            ],
        }
    )
    manifest["source_digest"] = _digest(facts)

    report = build_check_item_report_view(manifest)
    row = report["matrix"][0]

    assert row["expected_entity_count"] == 48
    assert row["observed_entity_count"] == 40
    assert row["missing_entity_sample"] == [
        ["interface", "Ethernet1/41"],
        ["interface", "Ethernet1/42"],
    ]
    assert row["unexpected_entity_sample"] == [
        ["interface", "Ethernet1/49"],
    ]
    assert row["coverage_reason_codes"] == [
        "entity_coverage_incomplete",
        "entity_keys_missing",
    ]

    artifacts = render_check_item_artifacts(manifest)
    markdown = artifacts["markdown"].content.decode("utf-8")
    assert "已观测 40 / 期望 48" in markdown
    assert "Ethernet1/41" in markdown

    with zipfile.ZipFile(
        io.BytesIO(artifacts["docx"].content)
    ) as archive:
        document_xml = archive.read(
            "word/document.xml"
        ).decode("utf-8")
    assert "已观测 40 / 期望 48" in document_xml
    assert "Ethernet1/41" in document_xml

    workbook = load_workbook(
        io.BytesIO(artifacts["excel"].content)
    )
    sheet = workbook["检查项结论"]
    headers = {
        str(cell.value): cell.column
        for cell in sheet[1]
        if cell.value is not None
    }
    assert sheet.cell(
        2,
        headers["期望实体数"],
    ).value == 48
    assert "Ethernet1/41" in str(
        sheet.cell(2, headers["缺失实体样例"]).value
    )
    assert "Ethernet1/49" in str(
        sheet.cell(2, headers["额外实体样例"]).value
    )
    assert "entity_coverage_incomplete" in str(
        sheet.cell(2, headers["覆盖判定原因"]).value
    )


def test_report_localizes_failure_layer_without_losing_reason_codes():
    manifest = _manifest(
        assessment_status="unable_to_determine",
    )
    facts = manifest["source_facts_json"]
    execution = facts["executions"][0]
    assessment = facts["assessments"][0]
    coverage = facts["coverage_decisions"][0]
    execution.update(
        {
            "execution_status": "parse_failed",
            "observation_count": 0,
            "observed_entity_count": 0,
            "coverage_status": "not_evaluable",
            "coverage_basis": "unknown",
        }
    )
    assessment.update(
        {
            "completeness_status": "insufficient",
            "observed_entity_count": 0,
            "evaluated_entity_count": 0,
            "coverage_status": "not_evaluable",
            "coverage_basis": "unknown",
            "blocking_reason_codes_json": [
                "parser_compile_failed",
                "coverage_not_evaluable",
            ],
        }
    )
    coverage.update(
        {
            "coverage_status": "not_evaluable",
            "coverage_basis": "unknown",
            "expected_entity_count": None,
            "observed_entity_count": 0,
            "evaluated_entity_count": 0,
            "reason_codes_json": [
                "coverage_not_evaluable",
                "parser_compile_failed",
            ],
        }
    )
    manifest["source_digest"] = _digest(facts)

    report = build_check_item_report_view(manifest)
    assert report["matrix"][0]["blocking_reason_codes"] == [
        "parser_compile_failed",
        "coverage_not_evaluable",
    ]

    artifacts = render_check_item_artifacts(manifest)
    markdown = artifacts["markdown"].content.decode("utf-8")
    assert "Parser 解析失败" in markdown
    assert "Parser 模板编译失败（parser_compile_failed）" in markdown
    assert "无法评估实体覆盖（coverage_not_evaluable）" in markdown

    with zipfile.ZipFile(
        io.BytesIO(artifacts["docx"].content)
    ) as archive:
        document_xml = archive.read(
            "word/document.xml"
        ).decode("utf-8")
    assert "失败层 / 原因" in document_xml
    assert "Parser 模板编译失败" in document_xml
    assert "parser_compile_failed" not in document_xml

    workbook = load_workbook(
        io.BytesIO(artifacts["excel"].content)
    )
    sheet = workbook["检查项结论"]
    headers = {
        str(cell.value): cell.column
        for cell in sheet[1]
        if cell.value is not None
    }
    assert "Parser 模板编译失败（parser_compile_failed）" in str(
        sheet.cell(2, headers["阻断原因"]).value
    )
    assert "无法评估实体覆盖（coverage_not_evaluable）" in str(
        sheet.cell(2, headers["覆盖判定原因"]).value
    )


def test_leadership_artifacts_include_change_event_and_data_limit_details():
    manifest = _manifest()
    facts = manifest["source_facts_json"]
    facts["changes"] = [
        {
            "id": "change-1",
            "execution_id": "execution-1",
            "device_id": "device-1",
            "check_item_id": "system.cpu_usage",
            "entity_key": '["device","0"]',
            "field": "cpu_usage",
            "old": 35,
            "new": 91,
            "decision_reason": "threshold_crossed",
            "observed_at": "2026-07-28T12:00:00+00:00",
        }
    ]
    facts["events"] = [
        {
            "id": "event-1",
            "execution_id": "execution-1",
            "device_id": "device-1",
            "check_item_id": "system.cpu_usage",
            "event_type": "CpuUtilizationHigh",
            "severity": "warning",
            "status": "open",
            "entity_key": '["device","0"]',
            "field": "cpu_usage",
            "previous_value": 35,
            "current_value": 91,
            "unit": "percent",
            "condition_kind": "threshold",
            "condition": {
                "kind": "threshold",
                "fields": ["cpu_usage"],
                "gte": 90,
            },
            "reference_field": "gte",
            "reference_value": 90,
            "reference_source": "event_rule_static",
            "event_rule_id": "cpu-high",
            "event_rule_version": 3,
            "occurred_at": "2026-07-28T12:00:00+00:00",
        }
    ]
    manifest["source_digest"] = _digest(facts)

    artifacts = render_check_item_artifacts(manifest)
    markdown = artifacts["markdown"].content.decode("utf-8")
    with zipfile.ZipFile(io.BytesIO(artifacts["docx"].content)) as archive:
        document_xml = archive.read("word/document.xml")

    assert "CPU 使用率（system.cpu_usage）" in markdown
    assert "threshold_crossed" in markdown
    assert "CpuUtilizationHigh" in markdown
    assert "cpu_usage >= 90 percent（事件规则静态阈值）" in markdown
    assert "cpu-high@3" in markdown
    assert "4.1 数据可信度与结论边界" in markdown
    assert "3.1.6 变化与事件".encode() in document_xml
    assert b"CpuUtilizationHigh" in document_xml
    assert b"cpu_usage" in document_xml
    assert "事件规则静态阈值".encode() in document_xml
    assert "4.1 数据可信度与结论边界".encode() in document_xml

    workbook = load_workbook(io.BytesIO(artifacts["excel"].content))
    event_sheet = workbook["事件"]
    assert event_sheet["I2"].value == "cpu_usage"
    assert event_sheet["K2"].value == 91
    assert event_sheet["M2"].value == ("cpu_usage >= 90 percent（事件规则静态阈值）")
    assert event_sheet["S2"].value == "cpu-high"
    assert event_sheet["T2"].value == 3


def test_report_uses_current_recovery_occurrence_instead_of_old_event_projection():
    manifest = _manifest(event_count=1)
    facts = manifest["source_facts_json"]
    facts["events"] = [
        {
            "id": "event-cpu-high",
            "execution_id": "execution-old",
            "device_id": "device-1",
            "check_item_id": "system.cpu_usage",
            "event_type": "CpuUtilizationHigh",
            "severity": "warning",
            "status": "resolved",
            "entity_key": '["device","0"]',
            "field": "cpu_usage",
            "previous_value": 35,
            "current_value": 91,
            "unit": "percent",
            "event_rule_id": "cpu-high",
            "event_rule_version": 1,
            "occurred_at": "2026-07-20T12:00:00+00:00",
        }
    ]
    facts["event_occurrences"] = [
        {
            "id": "event-occurrence-recovery",
            "aggregate_event_id": "event-cpu-high",
            "execution_id": "execution-1",
            "device_id": "device-1",
            "check_item_id": "system.cpu_usage",
            "event_type": "CpuUtilizationRecovered",
            "severity": "info",
            "status": "resolved",
            "occurrence_kind": "recovery",
            "projection_applied": True,
            "entity_key": '["device","0"]',
            "field": "cpu_usage",
            "previous_value": 91,
            "current_value": 60,
            "unit": "percent",
            "condition_kind": "threshold",
            "condition": {
                "kind": "threshold",
                "fields": ["cpu_usage"],
                "lte": 75,
            },
            "reference_field": "lte",
            "reference_value": 75,
            "reference_source": "event_rule_static",
            "event_rule_id": "cpu-recovered",
            "event_rule_version_id": "rule-cpu-recovered-v2",
            "event_rule_version": 2,
            "occurred_at": "2026-07-28T12:00:00+00:00",
        }
    ]
    manifest["source_digest"] = _digest(facts)

    report = build_check_item_report_view(manifest)

    assert len(report["events"]) == 1
    assert report["events"][0]["id"] == "event-occurrence-recovery"
    assert report["events"][0]["aggregate_event_id"] == "event-cpu-high"
    assert report["events"][0]["event_type"] == "CpuUtilizationRecovered"
    assert report["events"][0]["occurrence_kind"] == "recovery"
    assert (
        report["events"][0]["event_rule_version_id"]
        == "rule-cpu-recovered-v2"
    )


def test_excel_keeps_digests_and_negative_measurements_as_literal_text():
    manifest = _manifest()
    facts = manifest["source_facts_json"]
    facts["events"] = [
        {
            "id": "event-optical",
            "execution_id": "execution-1",
            "device_id": "device-1",
            "check_item_id": "system.cpu_usage",
            "event_type": "OpticalRxPowerLow",
            "severity": "warning",
            "status": "open",
            "entity_key": '["interface-1"]',
            "field": "rx_power_dbm",
            "previous_value": -14,
            "current_value": -18.7,
            "unit": "dBm",
            "condition_kind": "threshold_field_lte",
            "reference_field": "rx_low_warning_dbm",
            "reference_value": -17,
            "reference_source": "device_output",
            "event_rule_id": "optical-rx-low",
            "event_rule_version": 2,
            "occurred_at": "2026-07-28T12:00:00+00:00",
        }
    ]
    manifest["target_manifest_digest"] = "1" * 64
    manifest["runtime_manifest_digest"] = "3" * 64
    facts["target_manifest_digest"] = "1" * 64
    facts["runtime_manifest_digest"] = "3" * 64
    manifest["source_digest"] = _digest(facts)

    workbook = load_workbook(
        io.BytesIO(render_check_item_artifacts(manifest)["excel"].content),
    )

    overview = {
        str(row[0].value): row[1]
        for row in workbook["总览"].iter_rows(min_col=1, max_col=2)
        if row[0].value is not None
    }
    assert overview["目标清单指纹"].value == f"sha256:{'1' * 64}"
    assert overview["运行资产清单指纹"].value == f"sha256:{'3' * 64}"
    assert overview["目标清单指纹"].data_type == "s"
    assert workbook["事件"]["K2"].value == -18.7
    assert workbook["事件"]["M2"].value.startswith("rx_power_dbm <= -17")
    assert not str(workbook["事件"]["M2"].value).startswith("'")


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
        combined = b"\n".join(archive.read(name) for name in names)

    assert {
        "README.txt",
        "report-manifest.json",
        "check-assessments.csv",
        "evidence-index.csv",
        "risk-lifecycle.csv",
        "report-claims.json",
        "report-claims.csv",
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
            self.uploads.append((object_key, bytes(content), content_type))
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


def test_materializer_reuses_and_verifies_an_attached_manifest():
    base = _manifest()
    contents = {
        name: f"frozen-{name}".encode()
        for name in ("markdown", "docx", "excel", "json", "evidence")
    }
    keys = {
        name: f"reports/batch/{name}"
        for name in contents
    }
    digests = {
        name: hashlib.sha256(content).hexdigest()
        for name, content in contents.items()
    }
    manifest = {
        **base,
        "artifact_object_keys_json": keys,
        "artifact_sha256_json": digests,
    }

    class ManifestService:
        def build(self, _patrol_batch_id, *, generated_by):
            assert generated_by == "retry"
            return manifest

        def attach_artifacts(self, *_args, **_kwargs):
            raise AssertionError("an attached manifest must not be rewritten")

    class Storage:
        def upload_file(self, *_args, **_kwargs):
            raise AssertionError("an attached manifest must not be re-rendered")

        def download_file(self, object_key):
            name = next(name for name, key in keys.items() if key == object_key)
            return contents[name]

    result = CheckItemReportMaterializer(
        manifest_service=ManifestService(),
        object_storage=Storage(),
    ).materialize("batch-report", generated_by="retry")

    assert result["success"] is True
    assert result["reused"] is True
    assert result["artifact_sha256"] == digests
    assert result["artifact_bytes"] == {
        name: len(content) for name, content in contents.items()
    }


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
