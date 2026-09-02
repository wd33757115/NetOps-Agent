from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "test" / "check_item_golden_runtime_replay.py"
SPEC = importlib.util.spec_from_file_location(
    "check_item_golden_runtime_replay_script",
    SCRIPT,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def _capture(path: Path, *, cpu: str) -> None:
    path.write_text(
        "\n".join(
            [
                "命令: display cpu",
                "输出:",
                f"Slot 0 CPU usage: {cpu}%",
                "management address: 10.10.10.10",
                "password: field-secret",
                "----------------------------------------",
            ]
        ),
        encoding="utf-8",
    )


def test_regression_sample_args_require_both_periods(tmp_path: Path) -> None:
    baseline = tmp_path / "device-10.0.0.1.txt"
    _capture(baseline, cpu="10")

    with pytest.raises(ValueError, match="baseline and one current"):
        MODULE._parse_regression_sample_args(
            [f"baseline={baseline}"]
        )


def test_external_regression_samples_are_split_sanitized_and_pseudonymous(
    tmp_path: Path,
) -> None:
    baseline_dir = tmp_path / "baseline"
    current_dir = tmp_path / "current"
    baseline_dir.mkdir()
    current_dir.mkdir()
    baseline = baseline_dir / "router-a-10.0.0.1.txt"
    current = current_dir / "router-a-10.0.0.1.txt"
    _capture(baseline, cpu="10")
    _capture(current, cpu="90")
    entries = MODULE._parse_regression_sample_args(
        [f"baseline={baseline}", f"current={current}"]
    )

    samples = MODULE._load_regression_samples(
        {
            "canonical_command": "display cpu",
            "vendor": "H3C",
            "model": "MSR5620",
        },
        entries,
    )

    assert {item["period"] for item in samples} == {"baseline", "current"}
    assert {item["sample_origin"] for item in samples} == {
        "external_regression_file"
    }
    assert all(item["device_id"].startswith("device-") for item in samples)
    assert len({item["device_id"] for item in samples}) == 1
    assert all(item["source_evidence_id"].startswith("sha256:") for item in samples)
    assert all(
        item["observed_at_source"] == "frozen_period_order_non_authoritative"
        for item in samples
    )
    assert all("field-secret" not in item["sanitized_text"] for item in samples)
    assert all("10.10.10.10" not in item["sanitized_text"] for item in samples)
    assert str(tmp_path) not in json.dumps(samples, default=str)
