"""Fixed rule production sequence. No Workflow registry or expression engine."""


def generate(context, step):
    results = {}
    results["observation_contract"] = step(
        "observation_contract",
        "observation-contract-generator",
        {
            "mode": "infer_and_validate",
            "command": context.get("command"),
            "vendor": context.get("vendor"),
            "model": context.get("model"),
            "records": context.get("records"),
            "record_groups": context.get("record_groups"),
            "field_profiles": context.get("field_profiles"),
            "proposal": context.get("proposal"),
            "observation_targets": context.get("observation_targets"),
            "strict_observation_targets": context.get("strict_observation_targets"),
            "canonical_identity_constraint": context.get("canonical_identity_constraint"),
            "policy_intent": context.get("policy_intent"),
            "event_intent": context.get("event_intent"),
            "generation_provider": context.get("generation_provider"),
            "max_agent_attempts": context.get("max_agent_attempts"),
            "agent_loop": True,
        },
    )
    results["rule_target_planning"] = step(
        "rule_target_planning",
        "rule-target-planner",
        {
            "contract": results.get("observation_contract", {}).get("proposal"),
            "records": context.get("records"),
            "record_groups": context.get("record_groups"),
            "rule_asset_requirements": context.get("rule_asset_requirements"),
        },
    )
    if context.get("rule_asset_requirements", {}).get("requires_change_policy"):
        results["change_policy"] = step(
            "change_policy",
            "change-policy-generator",
            {
                "mode": "infer_and_validate",
                "observation_type": results.get("rule_target_planning", {}).get("observation_type"),
                "field_name": results.get("rule_target_planning", {}).get("field_name"),
                "policy_targets": results.get("rule_target_planning", {}).get("policy_targets"),
                "records": context.get("records"),
                "historical_values": context.get("historical_values"),
                "historical_values_by_field": results.get("rule_target_planning", {}).get(
                    "historical_values_by_field"
                ),
                "device_role": context.get("device_role"),
                "policy_intent": context.get("policy_intent"),
                "generation_provider": context.get("generation_provider"),
                "max_agent_attempts": context.get("max_agent_attempts"),
                "agent_loop": True,
            },
        )
    if context.get("rule_asset_requirements", {}).get("requires_event_rule_assets"):
        results["event_rule"] = step(
            "event_rule",
            "event-rule-generator",
            {
                "contract": results.get("observation_contract", {}).get("proposal"),
                "change_policy": results.get("change_policy", {}).get("policy"),
                "change_policies": results.get("change_policy", {}).get("target_policies"),
                "event_policy_targets": results.get("rule_target_planning", {}).get(
                    "event_policy_targets"
                ),
                "event_observation_types": results.get("rule_target_planning", {}).get(
                    "event_observation_types"
                ),
                "intent": context.get("event_intent"),
                "event_constraints": context.get("event_constraints"),
                "event_constraints_by_observation_type": context.get(
                    "event_constraints_by_observation_type"
                ),
                "rule_asset_requirements": context.get("rule_asset_requirements"),
                "generation_provider": context.get("generation_provider"),
                "max_agent_attempts": context.get("max_agent_attempts"),
                "agent_loop": True,
            },
        )
    if context.get("rule_asset_requirements", {}).get("requires_event_rule_assets"):
        results["event_rule_validation"] = step(
            "event_rule_validation",
            "event-rule-validator",
            {
                "mode": "candidates",
                "candidate_rules": results.get("event_rule", {}).get("rules"),
                "agent_loop": True,
            },
        )
    if context.get("rule_asset_requirements", {}).get("requires_historical_replay"):
        results["event_rule_replay"] = step(
            "event_rule_replay",
            "event-rule-replay",
            {
                "candidate_rules": results.get("event_rule", {}).get("rules"),
                "candidate_contract": results.get("observation_contract", {}).get("proposal"),
                "limit": context.get("replay_limit"),
                "run_ids": context.get("replay_run_ids"),
                "include_active_collisions": True,
            },
        )
    results["persist_assets"] = step(
        "persist_assets",
        "rule-asset-persister",
        {
            "source_ref": context.get("source_ref"),
            "schema_id": context.get("schema_id"),
            "schema_ref": context.get("schema_ref"),
            "source_template_id": context.get("source_template_id"),
            "command": context.get("command"),
            "vendor": context.get("vendor"),
            "model": context.get("model"),
            "observation_type": results.get("rule_target_planning", {}).get("observation_type"),
            "rule_generation_seed_job_id": context.get("rule_generation_seed_job_id"),
            "rule_generation_context_id": context.get("rule_generation_context_id"),
            "rule_generation_context_digest": context.get("rule_generation_context_digest"),
            "created_by": context.get("rule_generation_actor"),
            "generation_identity": context.get("rule_generation_identity"),
            "rule_asset_requirements": results.get("rule_target_planning", {}).get(
                "rule_asset_requirements"
            ),
            "contract_result": results.get("observation_contract", {}),
            "change_policy_result": results.get("change_policy", {}),
            "event_rule_result": results.get("event_rule", {}),
            "event_rule_validation": results.get("event_rule_validation", {}),
            "event_rule_replay": results.get("event_rule_replay", {}),
        },
    )
    return results["persist_assets"]
