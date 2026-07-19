# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import io
import os
import zipfile
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from src.core.execution import (
    ArchiveSpec,
    archive_output_files,
    cleanup_expired_output_captures,
    load_inventory_execution_context,
    run_plan_or_filter,
    runner_success_payload,
)


def test_archive_output_files_returns_none_when_storage_is_unavailable(tmp_path):
    output = tmp_path / "out.txt"
    output.write_text("content", encoding="utf-8")

    assert (
        archive_output_files(
            output_files=[str(output)],
            ticket_id="T-1",
            minio_storage_factory=lambda: None,
            spec=ArchiveSpec(
                object_prefix="test",
                zip_prefix="test_zip",
                default_ticket_prefix="TEST",
                log_event_prefix="test",
            ),
        )
        is None
    )


def test_archive_output_files_uploads_zip_with_stable_key(tmp_path):
    uploaded: dict[str, object] = {}
    output = tmp_path / "out.txt"
    output.write_text("content", encoding="utf-8")

    class FakeMinio:
        def is_ready(self):
            return True

        def upload_file(self, object_name, file_obj):
            uploaded["object_name"] = object_name
            uploaded["bytes"] = file_obj.read()
            return True

        def get_presigned_url(self, object_name, expires):
            uploaded["expires"] = expires
            return f"https://minio.local/{object_name}"

    url = archive_output_files(
        output_files=[str(output)],
        ticket_id="T-1",
        minio_storage_factory=lambda: FakeMinio(),
        spec=ArchiveSpec(
            object_prefix="test",
            zip_prefix="test_zip",
            default_ticket_prefix="TEST",
            log_event_prefix="test",
        ),
    )

    assert url == "https://minio.local/test/T-1/test_zip_T-1.zip"
    assert uploaded["object_name"] == "test/T-1/test_zip_T-1.zip"
    assert uploaded["bytes"]
    assert uploaded["expires"] == 3600 * 24 * 7


def test_archive_output_files_uses_allowlist_and_cleans_only_uploaded_files(tmp_path):
    uploaded: dict[str, object] = {}
    output = tmp_path / "out.txt"
    manifest = tmp_path / "out.txt.collection.json"
    unrelated = tmp_path / "other-device.txt"
    output.write_text("content", encoding="utf-8")
    manifest.write_text("{}", encoding="utf-8")
    unrelated.write_text("private", encoding="utf-8")

    class FakeMinio:
        def is_ready(self):
            return True

        def upload_file(self, _object_name, file_obj):
            uploaded["bytes"] = file_obj.read()
            return True

        def get_presigned_url(self, object_name, expires):
            return f"https://minio.local/{object_name}"

    archive_output_files(
        output_files=[str(output)],
        ticket_id="T-1",
        minio_storage_factory=lambda: FakeMinio(),
        spec=ArchiveSpec("test", "test_zip", "TEST", "test"),
        cleanup_after_upload=True,
    )

    with zipfile.ZipFile(io.BytesIO(uploaded["bytes"])) as archive:
        assert archive.namelist() == ["out.txt", "out.txt.collection.json"]
    assert not output.exists()
    assert not manifest.exists()
    assert unrelated.exists()


def test_expired_output_cleanup_requires_sidecar_and_preserves_recent_files(tmp_path):
    now = datetime(2026, 7, 19, tzinfo=timezone.utc)
    stale_dir = tmp_path / "patrol" / "old" / "device" / "capture"
    stale_dir.mkdir(parents=True)
    stale_output = stale_dir / "device.txt"
    stale_manifest = stale_dir / "device.txt.collection.json"
    stale_output.write_text("old", encoding="utf-8")
    stale_manifest.write_text("{}", encoding="utf-8")
    old_timestamp = (now - timedelta(hours=80)).timestamp()
    os.utime(stale_output, (old_timestamp, old_timestamp))
    os.utime(stale_manifest, (old_timestamp, old_timestamp))

    recent_output = tmp_path / "recent.txt"
    recent_manifest = tmp_path / "recent.txt.collection.json"
    recent_output.write_text("new", encoding="utf-8")
    recent_manifest.write_text("{}", encoding="utf-8")
    unrelated = tmp_path / "unmanaged.txt"
    unrelated.write_text("keep", encoding="utf-8")
    os.utime(unrelated, (old_timestamp, old_timestamp))

    result = cleanup_expired_output_captures(
        tmp_path,
        retention_hours=72,
        now=now,
    )

    assert result["deleted_files"] == 2
    assert not stale_output.exists()
    assert not stale_manifest.exists()
    assert recent_output.exists() and recent_manifest.exists()
    assert unrelated.exists()


def test_runner_success_payload_preserves_legacy_fields():
    result = SimpleNamespace(
        total_devices=2,
        success_devices=1,
        failed_devices=1,
        output_files=["a.txt"],
        message="done",
    )

    payload = runner_success_payload(
        action="patrol",
        ticket_id="T-1",
        result=result,
        execution_time_ms=123,
        download_url="https://minio.local/a.zip",
        extra={"mode": "production", "raw_outputs_archive": "https://minio.local/a.zip"},
    )

    assert payload == {
        "success": True,
        "status": "success",
        "action": "patrol",
        "ticket_id": "T-1",
        "download_url": "https://minio.local/a.zip",
        "total_devices": 2,
        "success_devices": 1,
        "failed_devices": 1,
        "output_files": ["a.txt"],
        "execution_time_ms": 123,
        "message": "done",
        "mode": "production",
        "raw_outputs_archive": "https://minio.local/a.zip",
    }


def test_inventory_execution_context_prefers_explicit_plan():
    captured: dict[str, object] = {}

    class FakeDBManager:
        pass

    class FakeTool:
        def __init__(self, db_manager):
            captured["db_manager"] = db_manager

        async def by_plan(self, plan):
            captured["plan"] = plan
            return "planned"

        async def by_filter(self, device_filter):
            raise AssertionError("explicit plan should be preferred")

    class FakeDeviceFilter:
        def __init__(self, **kwargs):
            captured["filter"] = kwargs

    context = load_inventory_execution_context(
        tool_factory_name="FakeTool",
        filter_params={"vendor": "Cisco"},
        execution_plan_data={
            "device_id": "device-1",
            "action": "patrol",
            "mode": "production",
            "collection_items": [{"command": "show version", "action": "patrol"}],
        },
        tools_loader=lambda: SimpleNamespace(
            DBManager=FakeDBManager,
            DeviceFilter=FakeDeviceFilter,
            FakeTool=FakeTool,
        ),
    )

    result = run_plan_or_filter(
        context=context,
        plan_runner=context.tool.by_plan,
        filter_runner=context.tool.by_filter,
    )

    assert result == "planned"
    assert context.execution_plan.device_id == "device-1"
    assert context.device_filter is None
    assert "filter" not in captured


def test_inventory_execution_context_builds_filter_without_plan():
    captured: dict[str, object] = {}

    class FakeTool:
        def __init__(self, db_manager):
            self.db_manager = db_manager

        async def by_plan(self, plan):
            raise AssertionError("filter path should be used")

        async def by_filter(self, device_filter):
            captured["filter"] = device_filter
            return "filtered"

    class FakeDeviceFilter:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    context = load_inventory_execution_context(
        tool_factory_name="FakeTool",
        filter_params={"vendor": "Cisco"},
        execution_plan_data=None,
        tools_loader=lambda: SimpleNamespace(
            DBManager=lambda: object(),
            DeviceFilter=FakeDeviceFilter,
            FakeTool=FakeTool,
        ),
    )

    result = run_plan_or_filter(
        context=context,
        plan_runner=context.tool.by_plan,
        filter_runner=context.tool.by_filter,
    )

    assert result == "filtered"
    assert context.execution_plan is None
    assert captured["filter"].kwargs == {"vendor": "Cisco"}
