#!/usr/bin/env python3
"""Replay one real running-configuration pair through the production text path.

The selected TextComparisonProfile and RuntimeBundle are activated only inside
an outer database transaction. Raw configuration is never written to the
result artifact; the transaction is always rolled back.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy.orm import sessionmaker

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.check_items.catalog import CheckItemCatalogRepository  # noqa: E402
from src.core.check_items.comparison import (  # noqa: E402
    BaselineCandidate,
    ComparisonContextRepository,
    ComparisonContextService,
)
from src.core.check_items.execution import (  # noqa: E402
    CheckItemExecutionService,
    TextExecutionResultInput,
)
from src.core.check_items.identity import (  # noqa: E402
    EntityIdentityPolicyRepository,
)
from src.core.check_items.report_projection import (  # noqa: E402
    build_report_projection_snapshot,
    project_report_tables,
)
from src.core.check_items.runtime_bundle import (  # noqa: E402
    CheckItemRuntimeBundleService,
)
from src.core.check_items.text_comparison import (  # noqa: E402
    TextComparisonProfileRepository,
    load_text_comparison_profiles,
)
from src.core.patrol.command_splitter import (  # noqa: E402
    infer_device_from_filename,
    split_cli_capture,
)
from src.infrastructure.db.models import (  # noqa: E402
    CheckExecutionRecord,
    CheckItemRuntimeBundleVersionRecord,
    CommandSnapshot,
    NetworkDevice,
    NetworkChangeRecord,
    PatrolRun,
    TextComparisonProfileVersionRecord,
)
from src.infrastructure.db.postgres import engine, get_db_session  # noqa: E402


CHECK_ITEM_ID = "configuration.running"
IMPLEMENTATION_ID = "h3c-running-configuration"
COMPARISON_PROFILE_ID = "configuration.ordered_lines"
COMMAND = "display current-configuration"


def _read_text_best_effort(path: Path) -> str:
    data = path.read_bytes()
    for encoding in (
        "utf-8-sig",
        "utf-8",
        "utf-16",
        "utf-16-le",
        "utf-16-be",
        "gb18030",
        "gbk",
    ):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _load_command(
    path: Path,
    *,
    observed_at: datetime | None = None,
) -> dict[str, Any]:
    device_name, management_ip = infer_device_from_filename(path)
    blocks = split_cli_capture(
        _read_text_best_effort(path),
        device_name=device_name,
    )
    matches = [block for block in blocks if block.command_canonical == COMMAND]
    if len(matches) != 1:
        raise AssertionError(
            f"expected one {COMMAND!r} block, got {len(matches)}: {path.name}"
        )
    block = matches[0]
    if not block.raw_output.strip():
        raise AssertionError(f"running configuration is empty: {path.name}")
    source_sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
    identity = str(management_ip or device_name or block.device_name or path.stem)
    return {
        "raw_text": block.raw_output,
        "source_sha256": source_sha256,
        "device_id": "device-"
        + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:12],
        "observed_at": observed_at
        or datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc),
        "observed_at_source": (
            "explicit_regression_period_time"
            if observed_at is not None
            else "filesystem_mtime_non_authoritative"
        ),
    }


def _parse_observed_at(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("observed-at values must include a timezone")
    return parsed


def _database_baseline() -> dict[str, int]:
    with get_db_session() as db:
        return {
            "active_runtime_bundle_count": db.query(
                CheckItemRuntimeBundleVersionRecord
            ).filter(
                CheckItemRuntimeBundleVersionRecord.status == "active"
            ).count(),
            "comparison_profile_version_count": db.query(
                TextComparisonProfileVersionRecord
            ).count(),
            "network_change_count": db.query(NetworkChangeRecord).count(),
        }


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


def _assert_preview_redacted(lines: list[str]) -> None:
    unsafe = re.compile(
        r"(?i)\b(?:password|secret|community)\s+(?!<redacted>)(\S+)"
    )
    for line in lines:
        if unsafe.search(line):
            raise AssertionError("text comparison preview contains an unredacted secret")


def run(
    *,
    baseline_file: Path,
    current_file: Path,
    output_dir: Path,
    baseline_observed_at: datetime | None = None,
    current_observed_at: datetime | None = None,
    vendor: str = "H3C",
    model: str = "UNKNOWN",
) -> dict[str, Any]:
    baseline = _load_command(
        baseline_file,
        observed_at=baseline_observed_at,
    )
    current = _load_command(
        current_file,
        observed_at=current_observed_at,
    )
    if baseline["device_id"] != current["device_id"]:
        raise AssertionError("baseline and current files identify different devices")
    if baseline["observed_at"] >= current["observed_at"]:
        raise AssertionError("baseline evidence must predate current evidence")

    before = _database_baseline()
    token = uuid.uuid4().hex[:12]
    connection = engine.connect()
    transaction = connection.begin()
    factory = sessionmaker(
        bind=connection,
        autoflush=False,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )

    @contextmanager
    def scope():
        session = factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    result: dict[str, Any] = {}
    try:
        catalog_repository = CheckItemCatalogRepository(scope)
        catalog_repository.bootstrap(actor="configuration-runtime-replay")
        catalog = catalog_repository.load_active()
        check_item = catalog.check_item(CHECK_ITEM_ID)
        implementation = next(
            (
                item
                for item in catalog.implementations
                if item.id == IMPLEMENTATION_ID
            ),
            None,
        )
        if check_item is None or implementation is None:
            raise AssertionError("configuration.running catalog scope is unavailable")

        profiles = load_text_comparison_profiles()
        profile = next(
            item for item in profiles if item.id == COMPARISON_PROFILE_ID
        )
        TextComparisonProfileRepository(scope).bootstrap(
            profiles,
            actor="configuration-runtime-replay",
        )
        EntityIdentityPolicyRepository(scope).bootstrap(
            actor="configuration-runtime-replay"
        )

        service = CheckItemRuntimeBundleService(scope)
        preview = service.preview_assembly(
            check_item_version_id=check_item.version_id,
            implementation_version_id=implementation.version_id,
            comparison_profile_version_id=profile.version_id,
        )
        if preview.get("operationally_ready") is not True:
            raise AssertionError(
                "configuration RuntimeBundle preview is blocked: "
                + json.dumps(preview.get("blockers") or [], ensure_ascii=False)
            )
        bundle = service.assemble(
            check_item_version_id=check_item.version_id,
            implementation_version_id=implementation.version_id,
            comparison_profile_version_id=profile.version_id,
            preview_digest=str(preview["preview_digest"]),
            actor="configuration-runtime-replay-maker",
        )
        bundle = service.activate(
            str(bundle["version_id"]),
            actor="configuration-runtime-replay-approver",
            reason="transactional real configuration replay",
        )

        schema_ref = (
            f"{bundle['canonical_catalog_version']}:"
            f"{bundle['canonical_catalog_digest']}"
        )
        context = ComparisonContextService().select_explicit(
            device_id=str(current["device_id"]),
            check_item_version_id=check_item.version_id,
            current_observed_at=current["observed_at"],
            baseline=BaselineCandidate(
                run_id=f"configuration-baseline-{token}",
                observed_at=baseline["observed_at"],
                snapshot_ids=(f"configuration-snapshot-baseline-{token}",),
                canonical_schema_version=schema_ref,
                entity_identity_policy_version=(
                    bundle["entity_identity_policy_version_id"]
                ),
            ),
            canonical_schema_version=schema_ref,
            entity_identity_policy_version=(
                bundle["entity_identity_policy_version_id"]
            ),
        )
        ComparisonContextRepository(scope).save(context)

        baseline_snapshot_id = f"configuration-snapshot-baseline-{token}"
        current_snapshot_id = f"configuration-snapshot-current-{token}"
        baseline_run_id = f"configuration-baseline-{token}"
        current_run_id = f"configuration-current-{token}"
        execution_id = f"configuration-execution-{token}"
        with scope() as db:
            db.add(
                NetworkDevice(
                    id=str(current["device_id"]),
                    device_name=f"CONFIG-REPLAY-{token}",
                    management_ip="192.0.2.250",
                    username="regression-operator",
                    credential_ref="test:transaction-only",
                    vendor=vendor,
                    model=model,
                    family="Comware7",
                    platform="comware",
                    device_type="hp_comware",
                    site="transactional-regression",
                    role="router",
                    status="active",
                )
            )
            db.flush()
            db.add_all(
                [
                    PatrolRun(
                        id=baseline_run_id,
                        mode="production",
                        source="offline_file",
                        status="completed",
                        collection_status="completed",
                        parser_readiness_status="not_required",
                        fact_readiness_status="ready",
                        analysis_status="completed",
                        baseline_eligible=False,
                        run_purpose="scheduled_patrol",
                        logical_run_id=baseline_run_id,
                        attempt_number=0,
                        owner_generation=1,
                        device_count=1,
                        command_count=1,
                        started_at=baseline["observed_at"],
                        completed_at=baseline["observed_at"],
                        metadata_json={
                            "source_sha256": baseline["source_sha256"],
                            "transactional_historical_replay": True,
                        },
                        created_at=baseline["observed_at"],
                        updated_at=baseline["observed_at"],
                    ),
                    PatrolRun(
                        id=current_run_id,
                        mode="production",
                        source="offline_file",
                        status="completed",
                        collection_status="completed",
                        parser_readiness_status="not_required",
                        fact_readiness_status="ready",
                        analysis_status="completed",
                        baseline_eligible=False,
                        run_purpose="scheduled_patrol",
                        logical_run_id=current_run_id,
                        attempt_number=0,
                        owner_generation=1,
                        device_count=1,
                        command_count=1,
                        started_at=current["observed_at"],
                        completed_at=current["observed_at"],
                        metadata_json={
                            "source_sha256": current["source_sha256"],
                            "transactional_historical_replay": True,
                        },
                        created_at=current["observed_at"],
                        updated_at=current["observed_at"],
                    ),
                ]
            )
            db.flush()
            db.add_all(
                [
                    CommandSnapshot(
                        id=baseline_snapshot_id,
                        run_id=baseline_run_id,
                        inventory_device_id=str(current["device_id"]),
                        device_id=str(current["device_id"]),
                        execution_item_id=f"configuration-action-baseline-{token}",
                        implementation_version_id=implementation.version_id,
                        check_item_ids_json=[CHECK_ITEM_ID],
                        device_name=str(current["device_id"]),
                        management_ip="192.0.2.1",
                        vendor=vendor,
                        model=model,
                        command=COMMAND,
                        canonical_command=COMMAND,
                        observed_at=baseline["observed_at"],
                        structured_json=None,
                        raw_text=str(baseline["raw_text"]),
                        raw_text_hash=hashlib.sha256(
                            str(baseline["raw_text"]).encode("utf-8")
                        ).hexdigest(),
                        parse_status="raw_hashed",
                        output_status="success",
                    ),
                    CommandSnapshot(
                        id=current_snapshot_id,
                        run_id=current_run_id,
                        inventory_device_id=str(current["device_id"]),
                        device_id=str(current["device_id"]),
                        execution_item_id=f"configuration-action-current-{token}",
                        implementation_version_id=implementation.version_id,
                        check_item_ids_json=[CHECK_ITEM_ID],
                        device_name=str(current["device_id"]),
                        management_ip="192.0.2.1",
                        vendor=vendor,
                        model=model,
                        command=COMMAND,
                        canonical_command=COMMAND,
                        observed_at=current["observed_at"],
                        structured_json=None,
                        raw_text=str(current["raw_text"]),
                        raw_text_hash=hashlib.sha256(
                            str(current["raw_text"]).encode("utf-8")
                        ).hexdigest(),
                        parse_status="raw_hashed",
                        output_status="success",
                    ),
                    CheckExecutionRecord(
                        id=execution_id,
                        idempotency_key=execution_id,
                        patrol_batch_id=f"configuration-batch-{token}",
                        device_id=str(current["device_id"]),
                        check_item_version_id=check_item.version_id,
                        implementation_version_id=implementation.version_id,
                        collection_action_ids_json=[
                            f"configuration-action-current-{token}"
                        ],
                        runtime_bundle_id=str(bundle["bundle_id"]),
                        runtime_bundle_version_id=str(bundle["version_id"]),
                        runtime_bundle_digest=str(bundle["digest"]),
                        comparison_context_id=str(context.id),
                        execution_status="processing",
                        reason_codes_json=[],
                        target_manifest_digest="a" * 64,
                        asset_manifest_digest=str(bundle["digest"]),
                        evidence_refs_json=[
                            f"sha256:{baseline['source_sha256']}",
                            f"sha256:{current['source_sha256']}",
                        ],
                        created_at=current["observed_at"],
                        updated_at=current["observed_at"],
                    ),
                ]
            )

        execution_result = CheckItemExecutionService(scope).finalize_text(
            TextExecutionResultInput(
                execution_id=execution_id,
                current_snapshot_id=current_snapshot_id,
                previous_snapshot_id=baseline_snapshot_id,
                assessed_at=current["observed_at"],
            )
        )
        comparison = dict(execution_result["comparison"])
        _assert_preview_redacted(list(comparison.get("redacted_preview") or []))
        if comparison.get("status") != "comparable":
            raise AssertionError("configuration comparison is not comparable")
        if len(execution_result.get("changes") or []) != 1:
            raise AssertionError("configuration replay must form one normalized Change")

        raw_change = dict(execution_result["changes"][0])
        report_change = {
            "id": raw_change["id"],
            "execution_id": raw_change["check_execution_id"],
            "canonical_command": raw_change["canonical_command"],
            "entity_type": raw_change["entity_type"],
            "entity_key": raw_change["entity_key"],
            "field": raw_change["field"],
            "old": raw_change["old_value_json"],
            "new": raw_change["new_value_json"],
            "change_type": raw_change["change_type"],
            "previous_snapshot_id": raw_change["previous_snapshot_id"],
            "current_snapshot_id": raw_change["current_snapshot_id"],
            "decision_reason": raw_change["decision_reason"],
            "observed_at": raw_change["observed_at"],
        }
        projection_snapshot = build_report_projection_snapshot(
            {
                check_item.version_id: {
                    "check_item_id": CHECK_ITEM_ID,
                    "report_projection": check_item.report_projection,
                }
            }
        )
        matrix = {
            "execution_id": execution_id,
            "device_id": str(current["device_id"]),
            "device_name": str(current["device_id"]),
            "management_ip": "192.0.2.1",
            "check_item_version_id": check_item.version_id,
            "execution_status": execution_result["execution"][
                "execution_status"
            ],
            "assessment_status": execution_result["assessment"][
                "assessment_status"
            ],
            "completeness_status": execution_result["assessment"][
                "completeness_status"
            ],
            "comparison_status": execution_result["assessment"][
                "comparison_status"
            ],
            "blocking_reason_codes": execution_result["assessment"].get(
                "blocking_reason_codes_json"
            )
            or [],
            "runtime_bundle_digest": str(bundle["digest"]),
        }
        tables = project_report_tables(
            {
                "report_projection_snapshot": projection_snapshot,
                "matrix": [matrix],
                "observations": [],
                "changes": [report_change],
            }
        )
        if len(tables) != 1 or len(tables[0].rows) != 1:
            raise AssertionError("configuration report projection lost its Change row")
        if tables[0].profile_ref != "configuration_change@1":
            raise AssertionError("configuration report projection profile drifted")
        projected = dict(
            zip(tables[0].column_ids, tables[0].rows[0], strict=True)
        )
        if projected.get("source_command") != COMMAND:
            raise AssertionError("configuration report projection lost its command")

        result = {
            "schema_version": "configuration-runtime-replay/v1",
            "success": True,
            "mode": "transactional_historical_regression",
            "raw_cli_included": False,
            "production_assets_activated": False,
            "source": {
                "baseline_sha256": baseline["source_sha256"],
                "current_sha256": current["source_sha256"],
                "device_id": current["device_id"],
                "vendor": vendor,
                "model": model,
                "baseline_observed_at_source": baseline["observed_at_source"],
                "current_observed_at_source": current["observed_at_source"],
            },
            "runtime": {
                "check_item_id": CHECK_ITEM_ID,
                "result_mode": check_item.result_mode,
                "comparison_profile_version_id": profile.version_id,
                "comparison_profile_digest": profile.digest,
                "runtime_bundle_digest": bundle["digest"],
                "required_asset_types": preview["asset_requirement_profile"][
                    "required_asset_types"
                ],
                "operationally_ready": preview["operationally_ready"],
            },
            "comparison": {
                "status": comparison["status"],
                "changed": comparison["changed"],
                "previous_digest": comparison["previous_digest"],
                "current_digest": comparison["current_digest"],
                "added_line_count": len(comparison.get("added_lines") or []),
                "removed_line_count": len(
                    comparison.get("removed_lines") or []
                ),
                "redacted_preview_digest": _digest(
                    comparison.get("redacted_preview") or []
                ),
            },
            "facts": {
                "execution_status": execution_result["execution"][
                    "execution_status"
                ],
                "assessment_status": execution_result["assessment"][
                    "assessment_status"
                ],
                "comparison_status": execution_result["assessment"][
                    "comparison_status"
                ],
                "change_count": len(execution_result["changes"]),
                "event_count": len(execution_result["events"]),
            },
            "report_projection": {
                "profile_ref": tables[0].profile_ref,
                "sheet_name": tables[0].sheet_name,
                "column_ids": list(tables[0].column_ids),
                "row_count": len(tables[0].rows),
                "projected_change_type": projected.get("change_type"),
                "projected_field": projected.get("changed_field"),
                "projected_decision_reason": projected.get("decision_reason"),
                "projection_snapshot_digest": projection_snapshot["digest"],
            },
        }
    finally:
        transaction.rollback()
        connection.close()

    after = _database_baseline()
    result["database_before"] = before
    result["database_after"] = after
    result["database_unchanged"] = before == after
    if not result["database_unchanged"]:
        raise AssertionError("configuration replay changed production database state")
    output_dir.mkdir(parents=True, exist_ok=True)
    summary_path = output_dir / "summary.json"
    summary_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    result["summary_path"] = str(summary_path)
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline-file", type=Path, required=True)
    parser.add_argument("--current-file", type=Path, required=True)
    parser.add_argument("--baseline-observed-at")
    parser.add_argument("--current-observed-at")
    parser.add_argument("--vendor", default="H3C")
    parser.add_argument("--model", default="UNKNOWN")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / ".tmp" / "configuration-runtime-replay",
    )
    args = parser.parse_args()
    result = run(
        baseline_file=args.baseline_file.resolve(),
        current_file=args.current_file.resolve(),
        output_dir=args.output_dir.resolve(),
        baseline_observed_at=_parse_observed_at(args.baseline_observed_at),
        current_observed_at=_parse_observed_at(args.current_observed_at),
        vendor=str(args.vendor).strip(),
        model=str(args.model).strip(),
    )
    print(
        json.dumps(
            {
                "success": result["success"],
                "changed": result["comparison"]["changed"],
                "change_count": result["facts"]["change_count"],
                "assessment_status": result["facts"]["assessment_status"],
                "projection_rows": result["report_projection"]["row_count"],
                "database_unchanged": result["database_unchanged"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
