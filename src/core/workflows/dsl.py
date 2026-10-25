# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""Workflow Builder DSL — UI 与生成器之间的统一契约。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator


class ExpressionRef(BaseModel):
    """Workflow 表达式引用，生成时渲染为 ${...} 形式。"""

    type: Literal["context", "run", "step_result", "step_artifact", "literal"]
    path: str = Field(default="", description="context/run 路径或 step 子路径")
    value: str | None = Field(default=None, description="literal 类型的固定值")

    @field_validator("path")
    @classmethod
    def _strip_path(cls, v: str) -> str:
        return v.strip()


class WorkflowStepDSL(BaseModel):
    """单个 Workflow 步骤。"""

    id: str = Field(..., description="UI 内部唯一 id")
    name: str = Field(..., description="WORKFLOW.yaml steps[].name")
    label: str = Field(default="", description="展示标签")
    skill: str = Field(default="", description="Skill 名称；subworkflow 步骤可留空")
    subworkflow: str | None = Field(default=None, description="嵌套 Workflow 模板名")
    when: str | None = Field(default=None, description="条件表达式，如 ${context.priority} == high")
    parallel_group: str | None = Field(
        default=None,
        description="并行组 ID；同组步骤并发执行（Engine Phase 4）",
    )
    depends_on: list[str] = Field(
        default_factory=list,
        description="依赖的前置 step name（拓扑校验 / 映射提示）",
    )
    inputs: dict[str, str | ExpressionRef] = Field(
        default_factory=dict,
        description="步骤输入；空 dict 表示由 mapping 引擎自动推断",
    )

    @model_validator(mode="after")
    def _skill_or_subworkflow(self) -> WorkflowStepDSL:
        if not self.skill and not self.subworkflow:
            raise ValueError("steps 需配置 skill 或 subworkflow")
        return self


class ChatIntentMatchDSL(BaseModel):
    require_any: list[str] = Field(default_factory=list)
    require_all: list[str] = Field(default_factory=list)
    require_any_secondary: list[str] = Field(default_factory=list)


class ChatIntentDSL(BaseModel):
    enabled: bool = True
    priority: int = 50
    description: str = ""
    match: ChatIntentMatchDSL = Field(default_factory=ChatIntentMatchDSL)
    required_context: list[str] = Field(
        default_factory=list,
        description="启动 Workflow 前必须解析出的上下文字段，如 ticket_id",
    )
    context_from_state: dict[str, str] = Field(default_factory=dict)
    context_from_query: dict[str, str] = Field(
        default_factory=dict,
        description="将通用查询提取结果映射到 Workflow 上下文，如 baseline_path: path_0",
    )
    context_defaults: dict[str, Any] = Field(default_factory=dict)
    files: dict[str, list[str]] = Field(
        default_factory=dict,
        description="聊天附件自动填写的文件字段及接受的扩展名，如 policy_file_url: [.xlsx]",
    )
    response_template: str = Field(
        default="[OK] 已启动 Workflow\n\n- **流程 ID**: `{run_id}`\n- **步骤**: {workflow_description}\n"
    )


class ItsmWebhookDSL(BaseModel):
    enabled: bool = False
    route_key: str = ""
    accepted_message: str = "已受理，正在处理"
    legacy_paths: list[str] = Field(default_factory=list)
    context_mapping: dict[str, str] = Field(default_factory=dict)


class WorkflowTriggersDSL(BaseModel):
    chat: ChatIntentDSL | None = None
    webhook: ItsmWebhookDSL | None = None


class NotificationDSL(BaseModel):
    title: str = '流程已完成 (${context.ticket_id})'
    body: str = "所有步骤已执行。"
    level: Literal["info", "success", "warning", "error"] = "success"


class OnCompleteDSL(BaseModel):
    message: str = "Workflow 已完成"
    notify_each_step: bool = False
    notify_on_failure: bool = True
    notification: NotificationDSL = Field(default_factory=NotificationDSL)


class WorkflowMetaDSL(BaseModel):
    name: str = Field(..., pattern=r"^[a-z0-9][a-z0-9-]*$")
    description: str = ""
    category: str = "itsm"
    version: str = "1.0"


class WorkflowDSL(BaseModel):
    """Workflow Builder 完整 DSL。"""

    meta: WorkflowMetaDSL
    steps: list[WorkflowStepDSL] = Field(..., min_length=1)
    triggers: WorkflowTriggersDSL = Field(default_factory=WorkflowTriggersDSL)
    on_complete: OnCompleteDSL = Field(default_factory=OnCompleteDSL)

    @field_validator("steps")
    @classmethod
    def _unique_step_names(cls, steps: list[WorkflowStepDSL]) -> list[WorkflowStepDSL]:
        names = [s.name for s in steps]
        if len(names) != len(set(names)):
            raise ValueError("steps[].name 必须唯一")
        return steps


class GenerateOptions(BaseModel):
    """生成选项。"""

    persist: bool = Field(default=False, description="是否写入 workflows/ 目录")
    overwrite: bool = Field(default=False, description="persist 时是否覆盖已存在插件")
    reload: bool = Field(default=True, description="persist 后是否热加载 Registry")
    auto_map_inputs: bool = Field(default=True, description="是否自动推断缺失的 step inputs")
    submit_review: bool = Field(default=False, description="保存后提交审核（status=review）")
    publish: bool = Field(default=False, description="保存后立即发布（admin，创建版本快照）")
    change_summary: str | None = Field(default=None, description="发布/版本说明")
