"""Model and reasoning effort for one conversation, like ChatGPT's per-chat picker.

A conversation without a choice runs the deployment's configured model with
thinking off. A choice applies to that conversation only and to its next turns;
the server reads it when a turn starts, so a question never carries its own.
"""

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from src.platform.agent_runtime.base import REASONING_EFFORTS

from . import history
from .models import ConversationSettings
from .repository import now

# DeepSeek models the runtime's provider route serves (dsh-llm-deepseek catalog).
MODELS = {"deepseek-v4-flash": "V4 Flash", "deepseek-v4-pro": "V4 Pro"}
EFFORTS = {"off": "关", "low": "低", "high": "高", "max": "最高"}
assert tuple(EFFORTS) == REASONING_EFFORTS


class Choice(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model: str = Field(min_length=1, max_length=64)
    reasoning_effort: str = Field(min_length=1, max_length=16)


def _default_model():
    from src.common.config import get_settings

    return get_settings().LLM_MODEL


def options():
    """What the picker offers; the default is what an untouched conversation runs."""
    models = {_default_model(): _default_model(), **MODELS}
    return {
        "models": [{"id": key, "label": label} for key, label in models.items()],
        "reasoning_efforts": [{"id": key, "label": label} for key, label in EFFORTS.items()],
        "default": {"model": _default_model(), "reasoning_effort": "off"},
    }


def _view(row):
    if row is None:
        return {"model": _default_model(), "reasoning_effort": "off", "chosen": False}
    return {"model": row.model, "reasoning_effort": row.reasoning_effort, "chosen": True}


def get(actor_id, conversation_id):
    with history.sessions() as session:
        history.owned(session, actor_id, conversation_id)
        return _view(session.get(ConversationSettings, conversation_id))


def save(actor_id, conversation_id, choice: Choice):
    allowed = {item["id"] for item in options()["models"]}
    if choice.model not in allowed or choice.reasoning_effort not in EFFORTS:
        raise ValueError("Unsupported model or reasoning effort")
    with history.sessions() as session:
        history.owned(session, actor_id, conversation_id)
        row = session.get(ConversationSettings, conversation_id) or ConversationSettings(
            conversation_id=conversation_id, owner_id=str(actor_id)
        )
        row.model, row.reasoning_effort, row.updated_at = (
            choice.model, choice.reasoning_effort, now()
        )
        session.add(row)
        session.flush()
        return _view(row)


def for_run(actor_id, conversation_id):
    """(model, reasoning effort) a turn of this conversation runs with."""
    with history.sessions() as session:
        row = session.scalar(
            select(ConversationSettings).where(
                ConversationSettings.conversation_id == conversation_id,
                ConversationSettings.owner_id == str(actor_id),
            )
        )
        view = _view(row)
    return view["model"], view["reasoning_effort"]
