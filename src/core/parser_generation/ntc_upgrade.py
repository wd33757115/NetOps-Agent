"""Compare frozen NTC Parser evidence with the currently installed package."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from .ntc_resolver import NtcResolution

NTC_REGRESSION_BASELINE_VERSION = "ntc-regression-baseline/v1"
NTC_REGRESSION_DECISION_VERSION = "ntc-regression-decision/v1"


def _stable_digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _sample_map(evidence: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for item in evidence.get("samples") or ():
        sample = dict(item)
        sample_id = str(sample.get("sample_id") or "").strip()
        if not sample_id:
            raise ValueError("NTC regression evidence contains a sample without sample_id")
        if sample_id in result:
            raise ValueError(f"duplicate NTC regression sample_id: {sample_id}")
        result[sample_id] = sample
    return result


def _require_evidence_digest(evidence: dict[str, Any]) -> None:
    payload = dict(evidence)
    digest = str(payload.pop("digest", "") or "")
    if not digest or digest != _stable_digest(payload):
        raise ValueError("NTC objective execution evidence digest mismatch")


def freeze_ntc_resolution(resolution: NtcResolution) -> dict[str, Any]:
    """Create an immutable baseline without embedding raw CLI."""

    if resolution.status != "resolved" or resolution.candidate is None:
        raise ValueError("only a resolved NTC candidate can become a regression baseline")
    evidence = dict(resolution.execution_evidence or {})
    _require_evidence_digest(evidence)
    if str(evidence.get("template_sha256") or "") != resolution.candidate.sha256:
        raise ValueError("NTC evidence does not belong to the resolved template")
    samples = _sample_map(evidence)
    if not samples:
        raise ValueError("NTC regression baseline requires objective sample evidence")
    for sample_id, sample in samples.items():
        required = {
            "record_count",
            "field_names",
            "record_shape_sha256",
            "records_sha256",
        }
        missing = sorted(required - set(sample))
        if missing:
            raise ValueError(
                f"NTC regression sample {sample_id} lacks evidence: {', '.join(missing)}"
            )
    payload = {
        "schema_version": NTC_REGRESSION_BASELINE_VERSION,
        "status": resolution.status,
        "platform": resolution.platform,
        "command": resolution.command,
        "template_name": resolution.template_name,
        "template_sha256": resolution.candidate.sha256,
        "provenance": dict(resolution.candidate.provenance or {}),
        "execution_evidence": evidence,
    }
    payload["baseline_digest"] = _stable_digest(payload)
    return payload


def _difference(
    *,
    category: str,
    field: str,
    before: Any,
    after: Any,
    breaking: bool,
) -> dict[str, Any]:
    return {
        "category": category,
        "field": field,
        "before": before,
        "after": after,
        "breaking": breaking,
    }


def compare_ntc_resolution(
    baseline: dict[str, Any],
    current: NtcResolution,
) -> dict[str, Any]:
    """Compare source provenance and objective parsed values.

    A passing comparison never activates or replaces a Parser. It only produces
    evidence for a subsequent engineer review.
    """

    frozen = dict(baseline or {})
    if frozen.get("schema_version") != NTC_REGRESSION_BASELINE_VERSION:
        raise ValueError("unsupported NTC regression baseline schema")
    baseline_digest = str(frozen.pop("baseline_digest", "") or "")
    if not baseline_digest or baseline_digest != _stable_digest(frozen):
        raise ValueError("NTC regression baseline digest mismatch")

    differences: list[dict[str, Any]] = []
    current_provenance = (
        dict(current.candidate.provenance or {}) if current.candidate else {}
    )
    baseline_provenance = dict(frozen.get("provenance") or {})

    if current.status != "resolved" or current.candidate is None:
        differences.append(
            _difference(
                category="availability",
                field="status",
                before=str(frozen.get("status") or ""),
                after=current.status,
                breaking=True,
            )
        )
        status = "unavailable"
    else:
        _require_evidence_digest(dict(current.execution_evidence or {}))
        if (
            str(current.execution_evidence.get("template_sha256") or "")
            != current.candidate.sha256
        ):
            raise ValueError("current NTC evidence does not belong to the template")
        metadata_fields = ("package_version", "license")
        source_fields = (
            "source_template",
            "source_content_sha256",
            "content_sha256",
            "platform",
            "command",
            "platform_mapping_version",
            "platform_mapping_ids",
            "transformation_version",
            "transformations",
        )
        for field in metadata_fields:
            before = baseline_provenance.get(field)
            after = current_provenance.get(field)
            if before != after:
                differences.append(
                    _difference(
                        category="metadata",
                        field=field,
                        before=before,
                        after=after,
                        breaking=False,
                    )
                )
        for field in source_fields:
            before = baseline_provenance.get(field)
            after = current_provenance.get(field)
            if before != after:
                differences.append(
                    _difference(
                        category="template_source",
                        field=field,
                        before=before,
                        after=after,
                        breaking=False,
                    )
                )
        for field, before, after in (
            (
                "template_name",
                frozen.get("template_name"),
                current.template_name,
            ),
            (
                "template_sha256",
                frozen.get("template_sha256"),
                current.candidate.sha256,
            ),
            (
                "engine_version",
                dict(frozen.get("execution_evidence") or {}).get("engine_version"),
                dict(current.execution_evidence or {}).get("engine_version"),
            ),
        ):
            if before != after:
                differences.append(
                    _difference(
                        category="template_source",
                        field=field,
                        before=before,
                        after=after,
                        breaking=False,
                    )
                )

        baseline_samples = _sample_map(
            dict(frozen.get("execution_evidence") or {})
        )
        current_samples = _sample_map(dict(current.execution_evidence or {}))
        for sample_id in sorted(set(baseline_samples) | set(current_samples)):
            before_sample = baseline_samples.get(sample_id)
            after_sample = current_samples.get(sample_id)
            if before_sample is None or after_sample is None:
                differences.append(
                    _difference(
                        category="parsed_output",
                        field=f"samples.{sample_id}",
                        before=before_sample,
                        after=after_sample,
                        breaking=True,
                    )
                )
                continue
            for field in (
                "record_count",
                "field_names",
                "record_shape_sha256",
                "records_sha256",
                "errors",
            ):
                before = before_sample.get(field)
                after = after_sample.get(field)
                if before != after:
                    differences.append(
                        _difference(
                            category="parsed_output",
                            field=f"samples.{sample_id}.{field}",
                            before=before,
                            after=after,
                            breaking=True,
                        )
                    )

        output_changed = any(
            item["category"] == "parsed_output" for item in differences
        )
        source_changed = any(
            item["category"] == "template_source" for item in differences
        )
        metadata_changed = any(
            item["category"] == "metadata" for item in differences
        )
        if output_changed:
            status = "output_changed"
        elif source_changed:
            status = "template_changed_output_equivalent"
        elif metadata_changed:
            status = "metadata_changed"
        else:
            status = "unchanged"

    result = {
        "schema_version": NTC_REGRESSION_DECISION_VERSION,
        "status": status,
        "breaking": status in {"unavailable", "output_changed"},
        "requires_human_review": status != "unchanged",
        "automatic_replacement_allowed": False,
        "baseline_digest": baseline_digest,
        "current_template_sha256": (
            current.candidate.sha256 if current.candidate else None
        ),
        "current_provenance": current_provenance,
        "differences": differences,
    }
    result["decision_digest"] = _stable_digest(result)
    return result


__all__ = [
    "NTC_REGRESSION_BASELINE_VERSION",
    "NTC_REGRESSION_DECISION_VERSION",
    "compare_ntc_resolution",
    "freeze_ntc_resolution",
]
