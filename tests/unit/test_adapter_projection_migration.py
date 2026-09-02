from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.core.check_items.adapter_projection_migration import (
    AdapterProjectionCutoverInventoryService,
    build_projection_generation_context,
    build_cutover_readiness_report,
    generate_projection_candidate,
    migrate_legacy_adapter_to_v2,
    validate_projection_candidate,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PROJECTION_SKILL_SCRIPT = (
    PROJECT_ROOT
    / "src"
    / "skills"
    / "observation-contract-generator"
    / "scripts"
    / "run.py"
)


def _load_projection_skill_module():
    spec = importlib.util.spec_from_file_location(
        "adapter_projection_generator_skill",
        PROJECTION_SKILL_SCRIPT,
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

CHECK_ITEM = {
    "version_id": "interface-oper-v6",
    "canonical_contract": {
        "entity_type": "interface",
        "identity_dimensions": ["interface"],
        "observations": [
            {
                "canonical_field": "oper_status",
                "observation_type": "interface.oper_status",
                "semantic_type": "enum",
                "allowed_values": ["up", "down", "admin_down", "unknown"],
                "required": True,
            }
        ],
    },
}


LEGACY_ADAPTER = {
    "schema_version": "command-adapter/v1",
    "parser": {"template_id": "parser-interface-v1"},
    "outputs": [
        {
            "check_item_id": "interface.oper_status",
            "source_record_set": "interfaces",
            "fields": {
                "interface": {"source": "name"},
                "oper_status": {"source": "link"},
            },
        }
    ],
}


PARSER_FIELD_CONTRACT = {
    "parser_spec_record_sets": {"interfaces": ["name", "link"]}
}


REPLAY = {
    "status": "passed",
    "sample_count": 3,
    "collected_sample_count": 3,
    "standard_records_verified": True,
    "primary_keys_verified": True,
    "quality_status_verified": True,
    "rule_results_verified": True,
    "evidence_summary_verified": True,
    "evidence_digest": "a" * 64,
}


def test_migration_copies_explicit_enum_mapping_and_pins_check_item() -> None:
    result = migrate_legacy_adapter_to_v2(
        LEGACY_ADAPTER,
        check_items={"interface.oper_status": CHECK_ITEM},
        observation_contracts_by_check_item={
            "interface.oper_status": {
                "state_semantics": {
                    "oper_status": {
                        "value_mapping": {
                            "up": "up",
                            "down": "down",
                            "administratively down": "admin_down",
                            "unknown": "unknown",
                        },
                        "on_unmapped": "reject",
                    }
                }
            }
        },
        parser_field_contract=PARSER_FIELD_CONTRACT,
        sample_replay=REPLAY,
    )

    assert result["success"] is True
    assert result["status"] == "ready"
    output = result["adapter_spec"]["outputs"][0]
    assert output["check_item_version_id"] == "interface-oper-v6"
    assert output["projection"]["fields"]["oper_status"]["value_mapping"][
        "administratively down"
    ] == "admin_down"
    assert output["projection"]["fields"]["oper_status"]["on_unmapped"] == "reject"


def test_projection_context_includes_check_item_policy_dimension_constraints() -> None:
    check_item = {
        "version_id": "cpu-v1",
        "canonical_contract": {
            "entity_type": "cpu_scope",
            "identity_dimensions": ["scope", "scope_id"],
            "observations": [
                {
                    "canonical_field": "cpu_usage_percent",
                    "observation_type": "device.cpu.utilization",
                    "dimensions": ["window"],
                    "policy_constraints": {
                        "applies_to_dimensions": {"window": ["5m"]}
                    },
                }
            ],
        },
    }

    context = build_projection_generation_context(
        check_item_id="system.cpu_usage",
        check_item=check_item,
        parser_template_id="parser-cpu-v1",
        source_record_set="cpu_scope",
        parser_source_fields=["slot", "cpu_5m"],
    )

    assert context["observation_targets"][0]["policy_constraints"] == {
        "applies_to_dimensions": {"window": ["5m"]}
    }


def test_projection_rejects_a_literal_dimension_outside_check_item_policy() -> None:
    check_item = {
        "version_id": "cpu-v1",
        "canonical_contract": {
            "entity_type": "cpu_scope",
            "identity_dimensions": ["scope", "scope_id"],
            "observations": [
                {
                    "canonical_field": "cpu_usage_percent",
                    "observation_type": "device.cpu.utilization",
                    "dimensions": ["window"],
                    "policy_constraints": {
                        "applies_to_dimensions": {"window": ["5m"]}
                    },
                }
            ],
        },
    }
    context = build_projection_generation_context(
        check_item_id="system.cpu_usage",
        check_item=check_item,
        parser_template_id="parser-cpu-v1",
        source_record_set="cpu_scope",
        parser_source_fields=["slot", "cpu_5s", "cpu_5m"],
    )

    result = validate_projection_candidate(
        {
            "fields": {
                "scope": {"literal": "slot"},
                "scope_id": {"source": "slot"},
                "cpu_usage_percent": {
                    "source": "cpu_5s",
                    "dimensions": {"window": {"literal": "5s"}},
                },
            }
        },
        context=context,
        check_item=check_item,
    )

    assert result["success"] is False
    assert any(
        "cpu_usage_percent dimension window literal '5s' is not permitted" in error
        for error in result["errors"]
    )


def test_migration_never_guesses_an_enum_mapping() -> None:
    result = migrate_legacy_adapter_to_v2(
        LEGACY_ADAPTER,
        check_items={"interface.oper_status": CHECK_ITEM},
        parser_field_contract=PARSER_FIELD_CONTRACT,
        sample_replay=REPLAY,
    )

    assert result["success"] is False
    assert result["status"] == "blocked"
    assert any(
        item["code"] == "projection_validation_failed"
        and "requires an explicit fail-closed value_mapping" in item["message"]
        for item in result["blockers"]
    )


def test_cutover_report_requires_every_active_route_and_replay() -> None:
    ready_route = {
        "route_id": "h3c-interface",
        "implementation_version_id": "h3c-interface-v2",
        "adapter_spec": LEGACY_ADAPTER,
        "check_items": {"interface.oper_status": CHECK_ITEM},
        "observation_contracts_by_check_item": {
            "interface.oper_status": {
                "state_semantics": {
                    "oper_status": {
                        "value_mapping": {
                            "up": "up",
                            "down": "down",
                            "administratively down": "admin_down",
                            "unknown": "unknown",
                        }
                    }
                }
            }
        },
        "parser_field_contract": PARSER_FIELD_CONTRACT,
        "sample_replay": REPLAY,
    }
    blocked_route = {
        **ready_route,
        "route_id": "huawei-interface",
        "sample_replay": {},
    }

    report = build_cutover_readiness_report([ready_route, blocked_route])

    assert report["route_count"] == 2
    assert report["ready_route_count"] == 1
    assert report["production_cutover_permitted"] is False
    assert report["blockers"][0]["route_id"] == "huawei-interface"
    assert build_cutover_readiness_report([])["production_cutover_permitted"] is False


def test_cutover_report_requires_complete_replay_evidence_not_just_a_pass_flag() -> None:
    result = migrate_legacy_adapter_to_v2(
        LEGACY_ADAPTER,
        check_items={"interface.oper_status": CHECK_ITEM},
        observation_contracts_by_check_item={
            "interface.oper_status": {
                "state_semantics": {
                    "oper_status": {
                        "value_mapping": {
                            "up": "up",
                            "down": "down",
                            "administratively down": "admin_down",
                            "unknown": "unknown",
                        },
                        "on_unmapped": "reject",
                    }
                }
            }
        },
        parser_field_contract=PARSER_FIELD_CONTRACT,
        sample_replay={**REPLAY, "sample_count": 2},
    )

    assert result["success"] is False
    assert any(
        item["code"] == "sample_replay_coverage_incomplete"
        for item in result["blockers"]
    )


def test_cutover_inventory_freezes_rows_before_the_session_closes(
    monkeypatch,
) -> None:
    """Production sessions expire ORM state at commit; readiness is post-scope."""

    from src.infrastructure.db.models import (
        AuditLogRecord,
        CheckItemImplementationLinkRecord,
        CheckItemRecord,
        CheckItemVersionRecord,
        CollectionImplementationParserBindingRecord,
        CollectionImplementationVersionRecord,
        ObservationContractRecord,
        ParserTemplate,
    )

    class DetachedRow:
        def __init__(self, session, **values):
            object.__setattr__(self, "_session", session)
            object.__setattr__(self, "_values", values)

        def __getattribute__(self, name):
            if name.startswith("_"):
                return object.__getattribute__(self, name)
            session = object.__getattribute__(self, "_session")
            if session.closed:
                raise RuntimeError(f"detached ORM field read: {name}")
            return object.__getattribute__(self, "_values")[name]

    class Query:
        def __init__(self, rows):
            self.rows = rows

        def filter(self, *_args, **_kwargs):
            return self

        def order_by(self, *_args, **_kwargs):
            return self

        def all(self):
            return list(self.rows)

    class FakeSession:
        def __init__(self):
            self.closed = False
            self.rows = {}

        def query(self, model):
            return Query(self.rows.get(model, []))

    class FakeScope:
        def __init__(self, session):
            self.session = session

        def __enter__(self):
            return self.session

        def __exit__(self, *_args):
            self.session.closed = True
            return False

    session = FakeSession()
    implementation_id = "implementation-interface-v1"
    check_version_id = "check-item-interface-v1"
    parser_id = "parser-interface-v1"
    session.rows = {
        CollectionImplementationVersionRecord: [
            DetachedRow(
                session,
                id=implementation_id,
                adapter_spec_json=LEGACY_ADAPTER,
            )
        ],
        CheckItemImplementationLinkRecord: [
            DetachedRow(
                session,
                implementation_version_id=implementation_id,
                check_item_version_id=check_version_id,
            )
        ],
        CheckItemVersionRecord: [
            DetachedRow(
                session,
                id=check_version_id,
                check_item_id="interface.oper_status",
                status="active",
                canonical_contract_json=CHECK_ITEM["canonical_contract"],
            )
        ],
        CheckItemRecord: [
            DetachedRow(session, id="interface.oper_status")
        ],
        CollectionImplementationParserBindingRecord: [
            DetachedRow(
                session,
                implementation_version_id=implementation_id,
                parser_template_id=parser_id,
            )
        ],
        ParserTemplate: [
            DetachedRow(
                session,
                id=parser_id,
                validation_json={},
                template_text="Value NAME (\\S+)\n",
                status="active",
            )
        ],
        ObservationContractRecord: [
            DetachedRow(
                session,
                id="observation-contract-interface-v1",
                source_template_id=parser_id,
                state_semantics_json={
                    "oper_status": {
                        "value_mapping": {
                            "up": "up",
                            "down": "down",
                            "administratively down": "admin_down",
                            "unknown": "unknown",
                        },
                        "on_unmapped": "reject",
                    }
                },
                record_sets_json=[],
            )
        ],
        AuditLogRecord: [],
    }
    monkeypatch.setattr(
        "src.core.check_items.runtime_bundle_assembly.build_parser_field_contract",
        lambda **_kwargs: PARSER_FIELD_CONTRACT,
    )

    service = AdapterProjectionCutoverInventoryService(
        session_scope=lambda: FakeScope(session),
    )
    report = service.readiness(
        replay_by_implementation_version={implementation_id: REPLAY},
    )

    assert session.closed is True
    assert report["route_count"] == 1
    assert report["ready_route_count"] == 1
    assert report["production_cutover_permitted"] is True


def test_replay_attestation_is_audited_without_raw_cli_and_reuses_hard_gate(
    monkeypatch,
) -> None:
    class FakeQuery:
        def filter(self, *_args, **_kwargs):
            return self

        def first(self):
            return None

    class FakeSession:
        def __init__(self):
            self.added = []

        def query(self, _model):
            return FakeQuery()

        def add(self, row):
            self.added.append(row)

        def flush(self):
            return None

    class FakeScope:
        def __init__(self, session):
            self.session = session

        def __enter__(self):
            return self.session

        def __exit__(self, *_args):
            return False

    session = FakeSession()
    service = AdapterProjectionCutoverInventoryService(
        session_scope=lambda: FakeScope(session),
    )
    ready_report = {
        "digest": "d" * 64,
        "route_count": 1,
        "production_cutover_permitted": True,
        "routes": [{"implementation_version_id": "implementation:interface:v2"}],
    }
    monkeypatch.setattr(service, "readiness", lambda **_kwargs: dict(ready_report))
    attestation = {**REPLAY, "raw_cli": "must never be persisted here"}

    report = service.record_replay_attestations(
        replay_by_implementation_version={"implementation:interface:v2": attestation},
        actor="replay-engineer",
        idempotency_key="adapter-projection-replay-1",
    )

    assert report["recorded_implementation_version_ids"] == [
        "implementation:interface:v2"
    ]
    assert len(session.added) == 1
    audit = session.added[0]
    assert audit.action == "adapter_projection_replay_attested"
    assert audit.resource_id == "implementation:interface:v2"
    assert audit.detail["attestation"]["evidence_digest"] == "a" * 64
    assert "raw_cli" not in audit.detail["attestation"]


def test_cutover_authorization_cannot_write_when_global_gate_is_blocked(
    monkeypatch,
) -> None:
    class FakeSession:
        def __init__(self):
            self.added = []

        def query(self, _model):
            raise AssertionError("blocked authorization must not query or write audit rows")

    class FakeScope:
        def __init__(self, session):
            self.session = session

        def __enter__(self):
            return self.session

        def __exit__(self, *_args):
            return False

    session = FakeSession()
    service = AdapterProjectionCutoverInventoryService(
        session_scope=lambda: FakeScope(session),
    )
    monkeypatch.setattr(
        service,
        "readiness",
        lambda **_kwargs: {
            "digest": "d" * 64,
            "production_cutover_permitted": False,
            "route_count": 1,
        },
    )

    with pytest.raises(ValueError, match="all active structured routes"):
        service.authorize_cutover(
            actor="cutover-admin",
            reason="attempt blocked cutover",
            expected_readiness_digest="d" * 64,
            idempotency_key="adapter-projection-authorize-1",
        )

    assert session.added == []


def test_projection_validation_accepts_only_the_selected_contract_and_parser() -> None:
    result = generate_projection_candidate(
        {
            "mode": "projection_validate",
            "selected_check_item": {
                "id": "interface.oper_status",
                **CHECK_ITEM,
            },
            "parser": {
                "template_id": "parser-interface-v1",
                "source_record_set": "interfaces",
                "source_fields": ["name", "link"],
            },
            "proposal": {
                "schema_version": "command-adapter/v2",
                "parser": {"template_id": "parser-interface-v1"},
                "outputs": [
                    {
                        "check_item_id": "interface.oper_status",
                        "check_item_version_id": "interface-oper-v6",
                        "source_record_set": "interfaces",
                        "projection": {
                            "fields": {
                                "interface": {"source": "name"},
                                "oper_status": {
                                    "source": "link",
                                    "value_mapping": {
                                        "up": "up",
                                        "down": "down",
                                        "administratively down": "admin_down",
                                        "unknown": "unknown",
                                    },
                                    "on_unmapped": "reject",
                                },
                            }
                        },
                    }
                ],
            },
        }
    )

    assert result["success"] is True
    assert result["proposal"]["schema_version"] == "command-adapter/v2"
    assert result["generation_context"]["allowed_target_fields"] == [
        "interface",
        "oper_status",
    ]
    assert "records" not in result["generation_context"]


def test_global_cutover_freezes_legacy_observation_contract_writes(monkeypatch) -> None:
    from src.common import config
    from src.core.observation.repository import (
        ObservationContractWriteFrozenError,
        assert_observation_contract_writes_enabled,
    )

    monkeypatch.setattr(
        config,
        "get_settings",
        lambda: SimpleNamespace(ADAPTER_PROJECTION_CUTOVER_ENFORCED=True),
    )

    with pytest.raises(ObservationContractWriteFrozenError, match="writes are frozen"):
        assert_observation_contract_writes_enabled()


def test_generator_dispatches_v2_projection_validation_and_freezes_legacy_modes(
    monkeypatch,
) -> None:
    module = _load_projection_skill_module()
    projection = module.run_contract_generation(
        {
            "mode": "projection_validate",
            "selected_check_item": {"id": "interface.oper_status", **CHECK_ITEM},
            "parser": {
                "template_id": "parser-interface-v1",
                "source_record_set": "interfaces",
                "source_fields": ["name", "link"],
            },
            "proposal": {
                "schema_version": "command-adapter/v2",
                "parser": {"template_id": "parser-interface-v1"},
                "outputs": [
                    {
                        "check_item_id": "interface.oper_status",
                        "check_item_version_id": "interface-oper-v6",
                        "source_record_set": "interfaces",
                        "projection": {
                            "fields": {
                                "interface": {"source": "name"},
                                "oper_status": {
                                    "source": "link",
                                    "value_mapping": {
                                        "up": "up",
                                        "down": "down",
                                        "administratively down": "admin_down",
                                        "unknown": "unknown",
                                    },
                                    "on_unmapped": "reject",
                                },
                            }
                        },
                    }
                ],
            },
        }
    )

    assert projection["success"] is True
    monkeypatch.setattr(
        module,
        "get_settings",
        lambda: SimpleNamespace(ADAPTER_PROJECTION_CUTOVER_ENFORCED=True),
    )
    with pytest.raises(ValueError, match="generation is frozen"):
        module.run_contract_generation(
            {"mode": "infer_and_validate", "command": "show interface"}
        )
