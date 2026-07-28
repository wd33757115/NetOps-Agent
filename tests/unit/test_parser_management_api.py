from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from src.auth.models import CurrentUser
from src.core.parser.assets import TemplateAssetStore
from src.gateway import parser_api


def _admin_request(username: str = "operator"):
    return SimpleNamespace(
        state=SimpleNamespace(
            current_user=CurrentUser(user_id=f"user:{username}", username=username, role="admin")
        )
    )


def test_parser_spec_and_sample_roles_are_engineer_confirmed_and_audited(monkeypatch):
    calls: list[tuple[str, dict]] = []

    class FakeJobRepository:
        def set_parser_spec(self, job_id, **payload):
            calls.append(("spec", {"job_id": job_id, **payload}))
            return {"id": job_id, "parser_spec_status": "confirmed"}

        def set_sample_role(self, job_id, sample_id, **payload):
            calls.append(
                ("sample", {"job_id": job_id, "sample_id": sample_id, **payload})
            )
            return {"id": sample_id, "sample_role": payload["role"]}

    monkeypatch.setattr(parser_api, "ParserJobRepository", FakeJobRepository)
    spec = {
        "entity": "interface",
        "primary_keys": ["interface"],
        "fields": {
            "interface": {"required": True, "type": "interface_name"},
            "status": {"required": True, "type": "enum", "values": ["UP", "DOWN"]},
        },
    }

    spec_result = parser_api.update_parser_spec(
        "job-1",
        _admin_request("engineer"),
        parser_api.ParserSpecUpdateRequest(
            parser_spec=spec,
            confirmed=True,
            reason="sample fields reviewed",
        ),
    )
    sample_result = parser_api.update_parser_sample_role(
        "job-1",
        "sample-1",
        _admin_request("engineer"),
        parser_api.ParserSampleRoleUpdateRequest(
            role="acceptance",
            expectation={"record_count": 1, "primary_keys": [["GE1/0/1"]]},
            reason="reserve independent acceptance evidence",
        ),
    )

    assert spec_result["job"]["parser_spec_status"] == "confirmed"
    assert sample_result["sample"]["sample_role"] == "acceptance"
    assert calls[0][1]["actor"] == "engineer"
    assert calls[0][1]["reason"] == "sample fields reviewed"
    assert calls[1][1]["expectation"]["primary_keys"] == [["GE1/0/1"]]


def test_simple_parser_dispatch_reports_readiness_blockers(monkeypatch):
    class FakeJobRepository:
        def get_job(self, job_id):
            assert job_id == "job-1"
            return {
                "id": job_id,
                "pipeline_version": "simple_v2",
                "generation_readiness": {
                    "ready": False,
                    "blockers": ["parser_spec_not_confirmed"],
                },
            }

    monkeypatch.setattr(parser_api, "ParserJobRepository", FakeJobRepository)

    with pytest.raises(HTTPException) as exc_info:
        parser_api.dispatch_job("job-1")

    assert exc_info.value.status_code == 409
    assert "parser_spec_not_confirmed" in str(exc_info.value.detail)


def test_recover_endpoint_fails_stale_job_and_revokes_task(monkeypatch):
    calls: list[tuple[str, dict]] = []

    class FakeJobRepository:
        def recover_stale_job(self, job_id, **payload):
            calls.append(("recover", {"job_id": job_id, **payload}))
            return {
                "id": job_id,
                "status": "failed",
                "failure_code": "stale_running_recovered",
                "recovered_celery_task_id": "task-9",
            }

    class FakeControl:
        def revoke(self, task_id, terminate):
            calls.append(("revoke", {"task_id": task_id, "terminate": terminate}))

    monkeypatch.setattr(parser_api, "ParserJobRepository", FakeJobRepository)
    monkeypatch.setattr(
        parser_api, "celery", SimpleNamespace(control=FakeControl())
    )

    result = parser_api.recover_parser_job(
        "job-1",
        _admin_request("engineer"),
        parser_api.ParserJobRecoverRequest(reason="generation worker crashed"),
    )

    assert result["success"] is True
    assert result["status"] == "failed"
    assert result["job"]["failure_code"] == "stale_running_recovered"
    assert calls[0][1]["actor"] == "engineer"
    assert calls[0][1]["reason"] == "generation worker crashed"
    assert ("revoke", {"task_id": "task-9", "terminate": False}) in calls


def test_recover_endpoint_keeps_409_for_fresh_running_job(monkeypatch):
    from src.core.parser.repository import ParserJobStateError

    class FakeJobRepository:
        def recover_stale_job(self, job_id, **_payload):
            raise ParserJobStateError(
                f"parser job {job_id} is still running within the stale timeout; "
                "recovery is allowed in 100 seconds"
            )

    monkeypatch.setattr(parser_api, "ParserJobRepository", FakeJobRepository)

    with pytest.raises(HTTPException) as exc_info:
        parser_api.recover_parser_job("job-1", _admin_request("engineer"), None)

    assert exc_info.value.status_code == 409
    assert "still running" in str(exc_info.value.detail)


def test_parser_spec_suggestion_is_queued_without_starting_generation_lifecycle(monkeypatch):
    calls: list[tuple[str, dict]] = []

    class FakeJobRepository:
        def queue_parser_spec_suggestion(self, job_id, **payload):
            calls.append(("queue", {"job_id": job_id, **payload}))
            return {"id": job_id}

        def record_parser_spec_suggestion(self, *_args, **_kwargs):
            raise AssertionError("dispatch should not fail")

    class FakeCelery:
        def send_task(self, name, **payload):
            calls.append((name, payload))

    monkeypatch.setattr(parser_api, "ParserJobRepository", FakeJobRepository)
    monkeypatch.setattr(parser_api, "celery", FakeCelery())

    result = parser_api.suggest_parser_spec(
        "job-1",
        _admin_request("engineer"),
        parser_api.ParserSpecSuggestionRequest(),
    )

    assert result["status"] == "queued"
    assert calls[0][0] == "queue"
    assert calls[0][1]["actor"] == "engineer"
    assert calls[1][0] == "execute_skill_task"
    assert calls[1][1]["kwargs"]["operation"] == "suggest_spec"


def test_asset_production_list_uses_current_user_scope(monkeypatch):
    captured: dict = {}

    class FakeService:
        def list_production_units(self, **kwargs):
            captured.update(kwargs)
            return {"items": [], "total": 0, "summary": {}}

    monkeypatch.setattr(parser_api, "RuleAssetCommandUnitService", FakeService)
    user = CurrentUser(user_id="user:engineer", username="engineer", role="operator")
    result = parser_api.list_asset_production_units(
        page=2,
        page_size=50,
        bucket="action_required",
        user=user,
    )

    assert result["total"] == 0
    assert captured["owner_id"] == "user:engineer"
    assert captured["is_admin"] is False
    assert captured["page"] == 2
    assert captured["bucket"] == "action_required"


def test_parser_template_comparison_returns_complete_human_approval_evidence(
    monkeypatch,
    tmp_path,
):
    template_path = tmp_path / "candidate.textfsm"
    template_text = "Value interface (\\S+)\n\nStart\n  ^${interface} -> Record\n"
    template_path.write_text(template_text, encoding="utf-8")
    candidate = SimpleNamespace(
        id="template-candidate",
        vendor="H3C",
        model="S5130S",
        family=None,
        profile="S5130S",
        canonical_command="display interface brief",
        category="interface",
        version="v2",
        status="pending_approval",
        sha256="candidate-sha",
        asset_path=str(template_path),
        validation_json={},
        contract_sha256="contract-new",
        source_job_id=None,
    )
    active = SimpleNamespace(
        id="template-active",
        validation_json={},
        contract_sha256="contract-old",
    )
    recorded: list[dict] = []

    class FakeTemplateRepository:
        def get_template(self, template_id):
            assert template_id == candidate.id
            return candidate

        def resolve_active(self, **kwargs):
            assert kwargs["command"] == candidate.canonical_command
            return active

        def record_regression_validation(self, template_id, **payload):
            recorded.append({"template_id": template_id, **payload})

    class FakeAssetStore:
        root = tmp_path

        @staticmethod
        def verify(path, sha256, embedded_text=None):
            assert embedded_text is None
            assert Path(path) == template_path
            assert sha256 == "candidate-sha"
            return True

        @staticmethod
        def read(path, sha256, embedded_text=None):
            assert FakeAssetStore.verify(path, sha256, embedded_text)
            return template_path.read_text(encoding="utf-8")

    snapshot = SimpleNamespace(
        snapshot_id="snapshot-1",
        dataset_id="dataset-q3",
        device_id="SW1",
        vendor="H3C",
        model="S5130S",
        command_canonical="display interface brief",
        raw_text="Interface Link\nGE1/0/1 UP\n",
        raw_text_hash=None,
    )

    class FakePatrolRepository:
        @staticmethod
        def list_dataset_snapshots(*, mode):
            assert mode == "parser_validation"
            return [snapshot]

    monkeypatch.setattr(parser_api, "ParserTemplateRepository", FakeTemplateRepository)
    monkeypatch.setattr(parser_api, "TemplateAssetStore", FakeAssetStore)
    monkeypatch.setattr(parser_api, "PatrolRepository", FakePatrolRepository)
    monkeypatch.setattr(
        parser_api,
        "_parse_snapshot_with_template",
        lambda template, _snapshot: {
            "status": "success",
            "failure_code": None,
            "records": [{"interface": "GE1/0/1", "status": "UP"}],
        },
    )

    result = parser_api.compare_template(candidate.id)

    assert result["approval_readiness"] == {"can_approve": True, "blockers": []}
    assert result["candidate_template"]["content"] == template_text
    assert result["candidate_template"]["hash_valid"] is True
    assert result["rows"][0]["raw_text"] == snapshot.raw_text
    assert result["rows"][0]["raw_text_sha256"]
    assert result["rows"][0]["proposed"]["records"][0]["status"] == "UP"
    assert recorded == [{
        "template_id": candidate.id,
        "passed": True,
        "sample_count": 1,
        "passed_samples": 1,
        "result_preview": [{
            "snapshot_id": "snapshot-1",
            "raw_text_sha256": result["rows"][0]["raw_text_sha256"],
            "status": "success",
            "records": [{"interface": "GE1/0/1", "status": "UP"}],
            "primary_keys": [[]],
        }],
    }]


def test_parser_template_comparison_scopes_evidence_to_source_job(
    monkeypatch,
    tmp_path,
):
    template_path = tmp_path / "candidate.textfsm"
    template_text = "Value interface (\\S+)\n\nStart\n  ^${interface} -> Record\n"
    template_path.write_text(template_text, encoding="utf-8")
    candidate = SimpleNamespace(
        id="template-candidate",
        vendor="Cisco",
        model="N9K-C9364C",
        family="NXOS9K",
        profile="N9K-C9364C",
        canonical_command="show interface status",
        category="interface",
        version="simple-v1",
        status="pending_approval",
        sha256="candidate-sha",
        asset_path=str(template_path),
        validation_json={},
        contract_sha256="contract-new",
        source_job_id="parser-job-1",
    )
    scoped_snapshot = SimpleNamespace(
        snapshot_id="snapshot-scoped",
        dataset_id="offline-job-current",
        device_id="switch-1",
        vendor="Cisco",
        model="N9K-C9364C",
        command_canonical="show interface status",
        raw_text="Eth1/1 uplink connected trunk full 100G QSFP\n",
        raw_text_hash=None,
    )
    historical_snapshot = SimpleNamespace(
        snapshot_id="snapshot-historical",
        dataset_id="historical",
        device_id="switch-old",
        vendor="Cisco",
        model="N9K-C9364C",
        command_canonical="show interface status",
        raw_text="Eth1/2 old notconnect 1 auto auto --\n",
        raw_text_hash=None,
    )

    class FakeTemplateRepository:
        def get_template(self, template_id):
            assert template_id == candidate.id
            return candidate

        def resolve_active(self, **_kwargs):
            return None

        def record_regression_validation(self, *_args, **_kwargs):
            return None

    class FakeAssetStore:
        root = tmp_path

        @staticmethod
        def verify(path, sha256, embedded_text=None):
            return Path(path) == template_path and sha256 == "candidate-sha"

        @staticmethod
        def read(path, sha256, embedded_text=None):
            assert FakeAssetStore.verify(path, sha256, embedded_text)
            return template_path.read_text(encoding="utf-8")

    class FakePatrolRepository:
        @staticmethod
        def list_dataset_snapshots(*, mode):
            assert mode == "parser_validation"
            return [historical_snapshot, scoped_snapshot]

    class FakeParserJobRepository:
        @staticmethod
        def get_job(job_id):
            assert job_id == "parser-job-1"
            return {"samples": [{"snapshot_id": "snapshot-scoped"}]}

    monkeypatch.setattr(parser_api, "ParserTemplateRepository", FakeTemplateRepository)
    monkeypatch.setattr(parser_api, "TemplateAssetStore", FakeAssetStore)
    monkeypatch.setattr(parser_api, "PatrolRepository", FakePatrolRepository)
    monkeypatch.setattr(parser_api, "ParserJobRepository", FakeParserJobRepository)
    monkeypatch.setattr(
        parser_api,
        "_parse_snapshot_with_template",
        lambda _template, _snapshot: {
            "status": "success",
            "failure_code": None,
            "records": [{"interface": "Eth1/1"}],
        },
    )

    result = parser_api.compare_template(candidate.id)

    assert result["sample_count"] == 1
    assert [row["snapshot_id"] for row in result["rows"]] == ["snapshot-scoped"]


def test_template_preview_reads_embedded_candidate_without_a_local_file(
    monkeypatch,
    tmp_path,
):
    content = "Value item (\\S+)\n\nStart\n  ^${item} -> Record\n"
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    template = SimpleNamespace(
        id="template-embedded",
        vendor="Generic",
        profile="Generic",
        canonical_command="inspect item",
        category="item",
        version="simple-1",
        status="pending_approval",
        sha256=digest,
        asset_path=f"postgresql://parser-template/{digest}",
        template_text=content,
        validation_json={"simple_parser": {"status": "candidate_ready"}},
    )
    monkeypatch.setattr(
        parser_api,
        "TemplateAssetStore",
        lambda: TemplateAssetStore(tmp_path),
    )

    evidence = parser_api._template_asset_evidence(template)

    assert evidence["content"] == content
    assert evidence["hash_valid"] is True
    assert not any(tmp_path.iterdir())


def test_effective_plan_preview_returns_commands(monkeypatch):
    class FakePlan:
        def to_metadata(self):
            return {
                "profile_name": "core",
                "profile_version_id": "v1",
                "collection_items": [{"command": "show version", "action": "patrol"}],
            }

        def command_texts(self):
            return ["show version"]

    class FakeResolver:
        def build_plan(self, *, device, action, mode):
            assert device["device_id"] == "device-1"
            assert action == "patrol"
            assert mode == "production"
            return FakePlan()

    monkeypatch.setattr(parser_api, "CollectionPolicyResolver", FakeResolver)

    result = parser_api.effective_plan_preview(
        parser_api.EffectivePlanPreviewRequest(
            device={"device_id": "device-1"},
            action="patrol",
            mode="production",
        )
    )

    assert result["success"] is True
    assert result["commands"] == ["show version"]


def test_dataset_coverage_uses_current_offline_patrol_run_scope(monkeypatch):
    class FakeRepository:
        def list_datasets(self, *, mode=None, dataset_ids=None):
            assert mode is None
            assert dataset_ids == ["offline-job-current"]
            return [
                {
                    "id": "offline-job-current",
                    "snapshot_count": 99,
                    "device_count": 9,
                    "command_count": 9,
                }
            ]

        def get_offline_dataset_run_scopes(self, dataset_ids):
            assert dataset_ids == ["offline-job-current"]
            return {
                "offline-job-current": {
                    "offline_job_id": "offline-job",
                    "job_purpose": "rule_asset_source",
                    "period_role": "current",
                    "observed_at": "2026-06-26T14:06:14+08:00",
                    "patrol_run_id": "offline-job-current-attempt-5",
                    "period_status": "completed",
                }
            }

        def list_run_snapshots(self, run_id):
            assert run_id == "offline-job-current-attempt-5"
            return [
                SimpleNamespace(
                    snapshot_id="snapshot-parsed",
                    parse_status="parsed",
                    device_id="device-1",
                    command_canonical="display interface brief",
                ),
                SimpleNamespace(
                    snapshot_id="snapshot-empty",
                    parse_status="parse_empty",
                    device_id="device-1",
                    command_canonical="display transceiver interface",
                ),
                SimpleNamespace(
                    snapshot_id="snapshot-missing",
                    parse_status="template_missing",
                    device_id="device-2",
                    command_canonical="display vrf",
                ),
                SimpleNamespace(
                    snapshot_id="snapshot-ignored",
                    parse_status="ignored",
                    device_id="device-2",
                    command_canonical="display current-configuration",
                ),
            ]

        def list_dataset_snapshots(self, *args, **kwargs):
            raise AssertionError("offline coverage must not aggregate historical attempts")

        def list_snapshots_with_entity_keys(self, snapshot_ids):
            assert len(snapshot_ids) == 1
            return set(snapshot_ids)

    monkeypatch.setattr(parser_api, "PatrolRepository", FakeRepository)

    result = parser_api.dataset_coverage("offline-job-current,offline-job-current")

    assert result["missing_dataset_ids"] == []
    assert len(result["items"]) == 1
    item = result["items"][0]
    assert item["job_purpose"] == "rule_asset_source"
    assert item["scope_type"] == "offline_patrol_run"
    assert item["patrol_run_id"] == "offline-job-current-attempt-5"
    assert item["snapshot_count"] == 4
    assert item["device_count"] == 2
    assert item["command_count"] == 4
    assert item["eligible_count"] == 3
    assert item["structured_coverage_rate"] == 2 / 3
    assert item["missing_template_rate"] == 1 / 3
    assert item["primary_key_stability_rate"] == 1.0
    assert item["primary_key_evaluated_count"] == 1


def test_event_rule_bootstrap_endpoint(monkeypatch):
    class FakeRepository:
        def ensure_default_rules(self, *, actor, reason):
            assert actor == "operator"
            assert reason == "restore baseline rules"
            return 9

    monkeypatch.setattr(parser_api, "EventRuleRepository", FakeRepository)

    assert parser_api.bootstrap_event_rules(
        _admin_request(),
        parser_api.CommandProfileLifecycleRequest(reason="restore baseline rules"),
    ) == {"success": True, "created": 9}


def test_event_rule_test_endpoint_matches_samples():
    result = parser_api.test_event_rule(
        parser_api.EventRuleTestRequest(
            condition={
                "kind": "threshold",
                "fields": ["cpu_5s"],
                "gte": 90,
            },
            samples=[
                {"field": "cpu_5s", "new": 95},
                {"field": "cpu_5s", "new": 20},
                {"field": "memory", "new": 95},
            ],
        )
    )

    assert result["success"] is True
    assert result["sample_count"] == 3
    assert result["matched_count"] == 1
    assert result["unmatched_count"] == 2
    assert [row["matched"] for row in result["rows"]] == [True, False, False]


def test_event_rule_validation_endpoint_uses_builtin_generic_cases():
    result = parser_api.validate_event_rules(None)

    assert result["success"] is True
    assert result["failed_count"] == 0
    assert result["case_count"] >= 8
    assert result["rule_count"] >= 10
    assert all("device_id" not in row["change"] for row in result["invariant_result"]["rows"])


def test_event_rule_replay_endpoint(monkeypatch):
    captured = {}

    def fake_replay(definitions, *, limit, include_active_collisions):
        captured["definitions"] = definitions
        captured["limit"] = limit
        captured["include_active_collisions"] = include_active_collisions
        return {
            "success": True,
            "matched_change_count": 0,
            "collision_count": 0,
        }

    monkeypatch.setattr(parser_api, "replay_event_rule_definitions", fake_replay)

    result = parser_api.replay_event_rules(
        parser_api.EventRuleReplayRequest(
            limit=50,
            include_active_collisions=False,
            candidate_rules=[
                parser_api.EventRuleCandidateRequest(
                    name="cpu-high-candidate",
                    event_type="CPUHigh",
                    severity="warning",
                    condition={"kind": "threshold", "fields": ["cpu_5s"], "gte": 90},
                )
            ],
        )
    )

    assert result["success"] is True
    assert captured["limit"] == 50
    assert captured["include_active_collisions"] is False
    assert captured["definitions"][0].rule_id == "cpu-high-candidate"


def test_event_rule_list_endpoint_passes_version_filters(monkeypatch):
    captured = {}

    class FakeRepository:
        def list_versions_page(self, *, status, event_type, search, limit, offset):
            captured["status"] = status
            captured["event_type"] = event_type
            captured["limit"] = limit
            captured["search"] = search
            captured["offset"] = offset
            items = [
                {
                    "rule_id": "rule-1",
                    "version_id": "version-1",
                    "version_status": status,
                    "event_type": event_type,
                    "source_job_id": "job-1",
                    "validation_report": {"success": True},
                    "digest": "abc",
                }
            ]
            return {"items": items, "total": 1, "limit": limit, "offset": offset}

    monkeypatch.setattr(parser_api, "EventRuleRepository", FakeRepository)

    result = parser_api.list_event_rules(
        status="pending_approval",
        event_type="CPUHigh",
        limit=25,
    )

    assert result["items"] == [
        {
            "rule_id": "rule-1",
            "version_id": "version-1",
            "version_status": "pending_approval",
            "event_type": "CPUHigh",
            "source_job_id": "job-1",
            "validation_report": {"success": True},
            "digest": "abc",
        }
    ]
    assert captured == {
        "status": "pending_approval",
        "event_type": "CPUHigh",
        "limit": 25,
        "search": None,
        "offset": 0,
    }


def test_work_queue_endpoint_passes_server_pagination_and_filters(monkeypatch):
    captured = {}

    class FakeService:
        def list_items(self, **kwargs):
            captured.update(kwargs)
            return {
                "items": [{"id": "work-1", "task_type": "parser"}],
                "total": 41,
                "limit": kwargs["limit"],
                "offset": kwargs["offset"],
            }

    monkeypatch.setattr(parser_api, "ParserWorkQueueService", FakeService)

    result = parser_api.list_parser_work_items(
        task_type="parser",
        status="failed",
        search="display interface",
        limit=25,
        offset=50,
    )

    assert result["total"] == 41
    assert captured == {
        "task_type": "parser",
        "status": "failed",
        "search": "display interface",
        "limit": 25,
        "offset": 50,
    }


def test_observation_contract_list_endpoint(monkeypatch):
    captured = {}

    class FakeRepository:
        def list_contracts_page(self, *, source_type, status, search, limit, offset):
            captured["source_type"] = source_type
            captured["status"] = status
            captured["limit"] = limit
            captured["search"] = search
            captured["offset"] = offset
            return {
                "items": [{"id": "contract-1", "status": status}],
                "total": 1,
                "limit": limit,
                "offset": offset,
            }

    monkeypatch.setattr(parser_api, "ObservationContractRepository", FakeRepository)

    result = parser_api.list_observation_contracts(
        source_type="cli",
        status="active",
        limit=25,
    )

    assert result["items"] == [{"id": "contract-1", "status": "active"}]
    assert captured == {
        "source_type": "cli",
        "status": "active",
        "limit": 25,
        "search": None,
        "offset": 0,
    }


def test_change_policy_list_endpoint(monkeypatch):
    captured = {}

    class FakeRepository:
        def list_versions_page(self, *, status, observation_type, search, limit, offset):
            captured["status"] = status
            captured["observation_type"] = observation_type
            captured["limit"] = limit
            captured["search"] = search
            captured["offset"] = offset
            return {
                "items": [{"version_id": "policy-version-1", "version_status": status}],
                "total": 1,
                "limit": limit,
                "offset": offset,
            }

    monkeypatch.setattr(parser_api, "ChangePolicyRepository", FakeRepository)

    result = parser_api.list_change_policies(
        status="draft",
        observation_type="device.cpu.utilization",
        limit=50,
    )

    assert result["items"] == [{"version_id": "policy-version-1", "version_status": "draft"}]
    assert captured == {
        "status": "draft",
        "observation_type": "device.cpu.utilization",
        "limit": 50,
        "search": None,
        "offset": 0,
    }


def test_replay_report_list_endpoint(monkeypatch):
    captured = {}

    class FakeRepository:
        def list_replay_reports_page(self, *, success, asset_type, search, limit, offset):
            captured["success"] = success
            captured["asset_type"] = asset_type
            captured["limit"] = limit
            captured["search"] = search
            captured["offset"] = offset
            items = [
                {
                    "id": "job-1:historical_replay",
                    "job_id": "job-1",
                    "success": success,
                    "asset_type": asset_type,
                    "matched_change_count": 2,
                    "collision_count": 0,
                }
            ]
            return {"items": items, "total": 1, "limit": limit, "offset": offset}

    monkeypatch.setattr(parser_api, "RuleGenerationJobRepository", FakeRepository)

    result = parser_api.list_replay_reports(
        success=True,
        asset_type="EVENT_RULE",
        limit=25,
    )

    assert result["items"] == [
        {
            "id": "job-1:historical_replay",
            "job_id": "job-1",
            "success": True,
            "asset_type": "EVENT_RULE",
            "matched_change_count": 2,
            "collision_count": 0,
        }
    ]
    assert captured == {
        "success": True,
        "asset_type": "EVENT_RULE",
        "limit": 25,
        "search": None,
        "offset": 0,
    }


def test_rule_asset_approval_audit_endpoint(monkeypatch):
    captured = {}

    class FakeRepository:
        def list_approval_audit_page(self, *, action, asset_type, actor, search, limit, offset):
            captured["action"] = action
            captured["asset_type"] = asset_type
            captured["actor"] = actor
            captured["limit"] = limit
            captured["search"] = search
            captured["offset"] = offset
            items = [
                {
                    "id": "job-1:approved",
                    "job_id": "job-1",
                    "action": action,
                    "asset_type": asset_type,
                    "actor": actor,
                    "validation_success": True,
                }
            ]
            return {"items": items, "total": 1, "limit": limit, "offset": offset}

    monkeypatch.setattr(parser_api, "RuleGenerationJobRepository", FakeRepository)

    result = parser_api.list_rule_asset_approval_audit(
        action="approved",
        asset_type="EVENT_RULE",
        actor="operator",
        limit=25,
    )

    assert result["items"] == [
        {
            "id": "job-1:approved",
            "job_id": "job-1",
            "action": "approved",
            "asset_type": "EVENT_RULE",
            "actor": "operator",
            "validation_success": True,
        }
    ]
    assert captured == {
        "action": "approved",
        "asset_type": "EVENT_RULE",
        "actor": "operator",
        "limit": 25,
        "search": None,
        "offset": 0,
    }


def test_agent_loop_attempts_endpoint(monkeypatch):
    captured = {}

    class FakeRepository:
        def list_agent_loop_attempts_page(
            self, *, asset_type, stage, success, search, limit, offset
        ):
            captured["asset_type"] = asset_type
            captured["stage"] = stage
            captured["success"] = success
            captured["limit"] = limit
            captured["search"] = search
            captured["offset"] = offset
            items = [
                {
                    "id": "job-1:agent_loop:1",
                    "job_id": "job-1",
                    "asset_type": asset_type,
                    "stage": stage,
                    "success": success,
                    "attempt": 1,
                    "revision_request": {
                        "target_generator": "event-rule-generator",
                    },
                }
            ]
            return {"items": items, "total": 1, "limit": limit, "offset": offset}

    monkeypatch.setattr(parser_api, "RuleGenerationJobRepository", FakeRepository)

    result = parser_api.list_rule_asset_agent_loop_attempts(
        asset_type="EVENT_RULE",
        stage="event_rule_validation",
        success=False,
        limit=25,
    )

    assert result["items"] == [
        {
            "id": "job-1:agent_loop:1",
            "job_id": "job-1",
            "asset_type": "EVENT_RULE",
            "stage": "event_rule_validation",
            "success": False,
            "attempt": 1,
            "revision_request": {
                "target_generator": "event-rule-generator",
            },
        }
    ]
    assert captured == {
        "asset_type": "EVENT_RULE",
        "stage": "event_rule_validation",
        "success": False,
        "limit": 25,
        "search": None,
        "offset": 0,
    }


def test_rule_generation_job_create_endpoint(monkeypatch):
    class FakeRepository:
        def create_job(
            self,
            *,
            job_type,
            source_ref,
            asset_ref,
            asset_type,
            asset_id,
            asset_version_id,
            asset_digest,
            payload,
            validation_report,
            approval_required,
            created_by,
            status,
        ):
            assert job_type == "EVENT_RULE"
            assert source_ref == "contract:1"
            assert asset_ref is None
            assert asset_type is None
            assert asset_id is None
            assert asset_version_id is None
            assert asset_digest is None
            assert payload == {"candidate": True}
            assert validation_report == {"success": True}
            assert approval_required is True
            assert created_by == "server-admin"
            assert status == "draft"
            return {"id": "job-1", "job_type": job_type, "status": status}

    monkeypatch.setattr(parser_api, "RuleGenerationJobRepository", FakeRepository)

    result = parser_api.create_rule_generation_job(
        _admin_request("server-admin"),
        parser_api.RuleGenerationJobCreateRequest(
            job_type="EVENT_RULE",
            source_ref="contract:1",
            payload={"candidate": True},
            validation_report={"success": True},
            created_by="unit-test",
        ),
    )

    assert result == {"id": "job-1", "job_type": "EVENT_RULE", "status": "draft"}


def test_rule_generation_job_transition_endpoint(monkeypatch):
    try:
        parser_api.transition_rule_generation_job(
            "job-1",
            parser_api.RuleGenerationJobTransitionRequest(status="validating"),
        )
    except parser_api.HTTPException as exc:
        assert exc.status_code == 409
        assert "transitions are disabled" in exc.detail
    else:
        raise AssertionError("direct transitions must be rejected")


def test_rule_generation_job_validation_report_endpoint(monkeypatch):
    try:
        parser_api.attach_rule_generation_validation_report(
            "job-1",
            parser_api.RuleGenerationJobValidationRequest(validation_report={"success": True}),
        )
    except parser_api.HTTPException as exc:
        assert exc.status_code == 409
        assert "validation report attachment is disabled" in exc.detail
    else:
        raise AssertionError("direct validation evidence attachment must be rejected")


def test_rule_generation_job_preview_endpoint(monkeypatch):
    class FakeRepository:
        def preview_job(self, job_id):
            assert job_id == "job-1"
            return {
                "success": True,
                "job": {"id": job_id, "status": "pending_approval"},
                "approval_readiness": {"can_approve": True, "blockers": []},
            }

    monkeypatch.setattr(parser_api, "RuleGenerationJobRepository", FakeRepository)

    result = parser_api.preview_rule_generation_job("job-1")

    assert result["success"] is True
    assert result["approval_readiness"]["can_approve"] is True


def test_rule_generation_job_preview_endpoint_reports_404(monkeypatch):
    class FakeRepository:
        def preview_job(self, job_id):
            raise KeyError("rule generation job not found")

    monkeypatch.setattr(parser_api, "RuleGenerationJobRepository", FakeRepository)

    try:
        parser_api.preview_rule_generation_job("missing")
    except parser_api.HTTPException as exc:
        assert exc.status_code == 404
    else:
        raise AssertionError("preview should report missing jobs")


def test_rule_generation_job_approve_activates_event_rule_asset(monkeypatch):
    class FakeApprovalService:
        def approve(self, job_id, *, actor, reason):
            assert job_id == "job-1"
            assert actor == "operator"
            assert reason == "validated for production"
            return {
                "success": True,
                "job": {"id": job_id, "status": "active"},
                "asset_activation": {
                    "version_id": "event-rule-version-1",
                    "version_status": "active",
                },
            }

    monkeypatch.setattr(parser_api, "RuleAssetApprovalService", FakeApprovalService)

    result = parser_api.approve_rule_generation_job(
        "job-1",
        _admin_request(),
        parser_api.RuleGenerationJobApprovalRequest(
            actor="spoofed-client",
            reason="validated for production",
        ),
    )

    assert result["success"] is True
    assert result["job"]["status"] == "active"
    assert result["asset_activation"]["version_status"] == "active"


def test_rule_generation_job_approve_rejects_non_pending(monkeypatch):
    class FakeApprovalService:
        def approve(self, job_id, *, actor, reason):
            raise parser_api.RuleGenerationJobStateError("must be pending_approval")

    monkeypatch.setattr(parser_api, "RuleAssetApprovalService", FakeApprovalService)

    try:
        parser_api.approve_rule_generation_job(
            "job-1",
            _admin_request(),
            parser_api.RuleGenerationJobApprovalRequest(reason="validation passed"),
        )
    except parser_api.HTTPException as exc:
        assert exc.status_code == 409
    else:
        raise AssertionError("approve should reject non pending_approval jobs")


def test_rule_generation_job_revise_endpoint(monkeypatch):
    class FakeRevisionService:
        def create_revision_job(self, job_id, *, actor):
            assert job_id == "job-1"
            assert actor == "operator"
            return {
                "success": True,
                "created": True,
                "parent_job_id": job_id,
                "job": {"id": "job-2", "status": "draft"},
            }

    monkeypatch.setattr(parser_api, "AgentLoopRevisionService", FakeRevisionService)

    result = parser_api.revise_rule_generation_job(
        "job-1",
        _admin_request(),
        parser_api.RuleGenerationJobApprovalRequest(actor="operator"),
    )

    assert result["created"] is True
    assert result["job"]["id"] == "job-2"


def test_rule_generation_job_revise_requires_revision_request(monkeypatch):
    class FakeRevisionService:
        def create_revision_job(self, job_id, *, actor):
            raise ValueError("agent loop revision_request is required")

    monkeypatch.setattr(parser_api, "AgentLoopRevisionService", FakeRevisionService)

    try:
        parser_api.revise_rule_generation_job("job-1", _admin_request(), None)
    except parser_api.HTTPException as exc:
        assert exc.status_code == 409
    else:
        raise AssertionError("revise should require revision_request")


def test_rule_generation_job_execute_revision_endpoint(monkeypatch):
    class FakeRevisionExecutor:
        def execute(self, job_id, *, actor):
            assert job_id == "job-2"
            assert actor == "operator"
            return {
                "success": True,
                "job": {"id": job_id, "status": "validating"},
                "skill_name": "event-rule-validator",
            }

    monkeypatch.setattr(parser_api, "AgentLoopRevisionExecutor", FakeRevisionExecutor)

    result = parser_api.execute_rule_generation_revision_job(
        "job-2",
        _admin_request(),
        parser_api.RuleGenerationJobApprovalRequest(actor="operator"),
    )

    assert result["success"] is True
    assert result["skill_name"] == "event-rule-validator"


def test_rule_generation_job_execute_revision_rejects_non_revision(monkeypatch):
    class FakeRevisionExecutor:
        def execute(self, job_id, *, actor):
            raise parser_api.RuleGenerationJobStateError(
                "only agent loop revision jobs can be executed"
            )

    monkeypatch.setattr(parser_api, "AgentLoopRevisionExecutor", FakeRevisionExecutor)

    try:
        parser_api.execute_rule_generation_revision_job("job-1", _admin_request(), None)
    except parser_api.HTTPException as exc:
        assert exc.status_code == 409
    else:
        raise AssertionError("execute_revision should reject non revision jobs")


def test_rule_generation_job_submit_candidate_endpoint(monkeypatch):
    class FakeSubmissionService:
        def submit_candidate(self, job_id, *, actor):
            assert job_id == "job-1"
            assert actor == "operator"
            return {
                "success": True,
                "submitted_job": {"id": job_id},
                "persister_result": {"asset_count": 1},
            }

    monkeypatch.setattr(parser_api, "RuleAssetSubmissionService", FakeSubmissionService)

    result = parser_api.submit_rule_generation_job_candidate(
        "job-1",
        _admin_request(),
        parser_api.RuleGenerationJobApprovalRequest(actor="operator"),
    )

    assert result["success"] is True
    assert result["persister_result"]["asset_count"] == 1


def test_rule_generation_job_submit_candidate_rejects_invalid_state(monkeypatch):
    class FakeSubmissionService:
        def submit_candidate(self, job_id, *, actor):
            raise parser_api.RuleGenerationJobStateError("validation required")

    monkeypatch.setattr(parser_api, "RuleAssetSubmissionService", FakeSubmissionService)

    try:
        parser_api.submit_rule_generation_job_candidate("job-1", _admin_request(), None)
    except parser_api.HTTPException as exc:
        assert exc.status_code == 409
    else:
        raise AssertionError("submit should reject invalid jobs")


def test_rule_generation_job_generation_context_endpoint(monkeypatch):
    class FakeContextService:
        def load(self, *, context_id, job_id):
            assert context_id == "context-1"
            assert job_id == "job-1"
            return {
                "context_id": context_id,
                "context_digest": "digest-1",
                "dataset_ids": ["offline-dataset"],
                "context": {"records": [{"field": "value"}]},
                "privacy": {"success": True, "raw_cli_included": False},
            }

    class FakeService:
        def build_generation_context(self, job_id, *, context_overrides):
            assert job_id == "job-1"
            assert context_overrides == {"records": [{"field": "value"}]}
            return {
                "success": True,
                "ready": True,
                "workflow": "rule-asset-generation",
                "context": {"records": [{"field": "value"}]},
                "missing_context": [],
            }

    monkeypatch.setattr(parser_api, "RuleAssetTemplateActivationService", FakeService)
    monkeypatch.setattr(
        "src.core.rule_control.dataset_context.DatasetRuleGenerationContextService",
        FakeContextService,
    )

    result = parser_api.preview_rule_asset_generation_context(
        "job-1",
        _admin_request(),
        parser_api.RuleAssetGenerationContextRequest(
            context_id="context-1",
        ),
    )

    assert result["ready"] is True
    assert result["workflow"] == "rule-asset-generation"


def test_rule_generation_job_start_generation_endpoint(monkeypatch):
    class FakeContextService:
        def load(self, *, context_id, job_id):
            return {
                "context_id": context_id,
                "context_digest": "digest-1",
                "dataset_ids": ["offline-dataset"],
                "context": {"records": [{"field": "value"}]},
                "privacy": {"success": True, "raw_cli_included": False},
            }

    class FakeService:
        def start_generation_workflow(self, job_id, *, context_overrides, actor, context_id):
            assert job_id == "job-1"
            assert context_overrides == {"records": [{"field": "value"}]}
            assert actor == "operator"
            assert context_id == "context-1"
            return {"success": True, "run_id": "workflow-1"}

    monkeypatch.setattr(parser_api, "RuleAssetTemplateActivationService", FakeService)
    monkeypatch.setattr(
        "src.core.rule_control.dataset_context.DatasetRuleGenerationContextService",
        FakeContextService,
    )

    result = parser_api.start_rule_asset_generation_workflow(
        "job-1",
        _admin_request(),
        parser_api.RuleAssetGenerationContextRequest(
            context_id="context-1",
        ),
    )

    assert result["success"] is True
    assert result["run_id"] == "workflow-1"


def test_rule_generation_endpoints_reject_manual_records_without_offline_dataset():
    payload = parser_api.RuleAssetGenerationContextRequest(
        context_overrides={"records": [{"field": "value"}]},
    )

    for endpoint in (
        parser_api.preview_rule_asset_generation_context,
        parser_api.start_rule_asset_generation_workflow,
    ):
        try:
            endpoint("job-1", _admin_request(), payload)
        except parser_api.HTTPException as exc:
            assert exc.status_code == 409
            assert "offline patrol dataset" in str(exc.detail)
        else:
            raise AssertionError("manual records must not bypass the offline evidence gate")


def test_rule_generation_job_reject_endpoint(monkeypatch):
    class FakeApprovalService:
        def reject(self, job_id, *, actor, reason):
            assert job_id == "job-1"
            assert actor == "operator"
            assert reason == "bad candidate"
            return {"success": True, "job": {"id": job_id, "status": "rejected"}}

    monkeypatch.setattr(parser_api, "RuleAssetApprovalService", FakeApprovalService)

    result = parser_api.reject_rule_generation_job(
        "job-1",
        _admin_request(),
        parser_api.RuleGenerationJobApprovalRequest(
            actor="spoofed-client",
            reason="bad candidate",
        ),
    )

    assert result["job"]["status"] == "rejected"


def test_rule_generation_job_deprecate_deprecates_event_rule_asset(monkeypatch):
    class FakeApprovalService:
        def deprecate(self, job_id, *, actor, reason):
            assert actor == "operator"
            assert reason == "superseded"
            return {
                "success": True,
                "job": {"id": job_id, "status": "deprecated"},
                "asset_deprecation": {
                    "version_id": "event-rule-version-1",
                    "version_status": "deprecated",
                },
            }

    monkeypatch.setattr(parser_api, "RuleAssetApprovalService", FakeApprovalService)

    result = parser_api.deprecate_rule_generation_job(
        "job-1",
        _admin_request(),
        parser_api.RuleGenerationJobApprovalRequest(
            actor="spoofed-client",
            reason="superseded",
        ),
    )

    assert result["job"]["status"] == "deprecated"
    assert result["asset_deprecation"]["version_status"] == "deprecated"


def test_event_rule_save_creates_draft_by_default(monkeypatch):
    class FakeRepository:
        def create_rule_version(
            self,
            *,
            name,
            event_type,
            severity,
            condition,
            description,
            created_by,
            status,
        ):
            assert name == "cpu-high-custom"
            assert event_type == "CPUHigh"
            assert severity == "warning"
            assert condition == {"kind": "threshold"}
            assert description is None
            assert created_by == "operator"
            assert status == "draft"
            return "event-rule-version-1"

    monkeypatch.setattr(parser_api, "EventRuleRepository", FakeRepository)

    result = parser_api.upsert_event_rule(
        _admin_request(),
        parser_api.EventRuleUpsertRequest(
            name="cpu-high-custom",
            event_type="CPUHigh",
            severity="warning",
            condition={"kind": "threshold"},
        ),
    )

    assert result == {"success": True, "version_id": "event-rule-version-1"}


def test_event_rule_save_rejects_direct_active_create(monkeypatch):
    class FakeRepository:
        def upsert_active_rule(self, **_kwargs):
            raise AssertionError("active rule creation must not bypass approval")

        def create_rule_version(self, **_kwargs):
            raise AssertionError("active status should be rejected before persistence")

    monkeypatch.setattr(parser_api, "EventRuleRepository", FakeRepository)

    try:
        parser_api.upsert_event_rule(
            _admin_request(),
            parser_api.EventRuleUpsertRequest(
                name="cpu-high-custom",
                event_type="CPUHigh",
                severity="warning",
                condition={"kind": "threshold"},
                status="active",
            ),
        )
    except parser_api.HTTPException as exc:
        assert exc.status_code == 400
        assert "must start as draft" in exc.detail
    else:
        raise AssertionError("active rule creation should be rejected")


def test_event_rule_direct_activate_endpoint_is_disabled():
    try:
        parser_api.activate_event_rule("event-rule-version-1")
    except parser_api.HTTPException as exc:
        assert exc.status_code == 409
        assert "Rule Asset Bundle" in exc.detail
    else:
        raise AssertionError("direct EventRule activation should be disabled")


def test_event_rule_deprecate_endpoint(monkeypatch):
    class FakeRepository:
        def deprecate_rule_version(self, version_id, *, actor, reason):
            assert version_id == "event-rule-version-1"
            assert actor == "operator"
            assert reason == "superseded by bundle"
            return {
                "version_id": version_id,
                "version_status": "deprecated",
            }

    monkeypatch.setattr(parser_api, "EventRuleRepository", FakeRepository)

    result = parser_api.deprecate_event_rule(
        "event-rule-version-1",
        _admin_request(),
        parser_api.EventRuleForceActivationRequest(
            actor="spoofed-client",
            reason="superseded by bundle",
        ),
    )

    assert result["version_status"] == "deprecated"


def test_command_profile_save_creates_draft_by_default(monkeypatch):
    class FakeRepository:
        def create_profile_version(self, *, name, action, commands, created_by, status):
            assert name == "core-patrol"
            assert action == "patrol"
            assert commands == ["show version"]
            assert created_by == "operator"
            assert status == "draft"
            return "profile-version-1"

    monkeypatch.setattr(parser_api, "CommandProfileRepository", FakeRepository)

    result = parser_api.upsert_command_profile(
        _admin_request(),
        parser_api.CommandProfileUpsertRequest(
            name="core-patrol",
            commands=["show version"],
        ),
    )

    assert result == {
        "success": True,
        "profile_version_id": "profile-version-1",
        "status": "draft",
    }


def test_check_item_profile_save_persists_business_selection(monkeypatch):
    calls: list[tuple[str, object]] = []

    class FakeProfileRepository:
        def create_profile_version(
            self,
            *,
            name,
            action,
            commands,
            created_by,
            status,
        ):
            calls.append(
                (
                    "profile",
                    {
                        "name": name,
                        "action": action,
                        "commands": commands,
                        "created_by": created_by,
                        "status": status,
                    },
                )
            )
            return "profile-version-check-items"

    class FakeSelectionRepository:
        def set_draft_selection(
            self,
            profile_version_id,
            check_item_ids,
            *,
            actor,
        ):
            calls.append(
                (
                    "selection",
                    {
                        "profile_version_id": profile_version_id,
                        "check_item_ids": check_item_ids,
                        "actor": actor,
                    },
                )
            )
            return []

    monkeypatch.setattr(
        parser_api,
        "CommandProfileRepository",
        FakeProfileRepository,
    )
    monkeypatch.setattr(
        parser_api,
        "PatrolProfileCheckItemRepository",
        FakeSelectionRepository,
    )

    result = parser_api.upsert_command_profile(
        _admin_request(),
        parser_api.CommandProfileUpsertRequest(
            name="golden-patrol",
            check_item_ids=[
                "system.cpu_usage",
                "interface.oper_status",
                "system.cpu_usage",
            ],
        ),
    )

    assert result == {
        "success": True,
        "profile_version_id": "profile-version-check-items",
        "status": "draft",
        "profile_mode": "check_items",
        "check_item_ids": [
            "interface.oper_status",
            "system.cpu_usage",
        ],
    }
    assert calls[0][1]["commands"] == []
    assert calls[1][1]["check_item_ids"] == [
        "interface.oper_status",
        "system.cpu_usage",
    ]


def test_patrol_profile_rejects_mixed_check_items_and_commands(monkeypatch):
    class FakeRepository:
        def create_profile_version(self, **_kwargs):
            raise AssertionError("mixed profile must not reach persistence")

    monkeypatch.setattr(
        parser_api,
        "CommandProfileRepository",
        FakeRepository,
    )

    with pytest.raises(HTTPException) as exc_info:
        parser_api.upsert_command_profile(
            _admin_request(),
            parser_api.CommandProfileUpsertRequest(
                name="invalid-mixed-profile",
                commands=["show version"],
                check_item_ids=["device.version"],
            ),
        )

    assert exc_info.value.status_code == 400
    assert "cannot mix" in str(exc_info.value.detail)


def test_command_profile_activate_endpoint(monkeypatch):
    calls = []

    class FakeRepository:
        def validate_profile_activation(self, profile_version_id):
            calls.append(("validate", profile_version_id))

        def activate_profile_version(self, profile_version_id, *, actor, reason):
            calls.append(("activate", profile_version_id))
            assert profile_version_id == "profile-version-1"
            assert actor == "operator"
            assert reason == "approved rollout"
            return {
                "profile_version_id": profile_version_id,
                "version_status": "active",
            }

    monkeypatch.setattr(parser_api, "CommandProfileRepository", FakeRepository)

    result = parser_api.activate_command_profile(
        "profile-version-1",
        _admin_request(),
        parser_api.CommandProfileLifecycleRequest(reason="approved rollout"),
    )

    assert result["version_status"] == "active"
    assert calls == [
        ("validate", "profile-version-1"),
        ("activate", "profile-version-1"),
    ]


def test_command_profile_rollback_endpoint(monkeypatch):
    class FakeRepository:
        def rollback_profile_version(self, profile_version_id, *, actor, reason):
            assert profile_version_id == "profile-version-1"
            assert actor == "operator"
            assert reason == "restore prior coverage"
            return {
                "profile_version_id": profile_version_id,
                "version_status": "active",
            }

    monkeypatch.setattr(parser_api, "CommandProfileRepository", FakeRepository)

    result = parser_api.rollback_command_profile(
        "profile-version-1",
        _admin_request(),
        parser_api.CommandProfileLifecycleRequest(reason="restore prior coverage"),
    )

    assert result["version_status"] == "active"


def test_command_profile_delete_endpoint(monkeypatch):
    class FakeRepository:
        def delete_profile_version(self, profile_version_id, *, actor, reason):
            assert profile_version_id == "profile-version-1"
            assert actor == "operator"
            assert reason == "remove invalid draft"
            return {
                "success": True,
                "profile_version_id": profile_version_id,
                "profile_deleted": True,
            }

    monkeypatch.setattr(parser_api, "CommandProfileRepository", FakeRepository)

    result = parser_api.delete_command_profile(
        "profile-version-1",
        _admin_request(),
        parser_api.CommandProfileLifecycleRequest(reason="remove invalid draft"),
    )

    assert result["success"] is True
    assert result["profile_deleted"] is True


def test_command_profile_binding_disable_endpoint(monkeypatch):
    class FakeRepository:
        def set_binding_status(self, binding_id, status, *, actor, reason):
            assert binding_id == "binding-1"
            assert status == "disabled"
            assert actor == "operator"
            assert reason == "remove obsolete device scope"
            return {"id": binding_id, "status": status}

    monkeypatch.setattr(parser_api, "CommandProfileRepository", FakeRepository)

    result = parser_api.disable_command_profile_binding(
        "binding-1",
        _admin_request(),
        parser_api.CommandProfileLifecycleRequest(reason="remove obsolete device scope"),
    )

    assert result == {"id": "binding-1", "status": "disabled"}


def test_command_profile_binding_endpoint(monkeypatch):
    class FakeRepository:
        def upsert_binding(self, *, profile_version_id, selector, priority, status):
            assert profile_version_id == "profile-version-1"
            assert selector == {"vendor": "Cisco", "role": "core"}
            assert priority == 10
            assert status == "disabled"
            return "binding-1"

    monkeypatch.setattr(parser_api, "CommandProfileRepository", FakeRepository)

    result = parser_api.upsert_command_profile_binding(
        _admin_request(),
        parser_api.CommandProfileBindingUpsertRequest(
            profile_version_id="profile-version-1",
            selector={"vendor": "Cisco", "role": "core"},
            priority=10,
        ),
    )

    assert result == {"success": True, "binding_id": "binding-1"}


def test_command_profile_binding_rejects_direct_active_create(monkeypatch):
    class FakeRepository:
        def upsert_binding(self, **_kwargs):
            raise AssertionError("active binding must not reach persistence")

    monkeypatch.setattr(parser_api, "CommandProfileRepository", FakeRepository)

    try:
        parser_api.upsert_command_profile_binding(
            _admin_request(),
            parser_api.CommandProfileBindingUpsertRequest(
                profile_version_id="profile-version-1",
                selector={"vendor": "Cisco"},
                status="active",
            ),
        )
    except parser_api.HTTPException as exc:
        assert exc.status_code == 400
        assert "must be created disabled" in exc.detail
    else:
        raise AssertionError("direct active binding creation should be rejected")


def test_dispatch_control_uses_authenticated_actor_and_reason(monkeypatch):
    class FakeRepository:
        def set_auto_dispatch_enabled(self, enabled, *, actor, reason):
            assert enabled is True
            assert actor == "operator"
            assert reason == "enable controlled rollout"
            return enabled

    monkeypatch.setattr(parser_api, "ParserJobRepository", FakeRepository)

    result = parser_api.set_dispatch_control(
        _admin_request(),
        parser_api.ParserDispatchControlRequest(
            enabled=True,
            reason="enable controlled rollout",
        ),
    )

    assert result == {"success": True, "auto_dispatch_enabled": True}


def test_command_profile_impact_preview_with_inline_commands(monkeypatch):
    class FakeRepository:
        def get_devices_by_filter(self, **kwargs):
            assert kwargs["status"] == "active"
            return [
                {
                    "device_id": "device-1",
                    "device_name": "SW1",
                    "ip": "10.0.0.1",
                    "vendor": "Cisco",
                    "model": "WS-C2960XR-24TS-I",
                    "role": "core",
                    "groups": ["xian"],
                },
                {
                    "device_id": "device-2",
                    "device_name": "SW2",
                    "ip": "10.0.0.2",
                    "vendor": "Cisco",
                    "model": "WS-C2960XR-24TS-I",
                    "role": "access",
                    "groups": ["xian"],
                },
            ]

    monkeypatch.setattr(parser_api, "DeviceRepository", FakeRepository)
    monkeypatch.setattr(
        parser_api.PatrolTool,
        "_production_coverage",
        staticmethod(
            lambda device, commands: [
                {
                    "canonical_command": "show version",
                    "policy_status": "eligible",
                    "coverage_status": "ready",
                },
                {
                    "canonical_command": "show inventory",
                    "policy_status": "eligible",
                    "coverage_status": "template_missing",
                },
            ]
        ),
    )

    result = parser_api.preview_command_profile_impact(
        parser_api.CommandProfileImpactPreviewRequest(
            profile_name="core-patrol",
            commands=["show version", "show inventory"],
            selector={"role": "core"},
        )
    )

    assert result["success"] is True
    assert result["matched_device_count"] == 1
    assert result["blocked_device_count"] == 1
    assert result["eligible_command_count"] == 2
    assert result["ready_command_count"] == 1
    assert result["blocked_command_count"] == 1
    assert result["coverage_rate"] == 0.5
    assert result["rows"][0]["device_id"] == "device-1"
    assert result["rows"][0]["violations"][0]["canonical_command"] == "show inventory"


def test_command_profile_impact_preview_can_load_profile_version(monkeypatch):
    saved = []

    class FakeProfileRepository:
        def get_profile_version(self, profile_version_id):
            assert profile_version_id == "profile-version-1"
            return {
                "profile_name": "site-patrol",
                "profile_version_id": profile_version_id,
                "items": [
                    {"action": "patrol", "command": "show version"},
                    {"action": "backup", "command": "show running-config"},
                ],
            }

        def record_profile_coverage(self, profile_version_id, coverage):
            saved.append((profile_version_id, coverage))
            return {"profile_version_id": profile_version_id, "coverage": coverage}

    class FakeDeviceRepository:
        def get_devices_by_filter(self, **kwargs):
            return [{"device_id": "device-1", "device_name": "SW1", "vendor": "Cisco"}]

    monkeypatch.setattr(parser_api, "CommandProfileRepository", FakeProfileRepository)
    monkeypatch.setattr(parser_api, "DeviceRepository", FakeDeviceRepository)
    monkeypatch.setattr(
        parser_api.PatrolTool,
        "_production_coverage",
        staticmethod(
            lambda device, commands: [
                {
                    "canonical_command": commands[0],
                    "policy_status": "eligible",
                    "coverage_status": "ready",
                }
            ]
        ),
    )

    result = parser_api.preview_command_profile_impact(
        parser_api.CommandProfileImpactPreviewRequest(
            profile_version_id="profile-version-1",
            save_evidence=True,
        )
    )

    assert result["profile_name"] == "site-patrol"
    assert result["profile_version_id"] == "profile-version-1"
    assert result["command_count"] == 1
    assert result["ready_device_count"] == 1
    assert result["rows"][0]["plan"]["profile_version_id"] == "profile-version-1"
    assert result["evidence_saved"] is True
    assert saved[0][0] == "profile-version-1"
    assert saved[0][1]["blocked_device_count"] == 0


def test_runtime_rule_asset_resolve_endpoint(monkeypatch):
    calls = []

    class FakeBundle:
        def to_dict(self):
            return {
                "ready": False,
                "observation_contracts": {"cli:template-1": {"entity_type": "interface"}},
                "change_policies": {},
                "event_rules": [{"rule_id": "InterfaceDown"}],
                "missing_observation_contracts": [],
                "missing_change_policies": ["interface.oper_status"],
                "counts": {
                    "observation_contracts": 1,
                    "change_policies": 0,
                    "event_rules": 1,
                },
            }

    class FakeResolver:
        def load_bundle(
            self,
            *,
            observation_schemas,
            observation_types,
            include_event_rules,
        ):
            calls.append(
                {
                    "observation_schemas": observation_schemas,
                    "observation_types": observation_types,
                    "include_event_rules": include_event_rules,
                }
            )
            return FakeBundle()

    monkeypatch.setattr(parser_api, "RuntimeRuleAssetResolver", FakeResolver)

    result = parser_api.resolve_runtime_rule_assets(
        parser_api.RuntimeRuleAssetResolveRequest(
            observation_schemas=[("cli", "template-1")],
            observation_types=["interface.oper_status"],
        )
    )

    assert result["ready"] is False
    assert result["counts"]["observation_contracts"] == 1
    assert result["missing_change_policies"] == ["interface.oper_status"]
    assert calls == [
        {
            "observation_schemas": [("cli", "template-1")],
            "observation_types": ["interface.oper_status"],
            "include_event_rules": True,
        }
    ]


def test_runtime_bundle_assembly_endpoints_preserve_preview_and_actor(monkeypatch):
    calls = []

    class FakeRuntimeBundleService:
        def preview_assembly(self, **payload):
            calls.append(("preview", payload))
            return {
                "preview_digest": "preview-1",
                "operationally_ready": True,
                "blockers": [],
            }

        def assemble(self, **payload):
            calls.append(("assemble", payload))
            return {"version_id": "bundle-v1", "version_status": "draft"}

        def activate(self, version_id, **payload):
            calls.append(("activate", {"version_id": version_id, **payload}))
            return {"version_id": version_id, "version_status": "active"}

    monkeypatch.setattr(
        parser_api,
        "CheckItemRuntimeBundleService",
        FakeRuntimeBundleService,
    )
    selection = {
        "check_item_version_id": "check-v1",
        "implementation_version_id": "implementation-v1",
        "parser_template_ids": ["parser-v1"],
        "event_rule_version_ids": ["rule-v1"],
    }

    preview = parser_api.preview_check_item_runtime_bundle_assembly(
        parser_api.RuntimeBundleAssemblyRequest(**selection),
        _admin_request("engineer").state.current_user,
    )
    draft = parser_api.assemble_check_item_runtime_bundle(
        parser_api.RuntimeBundleAssembleRequest(
            **selection,
            preview_digest="preview-1",
        ),
        _admin_request("engineer"),
        "request-key-assemble",
    )
    active = parser_api.activate_check_item_runtime_bundle(
        "bundle-v1",
        parser_api.RuntimeBundleLifecycleRequest(reason="verified runtime scope"),
        _admin_request("approver"),
        "request-key-activate",
    )

    assert preview["preview_digest"] == "preview-1"
    assert draft["version_status"] == "draft"
    assert active["version_status"] == "active"
    assert calls[1][1]["actor"] == "engineer"
    assert calls[1][1]["preview_digest"] == "preview-1"
    assert calls[2][1]["actor"] == "approver"
    assert calls[2][1]["reason"] == "verified runtime scope"


def test_runtime_bundle_assemble_endpoint_returns_structured_conflict(monkeypatch):
    class FakeRuntimeBundleService:
        def assemble(self, **_payload):
            raise parser_api.RuntimeBundleValidationError(
                ["implementation scope is broader than Parser evidence"]
            )

    monkeypatch.setattr(
        parser_api,
        "CheckItemRuntimeBundleService",
        FakeRuntimeBundleService,
    )

    with pytest.raises(HTTPException) as exc_info:
        parser_api.assemble_check_item_runtime_bundle(
            parser_api.RuntimeBundleAssembleRequest(
                check_item_version_id="check-v1",
                implementation_version_id="implementation-v1",
                preview_digest="preview-1",
            ),
            _admin_request("engineer"),
            "request-key-assemble",
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["message"] == "运行包装配被阻断"
    assert "broader" in exc_info.value.detail["blockers"][0]
