"""Validated request → per-device plan → equivalent factoring → vendor configuration."""

import argparse
import json
import re
import sys
from hashlib import sha256
from pathlib import Path

import request_template
from plan_preview import render
from policy_input import read_policy_workbook
from policy_plan import build_plan, digest, vendor_rules
from policy_window import NAME
from vendor_config import (
    H3CConfigGenerator,
    H3CF1000ConfigGenerator,
    HillstoneConfigGenerator,
    HuaweiConfigGenerator,
    TopSecConfigGenerator,
)
from vendor_validation import validate_output

from core import NetworkTopology

GENERATORS = {
    "华为": HuaweiConfigGenerator,
    "H3C": H3CConfigGenerator,
    "H3CF1000": H3CF1000ConfigGenerator,
    "天融信": TopSecConfigGenerator,
    "山石": HillstoneConfigGenerator,
}
# Device types that render validity windows and long links (USG6000, F5000).
TIMED = {"华为", "H3C"}


def safe_name(value):
    if not re.fullmatch(r"[\w.-]{1,80}", value) or value in {".", ".."}:
        raise ValueError(
            "工单号、设备名和安全域名只能包含文字、数字、下划线、点或短横线（最多 80 字符）"
        )
    return value


def main():
    parser = argparse.ArgumentParser(description="防火墙放行策略生成")
    parser.add_argument("-t", "--topology", default="topology.json")
    parser.add_argument("-p", "--policies", required=True)
    parser.add_argument("-o", "--output", default="configs")
    parser.add_argument("-u", "--user", default="system")
    parser.add_argument("-i", "--ticket-id", required=True)
    parser.add_argument("--input-name")
    parser.add_argument("--no-merge", action="store_true")
    parser.add_argument("--business-paths", action="store_true")
    args = parser.parse_args()
    safe_name(args.ticket_id)
    safe_name(args.user)
    topology = NetworkTopology(args.topology)
    df = read_policy_workbook(args.policies)
    plan, rules = build_plan(df, topology, args.ticket_id, merge=not args.no_merge)
    plan["include_business_paths"] = args.business_paths
    grouped = vendor_rules(rules, args.ticket_id)
    if not grouped:
        raise ValueError("没有可生成的设备规则")
    timed = any(row["window"] for rows in grouped.values() for row in rows)
    if timed and (not NAME.fullmatch(args.ticket_id) or args.ticket_id.lower() == "all"):
        raise ValueError(
            "有效期在设备上建以工单号命名的时间段，工单号需以英文字母开头，"
            "只含字母、数字、下划线或短横线，最多 32 个字符"
        )
    for name, rows in grouped.items():
        safe_name(name)
        fw_type = topology.firewalls[name].type
        if fw_type not in GENERATORS:
            raise ValueError(f"不支持的防火墙类型：{name} ({fw_type})")
        if fw_type not in TIMED and any(
            row["window"] or row["long_link"] is not None for row in rows
        ):
            raise ValueError(
                f"{name}（{fw_type}）暂不支持有效期和长连接；目前支持华为 USG6000 和 H3C F5000"
            )
        for row in rows:
            for zone in row["rule_key"]:
                safe_name(zone)
            if (
                not row["sources"]
                or not row["destinations"]
                or (row["proto"] != "icmp" and not row["ports"])
            ):
                raise ValueError("生成规则缺少源、目的或端口限制")
    output = Path(args.output)
    config_dir = output / args.user
    config_dir.mkdir(parents=True, exist_ok=True)
    commands = {}
    for name, rows in sorted(grouped.items()):
        generator = GENERATORS[topology.firewalls[name].type]
        generator.generate(str(config_dir), name, rows)
        files = [p for p in config_dir.iterdir() if p.stem == name and p.suffix in {".txt", ".cfg"}]
        if len(files) != 1:
            raise ValueError(f"{name} 的配置文件数量错误")
        text = files[0].read_text(encoding="utf-8")
        if not text.strip() or "move rule name xxx bottom" in text or "tcp/udp" in text:
            raise ValueError(f"{name} 的配置输出校验失败")
        validate_output(
            topology.firewalls[name].type, text, [rule for rule in rules if rule.context[0] == name]
        )
        # Use the same command bytes in the archive and persisted plan on Windows too.
        files[0].write_text(text, encoding="utf-8", newline="\n")
        commands[name] = text
    plan.update(
        input_name=args.input_name or Path(args.policies).name,
        input_sha256=sha256(Path(args.policies).read_bytes()).hexdigest(),
        topology_sha256=sha256(Path(args.topology).read_bytes()).hexdigest(),
        commands=commands,
    )
    plan["plan_id"] = digest(plan)
    (output / "policy_plan.json").write_text(
        json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (output / "策略计划预览.html").write_text(render(plan), encoding="utf-8")
    # Editable validated request in the template layout, for human intervention through the
    # existing upload flow. Window and long link as the requester wrote them, so a re-upload
    # reads them the same.
    names = {"source_system": "源业务系统", "destination_system": "目的业务系统"}
    columns = [
        key
        for key in [
            "source_system",
            "src_ip",
            "destination_system",
            "dst_ip",
            "port",
            "proto",
            "start_time",
            "end_time",
            "action",
            "long_link",
        ]
        if key in df
    ]
    rows = [
        {names.get(key, key): record[key] for key in columns}
        for record in df[columns].to_dict("records")
    ]
    (output / "策略申请表.xlsx").write_bytes(
        request_template.workbook(
            rows, note=f"工单 {args.ticket_id} 校验后的申请，可修改后重新上传生成"
        )
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"策略生成失败：{exc}", file=sys.stderr)
        raise SystemExit(1)
