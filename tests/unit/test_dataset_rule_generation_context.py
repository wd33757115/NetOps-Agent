from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from src.core.rule_control.dataset_context import (
    DatasetRuleGenerationContextService,
    _round_robin_snapshot_records,
    sanitize_structured_records,
)
from src.infrastructure.db.models import (
    CommandSnapshot,
    OfflinePatrolJobRecord,
    OfflinePatrolPeriodRecord,
    ParserTemplate,
    PatrolDataset,
    PatrolRun,
    RuleGenerationContextRecord,
    RuleGenerationJobRecord,
    SnapshotParseResult,
)
from src.infrastructure.db.postgres import get_db_session


def test_pure_structured_record_sanitizer_is_safe_for_local_agent_validation():
    result = sanitize_structured_records(
        [
            {
                "interface": "GE1/0/1",
                "ip_address": "10.34.2.11/24",
                "description": "peer 192.0.2.10 token=secret-value",
                "password": "top-secret",
                "unapproved": "drop-me",
            }
        ],
        approved_fields={"interface", "ip_address", "description", "password"},
    )

    serialized = json.dumps(result, ensure_ascii=False)
    assert "10.34.2.11" not in serialized
    assert "192.0.2.10" not in serialized
    assert "secret-value" not in serialized
    assert "top-secret" not in serialized
    assert "drop-me" not in serialized
    assert "peer " not in serialized
    assert result["records"][0]["description"].startswith("text-")
    assert result["records"][0]["ip_address"].startswith("198.51.100.")
    assert result["records"][0]["ip_address"].endswith("/24")
    assert result["privacy"]["raw_cli_included"] is False


def test_snapshot_sampling_represents_peers_before_repeating_large_capture():
    records, groups = _round_robin_snapshot_records(
        [
            ("period-a", "device-a", {"row": "a-1"}),
            ("period-a", "device-a", {"row": "a-2"}),
            ("period-a", "device-b", {"row": "b-1"}),
            ("period-a", "device-b", {"row": "b-2"}),
            ("period-b", "device-c", {"row": "c-1"}),
            ("period-b", "device-c", {"row": "c-2"}),
        ],
        max_records=3,
    )

    assert {record["row"] for record in records} == {"a-1", "b-1", "c-1"}
    assert set(groups) == {"device-a", "device-b", "device-c"}


def test_rule_context_prefers_comparable_contract_semantics_over_identity_default():
    service = DatasetRuleGenerationContextService()

    selected = service._first_comparable_field(
        [{"interface": "GE1/0/1", "link": "UP", "description": "uplink"}],
        semantics={
            "interface": {"semantic_type": "identity"},
            "link": {"semantic_type": "state"},
            "description": {"semantic_type": "text", "ignore": True},
        },
    )

    assert selected == "link"


def test_rule_context_load_rejects_legacy_context_without_offline_job_evidence():
    suffix = uuid.uuid4().hex
    job_id = f"rule-job-legacy-{suffix}"
    context_id = f"context-legacy-{suffix}"

    with get_db_session() as db:
        db.add(
            RuleGenerationJobRecord(
                id=job_id,
                job_type="OBSERVATION_CONTRACT",
                status="draft",
                payload_json={},
            )
        )
        db.flush()
        db.add(
            RuleGenerationContextRecord(
                id=context_id,
                job_id=job_id,
                offline_job_id=None,
                dataset_ids_json=[f"legacy-dataset-{suffix}"],
                context_json={"records": [{"interface": "GE1/0/1"}]},
                context_digest=suffix,
                privacy_report_json={
                    "success": True,
                    "scope_type": "dataset_structured_records",
                    "raw_cli_included": False,
                },
                created_by="unit-test",
            )
        )

    try:
        with pytest.raises(
            ValueError,
            match="not backed by an offline patrol job",
        ):
            DatasetRuleGenerationContextService().load(
                context_id=context_id,
                job_id=job_id,
            )
    finally:
        with get_db_session() as db:
            db.query(RuleGenerationContextRecord).filter(
                RuleGenerationContextRecord.id == context_id
            ).delete(synchronize_session=False)
            db.query(RuleGenerationJobRecord).filter(
                RuleGenerationJobRecord.id == job_id
            ).delete(synchronize_session=False)


def test_rule_context_rejects_offline_patrol_execution_dataset():
    suffix = uuid.uuid4().hex
    job_id = f"rule-job-patrol-boundary-{suffix}"
    template_id = f"template-patrol-boundary-{suffix}"
    offline_job_id = f"offline-patrol-boundary-{suffix}"
    dataset_id = f"dataset-patrol-boundary-{suffix}"

    with get_db_session() as db:
        db.add(
            OfflinePatrolJobRecord(
                id=offline_job_id,
                idempotency_key=f"idempotency-patrol-boundary-{suffix}",
                purpose="offline_patrol",
                job_type="single_patrol",
                name="patrol execution evidence",
                owner_id="unit-test",
                status="completed",
            )
        )
        db.add(
            OfflinePatrolPeriodRecord(
                id=f"period-patrol-boundary-{suffix}",
                job_id=offline_job_id,
                role="current",
                label="current",
                observed_at=datetime.now(timezone.utc),
                dataset_id=dataset_id,
                patrol_run_id=f"run-patrol-boundary-{suffix}",
                status="completed",
            )
        )
        db.add(
            ParserTemplate(
                id=template_id,
                vendor="H3C",
                profile="S5130S",
                canonical_command="display interface brief",
                category="interface_status",
                version="1.0.0",
                asset_path="tests/fixtures/interface.textfsm",
                sha256="b" * 64,
                status="active",
                validation_json={
                    "contract_approval_status": "approved",
                    "primary_keys": ["interface"],
                    "required_fields": ["interface", "status"],
                },
            )
        )
        db.add(
            RuleGenerationJobRecord(
                id=job_id,
                job_type="OBSERVATION_CONTRACT",
                status="draft",
                payload_json={"template_id": template_id},
            )
        )

    try:
        with pytest.raises(ValueError, match="Parser control asset-source jobs"):
            DatasetRuleGenerationContextService().prepare(
                job_id=job_id,
                dataset_ids=[dataset_id],
                created_by="unit-test",
            )
    finally:
        with get_db_session() as db:
            db.query(OfflinePatrolPeriodRecord).filter(
                OfflinePatrolPeriodRecord.job_id == offline_job_id
            ).delete(synchronize_session=False)
            db.query(OfflinePatrolJobRecord).filter(
                OfflinePatrolJobRecord.id == offline_job_id
            ).delete(synchronize_session=False)
            db.query(RuleGenerationJobRecord).filter(
                RuleGenerationJobRecord.id == job_id
            ).delete(synchronize_session=False)
            db.query(ParserTemplate).filter(ParserTemplate.id == template_id).delete(
                synchronize_session=False
            )


def test_dataset_rule_context_is_immutable_balanced_and_sanitized():
    suffix = uuid.uuid4().hex
    job_id = f"rule-job-{suffix}"
    template_id = f"template-{suffix}"
    offline_job_id = f"offline-{suffix}"
    dataset_ids = [f"dataset-a-{suffix}", f"dataset-b-{suffix}"]
    run_ids = [f"run-a-{suffix}", f"run-b-{suffix}"]
    stale_run_ids = [f"run-a-stale-{suffix}", f"run-b-stale-{suffix}"]
    observed_at = datetime.now(timezone.utc)
    records = [
        {
            "interface": "GigabitEthernet1/0/1",
            "rx_power_dbm": ["-18.2", "-18.4"],
            "device_name": "SW1",
            "management_ip": "10.0.0.1",
            "description": "peer 192.0.2.10 password=embedded-secret",
            "password": "must-never-leave-the-database",
            "nested": {"community": "private", "peer_ip": "192.0.2.10"},
        },
        {
            "interface": "GigabitEthernet1/0/2",
            "rx_power_dbm": -15.1,
            "device_name": "SW2",
            "management_ip": "10.0.0.2",
            "description": "healthy uplink",
            "token": "also-secret",
        },
    ]

    with get_db_session() as db:
        db.add(
            OfflinePatrolJobRecord(
                id=offline_job_id,
                idempotency_key=f"idempotency-{suffix}",
                purpose="rule_asset_source",
                job_type="history_compare",
                name="authoritative context test",
                owner_id="unit-test",
                status="completed",
            )
        )
        db.add(
            ParserTemplate(
                id=template_id,
                vendor="H3C",
                profile="S6850",
                canonical_command="display transceiver diagnosis interface",
                category="interface_optical",
                version="1.0.0",
                asset_path="tests/fixtures/optical.textfsm",
                sha256="a" * 64,
                status="active",
                validation_json={
                    "contract_approval_status": "approved",
                    "primary_keys": ["interface"],
                    "required_fields": ["interface", "rx_power_dbm"],
                    "optional_fields": ["device_name", "management_ip", "description"],
                },
            )
        )
        db.add(
            RuleGenerationJobRecord(
                id=job_id,
                job_type="OBSERVATION_CONTRACT",
                status="draft",
                payload_json={
                    "template_id": template_id,
                    "workflow_context_defaults": {
                        "field_name": "rx_power_dbm",
                        "observation_type": "interface.optical.rx_power",
                    },
                },
            )
        )
        db.flush()
        for index, dataset_id in enumerate(dataset_ids):
            db.add(
                PatrolDataset(
                    id=dataset_id,
                    name=dataset_id,
                    mode="parser_validation",
                    source="offline_test",
                    status="active",
                )
            )
            db.flush()
            db.add(
                PatrolRun(
                    id=run_ids[index],
                    logical_run_id=run_ids[index],
                    mode="parser_validation",
                    source="offline_test",
                    dataset_id=dataset_id,
                    status="completed",
                    collection_status="completed",
                    run_purpose="parser_validation",
                    attempt_number=0,
                )
            )
            db.add(
                PatrolRun(
                    id=stale_run_ids[index],
                    logical_run_id=run_ids[index],
                    mode="parser_validation",
                    source="offline_test",
                    dataset_id=dataset_id,
                    status="completed",
                    collection_status="completed",
                    run_purpose="parser_validation",
                    attempt_number=1,
                )
            )
            db.flush()
            db.add(
                OfflinePatrolPeriodRecord(
                    id=f"period-{index}-{suffix}",
                    job_id=offline_job_id,
                    role="baseline" if index == 0 else "current",
                    label=f"period {index}",
                    observed_at=observed_at + timedelta(days=index),
                    dataset_id=dataset_id,
                    patrol_run_id=run_ids[index],
                    status="completed",
                )
            )
            db.add(
                CommandSnapshot(
                    id=f"snapshot-{index}-{suffix}",
                    run_id=run_ids[index],
                    dataset_id=dataset_id,
                    device_id=f"device-{index}",
                    execution_item_id=f"item-{index}",
                    device_name=f"SW{index + 1}",
                    management_ip=f"10.0.0.{index + 1}",
                    command="display transceiver diagnosis interface",
                    canonical_command="display transceiver diagnosis interface",
                    parser_template_id=template_id if index == 1 else None,
                    observed_at=observed_at + timedelta(days=index),
                    structured_json=records[index] if index == 1 else None,
                    raw_text=f"raw CLI with password secret-{index}",
                    raw_text_hash=f"hash-{index}-{suffix}",
                    parse_status="parsed" if index == 1 else "template_missing",
                )
            )
            if index == 0:
                db.add(
                    SnapshotParseResult(
                        id=f"reparse-{index}-{suffix}",
                        snapshot_id=f"snapshot-{index}-{suffix}",
                        template_id=template_id,
                        template_version="1.0.0",
                        parse_status="success",
                        structured_json=records[index],
                        parsed_at=observed_at + timedelta(hours=1),
                    )
                )
            db.add(
                CommandSnapshot(
                    id=f"snapshot-stale-{index}-{suffix}",
                    run_id=stale_run_ids[index],
                    dataset_id=dataset_id,
                    device_id=f"device-stale-{index}",
                    execution_item_id=f"item-stale-{index}",
                    device_name=f"STALE{index + 1}",
                    command="display transceiver diagnosis interface",
                    canonical_command="display transceiver diagnosis interface",
                    parser_template_id=template_id,
                    observed_at=observed_at - timedelta(days=30),
                    structured_json={
                        "interface": f"GigabitEthernet9/0/{index + 1}",
                        "rx_power_dbm": -99.0,
                    },
                    raw_text="stale raw CLI",
                    raw_text_hash=f"stale-hash-{index}-{suffix}",
                    parse_status="parsed",
                )
            )

    try:
        service = DatasetRuleGenerationContextService()
        first = service.prepare(
            job_id=job_id,
            dataset_ids=dataset_ids,
            created_by="unit-test",
            max_records=20,
        )
        second = service.prepare(
            job_id=job_id,
            dataset_ids=list(reversed(dataset_ids)),
            created_by="unit-test",
            max_records=20,
        )

        assert first["context_id"] == second["context_id"]
        assert first["context_digest"] == second["context_digest"]
        assert first["context"]["historical_values"] == [-18.2, -18.4, -15.1]
        assert first["context"]["replay_run_ids"] == sorted(run_ids)
        assert len(first["context"]["record_groups"]) == 2
        assert all(
            len(group["records"]) == 1
            for group in first["context"]["record_groups"]
        )
        assert first["privacy"]["source_snapshot_count"] == 2
        assert first["privacy"]["capture_snapshot_count"] == 1
        assert first["privacy"]["reparse_snapshot_count"] == 1
        assert first["privacy"]["source_resolution"] == (
            "latest_successful_reparse_then_capture"
        )
        assert first["privacy"]["scope_type"] == "offline_period_authoritative_runs"
        assert first["privacy"]["dataset_run_scopes"] == [
            {"dataset_id": dataset_ids[0], "run_id": run_ids[0]},
            {"dataset_id": dataset_ids[1], "run_id": run_ids[1]},
        ]
        assert first["privacy"]["source_projection"] == [
            "dataset_id",
            "run_id",
            "device_id",
            "observed_at",
            "structured_json",
        ]
        assert first["privacy"]["raw_cli_included"] is False
        assert first["privacy"]["approved_field_whitelist"] == [
            "description",
            "device_name",
            "interface",
            "management_ip",
            "rx_power_dbm",
        ]
        assert first["privacy"]["dropped_unapproved_fields"] == {
            "nested": 1,
            "password": 1,
            "token": 1,
        }
        serialized = json.dumps(first, ensure_ascii=False)
        for forbidden in (
            "raw CLI",
            "must-never-leave-the-database",
            "also-secret",
            "private",
            "SW1",
            "SW2",
            "10.0.0.1",
            "10.0.0.2",
            "192.0.2.10",
            "embedded-secret",
        ):
            assert forbidden not in serialized
        assert "identity-" in serialized
    finally:
        with get_db_session() as db:
            db.query(RuleGenerationContextRecord).filter(
                RuleGenerationContextRecord.job_id == job_id
            ).delete(synchronize_session=False)
            all_run_ids = run_ids + stale_run_ids
            db.query(SnapshotParseResult).filter(
                SnapshotParseResult.snapshot_id.in_(
                    [f"snapshot-{index}-{suffix}" for index in range(2)]
                )
            ).delete(synchronize_session=False)
            db.query(CommandSnapshot).filter(CommandSnapshot.run_id.in_(all_run_ids)).delete(
                synchronize_session=False
            )
            db.query(OfflinePatrolPeriodRecord).filter(
                OfflinePatrolPeriodRecord.job_id == offline_job_id
            ).delete(synchronize_session=False)
            db.query(PatrolRun).filter(PatrolRun.id.in_(all_run_ids)).delete(
                synchronize_session=False
            )
            db.query(PatrolDataset).filter(PatrolDataset.id.in_(dataset_ids)).delete(
                synchronize_session=False
            )
            db.query(RuleGenerationJobRecord).filter(
                RuleGenerationJobRecord.id == job_id
            ).delete(synchronize_session=False)
            db.query(ParserTemplate).filter(ParserTemplate.id == template_id).delete(
                synchronize_session=False
            )
            db.query(OfflinePatrolJobRecord).filter(
                OfflinePatrolJobRecord.id == offline_job_id
            ).delete(synchronize_session=False)
