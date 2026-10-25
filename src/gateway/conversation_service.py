# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""
对话服务层 - 处理对话持久化和LLM总结
"""

import json
import uuid
from datetime import datetime, timezone
from typing import List, Optional

from sqlalchemy import desc, func
from sqlalchemy.orm import Session

from src.infrastructure.db.models import Conversation, Message
from src.infrastructure.db.postgres import get_db_session
from src.apps.assistant.backend.evidence import public_references


class ConversationService:
    """
    对话服务 - 提供对话CRUD和LLM总结功能
    """

    def create_conversation(self, title: str = "新对话", user_id: Optional[str] = None, 
                          thread_id: Optional[str] = None) -> dict:
        """创建新对话"""
        with get_db_session() as session:
            conversation = Conversation(
                id=f"conv-{uuid.uuid4().hex[:12]}",
                title=title,
                user_id=user_id,
                thread_id=thread_id,
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc)
            )
            session.add(conversation)
            session.commit()
            session.refresh(conversation)
            return {
                "id": conversation.id,
                "title": conversation.title,
                "user_id": conversation.user_id,
                "thread_id": conversation.thread_id,
                "status": conversation.status,
                "summary": conversation.summary,
                "created_at": conversation.created_at,
                "updated_at": conversation.updated_at
            }

    def get_conversation(self, conversation_id: str, user_id: Optional[str] = None) -> Optional[dict]:
        """获取单个对话；若提供 user_id 则校验归属。"""
        with get_db_session() as session:
            conversation = session.query(Conversation).filter(
                Conversation.id == conversation_id,
                Conversation.status != "document",
                Conversation.is_deleted == False
            ).first()
            if not conversation:
                return None
            if user_id and conversation.user_id != user_id:
                return None
            return {
                "id": conversation.id,
                "title": conversation.title,
                "user_id": conversation.user_id,
                "thread_id": conversation.thread_id,
                "status": conversation.status,
                "summary": conversation.summary,
                "created_at": conversation.created_at,
                "updated_at": conversation.updated_at
            }

    def get_conversation_for_user(
        self,
        conversation_id: str,
        user_id: str,
        *,
        allow_legacy_unowned: bool = False,
    ) -> Optional[dict]:
        """按已绑定的所有者隔离；默认不认领无主历史会话。"""
        conv = self.get_conversation(conversation_id)
        if not conv:
            return None
        owner = conv.get("user_id")
        if owner and owner != user_id:
            return None
        if not owner and allow_legacy_unowned:
            return self.update_conversation(conversation_id, user_id=user_id)
        if not owner:
            return None
        return conv

    def get_conversations(self, user_id: Optional[str] = None, limit: int = 20, 
                         offset: int = 0) -> List[dict]:
        """获取对话列表"""
        with get_db_session() as session:
            query = session.query(Conversation).filter(Conversation.is_deleted == False, Conversation.status != "document")
            if user_id:
                query = query.filter(Conversation.user_id == user_id)
            query = query.order_by(desc(Conversation.updated_at)).limit(limit).offset(offset)
            result = []
            for conv in query.all():
                result.append({
                    "id": conv.id,
                    "title": conv.title,
                    "user_id": conv.user_id,
                    "thread_id": conv.thread_id,
                    "status": conv.status,
                    "summary": conv.summary,
                    "created_at": conv.created_at,
                    "updated_at": conv.updated_at,
                    "message_count": self._get_message_count(session, conv.id)
                })
            return result

    def _get_message_count(self, session: Session, conversation_id: str) -> int:
        """获取对话消息数量（内部方法）"""
        return session.query(func.count(Message.id)).filter(
            Message.conversation_id == conversation_id,
            Message.is_deleted == False
        ).scalar()

    def update_conversation(self, conversation_id: str, **kwargs) -> Optional[dict]:
        """更新对话"""
        with get_db_session() as session:
            conversation = session.query(Conversation).filter(
                Conversation.id == conversation_id,
                Conversation.is_deleted == False
            ).first()
            if not conversation:
                return None
            
            for key, value in kwargs.items():
                if hasattr(conversation, key):
                    setattr(conversation, key, value)
            
            conversation.updated_at = datetime.now(timezone.utc)
            session.commit()
            session.refresh(conversation)
            return {
                "id": conversation.id,
                "title": conversation.title,
                "user_id": conversation.user_id,
                "thread_id": conversation.thread_id,
                "status": conversation.status,
                "summary": conversation.summary,
                "created_at": conversation.created_at,
                "updated_at": conversation.updated_at
            }

    def delete_conversation(self, conversation_id: str) -> bool:
        """删除对话（软删除）"""
        with get_db_session() as session:
            conversation = session.query(Conversation).filter(
                Conversation.id == conversation_id,
                Conversation.is_deleted == False
            ).first()
            if not conversation:
                return False
            
            conversation.is_deleted = True
            conversation.updated_at = datetime.now(timezone.utc)
            
            session.query(Message).filter(
                Message.conversation_id == conversation_id
            ).update({
                Message.is_deleted: True
            })
            
            session.commit()
            return True

    def add_message(self, conversation_id: str, role: str, content: str,
                   agent_type: Optional[str] = None, celery_task_id: Optional[str] = None,
                   download_url: Optional[str] = None, references: Optional[List[dict]] = None) -> dict:
        """添加消息"""
        with get_db_session() as session:
            message = Message(
                id=f"msg-{uuid.uuid4().hex[:12]}",
                conversation_id=conversation_id,
                role=role,
                content=content,
                agent_type=agent_type,
                celery_task_id=celery_task_id,
                download_url=download_url,
                references=json.dumps(references) if references else None,
                created_at=datetime.now(timezone.utc)
            )
            session.add(message)
            
            conversation = session.query(Conversation).filter(
                Conversation.id == conversation_id,
                Conversation.is_deleted == False
            ).first()
            if conversation:
                conversation.updated_at = datetime.now(timezone.utc)
            
            session.commit()
            session.refresh(message)
            return {
                "id": message.id,
                "role": message.role,
                "content": message.content,
                "agent_type": message.agent_type,
                "celery_task_id": message.celery_task_id,
                "download_url": message.download_url,
                "references": public_references(json.loads(message.references)) if message.references else None,
                "created_at": message.created_at
            }

    def get_messages(self, conversation_id: str) -> List[dict]:
        """获取对话的所有消息"""
        with get_db_session() as session:
            messages = session.query(Message).filter(
                Message.conversation_id == conversation_id,
                Message.is_deleted == False
            ).order_by(Message.created_at).all()
            result = []
            for msg in messages:
                result.append({
                    "id": msg.id,
                    "role": msg.role,
                    "content": msg.content,
                    "agent_type": msg.agent_type,
                    "celery_task_id": msg.celery_task_id,
                    "download_url": msg.download_url,
                    "references": public_references(json.loads(msg.references)) if msg.references else None,
                    "created_at": msg.created_at
                })
            return result

    def generate_title(self, conversation_id: str, max_tokens: int = 50) -> str:
        """使用LLM生成对话标题"""
        messages = self.get_messages(conversation_id)
        if not messages:
            return "新对话"
        
        messages_text = "\n".join([f"{m['role']}: {m['content']}" for m in messages])
        
        try:
            from langchain_deepseek import ChatDeepSeek
            from langchain.schema import HumanMessage

            llm = ChatDeepSeek(
                model="deepseek-chat",
                temperature=0.1
            )

            prompt = f"""
请为以下对话生成一个简短的标题（不超过{max_tokens}个字符）：

{messages_text}

标题要求：
1. 简洁明了，概括对话主题
2. 使用中文
3. 不超过{max_tokens}个字符
"""
            
            response = llm([HumanMessage(content=prompt)])
            title = response.content.strip()
            
            if len(title) > max_tokens:
                title = title[:max_tokens]
            
            return title
        except Exception as e:
            first_message = next((m for m in messages if m['role'] == "user"), messages[0])
            title = first_message['content'][:max_tokens]
            return title

    def summarize_conversation(self, conversation_id: str) -> str:
        """使用LLM生成对话总结"""
        messages = self.get_messages(conversation_id)
        if not messages:
            return ""
        
        messages_text = "\n".join([f"{m['role']}: {m['content']}" for m in messages])
        
        try:
            from langchain_deepseek import ChatDeepSeek
            from langchain.schema import HumanMessage

            llm = ChatDeepSeek(
                model="deepseek-chat",
                temperature=0.1
            )

            prompt = f"""
请总结以下对话内容：

{messages_text}

总结要求：
1. 用简洁的语言概括对话的主要内容和结论
2. 使用中文
3. 不超过200字
"""
            
            response = llm([HumanMessage(content=prompt)])
            return response.content.strip()
        except Exception as e:
            return ""

    def get_conversation_with_messages(self, conversation_id: str):
        """获取对话及其所有消息"""
        conversation = self.get_conversation(conversation_id)
        if not conversation:
            return None
        
        messages = self.get_messages(conversation_id)
        return {
            "conversation": conversation,
            "messages": messages
        }

    def get_conversations_count(self, user_id: Optional[str] = None) -> int:
        """获取对话数量"""
        with get_db_session() as session:
            query = session.query(func.count(Conversation.id)).filter(
                Conversation.is_deleted == False
            )
            if user_id:
                query = query.filter(Conversation.user_id == user_id)
            return query.scalar()


def get_conversation_service() -> ConversationService:
    """获取对话服务实例"""
    return ConversationService()
