from src.core.patrol.runner import PatrolPlatformError
from src.core.skills.executor import SkillExecutionError, _parse_last_json_payload
from src.core.workflows.tasks import (
    _parser_skill_failure_code,
    _workflow_exception_failure_code,
)


def test_subprocess_failure_payload_uses_last_json_object():
    payload = _parse_last_json_payload(
        "diagnostic line\n"
        '{"success": false, "reports": [{"skipped_reason": '
        '"model_requested_review:contract_conflict: required key is absent"}]}\n'
    )

    assert payload is not None
    assert payload["success"] is False


def test_parser_workflow_preserves_model_review_reason_code():
    error = SkillExecutionError(
        "textfsm generation failed",
        payload={
            "reports": [
                {
                    "skipped_reason": (
                        "model_requested_review:contract_conflict: required key is absent"
                    )
                }
            ]
        },
    )

    assert _parser_skill_failure_code(error) == "contract_conflict"


def test_parser_workflow_preserves_terminal_semantic_review_code():
    error = SkillExecutionError(
        "textfsm generation failed",
        payload={
            "success": False,
            "error": "invalid model-call terminal status: semantic_review_failed",
        },
    )

    assert _parser_skill_failure_code(error) == "semantic_review_failed"


def test_parser_workflow_uses_generic_code_without_structured_payload():
    assert _parser_skill_failure_code(SkillExecutionError("process crashed")) == (
        "skill_execution_failed"
    )


def test_workflow_failure_code_survives_a_celery_retry_wrapper():
    inner = PatrolPlatformError(
        "all devices were unreachable",
        failure_code="connection_failed",
    )
    wrapper = RuntimeError("Retry in 2s")
    wrapper.exc = inner

    assert _workflow_exception_failure_code(wrapper) == "connection_failed"
