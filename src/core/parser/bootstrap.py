"""Import locally generated shared templates into the authoritative registry."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .assets import DEFAULT_ASSET_ROOT, TemplateAssetStore
from .repository import ParserRepository


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
            legacy_path = source_root / str(entry["template_path"])
            content = legacy_path.read_text(encoding="utf-8")
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
                validation=dict(entry),
                status="draft",
            )
            repository.activate_template(template_id)
            imported += 1
        except Exception as exc:
            errors.append(str(exc))
    return {"imported": imported, "skipped": skipped, "errors": errors}
