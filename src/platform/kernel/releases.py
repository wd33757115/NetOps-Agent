"""Retain installed package code and migration history for recovery."""

import os
import zipfile
from uuid import uuid4

from .registry import ROOT, package_digest


def archive(manifest):
    if package_digest(manifest.path.parent) != manifest.digest:
        raise ValueError("Package changed during installation; retry from an immutable release")
    directory = ROOT / ".runtime/app-package-releases" / manifest.id
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / (manifest.digest + ".zip")
    if target.exists():
        return target
    temporary = directory / (uuid4().hex + ".tmp")
    try:
        with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED) as output:
            for path in sorted(manifest.path.parent.rglob("*")):
                relative = path.relative_to(manifest.path.parent)
                if path.is_file() and not any(
                    p in {"__pycache__", "node_modules", ".pytest_cache", ".ruff_cache"}
                    for p in relative.parts
                ):
                    output.write(path, relative.as_posix())
        if package_digest(manifest.path.parent) != manifest.digest:
            raise ValueError("Package changed while archiving; installation aborted")
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    return target
