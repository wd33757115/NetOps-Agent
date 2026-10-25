# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""Import offline raw patrol captures into the snapshot store."""

from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path
from typing import Any

from src.core.check_items.catalog import CheckItemCatalogRepository
from src.core.check_items.command_adapter import is_direct_projection_adapter
from src.core.check_items.resolver import CheckItemImplementationResolver
from src.apps.rule_governance.runtime import DeviceIdentityResolver
from src.core.parser.runtime import ParserRuntime, ParserRuntimeResult
from src.core.patrol.command_splitter import (
    canonicalize_command,
    infer_device_from_filename,
    inspect_raw_capture,
    split_cli_capture,
)
from src.core.patrol.modes import (
    PARSER_VALIDATION_MODE,
    PRODUCTION_MODE,
    normalize_patrol_mode,
)
from src.core.patrol.statuses import derive_command_status, normalize_execution_status
from src.infrastructure.db.postgres import require_postgres_connection


def _non_repairable_output(raw_text: str) -> bool:
    text = (raw_text or "").lower()
    return len(text.strip()) < 20 or any(
        marker in text
        for marker in (
            "connection timed out",
            "connection reset",
            "command execution failed",
            "ssh connection failed",
            "output truncated",
            "invalid command at",
            "unrecognized command found",
            "incomplete command found",
        )
    )


def _read_text_best_effort(path: Path) -> str:
    data = path.read_bytes()
    for encoding in ("utf-8-sig", "utf-8", "utf-16", "utf-16-le", "utf-16-be", "gb18030", "gbk"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _dataset_id(path: Path, metadata: dict[str, Any] | None) -> str:
    explicit = str((metadata or {}).get("dataset_id") or "").strip()
    if explicit:
        return explicit
    source = path if path.is_dir() else path.parent
    label = source.name or "parser-validation"
    digest = hashlib.sha256(str(source.resolve()).encode("utf-8")).hexdigest()[:10]
    return f"{label}-{digest}"


def _resolve_capture_device(
    *,
    path: Path,
    text: str,
    explicit_device_name: str | None = None,
    explicit_ip: str | None = None,
) -> tuple[str, str | None, Any]:
    inferred_name, inferred_ip = infer_device_from_filename(path)
    inspected = inspect_raw_capture(text)
    ip = explicit_ip or inferred_ip
    # Field-export filenames usually carry the inventory name and management IP.
    # Device prompts can be hostnames, aliases, or stale names, so use them only
    # when the file name does not provide a stronger asset identity.
    device_name = (
        explicit_device_name
        or (inferred_name if inferred_name and inferred_ip else None)
        or inspected.device_name
        or inferred_name
        or path.stem
    )
    return device_name, ip, inspected


def _import_fingerprint(
    *,
    mode: str,
    dataset_id: str,
    source_file_hash: str,
    device_id: str,
    canonical_command: str,
    raw_text: str,
    run_id: str | None = None,
    inventory_device_id: str | None = None,
    command_observed_at: str | None = None,
    command_ordinal: int | None = None,
    execution_item_id: str | None = None,
    fact_version: str = "v2",
) -> str:
    raw_hash = hashlib.sha256((raw_text or "").encode("utf-8")).hexdigest()
    if run_id:
        encoded = json.dumps(
            [
                f"command-fact-{fact_version}",
                mode,
                run_id,
                inventory_device_id or device_id,
                *(
                    [execution_item_id or {"missing": True, "ordinal": command_ordinal}]
                    if fact_version == "v2"
                    else []
                ),
                canonical_command.strip().lower(),
                command_observed_at or {"missing": True, "ordinal": command_ordinal},
                raw_hash,
            ],
            ensure_ascii=False,
            separators=(",", ":"),
            default=str,
        )
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    value = "|".join(
        [
            "v2",
            mode,
            dataset_id,
            source_file_hash,
            device_id,
            canonical_command.strip().lower(),
            raw_hash,
        ]
    )
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def build_output_review_item_id(
    *,
    period_role: str,
    source_relative_path: str,
    device_id: str,
    canonical_command: str,
    start_line: int | None,
    end_line: int | None,
    raw_text_hash: str,
) -> str:
    """Build a stable ID for a job-local output disposition decision."""

    payload = json.dumps(
        {
            "version": 1,
            "period_role": str(period_role or "").strip().lower(),
            "source_relative_path": str(source_relative_path or "")
            .replace("\\", "/")
            .strip("/"),
            "device_id": str(device_id or "").strip(),
            "canonical_command": " ".join(
                str(canonical_command or "").strip().lower().split()
            ),
            "start_line": int(start_line or 0),
            "end_line": int(end_line or 0),
            "raw_text_hash": str(raw_text_hash or "").strip().lower(),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return f"output-review-{digest[:32]}"


def _confirmed_output_disposition(
    *,
    metadata: dict[str, Any] | None,
    device_id: str,
    canonical_command: str,
    start_line: int | None,
    end_line: int | None,
    raw_text: str,
) -> dict[str, Any] | None:
    if (raw_text or "").strip():
        return None
    period_role = str((metadata or {}).get("period_role") or "").strip()
    source_relative_path = str(
        (metadata or {}).get("source_relative_path") or ""
    ).strip()
    if not period_role or not source_relative_path:
        return None
    review_item_id = build_output_review_item_id(
        period_role=period_role,
        source_relative_path=source_relative_path,
        device_id=device_id,
        canonical_command=canonical_command,
        start_line=start_line,
        end_line=end_line,
        raw_text_hash=hashlib.sha256((raw_text or "").encode("utf-8")).hexdigest(),
    )
    for raw in (metadata or {}).get("output_dispositions") or []:
        item = dict(raw) if isinstance(raw, dict) else {}
        if (
            str(item.get("review_item_id") or "") == review_item_id
            and str(item.get("decision") or "") == "ignore_valid_empty"
        ):
            return item
    return None


def _preview_parse_status(parsed: ParserRuntimeResult) -> str:
    """Project runtime outcomes onto the user-facing preview buckets."""
    if parsed.policy_status == "raw":
        return "raw_compare"
    if parsed.policy_status == "ignored":
        return "ignored"
    if parsed.failure_code == "unsupported_command":
        return "ignored"
    if parsed.identity_status != "known":
        return "identity_unknown"
    if parsed.parser_status in {"success", "empty"}:
        return "ready"
    if parsed.failure_code == "template_missing":
        return "template_missing"
    if parsed.failure_code in {
        "empty_unverified",
        "command_error",
        "truncated",
        "paged",
    }:
        return str(parsed.failure_code)
    return "parse_failed"


def _check_item_binding_metadata(
    command_collection: dict[str, Any],
) -> dict[str, Any]:
    all_bindings = [
        dict(item)
        for item in command_collection.get("check_item_bindings") or []
        if isinstance(item, dict)
    ]
    selected_bindings = [
        dict(item)
        for item in command_collection.get("selected_for_bindings") or []
        if isinstance(item, dict)
    ]
    effective = selected_bindings or all_bindings
    implementation_versions = sorted(
        {
            str(item.get("implementation_version_id"))
            for item in effective
            if item.get("implementation_version_id")
        }
    )
    contract_ref_by_id: dict[str, dict[str, str]] = {}
    for binding in effective:
        for raw_ref in binding.get("observation_contract_refs") or []:
            if not isinstance(raw_ref, dict):
                continue
            contract_id = str(raw_ref.get("contract_id") or "").strip()
            schema_id = str(raw_ref.get("schema_id") or "").strip()
            digest = str(raw_ref.get("digest") or "").strip()
            if not contract_id or not schema_id or len(digest) != 64:
                continue
            ref = {
                "contract_id": contract_id,
                "schema_id": schema_id,
                "digest": digest,
            }
            existing = contract_ref_by_id.get(contract_id)
            if existing is not None and existing != ref:
                raise ValueError(
                    "runtime bindings contain conflicting ObservationContract refs"
                )
            contract_ref_by_id[contract_id] = ref
    return {
        "collection_action_id": (
            str(command_collection.get("collection_item_id"))
            if command_collection.get("collection_item_id")
            else None
        ),
        "implementation_version_id": (
            implementation_versions[0]
            if len(implementation_versions) == 1
            else None
        ),
        "check_item_ids": sorted(
            {
                str(item.get("check_item_id"))
                for item in effective
                if item.get("check_item_id")
            }
        ),
        "check_item_version_ids": sorted(
            {
                str(item.get("check_item_version_id"))
                for item in effective
                if item.get("check_item_version_id")
            }
        ),
        "runtime_bundle_version_ids": sorted(
            {
                str(item.get("runtime_bundle_version_id"))
                for item in effective
                if item.get("runtime_bundle_version_id")
            }
        ),
        "observation_contract_refs": sorted(
            contract_ref_by_id.values(),
            key=lambda item: (item["schema_id"], item["contract_id"]),
        ),
        "all_bindings": all_bindings,
        "selected_bindings": selected_bindings,
    }


def _resolve_offline_direct_projection_bindings(
    *,
    identity: Any,
    device_id: str,
    commands: list[str],
) -> list[dict[str, Any]]:
    """Attach unambiguous active V2 routes to an imported offline capture.

    Offline captures have no inventory execution plan, so they previously
    arrived without an implementation version and the normalizer fell back to
    the retired ObservationContract bridge.  Resolve only the already-active,
    direct A→B routes from the identified vendor/model.  An unknown identity,
    ambiguous command, missing implementation, or legacy adapter deliberately
    remains unbound instead of being guessed.
    """

    vendor = str(getattr(identity, "vendor", "") or "").strip()
    model = str(getattr(identity, "model", "") or "").strip()
    if not vendor or not model:
        return []
    try:
        catalog = CheckItemCatalogRepository().load_active()
        resolver = CheckItemImplementationResolver(catalog)
        projected = resolver.project_legacy_commands(
            device={
                "device_id": device_id,
                "vendor": vendor,
                "model": model,
                "family": str(getattr(identity, "family", "") or "").strip(),
            },
            commands=commands,
        )
    except Exception:
        # A capture import must retain its raw evidence even if the catalog is
        # temporarily unavailable.  The normal quality gate reports asset
        # readiness before a production workflow starts.
        return []

    decisions = {
        str(item.get("check_item_id") or ""): item
        for item in projected.get("decisions") or []
        if isinstance(item, dict)
        and item.get("projection_status") == "mapped"
        and item.get("implementation_version_id")
    }
    implementations = {
        item.version_id: item for item in catalog.implementations
    }
    bindings: list[dict[str, Any]] = []
    for action in projected.get("collection_actions") or []:
        if not isinstance(action, dict):
            continue
        implementation_ids = []
        for item in action.get("implementation_version_ids") or []:
            implementation_version_id = str(item)
            implementation = implementations.get(implementation_version_id)
            if implementation is None:
                continue
            if is_direct_projection_adapter(dict(implementation.adapter_spec or {})):
                implementation_ids.append(implementation_version_id)
        # A snapshot has one implementation-version identity.  Do not choose
        # between multiple direct routes for a shared command.
        if len(implementation_ids) != 1:
            continue
        implementation_version_id = implementation_ids[0]
        action_bindings = [
            {
                "check_item_id": check_item_id,
                "check_item_version_id": str(
                    decisions[check_item_id].get("check_item_version_id") or ""
                ),
                "implementation_version_id": implementation_version_id,
            }
            for check_item_id in action.get("check_item_ids") or []
            if check_item_id in decisions
            and str(decisions[check_item_id].get("implementation_version_id") or "")
            == implementation_version_id
        ]
        if not action_bindings:
            continue
        action_key = "{}:{}".format(
            implementation_version_id,
            str(action.get("canonical_command") or "").strip(),
        )
        action_uuid = uuid.uuid5(uuid.NAMESPACE_URL, action_key)
        action_id = f"offline-direct:{action_uuid}"
        bindings.append(
            {
                # Keep the original capture spelling because status queues are
                # keyed by the command block, not by a parser-policy alias.
                "command": str(action.get("command") or "").strip(),
                # The persistence column is VARCHAR(64); keep the provenance
                # deterministic without embedding an arbitrary-length CLI.
                "collection_item_id": action_id,
                "check_item_bindings": action_bindings,
                "selected_for_bindings": action_bindings,
            }
        )
    return bindings


def _attach_command_analysis_routes(
    status_items: list[dict[str, Any]], *, identity: Any,
    device_id: str, commands: list[str],
) -> list[dict[str, Any]]:
    # Explicit command-set runs already carry collection statuses, but do not
    # have check-item-driven collection bindings. Attach only existing active
    # analysis routes; keep every original status, command and raw output.
    if status_items and not any(item.get("check_item_bindings") for item in status_items):
        routes = _resolve_offline_direct_projection_bindings(
            identity=identity,
            device_id=device_id,
            commands=commands,
        )
        by_command = {canonicalize_command(item["command"]): item for item in routes}
        status_items = [dict(item) for item in status_items]
        for item in status_items:
            route = by_command.get(canonicalize_command(str(item.get("command") or "")))
            if route:
                item["check_item_bindings"] = route["check_item_bindings"]
                item["selected_for_bindings"] = route["selected_for_bindings"]
    return status_items


def _align_collection_statuses(blocks, status_items):
    """Use the splitter's identity for both raw commands and their analysis bindings."""
    queues: dict[str, list[dict[str, Any]]] = {}
    for item in status_items:
        key = canonicalize_command(str(item.get("command") or ""))
        queues.setdefault(key, []).append(item)
    return [(queues.get(block.command_canonical) or [{}]).pop(0) for block in blocks]


def _parse_capture_blocks(
    *,
    runtime: ParserRuntime,
    identity: Any,
    blocks: list[Any],
) -> list[tuple[Any, ParserRuntimeResult]]:
    parsed_blocks: list[tuple[Any, ParserRuntimeResult]] = []
    for block in blocks:
        try:
            parsed = runtime.parse(
                identity=identity,
                command=block.command_canonical,
                raw_text=block.raw_output,
            )
        except Exception as exc:
            parsed = ParserRuntimeResult(
                policy_status="eligible",
                identity_status=identity.status,
                parser_status="failed",
                canonical_command=block.command_canonical,
                handling="structured",
                failure_code=f"parser_exception:{type(exc).__name__}",
                identity_evidence={
                    "confidence": identity.confidence,
                    "source": identity.source,
                    "command": identity.evidence_command,
                    "text": identity.evidence_text,
                },
            )
        parsed_blocks.append((block, parsed))
    return parsed_blocks


def _project_parser_result(
    parsed: ParserRuntimeResult,
) -> tuple[str, Any, str | None, str | None]:
    parse_status = "raw_only"
    structured = None
    if (
        parsed.handling == "raw_hash_diff"
        and parsed.parser_status == "not_required"
    ):
        parse_status = "raw_hashed"
        # Text-comparison commands have no Parser records.  The snapshot's
        # raw_text_hash column is the authoritative digest.
        structured = None
    elif parsed.policy_status == "ignored":
        parse_status = "ignored"
    elif parsed.failure_code == "unsupported_command":
        parse_status = "not_applicable"
    elif parsed.failure_code in {
        "empty_unverified",
        "command_error",
        "truncated",
        "paged",
    }:
        parse_status = str(parsed.failure_code)
    elif parsed.identity_status != "known":
        parse_status = "identity_unknown"
    elif parsed.parser_status == "unavailable":
        parse_status = "template_missing"
    elif parsed.parser_status == "empty":
        parse_status = "parse_empty"
        structured = []
    elif parsed.parser_status == "success":
        parse_status = "parsed"
        structured = parsed.structured_data
    else:
        parse_status = "parse_failed"
    return (
        parse_status,
        structured,
        parsed.template_id,
        parsed.template_version,
    )


def preview_raw_path(
    *,
    file_path: str | Path,
    mode: str = PARSER_VALIDATION_MODE,
    vendor: str | None = None,
    model: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Analyze an import without writing snapshots or parser jobs."""
    require_postgres_connection()
    mode = normalize_patrol_mode(mode)
    path = Path(file_path)
    if path.is_file():
        files = [path]
    elif path.is_dir():
        files = sorted(
            item
            for item in path.rglob("*")
            if item.is_file() and item.suffix.lower() in {".txt", ".log"}
        )
    else:
        raise FileNotFoundError(f"patrol source path does not exist: {path}")
    if not files:
        raise ValueError(f"no txt/log patrol files found under: {path}")

    runtime = ParserRuntime()
    from src.core.patrol.repository import PatrolSnapshotRepository

    snapshot_repository = PatrolSnapshotRepository()
    dataset_id = _dataset_id(path, metadata)
    totals = {
        "ready": 0,
        "template_missing": 0,
        "parse_failed": 0,
        "ignored": 0,
        "raw_compare": 0,
        "identity_unknown": 0,
        "empty_unverified": 0,
        "command_error": 0,
        "truncated": 0,
        "paged": 0,
        "duplicate_snapshots": 0,
        "new_snapshots": 0,
    }
    devices: set[str] = set()
    commands: set[str] = set()
    file_results: list[dict[str, Any]] = []
    existing_fingerprints = snapshot_repository.list_import_fingerprints(mode=mode)
    for candidate in files:
        text = _read_text_best_effort(candidate)
        file_hash = hashlib.sha256(candidate.read_bytes()).hexdigest()
        relative_path = (
            candidate.name
            if path.is_file()
            else candidate.relative_to(path).as_posix()
        )
        resolved_name, resolved_ip, _inspected = _resolve_capture_device(
            path=candidate,
            text=text,
        )
        device_id = f"{resolved_name}-{resolved_ip}" if resolved_ip else resolved_name
        blocks = split_cli_capture(text, device_name=resolved_name)
        identity = DeviceIdentityResolver().resolve(
            blocks=blocks,
            path=candidate,
            vendor=vendor,
            model=model,
        )
        devices.add(device_id)
        statuses: dict[str, int] = {}
        command_results: list[dict[str, Any]] = []
        for block in blocks:
            commands.add(block.command_canonical)
            fingerprint = _import_fingerprint(
                mode=mode,
                dataset_id=dataset_id,
                source_file_hash=file_hash,
                device_id=device_id,
                canonical_command=block.command_canonical,
                raw_text=block.raw_output,
            )
            if fingerprint in existing_fingerprints:
                totals["duplicate_snapshots"] += 1
                statuses["duplicate"] = statuses.get("duplicate", 0) + 1
                command_results.append(
                    {
                        "command": block.command_canonical,
                        "status": "duplicate",
                        "template_id": None,
                        "failure_code": None,
                    }
                )
                continue
            totals["new_snapshots"] += 1
            parsed = runtime.parse(
                identity=identity,
                command=block.command_canonical,
                raw_text=block.raw_output,
            )
            status = _preview_parse_status(parsed)
            totals[status] = totals.get(status, 0) + 1
            statuses[status] = statuses.get(status, 0) + 1
            raw_text_hash = hashlib.sha256(
                (block.raw_output or "").encode("utf-8")
            ).hexdigest()
            review_item_id = None
            if status == "empty_unverified":
                review_item_id = build_output_review_item_id(
                    period_role=str((metadata or {}).get("period_role") or ""),
                    source_relative_path=relative_path,
                    device_id=device_id,
                    canonical_command=block.command_canonical,
                    start_line=block.start_line,
                    end_line=block.end_line,
                    raw_text_hash=raw_text_hash,
                )
            command_results.append(
                {
                    "command": block.command_canonical,
                    "status": status,
                    "review_item_id": review_item_id,
                    "start_line": block.start_line,
                    "end_line": block.end_line,
                    "raw_text_hash": raw_text_hash,
                    "raw_text_length": len(block.raw_output or ""),
                    "template_id": parsed.template_id,
                    "template_version": parsed.template_version,
                    "failure_code": parsed.failure_code,
                    "handling": parsed.handling,
                    "comparison_profile": parsed.comparison_profile,
                    "comparison_profile_version": parsed.comparison_profile_version,
                    "record_count": (
                        len(parsed.structured_data)
                        if isinstance(parsed.structured_data, list)
                        else 1
                        if isinstance(parsed.structured_data, dict)
                        else 0
                    ),
                }
            )
        file_results.append(
            {
                "file_path": str(candidate),
                "relative_path": relative_path,
                "device_id": device_id,
                "device_name": resolved_name,
                "management_ip": resolved_ip,
                "vendor": identity.vendor,
                "model": identity.model,
                "identity_status": identity.status,
                "command_count": len(blocks),
                "statuses": statuses,
                "commands": command_results,
            }
        )
    return {
        "success": True,
        "preview": True,
        "mode": mode,
        "dataset_id": dataset_id,
        "source_path": str(path),
        "file_count": len(files),
        "device_count": len(devices),
        "command_count": sum(item["command_count"] for item in file_results),
        "unique_commands": len(commands),
        **totals,
        "files": file_results,
    }


def import_raw_capture(
    *,
    file_path: str | Path,
    db_path: str | Path | None = None,
    mode: str = PRODUCTION_MODE,
    run_id: str | None = None,
    device_name: str | None = None,
    ip: str | None = None,
    vendor: str | None = None,
    model: str | None = None,
    observed_at: str | None = None,
    metadata: dict[str, Any] | None = None,
    run_owner_token: str | None = None,
    inventory_device_id: str | None = None,
    collector_complete: bool | None = None,
    command_collection_statuses: (dict[str, dict[str, Any]] | list[dict[str, Any]] | None) = None,
    parser_runtime_bundle: dict[str, Any] | None = None,
    finalize_run: bool = True,
) -> dict[str, Any]:
    require_postgres_connection()
    mode = normalize_patrol_mode(mode)
    path = Path(file_path)
    text = _read_text_best_effort(path)
    source_file_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    device_name, ip, inspected = _resolve_capture_device(
        path=path,
        text=text,
        explicit_device_name=device_name,
        explicit_ip=ip,
    )
    device_id = f"{device_name}-{ip}" if ip else device_name
    blocks = split_cli_capture(text, device_name=device_name)
    identity = DeviceIdentityResolver().resolve(
        blocks=blocks,
        path=path,
        vendor=vendor,
        model=model,
    )
    vendor = identity.vendor
    model = identity.model

    runtime_bundle = None
    if parser_runtime_bundle:
        from src.apps.rule_governance.runtime import ParserRuntimeBundle

        runtime_bundle = ParserRuntimeBundle.from_dict(parser_runtime_bundle)
    runtime = ParserRuntime(runtime_bundle=runtime_bundle)
    block_output_dispositions = [
        _confirmed_output_disposition(
            metadata=metadata,
            device_id=device_id,
            canonical_command=block.command_canonical,
            start_line=block.start_line,
            end_line=block.end_line,
            raw_text=block.raw_output,
        )
        for block in blocks
    ]
    coverage = [
        runtime.check_coverage(
            identity=identity,
            command=block.command_canonical,
        )
        for block in blocks
    ]

    from src.core.patrol.repository import (
        PatrolCollectionResultRepository,
        PatrolSnapshotRepository,
    )
    from src.core.patrol.run_repository import PatrolRunRepository

    run_repository = PatrolRunRepository()
    collection_repository = PatrolCollectionResultRepository()
    snapshot_repository = PatrolSnapshotRepository()
    dataset_id = _dataset_id(path, metadata)
    if command_collection_statuses is None:
        command_collection_statuses = _resolve_offline_direct_projection_bindings(
            identity=identity,
            device_id=device_id,
            commands=[block.command for block in blocks],
        )
    if isinstance(command_collection_statuses, dict):
        status_items = [
            {"command": command, **dict(result or {})}
            for command, result in command_collection_statuses.items()
        ]
    else:
        status_items = [dict(item) for item in command_collection_statuses or []]
    status_items = _attach_command_analysis_routes(
        status_items, identity=identity, device_id=device_id,
        commands=[block.command for block in blocks],
    )
    block_collection_statuses = _align_collection_statuses(blocks, status_items)
    command_observed_ats = [
        (command_collection.get("observed_at") or observed_at or inspected.observed_at_text)
        for block, command_collection in zip(
            blocks, block_collection_statuses, strict=True
        )
    ]
    fingerprints = [
        _import_fingerprint(
            mode=mode,
            dataset_id=dataset_id,
            source_file_hash=source_file_hash,
            device_id=device_id,
            canonical_command=block.command_canonical,
            raw_text=block.raw_output,
            run_id=run_id,
            inventory_device_id=inventory_device_id,
            command_observed_at=command_observed_at,
            command_ordinal=block.start_line,
            execution_item_id=command_collection.get("execution_item_id"),
        )
        for block, command_observed_at, command_collection in zip(
            blocks,
            command_observed_ats,
            block_collection_statuses,
            strict=True,
        )
    ]
    legacy_fingerprints = [
        (
            None
            if command_collection.get("execution_item_id")
            else _import_fingerprint(
                mode=mode,
                dataset_id=dataset_id,
                source_file_hash=source_file_hash,
                device_id=device_id,
                canonical_command=block.command_canonical,
                raw_text=block.raw_output,
                run_id=run_id,
                inventory_device_id=inventory_device_id,
                command_observed_at=command_observed_at,
                command_ordinal=block.start_line,
                fact_version="v1",
            )
        )
        for block, command_observed_at, command_collection in zip(
            blocks,
            command_observed_ats,
            block_collection_statuses,
            strict=True,
        )
    ]
    existing = snapshot_repository.find_snapshots_by_import_fingerprints(
        {fingerprint for fingerprint in [*fingerprints, *legacy_fingerprints] if fingerprint}
    )
    fingerprints = [
        legacy if legacy and legacy in existing else current
        for current, legacy in zip(fingerprints, legacy_fingerprints, strict=True)
    ]
    all_preexisting = bool(
        fingerprints
        and all(fingerprint in existing for fingerprint in fingerprints)
    )
    import_source = str((metadata or {}).get("source") or "imported_file")
    run_repository.ensure_dataset(
        dataset_id=dataset_id,
        name=str((metadata or {}).get("dataset_name") or dataset_id),
        mode=mode,
        source=import_source,
        metadata={
            "source_path": str(path.parent),
            **(metadata or {}),
        },
    )
    rid = run_repository.create_run(
        run_id=run_id,
        source=import_source,
        mode=mode,
        dataset_id=dataset_id,
        metadata={
            "file_path": str(path),
            "prompt_style": inspected.prompt_style,
            "patrol_mode": mode,
            "dataset_id": dataset_id,
            "parser_runtime_bundle_digest": (
                runtime_bundle.digest if runtime_bundle is not None else None
            ),
            **(metadata or {}),
        },
        started_at=observed_at,
        run_purpose=str(
            (metadata or {}).get("run_purpose") or "scheduled_patrol"
        ),
        owner_token=run_owner_token,
    )
    output_facts = [
        runtime.classify_output(
            identity=identity,
            command=block.command_canonical,
            raw_text=block.raw_output,
        )
        for block in blocks
    ]
    persisted_snapshots: list[tuple[Any, bool]] = []
    qualification_facts_by_execution: dict[
        str,
        list[dict[str, Any]],
    ] = {}
    patrol_batch_id = str(
        (metadata or {}).get("patrol_batch_id") or ""
    ).strip()
    for (
        block,
        output_fact,
        fingerprint,
        command_observed_at,
        command_collection,
        output_disposition,
    ) in zip(
        blocks,
        output_facts,
        fingerprints,
        command_observed_ats,
        block_collection_statuses,
        block_output_dispositions,
        strict=True,
    ):
        binding_metadata = _check_item_binding_metadata(
            command_collection
        )
        deterministic_snapshot_id = (
            str(uuid.uuid5(uuid.NAMESPACE_URL, fingerprint))
            if run_id
            else None
        )
        snapshot_id, snapshot_created = (
            snapshot_repository.save_snapshot_idempotent(
                run_id=rid,
                device_id=device_id,
                device_name=device_name,
                ip=ip,
                vendor=vendor,
                model=model,
                command=block.command,
                command_canonical=block.command_canonical,
                raw_text=block.raw_output,
                observed_at=command_observed_at,
                structured_json=None,
                parser_name=None,
                parser_version=None,
                parse_status="not_attempted",
                output_status=str(
                    output_fact.get("output_status")
                    or "not_classified"
                ),
                dataset_id=dataset_id,
                source_file_hash=source_file_hash,
                import_fingerprint=fingerprint,
                snapshot_id=deterministic_snapshot_id,
                start_line=block.start_line,
                end_line=block.end_line,
                metadata={
                    "patrol_mode": mode,
                    "source_path": str(path),
                    "collector_complete": collector_complete,
                    "execution_item_id": command_collection.get(
                        "execution_item_id"
                    ),
                    "collection_item_id": command_collection.get(
                        "collection_item_id"
                    ),
                    "command_ordinal": command_collection.get(
                        "command_ordinal"
                    ),
                    "handling": output_fact.get("handling"),
                    "comparison_profile": output_fact.get(
                        "comparison_profile"
                    ),
                    "comparison_profile_version": output_fact.get(
                        "comparison_profile_version"
                    ),
                    "output_status": output_fact.get("output_status"),
                    "output_reason": output_fact.get("output_reason"),
                    "capability": output_fact.get("capability"),
                    "capability_status": output_fact.get(
                        "capability_status"
                    ),
                    "operator_output_disposition": output_disposition,
                    "parser_runtime_bundle_digest": (
                        runtime_bundle.digest
                        if runtime_bundle is not None
                        else None
                    ),
                    "check_item_version_ids": binding_metadata[
                        "check_item_version_ids"
                    ],
                    "runtime_bundle_version_ids": binding_metadata[
                        "runtime_bundle_version_ids"
                    ],
                    "observation_contract_refs": binding_metadata[
                        "observation_contract_refs"
                    ],
                    "check_item_bindings": binding_metadata[
                        "all_bindings"
                    ],
                    "selected_check_item_bindings": binding_metadata[
                        "selected_bindings"
                    ],
                },
                inventory_device_id=inventory_device_id,
                execution_item_id=command_collection.get(
                    "execution_item_id"
                ),
                collection_action_id=binding_metadata[
                    "collection_action_id"
                ],
                implementation_version_id=binding_metadata[
                    "implementation_version_id"
                ],
                check_item_ids=binding_metadata["check_item_ids"],
                owner_token=run_owner_token,
            )
        )
        stored_snapshot = snapshot_repository.get_snapshot(snapshot_id)
        if (
            stored_snapshot is None
            or not stored_snapshot.execution_item_id
        ):
            raise RuntimeError(
                f"persisted snapshot identity is unavailable: {snapshot_id}"
            )
        persisted_snapshots.append((stored_snapshot, snapshot_created))
        if (
            mode == PRODUCTION_MODE
            and patrol_batch_id
            and inventory_device_id
            and binding_metadata["selected_bindings"]
        ):
            for binding in binding_metadata["selected_bindings"]:
                check_item_version_id = str(
                    binding.get("check_item_version_id") or ""
                )
                if not check_item_version_id:
                    continue
                execution_id = str(
                    uuid.uuid5(
                        uuid.NAMESPACE_URL,
                        f"netops/check-execution/{patrol_batch_id}/"
                        f"{inventory_device_id}/{check_item_version_id}",
                    )
                )
                qualification_facts_by_execution.setdefault(
                    execution_id,
                    [],
                ).append(
                    {
                        "id": stored_snapshot.snapshot_id,
                        "device_id": inventory_device_id,
                        "source_device_id": inventory_device_id,
                        "inventory_device_id": inventory_device_id,
                        "output_status": stored_snapshot.output_status,
                        "observed_at": stored_snapshot.observed_at,
                    }
                )

    if collector_complete is False:
        provisional_ids: list[str] = []
        provisional_duplicates: list[str] = []
        for (
            block,
            (snapshot, snapshot_created),
            command_collection,
        ) in zip(
            blocks,
            persisted_snapshots,
            block_collection_statuses,
            strict=True,
        ):
            provisional_ids.append(snapshot.snapshot_id)
            if not snapshot_created:
                provisional_duplicates.append(snapshot.snapshot_id)
            execution_status = normalize_execution_status(
                command_collection.get("status") or "failed"
            )
            parse_status = str(
                snapshot.parse_status or "not_attempted"
            )
            command_status = derive_command_status(
                execution_status=execution_status,
                parse_status=parse_status,
            )
            binding_metadata = _check_item_binding_metadata(
                command_collection
            )
            collection_repository.save_collection_result(
                run_id=str(snapshot.run_id),
                inventory_device_id=inventory_device_id,
                device_id=str(inventory_device_id or snapshot.device_id),
                canonical_command=block.command_canonical,
                status=command_status.execution_status,
                execution_status=command_status.execution_status,
                parse_status=command_status.parse_status,
                output_status=str(snapshot.output_status),
                fact_status=command_status.fact_status,
                normalization_status=(
                    command_status.normalization_status
                ),
                authoritative_for_deletion=False,
                baseline_eligible=False,
                observed_entity_count=None,
                failure_code=(
                    None
                    if execution_status == "complete"
                    else execution_status
                ),
                observed_at=(
                    command_collection.get("observed_at")
                    or snapshot.observed_at
                ),
                execution_item_id=snapshot.execution_item_id,
                collection_action_id=binding_metadata[
                    "collection_action_id"
                ],
                metadata={
                    "parse_status": parse_status,
                    "execution_status": (
                        command_status.execution_status
                    ),
                    "fact_status": command_status.fact_status,
                    "normalization_status": (
                        command_status.normalization_status
                    ),
                    "snapshot_id": snapshot.snapshot_id,
                    "collector_complete": False,
                    "parser_deferred": True,
                    "collection_item_id": command_collection.get(
                        "collection_item_id"
                    ),
                    "command_ordinal": command_collection.get(
                        "command_ordinal"
                    ),
                    "check_item_version_ids": binding_metadata[
                        "check_item_version_ids"
                    ],
                    "runtime_bundle_version_ids": binding_metadata[
                        "runtime_bundle_version_ids"
                    ],
                    "observation_contract_refs": binding_metadata[
                        "observation_contract_refs"
                    ],
                    "selected_check_item_bindings": (
                        binding_metadata["selected_bindings"]
                    ),
                },
                owner_token=run_owner_token,
            )
        provisional_blocking = [
            item
            for item in coverage
            if item["policy_status"] == "eligible"
            and item["coverage_status"] not in {"ready", "not_applicable"}
        ]
        return {
            "success": True,
            "run_id": rid,
            "device_id": device_id,
            "device_name": device_name,
            "ip": ip,
            "command_count": len(blocks),
            "snapshot_ids": provisional_ids,
            "new_snapshot_count": (
                len(provisional_ids) - len(provisional_duplicates)
            ),
            "duplicate_snapshot_count": len(
                provisional_duplicates
            ),
            "duplicate_snapshot_ids": provisional_duplicates,
            "commands": [
                block.command_canonical for block in blocks
            ],
            "vendor": vendor,
            "model": model,
            "family": identity.family,
            "identity_status": identity.status,
            "mode": mode,
            "dataset_id": dataset_id,
            "storage": "postgresql",
            "legacy_db_path_ignored": (
                str(db_path) if db_path else None
            ),
            "coverage": coverage,
            "coverage_violations": provisional_blocking,
            "parse_violations": [],
            "production_parser_ready": False,
            "collection_status": "running",
            "parser_readiness_status": "pending",
            "fact_readiness_status": "pending",
            "parser_results": [
                {
                    "snapshot_id": snapshot.snapshot_id,
                    "command": snapshot.command_canonical,
                    "parse_status": snapshot.parse_status,
                    "candidate_job_id": None,
                    "parser_deferred": True,
                }
                for snapshot, _created in persisted_snapshots
            ],
        }

    if qualification_facts_by_execution:
        from src.core.check_items.execution import (
            CheckItemExecutionService,
        )

        execution_service = CheckItemExecutionService()
        for execution_id, raw_facts in sorted(
            qualification_facts_by_execution.items()
        ):
            facts_by_snapshot_id = {
                str(item["id"]): item for item in raw_facts
            }
            frozen_facts = tuple(
                facts_by_snapshot_id[snapshot_id]
                for snapshot_id in sorted(facts_by_snapshot_id)
            )
            execution_service.qualify_evidence(
                execution_id,
                snapshot_facts=frozen_facts,
                evidence_refs=tuple(
                    str(item["id"]) for item in frozen_facts
                ),
            )

    parsed_blocks = _parse_capture_blocks(
        runtime=runtime,
        identity=identity,
        blocks=blocks,
    )
    for coverage_item, (_, parsed), output_disposition in zip(
        coverage,
        parsed_blocks,
        block_output_dispositions,
        strict=True,
    ):
        if output_disposition:
            coverage_item["coverage_status"] = "not_applicable"
            coverage_item["failure_code"] = None
            coverage_item["output_disposition"] = "ignore_valid_empty"
        elif parsed.parser_status == "empty":
            coverage_item["coverage_status"] = "not_required_for_empty"
            coverage_item["failure_code"] = None
        elif parsed.failure_code == "unsupported_command":
            coverage_item["coverage_status"] = "not_applicable"
            coverage_item["failure_code"] = "unsupported_command"
        elif parsed.policy_status == "raw":
            coverage_item["coverage_status"] = "ready"
            coverage_item["failure_code"] = None
    blocking = [
        item
        for item in coverage
        if item["policy_status"] == "eligible"
        and item["coverage_status"]
        not in {"ready", "not_required_for_empty", "not_applicable"}
    ]
    parse_violations = [
        {
            "canonical_command": parsed.canonical_command,
            "parser_status": parsed.parser_status,
            "failure_code": parsed.failure_code,
            "template_id": parsed.template_id,
            "template_version": parsed.template_version,
        }
        for (_, parsed), output_disposition in zip(
            parsed_blocks,
            block_output_dispositions,
            strict=True,
        )
        if parsed.policy_status == "eligible"
        and parsed.parser_status not in {"success", "empty"}
        and parsed.failure_code != "unsupported_command"
        and not output_disposition
    ]
    if all_preexisting and all(
        snapshot.parse_status != "not_attempted"
        for snapshot, _created in persisted_snapshots
    ):
        existing_snapshots = [
            snapshot for snapshot, _created in persisted_snapshots
        ]
        # Streaming collection imports the growing capture repeatedly. The final
        # pass must reconcile command statuses even though the immutable snapshots
        # already exist; otherwise provisional failures remain stuck forever.
        for (block, parsed), command_collection, snapshot, output_disposition in zip(
            parsed_blocks,
            block_collection_statuses,
            existing_snapshots,
            block_output_dispositions,
            strict=True,
        ):
            execution_status = normalize_execution_status(
                command_collection.get("status")
                or ("failed" if collector_complete is False else "complete")
            )
            parse_status = (
                "not_applicable"
                if output_disposition
                else str(snapshot.parse_status or "not_attempted")
            )
            command_status = derive_command_status(
                execution_status=execution_status,
                parse_status=parse_status,
            )
            command_complete = (
                command_collection.get("status") == "complete"
                if command_collection
                else collector_complete is True
            )
            authoritative = bool(
                command_complete and parse_status in {"parsed", "parse_empty", "raw_hashed"}
            )
            structured = snapshot.structured_json
            entity_count = (
                len(structured)
                if isinstance(structured, list)
                else 1
                if isinstance(structured, dict)
                else None
            )
            binding_metadata = _check_item_binding_metadata(
                command_collection
            )
            collection_repository.save_collection_result(
                run_id=str(snapshot.run_id),
                inventory_device_id=inventory_device_id,
                device_id=str(inventory_device_id or snapshot.device_id),
                canonical_command=block.command_canonical,
                status=command_status.execution_status,
                execution_status=command_status.execution_status,
                parse_status=command_status.parse_status,
                output_status=str(snapshot.output_status or parsed.output_status),
                fact_status=command_status.fact_status,
                normalization_status=command_status.normalization_status,
                authoritative_for_deletion=authoritative,
                baseline_eligible=authoritative,
                observed_entity_count=entity_count,
                failure_code=(
                    None
                    if output_disposition
                    else parsed.failure_code
                    or (
                        str(snapshot.output_status)
                        if parse_status
                        in {
                            "not_applicable",
                            "empty_unverified",
                            "command_error",
                            "truncated",
                            "paged",
                        }
                        else None
                    )
                ),
                observed_at=command_collection.get("observed_at") or snapshot.observed_at,
                execution_item_id=snapshot.execution_item_id,
                collection_action_id=binding_metadata[
                    "collection_action_id"
                ],
                metadata={
                    "parse_status": parse_status,
                    "execution_status": command_status.execution_status,
                    "fact_status": command_status.fact_status,
                    "normalization_status": command_status.normalization_status,
                    "snapshot_id": snapshot.snapshot_id,
                    "collector_complete": command_complete,
                    "collection_item_id": command_collection.get("collection_item_id"),
                    "command_ordinal": command_collection.get("command_ordinal"),
                    "check_item_version_ids": binding_metadata[
                        "check_item_version_ids"
                    ],
                    "runtime_bundle_version_ids": binding_metadata[
                        "runtime_bundle_version_ids"
                    ],
                    "observation_contract_refs": binding_metadata[
                        "observation_contract_refs"
                    ],
                    "selected_check_item_bindings": binding_metadata[
                        "selected_bindings"
                    ],
                    "operator_output_disposition": output_disposition,
                },
                owner_token=run_owner_token,
            )
        return {
            "success": True,
            "run_id": existing_snapshots[0].run_id,
            "device_id": device_id,
            "device_name": device_name,
            "ip": ip,
            "command_count": len(blocks),
            "snapshot_ids": [snapshot.snapshot_id for snapshot in existing_snapshots],
            "new_snapshot_count": 0,
            "duplicate_snapshot_count": len(existing_snapshots),
            "duplicate_snapshot_ids": [snapshot.snapshot_id for snapshot in existing_snapshots],
            "commands": [block.command_canonical for block in blocks],
            "vendor": vendor,
            "model": model,
            "family": identity.family,
            "identity_status": identity.status,
            "mode": mode,
            "dataset_id": dataset_id,
            "storage": "postgresql",
            "legacy_db_path_ignored": str(db_path) if db_path else None,
            "coverage": coverage,
            "coverage_violations": blocking,
            "parse_violations": parse_violations,
            "production_parser_ready": not (
                mode == PRODUCTION_MODE and (blocking or parse_violations)
            ),
            "parser_results": [
                {
                    "snapshot_id": snapshot.snapshot_id,
                    "command": snapshot.command_canonical,
                    "parse_status": (
                        "not_applicable"
                        if output_disposition
                        else snapshot.parse_status
                    ),
                    "candidate_job_id": None,
                    "output_disposition": output_disposition,
                }
                for snapshot, output_disposition in zip(
                    existing_snapshots,
                    block_output_dispositions,
                    strict=True,
                )
            ],
        }
    snapshot_ids: list[str] = []
    duplicate_snapshot_ids: list[str] = []
    parser_results: list[dict[str, Any]] = []
    for (
        (block, parsed),
        (preparse_snapshot, snapshot_created),
        command_observed_at,
        command_collection,
        output_disposition,
    ) in zip(
        parsed_blocks,
        persisted_snapshots,
        command_observed_ats,
        block_collection_statuses,
        block_output_dispositions,
        strict=True,
    ):
        (
            projected_parse_status,
            projected_structured,
            projected_parser_name,
            projected_parser_version,
        ) = _project_parser_result(parsed)
        preparse_output_status = str(
            preparse_snapshot.output_status or "not_classified"
        )
        if output_disposition:
            projected_parse_status = "not_applicable"
            projected_structured = None
            projected_parser_name = None
            projected_parser_version = None
        elif preparse_output_status == "unsupported_command":
            projected_parse_status = "not_applicable"
            projected_structured = None
            projected_parser_name = None
            projected_parser_version = None
        elif preparse_output_status in {
            "empty_unverified",
            "command_error",
            "truncated",
            "paged",
        }:
            projected_parse_status = preparse_output_status
            projected_structured = None
            projected_parser_name = None
            projected_parser_version = None
        snapshot_id = preparse_snapshot.snapshot_id
        parser_attached = False
        if preparse_snapshot.parse_status == "not_attempted":
            _snapshot_id, parser_attached = (
                snapshot_repository.attach_parser_result(
                    snapshot_id=snapshot_id,
                    expected_raw_text_hash=(
                        preparse_snapshot.raw_text_hash
                    ),
                    parse_status=projected_parse_status,
                    structured_json=projected_structured,
                    parser_name=projected_parser_name,
                    parser_version=projected_parser_version,
                    metadata={
                        "handling": parsed.handling,
                        "comparison_profile": (
                            parsed.comparison_profile
                        ),
                        "comparison_profile_version": (
                            parsed.comparison_profile_version
                        ),
                        "capability": parsed.capability,
                        "capability_status": (
                            parsed.capability_status
                        ),
                        "parser_failure_code": parsed.failure_code,
                        "operator_output_disposition": output_disposition,
                    },
                    owner_token=run_owner_token,
                )
            )
        stored_snapshot = snapshot_repository.get_snapshot(snapshot_id)
        if (
            stored_snapshot is None
            or not stored_snapshot.execution_item_id
        ):
            raise RuntimeError(
                f"persisted snapshot identity is unavailable: {snapshot_id}"
            )
        parse_status = str(stored_snapshot.parse_status)
        structured = stored_snapshot.structured_json
        snapshot_ids.append(snapshot_id)
        if not snapshot_created:
            duplicate_snapshot_ids.append(snapshot_id)
        binding_metadata = _check_item_binding_metadata(
            command_collection
        )
        candidate_job_id = None
        candidate_error = None
        candidate_failure_codes = {
            "template_missing",
            "template_parse_error",
            "unexpected_empty",
            "required_fields_missing",
            "field_validation_failed",
            "primary_key_invalid",
            "primary_key_duplicate",
            "asset_hash_mismatch",
        }
        # Production patrol records the gap as evidence but never starts asset
        # generation. Only explicitly imported parser-validation evidence may
        # create Parser candidates. Production gaps are registered in the ledger.
        should_create_candidate = (
            (snapshot_created or parser_attached)
            and mode == PARSER_VALIDATION_MODE
        )
        if (
            (snapshot_created or parser_attached)
            and mode == PRODUCTION_MODE
            and parse_status in {"template_missing", "parse_failed"}
        ):
            try:
                from src.core.parser.gap_ledger import ParserGapLedgerService

                ParserGapLedgerService().record_gap(
                    vendor=identity.vendor or "",
                    model=identity.model,
                    family=identity.family,
                    canonical_command=parsed.canonical_command,
                    failure_code=parsed.failure_code or parse_status,
                    run_id=rid,
                    device_id=device_id,
                )
            except Exception:
                pass
        if (
            should_create_candidate
            and (
                parsed.failure_code in candidate_failure_codes
                or mode == PRODUCTION_MODE
            )
            and not _non_repairable_output(block.raw_output)
        ):
            from src.core.parser.repository import ParserJobRepository

            try:
                candidate_job_id = ParserJobRepository().create_candidate(
                    job_type=(
                        "CREATE_TEMPLATE"
                        if parse_status == "template_missing"
                        else "REPAIR_TEMPLATE"
                    ),
                    vendor=identity.vendor or "",
                    model=identity.model or "",
                    family=identity.family,
                    profile=parsed.template_profile,
                    command=parsed.canonical_command,
                    snapshot_id=snapshot_id,
                    device_id=device_id,
                    failure_code=parsed.failure_code,
                    dataset_id=dataset_id,
                )
            except Exception as exc:
                if mode != PRODUCTION_MODE:
                    raise
                candidate_error = f"{type(exc).__name__}: {exc}"
        parser_results.append(
            {
                "snapshot_id": snapshot_id,
                "command": block.command_canonical,
                "parse_status": parse_status,
                "candidate_job_id": candidate_job_id,
                "candidate_error": candidate_error,
                "output_disposition": output_disposition,
            }
        )
        if isinstance(structured, list):
            entity_count = len(structured)
        elif isinstance(structured, dict):
            entity_count = 1
        else:
            entity_count = None
        execution_status = normalize_execution_status(
            command_collection.get("status")
            or ("failed" if collector_complete is False else "complete")
        )
        command_status = derive_command_status(
            execution_status=execution_status,
            parse_status=parse_status,
        )
        command_complete = (
            command_collection.get("status") == "complete"
            if command_collection
            else collector_complete is True
        )
        authoritative = bool(
            command_complete and parse_status in {"parsed", "parse_empty", "raw_hashed"}
        )
        collection_repository.save_collection_result(
            run_id=rid,
            inventory_device_id=inventory_device_id,
            device_id=str(inventory_device_id or device_id),
            canonical_command=block.command_canonical,
            status=command_status.execution_status,
            execution_status=command_status.execution_status,
            parse_status=command_status.parse_status,
            output_status=str(stored_snapshot.output_status),
            fact_status=command_status.fact_status,
            normalization_status=command_status.normalization_status,
            parser_candidate_id=candidate_job_id,
            authoritative_for_deletion=authoritative,
            baseline_eligible=authoritative,
            observed_entity_count=entity_count,
            failure_code=(
                None
                if output_disposition
                else parsed.failure_code
                or (
                    str(stored_snapshot.output_status)
                    if parse_status
                    in {
                        "not_applicable",
                        "empty_unverified",
                        "command_error",
                        "truncated",
                        "paged",
                    }
                    else None
                )
            ),
            observed_at=command_observed_at,
            execution_item_id=stored_snapshot.execution_item_id,
            collection_action_id=binding_metadata[
                "collection_action_id"
            ],
            metadata={
                "parse_status": parse_status,
                "execution_status": command_status.execution_status,
                "fact_status": command_status.fact_status,
                "normalization_status": command_status.normalization_status,
                "parser_candidate_error": candidate_error,
                "snapshot_id": snapshot_id,
                "collector_complete": command_complete,
                "collection_item_id": command_collection.get("collection_item_id"),
                "command_ordinal": command_collection.get("command_ordinal"),
                "check_item_version_ids": binding_metadata[
                    "check_item_version_ids"
                ],
                "runtime_bundle_version_ids": binding_metadata[
                    "runtime_bundle_version_ids"
                ],
                "observation_contract_refs": binding_metadata[
                    "observation_contract_refs"
                ],
                "selected_check_item_bindings": binding_metadata[
                    "selected_bindings"
                ],
                "operator_output_disposition": output_disposition,
            },
            owner_token=run_owner_token,
        )
    execution_statuses = [
        normalize_execution_status(
            item.get("status") or ("failed" if collector_complete is False else "complete")
        )
        for item in block_collection_statuses
    ]
    if not execution_statuses:
        run_collection_status = "failed"
    elif all(status == "complete" for status in execution_statuses):
        run_collection_status = "completed"
    elif any(status == "complete" for status in execution_statuses):
        run_collection_status = "partial"
    else:
        run_collection_status = "failed"
    parser_readiness_status = (
        "ready"
        if not blocking and not parse_violations
        else "blocked"
        if parsed_blocks
        and all(
            parsed.parser_status not in {"success", "empty"}
            for (_, parsed), output_disposition in zip(
                parsed_blocks,
                block_output_dispositions,
                strict=True,
            )
            if parsed.policy_status == "eligible"
            and not output_disposition
        )
        else "partial"
    )
    if finalize_run:
        run_repository.finish_run(
            rid,
            collection_status=run_collection_status,
            parser_readiness_status=parser_readiness_status,
            fact_readiness_status=parser_readiness_status,
            analysis_status="pending",
            baseline_eligible=False,
            owner_token=run_owner_token,
        )
    return {
        "success": True,
        "run_id": rid,
        "device_id": device_id,
        "device_name": device_name,
        "ip": ip,
        "command_count": len(blocks),
        "snapshot_ids": snapshot_ids,
        "new_snapshot_count": len(snapshot_ids) - len(duplicate_snapshot_ids),
        "duplicate_snapshot_count": len(duplicate_snapshot_ids),
        "duplicate_snapshot_ids": duplicate_snapshot_ids,
        "commands": [b.command_canonical for b in blocks],
        "vendor": vendor,
        "model": model,
        "family": identity.family,
        "identity_status": identity.status,
        "mode": mode,
        "dataset_id": dataset_id,
        "storage": "postgresql",
        "legacy_db_path_ignored": str(db_path) if db_path else None,
        "coverage": coverage,
        "coverage_violations": blocking,
        "parse_violations": parse_violations,
        "production_parser_ready": not (mode == PRODUCTION_MODE and (blocking or parse_violations)),
        "collection_status": run_collection_status,
        "parser_readiness_status": parser_readiness_status,
        "fact_readiness_status": parser_readiness_status,
        "parser_results": parser_results,
    }


def import_raw_path(
    *,
    file_path: str | Path,
    db_path: str | Path | None = None,
    mode: str = PRODUCTION_MODE,
    run_id: str | None = None,
    vendor: str | None = None,
    model: str | None = None,
    observed_at: str | None = None,
    metadata: dict[str, Any] | None = None,
    inventory_bindings: dict[str, str | None] | None = None,
) -> dict[str, Any]:
    """Import one capture file or every txt/log capture in a directory."""
    path = Path(file_path)
    if path.is_file():
        text = _read_text_best_effort(path)
        resolved_name, resolved_ip, _ = _resolve_capture_device(path=path, text=text)
        external_id = f"{resolved_name}-{resolved_ip}" if resolved_ip else resolved_name
        return import_raw_capture(
            file_path=path,
            db_path=db_path,
            mode=mode,
            run_id=run_id,
            vendor=vendor,
            model=model,
            observed_at=observed_at,
            metadata={
                **(metadata or {}),
                "source_relative_path": str(
                    (metadata or {}).get("source_relative_path") or path.name
                ),
            },
            inventory_device_id=(inventory_bindings or {}).get(external_id),
        )
    if not path.is_dir():
        raise FileNotFoundError(f"巡检数据路径不存在: {path}")

    files = sorted(
        candidate
        for candidate in path.rglob("*")
        if candidate.is_file() and candidate.suffix.lower() in {".txt", ".log"}
    )
    if not files:
        raise ValueError(f"目录中未发现 txt/log 巡检文件: {path}")

    from src.core.patrol.run_repository import PatrolRunRepository

    rid = run_id or str(uuid.uuid4())
    dataset_id = _dataset_id(path, metadata)
    normalized_mode = normalize_patrol_mode(mode)
    source = str((metadata or {}).get("source") or "imported_file")
    run_repository = PatrolRunRepository()
    run_repository.ensure_dataset(
        dataset_id=dataset_id,
        name=str((metadata or {}).get("dataset_name") or dataset_id),
        mode=normalized_mode,
        source=source,
        metadata={"source_path": str(path), **(metadata or {})},
    )
    run_repository.create_run(
        run_id=rid,
        source=source,
        mode=normalized_mode,
        dataset_id=dataset_id,
        metadata={"source_directory": str(path), **(metadata or {})},
        started_at=observed_at,
        run_purpose=str((metadata or {}).get("run_purpose") or "scheduled_patrol"),
    )
    imported: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    for candidate in files:
        try:
            text = _read_text_best_effort(candidate)
            resolved_name, resolved_ip, _ = _resolve_capture_device(path=candidate, text=text)
            external_id = f"{resolved_name}-{resolved_ip}" if resolved_ip else resolved_name
            result = import_raw_capture(
                file_path=candidate,
                db_path=db_path,
                mode=mode,
                run_id=rid,
                vendor=vendor,
                model=model,
                observed_at=observed_at,
                metadata={
                    "source_directory": str(path),
                    "dataset_id": dataset_id,
                    **(metadata or {}),
                    "source_relative_path": candidate.relative_to(path).as_posix(),
                },
                inventory_device_id=(inventory_bindings or {}).get(external_id),
                finalize_run=False,
            )
            result["source_file"] = str(candidate)
            imported.append(result)
        except Exception as exc:
            failed.append(
                {
                    "file_path": str(candidate),
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
        run_repository.touch_run(
            rid,
            lease_seconds=900,
            metadata={
                "processed_file_count": len(imported) + len(failed),
                "total_file_count": len(files),
                "failed_file_count": len(failed),
            },
        )

    if imported and not failed:
        collection_status = "completed"
    elif imported:
        collection_status = "partial"
    else:
        collection_status = "failed"
    parser_ready = all(
        item.get("production_parser_ready", True) for item in imported
    ) and not failed
    run_repository.finish_run(
        rid,
        collection_status=collection_status,
        parser_readiness_status="ready" if parser_ready else "partial" if imported else "blocked",
        fact_readiness_status="ready" if parser_ready else "partial" if imported else "blocked",
        analysis_status="pending" if imported else "failed",
        baseline_eligible=False,
        metadata={
            "processed_file_count": len(imported) + len(failed),
            "total_file_count": len(files),
            "failed_files": failed,
        },
    )

    return {
        "success": not failed,
        "mode": normalized_mode,
        "run_id": rid,
        "dataset_id": dataset_id,
        "storage": "postgresql",
        "legacy_db_path_ignored": str(db_path) if db_path else None,
        "source_path": str(path),
        "file_count": len(imported),
        "failed_file_count": len(failed),
        "device_count": len({item["device_id"] for item in imported}),
        "command_count": sum(item["command_count"] for item in imported),
        "snapshot_ids": [snapshot_id for item in imported for snapshot_id in item["snapshot_ids"]],
        "new_snapshot_count": sum(item["new_snapshot_count"] for item in imported),
        "duplicate_snapshot_count": sum(item["duplicate_snapshot_count"] for item in imported),
        "collection_status": collection_status,
        "parser_readiness_status": (
            "ready" if parser_ready else "partial" if imported else "blocked"
        ),
        "failed_files": failed,
        "files": [
            {
                "file_path": result["source_file"],
                "device_id": result["device_id"],
                "command_count": result["command_count"],
            }
            for result in imported
        ],
    }
