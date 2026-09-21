"""Assistant views over existing NetOps conversation/message tables."""

import json
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import select

from src.infrastructure.db.models import Conversation, Message

from .repository import now, sessions


def owned(session, actor_id, conversation_id):
    row = session.scalar(
        select(Conversation).where(
            Conversation.id == conversation_id,
            Conversation.user_id == actor_id,
            Conversation.status.in_(["assistant", "active"]),
            Conversation.is_deleted.is_(False),
        )
    )
    if row is None:
        raise HTTPException(404, "会话不存在")
    return row


def create(actor_id):
    with sessions() as session:
        row = Conversation(
            id="conv-" + uuid4().hex,
            title="新问答",
            user_id=actor_id,
            thread_id=uuid4().hex,
            status="assistant",
            created_at=now(),
            updated_at=now(),
            is_deleted=False,
        )
        session.add(row)
        return {"id": row.id, "title": row.title}


def listing(actor_id):
    with sessions() as session:
        return [
            {"id": c.id, "title": c.title}
            for c in session.scalars(
                select(Conversation)
                .where(
                    Conversation.user_id == actor_id,
                    Conversation.status == "assistant",
                    Conversation.is_deleted.is_(False),
                )
                .order_by(Conversation.updated_at.desc())
                .limit(100)
            )
        ]


def message_view(row):
    references = json.loads(row.references) if isinstance(row.references, str) else row.references
    return {
        "id": row.id,
        "role": row.role,
        "content": row.content,
        "references": references or [],
        "created_at": row.created_at,
    }


def messages(actor_id, conversation_id):
    with sessions() as session:
        owned(session, actor_id, conversation_id)
        return [
            message_view(m)
            for m in session.scalars(
                select(Message)
                .where(Message.conversation_id == conversation_id, Message.is_deleted.is_(False))
                .order_by(Message.created_at, Message.id)
            )
        ]


def add(session, conversation_id, role, text, references=None):
    row = Message(
        id="msg-" + uuid4().hex,
        conversation_id=conversation_id,
        role=role,
        content=text,
        agent_type="assistant",
        references=json.dumps(references, ensure_ascii=False) if references else None,
        created_at=now(),
        is_deleted=False,
    )
    session.add(row)
    return row.id
