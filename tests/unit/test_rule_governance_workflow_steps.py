"""Historical Workflow messages are closed; new generation uses business tasks."""
from types import SimpleNamespace
from unittest.mock import Mock
import pytest

@pytest.mark.parametrize("name", ["rule-asset-generation", "rule-asset-submit", "rule-asset-approve"])
def test_legacy_queued_delivery_cannot_execute_business_code(monkeypatch, name):
    from src.core.workflows import tasks
    step = SimpleNamespace(name="legacy", step_name="legacy", skill_name="rule-asset-persister", subworkflow=None)
    monkeypatch.setattr(tasks, "get_workflow_run", lambda _: SimpleNamespace(template_name=name, status="running"))
    monkeypatch.setattr(tasks, "get_template", lambda _: SimpleNamespace(steps=[step]))
    monkeypatch.setattr(tasks, "list_workflow_steps", lambda _: [step])
    monkeypatch.setattr(tasks.WorkflowEngine, "ensure_wf_trace", lambda _: None)
    execute = Mock()
    monkeypatch.setattr(tasks.execute_skill_task, "run", execute)
    assert tasks._run_workflow_step("run", 0)["failure_code"] == "rule_workflow_retired"
    execute.assert_not_called()
