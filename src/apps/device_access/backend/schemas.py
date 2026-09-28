import ipaddress

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator


class Schema(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ConnectionRequest(Schema):
    domain: str = Field(default="default", min_length=1, max_length=64)
    address: str
    port: int = Field(default=22, ge=1, le=65535)
    username: str = Field(min_length=1, max_length=128)
    password: SecretStr = Field(min_length=1, max_length=1024)
    profiles: list[str] = Field(min_length=1, max_length=6)
    allow_terminal: bool = False

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
