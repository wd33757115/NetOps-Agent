"""Apply the operator-approved health formula as immutable catalog revisions.

Default is read-only. --apply publishes only the described CheckItems and re-pins
their existing adapters; it never creates command-to-CheckItem associations.
"""

import argparse
import json

from src.core.check_items.catalog import CheckItemCatalogRepository, validate_check_item_definition
from src.core.check_items.health_policy_revision import TARGETS, revise_health_catalog


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    repository = CheckItemCatalogRepository()
    before = repository.load_active()
    revised = revise_health_catalog(before)
    previous = {item.id: item for item in before.check_items}
    for item in revised.check_items:
        if item.id in TARGETS:
            validate_check_item_definition(item)
            print(
                json.dumps(
                    {
                        "check_item": item.id,
                        "previous_version": previous[item.id].version,
                        "new_version": item.version,
                    },
                    ensure_ascii=False,
                )
            )
    old_implementations = {item.id: item for item in before.implementations}
    for item in revised.implementations:
        old = old_implementations[item.id]
        if old.version != item.version:
            assert old.check_item_ids == item.check_item_ids
            assert old.commands == item.commands
            print(
                json.dumps(
                    {
                        "adapter": item.id,
                        "previous_version": old.version,
                        "new_version": item.version,
                    },
                    ensure_ascii=False,
                )
            )
    if args.apply:
        print(repository.bootstrap(revised, actor="user-authorized-health-policy-revision"))


if __name__ == "__main__":
    main()
