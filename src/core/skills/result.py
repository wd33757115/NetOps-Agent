# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""Skill 执行结果标准契约 v1（生产级统一 envelope）。"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


class SkillStatus(str, Enum):
    SUCCESS = "success"
    ERROR = "error"
    PARTIAL = "partial"
    SKIPPED = "skipped"


class ErrorInfo(BaseModel):
    code: str | None = None
    message: str
    detail: str | None = None


class ArtifactRef(BaseModel):
    kind: str
    file_key: str | None = None
    download_url: str | None = None
    filename: str | None = None
    content_type: str | None = None
    size_bytes: int | None = None
    checksum: str | None = None

    @classmethod
    def from_legacy_dict(cls, kind: str, raw: dict[str, Any]) -> ArtifactRef:
        return cls(
            kind=kind,
            file_key=raw.get("file_key"),
            download_url=raw.get("download_url"),
            filename=raw.get("filename"),
            content_type=raw.get("content_type"),
            size_bytes=raw.get("size_bytes"),
            checksum=raw.get("checksum"),
        )

    def to_legacy_dict(self) -> dict[str, Any]:
        return {
            "file_key": self.file_key,
            "download_url": self.download_url,
            "filename": self.filename,
            "content_type": self.content_type,
            "size_bytes": self.size_bytes,
            "checksum": self.checksum,
        }


def _is_downloadable_url(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    text = value.strip()
    return (
        text.startswith("http://")
        or text.startswith("https://")
        or text.startswith("/api/")
    )


def _infer_artifact_key(*, filename: str = "", file_key: str = "", download_url: str = "") -> str:
    hint = f"{filename} {file_key} {download_url}".lower()
    if hint.endswith(".xlsx") or "change_excel" in hint or "change_ticket" in hint:
        return "change_excel"
    if hint.endswith(".zip") or "firewall" in hint or "policies" in hint or "config_zip" in hint:
        return "config_zip"
    if hint.endswith(".docx") or "document" in hint:
        return "docx_file"
    if hint.endswith(".pdf"):
        return "pdf_file"
    return "download_file"


def _guess_content_type(filename: str, art_key: str) -> str:
    fn = filename.lower()
    if fn.endswith(".docx") or art_key == "docx_file":
        return "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    if fn.endswith(".xlsx") or art_key == "change_excel":
        return "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    if fn.endswith(".zip") or art_key == "config_zip":
        return "application/zip"
    if fn.endswith(".pdf"):
        return "application/pdf"
    return "application/octet-stream"


class ExecutionContext(BaseModel):
    """一次 Skill 执行的关联上下文。"""

    source: Literal["chat", "workflow", "api", "celery"] = "celery"
    message_id: str | None = None
    thread_id: str | None = None
    run_id: str | None = None
    step_name: str | None = None
    step_id: str | None = None
    user_id: str | None = None
    ticket_id: str | None = None
    trace_id: str | None = None


class SkillExecutionResult(BaseModel):
    schema_version: Literal["1"] = "1"
    execution_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    skill_name: str
    skill_version: str = "0.0.0"
    status: SkillStatus
    message: str = ""
    output: dict[str, Any] = Field(default_factory=dict)
    artifacts: dict[str, ArtifactRef] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    error_info: ErrorInfo | None = None
    context: ExecutionContext = Field(default_factory=ExecutionContext)
    executed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def success(self) -> bool:
        return self.status == SkillStatus.SUCCESS

    def to_legacy_dict(self) -> dict[str, Any]:
        """兼容 Workflow / 旧 intermediate_results / normalize_step_result。"""
        legacy_artifacts = {k: v.to_legacy_dict() for k, v in self.artifacts.items()}
        payload: dict[str, Any] = {
            "success": self.success,
            "status": self.status.value,
            "message": self.message,
            "execution_id": self.execution_id,
            "skill_name": self.skill_name,
            "skill_version": self.skill_version,
            "artifacts": legacy_artifacts,
            "metadata": dict(self.metadata),
            "executed_at": self.executed_at.isoformat(),
        }
        if self.output:
            payload["output"] = dict(self.output)
            if "manifest" in self.output:
                payload["manifest"] = self.output["manifest"]
        if self.error_info:
            payload["error"] = self.error_info.message
            payload["error_info"] = self.error_info.model_dump()
        zip_art = self.artifacts.get("config_zip")
        if zip_art:
            payload["download_url"] = zip_art.download_url
            payload["config_file_key"] = zip_art.file_key
            payload["filename"] = zip_art.filename
        excel_art = self.artifacts.get("change_excel")
        if excel_art:
            payload["change_excel_url"] = excel_art.download_url
            payload["change_excel_file_key"] = excel_art.file_key
        if not payload.get("download_url"):
            for art in self.artifacts.values():
                if art.download_url:
                    payload["download_url"] = art.download_url
                    if art.filename and not payload.get("filename"):
                        payload["filename"] = art.filename
                    break
        for key, val in self.output.items():
            if key not in payload and key not in ("artifacts",):
                payload.setdefault(key, val)
        return payload

    def to_summary(self) -> dict[str, Any]:
        return {
            "execution_id": self.execution_id,
            "skill_name": self.skill_name,
            "status": self.status.value,
            "message": self.message,
            "artifact_keys": list(self.artifacts.keys()),
            "duration_ms": self.metadata.get("duration_ms"),
        }

    @classmethod
    def from_legacy_dict(
        cls,
        raw: dict[str, Any],
        *,
        skill_name: str,
        skill_version: str = "0.0.0",
        context: ExecutionContext | None = None,
        execution_id: str | None = None,
    ) -> SkillExecutionResult:
        if not raw:
            return cls(
                execution_id=execution_id or str(uuid.uuid4()),
                skill_name=skill_name,
                skill_version=skill_version,
                status=SkillStatus.ERROR,
                message="空结果",
                error_info=ErrorInfo(message="空结果"),
                context=context or ExecutionContext(),
            )

        status_raw = raw.get("status")
        if status_raw in {s.value for s in SkillStatus}:
            status = SkillStatus(status_raw)
        elif raw.get("success") is False or raw.get("error"):
            status = SkillStatus.ERROR
        elif raw.get("success") is True or raw.get("status") == "success":
            status = SkillStatus.SUCCESS
        else:
            status = SkillStatus.SUCCESS

        artifacts: dict[str, ArtifactRef] = {}
        raw_artifacts = raw.get("artifacts") or {}
        if isinstance(raw_artifacts, dict):
            for key, meta in raw_artifacts.items():
                if key == "manifest":
                    continue
                if isinstance(meta, dict):
                    artifacts[key] = ArtifactRef.from_legacy_dict(key, meta)

        filename = raw.get("filename") or ""
        file_key = raw.get("config_file_key") or ""
        if raw.get("download_url") and "config_zip" not in artifacts:
            if str(filename).lower().endswith(".zip") or str(file_key).startswith("firewall_policies/"):
                artifacts["config_zip"] = ArtifactRef(
                    kind="config_zip",
                    file_key=raw.get("config_file_key"),
                    download_url=raw.get("download_url"),
                    filename=filename or "firewall_policies.zip",
                    content_type="application/zip",
                )
        if raw.get("change_excel_url") and "change_excel" not in artifacts:
            artifacts["change_excel"] = ArtifactRef(
                kind="change_excel",
                file_key=raw.get("change_excel_file_key"),
                download_url=raw.get("change_excel_url"),
                filename=raw.get("change_excel_filename") or "change_ticket.xlsx",
                content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        if raw.get("raw_outputs_url") and "raw_outputs_archive" not in artifacts:
            artifacts["raw_outputs_archive"] = ArtifactRef(
                kind="raw_outputs_archive",
                file_key=raw.get("raw_outputs_file_key"),
                download_url=raw.get("raw_outputs_url"),
                filename=raw.get("raw_outputs_filename") or "raw-cli-outputs.zip",
                content_type="application/zip",
            )

        download_url = raw.get("download_url")
        if _is_downloadable_url(download_url):
            has_url = any(
                _is_downloadable_url(getattr(art, "download_url", None))
                for art in artifacts.values()
            )
            if not has_url:
                filename = str(raw.get("filename") or raw.get("docx_filename") or "")
                file_key = str(raw.get("config_file_key") or raw.get("file_key") or "")
                art_key = _infer_artifact_key(
                    filename=filename,
                    file_key=file_key,
                    download_url=str(download_url),
                )
                artifacts[art_key] = ArtifactRef(
                    kind=art_key,
                    file_key=file_key or None,
                    download_url=str(download_url).strip(),
                    filename=filename or None,
                    content_type=_guess_content_type(filename, art_key),
                )

        skip_output = {
            "success",
            "status",
            "message",
            "error",
            "artifacts",
            "download_url",
            "raw_outputs_url",
            "config_file_key",
            "change_excel_url",
            "execution_time_ms",
            "data",
        }
        output: dict[str, Any] = {}
        if isinstance(raw.get("data"), dict):
            output.update(raw["data"])
        manifest = raw.get("manifest") or output.get("manifest")
        if manifest is not None:
            output["manifest"] = manifest
        for key, val in raw.items():
            if key in skip_output or key.startswith("_"):
                continue
            if key not in output:
                output[key] = val

        error_info = None
        if status == SkillStatus.ERROR:
            err_msg = str(raw.get("error") or raw.get("message") or "Skill 执行失败")
            error_info = ErrorInfo(
                code=raw.get("error_code"),
                message=err_msg,
                detail=raw.get("error_detail"),
            )

        metadata = dict(raw.get("metadata") or {})
        if raw.get("execution_time_ms") is not None:
            metadata.setdefault("duration_ms", raw["execution_time_ms"])
        if raw.get("celery_task_id"):
            metadata["celery_task_id"] = raw["celery_task_id"]

        return cls(
            execution_id=execution_id or raw.get("execution_id") or str(uuid.uuid4()),
            skill_name=skill_name,
            skill_version=skill_version,
            status=status,
            message=str(raw.get("message") or ""),
            output=output,
            artifacts=artifacts,
            metadata=metadata,
            error_info=error_info,
            context=context or ExecutionContext(),
        )

    @classmethod
    def from_skill_result(
        cls,
        result: Any,
        *,
        skill_name: str,
        skill_version: str = "0.0.0",
        context: ExecutionContext | None = None,
        execution_id: str | None = None,
    ) -> SkillExecutionResult:
        raw: dict[str, Any] = {
            "success": getattr(result, "success", False),
            "message": getattr(result, "message", ""),
            "error": getattr(result, "error", None),
            "download_url": getattr(result, "download_url", None),
            "execution_time_ms": getattr(result, "execution_time_ms", 0),
        }
        data = getattr(result, "data", None)
        if isinstance(data, dict):
            raw.update(data)
            raw["data"] = data
        return cls.from_legacy_dict(
            raw,
            skill_name=skill_name,
            skill_version=skill_version,
            context=context,
            execution_id=execution_id,
        )
