import ipaddress
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator


class Schema(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ConnectionRequest(Schema):
    domain: str = Field(default="default", min_length=1, max_length=64)
    address: str
    port: int = Field(default=22, ge=1, le=65535)
    credential_mode: Literal["temporary", "vault"] = "temporary"
    credential_set_id: str | None = Field(default=None, min_length=1, max_length=32)
    username: str | None = Field(default=None, min_length=1, max_length=128)
    password: SecretStr | None = Field(default=None, min_length=1, max_length=1024)
    profiles: list[str] = Field(min_length=1, max_length=6)
    allow_terminal: bool = False
    allow_session: bool = False

    @model_validator(mode="after")
    def credentials(self):
        if (self.username is None) != (self.password is None):
            raise ValueError("username and password must be supplied together")
        if self.credential_mode == "temporary":
            if self.password is None or self.credential_set_id is not None:
                raise ValueError("temporary mode requires credentials and no vault selection")
        return self

    @field_validator("address")
    @classmethod
    def valid_address(cls, value):
        return str(ipaddress.ip_address(value))


class Collect(Schema):
    request_id: str = Field(min_length=1, max_length=100)
    connection_id: str = Field(min_length=1, max_length=32)
    profile_id: str = Field(min_length=1, max_length=64)


class Ticket(Schema):
    request_id: str = Field(min_length=1, max_length=100)
    connection_id: str = Field(min_length=1, max_length=32)


class Read(Schema):
    operation_id: str
