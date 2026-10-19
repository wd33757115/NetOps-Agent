"""A draft of a check item links the adapters in force; the adapter still reads into the version it pins."""

from datetime import datetime, timezone

from src.infrastructure.db.models import (
    CheckItemImplementationLinkRecord,
    CheckItemVersionRecord,
    CollectionImplementationVersionRecord,
)
from tests.unit.test_check_item_catalog_governance import _environment, _session_scope

NOW = datetime(2026, 10, 17, 8, 0, tzinfo=timezone.utc)


def _copy(row, **changes):
    values = {column.name: getattr(row, column.key) for column in row.__table__.columns}
    return type(row)(**{**values, **changes})


def test_an_adapter_linked_to_a_draft_of_its_check_item_keeps_the_version_it_pins(tmp_path):
    repository, _service, session_factory = _environment(tmp_path)
    catalog = repository.load_active()
    adapter = next(item for item in catalog.implementations if len(item.check_item_ids) == 1)
    check_item = catalog.check_item(adapter.check_item_ids[0])
    with _session_scope(session_factory) as db:
        version = db.get(CollectionImplementationVersionRecord, adapter.version_id)
        version.adapter_spec_json = {
            **dict(version.adapter_spec_json or {}),
            "outputs": [{"check_item_id": check_item.id, "check_item_version_id": check_item.version_id}],
        }
        in_force = db.get(CheckItemVersionRecord, check_item.version_id)
        # Drafted, linked to the adapters in force so it can be tried, then cancelled or still waiting.
        for number, status in ((in_force.version + 1, "deprecated"), (in_force.version + 2, "draft")):
            db.add(_copy(in_force, id=f"draft-{number}", version=number, status=status, digest=f"{number:064d}",
                         activated_at=None))
            db.add(CheckItemImplementationLinkRecord(
                id=f"link-{number}", check_item_version_id=f"draft-{number}",
                implementation_version_id=adapter.version_id, priority=100, required=True, created_at=NOW))

    implementation, items = repository.get_implementation_version_context(adapter.version_id)

    assert [(item.id, item.version_id) for item in items] == [(check_item.id, check_item.version_id)]
    assert implementation.check_item_ids == (check_item.id,)
