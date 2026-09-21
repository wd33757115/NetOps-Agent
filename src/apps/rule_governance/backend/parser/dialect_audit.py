"""Read-only audit of active TextFSM assets against the generation dialect."""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from src.infrastructure.db.models import ParserTemplate
from src.infrastructure.db.postgres import get_db_session

from .textfsm_dialect import (
    DEFAULT_TEXTFSM_DIALECT,
    dialect_hash,
    installed_textfsm_version,
    lint_textfsm_template,
)


def _resolve_asset_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else Path.cwd() / path


def audit_textfsm_records(records: Iterable[dict[str, Any]]) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    for record in records:
        path = _resolve_asset_path(str(record.get("asset_path") or ""))
        embedded_text = record.get("template_text")
        violations: list[dict[str, Any]] = []
        digest = None
        if isinstance(embedded_text, str):
            template_text = embedded_text
        elif path.is_file():
            template_text = path.read_text(encoding="utf-8")
        else:
            template_text = None
            violations.append(
                {
                    "code": "asset_missing",
                    "message": "template asset content does not exist",
                }
            )
        if template_text is not None:
            digest = hashlib.sha256(template_text.encode("utf-8")).hexdigest()
            if record.get("sha256") and digest != record.get("sha256"):
                violations.append(
                    {
                        "code": "asset_hash_mismatch",
                        "message": "template asset hash does not match metadata",
                    }
                )
            violations.extend(item.to_dict() for item in lint_textfsm_template(template_text))
        validation = dict(record.get("validation_json") or {})
        regression = dict(validation.get("regression_validation") or {})
        runtime_blocking = {
            "asset_missing",
            "asset_hash_mismatch",
            "dialect_version_mismatch",
            "template_syntax_error",
        }
        items.append(
            {
                "template_id": record.get("id"),
                "vendor": record.get("vendor"),
                "profile": record.get("profile"),
                "canonical_command": record.get("canonical_command"),
                "version": record.get("version"),
                "asset_sha256": digest,
                "runtime_compatible": not any(
                    item["code"] in runtime_blocking for item in violations
                ),
                "platform_compliant": not violations,
                "regression_evidence_passed": regression.get("passed") is True,
                "violations": violations,
            }
        )
    violating = [item for item in items if not item["platform_compliant"]]
    runtime_blocked = [item for item in items if not item["runtime_compatible"]]
    regression_missing = [item for item in items if not item["regression_evidence_passed"]]
    return {
        "runtime_version": installed_textfsm_version(),
        "dialect_version": DEFAULT_TEXTFSM_DIALECT.dialect_version,
        "dialect_hash": dialect_hash(),
        "template_count": len(items),
        "platform_compliant_count": len(items) - len(violating),
        "platform_violation_count": len(violating),
        "runtime_blocked_count": len(runtime_blocked),
        "regression_evidence_missing_count": len(regression_missing),
        "ready": not violating and not regression_missing,
        "items": items,
    }


class TextFSMDialectAuditService:
    def audit(self, *, status: str = "active") -> dict[str, Any]:
        with get_db_session() as db:
            query = db.query(ParserTemplate)
            if status:
                query = query.filter(ParserTemplate.status == status)
            rows = [
                row.to_dict(include_content=True)
                for row in query.order_by(ParserTemplate.updated_at.desc()).all()
            ]
        return audit_textfsm_records(rows)
