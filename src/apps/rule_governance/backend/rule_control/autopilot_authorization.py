"""Internal release authority for one administrator-delegated AI task.

No HTTP endpoint accepts these lease credentials. Machine approval is recorded
as machine approval and cannot manufacture an engineer's semantic signature.
"""
from datetime import datetime, timezone

from src.core.check_items.assessment_policy import canonical_digest
from src.infrastructure.db.models import CheckItemVersionRecord, ParserControlSetting


def require_autopilot_release(db, authorization: tuple[str, str], *, kind: str, asset_id: str) -> dict:
    key, token = authorization
    row = db.get(ParserControlSetting, key)
    if not row:
        raise ValueError("自动发布授权不存在")
    state = row.value_json
    grant = state.get("publish_grant") or {}
    if (state.get("status") != "running" or state.get("stage") != "review"
            or not token or state.get("lease_token") != token
            or not state.get("lease_until")
            or datetime.fromisoformat(state["lease_until"]) <= datetime.now(timezone.utc)
            or not grant.get("by") or not grant.get("expires_at")
            or datetime.fromisoformat(grant["expires_at"]) <= datetime.now(timezone.utc)
            or grant.get("scope_digest") != canonical_digest(state["inputs"])):
        raise ValueError("自动发布授权已过期、暂停或范围已变化")
    inputs, outputs = state["inputs"], state["outputs"]
    check_item = db.get(CheckItemVersionRecord, inputs["check_item_version_id"])
    if not check_item or check_item.status != "active" or check_item.digest != inputs["check_item_digest"]:
        raise ValueError("人工检查项版本已变化，自动发布被阻断")
    expected = {
        "parser": (outputs.get("parser") or {}).get("template_id"),
        "rules": (outputs.get("rules") or {}).get("bundle_id"),
        "implementation": (outputs.get("implementation") or {}).get("id"),
    }.get(kind)
    if not expected or str(expected) != str(asset_id):
        raise ValueError("资产不属于本次管理员授权的自动制作任务")
    if inputs["result_mode"] == "structured" and not outputs.get("projection", {}).get("validation", {}).get("success"):
        raise ValueError("标准数据验证尚未完成")
    return {"mode": "administrator_delegated_automation", "task_key": key, "run_id": state["run_id"],
            "authorized_by_user_id": grant.get("by_user_id"),
            "authorized_by": grant["by"], "authorized_at": grant["authorized_at"], "scope_digest": grant["scope_digest"]}
