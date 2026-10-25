from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

# DeepSeek's reasoning levels; "off" keeps thinking disabled for the run.
REASONING_EFFORTS = ("off", "low", "high", "max")


@dataclass(frozen=True)
class RunContext:
    run_id: str
    actor_id: str
    session_id: str
    timeout_seconds: float = 90
    max_tool_calls: int | None = 12
    max_model_rounds: int | None = 8
    # Prompt plus completion tokens over every round of the run; None leaves the run to
    # the round and tool-call limits, which a token-limited run may set to None.
    max_total_tokens: int | None = None
    # Chosen per conversation; None is the deployment's configured model.
    model: str | None = None
    reasoning_effort: str = "off"

    def __post_init__(self):
        if not self.run_id or not self.actor_id or not self.session_id:
            raise ValueError("A server-owned run, actor and session are required")
        if self.reasoning_effort not in REASONING_EFFORTS:
            raise ValueError("Invalid reasoning effort")
        if not 0 < self.timeout_seconds <= 300:
            raise ValueError("Invalid run timeout")
        if self.max_tool_calls is not None and not 1 <= self.max_tool_calls <= 30:
            raise ValueError("Invalid run budget")
        if self.max_model_rounds is not None and not 1 <= self.max_model_rounds <= 15:
            raise ValueError("Invalid run budget")
        if self.max_total_tokens is not None and not 10_000 <= self.max_total_tokens <= 2_000_000:
            raise ValueError("Invalid run budget")
        if self.max_model_rounds is None and self.max_total_tokens is None:
            raise ValueError("A run needs a round or a token limit")


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
