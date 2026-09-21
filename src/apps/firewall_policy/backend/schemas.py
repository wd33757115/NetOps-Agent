import re

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
        if value in {".", ".."} or not re.fullmatch(r"[\w.-]{1,80}", value):
            raise ValueError("工单号只支持文字、数字、短横线、下划线和点")
        return value


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
