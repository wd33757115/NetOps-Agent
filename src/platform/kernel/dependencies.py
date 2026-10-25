"""Validate exact package requirements against the host's shared environment."""

import json
from importlib.metadata import version

from packaging.requirements import Requirement

from .registry import ROOT


def check_dependencies(manifest, *, frontend=False):
    directory = manifest.path.parent
    resolved = {}
    lock = directory / "requirements.lock"
    if lock.exists() and not frontend:
        for line in lock.read_text(encoding="utf-8").splitlines():
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            req = Requirement(line)
            specs = list(req.specifier)
            if (
                req.url
                or req.marker
                or req.extras
                or len(specs) != 1
                or specs[0].operator != "=="
                or "*" in specs[0].version
            ):
                raise ValueError("Package Python requirements must use exact == pins")
            actual = version(req.name)
            if actual not in req.specifier:
                raise ValueError(f"Host Python dependency mismatch: {req.name}")
            resolved[req.name] = actual
    package = directory / "package.json"
    if package.exists():
        lock = json.loads(
            (ROOT / "web/react_frontend/package-lock.json").read_text(encoding="utf-8")
        )
        for name, required in (
            json.loads(package.read_text(encoding="utf-8")).get("dependencies", {}).items()
        ):
            actual = lock["packages"].get("node_modules/" + name, {}).get("version")
            if actual != required:
                raise ValueError(
                    f"Package {manifest.id}: pin {name} to host lock ({actual}); run npm ci"
                )
            resolved[name] = actual
    return resolved
