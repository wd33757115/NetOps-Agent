import ipaddress
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator


class Schema(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Credentials(Schema):
    username: str = Field(min_length=1, max_length=128)
    password: SecretStr = Field(min_length=1, max_length=1024)


class Discovery(Schema):
    request_id: str = Field(min_length=1, max_length=100)
    network_id: str | None = None
    name: str = Field(default="网络拓扑", min_length=1, max_length=128)
    seeds: list[str] = Field(min_length=1, max_length=200)
    allowed_cidrs: list[str] = Field(min_length=1, max_length=32)
    port: int = Field(default=22, ge=1, le=65535)
    # A manual choice applies only to seeds; every discovered neighbor starts at auto.
    profile_id: Literal["auto", "comware-lldp-v1", "nxos-lldp-v1"] = "auto"
    max_depth: int = Field(default=8, ge=0, le=16)
    max_targets: int = Field(default=200, ge=1, le=200)
    # NETCONF first for the tables netconf-api publishes ("prefer"), never ("off"), or
    # as well as the CLI, compared fact by fact ("compare").
    netconf: Literal["prefer", "off", "compare"] = "prefer"
    # A temporary login for this run only, never saved; empty uses the default login.
    credentials: Credentials | None = None

    @field_validator("seeds")
    @classmethod
    def addresses(cls, values):
        return list(dict.fromkeys(str(ipaddress.ip_address(value)) for value in values))

    @field_validator("allowed_cidrs")
    @classmethod
    def networks(cls, values):
        return [str(ipaddress.ip_network(value, strict=True)) for value in values]


class Resolve(Schema):
    request_id: str = Field(min_length=1, max_length=100)
    version: int = Field(ge=1)
    action: Literal["retry", "skip"]
    credentials: Credentials | None = None
    profile_id: Literal["auto", "comware-lldp-v1", "nxos-lldp-v1"] | None = None


class Capture(Schema):
    address: str
    observed_at: str
    outputs: dict[str, str]
    complete: bool = True

    @field_validator("address")
    @classmethod
    def address_valid(cls, value):
        return str(ipaddress.ip_address(value))

    @field_validator("observed_at")
    @classmethod
    def time_valid(cls, value):
        from datetime import datetime

        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            raise ValueError("观测时间必须包含时区")
        return parsed.isoformat()


class Import(Schema):
    request_id: str = Field(min_length=1, max_length=100)
    network_id: str | None = None
    name: str = Field(default="离线拓扑", max_length=128)
    profile_id: Literal["comware-lldp-v1", "nxos-lldp-v1"]
    captures: list[Capture] = Field(min_length=1, max_length=200)
