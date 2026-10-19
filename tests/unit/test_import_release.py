"""A template from a rule package goes into force only after a replay it passed; a device's silence is said as such."""

from contextlib import contextmanager
from datetime import datetime, timezone
from types import SimpleNamespace

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from src.apps.inspection.backend.patrol_batch_service import (
    _missing_reference_text,
    evaluate_current_state,
    mark_device_absent_reference_fields,
)
from src.apps.rule_governance.backend.check_items import adapter_fields
from src.apps.rule_governance.backend.check_items.import_release import (
    _compatibility,
    bring_in,
    incoming_adapters,
    text_only_commands,
    text_only_warning,
)
from src.apps.rule_governance.backend.parser import repository as parser_repository
from src.apps.rule_governance.backend.transfer_service import parse_sample
from src.apps.rule_governance.backend.transfer_validator import _release_replay
from src.core.check_items.field_absence import device_absent, task_key
from src.infrastructure.db.models import (
    Base,
    CheckItemImplementationLinkRecord,
    CheckItemRuntimeBundleVersionRecord,
    CheckItemVersionRecord,
    CollectionImplementationParserBindingRecord,
    CollectionImplementationRecord,
    CollectionImplementationVersionRecord,
    ParserControlSetting,
    ParserTemplate,
)

TEMPLATE = """Value Required INTERFACE (\\S+)
Value RX_POWER (-?\\d+\\.\\d+)

Start
  ^${INTERFACE}\\s+rx\\s+${RX_POWER} -> Record
"""
OUTPUT = "Eth1/1 rx -3.20\nEth1/2 rx -17.96\n"


def _replay(template=TEMPLATE, current=TEMPLATE, samples=(), outputs=()):
    return _release_replay({"template_text": template, "current_text": current,
                            "samples": list(samples), "outputs": list(outputs)})


def test_a_template_that_parses_what_the_adapter_parses_today_passes():
    result = _replay(outputs=[OUTPUT])

    assert result["passed"] and result["reason"] is None
    assert result["outputs"] == result["outputs_parsed"] == 1 and result["records_differing"] == 0


def test_package_samples_must_still_parse_to_what_they_parsed_to_at_export():
    sample = {"text": OUTPUT, "expected": parse_sample(TEMPLATE, OUTPUT)}
    assert _replay(current=None, samples=[sample])["passed"]

    changed = TEMPLATE.replace("-?\\d+\\.\\d+", "-?\\d+")
    result = _replay(template=changed, current=None, samples=[sample])

    assert not result["passed"] and result["samples_passed"] == 0
    assert result["reason"] == "包内样本 1：解析结果与导出时不同"


def test_a_template_that_yields_nothing_where_the_one_in_force_does_is_refused():
    swallowing = TEMPLATE.replace("Start\n", "Start\n  ^.* -> Next\n")

    result = _replay(template=swallowing, outputs=[OUTPUT])

    assert not result["passed"] and result["outputs_emptied"] == 1
    assert result["reason"] == "本地输出 1：现用模板解析出 2 条记录，导入的模板一条也没有"


def test_values_that_differ_from_the_template_in_force_are_counted_not_refused():
    # A newer template may read a value better; the release preview shows how many records differ.
    rounded = TEMPLATE.replace("Value RX_POWER (-?\\d+\\.\\d+)", "Value RX_POWER (-?\\d+)").replace(
        "${RX_POWER} -> Record", "${RX_POWER}\\.\\d+ -> Record")

    result = _replay(template=rounded, outputs=[OUTPUT])

    assert result["passed"] and result["records_differing"] == 2


def test_with_nothing_to_replay_on_there_is_no_release():
    result = _replay(current=None)

    assert not result["passed"]
    assert "没有可以用来验证的输出" in result["reason"] and "携带已脱敏的验证样本" in result["reason"]


def test_a_template_that_does_not_compile_is_refused_before_anything_else():
    result = _replay(template="Value broken (\n\nStart\n", outputs=[OUTPUT])

    assert not result["passed"] and result["reason"].startswith("模板无法编译")


def test_the_compatibility_of_an_imported_template_is_that_of_the_adapter_it_was_replayed_for():
    adapter = SimpleNamespace(match_json={"vendor": "H3C", "models": ["MSR5620"], "model_patterns": ["^MSR5620$"]})
    template = SimpleNamespace(vendor="H3C", canonical_command="display startup")

    manifest = _compatibility(adapter, template, {"samples": 0, "outputs": 3})

    assert manifest["scope"] == "model" and manifest["models"] == ["MSR5620"]
    assert manifest["model_patterns"] == ["^MSR5620$"] and manifest["evidence_source"] == "release_replay"
    assert manifest["replayed_outputs"] == 3
    family = _compatibility(SimpleNamespace(match_json={"vendor": "H3C", "family": "Comware7"}), template, {})
    assert family["scope"] == "family" and family["families"] == ["Comware7"]


def test_an_imported_adapter_whose_command_the_platform_keeps_as_text_is_named(monkeypatch):
    # The platform policy as shipped; no command aliases from the database.
    monkeypatch.setattr(parser_repository, "ParserMetadataRepository", lambda: SimpleNamespace(list_command_aliases=list))
    adapter = SimpleNamespace(match_json={"vendor": "H3C"}, commands_json=[
        {"command": "display logbuffer level 1"},  # a log window: compared as text whatever templates exist
        {"command": "display logbuffer level 2"},  # enabled for parsing
        {"command": "display vrrp statistics"},
        {"command": "display some new feature"},  # no policy: an approved template is used
    ])

    assert text_only_commands(adapter) == ["display logbuffer level 1"]
    assert text_only_commands(SimpleNamespace(match_json={}, commands_json=[{"command": "display vrrp statistics"}])) == []
    # Every command it names, and a way out that does not point at a page that does not exist.
    warning = text_only_warning(["display logbuffer level 1", "show logging last 100"])
    assert "display logbuffer level 1、show logging last 100" in warning
    assert "改用本平台能解析的命令" in warning and "重新部署" in warning


def _db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'absence.db'}")
    Base.metadata.create_all(engine, tables=[ParserControlSetting.__table__])
    factory = sessionmaker(bind=engine)

    @contextmanager
    def scope():
        session = factory()
        try:
            yield session
            session.commit()
        finally:
            session.close()

    return scope


def test_a_field_is_marked_as_not_printed_only_when_every_adapter_it_was_missing_from_says_so(tmp_path):
    scope = _db(tmp_path)
    with scope() as db:
        db.add(ParserControlSetting(key=task_key("h3c-optics", "interface.optical_power"),
                                    value_json={"absent": {"temp_high_warning": {}, "bias_high_warning": {}}}))
        db.add(ParserControlSetting(key=task_key("cisco-optics", "interface.optical_power"),
                                    value_json={"absent": {"temp_high_warning": {}}}))
    with scope() as db:
        marked = device_absent(db, {
            "temp_high_warning": [("interface.optical_power", "h3c-optics"), ("interface.optical_power", "cisco-optics")],
            "bias_high_warning": [("interface.optical_power", "h3c-optics"), ("interface.optical_power", "cisco-optics")],
            "voltage_high_warning": [("interface.optical_power", "h3c-optics")],
            "rx_high_warning": [("interface.optical_power", "")],
        })

    # Cisco was not tried for bias; nobody found voltage absent; an unknown adapter vouches for nothing.
    assert marked == ["temp_high_warning"]


def test_the_report_names_a_missing_threshold_the_device_does_not_print_and_keeps_its_verdict(tmp_path):
    scope = _db(tmp_path)
    with scope() as db:
        db.add(ParserControlSetting(key=task_key("h3c-optics", "interface.optical_power"),
                                    value_json={"absent": {"temp_high_warning": {}}}))
    observation = {
        "device_id": "d1", "entity_type": "interface", "entity_key": "Eth1/1", "field": "temperature_celsius",
        "observation_type": "interface.optical.temperature", "value": 40.0, "value_number": 40.0,
        "observed_at": "2026-10-16T00:00:00+00:00", "check_item_id": "interface.optical_power",
        "metadata": {"check_item_id": "interface.optical_power", "command_adapter_id": "h3c-optics"},
        "context": {},
    }
    rule = {
        "event_type": "temperature.high", "event_class": "fault", "severity": "major", "evaluation_scope": "current_state",
        "observation_type": "interface.optical.temperature",
        "condition": {"kind": "threshold", "field": "temperature_celsius", "operator": "gt",
                      "reference_field": "temp_high_warning", "observation_type": "interface.optical.temperature"},
    }
    assessment = evaluate_current_state([observation], [rule], rule_source="test")
    # The judgement itself records which check item and adapter each missing threshold came from.
    assert assessment["missing_threshold_reference_sources"] == {
        "interface": {"temp_high_warning": [("interface.optical_power", "h3c-optics")]}}
    assessment["by_domain"]["interface"]["missing_threshold_reference_fields"].append("vendor_limit")
    assessment["missing_threshold_reference_sources"]["interface"]["vendor_limit"] = [
        ("interface.optical_power", "h3c-optics")]
    with scope() as db:
        mark_device_absent_reference_fields(db, assessment)

    row = assessment["by_domain"]["interface"]
    assert row["device_absent_reference_fields"] == ["temp_high_warning"]
    assert row["threshold_unknown_observation_count"] == 1
    assert _missing_reference_text(row) == "temp_high_warning（设备不输出）, vendor_limit"


NOW = datetime(2026, 10, 17, 8, 0, tzinfo=timezone.utc)
SWALLOWING = TEMPLATE.replace("Start\n", "Start\n  ^.* -> Next\n")


def _catalog(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'release.db'}")
    Base.metadata.create_all(engine, tables=[
        CheckItemVersionRecord.__table__,
        CheckItemImplementationLinkRecord.__table__,
        CheckItemRuntimeBundleVersionRecord.__table__,
        CollectionImplementationRecord.__table__,
        CollectionImplementationVersionRecord.__table__,
        CollectionImplementationParserBindingRecord.__table__,
        ParserTemplate.__table__,
    ])
    with engine.begin() as connection:
        # SQLite would make PostgreSQL's partial index a plain one: one template per command, whatever its status.
        connection.execute(text("DROP INDEX uq_parser_template_active"))
        connection.execute(text("CREATE UNIQUE INDEX uq_parser_template_active "
                                "ON parser_templates (vendor, profile, canonical_command) WHERE status = 'active'"))
    return sessionmaker(bind=engine)()


def _template(db, template_id, status, template_text=TEMPLATE):
    db.add(ParserTemplate(id=template_id, vendor="H3C", profile="S6520XP-54XG-EI-G", canonical_command="display transceiver",
                          category="multi_record", version=template_id, asset_path=f"database://parser/{template_id}",
                          sha256="0" * 64, status=status, template_text=template_text))


def _adapter_version(db, adapter_id, version, template_id, status):
    db.add(CollectionImplementationVersionRecord(
        id=f"{adapter_id}-v{version}", implementation_id=adapter_id, version=version, status=status,
        match_json={"vendor": "H3C", "models": ["S6520XP-54XG-EI-G"]}, commands_json=[{"command": "display transceiver"}],
        parser_requirement="structured", digest=f"{adapter_id}{version}".ljust(64, "0"),
        adapter_spec_json={"parser": {"template_id": template_id}}))


def _check_version(db, version_id, check_item_id, version, status):
    db.add(CheckItemVersionRecord(id=version_id, check_item_id=check_item_id, version=version, status=status,
                                  result_mode="structured", assessment_mode="health", canonical_contract_json={},
                                  report_projection="auto", digest=version_id.ljust(64, "0")))


def _link(db, check_item_version_id, implementation_version_id):
    db.add(CheckItemImplementationLinkRecord(
        id=f"{check_item_version_id}>{implementation_version_id}", check_item_version_id=check_item_version_id,
        implementation_version_id=implementation_version_id, priority=100, required=True, created_at=NOW))


def _imported(tmp_path, monkeypatch, *, imported_text=TEMPLATE):
    """v19 of the optics check item in force with adapter v1; a package brought v23 and adapter v2 with a new template."""

    monkeypatch.setattr(parser_repository, "ParserMetadataRepository", lambda: SimpleNamespace(list_command_aliases=list))
    # What this adapter parsed in the patrols here.
    monkeypatch.setattr(adapter_fields, "stored_outputs", lambda implementation_id: [{"id": "snapshot-1", "text": OUTPUT}])
    db = _catalog(tmp_path)
    db.add(CollectionImplementationRecord(id="optics", name="S6520XP · display transceiver", status="active", current_version=1))
    _template(db, "in-force-template", "active")
    _template(db, "imported-template", "draft", imported_text)
    _adapter_version(db, "optics", 1, "in-force-template", "active")
    _adapter_version(db, "optics", 2, "imported-template", "draft")
    _check_version(db, "optics-v19", "interface.optical_power", 19, "active")
    _check_version(db, "optics-v23", "interface.optical_power", 23, "draft")
    _link(db, "optics-v19", "optics-v1")
    _link(db, "optics-v23", "optics-v2")
    db.flush()
    return db


def test_an_imported_adapter_goes_into_force_with_its_template_and_what_it_replaces_stands_down(tmp_path, monkeypatch):
    db = _imported(tmp_path, monkeypatch)

    entries, blockers = bring_in(db, incoming_adapters(db, "optics-v23"), actor="admin", now=NOW)

    assert blockers == []
    [entry] = entries
    assert (entry["brought_in"], entry["previous_version_id"], entry["version_id"]) == (True, "optics-v1", "optics-v2")
    [replayed] = entry["templates"]
    assert replayed["template_id"] == "imported-template"
    assert replayed["replay"]["passed"] and replayed["replay"]["outputs_parsed"] == 1
    assert db.get(CollectionImplementationVersionRecord, "optics-v1").status == "deprecated"
    assert db.get(CollectionImplementationVersionRecord, "optics-v2").status == "active"
    root = db.get(CollectionImplementationRecord, "optics")
    assert (root.status, root.current_version) == ("active", 2)
    assert db.get(ParserTemplate, "in-force-template").status == "deprecated"
    imported = db.get(ParserTemplate, "imported-template")
    assert imported.status == "active"
    # The replay is its review, and the adapter's devices are where it was shown to fit.
    assert imported.validation_json["semantic_review"]["method"] == "release_replay"
    assert imported.validation_json["compatibility"]["evidence_source"] == "release_replay"
    assert imported.validation_json["compatibility"]["models"] == ["S6520XP-54XG-EI-G"]


def test_a_template_that_loses_what_the_one_in_force_parses_keeps_the_release_back(tmp_path, monkeypatch):
    db = _imported(tmp_path, monkeypatch, imported_text=SWALLOWING)

    _entries, blockers = bring_in(db, incoming_adapters(db, "optics-v23"), actor="admin", now=NOW)

    assert blockers == ["导入的适配器“S6520XP · display transceiver”的解析模板没有通过回放："
                        "本地输出 1：现用模板解析出 2 条记录，导入的模板一条也没有"]
    assert db.get(ParserTemplate, "imported-template").status == "draft"
    assert db.get(ParserTemplate, "in-force-template").status == "active"


def test_a_template_another_local_adapter_reads_is_not_replaced_from_a_package(tmp_path, monkeypatch):
    db = _imported(tmp_path, monkeypatch)
    db.add(CollectionImplementationRecord(id="elsewhere", name="elsewhere", status="active", current_version=3))
    _adapter_version(db, "elsewhere", 3, "in-force-template", "active")
    db.flush()

    _entries, blockers = bring_in(db, incoming_adapters(db, "optics-v23"), actor="admin", now=NOW)

    [blocker] = blockers
    assert "正被本地适配器“elsewhere”使用" in blocker and "连同那个适配器一起导出" in blocker
    assert db.get(ParserTemplate, "in-force-template").status == "active"
    assert db.get(ParserTemplate, "imported-template").status == "draft"


def test_an_imported_adapter_that_no_longer_serves_a_check_item_running_on_it_is_held_back(tmp_path, monkeypatch):
    db = _imported(tmp_path, monkeypatch)
    # The adapter in force also runs the hardware inventory, which the package did not bring.
    _check_version(db, "inventory-v4", "device.hardware_inventory", 4, "active")
    _link(db, "inventory-v4", "optics-v1")
    db.add(CheckItemRuntimeBundleVersionRecord(
        id="inventory-bundle", bundle_id="inventory-bundle-root", version=1, check_item_version_id="inventory-v4",
        implementation_version_id="optics-v1", parser_bundle_json={}, canonical_catalog_version=1,
        canonical_catalog_digest="0" * 64, entity_identity_policy_version_id="identity", dependency_digests_json={},
        compatibility_json={}, validation_report_json={}, digest="1" * 64, status="active"))
    db.flush()

    _entries, blockers = bring_in(db, incoming_adapters(db, "optics-v23"), actor="admin", now=NOW)

    assert blockers == ["导入的适配器“S6520XP · display transceiver”不再服务 device.hardware_inventory，"
                        "而这些检查项正在用它巡检；请连同这些检查项一起导出后再导入"]
    assert db.get(CollectionImplementationVersionRecord, "optics-v1").status == "active"
    assert db.get(CollectionImplementationVersionRecord, "optics-v2").status == "draft"
