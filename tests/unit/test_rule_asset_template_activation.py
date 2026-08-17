from __future__ import annotations

from types import SimpleNamespace

from src.core.parser import tasks as parser_tasks
from src.core.rule_control.template_activation import (
    RuleAssetTemplateActivationService,
    build_contract_seed,
)


def _active_template():
    return SimpleNamespace(
        id="template-1",
        status="active",
        version="1.0.0",
        vendor="Cisco",
        model="LAB-SW",
        family="IOS-LAB",
        category="interface_l3",
        canonical_command="show ip interface brief",
        contract_sha256="digest-1",
        validation_json={
            "entity_type": "interface_l3",
            "primary_keys": ["interface"],
            "required_fields": ["interface", "status", "protocol"],
            "optional_fields": ["ip_address"],
            "state_semantics": {
                "status": {
                    "semantic_type": "state",
                    "observation_type": "interface.oper_status",
                }
            },
            "contract_version": 1,
            "contract_sha256": "digest-1",
            "policy_intent": "Track operational status transitions.",
            "event_intent": "Generate InterfaceDown and InterfaceUp candidates.",
        },
    )


def _active_optical_template():
    return SimpleNamespace(
        id="template-optical-1",
        status="active",
        version="1.0.0",
        vendor="Cisco",
        model="LAB-SW",
        family="IOS-LAB",
        category="interface_optical",
        canonical_command="show interface transceiver detail",
        contract_sha256="digest-optical-1",
        validation_json={
            "entity_type": "interface_optical",
            "primary_keys": ["interface"],
            "required_fields": ["interface"],
            "optional_fields": ["rx_power_dbm", "tx_power_dbm"],
            "state_semantics": {
                "rx_power_dbm": {
                    "semantic_type": "gauge",
                    "observation_type": "interface.optical.rx_power",
                    "unit": "dbm",
                },
                "tx_power_dbm": {
                    "semantic_type": "gauge",
                    "observation_type": "interface.optical.tx_power",
                    "unit": "dbm",
                },
            },
            "contract_version": 1,
            "contract_sha256": "digest-optical-1",
            "policy_intent": "Use warning thresholds including -17 dBm.",
            "event_intent": "Generate OpticalRxPowerLow recovery candidates.",
        },
    )


def test_template_activation_seed_is_safe_and_idempotent():
    class FakeParserRepository:
        def get_template(self, template_id):
            assert template_id == "template-1"
            return _active_template()

    class FakeJobRepository:
        def __init__(self):
            self.created = []

        def find_job(self, **kwargs):
            assert kwargs["job_type"] == "OBSERVATION_CONTRACT"
            assert kwargs["source_ref"] == "parser_template:template-1"
            return self.created[0] if self.created else None

        def create_job(self, **kwargs):
            self.created.append({"id": "job-1", **kwargs})
            return self.created[-1]

    job_repository = FakeJobRepository()
    service = RuleAssetTemplateActivationService(
        parser_repository=FakeParserRepository(),
        job_repository=job_repository,
    )

    first = service.seed_from_template("template-1")
    second = service.seed_from_template("template-1")

    assert first["success"] is True
    assert first["created"] is True
    assert second["created"] is False
    assert len(job_repository.created) == 1
    payload = job_repository.created[0]["payload"]
    assert payload["privacy"] == {
        "raw_cli_included": False,
        "records_included": False,
        "historical_values_included": False,
    }
    assert "records" not in payload
    assert "raw_text" not in payload
    assert payload["workflow_context_defaults"]["observation_type"] == "interface.oper_status"
    assert "InterfaceDown" in payload["workflow_context_defaults"]["event_intent"]
    assert payload["missing_context"] == ["records"]


def test_template_activation_seed_compiles_all_targets_after_records_arrive():
    class FakeParserRepository:
        def get_template(self, template_id):
            assert template_id == "template-optical-1"
            return _active_optical_template()

    class FakeJobRepository:
        def find_job(self, **_kwargs):
            return None

        def create_job(self, **kwargs):
            return {"id": "job-optical-1", **kwargs}

    service = RuleAssetTemplateActivationService(
        parser_repository=FakeParserRepository(),
        job_repository=FakeJobRepository(),
    )

    result = service.seed_from_template("template-optical-1")

    defaults = result["job"]["payload"]["workflow_context_defaults"]
    assert "raw_cli_included" in result["job"]["payload"]["privacy"]
    assert "OpticalRxPowerLow" in defaults["event_intent"]
    assert "-17 dBm" in defaults["policy_intent"]
    assert [target["observation_type"] for target in defaults["policy_targets"]] == [
        "interface.optical.rx_power",
        "interface.optical.tx_power",
    ]
    assert result["missing_context"] == ["records"]


def test_contract_seed_keeps_sanitized_parser_labels_without_raw_evidence():
    template = SimpleNamespace(category="interface_optical")
    validation = {
        "entity_type": "multi_record",
        "record_sets": [
            {
                "name": "thresholds",
                "entity_type": "interface_threshold",
                "primary_keys": ["interface"],
                "required_fields": ["interface", "rx_power_low"],
                "optional_fields": [],
                "validators": {"rx_power_low": {"type": "number"}},
            }
        ],
        "parser_spec": {
            "version": "2",
            "record_sets": [
                {
                    "name": "thresholds",
                    "entity": "interface_threshold",
                    "fields": {
                        "interface": {
                            "required": True,
                            "type": "interface_name",
                            "source_labels": ["parent interface header"],
                            "evidence": ["Eth1/1 transceiver output"],
                        },
                        "rx_power_low": {
                            "required": True,
                            "type": "number",
                            "source_labels": ["Alarm Low RX power(dBm)"],
                            "role": "threshold",
                            "operational_reason": "raw-derived prose is excluded",
                            "evidence": ["Low -12.0"],
                        },
                    },
                }
            ],
        },
    }

    seed = build_contract_seed(template, validation)

    hints = seed["record_sets"][0]["source_field_hints"]
    assert hints["rx_power_low"] == {
        "source_labels": ["Alarm Low RX power(dBm)"],
        "role": "threshold",
    }
    assert "evidence" not in str(hints).casefold()
    assert "operational_reason" not in str(hints)


def test_publish_parser_outbox_seeds_rule_asset_generation(monkeypatch):
    class FakeOutboxRow:
        id = "event-1"
        event_type = "parser.template.activated"
        aggregate_id = "template-1"
        claim_token = "claim-1"
        payload_json = {"template_id": "template-1"}

    class FakeOutboxRepository:
        def claim_pending(self):
            return [FakeOutboxRow()]

        def mark_published(self, event_id, *, claim_token):
            assert event_id == "event-1"
            assert claim_token == "claim-1"

    class FakeRepository:
        pass

    class FakeSeedService:
        def __init__(self, *, parser_repository):
            assert isinstance(parser_repository, FakeRepository)

        def seed_from_template(self, template_id):
            assert template_id == "template-1"
            return {"success": True, "created": True, "job": {"id": "job-1"}}

    monkeypatch.setattr(parser_tasks, "ParserTemplateRepository", FakeRepository)
    monkeypatch.setattr(parser_tasks, "ParserOutboxRepository", FakeOutboxRepository)
    monkeypatch.setattr(parser_tasks, "RuleAssetTemplateActivationService", FakeSeedService)
    monkeypatch.setattr(parser_tasks.EventBus, "publish", lambda *_args, **_kwargs: "msg-1")

    result = parser_tasks.publish_parser_outbox()

    assert result["published"] == ["event-1"]
    assert result["rule_asset_seeds"] == [
        {
            "success": True,
            "created": True,
            "job": {"id": "job-1"},
            "next_action": "attach_offline_asset_source_dataset",
        }
    ]


def test_parser_outbox_releases_claim_when_rule_seed_fails(monkeypatch):
    released = []

    class FakeOutboxRow:
        id = "event-1"
        event_type = "parser.template.activated"
        aggregate_id = "template-1"
        claim_token = "claim-1"
        payload_json = {"template_id": "template-1"}

    class FakeOutboxRepository:
        def claim_pending(self):
            return [FakeOutboxRow()]

        def release(self, event_id, *, claim_token, error):
            released.append((event_id, claim_token, error))

        def mark_published(self, *_args, **_kwargs):
            raise AssertionError("failed rule seed must remain pending")

    class FakeRepository:
        pass

    class FakeSeedService:
        def __init__(self, *, parser_repository):
            assert isinstance(parser_repository, FakeRepository)

        def seed_from_template(self, _template_id):
            raise RuntimeError("database unavailable")

    monkeypatch.setattr(parser_tasks, "ParserTemplateRepository", FakeRepository)
    monkeypatch.setattr(parser_tasks, "ParserOutboxRepository", FakeOutboxRepository)
    monkeypatch.setattr(parser_tasks, "RuleAssetTemplateActivationService", FakeSeedService)
    monkeypatch.setattr(parser_tasks.EventBus, "publish", lambda *_args, **_kwargs: "msg-1")

    result = parser_tasks.publish_parser_outbox()

    assert result["published"] == []
    assert released == [("event-1", "claim-1", "database unavailable")]
    assert result["rule_asset_seeds"][0]["reason"] == "rule_asset_seed_failed"


def test_parser_outbox_acknowledges_obsolete_template_events(monkeypatch):
    marked = []
    released = []

    class FakeOutboxRow:
        id = "event-obsolete"
        event_type = "parser.template.activated"
        aggregate_id = "template-obsolete"
        claim_token = "claim-obsolete"
        payload_json = {"template_id": "template-obsolete"}

    class FakeOutboxRepository:
        def claim_pending(self):
            return [FakeOutboxRow()]

        def mark_published(self, event_id, *, claim_token):
            marked.append((event_id, claim_token))

        def release(self, event_id, *, claim_token, error):
            released.append((event_id, claim_token, error))

    class FakeRepository:
        pass

    class FakeSeedService:
        def __init__(self, *, parser_repository):
            assert isinstance(parser_repository, FakeRepository)

        def seed_from_template(self, template_id):
            return {
                "success": False,
                "created": False,
                "reason": "parser_template_not_found",
                "template_id": template_id,
            }

    monkeypatch.setattr(parser_tasks, "ParserTemplateRepository", FakeRepository)
    monkeypatch.setattr(parser_tasks, "ParserOutboxRepository", FakeOutboxRepository)
    monkeypatch.setattr(parser_tasks, "RuleAssetTemplateActivationService", FakeSeedService)
    monkeypatch.setattr(parser_tasks.EventBus, "publish", lambda *_args, **_kwargs: "msg-1")

    result = parser_tasks.publish_parser_outbox()

    assert result["published"] == ["event-obsolete"]
    assert result["rule_asset_seeds"] == [
        {
            "success": False,
            "created": False,
            "reason": "parser_template_not_found",
            "template_id": "template-obsolete",
            "skipped": True,
            "terminal": True,
        }
    ]
    assert marked == [("event-obsolete", "claim-obsolete")]
    assert released == []


def test_generation_context_requires_sanitized_records_and_rejects_raw_fields():
    class FakeJobRepository:
        def get_job(self, job_id):
            assert job_id == "job-1"
            return {
                "id": "job-1",
                "source_ref": "parser_template:template-1",
                "payload_json": {
                    "template_id": "template-1",
                    "workflow": "rule-asset-generation",
                    "missing_context": ["records"],
                    "workflow_context_defaults": {
                        "command": "show ip interface brief",
                        "vendor": "Cisco",
                        "model": "LAB-SW",
                        "source_ref": "parser_template:template-1",
                        "proposal": {"entity_type": "interface_l3"},
                        "observation_type": "interface.oper_status",
                        "field_name": "status",
                        "field_semantics": {
                            "semantic_type": "state",
                            "observation_type": "interface.oper_status",
                        },
                    },
                },
            }

    service = RuleAssetTemplateActivationService(job_repository=FakeJobRepository())

    missing = service.build_generation_context("job-1")
    ready = service.build_generation_context(
        "job-1",
        context_overrides={
            "records": [
                {
                    "interface": "GigabitEthernet1/0/1",
                    "status": "up",
                    "protocol": "up",
                }
            ]
        },
    )

    assert missing["ready"] is False
    assert missing["missing_context"] == ["records"]
    assert ready["ready"] is True
    assert ready["context"]["records"][0]["interface"] == "GigabitEthernet1/0/1"
    assert ready["context"]["schema_id"] == "template-1"
    assert ready["context"]["schema_ref"] == "parser_template:template-1"
    assert ready["context"]["source_template_id"] == "template-1"

    try:
        service.build_generation_context("job-1", context_overrides={"raw_text": "secret"})
    except ValueError as exc:
        assert "must not include private raw fields" in str(exc)
    else:
        raise AssertionError("raw context should be rejected")

    try:
        service.build_generation_context(
            "job-1",
            context_overrides={"records": [{"interface": "Gi1", "raw_output": "secret"}]},
        )
    except ValueError as exc:
        assert "records[0].raw_output" in str(exc)
    else:
        raise AssertionError("nested raw context should be rejected")


def test_start_generation_workflow_uses_prepared_context(monkeypatch):
    started = []

    class FakeJobRepository:
        def __init__(self):
            self.updated = []

        def get_job(self, _job_id):
            return {
                "id": "job-1",
                "source_ref": "parser_template:template-1",
                "payload_json": {
                    "template_id": "template-1",
                    "workflow": "rule-asset-generation",
                    "missing_context": ["records"],
                    "workflow_context_defaults": {
                        "command": "show ip interface brief",
                        "observation_type": "interface.oper_status",
                        "field_name": "status",
                        "field_semantics": {
                            "semantic_type": "state",
                            "observation_type": "interface.oper_status",
                        },
                    },
                },
            }

        def update_job(self, job_id, **changes):
            self.updated.append((job_id, changes))
            return {"id": job_id, **changes}

    class FakeWorkflowEngine:
        @staticmethod
        def start(template_name, context, **kwargs):
            started.append((template_name, context, kwargs))
            return "workflow-1"

    import src.core.workflows.engine as workflow_engine
    from src.core.rule_control.dataset_context import DatasetRuleGenerationContextService

    monkeypatch.setattr(workflow_engine, "WorkflowEngine", FakeWorkflowEngine)
    monkeypatch.setattr(
        DatasetRuleGenerationContextService,
        "load",
        lambda self, *, context_id, job_id: {
            "context_id": context_id,
            "context_digest": "digest-1",
            "context": {"records": [{"interface": "Gi1", "status": "up"}]},
            "privacy": {"success": True, "raw_cli_included": False},
        },
    )
    job_repository = FakeJobRepository()
    service = RuleAssetTemplateActivationService(job_repository=job_repository)

    result = service.start_generation_workflow(
        "job-1",
        context_id="context-1",
        actor="operator",
    )

    assert result["success"] is True
    assert result["run_id"] == "workflow-1"
    assert started[0][0] == "rule-asset-generation"
    assert started[0][1]["source_ref"] == "parser_template:template-1"
    assert started[0][1]["schema_id"] == "template-1"
    assert started[0][1]["source_template_id"] == "template-1"
    assert started[0][1]["rule_generation_seed_job_id"] == "job-1"
    assert started[0][1]["rule_generation_actor"] == "operator"
    assert started[0][2]["source"] == "parser-control-plane"
    assert started[0][2]["user_id"] == "operator"
    assert job_repository.updated[0][0] == "job-1"
    persisted_payload = job_repository.updated[0][1]["payload_json"]
    assert persisted_payload["latest_generation_run_id"] == "workflow-1"
    assert persisted_payload["latest_generation_context_id"] == "context-1"
    assert persisted_payload["generation_workflow_runs"] == [
        {
            "run_id": "workflow-1",
            "context_id": "context-1",
            "context_digest": "digest-1",
        }
    ]


def test_start_generation_workflow_rejects_context_without_offline_evidence():
    service = RuleAssetTemplateActivationService(job_repository=object())

    try:
        service.start_generation_workflow("job-1", actor="operator")
    except ValueError as exc:
        assert "immutable offline patrol context_id" in str(exc)
    else:
        raise AssertionError("generation should require immutable offline evidence")


def test_multi_entity_check_item_context_is_generation_ready():
    from src.core.rule_control.template_activation import (
        _generation_context_missing,
    )

    missing = _generation_context_missing(
        {
            "command": "show system resources",
            "records": [{"cpu_idle_percent": 95, "memory_used_kb": 1024}],
            "target_resolution": "resolved_multi_entity",
        },
        seed_missing=[],
    )

    assert missing == []
