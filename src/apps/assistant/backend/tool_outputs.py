"""Protected business tool results, with conservative recovery for older turns."""

import json
from datetime import datetime, timezone

from . import evidence, tool_trace


def from_callback(tool, output, references):
    """Resolve only the server-captured citation, never trust callback result text."""
    short = tool.removeprefix("mcp__netops__")

    def candidates(value):
        if isinstance(value, str):
            try:
                return candidates(json.loads(value))
            except (ValueError, TypeError):
                return []
        if isinstance(value, list):
            return [item for child in value for item in candidates(child)]
        if not isinstance(value, dict):
            return []
        citation = value.get("citation")
        if isinstance(citation, str):
            reference = references.get(citation.removeprefix("[").removesuffix("]"))
            if reference and reference.get("tool") == short:
                return [reference]
        # DSH's MCP adapter wraps official TextContent in its result value.
        return [
            item
            for key in ("content", "text", "value", "structuredContent")
            if key in value
            for item in candidates(value[key])
        ]

    matches = {item["id"]: item for item in candidates(output)}
    return evidence.validate(next(iter(matches.values()))) if len(matches) == 1 else None


def _time(value):
    try:
        parsed = datetime.fromisoformat(value)
        return parsed.replace(tzinfo=parsed.tzinfo or timezone.utc)
    except (ValueError, TypeError):
        return None


def _matches(step, reference):
    tool = step.get("tool") or step.get("operation", "").split("(", 1)[0]
    if tool.removeprefix("mcp__netops__") != reference.get("tool"):
        return False
    start, end, retrieved = (
        _time(step.get("started_at")),
        _time(step.get("ended_at")),
        _time(reference.get("retrieved_at")),
    )
    return bool(start and end and retrieved and start <= retrieved <= end)


def reference_for(step, progress, references):
    saved = progress.get("outputs", {}).get(step["id"])
    if saved:
        return saved
    matches = [
        reference
        for reference in references
        if reference.get("kind") == "business" and _matches(step, reference)
    ]
    if len(matches) != 1:
        return None
    # Concurrent identical calls cannot be reconstructed safely from time alone.
    reference = matches[0]
    if sum(_matches(other, reference) for other in progress.get("steps", [])) != 1:
        return None
    return reference


def mark_available(progress, references):
    if progress:
        for step in progress.get("steps", []):
            if not step.get("has_output") and reference_for(step, progress, references):
                step["has_output"] = True
    return progress


def preview(reference):
    evidence.validate(reference)
    excerpt = reference["excerpt"]
    if len(excerpt.encode("utf-8")) > evidence.MAX_EVIDENCE_BYTES:
        raise ValueError("Business evidence exceeds budget")
    # Capture already bounds each snapshot to 24 KB. Preserve valid JSON and each
    # command's own output_truncated flag instead of slicing a JSON object mid-string.
    return tool_trace.result_preview(None, excerpt, limit=None)
