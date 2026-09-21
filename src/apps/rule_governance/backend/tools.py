"""Agent tools call the same business services as the application."""

from pydantic import BaseModel, ConfigDict

from src.platform.tools.registry import Tool

from .rule_control.generation_jobs import GenerationJobs
from .rule_control.template_activation import RuleAssetTemplateActivationService


class Generate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    seed_id: str
    context_id: str


class Read(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run_id: str


TOOLS = [
    Tool(
        "generate_rules",
        "从已核验的不可变样本上下文生成规则候选；不自动批准发布",
        Generate,
        lambda user, body: RuleAssetTemplateActivationService().start_generation(
            body.seed_id,
            context_id=body.context_id,
            actor=user.username,
            actor_user_id=user.user_id,
        ),
        True,
    ),
    Tool(
        "read_generation",
        "查询规则生成任务的阶段、结果和失败详情",
        Read,
        lambda user, body: GenerationJobs().read(
            body.run_id, user_id=user.user_id, is_admin=user.is_admin()
        ),
    ),
]
