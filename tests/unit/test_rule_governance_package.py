from contextlib import contextmanager
from unittest.mock import Mock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from src.infrastructure.db.models import ParserControlSetting, RuleGenerationJobRecord
















@pytest.mark.parametrize("name", ["rule-asset-generation", "rule-asset-submit", "rule-asset-approve"])
def test_generic_workflow_entry_is_retired(name):
    from src.core.workflows.engine import WorkflowEngine
    with pytest.raises(ValueError, match="规则治理"):
        WorkflowEngine.start(name, {})


def test_manifest_owns_tasks_and_schedules_once():
    from src.core.celery_tasks.celery_app import celery
    from src.platform.kernel.registry import discover, task_configuration
    manifest = discover()["rule-governance"]
    modules, routes, schedules = task_configuration()
    assert manifest.data["backend"]["task_names"]
    assert all(routes[t]["queue"] == "app.rule-governance" for t in manifest.data["backend"]["task_names"])
    for item in manifest.data["backend"]["schedules"]:
        assert sum(entry["task"] == item["task"] for entry in celery.conf.beat_schedule.values()) == 1
    assert "src.apps.rule_governance.backend.tasks" not in modules


def test_handoff_rejects_old_worker_on_either_legacy_queue():
    from src.platform.kernel.readiness import check_worker_reports
    from src.platform.kernel.registry import discover
    manifest = discover()["rule-governance"]
    report = {"apps": {manifest.id: manifest.digest}, "tasks": manifest.data["backend"]["task_names"]}
    for queue in ("netops.ai", "netops.patrol"):
        queues = {"new": [{"name": "app.rule-governance"}], "old": [{"name": queue}]}
        with pytest.raises(ValueError, match="mismatch"):
            check_worker_reports(manifest, queues, {"new": report})
        assert set(check_worker_reports(manifest, queues, {"new": report, "old": report})) == {"new", "old"}


def test_execution_lock_prevents_drain_while_child_is_alive():
    from src.apps.rule_governance.backend.access import execution_lock
    with execution_lock():
        with pytest.raises(ValueError, match="排空"):
            with execution_lock(exclusive=True):
                pytest.fail("drain raced a running generator")
    with execution_lock(exclusive=True):
        pass
