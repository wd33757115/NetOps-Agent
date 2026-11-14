"""拓扑绘图的 DSH 会话目录：drawio 编辑模式的最小目录。

鸭子类型满足 DshRuntime 的全部 catalog 触点（definitions/submit_answer/invoke/
prepare/actor_id/session_id/read_roots/模式旗标），但只有 submit_answer 一个
"工具"：无拓扑工具、无资料引用门槛——模型的产出就是新的画布 XML。
"""

from pathlib import Path


class DrawCatalog:
    def __init__(self, actor_id: str, session_id: str):
        self.actor_id = actor_id
        self.session_id = session_id
        self.answer: str | None = None
        self.status: str | None = None
        # 模式旗标：DSH 按这些属性组装系统提示词与工具面
        self.active_diagnostics = False
        self.automation = False
        self.drawio_mode = True
        self.docset_catalog = None
        self.attachments: list = []
        self.skills_dir = None
        self.files: list = []
        self.filters: dict = {}

    def prepare(self, workspace) -> None:
        # DSH 用这个目录作为 SDK 子进程的 cwd，必须真实存在。
        Path(workspace).mkdir(parents=True, exist_ok=True)

    def read_roots(self) -> list:
        return []

    def check(self) -> None:
        # drawio 模式没有引用门槛，每轮授权检查直接通过。
        return None

    def citation_guidance(self) -> str:
        return ""

    def describe_tool(self, name: str, arguments: dict) -> str:
        return name

    def tool_operation(self, name: str, arguments: dict) -> str:
        return "submit"

    def record_read(self, value) -> None:
        return None

    def references(self, answer: str) -> list:
        # 画布编辑没有资料引用；发布前检查返回空引用集。
        return []

    # DSH 触碰的其余旗标/缓存位
    root = None
    prepared_answer = None
    validation_issue = None
    business_evidence: dict = {}

    def definitions(self) -> list:
        # submit_answer 的定义由 DshRuntime 自己注入（application 里统一加
        # answer_gate.DEFINITION），这里再给一份会导致 MCP 工具重名、同步失败。
        return []

    def submit_answer(self, arguments: dict) -> dict:
        self.answer = arguments.get("answer", "")
        self.status = arguments.get("status", "answered")
        return {"ok": True}

    def invoke(self, name: str, arguments: dict) -> dict:
        if name == "submit_answer":
            return self.submit_answer(arguments)
        raise PermissionError("Tool unavailable")
