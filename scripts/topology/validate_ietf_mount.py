"""Run real libyang mount validation in a disposable, offline Docker container."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / ".runtime/ietf-study/model-package"
CASES = ROOT / "models/network-twin/mount/cases"

CONTAINER_RUNNER = r"""
import json, subprocess
from pathlib import Path
spec = json.loads(Path('/cases/manifest.json').read_text())
results = []
for case in spec['cases']:
    mode = case.get('type', 'config')
    # In pinned 5.8.6 an explicit '-t data' enables LYD_VALIDATE_OPERATIONAL;
    # omission keeps strict default flags. Merge postpones mounted subtree
    # validation until all sibling modules (including YANG Library) are parsed.
    mode_args = ['--merge'] if mode == 'strict' else ['-t', mode]
    if mode == 'data':
        mode_args += ['--merge']
    extension_args = ['-x', '/cases/' + case['extension']] if case.get('extension') else []
    cmd = ['yanglint', '-p', '/modules', '-Y', '/cases/parent-library.json',
           *extension_args, *mode_args, '-f', 'json',
           '/cases/' + case['data']]
    run = subprocess.run(cmd, text=True, capture_output=True, timeout=30)
    retained = None
    preserved = None
    if run.returncode == 0 and 'type' in case:
        tree = json.loads(run.stdout)
        original = json.loads(Path('/cases/' + case['data']).read_text())
        def contains(actual, expected):
            if isinstance(expected, dict):
                return isinstance(actual, dict) and all(
                    k in actual and contains(actual[k], v) for k, v in expected.items())
            if isinstance(expected, list):
                return isinstance(actual, list) and len(actual) == len(expected) and all(
                    contains(a, b) for a, b in zip(actual, expected))
            return type(actual) is type(expected) and actual == expected
        # Strict validation can add schema defaults. Parse-only must not invent facts.
        preserved = tree == original if case['type'] == 'get' else contains(tree, original)
    elif run.returncode == 0:
        tree = json.loads(run.stdout)
        nis = tree['ietf-network-instance:network-instances']['network-instance']
        retained = [{
            'ni': ni['name'],
            'route': ni['vrf-root']['ietf-routing:routing']['control-plane-protocols']
              ['control-plane-protocol'][0]['static-routes']
              ['ietf-ipv4-unicast-routing:ipv4']['route'][0],
        } for ni in nis]
    expected_routes = [
        {'ni': name, 'route': {'destination-prefix': '192.0.2.0/24',
                             'next-hop': {'outgoing-interface': port}}}
        for name, port in [('blue', 'eth0'), ('red', 'eth1')]
    ]
    if case['expected_valid']:
        matched = run.returncode == 0 and (
            preserved if 'type' in case else retained == expected_routes)
        if 'type' in case:
            matched = matched and (case['diagnostic'] in run.stderr if case['diagnostic']
                                   else not run.stderr.strip())
    else:
        matched = run.returncode > 0 and case['diagnostic'] in run.stderr
        if case['diagnostic'] == 'outgoing-interface':
            matched = matched and 'Invalid leafref value' in run.stderr
    results.append({'name': case['name'], 'command': cmd, 'returncode': run.returncode,
                    'expected_valid': case['expected_valid'], 'matched': matched,
                    'diagnostic': run.stderr, 'mounted_routes_retained': retained,
                    'input_preserved': preserved,
                    'validation_mode': mode,
                    'semantic_validation_performed': mode != 'get',
                    'strict_complete_instance_proven': mode == 'strict' and run.returncode == 0
                        and preserved is True and not run.stderr.strip()})
version = subprocess.run(['yanglint', '--version'],
                         capture_output=True, text=True, check=True).stdout.strip()
packages = subprocess.run(['dpkg-query', '-W', '-f=${Package} ${Version}\n'],
                          capture_output=True, text=True, check=True).stdout.splitlines()
print(json.dumps({'version': version, 'checks': results, 'packages': packages,
                  'expectations_matched': all(r['matched'] for r in results)}))
"""


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def validate(image: str, cases: Path = CASES) -> dict:
    compile(CONTAINER_RUNNER, "mount-container-runner", "exec")
    cases = cases.resolve()
    manifest = json.loads((cases / "manifest.json").read_text(encoding="utf-8"))
    if digest((PACKAGE / "manifest.json").read_bytes()) != manifest["model_package_sha256"]:
        raise ValueError("Mount cases refer to a different compiled model package")
    package = json.loads((PACKAGE / "manifest.json").read_text(encoding="utf-8"))
    if {p.name for p in (PACKAGE / "modules").glob("*.yang")} != set(package["modules"]):
        raise ValueError("Module file set differs from compiled model package")
    for name, expected in package["modules"].items():
        if digest((PACKAGE / "modules" / name).read_bytes()) != expected:
            raise ValueError(f"Model bytes changed: {name}")
    for name, expected in manifest["files"].items():
        if digest((cases / name).read_bytes()) != expected:
            raise ValueError(f"Mount case bytes changed: {name}")
    image_id = subprocess.run(
        ["docker", "image", "inspect", image, "--format", "{{.Id}}"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    run = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--user",
            "65534:65534",
            "--mount",
            f"type=bind,source={PACKAGE / 'modules'},target=/modules,readonly",
            "--mount",
            f"type=bind,source={cases},target=/cases,readonly",
            "--entrypoint",
            "python",
            image_id,
            "-c",
            CONTAINER_RUNNER,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
        timeout=300,
    )
    result = json.loads(run.stdout)
    result.update(
        {
            "image_id": image_id,
            "image_tag": image,
            "cases_manifest_sha256": digest((cases / "manifest.json").read_bytes()),
            "scope": manifest["scope"],
            "complete_digital_twin": False,
        }
    )
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", default="netops-ietf-mount-verify:5.8.6")
    parser.add_argument("--cases", type=Path, default=CASES)
    parser.add_argument(
        "--output", type=Path, default=ROOT / "models/network-twin/reports/mount-validation.json"
    )
    args = parser.parse_args()
    result = validate(args.image, args.cases)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes((json.dumps(result, indent=2) + "\n").encode())
    print(
        json.dumps(
            {
                "version": result["version"],
                "checks": len(result["checks"]),
                "matched": result["expectations_matched"],
                "failures": [r for r in result["checks"] if not r["matched"]],
            }
        )
    )
    raise SystemExit(0 if result["expectations_matched"] else 1)


if __name__ == "__main__":
    main()
