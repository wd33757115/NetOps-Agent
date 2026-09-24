"""Agent rule tools use the same CheckItem semantic context as the workbench."""
from pydantic import BaseModel, ConfigDict
from src.platform.tools.registry import Tool
from .check_items.canonical_rule_context import CanonicalRuleContextService

class CheckItemRuleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    check_item_version_id: str

TOOLS = [
    Tool("generate_rules", "按已批准检查项语义生成 Change / 状态 / 事件规则草稿；不自动发布",
         CheckItemRuleRequest,
         lambda user, body: CanonicalRuleContextService().create_drafts(body.check_item_version_id, actor=user.username), True),
    Tool("read_rule_context", "查看检查项规则制作所用的 Value 和语义",
         CheckItemRuleRequest,
         lambda user, body: CanonicalRuleContextService().get(body.check_item_version_id)),
]
