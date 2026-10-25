"""Resolve credential references without exposing secret storage to skills."""

from __future__ import annotations

import os
from typing import Any

from dotenv import load_dotenv

from src.platform.kernel.registry import ROOT as PROJECT_ROOT

load_dotenv(PROJECT_ROOT / ".env")


class CredentialResolver:
    """Resolve short-lived collector credentials from a credential reference."""

    ENV_PREFIXES = ("NETOPS_", "DEVICE_", "NETWORK_")
    SENSITIVE_NAME_HINTS = ("PASSWORD", "PASS", "SECRET", "TOKEN", "KEY")

    def resolve_password(self, credential_ref: str | None) -> str | None:
        ref = str(credential_ref or "").strip()
        if not ref:
            return None
        if ref.startswith("env:"):
            return os.getenv(ref[4:])
        raise ValueError(f"unsupported credential_ref scheme: {ref.split(':', 1)[0]}")

    def list_references(self) -> list[dict[str, Any]]:
        """Return selectable credential references without exposing secret values."""
        items: list[dict[str, Any]] = []
        for name, value in os.environ.items():
            normalized = name.upper()
            if not (
                normalized.startswith(self.ENV_PREFIXES)
                and any(hint in normalized for hint in self.SENSITIVE_NAME_HINTS)
            ):
                continue
            items.append(
                {
                    "ref": f"env:{name}",
                    "scheme": "env",
                    "name": name,
                    "available": bool(value),
                }
            )
        return sorted(items, key=lambda item: item["ref"])

    def inspect_reference(self, credential_ref: str | None) -> dict[str, Any]:
        ref = str(credential_ref or "").strip()
        if not ref:
            return {
                "ref": ref,
                "scheme": None,
                "supported": False,
                "available": False,
                "failure_code": "missing_credential_ref",
            }
        scheme = ref.split(":", 1)[0] if ":" in ref else None
        try:
            value = self.resolve_password(ref)
        except ValueError as exc:
            return {
                "ref": ref,
                "scheme": scheme,
                "supported": False,
                "available": False,
                "failure_code": "unsupported_scheme",
                "message": str(exc),
            }
        return {
            "ref": ref,
            "scheme": scheme,
            "supported": True,
            "available": bool(value),
            "failure_code": None if value else "credential_unavailable",
        }
