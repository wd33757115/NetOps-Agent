# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""Workflow 热重载、Timeline、Subworkflow 单元测试。"""

import json
from unittest.mock import MagicMock, patch

import pytest

from src.core.workflows.events import append_timeline_event, get_workflow_timeline, publish_workflow_event
from src.core.workflows.generator import dsl_from_collab_template, generate_plugin_files
from src.core.workflows.reload_bus import RELOAD_CHANNEL, broadcast_workflow_reload, reload_all_registries


def test_reload_all_registries_local():
    stats = reload_all_registries(source="test")
    assert "templates" in stats
    assert stats["templates"] >= 0


def test_broadcast_workflow_reload_publishes():
    mock_client = MagicMock()
    with patch("src.core.workflows.reload_bus.get_redis", return_value=mock_client):
        with patch("src.core.workflows.reload_bus.reload_all_registries", return_value={"templates": 3, "intents": 1}):
            broadcast_workflow_reload(source="unit-test", plugin_name="demo")
    mock_client.publish.assert_called_once()
    channel, payload = mock_client.publish.call_args[0]
    assert channel == RELOAD_CHANNEL
    data = json.loads(payload)
    assert data["source"] == "unit-test"
    assert data["plugin_name"] == "demo"


def test_timeline_append_and_read():
    mock_client = MagicMock()
    mock_client.lrange.return_value = [
        json.dumps({"run_id": "r1", "status": "started", "message": "ok"}),
    ]
    with patch("src.core.workflows.events.get_redis", return_value=mock_client):
        append_timeline_event("r1", {"run_id": "r1", "status": "started", "message": "ok"})
        events = get_workflow_timeline("r1")
    assert len(events) == 1
    assert events[0]["status"] == "started"
    mock_client.rpush.assert_called_once()


def test_publish_workflow_event_writes_timeline():
    mock_client = MagicMock()
    with patch("src.core.workflows.events.get_redis", return_value=mock_client):
        publish_workflow_event("run-x", status="running", message="step")
    assert mock_client.rpush.called
    assert mock_client.publish.called


def test_subworkflow_dsl_yaml_roundtrip():
    dsl = dsl_from_collab_template(
        plugin_name="parent-with-sub",
        description="父流程含子流程",
        step1_skill="firewall-policy-generator",
        step2_skill=None,
        category="custom",
    )
    dsl.steps = [
        dsl.steps[0],
        dsl.steps[0].model_copy(
            update={
                "id": "s-sub",
                "name": "nested_flow",
                "label": "嵌套 ITSM 流程",
                "skill": "",
                "subworkflow": "itsm-firewall-change",
            },
        ),
    ]
    files = generate_plugin_files(dsl, auto_map_inputs=False)
    assert "subworkflow: itsm-firewall-change" in files["WORKFLOW.yaml"]
