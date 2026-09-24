"""Durable AI preparation after a human binds a command to a CheckItem.

The task owns execution checkpoints, never the human-owned definition or binding.
Existing Parser jobs and immutable catalog/rule drafts remain the asset authorities.
"""
from __future__ import annotations

import copy
import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from src.apps.rule_governance.backend.check_items.catalog import CheckItemCatalogRepository
from src.apps.rule_governance.backend.identity import actor_user_id as normalize_actor_user_id
from src.apps.rule_governance.backend.rule_control.command_asset_units import (
    RuleAssetCommandUnitService,
)
from src.core.check_items.assessment_policy import canonical_digest
from src.infrastructure.db.models import (
    ParserControlSetting,
    ParserTemplateJob,
    RuleAssetCommandUnitRecord,
)
from src.infrastructure.db.postgres import get_db_session

PREFIX = "command-autopilot:"
STAGES = ("rules", "parser", "projection", "implementation", "review")
STAGE_LABELS = {
    "rules": "编译检查规则", "parser": "解析与全样本验证",
    "projection": "转换标准数据并验证", "implementation": "准备设备适配与影响分析",
    "review": "验证与自动发布",
}
ACTIVE = {"queued", "running"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _future(value: str | None) -> bool:
    return bool(value and datetime.fromisoformat(value) > _now())


def task_key(job_id: str, unit_id: str) -> str:
    return PREFIX + canonical_digest([job_id, unit_id])[:48]


def human_scope(inputs: dict) -> dict:
    result = copy.deepcopy(inputs)
    result.get("unit", {}).pop("parser_template_id", None)
    return result


def stop_parser_children(db, state):
    """Fence child results using durable identities, including broker outages."""
    job_id = state.get("inputs", {}).get("unit", {}).get("parser_job_id")
    if state.get("stage") != "parser" or not job_id:
        return
    job = db.query(ParserTemplateJob).filter_by(id=job_id).with_for_update().first()
    if not job:
        return
    agent = dict(job.agent_state_json or {})
    suggestion = dict(agent.get("parser_spec_suggestion") or {})
    if suggestion.get("status") in {"queued", "running"}:
        agent["parser_spec_suggestion"] = {**suggestion, "status": "cancelled", "task_id": "paused:" + uuid.uuid4().hex}
        job.agent_state_json = agent
    if job.status in {"pending", "collecting", "running", "validating"}:
        job.status = "cancelled"
        job.celery_task_id = None
        job.completed_at = _now()
        job.last_error = "自动制作已由人工暂停"


class AutopilotStopped(ValueError):
    pass


class CommandAutopilotService:
    def __init__(self, *, session_scope=get_db_session, executor=None, dispatch=None):
        self.session_scope = session_scope
        self.executor = executor
        self.dispatch = dispatch

    def get(self, job_id: str, unit_id: str) -> dict[str, Any]:
        with self.session_scope() as db:
            row = db.get(ParserControlSetting, task_key(job_id, unit_id))
            state = copy.deepcopy(row.value_json) if row else {
                "status": "idle", "job_id": job_id, "unit_id": unit_id,
                "stage": "rules", "outputs": {}, "history": [],
            }
        # Internal lease tokens are never a browser capability.
        state.pop("lease_token", None)
        state["stage_label"] = STAGE_LABELS.get(state["stage"], state["stage"])
        state["same_name_values"] = (state.get("inputs") or {}).get("value_contract") == "same-name/v1"
        state["can_edit"] = state["status"] not in ACTIVE and not _future(state.get("lease_until"))
        return state

    def _inputs(self, job_id: str, unit_id: str) -> dict[str, Any]:
        unit = RuleAssetCommandUnitService().get_unit(job_id, unit_id)
        item_id = unit.get("candidate_check_item_id") or unit.get("check_item_id")
        version_id = unit.get("candidate_check_item_version_id")
        if not item_id:
            raise ValueError("请先由人工关联命令与检查项，再启动自动制作")
        item = next((x for x in CheckItemCatalogRepository().load_active().check_items if x.id == item_id), None)
        if item is None or (version_id and str(item.version_id) != str(version_id)):
            raise ValueError("检查项版本已变化，请人工重新确认命令关联")
        return {
            "unit": {key: unit.get(key) for key in (
                "asset_key", "vendor", "model", "models", "family", "profile",
                "canonical_command", "parser_job_id", "parser_template_id",
            )},
            "check_item_id": item.id, "check_item_version_id": item.version_id,
            "check_item_digest": item.digest, "result_mode": item.result_mode,
            "value_contract": item.canonical_contract.get("value_contract"),
        }

    def control(self, job_id: str, unit_id: str, *, action: str, actor: str,
                authorize_publish: bool = False, actor_user_id: str | None = None) -> dict[str, Any]:
        actor_user_id = normalize_actor_user_id(actor_user_id)
        if action not in {"start", "pause", "resume", "authorize"}:
            raise ValueError("unsupported autopilot action")
        if action == "authorize" and not authorize_publish:
            raise ValueError("请先在规则治理应用确认自动发布授权")
        inputs = self._inputs(job_id, unit_id) if action != "pause" else None
        key = task_key(job_id, unit_id)
        with self.session_scope() as db:
            # Lock the existing parent, including the first insertion of the setting.
            unit = db.query(RuleAssetCommandUnitRecord).filter_by(id=unit_id, offline_job_id=job_id).with_for_update().first()
            if unit is None:
                raise KeyError("command asset unit not found")
            row = db.query(ParserControlSetting).filter_by(key=key).with_for_update().first()
            old = copy.deepcopy(row.value_json) if row else {}
            if action == "pause":
                if not row:
                    raise ValueError("尚未启动自动制作")
                state = {**old, "status": "paused"}
                stop_parser_children(db, state)
            elif old.get("status") in ACTIVE and action != "authorize":
                return self._public(old)
            else:
                if _future(old.get("lease_until")):
                    raise ValueError("正在结束当前步骤，请稍后继续；现有调用结果不会覆盖人工编辑")
                if action == "resume" and not old:
                    raise ValueError("尚未启动自动制作")
                same = human_scope(old.get("inputs", {})) == human_scope(inputs)
                state = {
                    **old, "job_id": job_id, "unit_id": unit_id,
                    "run_id": old.get("run_id") if same else uuid.uuid4().hex,
                    "inputs": old["inputs"] if same else inputs, "actor": actor, "status": "queued",
                    "actor_user_id": actor_user_id,
                    "stage": old.get("stage", "rules") if same else "rules",
                    "outputs": old.get("outputs", {}) if same else {},
                    "started_at": _now().isoformat(), "error": None,
                    "lease_token": None, "lease_until": None,
                }
                state["publish_grant"] = ({
                    "by": actor, "by_user_id": actor_user_id, "scope_digest": canonical_digest(state["inputs"]),
                    "expires_at": (_now() + timedelta(hours=24)).isoformat(),
                    "authorized_at": _now().isoformat(),
                } if authorize_publish else old.get("publish_grant") if same else None)
                # Revalidate a manually edited Parser before downstream work.
                if same and state["stage"] != "rules" and inputs["result_mode"] == "structured":
                    from src.apps.rule_governance.backend.parser.repository import (
                        ParserJobRepository,
                        ParserTemplateRepository,
                    )
                    from src.apps.rule_governance.backend.rule_control.command_autopilot_steps import (
                        CommandAutopilotExecutor,
                    )
                    parser_job = ParserJobRepository().get_job(inputs["unit"]["parser_job_id"], include_sanitized_payload=True)
                    saved = state["outputs"].get("parser", {})
                    template = ParserTemplateRepository().get_template(saved["template_id"]) if saved.get("template_id") else None
                    if (not parser_job or not template or template.status not in {"active", "pending_approval"}
                            or parser_job.get("parser_spec_sha256") != saved.get("spec_digest")
                            or template.sha256 != saved.get("sha256")
                            or CommandAutopilotExecutor._sample_digest(parser_job) != saved.get("sample_digest")):
                        state["stage"] = "parser"
                        state["outputs"] = {key: value for key, value in state["outputs"].items()
                                            if key == "rules" or (key == "projection" and value.get("manual"))}
                state["outputs"].pop("parser_wait_started", None)
                # A human start/resume grants one bounded retry, even when rules
                # must be prepared before reaching a previously cancelled Parser.
                if (inputs["result_mode"] == "structured"
                        and state["stage"] in {"rules", "parser"}
                        and action in {"start", "resume"}):
                    state["outputs"]["retry_requested"] = True
            state["updated_at"] = _now().isoformat()
            state["history"] = [*old.get("history", []), {
                "action": action, "actor": actor, "at": state["updated_at"],
                "actor_user_id": actor_user_id,
                "stage": state["stage"],
            }][-100:]
            if row is None:
                row = ParserControlSetting(key=key, value_json=state)
                db.add(row)
            else:
                row.value_json = state
        if action != "pause":
            self.enqueue(key)
        return self.get(job_id, unit_id)

    @staticmethod
    def _public(state):
        result = copy.deepcopy(state)
        result.pop("lease_token", None)
        result["stage_label"] = STAGE_LABELS.get(state["stage"], state["stage"])
        result["can_edit"] = state["status"] not in ACTIVE and not _future(state.get("lease_until"))
        return result

    def enqueue(self, key: str, countdown: int = 0) -> None:
        from src.core.celery_tasks.celery_app import celery
        try:
            (self.dispatch or celery.send_task)(
                "advance_command_autopilot", args=[key], queue="app.rule-governance",
                countdown=countdown, soft_time_limit=1080, time_limit=1140,
            )
        except Exception:
            # Durable queued state is recovered by Beat; expose dispatch failure.
            with self.session_scope() as db:
                row = db.query(ParserControlSetting).filter_by(key=key).with_for_update().first()
                if row and row.value_json.get("status") in ACTIVE:
                    row.value_json = {**row.value_json, "error": "任务已保存，等待后台队列恢复"}

    def edit_projection(self, job_id: str, unit_id: str, *, proposal: dict, actor: str,
                        actor_user_id: str | None = None) -> dict:
        actor_user_id = normalize_actor_user_id(actor_user_id)
        if not proposal or len(str(proposal)) > 200_000:
            raise ValueError("请提供有效且大小合理的字段转换定义")
        with self.session_scope() as db:
            row = db.query(ParserControlSetting).filter_by(key=task_key(job_id, unit_id)).with_for_update().first()
            if not row or row.value_json.get("status") in ACTIVE or _future(row.value_json.get("lease_until")):
                raise ValueError("请先暂停并等待当前步骤结束，再进行人工编辑")
            state = copy.deepcopy(row.value_json)
            if not state["outputs"].get("parser", {}).get("template_id"):
                raise ValueError("请先完成解析器生成")
            state["outputs"]["projection"] = {"proposal": proposal, "manual": True}
            state["outputs"].pop("implementation", None)
            state["outputs"].pop("impact", None)
            state.update(stage="projection", status="paused", error=None)
            state["history"] = [*state.get("history", []), {"action": "manual_projection", "actor": actor,
                "actor_user_id": actor_user_id, "at": _now().isoformat(), "stage": "projection"}][-100:]
            row.value_json = state
        return self.get(job_id, unit_id)

    def checkpoint(self, key: str, token: str) -> None:
        with self.session_scope() as db:
            row = db.get(ParserControlSetting, key)
            if not row or row.value_json.get("status") != "running" or row.value_json.get("lease_token") != token:
                raise AutopilotStopped("自动制作已暂停")
            from src.apps.rule_governance.backend.access import authorize
            authorize(row.value_json.get("actor_user_id"))

    def advance(self, key: str) -> dict[str, Any]:
        token = uuid.uuid4().hex
        with self.session_scope() as db:
            row = db.query(ParserControlSetting).filter_by(key=key).with_for_update().first()
            if not row or row.value_json.get("status") not in ACTIVE:
                return {"skipped": True}
            state = copy.deepcopy(row.value_json)
            if _future(state.get("lease_until")):
                return {"skipped": True}
            if state.get("lease_token"):
                row.value_json = {**state, "status": "needs_attention", "lease_token": None, "lease_until": None,
                                  "error": "后台步骤中断，请核对已保留结果后继续"}
                return {"interrupted": True}
            state.update(status="running", lease_token=token, lease_until=(_now() + timedelta(minutes=20)).isoformat())
            row.value_json = state
        try:
            from src.apps.rule_governance.backend.access import authorize
            authorize(state.get("actor_user_id"))
            current = self._inputs(state["job_id"], state["unit_id"])
            # A reconciled template id may progress, but human-owned inputs may not drift.
            if human_scope(current) != human_scope(state["inputs"]):
                raise ValueError("人工定义、设备范围或关联已变化，请重新确认后继续")
            from src.apps.rule_governance.backend.rule_control.command_autopilot_steps import (
                CommandAutopilotExecutor,
            )
            result = (self.executor or CommandAutopilotExecutor()).step(
                state, checkpoint=lambda: self.checkpoint(key, token),
            )
            error = None
        except AutopilotStopped:
            result, error = {}, None
        except Exception as exc:
            result, error = ({"outputs": exc.outputs} if hasattr(exc, "outputs") else {}), str(exc)[:2000]
        schedule = False
        with self.session_scope() as db:
            row = db.query(ParserControlSetting).filter_by(key=key).with_for_update().first()
            if row and row.value_json.get("lease_token") == token:
                latest = copy.deepcopy(row.value_json)
                latest.update(lease_token=None, lease_until=None, updated_at=_now().isoformat())
                if latest["status"] == "running":
                    latest.update(json.loads(json.dumps(result, default=str)))
                    latest["error"] = error
                    latest["status"] = "needs_attention" if error else result.get("status", "queued")
                    schedule = latest["status"] == "queued"
                    if error or result.get("stage") != state["stage"]:
                        latest["history"] = [*latest.get("history", []), {
                            "action": "blocked" if error else "step_completed", "stage": state["stage"],
                            "at": latest["updated_at"], "actor": "ai-assistant", "error": error,
                        }][-100:]
                elif latest["status"] == "paused":
                    # A child may have been dispatched just after the pause commit.
                    stop_parser_children(db, latest)
                row.value_json = latest
        if schedule:
            self.enqueue(key, countdown=15)
        return self.get(state["job_id"], state["unit_id"])

    def recover(self) -> int:
        with self.session_scope() as db:
            rows = db.query(ParserControlSetting).filter(ParserControlSetting.key.startswith(PREFIX)).all()
            keys = [row.key for row in rows if row.value_json.get("status") in ACTIVE and not _future(row.value_json.get("lease_until"))]
        for key in keys:
            self.enqueue(key)
        return len(keys)
