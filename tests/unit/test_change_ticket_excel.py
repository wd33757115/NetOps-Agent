# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""变更工单 Excel 单元测试（Skill scripts）。"""

import sys
import io
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[2] / "src" / "skills" / "itsm-change-ticket-writer" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from change_ticket_excel import build_change_ticket_workbook  # noqa: E402


def test_build_change_ticket_workbook_minimal():
    manifest = {
        "ticket_id": "REQ001",
        "ticket_title": "测试变更",
        "change_background": "背景",
        "change_purpose": "目的",
        "devices": [{"device_name": "FW-01", "vendor": "华为", "ip_address": "10.0.0.1"}],
        "scripts": [
            {
                "device_name": "FW-01",
                "vendor": "华为",
                "order": 1,
                "commands": "acl number 3001\n rule permit ip",
                "command_count": 2,
            }
        ],
        "rollback": [{"step": 1, "device_name": "FW-01", "rollback_command": "undo acl 3001"}],
    }
    data = build_change_ticket_workbook(manifest, workflow_run_id="run-1")
    assert len(data) > 1000
    assert data[:2] == b"PK"


def test_ticket_download_is_not_overwritten_by_later_edits(tmp_path, monkeypatch):
    from src.core.skills import executor

    objects = {}

    class Storage:
        def is_ready(self):
            return True

        def upload_file(self, key, data, content_type):
            objects[key] = data
            return True

        def get_presigned_url(self, key, expires):
            return f"/api/artifacts/{key}"

    monkeypatch.setattr(executor, "get_minio_storage", lambda: Storage())
    path = tmp_path / "output.xlsx"
    original = {"ticket_id": "REQ001", "ticket_title": "首次工单", "devices": [], "scripts": []}
    path.write_bytes(build_change_ticket_workbook(original))
    first = executor._upload_file_output({}, str(path), {"ticket_id": "REQ001"}, "itsm-change-ticket-writer")
    path.write_bytes(build_change_ticket_workbook({**original, "ticket_title": "修改后的工单"}))
    second = executor._upload_file_output({}, str(path), {"ticket_id": "REQ001"}, "itsm-change-ticket-writer")
    a, b = (item["artifacts"]["change_excel"] for item in (first, second))
    assert a["file_key"] != b["file_key"]
    assert a["filename"] == b["filename"] == "变更工单.xlsx"
    from openpyxl import load_workbook
    assert load_workbook(io.BytesIO(objects[a["file_key"]])).active["D1"].value == "首次工单"
    assert load_workbook(io.BytesIO(objects[b["file_key"]])).active["D1"].value == "修改后的工单"
