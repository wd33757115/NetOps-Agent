#!/usr/bin/env python
"""Exercise first-observation, mixed failure and reduced fallback in one real batch.

The fixture uses a local Paramiko SSH endpoint, the production Netmiko collector,
the authenticated BFF, Workflow/Celery, PostgreSQL, Redis/RabbitMQ and MinIO. It
creates one isolated model-specific CPU RuntimeBundle with two declared commands
but only the lower-priority command executable. One target succeeds and produces
an at-risk first observation; the other target fails to connect. The same frozen
ReportManifest must disclose both outcomes and the reduced collection resilience.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import shutil
import socket
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
import paramiko
from openpyxl import load_workbook

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
from src.core.credentials import CredentialResolver  # noqa: E402
from src.core.observation.repository import (  # noqa: E402
    ObservationContractRepository,
)
from src.infrastructure.db.models import (  # noqa: E402
    AssetGapRecord,
    AuditLogRecord,
    ChangePolicyRecord,
    ChangePolicyVersionRecord,
    CheckAssessmentRecord,
    CheckExecutionRecord,
    CheckItemImplementationLinkRecord,
    CheckItemRuntimeBundleRecord,
    CheckItemRuntimeBundleVersionRecord,
    CheckItemRuntimeManifestRecord,
    CheckItemVersionRecord,
    CollectionExecutionPlanRecord,
    CollectionImplementationParserBindingRecord,
    CollectionImplementationRecord,
    CollectionImplementationVersionRecord,
    CollectionProfileBindingRecord,
    CommandProfileRecord,
    CommandProfileVersionRecord,
    CommandSnapshot,
    ComparisonContextRecord,
    CoverageDecisionRecord,
    CurrentStateFindingRecord,
    EventRuleRecord,
    EventRuleVersionRecord,
    EvidenceQualificationRecord,
    NetworkChangeRecord,
    NetworkChangeDecisionRecord,
    NetworkDevice,
    NetworkEventOccurrenceRecord,
    NetworkEventOutboxRecord,
    NetworkEventRecord,
    Notification,
    ObservationContractRecord,
    ParserTemplate,
    PatrolBatchDeviceRecord,
    PatrolBatchRecord,
    PatrolBatchSummaryProjection,
    PatrolCollectionResult,
    PatrolProfileCheckItemRecord,
    PatrolReportMaterializationRequestRecord,
    PatrolRun,
    RawEvidenceRecord,
    ReportReleaseRecord,
    ReportManifestRecord,
    RiskCaseRecord,
    RiskOccurrenceRecord,
    RiskRemediationRecord,
    RuntimeBundleAssetRecord,
    StateObservationRecord,
    WorkflowRun,
    WorkflowStepRecord,
)
from src.infrastructure.db.postgres import get_db_session  # noqa: E402
from src.infrastructure.storage.minio_client import get_minio_storage  # noqa: E402


CHECK_ITEM_ID = "system.cpu_usage"
OBSERVATION_TYPE = "device.cpu.utilization"
PRIMARY_COMMAND = "display cpu-usage"
FALLBACK_COMMAND = "display cpu"
WORKFLOW_TERMINAL = {"completed", "failed", "cancelled"}


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


class _SSHServerInterface(paramiko.ServerInterface):
    def __init__(self, *, username: str, password: str) -> None:
        self.username = username
        self.password = password
        self.shell_requested = threading.Event()

    def check_auth_password(self, username: str, password: str) -> int:
        if username == self.username and password == self.password:
            return paramiko.AUTH_SUCCESSFUL
        return paramiko.AUTH_FAILED

    def get_allowed_auths(self, _username: str) -> str:
        return "password"

    def check_channel_request(self, kind: str, _channel_id: int) -> int:
        if kind == "session":
            return paramiko.OPEN_SUCCEEDED
        return paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED

    def check_channel_pty_request(self, *_args: Any) -> bool:
        return True

    def check_channel_shell_request(self, _channel: Any) -> bool:
        self.shell_requested.set()
        return True


class LocalCLIServer:
    """Small Cisco-like SSH shell used by the production Netmiko adapter."""

    prompt = "E2E-CPU#"

    def __init__(self, *, username: str, password: str) -> None:
        self.username = username
        self.password = password
        self.host_key = paramiko.RSAKey.generate(2048)
        self.stop_event = threading.Event()
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.socket.bind(("127.0.0.1", 0))
        self.socket.listen(20)
        self.socket.settimeout(0.2)
        self.port = int(self.socket.getsockname()[1])
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.connections: list[threading.Thread] = []

    def start(self) -> None:
        self.thread.start()

    def close(self) -> None:
        self.stop_event.set()
        try:
            self.socket.close()
        except OSError:
            pass
        self.thread.join(timeout=2)
        for thread in self.connections:
            thread.join(timeout=2)

    def _serve(self) -> None:
        while not self.stop_event.is_set():
            try:
                connection, _address = self.socket.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            thread = threading.Thread(
                target=self._handle_connection,
                args=(connection,),
                daemon=True,
            )
            self.connections.append(thread)
            thread.start()

    def _handle_connection(self, connection: socket.socket) -> None:
        transport = paramiko.Transport(connection)
        channel = None
        try:
            transport.add_server_key(self.host_key)
            server = _SSHServerInterface(
                username=self.username,
                password=self.password,
            )
            transport.start_server(server=server)
            channel = transport.accept(10)
            if channel is None or not server.shell_requested.wait(5):
                return
            channel.send(self.prompt)
            buffer = ""
            while transport.is_active() and not self.stop_event.is_set():
                data = channel.recv(4096)
                if not data:
                    break
                buffer += data.decode("utf-8", errors="ignore")
                while "\r" in buffer or "\n" in buffer:
                    positions = [
                        position
                        for position in (buffer.find("\r"), buffer.find("\n"))
                        if position >= 0
                    ]
                    index = min(positions)
                    command = buffer[:index].strip()
                    buffer = buffer[index + 1 :].lstrip("\r\n")
                    self._reply(channel, command)
        except (EOFError, OSError, paramiko.SSHException):
            pass
        finally:
            if channel is not None:
                try:
                    channel.close()
                except (EOFError, OSError):
                    pass
            transport.close()

    def _reply(self, channel: Any, command: str) -> None:
        if not command:
            channel.send(f"\r\n{self.prompt}")
            return
        if command == FALLBACK_COMMAND:
            output = "Slot 0 CPU 5m: 91%"
        elif command == PRIMARY_COMMAND:
            output = "% Invalid input detected at '^' marker."
        else:
            output = ""
        channel.send(f"{command}\r\n{output}\r\n{self.prompt}")


def _unused_local_port() -> int:
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.bind(("127.0.0.1", 0))
    port = int(probe.getsockname()[1])
    probe.close()
    return port


def _active_asset_snapshot() -> dict[str, list[tuple[str, str]]]:
    with get_db_session() as db:
        return {
            "parsers": sorted(
                (str(row.id), str(row.sha256))
                for row in db.query(ParserTemplate)
                .filter(ParserTemplate.status == "active")
                .all()
            ),
            "contracts": sorted(
                (str(row.id), str(row.contract_sha256))
                for row in db.query(ObservationContractRecord)
                .filter(ObservationContractRecord.status == "active")
                .all()
            ),
            "policies": sorted(
                (str(row.id), str(row.digest))
                for row in db.query(ChangePolicyVersionRecord)
                .filter(ChangePolicyVersionRecord.status == "active")
                .all()
            ),
            "event_rules": sorted(
                (str(row.id), str(row.digest))
                for row in db.query(EventRuleVersionRecord)
                .filter(EventRuleVersionRecord.status == "active")
                .all()
            ),
            "runtime_bundles": sorted(
                (str(row.id), str(row.digest))
                for row in db.query(CheckItemRuntimeBundleVersionRecord)
                .filter(CheckItemRuntimeBundleVersionRecord.status == "active")
                .all()
            ),
        }


def _fixture_ids(suffix: str) -> dict[str, str]:
    return {
        "suffix": suffix,
        "model": f"E2E-CPU-FALLBACK-{suffix.upper()}",
        "family": f"E2E-Comware-{suffix}",
        "implementation": f"e2e-cpu-fallback-{suffix}",
        "implementation_version": f"e2e-cpu-fallback-version-{suffix}",
        "implementation_link": f"e2e-cpu-fallback-link-{suffix}",
        "parser": f"e2e-cpu-fallback-parser-{suffix}",
        "parser_binding": f"e2e-cpu-fallback-binding-{suffix}",
        "policy": f"e2e-cpu-fallback-policy-{suffix}",
        "policy_version": f"e2e-cpu-fallback-policy-version-{suffix}",
        "fault_rule": f"e2e-cpu-fallback-fault-{suffix}",
        "fault_rule_version": f"e2e-cpu-fallback-fault-version-{suffix}",
        "recovery_rule": f"e2e-cpu-fallback-recovery-{suffix}",
        "recovery_rule_version": f"e2e-cpu-fallback-recovery-version-{suffix}",
        "success_device": f"e2e-cpu-success-{suffix}",
        "failed_device": f"e2e-cpu-failed-{suffix}",
        "site": f"e2e-cpu-site-{suffix}",
    }


def _seed_runtime_assets(ids: dict[str, str], *, actor: str) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    catalog = CheckItemCatalogRepository().load_active()
    check_item = catalog.check_item(CHECK_ITEM_ID)
    if check_item is None:
        raise AssertionError("active system.cpu_usage check item is missing")
    source_implementation = next(
        item
        for item in catalog.implementations
        if item.id == "h3c-cpu-usage"
    )
    canonical_observation = next(
        item
        for item in check_item.canonical_contract.get("observations") or []
        if item.get("observation_type") == OBSERVATION_TYPE
    )
    template_text = (
        "Value slot (\\d+)\n"
        "Value cpu_5m (\\d+)\n"
        "\n"
        "Start\n"
        " ^Slot\\s+${slot}\\s+CPU\\s+5m:\\s+${cpu_5m}%\\s*$$ -> Record\n"
    )
    template_sha = hashlib.sha256(template_text.encode("utf-8")).hexdigest()
    validation = {
        "entity_type": "cpu_record",
        "primary_keys": ["slot"],
        "required_fields": ["slot", "cpu_5m"],
        "optional_fields": [],
        "validators": {
            "slot": {"type": "text"},
            "cpu_5m": {"type": "number"},
        },
        "allow_empty": False,
        "empty_patterns": [],
        "canonical_entity_type": "cpu_scope",
        "entity_identity_fields": ["scope", "scope_id"],
        "entity_dimensions": {
            "scope": {"literal": "slot"},
            "scope_id": {"field": "slot"},
        },
        "state_semantics": {
            "slot": {"semantic_type": "identity"},
            "cpu_5m": {
                "semantic_type": "gauge",
                "unit": "percent",
                "observation_type": OBSERVATION_TYPE,
                "dimensions": {"window": "5m"},
                "comparison_role": "observation",
                "monitor": True,
                "policy_constraints": dict(
                    canonical_observation.get("policy_constraints") or {}
                ),
                "event_constraints": dict(
                    canonical_observation.get("event_constraints") or {}
                ),
            },
        },
        "compatibility": {
            "manifest_version": 2,
            "scope": "model",
            "vendor": "H3C",
            "family": ids["family"],
            "families": [ids["family"]],
            "model": ids["model"],
            "models": [ids["model"]],
            "canonical_command": FALLBACK_COMMAND,
            "all_os_versions": True,
            "structure_signatures": ["e2e-slot-cpu-v1"],
        },
        "contract_approval_status": "approved",
        "contract_approved_by": actor,
        "sample_count": 1,
        "unique_sample_count": 1,
    }
    implementation_payload = {
        "id": ids["implementation"],
        "version": 1,
        "match": {
            "vendor": "H3C",
            "family": ids["family"],
            "models": [ids["model"]],
        },
        "commands": [
            {"command": PRIMARY_COMMAND, "priority": 100},
            {"command": FALLBACK_COMMAND, "priority": 50},
        ],
        "collector": "cli",
        "parser_requirement": "structured",
        "template_scope": "model",
        "output_quality_policy": dict(
            source_implementation.output_quality_policy
        ),
        "selection_priority": 10000,
    }

    with get_db_session() as db:
        check_version = db.get(CheckItemVersionRecord, check_item.version_id)
        if check_version is None:
            raise AssertionError("active system.cpu_usage version is missing")
        db.add(
            ParserTemplate(
                id=ids["parser"],
                vendor="H3C",
                model=ids["model"],
                family=ids["family"],
                profile=f"E2E-CPU-{ids['suffix']}",
                canonical_command=FALLBACK_COMMAND,
                category="cpu_record",
                version=f"e2e-{ids['suffix']}",
                asset_path=f"postgresql://parser-template/{ids['parser']}",
                template_text=template_text,
                sha256=template_sha,
                status="active",
                validation_json=validation,
                contract_version=1,
                source_job_id=None,
                created_at=now,
                updated_at=now,
                activated_at=now,
            )
        )
        db.add(
            CollectionImplementationRecord(
                id=ids["implementation"],
                name=f"E2E CPU fallback {ids['suffix']}",
                description="Isolated live Workflow fallback fixture.",
                status="active",
                current_version=1,
                created_at=now,
                updated_at=now,
            )
        )
        db.flush()
        db.add(
            CollectionImplementationVersionRecord(
                id=ids["implementation_version"],
                implementation_id=ids["implementation"],
                version=1,
                collector="cli",
                match_json=dict(implementation_payload["match"]),
                commands_json=list(implementation_payload["commands"]),
                parser_requirement="structured",
                template_scope="model",
                output_quality_policy_json=dict(
                    implementation_payload["output_quality_policy"]
                ),
                selection_priority=10000,
                status="active",
                digest=_digest(implementation_payload),
                created_by=actor,
                created_at=now,
                activated_at=now,
            )
        )
        db.flush()
        db.add(
            CheckItemImplementationLinkRecord(
                id=ids["implementation_link"],
                check_item_version_id=check_item.version_id,
                implementation_version_id=ids["implementation_version"],
                priority=10000,
                required=True,
                created_at=now,
            )
        )
        db.add(
            CollectionImplementationParserBindingRecord(
                id=ids["parser_binding"],
                implementation_version_id=ids["implementation_version"],
                canonical_command=FALLBACK_COMMAND,
                parser_template_id=ids["parser"],
                record_set="default",
                binding_role="fallback",
                output_signature="e2e-slot-cpu-v1",
                status="active",
                created_at=now,
                updated_at=now,
            )
        )

    with get_db_session() as db:
        contract = ObservationContractRepository().upsert_contract_row(
            db,
            source_type="cli",
            schema_id=ids["parser"],
            schema_ref=f"postgresql://parser-template/{ids['parser']}",
            category="cpu_record",
            validation=validation,
            version=1,
            source_template_id=ids["parser"],
            source_job_id=None,
            approval={"kind": "full_stack_e2e", "actor": actor},
            status="active",
            compatibility=dict(validation["compatibility"]),
        )
        parser = db.get(ParserTemplate, ids["parser"])
        assert parser is not None
        parser.contract_version = int(contract.version)
        parser.contract_sha256 = str(contract.contract_sha256)
        contract_id = str(contract.contract_id)

    policy_payload = {
        "observation_type": OBSERVATION_TYPE,
        "semantic_type": "gauge",
        "compare_mode": "threshold_and_deadband",
        "thresholds": {"high": 80},
        "recovery_threshold": 75,
        "deadband": 5,
        "dimension_selector": {"window": "5m"},
    }
    fault_condition = {
        "kind": "threshold",
        "observation_types": [OBSERVATION_TYPE],
        "entity_types": ["cpu_scope"],
        "fields": ["cpu_5m"],
        "gte": 80,
    }
    recovery_condition = {
        "kind": "threshold",
        "observation_types": [OBSERVATION_TYPE],
        "entity_types": ["cpu_scope"],
        "fields": ["cpu_5m"],
        "lte": 75,
    }
    with get_db_session() as db:
        db.add(
            ChangePolicyRecord(
                id=ids["policy"],
                name=f"e2e:device.cpu.utilization:{ids['suffix']}",
                observation_type=OBSERVATION_TYPE,
                description="Isolated live Workflow CPU policy.",
                status="active",
                current_version=1,
                created_at=now,
                updated_at=now,
            )
        )
        db.add(
            ChangePolicyVersionRecord(
                id=ids["policy_version"],
                policy_id=ids["policy"],
                version=1,
                observation_type=OBSERVATION_TYPE,
                semantic_type="gauge",
                compare_mode="threshold_and_deadband",
                policy_json=policy_payload,
                validation_report_json={
                    "success": True,
                    "source": "check_item_patrol_workflow_full_stack_e2e",
                },
                status="active",
                source_job_id=None,
                digest=_digest(policy_payload),
                created_by=actor,
                created_at=now,
                activated_at=now,
            )
        )
        db.add_all(
            [
                EventRuleRecord(
                    id=ids["fault_rule"],
                    name=f"e2e:system.cpu_usage.high:{ids['suffix']}",
                    description="CPU is at or above the approved threshold.",
                    status="active",
                    current_version=1,
                    created_at=now,
                    updated_at=now,
                ),
                EventRuleRecord(
                    id=ids["recovery_rule"],
                    name=f"e2e:system.cpu_usage.recovered:{ids['suffix']}",
                    description="CPU has recovered below the approved threshold.",
                    status="active",
                    current_version=1,
                    created_at=now,
                    updated_at=now,
                ),
            ]
        )
        db.flush()
        db.add_all(
            [
                EventRuleVersionRecord(
                    id=ids["fault_rule_version"],
                    rule_id=ids["fault_rule"],
                    version=1,
                    event_type="CpuUtilizationHigh",
                    severity="warning",
                    condition_json=fault_condition,
                    priority=100,
                    exclusive=True,
                    event_class="fault",
                    evaluation_scope="both",
                    recovery_json={},
                    correlation_key_json=[
                        "device_id",
                        "entity_type",
                        "entity_key",
                    ],
                    status="active",
                    source_job_id=None,
                    validation_report_json={"success": True},
                    digest=_digest(fault_condition),
                    created_by=actor,
                    created_at=now,
                    activated_at=now,
                ),
                EventRuleVersionRecord(
                    id=ids["recovery_rule_version"],
                    rule_id=ids["recovery_rule"],
                    version=1,
                    event_type="CpuUtilizationRecovered",
                    severity="info",
                    condition_json=recovery_condition,
                    priority=90,
                    exclusive=True,
                    event_class="recovery",
                    evaluation_scope="change",
                    recovery_json={"resolves": ["CpuUtilizationHigh"]},
                    correlation_key_json=[
                        "device_id",
                        "entity_type",
                        "entity_key",
                    ],
                    status="active",
                    source_job_id=None,
                    validation_report_json={"success": True},
                    digest=_digest(
                        {
                            "condition": recovery_condition,
                            "resolves": ["CpuUtilizationHigh"],
                        }
                    ),
                    created_by=actor,
                    created_at=now,
                    activated_at=now,
                ),
            ]
        )

    service = CheckItemRuntimeBundleService()
    preview = service.preview_assembly(
        check_item_version_id=check_item.version_id,
        implementation_version_id=ids["implementation_version"],
        parser_template_ids=[ids["parser"]],
        observation_contract_ids=[contract_id],
        change_policy_version_ids=[ids["policy_version"]],
        event_rule_version_ids=[
            ids["fault_rule_version"],
            ids["recovery_rule_version"],
        ],
    )
    if preview.get("operationally_ready") is not True:
        raise AssertionError(
            "isolated CPU RuntimeBundle preview is blocked: "
            + json.dumps(preview.get("blockers") or [], ensure_ascii=False)
        )
    if preview.get("fallback_resilience") != "reduced":
        raise AssertionError(
            f"expected reduced fallback resilience, got {preview}"
        )
    routes = {
        str(item.get("canonical_command") or ""): item
        for item in preview.get("command_routes") or []
    }
    if (
        str(routes.get(PRIMARY_COMMAND, {}).get("status")) == "executable"
        or str(routes.get(FALLBACK_COMMAND, {}).get("status")) != "executable"
    ):
        raise AssertionError(f"unexpected command route readiness: {routes}")
    draft = service.assemble(
        check_item_version_id=check_item.version_id,
        implementation_version_id=ids["implementation_version"],
        preview_digest=str(preview["preview_digest"]),
        actor=f"e2e-builder:{ids['suffix']}",
        parser_template_ids=[ids["parser"]],
        observation_contract_ids=[contract_id],
        change_policy_version_ids=[ids["policy_version"]],
        event_rule_version_ids=[
            ids["fault_rule_version"],
            ids["recovery_rule_version"],
        ],
    )
    active = service.activate(
        str(draft["version_id"]),
        actor=f"e2e-approver:{ids['suffix']}",
        reason="isolated live Workflow fallback verification",
    )
    return {
        "check_item_version_id": check_item.version_id,
        "contract_id": contract_id,
        "runtime_bundle_id": str(active["bundle_id"]),
        "runtime_bundle_version_id": str(active["version_id"]),
        "runtime_bundle_digest": str(active["digest"]),
        "fallback_resilience": preview["fallback_resilience"],
        "command_routes": list(preview.get("command_routes") or []),
    }


def _seed_devices(
    ids: dict[str, str],
    *,
    success_port: int,
    failed_port: int,
) -> None:
    common_attributes = {
        "netmiko_device_type": "cisco_ios",
        "connection_attempts": 1,
        "connection_retry_delay": 0,
        "conn_timeout": 2,
        "auth_timeout": 2,
        "banner_timeout": 2,
        "read_timeout": 10,
    }
    now = datetime.now(timezone.utc)
    with get_db_session() as db:
        for device_id, name, port in (
            (
                ids["success_device"],
                f"E2E-CPU-SUCCESS-{ids['suffix']}",
                success_port,
            ),
            (
                ids["failed_device"],
                f"E2E-CPU-FAILED-{ids['suffix']}",
                failed_port,
            ),
        ):
            db.add(
                NetworkDevice(
                    id=device_id,
                    device_name=name,
                    management_ip="127.0.0.1",
                    username="e2e-admin",
                    credential_ref="env:NETOPS_PASSWORD",
                    vendor="H3C",
                    model=ids["model"],
                    family=ids["family"],
                    platform="comware",
                    device_type="hp_comware",
                    site=ids["site"],
                    role="router",
                    status="active",
                    attributes_json={**common_attributes, "port": port},
                    created_at=now,
                    updated_at=now,
                )
            )


def _create_user(username: str, password: str) -> None:
    user = get_user_model().objects.create_user(
        username=username,
        email=f"{username}@example.invalid",
        password=password,
    )
    assign_user_role(user, "admin")
    user.save()


def _wait_workflows(
    batch_id: str,
    *,
    timeout_seconds: int,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        with get_db_session() as db:
            targets = (
                db.query(PatrolBatchDeviceRecord)
                .filter(PatrolBatchDeviceRecord.patrol_batch_id == batch_id)
                .order_by(PatrolBatchDeviceRecord.device_id)
                .all()
            )
            run_ids = [
                str(item.workflow_run_id)
                for item in targets
                if item.workflow_run_id
            ]
            runs = (
                db.query(WorkflowRun)
                .filter(WorkflowRun.id.in_(run_ids))
                .all()
                if run_ids
                else []
            )
            status_by_id = {str(item.id): str(item.status) for item in runs}
            last = {
                "target_count": len(targets),
                "targets": [
                    {
                        "device_id": str(item.device_id),
                        "dispatch_status": str(item.dispatch_status),
                        "workflow_run_id": str(item.workflow_run_id or ""),
                        "workflow_status": status_by_id.get(
                            str(item.workflow_run_id or ""),
                            "pending",
                        ),
                        "workflow_error": next(
                            (
                                str(run.error_message or "")
                                for run in runs
                                if str(run.id)
                                == str(item.workflow_run_id or "")
                            ),
                            "",
                        ),
                    }
                    for item in targets
                ],
            }
        if (
            len(last["targets"]) == 2
            and all(item["workflow_run_id"] for item in last["targets"])
            and all(
                item["workflow_status"] in WORKFLOW_TERMINAL
                for item in last["targets"]
            )
        ):
            return last
        time.sleep(0.5)
    raise TimeoutError(f"patrol workflows did not finish: {last}")


def _wait_report(
    client: httpx.Client,
    *,
    batch_id: str,
    task_id: str,
    timeout_seconds: int,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        last = _request_json(
            client,
            "GET",
            f"/api/patrol/batches/{batch_id}/reports/materialization-status",
        )
        if last.get("status") == "completed":
            return last
        if last.get("status") == "failed":
            raise AssertionError(f"report task {task_id} failed: {last}")
        time.sleep(0.5)
    raise TimeoutError(f"report task {task_id} did not finish: {last}")


def _database_evidence(
    ids: dict[str, str],
    *,
    batch_id: str,
) -> dict[str, Any]:
    with get_db_session() as db:
        patrol_runs = [
            item
            for item in db.query(PatrolRun).all()
            if str(
                (item.metadata_json or {}).get("patrol_batch_id") or ""
            )
            == batch_id
        ]
        run_ids = [str(item.id) for item in patrol_runs]
        collection_results = (
            db.query(PatrolCollectionResult)
            .filter(PatrolCollectionResult.run_id.in_(run_ids))
            .order_by(PatrolCollectionResult.inventory_device_id)
            .all()
            if run_ids
            else []
        )
        executions = (
            db.query(CheckExecutionRecord)
            .filter(CheckExecutionRecord.patrol_batch_id == batch_id)
            .order_by(CheckExecutionRecord.device_id)
            .all()
        )
        assessments = (
            db.query(CheckAssessmentRecord)
            .filter(CheckAssessmentRecord.patrol_batch_id == batch_id)
            .order_by(CheckAssessmentRecord.device_id)
            .all()
        )
        execution_ids = [str(item.id) for item in executions]
        snapshots = (
            db.query(CommandSnapshot)
            .filter(
                CommandSnapshot.inventory_device_id.in_(
                    [ids["success_device"], ids["failed_device"]]
                )
            )
            .filter(CommandSnapshot.canonical_command == FALLBACK_COMMAND)
            .all()
        )
        findings = (
            db.query(CurrentStateFindingRecord)
            .filter(CurrentStateFindingRecord.patrol_batch_id == batch_id)
            .all()
        )
        risks = (
            db.query(RiskCaseRecord)
            .filter(RiskCaseRecord.device_id.in_(
                [ids["success_device"], ids["failed_device"]]
            ))
            .all()
        )
        changes = (
            db.query(NetworkChangeRecord)
            .filter(NetworkChangeRecord.check_execution_id.in_(execution_ids))
            .all()
            if execution_ids
            else []
        )
        events = (
            db.query(NetworkEventRecord)
            .filter(NetworkEventRecord.check_execution_id.in_(execution_ids))
            .all()
            if execution_ids
            else []
        )
        event_ids = [str(item.id) for item in events]
        occurrences = (
            db.query(NetworkEventOccurrenceRecord)
            .filter(NetworkEventOccurrenceRecord.event_id.in_(event_ids))
            .all()
            if event_ids
            else []
        )
        runtime_manifest = (
            db.query(CheckItemRuntimeManifestRecord)
            .filter(CheckItemRuntimeManifestRecord.patrol_batch_id == batch_id)
            .one()
        )
        report_manifest = (
            db.query(ReportManifestRecord)
            .filter(ReportManifestRecord.patrol_batch_id == batch_id)
            .order_by(ReportManifestRecord.version.desc())
            .first()
        )
        if report_manifest is None:
            raise AssertionError("report manifest was not persisted")
        return {
            "patrol_runs": [item.to_dict() for item in patrol_runs],
            "collection_results": [
                item.to_dict() for item in collection_results
            ],
            "executions": [item.to_dict() for item in executions],
            "assessments": [item.to_dict() for item in assessments],
            "snapshots": [item.to_dict() for item in snapshots],
            "findings": [item.to_dict() for item in findings],
            "risks": [item.to_dict() for item in risks],
            "change_count": len(changes),
            "event_occurrence_count": len(occurrences),
            "runtime_manifest": dict(runtime_manifest.manifest_json or {}),
            "report_source_facts": dict(
                report_manifest.source_facts_json or {}
            ),
        }


def _verify_report_artifacts(
    client: httpx.Client,
    *,
    batch_id: str,
    manifest: dict[str, Any],
) -> dict[str, Any]:
    paths = {
        "markdown": "report.md",
        "docx": "report.docx",
        "excel": "report.xlsx",
        "json": "report.json",
        "evidence": "evidence.zip",
    }
    expected = dict(manifest.get("artifact_sha256") or {})
    downloads: dict[str, bytes] = {}
    summaries: dict[str, Any] = {}
    for name, suffix in paths.items():
        response = client.get(
            f"/api/workflows/runs/patrol-batches/{batch_id}/{suffix}"
        )
        if response.status_code != 200:
            raise AssertionError(
                f"download {name} returned {response.status_code}: {response.text}"
            )
        digest = hashlib.sha256(response.content).hexdigest()
        if digest != str(expected.get(name) or ""):
            raise AssertionError(
                f"{name} bytes differ from the frozen ReportManifest"
            )
        downloads[name] = response.content
        summaries[name] = {"bytes": len(response.content), "sha256": digest}

    report = json.loads(downloads["json"])
    if report.get("assessment_counts") != {
        "at_risk": 1,
        "unable_to_determine": 1,
    }:
        raise AssertionError(
            f"mixed device conclusion was not preserved: {report.get('assessment_counts')}"
        )
    if int((report.get("collection_resilience") or {}).get("reduced_count") or 0) != 2:
        raise AssertionError(
            f"reduced fallback was not disclosed: {report.get('collection_resilience')}"
        )
    matrix = list(report.get("matrix") or [])
    if len(matrix) != 2 or not all(
        item.get("fallback_resilience") == "reduced" for item in matrix
    ):
        raise AssertionError(f"report matrix lost fallback evidence: {matrix}")
    if not all(
        any(
            route.get("command") == PRIMARY_COMMAND
            and route.get("reason_code") == "asset_unavailable_at_plan_time"
            for route in item.get("unavailable_commands") or []
        )
        for item in matrix
    ):
        raise AssertionError(f"report matrix lost unavailable routes: {matrix}")
    markdown = downloads["markdown"].decode("utf-8")
    if "采集路径韧性" not in markdown or "无法判断" not in markdown:
        raise AssertionError("Markdown report omitted resilience or unknown scope")
    workbook = load_workbook(io.BytesIO(downloads["excel"]), read_only=True)
    try:
        values = {
            str(cell)
            for sheet in workbook.worksheets
            for row in sheet.iter_rows(values_only=True)
            for cell in row
            if cell not in (None, "")
        }
    finally:
        workbook.close()
    if "采集路径韧性" not in values or "韧性降低" not in values:
        raise AssertionError("Excel report omitted reduced fallback evidence")
    summaries["report"] = {
        "conclusion": report.get("conclusion"),
        "assessment_counts": report.get("assessment_counts"),
        "collection_resilience": report.get("collection_resilience"),
        "matrix": matrix,
    }
    return summaries


def _delete_user(username: str) -> int:
    deleted, _detail = get_user_model().objects.filter(
        username=username
    ).delete()
    return int(deleted)


def _cleanup_local_outputs(ids: dict[str, str]) -> int:
    output_root = (ROOT / "src" / "skills" / "shared" / "outputs").resolve()
    if not output_root.exists():
        return 0
    target_names = {
        f"E2E-CPU-SUCCESS-{ids['suffix']}",
        f"E2E-CPU-FAILED-{ids['suffix']}",
    }
    removed = 0
    for candidate in list(output_root.rglob("*")):
        if not candidate.is_dir() or candidate.name not in target_names:
            continue
        resolved = candidate.resolve()
        if output_root not in resolved.parents:
            raise AssertionError(
                f"refusing to remove output outside test root: {resolved}"
            )
        shutil.rmtree(resolved)
        removed += 1
    return removed


def _cleanup(
    ids: dict[str, str],
    *,
    username: str,
    profile_version_id: str | None,
    runtime: dict[str, Any],
    batch_id: str | None,
) -> dict[str, int]:
    counts: dict[str, int] = {}
    if batch_id:
        counts["objects"] = int(
            get_minio_storage().delete_objects_by_prefix(
                f"reports/patrol/{batch_id}"
            )
        )
    else:
        counts["objects"] = 0

    profile_id: str | None = None
    with get_db_session() as db:
        if profile_version_id:
            profile = db.get(CommandProfileVersionRecord, profile_version_id)
            if profile is not None:
                profile_id = str(profile.profile_id)

        workflow_ids: list[str] = []
        if batch_id:
            workflow_ids = [
                str(item.workflow_run_id)
                for item in db.query(PatrolBatchDeviceRecord)
                .filter(PatrolBatchDeviceRecord.patrol_batch_id == batch_id)
                .all()
                if item.workflow_run_id
            ]
        run_ids = [
            str(item.id)
            for item in db.query(PatrolRun).all()
            if (
                bool(batch_id)
                and str(
                    (item.metadata_json or {}).get("patrol_batch_id") or ""
                )
                == str(batch_id)
                or str((item.metadata_json or {}).get("device_id") or "")
                in {ids["success_device"], ids["failed_device"]}
            )
        ]
        snapshot_rows = (
            db.query(CommandSnapshot)
            .filter(
                CommandSnapshot.inventory_device_id.in_(
                    [ids["success_device"], ids["failed_device"]]
                )
            )
            .all()
        )
        run_ids = sorted(
            {
                *run_ids,
                *(str(item.run_id) for item in snapshot_rows if item.run_id),
            }
        )
        execution_rows = (
            db.query(CheckExecutionRecord)
            .filter(CheckExecutionRecord.patrol_batch_id == batch_id)
            .all()
            if batch_id
            else []
        )
        execution_ids = [str(item.id) for item in execution_rows]
        comparison_ids = sorted(
            {
                str(item.comparison_context_id)
                for item in execution_rows
                if item.comparison_context_id
            }
        )
        risk_ids = [
            str(item.id)
            for item in db.query(RiskCaseRecord)
            .filter(RiskCaseRecord.check_item_id == CHECK_ITEM_ID)
            .filter(
                RiskCaseRecord.device_id.in_(
                    [ids["success_device"], ids["failed_device"]]
                )
            )
            .all()
        ]
        event_ids = (
            [
                str(item.id)
                for item in db.query(NetworkEventRecord)
                .filter(
                    (NetworkEventRecord.check_execution_id.in_(execution_ids))
                    | (NetworkEventRecord.run_id.in_(run_ids))
                )
                .all()
            ]
            if execution_ids or run_ids
            else []
        )

        if risk_ids:
            counts["risk_remediations"] = int(
                db.query(RiskRemediationRecord)
                .filter(RiskRemediationRecord.risk_case_id.in_(risk_ids))
                .delete(synchronize_session=False)
            )
            counts["risk_occurrences"] = int(
                db.query(RiskOccurrenceRecord)
                .filter(RiskOccurrenceRecord.risk_case_id.in_(risk_ids))
                .delete(synchronize_session=False)
            )
            counts["risk_cases"] = int(
                db.query(RiskCaseRecord)
                .filter(RiskCaseRecord.id.in_(risk_ids))
                .delete(synchronize_session=False)
            )
        if event_ids:
            counts["event_outbox"] = int(
                db.query(NetworkEventOutboxRecord)
                .filter(NetworkEventOutboxRecord.event_id.in_(event_ids))
                .delete(synchronize_session=False)
            )
            counts["event_occurrences"] = int(
                db.query(NetworkEventOccurrenceRecord)
                .filter(NetworkEventOccurrenceRecord.event_id.in_(event_ids))
                .delete(synchronize_session=False)
            )
            counts["events"] = int(
                db.query(NetworkEventRecord)
                .filter(NetworkEventRecord.id.in_(event_ids))
                .delete(synchronize_session=False)
            )
        if execution_ids:
            counts["asset_gaps"] = int(
                db.query(AssetGapRecord)
                .filter(AssetGapRecord.check_execution_id.in_(execution_ids))
                .delete(synchronize_session=False)
            )
            counts["changes"] = int(
                db.query(NetworkChangeRecord)
                .filter(NetworkChangeRecord.check_execution_id.in_(execution_ids))
                .delete(synchronize_session=False)
            )
            counts["findings"] = int(
                db.query(CurrentStateFindingRecord)
                .filter(CurrentStateFindingRecord.check_execution_id.in_(execution_ids))
                .delete(synchronize_session=False)
            )
            counts["executions"] = int(
                db.query(CheckExecutionRecord)
                .filter(CheckExecutionRecord.id.in_(execution_ids))
                .delete(synchronize_session=False)
            )
        if run_ids:
            counts["change_decisions"] = int(
                db.query(NetworkChangeDecisionRecord)
                .filter(NetworkChangeDecisionRecord.run_id.in_(run_ids))
                .delete(synchronize_session=False)
            )
            counts["patrol_runs"] = int(
                db.query(PatrolRun)
                .filter(PatrolRun.id.in_(run_ids))
                .delete(synchronize_session=False)
            )
        if comparison_ids:
            counts["comparison_contexts"] = int(
                db.query(ComparisonContextRecord)
                .filter(ComparisonContextRecord.id.in_(comparison_ids))
                .delete(synchronize_session=False)
            )
        if batch_id:
            counts["projections"] = int(
                db.query(PatrolBatchSummaryProjection)
                .filter(PatrolBatchSummaryProjection.patrol_batch_id == batch_id)
                .delete(synchronize_session=False)
            )
            manifest_ids = [
                str(item.id)
                for item in db.query(ReportManifestRecord)
                .filter(ReportManifestRecord.patrol_batch_id == batch_id)
                .all()
            ]
            if manifest_ids:
                counts["report_releases"] = int(
                    db.query(ReportReleaseRecord)
                    .filter(ReportReleaseRecord.report_manifest_id.in_(manifest_ids))
                    .delete(synchronize_session=False)
                )
            counts["report_manifests"] = int(
                db.query(ReportManifestRecord)
                .filter(ReportManifestRecord.patrol_batch_id == batch_id)
                .delete(synchronize_session=False)
            )
            counts["report_requests"] = int(
                db.query(PatrolReportMaterializationRequestRecord)
                .filter(
                    PatrolReportMaterializationRequestRecord.patrol_batch_id
                    == batch_id
                )
                .delete(synchronize_session=False)
            )
            counts["runtime_manifests"] = int(
                db.query(CheckItemRuntimeManifestRecord)
                .filter(CheckItemRuntimeManifestRecord.patrol_batch_id == batch_id)
                .delete(synchronize_session=False)
            )
            counts["execution_plans"] = int(
                db.query(CollectionExecutionPlanRecord)
                .filter(CollectionExecutionPlanRecord.patrol_batch_id == batch_id)
                .delete(synchronize_session=False)
            )
            counts["batch"] = int(
                db.query(PatrolBatchRecord)
                .filter(PatrolBatchRecord.patrol_batch_id == batch_id)
                .delete(synchronize_session=False)
            )
        if workflow_ids:
            counts["notifications"] = int(
                db.query(Notification)
                .filter(Notification.workflow_run_id.in_(workflow_ids))
                .delete(synchronize_session=False)
            )
            counts["workflow_steps"] = int(
                db.query(WorkflowStepRecord)
                .filter(WorkflowStepRecord.run_id.in_(workflow_ids))
                .delete(synchronize_session=False)
            )
            counts["workflow_runs"] = int(
                db.query(WorkflowRun)
                .filter(WorkflowRun.id.in_(workflow_ids))
                .delete(synchronize_session=False)
            )

        if profile_id:
            counts["profiles"] = int(
                db.query(CommandProfileRecord)
                .filter(CommandProfileRecord.id == profile_id)
                .delete(synchronize_session=False)
            )

        bundle_id = str(runtime.get("runtime_bundle_id") or "")
        if bundle_id:
            counts["runtime_bundles"] = int(
                db.query(CheckItemRuntimeBundleRecord)
                .filter(CheckItemRuntimeBundleRecord.id == bundle_id)
                .delete(synchronize_session=False)
            )
        counts["implementations"] = int(
            db.query(CollectionImplementationRecord)
            .filter(CollectionImplementationRecord.id == ids["implementation"])
            .delete(synchronize_session=False)
        )
        contract_id = str(runtime.get("contract_id") or "")
        if contract_id:
            counts["contracts"] = int(
                db.query(ObservationContractRecord)
                .filter(ObservationContractRecord.id == contract_id)
                .delete(synchronize_session=False)
            )
        counts["policies"] = int(
            db.query(ChangePolicyRecord)
            .filter(ChangePolicyRecord.id == ids["policy"])
            .delete(synchronize_session=False)
        )
        counts["event_rules"] = int(
            db.query(EventRuleRecord)
            .filter(
                EventRuleRecord.id.in_(
                    [ids["fault_rule"], ids["recovery_rule"]]
                )
            )
            .delete(synchronize_session=False)
        )
        counts["parsers"] = int(
            db.query(ParserTemplate)
            .filter(ParserTemplate.id == ids["parser"])
            .delete(synchronize_session=False)
        )
        counts["devices"] = int(
            db.query(NetworkDevice)
            .filter(
                NetworkDevice.id.in_(
                    [ids["success_device"], ids["failed_device"]]
                )
            )
            .delete(synchronize_session=False)
        )
        counts["audits"] = int(
            db.query(AuditLogRecord)
            .filter(
                (AuditLogRecord.username == f"api:{username}")
                | AuditLogRecord.resource_id.contains(ids["suffix"])
                | AuditLogRecord.idempotency_key.contains(ids["suffix"])
            )
            .delete(synchronize_session=False)
        )

    counts["django_rows"] = _delete_user(username)
    counts["local_output_directories"] = _cleanup_local_outputs(ids)
    return counts


def _assert_effective_plan(
    payload: dict[str, Any],
    *,
    profile_version_id: str,
) -> dict[str, Any]:
    plan = dict(payload.get("plan") or {})
    effective = dict(payload.get("check_item_effective_plan") or {})
    decisions = list(effective.get("decisions") or [])
    if plan.get("profile_version_id") != profile_version_id:
        raise AssertionError("Effective Plan did not freeze the active Profile")
    if payload.get("commands") != [FALLBACK_COMMAND]:
        raise AssertionError(
            f"Effective Plan did not select only the executable fallback: {payload}"
        )
    if len(decisions) != 1:
        raise AssertionError(f"expected one check-item decision: {decisions}")
    decision = decisions[0]
    if (
        decision.get("status") != "resolved"
        or decision.get("fallback_resilience") != "reduced"
        or int(decision.get("declared_command_count") or 0) != 2
        or int(decision.get("executable_command_count") or 0) != 1
    ):
        raise AssertionError(f"fallback route facts are incorrect: {decision}")
    unavailable = list(decision.get("unavailable_commands") or [])
    if not any(
        item.get("command") == PRIMARY_COMMAND
        and item.get("reason_code") == "asset_unavailable_at_plan_time"
        for item in unavailable
    ):
        raise AssertionError(f"primary route gap was not preserved: {unavailable}")
    return {
        "commands": list(payload.get("commands") or []),
        "runtime_manifest_digest": plan.get("runtime_manifest_digest"),
        "decision": decision,
    }


def run(
    *,
    base_url: str,
    timeout_seconds: int,
    output_path: Path,
) -> dict[str, Any]:
    suffix = uuid.uuid4().hex[:12]
    ids = _fixture_ids(suffix)
    username = f"patrol-workflow-e2e-{suffix}"
    user_password = f"PatrolWorkflow-{uuid.uuid4().hex}!aA1"
    device_password = CredentialResolver().resolve_password(
        "env:NETOPS_PASSWORD"
    )
    if not device_password:
        raise AssertionError("NETOPS_PASSWORD is required for the live SSH fixture")
    profile_name = f"E2E CPU fallback patrol {suffix}"
    profile_version_id: str | None = None
    batch_id: str | None = None
    runtime: dict[str, Any] = {}
    before_assets = _active_asset_snapshot()
    server = LocalCLIServer(
        username="e2e-admin",
        password=device_password,
    )
    summary: dict[str, Any] = {
        "schema_version": "check-item-patrol-workflow-full-stack-e2e/v1",
        "success": False,
        "production_network_claim": False,
        "base_url": base_url,
        "suffix": suffix,
    }
    server.start()
    try:
        runtime = _seed_runtime_assets(ids, actor=f"e2e:{suffix}")
        failed_port = _unused_local_port()
        _seed_devices(
            ids,
            success_port=server.port,
            failed_port=failed_port,
        )
        _create_user(username, user_password)
        with httpx.Client(base_url=base_url.rstrip("/"), timeout=90) as client:
            token = _login(client, username=username, password=user_password)
            client.headers["Authorization"] = f"Bearer {token}"

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
            impact = _request_json(
                client,
                "POST",
                "/api/parser/command-profiles/impact-preview/",
                json_body={
                    "profile_version_id": profile_version_id,
                    "selector": {"site": ids["site"]},
                    "mode": "production",
                    "save_evidence": True,
                    "limit": 10,
                },
            )
            if (
                int(impact.get("matched_device_count") or 0) != 2
                or int(impact.get("ready_check_item_count") or 0) != 2
                or int(impact.get("blocked_check_item_count") or 0) != 0
            ):
                raise AssertionError(f"Profile impact preview is not ready: {impact}")
            _request_json(
                client,
                "POST",
                f"/api/parser/command-profiles/{profile_version_id}/activate/",
                json_body={"reason": "isolated full-stack Workflow evidence"},
            )
            binding = _request_json(
                client,
                "POST",
                "/api/parser/command-profile-bindings/",
                json_body={
                    "profile_version_id": profile_version_id,
                    "selector": {"site": ids["site"]},
                    "priority": 1,
                    "status": "disabled",
                },
            )
            _request_json(
                client,
                "POST",
                (
                    "/api/parser/command-profile-bindings/"
                    f"{binding['binding_id']}/enable/"
                ),
                json_body={"reason": "bind isolated full-stack devices"},
            )
            effective_plans = {
                key: _assert_effective_plan(
                    _request_json(
                        client,
                        "POST",
                        f"/api/devices/{ids[key]}/effective-plan/",
                        json_body={"action": "patrol", "mode": "production"},
                    ),
                    profile_version_id=profile_version_id,
                )
                for key in ("success_device", "failed_device")
            }
            connectivity = _request_json(
                client,
                "POST",
                (
                    f"/api/devices/{ids['success_device']}/"
                    "connectivity-test/"
                ),
                json_body={},
            )
            if connectivity.get("success") is not True:
                raise AssertionError(
                    f"production connectivity test failed: {connectivity}"
                )

            launch = _request_json(
                client,
                "POST",
                "/api/devices/patrol/launch/",
                json_body={
                    "device_ids": [
                        ids["success_device"],
                        ids["failed_device"],
                    ],
                    "mode": "production",
                    "save_baseline": False,
                    "ticket_id": f"E2E-{suffix}",
                    "launch_mode": "workflow",
                    "workflow_name": "patrol-change-event",
                },
            )
            batch_id = str(launch["patrol_batch_id"])
            workflow_state = _wait_workflows(
                batch_id,
                timeout_seconds=timeout_seconds,
            )
            with get_db_session() as db:
                batch = db.get(PatrolBatchRecord, batch_id)
                if batch is None:
                    raise AssertionError("launched batch disappeared")
                batch.report_purpose = "shadow_validation"
                batch.updated_at = datetime.now(timezone.utc)

            client.headers["Idempotency-Key"] = f"workflow-e2e-{suffix}"
            try:
                dispatch = _request_json(
                    client,
                    "POST",
                    f"/api/patrol/batches/{batch_id}/reports/materialize",
                    json_body={},
                )
            finally:
                client.headers.pop("Idempotency-Key", None)
            if dispatch.get("accepted") is not True:
                raise AssertionError(f"report materialization was not accepted: {dispatch}")
            materialization = _wait_report(
                client,
                batch_id=batch_id,
                task_id=str(dispatch["task_id"]),
                timeout_seconds=timeout_seconds,
            )
            manifest = _request_json(
                client,
                "GET",
                f"/api/patrol/batches/{batch_id}/report-manifest",
            )
            if (
                manifest.get("facts_status") != "final"
                or manifest.get("report_purpose") != "shadow_validation"
                or manifest.get("current_state_authoritative") is not False
            ):
                raise AssertionError(f"report authority boundary is wrong: {manifest}")
            evidence = _database_evidence(ids, batch_id=batch_id)
            summary["workflow_state"] = workflow_state
            summary["database_evidence"] = evidence
            summary["connectivity"] = connectivity
            executions = list(evidence["executions"])
            assessments = {
                str(item["device_id"]): item
                for item in evidence["assessments"]
            }
            if len(executions) != 2 or len(assessments) != 2:
                raise AssertionError(
                    f"mixed target matrix is incomplete: {evidence}"
                )
            success_assessment = assessments[ids["success_device"]]
            failed_assessment = assessments[ids["failed_device"]]
            if (
                success_assessment.get("assessment_status") != "at_risk"
                or success_assessment.get("comparison_status")
                != "first_observation"
                or failed_assessment.get("assessment_status")
                != "unable_to_determine"
            ):
                raise AssertionError(
                    f"first-observation/failure semantics are wrong: {assessments}"
                )
            parsed_snapshots = [
                item
                for item in evidence["snapshots"]
                if item.get("parse_status") == "parsed"
            ]
            if len(parsed_snapshots) != 1:
                raise AssertionError(f"fallback Parser did not produce one snapshot: {evidence}")
            if len(evidence["findings"]) != 1 or len(evidence["risks"]) != 1:
                raise AssertionError(f"current risk projection is incomplete: {evidence}")
            if evidence["change_count"] or evidence["event_occurrence_count"]:
                raise AssertionError(
                    "first observation must not fabricate a Change or Event"
                )
            artifact_summary = _verify_report_artifacts(
                client,
                batch_id=batch_id,
                manifest=manifest,
            )

        summary.update(
            {
                "success": True,
                "batch_id": batch_id,
                "runtime": runtime,
                "impact_preview": impact,
                "effective_plans": effective_plans,
                "launch": launch,
                "workflow_state": workflow_state,
                "materialization": materialization,
                "manifest": {
                    "id": manifest.get("report_manifest_id"),
                    "purpose": manifest.get("report_purpose"),
                    "facts_status": manifest.get("facts_status"),
                    "release_status": manifest.get("release_status"),
                },
                "database_evidence": evidence,
                "artifacts": artifact_summary,
            }
        )
    except Exception as exc:
        summary["error"] = {
            "type": type(exc).__name__,
            "message": str(exc),
        }
        raise
    finally:
        server.close()
        summary["cleanup"] = _cleanup(
            ids,
            username=username,
            profile_version_id=profile_version_id,
            runtime=runtime,
            batch_id=batch_id,
        )
        after_assets = _active_asset_snapshot()
        summary["active_assets_restored"] = after_assets == before_assets
        if summary.get("success") and not summary["active_assets_restored"]:
            summary["success"] = False
            summary["error"] = {
                "type": "AssertionError",
                "message": "active asset set was not restored after E2E cleanup",
            }
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
    if summary.get("success") is not True:
        raise AssertionError(summary.get("error") or "patrol Workflow E2E failed")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Verify first-observation, partial-device failure and reduced "
            "fallback disclosure through the real patrol Workflow"
        )
    )
    parser.add_argument(
        "--base-url",
        default=os.getenv("NETOPS_BFF_BASE_URL", "http://localhost:8001"),
    )
    parser.add_argument("--timeout-seconds", type=int, default=240)
    parser.add_argument(
        "--output-path",
        type=Path,
        default=(
            ROOT
            / ".tmp"
            / "check-item-patrol-workflow-full-stack-e2e"
            / "summary.json"
        ),
    )
    args = parser.parse_args()
    summary = run(
        base_url=args.base_url,
        timeout_seconds=args.timeout_seconds,
        output_path=args.output_path,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
