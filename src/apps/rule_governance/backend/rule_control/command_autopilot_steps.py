"""Execute existing generators and validators for a human-selected command target."""
from __future__ import annotations

import copy
import re
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

from src.apps.rule_governance.backend.check_items.adapter_projection_migration import (
    generate_projection_candidate,
)
from src.apps.rule_governance.backend.check_items.canonical_rule_context import (
    CanonicalRuleContextService,
)
from src.apps.rule_governance.backend.check_items.catalog import CheckItemCatalogRepository
from src.apps.rule_governance.backend.check_items.catalog_governance import (
    CheckItemCatalogGovernanceService,
)
from src.apps.rule_governance.backend.parser.field_contract import build_parser_field_contract
from src.apps.rule_governance.backend.parser.identity import DeviceIdentity
from src.apps.rule_governance.backend.parser.policy import PolicyDecision
from src.apps.rule_governance.backend.parser.repository import (
    ParserJobRepository,
    ParserSampleRepository,
    ParserTemplateRepository,
)
from src.apps.rule_governance.backend.parser.runtime import ParserRuntime
from src.apps.rule_governance.backend.parser.worker import PARSER_EXECUTION_TASK
from src.core.check_items.assessment_policy import canonical_digest
from src.core.check_items.command_adapter import execute_adapter
from src.infrastructure.db.models import ParserTemplate
from src.infrastructure.db.postgres import get_db_session


class AutopilotStepError(ValueError):
    def __init__(self, message, outputs):
        super().__init__(message)
        self.outputs = outputs


def parse_samples(template, samples):
    """Use the production parser and every current sanitized sample, not an LLM preview."""
    runtime = ParserRuntime(
        registry=SimpleNamespace(resolve_active=lambda **_: template),
        policy=SimpleNamespace(decide=lambda **_: PolicyDecision(
            enabled=True, canonical_command=template.canonical_command,
            handling="structured", asset_mode="full_chain", category=template.category,
            capability=template.category, capability_status="candidate",
            template_scope="model", template_profile=template.profile,
        )),
        output_classifier=SimpleNamespace(classify=lambda *_, **__: SimpleNamespace(status="data", reason="candidate_validation")),
    )
    rows = []
    for sample in samples:
        if sample.get("sanitization_status") != "sanitized":
            raise ValueError("样本尚未完成脱敏")
        parsed = runtime.parse(
            identity=DeviceIdentity(vendor=template.vendor, model=template.model or template.profile,
                                    family=template.family, confidence=1.0, source="autopilot_validation"),
            command=template.canonical_command, raw_text=sample.get("sanitized_text") or "",
        )
        negative = sample.get("sample_role") == "negative"
        if negative:
            if parsed.structured_data:
                # Structured envelopes can hold only empty record sets.
                data = parsed.structured_data
                if not isinstance(data, dict) or any((data.get("record_sets") or {}).values()):
                    raise ValueError("负样本被解析成业务数据，请修正解析器")
            continue
        if parsed.parser_status not in {"success", "empty"}:
            raise ValueError(f"样本 {sample.get('id')} 解析失败：{parsed.failure_code or parsed.parser_status}")
        data = parsed.structured_data
        sets = data["record_sets"] if isinstance(data, dict) and data.get("format") == "netops.structured_record_sets/v1" else {"default": data or []}
        rows.append({"sample_id": sample.get("id"), "device_id": sample.get("device_id") or "sample-device", "record_sets": sets})
    if not rows:
        raise ValueError("缺少可验证的正样本；不会自动编造设备输出")
    return rows


class CommandAutopilotExecutor:
    def step(self, state, *, checkpoint):
        inputs = state["inputs"]
        outputs = copy.deepcopy(state["outputs"])
        stage = state["stage"]
        actor = state["actor"]
        checkpoint()
        if stage == "rules":
            if inputs["result_mode"] in {"normalized_diff", "hash_only"}:
                from src.core.check_items.text_comparison import TextComparisonProfileRepository
                TextComparisonProfileRepository().bootstrap(actor=actor)
            result = CanonicalRuleContextService().create_drafts(inputs["check_item_version_id"], actor=actor)
            outputs["rules"] = result
            return {"outputs": outputs, "stage": "parser"}
        if stage == "parser":
            if inputs["result_mode"] != "structured":
                outputs["parser"] = {"skipped": True}
                return {"outputs": outputs, "stage": "implementation"}
            return self._parser(state, outputs, checkpoint)
        if stage == "projection":
            return self._projection(state, outputs, checkpoint)
        if stage == "implementation":
            return self._implementation(state, outputs, checkpoint)
        from src.apps.rule_governance.backend.rule_control.command_autopilot_release import (
            release_command_task,
        )
        return release_command_task(state, checkpoint=checkpoint)

    def _parser(self, state, outputs, checkpoint):
        from src.apps.rule_governance.backend.parser.tasks import dispatch_ready_parser_jobs
        from src.core.celery_tasks.celery_app import celery
        inputs = state["inputs"]
        job_id = inputs["unit"]["parser_job_id"]
        if not job_id:
            raise ValueError("尚未形成解析任务，请刷新命令样本后继续")
        repository = ParserJobRepository()
        ParserSampleRepository().sanitize_pending_samples(job_id=job_id, limit=500)
        job = repository.get_job(job_id, include_sanitized_payload=True)
        if not job:
            raise ValueError("解析任务已不存在")
        retry_requested = outputs.pop("retry_requested", False)
        if retry_requested and job["status"] in {"failed", "cancelled"}:
            checkpoint()
            repository.retry_job(job_id, actor=state["actor"], actor_user_id=state.get("actor_user_id"),
                reason="人工继续自动制作，重试有界解析流程")
            job = repository.get_job(job_id, include_sanitized_payload=True)
        with get_db_session() as db:
            candidate = db.query(ParserTemplate).filter(
                ParserTemplate.source_job_id == job_id,
                ParserTemplate.status.in_(["active", "pending_approval"]),
            ).order_by(ParserTemplate.created_at.desc()).first()
            candidate_id = candidate.id if candidate else inputs["unit"].get("parser_template_id")
        if candidate_id:
            template = ParserTemplateRepository().get_template(candidate_id)
            evidence = dict(template.validation_json or {}) if template else {}
            source_digest = evidence.get("source_parser_spec_sha256") or evidence.get("parser_spec_sha256")
            if template and template.source_job_id == job_id and source_digest and source_digest != job.get("parser_spec_sha256"):
                template = None
            if template and template.status in {"active", "pending_approval"}:
                rows = parse_samples(template, job.get("samples") or [])
                outputs["parser"] = {"template_id": template.id, "sha256": template.sha256,
                                     "spec_digest": job.get("parser_spec_sha256"),
                                     "sample_digest": self._sample_digest(job), "sample_count": len(rows)}
                return {"outputs": outputs, "stage": "projection"}
        suggestion = (job.get("agent_state_json") or {}).get("parser_spec_suggestion") or {}
        if job["status"] in {"failed", "cancelled", "rejected"}:
            raise ValueError(job.get("last_error") or "解析未通过，请查看诊断、修改或重试解析任务")
        if suggestion.get("status") == "failed" and not retry_requested:
            raise ValueError(suggestion.get("error") or "AI 解析方案生成失败")
        wait_start = outputs.setdefault("parser_wait_started", datetime.now(timezone.utc).isoformat())
        if (datetime.now(timezone.utc) - datetime.fromisoformat(wait_start)).total_seconds() > 2400:
            raise ValueError("等待解析任务超过 40 分钟，请检查 AI Worker 后继续")
        if job.get("parser_spec_status") != "confirmed":
            if suggestion.get("status") == "ready":
                raise ValueError((suggestion.get("result") or {}).get("rationale") or "样本不足以形成解析方案")
            if suggestion.get("status") not in {"queued", "running"}:
                checkpoint()
                task_id = uuid.uuid4().hex
                repository.queue_parser_spec_suggestion(job_id, task_id=task_id, actor=state["actor"],
                    actor_user_id=state.get("actor_user_id"),
                    reason="人工关联后自动分析解析方案", check_item_id=inputs["check_item_id"])
                try:
                    celery.send_task(PARSER_EXECUTION_TASK,
                        kwargs={"job_id": job_id, "operation": "suggest_spec", "suggestion_task_id": task_id},
                        queue="netops.ai", task_id=task_id)
                except Exception as exc:
                    repository.record_parser_spec_suggestion(job_id, task_id=task_id, status="failed", error=str(exc))
                    raise
        elif job["status"] not in {"running", "validating"} and not job.get("celery_task_id"):
            checkpoint()
            dispatched = dispatch_ready_parser_jobs(job_id=job_id, limit=1,
                actor=state["actor"], actor_user_id=state.get("actor_user_id"))
            if not dispatched["dispatched"]:
                raise ValueError("解析尚不能执行：" + str(job.get("generation_readiness", {}).get("blockers") or dispatched["failed"]))
        return {"outputs": outputs, "stage": "parser"}

    @staticmethod
    def _sample_digest(job):
        return canonical_digest(sorted((str(x.get("id")), str(x.get("sanitized_hash")), str(x.get("sample_role")), x.get("expectation_json")) for x in job.get("samples", [])))

    def _projection(self, state, outputs, checkpoint):
        inputs = state["inputs"]
        parser = outputs["parser"]
        template = ParserTemplateRepository().get_template(parser["template_id"])
        job = ParserJobRepository().get_job(inputs["unit"]["parser_job_id"], include_sanitized_payload=True)
        if not template or template.status not in {"active", "pending_approval"} or template.sha256 != parser["sha256"]:
            raise ValueError("解析候选已修改或失效，请从解析步骤重新验证")
        if self._sample_digest(job) != parser["sample_digest"] or job.get("parser_spec_sha256") != parser["spec_digest"]:
            raise ValueError("样本或解析方案已修改，请从解析步骤重新验证")
        item = next(x for x in CheckItemCatalogRepository().load_active().check_items if x.id == inputs["check_item_id"])
        field_contract = build_parser_field_contract(validation=dict(template.validation_json or {}), template_text=template.template_text)
        if field_contract.get("status") != "aligned":
            raise ValueError("解析字段契约未通过：" + str(field_contract.get("errors")))
        samples = parse_samples(template, job.get("samples") or [])
        actual_sets = set(samples[0]["record_sets"])
        record_sets = {name: fields for name, fields in field_contract.get("template_record_sets", {}).items() if name in actual_sets}
        previous = outputs.get("projection") or {}
        failures = []
        def validate_execution(proposal):
            checkpoint()
            errors = []
            reports = []
            for sample in samples:
                try:
                    batches = execute_adapter(proposal, record_sets=sample["record_sets"],
                        check_items={item.id: {"version_id": item.version_id, "canonical_contract": item.canonical_contract}},
                        device_id=sample["device_id"], adapter_version_id="autopilot-preview", require_pinned_check_item_versions=True)
                    if not batches:
                        errors.append("没有标准数据输出")
                    for batch in batches:
                        if batch.quality_status != "complete":
                            errors.append(f"{sample['sample_id']}: {batch.quality_status}: {batch.errors}")
                    reports.append({"sample_id": sample["sample_id"], "batches": [batch.to_dict() for batch in batches]})
                except ValueError as exc:
                    errors.append(str(exc))
            return {"success": not errors, "errors": errors, "sample_reports": reports}
        for name, fields in list(record_sets.items())[:3]:
            checkpoint()
            params = {"selected_check_item": {"id": item.id, "version_id": item.version_id, "canonical_contract": item.canonical_contract},
                      "parser": {"template_id": template.id, "source_record_set": name, "source_fields": fields}, "max_agent_attempts": 3}
            if previous.get("manual"):
                result = generate_projection_candidate({**params, "mode": "projection_validate", "proposal": previous["proposal"]}, execution_validator=validate_execution)
            else:
                result = generate_projection_candidate(params, execution_validator=validate_execution)
                if not result.get("success"):
                    result = generate_projection_candidate({**params, "generation_provider": "llm"}, execution_validator=validate_execution)
            checkpoint()
            if not result.get("success"):
                outputs["projection"] = {"proposal": result.get("proposal"), "validation": result.get("validation"),
                                         "manual": previous.get("manual", False), "agent_loop": result.get("agent_loop")}
                failures.extend(result.get("validation", {}).get("errors", []))
                continue
            proposal = result["proposal"]
            reports = result["validation"]["execution"]["sample_reports"]
            outputs["projection"] = {"proposal": proposal, "validation": result["validation"],
                                     "sample_reports": reports, "manual": previous.get("manual", False),
                                     "agent_loop": result.get("agent_loop"), "digest": canonical_digest(proposal)}
            return {"stage": "implementation", "outputs": outputs}
        raise AutopilotStepError("标准数据验证未通过：" + "; ".join(map(str, failures or ["没有可用记录集"])), outputs)

    def _implementation(self, state, outputs, checkpoint):
        inputs, unit = state["inputs"], state["inputs"]["unit"]
        match = {"vendors": [unit["vendor"]]}
        if unit.get("models"):
            match["model_patterns"] = ["^" + re.escape(model) + "$" for model in unit["models"]]
        elif unit.get("model"):
            match["model_patterns"] = ["^" + re.escape(unit["model"]) + "$"]
        else:
            raise ValueError("样本缺少型号，无法确定适配范围，请补充设备身份")
        spec = (outputs.get("projection") or {}).get("proposal")
        payload = {
            "id": "ai-adapter-" + canonical_digest([unit["asset_key"], inputs["check_item_id"]])[:24],
            "name": f"{unit['vendor']} · {unit['canonical_command']}",
            "description": "依据人工关联的检查项和离线样本自动制作",
            "check_item_ids": [inputs["check_item_id"]], "match": match,
            "commands": [{"command": unit["canonical_command"], "priority": 100}],
            "collector": "cli", "parser_requirement": inputs["result_mode"],
            "template_scope": "model" if inputs["result_mode"] == "structured" else None,
            "selection_priority": 100,
            **({"adapter_spec": spec} if spec else {}),
        }
        checkpoint()
        service = CheckItemCatalogGovernanceService()
        change = service.create_implementation_draft(payload, actor=state["actor"],
            reason="自动完成样本验证并准备设备适配", idempotency_key="auto-adapter-" + canonical_digest(payload)[:48])
        outputs["implementation"] = change
        checkpoint()
        outputs["impact"] = service.preview_impact(change["id"])
        return {"stage": "review", "outputs": outputs}
