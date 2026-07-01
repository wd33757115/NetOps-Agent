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
        return {
            "success": True,
            "mode": mode,
            "proposal": inferred.to_dict(),
            "validation": None,
        }
    if mode == "validate":
        if proposal is None:
            raise ValueError("proposal is required in validate mode")
        report = infer_and_validate_contract(
            command=command,
            records=records,
            samples_text=samples_text,
            proposal=proposal,
        )
        return {"mode": mode, **report}
    if mode == "infer_and_validate":
        report = infer_and_validate_contract(
            command=command,
            records=records,
            samples_text=samples_text,
            proposal=proposal,
        )
        return {"mode": mode, **report}
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
