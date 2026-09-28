"""Validate native instances against the disclosed SIMAP compatibility package."""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path

from build_ietf_model_package import ROOT, digest, load_model
from yangson.enumerations import ContentType


def validate(package: Path) -> dict:
    topology = load_model(package, "topology")
    device = load_model(package, "device")
    results = []

    def check(
        name, data, expected=True, model=topology, content=ContentType.all, gap=False, error=None
    ):
        diagnostic = None
        try:
            model.from_raw(data).validate(ctype=content)
            accepted = True
        except Exception as exc:
            diagnostic = f"{type(exc).__name__}: {exc}"
            accepted = False
        if not expected and not error:
            raise ValueError(f"Negative check must identify its expected diagnostic: {name}")
        results.append(
            {
                "name": name,
                "accepted": accepted,
                "expected": expected,
                "matched": accepted == expected and (error is None or error in (diagnostic or "")),
                "expected_diagnostic": error,
                "schema_only_gap": gap,
                "diagnostic": diagnostic,
            }
        )

    st = "ietf-simap-topology:"
    net = {
        "network-id": "urn:example:live",
        "node": [
            {
                "node-id": f"urn:example:r{i}",
                "ietf-network-topology:termination-point": [{"tp-id": "urn:example:eth0"}],
            }
            for i in (1, 2, 3)
        ],
        st + "network-category": st + "network-category-live",
    }
    link = {
        "link-id": "urn:example:link",
        st + "link-direction": st + "link-direction-bi",
        st + "link-type": st + "link-type-p2p",
        st + "tp": [
            {
                "network-ref": "urn:example:live",
                "node-ref": f"urn:example:r{i}",
                "tp-ref": "urn:example:eth0",
                "tp-direction": st + "tp-direction-symmetric",
            }
            for i in (1, 2)
        ],
    }
    net["ietf-network-topology:link"] = [link]
    base = {"ietf-network:networks": {"network": [net]}}
    check("simap-bidirectional-p2p", base)
    multi = copy.deepcopy(base)
    ml = multi["ietf-network:networks"]["network"][0]["ietf-network-topology:link"][0]
    ml[st + "link-type"] = st + "link-type-mp2mp"
    ml[st + "tp"].append(
        {
            "network-ref": "urn:example:live",
            "node-ref": "urn:example:r3",
            "tp-ref": "urn:example:eth0",
            "tp-direction": st + "tp-direction-symmetric",
        }
    )
    check("simap-multipoint-three-tps", multi)
    duplicate = copy.deepcopy(base)
    dl = duplicate["ietf-network:networks"]["network"][0]["ietf-network-topology:link"][0]
    dl[st + "tp"].append(copy.deepcopy(dl[st + "tp"][0]))
    check("simap-composite-tp-key-duplicate", duplicate, False, error="non-unique-key")
    navigation = copy.deepcopy(base)
    navigation["ietf-network:networks"]["network"][0]["node"][0][st + "linked-node"] = [
        {"network-ref": "urn:example:live", "node-ref": "urn:example:r2"}
    ]
    check("simap-navigation-operational", navigation)
    check(
        "simap-navigation-not-config",
        navigation,
        False,
        content=ContentType.config,
        error="config member-not-allowed: ietf-simap-topology:linked-node",
    )
    history = copy.deepcopy(base)
    history[st + "networks-history"] = {
        "network-history": [
            {
                "live-network-ref": "urn:example:live",
                "timestamp": "2026-09-28T00:00:00Z",
                "file-id": "urn:example:snapshot",
            }
        ]
    }
    check("simap-history-existing-live", history)
    history[st + "networks-history"]["network-history"][0]["live-network-ref"] = (
        "urn:example:missing"
    )
    check("simap-history-target-required", history, False, error="instance-required")
    potential = copy.deepcopy(base)
    potential["ietf-network:networks"]["network"].append(
        {
            "network-id": "urn:example:potential",
            st + "network-category": st + "network-category-potential",
            st + "live-network-ref": "urn:example:live",
            st + "timestamp": "2026-09-28T00:00:00Z",
        }
    )
    check("simap-potential-linked-live", potential)
    none = {
        "ietf-network:networks": {
            "network": [
                {
                    "network-id": "urn:example:none",
                    st + "network-category": st + "network-category-none",
                }
            ]
        }
    }
    check("none-identity-remains-outside-category-base", none, False, error="not derived from")
    underconstrained = {
        "ietf-network:networks": {
            "network": [
                {
                    "network-id": "urn:example:potential",
                    st + "network-category": st + "network-category-potential",
                }
            ]
        }
    }
    check("patch-does-not-invent-lifecycle-mandatory", underconstrained, gap=True)
    single = copy.deepcopy(base)
    single["ietf-network:networks"]["network"][0]["ietf-network-topology:link"][0][st + "tp"].pop()
    check("patch-does-not-invent-p2p-cardinality", single, gap=True)
    ni = {
        "ietf-network-instance:network-instances": {
            "network-instance": [
                {
                    "name": "example-vrf",
                    "vrf-root": {},
                }
            ]
        },
        "ietf-interfaces:interfaces": {
            "interface": [
                {
                    "name": "eth0",
                    "type": "iana-if-type:ethernetCsmacd",
                    "ietf-network-instance:bind-ni-name": "example-vrf",
                }
            ]
        },
    }
    check("native-interface-ni-reference", ni, model=device, content=ContentType.config)
    absent = copy.deepcopy(ni)
    absent["ietf-interfaces:interfaces"]["interface"][0]["ietf-network-instance:bind-ni-name"] = (
        "absent"
    )
    check(
        "ni-target-must-exist",
        absent,
        False,
        model=device,
        content=ContentType.config,
        error="instance-required",
    )
    mounted = copy.deepcopy(ni)
    mounted["ietf-network-instance:network-instances"]["network-instance"][0]["vrf-root"] = {
        "ietf-routing:routing": {}
    }
    check(
        "yangson-does-not-load-mounted-schema-automatically",
        mounted,
        False,
        model=device,
        content=ContentType.config,
        gap=True,
        error=(
            "RawMemberError: /ietf-network-instance:network-instances/"
            "network-instance=example-vrf/vrf-root/ietf-routing:routing"
        ),
    )

    mapping = {
        "ietf-network:networks": {
            "network": [
                {
                    "network-id": "urn:example:physical",
                    "network-types": {"ietf-network-inventory-topology:inventory-topology": {}},
                    "node": [
                        {
                            "node-id": "urn:example:r1",
                            "ietf-network-inventory-topology:inventory-mapping-attributes": {
                                "ne-ref": "r1"
                            },
                        }
                    ],
                }
            ]
        },
        "ietf-network-inventory:network-inventory": {
            "network-elements": {
                "network-element": [
                    {
                        "ne-id": "r1",
                        "ne-type": "ietf-network-inventory:ne-physical",
                    }
                ]
            }
        },
    }
    check("physical-node-to-inventory", mapping)
    missing = copy.deepcopy(mapping)
    missing.pop("ietf-network-inventory:network-inventory")
    check("inventory-reference-permits-external-target", missing)
    virtual = copy.deepcopy(mapping)
    virtual["ietf-network-inventory:network-inventory"]["network-elements"]["network-element"][0][
        "ne-type"
    ] = "ietf-network-inventory-sw-ext:ne-virtual"
    check("schema-does-not-enforce-physical-only-mapping-description", virtual, gap=True)
    custom = copy.deepcopy(base)
    custom["ietf-network:networks"]["network"][0]["project-confidence"] = 0.8
    check("custom-model-field-rejected", custom, False, error="project-confidence")

    folder = ROOT / "models/network-twin/instances"
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    for spec in manifest["entries"]:
        path = folder / spec["file"]
        if digest(path.read_bytes()) != spec["sha256"]:
            raise ValueError(f"Instance hash mismatch: {path.name}")
        model = {"device": device, "topology": topology}[spec["profile"]]
        check(
            path.name,
            json.loads(path.read_text(encoding="utf-8")),
            spec["expected_valid"],
            model=model,
            error=spec["expected_diagnostic"],
        )
    report = {
        "profile_scope": "local compatibility package; no production deployment",
        "checks": results,
        "expectations_matched": all(r["matched"] for r in results),
        "complete_digital_twin": False,
        "instances_manifest_sha256": digest((folder / "manifest.json").read_bytes()),
        "remaining": [
            "schema-mount runtime",
            "virtual topology-to-inventory/device association",
            "SIMAP missing feature/default/semantic constraints",
            "full TE/protocol closure",
        ],
    }
    (package / "validation.json").write_bytes((json.dumps(report, indent=2) + "\n").encode())
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", type=Path, default=ROOT / ".runtime/ietf-study/model-package")
    args = parser.parse_args()
    report = validate(args.package)
    print(
        json.dumps(
            {
                "checks": len(report["checks"]),
                "matched": report["expectations_matched"],
                "failures": [c for c in report["checks"] if not c["matched"]],
            }
        )
    )
    raise SystemExit(0 if report["expectations_matched"] else 1)


if __name__ == "__main__":
    main()
