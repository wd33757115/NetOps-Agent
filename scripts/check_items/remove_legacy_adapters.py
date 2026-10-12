"""Withdraw command adapters that can no longer take effect.

A legacy adapter here is one that serves patrol check items, carries no
projection definition (it predates command-adapter/v2) and has no active
runtime bundle for any of them, so no device has been checked through it. The
withdrawal is the catalog's own logical delete: versions are deprecated and the
action is audited; nothing is erased.

Default is read-only and lists the adapters. --apply withdraws them.
"""

import argparse
import json

from src.apps.rule_governance.backend.check_items.catalog import CheckItemCatalogRepository
from src.apps.rule_governance.backend.check_items.catalog_governance import (
    CheckItemCatalogGovernanceService,
)
from src.apps.rule_governance.backend.check_items.system_items import (
    system_check_item_ids,
    system_implementation_ids,
)
from src.infrastructure.db.models import CheckItemRuntimeBundleVersionRecord
from src.infrastructure.db.postgres import get_db_session


def legacy_adapters():
    catalog = CheckItemCatalogRepository().load_active()
    current = {item.id: item.version_id for item in catalog.check_items}
    system_items, system_adapters = system_check_item_ids(), system_implementation_ids()
    with get_db_session() as db:
        in_force = {
            (str(row.check_item_version_id), str(row.implementation_version_id))
            for row in db.query(
                CheckItemRuntimeBundleVersionRecord.check_item_version_id,
                CheckItemRuntimeBundleVersionRecord.implementation_version_id,
            ).filter(CheckItemRuntimeBundleVersionRecord.status == "active")
        }
    for adapter in catalog.implementations:
        patrol_items = [item_id for item_id in adapter.check_item_ids if item_id not in system_items]
        if not patrol_items or adapter.id in system_adapters or (adapter.adapter_spec or {}).get("outputs"):
            continue
        if any((current[item_id], adapter.version_id) in in_force for item_id in adapter.check_item_ids):
            continue
        yield adapter, patrol_items


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--actor", default="legacy-adapter-cleanup")
    parser.add_argument("--reason", default="清理遗留适配器：没有投影定义，也没有生效的运行包")
    args = parser.parse_args()

    service = CheckItemCatalogGovernanceService()
    count = 0
    for adapter, patrol_items in legacy_adapters():
        count += 1
        result = None
        if args.apply:
            result = service.delete_implementation(
                adapter.id,
                actor=args.actor,
                reason=args.reason,
                idempotency_key=f"legacy-adapter-cleanup:{adapter.version_id}",
            )
        print(json.dumps({
            "adapter": adapter.id, "name": adapter.name, "version": adapter.version,
            "check_items": patrol_items,
            "withdrawn": bool(result and result.get("success")),
        }, ensure_ascii=False))
    print(json.dumps({"adapters": count, "applied": args.apply}))


if __name__ == "__main__":
    main()
