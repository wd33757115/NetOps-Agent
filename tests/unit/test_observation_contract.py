from __future__ import annotations

import uuid

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
