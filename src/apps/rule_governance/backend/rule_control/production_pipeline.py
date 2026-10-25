"""Product-facing import progress for one offline CLI evidence job.

The source-job workflow deliberately stops once raw CLI evidence has been
imported and its commands are visible in the CheckItem catalog. Parser,
Projection, CommandAdapter and rule publication are owned by their respective
catalog workflows; they are not stages of an import task.
"""

from __future__ import annotations

from collections import Counter
from typing import Any
from urllib.parse import urlencode

from src.apps.rule_governance.backend.rule_control.command_asset_units import (
    RuleAssetCommandUnitService,
)
from src.apps.rule_governance.backend.rule_control.source_scope import require_rule_asset_source_job
from src.infrastructure.db.models import (
    CommandSnapshot,
    OfflinePatrolDeviceBindingRecord,
    OfflinePatrolPeriodRecord,
    OfflinePatrolSourceFileRecord,
)
from src.infrastructure.db.postgres import get_db_session


def _step(
    key: str,
    label: str,
    status: str,
    *,
    detail: str,
    counts: dict[str, int] | None = None,
) -> dict[str, Any]:
    return {
        "key": key,
        "label": label,
        "status": status,
        "detail": detail,
        "counts": counts or {},
        "optional": False,
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
    """Project a source job as evidence import, not as rule generation."""

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
            run_ids = [
                str(item.patrol_run_id)
                for item in periods
                if item.patrol_run_id
            ]
            source_count = (
                db.query(OfflinePatrolSourceFileRecord)
                .filter(OfflinePatrolSourceFileRecord.job_id == offline_job_id)
                .count()
            )
            sample_device_count = (
                db.query(OfflinePatrolDeviceBindingRecord)
                .filter(OfflinePatrolDeviceBindingRecord.job_id == offline_job_id)
                .count()
            )
            snapshot_rows = (
                db.query(CommandSnapshot.parse_status)
                .filter(CommandSnapshot.run_id.in_(run_ids))
                .all()
                if run_ids
                else []
            )
            parse_counts = Counter(
                str(status or "unknown") for (status,) in snapshot_rows
            )
            job_status = str(job.status)
            preview = dict(job.preview_json or {})
            preparation = dict((job.result_json or {}).get("preparation") or {})

        command_units = RuleAssetCommandUnitService().summary(offline_job_id)
        command_count = int(command_units.get("total") or 0)
        preview_totals = dict(preview.get("totals") or {})

        source_status = "completed" if source_count else "not_started"
        if job_status == "previewing":
            preview_status = "in_progress"
        elif preview:
            preview_status = "completed"
        elif job_status in {"failed", "cancelled"}:
            preview_status = "blocked"
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

        if import_status == "completed":
            catalog_status = "completed"
            catalog_detail = (
                f"已生成 {command_count} 条导入命令；"
                "请在待归类检查项中关联业务目标。"
                if command_count
                else "导入已完成，但未识别出可归类命令；请查看原始证据和预检结果。"
            )
        elif import_status == "blocked":
            catalog_status = "blocked"
            catalog_detail = "导入未完成，尚不能生成待归类命令。"
        else:
            catalog_status = "not_started"
            catalog_detail = "完成导入后，命令会自动进入检查项目录的待归类分组。"

        steps = [
            _step(
                "source",
                "导入 CLI 原始文件",
                source_status,
                detail=f"已关联 {source_count} 个文件",
                counts={"files": source_count},
            ),
            _step(
                "preview",
                "预检与命令识别",
                preview_status,
                detail=(
                    f"已识别 {sample_device_count} 个样本设备、"
                    f"{int(preview_totals.get('command_count') or 0)} 条命令"
                    if preview
                    else "上传后执行预检，识别设备、命令和输出质量。"
                ),
                counts={
                    "sample_devices": sample_device_count,
                    "commands": int(preview_totals.get("command_count") or 0),
                },
            ),
            _step(
                "ingest",
                "分割与脱敏 CLI 输出",
                import_status,
                detail=(f"已分割并脱敏 {preparation.get('sample_count', 0)} 条 CLI 输出"
                        if preparation.get("status") == "completed"
                        else f"已分割 {len(snapshot_rows)} 条 CLI 输出；旧任务的脱敏状态请在样本中核对"
                        if import_status == "completed" else "按命令分割输出并在本地脱敏，完成后进入待归类检查项。"),
                counts={"runs": len(run_ids), "snapshots": len(snapshot_rows)},
            ),
            _step(
                "catalog",
                "待归类检查项",
                catalog_status,
                detail=catalog_detail,
                counts={"commands": command_count, **dict(parse_counts)},
            ),
        ]

        source_job_url = "/skills/parser?" + urlencode(
            {
                "workspace": "production",
                "subtab": "source-jobs",
                "source_job_id": offline_job_id,
                "source_job_tab": "sources",
            }
        )
        catalog_url = "/skills/parser?" + urlencode(
            {
                "workspace": "production",
                "subtab": "catalog-management",
                "source_job_id": offline_job_id,
                "action": "classify_check_items",
            }
        )
        # Completed legacy imports can have their attachment metadata pruned.
        # The snapshots and pending commands are still actionable, so they must
        # proceed to the catalog instead of being sent back to file upload.
        if import_status == "completed" and command_count:
            next_action = _action(
                "open_check_item_catalog",
                "查看待归类命令",
                "在检查项目录中关联 CheckItem；随后按命令完成 Parser 与适配器。",
                catalog_url,
            )
        elif import_status == "completed":
            next_action = _action(
                "inspect_import_result",
                "查看导入结果",
                "本次导入未形成可归类命令，请核对预检与原始证据。",
                source_job_url,
            )
        elif not source_count:
            next_action = _action(
                "upload_sources",
                "上传 CLI 样本",
                "关联一个或多个 CLI 原始输出文件。",
                source_job_url,
            )
        elif preview_status != "completed":
            next_action = _action(
                "run_preview",
                "执行预检",
                "识别设备、命令和输出质量。",
                source_job_url,
            )
        elif import_status == "blocked":
            next_action = _action(
                "retry_import",
                "处理导入异常",
                "查看失败原因后重试样本导入。",
                source_job_url,
            )
        elif import_status != "completed":
            next_action = _action(
                "execute_import",
                "执行样本导入",
                "写入原始证据、样本 Snapshot 和已识别命令。",
                source_job_url,
            )

        completed_count = sum(item["status"] == "completed" for item in steps)
        if import_status == "completed":
            status = "completed"
        elif import_status == "blocked":
            status = "blocked"
        else:
            status = "in_progress"
        return {
            "offline_job_id": offline_job_id,
            "status": status,
            "progress_percent": round(completed_count / len(steps) * 100),
            "current_step": next_action["code"],
            "next_action": next_action,
            "secondary_actions": [],
            "steps": steps,
            "dataset_ids": dataset_ids,
            "template_ids": [],
            "context_ids": [],
            "bundle_ids": [],
            "command_units": command_units,
            "runtime_targets": [],
        }
