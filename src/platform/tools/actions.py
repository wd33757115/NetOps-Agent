"""Actions an App offers to Workflow steps.

A step whose skill is ``app:<app-id>/<action>`` runs the action in the Workflow worker, as
the run's user and under that user's App grant. Unlike an agent tool an action may run for
minutes, and it hands back files as bytes: the platform stores them as the step's artifacts,
so an App never signs download links of its own.
"""

from collections.abc import Callable
from dataclasses import dataclass
import re

from pydantic import BaseModel

from src.platform.authorization.client import authorize_worker
from src.platform.kernel.registry import discover, load_symbol

PREFIX = "app:"
_REFERENCE = re.compile(r"app:([a-z][a-z0-9]*(?:-[a-z0-9]+)*)/([a-z][a-z0-9_]*)")


@dataclass(frozen=True)
class Action:
    name: str
    description: str
    input_model: type[BaseModel]
    # handler(user, payload, run_id=...) -> {"success": bool, "message": str, ...,
    #                                        "files": {key: (filename, content_type, bytes)}}
    handler: Callable


def parse(reference: str) -> tuple[str, str] | None:
    """``app:firewall-policy/generate_policy`` -> ("firewall-policy", "generate_policy")."""
    match = _REFERENCE.fullmatch(str(reference or "").strip())
    return (match.group(1), match.group(2)) if match else None


def is_action(reference: str) -> bool:
    return str(reference or "").startswith(PREFIX)


def catalog():
    result = {}
    for manifest in discover().values():
        entry = manifest.data.get("workflow", {}).get("actions")
        for action in load_symbol(entry) if entry else []:
            result[f"{PREFIX}{manifest.id}/{action.name}"] = (manifest.id, action)
    return result


def invoke(reference: str, arguments: dict, *, actor_id: str | None, run_id: str):
    from src.platform.kernel.runtime import invocation

    found = catalog().get(reference)
    if found is None:
        raise LookupError(f"应用动作不存在或应用未安装：{reference}")
    if not actor_id:
        # Without a user there is no App grant to act under.
        raise PermissionError("应用动作需要运行所属的用户；请通过试跑、聊天或带账号的 Webhook 启动")
    app_id, action = found
    with invocation(app_id):
        user = authorize_worker(actor_id, app_id)
        payload = action.input_model.model_validate(arguments)
        return action.handler(user, payload, run_id=run_id)
