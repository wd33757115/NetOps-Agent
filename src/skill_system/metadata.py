# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""
Skill 元数据解析模块

负责解析 SKILL.md 文件，提取 frontmatter 元数据和指令内容。
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class InputSpec(BaseModel):
    """Skill 输入规范"""
    name: str = Field(..., description="参数名称")
    type: str = Field(..., description="参数类型：string, int, file, bool")
    required: bool = Field(False, description="是否必填")
    description: str = Field("", description="参数描述")
    default: Any | None = Field(None, description="默认值")


class OutputSpec(BaseModel):
    """Skill 输出规范"""
    name: str = Field(..., description="输出名称")
    type: str = Field(..., description="输出类型：text, download, json")
    description: str = Field("", description="输出描述")


class Reference(BaseModel):
    """Skill 引用（RAG 或文件）"""
    type: str = Field(..., description="引用类型：rag 或 file")
    source: str | None = Field(None, description="RAG 知识源")
    path: str | None = Field(None, description="文件路径")
    description: str | None = Field(None, description="引用描述")


class SkillMetadata(BaseModel):
    """
    Skill 元数据

    对应 SKILL.md 的 frontmatter 部分。
    """
    # 基础信息
    name: str = Field(..., description="Skill 名称（唯一标识）")
    version: str = Field("1.0.0", description="版本号")
    description: str = Field(..., description="简短描述")
    category: str = Field("general", description="分类")
    tags: list[str] = Field(default_factory=list, description="标签")
    author: str | None = Field(None, description="作者")
    author_email: str | None = Field(None, description="作者邮箱")

    # 路由相关
    triggers: list[str] = Field(
        default_factory=list,
        description="触发关键词列表，用于快速匹配"
    )
    domain: str = Field("default", description="业务域（security/network/itsm/...）")
    celery_queue: str | None = Field(None, description="Celery 队列名（如 netops.firewall）")
    deprecated: bool = Field(False, description="是否已废弃")
    min_permission_level: str = Field("user", description="最低执行权限：admin/operator/user")
    enabled_ratio: float | None = Field(None, description="灰度比例 0~1；None 表示不在 SKILL.md 声明")
    rollout_status: str | None = Field(None, description="draft/canary/stable/deprecated；None 表示不覆盖 Catalog")
    min_platform_version: str | None = Field(None, description="最低平台版本要求")

    # I/O 规范
    inputs: list[InputSpec] = Field(default_factory=list, description="输入参数")
    outputs: list[OutputSpec] = Field(default_factory=list, description="输出格式")

    # 核心指令
    instructions: str = Field("", description="核心指令（从 SKILL.md 提取）")

    # 引用
    references: list[Reference] = Field(default_factory=list, description="引用列表")

    # 元数据
    enabled: bool = Field(True, description="是否启用")
    hidden: bool = Field(False, description="是否隐藏（不显示在列表）")
    fallback_to_rag: bool = Field(True, description="失败时是否走 RAG")
    # DSH skill standard: model-invocable unless `disable-model-invocation: true`.
    model_invocable: bool = Field(True, description="问答助手可加载并按用户要求执行（DSH disable-model-invocation 取反）")
    when_to_use: str | None = Field(None, description="DSH whenToUse：何时使用，写入问答的技能目录")
    celery_task: str | None = Field(None, description="关联的 Celery 任务（execution_mode=async 时）")
    execution_mode: Literal["sync", "async"] = Field(
        "async",
        description="执行模式：sync（同步）或 async（Celery 异步，默认）",
    )

    # 路径
    skill_path: str | None = Field(None, description="Skill 目录路径")
    skill_md_path: str | None = Field(None, description="SKILL.md 文件路径")

    def get_llm_description(self) -> str:
        """
        生成供 LLM 使用的描述

        Returns:
            str: 格式化的 Skill 描述
        """
        inputs_str = ""
        if self.inputs:
            inputs_str = "\n输入参数：\n"
            for inp in self.inputs:
                required_mark = "（必填）" if inp.required else "（可选）"
                inputs_str += f"- {inp.name}: {inp.description}{required_mark}\n"

        outputs_str = ""
        if self.outputs:
            outputs_str = "\n输出格式：\n"
            for out in self.outputs:
                outputs_str += f"- {out.name}: {out.description}\n"

        return f"""
【{self.name}】{self.description}
分类：{self.category}
标签：{', '.join(self.tags)}
{inputs_str}{outputs_str}
""".strip()

    def to_dict(self) -> dict[str, Any]:
        """转换为字典"""
        return self.model_dump()


def normalize_markdown_content(content: str) -> str:
    """统一换行符，避免 Windows CRLF 导致 frontmatter 解析失败。"""
    return content.replace("\r\n", "\n").replace("\r", "\n")


def parse_frontmatter(content: str) -> tuple[dict[str, Any], str]:
    """
    解析 YAML frontmatter

    Args:
        content: 文件内容

    Returns:
        Tuple[Dict, str]: (frontmatter 字典, 正文内容)
    """
    text = normalize_markdown_content(content)

    # 跳过 frontmatter 之前的前导空白与 HTML 注释（如 SPDX license 头）
    text = re.sub(r"^\s*(?:<!--.*?-->\s*)*", "", text, flags=re.DOTALL)

    # 标准：首尾 --- 块
    pattern = r"^---\s*\n(.*?)\n---\s*\n(.*)$"
    match = re.match(pattern, text, re.DOTALL)
    if match:
        frontmatter_yaml = match.group(1)
        body = match.group(2)
        try:
            frontmatter = yaml.safe_load(frontmatter_yaml) or {}
        except yaml.YAMLError as e:
            logger.warning("YAML 解析失败: %s", e)
            frontmatter = {}
        return frontmatter, body

    # 宽松：仅开头有 ---，尝试找到第二个 ---
    loose = re.match(r"^---\s*\n(.*?)\n---\s*\n?", text, re.DOTALL)
    if loose:
        yaml_part = loose.group(1)
        body = text[loose.end() :]
        try:
            frontmatter = yaml.safe_load(yaml_part) or {}
        except yaml.YAMLError:
            frontmatter = {}
        return frontmatter, body

    return {}, text


def _dsh_boolean(value: Any) -> bool:
    """DSH frontmatter boolean grammar (true/yes/on/1); absent means false."""
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"true", "yes", "on", "1"} if value is not None else False


def parse_skill_md(file_path: Path, include_instructions: bool = True) -> SkillMetadata:
    """
    解析 SKILL.md 文件

    Args:
        file_path: SKILL.md 文件路径

    Returns:
        SkillMetadata: Skill 元数据对象
    """
    if not file_path.exists():
        raise FileNotFoundError(f"SKILL.md 不存在: {file_path}")

    content = normalize_markdown_content(file_path.read_text(encoding="utf-8"))

    # 解析 frontmatter
    frontmatter, body = parse_frontmatter(content)

    # 构建 SkillMetadata
    skill_dir = file_path.parent

    # 解析 inputs
    inputs = []
    if 'inputs' in frontmatter:
        for inp in frontmatter['inputs']:
            if isinstance(inp, dict):
                inputs.append(InputSpec(**inp))

    # 解析 outputs
    outputs = []
    if 'outputs' in frontmatter:
        for out in frontmatter['outputs']:
            if isinstance(out, dict):
                outputs.append(OutputSpec(**out))

    # 解析 references
    references = []
    if 'references' in frontmatter:
        for ref in frontmatter['references']:
            if isinstance(ref, dict):
                references.append(Reference(**ref))

    # 构建元数据
    metadata = SkillMetadata(
        name=frontmatter.get('name', skill_dir.name),
        version=frontmatter.get('version', '1.0.0'),
        description=frontmatter.get('description', ''),
        category=frontmatter.get('category', 'general'),
        tags=frontmatter.get('tags', []),
        author=frontmatter.get('author'),
        author_email=frontmatter.get('author_email'),
        triggers=frontmatter.get('triggers', []),
        domain=frontmatter.get('domain') or frontmatter.get('category', 'general'),
        celery_queue=frontmatter.get('celery_queue'),
        deprecated=frontmatter.get('deprecated', False),
        min_permission_level=str(frontmatter.get('min_permission_level', 'user')).lower(),
        enabled_ratio=float(frontmatter['enabled_ratio']) if 'enabled_ratio' in frontmatter else None,
        rollout_status=str(frontmatter['rollout_status']).lower() if frontmatter.get('rollout_status') else None,
        min_platform_version=frontmatter.get('min_platform_version'),
        inputs=inputs,
        outputs=outputs,
        instructions=body.strip() if include_instructions else "",
        references=references,
        enabled=frontmatter.get('enabled', True),
        hidden=frontmatter.get('hidden', False),
        fallback_to_rag=frontmatter.get('fallback_to_rag', True),
        model_invocable=not _dsh_boolean(frontmatter.get('disable-model-invocation')),
        when_to_use=frontmatter.get('whenToUse') or None,
        celery_task=frontmatter.get('celery_task'),
        execution_mode=str(frontmatter.get('execution_mode', 'async')).strip().lower(),
        skill_path=str(skill_dir),
        skill_md_path=str(file_path)
    )

    return metadata


def load_all_skill_metadata(skill_dirs: list[str]) -> list[SkillMetadata]:
    """
    从多个目录加载所有 Skill 元数据

    Args:
        skill_dirs: Skill 目录列表

    Returns:
        List[SkillMetadata]: 所有 Skill 的元数据列表
    """
    all_skills = []

    for skill_dir_str in skill_dirs:
        skill_dir = Path(skill_dir_str)

        if not skill_dir.exists():
            print(f"[WARN] Skill 目录不存在: {skill_dir}")
            continue

        # 扫描所有子目录
        for item in skill_dir.iterdir():
            if not item.is_dir():
                continue

            # 跳过隐藏目录和 examples 目录
            if item.name.startswith('.') or item.name == 'examples':
                continue

            skill_md = item / "SKILL.md"
            if skill_md.exists():
                try:
                    metadata = parse_skill_md(skill_md)
                    all_skills.append(metadata)
                    print(f"[OK] 加载 Skill: {metadata.name} v{metadata.version}")
                except Exception as e:
                    print(f"[ERROR] 加载 Skill 失败 {item.name}: {e}")

    return all_skills


# 标准化 SKILL.md 模板 (v2.0 - Phase 2 标准化格式)
# 章节规范:
#   【必填】frontmatter: name, version, description, category, triggers, enabled
#   【必填】# {skill_name} - 标题
#   【必填】## 核心能力 - 描述该 Skill 能做什么
#   【必填】## 工作流程 - 执行步骤
#   【必填】## 输出格式 - 期望的输出结构
#   【必填】## 示例 - 至少一个输入/输出示例
#   【推荐】## 核心原则 - 执行时必须遵守的硬性规则
#   【推荐】## 输入参数说明 - 参数详解
#   【推荐】## 实际执行说明 - Celery 任务映射等
#   【推荐】## 安全规范 - 凭证处理、权限要求
#   【必填】## 注意事项 - 关键注意事项

# category → Catalog 治理默认值（与 celery_routing.DOMAIN_QUEUE_MAP 对齐）
CATEGORY_GOVERNANCE: dict[str, dict[str, str]] = {
    "security": {"domain": "security", "celery_queue": "netops.firewall"},
    "network": {"domain": "network", "celery_queue": "netops.device"},
    "itsm": {"domain": "itsm", "celery_queue": "netops.default"},
    "analysis": {"domain": "general", "celery_queue": "netops.default"},
    "general": {"domain": "general", "celery_queue": "netops.default"},
}


def resolve_category_governance(category: str) -> dict[str, str]:
    """根据 category 推导 domain / celery_queue。"""
    key = str(category or "general").lower()
    return dict(CATEGORY_GOVERNANCE.get(key, CATEGORY_GOVERNANCE["general"]))


SKILL_MD_TEMPLATE = """---
name: {skill_name}
version: {version}
description: {description}
category: {category}
tags: [{tags}]
author: {author}
domain: {domain}
celery_queue: {celery_queue}
min_permission_level: {min_permission_level}
rollout_status: {rollout_status}
enabled_ratio: {enabled_ratio}
min_platform_version: "{min_platform_version}"
triggers:
{triggers_block}
inputs:
{inputs_block}
outputs:
{outputs_block}
references:
{references_block}
enabled: true
fallback_to_rag: true
entry_script: scripts/run.py
entry_output: {entry_output}
# celery_task: execute_skill_name_task  # 可选：指定 Celery 任务（留空则按命名约定自动推导）
---

# {skill_name}

{description}

## 核心原则

执行本 Skill 时必须遵守以下硬性规则：

1. **参数验证**：执行前必须验证所有必填参数，缺失时向用户明确提示
2. **幂等性**：相同输入应产生相同输出，避免副作用
3. **超时控制**：单次执行超过 300 秒视为失败，触发 RAG 兜底
4. **错误处理**：执行失败时必须提供明确的错误原因和建议
5. **不编造数据**：缺失参数或文件不得编造，必须如实告知用户
6. **安全第一**：涉及设备配置变更时，优先 dry-run 或生成可审查脚本

## 核心能力

1. 能力一：描述该 Skill 能做什么
2. 能力二：描述该 Skill 能做什么
3. 能力三：描述该 Skill 能做什么

## 工作流程

1. **参数确认**：验证用户提供的参数是否完整有效
2. **任务执行**：执行核心操作（调用 Celery 任务或 LLM 推理）
3. **结果处理**：收集和处理执行结果
4. **报告输出**：格式化输出结果并反馈用户

## 输入参数说明

{inputs_desc}

## 输出格式

```json
{{
  "success": true,
  "message": "执行结果描述",
  "data": {{}}
}}
```

## 实际执行说明

此 Skill 通过后端 Celery 任务执行实际操作。

执行步骤：
1. 接收用户参数
2. 验证参数有效性
3. 调用对应 Celery 任务提交异步执行
4. 轮询任务状态，等待完成（最长 300 秒）
5. 返回结构化执行结果

## 安全规范

1. **凭证安全**：所有设备凭证通过环境变量或 Secret Manager 获取，不得硬编码
2. **操作审计**：每次执行记录审计日志（Skill 名、用户、时间、参数摘要）
3. **权限控制**：根据用户权限级别判断是否允许执行
4. **敏感信息过滤**：日志和返回结果中过滤密码、Token 等敏感字段

## 示例

**输入**："示例用户请求"

**执行**：
1. 提取参数
2. 调用后端任务执行
3. 返回结果

## 注意事项

- 注意事项一：执行前请确认操作范围和影响
- 注意事项二：变更类操作建议先在测试环境验证
- 注意事项三：超时或失败时自动触发 RAG 知识库兜底
"""


def create_skill_md(
    skill_name: str,
    description: str,
    category: str = "general",
    tags: list[str] = None,
    author: str = "NetOps Team",
    triggers: list[str] = None,
    version: str = "1.0.0",
    inputs: list[dict] = None,
    outputs: list[dict] = None,
    references: list[dict] = None,
    *,
    domain: str | None = None,
    celery_queue: str | None = None,
    min_permission_level: str = "user",
    rollout_status: str = "draft",
    enabled_ratio: int = 0,
    min_platform_version: str = "1.0.0",
    entry_output: str = "none",
) -> str:
    """
    创建标准化的 SKILL.md 文件内容 (v2.0)

    Args:
        skill_name: Skill 名称
        description: 描述
        category: 分类
        tags: 标签列表
        author: 作者
        triggers: 触发关键词
        version: 版本号
        inputs: 输入参数列表 [{"name": "x", "type": "string", "required": false, "description": "..."}]
        outputs: 输出格式列表 [{"name": "x", "type": "text", "description": "..."}]
        references: 引用列表 [{"type": "rag|file", "source": "...", "path": "...", "description": "..."}]

    Returns:
        str: 标准化 SKILL.md 文件内容
    """
    import yaml

    if tags is None:
        tags = [skill_name]
    if triggers is None:
        triggers = [f"执行{skill_name}", f"使用{skill_name}"]
    if inputs is None:
        inputs = [{"name": "param1", "type": "string", "required": True, "description": "参数1描述"}]
    if outputs is None:
        outputs = [{"name": "result", "type": "text", "description": "输出结果描述"}]
    if references is None:
        references = []

    gov = resolve_category_governance(category)
    domain = domain or gov["domain"]
    celery_queue = celery_queue or gov["celery_queue"]

    triggers_block = "\n".join([f'  - "{t}"' for t in triggers])

    inputs_block = yaml.dump(inputs, allow_unicode=True, default_flow_style=False, indent=2)
    outputs_block = yaml.dump(outputs, allow_unicode=True, default_flow_style=False, indent=2)
    references_block = yaml.dump(references, allow_unicode=True, default_flow_style=False, indent=2)

    inputs_block = "\n".join("  " + line for line in inputs_block.split("\n")) if inputs_block.strip() else "  []"
    outputs_block = "\n".join("  " + line for line in outputs_block.split("\n")) if outputs_block.strip() else "  []"
    references_block = "\n".join("  " + line for line in references_block.split("\n")) if references_block.strip() else "  []"

    inputs_desc = "\n".join([
        f"- **{inp.get('name', '?')}** ({inp.get('type', 'string')}, {'必填' if inp.get('required') else '可选'}): {inp.get('description', '')}"
        for inp in inputs
    ])

    return SKILL_MD_TEMPLATE.format(
        skill_name=skill_name,
        version=version,
        description=description,
        category=category,
        tags=", ".join(tags),
        author=author,
        domain=domain,
        celery_queue=celery_queue,
        min_permission_level=min_permission_level,
        rollout_status=rollout_status,
        enabled_ratio=enabled_ratio,
        min_platform_version=min_platform_version,
        entry_output=entry_output,
        triggers_block=triggers_block,
        inputs_block=inputs_block,
        outputs_block=outputs_block,
        references_block=references_block,
        inputs_desc=inputs_desc,
    )
