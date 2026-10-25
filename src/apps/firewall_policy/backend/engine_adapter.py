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
from datetime import datetime
from pathlib import Path, PurePath

from . import storage as blobs
from .engine.policy.scripts.manifest import build_manifest_from_output, write_manifest_file
from .engine.ticket.scripts.change_ticket_excel import build_change_ticket_workbook

SCRIPT_DIR = Path(__file__).parent / "engine/policy/scripts"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def run_script(command, checkpoint, prefix, fallback):
    """Run one engine script; its last "<prefix>：…" stderr line becomes the error."""
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
                        raise ValueError("计算超时，请减少文件规模后重试")
        except BaseException:
            process.kill()
            process.communicate()
            raise
    if process.returncode:
        lines = stderr.decode("utf-8", errors="replace").splitlines()
        errors = [line for line in lines if line.startswith(f"{prefix}：")]
        raise ValueError(errors[-1] if errors else fallback)
    checkpoint()


def archive(directory):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as target:
        for path in sorted(directory.rglob("*")):
            if path.is_file():
                target.write(path, path.relative_to(directory).as_posix())
    return buffer.getvalue()


def generate(kind, inputs, parent, checkpoint):
    request = inputs["request"]
    if kind == "ticket":
        return ticket(request, parent, checkpoint)
    if kind == "block":
        return block(inputs, request, checkpoint)
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
        run_script(command, checkpoint, "策略生成失败", "策略生成失败，请检查申请表及拓扑内容")
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
        return {"success": True, "policy_plan": plan, "manifest": manifest}, {
            "config_zip": (
                f"{request['ticket_id']}-策略配置.zip",
                "application/zip",
                archive(output),
            ),
            "policy_preview": (
                "策略计划预览.html",
                "text/html; charset=utf-8",
                (output / "策略计划预览.html").read_bytes(),
            ),
            "policy_request": ("策略申请表.xlsx", XLSX, (output / "策略申请表.xlsx").read_bytes()),
        }


def ticket(request, parent, checkpoint):
    result = parent["result"]
    if "manifest" in result:
        manifest = copy.deepcopy(result["manifest"])
    else:
        # A block batch keeps its manifest as a file: device scripts can run to megabytes.
        item = parent["artifacts"]["manifest"]
        manifest = json.loads(blobs.get(item["object_key"], item["sha256"]))
    manifest.update(request["fields"])
    manifest["due_date"] = (manifest.get("due_date") or "").replace("T", " ")
    data = build_change_ticket_workbook(manifest)
    checkpoint()
    source = (
        {"policy_plan_id": result["policy_plan"]["plan_id"]}
        if "policy_plan" in result
        else {"batch": result["batch"]}
    )
    return source, {"change_excel": (f"{manifest['ticket_id']}-变更工单.xlsx", XLSX, data)}


def block(inputs, request, checkpoint):
    with tempfile.TemporaryDirectory(prefix="netops-firewall-block-") as temporary:
        directory = Path(temporary)
        output = directory / "output"
        command = [sys.executable, str(SCRIPT_DIR / "ip-block.py"), "-o", str(output)]
        for key in ["block", "whitelist"]:
            if key in inputs:
                item = inputs[key]
                path = directory / f"{key}{PurePath(item['filename']).suffix.lower()}"
                path.write_bytes(blobs.get(item["object_key"], item["sha256"]))
                command += [f"--{key}", str(path)]
        settings = directory / "request.json"
        settings.write_text(
            json.dumps(
                {
                    **request,
                    "now": datetime.now().strftime("%Y-%m-%d %H:%M"),
                    "block_name": inputs["block"]["filename"],
                    "whitelist_name": inputs.get("whitelist", {}).get("filename"),
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        command += ["--request", str(settings)]
        run_script(command, checkpoint, "封禁生成失败", "封禁生成失败，请检查名单文件")
        result = json.loads((output / "result.json").read_text(encoding="utf-8"))
        package = output / "package"
        return result, {
            "block_zip": (
                f"{request['ticket_id']}-IP封禁命令.zip",
                "application/zip",
                archive(package),
            ),
            "block_report": ("封禁核对表.xlsx", XLSX, (package / "封禁核对表.xlsx").read_bytes()),
            "manifest": (
                "manifest.json",
                "application/json",
                (output / "manifest.json").read_bytes(),
            ),
        }
