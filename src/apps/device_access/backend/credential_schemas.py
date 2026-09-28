"""Write-only credential input; never serialize these models as HTTP responses."""

import ipaddress
import re
from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator

from .schemas import Schema


class CredentialCreate(Schema):
    name: str = Field(min_length=1, max_length=128)
    domain: str = Field(default="default", min_length=1, max_length=64)
    username: str = Field(min_length=1, max_length=128)
    password: SecretStr = Field(min_length=1, max_length=1024)
    enable_secret: SecretStr | None = Field(default=None, min_length=1, max_length=1024)
    scope_kind: Literal["domain", "cidr", "device"]
    scope_value: str = Field(min_length=1, max_length=256)
    priority: int = Field(default=0, ge=-1000, le=1000, strict=True)

    @field_validator("name", "domain", "username", "scope_value")
    @classmethod
    def no_controls(cls, value):
        if value != value.strip() or any(ord(c) < 32 or ord(c) == 127 for c in value):
            raise ValueError("Invalid credential metadata")
        return value

    @model_validator(mode="after")
    def scope(self):
        if self.scope_kind == "domain":
            if self.scope_value != self.domain:
                raise ValueError("Domain scope must equal management domain")
        elif self.scope_kind == "cidr":
            self.scope_value = str(ipaddress.ip_network(self.scope_value, strict=False))
        elif self.scope_value.startswith("uid:"):
            if not re.fullmatch(r"uid:[A-Za-z0-9_.:-]{1,128}", self.scope_value):
                raise ValueError("Invalid device UID")
        else:
            self.scope_value = str(ipaddress.ip_address(self.scope_value))
        return self


class CredentialRotate(Schema):
    password: SecretStr = Field(min_length=1, max_length=1024)
    # Supplying null clears an old enable secret; omission preserves it.
    enable_secret: SecretStr | None = Field(default=None, min_length=1, max_length=1024)


class CredentialDisabled(Schema):
    disabled: bool = Field(strict=True)
