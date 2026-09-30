import json
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from src.infrastructure.db.models import AuditLogRecord, SSHPolicyRecord
from src.platform.device_access import governance
from src.platform.device_access.contracts import DeviceAccessError


def operation():
    return governance.SSHOperation(
        "alice",
        "topology",
        "device-1",
        "192.0.2.1",
        task_id="run-1",
        secrets=("fixture-private-password",),
    )


def test_policy_is_singleton_versioned_and_empty_means_deny(ssh_governance_database):
    first = governance.get_policy()
    assert first["patterns"] == [".*"] and first["version"] == 1
    op = operation()
    assert op.check_command("display version") == 1
    updated = governance.save_policy([], "admin", expected_version=1)
    assert updated["patterns"] == [] and updated["version"] == 2
    with pytest.raises(DeviceAccessError) as error:
        op.before_command("display version")
    assert error.value.failure_code == "policy_denied"
    with pytest.raises(DeviceAccessError) as error:
        governance.save_policy([".*"], "admin", expected_version=1)
    assert error.value.failure_code == "policy_conflict"
    assert governance.get_policy()["patterns"] == []


def test_fullmatch_checks_current_policy_for_every_command(ssh_governance_database):
    op = operation()
    governance.save_policy([r"display version"], "admin")
    ticket = op.before_command("display version")
    op.after_command(ticket, output="version output")
    for command in (
        "display version extra",
        "prefix display version",
        "display version\nreboot",
        "display\tversion",
        "display\x1b[A version",
        "display version\u2028reboot",
    ):
        with pytest.raises(DeviceAccessError):
            op.before_command(command)
    governance.save_policy([r"show version"], "admin")
    with pytest.raises(DeviceAccessError):
        op.before_command("display version")
    op.before_command("show version")


def test_policy_match_has_real_timeout_and_bounded_input(ssh_governance_database):
    governance.save_policy([r"(a+)+$"], "admin")
    with pytest.raises(DeviceAccessError) as error:
        operation().before_command("a" * 4000 + "!")
    assert error.value.failure_code in {"policy_denied", "policy_match_failed"}
    with pytest.raises(DeviceAccessError) as error:
        operation().before_command("x" * 4097)
    assert error.value.failure_code == "invalid_command"
    for invalid in (["["], ["x" * 513], [".*"] * 101, "not-a-list"):
        with pytest.raises(ValueError):
            governance.save_policy(invalid, "admin")


def test_audit_failures_never_send_or_return_unrecorded_results(
    ssh_governance_database, monkeypatch
):
    sent = []
    op = operation()

    def fail(**kwargs):
        raise DeviceAccessError("audit_unavailable", "audit failed")

    monkeypatch.setattr(governance, "write_event", fail)
    with pytest.raises(DeviceAccessError):
        op.execute("display version", lambda: sent.append(True))
    assert sent == []


def test_policy_failure_never_defaults_to_allow(ssh_governance_database, monkeypatch):
    @contextmanager
    def broken():
        raise RuntimeError("offline")
        yield

    monkeypatch.setattr(governance, "get_db_session", broken)
    with pytest.raises(DeviceAccessError) as error:
        operation().before_command("display version")
    assert error.value.failure_code == "policy_unavailable"


def test_ssh_audit_is_redacted_owner_filtered_and_bounded(ssh_governance_database):
    op = operation()
    op.event("connected", credential_changed=True)
    ticket = op.before_command("display version", phase="setup")
    op.after_command(
        ticket, output="fixture-private-password\npassword cipher hidden\n" + "x" * 300000
    )
    rows = governance.query_audit(actor_id="alice", host="192.0.2.1", task_id="run-1")
    assert rows["total"] == 3
    finished = next(item for item in rows["items"] if item["kind"] == "command_finished")
    assert finished["detail"]["output_truncated"]
    assert finished["detail"]["phase"] == "setup"
    assert "fixture-private-password" not in json.dumps(rows) and "hidden" not in json.dumps(rows)
    connected = next(item for item in rows["items"] if item["kind"] == "connected")
    assert connected["detail"]["credential_changed"] is True
    assert governance.query_audit(actor_id="bob")["total"] == 0
    assert governance.query_audit(session_id=op.session_id, kind="command_finished")["total"] == 1


def test_sqlite_audit_timestamp_is_serialized_as_utc(ssh_governance_database):
    identifier = governance.write_event(actor_id="alice", source_app="test", kind="connected")
    with ssh_governance_database() as session:
        session.get(AuditLogRecord, identifier).created_at = datetime(2026, 9, 30, 2, 54, 7)
    item = governance.query_audit()["items"][0]
    assert item["created_at"] == "2026-09-30T02:54:07+00:00"


def test_postgres_audit_uses_database_timezone_instead_of_assuming_utc(monkeypatch):
    from sqlalchemy.dialects import postgresql

    # PostgreSQL stores the aware writer's instant as local naive time in the
    # shared timestamp column; its projected AT TIME ZONE value is aware.
    local_time = datetime(2026, 9, 30, 10, 54, 7)
    aware_time = local_time.replace(tzinfo=timezone(timedelta(hours=8)))
    row = SimpleNamespace(
        id="audit-timestamp", user_id="alice", action="ssh.connected",
        status="success", created_at=local_time, detail={},
    )
    statements = []

    class ReadSession:
        def scalar(self, statement):
            return 1

        def get_bind(self):
            return SimpleNamespace(dialect=postgresql.dialect())

        def execute(self, statement):
            statements.append(str(statement.compile(
                dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True},
            )))
            return [(row, aware_time)]

    @contextmanager
    def sessions():
        yield ReadSession()

    monkeypatch.setattr(governance, "get_db_session", sessions)
    timestamp = governance.query_audit()["items"][0]["created_at"]
    assert "created_at AT TIME ZONE current_setting('TimeZone')" in statements[0]
    assert datetime.fromisoformat(timestamp).astimezone(timezone.utc) == datetime(
        2026, 9, 30, 2, 54, 7, tzinfo=timezone.utc,
    )
    assert timestamp == "2026-09-30T10:54:07+08:00"


def test_policy_and_audit_rollback_together(ssh_governance_database, monkeypatch):
    def fail(**kwargs):
        raise DeviceAccessError("audit_unavailable", "failed")

    monkeypatch.setattr(governance, "write_event", fail)
    with pytest.raises(DeviceAccessError):
        governance.save_policy([], "admin")
    assert governance.get_policy()["version"] == 1


def test_management_event_uses_callers_transaction(ssh_governance_database):
    with pytest.raises(RuntimeError):
        with ssh_governance_database() as session:
            governance.write_event(
                actor_id="alice",
                source_app="device-access",
                kind="session_saved",
                detail={"credential_changed": True},
                session=session,
            )
            raise RuntimeError("rollback")
    assert governance.query_audit()["total"] == 0


def test_session_policy_change_revokes_command_without_reconnecting(ssh_governance_database):
    from tests.unit.test_device_access_sessions import harness

    # The public service remains governed even with a transport test double.
    fixture = harness.__wrapped__()
    with fixture.open() as session:
        assert session.run("display version").execution_status == "succeeded"
        governance.save_policy([], "admin")
        denied = session.run("display version")
        assert denied.failure_code == "policy_denied"
    assert len(fixture.sent) == 1


def test_old_classification_still_denies_config_with_default_global_policy(ssh_governance_database):
    from tests.unit.test_device_access_sessions import harness

    fixture = harness.__wrapped__()
    with fixture.open() as session:
        denied = session.run("system-view")
        assert denied.policy_level == "L4" and denied.execution_status == "denied"
    assert not fixture.sent
    assert governance.query_audit(kind="command_denied")["total"] == 1


def test_missing_seed_does_not_silently_recreate_allow_all(ssh_governance_database):
    with ssh_governance_database() as session:
        session.delete(session.get(SSHPolicyRecord, 1))
    with pytest.raises(DeviceAccessError):
        governance.get_policy()


@pytest.mark.parametrize("driver_name", ["paramiko", "netmiko"])
def test_real_driver_setup_is_rejected_before_any_command_send(
    ssh_governance_database,
    tmp_path,
    driver_name,
):
    from src.platform.device_access.contracts import ConnectionProfile, Target
    from src.platform.device_access.drivers.netmiko_driver import NetmikoDriver
    from src.platform.device_access.drivers.paramiko_driver import ParamikoDriver
    from tests.unit.test_device_access_ssh import ssh_server

    governance.save_policy([r"display .*"], "admin")
    with ssh_server(tmp_path) as (port, known, commands, _):
        driver = ParamikoDriver() if driver_name == "paramiko" else NetmikoDriver("comware")
        with pytest.raises(DeviceAccessError) as error:
            driver.connect(
                Target("switch", "127.0.0.1"),
                ConnectionProfile(
                    "profile",
                    "operator",
                    "password",
                    port=port,
                    known_hosts_file=str(known),
                    platform="comware",
                ),
            )
        assert error.value.failure_code == "policy_denied"
        assert not [command for command in commands if command]
    denied = governance.query_audit(kind="command_denied")["items"]
    assert len(denied) == 1 and denied[0]["detail"]["phase"] == "setup"


def test_platform_migration_seeds_exact_default_and_enforces_singleton(tmp_path):
    import importlib.util
    from pathlib import Path

    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import create_engine, text
    from sqlalchemy.exc import IntegrityError

    location = Path(__file__).parents[2] / "migrations/versions/0107_ssh_governance.py"
    spec = importlib.util.spec_from_file_location("ssh_governance_migration", location)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = create_engine(f"sqlite:///{tmp_path / 'migration.sqlite'}")
    with engine.begin() as connection:
        migration.op = Operations(MigrationContext.configure(connection))
        migration.upgrade()
        assert connection.execute(
            text("SELECT patterns, version FROM netops_ssh_policy")
        ).one() == ('[".*"]', 1)
        with pytest.raises(IntegrityError):
            connection.execute(
                text("INSERT INTO netops_ssh_policy VALUES (2, '[]', 1, 'test', CURRENT_TIMESTAMP)")
            )
    engine.dispose()


@pytest.mark.parametrize("mode", ["batch", "session"])
@pytest.mark.parametrize("partial", [False, True])
def test_resolved_credentials_are_redacted_from_business_results_and_legacy_audit(
    ssh_governance_database,
    tmp_path,
    monkeypatch,
    mode,
    partial,
):
    from dataclasses import asdict

    from src.platform.device_access import AccessContext, AccessGrant, BatchRequest, Command
    from src.platform.device_access.contracts import ConnectionProfile, Target
    from src.platform.device_access.service import DeviceAccessService
    from tests.unit.test_device_access_ssh import LocalAdmission, ssh_server

    secret = "resolved-fixture-secret-123"
    monkeypatch.setattr(
        "src.platform.inventory.credentials.CredentialResolver.resolve_password",
        lambda self, ref: secret,
    )
    reply = "echo " + secret + ("\n--More--" if partial else "")
    audit = []
    published = []
    with ssh_server(tmp_path, password=secret, responses={"display version": reply}) as (
        port,
        known,
        _,
        _,
    ):
        profile = ConnectionProfile(
            "profile",
            "operator",
            credential_ref="env:TEST_ONLY",
            port=port,
            known_hosts_file=str(known),
            platform="comware",
        )
        access = DeviceAccessService(
            target_resolver=lambda _: Target("switch", "127.0.0.1"),
            profile_resolver=lambda _: profile,
            admission=LocalAdmission(),
        )
        if mode == "batch":
            result = access.execute_batch(
                AccessContext(
                    "alice",
                    "topology",
                    (
                        AccessGrant(
                            "switch",
                            "profile",
                            frozenset({"display version"}),
                        ),
                    ),
                ),
                BatchRequest("switch", "profile", (Command("version", "display version"),)),
                on_result=lambda item: published.append(asdict(item)),
            ).results[0]
        else:
            with access.open_session(
                AccessContext(
                    "alice",
                    "device-access",
                    (
                        AccessGrant(
                            "switch",
                            "profile",
                            action="device.session",
                        ),
                    ),
                ),
                "switch",
                connection_profile_id="profile",
                platform="comware",
                purpose="test",
                audit=lambda event: audit.append(event),
            ) as session:
                result = session.run("display version")
        assert profile.password is None
    assert "[redacted]" in result.output and secret not in result.output
    assert result.output_complete is (not partial)
    assert secret not in json.dumps(audit + published)
    assert secret not in json.dumps(governance.query_audit())
