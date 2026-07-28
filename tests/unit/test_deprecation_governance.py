from datetime import date
from pathlib import Path

import yaml

from scripts.deprecations.audit_deprecations import audit


def _write(path: Path, candidate: dict) -> Path:
    path.write_text(
        yaml.safe_dump(
            {"schema_version": 1, "candidates": [candidate]},
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return path


def _candidate() -> dict:
    return {
        "id": "legacy-path",
        "owner": "team",
        "status": "observing",
        "replacement": "new-path",
        "deprecated_at": "2026-07-01",
        "remove_after": "2026-09-01",
        "rollback": "feature flag",
        "gates": {
            "static_references_cleared": False,
            "runtime_traffic_zero": False,
            "database_writes_zero": True,
            "task_registry_cleared": True,
            "replacement_coverage_complete": False,
            "tests_and_migrations_passed": True,
            "rollback_rehearsed": False,
        },
    }


def test_deprecation_audit_accepts_owned_observation_candidate(tmp_path):
    registry = _write(tmp_path / "deprecations.yaml", _candidate())

    result = audit(registry, today=date(2026, 7, 28))

    assert result["success"] is True
    assert result["status_counts"]["observing"] == 1


def test_deprecation_audit_blocks_removal_until_every_gate_has_evidence(
    tmp_path,
):
    candidate = _candidate()
    candidate["status"] = "ready_to_remove"
    registry = _write(tmp_path / "deprecations.yaml", candidate)

    result = audit(registry, today=date(2026, 7, 28))

    assert result["success"] is False
    assert any(
        "requires completed gates" in error
        for error in result["errors"]
    )
    assert any(
        "requires immutable evidence" in error
        for error in result["errors"]
    )


def test_deprecation_audit_requires_removed_timestamp(tmp_path):
    candidate = _candidate()
    candidate["status"] = "removed"
    candidate["gates"] = {
        key: True for key in candidate["gates"]
    }
    candidate["evidence"] = {"report": "artifact-sha256"}
    registry = _write(tmp_path / "deprecations.yaml", candidate)

    result = audit(registry, today=date(2026, 7, 28))

    assert result["success"] is False
    assert any("removed_at is required" in error for error in result["errors"])
