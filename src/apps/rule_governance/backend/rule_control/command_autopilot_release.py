"""Release a validated task under explicit administrator delegation."""
from datetime import datetime, timezone

from src.apps.rule_governance.backend.check_items.canonical_rule_context import (
    CanonicalRuleContextService,
)
from src.apps.rule_governance.backend.check_items.catalog import CheckItemCatalogRepository
from src.apps.rule_governance.backend.check_items.catalog_governance import (
    CheckItemCatalogGovernanceService,
)
from src.apps.rule_governance.backend.check_items.runtime_bundle import (
    CheckItemRuntimeBundleService,
)
from src.apps.rule_governance.backend.check_items.runtime_bundle_assembly import (
    RuntimeBundleAssemblyPlanner,
    event_rule_observation_types,
)
from src.apps.rule_governance.backend.parser.repository import (
    ParserJobRepository,
    ParserTemplateRepository,
)
from src.apps.rule_governance.backend.rule_control.approval import RuleAssetApprovalService
from src.apps.rule_governance.backend.rule_control.autopilot_authorization import (
    require_autopilot_release,
)
from src.core.check_items.assessment_policy import canonical_digest
from src.core.check_items.command_adapter import direct_projection_manifest, execute_adapter
from src.infrastructure.db.models import (
    AuditLogRecord,
    ChangePolicyVersionRecord,
    CheckItemRuntimeBundleVersionRecord,
    EventRuleRecord,
    EventRuleVersionRecord,
    ParserTemplate,
    RuleGenerationJobRecord,
    RuntimeBundleAssetRecord,
)
from src.infrastructure.db.postgres import get_db_session


def release_command_task(state, *, checkpoint):
    from src.apps.rule_governance.backend.rule_control.command_autopilot import task_key
    from src.apps.rule_governance.backend.rule_control.command_autopilot_steps import (
        CommandAutopilotExecutor,
        parse_samples,
    )
    inputs, outputs = state["inputs"], state["outputs"]
    grant = state.get("publish_grant") or {}
    if not grant or datetime.fromisoformat(grant["expires_at"]) <= datetime.now(timezone.utc):
        return {"status": "needs_authorization", "stage": "review", "outputs": outputs}
    authorization = (task_key(state["job_id"], state["unit_id"]), state["lease_token"])
    actor = f"ai-autopilot:{grant['by']}"
    reason = f"管理员 {grant['by']} 授权任务 {state['run_id']} 验证通过后自动发布"
    change_id = outputs["implementation"]["id"]
    with get_db_session() as db:
        require_autopilot_release(db, authorization, kind="implementation", asset_id=change_id)
    current = CanonicalRuleContextService().generate_candidates(inputs["check_item_version_id"])
    if not current.get("success") or current["digest"] != outputs["rules"]["digest"]:
        raise ValueError("检查标准或规则候选已变化，需要重新验证")
    service = CheckItemCatalogGovernanceService()
    change = service.get_change(change_id)
    if change["candidate"]["digest"] != outputs["implementation"]["candidate"]["digest"]:
        raise ValueError("设备适配草稿已变化，需要重新验证")
    impact = service.preview_impact(change_id)
    if impact.get("blockers"):
        raise ValueError("发布影响检查未通过：" + "; ".join(str(x.get("message") or x.get("code")) for x in impact["blockers"]))
    parser_ids = [outputs["parser"]["template_id"]] if inputs["result_mode"] == "structured" else []
    runtime_service = CheckItemRuntimeBundleService()
    args = {
        "check_item_version_id": inputs["check_item_version_id"],
        "implementation_version_id": change["version_id"],
        "parser_template_ids": parser_ids,
        "change_policy_version_ids": [x["version_id"] for x in outputs["rules"].get("change_policy_assets", [])],
        "event_rule_version_ids": outputs["rules"].get("event_rule_version_ids", []),
    }
    with get_db_session() as db:
        from src.apps.rule_governance.backend.rule_control.approval import (
            _active_event_rule_predecessors,
        )
        predecessors = []
        for version_id in args["change_policy_version_ids"]:
            candidate = db.get(ChangePolicyVersionRecord, version_id)
            if candidate:
                predecessors.extend(x.id for x in db.query(ChangePolicyVersionRecord).filter(
                    ChangePolicyVersionRecord.observation_type == candidate.observation_type,
                    ChangePolicyVersionRecord.status == "active", ChangePolicyVersionRecord.id != candidate.id).all())
        for version_id in args["event_rule_version_ids"]:
            candidate = db.get(EventRuleVersionRecord, version_id)
            if candidate:
                _, previous = _active_event_rule_predecessors(db, candidate_rule=db.get(EventRuleRecord, candidate.rule_id), candidate_version=candidate)
                predecessors.extend(version.id for _, version in previous)
        live = db.query(RuntimeBundleAssetRecord).join(CheckItemRuntimeBundleVersionRecord,
            RuntimeBundleAssetRecord.bundle_version_id == CheckItemRuntimeBundleVersionRecord.id).filter(
            CheckItemRuntimeBundleVersionRecord.status == "active",
            RuntimeBundleAssetRecord.asset_version_id.in_(predecessors)).first()
        if live:
            raise ValueError("规则将替换正在使用的运行包依赖，请先完成版本切换验证；自动发布已停止")
        if inputs["result_mode"] == "structured":
            item = next(x for x in CheckItemCatalogRepository().load_active().check_items if x.id == inputs["check_item_id"])
            manifest = direct_projection_manifest(outputs["projection"]["proposal"], check_item_id=item.id,
                check_item={"version_id": item.version_id, "canonical_contract": item.canonical_contract})
            produced = set(manifest["observation_types"])
            args["change_policy_version_ids"] = [version_id for version_id in args["change_policy_version_ids"]
                if db.get(ChangePolicyVersionRecord, version_id).observation_type in produced]
            args["event_rule_version_ids"] = [version_id for version_id in args["event_rule_version_ids"]
                if set(event_rule_observation_types(db.get(EventRuleVersionRecord, version_id))) & produced]
        # Read-only candidate simulation; normal assembly still requires Active assets.
        preflight = RuntimeBundleAssemblyPlanner(db).build(**args, _preview_candidates=True)
        blockers = [x for x in preflight.get("blockers", []) if x.get("code") != "implementation_not_active"]
        if blockers:
            raise ValueError("运行包预检未通过：" + "; ".join(str(x.get("message") or x.get("code")) for x in blockers))
    if inputs["result_mode"] == "structured":
        parser = outputs["parser"]
        repository = ParserTemplateRepository()
        template = repository.get_template(parser["template_id"])
        job = ParserJobRepository().get_job(inputs["unit"]["parser_job_id"], include_sanitized_payload=True)
        if (not template or template.sha256 != parser["sha256"]
                or not job or job.get("parser_spec_sha256") != parser["spec_digest"]
                or CommandAutopilotExecutor._sample_digest(job) != parser["sample_digest"]):
            raise ValueError("解析器或样本已变化，需要重新验证")
        proposal = outputs["projection"]["proposal"]
        if canonical_digest(proposal) != outputs["projection"]["digest"]:
            raise ValueError("字段转换已变化，需要重新验证")
        samples = parse_samples(template, job.get("samples") or [])
        item = next(x for x in CheckItemCatalogRepository().load_active().check_items if x.id == inputs["check_item_id"])
        for sample in samples:
            batches = execute_adapter(proposal, record_sets=sample["record_sets"],
                check_items={item.id: {"version_id": item.version_id, "canonical_contract": item.canonical_contract}},
                device_id=sample["device_id"], adapter_version_id="autopilot-release-check", require_pinned_check_item_versions=True)
            if not batches or any(x.quality_status != "complete" for x in batches):
                raise ValueError("标准数据最终验证未通过")
        # Do not invalidate a live runtime before a required replacement rollout.
        with get_db_session() as db:
            predecessors = db.query(ParserTemplate.id).filter(
                ParserTemplate.vendor == template.vendor, ParserTemplate.profile == template.profile,
                ParserTemplate.canonical_command == template.canonical_command,
                ParserTemplate.status == "active", ParserTemplate.id != template.id,
            ).all()
            live = db.query(RuntimeBundleAssetRecord).join(CheckItemRuntimeBundleVersionRecord,
                RuntimeBundleAssetRecord.bundle_version_id == CheckItemRuntimeBundleVersionRecord.id).filter(
                CheckItemRuntimeBundleVersionRecord.status == "active", RuntimeBundleAssetRecord.asset_type == "parser_template",
                RuntimeBundleAssetRecord.asset_version_id.in_([x[0] for x in predecessors]),
            ).first()
            if live:
                raise ValueError("此解析器将替换正在使用的运行包依赖，请先完成版本切换验证；自动发布已停止")
        checkpoint()
        repository.record_regression_validation(template.id, passed=True, sample_count=len(job["samples"]),
                                               passed_samples=len(job["samples"]), result_preview=samples)
        checkpoint()
        repository.activate_template(template.id, approved_by=actor, reason=reason,
                                     approval_kind="administrator_delegated_automation", automation_authorization=authorization)
        parser_ids = [template.id]
    checkpoint()
    rule_bundle = outputs["rules"].get("bundle_id")
    if rule_bundle:
        with get_db_session() as db:
            bundle = db.get(RuleGenerationJobRecord, rule_bundle)
            active = bool(bundle and bundle.status == "active")
        if not active:
            RuleAssetApprovalService().approve(rule_bundle, actor=actor, reason=reason,
                                               allow_same_actor=True, automation_authorization=authorization)
    checkpoint()
    # Catalog APIs continue to enforce all machine gates and rollout blockers.
    change = service.get_change(change_id)
    if change["status"] == "draft":
        service.submit(change_id, actor=actor, reason=reason, expected_impact_digest=impact["impact_digest"],
                       idempotency_key="auto-submit-" + state["run_id"])
    checkpoint()
    if change["status"] != "approved":
        service.decide(change_id, decision="approved", actor=actor, reason=reason,
                       expected_impact_digest=impact["impact_digest"], allow_same_actor=True,
                       idempotency_key="auto-approve-" + state["run_id"])
    checkpoint()
    preview = runtime_service.preview_assembly(**args)
    if preview.get("blockers"):
        raise ValueError("运行包验证未通过：" + "; ".join(str(x.get("message") or x.get("code")) for x in preview["blockers"]))
    runtime = runtime_service.assemble(**args, preview_digest=preview["preview_digest"], actor=actor)
    checkpoint()
    runtime = runtime_service.activate(runtime["version_id"], actor=actor, reason=reason,
                                      allow_same_actor=True, _audit_action="runtime_bundle_automatically_activated")
    outputs["runtime"] = {"version_id": runtime["version_id"], "status": runtime.get("version_status", runtime.get("status"))}
    with get_db_session() as db:
        import uuid
        db.add(AuditLogRecord(id=str(uuid.uuid4()), user_id=grant["by"], username=actor,
            action="command_autopilot_published", resource_type="command_autopilot", resource_id=state["unit_id"],
            detail={"run_id": state["run_id"], "authorization": grant, "runtime_version_id": runtime["version_id"]},
            status="success", created_at=datetime.now(timezone.utc)))
    return {"status": "completed", "stage": "review", "outputs": outputs}
