"""Explicit per-turn live collection scope on top of the existing read-only workspace."""

import hashlib
import json
from threading import Lock

from src.apps.device_access.backend import saved, service
from src.platform.tools import registry

from . import evidence
from .workspace import DocumentWorkspace


class DiagnosticWorkspace(DocumentWorkspace):
    active_diagnostics = True

    def __init__(self, actor_id, *, session_ids, run_id, **kwargs):
        super().__init__(actor_id, **kwargs)
        self.session_ids = frozenset(session_ids)
        self.run_id = run_id
        self.calls = {}
        self.failed_sessions = set()
        self.call_lock = Lock()

    def definitions(self):
        definitions = super().definitions()
        scopes = {}
        for key in self.session_ids:
            name, host, port = saved.target(self.actor_id, key)
            scopes[key] = {"name": name, "host": host, "port": port}
        extra = registry.available_tools(actor_id=self.actor_id, names={"device_access__collect"})
        for tool in extra:
            tool["description"] += "。本轮范围：" + json.dumps(scopes, ensure_ascii=False)
            properties = tool["input_schema"]["properties"]
            properties["session_id"]["enum"] = sorted(self.session_ids)
            # Idempotency is issued by the server, never controlled by the model.
            del properties["request_id"]
            tool["input_schema"]["required"].remove("request_id")
        return definitions + extra

    def invoke(self, name, arguments):
        if name != "device_access__collect":
            return super().invoke(name, arguments)
        self.check()
        if (
            set(arguments) != {"session_id", "profile_id"}
            or arguments["session_id"] not in self.session_ids
        ):
            raise PermissionError("Live collection is outside this turn's human-approved scope")
        key = (arguments["session_id"], arguments["profile_id"])
        with self.call_lock:
            if key in self.calls:
                return self.calls[key]
            if key[0] in self.failed_sessions or len(self.calls) >= 2:
                raise PermissionError("Collection budget exhausted or device requires human input")
            self.calls[key] = {
                "data": {"status": "running"},
                "notice": "已有采集请求，不能重复执行；等待原操作结果。",
            }
        marker = service.checkpoint.set(self.check)
        try:
            result = registry.invoke(
                name,
                {
                    **arguments,
                    "request_id": hashlib.sha256(
                        (self.run_id + "|" + "|".join(key)).encode()
                    ).hexdigest(),
                },
                actor_id=self.actor_id,
            )
        except Exception:
            self.failed_sessions.add(key[0])
            self.calls[key] = {
                "data": {"status": "waiting_for_input"},
                "notice": "采集未完成，请人工检查；不能据此推断设备健康。",
            }
            raise
        finally:
            service.checkpoint.reset(marker)
        if result["status"] != "succeeded":
            self.failed_sessions.add(key[0])
        reference = evidence.capture(name, result)
        self.business_evidence[reference["id"]] = reference
        wrapped = {
            "data": result,
            "citation": reference["citation"],
            "retrieved_at": reference["retrieved_at"],
            "notice": "这是有限命令集的现场观测，不代表根因已确认；失败后等待人工，不得自行重试。",
        }
        self.calls[key] = wrapped
        return wrapped
