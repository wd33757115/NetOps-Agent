from __future__ import annotations

import io
import zipfile
from datetime import datetime, timezone

from docx import Document

from src.core.patrol.leadership_report import (
    _domain_threshold_coverage_text,
    build_leadership_report,
    render_leadership_docx,
    render_leadership_markdown,
)
from src.core.workflows.patrol_batch_service import (
    domain_checks,
    evaluate_current_state,
    report_domain,
)


def _summary(*, findings=None, completed=2, target=2, quality="ready", evaluated=2):
    findings = list(findings or [])
    return {
        "run_count": completed,
        "device_count": completed,
        "status_counts": {"completed": completed},
        "scope": {
            "target_count": target,
            "completed_count": completed,
            "failed_count": 0,
            "skipped_count": max(0, target - completed),
            "cancelled_count": 0,
            "active_count": 0,
            "target_label": "设备分组 H3C",
        },
        "quality": {
            "status": quality,
            "command_count": 12,
            "parser_ready_rate": 1.0 if quality == "ready" else 0.8,
            "observation_count": 24,
            "snapshot_count": 12,
        },
        "change_count": 0,
        "event_count": 0,
        "changes": [],
        "events": [],
        "current_findings": findings,
        "current_finding_count": len(findings),
        "current_state_rule_coverage": {
            "active_fault_rule_count": 2,
            "latest_observation_count": 24,
            "evaluated_observation_count": evaluated,
            "bundle_digests": ["digest-1"] if evaluated else [],
        },
        "runs": [],
        "deliverables": {
            "raw_cli_url": "/raw.zip",
            "events_url": "/events.csv",
            "technical_report_url": "/report.md",
        },
    }


def _unstructured_change():
    return {
        "change_id": "raw-change-1",
        "device_id": "sw-1",
        "device_name": "SW1",
        "command": "show running-config",
        "entity_type": "raw_command_output",
        "entity_key": "show running-config",
        "field": "raw_text_hash",
        "event_status": "no_event",
        "previous_snapshot_id": "snapshot-before",
        "current_snapshot_id": "snapshot-after",
        "dimensions": {
            "unstructured_diff": {
                "changed": True,
                "profile": "configuration",
                "profile_label": "配置输出",
                "added_line_count": 1,
                "removed_line_count": 1,
                "changed_line_count": 2,
                "similarity_ratio": 0.8,
                "categories": [
                    {"name": "security", "label": "安全策略", "line_count": 2}
                ],
                "preview": [
                    {
                        "operation": "added",
                        "category": "security",
                        "category_label": "安全策略",
                        "text": "snmp-agent community <redacted> ro",
                    }
                ],
            }
        },
    }


def test_non_structured_domain_reports_change_without_requiring_state_rule():
    change = _unstructured_change()
    checks = domain_checks(
        observations=[
            {
                "entity_type": "raw_command_output",
                "field": "raw_text_hash",
                "command": "show running-config",
            }
        ],
        command_details=[
            {"command": "show running-config", "parse_status": "raw_hashed"}
        ],
        changes=[change],
        events=[],
        current_findings=[],
        rule_coverage={"available": True, "by_domain": {}},
    )

    raw = next(item for item in checks if item["domain"] == "raw")
    assert raw["status"] == "有变化，未触发事件"
    assert "规则缺失" not in raw["reason"]


def test_leadership_report_includes_safe_non_structured_change_summary():
    summary = _summary()
    summary["quality"]["parse_status_counts"] = {"raw_hashed": 2}
    summary["changes"] = [_unstructured_change()]
    summary["change_count"] = 1

    report = build_leadership_report("batch-unstructured", summary)
    markdown = render_leadership_markdown(report)
    document = Document(io.BytesIO(render_leadership_docx(report)))
    document_text = "\n".join(paragraph.text for paragraph in document.paragraphs)

    assert report["metrics"]["unstructured_collected_count"] == 2
    assert report["metrics"]["unstructured_changed_count"] == 1
    assert any("非结构化输出变化" in item["action"] for item in report["recommendations"])
    assert "## 五、非结构化输出变化" in markdown
    assert "<redacted>" in markdown
    assert "非结构化输出变化" in document_text
    assert "public-community" not in markdown
    unstructured_table = next(
        table
        for table in document.tables
        if [cell.text for cell in table.rows[0].cells]
        == ["类型", "设备", "命令", "增/删行", "变化分类"]
    )
    assert len(unstructured_table.rows) == 2
    assert all(len(cell.text) < 120 for row in unstructured_table.rows for cell in row.cells)


def test_current_state_uses_initial_fault_semantics_and_suppresses_admin_down():
    rules = [
        {
            "rule_id": "interface-down",
            "event_type": "InterfaceDown",
            "severity": "major",
            "event_class": "fault",
            "condition": {
                "kind": "transition",
                "entity_types": ["interface_l3"],
                "fields": ["status"],
                "from_group": "up",
                "to_group": "down",
                "allow_initial_target": True,
            },
        }
    ]
    observations = [
        {
            "id": "obs-1",
            "device_id": "sw-1",
            "device_name": "SW1",
            "entity_type": "interface_l3",
            "entity_key": '["GE1/0/1"]',
            "field": "status",
            "value": "down",
            "command": "display interface brief",
        },
        {
            "id": "obs-2",
            "device_id": "sw-1",
            "device_name": "SW1",
            "entity_type": "interface_l3",
            "entity_key": '["GE1/0/2"]',
            "field": "status",
            "value": "administratively down",
            "command": "display interface brief",
        },
    ]

    result = evaluate_current_state(observations, rules, rule_source="test")

    assert result["evaluated_observation_count"] == 2
    assert result["finding_count"] == 1
    assert result["findings"][0]["event_type"] == "InterfaceDown"
    assert result["findings"][0]["entity_key"] == '["GE1/0/1"]'


def test_current_optical_value_is_evaluated_without_waiting_for_a_change():
    result = evaluate_current_state(
        [
            {
                "id": "obs-rx",
                "device_id": "sw-1",
                "entity_type": "interface_optical",
                "entity_key": '["GE1/0/1"]',
                "field": "rx_power_dbm",
                "value": -18.2,
                "unit": "dBm",
                "command": "display transceiver diagnosis interface",
            }
        ],
        [
            {
                "rule_id": "optical-rx-low",
                "event_type": "OpticalRxPowerLow",
                "severity": "warning",
                "event_class": "fault",
                "condition": {
                    "kind": "threshold_lte",
                    "entity_types": ["interface_optical"],
                    "fields": ["rx_power_dbm"],
                    "lte": -17,
                },
            }
        ],
        rule_source="frozen_analysis_attempt",
        rule_bundle_digest="abc",
    )

    assert result["finding_count"] == 1
    assert result["findings"][0]["value"] == -18.2
    assert result["findings"][0]["rule_bundle_digest"] == "abc"


def test_current_state_evaluates_list_lanes_as_independent_dimensions():
    result = evaluate_current_state(
        [
            {
                "id": "obs-lane-1",
                "device_id": "sw-1",
                "entity_type": "interface_optical",
                "entity_key": '["GE1/0/1"]',
                "field": "rx_power_dbm",
                "value": -18.2,
                "dimensions": {"interface": "GE1/0/1", "value_index": 1},
                "command": "display transceiver diagnosis interface",
            },
            {
                "id": "obs-lane-2",
                "device_id": "sw-1",
                "entity_type": "interface_optical",
                "entity_key": '["GE1/0/1"]',
                "field": "rx_power_dbm",
                "value": -2.1,
                "dimensions": {"interface": "GE1/0/1", "value_index": 2},
                "command": "display transceiver diagnosis interface",
            },
        ],
        [
            {
                "rule_id": "optical-rx-low",
                "event_type": "OpticalRxPowerLow",
                "severity": "major",
                "event_class": "fault",
                "condition": {
                    "kind": "threshold_lte",
                    "entity_types": ["interface_optical"],
                    "fields": ["rx_power_dbm"],
                    "lte": -17,
                },
            }
        ],
        rule_source="test",
    )

    assert result["evaluated_observation_count"] == 2
    assert result["finding_count"] == 1
    assert result["findings"][0]["dimensions"]["value_index"] == 1


def test_domain_checks_keep_list_lanes_as_independent_observations():
    observations = [
        {
            "entity_type": "interface_optical",
            "entity_key": '["GE1/0/1"]',
            "field": "rx_power_dbm",
            "value": -18.2,
            "dimensions": {"interface": "GE1/0/1", "value_index": 1},
            "command": "display transceiver diagnosis interface",
        },
        {
            "entity_type": "interface_optical",
            "entity_key": '["GE1/0/1"]',
            "field": "rx_power_dbm",
            "value": -2.1,
            "dimensions": {"interface": "GE1/0/1", "value_index": 2},
            "command": "display transceiver diagnosis interface",
        },
    ]

    checks = domain_checks(
        observations=observations,
        command_details=[],
        changes=[],
        events=[],
        current_findings=[],
        rule_coverage={
            "available": True,
            "by_domain": {
                "optical": {
                    "evaluated_observation_count": 2,
                    "threshold_unknown_observation_count": 0,
                    "partial_threshold_observation_count": 0,
                }
            },
        },
    )

    optical = next(item for item in checks if item["domain"] == "optical")
    assert optical["observation_count"] == 2
    assert optical["evaluated_observation_count"] == 2


def test_threshold_coverage_text_distinguishes_partial_from_unknown():
    text = _domain_threshold_coverage_text(
        {
            "label": "光模块与光功率",
            "observation_count": 164,
            "evaluated_observation_count": 152,
            "partial_threshold_observation_count": 148,
            "threshold_unknown_observation_count": 12,
        }
    )

    assert text is not None
    assert "152 条可按设备阈值评估" in text
    assert "148 条仅具备部分阈值层级" in text
    assert "12 条无可用设备阈值" in text


def test_current_optical_value_uses_device_reference_threshold():
    result = evaluate_current_state(
        [
            {
                "id": "obs-rx-reference",
                "device_id": "sw-1",
                "entity_type": "transceiver_diagnostic",
                "entity_key": '["Twenty-FiveGigE1/0/42"]',
                "field": "channel_rx_power_dbm",
                "observation_type": "interface.optical.rx_power",
                "value": -36.96,
                "unit": "dBm",
                "context": {"alarm_low_rx_power_dbm": "-14.10"},
            }
        ],
        [
            {
                "rule_id": "interface-optical-rx-power.low",
                "event_type": "transceiver_rx_power_low",
                "severity": "major",
                "event_class": "fault",
                "condition": {
                    "kind": "threshold_field_lte",
                    "entity_types": ["transceiver_diagnostic"],
                    "fields": ["channel_rx_power_dbm"],
                    "reference_field": "alarm_low_rx_power_dbm",
                },
            }
        ],
        rule_source="frozen_analysis_attempt",
    )

    assert result["finding_count"] == 1
    assert result["findings"][0]["value"] == -36.96
    assert result["findings"][0]["condition"]["reference_field"] == (
        "alarm_low_rx_power_dbm"
    )
    assert result["findings"][0]["reference_value"] == -14.1


def test_current_optical_state_reports_unknown_when_device_threshold_is_missing():
    observation = {
        "id": "obs-rx-no-threshold",
        "device_id": "sw-1",
        "entity_type": "interface_optical",
        "entity_key": '["Ten-GigabitEthernet1/0/49"]',
        "field": "rx_power_dbm",
        "observation_type": "interface.optical.rx_power",
        "value": -12.0,
        "unit": "dBm",
        "command": "display transceiver diagnosis interface",
        "context": {},
    }
    rules = [
        {
            "rule_id": "interface-optical-rx-power.low",
            "event_type": "OpticalRxPowerLow",
            "severity": "warning",
            "event_class": "fault",
            "condition": {
                "kind": "threshold_field_lte",
                "entity_types": ["interface_optical"],
                "fields": ["rx_power_dbm"],
                "reference_field": "rx_low_warning_dbm",
            },
        }
    ]

    assessment = evaluate_current_state([observation], rules, rule_source="test")
    checks = domain_checks(
        observations=[observation],
        command_details=[],
        changes=[],
        events=[],
        current_findings=assessment["findings"],
        rule_coverage=assessment,
    )

    assert assessment["evaluated_observation_count"] == 0
    assert assessment["threshold_unknown_observation_count"] == 1
    assert assessment["partial_threshold_observation_count"] == 0
    assert assessment["finding_count"] == 0
    optical = next(item for item in checks if item["domain"] == "optical")
    assert optical["status"] == "数据不完整"
    assert "不能据此判定健康" in optical["reason"]


def test_alarm_only_optic_is_partially_evaluated_without_claiming_health():
    result = evaluate_current_state(
        [
            {
                "id": "obs-rx-alarm-only",
                "device_id": "sw-1",
                "entity_type": "interface_optical",
                "entity_key": '["Ten-GigabitEthernet1/0/49"]',
                "field": "rx_power_dbm",
                "observation_type": "interface.optical.rx_power",
                "value": -12.0,
                "command": "display transceiver diagnosis interface",
                "context": {"rx_low_alarm_dbm": -16.99},
            }
        ],
        [
            {
                "rule_id": "interface-optical-rx-power.low",
                "event_type": "OpticalRxPowerLow",
                "severity": "warning",
                "event_class": "fault",
                "condition": {
                    "kind": "threshold_field_lte",
                    "entity_types": ["interface_optical"],
                    "fields": ["rx_power_dbm"],
                    "reference_field": "rx_low_warning_dbm",
                },
            },
            {
                "rule_id": "interface-optical-rx-power.critical-low",
                "event_type": "OpticalRxPowerCritical",
                "severity": "critical",
                "event_class": "fault",
                "condition": {
                    "kind": "threshold_field_lte",
                    "entity_types": ["interface_optical"],
                    "fields": ["rx_power_dbm"],
                    "reference_field": "rx_low_alarm_dbm",
                },
            },
        ],
        rule_source="test",
    )

    assert result["evaluated_observation_count"] == 1
    assert result["threshold_unknown_observation_count"] == 0
    assert result["partial_threshold_observation_count"] == 1
    assert result["finding_count"] == 0

    checks = domain_checks(
        observations=[
            {
                "entity_type": "interface_optical",
                "entity_key": '["Ten-GigabitEthernet1/0/49"]',
                "field": "rx_power_dbm",
                "value": -12.0,
                "command": "display transceiver diagnosis interface",
            }
        ],
        command_details=[],
        changes=[],
        events=[],
        current_findings=result["findings"],
        rule_coverage=result,
    )
    optical = next(item for item in checks if item["domain"] == "optical")
    assert optical["status"] == "需复核"
    assert "已按可用设备阈值完成部分判定" in optical["reason"]


def test_leadership_report_never_claims_health_for_partial_scope():
    report = build_leadership_report("batch-partial", _summary(completed=1, target=2))

    assert report["assessment"]["code"] == "partial"
    assert report["assessment"]["conclusion_allowed"] is False
    assert "未发现已确认异常" in report["assessment"]["status"]


def test_leadership_report_marks_current_fault_and_renders_valid_docx():
    finding = {
        "event_type": "OpticalRxPowerLow",
        "severity": "major",
        "device_id": "sw-1",
        "device_name": "SW1",
        "domain": "optical",
        "entity_type": "interface_optical",
        "entity_key": '["GE1/0/1"]',
        "field": "rx_power_dbm",
        "value": -19.1,
        "unit": "dBm",
        "rule_id": "optical-rx-low",
        "observation_id": "obs-1",
    }
    report = build_leadership_report(
        "batch-risk",
        _summary(findings=[finding]),
        generated_at=datetime(2026, 7, 14, tzinfo=timezone.utc),
    )
    content = render_leadership_docx(report)
    document = Document(io.BytesIO(content))
    text = "\n".join(paragraph.text for paragraph in document.paragraphs)

    assert report["assessment"]["code"] == "abnormal"
    assert report["risk_summary"]["total"] == 1
    assert report["metrics"]["current_state_finding_count"] == 1
    assert report["metrics"]["unresolved_fault_event_count"] == 0
    assert report["metrics"]["current_risk_count"] == 1
    assert content.startswith(b"PK")
    assert "发现重大网络风险" in text
    assert "巡检流程执行完成" in text
    assert round(document.sections[0].page_width.cm, 1) == 21.0
    assert round(document.sections[0].page_height.cm, 1) == 29.7


def test_leadership_report_keeps_independent_lanes_as_distinct_risks():
    findings = [
        {
            "event_type": "OpticalRxPowerLow",
            "severity": "major",
            "device_id": "sw-1",
            "device_name": "SW1",
            "domain": "optical",
            "entity_type": "interface_optical",
            "entity_key": '["HGE1/0/1"]',
            "field": "rx_power_dbm",
            "value": value,
            "unit": "dBm",
            "rule_id": "optical-rx-low",
            "dimensions": {"interface": "HGE1/0/1", "value_index": lane},
        }
        for lane, value in ((1, -18.1), (2, -19.2))
    ]

    report = build_leadership_report("batch-lanes", _summary(findings=findings))

    assert report["risk_summary"]["total"] == 2
    assert report["risk_groups"][0]["count"] == 2
    assert "通道 1" in report["risk_groups"][0]["objects"]
    assert "通道 2" in report["risk_groups"][0]["objects"]


def test_leadership_report_recovers_missing_parser_eligible_denominator():
    summary = _summary()
    summary["quality"].update(
        {
            "parser_ready_count": 8,
            "template_missing_count": 1,
            "parse_failed_count": 1,
        }
    )

    report = build_leadership_report("batch-parser-denominator", summary)

    assert report["metrics"]["parser_eligible_count"] == 10


def test_leadership_report_uses_frozen_analysis_time_and_is_reproducible():
    completed_at = datetime(2026, 7, 15, 11, 1, 1, tzinfo=timezone.utc)
    summary = _summary()
    summary["analysis"] = {
        "attempt_id": "analysis-immutable",
        "revision": "acceptance:immutable",
        "completed_at": completed_at.isoformat(),
    }

    first_report = build_leadership_report("batch-immutable", summary)
    second_report = build_leadership_report("batch-immutable", summary)
    first_content = render_leadership_docx(first_report)
    second_content = render_leadership_docx(second_report)

    assert datetime.fromisoformat(first_report["generated_at"]) == completed_at
    assert first_report == second_report
    assert first_content == second_content
    with zipfile.ZipFile(io.BytesIO(first_content)) as archive:
        assert {item.date_time for item in archive.infolist()} == {
            (1980, 1, 1, 0, 0, 0)
        }


def test_leadership_report_does_not_double_count_optical_signal_on_down_interface():
    summary = _summary()
    summary["runs"] = [
        {
            "device_id": "sw-1",
            "observations": [
                {
                    "device_id": "sw-1",
                    "entity_type": "interface",
                    "entity_key": '["WGE1/0/15"]',
                    "field": "link",
                    "observation_type": "interface.oper_status",
                    "value": "DOWN",
                }
            ],
        }
    ]
    summary["current_findings"] = [
        {
            "event_type": "OpticalRxPowerCritical",
            "severity": "critical",
            "device_id": "sw-1",
            "device_name": "SW1",
            "domain": "optical",
            "entity_type": "interface_optical",
            "entity_key": '["Twenty-FiveGigE1/0/15"]',
            "field": "rx_power_dbm",
            "value": -36.96,
            "unit": "dBm",
            "rule_id": "optical-rx-critical",
        }
    ]
    summary["events"] = [
        {
            "event_type": "InterfaceDown",
            "severity": "major",
            "event_class": "fault",
            "device_id": "sw-1",
            "device_name": "SW1",
            "domain": "interface",
            "entity_type": "interface",
            "entity_key": '["WGE1/0/15"]',
            "field": "status",
            "current_value": "DOWN",
            "rule_id": "interface-down",
        }
    ]
    summary["event_count"] = 1

    report = build_leadership_report("batch-correlated", summary)

    assert report["risk_summary"]["total"] == 1
    assert report["risk_summary"]["correlated_evidence_count"] == 1
    assert report["risks"][0]["event_type"] == "InterfaceDown"
    assert report["correlated_evidence"][0]["event_type"] == ("OpticalRxPowerCritical")
    assert report["assessment"]["status"] == "发现重大网络风险"


def test_recovery_facts_are_not_current_risks_and_quality_gaps_are_disclosed():
    summary = _summary(quality="degraded")
    summary["quality"].update(
        {
            "parser_eligible_count": 26,
            "parser_ready_count": 22,
            "parser_ready_rate": 22 / 26,
            "template_missing_count": 4,
            "parse_failed_count": 0,
        }
    )
    summary["events"] = [
        {
            "event_type": "interface_link_down",
            "severity": "major",
            "event_class": "fault",
            "status_after_occurrence": "open",
            "device_id": "sw-1",
            "device_name": "SW1",
            "domain": "interface",
            "entity_type": "interface_brief",
            "entity_key": '["WGE1/0/15"]',
            "field": "link",
            "previous_value": "UP",
            "current_value": "DOWN",
            "rule_id": "rule-down",
            "condition": {
                "kind": "transition",
                "from_group": "up",
                "to_group": "down",
            },
            "evidence_chain": {
                "current_snapshot_id": "snapshot-current",
                "current_observation_id": "observation-current",
                "change_id": "change-1",
                "change_policy_id": "policy-1",
                "event_rule_id": "rule-down",
                "event_id": "event-1",
            },
        },
        {
            "event_type": "interface_link_up",
            "severity": "info",
            "event_class": "recovery",
            "status_after_occurrence": "resolved",
            "device_id": "sw-1",
            "device_name": "SW1",
            "domain": "interface",
            "entity_type": "interface_brief",
            "entity_key": '["WGE1/0/16"]',
            "field": "link",
            "previous_value": "DOWN",
            "current_value": "UP",
            "rule_id": "rule-up",
        },
    ]
    summary["event_count"] = 2

    report = build_leadership_report("batch-recovery", summary)
    document = Document(io.BytesIO(render_leadership_docx(report)))
    text = "\n".join(paragraph.text for paragraph in document.paragraphs)

    assert report["risk_summary"]["total"] == 1
    assert report["event_summary"]["recovery_fact_count"] == 1
    assert report["metrics"]["template_missing_count"] == 4
    assert report["assessment"]["code"] == "abnormal_partial"
    assert any("4 条命令缺少 Parser 模板" in item for item in report["limitations"])
    assert report["evidence"]["risk_chains"][0]["chain"]["change_id"] == "change-1"
    assert "恢复类 1 条" in text
    assert "不得外推为全网健康结论" in text


def test_transceiver_diagnostic_events_are_classified_as_optical():
    assert report_domain("transceiver_diagnostic", "rx_power_low") == "optical"


def test_three_period_trend_uses_frozen_analysis_and_classifies_evidence():
    summary = _summary()
    summary["quality"]["command_count"] = 54
    summary["change_count"] = 465
    summary["analysis"] = {
        "attempt_id": "analysis-q2-q3",
        "revision": "acceptance:q2-q3",
        "source_change_count": 465,
        "rule_bundle_digest": "event-rule-bundle-q2-q3",
    }
    summary["periods"] = [
        {"role": "baseline", "observed_at": "2026-04-30T00:00:00+00:00"},
        {"role": "current", "observed_at": "2026-06-26T00:00:00+00:00"},
    ]
    summary["previous_period"] = {
        "continuity_evidence": "sha256_exact_match",
        "periods": [
            {"role": "baseline", "observed_at": "2026-03-23T00:00:00+00:00"},
            {"role": "current", "observed_at": "2026-04-30T00:00:00+00:00"},
        ],
        "metrics": {"command_count": 44, "parser_ready_rate": 1.0},
        "change_count": 81,
        "event_count": 4,
        "analysis": {
            "attempt_id": "analysis-q1-q2",
            "revision": "acceptance:q1-q2",
        },
        "risks": [
            {
                "event_type": "interface_link_down",
                "severity": "major",
                "device_id": "sw-1",
                "device_name": "SW1",
                "domain": "interface",
                "entity_type": "interface_brief",
                "entity_key": '["WGE1/0/16"]',
                "field": "link",
                "current_value": "DOWN",
            },
            {
                "event_type": "transceiver_rx_power_low",
                "severity": "major",
                "device_id": "sw-1",
                "device_name": "SW1",
                "domain": "optical",
                "entity_type": "transceiver_diagnostic",
                "entity_key": '["Twenty-FiveGigE1/0/16"]',
                "field": "channel_rx_power_dbm",
                "current_value": -36.96,
                "unit": "dBm",
            },
            {
                "event_type": "transceiver_rx_power_low",
                "severity": "major",
                "device_id": "sw-1",
                "device_name": "SW1",
                "domain": "optical",
                "entity_type": "transceiver_diagnostic",
                "entity_key": '["Twenty-FiveGigE1/0/42"]',
                "field": "channel_rx_power_dbm",
                "current_value": -36.96,
                "unit": "dBm",
            },
        ],
        "correlated_evidence": [],
        "events": [],
    }
    summary["current_findings"] = [
        {
            "event_type": "transceiver_rx_power_low",
            "severity": "major",
            "device_id": "sw-1",
            "device_name": "SW1",
            "domain": "optical",
            "entity_type": "transceiver_diagnostic",
            "entity_key": '["Twenty-FiveGigE1/0/42"]',
            "field": "channel_rx_power_dbm",
            "value": -36.96,
            "unit": "dBm",
            "rule_id": "rx-low",
        },
        {
            "event_type": "transceiver_rx_power_low",
            "severity": "major",
            "device_id": "sw-1",
            "device_name": "SW1",
            "domain": "optical",
            "entity_type": "transceiver_diagnostic",
            "entity_key": '["Twenty-FiveGigE1/0/15"]',
            "field": "channel_rx_power_dbm",
            "value": -36.96,
            "unit": "dBm",
            "rule_id": "rx-low",
        },
    ]
    summary["events"] = [
        {
            "event_type": "interface_link_down",
            "severity": "major",
            "event_class": "fault",
            "status_after_occurrence": "open",
            "device_id": "sw-1",
            "device_name": "SW1",
            "domain": "interface",
            "entity_type": "interface_brief",
            "entity_key": '["WGE1/0/15"]',
            "field": "link",
            "current_value": "DOWN",
            "rule_id": "interface-down",
            "evidence_chain": {
                "analysis_attempt_id": "analysis-q2-q3",
                "analysis_revision": "acceptance:q2-q3",
            },
        },
        {
            "event_type": "interface_link_up",
            "severity": "info",
            "event_class": "recovery",
            "occurrence_kind": "recovery",
            "status_after_occurrence": "resolved",
            "device_id": "sw-1",
            "device_name": "SW1",
            "domain": "interface",
            "entity_type": "interface_brief",
            "entity_key": '["WGE1/0/16"]',
            "field": "link",
            "current_value": "UP",
            "rule_id": "interface-up",
        },
        {
            "event_type": "transceiver_rx_power_low_recovery",
            "severity": "info",
            "event_class": "recovery",
            "occurrence_kind": "recovery",
            "status_after_occurrence": "resolved",
            "device_id": "sw-1",
            "device_name": "SW1",
            "domain": "optical",
            "entity_type": "transceiver_diagnostic",
            "entity_key": '["Twenty-FiveGigE1/0/16"]',
            "field": "channel_rx_power_dbm",
            "current_value": -2.89,
            "unit": "dBm",
            "reference_field": "alarm_low_rx_power_dbm",
            "reference_value": -14.1,
            "rule_id": "rx-recovered",
        },
    ]
    summary["event_count"] = len(summary["events"])

    report = build_leadership_report("batch-q2-q3", summary)
    trend = report["trend"]

    assert trend["available"] is True
    assert trend["continuity_evidence"] == "sha256_exact_match"
    assert trend["period_count"] == 3
    assert [item["command_count"] for item in trend["comparisons"]] == [44, 54]
    assert trend["new_evidence_count"] == 2
    assert trend["persistent_evidence_count"] == 1
    assert trend["resolved_evidence_count"] == 2
    assert {item["event_type"] for item in trend["resolved_evidence_groups"]} == {
        "interface_link_up",
        "transceiver_rx_power_low_recovery",
    }
    assert report["event_summary"]["resolved_prior_fault_count"] == 2
    assert report["event_summary"]["state_improvement_count"] == 0
    assert report["evidence"]["analysis"] == summary["analysis"]
    assert report["evidence"]["previous_analysis"] == {
        "attempt_id": "analysis-q1-q2",
        "revision": "acceptance:q1-q2",
    }
