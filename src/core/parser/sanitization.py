"""Deterministic redaction for production Parser generation samples."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

SANITIZATION_VERSION = "parser-cli-v1"

REDACTED_RUNTIME_PATTERNS = {
    "<SERIAL>": r"[A-Za-z0-9._/-]+",
    "<USER>": r"\S+",
    "<REDACTED>": r"\S+",
}


@dataclass(frozen=True)
class SanitizedParserSample:
    text: str
    sha256: str
    version: str
    redaction_summary: dict[str, int]


def _sub(pattern: str, replacement, text: str) -> tuple[str, int]:
    return re.subn(pattern, replacement, text, flags=re.IGNORECASE)


def runtime_pattern_for_redacted_value(value: str) -> str | None:
    return REDACTED_RUNTIME_PATTERNS.get(str(value or "").strip().upper())


def sanitize_parser_sample(raw_text: str) -> SanitizedParserSample:
    text = str(raw_text or "")
    counts: dict[str, int] = {}

    text, counts["credential"] = _sub(
        r"\b(password|secret|community|token|credential|private[-_ ]?key)"
        r"(\s*(?:[:=]|\bis\b)?\s*)(\S+)",
        lambda match: f"{match.group(1)}{match.group(2)}<REDACTED>",
        text,
    )
    text, counts["username"] = _sub(
        r"\b(username|user name|login user)(\s*(?:[:=]|\bis\b)?\s*)(\S+)",
        lambda match: f"{match.group(1)}{match.group(2)}<USER>",
        text,
    )
    text, counts["serial_number"] = _sub(
        r"(?m)^(?P<prefix>\s*[^:\n]*(?<![A-Z0-9])"
        r"(?:serial(?:[ _-]+number)?|s/n)\s*(?:[:=]|\bis\b)\s*)"
        r"(?P<value>[A-Z0-9._/-]+)",
        lambda match: f"{match.group('prefix')}<SERIAL>",
        text,
    )
    text, counts["mac_address"] = _sub(
        r"(?<![0-9A-F])(?:[0-9A-F]{2}[:-]){5}[0-9A-F]{2}(?![0-9A-F])",
        "02:00:00:00:00:01",
        text,
    )
    text, quartet_mac_count = _sub(
        r"(?<![0-9A-F])(?:[0-9A-F]{4}[-.]){2}[0-9A-F]{4}(?![0-9A-F])",
        "0200-0000-0001",
        text,
    )
    counts["mac_address"] += quartet_mac_count
    text, counts["ipv4_address"] = _sub(
        r"(?<!\d)(?:\d{1,3}\.){3}\d{1,3}(?!\d)",
        "192.0.2.1",
        text,
    )
    text, counts["email"] = _sub(
        r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b",
        "operator@example.invalid",
        text,
    )
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return SanitizedParserSample(
        text=text,
        sha256=digest,
        version=SANITIZATION_VERSION,
        redaction_summary={key: value for key, value in counts.items() if value},
    )
