"""A workflow notification written both ways (event consumer and direct fallback) is stored once."""

from types import SimpleNamespace

from src.core.events import publishers
from src.core.workflows import engine as engine_module
from src.core.workflows.engine import WorkflowEngine


def test_the_direct_write_uses_the_key_the_event_consumer_uses(monkeypatch):
    published = []
    monkeypatch.setattr(engine_module, "get_settings",
                        lambda: SimpleNamespace(EVENT_BUS_ENABLED=True, EVENT_BUS_DIRECT_NOTIFY_FALLBACK=True))
    monkeypatch.setattr(engine_module, "get_workflow_run", lambda _run_id: SimpleNamespace(
        user_id="1", thread_id=None, ticket_id="DEMO-2", context={}))
    monkeypatch.setattr(publishers.EventBus, "publish", lambda _stream, event: published.append(event) or "1-0")

    payload = {"title": "文本报告已生成 (DEMO-2)", "body": "统计结果：3 行", "artifacts": {}, "level": "success"}
    done, key = WorkflowEngine._publish_workflow_notify("workflow.completed", "run-1", payload)

    # Fallback on: the engine still writes, with the same key, so the database keeps one of the two.
    assert done is False
    assert key == published[0].idempotency_key
    # A later completion of a retried run carries other content, and is told apart.
    _, retried = WorkflowEngine._publish_workflow_notify("workflow.completed", "run-1", {**payload, "body": "统计结果：4 行"})
    assert retried != key


def test_without_the_event_bus_there_is_no_shared_key(monkeypatch):
    monkeypatch.setattr(engine_module, "get_settings",
                        lambda: SimpleNamespace(EVENT_BUS_ENABLED=False, EVENT_BUS_DIRECT_NOTIFY_FALLBACK=True))

    assert WorkflowEngine._publish_workflow_notify("workflow.completed", "run-1", {}) == (False, None)
