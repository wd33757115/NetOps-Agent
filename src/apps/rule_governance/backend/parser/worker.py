"""Package-owned Parser delivery lifecycle, shared by new and legacy task names."""

from __future__ import annotations

import logging
import re

from billiard.exceptions import SoftTimeLimitExceeded

from src.apps.rule_governance.backend.generators.runner import (
    GeneratorExecutionError,
    execute_generator,
)
from src.apps.rule_governance.backend.parser.repository import (
    ParserJobRepository,
    ParserJobStateError,
)

logger = logging.getLogger(__name__)

PARSER_EXECUTION_TASK = "netops.rule_governance.execute_parser"

_PARSER_FAILURE_CODE_RE = re.compile(r"^[a-z][a-z0-9_]{2,63}$")


def _parser_skill_failure_code(exc: Exception) -> str:
    """Preserve a stable generator reason instead of flattening every failure."""

    payload = getattr(exc, "payload", None)
    if not isinstance(payload, dict):
        return "skill_execution_failed"

    reasons: list[str] = []

    def add_reason(value) -> None:
        reason = str(value or "").strip()
        if reason:
            reasons.append(reason)

    add_reason(payload.get("failure_code"))
    add_reason(payload.get("error_code"))
    add_reason(payload.get("skipped_reason"))
    if isinstance(payload.get("agent_loop"), dict):
        add_reason(payload["agent_loop"].get("stop_reason"))
    for report in payload.get("reports") or []:
        if not isinstance(report, dict):
            continue
        add_reason(report.get("skipped_reason"))
        if isinstance(report.get("agent_loop"), dict):
            add_reason(report["agent_loop"].get("stop_reason"))

    error_text = str(payload.get("error") or "")
    terminal_match = re.search(
        r"terminal status:\s*([a-z][a-z0-9_]{2,63})",
        error_text,
        flags=re.IGNORECASE,
    )
    if terminal_match:
        reasons.append(terminal_match.group(1))

    for reason in reasons:
        segments = [segment.strip().lower() for segment in reason.split(":")]
        candidate = segments[0]
        if candidate == "model_requested_review" and len(segments) > 1:
            candidate = segments[1]
        if _PARSER_FAILURE_CODE_RE.fullmatch(candidate):
            return candidate
    return "skill_execution_failed"


def _refresh_rule_asset_projection(parser_job_id: str) -> None:
    """Keep the rule control-plane read model aligned with Parser job state."""

    try:
        from src.apps.rule_governance.backend.rule_control.command_asset_units import (
            RuleAssetCommandUnitService,
        )

        RuleAssetCommandUnitService().reconcile_for_parser_job(parser_job_id)
    except Exception:
        logger.exception(
            "Rule asset projection refresh failed parser_job_id=%s",
            parser_job_id,
        )




def execute_parser_delivery(*, task_id: str, params: dict) -> dict:
    """Run only a durable Parser request assigned to this delivery.

    Caller identity stays in the repository. Actor/user fields in task parameters
    cannot replace it. Unknown legacy principals remain unknown until App cutover.
    """
    job_id = str(params.get("job_id") or "").strip()
    operation = str(params.get("operation") or "generate")
    if not job_id or not task_id or operation not in {"generate", "suggest_spec"}:
        return {"success": False, "error": "invalid_parser_delivery"}
    suggestion = operation == "suggest_spec"
    if suggestion and str(params.get("suggestion_task_id") or "") != task_id:
        return {"success": False, "error": "stale_suggestion_discarded"}
    repository = ParserJobRepository()
    current = repository.get_job_lifecycle(job_id)
    if current is None:
        return {"success": False, "error": "parser_job_not_found"}
    identity = current.get("execution_identity") or {}
    if not suggestion and identity.get("task_id") and identity["task_id"] != task_id:
        return {"success": False, "error": "stale_generation_discarded"}
    try:
        if suggestion:
            repository.claim_parser_spec_suggestion(job_id, task_id=task_id)
        else:
            repository.transition_job_status(
                job_id,
                "running",
                expected_status="pending",
                expected_celery_task_id=task_id,
            )
    except ParserJobStateError as exc:
        return {"success": False, "ignored": True, "error": str(exc)}

    def fail_owned_request(error: str, failure_code: str) -> None:
        try:
            if suggestion:
                # Repository checks task ID and nonterminal status under one lock.
                repository.record_parser_spec_suggestion(
                    job_id,
                    task_id=task_id,
                    status="failed",
                    error=error,
                )
                return
            latest = repository.get_job_lifecycle(job_id)
            if not latest or latest["status"] not in {"running", "validating"}:
                return
            repository.transition_job_status(
                job_id,
                "failed",
                error,
                failure_code=failure_code,
                expected_status=latest["status"],
                expected_celery_task_id=task_id,
            )
        except ParserJobStateError:
            # Cancellation/requeue or durable success won the race.
            return

    _refresh_rule_asset_projection(job_id)
    try:
        from src.apps.rule_governance.backend.access import authorize
        principal = (current.get("parser_spec_suggestion") or {}).get("requested_by_user_id") if suggestion else identity.get("user_id")
        authorize(principal)
        allowed = {
            "job_id",
            "operation",
            "suggestion_task_id",
            "publish",
            "dry_run",
            "reports_dir",
            "templates_dir",
        }
        result = execute_generator(
            "textfsm-generator", {key: value for key, value in params.items() if key in allowed}
        )
        latest = repository.get_job_lifecycle(job_id)
        if suggestion:
            state = (latest or {}).get("parser_spec_suggestion") or {}
            if state.get("task_id") != task_id:
                return {"success": False, "error": "stale_suggestion_discarded"}
            if state.get("status") == "ready" and result.get("success"):
                return result
            # A process exit without durable output is not a successful suggestion.
            error = str(result.get("error") or "suggestion result was not persisted")
            fail_owned_request(error, "generator_failed")
            return {"success": False, "error": error, "failure_code": "generator_failed"}
        if not latest or str(latest.get("celery_task_id") or "") != task_id:
            return {"success": False, "error": "stale_generation_discarded"}
        if latest["status"] == "succeeded" and result.get("success"):
            return result
        if latest["status"] not in {"running", "validating"}:
            return {"success": False, "error": "stale_generation_discarded"}
        success = bool(result.get("success"))
        try:
            repository.transition_job_status(
                job_id,
                "succeeded" if success else "failed",
                None if success else str(result.get("error") or result.get("message")),
                failure_code=None if success else "generator_failed",
                expected_status=latest["status"],
                expected_celery_task_id=task_id,
            )
        except ParserJobStateError:
            return {"success": False, "error": "stale_generation_discarded"}
        return result
    except GeneratorExecutionError as exc:
        code = _parser_skill_failure_code(exc)
        fail_owned_request(str(exc), code)
        return {"success": False, "message": str(exc), "error": str(exc), "failure_code": code}
    except SoftTimeLimitExceeded:
        fail_owned_request(
            "Parser generation exceeded the Celery soft time limit", "worker_timeout"
        )
        raise
    except Exception as exc:
        fail_owned_request(str(exc), "worker_exception")
        raise
    finally:
        _refresh_rule_asset_projection(job_id)
