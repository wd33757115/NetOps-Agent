# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""P3：Langfuse trace 在 Celery / Skill 路径的传播测试。"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from src.observability.trace_context import (
    extract_observability_context,
    observability_from_workflow_run,
    strip_observability_keys,
)


def test_extract_observability_context():
    ctx = extract_observability_context(
        {
            "langfuse_trace_id": "lf-123",
            "langfuse_workflow_root_span_id": "wf-root-span",
            "workflow_run_id": "run-abc",
            "ticket_id": "REQ1",
        }
    )
    assert ctx["trace_id"] == "lf-123"
    assert ctx["run_id"] == "run-abc"
    assert ctx["workflow_root_span_id"] == "wf-root-span"


def test_strip_observability_keys():
    cleaned = strip_observability_keys(
        {
            "langfuse_trace_id": "x",
            "langfuse_workflow_root_span_id": "span-1",
            "workflow_run_id": "run-1",
            "ticket_id": "T1",
        }
    )
    assert "langfuse_trace_id" not in cleaned
    assert "langfuse_workflow_root_span_id" not in cleaned
    assert cleaned["workflow_run_id"] == "run-1"
    assert cleaned["ticket_id"] == "T1"


def test_observability_from_workflow_run(monkeypatch):
    fake_run = MagicMock()
    fake_run.context = {
        "langfuse_trace_id": "trace-from-db",
        "langfuse_workflow_root_span_id": "root-span-db",
    }
    monkeypatch.setattr(
        "src.core.workflows.repository.get_workflow_run",
        lambda run_id: fake_run if run_id == "run-1" else None,
    )
    ctx = observability_from_workflow_run("run-1")
    assert ctx["trace_id"] == "trace-from-db"
    assert ctx["run_id"] == "run-1"
    assert ctx["workflow_root_span_id"] == "root-span-db"


@patch("src.observability.langfuse.get_langfuse_client")
def test_resume_workflow_trace_nested(mock_get_client):
    from src.observability.langfuse import resume_workflow_trace

    client = MagicMock()
    mock_get_client.return_value = client

    wf = resume_workflow_trace(
        "chat-trace-1",
        run_id="run-1",
        template_name="demo",
        workflow_root_span_id="wf-root-span",
    )
    assert wf is not None
    assert wf.trace_id == "chat-trace-1"
    assert wf.workflow_root_span_id == "wf-root-span"
    assert wf.nested_under_chat is True
    client.trace.assert_not_called()


@patch("src.observability.langfuse.get_langfuse_client")
def test_start_workflow_trace_nested_under_chat(mock_get_client):
    from src.observability.langfuse import start_workflow_trace

    client = MagicMock()
    root_span = MagicMock(id="wf-root-span")
    client.span.return_value = root_span
    mock_get_client.return_value = client

    wf = start_workflow_trace(
        run_id="run-1",
        template_name="itsm-firewall-change",
        parent_trace_id="chat-trace-99",
    )
    assert wf is not None
    assert wf.trace_id == "chat-trace-99"
    assert wf.workflow_root_span_id == "wf-root-span"
    assert wf.nested_under_chat is True
    client.span.assert_called_once()
    assert client.span.call_args.kwargs["trace_id"] == "chat-trace-99"
    client.trace.assert_not_called()


@patch("src.observability.langfuse.get_langfuse_client")
def test_resume_workflow_trace(mock_get_client):
    from src.observability.langfuse import resume_workflow_trace

    client = MagicMock()
    trace = MagicMock(id="existing-trace")
    client.trace.return_value = trace
    mock_get_client.return_value = client

    wf = resume_workflow_trace("existing-trace", run_id="run-1", template_name="demo")
    assert wf is not None
    assert wf.trace_id == "existing-trace"
    client.trace.assert_called_once()
    assert client.trace.call_args.kwargs["id"] == "existing-trace"


@patch("src.observability.langfuse.flush_langfuse")
@patch("src.observability.langfuse.get_langfuse_client")
def test_record_skill_execution_span(mock_get_client, mock_flush):
    from src.observability.langfuse import record_skill_execution_span

    client = MagicMock()
    span = MagicMock()
    client.span.return_value = span
    mock_get_client.return_value = client

    record_skill_execution_span(
        trace_id="trace-1",
        skill_name="firewall-policy-generator",
        run_id="run-1",
        parent_observation_id="wf-root-span",
        status="completed",
        input_params={"ticket_id": "REQ1"},
        output={"success": True},
    )

    client.span.assert_called_once()
    assert client.span.call_args.kwargs["trace_id"] == "trace-1"
    assert client.span.call_args.kwargs["parent_observation_id"] == "wf-root-span"
    assert client.span.call_args.kwargs["name"] == "skill:firewall-policy-generator"
    span.end.assert_called_once()
    mock_flush.assert_called_once()


@patch("src.core.skills.runner.record_skill_execution_span")
@patch("src.core.skills.executor._run_subprocess_skill")
@patch("src.core.skills.executor.resolve_entry_script")
def test_execute_skill_records_langfuse_span(mock_resolve, mock_run, mock_record):
    from pathlib import Path

    from src.core.skills.executor import execute_skill

    mock_resolve.return_value = Path("run.py")
    mock_run.return_value = {"success": True, "message": "ok"}

    execute_skill(
        "firewall-policy-generator",
        {
            "ticket_id": "REQ1",
            "langfuse_trace_id": "lf-99",
            "langfuse_workflow_root_span_id": "wf-root-span",
            "workflow_run_id": "run-99",
        },
    )

    mock_record.assert_called_once()
    assert mock_record.call_args.kwargs["trace_id"] == "lf-99"
    assert mock_record.call_args.kwargs["run_id"] == "run-99"
    assert mock_record.call_args.kwargs["parent_observation_id"] == "wf-root-span"
    skill_params = mock_run.call_args[0][1]
    assert "langfuse_trace_id" not in skill_params
    assert "langfuse_workflow_root_span_id" not in skill_params
