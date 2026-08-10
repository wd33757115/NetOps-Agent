"""Freeze and compare ntc_templates candidates against command-output samples."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.parser_generation import (  # noqa: E402
    NtcResolution,
    NtcTemplateResolver,
    ParserSample,
    SampleRole,
    compare_ntc_resolution,
    freeze_ntc_resolution,
)

INPUT_SCHEMA = "ntc-regression-input/v1"
SUITE_SCHEMA = "ntc-regression-suite/v1"
REPORT_SCHEMA = "ntc-regression-report/v1"


def _stable_digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _decode_text(data: bytes) -> str:
    for encoding in (
        "utf-8-sig",
        "utf-8",
        "utf-16",
        "utf-16-le",
        "utf-16-be",
        "gb18030",
        "gbk",
    ):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("manifest root must be an object")
    return payload


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _sample_path(base_dir: Path, value: Any) -> Path:
    text = str(value or "").strip()
    if not text:
        raise ValueError("sample path is required")
    path = Path(text)
    if not path.is_absolute():
        path = base_dir / path
    path = path.resolve()
    if not path.is_file():
        raise ValueError(f"sample file does not exist: {path}")
    return path


def _load_samples(
    entry: dict[str, Any],
    *,
    manifest_dir: Path,
) -> tuple[tuple[ParserSample, ...], list[tuple[dict[str, Any], Path, str]]]:
    samples: list[ParserSample] = []
    sources: list[tuple[dict[str, Any], Path, str]] = []
    for item in entry.get("samples") or ():
        source = dict(item)
        sample_id = str(source.get("sample_id") or "").strip()
        if not sample_id:
            raise ValueError("sample_id is required")
        path = _sample_path(manifest_dir, source.get("path"))
        source_bytes = path.read_bytes()
        raw_text = _decode_text(source_bytes)
        digest = hashlib.sha256(source_bytes).hexdigest()
        expected_digest = str(source.get("sha256") or "").strip()
        if expected_digest and expected_digest != digest:
            raise ValueError(f"sample digest mismatch: {sample_id}")
        role = SampleRole(str(source.get("role") or SampleRole.GENERATION.value))
        samples.append(
            ParserSample(
                sample_id=sample_id,
                raw_text=raw_text,
                prompt_text=raw_text if role is SampleRole.GENERATION else None,
                role=role,
            )
        )
        sources.append((source, path, digest))
    if not samples:
        raise ValueError("each NTC regression entry requires at least one sample")
    return tuple(samples), sources


def _select_resolution(
    resolutions: tuple[NtcResolution, ...],
    *,
    expected_platform: str | None = None,
) -> NtcResolution:
    if expected_platform:
        matches = [
            item for item in resolutions if item.platform == expected_platform
        ]
        if len(matches) == 1:
            return matches[0]
        command = resolutions[0].command if resolutions else ""
        return NtcResolution(
            status="not_found",
            platform=expected_platform,
            command=command,
            template_name=None,
            candidate=None,
            execution_evidence={},
            error=(
                "frozen NTC platform is no longer uniquely resolvable: "
                f"{expected_platform}"
            ),
        )
    resolved = [item for item in resolutions if item.status == "resolved"]
    if len(resolved) == 1:
        return resolved[0]
    if len(resolutions) == 1:
        return resolutions[0]
    raise ValueError("NTC platform mapping is ambiguous; freeze a narrower identity")


def _sample_refs(
    sources: list[tuple[dict[str, Any], Path, str]],
    *,
    output_dir: Path,
) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    for source, path, digest in sources:
        relative = os.path.relpath(path, output_dir).replace("\\", "/")
        refs.append(
            {
                "sample_id": str(source["sample_id"]),
                "path": relative,
                "role": str(source.get("role") or SampleRole.GENERATION.value),
                "sha256": digest,
            }
        )
    return refs


def freeze_suite(manifest_path: Path, output_path: Path) -> dict[str, Any]:
    manifest = _load_json(manifest_path)
    if manifest.get("schema_version") != INPUT_SCHEMA:
        raise ValueError(f"freeze requires schema_version={INPUT_SCHEMA}")
    resolver = NtcTemplateResolver()
    entries: list[dict[str, Any]] = []
    for raw_entry in manifest.get("entries") or ():
        entry = dict(raw_entry)
        entry_id = str(entry.get("id") or "").strip()
        if not entry_id:
            raise ValueError("entry id is required")
        samples, sources = _load_samples(
            entry,
            manifest_dir=manifest_path.parent,
        )
        _decision, resolutions = resolver.resolve(
            device=dict(entry.get("device") or {}),
            command=str(entry.get("command") or ""),
            samples=samples,
        )
        resolution = _select_resolution(resolutions)
        entries.append(
            {
                "id": entry_id,
                "device": dict(entry.get("device") or {}),
                "command": str(entry.get("command") or ""),
                "samples": _sample_refs(sources, output_dir=output_path.parent),
                "baseline": freeze_ntc_resolution(resolution),
            }
        )
    payload = {
        "schema_version": SUITE_SCHEMA,
        "entries": entries,
        "summary": {
            "entry_count": len(entries),
            "package_version": resolver.package_version,
        },
    }
    payload["suite_digest"] = _stable_digest(payload)
    _write_json(output_path, payload)
    return payload


def compare_suite(manifest_path: Path, output_path: Path) -> dict[str, Any]:
    manifest = _load_json(manifest_path)
    if manifest.get("schema_version") != SUITE_SCHEMA:
        raise ValueError(f"compare requires schema_version={SUITE_SCHEMA}")
    frozen = dict(manifest)
    suite_digest = str(frozen.pop("suite_digest", "") or "")
    if not suite_digest or suite_digest != _stable_digest(frozen):
        raise ValueError("NTC regression suite digest mismatch")

    resolver = NtcTemplateResolver()
    results: list[dict[str, Any]] = []
    for raw_entry in manifest.get("entries") or ():
        entry = dict(raw_entry)
        samples, _sources = _load_samples(
            entry,
            manifest_dir=manifest_path.parent,
        )
        baseline = dict(entry.get("baseline") or {})
        _decision, resolutions = resolver.resolve(
            device=dict(entry.get("device") or {}),
            command=str(entry.get("command") or ""),
            samples=samples,
        )
        resolution = _select_resolution(
            resolutions,
            expected_platform=str(baseline.get("platform") or "") or None,
        )
        results.append(
            {
                "id": str(entry.get("id") or ""),
                "decision": compare_ntc_resolution(baseline, resolution),
            }
        )
    breaking_count = sum(
        1 for item in results if item["decision"].get("breaking") is True
    )
    review_count = sum(
        1
        for item in results
        if item["decision"].get("requires_human_review") is True
    )
    payload = {
        "schema_version": REPORT_SCHEMA,
        "suite_digest": suite_digest,
        "current_package_version": resolver.package_version,
        "summary": {
            "entry_count": len(results),
            "unchanged_count": len(results) - review_count,
            "review_required_count": review_count,
            "breaking_count": breaking_count,
            "automatic_replacement_allowed": False,
        },
        "results": results,
    }
    payload["report_digest"] = _stable_digest(payload)
    _write_json(output_path, payload)
    return payload


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Freeze NTC template evidence before dependency upgrades and compare "
            "the installed package afterwards."
        )
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("freeze", "compare"):
        command = subparsers.add_parser(name)
        command.add_argument("--manifest", type=Path, required=True)
        command.add_argument("--output", type=Path, required=True)
    return parser


def main() -> int:
    args = _parser().parse_args()
    manifest = args.manifest.resolve()
    output = args.output.resolve()
    payload = (
        freeze_suite(manifest, output)
        if args.command == "freeze"
        else compare_suite(manifest, output)
    )
    summary = dict(payload.get("summary") or {})
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    if args.command == "compare" and int(summary.get("review_required_count") or 0):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
