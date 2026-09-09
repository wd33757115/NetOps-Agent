"""Load only the selected patrol periods and their immutable check-item definitions."""

import json

from src.core.check_items.presentation import check_item_display_name


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
            for s in db.query(CommandSnapshot).filter(CommandSnapshot.run_id.in_(run_roles)).all()
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
        prepare_detail_records(snapshot, definitions, adapters)
    return {"snapshots": snapshots, "definitions": definitions}


def prepare_detail_records(snapshot, definitions, adapters):
    """Replay the exact pinned adapter over complete parser records, never state facts.

    This is historical compatibility, not a new alias mapper. A modern same-name
    parser passes through its pinned identity projection. Invalid old bindings are
    rejected; exporting must not quietly replace them with today's active adapter.
    """
    from src.core.check_items.command_adapter import CommandAdapterError, execute_adapter

    from .detail_workbook import record_sets

    snapshot["detail_records"] = {}
    snapshot["detail_errors"] = {}
    bindings = (snapshot.get("metadata_json") or {}).get("check_item_bindings") or []
    for binding in bindings:
        version = binding.get("check_item_version_id")
        definition = definitions.get(version)
        if not definition or definition["mode"] != "structured":
            continue
        adapter_id = binding.get("implementation_version_id")
        spec = adapters.get(adapter_id)
        try:
            if not spec or (spec.get("parser") or {}).get("template_id") != snapshot.get(
                "parser_template_id"
            ):
                raise CommandAdapterError("检查项关联的解析版本不可用")
            outputs = [
                o for o in spec.get("outputs", []) if o.get("check_item_id") == definition["id"]
            ]
            batches = execute_adapter(
                {**spec, "outputs": outputs},
                record_sets=record_sets(snapshot.get("structured_json")),
                check_items={
                    definition["id"]: {
                        "id": definition["id"],
                        "version_id": version,
                        "canonical_contract": definition["contract"],
                    }
                },
                device_id=snapshot["device_id"],
                adapter_version_id=adapter_id,
                observed_at=snapshot.get("observed_at"),
                require_pinned_check_item_versions=True,
            )
            records = []
            for batch in batches:
                if batch.quality_status not in {"complete", "valid_empty", "not_applicable"}:
                    raise CommandAdapterError("解析或字段校验未通过")
                for i, record in enumerate(batch.records):
                    # Keep all canonical columns, including fields not emitted as facts.
                    dims = batch.projection_dimensions[i] if batch.projection_dimensions else {}
                    by_dimensions = {}
                    common = {k: v for k, v in record.items() if k not in dims}
                    for field, dimensions in dims.items():
                        key = json.dumps(dimensions, sort_keys=True)
                        by_dimensions.setdefault(key, {**common, **dimensions})[field] = record.get(
                            field
                        )
                    records.extend(by_dimensions.values() if by_dimensions else [record])
            snapshot["detail_records"][version] = records
        except (CommandAdapterError, ValueError, TypeError):
            snapshot["detail_errors"][version] = (
                "解析记录与检查项定义不一致，请修正规则后重新分析。"
            )
