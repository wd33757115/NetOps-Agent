"""Load only the selected patrol periods and their immutable check-item definitions."""

from src.core.check_items.presentation import check_item_display_name


def load_detail_source(job_id, *, owner_id=None, is_admin=False):
    """Authorize and load period IDs without building a health report or CLI preview."""
    from sqlalchemy.orm import load_only

    from src.infrastructure.db.models import OfflinePatrolJobRecord, OfflinePatrolPeriodRecord
    from src.infrastructure.db.postgres import get_db_session
    from src.platform.evidence.offline_jobs import OfflinePatrolJobService

    with get_db_session() as db:
        job = (
            db.query(OfflinePatrolJobRecord)
            .options(
                load_only(
                    OfflinePatrolJobRecord.id,
                    OfflinePatrolJobRecord.owner_id,
                    OfflinePatrolJobRecord.status,
                    OfflinePatrolJobRecord.purpose,
                )
            )
            .filter_by(id=job_id)
            .first()
        )
        OfflinePatrolJobService._assert_access(job, owner_id=owner_id, is_admin=is_admin)
        if job.purpose != "offline_patrol":
            raise KeyError("offline job not found in this workspace")
        if job.status != "completed":
            raise ValueError("任务完成后才能导出巡检数据")
        periods = (
            db.query(
                OfflinePatrolPeriodRecord.role,
                OfflinePatrolPeriodRecord.observed_at,
                OfflinePatrolPeriodRecord.patrol_run_id,
            )
            .filter_by(job_id=job_id)
            .order_by(OfflinePatrolPeriodRecord.observed_at)
            .all()
        )
        return {"periods": [dict(p._mapping) for p in periods]}


def load_detail_data(source):
    from src.infrastructure.db.models import (
        CheckItemRecord,
        CheckItemVersionRecord,
        CollectionImplementationVersionRecord,
        CommandSnapshot,
    )
    from src.infrastructure.db.postgres import get_db_session

    periods = source.get("periods") or []
    run_roles = {p["patrol_run_id"]: p["role"] for p in periods if p.get("patrol_run_id")}
    if not run_roles:
        raise ValueError("任务缺少采集周期，不能导出详细数据")
    if "current" not in run_roles.values() or set(run_roles.values()) - {"baseline", "current"}:
        raise ValueError("任务缺少有效的本期采集范围")
    if len(run_roles) != len(periods) or len(set(run_roles.values())) != len(periods):
        raise ValueError("采集周期不唯一，不能可靠比较详细数据")
    with get_db_session() as db:
        snapshots = [
            s.to_dict()
            for s in db.query(CommandSnapshot)
            .filter(CommandSnapshot.run_id.in_(run_roles))
            .order_by(
                CommandSnapshot.device_id, CommandSnapshot.canonical_command, CommandSnapshot.id
            )
            .all()
        ]
        for s in snapshots:
            s["period"] = run_roles[s["run_id"]]
        version_ids = {
            b.get("check_item_version_id")
            for s in snapshots
            for b in (s.get("metadata_json") or {}).get("check_item_bindings") or []
            if b.get("check_item_version_id")
        }
        version_ids.update(
            v
            for s in snapshots
            for v in (s.get("metadata_json") or {}).get("check_item_version_ids") or []
        )
        versions = (
            db.query(CheckItemVersionRecord)
            .filter(CheckItemVersionRecord.id.in_(version_ids))
            .all()
            if version_ids
            else []
        )
        names = {c.id: c.name for c in db.query(CheckItemRecord).all()}
        definitions = {
            v.id: {
                "id": v.check_item_id,
                "name": check_item_display_name(v.check_item_id, names.get(v.check_item_id)),
                "contract": v.canonical_contract_json,
                "mode": v.result_mode,
            }
            for v in versions
        }
        adapter_ids = {
            b.get("implementation_version_id")
            for s in snapshots
            for b in (s.get("metadata_json") or {}).get("check_item_bindings") or []
            if b.get("implementation_version_id")
        }
        adapters = (
            {
                v.id: v.adapter_spec_json
                for v in db.query(CollectionImplementationVersionRecord)
                .filter(CollectionImplementationVersionRecord.id.in_(adapter_ids))
                .all()
            }
            if adapter_ids
            else {}
        )
    for snapshot in snapshots:
        # Only use historical bindings to select the record set. Never replay a
        # projection or reject saved parser data because today's rules changed.
        selections = {}
        for binding in (snapshot.get("metadata_json") or {}).get("check_item_bindings") or []:
            spec = adapters.get(binding.get("implementation_version_id")) or {}
            outputs = [
                o
                for o in spec.get("outputs", [])
                if o.get("check_item_id") == binding.get("check_item_id")
            ]
            if outputs:
                selections[binding["check_item_version_id"]] = list(
                    dict.fromkeys(o.get("source_record_set", "default") for o in outputs)
                )
        snapshot["detail_record_sets"] = selections
    return {"snapshots": snapshots, "definitions": definitions}
