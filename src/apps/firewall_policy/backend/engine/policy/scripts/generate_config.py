#!/usr/bin/env python
# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""
防火墙策略生成 Skill 入口脚本。

SKILL.md 中引用的 scripts/generate_config.py，与 firewall-policy.py 等价。
"""

from __future__ import annotations

import runpy
import sys
from pathlib import Path

SCRIPT = Path(__file__).with_name("firewall-policy.py")

if not SCRIPT.exists():
    raise SystemExit(f"策略生成脚本不存在: {SCRIPT}")

scripts_dir = str(SCRIPT.parent)
if scripts_dir not in sys.path:
    sys.path.insert(0, scripts_dir)

sys.argv[0] = str(SCRIPT)
runpy.run_path(str(SCRIPT), run_name="__main__")
