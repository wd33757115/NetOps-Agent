from src.core.check_items.risk_issue_projection import (
    build_risk_issue_projection,
)


def _risk(risk_id: str, episode_id: str, check_item_id: str, risk_type: str):
    return {
        "id": risk_id,
        "episode_id": episode_id,
        "check_item_id": check_item_id,
        "device_id": "device-1",
        "entity_type": "interface",
        "entity_key": "Ethernet1/1",
        "correlation_dimensions_json": {"interface": "Ethernet1/1"},
        "risk_type": risk_type,
        "severity": "major",
        "status": "open",
        "source_event_ids_json": [f"event-{risk_id}"],
        "claim_id": f"claim-{risk_id}",
    }


def _approved(primary, related):
    return {
        "id": "association-1",
        "primary_risk_case_id": primary["id"],
        "primary_risk_episode_id": primary["episode_id"],
        "related_risk_case_id": related["id"],
        "related_risk_episode_id": related["episode_id"],
        "relation_type": "causal_symptom",
        "status": "approved",
        "source_type": "manual",
        "reviewed_by": "engineer-b",
        "reviewed_at": "2026-08-02T12:00:00+00:00",
        "evidence_refs_json": ["snapshot-1", "snapshot-2"],
    }


def test_approved_association_reduces_problem_count_without_losing_facts():
    interface_down = _risk(
        "interface-down",
        "episode-interface-down",
        "interface.oper_status",
        "InterfaceDown",
    )
    optical_unavailable = _risk(
        "optical-unavailable",
        "episode-optical-unavailable",
        "interface.optical_power",
        "OpticalPowerUnavailable",
    )

    projection = build_risk_issue_projection(
        risks=[interface_down, optical_unavailable],
        associations=[_approved(interface_down, optical_unavailable)],
        event_occurrences=[
            {"id": "occurrence-down", "aggregate_event_id": "event-interface-down"},
            {
                "id": "occurrence-optical",
                "aggregate_event_id": "event-optical-unavailable",
            },
        ],
    )

    assert projection["problem_count"] == 1
    assert projection["risk_episode_count"] == 2
    assert projection["technical_risk_count"] == 2
    assert projection["associated_symptom_count"] == 1
    assert projection["symptom_count"] == 2
    assert projection["affected_device_count"] == 1
    assert projection["affected_entity_count"] == 1
    assert projection["event_occurrence_count"] == 2
    assert projection["approved_association_count"] == 1
    issue = projection["issues"][0]
    assert issue["risk_case_count"] == 2
    assert issue["technical_risk_count"] == 2
    assert issue["associated_symptom_count"] == 1
    assert issue["event_occurrence_count"] == 2
    assert issue["check_item_ids"] == [
        "interface.oper_status",
        "interface.optical_power",
    ]


def test_pending_or_stale_association_never_changes_problem_count():
    primary = _risk("risk-a", "episode-a", "check.a", "RiskA")
    related = _risk("risk-b", "episode-b-new", "check.b", "RiskB")
    stale = _approved(primary, related)
    stale["related_risk_episode_id"] = "episode-b-old"
    pending = {
        **_approved(primary, related),
        "id": "association-pending",
        "status": "pending_review",
    }

    projection = build_risk_issue_projection(
        risks=[primary, related],
        associations=[stale, pending],
    )

    assert projection["problem_count"] == 2
    assert projection["approved_association_count"] == 0
    assert projection["pending_review_count"] == 1
    assert projection["ignored_associations"] == [
        {
            "association_id": "association-1",
            "reason_code": "episode_stale",
            "primary_risk_case_id": "risk-a",
            "related_risk_case_id": "risk-b",
        }
    ]


def test_ambiguous_or_chained_associations_fail_open_as_separate_issues():
    risk_a = _risk("risk-a", "episode-a", "check.a", "RiskA")
    risk_b = _risk("risk-b", "episode-b", "check.b", "RiskB")
    risk_c = _risk("risk-c", "episode-c", "check.c", "RiskC")
    first = _approved(risk_a, risk_b)
    second = {
        **_approved(risk_b, risk_c),
        "id": "association-2",
    }

    projection = build_risk_issue_projection(
        risks=[risk_a, risk_b, risk_c],
        associations=[first, second],
    )

    assert projection["problem_count"] == 3
    assert projection["approved_association_count"] == 0
    assert projection["ignored_association_count"] == 2
    assert {item["reason_code"] for item in projection["ignored_associations"]} == {
        "non_star_topology"
    }
