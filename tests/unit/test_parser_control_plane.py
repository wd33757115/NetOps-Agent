from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from src.core.parser.assets import TemplateAssetStore
from src.core.parser.identity import DeviceIdentity
from src.core.parser.repository import ParserRepository, contract_digest
from src.core.parser.runtime import ParserRuntime
from src.core.patrol.modes import normalize_patrol_mode
from src.core.patrol.repository import PatrolRepository
from src.core.state.repository import StateRepository
from src.infrastructure.db.models import (
    CommandSnapshot,
    PatrolDataset,
    PatrolRun,
    RawEvidenceRecord,
)
from src.infrastructure.db.postgres import get_db_session


class FakeRegistry:
    def __init__(self, template=None):
        self.template = template
        self.calls = []

    def resolve_active(self, **kwargs):
        self.calls.append(kwargs)
        return self.template


def known_h3c_identity():
    return DeviceIdentity(
        vendor="H3C",
        model="S5130S-54S-HI-G",
        family="Comware7",
        confidence=1.0,
        source="test",
    )


def test_ignored_command_never_resolves_template():
    registry = FakeRegistry()
    result = ParserRuntime(registry=registry).parse(
        identity=known_h3c_identity(),
        command="display current-configuration",
        raw_text="configuration",
    )
    assert result.policy_status == "ignored"
    assert result.handling == "raw_hash_diff"
    assert registry.calls == []


def test_unknown_identity_never_resolves_template():
    registry = FakeRegistry()
    result = ParserRuntime(registry=registry).parse(
        identity=DeviceIdentity(
            vendor=None,
            model=None,
            family=None,
            confidence=0.0,
        ),
        command="display version",
        raw_text="version",
    )
    assert result.identity_status == "unknown"
    assert registry.calls == []


def test_eligible_command_without_active_template_is_missing():
    result = ParserRuntime(registry=FakeRegistry()).parse(
        identity=known_h3c_identity(),
        command="display version",
        raw_text="version",
    )
    assert result.policy_status == "eligible"
    assert result.parser_status == "unavailable"
    assert result.failure_code == "template_missing"


def test_active_template_parses_and_hash_is_verified(tmp_path):
    content = """Value model (\\S+)
Value os_version (\\S+)

Start
  ^Model:\\s+${model}
  ^Version:\\s+${os_version} -> Record
"""
    store = TemplateAssetStore(tmp_path)
    path, digest = store.write_immutable(
        vendor="H3C",
        profile="S5130S-54S-HI-G",
        command="display version",
        version="1",
        content=content,
    )
    template = SimpleNamespace(
        id="t1",
        version="1",
        asset_path=str(path),
        sha256=digest,
    )
    result = ParserRuntime(
        registry=FakeRegistry(template),
        asset_store=store,
    ).parse(
        identity=known_h3c_identity(),
        command="display version",
        raw_text="Model: S5130S-54S-HI-G\nVersion: 7.1\n",
    )
    assert result.parser_status == "success"
    assert result.template_id == "t1"
    assert result.structured_data == [
        {"model": "S5130S-54S-HI-G", "os_version": "7.1"}
    ]


def test_legal_empty_is_not_parse_failure(tmp_path):
    content = """Value interface (\\S+)
Value group (\\d+)
Value state (\\S+)

Start
  ^${interface}\\s+${group}\\s+${state} -> Record
"""
    store = TemplateAssetStore(tmp_path)
    path, digest = store.write_immutable(
        vendor="H3C",
        profile="S5130S-54S-HI-G",
        command="display vrrp statistics",
        version="1",
        content=content,
    )
    template = SimpleNamespace(
        id="t2",
        version="1",
        asset_path=str(path),
        sha256=digest,
        category="vrrp",
        validation_json={
            "entity_type": "vrrp",
            "primary_keys": ["interface", "group"],
            "required_fields": ["interface", "group", "state"],
            "allow_empty": True,
            "empty_patterns": [r"VRRP4 is not configured"],
        },
    )
    result = ParserRuntime(registry=FakeRegistry(template)).parse(
        identity=known_h3c_identity(),
        command="display vrrp statistics",
        raw_text="VRRP4 is not configured.",
    )
    assert result.parser_status == "empty"
    assert result.failure_code is None


def test_interface_name_validator_rejects_header_artifact(tmp_path):
    content = """Value interface (\\S+)
Value status (\\S+)
Value protocol (\\S+)

Start
  ^${interface}\\s+${status}\\s+${protocol} -> Record
"""
    store = TemplateAssetStore(tmp_path)
    path, digest = store.write_immutable(
        vendor="H3C",
        profile="S5130S-54S-HI-G",
        command="display interface brief",
        version="1",
        content=content,
    )
    template = SimpleNamespace(
        id="t3",
        version="1",
        asset_path=str(path),
        sha256=digest,
        category="interface_l3",
        validation_json={
            "entity_type": "interface_l3",
            "primary_keys": ["interface"],
            "required_fields": ["interface", "status", "protocol"],
            "validators": {"interface": {"type": "interface_name"}},
        },
    )

    result = ParserRuntime(registry=FakeRegistry(template)).parse(
        identity=known_h3c_identity(),
        command="display interface brief",
        raw_text="Interface Link Protocol\n",
    )

    assert result.parser_status == "failed"
    assert result.failure_code == "field_validation_failed"


def test_patrol_modes_are_normalized_without_storage_paths():
    assert normalize_patrol_mode("production") == "production"
    assert normalize_patrol_mode("test") == "parser_validation"


def test_candidate_rejects_unsupported_snapshot_mode():
    snapshot = SimpleNamespace(device_id="SW1", raw_text="version output")
    context = {
        "snapshot": snapshot,
        "mode": "historical_backfill",
        "dataset_id": "production-dataset",
    }
    with patch(
        "src.core.parser.repository.PatrolRepository.get_snapshot_context",
        return_value=context,
    ):
        with pytest.raises(ValueError, match="parser_validation or production"):
            ParserRepository().create_candidate(
                job_type="CREATE_TEMPLATE",
                vendor="H3C",
                model="S5130S-54S-HI-G",
                family="Comware7",
                command="display version",
                snapshot_id="snapshot-1",
                device_id="SW1",
                raw_text="version output",
            )


def test_snapshot_import_is_idempotent_and_dataset_is_queryable():
    repository = PatrolRepository()
    token = uuid.uuid4().hex
    dataset_id = f"test-{token}"
    repository.ensure_dataset(
        dataset_id=dataset_id,
        name=dataset_id,
        mode="parser_validation",
        source="unit_test",
    )
    run_id = repository.create_run(
        run_id=f"test-{token}",
        mode="parser_validation",
        dataset_id=dataset_id,
        metadata={"dataset_id": dataset_id},
    )
    params = {
        "run_id": run_id,
        "device_id": "SW1",
        "device_name": "SW1",
        "command": "display version",
        "command_canonical": "display version",
        "raw_text": "version output",
        "dataset_id": dataset_id,
        "source_file_hash": "file-hash",
        "import_fingerprint": f"stable-{token}",
    }

    try:
        first_id, first_created = repository.save_snapshot_idempotent(**params)
        second_id, second_created = repository.save_snapshot_idempotent(**params)

        assert first_created is True
        assert second_created is False
        assert first_id == second_id
        with get_db_session() as db:
            evidence_rows = (
                db.query(RawEvidenceRecord)
                .filter(RawEvidenceRecord.source_snapshot_id == first_id)
                .all()
            )
            assert len(evidence_rows) == 1
            assert evidence_rows[0].source_type == "cli"
            assert evidence_rows[0].evidence_type == "cli_command_output"
            assert evidence_rows[0].content_ref == f"command_snapshot:{first_id}"
            db.query(RawEvidenceRecord).filter(
                RawEvidenceRecord.source_snapshot_id == first_id
            ).delete()
        context = StateRepository().get_snapshot_context(first_id)
        assert context is not None
        assert context["snapshot"]["source_type"] == "cli"
        assert context["snapshot"]["evidence_id"]
        with get_db_session() as db:
            assert (
                db.query(RawEvidenceRecord)
                .filter(RawEvidenceRecord.source_snapshot_id == first_id)
                .count()
                == 1
            )
        dataset = next(
            item
            for item in repository.list_datasets(mode="parser_validation")
            if item["id"] == dataset_id
        )
        assert dataset["snapshot_count"] == 1
    finally:
        with get_db_session() as db:
            db.query(CommandSnapshot).filter(
                CommandSnapshot.run_id == run_id
            ).delete()
            db.query(PatrolRun).filter(PatrolRun.id == run_id).delete()
            db.query(PatrolDataset).filter(PatrolDataset.id == dataset_id).delete()


def test_contract_digest_is_order_independent_and_semantic():
    first = contract_digest(
        {
            "entity_type": "interface",
            "primary_keys": ["interface"],
            "required_fields": ["interface", "status"],
        }
    )
    second = contract_digest(
        {
            "required_fields": ["interface", "status"],
            "primary_keys": ["interface"],
            "entity_type": "interface",
        }
    )
    changed = contract_digest(
        {
            "entity_type": "interface",
            "primary_keys": ["interface", "vlan"],
            "required_fields": ["interface", "status"],
        }
    )

    assert first == second
    assert changed != first
