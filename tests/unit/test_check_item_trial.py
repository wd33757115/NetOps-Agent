"""The last patrol's values, judged again under another version of the check item."""

import json
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.apps.rule_governance.backend.check_items import trial
from src.apps.rule_governance.backend.check_items.trial import _as_version, _compiled, trial_on_last_patrol
from src.core.check_items.canonical import freeze_rule_semantics
from src.core.check_items.local_semantics import derive_assessment_mode
from src.infrastructure.db.models import (
    Base,
    ChangePolicyRecord,
    ChangePolicyVersionRecord,
    CheckItemRecord,
    CheckItemVersionRecord,
    PatrolCollectionResult,
    PatrolRun,
    StateObservationRecord,
)
from tests.unit.test_check_item_value_use import STATUS, contract, field, refs

AT = datetime(2026, 10, 9, 15, 31, tzinfo=timezone.utc)
ITEM = "probe.item"


def version(version_id, *rows):
    frozen = freeze_rule_semantics(contract(*rows))
    return {"version_id": version_id, "result_mode": "structured",
            "assessment_mode": derive_assessment_mode(frozen), "canonical_contract": frozen}


# Collected under a version that only shows the values ...
SHOWN = version(
    "v1",
    field("cpu", "gauge", "evidence", unit="percent"),
    field("warn", "threshold", "reference", unit="percent"),
    field("alarm", "threshold", "reference", unit="percent"),
    field("status", "state", "evidence", groups=STATUS),
    field("note", "text", "evidence"),
)
# ... and tried under one that judges them, and no longer has `note`.
JUDGED = version(
    "v2",
    field("cpu", "gauge", "current_state", unit="percent", threshold_references=refs(high="warn", critical_high="alarm")),
    field("warn", "threshold", "reference", unit="percent"),
    field("alarm", "threshold", "reference", unit="percent"),
    field("status", "state", "current_state", groups=STATUS),
    field("lane", "state", "current_state", groups=STATUS),
)


@pytest.fixture
def db(monkeypatch):
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine, tables=[
        CheckItemRecord.__table__, CheckItemVersionRecord.__table__, PatrolRun.__table__, StateObservationRecord.__table__,
        ChangePolicyRecord.__table__, ChangePolicyVersionRecord.__table__, PatrolCollectionResult.__table__])
    session = sessionmaker(bind=engine)()
    monkeypatch.setattr(trial.EventRuleRepository, "snapshot_active_rules", lambda self: [])

    def on_its_own(*_args, **_kwargs):
        raise AssertionError("a trial reads through the session it is given and writes nothing")

    # The patrol's comparison would otherwise look up, and could store, through these.
    for module in ("src.core.patrol.repository", "src.core.state.repository",
                   "src.core.patrol.change_policy_repository", "src.core.patrol.change_event_repository"):
        monkeypatch.setattr(f"{module}.get_db_session", on_its_own)
    # The one lookup it does make is for raw outputs that failed to parse; this run has none.
    monkeypatch.setattr("src.core.patrol.change_detector.PatrolRepository.list_run_snapshots",
                        lambda self, run_id: (), raising=False)
    yield session
    session.close()


def collect(db, run_id, values, *, under=SHOWN, role="current", completed=AT):
    """Store one patrol run: ``values`` maps a slot to the values read from it."""

    db.add(PatrolRun(id=run_id, mode="production", source="offline_import", logical_run_id=run_id, status="completed",
                     dataset_id=f"job-{role}", started_at=AT, completed_at=completed,
                     metadata_json={"offline_job_id": "job", "period_role": role}))
    rows = {row["canonical_field"]: row for row in under["canonical_contract"]["observations"]}
    for slot, record in values.items():
        for name, value in record.items():
            row = rows[name]
            db.add(StateObservationRecord(
                id=f"{run_id}-{slot}-{name}", idempotency_key=f"{run_id}-{slot}-{name}", run_id=run_id, snapshot_id="snapshot",
                mode="production", source="offline_import", device_id="core-1", canonical_command="display probe",
                execution_item_id="item", entity_type="probe", entity_key=json.dumps([slot]), field_name=name,
                observation_type=row["observation_type"], value_json=value, value_text=str(value), value_type="text",
                observed_at=AT, metadata_json={"check_item_id": ITEM, "check_item_version_id": under["version_id"],
                                               "semantics": row}))
    db.flush()


def test_values_are_labelled_as_the_other_version_would_have_labelled_them():
    stored = [
        {"device_id": "core-1", "entity_type": "probe", "entity_key": '["1"]', "field": name, "value": value,
         "observation_type": f"probe.{name}", "metadata": {"check_item_version_id": "v1", "semantics": {}}, "context": None}
        for name, value in {"cpu": 95, "warn": 80, "alarm": 90, "note": "x"}.items()
    ]

    labelled = {item["field"]: item for item in _as_version(stored, JUDGED)}

    assert set(labelled) == {"cpu", "warn", "alarm"}                      # the version has no `note`
    cpu = labelled["cpu"]
    assert cpu["metadata"]["check_item_version_id"] == "v2"
    assert cpu["metadata"]["semantics"]["health_policy"]["device_thresholds"] == {"high": ["warn", "alarm"]}
    # The limits come from the object's other values, though the collecting version named none.
    assert (cpu["context"]["warn"], cpu["context"]["alarm"]) == (80, 90)


def test_the_trial_reports_what_the_last_patrol_would_newly_flag(db):
    collect(db, "job-baseline-1", {"1": {"cpu": 10, "warn": 80, "alarm": 90, "status": "UP"}}, role="baseline")
    collect(db, "job-current-1", {
        "1": {"cpu": 95, "warn": 80, "alarm": 90, "status": "UP", "note": "hot"},
        "2": {"cpu": 10, "warn": 80, "alarm": 90, "status": "DOWN", "note": "cold"},
    })

    result = trial_on_last_patrol(db, check_item_id=ITEM, current=SHOWN, candidate=JUDGED)

    assert result["available"]
    assert result["run"] == {"run_id": "job-current-1", "observed_at": result["run"]["observed_at"], "devices": 1, "values": 10}
    assert (result["now"]["abnormal"], result["after"]["abnormal"]) == (0, 2)
    assert {(item["entity_key"], item["field"], item["value"]) for item in result["added"]} == {
        ('["1"]', "cpu", 95), ('["2"]', "status", "DOWN")}
    assert (result["added_count"], result["removed_count"], result["removed"]) == (2, 0, [])
    by_field = {rule["field"]: rule for rule in result["rules"]}
    assert (by_field["cpu"]["now"], by_field["cpu"]["after"], by_field["cpu"]["change"]) == (0, 1, "added")
    assert by_field["cpu"]["text"] == "超出设备阈值时判定异常。"
    assert (by_field["status"]["after"], by_field["status"]["change"]) == (1, "added")
    assert by_field["status"]["text"].startswith("值为 DOWN")
    # `lane` is judged by the candidate but was never collected.
    assert result["no_data_fields"] == ["lane"]


def test_the_trial_reports_what_would_no_longer_be_flagged(db):
    collect(db, "job-current-1", {"1": {"cpu": 95, "warn": 80, "alarm": 90, "status": "DOWN"}}, under=JUDGED)

    result = trial_on_last_patrol(db, check_item_id=ITEM, current=JUDGED, candidate=SHOWN)

    assert (result["now"]["abnormal"], result["after"]["abnormal"]) == (1, 0)
    assert [(item["field"], item["value"]) for item in result["removed"]] == [("cpu", 95)]
    assert {rule["change"] for rule in result["rules"]} == {"removed"}


def test_the_same_version_judges_the_last_patrol_the_same(db):
    collect(db, "job-current-1", {"1": {"cpu": 95, "warn": 80, "alarm": 90, "status": "UP"}}, under=JUDGED)

    result = trial_on_last_patrol(db, check_item_id=ITEM, current=JUDGED, candidate=JUDGED)

    assert (result["now"], result["added_count"], result["removed_count"]) == (result["after"], 0, 0)
    assert result["now"]["abnormal"] == 1


def test_the_latest_patrol_is_the_one_tried_and_a_baseline_is_not_a_patrol(db):
    earlier = datetime(2026, 10, 1, tzinfo=timezone.utc)
    collect(db, "old-current-1", {"1": {"cpu": 95, "warn": 80, "alarm": 90}}, completed=earlier)
    collect(db, "job-baseline-1", {"1": {"cpu": 95, "warn": 80, "alarm": 90}}, role="baseline",
            completed=datetime(2026, 10, 11, tzinfo=timezone.utc))
    collect(db, "job-current-1", {"1": {"cpu": 10, "warn": 80, "alarm": 90}})

    result = trial_on_last_patrol(db, check_item_id=ITEM, current=SHOWN, candidate=JUDGED)

    assert result["run"]["run_id"] == "job-current-1"
    assert result["after"]["abnormal"] == 0


@pytest.mark.parametrize("current,reason", [(None, "new_check_item"), (SHOWN, "no_run")])
def test_nothing_to_try_is_said_with_its_reason(db, current, reason):
    assert trial_on_last_patrol(db, check_item_id=ITEM, current=current, candidate=JUDGED) == {
        "available": False, "reason": reason}


# --- changes against the baseline, and the events made of them -------------------------------------------------

TRACKED = version("v3", field("status", "state", "change", groups=STATUS), field("note", "text", "evidence"))
UNTRACKED = version("v4", field("status", "state", "evidence", groups=STATUS), field("note", "text", "evidence"))


def in_force(db, monkeypatch, published):
    """Put a version's compiled change policies and event rules in force, as its release would have."""

    policies, rules = _compiled(ITEM, published)
    db.add(CheckItemRecord(id=ITEM, name="Probe", category="system", status="active", current_version=1))
    db.add(CheckItemVersionRecord(
        id=published["version_id"], check_item_id=ITEM, version=1, result_mode="structured",
        assessment_mode=published["assessment_mode"], canonical_contract_json=published["canonical_contract"],
        report_projection="auto", status="active", digest="0" * 64))
    for index, policy in enumerate(policies):
        db.add(ChangePolicyRecord(id=f"policy-{index}", name=f"policy-{index}", observation_type=policy["observation_type"],
                                  status="active", current_version=1))
        db.add(ChangePolicyVersionRecord(
            id=f"policy-{index}-v1", policy_id=f"policy-{index}", check_item_version_id=published["version_id"], version=1,
            observation_type=policy["observation_type"], semantic_type="state",
            compare_mode=str(policy["policy"].get("compare_mode")), policy_json=policy["policy"], status="active",
            digest="0" * 64))
    db.flush()
    monkeypatch.setattr(trial.EventRuleRepository, "snapshot_active_rules", lambda self: rules)


def went_down(db, *, under):
    collect(db, "job-baseline-1", {"1": {"status": "UP", "note": "a"}, "2": {"status": "UP", "note": "a"}},
            under=under, role="baseline")
    collect(db, "job-current-1", {"1": {"status": "DOWN", "note": "a"}, "2": {"status": "UP", "note": "b"}}, under=under)


def test_the_trial_finds_the_events_a_newly_tracked_field_would_have_raised(db):
    went_down(db, under=UNTRACKED)

    comparison = trial_on_last_patrol(db, check_item_id=ITEM, current=UNTRACKED, candidate=TRACKED)["comparison"]

    assert comparison["available"] and comparison["baseline"]["run_id"] == "job-baseline-1"
    assert comparison["now"] == {"changes": 0, "events": {"fault": 0, "recovery": 0, "change": 0}}
    assert comparison["after"] == {"changes": 1, "events": {"fault": 1, "recovery": 0, "change": 0}}
    assert (comparison["added_count"], comparison["removed_count"]) == (1, 0)
    [raised] = comparison["added"]
    assert (raised["entity_key"], raised["field"], raised["old"], raised["new"]) == ('["1"]', "status", "UP", "DOWN")
    assert (raised["event_class"], raised["severity"]) == ("fault", "major")
    [rule] = comparison["rules"]
    assert (rule["field"], rule["now"], rule["after"], rule["change"]) == ("status", 0, 1, "added")
    assert rule["text"].startswith("从 UP 变为 DOWN 时产生故障事件")


def test_the_trial_finds_the_events_that_would_no_longer_be_raised(db, monkeypatch):
    in_force(db, monkeypatch, TRACKED)
    went_down(db, under=TRACKED)

    comparison = trial_on_last_patrol(db, check_item_id=ITEM, current=TRACKED, candidate=UNTRACKED)["comparison"]

    assert comparison["now"] == {"changes": 1, "events": {"fault": 1, "recovery": 0, "change": 0}}
    assert comparison["after"] == {"changes": 0, "events": {"fault": 0, "recovery": 0, "change": 0}}
    assert [(item["field"], item["old"], item["new"], item["event_class"]) for item in comparison["removed"]] == [
        ("status", "UP", "DOWN", "fault")]
    assert [rule["change"] for rule in comparison["rules"]] == ["removed"]


def test_the_same_version_finds_the_same_changes(db, monkeypatch):
    in_force(db, monkeypatch, TRACKED)
    went_down(db, under=TRACKED)

    comparison = trial_on_last_patrol(db, check_item_id=ITEM, current=TRACKED, candidate=TRACKED)["comparison"]

    assert comparison["now"] == comparison["after"] == {"changes": 1, "events": {"fault": 1, "recovery": 0, "change": 0}}
    assert (comparison["added_count"], comparison["removed_count"], comparison["rules"]) == (0, 0, [
        {**comparison["rules"][0], "now": 1, "after": 1, "change": "same"}])


def test_a_patrol_that_was_compared_with_nothing_has_no_changes_to_try(db):
    collect(db, "job-current-1", {"1": {"status": "DOWN", "note": "a"}}, under=UNTRACKED)

    result = trial_on_last_patrol(db, check_item_id=ITEM, current=UNTRACKED, candidate=TRACKED)

    assert result["available"]
    assert result["comparison"] == {"available": False, "reason": "no_baseline"}


# --- a boolean with normal and abnormal values is judged the way a state is -------------------------------------

def flag(use):
    """A boolean field as the form saves it: boolean values, True normal and False abnormal."""

    groups = {"healthy": [True], "unhealthy": [False]}
    return {"observation_type": "probe.present", "canonical_field": "present", "value_type": "boolean", "unit": None,
            "required": False,
            "semantic_definition": {"description": "present", "semantic_type": "boolean", "value_types": ["boolean"],
                                    "canonical_unit": None, "assessment_roles": [use], "allowed_values": [True, False],
                                    "state_groups": groups}}


FLAG_SHOWN, FLAG_JUDGED, FLAG_TRACKED = (version(f"flag-{use}", flag(use)) for use in ("evidence", "current_state", "change"))


def test_a_boolean_health_judgment_flags_the_abnormal_value(db):
    collect(db, "job-current-1", {"1": {"present": True}, "2": {"present": False}}, under=FLAG_SHOWN)

    result = trial_on_last_patrol(db, check_item_id=ITEM, current=FLAG_SHOWN, candidate=FLAG_JUDGED)

    assert (result["now"]["abnormal"], result["after"]["abnormal"]) == (0, 1)
    assert [(item["entity_key"], item["value"], item["severity"]) for item in result["added"]] == [('["2"]', False, "major")]


def test_a_tracked_boolean_reports_the_move_to_its_abnormal_value_as_a_fault(db):
    collect(db, "job-baseline-1", {"1": {"present": True}, "2": {"present": True}}, under=FLAG_SHOWN, role="baseline")
    collect(db, "job-current-1", {"1": {"present": False}, "2": {"present": True}}, under=FLAG_SHOWN)

    comparison = trial_on_last_patrol(db, check_item_id=ITEM, current=FLAG_SHOWN, candidate=FLAG_TRACKED)["comparison"]

    assert comparison["after"] == {"changes": 1, "events": {"fault": 1, "recovery": 0, "change": 0}}
    [raised] = comparison["added"]
    assert (raised["entity_key"], raised["old"], raised["new"], raised["event_class"]) == ('["1"]', True, False, "fault")
