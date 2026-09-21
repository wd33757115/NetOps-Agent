"""Immutable TextFSM asset storage."""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
from pathlib import Path

from src.apps.rule_governance.paths import ROOT as PROJECT_ROOT  # noqa: F401 - legacy export
from src.apps.rule_governance.paths import TEMPLATE_STORE

DEFAULT_ASSET_ROOT = TEMPLATE_STORE


def safe_slug(value: str) -> str:
    normalized = re.sub(r"\s+", "_", str(value or "").strip())
    normalized = re.sub(r"[^A-Za-z0-9._-]+", "_", normalized)
    return re.sub(r"_+", "_", normalized).strip("._") or "Generic"


class TemplateAssetStore:
    def __init__(self, root: str | Path | None = None):
        self.root = Path(root or DEFAULT_ASSET_ROOT)

    def version_path(
        self, *, vendor: str, profile: str, command: str, version: str
    ) -> Path:
        filename = (
            f"{safe_slug(command.lower())}__{safe_slug(version)}.textfsm"
        )
        return self.root / safe_slug(vendor) / safe_slug(profile) / filename

    def write_immutable(
        self,
        *,
        vendor: str,
        profile: str,
        command: str,
        version: str,
        content: str,
    ) -> tuple[Path, str]:
        target = self.version_path(
            vendor=vendor, profile=profile, command=command, version=version
        )
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        if target.exists():
            existing = hashlib.sha256(target.read_bytes()).hexdigest()
            if existing != digest:
                raise FileExistsError(f"immutable parser asset already exists: {target}")
            return target, digest
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=f".{target.name}.", dir=str(target.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(content)
            os.replace(temporary, target)
        except Exception:
            try:
                os.unlink(temporary)
            except OSError:
                pass
            raise
        return target, digest

    def verify(
        self,
        path: str | Path,
        sha256: str,
        embedded_text: str | None = None,
    ) -> bool:
        if embedded_text is not None:
            return hashlib.sha256(embedded_text.encode("utf-8")).hexdigest() == sha256
        target = Path(path)
        return target.is_file() and hashlib.sha256(target.read_bytes()).hexdigest() == sha256

    def read(
        self,
        path: str | Path,
        sha256: str,
        embedded_text: str | None = None,
    ) -> str:
        if embedded_text is not None:
            if not self.verify(path, sha256, embedded_text):
                raise ValueError("embedded Parser asset hash mismatch")
            return embedded_text
        target = Path(path)
        if not self.verify(target, sha256):
            raise ValueError("Parser asset file is missing or its hash does not match")
        return target.read_text(encoding="utf-8")
