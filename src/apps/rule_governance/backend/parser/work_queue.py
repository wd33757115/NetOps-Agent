"""Unified, paginated work queue for parser and generated rule assets."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import String, case, cast, func, literal, or_, select, union_all

from src.apps.rule_governance.backend.parser.time_utils import database_clocks, elapsed_seconds
from src.infrastructure.db.models import (
    ParserBackfillJobRecord,
    ParserTemplateJob,
    RuleGenerationJobRecord,
)
from src.infrastructure.db.postgres import get_db_session

OPEN_STATUSES = {
    "parser": {"pending", "collecting", "running", "validating", "failed"},
    "backfill": {
        "draft", "running", "pending_approval", "validation_failed", "failed",
    },
    "rule_generation": {
        "draft", "running", "validating", "pending_approval", "failed",
    },
}


def _age_seconds(
    value: datetime | None,
    *,
    now_utc: datetime,
    now_local: datetime,
) -> int:
    return max(
        0,
        int(elapsed_seconds(value, now_utc=now_utc, now_local=now_local)),
    )


def _priority(status: str) -> str:
    if status in {"failed", "validation_failed", "rejected"}:
        return "high"
    if status == "pending_approval":
        return "high"
    if status in {"running", "collecting", "validating"}:
        return "normal"
    return "low"


def _next_action(task_type: str, status: str) -> str:
    actions = {
        "parser": {
            "pending": "补齐样本并调度生成",
            "collecting": "等待样本清洗完成",
            "running": "查看 Agent Loop 进度",
            "validating": "查看验证结果",
            "failed": "查看失败原因并重试",
        },
        "backfill": {
            "draft": "执行 Dry Run",
            "running": "查看回填进度",
            "pending_approval": "审批并提交回填",
            "validation_failed": "修复模板后重新验证",
            "failed": "查看失败原因并重试",
        },
        "rule_generation": {
            "draft": "补齐安全上下文并启动生成",
            "running": "查看生成进度",
            "validating": "提交验证或修订候选",
            "pending_approval": "进入资产包审批",
            "failed": "查看验证报告并修订",
        },
    }
    return actions.get(task_type, {}).get(status, "查看任务详情")


class ParserWorkQueueService:
    """Project heterogeneous jobs into one truthful operational queue."""

    def list_items(
        self,
        *,
        task_type: str | None = None,
        status: str | None = None,
        search: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> dict[str, Any]:
        page_size = max(1, min(int(limit), 200))
        page_offset = max(0, int(offset))
        normalized_type = str(task_type or "").strip() or None
        if normalized_type and normalized_type not in OPEN_STATUSES:
            raise ValueError(f"unsupported work queue task_type: {normalized_type}")
        pattern = f"%{str(search).strip()}%" if str(search or "").strip() else None

        with get_db_session() as db:
            now_utc, now_local = database_clocks(db)
            statements = []
            if normalized_type in {None, "parser"}:
                query = select(
                    ParserTemplateJob.id.label("task_id"),
                    literal("parser").label("task_type"),
                    ParserTemplateJob.status.label("status"),
                    ParserTemplateJob.created_at.label("created_at"),
                    ParserTemplateJob.updated_at.label("updated_at"),
                ).where(ParserTemplateJob.status.in_(OPEN_STATUSES["parser"]))
                if pattern:
                    query = query.where(or_(
                        ParserTemplateJob.id.ilike(pattern),
                        ParserTemplateJob.vendor.ilike(pattern),
                        ParserTemplateJob.model.ilike(pattern),
                        ParserTemplateJob.family.ilike(pattern),
                        ParserTemplateJob.canonical_command.ilike(pattern),
                    ))
                statements.append(query)
            if normalized_type in {None, "backfill"}:
                query = select(
                    ParserBackfillJobRecord.id.label("task_id"),
                    literal("backfill").label("task_type"),
                    ParserBackfillJobRecord.status.label("status"),
                    ParserBackfillJobRecord.created_at.label("created_at"),
                    ParserBackfillJobRecord.updated_at.label("updated_at"),
                ).where(ParserBackfillJobRecord.status.in_(OPEN_STATUSES["backfill"]))
                if pattern:
                    query = query.where(or_(
                        ParserBackfillJobRecord.id.ilike(pattern),
                        ParserBackfillJobRecord.template_id.ilike(pattern),
                        ParserBackfillJobRecord.requested_by.ilike(pattern),
                    ))
                statements.append(query)
            if normalized_type in {None, "rule_generation"}:
                query = select(
                    RuleGenerationJobRecord.id.label("task_id"),
                    literal("rule_generation").label("task_type"),
                    RuleGenerationJobRecord.status.label("status"),
                    RuleGenerationJobRecord.created_at.label("created_at"),
                    RuleGenerationJobRecord.updated_at.label("updated_at"),
                ).where(RuleGenerationJobRecord.status.in_(OPEN_STATUSES["rule_generation"]))
                if pattern:
                    query = query.where(or_(
                        RuleGenerationJobRecord.id.ilike(pattern),
                        RuleGenerationJobRecord.job_type.ilike(pattern),
                        RuleGenerationJobRecord.source_ref.ilike(pattern),
                        RuleGenerationJobRecord.asset_ref.ilike(pattern),
                        RuleGenerationJobRecord.asset_type.ilike(pattern),
                    ))
                statements.append(query)

            queue = union_all(*statements).subquery("parser_work_queue")
            filtered = select(queue)
            if status:
                filtered = filtered.where(queue.c.status == status)
            total = db.execute(
                select(func.count()).select_from(filtered.subquery())
            ).scalar_one()
            priority_rank = case(
                (queue.c.status.in_(("failed", "validation_failed", "rejected")), 0),
                (queue.c.status == "pending_approval", 1),
                (queue.c.status.in_(("running", "collecting", "validating")), 2),
                else_=3,
            )
            rows = db.execute(
                filtered.order_by(
                    priority_rank,
                    queue.c.created_at.asc(),
                    cast(queue.c.task_id, String),
                )
                .offset(page_offset)
                .limit(page_size)
            ).mappings().all()
            items = self._hydrate(
                db,
                rows,
                now_utc=now_utc,
                now_local=now_local,
            )
            return {
                "items": items,
                "total": int(total or 0),
                "limit": page_size,
                "offset": page_offset,
            }

    def _hydrate(
        self,
        db,
        rows,
        *,
        now_utc: datetime,
        now_local: datetime,
    ) -> list[dict[str, Any]]:
        ids_by_type: dict[str, list[str]] = {key: [] for key in OPEN_STATUSES}
        for row in rows:
            ids_by_type[str(row["task_type"])].append(str(row["task_id"]))
        parser_rows = {
            row.id: row for row in db.query(ParserTemplateJob).filter(
                ParserTemplateJob.id.in_(ids_by_type["parser"] or [""])
            ).all()
        }
        from src.apps.rule_governance.backend.parser.production_impact import (
            production_impact_for_jobs,
        )

        impact_scores = production_impact_for_jobs(list(parser_rows.values()))
        backfill_rows = {
            row.id: row for row in db.query(ParserBackfillJobRecord).filter(
                ParserBackfillJobRecord.id.in_(ids_by_type["backfill"] or [""])
            ).all()
        }
        rule_rows = {
            row.id: row for row in db.query(RuleGenerationJobRecord).filter(
                RuleGenerationJobRecord.id.in_(ids_by_type["rule_generation"] or [""])
            ).all()
        }
        output = []
        for row in rows:
            item_type = str(row["task_type"])
            item_id = str(row["task_id"])
            status = str(row["status"])
            title = item_id
            scope = "-"
            blocker = None
            target_workspace = "inbox"
            target_subtab = "jobs"
            if item_type == "parser":
                item = parser_rows[item_id]
                title = " · ".join(
                    filter(
                        None,
                        [item.vendor, item.model or item.family, item.canonical_command],
                    )
                )
                scope = f"{int(item.sample_count or 0)} 个样本"
                blocker = item.failure_code or item.last_error
            elif item_type == "backfill":
                item = backfill_rows[item_id]
                title = f"历史回填 · {item.template_id}"
                summary = dict(item.summary_json or {})
                scope = f"{int(summary.get('snapshot_count') or summary.get('total') or 0)} 个快照"
                blocker = item.last_error
                target_subtab = "backfills"
            else:
                item = rule_rows[item_id]
                title = " · ".join(
                    filter(
                        None,
                        [item.asset_type or item.job_type, item.asset_ref or item.source_ref],
                    )
                )
                payload = dict(item.payload_json or {})
                datasets = payload.get("dataset_ids") or []
                scope = f"{len(datasets)} 个数据集" if isinstance(datasets, list) else "规则资产"
                report = dict(item.validation_report_json or {})
                blockers = report.get("blockers") or report.get("errors") or []
                blocker = "; ".join(map(str, blockers[:3])) if isinstance(blockers, list) else None
                target_workspace = "generation"
                target_subtab = "rule-generation"
            impact = 0
            if item_type == "parser":
                impact = int(impact_scores.get(item_id) or 0)
            output.append({
                "id": item_id,
                "task_type": item_type,
                "status": status,
                "priority": _priority(status),
                "production_impact": impact,
                "title": title or item_id,
                "scope": scope,
                "blocker": blocker,
                "next_action": _next_action(item_type, status),
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
                "age_seconds": _age_seconds(
                    row["created_at"],
                    now_utc=now_utc,
                    now_local=now_local,
                ),
                "target_workspace": target_workspace,
                "target_subtab": target_subtab,
            })
        output.sort(
            key=lambda item: (
                0 if item["priority"] == "high" else 1 if item["priority"] == "normal" else 2,
                -int(item.get("production_impact") or 0),
                str(item.get("created_at") or ""),
            )
        )
        return output
