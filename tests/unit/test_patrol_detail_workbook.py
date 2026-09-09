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


def test_standard_columns_numeric_values_cell_changes_and_formula_safety():
    ws = workbook(fixture()).active
    assert [c.value for c in ws[1]] == [
        "设备ID",
        "命令",
        "问题描述",
        "interface",
        "input_errors",
        "description",
    ]
    assert ws["E2"].value == 12 and ws["E2"].font.color.rgb.endswith("C00000")
    assert not ws["D2"].font.color.rgb.endswith("C00000")
    assert ws["F2"].value == "=1+1" and ws["F2"].data_type == "s"
    assert ws.freeze_panes == "D2" and ws.auto_filter.ref == "A1:F2"


def test_missing_baseline_not_marked_changed():
    data = fixture()
    data["snapshots"] = data["snapshots"][1:]
    ws = workbook(data).active
    assert not ws["C2"].value
    assert "未提供可比基线" in workbook(data)["导出说明"]["D2"].value
    assert not ws["E2"].font.color.rgb.endswith("C00000")


def test_missing_five_minute_metric_is_explained_without_filling_a_value():
    data = fixture()
    data["definitions"]["v1"]["contract"]["observations"].append({
        "canonical_field": "memory_usage_percent", "required": False,
        "fixed_dimensions": {"window": "5m"}, "health_policy": {"window": "5m"},
    })
    result = workbook(data)
    assert result.active["G2"].value is None
    assert any("未采集五分钟使用率" in str(row[3])
               for row in result["导出说明"].iter_rows(values_only=True))


def test_duplicate_identity_and_parse_failure_do_not_invent_deletion():
    data = fixture()
    data["snapshots"][1]["structured_json"] *= 2
    ws = workbook(data).active
    assert all(not ws.cell(r, 3).value for r in [2, 3])
    assert "对象标识不唯一" in workbook(data)["导出说明"]["D2"].value
    data["snapshots"][1]["structured_json"] = []
    data["snapshots"][1]["parse_status"] = "failed"
    ws = workbook(data).active
    assert ws.max_row == 1
    assert "没有可用标准记录" in workbook(data)["导出说明"]["D2"].value
    assert "CLI" not in str([c.value for c in ws[1]])


def test_text_comparison_preserves_all_lines_and_marks_only_changes():
    ws = workbook(fixture("normalized_diff")).active
    assert [c.value for c in ws[1]] == [
        "设备ID",
        "命令",
        "问题描述",
        "基线 CLI 原文",
        "当前 CLI 原文",
    ]
    assert ws["D2"].value == "Interface GE1"
    assert not ws["D2"].font.color.rgb.endswith("C00000")
    assert ws["D3"].font.color.rgb.endswith("C00000") and ws["E3"].value == "errors 12"
    assert ws["E4"].value == "unchanged"


def test_long_text_cli_not_truncated_and_unlinked_only_has_a_note():
    data = fixture("normalized_diff")
    text = "x" * 40000
    for snapshot in data["snapshots"]:
        snapshot["raw_text"] = text
    ws = workbook(data).active
    assert "".join(ws.cell(r, 5).value or "" for r in range(2, ws.max_row + 1)) == text
    data["definitions"] = {}
    wb = workbook(data)
    assert wb.sheetnames == ["导出说明"]
    assert wb.active.max_row == 2


def test_full_record_sets_do_not_invent_objects_from_state_observations():
    data = fixture()
    for snapshot in data["snapshots"]:
        snapshot["structured_json"] = {"record_sets": {"main": snapshot["structured_json"]}}
        data["observations"].append(
            {
                "snapshot_id": snapshot["id"],
                "entity_key": '["GE2"]',
                "field_name": "input_errors",
                "value_json": 20,
                "observation_type": "input.errors",
            }
        )
    ws = workbook(data).active
    assert ws.max_row == 2
    assert ws["D2"].value == "GE1"
    assert "CLI" not in str([c.value for c in ws[1]])


def test_sheet_name_collisions_are_resolved_without_merging_check_items():
    data = fixture(name="接口/信息")
    data["definitions"]["v2"] = {**data["definitions"]["v1"], "id": "other", "name": "接口:信息"}
    for s in data["snapshots"]:
        s["metadata_json"]["check_item_version_ids"].append("v2")
    assert workbook(data).sheetnames == ["接口_信息", "接口_信息 (2)"]


def test_problem_description_is_identical_to_word_projection():
    from src.core.patrol.report_findings import project_findings
    from src.core.patrol.report_view import project_report

    data = fixture()
    report = {
        "risks": [
            {
                "source": "current_state",
                "device_id": "switch",
                "entity_key": '["GE1"]',
                "field": "input_errors",
                "current_value": 12,
                "evidence_id": "obs",
            }
        ],
        "structured_event_evidence": {
            "items": [
                {
                    "device_id": "switch",
                    "entity_key": '["GE1"]',
                    "command": "display interface",
                    "field": "input_errors",
                    "current_finding": True,
                    "finding_id": "obs",
                    "baseline": {"snapshot_id": "baseline", "raw_text": "GE1 errors 0"},
                    "current": {"snapshot_id": "current", "raw_text": "GE1 errors 12", "value": 12},
                }
            ]
        },
    }
    expected = project_findings(report, {}, {}, project_report(report))["tables"]["findings"][0][2]
    assert workbook(data, report).active["C2"].value == expected


def test_large_counter_remains_exact_and_singleton_is_comparable():
    data = fixture()
    data["definitions"]["v1"]["contract"]["identity_dimensions"] = []
    for snapshot in data["snapshots"]:
        snapshot["structured_json"][0]["input_errors"] = 12345678901234567890
    ws = workbook(data).active
    assert ws["D2"].value == "12345678901234567890"
    assert ws["D2"].data_type == "s"
    assert not ws["C2"].value


def test_missing_thresholds_and_text_baseline_are_notes_not_problem_descriptions():
    data = fixture()
    data["definitions"]["v1"]["contract"]["observations"][0]["health_policy"] = {
        "device_thresholds": {"high": ["device_limit"]}
    }
    wb = workbook(data)
    assert not wb.active["C2"].value
    assert "未采集设备阈值" in wb["导出说明"]["D2"].value
    text = fixture("normalized_diff")
    text["snapshots"] = text["snapshots"][1:]
    wb = workbook(text)
    assert not wb.active["C2"].value
    assert "缺少一期原文" in wb["导出说明"]["D2"].value
