#!/usr/bin/env python
# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""防火墙策略 Skill 统一 CLI 入口（--params JSON + --output-dir）。"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

# 复用 Skill 内 manifest 构建（自包含，不依赖 core）
from manifest import build_manifest_from_output, write_manifest_file  # noqa: E402

MAIN_SCRIPT = SCRIPT_DIR / "firewall-policy.py"
DEFAULT_TOPOLOGY = SCRIPT_DIR / "topology.json"


def main() -> int:
    parser = argparse.ArgumentParser(description="防火墙策略生成 Skill")
    parser.add_argument("--params", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--policy", help="策略 Excel 本地路径（覆盖 params.policy_file_url）")
    parser.add_argument("--zip", help="兼容参数，未使用")
    args = parser.parse_args()

    with open(args.params, encoding="utf-8-sig") as f:
        p = json.load(f)
    for key, default in (("merge_policies", True), ("include_business_paths", False)):
        if not isinstance(p.get(key, default), bool):
            print(
                json.dumps(
                    {"success": False, "error": f"{key} 必须为 true 或 false"}, ensure_ascii=False
                ),
                file=sys.stderr,
            )
            return 1

    ticket_id = (
        p.get("ticket_id") or ""
    ).strip() or f"POLICY_{datetime.now().strftime('%Y%m%d%H%M%S')}"
    os.makedirs(args.output_dir, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmpdir:
        policy_path = args.policy or p.get("policy_file_url") or p.get("_policy_path")
        if not policy_path:
            print(
                json.dumps(
                    {
                        "success": False,
                        "error": "请上传策略申请表；工单号本身不包含需要放行的地址和端口",
                    },
                    ensure_ascii=False,
                ),
                file=sys.stderr,
            )
            return 1
        if not Path(policy_path).is_file():
            print(
                json.dumps(
                    {
                        "success": False,
                        "error": "策略申请表不存在或无法读取，请重新上传 Excel 文件",
                    },
                    ensure_ascii=False,
                ),
                file=sys.stderr,
            )
            return 1

        policy_copy = os.path.join(tmpdir, "policy.xlsx")
        shutil.copy(str(policy_path), policy_copy)

        topology = p.get("topology_file_url") or str(DEFAULT_TOPOLOGY)
        if not Path(topology).is_file():
            print(
                json.dumps(
                    {"success": False, "error": "指定的拓扑文件不存在或无法读取"},
                    ensure_ascii=False,
                ),
                file=sys.stderr,
            )
            return 1

        # Only publish this run's verified files; stale output cannot count as success.
        generated_dir = os.path.join(tmpdir, "generated")

        cmd = [
            sys.executable,
            str(MAIN_SCRIPT),
            "-t",
            str(topology),
            "-p",
            policy_copy,
            "-o",
            generated_dir,
            "-u",
            p.get("requester") or "system",
            "--ticket-id",
            ticket_id,
            "--input-name",
            p.get("policy_input_name") or Path(policy_path).name,
        ]
        if p.get("merge_policies") is False:
            cmd.append("--no-merge")
        if p.get("include_business_paths") is True:
            cmd.append("--business-paths")
        env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            timeout=300,
            cwd=str(SCRIPT_DIR),
        )
        if proc.returncode != 0:
            error_lines = [
                line for line in proc.stderr.splitlines() if line.startswith("策略生成失败：")
            ]
            err = {
                "success": False,
                "error": error_lines[-1]
                if error_lines
                else (proc.stderr or proc.stdout or "策略生成失败"),
            }
            print(json.dumps(err, ensure_ascii=False), file=sys.stderr)
            return 1

        manifest = build_manifest_from_output(
            generated_dir,
            ticket_id=ticket_id,
            ticket_title=p.get("ticket_title", "防火墙策略生成"),
            change_background=p.get("change_background") or p.get("ticket_title", ""),
            change_purpose=p.get("change_purpose") or "开通防火墙策略",
            requester=p.get("requester", ""),
            requester_dept=p.get("requester_dept", ""),
            assignee=p.get("assignee", ""),
            priority=p.get("priority", "P2"),
            due_date=p.get("due_date"),
            topology_path=str(topology),
            trace_id=p.get("workflow_run_id"),
        )
        if not manifest["scripts"] or any(
            not script["commands"].strip() for script in manifest["scripts"]
        ):
            print(
                json.dumps(
                    {
                        "success": False,
                        "error": "未生成有效设备配置，已取消导出。请检查策略输入和拓扑。",
                    },
                    ensure_ascii=False,
                ),
                file=sys.stderr,
            )
            return 1
        write_manifest_file(generated_dir, manifest)
        shutil.copytree(generated_dir, args.output_dir, dirs_exist_ok=True)
        plan = json.loads((Path(generated_dir) / "policy_plan.json").read_text(encoding="utf-8"))

        result = {
            "success": True,
            "status": "success",
            "message": f"防火墙策略生成成功，共 {manifest['device_count']} 台设备",
            "ticket_id": ticket_id,
            "manifest": manifest,
            "policy_plan_id": plan["plan_id"],
            "optimization": plan["optimization"],
        }
        # The app requests the structured plan; chat keeps its compact result.
        if p.get("include_policy_plan") is True:
            result["policy_plan"] = plan
        print(json.dumps(result, ensure_ascii=False, default=str))
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
