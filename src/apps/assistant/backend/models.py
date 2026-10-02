"""Package-owned immutable document revisions and bounded chat turns.

Conversations and messages remain in the existing NetOps history tables.
"""

from sqlalchemy import JSON, Column, DateTime, Integer, String, UniqueConstraint
from sqlalchemy.orm import declarative_base

Base = declarative_base()


class Turn(Base):
    __tablename__ = "app_assistant_turns"
    id = Column(String(32), primary_key=True)
    owner_id = Column(String(64), nullable=False)
    conversation_id = Column(String(64), nullable=False, index=True)
    request_id = Column(String(64), nullable=False)
    input_digest = Column(String(64), nullable=False)
    status = Column(String(32), nullable=False)
    active_conversation = Column(String(64), unique=True)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False)
    result_message_id = Column(String(64))
    event_count = Column(Integer, nullable=False, default=0)
    error_code = Column(String(64))
    progress_json = Column(JSON)
    __table_args__ = (UniqueConstraint("owner_id", "request_id", name="uq_assistant_turn_request"),)


class ConversationSettings(Base):
    """Model and reasoning effort chosen for one conversation only."""

    __tablename__ = "app_assistant_conversation_settings"
    conversation_id = Column(String(64), primary_key=True)
    owner_id = Column(String(64), nullable=False)
    model = Column(String(64), nullable=False)
    reasoning_effort = Column(String(16), nullable=False)
    updated_at = Column(DateTime(timezone=True), nullable=False)
