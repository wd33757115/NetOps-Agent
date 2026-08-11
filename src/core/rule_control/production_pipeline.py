"""Product-facing rule asset production progress for one offline evidence job."""

from __future__ import annotations

from collections import Counter
from typing import Any
from urllib.parse import urlencode

from src.core.check_items.runtime_bundle import CheckItemRuntimeBundleService
from src.core.rule_control.command_asset_units import RuleAssetCommandUnitService
from src.core.rule_control.source_scope import require_rule_asset_source_job
from src.infrastructure.db.models import (
    CommandSnapshot,
    OfflinePatrolDeviceBindingRecord,
    OfflinePatrolPeriodRecord,
    OfflinePatrolSourceFileRecord,
    ParserBackfillJobRecord,
    ParserTemplate,
    RuleAssetCommandUnitCheckItemRecord,
    RuleAssetCommandUnitRecord,
    RuleGenerationContextRecord,
    RuleGenerationJobRecord,
)
from src.infrastructure.db.postgres import get_db_session

ACTIVE_OFFLINE_STATUSES = {"previewing", "queued", "running"}
ACTIVE_BACKFILL_STATUSES = {"draft", "running", "pending_approval", "committing"}


def _step(
    key: str,
    label: str,
    status: str,
    *,
    detail: str,
    counts: dict[str, int] | None = None,
    optional: bool = False,
) -> dict[str, Any]:
    return {
        "key": key,
        "label": label,
        "status": status,
        "detail": detail,
        "counts": counts or {},
        "optional": optional,
    }


def _action(
    code: str,
    label: str,
    description: str,
    target_url: str,
) -> dict[str, str]:
    return {
        "code": code,
        "label": label,
        "description": description,
        "target_url": target_url,
    }


class RuleAssetProductionPipelineService:
    """Project existing operational facts into one guided production flow."""

    def get(self, offline_job_id: str) -> dict[str, Any]:
        with get_db_session() as db:
            job = require_rule_asset_source_job(db, offline_job_id)
            periods = (
                db.query(OfflinePatrolPeriodRecord)
                .filter(OfflinePatrolPeriodRecord.job_id == offline_job_id)
                .order_by(OfflinePatrolPeriodRecord.observed_at)
                .all()
            )
            dataset_ids = [str(item.dataset_id) for item in periods]
            run_ids = [str(item.patrol_run_id) for item in periods if item.patrol_run_id]
            source_count = (
                db.query(OfflinePatrolSourceFileRecord)
                .filter(OfflinePatrolSourceFileRecord.job_id == offline_job_id)
                .count()
            )
            binding_status_counts = Counter(
                str(status or "unmatched")
                for (status,) in db.query(OfflinePatrolDeviceBindingRecord.match_status)
                .filter(OfflinePatrolDeviceBindingRecord.job_id == offline_job_id)
                .all()
            )
            sample_device_count = sum(binding_status_counts.values())
            snapshots = (
                db.query(
                    CommandSnapshot.parse_status,
                    CommandSnapshot.parser_template_id,
                )
                .filter(CommandSnapshot.run_id.in_(run_ids))
                .all()
                if run_ids
                else []
            )
            parse_counts = Counter(str(status or "unknown") for status, _template_id in snapshots)
            template_ids = sorted(
                {
                    str(template_id)
                    for status, template_id in snapshots
                    if status == "parsed" and template_id
                }
            )
            active_template_ids = {
                str(item.id)
                for item in (
                    db.query(ParserTemplate)
                    .filter(ParserTemplate.id.in_(template_ids), ParserTemplate.status == "active")
                    .all()
                    if template_ids
                    else []
                )
            }
            source_refs = [f"parser_template:{template_id}" for template_id in active_template_ids]
            seed_rows = (
                db.query(RuleGenerationJobRecord)
                .filter(
                    RuleGenerationJobRecord.job_type == "OBSERVATION_CONTRACT",
                    RuleGenerationJobRecord.source_ref.in_(source_refs),
                )
                .all()
                if source_refs
                else []
            )
            seed_count = sum(
                1
                for row in seed_rows
                if (row.payload_json or {}).get("workflow") == "rule-asset-generation"
            )
            backfill_rows = (
                db.query(ParserBackfillJobRecord)
                .filter(ParserBackfillJobRecord.template_id.in_(active_template_ids))
                .all()
                if active_template_ids
                else []
            )
            backfill_counts = Counter(str(item.status) for item in backfill_rows)
            context_rows = (
                db.query(RuleGenerationContextRecord)
                .filter(RuleGenerationContextRecord.offline_job_id == offline_job_id)
                .order_by(RuleGenerationContextRecord.created_at.desc())
                .all()
            )
            context_ids = {str(item.id) for item in context_rows}
            context_count = len(context_ids)
            bundle_rows = (
                db.query(RuleGenerationJobRecord)
                .filter(
                    RuleGenerationJobRecord.job_type == "RULE_ASSET_BUNDLE",
                    RuleGenerationJobRecord.payload_json["context_id"].as_string().in_(context_ids),
                )
                .order_by(RuleGenerationJobRecord.created_at.desc())
                .all()
                if context_ids
                else []
            )
            bundles = [
                {
                    "id": str(item.id),
                    "status": str(item.status),
                    "validation_report": dict(item.validation_report_json or {}),
                }
                for item in bundle_rows
            ]
            job_status = str(job.status)
            preview = dict(job.preview_json or {})

        command_unit_service = RuleAssetCommandUnitService()
        command_units = command_unit_service.summary(offline_job_id)
        preview_totals = dict(preview.get("totals") or {})
        raw_parser_gap_count = sum(
            int(preview_totals.get(key) or parse_counts.get(key) or 0)
            for key in ("template_missing", "parse_failed", "identity_unknown")
        )
        parser_gap_count = (
            int((command_units.get("by_stage") or {}).get("parser_generation") or 0)
            if int(command_units.get("total") or 0)
            else raw_parser_gap_count
        )
        source_status = "completed" if source_count else "not_started"
        if job_status == "previewing":
            preview_status = "in_progress"
        elif preview:
            preview_has_identity = int(preview_totals.get("device_count") or 0) > 0
            preview_has_commands = int(preview_totals.get("command_count") or 0) > 0
            preview_status = (
                "completed" if preview_has_identity and preview_has_commands else "blocked"
            )
        else:
            preview_status = "not_started"
        if job_status in {"queued", "running"}:
            import_status = "in_progress"
        elif job_status == "completed" and run_ids:
            import_status = "completed"
        elif job_status in {"failed", "cancelled"}:
            import_status = "blocked"
        else:
            import_status = "not_started"
        command_unit_total = int(command_units.get("total") or 0)
        command_unit_completed = int(command_units.get("completed") or 0)
        if parser_gap_count:
            parser_status = "blocked"
        elif command_unit_total and command_unit_completed == command_unit_total:
            parser_status = "completed"
        elif template_ids and len(active_template_ids) == len(template_ids):
            parser_status = "completed"
        elif template_ids:
            parser_status = "in_progress"
        else:
            parser_status = "not_started"

        if any(status in ACTIVE_BACKFILL_STATUSES for status in backfill_counts):
            backfill_status = "in_progress"
        elif backfill_counts.get("completed"):
            backfill_status = "completed"
        elif active_template_ids:
            backfill_status = "available"
        else:
            backfill_status = "not_available"

        required_context_count = seed_count
        if context_count and (
            not required_context_count or context_count >= required_context_count
        ):
            context_status = "completed"
        elif context_count:
            context_status = "in_progress"
        elif seed_count:
            context_status = "ready"
        else:
            context_status = "not_started"

        bundle_counts = Counter(item["status"] for item in bundles)
        if bundle_counts.get("active"):
            generation_status = "completed"
        elif bundle_counts.get("pending_approval"):
            generation_status = "completed"
        elif bundle_counts.get("draft") or bundle_counts.get("validating"):
            generation_status = "in_progress"
        elif bundles:
            generation_status = "blocked"
        elif context_count:
            generation_status = "ready"
        else:
            generation_status = "not_started"

        accepted_bundles = [
            item
            for item in bundles
            if (
                ((item["validation_report"] or {}).get("governance") or {}).get(
                    "engineering_review"
                )
                or {}
            ).get("decision")
            == "accepted"
        ]
        if bundle_counts.get("active"):
            review_status = "completed"
            release_status = "completed"
        elif accepted_bundles:
            review_status = "completed"
            release_status = "ready"
        elif bundle_counts.get("pending_approval"):
            review_status = "ready"
            release_status = "not_started"
        elif bundles and generation_status == "blocked":
            review_status = "blocked"
            release_status = "blocked"
        else:
            review_status = "not_started"
            release_status = "not_started"

        with get_db_session() as db:
            unit_rows = (
                db.query(
                    RuleAssetCommandUnitRecord.id,
                    RuleAssetCommandUnitRecord.asset_mode,
                )
                .filter(
                    RuleAssetCommandUnitRecord.offline_job_id == offline_job_id
                )
                .all()
            )
            link_rows = [
                {
                    "command_unit_id": str(item.command_unit_id),
                    "check_item_id": str(item.check_item_id),
                    "check_item_version_id": str(item.check_item_version_id),
                    "implementation_id": str(item.implementation_id),
                    "implementation_version_id": str(
                        item.implementation_version_id
                    ),
                }
                for item in (
                    db.query(RuleAssetCommandUnitCheckItemRecord)
                    .join(
                        RuleAssetCommandUnitRecord,
                        RuleAssetCommandUnitRecord.id
                        == RuleAssetCommandUnitCheckItemRecord.command_unit_id,
                    )
                    .filter(
                        RuleAssetCommandUnitRecord.offline_job_id
                        == offline_job_id
                    )
                    .all()
                )
            ]

        required_unit_ids = {
            str(unit_id)
            for unit_id, asset_mode in unit_rows
            if str(asset_mode or "") not in {"ignored", "raw_hash"}
        }
        mapped_unit_ids = {
            item["command_unit_id"]
            for item in link_rows
            if item["command_unit_id"] in required_unit_ids
        }
        unmapped_unit_count = len(required_unit_ids - mapped_unit_ids)
        runtime_target_map = {
            (
                item["check_item_version_id"],
                item["implementation_version_id"],
            ): {
                "check_item_id": item["check_item_id"],
                "check_item_version_id": item["check_item_version_id"],
                "implementation_id": item["implementation_id"],
                "implementation_version_id": item[
                    "implementation_version_id"
                ],
            }
            for item in link_rows
            if item["command_unit_id"] in required_unit_ids
        }
        runtime_pairs = tuple(sorted(runtime_target_map))
        runtime_resolutions = CheckItemRuntimeBundleService().get_active_many(
            runtime_pairs
        )
        runtime_targets: list[dict[str, Any]] = []
        for pair in runtime_pairs:
            resolved = runtime_resolutions.get(pair) or {}
            blockers = [str(item) for item in resolved.get("blockers") or ()]
            bundle = resolved.get("bundle")
            target_status = (
                "invalid"
                if blockers
                else "ready"
                if bundle is not None
                else "missing"
            )
            runtime_targets.append(
                {
                    **runtime_target_map[pair],
                    "status": target_status,
                    "blocking_reasons": blockers,
                    "bundle_version_id": (
                        bundle.get("version_id") if bundle is not None else None
                    ),
                    "bundle_digest": (
                        bundle.get("digest") if bundle is not None else None
                    ),
                }
            )
        runtime_counts = Counter(item["status"] for item in runtime_targets)
        if release_status != "completed":
            runtime_status = "not_started"
        elif unmapped_unit_count:
            runtime_status = "blocked"
        elif not runtime_pairs:
            runtime_status = "completed" if not required_unit_ids else "blocked"
        elif runtime_counts.get("invalid"):
            runtime_status = "blocked"
        elif runtime_counts.get("ready") == len(runtime_pairs):
            runtime_status = "completed"
        else:
            runtime_status = "ready"

        steps = [
            _step(
                "source",
                "导入离线原始数据",
                source_status,
                detail=f"已关联 {source_count} 个文件",
                counts={"files": source_count},
            ),
            _step(
                "preview",
                "预检与样本识别",
                preview_status,
                detail=(
                    f"已识别 {sample_device_count} 个样本设备"
                    if sample_device_count
                    else "预检尚未识别出样本设备"
                ),
                counts={
                    "sample_devices": sample_device_count,
                },
            ),
            _step(
                "ingest",
                "执行结构化导入",
                import_status,
                detail=f"已形成 {len(run_ids)} 个权威周期 Run",
                counts={"runs": len(run_ids), "snapshots": len(snapshots)},
            ),
            _step(
                "parser",
                "Parser 资产就绪",
                parser_status,
                detail=(
                    f"已使用模板 {len(active_template_ids)}/{len(template_ids)} 个 active；"
                    f"仍有 {parser_gap_count} 条命令缺模板、解析失败或身份不明"
                    if parser_gap_count
                    else f"已使用模板 {len(active_template_ids)}/{len(template_ids)} 个 active"
                ),
                counts={
                    "templates": len(template_ids),
                    "active_templates": len(active_template_ids),
                    "gaps": parser_gap_count,
                },
            ),
            _step(
                "backfill",
                "历史数据回填",
                backfill_status,
                detail="可按模板选择历史未结构化 Snapshot 做 dry-run 和审批回填",
                counts=dict(backfill_counts),
                optional=True,
            ),
            _step(
                "context",
                "生成脱敏上下文",
                context_status,
                detail=f"{context_count}/{required_context_count} 个不可变上下文",
                counts={"seed_jobs": required_context_count, "contexts": context_count},
            ),
            _step(
                "generation",
                "LLM 生成与自动验证",
                generation_status,
                detail=f"已产生 {len(bundles)} 个候选资产包",
                counts=dict(bundle_counts),
            ),
            _step(
                "review",
                "工程师复核与修订",
                review_status,
                detail=f"{len(accepted_bundles)} 个候选包已人工确认",
                counts={
                    "accepted": len(accepted_bundles),
                    "pending": int(bundle_counts.get("pending_approval") or 0),
                },
            ),
            _step(
                "release",
                "审批发布",
                release_status,
                detail=f"{int(bundle_counts.get('active') or 0)} 个资产包已进入生产",
                counts={"active": int(bundle_counts.get("active") or 0)},
            ),
            _step(
                "runtime_bundle",
                "生产运行包装配",
                runtime_status,
                detail=(
                    f"{int(runtime_counts.get('ready') or 0)}/{len(runtime_pairs)} "
                    "个检查项与采集实现已有健康 Active RuntimeBundle；"
                    f"{unmapped_unit_count} 个规则生产单元尚未归类"
                ),
                counts={
                    "targets": len(runtime_pairs),
                    "ready": int(runtime_counts.get("ready") or 0),
                    "missing": int(runtime_counts.get("missing") or 0),
                    "invalid": int(runtime_counts.get("invalid") or 0),
                    "unmapped_units": unmapped_unit_count,
                },
            ),
        ]

        dataset_query = ",".join(dataset_ids)
        asset_url = "/skills/parser?" + urlencode(
            {
                "workspace": "generation",
                "subtab": "rule-generation",
                "source_job_id": offline_job_id,
                "offline_job_id": offline_job_id,
                "dataset_ids": dataset_query,
            }
        )
        parser_url = "/skills/parser?" + urlencode(
            {
                "workspace": "production",
                "subtab": "asset-production-units",
                "source_job_id": offline_job_id,
                "offline_job_id": offline_job_id,
                "dataset_ids": dataset_query,
                "action": "parser",
            }
        )
        backfill_url = "/skills/parser?" + urlencode(
            {
                "workspace": "today",
                "subtab": "backfills",
                "source_job_id": offline_job_id,
                "offline_job_id": offline_job_id,
            }
        )
        bundle_url = "/skills/parser?" + urlencode(
            {
                "workspace": "release",
                "subtab": "asset-bundles",
                "source_job_id": offline_job_id,
                "offline_job_id": offline_job_id,
            }
        )
        source_job_url = "/skills/parser?" + urlencode(
            {
                "workspace": "production",
                "subtab": "source-jobs",
                "source_job_id": offline_job_id,
                "source_job_tab": "sources",
            }
        )
        runtime_target = next(
            (
                item
                for item in runtime_targets
                if item["status"] != "ready"
            ),
            runtime_targets[0] if runtime_targets else None,
        )
        runtime_url_params = {
            "workspace": "runtime",
            "subtab": "coverage-backfill",
            "source_job_id": offline_job_id,
            "offline_job_id": offline_job_id,
        }
        if runtime_target is not None:
            runtime_url_params.update(
                {
                    "search": runtime_target["check_item_id"],
                    "check_item_version_id": runtime_target[
                        "check_item_version_id"
                    ],
                    "implementation_version_id": runtime_target[
                        "implementation_version_id"
                    ],
                }
            )
        runtime_url = "/skills/parser?" + urlencode(runtime_url_params)

        if not source_count:
            next_action = _action(
                "upload_sources", "上传离线样本", "关联一个或多个 CLI 原始输出文件", source_job_url
            )
        elif not preview:
            next_action = _action(
                "run_preview", "执行预检", "识别设备、命令和 Parser 缺口", source_job_url
            )
        elif job_status != "completed":
            next_action = _action(
                "execute_import", "处理离线样本", "写入权威 Snapshot 和结构化结果", source_job_url
            )
        elif parser_gap_count:
            next_action = _action(
                "resolve_parser_gaps",
                "处理 Parser 缺口",
                "生成、验证并激活缺失 Parser 模板",
                parser_url,
            )
        elif not seed_count and release_status != "completed":
            next_action = _action(
                "repair_seed_jobs", "检查规则种子", "确认模板激活已幂等创建规则生成种子", asset_url
            )
        elif not context_count or context_count < required_context_count:
            next_action = _action(
                "prepare_context", "生成脱敏上下文", "从本次样本任务构造不可变结构化样本", asset_url
            )
        elif not bundles:
            next_action = _action(
                "start_generation", "启动规则资产生成", "运行 LLM、确定性校验与历史回放", asset_url
            )
        elif review_status in {"ready", "blocked"}:
            next_action = _action(
                "review_candidates", "复核候选资产", "接受候选或创建工程师修订版本", bundle_url
            )
        elif release_status == "ready":
            next_action = _action(
                "approve_release", "审批发布", "审批后原子激活整套规则资产", bundle_url
            )
        elif runtime_status != "completed":
            next_action = _action(
                (
                    "classify_check_items"
                    if unmapped_unit_count
                    else "assemble_runtime_bundle"
                ),
                (
                    "归类检查项"
                    if unmapped_unit_count
                    else "校验并装配运行包"
                ),
                (
                    f"仍有 {unmapped_unit_count} 个生产单元未绑定检查项；"
                    "完成归类后才能形成生产运行包"
                    if unmapped_unit_count
                    else "按检查项与采集实现校验完整资产，并独立审批激活 RuntimeBundle"
                ),
                parser_url if unmapped_unit_count else runtime_url,
            )
        else:
            next_action = _action(
                "completed",
                "资产链路已完成",
                "当前离线证据对应规则资产和生产运行包均已进入生产",
                runtime_url,
            )

        required_steps = [item for item in steps if not item["optional"]]
        completed_count = sum(item["status"] == "completed" for item in required_steps)
        progress_percent = round(completed_count / len(required_steps) * 100)
        secondary_actions = []
        if backfill_status in {"available", "in_progress"}:
            secondary_actions.append(
                _action(
                    "historical_backfill",
                    "选择历史数据回填",
                    "先 dry-run，再审批提交；不会静默覆盖历史数据",
                    backfill_url,
                )
            )
        return {
            "offline_job_id": offline_job_id,
            "status": "completed"
            if runtime_status == "completed"
            else "blocked"
            if any(item["status"] == "blocked" for item in required_steps)
            else "in_progress",
            "progress_percent": progress_percent,
            "current_step": next_action["code"],
            "next_action": next_action,
            "secondary_actions": secondary_actions,
            "steps": steps,
            "dataset_ids": dataset_ids,
            "template_ids": template_ids,
            "context_ids": sorted(context_ids),
            "bundle_ids": [item["id"] for item in bundles],
            "command_units": command_units,
            "runtime_targets": runtime_targets,
        }
