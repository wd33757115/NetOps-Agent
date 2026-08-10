#!/usr/bin/env python
"""Verify RuntimeBundle coverage and assembly guidance through the real BFF."""

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
from src.infrastructure.db.models import (  # noqa: E402
    AuditLogRecord,
    CheckItemRuntimeBundleRecord,
    CheckItemRuntimeBundleVersionRecord,
    RuntimeBundleAssetRecord,
)
from src.infrastructure.db.postgres import get_db_session  # noqa: E402

PAGE_SIZE = 7


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


def _database_state() -> dict[str, Any]:
    with get_db_session() as db:
        versions = db.query(CheckItemRuntimeBundleVersionRecord).all()
        return {
            "root_count": db.query(CheckItemRuntimeBundleRecord).count(),
            "version_count": len(versions),
            "asset_count": db.query(RuntimeBundleAssetRecord).count(),
            "draft_count": sum(row.status == "draft" for row in versions),
            "active_pairs": sorted(
                {
                    (
                        str(row.check_item_version_id),
                        str(row.implementation_version_id),
                    )
                    for row in versions
                    if row.status == "active"
                }
            ),
        }


def _catalog_pairs() -> set[tuple[str, str]]:
    catalog = CheckItemCatalogRepository().load_active()
    check_item_versions = {item.id: item.version_id for item in catalog.check_items}
    return {
        (
            check_item_versions[check_item_id],
            implementation.version_id,
        )
        for implementation in catalog.implementations
        for check_item_id in implementation.check_item_ids
        if check_item_id in check_item_versions
    }


def _read_all_health_rows(
    client: httpx.Client,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    first = _request_json(
        client,
        "GET",
        (f"/api/parser/check-item-runtime-bundles/health/?page=1&page_size={PAGE_SIZE}"),
    )
    page_count = int(first.get("page_count") or 0)
    pages = [first]
    for page in range(2, page_count + 1):
        pages.append(
            _request_json(
                client,
                "GET",
                (
                    "/api/parser/check-item-runtime-bundles/health/"
                    f"?page={page}&page_size={PAGE_SIZE}"
                ),
            )
        )
    rows = [
        dict(item) for page in pages for item in page.get("items") or [] if isinstance(item, dict)
    ]
    keys = [
        (
            str(item.get("check_item_version_id") or ""),
            str(item.get("implementation_version_id") or ""),
        )
        for item in rows
    ]
    if len(keys) != len(set(keys)):
        raise AssertionError("runtime health pagination returned duplicate pairs")
    if len(rows) != int(first.get("total") or 0):
        raise AssertionError("runtime health pagination did not reach the exact total")
    if [int(page.get("page") or 0) for page in pages] != list(range(1, page_count + 1)):
        raise AssertionError("runtime health pages are not contiguous")
    return dict(first.get("summary") or {}), rows, pages


def _preview(
    client: httpx.Client,
    row: dict[str, Any],
) -> dict[str, Any]:
    return _request_json(
        client,
        "POST",
        "/api/parser/check-item-runtime-bundles/assembly-preview/",
        json_body={
            "check_item_version_id": row["check_item_version_id"],
            "implementation_version_id": row["implementation_version_id"],
        },
    )


def _find_preview_cases(
    client: httpx.Client,
    rows: list[dict[str, Any]],
) -> tuple[tuple[dict[str, Any], dict[str, Any]], tuple[dict[str, Any], dict[str, Any]]]:
    published_children: tuple[dict[str, Any], dict[str, Any]] | None = None
    blocked: tuple[dict[str, Any], dict[str, Any]] | None = None
    for row in rows:
        preview = _preview(client, row)
        candidates = dict(preview.get("candidates") or {})
        has_published_child = any(
            candidates.get(asset_type)
            for asset_type in (
                "parsers",
                "observation_contracts",
                "change_policies",
                "event_rules",
                "comparison_profiles",
            )
        )
        if (
            published_children is None
            and row.get("status") == "missing"
            and preview.get("active_bundle") is None
            and has_published_child
        ):
            published_children = (row, preview)
        if (
            blocked is None
            and preview.get("operationally_ready") is False
            and preview.get("blockers")
        ):
            blocked = (row, preview)
        if published_children and blocked:
            break
    if published_children is None:
        raise AssertionError(
            "no catalog pair has published child assets while its Active RuntimeBundle is missing"
        )
    if blocked is None:
        raise AssertionError("no blocked RuntimeBundle assembly case was found")
    return published_children, blocked


def _assert_filtered_views(
    client: httpx.Client,
    *,
    rows: list[dict[str, Any]],
    summary: dict[str, Any],
) -> dict[str, Any]:
    status_totals: dict[str, int] = {}
    for status in ("ready", "missing", "invalid"):
        payload = _request_json(
            client,
            "GET",
            (
                "/api/parser/check-item-runtime-bundles/health/"
                f"?page=1&page_size=100&status={status}"
            ),
        )
        expected = sum(item.get("status") == status for item in rows)
        if int(payload.get("total") or 0) != expected:
            raise AssertionError(f"{status} filter total differs from unfiltered rows")
        if dict(payload.get("summary") or {}) != summary:
            raise AssertionError("filtered health view changed the catalog-wide summary")
        status_totals[status] = expected

    target = rows[0]
    search = str(target["check_item_id"])
    searched = _request_json(
        client,
        "GET",
        (f"/api/parser/check-item-runtime-bundles/health/?page=1&page_size=100&search={search}"),
    )
    if not searched.get("items"):
        raise AssertionError("runtime health search did not find the check item")
    if any(
        search.casefold()
        not in " ".join(
            [
                str(item.get("check_item_id") or ""),
                str(item.get("check_item_name") or ""),
                str(item.get("check_item_display_name") or ""),
                str(item.get("implementation_id") or ""),
                str(item.get("implementation_name") or ""),
                *[str(command) for command in item.get("commands") or []],
            ]
        ).casefold()
        for item in searched.get("items") or []
    ):
        raise AssertionError("runtime health search returned an out-of-scope row")

    beyond = _request_json(
        client,
        "GET",
        (
            "/api/parser/check-item-runtime-bundles/health/"
            f"?page={int(summary.get('implementation_pair_count') or 0) + 1}"
            "&page_size=1"
        ),
    )
    if beyond.get("items"):
        raise AssertionError("runtime health returned rows beyond the final page")
    return {
        "status_totals": status_totals,
        "search": search,
        "search_total": int(searched.get("total") or 0),
        "beyond_final_page_empty": True,
    }


def _assert_three_way_reconciliation(
    *,
    rows: list[dict[str, Any]],
    summary: dict[str, Any],
    database_state: dict[str, Any],
) -> dict[str, Any]:
    catalog_pairs = _catalog_pairs()
    api_by_key = {
        (
            str(item["check_item_version_id"]),
            str(item["implementation_version_id"]),
        ): str(item["status"])
        for item in rows
    }
    if set(api_by_key) != catalog_pairs:
        raise AssertionError("BFF runtime health rows differ from the Active Catalog")

    resolver = CheckItemRuntimeBundleService().get_active_many(catalog_pairs)
    resolver_ready = {
        key
        for key, value in resolver.items()
        if value.get("bundle") is not None and not value.get("blockers")
    }
    database_active = {
        tuple(item) for item in database_state["active_pairs"] if tuple(item) in catalog_pairs
    }
    api_ready = {key for key, status in api_by_key.items() if status == "ready"}
    api_invalid = {key for key, status in api_by_key.items() if status == "invalid"}
    api_missing = {key for key, status in api_by_key.items() if status == "missing"}
    if api_ready != resolver_ready:
        raise AssertionError("BFF ready pairs differ from RuntimeBundle Resolver")
    if api_invalid != database_active - resolver_ready:
        raise AssertionError("BFF invalid pairs differ from invalid Active database rows")
    if api_missing != catalog_pairs - database_active:
        raise AssertionError("BFF missing pairs differ from missing Active database rows")
    if int(summary.get("ready_count") or 0) != len(api_ready):
        raise AssertionError("runtime health summary ready_count is inconsistent")
    if int(summary.get("missing_count") or 0) != len(api_missing):
        raise AssertionError("runtime health summary missing_count is inconsistent")
    if int(summary.get("invalid_count") or 0) != len(api_invalid):
        raise AssertionError("runtime health summary invalid_count is inconsistent")
    return {
        "catalog_pairs": len(catalog_pairs),
        "database_active_pairs": len(database_active),
        "resolver_ready_pairs": len(resolver_ready),
        "api_ready_pairs": len(api_ready),
        "api_missing_pairs": len(api_missing),
        "api_invalid_pairs": len(api_invalid),
    }


def _assert_blocked_assembly(
    client: httpx.Client,
    *,
    row: dict[str, Any],
    preview: dict[str, Any],
) -> dict[str, Any]:
    blockers = [dict(item) for item in preview.get("blockers") or [] if isinstance(item, dict)]
    if not blockers:
        raise AssertionError("blocked assembly preview has no structured blockers")
    if any(not item.get("code") or not item.get("message") for item in blockers):
        raise AssertionError("assembly blocker is missing code or message")
    if any(not item.get("remediation") for item in blockers):
        raise AssertionError("one or more assembly blockers provide no engineer remediation")
    actions = [
        dict(item.get("remediation_action") or {})
        for item in blockers
    ]
    if any(
        not action.get("code")
        or not action.get("label")
        or not str(action.get("target_url") or "").startswith("/skills/parser?")
        for action in actions
    ):
        raise AssertionError("one or more assembly blockers provide no actionable deep link")

    response = client.post(
        "/api/parser/check-item-runtime-bundles/assemble/",
        headers={"Idempotency-Key": f"blocked-assembly-{uuid.uuid4().hex}"},
        json={
            "check_item_version_id": row["check_item_version_id"],
            "implementation_version_id": row["implementation_version_id"],
            "preview_digest": preview["preview_digest"],
        },
    )
    if response.status_code != 409:
        raise AssertionError(
            f"blocked assembly did not fail closed: {response.status_code} {response.text}"
        )
    return {
        "status_code": response.status_code,
        "blocker_codes": [str(item["code"]) for item in blockers],
        "blocker_messages": [str(item["message"]) for item in blockers],
        "remediations": [str(item["remediation"]) for item in blockers],
        "remediation_actions": actions,
    }


def run(*, base_url: str, output_path: Path) -> dict[str, Any]:
    suffix = uuid.uuid4().hex[:12]
    username = f"runtime-health-operator-{suffix}"
    password = f"RuntimeHealth-{uuid.uuid4().hex}!aA1"
    summary: dict[str, Any] = {
        "schema_version": "runtime-bundle-control-plane-full-stack-e2e/v1",
        "success": False,
        "base_url": base_url,
    }
    user_created = False
    before = _database_state()
    try:
        _create_user(username, password)
        user_created = True
        with httpx.Client(base_url=base_url.rstrip("/"), timeout=60) as client:
            client.headers["Authorization"] = (
                f"Bearer {_login(client, username=username, password=password)}"
            )
            health_summary, rows, pages = _read_all_health_rows(client)
            reconciliation = _assert_three_way_reconciliation(
                rows=rows,
                summary=health_summary,
                database_state=before,
            )
            filtered = _assert_filtered_views(
                client,
                rows=rows,
                summary=health_summary,
            )
            published_case, blocked_case = _find_preview_cases(client, rows)
            published_row, published_preview = published_case
            blocked_row, blocked_preview = blocked_case
            blocked_result = _assert_blocked_assembly(
                client,
                row=blocked_row,
                preview=blocked_preview,
            )
        after = _database_state()
        if after != before:
            raise AssertionError("read-only control-plane E2E changed RuntimeBundle database state")
        summary.update(
            {
                "success": True,
                "pagination": {
                    "page_size": PAGE_SIZE,
                    "page_count": len(pages),
                    "total": len(rows),
                    "unique_pair_count": len(
                        {
                            (
                                item["check_item_version_id"],
                                item["implementation_version_id"],
                            )
                            for item in rows
                        }
                    ),
                },
                "summary": health_summary,
                "reconciliation": reconciliation,
                "filters": filtered,
                "published_children_missing_bundle": {
                    "check_item_id": published_row["check_item_id"],
                    "check_item_display_name": published_row.get("check_item_display_name"),
                    "implementation_id": published_row["implementation_id"],
                    "health_status": published_row["status"],
                    "selected_parser_template_ids": published_preview.get("selected", {}).get(
                        "parser_template_ids", []
                    ),
                    "selected_contract_ids": published_preview.get("selected", {}).get(
                        "observation_contract_ids", []
                    ),
                    "candidate_counts": {
                        key: len(value or [])
                        for key, value in dict(published_preview.get("candidates") or {}).items()
                    },
                    "operationally_ready": published_preview.get("operationally_ready"),
                    "blocker_codes": [
                        str(item.get("code") or "")
                        for item in published_preview.get("blockers") or []
                        if isinstance(item, dict)
                    ],
                    "preview_digest": published_preview.get("preview_digest"),
                },
                "blocked_assembly": {
                    "check_item_id": blocked_row["check_item_id"],
                    "implementation_id": blocked_row["implementation_id"],
                    **blocked_result,
                },
                "database_unchanged": True,
            }
        )
    except Exception as exc:
        summary["error"] = {
            "type": type(exc).__name__,
            "message": str(exc),
        }
        raise
    finally:
        if user_created:
            with get_db_session() as db:
                deleted_audits = (
                    db.query(AuditLogRecord)
                    .filter(AuditLogRecord.username == f"api:{username}")
                    .delete(synchronize_session=False)
                )
            summary["cleanup"] = {
                "audit_records": int(deleted_audits),
                "django_rows": _delete_user(username),
            }
        else:
            summary["cleanup"] = {"audit_records": 0, "django_rows": 0}
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify RuntimeBundle coverage and assembly guidance"
    )
    parser.add_argument(
        "--base-url",
        default=os.getenv("NETOPS_BFF_BASE_URL", "http://localhost:8001"),
    )
    parser.add_argument(
        "--output-path",
        type=Path,
        default=(ROOT / ".tmp" / "runtime-bundle-control-plane-full-stack-e2e" / "summary.json"),
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
