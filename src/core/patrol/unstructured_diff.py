"""Deterministic, policy-driven comparison for non-structured CLI output."""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from difflib import SequenceMatcher
from functools import lru_cache
from pathlib import Path
from typing import Any

from src.core.parser.policy import SHARED_PARSER_CONFIG_ROOT, load_yaml

ANSI_ESCAPE_RE = re.compile(r"\x1b(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
PAGER_RE = re.compile(r"(?:--More--|---- More ----)", re.IGNORECASE)
SAFE_PREVIEW_REDACTIONS = (
    {
        "pattern": (
            r"(?i)(\b(?:user|username|login-name|operator)\s*[=:]\s*)"
            r".*?(?=-IPAddr=|[;,\s]|$)"
        ),
        "replacement": r"\1<redacted>",
    },
    {
        "pattern": r"(?i)(\b(?:ipaddr|source-ip|remote-ip)\s*[=:]\s*)(?:\d{1,3}\.){3}\d{1,3}",
        "replacement": r"\1<ip>",
    },
    {
        "pattern": r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])",
        "replacement": "<ip>",
    },
    {
        "pattern": r"(?i)(?<![0-9a-f])(?:[0-9a-f]{2}[:-]){5}[0-9a-f]{2}(?![0-9a-f])",
        "replacement": "<mac>",
    },
    {
        "pattern": r"(?i)(?<![0-9a-f])(?:[0-9a-f]{4}[-.]){2}[0-9a-f]{4}(?![0-9a-f])",
        "replacement": "<mac>",
    },
)


def _digest(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


@lru_cache(maxsize=4)
def _load_policy(path: str) -> dict[str, Any]:
    return load_yaml(path)


def load_unstructured_diff_config(
    policy_path: str | Path | None = None,
) -> dict[str, Any]:
    path = Path(policy_path or SHARED_PARSER_CONFIG_ROOT / "command_policy.yaml")
    policy = _load_policy(str(path.resolve()))
    return dict(policy.get("unstructured_comparison") or {})


def resolve_unstructured_diff_profile(
    command: str,
    *,
    profile_name: str | None = None,
    policy_path: str | Path | None = None,
    policy: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Resolve an immutable comparison profile for one canonical command."""

    # This resolver is also called while summarising every structured
    # observation.  Re-parsing the same YAML policy for each call turns a
    # normal offline patrol result into an O(observations) file-I/O path.
    # Keep caller-supplied policy objects intact, and otherwise reuse the
    # immutable cached policy already used by ``load_unstructured_diff_config``.
    root = policy or _load_policy(
        str(Path(policy_path or SHARED_PARSER_CONFIG_ROOT / "command_policy.yaml").resolve())
    )
    comparison = dict(root.get("unstructured_comparison") or {})
    profiles = dict(comparison.get("profiles") or {})
    canonical = " ".join(str(command or "").strip().lower().split())
    selected = str(profile_name or "").strip()
    if not selected:
        command_metadata = dict((root.get("commands") or {}).get(canonical) or {})
        selected = str(command_metadata.get("comparison_profile") or "").strip()
    if not selected:
        for rule in comparison.get("rules") or []:
            if not isinstance(rule, dict):
                continue
            pattern = str(rule.get("command_pattern") or "").strip()
            if pattern and re.search(pattern, canonical, re.IGNORECASE):
                selected = str(rule.get("profile") or "").strip()
                break
    if not selected:
        return None
    profile = profiles.get(selected)
    if not isinstance(profile, dict):
        raise ValueError(f"unstructured comparison profile not found: {selected}")
    mode = str(profile.get("mode") or "ordered_lines")
    if mode not in {"ordered_lines", "rolling_window"}:
        raise ValueError(f"unsupported unstructured comparison mode: {mode}")
    resolved = {
        **profile,
        "name": selected,
        "version": int(profile.get("version") or 1),
        "mode": mode,
    }
    resolved["policy_digest"] = _digest(resolved)
    return resolved


def _replace_patterns(text: str, patterns: Any) -> str:
    result = text
    for item in patterns or []:
        if not isinstance(item, dict):
            continue
        pattern = str(item.get("pattern") or "")
        if not pattern:
            continue
        result = re.sub(pattern, str(item.get("replacement") or ""), result)
    return result


def normalize_unstructured_text(
    text: str,
    profile: dict[str, Any],
) -> tuple[list[str], int]:
    """Normalize transport noise while preserving command semantics."""

    normalized: list[str] = []
    ignored = 0
    ignore_patterns = [
        re.compile(str(pattern), re.IGNORECASE)
        for pattern in profile.get("ignore_patterns") or []
        if str(pattern)
    ]
    for source_line in str(text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = ANSI_ESCAPE_RE.sub("", source_line).replace("\x08", "")
        line = PAGER_RE.sub("", line).rstrip()
        line = _replace_patterns(line, profile.get("normalize_patterns"))
        if profile.get("collapse_whitespace"):
            line = " ".join(line.split())
        if profile.get("ignore_blank_lines", True) and not line.strip():
            ignored += 1
            continue
        if any(pattern.search(line) for pattern in ignore_patterns):
            ignored += 1
            continue
        normalized.append(line)
    return normalized, ignored


def _redact_line(line: str, profile: dict[str, Any]) -> tuple[str, bool]:
    redacted = _replace_patterns(line, SAFE_PREVIEW_REDACTIONS)
    redacted = _replace_patterns(redacted, profile.get("redact_patterns"))
    max_length = max(40, int(profile.get("max_line_length") or 220))
    if len(redacted) > max_length:
        redacted = redacted[: max_length - 1] + "…"
    return redacted, redacted != line


def _category(line: str, profile: dict[str, Any]) -> tuple[str, str]:
    for item in profile.get("categories") or []:
        if not isinstance(item, dict):
            continue
        patterns = [str(value) for value in item.get("patterns") or [] if str(value)]
        if any(re.search(pattern, line, re.IGNORECASE) for pattern in patterns):
            return str(item.get("name") or "other"), str(item.get("label") or "其他")
    return "other", str(profile.get("other_category_label") or "其他变化")


def _ordered_changes(
    previous: list[str],
    current: list[str],
) -> tuple[list[tuple[str, str]], int, int, int, float]:
    matcher = SequenceMatcher(a=previous, b=current, autojunk=False)
    changed: list[tuple[str, str]] = []
    added = 0
    removed = 0
    blocks = 0
    for operation, old_start, old_end, new_start, new_end in matcher.get_opcodes():
        if operation == "equal":
            continue
        blocks += 1
        for line in previous[old_start:old_end]:
            changed.append(("removed", line))
            removed += 1
        for line in current[new_start:new_end]:
            changed.append(("added", line))
            added += 1
    return changed, added, removed, blocks, matcher.ratio()


def _counter_difference(
    source: list[str],
    other: list[str],
) -> list[str]:
    remaining = Counter(source) - Counter(other)
    result: list[str] = []
    for line in source:
        if remaining[line] <= 0:
            continue
        result.append(line)
        remaining[line] -= 1
    return result


def _rolling_changes(
    previous: list[str],
    current: list[str],
) -> tuple[list[tuple[str, str]], int, int, int, float]:
    removed_lines = _counter_difference(previous, current)
    added_lines = _counter_difference(current, previous)
    intersection = sum((Counter(previous) & Counter(current)).values())
    denominator = len(previous) + len(current)
    ratio = (2 * intersection / denominator) if denominator else 1.0
    # A rolling window is read from the current period first. Put newly observed
    # messages ahead of entries that merely aged out of the window so bounded
    # report previews describe the current period rather than stale history.
    changed = [("added", line) for line in added_lines]
    changed.extend(("removed", line) for line in removed_lines)
    return changed, len(added_lines), len(removed_lines), int(bool(changed)), ratio


def build_unstructured_diff(
    previous_text: str,
    current_text: str,
    *,
    command: str,
    profile_name: str | None = None,
    profile: dict[str, Any] | None = None,
    policy_path: str | Path | None = None,
) -> dict[str, Any]:
    """Return a bounded, credential-safe diff summary without exposing raw CLI."""

    resolved = profile or resolve_unstructured_diff_profile(
        command,
        profile_name=profile_name,
        policy_path=policy_path,
    )
    if resolved is None:
        resolved = {
            "name": "generic",
            "label": "非结构化输出",
            "version": 1,
            "mode": "ordered_lines",
            "ignore_blank_lines": True,
            "max_preview_lines": 20,
            "max_line_length": 220,
            "policy_digest": _digest({"name": "generic", "version": 1}),
        }
    previous_lines, previous_ignored = normalize_unstructured_text(previous_text, resolved)
    current_lines, current_ignored = normalize_unstructured_text(current_text, resolved)
    previous_hash = _digest(previous_lines)
    current_hash = _digest(current_lines)
    raw_previous_hash = hashlib.sha256(str(previous_text or "").encode("utf-8")).hexdigest()
    raw_current_hash = hashlib.sha256(str(current_text or "").encode("utf-8")).hexdigest()
    raw_changed = raw_previous_hash != raw_current_hash
    changed = previous_hash != current_hash
    if resolved["mode"] == "rolling_window":
        line_changes, added, removed, blocks, similarity = _rolling_changes(
            previous_lines, current_lines
        )
    else:
        line_changes, added, removed, blocks, similarity = _ordered_changes(
            previous_lines, current_lines
        )
    category_counts: Counter[str] = Counter()
    category_labels: dict[str, str] = {}
    sensitive_line_count = 0
    max_preview = max(1, int(resolved.get("max_preview_lines") or 20))
    preview: list[dict[str, Any]] = []
    baseline_evidence: list[dict[str, Any]] = []
    current_evidence: list[dict[str, Any]] = []
    baseline_evidence_total = 0
    current_evidence_total = 0
    for operation, line in line_changes:
        category, label = _category(line, resolved)
        category_counts[category] += 1
        category_labels[category] = label
        redacted, sensitive = _redact_line(line, resolved)
        sensitive_line_count += int(sensitive)
        evidence_row = {
            "category": category,
            "category_label": label,
            "text": redacted,
        }
        if operation == "removed":
            baseline_evidence_total += 1
            if len(baseline_evidence) < max_preview:
                baseline_evidence.append(evidence_row)
        elif operation == "added":
            current_evidence_total += 1
            if len(current_evidence) < max_preview:
                current_evidence.append(evidence_row)
        if len(preview) < max_preview:
            preview.append(
                {
                    "operation": operation,
                    "category": category,
                    "category_label": label,
                    "text": redacted,
                }
            )
    categories = [
        {
            "name": name,
            "label": category_labels[name],
            "line_count": count,
        }
        for name, count in category_counts.most_common()
    ]
    return {
        "schema_version": "unstructured-diff/v1",
        "command": " ".join(str(command or "").strip().lower().split()),
        "profile": str(resolved.get("name") or "generic"),
        "profile_label": str(resolved.get("label") or "非结构化输出"),
        "profile_version": int(resolved.get("version") or 1),
        "policy_digest": str(resolved.get("policy_digest") or _digest(resolved)),
        "comparison_mode": str(resolved.get("mode") or "ordered_lines"),
        "raw_changed": raw_changed,
        "changed": changed,
        "noise_only": raw_changed and not changed,
        "previous_normalized_hash": previous_hash,
        "current_normalized_hash": current_hash,
        "previous_line_count": len(previous_lines),
        "current_line_count": len(current_lines),
        "added_line_count": added if changed else 0,
        "removed_line_count": removed if changed else 0,
        "changed_line_count": (added + removed) if changed else 0,
        "changed_block_count": blocks if changed else 0,
        "ignored_line_count": previous_ignored + current_ignored,
        "similarity_ratio": round(similarity, 6),
        "sensitive_line_count": sensitive_line_count,
        "categories": categories,
        "preview": preview if changed else [],
        "preview_truncated": len(line_changes) > len(preview),
        "baseline_evidence": {
            "normalized_line_count": len(previous_lines),
            "changed_line_count": removed if changed else 0,
            "excerpt": baseline_evidence if changed else [],
            "excerpt_truncated": baseline_evidence_total > len(baseline_evidence),
        },
        "current_evidence": {
            "normalized_line_count": len(current_lines),
            "changed_line_count": added if changed else 0,
            "excerpt": current_evidence if changed else [],
            "excerpt_truncated": current_evidence_total > len(current_evidence),
        },
    }


def summarize_unstructured_changes(
    changes: list[dict[str, Any]],
    *,
    collected_output_count: int = 0,
    compared_output_count: int | None = None,
) -> dict[str, Any]:
    """Aggregate persisted raw-output Change facts for reports and APIs."""

    items: list[dict[str, Any]] = []
    category_counts: Counter[str] = Counter()
    profile_counts: Counter[str] = Counter()
    added = 0
    removed = 0
    for change in changes:
        dimensions = dict(change.get("dimensions") or {})
        diff = dimensions.get("unstructured_diff")
        if not isinstance(diff, dict) or not diff.get("changed"):
            continue
        categories = list(diff.get("categories") or [])
        for category in categories:
            category_label = str(
                category.get("label") or category.get("name") or "其他变化"
            )
            category_counts[category_label] += int(category.get("line_count") or 0)
        profile_label = str(diff.get("profile_label") or diff.get("profile") or "非结构化输出")
        profile_counts[profile_label] += 1
        added += int(diff.get("added_line_count") or 0)
        removed += int(diff.get("removed_line_count") or 0)
        preview = list(diff.get("preview") or [])
        baseline = dict(diff.get("baseline_evidence") or {})
        current = dict(diff.get("current_evidence") or {})
        comparison_evidence = dict(change.get("comparison_evidence") or {})
        baseline_raw = dict(comparison_evidence.get("baseline") or {})
        current_raw = dict(comparison_evidence.get("current") or {})
        legacy_baseline = [
            {
                "category": row.get("category"),
                "category_label": row.get("category_label"),
                "text": row.get("text"),
            }
            for row in preview
            if row.get("operation") == "removed"
        ]
        legacy_current = [
            {
                "category": row.get("category"),
                "category_label": row.get("category_label"),
                "text": row.get("text"),
            }
            for row in preview
            if row.get("operation") == "added"
        ]
        items.append(
            {
                "change_id": change.get("id") or change.get("change_id"),
                "device_id": change.get("device_id"),
                "device_name": change.get("device_name") or change.get("device_id"),
                "command": change.get("command"),
                "profile": diff.get("profile"),
                "profile_label": profile_label,
                "comparison_kind": "unstructured_text",
                "parser_failure_text_comparison": bool(
                    diff.get("parser_failure_text_comparison")
                ),
                "added_line_count": int(diff.get("added_line_count") or 0),
                "removed_line_count": int(diff.get("removed_line_count") or 0),
                "changed_line_count": int(diff.get("changed_line_count") or 0),
                "similarity_ratio": float(diff.get("similarity_ratio") or 0),
                "categories": categories,
                "preview": preview[:4],
                "preview_truncated": bool(diff.get("preview_truncated")) or len(preview) > 4,
                "previous_snapshot_id": change.get("previous_snapshot_id"),
                "current_snapshot_id": change.get("current_snapshot_id"),
                "baseline_evidence": {
                    "snapshot_id": (
                        baseline_raw.get("snapshot_id")
                        or change.get("previous_snapshot_id")
                    ),
                    "observed_at": baseline_raw.get("observed_at"),
                    "parse_status": baseline_raw.get("parse_status"),
                    "raw_text": baseline_raw.get("raw_text"),
                    "normalized_line_count": int(
                        baseline.get("normalized_line_count")
                        or diff.get("previous_line_count")
                        or 0
                    ),
                    "changed_line_count": int(
                        baseline.get("changed_line_count")
                        or diff.get("removed_line_count")
                        or 0
                    ),
                    "excerpt": list(baseline.get("excerpt") or legacy_baseline)[:12],
                    "excerpt_truncated": bool(baseline.get("excerpt_truncated")),
                },
                "current_evidence": {
                    "snapshot_id": (
                        current_raw.get("snapshot_id")
                        or change.get("current_snapshot_id")
                    ),
                    "observed_at": current_raw.get("observed_at"),
                    "parse_status": current_raw.get("parse_status"),
                    "raw_text": current_raw.get("raw_text"),
                    "normalized_line_count": int(
                        current.get("normalized_line_count")
                        or diff.get("current_line_count")
                        or 0
                    ),
                    "changed_line_count": int(
                        current.get("changed_line_count")
                        or diff.get("added_line_count")
                        or 0
                    ),
                    "excerpt": list(current.get("excerpt") or legacy_current)[:12],
                    "excerpt_truncated": bool(current.get("excerpt_truncated")),
                },
            }
        )
    items.sort(
        key=lambda item: (
            -int(item["changed_line_count"]),
            str(item.get("device_name") or ""),
            str(item.get("command") or ""),
        )
    )
    compared = len(items) if compared_output_count is None else int(compared_output_count)
    return {
        "schema_version": "unstructured-comparison-summary/v1",
        "collected_output_count": int(collected_output_count),
        "compared_output_count": compared,
        "changed_output_count": len(items),
        "unchanged_output_count": max(0, compared - len(items)),
        "added_line_count": added,
        "removed_line_count": removed,
        "by_profile": dict(profile_counts),
        "by_category": dict(category_counts),
        "items": items,
    }
