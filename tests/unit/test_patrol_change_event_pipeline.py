# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from src.common.config import get_settings
from src.core.celery_tasks.tasks import _persist_patrol_snapshots
from src.core.parser.runtime import ParserRuntimeResult
from src.core.patrol.change_detector import (
    NetworkChange,
    _aggregate_collection_results,
    _deduplicate_current_observations,
    _detect_observation_changes_against_history_impl,
    _detect_scoped_observation_changes_impl,
    _observation_dimensions,
    detect_changes_between_values,
    detect_changes_from_params,
)
from src.core.patrol.change_event_repository import ChangeEventRepository
from src.core.patrol.change_policy import (
    ChangePolicyProposal,
    compare_with_policy,
    validate_change_policy,
)
from src.core.patrol.command_splitter import split_cli_capture
from src.core.patrol.event_builder import build_events_from_changes, build_events_from_params
from src.core.patrol.event_rules import EventRule, default_event_rule_definitions
from src.core.patrol.raw_importer import (
    _import_fingerprint,
    _resolve_capture_device,
    build_output_review_item_id,
    import_raw_capture,
    import_raw_path,
)
from src.core.patrol.repository import (
    PatrolRepository,
    PatrolSnapshotRepository,
)
from src.core.state.normalizer import normalize_run
from src.infrastructure.db.models import (
    CommandSnapshot,
    NetworkChangeRecord,
    NetworkDevice,
    NetworkEventOccurrenceRecord,
    NetworkEventOutboxRecord,
    NetworkEventRecord,
    ParserJobSample,
    ParserOutboxEvent,
    ParserTemplateJob,
    PatrolCollectionResult,
    PatrolDataset,
    PatrolRun,
    RawEvidenceRecord,
)
from src.infrastructure.db.postgres import get_db_session

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_observation_change_preserves_runtime_dimensions():
    assert _observation_dimensions(
        {
            "entity_key": '["slot","0/0"]',
            "dimensions_json": {"window": "5m"},
            "metadata_json": {"primary_keys": ["scope", "scope_id"]},
        }
    ) == {"window": "5m"}


@pytest.fixture(autouse=True)
def _enable_explicit_builtin_rule_test_interface(monkeypatch):
    monkeypatch.setattr(get_settings(), "EVENT_RULE_ALLOW_BUILTIN_FALLBACK", True)
    monkeypatch.setattr(
        "src.core.patrol.event_rules.EventRuleRepository.list_active_rules",
        lambda _self: [],
    )
    monkeypatch.setattr(
        "src.core.patrol.event_builder.EventRuleRepository.snapshot_active_rules",
        lambda _self: [
            {
                "rule_name": definition.rule_id,
                "version": definition.version,
                "event_type": definition.event_type,
                "severity": definition.severity,
                "condition": dict(definition.condition),
                "priority": definition.priority,
                "exclusive": definition.exclusive,
                "event_class": definition.event_class,
                "recovery": dict(definition.recovery or {}),
                "correlation_keys": list(definition.correlation_keys),
            }
            for definition in default_event_rule_definitions()
        ],
    )


def _cleanup_runs(*run_ids: str) -> None:
    with get_db_session() as db:
        snapshots = db.query(CommandSnapshot).filter(CommandSnapshot.run_id.in_(run_ids)).all()
        snapshot_ids = [row.id for row in snapshots]
        dataset_ids = {row.dataset_id for row in snapshots if row.dataset_id}
        job_ids = {
            row[0]
            for row in db.query(ParserJobSample.job_id)
            .filter(ParserJobSample.snapshot_id.in_(snapshot_ids))
            .all()
        }
        if snapshot_ids:
            db.query(ParserJobSample).filter(ParserJobSample.snapshot_id.in_(snapshot_ids)).delete(
                synchronize_session=False
            )
        for job_id in job_ids:
            job = db.get(ParserTemplateJob, job_id)
            if job is None:
                continue
            remaining = db.query(ParserJobSample).filter(ParserJobSample.job_id == job_id).count()
            job.sample_count = remaining
            if remaining == 0:
                db.query(ParserOutboxEvent).filter(ParserOutboxEvent.aggregate_id == job_id).delete(
                    synchronize_session=False
                )
                db.delete(job)
        db.query(NetworkEventRecord).filter(NetworkEventRecord.run_id.in_(run_ids)).delete(
            synchronize_session=False
        )
        db.query(NetworkChangeRecord).filter(NetworkChangeRecord.run_id.in_(run_ids)).delete(
            synchronize_session=False
        )
        db.query(CommandSnapshot).filter(CommandSnapshot.run_id.in_(run_ids)).delete(
            synchronize_session=False
        )
        db.query(PatrolRun).filter(PatrolRun.id.in_(run_ids)).delete(synchronize_session=False)
        for dataset_id in dataset_ids:
            if (
                not db.query(CommandSnapshot.id)
                .filter(CommandSnapshot.dataset_id == dataset_id)
                .first()
            ):
                db.query(PatrolDataset).filter(PatrolDataset.id == dataset_id).delete(
                    synchronize_session=False
                )


H3C_SAMPLE = """[BEGIN] 2025/12/24 14:46:42
<SW1>
<SW1>dis
<SW1>display ver
<SW1>display version
H3C Comware Software, Version 7.1.070
<SW1>
<SW1>dis cpu
<SW1>dis cpu-usage
Slot 1 CPU usage:
      5% in last 5 seconds
"""


CISCO_SAMPLE = """[BEGIN] 2025/12/24 16:24:18
SW2#terminal length 0
SW2#show version
Cisco IOS Software
SW2#show interface status | include error
SW2#show interface status
Port      Name               Status       Vlan       Duplex  Speed Type
Gi1/0/1                      connected    1          a-full  a-1000 10/100/1000BaseTX
SW2#show processes cpu
CPU utilization for five seconds: 24%/0%; one minute: 25%; five minutes: 25%
"""

REPORT_SAMPLE = """命令: terminal length 0
输出:

--------------------------------------------------
命令: show version
输出:
Cisco IOS Software

--------------------------------------------------
命令: show processes cpu
输出:
CPU utilization for five seconds: 24%/0%; one minute: 25%; five minutes: 25%
"""


def test_split_h3c_ignores_completion_fragments():
    blocks = split_cli_capture(H3C_SAMPLE)

    assert [b.command_canonical for b in blocks] == ["display version", "display cpu-usage"]
    assert "H3C Comware" in blocks[0].raw_output


def test_split_cisco_drops_empty_pipe_probe():
    blocks = split_cli_capture(CISCO_SAMPLE)

    assert [b.command_canonical for b in blocks] == [
        "show version",
        "show interface status",
        "show processes cpu",
    ]


def test_split_command_report_format():
    blocks = split_cli_capture(REPORT_SAMPLE, device_name="SW3")

    assert [b.command_canonical for b in blocks] == [
        "show version",
        "show processes cpu",
    ]
    assert blocks[0].prompt_style == "command_report"


def test_split_ascii_command_report_format():
    blocks = split_cli_capture(
        "Command: show version\nOutput:\nCisco IOS Software\n"
        "--------------------------------------------------\n"
    )

    assert len(blocks) == 1
    assert blocks[0].command_canonical == "show version"
    assert blocks[0].raw_output == "Cisco IOS Software"


def test_split_command_report_preserves_executed_empty_output():
    blocks = split_cli_capture("命令: display vrf\n输出:\n", device_name="SW3")

    assert len(blocks) == 1
    assert blocks[0].command_canonical == "display vrf"
    assert blocks[0].raw_output == ""


def test_split_command_report_uses_embedded_prompt_boundaries():
    blocks = split_cli_capture(
        "Command: show switch-profile\n"
        "Output:\n\n"
        "----------------------------------------\n"
        "Command: show vpc\n"
        "Output:\n\n"
        "----------------------------------------\n"
        "Command: show running-config\n"
        "Output:\n\n"
        "N9K-1# show switch-profile\n"
        "N9K-1# show vpc\n"
        "vPC domain id : 300\n"
        "Peer status : peer adjacency formed ok\n"
        "N9K-1# show running-config\n"
        "version 9.3(8)\n"
        "hostname N9K-1\n",
        device_name="N9K-1",
    )

    assert [block.command_canonical for block in blocks] == [
        "show switch-profile",
        "show vpc",
        "show running-config",
    ]
    assert blocks[0].raw_output == ""
    assert "vPC domain id : 300" in blocks[1].raw_output
    assert "Peer status" in blocks[1].raw_output
    assert blocks[2].raw_output == "version 9.3(8)\nhostname N9K-1"
    assert "vPC domain" not in blocks[2].raw_output


def test_change_detector_field_changes():
    changes = detect_changes_between_values(
        [{"interface": "Gi1/0/1", "status": "up", "protocol": "up"}],
        [{"interface": "Gi1/0/1", "status": "up", "protocol": "down"}],
        entity_type="interface_l3",
        primary_keys=["interface"],
        device_id="SW2-10.0.0.2",
        command="show ip interface brief",
    )

    assert len(changes) == 1
    assert changes[0].field == "protocol"
    assert changes[0].old_value == "up"
    assert changes[0].new_value == "down"


def test_change_detector_ignores_status_case_noise():
    changes = detect_changes_between_values(
        [{"interface": "Vlan1", "status": "up", "protocol": "down"}],
        [{"interface": "Vlan1", "status": "UP", "protocol": "DOWN"}],
        entity_type="interface_l3",
        primary_keys=["interface"],
        device_id="SW1",
        command="display interface brief",
    )

    assert changes == []


def test_change_detector_treats_ip_with_unknown_prefix_as_same_host():
    changes = detect_changes_between_values(
        [{"interface": "GE1/0", "ip_address": "10.10.1.1/24"}],
        [{"interface": "GE1/0", "ip_address": "10.10.1.1"}],
        entity_type="interface_l3",
        primary_keys=["interface"],
    )

    assert changes == []


def test_change_detector_uses_latest_fact_for_duplicate_state_key():
    common = {
        "device_id": "device-1",
        "entity_type": "interface_l3",
        "entity_key": '["GE1/0"]',
        "field_name": "ip_address",
    }
    observations = _deduplicate_current_observations(
        [
            {
                **common,
                "value_json": "10.10.1.1",
                "observed_at": "2026-07-12T03:00:00+08:00",
                "fact_order_key": "10|brief",
            },
            {
                **common,
                "value_json": "10.10.1.1/24",
                "observed_at": "2026-07-12T03:00:01+08:00",
                "fact_order_key": "14|ip-brief",
            },
        ]
    )

    assert len(observations) == 1
    assert observations[0]["value_json"] == "10.10.1.1/24"


def test_change_detector_reconstructs_dimensioned_keys_from_persisted_metadata():
    common = {
        "device_id": "device-1",
        "entity_type": "measurement_group",
        "entity_key": '["group-a"]',
        "field_name": "readings",
        "observation_type": "measurement.reading",
        "observed_at": "2026-07-14T00:00:00+00:00",
    }

    observations = _deduplicate_current_observations(
        [
            {
                **common,
                "metadata_json": {"dimensions": {"name": "group-a", "value_index": 1}},
                "value_json": 1.5,
            },
            {
                **common,
                "metadata_json": {"dimensions": {"name": "group-a", "value_index": 2}},
                "value_json": 2.5,
            },
        ]
    )

    assert [item["value_json"] for item in observations] == [1.5, 2.5]


def test_duplicate_collection_items_have_distinct_fact_identity_and_safe_authority():
    common = {
        "mode": "production",
        "dataset_id": "dataset-1",
        "source_file_hash": "source-hash",
        "device_id": "SW1",
        "canonical_command": "show interface transceiver detail",
        "raw_text": "same output",
        "run_id": "run-1",
        "inventory_device_id": "device-1",
        "command_observed_at": "2026-07-11T01:00:00+00:00",
        "command_ordinal": 1,
    }
    before = _import_fingerprint(**common, execution_item_id="optical-before")
    after = _import_fingerprint(**common, execution_item_id="optical-after")

    assert before != after

    aggregated = _aggregate_collection_results(
        [
            {
                "device_id": "device-1",
                "canonical_command": common["canonical_command"],
                "execution_item_id": "optical-before",
                "authoritative_for_deletion": True,
                "observed_at": "2026-07-11T01:00:00+00:00",
            },
            {
                "device_id": "device-1",
                "canonical_command": common["canonical_command"],
                "execution_item_id": "optical-after",
                "authoritative_for_deletion": False,
                "observed_at": "2026-07-11T01:01:00+00:00",
            },
        ]
    )
    item = next(iter(aggregated.values()))
    assert item["authoritative_for_deletion"] is False
    assert item["execution_item_ids"] == ["optical-before", "optical-after"]


def test_observation_change_detector_emits_initial_changes(monkeypatch):
    current = [
        {
            "id": "obs-1",
            "run_id": "run-current",
            "snapshot_id": "snapshot-1",
            "device_id": "SW1",
            "canonical_command": "display cpu",
            "entity_type": "cpu",
            "entity_key": "slot1",
            "field_name": "cpu_5s",
            "value_json": 98,
            "value_number": 98,
            "confidence": 1.0,
        }
    ]

    class FakeStateRepository:
        def list_run_observations(self, run_id, *, device_ids=None):
            return current

        def list_prior_observations_for_run(self, run_id):
            return []

    monkeypatch.setattr(
        "src.core.patrol.change_detector.StateRepository",
        FakeStateRepository,
    )
    monkeypatch.setattr(
        "src.core.patrol.change_detector.save_changes",
        lambda changes, **_kwargs: len(changes),
    )

    PatrolRepository().create_run(run_id="run-current", source="test", mode="production")
    try:
        result = detect_changes_from_params(
            {
                "current_run_id": "run-current",
                "use_state_observations": True,
                "persist": True,
            }
        )
    finally:
        _cleanup_runs("run-current")

    assert result["baseline_observations"] == 1
    assert result["initial_observations"] == 1
    assert result["change_count"] == 1
    assert result["changes"][0]["change_type"] == "initial"
    assert result["changes"][0]["new"] == 98


def test_observation_change_detector_suppresses_first_observation_for_frozen_action():
    current = [
        {
            "id": "observation-1",
            "run_id": "run-current",
            "snapshot_id": "snapshot-current",
            "device_id": "device-1",
            "canonical_command": "display cpu",
            "entity_type": "cpu_scope",
            "entity_key": "0",
            "field_name": "cpu_5m",
            "observation_type": "device.cpu.utilization",
            "value_json": 91,
            "observed_at": "2026-08-01T00:00:00+00:00",
            "metadata_json": {"collection_item_id": "action-1"},
        }
    ]

    result = _detect_observation_changes_against_history_impl(
        current_run_id="run-current",
        persist=False,
        current_observations=current,
        prior_observations=[],
        suppress_initial_collection_item_ids=["action-1"],
    )

    assert result["change_count"] == 0
    assert result["initial_observations"] == 0
    assert result["suppressed_initial_observations"] == 1


def test_check_item_change_uses_only_frozen_baseline_and_scope(monkeypatch):
    monkeypatch.setattr(
        PatrolRepository,
        "get_run",
        lambda _self, _run_id: {"run_purpose": "production_current"},
    )
    monkeypatch.setattr(
        PatrolRepository,
        "list_collection_results",
        lambda _self, _run_id: [],
    )
    current = [
        {
            "id": "current-observation",
            "run_id": "current-run",
            "snapshot_id": "current-snapshot",
            "device_id": "device-1",
            "canonical_command": "display cpu-usage",
            "entity_type": "cpu_scope",
            "entity_key": "0",
            "field_name": "cpu_5m",
            "observation_type": "system.cpu.utilization",
            "value_json": 95,
            "value_number": 95,
            "observed_at": "2026-08-06T02:00:00+00:00",
            "metadata_json": {"collection_item_id": "action-1"},
        }
    ]
    prior = [
        {
            **current[0],
            "id": "frozen-observation",
            "run_id": "frozen-run",
            "snapshot_id": "frozen-snapshot",
            "value_json": 20,
            "value_number": 20,
            "observed_at": "2026-08-05T02:00:00+00:00",
        },
        {
            **current[0],
            "id": "unselected-observation",
            "run_id": "newer-run",
            "snapshot_id": "unselected-snapshot",
            "value_json": 94,
            "value_number": 94,
            "observed_at": "2026-08-06T01:00:00+00:00",
        },
    ]
    scopes = [
        {
            "check_item_id": "system.cpu_usage",
            "check_item_version_id": "cpu-version",
            "check_execution_id": "cpu-execution",
            "comparison_context_id": "cpu-context",
            "comparison_context_digest": "c" * 64,
            "comparison_status": "comparable",
            "selection_reason": "latest_compatible_success",
            "baseline_run_id": "frozen-run",
            "baseline_snapshot_ids": ["frozen-snapshot"],
            "collection_action_ids": ["action-1"],
        },
        {
            "check_item_id": "system.cpu_evidence",
            "check_item_version_id": "evidence-version",
            "check_execution_id": "evidence-execution",
            "comparison_context_id": "evidence-context",
            "comparison_context_digest": "d" * 64,
            "comparison_status": "baseline_missing",
            "selection_reason": "prior_facts_failed_qualification",
            "baseline_run_id": None,
            "baseline_snapshot_ids": [],
            "collection_action_ids": ["action-1"],
        },
    ]

    result = _detect_scoped_observation_changes_impl(
        current_run_id="current-run",
        device_ids=["device-1"],
        persist=False,
        missing_confirmations=2,
        current_observations=current,
        prior_observations=prior,
        comparison_scopes=scopes,
        policy_bundles_by_check_item_version_id={
            "cpu-version": [
                {
                    "policy_id": "cpu-policy",
                    "version_id": "cpu-policy-version",
                    "policy": {
                        "observation_type": "system.cpu.utilization",
                        "semantic_type": "gauge",
                        "compare_mode": "exact",
                    },
                }
            ]
        },
    )

    assert result["change_count"] == 1
    assert result["suppressed_comparison_observations"] == 1
    assert result["comparison_scope_status_counts"] == {
        "baseline_missing": 1,
        "comparable": 1,
    }
    assert result["changes"][0]["old"] == 20
    assert result["changes"][0]["new"] == 95
    assert result["changes"][0]["check_item_version_id"] == "cpu-version"
    assert result["changes"][0]["check_execution_id"] == "cpu-execution"


def test_check_item_change_does_not_bypass_empty_runtime_policy_bundle(
    monkeypatch,
):
    monkeypatch.setattr(
        PatrolRepository,
        "list_collection_results",
        lambda _self, _run_id: [],
    )
    current = {
        "id": "current-observation-no-policy",
        "run_id": "current-run-no-policy",
        "snapshot_id": "current-snapshot-no-policy",
        "device_id": "device-no-policy",
        "canonical_command": "display version",
        "entity_type": "device",
        "entity_key": "device-no-policy",
        "field_name": "software_version",
        "observation_type": "device.software.version",
        "value_json": "v2",
        "observed_at": "2026-08-06T02:00:00+00:00",
        "metadata_json": {"collection_item_id": "action-no-policy"},
    }
    prior = {
        **current,
        "id": "prior-observation-no-policy",
        "run_id": "prior-run-no-policy",
        "snapshot_id": "prior-snapshot-no-policy",
        "value_json": "v1",
        "observed_at": "2026-08-05T02:00:00+00:00",
    }

    result = _detect_scoped_observation_changes_impl(
        current_run_id="current-run-no-policy",
        device_ids=["device-no-policy"],
        persist=False,
        missing_confirmations=2,
        current_observations=[current],
        prior_observations=[prior],
        comparison_scopes=[
            {
                "check_item_id": "device.version",
                "check_item_version_id": "device-version",
                "check_execution_id": "device-version-execution",
                "comparison_context_id": "device-version-context",
                "comparison_context_digest": "e" * 64,
                "comparison_status": "comparable",
                "selection_reason": "latest_compatible_success",
                "baseline_run_id": "prior-run-no-policy",
                "baseline_snapshot_ids": ["prior-snapshot-no-policy"],
                "collection_action_ids": ["action-no-policy"],
            }
        ],
        policy_bundles_by_check_item_version_id={"device-version": []},
    )

    assert result["change_count"] == 0
    assert result["suppressed_comparison_observations"] == 1


def test_event_builder_distinguishes_change_from_event():
    small_cpu_change = [
        {
            "change_id": "c1",
            "device_id": "SW1",
            "entity_type": "cpu",
            "entity_key": "slot1",
            "field": "cpu_5s",
            "old": 15,
            "new": 18,
            "change_type": "modified",
        }
    ]
    high_cpu_change = [{**small_cpu_change[0], "change_id": "c2", "new": 95}]
    interface_down = [
        {
            "change_id": "c3",
            "device_id": "SW1",
            "entity_type": "interface_l3",
            "entity_key": "Gi1/0/1",
            "field": "protocol",
            "old": "up",
            "new": "down",
            "change_type": "modified",
        }
    ]

    assert build_events_from_changes(small_cpu_change) == []
    high_cpu_event = build_events_from_changes(high_cpu_change)[0]
    interface_event = build_events_from_changes(interface_down)[0]

    assert high_cpu_event.event_type == "CPUHigh"
    assert high_cpu_event.payload["rule"]["rule_id"] == "cpu-high"
    assert high_cpu_event.payload["rule"]["version"] == 1
    assert high_cpu_event.payload["rule"]["event_class"] == "fault"
    assert high_cpu_event.payload["rule"]["condition"]["kind"] == "threshold"
    assert high_cpu_event.payload["evaluation"] == {
        "condition_kind": "threshold",
        "field": "cpu_5s",
        "previous_value": 15,
        "current_value": 95,
        "unit": None,
        "reference_field": "gte",
        "reference_value": 90.0,
        "reference_source": "event_rule_static",
        "hysteresis": None,
        "recovery_boundary": None,
    }
    assert interface_event.event_type == "InterfaceDown"
    assert interface_event.payload["rule"]["rule_id"] == "interface-down"


def test_event_builder_uses_only_rules_from_change_check_item():
    always_match = EventRule(
        rule_id="always-match",
        version=1,
        event_type="ScopedRisk",
        severity="warning",
        predicate=lambda _change: True,
        condition={"kind": "changed"},
    )
    changes = [
        {
            "change_id": "cpu-change",
            "check_item_id": "system.cpu_usage",
            "check_item_version_id": "cpu-version",
            "check_execution_id": "cpu-execution",
            "device_id": "device-1",
            "entity_type": "cpu_scope",
            "entity_key": "0",
            "field": "cpu_5m",
            "old": 20,
            "new": 95,
            "change_type": "modified",
        },
        {
            "change_id": "evidence-change",
            "check_item_id": "system.cpu_evidence",
            "check_item_version_id": "evidence-version",
            "check_execution_id": "evidence-execution",
            "device_id": "device-1",
            "entity_type": "cpu_scope",
            "entity_key": "0",
            "field": "cpu_5m",
            "old": 20,
            "new": 95,
            "change_type": "modified",
        },
        {
            "change_id": "other-device-change",
            "check_item_id": "system.cpu_usage",
            "check_item_version_id": "cpu-version",
            "check_execution_id": "other-device-execution",
            "device_id": "device-2",
            "entity_type": "cpu_scope",
            "entity_key": "0",
            "field": "cpu_5m",
            "old": 20,
            "new": 95,
            "change_type": "modified",
        },
    ]

    events = build_events_from_changes(
        changes,
        rules=[always_match],
        rules_by_device_and_check_item_version_id={
            "device-1": {
                "cpu-version": [always_match],
                "evidence-version": [],
            },
            "device-2": {"cpu-version": []},
        },
    )

    assert len(events) == 1
    assert events[0].source_change_id == "cpu-change"
    assert events[0].check_item_version_id == "cpu-version"
    assert events[0].check_execution_id == "cpu-execution"
    assert events[0].payload["correlation_dimensions"][
        "check_item_version_id"
    ] == "cpu-version"
    assert events[0].payload["legacy_correlation_key"] == ""


def test_check_item_change_and_event_lineage_round_trip_postgresql():
    run_id = f"lineage-roundtrip-{uuid.uuid4()}"
    device_id = "device-lineage-roundtrip"
    check_item_version_id = "cpu-version-lineage-roundtrip"
    check_execution_id = "cpu-execution-lineage-roundtrip"
    observed_at = datetime.now(ZoneInfo("UTC")).isoformat()
    repository = ChangeEventRepository()
    rule = EventRule(
        rule_id="lineage-roundtrip-rule",
        version=1,
        event_type="ScopedCpuRisk",
        severity="warning",
        predicate=lambda _change: True,
        condition={"kind": "changed"},
    )
    change = NetworkChange(
        change_id="",
        run_id=run_id,
        device_id=device_id,
        command="display cpu-usage",
        entity_type="cpu_scope",
        entity_key="0",
        field="cpu_5m",
        old_value=20,
        new_value=95,
        change_type="modified",
        source="state_observation",
        observed_at=observed_at,
        detected_at=observed_at,
        processed_at=observed_at,
        check_item_id="system.cpu_usage",
        check_item_version_id=check_item_version_id,
        check_execution_id=check_execution_id,
    )

    try:
        assert repository.save_changes([change]) == 1
        persisted_changes = repository.list_changes(run_id=run_id)
        assert len(persisted_changes) == 1
        assert persisted_changes[0]["check_item_id"] == "system.cpu_usage"
        assert (
            persisted_changes[0]["check_item_version_id"]
            == check_item_version_id
        )
        assert (
            persisted_changes[0]["check_execution_id"]
            == check_execution_id
        )

        events = build_events_from_changes(
            persisted_changes,
            rules=[],
            rules_by_device_and_check_item_version_id={
                device_id: {check_item_version_id: [rule]}
            },
        )
        assert len(events) == 1
        assert repository.save_events(events) == 1

        facts = repository.list_run_event_facts(run_id=run_id)
        assert len(facts) == 1
        assert facts[0]["check_item_id"] == "system.cpu_usage"
        assert facts[0]["check_item_version_id"] == check_item_version_id
        assert facts[0]["check_execution_id"] == check_execution_id
    finally:
        _cleanup_runs(run_id)


def test_operator_confirmed_empty_output_is_skipped_without_hiding_raw_evidence(
    tmp_path: Path,
):
    report = tmp_path / "N9K-1.txt"
    report.write_text("命令: show vpc\n输出:\n\n", encoding="utf-8")
    block = split_cli_capture(
        report.read_text(encoding="utf-8"), device_name="N9K-1"
    )[0]
    review_item_id = build_output_review_item_id(
        period_role="sample",
        source_relative_path=report.name,
        device_id="N9K-1",
        canonical_command=block.command_canonical,
        start_line=block.start_line,
        end_line=block.end_line,
        raw_text_hash=hashlib.sha256(block.raw_output.encode("utf-8")).hexdigest(),
    )
    run_id = f"confirmed-empty-{uuid.uuid4()}"
    try:
        result = import_raw_capture(
            file_path=report,
            mode="production",
            device_name="N9K-1",
            vendor="Cisco",
            model="NEXUS9000",
            run_id=run_id,
            collector_complete=True,
            metadata={
                "period_role": "sample",
                "source_relative_path": report.name,
                "output_dispositions": [
                    {
                        "review_item_id": review_item_id,
                        "decision": "ignore_valid_empty",
                        "reason": "设备未配置 vPC",
                        "decided_by": "engineer-1",
                        "decided_at": "2026-08-08T10:00:00+00:00",
                    }
                ],
            },
        )

        assert result["production_parser_ready"] is True
        assert result["parse_violations"] == []
        with get_db_session() as db:
            snapshot = db.query(CommandSnapshot).filter_by(run_id=run_id).one()
            collection = db.query(PatrolCollectionResult).filter_by(run_id=run_id).one()
            assert snapshot.output_status == "empty_unverified"
            assert snapshot.parse_status == "not_applicable"
            assert collection.output_status == "empty_unverified"
            assert collection.parse_status == "not_applicable"
            assert collection.fact_status == "not_applicable"
            assert collection.failure_code is None
            assert collection.baseline_eligible is False
            assert (
                collection.metadata_json["operator_output_disposition"]["review_item_id"]
                == review_item_id
            )
    finally:
        _cleanup_runs(run_id)


def test_event_builder_handles_initial_abnormal_state():
    initial_cpu_normal = [
        {
            "change_id": "initial-cpu-normal",
            "device_id": "SW1",
            "entity_type": "cpu",
            "entity_key": "slot1",
            "field": "cpu_5s",
            "old": None,
            "new": 30,
            "change_type": "initial",
        }
    ]
    initial_cpu_high = [{**initial_cpu_normal[0], "change_id": "initial-cpu-high", "new": 98}]
    initial_interface_down = [
        {
            "change_id": "initial-interface-down",
            "device_id": "SW1",
            "entity_type": "interface_l3",
            "entity_key": "Gi1/0/1",
            "field": "protocol",
            "old": None,
            "new": "down",
            "change_type": "initial",
        }
    ]

    assert build_events_from_changes(initial_cpu_normal) == []
    assert build_events_from_changes(initial_cpu_high)[0].event_type == "CPUHigh"
    assert build_events_from_changes(initial_interface_down)[0].event_type == "InterfaceDown"


def test_event_builder_suppresses_initial_admin_disabled_interface_state():
    initial_disabled = [
        {
            "change_id": "initial-interface-disabled",
            "device_id": "SW1",
            "entity_type": "interface_l2",
            "entity_key": "Gi1/0/2",
            "field": "status",
            "old": None,
            "new": "disabled",
            "change_type": "initial",
        }
    ]

    assert build_events_from_changes(initial_disabled) == []


def test_event_correlation_never_merges_same_entity_across_devices():
    rule = EventRule(
        rule_id="weak-optical-rule",
        version=1,
        event_type="rx_power_low",
        severity="warning",
        predicate=lambda _change: True,
        correlation_keys=("interface_name",),
    )
    changes = [
        {
            "change_id": f"change-{device_id}",
            "device_id": device_id,
            "entity_type": "interface_optical",
            "entity_key": '["Twenty-FiveGigE1/0/16"]',
            "field": "channel_rx_power_dbm",
            "old": -2.0,
            "new": -36.96,
            "change_type": "modified",
            "dimensions": {"interface_name": "Twenty-FiveGigE1/0/16"},
        }
        for device_id in ("SW1", "SW2")
    ]

    events = build_events_from_changes(changes, rules=[rule])

    assert len({event.payload["correlation_key"] for event in events}) == 2
    assert {event.payload["correlation_dimensions"]["device_id"] for event in events} == {
        "SW1",
        "SW2",
    }


def test_change_policy_detects_optical_low_crossing_and_recovery():
    policy = ChangePolicyProposal(
        observation_type="interface.optical.rx_power",
        semantic_type="gauge",
        compare_mode="threshold_low_and_deadband",
        thresholds={"low": -17, "critical_low": -20},
        recovery_threshold=-15,
        deadband=2,
    )

    assert validate_change_policy(policy)["success"] is True
    low = compare_with_policy(policy, old=-14.5, new=-18.1, field="rx_power_dbm")
    recovered = compare_with_policy(policy, old=-18.1, new=-14.8, field="rx_power_dbm")

    assert low["changed"] is True
    assert low["crossed_low"] is True
    assert recovered["changed"] is True
    assert recovered["recovered_low"] is True


def test_event_builder_does_not_emit_initial_recovery_events():
    initial_interface_up = [
        {
            "change_id": "initial-interface-up",
            "device_id": "SW1",
            "entity_type": "interface_l3",
            "entity_key": "Gi1/0/1",
            "field": "protocol",
            "old": None,
            "new": "up",
            "change_type": "initial",
        }
    ]
    initial_bgp_established = [
        {
            "change_id": "initial-bgp-established",
            "device_id": "SW1",
            "command": "display bgp peer",
            "entity_type": "bgp_neighbor",
            "entity_key": "10.0.0.2",
            "field": "state",
            "old": None,
            "new": "Established",
            "change_type": "initial",
        }
    ]

    assert build_events_from_changes(initial_interface_up) == []
    assert build_events_from_changes(initial_bgp_established) == []


def test_event_builder_prefers_power_failed_over_interface_down_for_power_status():
    power_failed = [
        {
            "change_id": "power-failed",
            "device_id": "SW1",
            "entity_type": "power_supply",
            "entity_key": "PWR1",
            "field": "status",
            "old": "normal",
            "new": "failed",
            "change_type": "modified",
        }
    ]

    events = build_events_from_changes(power_failed)

    assert [event.event_type for event in events] == ["PowerFailed"]


def test_event_builder_suppresses_repeated_threshold_high_change():
    repeated_high = [
        {
            "change_id": "cpu-still-high",
            "device_id": "SW1",
            "entity_type": "cpu",
            "entity_key": "slot1",
            "field": "cpu_5s",
            "old": 91,
            "new": 92,
            "change_type": "modified",
        }
    ]

    assert build_events_from_changes(repeated_high) == []


def test_event_repository_dedupes_active_event_and_resolves_recovery():
    run_id = f"test-{uuid.uuid4()}"
    duplicate_high_changes = [
        {
            "change_id": f"change-high-{idx}",
            "run_id": run_id,
            "device_id": "SW1",
            "entity_type": "cpu",
            "entity_key": "slot1",
            "field": "cpu_5s",
            "old": None,
            "new": 98 + idx,
            "change_type": "initial",
        }
        for idx in range(2)
    ]
    recovery_change = [
        {
            "change_id": "change-recovered",
            "run_id": run_id,
            "device_id": "SW1",
            "entity_type": "cpu",
            "entity_key": "slot1",
            "field": "cpu_5s",
            "old": 92,
            "new": 85,
            "change_type": "modified",
        }
    ]
    repository = ChangeEventRepository()
    try:
        high_events = build_events_from_changes(duplicate_high_changes)
        assert len(high_events) == 2
        assert repository.save_events(high_events) == 1

        recovery_events = build_events_from_changes(recovery_change)
        assert [event.event_type for event in recovery_events] == ["CPURecovered"]
        assert repository.save_events(recovery_events) == 0

        lifecycle = repository.summarize_run_events(run_id=run_id)
        assert lifecycle["persisted_occurrence_count"] == 3
        assert lifecycle["created_event_count"] == 1
        assert lifecycle["updated_event_count"] == 1
        assert lifecycle["recovered_event_count"] == 1
        assert lifecycle["unique_event_count"] == 1
        assert lifecycle["open_after_run_count"] == 0
        assert lifecycle["current_open_event_count"] == 0
        assert lifecycle["resolved_event_count"] == 1

        recovery_scope = repository.summarize_run_events(
            run_id=run_id,
            source_change_ids={"change-recovered"},
        )
        assert recovery_scope["persisted_occurrence_count"] == 1
        assert recovery_scope["created_event_count"] == 0
        assert recovery_scope["updated_event_count"] == 0
        assert recovery_scope["recovered_event_count"] == 1
        assert recovery_scope["resolved_after_run_count"] == 1

        with get_db_session() as db:
            opening = (
                db.query(NetworkEventOccurrenceRecord)
                .filter(
                    NetworkEventOccurrenceRecord.run_id == run_id,
                    NetworkEventOccurrenceRecord.occurrence_kind == "opening",
                )
                .one()
            )
            payload = dict(opening.payload_json or {})
            payload.pop("aggregate_status_after_occurrence", None)
            opening.payload_json = payload

        historical_facts = repository.list_run_event_facts(run_id=run_id)
        historical_opening = next(
            item for item in historical_facts if item["occurrence_kind"] == "opening"
        )
        assert historical_opening["status_after_occurrence"] == "open"

        with get_db_session() as db:
            high = (
                db.query(NetworkEventRecord)
                .filter(
                    NetworkEventRecord.run_id == run_id,
                    NetworkEventRecord.event_type == "CPUHigh",
                )
                .one()
            )
            assert (
                db.query(NetworkEventRecord)
                .filter(
                    NetworkEventRecord.run_id == run_id,
                    NetworkEventRecord.event_type == "CPURecovered",
                )
                .count()
                == 0
            )
            assert high.status == "resolved"
            assert high.payload_json["latest_source_change_id"] == "change-high-1"
            assert high.payload_json["recovered_by_event_type"] == "CPURecovered"
            outbox_rows = (
                db.query(NetworkEventOutboxRecord)
                .filter(NetworkEventOutboxRecord.event_id == high.id)
                .all()
            )
            assert len(outbox_rows) == 3
            assert {row.status for row in outbox_rows} == {"pending"}
            publication_kinds = [row.payload_json["publication_kind"] for row in outbox_rows]
            assert publication_kinds.count("opening") == 1
            assert publication_kinds.count("recurrence") == 1
            assert publication_kinds.count("recovery") == 1
    finally:
        _cleanup_runs(run_id)


def test_event_builder_detects_memory_high():
    memory_high = [
        {
            "change_id": "memory-high",
            "device_id": "SW1",
            "entity_type": "memory",
            "entity_key": "slot1",
            "field": "memory_usage",
            "old": 70,
            "new": "93%",
            "change_type": "modified",
        }
    ]

    event = build_events_from_changes(memory_high)[0]

    assert event.event_type == "MemoryHigh"
    assert event.payload["rule"]["rule_id"] == "memory-high"
    assert event.payload["rule"]["version"] == 1


def test_event_builder_marks_non_event_changes(monkeypatch):
    calls = {}

    class FakeRepository:
        def list_changes(self, *, run_id=None, device_id=None):
            return [
                {
                    "change_id": "small-cpu",
                    "run_id": run_id,
                    "device_id": "SW1",
                    "entity_type": "cpu",
                    "entity_key": "slot1",
                    "field": "cpu_5s",
                    "old": 15,
                    "new": 18,
                    "change_type": "modified",
                },
                {
                    "change_id": "high-cpu",
                    "run_id": run_id,
                    "device_id": "SW1",
                    "entity_type": "cpu",
                    "entity_key": "slot1",
                    "field": "cpu_5s",
                    "old": 15,
                    "new": 95,
                    "change_type": "modified",
                },
            ]

        def save_events(self, events):
            calls["events"] = events
            return len(events)

        def mark_changes_event_status(
            self,
            change_ids,
            *,
            status,
            only_pending=True,
            decision_reasons=None,
        ):
            calls["no_event"] = (change_ids, status, only_pending, decision_reasons)
            return len(change_ids)

    monkeypatch.setattr(
        "src.core.patrol.event_builder.ChangeEventRepository",
        FakeRepository,
    )

    PatrolRepository().create_run(run_id="run-1", source="test", mode="production")
    try:
        result = build_events_from_params({"run_id": "run-1", "persist": True})
    finally:
        _cleanup_runs("run-1")

    assert result["event_count"] == 1
    assert result["persisted_event_count"] == 1
    assert result["no_event_change_count"] == 1
    assert result["no_event_summary"]["total"] == 1
    assert result["no_event_summary"]["by_field"] == {"cpu_5s": 1}
    assert result["no_event_summary"]["by_entity_type"] == {"cpu": 1}
    assert calls["no_event"] == (
        ["small-cpu"],
        "no_event",
        True,
        {"small-cpu": "active_rules_evaluated_no_match"},
    )


def test_no_event_reason_distinguishes_missing_rules_from_non_match():
    from src.core.patrol.event_builder import no_event_reason

    change = {
        "change_id": "memory-delta",
        "change_type": "modified",
        "field": "usage",
        "old": 50,
        "new": 51,
    }

    assert no_event_reason(change, active_rule_count=0) == "no_active_event_rules"
    assert no_event_reason(change, active_rule_count=3) == "active_rules_evaluated_no_match"
    assert (
        no_event_reason(
            {**change, "decision_reason": "threshold_unknown"},
            active_rule_count=3,
        )
        == "threshold_unknown"
    )


def test_import_and_raw_hash_change_roundtrip(tmp_path: Path):
    file1 = tmp_path / "SW2-10.0.0.2_2025-12-24_16-24-15.log"
    file2 = tmp_path / "SW2-10.0.0.2_2025-12-24_16-29-15.log"
    baseline = (
        CISCO_SAMPLE
        + "SW2#show running-config\n"
        + "hostname SW2\ninterface Gi1/0/1\n description server\n"
    )
    current = baseline.replace(" description server", " shutdown")
    file1.write_text(baseline, encoding="utf-8")
    file2.write_text(current, encoding="utf-8")
    run1 = f"test-{uuid.uuid4()}"
    run2 = f"test-{uuid.uuid4()}"
    dataset_id = f"test-{uuid.uuid4()}"
    try:
        r1 = import_raw_capture(
            file_path=file1,
            mode="parser_validation",
            run_id=run1,
            vendor="Cisco",
            metadata={"dataset_id": dataset_id},
        )
        r2 = import_raw_capture(
            file_path=file2,
            mode="parser_validation",
            run_id=run2,
            vendor="Cisco",
            metadata={"dataset_id": dataset_id},
        )
        assert r1["command_count"] == 4
        assert r2["command_count"] == 4

        result = detect_changes_from_params(
            {
                "previous_run_id": run1,
                "current_run_id": run2,
                "device_id": "SW2-10.0.0.2",
            }
        )

        assert result["change_count"] == 1
        assert result["changes"][0]["field"] == "raw_text_hash"
        assert result["changes"][0]["dimensions"]["unstructured_diff"]["changed"] is True
    finally:
        _cleanup_runs(run1, run2)


def test_duplicate_import_reconciles_provisional_collection_statuses(tmp_path: Path):
    source = tmp_path / "SW2-10.0.0.2.log"
    source.write_text(CISCO_SAMPLE, encoding="utf-8")
    run_id = f"test-{uuid.uuid4()}"
    try:
        first = import_raw_capture(
            file_path=source,
            mode="parser_validation",
            run_id=run_id,
            vendor="Cisco",
            collector_complete=False,
        )
        with get_db_session() as db:
            provisional = (
                db.query(PatrolCollectionResult)
                .filter(PatrolCollectionResult.run_id == run_id)
                .all()
            )
            assert {row.execution_status for row in provisional} == {"failed"}

        final = import_raw_capture(
            file_path=source,
            mode="parser_validation",
            run_id=run_id,
            vendor="Cisco",
            collector_complete=True,
        )

        assert final["new_snapshot_count"] == 0
        assert final["duplicate_snapshot_count"] == first["command_count"]
        with get_db_session() as db:
            snapshots = db.query(CommandSnapshot).filter(CommandSnapshot.run_id == run_id).all()
            reconciled = (
                db.query(PatrolCollectionResult)
                .filter(PatrolCollectionResult.run_id == run_id)
                .all()
            )
            assert len(snapshots) == first["command_count"]
            assert len(reconciled) == first["command_count"]
            assert {row.execution_item_id for row in snapshots} == {
                row.execution_item_id for row in reconciled
            }
            assert {row.execution_status for row in reconciled} == {"complete"}
            assert all((row.metadata_json or {}).get("collector_complete") for row in reconciled)
    finally:
        _cleanup_runs(run_id)


def test_incomplete_stream_persists_raw_without_running_parser(
    tmp_path: Path,
    monkeypatch,
):
    source = tmp_path / "SW2-10.0.0.2.log"
    source.write_text(CISCO_SAMPLE, encoding="utf-8")
    run_id = f"test-{uuid.uuid4()}"

    def parser_must_not_run(*args, **kwargs):
        raise AssertionError("Parser must wait until the device capture is complete")

    monkeypatch.setattr(
        "src.core.patrol.raw_importer.ParserRuntime.parse",
        parser_must_not_run,
    )
    try:
        result = import_raw_capture(
            file_path=source,
            mode="production",
            run_id=run_id,
            vendor="Cisco",
            model="WS-C2960XR-24TS-I",
            collector_complete=False,
            metadata={"dataset_id": f"test-{uuid.uuid4()}"},
        )

        assert result["parser_readiness_status"] == "pending"
        assert result["parser_results"]
        assert all(item["parser_deferred"] for item in result["parser_results"])
        with get_db_session() as db:
            snapshots = db.query(CommandSnapshot).filter(CommandSnapshot.run_id == run_id).all()
            assert snapshots
            assert {snapshot.parse_status for snapshot in snapshots} == {"not_attempted"}
            assert all(snapshot.raw_text for snapshot in snapshots)
    finally:
        _cleanup_runs(run_id)


def test_production_qualification_groups_all_raw_snapshots_before_parser(
    tmp_path: Path,
    monkeypatch,
):
    source = tmp_path / "SW2-10.0.0.2.log"
    source.write_text(
        "Command: show version\n"
        "Output:\n"
        "Cisco IOS Software\n"
        "--------------------------------------------------\n"
        "Command: show interface status\n"
        "Output:\n"
        "Gi1/0/1 connected\n"
        "--------------------------------------------------\n",
        encoding="utf-8",
    )
    run_id = f"test-{uuid.uuid4()}"
    inventory_device_id = f"device-{uuid.uuid4()}"
    check_item_version_id = "check-item-version-test"
    qualified: list[dict[str, object]] = []
    with get_db_session() as db:
        db.add(
            NetworkDevice(
                id=inventory_device_id,
                device_name=f"qualification-{uuid.uuid4()}",
                management_ip=f"192.0.2.{uuid.uuid4().int % 200 + 1}",
                vendor="Cisco",
                model="C2960",
                status="active",
            )
        )

    class FakeExecutionService:
        def qualify_evidence(
            self,
            execution_id,
            *,
            snapshot_facts,
            evidence_refs,
            **kwargs,
        ):
            facts = tuple(snapshot_facts)
            with get_db_session() as db:
                snapshots = db.query(CommandSnapshot).filter(CommandSnapshot.run_id == run_id).all()
                assert len(snapshots) == 2
                assert {snapshot.parse_status for snapshot in snapshots} == {"not_attempted"}
            qualified.append(
                {
                    "execution_id": execution_id,
                    "snapshot_facts": facts,
                    "evidence_refs": tuple(evidence_refs),
                }
            )
            return {"id": "qualification-test"}

    def fake_check_coverage(self, *, identity, command):
        return {
            "canonical_command": command,
            "policy_status": "eligible",
            "identity_status": "known",
            "coverage_status": "ready",
            "template_id": None,
            "template_version": None,
            "failure_code": None,
        }

    def fake_parse(self, *, identity, command, raw_text):
        assert len(qualified) == 1
        assert len(qualified[0]["snapshot_facts"]) == 2
        return ParserRuntimeResult(
            policy_status="eligible",
            identity_status="known",
            parser_status="success",
            canonical_command=command,
            handling="structured",
            structured_data=[{"value": command}],
            output_status="data",
        )

    monkeypatch.setattr(
        "src.core.check_items.execution.CheckItemExecutionService",
        FakeExecutionService,
    )
    monkeypatch.setattr(
        "src.core.patrol.raw_importer.ParserRuntime.check_coverage",
        fake_check_coverage,
    )
    monkeypatch.setattr(
        "src.core.patrol.raw_importer.ParserRuntime.parse",
        fake_parse,
    )
    binding = {
        "check_item_id": "system.test",
        "check_item_version_id": check_item_version_id,
        "implementation_version_id": "implementation-test",
        "runtime_bundle_version_id": "bundle-test",
    }
    command_statuses = [
        {
            "command": command,
            "status": "complete",
            "execution_item_id": f"execution-{index}",
            "collection_item_id": f"action-{index}",
            "check_item_bindings": [binding],
            "selected_for_bindings": [binding],
        }
        for index, command in enumerate(
            ("show version", "show interface status"),
            start=1,
        )
    ]

    try:
        result = import_raw_capture(
            file_path=source,
            mode="production",
            run_id=run_id,
            vendor="Cisco",
            model="C2960",
            inventory_device_id=inventory_device_id,
            collector_complete=True,
            command_collection_statuses=command_statuses,
            metadata={
                "dataset_id": f"test-{uuid.uuid4()}",
                "patrol_batch_id": "batch-qualification-test",
            },
        )

        assert result["command_count"] == 2
        assert len(qualified) == 1
        assert {item["id"] for item in qualified[0]["snapshot_facts"]} == set(
            result["snapshot_ids"]
        )
        assert set(qualified[0]["evidence_refs"]) == set(result["snapshot_ids"])
    finally:
        _cleanup_runs(run_id)
        with get_db_session() as db:
            db.query(NetworkDevice).filter(NetworkDevice.id == inventory_device_id).delete(
                synchronize_session=False
            )


def test_command_observed_at_propagates_to_snapshot_and_evidence(tmp_path: Path):
    source = tmp_path / "SW2-10.0.0.2.log"
    source.write_text(CISCO_SAMPLE, encoding="utf-8")
    run_id = f"test-{uuid.uuid4()}"
    observed = {
        "show version": "2026-07-11T01:00:01+00:00",
        "show interface status": "2026-07-11T01:00:02+00:00",
        "show processes cpu": "2026-07-11T01:00:03+00:00",
    }
    try:
        import_raw_capture(
            file_path=source,
            mode="parser_validation",
            run_id=run_id,
            vendor="Cisco",
            command_collection_statuses={
                command: {
                    "status": "complete",
                    "observed_at": timestamp,
                }
                for command, timestamp in observed.items()
            },
        )
        with get_db_session() as db:
            snapshots = db.query(CommandSnapshot).filter(CommandSnapshot.run_id == run_id).all()
            for snapshot in snapshots:
                expected = datetime.fromisoformat(observed[snapshot.canonical_command])
                evidence = (
                    db.query(RawEvidenceRecord)
                    .filter(RawEvidenceRecord.source_snapshot_id == snapshot.id)
                    .one()
                )
                assert evidence.observed_at == snapshot.observed_at
                actual = snapshot.observed_at.replace(tzinfo=ZoneInfo("Asia/Shanghai")).astimezone(
                    expected.tzinfo
                )
                assert actual == expected
    finally:
        _cleanup_runs(run_id)


def test_incremental_command_replay_creates_one_snapshot_per_fact(tmp_path: Path):
    source = tmp_path / "SW2-10.0.0.2.log"
    blocks = split_cli_capture(CISCO_SAMPLE, device_name="SW2")
    run_id = f"test-{uuid.uuid4()}"
    dataset_id = f"test-{uuid.uuid4()}"
    observed = {
        block.command_canonical: f"2026-07-11T01:00:0{index}+00:00"
        for index, block in enumerate(blocks, start=1)
    }

    def report(block_count: int) -> str:
        return "".join(
            f"Command: {block.command}\nOutput:\n{block.raw_output}\n{'-' * 50}\n"
            for block in blocks[:block_count]
        )

    try:
        for block_count in range(1, len(blocks) + 1):
            source.write_text(report(block_count), encoding="utf-8")
            import_raw_capture(
                file_path=source,
                mode="parser_validation",
                run_id=run_id,
                vendor="Cisco",
                metadata={"dataset_id": dataset_id},
                command_collection_statuses={
                    command: {"status": "complete", "observed_at": timestamp}
                    for command, timestamp in observed.items()
                },
                finalize_run=False,
            )
        import_raw_capture(
            file_path=source,
            mode="parser_validation",
            run_id=run_id,
            vendor="Cisco",
            metadata={"dataset_id": dataset_id},
            command_collection_statuses={
                command: {"status": "complete", "observed_at": timestamp}
                for command, timestamp in observed.items()
            },
            finalize_run=False,
        )

        with get_db_session() as db:
            snapshots = db.query(CommandSnapshot).filter(CommandSnapshot.run_id == run_id).all()
            evidence_count = (
                db.query(RawEvidenceRecord)
                .filter(
                    RawEvidenceRecord.source_snapshot_id.in_(
                        [snapshot.id for snapshot in snapshots]
                    )
                )
                .count()
            )
            snapshot_count = len(snapshots)
            fingerprints = {snapshot.import_fingerprint for snapshot in snapshots}
        assert snapshot_count == len(blocks)
        assert evidence_count == len(blocks)
        assert len(fingerprints) == len(blocks)
    finally:
        _cleanup_runs(run_id)


def test_online_command_fingerprint_ignores_mutable_report_hash():
    common = {
        "mode": "production",
        "dataset_id": "dataset-1",
        "device_id": "SW1-10.0.0.1",
        "canonical_command": "show interface status",
        "raw_text": "Gi1/0/1 connected",
        "run_id": "run-1",
        "inventory_device_id": "device-1",
        "command_observed_at": "2026-07-11T01:00:01+00:00",
        "command_ordinal": 1,
    }

    first = _import_fingerprint(source_file_hash="a" * 64, **common)
    cumulative_replay = _import_fingerprint(source_file_hash="b" * 64, **common)

    assert first == cumulative_replay


def test_offline_import_prefers_asset_name_from_filename_over_prompt_alias(tmp_path: Path):
    source = tmp_path / "XA-FOTIC-RT5620-1-10.34.2.219_2025-12-24_14-51-48.txt"
    text = """<XA-FOTIC-DCHL-RT5620-1>display version
H3C Comware Software
"""

    device_name, ip, inspected = _resolve_capture_device(path=source, text=text)

    assert device_name == "XA-FOTIC-RT5620-1"
    assert ip == "10.34.2.219"
    assert inspected.device_name == "XA-FOTIC-DCHL-RT5620-1"


def test_import_raw_path_batches_directory_into_one_run(tmp_path: Path):
    source = tmp_path / "captures"
    source.mkdir()
    (source / "SW1-10.0.0.1.log").write_text(CISCO_SAMPLE, encoding="utf-8")
    (source / "SW2-10.0.0.2.log").write_text(CISCO_SAMPLE, encoding="utf-8")
    run_id = f"test-{uuid.uuid4()}"
    try:
        result = import_raw_path(
            file_path=source,
            mode="parser_validation",
            run_id=run_id,
            metadata={"dataset_id": f"test-{uuid.uuid4()}"},
        )

        assert result["run_id"] == run_id
        assert result["file_count"] == 2
        assert result["device_count"] == 2
        assert result["command_count"] == 6
    finally:
        _cleanup_runs(run_id)


def test_raw_import_fails_before_writing_when_postgres_is_unavailable(tmp_path: Path, monkeypatch):
    source = tmp_path / "SW1-10.0.0.1.log"
    source.write_text(CISCO_SAMPLE, encoding="utf-8")
    db = tmp_path / "patrol.db"

    def unavailable():
        raise ConnectionError("PostgreSQL unavailable")

    monkeypatch.setattr(
        "src.core.patrol.raw_importer.require_postgres_connection",
        unavailable,
    )

    with pytest.raises(ConnectionError, match="PostgreSQL unavailable"):
        import_raw_capture(file_path=source, db_path=db)

    assert not db.exists()


def test_production_import_preserves_evidence_without_creating_asset_jobs(
    tmp_path: Path, monkeypatch
):
    source = tmp_path / "SW1-10.0.0.1.log"
    source.write_text(CISCO_SAMPLE, encoding="utf-8")
    run_id = f"test-{uuid.uuid4()}"

    def fake_check_coverage(self, *, identity, command):
        return {
            "canonical_command": command,
            "policy_status": "eligible",
            "identity_status": "known",
            "coverage_status": "ready",
            "template_id": "template-1",
            "template_version": "v1",
            "failure_code": None,
        }

    def fake_parse(self, *, identity, command, raw_text):
        raise RuntimeError("broken parser")

    monkeypatch.setattr(
        "src.core.patrol.raw_importer.ParserRuntime.check_coverage",
        fake_check_coverage,
    )
    monkeypatch.setattr(
        "src.core.patrol.raw_importer.ParserRuntime.parse",
        fake_parse,
    )

    try:
        result = import_raw_capture(
            file_path=source,
            mode="production",
            run_id=run_id,
            vendor="Cisco",
            model="WS-C2960XR-24TS-I",
            metadata={"dataset_id": f"test-{uuid.uuid4()}"},
        )

        assert result["success"] is True
        assert result["production_parser_ready"] is False
        assert result["parse_violations"]
        candidate_ids = {
            item["candidate_job_id"]
            for item in result["parser_results"]
            if item.get("candidate_job_id")
        }
        assert not candidate_ids
        with get_db_session() as db:
            snapshots = db.query(CommandSnapshot).filter(CommandSnapshot.run_id == run_id).all()
            assert snapshots
            assert {row.parse_status for row in snapshots} == {"parse_failed"}
            evidence_count = (
                db.query(RawEvidenceRecord)
                .filter(RawEvidenceRecord.source_snapshot_id.in_([row.id for row in snapshots]))
                .count()
            )
            assert evidence_count == len(snapshots)
            collection_results = (
                db.query(PatrolCollectionResult)
                .filter(PatrolCollectionResult.run_id == run_id)
                .all()
            )
            assert {row.execution_status for row in collection_results} == {"complete"}
            assert {row.fact_status for row in collection_results} == {"blocked_by_parser"}
            assert all(row.parser_candidate_id is None for row in collection_results)
    finally:
        _cleanup_runs(run_id)


def test_import_persists_raw_snapshot_before_parser_and_attaches_once(
    tmp_path: Path,
    monkeypatch,
):
    source = tmp_path / "SW1-10.0.0.1.log"
    source.write_text(
        "Command: show version\n"
        "Output:\n"
        "Cisco IOS Software\n"
        "--------------------------------------------------\n",
        encoding="utf-8",
    )
    run_id = f"test-{uuid.uuid4()}"
    observed_raw: dict[str, str] = {}

    def fake_parse(self, *, identity, command, raw_text):
        with get_db_session() as db:
            snapshot = (
                db.query(CommandSnapshot)
                .filter(
                    CommandSnapshot.run_id == run_id,
                    CommandSnapshot.canonical_command == command,
                )
                .one()
            )
            assert snapshot.parse_status == "not_attempted"
            assert snapshot.structured_json is None
            assert snapshot.parser_template_id is None
            assert snapshot.raw_text == raw_text
            assert snapshot.raw_text_hash == hashlib.sha256(raw_text.encode("utf-8")).hexdigest()
            observed_raw["snapshot_id"] = str(snapshot.id)
            observed_raw["raw_text"] = str(snapshot.raw_text)
            observed_raw["raw_text_hash"] = str(snapshot.raw_text_hash)
        return ParserRuntimeResult(
            policy_status="eligible",
            identity_status="known",
            parser_status="success",
            canonical_command=command,
            handling="structured",
            structured_data=[{"software_version": "test"}],
            output_status="data",
        )

    monkeypatch.setattr(
        "src.core.patrol.raw_importer.ParserRuntime.parse",
        fake_parse,
    )

    try:
        result = import_raw_capture(
            file_path=source,
            mode="parser_validation",
            run_id=run_id,
            vendor="Cisco",
            model="C2960",
            metadata={"dataset_id": f"test-{uuid.uuid4()}"},
        )

        snapshot_id = result["snapshot_ids"][0]
        assert snapshot_id == observed_raw["snapshot_id"]
        stored = PatrolSnapshotRepository().get_snapshot(snapshot_id)
        assert stored is not None
        assert stored.parse_status == "parsed"
        assert stored.structured_json == [{"software_version": "test"}]
        assert stored.raw_text == observed_raw["raw_text"]
        assert stored.raw_text_hash == observed_raw["raw_text_hash"]

        _same_id, attached = PatrolSnapshotRepository().attach_parser_result(
            snapshot_id=snapshot_id,
            expected_raw_text_hash=stored.raw_text_hash,
            parse_status="parsed",
            structured_json=[{"software_version": "test"}],
            parser_name=None,
            parser_version=None,
        )
        assert attached is False
        with pytest.raises(
            ValueError,
            match="already has a different Parser result",
        ):
            PatrolSnapshotRepository().attach_parser_result(
                snapshot_id=snapshot_id,
                expected_raw_text_hash=stored.raw_text_hash,
                parse_status="parsed",
                structured_json=[{"software_version": "different"}],
                parser_name=None,
                parser_version=None,
            )
    finally:
        _cleanup_runs(run_id)


def test_completed_patrol_run_only_persists_snapshots(tmp_path: Path):
    first_report = tmp_path / "sw1-first.txt"
    second_report = tmp_path / "sw1-second.txt"
    first_report.write_text(
        """命令: show version
输出:
Version 1
--------------------------------------------------
命令: show running-config
输出:
hostname SW1
interface Gi1/0/1
 description server
""",
        encoding="utf-8",
    )
    second_report.write_text(
        """命令: show version
输出:
Version 2
--------------------------------------------------
命令: show running-config
输出:
hostname SW1
interface Gi1/0/1
 shutdown
""",
        encoding="utf-8",
    )
    db = tmp_path / "pipeline.db"
    run1 = f"test-{uuid.uuid4()}"
    run2 = f"test-{uuid.uuid4()}"
    detail1 = [{"device_name": "SW1", "ip": "10.0.0.1", "output_file": str(first_report)}]
    detail2 = [{"device_name": "SW1", "ip": "10.0.0.1", "output_file": str(second_report)}]

    try:
        baseline = _persist_patrol_snapshots(
            details=detail1,
            snapshot_run_id=run1,
            db_path=str(db),
            mode="parser_validation",
        )
        current = _persist_patrol_snapshots(
            details=detail2,
            snapshot_run_id=run2,
            db_path=str(db),
            mode="parser_validation",
        )

        assert baseline["snapshot_commands"] == 2
        assert current["snapshot_commands"] == 2
        assert "change_count" not in current
        assert "event_count" not in current

        change_result = detect_changes_from_params(
            {
                "current_run_id": run2,
                "persist": True,
            }
        )
        event_result = build_events_from_changes(change_result["changes"])

        assert change_result["compared_snapshots"] == 2
        # ``show version`` is a structured command. A missing/failed Parser may
        # not silently downgrade it to a raw diff; only the governed config
        # command participates in non-structured comparison.
        assert change_result["change_count"] == 1
        assert [event.event_type for event in event_result] == ["ConfigChanged"]
    finally:
        _cleanup_runs(run1, run2)


def test_raw_hash_diff_flows_through_observation_change_event_chain(tmp_path: Path):
    first_report = tmp_path / "sw1-config-first.txt"
    second_report = tmp_path / "sw1-config-second.txt"
    first_report.write_text(
        "命令: show running-config\n输出:\nhostname SW1\ninterface Gi1/0/1\n description server\n",
        encoding="utf-8",
    )
    second_report.write_text(
        "命令: show running-config\n输出:\nhostname SW1\ninterface Gi1/0/1\n shutdown\n",
        encoding="utf-8",
    )
    run1 = f"raw-hash-{uuid.uuid4()}"
    run2 = f"raw-hash-{uuid.uuid4()}"
    try:
        _persist_patrol_snapshots(
            details=[{"device_name": "SW1", "ip": "10.0.0.1", "output_file": str(first_report)}],
            snapshot_run_id=run1,
            mode="parser_validation",
        )
        _persist_patrol_snapshots(
            details=[{"device_name": "SW1", "ip": "10.0.0.1", "output_file": str(second_report)}],
            snapshot_run_id=run2,
            mode="parser_validation",
        )
        first_normalized = normalize_run(run1)
        second_normalized = normalize_run(run2)
        assert first_normalized["observation_count"] == 1
        assert second_normalized["observation_count"] == 1
        assert first_normalized["results"][0]["observations"][0]["field_name"] == "raw_text_hash"

        change_result = detect_changes_from_params(
            {
                "current_run_id": run2,
                "previous_run_id": run1,
                "use_state_observations": True,
                "persist": True,
            }
        )
        events = build_events_from_changes(change_result["changes"])
        assert change_result["change_count"] == 1
        assert [event.event_type for event in events] == ["ConfigChanged"]
    finally:
        _cleanup_runs(run1, run2)


@pytest.mark.parametrize(
    ("raw_output", "parse_status", "output_status", "fact_status", "baseline"),
    [
        ("", "parse_empty", "valid_empty", "empty", True),
        (
            "% Unrecognized command found at '^' position.",
            "not_applicable",
            "unsupported_command",
            "not_applicable",
            False,
        ),
    ],
)
def test_vrf_empty_and_unsupported_outputs_do_not_create_parser_jobs(
    tmp_path: Path,
    raw_output: str,
    parse_status: str,
    output_status: str,
    fact_status: str,
    baseline: bool,
):
    report = tmp_path / f"vrf-{parse_status}.txt"
    report.write_text(f"命令: display vrf\n输出:\n{raw_output}\n", encoding="utf-8")
    run_id = f"vrf-output-{uuid.uuid4()}"
    try:
        result = import_raw_capture(
            file_path=report,
            mode="production",
            vendor="H3C",
            model="S5130S-54S-HI-G",
            run_id=run_id,
            collector_complete=True,
        )
        assert result["production_parser_ready"] is True
        with get_db_session() as db:
            snapshot = db.query(CommandSnapshot).filter_by(run_id=run_id).one()
            collection = db.query(PatrolCollectionResult).filter_by(run_id=run_id).one()
            assert snapshot.parse_status == parse_status
            assert snapshot.output_status == output_status
            assert collection.output_status == output_status
            assert collection.fact_status == fact_status
            assert collection.baseline_eligible is baseline
            assert collection.parser_candidate_id is None
    finally:
        _cleanup_runs(run_id)
