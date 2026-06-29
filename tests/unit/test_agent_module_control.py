# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import pytest

from src.core.agent_modules.control import (
    AgentModuleGovernanceError,
    required_modules_for_workflow,
)
from src.core.workflows import engine


def test_required_modules_for_patrol_change_event():
    assert required_modules_for_workflow("patrol-change-event", {}) == [
        "device-management",
        "collection-profile",
        "parser-control",
        "state-normalizer",
        "change-detector",
        "event-builder",
    ]


def test_required_modules_for_non_patrol_workflow_is_empty():
    assert required_modules_for_workflow("itsm-firewall-change", {}) == []


def test_workflow_engine_blocks_when_agent_module_governance_fails(monkeypatch):
    created_runs: list[str] = []

    def fake_enforce(template_name, context):
        assert template_name == "patrol-change-event"
        assert context["mode"] == "production"
        raise AgentModuleGovernanceError(
            "Workflow patrol-change-event 被 Agent 模块治理阻断: change-detector=maintenance",
            [
                {
                    "failure_code": "agent_module_not_enabled",
                    "module_id": "change-detector",
                    "status": "maintenance",
                    "maintenance_reason": "规则升级窗口",
                }
            ],
        )

    def fake_create_workflow_run(**kwargs):
        created_runs.append(kwargs["template_name"])
        return "run-1"

    monkeypatch.setattr(engine, "enforce_workflow_modules_enabled", fake_enforce)
    monkeypatch.setattr(engine, "create_workflow_run", fake_create_workflow_run)

    with pytest.raises(AgentModuleGovernanceError) as exc:
        engine.WorkflowEngine.start(
            "patrol-change-event",
            {"mode": "production"},
            source="test",
        )

    assert exc.value.violations[0]["module_id"] == "change-detector"
    assert created_runs == []
