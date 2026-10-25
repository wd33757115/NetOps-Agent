"""Bounded, deterministic parsing entry point: rule-governance check items, then the
generic tier for the families they do not cover yet."""

import re
from dataclasses import dataclass, field

from .normalize import parser_text
from .tiers import (
    generic_arp,
    generic_ifbrief,
    generic_ipif,
    generic_lldp,
    generic_mac,
    generic_version,
    generic_vrfs,
)
from .tiers.common import audit_table, table_count

PARSERS = {
    "lldp": generic_lldp.parse,
    "arp": generic_arp.parse,
    "mac": generic_mac.parse,
    "interfaces": generic_ifbrief.parse,
    "ip": generic_ipif.parse,
    "version": generic_version.parse,
    "vrfs": generic_vrfs.parse,
}
EMPTY = {
    "lldp": r"No (?:LLDP )?neighbors?(?: found)?\.?",
    "arp": r"No ARP entries(?: found)?\.?",
    "mac": r"No MAC(?: address)? entries(?: found)?\.?",
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
    # How NETCONF served this capability (collectors/netconf.py), when it was tried.
    netconf: dict = field(default_factory=dict)

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
    from src.apps.rule_governance.runtime import classify_output

    result = ParseResult(parser_ref=f"generic:{capability}:v1")
    if not isinstance(raw, str) or len(raw) > MAX_BYTES or len(raw.encode("utf-8")) > MAX_BYTES:
        result.warnings.append("input_invalid_or_over_limit")
        return result
    clean = parser_text(raw)
    # Raw terminal captures may retain framing. Only remove exact values supplied
    # by the collector, at the edges; leave blank lines to preserve evidence offsets.
    text = framed_text(raw, command, prompt)
    # Output classification is rule governance's, through its public contract.
    classified, _ = classify_output(
        raw,
        unsupported_patterns=(
            r"(?m)^\s*%\s*(?:Too many parameters|Wrong parameter)\b",
            r"(?m)^\s*The operation is not supported on the specified slot or subslot\.\s*$",
        ),
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
    if empty_pattern is not None and re.fullmatch(empty_pattern, text.strip(), re.I):
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
    has_facts = bool(parsed.records or parsed.summary)
    if has_facts:
        result.records, result.summary = parsed.records, parsed.summary
        result.status = "partial" if incomplete or parsed.rejected or count_mismatch else "complete"
    elif parsed.empty_verified and not incomplete and not parsed.rejected and not count_mismatch:
        result.status = "complete"
    else:
        result.warnings.append("unparsed_output")
    return result


def parse(identity, capability, raw, *, complete=True, command=None, prompt=None, model=None):
    """The facts of one command output.

    Rule governance publishes how a check-item capability is parsed (rules.py), with
    model-first template selection. The generic tier still serves the families it does
    not cover yet (NX-OS and other platforms).
    """
    from . import rules

    if rules.served(identity, capability):
        if not isinstance(raw, str) or len(raw.encode("utf-8")) > MAX_BYTES:
            return ParseResult(tier="rules", warnings=["input_invalid_or_over_limit"])
        return rules.parse(
            identity,
            capability,
            framed_text(raw, command, prompt),
            # Cleaning removes pager prompts; the output was still cut off there.
            complete=complete and not parser_text(raw).had_paging,
            model=model,
        )
    return parse_generic(
        identity, capability, raw, complete=complete, command=command, prompt=prompt
    )
