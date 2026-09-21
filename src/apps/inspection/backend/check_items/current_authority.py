"""Governed offline-current qualification requests and actor separation."""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Callable, Mapping, Sequence
from contextlib import AbstractContextManager
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func

from src.common.config import settings
from src.core.check_items.catalog import CheckItemCatalogRepository
from src.core.check_items.planning import build_inventory_snapshot
from src.core.check_items.resolver import CheckItemImplementationResolver
from src.core.check_items.scope import resolve_patrol_scope
from src.core.check_items.scope_repository import PatrolScopeRepository
from src.core.check_items.source_qualification import (
    ATTESTED_OFFLINE_CURRENT,
    build_attested_offline_current_qualification,
)
from src.infrastructure.db.models import (
    CommandSnapshot,
    NetworkDevice,
    OfflinePatrolDeviceBindingRecord,
    OfflinePatrolJobRecord,
    OfflinePatrolPeriodRecord,
    OfflinePatrolSourceFileRecord,
    PatrolBatchDeviceRecord,
    PatrolBatchRecord,
    PatrolCollectionResult,
    PatrolCurrentAuthorityRequestRecord,
    PatrolRun,
    PatrolSourceQualificationRecord,
    WorkflowRun,
)
from src.infrastructure.db.postgres import get_db_session

CURRENT_AUTHORITY_REQUEST_SCHEMA = "patrol-current-authority-request/v1"
CURRENT_AUTHORITY_PREPARE_SCOPE = "patrol-current-authority.prepare/v1"
CURRENT_AUTHORITY_ATTEST_SCOPE = "patrol-current-authority.attest/v1"
CURRENT_AUTHORITY_REVOKE_SCOPE = "patrol-current-authority.revoke/v1"

OBSERVED_AT_EVIDENCE_OPTIONS = (
    "current_period_observed_at",
    "snapshot_observed_at_set",
)
COMPLETENESS_EVIDENCE_OPTIONS = (
    "source_manifest",
    "identity_binding_manifest",
    "current_run_snapshot_manifest",
    "offline_job_result_ledger",
)


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()


def _utc(value: datetime | None = None) -> datetime:
    value = value or datetime.now(timezone.utc)
    return value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _idempotency_hash(scope: str, actor: str, key: str) -> str:
    normalized = str(key or "").strip()
    if len(normalized) < 8:
        raise CurrentAuthorityError(
            "idempotency_key_required",
            "Idempotency-Key must contain at least 8 characters",
            http_status=400,
        )
    return _digest(
        {
            "scope": scope,
            "actor_principal_id": str(actor or "").strip(),
            "key": normalized,
        }
    )


def _canonical_command(value: Any) -> str:
    return " ".join(str(value or "").strip().lower().split())


class CurrentAuthorityError(ValueError):
    """Stable business error returned by current-authority APIs."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        http_status: int = 409,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = str(code)
        self.message = str(message)
        self.http_status = int(http_status)
        self.details = dict(details or {})

    def to_detail(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "details": dict(self.details),
        }


def _error_summary(error: Exception) -> str:
    code = str(getattr(error, "code", type(error).__name__))
    return f"{code}: {error}"[:4000]


_BLOCKER_MESSAGES = {
    "source_manifest_missing": "当前周期没有可证明的源文件",
    "source_manifest_unverified": "存在尚未完成哈希校验的源文件",
    "observed_at_evidence_missing": "缺少采集时间证明",
    "observed_at_evidence_invalid": "采集时间证明不是平台内受支持的证据",
    "observed_at_untrusted": "采集时间不在允许的当前证据窗口内",
    "identity_binding_unconfirmed": "存在未与 Inventory 唯一确认的设备",
    "current_run_missing": "离线任务没有形成当前周期 PatrolRun",
    "current_snapshots_missing": "当前周期没有命令 Snapshot",
    "snapshot_source_unlinked": "命令 Snapshot 无法关联到已校验的源文件",
    "snapshot_content_hash_mismatch": "命令 Snapshot 正文与冻结哈希不一致",
    "device_result_unaccounted": "存在没有成功输出或失败记录的目标设备",
    "scope_check_items_missing": "无法从当前周期识别任何巡检检查项",
    "scope_unresolved": "应巡范围仍存在未处置目标",
    "completeness_statement_missing": "缺少完整性声明",
    "completeness_evidence_missing": "缺少完整性证据引用",
    "completeness_evidence_incomplete": "完整性证据没有覆盖文件、设备、运行结果与失败账本",
    "collection_tool_missing": "缺少采集工具信息",
    "collector_identity_missing": "缺少采集主体信息",
}

_BLOCKER_REMEDIATIONS = {
    "source_manifest_missing": {
        "code": "upload_current_sources",
        "label": "补充当前周期源文件",
        "target": "sources",
    },
    "source_manifest_unverified": {
        "code": "verify_current_sources",
        "label": "重新校验源文件",
        "target": "sources",
    },
    "observed_at_evidence_missing": {
        "code": "edit_proof",
        "label": "补充采集时间证据",
        "target": "proof_form",
    },
    "observed_at_evidence_invalid": {
        "code": "edit_proof",
        "label": "改用平台支持的时间证据",
        "target": "proof_form",
    },
    "observed_at_untrusted": {
        "code": "create_fresh_current_job",
        "label": "使用新鲜采集新建任务",
        "target": "new_current_job",
    },
    "identity_binding_unconfirmed": {
        "code": "confirm_inventory_binding",
        "label": "绑定 Inventory 设备",
        "target": "sources",
    },
    "current_run_missing": {
        "code": "retry_offline_patrol",
        "label": "重新执行离线巡检",
        "target": "new_current_job",
    },
    "current_snapshots_missing": {
        "code": "retry_offline_patrol",
        "label": "重新执行离线巡检",
        "target": "new_current_job",
    },
    "snapshot_source_unlinked": {
        "code": "reimport_current_sources",
        "label": "重新导入并执行",
        "target": "new_current_job",
    },
    "snapshot_content_hash_mismatch": {
        "code": "reimport_current_sources",
        "label": "重新导入未篡改源文件",
        "target": "new_current_job",
    },
    "device_result_unaccounted": {
        "code": "capture_complete_device_ledger",
        "label": "补齐成功与失败设备台账",
        "target": "new_current_job",
    },
    "scope_check_items_missing": {
        "code": "repair_device_and_check_mapping",
        "label": "核对设备型号与检查项映射",
        "target": "sources",
    },
    "scope_unresolved": {
        "code": "resolve_scope",
        "label": "处置应巡范围缺口",
        "target": "sources",
    },
    "completeness_statement_missing": {
        "code": "edit_proof",
        "label": "补充完整性声明",
        "target": "proof_form",
    },
    "completeness_evidence_missing": {
        "code": "edit_proof",
        "label": "补充完整性证据",
        "target": "proof_form",
    },
    "completeness_evidence_incomplete": {
        "code": "edit_proof",
        "label": "补齐完整性证据",
        "target": "proof_form",
    },
    "collection_tool_missing": {
        "code": "edit_proof",
        "label": "填写采集工具",
        "target": "proof_form",
    },
    "collector_identity_missing": {
        "code": "edit_proof",
        "label": "填写采集主体",
        "target": "proof_form",
    },
}


class PatrolCurrentAuthorityService:
    """Prepare, attest and revoke one immutable offline-current request."""

    def __init__(
        self,
        session_scope: Callable[[], AbstractContextManager[Any]] = (get_db_session),
        *,
        now: Callable[[], datetime] | None = None,
        production_readiness: Callable[[], Mapping[str, Any]] | None = None,
    ) -> None:
        self._session_scope = session_scope
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._production_readiness = production_readiness

    def _require_production_readiness(self) -> dict[str, Any]:
        try:
            if self._production_readiness is not None:
                readiness = dict(self._production_readiness())
            else:
                from src.core.patrol.production_readiness import (
                    PatrolProductionReadinessService,
                )

                readiness = PatrolProductionReadinessService().evaluate(
                    workflow_name="patrol-attested-offline-current",
                    launch_mode="direct",
                )
        except Exception as exc:
            raise CurrentAuthorityError(
                "patrol_platform_not_ready",
                "无法确认生产巡检运行环境，当前证明未进入设备执行",
                http_status=503,
                details={"check_error": type(exc).__name__},
            ) from exc
        if not bool(readiness.get("can_launch")):
            raise CurrentAuthorityError(
                "patrol_platform_not_ready",
                "生产巡检运行环境未就绪，当前证明未进入设备执行",
                http_status=503,
                details={
                    "status": str(readiness.get("status") or "blocked"),
                    "checked_at": readiness.get("checked_at"),
                    "blockers": [
                        dict(item)
                        for item in readiness.get("blockers") or []
                        if isinstance(item, Mapping)
                    ],
                },
            )
        return readiness

    def prepare(
        self,
        *,
        offline_job_id: str,
        requested_by_principal_id: str,
        requested_by_display: str,
        idempotency_key: str,
        completeness_statement: str,
        completeness_evidence_refs: Sequence[str],
        observed_at_evidence_refs: Sequence[str],
        collection_tool: str,
        collector_identity: str,
        scope_contract: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        actor = str(requested_by_principal_id or "").strip()
        if not actor:
            raise CurrentAuthorityError(
                "actor_required",
                "authenticated principal id is required",
                http_status=403,
            )
        key_hash = _idempotency_hash(
            CURRENT_AUTHORITY_PREPARE_SCOPE,
            actor,
            idempotency_key,
        )
        now = _utc(self._now())
        with self._session_scope() as db:
            job = (
                db.query(OfflinePatrolJobRecord)
                .filter(OfflinePatrolJobRecord.id == offline_job_id)
                .with_for_update()
                .one_or_none()
            )
            if job is None or str(job.purpose) != "offline_patrol":
                raise CurrentAuthorityError(
                    "offline_job_not_found",
                    "offline patrol job not found",
                    http_status=404,
                )
            if str(job.report_intent or "historical") != ("current_attestation"):
                raise CurrentAuthorityError(
                    "historical_intent_only",
                    "historical offline jobs cannot request current authority",
                )
            if str(job.status or "") != "completed":
                raise CurrentAuthorityError(
                    "offline_job_not_completed",
                    "offline patrol job must be completed before qualification",
                )
            if bool(job.archived):
                raise CurrentAuthorityError(
                    "offline_job_archived",
                    "archived offline patrol jobs cannot request current authority",
                )

            basis = self._build_basis(
                db,
                job=job,
                now=now,
                completeness_statement=completeness_statement,
                completeness_evidence_refs=completeness_evidence_refs,
                observed_at_evidence_refs=observed_at_evidence_refs,
                collection_tool=collection_tool,
                collector_identity=collector_identity,
                scope_contract=scope_contract,
                requested_by_principal_id=actor,
                requested_by_display=requested_by_display,
            )
            request_digest = _digest(basis["request_basis"])
            by_key = (
                db.query(PatrolCurrentAuthorityRequestRecord)
                .filter(PatrolCurrentAuthorityRequestRecord.idempotency_key_hash == key_hash)
                .with_for_update()
                .one_or_none()
            )
            if by_key is not None:
                if str(by_key.request_digest) != request_digest:
                    raise CurrentAuthorityError(
                        "idempotency_conflict",
                        "Idempotency-Key already belongs to different proof content",
                    )
                replay = self._serialize(db, by_key)
                replay["idempotent_replay"] = True
                return replay
            by_digest = (
                db.query(PatrolCurrentAuthorityRequestRecord)
                .filter(PatrolCurrentAuthorityRequestRecord.request_digest == request_digest)
                .order_by(PatrolCurrentAuthorityRequestRecord.created_at.desc())
                .first()
            )
            if by_digest is not None:
                replay = self._serialize(db, by_digest)
                replay["idempotent_replay"] = True
                return replay

            request_id = str(
                uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    f"netops/current-authority-request/{request_digest}",
                )
            )
            blockers = list(basis["blocker_codes"])
            expires_at = min(
                now
                + timedelta(
                    hours=max(
                        1,
                        int(settings.PATROL_CURRENT_AUTHORITY_REQUEST_TTL_HOURS),
                    )
                ),
                basis["observed_at_min"]
                + timedelta(
                    hours=max(
                        1,
                        int(settings.PATROL_OFFLINE_CURRENT_MAX_AGE_HOURS),
                    )
                ),
            )
            row = PatrolCurrentAuthorityRequestRecord(
                id=request_id,
                offline_job_id=str(job.id),
                candidate_batch_id=None,
                status=("blocked" if blockers else "ready_for_attestation"),
                blocker_codes_json=blockers,
                idempotency_scope=CURRENT_AUTHORITY_PREPARE_SCOPE,
                idempotency_key_hash=key_hash,
                request_digest=request_digest,
                source_manifest_digest=basis["source_manifest_digest"],
                observed_at_digest=basis["observed_at_digest"],
                identity_binding_digest=basis["identity_binding_digest"],
                scope_contract_version_id=basis.get("scope_contract_version_id"),
                scope_contract_digest=basis["scope_contract_digest"],
                intended_target_matrix_digest=basis["intended_target_matrix_digest"],
                scope_decision_digest=basis["scope_decision_digest"],
                completeness_evidence_digest=basis["completeness_evidence_digest"],
                request_json=basis["request_basis"],
                requested_by_principal_id=actor,
                requested_by_display=str(requested_by_display or actor).strip(),
                prepared_at=now,
                expires_at=expires_at,
                created_at=now,
                updated_at=now,
            )
            db.add(row)
            db.flush()
            if not blockers:
                self._create_candidate_batch(
                    db,
                    request=row,
                    job=job,
                    basis=basis,
                    now=now,
                )
            db.flush()
            created = self._serialize(db, row)
            created["idempotent_replay"] = False
            return created

    def get_for_job(
        self,
        offline_job_id: str,
    ) -> dict[str, Any] | None:
        with self._session_scope() as db:
            row = (
                db.query(PatrolCurrentAuthorityRequestRecord)
                .filter(PatrolCurrentAuthorityRequestRecord.offline_job_id == offline_job_id)
                .order_by(
                    PatrolCurrentAuthorityRequestRecord.created_at.desc(),
                    PatrolCurrentAuthorityRequestRecord.id.desc(),
                )
                .first()
            )
            return self._serialize(db, row) if row is not None else None

    def pending_dispatch_request_ids(
        self,
        *,
        limit: int = 100,
    ) -> list[str]:
        """Return approved requests whose production plans are not frozen yet."""

        now = _utc(self._now())
        stale_before = now - timedelta(minutes=5)
        with self._session_scope() as db:
            rows = (
                db.query(PatrolCurrentAuthorityRequestRecord.id)
                .filter(
                    (PatrolCurrentAuthorityRequestRecord.status == "approved")
                    | (
                        (PatrolCurrentAuthorityRequestRecord.status == "attesting")
                        & (PatrolCurrentAuthorityRequestRecord.updated_at < stale_before)
                    )
                )
                .order_by(
                    PatrolCurrentAuthorityRequestRecord.updated_at,
                    PatrolCurrentAuthorityRequestRecord.id,
                )
                .limit(max(1, min(int(limit), 500)))
                .all()
            )
            return [str(row[0]) for row in rows]

    def prepare_dispatch(self, request_id: str) -> dict[str, Any]:
        """Freeze a production EffectivePlan and persist idempotent device plans.

        The source offline job remains immutable. The resulting plans launch a
        separate production batch whose fixed collection phase replays only the
        frozen, attested command evidence into a new PatrolRun namespace.
        """

        now = _utc(self._now())
        planning_error: Exception | None = None
        with self._session_scope() as db:
            request = (
                db.query(PatrolCurrentAuthorityRequestRecord)
                .filter(PatrolCurrentAuthorityRequestRecord.id == request_id)
                .with_for_update()
                .one_or_none()
            )
            if request is None:
                raise CurrentAuthorityError(
                    "authority_request_not_found",
                    "current-authority request not found",
                    http_status=404,
                )
            if str(request.status) in {"dispatching", "completed"}:
                return self._serialize(db, request)
            if (
                str(request.status) == "attesting"
                and request.updated_at
                and _utc(request.updated_at) >= now - timedelta(minutes=5)
            ):
                current = self._serialize(db, request)
                current["planning_in_progress"] = True
                return current
            if str(request.status) not in {"approved", "attesting"}:
                raise CurrentAuthorityError(
                    "authority_request_not_approved",
                    "current-authority request is not approved for planning",
                    details={"status": str(request.status)},
                )
            if request.expires_at and _utc(request.expires_at) <= now:
                request.status = "expired"
                request.updated_at = now
                batch = db.get(PatrolBatchRecord, request.candidate_batch_id)
                if batch is not None:
                    batch.dispatch_status = "failed"
                    batch.dispatch_error = "qualification_expired"
                    batch.dispatch_completed_at = now
                    batch.updated_at = now
                planning_error = CurrentAuthorityError(
                    "qualification_expired",
                    "current-authority request expired before production planning",
                )
            else:
                qualification = db.get(
                    PatrolSourceQualificationRecord,
                    request.approved_qualification_id,
                )
                batch = db.get(
                    PatrolBatchRecord,
                    request.candidate_batch_id,
                )
                if (
                    qualification is None
                    or str(qualification.status) != "approved"
                    or batch is None
                    or str(batch.source_qualification_digest or "") != str(qualification.digest)
                ):
                    request.status = "failed"
                    request.updated_at = now
                    if batch is not None:
                        batch.dispatch_status = "failed"
                        batch.dispatch_error = "qualification_not_active"
                        batch.dispatch_completed_at = now
                        batch.updated_at = now
                    planning_error = CurrentAuthorityError(
                        "qualification_not_active",
                        "approved source qualification is missing or revoked",
                    )
                else:
                    job = db.get(
                        OfflinePatrolJobRecord,
                        request.offline_job_id,
                    )
                    try:
                        if job is None:
                            raise CurrentAuthorityError(
                                "authority_context_missing",
                                "offline job is missing before production planning",
                            )
                        self._revalidate_request(
                            db,
                            request,
                            job=job,
                            now=now,
                        )
                    except Exception as exc:
                        request.status = "failed"
                        request.updated_at = now
                        batch.dispatch_status = "failed"
                        batch.dispatch_error = _error_summary(exc)
                        batch.dispatch_completed_at = now
                        batch.updated_at = now
                        planning_error = exc
                    else:
                        request.status = "attesting"
                        request.updated_at = now
                        batch.dispatch_status = "planning"
                        batch.dispatch_error = None
                        batch.updated_at = now
                        frozen_payload = dict(request.request_json or {})
                        batch_id = str(batch.patrol_batch_id)

        if planning_error is not None:
            raise planning_error

        runtime_manifest_id: str | None = None
        runtime_manifest_digest: str | None = None
        try:
            self._require_production_readiness()
            plan_request = self._effective_plan_request(
                request_id=request_id,
                batch_id=batch_id,
                payload=frozen_payload,
            )
            from src.core.check_items.planning import (
                CheckItemEffectivePlanService,
            )

            frozen = CheckItemEffectivePlanService(self._session_scope).freeze(plan_request)
            runtime_manifest_id = str(frozen.manifest_id)
            runtime_manifest_digest = str(frozen.manifest_digest)
            frozen_scope = dict(frozen.manifest or {})
            expected_scope = dict(frozen_payload.get("scope") or {})
            scope_digest_pairs = (
                ("scope_contract_digest", "scope_contract_digest"),
                (
                    "intended_target_matrix_digest",
                    "intended_target_matrix_digest",
                ),
                ("scope_decision_digest", "scope_decision_digest"),
            )
            drifted = [
                manifest_key
                for manifest_key, request_key in scope_digest_pairs
                if str(frozen_scope.get(manifest_key) or "")
                != str(expected_scope.get(request_key) or "")
            ]
            if drifted:
                raise CurrentAuthorityError(
                    "authority_scope_drift",
                    "production plan scope differs from the attested source scope",
                    details={"digest_fields": drifted},
                )
            self._validate_frozen_dispatch_plan(frozen)
            actions_by_device: dict[str, list[dict[str, Any]]] = {}
            for action in frozen.collection_actions:
                actions_by_device.setdefault(str(action["device_id"]), []).append(dict(action))
            plan_ids: dict[str, str] = {}
            from src.core.collection import ExecutionPlanRepository

            repository = ExecutionPlanRepository(self._session_scope)
            selected_by_device = {
                str(device_id): [str(item) for item in values or []]
                for device_id, values in dict(
                    frozen_payload.get("selected_check_item_ids_by_device") or {}
                ).items()
            }
            for device_id, actions in sorted(actions_by_device.items()):
                plan_id = str(
                    uuid.uuid5(
                        uuid.NAMESPACE_URL,
                        f"netops/attested-offline-current-plan/{request_id}/{device_id}",
                    )
                )
                plan = {
                    "device_id": device_id,
                    "action": "patrol",
                    "mode": "production",
                    "profile_name": "attested-offline-current",
                    "profile_version_id": None,
                    "credential_ref": None,
                    "collector_options": {},
                    "policy_source": "attested_offline_current",
                    "selector": {
                        "authority_request_id": request_id,
                        "patrol_batch_id": batch_id,
                    },
                    "check_item_ids": selected_by_device.get(device_id, []),
                    "runtime_manifest_id": frozen.manifest_id,
                    "runtime_manifest_digest": frozen.manifest_digest,
                    "collection_items": [
                        {
                            "command": action["command"],
                            "action": "patrol",
                            "collection_item_id": action["id"],
                            "fallback_rank": action["fallback_rank"],
                            "check_item_bindings": list(action.get("bindings") or []),
                        }
                        for action in sorted(
                            actions,
                            key=lambda item: (
                                int(item.get("fallback_rank") or 0),
                                -int(item.get("priority") or 0),
                                str(item.get("canonical_command") or ""),
                            ),
                        )
                    ],
                }
                repository.create_plan(
                    plan,
                    plan_id=plan_id,
                    launch_source="direct",
                    patrol_batch_id=batch_id,
                )
                plan_ids[device_id] = plan_id
            return self._activate_dispatch_plans(
                request_id=request_id,
                batch_id=batch_id,
                plan_ids=plan_ids,
                runtime_manifest_id=frozen.manifest_id,
                runtime_manifest_digest=frozen.manifest_digest,
            )
        except Exception as exc:
            if isinstance(exc, CurrentAuthorityError) and exc.code == "patrol_platform_not_ready":
                self._defer_dispatch_prepare(
                    request_id=request_id,
                    batch_id=batch_id,
                    error=exc,
                )
            else:
                self._mark_dispatch_prepare_failed(
                    request_id=request_id,
                    batch_id=batch_id,
                    error=exc,
                    runtime_manifest_id=runtime_manifest_id,
                    runtime_manifest_digest=runtime_manifest_digest,
                )
            raise

    @staticmethod
    def _validate_frozen_dispatch_plan(frozen: Any) -> None:
        """Reject empty or internally incomplete production plans.

        Explicitly not-applicable targets may resolve without collection. Asset
        gaps and unsupported targets remain valid terminal facts when at least
        one other target can run, but an entirely blocked batch must not be
        presented as a completed production patrol.
        """

        decisions = [dict(item) for item in frozen.decisions]
        actions = [dict(item) for item in frozen.collection_actions]
        action_ids = {str(item.get("id") or "") for item in actions if item.get("id")}
        blocking_statuses = {"blocked_asset_missing", "unsupported"}
        blocking = [
            item for item in decisions if str(item.get("status") or "") in blocking_statuses
        ]
        invalid_planned: list[dict[str, Any]] = []
        referenced_action_ids: set[str] = set()
        for item in decisions:
            status = str(item.get("status") or "")
            refs = {str(value) for value in item.get("collection_action_ids") or [] if value}
            referenced_action_ids.update(refs)
            if status == "resolved" and (not refs or not refs.issubset(action_ids)):
                invalid_planned.append(item)
            elif status not in {
                "resolved",
                "not_applicable",
                *blocking_statuses,
            }:
                invalid_planned.append(item)

        orphan_action_ids = sorted(action_ids - referenced_action_ids)
        if not decisions or invalid_planned or orphan_action_ids:
            raise CurrentAuthorityError(
                "authority_production_plan_incomplete",
                "冻结生产计划未完整覆盖当前证明范围，请修复检查项实现或运行资产后重新提交",
                details={
                    "decision_count": len(decisions),
                    "collection_action_count": len(actions),
                    "invalid_target_pairs": [
                        {
                            "device_id": str(item.get("device_id") or ""),
                            "check_item_id": str(item.get("check_item_id") or ""),
                            "status": str(item.get("status") or ""),
                            "reason_codes": list(item.get("reason_codes") or []),
                        }
                        for item in invalid_planned[:50]
                    ],
                    "orphan_action_ids": orphan_action_ids[:50],
                },
            )

        if not actions and blocking:
            reason_codes = sorted(
                {str(code) for item in blocking for code in item.get("reason_codes") or [] if code}
            )
            raise CurrentAuthorityError(
                "authority_production_plan_incomplete",
                "当前证明范围没有可执行检查项，请先补齐并激活 RuntimeBundle 后重新提交",
                details={
                    "affected_device_count": len(
                        {str(item.get("device_id") or "") for item in blocking}
                    ),
                    "affected_check_item_count": len(
                        {str(item.get("check_item_id") or "") for item in blocking}
                    ),
                    "reason_codes": reason_codes,
                    "asset_gap_ids": list(dict(frozen.manifest or {}).get("asset_gap_ids") or []),
                },
            )

    def sync_workflow_terminal_state(self, workflow_run_id: str) -> dict[str, Any]:
        """Project one replay Workflow terminal state onto its authority request."""

        now = _utc(self._now())
        failures: list[tuple[str, str]] = []
        with self._session_scope() as db:
            target = (
                db.query(PatrolBatchDeviceRecord)
                .filter(PatrolBatchDeviceRecord.workflow_run_id == workflow_run_id)
                .one_or_none()
            )
            if target is None:
                return {"success": False, "reason": "batch_target_missing"}
            batch = db.get(PatrolBatchRecord, target.patrol_batch_id)
            if (
                batch is None
                or str(batch.workflow_name) != "patrol-attested-offline-current"
                or not batch.source_authority_request_id
            ):
                return {"success": False, "reason": "not_authority_batch"}
            request = db.get(
                PatrolCurrentAuthorityRequestRecord,
                batch.source_authority_request_id,
            )
            if request is None:
                return {"success": False, "reason": "authority_request_missing"}
            rows = (
                db.query(PatrolBatchDeviceRecord, WorkflowRun)
                .join(
                    WorkflowRun,
                    WorkflowRun.id == PatrolBatchDeviceRecord.workflow_run_id,
                )
                .filter(PatrolBatchDeviceRecord.patrol_batch_id == batch.patrol_batch_id)
                .all()
            )
            terminal = {"completed", "failed", "cancelled"}
            active = [str(run.status) for _device, run in rows if str(run.status) not in terminal]
            failures = [
                (str(device.device_id), str(run.status))
                for device, run in rows
                if str(run.status) in {"failed", "cancelled"}
            ]
            if not active and len(rows) == int(batch.launched_count or 0):
                request.status = "completed"
                request.updated_at = now
            payload = {
                "success": not active,
                "request_id": str(request.id),
                "candidate_batch_id": str(batch.patrol_batch_id),
                "authority_request_status": str(request.status),
                "workflow_count": len(rows),
                "active_workflow_count": len(active),
                "failed_workflow_count": len(failures),
            }
        if failures:
            from src.core.check_items.run_finalizer import (
                CheckItemRunFinalizer,
            )

            finalizer = CheckItemRunFinalizer(self._session_scope)
            for device_id, workflow_status in failures:
                finalizer.fail_device(
                    patrol_batch_id=str(payload["candidate_batch_id"]),
                    device_id=device_id,
                    reason_code=f"workflow_{workflow_status}",
                    status="assessment_failed",
                    evidence_refs=(workflow_run_id,),
                )
        return payload

    def _effective_plan_request(
        self,
        *,
        request_id: str,
        batch_id: str,
        payload: Mapping[str, Any],
    ) -> Any:
        from src.core.check_items.planning import EffectivePlanRequest

        inventory_rows = [
            dict(item)
            for item in dict(payload.get("inventory_snapshot") or {}).get("devices") or []
        ]
        scope_device_rows = [
            dict(item)
            for item in payload.get("scope_device_facts") or []
            if isinstance(item, Mapping)
        ]
        frozen_device_rows = scope_device_rows or inventory_rows
        binding_by_device = {
            str(item.get("inventory_device_id") or ""): dict(item)
            for item in payload.get("identity_bindings") or []
            if item.get("inventory_device_id")
        }
        devices: list[dict[str, Any]] = []
        for item in frozen_device_rows:
            device_id = str(item.get("device_id") or "")
            binding = binding_by_device.get(device_id, {})
            attributes = dict(item.get("attributes") or {})
            if "capabilities" not in attributes:
                attributes["capabilities"] = list(item.get("capabilities") or [])
            role = str(
                item.get("device_role")
                or item.get("role")
                or attributes.get("device_role")
                or attributes.get("role")
                or ""
            ).strip()
            if role:
                attributes["role"] = role
            devices.append(
                {
                    **item,
                    "id": device_id,
                    "device_id": device_id,
                    "device_name": str(binding.get("device_name") or device_id),
                    "management_ip": str(binding.get("management_ip") or ""),
                    "attributes": attributes,
                }
            )
        # ``scope_device_facts`` is an execution-oriented, derived view of the
        # frozen inventory.  It deliberately lifts role/capability values into
        # the resolver input, while the authority digest is defined over the
        # immutable inventory snapshot captured at preparation time.  Rebuild
        # that digest from its own frozen rows instead of the enriched planner
        # rows; otherwise an attribute-only role becomes a root ``role`` and
        # falsely invalidates a valid attestation.
        inventory = build_inventory_snapshot(inventory_rows)
        expected_inventory_digest = str(
            dict(payload.get("inventory_snapshot") or {}).get("digest") or ""
        )
        if inventory["digest"] != expected_inventory_digest:
            raise CurrentAuthorityError(
                "authority_digest_mismatch",
                "frozen inventory snapshot no longer verifies",
            )
        selected = {
            str(device_id): tuple(str(value) for value in values or [])
            for device_id, values in dict(
                payload.get("selected_check_item_ids_by_device") or {}
            ).items()
        }
        period = dict(payload.get("current_period") or {})
        observed_at_text = str(period.get("observed_at_max") or period.get("observed_at"))
        scope = dict(payload.get("scope") or {})
        return EffectivePlanRequest(
            patrol_batch_id=batch_id,
            devices=tuple(devices),
            check_item_ids=(),
            check_item_ids_by_device=selected,
            target_manifest_digest=str(payload.get("target_manifest_digest") or ""),
            inventory_snapshot_digest=expected_inventory_digest,
            current_observed_at=_utc(
                datetime.fromisoformat(observed_at_text.replace("Z", "+00:00"))
            ),
            actor=f"current-authority:{request_id}",
            report_purpose="production_current",
            scope_contract=dict(scope.get("scope_contract") or {}) or None,
        )

    def _activate_dispatch_plans(
        self,
        *,
        request_id: str,
        batch_id: str,
        plan_ids: Mapping[str, str],
        runtime_manifest_id: str,
        runtime_manifest_digest: str,
    ) -> dict[str, Any]:
        now = _utc(self._now())
        with self._session_scope() as db:
            request = (
                db.query(PatrolCurrentAuthorityRequestRecord)
                .filter(PatrolCurrentAuthorityRequestRecord.id == request_id)
                .with_for_update()
                .one()
            )
            batch = (
                db.query(PatrolBatchRecord)
                .filter(PatrolBatchRecord.patrol_batch_id == batch_id)
                .with_for_update()
                .one()
            )
            if str(request.status) not in {"attesting", "approved"}:
                if str(request.status) in {"dispatching", "completed"}:
                    return self._serialize(db, request)
                raise CurrentAuthorityError(
                    "authority_request_state_changed",
                    "current-authority request changed while planning",
                )
            rows = (
                db.query(PatrolBatchDeviceRecord)
                .filter(PatrolBatchDeviceRecord.patrol_batch_id == batch_id)
                .with_for_update()
                .all()
            )
            for row in rows:
                plan_id = plan_ids.get(str(row.device_id))
                row.execution_plan_id = plan_id
                row.dispatch_attempts = 0
                row.failure_code = None
                row.error_message = None
                row.updated_at = now
                if plan_id:
                    row.selection_status = "selected"
                    row.preflight_status = "ready"
                    row.dispatch_status = "pending"
                else:
                    row.selection_status = "skipped"
                    row.preflight_status = "resolved_without_collection"
                    row.dispatch_status = "skipped"
            batch.runtime_manifest_id = runtime_manifest_id
            batch.runtime_manifest_digest = runtime_manifest_digest
            batch.skipped_count = sum(1 for row in rows if row.selection_status == "skipped")
            batch.dispatch_error = None
            batch.dispatch_requested_at = now
            if plan_ids:
                request.status = "dispatching"
                batch.dispatch_status = "pending"
                batch.dispatch_completed_at = None
            else:
                request.status = "completed"
                batch.dispatch_status = "completed"
                batch.dispatch_completed_at = now
            request.updated_at = now
            batch.updated_at = now
            result = self._serialize(db, request)
            result.update(
                {
                    "runtime_manifest_id": runtime_manifest_id,
                    "runtime_manifest_digest": runtime_manifest_digest,
                    "planned_device_count": len(plan_ids),
                    "resolved_without_collection_count": int(batch.skipped_count or 0),
                }
            )
            return result

    def _mark_dispatch_prepare_failed(
        self,
        *,
        request_id: str,
        batch_id: str,
        error: Exception,
        runtime_manifest_id: str | None = None,
        runtime_manifest_digest: str | None = None,
    ) -> None:
        now = _utc(self._now())
        error_code = str(getattr(error, "code", type(error).__name__))
        error_message = str(error)[:4000]
        with self._session_scope() as db:
            request = db.get(PatrolCurrentAuthorityRequestRecord, request_id)
            batch = db.get(PatrolBatchRecord, batch_id)
            if request is not None and str(request.status) not in {
                "revoked",
                "expired",
            }:
                request.status = "failed"
                request.updated_at = now
            if batch is not None:
                batch.dispatch_status = "failed"
                batch.dispatch_error = _error_summary(error)
                if runtime_manifest_id:
                    batch.runtime_manifest_id = runtime_manifest_id
                if runtime_manifest_digest:
                    batch.runtime_manifest_digest = runtime_manifest_digest
                batch.dispatch_completed_at = now
                batch.updated_at = now
                targets = (
                    db.query(PatrolBatchDeviceRecord)
                    .filter(
                        PatrolBatchDeviceRecord.patrol_batch_id == batch_id,
                        PatrolBatchDeviceRecord.workflow_run_id.is_(None),
                        PatrolBatchDeviceRecord.celery_task_id.is_(None),
                    )
                    .with_for_update()
                    .all()
                )
                for target in targets:
                    target.preflight_status = "failed"
                    target.dispatch_status = "failed"
                    target.failure_code = error_code
                    target.error_message = error_message
                    target.updated_at = now

    def _defer_dispatch_prepare(
        self,
        *,
        request_id: str,
        batch_id: str,
        error: CurrentAuthorityError,
    ) -> None:
        """Keep an approved request retryable during infrastructure outages."""

        now = _utc(self._now())
        with self._session_scope() as db:
            request = db.get(PatrolCurrentAuthorityRequestRecord, request_id)
            batch = db.get(PatrolBatchRecord, batch_id)
            if request is not None and str(request.status) not in {
                "revoked",
                "expired",
            }:
                request.status = "approved"
                request.updated_at = now
            if batch is not None:
                batch.dispatch_status = "awaiting_current_plan"
                batch.dispatch_error = _error_summary(error)
                batch.dispatch_completed_at = None
                batch.updated_at = now
                targets = (
                    db.query(PatrolBatchDeviceRecord)
                    .filter(
                        PatrolBatchDeviceRecord.patrol_batch_id == batch_id,
                        PatrolBatchDeviceRecord.workflow_run_id.is_(None),
                        PatrolBatchDeviceRecord.celery_task_id.is_(None),
                    )
                    .with_for_update()
                    .all()
                )
                for target in targets:
                    target.preflight_status = "awaiting_current_plan"
                    target.dispatch_status = "awaiting_current_plan"
                    target.failure_code = None
                    target.error_message = None
                    target.updated_at = now

    def attest(
        self,
        *,
        offline_job_id: str,
        request_digest: str,
        attested_by_principal_id: str,
        attested_by_display: str,
        idempotency_key: str,
        reason: str,
    ) -> dict[str, Any]:
        actor = str(attested_by_principal_id or "").strip()
        reason = str(reason or "").strip()
        if len(reason) < 5:
            raise CurrentAuthorityError(
                "attestation_reason_required",
                "attestation reason must contain at least 5 characters",
                http_status=400,
            )
        key_hash = _idempotency_hash(
            CURRENT_AUTHORITY_ATTEST_SCOPE,
            actor,
            idempotency_key,
        )
        attestation_digest = _digest(
            {
                "scope": CURRENT_AUTHORITY_ATTEST_SCOPE,
                "offline_job_id": offline_job_id,
                "request_digest": str(request_digest or ""),
                "attested_by_principal_id": actor,
                "reason": reason,
            }
        )
        now = _utc(self._now())
        with self._session_scope() as db:
            existing_by_key = (
                db.query(PatrolSourceQualificationRecord)
                .filter(
                    PatrolSourceQualificationRecord.attestation_idempotency_key_hash == key_hash
                )
                .with_for_update()
                .one_or_none()
            )
            if existing_by_key is not None:
                if str(existing_by_key.attestation_request_digest or "") != attestation_digest:
                    raise CurrentAuthorityError(
                        "idempotency_conflict",
                        "Idempotency-Key already belongs to a different attestation",
                    )
                return self._attestation_payload(db, existing_by_key)

            request_row = (
                db.query(PatrolCurrentAuthorityRequestRecord)
                .filter(
                    PatrolCurrentAuthorityRequestRecord.offline_job_id == offline_job_id,
                    PatrolCurrentAuthorityRequestRecord.request_digest == str(request_digest or ""),
                )
                .with_for_update()
                .one_or_none()
            )
            if request_row is None:
                raise CurrentAuthorityError(
                    "authority_request_not_found",
                    "current-authority request not found",
                    http_status=404,
                )
            existing = (
                db.query(PatrolSourceQualificationRecord)
                .filter(
                    PatrolSourceQualificationRecord.request_id == request_row.id,
                    PatrolSourceQualificationRecord.status == "approved",
                )
                .first()
            )
            if existing is not None:
                return self._attestation_payload(db, existing)
            if str(request_row.status) != "ready_for_attestation":
                raise CurrentAuthorityError(
                    "authority_request_not_ready",
                    "current-authority request is not ready for attestation",
                    details={
                        "status": str(request_row.status),
                        "blocker_codes": list(request_row.blocker_codes_json or []),
                    },
                )
            if request_row.expires_at and _utc(request_row.expires_at) <= now:
                request_row.status = "expired"
                request_row.updated_at = now
                raise CurrentAuthorityError(
                    "qualification_expired",
                    "current-authority request has expired",
                )
            if (
                str(request_row.requested_by_principal_id) == actor
                or str(db.get(OfflinePatrolJobRecord, offline_job_id).owner_id) == actor
            ):
                raise CurrentAuthorityError(
                    "same_actor_forbidden",
                    "offline importer cannot attest the same current source",
                )

            job = db.get(OfflinePatrolJobRecord, offline_job_id)
            batch = db.get(PatrolBatchRecord, request_row.candidate_batch_id)
            if job is None or batch is None:
                raise CurrentAuthorityError(
                    "authority_context_missing",
                    "offline job or candidate patrol batch is missing",
                )
            self._revalidate_request(db, request_row, job=job, now=now)
            qualification_id = str(
                uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    "netops/patrol-source-qualification/"
                    f"{request_row.id}/{request_row.request_digest}",
                )
            )
            request_payload = dict(request_row.request_json or {})
            period = dict(request_payload.get("current_period") or {})
            proof = dict(request_payload.get("proof") or {})
            qualification = build_attested_offline_current_qualification(
                qualification_id=qualification_id,
                request_id=str(request_row.id),
                request_digest=str(request_row.request_digest),
                patrol_batch_id=str(batch.patrol_batch_id),
                offline_job_id=offline_job_id,
                version=1,
                observed_at_min=str(period.get("observed_at_min") or period["observed_at"]),
                observed_at_max=str(period.get("observed_at_max") or period["observed_at"]),
                source_file_manifest=list(request_payload.get("source_file_manifest") or []),
                identity_bindings=list(request_payload.get("identity_bindings") or []),
                scope_contract_digest=str(request_row.scope_contract_digest),
                intended_target_matrix_digest=str(request_row.intended_target_matrix_digest),
                completeness_statement=str(proof.get("completeness_statement") or ""),
                completeness_evidence_refs=list(proof.get("completeness_evidence_refs") or []),
                collection_tool=str(proof.get("collection_tool") or ""),
                collector_identity=str(proof.get("collector_identity") or ""),
                imported_by=str(job.owner_id),
                attested_by=actor,
                imported_by_principal_id=str(job.owner_id),
                imported_by_display=str(request_row.requested_by_display),
                attested_by_principal_id=actor,
                attested_by_display=str(attested_by_display or actor),
                attested_at=now,
                version_reason=reason,
            )
            row = PatrolSourceQualificationRecord(
                id=qualification_id,
                request_id=str(request_row.id),
                request_digest=str(request_row.request_digest),
                patrol_batch_id=str(batch.patrol_batch_id),
                offline_job_id=offline_job_id,
                version=1,
                acquisition_mode="offline_upload",
                source_kind=ATTESTED_OFFLINE_CURRENT,
                status="approved",
                observed_at_min=_utc(
                    datetime.fromisoformat(
                        str(period.get("observed_at_min") or period["observed_at"]).replace(
                            "Z", "+00:00"
                        )
                    )
                ),
                observed_at_max=_utc(
                    datetime.fromisoformat(
                        str(period.get("observed_at_max") or period["observed_at"]).replace(
                            "Z", "+00:00"
                        )
                    )
                ),
                chain_of_custody_digest=qualification["chain_of_custody_digest"],
                identity_binding_digest=qualification["identity_binding_digest"],
                scope_contract_digest=qualification["scope_contract_digest"],
                intended_target_matrix_digest=qualification["intended_target_matrix_digest"],
                completeness_evidence_digest=qualification["completeness_evidence_digest"],
                qualification_json=qualification,
                digest=qualification["digest"],
                imported_by=str(job.owner_id),
                attested_by=actor,
                imported_by_principal_id=str(job.owner_id),
                imported_by_display=str(request_row.requested_by_display),
                attested_by_principal_id=actor,
                attested_by_display=str(attested_by_display or actor),
                attestation_idempotency_key_hash=key_hash,
                attestation_request_digest=attestation_digest,
                version_reason=reason,
                attested_at=now,
                created_at=now,
            )
            db.add(row)
            db.flush()
            request_row.status = "approved"
            request_row.approved_qualification_id = qualification_id
            request_row.updated_at = now
            batch.source_qualification_id = qualification_id
            batch.source_qualification_digest = qualification["digest"]
            batch.dispatch_status = "awaiting_current_plan"
            batch.dispatch_requested_at = now
            batch.updated_at = now
            targets = (
                db.query(PatrolBatchDeviceRecord)
                .filter(PatrolBatchDeviceRecord.patrol_batch_id == batch.patrol_batch_id)
                .with_for_update()
                .all()
            )
            for target in targets:
                target.preflight_status = "awaiting_current_plan"
                target.dispatch_status = "awaiting_current_plan"
                target.failure_code = None
                target.error_message = None
                target.updated_at = now
            db.flush()
            return self._attestation_payload(db, row)

    def revoke(
        self,
        *,
        offline_job_id: str,
        revoked_by_principal_id: str,
        revoked_by_display: str,
        idempotency_key: str,
        reason: str,
    ) -> dict[str, Any]:
        actor = str(revoked_by_principal_id or "").strip()
        reason = str(reason or "").strip()
        if len(reason) < 5:
            raise CurrentAuthorityError(
                "revocation_reason_required",
                "revocation reason must contain at least 5 characters",
                http_status=400,
            )
        key_hash = _idempotency_hash(
            CURRENT_AUTHORITY_REVOKE_SCOPE,
            actor,
            idempotency_key,
        )
        revoke_digest = _digest(
            {
                "scope": CURRENT_AUTHORITY_REVOKE_SCOPE,
                "offline_job_id": offline_job_id,
                "actor": actor,
                "reason": reason,
            }
        )
        now = _utc(self._now())
        with self._session_scope() as db:
            existing_by_key = (
                db.query(PatrolSourceQualificationRecord)
                .filter(PatrolSourceQualificationRecord.revocation_idempotency_key_hash == key_hash)
                .with_for_update()
                .one_or_none()
            )
            if existing_by_key is not None:
                if str(existing_by_key.revocation_request_digest or "") != revoke_digest:
                    raise CurrentAuthorityError(
                        "idempotency_conflict",
                        "Idempotency-Key already belongs to a different revocation",
                    )
                return self._attestation_payload(db, existing_by_key)
            request_row = (
                db.query(PatrolCurrentAuthorityRequestRecord)
                .filter(
                    PatrolCurrentAuthorityRequestRecord.offline_job_id == offline_job_id,
                    PatrolCurrentAuthorityRequestRecord.approved_qualification_id.is_not(None),
                )
                .order_by(PatrolCurrentAuthorityRequestRecord.created_at.desc())
                .with_for_update()
                .first()
            )
            if request_row is None:
                raise CurrentAuthorityError(
                    "qualification_not_found",
                    "approved source qualification not found",
                    http_status=404,
                )
            qualification = db.get(
                PatrolSourceQualificationRecord,
                request_row.approved_qualification_id,
            )
            if qualification is None:
                raise CurrentAuthorityError(
                    "qualification_not_found",
                    "approved source qualification not found",
                    http_status=404,
                )
            if str(qualification.status) == "revoked":
                return self._attestation_payload(db, qualification)
            qualification.status = "revoked"
            qualification.revoked_by = actor
            qualification.revoked_by_principal_id = actor
            qualification.revoked_by_display = str(revoked_by_display or actor)
            qualification.revoked_at = now
            qualification.revocation_reason = reason
            qualification.revocation_idempotency_key_hash = key_hash
            qualification.revocation_request_digest = revoke_digest
            request_row.status = "revoked"
            request_row.updated_at = now
            batch = db.get(PatrolBatchRecord, request_row.candidate_batch_id)
            if batch is not None and str(batch.report_status) not in {
                "completed",
            }:
                batch.dispatch_status = "revoked"
                batch.updated_at = now
                for target in (
                    db.query(PatrolBatchDeviceRecord)
                    .filter_by(
                        patrol_batch_id=batch.patrol_batch_id,
                    )
                    .all()
                ):
                    if target.dispatch_status in {
                        "pending",
                        "dispatching",
                        "dispatched",
                        "awaiting_current_plan",
                    }:
                        target.dispatch_status = "cancelled"
                        target.error_message = "当前证据证明已撤销"
                        target.updated_at = now
            db.flush()
            return self._attestation_payload(db, qualification)

    def _build_basis(
        self,
        db: Any,
        *,
        job: OfflinePatrolJobRecord,
        now: datetime,
        completeness_statement: str,
        completeness_evidence_refs: Sequence[str],
        observed_at_evidence_refs: Sequence[str],
        collection_tool: str,
        collector_identity: str,
        scope_contract: Mapping[str, Any] | None,
        requested_by_principal_id: str,
        requested_by_display: str,
    ) -> dict[str, Any]:
        period = (
            db.query(OfflinePatrolPeriodRecord)
            .filter(
                OfflinePatrolPeriodRecord.job_id == job.id,
                OfflinePatrolPeriodRecord.role == "current",
            )
            .one_or_none()
        )
        if period is None:
            raise CurrentAuthorityError(
                "current_period_missing",
                "offline patrol job has no current period",
            )
        period_observed_at = _utc(period.observed_at)
        source_rows = (
            db.query(OfflinePatrolSourceFileRecord)
            .filter(
                OfflinePatrolSourceFileRecord.job_id == job.id,
                OfflinePatrolSourceFileRecord.period_role == "current",
            )
            .order_by(
                OfflinePatrolSourceFileRecord.relative_path,
                OfflinePatrolSourceFileRecord.id,
            )
            .all()
        )
        bindings = (
            db.query(OfflinePatrolDeviceBindingRecord)
            .filter(
                OfflinePatrolDeviceBindingRecord.job_id == job.id,
                OfflinePatrolDeviceBindingRecord.period_role == "current",
            )
            .order_by(
                OfflinePatrolDeviceBindingRecord.external_device_id,
                OfflinePatrolDeviceBindingRecord.id,
            )
            .all()
        )
        source_manifest = [
            {
                "source_id": str(row.id),
                "period_role": str(row.period_role),
                "file_id": str(row.file_id),
                "relative_path": str(row.relative_path),
                "sha256": str(row.sha256 or ""),
                "size_bytes": int(row.size_bytes or 0),
                "status": str(row.status or ""),
            }
            for row in source_rows
        ]
        identity_bindings = [
            {
                "binding_id": str(row.id),
                "external_device_id": str(row.external_device_id),
                "device_name": str(row.device_name or ""),
                "management_ip": str(row.management_ip or ""),
                "inventory_device_id": str(row.inventory_device_id or ""),
                "match_status": str(row.match_status or ""),
                "confirmed_by": str(row.confirmed_by or ""),
                "confirmed_at": (_utc(row.confirmed_at).isoformat() if row.confirmed_at else None),
            }
            for row in bindings
        ]
        blockers: list[str] = []
        if not source_manifest:
            blockers.append("source_manifest_missing")
        elif any(
            item["status"] != "verified" or len(item["sha256"]) != 64 for item in source_manifest
        ):
            blockers.append("source_manifest_unverified")
        identity_binding_ready = bool(identity_bindings) and not any(
            item["match_status"] != "confirmed"
            or not item["inventory_device_id"]
            or not item["confirmed_by"]
            for item in identity_bindings
        )
        if not identity_binding_ready:
            blockers.append("identity_binding_unconfirmed")
        statement = str(completeness_statement or "").strip()
        completeness_refs = sorted(
            {str(item).strip() for item in completeness_evidence_refs if str(item).strip()}
        )
        tool = str(collection_tool or "").strip()
        collector = str(collector_identity or "").strip()
        if len(statement) < 10:
            blockers.append("completeness_statement_missing")
        if not completeness_refs:
            blockers.append("completeness_evidence_missing")
        elif not set(COMPLETENESS_EVIDENCE_OPTIONS).issubset(completeness_refs) or not set(
            completeness_refs
        ).issubset(COMPLETENESS_EVIDENCE_OPTIONS):
            blockers.append("completeness_evidence_incomplete")
        if not tool:
            blockers.append("collection_tool_missing")
        if not collector:
            blockers.append("collector_identity_missing")

        run_id = str(period.patrol_run_id or "")
        patrol_run = db.get(PatrolRun, run_id) if run_id else None
        snapshots = (
            db.query(CommandSnapshot)
            .filter(CommandSnapshot.run_id == run_id)
            .order_by(CommandSnapshot.observed_at, CommandSnapshot.id)
            .all()
            if run_id
            else []
        )
        collection_rows = (
            db.query(PatrolCollectionResult)
            .filter(PatrolCollectionResult.run_id == run_id)
            .order_by(
                PatrolCollectionResult.device_id,
                PatrolCollectionResult.canonical_command,
                PatrolCollectionResult.execution_item_id,
            )
            .all()
            if run_id
            else []
        )
        if not run_id:
            blockers.append("current_run_missing")
        elif patrol_run is None:
            blockers.append("current_run_missing")
        elif not snapshots and not collection_rows:
            blockers.append("current_snapshots_missing")
        snapshot_observed_at = [_utc(snapshot.observed_at) for snapshot in snapshots]
        collection_observed_at = [
            _utc(row.observed_at) for row in collection_rows if row.observed_at is not None
        ]
        if any(row.observed_at is None for row in collection_rows):
            blockers.append("observed_at_untrusted")
        observed_at_values = snapshot_observed_at + collection_observed_at
        observed_at_min = min(observed_at_values) if observed_at_values else period_observed_at
        observed_at_max = max(observed_at_values) if observed_at_values else period_observed_at
        observed_refs = sorted(
            {str(item).strip() for item in observed_at_evidence_refs if str(item).strip()}
        )
        if not observed_refs:
            blockers.append("observed_at_evidence_missing")
        elif "snapshot_observed_at_set" not in observed_refs or not set(observed_refs).issubset(
            OBSERVED_AT_EVIDENCE_OPTIONS
        ):
            blockers.append("observed_at_evidence_invalid")
        max_age = timedelta(
            hours=max(
                1,
                int(settings.PATROL_OFFLINE_CURRENT_MAX_AGE_HOURS),
            )
        )
        if (
            observed_at_min > now + timedelta(minutes=5)
            or now - observed_at_min > max_age
            or period_observed_at < observed_at_min - timedelta(minutes=5)
            or period_observed_at > observed_at_max + timedelta(minutes=5)
        ):
            blockers.append("observed_at_untrusted")
        inventory_ids = sorted(
            {
                item["inventory_device_id"]
                for item in identity_bindings
                if item["inventory_device_id"]
            }
        )
        device_rows = (
            db.query(NetworkDevice)
            .filter(NetworkDevice.id.in_(inventory_ids))
            .order_by(NetworkDevice.id)
            .all()
            if inventory_ids
            else []
        )
        device_by_id = {str(row.id): row for row in device_rows}
        if len(device_by_id) != len(inventory_ids):
            blockers.append("identity_binding_unconfirmed")
        identity_scope_ready = (
            identity_binding_ready
            and bool(inventory_ids)
            and len(device_by_id) == len(inventory_ids)
        )
        devices = []
        for device_id in inventory_ids:
            row = device_by_id.get(device_id)
            if row is None:
                continue
            values = row.to_dict()
            devices.append(
                {
                    **values,
                    "device_id": str(row.id),
                    "attributes": dict(row.attributes_json or {}),
                }
            )

        scope_device_facts = []
        for device in devices:
            attributes = dict(device.get("attributes") or {})
            role = str(
                device.get("device_role")
                or device.get("role")
                or attributes.get("device_role")
                or attributes.get("role")
                or ""
            ).strip()
            capabilities = sorted(
                {
                    str(item).strip()
                    for item in (device.get("capabilities") or attributes.get("capabilities") or [])
                    if str(item).strip()
                }
            )
            scope_device_facts.append(
                {
                    "device_id": str(device.get("device_id") or ""),
                    "vendor": device.get("vendor"),
                    "family": device.get("family"),
                    "platform": device.get("platform"),
                    "model": device.get("model"),
                    "os_version": device.get("os_version"),
                    "role": role or None,
                    "capabilities": capabilities,
                    "attributes": {
                        "role": role or None,
                        "capabilities": capabilities,
                    },
                }
            )

        bindings_by_external = {str(item["external_device_id"]): item for item in identity_bindings}
        bindings_by_ip = {
            str(item["management_ip"]): item for item in identity_bindings if item["management_ip"]
        }
        bindings_by_name = {
            str(item["device_name"]): item for item in identity_bindings if item["device_name"]
        }
        selected_by_device: dict[str, set[str]] = {device_id: set() for device_id in inventory_ids}
        snapshot_commands_by_device: dict[str, list[str]] = {
            device_id: [] for device_id in inventory_ids
        }
        snapshot_manifest: list[dict[str, Any]] = []
        snapshot_content_hash_mismatch = False
        verified_source_hashes = {
            str(item["sha256"])
            for item in source_manifest
            if item["status"] == "verified" and len(str(item["sha256"])) == 64
        }
        for snapshot in snapshots:
            binding = None
            if snapshot.inventory_device_id:
                inventory_id = str(snapshot.inventory_device_id)
            else:
                binding = bindings_by_external.get(str(snapshot.device_id))
                binding = binding or bindings_by_ip.get(str(snapshot.management_ip or ""))
                binding = binding or bindings_by_name.get(str(snapshot.device_name or ""))
                inventory_id = str((binding or {}).get("inventory_device_id") or "")
            check_item_ids = sorted(
                {str(item) for item in snapshot.check_item_ids_json or [] if str(item)}
            )
            if inventory_id in selected_by_device:
                selected_by_device[inventory_id].update(check_item_ids)
                snapshot_commands_by_device[inventory_id].append(
                    _canonical_command(snapshot.canonical_command)
                )
            calculated_raw_text_hash = hashlib.sha256(
                str(snapshot.raw_text or "").encode("utf-8")
            ).hexdigest()
            if calculated_raw_text_hash != str(snapshot.raw_text_hash or ""):
                snapshot_content_hash_mismatch = True
            snapshot_manifest.append(
                {
                    "snapshot_id": str(snapshot.id),
                    "raw_text_hash": str(snapshot.raw_text_hash),
                    "calculated_raw_text_hash": calculated_raw_text_hash,
                    "source_file_hash": str(snapshot.source_file_hash or ""),
                    "inventory_device_id": inventory_id or None,
                    "canonical_command": _canonical_command(snapshot.canonical_command),
                    "check_item_ids": check_item_ids,
                    "observed_at": _utc(snapshot.observed_at).isoformat(),
                }
            )
        if snapshot_content_hash_mismatch:
            blockers.append("snapshot_content_hash_mismatch")
        if snapshot_manifest and any(
            not item["source_file_hash"] or item["source_file_hash"] not in verified_source_hashes
            for item in snapshot_manifest
        ):
            blockers.append("snapshot_source_unlinked")

        bindings_by_inventory = {
            str(item["inventory_device_id"]): item
            for item in identity_bindings
            if item["inventory_device_id"]
        }
        collection_result_manifest: list[dict[str, Any]] = []
        for row in collection_rows:
            inventory_id = str(row.inventory_device_id or "")
            if not inventory_id:
                direct = bindings_by_external.get(str(row.device_id))
                direct = direct or bindings_by_name.get(str(row.device_id))
                inventory_id = str((direct or {}).get("inventory_device_id") or "")
            if inventory_id in snapshot_commands_by_device:
                snapshot_commands_by_device[inventory_id].append(
                    _canonical_command(row.canonical_command)
                )
            collection_result_manifest.append(
                {
                    "collection_result_id": str(row.id),
                    "inventory_device_id": inventory_id or None,
                    "device_id": str(row.device_id),
                    "execution_item_id": str(row.execution_item_id),
                    "collection_action_id": (
                        str(row.collection_action_id) if row.collection_action_id else None
                    ),
                    "canonical_command": _canonical_command(row.canonical_command),
                    "execution_status": str(row.execution_status),
                    "parse_status": str(row.parse_status),
                    "output_status": str(row.output_status),
                    "fact_status": str(row.fact_status),
                    "failure_code": str(row.failure_code or "") or None,
                    "observed_at": (_utc(row.observed_at).isoformat() if row.observed_at else None),
                }
            )

        catalog = CheckItemCatalogRepository(session_scope=self._session_scope).load_active()
        resolver = CheckItemImplementationResolver(catalog)
        device_by_inventory_id = {str(device["device_id"]): device for device in devices}
        mapped_by_device_command: dict[tuple[str, str], set[str]] = {}
        for device_id, commands in snapshot_commands_by_device.items():
            device = device_by_inventory_id.get(device_id)
            if device is None or not commands:
                continue
            projection = resolver.project_legacy_commands(
                device=device,
                commands=commands,
            )
            for action in projection.get("collection_actions") or []:
                canonical_command = _canonical_command(action.get("canonical_command"))
                check_item_ids = {
                    str(item) for item in action.get("check_item_ids") or [] if str(item)
                }
                if not canonical_command or not check_item_ids:
                    continue
                selected_by_device[device_id].update(check_item_ids)
                mapped_by_device_command.setdefault((device_id, canonical_command), set()).update(
                    check_item_ids
                )
        for item in snapshot_manifest:
            inventory_id = str(item.get("inventory_device_id") or "")
            canonical_command = _canonical_command(item.get("canonical_command"))
            inferred = mapped_by_device_command.get((inventory_id, canonical_command), set())
            item["check_item_ids"] = sorted(set(item.get("check_item_ids") or []) | inferred)
        selected = {
            device_id: sorted(values) for device_id, values in selected_by_device.items() if values
        }
        if identity_scope_ready and (not selected or set(selected) != set(inventory_ids)):
            blockers.append("scope_check_items_missing")

        device_accounting_manifest: list[dict[str, Any]] = []
        for inventory_id in inventory_ids:
            device_snapshots = [
                item
                for item in snapshot_manifest
                if item.get("inventory_device_id") == inventory_id
            ]
            device_results = [
                item
                for item in collection_result_manifest
                if item.get("inventory_device_id") == inventory_id
            ]
            failed_result_count = sum(
                str(item.get("execution_status") or "") not in {"complete", "completed"}
                for item in device_results
            )
            if device_snapshots and failed_result_count:
                accounting_status = "partial"
            elif device_snapshots:
                accounting_status = "complete"
            elif device_results:
                accounting_status = "failed"
            else:
                accounting_status = "missing"
            device_accounting_manifest.append(
                {
                    "inventory_device_id": inventory_id,
                    "binding_id": str(
                        bindings_by_inventory.get(inventory_id, {}).get("binding_id") or ""
                    ),
                    "status": accounting_status,
                    "snapshot_count": len(device_snapshots),
                    "collection_result_count": len(device_results),
                    "failed_collection_result_count": failed_result_count,
                }
            )
        if any(item["status"] == "missing" for item in device_accounting_manifest):
            blockers.append("device_result_unaccounted")
        version_ids = {item.id: item.version_id for item in catalog.check_items}
        resolution = None
        try:
            if devices and selected:
                resolution = resolve_patrol_scope(
                    contract=scope_contract,
                    devices=devices,
                    selected_check_item_ids_by_device=selected,
                    check_item_version_ids=version_ids,
                    inventory_snapshot_digest=build_inventory_snapshot(devices)["digest"],
                )
                if not resolution.scope_reconciled:
                    blockers.append("scope_unresolved")
        except ValueError:
            blockers.append("scope_unresolved")
        blockers = sorted(set(blockers))
        empty_digest = _digest({"offline_job_id": str(job.id), "empty": True})
        inventory_snapshot = build_inventory_snapshot(devices)
        target_manifest = {
            "schema_version": "offline-current-target-manifest/v1",
            "offline_job_id": str(job.id),
            "period_role": "current",
            "device_ids": inventory_ids,
            "selected_check_item_ids_by_device": selected,
            "snapshot_ids": [item["snapshot_id"] for item in snapshot_manifest],
            "collection_result_ids": [
                item["collection_result_id"] for item in collection_result_manifest
            ],
        }
        offline_job_result_ledger = {
            "schema_version": "offline-current-result-ledger/v1",
            "job_status": str(job.status),
            "job_result": dict(job.result_json or {}),
            "period_status": str(period.status),
            "period_result": dict(period.result_json or {}),
            "patrol_run": {
                "run_id": str(patrol_run.id) if patrol_run else run_id,
                "status": str(patrol_run.status) if patrol_run else None,
                "collection_status": (str(patrol_run.collection_status) if patrol_run else None),
                "parser_readiness_status": (
                    str(patrol_run.parser_readiness_status) if patrol_run else None
                ),
                "device_count": int(patrol_run.device_count or 0) if patrol_run else 0,
                "command_count": int(patrol_run.command_count or 0) if patrol_run else 0,
            },
            "collection_results": collection_result_manifest,
            "device_accounting": device_accounting_manifest,
        }
        proof = {
            "completeness_statement": statement,
            "completeness_evidence_refs": completeness_refs,
            "observed_at_evidence_refs": observed_refs,
            "collection_tool": tool,
            "collector_identity": collector,
        }
        request_basis = {
            "schema_version": CURRENT_AUTHORITY_REQUEST_SCHEMA,
            "offline_job_id": str(job.id),
            "report_intent": str(job.report_intent),
            "current_period": {
                "period_id": str(period.id),
                "dataset_id": str(period.dataset_id or ""),
                "patrol_run_id": run_id,
                "observed_at": period_observed_at.isoformat(),
                "observed_at_min": observed_at_min.isoformat(),
                "observed_at_max": observed_at_max.isoformat(),
            },
            "source_file_manifest": source_manifest,
            "source_manifest_digest": _digest(source_manifest),
            "identity_bindings": identity_bindings,
            "identity_binding_digest": _digest(identity_bindings),
            "snapshot_manifest": snapshot_manifest,
            "offline_job_result_ledger": offline_job_result_ledger,
            "offline_job_result_ledger_digest": _digest(offline_job_result_ledger),
            "target_manifest": target_manifest,
            "target_manifest_digest": _digest(target_manifest),
            "inventory_snapshot": inventory_snapshot,
            "scope_device_facts": scope_device_facts,
            "selected_check_item_ids_by_device": selected,
            "scope": resolution.to_dict() if resolution else None,
            "proof": proof,
            "requested_by_principal_id": requested_by_principal_id,
            "requested_by_display": str(requested_by_display or requested_by_principal_id),
        }
        return {
            "request_basis": request_basis,
            "blocker_codes": blockers,
            "observed_at_min": observed_at_min,
            "observed_at_max": observed_at_max,
            "source_manifest_digest": request_basis["source_manifest_digest"],
            "observed_at_digest": _digest(
                {
                    "observed_at_min": observed_at_min.isoformat(),
                    "observed_at_max": observed_at_max.isoformat(),
                    "evidence_refs": observed_refs,
                }
            ),
            "identity_binding_digest": request_basis["identity_binding_digest"],
            "scope_contract_version_id": (
                resolution.contract["scope_contract_version_id"] if resolution else None
            ),
            "scope_contract_digest": (resolution.contract_digest if resolution else empty_digest),
            "intended_target_matrix_digest": (
                resolution.intended_target_matrix_digest if resolution else empty_digest
            ),
            "scope_decision_digest": (
                resolution.scope_decision_digest if resolution else empty_digest
            ),
            "completeness_evidence_digest": _digest(proof),
            "resolution": resolution,
            "devices": devices,
            "target_manifest_digest": request_basis["target_manifest_digest"],
            "inventory_snapshot_digest": inventory_snapshot["digest"],
        }

    def _create_candidate_batch(
        self,
        db: Any,
        *,
        request: PatrolCurrentAuthorityRequestRecord,
        job: OfflinePatrolJobRecord,
        basis: Mapping[str, Any],
        now: datetime,
    ) -> None:
        batch_id = "patrol-current-offline-" + str(request.id).replace("-", "")[:24]
        batch = PatrolBatchRecord(
            patrol_batch_id=batch_id,
            workflow_name="patrol-attested-offline-current",
            launch_mode="direct",
            patrol_mode="production",
            report_purpose="production_current",
            target_type="offline_import",
            target_label=f"离线当前证明：{job.name}",
            selector_json={
                "offline_job_id": str(job.id),
                "period_role": "current",
                "authority_request_id": str(request.id),
                "inspection": {"user_id": str(request.requested_by_principal_id)},
            },
            target_count=len(basis["devices"]),
            launched_count=0,
            skipped_count=0,
            target_manifest_digest=str(basis["target_manifest_digest"]),
            inventory_snapshot_digest=str(basis["inventory_snapshot_digest"]),
            source_offline_job_id=str(job.id),
            source_authority_request_id=str(request.id),
            dispatch_status="awaiting_source_qualification",
            dispatch_requested_at=None,
            created_at=now,
            updated_at=now,
        )
        db.add(batch)
        db.flush()
        request.candidate_batch_id = batch_id
        request.updated_at = now
        for device in basis["devices"]:
            db.add(
                PatrolBatchDeviceRecord(
                    id=str(uuid.uuid4()),
                    patrol_batch_id=batch_id,
                    device_id=str(device["device_id"]),
                    device_name=str(device.get("device_name") or ""),
                    management_ip=str(device.get("management_ip") or ""),
                    selection_status="selected",
                    preflight_status="awaiting_source_qualification",
                    failure_code=None,
                    error_message=None,
                    dispatch_status="awaiting_source_qualification",
                    dispatch_attempts=0,
                    created_at=now,
                    updated_at=now,
                )
            )
        PatrolScopeRepository(self._session_scope).persist_in_session(
            db,
            batch=batch,
            resolution=basis["resolution"],
            inventory_snapshot_digest=str(basis["inventory_snapshot_digest"]),
            actor=str(request.requested_by_principal_id),
            created_at=now,
        )

    def _revalidate_request(
        self,
        db: Any,
        request: PatrolCurrentAuthorityRequestRecord,
        *,
        job: OfflinePatrolJobRecord,
        now: datetime,
    ) -> None:
        frozen = dict(request.request_json or {})
        proof = dict(frozen.get("proof") or {})
        scope = dict(frozen.get("scope") or {})
        basis = self._build_basis(
            db,
            job=job,
            now=now,
            completeness_statement=str(proof.get("completeness_statement") or ""),
            completeness_evidence_refs=list(proof.get("completeness_evidence_refs") or []),
            observed_at_evidence_refs=list(proof.get("observed_at_evidence_refs") or []),
            collection_tool=str(proof.get("collection_tool") or ""),
            collector_identity=str(proof.get("collector_identity") or ""),
            scope_contract=dict(scope.get("scope_contract") or {}) or None,
            requested_by_principal_id=str(request.requested_by_principal_id),
            requested_by_display=str(request.requested_by_display),
        )
        if basis["blocker_codes"]:
            raise CurrentAuthorityError(
                str(basis["blocker_codes"][0]),
                _BLOCKER_MESSAGES.get(
                    str(basis["blocker_codes"][0]),
                    "current-authority evidence is no longer valid",
                ),
                details={"blocker_codes": basis["blocker_codes"]},
            )
        if _digest(basis["request_basis"]) != str(request.request_digest):
            raise CurrentAuthorityError(
                "authority_digest_mismatch",
                "current-authority evidence changed after preparation",
            )
        batch = db.get(PatrolBatchRecord, request.candidate_batch_id)
        if (
            batch is None
            or str(batch.scope_contract_digest) != str(request.scope_contract_digest)
            or str(batch.intended_target_matrix_digest)
            != str(request.intended_target_matrix_digest)
            or str(batch.scope_decision_digest) != str(request.scope_decision_digest)
        ):
            raise CurrentAuthorityError(
                "authority_digest_mismatch",
                "candidate patrol scope no longer matches the request",
            )

    @staticmethod
    def _serialize(
        db: Any,
        row: PatrolCurrentAuthorityRequestRecord,
    ) -> dict[str, Any]:
        qualification = (
            db.get(
                PatrolSourceQualificationRecord,
                row.approved_qualification_id,
            )
            if row.approved_qualification_id
            else None
        )
        payload = dict(row.request_json or {})
        scope = dict(payload.get("scope") or {})
        batch = (
            db.get(PatrolBatchRecord, row.candidate_batch_id) if row.candidate_batch_id else None
        )
        targets = (
            db.query(PatrolBatchDeviceRecord)
            .filter(PatrolBatchDeviceRecord.patrol_batch_id == row.candidate_batch_id)
            .all()
            if row.candidate_batch_id
            else []
        )
        workflow_statuses: dict[str, int] = {}
        workflow_ids = [str(item.workflow_run_id) for item in targets if item.workflow_run_id]
        if workflow_ids:
            for status_value, count in (
                db.query(WorkflowRun.status, func.count())
                .filter(WorkflowRun.id.in_(workflow_ids))
                .group_by(WorkflowRun.status)
                .all()
            ):
                workflow_statuses[str(status_value)] = int(count)
        from src.apps.inspection.backend.online_reads import states_for_targets

        execution_statuses = {}
        if batch and batch.launch_mode == "direct":
            for state in states_for_targets(db, targets).values():
                execution_statuses[state["status"]] = execution_statuses.get(state["status"], 0) + 1
        return {
            "schema_version": CURRENT_AUTHORITY_REQUEST_SCHEMA,
            "request_id": str(row.id),
            "offline_job_id": str(row.offline_job_id),
            "candidate_batch_id": (str(row.candidate_batch_id) if row.candidate_batch_id else None),
            "status": str(row.status),
            "blocker_codes": list(row.blocker_codes_json or []),
            "blockers": [
                {
                    "code": str(code),
                    "message": _BLOCKER_MESSAGES.get(str(code), str(code)),
                    "remediation": dict(_BLOCKER_REMEDIATIONS.get(str(code), {})),
                }
                for code in row.blocker_codes_json or []
            ],
            "request_digest": str(row.request_digest),
            "source_manifest_digest": str(row.source_manifest_digest),
            "identity_binding_digest": str(row.identity_binding_digest),
            "scope_contract_digest": str(row.scope_contract_digest),
            "intended_target_matrix_digest": str(row.intended_target_matrix_digest),
            "scope_decision_digest": str(row.scope_decision_digest),
            "scope_reconciled": bool(scope.get("scope_reconciled")),
            "source_file_count": len(payload.get("source_file_manifest") or []),
            "device_count": len(payload.get("identity_bindings") or []),
            "selected_check_item_count": len(
                {
                    item
                    for values in dict(
                        payload.get("selected_check_item_ids_by_device") or {}
                    ).values()
                    for item in values or []
                }
            ),
            "current_period": dict(payload.get("current_period") or {}),
            "proof": dict(payload.get("proof") or {}),
            "proof_options": {
                "observed_at_evidence_refs": list(OBSERVED_AT_EVIDENCE_OPTIONS),
                "completeness_evidence_refs": list(COMPLETENESS_EVIDENCE_OPTIONS),
            },
            "requested_by_principal_id": str(row.requested_by_principal_id),
            "requested_by_display": str(row.requested_by_display),
            "prepared_at": row.prepared_at,
            "expires_at": row.expires_at,
            "qualification": (
                {
                    "id": str(qualification.id),
                    "status": str(qualification.status),
                    "digest": str(qualification.digest),
                    "attested_by_principal_id": str(
                        qualification.attested_by_principal_id or qualification.attested_by
                    ),
                    "attested_by_display": str(
                        qualification.attested_by_display or qualification.attested_by
                    ),
                    "attested_at": qualification.attested_at,
                    "revoked_at": qualification.revoked_at,
                    "revocation_reason": qualification.revocation_reason,
                }
                if qualification is not None
                else None
            ),
            "production_batch": (
                {
                    "patrol_batch_id": str(batch.patrol_batch_id),
                    "dispatch_status": str(batch.dispatch_status),
                    "dispatch_error": batch.dispatch_error,
                    "dispatch_error_code": (
                        str(batch.dispatch_error).partition(":")[0]
                        if batch.dispatch_error
                        else None
                    ),
                    "report_status": str(batch.report_status),
                    "report_error": batch.report_error,
                    "runtime_manifest_id": batch.runtime_manifest_id,
                    "runtime_manifest_digest": (batch.runtime_manifest_digest),
                    "target_count": int(batch.target_count or 0),
                    "launched_count": int(batch.launched_count or 0),
                    "skipped_count": int(batch.skipped_count or 0),
                    "workflow_status_counts": workflow_statuses,
                    "execution_status_counts": execution_statuses,
                }
                if batch is not None
                else None
            ),
        }

    @staticmethod
    def _attestation_payload(
        db: Any,
        row: PatrolSourceQualificationRecord,
    ) -> dict[str, Any]:
        request = db.get(PatrolCurrentAuthorityRequestRecord, row.request_id)
        return {
            "success": str(row.status) == "approved",
            "request_id": str(row.request_id or ""),
            "request_digest": str(row.request_digest or ""),
            "qualification_id": str(row.id),
            "qualification_digest": str(row.digest),
            "candidate_batch_id": str(row.patrol_batch_id),
            "status": str(row.status),
            "attested_by_principal_id": str(row.attested_by_principal_id or row.attested_by),
            "attested_by_display": str(row.attested_by_display or row.attested_by),
            "attested_at": row.attested_at,
            "revoked_at": row.revoked_at,
            "revocation_reason": row.revocation_reason,
            "authority_request_status": (str(request.status) if request is not None else None),
        }


__all__ = [
    "COMPLETENESS_EVIDENCE_OPTIONS",
    "CURRENT_AUTHORITY_REQUEST_SCHEMA",
    "CurrentAuthorityError",
    "OBSERVED_AT_EVIDENCE_OPTIONS",
    "PatrolCurrentAuthorityService",
]
