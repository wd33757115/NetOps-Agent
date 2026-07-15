from __future__ import annotations

from types import SimpleNamespace

from src.gateway import parser_api


def test_effective_plan_preview_returns_commands(monkeypatch):
    class FakePlan:
        def to_metadata(self):
            return {
                "profile_name": "core",
                "profile_version_id": "v1",
                "collection_items": [{"command": "show version", "action": "patrol"}],
            }

        def command_texts(self):
            return ["show version"]

    class FakeResolver:
        def build_plan(self, *, device, action, mode):
            assert device["device_id"] == "device-1"
            assert action == "patrol"
            assert mode == "production"
            return FakePlan()

    monkeypatch.setattr(parser_api, "CollectionPolicyResolver", FakeResolver)

    result = parser_api.effective_plan_preview(
        parser_api.EffectivePlanPreviewRequest(
            device={"device_id": "device-1"},
            action="patrol",
            mode="production",
        )
    )

    assert result["success"] is True
    assert result["commands"] == ["show version"]


def test_dataset_coverage_uses_current_offline_patrol_run_scope(monkeypatch):
    class FakeRepository:
        def list_datasets(self, *, mode=None, dataset_ids=None):
            assert mode is None
            assert dataset_ids == ["offline-job-current"]
            return [
                {
                    "id": "offline-job-current",
                    "snapshot_count": 99,
                    "device_count": 9,
                    "command_count": 9,
                }
            ]

        def get_offline_dataset_run_scopes(self, dataset_ids):
            assert dataset_ids == ["offline-job-current"]
            return {
                "offline-job-current": {
                    "offline_job_id": "offline-job",
                    "period_role": "current",
                    "observed_at": "2026-06-26T14:06:14+08:00",
                    "patrol_run_id": "offline-job-current-attempt-5",
                    "period_status": "completed",
                }
            }

        def list_run_snapshots(self, run_id):
            assert run_id == "offline-job-current-attempt-5"
            return [
                SimpleNamespace(
                    snapshot_id="snapshot-parsed",
                    parse_status="parsed",
                    device_id="device-1",
                    command_canonical="display interface brief",
                ),
                SimpleNamespace(
                    snapshot_id="snapshot-empty",
                    parse_status="parse_empty",
                    device_id="device-1",
                    command_canonical="display transceiver interface",
                ),
                SimpleNamespace(
                    snapshot_id="snapshot-missing",
                    parse_status="template_missing",
                    device_id="device-2",
                    command_canonical="display vrf",
                ),
                SimpleNamespace(
                    snapshot_id="snapshot-ignored",
                    parse_status="ignored",
                    device_id="device-2",
                    command_canonical="display current-configuration",
                ),
            ]

        def list_dataset_snapshots(self, *args, **kwargs):
            raise AssertionError("offline coverage must not aggregate historical attempts")

        def list_snapshots_with_entity_keys(self, snapshot_ids):
            assert len(snapshot_ids) == 1
            return set(snapshot_ids)

    monkeypatch.setattr(parser_api, "PatrolRepository", FakeRepository)

    result = parser_api.dataset_coverage(
        "offline-job-current,offline-job-current"
    )

    assert result["missing_dataset_ids"] == []
    assert len(result["items"]) == 1
    item = result["items"][0]
    assert item["scope_type"] == "offline_patrol_run"
    assert item["patrol_run_id"] == "offline-job-current-attempt-5"
    assert item["snapshot_count"] == 4
    assert item["device_count"] == 2
    assert item["command_count"] == 4
    assert item["eligible_count"] == 3
    assert item["structured_coverage_rate"] == 2 / 3
    assert item["missing_template_rate"] == 1 / 3
    assert item["primary_key_stability_rate"] == 1.0
    assert item["primary_key_evaluated_count"] == 1


def test_event_rule_bootstrap_endpoint(monkeypatch):
    class FakeRepository:
        def ensure_default_rules(self):
            return 9

    monkeypatch.setattr(parser_api, "EventRuleRepository", FakeRepository)

    assert parser_api.bootstrap_event_rules() == {"success": True, "created": 9}


def test_event_rule_test_endpoint_matches_samples():
    result = parser_api.test_event_rule(
        parser_api.EventRuleTestRequest(
            condition={
                "kind": "threshold",
                "fields": ["cpu_5s"],
                "gte": 90,
            },
            samples=[
                {"field": "cpu_5s", "new": 95},
                {"field": "cpu_5s", "new": 20},
                {"field": "memory", "new": 95},
            ],
        )
    )

    assert result["success"] is True
    assert result["sample_count"] == 3
    assert result["matched_count"] == 1
    assert result["unmatched_count"] == 2
    assert [row["matched"] for row in result["rows"]] == [True, False, False]


def test_event_rule_validation_endpoint_uses_builtin_generic_cases():
    result = parser_api.validate_event_rules(None)

    assert result["success"] is True
    assert result["failed_count"] == 0
    assert result["case_count"] >= 8
    assert result["rule_count"] >= 10
    assert all("device_id" not in row["change"] for row in result["invariant_result"]["rows"])


def test_event_rule_replay_endpoint(monkeypatch):
    captured = {}

    def fake_replay(definitions, *, limit, include_active_collisions):
        captured["definitions"] = definitions
        captured["limit"] = limit
        captured["include_active_collisions"] = include_active_collisions
        return {
            "success": True,
            "matched_change_count": 0,
            "collision_count": 0,
        }

    monkeypatch.setattr(parser_api, "replay_event_rule_definitions", fake_replay)

    result = parser_api.replay_event_rules(
        parser_api.EventRuleReplayRequest(
            limit=50,
            include_active_collisions=False,
            candidate_rules=[
                parser_api.EventRuleCandidateRequest(
                    name="cpu-high-candidate",
                    event_type="CPUHigh",
                    severity="warning",
                    condition={"kind": "threshold", "fields": ["cpu_5s"], "gte": 90},
                )
            ],
        )
    )

    assert result["success"] is True
    assert captured["limit"] == 50
    assert captured["include_active_collisions"] is False
    assert captured["definitions"][0].rule_id == "cpu-high-candidate"


def test_event_rule_list_endpoint_passes_version_filters(monkeypatch):
    captured = {}

    class FakeRepository:
        def list_versions(self, *, status, event_type, limit):
            captured["status"] = status
            captured["event_type"] = event_type
            captured["limit"] = limit
            return [
                {
                    "rule_id": "rule-1",
                    "version_id": "version-1",
                    "version_status": status,
                    "event_type": event_type,
                    "source_job_id": "job-1",
                    "validation_report": {"success": True},
                    "digest": "abc",
                }
            ]

    monkeypatch.setattr(parser_api, "EventRuleRepository", FakeRepository)

    result = parser_api.list_event_rules(
        status="pending_approval",
        event_type="CPUHigh",
        limit=25,
    )

    assert result["items"] == [
        {
            "rule_id": "rule-1",
            "version_id": "version-1",
            "version_status": "pending_approval",
            "event_type": "CPUHigh",
            "source_job_id": "job-1",
            "validation_report": {"success": True},
            "digest": "abc",
        }
    ]
    assert captured == {
        "status": "pending_approval",
        "event_type": "CPUHigh",
        "limit": 25,
    }


def test_observation_contract_list_endpoint(monkeypatch):
    captured = {}

    class FakeRepository:
        def list_contracts(self, *, source_type, status, limit):
            captured["source_type"] = source_type
            captured["status"] = status
            captured["limit"] = limit
            return [{"id": "contract-1", "status": status}]

    monkeypatch.setattr(parser_api, "ObservationContractRepository", FakeRepository)

    result = parser_api.list_observation_contracts(
        source_type="cli",
        status="active",
        limit=25,
    )

    assert result["items"] == [{"id": "contract-1", "status": "active"}]
    assert captured == {"source_type": "cli", "status": "active", "limit": 25}


def test_change_policy_list_endpoint(monkeypatch):
    captured = {}

    class FakeRepository:
        def list_versions(self, *, status, observation_type, limit):
            captured["status"] = status
            captured["observation_type"] = observation_type
            captured["limit"] = limit
            return [{"version_id": "policy-version-1", "version_status": status}]

    monkeypatch.setattr(parser_api, "ChangePolicyRepository", FakeRepository)

    result = parser_api.list_change_policies(
        status="draft",
        observation_type="device.cpu.utilization",
        limit=50,
    )

    assert result["items"] == [{"version_id": "policy-version-1", "version_status": "draft"}]
    assert captured == {
        "status": "draft",
        "observation_type": "device.cpu.utilization",
        "limit": 50,
    }


def test_replay_report_list_endpoint(monkeypatch):
    captured = {}

    class FakeRepository:
        def list_replay_reports(self, *, success, asset_type, limit):
            captured["success"] = success
            captured["asset_type"] = asset_type
            captured["limit"] = limit
            return [
                {
                    "id": "job-1:historical_replay",
                    "job_id": "job-1",
                    "success": success,
                    "asset_type": asset_type,
                    "matched_change_count": 2,
                    "collision_count": 0,
                }
            ]

    monkeypatch.setattr(parser_api, "RuleGenerationJobRepository", FakeRepository)

    result = parser_api.list_replay_reports(
        success=True,
        asset_type="EVENT_RULE",
        limit=25,
    )

    assert result["items"] == [
        {
            "id": "job-1:historical_replay",
            "job_id": "job-1",
            "success": True,
            "asset_type": "EVENT_RULE",
            "matched_change_count": 2,
            "collision_count": 0,
        }
    ]
    assert captured == {"success": True, "asset_type": "EVENT_RULE", "limit": 25}


def test_rule_asset_approval_audit_endpoint(monkeypatch):
    captured = {}

    class FakeRepository:
        def list_approval_audit(self, *, action, asset_type, actor, limit):
            captured["action"] = action
            captured["asset_type"] = asset_type
            captured["actor"] = actor
            captured["limit"] = limit
            return [
                {
                    "id": "job-1:approved",
                    "job_id": "job-1",
                    "action": action,
                    "asset_type": asset_type,
                    "actor": actor,
                    "validation_success": True,
                }
            ]

    monkeypatch.setattr(parser_api, "RuleGenerationJobRepository", FakeRepository)

    result = parser_api.list_rule_asset_approval_audit(
        action="approved",
        asset_type="EVENT_RULE",
        actor="operator",
        limit=25,
    )

    assert result["items"] == [
        {
            "id": "job-1:approved",
            "job_id": "job-1",
            "action": "approved",
            "asset_type": "EVENT_RULE",
            "actor": "operator",
            "validation_success": True,
        }
    ]
    assert captured == {
        "action": "approved",
        "asset_type": "EVENT_RULE",
        "actor": "operator",
        "limit": 25,
    }


def test_agent_loop_attempts_endpoint(monkeypatch):
    captured = {}

    class FakeRepository:
        def list_agent_loop_attempts(self, *, asset_type, stage, success, limit):
            captured["asset_type"] = asset_type
            captured["stage"] = stage
            captured["success"] = success
            captured["limit"] = limit
            return [
                {
                    "id": "job-1:agent_loop:1",
                    "job_id": "job-1",
                    "asset_type": asset_type,
                    "stage": stage,
                    "success": success,
                    "attempt": 1,
                    "revision_request": {
                        "target_generator": "event-rule-generator",
                    },
                }
            ]

    monkeypatch.setattr(parser_api, "RuleGenerationJobRepository", FakeRepository)

    result = parser_api.list_rule_asset_agent_loop_attempts(
        asset_type="EVENT_RULE",
        stage="event_rule_validation",
        success=False,
        limit=25,
    )

    assert result["items"] == [
        {
            "id": "job-1:agent_loop:1",
            "job_id": "job-1",
            "asset_type": "EVENT_RULE",
            "stage": "event_rule_validation",
            "success": False,
            "attempt": 1,
            "revision_request": {
                "target_generator": "event-rule-generator",
            },
        }
    ]
    assert captured == {
        "asset_type": "EVENT_RULE",
        "stage": "event_rule_validation",
        "success": False,
        "limit": 25,
    }


def test_rule_generation_job_create_endpoint(monkeypatch):
    class FakeRepository:
        def create_job(
            self,
            *,
            job_type,
            source_ref,
            asset_ref,
            asset_type,
            asset_id,
            asset_version_id,
            asset_digest,
            payload,
            validation_report,
            approval_required,
            created_by,
            status,
        ):
            assert job_type == "EVENT_RULE"
            assert source_ref == "contract:1"
            assert asset_ref is None
            assert asset_type is None
            assert asset_id is None
            assert asset_version_id is None
            assert asset_digest is None
            assert payload == {"candidate": True}
            assert validation_report == {"success": True}
            assert approval_required is True
            assert created_by == "unit-test"
            assert status == "draft"
            return {"id": "job-1", "job_type": job_type, "status": status}

    monkeypatch.setattr(parser_api, "RuleGenerationJobRepository", FakeRepository)

    result = parser_api.create_rule_generation_job(
        parser_api.RuleGenerationJobCreateRequest(
            job_type="EVENT_RULE",
            source_ref="contract:1",
            payload={"candidate": True},
            validation_report={"success": True},
            created_by="unit-test",
        )
    )

    assert result == {"id": "job-1", "job_type": "EVENT_RULE", "status": "draft"}


def test_rule_generation_job_transition_endpoint(monkeypatch):
    class FakeRepository:
        def transition_job(
            self,
            job_id,
            *,
            status,
            validation_report,
            asset_ref,
            asset_type,
            asset_id,
            asset_version_id,
            asset_digest,
        ):
            assert job_id == "job-1"
            assert status == "validating"
            assert validation_report is None
            assert asset_ref is None
            assert asset_type is None
            assert asset_id is None
            assert asset_version_id is None
            assert asset_digest is None
            return {"id": job_id, "status": status}

    monkeypatch.setattr(parser_api, "RuleGenerationJobRepository", FakeRepository)

    result = parser_api.transition_rule_generation_job(
        "job-1",
        parser_api.RuleGenerationJobTransitionRequest(status="validating"),
    )

    assert result == {"id": "job-1", "status": "validating"}


def test_rule_generation_job_validation_report_endpoint(monkeypatch):
    class FakeRepository:
        def attach_validation_report(self, job_id, *, validation_report):
            assert job_id == "job-1"
            assert validation_report == {"success": True}
            return {"id": job_id, "validation_report_json": validation_report}

    monkeypatch.setattr(parser_api, "RuleGenerationJobRepository", FakeRepository)

    result = parser_api.attach_rule_generation_validation_report(
        "job-1",
        parser_api.RuleGenerationJobValidationRequest(validation_report={"success": True}),
    )

    assert result["validation_report_json"] == {"success": True}


def test_rule_generation_job_preview_endpoint(monkeypatch):
    class FakeRepository:
        def preview_job(self, job_id):
            assert job_id == "job-1"
            return {
                "success": True,
                "job": {"id": job_id, "status": "pending_approval"},
                "approval_readiness": {"can_approve": True, "blockers": []},
            }

    monkeypatch.setattr(parser_api, "RuleGenerationJobRepository", FakeRepository)

    result = parser_api.preview_rule_generation_job("job-1")

    assert result["success"] is True
    assert result["approval_readiness"]["can_approve"] is True


def test_rule_generation_job_preview_endpoint_reports_404(monkeypatch):
    class FakeRepository:
        def preview_job(self, job_id):
            raise KeyError("rule generation job not found")

    monkeypatch.setattr(parser_api, "RuleGenerationJobRepository", FakeRepository)

    try:
        parser_api.preview_rule_generation_job("missing")
    except parser_api.HTTPException as exc:
        assert exc.status_code == 404
    else:
        raise AssertionError("preview should report missing jobs")


def test_rule_generation_job_approve_activates_event_rule_asset(monkeypatch):
    class FakeApprovalService:
        def approve(self, job_id, *, actor):
            assert job_id == "job-1"
            assert actor == "operator"
            return {
                "success": True,
                "job": {"id": job_id, "status": "active"},
                "asset_activation": {
                    "version_id": "event-rule-version-1",
                    "version_status": "active",
                },
            }

    monkeypatch.setattr(parser_api, "RuleAssetApprovalService", FakeApprovalService)

    result = parser_api.approve_rule_generation_job(
        "job-1",
        parser_api.RuleGenerationJobApprovalRequest(actor="operator"),
    )

    assert result["success"] is True
    assert result["job"]["status"] == "active"
    assert result["asset_activation"]["version_status"] == "active"


def test_rule_generation_job_approve_rejects_non_pending(monkeypatch):
    class FakeApprovalService:
        def approve(self, job_id, *, actor):
            raise parser_api.RuleGenerationJobStateError("must be pending_approval")

    monkeypatch.setattr(parser_api, "RuleAssetApprovalService", FakeApprovalService)

    try:
        parser_api.approve_rule_generation_job("job-1", None)
    except parser_api.HTTPException as exc:
        assert exc.status_code == 409
    else:
        raise AssertionError("approve should reject non pending_approval jobs")


def test_rule_generation_job_revise_endpoint(monkeypatch):
    class FakeRevisionService:
        def create_revision_job(self, job_id, *, actor):
            assert job_id == "job-1"
            assert actor == "operator"
            return {
                "success": True,
                "created": True,
                "parent_job_id": job_id,
                "job": {"id": "job-2", "status": "draft"},
            }

    monkeypatch.setattr(parser_api, "AgentLoopRevisionService", FakeRevisionService)

    result = parser_api.revise_rule_generation_job(
        "job-1",
        parser_api.RuleGenerationJobApprovalRequest(actor="operator"),
    )

    assert result["created"] is True
    assert result["job"]["id"] == "job-2"


def test_rule_generation_job_revise_requires_revision_request(monkeypatch):
    class FakeRevisionService:
        def create_revision_job(self, job_id, *, actor):
            raise ValueError("agent loop revision_request is required")

    monkeypatch.setattr(parser_api, "AgentLoopRevisionService", FakeRevisionService)

    try:
        parser_api.revise_rule_generation_job("job-1", None)
    except parser_api.HTTPException as exc:
        assert exc.status_code == 409
    else:
        raise AssertionError("revise should require revision_request")


def test_rule_generation_job_execute_revision_endpoint(monkeypatch):
    class FakeRevisionExecutor:
        def execute(self, job_id, *, actor):
            assert job_id == "job-2"
            assert actor == "operator"
            return {
                "success": True,
                "job": {"id": job_id, "status": "validating"},
                "skill_name": "event-rule-validator",
            }

    monkeypatch.setattr(parser_api, "AgentLoopRevisionExecutor", FakeRevisionExecutor)

    result = parser_api.execute_rule_generation_revision_job(
        "job-2",
        parser_api.RuleGenerationJobApprovalRequest(actor="operator"),
    )

    assert result["success"] is True
    assert result["skill_name"] == "event-rule-validator"


def test_rule_generation_job_execute_revision_rejects_non_revision(monkeypatch):
    class FakeRevisionExecutor:
        def execute(self, job_id, *, actor):
            raise parser_api.RuleGenerationJobStateError(
                "only agent loop revision jobs can be executed"
            )

    monkeypatch.setattr(parser_api, "AgentLoopRevisionExecutor", FakeRevisionExecutor)

    try:
        parser_api.execute_rule_generation_revision_job("job-1", None)
    except parser_api.HTTPException as exc:
        assert exc.status_code == 409
    else:
        raise AssertionError("execute_revision should reject non revision jobs")


def test_rule_generation_job_submit_candidate_endpoint(monkeypatch):
    class FakeSubmissionService:
        def submit_candidate(self, job_id, *, actor):
            assert job_id == "job-1"
            assert actor == "operator"
            return {
                "success": True,
                "submitted_job": {"id": job_id},
                "persister_result": {"asset_count": 1},
            }

    monkeypatch.setattr(parser_api, "RuleAssetSubmissionService", FakeSubmissionService)

    result = parser_api.submit_rule_generation_job_candidate(
        "job-1",
        parser_api.RuleGenerationJobApprovalRequest(actor="operator"),
    )

    assert result["success"] is True
    assert result["persister_result"]["asset_count"] == 1


def test_rule_generation_job_submit_candidate_rejects_invalid_state(monkeypatch):
    class FakeSubmissionService:
        def submit_candidate(self, job_id, *, actor):
            raise parser_api.RuleGenerationJobStateError("validation required")

    monkeypatch.setattr(parser_api, "RuleAssetSubmissionService", FakeSubmissionService)

    try:
        parser_api.submit_rule_generation_job_candidate("job-1", None)
    except parser_api.HTTPException as exc:
        assert exc.status_code == 409
    else:
        raise AssertionError("submit should reject invalid jobs")


def test_rule_generation_job_generation_context_endpoint(monkeypatch):
    class FakeService:
        def build_generation_context(self, job_id, *, context_overrides):
            assert job_id == "job-1"
            assert context_overrides == {"records": [{"field": "value"}]}
            return {
                "success": True,
                "ready": True,
                "workflow": "rule-asset-generation",
                "context": {"records": [{"field": "value"}]},
                "missing_context": [],
            }

    monkeypatch.setattr(parser_api, "RuleAssetTemplateActivationService", FakeService)

    result = parser_api.preview_rule_asset_generation_context(
        "job-1",
        parser_api.RuleAssetGenerationContextRequest(
            context_overrides={"records": [{"field": "value"}]},
        ),
    )

    assert result["ready"] is True
    assert result["workflow"] == "rule-asset-generation"


def test_rule_generation_job_start_generation_endpoint(monkeypatch):
    class FakeService:
        def start_generation_workflow(self, job_id, *, context_overrides, actor):
            assert job_id == "job-1"
            assert context_overrides == {"records": [{"field": "value"}]}
            assert actor == "operator"
            return {"success": True, "run_id": "workflow-1"}

    monkeypatch.setattr(parser_api, "RuleAssetTemplateActivationService", FakeService)

    result = parser_api.start_rule_asset_generation_workflow(
        "job-1",
        parser_api.RuleAssetGenerationContextRequest(
            context_overrides={"records": [{"field": "value"}]},
            actor="operator",
        ),
    )

    assert result["success"] is True
    assert result["run_id"] == "workflow-1"


def test_rule_generation_job_reject_endpoint(monkeypatch):
    class FakeApprovalService:
        def reject(self, job_id, *, actor, reason):
            assert job_id == "job-1"
            assert actor == "operator"
            assert reason == "bad candidate"
            return {"success": True, "job": {"id": job_id, "status": "rejected"}}

    monkeypatch.setattr(parser_api, "RuleAssetApprovalService", FakeApprovalService)

    result = parser_api.reject_rule_generation_job(
        "job-1",
        parser_api.RuleGenerationJobApprovalRequest(
            actor="operator",
            reason="bad candidate",
        ),
    )

    assert result["job"]["status"] == "rejected"


def test_rule_generation_job_deprecate_deprecates_event_rule_asset(monkeypatch):
    class FakeApprovalService:
        def deprecate(self, job_id, *, actor, reason):
            assert actor == "operator"
            assert reason == "superseded"
            return {
                "success": True,
                "job": {"id": job_id, "status": "deprecated"},
                "asset_deprecation": {
                    "version_id": "event-rule-version-1",
                    "version_status": "deprecated",
                },
            }

    monkeypatch.setattr(parser_api, "RuleAssetApprovalService", FakeApprovalService)

    result = parser_api.deprecate_rule_generation_job(
        "job-1",
        parser_api.RuleGenerationJobApprovalRequest(
            actor="operator",
            reason="superseded",
        ),
    )

    assert result["job"]["status"] == "deprecated"
    assert result["asset_deprecation"]["version_status"] == "deprecated"


def test_event_rule_save_creates_draft_by_default(monkeypatch):
    class FakeRepository:
        def create_rule_version(
            self,
            *,
            name,
            event_type,
            severity,
            condition,
            description,
            created_by,
            status,
        ):
            assert name == "cpu-high-custom"
            assert event_type == "CPUHigh"
            assert severity == "warning"
            assert condition == {"kind": "threshold"}
            assert description is None
            assert created_by is None
            assert status == "draft"
            return "event-rule-version-1"

    monkeypatch.setattr(parser_api, "EventRuleRepository", FakeRepository)

    result = parser_api.upsert_event_rule(
        parser_api.EventRuleUpsertRequest(
            name="cpu-high-custom",
            event_type="CPUHigh",
            severity="warning",
            condition={"kind": "threshold"},
        )
    )

    assert result == {"success": True, "version_id": "event-rule-version-1"}


def test_event_rule_save_rejects_direct_active_create(monkeypatch):
    class FakeRepository:
        def upsert_active_rule(self, **_kwargs):
            raise AssertionError("active rule creation must not bypass approval")

        def create_rule_version(self, **_kwargs):
            raise AssertionError("active status should be rejected before persistence")

    monkeypatch.setattr(parser_api, "EventRuleRepository", FakeRepository)

    try:
        parser_api.upsert_event_rule(
            parser_api.EventRuleUpsertRequest(
                name="cpu-high-custom",
                event_type="CPUHigh",
                severity="warning",
                condition={"kind": "threshold"},
                status="active",
            )
        )
    except parser_api.HTTPException as exc:
        assert exc.status_code == 400
        assert "draft or pending_approval" in exc.detail
    else:
        raise AssertionError("active rule creation should be rejected")


def test_event_rule_activate_endpoint(monkeypatch):
    class FakeRepository:
        def activate_rule_version(self, version_id):
            assert version_id == "event-rule-version-1"
            return {
                "version_id": version_id,
                "version_status": "active",
            }

    monkeypatch.setattr(parser_api, "EventRuleRepository", FakeRepository)

    result = parser_api.activate_event_rule("event-rule-version-1")

    assert result["version_status"] == "active"


def test_event_rule_deprecate_endpoint(monkeypatch):
    class FakeRepository:
        def deprecate_rule_version(self, version_id):
            assert version_id == "event-rule-version-1"
            return {
                "version_id": version_id,
                "version_status": "deprecated",
            }

    monkeypatch.setattr(parser_api, "EventRuleRepository", FakeRepository)

    result = parser_api.deprecate_event_rule("event-rule-version-1")

    assert result["version_status"] == "deprecated"


def test_command_profile_save_creates_draft_by_default(monkeypatch):
    class FakeRepository:
        def create_profile_version(self, *, name, action, commands, created_by, status):
            assert name == "core-patrol"
            assert action == "patrol"
            assert commands == ["show version"]
            assert created_by is None
            assert status == "draft"
            return "profile-version-1"

    monkeypatch.setattr(parser_api, "CommandProfileRepository", FakeRepository)

    result = parser_api.upsert_command_profile(
        parser_api.CommandProfileUpsertRequest(
            name="core-patrol",
            commands=["show version"],
        )
    )

    assert result == {
        "success": True,
        "profile_version_id": "profile-version-1",
        "status": "draft",
    }


def test_command_profile_activate_endpoint(monkeypatch):
    calls = []

    class FakeRepository:
        def validate_profile_activation(self, profile_version_id):
            calls.append(("validate", profile_version_id))

        def activate_profile_version(self, profile_version_id):
            calls.append(("activate", profile_version_id))
            assert profile_version_id == "profile-version-1"
            return {
                "profile_version_id": profile_version_id,
                "version_status": "active",
            }

    monkeypatch.setattr(parser_api, "CommandProfileRepository", FakeRepository)

    result = parser_api.activate_command_profile("profile-version-1")

    assert result["version_status"] == "active"
    assert calls == [
        ("validate", "profile-version-1"),
        ("activate", "profile-version-1"),
    ]


def test_command_profile_rollback_endpoint(monkeypatch):
    class FakeRepository:
        def rollback_profile_version(self, profile_version_id):
            assert profile_version_id == "profile-version-1"
            return {
                "profile_version_id": profile_version_id,
                "version_status": "active",
            }

    monkeypatch.setattr(parser_api, "CommandProfileRepository", FakeRepository)

    result = parser_api.rollback_command_profile("profile-version-1")

    assert result["version_status"] == "active"


def test_command_profile_delete_endpoint(monkeypatch):
    class FakeRepository:
        def delete_profile_version(self, profile_version_id):
            assert profile_version_id == "profile-version-1"
            return {
                "success": True,
                "profile_version_id": profile_version_id,
                "profile_deleted": True,
            }

    monkeypatch.setattr(parser_api, "CommandProfileRepository", FakeRepository)

    result = parser_api.delete_command_profile("profile-version-1")

    assert result["success"] is True
    assert result["profile_deleted"] is True


def test_command_profile_binding_disable_endpoint(monkeypatch):
    class FakeRepository:
        def set_binding_status(self, binding_id, status):
            assert binding_id == "binding-1"
            assert status == "disabled"
            return {"id": binding_id, "status": status}

    monkeypatch.setattr(parser_api, "CommandProfileRepository", FakeRepository)

    result = parser_api.disable_command_profile_binding("binding-1")

    assert result == {"id": "binding-1", "status": "disabled"}


def test_command_profile_binding_endpoint(monkeypatch):
    class FakeRepository:
        def upsert_binding(self, *, profile_version_id, selector, priority, status):
            assert profile_version_id == "profile-version-1"
            assert selector == {"vendor": "Cisco", "role": "core"}
            assert priority == 10
            assert status == "active"
            return "binding-1"

    monkeypatch.setattr(parser_api, "CommandProfileRepository", FakeRepository)

    result = parser_api.upsert_command_profile_binding(
        parser_api.CommandProfileBindingUpsertRequest(
            profile_version_id="profile-version-1",
            selector={"vendor": "Cisco", "role": "core"},
            priority=10,
        )
    )

    assert result == {"success": True, "binding_id": "binding-1"}


def test_command_profile_impact_preview_with_inline_commands(monkeypatch):
    class FakeRepository:
        def get_devices_by_filter(self, **kwargs):
            assert kwargs["status"] == "active"
            return [
                {
                    "device_id": "device-1",
                    "device_name": "SW1",
                    "ip": "10.0.0.1",
                    "vendor": "Cisco",
                    "model": "WS-C2960XR-24TS-I",
                    "role": "core",
                    "groups": ["xian"],
                },
                {
                    "device_id": "device-2",
                    "device_name": "SW2",
                    "ip": "10.0.0.2",
                    "vendor": "Cisco",
                    "model": "WS-C2960XR-24TS-I",
                    "role": "access",
                    "groups": ["xian"],
                },
            ]

    monkeypatch.setattr(parser_api, "DeviceRepository", FakeRepository)
    monkeypatch.setattr(
        parser_api.PatrolTool,
        "_production_coverage",
        staticmethod(
            lambda device, commands: [
                {
                    "canonical_command": "show version",
                    "policy_status": "eligible",
                    "coverage_status": "ready",
                },
                {
                    "canonical_command": "show inventory",
                    "policy_status": "eligible",
                    "coverage_status": "template_missing",
                },
            ]
        ),
    )

    result = parser_api.preview_command_profile_impact(
        parser_api.CommandProfileImpactPreviewRequest(
            profile_name="core-patrol",
            commands=["show version", "show inventory"],
            selector={"role": "core"},
        )
    )

    assert result["success"] is True
    assert result["matched_device_count"] == 1
    assert result["blocked_device_count"] == 1
    assert result["eligible_command_count"] == 2
    assert result["ready_command_count"] == 1
    assert result["blocked_command_count"] == 1
    assert result["coverage_rate"] == 0.5
    assert result["rows"][0]["device_id"] == "device-1"
    assert result["rows"][0]["violations"][0]["canonical_command"] == "show inventory"


def test_command_profile_impact_preview_can_load_profile_version(monkeypatch):
    saved = []

    class FakeProfileRepository:
        def get_profile_version(self, profile_version_id):
            assert profile_version_id == "profile-version-1"
            return {
                "profile_name": "site-patrol",
                "profile_version_id": profile_version_id,
                "items": [
                    {"action": "patrol", "command": "show version"},
                    {"action": "backup", "command": "show running-config"},
                ],
            }

        def record_profile_coverage(self, profile_version_id, coverage):
            saved.append((profile_version_id, coverage))
            return {"profile_version_id": profile_version_id, "coverage": coverage}

    class FakeDeviceRepository:
        def get_devices_by_filter(self, **kwargs):
            return [{"device_id": "device-1", "device_name": "SW1", "vendor": "Cisco"}]

    monkeypatch.setattr(parser_api, "CommandProfileRepository", FakeProfileRepository)
    monkeypatch.setattr(parser_api, "DeviceRepository", FakeDeviceRepository)
    monkeypatch.setattr(
        parser_api.PatrolTool,
        "_production_coverage",
        staticmethod(
            lambda device, commands: [
                {
                    "canonical_command": commands[0],
                    "policy_status": "eligible",
                    "coverage_status": "ready",
                }
            ]
        ),
    )

    result = parser_api.preview_command_profile_impact(
        parser_api.CommandProfileImpactPreviewRequest(
            profile_version_id="profile-version-1",
            save_evidence=True,
        )
    )

    assert result["profile_name"] == "site-patrol"
    assert result["profile_version_id"] == "profile-version-1"
    assert result["command_count"] == 1
    assert result["ready_device_count"] == 1
    assert result["rows"][0]["plan"]["profile_version_id"] == "profile-version-1"
    assert result["evidence_saved"] is True
    assert saved[0][0] == "profile-version-1"
    assert saved[0][1]["blocked_device_count"] == 0


def test_runtime_rule_asset_resolve_endpoint(monkeypatch):
    calls = []

    class FakeBundle:
        def to_dict(self):
            return {
                "ready": False,
                "observation_contracts": {"cli:template-1": {"entity_type": "interface"}},
                "change_policies": {},
                "event_rules": [{"rule_id": "InterfaceDown"}],
                "missing_observation_contracts": [],
                "missing_change_policies": ["interface.oper_status"],
                "counts": {
                    "observation_contracts": 1,
                    "change_policies": 0,
                    "event_rules": 1,
                },
            }

    class FakeResolver:
        def load_bundle(
            self,
            *,
            observation_schemas,
            observation_types,
            include_event_rules,
        ):
            calls.append(
                {
                    "observation_schemas": observation_schemas,
                    "observation_types": observation_types,
                    "include_event_rules": include_event_rules,
                }
            )
            return FakeBundle()

    monkeypatch.setattr(parser_api, "RuntimeRuleAssetResolver", FakeResolver)

    result = parser_api.resolve_runtime_rule_assets(
        parser_api.RuntimeRuleAssetResolveRequest(
            observation_schemas=[("cli", "template-1")],
            observation_types=["interface.oper_status"],
        )
    )

    assert result["ready"] is False
    assert result["counts"]["observation_contracts"] == 1
    assert result["missing_change_policies"] == ["interface.oper_status"]
    assert calls == [
        {
            "observation_schemas": [("cli", "template-1")],
            "observation_types": ["interface.oper_status"],
            "include_event_rules": True,
        }
    ]
