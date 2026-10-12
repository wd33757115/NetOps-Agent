from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.core.check_items import (
    CatalogGovernanceConflictError,
    CheckItemCatalogGovernanceService,
    CheckItemCatalogRepository,
    load_catalog,
)
from src.infrastructure.db.models import (
    AuditLogRecord,
    Base,
    ChangePolicyRecord,
    ChangePolicyVersionRecord,
    CheckItemCatalogChangeRecord,
    CheckItemImplementationLinkRecord,
    CheckItemRecord,
    CheckItemRuntimeBundleVersionRecord,
    CheckItemRuntimeBundleRecord,
    CheckItemVersionRecord,
    CollectionImplementationRecord,
    CollectionImplementationVersionRecord,
    EventRuleRecord,
    EventRuleVersionRecord,
    NetworkEventRecord,
    ParserTemplate,
)


@contextmanager
def _session_scope(session_factory):
    session = session_factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _environment(tmp_path, *, devices=None):
    engine = create_engine(f"sqlite:///{tmp_path / 'catalog-governance.db'}")
    Base.metadata.create_all(
        engine,
        tables=[
            AuditLogRecord.__table__,
            CheckItemRecord.__table__,
            CheckItemVersionRecord.__table__,
            CollectionImplementationRecord.__table__,
            CollectionImplementationVersionRecord.__table__,
            CheckItemImplementationLinkRecord.__table__,
            CheckItemCatalogChangeRecord.__table__,
            CheckItemRuntimeBundleRecord.__table__,
            CheckItemRuntimeBundleVersionRecord.__table__,
            ParserTemplate.__table__,
            # Deleting a check item retires its rules and closes their events.
            ChangePolicyRecord.__table__,
            ChangePolicyVersionRecord.__table__,
            EventRuleRecord.__table__,
            EventRuleVersionRecord.__table__,
            NetworkEventRecord.__table__,
        ],
    )
    session_factory = sessionmaker(bind=engine)
    scope = lambda: _session_scope(session_factory)
    repository = CheckItemCatalogRepository(session_scope=scope)
    repository.bootstrap(load_catalog())
    service = CheckItemCatalogGovernanceService(
        session_scope=scope,
        inventory_loader=lambda: list(devices or []),
    )
    return repository, service, session_factory


def _implementation_payload(implementation, **overrides):
    payload = {
        "id": implementation.id,
        "name": implementation.name,
        "description": implementation.description,
        "check_item_ids": list(implementation.check_item_ids),
        "match": dict(implementation.match),
        "commands": [
            {"command": item.command, "priority": item.priority}
            for item in implementation.commands
        ],
        "collector": implementation.collector,
        "parser_requirement": implementation.parser_requirement,
        "template_scope": implementation.template_scope,
        "output_quality_policy": dict(implementation.output_quality_policy),
        "selection_priority": implementation.selection_priority,
    }
    payload.update(overrides)
    return payload


def _check_item_payload(check_item, **overrides):
    payload = {
        "id": check_item.id,
        "name": check_item.name,
        "category": check_item.category,
        "description": check_item.description,
        "result_mode": check_item.result_mode,
        "assessment_mode": check_item.assessment_mode,
        "canonical_contract": dict(check_item.canonical_contract),
        "report_projection": check_item.report_projection,
        "applicability": dict(check_item.applicability),
        "freshness_policy": dict(check_item.freshness_policy),
        "empty_result_policy": dict(check_item.empty_result_policy),
        "coverage_policy": dict(check_item.coverage_policy),
    }
    payload.update(overrides)
    return payload


def test_collection_implementation_must_follow_check_item_result_mode(tmp_path):
    repository, service, _session_factory = _environment(tmp_path)
    source = next(
        item
        for item in repository.load_active().implementations
        if item.id == "h3c-cpu-usage"
    )

    with pytest.raises(ValueError, match="must match.*structured"):
        service.create_implementation_draft(
            _implementation_payload(
                source,
                parser_requirement="evidence_only",
            ),
            actor="engineer-a",
            idempotency_key="catalog-invalid-parser-requirement",
        )


def test_create_publish_and_soft_delete_check_item(tmp_path):
    repository, service, _session_factory = _environment(tmp_path)
    source = next(
        item for item in repository.load_active().check_items
        if item.id == "device.version"
    )
    draft = service.create_check_item_draft(
        _check_item_payload(
            source,
            id="device.software_audit",
            name="Device software audit",
            description="Preserve approved software identity for audit.",
            report_projection="auto",
        ),
        actor="engineer-a",
        idempotency_key="catalog-create-device-software-audit",
    )

    assert draft["asset_kind"] == "check_item"
    assert draft["candidate"]["check_item_id"] == "device.software_audit"
    preview = service.preview_impact(draft["id"])
    assert preview["asset_kind"] == "check_item"
    assert {item["code"] for item in preview["warnings"]} == {
        "check_item_has_no_collection_implementation",
        "check_item_requires_profile_selection",
    }
    service.submit(
        draft["id"],
        actor="engineer-a",
        expected_impact_digest=preview["impact_digest"],
        idempotency_key="catalog-submit-device-software-audit",
    )
    service.decide(
        draft["id"],
        decision="approved",
        actor="release-admin-b",
        expected_impact_digest=preview["impact_digest"],
        idempotency_key="catalog-approve-device-software-audit",
    )

    created = repository.load_active().check_item("device.software_audit")
    assert created is not None
    assert created.version == 1
    deleted = service.delete_check_item(
        created.id,
        actor="release-admin-b",
        idempotency_key="catalog-delete-device-software-audit",
    )
    assert deleted["status"] == "deprecated"
    assert repository.load_active().check_item(created.id) is None


def test_local_semantic_draft_publishes_and_round_trips_to_rules_and_report(tmp_path):
    from tests.unit.test_local_check_item_semantics import contract, candidates
    from src.core.check_items.report_projection import build_report_projection_snapshot

    repository, service, _ = _environment(tmp_path)
    source = repository.load_active().check_item('device.version')
    draft = service.create_check_item_draft(
        _check_item_payload(source, id='custom.power', name='Custom power', assessment_mode='health',
                            canonical_contract=contract(), report_projection='auto'),
        actor='engineer-a', idempotency_key='local-semantic-create-test')
    preview = service.preview_impact(draft['id'])
    service.submit(draft['id'], actor='engineer-a', expected_impact_digest=preview['impact_digest'], idempotency_key='local-semantic-submit-test')
    service.decide(draft['id'], decision='approved', actor='release-admin-b', expected_impact_digest=preview['impact_digest'], idempotency_key='local-semantic-approve-test')
    created = repository.load_active().check_item('custom.power')
    assert created.canonical_contract['observations'][0]['semantic_definition']['description'] == 'Power state'
    assert candidates(created.canonical_contract)['success']
    snapshot = build_report_projection_snapshot({created.version_id: {
        'check_item_id': created.id, 'version': created.version, 'result_mode': created.result_mode,
        'report_projection': 'auto', 'canonical_contract': created.canonical_contract,
    }})
    assert any(column['source'] == 'custom.power.state' for profile in snapshot['profiles'].values() for column in profile['canonical_columns'])


def test_check_item_revision_preserves_collection_implementation_links(tmp_path):
    repository, service, _session_factory = _environment(tmp_path)
    source = repository.load_active().check_item("system.cpu_usage")
    assert source is not None
    original_implementation_ids = {
        implementation.id
        for implementation in repository.load_active().implementations
        if source.id in implementation.check_item_ids
    }
    assert original_implementation_ids

    draft = service.create_check_item_draft(
        _check_item_payload(
            source,
            name="CPU utilization governed revision",
        ),
        actor="engineer-a",
        idempotency_key="catalog-create-cpu-check-item-revision",
    )
    preview = service.preview_impact(draft["id"])
    service.submit(
        draft["id"],
        actor="engineer-a",
        expected_impact_digest=preview["impact_digest"],
        idempotency_key="catalog-submit-cpu-check-item-revision",
    )
    service.decide(
        draft["id"],
        decision="approved",
        actor="release-admin-b",
        expected_impact_digest=preview["impact_digest"],
        idempotency_key="catalog-approve-cpu-check-item-revision",
    )

    active = repository.load_active()
    revised = active.check_item(source.id)
    assert revised is not None
    assert revised.version == source.version + 1
    assert revised.name.endswith("governed revision")
    assert {
        implementation.id
        for implementation in active.implementations
        if source.id in implementation.check_item_ids
    } == original_implementation_ids


def test_ui_edit_is_immutable_and_survives_seed_bootstrap(tmp_path):
    devices = [
        {
            "device_id": "device-h3c-1",
            "device_name": "SW1",
            "vendor": "H3C",
            "family": "Comware7",
            "model": "S6850",
        }
    ]
    repository, service, _session_factory = _environment(
        tmp_path,
        devices=devices,
    )
    active = repository.load_active()
    source = next(item for item in active.implementations if item.id == "h3c-cpu-usage")
    draft = service.create_implementation_draft(
        _implementation_payload(
            source,
            name="H3C CPU utilization edited",
            commands=[{"command": "display cpu-usage summary", "priority": 100}],
        ),
        actor="engineer-a",
        reason="Edit the production command for the existing implementation",
        idempotency_key="catalog-create-h3c-cpu-vnext",
    )

    assert draft["status"] == "draft"
    assert draft["candidate"]["name"].endswith("edited")
    assert [item["command"] for item in draft["candidate"]["commands"]] == [
        "display cpu-usage summary",
    ]
    preview = service.preview_impact(draft["id"])
    assert preview["summary"]["changed"] == 1
    assert preview["blockers"] == []
    submitted = service.submit(
        draft["id"],
        actor="engineer-a",
        reason="Impact reviewed against the current inventory",
        expected_impact_digest=preview["impact_digest"],
        idempotency_key="catalog-submit-h3c-cpu-vnext",
    )
    assert submitted["status"] == "pending_approval"

    with pytest.raises(
        CatalogGovernanceConflictError,
        match="approver must differ",
    ):
        service.decide(
            draft["id"],
            decision="approved",
            actor="engineer-a",
            reason="Self approval must be rejected",
            expected_impact_digest=preview["impact_digest"],
            idempotency_key="catalog-self-approve-h3c-cpu",
        )

    approved = service.decide(
        draft["id"],
        decision="approved",
        actor="release-admin-b",
        reason="Compatibility range and command independently reviewed",
        expected_impact_digest=preview["impact_digest"],
        idempotency_key="catalog-approve-h3c-cpu-vnext",
    )
    assert approved["status"] == "approved"
    current = next(
        item
        for item in repository.load_active().implementations
        if item.id == "h3c-cpu-usage"
    )
    assert current.version == source.version + 1
    assert current.name.endswith("edited")

    # YAML remains a seed after UI publication and cannot roll the active version back.
    repository.bootstrap(load_catalog(), actor="service-restart-bootstrap")
    restarted = next(
        item
        for item in repository.load_active().implementations
        if item.id == "h3c-cpu-usage"
    )
    assert restarted.version == current.version
    assert restarted.version_id == current.version_id


def test_admin_can_approve_own_catalog_change_with_audited_override(tmp_path):
    repository, service, session_factory = _environment(tmp_path)
    source = next(
        item
        for item in repository.load_active().implementations
        if item.id == "h3c-cpu-usage"
    )
    draft = service.create_implementation_draft(
        _implementation_payload(
            source,
            name="H3C CPU utilization admin-reviewed revision",
        ),
        actor="admin-a",
        reason="Create a revision for explicit administrator approval",
        idempotency_key="catalog-admin-create-h3c-cpu",
    )
    preview = service.preview_impact(draft["id"])
    service.submit(
        draft["id"],
        actor="admin-a",
        reason="Administrator reviewed the current inventory impact",
        expected_impact_digest=preview["impact_digest"],
        idempotency_key="catalog-admin-submit-h3c-cpu",
    )

    approved = service.decide(
        draft["id"],
        decision="approved",
        actor="admin-a",
        reason="Administrator explicitly approved the audited self review",
        expected_impact_digest=preview["impact_digest"],
        idempotency_key="catalog-admin-approve-h3c-cpu",
        allow_same_actor=True,
    )

    assert approved["status"] == "approved"
    with _session_scope(session_factory) as db:
        audit = (
            db.query(AuditLogRecord)
            .filter(
                AuditLogRecord.action
                == "check_item_catalog_change_approved"
            )
            .one()
        )
        assert audit.detail["separation_of_duties_override"] is True


def test_catalog_notes_are_optional_through_publication(tmp_path):
    repository, service, session_factory = _environment(tmp_path)
    source = next(
        item
        for item in repository.load_active().implementations
        if item.id == "h3c-cpu-usage"
    )
    draft = service.create_implementation_draft(
        _implementation_payload(
            source,
            name="H3C CPU utilization without manual notes",
        ),
        actor="engineer-a",
        idempotency_key="catalog-create-without-notes",
    )
    preview = service.preview_impact(draft["id"])
    submitted = service.submit(
        draft["id"],
        actor="engineer-a",
        expected_impact_digest=preview["impact_digest"],
        idempotency_key="catalog-submit-without-notes",
    )
    approved = service.decide(
        draft["id"],
        decision="approved",
        actor="release-admin-b",
        expected_impact_digest=preview["impact_digest"],
        idempotency_key="catalog-approve-without-notes",
    )

    assert submitted["status"] == "pending_approval"
    assert approved["status"] == "approved"
    with _session_scope(session_factory) as db:
        change = db.get(CheckItemCatalogChangeRecord, draft["id"])
        assert change.creation_reason == ""
        assert change.submission_reason == ""
        assert change.decision_reason == ""


def test_impact_preview_blocks_ambiguous_new_implementation(tmp_path):
    devices = [
        {
            "device_id": "device-h3c-1",
            "device_name": "SW1",
            "vendor": "H3C",
            "family": "Comware7",
            "model": "S6850",
        }
    ]
    repository, service, _session_factory = _environment(
        tmp_path,
        devices=devices,
    )
    source = next(
        item
        for item in repository.load_active().implementations
        if item.id == "h3c-cpu-usage"
    )
    draft = service.create_implementation_draft(
        _implementation_payload(
            source,
            id="h3c-cpu-usage-overlap",
            name="Overlapping H3C CPU implementation",
        ),
        actor="engineer-a",
        reason="Exercise the compatibility ambiguity guard",
        idempotency_key="catalog-create-overlap-h3c-cpu",
    )

    preview = service.preview_impact(draft["id"])

    assert preview["summary"]["ambiguous"] == 1
    assert [item["code"] for item in preview["blockers"]] == [
        "implementation_resolution_ambiguous"
    ]
    submitted = service.submit(
        draft["id"],
        actor="engineer-a",
        reason="Submit the blocked example for reviewer visibility",
        expected_impact_digest=preview["impact_digest"],
        idempotency_key="catalog-submit-overlap-h3c-cpu",
    )
    assert submitted["status"] == "pending_approval"
    with pytest.raises(CatalogGovernanceConflictError, match="unresolved blockers"):
        service.decide(
            draft["id"],
            decision="approved",
            actor="release-admin-b",
            reason="Approval must remain fail closed",
            expected_impact_digest=preview["impact_digest"],
            idempotency_key="catalog-approve-overlap-h3c-cpu",
        )


def test_creation_idempotency_does_not_duplicate_versions(tmp_path):
    repository, service, session_factory = _environment(tmp_path)
    source = next(
        item
        for item in repository.load_active().implementations
        if item.id == "h3c-cpu-usage"
    )
    payload = _implementation_payload(source)
    first = service.create_implementation_draft(
        payload,
        actor="engineer-a",
        reason="Create one deterministic revision",
        idempotency_key="catalog-create-idempotent-h3c-cpu",
    )
    second = service.create_implementation_draft(
        payload,
        actor="engineer-a",
        reason="Create one deterministic revision",
        idempotency_key="catalog-create-idempotent-h3c-cpu",
    )
    assert first["id"] == second["id"]
    with _session_scope(session_factory) as db:
        assert db.query(CheckItemCatalogChangeRecord).count() == 1


def test_draft_rejects_fallback_command_chain(tmp_path):
    repository, service, _session_factory = _environment(tmp_path)
    source = next(
        item
        for item in repository.load_active().implementations
        if item.id == "h3c-cpu-usage"
    )

    with pytest.raises(ValueError, match="exactly one command"):
        service.create_implementation_draft(
            _implementation_payload(
                source,
                commands=[
                    {"command": "display cpu-usage", "priority": 100},
                    {"command": "display cpu", "priority": 80},
                ],
            ),
            actor="engineer-a",
            reason="Fallback commands must be rejected",
            idempotency_key="catalog-reject-fallback-chain",
        )


def test_delete_implementation_allows_empty_note_and_is_idempotent(tmp_path):
    repository, service, session_factory = _environment(tmp_path)
    source = next(
        item
        for item in repository.load_active().implementations
        if item.id == "h3c-cpu-usage"
    )
    draft = service.create_implementation_draft(
        _implementation_payload(source, name="Pending edit before deletion"),
        actor="engineer-a",
        reason="Create a pending revision before deleting the implementation",
        idempotency_key="catalog-create-before-delete",
    )

    result = service.delete_implementation(
        source.id,
        actor="admin-a",
        idempotency_key="catalog-delete-h3c-cpu",
    )
    replay = service.delete_implementation(
        source.id,
        actor="admin-a",
        idempotency_key="catalog-delete-h3c-cpu",
    )

    assert result == {
        "success": True,
        "implementation_id": source.id,
        "status": "deprecated",
        "cancelled_change_count": 1,
        "idempotent_replay": False,
    }
    assert replay["idempotent_replay"] is True
    assert all(
        item.id != source.id
        for item in repository.load_active().implementations
    )
    with _session_scope(session_factory) as db:
        assert db.get(CollectionImplementationRecord, source.id).status == "deprecated"
        assert {
            row.status
            for row in db.query(CollectionImplementationVersionRecord).filter(
                CollectionImplementationVersionRecord.implementation_id == source.id
            )
        } == {"deprecated"}
        assert db.get(CheckItemCatalogChangeRecord, draft["id"]).status == "cancelled"
        audit = (
            db.query(AuditLogRecord)
            .filter(AuditLogRecord.action == "collection_implementation_deleted")
            .one()
        )
        assert audit.detail["implementation_id"] == source.id
        assert audit.detail["reason"] == ""


@pytest.mark.parametrize("fail_audit", [False, True])
def test_delete_implementation_retires_runtime_bundles_atomically(tmp_path, monkeypatch, fail_audit):
    from src.apps.rule_governance.backend.check_items.runtime_bundle import CheckItemRuntimeBundleService, RuntimeBundleValidationError

    repository, service, session_factory = _environment(tmp_path)
    source = next(
        item
        for item in repository.load_active().implementations
        if item.id == "h3c-cpu-usage"
    )
    with _session_scope(session_factory) as db:
        db.add(ParserTemplate(
            id="shared-parser", vendor="H3C", profile="test", canonical_command="display cpu-usage",
            category="system", version="v1", asset_path="shared.textfsm", sha256="f" * 64,
            status="active",
        ))
        check_version_id = (
            db.query(CheckItemImplementationLinkRecord)
            .filter(
                CheckItemImplementationLinkRecord.implementation_version_id
                == source.version_id
            )
            .first()
            .check_item_version_id
        )
        db.add(
            CheckItemRuntimeBundleRecord(
                id="runtime-bundle-delete-blocker",
                check_item_id="system.cpu_usage",
                implementation_id=source.id,
                name="Delete blocker",
                status="active",
                current_version=1,
            )
        )
        db.add(
            CheckItemRuntimeBundleVersionRecord(
                id="runtime-bundle-delete-blocker-v1",
                bundle_id="runtime-bundle-delete-blocker",
                version=1,
                check_item_version_id=check_version_id,
                implementation_version_id=source.version_id,
                parser_bundle_json={},
                canonical_catalog_version=1,
                canonical_catalog_digest="a" * 64,
                entity_identity_policy_version_id="identity-v1",
                dependency_digests_json={},
                compatibility_json={},
                validation_report_json={"success": True},
                digest="d" * 64,
                status="active",
                created_by="bundle-engineer",
                activated_at=datetime.now(timezone.utc),
            )
        )

    if fail_audit:
        def fail(*args, **kwargs):
            raise RuntimeError("audit unavailable")
        monkeypatch.setattr(service, "_audit", fail)
        with pytest.raises(RuntimeError, match="audit unavailable"):
            service.delete_implementation(
                source.id, actor="admin-a", idempotency_key="catalog-delete-runtime-blocked",
            )
    else:
        service.delete_implementation(
            source.id,
            actor="admin-a",
            reason="Delete adapter and retire its runtime bundles together",
            idempotency_key="catalog-delete-runtime-blocked",
        )
        replay = service.delete_implementation(
            source.id, actor="admin-a", idempotency_key="catalog-delete-runtime-blocked",
        )
        assert replay["idempotent_replay"]
    with _session_scope(session_factory) as db:
        expected = "active" if fail_audit else "deprecated"
        assert db.get(CollectionImplementationRecord, source.id).status == expected
        assert db.get(CollectionImplementationVersionRecord, source.version_id).status == expected
        assert db.get(CheckItemRuntimeBundleRecord, "runtime-bundle-delete-blocker").status == expected
        bundle = db.get(CheckItemRuntimeBundleVersionRecord, "runtime-bundle-delete-blocker-v1")
        assert bundle.status == expected
        assert bundle.digest == "d" * 64
        assert bundle.check_item_version_id == check_version_id
        assert db.get(CheckItemVersionRecord, check_version_id).status == "active"
        assert db.get(CollectionImplementationRecord, "huawei-cpu-usage").status == "active"
        assert db.get(ParserTemplate, "shared-parser").status == "active"
        assert db.get(ParserTemplate, "shared-parser").sha256 == "f" * 64
        if not fail_audit:
            audit = db.query(AuditLogRecord).filter_by(action="collection_implementation_deleted").one()
            assert audit.detail["retired_runtime_bundle_version_ids"] == [bundle.id]
    if not fail_audit:
        runtime = CheckItemRuntimeBundleService(lambda: _session_scope(session_factory))
        with pytest.raises(RuntimeBundleValidationError, match="命令适配器已删除"):
            runtime.rollback("runtime-bundle-delete-blocker-v1", actor="admin-a", reason="Cannot resurrect deleted adapter")


def test_active_runtime_bundle_requires_rollout_and_cutover_is_atomic(
    tmp_path,
    monkeypatch,
):
    devices = [
        {
            "device_id": "device-h3c-1",
            "device_name": "SW1",
            "vendor": "H3C",
            "family": "Comware7",
            "model": "S6850",
        }
    ]
    repository, service, session_factory = _environment(
        tmp_path,
        devices=devices,
    )
    source = next(
        item
        for item in repository.load_active().implementations
        if item.id == "h3c-cpu-usage"
    )
    draft = service.create_implementation_draft(
        _implementation_payload(
            source,
            commands=[{"command": "display cpu-usage summary", "priority": 100}],
        ),
        actor="engineer-a",
        reason="Change the production collection command",
        idempotency_key="catalog-create-runtime-cutover",
    )
    with _session_scope(session_factory) as db:
        check_version_id = db.query(CheckItemImplementationLinkRecord).filter(
            CheckItemImplementationLinkRecord.implementation_version_id
            == source.version_id
        ).first().check_item_version_id
        root = CheckItemRuntimeBundleRecord(
            id="runtime-bundle-cpu",
            check_item_id="system.cpu_usage",
            implementation_id=source.id,
            name="CPU runtime bundle",
            status="active",
            current_version=1,
        )
        db.add(root)
        db.add(
            CheckItemRuntimeBundleVersionRecord(
                id="runtime-bundle-cpu-v1",
                bundle_id=root.id,
                version=1,
                check_item_version_id=check_version_id,
                implementation_version_id=source.version_id,
                parser_bundle_json={},
                canonical_catalog_version=1,
                canonical_catalog_digest="a" * 64,
                entity_identity_policy_version_id="identity-v1",
                dependency_digests_json={},
                compatibility_json={},
                validation_report_json={"success": True},
                digest="b" * 64,
                status="active",
                created_by="bundle-engineer-a",
                activated_at=datetime.now(timezone.utc),
            )
        )
    preview = service.preview_impact(draft["id"])
    assert "active_runtime_bundle_requires_rollout" in {
        item["code"] for item in preview["blockers"]
    }
    service.submit(
        draft["id"],
        actor="engineer-a",
        reason="Submit the implementation and runtime cutover",
        expected_impact_digest=preview["impact_digest"],
        idempotency_key="catalog-submit-runtime-cutover",
    )
    with pytest.raises(CatalogGovernanceConflictError, match="requires a completed"):
        service.decide(
            draft["id"],
            decision="approved",
            actor="release-admin-b",
            reason="No rollout evidence is available",
            expected_impact_digest=preview["impact_digest"],
            idempotency_key="catalog-approve-runtime-without-rollout",
        )

    with _session_scope(session_factory) as db:
        db.add(
            CheckItemRuntimeBundleVersionRecord(
                id="runtime-bundle-cpu-v2",
                bundle_id="runtime-bundle-cpu",
                version=2,
                check_item_version_id=check_version_id,
                implementation_version_id=draft["version_id"],
                parser_bundle_json={},
                canonical_catalog_version=1,
                canonical_catalog_digest="a" * 64,
                entity_identity_policy_version_id="identity-v1",
                dependency_digests_json={},
                compatibility_json={},
                validation_report_json={"success": True},
                digest="c" * 64,
                status="draft",
                created_by="bundle-engineer-c",
            )
        )

    evidence = {
        "candidate_runtime_bundle_version_ids": ["runtime-bundle-cpu-v2"],
        "evidence_digest": "d" * 64,
    }
    monkeypatch.setattr(
        service,
        "_validate_rollout_cutover",
        lambda *_args, **_kwargs: evidence,
    )

    def fake_activate(_self, db, version_id, **_context):
        candidate = db.get(CheckItemRuntimeBundleVersionRecord, version_id)
        baseline = db.get(CheckItemRuntimeBundleVersionRecord, "runtime-bundle-cpu-v1")
        baseline.status = "deprecated"
        candidate.status = "active"
        return candidate

    monkeypatch.setattr(
        "src.core.check_items.catalog_governance."
        "CheckItemRuntimeBundleService.activate_in_session",
        fake_activate,
    )
    approved = service.decide(
        draft["id"],
        decision="approved",
        actor="release-admin-b",
        reason="Rollout completed and cutover evidence reviewed",
        expected_impact_digest=preview["impact_digest"],
        idempotency_key="catalog-approve-runtime-with-rollout",
        rollout_campaign_id="rollout-completed-1",
    )

    assert approved["status"] == "approved"
    assert approved["rollout_campaign_id"] == "rollout-completed-1"
    assert approved["cutover_digest"] == "d" * 64
    with _session_scope(session_factory) as db:
        assert db.get(
            CollectionImplementationVersionRecord,
            source.version_id,
        ).status == "deprecated"
        assert db.get(
            CollectionImplementationVersionRecord,
            draft["version_id"],
        ).status == "active"
        assert db.get(
            CheckItemRuntimeBundleVersionRecord,
            "runtime-bundle-cpu-v1",
        ).status == "deprecated"
        assert db.get(
            CheckItemRuntimeBundleVersionRecord,
            "runtime-bundle-cpu-v2",
        ).status == "active"


def test_system_check_items_and_their_implementations_cannot_be_deleted(tmp_path):
    repository, service, _session_factory = _environment(tmp_path)
    assert repository.load_active().check_item("topology.routes") is not None
    with pytest.raises(CatalogGovernanceConflictError, match="系统基础检查项"):
        service.delete_check_item(
            "topology.routes", actor="admin-a", idempotency_key="delete-topology-routes"
        )
    with pytest.raises(CatalogGovernanceConflictError, match="默认采集实现"):
        service.delete_implementation(
            "topology.routes.comware",
            actor="admin-a",
            idempotency_key="delete-topology-routes-comware",
        )
    assert repository.load_active().check_item("topology.routes") is not None
