# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""
Skill 管理器

提供 Skill 的后端管理功能：创建、编辑、删除、启用/禁用、热加载等。
"""

import logging
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)


class SkillManager:
    """
    Skill 后端管理器

    提供 Skill 的 CRUD 操作和热加载功能。
    """

    def __init__(self):
        """初始化管理器"""
        self._skills_dir = Path(__file__).parent.parent.parent / "src" / "skills"

    def _catalog_map(self) -> dict[str, dict[str, Any]]:
        try:
            from src.skill_system.catalog.repository import list_catalog_entries

            return {
                entry["skill_name"]: entry
                for entry in list_catalog_entries(enabled_only=False)
            }
        except Exception as exc:
            logger.debug("加载 Catalog 失败，列表将不含灰度字段: %s", exc)
            return {}

    def list_all_skills(self) -> list[dict[str, Any]]:
        """
        获取所有 Skill 列表（仅 SKILL.md 文件型 Skill，与 SkillSystem 扫描范围一致）

        Returns:
            List[Dict]: Skill 信息列表（含 Catalog 灰度治理字段）
        """
        try:
            from src.skills.registry import skill_registry

            catalog = self._catalog_map()
            skills = []
            for item in self.list_file_skills():
                name = item["name"]
                reg_skill = skill_registry.get_skill(name)
                cat = catalog.get(name, {})
                enabled = reg_skill.enabled if reg_skill else item.get("enabled", True)
                if cat and "enabled" in cat:
                    enabled = bool(cat["enabled"])
                skills.append({
                    "name": name,
                    "description": item.get("description", ""),
                    "category": item.get("category", "general"),
                    "tags": item.get("tags", []),
                    "enabled": enabled,
                    "version": item.get("version", "1.0.0"),
                    "fallback_to_rag": (
                        reg_skill.fallback_to_rag_if_fail
                        if reg_skill
                        else item.get("fallback_to_rag", True)
                    ),
                    "domain": cat.get("domain") or item.get("domain") or item.get("category", "general"),
                    "celery_queue": cat.get("celery_queue") or item.get("celery_queue"),
                    "rollout_status": cat.get("rollout_status") or item.get("rollout_status") or "stable",
                    "enabled_ratio": cat.get("enabled_ratio") if cat.get("enabled_ratio") is not None else item.get("enabled_ratio", 100),
                    "min_platform_version": cat.get("min_platform_version") or item.get("min_platform_version"),
                    "min_permission_level": cat.get("min_permission_level") or item.get("min_permission_level", "user"),
                    "catalog_indexed": bool(cat.get("embedding_vector")),
                })
            return skills

        except Exception as e:
            logger.error(f"获取 Skill 列表失败: {e}")
            return []

    def list_file_skills(self) -> list[dict[str, Any]]:
        """
        获取基于 SKILL.md 的 Skill 列表

        Returns:
            List[Dict]: 文件型 Skill 信息列表
        """
        skills = []

        if not self._skills_dir.exists():
            return skills

        for item in self._skills_dir.iterdir():
            if not item.is_dir():
                continue

            if item.name.startswith('.') or item.name == 'examples':
                continue

            skill_md = item / "SKILL.md"
            if not skill_md.exists():
                continue

            try:
                # 解析 SKILL.md
                content = skill_md.read_text(encoding='utf-8')
                metadata = self._parse_skill_md_content(content)

                ratio = metadata.get("enabled_ratio")
                if ratio is not None:
                    ratio = int(float(ratio) * 100 if float(ratio) <= 1 else float(ratio))
                skills.append({
                    "name": metadata.get("name", item.name),
                    "description": metadata.get("description", ""),
                    "category": metadata.get("category", "general"),
                    "tags": metadata.get("tags", []),
                    "version": metadata.get("version", "1.0.0"),
                    "enabled": metadata.get("enabled", True),
                    "fallback_to_rag": metadata.get("fallback_to_rag", True),
                    "domain": metadata.get("domain") or metadata.get("category", "general"),
                    "celery_queue": metadata.get("celery_queue"),
                    "rollout_status": metadata.get("rollout_status"),
                    "enabled_ratio": ratio,
                    "min_platform_version": metadata.get("min_platform_version"),
                    "min_permission_level": metadata.get("min_permission_level", "user"),
                    "skill_path": str(item),
                    "skill_md_path": str(skill_md)
                })

            except Exception as e:
                logger.warning(f"解析 Skill {item.name} 失败: {e}")

        return skills

    def _parse_skill_md_content(self, content: str) -> dict[str, Any]:
        """解析 SKILL.md frontmatter（与 skill_system.metadata 对齐）。"""
        from src.skill_system.metadata import parse_frontmatter

        frontmatter, _ = parse_frontmatter(content)
        return frontmatter if isinstance(frontmatter, dict) else {}

    def _find_skill_md_path(self, skill_name: str) -> Path | None:
        direct = self._skills_dir / skill_name / "SKILL.md"
        if direct.exists():
            return direct
        for item in self._skills_dir.iterdir():
            if not item.is_dir() or item.name.startswith(".") or item.name == "examples":
                continue
            skill_md = item / "SKILL.md"
            if not skill_md.exists():
                continue
            meta = self._parse_skill_md_content(skill_md.read_text(encoding="utf-8"))
            if meta.get("name") == skill_name:
                return skill_md
        return None

    def _set_skill_md_enabled(self, skill_name: str, enabled: bool) -> bool:
        skill_md = self._find_skill_md_path(skill_name)
        if not skill_md:
            return False
        from src.skill_system.metadata import normalize_markdown_content, parse_frontmatter

        text = normalize_markdown_content(skill_md.read_text(encoding="utf-8"))
        frontmatter, body = parse_frontmatter(text)
        frontmatter["enabled"] = enabled
        yaml_block = yaml.dump(
            frontmatter,
            allow_unicode=True,
            default_flow_style=False,
            sort_keys=False,
        ).strip()
        skill_md.write_text(f"---\n{yaml_block}\n---\n\n{body.lstrip()}", encoding="utf-8")
        return True

    def create_skill(self, data: dict[str, Any]) -> dict[str, Any]:
        """
        创建新 Skill

        Args:
            data: 包含 name, description, category, triggers, tags 等

        Returns:
            Dict: {"success": bool, "message": str, "skill_path": str}
        """
        try:
            skill_name = data.get("name", "").strip()

            if not skill_name:
                return {"success": False, "message": "Skill 名称不能为空"}

            # 检查名称格式
            if not self._validate_skill_name(skill_name):
                return {"success": False, "message": "名称只能包含字母、数字和连字符"}

            # 检查是否已存在
            skill_dir = self._skills_dir / skill_name
            if skill_dir.exists():
                return {"success": False, "message": f"Skill '{skill_name}' 已存在"}

            # 创建目录
            skill_dir.mkdir(parents=True, exist_ok=True)

            # 生成 SKILL.md
            skill_md = skill_dir / "SKILL.md"
            content = self._generate_skill_md(data)
            skill_md.write_text(content, encoding='utf-8')

            # 注册到 Registry
            self.reload_skill(skill_name)

            logger.info(f"创建 Skill 成功: {skill_name}")

            return {
                "success": True,
                "message": f"Skill '{skill_name}' 创建成功",
                "skill_path": str(skill_dir)
            }

        except Exception as e:
            logger.error(f"创建 Skill 失败: {e}")
            return {"success": False, "message": f"创建失败: {str(e)}"}

    def update_skill(self, skill_name: str, data: dict[str, Any]) -> dict[str, Any]:
        """
        更新 Skill

        Args:
            skill_name: Skill 名称
            data: 更新数据

        Returns:
            Dict: {"success": bool, "message": str}
        """
        try:
            skill_dir = self._skills_dir / skill_name

            if not skill_dir.exists():
                return {"success": False, "message": f"Skill '{skill_name}' 不存在"}

            skill_md = skill_dir / "SKILL.md"

            # 更新内容
            content = self._generate_skill_md(data)
            skill_md.write_text(content, encoding='utf-8')

            # 重新加载
            self.reload_skill(skill_name)

            logger.info(f"更新 Skill 成功: {skill_name}")

            return {"success": True, "message": f"Skill '{skill_name}' 更新成功"}

        except Exception as e:
            logger.error(f"更新 Skill 失败: {e}")
            return {"success": False, "message": f"更新失败: {str(e)}"}

    def delete_skill(self, skill_name: str) -> dict[str, Any]:
        """
        删除 Skill

        Args:
            skill_name: Skill 名称

        Returns:
            Dict: {"success": bool, "message": str}
        """
        try:
            skill_dir = self._skills_dir / skill_name

            if not skill_dir.exists():
                return {"success": False, "message": f"Skill '{skill_name}' 不存在"}

            # 从 Registry 移除
            self._unregister_skill(skill_name)

            # 删除目录
            import shutil
            shutil.rmtree(skill_dir)

            logger.info(f"删除 Skill 成功: {skill_name}")

            return {"success": True, "message": f"Skill '{skill_name}' 删除成功"}

        except Exception as e:
            logger.error(f"删除 Skill 失败: {e}")
            return {"success": False, "message": f"删除失败: {str(e)}"}

    def toggle_skill(self, skill_name: str, enabled: bool) -> dict[str, Any]:
        """
        启用/禁用 Skill

        Args:
            skill_name: Skill 名称
            enabled: 是否启用

        Returns:
            Dict: {"success": bool, "message": str}
        """
        try:
            from src.skills.registry import skill_registry

            skill = skill_registry.get_skill(skill_name)
            if not skill:
                return {"success": False, "message": f"Skill '{skill_name}' 不存在"}

            skill.enabled = enabled
            if not self._set_skill_md_enabled(skill_name, enabled):
                logger.warning("未能写入 SKILL.md enabled 字段: %s", skill_name)

            from src.skills.bootstrap import bootstrap_skills

            bootstrap_skills(force=True, persist_catalog=True)
            from src.skill_system import get_skill_system

            get_skill_system().reload_skill(skill_name)

            status = "启用" if enabled else "禁用"
            logger.info(f"{status} Skill: {skill_name}")

            return {"success": True, "message": f"Skill '{skill_name}' 已{status}"}

        except Exception as e:
            logger.error(f"切换 Skill 状态失败: {e}")
            return {"success": False, "message": f"操作失败: {str(e)}"}

    def reload_skill(self, skill_name: str) -> dict[str, Any]:
        """
        热加载 Skill

        Args:
            skill_name: Skill 名称

        Returns:
            Dict: {"success": bool, "message": str}
        """
        try:
            from src.skills.bootstrap import bootstrap_skills

            bootstrap_skills(force=True, persist_catalog=True)
            from src.skill_system import get_skill_system

            get_skill_system().reload_skill(skill_name)

            logger.info(f"热加载 Skill: {skill_name}")

            return {"success": True, "message": f"Skill '{skill_name}' 已重新加载"}

        except Exception as e:
            logger.error(f"热加载 Skill 失败: {e}")
            return {"success": False, "message": f"加载失败: {str(e)}"}

    def reload_all(self) -> dict[str, Any]:
        """
        重新加载所有 Skill

        Returns:
            Dict: {"success": bool, "message": str, "count": int}
        """
        try:
            from src.skills.bootstrap import bootstrap_skills

            count = bootstrap_skills(force=True, persist_catalog=True)

            logger.info(f"重新加载了 {count} 个 Skill")

            return {
                "success": True,
                "message": f"成功重新加载 {count} 个 Skill",
                "count": count
            }

        except Exception as e:
            logger.error(f"重新加载所有 Skill 失败: {e}")
            return {"success": False, "message": f"加载失败: {str(e)}"}

    def save_skill_content(self, skill_name: str, content: str) -> dict[str, Any]:
        """保存 SKILL.md 原文并热重载。"""
        try:
            skill_dir = self._skills_dir / skill_name
            if not skill_dir.exists():
                return {"success": False, "message": f"Skill '{skill_name}' 不存在"}

            skill_md = skill_dir / "SKILL.md"
            skill_md.write_text(content, encoding="utf-8")
            self.reload_skill(skill_name)

            return {"success": True, "message": f"Skill '{skill_name}' 内容已保存"}
        except Exception as e:
            logger.error(f"保存 Skill 内容失败: {e}")
            return {"success": False, "message": f"保存失败: {str(e)}"}

    def list_skill_files(self, skill_name: str) -> dict[str, Any]:
        """列出 Skill 目录下 scripts/references/assets 中的文件。"""
        allowed = ("scripts", "references", "assets")
        skill_dir = self._skills_dir / skill_name
        if not skill_dir.exists():
            return {"success": False, "message": f"Skill '{skill_name}' 不存在", "files": {}}

        files: dict[str, list[str]] = {}
        for folder in allowed:
            folder_path = skill_dir / folder
            if not folder_path.exists():
                files[folder] = []
                continue
            files[folder] = sorted(
                str(p.relative_to(folder_path)).replace("\\", "/")
                for p in folder_path.rglob("*")
                if p.is_file()
            )
        return {"success": True, "files": files}

    def upload_skill_file(
        self,
        skill_name: str,
        folder: str,
        filename: str,
        file_content: str,
    ) -> dict[str, Any]:
        """上传文件到 Skill 子目录（base64 内容）。"""
        import base64

        allowed = {"scripts", "references", "assets"}
        if folder not in allowed:
            return {"success": False, "message": f"不允许的目录: {folder}"}

        safe_name = Path(filename).name
        if not safe_name or safe_name.startswith(".") or ".." in safe_name:
            return {"success": False, "message": "非法文件名"}

        skill_dir = self._skills_dir / skill_name
        if not skill_dir.exists():
            return {"success": False, "message": f"Skill '{skill_name}' 不存在"}

        target_dir = skill_dir / folder
        target_dir.mkdir(parents=True, exist_ok=True)
        target_path = target_dir / safe_name

        try:
            raw = base64.b64decode(file_content)
            target_path.write_bytes(raw)
            self.reload_skill(skill_name)
            rel = f"{folder}/{safe_name}"
            return {
                "success": True,
                "message": f"文件 {rel} 上传成功",
                "path": rel,
            }
        except Exception as e:
            logger.error(f"上传 Skill 文件失败: {e}")
            return {"success": False, "message": f"上传失败: {str(e)}"}

    def get_skill_content(self, skill_name: str) -> str | None:
        """
        获取 Skill 的 SKILL.md 内容

        Args:
            skill_name: Skill 名称

        Returns:
            str: SKILL.md 内容，如果不存在返回 None
        """
        try:
            skill_dir = self._skills_dir / skill_name
            skill_md = skill_dir / "SKILL.md"

            if not skill_md.exists():
                return None

            return skill_md.read_text(encoding='utf-8')

        except Exception as e:
            logger.error(f"读取 Skill 内容失败: {e}")
            return None

    def get_stats(self) -> dict[str, Any]:
        """
        获取 Skill 统计信息

        Returns:
            Dict: 统计数据
        """
        try:
            from src.skills.registry import skill_registry

            skills = skill_registry.list_skills()
            categories = {}

            for skill in skills:
                categories[skill.category] = categories.get(skill.category, 0) + 1

            enabled_count = sum(1 for s in skills if s.enabled)

            return {
                "total_skills": len(skills),
                "enabled_skills": enabled_count,
                "disabled_skills": len(skills) - enabled_count,
                "categories": categories,
                "skills": [
                    {
                        "name": s.name,
                        "category": s.category,
                        "enabled": s.enabled
                    }
                    for s in skills
                ]
            }

        except Exception as e:
            logger.error(f"获取统计信息失败: {e}")
            return {}

    def _validate_skill_name(self, name: str) -> bool:
        """验证 Skill 名称格式"""
        import re
        return bool(re.match(r'^[a-zA-Z0-9\-_]+$', name))

    def _unregister_skill(self, skill_name: str):
        """从 Registry 移除 Skill"""
        try:
            from src.skills.registry import skill_registry

            # 从 _skills 字典中移除
            if skill_name in skill_registry._skills:
                del skill_registry._skills[skill_name]

            if skill_name in skill_registry._metadata_cache:
                del skill_registry._metadata_cache[skill_name]

        except Exception as e:
            logger.warning(f"从 Registry 移除 Skill 失败: {e}")

    def _generate_skill_md(self, data: dict[str, Any]) -> str:
        """生成标准化 SKILL.md 文件内容 (v2.0)"""
        try:
            from src.skill_system.metadata import create_skill_md

            skill_name = data.get("name", "")
            description = data.get("description", "")
            category = data.get("category", "general")
            tags = data.get("tags", [])
            version = data.get("version", "1.0.0")
            author = data.get("author", "NetOps Team")
            triggers = data.get("triggers", [])
            inputs = data.get("inputs", [])
            outputs = data.get("outputs", [])
            template_type = data.get("template_type", "generic")

            if template_type == "analysis":
                inputs = inputs or [
                    {"name": "ticket_id", "type": "string", "required": False, "description": "工单号"},
                    {"name": "prev_result", "type": "object", "required": False, "description": "上游步骤结果"},
                    {"name": "analysis_prompt", "type": "string", "required": False, "description": "分析问题"},
                ]
                outputs = outputs or [
                    {"name": "analysis", "type": "text", "description": "Markdown 分析报告"},
                    {"name": "analysis_json", "type": "object", "description": "结构化元数据"},
                ]
                entry_output = "file"
                if category == "general":
                    category = "analysis"
            else:
                entry_output = data.get("entry_output", "none")
                if not inputs:
                    inputs = [{"name": "param1", "type": "string", "required": False, "description": "输入参数"}]
                if not outputs:
                    outputs = [{"name": "result", "type": "text", "description": "执行结果"}]

            if not triggers:
                triggers = [f"使用{skill_name}"]

            from src.skill_system.metadata import resolve_category_governance

            gov = resolve_category_governance(category)
            return create_skill_md(
                skill_name=skill_name,
                description=description,
                category=category,
                tags=tags,
                author=author,
                triggers=triggers,
                version=version,
                inputs=inputs,
                outputs=outputs,
                domain=data.get("domain") or gov["domain"],
                celery_queue=data.get("celery_queue") or gov["celery_queue"],
                rollout_status=data.get("rollout_status", "draft"),
                enabled_ratio=int(data.get("enabled_ratio", 0)),
                min_platform_version=data.get("min_platform_version", "1.0.0"),
                min_permission_level=data.get("min_permission_level", "user"),
                entry_output=entry_output,
            )

        except ImportError as e:
            logger.warning(f"无法导入 metadata 模板，使用基础格式: {e}")
            frontmatter = {
                "name": data.get("name", ""),
                "version": data.get("version", "1.0.0"),
                "description": data.get("description", ""),
                "category": data.get("category", "general"),
                "tags": data.get("tags", []),
                "author": data.get("author", "NetOps Team"),
                "triggers": data.get("triggers", []),
                "inputs": data.get("inputs", []),
                "outputs": data.get("outputs", []),
                "enabled": data.get("enabled", True),
                "fallback_to_rag": data.get("fallback_to_rag", True),
            }
            yaml_str = yaml.dump(frontmatter, allow_unicode=True, default_flow_style=False)
            instructions = data.get("instructions", f"# {data.get('name', '')}\n\n{data.get('description', '')}")
            return f"---\n{yaml_str}---\n\n{instructions}\n"

    def get_skill_schema(self, skill_name: str) -> dict[str, Any] | None:
        """返回 Skill 的 I/O schema，供 Workflow Builder 参数面板使用。"""
        skill_md = self._find_skill_md_path(skill_name)
        if not skill_md:
            return None

        from src.skill_system.metadata import parse_skill_md

        try:
            metadata = parse_skill_md(skill_md, include_instructions=False)
        except Exception as exc:
            logger.warning("解析 Skill schema 失败 %s: %s", skill_name, exc)
            return None

        frontmatter = self._parse_skill_md_content(skill_md.read_text(encoding="utf-8"))
        return {
            "name": metadata.name,
            "description": metadata.description,
            "category": metadata.category,
            "version": metadata.version,
            "enabled": metadata.enabled,
            "entry_script": frontmatter.get("entry_script"),
            "entry_output": frontmatter.get("entry_output"),
            "execution_mode": metadata.execution_mode,
            "inputs": [inp.model_dump() for inp in metadata.inputs],
            "outputs": [out.model_dump() for out in metadata.outputs],
        }


# 全局单例
_skill_manager: SkillManager | None = None


def get_skill_manager() -> SkillManager:
    """获取 Skill 管理器单例"""
    global _skill_manager
    if _skill_manager is None:
        _skill_manager = SkillManager()
    return _skill_manager
