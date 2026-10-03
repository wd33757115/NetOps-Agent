"""Template fields must match evidence and generic facts, not just produce rows."""

from dataclasses import replace

import pytest

from src.apps.topology.backend.collectors.parse import parse
from src.apps.topology.backend.collectors.tiers import template
from src.apps.topology.backend.facts.repository import FactInput, validate_records
from tests.unit.test_topology_generic_parsers import STYLES, lab


def run(cap, text, family="comware", **kwargs):
    return parse({"os_family": family}, cap, text, **kwargs)


@pytest.mark.parametrize("device,routes,vid", [("vsr-1", 18, 200), ("vsr-2", 19, 100)])
@pytest.mark.parametrize("capability", ["arp", "mac", "routes", "vlan"])
def test_real_template_replay_and_fact_contract(device, routes, vid, capability):
    result = run(capability, lab(capability, device))
    assert result.status == "complete" and result.tier == "template"
    assert result.confidence == "high" and "@" in result.parser_ref
    assert result.sources and result.summary["template_sha256"]
    validate_records(
        FactInput(
            capability=capability,
            observed_at="2026-09-28T12:00:00Z",
            status=result.status,
            source="offline",
            platform_profile="h3c-comware7",
            parser_ref=result.parser_ref,
            evidence_ref="test",
            records=result.records,
            summary=result.fact_summary(),
        )
    )
    if capability == "routes":
        assert len(result.records) == routes
        assert any(row["prefix"] == "0.0.0.0/32" for row in result.records)
        assert not any(row["prefix"] == "0.0.0.0/0" for row in result.records)
        assert any(
            hop["ifname"] == "InLoopBack0" for row in result.records for hop in row["next_hops"]
        )
        assert any(hop["ifname"] == "NULL0" for row in result.records for hop in row["next_hops"])
        remote = "1.1.1.0/24" if device == "vsr-1" else "4.4.4.4/32"
        assert next(r for r in result.records if r["prefix"] == remote)["protocol"] == "O_INTRA"
    elif capability == "vlan":
        assert result.records == [
            {"vlan_id": 1, "name": "VLAN 0001"},
            {"vlan_id": vid, "name": f"VLAN {vid:04d}"},
        ]
    else:
        generic = run(capability, lab(capability, device), use_templates=False)
        assert result.records == generic.records


@pytest.mark.parametrize(
    "case",
    [c for c in STYLES if c["family"] in {"comware", "ios", "nxos"}],
    ids=lambda c: c["family"],
)
@pytest.mark.parametrize("cap", ["arp", "mac"])
def test_three_profiles_synthetic_table_equivalence(case, cap):
    result = run(cap, case[cap], case["family"])
    generic = run(cap, case[cap], case["family"], use_templates=False)
    assert result.status == generic.status == "complete"
    assert result.records == generic.records
    if case["family"] == "nxos" and cap == "mac":
        assert result.tier == "generic"  # This shortened sample lacks the NTC table columns.
        assert any(w.startswith("template_rejected:") for w in result.warnings)
    else:
        assert result.tier == "template"


def test_unknown_family_never_guesses_a_route_template():
    result = run("routes", lab("routes"), "unknown")
    assert result.status == "failed" and result.tier == "generic"


def test_repository_precedence_when_exact_model_asset_available(monkeypatch):
    asset = template.ASSETS[("ios", "arp")][0]
    original_load = template.load
    # Use the installed asset when available; deployments without ignored local assets
    # must take the pinned NTC fallback rather than depend on a developer's workspace.
    path = template.ROOT / "src/skills/shared/textfsm-templates" / asset.path
    if not path.exists():
        pytest.skip("optional local repository asset absent")
    result = run(
        "arp", next(c["arp"] for c in STYLES if c["family"] == "ios"), "ios", model=asset.model
    )
    assert result.status == "complete" and result.parser_ref.startswith("repository:")

    def absent(item):
        if item.source == "repository":
            raise FileNotFoundError
        return original_load(item)

    monkeypatch.setattr(template, "load", absent)
    fallback = run(
        "arp", next(c["arp"] for c in STYLES if c["family"] == "ios"), "ios", model=asset.model
    )
    assert fallback.status == "complete" and fallback.parser_ref.startswith("ntc:")


@pytest.mark.parametrize("cap", ["arp", "mac", "routes", "vlan"])
def test_digest_mismatch_does_not_execute_changed_template(monkeypatch, cap):
    asset = template.ASSETS[("comware", cap)][0]
    monkeypatch.setitem(template.ASSETS, ("comware", cap), (replace(asset, sha256="0" * 64),))
    result = run(cap, lab(cap))
    assert result.tier == "generic"
    assert result.status == ("complete" if cap in {"arp", "mac", "vlan"} else "failed")


def test_changed_template_package_falls_back(monkeypatch):
    monkeypatch.setattr(template, "version", lambda _: "future")
    assert run("arp", lab("arp")).tier == "generic"
    assert run("routes", lab("routes")).status == "failed"


@pytest.mark.parametrize("suffix", ["\n---- More ----", "\n[truncated]"])
def test_incomplete_routes_never_replace_current(suffix):
    result = run("routes", lab("routes") + suffix)
    assert result.status in {"partial", "failed"}
    assert run("routes", lab("routes"), complete=False).status == "partial"


@pytest.mark.parametrize(
    "change",
    [
        lambda text: text.replace("Routes : 18", "Routes : 19"),
        lambda text: text.replace("Destinations : 18", "Destinations : 19"),
        lambda text: text.replace("1.1.1.0/24", "1.1.1.1/24"),
        lambda text: text.replace("1.1.1.0/24", "999.1.1.0/24"),
        lambda text: text.replace("GE2/0", "made-up-interface"),
        lambda text: text.replace("GE2/0", "GE2/0 UNPARSED"),
        lambda text: text + "\nBROKEN ROW\n",
        lambda text: "UNKNOWN HEADER\n" + text,
    ],
)
def test_route_malformed_counts_fields_or_coverage_fail(change):
    assert run("routes", change(lab("routes"))).status == "failed"


def test_ecmp_preserves_prefix_evidence_and_counts_forwarding_rows():
    text = (
        "Destinations : 1\tRoutes : 2\n"
        "Destination/Mask   Proto   Pre Cost        NextHop         Interface\n"
        "192.0.2.0/24 O_INTRA 10 2 198.51.100.1 GE1/0\n"
        "                           198.51.100.2 GE2/0\n"
    )
    result = run("routes", text)
    assert result.status == "complete"
    assert len(result.records) == 1 and len(result.records[0]["next_hops"]) == 2
    assert result.sources[0]["lines"] == [3, 4]
    assert result.candidate_count == 2


def test_empty_routes_requires_known_header_and_counts():
    text = (
        "Destinations : 0\tRoutes : 0\n"
        "Destination/Mask   Proto   Pre Cost        NextHop         Interface\n"
    )
    result = run("routes", text)
    assert result.status == "complete" and result.records == []
    assert run("routes", "unrecognized empty result").status == "failed"
    assert run("routes", text.replace("Routes : 0", "Routes : 1")).status == "failed"
    assert run("routes", "Destinations : 0\tRoutes : 0").status == "failed"


def test_vlan_full_names_and_wrapped_uncovered_lines():
    text = lab("vlan")
    assert run("vlan", text).records[1]["name"] == "VLAN 0200"
    malformed = run("vlan", text + "\n          wrapped-ports GE4/0\n")
    assert malformed.status == "partial" and malformed.rejected_count
    invalid = run("vlan", text.replace("200       VLAN", "4095      VLAN"))
    assert invalid.status == "partial" and invalid.rejected_count
    assert all(row["vlan_id"] != 4095 for row in invalid.records)
    unknown_header = run("vlan", "UNKNOWN HEADER\n" + text)
    assert unknown_header.status == "partial" and unknown_header.rejected_count


def test_template_zero_rows_not_treated_as_empty(monkeypatch):
    monkeypatch.setattr(template, "replay", lambda *args: [])
    result = run("arp", lab("arp"))
    assert result.tier == "generic" and len(result.records) == 4
    assert run("routes", lab("routes")).status == "failed"


def test_public_bundle_helper_reuses_local_content_without_mutation():
    asset = template.ASSETS[("ios", "arp")][0]
    path = template.ROOT / "src/skills/shared/textfsm-templates" / asset.path
    if not path.exists():
        pytest.skip("optional local repository asset absent")
    before = path.read_bytes()
    assert template.load(asset).startswith("# Adapted")
    assert path.read_bytes() == before
