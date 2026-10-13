"""Settle queued work only after deployment inspection proves workers are idle."""

from src.infrastructure.db.models import (
    OfflinePatrolJobRecord,
    ParserControlSetting,
    ParserTemplateJob,
    RolloutOutboxRecord,
    RolloutStageExecutionRecord,
    WorkflowRun,
    WorkflowStepRecord,
)
from src.infrastructure.db.postgres import get_db_session

PREFIXES = ("rule-generation-run:", "engineering-revision-job:", "command-autopilot:")
RETIRED_WORKFLOWS = (
    "rule-asset-generation",
    "rule-asset-submit",
    "rule-asset-approve",
    "parser-validation-ingest",
    "rollout-live-capture",
)


def drain():
    # The platform has already closed admission and checked gateway/worker activity.
    from .access import execution_lock

    with execution_lock(exclusive=True), get_db_session() as db:
        from .check_items.adapter_field_service import cancel_active

        # A field trial has saved nothing; it is simply ended and can be started again.
        cancel_active(db)
        for row in db.query(ParserControlSetting).with_for_update().all():
            if not row.key.startswith(PREFIXES):
                continue
            state = row.value_json
            if state.get("status") == "queued":
                row.value_json = {**state, "status": "cancelled", "error": "应用已停用"}
            elif state.get("status") == "running":
                # A terminated worker may have committed partial assets.
                row.value_json = {
                    **state,
                    "status": "needs_attention",
                    "token": None,
                    "lease_token": None,
                    "error": "应用停用，请核查中断执行的产物",
                }
        for job in db.query(ParserTemplateJob).with_for_update().all():
            if job.status in {"pending", "collecting", "running", "validating"}:
                job.status, job.last_error = "cancelled", "应用已停用"
            state = dict(job.agent_state_json or {})
            suggestion = dict(state.get("parser_spec_suggestion") or {})
            if suggestion.get("status") in {"queued", "running"}:
                state["parser_spec_suggestion"] = {**suggestion, "status": "cancelled"}
                job.agent_state_json = state
        for source in (
            db.query(OfflinePatrolJobRecord)
            .filter_by(purpose="rule_asset_source")
            .with_for_update()
            .all()
        ):
            if source.status in {"queued", "running", "previewing"}:
                source.status, source.error_message = (
                    "failed",
                    "应用停用，已保留导入证据，请检查后重新执行",
                )
        for stage in (
            db.query(RolloutStageExecutionRecord)
            .filter(
                RolloutStageExecutionRecord.status.in_(
                    ["queued", "running", "capturing", "evaluating"]
                )
            )
            .with_for_update()
            .all()
        ):
            stage.status, stage.failure_code = "failed", "app_disabled"
            stage.fencing_token = int(stage.fencing_token or 0) + 1
        db.query(RolloutOutboxRecord).filter(
            RolloutOutboxRecord.status.in_(["pending", "publishing"])
        ).update({"status": "cancelled"})
        for run in (
            db.query(WorkflowRun)
            .filter(WorkflowRun.template_name.in_(RETIRED_WORKFLOWS))
            .with_for_update()
            .all()
        ):
            if run.status not in {"completed", "failed", "cancelled"}:
                run.status, run.error_message = "cancelled", "业务已迁入规则治理应用"
                db.query(WorkflowStepRecord).filter(
                    WorkflowStepRecord.run_id == run.id,
                    WorkflowStepRecord.status.in_(["pending", "queued", "running"]),
                ).update({"status": "cancelled"})


def pending():
    with get_db_session() as db:
        controls = sum(
            1
            for row in db.query(ParserControlSetting).all()
            if row.key.startswith(PREFIXES)
            and row.value_json.get("status") in {"queued", "running"}
        )
        parsers = (
            db.query(ParserTemplateJob)
            .filter(
                ParserTemplateJob.status.in_(["pending", "collecting", "running", "validating"])
            )
            .count()
        )
        legacy = (
            db.query(WorkflowRun)
            .filter(
                WorkflowRun.template_name.in_(RETIRED_WORKFLOWS),
                WorkflowRun.status.notin_(["completed", "failed", "cancelled"]),
            )
            .count()
        )
        sources = (
            db.query(OfflinePatrolJobRecord)
            .filter(
                OfflinePatrolJobRecord.purpose == "rule_asset_source",
                OfflinePatrolJobRecord.status.in_(["queued", "running", "previewing"]),
            )
            .count()
        )
        stages = (
            db.query(RolloutStageExecutionRecord)
            .filter(
                RolloutStageExecutionRecord.status.in_(
                    ["queued", "running", "capturing", "evaluating"]
                )
            )
            .count()
        )
        from .check_items.adapter_field_service import active_trials

        return controls + parsers + legacy + sources + stages + active_trials()
