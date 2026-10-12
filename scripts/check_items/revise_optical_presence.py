"""Let a present transceiver that prints no presence token be assessed.

H3C single-lane optics print "The transceiver is absent" only when absent; a
present module prints its readings and nothing else. interface.optical_power
required `transceiver_present` on every record, so every present module was
rejected and its optical power never judged.

An empty port is not a fault either. The presence rule reports a module that
was present and is now absent; without a stated expected state the current
value alone stays undetermined instead of raising TransceiverAbsent on every
empty port.

Default is read-only. --apply publishes one interface.optical_power revision in
which the Value is optional and its current state needs an expected state, and
re-pins the adapters already bound to it; it creates no command-to-CheckItem
association. --restore publishes the opposite.
"""

import argparse
import json
from copy import deepcopy
from dataclasses import asdict

from src.apps.rule_governance.backend.check_items.catalog import (
    CheckItemCatalog,
    CheckItemCatalogRepository,
    build_check_item_definition,
    build_collection_implementation,
    validate_check_item_definition,
)

TARGET, FIELD = "interface.optical_power", "transceiver_present"


def revise(catalog: CheckItemCatalog, *, required: bool) -> CheckItemCatalog:
    items, changed = [], {}
    for item in catalog.check_items:
        if item.id == TARGET:
            raw = deepcopy(asdict(item))
            for row in raw["canonical_contract"]["observations"]:
                if row.get("canonical_field") != FIELD:
                    continue
                before = deepcopy(row)
                row["required"] = required
                constraints = dict(row.get("event_constraints") or {})
                if required:
                    constraints.pop("allow_initial_transition_fault", None)
                else:
                    constraints["allow_initial_transition_fault"] = False
                row["event_constraints"] = constraints
                if row != before:
                    raw["version"] += 1
                    item = build_check_item_definition(raw)
                    changed[item.id] = item.version_id
        items.append(item)
    known = {item.id for item in items}
    implementations = []
    for implementation in catalog.implementations:
        raw = deepcopy(asdict(implementation))
        raw["commands"] = list(raw["commands"])
        outputs = [
            output
            for output in (raw.get("adapter_spec") or {}).get("outputs", [])
            if output.get("check_item_id") in changed
            and output.get("check_item_version_id") != changed[output["check_item_id"]]
        ]
        for output in outputs:
            output["check_item_version_id"] = changed[output["check_item_id"]]
        if outputs:
            raw["version"] += 1
            implementation = build_collection_implementation(raw, known_check_item_ids=known)
        implementations.append(implementation)
    return CheckItemCatalog(
        catalog_version=catalog.catalog_version + bool(changed),
        check_items=tuple(items),
        implementations=tuple(implementations),
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--restore", action="store_true", help="make the Value required again")
    args = parser.parse_args()
    repository = CheckItemCatalogRepository()
    before = repository.load_active()
    revised = revise(before, required=args.restore)
    old_items = {item.id: item for item in before.check_items}
    old_implementations = {item.id: item for item in before.implementations}
    for item in revised.check_items:
        if item.version != old_items[item.id].version:
            validate_check_item_definition(item)
            print(json.dumps({"check_item": item.id, "previous_version": old_items[item.id].version,
                              "new_version": item.version, f"{FIELD}.required": args.restore}, ensure_ascii=False))
    for item in revised.implementations:
        old = old_implementations[item.id]
        if old.version != item.version:
            assert old.check_item_ids == item.check_item_ids and old.commands == item.commands
            print(json.dumps({"adapter": item.id, "previous_version": old.version, "new_version": item.version},
                             ensure_ascii=False))
    if args.apply:
        print(repository.bootstrap(revised, actor="optical-presence-revision"))


if __name__ == "__main__":
    main()
