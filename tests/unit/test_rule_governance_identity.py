from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from src.apps.rule_governance.backend.identity import actor_user_id
from src.apps.rule_governance.backend.rule_control import engineering_revision_async as tasks


@pytest.mark.parametrize("value", ["", "  ", 42, True, "a" * 65])
def test_invalid_principal_cannot_be_written_as_an_id(value):
    with pytest.raises(ValueError):
        actor_user_id(value)


def test_unknown_legacy_principal_is_not_inferred():
    assert actor_user_id(None) is None


@pytest.mark.parametrize("async_mode", [False, True])
def test_revision_api_identity_comes_from_current_user(monkeypatch, async_mode):
    from src.apps.rule_governance.backend import api
    from src.auth.models import CurrentUser

    submit = Mock(return_value={"success": True, "task_id": "task-id"})
    monkeypatch.setattr(tasks, "enqueue_engineering_revision", submit)
    from src.apps.rule_governance.backend.rule_control import engineering_revision_jobs
    jobs = Mock()
    jobs.submit = submit
    jobs.read.return_value = {"successful": True, "result": {"result": {"success": True}}}
    monkeypatch.setattr(engineering_revision_jobs, "EngineeringRevisionJobs", lambda: jobs)
    request = SimpleNamespace(
        state=SimpleNamespace(
            current_user=CurrentUser(user_id="trusted-id", username="display", role="admin")
        )
    )
    payload = api.RuleAssetEngineeringRevisionRequest.model_validate(
        {
            "contract": {"x": 1},
            "change_policies": [],
            "event_rules": [],
            "reason": "a revision",
            "idempotency_key": "request-id",
            "actor_user_id": "forged-id",
        }
    )
    api.revise_rule_asset_bundle("bundle", request, payload, async_mode=async_mode)
    assert submit.call_args.kwargs["actor_user_id"] == "trusted-id"
