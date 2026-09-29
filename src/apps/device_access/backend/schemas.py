import ipaddress

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


class Collect(Schema):
    request_id: str = Field(min_length=1, max_length=100)
    session_id: str = Field(min_length=1, max_length=32)
    profile_id: str = Field(min_length=1, max_length=64)


class Read(Schema):
    operation_id: str
