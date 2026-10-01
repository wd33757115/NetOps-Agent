"""Real text comparison, platform session, policy and audit against isolated fixtures."""

import hashlib
import json
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from src.apps.device_access.backend import baseline_compare, diagnosis, service
from src.apps.device_access.backend.models import SessionAudit
from src.apps.inspection.backend import config_baseline, diagnostics
from src.infrastructure.db.models import CommandSnapshot, NetworkDevice, PatrolRun
from src.platform.device_access import DeviceAccessService, governance
from src.platform.device_access.contracts import ConnectionProfile, DeviceAccessError, Target
from src.platform.device_access.errors import CommandExecutionError
from src.platform.kernel.registry import discover
from src.platform.tools import registry

HOST, OTHER = "192.0.2.1", "192.0.2.2"
NAMES = {HOST: "VSR2", OTHER: "VSR1"}
COMMAND = "display current-configuration"
ROUTES = "display ip routing-table"
BASELINE = "#\ninterface GigabitEthernet3/0\n port access vlan 100\n#\nreturn\n"
CURRENT = "#\ninterface GigabitEthernet3/0\n port access vlan 200\n#\nreturn\n"
ROUTE_TABLE = (
    "Destination/Mask   Proto   Pre Cost  NextHop  Interface\n"
    "1.1.1.0/24  O_INTRA 10 2 2.2.2.1 GE2/0\n"
)
TAKEN = datetime(2026, 9, 30, 6, 30, tzinfo=timezone.utc)


def snapshot(db, identifier, host, command, text, observed_at=TAKEN, **extra):
    db.add(
        CommandSnapshot(
            id=identifier,
            run_id="baseline-run",
            inventory_device_id=NAMES[host].lower(),
            device_id=NAMES[host],
            device_name=NAMES[host],
            management_ip=host,
            execution_item_id=f"item-{identifier}",
            command=command,
            canonical_command=command,
            observed_at=observed_at,
            parse_status="not_required",
            output_status=extra.pop("output_status", "data"),
            snapshot_kind="full",
            raw_text=text,
            raw_text_hash=hashlib.sha256(text.encode()).hexdigest(),
            **extra,
        )
    )


@pytest.fixture
def config_lab(tmp_path, monkeypatch):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'config-baseline.sqlite'}",
        connect_args={"check_same_thread": False},  # devices are compared in parallel
    )
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
        # (host, command) -> live output; anything else answers CURRENT.
        outputs={(HOST, ROUTES): ROUTE_TABLE},
        output=CURRENT,
        fail_connect=False,
        fail_command=False,
        grants={"device-access", "inspection", "assistant"},
        checks=[],
        database=database,
    )
    with database() as db:
        for host, name in NAMES.items():
            db.add(
                NetworkDevice(
                    id=name.lower(), device_name=name, management_ip=host, status="active"
                )
            )
        db.add(
            PatrolRun(
                id="baseline-run",
                logical_run_id="baseline-run",
                mode="offline",
                source="upload",
                status="completed",
                started_at=TAKEN,
            )
        )
        db.flush()
        snapshot(db, "baseline-config", HOST, COMMAND, BASELINE)
        snapshot(db, "baseline-routes", HOST, ROUTES, ROUTE_TABLE)
    monkeypatch.setattr(config_baseline, "get_db_session", database)
    monkeypatch.setattr(diagnostics, "get_db_session", database)
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
        diagnosis, "login_for", lambda owner, host: (22, "operator", "fixture-login", NAMES[host])
    )

    class Driver:
        name, version = "fixture", "1"

        def connect(self, target, profile):
            state.connects += 1
            assert profile.connection_attempts == 1
            if state.fail_connect:
                raise DeviceAccessError("authentication_failed", "fixture login failed")
            return SimpleNamespace(
                send_command=lambda command, **kwargs: self.send(target.host, command),
                find_prompt=lambda: f"<{NAMES[target.host]}>",
                disconnect=self.disconnect,
            )

        def send(self, host, command):
            state.sent.append(command)
            output = state.outputs.get((host, command), state.output)
            if state.fail_command:
                raise CommandExecutionError("read_timeout", "fixture timeout", output)
            return output

        def disconnect(self):
            state.closes += 1

    class Admission:
        @contextmanager
        def hold(self, *args):
            yield lambda: None

    def access(ref, host, port, username, password, **kwargs):
        assert kwargs["device_name"] == NAMES[host]
        return DeviceAccessService(
            target_resolver=lambda _: Target(ref, host, display_name=NAMES[host]),
            profile_resolver=lambda _: ConnectionProfile(ref, username, password, port=port),
            driver=Driver(),
            admission=Admission(),
        )

    monkeypatch.setattr(service, "access", access)
    state.body = baseline_compare.CompareBaseline(
        hosts=[HOST],
        platforms={HOST: "comware", OTHER: "comware"},
        purpose="核对接口配置",
        turn_id="diagnosis-config-test",
    )
    yield state
    engine.dispose()


def compare(lab, **changes):
    body = lab.body.model_dump() | changes
    return registry.invoke("device_access__compare_baseline", body, actor_id="alice")


def rows(result, host=HOST):
    [device] = [d for d in result["devices"] if d["host"] == host]
    return {row["command"]: row for row in device["commands"]}


def test_every_baseline_command_runs_in_one_session_and_only_diffs_return(
    config_lab, ssh_log_storage
):
    result = compare(config_lab)
    config, routes = rows(result)[COMMAND], rows(result)[ROUTES]
    assert config["status"] == "changed" and routes["status"] == "unchanged"
    assert config["baseline"]["snapshot_id"] == "baseline-config"
    assert "- port access vlan 100" in config["diff"] and "+ port access vlan 200" in config["diff"]
    assert "return" not in config["diff"]  # outside the one-line context
    assert "diff" not in routes
    assert config["added"] == config["removed"] == 1
    # One summary line per device names what changed; the scope is spelled out.
    assert result["summary"] == [
        f"{HOST}：对比 2 条基线命令，1 条有变化（{COMMAND} +1 -1），1 条无变化"
    ]
    assert "没列出的设备" in result["scope"]
    # Both sides in Beijing time with the gap spelled out; mixed offsets were once misread.
    assert config["baseline"]["observed_at"] == "2026-09-30T14:30:00+08:00"
    assert result["observed_at"].endswith("+08:00")
    assert isinstance(config["baseline_age_seconds"], int)
    assert config["evidence_ref"].startswith("device-access:audit:")
    assert sorted(config_lab.sent) == sorted([COMMAND, ROUTES])
    assert config_lab.connects == config_lab.closes == 1
    audit = governance.query_audit(task_id="diagnosis-config-test")["items"]
    finished = [row for row in audit if row["kind"] == "command_finished"]
    assert sorted(row["detail"]["command"] for row in finished) == sorted([COMMAND, ROUTES])
    assert any(row["kind"] == "disconnected" for row in audit)
    assert len(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2).encode()) < 24000


def test_several_devices_are_compared_in_one_call(config_lab, ssh_log_storage):
    with config_lab.database() as db:
        snapshot(db, "other-config", OTHER, COMMAND, BASELINE)
    config_lab.outputs[(OTHER, COMMAND)] = BASELINE
    result = compare(config_lab, hosts=[HOST, OTHER])
    assert [device["host"] for device in result["devices"]] == [HOST, OTHER]
    assert rows(result, OTHER)[COMMAND]["status"] == "unchanged"
    assert result["summary"][1] == f"{OTHER}：对比 1 条基线命令，0 条有变化，1 条无变化"
    assert config_lab.connects == config_lab.closes == 2


def test_the_newest_retained_snapshot_of_each_command_is_the_baseline(config_lab):
    with config_lab.database() as db:
        older = BASELINE.replace("vlan 100", "vlan 300")
        snapshot(db, "older-config", HOST, COMMAND, older, observed_at=TAKEN - timedelta(days=1))
    assert rows(compare(config_lab))[COMMAND]["baseline"]["snapshot_id"] == "baseline-config"


@pytest.mark.parametrize("invalid", ["foreign_host", "hash", "truncated"])
def test_invalid_baseline_never_resolves_or_opens_ssh(config_lab, monkeypatch, invalid):
    with config_lab.database() as db:
        db.delete(db.get(CommandSnapshot, "baseline-routes"))
        snapshot_row = db.get(CommandSnapshot, "baseline-config")
        if invalid == "foreign_host":
            db.get(NetworkDevice, "vsr2").management_ip = "192.0.2.9"
        elif invalid == "hash":
            snapshot_row.raw_text_hash = "0" * 64
        else:
            snapshot_row.output_status = "truncated"
    monkeypatch.setattr(diagnosis, "login_for", lambda *args: pytest.fail("resolved SSH login"))
    result = compare(config_lab)
    [device] = result["devices"]
    assert device["login"] == "not_needed"
    if invalid == "truncated":
        # An unusable snapshot is not a baseline at all.
        assert device["commands"] == [] and device["quality_gaps"] == ["no_retained_baseline"]
        assert result["summary"] == [f"{HOST}：没有可用的巡检基线，未对比"]
    else:
        [row] = device["commands"]
        assert row["status"] == "unavailable" and row["quality_gaps"]
    assert not config_lab.connects and not config_lab.sent


def test_inspection_grant_and_platform_whitelist_are_enforced(config_lab):
    config_lab.grants.remove("inspection")
    with pytest.raises(HTTPException) as denied:
        compare(config_lab)
    assert denied.value.status_code == 403 and config_lab.connects == 0
    config_lab.grants.add("inspection")
    governance.save_policy([r"display version"], "admin")
    result = compare(config_lab)
    config = rows(result)[COMMAND]
    assert config["status"] == "unavailable" and not config_lab.sent
    assert config["failure_code"] == "policy_denied"
    assert config_lab.connects == config_lab.closes == 1
    assert governance.query_audit(kind="command_denied")["total"] >= 1


@pytest.mark.parametrize("output", ["", "\r\n<VSR2>", "% Unrecognized command found at '^'"])
def test_empty_or_error_output_is_never_a_change(config_lab, output):
    config_lab.output = output
    config = rows(compare(config_lab))[COMMAND]
    assert config["status"] == "unavailable" and "diff" not in config
    assert any(gap.startswith("live_") for gap in config["quality_gaps"])
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
        assert rows(result)[COMMAND]["status"] == "unavailable"


def test_a_credential_line_change_is_compared_not_hidden(config_lab):
    """Every line takes part: an added authentication line shows where it was added."""
    config_lab.output = BASELINE.replace(
        " port access vlan 100",
        " port access vlan 100\n ospf authentication-mode md5 1 cipher hidden-config-key",
    )
    config = rows(compare(config_lab))[COMMAND]
    assert config["status"] == "changed" and config["added"] == 1 and config["removed"] == 0
    assert config["diff"][0].endswith("@@ interface GigabitEthernet3/0")
    assert "+[redacted credential line]" in config["diff"]
    assert "quality_gaps" not in config or not config["quality_gaps"]


def test_login_failure_is_not_retried(config_lab):
    config_lab.fail_connect = True
    result = compare(config_lab)
    [device] = result["devices"]
    assert device["login"] == "failed" and device["failure_code"] == "authentication_failed"
    assert all(row["status"] == "unavailable" for row in device["commands"])
    assert result["summary"][0].endswith("（登录失败）")
    assert config_lab.connects == 1 and not config_lab.sent


def test_large_diff_stays_within_the_evidence_budget_and_says_so(config_lab):
    config_lab.output = (
        "#\ninterface GigabitEthernet3/0\n"
        + "\n".join(f" description fixture-{index}-" + "x" * 100 for index in range(300))
        + "\n#\n"
    )
    config = rows(compare(config_lab))[COMMAND]
    assert config["status"] == "changed" and config["diff_truncated"]
    assert config["added"] == 300  # the count stays whole when the lines are cut
    assert config_lab.connects == config_lab.closes == 1


@pytest.mark.parametrize(
    "extra", [{"command": "reboot"}, {"configuration": "arbitrary text"}, {"snapshot_id": "x"}]
)
def test_schema_accepts_no_model_authored_command_or_config(config_lab, extra):
    with pytest.raises(ValueError):
        baseline_compare.CompareBaseline.model_validate(config_lab.body.model_dump() | extra)


def test_every_host_needs_a_platform_from_the_server(config_lab):
    with pytest.raises(ValueError):
        baseline_compare.CompareBaseline.model_validate(
            config_lab.body.model_dump() | {"hosts": [HOST, "192.0.2.77"]}
        )


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
