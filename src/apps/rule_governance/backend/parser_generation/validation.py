"""Small set of hard validation gates for the bounded Parser pipeline."""

from __future__ import annotations

import hashlib
import io
import json
import re
from dataclasses import asdict, dataclass
from typing import Any, Protocol

import textfsm

from src.apps.rule_governance.backend.parser.parser_bundle import (
    ParserBundleError,
    parse_parser_bundle,
)
from src.apps.rule_governance.backend.parser.record_shape import (
    RecordShapeError,
    expand_list_primary_records,
)
from src.apps.rule_governance.backend.parser.textfsm_input import prepare_textfsm_input
from src.apps.rule_governance.backend.parser.value_types import (
    is_unavailable_value,
    is_valid_ip_value,
)

from .samples import ParserSample, SampleExpectation, SampleRole
from .spec import FieldSpec, ParserSpec, ParserSpecDocument


class ValidationBackend(Protocol):
    def compile(self, template_text: str) -> tuple[str, ...]: ...

    def parse(self, template_text: str, raw_text: str) -> list[dict[str, Any]]: ...


class TextFSMValidationBackend:
    def compile(self, template_text: str) -> tuple[str, ...]:
        """Compile with the same TextFSM runtime used in production.

        Platform dialect lint remains available to authors and audits, but it is
        not execution truth. Engineer-uploaded templates may legitimately
        use native constructs such as ``Required``, ``NoRecord`` and implicit
        EOF recording. The generation loop must execute those templates and
        give their actual rows (or runtime error) to semantic review instead of
        rejecting valid TextFSM syntax before it runs.
        """

        try:
            textfsm.TextFSM(io.StringIO(template_text))
        except Exception as exc:
            return (f"template_syntax_error: compile failed: {exc}",)
        return ()

    def parse(self, template_text: str, raw_text: str) -> list[dict[str, Any]]:
        parser = textfsm.TextFSM(io.StringIO(template_text))
        rows = parser.ParseText(prepare_textfsm_input(template_text, raw_text))
        return [dict(zip(parser.header, row, strict=True)) for row in rows]

    def field_names(self, template_text: str) -> tuple[str, ...]:
        """Return the executable TextFSM schema used by ParserRuntime."""

        parser = textfsm.TextFSM(io.StringIO(template_text))
        return tuple(str(name) for name in parser.header)


def _field_inventory_error(
    *,
    template_text: str,
    spec: ParserSpec,
    backend: ValidationBackend,
) -> str | None:
    """Compare declared and executable fields when the backend can expose them."""

    read_fields = getattr(backend, "field_names", None)
    if not callable(read_fields):
        return None
    expected = {field.name for field in spec.fields}
    actual = {str(name) for name in read_fields(template_text)}
    if expected == actual:
        return None
    return (
        "ParserSpec field inventory does not match the executable TextFSM schema: "
        f"missing={sorted(expected - actual)}, extra={sorted(actual - expected)}"
    )


def _record_shape(records: list[dict[str, Any]]) -> tuple[tuple[tuple[str, bool], ...], ...]:
    return tuple(
        tuple(
            sorted(
                (str(key), bool(str(value or "").strip()))
                for key, value in record.items()
            )
        )
        for record in records
    )


def _source_compatibility(
    *,
    template_text: str,
    sample: ParserSample,
    validation_records: list[dict[str, Any]],
    backend: ValidationBackend,
) -> tuple[bool, bool, tuple[str, ...]]:
    """Execute raw evidence without comparing sensitive values to redacted values."""

    if sample.raw_text == sample.validation_text:
        return True, True, ()
    try:
        source_records = backend.parse(template_text, sample.raw_text)
    except Exception as exc:
        return False, False, (f"source_parse_failed: {exc}",)
    errors: list[str] = []
    if len(source_records) != len(validation_records):
        errors.append(
            "source record count differs from sanitized evidence: "
            f"{len(source_records)} != {len(validation_records)}"
        )
    elif _record_shape(source_records) != _record_shape(validation_records):
        errors.append(
            "source record field/non-empty shape differs from sanitized evidence"
        )
    return True, not errors, tuple(errors)


@dataclass(frozen=True)
class SampleValidation:
    sample_id: str
    role: SampleRole
    passed: bool
    record_count: int
    records: tuple[dict[str, Any], ...]
    errors: tuple[str, ...]
    expected_record_count: int | None = None
    expected_primary_keys: tuple[tuple[str, ...], ...] = ()
    actual_primary_keys: tuple[tuple[str, ...], ...] = ()


@dataclass(frozen=True)
class ValidationReport:
    passed: bool
    hard_gates: dict[str, bool]
    samples: tuple[SampleValidation, ...]
    diagnostics: tuple[str, ...]

    @property
    def primary_diagnostic(self) -> str:
        return self.diagnostics[0] if self.diagnostics else "validation_failed"

    @property
    def failure_signature(self) -> str:
        payload = {
            "failed_gates": [
                name for name, passed in sorted(self.hard_gates.items()) if not passed
            ],
            "diagnostics": list(self.diagnostics),
            "samples": [
                {
                    "sample_id": sample.sample_id,
                    "role": sample.role.value,
                    "errors": list(sample.errors),
                }
                for sample in self.samples
                if sample.errors
            ],
        }
        encoded = json.dumps(
            payload,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    def to_dict(self) -> dict[str, object]:
        return asdict(self)

    def parsed_result_preview(
        self,
        *,
        max_samples: int = 8,
        max_records_per_sample: int = 20,
        max_value_chars: int = 500,
    ) -> list[dict[str, object]]:
        def preview_value(value: Any) -> Any:
            if isinstance(value, str):
                return value[:value_limit] + ("..." if len(value) > value_limit else "")
            if isinstance(value, (list, tuple)):
                return [preview_value(item) for item in value[:200]]
            if isinstance(value, dict):
                return {
                    str(key): preview_value(item)
                    for key, item in list(value.items())[:100]
                }
            if value is None or isinstance(value, (bool, int, float)):
                return value
            text = str(value)
            return text[:value_limit] + ("..." if len(text) > value_limit else "")

        sample_limit = max(1, int(max_samples))
        record_limit = max(1, int(max_records_per_sample))
        value_limit = max(32, int(max_value_chars))
        output: list[dict[str, object]] = []
        for sample in self.samples[:sample_limit]:
            records = list(sample.records)
            if len(records) <= record_limit:
                indexed_records = list(enumerate(records, start=1))
            else:
                head_count = max(1, record_limit - 5)
                indexed_records = [
                    *list(enumerate(records[:head_count], start=1)),
                    *[
                        (len(records) - len(records[-5:]) + offset, record)
                        for offset, record in enumerate(records[-5:], start=1)
                    ],
                ]
            output.append(
                {
                    "sample_id": sample.sample_id,
                    "role": sample.role.value,
                    "record_count": sample.record_count,
                    "records": [
                        {
                            "record_index": index,
                            "values": {
                                str(key): preview_value(value)
                                for key, value in record.items()
                            },
                        }
                        for index, record in indexed_records
                    ],
                    "records_omitted": max(0, len(records) - len(indexed_records)),
                    "actual_primary_keys": [
                        list(key) for key in sample.actual_primary_keys[:50]
                    ],
                    "errors": list(sample.errors),
                }
            )
        return output

    def repair_diagnostic(self) -> dict[str, object]:
        failed_gates = [name for name, passed in self.hard_gates.items() if not passed]
        code_by_gate = {
            "compiled": "template_compile_failed",
            "field_inventory_aligned": "field_inventory_mismatch",
            "positive_samples_parsed": "zero_records",
            "required_fields_complete": "missing_required_fields",
            "primary_keys_valid": "primary_key_invalid",
            "field_types_valid": "invalid_field_value",
            "values_normalized": "non_normalized_field_value",
            "expectations_matched": "entity_expectation_mismatch",
            "negative_samples_rejected": "negative_sample_matched",
            "source_executed": "source_parse_failed",
            "source_shape_compatible": "source_structure_mismatch",
        }
        action_by_code = {
            "template_compile_failed": "repair_syntax",
            "field_inventory_mismatch": "repair_field_inventory",
            "zero_records": "repair_entity_anchor",
            "missing_required_fields": "repair_field_capture",
            "primary_key_invalid": "repair_key_integrity",
            "invalid_field_value": "repair_field_capture",
            "non_normalized_field_value": "repair_field_capture",
            "entity_expectation_mismatch": "repair_record_boundary",
            "negative_sample_matched": "tighten_noise_filter",
            "source_parse_failed": "repair_field_capture",
            "source_structure_mismatch": "repair_record_boundary",
        }
        primary_gate = failed_gates[0] if failed_gates else "validation"
        code = code_by_gate.get(primary_gate, "hard_validation_failed")
        return {
            "failure_type": "hard_validation_failed",
            "failure_signature": self.failure_signature,
            "failed_gates": failed_gates,
            "primary_diagnostic": {
                "code": code,
                "message": self.primary_diagnostic,
                "allowed_action": action_by_code.get(code, "repair_template"),
            },
            "diagnostics": list(self.diagnostics)[:20],
            "parsed_results": self.parsed_result_preview(),
            "samples": [
                {
                    "sample_id": sample.sample_id,
                    "role": sample.role.value,
                    "record_count": sample.record_count,
                    "expected_record_count": sample.expected_record_count,
                    "expected_primary_keys": [
                        list(key) for key in sample.expected_primary_keys[:50]
                    ],
                    "actual_primary_keys": [
                        list(key) for key in sample.actual_primary_keys[:50]
                    ],
                    "errors": list(sample.errors),
                }
                for sample in self.samples
                if sample.errors
            ][:20],
        }


def execute_template_candidate(
    template_text: str,
    samples: tuple[ParserSample, ...],
    backend: ValidationBackend,
    *,
    spec: ParserSpec | None = None,
) -> ValidationReport:
    """Return objective TextFSM compile and execution evidence.

    Semantic completeness remains a model/engineer decision, but a positive
    sample cannot be treated as executable evidence when a required record set
    yields no records at all.  Without this gate a template can compile and
    consume every source line while never emitting ``-> Record`` (for example
    an explicit EOF state), then incorrectly reach the human-approval step.
    """

    compile_errors = backend.compile(template_text)
    gates = {
        "compiled": not compile_errors,
        "executed": not compile_errors,
        "field_inventory_aligned": not compile_errors,
        "positive_samples_parsed": not compile_errors,
        "source_executed": not compile_errors,
        "source_shape_compatible": not compile_errors,
    }
    if compile_errors:
        return ValidationReport(
            passed=False,
            hard_gates=gates,
            samples=(),
            diagnostics=tuple(compile_errors),
        )
    diagnostics: list[str] = []
    sample_results: list[SampleValidation] = []
    for sample in samples:
        errors: list[str] = []
        try:
            records = backend.parse(template_text, sample.validation_text)
        except Exception as exc:
            records = []
            errors.append(f"parse_failed: {exc}")
            gates["executed"] = False
        source_executed, source_compatible, source_errors = _source_compatibility(
            template_text=template_text,
            sample=sample,
            validation_records=records,
            backend=backend,
        )
        if not source_executed:
            gates["source_executed"] = False
        if not source_compatible:
            gates["source_shape_compatible"] = False
        errors.extend(source_errors)
        if (
            spec is not None
            and sample.role is not SampleRole.NEGATIVE
            and not records
        ):
            legal_empty = bool(
                sample.expectation
                and sample.expectation.record_count == 0
            ) or (
                spec is not None
                and spec.allow_empty
                and any(
                    re.search(
                        pattern,
                        sample.validation_text,
                        re.IGNORECASE | re.MULTILINE,
                    )
                    for pattern in spec.empty_patterns
                )
            ) or (spec is not None and spec.presence == "optional")
            if not legal_empty:
                gates["positive_samples_parsed"] = False
                errors.append("positive sample produced no records")
        diagnostics.extend(
            f"{sample.sample_id}: {error}" for error in errors
        )
        sample_results.append(
            SampleValidation(
                sample_id=sample.sample_id,
                role=sample.role,
                passed=not errors,
                record_count=len(records),
                records=tuple(records),
                errors=tuple(errors),
            )
        )
    return ValidationReport(
        passed=all(gates.values()) and all(item.passed for item in sample_results),
        hard_gates=gates,
        samples=tuple(sample_results),
        diagnostics=tuple(diagnostics),
    )


def validate_template_execution(
    template_text: str,
    spec: ParserSpec | ParserSpecDocument,
    samples: tuple[ParserSample, ...],
    backend: ValidationBackend,
) -> ValidationReport:
    """Objectively execute a template or Parser bundle without semantic gates."""

    if isinstance(spec, ParserSpec):
        return execute_template_candidate(
            template_text,
            samples,
            backend,
            spec=spec,
        )
    try:
        bundle = parse_parser_bundle(template_text)
    except ParserBundleError as exc:
        bundle = None
        bundle_error = str(exc)
    else:
        bundle_error = ""
    expected_names = {item.record_set_name for item in spec.record_sets}
    base_gates = {
        "bundle_complete": False,
        "compiled": False,
        "executed": False,
        "field_inventory_aligned": False,
        "source_executed": False,
        "source_shape_compatible": False,
    }
    if bundle is None:
        return ValidationReport(
            passed=False,
            hard_gates=base_gates,
            samples=(),
            diagnostics=(
                bundle_error or "candidate is not a netops.parser_bundle/v1 asset",
            ),
        )
    templates = {item.name: item.template_text for item in bundle.record_sets}
    if set(templates) != expected_names:
        return ValidationReport(
            passed=False,
            hard_gates=base_gates,
            samples=(),
            diagnostics=(
                "Parser bundle record sets do not match ParserSpec: "
                f"missing={sorted(expected_names - set(templates))}, "
                f"extra={sorted(set(templates) - expected_names)}",
            ),
        )
    child_reports = [
        (
            record_set.record_set_name,
            execute_template_candidate(
                templates[record_set.record_set_name],
                samples,
                backend,
                spec=record_set,
            ),
        )
        for record_set in spec.record_sets
    ]
    aggregate_gates = {
        "bundle_complete": True,
        "compiled": all(
            report.hard_gates.get("compiled", False)
            for _, report in child_reports
        ),
        "executed": all(
            report.hard_gates.get("executed", False)
            for _, report in child_reports
        ),
        "field_inventory_aligned": all(
            report.hard_gates.get("field_inventory_aligned", False)
            for _, report in child_reports
        ),
        "source_executed": all(
            report.hard_gates.get("source_executed", False)
            for _, report in child_reports
        ),
        "source_shape_compatible": all(
            report.hard_gates.get("source_shape_compatible", False)
            for _, report in child_reports
        ),
    }
    combined_samples: list[SampleValidation] = []
    diagnostics: list[str] = []
    for record_set_name, report in child_reports:
        diagnostics.extend(
            f"{record_set_name}: {diagnostic}"
            for diagnostic in report.diagnostics
        )
        combined_samples.extend(
            SampleValidation(
                sample_id=f"{sample.sample_id}::{record_set_name}",
                role=sample.role,
                passed=sample.passed,
                record_count=sample.record_count,
                records=tuple(
                    {"__record_set__": record_set_name, **record}
                    for record in sample.records
                ),
                errors=tuple(
                    f"{record_set_name}: {error}" for error in sample.errors
                ),
            )
            for sample in report.samples
        )
    return ValidationReport(
        passed=all(aggregate_gates.values()),
        hard_gates=aggregate_gates,
        samples=tuple(combined_samples),
        diagnostics=tuple(diagnostics),
    )


def _without_record_expectations(
    spec: ParserSpec | ParserSpecDocument,
) -> ParserSpec | ParserSpecDocument:
    def strip(record_set: ParserSpec) -> ParserSpec:
        if not record_set.expected_records:
            return record_set
        return ParserSpec(
            entity=record_set.entity,
            primary_keys=record_set.primary_keys,
            fields=record_set.fields,
            allow_empty=record_set.allow_empty,
            empty_patterns=record_set.empty_patterns,
            name=record_set.name,
            cardinality=record_set.cardinality,
            expected_records=(),
            presence=record_set.presence,
        )

    if isinstance(spec, ParserSpec):
        return strip(spec)
    return ParserSpecDocument(
        business_purpose=spec.business_purpose,
        record_sets=tuple(strip(item) for item in spec.record_sets),
        version=spec.version,
    )


def validate_template_runtime_compatibility(
    template_text: str,
    spec: ParserSpec | ParserSpecDocument,
    samples: tuple[ParserSample, ...],
    backend: ValidationBackend,
) -> ValidationReport:
    """Verify the candidate can satisfy the production ParserRuntime contract.

    This deliberately ignores advisory pre-generation record counts and expected
    entity sets. It checks only executable invariants that production will enforce:
    compilation, non-empty positive execution unless explicitly allowed, required
    field presence, primary-key integrity, declared field types, negative samples,
    and source/sanitized structural compatibility. LLM and engineer review remain
    authoritative for business usefulness, record scope and semantic completeness.
    """

    expectation_free_samples = tuple(
        ParserSample(
            sample_id=sample.sample_id,
            raw_text=sample.raw_text,
            role=sample.role,
            prompt_text=sample.prompt_text,
            expectation=None,
            validation_text_override=sample.validation_text_override,
        )
        for sample in samples
    )
    return validate_template_candidate(
        template_text,
        _without_record_expectations(spec),
        expectation_free_samples,
        backend,
    )


def _field_value_valid(value: str, field: FieldSpec) -> bool:
    text = str(value or "").strip()
    if not text:
        return True
    if is_unavailable_value(text, field.unavailable_tokens):
        return True
    if field.value_type == "number":
        return re.fullmatch(r"[-+]?\d+(?:\.\d+)?", text) is not None
    if field.value_type == "percent":
        return re.fullmatch(r"[-+]?\d+(?:\.\d+)?%?", text) is not None
    if field.value_type == "enum":
        return text.casefold() in {item.casefold() for item in field.values}
    if field.value_type == "ip":
        return is_valid_ip_value(text)
    if field.value_type == "ip_or_unassigned":
        return is_valid_ip_value(text, allow_unassigned=True)
    if field.value_type == "regex":
        return re.fullmatch(str(field.pattern), text) is not None
    return True


def _primary_key_value(record: dict[str, Any], field: FieldSpec) -> str:
    """Return the audited identity component without changing Parser output.

    Some vendor CLIs omit a source token for a stable aggregate record (for
    example an unchannelized optic).  The Parser must retain that raw blank for
    the Adapter Projection, which owns canonical normalization.  A declared
    ``primary_key_default`` only gives the Parser validator a stable, explicit
    identity value for uniqueness and expectation checks.
    """

    value = str(record.get(field.name, "") or "").strip()
    return value or str(field.primary_key_default or "").strip()


def _validate_single_template_candidate(
    template_text: str,
    spec: ParserSpec,
    samples: tuple[ParserSample, ...],
    backend: ValidationBackend,
) -> ValidationReport:
    compile_errors = backend.compile(template_text)
    gates = {
        "compiled": not compile_errors,
        "executed": not compile_errors,
        "field_inventory_aligned": not compile_errors,
        "positive_samples_parsed": True,
        "required_fields_complete": True,
        "primary_keys_valid": True,
        "field_types_valid": True,
        "values_normalized": True,
        "expectations_matched": True,
        "negative_samples_rejected": True,
        "source_executed": not compile_errors,
        "source_shape_compatible": not compile_errors,
    }
    diagnostics: list[str] = list(compile_errors)
    if compile_errors:
        return ValidationReport(False, gates, (), tuple(diagnostics))

    inventory_error = _field_inventory_error(
        template_text=template_text,
        spec=spec,
        backend=backend,
    )
    if inventory_error:
        gates["field_inventory_aligned"] = False
        diagnostics.append(inventory_error)

    sample_results: list[SampleValidation] = []
    for sample in samples:
        errors: list[str] = []
        seen_keys: set[tuple[str, ...]] = set()

        def add_error(message: str) -> None:
            if message not in errors:
                errors.append(message)

        try:
            records = backend.parse(template_text, sample.validation_text)
        except Exception as exc:
            records = []
            gates["executed"] = False
            add_error(f"parse_failed: {exc}")
        else:
            try:
                records = expand_list_primary_records(records, spec.primary_keys)
            except RecordShapeError as exc:
                records = []
                gates["executed"] = False
                add_error(f"record_shape_invalid: {exc}")
        source_executed, source_compatible, source_errors = _source_compatibility(
            template_text=template_text,
            sample=sample,
            validation_records=records,
            backend=backend,
        )
        if not source_executed:
            gates["source_executed"] = False
        if not source_compatible:
            gates["source_shape_compatible"] = False
        for source_error in source_errors:
            add_error(source_error)
        if sample.role is SampleRole.NEGATIVE:
            if records:
                gates["negative_samples_rejected"] = False
                add_error("negative sample produced records")
        else:
            legal_empty = bool(
                sample.expectation
                and sample.expectation.record_count == 0
            ) or (
                spec.allow_empty
                and any(
                    re.search(
                        pattern,
                        sample.validation_text,
                        re.IGNORECASE | re.MULTILINE,
                    )
                    for pattern in spec.empty_patterns
                )
            ) or spec.presence == "optional"
            if not records and not legal_empty:
                gates["positive_samples_parsed"] = False
                add_error("positive sample produced no records")
            if spec.cardinality == "singleton" and len(records) > 1:
                gates["primary_keys_valid"] = False
                add_error("singleton record set produced more than one record")
            for record in records:
                missing = [
                    name
                    for name in spec.required_fields
                    if (
                        (name not in record or not str(record.get(name) or "").strip())
                        and spec.field_map[name].primary_key_default is None
                    )
                ]
                if missing:
                    gates["required_fields_complete"] = False
                    add_error(f"missing required fields: {', '.join(missing)}")
                if spec.primary_keys:
                    key = tuple(
                        _primary_key_value(record, spec.field_map[name])
                        for name in spec.primary_keys
                    )
                    if any(not value for value in key) or key in seen_keys:
                        gates["primary_keys_valid"] = False
                        add_error("primary key is empty or duplicated")
                    seen_keys.add(key)
                invalid_fields = [
                    field.name
                    for field in spec.fields
                    if not _field_value_valid(str(record.get(field.name, "")), field)
                ]
                if invalid_fields:
                    gates["field_types_valid"] = False
                    add_error(f"invalid field values: {', '.join(invalid_fields)}")
                non_normalized_fields = [
                    field.name
                    for field in spec.fields
                    if isinstance(record.get(field.name), str)
                    and record.get(field.name) != str(record.get(field.name)).strip()
                ]
                if non_normalized_fields:
                    gates["values_normalized"] = False
                    add_error(
                        "field values contain leading or trailing whitespace: "
                        f"{', '.join(non_normalized_fields)}"
                    )
            expectation = sample.expectation
            if expectation:
                if (
                    expectation.record_count is not None
                    and len(records) != expectation.record_count
                ):
                    gates["expectations_matched"] = False
                    add_error(
                        f"record count {len(records)} != expected {expectation.record_count}"
                    )
                if expectation.primary_keys and seen_keys != set(expectation.primary_keys):
                    gates["expectations_matched"] = False
                    add_error("parsed primary key set does not match expected keys")
        if errors:
            diagnostics.extend(f"{sample.sample_id}: {error}" for error in errors)
        sample_results.append(
            SampleValidation(
                sample_id=sample.sample_id,
                role=sample.role,
                passed=not errors,
                record_count=len(records),
                records=tuple(records),
                errors=tuple(errors),
                expected_record_count=(
                    sample.expectation.record_count if sample.expectation else None
                ),
                expected_primary_keys=(
                    tuple(sorted(sample.expectation.primary_keys))
                    if sample.expectation
                    else ()
                ),
                actual_primary_keys=tuple(sorted(seen_keys)),
            )
        )
    return ValidationReport(
        passed=all(gates.values()) and all(item.passed for item in sample_results),
        hard_gates=gates,
        samples=tuple(sample_results),
        diagnostics=tuple(diagnostics),
    )


def _record_set_samples(
    samples: tuple[ParserSample, ...],
    spec: ParserSpec,
) -> tuple[ParserSample, ...]:
    expected_by_sample = {
        str(item.get("sample_id") or ""): [
            dict(record)
            for record in item.get("records") or ()
            if isinstance(record, dict)
        ]
        for item in spec.expected_records
        if str(item.get("sample_id") or "").strip()
    }
    output: list[ParserSample] = []
    for sample in samples:
        expected_records = expected_by_sample.get(sample.sample_id)
        expectation = None
        if expected_records is not None:
            expectation = SampleExpectation(
                record_count=len(expected_records),
                primary_keys=frozenset(
                    tuple(str(record.get(key) or "") for key in spec.primary_keys)
                    for record in expected_records
                )
                if spec.primary_keys
                else frozenset(),
            )
        output.append(
            ParserSample(
                sample_id=sample.sample_id,
                raw_text=sample.raw_text,
                role=sample.role,
                prompt_text=sample.prompt_text,
                expectation=expectation,
                validation_text_override=sample.validation_text_override,
            )
        )
    return tuple(output)


def validate_template_candidate(
    template_text: str,
    spec: ParserSpec | ParserSpecDocument,
    samples: tuple[ParserSample, ...],
    backend: ValidationBackend,
) -> ValidationReport:
    """Validate one TextFSM template or an immutable multi-record Parser bundle."""

    if isinstance(spec, ParserSpec):
        return _validate_single_template_candidate(
            template_text,
            spec,
            samples,
            backend,
        )
    try:
        bundle = parse_parser_bundle(template_text)
    except ParserBundleError as exc:
        bundle = None
        bundle_error = str(exc)
    else:
        bundle_error = ""
    base_gates = {
        "bundle_complete": False,
        "compiled": False,
        "executed": False,
        "field_inventory_aligned": False,
        "positive_samples_parsed": False,
        "required_fields_complete": False,
        "primary_keys_valid": False,
        "field_types_valid": False,
        "values_normalized": False,
        "expectations_matched": False,
        "negative_samples_rejected": False,
        "source_executed": False,
        "source_shape_compatible": False,
    }
    if bundle is None:
        return ValidationReport(
            passed=False,
            hard_gates=base_gates,
            samples=(),
            diagnostics=(
                bundle_error
                or "candidate is not a netops.parser_bundle/v1 asset",
            ),
        )
    templates = {item.name: item.template_text for item in bundle.record_sets}
    expected_names = {item.record_set_name for item in spec.record_sets}
    if set(templates) != expected_names:
        missing = sorted(expected_names - set(templates))
        extra = sorted(set(templates) - expected_names)
        return ValidationReport(
            passed=False,
            hard_gates=base_gates,
            samples=(),
            diagnostics=(
                "Parser bundle record sets do not match ParserSpec: "
                f"missing={missing}, extra={extra}",
            ),
        )

    aggregate_gates = dict(base_gates)
    aggregate_gates["bundle_complete"] = True
    child_reports: list[tuple[str, ValidationReport]] = []
    for record_set in spec.record_sets:
        child_reports.append(
            (
                record_set.record_set_name,
                _validate_single_template_candidate(
                    templates[record_set.record_set_name],
                    record_set,
                    _record_set_samples(samples, record_set),
                    backend,
                ),
            )
        )
    aggregate_gates["executed"] = all(
        report.hard_gates.get("executed", False)
        for _, report in child_reports
    )
    for gate in aggregate_gates:
        if gate == "bundle_complete":
            continue
        aggregate_gates[gate] = all(
            report.hard_gates.get(gate, False)
            for _, report in child_reports
        )

    bundle_coverage_diagnostics: list[str] = []
    for sample in samples:
        if sample.role is SampleRole.NEGATIVE:
            continue
        total_records = sum(
            next(
                (
                    item.record_count
                    for item in report.samples
                    if item.sample_id == sample.sample_id
                ),
                0,
            )
            for _, report in child_reports
        )
        legal_bundle_empty = any(
            record_set.allow_empty
            and any(
                re.search(
                    pattern,
                    sample.validation_text,
                    re.IGNORECASE | re.MULTILINE,
                )
                for pattern in record_set.empty_patterns
            )
            for record_set in spec.record_sets
        )
        if total_records == 0 and not legal_bundle_empty:
            aggregate_gates["positive_samples_parsed"] = False
            bundle_coverage_diagnostics.append(
                f"{sample.sample_id}: positive sample produced no records across the Parser bundle"
            )

    combined_samples: list[SampleValidation] = []
    diagnostics: list[str] = []
    diagnostics.extend(bundle_coverage_diagnostics)
    for record_set_name, report in child_reports:
        diagnostics.extend(
            f"{record_set_name}: {diagnostic}"
            for diagnostic in report.diagnostics
        )
        for sample in report.samples:
            combined_samples.append(
                SampleValidation(
                    sample_id=f"{sample.sample_id}::{record_set_name}",
                    role=sample.role,
                    passed=sample.passed,
                    record_count=sample.record_count,
                    records=tuple(
                        {"__record_set__": record_set_name, **record}
                        for record in sample.records
                    ),
                    errors=tuple(
                        f"{record_set_name}: {error}"
                        for error in sample.errors
                    ),
                    expected_record_count=sample.expected_record_count,
                    expected_primary_keys=sample.expected_primary_keys,
                    actual_primary_keys=sample.actual_primary_keys,
                )
            )
    return ValidationReport(
        passed=all(report.passed for _, report in child_reports)
        and all(aggregate_gates.values()),
        hard_gates=aggregate_gates,
        samples=tuple(combined_samples),
        diagnostics=tuple(diagnostics),
    )
