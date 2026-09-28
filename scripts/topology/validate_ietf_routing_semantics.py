"""Exercise native RFC 8349 choices and the limits of schema-only route validation."""

from __future__ import annotations

import argparse
import copy
import json
from importlib.metadata import version
from pathlib import Path

from build_ietf_model_package import ROOT, digest, load_model
from build_ietf_mount_cases import library
from validate_ietf_mount import validate as validate_libyang
from yangson.enumerations import ContentType


def validate(package: Path) -> dict:
    manifest = json.loads((package / "manifest.json").read_text(encoding="utf-8"))
    actual = {p.name: digest(p.read_bytes()) for p in (package / "modules").glob("*.yang")}
    if actual != manifest["modules"]:
        raise ValueError("Model package bytes differ from manifest")
    if (
        digest((package / "device-library.json").read_bytes())
        != manifest["profiles"]["device"]["library_sha256"]
    ):
        raise ValueError("Device profile library differs from the compiled manifest")
    model = load_model(package, "device")
    results = []

    def check(
        name,
        data,
        expected=True,
        error=None,
        boundary=None,
        content=ContentType.all,
        known_tool_error=None,
    ):
        if not expected and not error:
            raise ValueError("Negative cases require a specific diagnostic")
        diagnostic = None
        try:
            model.from_raw(data).validate(ctype=content)
            accepted = True
        except Exception as exc:
            diagnostic = f"{type(exc).__name__}: {exc}"
            accepted = None if isinstance(exc, TypeError) else False
        results.append(
            {
                "name": name,
                "input": copy.deepcopy(data),
                "content_type": content.name,
                "expected_valid": expected,
                "accepted": accepted,
                "expected_diagnostic": error,
                "diagnostic": diagnostic,
                "boundary": boundary,
                "known_tool_error": known_tool_error,
                "verified_by": "yangson" if accepted is not None else None,
                "matched": accepted == expected and (error is None or error in (diagnostic or "")),
            }
        )

    interfaces = {
        "ietf-interfaces:interfaces": {
            "interface": [
                {
                    "name": port,
                    "type": "iana-if-type:ethernetCsmacd",
                    "oper-status": "up",
                    "statistics": {"discontinuity-time": "2026-01-01T00:00:00Z"},
                }
                for port in ("eth0", "eth1")
            ]
        }
    }

    def rib_route(tree):
        return tree["ietf-routing:routing"]["ribs"]["rib"][0]["routes"]["route"][0]

    for family, prefix, addresses in (
        (4, "192.0.2.0/24", ("198.51.100.1", "198.51.100.2")),
        (6, "2001:db8:1::/64", ("2001:db8:2::1", "2001:db8:2::2")),
    ):
        module = f"ietf-ipv{family}-unicast-routing"
        route = {
            f"{module}:destination-prefix": prefix,
            "source-protocol": "ietf-routing:static",
            "route-preference": 1,
            "active": [None],
            "next-hop": {"outgoing-interface": "eth0", f"{module}:next-hop-address": addresses[0]},
        }
        base = {
            **copy.deepcopy(interfaces),
            "ietf-routing:routing": {
                "ribs": {
                    "rib": [
                        {
                            "name": f"ipv{family}",
                            "address-family": f"{module}:ipv{family}-unicast",
                            "routes": {"route": [route]},
                        }
                    ]
                }
            },
        }
        check(f"ipv{family}-single-next-hop", base)
        multi = copy.deepcopy(base)
        rib_route(multi)["next-hop"] = {
            "next-hop-list": {
                "next-hop": [
                    {"outgoing-interface": port, f"{module}:address": address}
                    for port, address in zip(("eth0", "eth1"), addresses, strict=True)
                ]
            }
        }
        check(
            f"ipv{family}-multiple-state-next-hops",
            multi,
            boundary="Multiple next hops neither encode weights nor prove ECMP forwarding",
        )
        wrong = copy.deepcopy(multi)
        hop = rib_route(wrong)["next-hop"]["next-hop-list"]["next-hop"][0]
        hop[f"{module}:next-hop-address"] = hop.pop(f"{module}:address")
        check(f"ipv{family}-state-list-uses-address", wrong, False, "RawMemberError")
        for special in ("blackhole", "unreachable", "prohibit", "receive"):
            tree = copy.deepcopy(base)
            rib_route(tree)["next-hop"] = {"special-next-hop": special}
            check(f"ipv{family}-special-{special}", tree)
        wrong = copy.deepcopy(base)
        wrong["ietf-routing:routing"]["ribs"]["rib"][0]["address-family"] = (
            f"ietf-routing:ipv{family}"
        )
        check(
            f"ipv{family}-base-family-not-unicast",
            wrong,
            False,
            f"member-not-allowed: {module}:destination-prefix",
        )
        wrong = copy.deepcopy(base)
        rib_route(wrong)["next-hop"][f"{module}:next-hop-address"] = "not-an-address"
        check(f"ipv{family}-address-type-rejected", wrong, False, "invalid-type")
        wrong = copy.deepcopy(base)
        rib_route(wrong)["next-hop"]["special-next-hop"] = "receive"
        check(
            f"ipv{family}-next-hop-choice-exclusive",
            wrong,
            False,
            "member-not-allowed: special-next-hop",
        )
        wrong = copy.deepcopy(base)
        rib_route(wrong)["next-hop"] = {}
        check(
            f"ipv{family}-next-hop-choice-required",
            wrong,
            False,
            "missing-data",
            known_tool_error="TypeError: object of type 'NoneType' has no len()",
        )
        wrong = copy.deepcopy(base)
        rib_route(wrong).pop(f"{module}:destination-prefix")
        check(
            f"ipv{family}-missing-prefix-schema-gap",
            wrong,
            boundary="Native state destination-prefix is not mandatory; this cannot locate a route",
        )
        if family == 6:
            # Other shared constraints are exercised once, on IPv4, below.
            continue
        mutations = [
            ("source-protocol-required", lambda r: r.pop("source-protocol"), "missing-data"),
            ("active-empty-not-boolean", lambda r: r.update(active=True), "RawTypeError"),
            (
                "preference-uint32",
                lambda r: r.update({"route-preference": 4294967296}),
                "invalid-type",
            ),
            ("custom-metric-rejected", lambda r: r.update(metric=10), "RawMemberError"),
            (
                "unknown-special-rejected",
                lambda r: r.update({"next-hop": {"special-next-hop": "discard"}}),
                "invalid-type",
            ),
            (
                "unresolved-outgoing-interface",
                lambda r: r["next-hop"].update({"outgoing-interface": "absent"}),
                "instance-required",
            ),
        ]
        for name, mutate, error in mutations:
            wrong = copy.deepcopy(base)
            mutate(rib_route(wrong))
            check(name, wrong, False, error)
        for name, field, value in (
            ("state-list-no-index", "index", "hop-1"),
            ("state-list-no-weight", "weight", 1),
        ):
            wrong = copy.deepcopy(multi)
            rib_route(wrong)["next-hop"]["next-hop-list"]["next-hop"][0][field] = value
            check(name, wrong, False, "RawMemberError")
        empty = copy.deepcopy(multi)
        rib_route(empty)["next-hop"]["next-hop-list"]["next-hop"] = [{}]
        check(
            "empty-next-hop-entry-schema-gap",
            empty,
            boundary="State next-hop entry requires no address/interface; forwarding is unproven",
        )
        duplicate = copy.deepcopy(base)
        duplicate["ietf-routing:routing"]["ribs"]["rib"][0]["routes"]["route"].append(
            copy.deepcopy(route)
        )
        check(
            "same-prefix-two-active-routes-schema-gap",
            duplicate,
            boundary="Keyless RIB allows alternatives; schema does not check active selection",
        )
        check(
            "rib-state-not-config",
            base,
            False,
            "config member-not-allowed",
            content=ContentType.config,
        )

    # Configuration next-hop lists are keyed by index and inherit the augment's
    # namespace. They are not the keyless state list and must not share a codec.
    static_route = {
        "destination-prefix": "192.0.2.0/24",
        "next-hop": {
            "next-hop-list": {
                "next-hop": [
                    {"index": index, "outgoing-interface": port, "next-hop-address": address}
                    for index, port, address in (
                        ("first", "eth0", "198.51.100.1"),
                        ("second", "eth1", "198.51.100.2"),
                    )
                ]
            }
        },
    }
    config = {
        "ietf-interfaces:interfaces": {
            "interface": [
                {"name": port, "type": "iana-if-type:ethernetCsmacd"} for port in ("eth0", "eth1")
            ]
        },
        "ietf-routing:routing": {
            "control-plane-protocols": {
                "control-plane-protocol": [
                    {
                        "type": "ietf-routing:static",
                        "name": "static",
                        "static-routes": {
                            "ietf-ipv4-unicast-routing:ipv4": {"route": [static_route]}
                        },
                    }
                ]
            }
        },
    }

    def hops(tree):
        return tree["ietf-routing:routing"]["control-plane-protocols"]["control-plane-protocol"][0][
            "static-routes"
        ]["ietf-ipv4-unicast-routing:ipv4"]["route"][0]["next-hop"]["next-hop-list"]["next-hop"]

    check(
        "config-multiple-next-hops-string-indices",
        config,
        content=ContentType.config,
        boundary="Index is an identifier, not next-hop preference or weight",
    )
    wrong = copy.deepcopy(config)
    hops(wrong)[1]["index"] = "first"
    check("config-index-unique", wrong, False, "non-unique-key", content=ContentType.config)
    wrong = copy.deepcopy(config)
    hops(wrong)[0].pop("index")
    check(
        "config-index-required", wrong, False, "list-key-missing: index", content=ContentType.config
    )
    wrong = copy.deepcopy(config)
    hops(wrong)[0]["address"] = hops(wrong)[0].pop("next-hop-address")
    check(
        "config-list-uses-next-hop-address",
        wrong,
        False,
        "RawMemberError",
        content=ContentType.config,
    )

    # The pinned Yangson fails internally on an empty mandatory choice. Preserve
    # that failure and require a real semantic rejection from an independent tool.
    cases_dir = ROOT / ".runtime/ietf-study/routing-choice-cases"
    cases_dir.mkdir(parents=True, exist_ok=True)
    profile = json.loads((ROOT / "models/network-twin/profiles.json").read_text())["device"]
    modules = {p.name: p.read_bytes() for p in (package / "modules").glob("*.yang")}
    runtime_library = library(modules, profile["roots"], "routing-semantics")
    files = {"parent-library.json": (json.dumps(runtime_library, indent=2) + "\n").encode()}
    specs = []
    for case in results:
        if not case["known_tool_error"]:
            continue
        file = case["name"] + ".json"
        runtime_input = {**copy.deepcopy(case["input"]), **copy.deepcopy(runtime_library)}
        rib_route(runtime_input)["source-protocol"] = "static"
        files[file] = (json.dumps(runtime_input, indent=2) + "\n").encode()
        specs.append(
            {
                "name": case["name"],
                "data": file,
                "type": "strict",
                "expected_valid": False,
                "diagnostic": 'Mandatory choice "next-hop-options"',
            }
        )
        positive = copy.deepcopy(runtime_input)
        rib_route(positive)["next-hop"] = {"outgoing-interface": "eth0"}
        positive_name = case["name"] + "-positive-control"
        files[positive_name + ".json"] = (json.dumps(positive, indent=2) + "\n").encode()
        specs.append(
            {
                "name": positive_name,
                "data": positive_name + ".json",
                "type": "strict",
                "expected_valid": True,
                "diagnostic": None,
            }
        )
    for name, raw in files.items():
        (cases_dir / name).write_bytes(raw)
    case_manifest = {
        "scope": "RFC 8349 mandatory next-hop choice independent verification",
        "model_package_sha256": digest((package / "manifest.json").read_bytes()),
        "files": {name: digest(raw) for name, raw in files.items()},
        "cases": specs,
    }
    (cases_dir / "manifest.json").write_bytes((json.dumps(case_manifest, indent=2) + "\n").encode())
    secondary = validate_libyang("netops-ietf-mount-verify:5.8.6", cases_dir)
    secondary_checks = {c["name"]: c for c in secondary["checks"]}
    for case in results:
        if case["known_tool_error"]:
            rejection = secondary_checks[case["name"]]
            case["matched"] = (
                case["diagnostic"] == case["known_tool_error"]
                and rejection["matched"]
                and secondary_checks[case["name"] + "-positive-control"]["matched"]
            )
            case["verified_by"] = "libyang" if rejection["matched"] else None
    return {
        "scope": "Synthetic RFC 8349 IPv4/IPv6 route choices; no device or production I/O",
        "tool": {"name": "yangson", "version": version("yangson")},
        "model_package_sha256": digest((package / "manifest.json").read_bytes()),
        "device_library_sha256": digest((package / "device-library.json").read_bytes()),
        "script_sha256": digest(Path(__file__).read_bytes()),
        "checks": results,
        "independent_choice_verification": secondary,
        "expectations_matched": all(r["matched"] for r in results),
        "forwarding_path_proven": False,
        "complete_digital_twin": False,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", type=Path, default=ROOT / ".runtime/ietf-study/model-package")
    parser.add_argument(
        "--output", type=Path, default=ROOT / "models/network-twin/reports/routing-semantics.json"
    )
    args = parser.parse_args()
    result = validate(args.package)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes((json.dumps(result, indent=2) + "\n").encode())
    print(
        json.dumps(
            {
                "checks": len(result["checks"]),
                "matched": result["expectations_matched"],
                "failures": [
                    {k: v for k, v in c.items() if k != "input"}
                    for c in result["checks"]
                    if not c["matched"]
                ],
            }
        )
    )
    raise SystemExit(0 if result["expectations_matched"] else 1)
