from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol


@dataclass(frozen=True)
class RunContext:
    run_id: str
    actor_id: str
    session_id: str
    timeout_seconds: float = 90
    max_tool_calls: int = 12
    max_model_rounds: int = 8

    def __post_init__(self):
        if not self.run_id or not self.actor_id or not self.session_id:
            raise ValueError("A server-owned run, actor and session are required")
        if not 0 < self.timeout_seconds <= 300:
            raise ValueError("Invalid run timeout")
        if not 1 <= self.max_tool_calls <= 30 or not 1 <= self.max_model_rounds <= 15:
            raise ValueError("Invalid run budget")


@dataclass(frozen=True)
class AgentEvent:
    run_id: str
    kind: Literal[
        "run_started",
        "status",
        "progress",
        "metrics",
        "text_delta",
        "tool_started",
        "tool_finished",
        "citation",
        "job_submitted",
        "completed",
        "failed",
        "cancelled",
    ]
    data: dict[str, Any] = field(default_factory=dict)


class AgentRuntime(Protocol):
    def start(
        self, context: RunContext, messages: list, tool_catalog
    ) -> AsyncIterator[AgentEvent]: ...

    async def cancel(self, run_id: str, *, actor_id: str) -> bool: ...
