"""SSH key formatting and same-handshake verification, independent of App storage."""

import base64
import hashlib
import ipaddress

import paramiko

from .contracts import DeviceAccessError


def fingerprint(key):
    return "SHA256:" + base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode().rstrip("=")


def parse_public_key(key_type, public_key):
    try:
        blob = base64.b64decode(public_key, validate=True)
        if len(blob) > 16384:
            raise ValueError("oversize")
        key = paramiko.PKey.from_type_string(key_type, blob)
        if key.get_name() != key_type or key.get_base64() != public_key:
            raise ValueError("noncanonical")
        return key
    except Exception:
        raise DeviceAccessError("invalid_request", "SSH 公钥格式无效") from None


def known_hosts_line(host, port, key):
    host = str(ipaddress.ip_address(host))
    if type(port) is not int or not 1 <= port <= 65535:
        raise DeviceAccessError("invalid_request", "SSH 端口无效")
    token = host if port == 22 else f"[{host}]:{port}"
    return f"{token} {key.get_name()} {key.get_base64()}\n"


class DatabaseHostKeyPolicy(paramiko.MissingHostKeyPolicy):
    """Run before password authentication, even for previously pinned keys.

    The SSH client using this policy must not load ambient/system known_hosts:
    Paramiko bypasses this callback for keys found there. The verifier must check
    authorization and atomically pin/check storage; a false return is a rejection.
    """

    def __init__(self, target, profile, verify):
        self.target, self.profile, self.verify = target, profile, verify

    def missing_host_key(self, client, hostname, key):
        expected = (
            self.target.host
            if self.profile.port == 22
            else (f"[{self.target.host}]:{self.profile.port}")
        )
        if hostname != expected:
            raise DeviceAccessError("host_key_rejected", "SSH 主机密钥目标不匹配")
        if self.verify(self.target, self.profile.port, key) is not True:
            raise DeviceAccessError("host_key_rejected", "SSH 主机密钥未获授权")
