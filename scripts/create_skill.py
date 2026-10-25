#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""
Skill Creator CLI - 快速创建新 Skill

对标 Grok skill-creator，提供命令行一键创建 Skill 的功能。

用法:
    # 基本创建
    python scripts/create_skill.py --name my-skill --description "我的新技能"

    # 完整参数
    python scripts/create_skill.py --name my-skill --description "巡检新功能" \
        --category network --triggers "执行巡检" "设备巡检" \
        --tags "inspection" "device" --version 1.0.0

    # 交互模式（无参数时进入引导流程）
    python scripts/create_skill.py --interactive

设计原则:
    1. 生成标准化 SKILL.md（符合 metadata.py 的解析规范）
    2. 自动创建目录结构（scripts/ references/ assets/）
    3. 支持 frontmatter 所有字段
    4. 支持同步注册到 SkillSystem（热加载）
"""

import argparse
import sys
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BASE_DIR))


def create_skill(
    name: str,
    description: str,
    category: str = "general",
    tags: list = None,
    triggers: list = None,
    version: str = "1.0.0",
    author: str = "NetOps Team",
    inputs: list = None,
    outputs: list = None,
    enabled: bool = True,
    fallback_to_rag: bool = True,
    skill_dir_name: str = None,
    instructions: str = None,
    create_subdirs: bool = True,
):
    """
    创建一个新 Skill

    Args:
        name: Skill 名称（唯一标识）
        description: Skill 描述
        category: 分类
        tags: 标签列表
        triggers: 触发关键词列表
        version: 版本号
        author: 作者
        inputs: 输入参数列表 [{"name": "...", "type": "string", "required": false, "description": "..."}]
        outputs: 输出格式列表 [{"name": "...", "type": "text", "description": "..."}]
        enabled: 是否启用
        fallback_to_rag: 失败是否走 RAG
        skill_dir_name: 目录名（默认与 name 相同）
        instructions: 自定义指令内容
        create_subdirs: 是否创建 scripts/ references/ assets/ 子目录

    Returns:
        dict: {"success": bool, "message": str, "skill_dir": str}
    """
    if tags is None:
        tags = [name]
    if triggers is None:
        triggers = [description[:20]]
    if inputs is None:
        inputs = []
    if outputs is None:
        outputs = [{"name": "result", "type": "text", "description": "执行结果"}]

    dir_name = skill_dir_name or name

    src_skills_dir = BASE_DIR / "src" / "skills"
    skill_dir = src_skills_dir / dir_name

    if skill_dir.exists():
        return {
            "success": False,
            "message": f"Skill 目录已存在: {skill_dir}",
            "skill_dir": str(skill_dir)
        }

    # 创建目录结构
    skill_dir.mkdir(parents=True, exist_ok=True)

    if create_subdirs:
        (skill_dir / "scripts").mkdir(exist_ok=True)
        (skill_dir / "references").mkdir(exist_ok=True)
        (skill_dir / "assets").mkdir(exist_ok=True)

    # 生成 SKILL.md
    skill_md_content = _generate_skill_md(
        name=name,
        description=description,
        category=category,
        tags=tags,
        triggers=triggers,
        version=version,
        author=author,
        inputs=inputs,
        outputs=outputs,
        enabled=enabled,
        fallback_to_rag=fallback_to_rag,
        instructions=instructions,
    )

    skill_md_path = skill_dir / "SKILL.md"
    skill_md_path.write_text(skill_md_content, encoding="utf-8")

    return {
        "success": True,
        "message": f"Skill '{name}' 创建成功",
        "skill_dir": str(skill_dir),
        "skill_md": str(skill_md_path),
    }


def _generate_skill_md(
    name: str,
    description: str,
    category: str,
    tags: list,
    triggers: list,
    version: str,
    author: str,
    inputs: list,
    outputs: list,
    enabled: bool,
    fallback_to_rag: bool,
    instructions: str = None,
) -> str:
    """生成 SKILL.md 内容"""
    import yaml

    tags_str = ", ".join(tags)

    inputs_yaml = yaml.dump(inputs, allow_unicode=True, default_flow_style=False, indent=2)

    outputs_yaml = yaml.dump(outputs, allow_unicode=True, default_flow_style=False, indent=2)

    triggers_formatted = "\n".join([f'  - "{t}"' for t in triggers])

    if instructions is None:
        body = _generate_default_instructions(name, description, triggers)
    else:
        body = instructions

    content = f"""---
name: {name}
version: {version}
description: {description}
category: {category}
tags: [{tags_str}]
author: {author}
triggers:
{triggers_formatted}
inputs:
{inputs_yaml}outputs:
{outputs_yaml}enabled: {str(enabled).lower()}
fallback_to_rag: {str(fallback_to_rag).lower()}
---

{body}
"""
    return content


def _generate_default_instructions(name: str, description: str, triggers: list) -> str:
    """生成符合标准化格式的默认指令正文 (v2.0)"""
    triggers_list = "\n".join(["- " + t for t in triggers]) if triggers else "- （未定义）"

    return f"""# {name}

{description}

## 核心原则

执行本 Skill 时必须遵守以下硬性规则：

1. **参数验证**：执行前必须验证所有必填参数，缺失时向用户明确提示
2. **幂等性**：相同输入应产生相同输出，避免副作用
3. **超时控制**：单次执行超过 300 秒视为失败，触发 RAG 兜底
4. **错误处理**：执行失败时必须提供明确的错误原因和建议
5. **不编造数据**：缺失参数或文件不得编造，必须如实告知用户
6. **安全第一**：涉及设备配置变更时，优先 dry-run 或生成可审查脚本

## 触发词

{triggers_list}

## 核心能力

1. 能力一：描述该 Skill 能做什么
2. 能力二：描述该 Skill 能做什么
3. 能力三：描述该 Skill 能做什么

## 工作流程

1. **参数确认**：验证用户提供的参数是否完整有效
2. **任务执行**：执行核心操作
3. **结果处理**：收集和处理执行结果
4. **报告输出**：格式化输出结果并反馈用户

## 输入参数说明

（请在此处描述各输入参数的含义和使用方式）

## 输出格式

```json
{{
  "success": true,
  "message": "执行结果描述",
  "data": {{}}
}}
```

## 安全规范

1. **凭证安全**：所有设备凭证通过环境变量或 Secret Manager 获取，不得硬编码
2. **操作审计**：每次执行记录审计日志（Skill 名、用户、时间、参数摘要）
3. **权限控制**：根据用户权限级别判断是否允许执行
4. **敏感信息过滤**：日志和返回结果中过滤密码、Token 等敏感字段

## 示例

**输入**："示例用户请求"

**执行**：
1. 提取参数
2. 执行核心逻辑
3. 返回结果

## 注意事项

- 注意事项一：执行前请确认操作范围和影响
- 注意事项二：变更类操作建议先在测试环境验证
- 注意事项三：超时或失败时返回明确失败状态，不自动重复执行
"""


def interactive_create():
    """交互式创建 Skill（引导流程）"""
    print("=" * 60)
    print("  Skill Creator - 交互式创建新 Skill")
    print("=" * 60)
    print()

    # Step 1: 基本信息
    print("📝 Step 1/5: 基本信息")
    print("-" * 40)

    while True:
        name = input("  Skill 名称 (如 device-patrol): ").strip()
        if not name:
            print("  ❌ 名称不能为空")
            continue
        if not name.replace("-", "").replace("_", "").isalnum():
            print("  ❌ 名称只能包含字母、数字、连字符和下划线")
            continue
        src_skills_dir = BASE_DIR / "src" / "skills"
        if (src_skills_dir / name).exists():
            print(f"  ❌ Skill '{name}' 已存在")
            continue
        break

    description = input("  Skill 描述 (一句话说明功能): ").strip()
    if not description:
        description = f"{name} 技能"

    category = input("  分类 [general]: ").strip() or "general"

    version = input("  版本 [1.0.0]: ").strip() or "1.0.0"

    author = input("  作者 [NetOps Team]: ").strip() or "NetOps Team"

    # Step 2: 触发词
    print()
    print("🎯 Step 2/5: 触发词（用户说什么会触发此 Skill）")
    print("-" * 40)
    print("  输入触发词，一行一个，空行结束:")

    triggers = []
    i = 1
    while True:
        trigger = input(f"  触发词 {i}: ").strip()
        if not trigger:
            break
        triggers.append(trigger)
        i += 1

    if not triggers:
        triggers = [description[:30]]

    # Step 3: 标签
    print()
    print("🏷️  Step 3/5: 标签")
    print("-" * 40)

    tags_input = input("  标签（逗号分隔，如 inspection, device）: ").strip()
    if tags_input:
        tags = [t.strip() for t in tags_input.split(",") if t.strip()]
    else:
        tags = [name]

    # Step 4: 输入参数
    print()
    print("📥 Step 4/5: 输入参数")
    print("-" * 40)
    print("  定义 Skill 的输入参数，一行一个，格式: name:type:required:description")
    print("  例如: device_name:string:false:设备名称")
    print("  支持类型: string, int, float, bool, file")
    print("  空行结束:")

    inputs = []
    i = 1
    while True:
        param_input = input(f"  参数 {i}: ").strip()
        if not param_input:
            break

        parts = param_input.split(":", 3)
        if len(parts) < 2:
            print("    格式错误，请使用 name:type:required:description")
            continue

        param_name = parts[0].strip()
        param_type = parts[1].strip() if len(parts) > 1 else "string"
        param_required = parts[2].strip().lower() == "true" if len(parts) > 2 else False
        param_desc = parts[3].strip() if len(parts) > 3 else ""

        inputs.append({
            "name": param_name,
            "type": param_type,
            "required": param_required,
            "description": param_desc,
        })
        i += 1

    # Step 5: 指令内容
    print()
    print("📄 Step 5/5: 指令内容")
    print("-" * 40)
    print("  是否自定义指令内容？按 Enter 使用默认模板，输入 'yes' 自定义:")
    custom = input("  > ").strip().lower()

    instructions = None
    if custom == "yes":
        print("  请输入指令内容（输入 'END' 结束）:")
        lines = []
        while True:
            line = input()
            if line.strip() == "END":
                break
            lines.append(line)
        instructions = "\n".join(lines)

    # 确认并创建
    print()
    print("=" * 60)
    print("📋 确认信息:")
    print(f"  名称: {name}")
    print(f"  描述: {description}")
    print(f"  分类: {category}")
    print(f"  版本: {version}")
    print(f"  触发词: {', '.join(triggers)}")
    print(f"  标签: {', '.join(tags)}")
    print(f"  输入参数: {len(inputs)} 个")
    print("=" * 60)

    confirm = input("\n  确认创建? (y/n): ").strip().lower()
    if confirm != "y":
        print("  ❌ 已取消")
        return {"success": False, "message": "用户取消"}

    result = create_skill(
        name=name,
        description=description,
        category=category,
        tags=tags,
        triggers=triggers,
        version=version,
        author=author,
        inputs=inputs,
    )

    if result["success"]:
        print(f"\n  ✅ {result['message']}")
        print(f"  📁 目录: {result['skill_dir']}")
        print(f"  📄 SKILL.md: {result['skill_md']}")
    else:
        print(f"\n  ❌ {result['message']}")

    return result


def main():
    parser = argparse.ArgumentParser(
        description="Skill Creator - 快速创建新 Skill",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 基本创建
  python scripts/create_skill.py --name my-skill --description "我的新技能"

  # 完整参数
  python scripts/create_skill.py --name my-skill --description "巡检新功能" \\
      --category network --triggers "执行巡检" "设备巡检" \\
      --tags inspection device --version 1.0.0

  # 交互模式
  python scripts/create_skill.py --interactive
        """,
    )

    parser.add_argument(
        "--name", "-n",
        type=str,
        help="Skill 名称（唯一标识，如 device-patrol）",
    )
    parser.add_argument(
        "--description", "-d",
        type=str,
        help="Skill 简短描述",
    )
    parser.add_argument(
        "--interactive", "-i",
        action="store_true",
        help="交互式创建模式",
    )
    parser.add_argument(
        "--category", "-c",
        type=str,
        default="general",
        help="分类 (默认: general)",
    )
    parser.add_argument(
        "--triggers", "-t",
        type=str,
        nargs="+",
        help="触发关键词列表",
    )
    parser.add_argument(
        "--tags",
        type=str,
        nargs="+",
        help="标签列表",
    )
    parser.add_argument(
        "--version", "-v",
        type=str,
        default="1.0.0",
        help="版本号 (默认: 1.0.0)",
    )
    parser.add_argument(
        "--author", "-a",
        type=str,
        default="NetOps Team",
        help="作者 (默认: NetOps Team)",
    )
    parser.add_argument(
        "--dir-name",
        type=str,
        help="目录名（默认与 name 相同）",
    )
    parser.add_argument(
        "--no-subdirs",
        action="store_true",
        help="不创建 scripts/references/assets 子目录",
    )
    parser.add_argument(
        "--disabled",
        action="store_true",
        help="创建后默认禁用",
    )
    parser.add_argument(
        "--no-fallback",
        action="store_true",
        help="禁用 RAG fallback",
    )

    args = parser.parse_args()

    if args.interactive:
        result = interactive_create()
        sys.exit(0 if result["success"] else 1)

    if not args.name:
        parser.error("必须提供 --name 参数，或使用 --interactive 进入交互模式")

    if not args.description:
        args.description = f"{args.name} 技能"

    result = create_skill(
        name=args.name,
        description=args.description,
        category=args.category,
        tags=args.tags,
        triggers=args.triggers,
        version=args.version,
        author=args.author,
        skill_dir_name=args.dir_name,
        create_subdirs=not args.no_subdirs,
        enabled=not args.disabled,
        fallback_to_rag=not args.no_fallback,
    )

    if result["success"]:
        print(f"✅ {result['message']}")
        print(f"📁 目录: {result['skill_dir']}")
        print(f"📄 SKILL.md: {result['skill_md']}")
    else:
        print(f"❌ {result['message']}")
        sys.exit(1)


if __name__ == "__main__":
    main()
