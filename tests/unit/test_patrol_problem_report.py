import re
from io import BytesIO

from docx import Document

from src.core.patrol.report_findings import project_findings
from src.core.patrol.report_renderer import render_report
from src.core.patrol.report_templates import builtin_bytes
from src.core.patrol.report_view import project_report


def text_of(blob):
    doc = Document(BytesIO(blob))
    return (
        "\n".join(p.text for p in doc.paragraphs)
        + "\n"
        + "\n".join(c.text for t in doc.tables for r in t.rows for c in r.cells)
    )


def test_problem_template_omits_normal_measurements_and_does_not_claim_health():
    report = {
        "assessment": {"status": "无法判断", "basis": "阈值未提供"},
        "domains": [{"label": "CPU", "status": "数据不完整", "reason": "未提供阈值"}],
        "limitations": ["采样范围不完整"],
    }
    source = {
        "observations": [
            {
                "device_id": "device",
                "entity_type": "cpu_scope",
                "entity_key": '["1"]',
                "field": "FIVE_SEC",
                "value": 87654321,
            }
        ]
    }
    text = text_of(render_report(builtin_bytes(), report, source))
    assert "87654321" not in text and "FIVE_SEC" not in text
    assert "数据缺口" in text and "无法判断" in text and "采样范围不完整" in text
    assert "xx项目" not in text and "西安" not in text and "合同" not in text
    assert "备份验证完成。" in text  # explicitly not verified


def test_continuing_fault_recovery_and_evidence_are_distinct_without_visible_ids():
    baseline = "00000000-1111-2222-3333-444444444444"
    current = "99999999-8888-7777-6666-555555555555"
    raw = "Interface Link\nXGE1/0/1 DOWN\nXGE1/0/2 UP"
    report = {
        "assessment": {"status": "发现异常", "basis": "当前一项异常"},
        "risks": [
            {
                "source": "current_state",
                "device_id": "switch",
                "entity_key": '["XGE1/0/1"]',
                "field": "oper_status",
                "current_value": "DOWN",
                "evidence_id": "obs-1",
            }
        ],
        "numeric_trends": {
            "summary": "一项恢复",
            "items": [
                {
                    "status": "recovered",
                    "device_id": "switch",
                    "entity_key": '["XGE1/0/3"]',
                    "observation_id": "obs-2",
                    "label": "发射光功率",
                    "baseline_value": -32,
                    "current_value": -2,
                    "unit": "dBm",
                    "explanation": "回到阈值范围",
                }
            ],
        },
        "structured_event_evidence": {
            "items": [
                {
                    "current_finding": True,
                    "device_id": "switch",
                    "command": "display interface brief",
                    "entity_key": '["XGE1/0/1"]',
                    "field": "oper_status",
                    "finding_id": "obs-1",
                    "baseline": {"snapshot_id": baseline, "raw_text": raw, "value": "DOWN"},
                    "current": {
                        "snapshot_id": current,
                        "raw_text": raw,
                        "value": "DOWN",
                        "observation_id": "obs-1",
                    },
                },
                {
                    "device_id": "switch",
                    "entity_key": '["XGE1/0/3"]',
                    "field": "tx_power_dbm",
                    "event": {"event_id": "event-2", "event_class": "recovery"},
                    "baseline": {"value": -32},
                    "current": {"value": -2, "observation_id": "obs-2"},
                },
            ]
        },
    }
    view = project_findings(report, {}, {}, project_report(report))
    assert [r[0] for r in view["tables"]["findings"]] == ["当前异常", "已恢复"]
    assert view["tables"]["findings"][0][-1] == "CLI-001"
    text = text_of(render_report(builtin_bytes(), report))
    assert baseline not in text and current not in text
    assert "event-2" not in text and "obs-1" not in text
    assert "XGE1/0/2" not in text
    assert "1 项风险、1 项恢复" in text
    assert not re.search(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", text)


def test_component_serial_is_not_used_as_chassis_serial():
    report = {"device_inventory_rows": [["1", "switch", "model", "vendor"]]}
    source = {
        "observations": [
            {
                "device_id": "switch",
                "entity_type": "hardware_component",
                "entity_key": '["Fan 1"]',
                "field": "serial_number",
                "value": "FAN-SERIAL",
            }
        ]
    }
    text = text_of(render_report(builtin_bytes(), report, source))
    assert "FAN-SERIAL" not in text and "未确认机框序列号" in text
