from __future__ import annotations

from src.core.rule_control.generation_agent import (
    AdapterProjectionCandidate,
    ProviderResult,
    RuleGenerationAgent,
    _system_prompt,
    assert_sanitized_generation_context,
)


class FeedbackAwareProvider:
    name = "fake-weak-model"

    def __init__(self) -> None:
        self.feedback = []

    def propose(
        self,
        *,
        stage,
        context,
        attempt,
        previous_candidate,
        validator_feedback,
    ):
        self.feedback.append(validator_feedback)
        candidate = {
            "entity_type": "interface",
            "primary_keys": ["status" if attempt == 1 else "interface"],
        }
        return ProviderResult(candidate=candidate, provider=self.name, model_name="mock")


def test_rule_generation_agent_repairs_candidate_from_validator_feedback():
    provider = FeedbackAwareProvider()
    agent = RuleGenerationAgent(provider, max_attempts=3)

    def validator(candidate):
        valid = candidate["primary_keys"] == ["interface"]
        return {
            "success": valid,
            "errors": [] if valid else ["status is not a stable primary key"],
            "primary_key_status": "valid" if valid else "invalid",
        }

    result = agent.run(
        stage="observation_contract",
        context={
            "field_inventory": ["interface", "status"],
            "records": [{"interface": "GE1/0/1", "status": "up"}],
        },
        validator=validator,
    )

    assert result["success"] is True
    assert result["candidate"]["primary_keys"] == ["interface"]
    assert result["agent_loop"]["attempt_count"] == 2
    assert provider.feedback[0] is None
    assert provider.feedback[1]["primary_key_status"] == "invalid"


def test_rule_generation_agent_stops_after_bounded_failures():
    provider = FeedbackAwareProvider()
    result = RuleGenerationAgent(provider, max_attempts=2).run(
        stage="observation_contract",
        context={"field_inventory": ["interface"], "records": [{"interface": "GE1"}]},
        validator=lambda _candidate: {"success": False, "errors": ["still invalid"]},
    )

    assert result["success"] is False
    assert result["agent_loop"]["attempt_count"] == 2
    assert result["agent_loop"]["next_action"] == "revise_candidate"


def test_rule_generation_context_rejects_raw_or_secret_fields():
    try:
        assert_sanitized_generation_context(
            {"records": [{"interface": "GE1"}], "nested": {"raw_text": "private"}}
        )
    except ValueError as exc:
        assert "nested.raw_text" in str(exc)
    else:
        raise AssertionError("raw context should be rejected")


def test_adapter_projection_candidate_accepts_llm_source_field_context():
    candidate = AdapterProjectionCandidate.model_validate(
        {
            "schema_version": "command-adapter/v2",
            "parser": {
                "template_id": "parser-fan-v1",
                "source_record_set": "environment_fan",
                "source_fields": ["slot", "fan", "fan_status"],
            },
            "outputs": [
                {
                    "check_item_id": "environment.fan",
                    "check_item_version_id": "environment-fan-v1",
                    "source_record_set": "environment_fan",
                    "projection": {
                        "identity_fields": ["slot", "fan"],
                        "required_target_fields": ["slot", "fan", "fan_status"],
                        "fields": {
                            "slot": {"source": "slot"},
                            "fan": {"source": "fan"},
                            "fan_status": {"source": "fan_status"},
                        }
                    },
                }
            ],
        }
    )

    assert candidate.parser["source_fields"] == ["slot", "fan", "fan_status"]


def test_adapter_projection_prompt_requires_field_level_dimension_bindings():
    prompt = _system_prompt("adapter_projection")

    assert "Dimensions belong inside the\nbinding for their canonical field" in prompt
    assert '"window": {"literal": "5m"}' in prompt
    assert "policy_constraints.applies_to_dimensions" in prompt
    assert "use a metric value as a dimension" in prompt
