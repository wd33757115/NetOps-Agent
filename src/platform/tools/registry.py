"""All adapters share schema validation and the same application authorization."""

from collections.abc import Callable
from dataclasses import dataclass

from pydantic import BaseModel

from src.platform.authorization.client import authorize_worker
from src.platform.kernel.registry import discover, load_symbol


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    input_model: type[BaseModel]
    handler: Callable
    mutates: bool = False
    evidence: bool = False


def catalog():
    result = {}
    for manifest in discover().values():
        entry = manifest.data.get("agent", {}).get("tools")
        for tool in load_symbol(entry) if entry else []:
            name = manifest.id.replace("-", "_") + "__" + tool.name
            if name in result:
                raise ValueError(f"Duplicate tool: {name}")
            result[name] = (manifest.id, tool)
    return result


def invoke(name: str, arguments: dict, *, actor_id: str, read_only: bool = False):
    from src.platform.kernel.runtime import invocation

    app_id, tool = catalog()[name]
    if read_only and tool.mutates:
        raise PermissionError("This run permits read tools only")
    with invocation(app_id):
        user = authorize_worker(actor_id, app_id)
        payload = tool.input_model.model_validate(arguments)
        return tool.handler(user, payload)


def available_tools(
    *,
    actor_id: str,
    read_only: bool = False,
    names: set[str] | None = None,
    evidence_only: bool = False,
):
    """Only this filtered catalog may be sent to a model adapter."""
    from fastapi import HTTPException

    decisions = {}
    result = []
    for name, (app_id, tool) in catalog().items():
        if (read_only and tool.mutates) or (names is not None and name not in names):
            continue
        if evidence_only and not tool.evidence:
            continue
        if app_id not in decisions:
            try:
                authorize_worker(actor_id, app_id)
                decisions[app_id] = True
            except HTTPException as exc:
                if exc.status_code not in (401, 403, 404):
                    raise
                decisions[app_id] = False
        if decisions[app_id]:
            result.append(
                {
                    "name": name,
                    "description": tool.description,
                    "input_schema": tool.input_model.model_json_schema(),
                    "mutates": tool.mutates,
                    "evidence": tool.evidence,
                }
            )
    return result
