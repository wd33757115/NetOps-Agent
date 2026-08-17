from __future__ import annotations

from src.core.patrol.event_constraint_governance import (
    scope_event_constraints_to_policy,
    threshold_roles_from_policy,
)


def _constraints() -> dict:
    return {
        "allow_critical": True,
        "evidence_bound_roles": True,
        "role_event_types": {
            "low": "OpticalRxPowerLow",
            "critical_low": "OpticalRxPowerCritical",
            "low_recovery": "OpticalRxPowerRecovered",
        },
        "required_event_types": [
            "OpticalRxPowerLow",
            "OpticalRxPowerCritical",
            "OpticalRxPowerRecovered",
        ],
        "required_event_classes": {
            "OpticalRxPowerLow": "fault",
            "OpticalRxPowerCritical": "fault",
            "OpticalRxPowerRecovered": "recovery",
        },
        "required_severities": {
            "OpticalRxPowerLow": "warning",
            "OpticalRxPowerCritical": "critical",
            "OpticalRxPowerRecovered": "info",
        },
        "required_recovery_pairs": [
            {
                "fault": "OpticalRxPowerLow",
                "recovery": "OpticalRxPowerRecovered",
            },
            {
                "fault": "OpticalRxPowerCritical",
                "recovery": "OpticalRxPowerRecovered",
            },
        ],
        "max_rule_count": 3,
    }


def test_alarm_only_policy_requires_critical_and_recovery_assets():
    policy = {
        "threshold_references": {
            "critical_low": {"field_name": "threshold.critical_low"}
        }
    }

    scoped = scope_event_constraints_to_policy(_constraints(), policy)

    assert scoped["success"] is True
    assert threshold_roles_from_policy(policy) == ("critical_low",)
    assert scoped["active_roles"] == ["critical_low", "low_recovery"]
    assert scoped["active_event_types"] == [
        "OpticalRxPowerCritical",
        "OpticalRxPowerRecovered",
    ]
    assert scoped["constraints"]["required_recovery_pairs"] == [
        {
            "fault": "OpticalRxPowerCritical",
            "recovery": "OpticalRxPowerRecovered",
        }
    ]
    assert scoped["constraints"]["max_rule_count"] == 2


def test_warning_and_alarm_policy_requires_all_supported_assets():
    scoped = scope_event_constraints_to_policy(
        _constraints(),
        {
            "threshold_references": {
                "low": {"field_name": "threshold.low"},
                "critical_low": {"field_name": "threshold.critical_low"},
            }
        },
    )

    assert scoped["success"] is True
    assert scoped["active_roles"] == [
        "low",
        "critical_low",
        "low_recovery",
    ]
    assert len(scoped["active_event_types"]) == 3


def test_evidence_bound_constraints_reject_policy_without_threshold_evidence():
    scoped = scope_event_constraints_to_policy(_constraints(), {})

    assert scoped["success"] is False
    assert scoped["active_event_types"] == []
    assert "at least one proven ChangePolicy threshold role" in scoped["errors"][0]
