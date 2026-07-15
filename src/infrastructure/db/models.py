# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import declarative_base

Base = declarative_base()


class TaskStatus(Base):
    __tablename__ = "netops_task_status"

    id = Column(String(64), primary_key=True, index=True)
    task_id = Column(String(64), index=True, comment="Celery Task ID")
    thread_id = Column(String(64), index=True, comment="LangGraph Thread ID")
    user_id = Column(String(64), index=True, nullable=True)
    source = Column(String(32), default="chat", comment="chat / itsm_webhook")
    status = Column(String(32), index=True, comment="pending / processing / completed / failed")
    agent_type = Column(String(32), comment="knowledge_qa / script_executor")
    query = Column(Text, comment="原始查询")
    parameters = Column(JSON, nullable=True)
    result = Column(Text, nullable=True)
    file_url = Column(String(512), nullable=True)
    error_message = Column(Text, nullable=True)
    itsm_callback_url = Column(String(512), nullable=True)
    itsm_callback_status = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
    is_deleted = Column(Boolean, default=False)


class ITSMEvent(Base):
    __tablename__ = "netops_itsm_events"

    id = Column(String(64), primary_key=True, index=True)
    event_id = Column(String(128), unique=True, index=True)
    event_type = Column(String(64), index=True)
    source_system = Column(String(128))
    title = Column(String(512))
    description = Column(Text)
    priority = Column(String(32), default="medium")
    status = Column(String(32), default="new")
    assignee = Column(String(128), nullable=True)
    raw_payload = Column(JSON)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    processed_at = Column(DateTime, nullable=True)
    result_task_id = Column(String(64), nullable=True)


class KnowledgeBaseIndex(Base):
    __tablename__ = "netops_knowledge_index"

    id = Column(Integer, primary_key=True, autoincrement=True)
    file_name = Column(String(512), index=True)
    file_path = Column(String(1024))
    file_hash = Column(String(128), index=True)
    doc_type = Column(String(64), comment="sop / configuration / troubleshooting")
    meta_info = Column(JSON)
    vector_ids = Column(JSON, comment="关联向量库ID列表")
    is_indexed = Column(Boolean, default=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class Conversation(Base):
    __tablename__ = "netops_conversations"

    id = Column(String(64), primary_key=True, index=True)
    title = Column(String(512), comment="对话标题（由LLM生成）")
    user_id = Column(String(64), index=True, nullable=True, comment="用户ID")
    thread_id = Column(String(64), index=True, nullable=True, comment="LangGraph Thread ID")
    status = Column(String(32), default="active", comment="active / archived")
    summary = Column(Text, nullable=True, comment="对话总结")
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
    is_deleted = Column(Boolean, default=False)

    __table_args__ = ({"comment": "对话会话表 - 存储用户与Agent的对话历史"},)


class UserSession(Base):
    """用户登录会话（PostgreSQL，与 Django User.id 关联）。"""

    __tablename__ = "netops_user_sessions"

    id = Column(String(64), primary_key=True, index=True)
    user_id = Column(String(64), index=True, nullable=False)
    username = Column(String(128), nullable=False)
    role = Column(String(32), default="operator", comment="admin / operator / viewer")
    thread_prefix = Column(String(64), nullable=True, comment="LangGraph 用户级 thread 前缀")
    refresh_jti = Column(String(64), nullable=True, index=True)
    ip_address = Column(String(64), nullable=True)
    user_agent = Column(String(512), nullable=True)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    last_seen_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    revoked_at = Column(DateTime, nullable=True)


class AuditLogRecord(Base):
    """操作审计日志（登录、Skill 执行、Supervisor 规划等）。"""

    __tablename__ = "netops_audit_logs"

    id = Column(String(64), primary_key=True, index=True)
    idempotency_key = Column(String(256), nullable=True)
    user_id = Column(String(64), index=True, nullable=True)
    username = Column(String(128), nullable=True)
    action = Column(String(64), index=True, comment="login / logout / chat / skill_execute / ...")
    resource_type = Column(String(64), nullable=True)
    resource_id = Column(String(128), nullable=True)
    detail = Column(JSON, nullable=True)
    ip_address = Column(String(64), nullable=True)
    status = Column(String(32), default="success")
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        Index(
            "uq_netops_audit_logs_idempotency_key",
            "idempotency_key",
            unique=True,
        ),
    )


class Message(Base):
    __tablename__ = "netops_messages"

    id = Column(String(64), primary_key=True, index=True)
    conversation_id = Column(String(64), index=True, comment="关联的对话ID")
    role = Column(String(32), comment="user / assistant")
    content = Column(Text, comment="消息内容")
    agent_type = Column(String(64), nullable=True, comment="处理的Agent类型")
    celery_task_id = Column(String(64), nullable=True, comment="异步任务ID")
    download_url = Column(String(512), nullable=True, comment="下载链接")
    references = Column(JSON, nullable=True, comment="知识库引用")
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    is_deleted = Column(Boolean, default=False)

    __table_args__ = ({"comment": "消息表 - 存储单条对话消息"},)


class Team(Base):
    """团队（共享网盘归属）。"""

    __tablename__ = "netops_teams"

    id = Column(String(64), primary_key=True, index=True)
    name = Column(String(128), nullable=False)
    description = Column(Text, nullable=True)
    created_by = Column(String(64), index=True, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
    is_deleted = Column(Boolean, default=False)


class WorkflowRun(Base):
    """多步 Workflow 运行实例（ITSM 变更闭环等）。"""

    __tablename__ = "netops_workflow_runs"

    id = Column(String(64), primary_key=True, index=True)
    template_name = Column(String(64), index=True, nullable=False)
    ticket_id = Column(String(128), index=True, nullable=True)
    source = Column(String(32), default="chat", comment="chat / itsm_webhook")
    user_id = Column(String(64), index=True, nullable=True)
    thread_id = Column(String(64), index=True, nullable=True)
    status = Column(String(32), default="pending", index=True)
    context = Column(JSON, nullable=True)
    current_step_index = Column(Integer, default=0)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
    completed_at = Column(DateTime, nullable=True)


class PatrolBatchSummaryProjection(Base):
    """Rebuildable materialized patrol batch report."""

    __tablename__ = "netops_patrol_batch_summary_projections"

    patrol_batch_id = Column(String(128), primary_key=True)
    source_digest = Column(String(64), nullable=False, index=True)
    status = Column(String(32), nullable=False, index=True)
    run_count = Column(Integer, nullable=False, default=0)
    summary_json = Column(JSON, nullable=False)
    markdown_text = Column(Text, nullable=False)
    object_key = Column(String(512), nullable=True)
    generated_at = Column(DateTime(timezone=True), nullable=False)
    updated_at = Column(DateTime(timezone=True), nullable=False)

    def to_dict(self):
        return {column.name: getattr(self, column.name) for column in self.__table__.columns}


class PatrolBatchRecord(Base):
    """Durable launch-time manifest for a patrol batch."""

    __tablename__ = "netops_patrol_batches"

    patrol_batch_id = Column(String(128), primary_key=True)
    workflow_name = Column(String(128), nullable=False)
    launch_mode = Column(String(32), nullable=False)
    patrol_mode = Column(String(32), nullable=False)
    ticket_id = Column(String(128), nullable=True, index=True)
    target_type = Column(String(32), nullable=False, index=True)
    target_label = Column(String(256), nullable=False)
    selector_json = Column(JSON, nullable=False)
    target_count = Column(Integer, nullable=False, default=0)
    launched_count = Column(Integer, nullable=False, default=0)
    skipped_count = Column(Integer, nullable=False, default=0)
    archived = Column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
        index=True,
    )
    archived_at = Column(DateTime(timezone=True), nullable=True)
    archive_reason = Column(Text, nullable=True)
    archived_by = Column(String(128), nullable=True)
    keep_until = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, index=True)
    updated_at = Column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        Index(
            "ix_netops_patrol_batches_archive_created_id",
            "archived",
            "created_at",
            "patrol_batch_id",
        ),
    )

    def to_dict(self):
        return {column.name: getattr(self, column.name) for column in self.__table__.columns}


class PatrolBatchDeviceRecord(Base):
    """One launch-time target per device, suitable for server-side pagination."""

    __tablename__ = "netops_patrol_batch_devices"

    id = Column(String(64), primary_key=True)
    patrol_batch_id = Column(
        String(128),
        ForeignKey("netops_patrol_batches.patrol_batch_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    device_id = Column(String(256), nullable=False, index=True)
    device_name = Column(String(256), nullable=True, index=True)
    management_ip = Column(String(64), nullable=True)
    selection_status = Column(String(32), nullable=False, index=True)
    preflight_status = Column(String(32), nullable=False, index=True)
    failure_code = Column(String(128), nullable=True, index=True)
    error_message = Column(Text, nullable=True)
    workflow_run_id = Column(String(64), nullable=True, index=True)
    created_at = Column(DateTime(timezone=True), nullable=False)
    updated_at = Column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        Index(
            "uq_netops_patrol_batch_device",
            "patrol_batch_id",
            "device_id",
            unique=True,
        ),
    )

    def to_dict(self):
        return {column.name: getattr(self, column.name) for column in self.__table__.columns}


class WorkflowPluginMetadata(Base):
    """Workflow 插件治理元数据（文件仍为运行时真相源）。"""

    __tablename__ = "netops_workflow_plugins"

    name = Column(String(128), primary_key=True, index=True, comment="与 WORKFLOW.name 一致")
    category = Column(String(64), default="itsm", index=True)
    description = Column(Text, nullable=True)
    plugin_path = Column(String(512), nullable=True)
    status = Column(
        String(32),
        default="draft",
        index=True,
        comment="draft / review / published / archived",
    )
    current_version = Column(Integer, default=0)
    created_by = Column(String(64), index=True, nullable=True)
    updated_by = Column(String(64), nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
    published_at = Column(DateTime, nullable=True)


class WorkflowPluginVersion(Base):
    """Workflow 插件发布版本快照。"""

    __tablename__ = "netops_workflow_plugin_versions"

    id = Column(String(64), primary_key=True, index=True)
    plugin_name = Column(String(128), index=True, nullable=False)
    version = Column(Integer, nullable=False)
    files = Column(JSON, nullable=False, comment="WORKFLOW.yaml 等文件快照")
    status = Column(String(32), default="published")
    change_summary = Column(Text, nullable=True)
    created_by = Column(String(64), nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (Index("uq_workflow_plugin_version", "plugin_name", "version", unique=True),)


class WorkflowMarketTemplate(Base):
    """模板市场条目（内置 + 用户发布）。"""

    __tablename__ = "netops_workflow_market_templates"

    id = Column(String(64), primary_key=True, index=True)
    title = Column(String(256), nullable=False)
    description = Column(Text, nullable=True)
    category = Column(String(64), default="itsm", index=True)
    tags = Column(JSON, nullable=True)
    files = Column(JSON, nullable=False)
    source_plugin_name = Column(String(128), nullable=True, index=True)
    featured = Column(Boolean, default=False)
    use_count = Column(Integer, default=0)
    is_public = Column(Boolean, default=True)
    created_by = Column(String(64), nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class WorkflowStepRecord(Base):
    """Workflow 单步执行记录。"""

    __tablename__ = "netops_workflow_steps"

    id = Column(String(64), primary_key=True, index=True)
    run_id = Column(String(64), index=True, nullable=False)
    step_index = Column(Integer, default=0)
    step_name = Column(String(64), nullable=False)
    skill_name = Column(String(128), nullable=False)
    celery_task_id = Column(String(64), index=True, nullable=True)
    status = Column(String(32), default="pending", index=True)
    input_artifacts = Column(JSON, nullable=True)
    output_artifacts = Column(JSON, nullable=True)
    result = Column(JSON, nullable=True)
    error_message = Column(Text, nullable=True)
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class Notification(Base):
    """站内通知。"""

    __tablename__ = "netops_notifications"

    id = Column(String(64), primary_key=True, index=True)
    idempotency_key = Column(String(256), nullable=True)
    user_id = Column(String(64), index=True, nullable=False)
    title = Column(String(256), nullable=False)
    body = Column(Text, nullable=True)
    level = Column(String(32), default="info")
    payload = Column(JSON, nullable=True)
    workflow_run_id = Column(String(64), index=True, nullable=True)
    thread_id = Column(String(64), index=True, nullable=True)
    read_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        Index(
            "uq_netops_notifications_idempotency_key",
            "idempotency_key",
            unique=True,
        ),
    )


class TeamMember(Base):
    """团队成员与团队内角色。"""

    __tablename__ = "netops_team_members"

    id = Column(String(64), primary_key=True, index=True)
    team_id = Column(String(64), index=True, nullable=False)
    user_id = Column(String(64), index=True, nullable=False)
    role = Column(String(32), default="member", comment="owner / member / viewer")
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    is_deleted = Column(Boolean, default=False)


class StorageFolder(Base):
    """虚拟目录（DB 维护层级，MinIO 用 object_key 前缀映射）。"""

    __tablename__ = "netops_storage_folders"

    id = Column(String(64), primary_key=True, index=True)
    name = Column(String(256), nullable=False)
    parent_id = Column(String(64), index=True, nullable=True)
    owner_id = Column(String(64), index=True, nullable=True, comment="个人目录归属用户")
    team_id = Column(String(64), index=True, nullable=True, comment="团队共享目录归属团队")
    visibility = Column(String(32), default="private", comment="private / shared")
    path_cache = Column(String(1024), nullable=True, comment="物化路径缓存")
    created_by = Column(String(64), nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
    is_deleted = Column(Boolean, default=False)

    __table_args__ = (
        Index(
            "uq_storage_folder_private_root",
            "owner_id",
            unique=True,
            postgresql_where=text(
                "parent_id IS NULL AND visibility = 'private' AND is_deleted = false"
            ),
        ),
        Index(
            "uq_storage_folder_shared_root",
            "team_id",
            unique=True,
            postgresql_where=text(
                "parent_id IS NULL AND visibility = 'shared' AND is_deleted = false"
            ),
        ),
    )


class FileMetadata(Base):
    """文件元数据（对象存储在 MinIO）。"""

    __tablename__ = "netops_file_metadata"

    id = Column(String(64), primary_key=True, index=True)
    name = Column(String(512), nullable=False)
    folder_id = Column(String(64), index=True, nullable=True)
    object_key = Column(String(1024), nullable=False, unique=True, index=True)
    owner_id = Column(String(64), index=True, nullable=True)
    team_id = Column(String(64), index=True, nullable=True)
    visibility = Column(String(32), default="private", comment="private / shared")
    content_type = Column(String(128), nullable=True)
    size_bytes = Column(Integer, default=0)
    etag = Column(String(128), nullable=True)
    status = Column(String(32), default="active", comment="pending / active")
    created_by = Column(String(64), nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
    is_deleted = Column(Boolean, default=False)


class SkillCatalogRecord(Base):
    """Skill Catalog 索引（Phase 2：分级路由 + 预计算 Embedding）。"""

    __tablename__ = "netops_skill_catalog"

    skill_name = Column(String(128), primary_key=True, index=True)
    version = Column(String(32), default="1.0.0")
    description = Column(Text, default="")
    category = Column(String(64), index=True, default="general")
    domain = Column(String(64), index=True, default="default")
    tags = Column(JSON, nullable=True)
    triggers = Column(JSON, nullable=True)
    enabled = Column(Boolean, default=True, index=True)
    hidden = Column(Boolean, default=False)
    deprecated = Column(Boolean, default=False, index=True)
    min_permission_level = Column(String(32), default="user")
    celery_queue = Column(String(128), nullable=True)
    skill_path = Column(String(1024), nullable=True)
    content_hash = Column(String(64), index=True, nullable=True)
    embedding_model = Column(String(128), nullable=True)
    embedding_vector = Column(JSON, nullable=True)
    indexed_at = Column(DateTime, nullable=True)
    rollout_status = Column(
        String(32), default="stable", index=True, comment="draft/canary/stable/deprecated"
    )
    enabled_ratio = Column(Integer, default=100, comment="灰度比例 0-100")
    min_platform_version = Column(String(32), nullable=True)
    updated_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        Index("ix_skill_catalog_domain_enabled", "domain", "enabled"),
        {"comment": "Skill Catalog - 路由索引与 Embedding"},
    )


class SkillExecutionRecord(Base):
    """Skill 执行标准结果持久化（Phase 0）。"""

    __tablename__ = "netops_skill_executions"

    execution_id = Column(String(64), primary_key=True, index=True)
    skill_name = Column(String(128), index=True, nullable=False)
    skill_version = Column(String(32), default="0.0.0")
    status = Column(String(32), index=True, nullable=False)
    message = Column(Text, default="")
    input_params = Column(JSON, nullable=True)
    output = Column(JSON, nullable=True)
    artifacts = Column(JSON, nullable=True)
    exec_metadata = Column(JSON, nullable=True)
    error_info = Column(JSON, nullable=True)
    context = Column(JSON, nullable=True)
    thread_id = Column(String(64), index=True, nullable=True)
    message_id = Column(String(64), index=True, nullable=True)
    user_id = Column(String(64), index=True, nullable=True)
    ticket_id = Column(String(64), index=True, nullable=True)
    source = Column(String(32), default="celery")
    executed_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        Index("ix_skill_exec_thread_message", "thread_id", "message_id"),
        Index("ix_skill_exec_executed_at", "executed_at"),
        {"comment": "Skill 执行记录 - SkillExecutionResult v1"},
    )


class SkillExecutionArchiveLog(Base):
    """Skill 执行记录归档日志。"""

    __tablename__ = "netops_skill_execution_archives"

    id = Column(String(64), primary_key=True, index=True)
    object_key = Column(String(512), nullable=False)
    record_count = Column(Integer, default=0)
    before_date = Column(DateTime, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class NetworkDevice(Base):
    """Authoritative network-device inventory record."""

    __tablename__ = "netops_devices"

    id = Column(String(64), primary_key=True)
    legacy_device_id = Column(Integer, nullable=True)
    device_name = Column(String(256), nullable=False, index=True)
    management_ip = Column(String(64), nullable=False, index=True)
    username = Column(String(256), nullable=True)
    credential_ref = Column(String(512), nullable=True)
    vendor = Column(String(64), nullable=True, index=True)
    model = Column(String(128), nullable=True, index=True)
    family = Column(String(128), nullable=True, index=True)
    platform = Column(String(128), nullable=True, index=True)
    device_type = Column(String(128), nullable=True, index=True)
    os_version = Column(String(128), nullable=True)
    serial_number = Column(String(256), nullable=True)
    site = Column(String(256), nullable=True, index=True)
    role = Column(String(128), nullable=True, index=True)
    status = Column(String(32), nullable=False, default="active", index=True)
    output_path = Column(String(1024), nullable=True)
    command_profile = Column(String(512), nullable=True)
    attributes_json = Column(JSON, nullable=True)
    last_connection_status = Column(String(32), nullable=True, index=True)
    last_connection_checked_at = Column(DateTime, nullable=True)
    last_connection_failure_code = Column(String(128), nullable=True)
    last_connection_error = Column(Text, nullable=True)
    last_connection_latency_ms = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        Index(
            "uq_netops_device_name_ip",
            "device_name",
            "management_ip",
            unique=True,
        ),
    )

    def to_dict(self):
        return {column.name: getattr(self, column.name) for column in self.__table__.columns}


class NetworkDeviceTombstone(Base):
    """Immutable audit snapshot written before a device asset is deleted."""

    __tablename__ = "netops_device_tombstones"

    id = Column(String(64), primary_key=True)
    device_id = Column(String(64), nullable=False, index=True)
    device_name = Column(String(256), nullable=False, index=True)
    management_ip = Column(String(64), nullable=False, index=True)
    vendor = Column(String(64), nullable=True, index=True)
    model = Column(String(128), nullable=True, index=True)
    family = Column(String(128), nullable=True, index=True)
    snapshot_json = Column(JSON, nullable=False)
    deleted_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)

    def to_dict(self):
        return {column.name: getattr(self, column.name) for column in self.__table__.columns}


class NetworkDeviceGroup(Base):
    __tablename__ = "netops_device_groups"

    id = Column(String(64), primary_key=True)
    name = Column(String(256), nullable=False, unique=True, index=True)
    description = Column(Text, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    def to_dict(self):
        return {column.name: getattr(self, column.name) for column in self.__table__.columns}


class NetworkDeviceGroupMember(Base):
    __tablename__ = "netops_device_group_members"

    group_id = Column(
        String(64),
        ForeignKey("netops_device_groups.id", ondelete="CASCADE"),
        primary_key=True,
    )
    device_id = Column(
        String(64),
        ForeignKey("netops_devices.id", ondelete="CASCADE"),
        primary_key=True,
    )
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class NetworkDeviceDynamicGroup(Base):
    __tablename__ = "netops_device_dynamic_groups"

    id = Column(String(64), primary_key=True)
    name = Column(String(256), nullable=False, index=True)
    description = Column(Text, nullable=True)
    selector_json = Column(JSON, nullable=False)
    status = Column(
        String(32), nullable=False, default="active", server_default="active", index=True
    )
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        UniqueConstraint("name", name="uq_netops_device_dynamic_groups_name"),
    )

    def to_dict(self):
        return {column.name: getattr(self, column.name) for column in self.__table__.columns}


class NetworkDeviceConnectionHealth(Base):
    __tablename__ = "netops_device_connection_health"

    id = Column(String(64), primary_key=True)
    device_id = Column(
        String(64),
        ForeignKey("netops_devices.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    status = Column(String(32), nullable=False, index=True)
    failure_code = Column(String(128), nullable=True, index=True)
    error = Column(Text, nullable=True)
    latency_ms = Column(Integer, nullable=True)
    source = Column(String(64), nullable=False, default="unknown", index=True)
    metadata_json = Column(JSON, nullable=True)
    checked_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)

    def to_dict(self):
        return {column.name: getattr(self, column.name) for column in self.__table__.columns}


class DeviceReadinessProjection(Base):
    """Rebuildable device-list read model; never a patrol fact source."""

    __tablename__ = "netops_device_readiness_projections"

    device_id = Column(
        String(64),
        ForeignKey("netops_devices.id", ondelete="CASCADE"),
        primary_key=True,
    )
    mode = Column(String(32), primary_key=True)
    status = Column(String(32), nullable=False, index=True)
    source_digest = Column(String(64), nullable=False, index=True)
    readiness_json = Column(JSON, nullable=False)
    computed_at = Column(DateTime(timezone=True), nullable=False)
    updated_at = Column(DateTime(timezone=True), nullable=False)

    def to_dict(self):
        return {column.name: getattr(self, column.name) for column in self.__table__.columns}


class PatrolDataset(Base):
    """Named import dataset, such as one quarterly parser-validation corpus."""

    __tablename__ = "netops_patrol_datasets"

    id = Column(String(256), primary_key=True)
    name = Column(String(256), nullable=False)
    mode = Column(String(32), nullable=False, index=True)
    source = Column(String(64), nullable=False, default="imported_file", index=True)
    status = Column(String(32), nullable=False, default="active", index=True)
    metadata_json = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
    imported_at = Column(DateTime, nullable=True)

    def to_dict(self):
        return {column.name: getattr(self, column.name) for column in self.__table__.columns}


class OfflinePatrolJobRecord(Base):
    """Durable operator-owned offline patrol import or comparison job."""

    __tablename__ = "netops_offline_patrol_jobs"

    id = Column(String(64), primary_key=True)
    idempotency_key = Column(String(128), nullable=False, unique=True)
    job_type = Column(String(32), nullable=False)
    name = Column(String(256), nullable=False)
    owner_id = Column(String(64), nullable=False)
    status = Column(
        String(32),
        nullable=False,
        default="draft",
        server_default="draft",
    )
    vendor = Column(String(64), nullable=True)
    model = Column(String(128), nullable=True)
    publish_events = Column(Boolean, nullable=False, default=False, server_default=text("false"))
    folder_id = Column(
        String(64),
        ForeignKey("netops_storage_folders.id", ondelete="SET NULL"),
        nullable=True,
    )
    workflow_run_id = Column(String(64), nullable=True)
    celery_task_id = Column(String(64), nullable=True)
    attempt_number = Column(Integer, nullable=False, default=0, server_default="0")
    execution_idempotency_key = Column(String(128), nullable=True)
    progress_json = Column(JSON, nullable=False, default=dict)
    preview_json = Column(JSON, nullable=True)
    result_json = Column(JSON, nullable=True)
    artifacts_json = Column(JSON, nullable=True)
    blockers_json = Column(JSON, nullable=True)
    error_message = Column(Text, nullable=True)
    archived = Column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
    )
    archived_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
    completed_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("ix_offline_patrol_jobs_idempotency", "idempotency_key"),
        Index("ix_offline_patrol_jobs_owner_created", "owner_id", "created_at"),
        Index("ix_offline_patrol_jobs_status_created", "status", "created_at"),
        Index("ix_offline_patrol_jobs_archived", "archived"),
        Index("ix_offline_patrol_jobs_workflow", "workflow_run_id"),
    )

    def to_dict(self):
        return {column.name: getattr(self, column.name) for column in self.__table__.columns}


class OfflinePatrolPeriodRecord(Base):
    """One immutable observation period within an offline patrol job."""

    __tablename__ = "netops_offline_patrol_periods"

    id = Column(String(64), primary_key=True)
    job_id = Column(
        String(64),
        ForeignKey("netops_offline_patrol_jobs.id", ondelete="CASCADE"),
        nullable=False,
    )
    role = Column(String(32), nullable=False)
    label = Column(String(128), nullable=False)
    observed_at = Column(DateTime(timezone=True), nullable=False)
    dataset_id = Column(String(256), nullable=False, unique=True)
    patrol_run_id = Column(String(128), nullable=True)
    status = Column(
        String(32),
        nullable=False,
        default="pending",
        server_default="pending",
    )
    result_json = Column(JSON, nullable=True)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        Index("uq_offline_patrol_period_job_role", "job_id", "role", unique=True),
        Index("ix_offline_patrol_period_dataset", "dataset_id", unique=True),
        Index("ix_offline_patrol_period_run", "patrol_run_id"),
    )

    def to_dict(self):
        return {column.name: getattr(self, column.name) for column in self.__table__.columns}


class OfflinePatrolSourceFileRecord(Base):
    """Authorized object-storage source attached to one offline period."""

    __tablename__ = "netops_offline_patrol_source_files"

    id = Column(String(64), primary_key=True)
    job_id = Column(
        String(64),
        ForeignKey("netops_offline_patrol_jobs.id", ondelete="CASCADE"),
        nullable=False,
    )
    period_role = Column(String(32), nullable=False)
    file_id = Column(
        String(64),
        ForeignKey("netops_file_metadata.id", ondelete="RESTRICT"),
        nullable=False,
    )
    relative_path = Column(String(1024), nullable=False)
    sha256 = Column(String(64), nullable=True)
    size_bytes = Column(Integer, nullable=False, default=0, server_default="0")
    status = Column(
        String(32),
        nullable=False,
        default="attached",
        server_default="attached",
    )
    error_message = Column(Text, nullable=True)
    preview_json = Column(JSON, nullable=True)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        Index(
            "uq_offline_patrol_source_job_period_file",
            "job_id",
            "period_role",
            "file_id",
            unique=True,
        ),
        Index("ix_offline_patrol_source_file", "file_id"),
    )

    def to_dict(self):
        return {column.name: getattr(self, column.name) for column in self.__table__.columns}


class OfflinePatrolDeviceBindingRecord(Base):
    """Auditable suggestion or confirmation between imported and inventory identity."""

    __tablename__ = "netops_offline_patrol_device_bindings"

    id = Column(String(64), primary_key=True)
    job_id = Column(
        String(64),
        ForeignKey("netops_offline_patrol_jobs.id", ondelete="CASCADE"),
        nullable=False,
    )
    period_role = Column(String(32), nullable=False)
    external_device_id = Column(String(512), nullable=False)
    device_name = Column(String(256), nullable=False)
    management_ip = Column(String(64), nullable=True)
    inventory_device_id = Column(
        String(64),
        ForeignKey("netops_devices.id", ondelete="SET NULL"),
        nullable=True,
    )
    match_status = Column(
        String(32),
        nullable=False,
        default="unmatched",
        server_default="unmatched",
    )
    confidence = Column(Float, nullable=False, default=0.0, server_default="0")
    match_reason = Column(String(256), nullable=True)
    confirmed_by = Column(String(128), nullable=True)
    confirmed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        Index(
            "uq_offline_patrol_binding_identity",
            "job_id",
            "period_role",
            "external_device_id",
            unique=True,
        ),
        Index("ix_offline_patrol_binding_inventory", "inventory_device_id"),
    )

    def to_dict(self):
        return {column.name: getattr(self, column.name) for column in self.__table__.columns}


class PatrolRun(Base):
    __tablename__ = "netops_patrol_runs"

    id = Column(String(128), primary_key=True)
    mode = Column(String(32), nullable=False, index=True)
    source = Column(String(64), nullable=False, index=True)
    dataset_id = Column(
        String(256),
        ForeignKey("netops_patrol_datasets.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    status = Column(String(32), nullable=False, default="running", index=True)
    collection_status = Column(
        String(32), nullable=False, default="running", server_default="running", index=True
    )
    parser_readiness_status = Column(
        String(32), nullable=False, default="unknown", server_default="unknown", index=True
    )
    fact_readiness_status = Column(
        String(32), nullable=False, default="unknown", server_default="unknown", index=True
    )
    analysis_status = Column(
        String(32), nullable=False, default="pending", server_default="pending", index=True
    )
    baseline_eligible = Column(
        Boolean, nullable=False, default=False, server_default=text("false"), index=True
    )
    run_purpose = Column(
        String(32),
        nullable=False,
        default="scheduled_patrol",
        server_default="scheduled_patrol",
        index=True,
    )
    logical_run_id = Column(String(128), nullable=False, index=True)
    attempt_number = Column(Integer, nullable=False, default=0, server_default="0")
    owner_generation = Column(Integer, nullable=False, default=1, server_default="1")
    owner_token = Column(String(64), nullable=True, index=True)
    worker_id = Column(String(256), nullable=True, index=True)
    heartbeat_at = Column(DateTime(timezone=True), nullable=True, index=True)
    lease_until = Column(DateTime(timezone=True), nullable=True, index=True)
    last_progress_at = Column(DateTime(timezone=True), nullable=True, index=True)
    device_count = Column(Integer, nullable=False, default=0)
    command_count = Column(Integer, nullable=False, default=0)
    started_at = Column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    completed_at = Column(DateTime(timezone=True), nullable=True)
    metadata_json = Column(JSON, nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        Index(
            "uq_netops_patrol_runs_logical_attempt",
            "logical_run_id",
            "attempt_number",
            unique=True,
        ),
    )

    def to_dict(self):
        return {column.name: getattr(self, column.name) for column in self.__table__.columns}


class PatrolAnalysisAttemptRecord(Base):
    """Durable completion and immutable rule scope for one analysis stage."""

    __tablename__ = "netops_patrol_analysis_attempts"

    id = Column(String(64), primary_key=True)
    run_id = Column(
        String(128),
        ForeignKey("netops_patrol_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    stage = Column(String(64), nullable=False, index=True)
    scope_hash = Column(String(64), nullable=False)
    scope_json = Column(JSON, nullable=False)
    rule_bundle_digest = Column(String(64), nullable=False, index=True)
    rule_bundle_json = Column(JSON, nullable=False)
    status = Column(String(32), nullable=False, default="running", index=True)
    attempt_number = Column(Integer, nullable=False, default=1, server_default="1")
    owner_token = Column(String(64), nullable=False, index=True)
    heartbeat_at = Column(DateTime(timezone=True), nullable=False)
    lease_until = Column(DateTime(timezone=True), nullable=False, index=True)
    result_json = Column(JSON, nullable=True)
    result_digest = Column(String(64), nullable=True, index=True)
    error = Column(Text, nullable=True)
    started_at = Column(DateTime(timezone=True), nullable=False)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False)
    updated_at = Column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        Index(
            "uq_netops_patrol_analysis_scope",
            "run_id",
            "stage",
            "scope_hash",
            unique=True,
        ),
    )

    def to_dict(self):
        return {column.name: getattr(self, column.name) for column in self.__table__.columns}


class CommandSnapshot(Base):
    """Immutable command-level patrol observation."""

    __tablename__ = "netops_command_snapshots"

    id = Column(String(64), primary_key=True)
    run_id = Column(
        String(128),
        ForeignKey("netops_patrol_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    dataset_id = Column(String(256), nullable=True, index=True)
    inventory_device_id = Column(
        String(64),
        ForeignKey("netops_devices.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    device_id = Column(String(256), nullable=False, index=True)
    execution_item_id = Column(String(128), nullable=False, index=True)
    device_name = Column(String(256), nullable=False)
    management_ip = Column(String(64), nullable=True)
    vendor = Column(String(64), nullable=True, index=True)
    model = Column(String(128), nullable=True, index=True)
    command = Column(String(512), nullable=False)
    canonical_command = Column(String(512), nullable=False, index=True)
    parser_template_id = Column(String(64), nullable=True, index=True)
    parser_version = Column(String(64), nullable=True)
    contract_version = Column(Integer, nullable=True)
    observed_at = Column(DateTime(timezone=True), nullable=False, index=True)
    structured_json = Column(JSON, nullable=True)
    raw_text = Column(Text, nullable=False, default="")
    raw_text_hash = Column(String(64), nullable=False, index=True)
    parse_status = Column(String(32), nullable=False, index=True)
    output_status = Column(
        String(32),
        nullable=False,
        default="not_classified",
        server_default="not_classified",
        index=True,
    )
    snapshot_kind = Column(String(32), nullable=False, default="full")
    source_file_hash = Column(String(64), nullable=True)
    import_fingerprint = Column(String(64), nullable=True)
    start_line = Column(Integer, nullable=True)
    end_line = Column(Integer, nullable=True)
    metadata_json = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        Index(
            "uq_netops_command_snapshot_import",
            "import_fingerprint",
            unique=True,
            postgresql_where=text("import_fingerprint IS NOT NULL"),
        ),
        Index(
            "ix_netops_command_snapshot_entity",
            "device_id",
            "canonical_command",
            "observed_at",
        ),
        Index(
            "ix_netops_command_snapshot_dataset",
            "dataset_id",
            "device_id",
            "canonical_command",
        ),
    )

    def to_dict(self):
        return {column.name: getattr(self, column.name) for column in self.__table__.columns}


class PatrolCollectionResult(Base):
    """Command-level collection completeness used for safe baseline decisions."""

    __tablename__ = "netops_patrol_collection_results"

    id = Column(String(64), primary_key=True)
    run_id = Column(
        String(128),
        ForeignKey("netops_patrol_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    inventory_device_id = Column(
        String(64),
        ForeignKey("netops_devices.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    device_id = Column(String(256), nullable=False, index=True)
    execution_item_id = Column(String(128), nullable=False, index=True)
    canonical_command = Column(String(512), nullable=False, index=True)
    status = Column(
        String(32), nullable=False, default="unknown", server_default="unknown", index=True
    )
    execution_status = Column(
        String(32), nullable=False, default="failed", server_default="failed", index=True
    )
    parse_status = Column(
        String(32),
        nullable=False,
        default="not_attempted",
        server_default="not_attempted",
        index=True,
    )
    output_status = Column(
        String(32),
        nullable=False,
        default="not_classified",
        server_default="not_classified",
        index=True,
    )
    fact_status = Column(
        String(32),
        nullable=False,
        default="blocked_by_collection",
        server_default="blocked_by_collection",
        index=True,
    )
    normalization_status = Column(
        String(32), nullable=False, default="blocked", server_default="blocked", index=True
    )
    parser_candidate_id = Column(String(64), nullable=True, index=True)
    authoritative_for_deletion = Column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    baseline_eligible = Column(
        Boolean, nullable=False, default=False, server_default=text("false"), index=True
    )
    observed_entity_count = Column(Integer, nullable=True)
    failure_code = Column(String(128), nullable=True, index=True)
    observed_at = Column(DateTime(timezone=True), nullable=True, index=True)
    metadata_json = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        Index(
            "uq_netops_patrol_collection_result",
            "run_id",
            "device_id",
            "execution_item_id",
            unique=True,
        ),
    )

    def to_dict(self):
        return {column.name: getattr(self, column.name) for column in self.__table__.columns}


class RawEvidenceRecord(Base):
    """Source-agnostic immutable evidence metadata."""

    __tablename__ = "netops_raw_evidence"

    id = Column(String(64), primary_key=True)
    evidence_key = Column(String(256), nullable=False)
    source_type = Column(String(64), nullable=False, index=True)
    evidence_type = Column(String(128), nullable=False, index=True)
    run_id = Column(String(128), nullable=True, index=True)
    dataset_id = Column(String(256), nullable=True, index=True)
    inventory_device_id = Column(
        String(64),
        ForeignKey("netops_devices.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    device_id = Column(String(256), nullable=True, index=True)
    source_snapshot_id = Column(
        String(64),
        ForeignKey("netops_command_snapshots.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    observed_at = Column(DateTime(timezone=True), nullable=True, index=True)
    ingested_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    content_ref = Column(String(1024), nullable=False)
    content_hash = Column(String(64), nullable=False, index=True)
    retention_policy = Column(String(64), nullable=True)
    metadata_json = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        Index("uq_netops_raw_evidence_evidence_key", "evidence_key", unique=True),
        Index(
            "uq_netops_raw_evidence_snapshot",
            "source_snapshot_id",
            unique=True,
            postgresql_where=text("source_snapshot_id IS NOT NULL"),
        ),
        Index(
            "ix_netops_raw_evidence_lookup",
            "source_type",
            "evidence_type",
            "device_id",
            "observed_at",
        ),
    )

    def to_dict(self):
        return {column.name: getattr(self, column.name) for column in self.__table__.columns}


class StateObservationRecord(Base):
    """Immutable field-level state derived from one structured snapshot."""

    __tablename__ = "netops_state_observations"

    id = Column(String(64), primary_key=True)
    idempotency_key = Column(String(64), nullable=False)
    run_id = Column(String(128), nullable=False, index=True)
    snapshot_id = Column(
        String(64),
        ForeignKey("netops_command_snapshots.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    dataset_id = Column(String(256), nullable=True, index=True)
    mode = Column(String(32), nullable=False, index=True)
    source = Column(String(64), nullable=False, index=True)
    source_type = Column(
        String(64), nullable=False, default="cli", server_default="cli", index=True
    )
    evidence_id = Column(
        String(64),
        ForeignKey("netops_raw_evidence.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    schema_id = Column(String(128), nullable=True, index=True)
    device_id = Column(String(256), nullable=False, index=True)
    vendor = Column(String(64), nullable=True)
    model = Column(String(128), nullable=True)
    canonical_command = Column(String(512), nullable=False, index=True)
    execution_item_id = Column(String(128), nullable=False, index=True)
    entity_type = Column(String(128), nullable=False, index=True)
    entity_key = Column(String(1024), nullable=False)
    field_name = Column(String(256), nullable=False, index=True)
    observation_type = Column(String(256), nullable=True, index=True)
    semantic_type = Column(String(64), nullable=True, index=True)
    unit = Column(String(64), nullable=True)
    value_json = Column(JSON, nullable=True)
    value_text = Column(Text, nullable=True)
    value_number = Column(Float, nullable=True)
    value_type = Column(String(32), nullable=False)
    observation_kind = Column(String(32), nullable=False, default="state", index=True)
    observed_at = Column(DateTime(timezone=True), nullable=False, index=True)
    fact_order_key = Column(String(512), nullable=False, default="", server_default="")
    parser_template_id = Column(String(64), nullable=True, index=True)
    parser_version = Column(String(64), nullable=True)
    contract_version = Column(Integer, nullable=True)
    confidence = Column(Float, nullable=False, default=1.0)
    metadata_json = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        Index(
            "uq_netops_state_observation_idempotency",
            "idempotency_key",
            unique=True,
        ),
        Index(
            "ix_netops_state_observation_identity",
            "device_id",
            "entity_type",
            "field_name",
            "observed_at",
        ),
        Index(
            "ix_netops_state_observation_snapshot_entity",
            "snapshot_id",
            "entity_type",
            "entity_key",
        ),
    )

    def to_dict(self):
        return {column.name: getattr(self, column.name) for column in self.__table__.columns}


class CurrentStateRecord(Base):
    """Latest production state for one device entity field."""

    __tablename__ = "netops_current_state"

    id = Column(String(64), primary_key=True)
    state_key = Column(String(64), nullable=False)
    device_id = Column(String(256), nullable=False, index=True)
    vendor = Column(String(64), nullable=True)
    model = Column(String(128), nullable=True)
    entity_type = Column(String(128), nullable=False, index=True)
    entity_key = Column(String(1024), nullable=False)
    field_name = Column(String(256), nullable=False, index=True)
    observation_type = Column(String(256), nullable=True, index=True)
    semantic_type = Column(String(64), nullable=True, index=True)
    unit = Column(String(64), nullable=True)
    value_json = Column(JSON, nullable=True)
    value_text = Column(Text, nullable=True)
    value_number = Column(Float, nullable=True)
    value_type = Column(String(32), nullable=False)
    observation_kind = Column(String(32), nullable=False, default="state", index=True)
    source = Column(String(64), nullable=False, index=True)
    source_type = Column(
        String(64), nullable=False, default="cli", server_default="cli", index=True
    )
    source_run_id = Column(String(128), nullable=True, index=True)
    source_snapshot_id = Column(
        String(64),
        ForeignKey("netops_command_snapshots.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    source_evidence_id = Column(
        String(64),
        ForeignKey("netops_raw_evidence.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    schema_id = Column(String(128), nullable=True, index=True)
    observed_at = Column(DateTime(timezone=True), nullable=False, index=True)
    fact_order_key = Column(String(512), nullable=False, default="", server_default="")
    source_execution_item_id = Column(String(128), nullable=True, index=True)
    source_observation_id = Column(String(64), nullable=True, index=True)
    lifecycle_status = Column(
        String(32), nullable=False, default="active", server_default="active", index=True
    )
    missing_since = Column(DateTime(timezone=True), nullable=True, index=True)
    missing_count = Column(Integer, nullable=False, default=0, server_default="0")
    version = Column(Integer, nullable=False, default=1)
    confidence = Column(Float, nullable=False, default=1.0)
    metadata_json = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        Index("uq_netops_current_state_key", "state_key", unique=True),
        Index(
            "ix_netops_current_state_identity",
            "device_id",
            "entity_type",
            "field_name",
        ),
    )

    def to_dict(self):
        return {column.name: getattr(self, column.name) for column in self.__table__.columns}


class NetworkChangeRecord(Base):
    """Immutable field-level change derived from two command snapshots."""

    __tablename__ = "netops_network_changes"

    id = Column(String(64), primary_key=True)
    idempotency_key = Column(String(64), nullable=False)
    run_id = Column(String(128), nullable=True, index=True)
    device_id = Column(String(256), nullable=True, index=True)
    canonical_command = Column(String(512), nullable=True, index=True)
    entity_type = Column(String(128), nullable=False, index=True)
    entity_key = Column(String(1024), nullable=False)
    dimensions_json = Column(JSON, nullable=True)
    field = Column(String(256), nullable=False, index=True)
    observation_type = Column(String(256), nullable=True, index=True)
    old_value_json = Column(JSON, nullable=True)
    new_value_json = Column(JSON, nullable=True)
    change_type = Column(String(32), nullable=False, index=True)
    previous_snapshot_id = Column(String(64), nullable=True, index=True)
    current_snapshot_id = Column(String(64), nullable=True, index=True)
    previous_observation_id = Column(String(64), nullable=True, index=True)
    current_observation_id = Column(String(64), nullable=True, index=True)
    source = Column(
        String(64), nullable=False, default="patrol", server_default="patrol", index=True
    )
    analysis_revision = Column(
        String(128), nullable=False, default="original", server_default="original", index=True
    )
    rule_bundle_digest = Column(String(64), nullable=True, index=True)
    old_number = Column(Float, nullable=True)
    new_number = Column(Float, nullable=True)
    event_status = Column(
        String(32), nullable=False, default="pending", server_default="pending", index=True
    )
    change_policy_id = Column(String(64), nullable=True, index=True)
    change_policy_version_id = Column(String(64), nullable=True, index=True)
    comparison_type = Column(String(64), nullable=True)
    decision_reason = Column(String(256), nullable=True)
    policy_snapshot_json = Column(JSON, nullable=True)
    observed_at = Column(DateTime(timezone=True), nullable=True, index=True)
    detected_at = Column(DateTime(timezone=True), nullable=False, index=True)
    processed_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default=text("CURRENT_TIMESTAMP"),
        index=True,
    )
    confidence = Column(Float, nullable=False, default=1.0)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        Index(
            "ix_netops_network_changes_idempotency_key",
            "idempotency_key",
            unique=True,
        ),
    )

    def to_dict(self):
        return {column.name: getattr(self, column.name) for column in self.__table__.columns}


class NetworkChangeDecisionRecord(Base):
    """Auditable emitted/suppressed/invalid ChangePolicy decision."""

    __tablename__ = "netops_network_change_decisions"

    id = Column(String(64), primary_key=True)
    idempotency_key = Column(String(64), nullable=False)
    run_id = Column(String(128), nullable=False, index=True)
    device_id = Column(String(256), nullable=True, index=True)
    canonical_command = Column(String(512), nullable=True, index=True)
    entity_type = Column(String(128), nullable=False, index=True)
    entity_key = Column(String(1024), nullable=False)
    field = Column(String(256), nullable=False, index=True)
    observation_type = Column(String(256), nullable=True, index=True)
    previous_observation_id = Column(String(64), nullable=True, index=True)
    current_observation_id = Column(String(64), nullable=True, index=True)
    decision = Column(String(32), nullable=False, index=True)
    reason = Column(String(256), nullable=True)
    change_policy_id = Column(String(64), nullable=True, index=True)
    change_policy_version_id = Column(String(64), nullable=True, index=True)
    comparison_type = Column(String(64), nullable=True)
    old_value_json = Column(JSON, nullable=True)
    new_value_json = Column(JSON, nullable=True)
    observed_at = Column(DateTime(timezone=True), nullable=True, index=True)
    expires_at = Column(DateTime(timezone=True), nullable=True, index=True)
    metadata_json = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        Index("ix_netops_change_decisions_idempotency", "idempotency_key", unique=True),
    )

    def to_dict(self):
        return {column.name: getattr(self, column.name) for column in self.__table__.columns}


class NetworkEventRecord(Base):
    """Operational event derived from one persisted network change."""

    __tablename__ = "netops_network_events"

    id = Column(String(64), primary_key=True)
    idempotency_key = Column(String(64), nullable=False)
    event_type = Column(String(128), nullable=False, index=True)
    severity = Column(String(32), nullable=False, index=True)
    device_id = Column(String(256), nullable=True, index=True)
    entity_type = Column(String(128), nullable=False, index=True)
    entity_key = Column(String(1024), nullable=False)
    correlation_key = Column(String(1024), nullable=False, index=True)
    correlation_dimensions_json = Column(JSON, nullable=True)
    source_change_id = Column(String(64), nullable=True, index=True)
    run_id = Column(String(128), nullable=True, index=True)
    latest_change_id = Column(String(64), nullable=True, index=True)
    last_seen_run_id = Column(String(128), nullable=True, index=True)
    occurred_at = Column(DateTime(timezone=True), nullable=False, index=True)
    first_seen_at = Column(DateTime(timezone=True), nullable=False, index=True)
    last_seen_at = Column(DateTime(timezone=True), nullable=False, index=True)
    last_seen_order_key = Column(String(256), nullable=False, default="", server_default="")
    occurrence_count = Column(Integer, nullable=False, default=1, server_default="1")
    status = Column(String(32), nullable=False, default="open", index=True)
    payload_json = Column(JSON, nullable=False)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        Index(
            "ix_netops_network_events_idempotency_key",
            "idempotency_key",
            unique=True,
        ),
        Index(
            "ix_netops_network_events_active_correlation",
            "event_type",
            "correlation_key",
            "status",
        ),
        Index(
            "uq_netops_network_events_active_correlation",
            "event_type",
            "correlation_key",
            unique=True,
            postgresql_where=text(
                "status IN ('open', 'acknowledged', 'recovering') AND correlation_key <> ''"
            ),
        ),
    )

    def to_dict(self):
        return {column.name: getattr(self, column.name) for column in self.__table__.columns}


class NetworkEventOccurrenceRecord(Base):
    """Append-only observation of an operational event lifecycle."""

    __tablename__ = "netops_network_event_occurrences"

    id = Column(String(64), primary_key=True)
    idempotency_key = Column(String(64), nullable=False)
    event_id = Column(
        String(64),
        ForeignKey("netops_network_events.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    run_id = Column(String(128), nullable=True, index=True)
    source_change_id = Column(String(64), nullable=True, index=True)
    observed_at = Column(DateTime(timezone=True), nullable=False, index=True)
    severity = Column(String(32), nullable=False, index=True)
    occurrence_kind = Column(String(32), nullable=False, default="observed", index=True)
    payload_json = Column(JSON, nullable=False)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        Index(
            "ix_netops_event_occurrence_idempotency",
            "idempotency_key",
            unique=True,
        ),
        Index(
            "ix_netops_event_occurrence_run_event",
            "run_id",
            "event_id",
            "observed_at",
        ),
    )

    def to_dict(self):
        return {column.name: getattr(self, column.name) for column in self.__table__.columns}


class NetworkEventOutboxRecord(Base):
    """Durable publication record committed with the canonical NetworkEvent."""

    __tablename__ = "netops_network_event_outbox"

    id = Column(String(64), primary_key=True)
    event_id = Column(
        String(64),
        ForeignKey("netops_network_events.id", ondelete="CASCADE"),
        nullable=False,
    )
    occurrence_id = Column(
        String(64),
        ForeignKey("netops_network_event_occurrences.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    event_type = Column(String(128), nullable=False, index=True)
    payload_json = Column(JSON, nullable=False)
    status = Column(
        String(32), nullable=False, default="pending", server_default="pending", index=True
    )
    attempts = Column(Integer, nullable=False, default=0, server_default="0")
    claim_token = Column(String(64), nullable=True, index=True)
    lease_until = Column(DateTime, nullable=True, index=True)
    last_error = Column(Text, nullable=True)
    available_at = Column(DateTime, nullable=False, index=True)
    published_at = Column(DateTime, nullable=True)
    dead_letter_at = Column(DateTime, nullable=True)
    message_id = Column(String(256), nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        Index("ix_netops_network_event_outbox_event_id", "event_id"),
        Index(
            "uq_netops_network_event_outbox_occurrence_id",
            "occurrence_id",
            unique=True,
            postgresql_where=text("occurrence_id IS NOT NULL"),
        ),
    )

    def to_dict(self):
        return {column.name: getattr(self, column.name) for column in self.__table__.columns}


class EventRuleRecord(Base):
    """Auditable event rule metadata."""

    __tablename__ = "netops_event_rules"

    id = Column(String(64), primary_key=True)
    name = Column(String(128), nullable=False, index=True)
    description = Column(Text, nullable=True)
    status = Column(String(32), nullable=False, default="active", index=True)
    current_version = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (Index("uq_netops_event_rules_name", "name", unique=True),)

    def to_dict(self):
        return {column.name: getattr(self, column.name) for column in self.__table__.columns}


class EventRuleVersionRecord(Base):
    """Immutable event rule version with deterministic condition JSON."""

    __tablename__ = "netops_event_rule_versions"

    id = Column(String(64), primary_key=True)
    rule_id = Column(
        String(64),
        ForeignKey("netops_event_rules.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    version = Column(Integer, nullable=False)
    event_type = Column(String(128), nullable=False, index=True)
    severity = Column(String(32), nullable=False, index=True)
    condition_json = Column(JSON, nullable=False)
    priority = Column(Integer, nullable=False, default=100, server_default="100", index=True)
    exclusive = Column(Boolean, nullable=False, default=False, server_default=text("false"))
    event_class = Column(
        String(32), nullable=False, default="fault", server_default="fault", index=True
    )
    recovery_json = Column(JSON, nullable=True)
    correlation_key_json = Column(JSON, nullable=True)
    status = Column(String(32), nullable=False, default="draft", index=True)
    source_job_id = Column(String(64), nullable=True, index=True)
    validation_report_json = Column(JSON, nullable=True)
    digest = Column(String(64), nullable=True, index=True)
    created_by = Column(String(64), nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    activated_at = Column(DateTime, nullable=True)
    deprecated_at = Column(DateTime, nullable=True)

    __table_args__ = (
        Index("uq_event_rule_version", "rule_id", "version", unique=True),
        Index(
            "uq_event_rule_active_version",
            "rule_id",
            unique=True,
            postgresql_where=text("status = 'active'"),
        ),
    )

    def to_dict(self):
        return {column.name: getattr(self, column.name) for column in self.__table__.columns}


class CollectionPolicyRecord(Base):
    """Collection policy metadata."""

    __tablename__ = "netops_collection_policies"

    id = Column(String(64), primary_key=True)
    name = Column(String(128), nullable=False, index=True)
    description = Column(Text, nullable=True)
    status = Column(String(32), nullable=False, default="draft", index=True)
    current_version = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (Index("uq_netops_collection_policies_name", "name", unique=True),)

    def to_dict(self):
        return {column.name: getattr(self, column.name) for column in self.__table__.columns}


class CommandProfileRecord(Base):
    """Command profile metadata."""

    __tablename__ = "netops_command_profiles"

    id = Column(String(64), primary_key=True)
    name = Column(String(128), nullable=False, index=True)
    description = Column(Text, nullable=True)
    status = Column(String(32), nullable=False, default="draft", index=True)
    current_version = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (Index("uq_netops_command_profiles_name", "name", unique=True),)

    def to_dict(self):
        return {column.name: getattr(self, column.name) for column in self.__table__.columns}


class CommandProfileVersionRecord(Base):
    """Immutable version of command profile items."""

    __tablename__ = "netops_command_profile_versions"

    id = Column(String(64), primary_key=True)
    profile_id = Column(
        String(64),
        ForeignKey("netops_command_profiles.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    version = Column(Integer, nullable=False)
    status = Column(String(32), nullable=False, default="draft", index=True)
    items_json = Column(JSON, nullable=False)
    coverage_json = Column(JSON, nullable=True)
    created_by = Column(String(64), nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    activated_at = Column(DateTime, nullable=True)
    deprecated_at = Column(DateTime, nullable=True)

    __table_args__ = (
        Index("uq_command_profile_version", "profile_id", "version", unique=True),
        Index(
            "uq_command_profile_active_version",
            "profile_id",
            unique=True,
            postgresql_where=text("status = 'active'"),
        ),
    )

    def to_dict(self):
        return {column.name: getattr(self, column.name) for column in self.__table__.columns}


class CollectionProfileBindingRecord(Base):
    """Bind a command profile version to device inventory selectors."""

    __tablename__ = "netops_collection_profile_bindings"

    id = Column(String(64), primary_key=True)
    profile_version_id = Column(
        String(64),
        ForeignKey("netops_command_profile_versions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    selector_json = Column(JSON, nullable=False)
    priority = Column(Integer, nullable=False, default=100)
    status = Column(String(32), nullable=False, default="active", index=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    def to_dict(self):
        return {column.name: getattr(self, column.name) for column in self.__table__.columns}


class CollectionExecutionPlanRecord(Base):
    """Immutable collector execution-plan snapshot created before launch."""

    __tablename__ = "netops_collection_execution_plans"

    id = Column(String(64), primary_key=True)
    device_id = Column(String(64), nullable=False, index=True)
    action = Column(String(32), nullable=False, index=True)
    mode = Column(String(32), nullable=False, index=True)
    profile_name = Column(String(128), nullable=False, index=True)
    profile_version_id = Column(String(64), nullable=True, index=True)
    binding_id = Column(String(64), nullable=True, index=True)
    policy_source = Column(String(64), nullable=False, index=True)
    credential_ref = Column(String(512), nullable=True)
    collector_options_json = Column(JSON, nullable=True)
    selector_json = Column(JSON, nullable=True)
    plan_json = Column(JSON, nullable=False)
    command_count = Column(Integer, nullable=False, default=0)
    plan_sha256 = Column(String(64), nullable=False, index=True)
    status = Column(String(32), nullable=False, default="planned", index=True)
    launch_source = Column(String(64), nullable=True, index=True)
    patrol_batch_id = Column(String(128), nullable=True, index=True)
    workflow_run_id = Column(String(64), nullable=True, index=True)
    celery_task_id = Column(String(128), nullable=True, index=True)
    failure_code = Column(String(128), nullable=True)
    error = Column(Text, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    launched_at = Column(DateTime, nullable=True)
    updated_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        Index("ix_collection_execution_plan_launch", "patrol_batch_id", "status"),
        Index("ix_collection_execution_plan_device_created", "device_id", "created_at"),
    )

    def to_dict(self):
        return {column.name: getattr(self, column.name) for column in self.__table__.columns}


class ParserTemplate(Base):
    __tablename__ = "parser_templates"

    id = Column(String(64), primary_key=True)
    vendor = Column(String(64), nullable=False, index=True)
    model = Column(String(128), nullable=True, index=True)
    family = Column(String(128), nullable=True, index=True)
    profile = Column(String(128), nullable=False, index=True)
    canonical_command = Column(String(256), nullable=False, index=True)
    category = Column(String(64), nullable=False)
    version = Column(String(64), nullable=False)
    asset_path = Column(String(1024), nullable=False)
    sha256 = Column(String(64), nullable=False)
    status = Column(String(32), nullable=False, default="draft", index=True)
    validation_json = Column(JSON, nullable=True)
    contract_version = Column(Integer, nullable=False, default=1, server_default=text("1"))
    contract_sha256 = Column(String(64), nullable=True, index=True)
    source_job_id = Column(String(64), nullable=True, index=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    activated_at = Column(DateTime, nullable=True)
    deprecated_at = Column(DateTime, nullable=True)

    __table_args__ = (
        Index(
            "uq_parser_template_version",
            "vendor",
            "profile",
            "canonical_command",
            "version",
            unique=True,
        ),
        Index(
            "uq_parser_template_active",
            "vendor",
            "profile",
            "canonical_command",
            unique=True,
            postgresql_where=text("status = 'active'"),
        ),
    )

    def to_dict(self):
        return {column.name: getattr(self, column.name) for column in self.__table__.columns}


class ObservationContractRecord(Base):
    """Versioned state semantics shared by CLI parsers and telemetry mappers."""

    __tablename__ = "netops_observation_contracts"

    id = Column(String(64), primary_key=True)
    source_type = Column(String(64), nullable=False, index=True)
    schema_id = Column(String(128), nullable=False, index=True)
    schema_ref = Column(String(512), nullable=True)
    source_template_id = Column(
        String(64),
        ForeignKey("parser_templates.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    entity_type = Column(String(128), nullable=False, index=True)
    primary_keys_json = Column(JSON, nullable=False)
    required_fields_json = Column(JSON, nullable=False)
    optional_fields_json = Column(JSON, nullable=True)
    validators_json = Column(JSON, nullable=True)
    ignore_fields_json = Column(JSON, nullable=True)
    allow_empty = Column(Boolean, nullable=False, default=False)
    empty_patterns_json = Column(JSON, nullable=True)
    state_semantics_json = Column(JSON, nullable=True)
    version = Column(Integer, nullable=False, default=1)
    contract_sha256 = Column(String(64), nullable=False, index=True)
    status = Column(String(32), nullable=False, default="active", index=True)
    source_job_id = Column(String(64), nullable=True, index=True)
    approval_json = Column(JSON, nullable=True)
    compatibility_json = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
    activated_at = Column(DateTime, nullable=True)
    deprecated_at = Column(DateTime, nullable=True)

    __table_args__ = (
        Index(
            "uq_observation_contract_version",
            "source_type",
            "schema_id",
            "version",
            unique=True,
        ),
        Index(
            "uq_observation_contract_active",
            "source_type",
            "schema_id",
            unique=True,
            postgresql_where=text("status = 'active'"),
        ),
    )

    def to_dict(self):
        return {column.name: getattr(self, column.name) for column in self.__table__.columns}


class ChangePolicyRecord(Base):
    """Versioned change policy metadata."""

    __tablename__ = "netops_change_policies"

    id = Column(String(64), primary_key=True)
    name = Column(String(128), nullable=False, index=True)
    observation_type = Column(String(256), nullable=False, index=True)
    description = Column(Text, nullable=True)
    status = Column(String(32), nullable=False, default="draft", index=True)
    current_version = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (UniqueConstraint("name"),)

    def to_dict(self):
        return {column.name: getattr(self, column.name) for column in self.__table__.columns}


class ChangePolicyVersionRecord(Base):
    """Immutable ChangePolicy proposal version consumed by Change Detector."""

    __tablename__ = "netops_change_policy_versions"

    id = Column(String(64), primary_key=True)
    policy_id = Column(
        String(64),
        ForeignKey("netops_change_policies.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    version = Column(Integer, nullable=False)
    observation_type = Column(String(256), nullable=False, index=True)
    semantic_type = Column(String(64), nullable=False, index=True)
    compare_mode = Column(String(64), nullable=False, index=True)
    policy_json = Column(JSON, nullable=False)
    validation_report_json = Column(JSON, nullable=True)
    status = Column(String(32), nullable=False, default="draft", index=True)
    source_job_id = Column(String(64), nullable=True, index=True)
    digest = Column(String(64), nullable=False, index=True)
    created_by = Column(String(128), nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    activated_at = Column(DateTime, nullable=True)
    deprecated_at = Column(DateTime, nullable=True)

    __table_args__ = (
        Index("uq_change_policy_version", "policy_id", "version", unique=True),
        Index(
            "uq_change_policy_active_version",
            "policy_id",
            unique=True,
            postgresql_where=text("status = 'active'"),
        ),
        Index(
            "uq_change_policy_active_observation_type",
            "observation_type",
            unique=True,
            postgresql_where=text("status = 'active'"),
        ),
        Index(
            "uq_change_policy_version_digest",
            "policy_id",
            "version",
            "digest",
            unique=True,
        ),
    )

    def to_dict(self):
        return {column.name: getattr(self, column.name) for column in self.__table__.columns}


class ParserCommandAlias(Base):
    __tablename__ = "parser_command_aliases"

    id = Column(String(64), primary_key=True)
    alias = Column(String(256), nullable=False, unique=True, index=True)
    canonical_command = Column(String(256), nullable=False, index=True)
    confidence = Column(Float, nullable=False, default=1.0)
    evidence_json = Column(JSON, nullable=True)
    status = Column(String(32), nullable=False, default="confirmed", index=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    def to_dict(self):
        return {column.name: getattr(self, column.name) for column in self.__table__.columns}


class ParserDeviceProfile(Base):
    __tablename__ = "parser_device_profiles"

    id = Column(String(64), primary_key=True)
    vendor = Column(String(64), nullable=False, index=True)
    model = Column(String(128), nullable=False, index=True)
    family = Column(String(128), nullable=True, index=True)
    normalized_model = Column(String(128), nullable=False)
    confidence = Column(Float, nullable=False, default=1.0)
    status = Column(String(32), nullable=False, default="confirmed", index=True)
    evidence_json = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (Index("uq_parser_device_profile", "vendor", "model", unique=True),)

    def to_dict(self):
        return {column.name: getattr(self, column.name) for column in self.__table__.columns}


class ParserIdentitySignature(Base):
    __tablename__ = "parser_identity_signatures"

    id = Column(String(64), primary_key=True)
    vendor = Column(String(64), nullable=False, index=True)
    model = Column(String(128), nullable=False, index=True)
    family = Column(String(128), nullable=True)
    evidence_command = Column(String(256), nullable=False)
    pattern = Column(Text, nullable=False)
    confidence = Column(Float, nullable=False, default=1.0)
    status = Column(String(32), nullable=False, default="confirmed", index=True)
    source = Column(String(32), nullable=False, default="discovered")
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        Index(
            "uq_parser_identity_signature",
            "vendor",
            "model",
            "evidence_command",
            "pattern",
            unique=True,
        ),
    )

    def to_dict(self):
        return {column.name: getattr(self, column.name) for column in self.__table__.columns}


class ParserTemplateJob(Base):
    __tablename__ = "parser_template_jobs"

    id = Column(String(64), primary_key=True)
    job_type = Column(String(32), nullable=False, index=True)
    source_mode = Column(
        String(32),
        nullable=False,
        default="parser_validation",
        server_default="parser_validation",
        index=True,
    )
    vendor = Column(String(64), nullable=False)
    model = Column(String(128), nullable=True)
    family = Column(String(128), nullable=True)
    profile = Column(String(128), nullable=False)
    canonical_command = Column(String(256), nullable=False)
    output_signature = Column(String(64), nullable=False)
    idempotency_key = Column(String(64), nullable=False, index=True)
    status = Column(String(32), nullable=False, default="pending", index=True)
    sample_count = Column(Integer, default=0)
    attempts = Column(Integer, default=0)
    contract_revision_count = Column(Integer, nullable=False, default=0, server_default="0")
    template_attempt_count = Column(Integer, nullable=False, default=0, server_default="0")
    best_attempt_id = Column(String(64), nullable=True, index=True)
    agent_state_json = Column(JSON, nullable=True)
    agent_budget_json = Column(JSON, nullable=True)
    failure_code = Column(String(64), nullable=True)
    last_error = Column(Text, nullable=True)
    celery_task_id = Column(String(64), nullable=True, index=True)
    dispatched_at = Column(DateTime, nullable=True)
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    superseded_by_job_id = Column(String(64), nullable=True)
    superseded_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        Index(
            "ix_parser_template_jobs_superseded_by",
            "superseded_by_job_id",
        ),
        Index(
            "uq_parser_job_asset_key",
            "idempotency_key",
            unique=True,
        ),
    )

    def to_dict(self):
        return {column.name: getattr(self, column.name) for column in self.__table__.columns}


class ParserGenerationAttempt(Base):
    __tablename__ = "parser_generation_attempts"

    id = Column(String(64), primary_key=True)
    job_id = Column(
        String(64),
        ForeignKey("parser_template_jobs.id", ondelete="CASCADE"),
        nullable=False,
    )
    attempt_number = Column(Integer, nullable=False)
    parent_attempt_id = Column(
        String(64),
        ForeignKey("parser_generation_attempts.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    stage = Column(String(32), nullable=False, index=True)
    action = Column(String(64), nullable=False)
    model_provider = Column(String(64), nullable=True)
    model_name = Column(String(128), nullable=True)
    textfsm_runtime_version = Column(String(32), nullable=False)
    dialect_version = Column(String(64), nullable=False)
    dialect_hash = Column(String(64), nullable=False)
    prompt_version = Column(String(64), nullable=True)
    prompt_hash = Column(String(64), nullable=True)
    contract_hash = Column(String(64), nullable=True)
    train_sample_set_hash = Column(String(64), nullable=True)
    holdout_sample_set_hash = Column(String(64), nullable=True)
    template_text = Column(Text, nullable=True)
    template_sha256 = Column(String(64), nullable=True, index=True)
    validation_report_json = Column(JSON, nullable=False, default=dict)
    failure_signature = Column(String(64), nullable=True, index=True)
    candidate_rank = Column(Float, nullable=True)
    status = Column(String(32), nullable=False, index=True)
    token_usage_json = Column(JSON, nullable=True)
    latency_ms = Column(Integer, nullable=True)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        Index(
            "uq_parser_generation_attempt_number",
            "job_id",
            "attempt_number",
            unique=True,
        ),
        Index(
            "ix_parser_generation_attempt_job_created",
            "job_id",
            "created_at",
        ),
    )

    def to_dict(self):
        return {column.name: getattr(self, column.name) for column in self.__table__.columns}


class ParserJobSample(Base):
    __tablename__ = "parser_job_samples"

    id = Column(String(64), primary_key=True)
    job_id = Column(String(64), nullable=False, index=True)
    snapshot_id = Column(
        String(64),
        ForeignKey("netops_command_snapshots.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    snapshot_source = Column(
        String(32),
        nullable=False,
        default="postgresql",
        server_default="postgresql",
        index=True,
    )
    device_id = Column(String(256), nullable=False, index=True)
    output_signature = Column(String(64), nullable=False)
    dataset_id = Column(String(256), nullable=True, index=True)
    sanitization_status = Column(
        String(32),
        nullable=False,
        default="pending_sanitization",
        server_default="pending_sanitization",
        index=True,
    )
    sanitization_version = Column(String(32), nullable=True)
    sanitized_text = Column(Text, nullable=True)
    sanitized_hash = Column(String(64), nullable=True, index=True)
    redaction_summary_json = Column(JSON, nullable=True)
    sanitized_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        Index("uq_parser_job_snapshot", "job_id", "snapshot_id", unique=True),
        CheckConstraint(
            "snapshot_source = 'postgresql'",
            name="ck_parser_job_samples_postgresql_source",
        ),
    )

    def to_dict(self):
        return {column.name: getattr(self, column.name) for column in self.__table__.columns}


class SnapshotParseResult(Base):
    __tablename__ = "snapshot_parse_results"

    id = Column(String(64), primary_key=True)
    snapshot_id = Column(String(64), nullable=False, index=True)
    template_id = Column(String(64), nullable=False, index=True)
    template_version = Column(String(64), nullable=False)
    parse_status = Column(String(32), nullable=False, index=True)
    structured_json = Column(JSON, nullable=True)
    failure_code = Column(String(64), nullable=True)
    parsed_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        Index("uq_snapshot_template_parse", "snapshot_id", "template_id", unique=True),
    )


class ParserOutboxEvent(Base):
    __tablename__ = "parser_outbox_events"

    id = Column(String(64), primary_key=True)
    event_type = Column(String(128), nullable=False, index=True)
    aggregate_id = Column(String(64), nullable=False, index=True)
    payload_json = Column(JSON, nullable=False)
    status = Column(String(32), default="pending", index=True)
    attempts = Column(Integer, default=0)
    claim_token = Column(String(64), nullable=True, index=True)
    lease_until = Column(DateTime(timezone=True), nullable=True, index=True)
    last_error = Column(Text, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    published_at = Column(DateTime, nullable=True)


class ParserControlSetting(Base):
    __tablename__ = "parser_control_settings"

    key = Column(String(128), primary_key=True)
    value_json = Column(JSON, nullable=False)
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    def to_dict(self):
        return {column.name: getattr(self, column.name) for column in self.__table__.columns}


class ParserBackfillJobRecord(Base):
    __tablename__ = "parser_backfill_jobs"

    id = Column(String(64), primary_key=True)
    idempotency_key = Column(String(64), nullable=False, unique=True, index=True)
    template_id = Column(
        String(64),
        ForeignKey("parser_templates.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    status = Column(String(32), nullable=False, default="draft", index=True)
    dry_run = Column(Boolean, nullable=False, default=True, server_default=text("true"))
    filters_json = Column(JSON, nullable=False, default=dict)
    summary_json = Column(JSON, nullable=True)
    requested_by = Column(String(128), nullable=False)
    approved_by = Column(String(128), nullable=True)
    approval_reason = Column(Text, nullable=True)
    last_error = Column(Text, nullable=True)
    owner_token = Column(String(64), nullable=True, index=True)
    lease_until = Column(DateTime(timezone=True), nullable=True, index=True)
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    def to_dict(self):
        return {column.name: getattr(self, column.name) for column in self.__table__.columns}


class ParserBackfillResultRecord(Base):
    __tablename__ = "parser_backfill_results"

    id = Column(String(64), primary_key=True)
    job_id = Column(
        String(64),
        ForeignKey("parser_backfill_jobs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    snapshot_id = Column(
        String(64),
        ForeignKey("netops_command_snapshots.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    parse_status = Column(String(32), nullable=False, index=True)
    structured_json = Column(JSON, nullable=True)
    failure_code = Column(String(128), nullable=True)
    observation_count = Column(Integer, nullable=False, default=0, server_default="0")
    existing_observation_count = Column(Integer, nullable=False, default=0, server_default="0")
    committed_observation_count = Column(Integer, nullable=False, default=0, server_default="0")
    committed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        Index("uq_parser_backfill_job_snapshot", "job_id", "snapshot_id", unique=True),
    )

    def to_dict(self):
        return {column.name: getattr(self, column.name) for column in self.__table__.columns}


class RuleGenerationJobRecord(Base):
    """Unified control-plane job for generated parser/rule assets."""

    __tablename__ = "netops_rule_generation_jobs"

    id = Column(String(64), primary_key=True)
    job_type = Column(String(64), nullable=False, index=True)
    status = Column(String(32), nullable=False, default="draft", index=True)
    source_ref = Column(String(512), nullable=True, index=True)
    asset_ref = Column(String(512), nullable=True, index=True)
    asset_type = Column(String(64), nullable=True, index=True)
    asset_id = Column(String(64), nullable=True, index=True)
    asset_version_id = Column(String(64), nullable=True, index=True)
    asset_digest = Column(String(64), nullable=True, index=True)
    payload_json = Column(JSON, nullable=False, default=dict)
    validation_report_json = Column(JSON, nullable=True)
    approval_required = Column(Boolean, nullable=False, default=True)
    created_by = Column(String(128), nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
    completed_at = Column(DateTime, nullable=True)

    __table_args__ = (Index("ix_rule_generation_jobs_type_status", "job_type", "status"),)

    def to_dict(self):
        return {column.name: getattr(self, column.name) for column in self.__table__.columns}


class RuleGenerationContextRecord(Base):
    """Immutable, sanitized dataset context used by one LLM generation seed."""

    __tablename__ = "netops_rule_generation_contexts"

    id = Column(String(64), primary_key=True)
    job_id = Column(
        String(64),
        ForeignKey("netops_rule_generation_jobs.id", ondelete="CASCADE"),
        nullable=False,
    )
    offline_job_id = Column(
        String(64),
        ForeignKey("netops_offline_patrol_jobs.id", ondelete="SET NULL"),
        nullable=True,
    )
    dataset_ids_json = Column(JSON, nullable=False)
    observation_type = Column(String(256), nullable=True)
    field_name = Column(String(256), nullable=True)
    context_json = Column(JSON, nullable=False)
    context_digest = Column(String(64), nullable=False, unique=True)
    privacy_report_json = Column(JSON, nullable=False)
    created_by = Column(String(128), nullable=True)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        Index("ix_rule_generation_context_job", "job_id"),
        Index("ix_rule_generation_context_offline_job", "offline_job_id"),
        Index("ix_rule_generation_context_digest", "context_digest", unique=True),
    )

    def to_dict(self):
        return {column.name: getattr(self, column.name) for column in self.__table__.columns}


class AgentModuleControlRecord(Base):
    __tablename__ = "netops_agent_module_controls"

    id = Column(String(64), primary_key=True)
    module_id = Column(String(128), nullable=False, index=True)
    status = Column(String(32), nullable=False, default="enabled", index=True)
    maintenance_reason = Column(Text, nullable=True)
    maintenance_starts_at = Column(DateTime, nullable=True)
    maintenance_ends_at = Column(DateTime, nullable=True)
    updated_by = Column(String(128), nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        UniqueConstraint(
            "module_id", name="uq_netops_agent_module_controls_module_id"
        ),
        Index(
            "ix_netops_agent_module_controls_maintenance_window",
            "maintenance_starts_at",
            "maintenance_ends_at",
        ),
        CheckConstraint(
            "status IN ('enabled','disabled','maintenance')",
            name="ck_agent_module_control_status",
        ),
    )

    def to_dict(self):
        return {column.name: getattr(self, column.name) for column in self.__table__.columns}


def init_db_models(engine):
    """Compatibility wrapper; schema changes are owned by Alembic."""
    from src.infrastructure.db.migrations import upgrade_database

    upgrade_database()
    print("[OK] Database migrations applied")
