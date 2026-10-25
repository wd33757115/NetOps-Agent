# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""
Skill 开发模板

使用此模板创建新的运维技能：

1. 复制此文件到 src/skills/examples/ 目录
2. 重命名为 {skill_name}_skill.py
3. 修改以下部分：
   - 参数模型（继承 BaseModel）
   - Handler 函数（执行实际逻辑）
   - register_skill 函数（注册技能）

示例流程：
1. 定义参数模型（Pydantic BaseModel）
2. 实现 handler 函数（执行实际操作）
3. 创建技能类并注册到 SkillRegistry

命名规范：
- 文件命名：{skill_name}_skill.py（全小写，下划线分隔）
- 技能名称：{skill_name}（全小写，下划线分隔）
- 参数模型：{SkillName}Params（驼峰命名）
- Handler 函数：{skill_name}_handler
- 技能类：{SkillName}Skill（驼峰命名）
"""

from typing import Any

from pydantic import BaseModel, Field

from src.skills.registry import skill_registry


# ================================================
# 1. 定义参数模型
# ================================================
class ExampleSkillParams(BaseModel):
    """
    示例技能参数
    
    参数定义规范：
    - 使用 Pydantic BaseModel
    - 所有参数使用 Optional，在 handler 中处理默认值
    - 添加清晰的 description，用于 LLM 理解参数含义
    """
    param1: str | None = Field(None, description="参数1描述")
    param2: int | None = Field(None, description="参数2描述")


# ================================================
# 2. 实现 Handler 函数
# ================================================
def get_default_config():
    """
    获取默认配置（可选）
    
    用于处理默认文件路径等配置项
    """
    import os
    BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
    default_file = os.path.join(BASE_DIR, "tools", "example", "default.conf")
    if os.path.exists(default_file):
        return default_file
    return None


async def example_skill_handler(params: dict[str, Any]) -> dict[str, Any]:
    """
    示例技能处理函数
    
    Handler 函数规范：
    - 接收参数字典
    - 返回标准格式的字典（包含 success, message, data 等字段）
    - 处理默认值和参数校验
    - 调用实际的运维工具或 Celery Task
    
    Args:
        params: 参数字典
        
    Returns:
        Dict: 执行结果，必须包含以下字段：
            - success: bool - 执行是否成功
            - message: str - 执行结果消息
            - data: Dict - 额外数据（可选）
            - error: str - 错误信息（可选，失败时使用）
            - execution_time_ms: int - 执行耗时（毫秒）
    """
    # 获取参数，处理默认值
    param1 = params.get("param1") or "default_value"
    param2 = params.get("param2") or 100

    try:
        # 执行实际操作
        # ... 调用你的运维工具 ...

        return {
            "success": True,
            "message": "操作成功完成",
            "data": {
                "param1": param1,
                "param2": param2
            },
            "execution_time_ms": 100
        }

    except Exception as e:
        return {
            "success": False,
            "message": f"操作失败: {str(e)}",
            "error": str(e),
            "execution_time_ms": 0
        }


# ================================================
# 3. 注册技能
# ================================================
def register_skill():
    """
    注册技能到 SkillRegistry
    
    此函数会被 loader.py 自动扫描并调用
    
    技能注册规范：
    - name: 技能名称（唯一标识，全小写，下划线分隔）
    - description: 技能描述（清晰描述技能功能，用于 LLM 理解）
    - parameters: 参数模型（Pydantic BaseModel）
    - handler: 处理函数或 Celery Task
    - category: 分类（network, system, security, general 等）
    - tags: 标签列表（用于搜索和匹配）
    - fallback_to_rag_if_fail: 失败时是否走 RAG 兜底
    - enabled: 是否启用
    """
    from src.skills.skill_base import BaseSkill, SkillResult

    class ExampleSkill(BaseSkill):
        async def execute(self, **kwargs) -> SkillResult:
            result = await example_skill_handler(kwargs)
            return SkillResult(**result)

    skill = ExampleSkill(
        name="example_skill",
        description="示例技能描述：这是一个示例技能，展示如何创建新技能",
        parameters=ExampleSkillParams,
        handler=example_skill_handler,
        category="general",
        tags=["example", "demo", "template"],
        fallback_to_rag_if_fail=True,
        enabled=True
    )

    skill_registry.register_skill(skill)


# ================================================
# 4. 测试入口（可选）
# ================================================
if __name__ == "__main__":
    register_skill()

    skill = skill_registry.get_skill("example_skill")
    if skill:
        print(f"✅ 技能注册成功: {skill.name}")
        print(f"   描述: {skill.description}")
        print(f"   分类: {skill.category}")
        print(f"   标签: {skill.tags}")
    else:
        print("❌ 技能注册失败")
