"""Bounded, deterministic parsing entry point; subsequent tiers plug in here."""

import re
from dataclasses import dataclass, field
from types import SimpleNamespace

from src.core.parser.output_classifier import UNSUPPORTED_PATTERNS, OutputClassifier

from .normalize import parser_text
from .tiers import (
    generic_arp,
    generic_ifbrief,
    generic_ipif,
    generic_lag,
    generic_lldp,
    generic_lldp_local,
    generic_mac,
    generic_ospf,
    generic_switchport,
    generic_trunk,
    generic_version,
    generic_vlan,
    generic_vrfs,
    generic_vrrp,
)
from .tiers.common import audit_table, table_count

PARSERS = {
    "lldp": generic_lldp.parse,
    "lldp_local": generic_lldp_local.parse,
    "arp": generic_arp.parse,
    "mac": generic_mac.parse,
    "interfaces": generic_ifbrief.parse,
    "ip": generic_ipif.parse,
    "version": generic_version.parse,
    "vrrp": generic_vrrp.parse,
    "lag": generic_lag.parse,
    "vlan": generic_vlan.parse,
    "trunk": generic_trunk.parse,
    "switchport": generic_switchport.parse,
    "ospf_interfaces": generic_ospf.parse_interfaces,
    "ospf_process": generic_ospf.parse_process,
    "vrfs": generic_vrfs.parse,
}
EMPTY = {
    "lldp": r"No (?:LLDP )?neighbors?(?: found)?\.?",
    "arp": r"No ARP entries(?: found)?\.?",
    "mac": r"No MAC(?: address)? entries(?: found)?\.?",
    "lag": r"No link aggregation group exists\.",
    "vrrp": r"VRRP4 is not configured\.",
    "vrfs": (
        r"(?:L3VPN is not configured\.|Total VPN-Instances configured\s*:\s*0"
        r"(?:\s*\n[ \t]*License limit\s*:\s*[YN]\s*\n[ \t]*"
        r"Max VPN instances permitted by license\s*:\s*\d+)?)"
    ),
}
MAX_BYTES = 8 * 1024 * 1024


@dataclass
class ParseResult:
    records: list[dict] = field(default_factory=list)
    status: str = "failed"
    tier: str = "generic"
    parser_ref: str = ""
    confidence: str = "medium"
    warnings: list[str] = field(default_factory=list)
    summary: dict = field(default_factory=dict)
    sources: list[dict] = field(default_factory=list)
    candidate_count: int = 0
    rejected_count: int = 0

    def fact_summary(self):
        """Sidecar fits the existing FactSet summary; typed row schemas stay unchanged."""
        if self.status in {"failed", "unsupported"}:
            return {}
        return {
            **self.summary,
            "parse_metadata": {
                "tier": self.tier,
                "confidence": self.confidence,
                "warnings": self.warnings,
                "sources": self.sources,
                "line_coordinates": "parser_text_v1_1based",
                "candidate_count": self.candidate_count,
                "rejected_count": self.rejected_count,
            },
        }


def framed_text(raw, command=None, prompt=None):
    clean = parser_text(raw)
    lines = clean.text.split("\n")
    occupied = [index for index, line in enumerate(lines) if line.strip()]
    if occupied:
        if command and lines[occupied[0]].strip() == command:
            lines[occupied[0]] = ""
        if prompt and lines[occupied[-1]].strip() == prompt:
            lines[occupied[-1]] = ""
    return "\n".join(lines)


def parse_generic(identity, capability, raw, *, complete=True, command=None, prompt=None):
    """`complete` is transport evidence, not something inferred from cleaned CLI text.

    No route regex fallback, no profile/template lookup in this tier. Unknown
    syntax fails visibly and can be handed to later parsing tiers by the caller.
    """
    result = ParseResult(parser_ref=f"generic:{capability}:v1")
    if not isinstance(raw, str) or len(raw) > MAX_BYTES or len(raw.encode("utf-8")) > MAX_BYTES:
        result.warnings.append("input_invalid_or_over_limit")
        return result
    clean = parser_text(raw)
    # Raw terminal captures may retain framing. Only remove exact values supplied
    # by the collector, at the edges; leave blank lines to preserve evidence offsets.
    text = framed_text(raw, command, prompt)
    decision = SimpleNamespace(canonical_command="", empty_semantics={})
    classified = (
        OutputClassifier()
        .classify(
            raw,
            decision=decision,
            profile={
                "unsupported_patterns": [
                    *UNSUPPORTED_PATTERNS,
                    r"(?m)^\s*%\s*(?:Too many parameters|Wrong parameter)\b",
                    r"(?m)^\s*The operation is not supported on the specified "
                    r"slot or subslot\.\s*$",
                ],
            },
        )
        .status
    )
    if classified in {"unsupported_command", "command_error"}:
        result.status = "unsupported" if classified == "unsupported_command" else "failed"
        result.warnings.append(classified)
        return result
    incomplete = not complete or clean.had_paging or classified in {"paged", "truncated"}
    if incomplete:
        result.warnings.append("collection_incomplete")
    family = (
        identity.get("os_family", "unknown")
        if isinstance(identity, dict)
        else getattr(identity, "os_family", "unknown")
    )
    empty_pattern = EMPTY.get(capability)
    if (
        (family in {"ios", "iosxe"} and capability in {"arp", "vrrp", "trunk"})
        or (family == "comware" and capability == "trunk")
    ) and not text.strip():
        # Verified lab outputs have no header for empty IOS ARP/VRRP/trunks
        # and Comware trunks; transport completeness is still required.
        empty_pattern = ""
    if empty_pattern is not None and re.fullmatch(empty_pattern, text.strip(), re.I):
        if incomplete and capability in {"trunk", "switchport"}:
            result.summary["invalid_structure"] = True
            result.status = "partial"
        else:
            result.status = "failed" if incomplete else "complete"
        return result
    parser = PARSERS.get(capability)
    if parser is None:
        result.warnings.append("no_generic_parser")
        return result
    try:
        parsed = parser(text, family)
    except ValueError:
        result.warnings.append("invalid_fact_value")
        return result
    if capability in {"arp", "mac", "interfaces", "ip"}:
        audit_table(text, capability, parsed)
    result.candidate_count, result.rejected_count = parsed.candidates, parsed.rejected
    result.warnings.extend(parsed.warnings)
    result.sources = parsed.sources
    counts = table_count(text) if capability in {"lldp", "arp", "mac", "interfaces", "ip"} else []
    if counts == [0] and len([line for line in text.splitlines() if line.strip()]) == 1:
        parsed.empty_verified = True
    count_mismatch = any(count != len(parsed.records) for count in counts)
    if count_mismatch:
        result.warnings.append("advertised_count_mismatch")
    if incomplete and capability in {"trunk", "switchport"}:
        # Unread tables/blocks can hide another row or operational contradiction.
        # Persist the veto in summary-only facts, even for callers that omit the
        # parse metadata; individually damaged known ports still use local gates.
        parsed.summary["invalid_structure"] = True
    has_facts = bool(parsed.records or parsed.summary)
    if has_facts:
        result.records, result.summary = parsed.records, parsed.summary
        result.status = "partial" if incomplete or parsed.rejected or count_mismatch else "complete"
    elif parsed.empty_verified and not incomplete and not parsed.rejected and not count_mismatch:
        result.status = "complete"
    else:
        result.warnings.append("unparsed_output")
    return result


def parse(
    identity,
    capability,
    raw,
    *,
    complete=True,
    command=None,
    prompt=None,
    use_templates=True,
    model=None,
):
    result = parse_generic(
        identity, capability, raw, complete=complete, command=command, prompt=prompt
    )
    # Classification and transport bounds always precede template execution.
    if (
        not use_templates
        or result.status == "unsupported"
        or any(w in result.warnings for w in {"input_invalid_or_over_limit", "command_error"})
        or (result.status == "complete" and not result.records and not result.summary)
    ):
        return result
    from .tiers.template import apply

    return apply(
        identity,
        capability,
        framed_text(raw, command, prompt),
        result,
        complete=complete,
        model=model,
    )
