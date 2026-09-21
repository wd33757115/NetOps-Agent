"""Durable revision requests in the existing governance control-state store.

Running requests are never automatically replayed: a crashed persister may have
committed assets. Uncertain requests remain part of the drain count until resolved.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from billiard.exceptions import SoftTimeLimitExceeded
from sqlalchemy.exc import IntegrityError, OperationalError

from src.apps.rule_governance.backend.identity import actor_user_id as normalize_actor_user_id
from src.infrastructure.db.models import ParserControlSetting, RuleGenerationJobRecord
from src.infrastructure.db.postgres import get_db_session

PREFIX = "engineering-revision-job:"
TASK_NAME = "netops.rule_governance.revise"
SOFT_LIMIT, HARD_LIMIT = 1740, 1800
UNSETTLED = {"queued", "running", "needs_attention"}


def _now():
    return datetime.now(timezone.utc)


def _digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()


def _key(task_id):
    if not isinstance(task_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", task_id):
        raise KeyError("revision task not found")
    return PREFIX + task_id


class RevisionRequestConflictError(ValueError):
    pass


class RevisionExecutionStoppedError(RuntimeError):
    pass


class EngineeringRevisionJobs:
    def __init__(self, *, session_scope=None, dispatch=None, clock=None):
        self.session_scope = session_scope or get_db_session
        self.send = dispatch
        self.clock = clock or _now

    @staticmethod
    def public(state):
        status = state["status"]
        return {
            "task_id": state["task_id"],
            "bundle_id": state["bundle_id"],
            "status": {
                "queued": "PENDING",
                "running": "STARTED",
                "succeeded": "SUCCESS",
                "resolved": "RESOLVED",
            }.get(status, "FAILURE"),
            "state": status,
            "ready": status not in {"queued", "running"},
            "successful": status == "succeeded",
            "phase": state.get("phase"),
            "created_at": state["created_at"],
            "updated_at": state["updated_at"],
            "error": state.get("error"),
            "validation": state.get("validation"),
            "dispatch_error": state.get("dispatch_error"),
            "result": state.get("result"),
            "resolution": state.get("resolution"),
        }

    def submit(
        self,
        bundle_id,
        *,
        contract,
        change_policies,
        event_rules,
        actor,
        reason,
        idempotency_key,
        actor_user_id,
        legacy_task_id=None,
        dispatch=True,
    ):
        owner = normalize_actor_user_id(actor_user_id)
        if owner is None and legacy_task_id is None:
            raise ValueError("authenticated actor user ID is required")
        if not contract or not isinstance(contract, dict):
            raise ValueError("engineering revision requires one ObservationContract")
        if not all(
            isinstance(values, list) and all(isinstance(x, dict) for x in values)
            for values in (change_policies, event_rules)
        ):
            raise ValueError("revision policies and rules must be lists of objects")
        if (
            not isinstance(reason, str)
            or not isinstance(idempotency_key, str)
            or len(reason.strip()) < 5
            or not 8 <= len(idempotency_key.strip()) <= 128
        ):
            raise ValueError("revision reason or idempotency key is invalid")
        inputs = {
            "contract": contract,
            "change_policies": change_policies,
            "event_rules": event_rules,
            "reason": reason.strip(),
            "idempotency_key": idempotency_key.strip(),
        }
        task_id = legacy_task_id or _digest([owner, bundle_id, inputs["idempotency_key"]])[:32]
        key, fingerprint = _key(task_id), _digest([bundle_id, inputs])
        now = self.clock().isoformat()

        def existing(db):
            row = db.get(ParserControlSetting, key)
            if row is not None and (
                row.value_json.get("input_digest") != fingerprint
                or row.value_json.get("owner_id") != owner
            ):
                raise RevisionRequestConflictError("同一请求标识已用于不同的修订内容")
            return row

        try:
            with self.session_scope() as db:
                row = existing(db)
                if row is None:
                    parent = db.get(RuleGenerationJobRecord, bundle_id)
                    if parent is None or parent.job_type != "RULE_ASSET_BUNDLE":
                        raise KeyError("rule asset bundle not found")
                    if legacy_task_id is None and parent.status not in {
                        "pending_approval",
                        "rejected",
                    }:
                        raise ValueError("only pending or rejected bundles can be revised")
                    row = ParserControlSetting(
                        key=key,
                        value_json={
                            "task_id": task_id,
                            "bundle_id": bundle_id,
                            "owner_id": owner,
                            "actor": actor,
                            "input_digest": fingerprint,
                            "inputs": inputs,
                            "status": "queued",
                            "business_key": inputs["idempotency_key"]
                            if legacy_task_id
                            else task_id,
                            "created_at": now,
                            "updated_at": now,
                            "phase": "queued",
                        },
                    )
                    db.add(row)
                    db.flush()
                state = copy.deepcopy(row.value_json)
        except IntegrityError:
            # Concurrent identical submissions converge on the primary-key winner.
            with self.session_scope() as db:
                row = existing(db)
                if row is None:
                    raise
                state = copy.deepcopy(row.value_json)
        if dispatch and state["status"] == "queued":
            self.dispatch(task_id)
        return {
            "success": True,
            "async": True,
            **self.read(task_id, user_id=owner, is_admin=legacy_task_id is not None),
        }

    def dispatch(self, task_id):
        with self.session_scope() as db:
            row = (
                db.query(ParserControlSetting)
                .filter_by(key=_key(task_id))
                .with_for_update()
                .first()
            )
            if row is None or row.value_json["status"] != "queued":
                return False
            state = dict(row.value_json)
            now = self.clock()
            if state.get("last_dispatch_at") and now - datetime.fromisoformat(
                state["last_dispatch_at"]
            ) < timedelta(seconds=30):
                return False
            state.update(last_dispatch_at=now.isoformat(), updated_at=now.isoformat())
            row.value_json = state
        try:
            if self.send is None:
                from src.core.celery_tasks.celery_app import celery

                send = celery.send_task
            else:
                send = self.send
            send(
                TASK_NAME,
                kwargs={"task_id": task_id},
                task_id=task_id,
                queue="app.rule-governance",
                soft_time_limit=SOFT_LIMIT,
                time_limit=HARD_LIMIT,
            )
        except Exception:
            with self.session_scope() as db:
                row = (
                    db.query(ParserControlSetting)
                    .filter_by(key=_key(task_id))
                    .with_for_update()
                    .first()
                )
                if row and row.value_json["status"] == "queued":
                    row.value_json = {
                        **row.value_json,
                        "dispatch_error": "请求已保存，等待后台队列恢复",
                    }
            return False
        return True

    def read(self, task_id, *, user_id, is_admin=False):
        with self.session_scope() as db:
            row = db.get(ParserControlSetting, _key(task_id))
            if row is None or (
                not is_admin and (not user_id or row.value_json["owner_id"] != user_id)
            ):
                raise KeyError("revision task not found")
            return self.public(row.value_json)

    def list_for_bundle(self, bundle_id, *, user_id, is_admin=False):
        if not user_id and not is_admin:
            return {"items": []}
        with self.session_scope() as db:
            query = db.query(ParserControlSetting).filter(
                ParserControlSetting.key.startswith(PREFIX),
                ParserControlSetting.value_json["bundle_id"].as_string() == bundle_id,
            )
            if not is_admin:
                query = query.filter(
                    ParserControlSetting.value_json["owner_id"].as_string() == user_id
                )
            rows = (
                query.order_by(
                    ParserControlSetting.value_json["status"].as_string().in_(UNSETTLED).desc(),
                    ParserControlSetting.value_json["created_at"].as_string().desc(),
                )
                .limit(20)
                .all()
            )
            return {"items": [self.public(row.value_json) for row in rows]}

    def _claim(self, task_id):
        with self.session_scope() as db:
            row = (
                db.query(ParserControlSetting)
                .filter_by(key=_key(task_id))
                .with_for_update()
                .first()
            )
            if row is None or row.value_json["status"] != "queued":
                return None
            now = self.clock()
            state = {
                **row.value_json,
                "status": "running",
                "phase": "preparing",
                "lease_token": uuid.uuid4().hex,
                "lease_until": (now + timedelta(seconds=HARD_LIMIT + 60)).isoformat(),
                "updated_at": now.isoformat(),
                "dispatch_error": None,
                "write_fencing_version": 1,
            }
            row.value_json = state
            return copy.deepcopy(state)

    @contextmanager
    def write_guard(self, task_id, token, *, source_ref=None):
        """Serialize all revision writes with resolution, including child processes."""
        with self.session_scope() as db:
            row = (
                db.query(ParserControlSetting)
                .filter_by(key=_key(task_id))
                .with_for_update()
                .first()
            )
            state = row.value_json if row else {}
            if (
                state.get("status") != "running"
                or state.get("lease_token") != token
                or state.get("write_fencing_version") != 1
                or datetime.fromisoformat(state["lease_until"]) <= self.clock()
            ):
                raise RevisionExecutionStoppedError("修订写入已停止，不能继续保存资产")
            from src.apps.rule_governance.backend.access import authorize
            authorize(state["owner_id"])
            expected_source = f"engineering_revision:{state['bundle_id']}:{state['business_key']}"
            if source_ref is not None and source_ref != expected_source:
                raise RevisionExecutionStoppedError("修订资产来源与任务不匹配")
            yield

    @contextmanager
    def _review_lock(self, task_id, *, user_id, is_admin):
        try:
            with self.session_scope() as db:
                row = db.get(ParserControlSetting, _key(task_id))
                if row is None or (
                    not is_admin and (not user_id or row.value_json["owner_id"] != user_id)
                ):
                    raise KeyError("revision task not found")
                row = (
                    db.query(ParserControlSetting)
                    .filter_by(key=_key(task_id))
                    .populate_existing()
                    .with_for_update(nowait=True)
                    .first()
                )
                yield db, row
        except OperationalError as exc:
            if (
                getattr(exc.orig, "pgcode", None) == "55P03"
                or getattr(exc.orig, "sqlstate", None) == "55P03"
            ):
                raise RevisionRequestConflictError("资产仍在写入，请稍后重新核查") from exc
            raise

    @staticmethod
    def _evidence(db, state):
        source = f"engineering_revision:{state['bundle_id']}:{state['business_key']}"
        bundles = (
            db.query(RuleGenerationJobRecord)
            .filter_by(job_type="RULE_ASSET_BUNDLE", source_ref=source)
            .with_for_update(nowait=True)
            .all()
        )
        records = list(bundles)
        if bundles:
            # Source linkage also finds children committed before child_job_ids was saved.
            records.extend(
                db.query(RuleGenerationJobRecord)
                .filter(
                    RuleGenerationJobRecord.source_ref.in_(
                        [f"rule_asset_bundle:{b.id}" for b in bundles]
                    ),
                )
                .with_for_update(nowait=True)
                .all()
            )
        summaries = [
            {
                "id": r.id,
                "job_type": r.job_type,
                "status": r.status,
                "asset_ref": r.asset_ref,
                "asset_digest": r.asset_digest,
                "validation": r.validation_report_json,
                "record_digest": _digest(
                    [r.payload_json, r.validation_report_json, r.asset_ref, r.asset_digest]
                ),
            }
            for r in sorted(records, key=lambda r: r.id)
        ]
        return {"records": summaries, "digest": _digest(summaries)}

    def inspection(self, task_id, *, user_id, is_admin=False):
        with self._review_lock(task_id, user_id=user_id, is_admin=is_admin) as (db, row):
            state = row.value_json
            evidence = self._evidence(db, state)
            supported = state.get("write_fencing_version") == 1
            return {
                **evidence,
                "task_id": task_id,
                "state": state["status"],
                "can_resolve": supported and state["status"] == "needs_attention",
                "blocked_reason": None
                if supported
                else "历史执行未使用写入互斥保护，需先完成旧 Worker 排空核验",
                "resolution": state.get("resolution"),
            }

    def resolve(self, task_id, *, user_id, actor, evidence_digest, note, is_admin=False):
        resolver = normalize_actor_user_id(user_id)
        if not resolver or not isinstance(note, str) or not 5 <= len(note.strip()) <= 2000:
            raise ValueError("核查说明需为 5–2000 个字符，并记录真实处理人")
        with self._review_lock(task_id, user_id=resolver, is_admin=is_admin) as (db, row):
            state = row.value_json
            previous = state.get("resolution") or {}
            if state["status"] == "resolved":
                if (
                    previous.get("actor_user_id"),
                    previous.get("note"),
                    previous.get("evidence_digest"),
                ) == (resolver, note.strip(), evidence_digest):
                    return self.public(state)
                raise RevisionRequestConflictError("该任务已结案，请刷新核查记录")
            if state["status"] != "needs_attention" or state.get("write_fencing_version") != 1:
                raise RevisionRequestConflictError("仅支持对已停止写入的待核查任务结案")
            evidence = self._evidence(db, state)
            if evidence_digest != evidence["digest"]:
                raise RevisionRequestConflictError("资产状态已变化，请刷新核查结果后重新确认")
            now = self.clock().isoformat()
            state = {
                **state,
                "status": "resolved",
                "lease_token": None,
                "updated_at": now,
                "resolution": {
                    "actor_user_id": resolver,
                    "actor": actor,
                    "note": note.strip(),
                    "reviewed_at": now,
                    "evidence_digest": evidence["digest"],
                    "records": evidence["records"],
                    "outcome": "reviewed",
                },
            }
            row.value_json = state
            return self.public(state)

    def checkpoint(self, task_id, token, phase):
        with self.session_scope() as db:
            row = (
                db.query(ParserControlSetting)
                .filter_by(key=_key(task_id))
                .with_for_update()
                .first()
            )
            state = row.value_json if row else {}
            if (
                state.get("status") != "running"
                or state.get("lease_token") != token
                or datetime.fromisoformat(state["lease_until"]) <= self.clock()
            ):
                raise RevisionExecutionStoppedError("修订任务执行已中断，请核查已生成的资产")
            row.value_json = {**state, "phase": phase, "updated_at": self.clock().isoformat()}

    def _finish(self, task_id, token, **result):
        with self.session_scope() as db:
            row = (
                db.query(ParserControlSetting)
                .filter_by(key=_key(task_id))
                .with_for_update()
                .first()
            )
            if (
                row is None
                or row.value_json.get("status") != "running"
                or row.value_json.get("lease_token") != token
            ):
                return False
            if datetime.fromisoformat(row.value_json["lease_until"]) <= self.clock():
                result = {
                    "status": "needs_attention",
                    "error": "执行期限已过，需核查修订结果后处理",
                }
            row.value_json = {
                **row.value_json,
                **result,
                "lease_token": None,
                "updated_at": self.clock().isoformat(),
            }
            return True

    def run(self, task_id):
        from src.apps.rule_governance.backend.rule_control.engineering_revision import (
            RuleAssetEngineeringRevisionError,
            RuleAssetEngineeringRevisionService,
        )

        state = self._claim(task_id)
        if state is None:
            return {"success": False, "ignored": True}
        token = state["lease_token"]
        phase = "preparing"

        def checkpoint(next_phase):
            from src.apps.rule_governance.backend.access import authorize
            authorize(state["owner_id"])
            nonlocal phase
            self.checkpoint(task_id, token, next_phase)
            phase = next_phase

        try:
            from src.apps.rule_governance.backend.access import authorize
            authorize(state["owner_id"])
            inputs = {**state["inputs"], "idempotency_key": state["business_key"]}
            result = RuleAssetEngineeringRevisionService().revise(
                state["bundle_id"],
                **inputs,
                actor=state["actor"],
                actor_user_id=state["owner_id"],
                checkpoint=checkpoint,
                revision_execution={"task_id": task_id, "lease_token": token},
                write_guard=lambda: self.write_guard(task_id, token),
            )
            success = bool(result.get("success"))
            envelope = {
                "success": success,
                "task_id": task_id,
                "bundle_id": state["bundle_id"],
                "result": result,
            }
            self._finish(
                task_id,
                token,
                status="succeeded" if success else "needs_attention",
                result=envelope,
                error=None if success else "已有修订结果需要核查",
            )
        except RuleAssetEngineeringRevisionError as exc:
            self._finish(
                task_id,
                token,
                status="needs_attention" if phase in {"persisting", "recording"} else "failed",
                error=str(exc),
                validation=exc.validation,
            )
        except (ValueError, KeyError) as exc:
            self._finish(
                task_id,
                token,
                status="needs_attention" if phase in {"persisting", "recording"} else "failed",
                error=str(exc),
            )
        except Exception as exc:
            self._finish(task_id, token, status="needs_attention", error=str(exc)[:2000])
            if isinstance(exc, SoftTimeLimitExceeded):
                raise
        return self.read(task_id, user_id=state["owner_id"], is_admin=True)

    def reconcile(self):
        with self.session_scope() as db:
            rows = (
                db.query(ParserControlSetting)
                .filter(
                    ParserControlSetting.key.startswith(PREFIX),
                    ParserControlSetting.value_json["status"]
                    .as_string()
                    .in_(["queued", "running"]),
                )
                .with_for_update(skip_locked=True)
                .all()
            )
            queued = []
            expired = 0
            for row in rows:
                state = row.value_json
                if state["status"] == "queued":
                    queued.append(state["task_id"])
                elif datetime.fromisoformat(state["lease_until"]) <= self.clock():
                    row.value_json = {
                        **state,
                        "status": "needs_attention",
                        "lease_token": None,
                        "error": "Worker 未在执行期限内完成，请核查已生成的资产",
                        "updated_at": self.clock().isoformat(),
                    }
                    expired += 1
        return {
            "dispatched": sum(self.dispatch(task) for task in queued),
            "needs_attention": expired,
        }

    def pending_count(self):
        with self.session_scope() as db:
            return (
                db.query(ParserControlSetting)
                .filter(
                    ParserControlSetting.key.startswith(PREFIX),
                    ParserControlSetting.value_json["status"].as_string().in_(UNSETTLED),
                )
                .count()
            )
