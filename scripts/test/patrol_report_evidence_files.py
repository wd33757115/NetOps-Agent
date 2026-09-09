"""Local CLI evidence acceptance, independent of DB and without an LLM call.

Uses the real command splitter and a repository TextFSM for optical records.
This does not replace the database-backed, published-check-item patrol pipeline.
Raw input is never copied into the summary or into repository test fixtures.
"""

import argparse
import io
import json
from collections import Counter
from pathlib import Path

import textfsm

from src.core.parser.textfsm_input import prepare_textfsm_input
from src.core.patrol.command_splitter import inspect_raw_capture, split_cli_capture
from src.core.patrol.report_evidence import locate_field_value, object_rows
from src.core.patrol.unstructured_diff import (
    build_unstructured_diff,
    resolve_unstructured_diff_profile,
)


def read_capture(path):
    data = path.read_bytes()
    for encoding in ("utf-8-sig", "gb18030", "utf-16"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ValueError(f"Cannot decode capture: {path.name}")


def optical_values(template, raw):
    parser = textfsm.TextFSM(io.StringIO(template))
    records = parser.ParseText(prepare_textfsm_input(template, raw))
    values = {}
    for record in records:
        fields = dict(zip(parser.header, record))
        interface = fields.get("interface")
        if not interface:
            continue
        for key, value in fields.items():
            if value == "" or key == "interface":
                continue
            channel = None
            field = key
            for direction in ("rx", "tx"):
                prefix = f"{direction}_power_channel_"
                if key.startswith(prefix) and key.endswith("_dbm"):
                    channel = key[len(prefix) : -len("_dbm")]
                    field = f"{direction}_power_dbm"
            identity = (interface, channel, field)
            if identity in values and values[identity] != value:
                raise ValueError("Conflicting optical records require manual review")
            values[identity] = value
    return len(records), values


def inspect_material(root):
    root = Path(root)
    sides = {}
    summary = {
        "scope": "local_file_evidence_acceptance_only",
        "database_pipeline_verified": False,
        "ai_requests": 0,
        "periods": {},
        "pairs": [],
    }
    for role in ("BASE", "NOW"):
        captures = {}
        for path in sorted((root / role).rglob("*.txt")):
            text = read_capture(path)
            relative = path.relative_to(root / role).as_posix()
            blocks = split_cli_capture(text)
            captures[relative] = blocks
            metadata = inspect_raw_capture(text)
            summary["periods"].setdefault(role, []).append(
                {
                    "file": relative,
                    "bytes": path.stat().st_size,
                    "lines": len(text.splitlines()),
                    "commands": len(blocks),
                    "capture_time_present": bool(metadata.observed_at_text),
                    "duplicate_commands": sum(
                        n - 1 for n in Counter(b.command_canonical for b in blocks).values()
                    ),
                }
            )
        if not captures:
            raise ValueError(f"No .txt captures in {role}")
        sides[role] = captures
    template = (
        Path(__file__).resolve().parents[2]
        / "src/skills/shared/textfsm-templates/H3C/Comware7"
        / "display_transceiver_diagnosis_interface__1.0.0.textfsm"
    ).read_text(encoding="utf-8")
    summary["unpaired_files"] = sorted(set(sides["BASE"]) ^ set(sides["NOW"]))
    for filename in sorted(set(sides["BASE"]) & set(sides["NOW"])):
        # Duplicate commands are intentionally not guessed or overwritten.
        old = sides["BASE"][filename]
        new = sides["NOW"][filename]
        counts_old = Counter(b.command_canonical for b in old)
        counts_new = Counter(b.command_canonical for b in new)
        old = {b.command_canonical: b for b in old if counts_old[b.command_canonical] == 1}
        new = {b.command_canonical: b for b in new if counts_new[b.command_canonical] == 1}
        pair = {"file": filename, "commands": []}
        for command in sorted(set(old) & set(new)):
            baseline, current = old[command].raw_output, new[command].raw_output
            result = {
                "command": command,
                "baseline_lines": len(baseline.splitlines()),
                "current_lines": len(current.splitlines()),
                "raw_changed": baseline != current,
            }
            profile = resolve_unstructured_diff_profile(command)
            if profile:
                diff = build_unstructured_diff(baseline, current, command=command, profile=profile)
                result["text_comparison"] = {
                    k: diff[k]
                    for k in ("changed", "noise_only", "added_line_count", "removed_line_count")
                }
            if command == "display transceiver diagnosis interface":
                base_count, before = optical_values(template, baseline)
                now_count, after = optical_values(template, current)
                changed = [key for key in before.keys() & after.keys() if before[key] != after[key]]
                excerpts = []
                for interface, channel, field in sorted(changed, key=str):
                    key = json.dumps([interface, channel] if channel else [interface])
                    rows, note = object_rows(
                        {
                            "entity_key": key,
                            "baseline": {"raw_text": baseline},
                            "current": {"raw_text": current},
                        }
                    )
                    # Validate provenance of every excerpt character and line number.
                    for row in rows:
                        for side, raw in (("baseline", baseline), ("current", current)):
                            value = row.get(side)
                            if value:
                                assert raw.splitlines()[value["line"] - 1] == value["text"]
                    candidates = locate_field_value(rows, field, after[(interface, channel, field)])
                    excerpts.append(
                        {
                            "field": field,
                            "channel": channel,
                            "rows": len(rows),
                            "field_located_uniquely": len(candidates) == 1,
                            "note": note,
                        }
                    )
                result["optical_parser"] = {
                    "template_source": "repository_1.0.0_not_database_active_version",
                    "baseline_records": base_count,
                    "current_records": now_count,
                    "changed_fields": len(changed),
                    "changed_interfaces": len({key[0] for key in changed}),
                    "added_fields": len(after.keys() - before.keys()),
                    "removed_fields": len(before.keys() - after.keys()),
                    "excerpts": excerpts,
                    "event_judgement": "not_run_requires_published_rules",
                }
            pair["commands"].append(result)
        summary["pairs"].append(pair)
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = inspect_material(args.root)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
