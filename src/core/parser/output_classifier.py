"""Deterministic CLI output classification before template resolution."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


UNSUPPORTED_PATTERNS = (
    r"%\s*(?:invalid input|unrecognized command)",
    r"error:\s*unrecognized command",
    r"(?:wrong|invalid) parameter found",
    r"unrecognized command found",
    r"incomplete command found",
)

COMMAND_ERROR_PATTERNS = (
    r"permission denied",
    r"authorization failed",
    r"insufficient privilege",
    r"command execution failed",
    r"output truncated",
)

OUTPUT_STATUSES = {
    "not_classified",
    "data",
    "valid_empty",
    "empty_unverified",
    "unsupported_command",
    "command_error",
}


@dataclass(frozen=True)
class OutputClassification:
    status: str
    confidence: float
    reason: str


class OutputClassifier:
    def classify(self, raw_text: str, *, decision: Any) -> OutputClassification:
        text = str(raw_text or "")
        for pattern in UNSUPPORTED_PATTERNS:
            if re.search(pattern, text, re.IGNORECASE | re.DOTALL):
                return OutputClassification("unsupported_command", 1.0, pattern)
        for pattern in COMMAND_ERROR_PATTERNS:
            if re.search(pattern, text, re.IGNORECASE | re.DOTALL):
                return OutputClassification("command_error", 1.0, pattern)

        semantics = dict(getattr(decision, "empty_semantics", None) or {})
        for pattern in semantics.get("valid_empty_patterns") or ():
            if pattern and re.search(str(pattern), text, re.IGNORECASE | re.DOTALL):
                return OutputClassification("valid_empty", 1.0, str(pattern))

        if text.strip():
            return OutputClassification("data", 1.0, "non_empty_output")

        blank_semantics = str(semantics.get("blank") or "empty_unverified")
        capability_status = str(getattr(decision, "capability_status", "unknown"))
        if blank_semantics == "valid_empty" and capability_status == "supported":
            return OutputClassification(
                "valid_empty",
                1.0,
                "capability_contract_allows_blank_zero_entities",
            )
        return OutputClassification(
            "empty_unverified",
            0.0,
            "blank_output_has_no_verified_empty_contract",
        )
