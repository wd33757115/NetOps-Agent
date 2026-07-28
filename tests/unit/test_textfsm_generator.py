from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "src" / "skills" / "textfsm-generator" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from generate_templates import (  # noqa: E402
    _process_simple_job,
    generate_templates,
)
from llm_generator import TextFSMToolTurn  # noqa: E402
from parser_model import canonicalize_model_fields, strip_model_template  # noqa: E402
from src.core.parser_generation import ParserReviewReport  # noqa: E402
from spec_suggester import (  # noqa: E402
    _enrich_structural_spec,
    _nested_record_layout,
    suggest_parser_spec,
)


def _job() -> dict:
    return {
        "id": "simple-job-1",
        "pipeline_version": "simple_v2",
        "generation_strategy": "deterministic_fallback",
        "vendor": "H3C",
        "model": "S5130",
        "profile": "S5130",
        "family": None,
        "canonical_command": "display interface brief",
        "parser_spec_sha256": "spec-sha",
        "parser_spec_json": {
            "entity": "interface",
            "primary_keys": ["interface"],
            "fields": {
                "interface": {
                    "required": True,
                    "type": "interface_name",
                    "source_labels": ["Interface"],
                },
                "status": {
                    "required": True,
                    "type": "enum",
                    "values": ["UP", "DOWN"],
                    "source_labels": ["Link"],
                },
            },
        },
        "generation_readiness": {"ready": True, "blockers": []},
        "structure_variant_count": 1,
        "structure_signatures": ["layout-1"],
        "samples": [
            {
                "id": "sample-generation",
                "snapshot_id": "snapshot-generation",
                "sample_role": "generation",
                "expectation_json": {
                    "record_count": 1,
                    "primary_keys": [["GE1/0/1"]],
                },
                "sanitization_status": "sanitized",
                "sanitized_text": "Interface Link\nGE1/0/1 UP\n",
            },
            {
                "id": "sample-acceptance",
                "snapshot_id": "snapshot-acceptance",
                "sample_role": "acceptance",
                "expectation_json": {
                    "record_count": 1,
                    "primary_keys": [["GE1/0/1"]],
                },
                "sanitization_status": "sanitized",
                "sanitized_text": "must-not-enter-model",
            },
        ],
    }


@pytest.fixture(autouse=True)
def _stub_independent_reviewer(monkeypatch):
    class Reviewer:
        def review(self, **_kwargs):
            return ParserReviewReport(
                passed=True,
                summary="independent review passed",
                primary_key_assessment={"passed": True},
                completeness_assessment={"passed": True},
            )

    monkeypatch.setattr(
        "generate_templates.IndependentParserResultReviewer",
        lambda: Reviewer(),
    )


def test_model_output_is_stripped_and_field_names_are_canonicalized():
    raw = """Explanation
```textfsm
Value Required INTERFACE (\\S+)
Value Status (\\S+)
Value FreeRatio (\\S+)

Start
^${INTERFACE}\\s+${Status}\\s+${FreeRatio} -> Record
```
"""
    cleaned = canonicalize_model_fields(
        strip_model_template(raw),
        ("interface", "status", "free_ratio"),
    )
    assert "Explanation" not in cleaned
    assert "Required" not in cleaned
    assert "Value interface" in cleaned
    assert "Value status" in cleaned
    assert "${interface}" in cleaned
    assert "${status}" in cleaned
    assert "Value free_ratio" in cleaned
    assert "${free_ratio}" in cleaned


@patch("parser_model.create_textfsm_tool_session")
def test_simple_job_uses_deterministic_pipeline_without_llm(mock_session, tmp_path):
    job = _job()
    contexts = {
        snapshot_id: {
            "mode": "parser_validation",
            "snapshot": SimpleNamespace(
                snapshot_id=snapshot_id,
                raw_text="Interface Link\nGE1/0/1 UP\n",
            ),
        }
        for snapshot_id in ("snapshot-generation", "snapshot-acceptance")
    }
    attempts: list[dict] = []
    with patch("generate_templates.NtcTemplateResolver") as ntc_resolver:
        ntc_resolver.return_value.resolve.return_value = (None, [])
        with patch("generate_templates.PatrolRepository") as patrol_repository:
            patrol_repository.return_value.get_snapshot_context.side_effect = contexts.get
            with patch("generate_templates.ParserJobRepository") as job_repository:
                job_repository.return_value.create_generation_attempt.side_effect = (
                    lambda **payload: (
                        attempts.append(payload) or {"id": f"attempt-{len(attempts)}"}
                    )
                )
                report = _process_simple_job(
                    job,
                    reports_dir=tmp_path / "reports",
                    publish=False,
                    dry_run=True,
                )

    assert report.accepted is True
    assert report.template_generated is False
    assert report.agent_loop["pipeline_version"] == "simple_v2"
    assert report.agent_loop["model_calls"] == 0
    assert [item["action"] for item in attempts] == ["deterministic:table"]
    mock_session.assert_not_called()


@patch("parser_model.create_textfsm_tool_session")
def test_context_table_job_uses_deterministic_pipeline_without_llm(
    mock_session,
    tmp_path,
):
    raw = """Memory statistics are measured in KB:
Slot 1:
             Total      Used      Free    Shared   Buffers    Cached   FreeRatio
Mem:       1946044   1034320    911724         0      4860    209388       47.4%
-/+ Buffers/Cache:    820072   1125972
Swap:            0         0         0
"""
    job = {
        **_job(),
        "canonical_command": "inspect memory",
        "parser_spec_json": {
            "entity": "memory_statistics",
            "primary_keys": ["slot"],
            "fields": {
                "slot": {"required": True, "type": "text", "source_labels": ["Slot"]},
                "total": {"required": True, "type": "number", "source_labels": ["Total"]},
                "used": {"required": True, "type": "number", "source_labels": ["Used"]},
                "free": {"required": True, "type": "number", "source_labels": ["Free"]},
                "shared": {
                    "required": True,
                    "type": "number",
                    "source_labels": ["Shared"],
                },
                "buffers": {
                    "required": True,
                    "type": "number",
                    "source_labels": ["Buffers"],
                },
                "cached": {
                    "required": True,
                    "type": "number",
                    "source_labels": ["Cached"],
                },
                "free_ratio": {
                    "required": True,
                    "type": "percent",
                    "source_labels": ["FreeRatio"],
                },
            },
        },
        "samples": [
            {
                "id": "sample-memory",
                "snapshot_id": "snapshot-memory",
                "sample_role": "generation",
                "expectation_json": {
                    "record_count": 1,
                    "primary_keys": [["1"]],
                },
                "sanitization_status": "sanitized",
                "sanitized_text": raw,
            }
        ],
    }
    attempts: list[dict] = []
    context = {
        "mode": "parser_validation",
        "snapshot": SimpleNamespace(snapshot_id="snapshot-memory", raw_text=raw),
    }
    with patch("generate_templates.PatrolRepository") as patrol_repository:
        patrol_repository.return_value.get_snapshot_context.return_value = context
        with patch("generate_templates.ParserJobRepository") as job_repository:
            job_repository.return_value.create_generation_attempt.side_effect = lambda **payload: (
                attempts.append(payload) or {"id": f"attempt-{len(attempts)}"}
            )
            report = _process_simple_job(
                job,
                reports_dir=tmp_path / "reports",
                publish=False,
                dry_run=True,
            )

    assert report.accepted is True
    assert report.compile_success is True
    assert report.record_count == 1
    assert attempts[0]["action"] == "deterministic:table"
    assert "Value Filldown slot" in attempts[0]["template_text"]
    assert report.parsed_records[0]["free_ratio"] == "47.4%"
    mock_session.assert_not_called()


@patch("parser_model.create_textfsm_tool_session")
def test_failed_report_distinguishes_compilation_from_parse_validation(
    mock_session,
    tmp_path,
):
    job = _job()
    job["samples"] = [dict(job["samples"][0])]
    job["samples"][0]["sanitized_text"] = "UNMATCHED OUTPUT"
    template = """Value interface (\\S+)
Value status (\\S+)

Start
  ^NEVER\\s+${interface}\\s+${status} -> Record
"""

    class FailedSession:
        def next(self, _feedback=None):
            return TextFSMToolTurn(
                candidate_text=template,
                submission_mode="native_tool_call",
                model_name="deepseek-v4-pro",
                token_usage={},
                latency_ms=1,
            )

    mock_session.return_value = FailedSession()

    class RejectingReviewer:
        def review(self, **_kwargs):
            return ParserReviewReport(
                passed=False,
                summary="Positive data sample produced no records.",
                diagnostics=(
                    {
                        "code": "missing_records",
                        "sample_id": "sample-generation",
                        "target": "record_scope",
                        "message": "positive sample produced no records",
                    },
                ),
                primary_key_assessment={"passed": False},
                completeness_assessment={"passed": False},
            )

    attempts: list[dict] = []
    context = {
        "mode": "parser_validation",
        "snapshot": SimpleNamespace(
            snapshot_id="snapshot-generation",
            raw_text="UNMATCHED OUTPUT",
        ),
    }
    with patch("generate_templates.NtcTemplateResolver") as ntc_resolver:
        ntc_resolver.return_value.resolve.return_value = (None, [])
        with patch(
            "generate_templates.IndependentParserResultReviewer",
            lambda: RejectingReviewer(),
        ), patch("generate_templates.PatrolRepository") as patrol_repository:
            patrol_repository.return_value.get_snapshot_context.return_value = context
            with patch("generate_templates.ParserJobRepository") as job_repository:
                repository = job_repository.return_value
                model_calls: list[dict] = []
                repository.reserve_model_call.side_effect = lambda job_id, **payload: (
                    model_calls.append({"job_id": job_id, **payload})
                    or {
                        "id": f"call-{len(model_calls)}",
                        "call_number": len(model_calls),
                        "generation_run_id": payload["generation_run_id"],
                    }
                )
                repository.complete_model_call.return_value = {}
                repository.create_generation_attempt.side_effect = lambda **payload: (
                    attempts.append(payload) or {"id": f"attempt-{len(attempts)}"}
                )
                report = _process_simple_job(
                    job,
                    reports_dir=tmp_path / "reports",
                    publish=False,
                    dry_run=True,
                )

    assert report.accepted is False
    assert report.compile_success is True
    assert report.passed_samples == 1
    assert any("positive sample produced no records" in item for item in report.errors)
    assert len(attempts) == 3


def test_generation_requires_job_id_before_any_files_are_written(tmp_path):
    with pytest.raises(ValueError, match="job_id is required"):
        generate_templates(
            {
                "source_path": str(tmp_path / "legacy-input"),
                "reports_dir": str(tmp_path / "reports"),
            }
        )
    assert not (tmp_path / "reports").exists()


def test_legacy_job_cannot_execute_even_when_requested(monkeypatch):
    class Repository:
        def get_job(self, *_args, **_kwargs):
            return {"id": "legacy", "pipeline_version": "legacy"}

    monkeypatch.setattr("generate_templates.ParserJobRepository", Repository)
    with pytest.raises(ValueError, match="legacy Parser jobs are read-only"):
        generate_templates({"job_id": "legacy"})


def test_spec_suggester_classifies_valid_empty_without_calling_llm(monkeypatch):
    recorded: list[dict] = []

    class Repository:
        def record_parser_spec_suggestion(self, job_id, **payload):
            recorded.append({"job_id": job_id, **payload})

        def get_job(self, job_id, **_kwargs):
            return {
                "id": job_id,
                "vendor": "H3C",
                "profile": "S5130",
                "canonical_command": "display m-lag system",
                "samples": [
                    {
                        "id": "sample-1",
                        "sanitization_status": "sanitized",
                        "sanitized_text": "M-LAG is not configured.",
                    }
                ],
            }

    llm = SimpleNamespace()
    llm.call = lambda *_args, **_kwargs: (_ for _ in ()).throw(
        AssertionError("valid-empty classification must not call the LLM")
    )
    monkeypatch.setattr("spec_suggester.ParserJobRepository", Repository)
    monkeypatch.setattr("spec_suggester.call_llm", llm.call)

    result = suggest_parser_spec(
        {
            "job_id": "job-empty",
            "suggestion_task_id": "task-1",
        }
    )

    assert result["success"] is True
    assert result["suggestion"]["classification"] == "no_positive_data"
    assert result["suggestion"]["sample_suggestions"][0]["recommended_role"] == "negative"
    assert [item["status"] for item in recorded] == ["running", "ready"]


def test_spec_suggester_normalizes_and_validates_a_weaker_model_response(monkeypatch):
    recorded: list[dict] = []

    class Repository:
        def record_parser_spec_suggestion(self, job_id, **payload):
            recorded.append({"job_id": job_id, **payload})

        def get_job(self, job_id, **_kwargs):
            return {
                "id": job_id,
                "vendor": "H3C",
                "profile": "S5130",
                "canonical_command": "display environment",
                "samples": [
                    {
                        "id": "sample-data",
                        "sanitization_status": "sanitized",
                        "sanitized_text": ("Slot Sensor Temperature Warning\n1 hotspot-1 36 65\n"),
                    }
                ],
            }

    model_response = json.dumps(
        {
            "parser_spec": {
                "entity": "Temperature Sensor",
                "primary_keys": "sensor",
                "fields": [
                    {
                        "name": "sensor",
                        "required": False,
                        "type": "string",
                        "canonical_candidate": "environment.sensor.5m",
                    },
                    {"name": "temperature", "required": True, "type": "integer"},
                ],
            },
            "sample_plans": [
                {
                    "sample_id": "sample-data",
                    "target_section": "temperature sensor table",
                    "record_unit": "one sensor row",
                    "record_start": "slot and sensor columns",
                    "record_end": "end of physical row",
                    "exclude_sections": [],
                    "ambiguities": [],
                }
            ],
            "confidence": 0.7,
            "rationale": "One row per sensor.",
        }
    )
    monkeypatch.setattr("spec_suggester.ParserJobRepository", Repository)
    monkeypatch.setattr("spec_suggester.call_llm", lambda *_args, **_kwargs: model_response)

    result = suggest_parser_spec({"job_id": "job-data", "suggestion_task_id": "task-data"})

    spec = result["suggestion"]["parser_spec"]
    assert spec["entity"] == "temperature_sensor"
    assert spec["primary_keys"] == ["sensor"]
    assert spec["fields"]["sensor"] == {"required": True, "type": "text"}
    assert spec["fields"]["temperature"]["type"] == "number"
    assert result["suggestion"]["normalization_warnings"] == [
        "ignored invalid canonical_candidate for sensor: environment.sensor.5m"
    ]
    assert recorded[-1]["status"] == "ready"
    assert recorded[-1]["auto_adopt"] is True


def test_spec_suggester_keeps_record_plan_advisory_without_compiler_derived_expectations(
    monkeypatch,
):
    recorded: list[dict] = []

    class Repository:
        def record_parser_spec_suggestion(self, job_id, **payload):
            recorded.append({"job_id": job_id, **payload})

        def get_job(self, job_id, **_kwargs):
            return {
                "id": job_id,
                "vendor": "Cisco",
                "profile": "N9K-C93180YC-FX",
                "canonical_command": "show interface status",
                "samples": [
                    {
                        "id": "sample-table",
                        "sanitization_status": "sanitized",
                        "sanitized_text": (
                            "Port          Status       Vlan\n"
                            "Eth1/1        connected    10\n"
                            "Eth1/2        notconnect   20\n"
                        ),
                    }
                ],
            }

    model_response = json.dumps(
        {
            "parser_spec": {
                "entity": "interface",
                "primary_keys": ["interface"],
                "fields": {
                    "interface": {
                        "required": True,
                        "type": "interface",
                        "source_labels": ["Port"],
                    },
                    "status": {
                        "required": True,
                        "type": "string",
                        "source_labels": ["Status"],
                    },
                    "vlan": {
                        "required": True,
                        "type": "string",
                        "source_labels": ["Vlan"],
                    },
                },
            },
            "sample_plans": [
                {
                    "sample_id": "sample-table",
                    "target_section": "interface status table",
                    "record_unit": "one interface row",
                    "record_start": "interface name",
                    "record_end": "end of physical row",
                    "exclude_sections": ["table heading"],
                    "ambiguities": [],
                }
            ],
            "confidence": 0.6,
            "rationale": "One row per interface.",
        }
    )
    monkeypatch.setattr("spec_suggester.ParserJobRepository", Repository)
    monkeypatch.setattr("spec_suggester.call_llm", lambda *_args, **_kwargs: model_response)

    result = suggest_parser_spec({"job_id": "job-table", "suggestion_task_id": "task-table"})

    sample = result["suggestion"]["sample_suggestions"][0]
    assert "expectation" not in sample
    assert sample["semantic_plan"]["record_unit"] == "one interface row"
    assert sample["semantic_plan"]["exclude_sections"] == ["table heading"]
    assert recorded[-1]["status"] == "ready"


def test_spec_suggester_preserves_llm_scope_plan_for_summary_plus_table_layout(
    monkeypatch,
):
    recorded: list[dict] = []
    sample_text = """Domain id                        : 300
Peer status                      : peer formed

Member status
-----------------------------------------------------------------------
Id    Port          Status Consistency Reason       Active ranges
--    ------------  ------ ----------- ------------ -------------
400   Po400         up     success     success      1-20
401   Po401         up     success     success      50

402   Po402         up     success     success      60-70
"""

    class Repository:
        def record_parser_spec_suggestion(self, job_id, **payload):
            recorded.append({"job_id": job_id, **payload})

        def get_job(self, job_id, **_kwargs):
            return {
                "id": job_id,
                "vendor": "Generic",
                "profile": "Switch",
                "canonical_command": "inspect redundancy",
                "samples": [
                    {
                        "id": "sample-summary-table",
                        "sanitization_status": "sanitized",
                        "sanitized_text": sample_text,
                    }
                ],
            }

    weak_response = json.dumps(
        {
            "parser_spec": {
                "entity": "redundancy_member",
                "primary_keys": ["member_id"],
                "fields": {
                    "member_id": {
                        "required": True,
                        "type": "number",
                        "source_labels": ["Id"],
                    },
                    "port": {
                        "required": True,
                        "type": "interface",
                        "source_labels": ["Port"],
                    },
                    "status": {
                        "required": True,
                        "type": "string",
                        "source_labels": ["Status"],
                    },
                    "consistency": {
                        "required": True,
                        "type": "string",
                        "source_labels": ["Consistency"],
                    },
                    "reason": {
                        "required": True,
                        "type": "string",
                        "source_labels": ["Reason"],
                    },
                    "active_ranges": {
                        "required": True,
                        "type": "string",
                        "source_labels": ["Active ranges"],
                    },
                    "domain_id": {
                        "required": True,
                        "type": "number",
                        "source_labels": ["Domain id"],
                    },
                    "peer_status": {
                        "required": True,
                        "type": "string",
                        "source_labels": ["Peer status"],
                    },
                },
            },
            "sample_plans": [
                {
                    "sample_id": "sample-summary-table",
                    "target_section": "Member status",
                    "record_unit": "one member row",
                    "record_start": "numeric member id",
                    "record_end": "end of member row",
                    "exclude_sections": ["domain summary"],
                    "ambiguities": [],
                }
            ],
            "confidence": 0.7,
            "rationale": "One record per member row.",
        }
    )
    monkeypatch.setattr("spec_suggester.ParserJobRepository", Repository)
    monkeypatch.setattr("spec_suggester.call_llm", lambda *_args, **_kwargs: weak_response)

    result = suggest_parser_spec(
        {"job_id": "job-summary-table", "suggestion_task_id": "task-summary-table"}
    )

    sample = result["suggestion"]["sample_suggestions"][0]
    assert "expectation" not in sample
    assert sample["semantic_plan"]["target_section"] == "Member status"
    assert sample["semantic_plan"]["exclude_sections"] == ["domain summary"]
    assert recorded[-1]["status"] == "ready"


def test_spec_suggester_does_not_silently_rewrite_llm_contract_with_command_heuristics(
    monkeypatch,
):
    recorded: list[dict] = []
    sample_text = """Ethernet1/1
    transceiver is not present

Ethernet1/2
    transceiver is present
           SFP Detail Diagnostics Information (internal calibration)
                Current              Alarms                  Warnings
                Measurement     High        Low         High          Low
  Tx Power      -6.61 dBm       0.99 dBm  -13.56 dBm   -3.00 dBm     -9.50 dBm
  Rx Power      -5.60 dBm       3.95 dBm  -21.54 dBm    0.00 dBm    -17.21 dBm

Ethernet1/3
    transceiver is present
Lane Number:1 Network Lane
           SFP Detail Diagnostics Information (internal calibration)
                Current              Alarms                  Warnings
                Measurement     High        Low         High          Low
  Tx Power      -2.94 dBm       1.99 dBm  -11.61 dBm   -1.00 dBm     -7.61 dBm
  Rx Power      -2.02 dBm       3.39 dBm  -13.56 dBm    2.39 dBm     -9.50 dBm
Lane Number:2 Network Lane
           SFP Detail Diagnostics Information (internal calibration)
                Current              Alarms                  Warnings
                Measurement     High        Low         High          Low
  Tx Power      -3.15 dBm       1.99 dBm  -11.61 dBm   -1.00 dBm     -7.61 dBm
  Rx Power      -2.36 dBm       3.39 dBm  -13.56 dBm    2.39 dBm     -9.50 dBm
"""

    class Repository:
        def record_parser_spec_suggestion(self, job_id, **payload):
            recorded.append({"job_id": job_id, **payload})

        def get_job(self, job_id, **_kwargs):
            return {
                "id": job_id,
                "vendor": "Cisco",
                "profile": "N9K",
                "canonical_command": "show optics detail",
                "samples": [
                    {
                        "id": "sample-optics",
                        "sanitization_status": "sanitized",
                        "sanitized_text": sample_text,
                    }
                ],
            }

    weak_response = json.dumps(
        {
            "parser_spec": {
                "entity": "transceiver",
                "primary_keys": ["interface"],
                "fields": {
                    "interface": {
                        "required": True,
                        "type": "interface",
                        "source_labels": ["Ethernet1/1", "Ethernet1/2", "Ethernet1/3"],
                    },
                    "tx_power_dbm": {
                        "required": False,
                        "type": "number",
                        "source_labels": ["Tx Power"],
                    },
                    "rx_power_dbm": {
                        "required": False,
                        "type": "number",
                        "source_labels": ["Rx Power"],
                    },
                },
            },
            "sample_plans": [
                {
                    "sample_id": "sample-optics",
                    "target_section": "transceiver diagnostics",
                    "record_unit": "one interface block",
                    "record_start": "interface heading",
                    "record_end": "next interface heading",
                    "exclude_sections": [],
                    "ambiguities": ["lane data may require a composite key"],
                }
            ],
        }
    )
    monkeypatch.setattr("spec_suggester.ParserJobRepository", Repository)
    monkeypatch.setattr("spec_suggester.call_llm", lambda *_args, **_kwargs: weak_response)

    result = suggest_parser_spec({"job_id": "job-optics", "suggestion_task_id": "task-optics"})

    suggestion = result["suggestion"]
    spec = suggestion["parser_spec"]
    assert spec["primary_keys"] == ["interface"]
    assert "channel" not in spec["fields"]
    assert "rx_power_low_warning_dbm" not in spec["fields"]
    assert suggestion["sample_suggestions"][0]["semantic_plan"]["ambiguities"] == [
        "lane data may require a composite key"
    ]
    assert recorded[-1]["status"] == "ready"


def test_nested_layout_prefers_child_repeated_inside_parent_block():
    raw_text = """Ethernet1/1
    transceiver is present
    Note: calibrated values
    Note: thresholds are module supplied
    SFP Detail Diagnostics Information (internal calibration)
Ethernet1/2
    transceiver is present
    Note: calibrated values
Lane Number:1
    Rx Power -2.10 dBm -1.00 dBm -15.00 dBm -2.00 dBm -13.00 dBm
Lane Number:2
    Rx Power -2.20 dBm -1.00 dBm -15.00 dBm -2.00 dBm -13.00 dBm
"""

    layout = _nested_record_layout(raw_text)

    assert layout is not None
    keys, labels = layout
    assert keys == [
        ("Ethernet1/1", "SFP"),
        ("Ethernet1/2", "1"),
        ("Ethernet1/2", "2"),
    ]
    assert "Lane Number:" in labels


def test_structural_enrichment_does_not_duplicate_existing_grouped_thresholds():
    spec = {
        "entity": "measurement",
        "primary_keys": ["interface"],
        "fields": {
            "interface": {
                "required": True,
                "type": "interface_name",
                "source_labels": ["Ethernet1/1"],
            },
            "rx_power_current": {
                "required": False,
                "type": "number",
                "source_labels": ["Rx Power"],
            },
            "rx_power_alarm_high": {
                "required": False,
                "type": "number",
                "source_labels": ["Rx Power High Alarm"],
            },
            "rx_power_alarm_low": {
                "required": False,
                "type": "number",
                "source_labels": ["Rx Power Low Alarm"],
            },
            "rx_power_warning_high": {
                "required": False,
                "type": "number",
                "source_labels": ["Rx Power High Warning"],
            },
            "rx_power_warning_low": {
                "required": False,
                "type": "number",
                "source_labels": ["Rx Power Low Warning"],
            },
            "rx_power_current_low_warning_dbm": {
                "required": False,
                "type": "number",
                "source_labels": ["Rx Power/Warnings/Low"],
            },
        },
        "allow_empty": False,
        "empty_patterns": [],
    }
    sample = {
        "sanitized_text": """Ethernet1/1
    transceiver is present
    SFP Detail Diagnostics Information (internal calibration)
                Current              Alarms                  Warnings
                Measurement     High        Low         High          Low
    Rx Power    -2.10 dBm       -1.00 dBm   -15.00 dBm  -2.00 dBm   -13.00 dBm
"""
    }

    enriched = _enrich_structural_spec(spec, [sample])

    rx_fields = [name for name in enriched["fields"] if name.startswith("rx_power")]
    assert sorted(rx_fields) == [
        "rx_power_alarm_high",
        "rx_power_alarm_low",
        "rx_power_current",
        "rx_power_warning_high",
        "rx_power_warning_low",
    ]
