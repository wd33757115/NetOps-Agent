"""PostgreSQL repository for ObservationContract assets."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import or_, text

from src.core.observation.contracts import (
    ObservationContract,
    build_contract_payload,
    observation_contract_digest,
)
from src.infrastructure.db.models import ObservationContractRecord, ParserTemplate
from src.infrastructure.db.postgres import get_db_session


class ObservationContractWriteFrozenError(RuntimeError):
    """Raised once the all-at-once V2 cutover has retired contract writes."""


def assert_observation_contract_writes_enabled() -> None:
    """Protect legacy write paths while keeping historical reads available."""

    from src.common.config import get_settings

    if get_settings().ADAPTER_PROJECTION_CUTOVER_ENFORCED:
        raise ObservationContractWriteFrozenError(
            "ObservationContract writes are frozen after the CommandAdapter V2 "
            "cutover; use a versioned CommandAdapter projection instead"
        )


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _state_identity(
    *,
    entity_type: str | None,
    primary_keys: Any,
    canonical_entity_type: str | None = None,
    entity_identity_fields: Any = None,
    entity_dimensions: Any = None,
    record_sets: Any = None,
) -> tuple[Any, ...]:
    def identity(
        item: dict[str, Any],
        *,
        name: str,
        fallback_entity_type: str | None,
        fallback_primary_keys: Any,
    ) -> tuple[Any, ...]:
        source_entity_type = str(item.get("entity_type") or fallback_entity_type or "record")
        source_primary_keys = tuple(
            str(key)
            for key in (
                item.get("primary_keys") if "primary_keys" in item else fallback_primary_keys
            )
            or ()
        )
        canonical_type = str(
            item.get("canonical_entity_type") or canonical_entity_type or source_entity_type
        )
        identity_fields = tuple(
            str(key)
            for key in (
                item.get("entity_identity_fields")
                if "entity_identity_fields" in item
                else entity_identity_fields
            )
            or source_primary_keys
        )
        dimensions = (
            item.get("entity_dimensions") if "entity_dimensions" in item else entity_dimensions
        ) or {}
        return (
            name,
            source_entity_type,
            source_primary_keys,
            canonical_type,
            identity_fields,
            json.dumps(
                dimensions,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            ),
        )

    normalized_sets = [dict(item) for item in record_sets or () if isinstance(item, dict)]
    if normalized_sets:
        return tuple(
            identity(
                item,
                name=str(item.get("name") or item.get("entity_type") or ""),
                fallback_entity_type=entity_type,
                fallback_primary_keys=primary_keys,
            )
            for item in normalized_sets
        )
    return (
        identity(
            {},
            name="default",
            fallback_entity_type=entity_type,
            fallback_primary_keys=primary_keys,
        ),
    )


def _row_to_contract(row: ObservationContractRecord) -> ObservationContract:
    return ObservationContract(
        contract_id=row.id,
        source_type=row.source_type,
        schema_id=row.schema_id,
        schema_ref=row.schema_ref,
        source_template_id=row.source_template_id,
        entity_type=row.entity_type,
        primary_keys=tuple(row.primary_keys_json or ()),
        required_fields=tuple(row.required_fields_json or ()),
        canonical_entity_type=row.canonical_entity_type,
        entity_identity_fields=tuple(row.entity_identity_fields_json or ()),
        entity_dimensions=dict(row.entity_dimensions_json or {}),
        optional_fields=tuple(row.optional_fields_json or ()),
        validators=dict(row.validators_json or {}),
        ignore_fields=tuple(row.ignore_fields_json or ()),
        allow_empty=bool(row.allow_empty),
        empty_patterns=tuple(row.empty_patterns_json or ()),
        state_semantics=dict(row.state_semantics_json or {}),
        record_sets=tuple(
            dict(item) for item in row.record_sets_json or () if isinstance(item, dict)
        ),
        record_set_relations=tuple(
            dict(item)
            for item in row.record_set_relations_json or ()
            if isinstance(item, dict)
        ),
        version=int(row.version or 1),
        contract_sha256=row.contract_sha256,
        status=row.status,
        compatibility=dict(row.compatibility_json or {}),
    )


def _current_semantic_digest_from_row(row: ObservationContractRecord) -> str:
    """Rehash persisted semantics with the current digest schema.

    Contract digest schemas can gain optional semantic fields over time. Existing
    immutable rows retain their original digest, so equality must also compare
    their persisted meaning after applying today's empty-value normalization.
    """

    return observation_contract_digest(
        {
            "source_type": row.source_type,
            "schema_id": row.schema_id,
            "entity_type": row.entity_type,
            "primary_keys": tuple(row.primary_keys_json or ()),
            "canonical_entity_type": row.canonical_entity_type,
            "entity_identity_fields": tuple(row.entity_identity_fields_json or ()),
            "entity_dimensions": dict(row.entity_dimensions_json or {}),
            "required_fields": tuple(row.required_fields_json or ()),
            "optional_fields": tuple(row.optional_fields_json or ()),
            "validators": dict(row.validators_json or {}),
            "ignore_fields": tuple(row.ignore_fields_json or ()),
            "allow_empty": bool(row.allow_empty),
            "empty_patterns": tuple(row.empty_patterns_json or ()),
            "state_semantics": dict(row.state_semantics_json or {}),
            "record_sets": tuple(
                dict(item)
                for item in row.record_sets_json or ()
                if isinstance(item, dict)
            ),
            "record_set_relations": tuple(
                dict(item)
                for item in row.record_set_relations_json or ()
                if isinstance(item, dict)
            ),
        }
    )


def _row_semantically_matches_payload(
    row: ObservationContractRecord,
    payload: dict[str, Any],
) -> bool:
    return _current_semantic_digest_from_row(row) == payload["contract_sha256"]


class ObservationContractRepository:
    """Persist and resolve source-agnostic observation contracts."""

    def get_by_id(self, contract_id: str) -> ObservationContract | None:
        with get_db_session() as db:
            row = db.get(ObservationContractRecord, str(contract_id))
            return _row_to_contract(row) if row else None

    def upsert_contract(
        self,
        *,
        source_type: str,
        schema_id: str,
        schema_ref: str | None = None,
        category: str | None = None,
        validation: dict[str, Any] | None = None,
        version: int | None = None,
        source_template_id: str | None = None,
        source_job_id: str | None = None,
        approval: dict[str, Any] | None = None,
        status: str = "active",
        compatibility: dict[str, Any] | None = None,
    ) -> ObservationContract:
        with get_db_session() as db:
            return self.upsert_contract_row(
                db,
                source_type=source_type,
                schema_id=schema_id,
                schema_ref=schema_ref,
                category=category,
                validation=validation,
                version=version,
                source_template_id=source_template_id,
                source_job_id=source_job_id,
                approval=approval,
                status=status,
                compatibility=compatibility,
            )

    def create_contract_version(
        self,
        *,
        source_type: str,
        schema_id: str,
        schema_ref: str | None = None,
        category: str | None = None,
        validation: dict[str, Any] | None = None,
        source_template_id: str | None = None,
        source_job_id: str | None = None,
        approval: dict[str, Any] | None = None,
        status: str = "draft",
        compatibility: dict[str, Any] | None = None,
    ) -> ObservationContract:
        """Reuse an identical digest or allocate the next immutable version."""

        assert_observation_contract_writes_enabled()
        probe = build_contract_payload(
            source_type=source_type,
            schema_id=schema_id,
            schema_ref=schema_ref,
            category=category,
            validation=validation,
            version=1,
            source_template_id=source_template_id,
            status=status,
        )
        with get_db_session() as db:
            lock_key = f"observation-contract:{source_type}:{schema_id}"
            db.execute(
                text("SELECT pg_advisory_xact_lock(hashtext(:lock_key))"),
                {"lock_key": lock_key},
            )
            existing = (
                db.query(ObservationContractRecord)
                .filter(
                    ObservationContractRecord.source_type == source_type,
                    ObservationContractRecord.schema_id == schema_id,
                    ObservationContractRecord.contract_sha256 == probe["contract_sha256"],
                )
                .order_by(ObservationContractRecord.version.desc())
                .first()
            )
            if existing is not None:
                return _row_to_contract(existing)
            legacy_equivalent = next(
                (
                    row
                    for row in db.query(ObservationContractRecord)
                    .filter(
                        ObservationContractRecord.source_type == source_type,
                        ObservationContractRecord.schema_id == schema_id,
                    )
                    .order_by(ObservationContractRecord.version.desc())
                    .all()
                    if _row_semantically_matches_payload(row, probe)
                ),
                None,
            )
            if legacy_equivalent is not None:
                return _row_to_contract(legacy_equivalent)
            latest = (
                db.query(ObservationContractRecord.version)
                .filter(
                    ObservationContractRecord.source_type == source_type,
                    ObservationContractRecord.schema_id == schema_id,
                )
                .order_by(ObservationContractRecord.version.desc())
                .first()
            )
            next_version = int(latest[0]) + 1 if latest else 1
            return self.upsert_contract_row(
                db,
                source_type=source_type,
                schema_id=schema_id,
                schema_ref=schema_ref,
                category=category,
                validation=validation,
                version=next_version,
                source_template_id=source_template_id,
                source_job_id=source_job_id,
                approval=approval,
                status=status,
                compatibility=compatibility,
            )

    def upsert_contract_row(
        self,
        db,
        *,
        source_type: str,
        schema_id: str,
        schema_ref: str | None = None,
        category: str | None = None,
        validation: dict[str, Any] | None = None,
        version: int | None = None,
        source_template_id: str | None = None,
        source_job_id: str | None = None,
        approval: dict[str, Any] | None = None,
        status: str = "active",
        compatibility: dict[str, Any] | None = None,
    ) -> ObservationContract:
        assert_observation_contract_writes_enabled()
        payload = build_contract_payload(
            source_type=source_type,
            schema_id=schema_id,
            schema_ref=schema_ref,
            category=category,
            validation=validation,
            version=version,
            source_template_id=source_template_id,
            status=status,
        )
        row = (
            db.query(ObservationContractRecord)
            .filter(
                ObservationContractRecord.source_type == payload["source_type"],
                ObservationContractRecord.schema_id == payload["schema_id"],
                ObservationContractRecord.version == payload["version"],
            )
            .first()
        )
        now = _utcnow()
        if row is None:
            row = ObservationContractRecord(
                id=str(uuid.uuid4()),
                source_type=payload["source_type"],
                schema_id=payload["schema_id"],
                version=payload["version"],
                created_at=now,
            )
            db.add(row)
        elif (
            row.contract_sha256 != payload["contract_sha256"]
            and not _row_semantically_matches_payload(row, payload)
        ):
            raise ValueError(
                "observation contract version is immutable; create a new version "
                f"for {payload['source_type']}:{payload['schema_id']} "
                f"v{payload['version']}"
            )
        if payload["status"] == "active":
            (
                db.query(ObservationContractRecord)
                .filter(
                    ObservationContractRecord.source_type == payload["source_type"],
                    ObservationContractRecord.schema_id == payload["schema_id"],
                    ObservationContractRecord.status == "active",
                    ObservationContractRecord.id != row.id,
                )
                .update(
                    {
                        "status": "deprecated",
                        "deprecated_at": now,
                        "updated_at": now,
                    },
                    synchronize_session=False,
                )
            )
            row.activated_at = row.activated_at or now
        if row.schema_ref is None:
            row.schema_ref = payload["schema_ref"]
        if row.source_template_id is None:
            row.source_template_id = payload["source_template_id"]
        if row.source_job_id is None:
            row.source_job_id = source_job_id
        if approval is not None:
            row.approval_json = approval
        if compatibility is not None:
            row.compatibility_json = compatibility
        if row.entity_type is None:
            row.entity_type = payload["entity_type"]
            row.primary_keys_json = list(payload["primary_keys"])
            row.canonical_entity_type = payload["canonical_entity_type"]
            row.entity_identity_fields_json = list(payload["entity_identity_fields"])
            row.entity_dimensions_json = dict(payload["entity_dimensions"])
            row.required_fields_json = list(payload["required_fields"])
            row.optional_fields_json = list(payload["optional_fields"])
            row.validators_json = payload["validators"]
            row.ignore_fields_json = list(payload["ignore_fields"])
            row.allow_empty = bool(payload["allow_empty"])
            row.empty_patterns_json = list(payload["empty_patterns"])
            row.state_semantics_json = payload["state_semantics"]
            row.record_sets_json = [dict(item) for item in payload["record_sets"]]
            row.record_set_relations_json = [
                dict(item) for item in payload["record_set_relations"]
            ]
            row.contract_sha256 = payload["contract_sha256"]
        row.status = payload["status"]
        row.updated_at = now
        db.flush()
        return _row_to_contract(row)

    def upsert_from_parser_template(
        self,
        template_id: str,
        *,
        status: str | None = None,
    ) -> ObservationContract | None:
        with get_db_session() as db:
            template = db.get(ParserTemplate, template_id)
            if template is None:
                return None
            return self.upsert_from_parser_template_row(
                db,
                template,
                status=status,
            )

    def deprecate_from_parser_template_row(
        self,
        db,
        template_id: str,
    ) -> int:
        """Retire existing derived contracts without rebuilding immutable content."""

        assert_observation_contract_writes_enabled()
        now = _utcnow()
        rows = (
            db.query(ObservationContractRecord)
            .filter(ObservationContractRecord.source_template_id == template_id)
            .with_for_update()
            .all()
        )
        for row in rows:
            row.status = "deprecated"
            row.deprecated_at = row.deprecated_at or now
            row.updated_at = now
        db.flush()
        return len(rows)

    def upsert_from_parser_template_row(
        self,
        db,
        template: ParserTemplate,
        *,
        status: str | None = None,
    ) -> ObservationContract:
        contract_status = status or ("active" if template.status == "active" else "draft")
        validation = dict(template.validation_json or {})
        previous = (
            db.query(ObservationContractRecord)
            .join(
                ParserTemplate,
                ParserTemplate.id == ObservationContractRecord.source_template_id,
            )
            .filter(
                ObservationContractRecord.source_type == "cli",
                ObservationContractRecord.status == "active",
                ObservationContractRecord.source_template_id != template.id,
                ParserTemplate.vendor == template.vendor,
                ParserTemplate.profile == template.profile,
                ParserTemplate.canonical_command == template.canonical_command,
            )
            .order_by(ObservationContractRecord.version.desc())
            .first()
        )
        next_identity = _state_identity(
            entity_type=str(validation.get("entity_type") or template.category or "record"),
            primary_keys=validation.get("primary_keys"),
            canonical_entity_type=validation.get("canonical_entity_type"),
            entity_identity_fields=validation.get("entity_identity_fields"),
            entity_dimensions=validation.get("entity_dimensions"),
            record_sets=validation.get("record_sets"),
        )
        previous_identity = (
            _state_identity(
                entity_type=previous.entity_type,
                primary_keys=previous.primary_keys_json,
                canonical_entity_type=previous.canonical_entity_type,
                entity_identity_fields=previous.entity_identity_fields_json,
                entity_dimensions=previous.entity_dimensions_json,
                record_sets=previous.record_sets_json,
            )
            if previous is not None
            else None
        )
        breaking = previous_identity is not None and previous_identity != next_identity
        compatibility = {
            "state_identity": "breaking" if breaking else "compatible",
            "previous": previous_identity,
            "current": next_identity,
            "action": "rebaseline" if breaking else "none",
        }
        approval = dict(validation.get("compatibility_approval") or {})
        if (
            breaking
            and contract_status == "active"
            and not (approval.get("approved") is True and approval.get("action") == "rebaseline")
        ):
            raise ValueError(
                "breaking ObservationContract identity change requires approved rebaseline"
            )
        return self.upsert_contract_row(
            db,
            source_type="cli",
            schema_id=template.id,
            schema_ref=f"parser_template:{template.id}",
            source_template_id=template.id,
            category=template.category,
            validation=validation,
            version=int(template.contract_version or 1),
            status=contract_status,
            compatibility=compatibility,
            approval=approval or None,
        )

    def get_active(
        self,
        *,
        source_type: str,
        schema_id: str,
    ) -> ObservationContract | None:
        with get_db_session() as db:
            row = (
                db.query(ObservationContractRecord)
                .filter(
                    ObservationContractRecord.source_type == source_type,
                    ObservationContractRecord.schema_id == schema_id,
                    ObservationContractRecord.status == "active",
                )
                .order_by(ObservationContractRecord.version.desc())
                .first()
            )
            return _row_to_contract(row) if row else None

    def get_version(
        self,
        *,
        source_type: str,
        schema_id: str,
        version: int,
    ) -> ObservationContract | None:
        with get_db_session() as db:
            row = (
                db.query(ObservationContractRecord)
                .filter(
                    ObservationContractRecord.source_type == source_type,
                    ObservationContractRecord.schema_id == schema_id,
                    ObservationContractRecord.version == int(version),
                )
                .first()
            )
            return _row_to_contract(row) if row else None

    def get_validation_dict(
        self,
        *,
        source_type: str,
        schema_id: str,
        version: int,
    ) -> dict[str, Any] | None:
        contract = self.get_version(
            source_type=source_type,
            schema_id=schema_id,
            version=version,
        )
        return contract.to_validation_dict() if contract else None

    def get_active_validation_dict(
        self,
        *,
        source_type: str,
        schema_id: str,
    ) -> dict[str, Any] | None:
        contract = self.get_active(source_type=source_type, schema_id=schema_id)
        return contract.to_validation_dict() if contract else None

    def list_contracts(
        self,
        *,
        source_type: str | None = None,
        status: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        with get_db_session() as db:
            query = db.query(ObservationContractRecord)
            if source_type:
                query = query.filter(ObservationContractRecord.source_type == source_type)
            if status:
                query = query.filter(ObservationContractRecord.status == status)
            rows = (
                query.order_by(ObservationContractRecord.updated_at.desc())
                .limit(max(1, min(int(limit), 1000)))
                .all()
            )
            return [row.to_dict() for row in rows]

    def list_contracts_page(
        self,
        *,
        source_type: str | None = None,
        status: str | None = None,
        search: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> dict[str, Any]:
        page_size = max(1, min(int(limit), 200))
        page_offset = max(0, int(offset))
        with get_db_session() as db:
            query = db.query(ObservationContractRecord)
            if source_type:
                query = query.filter(ObservationContractRecord.source_type == source_type)
            if status:
                query = query.filter(ObservationContractRecord.status == status)
            pattern = f"%{str(search).strip()}%" if str(search or "").strip() else None
            if pattern:
                query = query.filter(
                    or_(
                        ObservationContractRecord.id.ilike(pattern),
                        ObservationContractRecord.schema_id.ilike(pattern),
                        ObservationContractRecord.schema_ref.ilike(pattern),
                        ObservationContractRecord.entity_type.ilike(pattern),
                        ObservationContractRecord.source_type.ilike(pattern),
                    )
                )
            total = query.order_by(None).count()
            rows = (
                query.order_by(
                    ObservationContractRecord.updated_at.desc(),
                    ObservationContractRecord.id.desc(),
                )
                .offset(page_offset)
                .limit(page_size)
                .all()
            )
            return {
                "items": [row.to_dict() for row in rows],
                "total": total,
                "limit": page_size,
                "offset": page_offset,
            }

    def list_active_validation_dicts(self) -> list[dict[str, Any]]:
        with get_db_session() as db:
            rows = (
                db.query(ObservationContractRecord)
                .filter(ObservationContractRecord.status == "active")
                .order_by(
                    ObservationContractRecord.source_type,
                    ObservationContractRecord.schema_id,
                    ObservationContractRecord.id,
                )
                .all()
            )
            return [_row_to_contract(row).to_validation_dict() for row in rows]

    def backfill_from_parser_templates(self) -> int:
        count = 0
        with get_db_session() as db:
            templates = db.query(ParserTemplate).all()
            for template in templates:
                self.upsert_from_parser_template_row(
                    db,
                    template,
                    status="active" if template.status == "active" else "draft",
                )
                count += 1
        return count
