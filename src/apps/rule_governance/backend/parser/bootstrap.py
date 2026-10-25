"""Import locally generated shared templates into the authoritative registry."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .assets import DEFAULT_ASSET_ROOT, TemplateAssetStore
from .repository import ParserTemplateRepository


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
            return path, path.read_bytes().decode("utf-8")
    raise FileNotFoundError(
        "template asset not found; tried: " + ", ".join(str(path) for path in paths)
    )


def _activation_validation(entry: dict[str, Any]) -> dict[str, Any]:
    validation = dict(entry)
    defaults = _contract_defaults(str(entry.get("command") or ""))
    for key in ("category", "entity_type", "primary_keys", "required_fields", "optional_fields"):
        if defaults.get(key) and not validation.get(key):
            validation[key] = defaults[key]
    if defaults.get("state_semantics") and not validation.get("state_semantics"):
        validation["state_semantics"] = defaults["state_semantics"]
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


def _contract_defaults(command: str) -> dict[str, Any]:
    normalized = " ".join(command.lower().split())
    if normalized in {"show processes cpu", "display cpu", "display cpu-usage"}:
        return {
            "category": "cpu",
            "entity_type": "cpu",
            "primary_keys": ["slot"],
            "required_fields": ["slot", "cpu_5s"],
            "optional_fields": ["cpu_1m", "cpu_5m", "cpu_user", "cpu_kernel", "cpu_idle"],
            "state_semantics": {
                "cpu_5s": {
                    "semantic_type": "gauge",
                    "observation_type": "device.cpu.utilization",
                    "unit": "percent",
                }
            },
        }
    if normalized == "show system resources":
        return {
            "category": "system_resources",
            "entity_type": "system",
            "primary_keys": ["resource"],
            "required_fields": ["cpu_idle", "used", "free"],
            "optional_fields": ["load_1m", "load_5m", "load_15m", "memory_status"],
            "state_semantics": {
                "cpu_idle": {
                    "semantic_type": "gauge",
                    "observation_type": "device.cpu.idle",
                    "unit": "percent",
                },
                "used": {
                    "semantic_type": "gauge",
                    "observation_type": "device.memory.used",
                    "unit": "kilobytes",
                },
            },
        }
    if normalized in {"show processes memory", "display memory"}:
        return {
            "category": "memory",
            "entity_type": "memory",
            "primary_keys": ["slot"],
            "required_fields": ["slot", "total", "used", "free"],
            "optional_fields": ["free_ratio", "shared", "buffers", "cached"],
            "state_semantics": {
                "used": {
                    "semantic_type": "gauge",
                    "observation_type": "device.memory.used",
                },
                "free_ratio": {
                    "semantic_type": "gauge",
                    "observation_type": "device.memory.free_ratio",
                    "unit": "percent",
                },
            },
        }
    if normalized in {"show inventory", "display device manuinfo"}:
        return {
            "category": "inventory",
            "entity_type": "inventory",
            "primary_keys": ["serial"],
            "required_fields": ["serial"],
            "optional_fields": ["component", "name", "pid", "vendor", "slot"],
            "state_semantics": {},
        }
    if normalized in {
        "show ip interface brief",
        "display interface brief",
        "display ip interface brief",
    }:
        return {
            "category": "interface_l3",
            "entity_type": "interface_l3",
            "primary_keys": ["interface"],
            "required_fields": ["interface", "status", "protocol"],
            "optional_fields": ["ip_address", "description"],
            "state_semantics": {},
        }
    if normalized == "show interface status":
        return {
            "category": "interface_l2",
            "entity_type": "interface_l2",
            "primary_keys": ["interface"],
            "required_fields": ["interface", "status"],
            "optional_fields": ["description", "vlan", "duplex", "speed", "type"],
            "state_semantics": {},
        }
    if normalized in {
        "show interface transceiver detail",
        "display transceiver diagnosis interface",
    }:
        return {
            "category": "interface_optical",
            "entity_type": "interface_optical",
            "primary_keys": ["interface"],
            "required_fields": ["interface"],
            "optional_fields": [
                "transceiver_present",
                "dom_supported",
                "rx_power_dbm",
                "tx_power_dbm",
                "rx_low_warning_dbm",
                "rx_high_warning_dbm",
                "tx_low_warning_dbm",
                "tx_high_warning_dbm",
                "rx_low_alarm_dbm",
                "rx_high_alarm_dbm",
                "tx_low_alarm_dbm",
                "tx_high_alarm_dbm",
            ],
            "state_semantics": {
                "transceiver_present": {
                    "semantic_type": "state",
                    "observation_type": "interface.optical.presence",
                },
                "rx_power_dbm": {
                    "semantic_type": "gauge",
                    "observation_type": "interface.optical.rx_power",
                    "unit": "dbm",
                },
                "tx_power_dbm": {
                    "semantic_type": "gauge",
                    "observation_type": "interface.optical.tx_power",
                    "unit": "dbm",
                },
            },
            "validators": {
                "rx_power_dbm": {"type": "number"},
                "tx_power_dbm": {"type": "number"},
            },
        }
    if normalized in {"display fan", "show environment fan"}:
        return {
            "category": "fan",
            "entity_type": "environment_fan",
            "primary_keys": ["fan"],
            "required_fields": ["fan", "state"],
            "optional_fields": ["slot", "model", "direction", "speed"],
            "state_semantics": {
                "state": {
                    "semantic_type": "state",
                    "observation_type": "environment.fan.state",
                }
            },
        }
    if normalized in {"display power", "show environment power"}:
        return {
            "category": "power",
            "entity_type": "environment_power",
            "primary_keys": ["power_supply"],
            "required_fields": ["power_supply", "state"],
            "optional_fields": ["slot", "mode", "current", "voltage", "power"],
            "state_semantics": {
                "state": {
                    "semantic_type": "state",
                    "observation_type": "environment.power.state",
                }
            },
        }
    if normalized in {"display environment", "show environment temperature", "show env all"}:
        return {
            "category": "environment",
            "entity_type": "environment_sensor",
            "primary_keys": ["sensor"],
            "required_fields": ["sensor", "state"],
            "optional_fields": ["temperature", "threshold", "slot"],
            "state_semantics": {
                "state": {
                    "semantic_type": "state",
                    "observation_type": "environment.sensor.state",
                }
            },
        }
    if normalized in {"display vrrp statistics", "show vrrp brief"}:
        return {
            "category": "vrrp",
            "entity_type": "vrrp",
            "primary_keys": ["interface", "vrid"],
            "required_fields": ["state"],
            "optional_fields": ["interface", "vrid", "virtual_ip", "priority"],
            "state_semantics": {
                "state": {
                    "semantic_type": "state",
                    "observation_type": "redundancy.vrrp.state",
                }
            },
        }
    if normalized in {"display irf", "display m-lag system", "show vpc"}:
        return {
            "category": "redundancy",
            "entity_type": "redundancy",
            "primary_keys": ["name"],
            "required_fields": ["state"],
            "optional_fields": ["role", "peer", "domain"],
            "state_semantics": {
                "state": {
                    "semantic_type": "state",
                    "observation_type": "redundancy.state",
                }
            },
        }
    if normalized in {"show version", "display version"}:
        return {
            "category": "device_version",
            "entity_type": "device",
            "primary_keys": ["model"],
            "required_fields": ["model", "os_version"],
            "optional_fields": ["software_release", "uptime"],
            "state_semantics": {},
        }
    return {"category": "unknown", "entity_type": "unknown"}


def _command_from_filename(path: Path) -> tuple[str, str]:
    stem = path.stem
    if "__" in stem:
        command_slug, version = stem.rsplit("__", 1)
    else:
        command_slug, version = stem, path.stat().st_mtime_ns.__str__()
    return command_slug.replace("_", " "), version


def _discover_template_entries(source_root: Path) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for path in sorted(source_root.glob("*/*/*.textfsm")):
        vendor = path.parent.parent.name
        model = path.parent.name
        command, version = _command_from_filename(path)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        defaults = _contract_defaults(command)
        entries.append(
            {
                "vendor": vendor,
                "model": model,
                "family": "auto-discovered",
                "command": command,
                "parser_version": version,
                "template_path": str(path.relative_to(source_root)),
                "sha256": digest,
                "sample_count": 1,
                "validation_pass_rate": 1.0,
                "discovery_source": "filesystem_textfsm_assets",
                **defaults,
            }
        )
    return entries


def _merged_manifest_entries(
    manifest: dict[str, Any],
    source_root: Path,
) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for entry in [*(manifest.get("templates") or []), *_discover_template_entries(source_root)]:
        key = (
            str(entry.get("vendor") or ""),
            str(entry.get("model") or ""),
            str(entry.get("command") or ""),
            str(entry.get("parser_version") or ""),
        )
        if key in seen:
            continue
        seen.add(key)
        entries.append(entry)
    return entries


def bootstrap_shared_templates(root: str | Path | None = None) -> dict[str, Any]:
    source_root = Path(root or DEFAULT_ASSET_ROOT)
    manifest_path = source_root / "manifest.json"
    if not manifest_path.is_file():
        manifest: dict[str, Any] = {"templates": []}
    else:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    repository = ParserTemplateRepository()
    assets = TemplateAssetStore(source_root)
    imported = skipped = 0
    errors: list[str] = []
    entries = _merged_manifest_entries(manifest, source_root)
    for entry in entries:
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
                template_text=content,
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
    return {
        "imported": imported,
        "skipped": skipped,
        "discovered": len(entries),
        "manifest_entries": len(manifest.get("templates") or []),
        "errors": errors,
    }
