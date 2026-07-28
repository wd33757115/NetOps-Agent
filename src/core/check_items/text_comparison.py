"""Versioned deterministic comparison for non-structured check items."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable

import yaml

from src.infrastructure.db.models import (
    TextComparisonProfileRecord,
    TextComparisonProfileVersionRecord,
)
from src.infrastructure.db.postgres import get_db_session

DEFAULT_TEXT_COMPARISON_PROFILE_PATH = (
    Path(__file__).parent / "assets" / "text_comparison_profiles.v1.yaml"
)


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


@dataclass(frozen=True, slots=True)
class TextComparisonProfile:
    id: str
    version: int
    version_id: str
    name: str
    mode: str
    ignore_blank_lines: bool
    ignore_patterns: tuple[str, ...]
    normalize_patterns: tuple[dict[str, str], ...]
    redact_patterns: tuple[dict[str, str], ...]
    categories: tuple[str, ...]
    status: str
    digest: str

    def to_payload(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "version": self.version,
            "name": self.name,
            "mode": self.mode,
            "ignore_blank_lines": self.ignore_blank_lines,
            "ignore_patterns": list(self.ignore_patterns),
            "normalize_patterns": [dict(item) for item in self.normalize_patterns],
            "redact_patterns": [dict(item) for item in self.redact_patterns],
            "categories": list(self.categories),
        }


@dataclass(frozen=True, slots=True)
class TextComparisonResult:
    profile_version_id: str
    profile_digest: str
    comparison_status: str
    changed: bool | None
    previous_digest: str | None
    current_digest: str
    added_lines: tuple[str, ...]
    removed_lines: tuple[str, ...]
    redacted_preview: tuple[str, ...]


@lru_cache(maxsize=4)
def load_text_comparison_profiles(
    path: str | Path = DEFAULT_TEXT_COMPARISON_PROFILE_PATH,
) -> tuple[TextComparisonProfile, ...]:
    source = Path(path)
    payload = yaml.safe_load(source.read_text(encoding="utf-8")) or {}
    rows = payload.get("profiles")
    if not isinstance(rows, list) or not rows:
        raise ValueError("text comparison profile catalog is empty")
    result: list[TextComparisonProfile] = []
    for index, raw in enumerate(rows):
        if not isinstance(raw, dict):
            raise ValueError(f"profiles[{index}] must be an object")
        profile_id = str(raw.get("id") or "").strip()
        version = int(raw.get("version") or 0)
        mode = str(raw.get("mode") or "").strip()
        status = str(raw.get("status") or "").strip()
        if not profile_id or version < 1:
            raise ValueError(f"profiles[{index}] identity is invalid")
        if mode not in {"ordered_lines", "unordered_lines", "hash_only"}:
            raise ValueError(f"{profile_id} has invalid mode")
        if status not in {"draft", "active", "deprecated"}:
            raise ValueError(f"{profile_id} has invalid status")
        for pattern in raw.get("ignore_patterns") or []:
            re.compile(str(pattern))
        for item in [
            *(raw.get("normalize_patterns") or []),
            *(raw.get("redact_patterns") or []),
        ]:
            if not isinstance(item, dict) or not item.get("pattern"):
                raise ValueError(f"{profile_id} has invalid pattern")
            re.compile(str(item["pattern"]))
        normalized = {
            "id": profile_id,
            "version": version,
            "name": str(raw.get("name") or profile_id),
            "mode": mode,
            "ignore_blank_lines": bool(raw.get("ignore_blank_lines", True)),
            "ignore_patterns": [str(item) for item in raw.get("ignore_patterns") or []],
            "normalize_patterns": [
                {
                    "pattern": str(item["pattern"]),
                    "replacement": str(item.get("replacement") or ""),
                }
                for item in raw.get("normalize_patterns") or []
            ],
            "redact_patterns": [
                {
                    "pattern": str(item["pattern"]),
                    "replacement": str(item.get("replacement") or ""),
                }
                for item in raw.get("redact_patterns") or []
            ],
            "categories": [str(item) for item in raw.get("categories") or []],
        }
        digest = _digest(normalized)
        result.append(
            TextComparisonProfile(
                id=profile_id,
                version=version,
                version_id=str(
                    uuid.uuid5(
                        uuid.NAMESPACE_URL,
                        f"netops/text-comparison/{profile_id}/v{version}",
                    )
                ),
                name=normalized["name"],
                mode=mode,
                ignore_blank_lines=normalized["ignore_blank_lines"],
                ignore_patterns=tuple(normalized["ignore_patterns"]),
                normalize_patterns=tuple(normalized["normalize_patterns"]),
                redact_patterns=tuple(normalized["redact_patterns"]),
                categories=tuple(normalized["categories"]),
                status=status,
                digest=digest,
            )
        )
    return tuple(result)


def _normalized_text(raw_text: str, profile: TextComparisonProfile) -> str:
    text = str(raw_text or "")
    for item in profile.redact_patterns:
        text = re.sub(item["pattern"], item["replacement"], text)
    for item in profile.normalize_patterns:
        text = re.sub(item["pattern"], item["replacement"], text)
    lines = []
    for line in text.splitlines():
        if profile.ignore_blank_lines and not line.strip():
            continue
        if any(re.search(pattern, line) for pattern in profile.ignore_patterns):
            continue
        lines.append(line.rstrip())
    if profile.mode == "unordered_lines":
        lines.sort()
    return "\n".join(lines)


def compare_text(
    *,
    current_text: str,
    profile: TextComparisonProfile,
    previous_text: str | None = None,
    preview_limit: int = 100,
) -> TextComparisonResult:
    current = _normalized_text(current_text, profile)
    current_digest = hashlib.sha256(current.encode("utf-8")).hexdigest()
    if previous_text is None:
        return TextComparisonResult(
            profile_version_id=profile.version_id,
            profile_digest=profile.digest,
            comparison_status="no_baseline",
            changed=None,
            previous_digest=None,
            current_digest=current_digest,
            added_lines=(),
            removed_lines=(),
            redacted_preview=tuple(current.splitlines()[:preview_limit]),
        )
    previous = _normalized_text(previous_text, profile)
    previous_digest = hashlib.sha256(previous.encode("utf-8")).hexdigest()
    previous_lines = previous.splitlines()
    current_lines = current.splitlines()
    if profile.mode == "hash_only":
        added: tuple[str, ...] = ()
        removed: tuple[str, ...] = ()
        preview: tuple[str, ...] = ()
    else:
        previous_set = set(previous_lines)
        current_set = set(current_lines)
        added = tuple(line for line in current_lines if line not in previous_set)
        removed = tuple(line for line in previous_lines if line not in current_set)
        preview = tuple([*added, *removed][:preview_limit])
    return TextComparisonResult(
        profile_version_id=profile.version_id,
        profile_digest=profile.digest,
        comparison_status="comparable",
        changed=current_digest != previous_digest,
        previous_digest=previous_digest,
        current_digest=current_digest,
        added_lines=added,
        removed_lines=removed,
        redacted_preview=preview,
    )


class TextComparisonProfileRepository:
    def __init__(
        self,
        session_scope: Callable[[], AbstractContextManager[Any]] = get_db_session,
    ) -> None:
        self._session_scope = session_scope

    def bootstrap(
        self,
        profiles: tuple[TextComparisonProfile, ...] | None = None,
        *,
        actor: str = "text-comparison-bootstrap",
    ) -> dict[str, int]:
        profiles = profiles or load_text_comparison_profiles()
        created_roots = 0
        created_versions = 0
        now = datetime.now(timezone.utc)
        with self._session_scope() as db:
            for profile in profiles:
                root = db.get(TextComparisonProfileRecord, profile.id)
                if root is None:
                    root = TextComparisonProfileRecord(
                        id=profile.id,
                        name=profile.name,
                        status=profile.status,
                        current_version=(
                            profile.version if profile.status == "active" else 0
                        ),
                        created_at=now,
                        updated_at=now,
                    )
                    db.add(root)
                    created_roots += 1
                version = db.get(
                    TextComparisonProfileVersionRecord,
                    profile.version_id,
                )
                if version is not None and str(version.digest) != profile.digest:
                    raise ValueError(
                        f"text comparison profile changed in place: {profile.id}"
                    )
                if version is None:
                    version = TextComparisonProfileVersionRecord(
                        id=profile.version_id,
                        profile_id=profile.id,
                        version=profile.version,
                        mode=profile.mode,
                        profile_json=profile.to_payload(),
                        digest=profile.digest,
                        status=profile.status,
                        created_by=actor,
                        created_at=now,
                        activated_at=now if profile.status == "active" else None,
                    )
                    db.add(version)
                    created_versions += 1
                root.status = profile.status
                root.current_version = (
                    profile.version if profile.status == "active" else 0
                )
                root.updated_at = now
            db.flush()
        return {
            "created_roots": created_roots,
            "created_versions": created_versions,
        }

    def get_version(self, version_id: str) -> TextComparisonProfile | None:
        """Load the exact immutable profile version referenced by a runtime bundle."""

        with self._session_scope() as db:
            row = db.get(TextComparisonProfileVersionRecord, version_id)
            if row is None:
                return None
            payload = dict(row.profile_json or {})
            digest = _digest(payload)
            if digest != str(row.digest):
                raise ValueError("text comparison profile digest mismatch")
            return TextComparisonProfile(
                id=str(row.profile_id),
                version=int(row.version),
                version_id=str(row.id),
                name=str(payload.get("name") or row.profile_id),
                mode=str(row.mode),
                ignore_blank_lines=bool(
                    payload.get("ignore_blank_lines", True)
                ),
                ignore_patterns=tuple(
                    str(item) for item in payload.get("ignore_patterns") or []
                ),
                normalize_patterns=tuple(
                    {
                        "pattern": str(item.get("pattern") or ""),
                        "replacement": str(item.get("replacement") or ""),
                    }
                    for item in payload.get("normalize_patterns") or []
                    if isinstance(item, dict)
                ),
                redact_patterns=tuple(
                    {
                        "pattern": str(item.get("pattern") or ""),
                        "replacement": str(item.get("replacement") or ""),
                    }
                    for item in payload.get("redact_patterns") or []
                    if isinstance(item, dict)
                ),
                categories=tuple(
                    str(item) for item in payload.get("categories") or []
                ),
                status=str(row.status),
                digest=str(row.digest),
            )
