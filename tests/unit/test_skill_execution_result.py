# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""SkillExecutionResult v1 单元测试。"""

from src.core.skills.result import SkillExecutionResult, SkillStatus


def test_from_legacy_firewall_zip():
    raw = {
        "success": True,
        "message": "策略已生成",
        "manifest": {"rules": 3},
        "download_url": "https://minio/policies.zip",
        "config_file_key": "firewall_policies/REQ001/policies.zip",
        "filename": "policies.zip",
    }
    ser = SkillExecutionResult.from_legacy_dict(raw, skill_name="firewall-policy-generator")
    assert ser.success
    assert ser.status == SkillStatus.SUCCESS
    assert "config_zip" in ser.artifacts
    assert ser.artifacts["config_zip"].file_key == "firewall_policies/REQ001/policies.zip"
    assert ser.output.get("manifest") == {"rules": 3}


def test_to_legacy_roundtrip():
    raw = {
        "success": True,
        "message": "工单已生成",
        "change_excel_url": "https://minio/change.xlsx",
        "change_excel_file_key": "change_tickets/REQ001/change.xlsx",
    }
    ser = SkillExecutionResult.from_legacy_dict(raw, skill_name="itsm-change-ticket-writer")
    legacy = ser.to_legacy_dict()
    assert legacy["success"] is True
    assert legacy["execution_id"] == ser.execution_id
    assert "change_excel" in legacy["artifacts"]
    assert legacy.get("change_excel_url") == "https://minio/change.xlsx"


def test_docx_download_roundtrip():
    raw = {
        "success": True,
        "message": "公文撰写完成",
        "download_url": "/api/artifacts/download/?key=documents%2Fa.docx&exp=1&sig=x",
        "filename": "请示_20260101120000.docx",
    }
    ser = SkillExecutionResult.from_legacy_dict(raw, skill_name="official-document-writing")
    assert "docx_file" in ser.artifacts
    legacy = ser.to_legacy_dict()
    assert legacy.get("download_url") == raw["download_url"]
    assert legacy["artifacts"]["docx_file"]["download_url"] == raw["download_url"]


def test_raw_outputs_archive_roundtrip():
    raw = {
        "success": True,
        "message": "patrol complete",
        "download_url": "https://minio/device_patrol/raw.zip",
        "raw_outputs_url": "https://minio/device_patrol/raw.zip",
    }
    ser = SkillExecutionResult.from_legacy_dict(raw, skill_name="device-patrol")

    assert "raw_outputs_archive" in ser.artifacts
    legacy = ser.to_legacy_dict()
    assert legacy["artifacts"]["raw_outputs_archive"]["download_url"] == raw["raw_outputs_url"]


def test_error_status():
    raw = {"success": False, "error": "脚本超时"}
    ser = SkillExecutionResult.from_legacy_dict(raw, skill_name="device-backup")
    assert ser.status == SkillStatus.ERROR
    assert ser.error_info is not None
    assert "超时" in ser.error_info.message
