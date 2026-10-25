"""Versioned, vendor-neutral projections for report technical annexes."""

from __future__ import annotations

import hashlib
import itertools
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from src.core.check_items.canonical import load_canonical_catalog, rule_semantics_catalog

DEFAULT_PROJECTION_CATALOG_PATH = Path(__file__).parent / "assets" / "report_projections.v1.yaml"
DEFAULT_PROJECTION_PRESENTATION_PATH = (
    Path(__file__).parent / "assets" / "report_projection_presentation.zh-CN.v1.yaml"
)

_SOURCE_KINDS = {"canonical_observation", "normalized_change"}
_NULL_POLICIES = {"preserve_null"}
_STATUS_FIELDS = {
    "execution_status",
    "assessment_status",
    "completeness_status",
    "comparison_status",
    "threshold_unknown_count",
    "risk_verification_status",
    "blocking_reason_codes",
}
_EVIDENCE_FIELDS = {
    "execution_id",
    "snapshot_id",
    "previous_snapshot_id",
    "current_snapshot_id",
    "parser_template_id",
    "runtime_bundle_digest",
    "observed_at",
}
_CHANGE_SOURCES = {
    "change.canonical_command",
    "change.change_type",
    "change.field",
    "change.old",
    "change.new",
    "change.decision_reason",
    "change.observed_at",
}
_BASE_COLUMN_IDS = (
    "device_name",
    "management_ip",
    "entity_type",
    "entity_key",
)
_AUTO_PROJECTION_REF = "auto@1"


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


def _mapping(value: Any, *, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object")
    return {str(key): item for key, item in value.items()}


@dataclass(frozen=True, slots=True)
class ProjectionColumn:
    column_id: str
    source: str
    dimensions: tuple[tuple[str, str], ...] = ()
    unit: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "column_id": self.column_id,
            "source": self.source,
            "dimensions": dict(self.dimensions),
            "unit": self.unit,
        }


@dataclass(frozen=True, slots=True)
class ReportProjectionProfile:
    profile_id: str
    version: int
    check_item_id: str
    source_kind: str
    row_grain: str
    canonical_columns: tuple[ProjectionColumn, ...]
    status_columns: tuple[str, ...]
    evidence_columns: tuple[str, ...]
    sheet_id: str
    sort: tuple[str, ...]
    null_policy: str
    digest: str

    @property
    def ref(self) -> str:
        return f"{self.profile_id}@{self.version}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "profile_id": self.profile_id,
            "version": self.version,
            "check_item_id": self.check_item_id,
            "source_kind": self.source_kind,
            "row_grain": self.row_grain,
            "canonical_columns": [item.to_dict() for item in self.canonical_columns],
            "status_columns": list(self.status_columns),
            "evidence_columns": list(self.evidence_columns),
            "sheet_id": self.sheet_id,
            "sort": list(self.sort),
            "null_policy": self.null_policy,
            "digest": self.digest,
        }


@dataclass(frozen=True, slots=True)
class ReportProjectionCatalog:
    catalog_id: str
    version: int
    digest: str
    presentation_id: str
    presentation_version: int
    presentation_digest: str
    profiles: tuple[ReportProjectionProfile, ...]
    sheet_labels: dict[str, str]
    column_labels: dict[str, str]

    def get(self, profile_ref: str) -> ReportProjectionProfile | None:
        normalized = normalize_projection_ref(profile_ref)
        return next((item for item in self.profiles if item.ref == normalized), None)


@dataclass(frozen=True, slots=True)
class ReportProjectionTable:
    profile_ref: str
    check_item_id: str
    sheet_name: str
    column_ids: tuple[str, ...]
    headers: tuple[str, ...]
    rows: tuple[tuple[Any, ...], ...]


def normalize_projection_ref(value: Any) -> str:
    """Resolve legacy profile names to their explicit v1 reference."""

    text = str(value or "").strip()
    if not text:
        raise ValueError("report projection reference is required")
    if "@" not in text:
        return f"{text}@1"
    profile_id, raw_version = text.rsplit("@", 1)
    try:
        version = int(raw_version)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid report projection reference: {text}") from exc
    if not profile_id or version < 1:
        raise ValueError(f"invalid report projection reference: {text}")
    return f"{profile_id}@{version}"


def _identifier(value: Any) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9]+", "_", str(value or "")).strip("_")
    return normalized.lower() or "value"


def _automatic_projection(
    check_item: Mapping[str, Any],
) -> tuple[str, dict[str, Any], dict[str, str], dict[str, str]]:
    """Compile a frozen report projection from one versioned canonical contract."""

    check_item_id = str(check_item.get("check_item_id") or "").strip()
    check_item_version = int(check_item.get("version") or 1)
    result_mode = str(check_item.get("result_mode") or "structured").strip()
    contract = _mapping(
        check_item.get("canonical_contract") or {},
        label=f"{check_item_id}.canonical_contract",
    )
    canonical_catalog = rule_semantics_catalog(dict(contract))
    column_labels: dict[str, str] = {}
    canonical_columns: list[dict[str, Any]] = []

    if result_mode in {"normalized_diff", "hash_only"}:
        for source in sorted(_CHANGE_SOURCES):
            column_id = _identifier(source)
            canonical_columns.append(
                {
                    "column_id": column_id,
                    "source": source,
                    "dimensions": {},
                    "unit": None,
                }
            )
            column_labels[column_id] = source
        source_kind = "normalized_change"
    else:
        for raw_observation in contract.get("observations") or []:
            observation = _mapping(
                raw_observation,
                label=f"{check_item_id}.canonical_contract.observations[]",
            )
            observation_type = str(observation.get("observation_type") or "").strip()
            definition = canonical_catalog.get(observation_type)
            if definition is None:
                raise ValueError(
                    f"{check_item_id} references unknown canonical observation "
                    f"{observation_type or '<empty>'}"
                )
            dimension_names = tuple(str(item) for item in observation.get("dimensions") or ())
            expanded_dimensions = [
                (name, definition.allowed_dimension_values.get(name) or ())
                for name in dimension_names
                if definition.allowed_dimension_values.get(name)
            ]
            combinations = (
                itertools.product(*(values for _, values in expanded_dimensions))
                if expanded_dimensions
                else [()]
            )
            for values in combinations:
                dimensions = {name: value for (name, _), value in zip(expanded_dimensions, values)}
                suffix = "".join(
                    f"_{_identifier(name)}_{_identifier(value)}"
                    for name, value in dimensions.items()
                )
                column_id = f"{_identifier(observation_type)}{suffix}"
                canonical_columns.append(
                    {
                        "column_id": column_id,
                        "source": observation_type,
                        "dimensions": dimensions,
                        "unit": str(
                            observation.get("unit") or definition.canonical_unit or ""
                        ).strip()
                        or None,
                    }
                )
                dimension_label = ", ".join(f"{name}={value}" for name, value in dimensions.items())
                column_labels[column_id] = (
                    f"{definition.description} ({dimension_label})"
                    if dimension_label
                    else definition.description or observation_type
                )
        source_kind = "canonical_observation"

    profile_id = f"auto.{check_item_id}"
    profile_ref = f"{profile_id}@{check_item_version}"
    sheet_id = f"auto_{_identifier(check_item_id)}"
    status_columns = [
        "execution_status",
        "assessment_status",
        "completeness_status",
        "blocking_reason_codes",
    ]
    if source_kind == "normalized_change":
        status_columns.insert(3, "comparison_status")
        evidence_columns = [
            "execution_id",
            "previous_snapshot_id",
            "current_snapshot_id",
            "runtime_bundle_digest",
            "observed_at",
        ]
    else:
        status_columns[3:3] = ["threshold_unknown_count", "risk_verification_status"]
        evidence_columns = [
            "execution_id",
            "snapshot_id",
            "parser_template_id",
            "runtime_bundle_digest",
            "observed_at",
        ]
    stable_profile = {
        "profile_id": profile_id,
        "version": check_item_version,
        "check_item_id": check_item_id,
        "source_kind": source_kind,
        "row_grain": str(contract.get("entity_type") or "device"),
        "canonical_columns": canonical_columns,
        "status_columns": status_columns,
        "evidence_columns": evidence_columns,
        "sheet_id": sheet_id,
        "sort": ["device_name", "entity_key"],
        "null_policy": "preserve_null",
        "projection_strategy": "canonical_contract_auto",
    }
    return (
        profile_ref,
        {**stable_profile, "digest": _digest(stable_profile)},
        {sheet_id: str(check_item.get("name") or check_item_id)},
        column_labels,
    )


def _load_column(
    raw: Any,
    *,
    profile_id: str,
    source_kind: str,
) -> ProjectionColumn:
    payload = _mapping(raw, label=f"{profile_id}.canonical_columns[]")
    column_id = str(payload.get("column_id") or "").strip()
    source = str(payload.get("source") or "").strip()
    if not column_id or not source:
        raise ValueError(f"{profile_id} has an incomplete projection column")
    dimensions = tuple(
        sorted(
            (str(key), str(value))
            for key, value in _mapping(
                payload.get("dimensions") or {},
                label=f"{profile_id}.{column_id}.dimensions",
            ).items()
        )
    )
    unit = str(payload.get("unit") or "").strip() or None
    canonical_catalog = load_canonical_catalog()
    if source_kind == "canonical_observation":
        definition = canonical_catalog.get(source)
        if definition is None:
            raise ValueError(
                f"{profile_id}.{column_id} references unknown canonical observation {source}"
            )
        unknown_dimensions = sorted(set(dict(dimensions)) - set(definition.dimensions))
        if unknown_dimensions:
            raise ValueError(
                f"{profile_id}.{column_id} references unknown dimensions: "
                + ", ".join(unknown_dimensions)
            )
        if unit and definition.canonical_unit and unit != definition.canonical_unit:
            raise ValueError(f"{profile_id}.{column_id} unit does not match the canonical catalog")
    elif source not in _CHANGE_SOURCES:
        raise ValueError(f"{profile_id}.{column_id} has unsupported change source {source}")
    return ProjectionColumn(
        column_id=column_id,
        source=source,
        dimensions=dimensions,
        unit=unit,
    )


@lru_cache(maxsize=4)
def load_report_projection_catalog(
    path: str | Path = DEFAULT_PROJECTION_CATALOG_PATH,
    presentation_path: str | Path = DEFAULT_PROJECTION_PRESENTATION_PATH,
) -> ReportProjectionCatalog:
    source = Path(path)
    presentation_source = Path(presentation_path)
    raw_bytes = source.read_bytes()
    presentation_bytes = presentation_source.read_bytes()
    payload = yaml.safe_load(raw_bytes.decode("utf-8")) or {}
    presentation = yaml.safe_load(presentation_bytes.decode("utf-8")) or {}
    if not isinstance(payload, Mapping) or not isinstance(presentation, Mapping):
        raise ValueError("report projection catalogs must be objects")
    catalog_id = str(payload.get("catalog_id") or "").strip()
    version = int(payload.get("catalog_version") or 0)
    presentation_id = str(presentation.get("presentation_id") or "").strip()
    presentation_version = int(presentation.get("version") or 0)
    if not catalog_id or version < 1 or not presentation_id or presentation_version < 1:
        raise ValueError("report projection catalog metadata is invalid")
    sheet_labels = {
        str(key): str(value).strip()
        for key, value in _mapping(
            presentation.get("sheets") or {},
            label="presentation.sheets",
        ).items()
    }
    column_labels = {
        str(key): str(value).strip()
        for key, value in _mapping(
            presentation.get("columns") or {},
            label="presentation.columns",
        ).items()
    }
    profiles: list[ReportProjectionProfile] = []
    seen_refs: set[str] = set()
    seen_sheets: set[str] = set()
    for index, raw in enumerate(payload.get("profiles") or []):
        row = _mapping(raw, label=f"profiles[{index}]")
        profile_id = str(row.get("profile_id") or "").strip()
        profile_version = int(row.get("version") or 0)
        check_item_id = str(row.get("check_item_id") or "").strip()
        source_kind = str(row.get("source_kind") or "").strip()
        sheet_id = str(row.get("sheet_id") or "").strip()
        row_grain = str(row.get("row_grain") or "").strip()
        null_policy = str(row.get("null_policy") or "").strip()
        ref = f"{profile_id}@{profile_version}"
        if (
            not profile_id
            or profile_version < 1
            or not check_item_id
            or not row_grain
            or source_kind not in _SOURCE_KINDS
            or null_policy not in _NULL_POLICIES
        ):
            raise ValueError(f"profiles[{index}] metadata is invalid")
        if ref in seen_refs:
            raise ValueError(f"duplicated report projection profile: {ref}")
        if sheet_id in seen_sheets or sheet_id not in sheet_labels:
            raise ValueError(f"{ref} has an unknown or duplicated sheet_id")
        seen_refs.add(ref)
        seen_sheets.add(sheet_id)
        columns = tuple(
            _load_column(item, profile_id=profile_id, source_kind=source_kind)
            for item in row.get("canonical_columns") or []
        )
        column_ids = [item.column_id for item in columns]
        if not columns or len(column_ids) != len(set(column_ids)):
            raise ValueError(f"{ref} requires unique projection columns")
        status_columns = tuple(str(item) for item in row.get("status_columns") or [])
        evidence_columns = tuple(str(item) for item in row.get("evidence_columns") or [])
        if set(status_columns) - _STATUS_FIELDS:
            raise ValueError(f"{ref} has unsupported status columns")
        if set(evidence_columns) - _EVIDENCE_FIELDS:
            raise ValueError(f"{ref} has unsupported evidence columns")
        all_column_ids = (
            *_BASE_COLUMN_IDS,
            *column_ids,
            *status_columns,
            *evidence_columns,
        )
        missing_labels = sorted(set(all_column_ids) - set(column_labels))
        if missing_labels:
            raise ValueError(f"{ref} lacks presentation labels: {missing_labels}")
        stable_payload = {
            "profile_id": profile_id,
            "version": profile_version,
            "check_item_id": check_item_id,
            "source_kind": source_kind,
            "row_grain": row_grain,
            "canonical_columns": [item.to_dict() for item in columns],
            "status_columns": list(status_columns),
            "evidence_columns": list(evidence_columns),
            "sheet_id": sheet_id,
            "sort": [str(item) for item in row.get("sort") or []],
            "null_policy": null_policy,
        }
        profiles.append(
            ReportProjectionProfile(
                profile_id=profile_id,
                version=profile_version,
                check_item_id=check_item_id,
                source_kind=source_kind,
                row_grain=row_grain,
                canonical_columns=columns,
                status_columns=status_columns,
                evidence_columns=evidence_columns,
                sheet_id=sheet_id,
                sort=tuple(stable_payload["sort"]),
                null_policy=null_policy,
                digest=_digest(stable_payload),
            )
        )
    if not profiles:
        raise ValueError("report projection catalog requires profiles")
    return ReportProjectionCatalog(
        catalog_id=catalog_id,
        version=version,
        digest=hashlib.sha256(raw_bytes).hexdigest(),
        presentation_id=presentation_id,
        presentation_version=presentation_version,
        presentation_digest=hashlib.sha256(presentation_bytes).hexdigest(),
        profiles=tuple(profiles),
        sheet_labels=sheet_labels,
        column_labels=column_labels,
    )


def build_report_projection_snapshot(
    check_items: Mapping[str, Any],
    *,
    catalog: ReportProjectionCatalog | None = None,
) -> dict[str, Any]:
    """Freeze exact profile and presentation data into a report fact set."""

    selected = catalog or load_report_projection_catalog()
    profiles: dict[str, Any] = {}
    bindings: dict[str, Any] = {}
    sheet_labels = dict(selected.sheet_labels)
    column_labels = dict(selected.column_labels)
    for version_id, raw in sorted(check_items.items(), key=lambda item: str(item[0])):
        check_item = _mapping(raw, label=f"check_items.{version_id}")
        check_item_id = str(check_item.get("check_item_id") or "").strip()
        profile_ref = normalize_projection_ref(check_item.get("report_projection"))
        if profile_ref == _AUTO_PROJECTION_REF:
            profile_ref, profile_payload, dynamic_sheets, dynamic_columns = _automatic_projection(
                check_item
            )
            previous = profiles.setdefault(profile_ref, profile_payload)
            if previous.get("digest") != profile_payload.get("digest"):
                raise ValueError(f"conflicting automatic report projection: {profile_ref}")
            sheet_labels.update(dynamic_sheets)
            column_labels.update(dynamic_columns)
            profile_digest = str(profile_payload["digest"])
        else:
            profile = selected.get(profile_ref)
            if profile is None:
                raise ValueError(f"unknown report projection profile: {profile_ref}")
            if profile.check_item_id != check_item_id:
                raise ValueError(f"{version_id} cannot bind {profile_ref}: check item mismatch")
            profiles.setdefault(profile_ref, profile.to_dict())
            profile_digest = profile.digest
        bindings[str(version_id)] = {
            "check_item_id": check_item_id,
            "profile_ref": profile_ref,
            "profile_digest": profile_digest,
        }
    stable = {
        "schema_version": "report-projection-snapshot/v1",
        "catalog_id": selected.catalog_id,
        "catalog_version": selected.version,
        "catalog_digest": selected.digest,
        "presentation_id": selected.presentation_id,
        "presentation_version": selected.presentation_version,
        "presentation_digest": selected.presentation_digest,
        "sheets": dict(sorted(sheet_labels.items())),
        "columns": dict(sorted(column_labels.items())),
        "profiles": profiles,
        "bindings": bindings,
    }
    return {**stable, "digest": _digest(stable)}


def validate_report_projection_snapshot(snapshot: Mapping[str, Any]) -> None:
    payload = dict(snapshot)
    frozen_digest = str(payload.pop("digest", ""))
    if payload.get("schema_version") != "report-projection-snapshot/v1":
        raise ValueError("report projection snapshot schema is invalid")
    if not frozen_digest or _digest(payload) != frozen_digest:
        raise ValueError("report projection snapshot digest is invalid")
    profiles = _mapping(payload.get("profiles") or {}, label="snapshot.profiles")
    bindings = _mapping(payload.get("bindings") or {}, label="snapshot.bindings")
    for version_id, raw in bindings.items():
        binding = _mapping(raw, label=f"snapshot.bindings.{version_id}")
        profile_ref = str(binding.get("profile_ref") or "")
        profile = _mapping(profiles.get(profile_ref), label=f"snapshot.profiles.{profile_ref}")
        if str(profile.get("digest") or "") != str(binding.get("profile_digest") or ""):
            raise ValueError(f"report projection binding digest is invalid: {version_id}")
        stable_profile = dict(profile)
        profile_digest = str(stable_profile.pop("digest", ""))
        if _digest(stable_profile) != profile_digest:
            raise ValueError(f"report projection profile digest is invalid: {profile_ref}")


def _rows(value: Any) -> list[dict[str, Any]]:
    return [dict(item) for item in value or [] if isinstance(item, Mapping)]


def _observation_value(observation: Mapping[str, Any]) -> Any:
    if observation.get("value_number") is not None:
        return observation.get("value_number")
    if observation.get("value_text") is not None:
        return observation.get("value_text")
    return observation.get("value")


def _sort_token(row: Mapping[str, Any]) -> tuple[str, str]:
    return str(row.get("observed_at") or ""), str(row.get("id") or "")


def _canonical_profile_rows(
    profile: Mapping[str, Any],
    matrix_rows: Sequence[dict[str, Any]],
    observations: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    columns = _rows(profile.get("canonical_columns"))
    observations_by_execution: dict[str, list[dict[str, Any]]] = {}
    for observation in observations:
        for execution_id in observation.get("execution_ids") or []:
            observations_by_execution.setdefault(str(execution_id), []).append(observation)
    for matrix in matrix_rows:
        execution_id = str(matrix.get("execution_id") or "")
        matching = observations_by_execution.get(execution_id, [])
        entity_keys = sorted(
            {
                (
                    str(item.get("entity_type") or ""),
                    str(item.get("entity_key") or ""),
                )
                for item in matching
                if any(str(item.get("observation_type") or "") == col["source"] for col in columns)
            }
        )
        if not entity_keys:
            entity_keys = [("", "")]
        for entity_type, entity_key in entity_keys:
            entity_observations = [
                item
                for item in matching
                if str(item.get("entity_type") or "") == entity_type
                and str(item.get("entity_key") or "") == entity_key
            ]
            row = {
                **matrix,
                "device_name": matrix.get("device_name"),
                "management_ip": matrix.get("management_ip"),
                "entity_type": entity_type,
                "entity_key": entity_key,
                "execution_id": execution_id,
                "runtime_bundle_digest": matrix.get("runtime_bundle_digest"),
            }
            selected_observations: list[dict[str, Any]] = []
            for column in columns:
                dimensions = dict(column.get("dimensions") or {})
                candidates = [
                    item
                    for item in entity_observations
                    if str(item.get("observation_type") or "") == str(column.get("source") or "")
                    and all(
                        str(dict(item.get("dimensions") or {}).get(key)) == str(value)
                        for key, value in dimensions.items()
                    )
                ]
                if candidates:
                    selected_observation = max(candidates, key=_sort_token)
                    selected_observations.append(selected_observation)
                    row[str(column["column_id"])] = _observation_value(selected_observation)
                else:
                    row[str(column["column_id"])] = None
            if selected_observations:
                latest = max(selected_observations, key=_sort_token)
                row["snapshot_id"] = latest.get("snapshot_id")
                row["parser_template_id"] = latest.get("parser_template_id")
                row["observed_at"] = latest.get("observed_at")
            output.append(row)
    return output


def _change_profile_rows(
    profile: Mapping[str, Any],
    matrix_rows: Sequence[dict[str, Any]],
    changes: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    columns = _rows(profile.get("canonical_columns"))
    changes_by_execution: dict[str, list[dict[str, Any]]] = {}
    for change in changes:
        changes_by_execution.setdefault(str(change.get("execution_id") or ""), []).append(change)
    for matrix in matrix_rows:
        execution_id = str(matrix.get("execution_id") or "")
        matching = changes_by_execution.get(execution_id) or [None]
        for change in matching:
            row = {
                **matrix,
                "device_name": matrix.get("device_name"),
                "management_ip": matrix.get("management_ip"),
                "entity_type": (change or {}).get("entity_type"),
                "entity_key": (change or {}).get("entity_key"),
                "execution_id": execution_id,
                "runtime_bundle_digest": matrix.get("runtime_bundle_digest"),
            }
            for column in columns:
                source = str(column.get("source") or "")
                row[str(column["column_id"])] = (change or {}).get(source.split(".", 1)[1])
            row["previous_snapshot_id"] = (change or {}).get("previous_snapshot_id")
            row["current_snapshot_id"] = (change or {}).get("current_snapshot_id")
            row["observed_at"] = (change or {}).get("observed_at")
            output.append(row)
    return output


def project_report_tables(report: Mapping[str, Any]) -> tuple[ReportProjectionTable, ...]:
    """Project frozen canonical facts without consulting mutable catalogs."""

    snapshot = report.get("report_projection_snapshot")
    if not isinstance(snapshot, Mapping) or not snapshot:
        return ()
    validate_report_projection_snapshot(snapshot)
    profiles = _mapping(snapshot.get("profiles") or {}, label="snapshot.profiles")
    bindings = _mapping(snapshot.get("bindings") or {}, label="snapshot.bindings")
    sheets = _mapping(snapshot.get("sheets") or {}, label="snapshot.sheets")
    labels = _mapping(snapshot.get("columns") or {}, label="snapshot.columns")
    matrix = _rows(report.get("matrix"))
    observations = _rows(report.get("observations"))
    changes = _rows(report.get("changes"))
    tables: list[ReportProjectionTable] = []
    for profile_ref, raw_profile in sorted(profiles.items()):
        profile = _mapping(raw_profile, label=f"snapshot.profiles.{profile_ref}")
        version_ids = {
            version_id
            for version_id, raw_binding in bindings.items()
            if str(_mapping(raw_binding, label="snapshot.binding").get("profile_ref") or "")
            == profile_ref
        }
        matching_matrix = [
            item for item in matrix if str(item.get("check_item_version_id") or "") in version_ids
        ]
        if not matching_matrix:
            continue
        if profile.get("source_kind") == "canonical_observation":
            projected = _canonical_profile_rows(profile, matching_matrix, observations)
        else:
            projected = _change_profile_rows(profile, matching_matrix, changes)
        column_ids = (
            *_BASE_COLUMN_IDS,
            *(str(item.get("column_id")) for item in _rows(profile.get("canonical_columns"))),
            *(str(item) for item in profile.get("status_columns") or []),
            *(str(item) for item in profile.get("evidence_columns") or []),
        )
        sort_fields = [str(item) for item in profile.get("sort") or []]
        projected.sort(key=lambda row: tuple(str(row.get(field) or "") for field in sort_fields))
        tables.append(
            ReportProjectionTable(
                profile_ref=profile_ref,
                check_item_id=str(profile.get("check_item_id") or ""),
                sheet_name=str(sheets.get(str(profile.get("sheet_id") or "")) or ""),
                column_ids=tuple(column_ids),
                headers=tuple(str(labels.get(item) or item) for item in column_ids),
                rows=tuple(tuple(row.get(item) for item in column_ids) for row in projected),
            )
        )
    return tuple(tables)
