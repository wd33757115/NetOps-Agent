from __future__ import annotations

from scripts.test import export_parser_candidate_approval_pack as approval_pack
from src.core.parser.parser_bundle import build_parser_bundle


class _FakeField:
    def __init__(self, name: str):
        self.name = name

    def __eq__(self, value):
        return self.name, value


class _FakeSample:
    id = _FakeField("id")
    job_id = _FakeField("job_id")
    lifecycle_status = _FakeField("lifecycle_status")


class _FakeQuery:
    def __init__(self):
        self.conditions = ()

    def filter(self, *conditions):
        self.conditions = conditions
        return self

    def all(self):
        assert ("job_id", "job-1") in self.conditions
        assert ("lifecycle_status", "current") in self.conditions
        return [("current-sample",)]


class _FakeDb:
    def query(self, field):
        assert field.name == "id"
        return _FakeQuery()


def test_approval_pack_source_set_excludes_retired_attempt_samples(monkeypatch):
    monkeypatch.setattr(approval_pack, "ParserJobSample", _FakeSample)

    assert approval_pack._current_job_sample_ids(_FakeDb(), "job-1") == {
        "current-sample"
    }


def test_approval_pack_replays_named_parser_bundle_record_sets():
    template = build_parser_bundle(
        {
            "device_version": r"""
Value software_version (\S+)

Start
  ^Version:\s+${software_version} -> Record
""",
            "slot_inventory": r"""
Value slot (\d+)
Value board_type (\S+)

Start
  ^Slot\s+${slot}:\s+${board_type} -> Record
""",
        }
    ).serialize()

    result = approval_pack._parse_template(
        template,
        "Version: 7.1.064\nSlot 0: MPU-60\nSlot 1: MPU-60\n",
    )

    assert result == {
        "format": "netops.structured_record_sets/v1",
        "record_sets": {
            "device_version": [{"software_version": "7.1.064"}],
            "slot_inventory": [
                {"slot": "0", "board_type": "MPU-60"},
                {"slot": "1", "board_type": "MPU-60"},
            ],
        },
    }
    assert approval_pack._record_set_counts(result) == {
        "device_version": 1,
        "slot_inventory": 2,
    }
    assert approval_pack._flatten_records(result) == [
        {"__record_set__": "device_version", "software_version": "7.1.064"},
        {
            "__record_set__": "slot_inventory",
            "slot": "0",
            "board_type": "MPU-60",
        },
        {
            "__record_set__": "slot_inventory",
            "slot": "1",
            "board_type": "MPU-60",
        },
    ]


def test_approval_pack_markdown_keeps_each_record_set_separate():
    lines = approval_pack._parsed_result_markdown(
        {
            "parsed_result": {
                "format": "netops.structured_record_sets/v1",
                "record_sets": {
                    "status": [{"interface": "GE1/0/1", "state": "up"}],
                    "power": [{"interface": "GE1/0/1", "rx_dbm": "-2.5"}],
                },
            }
        }
    )
    rendered = "\n".join(lines)

    assert "`status`（1 条）" in rendered
    assert "`power`（1 条）" in rendered
    assert '"rx_dbm": "-2.5"' in rendered


def test_approval_pack_keeps_single_record_templates_backward_compatible():
    result = approval_pack._parse_template(
        r"""Value interface (\S+)
Value state (\S+)

Start
  ^${interface}\s+${state} -> Record
""",
        "GE1/0/1 up\n",
    )

    assert result == [{"interface": "GE1/0/1", "state": "up"}]
    assert approval_pack._record_set_counts(result) == {}
    assert approval_pack._flatten_records(result) == result


def test_target_matrix_does_not_promote_parser_review_to_check_item_readiness():
    capability = {
        "status": "parser_shape_incomplete",
        "blockers": [
            {
                "code": "parser_output_role_missing",
                "message": "Parser output is missing metric fields",
            },
            {
                "code": "observation_contract_mapping_required",
                "message": "ObservationContract mapping is required",
            },
        ],
    }
    matrix = approval_pack._target_matrix(
        [
            {
                "check_item_id": "interface.optical_power",
                "check_item_capability": capability,
                "template": {
                    "id": "template-1",
                    "vendor": "H3C",
                    "model": "MSR5620",
                    "canonical_command": "display transceiver diagnosis interface",
                    "status": "pending_approval",
                },
            }
        ]
    )

    target = matrix["check_items"][0]
    assert target["parser_review_status"] == "ready_for_human_review"
    assert target["check_item_runtime_ready"] is False
    assert "parser_output_role_missing" in target["blocker_codes"]
    assert "observation_contract_mapping_required" in target["blocker_codes"]
    assert matrix["invariants"]["production_activation_permitted"] is False
