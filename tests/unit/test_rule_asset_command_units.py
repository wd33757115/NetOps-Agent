from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from src.core.parser.policy import ParserPolicy
from src.core.rule_control.command_asset_units import RuleAssetCommandUnitService
from src.infrastructure.db.models import (
    CommandSnapshot,
    OfflinePatrolJobRecord,
    OfflinePatrolPeriodRecord,
    ParserTemplate,
    PatrolDataset,
    PatrolRun,
    RuleAssetCommandUnitRecord,
    RuleGenerationJobRecord,
    SnapshotParseResult,
)
from src.infrastructure.db.postgres import get_db_session


def _snapshot(
    *,
    suffix: str,
    run_id: str,
    dataset_id: str,
    ordinal: int,
    vendor: str,
    model: str,
    command: str,
    device_id: str,
    device_name: str | None = None,
    management_ip: str | None = None,
    parse_status: str = "template_missing",
    output_status: str = "content",
) -> CommandSnapshot:
    raw_text = f"{device_id}\n{command}\noutput-{ordinal}"
    return CommandSnapshot(
        id=f"snapshot-command-unit-{suffix}-{ordinal}",
        run_id=run_id,
        dataset_id=dataset_id,
        device_id=device_id,
        execution_item_id=f"execution-{suffix}-{ordinal}",
        device_name=device_name or device_id,
        management_ip=management_ip,
        vendor=vendor,
        model=model,
        command=command,
        canonical_command=command,
        observed_at=datetime.now(timezone.utc),
        structured_json=None,
        raw_text=raw_text,
        raw_text_hash=hashlib.sha256(raw_text.encode()).hexdigest(),
        parse_status=parse_status,
        output_status=output_status,
    )


def test_command_units_deduplicate_evidence_and_keep_asset_boundaries():
    suffix = uuid.uuid4().hex[:10]
    job_id = f"offline-command-unit-{suffix}"
    dataset_id = f"dataset-command-unit-{suffix}"
    run_id = f"run-command-unit-{suffix}"
    with get_db_session() as db:
        db.add(
            PatrolDataset(
                id=dataset_id,
                name="Command unit test",
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
                device_count=3,
                command_count=4,
            )
        )
        db.flush()
        db.add(
            OfflinePatrolJobRecord(
                id=job_id,
                idempotency_key=f"idempotency-command-unit-{suffix}",
                purpose="rule_asset_source",
                job_type="parser_validation",
                name="Command unit test",
                owner_id="pytest",
                status="completed",
                progress_json={},
            )
        )
        db.add(
            OfflinePatrolPeriodRecord(
                id=f"period-command-unit-{suffix}",
                job_id=job_id,
                role="sample",
                label="Sample",
                observed_at=datetime.now(timezone.utc),
                dataset_id=dataset_id,
                patrol_run_id=run_id,
                status="completed",
            )
        )
        db.flush()
        db.add_all(
            [
                _snapshot(
                    suffix=suffix,
                    run_id=run_id,
                    dataset_id=dataset_id,
                    ordinal=1,
                    vendor="H3C",
                    model="S6520",
                    command="display interface brief",
                    device_id="SW1",
                    management_ip="10.0.0.1",
                ),
                _snapshot(
                    suffix=suffix,
                    run_id=run_id,
                    dataset_id=dataset_id,
                    ordinal=2,
                    vendor="H3C",
                    model="S6520",
                    command="display interface brief",
                    device_id="SW2",
                    management_ip="10.0.0.2",
                ),
                _snapshot(
                    suffix=suffix,
                    run_id=run_id,
                    dataset_id=dataset_id,
                    ordinal=3,
                    vendor="Huawei",
                    model="CE6865",
                    command="display interface brief",
                    device_id="SW3",
                ),
                _snapshot(
                    suffix=suffix,
                    run_id=run_id,
                    dataset_id=dataset_id,
                    ordinal=4,
                    vendor="H3C",
                    model="S6520",
                    command="show running-config",
                    device_id="SW1",
                ),
                _snapshot(
                    suffix=suffix,
                    run_id=run_id,
                    dataset_id=dataset_id,
                    ordinal=5,
                    vendor="H3C",
                    model="S6520",
                    command="display interface brief",
                    device_id="sample-period-current-SW1",
                    device_name="SW1",
                    management_ip="10.0.0.1",
                ),
                _snapshot(
                    suffix=suffix,
                    run_id=run_id,
                    dataset_id=dataset_id,
                    ordinal=6,
                    vendor="H3C",
                    model="UnsupportedModel",
                    command="display m-lag system",
                    device_id="unsupported-device",
                    parse_status="not_applicable",
                    output_status="unsupported_command",
                ),
            ]
        )

    service = RuleAssetCommandUnitService()
    try:
        first = service.reconcile(job_id)
        second = service.reconcile(job_id)
        assert first["unit_count"] == 4
        assert second["unit_count"] == 4

        interface_page = service.list_units(
            job_id,
            search="interface brief",
            page=1,
            page_size=1,
        )
        assert interface_page["total"] == 2
        assert interface_page["page_count"] == 2

        all_units = service.list_units(job_id, page=1, page_size=10)
        assert all_units["total"] == 4
        h3c_interface = next(
            item
            for item in all_units["items"]
            if item["vendor"] == "H3C"
            and item["canonical_command"] == "display interface brief"
        )
        assert h3c_interface["sample_count"] == 3
        assert h3c_interface["device_count"] == 2
        assert h3c_interface["asset_mode"] == "full_chain"
        assert h3c_interface["current_stage"] == "parser_generation"

        raw_hash = next(
            item
            for item in all_units["items"]
            if item["canonical_command"] == "show running-config"
        )
        assert raw_hash["asset_mode"] == "raw_hash"
        assert raw_hash["status"] == "completed"
        assert raw_hash["stage_statuses"]["event_rule"] == "skipped"

        unsupported = next(
            item
            for item in all_units["items"]
            if item["canonical_command"] == "display m-lag system"
        )
        assert unsupported["profile"] == "UnsupportedModel"
        assert unsupported["handling"] == "not_applicable"
        assert unsupported["asset_mode"] == "ignored"
        assert unsupported["status"] == "completed"

        detail = service.get_unit(job_id, h3c_interface["id"])
        assert len(detail["stages"]) == 10
        assert len(detail["evidence"]) == 3
        assert all("raw_text" not in item for item in detail["evidence"])
        policy_stage = next(
            item for item in detail["stages"] if item["key"] == "change_policy"
        )
        generation_url = policy_stage["next_action"]["target_url"]
        assert f"source_job_id={job_id}" in generation_url
        assert f"dataset_ids={dataset_id}" in generation_url
        paged_detail = service.get_unit(
            job_id,
            h3c_interface["id"],
            evidence_page=2,
            evidence_page_size=2,
        )
        assert len(paged_detail["evidence"]) == 1
        assert paged_detail["evidence_pagination"] == {
            "page": 2,
            "page_size": 2,
            "total": 3,
            "page_count": 2,
        }

        with get_db_session() as db:
            assert (
                db.query(RuleAssetCommandUnitRecord)
                .filter(RuleAssetCommandUnitRecord.offline_job_id == job_id)
                .count()
                == 4
            )
    finally:
        with get_db_session() as db:
            db.query(RuleAssetCommandUnitRecord).filter(
                RuleAssetCommandUnitRecord.offline_job_id == job_id
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


def test_parser_job_resolution_never_reuses_an_unmatched_model_profile():
    candidate = SimpleNamespace(model="S5130S", profile="S5130S")

    resolved = RuleAssetCommandUnitService._resolve_parser_job(
        {
            "vendor": "H3C",
            "model": "MSR5620",
            "command": "display interface brief",
        },
        {("h3c", "display interface brief"): [candidate]},
    )

    assert resolved is None


def test_command_unit_projection_uses_latest_successful_reparse_as_effective_state():
    suffix = uuid.uuid4().hex[:10]
    job_id = f"offline-reparse-unit-{suffix}"
    dataset_id = f"dataset-reparse-unit-{suffix}"
    run_id = f"run-reparse-unit-{suffix}"
    snapshot_id = f"snapshot-command-unit-{suffix}-1"
    template_id = f"template-reparse-unit-{suffix}"
    command = "display interface brief"
    model = f"LAB-{suffix}"
    with get_db_session() as db:
        db.add(
            PatrolDataset(
                id=dataset_id,
                name="Reparse command unit test",
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
                idempotency_key=f"idempotency-reparse-unit-{suffix}",
                purpose="rule_asset_source",
                job_type="parser_validation",
                name="Reparse command unit test",
                owner_id="pytest",
                status="completed",
                progress_json={},
            )
        )
        db.add(
            OfflinePatrolPeriodRecord(
                id=f"period-reparse-unit-{suffix}",
                job_id=job_id,
                role="sample",
                label="Sample",
                observed_at=datetime.now(timezone.utc),
                dataset_id=dataset_id,
                patrol_run_id=run_id,
                status="completed",
            )
        )
        db.add(
            ParserTemplate(
                id=template_id,
                vendor="H3C",
                model=model,
                family="Comware7",
                profile=model,
                canonical_command=command,
                category="interface_l3",
                version="v1",
                asset_path=f"pytest/{template_id}.textfsm",
                sha256=hashlib.sha256(template_id.encode()).hexdigest(),
                status="active",
                validation_json={
                    "entity_type": "interface_l3",
                    "primary_keys": ["interface"],
                    "regression_validation": {
                        "passed": True,
                        "sample_count": 1,
                        "passed_samples": 1,
                    },
                },
            )
        )
        db.flush()
        db.add(
            _snapshot(
                suffix=suffix,
                run_id=run_id,
                dataset_id=dataset_id,
                ordinal=1,
                vendor="H3C",
                model=model,
                command=command,
                device_id="SW1",
            )
        )
        db.flush()
        db.add(
            SnapshotParseResult(
                id=f"parse-result-reparse-unit-{suffix}",
                snapshot_id=snapshot_id,
                template_id=template_id,
                template_version="v1",
                parse_status="success",
                structured_json=[{"interface": "GE1/0/1", "status": "UP"}],
                parsed_at=datetime.now(timezone.utc),
            )
        )

    service = RuleAssetCommandUnitService()
    try:
        service.reconcile(job_id)
        unit = service.list_units(job_id, page=1, page_size=10)["items"][0]
        assert unit["parse_counts"] == {"success": 1}
        assert unit["stage_statuses"]["structured_quality"] == "passed"
        assert unit["current_stage"] == "observation_contract"
        assert unit["rule_seed_job_id"]

        service.reconcile(job_id)
        with get_db_session() as db:
            seeds = (
                db.query(RuleGenerationJobRecord)
                .filter(
                    RuleGenerationJobRecord.job_type == "OBSERVATION_CONTRACT",
                    RuleGenerationJobRecord.source_ref == f"parser_template:{template_id}",
                )
                .all()
            )
            assert len(seeds) == 1

        detail = service.get_unit(job_id, unit["id"])
        assert len(detail["evidence"]) == 1
        evidence = detail["evidence"][0]
        assert evidence["parse_status"] == "success"
        assert evidence["original_parse_status"] == "template_missing"
        assert evidence["parse_source"] == "reparse"
        assert evidence["parser_template_id"] == template_id
        assert evidence["structured_record_count"] == 1
        contract_stage = next(
            item for item in detail["stages"] if item["key"] == "observation_contract"
        )
        assert contract_stage["status"] == "ready"
        assert f"pc_rule_job_search={unit['rule_seed_job_id']}" in (
            contract_stage["next_action"]["target_url"]
        )
    finally:
        with get_db_session() as db:
            db.query(RuleGenerationJobRecord).filter(
                RuleGenerationJobRecord.source_ref == f"parser_template:{template_id}"
            ).delete(synchronize_session=False)
            db.query(RuleAssetCommandUnitRecord).filter_by(offline_job_id=job_id).delete()
            db.query(SnapshotParseResult).filter_by(snapshot_id=snapshot_id).delete()
            db.query(CommandSnapshot).filter_by(id=snapshot_id).delete()
            db.query(OfflinePatrolPeriodRecord).filter_by(job_id=job_id).delete()
            db.query(OfflinePatrolJobRecord).filter_by(id=job_id).delete()
            db.query(PatrolRun).filter_by(id=run_id).delete()
            db.query(PatrolDataset).filter_by(id=dataset_id).delete()
            db.query(ParserTemplate).filter_by(id=template_id).delete()


class _PolicyRepository:
    @staticmethod
    def list_command_aliases():
        return []


def test_parser_policy_exposes_all_governed_semantic_asset_modes(tmp_path):
    policy_path = tmp_path / "command_policy.yaml"
    policy_path.write_text(
        """
commands:
  show inventory: {enabled: true, asset_mode: observation_only}
  show version: {enabled: true, asset_mode: change_only}
  show interface status: {enabled: true, asset_mode: full_chain}
  show running-config: {enabled: false, handling: raw_hash_diff, asset_mode: raw_hash}
  show clock: {enabled: false, handling: raw_only, asset_mode: ignored}
""".strip(),
        encoding="utf-8",
    )
    policy = ParserPolicy(policy_path=policy_path, repository=_PolicyRepository())

    def asset_mode(command: str) -> str:
        return policy.decide(
            vendor="Cisco",
            model="C9300",
            family=None,
            command=command,
        ).asset_mode

    assert asset_mode("show inventory") == "observation_only"
    assert asset_mode("show version") == "change_only"
    assert asset_mode("show interface status") == "full_chain"
    assert asset_mode("show running-config") == "raw_hash"
    assert asset_mode("show clock") == "ignored"


def test_parser_policy_rejects_asset_modes_that_conflict_with_handling(tmp_path):
    policy_path = tmp_path / "invalid-command-policy.yaml"
    policy_path.write_text(
        "commands:\n  show clock: {enabled: false, handling: raw_only, asset_mode: full_chain}\n",
        encoding="utf-8",
    )
    policy = ParserPolicy(policy_path=policy_path, repository=_PolicyRepository())

    with pytest.raises(ValueError, match="requires structured handling"):
        policy.decide(vendor="Cisco", model="C9300", family=None, command="show clock")


@pytest.mark.parametrize(
    ("asset_mode", "policy_status", "event_status"),
    [
        ("observation_only", "skipped", "skipped"),
        ("change_only", "not_started", "skipped"),
        ("full_chain", "not_started", "not_started"),
    ],
)
def test_command_unit_stages_respect_semantic_asset_mode(
    asset_mode: str,
    policy_status: str,
    event_status: str,
):
    unit = {
        "vendor": "Cisco",
        "profile": "C9300",
        "models": {"C9300"},
        "command": "show inventory",
        "asset_mode": asset_mode,
        "sample_count": 2,
        "device_count": 1,
        "structured_count": 2,
        "parse_counts": {"parsed": 2},
    }

    stages = RuleAssetCommandUnitService()._build_stages(
        offline_job_id="offline-stage-mode-test",
        unit=unit,
        parser_job=None,
        template=None,
        active_contract=None,
        seed=None,
        context=None,
        bundle=None,
        children=[],
        output_signature_count=1,
    )
    by_key = {stage["key"]: stage["status"] for stage in stages}

    assert by_key["change_policy"] == policy_status
    assert by_key["event_rule"] == event_status
