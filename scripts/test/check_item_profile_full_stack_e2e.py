#!/usr/bin/env python
"""Exercise one check-item patrol Profile through the real BFF and PostgreSQL.

The test creates an isolated active ``configuration.running`` RuntimeBundle so
that impact preview can prove a ready device-check pair. It then creates,
reloads, previews, activates and binds a Profile through the same HTTP APIs as
the browser, and verifies that the device Effective Plan freezes that exact
Profile version. All fixture rows are removed before exit.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[2]
DJANGO_ROOT = ROOT / "web" / "django_backend"
for import_root in (ROOT, DJANGO_ROOT):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "django_backend.settings")

import django  # noqa: E402

django.setup()

from bff.roles import assign_user_role  # noqa: E402
from django.contrib.auth import get_user_model  # noqa: E402

from scripts.test.rule_asset_source_import_full_stack_e2e import (  # noqa: E402
    _login,
    _request_json,
)
from src.core.check_items.catalog import CheckItemCatalogRepository  # noqa: E402
from src.core.check_items.runtime_bundle import (  # noqa: E402
    CheckItemRuntimeBundleService,
)
from src.core.check_items.text_comparison import (  # noqa: E402
    TextComparisonProfileRepository,
    load_text_comparison_profiles,
)
from src.infrastructure.db.models import (  # noqa: E402
    AuditLogRecord,
    CheckItemRuntimeBundleRecord,
    CheckItemRuntimeBundleVersionRecord,
    CollectionProfileBindingRecord,
    CommandProfileRecord,
    CommandProfileVersionRecord,
    NetworkDevice,
    PatrolProfileCheckItemRecord,
    RuntimeBundleAssetRecord,
    TextComparisonProfileRecord,
    TextComparisonProfileVersionRecord,
)
from src.infrastructure.db.postgres import get_db_session  # noqa: E402

CHECK_ITEM_ID = "configuration.running"
IMPLEMENTATION_ID = "h3c-running-configuration"
COMPARISON_PROFILE_ID = "configuration.ordered_lines"


def _create_user(username: str, password: str) -> None:
    user = get_user_model().objects.create_user(
        username=username,
        email=f"{username}@example.invalid",
        password=password,
    )
    assign_user_role(user, "operator")
    user.save()


def _delete_user(username: str) -> int:
    deleted, _details = get_user_model().objects.filter(username=username).delete()
    return int(deleted)


def _runtime_state() -> dict[str, int]:
    with get_db_session() as db:
        return {
            "runtime_roots": db.query(CheckItemRuntimeBundleRecord).count(),
            "runtime_versions": db.query(CheckItemRuntimeBundleVersionRecord).count(),
            "active_runtime_versions": (
                db.query(CheckItemRuntimeBundleVersionRecord)
                .filter(CheckItemRuntimeBundleVersionRecord.status == "active")
                .count()
            ),
            "runtime_assets": db.query(RuntimeBundleAssetRecord).count(),
            "comparison_roots": db.query(TextComparisonProfileRecord).count(),
            "comparison_versions": db.query(
                TextComparisonProfileVersionRecord
            ).count(),
        }


def _seed_runtime_bundle(token: str) -> dict[str, Any]:
    catalog = CheckItemCatalogRepository().load_active()
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
    comparison_profile = next(
        item for item in profiles if item.id == COMPARISON_PROFILE_ID
    )
    TextComparisonProfileRepository().bootstrap(
        profiles,
        actor=f"profile-e2e-{token}",
    )
    service = CheckItemRuntimeBundleService()
    preview = service.preview_assembly(
        check_item_version_id=check_item.version_id,
        implementation_version_id=implementation.version_id,
        comparison_profile_version_id=comparison_profile.version_id,
    )
    if preview.get("blockers"):
        raise AssertionError(
            "isolated RuntimeBundle preview is blocked: "
            + json.dumps(preview["blockers"], ensure_ascii=False, default=str)
        )
    draft = service.assemble(
        check_item_version_id=check_item.version_id,
        implementation_version_id=implementation.version_id,
        comparison_profile_version_id=comparison_profile.version_id,
        preview_digest=str(preview["preview_digest"]),
        actor=f"profile-e2e-maker-{token}",
    )
    active = service.activate(
        str(draft["version_id"]),
        actor=f"profile-e2e-approver-{token}",
        reason="isolated profile full-stack verification",
    )
    return {
        "check_item_version_id": check_item.version_id,
        "implementation_version_id": implementation.version_id,
        "comparison_profile_version_id": comparison_profile.version_id,
        "bundle_id": active["bundle_id"],
        "bundle_version_id": active["version_id"],
        "bundle_digest": active["digest"],
    }


def _seed_device(*, device_id: str, token: str) -> None:
    with get_db_session() as db:
        db.add(
            NetworkDevice(
                id=device_id,
                device_name=f"E2E-PROFILE-{token}",
                management_ip="198.51.100.242",
                username="e2e-operator",
                credential_ref="env:NETOPS_PASSWORD",
                vendor="H3C",
                model="MSR5620",
                family="Comware7",
                platform="comware",
                device_type="hp_comware",
                site=f"e2e-profile-{token}",
                role="router",
                status="active",
            )
        )


def _cleanup(
    *,
    username: str,
    device_id: str,
    profile_version_id: str | None,
    binding_id: str | None,
    bundle_version_id: str | None,
    bundle_id: str | None,
    comparison_created: bool,
) -> dict[str, int]:
    profile_id: str | None = None
    with get_db_session() as db:
        if profile_version_id:
            version = db.get(CommandProfileVersionRecord, profile_version_id)
            profile_id = str(version.profile_id) if version is not None else None
        if binding_id:
            db.query(CollectionProfileBindingRecord).filter(
                CollectionProfileBindingRecord.id == binding_id
            ).delete(synchronize_session=False)
        if profile_version_id:
            db.query(PatrolProfileCheckItemRecord).filter(
                PatrolProfileCheckItemRecord.profile_version_id
                == profile_version_id
            ).delete(synchronize_session=False)
            db.query(CommandProfileVersionRecord).filter(
                CommandProfileVersionRecord.id == profile_version_id
            ).delete(synchronize_session=False)
        if profile_id:
            db.query(CommandProfileRecord).filter(
                CommandProfileRecord.id == profile_id
            ).delete(synchronize_session=False)
        if bundle_version_id:
            db.query(RuntimeBundleAssetRecord).filter(
                RuntimeBundleAssetRecord.bundle_version_id
                == bundle_version_id
            ).delete(synchronize_session=False)
            db.query(CheckItemRuntimeBundleVersionRecord).filter(
                CheckItemRuntimeBundleVersionRecord.id == bundle_version_id
            ).delete(synchronize_session=False)
        if bundle_id:
            db.query(CheckItemRuntimeBundleRecord).filter(
                CheckItemRuntimeBundleRecord.id == bundle_id
            ).delete(synchronize_session=False)
        if comparison_created:
            comparison_version_ids = [
                item.version_id
                for item in load_text_comparison_profiles()
            ]
            db.query(TextComparisonProfileVersionRecord).filter(
                TextComparisonProfileVersionRecord.id.in_(
                    comparison_version_ids
                )
            ).delete(synchronize_session=False)
            db.query(TextComparisonProfileRecord).filter(
                TextComparisonProfileRecord.id.in_(
                    [item.id for item in load_text_comparison_profiles()]
                )
            ).delete(synchronize_session=False)
        db.query(NetworkDevice).filter(NetworkDevice.id == device_id).delete(
            synchronize_session=False
        )
        deleted_audits = (
            db.query(AuditLogRecord)
            .filter(
                (AuditLogRecord.username == f"api:{username}")
                | AuditLogRecord.resource_id.in_(
                    [
                        value
                        for value in (
                            profile_version_id,
                            binding_id,
                            bundle_version_id,
                        )
                        if value
                    ]
                )
            )
            .delete(synchronize_session=False)
        )
    return {
        "audit_records": int(deleted_audits),
        "django_rows": _delete_user(username),
    }


def run(*, base_url: str, output_path: Path) -> dict[str, Any]:
    token = uuid.uuid4().hex[:12]
    username = f"profile-e2e-{token}"
    password = f"ProfileE2E-{uuid.uuid4().hex}!aA1"
    device_id = f"e2e-profile-device-{token}"
    profile_name = f"E2E 检查项巡检方案 {token}"
    before = _runtime_state()
    comparison_created = before["comparison_versions"] == 0
    profile_version_id: str | None = None
    binding_id: str | None = None
    runtime: dict[str, Any] = {}
    user_created = False
    summary: dict[str, Any] = {
        "schema_version": "check-item-profile-full-stack-e2e/v1",
        "success": False,
        "base_url": base_url,
        "before": before,
    }
    try:
        runtime = _seed_runtime_bundle(token)
        _seed_device(device_id=device_id, token=token)
        _create_user(username, password)
        user_created = True
        with httpx.Client(base_url=base_url.rstrip("/"), timeout=60) as client:
            client.headers["Authorization"] = (
                f"Bearer {_login(client, username=username, password=password)}"
            )
            created = _request_json(
                client,
                "POST",
                "/api/parser/command-profiles/",
                json_body={
                    "name": profile_name,
                    "action": "patrol",
                    "check_item_ids": [CHECK_ITEM_ID],
                    "status": "draft",
                },
            )
            profile_version_id = str(created["profile_version_id"])

            listed = _request_json(
                client,
                "GET",
                (
                    "/api/parser/command-profiles/"
                    f"?search={token}&limit=10&offset=0"
                ),
            )
            selected = _request_json(
                client,
                "GET",
                (
                    "/api/parser/command-profiles/"
                    f"{profile_version_id}/check-items/"
                ),
            )
            if int(listed.get("total") or 0) != 1:
                raise AssertionError("saved Profile was not reloadable by search")
            if selected.get("check_item_ids") != [CHECK_ITEM_ID]:
                raise AssertionError("reloaded Profile selection is inconsistent")

            preview = _request_json(
                client,
                "POST",
                "/api/parser/command-profiles/impact-preview/",
                json_body={
                    "profile_version_id": profile_version_id,
                    "selector": {"device_id": device_id},
                    "mode": "production",
                    "save_evidence": True,
                    "limit": 10,
                },
            )
            if (
                int(preview.get("matched_device_count") or 0) != 1
                or int(preview.get("ready_check_item_count") or 0) != 1
                or int(preview.get("blocked_check_item_count") or 0) != 0
                or preview.get("evidence_saved") is not True
            ):
                raise AssertionError(
                    "Profile impact preview did not prove one ready device-check pair"
                )

            activated = _request_json(
                client,
                "POST",
                (
                    "/api/parser/command-profiles/"
                    f"{profile_version_id}/activate/"
                ),
                json_body={"reason": "E2E impact evidence accepted"},
            )
            binding = _request_json(
                client,
                "POST",
                "/api/parser/command-profile-bindings/",
                json_body={
                    "profile_version_id": profile_version_id,
                    "selector": {"device_id": device_id},
                    "priority": 1,
                    "status": "disabled",
                },
            )
            binding_id = str(binding["binding_id"])
            _request_json(
                client,
                "POST",
                (
                    "/api/parser/command-profile-bindings/"
                    f"{binding_id}/enable/"
                ),
                json_body={"reason": "E2E bind isolated device"},
            )
            plan = _request_json(
                client,
                "POST",
                f"/api/devices/{device_id}/effective-plan/",
                json_body={"action": "patrol", "mode": "production"},
            )

        plan_metadata = dict(plan.get("plan") or {})
        if plan_metadata.get("profile_version_id") != profile_version_id:
            raise AssertionError("Effective Plan did not freeze the activated Profile version")
        if plan_metadata.get("check_item_ids") != [CHECK_ITEM_ID]:
            raise AssertionError("Effective Plan did not preserve the selected check item")
        if not plan_metadata.get("runtime_manifest_digest"):
            raise AssertionError("Effective Plan did not freeze a runtime manifest digest")
        summary.update(
            {
                "success": True,
                "profile": {
                    "name": profile_name,
                    "version_id": profile_version_id,
                    "status": activated.get("version_status"),
                    "check_item_ids": selected.get("check_item_ids"),
                },
                "impact_preview": {
                    "matched_device_count": preview.get("matched_device_count"),
                    "ready_check_item_count": preview.get(
                        "ready_check_item_count"
                    ),
                    "blocked_check_item_count": preview.get(
                        "blocked_check_item_count"
                    ),
                    "coverage_rate": preview.get("coverage_rate"),
                    "runtime_manifest_digest": preview.get(
                        "runtime_manifest_digest"
                    ),
                    "evidence_saved": preview.get("evidence_saved"),
                },
                "binding": {
                    "binding_id": binding_id,
                    "selector": {"device_id": device_id},
                },
                "effective_plan": {
                    "profile_version_id": plan_metadata.get(
                        "profile_version_id"
                    ),
                    "check_item_ids": plan_metadata.get("check_item_ids"),
                    "runtime_manifest_id": plan_metadata.get(
                        "runtime_manifest_id"
                    ),
                    "runtime_manifest_digest": plan_metadata.get(
                        "runtime_manifest_digest"
                    ),
                    "commands": plan.get("commands"),
                },
                "isolated_runtime": runtime,
            }
        )
    except Exception as exc:
        summary["error"] = {
            "type": type(exc).__name__,
            "message": str(exc),
        }
        raise
    finally:
        summary["cleanup"] = _cleanup(
            username=username,
            device_id=device_id,
            profile_version_id=profile_version_id,
            binding_id=binding_id,
            bundle_version_id=runtime.get("bundle_version_id"),
            bundle_id=runtime.get("bundle_id"),
            comparison_created=comparison_created,
        )
        after = _runtime_state()
        summary["after"] = after
        summary["database_restored"] = after == before
        if summary.get("success") and not summary["database_restored"]:
            summary["success"] = False
            summary["error"] = {
                "type": "AssertionError",
                "message": f"database state was not restored: {before} != {after}",
            }
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
    if summary.get("success") is not True:
        raise AssertionError(summary.get("error") or "profile E2E failed")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify check-item Profile save, activation and Effective Plan"
    )
    parser.add_argument(
        "--base-url",
        default=os.getenv("NETOPS_BFF_BASE_URL", "http://localhost:8001"),
    )
    parser.add_argument(
        "--output-path",
        type=Path,
        default=(
            ROOT
            / ".tmp"
            / "check-item-profile-full-stack-e2e"
            / "summary.json"
        ),
    )
    args = parser.parse_args()
    result = run(
        base_url=args.base_url,
        output_path=args.output_path.resolve(),
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
