from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone

import pytest

from src.core.rule_control.command_asset_units import RuleAssetCommandUnitService
from src.core.rule_control.production_pipeline import RuleAssetProductionPipelineService
from src.infrastructure.db.models import (
    CommandSnapshot,
    OfflinePatrolDeviceBindingRecord,
    OfflinePatrolJobRecord,
    OfflinePatrolPeriodRecord,
    ParserTemplate,
    PatrolDataset,
    PatrolRun,
    RuleAssetCommandUnitRecord,
    RuleGenerationContextRecord,
    RuleGenerationJobRecord,
)
from src.infrastructure.db.postgres import get_db_session


def test_empty_offline_job_pipeline_points_to_source_upload():
    suffix = uuid.uuid4().hex[:12]
    job_id = f"offline-pipeline-{suffix}"
    with get_db_session() as db:
        db.add(
            OfflinePatrolJobRecord(
                id=job_id,
                idempotency_key=f"idempotency-{suffix}",
                purpose="rule_asset_source",
                job_type="parser_validation",
                name="Pipeline test",
                owner_id="pytest",
                status="draft",
                progress_json={},
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            )
        )
        db.add(
            OfflinePatrolPeriodRecord(
                id=f"period-{suffix}",
                job_id=job_id,
                role="sample",
                label="Sample",
                observed_at=datetime.now(timezone.utc),
                dataset_id=f"dataset-{suffix}",
                status="pending",
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            )
        )

    try:
        result = RuleAssetProductionPipelineService().get(job_id)

        assert result["progress_percent"] == 0
        assert result["next_action"]["code"] == "upload_sources"
        assert result["next_action"]["target_url"].startswith("/skills/parser?")
        assert "workspace=production" in result["next_action"]["target_url"]
        assert "source_job_tab=sources" in result["next_action"]["target_url"]
        assert "/devices" not in result["next_action"]["target_url"]
        assert [item["key"] for item in result["steps"]] == [
            "source",
            "preview",
            "ingest",
            "catalog",
        ]
        assert all(item["optional"] is False for item in result["steps"])
    finally:
        with get_db_session() as db:
            db.query(OfflinePatrolPeriodRecord).filter(
                OfflinePatrolPeriodRecord.job_id == job_id
            ).delete(synchronize_session=False)
            db.query(OfflinePatrolJobRecord).filter(OfflinePatrolJobRecord.id == job_id).delete(
                synchronize_session=False
            )


def test_patrol_execution_job_cannot_enter_rule_asset_production():
    suffix = uuid.uuid4().hex[:12]
    job_id = f"offline-patrol-boundary-{suffix}"
    with get_db_session() as db:
        db.add(
            OfflinePatrolJobRecord(
                id=job_id,
                idempotency_key=f"idempotency-patrol-boundary-{suffix}",
                purpose="offline_patrol",
                job_type="single_patrol",
                name="Patrol execution only",
                owner_id="pytest",
                status="completed",
                progress_json={},
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            )
        )

    try:
        with pytest.raises(ValueError, match="Parser control asset-source jobs"):
            RuleAssetProductionPipelineService().get(job_id)
        with pytest.raises(ValueError, match="Parser control asset-source jobs"):
            RuleAssetCommandUnitService().reconcile(job_id)
    finally:
        with get_db_session() as db:
            db.query(OfflinePatrolJobRecord).filter(
                OfflinePatrolJobRecord.id == job_id
            ).delete(synchronize_session=False)


def test_pipeline_stops_at_sample_command_catalog_before_database_session_closes():
    suffix = uuid.uuid4().hex[:12]
    job_id = f"offline-pipeline-assets-{suffix}"
    dataset_id = f"dataset-pipeline-assets-{suffix}"
    run_id = f"run-pipeline-assets-{suffix}"
    template_id = f"template-pipeline-assets-{suffix}"
    seed_id = f"seed-pipeline-assets-{suffix}"
    context_id = f"context-pipeline-assets-{suffix}"
    bundle_id = f"bundle-pipeline-assets-{suffix}"
    command = f"display pipeline test {suffix}"
    now = datetime.now(timezone.utc)
    raw_text = "Interface Link\nGE1/0/1 UP"

    with get_db_session() as db:
        db.add(
            PatrolDataset(
                id=dataset_id,
                name="Pipeline asset projection test",
                mode="parser_validation",
                source="imported_file",
            )
        )
        db.flush()
        db.add(
            PatrolRun(
                id=run_id,
                mode="parser_validation",
                source="imported_file",
                dataset_id=dataset_id,
                status="completed",
                logical_run_id=run_id,
                device_count=1,
                command_count=1,
            )
        )
        db.add(
            OfflinePatrolJobRecord(
                id=job_id,
                idempotency_key=f"idempotency-assets-{suffix}",
                purpose="rule_asset_source",
                job_type="parser_validation",
                name="Pipeline asset projection test",
                owner_id="pytest",
                status="completed",
                progress_json={},
            )
        )
        db.add(
            OfflinePatrolPeriodRecord(
                id=f"period-pipeline-assets-{suffix}",
                job_id=job_id,
                role="sample",
                label="Sample",
                observed_at=now,
                dataset_id=dataset_id,
                patrol_run_id=run_id,
                status="completed",
            )
        )
        db.add(
            ParserTemplate(
                id=template_id,
                vendor="H3C",
                model="S6850",
                profile="default",
                canonical_command=command,
                category="interface",
                version="1.0.0",
                asset_path=f"tests/{template_id}.textfsm",
                sha256="a" * 64,
                status="active",
            )
        )
        db.flush()
        db.add(
            CommandSnapshot(
                id=f"snapshot-pipeline-assets-{suffix}",
                run_id=run_id,
                dataset_id=dataset_id,
                device_id="SW1",
                execution_item_id=f"execution-assets-{suffix}",
                device_name="SW1",
                vendor="H3C",
                model="S6850",
                command=command,
                canonical_command=command,
                parser_template_id=template_id,
                observed_at=now,
                structured_json=[{"interface": "GE1/0/1", "link": "UP"}],
                raw_text=raw_text,
                raw_text_hash=hashlib.sha256(raw_text.encode()).hexdigest(),
                parse_status="parsed",
                output_status="content",
            )
        )
        db.add(
            RuleGenerationJobRecord(
                id=seed_id,
                job_type="OBSERVATION_CONTRACT",
                status="draft",
                source_ref=f"parser_template:{template_id}",
                payload_json={"workflow": "rule-asset-generation"},
            )
        )
        db.flush()
        db.add(
            RuleGenerationContextRecord(
                id=context_id,
                job_id=seed_id,
                offline_job_id=job_id,
                dataset_ids_json=[dataset_id],
                context_json={"records": [{"interface": "if-1", "link": "UP"}]},
                context_digest=hashlib.sha256(context_id.encode()).hexdigest(),
                privacy_report_json={"success": True, "raw_cli_included": False},
            )
        )
        db.add(
            RuleGenerationJobRecord(
                id=bundle_id,
                job_type="RULE_ASSET_BUNDLE",
                status="pending_approval",
                payload_json={"context_id": context_id},
                validation_report_json={
                    "governance": {"engineering_review": {"decision": "accepted"}}
                },
            )
        )

    try:
        result = RuleAssetProductionPipelineService().get(job_id)

        assert result["context_ids"] == []
        assert result["bundle_ids"] == []
        assert result["runtime_targets"] == []
        assert [step["key"] for step in result["steps"]] == [
            "source", "preview", "ingest", "catalog",
        ]
        assert next(step for step in result["steps"] if step["key"] == "ingest")[
            "status"
        ] == "completed"
        assert next(step for step in result["steps"] if step["key"] == "catalog")[
            "status"
        ] == "completed"
        assert result["next_action"]["code"] in {
            "open_check_item_catalog", "inspect_import_result",
        }
    finally:
        with get_db_session() as db:
            db.query(RuleAssetCommandUnitRecord).filter(
                RuleAssetCommandUnitRecord.offline_job_id == job_id
            ).delete(synchronize_session=False)
            db.query(RuleGenerationContextRecord).filter(
                RuleGenerationContextRecord.id == context_id
            ).delete(synchronize_session=False)
            db.query(RuleGenerationJobRecord).filter(
                RuleGenerationJobRecord.id.in_([seed_id, bundle_id])
            ).delete(synchronize_session=False)
            db.query(CommandSnapshot).filter(CommandSnapshot.run_id == run_id).delete(
                synchronize_session=False
            )
            db.query(OfflinePatrolPeriodRecord).filter(
                OfflinePatrolPeriodRecord.job_id == job_id
            ).delete(synchronize_session=False)
            db.query(OfflinePatrolJobRecord).filter(OfflinePatrolJobRecord.id == job_id).delete(
                synchronize_session=False
            )
            db.query(PatrolRun).filter(PatrolRun.id == run_id).delete(synchronize_session=False)
            db.query(PatrolDataset).filter(PatrolDataset.id == dataset_id).delete(
                synchronize_session=False
            )
            db.query(ParserTemplate).filter(ParserTemplate.id == template_id).delete(
                synchronize_session=False
            )


def test_rule_asset_sample_identity_never_requires_inventory_confirmation():
    suffix = uuid.uuid4().hex[:12]
    job_id = f"offline-pipeline-preview-{suffix}"
    dataset_id = f"dataset-pipeline-preview-{suffix}"
    now = datetime.now(timezone.utc)
    with get_db_session() as db:
        db.add(
            OfflinePatrolJobRecord(
                id=job_id,
                idempotency_key=f"idempotency-preview-{suffix}",
                purpose="rule_asset_source",
                job_type="parser_validation",
                name="Pipeline preview stage test",
                owner_id="pytest",
                status="completed",
                progress_json={},
                preview_json={
                    "totals": {
                        "file_count": 1,
                        "device_count": 1,
                        "command_count": 2,
                        "template_missing": 1,
                    }
                },
                blockers_json=[
                    {
                        "code": "template_missing",
                        "count": 1,
                        "message": "one Parser template is missing",
                    }
                ],
            )
        )
        db.flush()
        db.add(
            OfflinePatrolPeriodRecord(
                id=f"period-pipeline-preview-{suffix}",
                job_id=job_id,
                role="sample",
                label="Sample",
                observed_at=now,
                dataset_id=dataset_id,
                status="completed",
            )
        )
        db.add(
            OfflinePatrolDeviceBindingRecord(
                id=f"binding-pipeline-preview-{suffix}",
                job_id=job_id,
                period_role="sample",
                external_device_id=f"external-{suffix}",
                device_name="External switch",
                match_status="ambiguous",
                confidence=1.0,
                confirmed_by="pytest",
                confirmed_at=now,
            )
        )

    try:
        result = RuleAssetProductionPipelineService().get(job_id)

        preview_step = next(step for step in result["steps"] if step["key"] == "preview")
        catalog_step = next(step for step in result["steps"] if step["key"] == "catalog")
        assert preview_step["status"] == "completed"
        assert preview_step["label"] == "预检与命令识别"
        assert preview_step["counts"] == {"sample_devices": 1, "commands": 2}
        assert catalog_step["status"] == "not_started"
        assert result["next_action"]["code"] != "confirm_devices"
    finally:
        with get_db_session() as db:
            db.query(OfflinePatrolDeviceBindingRecord).filter(
                OfflinePatrolDeviceBindingRecord.job_id == job_id
            ).delete(synchronize_session=False)
            db.query(OfflinePatrolPeriodRecord).filter(
                OfflinePatrolPeriodRecord.job_id == job_id
            ).delete(synchronize_session=False)
            db.query(OfflinePatrolJobRecord).filter(
                OfflinePatrolJobRecord.id == job_id
            ).delete(synchronize_session=False)
