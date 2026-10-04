"""Template fields must match evidence and generic facts, not just produce rows.

IOS and Comware tables are read through rule governance (test_topology_rules.py); the
pinned template tier serves NX-OS ARP and MAC tables, checked against the generic parser.
"""

from dataclasses import replace

import pytest

from src.apps.topology.backend.collectors.parse import parse
from src.apps.topology.backend.collectors.tiers import template
from tests.unit.test_topology_generic_parsers import STYLES, lab

NXOS = next(c for c in STYLES if c["family"] == "nxos")


def run(cap, text, family="nxos", **kwargs):
    return parse({"os_family": family}, cap, text, **kwargs)


def test_nxos_synthetic_tables_agree_with_the_generic_parser():
    for cap in ("arp", "mac"):
        result = run(cap, NXOS[cap])
        generic = run(cap, NXOS[cap], use_templates=False)
        assert result.status == generic.status == "complete"
        assert result.records == generic.records
    assert run("arp", NXOS["arp"]).tier == "template"
    mac = run("mac", NXOS["mac"])
    assert mac.tier == "generic"  # This shortened sample lacks the NTC table columns.
    assert any(w.startswith("template_rejected:") for w in mac.warnings)


def test_unknown_family_never_guesses_a_route_template():
    result = run("routes", lab("routes"), "unknown")
    assert result.status == "failed" and result.tier == "generic"


def test_repository_precedence_when_exact_model_asset_available(monkeypatch):
    asset = template.ASSETS[("nxos", "arp")][0]
    original_load = template.load
    # Use the installed asset when available; deployments without ignored local assets
    # must take the pinned NTC fallback rather than depend on a developer's workspace.
    path = template.ROOT / "src/skills/shared/textfsm-templates" / asset.path
    if not path.exists():
        pytest.skip("optional local repository asset absent")
    result = run("arp", NXOS["arp"], model=asset.model)
    assert result.status == "complete" and result.parser_ref.startswith("repository:")

    def absent(item):
        if item.source == "repository":
            raise FileNotFoundError
        return original_load(item)

    monkeypatch.setattr(template, "load", absent)
    fallback = run("arp", NXOS["arp"], model=asset.model)
    assert fallback.status == "complete" and fallback.parser_ref.startswith("ntc:")


def test_digest_mismatch_does_not_execute_changed_template(monkeypatch):
    changed = tuple(replace(a, sha256="0" * 64) for a in template.ASSETS[("nxos", "arp")])
    monkeypatch.setitem(template.ASSETS, ("nxos", "arp"), changed)
    result = run("arp", NXOS["arp"])
    assert result.tier == "generic" and result.status == "complete"


def test_changed_template_package_falls_back(monkeypatch):
    monkeypatch.setattr(template, "version", lambda _: "future")
    assert run("arp", NXOS["arp"]).tier == "generic"


def test_template_zero_rows_not_treated_as_empty(monkeypatch):
    monkeypatch.setattr(template, "replay", lambda *args: [])
    result = run("arp", NXOS["arp"])
    assert result.tier == "generic" and len(result.records) == 1


def test_public_bundle_helper_reuses_local_content_without_mutation():
    asset = template.ASSETS[("nxos", "arp")][0]
    path = template.ROOT / "src/skills/shared/textfsm-templates" / asset.path
    if not path.exists():
        pytest.skip("optional local repository asset absent")
    before = path.read_bytes()
    assert template.load(asset).startswith("# Adapted")
    assert path.read_bytes() == before
