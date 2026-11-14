"""拓扑绘图的 DSH 会话目录：drawio 编辑模式的最小目录。

鸭子类型满足 DshRuntime 的全部 catalog 触点（definitions/submit_answer/invoke/
prepare/actor_id/session_id/read_roots/模式旗标），但只有 submit_answer 一个
"工具"：无拓扑工具、无资料引用门槛——模型的产出就是新的画布 XML。
"""

from src.apps.assistant.backend import answer_gate


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
        return None

    def read_roots(self) -> list:
        return []

    def definitions(self) -> list:
        return [answer_gate.DEFINITION]

    def submit_answer(self, arguments: dict) -> dict:
        self.answer = arguments.get("answer", "")
        self.status = arguments.get("status", "answered")
        return {"ok": True}

    def invoke(self, name: str, arguments: dict) -> dict:
        if name == "submit_answer":
            return self.submit_answer(arguments)
        raise PermissionError("Tool unavailable")
