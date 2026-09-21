"""Stdlib-only build step; merge trusted packages' exact Python dependency pins."""

import argparse
import re
from pathlib import Path


def collect(root):
    pins = {}
    for manifest in sorted(root.glob("*/app.yaml")):
        lock = manifest.parent / "requirements.lock"
        if not lock.is_file():
            continue
        for line in lock.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            match = re.fullmatch(
                r"([A-Za-z0-9][A-Za-z0-9_.-]*)==([A-Za-z0-9][A-Za-z0-9_.+!-]*)", line
            )
            if not match:
                raise ValueError(f"Use exact name==version pins: {lock.name}")
            name, version = match.groups()
            key = re.sub(r"[-_.]+", "-", name).lower()
            if key in pins and pins[key] != version:
                raise ValueError(f"Conflicting package dependency: {name}")
            pins[key] = version
    return "\n".join(f"{name}=={value}" for name, value in sorted(pins.items())) + "\n"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[3]
    Path(args.output).write_text(collect(root / "src/apps"), encoding="utf-8")


if __name__ == "__main__":
    main()
