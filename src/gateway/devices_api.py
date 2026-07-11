"""Device Management API."""

from __future__ import annotations

import base64
import binascii
import csv
import io
import json
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, or_

from src.auth.dependencies import require_role
from src.auth.models import CurrentUser
from src.core.agent_modules.control import (
    AGENT_MODULE_CONTROL_STATUSES,
    AGENT_MODULE_IDS,
    agent_module_control_violations,
    module_control_dict,
)
from src.core.collection import CollectionPolicyResolver, ExecutionPlanRepository
from src.core.credentials import CredentialResolver
from src.core.device_ops.identity_discovery import (
    discover_device_identity,
    identity_evidence,
    platform_for_identity,
)
from src.core.device_ops.repository import DeviceRepository, normalize_device_status
from src.core.evidence import evidence_id_for_command_snapshot
from src.core.patrol.modes import PRODUCTION_MODE, normalize_patrol_mode
from src.core.rule_control import RuntimeRuleAssetResolver
from src.gateway.audit_service import write_audit_log
from src.infrastructure.db.models import (
    AgentModuleControlRecord,
    CollectionExecutionPlanRecord,
    CollectionProfileBindingRecord,
    CommandProfileVersionRecord,
    CommandSnapshot,
    CurrentStateRecord,
    EventRuleVersionRecord,
    NetworkChangeRecord,
    NetworkDevice,
    NetworkDeviceDynamicGroup,
    NetworkEventOccurrenceRecord,
    NetworkEventRecord,
    ParserDeviceProfile,
    ParserIdentitySignature,
    ParserTemplate,
    ParserTemplateJob,
    PatrolRun,
    RawEvidenceRecord,
    StateObservationRecord,
    WorkflowRun,
    WorkflowStepRecord,
)
from src.infrastructure.db.postgres import get_db_session

router = APIRouter(
    prefix="/api/v1/devices",
    tags=["Device Management"],
    dependencies=[Depends(require_role(["admin"]))],
)


class DevicePlanPreviewRequest(BaseModel):
    action: str = "patrol"
    mode: str = "production"


class DeviceUpsertRequest(BaseModel):
    device_name: str = Field(..., min_length=1)
    management_ip: str = Field(..., min_length=1)
    username: str | None = None
    credential_ref: str | None = None
    vendor: str | None = None
    model: str | None = None
    family: str | None = None
    platform: str | None = None
    device_type: str | None = None
    os_version: str | None = None
    serial_number: str | None = None
    site: str | None = None
    role: str | None = None
    status: str = "active"
    output_path: str | None = None
    command_profile: str | None = None
    attributes: dict[str, Any] | None = None
    groups: list[str] | None = None


class DeviceUpdateRequest(BaseModel):
    device_name: str | None = None
    management_ip: str | None = None
    username: str | None = None
    credential_ref: str | None = None
    vendor: str | None = None
    model: str | None = None
    family: str | None = None
    platform: str | None = None
    device_type: str | None = None
    os_version: str | None = None
    serial_number: str | None = None
    site: str | None = None
    role: str | None = None
    status: str | None = None
    output_path: str | None = None
    command_profile: str | None = None
    attributes: dict[str, Any] | None = None
    groups: list[str] | None = None


class DeviceStatusRequest(BaseModel):
    status: str = Field(..., min_length=1)


class DeviceGroupsRequest(BaseModel):
    groups: list[str] = Field(default_factory=list)


class DeviceDynamicGroupRequest(BaseModel):
    name: str = Field(..., min_length=1)
    selector: dict[str, Any]
    description: str | None = None
    status: str = "active"


class CredentialInspectRequest(BaseModel):
    credential_ref: str | None = None


class DeviceImportRequest(BaseModel):
    content: str = Field(..., min_length=1)
    format: str = "csv"
    filename: str | None = None
    dry_run: bool = True


class DeviceBulkFilters(BaseModel):
    device_id: str | None = None
    device_name: str | None = None
    ip: str | None = None
    group: str | None = None
    vendor: str | None = None
    model: str | None = None
    family: str | None = None
    platform: str | None = None
    device_type: str | None = None
    site: str | None = None
    role: str | None = None
    status: str | None = None
    connection_status: str | None = None


class DeviceBulkUpdate(BaseModel):
    command_profile: str | None = None
    credential_ref: str | None = None
    status: str | None = None
    platform: str | None = None
    device_type: str | None = None
    site: str | None = None
    role: str | None = None
    groups: list[str] | None = None
    group_mode: str = "replace"


class DeviceBulkRequest(BaseModel):
    device_ids: list[str] | None = None
    dynamic_group_id: str | None = None
    filters: DeviceBulkFilters | None = None
    update: DeviceBulkUpdate


class DeviceBulkSelectionRequest(BaseModel):
    device_ids: list[str] | None = None
    dynamic_group_id: str | None = None
    filters: DeviceBulkFilters | None = None


class DevicePatrolLaunchRequest(BaseModel):
    device_ids: list[str] | None = None
    dynamic_group_id: str | None = None
    filters: DeviceBulkFilters | None = None
    mode: str = "production"
    save_baseline: bool = False
    ticket_id: str | None = None
    launch_mode: str = "workflow"
    workflow_name: str = "patrol-change-event"


class AgentModuleControlRequest(BaseModel):
    status: str = Field(..., min_length=1)
    maintenance_reason: str | None = None
    maintenance_starts_at: datetime | None = None
    maintenance_ends_at: datetime | None = None


DEVICE_IMPORT_COLUMNS: list[dict[str, Any]] = [
    {
        "name": "device_name",
        "required": True,
        "aliases": ["name", "hostname"],
        "description": "设备名称，建议使用网管系统中的唯一名称。",
        "example": "SW1",
    },
    {
        "name": "management_ip",
        "required": True,
        "aliases": ["ip", "mgmt_ip", "management_address"],
        "description": "管理地址，用于连接设备和幂等匹配。",
        "example": "10.0.0.1",
    },
    {
        "name": "vendor",
        "required": False,
        "aliases": [],
        "description": "厂商，例如 Cisco、H3C。",
        "example": "Cisco",
    },
    {
        "name": "model",
        "required": False,
        "aliases": [],
        "description": "精确型号。",
        "example": "WS-C2960XR-24TS-I",
    },
    {
        "name": "family",
        "required": False,
        "aliases": [],
        "description": "设备族。",
        "example": "IOS-C2960",
    },
    {
        "name": "platform",
        "required": False,
        "aliases": [],
        "description": "采集平台类型。",
        "example": "ios",
    },
    {
        "name": "device_type",
        "required": False,
        "aliases": ["type"],
        "description": "设备类型，例如 router、access_switch、firewall。",
        "example": "access_switch",
    },
    {
        "name": "os_version",
        "required": False,
        "aliases": [],
        "description": "系统版本。",
        "example": "15.2(7)E10",
    },
    {
        "name": "serial_number",
        "required": False,
        "aliases": ["serial"],
        "description": "序列号。",
        "example": "FOC1234",
    },
    {
        "name": "site",
        "required": False,
        "aliases": [],
        "description": "站点或机房。",
        "example": "xian",
    },
    {
        "name": "role",
        "required": False,
        "aliases": [],
        "description": "设备角色。",
        "example": "access",
    },
    {
        "name": "status",
        "required": False,
        "aliases": [],
        "description": "设备状态，active=启用，disabled=停用。留空默认 active；旧值 inactive 会自动转换为 disabled。",
        "example": "active",
    },
    {
        "name": "credential_ref",
        "required": False,
        "aliases": [],
        "description": "凭据引用，例如 env:NETOPS_PASSWORD。",
        "example": "env:NETOPS_PASSWORD",
    },
    {
        "name": "command_profile",
        "required": False,
        "aliases": [],
        "description": "采集命令集名称或版本引用。",
        "example": "browser-test-patrol",
    },
    {
        "name": "output_path",
        "required": False,
        "aliases": [],
        "description": "设备原始巡检输出目录，留空使用系统默认位置。",
        "example": "",
    },
    {
        "name": "groups",
        "required": False,
        "aliases": [],
        "description": "逗号分隔的设备分组。",
        "example": "core,xian",
    },
    {
        "name": "attributes_json",
        "required": False,
        "aliases": [],
        "description": "扩展属性 JSON 对象，用于采集参数、身份发现证据等高级配置。",
        "example": '{"collector_options":{"read_timeout":60}}',
    },
]

DEVICE_IMPORT_COLUMN_NAMES = [column["name"] for column in DEVICE_IMPORT_COLUMNS]
DEVICE_IMPORT_REQUIRED_COLUMNS = [
    column["name"] for column in DEVICE_IMPORT_COLUMNS if column["required"]
]
DEVICE_IMPORT_HEADER_ALIASES = {
    alias: column["name"]
    for column in DEVICE_IMPORT_COLUMNS
    for alias in [column["name"], *column.get("aliases", [])]
}


def _redact_device(device: dict[str, Any]) -> dict[str, Any]:
    data = dict(device)
    data.pop("password", None)
    return data


def _audit_device_action(
    request: Request | None,
    action: str,
    resource_type: str,
    resource_id: str | None,
    detail: dict[str, Any] | None = None,
) -> None:
    user = getattr(getattr(request, "state", None), "current_user", None)
    if user is not None and not isinstance(user, CurrentUser):
        user = None
    write_audit_log(
        action=action,
        user_id=user.user_id if user else None,
        username=user.username if user else None,
        resource_type=resource_type,
        resource_id=resource_id,
        detail=detail,
        ip_address=request.client.host if request and request.client else None,
    )


def _split_csv_list(value: str | None) -> list[str]:
    return [item.strip() for item in (value or "").split(",") if item.strip()]


def _parse_json_field(
    value: str | None, *, row_number: int, errors: list[str]
) -> dict[str, Any] | None:
    text = (value or "").strip()
    if not text:
        return None
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        errors.append(f"第 {row_number} 行 attributes_json 不是合法 JSON：{exc.msg}")
        return None
    if not isinstance(data, dict):
        errors.append(f"第 {row_number} 行 attributes_json 必须是 JSON 对象")
        return None
    return data


def _build_import_row(
    normalized: dict[str, Any],
    *,
    row_number: int,
    row_errors: list[str] | None = None,
) -> dict[str, Any]:
    errors: list[str] = list(row_errors or [])
    management_ip = normalized.get("management_ip") or ""
    device_name = normalized.get("device_name") or ""
    if not device_name:
        errors.append(f"第 {row_number} 行缺少必填列 device_name")
    if not management_ip:
        errors.append(f"第 {row_number} 行缺少必填列 management_ip")
    try:
        status = normalize_device_status(normalized.get("status") or "active")
        if status == "deleted":
            errors.append(f"第 {row_number} 行 status 只能是 active 或 disabled")
    except ValueError:
        status = normalized.get("status") or "active"
        errors.append(f"第 {row_number} 行 status 只能是 active 或 disabled")
    attributes = _parse_json_field(
        normalized.get("attributes_json"), row_number=row_number, errors=errors
    )
    payload = {
        "device_name": device_name,
        "management_ip": management_ip,
        "username": normalized.get("username") or None,
        "credential_ref": normalized.get("credential_ref") or None,
        "vendor": normalized.get("vendor") or None,
        "model": normalized.get("model") or None,
        "family": normalized.get("family") or None,
        "platform": normalized.get("platform") or None,
        "device_type": normalized.get("device_type") or None,
        "os_version": normalized.get("os_version") or None,
        "serial_number": normalized.get("serial_number") or None,
        "site": normalized.get("site") or None,
        "role": normalized.get("role") or None,
        "status": status,
        "output_path": normalized.get("output_path") or None,
        "command_profile": normalized.get("command_profile") or None,
        "attributes": attributes,
        "groups": _split_csv_list(normalized.get("groups")),
    }
    return {
        "row_number": row_number,
        "valid": not errors,
        "errors": errors,
        "device": payload,
    }


def _canonical_import_headers(fieldnames: list[Any]) -> tuple[list[str], list[str]]:
    canonical_headers: list[str] = []
    errors: list[str] = []
    seen: set[str] = set()
    unknown: list[str] = []
    duplicates: list[str] = []
    for value in fieldnames:
        raw = str(value or "").strip()
        if not raw:
            continue
        key = raw.lower()
        canonical = DEVICE_IMPORT_HEADER_ALIASES.get(key)
        if canonical is None:
            unknown.append(raw)
            continue
        if canonical in seen:
            duplicates.append(raw)
            continue
        seen.add(canonical)
        canonical_headers.append(canonical)
    missing = [name for name in DEVICE_IMPORT_REQUIRED_COLUMNS if name not in seen]
    if missing:
        errors.append(f"缺少必填列：{', '.join(missing)}")
    if unknown:
        errors.append(f"存在未识别列：{', '.join(unknown)}")
    if duplicates:
        errors.append(f"存在重复列或别名冲突：{', '.join(duplicates)}")
    return canonical_headers, errors


def _raise_import_header_error(errors: list[str]) -> None:
    raise HTTPException(
        status_code=400,
        detail={
            "message": "设备导入表头校验失败",
            "errors": errors,
            "required_columns": DEVICE_IMPORT_REQUIRED_COLUMNS,
            "supported_columns": DEVICE_IMPORT_COLUMNS,
            "template": _device_import_template_payload(),
        },
    )


def _parse_csv_import_rows(content: str) -> list[dict[str, Any]]:
    reader = csv.DictReader(io.StringIO(content.strip()))
    if not reader.fieldnames:
        raise HTTPException(status_code=400, detail="CSV 必须包含表头")
    canonical_headers, header_errors = _canonical_import_headers(reader.fieldnames)
    if header_errors:
        _raise_import_header_error(header_errors)
    rows: list[dict[str, Any]] = []
    for index, raw in enumerate(reader, start=2):
        row_errors: list[str] = []
        if raw.get(None):
            row_errors.append(f"第 {index} 行列数超过表头，请检查逗号或引号")
        normalized = {
            DEVICE_IMPORT_HEADER_ALIASES.get(
                str(key or "").strip().lower(), str(key or "").strip().lower()
            ): (value.strip() if isinstance(value, str) else value)
            for key, value in raw.items()
            if key is not None
        }
        normalized = {key: normalized.get(key, "") for key in canonical_headers}
        rows.append(_build_import_row(normalized, row_number=index, row_errors=row_errors))
    return rows


def _parse_xlsx_import_rows(content: str) -> list[dict[str, Any]]:
    try:
        raw = base64.b64decode(content, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise HTTPException(status_code=400, detail="xlsx content must be base64 encoded") from exc
    try:
        from openpyxl import load_workbook
    except ImportError as exc:
        raise HTTPException(status_code=500, detail="openpyxl is required for xlsx import") from exc
    try:
        workbook = load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"invalid xlsx file: {exc}") from exc
    sheet = workbook.active
    rows_iter = sheet.iter_rows(values_only=True)
    try:
        header_row = next(rows_iter)
    except StopIteration as exc:
        raise HTTPException(status_code=400, detail="XLSX 必须包含表头") from exc
    headers = [str(value or "").strip() for value in header_row]
    if not any(headers):
        raise HTTPException(status_code=400, detail="XLSX 必须包含表头")
    canonical_headers, header_errors = _canonical_import_headers(headers)
    if header_errors:
        _raise_import_header_error(header_errors)
    rows: list[dict[str, Any]] = []
    for index, values in enumerate(rows_iter, start=2):
        if not any(value is not None and str(value).strip() for value in values):
            continue
        normalized: dict[str, Any] = {}
        row_errors: list[str] = []
        if len(values) > len(headers) and any(
            value is not None and str(value).strip() for value in values[len(headers) :]
        ):
            row_errors.append(f"第 {index} 行列数超过表头，请检查多余单元格")
        for header, value in zip(headers, values):
            canonical = DEVICE_IMPORT_HEADER_ALIASES.get(str(header or "").strip().lower())
            if canonical:
                normalized[canonical] = str(value).strip() if value is not None else ""
        normalized = {key: normalized.get(key, "") for key in canonical_headers}
        rows.append(_build_import_row(normalized, row_number=index, row_errors=row_errors))
    return rows


def _parse_import_rows(payload: DeviceImportRequest) -> list[dict[str, Any]]:
    import_format = (payload.format or "csv").strip().lower()
    if import_format == "csv":
        return _parse_csv_import_rows(payload.content)
    if import_format in {"xlsx", "excel"}:
        return _parse_xlsx_import_rows(payload.content)
    raise HTTPException(status_code=400, detail="only csv and xlsx import are supported")


def _device_import_template_payload() -> dict[str, Any]:
    sample_row = {column["name"]: column["example"] for column in DEVICE_IMPORT_COLUMNS}
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=DEVICE_IMPORT_COLUMN_NAMES, lineterminator="\n")
    writer.writeheader()
    writer.writerow(sample_row)
    return {
        "filename": "device-import-template.csv",
        "format": "csv",
        "columns": DEVICE_IMPORT_COLUMNS,
        "required_columns": DEVICE_IMPORT_REQUIRED_COLUMNS,
        "csv": buffer.getvalue(),
    }


def _preview_import(payload: DeviceImportRequest, repository: DeviceRepository) -> dict[str, Any]:
    rows = _parse_import_rows(payload)
    preview_rows: list[dict[str, Any]] = []
    create_count = 0
    update_count = 0
    invalid_count = 0
    for row in rows:
        existing = None
        if row["valid"]:
            device = row["device"]
            existing = repository.get_device_by_ip_or_name(device["management_ip"])
            if not existing:
                exact_name_matches = repository.get_devices_by_filter(
                    device_name=device["device_name"],
                    status=None,
                )
                existing = exact_name_matches[0] if exact_name_matches else None
            row["action"] = "update" if existing else "create"
            row["existing_device_id"] = existing.get("device_id") if existing else None
            if existing:
                update_count += 1
            else:
                create_count += 1
        else:
            row["action"] = "invalid"
            row["existing_device_id"] = None
            invalid_count += 1
        preview_rows.append(row)
    return {
        "success": True,
        "total": len(preview_rows),
        "create_count": create_count,
        "update_count": update_count,
        "invalid_count": invalid_count,
        "rows": preview_rows,
    }


def _resolve_bulk_devices(
    payload: DeviceBulkRequest | DeviceBulkSelectionRequest,
    repository: DeviceRepository,
    *,
    active_only: bool = False,
) -> list[dict[str, Any]]:
    devices_by_id: dict[str, dict[str, Any]] = {}
    for device_id in payload.device_ids or []:
        matches = repository.get_devices_by_filter(device_id=device_id, status=None)
        if matches and (not active_only or matches[0].get("status", "active") == "active"):
            devices_by_id[matches[0]["device_id"]] = matches[0]
    if payload.dynamic_group_id:
        group, group_devices = repository.resolve_dynamic_group_devices(payload.dynamic_group_id)
        if group is None:
            raise HTTPException(status_code=404, detail="dynamic group not found")
        if group.get("status") != "active":
            raise HTTPException(status_code=400, detail="dynamic group is not active")
        for device in group_devices:
            if not active_only or device.get("status", "active") == "active":
                devices_by_id[device["device_id"]] = device
    if payload.filters:
        filters = payload.filters.model_dump()
        if active_only:
            filters["status"] = "active"
        for device in repository.get_devices_by_filter(**filters):
            devices_by_id[device["device_id"]] = device
    return sorted(
        devices_by_id.values(),
        key=lambda item: (item.get("device_name") or "", item.get("ip") or ""),
    )


def _production_parser_coverage(
    device: dict[str, Any], commands: list[str]
) -> list[dict[str, Any]]:
    from src.core.device_ops.tools import PatrolTool

    return PatrolTool._production_coverage(device, commands)


def _blocking_parser_coverage(
    device: dict[str, Any], commands: list[str], mode: str
) -> list[dict[str, Any]]:
    if normalize_patrol_mode(mode) != PRODUCTION_MODE:
        return []
    return [
        item
        for item in _production_parser_coverage(device, commands)
        if item.get("policy_status") == "eligible" and item.get("coverage_status") != "ready"
    ]


def _runtime_rule_asset_health(
    *,
    mode: str,
    coverage: list[dict[str, Any]],
) -> dict[str, Any]:
    if normalize_patrol_mode(mode) != PRODUCTION_MODE:
        return {"status": "not_required", "ready": True}
    schemas = []
    for item in coverage:
        template_id = str(item.get("template_id") or "").strip()
        if (
            item.get("policy_status") == "eligible"
            and item.get("coverage_status") == "ready"
            and template_id
        ):
            schemas.append(("cli", template_id))
    if not schemas:
        return {
            "status": "not_required",
            "ready": True,
            "reason": "no ready parser templates require runtime rule asset checks",
        }
    try:
        bundle = (
            RuntimeRuleAssetResolver()
            .load_bundle(
                observation_schemas=schemas,
                include_event_rules=True,
            )
            .to_dict()
        )
    except Exception as exc:
        return {
            "status": "error",
            "ready": False,
            "error": str(exc),
            "observation_schemas": [f"{source}:{schema}" for source, schema in schemas],
        }
    missing_count = len(bundle.get("missing_observation_contracts") or []) + len(
        bundle.get("missing_change_policies") or []
    )
    return {
        **bundle,
        "status": "ready" if bundle.get("ready") else "blocked",
        "missing_count": missing_count,
    }


def _execution_plan_violations(plan: Any, mode: str) -> list[dict[str, Any]]:
    policy_source = str(getattr(plan, "policy_source", "") or "").strip()
    commands = list(getattr(plan, "commands", []) or [])
    profile_version_id = str(getattr(plan, "profile_version_id", "") or "").strip()
    normalized_mode = normalize_patrol_mode(mode)
    if normalized_mode != PRODUCTION_MODE and commands:
        return []
    violations = []
    if normalized_mode == PRODUCTION_MODE and not profile_version_id:
        violations.append(
            {
                "failure_code": "active_command_profile_required",
                "message": "production patrol requires an active command profile version",
                "policy_source": policy_source or "unknown",
                "profile_name": getattr(plan, "profile_name", None),
            }
        )
    if not commands:
        violations.append(
            {
                "failure_code": "collection_commands_required",
                "message": "patrol requires commands from an active command profile binding",
                "policy_source": policy_source or "unknown",
                "profile_name": getattr(plan, "profile_name", None),
            }
        )
    return violations


def _coverage_summary(
    *,
    mode: str,
    commands: list[str],
    coverage: list[dict[str, Any]],
    connection_issues: list[dict[str, Any]],
    plan_violations: list[dict[str, Any]] | None = None,
    runtime_asset_health: dict[str, Any] | None = None,
) -> dict[str, Any]:
    normalized_mode = normalize_patrol_mode(mode)
    plan_violations = plan_violations or []
    eligible_count = sum(1 for item in coverage if item.get("policy_status") == "eligible")
    ready_count = sum(
        1
        for item in coverage
        if item.get("policy_status") == "eligible" and item.get("coverage_status") == "ready"
    )
    blocking = [
        item
        for item in coverage
        if item.get("policy_status") == "eligible" and item.get("coverage_status") != "ready"
    ]
    status = "ready"
    if normalized_mode != PRODUCTION_MODE:
        status = "blocked" if connection_issues else "not_required"
    elif (
        plan_violations
        or blocking
        or connection_issues
        or (runtime_asset_health or {}).get("status") in {"blocked", "error"}
    ):
        status = "blocked"
    return {
        "status": status,
        "mode": normalized_mode,
        "command_count": len(commands),
        "eligible_count": eligible_count,
        "ready_count": ready_count,
        "blocked_count": len(blocking),
        "coverage_rate": ready_count / eligible_count if eligible_count else 1.0,
        "violations": blocking,
        "plan_violations": plan_violations,
        "connection_issues": connection_issues,
        "runtime_asset_health": runtime_asset_health or {"status": "not_checked", "ready": False},
    }


def _connection_preflight(device: dict[str, Any]) -> list[dict[str, Any]]:
    """Return blocking collector issues visible before a patrol is launched."""
    issues: list[dict[str, Any]] = []
    if not (device.get("ip") or device.get("management_ip")):
        issues.append(
            {
                "failure_code": "missing_management_ip",
                "message": "management IP is required",
            }
        )
    if device.get("password"):
        return issues
    credential_ref = str(device.get("credential_ref") or "").strip()
    if not credential_ref:
        issues.append(
            {
                "failure_code": "missing_credential",
                "message": "credential_ref is required before patrol launch",
            }
        )
        return issues
    try:
        if not CredentialResolver().resolve_password(credential_ref):
            issues.append(
                {
                    "failure_code": "credential_unavailable",
                    "message": f"credential_ref did not resolve to a password: {credential_ref}",
                }
            )
    except Exception as exc:
        issues.append(
            {
                "failure_code": "credential_unavailable",
                "message": str(exc),
            }
        )
    return issues


def _latency_ms(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(float(value) * 1000)
    except (TypeError, ValueError):
        return None


def _first_issue(issues: list[dict[str, Any]]) -> tuple[str | None, str | None]:
    if not issues:
        return None, None
    issue = issues[0]
    return (
        str(issue.get("failure_code") or "collector_preflight_failed"),
        str(issue.get("message") or ""),
    )


def _iso(value: Any) -> str | None:
    return value.isoformat() if hasattr(value, "isoformat") else None


def _count_rows(db: Any, model: Any, *filters: Any) -> int:
    query = db.query(model)
    if filters:
        query = query.filter(*filters)
    return int(query.count() or 0)


def _module_check(
    *,
    name: str,
    status: str,
    message: str,
    value: int | float | str | None = None,
    target: str | None = None,
) -> dict[str, Any]:
    return {
        "name": name,
        "status": status,
        "message": message,
        "value": value,
        "target": target,
    }


def _metric(label: str, value: int | float | str, status: str = "ok") -> dict[str, Any]:
    return {"label": label, "value": value, "status": status}


def _evidence(
    *,
    kind: str,
    status: str,
    title: str,
    object_id: str | None = None,
    occurred_at: Any = None,
    summary: str | None = None,
    detail: dict[str, Any] | None = None,
    target_url: str | None = None,
) -> dict[str, Any]:
    return {
        "kind": kind,
        "status": status,
        "title": title,
        "object_id": object_id,
        "occurred_at": _iso(occurred_at),
        "summary": summary,
        "detail": detail or {},
        "target_url": target_url,
    }


def _latest_row(db: Any, model: Any, *filters: Any, order_by: Any = None) -> Any:
    query = db.query(model)
    if filters:
        query = query.filter(*filters)
    if order_by is not None:
        query = query.order_by(order_by.desc())
    return query.first()


def _latest_step(db: Any, skill_names: list[str], status: str | None = None) -> Any:
    filters: list[Any] = [WorkflowStepRecord.skill_name.in_(skill_names)]
    if status:
        filters.append(WorkflowStepRecord.status == status)
    return _latest_row(db, WorkflowStepRecord, *filters, order_by=WorkflowStepRecord.created_at)


def _module_status(checks: list[dict[str, Any]]) -> str:
    statuses = {str(check.get("status") or "") for check in checks}
    if "blocked" in statuses:
        return "blocked"
    if "warning" in statuses:
        return "degraded"
    return "healthy"


def _module_control_dict(row: AgentModuleControlRecord | None, module_id: str) -> dict[str, Any]:
    return module_control_dict(row, module_id)


def _load_agent_module_controls(db: Any) -> dict[str, dict[str, Any]]:
    rows = (
        db.query(AgentModuleControlRecord)
        .filter(AgentModuleControlRecord.module_id.in_(sorted(AGENT_MODULE_IDS)))
        .all()
    )
    return {row.module_id: _module_control_dict(row, row.module_id) for row in rows}


def _effective_module_status(runtime_status: str, control: dict[str, Any]) -> str:
    control_status = str(control.get("status") or "enabled")
    if control_status == "disabled":
        return "disabled"
    if control_status == "maintenance":
        return "maintenance"
    return runtime_status


def _agent_module(
    *,
    module_id: str,
    design_status: str,
    checks: list[dict[str, Any]],
    metrics: list[dict[str, Any]],
    control: dict[str, Any] | None = None,
    evidence: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    runtime_status = _module_status(checks)
    control = control or _module_control_dict(None, module_id)
    return {
        "module_id": module_id,
        "design_status": design_status,
        "runtime_status": runtime_status,
        "effective_status": _effective_module_status(runtime_status, control),
        "control": control,
        "metrics": metrics,
        "checks": checks,
        "evidence": evidence or [],
    }


def _parser_control_evidence(db: Any) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    active_template = _latest_row(
        db,
        ParserTemplate,
        ParserTemplate.status == "active",
        order_by=ParserTemplate.activated_at,
    )
    if active_template:
        items.append(
            _evidence(
                kind="template",
                status="ok",
                title="最近激活模板",
                object_id=active_template.id,
                occurred_at=active_template.activated_at or active_template.updated_at,
                summary=f"{active_template.vendor}/{active_template.profile} 路 {active_template.canonical_command}",
                detail={
                    "version": active_template.version,
                    "asset_path": active_template.asset_path,
                },
                target_url="/devices?tab=parser",
            )
        )
    failed_job = _latest_row(
        db,
        ParserTemplateJob,
        ParserTemplateJob.status == "failed",
        order_by=ParserTemplateJob.completed_at,
    )
    if failed_job:
        items.append(
            _evidence(
                kind="template_job",
                status="failed",
                title="最近失败模板任务",
                object_id=failed_job.id,
                occurred_at=failed_job.completed_at or failed_job.updated_at,
                summary=f"{failed_job.vendor}/{failed_job.model or failed_job.family or failed_job.profile} 路 {failed_job.canonical_command}",
                detail={
                    "failure_code": failed_job.failure_code,
                    "last_error": failed_job.last_error,
                },
                target_url="/devices?tab=parser",
            )
        )
    open_job = _latest_row(
        db,
        ParserTemplateJob,
        ParserTemplateJob.status.in_(["pending", "collecting", "running", "validating"]),
        order_by=ParserTemplateJob.updated_at,
    )
    if open_job:
        items.append(
            _evidence(
                kind="template_job",
                status="warning",
                title="最近开放模板任务",
                object_id=open_job.id,
                occurred_at=open_job.updated_at,
                summary=f"{open_job.status} 路 {open_job.vendor}/{open_job.model or open_job.family or open_job.profile} 路 {open_job.canonical_command}",
                detail={"sample_count": open_job.sample_count, "attempts": open_job.attempts},
                target_url="/devices?tab=parser",
            )
        )
    return items


def _state_normalizer_evidence(db: Any) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    observation = _latest_row(
        db, StateObservationRecord, order_by=StateObservationRecord.observed_at
    )
    if observation:
        items.append(
            _evidence(
                kind="state_observation",
                status="ok",
                title="最近状态观测",
                object_id=observation.id,
                occurred_at=observation.observed_at,
                summary=f"{observation.device_id} 路 {observation.entity_type}/{observation.field_name}",
                detail={"run_id": observation.run_id, "snapshot_id": observation.snapshot_id},
                target_url="/devices?tab=overview",
            )
        )
    current_state = _latest_row(db, CurrentStateRecord, order_by=CurrentStateRecord.updated_at)
    if current_state:
        items.append(
            _evidence(
                kind="current_state",
                status="ok",
                title="最近 CurrentState 更新",
                object_id=current_state.id,
                occurred_at=current_state.updated_at,
                summary=f"{current_state.device_id} 路 {current_state.entity_type}/{current_state.field_name}",
                detail={"source": current_state.source, "version": current_state.version},
                target_url="/devices?tab=overview",
            )
        )
    polluted = _latest_row(
        db,
        CurrentStateRecord,
        CurrentStateRecord.source == "parser_validation",
        order_by=CurrentStateRecord.updated_at,
    )
    if polluted:
        items.append(
            _evidence(
                kind="current_state",
                status="blocked",
                title="验证数据污染证据",
                object_id=polluted.id,
                occurred_at=polluted.updated_at,
                summary=f"{polluted.device_id} 路 {polluted.entity_type}/{polluted.field_name}",
                detail={
                    "source_run_id": polluted.source_run_id,
                    "source_snapshot_id": polluted.source_snapshot_id,
                },
                target_url="/devices?tab=overview",
            )
        )
    return items


def _change_detector_evidence(db: Any) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    change = _latest_row(db, NetworkChangeRecord, order_by=NetworkChangeRecord.detected_at)
    if change:
        items.append(
            _evidence(
                kind="network_change",
                status="ok",
                title="最近网络变化",
                object_id=change.id,
                occurred_at=change.detected_at,
                summary=f"{change.device_id or '-'} 路 {change.entity_type}/{change.entity_key} 路 {change.field}",
                detail={"run_id": change.run_id, "event_status": change.event_status},
                target_url="/devices?tab=overview",
            )
        )
    pending = _latest_row(
        db,
        NetworkChangeRecord,
        NetworkChangeRecord.event_status == "pending",
        order_by=NetworkChangeRecord.detected_at,
    )
    if pending:
        items.append(
            _evidence(
                kind="network_change",
                status="warning",
                title="最近待事件处理变化",
                object_id=pending.id,
                occurred_at=pending.detected_at,
                summary=f"{pending.device_id or '-'} 路 {pending.entity_type}/{pending.entity_key} 路 {pending.field}",
                detail={"run_id": pending.run_id, "canonical_command": pending.canonical_command},
                target_url="/devices?tab=overview",
            )
        )
    step = _latest_step(db, ["change-detector"], status="failed")
    if step:
        items.append(
            _evidence(
                kind="workflow_step",
                status="failed",
                title="最近 change-detector 失败",
                object_id=step.id,
                occurred_at=step.completed_at or step.created_at,
                summary=step.error_message or "Workflow step failed",
                detail={"run_id": step.run_id, "step_name": step.step_name},
                target_url="/devices?tab=overview",
            )
        )
    return items


def _event_builder_evidence(db: Any) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    event = _latest_row(db, NetworkEventRecord, order_by=NetworkEventRecord.occurred_at)
    if event:
        items.append(
            _evidence(
                kind="network_event",
                status="ok",
                title="最近网络事件",
                object_id=event.id,
                occurred_at=event.occurred_at,
                summary=f"{event.event_type} 路 {event.severity} 路 {event.device_id or '-'}",
                detail={"run_id": event.run_id, "source_change_id": event.source_change_id},
                target_url="/devices?tab=overview",
            )
        )
    rule = _latest_row(
        db,
        EventRuleVersionRecord,
        EventRuleVersionRecord.status == "active",
        order_by=EventRuleVersionRecord.activated_at,
    )
    if rule:
        items.append(
            _evidence(
                kind="event_rule",
                status="ok",
                title="最近激活事件规则",
                object_id=rule.id,
                occurred_at=rule.activated_at or rule.created_at,
                summary=f"{rule.event_type} 路 {rule.severity} 路 v{rule.version}",
                detail={"rule_id": rule.rule_id},
                target_url="/devices?tab=parser",
            )
        )
    step = _latest_step(db, ["event-builder"], status="failed")
    if step:
        items.append(
            _evidence(
                kind="workflow_step",
                status="failed",
                title="最近 event-builder 失败",
                object_id=step.id,
                occurred_at=step.completed_at or step.created_at,
                summary=step.error_message or "Workflow step failed",
                detail={"run_id": step.run_id, "step_name": step.step_name},
                target_url="/devices?tab=overview",
            )
        )
    return items


def _device_management_evidence(db: Any) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    device = _latest_row(db, NetworkDevice, order_by=NetworkDevice.updated_at)
    if device:
        items.append(
            _evidence(
                kind="device",
                status="ok",
                title="最近设备资产更新",
                object_id=device.id,
                occurred_at=device.updated_at,
                summary=f"{device.device_name} 路 {device.management_ip}",
                detail={"vendor": device.vendor, "model": device.model, "status": device.status},
                target_url="/devices?tab=overview",
            )
        )
    failed_device = _latest_row(
        db,
        NetworkDevice,
        NetworkDevice.last_connection_status.in_(["failed", "preflight_failed"]),
        order_by=NetworkDevice.last_connection_checked_at,
    )
    if failed_device:
        items.append(
            _evidence(
                kind="connection_health",
                status="warning",
                title="最近连接异常",
                object_id=failed_device.id,
                occurred_at=failed_device.last_connection_checked_at,
                summary=f"{failed_device.device_name} 路 {failed_device.last_connection_failure_code or failed_device.last_connection_status}",
                detail={"error": failed_device.last_connection_error},
                target_url="/devices?tab=overview",
            )
        )
    return items


def _collection_profile_evidence(db: Any) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    profile = _latest_row(
        db,
        CommandProfileVersionRecord,
        CommandProfileVersionRecord.status == "active",
        order_by=CommandProfileVersionRecord.activated_at,
    )
    if profile:
        items.append(
            _evidence(
                kind="command_profile",
                status="ok",
                title="最近激活采集命令集版本",
                object_id=profile.id,
                occurred_at=profile.activated_at or profile.created_at,
                summary=f"profile={profile.profile_id} 路 v{profile.version}",
                detail={"item_count": len(profile.items_json or [])},
                target_url="/devices?tab=parser",
            )
        )
    binding = _latest_row(
        db,
        CollectionProfileBindingRecord,
        CollectionProfileBindingRecord.status == "active",
        order_by=CollectionProfileBindingRecord.updated_at,
    )
    if binding:
        items.append(
            _evidence(
                kind="profile_binding",
                status="ok",
                title="最近采集命令集绑定",
                object_id=binding.id,
                occurred_at=binding.updated_at,
                summary=f"profile_version={binding.profile_version_id} 路 priority={binding.priority}",
                detail={"selector": binding.selector_json},
                target_url="/devices?tab=parser",
            )
        )
    return items


def _report_diff_evidence(db: Any) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    run = _latest_row(
        db,
        WorkflowRun,
        WorkflowRun.template_name == "patrol-change-event",
        order_by=WorkflowRun.created_at,
    )
    if run:
        context = run.context or {}
        items.append(
            _evidence(
                kind="workflow_run",
                status="ok"
                if run.status == "completed"
                else "warning"
                if run.status in {"running", "pending"}
                else "failed",
                title="最近巡检 Workflow",
                object_id=run.id,
                occurred_at=run.completed_at or run.created_at,
                summary=f"{run.status} 路 {context.get('device_name') or context.get('device_id') or '-'}",
                detail={
                    "patrol_batch_id": context.get("patrol_batch_id"),
                    "error_message": run.error_message,
                },
                target_url="/devices?tab=overview",
            )
        )
    failed_step = _latest_step(db, ["patrol-diff", "report-generator"], status="failed")
    if failed_step:
        items.append(
            _evidence(
                kind="workflow_step",
                status="failed",
                title="最近报告/差异失败",
                object_id=failed_step.id,
                occurred_at=failed_step.completed_at or failed_step.created_at,
                summary=failed_step.error_message or "Workflow step failed",
                detail={"run_id": failed_step.run_id, "skill_name": failed_step.skill_name},
                target_url="/devices?tab=overview",
            )
        )
    return items


def _build_agent_module_health(db: Any) -> list[dict[str, Any]]:
    controls = _load_agent_module_controls(db)
    active_templates = _count_rows(db, ParserTemplate, ParserTemplate.status == "active")
    total_templates = _count_rows(db, ParserTemplate)
    failed_jobs = _count_rows(db, ParserTemplateJob, ParserTemplateJob.status == "failed")
    open_jobs = _count_rows(
        db,
        ParserTemplateJob,
        ParserTemplateJob.status.in_(["pending", "collecting", "running", "validating"]),
    )
    template_missing = _count_rows(
        db,
        CommandSnapshot,
        CommandSnapshot.parse_status == "template_missing",
    )
    parse_failed = _count_rows(
        db,
        CommandSnapshot,
        CommandSnapshot.parse_status == "parse_failed",
    )
    parser_profiles = _count_rows(
        db, ParserDeviceProfile, ParserDeviceProfile.status == "confirmed"
    )
    identity_signatures = _count_rows(
        db,
        ParserIdentitySignature,
        ParserIdentitySignature.status == "confirmed",
    )

    observations = _count_rows(db, StateObservationRecord)
    production_observations = _count_rows(
        db,
        StateObservationRecord,
        StateObservationRecord.mode == "production",
    )
    current_states = _count_rows(db, CurrentStateRecord)
    validation_current_states = _count_rows(
        db,
        CurrentStateRecord,
        CurrentStateRecord.source == "parser_validation",
    )

    changes = _count_rows(db, NetworkChangeRecord)
    observation_backed_changes = _count_rows(
        db,
        NetworkChangeRecord,
        or_(
            NetworkChangeRecord.previous_observation_id.is_not(None),
            NetworkChangeRecord.current_observation_id.is_not(None),
        ),
    )
    pending_changes = _count_rows(
        db,
        NetworkChangeRecord,
        NetworkChangeRecord.event_status == "pending",
    )

    events = _count_rows(db, NetworkEventRecord)
    active_event_rules = _count_rows(
        db,
        EventRuleVersionRecord,
        EventRuleVersionRecord.status == "active",
    )
    no_event_changes = _count_rows(
        db,
        NetworkChangeRecord,
        NetworkChangeRecord.event_status == "no_event",
    )

    devices = _count_rows(db, NetworkDevice)
    active_devices = _count_rows(db, NetworkDevice, NetworkDevice.status == "active")
    credential_devices = _count_rows(
        db,
        NetworkDevice,
        NetworkDevice.status == "active",
        NetworkDevice.credential_ref.is_not(None),
    )
    connected_devices = _count_rows(
        db,
        NetworkDevice,
        NetworkDevice.last_connection_status == "connected",
    )
    connection_failed = _count_rows(
        db,
        NetworkDevice,
        NetworkDevice.last_connection_status.in_(["failed", "preflight_failed"]),
    )
    dynamic_groups = _count_rows(
        db,
        NetworkDeviceDynamicGroup,
        NetworkDeviceDynamicGroup.status == "active",
    )

    active_profiles = _count_rows(
        db,
        CommandProfileVersionRecord,
        CommandProfileVersionRecord.status == "active",
    )
    active_bindings = _count_rows(
        db,
        CollectionProfileBindingRecord,
        CollectionProfileBindingRecord.status == "active",
    )
    profiled_snapshots = _count_rows(
        db,
        CommandSnapshot,
        CommandSnapshot.metadata_json.is_not(None),
    )

    patrol_runs = _count_rows(
        db,
        PatrolRun,
        PatrolRun.mode == "production",
    )
    patrol_workflows = _count_rows(
        db,
        WorkflowRun,
        WorkflowRun.template_name == "patrol-change-event",
    )
    report_steps = _count_rows(
        db,
        WorkflowStepRecord,
        WorkflowStepRecord.skill_name.in_(["patrol-diff", "report-generator"]),
    )
    failed_report_steps = _count_rows(
        db,
        WorkflowStepRecord,
        WorkflowStepRecord.skill_name.in_(["patrol-diff", "report-generator"]),
        WorkflowStepRecord.status == "failed",
    )

    return [
        _agent_module(
            module_id="parser-control",
            design_status="active",
            control=controls.get("parser-control"),
            evidence=_parser_control_evidence(db),
            metrics=[
                _metric("已激活模板", active_templates),
                _metric("妯℃澘鎬绘暟", total_templates),
                _metric("开放任务", open_jobs, "warning" if open_jobs else "ok"),
                _metric("失败任务", failed_jobs, "warning" if failed_jobs else "ok"),
                _metric("缺模板快照", template_missing, "warning" if template_missing else "ok"),
                _metric("解析失败快照", parse_failed, "warning" if parse_failed else "ok"),
            ],
            checks=[
                _module_check(
                    name="模板资产",
                    status="ok" if active_templates else "blocked",
                    value=active_templates,
                    target="鑷冲皯 1 涓?active 妯℃澘",
                    message="存在已激活模板"
                    if active_templates
                    else "没有 active 模板，生产巡检会缺少结构化资产",
                ),
                _module_check(
                    name="浠诲姟闃熷垪",
                    status="ok" if failed_jobs == 0 else "warning",
                    value=failed_jobs,
                    target="失败任务可定位且可重试",
                    message="没有失败任务" if failed_jobs == 0 else f"{failed_jobs} 个模板任务失败",
                ),
                _module_check(
                    name="身份资产",
                    status="ok" if parser_profiles or identity_signatures else "warning",
                    value=parser_profiles + identity_signatures,
                    target="已确认设备身份规则",
                    message="身份资产已登记"
                    if parser_profiles or identity_signatures
                    else "尚无确认的设备身份资产",
                ),
            ],
        ),
        _agent_module(
            module_id="state-normalizer",
            design_status="active",
            control=controls.get("state-normalizer"),
            evidence=_state_normalizer_evidence(db),
            metrics=[
                _metric("状态观测", observations),
                _metric("鐢熶骇瑙傛祴", production_observations),
                _metric("当前状态", current_states),
                _metric(
                    "验证数据污染",
                    validation_current_states,
                    "blocked" if validation_current_states else "ok",
                ),
            ],
            checks=[
                _module_check(
                    name="Observation 鍐欏叆",
                    status="ok" if observations else "warning",
                    value=observations,
                    target="缁撴瀯鍖栧揩鐓у彲鐢熸垚 StateObservation",
                    message="已有状态观测" if observations else "尚未看到 StateObservation 数据",
                ),
                _module_check(
                    name="CurrentState 鏇存柊",
                    status="ok" if current_states else "warning",
                    value=current_states,
                    target="鐢熶骇鐘舵€佸彲鏇存柊 CurrentState",
                    message="已有当前状态" if current_states else "尚未看到 CurrentState 数据",
                ),
                _module_check(
                    name="鐢熶骇闅旂",
                    status="ok" if validation_current_states == 0 else "blocked",
                    value=validation_current_states,
                    target="parser_validation 涓嶆洿鏂?CurrentState",
                    message="验证数据未污染 CurrentState"
                    if validation_current_states == 0
                    else "发现验证数据写入 CurrentState",
                ),
            ],
        ),
        _agent_module(
            module_id="change-detector",
            design_status="active",
            control=controls.get("change-detector"),
            evidence=_change_detector_evidence(db),
            metrics=[
                _metric("变化数", changes),
                _metric("观测证据变化", observation_backed_changes),
                _metric("待事件处理", pending_changes, "warning" if pending_changes else "ok"),
            ],
            checks=[
                _module_check(
                    name="变化生成",
                    status="ok" if changes else "warning",
                    value=changes,
                    target="StateObservation 可生成 NetworkChange",
                    message="已有变化记录" if changes else "尚未看到 NetworkChange 数据",
                ),
                _module_check(
                    name="证据引用",
                    status="ok" if not changes or observation_backed_changes else "warning",
                    value=observation_backed_changes,
                    target="变化可追溯到 observation",
                    message="变化包含 observation 证据"
                    if observation_backed_changes
                    else "变化证据仍需补强",
                ),
                _module_check(
                    name="事件交接",
                    status="ok" if pending_changes == 0 else "warning",
                    value=pending_changes,
                    target="Change 进入 Event Builder 后不长期 pending",
                    message="没有待事件处理变化"
                    if pending_changes == 0
                    else f"{pending_changes} 条变化仍待事件处理",
                ),
            ],
        ),
        _agent_module(
            module_id="event-builder",
            design_status="active",
            control=controls.get("event-builder"),
            evidence=_event_builder_evidence(db),
            metrics=[
                _metric("事件数", events),
                _metric("已激活规则", active_event_rules),
                _metric("无事件变化", no_event_changes),
            ],
            checks=[
                _module_check(
                    name="浜嬩欢瑙勫垯",
                    status="ok" if active_event_rules else "blocked",
                    value=active_event_rules,
                    target="鑷冲皯 1 涓?active 浜嬩欢瑙勫垯",
                    message="已有 active 事件规则"
                    if active_event_rules
                    else "没有 active 事件规则",
                ),
                _module_check(
                    name="浜嬩欢鐢熸垚",
                    status="ok" if events else "warning",
                    value=events,
                    target="NetworkChange 可生成 NetworkEvent",
                    message="已有网络事件" if events else "尚未看到 NetworkEvent 数据",
                ),
                _module_check(
                    name="小幅变化抑制",
                    status="ok" if no_event_changes or not changes else "warning",
                    value=no_event_changes,
                    target="无事件变化应被标记为 no_event",
                    message="已有 no_event 变化" if no_event_changes else "尚未看到 no_event 标记",
                ),
            ],
        ),
        _agent_module(
            module_id="device-management",
            design_status="building",
            control=controls.get("device-management"),
            evidence=_device_management_evidence(db),
            metrics=[
                _metric("设备总数", devices),
                _metric("启用设备", active_devices),
                _metric("已配置凭据", credential_devices),
                _metric("已连接", connected_devices),
                _metric(
                    "杩炴帴寮傚父", connection_failed, "warning" if connection_failed else "ok"
                ),
                _metric("动态分组", dynamic_groups),
            ],
            checks=[
                _module_check(
                    name="资产可见",
                    status="ok" if devices else "warning",
                    value=devices,
                    target="设备列表、筛选、详情稳定可用",
                    message="已有设备资产" if devices else "尚未添加设备资产",
                ),
                _module_check(
                    name="鍑嵁寮曠敤",
                    status="ok"
                    if active_devices == 0 or credential_devices == active_devices
                    else "warning",
                    value=f"{credential_devices}/{active_devices}",
                    target="启用设备都具备 credential_ref",
                    message="启用设备凭据引用完整"
                    if active_devices == 0 or credential_devices == active_devices
                    else "部分启用设备缺少凭据引用",
                ),
                _module_check(
                    name="杩炴帴鍋ュ悍",
                    status="ok" if connection_failed == 0 else "warning",
                    value=connection_failed,
                    target="连接失败可定位且不阻塞资产查询",
                    message="没有连接异常记录"
                    if connection_failed == 0
                    else f"{connection_failed} 台设备连接异常",
                ),
            ],
        ),
        _agent_module(
            module_id="collection-profile",
            design_status="building",
            control=controls.get("collection-profile"),
            evidence=_collection_profile_evidence(db),
            metrics=[
                _metric("宸叉縺娲?Profile", active_profiles),
                _metric("已激活绑定", active_bindings),
                _metric("带策略元数据快照", profiled_snapshots),
            ],
            checks=[
                _module_check(
                    name="Profile 鐗堟湰",
                    status="ok" if active_profiles else "blocked",
                    value=active_profiles,
                    target="鑷冲皯 1 涓?active Command Profile",
                    message="已有 active 采集命令集"
                    if active_profiles
                    else "没有 active 采集命令集",
                ),
                _module_check(
                    name="绑定策略",
                    status="ok" if active_bindings else "warning",
                    value=active_bindings,
                    target="采集命令集可绑定到设备集合",
                    message="已有 active 采集命令集绑定"
                    if active_bindings
                    else "尚未配置采集命令集绑定",
                ),
                _module_check(
                    name="策略追踪",
                    status="ok" if profiled_snapshots else "warning",
                    value=profiled_snapshots,
                    target="快照携带采集策略元数据",
                    message="已有带策略元数据的快照"
                    if profiled_snapshots
                    else "尚未看到带策略元数据的快照",
                ),
            ],
        ),
        _agent_module(
            module_id="report-diff",
            design_status="building",
            control=controls.get("report-diff"),
            evidence=_report_diff_evidence(db),
            metrics=[
                _metric("鐢熶骇宸℃杩愯", patrol_runs),
                _metric("宸℃ Workflow", patrol_workflows),
                _metric("报告/差异步骤", report_steps),
                _metric(
                    "失败报告步骤", failed_report_steps, "warning" if failed_report_steps else "ok"
                ),
            ],
            checks=[
                _module_check(
                    name="宸℃杩愯",
                    status="ok" if patrol_runs or patrol_workflows else "warning",
                    value=patrol_runs + patrol_workflows,
                    target="历史巡检可作为报告输入",
                    message="已有巡检运行证据"
                    if patrol_runs or patrol_workflows
                    else "尚未看到生产巡检运行",
                ),
                _module_check(
                    name="报告解析",
                    status="ok" if report_steps else "warning",
                    value=report_steps,
                    target="报告由 patrol-diff/report-generator 独立生成",
                    message="已有报告/差异步骤记录" if report_steps else "尚未看到独立报告步骤",
                ),
                _module_check(
                    name="报告失败",
                    status="ok" if failed_report_steps == 0 else "warning",
                    value=failed_report_steps,
                    target="报告失败可定位且不影响巡检入库",
                    message="没有失败报告步骤"
                    if failed_report_steps == 0
                    else f"{failed_report_steps} 个报告步骤失败",
                ),
            ],
        ),
    ]


def _set_agent_module_control(
    db: Any,
    module_id: str,
    payload: AgentModuleControlRequest,
    *,
    updated_by: str | None,
) -> dict[str, Any]:
    if module_id not in AGENT_MODULE_IDS:
        raise HTTPException(status_code=404, detail="agent module not found")
    status = payload.status.strip().lower()
    if status not in AGENT_MODULE_CONTROL_STATUSES:
        raise HTTPException(
            status_code=400, detail="status must be enabled, disabled or maintenance"
        )
    reason = (payload.maintenance_reason or "").strip() or None
    if status in {"disabled", "maintenance"} and not reason:
        raise HTTPException(
            status_code=400,
            detail="maintenance_reason is required when disabling or entering maintenance",
        )
    starts_at = payload.maintenance_starts_at
    ends_at = payload.maintenance_ends_at
    if starts_at and ends_at and ends_at <= starts_at:
        raise HTTPException(
            status_code=400, detail="maintenance_ends_at must be after maintenance_starts_at"
        )
    if status == "enabled":
        reason = None
        starts_at = None
        ends_at = None
    row = (
        db.query(AgentModuleControlRecord)
        .filter(AgentModuleControlRecord.module_id == module_id)
        .first()
    )
    now = datetime.now(timezone.utc)
    if row is None:
        row = AgentModuleControlRecord(
            id=str(uuid.uuid4()),
            module_id=module_id,
            created_at=now,
        )
        db.add(row)
    row.status = status
    row.maintenance_reason = reason
    row.maintenance_starts_at = starts_at
    row.maintenance_ends_at = ends_at
    row.updated_by = updated_by
    row.updated_at = now
    db.commit()
    db.refresh(row)
    return _module_control_dict(row, module_id)


def _required_agent_modules_for_patrol(payload: DevicePatrolLaunchRequest) -> list[str]:
    modules = ["device-management", "collection-profile", "parser-control"]
    if payload.launch_mode == "workflow" and payload.workflow_name == "patrol-change-event":
        modules.extend(["state-normalizer", "change-detector", "event-builder"])
    if "report" in (payload.workflow_name or "").lower():
        modules.append("report-diff")
    return list(dict.fromkeys(modules))


def _agent_module_control_violations(module_ids: list[str]) -> list[dict[str, Any]]:
    return agent_module_control_violations(module_ids)


def _find_nested_value(payload: Any, key: str) -> Any:
    if isinstance(payload, dict):
        if key in payload:
            return payload[key]
        for value in payload.values():
            found = _find_nested_value(value, key)
            if found is not None:
                return found
    elif isinstance(payload, list):
        for value in payload:
            found = _find_nested_value(value, key)
            if found is not None:
                return found
    return None


def _execution_plan_record_dict(row: CollectionExecutionPlanRecord) -> dict[str, Any]:
    return {
        "execution_plan_id": row.id,
        "device_id": row.device_id,
        "action": row.action,
        "mode": row.mode,
        "profile_name": row.profile_name,
        "profile_version_id": row.profile_version_id,
        "binding_id": row.binding_id,
        "policy_source": row.policy_source,
        "credential_ref": row.credential_ref,
        "collector_options": row.collector_options_json or {},
        "selector": row.selector_json or {},
        "plan": dict(row.plan_json or {}),
        "command_count": row.command_count,
        "plan_sha256": row.plan_sha256,
        "status": row.status,
        "launch_source": row.launch_source,
        "patrol_batch_id": row.patrol_batch_id,
        "workflow_run_id": row.workflow_run_id,
        "celery_task_id": row.celery_task_id,
        "failure_code": row.failure_code,
        "error": row.error,
        "created_at": _iso(row.created_at),
        "launched_at": _iso(row.launched_at),
        "updated_at": _iso(row.updated_at),
    }


def _list_execution_plans_for_device(device_id: str, *, limit: int = 20) -> list[dict[str, Any]]:
    limit = max(1, min(int(limit or 20), 100))
    with get_db_session() as db:
        rows = (
            db.query(CollectionExecutionPlanRecord)
            .filter(CollectionExecutionPlanRecord.device_id == device_id)
            .order_by(CollectionExecutionPlanRecord.created_at.desc())
            .limit(limit)
            .all()
        )
        return [_execution_plan_record_dict(row) for row in rows]


def _device_evidence_timeline(device: dict[str, Any], *, limit: int = 10) -> dict[str, Any]:
    device_id = str(device.get("device_id") or "")
    limit = max(1, min(int(limit or 10), 50))
    with get_db_session() as db:
        workflow_runs = (
            db.query(WorkflowRun)
            .filter(
                WorkflowRun.template_name == "patrol-change-event",
                WorkflowRun.context["device_id"].as_string() == device_id,
            )
            .order_by(WorkflowRun.created_at.desc())
            .limit(limit)
            .all()
        )
        run_entries: list[tuple[WorkflowRun | None, list[WorkflowStepRecord], str, datetime]] = []
        seen_snapshot_runs: set[str] = set()
        for workflow_run in workflow_runs:
            workflow_steps = (
                db.query(WorkflowStepRecord)
                .filter(WorkflowStepRecord.run_id == workflow_run.id)
                .order_by(WorkflowStepRecord.step_index.asc())
                .all()
            )
            workflow_snapshot_run_id = next(
                (
                    str(value)
                    for step in workflow_steps
                    if (value := _find_nested_value(step.result or {}, "snapshot_run_id"))
                ),
                "",
            )
            if workflow_snapshot_run_id:
                seen_snapshot_runs.add(workflow_snapshot_run_id)
                run_entries.append(
                    (
                        workflow_run,
                        workflow_steps,
                        workflow_snapshot_run_id,
                        workflow_run.created_at,
                    )
                )
        patrol_run_ids = [
            row[0]
            for row in db.query(
                CommandSnapshot.run_id,
                func.max(CommandSnapshot.observed_at).label("latest_observed_at"),
            )
            .filter(
                or_(
                    CommandSnapshot.inventory_device_id == device_id,
                    CommandSnapshot.device_id == device_id,
                )
            )
            .group_by(CommandSnapshot.run_id)
            .order_by(func.max(CommandSnapshot.observed_at).desc())
            .limit(limit)
            .all()
        ]
        patrol_run_rows = (
            db.query(PatrolRun).filter(PatrolRun.id.in_(patrol_run_ids)).all()
            if patrol_run_ids
            else []
        )
        patrol_runs_by_id = {row.id: row for row in patrol_run_rows}
        patrol_runs = [
            patrol_runs_by_id[run_id] for run_id in patrol_run_ids if run_id in patrol_runs_by_id
        ]
        for patrol_run in patrol_runs:
            if patrol_run.id in seen_snapshot_runs:
                continue
            run_entries.append((None, [], patrol_run.id, patrol_run.started_at))
        run_entries.sort(key=lambda entry: entry[3], reverse=True)
        items: list[dict[str, Any]] = []
        for run, steps, snapshot_run_id, _created_at in run_entries[:limit]:
            patrol_run = db.get(PatrolRun, snapshot_run_id)
            run_context = dict((run.context if run is not None else {}) or {})
            execution_plan_id = str(
                run_context.get("execution_plan_id")
                or ((patrol_run.metadata_json or {}).get("execution_plan_id") if patrol_run else "")
                or ""
            )
            execution_plan = None
            if execution_plan_id:
                plan_row = db.get(CollectionExecutionPlanRecord, execution_plan_id)
                if plan_row is not None:
                    execution_plan = _execution_plan_record_dict(plan_row)
            if execution_plan is None and run is not None:
                plan_row = (
                    db.query(CollectionExecutionPlanRecord)
                    .filter(CollectionExecutionPlanRecord.workflow_run_id == run.id)
                    .order_by(CollectionExecutionPlanRecord.created_at.desc())
                    .first()
                )
                if plan_row is not None:
                    execution_plan = _execution_plan_record_dict(plan_row)
                    execution_plan_id = plan_row.id
            snapshots: list[CommandSnapshot] = []
            changes: list[NetworkChangeRecord] = []
            events: list[NetworkEventRecord] = []
            parse_counts: dict[str, int] = {}
            evidence_by_snapshot: dict[str, RawEvidenceRecord] = {}
            observation_count = 0
            change_count = 0
            event_count = 0
            if snapshot_run_id:
                snapshots = (
                    db.query(CommandSnapshot)
                    .filter(
                        CommandSnapshot.run_id == snapshot_run_id,
                        or_(
                            CommandSnapshot.inventory_device_id == device_id,
                            CommandSnapshot.device_id == device_id,
                        ),
                    )
                    .order_by(
                        CommandSnapshot.observed_at.asc(), CommandSnapshot.canonical_command.asc()
                    )
                    .all()
                )
                snapshot_ids = [row.id for row in snapshots]
                for row in snapshots:
                    status = row.parse_status or "unknown"
                    parse_counts[status] = parse_counts.get(status, 0) + 1
                if snapshot_ids:
                    evidence_by_snapshot = {
                        row.source_snapshot_id: row
                        for row in db.query(RawEvidenceRecord)
                        .filter(RawEvidenceRecord.source_snapshot_id.in_(snapshot_ids))
                        .all()
                        if row.source_snapshot_id
                    }
                    observation_count = (
                        db.query(StateObservationRecord.id)
                        .filter(StateObservationRecord.snapshot_id.in_(snapshot_ids))
                        .count()
                    )
                change_filter = (
                    NetworkChangeRecord.run_id == snapshot_run_id,
                    NetworkChangeRecord.device_id == device_id,
                )
                change_count = db.query(NetworkChangeRecord.id).filter(*change_filter).count()
                occurrence_event_ids = db.query(NetworkEventOccurrenceRecord.event_id).filter(
                    NetworkEventOccurrenceRecord.run_id == snapshot_run_id
                )
                event_query = db.query(NetworkEventRecord).filter(
                    or_(
                        NetworkEventRecord.run_id == snapshot_run_id,
                        NetworkEventRecord.id.in_(occurrence_event_ids),
                    ),
                    NetworkEventRecord.device_id == device_id,
                )
                event_count = event_query.count()
                changes = (
                    db.query(NetworkChangeRecord)
                    .filter(*change_filter)
                    .order_by(NetworkChangeRecord.detected_at.desc())
                    .limit(20)
                    .all()
                )
                events = event_query.order_by(NetworkEventRecord.occurred_at.desc()).limit(20).all()
            items.append(
                {
                    "workflow_run_id": run.id if run is not None else None,
                    "workflow_status": (
                        run.status if run is not None else patrol_run.status if patrol_run else None
                    ),
                    "workflow_created_at": _iso(
                        run.created_at
                        if run is not None
                        else patrol_run.started_at
                        if patrol_run
                        else None
                    ),
                    "workflow_completed_at": _iso(
                        run.completed_at
                        if run is not None
                        else patrol_run.completed_at
                        if patrol_run
                        else None
                    ),
                    "execution_plan_id": execution_plan_id or None,
                    "execution_plan": execution_plan,
                    "patrol_batch_id": run_context.get("patrol_batch_id"),
                    "snapshot_run_id": snapshot_run_id,
                    "snapshot_count": len(snapshots),
                    "observation_count": observation_count,
                    "change_count": change_count,
                    "event_count": event_count,
                    "parse_counts": parse_counts,
                    "steps": [
                        {
                            "step_name": step.step_name,
                            "skill_name": step.skill_name,
                            "status": step.status,
                            "started_at": _iso(step.started_at),
                            "completed_at": _iso(step.completed_at),
                            "error": step.error_message,
                        }
                        for step in steps
                    ],
                    "snapshots": [
                        {
                            "snapshot_id": row.id,
                            "evidence_id": (
                                evidence_by_snapshot[row.id].id
                                if row.id in evidence_by_snapshot
                                else evidence_id_for_command_snapshot(row.id)
                            ),
                            "source_type": (
                                evidence_by_snapshot[row.id].source_type
                                if row.id in evidence_by_snapshot
                                else "cli"
                            ),
                            "evidence_type": (
                                evidence_by_snapshot[row.id].evidence_type
                                if row.id in evidence_by_snapshot
                                else "cli_command_output"
                            ),
                            "content_ref": (
                                evidence_by_snapshot[row.id].content_ref
                                if row.id in evidence_by_snapshot
                                else f"command_snapshot:{row.id}"
                            ),
                            "command": row.canonical_command,
                            "parse_status": row.parse_status,
                            "parser_template_id": row.parser_template_id,
                            "raw_text_hash": row.raw_text_hash,
                            "observed_at": _iso(row.observed_at),
                        }
                        for row in snapshots[:20]
                    ],
                    "changes": [
                        {
                            "change_id": row.id,
                            "entity_type": row.entity_type,
                            "entity_key": row.entity_key,
                            "field": row.field,
                            "change_type": row.change_type,
                            "event_status": row.event_status,
                            "detected_at": _iso(row.detected_at),
                        }
                        for row in changes[:10]
                    ],
                    "events": [
                        {
                            "event_id": row.id,
                            "event_type": row.event_type,
                            "severity": row.severity,
                            "entity_type": row.entity_type,
                            "entity_key": row.entity_key,
                            "status": row.status,
                            "occurred_at": _iso(row.occurred_at),
                            "last_seen_at": _iso(row.last_seen_at),
                            "occurrence_count": row.occurrence_count,
                        }
                        for row in events[:10]
                    ],
                }
            )
    return {
        "device": _redact_device(device),
        "items": items,
        "limit": limit,
    }


def _patrol_readiness(device: dict[str, Any], mode: str = PRODUCTION_MODE) -> dict[str, Any]:
    try:
        normalized_mode = normalize_patrol_mode(mode)
        plan = CollectionPolicyResolver().build_plan(
            device=device,
            action="patrol",
            mode=normalized_mode,
        )
        commands = plan.command_texts()
        coverage = (
            _production_parser_coverage(device, commands)
            if normalized_mode == PRODUCTION_MODE
            else []
        )
        connection_issues = _connection_preflight(device)
        plan_violations = _execution_plan_violations(plan, normalized_mode)
        runtime_asset_health = _runtime_rule_asset_health(
            mode=normalized_mode,
            coverage=coverage,
        )
        summary = _coverage_summary(
            mode=normalized_mode,
            commands=commands,
            coverage=coverage,
            connection_issues=connection_issues,
            plan_violations=plan_violations,
            runtime_asset_health=runtime_asset_health,
        )
        return {
            **summary,
            "policy_source": plan.policy_source,
            "profile_name": plan.profile_name,
            "profile_version_id": plan.profile_version_id,
        }
    except Exception as exc:
        return {
            "status": "error",
            "mode": mode,
            "command_count": 0,
            "eligible_count": 0,
            "ready_count": 0,
            "blocked_count": 0,
            "policy_source": None,
            "profile_name": None,
            "profile_version_id": None,
            "violations": [],
            "connection_issues": [],
            "error": str(exc),
        }


def _bulk_update_preview(
    payload: DeviceBulkRequest, repository: DeviceRepository
) -> dict[str, Any]:
    devices = _resolve_bulk_devices(payload, repository)
    update = payload.update
    if update.status is not None:
        try:
            update.status = normalize_device_status(update.status)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if update.status == "deleted":
            raise HTTPException(status_code=400, detail="use bulk delete to delete devices")
    if update.group_mode not in {"replace", "append"}:
        raise HTTPException(status_code=400, detail="group_mode must be replace or append")
    changed_fields = [
        name
        for name in (
            "command_profile",
            "credential_ref",
            "status",
            "platform",
            "device_type",
            "site",
            "role",
            "groups",
        )
        if getattr(update, name) is not None
    ]
    if not changed_fields:
        raise HTTPException(status_code=400, detail="at least one update field is required")
    rows: list[dict[str, Any]] = []
    for device in devices:
        next_groups = None
        if update.groups is not None:
            if update.group_mode == "append":
                next_groups = list(dict.fromkeys([*(device.get("groups") or []), *update.groups]))
            else:
                next_groups = update.groups
        rows.append(
            {
                "device_id": device["device_id"],
                "device_name": device.get("device_name"),
                "ip": device.get("ip") or device.get("management_ip"),
                "current": _redact_device(device),
                "update": {
                    "command_profile": update.command_profile,
                    "credential_ref": update.credential_ref,
                    "status": update.status,
                    "platform": update.platform,
                    "device_type": update.device_type,
                    "site": update.site,
                    "role": update.role,
                    "groups": next_groups,
                    "group_mode": update.group_mode if update.groups is not None else None,
                },
            }
        )
    return {
        "success": True,
        "target_count": len(rows),
        "changed_fields": changed_fields,
        "rows": rows,
    }


def _patrol_launch_preview(
    payload: DevicePatrolLaunchRequest, repository: DeviceRepository
) -> dict[str, Any]:
    devices = _resolve_bulk_devices(
        DeviceBulkRequest(
            device_ids=payload.device_ids,
            dynamic_group_id=payload.dynamic_group_id,
            filters=payload.filters,
            update=DeviceBulkUpdate(status="preview"),
        ),
        repository,
        active_only=True,
    )
    module_violations = _agent_module_control_violations(
        _required_agent_modules_for_patrol(payload)
    )
    if module_violations:
        errors = [
            {
                "device_id": device.get("device_id"),
                "device_name": device.get("device_name"),
                "error": "agent module governance preflight failed",
                "failure_code": "agent_module_not_ready",
                "module_violations": module_violations,
                "plan_violations": [],
                "coverage_violations": [],
                "connection_issues": [],
            }
            for device in devices
        ]
        if not errors:
            errors.append(
                {
                    "error": "agent module governance preflight failed",
                    "failure_code": "agent_module_not_ready",
                    "module_violations": module_violations,
                    "plan_violations": [],
                    "coverage_violations": [],
                    "connection_issues": [],
                }
            )
        return {
            "success": False,
            "target_count": len(devices),
            "ready_count": 0,
            "error_count": len(errors),
            "mode": normalize_patrol_mode(payload.mode),
            "save_baseline": payload.save_baseline,
            "launch_mode": payload.launch_mode,
            "workflow_name": payload.workflow_name,
            "module_violations": module_violations,
            "rows": [],
            "errors": errors,
        }
    resolver = CollectionPolicyResolver()
    rows: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for device in devices:
        try:
            plan = resolver.build_plan(
                device=device,
                action="patrol",
                mode=normalize_patrol_mode(payload.mode),
            )
            commands = plan.command_texts()
            plan_violations = _execution_plan_violations(plan, payload.mode)
            parser_coverage = (
                _production_parser_coverage(device, commands)
                if normalize_patrol_mode(payload.mode) == PRODUCTION_MODE
                else []
            )
            coverage_violations = [
                item
                for item in parser_coverage
                if item.get("policy_status") == "eligible"
                and item.get("coverage_status") != "ready"
            ]
            runtime_asset_health = _runtime_rule_asset_health(
                mode=payload.mode,
                coverage=parser_coverage,
            )
            connection_issues = _connection_preflight(device)
            runtime_asset_blocked = runtime_asset_health.get("status") in {"blocked", "error"}
            if plan_violations or coverage_violations or connection_issues or runtime_asset_blocked:
                failure_code = (
                    "collector_preflight_failed"
                    if connection_issues
                    else "command_profile_not_ready"
                    if plan_violations
                    else "parser_coverage_not_ready"
                    if coverage_violations
                    else "runtime_rule_assets_not_ready"
                )
                if connection_issues and device.get("device_id"):
                    issue_code, issue_message = _first_issue(connection_issues)
                    repository.record_connection_health(
                        str(device["device_id"]),
                        status="preflight_failed",
                        failure_code=issue_code,
                        error=issue_message,
                        source="patrol-preflight",
                    )
                errors.append(
                    {
                        "device_id": device.get("device_id"),
                        "device_name": device.get("device_name"),
                        "error": "device patrol preflight failed",
                        "failure_code": failure_code,
                        "plan_violations": plan_violations,
                        "coverage_violations": coverage_violations,
                        "connection_issues": connection_issues,
                        "runtime_asset_health": runtime_asset_health,
                    }
                )
                continue
            rows.append(
                {
                    "device_id": device["device_id"],
                    "device_name": device.get("device_name"),
                    "ip": device.get("ip") or device.get("management_ip"),
                    "device": _redact_device(device),
                    "plan": plan.to_metadata(),
                    "commands": commands,
                    "command_count": len(plan.commands),
                    "plan_violations": [],
                    "coverage_violations": [],
                    "connection_issues": [],
                    "runtime_asset_health": runtime_asset_health,
                }
            )
        except Exception as exc:
            errors.append(
                {
                    "device_id": device.get("device_id"),
                    "device_name": device.get("device_name"),
                    "error": str(exc),
                }
            )
    return {
        "success": not errors,
        "target_count": len(devices),
        "ready_count": len(rows),
        "error_count": len(errors),
        "mode": normalize_patrol_mode(payload.mode),
        "save_baseline": payload.save_baseline,
        "launch_mode": payload.launch_mode,
        "workflow_name": payload.workflow_name,
        "module_violations": [],
        "rows": rows,
        "errors": errors,
    }


@router.get("")
def list_devices(
    device_id: str | None = None,
    device_name: str | None = None,
    ip: str | None = None,
    group: str | None = None,
    vendor: str | None = None,
    model: str | None = None,
    family: str | None = None,
    platform: str | None = None,
    device_type: str | None = None,
    site: str | None = None,
    role: str | None = None,
    status: str | None = None,
    connection_status: str | None = None,
    include_patrol_readiness: bool = False,
    readiness_mode: str = PRODUCTION_MODE,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    repository = DeviceRepository()
    normalized_status = status
    if normalized_status in {"", "all"}:
        normalized_status = None
    elif normalized_status is not None:
        normalized_status = normalize_device_status(normalized_status)
    devices = repository.get_devices_by_filter(
        device_id=device_id,
        device_name=device_name,
        ip=ip,
        group=group,
        vendor=vendor,
        model=model,
        family=family,
        platform=platform,
        device_type=device_type,
        site=site,
        role=role,
        status=normalized_status,
        connection_status=connection_status,
    )
    if normalized_status is None:
        devices = [device for device in devices if device.get("status") != "deleted"]
    total = len(devices)
    page = devices[offset : offset + limit]
    items: list[dict[str, Any]] = []
    for item in page:
        redacted = _redact_device(item)
        if include_patrol_readiness:
            redacted["patrol_readiness"] = _patrol_readiness(item, readiness_mode)
        items.append(redacted)
    return {
        "items": items,
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.post("")
def upsert_device(payload: DeviceUpsertRequest, request: Request):
    repository = DeviceRepository()
    values = payload.model_dump()
    try:
        values["status"] = normalize_device_status(values.get("status"))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if values["status"] == "deleted":
        raise HTTPException(status_code=400, detail="new devices cannot be created as deleted")
    device_id = repository.upsert_device(**values)
    matches = repository.get_devices_by_filter(device_id=device_id, status=None)
    _audit_device_action(
        request,
        "device_upsert",
        "device",
        device_id,
        {
            "device_name": payload.device_name,
            "management_ip": payload.management_ip,
            "vendor": payload.vendor,
            "model": payload.model,
            "groups": payload.groups or [],
        },
    )
    return {
        "success": True,
        "device_id": device_id,
        "device": _redact_device(matches[0]) if matches else None,
    }


@router.post("/import/preview")
def preview_device_import(payload: DeviceImportRequest):
    return _preview_import(payload, DeviceRepository())


@router.get("/import/template")
def get_device_import_template():
    return _device_import_template_payload()


@router.post("/import/apply")
def apply_device_import(payload: DeviceImportRequest, request: Request):
    repository = DeviceRepository()
    preview = _preview_import(payload, repository)
    applied: list[dict[str, Any]] = []
    for row in preview["rows"]:
        if not row["valid"]:
            continue
        device_id = repository.upsert_device(**row["device"])
        matches = repository.get_devices_by_filter(device_id=device_id, status=None)
        applied.append(
            {
                "row_number": row["row_number"],
                "action": row["action"],
                "device_id": device_id,
                "device": _redact_device(matches[0]) if matches else None,
            }
        )
    result = {
        "success": True,
        "dry_run": False,
        "total": preview["total"],
        "applied_count": len(applied),
        "invalid_count": preview["invalid_count"],
        "create_count": preview["create_count"],
        "update_count": preview["update_count"],
        "rows": preview["rows"],
        "applied": applied,
        "skipped": [row for row in preview["rows"] if not row["valid"]],
    }
    _audit_device_action(
        request,
        "device_import_apply",
        "device_import",
        payload.filename,
        {
            "format": payload.format,
            "filename": payload.filename,
            "total": result["total"],
            "applied_count": result["applied_count"],
            "create_count": result["create_count"],
            "update_count": result["update_count"],
            "invalid_count": result["invalid_count"],
        },
    )
    return result


@router.post("/bulk/preview")
def preview_device_bulk_update(payload: DeviceBulkRequest):
    return _bulk_update_preview(payload, DeviceRepository())


@router.post("/bulk/apply")
def apply_device_bulk_update(payload: DeviceBulkRequest, request: Request):
    repository = DeviceRepository()
    preview = _bulk_update_preview(payload, repository)
    update = payload.update
    applied: list[dict[str, Any]] = []
    for row in preview["rows"]:
        update_values: dict[str, Any] = {
            "command_profile": update.command_profile,
            "credential_ref": update.credential_ref,
            "status": update.status,
            "platform": update.platform,
            "device_type": update.device_type,
            "site": update.site,
            "role": update.role,
        }
        if update.groups is not None:
            update_values["groups"] = row["update"]["groups"]
        update_values = {key: value for key, value in update_values.items() if value is not None}
        device = repository.update_device(row["device_id"], **update_values)
        if device:
            applied.append(_redact_device(device))
    result = {
        "success": True,
        "target_count": preview["target_count"],
        "applied_count": len(applied),
        "changed_fields": preview["changed_fields"],
        "devices": applied,
    }
    _audit_device_action(
        request,
        "device_bulk_update",
        "device",
        None,
        {
            "target_count": result["target_count"],
            "applied_count": result["applied_count"],
            "changed_fields": result["changed_fields"],
            "device_ids": [item.get("device_id") for item in applied[:50]],
            "dynamic_group_id": payload.dynamic_group_id,
        },
    )
    return result


@router.post("/bulk/delete/preview")
def preview_device_bulk_delete(payload: DeviceBulkSelectionRequest):
    devices = _resolve_bulk_devices(payload, DeviceRepository())
    return {
        "success": True,
        "target_count": len(devices),
        "rows": [
            {
                "device_id": device["device_id"],
                "device_name": device.get("device_name"),
                "ip": device.get("ip") or device.get("management_ip"),
                "current": _redact_device(device),
            }
            for device in devices
        ],
    }


@router.post("/bulk/delete")
def apply_device_bulk_delete(payload: DeviceBulkSelectionRequest, request: Request):
    repository = DeviceRepository()
    devices = _resolve_bulk_devices(payload, repository)
    deleted: list[dict[str, Any]] = []
    for device in devices:
        snapshot = repository.delete_device(device["device_id"])
        if snapshot:
            deleted.append(_redact_device(snapshot))
    _audit_device_action(
        request,
        "device_bulk_delete",
        "device",
        None,
        {
            "target_count": len(devices),
            "deleted_count": len(deleted),
            "device_ids": [item.get("device_id") for item in deleted[:50]],
            "dynamic_group_id": payload.dynamic_group_id,
        },
    )
    return {
        "success": True,
        "target_count": len(devices),
        "deleted_count": len(deleted),
        "devices": deleted,
    }


@router.get("/credentials")
def list_device_credentials():
    return {"items": CredentialResolver().list_references()}


@router.post("/credentials/inspect")
def inspect_device_credential(payload: CredentialInspectRequest):
    return CredentialResolver().inspect_reference(payload.credential_ref)


@router.get("/groups")
def list_device_groups():
    return {"items": DeviceRepository().list_groups()}


@router.get("/dynamic-groups")
def list_device_dynamic_groups(status: str | None = None):
    return {"items": DeviceRepository().list_dynamic_groups(status=status)}


@router.post("/dynamic-groups")
def upsert_device_dynamic_group(payload: DeviceDynamicGroupRequest, request: Request):
    try:
        group = DeviceRepository().upsert_dynamic_group(**payload.model_dump())
        _audit_device_action(
            request,
            "device_dynamic_group_upsert",
            "device_dynamic_group",
            group.get("id"),
            {"name": group.get("name"), "status": group.get("status")},
        )
        return {"success": True, "group": group}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.patch("/dynamic-groups/{group_id}")
def update_device_dynamic_group(
    group_id: str, payload: DeviceDynamicGroupRequest, request: Request
):
    try:
        group = DeviceRepository().upsert_dynamic_group(group_id=group_id, **payload.model_dump())
        _audit_device_action(
            request,
            "device_dynamic_group_update",
            "device_dynamic_group",
            group_id,
            {"name": group.get("name"), "status": group.get("status")},
        )
        return {"success": True, "group": group}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/dynamic-groups/{group_id}/status")
def update_device_dynamic_group_status(
    group_id: str, payload: DeviceStatusRequest, request: Request
):
    try:
        group = DeviceRepository().set_dynamic_group_status(group_id, payload.status)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not group:
        raise HTTPException(status_code=404, detail="dynamic group not found")
    _audit_device_action(
        request,
        "device_dynamic_group_status",
        "device_dynamic_group",
        group_id,
        {"status": payload.status},
    )
    return {"success": True, "group": group}


@router.get("/dynamic-groups/{group_id}/preview")
def preview_device_dynamic_group(group_id: str, limit: int = Query(100, ge=1, le=500)):
    group, devices = DeviceRepository().resolve_dynamic_group_devices(group_id)
    if group is None:
        raise HTTPException(status_code=404, detail="dynamic group not found")
    return {
        "success": True,
        "group": group,
        "total": len(devices),
        "limit": limit,
        "items": [_redact_device(device) for device in devices[:limit]],
    }


@router.get("/agent-modules/health")
def get_agent_module_health():
    with get_db_session() as db:
        items = _build_agent_module_health(db)
    return {
        "items": items,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


@router.patch("/agent-modules/{module_id}/control")
def update_agent_module_control(
    module_id: str, payload: AgentModuleControlRequest, request: Request
):
    user = getattr(getattr(request, "state", None), "current_user", None)
    if user is not None and not isinstance(user, CurrentUser):
        user = None
    with get_db_session() as db:
        control = _set_agent_module_control(
            db,
            module_id,
            payload,
            updated_by=user.username if user else None,
        )
    _audit_device_action(
        request,
        "agent_module_control_update",
        "agent_module",
        module_id,
        {
            "status": control["status"],
            "maintenance_reason": control["maintenance_reason"],
            "maintenance_starts_at": control.get("maintenance_starts_at"),
            "maintenance_ends_at": control.get("maintenance_ends_at"),
        },
    )
    return {"success": True, "control": control}


@router.post("/patrol/preview")
def preview_device_patrol_launch(payload: DevicePatrolLaunchRequest):
    return _patrol_launch_preview(payload, DeviceRepository())


@router.post("/patrol/launch")
def launch_device_patrol(payload: DevicePatrolLaunchRequest, request: Request):
    preview = _patrol_launch_preview(payload, DeviceRepository())
    if preview["error_count"] and not preview["rows"]:
        raise HTTPException(
            status_code=400,
            detail={
                "message": "no devices can build patrol execution plans",
                "errors": preview["errors"],
            },
        )
    if payload.launch_mode not in {"workflow", "direct"}:
        raise HTTPException(status_code=400, detail="launch_mode must be workflow or direct")
    patrol_batch_id = f"patrol-batch-{uuid.uuid4()}"
    plan_repository = ExecutionPlanRepository()
    if payload.launch_mode == "workflow":
        from src.core.workflows.engine import WorkflowEngine

        launched: list[dict[str, Any]] = []
        for row in preview["rows"]:
            plan_record = plan_repository.create_plan(
                row["plan"],
                launch_source="workflow",
                patrol_batch_id=patrol_batch_id,
            )
            context = {
                "execution_plan": row["plan"],
                "execution_plan_id": plan_record["execution_plan_id"],
                "filter_params": {},
                "ticket_id": payload.ticket_id,
                "save_baseline": payload.save_baseline,
                "mode": payload.mode,
                "update_current_state": normalize_patrol_mode(payload.mode) == PRODUCTION_MODE,
                "publish_events": False,
                "patrol_batch_id": patrol_batch_id,
                "device_id": row["device_id"],
                "device_name": row["device_name"],
            }
            try:
                run_id = WorkflowEngine.start(
                    payload.workflow_name,
                    context,
                    source="device-management",
                )
            except ValueError as exc:
                plan_repository.mark_failed(
                    plan_record["execution_plan_id"],
                    failure_code="workflow_launch_failed",
                    error=str(exc),
                )
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            plan_repository.mark_launched(
                plan_record["execution_plan_id"],
                launch_source="workflow",
                patrol_batch_id=patrol_batch_id,
                workflow_run_id=run_id,
            )
            launched.append(
                {
                    "device_id": row["device_id"],
                    "device_name": row["device_name"],
                    "ip": row["ip"],
                    "execution_plan_id": plan_record["execution_plan_id"],
                    "workflow_run_id": run_id,
                    "workflow_name": payload.workflow_name,
                    "patrol_batch_id": patrol_batch_id,
                    "command_count": row["command_count"],
                }
            )
        result = {
            "success": True,
            "target_count": preview["target_count"],
            "launched_count": len(launched),
            "mode": payload.mode,
            "save_baseline": payload.save_baseline,
            "ticket_id": payload.ticket_id,
            "patrol_batch_id": patrol_batch_id,
            "launch_mode": payload.launch_mode,
            "workflow_name": payload.workflow_name,
            "runs": launched,
            "tasks": [],
            "skipped_count": preview["error_count"],
            "skipped_errors": preview["errors"],
        }
        _audit_device_action(
            request,
            "device_patrol_launch",
            "patrol_batch",
            patrol_batch_id,
            {
                "launch_mode": payload.launch_mode,
                "workflow_name": payload.workflow_name,
                "mode": payload.mode,
                "save_baseline": payload.save_baseline,
                "target_count": result["target_count"],
                "launched_count": result["launched_count"],
                "skipped_count": result["skipped_count"],
                "device_ids": [item.get("device_id") for item in launched[:50]],
            },
        )
        return result

    from src.core.celery_tasks.tasks import execute_device_patrol_task

    launched: list[dict[str, Any]] = []
    for row in preview["rows"]:
        plan_record = plan_repository.create_plan(
            row["plan"],
            launch_source="direct",
            patrol_batch_id=patrol_batch_id,
        )
        try:
            task = execute_device_patrol_task.apply_async(
                kwargs={
                    "execution_plan": row["plan"],
                    "execution_plan_id": plan_record["execution_plan_id"],
                    "ticket_id": payload.ticket_id,
                    "save_baseline": payload.save_baseline,
                    "mode": payload.mode,
                    "patrol_batch_id": patrol_batch_id,
                },
                queue="netops.device",
            )
        except Exception as exc:
            plan_repository.mark_failed(
                plan_record["execution_plan_id"],
                failure_code="celery_launch_failed",
                error=str(exc),
            )
            raise
        plan_repository.mark_launched(
            plan_record["execution_plan_id"],
            launch_source="direct",
            patrol_batch_id=patrol_batch_id,
            celery_task_id=task.id,
        )
        launched.append(
            {
                "device_id": row["device_id"],
                "device_name": row["device_name"],
                "ip": row["ip"],
                "execution_plan_id": plan_record["execution_plan_id"],
                "celery_task_id": task.id,
                "patrol_batch_id": patrol_batch_id,
                "command_count": row["command_count"],
            }
        )
    result = {
        "success": True,
        "target_count": preview["target_count"],
        "launched_count": len(launched),
        "mode": payload.mode,
        "save_baseline": payload.save_baseline,
        "ticket_id": payload.ticket_id,
        "patrol_batch_id": patrol_batch_id,
        "launch_mode": payload.launch_mode,
        "workflow_name": payload.workflow_name,
        "runs": [],
        "tasks": launched,
        "skipped_count": preview["error_count"],
        "skipped_errors": preview["errors"],
    }
    _audit_device_action(
        request,
        "device_patrol_launch",
        "patrol_batch",
        patrol_batch_id,
        {
            "launch_mode": payload.launch_mode,
            "workflow_name": payload.workflow_name,
            "mode": payload.mode,
            "save_baseline": payload.save_baseline,
            "target_count": result["target_count"],
            "launched_count": result["launched_count"],
            "skipped_count": result["skipped_count"],
            "device_ids": [item.get("device_id") for item in launched[:50]],
        },
    )
    return result


@router.get("/{device_id}")
def get_device(device_id: str):
    matches = DeviceRepository().get_devices_by_filter(device_id=device_id, status=None)
    if not matches:
        raise HTTPException(status_code=404, detail="device not found")
    return _redact_device(matches[0])


@router.delete("/{device_id}")
def delete_device(device_id: str, request: Request):
    device = DeviceRepository().delete_device(device_id)
    if not device:
        raise HTTPException(status_code=404, detail="device not found")
    _audit_device_action(
        request,
        "device_delete",
        "device",
        device_id,
        {"deleted_device": _redact_device(device)},
    )
    return {"success": True, "device": _redact_device(device)}


@router.patch("/{device_id}")
def update_device(device_id: str, payload: DeviceUpdateRequest, request: Request):
    values = payload.model_dump(exclude_unset=True)
    if values.get("status") is not None:
        try:
            values["status"] = normalize_device_status(values.get("status"))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if values["status"] == "deleted":
            raise HTTPException(
                status_code=400, detail="use DELETE /devices/{device_id} to delete a device"
            )
    device = DeviceRepository().update_device(device_id, **values)
    if not device:
        raise HTTPException(status_code=404, detail="device not found")
    _audit_device_action(
        request,
        "device_update",
        "device",
        device_id,
        {"changed_fields": sorted(values.keys())},
    )
    return {"success": True, "device": _redact_device(device)}


@router.post("/{device_id}/status")
def update_device_status(device_id: str, payload: DeviceStatusRequest, request: Request):
    try:
        device = DeviceRepository().set_device_status(device_id, payload.status)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not device:
        raise HTTPException(status_code=404, detail="device not found")
    _audit_device_action(
        request,
        "device_status_update",
        "device",
        device_id,
        {"status": payload.status},
    )
    return {"success": True, "device": _redact_device(device)}


@router.post("/{device_id}/groups")
def update_device_groups(device_id: str, payload: DeviceGroupsRequest, request: Request):
    device = DeviceRepository().set_device_groups(device_id, payload.groups)
    if not device:
        raise HTTPException(status_code=404, detail="device not found")
    _audit_device_action(
        request,
        "device_groups_update",
        "device",
        device_id,
        {"groups": payload.groups},
    )
    return {"success": True, "device": _redact_device(device)}


@router.post("/{device_id}/effective-plan")
def preview_device_plan(device_id: str, payload: DevicePlanPreviewRequest):
    matches = DeviceRepository().get_devices_by_filter(device_id=device_id, status=None)
    if not matches:
        raise HTTPException(status_code=404, detail="device not found")
    device = matches[0]
    normalized_mode = normalize_patrol_mode(payload.mode)
    plan = CollectionPolicyResolver().build_plan(
        device=device,
        action=payload.action,
        mode=normalized_mode,
    )
    commands = plan.command_texts()
    parser_coverage = (
        _production_parser_coverage(device, commands) if normalized_mode == PRODUCTION_MODE else []
    )
    connection_issues = _connection_preflight(device)
    plan_violations = _execution_plan_violations(plan, normalized_mode)
    runtime_asset_health = _runtime_rule_asset_health(
        mode=normalized_mode,
        coverage=parser_coverage,
    )
    coverage_summary = _coverage_summary(
        mode=normalized_mode,
        commands=commands,
        coverage=parser_coverage,
        connection_issues=connection_issues,
        plan_violations=plan_violations,
        runtime_asset_health=runtime_asset_health,
    )
    return {
        "success": True,
        "device": _redact_device(device),
        "plan": plan.to_metadata(),
        "commands": commands,
        "parser_coverage": parser_coverage,
        "coverage_summary": coverage_summary,
    }


@router.post("/{device_id}/connectivity-test")
def test_device_connectivity_endpoint(device_id: str):
    repository = DeviceRepository()
    matches = repository.get_devices_by_filter(device_id=device_id, status=None)
    if not matches:
        raise HTTPException(status_code=404, detail="device not found")
    device = matches[0]
    issues = _connection_preflight(device)
    if issues:
        issue_code, issue_message = _first_issue(issues)
        updated = repository.record_connection_health(
            device_id,
            status="preflight_failed",
            failure_code=issue_code,
            error=issue_message,
            source="connectivity-test",
        )
        return {
            "success": False,
            "failure_code": "collector_preflight_failed",
            "device": _redact_device(updated or device),
            "connection_issues": issues,
        }
    from src.core.device_ops.tools import test_device_connectivity

    result = test_device_connectivity(device)
    updated = repository.record_connection_health(
        device_id,
        status="connected" if result.get("success") else "failed",
        failure_code=result.get("failure_code"),
        error=result.get("error"),
        latency_ms=_latency_ms(result.get("elapsed_seconds")),
        source="connectivity-test",
        metadata={
            "device_type": result.get("device_type"),
            "prompt": result.get("prompt"),
        },
    )
    return {
        **result,
        "device": _redact_device(updated or device),
        "connection_issues": [],
    }


@router.post("/{device_id}/identity-discovery")
def discover_device_identity_endpoint(device_id: str, request: Request):
    repository = DeviceRepository()
    matches = repository.get_devices_by_filter(device_id=device_id, status=None)
    if not matches:
        raise HTTPException(status_code=404, detail="device not found")
    device = matches[0]
    issues = _connection_preflight(device)
    if issues:
        issue_code, issue_message = _first_issue(issues)
        updated = repository.record_connection_health(
            device_id,
            status="preflight_failed",
            failure_code=issue_code,
            error=issue_message,
            source="identity-discovery",
        )
        return {
            "success": False,
            "failure_code": "collector_preflight_failed",
            "identity_status": "unknown",
            "device": _redact_device(updated or device),
            "connection_issues": issues,
        }

    result = discover_device_identity(device)
    profile_registration_error = None
    metadata = {
        "device_type": result.device_type,
        "prompt": result.prompt,
        "identity": identity_evidence(result.identity),
    }
    updated = repository.record_connection_health(
        device_id,
        status="connected" if result.failure_code != "connection_failed" else "failed",
        failure_code=result.failure_code,
        error=result.error,
        latency_ms=_latency_ms(result.elapsed_seconds),
        source="identity-discovery",
        metadata=metadata,
    )
    device_after_health = updated or device
    if result.identity.status == "known":
        attrs = dict(device_after_health.get("attributes") or {})
        attrs["identity_discovery"] = identity_evidence(result.identity)
        updated_identity = repository.update_device(
            device_id,
            vendor=result.identity.vendor,
            model=result.identity.model,
            family=result.identity.family,
            platform=platform_for_identity(result.identity, device_after_health.get("platform")),
            attributes=attrs,
        )
        device_after_health = updated_identity or device_after_health
        try:
            from src.core.parser.repository import ParserRepository

            ParserRepository().upsert_device_profile(
                vendor=result.identity.vendor or "",
                model=result.identity.model or "",
                family=result.identity.family,
                confidence=result.identity.confidence,
                evidence=identity_evidence(result.identity),
                status="confirmed",
            )
        except Exception as exc:
            profile_registration_error = str(exc)
    _audit_device_action(
        request,
        "device_identity_discovery",
        "device",
        device_id,
        {
            "success": result.success,
            "identity_status": result.identity_status,
            "identity": identity_evidence(result.identity),
            "profile_registration_error": profile_registration_error,
        },
    )
    return {
        **result.to_dict(),
        "device": _redact_device(device_after_health),
        "connection_issues": [],
        "warnings": (
            [{"code": "profile_registration_failed", "message": profile_registration_error}]
            if profile_registration_error
            else []
        ),
    }


@router.get("/{device_id}/connection-health")
def list_device_connection_health(
    device_id: str,
    limit: int = Query(50, ge=1, le=200),
):
    repository = DeviceRepository()
    matches = repository.get_devices_by_filter(device_id=device_id, status=None)
    if not matches:
        raise HTTPException(status_code=404, detail="device not found")
    return {
        "device": _redact_device(matches[0]),
        "items": repository.list_connection_health(device_id, limit=limit),
        "limit": limit,
    }


@router.get("/{device_id}/execution-plans")
def list_device_execution_plans(
    device_id: str,
    limit: int = Query(20, ge=1, le=100),
):
    matches = DeviceRepository().get_devices_by_filter(device_id=device_id, status=None)
    if not matches:
        raise HTTPException(status_code=404, detail="device not found")
    return {
        "device": _redact_device(matches[0]),
        "items": _list_execution_plans_for_device(device_id, limit=limit),
        "limit": limit,
    }


@router.get("/{device_id}/evidence-timeline")
def list_device_evidence_timeline(
    device_id: str,
    limit: int = Query(10, ge=1, le=50),
):
    matches = DeviceRepository().get_devices_by_filter(device_id=device_id, status=None)
    if not matches:
        raise HTTPException(status_code=404, detail="device not found")
    return _device_evidence_timeline(matches[0], limit=limit)
