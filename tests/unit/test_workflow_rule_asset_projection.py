from unittest.mock import patch

from src.core.workflows.tasks import _refresh_rule_asset_projection_from_skill_result


def test_rule_asset_persister_refreshes_command_unit_projection() -> None:
    result = {
        "bundle": {
            "id": "bundle-1",
            "payload_json": {"offline_job_id": "offline-1"},
        }
    }

    with patch(
        "src.core.rule_control.command_asset_units."
        "RuleAssetCommandUnitService.reconcile"
    ) as reconcile:
        _refresh_rule_asset_projection_from_skill_result(
            "rule-asset-persister",
            result,
        )

    reconcile.assert_called_once_with("offline-1")


def test_other_skills_do_not_refresh_command_unit_projection() -> None:
    with patch(
        "src.core.rule_control.command_asset_units."
        "RuleAssetCommandUnitService.reconcile"
    ) as reconcile:
        _refresh_rule_asset_projection_from_skill_result(
            "event-rule-generator",
            {
                "bundle": {
                    "payload_json": {"offline_job_id": "offline-1"},
                }
            },
        )

    reconcile.assert_not_called()
