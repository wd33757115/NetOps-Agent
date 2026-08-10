"""Shared governance identities used by approval boundaries."""

from __future__ import annotations

from collections.abc import Iterable

_ACTOR_PREFIXES = {
    "api",
    "celery",
    "skill",
    "task",
    "workflow",
}


def normalize_actor_identity(value: object) -> str:
    """Return one comparable identity across transport and worker prefixes."""

    identity = str(value or "").strip()
    while ":" in identity:
        prefix, remainder = identity.split(":", 1)
        if prefix.strip().casefold() not in _ACTOR_PREFIXES:
            break
        identity = remainder.strip()
    return identity.casefold()


def actor_matches_any(actor: object, candidates: Iterable[object]) -> bool:
    normalized = normalize_actor_identity(actor)
    if not normalized:
        return False
    return any(
        normalized == candidate
        for candidate in (
            normalize_actor_identity(item) for item in candidates
        )
        if candidate
    )
