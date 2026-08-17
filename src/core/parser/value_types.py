"""Shared source-value semantics used by Parser generation and runtime."""

from __future__ import annotations

import ipaddress
from typing import Any

IP_UNASSIGNED_TOKENS = frozenset(
    {
        "-",
        "--",
        "n/a",
        "na",
        "none",
        "null",
        "unassigned",
        "unset",
    }
)


def is_unassigned_ip(value: Any) -> bool:
    return str(value or "").strip().casefold() in IP_UNASSIGNED_TOKENS


def is_valid_ip_value(value: Any, *, allow_unassigned: bool = False) -> bool:
    text = str(value or "").strip()
    if allow_unassigned and is_unassigned_ip(text):
        return True
    try:
        ipaddress.ip_interface(text)
    except ValueError:
        return False
    return True


def is_unavailable_value(value: Any, tokens: Any) -> bool:
    """Return whether a source value is an explicitly declared missing token."""

    text = str(value or "").strip().casefold()
    if not text:
        return False
    return text in {
        str(item).strip().casefold()
        for item in tokens or ()
        if str(item).strip()
    }
