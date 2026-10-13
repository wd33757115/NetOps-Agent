"""A field trial runs in the background and saves nothing; saving decides again and switches as a whole."""

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.apps.rule_governance.backend.check_items import adapter_field_service as service_module
from src.apps.rule_governance.backend.check_items import adapter_fields
from src.apps.rule_governance.backend.check_items.adapter_field_service import (
    AdapterFieldService,
    active_trials,
    cancel_active,
    recorded,
    task_key,
)
from src.apps.rule_governance.backend.check_items.adapter_fields import AdapterFieldsError
from src.infrastructure.db.models import AuditLogRecord, Base, ParserControlSetting
from tests.unit.test_adapter_fields import CURRENT, EXTENDED, SAMPLES

ADAPTER, ITEM = "vendor-optics", "interface.optics"
BASE = {"implementation_version_id": "adapter-v2", "check_item_version_id": "item-v3", "template_id": "t1",
        "template_sha256": "a" * 64, "record_set": "optics"}
# What a trial hands back when it found the wavelength and the note in the outputs.
BOTH = {
    "status": "ready", "requested_fields": ["WAVELENGTH", "VENDOR_NOTE"], "sample_count": 2, "record_count": 3,
    "provided_fields": ["WAVELENGTH", "VENDOR_NOTE"], "absent_fields": [], "base": BASE,
    "regression": {"passed": True, "differences": []}, "fields": [], "template_text": EXTENDED,
}
NOTED = {**SAMPLES, "s3": "Eth1/4 rx -4.00 note spare\n"}


@pytest.fixture
def world(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'adapter-fields.db'}")
    Base.metadata.create_all(engine, tables=[ParserControlSetting.__table__, AuditLogRecord.__table__])
    factory = sessionmaker(bind=engine)

    @contextmanager
    def scope():
        session = factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    row = lambda name, **extra: {"canonical_field": name, "value_type": "text", "required": False, **extra}  # noqa: E731
    state = SimpleNamespace(outputs=dict(NOTED), base=dict(BASE), provided=["INTERFACE", "RX_POWER"], scope=scope)

    def context(implementation_id, check_item_id):
        if implementation_id != ADAPTER or check_item_id != ITEM:
            raise AdapterFieldsError("适配器或检查项不存在，或两者没有关联")
        fields = {name: {"source": name} for name in state.provided}
        return {
            "adapter": SimpleNamespace(id=ADAPTER, name="Vendor · show optics", version_id=state.base["implementation_version_id"],
                                       check_item_ids=(ITEM,), adapter_spec={"outputs": [{
                                           "check_item_id": ITEM, "source_record_set": "optics",
                                           "projection": {"fields": fields}}]}),
            "item": SimpleNamespace(id=ITEM, version_id="item-v3", canonical_contract={
                "identity_dimensions": ["INTERFACE"],
                "observations": [row("RX_POWER"), row("WAVELENGTH", description="波长", assessment_roles=["change"]),
                                 row("VENDOR_NOTE")]}),
            "template": SimpleNamespace(id="t1"), "record_set": "optics", "current_text": CURRENT,
            "outputs": dict(state.outputs), "provided": list(state.provided), "base": dict(state.base),
        }

    monkeypatch.setattr(adapter_fields, "extension_context", context)
    monkeypatch.setattr(service_module, "get_db_session", scope)
    return state


def _service(world, trial=None, sent=None, switched=None, monkeypatch=None):
    sent = [] if sent is None else sent
    if switched is not None:
        def switch(db, context, template_text, fields, *, actor, now):
            switched.append({"template_text": template_text, "fields": fields, "actor": actor})
            return {"template_id": "t2", "previous_template_id": "t1", "implementation_version_id": "adapter-v3",
                    "implementation_version": 3, "previous_implementation_version": 2,
                    "runtime_bundle_version_ids": ["bundle-9"]}
        monkeypatch.setattr(service_module, "_switch", switch)
    return AdapterFieldService(world.scope, dispatch=lambda key, token: sent.append((key, token)) or True,
                               trial=trial or (lambda *_: dict(BOTH))), sent


def test_before_any_trial_the_dialog_gets_the_fields_that_can_be_asked_for(world):
    service, _sent = _service(world)

    state = service.get(ADAPTER, ITEM)

    assert state["status"] == "idle" and state["sample_count"] == 3
    # Three fields in the check item, one of them provided: the same count its own page shows.
    assert state["field_count"] == 3 and state["provided_count"] == 1
    assert state["missing_fields"] == [
        {"name": "WAVELENGTH", "description": "波长", "use": "change", "absent": False},
        {"name": "VENDOR_NOTE", "description": "", "use": "evidence", "absent": False},
    ]
    assert state["result"] is None


def test_a_trial_is_queued_for_the_worker_and_asking_twice_does_not_start_two(world):
    service, sent = _service(world)

    first = service.start(ADAPTER, ITEM, fields=["WAVELENGTH"], actor="engineer", actor_user_id="7")
    second = service.start(ADAPTER, ITEM, fields=["VENDOR_NOTE"], actor="someone-else")

    assert first["status"] == "queued" and first["requested_fields"] == ["WAVELENGTH"]
    assert second["status"] == "queued" and second["requested_fields"] == ["WAVELENGTH"]
    assert len(sent) == 1 and sent[0][0] == task_key(ADAPTER, ITEM)
    assert active_trials() == 1


def test_a_trial_cannot_start_without_an_output_to_check_against(world):
    world.outputs = {}
    service, sent = _service(world)

    with pytest.raises(AdapterFieldsError, match="没有样本可用来验证"):
        service.start(ADAPTER, ITEM, fields=None, actor="engineer")
    assert sent == []


def test_a_queue_that_cannot_be_reached_ends_the_trial_instead_of_leaving_it_waiting(world):
    service = AdapterFieldService(world.scope, dispatch=lambda key, token: False)

    state = service.start(ADAPTER, ITEM, fields=None, actor="engineer")

    assert state["status"] == "failed" and "后台任务队列暂时不可用" in state["error"]
    assert active_trials() == 0


def test_the_worker_keeps_the_result_and_the_browser_never_gets_the_template(world):
    found = {**BOTH, "provided_fields": ["WAVELENGTH"], "absent_fields": ["VENDOR_NOTE"]}
    calls = []
    service, sent = _service(world, trial=lambda *args: calls.append(args) or dict(found))
    service.start(ADAPTER, ITEM, fields=None, actor="engineer", actor_user_id="7")
    asked = []

    service.run(*sent[0], guard=asked.append)

    assert calls == [(ADAPTER, ITEM, ["WAVELENGTH", "VENDOR_NOTE"])] and asked == ["7"]
    state = service.get(ADAPTER, ITEM)
    assert state["status"] == "ready" and state["result"]["provided_fields"] == ["WAVELENGTH"]
    assert "template_text" not in state["result"] and state["result"]["stale"] is False
    # What the device did not print is remembered for the adapter, and offered unticked next time.
    assert [(field["name"], field["absent"]) for field in state["missing_fields"]] == [
        ("WAVELENGTH", False), ("VENDOR_NOTE", True)]
    assert recorded(ITEM) == {ADAPTER: {"absent_fields": ["VENDOR_NOTE"], "trial_status": "ready"}}
    assert recorded("another.check_item") == {}


def test_a_stale_delivery_or_a_second_one_does_nothing(world):
    calls = []
    service, sent = _service(world, trial=lambda *args: calls.append(args) or dict(BOTH))
    service.start(ADAPTER, ITEM, fields=None, actor="engineer")
    key, token = sent[0]

    service.run(key, "another-token")
    assert calls == [] and service.get(ADAPTER, ITEM)["status"] == "queued"
    service.run(key, token)
    service.run(key, token)

    assert len(calls) == 1


def test_whatever_stops_a_trial_ends_it_with_words_and_keeps_the_detail(world):
    def broken(*_):
        raise RuntimeError("deepseek API key is not configured")

    service, sent = _service(world, trial=broken)
    service.start(ADAPTER, ITEM, fields=None, actor="engineer")

    service.run(*sent[0])

    state = service.get(ADAPTER, ITEM)
    assert state["status"] == "failed"
    assert state["error"] == "AI 模型没有配置好，试算没有开始。请联系管理员检查模型设置。"
    assert state["error_detail"] == "RuntimeError: deepseek API key is not configured"
    assert active_trials() == 0


def test_a_user_who_lost_access_while_the_trial_waited_does_not_get_it_run(world):
    calls = []
    service, sent = _service(world, trial=lambda *args: calls.append(args) or dict(BOTH))
    service.start(ADAPTER, ITEM, fields=None, actor="engineer", actor_user_id="7")

    def refuse(_user):
        raise PermissionError("no longer granted")

    service.run(*sent[0], guard=refuse)

    assert calls == [] and service.get(ADAPTER, ITEM)["status"] == "failed"


def test_a_trial_nobody_is_running_any_more_is_shown_as_lost_and_can_be_started_again(world):
    service, sent = _service(world)
    service.start(ADAPTER, ITEM, fields=None, actor="engineer")
    with world.scope() as db:
        row = db.get(ParserControlSetting, task_key(ADAPTER, ITEM))
        long_ago = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        row.value_json = {**row.value_json, "status": "running", "started_at": long_ago}

    state = service.get(ADAPTER, ITEM)
    assert state["status"] == "failed" and "可能已经中断" in state["error"]
    assert active_trials() == 0 and recorded(ITEM)[ADAPTER]["trial_status"] == "failed"

    assert service.start(ADAPTER, ITEM, fields=None, actor="engineer")["status"] == "queued"
    assert len(sent) == 2


def test_stopping_the_app_ends_trials_under_way_and_only_those(world):
    service, sent = _service(world)
    service.start(ADAPTER, ITEM, fields=None, actor="engineer")
    with world.scope() as db:
        db.add(ParserControlSetting(key="adapter-fields:done", value_json={"status": "ready", "check_item_id": "x"}))

    with world.scope() as db:
        cancel_active(db)

    state = service.get(ADAPTER, ITEM)
    assert state["status"] == "cancelled" and "请重新开始" in state["error"]
    service.run(*sent[0])
    assert service.get(ADAPTER, ITEM)["status"] == "cancelled"
    with world.scope() as db:
        assert db.get(ParserControlSetting, "adapter-fields:done").value_json["status"] == "ready"


def _ready(world, monkeypatch, switched, result=None):
    service, sent = _service(world, trial=lambda *_: dict(result or BOTH), switched=switched, monkeypatch=monkeypatch)
    service.start(ADAPTER, ITEM, fields=None, actor="engineer")
    service.run(*sent[0])
    return service


def test_saving_puts_the_confirmed_fields_into_force_and_records_who_did_it(world, monkeypatch):
    switched = []
    service = _ready(world, monkeypatch, switched)

    saved = service.save(ADAPTER, ITEM, fields=["WAVELENGTH", "VENDOR_NOTE"], actor="engineer")

    assert switched == [{"template_text": EXTENDED, "fields": ["WAVELENGTH", "VENDOR_NOTE"], "actor": "engineer"}]
    assert saved["fields"] == ["WAVELENGTH", "VENDOR_NOTE"] and saved["left_out_fields"] == []
    assert saved["outputs_replayed"] == 3 and saved["records_replayed"] == 4 and saved["dry_run"] is False
    state = service.get(ADAPTER, ITEM)
    assert state["status"] == "saved" and state["saved"]["implementation_version"] == 3
    with world.scope() as db:
        audit = db.query(AuditLogRecord).one()
        assert audit.action == "adapter_fields_added" and audit.username == "engineer"
        assert audit.resource_id == ADAPTER and audit.detail["fields"] == ["WAVELENGTH", "VENDOR_NOTE"]
        # The template the trial produced is not kept once it has been used.
        assert db.get(ParserControlSetting, task_key(ADAPTER, ITEM)).value_json["result"]["template_text"] is None
    with pytest.raises(AdapterFieldsError, match="没有可以保存的试算结果"):
        service.save(ADAPTER, ITEM, fields=["WAVELENGTH"], actor="engineer")


def test_a_field_the_engineer_did_not_confirm_is_taken_out_of_the_template_before_it_is_saved(world, monkeypatch):
    switched = []
    service = _ready(world, monkeypatch, switched)

    saved = service.save(ADAPTER, ITEM, fields=["WAVELENGTH"], actor="engineer")

    assert saved["fields"] == ["WAVELENGTH"] and saved["left_out_fields"] == ["VENDOR_NOTE"]
    template = switched[0]["template_text"]
    assert "VENDOR_NOTE" not in template and "Value WAVELENGTH" in template
    assert adapter_fields.rules_lost(CURRENT, template) == []


def test_only_fields_the_trial_found_can_be_saved(world, monkeypatch):
    switched = []
    service = _ready(world, monkeypatch, switched)

    for fields in ([], ["RX_POWER"], ["WAVELENGTH", "SERIAL"]):
        with pytest.raises(AdapterFieldsError, match="只能保存这次试算取到值的字段"):
            service.save(ADAPTER, ITEM, fields=fields, actor="engineer")
    assert switched == []


def test_a_result_from_before_the_adapter_changed_is_not_saved(world, monkeypatch):
    switched = []
    service = _ready(world, monkeypatch, switched)
    world.base["implementation_version_id"] = "adapter-v9"

    assert service.get(ADAPTER, ITEM)["result"]["stale"] is True
    with pytest.raises(AdapterFieldsError, match="请重新试算"):
        service.save(ADAPTER, ITEM, fields=["WAVELENGTH"], actor="engineer")
    assert switched == [] and service.get(ADAPTER, ITEM)["status"] == "ready"


def test_saving_decides_again_on_the_outputs_stored_now_and_takes_nothing_on_trust(world, monkeypatch):
    switched = []
    # A stored result whose template edits the rule in force: whatever produced it, it must not go in.
    tampered = EXTENDED.replace("^${INTERFACE}\\s+rx\\s+${RX_POWER} -> Record", "^${INTERFACE}\\s+rx\\s+-?${RX_POWER} -> Record")
    service = _ready(world, monkeypatch, switched, result={**BOTH, "template_text": tampered})

    with pytest.raises(AdapterFieldsError, match="保存前的复核没有通过，没有做任何改动") as refused:
        service.save(ADAPTER, ITEM, fields=["WAVELENGTH", "VENDOR_NOTE"], actor="engineer")

    assert "已有规则被改动" in str(refused.value)
    assert switched == []
    with world.scope() as db:
        assert db.query(AuditLogRecord).count() == 0

    # And a field the outputs no longer show cannot be saved either.
    world.outputs = dict(SAMPLES)
    service = _ready(world, monkeypatch, switched)
    with pytest.raises(AdapterFieldsError, match="字段 VENDOR_NOTE 在现有输出里取不到值"):
        service.save(ADAPTER, ITEM, fields=["WAVELENGTH", "VENDOR_NOTE"], actor="engineer")


def test_a_dry_run_reports_the_switch_and_leaves_everything_as_it_was(world, monkeypatch):
    switched = []
    service = _ready(world, monkeypatch, switched)

    saved = service.save(ADAPTER, ITEM, fields=["WAVELENGTH"], actor="engineer", dry_run=True)

    assert saved["dry_run"] is True and saved["implementation_version"] == 3
    state = service.get(ADAPTER, ITEM)
    assert state["status"] == "ready" and state["saved"] is None
    with world.scope() as db:
        assert db.query(AuditLogRecord).count() == 0


def test_a_switch_that_cannot_complete_leaves_the_trial_ready_to_try_again(world, monkeypatch):
    service = _ready(world, monkeypatch, [])

    def blocked(*_args, **_kwargs):
        raise AdapterFieldsError("运行包无法切到新的适配器版本，没有做任何改动：blocked")

    monkeypatch.setattr(service_module, "_switch", blocked)
    with pytest.raises(AdapterFieldsError, match="运行包无法切到新的适配器版本"):
        service.save(ADAPTER, ITEM, fields=["WAVELENGTH"], actor="engineer")

    assert service.get(ADAPTER, ITEM)["status"] == "ready"
