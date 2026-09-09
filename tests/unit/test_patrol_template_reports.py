import asyncio
import json
from contextlib import contextmanager
from io import BytesIO
from zipfile import ZipFile

import pytest
from docx import Document
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from src.core.patrol import report_drafting as ai
from src.core.patrol.report_evidence import build_excerpts, locate_field_value, locate_object
from src.core.patrol.report_renderer import render_report
from src.core.patrol.report_templates import builtin_bytes, controls, validate_template
from src.core.patrol.report_view import project_report


def optical():
    old = "\n".join(
        f"Twenty-FiveGigE1/0/{p} transceiver diagnostic information:\n"
        "[channel] Bias(mA) RX power(dBm) TX power(dBm)\n"
        "1 6.66 -10.21 -1.36\n2 6.46 -1.75 -1.29"
        for p in range(1, 41)
    )
    new = old.replace(
        "Twenty-FiveGigE1/0/39 transceiver diagnostic information:\n"
        "[channel] Bias(mA) RX power(dBm) TX power(dBm)\n1 6.66 -10.21 -1.36",
        "Twenty-FiveGigE1/0/39 transceiver diagnostic information:\n"
        "[channel] Bias(mA) RX power(dBm) TX power(dBm)\n1 6.66 -10.21 -9.36",
    )
    item = {
        "command": "display transceiver diagnosis interface",
        "device_id": "private-device",
        "entity_key": '["Twenty-FiveGigE1/0/39", "1"]',
        "field": "tx_power_dbm",
        "baseline": {"raw_text": old, "snapshot_id": "base", "value": -1.36},
        "current": {"raw_text": new, "snapshot_id": "now", "value": -9.36},
        "event": {"event_id": "event-1", "event_type": "OpticalPowerLow"},
    }
    return {
        "structured_event_evidence": {"items": [item]},
        "metrics": {"target_device_count": 1},
        "assessment": {"status": "需复核", "basis": "数据覆盖有限"},
        "limitations": ["不可断言全部正常"],
    }


def test_exact_interface_channel_excerpt_and_anomaly():
    entry = build_excerpts(optical())[0]
    text = "\n".join(r["current"]["text"] for r in entry["rows"] if r["current"])
    assert "1/0/39" in text and "1/0/38" not in text and "2 6.46" not in text
    assert any((r.get("current") or {}).get("anomaly_span") for r in entry["rows"])
    assert entry["rows"][0]["current"]["line"] > 100
    assert "1/0/40" in entry["full_source"]["current"]


def test_ambiguous_and_recovery_never_guess():
    assert locate_object("port 1\nport 1", '["1"]')[0] == []
    report = optical()
    report["structured_event_evidence"]["items"][0]["event"]["event_type"] = "OpticalRecovered"
    assert not any(
        (r.get("current") or {}).get("anomaly_span") for r in build_excerpts(report)[0]["rows"]
    )


def test_field_change_event_does_not_mark_a_fault():
    report = optical()
    report["structured_event_evidence"]["items"][0]["event"]["event_class"] = "change"
    entry = build_excerpts(report)[0]
    assert any(row["change"] != "equal" for row in entry["rows"])
    assert not any((row.get("current") or {}).get("anomaly_span") for row in entry["rows"])


def test_interface_brief_keeps_header_and_marks_state_column_only():
    raw = "Interface Link Speed\nXGE1/0/1 DOWN auto\nXGE1/0/2 UP auto\nXGE1/0/3 DOWN auto"
    report = {
        "structured_event_evidence": {
            "items": [
                {
                    "current_finding": True,
                    "entity_key": '["XGE1/0/3"]',
                    "field": "oper_status",
                    "current": {"raw_text": raw, "value": "down"},
                }
            ]
        }
    }
    rows = build_excerpts(report)[0]["rows"]
    assert rows[0]["current"]["text"].startswith("Interface Link")
    assert rows[-1]["current"]["anomaly_span"] == [9, 13]
    assert not any("XGE1/0/1" in r["current"]["text"] for r in rows if r["current"])


@pytest.mark.parametrize(
    ("baseline_state", "comparison"),
    [("DOWN", "unchanged"), ("UP", "changed"), (None, "unavailable")],
)
def test_current_finding_distinguishes_equal_changed_and_missing_baseline(
    baseline_state, comparison
):
    header = "Interface Link Speed\n"
    item = {
        "current_finding": True,
        "entity_key": '["XGE1/0/3"]',
        "field": "oper_status",
        "current": {"raw_text": header + "XGE1/0/3 DOWN auto", "value": "down"},
        "baseline": {"raw_text": header + f"XGE1/0/3 {baseline_state} auto"}
        if baseline_state
        else {},
    }
    entry = build_excerpts({"structured_event_evidence": {"items": [item]}})[0]
    assert entry["source_kind"] == "current_finding"
    assert entry["excerpt_comparison"] == comparison
    assert "不代表本期新增变化事件" in entry["note"]
    assert ("两期对象摘录一致" in entry["note"]) == (comparison == "unchanged")
    assert any((r.get("current") or {}).get("anomaly_span") for r in entry["rows"])
    assert not any((r.get("baseline") or {}).get("anomaly_span") for r in entry["rows"])


def test_change_event_is_labelled_separately_from_current_finding():
    entry = build_excerpts(optical())[0]
    assert entry["source_kind"] == "change_event"
    assert entry["excerpt_comparison"] == "changed"
    assert "变化事件：由关联事件列入" in entry["note"]
    assert "当前状态异常：" not in entry["note"]


def test_cli_reference_matches_observation_and_change_without_losing_event_identity():
    report = optical()
    item = report["structured_event_evidence"]["items"][0]
    item["change_id"] = "change-123"
    item["current"]["observation_id"] = "obs-123"
    entry = build_excerpts(report)[0]
    assert {"obs-123", "change-123"} <= set(entry["match_references"])
    assert entry["references"] == [item["event"]["event_id"]]


def test_healthy_numeric_trend_can_show_cli_without_marking_it_as_fault():
    report = optical()
    item = report["structured_event_evidence"]["items"][0]
    item.pop("event")
    item.update(
        numeric_trend=True, trend_explanation="功率下降趋势提醒，当前正常", finding_id="obs"
    )
    entry = build_excerpts(report)[0]
    assert entry["source_kind"] == "numeric_trend"
    assert "当前正常" in entry["note"]
    assert not any((r.get("current") or {}).get("anomaly_span") for r in entry["rows"])


def test_single_interface_optical_columns_disambiguate_identical_values():
    header = "Temp.(C) Voltage(V) Bias(mA) RX power(dBm) TX power(dBm)"
    value = "-2.5 3.26 6.66 -12.5 -12.5"
    rows = [{"current": {"line": 1, "text": header}}, {"current": {"line": 2, "text": value}}]
    candidates = locate_field_value(rows, "tx_power_dbm", "-12.5")
    assert len(candidates) == 1
    assert candidates[0][1][0] == value.rindex("-12.5")
    assert locate_field_value(rows, "tx_power_dbm", "12.5") == []
    assert not any("anomaly_span" in row["current"] for row in rows)


def test_numeric_highlight_preserves_cli_precision_and_exact_field():
    rows = [
        {
            "current": {
                "line": 1,
                "text": "Temp.(C) Voltage(V) Bias(mA) RX power(dBm) TX power(dBm)",
            }
        },
        {"current": {"line": 2, "text": "40 3.20 2.00 -12.00 -2.00"}},
    ]
    matches = locate_field_value(rows, "tx_power_dbm", -2.0)
    assert len(matches) == 1
    row, (start, end) = matches[0]
    assert row["text"][start:end] == "-2.00"
    assert locate_field_value(rows, "tx_power_dbm", 2.0) == []


def test_brief_excerpt_does_not_include_other_non_port_rows_or_next_table():
    raw = (
        "Interface Link Protocol\nMGE0/0/1 DOWN DOWN\nNULL0 UP UP\nVlan2 UP UP\n"
        "Interface Link Speed\nBAGG1 UP 20G\nGE1/0/57 DOWN auto"
    )
    indices, _ = locate_object(raw, '["MGE0/0/1"]')
    assert indices == [0, 1]
    assert [raw.splitlines()[i] for i in indices] == [
        "Interface Link Protocol",
        "MGE0/0/1 DOWN DOWN",
    ]


@pytest.mark.parametrize("data", ["-2.5 3.26", "1 -2.5 3.26"])
def test_module_header_supports_rows_with_and_without_module_identifier(data):
    rows = [
        {"current": {"line": 1, "text": "[module] Temp.(C) Voltage(V)"}},
        {"current": {"line": 2, "text": data}},
    ]
    assert len(locate_field_value(rows, "temperature_celsius", "-2.5")) == 1
    assert len(locate_field_value(rows, "voltage_v", "3.26")) == 1


def test_multiple_events_keep_references():
    report = optical()
    extra = {**report["structured_event_evidence"]["items"][0], "event": {"event_id": "event-2"}}
    report["structured_event_evidence"]["items"].append(extra)
    entries = build_excerpts(report)
    assert len(entries) == 1 and entries[0]["references"] == ["event-1", "event-2"]


def test_word_fills_template_preserves_local_conclusion_and_bundles_full_evidence():
    report = optical()
    blob = render_report(
        builtin_bytes(),
        report,
        drafts={"conclusion.text": "FORBIDDEN", "narrative.summary": "采用的文字"},
        bundle=True,
    )
    with ZipFile(BytesIO(blob)) as z:
        docx = z.read("patrol-report.docx")
        assert "1/0/40" in z.read("evidence/CLI-001-current.txt").decode()
        assert "observations.csv" in z.namelist()
    assert not controls(docx)
    doc = Document(BytesIO(docx))
    text = "\n".join(p.text for p in doc.paragraphs) + "\n".join(
        c.text for t in doc.tables for r in t.rows for c in r.cells
    )
    assert "FORBIDDEN" not in text and "采用的文字" in text and "需复核" in text
    assert "1/0/40" not in text


def test_builtin_upload_and_external_relationship_rejected():
    content = builtin_bytes()
    assert validate_template(content)
    altered = BytesIO()
    with ZipFile(BytesIO(content)) as source, ZipFile(altered, "w") as target:
        for name in source.namelist():
            raw = source.read(name)
            if name == "word/_rels/document.xml.rels":
                raw = raw.replace(
                    b"</Relationships>",
                    b'<Relationship Id="evil" Type="hyperlink" Target="https://example.com" '
                    b'TargetMode="External"/></Relationships>',
                )
            target.writestr(name, raw)
    with pytest.raises(ValueError, match="外部链接"):
        validate_template(altered.getvalue())


def test_observations_are_grouped_without_inventing_counters():
    obs = [
        {
            "device_id": "a",
            "snapshot_id": "s",
            "entity_type": "interface",
            "entity_key": '["GE1/0/1"]',
            "field": "input_errors",
            "value": 12,
        },
        {
            "device_id": "a",
            "snapshot_id": "s",
            "entity_type": "interface",
            "entity_key": '["GE1/0/1"]',
            "field": "output_errors",
            "value": 13,
        },
    ]
    view = project_report({}, {"observations": obs})
    assert len(view["tables"]["evidence"]) == 1
    assert view["tables"]["interface_counters"][0][-1] is None


def test_ai_packet_does_not_copy_private_free_text_and_budget_is_bounded():
    report = optical()
    report.update(
        risks=[{"domain": "SECRET-IP-10.0.0.1", "severity": "SECRET", "criterion": "SECRET"}]
        * 10000,
        title="SECRET",
        unstructured_comparison={"items": [{"raw_text": "SECRET" * 100000}]},
    )
    packet = ai.build_packet(report, list(ai.ALLOWED_SECTIONS))
    body = ai.request_body(packet, {"model": "test", "provider": "openai"})
    assert "SECRET" not in ai.encoded(body) and "private-device" not in ai.encoded(body)
    assert sum(len(m["content"].encode()) for m in body["messages"]) <= 6000
    with pytest.raises(ValueError):
        ai.build_packet(report, ["conclusion.text"])


@pytest.fixture
def drafting_db(monkeypatch):
    from src.core.patrol.report_models import PatrolReportDraft
    from src.infrastructure.db import postgres

    engine = create_engine("sqlite://")
    PatrolReportDraft.__table__.create(engine)

    @contextmanager
    def session():
        with Session(engine) as db:
            yield db
            db.commit()

    monkeypatch.setattr(postgres, "get_db_session", session)
    monkeypatch.setattr(
        ai,
        "provider_config",
        lambda: {
            "provider": "openai",
            "model": "test",
            "base_url": "https://example.invalid/v1",
            "api_key": "test",
        },
    )
    return session


def test_consent_scope_staleness_idempotency_and_manual_adoption(drafting_db, monkeypatch):
    report = optical()
    calls = []

    async def model(body, config):
        calls.append(body)
        return json.dumps(
            {
                "sections": [
                    {
                        "id": "narrative.summary",
                        "paragraphs": [
                            {
                                "text": "本次目标设备 1 台，覆盖有限，业务影响需人工核实。",
                                "fact_ids": ["F001", "F002", "F003"],
                            }
                        ],
                    }
                ]
            },
            ensure_ascii=False,
        )

    monkeypatch.setattr(ai, "call_model", model)
    preview = ai.prepare_draft(report, "job", "owner", "t", "hash", ["narrative.summary"])
    assert calls == []
    with pytest.raises(PermissionError):
        asyncio.run(
            ai.generate_draft(preview["id"], "job", "other", preview["digest"], report, "hash")
        )
    with pytest.raises(ValueError):
        asyncio.run(
            ai.generate_draft(
                preview["id"],
                "job",
                "owner",
                preview["digest"],
                {**report, "changed": True},
                "hash",
            )
        )
    result = asyncio.run(
        ai.generate_draft(preview["id"], "job", "owner", preview["digest"], report, "hash")
    )
    assert result["state"] == "ready"
    asyncio.run(ai.generate_draft(preview["id"], "job", "owner", preview["digest"], report, "hash"))
    assert len(calls) == 1
    with pytest.raises(ValueError):
        ai.accepted_text(preview["id"], "job", "owner", report, "t", "hash")
    ai.accept_draft(preview["id"], "job", "owner", report, "hash")
    assert (
        "1 台"
        in ai.accepted_text(preview["id"], "job", "owner", report, "t", "hash")["narrative.summary"]
    )


def test_model_cannot_write_conclusions_or_invent_numbers():
    packet = ai.build_packet(optical(), ["narrative.summary"])
    for identifier, text in [("conclusion.text", "正常"), ("narrative.summary", "共有 999 台正常")]:
        with pytest.raises(ValueError):
            ai.validate_output(
                json.dumps(
                    {
                        "sections": [
                            {
                                "id": identifier,
                                "paragraphs": [
                                    {"text": text, "fact_ids": ["F001", "F002", "F003"]}
                                ],
                            }
                        ]
                    }
                ),
                packet,
            )


def test_imported_columns_keep_control_meaning_after_reordering():
    from docx.oxml.ns import qn

    doc = Document(BytesIO(builtin_bytes()))
    table = doc.tables[0]
    for row in table.rows:
        cells = row._tr.findall(qn("w:tc"))
        row._tr.remove(cells[2])
        row._tr.append(cells[2])
    modified = BytesIO()
    doc.save(modified)
    assert validate_template(modified.getvalue())
    report = {"device_inventory_rows": [["1", "device", "model", "vendor"]]}
    rendered = Document(BytesIO(render_report(modified.getvalue(), report)))
    assert [c.text for c in rendered.tables[0].rows[1].cells] == [
        "device", "model", "未确认机框序列号", "vendor"
    ]


def test_expired_consent_and_model_failure_do_not_send_or_approve(drafting_db, monkeypatch):
    from datetime import datetime, timedelta, timezone

    from src.core.patrol.report_models import PatrolReportDraft

    report = optical()
    preview = ai.prepare_draft(report, "job", "owner", "t", "hash", ["narrative.summary"])
    with drafting_db() as db:
        db.get(PatrolReportDraft, preview["id"]).created_at = datetime.now(
            timezone.utc
        ) - timedelta(minutes=16)
    with pytest.raises(ValueError, match="失效"):
        asyncio.run(
            ai.generate_draft(preview["id"], "job", "owner", preview["digest"], report, "hash")
        )
    fresh = ai.prepare_draft(report, "job", "owner", "t", "hash", ["narrative.summary"])

    async def failing(*args):
        raise RuntimeError("sensitive provider diagnostics")

    monkeypatch.setattr(ai, "call_model", failing)
    result = asyncio.run(
        ai.generate_draft(fresh["id"], "job", "owner", fresh["digest"], report, "hash")
    )
    assert result["state"] == "failed" and "sensitive" not in str(result)
    with pytest.raises(ValueError):
        ai.accept_draft(fresh["id"], "job", "owner", report, "hash")


def test_migration_creates_and_removes_only_report_tables():
    import importlib.util
    from pathlib import Path

    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import inspect, text

    path = Path(__file__).parents[2] / "migrations/versions/0101_patrol_report_templates.py"
    spec = importlib.util.spec_from_file_location("report_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE existing_data (id INTEGER PRIMARY KEY)"))
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
            assert "netops_patrol_report_drafts" in inspect(connection).get_table_names()
            assert len(inspect(connection).get_indexes("netops_patrol_report_drafts")) == 2
            migration.downgrade()
        assert inspect(connection).get_table_names() == ["existing_data"]
