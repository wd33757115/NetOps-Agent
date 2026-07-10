"""PostgreSQL repository for ObservationContract assets."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from src.core.observation.contracts import (
    ObservationContract,
    build_contract_payload,
)
from src.infrastructure.db.models import ObservationContractRecord, ParserTemplate
from src.infrastructure.db.postgres import get_db_session


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


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
        optional_fields=tuple(row.optional_fields_json or ()),
        validators=dict(row.validators_json or {}),
        ignore_fields=tuple(row.ignore_fields_json or ()),
        allow_empty=bool(row.allow_empty),
        empty_patterns=tuple(row.empty_patterns_json or ()),
        state_semantics=dict(row.state_semantics_json or {}),
        version=int(row.version or 1),
        contract_sha256=row.contract_sha256,
        status=row.status,
        compatibility=dict(row.compatibility_json or {}),
    )


class ObservationContractRepository:
    """Persist and resolve source-agnostic observation contracts."""

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
        elif row.contract_sha256 != payload["contract_sha256"]:
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
            row.required_fields_json = list(payload["required_fields"])
            row.optional_fields_json = list(payload["optional_fields"])
            row.validators_json = payload["validators"]
            row.ignore_fields_json = list(payload["ignore_fields"])
            row.allow_empty = bool(payload["allow_empty"])
            row.empty_patterns_json = list(payload["empty_patterns"])
            row.state_semantics_json = payload["state_semantics"]
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

    def upsert_from_parser_template_row(
        self,
        db,
        template: ParserTemplate,
        *,
        status: str | None = None,
    ) -> ObservationContract:
        contract_status = status or (
            "active" if template.status == "active" else "draft"
        )
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
        next_identity = (
            str(validation.get("entity_type") or template.category or "record"),
            tuple(validation.get("primary_keys") or ()),
        )
        previous_identity = (
            (str(previous.entity_type), tuple(previous.primary_keys_json or ()))
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
        if breaking and contract_status == "active" and not (
            approval.get("approved") is True and approval.get("action") == "rebaseline"
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
