"""Bridge config-diff-tool into patrol raw_hash_diff / backup compare paths."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any


def _load_run_config_diff():
    script = (
        Path(__file__).resolve().parents[2]
        / "skills"
        / "config-diff-tool"
        / "scripts"
        / "run.py"
    )
    spec = importlib.util.spec_from_file_location("config_diff_tool_run", script)
    if spec is None or spec.loader is None:
        raise ImportError(f"unable to load config-diff-tool entry: {script}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.run_config_diff


def compare_config_texts(
    before: str,
    after: str,
    *,
    device_type: str | None = None,
) -> dict[str, Any]:
    """Run config-diff-tool and normalize for change-detector metadata."""
    run_config_diff = _load_run_config_diff()
    result = run_config_diff(
        {
            "config1": before or "",
            "config2": after or "",
            "device_type": device_type,
        }
    )
    data = dict(result.get("data") or {}) if isinstance(result, dict) else {}
    changed = bool(data.get("has_diff") or data.get("changed") or data.get("diff"))
    if "has_diff" not in data and "changed" not in data:
        # Fallback: treat non-empty textual diff as change.
        changed = bool(str(data.get("diff") or data.get("unified_diff") or "").strip())
    return {
        "success": bool(result.get("success")) if isinstance(result, dict) else False,
        "changed": changed,
        "handling": "raw_hash_diff",
        "comparison_mode": "config_diff_tool",
        "detail": data,
        "error": result.get("error") if isinstance(result, dict) else None,
    }


def looks_like_config_command(command: str | None) -> bool:
    text = str(command or "").strip().lower()
    markers = (
        "display current-configuration",
        "display current configuration",
        "show running-config",
        "show configuration",
        "show run",
        "current-configuration",
    )
    return any(marker in text for marker in markers)
