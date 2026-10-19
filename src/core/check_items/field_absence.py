"""Fields a device is known not to print, as found when fields were added to its adapter.

Rule governance keeps one row per adapter and check item while it tries to add
fields to an adapter (``parser_control_settings``, key ``adapter-fields:`` plus a
hash of the two). A field none of the adapter's stored outputs contain is kept
there as absent. The patrol reads it to say so where it lists the fields it
could not judge without: a device that does not print a value is a different
thing from a value the patrol lost.
"""

from __future__ import annotations

import hashlib
from typing import Any, Iterable

from src.infrastructure.db.models import ParserControlSetting

PREFIX = "adapter-fields:"


def task_key(implementation_id: str, check_item_id: str) -> str:
    digest = hashlib.sha1(f"{implementation_id}|{check_item_id}".encode("utf-8")).hexdigest()
    return PREFIX + digest


def absent_fields(db: Any, sources: Iterable[tuple[str, str]]) -> dict[tuple[str, str], set[str]]:
    """For each (check item, adapter) pair, the fields its device was found not to print."""

    pairs = {(str(check_item_id), str(adapter_id)) for check_item_id, adapter_id in sources if check_item_id and adapter_id}
    if not pairs:
        return {}
    keys = {task_key(adapter_id, check_item_id): (check_item_id, adapter_id) for check_item_id, adapter_id in pairs}
    found: dict[tuple[str, str], set[str]] = {pair: set() for pair in pairs}
    for row in db.query(ParserControlSetting).filter(ParserControlSetting.key.in_(list(keys))):
        found[keys[row.key]] = set(dict((row.value_json or {}).get("absent") or {}))
    return found


def device_absent(db: Any, sources_by_field: dict[str, Iterable[tuple[str, str]]]) -> list[str]:
    """The fields that every adapter they were missing from is known not to print."""

    named = {
        field: [(str(check_item_id or ""), str(adapter_id or "")) for check_item_id, adapter_id in pairs]
        for field, pairs in sources_by_field.items()
    }
    known = absent_fields(db, {pair for pairs in named.values() for pair in pairs})
    marked = []
    for field, pairs in named.items():
        # An observation whose adapter is unknown cannot vouch for the device; such a field is not marked.
        if not pairs or any(not check_item_id or not adapter_id for check_item_id, adapter_id in pairs):
            continue
        if all(field in known.get(pair, set()) for pair in pairs):
            marked.append(field)
    return sorted(marked)


__all__ = ["PREFIX", "absent_fields", "device_absent", "task_key"]
