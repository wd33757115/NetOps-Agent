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
)

TRUNCATION_PATTERNS = (
    r"(?im)^\s*(?:%|\[)?\s*output\s+(?:was\s+)?truncated\b",
    r"(?im)^\s*\[\s*truncated\s*\]\s*$",
    r"(?im)^\s*\.\.\.\s+(?:output\s+)?omitted\s+\.\.\.\s*$",
)

PAGING_PATTERNS = (
    r"(?im)(?:--|----)\s*more\s*(?:--|----)",
    r"(?im)^\s*press\s+(?:any\s+key|space|enter)\s+to\s+continue",
    r"(?im)^\s*more:\s*<space>",
)

VALID_EMPTY_SAMPLE_PATTERNS = (
    r"(?im)^\s*\S[^\r\n]{0,120}\s+(?:is|are)\s+not\s+configured[.!]?\s*$",
    r"(?im)^\s*no\s+(?:entries|records|instances|sessions|neighbors|routes|information)"
    r"(?:\s+(?:found|available|present))?[.!]?\s*$",
    r"(?im)^\s*no\s+\S[^\r\n]{0,120}\s+(?:found|available|present)[.!]?\s*$",
)

OUTPUT_STATUSES = {
    "not_classified",
    "data",
    "valid_empty",
    "header_only_unverified",
    "empty_unverified",
    "unsupported_command",
    "command_error",
    "truncated",
    "paged",
}


def _collection_diagnostics(text: str, command: str) -> str:
    """Comware syslog messages describe past events, not this CLI invocation.

    Keep standalone diagnostics and paging markers visible. Only skip a
    timestamped log record and its indented message continuation lines.
    """
    if not re.fullmatch(r"display logbuffer(?:\s+.*)?", command, re.I):
        return text
    lines = []
    in_record = False
    for line in text.splitlines():
        if re.match(r"^%[A-Za-z]{3}\s+\d{1,2}\s+\d{2}:\d{2}:\d{2}:\d+\s+\d{4}\s+\S+\s+[^/\s]+/[0-7]/[^:]+:", line):
            in_record = True
            continue
        if in_record and line[:1].isspace() and not re.match(r"\s*[%^]", line):
            continue
        in_record = False
        lines.append(line)
    return "\n".join(lines)


@dataclass(frozen=True)
class OutputClassification:
    status: str
    confidence: float
    reason: str


class OutputClassifier:
    def classify(
        self,
        raw_text: str,
        *,
        decision: Any,
        profile: dict[str, Any] | None = None,
    ) -> OutputClassification:
        text = str(raw_text or "")
        diagnostics = _collection_diagnostics(text, str(getattr(decision, "canonical_command", "")))
        profile_policy = dict(profile or {})
        if isinstance(profile_policy.get("policy"), dict):
            profile_policy = dict(profile_policy["policy"])

        for pattern in self._patterns(
            profile_policy,
            "unsupported_patterns",
            UNSUPPORTED_PATTERNS,
        ):
            if re.search(pattern, diagnostics, re.IGNORECASE | re.DOTALL):
                return OutputClassification("unsupported_command", 1.0, pattern)
        for pattern in self._patterns(
            profile_policy,
            "command_error_patterns",
            COMMAND_ERROR_PATTERNS,
        ):
            if re.search(pattern, diagnostics, re.IGNORECASE | re.DOTALL):
                return OutputClassification("command_error", 1.0, pattern)
        for pattern in self._patterns(
            profile_policy,
            "truncation_patterns",
            TRUNCATION_PATTERNS,
        ):
            if re.search(pattern, text, re.IGNORECASE | re.DOTALL):
                return OutputClassification("truncated", 1.0, pattern)
        for pattern in self._patterns(
            profile_policy,
            "paging_patterns",
            PAGING_PATTERNS,
        ):
            if re.search(pattern, text, re.IGNORECASE | re.DOTALL):
                return OutputClassification("paged", 1.0, pattern)

        semantics = dict(getattr(decision, "empty_semantics", None) or {})
        valid_empty_patterns = [
            *(
                profile_policy.get("valid_empty_patterns")
                or ()
            ),
            *(semantics.get("valid_empty_patterns") or ()),
        ]
        for pattern in valid_empty_patterns:
            if pattern and re.search(str(pattern), text, re.IGNORECASE | re.DOTALL):
                return OutputClassification("valid_empty", 1.0, str(pattern))

        if text.strip():
            minimum = profile_policy.get("minimum_non_whitespace_chars")
            if minimum is not None:
                try:
                    minimum_chars = max(0, int(minimum))
                except (TypeError, ValueError):
                    minimum_chars = 0
                content_chars = len(re.sub(r"\s+", "", text))
                if content_chars < minimum_chars:
                    return OutputClassification(
                        "empty_unverified",
                        0.0,
                        "output_below_profile_minimum_content",
                    )
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

    @staticmethod
    def _patterns(
        profile: dict[str, Any],
        key: str,
        defaults: tuple[str, ...],
    ) -> tuple[str, ...]:
        configured = profile.get(key)
        if configured is None:
            return defaults
        if not isinstance(configured, (list, tuple)):
            return defaults
        return tuple(
            str(pattern)
            for pattern in configured
            if str(pattern).strip()
        )


def classify_parser_generation_sample(text: str) -> dict[str, Any]:
    """Classify a sanitized Parser sample without command-specific assumptions."""

    value = str(text or "")
    if not value.strip():
        return {
            "status": "empty_unverified",
            "confidence": 1.0,
            "reason": "blank_output_requires_engineer_confirmation",
            "recommended_role": "unassigned",
        }
    for status, patterns in (
        ("unsupported_command", UNSUPPORTED_PATTERNS),
        ("command_error", COMMAND_ERROR_PATTERNS),
        ("valid_empty", VALID_EMPTY_SAMPLE_PATTERNS),
    ):
        for pattern in patterns:
            if re.search(pattern, value, re.IGNORECASE | re.DOTALL):
                return {
                    "status": status,
                    "confidence": 1.0,
                    "reason": pattern,
                    "recommended_role": "negative",
                }
    lines = [line.rstrip() for line in value.splitlines() if line.strip()]
    if len(lines) <= 8:
        for index, line in enumerate(lines):
            stripped = line.strip()
            if (
                stripped.endswith(":")
                or ":" in stripped
                or re.fullmatch(r"[-=]+", stripped)
            ):
                continue
            columns = [
                column.strip()
                for column in re.split(r"\s{2,}", stripped)
                if column.strip()
            ]
            if (
                len(columns) < 2
                or re.search(r"\d", stripped)
                or sum(bool(re.search(r"[A-Za-z]", column)) for column in columns)
                < max(1, len(columns) // 2)
            ):
                continue
            trailing = [
                candidate
                for candidate in lines[index + 1 :]
                if not re.fullmatch(r"\s*[-=]+\s*", candidate)
            ]
            if not trailing:
                return {
                    "status": "header_only_unverified",
                    "confidence": 0.95,
                    "reason": "table_header_present_without_data_rows",
                    "recommended_role": "unassigned",
                }
    return {
        "status": "data",
        "confidence": 0.8,
        "reason": "non_empty_output_requires_schema_inference",
        "recommended_role": "generation",
    }
