#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""
Skill 验证工具 - 检查 SKILL.md 格式标准化和完整性

功能:
  1. Frontmatter 必填字段检查
  2. 必填章节完整性检查
  3. Token 长度估算（800-1500 目标范围告警）
  4. 格式问题检查（空白行、YAML 语法等）
  5. 参数定义一致性检查

用法:
    # 验证单个 Skill
    python scripts/validate_skill.py src/skills/device-backup/SKILL.md

    # 验证所有 Skill
    python scripts/validate_skill.py --all

    # 严格模式（警告也视为失败）
    python scripts/validate_skill.py --all --strict

    # JSON 输出（用于 CI/CD）
    python scripts/validate_skill.py --all --json
"""

import sys
import re
import json
import argparse
from pathlib import Path

if sys.platform == "win32":
    try:
        import io
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    except (ValueError, AttributeError):
        pass

BASE_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BASE_DIR))

from src.skill_system.metadata import parse_frontmatter

REQUIRED_FRONTMATTER_FIELDS = [
    "name", "version", "description", "category", "triggers",
]

RECOMMENDED_FRONTMATTER_FIELDS = [
    "tags", "author", "inputs", "outputs", "enabled", "fallback_to_rag",
]

REQUIRED_BODY_SECTIONS = [
    "#",                    # 标题（H1）
    "## 核心能力",           # 核心能力
    "## 工作流程",           # 工作流程
    "## 输出格式",           # 输出格式
    "## 示例",               # 示例
    "## 注意事项",           # 注意事项
]

RECOMMENDED_BODY_SECTIONS = [
    "## 核心原则",           # 核心原则
    "## 输入参数说明",       # 参数说明
    "## 安全规范",           # 安全规范
    "## 实际执行说明",       # 执行说明
]

TARGET_TOKEN_MIN = 500
TARGET_TOKEN_MAX = 2000
WARN_TOKEN_MAX = 2500


class ValidationResult:
    def __init__(self, file_path: str):
        self.file_path = file_path
        self.errors = []
        self.warnings = []
        self.info = []

    @property
    def valid(self):
        return len(self.errors) == 0

    @property
    def warning_count(self):
        return len(self.warnings)

    def error(self, msg: str):
        self.errors.append(msg)

    def warn(self, msg: str):
        self.warnings.append(msg)

    def info_msg(self, msg: str):
        self.info.append(msg)


def estimate_tokens(text: str) -> int:
    """估算文本的 token 数量（中英文混合约 1.5-2 char/token）"""
    chinese_chars = len(re.findall(r'[\u4e00-\u9fff]', text))
    other_chars = len(text) - chinese_chars
    return int(chinese_chars / 1.5 + other_chars / 3.5)


def validate_frontmatter(content: str, result: ValidationResult):
    """验证 frontmatter 部分"""
    frontmatter, body = parse_frontmatter(content)

    if not frontmatter and not content.strip().startswith("---"):
        result.error("缺少 YAML frontmatter（必须以 --- 开头）")
        return

    for field in REQUIRED_FRONTMATTER_FIELDS:
        if field not in frontmatter or (field != "triggers" and not frontmatter[field]):
            result.error(f"frontmatter 缺少必填字段: {field}")

    for field in RECOMMENDED_FRONTMATTER_FIELDS:
        if field not in frontmatter or frontmatter[field] is None:
            result.warn(f"frontmatter 缺少推荐字段: {field}")

    if "name" in frontmatter:
        name = frontmatter["name"]
        if not re.match(r'^[a-zA-Z0-9\-_]+$', name):
            result.error(f"Skill 名称格式无效: {name}（只允许字母、数字、连字符、下划线）")

    if "version" in frontmatter:
        version = frontmatter["version"]
        if not re.match(r'^\d+\.\d+\.\d+$', str(version)):
            result.warn(f"版本号格式不规范: {version}（建议使用 semver，如 1.0.0）")

    if "triggers" in frontmatter:
        triggers = frontmatter["triggers"]
        if not isinstance(triggers, list) or any(
            not isinstance(item, str) or not item.strip() for item in triggers
        ):
            result.error("triggers 必须是非空字符串组成的列表；内部 Skill 可显式使用 []")
        elif len(triggers) == 1:
            result.warn("聊天 Skill 推荐至少 2 个触发词")

    if "category" in frontmatter:
        valid_categories = ["network", "security", "compute", "storage", "monitoring", "general", "test"]
        if frontmatter["category"] not in valid_categories:
            result.warn(f"category 值非标准: {frontmatter['category']}（推荐: {', '.join(valid_categories)}）")

    if "enabled" in frontmatter:
        if not isinstance(frontmatter["enabled"], bool):
            result.warn("enabled 应为布尔值 (true/false)")

    if "inputs" in frontmatter and isinstance(frontmatter["inputs"], list):
        for i, inp in enumerate(frontmatter["inputs"]):
            if not isinstance(inp, dict):
                result.error(f"inputs[{i}] 格式错误，应为字典")
                continue
            for key in ["name", "type"]:
                if key not in inp:
                    result.error(f"inputs[{i}] 缺少 {key} 字段")

    if "outputs" in frontmatter and isinstance(frontmatter["outputs"], list):
        for i, out in enumerate(frontmatter["outputs"]):
            if not isinstance(out, dict):
                result.error(f"outputs[{i}] 格式错误，应为字典")
                continue
            for key in ["name", "type"]:
                if key not in out:
                    result.error(f"outputs[{i}] 缺少 {key} 字段")


def validate_body(content: str, result: ValidationResult):
    """验证正文部分"""
    frontmatter, body = parse_frontmatter(content)

    if not body.strip():
        result.error("正文内容为空")
        return

    for section in REQUIRED_BODY_SECTIONS:
        if section not in body:
            result.error(f"缺少必填章节: {section}")
        else:
            section_index = body.index(section)
            section_end = len(body)
            for other_section in REQUIRED_BODY_SECTIONS + RECOMMENDED_BODY_SECTIONS:
                other_index = body.find(other_section, section_index + len(section))
                if other_index != -1 and other_index < section_end:
                    section_end = other_index
            section_content = body[section_index:section_end].strip()
            actual = len(section_content) - len(section)
            if actual < 10 and section not in ["#", "## 输出格式", "## 示例"]:
                result.warn(f"章节内容过少: {section}（仅 {actual} 字符）")

    for section in RECOMMENDED_BODY_SECTIONS:
        if section not in body:
            result.warn(f"缺少推荐章节: {section}")

    token_count = estimate_tokens(body)
    result.info_msg(f"估算 token 数: {token_count}")

    if token_count < TARGET_TOKEN_MIN:
        result.warn(f"Token 数过低 ({token_count})，建议 500-2000，可能指令不够详细")
    elif token_count > WARN_TOKEN_MAX:
        result.warn(f"Token 数过高 ({token_count})，建议控制在 {TARGET_TOKEN_MAX} 以内")
    elif token_count > TARGET_TOKEN_MAX:
        result.info_msg(f"Token 数偏高 ({token_count})，建议精简至 {TARGET_TOKEN_MAX} 以内")


def validate_format(content: str, result: ValidationResult):
    """验证文件格式"""
    normalized = re.sub(r"^\s*(?:<!--.*?-->\s*)*", "", content, flags=re.DOTALL)
    if not normalized.startswith("---\n") and not normalized.startswith("---\r\n"):
        result.error("文件必须以 --- 开头（frontmatter 标记）")

    if not content.endswith("\n"):
        result.warn("文件末尾建议添加空行")

    if "\r\n" in content:
        result.info_msg("检测到 Windows 换行符 (CRLF)")



def validate_skill_file(file_path: str, strict: bool = False) -> ValidationResult:
    """验证单个 SKILL.md 文件"""
    path = Path(file_path)
    result = ValidationResult(str(path))

    if not path.exists():
        result.error(f"文件不存在: {path}")
        return result

    if path.name != "SKILL.md":
        result.warn(f"文件名应为 SKILL.md: {path.name}")

    try:
        content = path.read_text(encoding="utf-8")
    except Exception as e:
        result.error(f"读取文件失败: {e}")
        return result

    validate_format(content, result)
    validate_frontmatter(content, result)
    validate_body(content, result)

    if strict:
        for w in result.warnings:
            result.errors.append(f"[strict] {w}")
        result.warnings.clear()

    return result


def discover_skills(skill_dirs: list = None) -> list:
    """发现所有 SKILL.md 文件"""
    if skill_dirs is None:
        skill_dirs = [str(BASE_DIR / "src" / "skills")]

    skill_files = []
    for skill_dir_str in skill_dirs:
        skill_dir = Path(skill_dir_str)
        if not skill_dir.exists():
            continue

        for item in skill_dir.iterdir():
            if not item.is_dir():
                continue
            if item.name.startswith(".") or item.name == "examples":
                continue

            skill_md = item / "SKILL.md"
            if skill_md.exists():
                skill_files.append(str(skill_md))

    return skill_files


def print_result(result: ValidationResult):
    """打印单个验证结果"""
    status = "PASS" if result.valid else "FAIL"
    status_mark = "[OK]" if result.valid else "[FAIL]"

    print(f"\n{status_mark} {status} | {result.file_path}")

    if result.info:
        for msg in result.info:
            print(f"   INFO  {msg}")

    if result.warnings:
        for msg in result.warnings:
            print(f"   WARN  {msg}")

    if result.errors:
        for msg in result.errors:
            print(f"   ERROR {msg}")


def main():
    parser = argparse.ArgumentParser(
        description="Skill 验证工具 - 检查 SKILL.md 格式标准化和完整性",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 验证单个 Skill
  python scripts/validate_skill.py src/skills/device-backup/SKILL.md

  # 验证所有 Skill
  python scripts/validate_skill.py --all

  # 严格模式
  python scripts/validate_skill.py --all --strict

  # JSON 输出（用于 CI/CD）
  python scripts/validate_skill.py --all --json
        """,
    )

    parser.add_argument(
        "files",
        nargs="*",
        help="要验证的 SKILL.md 文件路径",
    )
    parser.add_argument(
        "--all", "-a",
        action="store_true",
        help="验证 src/skills/ 下所有 Skill",
    )
    parser.add_argument(
        "--strict", "-s",
        action="store_true",
        help="严格模式，警告也视为错误",
    )
    parser.add_argument(
        "--json", "-j",
        action="store_true",
        help="以 JSON 格式输出（用于 CI/CD）",
    )
    parser.add_argument(
        "--dir", "-d",
        nargs="+",
        help="指定 Skill 目录",
    )

    args = parser.parse_args()

    if not args.files and not args.all:
        parser.print_help()
        sys.exit(0)

    files_to_check = list(args.files)

    if args.all:
        skill_dirs = args.dir if args.dir else None
        files_to_check.extend(discover_skills(skill_dirs))

    if not files_to_check:
        print("未找到任何 SKILL.md 文件")
        sys.exit(1)

    results = []
    for f in files_to_check:
        result = validate_skill_file(f, strict=args.strict)
        results.append(result)

    if args.json:
        output = {
            "total": len(results),
            "passed": sum(1 for r in results if r.valid),
            "failed": sum(1 for r in results if not r.valid),
            "warnings_total": sum(1 for r in results if r.warning_count > 0),
            "skills": [
                {
                    "file": r.file_path,
                    "valid": r.valid,
                    "errors": r.errors,
                    "warnings": r.warnings,
                    "info": r.info,
                }
                for r in results
            ],
        }
        print(json.dumps(output, ensure_ascii=False, indent=2))
        if output["failed"]:
            raise SystemExit(1)
    else:
        print("=" * 60)
        print("  Skill 格式验证 v2.0")
        print("=" * 60)

        for r in results:
            print_result(r)

        print("\n" + "=" * 60)
        total = len(results)
        passed = sum(1 for r in results if r.valid)
        failed = total - passed
        warnings = sum(1 for r in results if r.warning_count > 0)

        print(f"  总计: {total} | 通过: {passed} | 失败: {failed} | 警告: {warnings}")

        if failed > 0:
            print(f"\n  [{failed} 个 Skill 验证失败，请修复后重试]")
            print("=" * 60)
            sys.exit(1)
        else:
            print("  [所有 Skill 验证通过!]")
            print("=" * 60)


if __name__ == "__main__":
    main()
