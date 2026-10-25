import ipaddress
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator


class Schema(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _address(value):
    return str(ipaddress.ip_address(value))


class Login(Schema):
    username: str = Field(min_length=1, max_length=128)
    password: SecretStr = Field(min_length=1, max_length=1024)


class SessionCreate(Schema):
    name: str = Field(min_length=1, max_length=128)
    host: str
    port: int = Field(default=22, ge=1, le=65535)
    # Leave both empty to log in with the default username/password.
    username: str | None = Field(default=None, max_length=128)
    password: SecretStr | None = Field(default=None, max_length=1024)

    @field_validator("host")
    @classmethod
    def valid_host(cls, value):
        return _address(value)

    @model_validator(mode="after")
    def together(self):
        self.username = self.username or None
        if self.password is not None and not self.password.get_secret_value():
            self.password = None
        if (self.username is None) != (self.password is None):
            raise ValueError("用户名和密码需要同时填写，或都留空使用默认")
        return self


class SessionUpdate(Schema):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    host: str | None = None
    port: int | None = Field(default=None, ge=1, le=65535)
    # username "" clears the session's own login; an omitted password keeps the saved one.
    username: str | None = Field(default=None, max_length=128)
    password: SecretStr | None = Field(default=None, max_length=1024)

    @field_validator("host")
    @classmethod
    def valid_host(cls, value):
        return None if value is None else _address(value)


class BatchLogin(Login):
    session_ids: list[str] = Field(min_length=1, max_length=500)


class Ticket(Schema):
    request_id: str = Field(min_length=1, max_length=100)
    session_id: str | None = Field(default=None, max_length=32)
    host: str | None = None
    port: int = Field(default=22, ge=1, le=65535)
    # Typed after a failed login; used once unless saved to the session.
    login: Login | None = None
    save: bool = False

    @field_validator("host")
    @classmethod
    def valid_host(cls, value):
        return None if value is None else _address(value)

    @model_validator(mode="after")
    def one_target(self):
        if (self.session_id is None) == (self.host is None):
            raise ValueError("请指定一个已保存会话或一个设备地址")
        if self.save and (self.session_id is None or self.login is None):
            raise ValueError("只有已保存会话可以保存新输入的用户名和密码")
        return self


class HostKeyAccept(Schema):
    host: str
    port: int = Field(default=22, ge=1, le=65535)
    fingerprint: str = Field(pattern=r"^SHA256:[A-Za-z0-9+/]{43}$")

    @field_validator("host")
    @classmethod
    def valid_host(cls, value):
        return _address(value)


class DiagnosisRun(Schema):
    host: str = Field(description="路径计算或终端定位结果中的设备管理地址")
    commands: list[str] = Field(
        min_length=1,
        max_length=5,
        description="只读 display/show 或有限 ping/tracert，一次最多 5 条",
    )
    purpose: str = Field(min_length=1, max_length=512, description="为什么执行，写入审计")
    # Filled by the server from the turn's own topology results, never by the model.
    platform: Literal["comware", "nxos", "ios", "vrp"]
    turn_id: str = Field(min_length=1, max_length=64)

    @field_validator("host")
    @classmethod
    def valid_host(cls, value):
        return _address(value)

    @field_validator("commands")
    @classmethod
    def bounded(cls, value):
        if any(not item.strip() or len(item) > 256 for item in value):
            raise ValueError("each command is one non-empty line of at most 256 characters")
        return value


class Read(Schema):
    operation_id: str


class CommandPolicy(Schema):
    patterns: list[str] = Field(max_length=100)
    version: int | None = Field(default=None, ge=1)


class BatchCommands(Schema):
    request_id: str = Field(min_length=1, max_length=100)
    session_ids: list[str] = Field(min_length=1, max_length=20)
    commands: list[str] = Field(min_length=1, max_length=50)

    @field_validator("commands")
    @classmethod
    def complete_lines(cls, values):
        if any(
            not text.strip()
            or len(text) > 2048
            or any(ord(char) < 32 or ord(char) == 127 for char in text)
            for text in values
        ):
            raise ValueError("每条命令必须是非空完整单行，不含控制字符，最多 2048 字符")
        return values
