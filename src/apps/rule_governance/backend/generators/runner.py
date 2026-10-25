"""Bounded subprocess calls to fixed, package-owned business generators.

This is an internal service API, not a user-facing tool or an authorization gate.
It deliberately does not discover scripts from the host Skill registry.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent
GENERATORS = frozenset(
    {
        "textfsm-generator",
        "rule-target-planner",
        "change-policy-generator",
        "event-rule-generator",
        "rule-asset-approver",
        "event-rule-validator",
        "event-rule-replay",
    }
)


class GeneratorExecutionError(RuntimeError):
    def __init__(self, message: str, *, payload: dict | None = None):
        super().__init__(message)
        self.payload = dict(payload or {})


def _decode(value: bytes | str | None) -> str:
    if isinstance(value, str):
        return value
    try:
        return (value or b"").decode("utf-8")
    except UnicodeDecodeError:
        return (value or b"").decode("gbk", errors="replace")


def _payload(*streams: str) -> dict | None:
    for stream in streams:
        for line in reversed(stream.splitlines()):
            try:
                value = json.loads(line)
            except ValueError:
                continue
            if isinstance(value, dict):
                return value
    return None


def generator_metadata(name: str) -> dict[str, Any]:
    if name not in GENERATORS:
        raise GeneratorExecutionError(f"unknown rule governance generator: {name}")
    directory = ROOT / name.replace("-", "_")
    script = directory / "scripts" / "run.py"
    try:
        metadata = yaml.safe_load(
            (directory / "SKILL.md").read_text(encoding="utf-8-sig").split("---", 2)[1]
        )
    except (OSError, IndexError, yaml.YAMLError) as exc:
        raise GeneratorExecutionError(f"invalid generator metadata for {name}") from exc
    if not script.is_file() or not isinstance(metadata, dict):
        raise GeneratorExecutionError(f"incomplete generator package for {name}")
    return metadata


def execute_generator(name: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    metadata = generator_metadata(name)
    script = ROOT / name.replace("-", "_") / "scripts" / "run.py"
    timeout = metadata.get("execution_timeout_seconds", 600)
    if type(timeout) is not int or not 1 <= timeout <= 7200:
        raise GeneratorExecutionError(f"invalid execution timeout for {name}")
    env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
    with tempfile.TemporaryDirectory(prefix="netops-rule-generator-") as workdir:
        params_file = Path(workdir) / "params.json"
        params_file.write_text(
            json.dumps(
                {key: value for key, value in (params or {}).items() if not key.startswith("_")},
                ensure_ascii=False,
                default=str,
            ),
            encoding="utf-8",
        )
        try:
            result = subprocess.run(
                [sys.executable, str(script), "--params", str(params_file)],
                cwd=str(script.parent),
                env=env,
                capture_output=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as exc:
            payload = _payload(_decode(exc.stdout), _decode(exc.stderr)) or {}
            payload.update(
                success=False,
                error="skill_execution_timeout",
                failure_code="skill_execution_timeout",
                timeout_seconds=timeout,
            )
            raise GeneratorExecutionError(
                f"Generator timed out ({name}, {timeout}s)", payload=payload
            ) from exc
        except OSError as exc:
            raise GeneratorExecutionError(f"Generator could not start ({name}): {exc}") from exc
    stdout, stderr = _decode(result.stdout), _decode(result.stderr)
    payload = _payload(stdout, stderr)
    if result.returncode:
        raise GeneratorExecutionError(
            f"Generator failed ({name}): {(stderr or stdout).strip()}", payload=payload
        )
    # Successful execution must end with one JSON object, not an earlier log line.
    last = stdout.strip().splitlines()[-1:]
    final = _payload(*last)
    if final is None:
        raise GeneratorExecutionError(f"Generator returned no final JSON object ({name})")
    return final
