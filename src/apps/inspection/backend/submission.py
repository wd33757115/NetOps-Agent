from __future__ import annotations


def _launch_patrol(payload, *, actor, batch_id, intent):
    from src.platform.inventory.api import (
        PRODUCTION_MODE,
        DeviceRepository,
        ExecutionPlanRepository,
        HTTPException,
        PatrolBatchManifestRepository,
        StartPatrolCommand,
        StartPatrolHandler,
        _enqueue_patrol_batch_dispatch,
        _patrol_launch_preview,
        _patrol_target_snapshot,
        normalize_patrol_mode,
    )

    if payload.launch_mode not in {"workflow", "direct"}:
        raise HTTPException(status_code=400, detail="launch_mode must be workflow or direct")
    try:
        patrol_mode = normalize_patrol_mode(payload.mode)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    payload = payload.model_copy(update={"launch_mode": "direct"})
    if patrol_mode == PRODUCTION_MODE:
        if payload.workflow_name != "patrol-change-event":
            raise HTTPException(400, "生产巡检请使用固定巡检入口")

    preview = _patrol_launch_preview(payload, DeviceRepository())
    platform_readiness = preview.get("platform_readiness") or {}
    if not platform_readiness.get("can_launch", False):
        raise HTTPException(
            status_code=503,
            detail={
                "message": "patrol platform is not ready; no batch was created",
                "failure_code": "patrol_platform_not_ready",
                "platform_readiness": platform_readiness,
            },
        )
    if preview["error_count"] and not preview["rows"]:
        raise HTTPException(
            status_code=400,
            detail={
                "message": "no devices can build patrol execution plans",
                "errors": preview["errors"],
            },
        )
    if patrol_mode == PRODUCTION_MODE and not preview.get("check_item_mode"):
        raise HTTPException(409, "生产巡检需要已发布检查项和冻结运行包")
    try:
        result = StartPatrolHandler(
            plan_repository=ExecutionPlanRepository(),
            batch_repository=PatrolBatchManifestRepository(),
            batch_dispatcher=_enqueue_patrol_batch_dispatch,
            batch_id_factory=lambda: batch_id,
        ).handle(
            StartPatrolCommand(
                rows=preview["rows"],
                target_count=preview["target_count"],
                skipped_errors=[
                    *preview["errors"],
                    *preview.get("skipped", []),
                ],
                launch_mode=payload.launch_mode,
                workflow_name=payload.workflow_name,
                mode=payload.mode,
                save_baseline=payload.save_baseline,
                ticket_id=payload.ticket_id,
                target_snapshot={
                    **_patrol_target_snapshot(payload),
                    "selector": {
                        **(_patrol_target_snapshot(payload).get("selector") or {}),
                        "inspection": {**intent, "user_id": actor},
                    },
                },
                actor=actor,
                target_devices=list(preview.get("target_devices") or []),
                check_item_ids_by_device=dict(preview.get("check_item_ids_by_device") or {}),
                profile_version_ids_by_device=dict(
                    preview.get("profile_version_ids_by_device") or {}
                ),
                scope_contract=(
                    dict(payload.scope_contract) if payload.scope_contract is not None else None
                ),
                scope_exclusions=dict(payload.scope_exclusions or {}),
                scope_not_applicable=dict(payload.scope_not_applicable or {}),
            )
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return result
