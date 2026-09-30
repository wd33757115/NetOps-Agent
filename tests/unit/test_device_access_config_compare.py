"""Real scoped comparison, platform session, policy and audit against isolated fixtures."""

import hashlib
import json
from contextlib import contextmanager
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from src.apps.device_access.backend import config_compare, diagnosis, service
from src.apps.device_access.backend.models import SessionAudit
from src.apps.inspection.backend import config_baseline
from src.infrastructure.db.models import CommandSnapshot, NetworkDevice, PatrolRun
from src.platform.device_access import DeviceAccessService, governance
from src.platform.device_access.contracts import ConnectionProfile, DeviceAccessError, Target
from src.platform.device_access.errors import CommandExecutionError
from src.platform.kernel.registry import discover
from src.platform.tools import registry

HOST = "192.0.2.1"
COMMAND = "display current-configuration interface GigabitEthernet3/0"
BASELINE = "#\ninterface GigabitEthernet3/0\n port access vlan 100\n#\nreturn\n"
CURRENT = "#\ninterface GigabitEthernet3/0\n port access vlan 200\n#\nreturn\n"


@pytest.fixture
def config_lab(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'config-baseline.sqlite'}")
    for model in (NetworkDevice, PatrolRun, CommandSnapshot, SessionAudit):
        model.__table__.create(engine)

    @contextmanager
    def database():
        with Session(engine) as db, db.begin():
            yield db

    state = SimpleNamespace(
        connects=0,
        closes=0,
        sent=[],
        output=CURRENT,
        fail_connect=False,
        fail_command=False,
        grants={"device-access", "inspection", "assistant"},
        checks=[],
        database=database,
    )
    with database() as db:
        timestamp = datetime(2026, 9, 30, 6, 30, tzinfo=timezone.utc)
        db.add(NetworkDevice(id="vsr2", device_name="VSR2", management_ip=HOST, status="active"))
        db.add(
            PatrolRun(
                id="baseline-run",
                logical_run_id="baseline-run",
                mode="offline",
                source="upload",
                status="completed",
                started_at=timestamp,
            )
        )
        db.flush()
        db.add(
            CommandSnapshot(
                id="baseline-config",
                run_id="baseline-run",
                inventory_device_id="vsr2",
                device_id="VSR2",
                device_name="VSR2",
                execution_item_id="item-config",
                command="display current-configuration",
                canonical_command="display current-configuration",
                observed_at=timestamp,
                parse_status="not_required",
                output_status="data",
                snapshot_kind="full",
                raw_text=BASELINE,
                raw_text_hash=hashlib.sha256(BASELINE.encode()).hexdigest(),
            )
        )
    monkeypatch.setattr(config_baseline, "get_db_session", database)
    monkeypatch.setattr(service, "sessions", database)
    manifests = discover()
    monkeypatch.setattr(
        registry,
        "discover",
        lambda: {app: manifests[app] for app in ("inspection", "device-access")},
    )

    def authorize(owner, app):
        state.checks.append((owner, app))
        if owner != "alice" or app not in state.grants:
            raise HTTPException(403, "App grant revoked")
        return SimpleNamespace(user_id=owner, can_execute_skills=lambda: True)

    monkeypatch.setattr(registry, "authorize_worker", authorize)
    state.authorize = authorize
    monkeypatch.setattr(service, "authorize_worker", authorize)
    monkeypatch.setattr("src.apps.assistant.backend.workspace.authorize_worker", authorize)
    monkeypatch.setattr(
        diagnosis, "login_for", lambda *args: (22, "operator", "fixture-login", "VSR2")
    )

    class Driver:
        name, version = "fixture", "1"

        def connect(self, target, profile):
            state.connects += 1
            assert profile.connection_attempts == 1
            if state.fail_connect:
                raise DeviceAccessError("authentication_failed", "fixture login failed")
            return SimpleNamespace(
                send_command=self.send, find_prompt=lambda: "<VSR2>", disconnect=self.disconnect
            )

        def send(self, command, **kwargs):
            state.sent.append(command)
            if state.fail_command:
                raise CommandExecutionError("read_timeout", "fixture timeout", state.output)
            return state.output

        def disconnect(self):
            state.closes += 1

    class Admission:
        @contextmanager
        def hold(self, *args):
            yield lambda: None

    def access(ref, host, port, username, password, **kwargs):
        assert host == HOST and kwargs["device_name"] == "VSR2"
        return DeviceAccessService(
            target_resolver=lambda _: Target(ref, host, display_name="VSR2"),
            profile_resolver=lambda _: ConnectionProfile(ref, username, password, port=port),
            driver=Driver(),
            admission=Admission(),
        )

    monkeypatch.setattr(service, "access", access)
    state.body = config_compare.CompareConfig(
        host=HOST,
        platform="comware",
        section="interface",
        target="GigabitEthernet3/0",
        snapshot_id="baseline-config",
        purpose="核对接口配置",
        turn_id="diagnosis-config-test",
    )
    yield state
    engine.dispose()


def compare(lab):
    return registry.invoke("device_access__compare_config", lab.body.model_dump(), actor_id="alice")


def test_fixed_command_reads_real_baseline_and_audits_one_closed_session(
    config_lab, ssh_log_storage
):
    result = compare(config_lab)
    assert result["status"] == "changed"
    assert result["baseline"]["snapshot_id"] == "baseline-config"
    assert result["baseline"]["run_mode"] == "offline"
    assert result["baseline"]["fragment"]["complete"]
    assert " port access vlan 200" in result["current"]["fragment"]["text"]
    assert result["comparison"]["added_count"] == result["comparison"]["removed_count"] == 1
    assert config_lab.sent == [COMMAND] and config_lab.connects == config_lab.closes == 1
    assert result["current"]["policy_level"] == "L1"
    assert result["current"]["evidence_ref"].startswith("device-access:audit:")
    audit = governance.query_audit(task_id="diagnosis-config-test")["items"]
    finished = [row for row in audit if row["kind"] == "command_finished"]
    assert len(finished) == 1 and finished[0]["detail"]["command"] == COMMAND
    assert finished[0]["detail"]["output_sha256"] == result["current"]["output_sha256"]
    assert any(row["kind"] == "disconnected" for row in audit)
    assert len(ssh_log_storage.objects) == 1
    assert len(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2).encode()) < 24000


@pytest.mark.parametrize("invalid", ["foreign_host", "hash", "truncated", "missing_block"])
def test_invalid_baseline_never_resolves_or_opens_ssh(config_lab, monkeypatch, invalid):
    with config_lab.database() as db:
        snapshot = db.get(CommandSnapshot, "baseline-config")
        if invalid == "foreign_host":
            db.get(NetworkDevice, "vsr2").management_ip = "192.0.2.2"
        elif invalid == "hash":
            snapshot.raw_text_hash = "0" * 64
        elif invalid == "truncated":
            snapshot.output_status = "truncated"
        else:
            snapshot.raw_text = "#\ninterface GigabitEthernet2/0\n#\n"
            snapshot.raw_text_hash = hashlib.sha256(snapshot.raw_text.encode()).hexdigest()
    monkeypatch.setattr(diagnosis, "login_for", lambda *args: pytest.fail("resolved SSH login"))
    if invalid == "foreign_host":
        with pytest.raises(HTTPException):
            compare(config_lab)
    else:
        result = compare(config_lab)
        assert result["status"] == "unavailable" and result["current"] is None
    assert not config_lab.connects and not config_lab.sent


def test_inspection_grant_and_platform_whitelist_are_enforced(config_lab):
    config_lab.grants.remove("inspection")
    with pytest.raises(HTTPException) as denied:
        compare(config_lab)
    assert denied.value.status_code == 403 and config_lab.connects == 0
    config_lab.grants.add("inspection")
    governance.save_policy([r"display version"], "admin")
    result = compare(config_lab)
    assert result["status"] == "unavailable" and not config_lab.sent
    assert result["current"]["failure_code"] == "policy_denied"
    assert config_lab.connects == config_lab.closes == 1
    assert governance.query_audit(kind="command_denied")["total"] == 1


@pytest.mark.parametrize("output", ["", CURRENT.split("\n#\nreturn")[0], "% Unrecognized command"])
def test_incomplete_live_section_never_reports_unchanged(config_lab, output):
    config_lab.output = output
    result = compare(config_lab)
    assert result["status"] == "unavailable" and not result["current"]["fragment"]["complete"]
    assert config_lab.connects == config_lab.closes == 1


@pytest.mark.parametrize("failed", [False, True])
def test_credentials_are_hidden_before_all_audits_logs_and_return(
    config_lab, ssh_log_storage, failed
):
    sensitive = " ospf authentication-mode hmac-sha-256 1 cipher hidden-config-key"
    config_lab.output = CURRENT.replace(" port access vlan 200", sensitive)
    config_lab.fail_command = failed
    result = compare(config_lab)
    with config_lab.database() as db:
        local = [row.payload for row in db.scalars(select(SessionAudit))]
    platform = governance.query_audit()["items"]
    stored = b"".join(ssh_log_storage.objects.values()).decode("utf-8")
    for serialized in (json.dumps(result), json.dumps(local), json.dumps(platform), stored):
        assert "hidden-config-key" not in serialized
    assert "[redacted credential line]" in stored
    assert config_lab.connects == config_lab.closes == 1
    if failed:
        assert result["status"] == "unavailable"


def test_login_failure_is_not_retried(config_lab):
    config_lab.fail_connect = True
    result = compare(config_lab)
    assert result["status"] == "unavailable" and result["failure_code"] == "authentication_failed"
    assert config_lab.connects == 1 and not config_lab.sent


def test_identical_hidden_credentials_never_claim_complete_equality(config_lab):
    text = BASELINE.replace(
        " port access vlan 100", " ospf authentication-mode md5 1 cipher hidden-config-key"
    )
    with config_lab.database() as db:
        snapshot = db.get(CommandSnapshot, "baseline-config")
        snapshot.raw_text = text
        snapshot.raw_text_hash = hashlib.sha256(text.encode()).hexdigest()
    config_lab.output = text
    result = compare(config_lab)
    assert result["status"] == "unavailable"
    assert result["comparison"]["added_count"] == result["comparison"]["removed_count"] == 0
    assert "sensitive_lines_not_compared" in result["quality_gaps"]
    assert "hidden-config-key" not in json.dumps(result)


def test_large_live_block_stays_bounded_and_unavailable(config_lab):
    config_lab.output = (
        "#\ninterface GigabitEthernet3/0\n"
        + "\n".join(f" description fixture-{index}-" + "x" * 100 for index in range(90))
        + "\n#\n"
    )
    result = compare(config_lab)
    assert result["status"] == "unavailable" and result["current"]["fragment"]["truncated"]
    assert "section_excerpt_truncated" in result["quality_gaps"]
    assert len(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2).encode()) < 24000
    assert config_lab.connects == config_lab.closes == 1


@pytest.mark.parametrize("extra", [{"command": "reboot"}, {"configuration": "arbitrary text"}])
def test_schema_accepts_no_model_authored_command_or_config(config_lab, extra):
    with pytest.raises(ValueError):
        config_compare.CompareConfig.model_validate(config_lab.body.model_dump() | extra)


@pytest.mark.parametrize(
    "platform,command",
    [
        ("comware", "display CURRENT-CONFIGURATION"),
        ("comware", "dis cur interface GigabitEthernet3/0"),
        ("comware", "display SAV"),
        ("comware", "display th"),
        ("vrp", "display current"),
        ("ios", "show RUN"),
        ("nxos", "show START"),
    ],
)
def test_generic_diagnosis_config_abbreviations_remain_blocked(
    config_lab, monkeypatch, platform, command
):
    monkeypatch.setattr(diagnosis, "login_for", lambda *args: pytest.fail("resolved SSH login"))
    result = diagnosis.run("alice", HOST, platform, [command], "检查", "generic-dump-test")
    assert result["login"] == "not_needed"
    assert result["commands"][0]["status"] == "not_executed"
    assert config_lab.connects == 0


@pytest.mark.parametrize(
    "platform,command",
    [
        ("comware", "display ip routing-table"),
        ("comware", "display interface brief"),
        ("comware", "display clock"),
        ("ios", "show ip route"),
    ],
)
def test_generic_dump_gate_preserves_normal_read_commands(platform, command):
    assert not diagnosis._dump(platform, command)
