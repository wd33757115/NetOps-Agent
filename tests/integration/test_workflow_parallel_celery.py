# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""Workflow 并行 Celery 集成测试（eager 模式 + PostgreSQL）。"""

from __future__ import annotations

import time
import uuid
from pathlib import Path

import pytest

from src.core.workflows.engine import WorkflowEngine
from src.core.workflows.generator import generate_plugin_files
from src.core.workflows.manager import save_plugin
from src.core.workflows.registry import WORKFLOWS_ROOT, get_template, load_workflows
from src.core.workflows.repository import get_workflow_run, list_workflow_steps
from src.core.workflows.dsl import WorkflowDSL, WorkflowMetaDSL, WorkflowStepDSL
from src.infrastructure.db.migrations import upgrade_database
from src.infrastructure.db.postgres import verify_postgres_connection


@pytest.fixture(scope="module")
def postgres_available():
    if not verify_postgres_connection():
        pytest.skip("PostgreSQL 不可用")
    upgrade_database()
    return True


@pytest.fixture
def celery_eager():
    from src.core.celery_tasks.celery_app import celery

    old_eager = celery.conf.task_always_eager
    old_prop = celery.conf.task_eager_propagates
    celery.conf.task_always_eager = True
    celery.conf.task_eager_propagates = True
    yield
    celery.conf.task_always_eager = old_eager
    celery.conf.task_eager_propagates = old_prop


@pytest.fixture
def parallel_plugin_name(postgres_available):
    name = f"parallel-test-{uuid.uuid4().hex[:8]}"
    yield name
    plugin_dir = WORKFLOWS_ROOT / "custom" / name
    if plugin_dir.exists():
        import shutil

        shutil.rmtree(plugin_dir, ignore_errors=True)
    load_workflows(force=True)


def _build_parallel_dsl(name: str) -> WorkflowDSL:
    return WorkflowDSL(
        meta=WorkflowMetaDSL(name=name, description="并行测试", category="custom"),
        steps=[
            WorkflowStepDSL(
                id="p1",
                name="parallel_a",
                label="并行 A",
                skill="firewall-policy-generator",
                parallel_group="batch1",
                inputs={"ticket_id": "${context.ticket_id}"},
            ),
            WorkflowStepDSL(
                id="p2",
                name="parallel_b",
                label="并行 B",
                skill="itsm-change-ticket-writer",
                parallel_group="batch1",
                inputs={"ticket_id": "${context.ticket_id}"},
            ),
        ],
    )


@pytest.mark.integration
@pytest.mark.postgres
@pytest.mark.celery
def test_parallel_batch_completes_with_celery_eager(
    parallel_plugin_name,
    celery_eager,
    monkeypatch,
):
    """并行批在 Celery eager 模式下应完成两步并标记 run completed。"""

    def _fake_execute(skill_name: str, params):
        return {
            "success": True,
            "message": f"mock-{skill_name}",
            "artifacts": {f"art_{skill_name}": {"file_key": f"mock/{skill_name}"}},
        }

    monkeypatch.setattr("src.core.skills.executor.execute_skill", _fake_execute)

    dsl = _build_parallel_dsl(parallel_plugin_name)
    files = generate_plugin_files(dsl, auto_map_inputs=False)
    save_result = save_plugin(parallel_plugin_name, category="custom", files=files)
    assert save_result["success"] is True
    load_workflows(force=True)
    assert get_template(parallel_plugin_name) is not None

    run_id = WorkflowEngine.start(
        parallel_plugin_name,
        {"ticket_id": f"REQ-PAR-{uuid.uuid4().hex[:6]}"},
        source="test",
    )

    deadline = time.time() + 30
    run = get_workflow_run(run_id)
    while run and run.status not in ("completed", "failed") and time.time() < deadline:
        time.sleep(0.2)
        run = get_workflow_run(run_id)

    assert run is not None
    assert run.status == "completed", run.error_message
    steps = list_workflow_steps(run_id)
    assert len(steps) == 2
    assert all(s.status == "completed" for s in steps)
