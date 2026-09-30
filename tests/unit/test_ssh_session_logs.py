from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime

import pytest

from src.infrastructure.db.models import AuditLogRecord
from src.platform.device_access import governance, session_logs
from src.platform.device_access.contracts import (
    AccessContext,
    ConnectionProfile,
    DeviceAccessError,
    Target,
)


def operation(actor="alice", name="核心交换机", **kwargs):
    return governance.SSHOperation(
        actor,
        "test",
        "saved-config",
        "192.0.2.1",
        device_name=name,
        secrets=("test-private-password",),
        **kwargs,
    )


def test_each_connection_persists_one_readable_log_with_name_and_redaction(ssh_log_storage):
    op = operation()
    op.event("connecting")
    op.event("connected")
    ticket = op.before_command("display version", phase="setup")
    op.after_command(ticket, output="test-private-password\nversion output")
    op.event("disconnected")
    assert len(ssh_log_storage.objects) == 1
    assert len(ssh_log_storage.uploads) == 5
    log = session_logs.read_log(op.session_id, actor_id="alice")
    assert log["status"] == "closed" and log["port"] == 22
    assert log["filename"].startswith("核心交换机_") and log["filename"].endswith(".log")
    assert "初始化命令: display version" in log["content"]
    assert "version output" in log["content"] and "连接结束" in log["content"]
    assert "test-private-password" not in log["content"]
    assert "未知" not in log["content"]
    assert len(ssh_log_storage.uploads) == 5  # Current objects are not rewritten on reads.
    second = operation()
    second.event("connecting")
    assert len(ssh_log_storage.objects) == 2


def test_completion_events_share_the_session_file_without_claiming_execution(ssh_log_storage):
    op = operation()
    op.event("connecting")
    op.event("completion_requested", command="dis", request_id="tab1")
    op.event("completion_finished", command="dis", completed_command="display ",
             cleared=True, request_id="tab1", status="succeeded")
    log = session_logs.read_log(op.session_id, actor_id="alice")
    assert len(ssh_log_storage.objects) == 1
    assert "请求 Tab 补全（未执行命令）" in log["content"]
    assert "补全结果: display " in log["content"] and "远端草稿已清空" in log["content"]
    assert "执行结果: 成功" not in log["content"]
    assert governance.query_audit(management_only=True)["total"] == 0


def test_management_events_and_unused_ticket_are_not_connection_files(ssh_log_storage):
    for kind in (
        "session_created",
        "session_login_updated",
        "host_key_accepted",
        "terminal_requested",
    ):
        governance.write_event(
            actor_id="alice",
            source_app="test",
            kind=kind,
            detail={"session_id": "saved-config", "host": "192.0.2.1"},
        )
    assert not ssh_log_storage.objects
    assert session_logs.list_logs()["total"] == 0
    assert governance.query_audit(management_only=True)["total"] == 4
    op = operation()
    op.event("connecting")
    op.event("host_key_accepted")
    assert governance.query_audit(management_only=True)["total"] == 4
    assert "主机密钥已接受" in session_logs.read_log(op.session_id)["content"]


def test_storage_failure_keeps_durable_audit_and_blocks_command_before_send(ssh_log_storage):
    op = operation()
    op.event("connecting")
    ssh_log_storage.fail = True
    sent = []
    with pytest.raises(DeviceAccessError) as error:
        op.execute("display version", lambda: sent.append("sent"))
    assert error.value.failure_code == "audit_unavailable"
    assert not sent
    assert governance.query_audit(kind="command_requested")["total"] == 1
    ssh_log_storage.fail = False
    log = session_logs.read_log(op.session_id)
    assert "尚未记录完成结果" in log["content"]
    assert "是否已发送及执行结果未知" in log["content"]


def test_failed_connection_denial_and_partial_disconnect_are_preserved(ssh_log_storage):
    op = operation()
    op.event("connecting")
    governance.save_policy([], "admin")
    with pytest.raises(DeviceAccessError):
        op.before_command("reboot")
    op.event("connection_failed", status="failed", failure_code="connection_closed")
    op.event("disconnected")
    log = session_logs.read_log(op.session_id)
    assert log["status"] == "failed"
    assert "命令已拒绝" in log["content"] and "命令: reboot" in log["content"]
    assert "connection_closed" in log["content"]


def test_log_access_checks_owner_before_any_storage_read(ssh_log_storage, monkeypatch):
    op = operation()
    op.event("connecting")
    monkeypatch.setattr(
        ssh_log_storage, "download_file", lambda key: pytest.fail("unauthorized read")
    )
    for identifier in (op.session_id, "not-a-session"):
        with pytest.raises(DeviceAccessError) as error:
            session_logs.read_log(identifier, actor_id="bob")
        assert error.value.failure_code == "session_log_not_found"
    assert session_logs.list_logs(actor_id="bob")["total"] == 0
    assert session_logs.list_logs(actor_id="alice")["total"] == 1
    assert session_logs.list_logs(host="192.0.2.9")["total"] == 0


def test_history_restores_only_requested_log_with_beijing_name_and_truncation(
    ssh_governance_database,
    ssh_log_storage,
):
    with ssh_governance_database() as session:
        for identifier in ("old-one", "old-two"):
            for number, (kind, detail) in enumerate(
                (
                    ("opening", {}),
                    (
                        "command_finished",
                        {"command": "show version", "output": "retained", "output_truncated": True},
                    ),
                    ("closed", {}),
                )
            ):
                session.add(
                    AuditLogRecord(
                        id=f"{identifier}-{number}",
                        user_id="alice",
                        resource_type="ssh",
                        action="ssh." + kind,
                        status="success",
                        created_at=datetime(2026, 9, 30, 3, 20, 35, 123456 + number),
                        detail={
                            "session_id": identifier,
                            "host": "192.0.2.1",
                            "port": 22,
                            "source_app": "legacy",
                            **detail,
                        },
                    )
                )
    listed = session_logs.list_logs(limit=1)
    assert listed["total"] == 2 and len(listed["items"]) == 1
    assert "content" not in listed["items"][0]
    assert not ssh_log_storage.objects
    log = session_logs.read_log("old-one", actor_id="alice")
    assert len(ssh_log_storage.objects) == 1
    assert log["filename"] == "192.0.2.1_2026-09-30_11-20-35.123456.log"
    assert "输出已截断" in log["content"] and "retained" in log["content"]
    assert "命令: show version" in log["content"]


def test_filename_is_safe_and_connection_name_snapshot_is_stable(ssh_log_storage):
    op = operation(name="../核心/交换机:*?\n")
    op.event("connecting")
    op.device_name = "later-name"
    op.event("connected")
    log = session_logs.read_log(op.session_id)
    assert "later-name" not in log["filename"]
    assert all(c not in log["filename"] for c in "/\\:*?\n")
    assert next(iter(ssh_log_storage.objects)).startswith(session_logs.PREFIX)


def test_caller_transaction_projects_after_commit_and_never_after_rollback(
    ssh_governance_database,
    ssh_log_storage,
):
    op = operation()
    with ssh_governance_database() as session:
        governance.write_event(
            actor_id="alice",
            source_app="test",
            kind="connecting",
            session=session,
            detail={"session_id": op.session_id, "host": op.host},
        )
        assert not ssh_log_storage.objects
    assert len(ssh_log_storage.objects) == 1
    uploads = len(ssh_log_storage.uploads)
    with pytest.raises(RuntimeError):
        with ssh_governance_database() as session:
            governance.write_event(
                actor_id="alice",
                source_app="test",
                kind="host_key_observed",
                session=session,
                detail={"session_id": op.session_id, "host": op.host},
            )
            raise RuntimeError("rollback")
    assert len(ssh_log_storage.uploads) == uploads
    assert governance.query_audit(kind="host_key_observed")["total"] == 0


def test_post_commit_storage_failure_is_not_a_rollback_of_durable_audit(
    ssh_governance_database,
    ssh_log_storage,
):
    ssh_log_storage.fail = True
    with pytest.raises(DeviceAccessError) as error:
        with ssh_governance_database() as session:
            governance.write_event(
                actor_id="alice",
                source_app="test",
                kind="opening",
                session=session,
                detail={"session_id": "persisted", "host": "192.0.2.1"},
            )
    assert error.value.failure_code == "audit_unavailable"
    assert governance.query_audit(kind="opening")["total"] == 1


def test_concurrent_events_do_not_overwrite_completed_transcript(ssh_log_storage):
    op = operation()
    op.event("connecting")
    with ThreadPoolExecutor(max_workers=4) as executor:
        list(
            executor.map(
                lambda number: op.event(
                    "command_denied",
                    status="denied",
                    command=f"command-{number}",
                ),
                range(8),
            )
        )
    op.event("disconnected")
    raw = next(iter(ssh_log_storage.objects.values())).decode()
    assert all(f"command-{number}" in raw for number in range(8))
    assert raw.rstrip().endswith("连接结束")


def test_explicit_handle_id_never_reuses_an_unrelated_active_operation():
    original = operation(session_id="outer")
    target = Target("saved-config", original.host, display_name="target-name")
    profile = ConnectionProfile("p", "user", "fixture-password")
    context = AccessContext("alice", "test")
    with original.activate():
        same = governance.operation_for(context, target, profile)
        different = governance.operation_for(context, target, profile, session_id="new-handle")
    assert same is original
    assert different.session_id == "new-handle" and different.device_name == "target-name"
    original.event("connecting")
    with original.activate():
        assert governance.operation_for(context, target, profile) is not original


def test_existing_connection_retry_gets_distinct_closed_logs():
    from tests.unit.test_device_access import batch

    fixture = batch.__wrapped__()
    fixture.service.profile_resolver = lambda _: replace(
        fixture.profile,
        connection_attempts=2,
        retry_delay=0,
    )
    attempts = []

    def connect(target, profile):
        attempts.append(governance.current_operation().session_id)
        if len(attempts) == 1:
            raise TimeoutError("timeout")
        return fixture.connection

    fixture.driver.connect = connect
    fixture.service.execute_batch(fixture.context, fixture.request)
    assert len(set(attempts)) == 2
    first, second = [session_logs.read_log(identifier) for identifier in attempts]
    assert first["status"] == "failed" and second["status"] == "closed"
    assert "连接失败" in first["content"] and "连接结束" in first["content"]
