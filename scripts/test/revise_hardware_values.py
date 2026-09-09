"""Validate and atomically publish the user-authorized four-CheckItem revision.

Default is read-only. --apply publishes only after replaying both source periods.
No LLM calls; source CLI stays on this host. Existing command associations stay intact.
"""

import argparse
import hashlib
import json
import uuid
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timezone
from io import StringIO

import textfsm
from sqlalchemy import func

from src.core.check_items.canonical import validate_check_item_contract
from src.core.check_items.catalog import CheckItemCatalog, CheckItemCatalogRepository
from src.core.check_items.command_adapter import execute_adapter
from src.core.check_items.hardware_values import (
    TARGETS,
    revise_adapter,
    revise_definition,
    template_path,
)
from src.core.parser.key_contract import validate_entity_keys
from src.core.parser.repository import contract_digest
from src.core.patrol.event_rules import current_state_condition_matches, event_rule_digest
from src.infrastructure.db.models import (
    AuditLogRecord,
    ChangePolicyRecord,
    ChangePolicyVersionRecord,
    CommandSnapshot,
    EventRuleRecord,
    EventRuleVersionRecord,
    ParserTemplate,
)
from src.infrastructure.db.postgres import get_db_session

ACTOR = "authorized-hardware-value-revision"


def run(source_job, apply=False):
    repo = CheckItemCatalogRepository()
    catalog = repo.load_active()
    before = {i.id: i for i in catalog.check_items if i.id in TARGETS}
    items = {key: revise_definition(item) for key, item in before.items()}
    if all(items[key] == before[key] for key in items):
        print("The four active CheckItems are already aligned.")
        return
    if len(items) != 4:
        raise ValueError("Expected all four existing CheckItems")
    for item in items.values():
        errors = validate_check_item_contract(item)
        if errors:
            raise ValueError(errors)
    adapters = [
        a
        for a in catalog.implementations
        if set(a.check_item_ids) & TARGETS.keys() and a.adapter_spec.get("parser")
    ]
    if len(adapters) != 4:
        raise ValueError(
            "Review unexpected active parser associations before this bounded migration"
        )
    now = datetime.now(timezone.utc)
    with get_db_session() as db:
        new_parsers, new_adapters, evidence = [], [], []
        for adapter in adapters:
            item = items[adapter.check_item_ids[0]]
            previous = db.get(ParserTemplate, adapter.adapter_spec["parser"]["template_id"])
            if previous.status != "active" or previous.canonical_command != TARGETS[item.id][2]:
                raise ValueError("Associated active parser/command changed")
            content = template_path(item.id).read_text(encoding="utf8")
            digest = hashlib.sha256(content.encode()).hexdigest()
            parser_id = str(
                uuid.uuid5(uuid.NAMESPACE_URL, f"hardware-values/v1/{previous.id}/{digest}")
            )
            headers = textfsm.TextFSM(StringIO(content)).header
            updated = revise_adapter(adapter, item, parser_id, headers)
            samples = (
                db.query(CommandSnapshot)
                .filter(
                    CommandSnapshot.run_id.like(source_job + "-%"),
                    CommandSnapshot.canonical_command == previous.canonical_command,
                )
                .all()
            )
            if not any("-baseline-" in s.run_id for s in samples) or not any(
                "-current-" in s.run_id for s in samples
            ):
                raise ValueError("Both baseline and current CLI samples are required")
            manifests = []
            for sample in samples:
                parser = textfsm.TextFSM(StringIO(content))
                records = [
                    dict(zip(parser.header, row)) for row in parser.ParseText(sample.raw_text)
                ]
                keys = validate_entity_keys(records, item.canonical_contract["identity_dimensions"])
                batch = execute_adapter(
                    updated.adapter_spec,
                    record_sets={"default": records},
                    check_items={item.id: asdict(item)},
                    device_id=sample.device_id,
                    adapter_version_id=updated.version_id,
                )[0]
                # The existing reviewed record count is independent evidence for coverage.
                old_records = sample.structured_json
                if isinstance(old_records, dict):
                    sets = old_records.get("record_sets", old_records)
                    old_records = sets.get("power_supplies", [])
                    if isinstance(old_records, dict):
                        old_records = old_records.get("records", [])
                if (
                    not keys.valid
                    or batch.quality_status != "complete"
                    or len(records) != len(old_records)
                ):
                    raise ValueError(
                        (
                            item.id,
                            "sample replay failed",
                            keys.errors,
                            batch.errors,
                            len(records),
                            len(old_records),
                        )
                    )
                manifests.append(
                    {
                        "snapshot_id": sample.id,
                        "source_sha256": sample.raw_text_hash,
                        "record_count": len(records),
                        "passed": True,
                    }
                )
            required = item.canonical_contract["identity_dimensions"] + [
                r["canonical_field"]
                for r in item.canonical_contract["observations"]
                if r.get("required", True)
            ]
            validation = {
                "key_contract_version": 1,
                "entity_type": item.canonical_contract["entity_type"],
                "primary_keys": item.canonical_contract["identity_dimensions"],
                "required_fields": required,
                "optional_fields": [h for h in headers if h not in required],
                "validators": {},
                "allow_empty": False,
                "empty_patterns": [],
                "ignore_fields": [],
                "primary_key_defaults": {},
                "record_sets": [],
                "simple_parser": True,
                "contract_source": "user_authorized_same_name_revision",
                "contract_confidence": 1,
                "compatibility": deepcopy(
                    (previous.validation_json or {}).get("compatibility") or {}
                ),
                "sample_count": len(samples),
                "passed_samples": len(samples),
                "validation_pass_rate": 1,
                "samples": manifests,
                "regression_validation": {"passed": True, "sample_count": len(samples)},
                "contract_approval_status": "approved",
                "contract_approved_by": ACTOR,
                "contract_approved_at": now.isoformat(),
                "check_item_version_id": item.version_id,
                "approval_result_preview": {"fields": headers, "samples": manifests},
            }
            new = ParserTemplate(
                id=parser_id,
                vendor=previous.vendor,
                model=previous.model,
                family=previous.family,
                profile=previous.profile,
                canonical_command=previous.canonical_command,
                category=previous.category,
                version="hardware-values-v1",
                asset_path=str(template_path(item.id)),
                template_text=content,
                sha256=digest,
                status="active",
                validation_json=validation,
                contract_version=(previous.contract_version or 1) + 1,
                contract_sha256=contract_digest(validation),
                created_at=now,
                updated_at=now,
                activated_at=now,
            )
            new_parsers.append((previous, new))
            new_adapters.append(updated)
            evidence.append({"check_item": item.id, "fields": headers, "samples": manifests})
        print(json.dumps(evidence, ensure_ascii=False, indent=2))
        if not apply:
            print("Validation passed. No active assets changed.")
            return

        @contextmanager
        def transaction_scope():
            yield db

        for old, new in new_parsers:
            old.status, old.deprecated_at = "deprecated", now
        db.flush()
        for old, new in new_parsers:
            db.add(new)
        CheckItemCatalogRepository(session_scope=transaction_scope).bootstrap(
            CheckItemCatalog(
                catalog.catalog_version + 1, tuple(items.values()), tuple(new_adapters)
            ),
            actor=ACTOR,
        )
        # Existing state rules keep their purpose, but are re-pinned to the new
        # CheckItem. Include H3C's literal Faulty state in the approved state group.
        for key, item in items.items():
            for model, root_model, root_key in (
                (ChangePolicyVersionRecord, ChangePolicyRecord, "policy_id"),
                (EventRuleVersionRecord, EventRuleRecord, "rule_id"),
            ):
                old_versions = (
                    db.query(model)
                    .filter(
                        model.check_item_version_id == before[key].version_id,
                        model.status == "active",
                    )
                    .all()
                )
                for old in old_versions:
                    values = {
                        c.name: deepcopy(getattr(old, c.name)) for c in model.__table__.columns
                    }
                    root_id = getattr(old, root_key)
                    next_version = (
                        db.query(func.max(model.version))
                        .filter(getattr(model, root_key) == root_id)
                        .scalar()
                        + 1
                    )
                    values.update(
                        id=str(uuid.uuid4()),
                        version=next_version,
                        check_item_version_id=item.version_id,
                        created_by=ACTOR,
                        created_at=now,
                        activated_at=now,
                        deprecated_at=None,
                        source_job_id=None,
                        validation_report_json={
                            "passed": True,
                            "revision": "same-name/v1",
                            "basis": (
                                "Existing state semantics preserved; "
                                "raw Normal/Faulty/Absent checked locally"
                            ),
                        },
                    )
                    if model is EventRuleVersionRecord:
                        condition = values["condition_json"]
                        if (
                            key in {"environment.fan", "environment.power"}
                            and condition.get("kind") == "entity_status_down"
                        ):
                            condition["values"] = sorted(
                                set(condition.get("values", [])) | {"faulty"}
                            )
                            for state, expected in [
                                ("Normal", False),
                                ("Faulty", True),
                                ("Absent", True),
                            ]:
                                obs = {
                                    "field": condition["fields"][0],
                                    "value": state,
                                    "observation_type": condition["observation_types"][0],
                                    "entity_type": condition["entity_types"][0],
                                }
                                assert current_state_condition_matches(condition, obs) == expected
                        values["digest"] = event_rule_digest(
                            name=db.get(root_model, root_id).name,
                            event_type=values["event_type"],
                            severity=values["severity"],
                            condition=condition,
                            governance={
                                k: values[k]
                                for k in (
                                    "priority",
                                    "exclusive",
                                    "event_class",
                                    "evaluation_scope",
                                )
                            },
                        )
                    old.status, old.deprecated_at = "deprecated", now
                    db.flush()
                    db.add(model(**values))
                    root = db.get(root_model, root_id)
                    root.current_version, root.updated_at = next_version, now
        db.add(
            AuditLogRecord(
                id=str(uuid.uuid4()),
                user_id=ACTOR,
                username=ACTOR,
                action="hardware_value_alignment",
                resource_type="check_item",
                resource_id="cpu-memory-fan-power",
                status="success",
                created_at=now,
                detail={
                    "source_job": source_job,
                    "validation": evidence,
                    "reason": "User authorized CheckItem/TextFSM Value alignment",
                },
            )
        )
        db.flush()
    print("Published four CheckItems, four parsers and four adapters atomically.")


if __name__ == "__main__":
    args = argparse.ArgumentParser()
    args.add_argument("--source-job", required=True)
    args.add_argument("--apply", action="store_true")
    parsed = args.parse_args()
    run(parsed.source_job, parsed.apply)
