"""Stable diagnostics and routing for the Parser Template Agent."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from typing import Any

CONTRACT_FAILURE_CODES = {
    "contract_evidence_conflict",
    "sample_insufficient",
}

ACTION_BY_CODE = {
    "dialect_version_mismatch": "block_for_review",
    "template_syntax_error": "repair_syntax",
    "unsupported_platform_construct": "repair_syntax",
    "zero_records": "repair_entity_anchor",
    "missing_entities": "repair_entity_anchor",
    "extra_entities": "tighten_noise_filter",
    "missing_required_fields": "repair_field_capture",
    "missing_evidenced_fields": "repair_field_capture",
    "missing_evidenced_field_instance": "repair_field_capture",
    "uncovered_structured_line": "repair_field_capture",
    "invalid_field_value": "repair_field_capture",
    "missing_primary_key": "repair_key_integrity",
    "duplicate_primary_key": "repair_key_integrity",
    "record_boundary_error": "repair_record_boundary",
    "record_evidence_mismatch": "repair_record_boundary",
    "implicit_eof_record": "repair_record_boundary",
    "header_or_summary_recorded": "tighten_noise_filter",
    "legal_empty_unproven": "request_contract_review",
    "cross_sample_regression": "repair_entity_anchor",
    "contract_evidence_conflict": "request_contract_review",
    "sample_insufficient": "request_more_samples",
    "model_call_failed": "repair_template",
}


@dataclass(frozen=True)
class ParserDiagnostic:
    code: str
    message: str
    sample_id: str | None = None
    line_number: int | None = None
    expected: Any = None
    actual: Any = None
    evidence: dict[str, Any] = field(default_factory=dict)
    allowed_action: str | None = None

    def __post_init__(self) -> None:
        if self.allowed_action is None:
            object.__setattr__(
                self,
                "allowed_action",
                ACTION_BY_CODE.get(self.code, "repair_template"),
            )

    @property
    def failure_signature(self) -> str:
        payload = {
            "code": self.code,
            "sample_id": self.sample_id,
            "expected": self.expected,
            "actual": self.actual,
            "evidence": self.evidence,
        }
        encoded = json.dumps(payload, ensure_ascii=True, sort_keys=True, default=str)
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    @property
    def requests_contract_review(self) -> bool:
        return self.code in CONTRACT_FAILURE_CODES or self.allowed_action == (
            "request_contract_review"
        )

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "failure_signature": self.failure_signature}


def primary_diagnostic(
    diagnostics: list[ParserDiagnostic] | tuple[ParserDiagnostic, ...],
) -> ParserDiagnostic | None:
    """Pick one deterministic repair target instead of mixing failures."""
    priority = {
        "dialect_version_mismatch": 0,
        "template_syntax_error": 1,
        "unsupported_platform_construct": 2,
        "implicit_eof_record": 3,
        "record_boundary_error": 4,
        "record_evidence_mismatch": 5,
        "zero_records": 6,
        "missing_entities": 7,
        "extra_entities": 8,
        "missing_primary_key": 9,
        "duplicate_primary_key": 10,
        "missing_required_fields": 11,
        "missing_evidenced_field_instance": 12,
        "missing_evidenced_fields": 13,
        "uncovered_structured_line": 14,
        "invalid_field_value": 15,
        "legal_empty_unproven": 16,
        "cross_sample_regression": 17,
        "contract_evidence_conflict": 18,
        "sample_insufficient": 19,
        "model_call_failed": 20,
    }
    if not diagnostics:
        return None
    return min(
        diagnostics,
        key=lambda item: (
            priority.get(item.code, 100),
            item.sample_id or "",
            item.line_number or 0,
            item.failure_signature,
        ),
    )
