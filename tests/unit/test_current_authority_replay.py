from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.core.check_items.current_authority_replay import (
    AttestedOfflineReplayError,
    AttestedOfflineCurrentReplayService,
)
from src.core.collection import execution_plan_from_metadata


def _plan():
    def binding(version_id: str):
        return {
            "check_item_id": version_id.removesuffix("-v1"),
            "check_item_version_id": version_id,
            "implementation_version_id": f"impl-{version_id}",
            "runtime_bundle_version_id": f"bundle-{version_id}",
        }

    return execution_plan_from_metadata(
        {
            "device_id": "device-1",
            "action": "patrol",
            "mode": "production",
            "selector": {
                "authority_request_id": "authority-1",
                "patrol_batch_id": "batch-1",
            },
            "runtime_manifest_id": "runtime-manifest-1",
            "runtime_manifest_digest": "d" * 64,
            "collection_items": [
                {
                    "command": "display cpu primary",
                    "collection_item_id": "action-primary",
                    "fallback_rank": 0,
                    "check_item_bindings": [binding("system.cpu_usage-v1")],
                },
                {
                    "command": "display cpu fallback",
                    "collection_item_id": "action-fallback",
                    "fallback_rank": 1,
                    "check_item_bindings": [binding("system.cpu_usage-v1")],
                },
                {
                    "command": "display memory",
                    "collection_item_id": "action-memory",
                    "fallback_rank": 0,
                    "check_item_bindings": [binding("system.memory_usage-v1")],
                },
                {
                    "command": "display missing",
                    "collection_item_id": "action-missing",
                    "fallback_rank": 0,
                    "check_item_bindings": [binding("system.missing-v1")],
                },
                {
                    "command": "display unbound",
                    "collection_item_id": "action-unbound",
                    "fallback_rank": 0,
                    "check_item_bindings": [],
                },
            ],
        }
    )


def _row(command: str, raw: str):
    return SimpleNamespace(command=command, raw_text=raw)


def test_replay_does_not_replace_missing_primary_with_fallback():
    manifest = [
        {
            "snapshot_id": "cpu-old",
            "canonical_command": "display cpu fallback",
            "observed_at": "2026-07-31T09:00:00+00:00",
        },
        {
            "snapshot_id": "cpu-new",
            "canonical_command": "display cpu fallback",
            "observed_at": "2026-07-31T10:00:00+00:00",
        },
        {
            "snapshot_id": "memory",
            "canonical_command": "display memory",
            "observed_at": "2026-07-31T10:00:00+00:00",
        },
        {
            "snapshot_id": "unplanned",
            "canonical_command": "display version",
            "observed_at": "2026-07-31T10:00:00+00:00",
        },
    ]
    rows = {
        "cpu-old": _row("display cpu fallback", "old"),
        "cpu-new": _row("display cpu fallback", "new"),
        "memory": _row("display memory", "memory"),
        "unplanned": _row("display version", "version"),
    }

    selected, results, accounting = (
        AttestedOfflineCurrentReplayService._select_evidence(
            plan=_plan(),
            frozen_manifest=manifest,
            snapshot_rows=rows,
        )
    )

    assert [item["manifest"]["snapshot_id"] for item in selected] == [
        "memory",
    ]
    assert [item["status"] for item in results] == [
        "source_output_missing",
        "ignored_legacy_fallback",
        "complete",
        "source_output_missing",
        "source_output_missing",
    ]
    assert accounting == {
        "schema_version": "attested-offline-replay-accounting/v1",
        "frozen_snapshot_count": 4,
        "selected_snapshot_count": 1,
        "selected_snapshot_ids": ["memory"],
        "unselected_snapshot_ids": ["cpu-new", "cpu-old", "unplanned"],
        "planned_action_count": 5,
        "completed_action_count": 1,
        "ignored_legacy_fallback_count": 1,
        "missing_action_count": 3,
    }


def test_replay_command_report_preserves_original_cli_text():
    selected = [
        {
            "row": _row("show interface status", "Eth1/1 connected\n"),
        }
    ]

    report = AttestedOfflineCurrentReplayService._command_report(selected)

    assert "Command: show interface status" in report
    assert "Eth1/1 connected" in report


def test_replay_command_report_accepts_session_independent_snapshot_values():
    selected = [
        {
            "row": {
                "command": "display current-configuration",
                "raw_text": "sysname CORE-1\n",
            },
        }
    ]

    report = AttestedOfflineCurrentReplayService._command_report(selected)

    assert "Command: display current-configuration" in report
    assert "sysname CORE-1" in report


def test_frozen_parser_bundle_uses_exact_versions_after_active_switch(
    monkeypatch,
):
    from src.core import check_items
    from src.core.patrol import runner

    resolved_version_ids: list[str] = []

    class FrozenBundleService:
        @staticmethod
        def get_version(version_id: str):
            resolved_version_ids.append(version_id)
            return {
                "version_id": version_id,
                "compatibility": {},
                "parser_bundle": {},
            }

    monkeypatch.setattr(
        runner,
        "load_frozen_check_item_runtime_manifest",
        lambda _plan: {"digest": "d" * 64},
    )
    monkeypatch.setattr(
        check_items,
        "CheckItemRuntimeBundleService",
        FrozenBundleService,
    )

    bundle = runner.build_frozen_check_item_parser_bundle(
        _plan(),
        frozen_device={
            "vendor": "H3C",
            "model": "S5130S-54S-HI-G",
            "family": "Comware7",
        },
    )

    assert bundle.vendor == "H3C"
    assert set(resolved_version_ids) == {
        "bundle-system.cpu_usage-v1",
        "bundle-system.memory_usage-v1",
        "bundle-system.missing-v1",
    }


def test_replay_reports_frozen_manifest_tampering_with_stable_code(
    monkeypatch,
):
    from src.core.check_items import current_authority_replay

    service = AttestedOfflineCurrentReplayService()
    monkeypatch.setattr(service, "_load_context", lambda **_kwargs: {})

    def invalid_manifest(_plan):
        raise RuntimeError("frozen manifest digest mismatch")

    monkeypatch.setattr(
        current_authority_replay,
        "load_frozen_check_item_runtime_manifest",
        invalid_manifest,
    )

    with pytest.raises(AttestedOfflineReplayError) as blocked:
        service.execute(
            execution_plan={
                "device_id": "device-1",
                "action": "patrol",
                "mode": "production",
                "selector": {
                    "authority_request_id": "authority-1",
                    "patrol_batch_id": "batch-1",
                },
                "runtime_manifest_id": "runtime-manifest-1",
                "runtime_manifest_digest": "d" * 64,
                "collection_items": [{
                    "command": "display cpu-usage",
                    "collection_item_id": "action-cpu",
                    "fallback_rank": 0,
                    "check_item_bindings": [],
                }],
            },
            execution_plan_id="plan-1",
            workflow_run_id="workflow-1",
        )

    assert blocked.value.code == "runtime_manifest_invalid"
