import csv
import io
import zipfile

from src.gateway.workflow_api import _device_inventory_csv, _write_original_cli_files


def test_raw_cli_export_keeps_text_and_excludes_internal_collection_metadata():
    source_buffer = io.BytesIO()
    with zipfile.ZipFile(source_buffer, "w") as source:
        source.writestr("switch.txt", "display interface brief\n...")
        source.writestr("switch.txt.collection.json", '{"internal": true}')
        source.writestr("../outside.txt", "unsafe")

    output_buffer = io.BytesIO()
    with zipfile.ZipFile(output_buffer, "w") as output:
        written = _write_original_cli_files(output, source_buffer.getvalue(), "SW-1")

    with zipfile.ZipFile(io.BytesIO(output_buffer.getvalue())) as archive:
        assert archive.namelist() == ["SW-1/switch.txt"]
        assert archive.read("SW-1/switch.txt").decode() == "display interface brief\n..."
    assert written == 1


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
