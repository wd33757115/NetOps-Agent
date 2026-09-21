"""Run the packaged algorithm in a cancellable, bounded subprocess."""

import copy
import io
import json
import os
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

from . import storage as blobs
from .engine.policy.scripts.manifest import build_manifest_from_output, write_manifest_file
from .engine.ticket.scripts.change_ticket_excel import build_change_ticket_workbook

SCRIPT_DIR = Path(__file__).parent / "engine/policy/scripts"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def generate(kind, inputs, parent_result, checkpoint):
    request = inputs["request"]
    if kind == "ticket":
        manifest = copy.deepcopy(parent_result["manifest"])
        manifest.update(request["fields"])
        manifest["due_date"] = manifest.get("due_date", "").replace("T", " ")
        data = build_change_ticket_workbook(manifest)
        checkpoint()
        return {"policy_plan_id": parent_result["policy_plan"]["plan_id"]}, {
            "change_excel": (f"{manifest['ticket_id']}-变更工单.xlsx", XLSX, data)
        }
    with tempfile.TemporaryDirectory(prefix="netops-firewall-") as temporary:
        directory = Path(temporary)
        policy, topology, output = (
            directory / "policy.xlsx",
            directory / "topology.json",
            directory / "output",
        )
        policy.write_bytes(blobs.get(inputs["policy"]["object_key"], inputs["policy"]["sha256"]))
        topology.write_bytes(
            blobs.get(inputs["topology"]["object_key"], inputs["topology"]["sha256"])
            if "topology" in inputs
            else json.dumps(inputs["default_topology"], ensure_ascii=False).encode()
        )
        command = [
            sys.executable,
            str(SCRIPT_DIR / "firewall-policy.py"),
            "-t",
            str(topology),
            "-p",
            str(policy),
            "-o",
            str(output),
            "-u",
            "system",
            "--ticket-id",
            request["ticket_id"],
            "--input-name",
            inputs["policy"]["filename"],
        ]
        if not request["merge_policies"]:
            command.append("--no-merge")
        if request["include_business_paths"]:
            command.append("--business-paths")
        started = time.monotonic()
        with subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8"),
            cwd=SCRIPT_DIR,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        ) as process:
            try:
                while True:
                    try:
                        stdout, stderr = process.communicate(timeout=5)
                        break
                    except subprocess.TimeoutExpired:
                        checkpoint()
                        if time.monotonic() - started > 300:
                            raise ValueError("策略计算超时，请减少申请表规模后重试")
            except BaseException:
                process.kill()
                process.communicate()
                raise
        if process.returncode:
            lines = stderr.decode("utf-8", errors="replace").splitlines()
            errors = [line for line in lines if line.startswith("策略生成失败：")]
            raise ValueError(errors[-1] if errors else "策略生成失败，请检查申请表及拓扑内容")
        checkpoint()
        plan = json.loads((output / "policy_plan.json").read_text(encoding="utf-8"))
        manifest = build_manifest_from_output(
            str(output),
            ticket_id=request["ticket_id"],
            ticket_title="防火墙策略变更",
            change_purpose="开通防火墙策略",
            topology_path=str(topology),
        )
        if not manifest["scripts"] or any(
            not script["commands"].strip() for script in manifest["scripts"]
        ):
            raise ValueError("未生成有效设备配置，请检查申请表及拓扑")
        write_manifest_file(str(output), manifest)
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(output.rglob("*")):
                if path.is_file():
                    archive.write(path, path.relative_to(output).as_posix())
        return {"success": True, "policy_plan": plan, "manifest": manifest}, {
            "config_zip": (
                f"{request['ticket_id']}-策略配置.zip",
                "application/zip",
                buffer.getvalue(),
            ),
            "policy_preview": (
                "策略计划预览.html",
                "text/html; charset=utf-8",
                (output / "策略计划预览.html").read_bytes(),
            ),
            "policy_request": ("策略申请表.xlsx", XLSX, (output / "策略申请表.xlsx").read_bytes()),
        }
