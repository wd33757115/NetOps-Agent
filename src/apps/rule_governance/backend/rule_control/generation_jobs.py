"""Durable fixed rule generation, stored alongside existing control requests.

Only queued deliveries are replayed. Interrupted executions require inspection;
the persister holds the same row lock, so closing a run cannot race asset writes.
"""

import copy
import hashlib
import json
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from src.apps.rule_governance.backend.access import authorize
from src.apps.rule_governance.backend.generators.runner import execute_generator
from src.infrastructure.db.models import ParserControlSetting, RuleGenerationJobRecord
from src.infrastructure.db.postgres import get_db_session

PREFIX = "rule-generation-run:"
TASK = "netops.rule_governance.generate"
ACTIVE = {"queued", "running", "needs_attention"}


def now():
    return datetime.now(timezone.utc)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


class GenerationJobs:
    def latest(self, seed_id, *, user_id, is_admin=False):
        with get_db_session() as db:
            seed = db.get(RuleGenerationJobRecord, seed_id)
            run_id = (seed.payload_json or {}).get("latest_generation_run_id") if seed else None
        if not run_id:
            return None
        try:
            return self.read(run_id, user_id=user_id, is_admin=is_admin)
        except KeyError:
            return None

    def read(self, run_id, *, user_id=None, is_admin=False):
        with get_db_session() as db:
            row = db.get(ParserControlSetting, PREFIX + run_id)
            if row is None or (
                user_id is not None and not is_admin and row.value_json["owner_id"] != user_id
            ):
                raise KeyError("规则生成任务不存在")
            result = copy.deepcopy(row.value_json)
            result["assets"] = [
                {"id": item.id, "status": item.status, "validation": item.validation_report_json}
                for item in db.query(RuleGenerationJobRecord)
                .filter(
                    RuleGenerationJobRecord.payload_json["generation_run_id"].as_string() == run_id
                )
                .all()
            ]
            return result

    def submit(self, seed_id, context, *, user_id):
        authorize(user_id)
        signature = digest([user_id, context])
        with get_db_session() as db:
            seed = db.query(RuleGenerationJobRecord).filter_by(id=seed_id).with_for_update().one()
            payload = dict(seed.payload_json or {})
            previous_id = payload.get("latest_generation_run_id")
            previous = db.get(ParserControlSetting, PREFIX + previous_id) if previous_id else None
            if previous:
                state = previous.value_json
                if state["signature"] == signature and state["status"] not in {
                    "failed",
                    "cancelled",
                    "resolved",
                }:
                    return state["run_id"]
                if state["status"] in ACTIVE:
                    raise ValueError("已有生成任务运行中或等待核查")
            run_id = uuid.uuid4().hex
            state = {
                "run_id": run_id,
                "seed_id": seed_id,
                "owner_id": user_id,
                "context": context,
                "signature": signature,
                "status": "queued",
                "steps": {},
                "created_at": now().isoformat(),
                "updated_at": now().isoformat(),
            }
            db.add(ParserControlSetting(key=PREFIX + run_id, value_json=state))
            history = [
                dict(item)
                for item in payload.get("generation_workflow_runs", [])
                if isinstance(item, dict)
            ]
            history.append(
                {
                    "run_id": run_id,
                    "context_id": context.get("rule_generation_context_id"),
                    "context_digest": context.get("rule_generation_context_digest"),
                    "execution": dict(context.get("rule_generation_identity") or {}),
                }
            )
            seed.payload_json = {
                **payload,
                "generation_workflow_runs": history[-20:],
                "latest_generation_run_id": run_id,
                "latest_generation_context_id": context.get("rule_generation_context_id"),
            }
        self.dispatch(run_id)
        return run_id

    def dispatch(self, run_id):
        from src.core.celery_tasks.celery_app import celery

        try:
            celery.send_task(TASK, args=[run_id], task_id=run_id, queue="app.rule-governance")
        except Exception:
            # The durable queued record is recovered by the package scheduler.
            return False
        return True

    @contextmanager
    def write_guard(self, run_id, token):
        with get_db_session() as db:
            row = (
                db.query(ParserControlSetting)
                .filter_by(key=PREFIX + run_id)
                .with_for_update()
                .one()
            )
            state = row.value_json
            if (
                state["status"] != "running"
                or state.get("token") != token
                or datetime.fromisoformat(state["lease_until"]) <= now()
            ):
                raise ValueError("规则生成任务已停止")
            authorize(state["owner_id"])
            yield row

    def run(self, run_id):
        from src.apps.rule_governance.backend.parser.worker import (
            _refresh_rule_asset_projection_from_skill_result,
        )

        from .generation import generate

        with get_db_session() as db:
            row = (
                db.query(ParserControlSetting)
                .filter_by(key=PREFIX + run_id)
                .with_for_update()
                .one_or_none()
            )
            if row is None or row.value_json["status"] != "queued":
                return {"success": False, "ignored": True}
            state = copy.deepcopy(row.value_json)
            token = uuid.uuid4().hex
            row.value_json = {
                **state,
                "status": "running",
                "token": token,
                "lease_until": (now() + timedelta(hours=2)).isoformat(),
            }
        phase = "preparing"

        def step(name, generator, params):
            nonlocal phase
            with self.write_guard(run_id, token) as row:
                phase = name
                row.value_json = {**row.value_json, "phase": name, "updated_at": now().isoformat()}
            if name == "persist_assets":
                params["generation_execution"] = {"run_id": run_id, "token": token}
            result = execute_generator(generator, params)
            if not result.get("success"):
                raise ValueError(result.get("error") or result.get("message") or f"{name} failed")
            with self.write_guard(run_id, token) as row:
                row.value_json = {
                    **row.value_json,
                    "steps": {**row.value_json["steps"], name: result},
                    "updated_at": now().isoformat(),
                }
            return result

        try:
            result = generate(state["context"], step)
            with self.write_guard(run_id, token) as row:
                row.value_json = {
                    **row.value_json,
                    "status": "completed",
                    "result": result,
                    "updated_at": now().isoformat(),
                }
            _refresh_rule_asset_projection_from_skill_result("rule-asset-persister", result)
        except Exception as exc:
            with get_db_session() as db:
                row = (
                    db.query(ParserControlSetting)
                    .filter_by(key=PREFIX + run_id)
                    .with_for_update()
                    .one()
                )
                if row.value_json.get("token") == token and row.value_json["status"] == "running":
                    row.value_json = {
                        **row.value_json,
                        "status": "needs_attention" if phase == "persist_assets" else "failed",
                        "error": str(exc),
                        "updated_at": now().isoformat(),
                    }
        return self.read(run_id)

    def reconcile(self):
        queued, expired = [], []
        with get_db_session() as db:
            rows = (
                db.query(ParserControlSetting)
                .filter(ParserControlSetting.key.startswith(PREFIX))
                .with_for_update(skip_locked=True)
                .all()
            )
            for row in rows:
                state = row.value_json
                if state["status"] == "queued":
                    queued.append(state["run_id"])
                elif (
                    state["status"] == "running"
                    and datetime.fromisoformat(state["lease_until"]) <= now()
                ):
                    row.value_json = {
                        **state,
                        "status": "needs_attention",
                        "token": None,
                        "error": "执行中断，请核查产物；不会自动重复生成",
                    }
                    expired.append({"run_id": state["run_id"], "next_action": "human_review"})
        for run_id in queued:
            self.dispatch(run_id)
        return {"queued": queued, "recovered": expired}

    def active_count(self):
        with get_db_session() as db:
            return (
                db.query(ParserControlSetting)
                .filter(
                    ParserControlSetting.key.startswith(PREFIX),
                    ParserControlSetting.value_json["status"].as_string().in_(ACTIVE),
                )
                .count()
            )

    def close(self, run_id, *, user_id, note):
        user = authorize(user_id)
        self.read(run_id, user_id=user_id, is_admin=user.is_admin())
        if len(note.strip()) < 5:
            raise ValueError("请填写至少五个字符的核查说明")
        with get_db_session() as db:
            row = (
                db.query(ParserControlSetting)
                .filter_by(key=PREFIX + run_id)
                .with_for_update(nowait=True)
                .one()
            )
            if row.value_json["status"] != "needs_attention":
                raise ValueError("仅中断待核查任务可以结案")
            row.value_json = {
                **row.value_json,
                "status": "resolved",
                "token": None,
                "resolution": {"user_id": user_id, "note": note, "at": now().isoformat()},
            }
        return self.read(run_id)
