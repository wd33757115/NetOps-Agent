"""Idempotent admission and explicit re-execution of existing patrol business scope."""

import hashlib
import json
import uuid
from contextlib import ExitStack

from fastapi import HTTPException

from src.infrastructure.db.models import (
    PatrolBatchDeviceRecord,
    PatrolBatchRecord,
    PatrolScopeContractVersionRecord,
    WorkflowRun,
    WorkflowStepRecord,
)
from src.infrastructure.db.postgres import get_db_session

from .online_execution import OnlinePatrolBusyError, execution_lock
from .online_reads import states_for_targets


def launch_patrol(payload, *, actor, origin=None):
    from src.gateway.devices_api import _launch_patrol

    if not actor:
        raise HTTPException(403, "巡检缺少执行用户")
    data = payload.model_dump(exclude={"request_id"})
    if data["mode"] == "production":
        data["launch_mode"] = "direct"
    digest = hashlib.sha256(
        json.dumps(
            {"request": data, "origin": origin},
            sort_keys=True,
            ensure_ascii=False,
        ).encode()
    ).hexdigest()
    request_id = payload.request_id or str(uuid.uuid4())
    batch_id = "patrol-batch-" + str(
        uuid.uuid5(uuid.NAMESPACE_URL, f"inspection/{actor}/{request_id}")
    )
    try:
        with execution_lock("launch/" + batch_id):
            with get_db_session() as db:
                batch = db.get(PatrolBatchRecord, batch_id)
                if batch is not None:
                    previous = dict((batch.selector_json or {}).get("inspection") or {})
                    if previous.get("request_digest") != digest:
                        raise HTTPException(409, "同一请求编号不能用于不同巡检范围")
                    return {
                        "success": batch.dispatch_status != "failed",
                        "idempotent": True,
                        "patrol_batch_id": batch_id,
                        "launch_mode": batch.launch_mode,
                        "workflow_name": batch.workflow_name,
                        "mode": batch.patrol_mode,
                        "save_baseline": bool(batch.save_baseline),
                        "ticket_id": batch.ticket_id,
                        "target_count": batch.target_count,
                        "launched_count": batch.launched_count,
                        "skipped_count": batch.skipped_count,
                        "dispatch_status": batch.dispatch_status,
                        "queued_count": 0,
                        "runs": [],
                        "tasks": [],
                    }
            return _launch_patrol(
                payload,
                actor=actor,
                batch_id=batch_id,
                intent={
                    "request_digest": digest,
                    "request_id": request_id,
                    "scope_contract": payload.scope_contract,
                    "scope_exclusions": payload.scope_exclusions,
                    "scope_not_applicable": payload.scope_not_applicable,
                    **(origin or {}),
                },
            )
    except OnlinePatrolBusyError as exc:
        raise HTTPException(409, "同一巡检请求正在提交，请使用原请求编号重试") from exc


def repeat_batch(batch_id, *, actor, request_id=None, failed_only=False, device_id=None):
    from src.gateway.devices_api import DevicePatrolLaunchRequest

    with get_db_session() as db:
        batch = db.get(PatrolBatchRecord, batch_id)
        if batch is None:
            raise HTTPException(404, "巡检批次不存在")
        if batch.archived:
            raise HTTPException(409, "请先恢复已归档批次")
        if batch.workflow_name != "patrol-change-event" or batch.patrol_mode != "production":
            raise HTTPException(409, "此入口仅重新执行在线生产巡检；离线任务请从原作业重试")
        targets = db.query(PatrolBatchDeviceRecord).filter_by(patrol_batch_id=batch_id).all()
        states = states_for_targets(db, targets) if batch.launch_mode == "direct" else {}
        selected, task_ids = [], []
        workflow_ids = []
        for target in targets:
            if device_id and target.device_id != device_id:
                continue
            state = states.get(target.id)
            if state:
                status = state["status"]
            elif target.workflow_run_id:
                run = db.get(WorkflowRun, target.workflow_run_id)
                status = run.status if run else "failed"
            else:
                status = target.dispatch_status
            if failed_only and status not in {"failed", "cancelled", "skipped"}:
                continue
            if status not in {"completed", "failed", "cancelled", "skipped"}:
                raise HTTPException(409, "原巡检仍在执行，结束后才能重跑")
            selected.append(target.device_id)
            if target.celery_task_id:
                task_ids.append(target.celery_task_id)
            if target.workflow_run_id:
                workflow_ids.append(target.workflow_run_id)
        if (
            workflow_ids
            and db.query(WorkflowStepRecord)
            .filter(
                WorkflowStepRecord.run_id.in_(workflow_ids),
                WorkflowStepRecord.status == "running",
            )
            .first()
        ):
            raise HTTPException(409, "历史巡检步骤尚未退出，请先排空旧 Worker")
        if not selected:
            raise HTTPException(409, "没有可重试的目标设备")
        intent = dict((batch.selector_json or {}).get("inspection") or {})
        contract = intent.get("scope_contract")
        if contract is None and batch.scope_contract_version_id:
            version = db.get(PatrolScopeContractVersionRecord, batch.scope_contract_version_id)
            contract = dict(version.contract_json) if version else None
        payload = DevicePatrolLaunchRequest(
            device_ids=sorted(set(selected)),
            mode="production",
            launch_mode="direct",
            save_baseline=bool(batch.save_baseline),
            ticket_id=batch.ticket_id,
            request_id=request_id,
            scope_contract=contract,
            scope_exclusions=intent.get("scope_exclusions"),
            scope_not_applicable=intent.get("scope_not_applicable"),
        )
    try:
        with ExitStack() as stack:
            for task_id in sorted(set(task_ids)):
                stack.enter_context(execution_lock(task_id))
            result = launch_patrol(
                payload,
                actor=actor,
                origin={
                    "source_batch_id": batch_id,
                    "operation": "retry" if failed_only else "rerun",
                },
            )
    except OnlinePatrolBusyError as exc:
        raise HTTPException(409, "原 Worker 尚未退出，请稍后重试") from exc
    return {
        **result,
        "patrol_batch_id": batch_id,
        "new_patrol_batch_id": result["patrol_batch_id"],
        "retryable_count": len(selected),
        "retried_count": len(selected) if failed_only else 0,
        "rerun_count": len(selected),
        "deduplicated_device_count": len(selected),
    }


def recover_incomplete_submissions():
    """A crashed freeze is visible as failed; recovery must never launch a partial plan."""
    from datetime import datetime, timedelta, timezone

    cutoff = datetime.now(timezone.utc) - timedelta(minutes=10)
    with get_db_session() as db:
        ids = [
            row.patrol_batch_id
            for row in db.query(PatrolBatchRecord)
            .filter(
                PatrolBatchRecord.launch_mode == "direct",
                PatrolBatchRecord.patrol_mode == "production",
                PatrolBatchRecord.dispatch_status == "pending",
                PatrolBatchRecord.runtime_manifest_id.is_(None),
                PatrolBatchRecord.updated_at < cutoff,
            )
            .all()
            if (row.selector_json or {}).get("inspection") and not row.source_authority_request_id
        ]
    recovered = []
    for batch_id in ids:
        try:
            with execution_lock("launch/" + batch_id), get_db_session() as db:
                batch = (
                    db.query(PatrolBatchRecord)
                    .filter_by(patrol_batch_id=batch_id)
                    .with_for_update()
                    .one()
                )
                if batch.runtime_manifest_id or batch.dispatch_status != "pending":
                    continue
                batch.dispatch_status = "failed"
                for target in (
                    db.query(PatrolBatchDeviceRecord).filter_by(patrol_batch_id=batch_id).all()
                ):
                    if target.dispatch_status == "pending":
                        target.dispatch_status = "failed"
                        target.failure_code = "freeze_interrupted"
                        target.error_message = "巡检计划冻结中断，请重新发起巡检"
                recovered.append(batch_id)
        except OnlinePatrolBusyError:
            continue
    return recovered
