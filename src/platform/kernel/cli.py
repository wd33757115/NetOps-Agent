"""Build-time registry generation. No database or application imports required."""

import argparse
import json
import os

from .registry import ROOT, discover


def generate_frontend():
    target = ROOT / "web/react_frontend/src/apps/generated.ts"
    lines = ["// Generated from src/apps/*/app.yaml. Do not edit."]
    entries = []
    for index, m in enumerate(discover().values()):
        from .dependencies import check_dependencies

        check_dependencies(m, frontend=True)
        entry = (m.path.parent / m.data["frontend"]["entry"]).with_suffix("")
        spec = os.path.relpath(entry, target.parent).replace("\\", "/")
        lines.append(f"import frontend{index} from {json.dumps(spec)}")
        metadata = json.dumps(m.public(), ensure_ascii=False)
        flag = m.data["frontend"].get("enabled_env")
        enabled = f'import.meta.env.{flag} !== "false"' if flag else "true"
        entries.append(f"{{ ...{metadata}, enabled: {enabled}, frontend: frontend{index} }}")
    lines.append("export const packagedApps = [" + ",\n".join(entries) + "]")
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    manifests = discover()
    npm_dependencies = {}
    for m in manifests.values():
        package = m.path.parent / "package.json"
        if package.exists():
            npm_dependencies.update(
                json.loads(package.read_text(encoding="utf-8")).get("dependencies", {})
            )
    target.with_name("generated.meta.json").write_text(
        json.dumps(
            {
                "apps": {m.id: m.digest for m in manifests.values()},
                "flags": {m.id: m.data["frontend"].get("enabled_env") for m in manifests.values()},
                "frontendDependencies": npm_dependencies,
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"Generated {target.relative_to(ROOT)}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["validate", "frontend", "status", "check-dependencies"])
    args = parser.parse_args()
    if args.command == "frontend":
        generate_frontend()
    elif args.command == "check-dependencies":
        from .dependencies import check_dependencies

        failed = False
        for manifest in discover().values():
            try:
                check_dependencies(manifest)
                print(f"[OK] {manifest.id}: locked dependencies available")
            except (ValueError, ImportError, LookupError, OSError) as exc:
                failed = True
                print(f"[ERROR] {manifest.id}: {exc}")
        if failed:
            raise SystemExit(1)
    else:
        print(json.dumps([m.public() for m in discover().values()], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
