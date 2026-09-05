"""The authoring CLI must give CI the same verdict as its JSON report."""

import json
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize(
    "has_name,triggers,valid",
    [(True, "[sample, example]", True), (False, "[sample]", False),
     (True, "[]", True), (True, "sample", False), (True, '[" "]', False)],
    ids=["valid-disabled-skill", "missing-name", "internal-skill", "wrong-trigger-type", "blank-trigger"],
)
def test_skill_validator_json_exit_status_and_explicit_false(tmp_path, has_name, triggers, valid):
    header = "name: sample-skill\n" if has_name else ""
    content = (
        "<!-- SPDX-License-Identifier: Apache-2.0 -->\n\n---\n"
        + header
        + "version: 1.0.0\ndescription: Example skill\ncategory: network\n"
        f"triggers: {triggers}\nenabled: false\nfallback_to_rag: false\n"
        "---\n\n# Sample skill\n\n"
    )
    for title in ("核心能力", "工作流程", "输出格式", "示例", "注意事项"):
        content += f"## {title}\n\nThis section describes the example skill behavior.\n\n"
    path = tmp_path / "SKILL.md"
    path.write_text(content, encoding="utf-8")
    root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [sys.executable, str(root / "scripts/validate_skill.py"), str(path), "--json"],
        cwd=root,
        capture_output=True,
        encoding="utf-8",
        check=False,
    )
    report = json.loads(result.stdout)
    assert result.returncode == (0 if valid else 1)
    assert report["failed"] == (0 if valid else 1)
    skill = report["skills"][0]
    assert not any("enabled" in item or "fallback_to_rag" in item for item in skill["warnings"])
    assert not any("文件末尾" in item for item in skill["warnings"])
    if not has_name:
        assert any("必填字段: name" in item for item in skill["errors"])
