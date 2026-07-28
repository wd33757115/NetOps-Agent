"""Deterministic patrol deliverables rendered from one immutable report manifest."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import zipfile
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

_ARCHIVE_TIMESTAMP = (1980, 1, 1, 0, 0, 0)
_SEVERITY_ORDER = {
    "critical": 5,
    "major": 4,
    "warning": 3,
    "minor": 2,
    "info": 1,
    "unknown": 0,
}
_STATUS_LABELS = {
    "healthy": "正常",
    "at_risk": "存在风险",
    "unable_to_determine": "无法判断",
    "not_applicable": "不适用",
    "informational": "信息性结果",
}
_COMPLETENESS_LABELS = {
    "complete": "完整",
    "partial": "部分完整",
    "insufficient": "证据不足",
}
_COMPARISON_LABELS = {
    "comparable": "可比较",
    "first_observation": "首次观测",
    "baseline_missing": "缺少基线",
    "baseline_stale": "基线过期",
    "schema_incompatible": "口径不兼容",
}
_SEVERITY_LABELS = {
    "critical": "严重",
    "major": "重大",
    "warning": "告警",
    "minor": "提示",
    "info": "信息",
    "unknown": "未知",
}


@dataclass(frozen=True, slots=True)
class ReportArtifact:
    """One immutable report artifact ready for object storage."""

    name: str
    filename: str
    content_type: str
    content: bytes

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.content).hexdigest()


class CheckItemReportMaterializer:
    """Atomically materialize every formal artifact from one manifest."""

    def __init__(
        self,
        *,
        manifest_service: Any | None = None,
        object_storage: Any | None = None,
    ) -> None:
        if manifest_service is None:
            from src.core.check_items.report_manifest import (
                ReportManifestService,
            )

            manifest_service = ReportManifestService()
        if object_storage is None:
            from src.infrastructure.storage.minio_client import (
                get_minio_storage,
            )

            object_storage = get_minio_storage()
        self._manifest_service = manifest_service
        self._object_storage = object_storage

    def materialize(
        self,
        patrol_batch_id: str,
        *,
        generated_by: str,
    ) -> dict[str, Any]:
        manifest = self._manifest_service.build(
            patrol_batch_id,
            generated_by=generated_by,
        )
        if str(manifest.get("status") or "") != "final":
            reasons = (
                _as_dict(
                    _as_dict(
                        manifest.get("source_facts_json")
                    ).get("readiness")
                ).get("reason_codes")
                or []
            )
            raise ValueError(
                "formal report is blocked by non-final facts: "
                + ",".join(str(item) for item in reasons)
            )
        artifacts = render_check_item_artifacts(manifest)
        source_digest = str(manifest["source_digest"])
        prefix = (
            f"reports/patrol/{patrol_batch_id}/"
            f"{source_digest}"
        )
        object_keys: dict[str, str] = {}
        sha256_by_artifact: dict[str, str] = {}
        for name, artifact in artifacts.items():
            object_key = f"{prefix}/{artifact.filename}"
            uploaded = self._object_storage.upload_file(
                object_key,
                artifact.content,
                content_type=artifact.content_type,
            )
            if not uploaded:
                raise RuntimeError(
                    f"failed to upload report artifact: {name}"
                )
            object_keys[name] = object_key
            sha256_by_artifact[name] = artifact.sha256
        attached = self._manifest_service.attach_artifacts(
            str(manifest["id"]),
            object_keys=object_keys,
            sha256_by_artifact=sha256_by_artifact,
        )
        return {
            "success": True,
            "patrol_batch_id": patrol_batch_id,
            "report_manifest_id": str(manifest["id"]),
            "source_digest": source_digest,
            "artifact_object_keys": object_keys,
            "artifact_sha256": sha256_by_artifact,
            "artifact_bytes": {
                name: len(artifact.content)
                for name, artifact in artifacts.items()
            },
            "manifest": attached,
        }


_ARTIFACT_CONTENT_TYPES = {
    "markdown": "text/markdown; charset=utf-8",
    "docx": (
        "application/vnd.openxmlformats-officedocument."
        "wordprocessingml.document"
    ),
    "excel": (
        "application/vnd.openxmlformats-officedocument."
        "spreadsheetml.sheet"
    ),
    "json": "application/json; charset=utf-8",
    "evidence": "application/zip",
}


def load_attached_report_artifact(
    manifest: Mapping[str, Any],
    name: str,
    *,
    object_storage: Any | None = None,
) -> ReportArtifact:
    """Read one immutable artifact and verify it against the manifest."""

    artifact_name = str(name)
    if artifact_name not in _ARTIFACT_CONTENT_TYPES:
        raise ValueError(f"unsupported report artifact: {artifact_name}")
    object_keys = _as_dict(manifest.get("artifact_object_keys_json"))
    artifact_digests = _as_dict(manifest.get("artifact_sha256_json"))
    object_key = str(object_keys.get(artifact_name) or "").strip()
    expected_digest = str(
        artifact_digests.get(artifact_name) or ""
    ).strip().lower()
    if not object_key or not expected_digest:
        raise ValueError(
            f"report artifact is not attached: {artifact_name}"
        )
    if object_storage is None:
        from src.infrastructure.storage.minio_client import (
            get_minio_storage,
        )

        object_storage = get_minio_storage()
    content = object_storage.download_file(object_key)
    if content is None:
        raise RuntimeError(
            f"attached report artifact is unavailable: {artifact_name}"
        )
    actual_digest = hashlib.sha256(content).hexdigest()
    if actual_digest != expected_digest:
        raise RuntimeError(
            f"attached report artifact digest mismatch: {artifact_name}"
        )
    return ReportArtifact(
        name=artifact_name,
        filename=object_key.rsplit("/", 1)[-1],
        content_type=_ARTIFACT_CONTENT_TYPES[artifact_name],
        content=content,
    )


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()


def _iso(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _jsonable(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {
            str(key): _jsonable(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(item) for item in value]
    return value


def _as_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _as_rows(value: Any) -> list[dict[str, Any]]:
    return [
        dict(item)
        for item in value or []
        if isinstance(item, Mapping)
    ]


def _display_value(row: Mapping[str, Any]) -> Any:
    if row.get("value_number") is not None:
        return row.get("value_number")
    if row.get("value_text") not in (None, ""):
        return row.get("value_text")
    return row.get("value")


def _device_label(
    device_id: str,
    target_by_device: Mapping[str, Mapping[str, Any]],
) -> str:
    target = target_by_device.get(device_id, {})
    return str(target.get("device_name") or device_id or "-")


def _check_item_label(check_item: Mapping[str, Any]) -> str:
    name = str(check_item.get("name") or "").strip()
    check_item_id = str(check_item.get("check_item_id") or "").strip()
    if name and name != check_item_id:
        return f"{name} ({check_item_id})"
    return name or check_item_id or "-"


def validate_report_manifest(manifest: Mapping[str, Any]) -> None:
    """Reject mutable, incomplete or digest-inconsistent report inputs."""

    source_facts = _as_dict(manifest.get("source_facts_json"))
    if not source_facts:
        raise ValueError("report manifest has no frozen source facts")
    if source_facts.get("schema_version") != "patrol-report-source/v2":
        raise ValueError("unsupported patrol report source schema")
    expected_digest = str(manifest.get("source_digest") or "")
    if len(expected_digest) != 64 or _digest(source_facts) != expected_digest:
        raise ValueError("report manifest source digest is invalid")
    if (
        str(source_facts.get("patrol_batch_id") or "")
        != str(manifest.get("patrol_batch_id") or "")
    ):
        raise ValueError("report manifest batch identity mismatch")


def build_check_item_report_view(
    manifest: Mapping[str, Any],
) -> dict[str, Any]:
    """Build a human-oriented view without consulting mutable runtime tables."""

    validate_report_manifest(manifest)
    facts = _as_dict(manifest.get("source_facts_json"))
    readiness = _as_dict(facts.get("readiness"))
    targets = _as_rows(facts.get("target_facts"))
    executions = _as_rows(facts.get("executions"))
    assessments = _as_rows(facts.get("assessments"))
    check_items = {
        str(key): _as_dict(value)
        for key, value in _as_dict(facts.get("check_items")).items()
    }
    target_by_device = {
        str(item.get("device_id") or ""): item
        for item in targets
    }
    execution_by_id = {
        str(item.get("id") or ""): item
        for item in executions
    }
    assessment_by_execution = {
        str(item.get("execution_id") or ""): item
        for item in assessments
    }

    matrix: list[dict[str, Any]] = []
    for execution in executions:
        execution_id = str(execution.get("id") or "")
        device_id = str(execution.get("device_id") or "")
        version_id = str(
            execution.get("check_item_version_id") or ""
        )
        check_item = check_items.get(version_id, {})
        assessment = assessment_by_execution.get(execution_id, {})
        matrix.append(
            {
                "execution_id": execution_id,
                "device_id": device_id,
                "device_name": _device_label(
                    device_id,
                    target_by_device,
                ),
                "management_ip": target_by_device.get(
                    device_id,
                    {},
                ).get("management_ip"),
                "check_item_version_id": version_id,
                "check_item_id": check_item.get("check_item_id"),
                "check_item_name": check_item.get("name"),
                "check_item_label": _check_item_label(check_item),
                "category": check_item.get("category"),
                "result_mode": check_item.get("result_mode"),
                "assessment_mode": check_item.get("assessment_mode"),
                "report_projection": check_item.get(
                    "report_projection"
                ),
                "execution_status": execution.get("execution_status"),
                "assessment_status": assessment.get(
                    "assessment_status",
                    "unable_to_determine",
                ),
                "completeness_status": assessment.get(
                    "completeness_status",
                    "insufficient",
                ),
                "comparison_status": assessment.get(
                    "comparison_status",
                    "baseline_missing",
                ),
                "blocking_reason_codes": list(
                    assessment.get("blocking_reason_codes_json")
                    or execution.get("reason_codes_json")
                    or []
                ),
                "expected_entity_count": assessment.get(
                    "expected_entity_count"
                ),
                "observed_entity_count": assessment.get(
                    "observed_entity_count",
                    execution.get("observed_entity_count", 0),
                ),
                "evaluated_entity_count": assessment.get(
                    "evaluated_entity_count",
                    0,
                ),
                "threshold_unknown_count": assessment.get(
                    "threshold_unknown_count",
                    0,
                ),
                "observation_count": execution.get(
                    "observation_count",
                    0,
                ),
                "change_count": execution.get("change_count", 0),
                "event_count": execution.get("event_count", 0),
                "current_finding_count": execution.get(
                    "current_finding_count",
                    0,
                ),
                "evidence_qualification_id": assessment.get(
                    "evidence_qualification_id",
                    execution.get("evidence_qualification_id"),
                ),
                "coverage_decision_id": assessment.get(
                    "coverage_decision_id",
                    execution.get("coverage_decision_id"),
                ),
                "source_identity_status": assessment.get(
                    "source_identity_status",
                    execution.get("source_identity_status"),
                ),
                "output_quality_status": assessment.get(
                    "output_quality_status",
                    execution.get("output_quality_status"),
                ),
                "freshness_status": assessment.get(
                    "freshness_status",
                    execution.get("freshness_status"),
                ),
                "empty_result_classification": assessment.get(
                    "empty_result_classification",
                    execution.get("empty_result_classification"),
                ),
                "coverage_status": assessment.get(
                    "coverage_status",
                    execution.get("coverage_status"),
                ),
                "coverage_basis": assessment.get(
                    "coverage_basis",
                    execution.get("coverage_basis"),
                ),
                "risk_verification_status": assessment.get(
                    "risk_verification_status",
                    execution.get("risk_verification_status"),
                ),
                "evidence_digest": assessment.get("evidence_digest"),
                "runtime_bundle_digest": execution.get(
                    "runtime_bundle_digest"
                ),
            }
        )
    matrix.sort(
        key=lambda item: (
            str(item.get("device_name") or ""),
            str(item.get("check_item_id") or ""),
        )
    )

    health_assessments = [
        row for row in matrix if row.get("assessment_mode") == "health"
    ]
    status_counts = Counter(
        str(row.get("assessment_status") or "unable_to_determine")
        for row in matrix
    )
    completeness_counts = Counter(
        str(row.get("completeness_status") or "insufficient")
        for row in matrix
    )
    comparison_counts = Counter(
        str(row.get("comparison_status") or "baseline_missing")
        for row in matrix
    )
    blocker_counts = Counter(
        str(code)
        for row in matrix
        for code in row.get("blocking_reason_codes") or []
    )

    formal = (
        str(manifest.get("status") or "") == "final"
        and bool(readiness.get("ready_for_final"))
    )
    if not formal:
        conclusion_code = "not_final"
        conclusion = "正式巡检结论尚未形成"
        conclusion_basis = (
            "目标设备、检查项执行或评估尚未全部完成，"
            "当前结果仅供过程排错，不能作为正式健康结论。"
        )
        trust = "过程数据"
    elif (
        status_counts["at_risk"]
        and status_counts["unable_to_determine"]
    ):
        conclusion_code = "at_risk_with_unknown"
        conclusion = "本次巡检发现当前风险，且部分检查项无法判断"
        conclusion_basis = (
            f"已确认 {status_counts['at_risk']} 项当前风险；另有 "
            f"{status_counts['unable_to_determine']} 项检查因证据不足"
            "无法判断，已确认风险和未知范围均需跟进。"
        )
        trust = "受限结论"
    elif status_counts["at_risk"]:
        conclusion_code = "at_risk"
        conclusion = "本次巡检发现当前风险"
        conclusion_basis = (
            f"已完成 {len(matrix)} 项检查，其中 "
            f"{status_counts['at_risk']} 项形成可追溯风险结论。"
        )
        trust = "正式结论"
    elif status_counts["unable_to_determine"]:
        conclusion_code = "partially_unknown"
        conclusion = "部分检查项无法判断"
        conclusion_basis = (
            f"已有 {status_counts['unable_to_determine']} 项检查"
            "因采集、解析、规则、阈值或基线证据不足而无法判断；"
            "其余检查结果不得代表未覆盖范围。"
        )
        trust = "受限结论"
    elif (
        not health_assessments
        or not any(
            row.get("assessment_status") == "healthy"
            for row in health_assessments
        )
    ):
        conclusion_code = "informational_only"
        conclusion = "本次仅形成信息性结果，未判定网络健康"
        conclusion_basis = (
            "本次范围未包含可形成健康判定的检查项，"
            "因此不能使用“未发现异常”作为结论。"
        )
        trust = "信息性结果"
    elif all(
        row.get("assessment_status")
        in {"healthy", "not_applicable"}
        for row in health_assessments
    ):
        conclusion_code = "healthy"
        conclusion = "本次覆盖范围内未发现异常"
        conclusion_basis = (
            f"{len(health_assessments)} 项健康类检查均已形成完整评估，"
            "且未产生当前风险；该结论仅适用于本次目标范围和已发布规则版本。"
        )
        trust = "正式结论"
    else:
        conclusion_code = "mixed"
        conclusion = "本次检查结果需工程复核"
        conclusion_basis = "存在尚未纳入明确结论类别的评估状态。"
        trust = "受限结论"

    findings = _as_rows(facts.get("findings"))
    changes = _as_rows(facts.get("changes"))
    events = _as_rows(facts.get("events"))
    risks = _as_rows(facts.get("risks"))
    occurrences = _as_rows(facts.get("risk_occurrences"))
    remediations = _as_rows(facts.get("risk_remediations"))
    observations = _as_rows(facts.get("observations"))
    evidence_index = _as_rows(facts.get("evidence_index"))
    risk_by_id = {
        str(item.get("id") or ""): item
        for item in risks
    }
    current_execution_ids = {
        str(item.get("id") or "")
        for item in executions
        if item.get("id")
    }
    current_finding_ids = {
        str(item.get("id") or "")
        for item in findings
        if item.get("id")
    }
    current_event_ids = {
        str(item.get("id") or "")
        for item in events
        if item.get("id")
    }
    check_item_name_by_id = {
        str(item.get("check_item_id") or ""): _check_item_label(item)
        for item in check_items.values()
    }
    enriched_risks: list[dict[str, Any]] = []
    for risk in risks:
        row = dict(risk)
        device_id = str(row.get("device_id") or "")
        row["device_name"] = _device_label(
            device_id,
            target_by_device,
        )
        row["check_item_label"] = check_item_name_by_id.get(
            str(row.get("check_item_id") or ""),
            str(row.get("check_item_id") or "-"),
        )
        finding_refs = {
            str(item)
            for item in row.get("source_finding_ids_json") or []
            if item
        }
        event_refs = {
            str(item)
            for item in row.get("source_event_ids_json") or []
            if item
        }
        verification_execution_id = str(
            row.get("last_verification_execution_id") or ""
        )
        row["verified_by_current_batch"] = bool(
            verification_execution_id in current_execution_ids
            and (
                finding_refs.intersection(current_finding_ids)
                or event_refs.intersection(current_event_ids)
            )
        )
        enriched_risks.append(row)
    enriched_risks.sort(
        key=lambda item: (
            -_SEVERITY_ORDER.get(
                str(item.get("severity") or "unknown").lower(),
                0,
            ),
            str(item.get("device_name") or ""),
            str(item.get("entity_key") or ""),
        )
    )
    current_risks = [
        row
        for row in enriched_risks
        if str(row.get("verification_status") or "")
        == "confirmed_current"
        and bool(row.get("verified_by_current_batch"))
        and str(row.get("status") or "") not in {"resolved", "suppressed"}
    ]
    recovery_pending_risks = [
        row
        for row in enriched_risks
        if str(row.get("verification_status") or "")
        == "recovery_pending"
        and str(row.get("status") or "") != "resolved"
    ]
    resolved_risks = [
        row
        for row in enriched_risks
        if str(row.get("verification_status") or "") == "resolved"
        or str(row.get("status") or "") == "resolved"
    ]
    current_risk_ids = {
        str(row.get("id") or "") for row in current_risks
    }
    recovery_pending_ids = {
        str(row.get("id") or "") for row in recovery_pending_risks
    }
    resolved_risk_ids = {
        str(row.get("id") or "") for row in resolved_risks
    }
    historical_not_reverified_risks = [
        row
        for row in enriched_risks
        if str(row.get("id") or "") not in current_risk_ids
        and str(row.get("id") or "") not in recovery_pending_ids
        and str(row.get("id") or "") not in resolved_risk_ids
    ]
    risk_verification_counts = {
        "confirmed_current": len(current_risks),
        "recovery_pending": len(recovery_pending_risks),
        "not_reverified": len(historical_not_reverified_risks),
        "resolved": len(resolved_risks),
    }
    enriched_remediations: list[dict[str, Any]] = []
    for remediation in remediations:
        row = dict(remediation)
        risk = risk_by_id.get(str(row.get("risk_case_id") or ""), {})
        row["device_id"] = risk.get("device_id")
        row["device_name"] = _device_label(
            str(risk.get("device_id") or ""),
            target_by_device,
        )
        row["check_item_id"] = risk.get("check_item_id")
        row["risk_type"] = risk.get("risk_type")
        row["severity"] = risk.get("severity")
        enriched_remediations.append(row)

    comparison_contexts = _as_rows(
        facts.get("comparison_contexts")
    )
    comparison_by_id = {
        str(item.get("id") or ""): item
        for item in comparison_contexts
    }
    for row in matrix:
        execution = execution_by_id.get(
            str(row.get("execution_id") or ""),
            {},
        )
        context = comparison_by_id.get(
            str(execution.get("comparison_context_id") or ""),
            {},
        )
        row["baseline_run_id"] = context.get("baseline_run_id")
        row["baseline_snapshot_ids"] = list(
            context.get("baseline_snapshot_ids") or []
        )

    source_digest = str(manifest.get("source_digest") or "")
    report_view = {
        "schema_version": "check-item-patrol-report/v1",
        "report_manifest_id": str(manifest.get("id") or ""),
        "report_manifest_version": int(manifest.get("version") or 0),
        "report_status": str(manifest.get("status") or ""),
        "patrol_batch_id": str(manifest.get("patrol_batch_id") or ""),
        "generated_at": _iso(manifest.get("generated_at")),
        "report_cutoff_at": _iso(
            manifest.get("report_cutoff_at")
            or facts.get("report_cutoff_at")
        ),
        "generator_version": str(manifest.get("generator_version") or ""),
        "source_digest": source_digest,
        "qualification_fact_digest": str(
            manifest.get("qualification_fact_digest") or ""
        ),
        "coverage_fact_digest": str(
            manifest.get("coverage_fact_digest") or ""
        ),
        "target_manifest_digest": str(
            manifest.get("target_manifest_digest") or ""
        ),
        "runtime_manifest_digest": str(
            manifest.get("runtime_manifest_digest") or ""
        ),
        "conclusion": {
            "code": conclusion_code,
            "text": conclusion,
            "basis": conclusion_basis,
            "trust": trust,
            "formal": formal,
        },
        "coverage": {
            "target_device_count": int(
                readiness.get("target_count") or len(targets)
            ),
            "target_record_count": int(
                readiness.get("target_record_count") or len(targets)
            ),
            "expected_check_count": int(
                readiness.get("expected_execution_count") or len(matrix)
            ),
            "executed_check_count": len(executions),
            "assessed_check_count": len(assessments),
            "complete_check_count": completeness_counts["complete"],
            "partial_check_count": completeness_counts["partial"],
            "insufficient_check_count": completeness_counts[
                "insufficient"
            ],
        },
        "assessment_counts": dict(sorted(status_counts.items())),
        "completeness_counts": dict(
            sorted(completeness_counts.items())
        ),
        "comparison_counts": dict(sorted(comparison_counts.items())),
        "blocking_reason_counts": dict(sorted(blocker_counts.items())),
        "readiness": readiness,
        "targets": targets,
        "check_items": check_items,
        "matrix": matrix,
        # Keep the legacy key truthful: it now means risks confirmed by this
        # report's frozen current-batch evidence, not every open historical case.
        "risks": current_risks,
        "current_risks": current_risks,
        "historical_not_reverified_risks": (
            historical_not_reverified_risks
        ),
        "recovery_pending_risks": recovery_pending_risks,
        "resolved_risks": resolved_risks,
        "all_risk_cases": enriched_risks,
        "risk_verification_counts": risk_verification_counts,
        "risk_occurrences": occurrences,
        "remediations": enriched_remediations,
        "findings": findings,
        "changes": changes,
        "events": events,
        "observations": observations,
        "evidence_index": evidence_index,
    }
    return _jsonable(report_view)


def _markdown_text(value: Any) -> str:
    text = str(value if value not in (None, "") else "-")
    return text.replace("|", "\\|").replace("\r", " ").replace("\n", " ")


def _markdown_table(
    headers: Sequence[str],
    rows: Iterable[Sequence[Any]],
) -> list[str]:
    output = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    materialized = list(rows)
    if not materialized:
        materialized = [["-" for _ in headers]]
    output.extend(
        "| " + " | ".join(_markdown_text(value) for value in row) + " |"
        for row in materialized
    )
    return output


def render_check_item_markdown(report: Mapping[str, Any]) -> str:
    """Render the leadership report with explicit confidence limitations."""

    coverage = _as_dict(report.get("coverage"))
    conclusion = _as_dict(report.get("conclusion"))
    matrix = _as_rows(report.get("matrix"))
    risks = _as_rows(report.get("risks"))
    historical_risks = _as_rows(
        report.get("historical_not_reverified_risks")
    )
    recovery_pending_risks = _as_rows(
        report.get("recovery_pending_risks")
    )
    remediations = _as_rows(report.get("remediations"))
    changes = _as_rows(report.get("changes"))
    events = _as_rows(report.get("events"))
    blockers = _as_dict(report.get("blocking_reason_counts"))
    lines = [
        "# 网络设备巡检报告",
        "",
        f"- 巡检批次：`{report.get('patrol_batch_id')}`",
        (
            f"- 报告清单：`{report.get('report_manifest_id')}`"
            f"（版本 {report.get('report_manifest_version')}）"
        ),
        f"- 生成时间：{report.get('generated_at') or '-'}",
        f"- 数据指纹：`{report.get('source_digest')}`",
        "",
        "## 一、总体结论",
        "",
        f"**{conclusion.get('text')}。**",
        "",
        str(conclusion.get("basis") or ""),
        "",
        f"结论可信度：**{conclusion.get('trust')}**。",
        "",
        "## 二、巡检范围与完成情况",
        "",
        *_markdown_table(
            ("目标设备", "计划检查", "已执行", "已评估", "完整", "部分", "证据不足"),
            [
                (
                    coverage.get("target_device_count"),
                    coverage.get("expected_check_count"),
                    coverage.get("executed_check_count"),
                    coverage.get("assessed_check_count"),
                    coverage.get("complete_check_count"),
                    coverage.get("partial_check_count"),
                    coverage.get("insufficient_check_count"),
                )
            ],
        ),
        "",
        "## 三、当前风险",
        "",
        *_markdown_table(
            ("等级", "设备", "检查项", "风险", "对象", "状态", "整改状态"),
            [
                (
                    _SEVERITY_LABELS.get(
                        str(item.get("severity") or "unknown").lower(),
                        item.get("severity"),
                    ),
                    item.get("device_name"),
                    item.get("check_item_label"),
                    item.get("risk_type"),
                    item.get("entity_key"),
                    item.get("status"),
                    item.get("remediation_status"),
                )
                for item in risks
            ],
        ),
        "",
        "## 四、历史风险待复核",
        "",
        (
            "以下风险来自历史批次，但本批次没有形成足以确认其"
            "当前状态的合格证据；不得计入“本次确认风险”，也不得"
            "直接视为已恢复。"
        ),
        "",
        *_markdown_table(
            ("等级", "设备", "检查项", "风险", "对象", "历史状态"),
            [
                (
                    _SEVERITY_LABELS.get(
                        str(item.get("severity") or "unknown").lower(),
                        item.get("severity"),
                    ),
                    item.get("device_name"),
                    item.get("check_item_label"),
                    item.get("risk_type"),
                    item.get("entity_key"),
                    item.get("status"),
                )
                for item in historical_risks
            ],
        ),
        "",
        "## 五、恢复待确认",
        "",
        (
            "以下风险在本批次未再次出现，但仍需恢复规则或后续合格"
            "巡检证据完成闭环。"
        ),
        "",
        *_markdown_table(
            ("等级", "设备", "检查项", "风险", "对象", "当前状态"),
            [
                (
                    _SEVERITY_LABELS.get(
                        str(item.get("severity") or "unknown").lower(),
                        item.get("severity"),
                    ),
                    item.get("device_name"),
                    item.get("check_item_label"),
                    item.get("risk_type"),
                    item.get("entity_key"),
                    item.get("status"),
                )
                for item in recovery_pending_risks
            ],
        ),
        "",
        "## 六、检查项结论",
        "",
        *_markdown_table(
            (
                "设备",
                "检查项",
                "执行",
                "结论",
                "完整性",
                "证据资质",
                "覆盖",
                "基线",
                "阻断原因",
            ),
            [
                (
                    item.get("device_name"),
                    item.get("check_item_label"),
                    item.get("execution_status"),
                    _STATUS_LABELS.get(
                        str(item.get("assessment_status")),
                        item.get("assessment_status"),
                    ),
                    _COMPLETENESS_LABELS.get(
                        str(item.get("completeness_status")),
                        item.get("completeness_status"),
                    ),
                    (
                        f"{item.get('source_identity_status') or '-'} / "
                        f"{item.get('output_quality_status') or '-'} / "
                        f"{item.get('freshness_status') or '-'}"
                    ),
                    (
                        f"{item.get('coverage_status') or '-'}"
                        f" ({item.get('coverage_basis') or '-'})"
                    ),
                    _COMPARISON_LABELS.get(
                        str(item.get("comparison_status")),
                        item.get("comparison_status"),
                    ),
                    ", ".join(
                        str(code)
                        for code in item.get("blocking_reason_codes") or []
                    )
                    or "-",
                )
                for item in matrix
            ],
        ),
        "",
        "## 七、变化与事件",
        "",
        f"- 标准化变化：{len(changes)} 条。",
        f"- 网络事件：{len(events)} 条。",
        "- 未产生事件只表示没有命中已发布事件规则，不能单独证明设备健康。",
        "",
        "## 八、整改跟踪",
        "",
        *_markdown_table(
            ("设备", "风险", "整改状态", "责任人", "期限", "整改动作"),
            [
                (
                    item.get("device_name"),
                    item.get("risk_type"),
                    item.get("status"),
                    item.get("owner"),
                    item.get("due_at"),
                    item.get("action_text"),
                )
                for item in remediations
            ],
        ),
        "",
        "## 九、数据完整性与限制",
        "",
    ]
    if blockers:
        lines.extend(
            f"- `{code}`：{count} 项。"
            for code, count in sorted(blockers.items())
        )
    else:
        lines.append("- 未发现影响本次正式结论的数据阻断项。")
    lines.extend(
        [
            "",
            "## 十、证据与复核说明",
            "",
            f"- 目标清单指纹：`{report.get('target_manifest_digest')}`",
            f"- 运行资产清单指纹：`{report.get('runtime_manifest_digest')}`",
            f"- 报告事实指纹：`{report.get('source_digest')}`",
            f"- 证据资质事实指纹：`{report.get('qualification_fact_digest') or '-'}`",
            f"- 覆盖决策事实指纹：`{report.get('coverage_fact_digest') or '-'}`",
            f"- 事实截止时间：{report.get('report_cutoff_at') or '-'}",
            f"- 证据索引：{len(_as_rows(report.get('evidence_index')))} 条命令快照。",
            "- 每项结论均可由设备、检查项、命令快照、Parser/Contract 版本和规则资产摘要复核。",
            "- 原始 CLI 不嵌入本报告；原始输出通过独立、受权限控制的交付物提供。",
            "",
        ]
    )
    return "\n".join(lines)


def _normalize_zip_archive(content: bytes) -> bytes:
    source_stream = io.BytesIO(content)
    output_stream = io.BytesIO()
    with zipfile.ZipFile(source_stream, "r") as source, zipfile.ZipFile(
        output_stream,
        "w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
    ) as target:
        for source_info in sorted(
            source.infolist(),
            key=lambda item: item.filename,
        ):
            entry_content = source.read(source_info.filename)
            if source_info.filename == "docProps/core.xml":
                created_match = re.search(
                    rb"(<dcterms:created\b[^>]*>)([^<]*)(</dcterms:created>)",
                    entry_content,
                )
                if created_match is not None:
                    created_value = created_match.group(2)
                    entry_content = re.sub(
                        rb"(<dcterms:modified\b[^>]*>)[^<]*(</dcterms:modified>)",
                        lambda match: (
                            match.group(1)
                            + created_value
                            + match.group(2)
                        ),
                        entry_content,
                    )
            target_info = zipfile.ZipInfo(
                filename=source_info.filename,
                date_time=_ARCHIVE_TIMESTAMP,
            )
            target_info.compress_type = zipfile.ZIP_DEFLATED
            target_info.create_system = source_info.create_system
            target_info.external_attr = source_info.external_attr
            target_info.internal_attr = source_info.internal_attr
            target_info.comment = source_info.comment
            target.writestr(
                target_info,
                entry_content,
            )
    return output_stream.getvalue()


def _report_datetime(value: Any) -> datetime:
    try:
        parsed = datetime.fromisoformat(
            str(value or "").replace("Z", "+00:00")
        )
    except ValueError:
        parsed = datetime(2000, 1, 1, tzinfo=timezone.utc)
    if parsed.tzinfo is not None:
        return parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def render_check_item_docx(report: Mapping[str, Any]) -> bytes:
    """Render a concise leadership document from the same report view."""

    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml.ns import qn
    from docx.shared import Cm, Pt

    doc = Document()
    section = doc.sections[0]
    section.top_margin = Cm(2.4)
    section.bottom_margin = Cm(2.2)
    section.left_margin = Cm(2.6)
    section.right_margin = Cm(2.6)
    generated_at = _report_datetime(report.get("generated_at"))
    doc.core_properties.created = generated_at
    doc.core_properties.modified = generated_at
    doc.core_properties.author = "NetOps Agent"
    doc.core_properties.title = "网络设备巡检报告"

    styles = doc.styles
    styles["Normal"].font.name = "宋体"
    styles["Normal"].font.size = Pt(10.5)
    styles["Normal"]._element.rPr.rFonts.set(qn("w:eastAsia"), "宋体")

    title = doc.add_paragraph()
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    title.paragraph_format.space_after = Pt(16)
    title_run = title.add_run("网络设备巡检报告")
    title_run.bold = True
    title_run.font.name = "方正小标宋简体"
    title_run.font.size = Pt(22)
    title_run._element.rPr.rFonts.set(
        qn("w:eastAsia"),
        "方正小标宋简体",
    )

    def heading(text: str) -> None:
        paragraph = doc.add_paragraph()
        paragraph.paragraph_format.space_before = Pt(8)
        paragraph.paragraph_format.space_after = Pt(4)
        run = paragraph.add_run(text)
        run.bold = True
        run.font.name = "黑体"
        run.font.size = Pt(14)
        run._element.rPr.rFonts.set(qn("w:eastAsia"), "黑体")

    def paragraph(text: str, *, bold: bool = False) -> None:
        item = doc.add_paragraph()
        item.paragraph_format.first_line_indent = Cm(0.74)
        item.paragraph_format.line_spacing = 1.5
        run = item.add_run(text)
        run.bold = bold

    def table(headers: Sequence[str], rows: Iterable[Sequence[Any]]) -> None:
        materialized = list(rows)
        grid = doc.add_table(rows=1, cols=len(headers))
        grid.style = "Table Grid"
        for index, header in enumerate(headers):
            cell = grid.rows[0].cells[index]
            cell.text = str(header)
            for run in cell.paragraphs[0].runs:
                run.bold = True
                run.font.name = "黑体"
                run._element.rPr.rFonts.set(qn("w:eastAsia"), "黑体")
        if not materialized:
            materialized = [["-" for _ in headers]]
        for row in materialized:
            cells = grid.add_row().cells
            for index, value in enumerate(row):
                cells[index].text = str(
                    value if value not in (None, "") else "-"
                )
        doc.add_paragraph()

    table(
        ("巡检批次", "目标设备", "计划检查", "报告状态"),
        [
            (
                report.get("patrol_batch_id"),
                _as_dict(report.get("coverage")).get(
                    "target_device_count"
                ),
                _as_dict(report.get("coverage")).get(
                    "expected_check_count"
                ),
                report.get("report_status"),
            )
        ],
    )
    conclusion = _as_dict(report.get("conclusion"))
    heading("一、总体结论")
    paragraph(
        f"{conclusion.get('text')}。{conclusion.get('basis')}",
        bold=True,
    )
    paragraph(f"结论可信度：{conclusion.get('trust')}。")

    heading("二、巡检范围与完成情况")
    coverage = _as_dict(report.get("coverage"))
    table(
        ("目标设备", "计划检查", "已执行", "已评估", "完整", "部分", "证据不足"),
        [
            (
                coverage.get("target_device_count"),
                coverage.get("expected_check_count"),
                coverage.get("executed_check_count"),
                coverage.get("assessed_check_count"),
                coverage.get("complete_check_count"),
                coverage.get("partial_check_count"),
                coverage.get("insufficient_check_count"),
            )
        ],
    )

    heading("三、当前风险")
    table(
        ("等级", "设备", "检查项", "风险", "对象", "状态"),
        [
            (
                _SEVERITY_LABELS.get(
                    str(item.get("severity") or "unknown").lower(),
                    item.get("severity"),
                ),
                item.get("device_name"),
                item.get("check_item_label"),
                item.get("risk_type"),
                item.get("entity_key"),
                item.get("status"),
            )
            for item in _as_rows(report.get("risks"))
        ],
    )

    heading("四、历史风险待复核")
    paragraph(
        "以下风险来自历史批次，本批次未形成足以确认其当前状态的"
        "合格证据，不计入本次确认风险。"
    )
    table(
        ("等级", "设备", "检查项", "风险", "对象", "历史状态"),
        [
            (
                _SEVERITY_LABELS.get(
                    str(item.get("severity") or "unknown").lower(),
                    item.get("severity"),
                ),
                item.get("device_name"),
                item.get("check_item_label"),
                item.get("risk_type"),
                item.get("entity_key"),
                item.get("status"),
            )
            for item in _as_rows(
                report.get("historical_not_reverified_risks")
            )
        ],
    )

    heading("五、恢复待确认")
    paragraph(
        "以下风险在本批次未再次出现，但尚未满足恢复闭环条件。"
    )
    table(
        ("等级", "设备", "检查项", "风险", "对象", "状态"),
        [
            (
                _SEVERITY_LABELS.get(
                    str(item.get("severity") or "unknown").lower(),
                    item.get("severity"),
                ),
                item.get("device_name"),
                item.get("check_item_label"),
                item.get("risk_type"),
                item.get("entity_key"),
                item.get("status"),
            )
            for item in _as_rows(report.get("recovery_pending_risks"))
        ],
    )

    doc.add_page_break()
    heading("六、检查项结论")
    table(
        ("设备", "检查项", "执行", "结论", "完整性", "证据", "覆盖"),
        [
            (
                item.get("device_name"),
                item.get("check_item_label"),
                item.get("execution_status"),
                _STATUS_LABELS.get(
                    str(item.get("assessment_status")),
                    item.get("assessment_status"),
                ),
                _COMPLETENESS_LABELS.get(
                    str(item.get("completeness_status")),
                    item.get("completeness_status"),
                ),
                (
                    f"{item.get('source_identity_status') or '-'} / "
                    f"{item.get('output_quality_status') or '-'} / "
                    f"{item.get('freshness_status') or '-'}"
                ),
                (
                    f"{item.get('coverage_status') or '-'}"
                    f" ({item.get('coverage_basis') or '-'})"
                ),
            )
            for item in _as_rows(report.get("matrix"))
        ],
    )

    heading("七、整改跟踪")
    table(
        ("设备", "风险", "状态", "责任人", "期限", "整改动作"),
        [
            (
                item.get("device_name"),
                item.get("risk_type"),
                item.get("status"),
                item.get("owner"),
                item.get("due_at"),
                item.get("action_text"),
            )
            for item in _as_rows(report.get("remediations"))
        ],
    )

    heading("八、证据与复核说明")
    paragraph(
        f"报告事实指纹：{report.get('source_digest')}。"
        f"目标清单指纹：{report.get('target_manifest_digest')}。"
        f"运行资产清单指纹：{report.get('runtime_manifest_digest')}。"
        f"证据资质事实指纹：{report.get('qualification_fact_digest') or '-'}。"
        f"覆盖决策事实指纹：{report.get('coverage_fact_digest') or '-'}。"
    )
    paragraph(
        "本报告不以“流程完成”或“事件为零”代替健康判断。"
        "数据不完整时明确标记无法判断；原始 CLI 通过独立、"
        "受权限控制的原始输出交付物提供。"
    )
    stream = io.BytesIO()
    doc.save(stream)
    return _normalize_zip_archive(stream.getvalue())


def _safe_excel_value(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, (dict, list, tuple, set)):
        text = json.dumps(
            _jsonable(value),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    else:
        text = str(value)
    if len(text) > 32_767:
        text = text[:32_764] + "..."
    if text.startswith(("=", "+", "-", "@")):
        text = "'" + text
    return text


def _append_sheet(
    workbook: Any,
    *,
    name: str,
    headers: Sequence[str],
    rows: Iterable[Sequence[Any]],
) -> None:
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    sheet = workbook.create_sheet(name)
    sheet.append(list(headers))
    materialized = list(rows)
    for row in materialized:
        sheet.append([_safe_excel_value(value) for value in row])
    header_fill = PatternFill("solid", fgColor="365F4B")
    for cell in sheet[1]:
        cell.fill = header_fill
        cell.font = Font(color="FFFFFF", bold=True)
        cell.alignment = Alignment(
            horizontal="center",
            vertical="center",
            wrap_text=True,
        )
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = (
        f"A1:{get_column_letter(len(headers))}"
        f"{max(sheet.max_row, 1)}"
    )
    for row in sheet.iter_rows(min_row=2):
        for cell in row:
            cell.alignment = Alignment(
                vertical="top",
                wrap_text=True,
            )
    for index, header in enumerate(headers, start=1):
        max_length = max(
            [len(str(header))]
            + [
                len(str(sheet.cell(row, index).value or ""))
                for row in range(2, min(sheet.max_row, 200) + 1)
            ]
        )
        sheet.column_dimensions[get_column_letter(index)].width = min(
            max(max_length + 2, 12),
            42,
        )


def render_check_item_xlsx(report: Mapping[str, Any]) -> bytes:
    """Render a scalable technical annex from the immutable report view."""

    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill

    workbook = Workbook()
    overview = workbook.active
    overview.title = "总览"
    generated_at = _report_datetime(report.get("generated_at"))
    workbook.properties.creator = "NetOps Agent"
    workbook.properties.created = generated_at
    workbook.properties.modified = generated_at
    overview["A1"] = "网络设备巡检技术附表"
    overview["A1"].font = Font(
        color="FFFFFF",
        bold=True,
        size=16,
    )
    overview["A1"].fill = PatternFill("solid", fgColor="263238")
    overview["B1"] = report.get("patrol_batch_id")
    overview["B1"].font = Font(color="FFFFFF")
    overview["B1"].fill = PatternFill("solid", fgColor="263238")
    metrics = [
        ("总体结论", _as_dict(report.get("conclusion")).get("text")),
        ("结论可信度", _as_dict(report.get("conclusion")).get("trust")),
        ("目标设备", _as_dict(report.get("coverage")).get("target_device_count")),
        ("计划检查", _as_dict(report.get("coverage")).get("expected_check_count")),
        ("已评估", _as_dict(report.get("coverage")).get("assessed_check_count")),
        ("当前风险", len(_as_rows(report.get("risks")))),
        ("变化", len(_as_rows(report.get("changes")))),
        ("事件", len(_as_rows(report.get("events")))),
        ("数据指纹", report.get("source_digest")),
        ("目标清单指纹", report.get("target_manifest_digest")),
        ("运行资产清单指纹", report.get("runtime_manifest_digest")),
        (
            "历史风险待复核",
            len(_as_rows(report.get("historical_not_reverified_risks"))),
        ),
        (
            "恢复待确认",
            len(_as_rows(report.get("recovery_pending_risks"))),
        ),
    ]
    for row_index, (label, value) in enumerate(metrics, start=3):
        overview.cell(row_index, 1, label)
        overview.cell(row_index, 2, _safe_excel_value(value))
        overview.cell(row_index, 1).font = Font(bold=True)
    overview.column_dimensions["A"].width = 24
    overview.column_dimensions["B"].width = 70
    overview.freeze_panes = "A3"

    _append_sheet(
        workbook,
        name="检查项结论",
        headers=(
            "设备ID",
            "设备名称",
            "管理IP",
            "检查项ID",
            "检查项名称",
            "执行状态",
            "评估结论",
            "完整性",
            "可比性",
            "身份",
            "输出质量",
            "时效性",
            "空结果分类",
            "覆盖状态",
            "覆盖基准",
            "风险验证",
            "观测实体数",
            "评估实体数",
            "阻断原因",
            "证据摘要",
        ),
        rows=(
            (
                item.get("device_id"),
                item.get("device_name"),
                item.get("management_ip"),
                item.get("check_item_id"),
                item.get("check_item_name"),
                item.get("execution_status"),
                item.get("assessment_status"),
                item.get("completeness_status"),
                item.get("comparison_status"),
                item.get("source_identity_status"),
                item.get("output_quality_status"),
                item.get("freshness_status"),
                item.get("empty_result_classification"),
                item.get("coverage_status"),
                item.get("coverage_basis"),
                item.get("risk_verification_status"),
                item.get("observed_entity_count"),
                item.get("evaluated_entity_count"),
                item.get("blocking_reason_codes"),
                item.get("evidence_digest"),
            )
            for item in _as_rows(report.get("matrix"))
        ),
    )
    _append_sheet(
        workbook,
        name="当前风险",
        headers=(
            "等级",
            "设备ID",
            "设备名称",
            "检查项",
            "风险类型",
            "实体类型",
            "实体",
            "状态",
            "首次发现",
            "最近发现",
            "整改状态",
            "责任人",
            "期限",
            "证据",
        ),
        rows=(
            (
                item.get("severity"),
                item.get("device_id"),
                item.get("device_name"),
                item.get("check_item_label"),
                item.get("risk_type"),
                item.get("entity_type"),
                item.get("entity_key"),
                item.get("status"),
                item.get("first_seen_at"),
                item.get("last_seen_at"),
                item.get("remediation_status"),
                item.get("owner"),
                item.get("due_at"),
                item.get("source_finding_ids_json")
                or item.get("source_event_ids_json"),
            )
            for item in _as_rows(report.get("risks"))
        ),
    )
    _append_sheet(
        workbook,
        name="历史风险待复核",
        headers=(
            "等级",
            "设备ID",
            "设备名称",
            "检查项",
            "风险类型",
            "实体类型",
            "实体",
            "历史状态",
            "首次发现",
            "最近发现",
            "最近验证",
            "整改状态",
        ),
        rows=(
            (
                item.get("severity"),
                item.get("device_id"),
                item.get("device_name"),
                item.get("check_item_label"),
                item.get("risk_type"),
                item.get("entity_type"),
                item.get("entity_key"),
                item.get("status"),
                item.get("first_seen_at"),
                item.get("last_seen_at"),
                item.get("last_verified_at"),
                item.get("remediation_status"),
            )
            for item in _as_rows(
                report.get("historical_not_reverified_risks")
            )
        ),
    )
    _append_sheet(
        workbook,
        name="恢复待确认",
        headers=(
            "等级",
            "设备ID",
            "设备名称",
            "检查项",
            "风险类型",
            "实体类型",
            "实体",
            "状态",
            "最近发现",
            "最近验证",
            "整改状态",
        ),
        rows=(
            (
                item.get("severity"),
                item.get("device_id"),
                item.get("device_name"),
                item.get("check_item_label"),
                item.get("risk_type"),
                item.get("entity_type"),
                item.get("entity_key"),
                item.get("status"),
                item.get("last_seen_at"),
                item.get("last_verified_at"),
                item.get("remediation_status"),
            )
            for item in _as_rows(report.get("recovery_pending_risks"))
        ),
    )
    _append_sheet(
        workbook,
        name="整改跟踪",
        headers=(
            "设备ID",
            "设备名称",
            "风险类型",
            "等级",
            "整改状态",
            "责任人",
            "期限",
            "整改动作",
            "创建人",
            "完成人",
            "完成时间",
            "证据",
        ),
        rows=(
            (
                item.get("device_id"),
                item.get("device_name"),
                item.get("risk_type"),
                item.get("severity"),
                item.get("status"),
                item.get("owner"),
                item.get("due_at"),
                item.get("action_text"),
                item.get("created_by"),
                item.get("completed_by"),
                item.get("completed_at"),
                item.get("evidence_refs_json"),
            )
            for item in _as_rows(report.get("remediations"))
        ),
    )
    _append_sheet(
        workbook,
        name="事件",
        headers=(
            "事件ID",
            "执行ID",
            "时间",
            "等级",
            "事件类型",
            "状态",
            "实体类型",
            "实体",
        ),
        rows=(
            (
                item.get("id"),
                item.get("execution_id"),
                item.get("occurred_at"),
                item.get("severity"),
                item.get("event_type"),
                item.get("status"),
                item.get("entity_type"),
                item.get("entity_key"),
            )
            for item in _as_rows(report.get("events"))
        ),
    )
    _append_sheet(
        workbook,
        name="变化",
        headers=(
            "变化ID",
            "执行ID",
            "时间",
            "标准观测类型",
            "变化类型",
            "实体类型",
            "实体",
            "字段",
            "原值",
            "现值",
        ),
        rows=(
            (
                item.get("id"),
                item.get("execution_id"),
                item.get("observed_at"),
                item.get("observation_type"),
                item.get("change_type"),
                item.get("entity_type"),
                item.get("entity_key"),
                item.get("field"),
                item.get("old"),
                item.get("new"),
            )
            for item in _as_rows(report.get("changes"))
        ),
    )
    _append_sheet(
        workbook,
        name="当前观测",
        headers=(
            "观测ID",
            "执行ID",
            "设备ID",
            "时间",
            "实体类型",
            "实体",
            "标准观测类型",
            "字段",
            "值",
            "单位",
            "维度",
            "Parser模板",
            "Parser版本",
            "Contract版本",
            "快照ID",
        ),
        rows=(
            (
                item.get("id"),
                item.get("execution_ids"),
                item.get("device_id"),
                item.get("observed_at"),
                item.get("entity_type"),
                item.get("entity_key"),
                item.get("observation_type"),
                item.get("field"),
                _display_value(item),
                item.get("unit"),
                item.get("dimensions"),
                item.get("parser_template_id"),
                item.get("parser_version"),
                item.get("contract_version"),
                item.get("snapshot_id"),
            )
            for item in _as_rows(report.get("observations"))
        ),
    )
    _append_sheet(
        workbook,
        name="证据索引",
        headers=(
            "快照ID",
            "运行ID",
            "设备ID",
            "设备名称",
            "管理IP",
            "厂商",
            "型号",
            "时间",
            "命令",
            "原文SHA256",
            "输出分类",
            "解析状态",
            "Parser模板",
            "Parser版本",
            "Contract版本",
            "采集动作",
        ),
        rows=(
            (
                item.get("snapshot_id"),
                item.get("run_id"),
                item.get("device_id"),
                item.get("device_name"),
                item.get("management_ip"),
                item.get("vendor"),
                item.get("model"),
                item.get("observed_at"),
                item.get("canonical_command"),
                item.get("raw_text_hash"),
                item.get("output_status"),
                item.get("parse_status"),
                item.get("parser_template_id"),
                item.get("parser_version"),
                item.get("contract_version"),
                item.get("collection_action_id"),
            )
            for item in _as_rows(report.get("evidence_index"))
        ),
    )
    stream = io.BytesIO()
    workbook.save(stream)
    return _normalize_zip_archive(stream.getvalue())


def _csv_bytes(
    headers: Sequence[str],
    rows: Iterable[Sequence[Any]],
) -> bytes:
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, lineterminator="\n")
    writer.writerow(headers)
    for row in rows:
        writer.writerow(
            [
                _safe_excel_value(value)
                for value in row
            ]
        )
    return ("\ufeff" + stream.getvalue()).encode("utf-8")


def render_check_item_evidence_zip(
    manifest: Mapping[str, Any],
    report: Mapping[str, Any],
) -> bytes:
    """Package the evidence index without embedding protected raw CLI."""

    validate_report_manifest(manifest)
    files: dict[str, bytes] = {
        "README.txt": (
            "网络设备巡检证据包\n"
            f"巡检批次: {report.get('patrol_batch_id')}\n"
            f"报告清单: {report.get('report_manifest_id')}\n"
            f"事实指纹: {report.get('source_digest')}\n\n"
            "本证据包包含冻结事实、检查项结论和命令快照索引，"
            "不包含原始 CLI 正文。原始输出需通过受权限控制的"
            "“原始输出”交付物获取，并以 raw_text_hash 校验一致性。\n"
        ).encode(),
        "report-manifest.json": json.dumps(
            _jsonable(dict(manifest)),
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        ).encode("utf-8"),
        "check-assessments.csv": _csv_bytes(
            (
                "device_id",
                "device_name",
                "check_item_id",
                "execution_status",
                "assessment_status",
                "completeness_status",
                "comparison_status",
                "blocking_reason_codes",
                "evidence_digest",
            ),
            (
                (
                    item.get("device_id"),
                    item.get("device_name"),
                    item.get("check_item_id"),
                    item.get("execution_status"),
                    item.get("assessment_status"),
                    item.get("completeness_status"),
                    item.get("comparison_status"),
                    item.get("blocking_reason_codes"),
                    item.get("evidence_digest"),
                )
                for item in _as_rows(report.get("matrix"))
            ),
        ),
        "evidence-index.csv": _csv_bytes(
            (
                "snapshot_id",
                "run_id",
                "device_id",
                "device_name",
                "observed_at",
                "canonical_command",
                "raw_text_hash",
                "parse_status",
                "output_status",
                "parser_template_id",
                "parser_version",
                "contract_version",
            ),
            (
                (
                    item.get("snapshot_id"),
                    item.get("run_id"),
                    item.get("device_id"),
                    item.get("device_name"),
                    item.get("observed_at"),
                    item.get("canonical_command"),
                    item.get("raw_text_hash"),
                    item.get("parse_status"),
                    item.get("output_status"),
                    item.get("parser_template_id"),
                    item.get("parser_version"),
                    item.get("contract_version"),
                )
                for item in _as_rows(report.get("evidence_index"))
            ),
        ),
        "risk-lifecycle.csv": _csv_bytes(
            (
                "risk_case_id",
                "occurrence_kind",
                "observed_at",
                "status_after",
                "actor",
                "reason",
                "evidence_refs",
            ),
            (
                (
                    item.get("risk_case_id"),
                    item.get("occurrence_kind"),
                    item.get("observed_at"),
                    item.get("status_after"),
                    item.get("actor"),
                    item.get("reason"),
                    item.get("evidence_refs_json"),
                )
                for item in _as_rows(report.get("risk_occurrences"))
            ),
        ),
    }
    output = io.BytesIO()
    with zipfile.ZipFile(
        output,
        "w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
    ) as archive:
        for name, content in sorted(files.items()):
            info = zipfile.ZipInfo(name, _ARCHIVE_TIMESTAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, content)
    return output.getvalue()


def render_check_item_json(report: Mapping[str, Any]) -> bytes:
    return json.dumps(
        _jsonable(dict(report)),
        ensure_ascii=False,
        sort_keys=True,
        indent=2,
    ).encode("utf-8")


def render_check_item_artifacts(
    manifest: Mapping[str, Any],
) -> dict[str, ReportArtifact]:
    """Render all final deliverables from one source-fact manifest."""

    report = build_check_item_report_view(manifest)
    batch_id = str(report.get("patrol_batch_id") or "patrol")
    markdown = render_check_item_markdown(report).encode("utf-8")
    artifacts = {
        "markdown": ReportArtifact(
            name="markdown",
            filename=f"{batch_id}-report.md",
            content_type="text/markdown; charset=utf-8",
            content=markdown,
        ),
        "docx": ReportArtifact(
            name="docx",
            filename=f"{batch_id}-leadership-report.docx",
            content_type=(
                "application/vnd.openxmlformats-officedocument."
                "wordprocessingml.document"
            ),
            content=render_check_item_docx(report),
        ),
        "excel": ReportArtifact(
            name="excel",
            filename=f"{batch_id}-technical-report.xlsx",
            content_type=(
                "application/vnd.openxmlformats-officedocument."
                "spreadsheetml.sheet"
            ),
            content=render_check_item_xlsx(report),
        ),
        "json": ReportArtifact(
            name="json",
            filename=f"{batch_id}-report.json",
            content_type="application/json; charset=utf-8",
            content=render_check_item_json(report),
        ),
        "evidence": ReportArtifact(
            name="evidence",
            filename=f"{batch_id}-evidence.zip",
            content_type="application/zip",
            content=render_check_item_evidence_zip(manifest, report),
        ),
    }
    return artifacts


def render_check_item_artifact(
    manifest: Mapping[str, Any],
    name: str,
) -> ReportArtifact:
    """Render one requested format without producing unrelated artifacts."""

    report = build_check_item_report_view(manifest)
    batch_id = str(report.get("patrol_batch_id") or "patrol")
    factories = {
        "markdown": lambda: ReportArtifact(
            name="markdown",
            filename=f"{batch_id}-report.md",
            content_type="text/markdown; charset=utf-8",
            content=render_check_item_markdown(report).encode("utf-8"),
        ),
        "docx": lambda: ReportArtifact(
            name="docx",
            filename=f"{batch_id}-leadership-report.docx",
            content_type=(
                "application/vnd.openxmlformats-officedocument."
                "wordprocessingml.document"
            ),
            content=render_check_item_docx(report),
        ),
        "excel": lambda: ReportArtifact(
            name="excel",
            filename=f"{batch_id}-technical-report.xlsx",
            content_type=(
                "application/vnd.openxmlformats-officedocument."
                "spreadsheetml.sheet"
            ),
            content=render_check_item_xlsx(report),
        ),
        "json": lambda: ReportArtifact(
            name="json",
            filename=f"{batch_id}-report.json",
            content_type="application/json; charset=utf-8",
            content=render_check_item_json(report),
        ),
        "evidence": lambda: ReportArtifact(
            name="evidence",
            filename=f"{batch_id}-evidence.zip",
            content_type="application/zip",
            content=render_check_item_evidence_zip(manifest, report),
        ),
    }
    try:
        return factories[str(name)]()
    except KeyError as exc:
        raise ValueError(f"unsupported report artifact: {name}") from exc


__all__ = [
    "CheckItemReportMaterializer",
    "ReportArtifact",
    "build_check_item_report_view",
    "load_attached_report_artifact",
    "render_check_item_artifacts",
    "render_check_item_artifact",
    "render_check_item_docx",
    "render_check_item_evidence_zip",
    "render_check_item_json",
    "render_check_item_markdown",
    "render_check_item_xlsx",
    "validate_report_manifest",
]
