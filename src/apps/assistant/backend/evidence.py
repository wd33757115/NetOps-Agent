"""Immutable, bounded business evidence snapshots in the existing message references."""

import json

from src.apps.materials.backend.documents import digest
from src.platform.clock import beijing

from .progress import LABELS
from .repository import now

MAX_EVIDENCE_BYTES = 24000


def source_apps(reference):
    """A live comparison embeds inspection data as well as device-access data."""
    # compare_config is the earlier single-snapshot name, kept for recorded citations.
    if reference.get("tool") in {
        "device_access__compare_baseline",
        "device_access__compare_rows",
        "device_access__compare_config",
    }:
        return ("device-access", "inspection")
    return (reference["app_id"],)


def capture(tool, result):
    # Only registered evidence tools can reach this adapter. They own field allowlists,
    # pagination and redaction; refusing oversize results preserves valid structured data.
    excerpt = json.dumps(result, ensure_ascii=False, sort_keys=True, default=str, indent=2)
    if len(excerpt.encode("utf-8")) > MAX_EVIDENCE_BYTES:
        raise ValueError("Business evidence exceeds budget; narrow the query or lower limit")
    app_id = tool.split("__", 1)[0].replace("_", "-")
    retrieved_at = beijing(now())
    revision = digest(excerpt)
    identifier = "evidence-" + digest(tool + retrieved_at + revision)[:24]
    return {
        "kind": "business",
        "id": identifier,
        "app_id": app_id,
        "tool": tool,
        "title_path": ["业务证据", LABELS.get("mcp__netops__" + tool, "业务查询")],
        "revision": revision,
        "excerpt": excerpt,
        "retrieved_at": retrieved_at,
        # List answers (one per network, per session) carry their own times inside.
        "observed_at": result.get("observed_at") if isinstance(result, dict) else None,
        "citation": f"[{identifier}]",
    }


def validate(reference):
    if reference.get("kind") != "business" or digest(reference["excerpt"]) != reference["revision"]:
        raise ValueError("Business evidence snapshot mismatch")
    return reference


def public_references(references):
    """Business excerpts require the source App check at the citation endpoint."""
    return [
        {
            key: value
            for key, value in reference.items()
            if reference.get("kind") != "business" or key != "excerpt"
        }
        for reference in (references or [])
    ]
