"""Pure CLI classification; load and validate trusted YAML once at module import.

This is not an authorization or execution API. Execute only ``normalized`` after
applying target grants, approval, session budgets and audit at the service layer.
L0 belongs to server-issued command bundles and cannot be claimed by free text.
"""

import re
import unicodedata
from dataclasses import dataclass
from ipaddress import ip_address
from pathlib import Path
from types import MappingProxyType

import yaml


@dataclass(frozen=True)
class Decision:
    level: str
    reason: str
    normalized: str


@dataclass(frozen=True)
class Option:
    kind: str
    minimum: int = 0
    maximum: int = 0
    default: int | None = None


@dataclass(frozen=True)
class Probe:
    layout: str
    options: object
    vrf_prefix: bool
    ip_marker: bool


@dataclass(frozen=True)
class Rules:
    aliases: object
    readonly: tuple[str, ...]
    blocked: tuple[str, ...]
    approval: tuple[tuple[str, ...], ...]
    denied_patterns: tuple
    probes: object


class _UniqueLoader(yaml.SafeLoader):
    pass


def _unique_mapping(loader, node):
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node)
        if key in result:
            raise ValueError("Duplicate command policy key")
        result[key] = loader.construct_object(value_node)
    return result


_UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _unique_mapping)

_FAMILIES = {"comware", "nxos", "ios", "vrp"}
_PLATFORMS = {
    "hp_comware": "comware",
    "h3c": "comware",
    "h3c_comware": "comware",
    "cisco_nxos": "nxos",
    "nx-os": "nxos",
    "cisco_ios": "ios",
    "huawei": "vrp",
}
_BLOCKED = (
    "system-view",
    "configure",
    "reset",
    "reboot",
    "reload",
    "save",
    "write",
    "delete",
    "format",
    "copy",
    "rename",
    "mkdir",
    "undo",
    "shutdown",
    "clear",
    "erase",
    "no",
    "do",
    "bash",
    "shell",
    "run",
    "python",
    "tclsh",
    "guestshell",
)
_CAPS = {"count": 5, "size": 1500, "timeout_ms": 2000, "timeout_s": 2, "hops": 30}
_TOKEN = re.compile(r"[a-z][a-z0-9_-]*\Z")
_NAME = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.:-]{0,63}\Z")
_HOST = re.compile(
    r"(?=.{1,253}\Z)[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?)*\.?\Z"
)


def _strings(value):
    if (
        not isinstance(value, list)
        or not value
        or any(not isinstance(v, str) or not v for v in value)
    ):
        raise ValueError("Command policy requires nonempty string lists")
    return tuple(value)


def _compile(data, family):
    """Reject malformed or permissively widened policy configuration at startup."""
    keys = {
        "schema_version",
        "platform",
        "aliases",
        "readonly",
        "blocked",
        "approval",
        "deny_regex",
        "probes",
    }
    if (
        not isinstance(data, dict)
        or set(data) != keys
        or type(data["schema_version"]) is not int
        or data["schema_version"] != 1
        or data["platform"] != family
    ):
        raise ValueError("Invalid command policy schema")
    readonly = _strings(data["readonly"])
    if set(readonly) != ({"display", "dir"} if family in {"comware", "vrp"} else {"show", "dir"}):
        raise ValueError("Invalid read-only command heads")
    aliases = data["aliases"]
    if not isinstance(aliases, dict) or any(
        not isinstance(k, str)
        or not _TOKEN.fullmatch(k)
        or v not in readonly
        or k in _BLOCKED
        or k in readonly
        or not v.startswith(k)
        or len(k) < (3 if v == "display" else 2)
        for k, v in aliases.items()
    ):
        raise ValueError("Invalid command aliases")
    blocked = _strings(data["blocked"])
    if any(not _TOKEN.fullmatch(v) for v in blocked):
        raise ValueError("Invalid blocked command")
    approval = _strings(data["approval"])
    patterns = _strings(data["deny_regex"])
    probes = {}
    if not isinstance(data["probes"], dict) or "ping" not in data["probes"]:
        raise ValueError("Missing ping policy")
    for command, spec in data["probes"].items():
        if command not in {"ping", "tracert", "traceroute"} or set(spec) != {
            "layout",
            "vrf_prefix",
            "ip_marker",
            "options",
        }:
            raise ValueError("Invalid probe schema")
        if (
            spec["layout"] not in {"before", "after"}
            or type(spec["vrf_prefix"]) is not bool
            or type(spec["ip_marker"]) is not bool
        ):
            raise ValueError("Invalid probe layout")
        options = {}
        for flag, item in spec["options"].items():
            if not isinstance(flag, str) or not re.fullmatch(r"-?[a-z][a-z-]*", flag):
                raise ValueError("Invalid probe flag")
            kind = item.get("kind")
            if kind in {"source", "vrf"}:
                if set(item) != {"kind"}:
                    raise ValueError("Invalid string option")
                options[flag] = Option(kind)
            elif kind in _CAPS:
                if set(item) != {"kind", "min", "max", "default"} or any(
                    type(item[k]) is not int for k in ("min", "max", "default")
                ):
                    raise ValueError("Invalid numeric probe limit")
                if not 1 <= item["min"] <= item["default"] <= item["max"] <= _CAPS[kind]:
                    raise ValueError("Probe limits exceed policy ceilings")
                options[flag] = Option(kind, item["min"], item["max"], item["default"])
            else:
                raise ValueError("Unknown probe option kind")
        kinds = [v.kind for v in options.values()]
        required = {"count", "size"} if command == "ping" else {"count", "hops"}
        if (
            len(set(kinds)) != len(kinds)
            or not required.issubset(kinds)
            or len(set(kinds) & {"timeout_s", "timeout_ms"}) != 1
        ):
            raise ValueError("Missing or duplicate probe budget")
        probes[command] = Probe(
            spec["layout"], MappingProxyType(options), spec["vrf_prefix"], spec["ip_marker"]
        )
    return Rules(
        MappingProxyType(dict(aliases)),
        readonly,
        tuple(dict.fromkeys((*_BLOCKED, *blocked))),
        tuple(tuple(s.casefold().split()) for s in approval),
        tuple(re.compile(p, re.IGNORECASE | re.ASCII) for p in patterns),
        MappingProxyType(probes),
    )


def _load_rules():
    directory = Path(__file__).with_name("policy_rules")
    return MappingProxyType(
        {
            family: _compile(
                yaml.load(
                    (directory / f"{family}.yaml").read_text(encoding="utf-8"), Loader=_UniqueLoader
                ),
                family,
            )
            for family in sorted(_FAMILIES)
        }
    )


_RULES = _load_rules()


def _host(value):
    if value.casefold() in {"ip", "ipv6", "vrf", "multicast", "mcast", "mpls", "lsp", "vpls"}:
        return False
    try:
        address = ip_address(value)
        return (
            address.version == 4
            and not address.is_multicast
            and not address.is_unspecified
            and str(address) != "255.255.255.255"
        )
    except ValueError:
        # Numeric-looking malformed IPv4 addresses must not become DNS names.
        return bool(_HOST.fullmatch(value)) and not re.fullmatch(r"[0-9.]+", value)


def _probe(tokens, spec, required_vrf):
    head, *args = tokens
    prefix = [head]
    vrf = None
    if spec.vrf_prefix and args and args[0].casefold() == "vrf":
        if len(args) < 3 or not _NAME.fullmatch(args[1]):
            return Decision("L4", "invalid_vrf", " ".join(tokens))
        vrf = args[1]
        prefix += ["vrf", vrf]
        args = args[2:]
    if spec.ip_marker and args and args[0].casefold() == "ip":
        args = args[1:]
    if not args:
        return Decision("L3", "interactive_probe_requires_review", " ".join(tokens))
    target = args[-1] if spec.layout == "before" else args[0]
    args = args[:-1] if spec.layout == "before" else args[1:]
    if not _host(target):
        return Decision("L3", "unsupported_probe_target", " ".join(tokens))
    values = {}
    while args:
        flag = args.pop(0).casefold()
        option = spec.options.get(flag)
        if option is None:
            return Decision("L3", "unsupported_probe_option", " ".join(tokens))
        if flag in values or not args:
            return Decision("L4", "duplicate_or_missing_probe_option", " ".join(tokens))
        value = args.pop(0)
        if option.kind in _CAPS:
            if (
                not re.fullmatch(r"[0-9]{1,8}", value)
                or not option.minimum <= int(value) <= option.maximum
            ):
                return Decision("L4", "probe_budget_exceeded_or_invalid", " ".join(tokens))
            value = str(int(value))
        elif option.kind == "source":
            try:
                address = ip_address(value)
                if address.version != 4 or address.is_multicast or address.is_unspecified:
                    raise ValueError
            except ValueError:
                return Decision("L3", "unsupported_probe_source", " ".join(tokens))
        elif not _NAME.fullmatch(value):
            return Decision("L4", "invalid_vrf", " ".join(tokens))
        if option.kind == "vrf":
            vrf = value
        values[flag] = value
    if required_vrf is not None and (
        not isinstance(required_vrf, str)
        or not _NAME.fullmatch(required_vrf)
        or vrf != required_vrf
    ):
        return Decision("L4", "required_vrf_missing_or_mismatched", " ".join(tokens))
    output = []
    for flag, option in spec.options.items():
        value = values.get(flag, str(option.default) if option.default is not None else None)
        if value is not None:
            output.extend((flag, value))
    normalized = prefix + (output + [target] if spec.layout == "before" else [target] + output)
    return Decision("L2", "bounded_probe", " ".join(normalized))


def classify(platform: str, command_text: str, *, required_vrf: str | None = None) -> Decision:
    """Classify one command without I/O; approval and quotas are NOT enforced here."""
    if not isinstance(command_text, str) or not command_text or len(command_text) > 4096:
        return Decision("L4", "invalid_command", "")
    # Reject controls before whitespace normalization: never erase a second line.
    if any(
        c in ";><&`$\r\n\v\f\x85\u2028\u2029"
        or (unicodedata.category(c).startswith("C") and c != "\t")
        for c in command_text
    ):
        return Decision("L4", "control_operator_or_redirection", "")
    normalized = " ".join(command_text.split())
    if not normalized:
        return Decision("L4", "empty_command", "")
    family = platform.casefold().strip() if isinstance(platform, str) else ""
    rules = _RULES.get(_PLATFORMS.get(family, family))
    if rules is None:
        return Decision("L4", "unsupported_platform", normalized)
    segments = [part.strip() for part in normalized.split("|")]
    if not segments[0]:
        return Decision("L4", "missing_command", normalized)
    tokens = segments[0].split()
    head = tokens[0].casefold()
    if not _TOKEN.fullmatch(head):
        return Decision("L4", "invalid_command_head", normalized)
    tokens[0] = head = rules.aliases.get(head, head)
    normalized = " ".join(tokens)
    # Prefix rejection catches abbreviated writes/configuration commands.
    if any(word.startswith(head) for word in rules.blocked):
        return Decision("L4", "configuration_or_mutation", normalized)
    if any(pattern.search(normalized) for pattern in rules.denied_patterns):
        return Decision("L4", "denied_command_pattern", normalized)
    for part in segments[1:]:
        words = part.split(maxsplit=1)
        if not words or words[0].casefold() not in {"include", "begin", "count"}:
            return Decision("L4", "unsafe_pipe", normalized)
        filter_name = words[0].casefold()
        if (filter_name == "count" and len(words) != 1) or (
            filter_name != "count" and len(words) != 2
        ):
            return Decision("L4", "invalid_filter", normalized)
        normalized += " | " + filter_name + (" " + words[1] if len(words) == 2 else "")
    folded = [t.casefold() for t in tokens]
    for prefix in rules.approval:
        if len(folded) >= len(prefix) and all(
            expected.startswith(actual) for actual, expected in zip(folded, prefix)
        ):
            tail = folded[len(prefix) :]
            if (prefix[-1] == "diagnostic-information" and tail) or (
                prefix[-1] == "tech-support"
                and any(
                    value in {"file", "filename", "to-file"}
                    or ":" in value
                    or value.endswith((".txt", ".log", ".gz"))
                    for value in tail
                )
            ):
                return Decision("L4", "diagnostic_output_file", normalized)
            return Decision("L3", "explicit_approval_required", normalized)
    if "?" in normalized:
        return Decision("L3", "interactive_help_requires_review", normalized)
    if head in rules.probes:
        if len(segments) != 1:
            return Decision("L3", "probe_filters_require_review", normalized)
        return _probe(tokens, rules.probes[head], required_vrf)
    if head in rules.readonly and len(tokens) > (0 if head == "dir" else 1):
        return Decision("L1", "read_only_query", normalized)
    return Decision("L3", "unknown_command_requires_review", normalized)
