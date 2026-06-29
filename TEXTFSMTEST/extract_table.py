#!/usr/bin/env python3
"""
Extract structured device patrol data from raw capture files.
Parses the "命令:/输出:/------" format correctly.
Outputs CSV.
"""
import re
import csv
from pathlib import Path

SRC_DIR = Path(__file__).parent
OUT_CSV = SRC_DIR / "device_patrol_summary.csv"

FILES = sorted(f for f in SRC_DIR.glob("*.txt") if "obfuscated" not in str(f))


def read_file(filepath):
    for enc in ["utf-8", "gb18030", "gbk", "latin-1"]:
        try:
            return filepath.read_text(encoding=enc)
        except (UnicodeDecodeError, UnicodeError):
            continue
    return filepath.read_text(encoding="utf-8", errors="replace")


def parse_commands(content):
    """
    Split content into command blocks.
    Format:
        命令: <cmd>
        输出:
        <lines>
        --------------------------------------------------
    Returns dict: {normalized_cmd_name: output_text}
    """
    blocks = {}
    # Split by "命令:" format. Each block is: 命令: <cmd>\n输出:\n<output>\n--------------------------------------------------
    # Use simpler pattern: just find command+output between separators
    pattern = re.compile(r'命令:\s*(.+?)\s*\n输出:\n(.*?)(?=\n--------------------------------------------------\n)', re.DOTALL)
    for m in pattern.finditer(content):
        cmd_name = m.group(1).strip()
        output = m.group(2).strip()
        normalized = cmd_name.lower().replace(" ", "").replace("_", "")
        blocks[normalized] = output
        blocks[cmd_name] = output

    # Handle last block (may not have trailing separator)
    last_cmd = re.search(r'命令:\s*(.+?)\s*\n输出:\n(.*?)$', content, re.DOTALL)
    if last_cmd:
        cmd_name = last_cmd.group(1).strip()
        output = last_cmd.group(2).strip()
        normalized = cmd_name.lower().replace(" ", "").replace("_", "")
        if normalized not in blocks:
            blocks[normalized] = output
            blocks[cmd_name] = output

    return blocks


def find_block(blocks, *keys):
    """Find a block by multiple possible normalized keys."""
    for k in keys:
        k2 = k.lower().replace(" ", "").replace("_", "")
        if k2 in blocks:
            return blocks[k2]
    return ""


def extract_all(filepath):
    """Extract all metrics from one file."""
    content = read_file(filepath)
    blocks = parse_commands(content)

    # Parse device name from filename
    name = filepath.stem
    parts = name.split("-")
    # Find IP address part
    ip = ""
    for i, p in enumerate(parts):
        if re.match(r"^\d+\.\d+\.\d+\.\d+$", p):
            ip = p
            device = "-".join(parts[:i])
            break
    else:
        device = name

    result = {"device": device, "ip": ip}

    # === 1. System Version ===
    ver_output = find_block(blocks, "show version", "Show version", "display version")
    ver_info = {}

    if ver_output:
        # NXOS
        for m in re.finditer(r'Hardware\s*\n\s*cisco\s+(\S[\S\s]+?)\s+Chassis', ver_output):
            ver_info["model"] = m.group(1).strip()
        m = re.search(r"(N9K-C\d+\S*)", ver_output)
        if m:
            ver_info["model"] = m.group(1)
        m = re.search(r'\bNXOS:\s*version\s+([\d\.\(\)A-Za-z]+)', ver_output)
        if m:
            ver_info["version"] = f"NXOS {m.group(1)}"
        m = re.search(r'System version:\s*([\d\.\(\)A-Za-z]+)', ver_output)
        if m and "version" not in ver_info:
            ver_info["version"] = m.group(1)

        # Cisco IOS
        m = re.search(r'Cisco IOS(?: XE)? Software.*?Version\s+([\d\.\(\)A-Za-z]+)', ver_output)
        if m:
            ver_info["version"] = f"IOS {m.group(1)}"
        m = re.search(r'(WS-C\d\S+|ISR\d\S+|C\d{4}\S+)', ver_output)
        if m and "model" not in ver_info:
            ver_info["model"] = m.group(1)
        m = re.search(r'cisco\s+(ISR\d\S+|N\S+)', ver_output)
        if m and "model" not in ver_info:
            ver_info["model"] = m.group(1)
        m = re.search(r'Model number\s*:\s*(\S+)', ver_output)
        if m and "model" not in ver_info:
            ver_info["model"] = m.group(1)

        # H3C Comware
        m = re.search(r'Comware Software.*?Version\s+([\d\.]+)', ver_output, re.IGNORECASE)
        if m:
            ver_info["version"] = f"Comware {m.group(1)}"

        # Uptime
        m = re.search(r'Kernel uptime is (\d+ day\(s\), \d+ hour\(s\), \d+ minute\(s\).*?)$', ver_output, re.MULTILINE)
        if m:
            ver_info["uptime"] = m.group(1).strip()
        else:
            m = re.search(r'(uptime is .+)$', ver_output, re.MULTILINE)
            if m:
                ver_info["uptime"] = m.group(1).strip()

    result["model"] = ver_info.get("model", "")
    result["version"] = ver_info.get("version", "")
    result["uptime"] = ver_info.get("uptime", "")

    # === 2. System Clock / Time ===
    clock_output = find_block(blocks, "show clock", "Show clock", "display clock")
    if clock_output:
        lines = clock_output.strip().split("\n")
        for l in lines:
            if re.search(r"\d{1,2}:\d{2}:\d{2}", l):
                result["clock"] = l.strip()
                break
        else:
            result["clock"] = clock_output.split("\n")[0].strip()
    else:
        result["clock"] = ""

    # === 3. Deployment Type ===
    deploy = "单机"
    for block_text in blocks.values():
        if "vpc domain" in block_text.lower() or "vPC role" in block_text.lower():
            deploy = "vPC双机"
            break
        if "m-lag" in block_text.lower() or "MLAG" in block_text:
            deploy = "M-LAG双机"
            break
        if "irf member" in block_text.lower() or "irf-port" in block_text.lower() or "irf" in block_text.lower():
            deploy = "IRF堆叠"
            break
        if re.search(r'Switch\s+Ports\s+Model', block_text):
            # Stack: look for multiple switches
            if "Switch 02" in block_text or "Switch  02" in block_text:
                deploy = "Stack堆叠"
                break
        if re.search(r'switch/role.*master', block_text, re.IGNORECASE):
            deploy = "Stack堆叠"
            break
    result["deploy"] = deploy

    # === 4. CPU ===
    cpu_output = find_block(blocks, "show system resources", "show processes cpu", "display cpu", "display cpu-usage")
    cpu_str = ""
    if cpu_output:
        # NXOS system resources
        m = re.search(r'CPU states\s*:\s*(.+)$', cpu_output, re.MULTILINE)
        if m:
            cpu_str = m.group(1).strip()
        # IOS processes cpu
        if not cpu_str:
            m = re.search(r'CPU utilization for five seconds.*?(\d+)%', cpu_output)
            if m:
                cpu_str = f"5sec: {m.group(1)}%"
            m = re.search(r'(one minute: \d+%; five minutes: \d+%)', cpu_output)
            if m:
                cpu_str = f"{cpu_str}, {m.group(1)}"
            m = re.search(r'Load average.*?:\s*(.+)$', cpu_output, re.MULTILINE)
            if m:
                cpu_str = f"{cpu_str}, LoadAvg: {m.group(1).strip()}"
        # H3C
        if not cpu_str:
            m = re.search(r'(\d+%) in last 5 seconds', cpu_output)
            if m:
                cpu_str = f"5sec: {m.group(1)}"
    result["cpu"] = cpu_str

    # === 5. Memory ===
    mem_output = find_block(blocks, "show system resources", "show processes memory", "display memory")
    mem_str = ""
    if mem_output:
        # NXOS: "Memory usage: 32828228K total, 6724700K used, 26103528K free"
        m = re.search(r'Memory usage:\s*(\d+)\S*\s*\w+,?\s+(\d+)\S*\s*\w+,?\s+(\d+)\S*', mem_output)
        if m:
            total = int(m.group(1))
            used = int(m.group(2))
            pct = round(used / total * 100, 1)
            mem_str = f"{pct}% ({used//1024}M/{total//1024}M)"
        # IOS
        if not mem_str:
            m = re.search(r'Processor Pool Total:\s*(\d+)\s+Used:\s*(\d+)\s+Free:\s*(\d+)', mem_output)
            if m:
                total = int(m.group(1))
                used = int(m.group(2))
                pct = round(used / total * 100, 1)
                mem_str = f"{pct}% ({used//1024}K/{total//1024}K)"
            # Another format
            m = re.search(r'Processor.*?(\d+) Total.*?(\d+) Used.*?(\d+) Free', mem_output)
            if m:
                total = int(m.group(1))
                used = int(m.group(2))
                pct = round(used / total * 100, 1)
                mem_str = f"{pct}% ({used//1024}K/{total//1024}K)"
        # H3C
        if not mem_str:
            m = re.search(r'(\d+)\s*%\s*used', mem_output)
            if m:
                mem_str = f"{m.group(1)}%"
    result["memory"] = mem_str

    # === 6. Fan ===
    fan_output = find_block(blocks, "show environment fan", "show env all")
    fan_lines = []
    if fan_output:
        for line in fan_output.split("\n"):
            s = line.strip()
            if not s or s.startswith("--") or s.startswith("=="):
                continue
            # NXOS fan table: Fan1(sys_fan1) ... Ok
            m = re.search(r'(Fan\d+\S*|sys_fan\S*|Fan_in_\S+)\s+.*?\s+(O[kK]|Ok)$', s)
            if m:
                fan_lines.append(f"{m.group(1)}:{m.group(2)}")
                continue
            # IOS: FAN PS-1 is OK  (with PS number)
            m = re.search(r'(FAN\s+PS-\d+)\s+is\s+(OK|NORMAL|GOOD)$', s, re.IGNORECASE)
            if m:
                fan_lines.append(f"{m.group(1)}:{m.group(2)}")
                continue
            # IOS: FAN is OK  (without PS number)
            m = re.search(r'^FAN\s+is\s+(OK|NORMAL|GOOD)$', s, re.IGNORECASE)
            if m:
                fan_lines.append(f"FAN:{m.group(1)}")
                continue
            # IOS: RPM: fan0 P0 Normal 3584 RPM
            m = re.search(r'RPM:\s*(fan\d+\S*)\s+\S+\s+(Normal|OK)', s, re.IGNORECASE)
            if m:
                fan_lines.append(f"{m.group(1)}:{m.group(2)}")
                continue
    result["fan"] = "; ".join(fan_lines) if fan_lines else "未采集"

    # === 7. Power ===
    pwr_output = find_block(blocks, "show environment power", "show env all")
    pwr_lines = []
    seen_pwr = set()
    if pwr_output:
        for line in pwr_output.split("\n"):
            s = line.strip()
            if not s:
                continue
            # NXOS: 1    NXA-PAC-1200W-PI    129 W    158 W    1200 W    Ok
            m = re.search(r'^(\d+)\s+\S+\s+.*?(\d+)\s*W\s+(\d+)\s*W\s+(\d+)\s*W\s+(O[kK]|Ok)$', s)
            if m:
                label = f"PS{m.group(1)}:{m.group(5)}({m.group(2)}W)"
                if "PS" + m.group(1) not in seen_pwr:
                    seen_pwr.add("PS" + m.group(1))
                    pwr_lines.append(label)
                continue
            # NXOS redundancy
            if re.search(r'(PS-Redundant|Power\s*Supply\s*redundancy)', s, re.IGNORECASE):
                if "冗余" not in seen_pwr:
                    seen_pwr.add("冗余")
                    pwr_lines.append("冗余")
                continue
            # IOS: 1A  PWR-C2-250WAC  SN  OK  Good  N/A  250/0
            m = re.search(r'^(\d+\S*)\s+PWR-\S+.*?\s+(OK|NORMAL|GOOD)\s+Good', s)
            if m:
                label = f"PWR{m.group(1)}:{m.group(2)}"
                if label not in seen_pwr:
                    seen_pwr.add(label)
                    pwr_lines.append(label)
                continue
            # IOS: 1  Built-in  Good  (no PWR part, built-in supply)
            m = re.search(r'^(\d+)\s+Built-in\s+(Good|OK)', s, re.IGNORECASE)
            if m:
                label = f"PS{m.group(1)}(内置):OK"
                if label not in seen_pwr:
                    seen_pwr.add(label)
                    pwr_lines.append(label)
                continue
            # IOS: P: pwr  P0  Normal  27 Watts
            m = re.search(r'P:\s*pwr\s+(\S+)\s+(Normal|OK)\s+(\d+)\s*Watts', s, re.IGNORECASE)
            if m:
                label = f"PU{m.group(1)}({m.group(3)}W)"
                if label not in seen_pwr:
                    seen_pwr.add(label)
                    pwr_lines.append(label)
                continue
    result["power"] = "; ".join(pwr_lines) if pwr_lines else "未采集"

    # === 8. Temperature ===
    temp_output = find_block(blocks, "show environment temperature", "show env all", "display environment")
    temp_lines = []
    if temp_output:
        for line in temp_output.split("\n"):
            s = line.strip()
            if not s:
                continue
            # NXOS table: 1  FRONT  70  42  22  Ok
            m = re.search(r'^\d+\s+(\w+(?:-\w+)*)\s+\d+\s+\d+\s+(\d+)\s+(O[kK]|Ok|Normal|GOOD)$', s)
            if m:
                temp_lines.append(f"{m.group(1)}:{m.group(2)}C")
                continue
            # IOS: System Temperature Value: 43 Degree Celsius
            m = re.search(r'System\s+Temperature\s+Value:\s*(\d+)\s*Degree', s, re.IGNORECASE)
            if m:
                temp_lines.append(f"System:{m.group(1)}C")
                continue
            # IOS: Temp: Inlet 1    R0    Normal    25 Celsius
            m = re.search(r'Temp:\s*(\S+\s*\S+?)\s+\S+\s+(?:Normal|OK|GOOD)\s+(\d+)\s*Celsius', s, re.IGNORECASE)
            if m:
                temp_lines.append(f"{m.group(1).strip()}:{m.group(2)}C")
                continue
            # Generic: sensor temp value
            m = re.search(r'Temperature\s*Value.*?(\d+)\s*Celsius', s, re.IGNORECASE)
            if m:
                temp_lines.append(f"Temp:{m.group(1)}C")
                continue
    result["temperature"] = "; ".join(temp_lines) if temp_lines else "未采集"

    # === 9. Interface Status ===
    iface_output = find_block(blocks, "show interface status", "show ip interface brief", "display interface brief", "display ip interface brief")
    up = down = admin_down = 0
    key_ifaces = []
    if iface_output:
        for line in iface_output.split("\n"):
            # NXOS/Cisco: show interface status
            m = re.match(r'(?:Eth\d\S*|Gi\d\S*|Te\d\S*|Fo\d\S*|Po\d+|Vlan\d+|mgmt\d+|Lo\d+)\s+'
                         r'.*?\s+(connected|notconnect|disabled|errdisabled|xcvrAbsen|sfpAbsent|up|down|administratively\s*down)', line, flags=re.IGNORECASE)
            if m:
                s = m.group(1).lower()
                port = line.split()[0]
                if s in ("connected", "up"):
                    up += 1
                    key_ifaces.append(f"{port}:up")
                elif s in ("disabled", "administratively down"):
                    admin_down += 1
                elif s in ("notconnect", "down", "xcvrabsen", "sfpabsent"):
                    down += 1
                continue

            # Cisco IOS: show ip interface brief (6 columns: Iface IP OK? Method Status Protocol)
            m = re.match(r'(\S+)\s+\S+\s+YES\s+\S+\s+(up|down|administratively\s*down)', line, re.IGNORECASE)
            if m:
                s = m.group(2).lower()
                if "up" in s:
                    up += 1
                elif "administratively" in s:
                    admin_down += 1
                else:
                    down += 1
                continue

            # H3C display interface brief
            m = re.match(r'(\S+)\s+(UP|DOWN|ADM|\*DOWN)', line.strip())
            if m:
                s = m.group(2).upper()
                if s == "UP":
                    up += 1
                elif s in ("ADM", "*DOWN"):
                    admin_down += 1
                else:
                    down += 1

    result["iface_status"] = f"UP:{up} DOWN:{down} ADMIN_DOWN:{admin_down}"
    if key_ifaces:
        result["iface_status"] += f" | {', '.join(key_ifaces[:10])}"

    # === 10. Redundancy (vPC/Stack/MLAG/VRRP) ===
    redundancy = ""
    # vPC
    vpc_output = find_block(blocks, "show vpc", "show vpc brief")
    if vpc_output:
        role = peer = vpcs = ""
        m = re.search(r'Role:\s*(\S+)', vpc_output)
        if m:
            role = m.group(1)
        m = re.search(r'peer adjacency formed\s*(ok|OK)', vpc_output, re.IGNORECASE)
        if m:
            peer = "peerOK"
        m = re.search(r'(\d+)\s+vPCs.*?u[pP]', vpc_output)
        if m:
            vpcs = f"vPCs:{m.group(1)}up"
        if role:
            redundancy = f"vPC domain, role:{role}, {peer} {vpcs}".strip()

    # Stack (Cisco)
    if not redundancy:
        # From show version
        ver_output = find_block(blocks, "show version", "Show version")
        if ver_output and "Switch 02" in ver_output:
            # Count switches
            count = len(re.findall(r'Switch\s+\d{2}\s', ver_output))
            if count >= 2:
                redundancy = f"Stack: {count} members"

    # IRF
    if not redundancy:
        irf_output = find_block(blocks, "display irf")
        if irf_output:
            members = len(re.findall(r'^\s*\d+\s', irf_output, re.MULTILINE))
            if members >= 2:
                redundancy = f"IRF: {members} members"

    # MLAG
    if not redundancy:
        mlag_output = find_block(blocks, "display m-lag")
        if mlag_output:
            m = re.search(r'(Work mode|MLAG role).*?:\s*(\S+)', mlag_output, re.IGNORECASE)
            role_str = m.group(2) if m else ""
            redundancy = f"M-LAG, role:{role_str}" if role_str else "M-LAG"

    # VRRP
    vrrp_output = find_block(blocks, "show vrrp", "show vrrp brief", "display vrrp", "display vrrp statistics")
    vrrp_detail = ""
    if vrrp_output:
        vrrp_entries = []
        for line in vrrp_output.split("\n"):
            m = re.search(r'(Vlan\S+|Vl\d+|Gi\S+|Eth\S+|Vlif\S+)\s+(\d+)\s+(Master|Backup|Initialize)', line)
            if m:
                vrrp_entries.append(f"{m.group(1)}:{m.group(3)}(pri{m.group(2)})")
        vrrp_detail = "; ".join(vrrp_entries[:6])
        if not redundancy:
            redundancy = f"VRRP ({len(vrrp_entries)} groups)" if vrrp_entries else ""

    # Check if vPC command returned invalid (not configured)
    if vpc_output and "invalid command" in vpc_output.lower():
        redundancy = "vPC未启用"

    result["redundancy"] = redundancy
    result["vrrp"] = vrrp_detail

    # === 11. Boot/Startup File ===
    boot_output = find_block(blocks, "show boot", "show version", "Show version")
    boot_str = ""
    if boot_output:
        m = re.search(r'NXOS image file is:\s*(\S+)', boot_output)
        if m:
            boot_str = m.group(1)
        m = re.search(r'System image file is\s*"(.+?)"', boot_output)
        if m:
            boot_str = m.group(1)
    if not boot_str:
        startup_output = find_block(blocks, "show startup-config", "display startup")
        if startup_output:
            m = re.search(r'version\s+([\d\.\(\)A-Za-z]+)', startup_output)
            if m:
                boot_str = f"Startup ver: {m.group(1)}"
            m = re.search(r'(Current startup|Next startup|Startup).*', startup_output)
            if m:
                boot_str = m.group(0).strip()
    result["boot"] = boot_str

    # === 12. Optical Power (Transceiver) ===
    transceiver_output = find_block(blocks, "show interface transceiver detail", "show interface transceiver", "display transceiver", "show interface transceiver details")
    optical_lines = []
    if transceiver_output:
        # Check DOM not supported
        if "dom is not supported" in transceiver_output.lower() or "transceiver monitoring not supported" in transceiver_output.lower():
            optical_lines.append("DOM不支持")
        # Extract Tx/Rx power per interface
        current_iface = ""
        for line in transceiver_output.split("\n"):
            m = re.match(r'^(Ethernet\d\S*|Eth\d\S*)', line, re.IGNORECASE)
            if m:
                current_iface = m.group(1)
            m = re.search(r'(Tx Power|Rx Power|Transceiver Temperature)\s+([-\d.]+)\s*(dBm|C)', line, re.IGNORECASE)
            if m:
                prefix = f"{current_iface}:" if current_iface else ""
                optical_lines.append(f"{prefix}{m.group(1)}:{m.group(2)}{m.group(3)}")
            # Aggregate format: "0.55 dBm   -6.55 dBm"
            m = re.search(r'Transceiver Temperature\s+([-\d.]+)\s*C.*?(?:Tx Power|In\s+).*?([-\d.]+)\s*dBm.*?(?:Rx Power|Out\s+).*?([-\d.]+)\s*dBm', line)
            if m:
                prefix = f"{current_iface}:" if current_iface else ""
                optical_lines.append(f"{prefix}Temp:{m.group(1)}C Tx:{m.group(2)}dBm Rx:{m.group(3)}dBm")
    result["optical"] = "; ".join(optical_lines[:12]) if optical_lines else "未采集"

    # === 13. Interface Errors ===
    # Check "show interface status | include error"
    err_status = find_block(blocks, "show interface status | include error")
    err_lines = []
    if err_status and err_status.strip():
        err_lines.append(f"status_errors: {err_status.strip()[:200]}")

    # Check detailed interface error counts (from show interface, show interface stats, etc.)
    # We look for input_errors, CRC, output_errors in any output that looks like interface details
    for key, output in blocks.items():
        if "interface" in key.lower() and "transceiver" not in key.lower() and "brief" not in key.lower() and "status" not in key.lower():
            # Detailed interface output
            current_iface = ""
            for line in output.split("\n"):
                m = re.match(r'^(\S+) is (?:up|down|administratively)', line)
                if m:
                    current_iface = m.group(1)
                m = re.search(r'(\d+) input errors', line)
                if m and m.group(1) != "0":
                    err_lines.append(f"{current_iface}:input_err={m.group(1)}")
                m = re.search(r'(\d+) CRC', line)
                if m and m.group(1) != "0":
                    err_lines.append(f"{current_iface}:CRC={m.group(1)}")
                m = re.search(r'(\d+) frame\b', line)
                if m and m.group(1) != "0":
                    err_lines.append(f"{current_iface}:frame={m.group(1)}")
                m = re.search(r'(\d+) output errors', line)
                if m and m.group(1) != "0":
                    err_lines.append(f"{current_iface}:output_err={m.group(1)}")
                m = re.search(r'(\d+) collisions', line)
                if m and m.group(1) != "0":
                    err_lines.append(f"{current_iface}:collisions={m.group(1)}")
    result["errors"] = "; ".join(err_lines[:10]) if err_lines else "无错包"

    return result


def main():
    headers = [
        "设备名称", "IP地址", "设备型号", "软件版本",
        "运行时间", "系统时间", "部署方式",
        "CPU使用率", "内存使用率",
        "风扇状态", "电源状态", "温度",
        "接口状态", "设备冗余状态(堆叠/MLAG/VRRP)",
        "VRRP详情", "启动文件", "接口光衰", "接口错包"
    ]

    rows = []
    for filepath in FILES:
        print(f"Processing: {filepath.name}...")
        d = extract_all(filepath)
        rows.append([
            d["device"], d["ip"],
            d["model"], d["version"],
            d["uptime"], d["clock"], d["deploy"],
            d["cpu"], d["memory"],
            d["fan"], d["power"], d["temperature"],
            d["iface_status"], d["redundancy"],
            d["vrrp"], d["boot"], d["optical"], d["errors"]
        ])

    # Write CSV
    with open(OUT_CSV, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(headers)
        writer.writerows(rows)
    print(f"\nCSV saved: {OUT_CSV}")

    # Print table
    print("\n" + "=" * 180)
    print("设备巡检汇总表".center(100))
    print("=" * 180)

    # Use abbreviated format for readability
    for row in rows:
        print(f"\n{'─'*120}")
        for h, v in zip(headers, row):
            if v and str(v).strip():
                print(f"  {h:<24}: {v}")

    # Also print compact table with key columns
    print(f"\n\n{'='*180}")
    print("紧凑总览表")
    print(f"{'='*180}")
    cw = [24, 14, 22, 20, 22, 12, 12, 20, 18, 30, 24, 22]
    ch = ["设备名称", "IP", "型号", "软件版本", "运行时间", "部署方式", "CPU", "内存", "温度(部分)", "冗余状态", "错包", "光衰"]
    header_str = "|".join(f"{h:<{w}}" for h, w in zip(ch, cw))
    sep = "+".join("-" * w for w in cw)
    print(sep)
    print(header_str)
    print(sep)
    for row in rows:
        vals = [
            row[0][:23], row[1], row[2][:21], row[3][:19], row[4][:21],
            row[6][:11], row[7][:19], row[8][:17], row[11][:29],
            row[13][:23], row[17][:21], row[16][:23]
        ]
        print("|".join(f"{str(v)[:w-1]:<{w}}" for v, w in zip(vals, cw)))
    print(sep)


if __name__ == "__main__":
    main()
