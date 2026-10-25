import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator


class Request(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: str = Field(min_length=1, max_length=100)


class PolicyRequest(Request):
    policy_upload_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    topology_upload_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")
    ticket_id: str = Field(min_length=1, max_length=80)
    merge_policies: StrictBool = True
    include_business_paths: StrictBool = False

    @field_validator("ticket_id")
    @classmethod
    def safe_ticket(cls, value):
        return safe_ticket(value)


def safe_ticket(value):
    if value in {".", ".."} or not re.fullmatch(r"[\w.-]{1,80}", value):
        raise ValueError("工单号只支持文字、数字、短横线、下划线和点")
    return value


class BlockDevice(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(pattern=r"^[\w.-]{1,80}$")
    vendor: Literal["华为", "H3C", "H3CF1000"]


class BlockRequest(Request):
    """One import of a block list: a new batch of books and deny rules on each firewall."""

    block_upload_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    whitelist_upload_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")
    ticket_id: str = Field(min_length=1, max_length=80)
    devices: list[BlockDevice] = Field(min_length=1, max_length=50)
    book_limit: int = Field(default=2000, ge=1, le=2000)
    allow_broad: StrictBool = False

    @field_validator("ticket_id")
    @classmethod
    def safe_ticket(cls, value):
        return safe_ticket(value)

    @field_validator("devices")
    @classmethod
    def distinct(cls, value):
        if len({device.name for device in value}) != len(value):
            raise ValueError("设备名不能重复")
        return value


class LedgerDraft(BaseModel):
    """One access drafted from the resource ledger: who reaches whom, on which ports, for how long."""

    model_config = ConfigDict(extra="forbid")
    source: str = Field(min_length=1, max_length=500)
    destination: str = Field(min_length=1, max_length=500)
    source_subsystem: str | None = Field(default=None, max_length=64)
    destination_subsystem: str | None = Field(default=None, max_length=64)
    ports: str | None = Field(default=None, max_length=300)
    start_time: str | None = Field(default=None, max_length=40)
    end_time: str | None = Field(default=None, max_length=40)
    long_link: str | None = Field(default=None, max_length=20)

    @field_validator("*", mode="before")
    @classmethod
    def text(cls, value):
        # Values may arrive as numbers (long_link: 168) or blank strings.
        if value is None or (isinstance(value, str) and not value.strip()):
            return None
        return str(value).strip() if isinstance(value, (int, float, str)) else value


class LedgerUpload(LedgerDraft):
    request_id: str = Field(min_length=1, max_length=100)
    ticket_id: str = Field(min_length=1, max_length=80)

    @field_validator("ticket_id")
    @classmethod
    def safe(cls, value):
        return safe_ticket(value)


class TicketFields(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    ticket_title: str = Field(default="防火墙策略变更", min_length=1, max_length=200)
    assignee: str = Field(default="", max_length=80)
    due_date: str = Field(default="", max_length=40)
    change_background: str = Field(default="", max_length=2000)
    change_purpose: str = Field(default="", max_length=2000)
    requester: str = Field(default="", max_length=80)
    requester_dept: str = Field(default="", max_length=120)
    technical_reviewer: str = Field(default="", max_length=80)
    reviewer: str = Field(default="", max_length=80)


class TicketRequest(Request):
    fields: TicketFields = Field(default_factory=TicketFields)
