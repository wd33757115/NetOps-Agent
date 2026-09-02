"""Replay two H3C commands from quarterly CLI evidence through Adapter V2.

This is deliberately a read-only pre-release verification.  It compiles the
TextFSM assets, checks the Projection's Parser field contract, executes the
direct A -> B projection and compares canonical StateObservations.  It never
creates Parser, CommandAdapter, Bundle or CheckItem rows in PostgreSQL.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import textfsm

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.core.check_items.adapter_projection import (
    project_adapter_batches_to_observations,
)  # noqa: E402
from src.core.check_items.catalog import CheckItemDefinition, load_catalog  # noqa: E402
from src.core.check_items.command_adapter import (
    direct_projection_manifest,
    direct_projection_source_field_errors,
    execute_adapter,
)  # noqa: E402
from src.core.parser.textfsm_input import prepare_textfsm_input  # noqa: E402
from src.core.state.domain import StateObservation  # noqa: E402

TEMPLATE_ROOT = (
    PROJECT_ROOT / "src/skills/shared/textfsm-templates/H3C/Comware7"
)
DEFAULT_SAMPLE_ROOT = Path(r"C:\Users\wangd\Desktop\TEXTFSMTEST")
DEVICE_ID = "h3c-s6000-192.168.1.1"
INVENTORY_DEVICE_ID = "offline-h3c-s6000-192.168.1.1"


def _check_item_payload(item: CheckItemDefinition) -> dict[str, Any]:
    return {
        "version_id": item.version_id,
        "canonical_contract": item.canonical_contract,
    }


def _interface_spec(item: CheckItemDefinition) -> dict[str, Any]:
    return {
        "schema_version": "command-adapter/v2",
        "parser": {"template_id": "h3c-comware7-display-interface-brief-v1"},
        "outputs": [
            {
                "check_item_id": item.id,
                "check_item_version_id": item.version_id,
                "source_record_set": "interfaces",
                "projection": {
                    "fields": {
                        "interface": {"source": "interface"},
                        "oper_status": {
                            "source": "link",
                            "value_mapping": {
                                "UP": "up",
                                "DOWN": "down",
                                "ADM": "admin_down",
                                "Stby": "unknown",
                            },
                            "on_unmapped": "reject",
                        },
                    }
                },
            }
        ],
    }


def _optical_spec(item: CheckItemDefinition) -> dict[str, Any]:
    channel_dimensions = {"channel": {"field": "channel"}}
    return {
        "schema_version": "command-adapter/v2",
        "parser": {
            "template_id": (
                "h3c-comware7-display-transceiver-diagnosis-interface-v1.1"
            )
        },
        "outputs": [
            {
                "check_item_id": item.id,
                "check_item_version_id": item.version_id,
                "source_record_set": "optics",
                "record_set_joins": [
                    {
                        "source_record_set": "optical_thresholds",
                        "on": {"interface": "interface"},
                        "fields": [
                            "rx_high_alarm_dbm",
                            "rx_low_alarm_dbm",
                            "tx_high_alarm_dbm",
                            "tx_low_alarm_dbm",
                        ],
                        "required": False,
                    }
                ],
                "projection": {
                    "fields": {
                        "interface": {"source": "interface"},
                        "channel": {
                            "source": "channel_token",
                            "value_mapping": {
                                "": "aggregate",
                                "Current": "aggregate",
                                "1": "1",
                                "2": "2",
                                "3": "3",
                                "4": "4",
                            },
                            "on_unmapped": "reject",
                        },
                        "transceiver_present": {
                            "source": "transceiver_present",
                            "value_mapping": {"": "present", "absent": "absent"},
                            "on_unmapped": "reject",
                            "dimensions": channel_dimensions,
                        },
                        "rx_power_dbm": {
                            "source": "rx_power_dbm",
                            "canonical_unit": "dbm",
                            "dimensions": channel_dimensions,
                        },
                        "tx_power_dbm": {
                            "source": "tx_power_dbm",
                            "canonical_unit": "dbm",
                            "dimensions": channel_dimensions,
                        },
                        "rx_high_alarm_dbm": {
                            "source": "rx_high_alarm_dbm",
                            "canonical_unit": "dbm",
                            "dimensions": channel_dimensions,
                        },
                        "rx_low_alarm_dbm": {
                            "source": "rx_low_alarm_dbm",
                            "canonical_unit": "dbm",
                            "dimensions": channel_dimensions,
                        },
                        "tx_high_alarm_dbm": {
                            "source": "tx_high_alarm_dbm",
                            "canonical_unit": "dbm",
                            "dimensions": channel_dimensions,
                        },
                        "tx_low_alarm_dbm": {
                            "source": "tx_low_alarm_dbm",
                            "canonical_unit": "dbm",
                            "dimensions": channel_dimensions,
                        },
                    }
                },
            }
        ],
    }


def _quarter_file(sample_root: Path, quarter: str) -> Path:
    candidates = sorted((sample_root / quarter).glob("*.txt"))
    if len(candidates) != 1:
        raise ValueError(
            f"{sample_root / quarter} must contain exactly one .txt sample; "
            f"found {len(candidates)}"
        )
    return candidates[0]


def _extract_command_output(source: str, command: str) -> str:
    match = re.search(
        rf"^命令:\s*{re.escape(command)}\r?\n输出:\r?\n"
        rf"(.*?)(?=^[-]{{10,}}\s*$|\Z)",
        source,
        re.MULTILINE | re.DOTALL,
    )
    if match is None:
        raise ValueError(f"sample does not contain command: {command}")
    return match.group(1).rstrip()


def _parse(template_name: str, raw_text: str) -> tuple[list[str], list[dict[str, str]]]:
    template_text = (TEMPLATE_ROOT / template_name).read_text(encoding="utf-8")
    parser = textfsm.TextFSM(io.StringIO(template_text))
    rows = [
        dict(zip(parser.header, row, strict=False))
        for row in parser.ParseText(prepare_textfsm_input(template_text, raw_text))
    ]
    return list(parser.header), rows


def _state_facts(observations: list[StateObservation]) -> dict[str, StateObservation]:
    facts = {item.state_key: item for item in observations}
    if len(facts) != len(observations):
        raise AssertionError("direct Projection emitted duplicate state facts")
    return facts


def _diff_facts(
    before: Mapping[str, StateObservation],
    after: Mapping[str, StateObservation],
) -> list[dict[str, Any]]:
    changes: list[dict[str, Any]] = []
    for state_key in sorted(set(before) | set(after)):
        prior = before.get(state_key)
        current = after.get(state_key)
        if prior is not None and current is not None and prior.value == current.value:
            continue
        reference = current or prior
        changes.append(
            {
                "entity_key": reference.entity_key if reference else None,
                "field": reference.field_name if reference else None,
                "before": prior.value if prior is not None else None,
                "after": current.value if current is not None else None,
            }
        )
    return changes


def _replay_command(
    *,
    sample_root: Path,
    command: str,
    template_name: str,
    item: CheckItemDefinition,
    spec: dict[str, Any],
    record_set: str,
    extra_templates: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    payload = _check_item_payload(item)
    manifest = direct_projection_manifest(
        spec,
        check_item_id=item.id,
        check_item=payload,
    )
    quarters: dict[str, Any] = {}
    facts_by_quarter: dict[str, dict[str, StateObservation]] = {}
    for number, quarter in enumerate(("Q1", "Q2"), start=1):
        raw_text = _extract_command_output(
            _quarter_file(sample_root, quarter).read_text(encoding="utf-8"),
            command,
        )
        headers, rows = _parse(template_name, raw_text)
        parsed_record_sets: dict[str, list[dict[str, str]]] = {record_set: rows}
        parser_contract: dict[str, list[str]] = {record_set: headers}
        extra_counts: dict[str, int] = {}
        for extra_set, extra_template in dict(extra_templates or {}).items():
            extra_headers, extra_rows = _parse(extra_template, raw_text)
            parsed_record_sets[extra_set] = extra_rows
            parser_contract[extra_set] = extra_headers
            extra_counts[extra_set] = len(extra_rows)
        source_errors = direct_projection_source_field_errors(
            manifest,
            parser_field_contract={"template_record_sets": parser_contract},
        )
        if source_errors:
            raise AssertionError("; ".join(source_errors))
        batches = execute_adapter(
            spec,
            record_sets=parsed_record_sets,
            check_items={item.id: payload},
            device_id=DEVICE_ID,
            adapter_version_id=f"offline-h3c-{item.id}-candidate-v1",
            observed_at=f"2026-0{1 if number == 1 else 4}-01T00:00:00+00:00",
            require_pinned_check_item_versions=True,
        )
        batch = batches[0]
        if batch.quality_status != "complete":
            raise AssertionError(
                f"{command} {quarter} is not complete: {batch.errors}"
            )
        observations = project_adapter_batches_to_observations(
            batches,
            check_items={item.id: item},
            snapshot={
                "id": f"offline-h3c-{item.id}-{quarter}",
                "run_id": f"offline-h3c-{quarter}",
                "device_id": DEVICE_ID,
                "inventory_device_id": INVENTORY_DEVICE_ID,
                "vendor": "H3C",
                "model": "S6000",
                "canonical_command": command,
                "observed_at": batch.observed_at,
                "parser_template_id": spec["parser"]["template_id"],
                "parser_version": "candidate",
                "metadata_json": {},
            },
            mode="parser_validation",
            source="offline_h3c_quarterly_replay",
            dataset_id=None,
            command_adapter_id=f"offline-h3c-{item.id}",
            command_adapter_version_id=f"offline-h3c-{item.id}-candidate-v1",
        )
        facts_by_quarter[quarter] = _state_facts(observations)
        quarters[quarter] = {
            "raw_sha256": hashlib.sha256(raw_text.encode("utf-8")).hexdigest(),
            "parser_row_count": len(rows),
            "additional_record_set_counts": extra_counts,
            "canonical_record_count": len(batch.records),
            "observation_count": len(observations),
            "quality_status": batch.quality_status,
        }
    return {
        "command": command,
        "parser_template": template_name,
        "check_item_id": item.id,
        "check_item_version_id": item.version_id,
        "projection_digest": manifest["digest"],
        "quarters": quarters,
        "changes": _diff_facts(facts_by_quarter["Q1"], facts_by_quarter["Q2"]),
    }


def run(sample_root: Path) -> dict[str, Any]:
    catalog = load_catalog()
    interface = catalog.check_item("interface.oper_status")
    optical = catalog.check_item("interface.optical_power")
    if interface is None or optical is None:
        raise AssertionError("the H3C replay CheckItems are unavailable")
    results = [
        _replay_command(
            sample_root=sample_root,
            command="display interface brief",
            template_name="display_interface_brief__1.0.0.textfsm",
            item=interface,
            spec=_interface_spec(interface),
            record_set="interfaces",
        ),
        _replay_command(
            sample_root=sample_root,
            command="display transceiver diagnosis interface",
            template_name="display_transceiver_diagnosis_interface__1.1.0.textfsm",
            item=optical,
            spec=_optical_spec(optical),
            record_set="optics",
            extra_templates={
                "optical_thresholds": (
                    "display_transceiver_diagnosis_interface_thresholds__1.0.0.textfsm"
                )
            },
        ),
    ]
    interface_result, optical_result = results
    expected_interface_change = [
        {
            "entity_key": '["FGE1/0/25"]',
            "field": "oper_status",
            "before": "up",
            "after": "down",
        }
    ]
    if interface_result["changes"] != expected_interface_change:
        raise AssertionError(
            "unexpected interface differences: "
            + json.dumps(interface_result["changes"], ensure_ascii=False)
        )
    expected_optical_change = [
        {
            "entity_key": '["Ten-GigabitEthernet1/0/2","aggregate"]',
            "field": "tx_power_dbm",
            "before": -2.68,
            "after": -12.68,
        }
    ]
    if optical_result["changes"] != expected_optical_change:
        raise AssertionError(
            "unexpected optical differences: "
            + json.dumps(optical_result["changes"], ensure_ascii=False)
        )
    return {
        "success": True,
        "mode": "read_only_offline_replay",
        "sample_root": str(sample_root),
        "commands": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sample-root", type=Path, default=DEFAULT_SAMPLE_ROOT)
    arguments = parser.parse_args()
    report = run(arguments.sample_root)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
