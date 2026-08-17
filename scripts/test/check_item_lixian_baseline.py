"""Build and verify the immutable check-item baseline for the lixian corpus."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.core.check_items import CheckItemImplementationResolver, load_catalog  # noqa: E402
from src.core.parser.identity import DeviceIdentityResolver  # noqa: E402
from src.core.parser.policy import ParserPolicy  # noqa: E402
from src.core.patrol.command_splitter import split_cli_capture  # noqa: E402
from src.core.patrol.raw_importer import (  # noqa: E402
    _read_text_best_effort,
    _resolve_capture_device,
)

EXPECTED_SUMMARY = {
    "Q1": {"file_count": 2, "device_count": 2, "command_count": 54},
    "Q2": {"file_count": 6, "device_count": 6, "command_count": 152},
    "Q3": {"file_count": 6, "device_count": 6, "command_count": 162},
}
DEFAULT_MANIFEST_PATH = (
    PROJECT_ROOT / "tests" / "fixtures" / "check_item_lixian_manifest.v1.json"
)


class _OfflineIdentityRepository:
    """Empty approved-identity view used only for generic fingerprint scanning."""

    @staticmethod
    def list_command_aliases() -> list[dict[str, Any]]:
        return []

    @staticmethod
    def list_identity_signatures() -> list[dict[str, Any]]:
        return []

    @staticmethod
    def find_device_profile(_vendor: str, _model: str) -> None:
        return None


def _normalize_commands(policy: ParserPolicy, commands: list[str]) -> list[str]:
    return sorted({policy.normalize_command(command) for command in commands})


def scan_lixian(root: str | Path) -> dict[str, Any]:
    source_root = Path(root).resolve()
    if not source_root.is_dir():
        raise FileNotFoundError(f"lixian root does not exist: {source_root}")
    repository = _OfflineIdentityRepository()
    policy = ParserPolicy(repository=repository)
    identity_resolver = DeviceIdentityResolver(repository=repository, policy=policy)
    plan_resolver = CheckItemImplementationResolver(load_catalog())
    periods: dict[str, Any] = {}
    corpus_hash = hashlib.sha256()
    for period in sorted(path for path in source_root.iterdir() if path.is_dir()):
        files: list[dict[str, Any]] = []
        unique_commands: set[str] = set()
        device_ids: set[str] = set()
        command_count = 0
        for path in sorted(period.rglob("*")):
            if not path.is_file() or path.suffix.lower() not in {".txt", ".log"}:
                continue
            raw_bytes = path.read_bytes()
            file_sha256 = hashlib.sha256(raw_bytes).hexdigest()
            relative_path = path.relative_to(source_root).as_posix()
            corpus_hash.update(relative_path.encode("utf-8"))
            corpus_hash.update(file_sha256.encode("ascii"))
            text = _read_text_best_effort(path)
            device_name, management_ip, _inspection = _resolve_capture_device(
                path=path,
                text=text,
            )
            blocks = split_cli_capture(text, device_name=device_name)
            identity = identity_resolver.resolve(blocks=blocks, path=path)
            device_id = (
                f"{device_name}-{management_ip}" if management_ip else device_name
            )
            device_ids.add(device_id)
            raw_commands = [block.command_canonical for block in blocks]
            normalized_commands = _normalize_commands(policy, raw_commands)
            unique_commands.update(normalized_commands)
            command_count += len(blocks)
            shadow_plan = plan_resolver.build_shadow_plan(
                device={
                    "device_id": device_id,
                    "vendor": identity.vendor,
                    "model": identity.model,
                    "family": identity.family,
                }
            )
            actual_set = set(normalized_commands)
            action_evidence = [
                {
                    "command": action["command"],
                    "captured": policy.normalize_command(action["command"]) in actual_set,
                    "check_item_ids": action["check_item_ids"],
                    "fallback_rank": action["fallback_rank"],
                }
                for action in shadow_plan.collection_actions
            ]
            files.append(
                {
                    "path": relative_path,
                    "sha256": file_sha256,
                    "device_id": device_id,
                    "device_name": device_name,
                    "management_ip": management_ip,
                    "vendor": identity.vendor,
                    "model": identity.model,
                    "family": identity.family,
                    "identity_status": identity.status,
                    "command_count": len(blocks),
                    "normalized_commands": normalized_commands,
                    "check_item_plan_digest": shadow_plan.digest,
                    "resolver_statuses": {
                        decision.check_item_id: decision.status
                        for decision in shadow_plan.decisions
                    },
                    "collection_action_evidence": action_evidence,
                }
            )
        periods[period.name] = {
            "file_count": len(files),
            "device_count": len(device_ids),
            "command_count": command_count,
            "unique_command_count": len(unique_commands),
            "unique_commands": sorted(unique_commands),
            "files": files,
        }
    try:
        source_locator = source_root.relative_to(PROJECT_ROOT).as_posix()
    except ValueError:
        source_locator = source_root.name
    return {
        "format": "netops.check_item_lixian_baseline/v1",
        "source_root": source_locator,
        "catalog_version": load_catalog().catalog_version,
        "corpus_digest": corpus_hash.hexdigest(),
        "periods": periods,
    }


def validate_baseline(
    report: dict[str, Any],
    *,
    manifest: dict[str, Any] | None = None,
) -> list[str]:
    errors: list[str] = []
    periods = report.get("periods") or {}
    for period, expected in EXPECTED_SUMMARY.items():
        actual = periods.get(period)
        if actual is None:
            errors.append(f"missing period: {period}")
            continue
        for key, expected_value in expected.items():
            if int(actual.get(key) or 0) != expected_value:
                errors.append(
                    f"{period}.{key}: expected {expected_value}, got {actual.get(key)}"
                )
        unknown_identity = [
            item["path"]
            for item in actual.get("files") or []
            if item.get("identity_status") != "known"
        ]
        if unknown_identity:
            errors.append(f"{period} has unknown device identity: {unknown_identity}")
    unexpected = sorted(set(periods) - set(EXPECTED_SUMMARY))
    if unexpected:
        errors.append(f"unexpected periods: {unexpected}")
    if manifest:
        expected_digest = str(manifest.get("corpus_digest") or "")
        if report.get("corpus_digest") != expected_digest:
            errors.append(
                "corpus_digest changed: "
                f"expected {expected_digest}, got {report.get('corpus_digest')}"
            )
        expected_files = {
            str(item["path"]): item for item in manifest.get("files") or []
        }
        actual_files = {
            str(item["path"]): item
            for period in periods.values()
            for item in period.get("files") or []
        }
        if set(actual_files) != set(expected_files):
            errors.append(
                "corpus file set changed: "
                f"missing={sorted(set(expected_files) - set(actual_files))}, "
                f"added={sorted(set(actual_files) - set(expected_files))}"
            )
        for path in sorted(set(actual_files).intersection(expected_files)):
            actual = actual_files[path]
            expected = expected_files[path]
            for key in ("sha256", "command_count", "vendor", "model", "family"):
                if actual.get(key) != expected.get(key):
                    errors.append(
                        f"{path}.{key}: expected {expected.get(key)!r}, "
                        f"got {actual.get(key)!r}"
                    )
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=str(PROJECT_ROOT / "lixian"))
    parser.add_argument("--output")
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST_PATH))
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    report = scan_lixian(args.root)
    manifest_path = Path(args.manifest)
    manifest = (
        json.loads(manifest_path.read_text(encoding="utf-8"))
        if args.verify and manifest_path.is_file()
        else None
    )
    errors = validate_baseline(report, manifest=manifest) if args.verify else []
    report["validation"] = {
        "success": not errors,
        "errors": errors,
        "expected_summary": EXPECTED_SUMMARY,
        "manifest": (
            manifest_path.relative_to(PROJECT_ROOT).as_posix()
            if manifest_path.is_relative_to(PROJECT_ROOT)
            else manifest_path.name
        ),
    }
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
