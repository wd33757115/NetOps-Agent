# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import uuid
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from src.common.config import get_settings
from src.core.celery_tasks.tasks import _persist_patrol_snapshots
from src.core.patrol.change_detector import (
    _aggregate_collection_results,
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
from src.core.patrol.raw_importer import (
    _import_fingerprint,
    _resolve_capture_device,
    import_raw_capture,
    import_raw_path,
)
from src.core.patrol.repository import PatrolRepository
from src.infrastructure.db.models import (
    CommandSnapshot,
    NetworkChangeRecord,
    NetworkEventOutboxRecord,
    NetworkEventRecord,
    ParserJobSample,
    ParserOutboxEvent,
    ParserTemplateJob,
    PatrolDataset,
    PatrolRun,
    RawEvidenceRecord,
)
from src.infrastructure.db.postgres import get_db_session

PROJECT_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def _enable_explicit_builtin_rule_test_interface(monkeypatch):
    monkeypatch.setattr(get_settings(), "EVENT_RULE_ALLOW_BUILTIN_FALLBACK", True)


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
    assert interface_event.event_type == "InterfaceDown"
    assert interface_event.payload["rule"]["rule_id"] == "interface-down"


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

        def mark_changes_event_status(self, change_ids, *, status, only_pending=True):
            calls["no_event"] = (change_ids, status, only_pending)
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
    assert calls["no_event"] == (["small-cpu"], "no_event", True)


def test_import_and_raw_hash_change_roundtrip(tmp_path: Path):
    file1 = tmp_path / "SW2-10.0.0.2_2025-12-24_16-24-15.log"
    file2 = tmp_path / "SW2-10.0.0.2_2025-12-24_16-29-15.log"
    file1.write_text(CISCO_SAMPLE, encoding="utf-8")
    file2.write_text(CISCO_SAMPLE.replace("24%/0%", "95%/0%"), encoding="utf-8")
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
        assert r1["command_count"] == 3
        assert r2["command_count"] == 3

        result = detect_changes_from_params(
            {
                "previous_run_id": run1,
                "current_run_id": run2,
                "device_id": "SW2-10.0.0.2",
            }
        )

        assert result["change_count"] == 1
        assert result["changes"][0]["field"] == "raw_text_hash"
    finally:
        _cleanup_runs(run1, run2)


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


def test_production_import_preserves_evidence_when_parser_fails(tmp_path: Path, monkeypatch):
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
        assert change_result["change_count"] == 2
        assert [event.event_type for event in event_result] == ["ConfigChanged"]
    finally:
        _cleanup_runs(run1, run2)
