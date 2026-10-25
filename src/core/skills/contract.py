# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""SkillExecutionResult 契约校验（CI / 契约测试）。"""

from __future__ import annotations

from typing import Any

from src.core.skills.result import SkillExecutionResult, SkillStatus
from src.core.skills.resolver import get_skill_dir

# SKILL.md outputs.name → artifacts 标准 key
OUTPUT_ARTIFACT_ALIASES: dict[str, str] = {
    "config_files": "config_zip",
    "change_excel": "change_excel",
}


def load_skill_output_specs(skill_name: str) -> list[dict[str, Any]]:
    skill_md = get_skill_dir(skill_name) / "SKILL.md"
    if not skill_md.is_file():
        return []
    from src.skill_system.metadata import parse_skill_md

    meta = parse_skill_md(skill_md, include_instructions=False)
    return [o.model_dump() for o in meta.outputs]


def validate_execution_result(
    result: SkillExecutionResult | dict[str, Any],
    *,
    skill_name: str | None = None,
    require_success: bool = False,
    check_outputs: bool = True,
) -> list[str]:
    """返回违规列表；空列表表示通过。"""
    errors: list[str] = []
    if isinstance(result, dict):
        ser = SkillExecutionResult.from_legacy_dict(
            result,
            skill_name=skill_name or str(result.get("skill_name") or "unknown"),
        )
    else:
        ser = result

    if ser.schema_version != "1":
        errors.append(f"schema_version 必须为 1，实际 {ser.schema_version}")
    if not ser.execution_id:
        errors.append("execution_id 不能为空")
    if not ser.skill_name:
        errors.append("skill_name 不能为空")
    if require_success and ser.status != SkillStatus.SUCCESS:
        errors.append(f"期望 success，实际 {ser.status.value}")

    if check_outputs and skill_name:
        specs = load_skill_output_specs(skill_name)
        for spec in specs:
            out_name = str(spec.get("name") or "")
            out_type = str(spec.get("type") or "").lower()
            if out_type == "download":
                art_key = OUTPUT_ARTIFACT_ALIASES.get(out_name, out_name)
                if art_key not in ser.artifacts:
                    legacy = ser.to_legacy_dict()
                    if not legacy.get("download_url") and not legacy.get(f"{out_name}_url"):
                        errors.append(f"缺少产物 artifacts[{art_key}]（outputs 声明 {out_name}）")

    return errors


def assert_valid_execution_result(*args, **kwargs) -> SkillExecutionResult:
    errors = validate_execution_result(*args, **kwargs)
    if errors:
        raise AssertionError("Skill 契约校验失败:\n- " + "\n- ".join(errors))
    result = args[0] if args else kwargs.get("result")
    if isinstance(result, SkillExecutionResult):
        return result
    return SkillExecutionResult.from_legacy_dict(
        result,
        skill_name=kwargs.get("skill_name") or str(result.get("skill_name")),
    )
