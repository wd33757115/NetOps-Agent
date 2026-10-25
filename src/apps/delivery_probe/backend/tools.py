from pydantic import BaseModel

from src.platform.tools.registry import Tool

from . import service


class Read(BaseModel):
    run_id: str


TOOLS = [
    Tool(
        "submit",
        "提交一条接入验证记录，异步返回大写文本。",
        service.Submit,
        service.submit,
        mutates=True,
    ),
    Tool(
        "read",
        "读取本人提交的接入验证记录。",
        Read,
        lambda actor, args: service.read(actor, args.run_id),
    ),
]
