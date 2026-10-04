"""Pinned read-only TextFSM assets, schema projections and completeness checks.

Comware and IOS ARP and route tables are read through rule-governance check items
(collectors/rules.py); the assets here serve the capabilities not yet migrated.
"""

import hashlib
import io
import re
from dataclasses import dataclass
from importlib.metadata import version
from pathlib import Path

import ntc_templates
import textfsm

from src.core.parser.parser_bundle import parse_parser_bundle
from src.core.parser.textfsm_input import prepare_textfsm_input

from ..normalize import ip_address, mac_address
from .common import interface, vlan

ROOT = Path(__file__).resolve().parents[6]


@dataclass(frozen=True)
class Asset:
    source: str
    path: str
    sha256: str
    model: str | None = None


# Only reviewed files are executable; changed/missing local assets fall through to NTC/generic.
ASSETS = {
    ("comware", "mac"): (
        Asset(
            "ntc",
            "hp_comware_display_mac-address.textfsm",
            "066bec0143b4e5addfe5e4b12deb8aad9beb916e981f6f5125fc04db4d878bbc",
        ),
    ),
    ("comware", "vlan"): (
        Asset(
            "ntc",
            "hp_comware_display_vlan_brief.textfsm",
            "bc11a3776ace464ee887bb51e468c25fcc7a38b02eb89e8a5e8c7b6541e86bfd",
        ),
    ),
    ("ios", "mac"): (
        Asset(
            "ntc",
            "cisco_ios_show_mac-address-table.textfsm",
            "28b93670e7c4a393e8cefd4501cb7230ba7269e6ee3939d887d7f2959e5d09e6",
        ),
    ),
    ("nxos", "arp"): (
        Asset(
            "repository",
            "Cisco/N9K-C93180YC-FX/show_ip_arp__manual-20260923.textfsm",
            "64f8ca8acf1d20b0f95d57a53db07d39bb1ba8a3c26bc708fc7e4be12c56faa6",
            "N9K-C93180YC-FX",
        ),
        Asset(
            "ntc",
            "cisco_nxos_show_ip_arp.textfsm",
            "a3edce17e75dd2da71d0bc3da6643efc45e7391926caec71c678689fe11826f7",
        ),
    ),
    ("nxos", "mac"): (
        Asset(
            "ntc",
            "cisco_nxos_show_mac_address-table.textfsm",
            "eb2f369f50868f459e35907b5b8c1959c7ddd23d9f7632240d7bd53b0ccc9924",
        ),
    ),
}


def load(asset):
    if asset.source == "ntc":
        if version("ntc-templates") != "9.1.0":
            raise ValueError("template_package_version_changed")
        root = Path(ntc_templates.__file__).parent / "templates"
    else:
        root = ROOT / "src/skills/shared/textfsm-templates"
    path = root / asset.path
    content = path.read_bytes()
    if len(content) > 128000 or hashlib.sha256(content).hexdigest() != asset.sha256:
        raise ValueError("template_digest_changed")
    text = content.decode("utf-8")
    bundle = parse_parser_bundle(text)
    if bundle:
        if len(bundle.record_sets) != 1:
            raise ValueError("template_record_set_unsupported")
        text = bundle.record_sets[0].template_text
    return text


def replay(template, text):
    """Use public incremental TextFSM parsing to attach each emitted row to a line."""
    fsm = textfsm.TextFSM(io.StringIO(template))
    prepared = prepare_textfsm_input(template, text)
    records, seen = [], 0
    for number, line in enumerate(prepared.splitlines(), 1):
        if len(line) > 16384:
            raise ValueError("template_line_over_limit")
        current = fsm.ParseText(line + "\n", eof=False)
        for row in current[seen:]:
            values = {
                key.upper(): value[:] if isinstance(value, list) else value
                for key, value in zip(fsm.header, row, strict=True)
            }
            records.append((values, number))
        seen = len(current)
        if seen > 100000:
            raise ValueError("template_record_budget")
    if len(fsm.ParseText("", eof=True)) != seen:
        raise ValueError("template_implicit_eof_record")
    return records


def project_table(cap, rows, family):
    records = []
    for raw, _ in rows:
        if cap == "arp":
            row = {
                "vrf": raw.get("VRF") or "default",
                "ip": ip_address(raw["IP_ADDRESS"]),
                "mac": mac_address(raw["MAC_ADDRESS"]),
            }
            if raw.get("INTERFACE"):
                row["ifname"] = interface(raw["INTERFACE"], family).name
            records.append(row)
        else:
            port = raw.get("INTERFACE") or raw.get("DESTINATION_PORT") or raw.get("PORTS")
            ports = port if isinstance(port, list) else [port]
            for port in ports:
                records.append(
                    {
                        "vlan_or_bd": str(vlan(raw["VLAN_ID"])),
                        "mac": mac_address(raw.get("MAC_ADDRESS") or raw["DESTINATION_ADDRESS"]),
                        "port": interface(port, family).name,
                        "type": (raw.get("STATE") or raw.get("TYPE") or "").lower() or None,
                    }
                )
    return records


def vlan_records(rows, text):
    """NTC matches VLAN IDs but its word-only name truncates spaced names.

    Map name from the explicit Name..Port column boundaries, with per-row evidence.
    This adapts fields without changing any shared/patrol template.
    """
    lines = text.splitlines()
    headers = [
        (n, line)
        for n, line in enumerate(lines, 1)
        if re.fullmatch(r"VLAN ID\s+Name\s+Port\s*", line)
    ]
    if len(headers) != 1:
        raise ValueError("vlan_header_missing")
    header_number, header = headers[0]
    # Comware 7 prints this fixed preamble before the table (lab sample E-series).
    preamble = (
        r"Brief information about all VLANs:",
        r"Supported Minimum VLAN ID:\s*\d+",
        r"Supported Maximum VLAN ID:\s*\d+",
        r"Default VLAN ID:\s*\d+",
    )
    if any(
        line.strip() and not any(re.fullmatch(p, line.strip()) for p in preamble)
        for line in lines[: header_number - 1]
    ):
        raise ValueError("uncovered_vlan_header")
    start, end = header.index("Name"), header.index("Port")
    records, sources, seen = [], [], set()
    for raw, number in rows:
        line = lines[number - 1]
        identifier = vlan(raw["VLAN_ID"])
        if identifier in seen or number <= header_number or line[:start].strip() != raw["VLAN_ID"]:
            raise ValueError("invalid_vlan_row")
        name = line[start:end].strip()
        if not name or not name.startswith(raw["VLAN_NAME"]):
            raise ValueError("vlan_name_source_mismatch")
        records.append({"vlan_id": identifier, "name": name})
        sources.append({"lines": [number], "raw": {**raw, "name_column": name}})
        seen.add(identifier)
    row_numbers = {number for _, number in rows}
    if any(
        line.strip()
        for n, line in enumerate(lines, 1)
        if n > header_number and n not in row_numbers
    ):
        raise ValueError("uncovered_vlan_line")
    return records, sources


def apply(identity, capability, text, fallback, *, complete, model=None):
    from ..parse import ParseResult

    family = (
        identity.get("os_family")
        if isinstance(identity, dict)
        else getattr(identity, "os_family", None)
    )
    family = "ios" if family == "iosxe" else family
    assets = ASSETS.get((family, capability), ())
    warnings = []
    for asset in assets:
        if asset.model and asset.model != model:
            continue
        try:
            rows = replay(load(asset), text)
            if not rows:
                raise ValueError("template_zero_rows_nonempty")
            if capability == "vlan":
                records, sources = vlan_records(rows, text)
            else:
                records = project_table(capability, rows, family)
                if not fallback.records or records != fallback.records:
                    raise ValueError("template_generic_disagreement")
                sources = fallback.sources
            incomplete = (
                not complete
                or "collection_incomplete" in fallback.warnings
                or fallback.status == "partial"
            )
            return ParseResult(
                records=records,
                sources=sources,
                status="partial" if incomplete else "complete",
                tier="template",
                confidence="high",
                parser_ref=f"{asset.source}:{asset.path}@{asset.sha256}",
                warnings=warnings
                + [
                    w
                    for w in fallback.warnings
                    if w not in {"no_generic_parser", "unparsed_output"}
                ],
                summary={
                    **fallback.summary,
                    "template_sha256": asset.sha256,
                    "template_package": "ntc-templates==9.1.0"
                    if asset.source == "ntc"
                    else "repository",
                },
                candidate_count=len(rows),
            )
        except (OSError, ValueError, KeyError, TypeError, textfsm.TextFSMError):
            warnings.append(f"template_rejected:{asset.source}:{asset.path}")
    fallback.warnings.extend(warnings)
    return fallback
