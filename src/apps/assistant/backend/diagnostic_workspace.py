"""Live, read-only diagnosis logins scoped by the turn's own topology answers.

A turn may log into a device only after topology__trace_path or topology__locate
returned it in this same turn (a collected device with an identified platform). The
device-access App runs every call through a platform-controlled, audited session;
this workspace adds that scope, the per-turn budgets and the evidence citation.
"""

import ipaddress
from threading import Lock

from src.platform.tools import registry

from . import evidence
from .workspace import DocumentWorkspace

RUN = "device_access__run"
COMPARE = "device_access__compare_config"
BASELINE = "inspection__read_config_section"
SCOPE_TOOLS = frozenset({"topology__trace_path", "topology__locate"})
PLATFORMS = frozenset({"comware", "nxos", "ios", "vrp"})
SERVER_FIELDS = ("platform", "turn_id")
MAX_DEVICES = 8
MAX_COMMANDS = 20


def devices_in(value):
    """Every collected device named anywhere in a topology answer."""
    if isinstance(value, dict):
        own = [value] if value.get("collected") and value.get("address") else []
        return own + [d for item in value.values() for d in devices_in(item)]
    if isinstance(value, list):
        return [d for item in value for d in devices_in(item)]
    return []


class DiagnosticWorkspace(DocumentWorkspace):
    active_diagnostics = True

    def __init__(self, actor_id, *, run_id, **kwargs):
        super().__init__(actor_id, **kwargs)
        self.run_id = run_id
        self.scope = {}  # management address -> identified platform
        self.commands = 0
        self.lock = Lock()

    def definitions(self):
        definitions = super().definitions()
        available = registry.available_tools(actor_id=self.actor_id, names={RUN, COMPARE, BASELINE})
        has_baseline = any(tool["name"] == BASELINE for tool in available)
        extra = [tool for tool in available if tool["name"] == RUN
                 or (tool["name"] == COMPARE and has_baseline)]
        for tool in extra:
            schema = tool["input_schema"]
            for field in SERVER_FIELDS:
                schema["properties"].pop(field, None)
                if field in schema.get("required", []):
                    schema["required"].remove(field)
            tool["description"] += (
                f"。只能登录本轮 topology__trace_path 或 topology__locate 结果中出现的已采集设备，"
                f"每轮最多 {MAX_DEVICES} 台、共 {MAX_COMMANDS} 条命令"
            )
        return definitions + extra

    def widen(self, result):
        with self.lock:
            for device in devices_in(result):
                if len(self.scope) >= MAX_DEVICES:
                    break
                if device.get("platform") in PLATFORMS:
                    self.scope.setdefault(device["address"], device["platform"])

    def invoke(self, name, arguments):
        if name not in {RUN, COMPARE}:
            result = super().invoke(name, arguments)
            if name in SCOPE_TOOLS:
                self.widen(result["data"])
            return result
        self.check()
        allowed = {"host", "commands", "purpose"} if name == RUN else {
            "snapshot_id", "host", "section", "target", "purpose"
        }
        if set(arguments) - allowed:
            raise PermissionError("Platform and turn are filled by the server")
        try:
            host = str(ipaddress.ip_address(str(arguments.get("host", "")).strip()))
        except ValueError:
            raise PermissionError("需要设备管理地址") from None
        command_count = len(arguments.get("commands") or []) if name == RUN else 1
        with self.lock:
            platform = self.scope.get(host)
            if platform is None:
                raise PermissionError("该设备不在本轮路径计算或终端定位的结果中，不能登录")
            if self.commands + command_count > MAX_COMMANDS:
                raise PermissionError("本轮诊断命令预算已用完")
            self.commands += command_count
        result = registry.invoke(
            name,
            {**arguments, "host": host, "platform": platform, "turn_id": self.run_id},
            actor_id=self.actor_id,
        )
        reference = evidence.capture(name, result)
        self.business_evidence[reference["id"]] = reference
        return {
            "data": result,
            "citation": reference["citation"],
            "retrieved_at": reference["retrieved_at"],
            "notice": "本轮只读命令的现场观测；未执行的命令不能作为证据，登录失败不得重试。",
        }
