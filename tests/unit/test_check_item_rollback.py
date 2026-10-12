"""Going back is publishing the earlier content again as a new version; a draft nobody wants can be withdrawn."""

import pytest

from src.core.check_items import CatalogGovernanceConflictError
from src.core.check_items.canonical import freeze_rule_semantics
from src.infrastructure.db.models import (
    CheckItemCatalogChangeRecord,
    CheckItemVersionRecord,
    EventRuleVersionRecord,
)
from tests.unit.test_check_item_catalog_governance import _check_item_payload, _environment, _session_scope
from tests.unit.test_check_item_retirement import _rule


def _source(repository):
    return next(item for item in repository.load_active().check_items if not item.id.startswith("topology."))


def _publish(service, source, key, **changes):
    draft = service.create_check_item_draft(
        _check_item_payload(source, **changes), actor="engineer-a", idempotency_key=f"rollback-test-{key}")
    impact = service.preview_impact(draft["id"])
    service.submit(draft["id"], actor="engineer-a", expected_impact_digest=impact["impact_digest"],
                   idempotency_key=f"rollback-test-submit-{key}")
    service.decide(draft["id"], decision="approved", actor="release-admin-b",
                   expected_impact_digest=impact["impact_digest"], idempotency_key=f"rollback-test-approve-{key}")
    return draft


def test_a_rollback_drafts_the_previous_release_again_as_a_new_version(tmp_path):
    repository, service, session_factory = _environment(tmp_path)
    source = _source(repository)
    _publish(service, source, "second", description="The description that was right.")
    _publish(service, source, "third", description="A description that turned out wrong.")
    assert repository.load_active().check_item(source.id).description == "A description that turned out wrong."

    rollback = service.preview_rollback(source.id, actor="engineer-a")

    assert (rollback["current_version"], rollback["restores_version"], rollback["new_version"]) == (
        source.version + 2, source.version + 1, source.version + 3)
    assert rollback["restores_wording"] is True
    assert repository.load_active().check_item(source.id).version == source.version + 2   # nothing switched yet
    with _session_scope(session_factory) as db:
        change = db.get(CheckItemCatalogChangeRecord, rollback["change_id"])
        assert (change.status, change.creation_reason) == ("draft", f"回退到 v{source.version + 1}")
        # A seeded first version is stored as written; every draft is stored with its semantics frozen.
        assert change.candidate_payload_json["canonical_contract"] == freeze_rule_semantics(source.canonical_contract)

    service.submit(rollback["change_id"], actor="engineer-a", expected_impact_digest=rollback["impact_digest"],
                   idempotency_key="rollback-test-submit-back")
    service.decide(rollback["change_id"], decision="approved", actor="engineer-a", allow_same_actor=True,
                   expected_impact_digest=rollback["impact_digest"], idempotency_key="rollback-test-approve-back")

    restored = repository.load_active().check_item(source.id)
    assert (restored.version, restored.description) == (source.version + 3, "The description that was right.")
    assert restored.canonical_contract == freeze_rule_semantics(source.canonical_contract)
    # Going back once more returns to the version that was just left.
    again = service.preview_rollback(source.id, actor="engineer-a")
    assert again["restores_version"] == source.version + 2


def test_a_version_that_was_seeded_gives_back_its_definition_but_not_its_wording(tmp_path):
    repository, service, session_factory = _environment(tmp_path)
    source = _source(repository)
    _publish(service, source, "second", description="Reworded since.")

    rollback = service.preview_rollback(source.id, actor="engineer-a")

    # Nothing recorded what the seeded version was called, so the wording in use stays.
    assert (rollback["restores_version"], rollback["restores_wording"]) == (source.version, False)
    with _session_scope(session_factory) as db:
        payload = db.get(CheckItemCatalogChangeRecord, rollback["change_id"]).candidate_payload_json
        assert payload["description"] == "Reworded since."
        assert payload["canonical_contract"] == freeze_rule_semantics(source.canonical_contract)


def test_a_check_item_that_was_released_only_once_has_nothing_to_go_back_to(tmp_path):
    repository, service, _session_factory = _environment(tmp_path)
    with pytest.raises(CatalogGovernanceConflictError, match="无法回退"):
        service.preview_rollback(_source(repository).id, actor="engineer-a")


def test_a_withdrawn_draft_changes_nothing_and_is_never_a_rollback_target(tmp_path):
    repository, service, session_factory = _environment(tmp_path)
    source = _source(repository)
    _publish(service, source, "second", description="Second release.")
    draft = service.create_check_item_draft(
        _check_item_payload(source, description="Abandoned."), actor="engineer-a", idempotency_key="rollback-test-abandoned")
    with _session_scope(session_factory) as db:
        _rule(db, draft["version_id"], "DraftedForThePreview", status="draft")

    discarded = service.discard_draft(draft["id"], actor="engineer-a")

    assert discarded["status"] == "cancelled"
    assert service.discard_draft(draft["id"], actor="engineer-a")["status"] == "cancelled"   # asking twice is fine
    assert repository.load_active().check_item(source.id).description == "Second release."
    with _session_scope(session_factory) as db:
        version = db.get(CheckItemVersionRecord, draft["version_id"])
        assert (version.status, version.activated_at) == ("deprecated", None)
        # The rule drafted for its preview can never be approved now.
        assert db.get(EventRuleVersionRecord, "rule-DraftedForThePreview-v1").status == "rejected"
    assert service.preview_rollback(source.id, actor="engineer-a")["restores_version"] == source.version


def test_only_a_draft_can_be_withdrawn(tmp_path):
    repository, service, _session_factory = _environment(tmp_path)
    source = _source(repository)
    published = _publish(service, source, "second", description="Second release.")
    with pytest.raises(CatalogGovernanceConflictError, match="cannot be discarded from approved"):
        service.discard_draft(published["id"], actor="engineer-a")
    with pytest.raises(ValueError, match="not found"):
        service.discard_draft("missing-change", actor="engineer-a")
