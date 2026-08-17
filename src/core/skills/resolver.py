# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""Skill 入口脚本解析（基于 SKILL.md，平台不含业务逻辑）。"""

from __future__ import annotations

import logging
import re
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[3]
SKILLS_ROOT = PROJECT_ROOT / "src" / "skills"

# 标准入口候选（按优先级）
_ENTRY_CANDIDATES = (
    "scripts/run.py",
    "scripts/generate_change_ticket.py",
    "scripts/generate_config.py",
    "scripts/itsm_change_ticket_excel.py",
    "scripts/itsm_callback.py",
    "scripts/firewall-policy.py",
)


def get_skill_dir(skill_name: str) -> Path:
    return SKILLS_ROOT / skill_name


def _load_frontmatter(skill_name: str) -> dict:
    skill_md = get_skill_dir(skill_name) / "SKILL.md"
    if not skill_md.is_file():
        return {}
    text = skill_md.read_text(encoding="utf-8").replace("\r\n", "\n")
    # 跳过 frontmatter 之前的前导空白与 HTML 注释（如 SPDX license 头）
    text = re.sub(r"^\s*(?:<!--.*?-->\s*)*", "", text, flags=re.DOTALL)
    if not text.startswith("---"):
        return {}
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}
    try:
        return yaml.safe_load(parts[1]) or {}
    except Exception as exc:
        logger.warning("解析 SKILL.md 失败 %s: %s", skill_name, exc)
        return {}


def resolve_entry_script(skill_name: str) -> Path | None:
    skill_dir = get_skill_dir(skill_name)
    if not skill_dir.is_dir():
        return None

    meta = _load_frontmatter(skill_name)
    entry = meta.get("entry_script")
    if entry:
        path = skill_dir / str(entry)
        if path.is_file():
            return path

    for ref in meta.get("references") or []:
        if isinstance(ref, dict) and ref.get("type") == "file" and ref.get("path"):
            path = skill_dir / str(ref["path"])
            if path.is_file() and "scripts" in path.parts:
                return path

    for rel in _ENTRY_CANDIDATES:
        path = skill_dir / rel
        if path.is_file():
            return path

    return None


def get_entry_output_mode(skill_name: str) -> str:
    """file | dir | none"""
    meta = _load_frontmatter(skill_name)
    mode = str(meta.get("entry_output") or "").lower()
    if mode in ("file", "dir", "none"):
        return mode
    script = resolve_entry_script(skill_name)
    if not script:
        return "none"
    name = script.name.lower()
    if "callback" in name:
        return "none"
    if "firewall" in name or name == "run.py":
        return "dir"
    return "file"


def get_skill_version(skill_name: str) -> str:
    return str(_load_frontmatter(skill_name).get("version") or "0.0.0")


def get_skill_execution_timeout_seconds(
    skill_name: str,
    *,
    default: int = 600,
) -> int:
    """Return a bounded wall-clock timeout declared by the Skill."""

    fallback = max(1, int(default))
    raw = _load_frontmatter(skill_name).get("execution_timeout_seconds")
    if raw is None:
        return fallback
    try:
        timeout = int(raw)
    except (TypeError, ValueError):
        logger.warning("Invalid Skill execution timeout %s: %r", skill_name, raw)
        return fallback
    if not 1 <= timeout <= 7200:
        logger.warning("Out-of-range Skill execution timeout %s: %s", skill_name, timeout)
        return fallback
    return timeout


def get_skill_cwd(skill_name: str) -> Path:
    script = resolve_entry_script(skill_name)
    if script:
        return script.parent
    return get_skill_dir(skill_name) / "scripts"
