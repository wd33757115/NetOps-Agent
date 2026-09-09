"""Conservative product-goal audit derived from authoritative runtime facts.

This module deliberately distinguishes machine-verifiable blockers from evidence
that still requires a customer or release owner.  It must never turn engineering
fixtures into production acceptance.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

ProductGateStatus = Literal[
    "passed",
    "blocked",
    "external_evidence_required",
]

GOLDEN_CHECK_ITEM_IDS = frozenset(
    {
        "system.cpu_usage",
        "interface.runtime_statistics",
        "interface.optical_power",
        "device.version",
        "configuration.running",
    }
)

_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_PRODUCTION_ACTOR_KINDS = frozenset(
    {"production_owner", "customer_authorized", "independent_auditor"}
)


@dataclass(frozen=True, slots=True)
class ProductGoalGate:
    gate_id: str
    status: ProductGateStatus
    message: str
    evidence: dict[str, Any]
    next_action: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "gate_id": self.gate_id,
            "status": self.status,
            "message": self.message,
            "evidence": dict(self.evidence),
            "next_action": self.next_action,
        }


def _strings(value: Any) -> set[str]:
    if not isinstance(value, (list, tuple, set, frozenset)):
        return set()
    return {str(item).strip() for item in value if str(item).strip()}


def _external_evidence_result(
    external_evidence: Mapping[str, Any],
    gate_id: str,
) -> dict[str, Any]:
    value = external_evidence.get(gate_id)
    item = value if isinstance(value, Mapping) else {}
    errors: list[str] = []
    if item.get("status") != "verified":
        errors.append("status_not_verified")
    artifact_uri = str(item.get("artifact_uri") or "").strip()
    if not artifact_uri or artifact_uri.startswith(("file:", ".tmp", "tmp")):
        errors.append("immutable_artifact_uri_missing")
    sha256 = str(item.get("sha256") or "").strip().lower()
    if not _SHA256_PATTERN.fullmatch(sha256):
        errors.append("artifact_sha256_invalid")
    verified_by = str(item.get("verified_by") or "").strip()
    if not verified_by:
        errors.append("verified_by_missing")
    actor_kind = str(item.get("actor_kind") or "").strip()
    if actor_kind not in _PRODUCTION_ACTOR_KINDS:
        errors.append("production_actor_kind_invalid")
    verified_at = str(item.get("verified_at") or "").strip()
    try:
        parsed_at = datetime.fromisoformat(verified_at.replace("Z", "+00:00"))
        if parsed_at.tzinfo is None:
            errors.append("verified_at_timezone_missing")
    except ValueError:
        errors.append("verified_at_invalid")
    if item.get("test_evidence") is not False:
        errors.append("test_evidence_not_explicitly_false")
    return {
        "verified": not errors,
        "errors": errors,
        "artifact_uri": artifact_uri or None,
        "sha256": sha256 or None,
        "verified_by": verified_by or None,
        "actor_kind": actor_kind or None,
        "verified_at": verified_at or None,
    }


def _gate_status(
    *,
    machine_blocked: bool,
    external_result: Mapping[str, Any],
) -> ProductGateStatus:
    if machine_blocked:
        return "blocked"
    if bool(external_result.get("verified")):
        return "passed"
    return "external_evidence_required"


def evaluate_product_goal_snapshot(
    snapshot: Mapping[str, Any],
    *,
    external_evidence: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Evaluate only what the supplied authoritative snapshot can prove.

    `PlatformEngineeringVerified` and customer acceptance remain external-evidence
    gates even when every runtime counter is healthy.  This prevents a local test
    actor or synthetic device from satisfying the final product objective.
    """

    active_bundle_items = _strings(snapshot.get("active_runtime_bundle_check_items"))
    missing_golden = sorted(GOLDEN_CHECK_ITEM_IDS - active_bundle_items)
    production_device_count = int(snapshot.get("production_candidate_device_count") or 0)
    production_vendor_count = int(snapshot.get("production_candidate_vendor_count") or 0)
    production_claim_manifest_count = int(
        snapshot.get("production_claim_manifest_count") or 0
    )
    accepted_production_cycle_count = int(
        snapshot.get("accepted_production_cycle_count") or 0
    )
    accepted_content_review_count = int(
        snapshot.get("accepted_content_review_count") or 0
    )
    production_release_count = int(snapshot.get("production_release_count") or 0)
    deprecation_candidate_count = int(
        snapshot.get("deprecation_candidate_count") or 0
    )
    deprecation_exit_ready_count = int(
        snapshot.get("deprecation_exit_ready_count") or 0
    )
    legacy_call_count = int(snapshot.get("legacy_call_count") or 0)

    external = external_evidence or {}
    external_results = {
        gate_id: _external_evidence_result(external, gate_id)
        for gate_id in (
            "platform_engineering_verified",
            "five_golden_slices_accepted",
            "customer_production_matrix_ready",
            "fresh_current_cycle_accepted",
            "two_production_cycles_accepted",
            "formal_content_accepted_and_released",
            "multi_vendor_scale_validated",
            "governance_exit_gates_passed",
        )
    }
    customer_matrix_blocked = production_device_count <= 0 or bool(missing_golden)
    fresh_current_blocked = production_claim_manifest_count <= 0
    two_cycles_blocked = accepted_production_cycle_count < 2
    formal_release_blocked = (
        accepted_content_review_count <= 0 or production_release_count <= 0
    )
    multi_vendor_blocked = production_vendor_count < 2
    governance_blocked = (
        deprecation_candidate_count <= 0
        or deprecation_exit_ready_count != deprecation_candidate_count
        or legacy_call_count > 0
    )
    gates = [
        ProductGoalGate(
            gate_id="platform_engineering_verified",
            status=_gate_status(
                machine_blocked=False,
                external_result=external_results["platform_engineering_verified"],
            ),
            message="工程闭环必须由当前构建的浏览器、API、Worker、数据库、队列和对象存储证据矩阵复核。",
            evidence={
                "audit_scope": "runtime_database_and_governance_facts",
                "automatic_pass_forbidden": True,
                "external_evidence": external_results[
                    "platform_engineering_verified"
                ],
            },
            next_action="复核完成矩阵中每项全栈证据的构建版本、摘要和可回读制品。",
        ),
        ProductGoalGate(
            gate_id="five_golden_slices_accepted",
            status=_gate_status(
                machine_blocked=bool(missing_golden),
                external_result=external_results["five_golden_slices_accepted"],
            ),
            message=(
                "五条黄金检查项尚未全部具有 Active RuntimeBundle。"
                if missing_golden
                else "五条黄金检查项已有 Active RuntimeBundle，但仍需真实工程复核与独立审批证据。"
            ),
            evidence={
                "required_check_items": sorted(GOLDEN_CHECK_ITEM_IDS),
                "active_bundle_check_items": sorted(active_bundle_items),
                "missing_check_items": missing_golden,
                "external_evidence": external_results[
                    "five_golden_slices_accepted"
                ],
            },
            next_action="补齐并由不同责任人审批 Parser、规则链和 RuntimeBundle。",
        ),
        ProductGoalGate(
            gate_id="customer_production_matrix_ready",
            status=_gate_status(
                machine_blocked=customer_matrix_blocked,
                external_result=external_results[
                    "customer_production_matrix_ready"
                ],
            ),
            message=(
                "没有可作为甲方生产矩阵证据的设备与完整运行包。"
                if customer_matrix_blocked
                else "存在生产候选设备与运行包，但仍需冻结并由甲方确认 ScopeContract 和目标矩阵。"
            ),
            evidence={
                "production_candidate_device_count": production_device_count,
                "production_candidate_vendor_count": production_vendor_count,
                "missing_golden_bundle_check_items": missing_golden,
                "external_evidence": external_results[
                    "customer_production_matrix_ready"
                ],
            },
            next_action=(
                "导入真实 Inventory，冻结应巡范围，并对每个设备检查项目标解析 "
                "Active RuntimeBundle。"
            ),
        ),
        ProductGoalGate(
            gate_id="fresh_current_cycle_accepted",
            status=_gate_status(
                machine_blocked=fresh_current_blocked,
                external_result=external_results["fresh_current_cycle_accepted"],
            ),
            message=(
                "没有声明 production_network_claim=true 的新鲜生产现状 Manifest。"
                if fresh_current_blocked
                else "已有生产现状 Manifest，但仍需真实责任人完成内容验收。"
            ),
            evidence={
                "production_claim_manifest_count": production_claim_manifest_count,
                "external_evidence": external_results[
                    "fresh_current_cycle_accepted"
                ],
            },
            next_action=(
                "使用真实设备、真实身份和冻结资产执行 production_current，"
                "并完成独立内容验收。"
            ),
        ),
        ProductGoalGate(
            gate_id="two_production_cycles_accepted",
            status=_gate_status(
                machine_blocked=two_cycles_blocked,
                external_result=external_results["two_production_cycles_accepted"],
            ),
            message=(
                "尚无两个通过内容验收和签发的真实生产周期。"
                if two_cycles_blocked
                else "两个生产周期已形成记录，仍需核对冻结范围一致性及变化、恢复和持续风险。"
            ),
            evidence={
                "accepted_production_cycle_count": accepted_production_cycle_count,
                "minimum_required": 2,
                "external_evidence": external_results[
                    "two_production_cycles_accepted"
                ],
            },
            next_action="在相同冻结范围运行两个连续生产周期并完成人工验收。",
        ),
        ProductGoalGate(
            gate_id="formal_content_accepted_and_released",
            status=_gate_status(
                machine_blocked=formal_release_blocked,
                external_result=external_results[
                    "formal_content_accepted_and_released"
                ],
            ),
            message=(
                "没有已接受的内容复核和正式生产签发记录。"
                if formal_release_blocked
                else "存在内容验收与签发记录，仍需确认签发人为甲方授权主体而非测试账号。"
            ),
            evidence={
                "accepted_content_review_count": accepted_content_review_count,
                "production_release_count": production_release_count,
                "external_evidence": external_results[
                    "formal_content_accepted_and_released"
                ],
            },
            next_action="由真实工程复核人完成内容验收，再由不同的甲方授权主体签发精确制品摘要。",
        ),
        ProductGoalGate(
            gate_id="multi_vendor_scale_validated",
            status=_gate_status(
                machine_blocked=multi_vendor_blocked,
                external_result=external_results["multi_vendor_scale_validated"],
            ),
            message=(
                "真实生产候选矩阵未覆盖至少两个厂商。"
                if multi_vendor_blocked
                else "生产候选矩阵覆盖多个厂商，但仍需结合 2000×20 容量证据和真实内容复核。"
            ),
            evidence={
                "production_candidate_vendor_count": production_vendor_count,
                "minimum_required": 2,
                "capacity_evidence_requires_separate_review": True,
                "external_evidence": external_results[
                    "multi_vendor_scale_validated"
                ],
            },
            next_action="冻结真实多厂商、多型号矩阵并完成规模压测与跨厂商报告内容验收。",
        ),
        ProductGoalGate(
            gate_id="governance_exit_gates_passed",
            status=_gate_status(
                machine_blocked=governance_blocked,
                external_result=external_results["governance_exit_gates_passed"],
            ),
            message=(
                "废弃候选尚未全部满足退出条件，或观察期仍有旧路径调用。"
                if governance_blocked
                else "机器门禁已满足，仍需所有者确认观察周期、回滚证据和删除窗口。"
            ),
            evidence={
                "candidate_count": deprecation_candidate_count,
                "exit_ready_count": deprecation_exit_ready_count,
                "observed_legacy_call_count": legacy_call_count,
                "external_evidence": external_results[
                    "governance_exit_gates_passed"
                ],
            },
            next_action="完成两个不重叠发布周期零调用证据及所有者、依赖、回滚和删除审批。",
        ),
    ]
    status_counts = {
        status: sum(1 for gate in gates if gate.status == status)
        for status in ("passed", "blocked", "external_evidence_required")
    }
    achieved = bool(gates) and all(gate.status == "passed" for gate in gates)
    return {
        "schema_version": "check-item-product-goal-audit/v1",
        "product_goal_achieved": achieved,
        "status": "achieved" if achieved else "not_achieved",
        "status_counts": status_counts,
        "gates": [gate.to_dict() for gate in gates],
    }


__all__ = [
    "GOLDEN_CHECK_ITEM_IDS",
    "ProductGoalGate",
    "evaluate_product_goal_snapshot",
]
