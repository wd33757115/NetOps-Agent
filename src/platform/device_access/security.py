"""Operator-managed address grants and short-lived encrypted credential references."""

import ipaddress
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

from cryptography.fernet import Fernet, InvalidToken

from .contracts import DeviceAccessError


@dataclass(frozen=True)
class CredentialCandidate:
    id: str
    owner_id: str
    domain: str
    scope_kind: str
    scope_value: str
    priority: int = 0
    disabled: bool = False


@dataclass(frozen=True)
class CredentialMaterial:
    username: str
    password: str = field(repr=False)
    enable_secret: str | None = field(default=None, repr=False)


def credential_order(candidates, owner, domain, address, *, remembered_id=None, device_uid=None):
    """Scope before memory/priority; pure selection capped at three unique sets."""
    address = ipaddress.ip_address(address)
    ranked = []
    for candidate in candidates:
        if candidate.disabled or candidate.owner_id != str(owner) or candidate.domain != domain:
            continue
        prefix = 0
        if candidate.scope_kind == "device":
            if candidate.scope_value not in {
                str(address),
                f"uid:{device_uid}" if device_uid else "",
            }:
                continue
            rank = 1
        elif candidate.scope_kind == "cidr":
            try:
                network = ipaddress.ip_network(candidate.scope_value)
            except ValueError:
                continue
            if address not in network:
                continue
            rank, prefix = 2, network.prefixlen
        elif candidate.scope_kind == "domain" and candidate.scope_value == domain:
            rank = 3
        elif candidate.scope_kind == "temporary" and candidate.scope_value == str(address):
            rank = 4
        else:
            continue
        # Temporary references remain last, even if supplied as remembered_id.
        if candidate.id == remembered_id and rank != 4:
            rank = 0
        ranked.append(((rank, -prefix, -candidate.priority, candidate.id), candidate))
    seen, result = set(), []
    for _, candidate in sorted(ranked, key=lambda item: item[0]):
        if candidate.id not in seen:
            seen.add(candidate.id)
            result.append(candidate)
        if len(result) == 3:
            break
    return tuple(result)


def authorize_address(owner, domain, address, action="device.collect"):
    address = ipaddress.ip_address(address)
    path = os.getenv("DEVICE_ACCESS_GRANTS_FILE", "")
    if not path:
        raise DeviceAccessError("not_authorized", "设备访问范围尚未配置")
    try:
        grants = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise DeviceAccessError("not_authorized", "设备访问范围配置不可用") from None
    for grant in grants.get(str(owner), []):
        if grant.get("domain") == domain and action in grant.get("actions", []):
            if any(address in ipaddress.ip_network(cidr) for cidr in grant.get("cidrs", [])):
                return
    raise DeviceAccessError("not_authorized", "设备不在当前用户授权范围内")


class CredentialStore:
    prefix = "netops:device-access:credential:"

    def __init__(self, client=None, key=None):
        from src.auth.token_store import get_redis

        self.client = client if client is not None else get_redis()
        if self.client is None:
            raise DeviceAccessError("credential_store_unavailable", "临时凭据服务不可用")
        try:
            self.cipher = Fernet(key or os.environ["DEVICE_ACCESS_CREDENTIAL_KEY"])
        except (KeyError, ValueError) as exc:
            raise DeviceAccessError(
                "credential_store_unavailable", "临时凭据加密密钥未配置"
            ) from exc

    def put(self, owner, domain, addresses, username, password, *, ttl=3600):
        reference = uuid4().hex
        data = dict(
            owner=str(owner),
            domain=domain,
            addresses=addresses,
            username=username,
            password=password,
        )
        self.client.set(
            self.prefix + reference,
            self.cipher.encrypt(json.dumps(data).encode()),
            ex=min(86400, max(60, ttl)),
        )
        return reference

    def get(self, reference, owner, domain, address):
        if not isinstance(reference, str) or not reference:
            raise DeviceAccessError("credential_expired", "请人工提供设备凭据")
        value = self.client.get(self.prefix + reference)
        if not value:
            raise DeviceAccessError("credential_expired", "凭据已过期，请重新输入")
        try:
            data = json.loads(self.cipher.decrypt(value))
        except (InvalidToken, ValueError, TypeError) as exc:
            raise DeviceAccessError("credential_expired", "凭据不可恢复，请重新输入") from exc
        if (
            data["owner"] != str(owner)
            or data["domain"] != domain
            or not any(
                ipaddress.ip_address(address) in ipaddress.ip_network(cidr)
                for cidr in data["addresses"]
            )
        ):
            raise DeviceAccessError("credential_scope_mismatch", "本节点需要单独提供凭据")
        return data["username"], data["password"]

    def revoke(self, reference):
        self.client.delete(self.prefix + reference)
