"""Agent adapters to the document service; identity never comes from tool arguments."""

from pydantic import BaseModel, ConfigDict, Field
from src.platform.tools.registry import Tool
from .schemas import CreateRequest, AssistantRequest, RevisionRequest
from . import service


class ReadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    document_id: str


class GenerateRequest(AssistantRequest):
    document_id: str


class ExportRequest(RevisionRequest):
    document_id: str
    version: int = Field(ge=1)


class OperationRequest(ReadRequest):
    operation_id: str


def create(user, body):
    from .operations import db_session

    with db_session() as session:
        return service.create(session, user.user_id, body)


def read(user, body):
    from .operations import db_session

    with db_session() as session:
        return service.describe(session, service.owned(session, body.document_id, user.user_id))


def generate(user, body):
    from .operations import db_session, dispatch

    with db_session() as session:
        doc = service.owned(session, body.document_id, user.user_id, lock=True)
        request = AssistantRequest.model_validate(body.model_dump(exclude={"document_id"}))
        op = service.submit(session, doc, user.user_id, request)
        result = service.operation_data(op)
    if result["status"] == "queued":
        dispatch(result["id"])
    return result


def export(user, body):
    from .operations import db_session, dispatch

    with db_session() as session:
        doc = service.owned(session, body.document_id, user.user_id, lock=True)
        op = service.submit(
            session, doc, user.user_id, body, kind="export", export_version=body.version
        )
        result = service.operation_data(op)
    if result["status"] == "queued":
        dispatch(result["id"])
    return result


def operation(user, body):
    from .operations import db_session

    with db_session() as session:
        doc = service.owned(session, body.document_id, user.user_id)
        return service.operation_data(service.operation(session, doc, body.operation_id))


TOOLS = [
    Tool("create_draft", "创建公文草稿；request_id 是幂等请求标识", CreateRequest, create, True),
    Tool("read_document", "读取当前用户的公文及版本", ReadRequest, read),
    Tool(
        "submit_assistant",
        "提交生成、改写、审查或答疑任务，建议由用户核对后接受",
        GenerateRequest,
        generate,
        True,
    ),
    Tool("export_document", "提交指定版本的 Word 导出任务", ExportRequest, export, True),
    Tool("get_operation", "查询公文生成或导出任务状态", OperationRequest, operation),
]
