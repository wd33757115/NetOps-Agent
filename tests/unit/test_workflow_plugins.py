# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""Workflow 表达式与注册表单元测试。"""

from src.core.workflows.expression import build_step_env, resolve_inputs, resolve_value
from src.core.workflows.registry import get_template, load_workflows


def test_load_firewall_workflow_plugin():
    load_workflows(force=True)
    tpl = get_template("itsm-firewall-change")
    assert tpl is not None
    assert len(tpl.steps) == 3
    assert tpl.steps[0].skill_name == "firewall-policy-generator"
    assert tpl.steps[2].when == "${context.callback_url}"


def test_load_patrol_change_event_workflow():
    load_workflows(force=True)
    tpl = get_template("patrol-change-event")

    assert tpl is not None
    assert [step.skill_name for step in tpl.steps] == [
        "device-patrol",
        "change-detector",
        "event-builder",
    ]
    assert (
        tpl.steps[1].inputs["current_run_id"]
        == "${steps.patrol.result.snapshot_run_id}"
    )
    assert tpl.steps[2].inputs["run_id"] == "${steps.patrol.result.snapshot_run_id}"


def test_workflow_owns_test_context_contract():
    from src.core.workflows.registry import missing_test_context

    load_workflows(force=True)
    tpl = get_template("parser-validation-ingest")

    assert tpl is not None
    assert tpl.test_context["dataset_id"] == "dev-test-previous-quarter"
    assert tpl.test_required_context == ["file_path"]
    assert missing_test_context(tpl, tpl.test_context) == ["file_path"]
    assert missing_test_context(
        tpl,
        {**tpl.test_context, "file_path": "C:/captures"},
    ) == []


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
    assert format_steps_flow(active) == "生成配置 ZIP → 编写变更工单 Excel"


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
