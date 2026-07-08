#!/usr/bin/env python
# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[4]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.core.parser.contract_inference import infer_contract  # noqa: E402
from src.core.parser.contract_validation import (  # noqa: E402
    infer_and_validate_contract,
    proposal_from_dict,
)


def _records(params: dict[str, Any]) -> list[dict[str, Any]]:
    records = params.get("records") or params.get("structured_records") or []
    if not isinstance(records, list):
        raise ValueError("records must be a list")
    normalized: list[dict[str, Any]] = []
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            raise ValueError(f"records[{index}] must be an object")
        normalized.append(record)
    return normalized


def _agent_loop(
    *,
    mode: str,
    result: dict[str, Any],
    command: str,
) -> dict[str, Any]:
    proposal = result.get("proposal") if isinstance(result.get("proposal"), dict) else {}
    validation = result.get("validation") if isinstance(result.get("validation"), dict) else {}
    errors: list[str] = []
    for field in validation.get("missing_fields") or []:
        errors.append(f"required field missing: {field}")
    if validation.get("primary_key_status") == "invalid":
        errors.append("primary key validation failed")
    value_error_count = int(validation.get("value_error_count") or 0)
    if value_error_count:
        errors.append(f"value validation failed: {value_error_count} errors")
    success = result.get("success") is True
    revision_request = None
    if not success:
        revision_request = {
            "target_generator": "observation-contract-generator",
            "reason": errors or ["contract validation failed"],
            "required_fixes": [
                "choose stable non-empty primary keys",
                "keep required_fields aligned with parsed records",
                "remove dynamic fields from primary_keys",
                "fix validators for fields that failed deterministic checks",
            ],
            "next_params_patch": {
                "mode": "infer_and_validate",
                "command": command,
                "agent_loop": True,
            },
        }
    return {
        "ready_for_approval": success,
        "next_action": "submit_for_approval" if success else "revise_observation_contract",
        "revision_request": revision_request,
        "attempts": [
            {
                "attempt": 1,
                "stage": "observation_contract_generation",
                "generator": "observation-contract-generator",
                "mode": mode,
                "success": success,
                "input_summary": {
                    "command": command,
                    "record_count": validation.get("record_count", 0),
                },
                "output_summary": {
                    "category": proposal.get("category"),
                    "entity_type": proposal.get("entity_type"),
                    "primary_keys": proposal.get("primary_keys") or [],
                    "required_fields": proposal.get("required_fields") or [],
                    "semantic_field_count": validation.get("semantic_field_count", 0),
                },
                "validator_feedback": {
                    "errors": errors,
                    "missing_fields": validation.get("missing_fields") or [],
                    "duplicate_key_count": validation.get("duplicate_key_count", 0),
                    "key_error_count": validation.get("key_error_count", 0),
                    "value_error_count": value_error_count,
                },
                "revision_request": revision_request,
            }
        ],
    }


def run_contract_generation(params: dict[str, Any]) -> dict[str, Any]:
    mode = str(params.get("mode") or "infer_and_validate").strip().lower()
    command = str(params.get("command") or "").strip()
    if not command:
        raise ValueError("command is required")
    samples_text = str(params.get("samples_text") or params.get("samples_summary") or "")
    records = _records(params)
    proposal_payload = params.get("proposal") or params.get("contract")
    proposal = (
        proposal_from_dict(proposal_payload)
        if isinstance(proposal_payload, dict)
        else None
    )

    if mode == "infer":
        inferred = proposal or infer_contract(command=command, samples_text=samples_text)
        result = {
            "success": True,
            "mode": mode,
            "proposal": inferred.to_dict(),
            "validation": None,
        }
        if params.get("agent_loop"):
            result["agent_loop"] = _agent_loop(mode=mode, result=result, command=command)
        return result
    if mode == "validate":
        if proposal is None:
            raise ValueError("proposal is required in validate mode")
        report = infer_and_validate_contract(
            command=command,
            records=records,
            samples_text=samples_text,
            proposal=proposal,
        )
        result = {"mode": mode, **report}
        if params.get("agent_loop"):
            result["agent_loop"] = _agent_loop(mode=mode, result=result, command=command)
        return result
    if mode == "infer_and_validate":
        report = infer_and_validate_contract(
            command=command,
            records=records,
            samples_text=samples_text,
            proposal=proposal,
        )
        result = {"mode": mode, **report}
        if params.get("agent_loop"):
            result["agent_loop"] = _agent_loop(mode=mode, result=result, command=command)
        return result
    raise ValueError("mode must be infer, validate, or infer_and_validate")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate and validate ObservationContract proposals"
    )
    parser.add_argument("--params", required=True, help="params.json path")
    args = parser.parse_args()
    with open(args.params, encoding="utf-8-sig") as f:
        params = json.load(f)
    result = run_contract_generation(params)
    print(json.dumps(result, ensure_ascii=False, default=str))
    return 0 if result.get("success") else 1


if __name__ == "__main__":
    raise SystemExit(main())
