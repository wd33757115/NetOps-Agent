# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""Discover and group TextFSM samples from offline patrol captures."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.apps.rule_governance.backend.generators.textfsm_generator.scripts.config_loader import CommandMapping, normalize_command
from src.apps.rule_governance.backend.parser.identity import DeviceIdentity

from src.apps.rule_governance.backend.parser.identity import DeviceIdentityResolver
from src.apps.rule_governance.backend.parser.policy import ParserPolicy
from src.core.patrol.command_splitter import infer_device_from_filename, split_cli_capture
from src.core.patrol.raw_importer import _read_text_best_effort


@dataclass(frozen=True)
class TemplateSample:
    file_path: str
    device_id: str
    command: str
    output: str
    prompt_output: str | None = None


@dataclass(frozen=True)
class DirectoryCandidate:
    vendor: str
    model: str
    family: str | None
    command: str
    category: str | None
    samples: tuple[TemplateSample, ...]
    confidence: float
    evidence_command: str | None
    evidence_text: str | None


@dataclass(frozen=True)
class DirectoryDiscovery:
    files_scanned: int
    devices_detected: int
    device_profiles: tuple[dict[str, Any], ...]
    candidates: tuple[DirectoryCandidate, ...]
    skipped_commands: tuple[dict[str, Any], ...]
    unresolved_files: tuple[str, ...]


def _discover_files(path: Path, recursive: bool) -> list[Path]:
    if path.is_file():
        return [path] if path.suffix.lower() in {".txt", ".log"} else []
    if not path.is_dir():
        raise FileNotFoundError(f"source_path does not exist: {path}")
    iterator = path.rglob("*") if recursive else path.glob("*")
    return sorted(
        item for item in iterator if item.is_file() and item.suffix.lower() in {".txt", ".log"}
    )


def discover_directory_candidates(
    source_path: str | Path,
    *,
    mappings: list[CommandMapping] | None = None,
    signatures: dict[str, Any] | None = None,
    aliases: dict[str, str] | None = None,
    recursive: bool = True,
    vendor: str | None = None,
    model: str | None = None,
    command_filter: str | None = None,
) -> DirectoryDiscovery:
    paths = _discover_files(Path(source_path), recursive)
    grouped: dict[tuple[str, str, str], list[TemplateSample]] = defaultdict(list)
    identities: dict[tuple[str, str], DeviceIdentity] = {}
    skipped: list[dict[str, Any]] = []
    unresolved: list[str] = []
    seen_samples: set[tuple[str, str, str]] = set()
    normalized_filter = normalize_command(command_filter or "", aliases) or None
    policy = ParserPolicy()
    identity_resolver = DeviceIdentityResolver(policy=policy)

    for path in paths:
        device_name, ip = infer_device_from_filename(path)
        blocks = split_cli_capture(_read_text_best_effort(path), device_name=device_name)
        identity = identity_resolver.resolve(
            path=path,
            blocks=blocks,
            vendor=vendor,
            model=model,
        )
        if not identity.detected:
            unresolved.append(str(path))
            continue
        identities[(identity.vendor or "", identity.model or "")] = identity
        device_id = f"{device_name}-{ip}" if device_name and ip else device_name or path.stem

        for block in blocks:
            command = normalize_command(block.command_canonical, aliases)
            if normalized_filter and command != normalized_filter:
                continue
            decision = policy.decide(
                vendor=identity.vendor,
                model=identity.model,
                family=identity.family,
                command=command,
            )
            if not decision.enabled:
                skipped.append(
                    {
                        "file_path": str(path),
                        "device_id": device_id,
                        "vendor": identity.vendor,
                        "model": identity.model,
                        "command": command,
                        "reason": "command_not_configured",
                    }
                )
                continue
            sample_key = (str(path), command, block.raw_output)
            if sample_key in seen_samples:
                continue
            seen_samples.add(sample_key)
            grouped[(identity.vendor or "", identity.model or "", command)].append(
                TemplateSample(
                    file_path=str(path),
                    device_id=device_id,
                    command=command,
                    output=block.raw_output,
                )
            )

    candidates: list[DirectoryCandidate] = []
    for (candidate_vendor, candidate_model, command), samples in sorted(grouped.items()):
        identity = identities[(candidate_vendor, candidate_model)]
        candidates.append(
            DirectoryCandidate(
                vendor=candidate_vendor,
                model=candidate_model,
                family=identity.family,
                command=command,
                category=None,
                samples=tuple(samples),
                confidence=identity.confidence,
                evidence_command=identity.evidence_command,
                evidence_text=identity.evidence_text,
            )
        )

    profiles = tuple(
        {
            "vendor": item.vendor,
            "model": item.model,
            "family": item.family,
            "confidence": item.confidence,
            "evidence_command": item.evidence_command,
            "evidence_text": item.evidence_text,
            "source": item.source,
        }
        for item in sorted(
            identities.values(), key=lambda value: (value.vendor or "", value.model or "")
        )
    )
    return DirectoryDiscovery(
        files_scanned=len(paths),
        devices_detected=len(paths) - len(unresolved),
        device_profiles=profiles,
        candidates=tuple(candidates),
        skipped_commands=tuple(skipped),
        unresolved_files=tuple(unresolved),
    )
