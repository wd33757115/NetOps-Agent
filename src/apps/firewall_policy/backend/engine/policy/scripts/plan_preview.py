"""Standalone, escaped preview with no external assets or network requests."""

from html import escape


def render(plan):
    def text(value):
        return escape(str(value), quote=True)

    def table(headers, rows):
        return (
            '<div class="table"><table><thead><tr>'
            + "".join(f"<th>{text(h)}</th>" for h in headers)
            + "</tr></thead><tbody>"
            + "".join(
                "<tr>" + "".join(f"<td>{text(v)}</td>" for v in row) + "</tr>" for row in rows
            )
            + "</tbody></table></div>"
        )

    evidence = {item["id"]: item for item in plan["evidence"]}
    # Older plans have neither field; tickets without them keep the plain tables.
    timed = any(
        r.get("validity") or r.get("long_link_hours") is not None for r in plan["requests"]
    )

    def settings(item):
        hours = item.get("long_link_hours")
        return (
            item.get("validity") or "长期",
            "不启用" if hours is None else ("永不老化" if hours == 0 else f"{hours} 小时"),
        )

    opt = plan["optimization"]
    labels = {"verified": "等价校验通过", "disabled": "按原始规则生成", "unmerged": "保留原始规则"}
    body = f"<h1>防火墙策略计划</h1><p>工单 {text(plan['ticket_id'])} · 仅生成放行策略</p>"
    body += f"<p>{text(labels[opt['status']])} · {opt['before']} → {opt['after']} 条设备规则</p>"
    if opt.get("reason"):
        body += '<p class="notice">' + text(opt["reason"]) + "</p>"
    body += "<p>可核对申请行、地址归属和路径，再查看对应 CLI。需要修改时，编辑“策略申请表.xlsx”后重新上传生成；可在请求中说明“不要合并”。</p>"
    body += f'<p class="muted">输入文件：{text(plan["input_name"])}</p>'
    if plan.get("include_business_paths"):
        requests = {r["row"]: r for r in plan["requests"]}
        rows = []
        for ref in plan["evidence"]:
            request = requests[ref["row"]]
            rules = [r for r in plan["rules"] if ref["id"] in r["evidence_ids"]]
            rows.append(
                (
                    ref["row"],
                    request.get("source_system") or "未填写",
                    "\n".join(a for s in ref["source_segments"] for a in s["addresses"]),
                    request.get("destination_system") or "未填写",
                    "\n".join(a for s in ref["destination_segments"] for a in s["addresses"]),
                    request["proto"].upper()
                    + " "
                    + (request["port"] or "无需端口")
                    + ("\n有效期 %s\n长连接 %s" % settings(request) if timed else ""),
                    ("指定路径" if ref["path_source"] == "explicit" else "拓扑推导路径")
                    + "："
                    + " → ".join(" / ".join(hop) for hop in ref["path"]),
                    "\n".join(
                        r["device"]
                        + " · "
                        + r["source_zone"]
                        + " → "
                        + r["destination_zone"]
                        + " · "
                        + r["protocol"].upper()
                        + " "
                        + (", ".join(r["ports"]) or "无需端口")
                        for r in rules
                    ),
                )
            )
        body += "<section><h2>业务系统与拓扑路径</h2><p>按申请行和实际地址分段展示生成方案；尚未下发设备，拓扑路径不代表实际连通性。业务系统名称未填写时不推测。</p>"
        body += (
            table(
                [
                    "申请行",
                    "源业务系统",
                    "源地址分段",
                    "目的业务系统",
                    "目的地址分段",
                    "服务",
                    "拓扑路径",
                    "关联设备策略",
                ],
                rows,
            )
            + "</section>"
        )
    body += '<label>筛选设备、地址或申请行 <input id="search" type="search" placeholder="输入关键词" /></label>'
    for device in sorted({rule["device"] for rule in plan["rules"]}):
        body += f'<section class="device"><h2>{text(device)}</h2>'
        for rule in (r for r in plan["rules"] if r["device"] == device):
            refs = [evidence[ref] for ref in rule["evidence_ids"]]
            body += f"<article><h3>{text(rule['source_zone'])} → {text(rule['destination_zone'])} · {text(rule['protocol'].upper())}</h3>"
            body += table(
                ["源地址", "目的地址", "目的端口", "申请表行号"]
                + (["有效期", "长连接"] if timed else []),
                [
                    (
                        "\n".join(rule["sources"]),
                        "\n".join(rule["destinations"]),
                        ", ".join(rule["ports"]) or "不适用",
                        ", ".join(str(n) for n in sorted({ref["row"] for ref in refs})),
                    )
                    + (settings(rule) if timed else ())
                ],
            )
            body += "<details><summary>归属依据与经过的设备</summary>"
            for ref in refs:
                body += (
                    f"<p>申请表第 {ref['row']} 行 · "
                    + ("指定路径" if ref["path_source"] == "explicit" else "拓扑推导路径")
                    + "："
                    + text(" → ".join(".".join(n) for n in ref["path"]))
                    + "</p>"
                )
                body += table(
                    ["方向", "地址范围", "所属设备 / 安全域", "命中前缀"],
                    [
                        (
                            side,
                            ", ".join(segment["addresses"]),
                            " / ".join(segment["owner"]),
                            segment["prefix"],
                        )
                        for side, key in [
                            ("源", "source_segments"),
                            ("目的", "destination_segments"),
                        ]
                        for segment in ref[key]
                    ],
                )
            body += "</details></article>"
        body += (
            "<details><summary>查看最终 CLI</summary><pre>"
            + text(plan["commands"][device])
            + "</pre></details></section>"
        )
    body += (
        "<details><summary>原始申请</summary>"
        + table(
            ["行号", "源地址", "目的地址", "端口", "协议"] + (["有效期", "长连接"] if timed else []),
            [
                (r["row"], r["src_ip"], r["dst_ip"], r["port"], r["proto"])
                + (settings(r) if timed else ())
                for r in plan["requests"]
            ],
        )
        + "</details>"
    )
    if plan["warnings"]:
        body += (
            "<details open><summary>拓扑核对信息</summary><ul>"
            + "".join("<li>" + text(w) + "</li>" for w in plan["warnings"])
            + "</ul></details>"
        )
    return (
        """<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>防火墙策略计划</title><style>
body{font:15px/1.7 system-ui,"Microsoft YaHei",sans-serif;color:#1e293b;background:#f6f8fb;max-width:1200px;margin:40px auto;padding:0 24px}h1{font-size:30px}h2{font-size:22px;border-bottom:2px solid #cbd5e1;padding-bottom:8px}h3{font-size:16px}section{background:white;padding:20px 26px;margin:24px 0;border:1px solid #dbe3ee;border-radius:10px}.table{overflow:auto}table{border-collapse:collapse;width:100%;margin:12px 0}th,td{border:1px solid #dbe3ee;text-align:left;padding:10px;white-space:pre-wrap;vertical-align:top}th{background:#eff4fa}summary{cursor:pointer;color:#1758a6;padding:10px 0}pre{background:#101d30;color:#e2e8f0;padding:18px;overflow:auto;white-space:pre}input{padding:10px;border:1px solid #94a3b8;border-radius:6px;margin:8px;width:300px;max-width:80%}.notice{color:#92400e}.muted{color:#64748b;font-size:13px;overflow-wrap:anywhere}article{border-bottom:1px solid #e2e8f0;padding-bottom:14px}[hidden]{display:none}
</style>"""
        + body
        + """<script>document.getElementById('search').addEventListener('input',function(){const q=this.value.toLowerCase();document.querySelectorAll('.device').forEach(s=>{s.hidden=!s.textContent.toLowerCase().includes(q)})});</script></html>"""
    )
