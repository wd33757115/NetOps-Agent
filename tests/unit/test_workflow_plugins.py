# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""Workflow 表达式与注册表单元测试。"""

import asyncio
from pathlib import Path

import pytest
import yaml

from src.core.workflows import patrol_batch_service
from src.core.workflows.expression import build_step_env, resolve_inputs, resolve_value
from src.core.workflows.registry import WORKFLOWS_ROOT, get_template, load_workflows


def test_load_firewall_workflow_plugin():
    load_workflows(force=True)
    tpl = get_template("itsm-firewall-change")
    assert tpl is not None
    assert len(tpl.steps) == 3
    assert tpl.steps[0].skill_name == "app:firewall-policy/generate_policy"
    assert tpl.steps[1].inputs["policy_run_id"] == "${steps.policy_generation.result.policy_run_id}"
    assert tpl.steps[2].when == "${context.callback_url}"


def test_load_patrol_change_event_workflow():
    load_workflows(force=True)
    tpl = get_template("patrol-change-event")

    assert tpl is not None
    assert [step.skill_name for step in tpl.steps] == [
        "device-patrol",
        "state-normalizer",
        "change-detector",
        "event-builder",
    ]
    assert tpl.steps[2].inputs["current_run_id"] == "${steps.patrol.result.snapshot_run_id}"
    assert tpl.steps[1].inputs["run_id"] == "${steps.patrol.result.snapshot_run_id}"
    assert "update_current_state" not in tpl.steps[1].inputs
    assert tpl.steps[2].inputs["use_state_observations"] is True
    assert tpl.steps[3].inputs["run_id"] == "${steps.patrol.result.snapshot_run_id}"


def test_load_single_period_offline_patrol_workflow():
    load_workflows(force=True)
    tpl = get_template("patrol-offline-current")

    assert tpl is not None
    assert [step.skill_name for step in tpl.steps] == [
        "offline-patrol-quality-gate",
        "patrol-raw-ingestor",
        "state-normalizer",
        "change-detector",
        "event-builder",
        "report-generator",
    ]


def test_load_patrol_history_change_event_uses_normalized_state():
    load_workflows(force=True)
    tpl = get_template("patrol-history-change-event")

    assert tpl is not None
    by_name = {step.name: step for step in tpl.steps}
    assert by_name["baseline_normalization"].skill_name == "state-normalizer"
    assert by_name["current_normalization"].skill_name == "state-normalizer"
    assert by_name["change_detection"].inputs["previous_run_id"] == (
        "${steps.baseline_ingest.result.run_id}"
    )
    assert by_name["change_detection"].inputs["current_run_id"] == (
        "${steps.current_ingest.result.run_id}"
    )
    assert by_name["change_detection"].inputs["use_state_observations"] is True


def test_load_rule_asset_generation_workflow():
    load_workflows(force=True)
    tpl = get_template("rule-asset-generation")

    assert tpl is not None
    assert [step.skill_name for step in tpl.steps] == [
        "observation-contract-generator",
        "rule-target-planner",
        "change-policy-generator",
        "event-rule-generator",
        "event-rule-validator",
        "event-rule-replay",
        "rule-asset-persister",
    ]
    assert tpl.test_context["command"] == "display cpu"
    assert tpl.test_required_context == [
        "command",
        "records",
    ]
    assert tpl.steps[4].inputs["candidate_rules"] == "${steps.event_rule.result.rules}"
    assert tpl.steps[0].inputs["proposal"] == "${context.proposal}"
    assert tpl.steps[1].inputs["contract"] == "${steps.observation_contract.result.proposal}"
    assert tpl.steps[4].inputs["mode"] == "candidates"
    assert tpl.steps[5].inputs["candidate_rules"] == "${steps.event_rule.result.rules}"
    assert tpl.steps[5].inputs["include_active_collisions"] is True
    assert tpl.steps[6].inputs["contract_result"] == "${steps.observation_contract.result}"
    assert tpl.steps[6].inputs["event_rule_validation"] == "${steps.event_rule_validation.result}"
    assert tpl.steps[6].inputs["event_rule_replay"] == "${steps.event_rule_replay.result}"
    assert tpl.steps[3].inputs["rule_asset_requirements"] == (
        "${context.rule_asset_requirements}"
    )


def test_rule_asset_workflow_skips_assets_not_required_by_profile():
    from src.core.workflows.registry import resolve_active_steps

    load_workflows(force=True)
    tpl = get_template("rule-asset-generation")
    assert tpl is not None
    context = {
        **tpl.test_context,
        "rule_asset_requirements": {
            "requires_change_policy": False,
            "requires_event_rule_assets": False,
            "requires_historical_replay": False,
        },
    }

    active = resolve_active_steps(tpl, context)

    assert [step.name for step in active] == [
        "observation_contract",
        "rule_target_planning",
        "persist_assets",
    ]


def test_rule_asset_workflow_runs_full_health_asset_chain():
    from src.core.workflows.registry import resolve_active_steps

    load_workflows(force=True)
    tpl = get_template("rule-asset-generation")
    assert tpl is not None

    active = resolve_active_steps(tpl, tpl.test_context)

    assert [step.name for step in active] == [
        "observation_contract",
        "rule_target_planning",
        "change_policy",
        "event_rule",
        "event_rule_validation",
        "event_rule_replay",
        "persist_assets",
    ]


def test_load_rule_asset_submit_workflow():
    load_workflows(force=True)
    tpl = get_template("rule-asset-submit")

    assert tpl is not None
    assert [step.skill_name for step in tpl.steps] == ["rule-asset-submitter"]
    assert tpl.test_context["actor"] == "workflow"
    assert tpl.test_required_context == ["job_id"]
    assert tpl.steps[0].inputs["job_id"] == "${context.job_id}"


def test_load_rule_asset_approve_workflow():
    load_workflows(force=True)
    tpl = get_template("rule-asset-approve")

    assert tpl is not None
    assert [step.skill_name for step in tpl.steps] == ["rule-asset-approver"]
    assert tpl.test_context["actor"] == "workflow"
    assert tpl.test_required_context == ["job_id"]
    assert tpl.steps[0].inputs["job_id"] == "${context.job_id}"
    assert tpl.steps[0].inputs["actor"] == "${context.actor}"


def test_rule_asset_submit_and_approve_chat_intents_require_job_id():
    from src.core.plugins.chat_intent import get_chat_intent_registry

    registry = get_chat_intent_registry()
    registry.load(force=True)
    submit = registry.get_intent("rule-asset-submit")
    approve = registry.get_intent("rule-asset-approve")

    assert submit is not None
    assert submit.required_context == ["job_id"]
    assert submit.context_from_query == {"job_id": "job_id"}
    assert approve is not None
    assert approve.required_context == ["job_id"]
    assert approve.context_from_query == {"job_id": "job_id"}


def test_chat_intent_workflow_names_match_local_workflow_files():
    for chat_path in sorted(Path(WORKFLOWS_ROOT).rglob("CHAT.intent.yaml")):
        workflow_path = chat_path.with_name("WORKFLOW.yaml")
        assert workflow_path.exists(), f"{chat_path} missing sibling WORKFLOW.yaml"

        chat = yaml.safe_load(chat_path.read_text(encoding="utf-8")) or {}
        workflow = yaml.safe_load(workflow_path.read_text(encoding="utf-8")) or {}

        assert chat.get("workflow") == workflow.get("name"), (
            f"{chat_path} workflow {chat.get('workflow')!r} does not match "
            f"{workflow_path} name {workflow.get('name')!r}"
        )


def test_rule_asset_workflows_are_documented_and_chat_triggerable():
    for workflow_name in [
        "rule-asset-generation",
        "rule-asset-submit",
        "rule-asset-approve",
    ]:
        workflow_dir = Path(WORKFLOWS_ROOT) / "network" / workflow_name
        assert (workflow_dir / "WORKFLOW.yaml").exists()
        assert (workflow_dir / "CHAT.intent.yaml").exists()
        assert (workflow_dir / "README.md").exists()


def test_workflow_owns_test_context_contract():
    from src.core.workflows.registry import missing_test_context

    load_workflows(force=True)
    tpl = get_template("parser-validation-ingest")

    assert tpl is not None
    assert tpl.test_context["dataset_id"] == "dev-test-previous-quarter"
    assert tpl.test_required_context == ["file_path"]
    assert missing_test_context(tpl, tpl.test_context) == ["file_path"]
    assert (
        missing_test_context(
            tpl,
            {**tpl.test_context, "file_path": "C:/captures"},
        )
        == []
    )


def test_patrol_chat_intent_has_no_ticket_requirement():
    from src.core.plugins.chat_intent import get_chat_intent_registry

    registry = get_chat_intent_registry()
    registry.load(force=True)
    intent = registry.get_intent("patrol-change-event")

    assert intent is not None
    assert intent.required_context == []


def test_resolve_active_steps_without_callback():
    from src.core.workflows.registry import format_steps_flow, resolve_active_steps

    load_workflows(force=True)
    tpl = get_template("itsm-firewall-change")
    ctx = {"ticket_id": "REQ2025", "policy_file_url": "/tmp/p.xlsx"}
    active = resolve_active_steps(tpl, ctx)
    assert len(active) == 2
    assert active[-1].name == "change_ticket"
    assert format_steps_flow(active) == "生成防火墙策略 → 编写变更工单"


def test_resolve_active_steps_with_callback():
    from src.core.workflows.registry import resolve_active_steps

    load_workflows(force=True)
    tpl = get_template("itsm-firewall-change")
    ctx = {"ticket_id": "REQ2025", "callback_url": "http://cb"}
    active = resolve_active_steps(tpl, ctx)
    assert len(active) == 3
    assert active[-1].name == "itsm_callback"


def test_resolve_context_expression():
    env = {"context": {"ticket_id": "T001"}, "run": {"id": "run-1"}}
    assert resolve_value("${context.ticket_id}", env) == "T001"
    assert resolve_value("${run.id}", env) == "run-1"


def test_resolve_brace_style_notification():
    env = {"context": {"ticket_id": "REQ2025"}, "run": {"id": "run-1"}}
    assert resolve_value("变更工单已完成 ({context.ticket_id})", env) == "变更工单已完成 (REQ2025)"


def test_resolve_step_artifacts_expression():
    env = build_step_env(
        context={"ticket_id": "T1"},
        run_id="run-1",
        ticket_id="T1",
        step_records=[],
        current_step_index=0,
    )
    env["steps"] = {
        "policy_generation": {
            "result": {"manifest": {"devices": []}},
            "artifacts": {"config_zip": {"file_key": "fk1", "download_url": "http://x"}},
        }
    }
    params = resolve_inputs(
        {
            "manifest": "${steps.policy_generation.result.manifest}",
            "config_file_key": "${steps.policy_generation.artifacts.config_zip.file_key}",
        },
        env,
    )
    assert params["manifest"] == {"devices": []}
    assert params["config_file_key"] == "fk1"


def test_resolve_step_result_list_index_expression():
    env = build_step_env(
        context={},
        run_id="run-1",
        ticket_id=None,
        step_records=[],
        current_step_index=0,
    )
    env["steps"] = {
        "event_rule": {
            "result": {"rules": [{"name": "cpu-high"}, {"name": "cpu-recovered"}]},
            "artifacts": {},
        }
    }

    params = resolve_inputs(
        {"candidate_rule": "${steps.event_rule.result.rules.0}"},
        env,
    )

    assert params["candidate_rule"] == {"name": "cpu-high"}


def test_list_workflow_runs_filters_device_context(monkeypatch):
    from types import SimpleNamespace

    from src.core.workflows import repository

    runs = [
        SimpleNamespace(
            id="run-1",
            template_name="patrol-change-event",
            ticket_id=None,
            source="device-management",
            status="failed",
            context={"device_id": "device-1"},
            created_at=3,
        ),
        SimpleNamespace(
            id="run-2",
            template_name="patrol-change-event",
            ticket_id=None,
            source="device-management",
            status="completed",
            context={"device_id": "device-2"},
            created_at=2,
        ),
        SimpleNamespace(
            id="run-3",
            template_name="other",
            ticket_id=None,
            source="chat",
            status="failed",
            context={"device_id": "device-1"},
            created_at=1,
        ),
        SimpleNamespace(
            id="run-4",
            template_name="patrol-change-event",
            ticket_id=None,
            source="device-management",
            status="failed",
            context={"device_id": "device-1", "patrol_batch_id": "batch-2"},
            created_at=0,
        ),
    ]

    class FakeQuery:
        def __init__(self, items):
            self.items = list(items)

        def order_by(self, *_args):
            return self

        def filter(self, predicate):
            self.items = [item for item in self.items if predicate(item)]
            return self

        def limit(self, _limit):
            return self

        def all(self):
            return self.items

    class FakeColumn:
        def __init__(self, name):
            self.name = name

        def desc(self):
            return self

        def __eq__(self, other):
            return lambda item: getattr(item, self.name) == other

    class FakeJsonValue:
        def __init__(self, key):
            self.key = key

        def as_string(self):
            return self

        def __eq__(self, other):
            return lambda item: str((item.context or {}).get(self.key) or "") == other

    class FakeJsonColumn:
        def __getitem__(self, key):
            return FakeJsonValue(key)

    class FakeWorkflowRun:
        created_at = FakeColumn("created_at")
        template_name = FakeColumn("template_name")
        ticket_id = FakeColumn("ticket_id")
        source = FakeColumn("source")
        status = FakeColumn("status")
        context = FakeJsonColumn()

    class FakeSession:
        def query(self, _model):
            return FakeQuery(runs)

        def expunge(self, _item):
            return None

    class FakeSessionContext:
        def __enter__(self):
            return FakeSession()

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(repository, "WorkflowRun", FakeWorkflowRun)
    monkeypatch.setattr(repository, "get_db_session", lambda: FakeSessionContext())

    result = repository.list_workflow_runs(
        template_name="patrol-change-event",
        source="device-management",
        status="failed",
        device_id="device-1",
        patrol_batch_id=None,
    )

    assert [item.id for item in result] == ["run-1", "run-4"]

    batch_result = repository.list_workflow_runs(
        template_name="patrol-change-event",
        source="device-management",
        status="failed",
        device_id="device-1",
        patrol_batch_id="batch-2",
    )

    assert [item.id for item in batch_result] == ["run-4"]


def test_retry_workflow_run_reuses_context(monkeypatch):
    from types import SimpleNamespace

    from src.gateway import workflow_api

    started = []
    original = SimpleNamespace(
        id="run-1",
        template_name="patrol-change-event",
        ticket_id=None,
        source="device-management",
        user_id="user-1",
        thread_id="thread-1",
        status="failed",
        context={
            "device_id": "device-1",
            "langfuse_trace_id": "old-trace",
            "langfuse_workflow_root_span_id": "old-span",
        },
    )
    retried = SimpleNamespace(
        id="run-2",
        template_name="patrol-change-event",
        ticket_id=None,
        source="device-management",
        user_id="user-1",
        thread_id="thread-1",
        status="running",
        context={"device_id": "device-1", "retry_of_run_id": "run-1"},
        current_step_index=0,
        error_message=None,
        created_at=None,
        completed_at=None,
    )

    def fake_get_run(run_id):
        return original if run_id == "run-1" else retried

    class FakeEngine:
        @staticmethod
        def start(template_name, context, **kwargs):
            started.append((template_name, context, kwargs))
            return "run-2"

    monkeypatch.setattr(workflow_api, "get_workflow_run", fake_get_run)
    monkeypatch.setattr(workflow_api, "list_workflow_steps", lambda _run_id: [])
    monkeypatch.setattr(workflow_api, "get_workflow_timeline", lambda _run_id: [])
    monkeypatch.setattr(workflow_api, "list_child_runs", lambda _run_id: [])
    monkeypatch.setattr(workflow_api, "WorkflowEngine", FakeEngine)

    result = asyncio.run(
        workflow_api.retry_workflow_run(
            "run-1",
            workflow_api.WorkflowRetryRequest(),
            user=None,
        )
    )

    assert result["success"] is True
    assert result["run_id"] == "run-2"
    assert result["retry_of_run_id"] == "run-1"
    assert started[0][0] == "patrol-change-event"
    assert started[0][1]["device_id"] == "device-1"
    assert started[0][1]["retry_of_run_id"] == "run-1"
    assert "langfuse_trace_id" not in started[0][1]
    assert started[0][2]["source"] == "device-management"
    assert started[0][2]["parent_run_id"] == "run-1"


def test_retry_workflow_run_rejects_completed(monkeypatch):
    from types import SimpleNamespace

    import pytest
    from fastapi import HTTPException

    from src.gateway import workflow_api

    monkeypatch.setattr(
        workflow_api,
        "get_workflow_run",
        lambda _run_id: SimpleNamespace(id="run-1", status="completed"),
    )

    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            workflow_api.retry_workflow_run(
                "run-1",
                workflow_api.WorkflowRetryRequest(),
                user=None,
            )
        )

    assert exc.value.status_code == 400


def test_list_patrol_batches_groups_runs(monkeypatch):
    from types import SimpleNamespace

    from src.core.patrol.batch_manifest import PatrolBatchManifestRepository
    from src.core.patrol.legacy_batch_history import LegacyPatrolBatchHistoryRepository
    from src.gateway import workflow_api

    runs = [
        SimpleNamespace(
            id="run-1",
            template_name="patrol-change-event",
            ticket_id="PATROL-1",
            source="device-management",
            status="completed",
            current_step_index=3,
            error_message=None,
            created_at="2026-06-21T10:00:00",
            completed_at="2026-06-21T10:10:00",
            context={"patrol_batch_id": "batch-1", "device_id": "device-1", "device_name": "SW1"},
        ),
        SimpleNamespace(
            id="run-2",
            template_name="patrol-change-event",
            ticket_id="PATROL-1",
            source="device-management",
            status="failed",
            current_step_index=1,
            error_message="boom",
            created_at="2026-06-21T10:01:00",
            completed_at="2026-06-21T10:11:00",
            context={"patrol_batch_id": "batch-1", "device_id": "device-2", "device_name": "SW2"},
        ),
        SimpleNamespace(
            id="run-3",
            template_name="patrol-change-event",
            ticket_id=None,
            source="device-management",
            status="running",
            current_step_index=1,
            error_message=None,
            created_at="2026-06-21T11:00:00",
            completed_at=None,
            context={"patrol_batch_id": "batch-2", "device_id": "device-3", "device_name": "SW3"},
        ),
        SimpleNamespace(
            id="run-4",
            template_name="patrol-change-event",
            ticket_id=None,
            source="device-management",
            status="completed",
            current_step_index=3,
            error_message=None,
            created_at="2026-06-21T12:00:00",
            completed_at="2026-06-21T12:05:00",
            context={
                "patrol_batch_id": "batch-archived",
                "device_id": "device-4",
                "device_name": "SW4",
                "patrol_batch_archived": True,
                "patrol_batch_archived_at": "2026-06-21T13:00:00Z",
                "patrol_batch_archive_reason": "old batch",
            },
        ),
    ]
    monkeypatch.setattr(
        PatrolBatchManifestRepository,
        "list_batches_page",
        lambda self, **kwargs: {
            "items": [],
            "total": 0,
            "limit": kwargs.get("limit", 25),
            "offset": kwargs.get("offset", 0),
        },
    )

    def legacy_page(_self, **kwargs):
        grouped = {
            batch_id: [run for run in runs if run.context.get("patrol_batch_id") == batch_id]
            for batch_id in {run.context["patrol_batch_id"] for run in runs}
        }
        items = [
            LegacyPatrolBatchHistoryRepository._serialize_batch(batch_id, batch_runs)
            for batch_id, batch_runs in grouped.items()
            if kwargs.get("include_archived")
            or not all(run.context.get("patrol_batch_archived") for run in batch_runs)
        ]
        items.sort(key=lambda item: item["created_at"], reverse=True)
        return {"items": items, "total": len(items)}

    monkeypatch.setattr(
        LegacyPatrolBatchHistoryRepository,
        "list_batches_window",
        legacy_page,
    )

    result = asyncio.run(workflow_api.list_patrol_batches())

    assert result["items"][0]["patrol_batch_id"] == "batch-2"
    assert result["items"][1]["patrol_batch_id"] == "batch-1"
    assert result["items"][1]["total"] == 2
    assert result["items"][1]["completed_count"] == 1
    assert result["items"][1]["failed_count"] == 1
    assert result["items"][1]["sample_devices"] == ["SW1", "SW2"]
    assert result["total"] == 2
    assert result["page"] == 1
    assert result["page_size"] == 25
    assert all(item["patrol_batch_id"] != "batch-archived" for item in result["items"])

    with_archived = asyncio.run(workflow_api.list_patrol_batches(include_archived=True))
    archived = next(
        item for item in with_archived["items"] if item["patrol_batch_id"] == "batch-archived"
    )
    assert archived["archived"] is True
    assert archived["archived_count"] == 1
    assert archived["archive_reason"] == "old batch"


def test_list_patrol_batches_globally_merges_manifest_and_legacy(monkeypatch):
    from src.core.patrol.batch_manifest import PatrolBatchManifestRepository
    from src.core.patrol.legacy_batch_history import LegacyPatrolBatchHistoryRepository
    from src.gateway import workflow_api

    monkeypatch.setattr(
        PatrolBatchManifestRepository,
        "list_batches_page",
        lambda self, **_kwargs: {
            "items": [
                {"patrol_batch_id": "manifest-new", "created_at": "2026-07-03T00:00:00Z"},
                {"patrol_batch_id": "manifest-old", "created_at": "2026-07-01T00:00:00Z"},
            ],
            "total": 2,
        },
    )
    monkeypatch.setattr(
        LegacyPatrolBatchHistoryRepository,
        "list_batches_window",
        lambda self, **_kwargs: {
            "items": [
                {"patrol_batch_id": "legacy-middle", "created_at": "2026-07-02T00:00:00Z"},
            ],
            "total": 1,
        },
    )

    result = asyncio.run(workflow_api.list_patrol_batches(limit=2))

    assert [item["patrol_batch_id"] for item in result["items"]] == [
        "manifest-new",
        "legacy-middle",
    ]
    assert result["total"] == 3

    second_page = asyncio.run(workflow_api.list_patrol_batches(limit=2, offset=2))
    assert [item["patrol_batch_id"] for item in second_page["items"]] == ["manifest-old"]


def test_archive_patrol_batch_writes_governance_context(monkeypatch):
    from types import SimpleNamespace

    from src.gateway import workflow_api

    runs = [
        SimpleNamespace(
            id="run-1",
            template_name="patrol-change-event",
            ticket_id=None,
            source="device-management",
            status="completed",
            context={"patrol_batch_id": "batch-1", "device_id": "device-1"},
        ),
        SimpleNamespace(
            id="run-2",
            template_name="patrol-change-event",
            ticket_id=None,
            source="device-management",
            status="failed",
            context={"patrol_batch_id": "batch-1", "device_id": "device-2"},
        ),
    ]
    updated = {}

    monkeypatch.setattr(workflow_api, "list_workflow_runs", lambda **_kwargs: runs)
    monkeypatch.setattr(
        workflow_api,
        "update_run_context",
        lambda run_id, context: updated.setdefault(run_id, context),
    )

    result = asyncio.run(
        workflow_api.archive_patrol_batch(
            "batch-1",
            workflow_api.PatrolBatchGovernanceRequest(
                reason="operator cleanup",
                keep_until="2026-12-31T00:00:00Z",
            ),
            user=None,
        )
    )

    assert result["success"] is True
    assert result["archived"] is True
    assert result["affected_runs"] == 2
    assert set(updated) == {"run-1", "run-2"}
    assert updated["run-1"]["patrol_batch_archived"] is True
    assert updated["run-1"]["patrol_batch_archive_reason"] == "operator cleanup"
    assert updated["run-1"]["patrol_batch_keep_until"] == "2026-12-31T00:00:00Z"


def test_cancel_patrol_batch_stops_active_runs_and_records_reason(monkeypatch):
    from types import SimpleNamespace

    from src.gateway import workflow_api

    runs = [
        SimpleNamespace(
            id="run-active",
            template_name="patrol-change-event",
            status="running",
            context={"patrol_batch_id": "batch-cancel", "device_id": "device-1"},
        )
    ]
    cancelled = []
    reports = []

    class FakeRepository:
        def workflow_runs(self, _batch_id):
            return runs

        def cancel_dispatch(self, batch_id, *, actor, reason):
            assert batch_id == "batch-cancel"
            assert actor == "system"
            assert reason == "operator requested stop"
            return {
                "found": True,
                "changed": True,
                "cancellable": True,
                "idempotent": False,
                "cancelled_at": "2026-08-02T00:00:00+00:00",
                "workflow_run_ids": ["run-active"],
                "celery_task_ids": [],
                "device_ids": ["device-1"],
                "runtime_manifest_id": None,
            }

    class FakeEngine:
        @staticmethod
        def cancel_run(run_id, *, actor, reason):
            cancelled.append((run_id, actor, reason))
            return {"changed": True, "run_id": run_id, "status": "cancelled"}

        @staticmethod
        def request_patrol_batch_report_if_terminal(batch_id):
            reports.append(batch_id)
            return False

    monkeypatch.setattr(workflow_api, "PatrolBatchManifestRepository", FakeRepository)
    monkeypatch.setattr(workflow_api, "WorkflowEngine", FakeEngine)

    result = asyncio.run(
        workflow_api.cancel_patrol_batch(
            "batch-cancel",
            workflow_api.PatrolBatchCancelRequest(reason="operator requested stop"),
            user=None,
        )
    )

    assert result["success"] is True
    assert result["status"] == "cancelled"
    assert result["cancelled_run_count"] == 1
    assert cancelled == [
        ("run-active", "system", "operator requested stop")
    ]
    assert reports == ["batch-cancel"]


def test_retry_patrol_batch_rejects_archived_batch(monkeypatch):
    from types import SimpleNamespace

    from fastapi import HTTPException

    from src.gateway import workflow_api

    runs = [
        SimpleNamespace(
            id="run-1",
            template_name="patrol-change-event",
            ticket_id=None,
            source="device-management",
            status="failed",
            context={
                "patrol_batch_id": "batch-1",
                "device_id": "device-1",
                "patrol_batch_archived": True,
            },
        )
    ]
    monkeypatch.setattr(workflow_api, "list_workflow_runs", lambda **_kwargs: runs)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            workflow_api.retry_patrol_batch(
                "batch-1",
                workflow_api.WorkflowRetryRequest(),
                user=None,
            )
        )

    assert exc.value.status_code == 409


def test_retry_patrol_batch_only_retries_failed_and_cancelled(monkeypatch):
    from types import SimpleNamespace

    from src.gateway import workflow_api

    runs = [
        SimpleNamespace(
            id="run-1",
            template_name="patrol-change-event",
            ticket_id=None,
            source="device-management",
            user_id=None,
            thread_id=None,
            status="completed",
            context={"patrol_batch_id": "batch-1", "device_id": "device-1"},
        ),
        SimpleNamespace(
            id="run-2",
            template_name="patrol-change-event",
            ticket_id=None,
            source="device-management",
            user_id=None,
            thread_id=None,
            status="failed",
            context={"patrol_batch_id": "batch-1", "device_id": "device-2"},
        ),
        SimpleNamespace(
            id="run-3",
            template_name="patrol-change-event",
            ticket_id=None,
            source="device-management",
            user_id=None,
            thread_id=None,
            status="cancelled",
            context={"patrol_batch_id": "batch-1", "device_id": "device-3"},
        ),
    ]
    started = []

    class FakeEngine:
        @staticmethod
        def start(template_name, context, **kwargs):
            started.append((template_name, context, kwargs))
            return f"retry-{len(started)}"

    monkeypatch.setattr(workflow_api, "list_workflow_runs", lambda **_kwargs: runs)
    monkeypatch.setattr(workflow_api, "WorkflowEngine", FakeEngine)

    result = asyncio.run(
        workflow_api.retry_patrol_batch(
            "batch-1",
            workflow_api.WorkflowRetryRequest(),
            user=None,
        )
    )

    assert result["retried_count"] == 2
    assert [item["retry_of_run_id"] for item in result["runs"]] == ["run-2", "run-3"]
    assert [call[1]["retry_of_run_id"] for call in started] == ["run-2", "run-3"]
    assert all(call[1]["patrol_batch_id"] == "batch-1" for call in started)


def test_rerun_patrol_batch_starts_new_batch_with_latest_device_context(monkeypatch):
    from types import SimpleNamespace

    from src.gateway import workflow_api

    started = []
    runs = [
        SimpleNamespace(
            id="run-old",
            template_name="patrol-change-event",
            ticket_id=None,
            source="device-management",
            user_id="user-1",
            thread_id="thread-1",
            status="failed",
            created_at="2026-06-21T10:00:00",
            context={
                "patrol_batch_id": "batch-1",
                "device_id": "device-1",
                "device_name": "SW1",
                "retry_of_run_id": "previous",
                "langfuse_trace_id": "old-trace",
            },
        ),
        SimpleNamespace(
            id="run-new",
            template_name="patrol-change-event",
            ticket_id=None,
            source="device-management",
            user_id="user-1",
            thread_id="thread-1",
            status="completed",
            created_at="2026-06-21T10:05:00",
            context={"patrol_batch_id": "batch-1", "device_id": "device-1", "device_name": "SW1"},
        ),
        SimpleNamespace(
            id="run-2",
            template_name="patrol-change-event",
            ticket_id=None,
            source="device-management",
            user_id="user-1",
            thread_id="thread-1",
            status="completed",
            created_at="2026-06-21T10:01:00",
            context={"patrol_batch_id": "batch-1", "device_id": "device-2", "device_name": "SW2"},
        ),
    ]

    class FakeEngine:
        @staticmethod
        def start(template_name, context, **kwargs):
            started.append((template_name, context, kwargs))
            return f"rerun-{len(started)}"

    monkeypatch.setattr(workflow_api, "list_workflow_runs", lambda **_kwargs: runs)
    monkeypatch.setattr(workflow_api, "WorkflowEngine", FakeEngine)

    result = asyncio.run(
        workflow_api.rerun_patrol_batch(
            "batch-1",
            workflow_api.WorkflowRetryRequest(),
            user=None,
        )
    )

    assert result["success"] is True
    assert result["source_run_count"] == 3
    assert result["deduplicated_device_count"] == 2
    assert result["rerun_count"] == 2
    assert result["new_patrol_batch_id"].startswith("patrol-batch-")
    assert [item[1]["device_id"] for item in started] == ["device-1", "device-2"]
    assert started[0][1]["rerun_of_run_id"] == "run-new"
    assert started[0][1]["rerun_of_batch_id"] == "batch-1"
    assert started[0][1]["patrol_batch_id"] == result["new_patrol_batch_id"]
    assert "retry_of_run_id" not in started[0][1]
    assert "langfuse_trace_id" not in started[0][1]


def test_export_patrol_batch_csv(monkeypatch):
    from types import SimpleNamespace

    from src.gateway import workflow_api

    runs = [
        SimpleNamespace(
            id="run-1",
            template_name="patrol-change-event",
            ticket_id="PATROL-1",
            source="device-management",
            status="failed",
            current_step_index=2,
            error_message="boom",
            created_at="2026-06-21T10:00:00",
            completed_at="2026-06-21T10:05:00",
            context={"patrol_batch_id": "batch-1", "device_id": "device-1", "device_name": "SW1"},
        )
    ]
    monkeypatch.setattr(workflow_api, "list_workflow_runs", lambda **_kwargs: runs)

    response = asyncio.run(workflow_api.export_patrol_batch_csv("batch-1"))
    body = response.body.decode("utf-8")

    assert "patrol_batch_id,run_id,retry_of_run_id,device_id" in body
    assert "batch-1,run-1,,device-1,SW1,failed,2,PATROL-1" in body


def test_export_patrol_batch_report_summarizes_changes_and_events(monkeypatch):
    from types import SimpleNamespace

    from src.gateway import workflow_api

    runs = [
        SimpleNamespace(
            id="workflow-1",
            template_name="patrol-change-event",
            ticket_id=None,
            source="device-management",
            status="completed",
            current_step_index=3,
            error_message=None,
            created_at="2026-06-21T10:00:00",
            completed_at="2026-06-21T10:05:00",
            context={"patrol_batch_id": "batch-1", "device_id": "device-1", "device_name": "SW1"},
        )
    ]

    class FakeColumn:
        def __init__(self, name):
            self.name = name

        def __eq__(self, other):
            return lambda item: getattr(item, self.name) == other

        def asc(self):
            return self

        def desc(self):
            return self

    class FakeWorkflowStepRecord:
        run_id = FakeColumn("run_id")
        step_index = FakeColumn("step_index")

    class FakeNetworkChangeRecord:
        run_id = FakeColumn("run_id")
        detected_at = FakeColumn("detected_at")

    class FakeNetworkEventRecord:
        id = FakeColumn("id")
        run_id = FakeColumn("run_id")
        occurred_at = FakeColumn("occurred_at")

    rows_by_model = {
        FakeWorkflowStepRecord: [
            SimpleNamespace(
                run_id="workflow-1",
                step_index=0,
                step_name="patrol",
                skill_name="device-patrol",
                status="completed",
                started_at=None,
                completed_at=None,
                error_message=None,
                result={"snapshot_run_id": "snapshot-run-1"},
            )
        ],
        FakeNetworkChangeRecord: [
            SimpleNamespace(
                run_id="snapshot-run-1",
                device_id="device-1",
                entity_type="interface",
                entity_key="GE1/0/1",
                field="status",
                old_value_json="up",
                new_value_json="down",
                detected_at="2026-06-21T10:03:00",
            )
        ],
        FakeNetworkEventRecord: [
            SimpleNamespace(
                id="event-1",
                run_id="snapshot-run-1",
                source_change_id="change-1",
                device_id="device-1",
                event_type="InterfaceDown",
                severity="critical",
                status="open",
                entity_type="interface",
                entity_key="GE1/0/1",
                payload_json={},
                occurred_at="2026-06-21T10:04:00",
            )
        ],
    }

    class FakeQuery:
        def __init__(self, items):
            self.items = list(items)

        def join(self, *_args, **_kwargs):
            return self

        def filter(self, predicate):
            self.items = [item for item in self.items if predicate(item)]
            return self

        def order_by(self, *_args):
            return self

        def all(self):
            return self.items

    class FakeSession:
        def query(self, model, *additional_models):
            if additional_models:
                return FakeQuery([])
            return FakeQuery(rows_by_model.get(model, []))

    class FakeSessionContext:
        def __enter__(self):
            return FakeSession()

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(workflow_api, "list_workflow_runs", lambda **_kwargs: runs)
    monkeypatch.setattr(patrol_batch_service, "WorkflowStepRecord", FakeWorkflowStepRecord)
    monkeypatch.setattr(patrol_batch_service, "NetworkChangeRecord", FakeNetworkChangeRecord)
    monkeypatch.setattr(patrol_batch_service, "NetworkEventRecord", FakeNetworkEventRecord)
    monkeypatch.setattr(patrol_batch_service, "get_db_session", lambda: FakeSessionContext())

    response = asyncio.run(workflow_api.export_patrol_batch_report("batch-1"))
    body = response.body.decode("utf-8")

    assert "# 网络设备巡检报告" in body
    assert "**批次 ID**：`batch-1`" in body
    assert "**总体结论**：发现异常" in body
    assert "Workflow 完成只表示巡检链路执行结束，不等同于设备健康" in body
    assert "## 七、完整变化明细" in body
    assert "InterfaceDown" in body
    assert "| SW1 | workflow-1 | completed | snapshot-run-1" in body


def test_patrol_batch_summary_exposes_report_counts(monkeypatch):
    from types import SimpleNamespace

    from src.gateway import workflow_api

    runs = [
        SimpleNamespace(
            id="workflow-1",
            template_name="patrol-change-event",
            ticket_id=None,
            source="device-management",
            status="completed",
            current_step_index=3,
            error_message=None,
            created_at="2026-06-21T10:00:00",
            completed_at="2026-06-21T10:05:00",
            context={"patrol_batch_id": "batch-1", "device_id": "device-1", "device_name": "SW1"},
        )
    ]

    class FakeColumn:
        def __init__(self, name):
            self.name = name

        def __eq__(self, other):
            return lambda item: getattr(item, self.name) == other

        def asc(self):
            return self

        def desc(self):
            return self

    class FakeWorkflowStepRecord:
        run_id = FakeColumn("run_id")
        step_index = FakeColumn("step_index")

    class FakeNetworkChangeRecord:
        run_id = FakeColumn("run_id")
        detected_at = FakeColumn("detected_at")

    class FakeNetworkEventRecord:
        id = FakeColumn("id")
        run_id = FakeColumn("run_id")
        occurred_at = FakeColumn("occurred_at")

    rows_by_model = {
        FakeWorkflowStepRecord: [
            SimpleNamespace(
                run_id="workflow-1",
                step_index=0,
                step_name="patrol",
                skill_name="device-patrol",
                status="completed",
                started_at=None,
                completed_at=None,
                error_message=None,
                result={"snapshot_run_id": "snapshot-run-1"},
            )
        ],
        FakeNetworkChangeRecord: [
            SimpleNamespace(
                id="change-1",
                run_id="snapshot-run-1",
                device_id="device-1",
                entity_type="interface",
                entity_key="GE1/0/1",
                field="status",
                old_value_json="up",
                new_value_json="down",
                detected_at="2026-06-21T10:03:00",
            )
        ],
        FakeNetworkEventRecord: [
            SimpleNamespace(
                id="event-1",
                run_id="snapshot-run-1",
                source_change_id="change-1",
                device_id="device-1",
                event_type="InterfaceDown",
                severity="critical",
                status="open",
                entity_type="interface",
                entity_key="GE1/0/1",
                payload_json={"field": "status", "new": "down"},
                occurred_at="2026-06-21T10:04:00",
            )
        ],
    }

    class FakeQuery:
        def __init__(self, items):
            self.items = list(items)

        def filter(self, predicate):
            self.items = [item for item in self.items if predicate(item)]
            return self

        def join(self, *_args):
            return self

        def order_by(self, *_args):
            return self

        def all(self):
            return self.items

    class FakeSession:
        def query(self, *models):
            if len(models) != 1:
                return FakeQuery([])
            return FakeQuery(rows_by_model.get(models[0], []))

    class FakeSessionContext:
        def __enter__(self):
            return FakeSession()

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(workflow_api, "list_workflow_runs", lambda **_kwargs: runs)
    monkeypatch.setattr(patrol_batch_service, "WorkflowStepRecord", FakeWorkflowStepRecord)
    monkeypatch.setattr(patrol_batch_service, "NetworkChangeRecord", FakeNetworkChangeRecord)
    monkeypatch.setattr(patrol_batch_service, "NetworkEventRecord", FakeNetworkEventRecord)
    monkeypatch.setattr(patrol_batch_service, "get_db_session", lambda: FakeSessionContext())

    result = asyncio.run(workflow_api.get_patrol_batch_summary("batch-1"))

    assert result["run_count"] == 1
    assert result["device_count"] == 1
    assert result["change_count"] == 1
    assert result["event_count"] == 1
    assert result["pipeline"]["stages"][0]["label"] == "命令采集与解析"
    assert result["event_types"] == {"InterfaceDown": 1}
    assert result["severities"] == {"critical": 1}
    assert result["runs"][0]["snapshot_run_id"] == "snapshot-run-1"
    assert result["sample_events"][0]["id"] == "event-1"
    assert result["deliverables"]["report_url"].endswith("/batch-1/report.docx")
    assert result["deliverables"]["technical_report_url"].endswith("/batch-1/report.md")


def test_patrol_batch_summary_returns_diagnostic_empty_state_for_manifest_without_runs(
    monkeypatch,
):
    from src.gateway import workflow_api

    expected = {"patrol_batch_id": "batch-empty", "run_count": 0, "pipeline": {"stages": []}}
    monkeypatch.setattr(workflow_api, "_list_patrol_batch_runs", lambda _batch_id: [])
    monkeypatch.setattr(workflow_api, "_patrol_batch_manifest_exists", lambda _batch_id: True)
    monkeypatch.setattr(
        workflow_api,
        "_build_patrol_batch_summary",
        lambda _batch_id, _runs: expected,
    )

    result = asyncio.run(workflow_api.get_patrol_batch_summary("batch-empty"))

    assert result == expected


def test_patrol_report_does_not_treat_collected_state_as_healthy():
    from src.core.workflows.patrol_batch_service import domain_checks

    checks = domain_checks(
        observations=[
            {
                "entity_type": "interface_l3",
                "entity_key": '["GE1/0"]',
                "field": "status",
                "value": "down",
                "command": "display interface brief",
            }
        ],
        command_details=[
            {
                "command": "display interface brief",
                "parse_status": "parsed",
            }
        ],
        changes=[],
        events=[],
    )

    assert checks[0]["status"] == "未发现变化（健康未判定）"
    assert "不代表当前值处于健康范围" in checks[0]["reason"]


def test_patrol_report_distinguishes_missing_rules_from_evaluated_non_match():
    from src.core.workflows.patrol_batch_service import domain_checks

    base = {
        "device_id": "device-1",
        "entity_type": "memory",
        "entity_key": '["0"]',
        "field": "usage",
        "command": "display memory",
    }
    observation = {**base, "value": 51}
    command = {"command": "display memory", "parse_status": "parsed"}

    missing = domain_checks(
        observations=[observation],
        command_details=[command],
        changes=[{**base, "no_event_reason": "no_active_event_rules"}],
        events=[],
    )
    evaluated = domain_checks(
        observations=[observation],
        command_details=[command],
        changes=[{**base, "no_event_reason": "active_rules_evaluated_no_match"}],
        events=[],
    )

    assert missing[0]["status"] == "规则缺失"
    assert evaluated[0]["status"] == "有变化，未触发事件"


def test_patrol_report_does_not_treat_recovery_event_as_current_fault():
    from src.core.workflows.patrol_batch_service import domain_checks

    observation = {
        "device_id": "device-1",
        "entity_type": "interface",
        "entity_key": '["GE1/0/1"]',
        "field": "link",
        "value": "UP",
        "command": "display interface brief",
    }
    checks = domain_checks(
        observations=[observation],
        command_details=[
            {"command": "display interface brief", "parse_status": "parsed"}
        ],
        changes=[],
        events=[
            {
                **observation,
                "event_type": "InterfaceUp",
                "event_class": "recovery",
                "status_after_occurrence": "resolved",
            }
        ],
        current_findings=[],
        rule_coverage={
            "available": True,
            "by_domain": {"interface": {"evaluated_observation_count": 1}},
        },
    )

    assert checks[0]["status"] == "本次覆盖范围内未发现当前异常"
    assert checks[0]["current_risk_count"] == 0
    assert checks[0]["unresolved_fault_event_count"] == 0
    assert "恢复事件" in checks[0]["reason"]
