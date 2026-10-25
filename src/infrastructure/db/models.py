# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

import uuid
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
    event,
    text,
)
from sqlalchemy.orm import declarative_base

Base = declarative_base()


class BootstrapStepRecord(Base):
    """Platform ledger; App seed definitions remain in the owning package."""
    __tablename__ = "netops_bootstrap_steps"
    owner = Column(String(128), primary_key=True)
    version = Column(String(64), primary_key=True)
    completed_at = Column(DateTime(timezone=True), nullable=False)


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


class SSHPolicyRecord(Base):
    """One administrator-managed command policy, independent of every App."""

    __tablename__ = "netops_ssh_policy"
    id = Column(Integer, primary_key=True)
    patterns = Column(JSON, nullable=False)
    version = Column(Integer, nullable=False)
    updated_by = Column(String(64), nullable=False)
    updated_at = Column(DateTime(timezone=True), nullable=False)
    __table_args__ = (CheckConstraint("id = 1", name="ck_netops_ssh_policy_singleton"),)


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
    report_manifest_id = Column(String(64), nullable=True, index=True)
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
    report_purpose = Column(
        String(32),
        nullable=False,
        default="production_current",
        server_default="production_current",
        index=True,
    )
    ticket_id = Column(String(128), nullable=True, index=True)
    target_type = Column(String(32), nullable=False, index=True)
    target_label = Column(String(256), nullable=False)
    selector_json = Column(JSON, nullable=False)
    target_count = Column(Integer, nullable=False, default=0)
    launched_count = Column(Integer, nullable=False, default=0)
    skipped_count = Column(Integer, nullable=False, default=0)
    target_manifest_digest = Column(String(64), nullable=True, index=True)
    inventory_snapshot_digest = Column(String(64), nullable=True, index=True)
    scope_contract_version_id = Column(
        String(64),
        ForeignKey(
            "netops_patrol_scope_contract_versions.id",
            ondelete="RESTRICT",
        ),
        nullable=True,
        index=True,
    )
    scope_contract_digest = Column(String(64), nullable=True, index=True)
    intended_target_matrix_digest = Column(String(64), nullable=True, index=True)
    scope_decision_digest = Column(String(64), nullable=True, index=True)
    source_qualification_id = Column(
        String(64),
        ForeignKey(
            "netops_patrol_source_qualifications.id",
            ondelete="RESTRICT",
        ),
        nullable=True,
        index=True,
    )
    source_qualification_digest = Column(String(64), nullable=True, index=True)
    source_offline_job_id = Column(
        String(64),
        ForeignKey("netops_offline_patrol_jobs.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    source_authority_request_id = Column(
        String(64),
        ForeignKey(
            "netops_patrol_current_authority_requests.id",
            ondelete="RESTRICT",
            use_alter=True,
            name="fk_patrol_batch_current_authority_request",
        ),
        nullable=True,
        unique=True,
        index=True,
    )
    runtime_manifest_id = Column(String(64), nullable=True, index=True)
    runtime_manifest_digest = Column(String(64), nullable=True, index=True)
    report_manifest_id = Column(String(64), nullable=True, index=True)
    report_manifest_digest = Column(String(64), nullable=True, index=True)
    report_request_id = Column(String(64), nullable=True, index=True)
    save_baseline = Column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
    )
    dispatch_status = Column(
        String(32),
        nullable=False,
        default="pending",
        server_default="pending",
        index=True,
    )
    dispatch_requested_at = Column(DateTime(timezone=True), nullable=True)
    dispatch_started_at = Column(DateTime(timezone=True), nullable=True)
    dispatch_completed_at = Column(DateTime(timezone=True), nullable=True)
    dispatch_error = Column(Text, nullable=True)
    report_status = Column(
        String(32),
        nullable=False,
        default="not_requested",
        server_default="not_requested",
        index=True,
    )
    report_requested_at = Column(DateTime(timezone=True), nullable=True)
    report_completed_at = Column(DateTime(timezone=True), nullable=True)
    report_error = Column(Text, nullable=True)
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
        CheckConstraint(
            "report_purpose IN ("
            "'production_current', 'offline_historical', "
            "'shadow_validation', 'parser_validation', "
            "'rule_asset_replay'"
            ")",
            name="ck_patrol_batch_report_purpose",
        ),
        Index(
            "ix_netops_patrol_batches_archive_created_id",
            "archived",
            "created_at",
            "patrol_batch_id",
        ),
    )

    def to_dict(self):
        return {column.name: getattr(self, column.name) for column in self.__table__.columns}


class PatrolReportMaterializationRequestRecord(Base):
    """Durable idempotency and fencing ledger for report materialization."""

    __tablename__ = "netops_patrol_report_requests"

    id = Column(String(64), primary_key=True)
    patrol_batch_id = Column(
        String(128),
        ForeignKey("netops_patrol_batches.patrol_batch_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    idempotency_key = Column(String(128), nullable=False)
    task_id = Column(String(128), nullable=False, unique=True)
    status = Column(
        String(32),
        nullable=False,
        default="queued",
        server_default="queued",
        index=True,
    )
    requested_by = Column(String(256), nullable=False)
    error = Column(Text, nullable=True)
    report_manifest_id = Column(String(64), nullable=True, index=True)
    report_manifest_digest = Column(String(64), nullable=True, index=True)
    requested_at = Column(DateTime(timezone=True), nullable=False)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    updated_at = Column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        CheckConstraint(
            "status IN ('queued', 'generating', 'completed', 'failed')",
            name="ck_patrol_report_request_status",
        ),
        Index(
            "uq_patrol_report_request_batch_key",
            "patrol_batch_id",
            "idempotency_key",
            unique=True,
        ),
        Index(
            "ix_patrol_report_request_batch_status",
            "patrol_batch_id",
            "status",
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
    execution_plan_id = Column(
        String(64),
        ForeignKey("netops_collection_execution_plans.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    celery_task_id = Column(String(128), nullable=True, index=True)
    dispatch_status = Column(
        String(32),
        nullable=False,
        default="pending",
        server_default="pending",
        index=True,
    )
    dispatch_attempts = Column(Integer, nullable=False, default=0, server_default="0")
    dispatch_claim_token = Column(String(64), nullable=True, index=True)
    dispatch_lease_until = Column(DateTime(timezone=True), nullable=True, index=True)
    dispatched_at = Column(DateTime(timezone=True), nullable=True)
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

    __table_args__ = (
        Index("uq_netops_workflow_step_run_index", "run_id", "step_index", unique=True),
    )


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
    purpose = Column(
        String(32),
        nullable=False,
        default="offline_patrol",
        server_default="offline_patrol",
    )
    job_type = Column(String(32), nullable=False)
    report_intent = Column(
        String(32),
        nullable=False,
        default="historical",
        server_default="historical",
        index=True,
    )
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
        CheckConstraint(
            "report_intent IN ('historical', 'current_attestation')",
            name="ck_offline_patrol_job_report_intent",
        ),
        Index("ix_offline_patrol_jobs_idempotency", "idempotency_key"),
        Index("ix_offline_patrol_jobs_purpose_created", "purpose", "created_at"),
        Index("ix_offline_patrol_jobs_owner_created", "owner_id", "created_at"),
        Index("ix_offline_patrol_jobs_status_created", "status", "created_at"),
        Index("ix_offline_patrol_jobs_archived", "archived"),
        Index("ix_offline_patrol_jobs_workflow", "workflow_run_id"),
        Index("ix_offline_jobs_workspace_page", "purpose", "archived", created_at.desc(), id.desc()),
        Index("ix_offline_jobs_owner_page", "owner_id", "purpose", "archived", created_at.desc(), id.desc()),
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
    collection_action_id = Column(String(64), nullable=True, index=True)
    implementation_version_id = Column(String(64), nullable=True, index=True)
    check_item_ids_json = Column(JSON, nullable=True)
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
    collection_action_id = Column(String(64), nullable=True, index=True)
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
    source = Column(
        String(32),
        nullable=False,
        default="cli",
        index=True,
        comment="cli|gnmi|snmp|syslog",
    )
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
    state_key = Column(String(64), nullable=True, index=True)
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
    dimensions_json = Column(JSON, nullable=True)
    dimension_signature = Column(String(64), nullable=True, index=True)
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
        Index("ix_state_observation_device_history", "mode", "device_id", observed_at.desc()),
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
    dimensions_json = Column(JSON, nullable=True)
    dimension_signature = Column(String(64), nullable=True, index=True)
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
    check_item_id = Column(String(128), nullable=True, index=True)
    check_item_version_id = Column(String(64), nullable=True, index=True)
    check_execution_id = Column(String(64), nullable=True, index=True)
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
    check_item_id = Column(String(128), nullable=True, index=True)
    check_item_version_id = Column(String(64), nullable=True, index=True)
    check_execution_id = Column(String(64), nullable=True, index=True)
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
    check_item_version_id = Column(
        String(64),
        ForeignKey("netops_check_item_versions.id", ondelete="SET NULL"),
        nullable=True,
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
    evaluation_scope = Column(
        String(32), nullable=False, default="both", server_default="both", index=True
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
        CheckConstraint(
            "evaluation_scope IN ('current_state', 'change', 'both')",
            name="ck_event_rule_version_evaluation_scope",
        ),
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


class PatrolScopeContractRecord(Base):
    """Stable identity for a reusable patrol scope contract."""

    __tablename__ = "netops_patrol_scope_contracts"

    id = Column(String(128), primary_key=True)
    name = Column(String(256), nullable=False)
    status = Column(
        String(32), nullable=False, default="draft", server_default="draft", index=True
    )
    current_version = Column(Integer, nullable=False, default=0, server_default="0")
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
        CheckConstraint(
            "status IN ('draft', 'active', 'deprecated', 'frozen')",
            name="ck_patrol_scope_contract_status",
        ),
    )

    def to_dict(self):
        return {column.name: getattr(self, column.name) for column in self.__table__.columns}


class PatrolScopeContractVersionRecord(Base):
    """Immutable scope policy used to derive one intended target matrix."""

    __tablename__ = "netops_patrol_scope_contract_versions"

    id = Column(String(64), primary_key=True)
    scope_contract_id = Column(
        String(128),
        ForeignKey("netops_patrol_scope_contracts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    version = Column(Integer, nullable=False)
    report_purpose = Column(String(32), nullable=True, index=True)
    inventory_selector_json = Column(JSON, nullable=False, default=dict)
    device_role_rules_json = Column(JSON, nullable=False, default=list)
    explicit_required_by_device_json = Column(JSON, nullable=False, default=dict)
    allowed_exclusion_codes_json = Column(JSON, nullable=False, default=list)
    claim_boundary = Column(String(64), nullable=False)
    origin = Column(String(64), nullable=False)
    contract_json = Column(JSON, nullable=False)
    digest = Column(String(64), nullable=False, index=True)
    status = Column(
        String(32), nullable=False, default="frozen", server_default="frozen", index=True
    )
    created_by = Column(String(128), nullable=True)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('draft', 'active', 'deprecated', 'frozen')",
            name="ck_patrol_scope_contract_version_status",
        ),
        Index(
            "uq_patrol_scope_contract_version",
            "scope_contract_id",
            "version",
            unique=True,
        ),
    )

    def to_dict(self):
        return {column.name: getattr(self, column.name) for column in self.__table__.columns}


class PatrolIntendedTargetMatrixRecord(Base):
    """Frozen device x required-check-item matrix for one patrol batch."""

    __tablename__ = "netops_intended_target_matrices"

    id = Column(String(64), primary_key=True)
    patrol_batch_id = Column(
        String(128),
        ForeignKey("netops_patrol_batches.patrol_batch_id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    scope_contract_version_id = Column(
        String(64),
        ForeignKey("netops_patrol_scope_contract_versions.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    inventory_snapshot_digest = Column(String(64), nullable=False, index=True)
    matrix_json = Column(JSON, nullable=False)
    digest = Column(String(64), nullable=False, index=True)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    def to_dict(self):
        return {column.name: getattr(self, column.name) for column in self.__table__.columns}


class PatrolScopeDecisionRecord(Base):
    """One immutable disposition for an intended or selected target pair."""

    __tablename__ = "netops_scope_decisions"

    id = Column(String(64), primary_key=True)
    patrol_batch_id = Column(
        String(128),
        ForeignKey("netops_patrol_batches.patrol_batch_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    intended_target_matrix_id = Column(
        String(64),
        ForeignKey("netops_intended_target_matrices.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    device_id = Column(String(256), nullable=False, index=True)
    check_item_version_id = Column(
        String(64),
        ForeignKey("netops_check_item_versions.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    check_item_id = Column(String(128), nullable=False, index=True)
    decision = Column(String(32), nullable=False, index=True)
    requirement = Column(String(32), nullable=False)
    reason_codes_json = Column(JSON, nullable=False, default=list)
    evidence_json = Column(JSON, nullable=False, default=dict)
    digest = Column(String(64), nullable=False, index=True)
    decided_by = Column(String(128), nullable=True)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        CheckConstraint(
            "decision IN ('included', 'approved_exclusion', 'not_applicable', 'unresolved')",
            name="ck_patrol_scope_decision",
        ),
        Index(
            "uq_patrol_scope_decision_pair",
            "patrol_batch_id",
            "device_id",
            "check_item_version_id",
            unique=True,
        ),
    )

    def to_dict(self):
        return {column.name: getattr(self, column.name) for column in self.__table__.columns}


class PatrolSourceQualificationRecord(Base):
    """Immutable evidence qualification for an offline patrol source."""

    __tablename__ = "netops_patrol_source_qualifications"

    id = Column(String(64), primary_key=True)
    request_id = Column(
        String(64),
        ForeignKey(
            "netops_patrol_current_authority_requests.id",
            ondelete="RESTRICT",
            use_alter=True,
            name="fk_source_qualification_authority_request",
        ),
        nullable=True,
        index=True,
    )
    request_digest = Column(String(64), nullable=True, index=True)
    patrol_batch_id = Column(String(128), nullable=False, index=True)
    offline_job_id = Column(
        String(64),
        ForeignKey("netops_offline_patrol_jobs.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    version = Column(Integer, nullable=False)
    acquisition_mode = Column(String(32), nullable=False, index=True)
    source_kind = Column(String(64), nullable=False, index=True)
    status = Column(
        String(32),
        nullable=False,
        default="approved",
        server_default="approved",
        index=True,
    )
    observed_at_min = Column(DateTime(timezone=True), nullable=False)
    observed_at_max = Column(DateTime(timezone=True), nullable=False)
    chain_of_custody_digest = Column(String(64), nullable=False, index=True)
    identity_binding_digest = Column(String(64), nullable=False, index=True)
    scope_contract_digest = Column(String(64), nullable=False, index=True)
    intended_target_matrix_digest = Column(String(64), nullable=False, index=True)
    completeness_evidence_digest = Column(String(64), nullable=False, index=True)
    qualification_json = Column(JSON, nullable=False)
    digest = Column(String(64), nullable=False, unique=True, index=True)
    imported_by = Column(String(128), nullable=False)
    attested_by = Column(String(128), nullable=False)
    imported_by_principal_id = Column(String(128), nullable=True, index=True)
    imported_by_display = Column(String(256), nullable=True)
    attested_by_principal_id = Column(String(128), nullable=True, index=True)
    attested_by_display = Column(String(256), nullable=True)
    attestation_idempotency_key_hash = Column(
        String(64), nullable=True, unique=True, index=True
    )
    attestation_request_digest = Column(String(64), nullable=True, index=True)
    supersedes_id = Column(
        String(64),
        ForeignKey(
            "netops_patrol_source_qualifications.id",
            ondelete="RESTRICT",
        ),
        nullable=True,
        index=True,
    )
    version_reason = Column(Text, nullable=True)
    attested_at = Column(DateTime(timezone=True), nullable=False)
    revoked_by = Column(String(128), nullable=True)
    revoked_by_principal_id = Column(String(128), nullable=True, index=True)
    revoked_by_display = Column(String(256), nullable=True)
    revocation_idempotency_key_hash = Column(
        String(64), nullable=True, unique=True, index=True
    )
    revocation_request_digest = Column(String(64), nullable=True, index=True)
    revoked_at = Column(DateTime(timezone=True), nullable=True)
    revocation_reason = Column(Text, nullable=True)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        CheckConstraint(
            "acquisition_mode IN ('offline_upload')",
            name="ck_patrol_source_qualification_acquisition",
        ),
        CheckConstraint(
            "source_kind IN ('attested_offline_current')",
            name="ck_patrol_source_qualification_kind",
        ),
        CheckConstraint(
            "status IN ('approved', 'revoked')",
            name="ck_patrol_source_qualification_status",
        ),
        Index(
            "uq_patrol_source_qualification_version",
            "patrol_batch_id",
            "version",
            unique=True,
        ),
        CheckConstraint(
            "imported_by_principal_id IS NULL OR "
            "attested_by_principal_id IS NULL OR "
            "imported_by_principal_id <> attested_by_principal_id",
            name="ck_patrol_source_qualification_independent_actor",
        ),
    )

    def to_dict(self):
        return {column.name: getattr(self, column.name) for column in self.__table__.columns}


class PatrolCurrentAuthorityRequestRecord(Base):
    """Immutable request ledger for qualifying one offline source as current."""

    __tablename__ = "netops_patrol_current_authority_requests"

    id = Column(String(64), primary_key=True)
    offline_job_id = Column(
        String(64),
        ForeignKey("netops_offline_patrol_jobs.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    candidate_batch_id = Column(
        String(128),
        ForeignKey(
            "netops_patrol_batches.patrol_batch_id",
            ondelete="RESTRICT",
            use_alter=True,
            name="fk_current_authority_candidate_batch",
        ),
        nullable=True,
        unique=True,
        index=True,
    )
    status = Column(String(32), nullable=False, index=True)
    blocker_codes_json = Column(JSON, nullable=False, default=list)
    idempotency_scope = Column(String(128), nullable=False)
    idempotency_key_hash = Column(
        String(64), nullable=False, unique=True, index=True
    )
    request_digest = Column(String(64), nullable=False, unique=True, index=True)
    source_manifest_digest = Column(String(64), nullable=False, index=True)
    observed_at_digest = Column(String(64), nullable=False, index=True)
    identity_binding_digest = Column(String(64), nullable=False, index=True)
    scope_contract_version_id = Column(String(64), nullable=True, index=True)
    scope_contract_digest = Column(String(64), nullable=False, index=True)
    intended_target_matrix_digest = Column(String(64), nullable=False, index=True)
    scope_decision_digest = Column(String(64), nullable=False, index=True)
    completeness_evidence_digest = Column(String(64), nullable=False, index=True)
    request_json = Column(JSON, nullable=False)
    requested_by_principal_id = Column(String(128), nullable=False, index=True)
    requested_by_display = Column(String(256), nullable=False)
    prepared_at = Column(DateTime(timezone=True), nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=True, index=True)
    approved_qualification_id = Column(
        String(64),
        ForeignKey(
            "netops_patrol_source_qualifications.id",
            ondelete="RESTRICT",
            use_alter=True,
            name="fk_current_authority_approved_qualification",
        ),
        nullable=True,
        index=True,
    )
    created_at = Column(DateTime(timezone=True), nullable=False)
    updated_at = Column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        CheckConstraint(
            "status IN ('blocked', 'ready_for_attestation', 'attesting', "
            "'approved', 'rejected', 'dispatching', 'completed', 'failed', "
            "'revoked', 'expired')",
            name="ck_patrol_current_authority_request_status",
        ),
        Index(
            "ix_patrol_current_authority_job_created",
            "offline_job_id",
            "created_at",
        ),
    )

    def to_dict(self):
        return {
            column.name: getattr(self, column.name)
            for column in self.__table__.columns
        }


class CheckItemRecord(Base):
    """Stable business identity for one patrol check item."""

    __tablename__ = "netops_check_items"

    id = Column(String(128), primary_key=True)
    name = Column(String(256), nullable=False)
    category = Column(String(64), nullable=False, index=True)
    description = Column(Text, nullable=True)
    status = Column(
        String(32), nullable=False, default="draft", server_default="draft", index=True
    )
    current_version = Column(Integer, nullable=False, default=0, server_default="0")
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

    def to_dict(self):
        return {column.name: getattr(self, column.name) for column in self.__table__.columns}


class CheckItemVersionRecord(Base):
    """Immutable version of check-item semantics and report behavior."""

    __tablename__ = "netops_check_item_versions"

    id = Column(String(64), primary_key=True)
    check_item_id = Column(
        String(128),
        ForeignKey("netops_check_items.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    version = Column(Integer, nullable=False)
    result_mode = Column(String(32), nullable=False, index=True)
    assessment_mode = Column(String(32), nullable=False, index=True)
    canonical_contract_json = Column(JSON, nullable=False)
    report_projection = Column(String(128), nullable=False, index=True)
    applicability_json = Column(JSON, nullable=True)
    freshness_policy_json = Column(JSON, nullable=True)
    empty_result_policy_json = Column(JSON, nullable=True)
    coverage_policy_json = Column(JSON, nullable=True)
    status = Column(
        String(32), nullable=False, default="draft", server_default="draft", index=True
    )
    digest = Column(String(64), nullable=False, index=True)
    created_by = Column(String(128), nullable=True)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    activated_at = Column(DateTime(timezone=True), nullable=True)
    deprecated_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint(
            "result_mode IN ('structured', 'normalized_diff', 'hash_only', 'evidence_only')",
            name="ck_check_item_version_result_mode",
        ),
        CheckConstraint(
            "assessment_mode IN ('health', 'change', 'evidence')",
            name="ck_check_item_version_assessment_mode",
        ),
        CheckConstraint(
            "status IN ('draft', 'active', 'deprecated')",
            name="ck_check_item_version_status",
        ),
        Index(
            "uq_check_item_version",
            "check_item_id",
            "version",
            unique=True,
        ),
        Index(
            "uq_check_item_active_version",
            "check_item_id",
            unique=True,
            postgresql_where=text("status = 'active'"),
            sqlite_where=text("status = 'active'"),
        ),
    )

    def to_dict(self):
        return {column.name: getattr(self, column.name) for column in self.__table__.columns}


class CheckItemCatalogChangeRecord(Base):
    """Governed lifecycle for one immutable check-item catalog revision."""

    __tablename__ = "netops_check_item_catalog_changes"

    id = Column(String(64), primary_key=True)
    asset_kind = Column(String(32), nullable=False, index=True)
    asset_id = Column(String(128), nullable=False, index=True)
    version_id = Column(String(64), nullable=False, unique=True, index=True)
    version = Column(Integer, nullable=False)
    baseline_version_id = Column(String(64), nullable=True, index=True)
    status = Column(
        String(32), nullable=False, default="draft", server_default="draft", index=True
    )
    payload_digest = Column(String(64), nullable=False, index=True)
    candidate_payload_json = Column(JSON, nullable=False)
    impact_snapshot_json = Column(JSON, nullable=True)
    impact_digest = Column(String(64), nullable=True, index=True)
    created_by = Column(String(128), nullable=False, index=True)
    creation_reason = Column(Text, nullable=False)
    creation_idempotency_key = Column(String(128), nullable=False, unique=True)
    request_digest = Column(String(64), nullable=False)
    submitted_by = Column(String(128), nullable=True, index=True)
    submission_reason = Column(Text, nullable=True)
    submission_idempotency_key = Column(String(128), nullable=True, unique=True)
    submitted_at = Column(DateTime(timezone=True), nullable=True)
    decided_by = Column(String(128), nullable=True, index=True)
    decision_reason = Column(Text, nullable=True)
    decision_idempotency_key = Column(String(128), nullable=True, unique=True)
    rollout_campaign_id = Column(String(64), nullable=True, index=True)
    cutover_bundle_version_ids_json = Column(JSON, nullable=True)
    cutover_evidence_json = Column(JSON, nullable=True)
    cutover_digest = Column(String(64), nullable=True, index=True)
    decided_at = Column(DateTime(timezone=True), nullable=True)
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
        CheckConstraint(
            "asset_kind IN ('check_item', 'collection_implementation')",
            name="ck_check_item_catalog_change_kind",
        ),
        CheckConstraint(
            "status IN ('draft', 'pending_approval', 'approved', 'rejected', 'cancelled')",
            name="ck_check_item_catalog_change_status",
        ),
        Index(
            "uq_check_item_catalog_change_asset_version",
            "asset_kind",
            "asset_id",
            "version",
            unique=True,
        ),
    )

    def to_dict(self):
        return {column.name: getattr(self, column.name) for column in self.__table__.columns}


class AssessmentPolicyProfileRecord(Base):
    """Stable identity for one governed check-assessment policy role."""

    __tablename__ = "netops_assessment_policy_profiles"

    id = Column(String(64), primary_key=True)
    check_item_id = Column(
        String(128),
        ForeignKey("netops_check_items.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    observation_type = Column(String(160), nullable=False, index=True)
    policy_role = Column(String(32), nullable=False, index=True)
    name = Column(String(256), nullable=False)
    description = Column(Text, nullable=True)
    status = Column(
        String(32), nullable=False, default="draft", server_default="draft", index=True
    )
    current_version = Column(Integer, nullable=False, default=0, server_default="0")
    created_by = Column(String(128), nullable=False)
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
        CheckConstraint(
            "policy_role IN ('high', 'critical_high', 'low', 'critical_low', "
            "'expected_state', 'applicability')",
            name="ck_assessment_policy_profile_role",
        ),
        CheckConstraint(
            "status IN ('draft', 'active', 'deprecated')",
            name="ck_assessment_policy_profile_status",
        ),
        Index(
            "uq_assessment_policy_profile_identity",
            "check_item_id",
            "observation_type",
            "policy_role",
            "name",
            unique=True,
        ),
    )

    def to_dict(self):
        return {column.name: getattr(self, column.name) for column in self.__table__.columns}


class AssessmentPolicyVersionRecord(Base):
    """Immutable threshold, expected-state or applicability policy candidate."""

    __tablename__ = "netops_assessment_policy_versions"

    id = Column(String(64), primary_key=True)
    profile_id = Column(
        String(64),
        ForeignKey("netops_assessment_policy_profiles.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    check_item_version_id = Column(
        String(64),
        ForeignKey("netops_check_item_versions.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    version = Column(Integer, nullable=False)
    policy_json = Column(JSON, nullable=False)
    evidence_refs_json = Column(JSON, nullable=False)
    provenance_json = Column(JSON, nullable=False)
    valid_from = Column(DateTime(timezone=True), nullable=True)
    valid_until = Column(DateTime(timezone=True), nullable=True)
    digest = Column(String(64), nullable=False, unique=True, index=True)
    status = Column(
        String(32), nullable=False, default="draft", server_default="draft", index=True
    )
    idempotency_key = Column(String(128), nullable=False, unique=True)
    request_digest = Column(String(64), nullable=False)
    created_by = Column(String(128), nullable=False)
    submitted_at = Column(DateTime(timezone=True), nullable=True)
    semantic_reviewed_by = Column(String(128), nullable=True)
    semantic_review_reason = Column(Text, nullable=True)
    semantic_reviewed_at = Column(DateTime(timezone=True), nullable=True)
    approved_by = Column(String(128), nullable=True)
    approval_reason = Column(Text, nullable=True)
    approved_at = Column(DateTime(timezone=True), nullable=True)
    revoked_by = Column(String(128), nullable=True)
    revoke_reason = Column(Text, nullable=True)
    revoked_at = Column(DateTime(timezone=True), nullable=True)
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
        CheckConstraint(
            "status IN ('draft', 'pending_review', 'pending_approval', 'approved', "
            "'rejected', 'superseded', 'revoked')",
            name="ck_assessment_policy_version_status",
        ),
        CheckConstraint(
            "valid_until IS NULL OR valid_from IS NULL OR valid_from < valid_until",
            name="ck_assessment_policy_version_validity",
        ),
        Index(
            "uq_assessment_policy_version",
            "profile_id",
            "version",
            unique=True,
        ),
    )

    def to_dict(self):
        return {column.name: getattr(self, column.name) for column in self.__table__.columns}


class AssessmentPolicyDecisionRecord(Base):
    """Append-only semantic-review and publication decisions."""

    __tablename__ = "netops_assessment_policy_decisions"

    id = Column(String(64), primary_key=True)
    policy_version_id = Column(
        String(64),
        ForeignKey("netops_assessment_policy_versions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    stage = Column(String(32), nullable=False, index=True)
    decision = Column(String(32), nullable=False, index=True)
    actor = Column(String(128), nullable=False, index=True)
    reason = Column(Text, nullable=False)
    policy_digest = Column(String(64), nullable=False)
    idempotency_key = Column(String(128), nullable=False, unique=True)
    request_digest = Column(String(64), nullable=False)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        CheckConstraint(
            "stage IN ('submission', 'semantic_review', 'publication', 'revocation')",
            name="ck_assessment_policy_decision_stage",
        ),
        CheckConstraint(
            "decision IN ('submitted', 'approved', 'rejected', 'revoked')",
            name="ck_assessment_policy_decision_value",
        ),
    )

    def to_dict(self):
        return {column.name: getattr(self, column.name) for column in self.__table__.columns}


class AssessmentPolicyBindingRecord(Base):
    """Time-bounded binding of an approved policy version to a governed scope."""

    __tablename__ = "netops_assessment_policy_bindings"

    id = Column(String(64), primary_key=True)
    profile_id = Column(
        String(64),
        ForeignKey("netops_assessment_policy_profiles.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    policy_version_id = Column(
        String(64),
        ForeignKey("netops_assessment_policy_versions.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    check_item_id = Column(
        String(128),
        ForeignKey("netops_check_items.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    observation_type = Column(String(160), nullable=False, index=True)
    policy_role = Column(String(32), nullable=False, index=True)
    binding_type = Column(String(32), nullable=False, index=True)
    scope_json = Column(JSON, nullable=False)
    scope_digest = Column(String(64), nullable=False, index=True)
    binding_digest = Column(String(64), nullable=False, unique=True, index=True)
    valid_from = Column(DateTime(timezone=True), nullable=True)
    valid_until = Column(DateTime(timezone=True), nullable=True)
    status = Column(
        String(32), nullable=False, default="active", server_default="active", index=True
    )
    idempotency_key = Column(String(128), nullable=False, unique=True)
    request_digest = Column(String(64), nullable=False)
    created_by = Column(String(128), nullable=False)
    reason = Column(Text, nullable=False)
    ended_by = Column(String(128), nullable=True)
    end_reason = Column(Text, nullable=True)
    ended_at = Column(DateTime(timezone=True), nullable=True)
    end_idempotency_key = Column(String(128), nullable=True, unique=True)
    end_request_digest = Column(String(64), nullable=True)
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
        CheckConstraint(
            "policy_role IN ('high', 'critical_high', 'low', 'critical_low', "
            "'expected_state', 'applicability')",
            name="ck_assessment_policy_binding_role",
        ),
        CheckConstraint(
            "binding_type IN ('inventory_profile', 'scope_override', 'catalog_default')",
            name="ck_assessment_policy_binding_type",
        ),
        CheckConstraint(
            "status IN ('active', 'ended', 'revoked')",
            name="ck_assessment_policy_binding_status",
        ),
        CheckConstraint(
            "valid_until IS NULL OR valid_from IS NULL OR valid_from < valid_until",
            name="ck_assessment_policy_binding_validity",
        ),
        Index(
            "uq_assessment_policy_active_exact_scope",
            "check_item_id",
            "observation_type",
            "policy_role",
            "binding_type",
            "scope_digest",
            unique=True,
            postgresql_where=text("status = 'active'"),
            sqlite_where=text("status = 'active'"),
        ),
    )

    def to_dict(self):
        return {column.name: getattr(self, column.name) for column in self.__table__.columns}


class AssessmentPolicyMigrationSourceRecord(Base):
    """Immutable inventory entry for one legacy assessment-policy source."""

    __tablename__ = "netops_assessment_policy_migration_sources"

    id = Column(String(64), primary_key=True)
    source_type = Column(String(32), nullable=False, index=True)
    source_locator = Column(String(512), nullable=False)
    source_digest = Column(String(64), nullable=False, unique=True, index=True)
    check_item_id = Column(String(128), nullable=True, index=True)
    check_item_version_id = Column(String(64), nullable=True, index=True)
    device_id = Column(String(256), nullable=True, index=True)
    observation_type = Column(String(160), nullable=True, index=True)
    policy_role = Column(String(32), nullable=True, index=True)
    binding_type = Column(String(32), nullable=True, index=True)
    scope_json = Column(JSON, nullable=False, default=dict)
    source_payload_json = Column(JSON, nullable=False)
    conversion_status = Column(
        String(32),
        nullable=False,
        default="inventoried",
        server_default="inventoried",
        index=True,
    )
    reason_codes_json = Column(JSON, nullable=False, default=list)
    inventoried_by = Column(String(128), nullable=False)
    draft_policy_version_id = Column(
        String(64),
        ForeignKey("netops_assessment_policy_versions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    first_seen_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    last_seen_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    converted_at = Column(DateTime(timezone=True), nullable=True)
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
        CheckConstraint(
            "source_type IN ('inventory_attribute', 'scope_configuration', "
            "'catalog_default', 'check_item_applicability', "
            "'check_item_empty_policy', 'execution_snapshot', 'rule_constant')",
            name="ck_assessment_policy_migration_source_type",
        ),
        CheckConstraint(
            "conversion_status IN ('inventoried', 'draft_created', "
            "'review_required', 'ignored', 'cutover', 'retired')",
            name="ck_assessment_policy_migration_conversion_status",
        ),
    )

    def to_dict(self):
        return {column.name: getattr(self, column.name) for column in self.__table__.columns}


class AssessmentPolicyShadowResultRecord(Base):
    """Immutable comparison of legacy and governed policy resolution."""

    __tablename__ = "netops_assessment_policy_shadow_results"

    id = Column(String(64), primary_key=True)
    input_digest = Column(String(64), nullable=False, unique=True, index=True)
    check_item_id = Column(String(128), nullable=False, index=True)
    check_item_version_id = Column(String(64), nullable=False, index=True)
    device_id = Column(String(256), nullable=False, index=True)
    observation_type = Column(String(160), nullable=False, index=True)
    rule_version_id = Column(String(128), nullable=True, index=True)
    legacy_snapshot_digest = Column(String(64), nullable=False, index=True)
    governed_snapshot_digest = Column(String(64), nullable=False, index=True)
    legacy_result_json = Column(JSON, nullable=False)
    governed_result_json = Column(JSON, nullable=False)
    difference_json = Column(JSON, nullable=False)
    comparison_status = Column(String(32), nullable=False, index=True)
    created_by = Column(String(128), nullable=False)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        CheckConstraint(
            "comparison_status IN ('matched', 'different', "
            "'governed_missing', 'legacy_missing', 'error')",
            name="ck_assessment_policy_shadow_result_status",
        ),
        Index(
            "ix_assessment_policy_shadow_scope",
            "check_item_version_id",
            "device_id",
            "created_at",
        ),
    )

    def to_dict(self):
        return {column.name: getattr(self, column.name) for column in self.__table__.columns}


class AssessmentPolicyCutoverRecord(Base):
    """Append-only target-level resolver cutover and rollback ledger."""

    __tablename__ = "netops_assessment_policy_cutovers"

    id = Column(String(64), primary_key=True)
    check_item_id = Column(String(128), nullable=False, index=True)
    check_item_version_id = Column(
        String(64),
        ForeignKey("netops_check_item_versions.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    device_id = Column(
        String(256),
        ForeignKey("netops_devices.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    resolver_mode = Column(String(32), nullable=False, index=True)
    status = Column(
        String(32), nullable=False, default="active", server_default="active", index=True
    )
    binding_ids_json = Column(JSON, nullable=False, default=list)
    shadow_result_ids_json = Column(JSON, nullable=False, default=list)
    evidence_digest = Column(String(64), nullable=False, index=True)
    idempotency_key = Column(String(128), nullable=False, unique=True)
    request_digest = Column(String(64), nullable=False)
    created_by = Column(String(128), nullable=False)
    reason = Column(Text, nullable=False)
    supersedes_id = Column(
        String(64),
        ForeignKey("netops_assessment_policy_cutovers.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    superseded_by_id = Column(
        String(64),
        ForeignKey("netops_assessment_policy_cutovers.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    superseded_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        CheckConstraint(
            "resolver_mode IN ('legacy_compat', 'governed_only')",
            name="ck_assessment_policy_cutover_mode",
        ),
        CheckConstraint(
            "status IN ('active', 'superseded')",
            name="ck_assessment_policy_cutover_status",
        ),
        Index(
            "uq_assessment_policy_cutover_active_target",
            "check_item_version_id",
            "device_id",
            unique=True,
            postgresql_where=text("status = 'active'"),
            sqlite_where=text("status = 'active'"),
        ),
        Index(
            "ix_assessment_policy_cutover_history",
            "check_item_version_id",
            "device_id",
            "created_at",
        ),
    )

    def to_dict(self):
        return {column.name: getattr(self, column.name) for column in self.__table__.columns}


class CollectionImplementationRecord(Base):
    """Stable identity for a vendor/platform-specific collection implementation."""

    __tablename__ = "netops_collection_implementations"

    id = Column(String(128), primary_key=True)
    name = Column(String(256), nullable=False)
    description = Column(Text, nullable=True)
    status = Column(
        String(32), nullable=False, default="draft", server_default="draft", index=True
    )
    current_version = Column(Integer, nullable=False, default=0, server_default="0")
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

    def to_dict(self):
        return {column.name: getattr(self, column.name) for column in self.__table__.columns}


class CollectionImplementationVersionRecord(Base):
    """Immutable compatibility selector and command chain for one implementation."""

    __tablename__ = "netops_collection_implementation_versions"

    id = Column(String(64), primary_key=True)
    implementation_id = Column(
        String(128),
        ForeignKey("netops_collection_implementations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    version = Column(Integer, nullable=False)
    collector = Column(String(32), nullable=False, default="cli", server_default="cli")
    match_json = Column(JSON, nullable=False)
    commands_json = Column(JSON, nullable=False)
    parser_requirement = Column(String(32), nullable=False, index=True)
    template_scope = Column(String(32), nullable=True)
    adapter_spec_json = Column(JSON, nullable=True)
    output_quality_policy_json = Column(JSON, nullable=True)
    selection_priority = Column(Integer, nullable=False, default=100, server_default="100")
    status = Column(
        String(32), nullable=False, default="draft", server_default="draft", index=True
    )
    digest = Column(String(64), nullable=False, index=True)
    created_by = Column(String(128), nullable=True)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    activated_at = Column(DateTime(timezone=True), nullable=True)
    deprecated_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint(
            "parser_requirement IN ('structured', 'normalized_diff', 'hash_only', 'evidence_only')",
            name="ck_collection_impl_parser_requirement",
        ),
        CheckConstraint(
            "status IN ('draft', 'active', 'deprecated')",
            name="ck_collection_impl_version_status",
        ),
        Index(
            "uq_collection_implementation_version",
            "implementation_id",
            "version",
            unique=True,
        ),
        Index(
            "uq_collection_implementation_active",
            "implementation_id",
            unique=True,
            postgresql_where=text("status = 'active'"),
            sqlite_where=text("status = 'active'"),
        ),
    )

    def to_dict(self):
        return {column.name: getattr(self, column.name) for column in self.__table__.columns}


class CheckItemImplementationLinkRecord(Base):
    """Normalized many-to-many relation between immutable asset versions."""

    __tablename__ = "netops_check_item_implementation_links"

    id = Column(String(64), primary_key=True)
    check_item_version_id = Column(
        String(64),
        ForeignKey("netops_check_item_versions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    implementation_version_id = Column(
        String(64),
        ForeignKey("netops_collection_implementation_versions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    priority = Column(Integer, nullable=False, default=100, server_default="100")
    required = Column(Boolean, nullable=False, default=True, server_default=text("true"))
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        Index(
            "uq_check_item_implementation_link",
            "check_item_version_id",
            "implementation_version_id",
            unique=True,
        ),
    )

    def to_dict(self):
        return {column.name: getattr(self, column.name) for column in self.__table__.columns}


class CollectionImplementationParserBindingRecord(Base):
    """Approved Parser binding for one implementation command."""

    __tablename__ = "netops_collection_implementation_parsers"

    id = Column(String(64), primary_key=True)
    implementation_version_id = Column(
        String(64),
        ForeignKey("netops_collection_implementation_versions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    canonical_command = Column(String(512), nullable=False)
    parser_template_id = Column(
        String(64),
        ForeignKey("parser_templates.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    record_set = Column(String(64), nullable=False, default="default", server_default="default")
    binding_role = Column(String(32), nullable=False, default="primary", server_default="primary")
    output_signature = Column(String(64), nullable=True, index=True)
    status = Column(
        String(32), nullable=False, default="draft", server_default="draft", index=True
    )
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
            "uq_collection_implementation_parser",
            "implementation_version_id",
            "canonical_command",
            "record_set",
            "binding_role",
            unique=True,
        ),
    )

    def to_dict(self):
        return {column.name: getattr(self, column.name) for column in self.__table__.columns}


class ComparisonContextRecord(Base):
    """Immutable baseline-selection decision for one device and check item."""

    __tablename__ = "netops_comparison_contexts"

    id = Column(String(64), primary_key=True)
    digest = Column(String(64), nullable=False, unique=True, index=True)
    mode = Column(String(32), nullable=False, index=True)
    device_id = Column(String(256), nullable=False, index=True)
    check_item_version_id = Column(
        String(64),
        ForeignKey("netops_check_item_versions.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    current_observed_at = Column(DateTime(timezone=True), nullable=True, index=True)
    baseline_observed_at = Column(DateTime(timezone=True), nullable=True, index=True)
    baseline_run_id = Column(String(128), nullable=True, index=True)
    baseline_snapshot_ids_json = Column(JSON, nullable=True)
    max_baseline_age_seconds = Column(Integer, nullable=True)
    canonical_schema_version = Column(String(128), nullable=True)
    entity_identity_policy_version = Column(String(64), nullable=True)
    selection_reason = Column(String(256), nullable=False)
    metadata_json = Column(JSON, nullable=True)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        CheckConstraint(
            "mode IN ('online_previous_success', 'explicit_periods', 'no_baseline')",
            name="ck_comparison_context_mode",
        ),
    )

    def to_dict(self):
        return {column.name: getattr(self, column.name) for column in self.__table__.columns}


class CheckExecutionRecord(Base):
    """Execution fact for one batch, device and immutable check-item version."""

    __tablename__ = "netops_check_executions"

    id = Column(String(64), primary_key=True)
    idempotency_key = Column(String(128), nullable=False, unique=True)
    patrol_batch_id = Column(String(128), nullable=False, index=True)
    device_id = Column(String(256), nullable=False, index=True)
    check_item_version_id = Column(
        String(64),
        ForeignKey("netops_check_item_versions.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    implementation_version_id = Column(
        String(64),
        ForeignKey("netops_collection_implementation_versions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    collection_action_ids_json = Column(JSON, nullable=False, default=list)
    runtime_bundle_id = Column(String(64), nullable=True, index=True)
    runtime_bundle_version_id = Column(String(64), nullable=True, index=True)
    runtime_bundle_digest = Column(String(64), nullable=True, index=True)
    assessment_policy_snapshot_json = Column(JSON, nullable=False, default=dict)
    assessment_policy_snapshot_digest = Column(String(64), nullable=True, index=True)
    comparison_context_id = Column(
        String(64),
        ForeignKey("netops_comparison_contexts.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    execution_status = Column(
        String(32), nullable=False, default="planned", server_default="planned", index=True
    )
    reason_codes_json = Column(JSON, nullable=False, default=list)
    observation_count = Column(Integer, nullable=False, default=0, server_default="0")
    observed_entity_count = Column(Integer, nullable=False, default=0, server_default="0")
    change_count = Column(Integer, nullable=False, default=0, server_default="0")
    event_count = Column(Integer, nullable=False, default=0, server_default="0")
    current_finding_count = Column(Integer, nullable=False, default=0, server_default="0")
    assessment_id = Column(String(64), nullable=True, index=True)
    evidence_qualification_id = Column(String(64), nullable=True, index=True)
    coverage_decision_id = Column(String(64), nullable=True, index=True)
    source_identity_status = Column(
        String(32), nullable=False, default="unknown", server_default="unknown", index=True
    )
    output_quality_status = Column(
        String(32), nullable=False, default="unknown", server_default="unknown", index=True
    )
    freshness_status = Column(
        String(32), nullable=False, default="unknown", server_default="unknown", index=True
    )
    empty_result_classification = Column(
        String(32),
        nullable=False,
        default="not_evaluated",
        server_default="not_evaluated",
        index=True,
    )
    coverage_status = Column(
        String(32), nullable=False, default="unknown", server_default="unknown", index=True
    )
    coverage_basis = Column(
        String(32), nullable=False, default="unknown", server_default="unknown", index=True
    )
    risk_verification_status = Column(
        String(32), nullable=False, default="unknown", server_default="unknown", index=True
    )
    expected_entity_count = Column(Integer, nullable=True)
    observation_min_at = Column(DateTime(timezone=True), nullable=True, index=True)
    observation_max_at = Column(DateTime(timezone=True), nullable=True, index=True)
    target_manifest_digest = Column(String(64), nullable=False, index=True)
    asset_manifest_digest = Column(String(64), nullable=True, index=True)
    evidence_refs_json = Column(JSON, nullable=False, default=list)
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
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
        CheckConstraint(
            "execution_status IN ("
            "'planned', 'collecting', 'processing', 'completed', 'not_applicable', "
            "'unsupported', 'blocked_asset_missing', 'collection_failed', 'parse_failed', "
            "'normalization_failed', 'comparison_failed', 'assessment_failed', 'cancelled')",
            name="ck_check_execution_status",
        ),
        Index(
            "uq_check_execution_scope",
            "patrol_batch_id",
            "device_id",
            "check_item_version_id",
            unique=True,
        ),
        Index(
            "ix_check_execution_batch_status",
            "patrol_batch_id",
            "execution_status",
        ),
        Index(
            "ix_check_execution_baseline_lookup",
            "device_id",
            "check_item_version_id",
            "observation_max_at",
            "execution_status",
        ),
    )

    def to_dict(self):
        return {column.name: getattr(self, column.name) for column in self.__table__.columns}


class CheckAssessmentRecord(Base):
    """Explicit health, completeness and comparison conclusion for a check execution."""

    __tablename__ = "netops_check_assessments"

    id = Column(String(64), primary_key=True)
    idempotency_key = Column(String(128), nullable=False, unique=True)
    patrol_batch_id = Column(String(128), nullable=False, index=True)
    device_id = Column(String(256), nullable=False, index=True)
    check_item_version_id = Column(
        String(64),
        ForeignKey("netops_check_item_versions.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    execution_id = Column(
        String(64),
        ForeignKey("netops_check_executions.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    assessment_status = Column(String(32), nullable=False, index=True)
    completeness_status = Column(String(32), nullable=False, index=True)
    comparison_status = Column(String(32), nullable=False, index=True)
    expected_entity_count = Column(Integer, nullable=True)
    observed_entity_count = Column(Integer, nullable=False, default=0, server_default="0")
    evaluated_entity_count = Column(Integer, nullable=False, default=0, server_default="0")
    threshold_unknown_count = Column(Integer, nullable=False, default=0, server_default="0")
    current_finding_ids_json = Column(JSON, nullable=False, default=list)
    risk_case_ids_json = Column(JSON, nullable=False, default=list)
    evidence_qualification_id = Column(String(64), nullable=True, index=True)
    coverage_decision_id = Column(String(64), nullable=True, index=True)
    source_identity_status = Column(
        String(32), nullable=False, default="unknown", server_default="unknown", index=True
    )
    output_quality_status = Column(
        String(32), nullable=False, default="unknown", server_default="unknown", index=True
    )
    freshness_status = Column(
        String(32), nullable=False, default="unknown", server_default="unknown", index=True
    )
    empty_result_classification = Column(
        String(32),
        nullable=False,
        default="not_evaluated",
        server_default="not_evaluated",
        index=True,
    )
    coverage_status = Column(
        String(32), nullable=False, default="unknown", server_default="unknown", index=True
    )
    coverage_basis = Column(
        String(32), nullable=False, default="unknown", server_default="unknown", index=True
    )
    risk_verification_status = Column(
        String(32), nullable=False, default="unknown", server_default="unknown", index=True
    )
    blocking_reason_codes_json = Column(JSON, nullable=False, default=list)
    assessment_rule_bundle_digest = Column(String(64), nullable=True, index=True)
    assessment_policy_snapshot_digest = Column(String(64), nullable=True, index=True)
    assessment_basis_json = Column(JSON, nullable=False, default=dict)
    assessment_basis_digest = Column(String(64), nullable=True, index=True)
    comparison_context_digest = Column(String(64), nullable=True, index=True)
    evidence_digest = Column(String(64), nullable=False, index=True)
    assessed_at = Column(DateTime(timezone=True), nullable=False, index=True)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        CheckConstraint(
            "assessment_status IN ("
            "'healthy', 'at_risk', 'unable_to_determine', 'not_applicable', 'informational')",
            name="ck_check_assessment_status",
        ),
        CheckConstraint(
            "completeness_status IN ('complete', 'partial', 'insufficient')",
            name="ck_check_assessment_completeness",
        ),
        CheckConstraint(
            "comparison_status IN ("
            "'comparable', 'first_observation', 'baseline_missing', "
            "'baseline_stale', 'schema_incompatible')",
            name="ck_check_assessment_comparison",
        ),
        Index(
            "uq_check_assessment_scope",
            "patrol_batch_id",
            "device_id",
            "check_item_version_id",
            unique=True,
        ),
        Index(
            "ix_check_assessment_batch_status",
            "patrol_batch_id",
            "assessment_status",
        ),
    )

    def to_dict(self):
        return {column.name: getattr(self, column.name) for column in self.__table__.columns}


class ProductionFeedbackRecord(Base):
    """Governed production feedback bound to immutable execution evidence."""

    __tablename__ = "netops_production_feedback"

    id = Column(String(64), primary_key=True)
    idempotency_key = Column(String(128), nullable=False, unique=True)
    request_digest = Column(String(64), nullable=False, index=True)
    feedback_type = Column(String(32), nullable=False, index=True)
    status = Column(
        String(32), nullable=False, default="submitted", server_default="submitted", index=True
    )
    triage_outcome = Column(String(32), nullable=True, index=True)
    execution_id = Column(
        String(64),
        ForeignKey("netops_check_executions.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    assessment_id = Column(
        String(64),
        ForeignKey("netops_check_assessments.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    event_occurrence_id = Column(
        String(64),
        ForeignKey("netops_network_event_occurrences.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    risk_occurrence_id = Column(
        String(64),
        ForeignKey("netops_risk_occurrences.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    risk_episode_id = Column(String(64), nullable=True, index=True)
    patrol_batch_id = Column(String(128), nullable=False, index=True)
    device_id = Column(String(256), nullable=False, index=True)
    check_item_version_id = Column(String(64), nullable=False, index=True)
    runtime_bundle_id = Column(String(64), nullable=True, index=True)
    runtime_bundle_version_id = Column(String(64), nullable=True, index=True)
    runtime_bundle_digest = Column(String(64), nullable=True, index=True)
    evidence_digest = Column(String(64), nullable=False, index=True)
    assessment_basis_digest = Column(String(64), nullable=True, index=True)
    source_context_json = Column(JSON, nullable=False)
    statement = Column(Text, nullable=False)
    reported_by = Column(String(128), nullable=False, index=True)
    triaged_by = Column(String(128), nullable=True, index=True)
    triage_reason = Column(Text, nullable=True)
    triage_idempotency_key = Column(String(128), nullable=True, unique=True)
    triage_request_digest = Column(String(64), nullable=True)
    work_item_id = Column(String(64), nullable=True, index=True)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    triaged_at = Column(DateTime(timezone=True), nullable=True)
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        CheckConstraint(
            "feedback_type IN ('false_positive', 'missed_detection', "
            "'wrong_severity', 'wrong_scope', 'expected_behavior', "
            "'evidence_quality_issue')",
            name="ck_production_feedback_type",
        ),
        CheckConstraint(
            "status IN ('submitted', 'triaged', 'work_item_created', 'closed', 'rejected')",
            name="ck_production_feedback_status",
        ),
        CheckConstraint(
            "triage_outcome IS NULL OR triage_outcome IN "
            "('confirmed', 'not_confirmed', 'inconclusive')",
            name="ck_production_feedback_triage_outcome",
        ),
        Index(
            "ix_production_feedback_bundle_type",
            "runtime_bundle_version_id",
            "feedback_type",
            "triage_outcome",
        ),
        Index(
            "ix_production_feedback_status_created",
            "status",
            "created_at",
        ),
    )

    def to_dict(self):
        return {column.name: getattr(self, column.name) for column in self.__table__.columns}


class ProductionFeedbackWorkItemRecord(Base):
    """Governance action created from independently confirmed feedback."""

    __tablename__ = "netops_production_feedback_work_items"

    id = Column(String(64), primary_key=True)
    feedback_id = Column(
        String(64),
        ForeignKey("netops_production_feedback.id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
        index=True,
    )
    work_item_kind = Column(String(32), nullable=False, index=True)
    status = Column(
        String(32), nullable=False, default="open", server_default="open", index=True
    )
    action_payload_json = Column(JSON, nullable=False)
    action_digest = Column(String(64), nullable=False, index=True)
    candidate_ref_json = Column(JSON, nullable=True)
    candidate_ref_digest = Column(String(64), nullable=True, index=True)
    created_by = Column(String(128), nullable=False, index=True)
    linked_by = Column(String(128), nullable=True, index=True)
    completed_by = Column(String(128), nullable=True, index=True)
    completion_reason = Column(Text, nullable=True)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    linked_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        CheckConstraint(
            "work_item_kind IN ('rule_sample', 'assessment_policy', 'data_quality')",
            name="ck_production_feedback_work_item_kind",
        ),
        CheckConstraint(
            "status IN ('open', 'candidate_linked', 'completed', 'cancelled')",
            name="ck_production_feedback_work_item_status",
        ),
        Index(
            "ix_production_feedback_work_item_status_created",
            "status",
            "created_at",
        ),
    )

    def to_dict(self):
        return {column.name: getattr(self, column.name) for column in self.__table__.columns}


class EvidenceQualificationRecord(Base):
    """Immutable identity, output-quality and freshness decision for one execution."""

    __tablename__ = "netops_evidence_qualifications"

    id = Column(String(64), primary_key=True)
    digest = Column(String(64), nullable=False, unique=True, index=True)
    check_execution_id = Column(
        String(64),
        ForeignKey("netops_check_executions.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    patrol_batch_id = Column(String(128), nullable=False, index=True)
    device_id = Column(String(256), nullable=False, index=True)
    check_item_version_id = Column(
        String(64),
        ForeignKey("netops_check_item_versions.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    implementation_version_id = Column(String(64), nullable=True, index=True)
    runtime_bundle_version_id = Column(String(64), nullable=True, index=True)
    runtime_bundle_digest = Column(String(64), nullable=True, index=True)
    source_identity_status = Column(String(32), nullable=False, index=True)
    output_quality_status = Column(String(32), nullable=False, index=True)
    freshness_status = Column(String(32), nullable=False, index=True)
    empty_result_classification = Column(String(32), nullable=False, index=True)
    snapshot_ids_json = Column(JSON, nullable=False, default=list)
    observation_min_at = Column(DateTime(timezone=True), nullable=True, index=True)
    observation_max_at = Column(DateTime(timezone=True), nullable=True, index=True)
    qualification_reason_codes_json = Column(JSON, nullable=False, default=list)
    policy_snapshot_json = Column(JSON, nullable=False, default=dict)
    policy_digest = Column(String(64), nullable=False, index=True)
    profile_version_id = Column(String(64), nullable=True, index=True)
    profile_digest = Column(String(64), nullable=True, index=True)
    evidence_refs_json = Column(JSON, nullable=False, default=list)
    qualified_at = Column(DateTime(timezone=True), nullable=False, index=True)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        CheckConstraint(
            "source_identity_status IN "
            "('confirmed', 'ambiguous', 'conflict', 'external_unbound', 'unknown')",
            name="ck_evidence_qualification_identity",
        ),
        CheckConstraint(
            "output_quality_status IN "
            "('complete', 'empty', 'unsupported', 'device_error', 'truncated', "
            "'paged', 'ambiguous', 'unknown')",
            name="ck_evidence_qualification_output",
        ),
        CheckConstraint(
            "freshness_status IN ('fresh', 'stale', 'unknown')",
            name="ck_evidence_qualification_freshness",
        ),
    )

    def to_dict(self):
        return {column.name: getattr(self, column.name) for column in self.__table__.columns}


class CoverageDecisionRecord(Base):
    """Immutable expected-versus-observed entity-set decision."""

    __tablename__ = "netops_coverage_decisions"

    id = Column(String(64), primary_key=True)
    digest = Column(String(64), nullable=False, unique=True, index=True)
    check_execution_id = Column(
        String(64),
        ForeignKey("netops_check_executions.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    patrol_batch_id = Column(String(128), nullable=False, index=True)
    device_id = Column(String(256), nullable=False, index=True)
    check_item_version_id = Column(
        String(64),
        ForeignKey("netops_check_item_versions.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    coverage_status = Column(String(32), nullable=False, index=True)
    coverage_basis = Column(String(32), nullable=False, index=True)
    expected_entity_count = Column(Integer, nullable=True)
    observed_entity_count = Column(Integer, nullable=False, default=0, server_default="0")
    evaluated_entity_count = Column(Integer, nullable=False, default=0, server_default="0")
    expected_entity_set_digest = Column(String(64), nullable=True, index=True)
    observed_entity_set_digest = Column(String(64), nullable=False, index=True)
    denominator_ref_json = Column(JSON, nullable=False, default=dict)
    missing_entity_sample_json = Column(JSON, nullable=False, default=list)
    unexpected_entity_sample_json = Column(JSON, nullable=False, default=list)
    reason_codes_json = Column(JSON, nullable=False, default=list)
    policy_snapshot_json = Column(JSON, nullable=False, default=dict)
    policy_digest = Column(String(64), nullable=False, index=True)
    evidence_refs_json = Column(JSON, nullable=False, default=list)
    decided_at = Column(DateTime(timezone=True), nullable=False, index=True)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        CheckConstraint(
            "coverage_status IN "
            "('complete', 'partial', 'empty', 'unbounded', 'unknown', "
            "'not_applicable', 'not_evaluable')",
            name="ck_coverage_decision_status",
        ),
        CheckConstraint(
            "coverage_basis IN "
            "('fixed', 'inventory_entity_set', 'derived_entity_set', "
            "'command_reported_total', 'non_exhaustive', 'unknown')",
            name="ck_coverage_decision_basis",
        ),
    )

    def to_dict(self):
        return {column.name: getattr(self, column.name) for column in self.__table__.columns}


class PatrolProfileCheckItemRecord(Base):
    """Versioned check-item selection for a patrol profile."""

    __tablename__ = "netops_patrol_profile_check_items"

    id = Column(String(64), primary_key=True)
    profile_version_id = Column(
        String(64),
        ForeignKey("netops_command_profile_versions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    check_item_version_id = Column(
        String(64),
        ForeignKey("netops_check_item_versions.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    priority = Column(Integer, nullable=False, default=100, server_default="100")
    required = Column(Boolean, nullable=False, default=True, server_default=text("true"))
    status = Column(
        String(32), nullable=False, default="active", server_default="active", index=True
    )
    created_by = Column(String(128), nullable=True)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('active', 'disabled')",
            name="ck_patrol_profile_check_item_status",
        ),
        Index(
            "uq_patrol_profile_check_item",
            "profile_version_id",
            "check_item_version_id",
            unique=True,
        ),
    )

    def to_dict(self):
        return {column.name: getattr(self, column.name) for column in self.__table__.columns}


class CheckItemCollectionActionRecord(Base):
    """One immutable physical collection action shared by logical check items."""

    __tablename__ = "netops_check_item_collection_actions"

    id = Column(String(64), primary_key=True)
    patrol_batch_id = Column(String(128), nullable=False, index=True)
    device_id = Column(String(256), nullable=False, index=True)
    collector = Column(String(32), nullable=False, default="cli", server_default="cli")
    command = Column(String(512), nullable=False)
    canonical_command = Column(String(512), nullable=False, index=True)
    fallback_rank = Column(Integer, nullable=False, default=0, server_default="0")
    priority = Column(Integer, nullable=False, default=100, server_default="100")
    parameters_json = Column(JSON, nullable=False, default=dict)
    check_item_version_ids_json = Column(JSON, nullable=False, default=list)
    implementation_version_ids_json = Column(JSON, nullable=False, default=list)
    runtime_bundle_version_ids_json = Column(JSON, nullable=False, default=list)
    digest = Column(String(64), nullable=False, index=True)
    status = Column(
        String(32), nullable=False, default="planned", server_default="planned", index=True
    )
    execution_plan_id = Column(
        String(64),
        ForeignKey("netops_collection_execution_plans.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    failure_code = Column(String(128), nullable=True, index=True)
    error = Column(Text, nullable=True)
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
        CheckConstraint(
            "status IN ('planned', 'collecting', 'completed', 'failed', 'cancelled', 'skipped')",
            name="ck_check_item_collection_action_status",
        ),
        Index(
            "uq_check_item_collection_action",
            "patrol_batch_id",
            "device_id",
            "digest",
            unique=True,
        ),
    )

    def to_dict(self):
        return {column.name: getattr(self, column.name) for column in self.__table__.columns}


class CheckItemRuntimeManifestRecord(Base):
    """Frozen per-batch check-item execution and asset selection."""

    __tablename__ = "netops_check_item_runtime_manifests"

    id = Column(String(64), primary_key=True)
    patrol_batch_id = Column(String(128), nullable=False, unique=True, index=True)
    schema_version = Column(Integer, nullable=False, default=2, server_default="2")
    target_manifest_digest = Column(String(64), nullable=False, index=True)
    inventory_snapshot_digest = Column(String(64), nullable=False, index=True)
    scope_contract_version_id = Column(
        String(64),
        ForeignKey(
            "netops_patrol_scope_contract_versions.id",
            ondelete="RESTRICT",
        ),
        nullable=True,
        index=True,
    )
    scope_contract_digest = Column(String(64), nullable=True, index=True)
    intended_target_matrix_digest = Column(String(64), nullable=True, index=True)
    scope_decision_digest = Column(String(64), nullable=True, index=True)
    check_item_versions_json = Column(JSON, nullable=False)
    collection_actions_json = Column(JSON, nullable=False)
    runtime_bundle_refs_json = Column(JSON, nullable=False)
    comparison_context_ids_json = Column(JSON, nullable=False)
    entity_identity_policy_versions_json = Column(JSON, nullable=False)
    assessment_schema_version = Column(String(64), nullable=False)
    report_schema_version = Column(String(64), nullable=False)
    manifest_json = Column(JSON, nullable=False)
    digest = Column(String(64), nullable=False, unique=True, index=True)
    status = Column(
        String(32), nullable=False, default="frozen", server_default="frozen", index=True
    )
    created_by = Column(String(128), nullable=True)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('frozen', 'superseded')",
            name="ck_check_item_runtime_manifest_status",
        ),
    )

    def to_dict(self):
        return {column.name: getattr(self, column.name) for column in self.__table__.columns}


class TextComparisonProfileRecord(Base):
    """Stable identity for a governed non-structured comparison profile."""

    __tablename__ = "netops_text_comparison_profiles"

    id = Column(String(128), primary_key=True)
    name = Column(String(256), nullable=False)
    status = Column(
        String(32), nullable=False, default="draft", server_default="draft", index=True
    )
    current_version = Column(Integer, nullable=False, default=0, server_default="0")
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

    def to_dict(self):
        return {column.name: getattr(self, column.name) for column in self.__table__.columns}


class TextComparisonProfileVersionRecord(Base):
    """Immutable normalization and diff policy for raw command evidence."""

    __tablename__ = "netops_text_comparison_profile_versions"

    id = Column(String(64), primary_key=True)
    profile_id = Column(
        String(128),
        ForeignKey("netops_text_comparison_profiles.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    version = Column(Integer, nullable=False)
    mode = Column(String(32), nullable=False)
    profile_json = Column(JSON, nullable=False)
    digest = Column(String(64), nullable=False, index=True)
    status = Column(
        String(32), nullable=False, default="draft", server_default="draft", index=True
    )
    created_by = Column(String(128), nullable=True)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    activated_at = Column(DateTime(timezone=True), nullable=True)
    deprecated_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint(
            "mode IN ('ordered_lines', 'unordered_lines', 'hash_only')",
            name="ck_text_comparison_profile_mode",
        ),
        CheckConstraint(
            "status IN ('draft', 'active', 'deprecated')",
            name="ck_text_comparison_profile_status",
        ),
        Index(
            "uq_text_comparison_profile_version",
            "profile_id",
            "version",
            unique=True,
        ),
        Index(
            "uq_text_comparison_profile_active",
            "profile_id",
            unique=True,
            postgresql_where=text("status = 'active'"),
            sqlite_where=text("status = 'active'"),
        ),
    )

    def to_dict(self):
        return {column.name: getattr(self, column.name) for column in self.__table__.columns}


class AssetGapRecord(Base):
    """A production asset or output-compatibility gap; never an LLM trigger."""

    __tablename__ = "netops_asset_gaps"

    id = Column(String(64), primary_key=True)
    idempotency_key = Column(String(64), nullable=False, unique=True)
    patrol_batch_id = Column(String(128), nullable=True, index=True)
    check_execution_id = Column(
        String(64),
        ForeignKey("netops_check_executions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    check_item_id = Column(String(128), nullable=False, index=True)
    check_item_version_id = Column(String(64), nullable=True, index=True)
    implementation_version_id = Column(String(64), nullable=True, index=True)
    canonical_command = Column(String(512), nullable=True, index=True)
    output_signature = Column(String(64), nullable=True, index=True)
    gap_type = Column(String(64), nullable=False, index=True)
    purpose = Column(String(32), nullable=False, index=True)
    status = Column(
        String(32), nullable=False, default="open", server_default="open", index=True
    )
    reason_code = Column(String(128), nullable=False, index=True)
    sanitized_sample_refs_json = Column(JSON, nullable=False, default=list)
    evidence_refs_json = Column(JSON, nullable=False, default=list)
    occurrence_count = Column(Integer, nullable=False, default=1, server_default="1")
    first_seen_at = Column(DateTime(timezone=True), nullable=False, index=True)
    last_seen_at = Column(DateTime(timezone=True), nullable=False, index=True)
    resolved_at = Column(DateTime(timezone=True), nullable=True)
    resolved_by = Column(String(128), nullable=True)
    resolution_note = Column(Text, nullable=True)
    metadata_json = Column(JSON, nullable=True)
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
        CheckConstraint(
            "purpose IN ('production_patrol', 'offline_import', 'historical_backfill')",
            name="ck_asset_gap_purpose",
        ),
        CheckConstraint(
            "status IN ('open', 'in_progress', 'resolved', 'ignored')",
            name="ck_asset_gap_status",
        ),
        Index(
            "ix_asset_gap_work_queue",
            "status",
            "gap_type",
            "last_seen_at",
        ),
    )

    def to_dict(self):
        return {column.name: getattr(self, column.name) for column in self.__table__.columns}


class ReportManifestRecord(Base):
    """Immutable fact and artifact manifest for one report version."""

    __tablename__ = "netops_report_manifests"

    id = Column(String(64), primary_key=True)
    patrol_batch_id = Column(String(128), nullable=False, index=True)
    version = Column(Integer, nullable=False)
    schema_version = Column(String(64), nullable=False)
    status = Column(String(32), nullable=False, index=True)
    target_manifest_digest = Column(String(64), nullable=False, index=True)
    scope_contract_digest = Column(String(64), nullable=True, index=True)
    intended_target_matrix_digest = Column(String(64), nullable=True, index=True)
    scope_decision_digest = Column(String(64), nullable=True, index=True)
    source_qualification_digest = Column(String(64), nullable=True, index=True)
    runtime_manifest_digest = Column(String(64), nullable=False, index=True)
    comparison_context_digests_json = Column(JSON, nullable=False)
    check_execution_fact_digest = Column(String(64), nullable=False, index=True)
    check_assessment_fact_digest = Column(String(64), nullable=False, index=True)
    risk_case_revision = Column(String(64), nullable=False, index=True)
    evidence_index_digest = Column(String(64), nullable=False, index=True)
    raw_evidence_set_digest = Column(
        String(64),
        nullable=False,
        index=True,
    )
    report_purpose = Column(
        String(32),
        nullable=False,
        default="shadow_validation",
        server_default="shadow_validation",
        index=True,
    )
    generator_version = Column(String(64), nullable=False)
    source_facts_json = Column(JSON, nullable=False)
    source_digest = Column(String(64), nullable=False, index=True)
    claim_ledger_digest = Column(String(64), nullable=True, index=True)
    claim_count = Column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )
    artifact_object_keys_json = Column(JSON, nullable=False, default=dict)
    artifact_sha256_json = Column(JSON, nullable=False, default=dict)
    generated_by = Column(String(128), nullable=True)
    generated_at = Column(DateTime(timezone=True), nullable=False)
    report_cutoff_at = Column(DateTime(timezone=True), nullable=False, index=True)
    fact_ingestion_high_watermark = Column(
        DateTime(timezone=True),
        nullable=False,
        index=True,
    )
    qualification_fact_digest = Column(String(64), nullable=False, index=True)
    coverage_fact_digest = Column(String(64), nullable=False, index=True)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('draft', 'partial', 'final', 'superseded')",
            name="ck_report_manifest_status",
        ),
        CheckConstraint(
            "report_purpose IN ("
            "'production_current', 'offline_historical', "
            "'shadow_validation', 'parser_validation', "
            "'rule_asset_replay'"
            ")",
            name="ck_report_manifest_purpose",
        ),
        Index(
            "uq_report_manifest_batch_version",
            "patrol_batch_id",
            "version",
            unique=True,
        ),
        Index(
            "uq_report_manifest_batch_source",
            "patrol_batch_id",
            "source_digest",
            unique=True,
        ),
    )

    def to_dict(self):
        return {column.name: getattr(self, column.name) for column in self.__table__.columns}


class ReportClaimRecord(Base):
    """One immutable, evidence-bounded factual statement in a report."""

    __tablename__ = "netops_report_claims"

    id = Column(String(64), primary_key=True)
    report_manifest_id = Column(
        String(64),
        ForeignKey("netops_report_manifests.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    patrol_batch_id = Column(String(128), nullable=False, index=True)
    claim_code = Column(String(256), nullable=False)
    claim_scope = Column(String(32), nullable=False, index=True)
    assertion_level = Column(String(32), nullable=False, index=True)
    allowed_phrase_code = Column(String(128), nullable=False, index=True)
    subject_json = Column(JSON, nullable=False, default=dict)
    fact_refs_json = Column(JSON, nullable=False, default=list)
    evidence_refs_json = Column(JSON, nullable=False, default=list)
    limitation_refs_json = Column(JSON, nullable=False, default=list)
    authority_context_digest = Column(String(64), nullable=False, index=True)
    source_facts_digest = Column(String(64), nullable=False, index=True)
    claim_digest = Column(String(64), nullable=False, unique=True, index=True)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        CheckConstraint(
            "claim_scope IN ('network', 'site', 'device_group', 'device', 'check_item', 'entity')",
            name="ck_report_claim_scope",
        ),
        CheckConstraint(
            "assertion_level IN ('observed', 'assessed', 'current', 'historical', 'limited')",
            name="ck_report_claim_assertion_level",
        ),
        Index(
            "uq_report_claim_manifest_code",
            "report_manifest_id",
            "claim_code",
            unique=True,
        ),
    )

    def to_dict(self):
        return {column.name: getattr(self, column.name) for column in self.__table__.columns}


class ReportContentReviewRecord(Base):
    """Frozen, human-reviewable content acceptance plan for one manifest."""

    __tablename__ = "netops_report_content_reviews"

    id = Column(String(64), primary_key=True)
    report_manifest_id = Column(
        String(64),
        ForeignKey("netops_report_manifests.id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
        index=True,
    )
    patrol_batch_id = Column(String(128), nullable=False, index=True)
    schema_version = Column(String(64), nullable=False)
    status = Column(
        String(32),
        nullable=False,
        default="in_review",
        server_default="in_review",
        index=True,
    )
    source_facts_digest = Column(String(64), nullable=False, index=True)
    claim_ledger_digest = Column(String(64), nullable=False, index=True)
    artifact_sha256_json = Column(JSON, nullable=False)
    review_plan_digest = Column(String(64), nullable=False, unique=True, index=True)
    machine_validation_json = Column(JSON, nullable=False)
    machine_validation_digest = Column(String(64), nullable=False, index=True)
    required_item_count = Column(Integer, nullable=False, default=0, server_default="0")
    accepted_item_count = Column(Integer, nullable=False, default=0, server_default="0")
    rejected_item_count = Column(Integer, nullable=False, default=0, server_default="0")
    required_confirmations_json = Column(JSON, nullable=False, default=list)
    accepted_confirmations_json = Column(JSON, nullable=True)
    created_by = Column(String(128), nullable=False, index=True)
    accepted_by = Column(String(128), nullable=True, index=True)
    accepted_at = Column(DateTime(timezone=True), nullable=True, index=True)
    acceptance_comment = Column(Text, nullable=True)
    acceptance_digest = Column(String(64), nullable=True, unique=True, index=True)
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
        CheckConstraint(
            "status IN ('in_review', 'revision_required', 'accepted')",
            name="ck_report_content_review_status",
        ),
        CheckConstraint(
            "required_item_count >= 0 AND accepted_item_count >= 0 "
            "AND rejected_item_count >= 0",
            name="ck_report_content_review_counts_nonnegative",
        ),
    )

    def to_dict(self):
        return {column.name: getattr(self, column.name) for column in self.__table__.columns}


class ReportContentReviewItemRecord(Base):
    """One immutable review decision target from a frozen content plan."""

    __tablename__ = "netops_report_content_review_items"

    id = Column(String(64), primary_key=True)
    review_id = Column(
        String(64),
        ForeignKey("netops_report_content_reviews.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    review_item_id = Column(String(64), nullable=False, index=True)
    category = Column(String(64), nullable=False, index=True)
    review_reasons_json = Column(JSON, nullable=False, default=list)
    stratum_key = Column(String(512), nullable=True, index=True)
    fact_type = Column(String(64), nullable=False, index=True)
    fact_id = Column(String(256), nullable=False, index=True)
    device_id = Column(String(256), nullable=True, index=True)
    check_item_id = Column(String(256), nullable=True, index=True)
    severity = Column(String(32), nullable=True, index=True)
    conclusion_status = Column(String(64), nullable=True, index=True)
    evidence_refs_json = Column(JSON, nullable=False, default=list)
    summary_json = Column(JSON, nullable=False, default=dict)
    review_status = Column(
        String(32),
        nullable=False,
        default="pending",
        server_default="pending",
        index=True,
    )
    reviewed_by = Column(String(128), nullable=True, index=True)
    reviewed_at = Column(DateTime(timezone=True), nullable=True, index=True)
    review_comment = Column(Text, nullable=True)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        Index(
            "uq_report_content_review_item",
            "review_id",
            "review_item_id",
            unique=True,
        ),
        Index(
            "ix_report_content_review_queue",
            "review_id",
            "review_status",
            "category",
            "id",
        ),
        CheckConstraint(
            "review_status IN ('pending', 'accepted', 'rejected')",
            name="ck_report_content_review_item_status",
        ),
    )

    def to_dict(self):
        return {column.name: getattr(self, column.name) for column in self.__table__.columns}


class ReportReleaseRecord(Base):
    """Immutable human sign-off for one exact report artifact set."""

    __tablename__ = "netops_report_releases"

    id = Column(String(64), primary_key=True)
    report_manifest_id = Column(
        String(64),
        ForeignKey("netops_report_manifests.id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
        index=True,
    )
    content_review_id = Column(
        String(64),
        ForeignKey("netops_report_content_reviews.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    content_acceptance_digest = Column(String(64), nullable=True, index=True)
    patrol_batch_id = Column(String(128), nullable=False, index=True)
    status = Column(
        String(32), nullable=False, default="released", server_default="released", index=True
    )
    source_facts_digest = Column(String(64), nullable=False, index=True)
    claim_ledger_digest = Column(String(64), nullable=True, index=True)
    artifact_sha256_json = Column(JSON, nullable=False)
    release_digest = Column(String(64), nullable=False, unique=True, index=True)
    signed_by = Column(String(128), nullable=False, index=True)
    signed_at = Column(DateTime(timezone=True), nullable=False, index=True)
    signoff_comment = Column(Text, nullable=False)
    disclosure_confirmations_json = Column(JSON, nullable=False, default=list)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        CheckConstraint(
            "status = 'released'",
            name="ck_report_release_status",
        ),
    )

    def to_dict(self):
        return {column.name: getattr(self, column.name) for column in self.__table__.columns}


class EntityIdentityPolicyRecord(Base):
    """Stable identity-policy metadata."""

    __tablename__ = "netops_entity_identity_policies"

    id = Column(String(128), primary_key=True)
    name = Column(String(256), nullable=False)
    status = Column(String(32), nullable=False, default="draft", server_default="draft", index=True)
    current_version = Column(Integer, nullable=False, default=0, server_default="0")
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

    def to_dict(self):
        return {column.name: getattr(self, column.name) for column in self.__table__.columns}


class EntityIdentityPolicyVersionRecord(Base):
    """Immutable entity-identity normalization policy version."""

    __tablename__ = "netops_entity_identity_policy_versions"

    id = Column(String(64), primary_key=True)
    policy_id = Column(
        String(128),
        ForeignKey("netops_entity_identity_policies.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    version = Column(Integer, nullable=False)
    policy_json = Column(JSON, nullable=False)
    digest = Column(String(64), nullable=False, index=True)
    status = Column(String(32), nullable=False, default="draft", server_default="draft", index=True)
    created_by = Column(String(128), nullable=True)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    activated_at = Column(DateTime(timezone=True), nullable=True)
    deprecated_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint(
            "status IN ('draft', 'active', 'deprecated')",
            name="ck_entity_identity_policy_version_status",
        ),
        Index(
            "uq_entity_identity_policy_version",
            "policy_id",
            "version",
            unique=True,
        ),
        Index(
            "uq_entity_identity_policy_active",
            "policy_id",
            unique=True,
            postgresql_where=text("status = 'active'"),
            sqlite_where=text("status = 'active'"),
        ),
    )

    def to_dict(self):
        return {column.name: getattr(self, column.name) for column in self.__table__.columns}


class CheckItemRuntimeBundleRecord(Base):
    """Stable runtime-bundle identity for one check item and implementation."""

    __tablename__ = "netops_check_item_runtime_bundles"

    id = Column(String(64), primary_key=True)
    check_item_id = Column(
        String(128),
        ForeignKey("netops_check_items.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    implementation_id = Column(
        String(128),
        ForeignKey("netops_collection_implementations.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    name = Column(String(256), nullable=False)
    status = Column(String(32), nullable=False, default="draft", server_default="draft", index=True)
    current_version = Column(Integer, nullable=False, default=0, server_default="0")
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
            "uq_check_item_runtime_bundle_identity",
            "check_item_id",
            "implementation_id",
            unique=True,
        ),
    )

    def to_dict(self):
        return {column.name: getattr(self, column.name) for column in self.__table__.columns}


class CheckItemRuntimeBundleVersionRecord(Base):
    """Immutable, atomically activated set of compatible production assets."""

    __tablename__ = "netops_check_item_runtime_bundle_versions"

    id = Column(String(64), primary_key=True)
    bundle_id = Column(
        String(64),
        ForeignKey("netops_check_item_runtime_bundles.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    version = Column(Integer, nullable=False)
    check_item_version_id = Column(
        String(64),
        ForeignKey("netops_check_item_versions.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    implementation_version_id = Column(
        String(64),
        ForeignKey("netops_collection_implementation_versions.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    parser_bundle_json = Column(JSON, nullable=False)
    canonical_catalog_version = Column(Integer, nullable=False)
    canonical_catalog_digest = Column(String(64), nullable=False, index=True)
    entity_identity_policy_version_id = Column(
        String(64),
        ForeignKey("netops_entity_identity_policy_versions.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    comparison_profile_version_id = Column(String(64), nullable=True, index=True)
    dependency_digests_json = Column(JSON, nullable=False)
    compatibility_json = Column(JSON, nullable=False)
    validation_report_json = Column(JSON, nullable=False)
    digest = Column(String(64), nullable=False, unique=True, index=True)
    status = Column(String(32), nullable=False, default="draft", server_default="draft", index=True)
    created_by = Column(String(128), nullable=True)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    activated_at = Column(DateTime(timezone=True), nullable=True)
    deprecated_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint(
            "status IN ('draft', 'active', 'deprecated')",
            name="ck_check_item_runtime_bundle_version_status",
        ),
        Index(
            "uq_check_item_runtime_bundle_version",
            "bundle_id",
            "version",
            unique=True,
        ),
        Index(
            "uq_check_item_runtime_bundle_active",
            "bundle_id",
            unique=True,
            postgresql_where=text("status = 'active'"),
            sqlite_where=text("status = 'active'"),
        ),
    )

    def to_dict(self):
        return {column.name: getattr(self, column.name) for column in self.__table__.columns}


class RuntimeBundleAssetRecord(Base):
    """Normalized child-asset reference for an immutable runtime bundle."""

    __tablename__ = "netops_runtime_bundle_assets"

    id = Column(String(64), primary_key=True)
    bundle_version_id = Column(
        String(64),
        ForeignKey("netops_check_item_runtime_bundle_versions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    asset_type = Column(String(32), nullable=False, index=True)
    asset_id = Column(String(128), nullable=False, index=True)
    asset_version_id = Column(String(128), nullable=False, index=True)
    asset_digest = Column(String(64), nullable=False, index=True)
    role = Column(String(64), nullable=False, default="primary", server_default="primary")
    canonical_command = Column(String(512), nullable=True, index=True)
    observation_type = Column(String(256), nullable=True, index=True)
    required = Column(Boolean, nullable=False, default=True, server_default=text("true"))
    metadata_json = Column(JSON, nullable=True)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        CheckConstraint(
            "asset_type IN ("
            "'parser_template', 'command_adapter_projection', "
            "'observation_contract', 'current_state_rule', "
            "'change_policy', 'event_rule', 'comparison_profile', 'identity_policy')",
            name="ck_runtime_bundle_asset_type",
        ),
        Index(
            "uq_runtime_bundle_asset_ref",
            "bundle_version_id",
            "asset_type",
            "asset_version_id",
            "role",
            unique=True,
        ),
    )

    def to_dict(self):
        return {column.name: getattr(self, column.name) for column in self.__table__.columns}


class RolloutCampaignRecord(Base):
    """Persistent rollout authority for one frozen target and asset set."""

    __tablename__ = "netops_rollout_campaigns"

    id = Column(String(64), primary_key=True)
    idempotency_key = Column(String(128), nullable=False, unique=True)
    name = Column(String(256), nullable=False)
    description = Column(Text, nullable=True)
    status = Column(
        String(32),
        nullable=False,
        default="draft",
        server_default="draft",
        index=True,
    )
    current_stage = Column(String(32), nullable=True, index=True)
    paused_from_stage = Column(String(32), nullable=True)
    target_manifest_json = Column(JSON, nullable=True)
    target_manifest_digest = Column(String(64), nullable=True, index=True)
    runtime_bundle_refs_json = Column(JSON, nullable=True)
    runtime_bundle_digest = Column(String(64), nullable=True, index=True)
    comparison_context_ids_json = Column(JSON, nullable=True)
    comparison_context_digest = Column(String(64), nullable=True, index=True)
    baseline_window_json = Column(JSON, nullable=True)
    thresholds_json = Column(JSON, nullable=True)
    rollback_plan_json = Column(JSON, nullable=True)
    pause_context_json = Column(JSON, nullable=True)
    last_error = Column(Text, nullable=True)
    lock_version = Column(
        Integer,
        nullable=False,
        default=1,
        server_default="1",
    )
    created_by = Column(String(128), nullable=False, index=True)
    frozen_by = Column(String(128), nullable=True, index=True)
    paused_by = Column(String(128), nullable=True)
    completed_by = Column(String(128), nullable=True)
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
    frozen_at = Column(DateTime(timezone=True), nullable=True)
    paused_at = Column(DateTime(timezone=True), nullable=True)
    rolled_back_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint(
            "status IN ("
            "'draft', 'target_frozen', 'shadow', 'canary_5', "
            "'canary_20', 'canary_50', 'full_100', 'observing', "
            "'paused', 'rolled_back', 'completed'"
            ")",
            name="ck_rollout_campaign_status",
        ),
        CheckConstraint(
            "current_stage IS NULL OR current_stage IN ("
            "'shadow', 'canary_5', 'canary_20', 'canary_50', "
            "'full_100', 'observing'"
            ")",
            name="ck_rollout_campaign_current_stage",
        ),
        Index(
            "ix_rollout_campaign_status_updated",
            "status",
            "updated_at",
        ),
    )

    def to_dict(self):
        return {column.name: getattr(self, column.name) for column in self.__table__.columns}


class RolloutStageAttemptRecord(Base):
    """Immutable stage scope with mutable execution and approval outcome."""

    __tablename__ = "netops_rollout_stage_attempts"

    id = Column(String(64), primary_key=True)
    campaign_id = Column(
        String(64),
        ForeignKey("netops_rollout_campaigns.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    stage = Column(String(32), nullable=False, index=True)
    attempt_number = Column(Integer, nullable=False)
    percentage = Column(Integer, nullable=False)
    status = Column(
        String(32),
        nullable=False,
        default="pending",
        server_default="pending",
        index=True,
    )
    target_manifest_digest = Column(String(64), nullable=False)
    runtime_bundle_digest = Column(String(64), nullable=False)
    comparison_context_digest = Column(String(64), nullable=False)
    baseline_window_json = Column(JSON, nullable=False)
    thresholds_json = Column(JSON, nullable=False)
    metrics_json = Column(JSON, nullable=True)
    comparison_result_json = Column(JSON, nullable=True)
    evidence_refs_json = Column(JSON, nullable=True)
    blockers_json = Column(JSON, nullable=True)
    decision_digest = Column(String(64), nullable=True, index=True)
    started_by = Column(String(128), nullable=True, index=True)
    approved_by = Column(String(128), nullable=True, index=True)
    approval_comment = Column(Text, nullable=True)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    approved_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint(
            "stage IN ("
            "'shadow', 'canary_5', 'canary_20', 'canary_50', "
            "'full_100', 'observing'"
            ")",
            name="ck_rollout_stage_attempt_stage",
        ),
        CheckConstraint(
            "status IN ("
            "'pending', 'running', 'awaiting_approval', 'passed', "
            "'failed', 'paused', 'rolled_back'"
            ")",
            name="ck_rollout_stage_attempt_status",
        ),
        CheckConstraint(
            "percentage IN (0, 5, 20, 50, 100)",
            name="ck_rollout_stage_attempt_percentage",
        ),
        Index(
            "uq_rollout_stage_attempt",
            "campaign_id",
            "stage",
            "attempt_number",
            unique=True,
        ),
        Index(
            "ix_rollout_stage_attempt_campaign_status",
            "campaign_id",
            "status",
        ),
    )

    def to_dict(self):
        return {column.name: getattr(self, column.name) for column in self.__table__.columns}


class RolloutLegacyRouteRecord(Base):
    """Immutable, versioned adapter route for a pre-RuntimeBundle baseline."""

    __tablename__ = "netops_rollout_legacy_routes"

    id = Column(String(64), primary_key=True)
    route_name = Column(String(256), nullable=False)
    check_item_version_id = Column(String(64), nullable=False, index=True)
    implementation_version_id = Column(String(64), nullable=False, index=True)
    adapter_kind = Column(String(64), nullable=False, index=True)
    adapter_version = Column(String(32), nullable=False)
    config_json = Column(JSON, nullable=False)
    digest = Column(String(64), nullable=False, unique=True, index=True)
    status = Column(
        String(32),
        nullable=False,
        default="active",
        server_default="active",
        index=True,
    )
    idempotency_key = Column(String(128), nullable=False, unique=True)
    created_by = Column(String(128), nullable=False, index=True)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    deprecated_by = Column(String(128), nullable=True)
    deprecated_at = Column(DateTime(timezone=True), nullable=True)
    deprecation_reason = Column(Text, nullable=True)

    __table_args__ = (
        CheckConstraint(
            "status IN ('active', 'deprecated')",
            name="ck_rollout_legacy_route_status",
        ),
        Index(
            "ix_rollout_legacy_route_scope_status",
            "check_item_version_id",
            "implementation_version_id",
            "status",
        ),
    )

    def to_dict(self):
        return {column.name: getattr(self, column.name) for column in self.__table__.columns}


class RolloutStageExecutionRecord(Base):
    """Durable execution lease for one immutable rollout stage attempt."""

    __tablename__ = "netops_rollout_stage_executions"

    id = Column(String(64), primary_key=True)
    campaign_id = Column(
        String(64),
        ForeignKey("netops_rollout_campaigns.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    stage_attempt_id = Column(
        String(64),
        ForeignKey("netops_rollout_stage_attempts.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    execution_mode = Column(
        String(32),
        nullable=False,
        default="snapshot_replay",
        server_default="snapshot_replay",
    )
    status = Column(
        String(32),
        nullable=False,
        default="queued",
        server_default="queued",
        index=True,
    )
    selection_manifest_json = Column(JSON, nullable=False)
    selection_manifest_digest = Column(String(64), nullable=False, index=True)
    capture_batch_id = Column(String(128), nullable=True, index=True)
    capture_manifest_json = Column(JSON, nullable=True)
    capture_manifest_digest = Column(String(64), nullable=True, index=True)
    capture_requested_at = Column(DateTime(timezone=True), nullable=True)
    capture_completed_at = Column(DateTime(timezone=True), nullable=True)
    source_snapshot_ids_json = Column(JSON, nullable=False)
    snapshot_set_digest = Column(String(64), nullable=False, index=True)
    fact_high_watermark = Column(DateTime(timezone=True), nullable=False)
    baseline_projection_digest = Column(String(64), nullable=True, index=True)
    candidate_projection_digest = Column(String(64), nullable=True, index=True)
    comparison_manifest_id = Column(String(64), nullable=True, index=True)
    decision_digest = Column(String(64), nullable=True, index=True)
    worker_task_id = Column(String(128), nullable=True, index=True)
    worker_owner = Column(String(256), nullable=True, index=True)
    fencing_token = Column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )
    heartbeat_at = Column(DateTime(timezone=True), nullable=True, index=True)
    attempt_count = Column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )
    failure_code = Column(String(128), nullable=True, index=True)
    error = Column(Text, nullable=True)
    created_by = Column(String(128), nullable=False, index=True)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        CheckConstraint(
            "execution_mode IN ('snapshot_replay', 'live_capture')",
            name="ck_rollout_stage_execution_mode",
        ),
        CheckConstraint(
            "status IN ("
            "'queued', 'capturing', 'running', 'evaluating', 'completed', "
            "'failed', 'cancelled'"
            ")",
            name="ck_rollout_stage_execution_status",
        ),
        Index(
            "ix_rollout_stage_execution_campaign_status",
            "campaign_id",
            "status",
        ),
    )

    def to_dict(self):
        return {column.name: getattr(self, column.name) for column in self.__table__.columns}


class RolloutProjectionRecord(Base):
    """Isolated baseline or candidate facts for one frozen target pair."""

    __tablename__ = "netops_rollout_projections"

    id = Column(String(64), primary_key=True)
    stage_execution_id = Column(
        String(64),
        ForeignKey("netops_rollout_stage_executions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    track = Column(String(16), nullable=False, index=True)
    target_key = Column(String(64), nullable=False, index=True)
    route_json = Column(JSON, nullable=False)
    runtime_bundle_version_id = Column(String(64), nullable=True, index=True)
    snapshot_ids_json = Column(JSON, nullable=False)
    snapshot_set_digest = Column(String(64), nullable=False, index=True)
    status = Column(
        String(32),
        nullable=False,
        default="pending",
        server_default="pending",
        index=True,
    )
    facts_json = Column(JSON, nullable=True)
    facts_digest = Column(String(64), nullable=True, index=True)
    failure_code = Column(String(128), nullable=True, index=True)
    error = Column(Text, nullable=True)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    completed_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint(
            "track IN ('baseline', 'candidate')",
            name="ck_rollout_projection_track",
        ),
        CheckConstraint(
            "status IN ('pending', 'running', 'completed', 'failed')",
            name="ck_rollout_projection_status",
        ),
        Index(
            "uq_rollout_projection_target",
            "stage_execution_id",
            "track",
            "target_key",
            unique=True,
        ),
        Index(
            "ix_rollout_projection_execution_status",
            "stage_execution_id",
            "status",
        ),
    )

    def to_dict(self):
        return {column.name: getattr(self, column.name) for column in self.__table__.columns}


class RolloutComparisonEvidenceRecord(Base):
    """Immutable evaluator output used by the independent rollout approver."""

    __tablename__ = "netops_rollout_comparison_evidence"

    id = Column(String(64), primary_key=True)
    stage_execution_id = Column(
        String(64),
        ForeignKey("netops_rollout_stage_executions.id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
        index=True,
    )
    campaign_id = Column(
        String(64),
        ForeignKey("netops_rollout_campaigns.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    stage_attempt_id = Column(
        String(64),
        ForeignKey("netops_rollout_stage_attempts.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    schema_version = Column(
        String(64),
        nullable=False,
        default="rollout-comparison-evidence/v1",
        server_default="rollout-comparison-evidence/v1",
    )
    manifest_json = Column(JSON, nullable=False)
    manifest_digest = Column(String(64), nullable=False, unique=True, index=True)
    artifact_object_key = Column(String(1024), nullable=False, unique=True)
    artifact_sha256 = Column(String(64), nullable=False, index=True)
    fact_high_watermark = Column(DateTime(timezone=True), nullable=False)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    def to_dict(self):
        return {column.name: getattr(self, column.name) for column in self.__table__.columns}


class RolloutOutboxRecord(Base):
    """Transactional handoff from rollout control state to Celery."""

    __tablename__ = "netops_rollout_outbox"

    id = Column(String(64), primary_key=True)
    event_type = Column(String(128), nullable=False, index=True)
    aggregate_id = Column(
        String(64),
        ForeignKey("netops_rollout_stage_executions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    payload_json = Column(JSON, nullable=False)
    status = Column(
        String(32),
        nullable=False,
        default="pending",
        server_default="pending",
        index=True,
    )
    attempts = Column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )
    available_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        index=True,
    )
    lease_until = Column(DateTime(timezone=True), nullable=True, index=True)
    claim_token = Column(String(64), nullable=True, index=True)
    last_error = Column(Text, nullable=True)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    published_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint(
            "status IN ("
            "'pending', 'publishing', 'published', 'retry_wait', "
            "'dead_letter'"
            ")",
            name="ck_rollout_outbox_status",
        ),
        Index(
            "ix_rollout_outbox_delivery",
            "status",
            "available_at",
        ),
    )

    def to_dict(self):
        return {column.name: getattr(self, column.name) for column in self.__table__.columns}


class CurrentStateFindingRecord(Base):
    """Immutable evidence that a current observation matched a fault rule."""

    __tablename__ = "netops_current_state_findings"

    id = Column(String(64), primary_key=True)
    idempotency_key = Column(String(64), nullable=False, unique=True)
    patrol_batch_id = Column(String(128), nullable=False, index=True)
    check_execution_id = Column(
        String(64),
        ForeignKey("netops_check_executions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    device_id = Column(String(256), nullable=False, index=True)
    check_item_version_id = Column(
        String(64),
        ForeignKey("netops_check_item_versions.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    implementation_version_id = Column(
        String(64),
        ForeignKey("netops_collection_implementation_versions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    entity_type = Column(String(128), nullable=False, index=True)
    entity_key = Column(String(1024), nullable=False)
    dimensions_json = Column(JSON, nullable=True)
    observation_id = Column(
        String(64),
        ForeignKey("netops_state_observations.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    observation_type = Column(String(256), nullable=True, index=True)
    actual_value_json = Column(JSON, nullable=True)
    actual_value_text = Column(Text, nullable=True)
    actual_value_number = Column(Float, nullable=True)
    unit = Column(String(64), nullable=True)
    reference_value_json = Column(JSON, nullable=True)
    reference_value_text = Column(Text, nullable=True)
    reference_value_number = Column(Float, nullable=True)
    reference_source = Column(String(128), nullable=True)
    severity = Column(String(32), nullable=False, index=True)
    event_type = Column(String(128), nullable=False, index=True)
    event_rule_version_id = Column(
        String(64),
        ForeignKey("netops_event_rule_versions.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    rule_digest = Column(String(64), nullable=False, index=True)
    runtime_bundle_version_id = Column(
        String(64),
        ForeignKey("netops_check_item_runtime_bundle_versions.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    runtime_bundle_digest = Column(String(64), nullable=False, index=True)
    assessment_basis_json = Column(JSON, nullable=False, default=dict)
    assessment_basis_digest = Column(String(64), nullable=True, index=True)
    observed_at = Column(DateTime(timezone=True), nullable=False, index=True)
    assessed_at = Column(DateTime(timezone=True), nullable=False, index=True)
    evidence_refs_json = Column(JSON, nullable=False)
    evidence_digest = Column(String(64), nullable=False, index=True)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        Index(
            "ix_current_state_finding_scope",
            "patrol_batch_id",
            "device_id",
            "check_item_version_id",
        ),
    )

    def to_dict(self):
        return {column.name: getattr(self, column.name) for column in self.__table__.columns}


class RiskCaseRecord(Base):
    """Cross-batch operational risk projected from immutable findings and events."""

    __tablename__ = "netops_risk_cases"

    id = Column(String(64), primary_key=True)
    episode_id = Column(
        String(64),
        nullable=False,
        default=lambda: str(uuid.uuid4()),
        index=True,
    )
    correlation_key = Column(String(1024), nullable=False, unique=True)
    check_item_id = Column(
        String(128),
        ForeignKey("netops_check_items.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    device_id = Column(String(256), nullable=False, index=True)
    entity_type = Column(String(128), nullable=False, index=True)
    entity_key = Column(String(1024), nullable=False)
    correlation_dimensions_json = Column(JSON, nullable=True)
    risk_type = Column(String(128), nullable=False, index=True)
    severity = Column(String(32), nullable=False, index=True)
    status = Column(String(32), nullable=False, default="open", server_default="open", index=True)
    first_seen_at = Column(DateTime(timezone=True), nullable=False, index=True)
    last_seen_at = Column(DateTime(timezone=True), nullable=False, index=True)
    last_successful_evaluation_at = Column(DateTime(timezone=True), nullable=True, index=True)
    verification_status = Column(
        String(32),
        nullable=False,
        default="not_reverified",
        server_default="not_reverified",
        index=True,
    )
    last_verified_at = Column(DateTime(timezone=True), nullable=True, index=True)
    last_verification_execution_id = Column(String(64), nullable=True, index=True)
    resolved_at = Column(DateTime(timezone=True), nullable=True, index=True)
    closed_at = Column(DateTime(timezone=True), nullable=True, index=True)
    closed_by = Column(String(128), nullable=True)
    closure_reason = Column(Text, nullable=True)
    closure_disposition = Column(String(32), nullable=True)
    closure_evidence_refs_json = Column(JSON, nullable=True)
    source_finding_ids_json = Column(JSON, nullable=False, default=list)
    source_event_ids_json = Column(JSON, nullable=False, default=list)
    owner = Column(String(128), nullable=True, index=True)
    remediation_status = Column(
        String(32), nullable=False, default="not_started", server_default="not_started", index=True
    )
    due_at = Column(DateTime(timezone=True), nullable=True, index=True)
    resolution_evidence_refs_json = Column(JSON, nullable=True)
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
        CheckConstraint(
            "status IN ('open', 'acknowledged', 'recovering', 'resolved', 'suppressed')",
            name="ck_risk_case_status",
        ),
        CheckConstraint(
            "remediation_status IN ("
            "'not_started', 'planned', 'in_progress', 'completed', "
            "'accepted', 'cancelled')",
            name="ck_risk_case_remediation_status",
        ),
        CheckConstraint(
            "verification_status IN "
            "('confirmed_current', 'recovery_pending', 'not_reverified', 'resolved')",
            name="ck_risk_case_verification_status",
        ),
        Index("ix_risk_case_active_scope", "check_item_id", "device_id", "status"),
    )

    def to_dict(self):
        return {column.name: getattr(self, column.name) for column in self.__table__.columns}


class RiskEpisodeAssociationRecord(Base):
    """Governed link between two immutable risk lifecycle episodes."""

    __tablename__ = "netops_risk_episode_associations"

    id = Column(String(64), primary_key=True)
    idempotency_key = Column(String(128), nullable=False, unique=True)
    request_digest = Column(String(64), nullable=False)
    primary_risk_case_id = Column(
        String(64),
        ForeignKey("netops_risk_cases.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    primary_risk_episode_id = Column(String(64), nullable=False, index=True)
    related_risk_case_id = Column(
        String(64),
        ForeignKey("netops_risk_cases.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    related_risk_episode_id = Column(String(64), nullable=False, index=True)
    relation_type = Column(String(32), nullable=False, index=True)
    status = Column(
        String(32),
        nullable=False,
        default="pending_review",
        server_default="pending_review",
        index=True,
    )
    source_type = Column(
        String(32),
        nullable=False,
        default="manual",
        server_default="manual",
        index=True,
    )
    policy_ref_json = Column(JSON, nullable=True)
    evidence_refs_json = Column(JSON, nullable=False, default=list)
    reason = Column(Text, nullable=False)
    proposed_by = Column(String(128), nullable=False, index=True)
    reviewed_by = Column(String(128), nullable=True, index=True)
    review_reason = Column(Text, nullable=True)
    reviewed_at = Column(DateTime(timezone=True), nullable=True, index=True)
    revoked_by = Column(String(128), nullable=True, index=True)
    revoke_reason = Column(Text, nullable=True)
    revoked_at = Column(DateTime(timezone=True), nullable=True, index=True)
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
        CheckConstraint(
            "primary_risk_case_id <> related_risk_case_id",
            name="ck_risk_episode_association_distinct_cases",
        ),
        CheckConstraint(
            "primary_risk_episode_id <> related_risk_episode_id",
            name="ck_risk_episode_association_distinct_episodes",
        ),
        CheckConstraint(
            "relation_type IN ('same_root_cause', 'causal_symptom', 'shared_incident')",
            name="ck_risk_episode_association_relation_type",
        ),
        CheckConstraint(
            "status IN ('pending_review', 'approved', 'rejected', 'revoked')",
            name="ck_risk_episode_association_status",
        ),
        CheckConstraint(
            "source_type IN ('manual', 'approved_policy')",
            name="ck_risk_episode_association_source_type",
        ),
        Index(
            "uq_risk_episode_association_live_pair",
            "primary_risk_episode_id",
            "related_risk_episode_id",
            unique=True,
            postgresql_where=text("status IN ('pending_review', 'approved')"),
            sqlite_where=text("status IN ('pending_review', 'approved')"),
        ),
        Index(
            "uq_risk_episode_association_approved_child",
            "related_risk_episode_id",
            unique=True,
            postgresql_where=text("status = 'approved'"),
            sqlite_where=text("status = 'approved'"),
        ),
        Index(
            "ix_risk_episode_association_primary_status",
            "primary_risk_episode_id",
            "status",
        ),
    )

    def to_dict(self):
        return {column.name: getattr(self, column.name) for column in self.__table__.columns}


class RiskOccurrenceRecord(Base):
    """Append-only lifecycle transition for a RiskCase."""

    __tablename__ = "netops_risk_occurrences"

    id = Column(String(64), primary_key=True)
    idempotency_key = Column(String(64), nullable=False, unique=True)
    risk_case_id = Column(
        String(64),
        ForeignKey("netops_risk_cases.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    risk_episode_id = Column(String(64), nullable=False, index=True)
    occurrence_kind = Column(String(32), nullable=False, index=True)
    source_finding_id = Column(
        String(64),
        ForeignKey("netops_current_state_findings.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    source_event_id = Column(
        String(64),
        ForeignKey("netops_network_events.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    patrol_batch_id = Column(String(128), nullable=True, index=True)
    observed_at = Column(DateTime(timezone=True), nullable=False, index=True)
    status_after = Column(String(32), nullable=False)
    actor = Column(String(128), nullable=True)
    reason = Column(Text, nullable=True)
    evidence_refs_json = Column(JSON, nullable=True)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        CheckConstraint(
            "occurrence_kind IN ("
            "'opened', 'observed', 'reopened', 'acknowledged', 'recovering', "
            "'resolved', 'closed', 'suppressed', 'unable_to_confirm')",
            name="ck_risk_occurrence_kind",
        ),
    )

    def to_dict(self):
        return {column.name: getattr(self, column.name) for column in self.__table__.columns}


class RiskRemediationRecord(Base):
    """Remediation workflow kept separate from technical recovery state."""

    __tablename__ = "netops_risk_remediations"

    id = Column(String(64), primary_key=True)
    risk_case_id = Column(
        String(64),
        ForeignKey("netops_risk_cases.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    risk_episode_id = Column(String(64), nullable=False, index=True)
    status = Column(
        String(32),
        nullable=False,
        default="planned",
        server_default="planned",
        index=True,
    )
    owner = Column(String(128), nullable=True, index=True)
    action_text = Column(Text, nullable=False)
    due_at = Column(DateTime(timezone=True), nullable=True, index=True)
    evidence_refs_json = Column(JSON, nullable=True)
    created_by = Column(String(128), nullable=True)
    completed_by = Column(String(128), nullable=True)
    accepted_by = Column(String(128), nullable=True)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    completed_at = Column(DateTime(timezone=True), nullable=True)
    accepted_at = Column(DateTime(timezone=True), nullable=True)
    acceptance_reason = Column(Text, nullable=True)
    acceptance_evidence_refs_json = Column(JSON, nullable=True)
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ("
            "'planned', 'in_progress', 'completed', 'accepted', 'cancelled')",
            name="ck_risk_remediation_status",
        ),
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
    template_text = Column(Text, nullable=True)
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

    def to_dict(self, *, include_content: bool = False):
        return {
            column.name: getattr(self, column.name)
            for column in self.__table__.columns
            if include_content or column.name != "template_text"
        }




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
    check_item_version_id = Column(
        String(64),
        ForeignKey("netops_check_item_versions.id", ondelete="SET NULL"),
        nullable=True,
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
            "check_item_version_id",
            "observation_type",
            unique=True,
            postgresql_where=text(
                "status = 'active' AND check_item_version_id IS NOT NULL"
            ),
        ),
        Index(
            "uq_change_policy_active_observation_type_legacy",
            "observation_type",
            unique=True,
            postgresql_where=text(
                "status = 'active' AND check_item_version_id IS NULL"
            ),
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
    pipeline_version = Column(
        String(32),
        nullable=False,
        default="simple_v2",
        server_default="simple_v2",
        index=True,
    )
    parser_spec_status = Column(
        String(32),
        nullable=False,
        default="missing",
        server_default="missing",
        index=True,
    )
    parser_spec_json = Column(JSON, nullable=True)
    parser_spec_sha256 = Column(String(64), nullable=True, index=True)
    parser_spec_revision = Column(Integer, nullable=False, default=0, server_default="0")
    sample_count = Column(Integer, default=0)
    attempts = Column(Integer, default=0)
    contract_revision_count = Column(Integer, nullable=False, default=0, server_default="0")
    template_attempt_count = Column(Integer, nullable=False, default=0, server_default="0")
    model_calls_used = Column(Integer, nullable=False, default=0, server_default="0")
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


class ParserSpecRevisionRecord(Base):
    """Immutable engineer-visible history for one ParserSpec."""

    __tablename__ = "netops_parser_spec_revisions"

    id = Column(String(64), primary_key=True)
    job_id = Column(
        String(64),
        ForeignKey("parser_template_jobs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    revision_number = Column(Integer, nullable=False)
    parser_spec_status = Column(String(32), nullable=False)
    parser_spec_json = Column(JSON, nullable=False)
    parser_spec_sha256 = Column(String(64), nullable=False, index=True)
    previous_parser_spec_sha256 = Column(String(64), nullable=True)
    source = Column(String(32), nullable=False)
    actor = Column(String(128), nullable=False)
    reason = Column(Text, nullable=False)
    invalidated_template_ids_json = Column(JSON, nullable=False, default=list)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        Index(
            "uq_parser_spec_revision_job_number",
            "job_id",
            "revision_number",
            unique=True,
        ),
        CheckConstraint(
            "parser_spec_status IN ('draft', 'confirmed')",
            name="ck_parser_spec_revision_status",
        ),
        CheckConstraint(
            "source IN ('engineer', 'llm_suggestion')",
            name="ck_parser_spec_revision_source",
        ),
    )

    def to_dict(self):
        return {column.name: getattr(self, column.name) for column in self.__table__.columns}


class ParserGapLedgerRecord(Base):
    """Production parse-gap ledger (draft-only; never auto-dispatches LLM)."""

    __tablename__ = "netops_parser_gap_ledger"

    id = Column(String(64), primary_key=True)
    vendor = Column(String(64), nullable=False)
    model = Column(String(128), nullable=True)
    family = Column(String(128), nullable=True)
    canonical_command = Column(String(256), nullable=False)
    failure_code = Column(String(64), nullable=False)
    occurrence_count = Column(Integer, nullable=False, default=0, server_default="0")
    first_seen_at = Column(DateTime(timezone=True), nullable=False)
    last_seen_at = Column(DateTime(timezone=True), nullable=False)
    last_run_id = Column(String(128), nullable=True)
    last_device_id = Column(String(256), nullable=True)
    draft_job_id = Column(String(64), nullable=True)
    metadata_json = Column(JSON, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False)
    updated_at = Column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        Index(
            "uq_parser_gap_ledger_key",
            "vendor",
            "canonical_command",
            "failure_code",
            unique=True,
        ),
        Index("ix_parser_gap_ledger_last_seen", "last_seen_at"),
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


class ParserModelCallLedger(Base):
    __tablename__ = "parser_model_call_ledger"

    id = Column(String(64), primary_key=True)
    job_id = Column(
        String(64),
        ForeignKey("parser_template_jobs.id", ondelete="CASCADE"),
        nullable=False,
    )
    call_number = Column(Integer, nullable=False)
    generation_run_id = Column(String(64), nullable=False, index=True)
    status = Column(String(32), nullable=False, default="reserved", index=True)
    model_provider = Column(String(64), nullable=True)
    model_name = Column(String(128), nullable=True)
    protocol_version = Column(String(64), nullable=True)
    candidate_sha256 = Column(String(64), nullable=True, index=True)
    failure_signature = Column(String(64), nullable=True, index=True)
    result_json = Column(JSON, nullable=False, default=dict)
    token_usage_json = Column(JSON, nullable=True)
    latency_ms = Column(Integer, nullable=True)
    error = Column(Text, nullable=True)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    completed_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index(
            "uq_parser_model_call_job_number",
            "job_id",
            "call_number",
            unique=True,
        ),
        Index(
            "ix_parser_model_call_job_created",
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
    sample_role = Column(
        String(32),
        nullable=False,
        default="unassigned",
        server_default="unassigned",
        index=True,
    )
    expectation_json = Column(JSON, nullable=True)
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
    lifecycle_status = Column(
        String(32),
        nullable=False,
        default="current",
        server_default="current",
        index=True,
    )
    retired_at = Column(DateTime(timezone=True), nullable=True)
    retirement_reason = Column(Text, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        Index("uq_parser_job_snapshot", "job_id", "snapshot_id", unique=True),
        CheckConstraint(
            "snapshot_source = 'postgresql'",
            name="ck_parser_job_samples_postgresql_source",
        ),
        CheckConstraint(
            "lifecycle_status IN ('current', 'retired')",
            name="ck_parser_job_samples_lifecycle_status",
        ),
    )

    def to_dict(self):
        return {column.name: getattr(self, column.name) for column in self.__table__.columns}


class ParserCandidateSampleEvidence(Base):
    """Immutable source-sample binding for one generated Parser candidate."""

    __tablename__ = "parser_candidate_sample_evidence"

    id = Column(String(64), primary_key=True)
    template_id = Column(
        String(64),
        ForeignKey("parser_templates.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    sample_id = Column(
        String(64),
        ForeignKey("parser_job_samples.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    manifest_item_json = Column(JSON, nullable=False)
    manifest_item_sha256 = Column(String(64), nullable=False, index=True)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        Index(
            "uq_parser_candidate_sample_evidence",
            "template_id",
            "sample_id",
            unique=True,
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


class RuleAssetCommandUnitRecord(Base):
    """Materialized command-level production flow for one offline patrol job."""

    __tablename__ = "netops_rule_asset_command_units"

    id = Column(String(64), primary_key=True)
    offline_job_id = Column(
        String(64),
        ForeignKey("netops_offline_patrol_jobs.id", ondelete="CASCADE"),
        nullable=False,
    )
    check_item_id = Column(String(128), nullable=True, index=True)
    asset_key = Column(String(64), nullable=False)
    vendor = Column(String(64), nullable=False)
    model = Column(String(128), nullable=True)
    models_json = Column(JSON, nullable=False, default=list)
    family = Column(String(128), nullable=True)
    profile = Column(String(128), nullable=False)
    canonical_command = Column(String(512), nullable=False)
    handling = Column(String(32), nullable=False)
    asset_mode = Column(String(32), nullable=False)
    status = Column(String(32), nullable=False)
    current_stage = Column(String(64), nullable=False)
    progress_percent = Column(Integer, nullable=False, default=0, server_default="0")
    sample_count = Column(Integer, nullable=False, default=0, server_default="0")
    device_count = Column(Integer, nullable=False, default=0, server_default="0")
    output_signature_count = Column(Integer, nullable=False, default=0, server_default="0")
    blocker_count = Column(Integer, nullable=False, default=0, server_default="0")
    parse_counts_json = Column(JSON, nullable=False, default=dict)
    stage_states_json = Column(JSON, nullable=False, default=list)
    next_action_json = Column(JSON, nullable=False, default=dict)
    parser_job_id = Column(String(64), nullable=True)
    parser_template_id = Column(String(64), nullable=True)
    rule_seed_job_id = Column(String(64), nullable=True)
    context_id = Column(String(64), nullable=True)
    bundle_id = Column(String(64), nullable=True)
    projection_version = Column(Integer, nullable=False, default=1, server_default="1")
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
            "uq_rule_asset_command_unit_job_key",
            "offline_job_id",
            "asset_key",
            unique=True,
        ),
        Index(
            "ix_rule_asset_command_unit_job_status",
            "offline_job_id",
            "status",
        ),
        Index(
            "ix_rule_asset_command_unit_job_stage",
            "offline_job_id",
            "current_stage",
        ),
        Index(
            "ix_rule_asset_command_unit_job_mode",
            "offline_job_id",
            "asset_mode",
        ),
        Index(
            "ix_rule_asset_command_unit_identity",
            "vendor",
            "profile",
            "canonical_command",
        ),
        Index(
            "ix_rule_asset_command_unit_asset_updated",
            "asset_key",
            "updated_at",
        ),
    )

    def to_dict(self):
        return {column.name: getattr(self, column.name) for column in self.__table__.columns}


class RuleAssetCommandUnitCheckItemRecord(Base):
    """Catalog-approved many-to-many projection from command units to check items."""

    __tablename__ = "netops_rule_asset_command_unit_check_items"

    id = Column(String(64), primary_key=True)
    command_unit_id = Column(
        String(64),
        ForeignKey("netops_rule_asset_command_units.id", ondelete="CASCADE"),
        nullable=False,
    )
    check_item_id = Column(
        String(128),
        ForeignKey("netops_check_items.id", ondelete="RESTRICT"),
        nullable=False,
    )
    check_item_version_id = Column(
        String(64),
        ForeignKey("netops_check_item_versions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    implementation_id = Column(
        String(128),
        ForeignKey("netops_collection_implementations.id", ondelete="RESTRICT"),
        nullable=False,
    )
    implementation_version_id = Column(
        String(64),
        ForeignKey("netops_collection_implementation_versions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    mapping_source = Column(
        String(32),
        nullable=False,
        default="catalog_resolver",
        server_default="catalog_resolver",
    )
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        Index("ix_rule_asset_link_unit", "command_unit_id"),
        Index("ix_rule_asset_link_check", "check_item_id"),
        Index("ix_rule_asset_link_check_ver", "check_item_version_id"),
        Index("ix_rule_asset_link_impl", "implementation_id"),
        Index("ix_rule_asset_link_impl_ver", "implementation_version_id"),
        Index(
            "uq_rule_asset_command_unit_check_item",
            "command_unit_id",
            "check_item_id",
            "implementation_version_id",
            unique=True,
        ),
        Index(
            "ix_rule_asset_check_item_implementation",
            "check_item_id",
            "implementation_version_id",
        ),
    )

    def to_dict(self):
        return {column.name: getattr(self, column.name) for column in self.__table__.columns}


class RuleReplayReportRecord(Base):
    """Queryable projection of the current historical replay for one rule job."""

    __tablename__ = "netops_rule_replay_reports"

    job_id = Column(
        String(64),
        ForeignKey("netops_rule_generation_jobs.id", ondelete="CASCADE"),
        primary_key=True,
    )
    success = Column(Boolean, nullable=True, index=True)
    sampled_change_count = Column(Integer, nullable=False, default=0, server_default="0")
    matched_change_count = Column(Integer, nullable=False, default=0, server_default="0")
    estimated_daily_event_count = Column(Float, nullable=False, default=0, server_default="0")
    collision_count = Column(Integer, nullable=False, default=0, server_default="0")
    event_types_json = Column(JSON, nullable=False, default=dict)
    matched_entity_types_json = Column(JSON, nullable=False, default=dict)
    top_devices_json = Column(JSON, nullable=False, default=dict)
    collision_rules_json = Column(JSON, nullable=False, default=dict)
    unexpected_matches_json = Column(JSON, nullable=False, default=list)
    recovery_pair_coverage_json = Column(JSON, nullable=True)
    report_json = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), nullable=True)
    updated_at = Column(DateTime(timezone=True), nullable=True, index=True)

    __table_args__ = (
        Index("ix_rule_replay_success_updated", "success", "updated_at"),
    )

    def to_dict(self):
        return {column.name: getattr(self, column.name) for column in self.__table__.columns}


class RuleApprovalAuditRecord(Base):
    """Append-semantic governance event projected from a rule job report."""

    __tablename__ = "netops_rule_approval_audit"

    id = Column(String(64), primary_key=True)
    job_id = Column(
        String(64),
        ForeignKey("netops_rule_generation_jobs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    action = Column(String(32), nullable=False, index=True)
    actor = Column(String(128), nullable=True, index=True)
    reason = Column(Text, nullable=True)
    occurred_at = Column(DateTime(timezone=True), nullable=True, index=True)
    validation_success = Column(Boolean, nullable=True)
    synthetic_success = Column(Boolean, nullable=True)
    replay_success = Column(Boolean, nullable=True)
    governance_json = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), nullable=True)
    updated_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint("job_id", "action", name="uq_rule_approval_job_action"),
        Index("ix_rule_approval_action_occurred", "action", "occurred_at"),
    )

    def to_dict(self):
        return {column.name: getattr(self, column.name) for column in self.__table__.columns}


class RuleAgentLoopAttemptRecord(Base):
    """Queryable projection of one rule-generation Agent Loop attempt."""

    __tablename__ = "netops_rule_agent_loop_attempts"

    id = Column(String(64), primary_key=True)
    job_id = Column(
        String(64),
        ForeignKey("netops_rule_generation_jobs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    attempt_ordinal = Column(Integer, nullable=False)
    attempt_number = Column(Integer, nullable=False)
    stage = Column(String(128), nullable=True, index=True)
    generator = Column(String(128), nullable=True)
    model_name = Column(String(128), nullable=True, index=True)
    mode = Column(String(64), nullable=True)
    success = Column(Boolean, nullable=True, index=True)
    input_summary_json = Column(JSON, nullable=False, default=dict)
    output_summary_json = Column(JSON, nullable=False, default=dict)
    validator_feedback_json = Column(JSON, nullable=False, default=dict)
    revision_request_json = Column(JSON, nullable=True)
    ready_for_approval = Column(Boolean, nullable=True)
    next_action = Column(String(256), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=True)
    updated_at = Column(DateTime(timezone=True), nullable=True, index=True)

    __table_args__ = (
        Index("ix_rule_agent_loop_job_ordinal", "job_id", "attempt_ordinal"),
        Index("ix_rule_agent_loop_stage_success", "stage", "success"),
        Index("ix_rule_agent_loop_updated_job", "updated_at", "job_id"),
    )

    def to_dict(self):
        return {column.name: getattr(self, column.name) for column in self.__table__.columns}


@event.listens_for(RuleGenerationJobRecord, "after_insert")
@event.listens_for(RuleGenerationJobRecord, "after_update")
def _sync_rule_generation_job_projections(_mapper, connection, target) -> None:
    if connection.dialect.name == "sqlite":
        from sqlalchemy import inspect

        if not inspect(connection).has_table("netops_rule_replay_reports"):
            return
    from src.infrastructure.db.rule_control_projections import (
        synchronize_rule_job_projections,
    )

    synchronize_rule_job_projections(connection, target)


class LegacyPathObservationRecord(Base):
    """Append-only, payload-free evidence that a governed legacy path was used."""

    __tablename__ = "netops_legacy_path_observations"

    id = Column(String(64), primary_key=True)
    candidate_id = Column(String(128), nullable=False, index=True)
    operation = Column(String(128), nullable=False)
    outcome = Column(String(64), nullable=False)
    observed_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        index=True,
    )

    __table_args__ = (
        Index(
            "ix_legacy_path_candidate_observed",
            "candidate_id",
            "observed_at",
        ),
        Index(
            "ix_legacy_path_operation_observed",
            "operation",
            "observed_at",
        ),
    )

    def to_dict(self):
        return {column.name: getattr(self, column.name) for column in self.__table__.columns}
