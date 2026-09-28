"""Build synthetic RFC 8529/8528 examples using only pinned native modules."""

from __future__ import annotations

import argparse
import copy
import json
from xml.sax.saxutils import escape, quoteattr

from build_ietf_model_package import ROOT, digest, make_library

PACKAGE = ROOT / ".runtime/ietf-study/model-package"
OUTPUT = ROOT / "models/network-twin/mount/cases"


def extension_xml(data: dict, namespaces: dict) -> str:
    """yanglint 3.12 -x consumes XML; preserve the native JSON namespace mapping."""

    def element(key, value, module=None, top=False):
        if ":" in key:
            module, key = key.split(":", 1)
        tag = f"{module}:{key}"
        if isinstance(value, list):
            return "".join(element(key, item, module, top) for item in value)
        attributes = (
            "".join(f" xmlns:{p}={quoteattr(uri)}" for p, uri in namespaces.items()) if top else ""
        )
        if isinstance(value, dict):
            body = "".join(element(k, v, module) for k, v in value.items())
        else:
            body = escape(str(value).lower() if isinstance(value, bool) else str(value))
        return f"<{tag}{attributes}>{body}</{tag}>"

    return "\n".join(element(k, v, top=True) for k, v in data.items()) + "\n"


def library(modules: dict, roots: list[str], label: str) -> dict:
    old = make_library(modules, {"roots": roots, "features": {}}, label)
    entries = old["ietf-yang-library:modules-state"]["module"]
    module_set = {"name": label, "module": [], "import-only-module": []}
    for entry in entries:
        item = dict(entry)
        kind = "module" if item.pop("conformance-type") == "implement" else "import-only-module"
        module_set[kind].append(item)
    return {
        **old,
        "ietf-yang-library:yang-library": {
            "content-id": digest(json.dumps(module_set, sort_keys=True).encode()),
            "module-set": [module_set],
            "schema": [{"name": label, "module-set": [label]}],
            "datastore": [
                {"name": f"ietf-datastores:{d}", "schema": label}
                for d in ("running", "operational")
            ],
        },
    }


def build() -> dict[str, bytes]:
    modules = {p.name: p.read_bytes() for p in (PACKAGE / "modules").glob("*.yang")}
    manifest = json.loads((PACKAGE / "manifest.json").read_text(encoding="utf-8"))
    if {name: digest(data) for name, data in modules.items()} != manifest["modules"]:
        raise ValueError("Model package bytes differ from the compiled manifest")
    common = ["ietf-yang-library", "ietf-datastores", "iana-if-type", "ietf-interfaces"]
    parent = library(
        modules, common + ["ietf-ip", "ietf-network-instance", "ietf-yang-schema-mount"], "parent"
    )
    child = library(modules, common + ["ietf-routing", "ietf-ipv4-unicast-routing"], "vrf")
    selected_interfaces = "/if:interfaces/if:interface[ni:bind-ni-name = current()/../ni:name]"
    extension = {
        "ietf-yang-schema-mount:schema-mounts": {
            "namespace": [
                {"prefix": "if", "uri": "urn:ietf:params:xml:ns:yang:ietf-interfaces"},
                {"prefix": "ni", "uri": "urn:ietf:params:xml:ns:yang:ietf-network-instance"},
            ],
            "mount-point": [
                {
                    "module": "ietf-network-instance",
                    "label": "vrf-root",
                    "shared-schema": {
                        "parent-reference": [
                            selected_interfaces + "/if:name",
                            selected_interfaces + "/if:type",
                        ]
                    },
                }
            ],
        },
        "ietf-network-instance:network-instances": {
            "network-instance": [
                {"name": name, "vrf-root": copy.deepcopy(child)} for name in ("blue", "red")
            ]
        },
    }
    data = {
        "ietf-interfaces:interfaces": {
            "interface": [
                {
                    "name": port,
                    "type": "iana-if-type:ethernetCsmacd",
                    "ietf-network-instance:bind-ni-name": name,
                }
                for name, port in (("blue", "eth0"), ("red", "eth1"))
            ]
        },
        "ietf-network-instance:network-instances": {
            "network-instance": [
                {
                    "name": name,
                    "vrf-root": {
                        "ietf-routing:routing": {
                            "control-plane-protocols": {
                                "control-plane-protocol": [
                                    {
                                        "type": "ietf-routing:static",
                                        "name": "static",
                                        "static-routes": {
                                            "ietf-ipv4-unicast-routing:ipv4": {
                                                "route": [
                                                    {
                                                        "destination-prefix": "192.0.2.0/24",
                                                        "next-hop": {"outgoing-interface": port},
                                                    }
                                                ]
                                            }
                                        },
                                    }
                                ]
                            }
                        }
                    },
                }
                for name, port in (("blue", "eth0"), ("red", "eth1"))
            ]
        },
    }
    documents = {"parent-library.json": parent, "extension.json": extension, "valid.json": data}
    namespaces = {
        entry["name"]: entry["namespace"]
        for lib in (parent, child)
        for entry in lib["ietf-yang-library:modules-state"]["module"]
    }
    output = {"extension.xml": extension_xml(extension, namespaces).encode()}
    whole_interface = copy.deepcopy(extension)
    whole_interface["ietf-yang-schema-mount:schema-mounts"]["mount-point"][0]["shared-schema"][
        "parent-reference"
    ] = ["/if:interfaces/if:interface[ni:bind-ni-name = current()/../ni:name]"]
    output["whole-interface.xml"] = extension_xml(whole_interface, namespaces).encode()

    def route(tree):
        return tree["ietf-network-instance:network-instances"]["network-instance"][0]["vrf-root"][
            "ietf-routing:routing"
        ]["control-plane-protocols"]["control-plane-protocol"][0]["static-routes"][
            "ietf-ipv4-unicast-routing:ipv4"
        ]["route"][0]

    cross = copy.deepcopy(data)
    route(cross)["next-hop"]["outgoing-interface"] = "eth1"
    documents["cross-vrf.json"] = cross
    missing = copy.deepcopy(data)
    route(missing)["next-hop"]["outgoing-interface"] = "absent"
    documents["missing-interface.json"] = missing
    custom = copy.deepcopy(data)
    route(custom)["project-confidence"] = 0.8
    documents["custom-field.json"] = custom
    for mode in ("no-parent", "inline"):
        ext = copy.deepcopy(extension)
        point = ext["ietf-yang-schema-mount:schema-mounts"]["mount-point"][0]
        point.pop("shared-schema")
        point["inline" if mode == "inline" else "shared-schema"] = {}
        if mode == "inline":
            # yanglint's callback supplies the inline schema at its response root.
            # This is callback input, not a replacement for the parent datastore library.
            ext.update(copy.deepcopy(child))
        output[f"{mode}.xml"] = extension_xml(ext, namespaces).encode()
    inline_data = copy.deepcopy(data)
    for ni, port in zip(
        inline_data["ietf-network-instance:network-instances"]["network-instance"],
        ("eth0", "eth1"),
        strict=True,
    ):
        ni["vrf-root"]["ietf-interfaces:interfaces"] = {
            "interface": [{"name": port, "type": "iana-if-type:ethernetCsmacd"}]
        }
    documents["inline-local.json"] = inline_data
    case_specs = [
        ("shared-two-vrfs", "valid.json", "extension.xml", True, None),
        (
            "cross-vrf-interface-rejected",
            "cross-vrf.json",
            "extension.xml",
            False,
            "outgoing-interface",
        ),
        (
            "missing-interface-rejected",
            "missing-interface.json",
            "extension.xml",
            False,
            "outgoing-interface",
        ),
        (
            "no-parent-reference-rejected",
            "valid.json",
            "no-parent.xml",
            False,
            "outgoing-interface",
        ),
        ("inline-no-parent-visibility", "valid.json", "inline.xml", False, "outgoing-interface"),
        ("inline-local-interface", "inline-local.json", "inline.xml", True, None),
        (
            "custom-field-rejected",
            "custom-field.json",
            "extension.xml",
            False,
            "project-confidence",
        ),
        (
            "unavailable-augmentation-in-child",
            "valid.json",
            "whole-interface.xml",
            False,
            "not found in the target context",
        ),
    ]
    for name, content in documents.items():
        output[name] = (json.dumps(content, indent=2) + "\n").encode()
    case_manifest = {
        "scope": "Synthetic configuration examples; not device capture or production service",
        "model_package_sha256": digest((PACKAGE / "manifest.json").read_bytes()),
        "files": {name: digest(raw) for name, raw in sorted(output.items())},
        "cases": [
            dict(
                zip(
                    ("name", "data", "extension", "expected_valid", "diagnostic"), spec, strict=True
                )
            )
            for spec in case_specs
        ],
    }
    output["manifest.json"] = (json.dumps(case_manifest, indent=2) + "\n").encode()
    return output


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    files = build()
    OUTPUT.mkdir(parents=True, exist_ok=True)
    if args.check and {p.name for p in OUTPUT.iterdir() if p.is_file()} != set(files):
        raise ValueError("Generated case file set differs")
    for name, raw in files.items():
        if args.check:
            if (OUTPUT / name).read_bytes() != raw:
                raise ValueError(f"Generated case differs: {name}")
        else:
            (OUTPUT / name).write_bytes(raw)
    print(json.dumps({"files": len(files), "checked": args.check}))
