from pydantic import BaseModel, ConfigDict

from src.platform.tools.registry import Tool

from . import service
from .schemas import PolicyRequest, TicketRequest


class Read(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run_id: str


class Ticket(TicketRequest):
    run_id: str


TOOLS = [
    Tool(
        "submit_policy",
        "使用应用上传接口返回的 upload_id 提交策略任务；不接受 URL 或本地路径",
        PolicyRequest,
        lambda user, body: service.submit_policy(user.user_id, body),
        True,
    ),
    Tool(
        "read_run",
        "查询策略或工单任务及授权下载入口",
        Read,
        lambda user, body: service.read(user.user_id, body.run_id),
    ),
    Tool(
        "submit_ticket",
        "从服务端保存的策略计划生成工单，只可填写工单头信息",
        Ticket,
        lambda user, body: service.submit_ticket(
            user.user_id,
            body.run_id,
            TicketRequest.model_validate(body.model_dump(exclude={"run_id"})),
        ),
        True,
    ),
    Tool(
        "cancel_run",
        "取消尚未完成的策略或工单任务",
        Read,
        lambda user, body: service.cancel(user.user_id, body.run_id),
        True,
    ),
]
