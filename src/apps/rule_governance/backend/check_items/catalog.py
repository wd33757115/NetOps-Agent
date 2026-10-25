"""Versioned check-item catalog loading and persistence."""

from __future__ import annotations

import hashlib
import json
import logging
import re
import uuid
from collections.abc import Callable, Iterable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from src.core.check_items.command_adapter import (
    is_direct_projection_adapter,
    validate_adapter_spec,
)
from src.infrastructure.db.models import (
    CheckItemImplementationLinkRecord,
    CheckItemRecord,
    CheckItemVersionRecord,
    CollectionImplementationRecord,
    CollectionImplementationVersionRecord,
)
from src.infrastructure.db.postgres import get_db_session

DEFAULT_CATALOG_PATH = Path(__file__).parent / "assets" / "catalog.yaml"
_ASSET_ID_RE = re.compile(r"^[a-z][a-z0-9_.-]+$")
_RESULT_MODES = {"structured", "normalized_diff", "hash_only", "evidence_only"}
_ASSESSMENT_MODES = {"health", "change", "evidence"}
_STATUSES = {"draft", "active", "deprecated"}
_COVERAGE_MODELS = {
    "fixed",
    "inventory_entity_set",
    "derived_entity_set",
    "command_reported_total",
    "non_exhaustive",
}
_EMPTY_CLASSIFICATIONS = {
    "invalid",
    "expected_empty",
    "not_applicable",
    "unsupported",
}
_OUTPUT_PATTERN_KEYS = {
    "unsupported_patterns",
    "command_error_patterns",
    "truncation_patterns",
    "paging_patterns",
    "valid_empty_patterns",
}

logger = logging.getLogger(__name__)
_WARNED_MULTI_COMMAND_IMPLEMENTATIONS: set[tuple[str, str]] = set()


class CatalogConflictError(ValueError):
    """Raised when an immutable catalog version is changed in place."""


@dataclass(frozen=True)
class CheckItemDefinition:
    id: str
    version: int
    name: str
    category: str
    description: str
    result_mode: str
    assessment_mode: str
    canonical_contract: dict[str, Any]
    report_projection: str
    applicability: dict[str, Any]
    freshness_policy: dict[str, Any]
    empty_result_policy: dict[str, Any]
    coverage_policy: dict[str, Any]
    status: str
    digest: str
    version_id: str


@dataclass(frozen=True)
class CollectionCommand:
    command: str
    priority: int


def primary_collection_commands(
    commands: Iterable[CollectionCommand],
    *,
    implementation_id: str,
    version_id: str = "",
) -> tuple[CollectionCommand, ...]:
    """Return the single declared command and warn for legacy fallback rows."""

    ordered = tuple(
        sorted(
            commands,
            key=lambda item: (-int(item.priority), item.command.casefold()),
        )
    )
    if len(ordered) > 1:
        warning_key = (str(implementation_id), str(version_id))
        if warning_key not in _WARNED_MULTI_COMMAND_IMPLEMENTATIONS:
            _WARNED_MULTI_COMMAND_IMPLEMENTATIONS.add(warning_key)
            logger.warning(
                "collection fallback commands are ignored; only the primary command is used",
                extra={
                    "event": "collection_fallback_commands_ignored",
                    "implementation_id": str(implementation_id),
                    "implementation_version_id": str(version_id),
                    "primary_command": ordered[0].command,
                    "ignored_commands": [item.command for item in ordered[1:]],
                },
            )
    return ordered[:1]


@dataclass(frozen=True)
class CollectionImplementation:
    id: str
    version: int
    name: str
    description: str
    check_item_ids: tuple[str, ...]
    match: dict[str, Any]
    commands: tuple[CollectionCommand, ...]
    collector: str
    parser_requirement: str
    template_scope: str | None
    adapter_spec: dict[str, Any]
    output_quality_policy: dict[str, Any]
    selection_priority: int
    status: str
    digest: str
    version_id: str


@dataclass(frozen=True)
class CheckItemCatalog:
    catalog_version: int
    check_items: tuple[CheckItemDefinition, ...]
    implementations: tuple[CollectionImplementation, ...]

    def check_item(self, check_item_id: str) -> CheckItemDefinition | None:
        return next((item for item in self.check_items if item.id == check_item_id), None)


def _canonical_digest(payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _version_id(kind: str, asset_id: str, version: int) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"netops/{kind}/{asset_id}/v{version}"))


def _require_asset_id(value: Any, *, label: str) -> str:
    asset_id = str(value or "").strip()
    if not _ASSET_ID_RE.fullmatch(asset_id):
        raise ValueError(f"{label} has invalid id: {asset_id or '<empty>'}")
    return asset_id


def _require_version(value: Any, *, label: str) -> int:
    try:
        version = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} version must be an integer") from exc
    if version < 1:
        raise ValueError(f"{label} version must be positive")
    return version


def _load_check_item(
    raw: dict[str, Any],
    *,
    defaults: dict[str, Any] | None = None,
) -> CheckItemDefinition:
    defaults = dict(defaults or {})
    check_item_id = _require_asset_id(raw.get("id"), label="check item")
    version = _require_version(raw.get("version"), label=check_item_id)
    result_mode = str(raw.get("result_mode") or "").strip()
    assessment_mode = str(raw.get("assessment_mode") or "").strip()
    status = str(raw.get("status") or "draft").strip()
    if result_mode not in _RESULT_MODES:
        raise ValueError(f"{check_item_id} has invalid result_mode: {result_mode}")
    if assessment_mode not in _ASSESSMENT_MODES:
        raise ValueError(f"{check_item_id} has invalid assessment_mode: {assessment_mode}")
    if status not in _STATUSES:
        raise ValueError(f"{check_item_id} has invalid status: {status}")
    canonical_contract = raw.get("canonical_contract")
    if not isinstance(canonical_contract, dict) or not canonical_contract:
        raise ValueError(f"{check_item_id} requires canonical_contract")
    from src.core.check_items.value_contract import normalize_value_contract
    canonical_contract = normalize_value_contract(canonical_contract)
    report_projection = str(raw.get("report_projection") or "").strip()
    if not report_projection:
        raise ValueError(f"{check_item_id} requires report_projection")
    version_payload = {
        "id": check_item_id,
        "version": version,
        "result_mode": result_mode,
        "assessment_mode": assessment_mode,
        "canonical_contract": canonical_contract,
        "report_projection": report_projection,
        "applicability": dict(raw.get("applicability") or {}),
        "status": status,
    }
    policy_fields = {
        key: {
            **dict(defaults.get(key) or {}),
            **dict(raw.get(key) or {}),
        }
        for key in (
            "freshness_policy",
            "empty_result_policy",
            "coverage_policy",
        )
    }
    for key, value in policy_fields.items():
        if key in raw or value:
            version_payload[key] = value
    return CheckItemDefinition(
        id=check_item_id,
        version=version,
        name=str(raw.get("name") or check_item_id).strip(),
        category=str(raw.get("category") or "other").strip(),
        description=str(raw.get("description") or "").strip(),
        result_mode=result_mode,
        assessment_mode=assessment_mode,
        canonical_contract=dict(canonical_contract),
        report_projection=report_projection,
        applicability=dict(raw.get("applicability") or {}),
        freshness_policy=policy_fields["freshness_policy"],
        empty_result_policy=policy_fields["empty_result_policy"],
        coverage_policy=policy_fields["coverage_policy"],
        status=status,
        digest=_canonical_digest(version_payload),
        version_id=_version_id("check-item", check_item_id, version),
    )


def _load_implementation(
    raw: dict[str, Any],
    *,
    known_check_item_ids: set[str],
    defaults: dict[str, Any] | None = None,
) -> CollectionImplementation:
    defaults = dict(defaults or {})
    implementation_id = _require_asset_id(raw.get("id"), label="implementation")
    version = _require_version(raw.get("version"), label=implementation_id)
    check_item_ids = tuple(
        _require_asset_id(item, label=f"{implementation_id}.check_item_ids")
        for item in (raw.get("check_item_ids") or [])
    )
    if not check_item_ids:
        raise ValueError(f"{implementation_id} requires check_item_ids")
    unknown = sorted(set(check_item_ids) - known_check_item_ids)
    if unknown:
        raise ValueError(f"{implementation_id} references unknown check items: {unknown}")
    match = raw.get("match")
    if not isinstance(match, dict) or not match:
        raise ValueError(f"{implementation_id} requires a non-empty match selector")
    for pattern_key in ("model_patterns", "os_version_patterns"):
        for pattern in match.get(pattern_key) or []:
            try:
                re.compile(str(pattern))
            except re.error as exc:
                raise ValueError(
                    f"{implementation_id} has invalid {pattern_key} regex: {pattern}"
                ) from exc
    command_rows = raw.get("commands")
    if not isinstance(command_rows, list) or not command_rows:
        raise ValueError(f"{implementation_id} requires commands")
    commands: list[CollectionCommand] = []
    seen_commands: set[str] = set()
    for index, command_row in enumerate(command_rows):
        if isinstance(command_row, str):
            command = command_row.strip()
            priority = 100 - index
        elif isinstance(command_row, dict):
            command = str(command_row.get("command") or "").strip()
            priority = int(command_row.get("priority") or 0)
        else:
            raise ValueError(f"{implementation_id}.commands must contain strings or objects")
        normalized = " ".join(command.lower().split())
        if not normalized:
            raise ValueError(f"{implementation_id} contains an empty command")
        if normalized in seen_commands:
            raise ValueError(f"{implementation_id} contains duplicate command: {command}")
        seen_commands.add(normalized)
        commands.append(CollectionCommand(command=command, priority=priority))
    adapter_spec = validate_adapter_spec(
        raw.get("adapter_spec"),
        check_item_ids=check_item_ids,
        require_pinned_check_item_versions=is_direct_projection_adapter(
            raw.get("adapter_spec")
        ),
    )
    if adapter_spec and len(commands) != 1:
        raise ValueError(
            f"{implementation_id} command adapters require exactly one command"
        )
    parser_requirement = str(raw.get("parser_requirement") or "").strip()
    if parser_requirement not in _RESULT_MODES:
        raise ValueError(
            f"{implementation_id} has invalid parser_requirement: {parser_requirement}"
        )
    status = str(raw.get("status") or "draft").strip()
    if status not in _STATUSES:
        raise ValueError(f"{implementation_id} has invalid status: {status}")
    version_payload = {
        "id": implementation_id,
        "version": version,
        "check_item_ids": list(check_item_ids),
        "match": match,
        "commands": [
            {"command": command.command, "priority": command.priority}
            for command in commands
        ],
        "collector": str(raw.get("collector") or "cli").strip(),
        "parser_requirement": parser_requirement,
        "template_scope": raw.get("template_scope"),
        "selection_priority": int(raw.get("selection_priority") or 100),
        "status": status,
    }
    # Preserve immutable digests created before CommandAdapter existed. An
    # empty adapter carries no new semantics and must not rewrite old history.
    if adapter_spec:
        version_payload["adapter_spec"] = adapter_spec
    output_quality_policy = {
        **dict(defaults.get("output_quality_policy") or {}),
        **dict(raw.get("output_quality_policy") or {}),
    }
    if "output_quality_policy" in raw or output_quality_policy:
        version_payload["output_quality_policy"] = output_quality_policy
    return CollectionImplementation(
        id=implementation_id,
        version=version,
        name=str(raw.get("name") or implementation_id).strip(),
        description=str(raw.get("description") or "").strip(),
        check_item_ids=check_item_ids,
        match=dict(match),
        commands=tuple(commands),
        collector=version_payload["collector"],
        parser_requirement=parser_requirement,
        template_scope=(
            str(raw.get("template_scope")).strip() if raw.get("template_scope") else None
        ),
        adapter_spec=adapter_spec,
        output_quality_policy=output_quality_policy,
        selection_priority=version_payload["selection_priority"],
        status=status,
        digest=_canonical_digest(version_payload),
        version_id=_version_id("collection-implementation", implementation_id, version),
    )


def load_catalog_defaults(
    path: str | Path = DEFAULT_CATALOG_PATH,
) -> dict[str, dict[str, Any]]:
    """Load reusable catalog defaults for governed UI-created revisions."""

    payload = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    defaults = payload.get("defaults") or {}
    if not isinstance(defaults, dict):
        raise ValueError("check-item catalog defaults must be an object")
    check_item = defaults.get("check_item") or {}
    implementation = defaults.get("implementation") or {}
    if not isinstance(check_item, dict) or not isinstance(implementation, dict):
        raise ValueError("check-item catalog defaults contain invalid sections")
    return {
        "check_item": dict(check_item),
        "implementation": dict(implementation),
    }


def build_check_item_definition(
    raw: dict[str, Any],
    *,
    defaults: dict[str, Any] | None = None,
) -> CheckItemDefinition:
    """Validate and canonicalize one check-item revision payload."""

    return _load_check_item(dict(raw), defaults=defaults)


def validate_check_item_definition(item: CheckItemDefinition) -> None:
    """Validate one UI-authored check item before it enters governance."""

    from src.core.check_items.canonical import validate_check_item_contract

    errors = list(validate_check_item_contract(item))
    contract = dict(item.canonical_contract or {})
    if not str(contract.get("entity_type") or "").strip():
        errors.append(f"{item.id}.canonical_contract.entity_type is required")
    identity_dimensions = contract.get("identity_dimensions")
    if not isinstance(identity_dimensions, list) or not all(
        str(value).strip() for value in identity_dimensions
    ):
        errors.append(
            f"{item.id}.canonical_contract.identity_dimensions must be a non-empty list"
        )
    if item.result_mode == "structured" and not contract.get("observations"):
        errors.append(
            f"{item.id}.canonical_contract.observations is required for structured results"
        )
    from src.core.check_items.report_projection import (
        load_report_projection_catalog,
        normalize_projection_ref,
    )

    projection_ref = normalize_projection_ref(item.report_projection)
    if projection_ref != "auto@1":
        profile = load_report_projection_catalog().get(projection_ref)
        if profile is None:
            errors.append(f"{item.id} references unknown report projection {projection_ref}")
        elif profile.check_item_id != item.id:
            errors.append(
                f"{item.id} cannot use report projection {projection_ref}: "
                "check item mismatch"
            )
    if errors:
        raise ValueError("invalid check item definition: " + "; ".join(errors))
    _validate_evidence_policies(
        catalog_version=2,
        check_items=(item,),
        implementations=(),
    )


def build_collection_implementation(
    raw: dict[str, Any],
    *,
    known_check_item_ids: set[str],
    defaults: dict[str, Any] | None = None,
) -> CollectionImplementation:
    """Validate and canonicalize one collection implementation revision."""

    return _load_implementation(
        dict(raw),
        known_check_item_ids=set(known_check_item_ids),
        defaults=defaults,
    )


def load_catalog(path: str | Path = DEFAULT_CATALOG_PATH) -> CheckItemCatalog:
    source = Path(path)
    payload = yaml.safe_load(source.read_text(encoding="utf-8")) or {}
    if not isinstance(payload, dict):
        raise ValueError("check-item catalog must be an object")
    catalog_version = _require_version(payload.get("catalog_version"), label="catalog")
    raw_check_items = payload.get("check_items")
    raw_implementations = payload.get("implementations")
    if not isinstance(raw_check_items, list) or not raw_check_items:
        raise ValueError("check-item catalog requires check_items")
    if not isinstance(raw_implementations, list) or not raw_implementations:
        raise ValueError("check-item catalog requires implementations")
    if (source.parent / "system_check_items.yaml").is_file():
        # System base check items that topology discovery consumes ship with the catalog.
        from .system_items import check_item_payloads, implementation_payloads

        raw_check_items = [*raw_check_items, *check_item_payloads()]
        raw_implementations = [*raw_implementations, *implementation_payloads()]
    defaults = load_catalog_defaults(source)
    check_item_defaults = defaults["check_item"]
    implementation_defaults = defaults["implementation"]
    check_items = tuple(
        _load_check_item(
            dict(item),
            defaults=dict(check_item_defaults),
        )
        for item in raw_check_items
    )
    check_item_ids = [item.id for item in check_items]
    if len(check_item_ids) != len(set(check_item_ids)):
        raise ValueError("check-item catalog contains duplicate check item ids")
    implementations = tuple(
        _load_implementation(
            dict(item),
            known_check_item_ids=set(check_item_ids),
            defaults=dict(implementation_defaults),
        )
        for item in raw_implementations
    )
    implementation_ids = [item.id for item in implementations]
    if len(implementation_ids) != len(set(implementation_ids)):
        raise ValueError("check-item catalog contains duplicate implementation ids")
    from src.core.check_items.canonical import validate_check_item_catalog_contracts

    validate_check_item_catalog_contracts(check_items)
    _validate_evidence_policies(
        catalog_version=catalog_version,
        check_items=check_items,
        implementations=implementations,
    )
    return CheckItemCatalog(
        catalog_version=catalog_version,
        check_items=check_items,
        implementations=implementations,
    )


def _validate_evidence_policies(
    *,
    catalog_version: int,
    check_items: tuple[CheckItemDefinition, ...],
    implementations: tuple[CollectionImplementation, ...],
) -> None:
    if catalog_version < 2:
        return
    health_ids = {
        item.id for item in check_items if item.assessment_mode == "health"
    }
    for item in check_items:
        if item.assessment_mode != "health":
            continue
        if not item.freshness_policy:
            raise ValueError(f"{item.id} requires freshness_policy")
        if not item.empty_result_policy:
            raise ValueError(f"{item.id} requires empty_result_policy")
        if not item.coverage_policy:
            raise ValueError(f"{item.id} requires coverage_policy")
        if not (
            item.freshness_policy.get("max_observation_age")
            or item.freshness_policy.get("max_observation_age_seconds")
        ):
            raise ValueError(
                f"{item.id} freshness_policy requires max observation age"
            )
        if not (
            item.freshness_policy.get("max_collection_span")
            or item.freshness_policy.get("max_collection_span_seconds")
        ):
            raise ValueError(
                f"{item.id} freshness_policy requires max collection span"
            )
        empty_classification = str(
            item.empty_result_policy.get("classification") or ""
        )
        if empty_classification not in _EMPTY_CLASSIFICATIONS:
            raise ValueError(
                f"{item.id} has invalid empty-result classification: "
                f"{empty_classification or '<empty>'}"
            )
        coverage_model = str(item.coverage_policy.get("model") or "")
        if coverage_model not in _COVERAGE_MODELS:
            raise ValueError(
                f"{item.id} has invalid coverage model: "
                f"{coverage_model or '<empty>'}"
            )
        if (
            coverage_model == "fixed"
            and item.coverage_policy.get("expected_count") is None
        ):
            raise ValueError(
                f"{item.id} fixed coverage requires expected_count"
            )
    for implementation in implementations:
        if not health_ids.intersection(implementation.check_item_ids):
            continue
        policy = dict(implementation.output_quality_policy or {})
        if not policy:
            raise ValueError(
                f"{implementation.id} requires output_quality_policy"
            )
        if int(policy.get("profile_version") or 0) < 1:
            raise ValueError(
                f"{implementation.id} output quality profile requires version"
            )
        for key in _OUTPUT_PATTERN_KEYS:
            patterns = policy.get(key)
            if patterns is None:
                continue
            if not isinstance(patterns, list):
                raise ValueError(
                    f"{implementation.id}.{key} must be an array"
                )
            for pattern in patterns:
                try:
                    re.compile(str(pattern))
                except re.error as exc:
                    raise ValueError(
                        f"{implementation.id} has invalid {key} regex: "
                        f"{pattern}"
                    ) from exc


class CheckItemCatalogRepository:
    """Persist immutable catalog versions without silently replacing active assets."""

    def __init__(
        self,
        session_scope: Callable[
            [], AbstractContextManager[Any]
        ] = get_db_session,
    ) -> None:
        self._session_scope = session_scope

    def bootstrap(
        self,
        catalog: CheckItemCatalog | None = None,
        *,
        actor: str = "catalog-bootstrap",
    ) -> dict[str, int]:
        catalog = catalog or load_catalog()
        created_items = 0
        created_implementations = 0
        created_links = 0
        now = datetime.now(timezone.utc)
        with self._session_scope() as db:
            for item in catalog.check_items:
                root = db.get(CheckItemRecord, item.id)
                activated_new_version = False
                if root is None:
                    root = CheckItemRecord(
                        id=item.id,
                        name=item.name,
                        category=item.category,
                        description=item.description,
                        status=item.status,
                        current_version=item.version if item.status == "active" else 0,
                        created_at=now,
                        updated_at=now,
                    )
                    db.add(root)
                    created_items += 1
                version = db.get(CheckItemVersionRecord, item.version_id)
                if version is not None and version.digest != item.digest:
                    raise CatalogConflictError(
                        f"check item {item.id} v{item.version} was changed in place"
                    )
                if version is None:
                    self._retire_older_active_check_item(
                        db,
                        item,
                        deprecated_at=now,
                    )
                    self._assert_no_other_active_check_item(db, item)
                    db.add(
                        CheckItemVersionRecord(
                            id=item.version_id,
                            check_item_id=item.id,
                            version=item.version,
                            result_mode=item.result_mode,
                            assessment_mode=item.assessment_mode,
                            canonical_contract_json=item.canonical_contract,
                            report_projection=item.report_projection,
                            applicability_json=item.applicability,
                            freshness_policy_json=(
                                item.freshness_policy or None
                            ),
                            empty_result_policy_json=(
                                item.empty_result_policy or None
                            ),
                            coverage_policy_json=(
                                item.coverage_policy or None
                            ),
                            status=item.status,
                            digest=item.digest,
                            created_by=actor,
                            created_at=now,
                            activated_at=now if item.status == "active" else None,
                        )
                    )
                    activated_new_version = item.status == "active"
                root.name = item.name
                root.category = item.category
                root.description = item.description
                root.updated_at = now
                active_item_version = None if activated_new_version else (
                    db.query(CheckItemVersionRecord)
                    .filter(CheckItemVersionRecord.check_item_id == item.id)
                    .filter(CheckItemVersionRecord.status == "active")
                    .first()
                )
                if (
                    item.status == "active"
                    and (
                        activated_new_version
                        or (
                            active_item_version is not None
                            and str(active_item_version.id) == item.version_id
                        )
                    )
                ):
                    root.status = "active"
                    root.current_version = item.version

            for implementation in catalog.implementations:
                root = db.get(CollectionImplementationRecord, implementation.id)
                activated_new_version = False
                if root is None:
                    root = CollectionImplementationRecord(
                        id=implementation.id,
                        name=implementation.name,
                        description=implementation.description,
                        status=implementation.status,
                        current_version=(
                            implementation.version
                            if implementation.status == "active"
                            else 0
                        ),
                        created_at=now,
                        updated_at=now,
                    )
                    db.add(root)
                    created_implementations += 1
                version = db.get(
                    CollectionImplementationVersionRecord,
                    implementation.version_id,
                )
                if version is not None and version.digest != implementation.digest:
                    raise CatalogConflictError(
                        f"implementation {implementation.id} v{implementation.version} "
                        "was changed in place"
                    )
                if version is None:
                    self._retire_older_active_implementation(
                        db,
                        implementation,
                        deprecated_at=now,
                    )
                    self._assert_no_other_active_implementation(db, implementation)
                    db.add(
                        CollectionImplementationVersionRecord(
                            id=implementation.version_id,
                            implementation_id=implementation.id,
                            version=implementation.version,
                            collector=implementation.collector,
                            match_json=implementation.match,
                            commands_json=[
                                {
                                    "command": command.command,
                                    "priority": command.priority,
                                }
                                for command in implementation.commands
                            ],
                            parser_requirement=implementation.parser_requirement,
                            template_scope=implementation.template_scope,
                            adapter_spec_json=(implementation.adapter_spec or None),
                            output_quality_policy_json=(
                                implementation.output_quality_policy or None
                            ),
                            selection_priority=implementation.selection_priority,
                            status=implementation.status,
                            digest=implementation.digest,
                            created_by=actor,
                            created_at=now,
                            activated_at=(
                                now if implementation.status == "active" else None
                            ),
                        )
                    )
                    activated_new_version = implementation.status == "active"
                root.name = implementation.name
                root.description = implementation.description
                root.updated_at = now
                active_implementation_version = None if activated_new_version else (
                    db.query(CollectionImplementationVersionRecord)
                    .filter(
                        CollectionImplementationVersionRecord.implementation_id
                        == implementation.id
                    )
                    .filter(CollectionImplementationVersionRecord.status == "active")
                    .first()
                )
                if (
                    implementation.status == "active"
                    and (
                        activated_new_version
                        or (
                            active_implementation_version is not None
                            and str(active_implementation_version.id)
                            == implementation.version_id
                        )
                    )
                ):
                    root.status = "active"
                    root.current_version = implementation.version

            db.flush()
            item_by_id = {item.id: item for item in catalog.check_items}
            for implementation in catalog.implementations:
                for check_item_id in implementation.check_item_ids:
                    item = item_by_id[check_item_id]
                    link_id = str(
                        uuid.uuid5(
                            uuid.NAMESPACE_URL,
                            f"netops/check-item-link/{item.version_id}/"
                            f"{implementation.version_id}",
                        )
                    )
                    if db.get(CheckItemImplementationLinkRecord, link_id) is not None:
                        continue
                    db.add(
                        CheckItemImplementationLinkRecord(
                            id=link_id,
                            check_item_version_id=item.version_id,
                            implementation_version_id=implementation.version_id,
                            priority=implementation.selection_priority,
                            required=True,
                            created_at=now,
                        )
                    )
                    created_links += 1
            db.flush()
        return {
            "created_check_items": created_items,
            "created_implementations": created_implementations,
            "created_links": created_links,
        }

    @staticmethod
    def _retire_older_active_check_item(
        db: Any,
        item: CheckItemDefinition,
        *,
        deprecated_at: datetime,
    ) -> None:
        if item.status != "active":
            return
        existing = (
            db.query(CheckItemVersionRecord)
            .filter(CheckItemVersionRecord.check_item_id == item.id)
            .filter(CheckItemVersionRecord.status == "active")
            .with_for_update()
            .first()
        )
        if existing is None or str(existing.id) == item.version_id:
            return
        if int(existing.version) >= item.version:
            raise CatalogConflictError(
                f"check item {item.id} cannot replace active version "
                f"{existing.version} with version {item.version}"
            )
        existing.status = "deprecated"
        existing.deprecated_at = deprecated_at
        db.flush()

    @staticmethod
    def _retire_older_active_implementation(
        db: Any,
        implementation: CollectionImplementation,
        *,
        deprecated_at: datetime,
    ) -> None:
        if implementation.status != "active":
            return
        existing = (
            db.query(CollectionImplementationVersionRecord)
            .filter(
                CollectionImplementationVersionRecord.implementation_id
                == implementation.id
            )
            .filter(
                CollectionImplementationVersionRecord.status == "active"
            )
            .with_for_update()
            .first()
        )
        if existing is None or str(existing.id) == implementation.version_id:
            return
        if int(existing.version) >= implementation.version:
            raise CatalogConflictError(
                f"implementation {implementation.id} cannot replace active "
                f"version {existing.version} with version "
                f"{implementation.version}"
            )
        existing.status = "deprecated"
        existing.deprecated_at = deprecated_at
        db.flush()

    @staticmethod
    def _assert_no_other_active_check_item(db: Any, item: CheckItemDefinition) -> None:
        if item.status != "active":
            return
        existing = (
            db.query(CheckItemVersionRecord)
            .filter(CheckItemVersionRecord.check_item_id == item.id)
            .filter(CheckItemVersionRecord.status == "active")
            .first()
        )
        if existing is not None:
            raise CatalogConflictError(
                f"check item {item.id} already has active version {existing.version}"
            )

    @staticmethod
    def _assert_no_other_active_implementation(
        db: Any,
        implementation: CollectionImplementation,
    ) -> None:
        if implementation.status != "active":
            return
        existing = (
            db.query(CollectionImplementationVersionRecord)
            .filter(
                CollectionImplementationVersionRecord.implementation_id
                == implementation.id
            )
            .filter(CollectionImplementationVersionRecord.status == "active")
            .first()
        )
        if existing is not None:
            raise CatalogConflictError(
                f"implementation {implementation.id} already has active version "
                f"{existing.version}"
            )

    def load_active(self) -> CheckItemCatalog:
        with self._session_scope() as db:
            item_rows = (
                db.query(CheckItemRecord, CheckItemVersionRecord)
                .join(
                    CheckItemVersionRecord,
                    CheckItemVersionRecord.check_item_id == CheckItemRecord.id,
                )
                .filter(CheckItemRecord.status == "active")
                .filter(CheckItemVersionRecord.status == "active")
                .order_by(CheckItemRecord.id)
                .all()
            )
            implementation_rows = (
                db.query(
                    CollectionImplementationRecord,
                    CollectionImplementationVersionRecord,
                )
                .join(
                    CollectionImplementationVersionRecord,
                    CollectionImplementationVersionRecord.implementation_id
                    == CollectionImplementationRecord.id,
                )
                .filter(CollectionImplementationRecord.status == "active")
                .filter(CollectionImplementationVersionRecord.status == "active")
                .order_by(CollectionImplementationRecord.id)
                .all()
            )
            links = db.query(CheckItemImplementationLinkRecord).all()
            item_id_by_version = {
                str(version.id): str(root.id) for root, version in item_rows
            }
            check_items = tuple(
                CheckItemDefinition(
                    id=str(root.id),
                    version=int(version.version),
                    name=str(root.name),
                    category=str(root.category),
                    description=str(root.description or ""),
                    result_mode=str(version.result_mode),
                    assessment_mode=str(version.assessment_mode),
                    canonical_contract=dict(version.canonical_contract_json or {}),
                    report_projection=str(version.report_projection),
                    applicability=dict(version.applicability_json or {}),
                    freshness_policy=dict(
                        version.freshness_policy_json or {}
                    ),
                    empty_result_policy=dict(
                        version.empty_result_policy_json or {}
                    ),
                    coverage_policy=dict(
                        version.coverage_policy_json or {}
                    ),
                    status=str(version.status),
                    digest=str(version.digest),
                    version_id=str(version.id),
                )
                for root, version in item_rows
            )
            check_item_ids_by_implementation: dict[str, list[str]] = {}
            for link in links:
                check_item_id = item_id_by_version.get(str(link.check_item_version_id))
                if check_item_id:
                    check_item_ids_by_implementation.setdefault(
                        str(link.implementation_version_id),
                        [],
                    ).append(check_item_id)
            implementations = tuple(
                CollectionImplementation(
                    id=str(root.id),
                    version=int(version.version),
                    name=str(root.name),
                    description=str(root.description or ""),
                    check_item_ids=tuple(
                        sorted(
                            check_item_ids_by_implementation.get(str(version.id), [])
                        )
                    ),
                    match=dict(version.match_json or {}),
                    commands=primary_collection_commands(
                        (
                            CollectionCommand(
                                command=str(row.get("command") or ""),
                                priority=int(row.get("priority") or 0),
                            )
                            for row in (version.commands_json or [])
                        ),
                        implementation_id=str(root.id),
                        version_id=str(version.id),
                    ),
                    collector=str(version.collector),
                    parser_requirement=str(version.parser_requirement),
                    template_scope=(
                        str(version.template_scope)
                        if version.template_scope is not None
                        else None
                    ),
                    adapter_spec=dict(version.adapter_spec_json or {}),
                    output_quality_policy=dict(
                        version.output_quality_policy_json or {}
                    ),
                    selection_priority=int(version.selection_priority),
                    status=str(version.status),
                    digest=str(version.digest),
                    version_id=str(version.id),
                )
                for root, version in implementation_rows
            )
            return CheckItemCatalog(
                catalog_version=max(
                    [
                        *(item.version for item in check_items),
                        *(
                            implementation.version
                            for implementation in implementations
                        ),
                    ],
                    default=1,
                ),
                check_items=check_items,
                implementations=implementations,
            )

    def get_implementation_version_context(
        self,
        version_id: str,
    ) -> tuple[CollectionImplementation, tuple[CheckItemDefinition, ...]] | None:
        """Load the immutable adapter and exactly linked check-item versions."""

        with self._session_scope() as db:
            version = db.get(CollectionImplementationVersionRecord, str(version_id))
            if version is None:
                return None
            root = db.get(CollectionImplementationRecord, version.implementation_id)
            if root is None:
                return None
            links = (
                db.query(CheckItemImplementationLinkRecord)
                .filter(
                    CheckItemImplementationLinkRecord.implementation_version_id
                    == str(version.id)
                )
                .order_by(CheckItemImplementationLinkRecord.priority.desc())
                .all()
            )
            # A draft of a check item links the adapters in force so it can be
            # tried, and a cancelled draft keeps those links. The adapter's
            # output still reads into the version it pins: only that one is
            # its context, or every version of the item would compete for it.
            pinned = {
                str(output.get("check_item_id") or ""): str(output.get("check_item_version_id") or "")
                for output in dict(version.adapter_spec_json or {}).get("outputs") or []
                if isinstance(output, dict) and output.get("check_item_version_id")
            }
            item_versions = [
                item_version
                for item_version in (
                    db.get(CheckItemVersionRecord, str(link.check_item_version_id))
                    for link in links
                )
                if item_version is None
                or pinned.get(str(item_version.check_item_id)) in (None, str(item_version.id))
            ]
            check_items: list[CheckItemDefinition] = []
            for item_version in item_versions:
                if item_version is None:
                    continue
                item_root = db.get(CheckItemRecord, item_version.check_item_id)
                if item_root is None:
                    continue
                check_items.append(
                    CheckItemDefinition(
                        id=str(item_root.id),
                        version=int(item_version.version),
                        name=str(item_root.name),
                        category=str(item_root.category),
                        description=str(item_root.description or ""),
                        result_mode=str(item_version.result_mode),
                        assessment_mode=str(item_version.assessment_mode),
                        canonical_contract=dict(
                            item_version.canonical_contract_json or {}
                        ),
                        report_projection=str(item_version.report_projection),
                        applicability=dict(item_version.applicability_json or {}),
                        freshness_policy=dict(
                            item_version.freshness_policy_json or {}
                        ),
                        empty_result_policy=dict(
                            item_version.empty_result_policy_json or {}
                        ),
                        coverage_policy=dict(
                            item_version.coverage_policy_json or {}
                        ),
                        status=str(item_version.status),
                        digest=str(item_version.digest),
                        version_id=str(item_version.id),
                    )
                )
            implementation = CollectionImplementation(
                id=str(root.id),
                version=int(version.version),
                name=str(root.name),
                description=str(root.description or ""),
                check_item_ids=tuple(item.id for item in check_items),
                match=dict(version.match_json or {}),
                commands=primary_collection_commands(
                    (
                        CollectionCommand(
                            command=str(row.get("command") or ""),
                            priority=int(row.get("priority") or 0),
                        )
                        for row in (version.commands_json or [])
                    ),
                    implementation_id=str(root.id),
                    version_id=str(version.id),
                ),
                collector=str(version.collector),
                parser_requirement=str(version.parser_requirement),
                template_scope=(
                    str(version.template_scope)
                    if version.template_scope is not None
                    else None
                ),
                adapter_spec=dict(version.adapter_spec_json or {}),
                output_quality_policy=dict(
                    version.output_quality_policy_json or {}
                ),
                selection_priority=int(version.selection_priority),
                status=str(version.status),
                digest=str(version.digest),
                version_id=str(version.id),
            )
            return implementation, tuple(check_items)
