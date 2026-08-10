from src.core.rule_control.asset_requirements import (
    event_rule_role_errors,
    rule_asset_composition_errors,
)


def test_evidence_profile_rejects_irrelevant_policy_and_event_assets():
    requirements = {
        "requires_observation_contract": True,
        "requires_change_policy": False,
        "requires_current_state_rule": False,
        "requires_event_rule": False,
        "requires_event_rule_assets": False,
        "requires_historical_replay": False,
    }

    errors = rule_asset_composition_errors(
        counts={
            "OBSERVATION_CONTRACT": 1,
            "CHANGE_POLICY": 1,
            "EVENT_RULE": 1,
        },
        requirements=requirements,
    )

    assert "bundle contains ChangePolicies that are not required" in errors
    assert "bundle contains EventRules that are not required" in errors


def test_current_state_role_requires_each_declared_observation_type():
    requirements = {
        "requires_observation_contract": True,
        "requires_change_policy": True,
        "requires_current_state_rule": True,
        "requires_event_rule": False,
        "requires_event_rule_assets": True,
        "requires_historical_replay": False,
        "current_state_observation_types": [
            "interface.oper_status",
            "interface.optical.presence",
        ],
    }

    errors = event_rule_role_errors(
        requirements=requirements,
        event_rule_roles=[
            {
                "event_type": "InterfaceDown",
                "event_class": "fault",
                "evaluation_scope": "current_state",
                "observation_types": ["interface.oper_status"],
            }
        ],
    )

    assert (
        "bundle requires current-state rule coverage for "
        "interface.optical.presence"
    ) in errors
    assert (
        "bundle requires current-state rule coverage for interface.oper_status"
        not in errors
    )


def test_change_event_role_requires_catalog_event_types_per_observation():
    requirements = {
        "requires_observation_contract": True,
        "requires_change_policy": True,
        "requires_current_state_rule": False,
        "requires_event_rule": True,
        "requires_event_rule_assets": True,
        "requires_historical_replay": True,
        "required_event_types_by_observation": {
            "interface.oper_status": ["InterfaceDown", "InterfaceUp"],
        },
    }

    errors = event_rule_role_errors(
        requirements=requirements,
        event_rule_roles=[
            {
                "event_type": "InterfaceDown",
                "event_class": "fault",
                "evaluation_scope": "both",
                "condition": {
                    "observation_types": ["interface.oper_status"],
                },
            }
        ],
    )

    assert (
        "bundle is missing required event types for interface.oper_status: "
        "InterfaceUp"
    ) in errors


def test_current_state_only_profile_rejects_change_capable_rule_roles():
    requirements = {
        "requires_observation_contract": True,
        "requires_change_policy": True,
        "requires_current_state_rule": True,
        "requires_event_rule": False,
        "current_state_observation_types": ["environment.fan.state"],
    }

    errors = event_rule_role_errors(
        requirements=requirements,
        event_rule_roles=[
            {
                "event_class": "fault",
                "evaluation_scope": "both",
                "event_type": "environment_fan_state_down",
                "condition": {
                    "observation_types": ["environment.fan.state"],
                },
            },
            {
                "event_class": "recovery",
                "evaluation_scope": "change",
                "event_type": "environment_fan_state_up",
                "condition": {
                    "observation_types": ["environment.fan.state"],
                },
            },
        ],
    )

    assert errors == [
        "bundle requires a current-state EventRule role",
        "bundle requires current-state rule coverage for environment.fan.state",
        "current-state-only bundle cannot contain change-capable EventRule roles",
    ]
