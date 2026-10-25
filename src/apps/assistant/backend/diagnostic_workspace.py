"""Live, read-only diagnosis logins scoped by the turn's own topology answers.

A turn may log into a device only after topology__trace_path or topology__locate
returned it in this same turn (a collected device with an identified platform). The
device-access App runs every call through a platform-controlled, audited session;
this workspace adds that scope and the evidence citation. A turn has no device or command
count: it is limited by the tokens it spends (the run's budget).
"""

import ipaddress
from threading import Lock

from src.platform.clock import in_beijing
from src.platform.tools import registry

from src.platform.authorization.client import authorize_worker

from . import automation, evidence
from .workspace import DocumentWorkspace

RUN = "device_access__run"
COMPARE = "device_access__compare_baseline"
ROWS = "device_access__compare_rows"
BASELINE = "inspection__read_baseline"
SCOPE_TOOLS = frozenset({"topology__trace_path", "topology__locate"})
PLATFORMS = frozenset({"comware", "nxos", "ios", "vrp"})
SERVER_FIELDS = ("platform", "platforms", "turn_id")


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
        self.automation = set()  # Skill/Workflow tools offered this turn
        self.skills_dir = None  # the turn's DSH skill root, when it has one
        self.lock = Lock()

    def definitions(self):
        definitions = super().definitions()
        available = registry.available_tools(
            actor_id=self.actor_id, names={RUN, COMPARE, ROWS, BASELINE, *automation.NAMES}
        )
        offered = self.automation_tools(
            [tool for tool in available if tool["name"] in automation.NAMES]
        )
        available = [tool for tool in available if tool["name"] not in automation.NAMES]
        has_baseline = any(tool["name"] == BASELINE for tool in available)
        extra = [tool for tool in available if tool["name"] == RUN
                 or (tool["name"] in {COMPARE, ROWS} and has_baseline)]
        for tool in extra:
            schema = tool["input_schema"]
            for field in SERVER_FIELDS:
                schema["properties"].pop(field, None)
                if field in schema.get("required", []):
                    schema["required"].remove(field)
            scope = "本轮 topology__trace_path 或 topology__locate 结果中出现的"
            tool["description"] += (
                f"。只查{scope}设备，不登录设备，不占命令预算"
                if tool["name"] == ROWS
                else f"。只能登录{scope}已采集设备；按设备平台写命令（comware/vrp 用 display，"
                "ios/nxos 用 show）"
            )
        return definitions + extra + offered

    def automation_tools(self, tools):
        """Skill and Workflow tools for accounts that may execute (admin, operator).

        What the account may run is written as a DSH skill root; DSH lists it in the
        session's skill catalog and its ``skill`` tool loads one before the model acts.
        """
        self.automation, self.skills_dir = set(), None
        if not tools or not automation.can_execute(authorize_worker(self.actor_id, "assistant")):
            return []
        skills = automation.chat_skills(self.actor_id)
        workflows = automation.chat_workflows()
        if not skills and not workflows:
            return []
        self.skills_dir = automation.write_skill_catalog(self.root.parent / "skills", skills, workflows)
        offered = []
        for tool in tools:
            properties = tool["input_schema"]["properties"]
            if tool["name"] == automation.RUN_SKILL:
                if not skills:
                    continue
                properties["skill"]["enum"] = [skill["name"] for skill in skills]
            elif tool["name"] == automation.START_WORKFLOW:
                if not workflows:
                    continue
                properties["workflow"]["enum"] = [flow["name"] for flow in workflows]
                # Attachments come from the conversation, filled in by the server.
                properties.pop("attachments", None)
                tool["input_schema"].get("$defs", {}).pop("Attachment", None)
            elif not workflows:
                continue
            tool["description"] += "；先用 skill 工具加载对应技能的说明，按说明填写参数"
            offered.append(tool)
        self.automation = {tool["name"] for tool in offered}
        return offered

    def widen(self, result):
        with self.lock:
            for device in devices_in(result):
                if device.get("platform") in PLATFORMS:
                    self.scope.setdefault(device["address"], device["platform"])

    def invoke(self, name, arguments):
        if name == ROWS:
            return self.rows(arguments)
        if name in automation.NAMES:
            return self.automate(name, arguments)
        if name not in {RUN, COMPARE}:
            result = super().invoke(name, arguments)
            if name in SCOPE_TOOLS:
                self.widen(result["data"])
            return result
        self.check()
        allowed = {"host", "commands", "purpose"} if name == RUN else {"hosts", "purpose"}
        if set(arguments) - allowed:
            raise PermissionError("Platform and turn are filled by the server")
        requested = [arguments.get("host", "")] if name == RUN else arguments.get("hosts") or []
        try:
            hosts = [str(ipaddress.ip_address(str(host).strip())) for host in requested]
            hosts = list(dict.fromkeys(hosts))
        except ValueError:
            raise PermissionError("需要设备管理地址") from None
        if not hosts:
            raise PermissionError("需要设备管理地址")
        with self.lock:
            outside = [host for host in hosts if host not in self.scope]
            if outside:
                raise PermissionError(
                    f"{'、'.join(outside)} 不在本轮路径计算或终端定位的结果中，不能登录"
                )
            platforms = {host: self.scope[host] for host in hosts}
        server = (
            {"host": hosts[0], "platform": platforms[hosts[0]]}
            if name == RUN
            else {"hosts": hosts, "platforms": platforms}
        )
        result = in_beijing(
            registry.invoke(
                name, {**arguments, **server, "turn_id": self.run_id}, actor_id=self.actor_id
            )
        )
        reference = evidence.capture(name, result)
        self.business_evidence[reference["id"]] = reference
        return {
            "data": result,
            "citation": reference["citation"],
            "retrieved_at": reference["retrieved_at"],
            "notice": "本轮只读命令的现场观测；未执行的命令不能作为证据，登录失败不得重试。",
        }

    def automate(self, name, arguments):
        """Run a Skill or a Workflow for the user; the answer cites what it returned."""
        self.check()
        if name not in self.automation:
            raise PermissionError("Tool is outside this turn's contract")
        if name == automation.START_WORKFLOW:
            arguments = {
                **{key: value for key, value in arguments.items() if key != "attachments"},
                "attachments": [
                    {"file_id": item["file_id"], "name": item["name"], "current": item.get("current", False)}
                    for item in self.attachments if not item.get("unavailable")
                ],
            }
        result = in_beijing(registry.invoke(name, arguments, actor_id=self.actor_id))
        reference = evidence.capture(name, result)
        self.business_evidence[reference["id"]] = reference
        return {
            "data": result,
            "citation": reference["citation"],
            "retrieved_at": reference["retrieved_at"],
            "notice": "这是本轮按用户要求执行的结果；下载链接原样提供给用户。",
        }

    def rows(self, arguments):
        """Look rows up in this turn's reads of a scoped device; no login."""
        self.check()
        if set(arguments) - {"host", "command", "match", "limit"}:
            raise PermissionError("Turn is filled by the server")
        try:
            host = str(ipaddress.ip_address(str(arguments.get("host", "")).strip()))
        except ValueError:
            raise PermissionError("需要设备管理地址") from None
        with self.lock:
            if host not in self.scope:
                raise PermissionError(f"{host} 不在本轮路径计算或终端定位的结果中")
        result = in_beijing(
            registry.invoke(
                ROWS, {**arguments, "host": host, "turn_id": self.run_id}, actor_id=self.actor_id
            )
        )
        reference = evidence.capture(ROWS, result)
        self.business_evidence[reference["id"]] = reference
        return {
            "data": result,
            "citation": reference["citation"],
            "retrieved_at": reference["retrieved_at"],
            "notice": "按关键词查到的基线与本轮已读取的现场内容；没有再次登录设备。",
        }
