"""Deterministic redaction for production Parser generation samples."""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import re
from dataclasses import dataclass

SANITIZATION_VERSION = "parser-cli-v2"

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
    normalized = str(value or "").strip().upper()
    direct = REDACTED_RUNTIME_PATTERNS.get(normalized)
    if direct:
        return direct
    if re.fullmatch(r"<SERIAL:[0-9A-F]{12}>", normalized):
        return REDACTED_RUNTIME_PATTERNS["<SERIAL>"]
    if re.fullmatch(r"<USER:[0-9A-F]{12}>", normalized):
        return REDACTED_RUNTIME_PATTERNS["<USER>"]
    return None


def _pseudonym_token(kind: str, value: str, *, length: int = 12) -> str:
    from src.common.config import get_settings

    settings = get_settings()
    secret = str(
        settings.PARSER_SANITIZATION_SECRET or settings.JWT_SECRET_KEY
    ).encode("utf-8")
    digest = hmac.new(
        secret,
        f"parser-sample:{kind}:{value.casefold()}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return digest[:length]


def _pseudonymize_ipv4(match: re.Match[str]) -> str:
    value = match.group(0)
    token = int(_pseudonym_token("ipv4", value, length=16), 16)
    # RFC 6598 shared space keeps the value IPv4-shaped without exposing topology.
    network = ipaddress.ip_network("100.64.0.0/10")
    offset = token % (network.num_addresses - 2) + 1
    return str(network.network_address + offset)


def _pseudonymize_mac(match: re.Match[str]) -> str:
    value = match.group(0)
    suffix = _pseudonym_token("mac", value, length=6)
    compact = f"02005e{suffix}".upper()
    lowercase = value.lower() == value
    if "." in value:
        rendered = ".".join(compact[index : index + 4] for index in range(0, 12, 4))
    elif re.fullmatch(r"(?:[0-9A-Fa-f]{4}-){2}[0-9A-Fa-f]{4}", value):
        rendered = "-".join(compact[index : index + 4] for index in range(0, 12, 4))
    else:
        delimiter = ":" if ":" in value else "-"
        rendered = delimiter.join(
            compact[index : index + 2] for index in range(0, 12, 2)
        )
    return rendered.lower() if lowercase else rendered


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
        lambda match: (
            f"{match.group(1)}{match.group(2)}"
            f"<USER:{_pseudonym_token('username', match.group(3)).upper()}>"
        ),
        text,
    )
    text, counts["serial_number"] = _sub(
        r"(?m)^(?P<prefix>\s*[^:\n]*(?<![A-Z0-9])"
        r"(?:serial(?:[ _-]+number)?|s/n)\s*(?:[:=]|\bis\b)\s*)"
        r"(?P<value>[A-Z0-9._/-]+)",
        lambda match: (
            f"{match.group('prefix')}"
            f"<SERIAL:{_pseudonym_token('serial', match.group('value')).upper()}>"
        ),
        text,
    )
    text, counts["mac_address"] = _sub(
        r"(?<![0-9A-F])(?:[0-9A-F]{2}[:-]){5}[0-9A-F]{2}(?![0-9A-F])",
        _pseudonymize_mac,
        text,
    )
    text, quartet_mac_count = _sub(
        r"(?<![0-9A-F])(?:[0-9A-F]{4}[-.]){2}[0-9A-F]{4}(?![0-9A-F])",
        _pseudonymize_mac,
        text,
    )
    counts["mac_address"] += quartet_mac_count
    text, counts["ipv4_address"] = _sub(
        r"(?<!\d)(?:\d{1,3}\.){3}\d{1,3}(?!\d)",
        _pseudonymize_ipv4,
        text,
    )
    text, counts["email"] = _sub(
        r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b",
        lambda match: f"operator+{_pseudonym_token('email', match.group(0))}@example.invalid",
        text,
    )
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return SanitizedParserSample(
        text=text,
        sha256=digest,
        version=SANITIZATION_VERSION,
        redaction_summary={key: value for key, value in counts.items() if value},
    )
