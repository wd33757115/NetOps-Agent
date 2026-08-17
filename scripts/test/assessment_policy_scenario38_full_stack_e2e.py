#!/usr/bin/env python
"""Scenario 38 full-stack acceptance for governed assessment policies.

The script uses temporary Inventory targets and historical CLI evidence. It never
creates a production-current claim and removes every test row and object on exit.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import sys
import time
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Barrier
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.celery_tasks.celery_app import celery
from src.core.celery_tasks.tasks import materialize_patrol_batch_report_task
from src.core.check_items.assessment import CheckAssessmentInput, CheckAssessmentService
from src.core.check_items.assessment_policy import (
    build_assessment_basis,
    canonical_digest,
    resolve_assessment_basis,
)
from src.core.parser_generation.validation import TextFSMValidationBackend
from src.core.patrol.command_splitter import infer_device_from_filename, split_cli_capture
from src.infrastructure.db.models import (
    AssessmentPolicyBindingRecord,
    AssessmentPolicyCutoverRecord,
    AssessmentPolicyDecisionRecord,
    AssessmentPolicyMigrationSourceRecord,
    AssessmentPolicyProfileRecord,
    AssessmentPolicyShadowResultRecord,
    AssessmentPolicyVersionRecord,
    AuditLogRecord,
    CheckAssessmentRecord,
    CheckExecutionRecord,
    CheckItemRecord,
    CheckItemRuntimeManifestRecord,
    CheckItemVersionRecord,
    CommandSnapshot,
    CoverageDecisionRecord,
    EvidenceQualificationRecord,
    NetworkDevice,
    ParserTemplate,
    PatrolBatchDeviceRecord,
    PatrolBatchRecord,
    PatrolBatchSummaryProjection,
    PatrolRun,
    ReportManifestRecord,
    StateObservationRecord,
    UserSession,
    WorkflowRun,
)
from src.infrastructure.db.postgres import get_db_session
from src.infrastructure.storage.minio_client import get_minio_storage


CPU_CHECK_ITEM = "system.cpu_usage"
CPU_OBSERVATION = "device.cpu.utilization"
INTERFACE_CHECK_ITEM = "interface.oper_status"
INTERFACE_OBSERVATION = "interface.oper_status"
OPTICAL_CHECK_ITEM = "interface.optical_power"
OPTICAL_OBSERVATION = "interface.optical.rx_power"
MEMORY_CHECK_ITEM = "system.memory_usage"
MEMORY_OBSERVATION = "device.memory.utilization"
RUNTIME_BUNDLE_VERSION = "scenario38-runtime-v1"
RUNTIME_BUNDLE_DIGEST = "b" * 64
CPU_RULE = {
    "rule_id": "scenario38-cpu-high",
    "version_id": "scenario38-cpu-high-v1",
    "version": 1,
    "digest": "a" * 64,
    "condition": {
        "kind": "threshold",
        "threshold_role": "high",
        "fields": ["cpu_usage"],
        "observation_types": [CPU_OBSERVATION],
        "gte": 80,
    },
}
CPU_TEMPLATE = r"""Value Filldown slot (\d+)
Value Filldown cpu_id (\d+)
Value cpu_5s (\d+)
Value cpu_1m (\d+)
Value cpu_5m (\d+)

Start
 ^Slot ${slot} CPU ${cpu_id} CPU usage:
 ^\s+${cpu_5s}% in last 5 seconds
 ^\s+${cpu_1m}% in last 1 minute
 ^\s+${cpu_5m}% in last 5 minutes -> Record
 ^\s*$$

EOF
"""


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


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


def _json_response(
    response: httpx.Response,
    *,
    expected_status: int = 200,
) -> dict[str, Any]:
    if response.status_code != expected_status:
        raise AssertionError(
            f"{response.request.method} {response.request.url.path} returned "
            f"{response.status_code}: {response.text}"
        )
    payload = response.json()
    if payload.get("success") is True and isinstance(payload.get("data"), dict):
        return dict(payload["data"])
    return dict(payload)


def _headers(token: str, idempotency_key: str | None = None) -> dict[str, str]:
    headers = {"Authorization": f"Bearer {token}"}
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key
    return headers


def _login(client: httpx.Client, username: str, password: str) -> str:
    response = client.post(
        "/api/auth/login/",
        json={"username": username, "password": password},
    )
    payload = _json_response(response)
    token = str((payload.get("data") or payload).get("access") or "")
    if not token:
        raise AssertionError("BFF login did not return an access token")
    return token


def _create_test_user(username: str, password: str, role: str) -> None:
    django_root = ROOT / "web" / "django_backend"
    if str(django_root) not in sys.path:
        sys.path.insert(0, str(django_root))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "django_backend.settings")

    import django

    django.setup()
    from bff.roles import assign_user_role
    from django.contrib.auth import get_user_model

    user = get_user_model().objects.create_user(
        username=username,
        email=f"{username}@example.invalid",
        password=password,
    )
    assign_user_role(user, role)
    user.save()


def _delete_test_user(username: str) -> int:
    from django.contrib.auth import get_user_model

    deleted, _ = get_user_model().objects.filter(username=username).delete()
    return int(deleted)


def _active_check_item_version(check_item_id: str) -> dict[str, Any]:
    with get_db_session() as db:
        row = (
            db.query(CheckItemVersionRecord)
            .join(CheckItemRecord, CheckItemRecord.id == CheckItemVersionRecord.check_item_id)
            .filter(
                CheckItemRecord.id == check_item_id,
                CheckItemVersionRecord.status == "active",
            )
            .one()
        )
        return row.to_dict()


def _load_cpu_source(source_file: Path) -> dict[str, Any]:
    source_bytes = source_file.read_bytes()
    raw_capture = source_bytes.decode("utf-8-sig", errors="strict")
    device_name, management_ip = infer_device_from_filename(source_file)
    blocks = [
        block
        for block in split_cli_capture(raw_capture, device_name=device_name)
        if block.command_canonical in {"display cpu", "display cpu-usage"}
    ]
    if len(blocks) != 1:
        raise AssertionError(f"expected one CPU command block, got {len(blocks)}")
    records = TextFSMValidationBackend().parse(CPU_TEMPLATE, str(blocks[0].raw_output))
    if not records:
        raise AssertionError("CPU command block produced no records")
    entity_keys: set[tuple[str, str]] = set()
    for record in records:
        for field in ("slot", "cpu_id", "cpu_5s", "cpu_1m", "cpu_5m"):
            if str(record.get(field) or "").strip() == "":
                raise AssertionError(f"CPU evidence is missing {field}")
        entity_key = (str(record["slot"]), str(record["cpu_id"]))
        if entity_key in entity_keys:
            raise AssertionError(f"CPU evidence contains duplicate entity {entity_key}")
        entity_keys.add(entity_key)
    return {
        "block": blocks[0],
        "records": records,
        "device_name": str(device_name or blocks[0].device_name),
        "management_ip": str(management_ip or "198.51.100.38"),
        "source_sha256": hashlib.sha256(source_bytes).hexdigest(),
    }


class PolicyApi:
    def __init__(
        self,
        *,
        base_url: str,
        author_token: str,
        reviewer_token: str,
        publisher_token: str,
        operator_token: str,
        suffix: str,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.author_token = author_token
        self.reviewer_token = reviewer_token
        self.publisher_token = publisher_token
        self.operator_token = operator_token
        self.suffix = suffix
        self.created_version_ids: list[str] = []
        self.created_profile_ids: list[str] = []
        self.created_binding_ids: list[str] = []

    def _client(self) -> httpx.Client:
        return httpx.Client(base_url=self.base_url, timeout=60)

    def create_approve_bind(
        self,
        *,
        key: str,
        check_item_id: str,
        check_item_version_id: str,
        observation_type: str,
        policy_role: str,
        policy: dict[str, Any],
        binding_type: str,
        scope: dict[str, Any],
        valid_from: datetime | None = None,
        valid_until: datetime | None = None,
        profile_id: str | None = None,
        replaces_binding_id: str | None = None,
        concurrent_semantic_review: bool = False,
        verify_operator_denial: bool = False,
    ) -> dict[str, Any]:
        prefix = f"s38-{self.suffix}-{key}"
        payload: dict[str, Any] = {
            "check_item_id": check_item_id,
            "check_item_version_id": check_item_version_id,
            "observation_type": observation_type,
            "policy_role": policy_role,
            "name": f"Scenario 38 {key} {self.suffix}",
            "description": "isolated full-stack engineering acceptance",
            "policy": policy,
            "evidence_refs": [f"scenario38:{self.suffix}:{key}"],
            "provenance": {
                "source": "full_stack_engineering_acceptance",
                "production_network_claim": False,
            },
        }
        if valid_from is not None:
            payload["valid_from"] = valid_from.isoformat()
        if valid_until is not None:
            payload["valid_until"] = valid_until.isoformat()
        if profile_id:
            payload["profile_id"] = profile_id

        with self._client() as client:
            created = _json_response(
                client.post(
                    "/api/parser/assessment-policies/versions/",
                    headers=_headers(self.author_token, f"{prefix}-create"),
                    json=payload,
                )
            )
            version_id = str(created["id"])
            self.created_version_ids.append(version_id)
            profile_ref = str(created["profile_id"])
            if profile_ref not in self.created_profile_ids:
                self.created_profile_ids.append(profile_ref)
            submitted = _json_response(
                client.post(
                    f"/api/parser/assessment-policies/versions/{version_id}/submit/",
                    headers=_headers(self.author_token, f"{prefix}-submit"),
                    json={"reason": "submit immutable candidate for independent review"},
                )
            )

            if verify_operator_denial:
                denied = client.post(
                    f"/api/parser/assessment-policies/versions/{version_id}/semantic-review/",
                    headers=_headers(self.operator_token, f"{prefix}-operator-denied"),
                    json={
                        "decision": "approved",
                        "reason": "operator must not approve policy semantics",
                    },
                )
                if denied.status_code != 403:
                    raise AssertionError(
                        f"operator semantic review was not denied: {denied.status_code}"
                    )

        if concurrent_semantic_review:
            semantic = self._concurrent_semantic_review(version_id, prefix)
        else:
            with self._client() as client:
                semantic = _json_response(
                    client.post(
                        f"/api/parser/assessment-policies/versions/{version_id}/semantic-review/",
                        headers=_headers(self.reviewer_token, f"{prefix}-semantic"),
                        json={
                            "decision": "approved",
                            "reason": "canonical semantics and evidence are independently verified",
                        },
                    )
                )

        with self._client() as client:
            approved = _json_response(
                client.post(
                    f"/api/parser/assessment-policies/versions/{version_id}/publication-review/",
                    headers=_headers(self.publisher_token, f"{prefix}-publication"),
                    json={
                        "decision": "approved",
                        "reason": "publish isolated policy after independent semantic review",
                    },
                )
            )
            binding_payload: dict[str, Any] = {
                "binding_type": binding_type,
                "scope": scope,
                "reason": "bind only the isolated scenario 38 acceptance target",
            }
            if replaces_binding_id:
                binding_payload["replaces_binding_id"] = replaces_binding_id
            binding = _json_response(
                client.post(
                    f"/api/parser/assessment-policies/versions/{version_id}/bindings/",
                    headers=_headers(self.publisher_token, f"{prefix}-binding"),
                    json=binding_payload,
                )
            )
        self.created_binding_ids.append(str(binding["id"]))
        return {
            "created": created,
            "submitted": submitted,
            "semantic": semantic,
            "approved": approved,
            "binding": binding,
        }

    def _concurrent_semantic_review(self, version_id: str, prefix: str) -> dict[str, Any]:
        barrier = Barrier(2)

        def call() -> tuple[int, dict[str, Any]]:
            barrier.wait(timeout=10)
            with self._client() as client:
                response = client.post(
                    f"/api/parser/assessment-policies/versions/{version_id}/semantic-review/",
                    headers=_headers(self.reviewer_token, f"{prefix}-semantic-concurrent"),
                    json={
                        "decision": "approved",
                        "reason": "same immutable evidence reviewed concurrently",
                    },
                )
                payload = response.json()
                if payload.get("success") is True and isinstance(payload.get("data"), dict):
                    payload = payload["data"]
                return response.status_code, dict(payload)

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(call), executor.submit(call)]
            results = [future.result(timeout=30) for future in futures]
        if [status for status, _ in results] != [200, 200]:
            raise AssertionError(f"concurrent semantic review failed: {results}")
        if {str(payload.get("status")) for _, payload in results} != {"pending_approval"}:
            raise AssertionError(f"concurrent review was not idempotent: {results}")
        return {
            "status": "pending_approval",
            "http_statuses": [status for status, _ in results],
            "result_ids": [str(payload.get("id") or "") for _, payload in results],
        }

    def preview(
        self,
        *,
        check_item_version_id: str,
        device: dict[str, Any],
        observation: dict[str, Any],
        rule: dict[str, Any],
        policy_effective_at: datetime | None = None,
        include_legacy: bool = False,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "check_item_version_id": check_item_version_id,
            "device": device,
            "observation": observation,
            "rule": rule,
            "include_legacy": include_legacy,
            "runtime_bundle_version_id": RUNTIME_BUNDLE_VERSION,
            "runtime_bundle_digest": RUNTIME_BUNDLE_DIGEST,
        }
        if policy_effective_at is not None:
            payload["policy_effective_at"] = policy_effective_at.isoformat()
        with self._client() as client:
            return _json_response(
                client.post(
                    "/api/parser/assessment-policies/resolution-preview/",
                    headers=_headers(self.author_token),
                    json=payload,
                )
            )

    def end_binding(self, binding_id: str, key: str) -> dict[str, Any]:
        with self._client() as client:
            return _json_response(
                client.post(
                    f"/api/parser/assessment-policy-bindings/{binding_id}/end/",
                    headers=_headers(
                        self.publisher_token,
                        f"s38-{self.suffix}-{key}-end-binding",
                    ),
                    json={"reason": "end isolated binding after scenario assertion"},
                )
            )


def _seed_devices(suffix: str) -> dict[str, str]:
    devices = {
        "healthy": {
            "role": "core",
            "site": "xian",
            "ip": "198.51.100.31",
        },
        "conflict": {
            "role": "scenario38_conflict",
            "site": "xian",
            "ip": "198.51.100.32",
        },
        "outside": {
            "role": "branch",
            "site": "beijing",
            "ip": "198.51.100.33",
        },
        "expired": {
            "role": "scenario38_expired",
            "site": "xian",
            "ip": "198.51.100.34",
        },
        "tampered": {
            "role": "scenario38_tampered",
            "site": "xian",
            "ip": "198.51.100.35",
        },
    }
    result: dict[str, str] = {}
    now = _utcnow()
    with get_db_session() as db:
        for index, (name, values) in enumerate(devices.items(), start=1):
            device_id = f"scenario38-{name}-{suffix}"
            db.add(
                NetworkDevice(
                    id=device_id,
                    device_name=f"S38-{name.upper()}-{suffix}",
                    management_ip=values["ip"],
                    vendor="H3C",
                    model="MSR5620",
                    family="Comware7",
                    platform="comware",
                    device_type="router",
                    site=values["site"],
                    role=values["role"],
                    status="active",
                    attributes_json={
                        "capabilities": ["cpu_metrics", "optical_diagnostics"],
                        "scenario38": True,
                        "ordinal": index,
                    },
                    created_at=now,
                    updated_at=now,
                )
            )
            result[name] = device_id
    return result


def _cpu_observation(
    *,
    device_id: str,
    observation_id: str,
    value: float = 24,
    slot: str = "2",
    native_threshold: float | None = None,
) -> dict[str, Any]:
    context: dict[str, Any] = {}
    if native_threshold is not None:
        context = {
            "native_high": native_threshold,
            "threshold_source": "device_output",
            "threshold_reference_sources": {
                "high": {
                    "field_name": "native_high",
                    "source": "same_record",
                }
            },
        }
    return {
        "id": observation_id,
        "snapshot_id": f"snapshot-{observation_id}",
        "device_id": device_id,
        "entity_type": "cpu_scope",
        "entity_key": json.dumps(["slot", slot]),
        "observation_type": CPU_OBSERVATION,
        "field": "cpu_usage",
        "value": value,
        "unit": "%",
        "dimensions": {"scope": "slot", "scope_id": slot, "window": "5m"},
        "observed_at": _utcnow().isoformat(),
        "context": context,
    }


def _assert_resolution(
    result: dict[str, Any],
    *,
    status: str,
    source_kind: str | None = None,
    threshold: float | None = None,
    blocker_code: str | None = None,
) -> dict[str, Any]:
    resolution = dict(result.get("resolution") or {})
    if str(resolution.get("status") or "") != status:
        raise AssertionError(f"unexpected resolution status: {resolution}")
    if blocker_code is not None and resolution.get("blocker_code") != blocker_code:
        raise AssertionError(f"unexpected resolution blocker: {resolution}")
    if source_kind is not None:
        binding = dict(
            dict(resolution.get("assessment_basis") or {}).get("threshold_binding")
            or {}
        )
        if binding.get("source_kind") != source_kind:
            raise AssertionError(f"unexpected threshold source: {binding}")
    if threshold is not None:
        condition = dict(resolution.get("effective_condition") or {})
        actual = condition.get("gte")
        if actual is None:
            actual = condition.get("lte")
        if float(actual) != float(threshold):
            raise AssertionError(f"unexpected effective threshold: {condition}")
    return resolution


def _run_policy_matrix(
    *,
    api: PolicyApi,
    devices: dict[str, str],
) -> dict[str, Any]:
    cpu_version = _active_check_item_version(CPU_CHECK_ITEM)
    interface_version = _active_check_item_version(INTERFACE_CHECK_ITEM)
    optical_version = _active_check_item_version(OPTICAL_CHECK_ITEM)
    memory_version = _active_check_item_version(MEMORY_CHECK_ITEM)
    cpu_version_id = str(cpu_version["id"])
    now = _utcnow()

    applicability = api.create_approve_bind(
        key="cpu-applicability",
        check_item_id=CPU_CHECK_ITEM,
        check_item_version_id=cpu_version_id,
        observation_type="*",
        policy_role="applicability",
        policy={
            "mode": "explicit",
            "decision": "applicable",
            "reason_code": "scenario38_cpu_supported",
        },
        binding_type="catalog_default",
        scope={},
        concurrent_semantic_review=True,
        verify_operator_denial=True,
    )
    catalog = api.create_approve_bind(
        key="cpu-catalog",
        check_item_id=CPU_CHECK_ITEM,
        check_item_version_id=cpu_version_id,
        observation_type=CPU_OBSERVATION,
        policy_role="high",
        policy={"value": 80, "unit": "percent", "operator": "gte", "hysteresis": 5},
        binding_type="catalog_default",
        scope={},
    )
    outside_observation = _cpu_observation(
        device_id=devices["outside"],
        observation_id=f"s38-outside-{api.suffix}",
    )
    catalog_preview = api.preview(
        check_item_version_id=cpu_version_id,
        device={"device_id": devices["outside"]},
        observation=outside_observation,
        rule=CPU_RULE,
    )
    _assert_resolution(
        catalog_preview,
        status="resolved",
        source_kind="catalog_default",
        threshold=80,
    )

    scope = api.create_approve_bind(
        key="cpu-scope-core",
        check_item_id=CPU_CHECK_ITEM,
        check_item_version_id=cpu_version_id,
        observation_type=CPU_OBSERVATION,
        policy_role="high",
        policy={"value": 85, "unit": "%", "operator": "gte", "hysteresis": 5},
        binding_type="scope_override",
        scope={"roles": ["core"]},
    )
    healthy_observation = _cpu_observation(
        device_id=devices["healthy"],
        observation_id=f"s38-healthy-{api.suffix}",
    )
    scope_preview = api.preview(
        check_item_version_id=cpu_version_id,
        device={"device_id": devices["healthy"]},
        observation=healthy_observation,
        rule=CPU_RULE,
    )
    _assert_resolution(
        scope_preview,
        status="resolved",
        source_kind="scope_override",
        threshold=85,
    )

    inventory = api.create_approve_bind(
        key="cpu-inventory",
        check_item_id=CPU_CHECK_ITEM,
        check_item_version_id=cpu_version_id,
        observation_type=CPU_OBSERVATION,
        policy_role="high",
        policy={"value": 90, "unit": "percent", "operator": "gte", "hysteresis": 5},
        binding_type="inventory_profile",
        scope={"device_ids": [devices["healthy"]]},
    )
    inventory_preview = api.preview(
        check_item_version_id=cpu_version_id,
        device={"device_id": devices["healthy"]},
        observation=healthy_observation,
        rule=CPU_RULE,
    )
    inventory_resolution = _assert_resolution(
        inventory_preview,
        status="resolved",
        source_kind="inventory_profile",
        threshold=90,
    )
    considered = list(
        dict(inventory_resolution["assessment_basis"])["threshold_binding"].get(
            "considered_sources"
        )
        or []
    )
    if [item.get("source_kind") for item in considered] != [
        "inventory_profile",
        "scope_override",
        "catalog_default",
    ]:
        raise AssertionError(f"source precedence evidence is incomplete: {considered}")

    native_preview = api.preview(
        check_item_version_id=cpu_version_id,
        device={"device_id": devices["healthy"]},
        observation=_cpu_observation(
            device_id=devices["healthy"],
            observation_id=f"s38-native-{api.suffix}",
            native_threshold=95,
        ),
        rule=CPU_RULE,
    )
    _assert_resolution(
        native_preview,
        status="resolved",
        source_kind="device_output",
        threshold=95,
    )

    with api._client() as client:
        impact = _json_response(
            client.post(
                "/api/parser/assessment-policies/versions/"
                f"{inventory['created']['id']}/impact-preview/",
                headers=_headers(api.author_token),
                json={
                    "binding_type": "inventory_profile",
                    "proposed_scope": {"device_ids": [devices["healthy"]]},
                    "target_selector": {
                        "device_ids": [devices["healthy"], devices["outside"]]
                    },
                    "page": 1,
                    "page_size": 50,
                },
            )
        )
    if int(impact.get("total") or 0) != 2:
        raise AssertionError(f"impact preview did not include both targets: {impact}")

    shadow_payload = {
        "check_item_version_id": cpu_version_id,
        "device": {"device_id": devices["outside"]},
        "observation": outside_observation,
        "rule": CPU_RULE,
        "policy_effective_at": _utcnow().isoformat(),
        "runtime_bundle_version_id": RUNTIME_BUNDLE_VERSION,
        "runtime_bundle_digest": RUNTIME_BUNDLE_DIGEST,
    }
    with api._client() as client:
        shadow = _json_response(
            client.post(
                "/api/parser/assessment-policies/migration/shadow-compare/",
                headers=_headers(api.reviewer_token, f"s38-{api.suffix}-shadow"),
                json=shadow_payload,
            )
        )
        if shadow.get("comparison_status") != "matched":
            raise AssertionError(f"shadow comparison did not match: {shadow}")
        cutover_payload = {
            "check_item_version_id": cpu_version_id,
            "device_id": devices["outside"],
            "shadow_result_ids": [shadow["id"]],
            "reason": "matched shadow evidence permits isolated target cutover",
        }
        denied_cutover = client.post(
            "/api/parser/assessment-policies/migration/cutovers/",
            headers=_headers(api.operator_token, f"s38-{api.suffix}-cutover-denied"),
            json=cutover_payload,
        )
        if denied_cutover.status_code != 403:
            raise AssertionError(
                f"operator cutover was not denied: {denied_cutover.status_code}"
            )
        cutover = _json_response(
            client.post(
                "/api/parser/assessment-policies/migration/cutovers/",
                headers=_headers(api.publisher_token, f"s38-{api.suffix}-cutover"),
                json=cutover_payload,
            )
        )
        rollback = _json_response(
            client.post(
                "/api/parser/assessment-policies/migration/cutovers/"
                f"{cutover['id']}/rollback/",
                headers=_headers(api.publisher_token, f"s38-{api.suffix}-rollback"),
                json={"reason": "rollback rehearsal restores legacy-compatible resolver"},
            )
        )
    if cutover.get("resolver_mode") != "governed_only":
        raise AssertionError(f"cutover mode is invalid: {cutover}")
    if rollback.get("resolver_mode") != "legacy_compat":
        raise AssertionError(f"rollback mode is invalid: {rollback}")

    conflict_a = api.create_approve_bind(
        key="cpu-conflict-a",
        check_item_id=CPU_CHECK_ITEM,
        check_item_version_id=cpu_version_id,
        observation_type=CPU_OBSERVATION,
        policy_role="high",
        policy={"value": 86, "unit": "percent", "operator": "gte"},
        binding_type="scope_override",
        scope={"roles": ["scenario38_conflict"]},
    )
    conflict_b = api.create_approve_bind(
        key="cpu-conflict-b",
        check_item_id=CPU_CHECK_ITEM,
        check_item_version_id=cpu_version_id,
        observation_type=CPU_OBSERVATION,
        policy_role="high",
        policy={"value": 87, "unit": "%", "operator": "gte"},
        binding_type="scope_override",
        scope={"roles": ["scenario38_conflict"], "sites": ["xian"]},
    )
    conflict_preview = api.preview(
        check_item_version_id=cpu_version_id,
        device={"device_id": devices["conflict"]},
        observation=_cpu_observation(
            device_id=devices["conflict"],
            observation_id=f"s38-conflict-{api.suffix}",
        ),
        rule=CPU_RULE,
    )
    _assert_resolution(
        conflict_preview,
        status="conflict",
        blocker_code="assessment_policy_conflict",
    )
    if conflict_preview["resolution"].get("should_evaluate") is not False:
        raise AssertionError("same-priority conflict did not fail closed")

    expired = api.create_approve_bind(
        key="cpu-expired",
        check_item_id=CPU_CHECK_ITEM,
        check_item_version_id=cpu_version_id,
        observation_type=CPU_OBSERVATION,
        policy_role="high",
        policy={"value": 88, "unit": "percent", "operator": "gte"},
        binding_type="scope_override",
        scope={"roles": ["scenario38_expired"]},
        valid_from=now - timedelta(days=2),
        valid_until=now - timedelta(days=1),
    )
    expired_preview = api.preview(
        check_item_version_id=cpu_version_id,
        device={"device_id": devices["expired"]},
        observation=_cpu_observation(
            device_id=devices["expired"],
            observation_id=f"s38-expired-{api.suffix}",
        ),
        rule=CPU_RULE,
    )
    _assert_resolution(
        expired_preview,
        status="conflict",
        blocker_code="assessment_policy_source_invalid",
    )

    tampered = api.create_approve_bind(
        key="cpu-tampered",
        check_item_id=CPU_CHECK_ITEM,
        check_item_version_id=cpu_version_id,
        observation_type=CPU_OBSERVATION,
        policy_role="high",
        policy={"value": 89, "unit": "percent", "operator": "gte"},
        binding_type="scope_override",
        scope={"roles": ["scenario38_tampered"]},
    )
    with get_db_session() as db:
        row = db.get(AssessmentPolicyVersionRecord, str(tampered["created"]["id"]))
        policy = dict(row.policy_json or {})
        threshold = dict(policy.get("threshold") or {})
        threshold["value"] = 99
        row.policy_json = {**policy, "threshold": threshold}
    tampered_preview = api.preview(
        check_item_version_id=cpu_version_id,
        device={"device_id": devices["tampered"]},
        observation=_cpu_observation(
            device_id=devices["tampered"],
            observation_id=f"s38-tampered-{api.suffix}",
        ),
        rule=CPU_RULE,
    )
    _assert_resolution(
        tampered_preview,
        status="conflict",
        blocker_code="assessment_policy_source_invalid",
    )

    freeze_before_approval = api.preview(
        check_item_version_id=cpu_version_id,
        device={"device_id": devices["outside"]},
        observation=outside_observation,
        rule=CPU_RULE,
        policy_effective_at=now - timedelta(days=1),
    )
    if str(freeze_before_approval["resolution"].get("status")) == "resolved":
        raise AssertionError("policy approved after the freeze timestamp was accepted")

    memory_policy = api.create_approve_bind(
        key="memory-unrelated-tampered",
        check_item_id=MEMORY_CHECK_ITEM,
        check_item_version_id=str(memory_version["id"]),
        observation_type=MEMORY_OBSERVATION,
        policy_role="high",
        policy={"value": 90, "unit": "percent", "operator": "gte"},
        binding_type="inventory_profile",
        scope={"device_ids": [devices["healthy"]]},
    )
    with get_db_session() as db:
        row = db.get(AssessmentPolicyVersionRecord, str(memory_policy["created"]["id"]))
        row.digest = _digest({"scenario38_memory_tamper": api.suffix})
    isolated_cpu = api.preview(
        check_item_version_id=cpu_version_id,
        device={"device_id": devices["healthy"]},
        observation=healthy_observation,
        rule=CPU_RULE,
    )
    _assert_resolution(
        isolated_cpu,
        status="resolved",
        source_kind="inventory_profile",
        threshold=90,
    )

    interface_applicability = api.create_approve_bind(
        key="interface-applicability",
        check_item_id=INTERFACE_CHECK_ITEM,
        check_item_version_id=str(interface_version["id"]),
        observation_type="*",
        policy_role="applicability",
        policy={
            "mode": "explicit",
            "decision": "applicable",
            "reason_code": "scenario38_interface_supported",
        },
        binding_type="inventory_profile",
        scope={"device_ids": [devices["healthy"]]},
    )
    expected_state = api.create_approve_bind(
        key="interface-expected-state",
        check_item_id=INTERFACE_CHECK_ITEM,
        check_item_version_id=str(interface_version["id"]),
        observation_type=INTERFACE_OBSERVATION,
        policy_role="expected_state",
        policy={"expected_states": ["up"]},
        binding_type="inventory_profile",
        scope={"device_ids": [devices["healthy"]]},
    )
    interface_rule = {
        "rule_id": "scenario38-interface-down",
        "version_id": "scenario38-interface-down-v1",
        "digest": "d" * 64,
        "condition": {"kind": "transition", "fields": ["status"], "to_group": "down"},
    }
    interface_sources: list[str] = []
    for current_status in ("down", "up"):
        preview = api.preview(
            check_item_version_id=str(interface_version["id"]),
            device={"device_id": devices["healthy"]},
            observation={
                "id": f"s38-interface-{current_status}-{api.suffix}",
                "device_id": devices["healthy"],
                "entity_type": "interface",
                "entity_key": "Ethernet1/1",
                "observation_type": INTERFACE_OBSERVATION,
                "field": "status",
                "value": current_status,
                "unit": None,
            },
            rule=interface_rule,
        )
        resolution = _assert_resolution(preview, status="resolved")
        expected_binding = dict(
            dict(resolution["assessment_basis"]).get("expected_state_binding") or {}
        )
        if expected_binding.get("expected_states") != ["up"]:
            raise AssertionError(f"interface intent was inferred from current state: {preview}")
        interface_sources.append(str(expected_binding.get("source_kind")))
    if interface_sources != ["inventory_profile", "inventory_profile"]:
        raise AssertionError(f"interface expected-state source drifted: {interface_sources}")

    optical_applicability = api.create_approve_bind(
        key="optical-applicability",
        check_item_id=OPTICAL_CHECK_ITEM,
        check_item_version_id=str(optical_version["id"]),
        observation_type="*",
        policy_role="applicability",
        policy={
            "mode": "explicit",
            "decision": "applicable",
            "reason_code": "scenario38_optical_supported",
        },
        binding_type="inventory_profile",
        scope={"device_ids": [devices["healthy"]]},
    )
    optical_rule = {
        "rule_id": "scenario38-optical-rx-low",
        "version_id": "scenario38-optical-rx-low-v1",
        "digest": "e" * 64,
        "condition": {
            "kind": "threshold_field_lte",
            "threshold_role": "low",
            "fields": ["rx_power_dbm"],
            "reference_field": "rx_low_warning_dbm",
            "hysteresis": 2,
        },
    }

    optical_native = api.preview(
        check_item_version_id=str(optical_version["id"]),
        device={"device_id": devices["healthy"]},
        observation={
            "id": f"s38-optical-e1-1-{api.suffix}",
            "snapshot_id": f"s38-optical-snapshot-{api.suffix}",
            "device_id": devices["healthy"],
            "entity_type": "interface_optical",
            "entity_key": json.dumps(["Ethernet1/1", "lane0"]),
            "observation_type": OPTICAL_OBSERVATION,
            "field": "rx_power_dbm",
            "value": -18,
            "unit": "dBm",
            "context": {
                "rx_low_warning_dbm": -17,
                "threshold_reference_sources": {
                    "low": {
                        "field_name": "rx_low_warning_dbm",
                        "source": "same_record",
                    }
                },
            },
        },
        rule=optical_rule,
    )
    _assert_resolution(
        optical_native,
        status="resolved",
        source_kind="device_output",
        threshold=-17,
    )
    optical_other_entity = api.preview(
        check_item_version_id=str(optical_version["id"]),
        device={"device_id": devices["healthy"]},
        observation={
            "id": f"s38-optical-e1-2-{api.suffix}",
            "device_id": devices["healthy"],
            "entity_type": "interface_optical",
            "entity_key": json.dumps(["Ethernet1/2", "lane0"]),
            "observation_type": OPTICAL_OBSERVATION,
            "field": "rx_power_dbm",
            "value": -18,
            "unit": "dBm",
            "context": {},
        },
        rule=optical_rule,
    )
    _assert_resolution(
        optical_other_entity,
        status="missing",
        blocker_code="assessment_policy_missing",
    )

    frozen_snapshot = dict(inventory_preview["snapshot"])
    frozen_resolution = dict(inventory_preview["resolution"])
    inventory_revision = api.create_approve_bind(
        key="cpu-inventory-revision",
        check_item_id=CPU_CHECK_ITEM,
        check_item_version_id=cpu_version_id,
        observation_type=CPU_OBSERVATION,
        policy_role="high",
        policy={"value": 70, "unit": "percent", "operator": "gte", "hysteresis": 5},
        binding_type="inventory_profile",
        scope={"device_ids": [devices["healthy"]]},
        profile_id=str(inventory["created"]["profile_id"]),
        replaces_binding_id=str(inventory["binding"]["id"]),
    )
    drift_preview = api.preview(
        check_item_version_id=cpu_version_id,
        device={"device_id": devices["healthy"]},
        observation=healthy_observation,
        rule=CPU_RULE,
    )
    _assert_resolution(
        drift_preview,
        status="resolved",
        source_kind="inventory_profile",
        threshold=70,
    )
    replay = resolve_assessment_basis(
        rule=CPU_RULE,
        observation=healthy_observation,
        policy_snapshot=frozen_snapshot,
        runtime_bundle_version_id=RUNTIME_BUNDLE_VERSION,
        runtime_bundle_digest=RUNTIME_BUNDLE_DIGEST,
    )
    if replay.status != "resolved" or float(replay.effective_condition.get("gte")) != 90:
        raise AssertionError("frozen policy snapshot changed after active asset drift")
    if replay.basis != frozen_resolution["assessment_basis"]:
        raise AssertionError("frozen AssessmentBasis is not deterministically replayable")

    return {
        "check_item_versions": {
            "cpu": cpu_version_id,
            "interface": str(interface_version["id"]),
            "optical": str(optical_version["id"]),
            "memory": str(memory_version["id"]),
        },
        "rbac": {
            "operator_semantic_review": 403,
            "operator_cutover": 403,
        },
        "concurrent_semantic_review": applicability["semantic"],
        "source_precedence": {
            "catalog": 80,
            "scope": 85,
            "inventory": 90,
            "device_output": 95,
            "considered_sources": [item.get("source_kind") for item in considered],
        },
        "impact_preview": {
            "total": impact.get("total"),
            "counts": impact.get("counts"),
        },
        "conflict": {
            "status": conflict_preview["resolution"]["status"],
            "blocker_code": conflict_preview["resolution"]["blocker_code"],
            "snapshot": conflict_preview["snapshot"],
            "basis_entry": conflict_preview["resolution"]["assessment_basis"],
        },
        "invalid_sources": {
            "expired": expired_preview["resolution"]["blocker_code"],
            "tampered": tampered_preview["resolution"]["blocker_code"],
            "approved_after_freeze": freeze_before_approval["resolution"]["status"],
            "unrelated_memory_isolated": isolated_cpu["resolution"]["status"],
        },
        "interface_expected_state_sources": interface_sources,
        "optical": {
            "exact_entity_source": optical_native["resolution"]["assessment_basis"][
                "threshold_binding"
            ]["source_kind"],
            "other_entity_status": optical_other_entity["resolution"]["status"],
        },
        "migration": {
            "shadow_id": shadow["id"],
            "shadow_status": shadow["comparison_status"],
            "cutover_id": cutover["id"],
            "cutover_mode": cutover["resolver_mode"],
            "rollback_id": rollback["id"],
            "rollback_mode": rollback["resolver_mode"],
        },
        "frozen": {
            "snapshot": frozen_snapshot,
            "basis_entry": frozen_resolution["assessment_basis"],
            "old_threshold": 90,
            "new_threshold": 70,
            "new_binding_id": inventory_revision["binding"]["id"],
        },
        "asset_ids": {
            "scope_binding": scope["binding"]["id"],
            "catalog_binding": catalog["binding"]["id"],
            "conflict_bindings": [
                conflict_a["binding"]["id"],
                conflict_b["binding"]["id"],
            ],
            "expired_binding": expired["binding"]["id"],
            "tampered_binding": tampered["binding"]["id"],
            "interface_applicability": interface_applicability["binding"]["id"],
            "interface_expected_state": expected_state["binding"]["id"],
            "optical_applicability": optical_applicability["binding"]["id"],
        },
    }


def _seed_report(
    *,
    suffix: str,
    source_file: Path,
    device_id: str,
    cpu_version_id: str,
    frozen_snapshot: dict[str, Any],
    source: dict[str, Any],
) -> dict[str, Any]:
    now = _utcnow()
    batch_id = f"assessment-policy-s38-{suffix}"
    run_id = f"assessment-policy-s38-run-{suffix}"
    workflow_run_id = f"assessment-policy-s38-wf-{suffix}"
    snapshot_id = f"assessment-policy-s38-snapshot-{suffix}"
    execution_id = f"assessment-policy-s38-execution-{suffix}"
    qualification_id = f"assessment-policy-s38-qual-{suffix}"
    coverage_id = f"assessment-policy-s38-coverage-{suffix}"
    runtime_manifest_id = f"assessment-policy-s38-runtime-{suffix}"
    parser_id = f"assessment-policy-s38-parser-{suffix}"
    action_id = f"assessment-policy-s38-action-{suffix}"

    records = list(source["records"])
    entries: list[dict[str, Any]] = []
    observation_payloads: list[dict[str, Any]] = []
    for index, record in enumerate(records):
        observation = _cpu_observation(
            device_id=device_id,
            observation_id=f"assessment-policy-s38-observation-{index}-{suffix}",
            value=float(record["cpu_5m"]),
            slot=str(record["slot"]),
        )
        resolution = resolve_assessment_basis(
            rule=CPU_RULE,
            observation=observation,
            policy_snapshot=frozen_snapshot,
            runtime_bundle_version_id=RUNTIME_BUNDLE_VERSION,
            runtime_bundle_digest=RUNTIME_BUNDLE_DIGEST,
        )
        if resolution.status != "resolved" or not resolution.should_evaluate:
            raise AssertionError(f"frozen report policy did not resolve: {resolution}")
        if float(resolution.effective_condition.get("gte")) != 90:
            raise AssertionError("historical report no longer uses its frozen threshold")
        entries.append(resolution.basis)
        observation_payloads.append(observation)

    assessment_basis = build_assessment_basis(
        entries=entries,
        policy_snapshot_digest=str(frozen_snapshot["digest"]),
        runtime_bundle_version_id=RUNTIME_BUNDLE_VERSION,
        runtime_bundle_digest=RUNTIME_BUNDLE_DIGEST,
        applicability_decision=dict(frozen_snapshot.get("applicability_decision") or {}),
    )
    if int(dict(assessment_basis["summary"])["entry_count"]) != len(records):
        raise AssertionError("report AssessmentBasis lost CPU entities")

    target_digest = _digest(
        {
            "device_id": device_id,
            "device_name": source["device_name"],
            "management_ip": source["management_ip"],
        }
    )
    inventory_digest = _digest(
        {
            "device_id": device_id,
            "vendor": "H3C",
            "model": "MSR5620",
            "site": "xian",
            "role": "core",
        }
    )
    runtime_payload = {
        "schema_version": "check-item-runtime-manifest/v2",
        "patrol_batch_id": batch_id,
        "target_manifest_digest": target_digest,
        "inventory_snapshot_digest": inventory_digest,
        "check_item_versions": [cpu_version_id],
        "device_check_item_versions": {device_id: [cpu_version_id]},
        "collection_actions": [{"id": action_id, "command": "display cpu"}],
        "runtime_bundle_refs": [
            {
                "version_id": RUNTIME_BUNDLE_VERSION,
                "digest": RUNTIME_BUNDLE_DIGEST,
            }
        ],
        "comparison_contexts": [],
        "entity_identity_policy_versions": [],
        "assessment_schema_version": "check-assessment/v1",
        "report_schema_version": "patrol-report/v2",
        "production_network_claim": False,
    }
    runtime_digest = _digest(runtime_payload)
    runtime_payload["digest"] = runtime_digest
    template_sha = hashlib.sha256(CPU_TEMPLATE.encode("utf-8")).hexdigest()
    qualification_policy = {
        "source_identity": "inventory_match",
        "output_quality": "complete",
        "freshness": "PT24H",
        "engineering_fixture": True,
    }
    coverage_policy = {
        "model": "fixed",
        "expected_entity_keys": [
            ["cpu_scope", json.dumps(["slot", str(record["slot"])])]
            for record in records
        ],
    }
    expected_entity_keys = list(coverage_policy["expected_entity_keys"])

    with get_db_session() as db:
        parser = ParserTemplate(
            id=parser_id,
            vendor="H3C",
            model="MSR5620",
            family="Comware7",
            profile=f"scenario38-{suffix}",
            canonical_command="display cpu",
            category="cpu_statistics",
            version="1",
            asset_path=f"scenario38/{suffix}/display_cpu.textfsm",
            template_text=CPU_TEMPLATE,
            sha256=template_sha,
            status="active",
            validation_json={
                "compile_success": True,
                "sample_count": 1,
                "record_count": len(records),
                "source": "scenario38_full_stack",
            },
            contract_version=1,
            contract_sha256=_digest(
                {
                    "entity": "cpu_scope",
                    "fields": ["slot", "cpu_id", "cpu_5s", "cpu_1m", "cpu_5m"],
                }
            ),
            source_job_id=None,
            created_at=now,
            updated_at=now,
            activated_at=now,
        )
        batch = PatrolBatchRecord(
            patrol_batch_id=batch_id,
            workflow_name="assessment-policy-scenario38-full-stack-e2e",
            launch_mode="workflow",
            patrol_mode="offline_history",
            report_purpose="offline_historical",
            target_type="offline_import",
            target_label="Scenario 38 isolated historical acceptance",
            selector_json={
                "source": "lixian",
                "file": source_file.name,
                "engineering_fixture": True,
                "production_network_claim": False,
            },
            target_count=1,
            launched_count=1,
            skipped_count=0,
            target_manifest_digest=target_digest,
            inventory_snapshot_digest=inventory_digest,
            runtime_manifest_id=runtime_manifest_id,
            runtime_manifest_digest=runtime_digest,
            save_baseline=False,
            dispatch_status="completed",
            dispatch_requested_at=now,
            dispatch_started_at=now,
            dispatch_completed_at=now,
            report_status="not_requested",
            archived=False,
            created_at=now,
            updated_at=now,
        )
        workflow = WorkflowRun(
            id=workflow_run_id,
            template_name="assessment-policy-scenario38-full-stack-e2e",
            source="full-stack-e2e",
            status="completed",
            context={
                "patrol_batch_id": batch_id,
                "device_id": device_id,
                "production_network_claim": False,
            },
            current_step_index=1,
            created_at=now,
            updated_at=now,
            completed_at=now,
        )
        patrol_run = PatrolRun(
            id=run_id,
            mode="parser_validation",
            source="offline_file",
            status="completed",
            collection_status="completed",
            parser_readiness_status="ready",
            fact_readiness_status="ready",
            analysis_status="completed",
            baseline_eligible=False,
            run_purpose="parser_validation",
            logical_run_id=run_id,
            attempt_number=0,
            owner_generation=1,
            device_count=1,
            command_count=1,
            started_at=now,
            completed_at=now,
            metadata_json={
                "source_file": str(source_file),
                "source_sha256": source["source_sha256"],
                "engineering_fixture": True,
                "production_network_claim": False,
            },
            created_at=now,
            updated_at=now,
        )
        runtime_manifest = CheckItemRuntimeManifestRecord(
            id=runtime_manifest_id,
            patrol_batch_id=batch_id,
            schema_version=2,
            target_manifest_digest=target_digest,
            inventory_snapshot_digest=inventory_digest,
            check_item_versions_json=[cpu_version_id],
            collection_actions_json=[action_id],
            runtime_bundle_refs_json=[
                {
                    "version_id": RUNTIME_BUNDLE_VERSION,
                    "digest": RUNTIME_BUNDLE_DIGEST,
                }
            ],
            comparison_context_ids_json=[],
            entity_identity_policy_versions_json=[],
            assessment_schema_version="check-assessment/v1",
            report_schema_version="patrol-report/v2",
            manifest_json=runtime_payload,
            digest=runtime_digest,
            status="frozen",
            created_by="scenario38-full-stack-e2e",
            created_at=now,
        )
        db.add_all([parser, batch, workflow, patrol_run, runtime_manifest])
        db.flush()
        db.add(
            PatrolBatchDeviceRecord(
                id=f"assessment-policy-s38-target-{suffix}",
                patrol_batch_id=batch_id,
                device_id=device_id,
                device_name=str(source["device_name"]),
                management_ip=str(source["management_ip"]),
                selection_status="launched",
                preflight_status="ready",
                workflow_run_id=workflow_run_id,
                dispatch_status="dispatched",
                dispatch_attempts=1,
                dispatched_at=now,
                created_at=now,
                updated_at=now,
            )
        )
        block = source["block"]
        snapshot = CommandSnapshot(
            id=snapshot_id,
            run_id=run_id,
            inventory_device_id=device_id,
            device_id=device_id,
            execution_item_id=action_id,
            collection_action_id=action_id,
            implementation_version_id=None,
            check_item_ids_json=[CPU_CHECK_ITEM],
            device_name=str(source["device_name"]),
            management_ip=str(source["management_ip"]),
            vendor="H3C",
            model="MSR5620",
            command=str(block.command),
            canonical_command="display cpu",
            parser_template_id=parser_id,
            parser_version="1",
            contract_version=1,
            observed_at=now,
            structured_json=records,
            raw_text=str(block.raw_output),
            raw_text_hash=hashlib.sha256(
                str(block.raw_output).encode("utf-8")
            ).hexdigest(),
            parse_status="parsed",
            output_status="success",
            snapshot_kind="full",
            source_file_hash=str(source["source_sha256"]),
            start_line=int(block.start_line),
            end_line=int(block.end_line),
            metadata_json={
                "engineering_fixture": True,
                "production_network_claim": False,
            },
            created_at=now,
        )
        execution = CheckExecutionRecord(
            id=execution_id,
            idempotency_key=execution_id,
            patrol_batch_id=batch_id,
            device_id=device_id,
            check_item_version_id=cpu_version_id,
            implementation_version_id=None,
            collection_action_ids_json=[action_id],
            runtime_bundle_id="scenario38-runtime",
            runtime_bundle_version_id=RUNTIME_BUNDLE_VERSION,
            runtime_bundle_digest=RUNTIME_BUNDLE_DIGEST,
            assessment_policy_snapshot_json=frozen_snapshot,
            assessment_policy_snapshot_digest=str(frozen_snapshot["digest"]),
            execution_status="completed",
            reason_codes_json=[],
            observation_count=len(records),
            observed_entity_count=len(records),
            change_count=0,
            event_count=0,
            current_finding_count=0,
            assessment_id=None,
            evidence_qualification_id=qualification_id,
            coverage_decision_id=coverage_id,
            source_identity_status="confirmed",
            output_quality_status="complete",
            freshness_status="fresh",
            empty_result_classification="not_empty",
            coverage_status="complete",
            coverage_basis="fixed",
            risk_verification_status="no_current_risk",
            expected_entity_count=len(records),
            observation_min_at=now,
            observation_max_at=now,
            target_manifest_digest=target_digest,
            asset_manifest_digest=runtime_digest,
            evidence_refs_json=[snapshot_id],
            completed_at=now,
            created_at=now,
            updated_at=now,
        )
        db.add_all([snapshot, execution])
        db.flush()
        qualification = EvidenceQualificationRecord(
            id=qualification_id,
            digest=_digest({"qualification": qualification_id, "snapshot": snapshot_id}),
            check_execution_id=execution_id,
            patrol_batch_id=batch_id,
            device_id=device_id,
            check_item_version_id=cpu_version_id,
            implementation_version_id=None,
            runtime_bundle_version_id=RUNTIME_BUNDLE_VERSION,
            runtime_bundle_digest=RUNTIME_BUNDLE_DIGEST,
            source_identity_status="confirmed",
            output_quality_status="complete",
            freshness_status="fresh",
            empty_result_classification="not_empty",
            snapshot_ids_json=[snapshot_id],
            observation_min_at=now,
            observation_max_at=now,
            qualification_reason_codes_json=[],
            policy_snapshot_json=qualification_policy,
            policy_digest=_digest(qualification_policy),
            evidence_refs_json=[snapshot_id],
            qualified_at=now,
            created_at=now,
        )
        coverage = CoverageDecisionRecord(
            id=coverage_id,
            digest=_digest({"coverage": coverage_id, "entities": expected_entity_keys}),
            check_execution_id=execution_id,
            patrol_batch_id=batch_id,
            device_id=device_id,
            check_item_version_id=cpu_version_id,
            coverage_status="complete",
            coverage_basis="fixed",
            expected_entity_count=len(records),
            observed_entity_count=len(records),
            evaluated_entity_count=len(records),
            expected_entity_set_digest=_digest(expected_entity_keys),
            observed_entity_set_digest=_digest(expected_entity_keys),
            denominator_ref_json={
                "basis": "fixed",
                "inventory_snapshot_digest": inventory_digest,
            },
            missing_entity_sample_json=[],
            unexpected_entity_sample_json=[],
            reason_codes_json=[],
            policy_snapshot_json=coverage_policy,
            policy_digest=_digest(coverage_policy),
            evidence_refs_json=[snapshot_id],
            decided_at=now,
            created_at=now,
        )
        db.add_all([qualification, coverage])
        for index, (record, observation) in enumerate(zip(records, observation_payloads, strict=True)):
            db.add(
                StateObservationRecord(
                    id=str(observation["id"]),
                    idempotency_key=str(observation["id"]),
                    run_id=run_id,
                    snapshot_id=snapshot_id,
                    mode="parser_validation",
                    source="cli",
                    source_type="cli",
                    device_id=device_id,
                    vendor="H3C",
                    model="MSR5620",
                    canonical_command="display cpu",
                    execution_item_id=action_id,
                    entity_type="cpu_scope",
                    entity_key=json.dumps(["slot", str(record["slot"])]),
                    field_name="cpu_usage",
                    observation_type=CPU_OBSERVATION,
                    semantic_type="gauge",
                    unit="percent",
                    dimensions_json={
                        "scope": "slot",
                        "scope_id": str(record["slot"]),
                        "window": "5m",
                    },
                    value_json=float(record["cpu_5m"]),
                    value_text=str(record["cpu_5m"]),
                    value_number=float(record["cpu_5m"]),
                    value_type="number",
                    observation_kind="state",
                    observed_at=now,
                    fact_order_key=f"slot:{int(record['slot']):04d}",
                    parser_template_id=parser_id,
                    parser_version="1",
                    contract_version=1,
                    confidence=1.0,
                    metadata_json={"source_field": "cpu_5m", "record_index": index},
                    created_at=now,
                )
            )

        decision = CheckAssessmentService.assess(
            CheckAssessmentInput(
                execution_id=execution_id,
                patrol_batch_id=batch_id,
                device_id=device_id,
                check_item_version_id=cpu_version_id,
                execution_status="completed",
                assessment_mode="health",
                comparison_status="first_observation",
                observation_count=len(records),
                expected_entity_count=len(records),
                observed_entity_count=len(records),
                evaluated_entity_count=len(records),
                threshold_unknown_count=0,
                current_finding_ids=(),
                unresolved_risk_case_ids=(),
                rule_assets_ready=True,
                evidence_qualification_id=qualification_id,
                coverage_decision_id=coverage_id,
                source_identity_status="confirmed",
                output_quality_status="complete",
                freshness_status="fresh",
                empty_result_classification="not_empty",
                coverage_status="complete",
                coverage_basis="fixed",
                evidence_refs=(snapshot_id,),
                assessment_rule_bundle_digest=RUNTIME_BUNDLE_DIGEST,
                runtime_bundle_version_id=RUNTIME_BUNDLE_VERSION,
                assessment_policy_snapshot_digest=str(frozen_snapshot["digest"]),
                assessment_basis=assessment_basis,
                assessed_at=now,
            )
        )
        if decision.assessment_status != "healthy":
            raise AssertionError(f"report fixture did not assess healthy: {decision}")
        execution.assessment_id = decision.id
        db.add(CheckAssessmentRecord(**decision.values))

    return {
        "batch_id": batch_id,
        "run_id": run_id,
        "workflow_run_id": workflow_run_id,
        "snapshot_id": snapshot_id,
        "execution_id": execution_id,
        "parser_id": parser_id,
        "assessment_id": decision.id,
        "assessment_status": decision.assessment_status,
        "policy_snapshot_digest": str(frozen_snapshot["digest"]),
        "assessment_basis_digest": str(assessment_basis["digest"]),
        "assessment_basis_entry_count": len(entries),
        "source_sha256": source["source_sha256"],
    }


def _wait_report(
    client: httpx.Client,
    *,
    token: str,
    batch_id: str,
    task_id: str | None,
    timeout_seconds: int,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    latest: dict[str, Any] = {}
    task_state = "UNTRACKED"
    while time.monotonic() < deadline:
        if task_id:
            task_state = str(celery.AsyncResult(task_id).state)
        latest = _json_response(
            client.get(
                f"/api/patrol/batches/{batch_id}/reports/materialization-status",
                headers=_headers(token),
            )
        )
        if latest.get("status") == "completed" and (
            not task_id or task_state == "SUCCESS"
        ):
            return latest
        if task_state == "FAILURE" or latest.get("status") == "failed":
            raise AssertionError(f"report materialization failed: {task_state} {latest}")
        time.sleep(0.5)
    raise TimeoutError(f"report did not complete: celery={task_state}, api={latest}")


def _wait_celery_result(task_id: str, timeout_seconds: int) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    result = celery.AsyncResult(task_id)
    while time.monotonic() < deadline:
        if result.state == "SUCCESS":
            return dict(result.result or {})
        if result.state == "FAILURE":
            raise AssertionError(f"redelivery worker failed: {result.result}")
        time.sleep(0.5)
    raise TimeoutError(f"redelivery worker did not finish: {result.state}")


def _download_artifacts(
    client: httpx.Client,
    *,
    token: str,
    batch_id: str,
    manifest: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    paths = {
        "markdown": "report.md",
        "docx": "report.docx",
        "excel": "report.xlsx",
        "json": "report.json",
        "evidence": "evidence.zip",
    }
    expected = dict(manifest.get("artifact_sha256") or {})
    object_keys = dict(manifest.get("artifact_object_keys") or {})
    storage = get_minio_storage()
    artifacts: dict[str, Any] = {}
    report_json: dict[str, Any] = {}
    for name, path in paths.items():
        response = client.get(
            f"/api/workflows/runs/patrol-batches/{batch_id}/{path}",
            headers=_headers(token),
        )
        if response.status_code != 200:
            raise AssertionError(f"artifact {name} returned {response.status_code}: {response.text}")
        digest = hashlib.sha256(response.content).hexdigest()
        if digest != str(expected.get(name) or ""):
            raise AssertionError(f"artifact {name} digest differs from ReportManifest")
        object_bytes = storage.download_file(str(object_keys.get(name) or ""))
        if object_bytes is None or hashlib.sha256(object_bytes).hexdigest() != digest:
            raise AssertionError(f"artifact {name} differs between BFF and MinIO")
        artifacts[name] = {
            "bytes": len(response.content),
            "sha256": digest,
            "object_key": object_keys.get(name),
        }
        if name == "json":
            report_json = json.loads(response.content)
        if name == "evidence":
            with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
                entries = sorted(archive.namelist())
                combined = b"\n".join(archive.read(item) for item in entries)
                if b"Slot 0 CPU 0 CPU usage" in combined:
                    raise AssertionError("evidence index leaked raw CLI text")
                artifacts[name]["entries"] = entries
    return artifacts, report_json


def _materialize_and_verify_report(
    *,
    base_url: str,
    token: str,
    seed: dict[str, Any],
    suffix: str,
    timeout_seconds: int,
) -> dict[str, Any]:
    batch_id = str(seed["batch_id"])
    idempotency_key = f"s38-{suffix}-report-materialization"
    with httpx.Client(base_url=base_url.rstrip("/"), timeout=60) as client:
        dispatch = _json_response(
            client.post(
                f"/api/patrol/batches/{batch_id}/reports/materialize",
                headers=_headers(token, idempotency_key),
                json={},
            ),
            expected_status=202,
        )
        if dispatch.get("accepted") is not True and dispatch.get("reason") != "already_in_progress":
            raise AssertionError(f"report dispatch was not accepted: {dispatch}")
        if dispatch.get("accepted") is not True:
            _wait_report(
                client,
                token=token,
                batch_id=batch_id,
                task_id=None,
                timeout_seconds=timeout_seconds,
            )
            dispatch = _json_response(
                client.post(
                    f"/api/patrol/batches/{batch_id}/reports/materialize",
                    headers=_headers(token, idempotency_key),
                    json={},
                ),
                expected_status=202,
            )
            if dispatch.get("accepted") is not True:
                raise AssertionError(
                    f"explicit report regeneration was not accepted: {dispatch}"
                )
        replay = _json_response(
            client.post(
                f"/api/patrol/batches/{batch_id}/reports/materialize",
                headers=_headers(token, idempotency_key),
                json={},
            ),
            expected_status=202,
        )
        if replay.get("reason") != "idempotent_replay" or replay.get("accepted") is not False:
            raise AssertionError(f"in-flight API replay was not idempotent: {replay}")
        status = _wait_report(
            client,
            token=token,
            batch_id=batch_id,
            task_id=str(dispatch["task_id"]),
            timeout_seconds=timeout_seconds,
        )
        manifest_before = _json_response(
            client.get(
                f"/api/patrol/batches/{batch_id}/report-manifest",
                headers=_headers(token),
            )
        )
        artifacts, report_json = _download_artifacts(
            client,
            token=token,
            batch_id=batch_id,
            manifest=manifest_before,
        )
        redelivery_task_id = f"s38-report-redelivery-{suffix}"
        materialize_patrol_batch_report_task.apply_async(
            args=[batch_id, "scenario38:redelivery", str(dispatch["request_id"])],
            queue="netops.patrol",
            task_id=redelivery_task_id,
        )
        redelivery = _wait_celery_result(redelivery_task_id, timeout_seconds)
        if redelivery.get("reason") != "stale_report_request":
            raise AssertionError(f"completed worker redelivery was not fenced: {redelivery}")
        manifest_after = _json_response(
            client.get(
                f"/api/patrol/batches/{batch_id}/report-manifest",
                headers=_headers(token),
            )
        )

    immutable_fields = (
        "report_manifest_id",
        "source_facts_digest",
        "artifact_sha256",
        "artifact_object_keys",
        "claim_ledger_digest",
    )
    for field in immutable_fields:
        if manifest_after.get(field) != manifest_before.get(field):
            raise AssertionError(f"worker redelivery changed frozen report field {field}")
    if report_json.get("report_purpose") != "offline_historical":
        raise AssertionError("historical evidence was mislabeled as a production-current report")
    permission = dict(report_json.get("conclusion_permission") or {})
    if permission.get("overall_healthy_allowed") is not False:
        raise AssertionError("historical report unexpectedly received overall healthy permission")

    claims = list(dict(report_json.get("claim_ledger") or {}).get("claims") or [])
    assessment_claims = [
        claim for claim in claims if str(claim.get("claim_code") or "").startswith("assessment.")
    ]
    if len(assessment_claims) != 1:
        raise AssertionError(f"expected one assessment claim, got {len(assessment_claims)}")
    subject = dict(assessment_claims[0].get("subject") or {})
    if subject.get("assessment_policy_snapshot_digest") != seed["policy_snapshot_digest"]:
        raise AssertionError("ClaimLedger lost the frozen policy snapshot digest")
    if subject.get("assessment_basis_digest") != seed["assessment_basis_digest"]:
        raise AssertionError("ClaimLedger lost the frozen AssessmentBasis digest")
    if int(
        dict(subject.get("assessment_basis_summary") or {}).get("entry_count") or 0
    ) != int(seed["assessment_basis_entry_count"]):
        raise AssertionError("ClaimLedger lost per-slot policy evidence")

    return {
        "production_network_claim": False,
        "dispatch": dispatch,
        "materialization_status": status,
        "manifest": {
            "id": manifest_before.get("report_manifest_id"),
            "report_purpose": manifest_before.get("report_purpose"),
            "source_facts_digest": manifest_before.get("source_facts_digest"),
            "claim_ledger_digest": manifest_before.get("claim_ledger_digest"),
        },
        "artifacts": artifacts,
        "conclusion_permission": permission,
        "assessment_claim": {
            "claim_id": assessment_claims[0].get("claim_id"),
            "policy_snapshot_digest": subject.get("assessment_policy_snapshot_digest"),
            "assessment_basis_digest": subject.get("assessment_basis_digest"),
            "assessment_basis_summary": subject.get("assessment_basis_summary"),
        },
        "api_idempotent_replay": replay,
        "worker_redelivery": redelivery,
    }


def _cleanup(
    *,
    suffix: str,
    devices: dict[str, str],
    api: PolicyApi | None,
    usernames: list[str],
) -> dict[str, int]:
    batch_id = f"assessment-policy-s38-{suffix}"
    run_id = f"assessment-policy-s38-run-{suffix}"
    workflow_run_id = f"assessment-policy-s38-wf-{suffix}"
    parser_id = f"assessment-policy-s38-parser-{suffix}"
    deleted_objects = get_minio_storage().delete_objects_by_prefix(
        f"reports/patrol/{batch_id}"
    )
    version_ids = list(api.created_version_ids if api is not None else [])
    profile_ids = list(api.created_profile_ids if api is not None else [])
    binding_ids = list(api.created_binding_ids if api is not None else [])
    resource_ids = sorted(
        {
            batch_id,
            run_id,
            workflow_run_id,
            parser_id,
            *devices.values(),
            *version_ids,
            *profile_ids,
            *binding_ids,
        }
    )
    counts: dict[str, int] = {"objects": int(deleted_objects)}
    with get_db_session() as db:
        counts["audits"] = int(
            db.query(AuditLogRecord)
            .filter(AuditLogRecord.resource_id.in_(resource_ids))
            .delete(synchronize_session=False)
            if resource_ids
            else 0
        )
        counts["projections"] = int(
            db.query(PatrolBatchSummaryProjection)
            .filter(PatrolBatchSummaryProjection.patrol_batch_id == batch_id)
            .delete(synchronize_session=False)
        )
        counts["report_manifests"] = int(
            db.query(ReportManifestRecord)
            .filter(ReportManifestRecord.patrol_batch_id == batch_id)
            .delete(synchronize_session=False)
        )
        counts["executions"] = int(
            db.query(CheckExecutionRecord)
            .filter(CheckExecutionRecord.patrol_batch_id == batch_id)
            .delete(synchronize_session=False)
        )
        counts["runtime_manifests"] = int(
            db.query(CheckItemRuntimeManifestRecord)
            .filter(CheckItemRuntimeManifestRecord.patrol_batch_id == batch_id)
            .delete(synchronize_session=False)
        )
        counts["patrol_runs"] = int(
            db.query(PatrolRun).filter(PatrolRun.id == run_id).delete(synchronize_session=False)
        )
        counts["batches"] = int(
            db.query(PatrolBatchRecord)
            .filter(PatrolBatchRecord.patrol_batch_id == batch_id)
            .delete(synchronize_session=False)
        )
        counts["workflow_runs"] = int(
            db.query(WorkflowRun)
            .filter(WorkflowRun.id == workflow_run_id)
            .delete(synchronize_session=False)
        )
        counts["parsers"] = int(
            db.query(ParserTemplate)
            .filter(ParserTemplate.id == parser_id)
            .delete(synchronize_session=False)
        )
        if devices:
            device_ids = list(devices.values())
            counts["cutovers"] = int(
                db.query(AssessmentPolicyCutoverRecord)
                .filter(AssessmentPolicyCutoverRecord.device_id.in_(device_ids))
                .delete(synchronize_session=False)
            )
            counts["shadow_results"] = int(
                db.query(AssessmentPolicyShadowResultRecord)
                .filter(AssessmentPolicyShadowResultRecord.device_id.in_(device_ids))
                .delete(synchronize_session=False)
            )
            counts["migration_sources"] = int(
                db.query(AssessmentPolicyMigrationSourceRecord)
                .filter(AssessmentPolicyMigrationSourceRecord.device_id.in_(device_ids))
                .delete(synchronize_session=False)
            )
        else:
            counts["cutovers"] = 0
            counts["shadow_results"] = 0
            counts["migration_sources"] = 0
        counts["bindings"] = int(
            db.query(AssessmentPolicyBindingRecord)
            .filter(AssessmentPolicyBindingRecord.id.in_(binding_ids))
            .delete(synchronize_session=False)
            if binding_ids
            else 0
        )
        counts["decisions"] = int(
            db.query(AssessmentPolicyDecisionRecord)
            .filter(AssessmentPolicyDecisionRecord.policy_version_id.in_(version_ids))
            .delete(synchronize_session=False)
            if version_ids
            else 0
        )
        counts["policy_versions"] = int(
            db.query(AssessmentPolicyVersionRecord)
            .filter(AssessmentPolicyVersionRecord.id.in_(version_ids))
            .delete(synchronize_session=False)
            if version_ids
            else 0
        )
        counts["policy_profiles"] = int(
            db.query(AssessmentPolicyProfileRecord)
            .filter(AssessmentPolicyProfileRecord.id.in_(profile_ids))
            .delete(synchronize_session=False)
            if profile_ids
            else 0
        )
        counts["devices"] = int(
            db.query(NetworkDevice)
            .filter(NetworkDevice.id.in_(list(devices.values())))
            .delete(synchronize_session=False)
            if devices
            else 0
        )
        counts["postgres_sessions"] = int(
            db.query(UserSession)
            .filter(UserSession.username.in_(usernames))
            .delete(synchronize_session=False)
            if usernames
            else 0
        )
    counts["django_users"] = sum(_delete_test_user(username) for username in usernames)
    return counts


def run(
    *,
    base_url: str,
    source_file: Path,
    timeout_seconds: int,
    output_path: Path,
) -> dict[str, Any]:
    suffix = uuid.uuid4().hex[:12]
    users = {
        "author": (f"s38-author-{suffix}", f"S38-{uuid.uuid4().hex}"),
        "reviewer": (f"s38-reviewer-{suffix}", f"S38-{uuid.uuid4().hex}"),
        "publisher": (f"s38-publisher-{suffix}", f"S38-{uuid.uuid4().hex}"),
        "operator": (f"s38-operator-{suffix}", f"S38-{uuid.uuid4().hex}"),
    }
    created_usernames: list[str] = []
    devices: dict[str, str] = {}
    api: PolicyApi | None = None
    summary: dict[str, Any] = {
        "schema_version": "assessment-policy-scenario38-full-stack-e2e/v1",
        "success": False,
        "production_network_claim": False,
        "evidence_class": "engineering_integration",
        "base_url": base_url,
        "source_file": str(source_file),
        "started_at": _utcnow().isoformat(),
    }
    error: Exception | None = None
    try:
        if not source_file.is_file():
            raise FileNotFoundError(source_file)
        storage = get_minio_storage()
        if not storage.is_ready():
            raise AssertionError("MinIO is unavailable")
        worker_pings = celery.control.inspect(timeout=3).ping() or {}
        if not worker_pings:
            raise AssertionError("no Celery worker responded to inspect ping")
        summary["dependencies"] = {
            "minio": "ready",
            "celery_workers": sorted(worker_pings),
        }
        for role, (username, password) in users.items():
            _create_test_user(
                username,
                password,
                "operator" if role == "operator" else "admin",
            )
            created_usernames.append(username)
        devices = _seed_devices(suffix)
        source = _load_cpu_source(source_file)

        with httpx.Client(base_url=base_url.rstrip("/"), timeout=60) as client:
            tokens = {
                role: _login(client, username, password)
                for role, (username, password) in users.items()
            }
        api = PolicyApi(
            base_url=base_url,
            author_token=tokens["author"],
            reviewer_token=tokens["reviewer"],
            publisher_token=tokens["publisher"],
            operator_token=tokens["operator"],
            suffix=suffix,
        )
        matrix = _run_policy_matrix(api=api, devices=devices)
        report_seed = _seed_report(
            suffix=suffix,
            source_file=source_file,
            device_id=devices["healthy"],
            cpu_version_id=str(matrix["check_item_versions"]["cpu"]),
            frozen_snapshot=dict(matrix["frozen"]["snapshot"]),
            source=source,
        )
        report = _materialize_and_verify_report(
            base_url=base_url,
            token=tokens["publisher"],
            seed=report_seed,
            suffix=suffix,
            timeout_seconds=timeout_seconds,
        )
        summary.update(
            {
                "success": True,
                "completed_at": _utcnow().isoformat(),
                "devices": devices,
                "policy_matrix": matrix,
                "report_seed": report_seed,
                "report": report,
            }
        )
    except Exception as exc:
        error = exc
        summary["error"] = {"type": type(exc).__name__, "message": str(exc)}
    finally:
        try:
            summary["cleanup"] = _cleanup(
                suffix=suffix,
                devices=devices,
                api=api,
                usernames=created_usernames,
            )
        except Exception as cleanup_error:
            summary["cleanup_error"] = {
                "type": type(cleanup_error).__name__,
                "message": str(cleanup_error),
            }
            if error is None:
                error = cleanup_error
                summary["success"] = False
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
    if error is not None:
        raise error
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Validate assessment-policy scenario 38 through real BFF, API, "
            "PostgreSQL, Celery/Redis and MinIO"
        )
    )
    parser.add_argument(
        "--base-url",
        default=os.getenv("NETOPS_BFF_BASE_URL", "http://localhost:8001"),
    )
    parser.add_argument(
        "--source-file",
        type=Path,
        default=ROOT / "lixian" / "Q2" / "XA-FOTIC-RT5620-1-10.34.2.219.txt",
    )
    parser.add_argument("--timeout-seconds", type=int, default=180)
    parser.add_argument(
        "--output-path",
        type=Path,
        default=(
            ROOT
            / ".tmp"
            / "assessment-policy-scenario38-full-stack-e2e"
            / "summary.json"
        ),
    )
    args = parser.parse_args()
    result = run(
        base_url=str(args.base_url),
        source_file=args.source_file.resolve(),
        timeout_seconds=int(args.timeout_seconds),
        output_path=args.output_path.resolve(),
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
