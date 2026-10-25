# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""防火墙策略生成脚本路径（Skill scripts 目录）。"""

from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
SKILL_SCRIPTS_DIR = PROJECT_ROOT / "src/apps/firewall_policy/backend/engine/policy/scripts"
DEFAULT_POLICY_FILE = SKILL_SCRIPTS_DIR / "test_policy.xlsx"
