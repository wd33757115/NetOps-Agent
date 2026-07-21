"""Async Celery wrappers for rule-asset engineering revision validation."""

from __future__ import annotations

from typing import Any

from src.core.celery_tasks.celery_app import celery


@celery.task(name="validate_rule_asset_engineering_revision", bind=True)
def validate_rule_asset_engineering_revision_task(
    self,
    bundle_id: str,
    contract: dict[str, Any],
    change_policies: list[dict[str, Any]],
    event_rules: list[dict[str, Any]],
    actor: str,
    reason: str,
    idempotency_key: str,
) -> dict[str, Any]:
    from src.core.rule_control.engineering_revision import RuleAssetEngineeringRevisionService

    result = RuleAssetEngineeringRevisionService().revise(
        bundle_id,
        contract=contract,
        change_policies=change_policies,
        event_rules=event_rules,
        actor=actor,
        reason=reason,
        idempotency_key=idempotency_key,
    )
    return {
        "success": True,
        "task_id": getattr(self.request, "id", None),
        "bundle_id": bundle_id,
        "result": result,
    }


def enqueue_engineering_revision(
    bundle_id: str,
    *,
    contract: dict[str, Any],
    change_policies: list[dict[str, Any]],
    event_rules: list[dict[str, Any]],
    actor: str,
    reason: str,
    idempotency_key: str,
) -> dict[str, Any]:
    async_result = validate_rule_asset_engineering_revision_task.delay(
        bundle_id,
        contract,
        change_policies,
        event_rules,
        actor,
        reason,
        idempotency_key,
    )
    return {
        "success": True,
        "async": True,
        "task_id": async_result.id,
        "bundle_id": bundle_id,
        "status": "queued",
    }
