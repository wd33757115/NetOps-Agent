"""A new check item version takes its adapters with it, or does not take over at all."""

from copy import deepcopy
from datetime import datetime, timezone

import pytest

from src.apps.rule_governance.backend.check_items.release import CheckItemReleaseBlocked, _repin_adapters
from src.infrastructure.db.models import (
    CheckItemImplementationLinkRecord,
    CheckItemRecord,
    CheckItemVersionRecord,
    CollectionImplementationVersionRecord,
)
from tests.unit.test_check_item_catalog_governance import _check_item_payload, _environment, _session_scope
from tests.unit.test_check_item_value_use import field


def _projection_adapter(repository):
    catalog = repository.load_active()
    adapter = next(item for item in catalog.implementations if (item.adapter_spec or {}).get("outputs"))
    return adapter, catalog.check_item(adapter.adapter_spec["outputs"][0]["check_item_id"])


def _switch(session_factory, service, source, **changes):
    """Draft a revision and move the adapters onto it the way the approval does, without committing."""

    draft = service.create_check_item_draft(
        _check_item_payload(source, **changes), actor="engineer-a", idempotency_key=f"release-{sorted(changes)}"
    )
    now = datetime.now(timezone.utc)
    with _session_scope(session_factory) as db:
        root = db.get(CheckItemRecord, source.id)
        previous = db.get(CheckItemVersionRecord, source.version_id)
        version = db.get(CheckItemVersionRecord, draft["version_id"])
        previous.status = "deprecated"
        db.flush()
        version.status = "active"
        db.flush()
        moved = _repin_adapters(db, root, version, previous, actor="release-admin", now=now)
        pins = {
            str(row.id): [
                output.get("check_item_version_id")
                for output in (row.adapter_spec_json or {}).get("outputs", [])
                if output.get("check_item_id") == source.id
            ]
            for row in db.query(CollectionImplementationVersionRecord).filter(
                CollectionImplementationVersionRecord.id.in_([item["version_id"] for item in moved])
            )
        }
        statuses = {
            str(row.id): row.status
            for row in db.query(CollectionImplementationVersionRecord).filter(
                CollectionImplementationVersionRecord.id.in_(
                    [item["previous_version_id"] for item in moved] + [item["version_id"] for item in moved]
                )
            )
        }
        links = {
            str(row.implementation_version_id)
            for row in db.query(CheckItemImplementationLinkRecord).filter(
                CheckItemImplementationLinkRecord.check_item_version_id == draft["version_id"]
            )
        }
        return draft, moved, pins, statuses, links


def test_projection_adapters_get_a_version_pinned_to_the_new_check_item_version(tmp_path):
    repository, service, session_factory = _environment(tmp_path)
    adapter, source = _projection_adapter(repository)

    draft, moved, pins, statuses, links = _switch(session_factory, service, source, description="复核后的说明")

    repinned = next(item for item in moved if item["implementation_id"] == adapter.id)
    assert repinned["repinned"] and repinned["version_id"] != repinned["previous_version_id"]
    assert pins[repinned["version_id"]] == [draft["version_id"]]
    assert statuses[repinned["previous_version_id"]] == "deprecated"
    assert statuses[repinned["version_id"]] == "active"
    assert {item["version_id"] for item in moved} <= links
    assert adapter.id in {
        item.id for item in repository.load_active().implementations if source.id in item.check_item_ids
    }


def test_a_removed_field_is_dropped_from_the_adapter_without_anyone_editing_it(tmp_path):
    repository, service, session_factory = _environment(tmp_path)
    adapter, source = _projection_adapter(repository)
    optional = {row["canonical_field"] for row in source.canonical_contract["observations"] if row.get("required") is False}
    mapped = sorted(set(adapter.adapter_spec["outputs"][0]["projection"]["fields"]) & optional)[0]
    contract = deepcopy(source.canonical_contract)
    contract["observations"] = [row for row in contract["observations"] if row.get("canonical_field") != mapped]

    _draft, moved, pins, _statuses, _links = _switch(session_factory, service, source, canonical_contract=contract)

    repinned = next(item for item in moved if item["implementation_id"] == adapter.id)
    assert repinned["repinned"] and repinned["dropped_fields"] == [mapped]
    assert pins[repinned["version_id"]]


def test_a_required_field_the_adapter_does_not_provide_blocks_the_switch(tmp_path):
    repository, service, session_factory = _environment(tmp_path)
    adapter, source = _projection_adapter(repository)
    contract = deepcopy(source.canonical_contract)
    contract["observations"].append(field("vendor_note", "text", "evidence"))

    with pytest.raises(CheckItemReleaseBlocked, match="不提供必填字段 vendor_note") as blocked:
        _switch(session_factory, service, source, canonical_contract=contract)

    assert adapter.name in str(blocked.value)
    still_active = {item.id: item.version_id for item in repository.load_active().implementations}
    assert still_active[adapter.id] == adapter.version_id


def test_a_field_given_a_dimension_the_adapter_does_not_output_blocks_the_switch_and_says_so(tmp_path):
    repository, service, session_factory = _environment(tmp_path)
    adapter, source = _projection_adapter(repository)
    bound = adapter.adapter_spec["outputs"][0]["projection"]["fields"]
    contract = deepcopy(source.canonical_contract)
    identity = [name for name in contract["identity_dimensions"] if name != "device_id"]
    # What an early imported check item looks like after the old form filled its fields' dimensions in.
    moved = sorted(
        row["canonical_field"]
        for row in contract["observations"]
        if row["canonical_field"] in bound and not row.get("dimensions") and not bound[row["canonical_field"]].get("dimensions")
    )
    assert identity and moved
    types = set()
    for row in contract["observations"]:
        if row["canonical_field"] in moved:
            row["dimensions"] = list(identity)
            types.add(row["observation_type"])
            if "semantic_definition" in row:
                row["semantic_definition"]["dimensions"] = list(identity)
    # Its frozen definitions name the identity as a dimension, which is where the form took it from.
    for definition in contract["rule_semantics_snapshot"]["observations"]:
        if definition["id"] in types:
            definition["dimensions"] = list(identity)

    with pytest.raises(CheckItemReleaseBlocked) as blocked:
        _switch(session_factory, service, source, canonical_contract=contract)

    reason = str(blocked.value)
    assert f"{len(moved)} 个字段（{'、'.join(moved[:3])}" in reason
    assert f"在新版本里按 {'、'.join(sorted(identity))} 区分，适配器输出时不分维度" in reason
    assert "撤销这份草稿后重新修改" in reason
    # Nothing was renamed and the template is fine: the reason must not send anyone to fix it.
    assert "TextFSM" not in reason and "改名" not in reason
    assert adapter.name in reason
    still_active = {item.id: item.version_id for item in repository.load_active().implementations}
    assert still_active[adapter.id] == adapter.version_id
