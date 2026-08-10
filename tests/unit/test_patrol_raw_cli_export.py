import csv
import io
import zipfile

from src.gateway.workflow_api import (
    _device_inventory_csv,
    _device_inventory_text,
    _raw_cli_verification_text,
    _write_original_cli_files,
)


def test_raw_cli_export_keeps_text_and_excludes_internal_collection_metadata():
    source_buffer = io.BytesIO()
    with zipfile.ZipFile(source_buffer, "w") as source:
        source.writestr("switch.txt", "display interface brief\n...")
        source.writestr("switch.txt.collection.json", '{"internal": true}')
        source.writestr("../outside.txt", "unsafe")

    output_buffer = io.BytesIO()
    file_hashes: dict[str, str] = {}
    with zipfile.ZipFile(output_buffer, "w") as output:
        written = _write_original_cli_files(
            output,
            source_buffer.getvalue(),
            "SW-1",
            file_hashes=file_hashes,
        )

    with zipfile.ZipFile(io.BytesIO(output_buffer.getvalue())) as archive:
        assert archive.namelist() == ["SW-1/switch.txt"]
        assert archive.read("SW-1/switch.txt").decode() == "display interface brief\n..."
    assert written == 1
    assert list(file_hashes) == ["SW-1/switch.txt"]
    assert len(file_hashes["SW-1/switch.txt"]) == 64


def test_device_inventory_csv_supports_success_and_login_failure_rows():
    payload = _device_inventory_csv(
        [
            {
                "device_id": "device-1",
                "device_name": "SW-1",
                "management_ip": "10.0.0.1",
                "selection_status": "launched",
                "preflight_status": "ready",
                "connection_status": "failed",
                "workflow_status": "failed",
                "failure_code": "auth_failed",
                "error_message": "authentication failed",
                "raw_cli_file_count": 0,
            }
        ]
    )

    rows = list(csv.DictReader(io.StringIO(payload.decode("utf-8-sig"))))
    assert rows[0]["device_name"] == "SW-1"
    assert rows[0]["connection_status"] == "failed"
    assert rows[0]["failure_code"] == "auth_failed"
    assert rows[0]["raw_cli_file_count"] == "0"


def test_device_inventory_text_is_human_readable_and_keeps_failure_reason():
    payload = _device_inventory_text(
        [
            {
                "device_id": "device-1",
                "device_name": "SW-1",
                "management_ip": "10.0.0.1",
                "connection_status": "failed",
                "failure_code": "auth_failed",
                "error_message": "authentication failed",
                "raw_cli_file_count": 0,
            }
        ]
    ).decode("utf-8-sig")

    assert payload.startswith("设备ID\t设备名称\t管理地址")
    assert "SW-1\t10.0.0.1" in payload
    assert "auth_failed\tauthentication failed\t0" in payload


def test_raw_cli_verification_text_binds_manifest_and_snapshot_hashes():
    payload = _raw_cli_verification_text(
        patrol_batch_id="batch-1",
        manifest={
            "id": "manifest-1",
            "version": 2,
            "raw_evidence_set_digest": "a" * 64,
        },
        evidence_index=[
            {
                "snapshot_id": "snapshot-1",
                "run_id": "run-1",
                "device_name": "SW-1",
                "management_ip": "10.0.0.1",
                "canonical_command": "display version",
                "raw_text_hash": "b" * 64,
                "source_file_hash": "c" * 64,
            }
        ],
        file_hashes={"SW-1/output.txt": "c" * 64},
    ).decode("utf-8-sig")

    assert "报告Manifest\tmanifest-1" in payload
    assert "报告版本\t2" in payload
    assert f"原始证据集合摘要\t{'a' * 64}" in payload
    assert f"SW-1/output.txt\t{'c' * 64}" in payload
    assert "snapshot-1\tSW-1\t10.0.0.1\tdisplay version" in payload
