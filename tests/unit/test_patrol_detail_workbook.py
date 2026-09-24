from io import BytesIO

from openpyxl import load_workbook

from src.core.patrol.detail_workbook import render_detail_workbook


def fixture(mode="structured", name="接口信息"):
    definition = {
        "id": "interface.test",
        "name": name,
        "mode": mode,
        "contract": {
            "identity_dimensions": ["interface"],
            "observations": [
                {
                    "canonical_field": "input_errors",
                    "observation_type": "input.errors",
                    "value_type": "integer",
                },
                {"canonical_field": "description", "observation_type": "description"},
            ],
        },
    }
    snapshots = [
        {
            "id": role,
            "device_id": "switch",
            "command": "display interface",
            "canonical_command": "display interface",
            "period": role,
            "metadata_json": {"check_item_version_ids": ["v1"]},
            "structured_json": [{"interface": "GE1", "input_errors": val, "description": "=1+1"}],
            "parse_status": "parsed",
            "raw_text": f"Interface GE1\nerrors {val}\nunchanged",
        }
        for role, val in [("baseline", "0"), ("current", "12")]
    ]
    return {"definitions": {"v1": definition}, "snapshots": snapshots, "observations": []}


def workbook(data, report=None):
    return load_workbook(BytesIO(render_detail_workbook(report or {}, {}, data)))


def test_original_values_and_reference_style():
    ws = workbook(fixture()).active
    assert ws.title == "接口信息"
    assert [c.value for c in ws[1]] == [
        "设备ID",
        "命令",
        "interface",
        "input_errors",
        "description",
    ]
    assert ws["D2"].value == "12"
    assert ws["E2"].value == "=1+1" and ws["E2"].data_type == "s"
    assert ws.freeze_panes == "C2"
    assert ws["A1"].fill.fgColor.rgb.endswith("BDD7EE")
    assert all(
        getattr(ws["A2"].border, side).style == "thin"
        for side in ("left", "right", "top", "bottom")
    )


def test_no_mapping_filtering_or_renaming_of_raw_value_records():
    data = fixture()
    s = data["snapshots"][1]
    s["structured_json"] = [
        {"SLOT_TYPE": "Fan", "SLOT_ID": "01", "SERIAL": "000123", "COUNT": 12345678901234567890},
        {"SLOT_TYPE": "Power"},
    ]
    s["detail_records"] = {"v1": []}  # Old adapter failure must not discard saved records.
    s["parse_status"] = "incomplete"
    ws = workbook(data).active
    assert ws.max_row == 3
    assert [c.value for c in ws[1]][2:] == ["SLOT_TYPE", "SLOT_ID", "SERIAL", "COUNT"]
    assert ws["D2"].value == "01" and ws["E2"].value == "000123"
    assert ws["F2"].value == "12345678901234567890"
    assert ws["D3"].value is None


def test_multi_check_item_command_uses_record_set_selection():
    data = fixture()
    data["definitions"]["v2"] = {
        "id": "memory",
        "name": "内存",
        "mode": "structured",
        "contract": {},
    }
    for s in data["snapshots"]:
        s["metadata_json"]["check_item_version_ids"].append("v2")
        s["structured_json"] = {
            "record_sets": {"cpu": [{"CPU": "0", "IDLE": "99"}], "mem": [{"FREE": "100"}]}
        }
        s["detail_record_sets"] = {"v1": ["cpu"], "v2": ["mem"]}
    wb = workbook(data)
    assert [c.value for c in wb["接口信息"][1]][2:] == ["CPU", "IDLE"]
    assert [c.value for c in wb["内存"][1]][2:] == ["FREE"]
    assert wb["内存"].max_row == 2


def test_text_comparison_preserves_every_byte_and_marks_changes():
    data = fixture("normalized_diff")
    data["snapshots"][0]["raw_text"] = "same\r\nold\n"
    data["snapshots"][1]["raw_text"] = "same\r\nnew\n" + "x" * 40000
    ws = workbook(data).active
    assert [c.value for c in ws[1]] == ["设备ID", "命令", "基线数据", "当前数据"]
    for col, snapshot in [(3, data["snapshots"][0]), (4, data["snapshots"][1])]:
        assert (
            "".join(ws.cell(r, col).value or "" for r in range(2, ws.max_row + 1))
            == snapshot["raw_text"]
        )
    assert ws["C3"].fill.fgColor.rgb.endswith("FFC7CE")
    assert not ws["C2"].fill.fgColor.rgb.endswith("FFC7CE")


def test_unbound_original_text_is_retained_with_an_explicit_note():
    data = fixture("normalized_diff")
    data["definitions"] = {}
    for s in data["snapshots"]:
        s["structured_json"] = None
        s["metadata_json"] = {"handling": "raw_hash_diff"}
    wb = workbook(data)
    assert wb.sheetnames == ["display interface", "导出说明"]
    assert wb.active["D2"].value == "Interface GE1\n"
    assert "未保存可用检查项关联" in wb["导出说明"]["D2"].value


def test_missing_period_is_not_substituted_and_empty_results_have_notes():
    data = fixture()
    data["snapshots"] = data["snapshots"][:1]
    wb = workbook(data)
    assert wb.active.max_row == 1
    assert "本期未采集" in wb["导出说明"]["D2"].value
    data = fixture()
    data["snapshots"][1]["structured_json"] = []
    wb = workbook(data)
    assert wb.active.max_row == 1
    assert "无可导出" in wb["导出说明"]["D2"].value


def test_duplicate_current_snapshots_remain_exportable():
    from copy import deepcopy

    data = fixture()
    duplicate = deepcopy(data["snapshots"][1])
    duplicate["id"] = "second-current"
    data["snapshots"].append(duplicate)
    assert workbook(data).active.max_row == 3
    data["definitions"]["v1"]["mode"] = "normalized_diff"
    wb = workbook(data)
    assert "分别保留" in wb["导出说明"]["D2"].value
    assert wb.active.max_row == 10


def test_sheet_name_collisions_do_not_merge_different_check_items():
    data = fixture(name="接口/信息")
    data["definitions"]["v2"] = {**data["definitions"]["v1"], "id": "other", "name": "接口:信息"}
    for s in data["snapshots"]:
        s["metadata_json"]["check_item_version_ids"].append("v2")
        s["detail_record_sets"] = {"v1": ["default"], "v2": ["default"]}
    assert workbook(data).sheetnames == ["接口_信息", "接口_信息 (2)"]


def test_no_saved_data_and_invalid_period_selection(monkeypatch):
    import pytest

    from src.core.patrol.detail_data import load_detail_data

    assert workbook({"definitions": {}, "snapshots": []}).active.title == "导出说明"
    with pytest.raises(ValueError):
        load_detail_data({"periods": [{"role": "baseline", "patrol_run_id": "one"}]})
    with pytest.raises(ValueError):
        load_detail_data(
            {
                "periods": [
                    {"role": "current", "patrol_run_id": "one"},
                    {"role": "baseline", "patrol_run_id": "one"},
                ]
            }
        )


def test_detail_source_authorization_and_period_scope(monkeypatch):
    from contextlib import contextmanager
    from datetime import datetime, timezone

    import pytest
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from src.core.patrol.detail_data import load_detail_source
    from src.infrastructure.db import postgres
    from src.infrastructure.db.models import OfflinePatrolJobRecord, OfflinePatrolPeriodRecord

    engine = create_engine("sqlite://")
    for model in (OfflinePatrolJobRecord, OfflinePatrolPeriodRecord):
        model.__table__.create(engine)
    now = datetime.now(timezone.utc)
    with Session(engine) as db:
        db.add(
            OfflinePatrolJobRecord(
                id="job",
                idempotency_key="k",
                name="test",
                owner_id="owner",
                status="completed",
                purpose="offline_patrol",
                job_type="single_patrol",
            )
        )
        db.add(
            OfflinePatrolPeriodRecord(
                id="period",
                job_id="job",
                role="current",
                label="current",
                observed_at=now,
                dataset_id="dataset",
                patrol_run_id="selected-run",
            )
        )
        db.commit()

    @contextmanager
    def session():
        with Session(engine) as db:
            yield db

    monkeypatch.setattr(postgres, "get_db_session", session)
    assert (
        load_detail_source("job", owner_id="owner")["periods"][0]["patrol_run_id"] == "selected-run"
    )
    with pytest.raises(PermissionError):
        load_detail_source("job", owner_id="other")
    assert load_detail_source("job", owner_id="admin", is_admin=True)["periods"]
    with pytest.raises(KeyError):
        load_detail_source("missing", owner_id="owner")
    with Session(engine) as db:
        db.get(OfflinePatrolJobRecord, "job").status = "running"
        db.commit()
    with pytest.raises(ValueError, match="任务完成"):
        load_detail_source("job", owner_id="owner")
