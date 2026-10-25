"""Static application discovery. Import business code only during service assembly."""

from __future__ import annotations

import hashlib
import importlib
import logging
import os
import re
from copy import deepcopy
from functools import lru_cache
from dataclasses import dataclass
from pathlib import Path

import yaml
from packaging.specifiers import SpecifierSet
from packaging.version import Version

_log = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[3]
APPS_ROOT = ROOT / "src/apps"
PLATFORM_VERSION = "1.0.0"


def package_digest(directory):
    """Fingerprint shipped code/resources; ignore development output, not migrations."""
    result = hashlib.sha256()
    ignored = {"__pycache__", "node_modules", ".pytest_cache", ".ruff_cache", "tests", "test-results"}
    files = []

    def collect(folder):
        # scandir reuses directory metadata on Windows and prunes excluded trees.
        # Still read every shipped file on every discovery: no stale digest cache.
        with os.scandir(folder) as entries:
            for entry in entries:
                if entry.name in ignored:
                    continue
                path = Path(entry.path)
                if entry.is_symlink():
                    raise ValueError(f"Package symlinks are not supported: {path.relative_to(directory)}")
                if entry.is_dir(follow_symlinks=False):
                    collect(path)
                elif entry.is_file(follow_symlinks=False) and path.suffix not in {".pyc", ".pyo"} and ".test." not in path.name:
                    files.append(path)

    collect(directory)
    for path in sorted(files):
        relative = path.relative_to(directory)
        content = path.read_bytes()
        # Git autocrlf must not change release identity between Windows and Linux.
        if path.suffix in {
            ".py",
            ".yaml",
            ".yml",
            ".json",
            ".ts",
            ".tsx",
            ".js",
            ".mjs",
            ".css",
            ".md",
            ".txt",
            ".lock",
        }:
            content = content.replace(b"\r\n", b"\n")
        result.update(relative.as_posix().encode() + b"\0")
        result.update(hashlib.sha256(content).digest())
    return result.hexdigest()


@dataclass(frozen=True)
class AppManifest:
    path: Path
    data: dict
    digest: str

    @property
    def id(self):
        return self.data["id"]

    def public(self):
        return {
            "id": self.id,
            "name": self.data["name"],
            "version": self.data["version"],
            "icon": self.data["icon"],
            "digest": self.digest,
            "route": f"/apps/{self.id}",
            "aliases": self.data.get("aliases", []),
        }


@lru_cache(maxsize=128)
def _parse_manifest(content: str):
    # Keyed by file contents, not time: edits take effect on the next request.
    return yaml.safe_load(content)


def discover(root: Path = APPS_ROOT, *, app_id: str | None = None) -> dict[str, AppManifest]:
    requested_id = app_id
    manifests = {}
    for path in sorted(root.glob("*/app.yaml")):
        data = deepcopy(_parse_manifest(path.read_text(encoding="utf-8")))
        if not isinstance(data, dict) or data.get("manifest_version") != 1:
            raise ValueError(f"Unsupported manifest: {path}")
        app_id = data.get("id", "")
        if not re.fullmatch(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*", app_id) or app_id in manifests:
            raise ValueError(f"Invalid or duplicate app id: {app_id}")
        if not re.fullmatch(r"[a-z][a-z0-9_]*", path.parent.name):
            raise ValueError(f"Invalid Python package directory: {path.parent.name}")
        Version(data["version"])
        if Version(PLATFORM_VERSION) not in SpecifierSet(data["platform_api"]):
            raise ValueError(f"Incompatible platform API: {app_id}")
        for key in ("name", "icon"):
            if not isinstance(data.get(key), str) or not data[key].strip():
                raise ValueError(f"Missing {key}: {app_id}")
        entry = (path.parent / data["frontend"]["entry"]).resolve()
        if not entry.is_relative_to(path.parent.resolve()) or not entry.is_file():
            raise ValueError(f"Invalid frontend entry: {app_id}")
        flag = data["frontend"].get("enabled_env")
        if flag and not re.fullmatch(r"VITE_[A-Z0-9_]+", flag):
            raise ValueError(f"Invalid frontend feature flag: {app_id}")
        backend = data["backend"]
        symbols = [backend["router"], *backend.get("task_modules", [])]
        database = data.get("database", {"mode": "none"})
        if database.get("bootstrap"):
            if type(database.get("bootstrap_version")) is not int or database["bootstrap_version"] < 1:
                raise ValueError(f"Bootstrap requires a positive version: {app_id}")
            symbols.append(database["bootstrap"])
        if data.get("agent", {}).get("tools"):
            symbols.append(data["agent"]["tools"])
        if data.get("workflow", {}).get("actions"):
            symbols.append(data["workflow"]["actions"])
        lifecycle = data.get("lifecycle", {})
        for hook in ("drain", "pending"):
            if lifecycle.get(hook):
                symbols.append(lifecycle[hook])
        if backend.get("task_names") and not all(
            lifecycle.get(hook) for hook in ("drain", "pending")
        ):
            raise ValueError(f"Task packages require drain and pending hooks: {app_id}")
        prefix = f"src.apps.{path.parent.name}."
        for symbol in symbols:
            if (
                not isinstance(symbol, str)
                or not symbol.startswith(prefix)
                or not re.fullmatch(r"[A-Za-z_][\w.]*(?::[A-Za-z_]\w*)?", symbol)
            ):
                raise ValueError(f"Entrypoint outside package: {symbol}")
            module = symbol.split(":")[0].removeprefix(prefix)
            module_path = path.parent.joinpath(*module.split("."))
            if (
                not module_path.with_suffix(".py").is_file()
                and not (module_path / "__init__.py").is_file()
            ):
                raise ValueError(f"Missing module: {symbol}")
        database = data.get("database", {"mode": "none"})
        if database.get("mode") not in {"none", "legacy", "package"}:
            raise ValueError(f"Invalid database mode: {app_id}")
        if database["mode"] == "package":
            location = (path.parent / database["migrations"]).resolve()
            if (
                not location.is_relative_to(path.parent.resolve())
                or not (location / "versions").is_dir()
            ):
                raise ValueError(f"Invalid migration directory: {app_id}")
            if not re.fullmatch(r"[A-Za-z0-9_]{1,32}", database.get("head", "")):
                raise ValueError(f"Invalid migration head: {app_id}")
            if not database.get("tables"):
                raise ValueError(f"Package must declare table ownership: {app_id}")
        for table in database.get("tables", []):
            if not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", table):
                raise ValueError(f"Invalid owned table: {table}")
        if backend.get("task_names") and not re.fullmatch(
            r"[a-zA-Z0-9_.-]+", backend.get("queue", "")
        ):
            raise ValueError(f"Invalid task queue: {app_id}")
        manifests[app_id] = AppManifest(path, data, "")
    owners = {}
    for manifest in manifests.values():
        for table in manifest.data.get("database", {}).get("tables", []):
            if table in owners:
                raise ValueError(f"Duplicate table ownership: {table}")
            owners[table] = manifest.id
    ordered = {}
    visiting = set()

    def visit(app_id):
        if app_id in visiting:
            raise ValueError(f"Application dependency cycle: {app_id}")
        if app_id in ordered:
            return
        visiting.add(app_id)
        for dependency, constraint in manifests[app_id].data.get("dependencies", {}).items():
            if dependency not in manifests or Version(
                manifests[dependency].data["version"]
            ) not in SpecifierSet(constraint):
                raise ValueError(f"Unsatisfied dependency: {app_id} -> {dependency}")
            visit(dependency)
        visiting.remove(app_id)
        manifest = manifests[app_id]
        ordered[app_id] = AppManifest(manifest.path, manifest.data, package_digest(manifest.path.parent))

    for app_id in (manifests if requested_id is None else [requested_id] if requested_id in manifests else []):
        visit(app_id)
    return ordered


def load_symbol(reference):
    module, name = reference.split(":", 1)
    return getattr(importlib.import_module(module), name)


def migrated_skill_app(skill_name):
    for manifest in discover().values():
        if skill_name in manifest.data.get("agent", {}).get("legacy_skills", []):
            return manifest.id
    return None


def mount_apps(app):
    from fastapi import Depends

    from src.platform.authorization.client import require_app

    from .runtime import mount_readiness

    mounted = {}
    for manifest in discover().values():
        try:
            router = load_symbol(manifest.data["backend"]["router"])
        except Exception:
            # One application that cannot load stays unavailable (its deployment
            # probe fails); the gateway and every other application still start.
            _log.exception("Application %s not mounted", manifest.id)
            continue
        app.include_router(
            router,
            prefix=f"/api/apps/{manifest.id}",
            dependencies=[Depends(require_app(manifest.id))],
        )
        mounted[manifest.id] = manifest
    # Readiness reports only what this process actually serves.
    mount_readiness(app, mounted)


def task_configuration():
    from .dependencies import check_dependencies

    modules, routes, schedules = [], {}, {}
    for manifest in discover().values():
        try:
            check_dependencies(manifest)
        except Exception:
            # Its tasks would fail to import and take the worker down with them.
            _log.exception("Application %s tasks not loaded", manifest.id)
            continue
        backend = manifest.data["backend"]
        modules.extend(backend.get("task_modules", []))
        for name in backend.get("task_names", []):
            if name in routes:
                raise ValueError(f"Duplicate task name: {name}")
            routes[name] = {"queue": backend["queue"]}
        for schedule in backend.get("schedules", []):
            name = f"app.{manifest.id}.{schedule['name']}"
            schedules[name] = {
                "task": schedule["task"],
                "schedule": float(schedule["seconds"]),
                "options": {"queue": backend["queue"]},
            }
    return modules, routes, schedules


def package_queues():
    return sorted(
        {
            m.data["backend"]["queue"]
            for m in discover().values()
            if m.data["backend"].get("task_names")
        }
    )
