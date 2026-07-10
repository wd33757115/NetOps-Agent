from __future__ import annotations

from types import SimpleNamespace

from src.core.parser import tasks as parser_tasks
from src.core.rule_control.template_activation import RuleAssetTemplateActivationService


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


def test_template_activation_seed_adds_optical_generation_intent():
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
    assert result["missing_context"] == ["records", "historical_values"]


def test_publish_parser_outbox_seeds_rule_asset_generation(monkeypatch):
    class FakeOutboxRow:
        id = "event-1"
        event_type = "parser.template.activated"
        aggregate_id = "template-1"
        payload_json = {"template_id": "template-1"}

    class FakeRepository:
        def list_pending_outbox(self):
            return [FakeOutboxRow()]

        def mark_outbox_published(self, event_id):
            assert event_id == "event-1"

    class FakeSeedService:
        def __init__(self, *, parser_repository):
            assert isinstance(parser_repository, FakeRepository)

        def seed_from_template(self, template_id):
            assert template_id == "template-1"
            return {"success": True, "created": True, "job": {"id": "job-1"}}

    monkeypatch.setattr(parser_tasks, "ParserRepository", FakeRepository)
    monkeypatch.setattr(parser_tasks, "RuleAssetTemplateActivationService", FakeSeedService)
    monkeypatch.setattr(parser_tasks.EventBus, "publish", lambda *_args, **_kwargs: "msg-1")

    result = parser_tasks.publish_parser_outbox()

    assert result["published"] == ["event-1"]
    assert result["rule_asset_seeds"] == [
        {"success": True, "created": True, "job": {"id": "job-1"}}
    ]


def test_generation_context_requires_sanitized_records_and_rejects_raw_fields():
    class FakeJobRepository:
        def get_job(self, job_id):
            assert job_id == "job-1"
            return {
                "id": "job-1",
                "source_ref": "parser_template:template-1",
                "payload_json": {
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
        def get_job(self, _job_id):
            return {
                "id": "job-1",
                "source_ref": "parser_template:template-1",
                "payload_json": {
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

    class FakeWorkflowEngine:
        @staticmethod
        def start(template_name, context, **kwargs):
            started.append((template_name, context, kwargs))
            return "workflow-1"

    import src.core.workflows.engine as workflow_engine

    monkeypatch.setattr(workflow_engine, "WorkflowEngine", FakeWorkflowEngine)
    service = RuleAssetTemplateActivationService(job_repository=FakeJobRepository())

    result = service.start_generation_workflow(
        "job-1",
        context_overrides={"records": [{"interface": "Gi1", "status": "up"}]},
        actor="operator",
    )

    assert result["success"] is True
    assert result["run_id"] == "workflow-1"
    assert started[0][0] == "rule-asset-generation"
    assert started[0][1]["source_ref"] == "parser_template:template-1"
    assert started[0][2]["source"] == "parser-control-plane"
    assert started[0][2]["user_id"] == "operator"
