"""Real text comparison, platform session, policy and audit against isolated fixtures."""

import hashlib
import json
from collections import OrderedDict
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from src.apps.device_access.backend import baseline_compare, diagnosis, live_outputs, service
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
    # This turn's live reads are process-wide; every test starts without any.
    monkeypatch.setattr(live_outputs, "_items", OrderedDict())
    monkeypatch.setattr(live_outputs, "_size", 0)
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
        f"{HOST}：对比 2 条基线命令，1 条有变化（{COMMAND} 文本 +1 -1 行），1 条无变化"
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
def test_device_output_stays_raw_and_only_the_login_secret_is_hidden(
    config_lab, ssh_log_storage, failed
):
    # Owner, 2026-10-02: collected data is never masked by keyword. Only the
    # credential the platform logged in with stays out of records.
    sensitive = " ospf authentication-mode hmac-sha-256 1 cipher config-key"
    config_lab.output = CURRENT.replace(" port access vlan 200", sensitive)
    config_lab.fail_command = failed
    result = compare(config_lab)
    with config_lab.database() as db:
        local = [row.payload for row in db.scalars(select(SessionAudit))]
    platform = governance.query_audit()["items"]
    stored = b"".join(ssh_log_storage.objects.values()).decode("utf-8")
    for serialized in (json.dumps(result), json.dumps(local), json.dumps(platform), stored):
        assert "fixture-login" not in serialized
    assert "cipher config-key" in stored
    assert config_lab.connects == config_lab.closes == 1
    if failed:
        assert rows(result)[COMMAND]["status"] == "unavailable"
    else:
        assert "+" + sensitive in rows(result)[COMMAND]["diff"]


def test_an_authentication_line_change_shows_as_written(config_lab):
    config_lab.output = BASELINE.replace(
        " port access vlan 100",
        " port access vlan 100\n ospf authentication-mode md5 1 cipher config-key",
    )
    config = rows(compare(config_lab))[COMMAND]
    assert config["status"] == "changed" and config["added"] == 1 and config["removed"] == 0
    assert config["diff"][0].endswith("@@ interface GigabitEthernet3/0")
    assert "+ ospf authentication-mode md5 1 cipher config-key" in config["diff"]
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


REAL_ROUTES = (
    "Destinations : 4\tRoutes : 4\n\n"
    "Destination/Mask   Proto   Pre Cost        NextHop         Interface\n"
    "2.2.2.0/24         Direct  0   0           2.2.2.1         GE2/0\n"
    "4.4.4.4/32         O_INTRA 10  1           2.2.2.2         GE2/0\n"
    "10.10.1.0/24       Direct  0   0           10.10.1.2       GE1/0\n"
    "192.168.1.0/24     O_INTRA 10  2           2.2.2.2         GE2/0\n"
)
OSPF_GONE = REAL_ROUTES.replace(
    "4.4.4.4/32         O_INTRA 10  1           2.2.2.2         GE2/0\n", ""
).replace("192.168.1.0/24     O_INTRA 10  2           2.2.2.2         GE2/0\n", "")


def retext(lab, identifier, text):
    with lab.database() as db:
        row = db.get(CommandSnapshot, identifier)
        row.raw_text, row.raw_text_hash = text, hashlib.sha256(text.encode()).hexdigest()


def lookup(lab, command, match, turn="diagnosis-config-test"):
    arguments = {"host": HOST, "command": command, "match": match, "turn_id": turn}
    return registry.invoke("device_access__compare_rows", arguments, actor_id="alice")


def test_tables_are_compared_by_entry_and_summarised(config_lab):
    retext(config_lab, "baseline-routes", REAL_ROUTES)
    config_lab.outputs[(HOST, ROUTES)] = OSPF_GONE
    result = compare(config_lab)
    routes = rows(result)[ROUTES]
    [table] = routes["tables"]
    assert table["key"] == ["Destination/Mask"] and table["rows"] == [4, 2]
    assert table["removed"]["count"] == 2
    assert "NextHop=2.2.2.2（2/2）" in table["removed"]["mostly"]
    assert table["summary"].startswith("表 Destination/Mask 等 6 列，4→2 条：消失 2")
    assert f"{ROUTES} 消失 2）" in result["summary"][0]


def test_rows_are_looked_up_afterwards_without_logging_in(config_lab):
    retext(config_lab, "baseline-routes", REAL_ROUTES)
    config_lab.outputs[(HOST, ROUTES)] = OSPF_GONE
    compare(config_lab)
    connects = config_lab.connects
    found = lookup(config_lab, ROUTES, "192.168.1")
    [table] = found["tables"]
    assert table["entries"] == [{
        "baseline": "192.168.1.0/24 | O_INTRA | 10 | 2 | 2.2.2.2 | GE2/0",
        "current": None,
        "status": "only_baseline",
    }]
    assert found["current"]["command"] == ROUTES
    # Configuration is searched by section, baseline beside current.
    [section] = lookup(config_lab, COMMAND, "GigabitEthernet3/0")["sections"]
    assert section["baseline"][1] == " port access vlan 100"
    assert section["current"][1] == " port access vlan 200"
    assert config_lab.connects == connects


def test_a_large_table_is_looked_up_rather_than_read_whole(config_lab, monkeypatch):
    big = REAL_ROUTES + "".join(
        f"{f'10.{n // 256}.{n % 256}.0/24':<19}O_INTRA 10  2           2.2.2.2         GE2/0\n"
        for n in range(baseline_compare.LARGE_LINES)
    )
    retext(config_lab, "baseline-routes", big)
    result = compare(config_lab)
    routes = rows(result)[ROUTES]
    assert "large_table_not_read_live" in routes["quality_gaps"]
    assert "compare_rows" in routes["note"]
    assert ROUTES not in config_lab.sent
    assert "1 张大表没有整表读取" in result["summary"][0]
    # Baseline rows only, until a filtered live read exists ...
    found = lookup(config_lab, ROUTES, "192.168.1.0/24")
    assert found["current"] is None and "note" in found
    assert found["tables"][0]["entries"][0]["status"] == "only_baseline"
    # ... then the filtered read lines up with the full baseline by its header.
    config_lab.outputs[(HOST, "display ip routing-table 192.168.1.0 24")] = (
        "Summary count : 1\n\n"
        "Destination/Mask   Proto   Pre Cost        NextHop         Interface\n"
        "192.168.1.0/24     O_INTRA 10  20          2.2.2.2         GE2/0\n"
    )
    diagnosis.run(
        "alice", HOST, "comware", ["display ip routing-table 192.168.1.0 24"], "查一条路由",
        "diagnosis-config-test",
    )
    entry = lookup(config_lab, ROUTES, "192.168.1.0/24")["tables"][0]["entries"][0]
    assert entry["status"] == "changed" and entry["changed"] == ["Cost"]


def test_a_lookup_with_nothing_to_look_in_says_so(config_lab):
    with pytest.raises(ValueError, match="没有这条命令的巡检基线"):
        lookup(config_lab, "display version", "VSR")
