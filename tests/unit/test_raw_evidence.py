from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from src.core.evidence import build_cli_evidence, evidence_id_for_command_snapshot


def test_cli_snapshot_evidence_id_is_deterministic():
    first = evidence_id_for_command_snapshot("snapshot-1")
    second = evidence_id_for_command_snapshot("snapshot-1")

    assert first == second
    assert first.startswith("ev-cli-")
    assert len(first) <= 64


def test_build_cli_evidence_uses_snapshot_as_content_reference():
    snapshot = SimpleNamespace(
        id="snapshot-1",
        run_id="run-1",
        dataset_id="dataset-1",
        inventory_device_id="inventory-device-1",
        device_id="SW1",
        command="show version",
        canonical_command="show version",
        parser_template_id="template-1",
        parser_version="v1",
        contract_version=3,
        parse_status="parsed",
        snapshot_kind="full",
        source_file_hash="file-hash",
        import_fingerprint="fingerprint-1",
        start_line=10,
        end_line=20,
        raw_text_hash="raw-hash",
        observed_at=datetime(2026, 6, 25, tzinfo=timezone.utc),
    )

    evidence = build_cli_evidence(snapshot)

    assert evidence.evidence_id == evidence_id_for_command_snapshot("snapshot-1")
    assert evidence.evidence_key == "cli_command_snapshot:snapshot-1"
    assert evidence.source_type == "cli"
    assert evidence.evidence_type == "cli_command_output"
    assert evidence.content_ref == "command_snapshot:snapshot-1"
    assert evidence.content_hash == "raw-hash"
    assert evidence.metadata["canonical_command"] == "show version"
    assert evidence.metadata["source_lines"] == {"start": 10, "end": 20}
