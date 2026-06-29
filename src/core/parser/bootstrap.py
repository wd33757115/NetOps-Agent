"""Import locally generated shared templates into the authoritative registry."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .assets import DEFAULT_ASSET_ROOT, TemplateAssetStore
from .repository import ParserRepository


def _candidate_template_paths(
    *,
    source_root: Path,
    assets: TemplateAssetStore,
    vendor: str,
    model: str,
    command: str,
    version: str,
    manifest_path: str | None,
) -> list[Path]:
    candidates: list[Path] = []
    if manifest_path:
        candidates.append(source_root / manifest_path)
    candidates.append(
        assets.version_path(
            vendor=vendor,
            profile=model,
            command=command,
            version=version,
        )
    )
    if manifest_path:
        legacy = Path(manifest_path)
        candidates.append(source_root / vendor / model / legacy.name)
    seen: set[str] = set()
    unique: list[Path] = []
    for candidate in candidates:
        key = str(candidate)
        if key in seen:
            continue
        seen.add(key)
        unique.append(candidate)
    return unique


def _read_template_content(paths: list[Path]) -> tuple[Path, str]:
    for path in paths:
        if path.is_file():
            return path, path.read_text(encoding="utf-8")
    raise FileNotFoundError(
        "template asset not found; tried: "
        + ", ".join(str(path) for path in paths)
    )


def _activation_validation(entry: dict[str, Any]) -> dict[str, Any]:
    validation = dict(entry)
    pass_rate = float(validation.get("validation_pass_rate") or 0)
    sample_count = int(validation.get("sample_count") or 0)
    validation.setdefault(
        "regression_validation",
        {
            "passed": pass_rate >= 1.0 and sample_count > 0,
            "validation_pass_rate": pass_rate,
            "sample_count": sample_count,
            "source": "shared_manifest_bootstrap",
        },
    )
    return validation


def bootstrap_shared_templates(root: str | Path | None = None) -> dict[str, Any]:
    source_root = Path(root or DEFAULT_ASSET_ROOT)
    manifest_path = source_root / "manifest.json"
    if not manifest_path.is_file():
        return {"imported": 0, "skipped": 0, "reason": "manifest_not_found"}
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    repository = ParserRepository()
    assets = TemplateAssetStore(source_root)
    imported = skipped = 0
    errors: list[str] = []
    for entry in manifest.get("templates") or []:
        try:
            vendor = str(entry["vendor"])
            model = str(entry["model"])
            command = str(entry["command"])
            version = str(entry.get("parser_version") or entry.get("sha256", "")[:12] or "1")
            if repository.find_template_version(
                vendor=vendor, profile=model, command=command, version=version
            ):
                skipped += 1
                continue
            asset_path, content = _read_template_content(
                _candidate_template_paths(
                    source_root=source_root,
                    assets=assets,
                    vendor=vendor,
                    model=model,
                    command=command,
                    version=version,
                    manifest_path=str(entry.get("template_path") or ""),
                )
            )
            target, digest = assets.write_immutable(
                vendor=vendor,
                profile=model,
                command=command,
                version=version,
                content=content,
            )
            template_id = repository.register_template(
                vendor=vendor,
                model=model,
                family=entry.get("family"),
                profile=model,
                command=command,
                category=str(entry.get("category") or "unknown"),
                version=version,
                asset_path=str(target),
                sha256=digest,
                validation={
                    **_activation_validation(entry),
                    "bootstrap_source_path": str(asset_path),
                },
                status="draft",
            )
            repository.activate_template(template_id)
            imported += 1
        except Exception as exc:
            errors.append(str(exc))
    return {"imported": imported, "skipped": skipped, "errors": errors}
