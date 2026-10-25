"""Pure normalization for parser inputs. Original evidence is never modified."""

import ipaddress
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

from .families import global_instance


def token(value):
    if not isinstance(value, str) or not value or value != value.strip() or not value.isascii():
        raise ValueError("invalid token")
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError("control character in token")
    return value


def mac_address(value):
    value = token(value)
    formats = (
        r"[0-9a-f]{12}",
        r"(?:[0-9a-f]{2}:){5}[0-9a-f]{2}",
        r"(?:[0-9a-f]{2}-){5}[0-9a-f]{2}",
        r"(?:[0-9a-f]{4}-){2}[0-9a-f]{4}",
        r"(?:[0-9a-f]{4}\.){2}[0-9a-f]{4}",
    )
    if not any(re.fullmatch(pattern, value, re.I) for pattern in formats):
        raise ValueError("invalid MAC address")
    compact = re.sub(r"[-:.]", "", value).lower()
    return ":".join(compact[i : i + 2] for i in range(0, 12, 2))


def ip_address(value):
    value = token(value)
    if "%" in value:
        raise ValueError("zone identifiers require a separate interface scope")
    return str(ipaddress.ip_address(value))


def network_prefix(value):
    value = token(value)
    if "/" not in value or "%" in value:
        raise ValueError("explicit network prefix required")
    return str(ipaddress.ip_network(value, strict=True))


def interface_address(value):
    value = token(value)
    if "/" not in value or "%" in value:
        raise ValueError("explicit interface prefix required")
    interface = ipaddress.ip_interface(value)
    return {"address": str(interface.ip), "prefix_len": interface.network.prefixlen}


def vrf_name(value, platform):
    if value is None or value == "" or value == "--":
        return "default"
    if (
        not isinstance(value, str)
        or len(value) > 256
        or any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in value)
    ):
        raise ValueError("invalid VRF name")
    # A family may name its global table (VRP: "_public_"; families.global_instance).
    return "default" if value == global_instance(platform) else value


def observed_time(command_finished_at):
    if not isinstance(command_finished_at, datetime) or command_finished_at.utcoffset() is None:
        raise ValueError("command completion time must include timezone")
    return command_finished_at.astimezone(timezone.utc)


def speed_mbps(value):
    if isinstance(value, str) and value in {"--", "-", "auto", "unknown"}:
        return None
    value = token(value)
    match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)([KMGT])(?:bps)?(?:\(a\))?", value, re.I)
    if not match:
        raise ValueError("speed requires an explicit unit")
    result = (
        Decimal(match.group(1))
        * {"K": Decimal("0.001"), "M": 1, "G": 1000, "T": 1000000}[match.group(2).upper()]
    )
    if result != result.to_integral_value() or result > 2147483647:
        raise ValueError("speed cannot be represented as integer Mbps")
    return int(result)


def age_seconds(value, *, unit):
    if unit not in {"s", "min", "h", "d"}:
        raise ValueError("explicit age unit required")
    if isinstance(value, str) and value in {"--", "-"}:
        return None
    if type(value) is int:
        amount = value
    elif isinstance(value, str) and re.fullmatch(r"[0-9]+", value):
        amount = int(value)
    else:
        raise ValueError("age must be an unsigned integer")
    result = amount * {"s": 1, "min": 60, "h": 3600, "d": 86400}[unit]
    if result < 0 or result > 2147483647:
        raise ValueError("age out of range")
    return result


@dataclass(frozen=True)
class ParserText:
    text: str
    had_paging: bool


def parser_text(raw):
    if not isinstance(raw, str):
        raise ValueError("decode original bytes explicitly before normalizing")
    paging = re.compile(r"[ \t]*-{2,}\s*More\s*-{2,}(?:\x1b\[\d*D|[ \x08])*", re.I)
    had_paging = bool(paging.search(raw))
    value = paging.sub("", raw)
    value = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", value)
    value = re.sub(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)", "", value)
    for ending in ("\r\r\n", "\r\n", "\n\r"):
        value = value.replace(ending, "\n")
    return ParserText(value.replace("\r", "\n").replace("\x00", ""), had_paging)
