from __future__ import annotations

import uuid

import pytest

from src.core.observation import (
    ObservationContractRepository,
    build_contract_payload,
    observation_contract_digest,
)
from src.core.parser.repository import ParserRepository
from src.infrastructure.db.models import ObservationContractRecord, ParserTemplate
from src.infrastructure.db.postgres import get_db_session


def test_observation_contract_digest_changes_with_semantics():
    first = build_contract_payload(
        source_type="cli",
        schema_id="template-1",
        category="interface_l3",
        validation={
            "entity_type": "interface_l3",
            "primary_keys": ["interface"],
            "required_fields": ["interface", "status", "protocol"],
        },
    )
    same = {
        **first,
        "required_fields": ["interface", "status", "protocol"],
    }
    changed = {
        **first,
        "primary_keys": ["interface", "vrf"],
    }

    assert first["contract_sha256"] == observation_contract_digest(same)
    assert first["contract_sha256"] != observation_contract_digest(changed)


def test_parser_template_registration_persists_observation_contract():
    token = uuid.uuid4().hex
    repository = ParserRepository()
    template_id = repository.register_template(
        vendor="Cisco",
        model="N9K-C9364C",
        family="NX-OS",
        profile=f"N9K-C9364C-{token}",
        command="show ip interface brief",
        category="interface_l3",
        version=f"v-{token}",
        asset_path=f"/tmp/template-{token}.textfsm",
        sha256="0" * 64,
        status="active",
        validation={
            "entity_type": "interface_l3",
            "primary_keys": ["interface"],
            "required_fields": ["interface", "status", "protocol"],
            "optional_fields": ["ip_address"],
            "ignore_fields": ["description"],
            "validators": {"interface": {"type": "interface_name"}},
        },
    )
    try:
        contract = ObservationContractRepository().get_active(
            source_type="cli",
            schema_id=template_id,
        )

        assert contract is not None
        assert contract.source_template_id == template_id
        assert contract.entity_type == "interface_l3"
        assert contract.primary_keys == ("interface",)
        assert contract.required_fields == ("interface", "status", "protocol")
        assert contract.validators == {"interface": {"type": "interface_name"}}
    finally:
        with get_db_session() as db:
            db.query(ObservationContractRecord).filter(
                ObservationContractRecord.source_template_id == template_id
            ).delete(synchronize_session=False)
            db.query(ParserTemplate).filter(ParserTemplate.id == template_id).delete(
                synchronize_session=False
            )


def test_observation_contract_rejects_same_version_different_digest():
    token = uuid.uuid4().hex
    repository = ObservationContractRepository()
    schema_id = f"template-{token}"
    try:
        first = repository.upsert_contract(
            source_type="cli",
            schema_id=schema_id,
            category="interface_l3",
            validation={
                "entity_type": "interface_l3",
                "primary_keys": ["interface"],
                "required_fields": ["interface", "status"],
            },
            version=1,
            status="active",
        )
        same = repository.upsert_contract(
            source_type="cli",
            schema_id=schema_id,
            category="interface_l3",
            validation={
                "entity_type": "interface_l3",
                "primary_keys": ["interface"],
                "required_fields": ["interface", "status"],
            },
            version=1,
            status="active",
        )

        assert same.contract_id == first.contract_id
        with pytest.raises(ValueError, match="version is immutable"):
            repository.upsert_contract(
                source_type="cli",
                schema_id=schema_id,
                category="interface_l3",
                validation={
                    "entity_type": "interface_l3",
                    "primary_keys": ["interface", "vrf"],
                    "required_fields": ["interface", "status"],
                },
                version=1,
                status="active",
            )
    finally:
        with get_db_session() as db:
            db.query(ObservationContractRecord).filter(
                ObservationContractRecord.schema_id == schema_id
            ).delete(synchronize_session=False)


def test_parser_template_status_change_deactivates_observation_contract():
    token = uuid.uuid4().hex
    repository = ParserRepository()
    template_id = repository.register_template(
        vendor="H3C",
        model=f"S6860-{token}",
        family="Comware7",
        profile=f"S6860-{token}",
        command="display fan",
        category="fan",
        version=f"v-{token}",
        asset_path=f"/tmp/template-{token}.textfsm",
        sha256="1" * 64,
        status="active",
        validation={
            "entity_type": "fan",
            "primary_keys": ["slot", "fan"],
            "required_fields": ["slot", "fan", "state"],
            "optional_fields": ["direction"],
        },
    )
    try:
        assert (
            ObservationContractRepository().get_active(
                source_type="cli",
                schema_id=template_id,
            )
            is not None
        )

        repository.set_template_status(
            template_id,
            "rejected",
            actor="reviewer",
            reason="sample validation rejected",
        )

        assert (
            ObservationContractRepository().get_active(
                source_type="cli",
                schema_id=template_id,
            )
            is None
        )
        with get_db_session() as db:
            template = db.get(ParserTemplate, template_id)
            audit = template.validation_json["status_audit"][-1]
            assert audit["actor"] == "reviewer"
            assert audit["reason"] == "sample validation rejected"
            assert audit["to_status"] == "rejected"
            row = (
                db.query(ObservationContractRecord)
                .filter(ObservationContractRecord.source_template_id == template_id)
                .one()
            )
            assert row.status == "deprecated"
    finally:
        with get_db_session() as db:
            db.query(ObservationContractRecord).filter(
                ObservationContractRecord.source_template_id == template_id
            ).delete(synchronize_session=False)
            db.query(ParserTemplate).filter(ParserTemplate.id == template_id).delete(
                synchronize_session=False
            )


def test_template_activation_deprecates_previous_observation_contract():
    token = uuid.uuid4().hex
    repository = ParserRepository()
    common = {
        "vendor": "Cisco",
        "model": f"N9K-{token}",
        "family": "NX-OS",
        "profile": f"N9K-{token}",
        "command": "show inventory",
        "category": "inventory",
        "sha256": "2" * 64,
        "status": "draft",
        "validation": {
            "entity_type": "inventory_component",
            "primary_keys": ["component"],
            "required_fields": ["component", "name"],
            "optional_fields": ["pid", "serial"],
            "regression_validation": {
                "passed": True,
                "sample_count": 2,
                "passed_samples": 2,
            },
        },
    }
    first_id = repository.register_template(
        **common,
        version=f"v1-{token}",
        asset_path=f"/tmp/template-v1-{token}.textfsm",
    )
    second_id = repository.register_template(
        **common,
        version=f"v2-{token}",
        asset_path=f"/tmp/template-v2-{token}.textfsm",
    )
    try:
        repository.activate_template(
            first_id,
            approved_by="unit-test",
            reason="validated first template",
        )
        assert (
            ObservationContractRepository().get_active(
                source_type="cli",
                schema_id=first_id,
            )
            is not None
        )

        repository.activate_template(
            second_id,
            approved_by="unit-test",
            reason="validated replacement template",
        )

        assert (
            ObservationContractRepository().get_active(
                source_type="cli",
                schema_id=first_id,
            )
            is None
        )
        assert (
            ObservationContractRepository().get_active(
                source_type="cli",
                schema_id=second_id,
            )
            is not None
        )
        with get_db_session() as db:
            first_contract = (
                db.query(ObservationContractRecord)
                .filter(ObservationContractRecord.source_template_id == first_id)
                .one()
            )
            assert first_contract.status == "deprecated"
            second_template = db.get(ParserTemplate, second_id)
            assert second_template.validation_json["contract_approved_by"] == "unit-test"
            assert second_template.validation_json["approval_audit"][-1]["reason"] == (
                "validated replacement template"
            )

        repository.rollback_template(
            first_id,
            actor="unit-test",
            reason="rollback rehearsal",
        )
        with get_db_session() as db:
            first_template = db.get(ParserTemplate, first_id)
            second_template = db.get(ParserTemplate, second_id)
            assert first_template.status == "active"
            assert second_template.status == "deprecated"
            assert first_template.validation_json["approval_audit"][-1]["action"] == "rollback"
        assert (
            ObservationContractRepository().get_active(
                source_type="cli",
                schema_id=first_id,
            )
            is not None
        )
        assert (
            ObservationContractRepository().get_active(
                source_type="cli",
                schema_id=second_id,
            )
            is None
        )
    finally:
        with get_db_session() as db:
            db.query(ObservationContractRecord).filter(
                ObservationContractRecord.source_template_id.in_([first_id, second_id])
            ).delete(synchronize_session=False)
            db.query(ParserTemplate).filter(ParserTemplate.id.in_([first_id, second_id])).delete(
                synchronize_session=False
            )
